"""The risk study on synthetic labels with a planted signal (no API calls)."""

import json
import math

import pytest

pytest.importorskip("sklearn")

from routewise import risk  # noqa: E402
from routewise.dataset import load_examples  # noqa: E402
from routewise.schema import CallRecord  # noqa: E402


def call(label, ex, answer, cost, latency):
    return CallRecord(
        call_id=label, example_id=ex.id, task="extraction", candidate=label, model_id=label, reasoning="off",
        prompt_version="v", prompt_sha256="x", response_format="json_schema", started_at="", latency_ms=latency,
        total_ms=latency, attempts=1, prompt_tokens=1, completion_tokens=1, reasoning_tokens=None, cached_tokens=0,
        cost_usd=cost, finish_reason="stop", content=json.dumps(answer), reasoning_chars=None, parsed=answer,
        parse_error=None, schema_error=None, error=None,
    )


def planted_rows():
    """Nano fails exactly when a conversation mentions two or more distinct amounts (a learnable signal)."""
    from routewise.features import extract

    examples = [e for e in load_examples() if e.task == "extraction"]
    rows = []
    for ex in examples:
        hard = extract(ex.input)["n_distinct_amounts"] >= 2
        wrong = dict(ex.expected, refund_requested=not ex.expected["refund_requested"])
        nano = call("nano", ex, wrong if hard else ex.expected, 1.0, 100.0)
        rows.append(risk.Row(example=ex, sample=0, nano=nano, super_=call("super", ex, ex.expected, 5.0, 150.0),
                             ultra=call("ultra", ex, ex.expected, 15.0, 120.0), nano_wrong=hard, validator_flags=False))
    return rows


def test_study_recovers_planted_signal_and_portable_model_matches():
    rows = planted_rows()
    families = sorted({r.example.tags[0] for r in rows})
    test_f, cal_f = set(families[:3]), set(families[3:5])
    split = {"test": [r for r in rows if r.example.tags[0] in test_f],
             "calibration": [r for r in rows if r.example.tags[0] in cal_f],
             "train": [r for r in rows if r.example.tags[0] not in test_f | cal_f]}
    model, summary = risk.run_study(split["train"], split["calibration"], split["test"])

    assert summary["test"]["auc"] is None or summary["test"]["auc"] > 0.9
    assert summary["coefficients"]["n_distinct_amounts"] > 0
    # oracle is an upper bound; every policy is billed
    pol = summary["test"]["policies"]
    assert pol["oracle"]["task_accuracy"] >= pol["validator"]["task_accuracy"]
    assert pol["always_strongest"]["cost_per_1k_tasks"] == pytest.approx(15000.0)

    # Pure-Python inference reproduces the study's probabilities and survives a JSON round trip.
    restored = risk.RiskModel.from_dict(json.loads(json.dumps(model.to_dict())))
    for r in split["test"][:10]:
        p = restored.probability(r.example.input)
        z = model.platt_a * model.raw_score(risk.vector(r.example.input)) + model.platt_b
        assert 0.0 < p < 1.0 and math.isclose(p, 1 / (1 + math.exp(-z)))


def test_replay_semantics():
    rows = planted_rows()
    hard = next(r for r in rows if r.nano_wrong)
    assert risk.replay("validator", hard)["missed_error"]  # validator_flags=False in this fixture
    out = risk.replay("risk+validator", hard, p=0.9, tau=0.5)
    assert out["correct"] and out["super_calls"] == 1 and out["cost"] == 5.0
    assert risk.replay("risk+validator", hard, p=0.1, tau=0.5)["missed_error"]
    assert risk.replay("oracle", hard)["correct"]
