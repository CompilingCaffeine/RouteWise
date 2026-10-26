import json
import math
import re
from datetime import UTC, datetime, timedelta
from statistics import NormalDist

import httpx
import pytest

from fakes import MODELS, completion_body, transport
from routewise import labels, validation_design as vd, workload_validation as wv
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config
from routewise.evaluation import profile_routers, run_evaluation
from routewise.prices import build_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.runlog import RunDir, read_jsonl

TEMPLATE = REPO_ROOT / "data/validation-template"
TEXT = ("[2026-05-01 10:00] Customer: Please refund my $20 for order ORD-1.\n"
        "[2026-05-01 12:00] Agent: Your refund will be issued in two days.")


def row(where="t.csv:row 2", **over):
    base = {"conversation_id": "c1", "split": "development", "text": TEXT, "refund_requested": "true",
            "refund_amount": "20", "currency": "USD", "order_id": "ORD-1", "contact_date": "2026-05-01",
            "refund_date": "2026-05-03", "review_required": "false", "review_reason": "",
            "evidence": "refund my $20 | refund will be issued in two days", "scenario_tags": "relative_issue_date",
            "labeler_id": "ann", "notes": ""}
    return where, {**base, **over}


def renamed(labeler):
    return [(w, {**r, "conversation_id": r["conversation_id"].replace("EXAMPLE-", "conv-"), "labeler_id": labeler})
            for w, r in labels.read_rows(TEMPLATE / "labeling-template.csv")]


def test_template_illustrations_are_refused_but_follow_the_contract():
    with pytest.raises(labels.LabelError, match="illustrative template rows"):
        labels.assemble(labels.read_rows(TEMPLATE / "labeling-template.csv"))
    built = labels.assemble(renamed("ann") + renamed("bob"))
    assert [r.id for r in built.records] == ["conv-001", "conv-002"] and not built.queue
    review = built.records[1]
    assert review.review_required and review.expected["refund_date"] is None and review.family == "business_days"
    adjudication = labels.read_rows(TEMPLATE / "adjudication-template.csv")
    assert set(adjudication[0][1]) == set(labels.ADJUDICATION_COLUMNS)
    schema = json.loads((TEMPLATE / "label-record.schema.json").read_text())
    assert set(schema["properties"]) == set(labels.LABEL_COLUMNS)


@pytest.mark.parametrize("over, message", [
    ({"text": "Customer: hi"}, r"is not '\[YYYY-MM-DD HH:MM\] Speaker"),
    ({"text": TEXT.replace("Agent", "Maria")}, "speaker 'Maria' must be one of"),
    ({"contact_date": "2026-05-02"}, r"contact_date must be the first Customer message date \(2026-05-01\)"),
    ({"evidence": "refund within a week"}, "evidence quote not found"),
    ({"evidence": ""}, "evidence: quote the message text"),
    ({"review_required": "true"}, "review_reason must start with one of"),
    ({"review_reason": "other"}, "review_reason is set but review_required is false"),
    ({"review_required": "yes", "review_reason": "other"}, "needs a description"),
    ({"refund_amount": "", "currency": "USD"}, "currency is set but refund_amount is empty"),
    ({"refund_amount": "12,50"}, "plain number with a decimal point"),
    ({"currency": "XYZ"}, "currency: Input should be"),
    ({"refund_date": "2026-02-30"}, "refund_date"),
    ({"refund_requested": ""}, "refund_requested: expected true or false"),
    ({"scenario_tags": "Relative Date"}, "must use lowercase letters"),
    ({"split": "test"}, "split must be development or heldout"),
    ({"labeler_id": ""}, "labeler_id is empty"),
    ({"extra": "x"}, "unknown column"),
])
def test_contract_violations_are_reported_with_their_location(over, message):
    with pytest.raises(labels.LabelError, match=message) as err:
        labels.assemble([row(**over), row(labeler_id="bob")])
    assert "t.csv:row 2 (c1)" in str(err.value) or "unknown column" in str(err.value)


def test_each_conversation_needs_two_independent_identical_copies():
    with pytest.raises(labels.LabelError, match="exactly two labels from two different labelers"):
        labels.assemble([row(), row(where="t.csv:row 3")])
    with pytest.raises(labels.LabelError, match="text or split differs"):
        labels.assemble([row(), row(labeler_id="bob", split="heldout")])
    with pytest.raises(labels.LabelError, match="same conversation text as c1"):
        labels.assemble([row(), row(labeler_id="bob"), row(conversation_id="c2"),
                         row(conversation_id="c2", labeler_id="bob")])


def test_metric_changing_disagreements_go_to_a_third_person():
    pair = [row(), row(labeler_id="bob", refund_date="2026-05-01")]
    queued = labels.assemble(pair)
    assert not queued.records and queued.queue[0]["ref_differs"] == "refund_date"
    assert queued.agreement["metric_disagreements"] == 1
    decision = {k: v for k, v in row()[1].items() if k in labels.ADJUDICATION_COLUMNS}
    decision.update(decision="label", adjudicator_id="ann", reason="the store issues in two days")
    with pytest.raises(labels.LabelError, match="must not be one of the conversation's labelers"):
        labels.assemble(pair, [("adj.csv:row 2", decision)])
    built = labels.assemble(pair, [("adj.csv:row 2", {**decision, "adjudicator_id": "cat"})])
    assert built.records[0].expected["refund_date"] == "2026-05-03" and built.records[0].adjudicator == "cat"
    excluded = labels.assemble(pair, [("adj.csv:row 2", {"conversation_id": "c1", "decision": "exclude",
                                                         "adjudicator_id": "cat", "reason": "not de-identifiable"})])
    assert not excluded.records and excluded.excluded[0]["reason"] == "not de-identifiable"
    untouched = {k: "" for k in labels.ADJUDICATION_COLUMNS} | {"conversation_id": "c1"}
    assert labels.assemble(pair, [("queue.csv:row 2", untouched)]).queue  # an empty queue row decides nothing


def test_field_values_are_not_adjudicated_when_both_require_review():
    review = {"review_required": "true", "review_reason": "issue_date_ambiguous: two days or three"}
    built = labels.assemble([row(**review), row(labeler_id="bob", refund_date="", **review)])
    assert not built.queue and built.records[0].review_required and built.records[0].expected["refund_date"] is None


def test_agreement_statistics():
    assert labels.cohen_kappa([(True, True), (False, False), (True, False), (False, False)]) == pytest.approx(0.5)
    assert labels.cohen_kappa([(True, True)]) is None
    stats = labels.agreement([(labels.parse_label(*row())[0], labels.parse_label(*row(labeler_id="bob"))[0])])
    assert stats["metric_agreement_rate"] == 1.0 and stats["fields_when_both_answerable"]["order_id"]["agreement"] == 1.0


def test_personal_data_warnings_ignore_timestamps():
    assert labels.pii_flags(TEXT) == []
    flagged = TEXT + "\n[2026-05-01 12:05] Customer: Mail me at jo@example.com or call +1 415 555 0100."
    assert labels.pii_flags(flagged) == ["email", "phone"]


# ---------------------------------------------------------------- the three arms


def nano_answer(text, refund_date=None):
    """A fake model that reads amount and order correctly but misses relative issue dates."""
    amount = re.search(r"\$(\d+(?:\.\d+)?)", text)
    order = re.search(r"ORD-\d+", text)
    first = re.search(r"\[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\] Customer", text)
    return {"refund_requested": True, "refund_amount": float(amount.group(1)) if amount else None,
            "currency": "USD" if amount else None, "order_id": order.group() if order else None,
            "contact_date": first.group(1) if first else None, "refund_date": refund_date}


def fake_client():
    def chat(body):
        return httpx.Response(200, json=completion_body(json.dumps(nano_answer(body["messages"][-1]["content"]))))
    return TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(chat)))


def arms(client):
    cfg = load_config(wv.CONFIG)
    profiles = {arm: json.loads(path.read_text()) for arm, path in wv.PROFILES.items()}
    prices = {c.label: price_for(build_snapshot(MODELS, "https://fake/v1/"), c.model_id) for c in cfg.candidates}
    prompts = {t: load_prompt(p, t) for t, p in cfg.prompts.items()}
    return profile_routers(client, cfg, profiles, prices, prompts), cfg, profiles, prices, prompts


def test_profiles_run_as_labelled_arms_on_identical_conversations(tmp_path):
    routers, cfg, profiles, prices, prompts = arms(fake_client())
    assert list(routers) == ["adopted", "previous", "grounded_super"]
    example = labels.assemble([row(), row(labeler_id="bob")]).records[0].example()
    run = RunDir.create("t", root=tmp_path)
    traces = {t.strategy: t for t in run_evaluation(routers, [example], run, order_seed=7, progress=lambda _: None)}
    assert traces["adopted"].final_valid and traces["adopted"].final_output["refund_date"] == "2026-05-03"
    assert traces["adopted"].recovery["action"] == "normalized"
    assert not traces["previous"].final_valid and traces["previous"].model_chain == ["nano", "super"]
    assert traces["grounded_super"].model_chain == ["super"] and traces["previous"].policy_id == profiles["previous"]["policy_id"]
    done = {(example.id, name) for name in routers}
    assert run_evaluation(routers, [example], run, order_seed=7, skip=done, progress=lambda _: None) == []
    with pytest.raises(ValueError, match="profile expects extraction_v4"):
        profile_routers(fake_client(), cfg, {"x": profiles["previous"]}, prices,
                        {"extraction": load_prompt(REPO_ROOT / "prompts/extraction_v2.txt", "extraction")})


# ---------------------------------------------------------------- end to end, without network


def conversations(n, start, review_every=6):
    rows = []
    for i in range(start, start + n):
        day = 1 + i % 27
        review = i % review_every == 0
        promise = "within 3-5 business days" if review else "in two days"
        text = (f"[2026-06-{day:02d} 09:00] Customer: Please refund my ${10 + i} for order ORD-{1000 + i}.\n"
                f"[2026-06-{day:02d} 11:00] Agent: Your refund will be issued {promise}.")
        label = {"conversation_id": f"conv-{i:03d}", "text": text, "refund_amount": str(10 + i),
                 "order_id": f"ORD-{1000 + i}", "contact_date": f"2026-06-{day:02d}",
                 "refund_date": "" if review else f"2026-06-{day + 2:02d}",
                 "review_required": str(review).lower(),
                 "review_reason": "issue_date_ambiguous: business-day range" if review else "",
                 "evidence": f"refund my ${10 + i} | issued {promise}",
                 "scenario_tags": "business_days" if review else "relative_issue_date"}
        rows += [row(f"{who}.csv:row {i}", labeler_id=who, **label) for who in ("ann", "bob")]
    return rows


@pytest.fixture
def offline(tmp_path, monkeypatch):
    def snapshot(config, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(build_snapshot(MODELS, config.base_url)))

    def study_client(config, snap, budget):
        client = fake_client()
        client.pacers, client.spent = {c.model_id: None for c in config.candidates}, 0.0
        return client

    monkeypatch.setattr(wv, "fetch_prices", snapshot)
    monkeypatch.setattr(wv, "StudyClient", study_client)
    monkeypatch.setattr(wv, "LOAD", {"concurrency": 4, "rpm": 0})
    for name in ("PROTOCOL", "DESIGN", "PUBLIC_SEAL"):
        monkeypatch.setattr(wv, name, tmp_path / f"{name.lower()}.md")
    wv.PROTOCOL.write_text("# M8\n\n## Part A\n")
    monkeypatch.setattr(wv, "_require_committed", lambda paths: None)
    return tmp_path / "ws"


def write_rows(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for _, r in rows))
    return path


def test_pilot_report_proposal_seal_and_confirmation(offline, tmp_path, monkeypatch):
    ws = offline
    with pytest.raises(labels.LabelError):
        wv.import_labels(ws, "pilot", [write_rows(tmp_path / "bad.jsonl", [row()])], None)
    wv.import_labels(ws, "pilot", [write_rows(tmp_path / "pilot.jsonl", conversations(60, 0))], None)
    with pytest.raises(ValueError, match="no built dataset"):
        wv.run(ws, "missing", "development")
    run_path = wv.run(ws, "pilot", "development")
    summary = json.loads((run_path / "summary.json").read_text())
    adopted, previous = summary["metrics"]["adopted"], summary["metrics"]["previous"]
    assert summary["complete"] and summary["n"] == 60 and adopted["n_review_required"] == 10
    assert adopted["correct_autonomous"] == 50 and adopted["undetected_errors"] == 0
    assert adopted["necessary_review_rate"] == pytest.approx(10 / 60)
    # the previous route misses every relative date (avoidable review) and releases null for business days
    assert previous["correct_autonomous"] == 0 and previous["undetected_errors"] == 10
    assert summary["review_causes"]["adopted"] == {"issue date: ambiguous issue date": 10}
    assert summary["paired"]["adopted - previous"]["cac"]["a_only"] == 50
    assert summary["issue_date_coverage"]["agrees_with_label"] == 60
    diagnostics = json.loads((run_path / "diagnostics.json").read_text())
    assert len(diagnostics["wrong_releases"]["previous"]) == 10 and len(read_jsonl(run_path / "traces.jsonl")) == 180
    assert "conv-" not in json.dumps(summary) and "Please refund" not in json.dumps(summary)  # aggregates only

    proposal = wv.propose(run_path, {})
    design = json.loads((proposal / "design.json").read_text())
    assert design["feasible"] and design["n_required"] == max(c["n"] for c in design["criteria"].values())
    assert design["n_collect"] >= design["n_required"] and design["labels_needed"] == 2 * design["n_collect"]
    assert all(p >= 0.9 for p in design["power_at_n_required"].values() if p is not None)
    with pytest.raises(ValueError, match="Replace every"):
        wv.preregister(proposal)
    part = (proposal / "part-b.md").read_text()
    (proposal / "part-b.md").write_text(re.sub(r"\[TO COMPLETE: [^\]]*\]", "Support tickets, June 2026.", part))
    wv.preregister(proposal)
    assert "## Part B" in wv.PROTOCOL.read_text() and json.loads(wv.DESIGN.read_text())["n_required"]
    with pytest.raises(ValueError, match="already registered"):
        wv.preregister(proposal)

    # Held-out data: a small design keeps the test fast; the guards are what is tested.
    wv.DESIGN.write_text(json.dumps({**design, "n_required": 30}))
    monkeypatch.setattr(wv, "_committed_at", lambda path: datetime.now(UTC) - timedelta(seconds=1))
    heldout = [(w, {**r, "split": "heldout"}) for w, r in conversations(36, 100)]
    wv.import_labels(ws, "late", [write_rows(tmp_path / "late.jsonl", heldout[:20])], None)
    with pytest.raises(ValueError, match="the design requires 30"):
        wv.seal(ws, "late")
    overlap = [(w, {**r, "split": "heldout"}) for w, r in conversations(36, 30)]
    wv.import_labels(ws, "overlap", [write_rows(tmp_path / "overlap.jsonl", overlap)], None)
    with pytest.raises(ValueError, match="overlaps the held-out set"):
        wv.seal(ws, "overlap")
    with pytest.raises(ValueError, match="only after `routewise validation seal`"):
        wv.run(ws, "overlap", "heldout")
    for name in ("late", "overlap"):
        for f in (ws / "data" / name).iterdir():
            f.unlink()
        (ws / "data" / name).rmdir()
    wv.import_labels(ws, "heldout", [write_rows(tmp_path / "heldout.jsonl", heldout)], None)
    wv.seal(ws, "heldout")
    with pytest.raises(ValueError, match="never used for smoke tests"):
        wv.run(ws, "heldout", "heldout", smoke=3)
    confirm = wv.run(ws, "heldout", "heldout")
    result = json.loads((confirm / "summary.json").read_text())
    checks = result["decision"]["checks"]
    assert result["kind"] == "confirmation" and checks["vs_previous"] and checks["cost_vs_grounded_super"]
    assert not checks["primary_wrong_release"] and not result["decision"]["validated"]  # 0/36 cannot bound 2%
    with pytest.raises(ValueError, match="A confirmation run for this seal exists"):
        wv.run(ws, "heldout", "heldout")
    (ws / "data" / "heldout" / "examples.jsonl").write_text("")
    with pytest.raises(ValueError, match="changed after sealing"):
        wv.check_seal(ws / "data" / "heldout")


def test_smoke_runs_cannot_inform_the_design(offline, tmp_path):
    wv.import_labels(offline, "few", [write_rows(tmp_path / "few.jsonl", conversations(4, 0))], None)
    with pytest.raises(ValueError, match="uses 50-100 development conversations"):
        wv.run(offline, "few", "development")
    smoke = wv.run(offline, "few", "development", smoke=2)
    assert json.loads((smoke / "summary.json").read_text())["n"] == 2
    with pytest.raises(ValueError, match="Only a complete diagnostic pilot"):
        wv.propose(smoke, {})


# ---------------------------------------------------------------- planning math


def pilot(**over):
    base = {"n": 80, "n_collected": 82, "n_excluded": 2,
            "adopted": {"wrong_releases": 0, "answerable": 72, "correct": 72, "field_accuracy": 1.0,
                        "deadline_misses": 1, "incomplete_cost": 0},
            "vs_previous": {"cac": {"diff": 0.05, "a_only": 4, "b_only": 0, "n_pairs": 80},
                            "undetected": {"diff": -0.0125, "a_only": 0, "b_only": 1, "n_pairs": 80}},
            "cost_diffs_vs_grounded_super": [-0.0002, -0.00025, -0.0003] * 27,
            "label_disagreement_rate": 0.1, "cost_per_conversation_usd": 0.0006, "labels_revised_after_run": False}
    return base | over


def test_exact_binomial_sample_size_matches_the_closed_form_for_zero_events():
    z2 = NormalDist().inv_cdf(0.95) ** 2  # one-sided 95% Wilson upper bound at x = 0 is z²/(n + z²)
    assert vd.wrong_release(pilot(), 0.02, conservative=False)["n"] == math.ceil(z2 * (1 - 0.02) / 0.02)
    assert 0 < vd.power_rate_below(200, 0.01, 0.02) < vd.power_rate_below(2000, 0.01, 0.02) <= 1
    assert vd.power_rate_above(400, 0.99, 0.95) > 0.9 > vd.power_rate_above(400, 0.955, 0.95)


def test_paired_sample_size_and_planning_direction():
    z = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.90)
    assert vd.paired_n(0.0, 0.03, 0.02) == math.ceil(z * z * 0.03 / 0.02 ** 2)
    assert vd.paired_n(-0.03, 0.05, 0.02) is None  # worse than the margin: no n suffices
    assert vd.planning_difference(0.04) == 0.02 and vd.planning_difference(-0.01) == -0.01
    assert vd.paired_power(vd.paired_n(0.01, 0.04, 0.02), 0.01, 0.04, 0.02) >= 0.9


def test_proposal_reports_feasibility_alternatives_and_burden():
    design = vd.propose(pilot())
    assert design["feasible"] and design["binding"] in design["criteria"]
    by_limit = design["alternatives"]["max_wrong_release_rate"]
    assert by_limit["0.01"] is None and by_limit["0.02"] > by_limit["0.03"] > by_limit["0.05"]  # 0.88% planning rate
    assert design["expected_adjudications"] == math.ceil(0.1 * design["n_collect"])
    weak = vd.propose(pilot(adopted=pilot()["adopted"] | {"correct": 66}))
    assert not weak["feasible"] and "task_accuracy" in weak["recommendation"] and weak["n_required"] is None


def test_decision_requires_every_check():
    adopted = {"undetected_upper95": 0.015, "accuracy_lower95": 0.97, "field_accuracy": 0.99,
               "deadline_miss_upper95": 0.03, "incomplete_cost_count": 0}
    vs_prev = {"cac": {"ci95": (-0.01, 0.02)}, "undetected": {"ci95": (-0.01, 0.005)}}
    vs_super = {"cost_per_1k": {"ci95": (-0.3, -0.2)}}
    assert vd.decide(vd.DEFAULTS, adopted, vs_prev, vs_super)["validated"]
    failed = vd.decide(vd.DEFAULTS, adopted | {"undetected_upper95": 0.03}, vs_prev, vs_super)
    assert not failed["validated"] and not failed["checks"]["primary_wrong_release"]
