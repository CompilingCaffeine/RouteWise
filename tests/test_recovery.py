"""M7 relative-date recovery: analyzer, challenge fact sheets, candidate decisions and the declared analysis."""

import json

import httpx
import pytest

from fakes import MODELS, completion_body, transport
from routewise import issue_workload
from routewise.config import REPO_ROOT, load_config
from routewise.date_challenge import (CHALLENGE_FAMILIES, REVIEW_FAMILIES, challenge_sheets, challenge_verify,
                                      representative_sheets)
from routewise.issue_dates import analyze_issue_date
from routewise.client import TokenFactoryClient
from routewise.prices import build_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.recovery import RecoveryRouter, decide
from routewise.recovery_metrics import RequestOutcome, paired, sample_size, summarize
from routewise.router import Router
from routewise.policy import RouteOption
from routewise.schema import CallRecord
from routewise.synth import render

M5_MISS = ("[2026-08-11 17:57] Customer: I returned the Atlas hiking backpack using my order reference ORD-1140324 "
           "and would like a refund for the $246.99 I paid.\n"
           "[2026-08-12 10:07] Agent: Your return has been received and The refund will be issued in four days.")
LABEL = {"refund_requested": True, "refund_amount": 246.99, "currency": "USD", "order_id": "ORD-1140324",
         "contact_date": "2026-08-11", "refund_date": "2026-08-16"}


def convo(*store: str, customer: str = "I want a refund for order ORD-1.", day: str = "2026-06-09") -> str:
    lines = [f"[{day} 09:18] Customer: {customer}"] + [f"[{day} 1{i}:00] Agent: {s}" for i, s in enumerate(store)]
    return "\n".join(lines)


@pytest.mark.parametrize("text,status,when", [
    (M5_MISS, "resolved", "2026-08-16"),
    (convo("The refund will be issued the day after tomorrow."), "resolved", "2026-06-11"),
    (convo("The refund will be issued tomorrow.", day="2028-02-28"), "resolved", "2028-02-29"),
    (convo("The refund will be issued in 3 days.", day="2026-12-30"), "resolved", "2027-01-02"),
    (convo("The refund will be issued a week from today."), "resolved", "2026-06-16"),
    (convo("The refund will be issued this Friday."), "resolved", "2026-06-12"),
    (convo("The refund was issued two days ago."), "resolved", "2026-06-07"),
    (convo("The refund will be issued on Friday and should reach your account in 5 days."), "resolved", "2026-06-12"),
    (convo("The refund will be issued tomorrow.", "Correction: the refund will now be issued in 4 days."), "resolved", "2026-06-13"),
    (convo("The refund should arrive in your account in 3 days. The refund issue date is not confirmed yet."), "none", None),
    (convo("The refund will be issued in 3 business days."), "review", None),
    (convo("The refund will be issued in 3-5 days."), "review", None),
    (convo("The refund will be issued within 3 days."), "review", None),
    (convo("The refund will be issued next Friday."), "review", None),
    (convo("The refund will be issued by the end of the week."), "review", None),
    (convo("The refund will be issued tomorrow.", "The refund will be issued on June 15."), "review", None),
    (convo("A prepaid label will be emailed today and the repair done within two weeks."), "none", None),
    (convo("The refund of $20.00 was issued on June 5 and can take 3-5 business days to appear."), "resolved", "2026-06-05"),
])
def test_issue_date_analysis(text, status, when):
    result = analyze_issue_date(text)
    assert (result.status, result.date) == (status, when), result.reason


def test_customer_restatement_never_moves_the_anchor():
    text = ("[2026-06-09 09:18] Customer: refund please.\n[2026-06-09 12:44] Agent: The refund will be issued in 3 days.\n"
            "[2026-06-11 10:00] Customer: You said the refund would be issued in 3 days, is that still right?")
    assert analyze_issue_date(text).date == "2026-06-12"


def test_unrecognized_language_is_review_but_not_known_ambiguity():
    result = analyze_issue_date(convo("The refund will be issued three days hence."))
    assert result.status == "review" and not result.known_ambiguity


def test_analyzer_matches_every_exposed_v4_label():
    rows = [json.loads(line) for line in (REPO_ROOT / "data/v4/examples.jsonl").read_text().splitlines()]
    for row in rows:
        result = analyze_issue_date(row["input"])
        assert result.status != "review", row["id"]
        assert result.date == row["expected"]["refund_date"] or result.status == "none", row["id"]


# ---------------------------------------------------------------- workloads

def _minimal_texts(sheet):
    return [". ".join(m.must) + "." if m.must else "Thanks." for m in sheet.messages]


@pytest.mark.parametrize("family", [f for f in CHALLENGE_FAMILIES if f != "paraphrased_offset"])
def test_fact_sheet_labels_agree_with_the_analyzer(family):
    for sheet in [s for s in challenge_sheets(7, 12, "t") if s.family == family]:
        texts = _minimal_texts(sheet)
        assert challenge_verify(sheet, texts) == [], (sheet.id, challenge_verify(sheet, texts))
        result = analyze_issue_date(render(sheet, texts))
        if family in REVIEW_FAMILIES:
            assert result.status == "review" and result.known_ambiguity, (sheet.id, result.reason)
        else:
            assert result.status in ("resolved", "none") and result.date == sheet.expected["refund_date"], (sheet.id, result)


def test_paraphrase_acceptance():
    sheet = next(s for s in challenge_sheets(7, 12, "t") if s.family == "paraphrased_offset")
    texts = _minimal_texts(sheet)
    index = next(i for i, m in enumerate(sheet.messages) if "will be issued" in m.must)
    n = (__import__("datetime").date.fromisoformat(sheet.expected["refund_date"])
         - __import__("datetime").date.fromisoformat(sheet.contact)).days - sheet.messages[index].day
    assert any("no timing" in p for p in challenge_verify(sheet, texts))
    texts[index] = texts[index] + " It will go out within a few days."
    assert any("forbidden" in p for p in challenge_verify(sheet, texts))
    texts[index] = ". ".join(sheet.messages[index].must) + {2: " two days from now.", 3: " three days from now.",
                                                           7: " a week from now.", 14: " a fortnight from now."}[n]
    assert challenge_verify(sheet, texts) == []


def test_workloads_are_deterministic_and_disjoint_from_v4():
    assert challenge_sheets(7, 3, "t")[5].to_dict() == challenge_sheets(7, 3, "t")[5].to_dict()
    sheets = representative_sheets(12345, 15, "v5r")
    assert len(sheets) == 330 and len({s.id for s in sheets}) == 330
    v4_ids = {oid for s in issue_workload.fresh_sheets() for oid in s.order_ids}
    assert not v4_ids & {oid for s in sheets for oid in s.order_ids}
    assert {s.family for s in sheets} == {s.family for s in issue_workload.fresh_sheets()}


def test_v4_fact_sheets_unchanged_by_refactor():
    regenerated = "".join(json.dumps(s.to_dict()) + "\n" for s in issue_workload.fresh_sheets())
    assert regenerated == (REPO_ROOT / "data/v4/factsheets.jsonl").read_text()


# ---------------------------------------------------------------- decisions

def record(label, parsed, *, reasoning="off", cost=0.001, ms=900.0, schema_error=None):
    return CallRecord(call_id="c", example_id="x", task="extraction", candidate=label, model_id="m",
                      reasoning=reasoning, prompt_version="v", prompt_sha256="s", response_format="json_schema",
                      started_at="t", latency_ms=ms, total_ms=ms, attempts=1, prompt_tokens=10, completion_tokens=5,
                      reasoning_tokens=0, cached_tokens=0, cost_usd=cost, finish_reason="stop",
                      content=json.dumps(parsed), reasoning_chars=None, parsed=parsed, parse_error=None,
                      schema_error=schema_error, error=None)


def caller(nano, super_=None, fallback=None):
    calls = []

    def call(label):
        calls.append(label)
        return {"nano": lambda: record("nano", nano), "super": lambda: record("super", super_),
                "fallback": lambda: record("super+think", fallback, reasoning="on")}[label]()
    return call, calls


def test_control_sends_the_m5_miss_to_review():
    call, calls = caller(dict(LABEL, refund_date=None), dict(LABEL, refund_date=None))
    d = decide("control", M5_MISS, call)
    assert calls == ["nano", "super"] and not d.released and d.escalated


def test_deterministic_recovers_the_m5_miss_without_another_call():
    call, calls = caller(dict(LABEL, refund_date=None))
    d = decide("deterministic", M5_MISS, call)
    assert calls == ["nano"] and d.released and d.final_output["refund_date"] == "2026-08-16"
    assert d.recovery["action"] == "normalized"


def test_reasoning_releases_only_on_agreement():
    good = {"status": "issue_date", "refund_date": "2026-08-16", "evidence": "The refund will be issued in four days"}
    call, calls = caller(dict(LABEL, refund_date=None), fallback=good)
    d = decide("reasoning", M5_MISS, call)
    assert calls == ["nano", "fallback"] and d.released and d.final_output["refund_date"] == "2026-08-16"
    call, _ = caller(dict(LABEL, refund_date=None), fallback=dict(good, refund_date="2026-08-15"))
    assert not decide("reasoning", M5_MISS, call).released


def test_known_ambiguity_goes_to_review_without_a_fallback():
    text = convo("The refund will be issued in 3 business days.")
    answer = {"refund_requested": True, "refund_amount": None, "currency": None, "order_id": "ORD-1",
              "contact_date": "2026-06-09", "refund_date": None}
    for kind in ("deterministic", "reasoning"):
        call, calls = caller(answer)
        d = decide(kind, text, call)
        assert calls == ["nano"] and not d.released
    call, calls = caller(answer)
    assert decide("control", text, call).released  # the deployed validator lets null through


def test_reasoning_may_release_unparsed_language_only_with_a_store_quote():
    text = convo("The refund will be issued three days hence.")
    answer = {"refund_requested": True, "refund_amount": None, "currency": None, "order_id": "ORD-1",
              "contact_date": "2026-06-09", "refund_date": None}
    quoted = {"status": "issue_date", "refund_date": "2026-06-12", "evidence": "issued three days hence"}
    call, _ = caller(answer, fallback=quoted)
    assert decide("reasoning", text, call).released
    call, _ = caller(answer, fallback=dict(quoted, evidence="three days later"))
    assert not decide("reasoning", text, call).released
    call, calls = caller(answer)
    assert not decide("deterministic", text, call).released and calls == ["nano"]


def test_arrival_date_is_removed_by_normalization():
    text = convo("The money should arrive by 2026-06-14. No refund issue date has been provided.")
    answer = {"refund_requested": True, "refund_amount": None, "currency": None, "order_id": "ORD-1",
              "contact_date": "2026-06-09", "refund_date": "2026-06-14"}
    call, _ = caller(answer, dict(answer))
    d = decide("deterministic", text, call)
    assert d.released and d.final_output["refund_date"] is None


def _fake_client(answers):
    def chat(body):
        content = answers[body["model"]]
        return httpx.Response(200, json=completion_body(json.dumps(content), model=body["model"]))
    return TokenFactoryClient(api_key="t", http_client=httpx.Client(transport=transport(chat)))


def test_live_routes_match_replayed_decisions():
    config = load_config()
    snapshot = build_snapshot(MODELS, "https://fake/v1/")
    prices = {c.label: price_for(snapshot, c.model_id) for c in config.candidates}
    v4 = load_prompt(REPO_ROOT / "prompts/extraction_v4.txt", "extraction")
    fb = load_prompt(REPO_ROOT / "prompts/date_fallback_v1.txt", "extraction")
    wrong = dict(LABEL, refund_date=None)
    client = _fake_client({config.candidate("nano").model_id: wrong, config.candidate("super").model_id: wrong})
    control = Router(client, config, prices, {"extraction": v4}, {"extraction": RouteOption("nano", "super")}, strategy="control")
    trace = control.route("extraction", M5_MISS, example_id="x", expected=LABEL)
    call, _ = caller(wrong, wrong)
    assert trace.final_valid == decide("control", M5_MISS, call).released is False
    live = RecoveryRouter(client, config, prices, v4, fb, strategy="deterministic")
    trace = live.route("extraction", M5_MISS, example_id="x", expected=LABEL)
    assert trace.final_valid and trace.score["task_correct"] and trace.model_chain == ["nano"]
    assert trace.recovery["action"] == "normalized" and trace.cost_complete


# ---------------------------------------------------------------- analysis

def outcome(i, *, review_required=False, released=True, correct=True, cost=0.0001, family="f"):
    return RequestOutcome(example_id=str(i), family=family, review_required=review_required, released=released,
                          correct=correct, field_accuracy=1.0 if correct else 0.5, cost_usd=cost, latency_ms=1000,
                          cost_complete=True)


def test_review_never_counts_as_a_correct_autonomous_answer():
    outs = [outcome(0), outcome(1, released=False), outcome(2, review_required=True, released=False),
            outcome(3, review_required=True), outcome(4, correct=False)]
    m = summarize(outs)
    assert m["correct_autonomous"] == 1 and m["undetected_errors"] == 2
    assert m["necessary_review_rate"] == m["avoidable_review_rate"] == 0.2
    assert m["released_accuracy"] == 1 / 3 and m["task_accuracy"] == 2 / 3


def test_paired_difference_and_sample_size():
    a = [outcome(i) for i in range(10)]
    b = [outcome(i, released=i % 2 == 0) for i in range(10)]
    p = paired(a, b, "cac")
    assert p["diff"] == pytest.approx(0.5) and p["a_only"] == 5 and p["b_only"] == 0
    plan = sample_size(0.6, 0.6)
    assert 15 <= plan["accepted_per_family"] <= 30 and plan["planned_per_family"] >= plan["accepted_per_family"]
    assert sample_size(0.1, 0.1)["cap_binds"]
