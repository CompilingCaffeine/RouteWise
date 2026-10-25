"""Router control flow: sequential escalation and hedging, against a fake endpoint with per-model delays."""

import json
import time

import httpx
import pytest

from fakes import MODELS, completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config
from routewise.policy import RouteOption, simulate
from routewise.prices import build_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.router import Router
from routewise.schema import Example

PROBE = Example.model_validate_json((REPO_ROOT / "data/probe/extraction_probe.json").read_text())
NANO, SUPER = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/nemotron-3-super-120b-a12b"
WRONG = dict(PROBE.expected, refund_date=None)  # fails refund_date_matches_statement


def make_router(behaviour: dict[str, tuple[float, dict]], option: RouteOption) -> Router:
    def chat(body):
        delay, answer = behaviour[body["model"]]
        time.sleep(delay)
        return httpx.Response(200, json=completion_body(json.dumps(answer), model=body["model"]))

    config = load_config()
    client = TokenFactoryClient(api_key="t", http_client=httpx.Client(transport=transport(chat)))
    snapshot = build_snapshot(MODELS, "https://fake/v1/")
    prices = {c.label: price_for(snapshot, c.model_id) for c in config.candidates}
    prompts = {t: load_prompt(p, t) for t, p in config.prompts.items()}
    return Router(client, config, prices, prompts, {"extraction": option}, strategy="routewise_hedged")


def route(router):
    return router.route("extraction", PROBE.input, example_id=PROBE.id, expected=PROBE.expected)


def test_fast_valid_primary_uses_one_call():
    trace = route(make_router({NANO: (0.0, PROBE.expected), SUPER: (0.0, PROBE.expected)},
                              RouteOption("nano", "super", hedge_after_ms=500)))
    assert [c.candidate for c in trace.calls] == ["nano"]
    assert not trace.hedged and not trace.escalated and trace.answered_by == "nano"


def test_fast_invalid_primary_escalates_sequentially():
    trace = route(make_router({NANO: (0.0, WRONG), SUPER: (0.0, PROBE.expected)},
                              RouteOption("nano", "super", hedge_after_ms=500)))
    assert trace.escalated and not trace.hedged and trace.answered_by == "super"
    assert trace.escalation_reason == "validator failed: refund_date_matches_statement"
    assert trace.score["task_correct"]


def test_slow_primary_is_hedged_and_faster_answer_wins():
    trace = route(make_router({NANO: (0.6, PROBE.expected), SUPER: (0.05, PROBE.expected)},
                              RouteOption("nano", "super", hedge_after_ms=100)))
    assert trace.hedged and not trace.escalated and trace.answered_by == "super"
    assert trace.latency_ms < 450, "answer must not wait for the slow primary"
    # The slow primary still completed and is billed.
    assert [c.candidate for c in trace.calls] == ["nano", "super"]
    assert all(c.cost_usd for c in trace.calls)
    assert trace.cost_usd == pytest.approx(sum(c.cost_usd for c in trace.calls))
    assert trace.timeline_ms["hedge_dispatched"] >= 100


def test_hedge_with_invalid_backup_falls_back_to_valid_primary():
    trace = route(make_router({NANO: (0.3, PROBE.expected), SUPER: (0.0, WRONG)},
                              RouteOption("nano", "super", hedge_after_ms=100)))
    assert trace.hedged and trace.answered_by == "nano" and not trace.escalated
    assert trace.final_valid


def test_hedge_with_invalid_primary_counts_as_escalation():
    trace = route(make_router({NANO: (0.15, WRONG), SUPER: (0.4, PROBE.expected)},
                              RouteOption("nano", "super", hedge_after_ms=100)))
    assert trace.hedged and trace.escalated and trace.answered_by == "super"


def test_simulation_matches_router_semantics():
    """The policy replay must bill and time a hedged option the way the router executes it."""
    from routewise.schema import CallRecord

    def record(label, latency, answer, cost):
        return CallRecord(
            call_id=label, example_id=PROBE.id, task="extraction", candidate=label, model_id=label, reasoning="off",
            prompt_version="v", prompt_sha256="x", response_format="json_schema", started_at="", latency_ms=latency,
            total_ms=latency, attempts=1, prompt_tokens=1, completion_tokens=1, reasoning_tokens=None,
            cached_tokens=0, cost_usd=cost, finish_reason="stop", content=json.dumps(answer), reasoning_chars=None,
            parsed=answer, parse_error=None, schema_error=None, error=None,
            score={"task_correct": answer == PROBE.expected, "field_accuracy": 1.0, "fields": {}},
        )

    index = {("nano", PROBE.id): record("nano", 600, PROBE.expected, 1.0),
             ("super", PROBE.id): record("super", 50, PROBE.expected, 5.0)}
    (hedged,) = simulate(RouteOption("nano", "super", 100), [PROBE], index)
    assert hedged.hedged and hedged.latency_ms == 150 and hedged.cost_usd == 6.0
    (plain,) = simulate(RouteOption("nano", "super"), [PROBE], index)
    assert not plain.hedged and plain.latency_ms == 600 and plain.cost_usd == 1.0
