import json

import pytest

from routewise.config import REPO_ROOT, load_config
from routewise.judge import JudgeRecord
from routewise.schema import CallRecord, Example
from routewise.validator_study import Sample, decide, evaluate_validators

PROBE = Example.model_validate_json((REPO_ROOT / "data/probe/extraction_probe.json").read_text())
CONFIG = load_config()


def call(label, answer, cost, latency=100.0):
    return CallRecord(
        call_id=label, example_id=PROBE.id, task="extraction", candidate=label, model_id=label, reasoning="off",
        prompt_version="v", prompt_sha256="x", response_format="json_schema", started_at="", latency_ms=latency,
        total_ms=latency, attempts=1, prompt_tokens=1, completion_tokens=1, reasoning_tokens=None, cached_tokens=0,
        cost_usd=cost, finish_reason="stop", content=json.dumps(answer), reasoning_chars=None, parsed=answer,
        parse_error=None, schema_error=None, error=None,
    )


def verdict(sample_id, judge, flagged, cost):
    return JudgeRecord(judge=judge, model_id=judge, sample_id=sample_id, example_id=PROBE.id, task="extraction",
                       prompt_version="v", prompt_sha256="x", latency_ms=50.0, total_ms=50.0, attempts=1,
                       prompt_tokens=1, completion_tokens=1, cost_usd=cost, content="{}",
                       verdict="incorrect" if flagged else "correct", wrong_fields=[], flagged=flagged, error=None)


RIGHT = PROBE.expected
SEMANTIC_MISS = dict(PROBE.expected, refund_requested=False)  # passes every deterministic rule
RULE_MISS = dict(PROBE.expected, refund_date=None)  # fails refund_date_matches_statement


def test_cascade_skips_the_judge_when_rules_already_reject():
    s = Sample("r:a", "r", PROBE, call("nano", RULE_MISS, 1.0))
    judged = {("super", "r:a"): verdict("r:a", "super", False, 9.0)}
    d = decide("deterministic+judge_super", s, judged)
    assert d.flagged and not d.judge_ran and d.cost_usd == 0
    # judge alone sees the schema-valid answer, misses the error, and is billed
    d = decide("judge_super", s, judged)
    assert not d.flagged and d.judge_ran and d.cost_usd == 9.0


def test_metrics_and_billing():
    samples = [
        Sample("r:1", "r", PROBE, call("nano", RIGHT, 1.0)),
        Sample("r:2", "r", PROBE, call("nano", SEMANTIC_MISS, 1.0)),
        Sample("r:3", "r", PROBE, call("nano", RULE_MISS, 1.0)),
    ]
    judged = {
        ("super", "r:1"): verdict("r:1", "super", False, 3.0),
        ("super", "r:2"): verdict("r:2", "super", True, 3.0),
        ("super", "r:3"): verdict("r:3", "super", True, 3.0),
    }
    escalation = {PROBE.id: call("super", RIGHT, 10.0)}
    out = evaluate_validators(CONFIG, samples, ["super"], judged, escalation)["validators"]

    det = out["deterministic"]["overall"]
    assert (det["true_positive"], det["false_negative"], det["false_positive"]) == (1, 1, 0)
    assert det["cost_usd_total"] == pytest.approx(3 * 1.0 + 10.0)

    judge = out["judge_super"]["overall"]
    assert (judge["true_positive"], judge["false_negative"]) == (2, 0)
    assert judge["task_accuracy"] == 1.0 and judge["correction_rate"] == 1.0
    assert judge["cost_usd_total"] == pytest.approx(3 * 1.0 + 3 * 3.0 + 2 * 10.0)

    combo = out["deterministic+judge_super"]["overall"]
    assert combo["judge_calls"] == 2  # the rule-rejected answer never reached the judge
    assert combo["cost_usd_total"] == pytest.approx(3 * 1.0 + 2 * 3.0 + 2 * 10.0)

    assert out["none"]["overall"]["task_accuracy"] == pytest.approx(1 / 3)
