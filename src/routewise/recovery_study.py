"""M7 relative-date recovery (displayed as M6). Run with `routewise recovery <stage>`.

prepare --set dev|confirm   writers, deterministic acceptance and contract-verifier label checks
build --set dev|confirm     apply the blinded keep/drop review; `confirm` also seals the study
dev-run                     grounded Nano/Super and every reasoning fallback on the development set
dev-retry                   one sequential retry of development calls that failed in transport
select                      replay every strategy on development data and apply the selection rule
plan                        the preregistered sample-size formula for the selected candidate
evaluate [--run]            live three-arm confirmation (resumes missing outcomes only)
report --run                the preregistered analysis
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from random import Random
from typing import Any

from routewise.benchmark import Pacer, run_benchmark, run_parallel
from routewise.client import TokenFactoryClient
from routewise.confirmation import StudyClient
from routewise.config import REPO_ROOT, Config, load_config, load_dotenv
from routewise.cost import call_cost_usd
from routewise.date_challenge import (CHALLENGE_FAMILIES, REVIEW_TAG, challenge_sheets, challenge_verify, representative_sheets,
                                      requires_review, to_examples)
from routewise.evaluate import score
from routewise.policy import RouteOption
from routewise.prices import fetch_snapshot, load_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.recovery import STATUSES, STRATEGIES, Decision, RecoveryRouter, call_fallback, replay
from routewise.recovery_metrics import RequestOutcome, gates, paired, sample_size, summarize
from routewise.router import Router, Trace
from routewise.runlog import RunDir, read_jsonl, sha256_file
from routewise.schema import CallRecord, Example, output_json_schema
from routewise.synth import write_all

STUDY = "m7"
ROOT = REPO_ROOT / "data/v5"
DIRS = {"dev": ROOT / "dev", "confirm": ROOT / "confirm"}
PROTOCOL = REPO_ROOT / "experiments/M7-relative-date-recovery.md"
DEV_SEED = 2026102601
REPRESENTATIVE_PER_FAMILY = 15
CANDIDATES = ("deterministic", "reasoning_nano", "reasoning_super", "reasoning_ultra")
ARMS = ("control", "fixed_grounded_super")
PROMPTS = {"v4": REPO_ROOT / "prompts/extraction_v4.txt", "fallback": REPO_ROOT / "prompts/date_fallback_v1.txt",
           "verifier": REPO_ROOT / "prompts/date_contract_verifier_v1.txt"}
V4_HELDOUT_RUN = REPO_ROOT / "runs/confirmation-20261025T212745Z-ac61b1"
V4_TUNE_CALLS = REPO_ROOT / "runs/confirmtune-20261025T212438Z-7e696a/v4-calls.jsonl"


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def frozen_sources() -> dict[str, str]:
    """Everything that decides an outcome or its analysis; this orchestration module is tracked by git instead."""
    files = [p for p in sorted((REPO_ROOT / "src/routewise").glob("*.py")) if p.name != "recovery_study.py"]
    files += [*PROMPTS.values(), REPO_ROOT / "config/routewise.yaml"]
    return {str(p.relative_to(REPO_ROOT)): sha256_file(p) for p in files}


def context(price_path: Path) -> tuple[Config, StudyClient, dict]:
    config = load_config()
    load_dotenv(REPO_ROOT / ".env")
    snapshot = load_snapshot(price_path)
    return config, StudyClient(config, snapshot), {c.label: price_for(snapshot, c.model_id) for c in config.candidates}


def prompts() -> dict:
    return {k: load_prompt(p, "extraction") for k, p in PROMPTS.items()}


def confirm_seed() -> int:
    return int(hashlib.sha256((ROOT / "selection.json").read_bytes()).hexdigest()[:8], 16)


# ---------------------------------------------------------------- label verification

def verifier_schema() -> dict[str, Any]:
    schema = output_json_schema("extraction")
    schema["properties"]["refund_date_status"] = {"type": "string", "enum": list(STATUSES)}
    schema["required"] = [*schema["required"], "refund_date_status"]
    return schema


def label_mismatches(example: Example, parsed: dict[str, Any] | None) -> list[str]:
    if parsed is None:
        return ["unparsed"]
    review = requires_review(example)
    fields = score("extraction", example.expected, parsed).fields
    wrong = [f for f, ok in fields.items() if not ok and not (review and f == "refund_date")]
    status = parsed.get("refund_date_status")
    expected_status = "needs_review" if review else ("issue_date" if example.expected["refund_date"] else "no_issue_date")
    return wrong + ([] if status == expected_status else ["refund_date_status"])


def verify_one(client, config, candidate, price, prompt_text: str, example: Example) -> dict[str, Any]:
    schema = verifier_schema()
    result = client.chat(model=candidate.model_id, max_tokens=candidate.max_tokens, temperature=config.temperature,
                         seed=config.seed, extra_body=candidate.extra_body,
                         messages=[{"role": "system", "content": prompt_text.replace("{schema}", json.dumps(schema, separators=(",", ":")))},
                                   {"role": "user", "content": example.input}],
                         response_format={"type": "json_schema", "json_schema": {"name": "verified", "schema": schema, "strict": True}})
    parsed = None
    if result.content and not result.error:
        try:
            parsed = json.loads(result.content)
        except json.JSONDecodeError:
            parsed = None
    has_usage = result.prompt_tokens is not None and result.completion_tokens is not None
    return {"example_id": example.id, "candidate": candidate.label, "model_id": candidate.model_id,
            "error": result.error, "content": result.content, "parsed": parsed,
            "mismatches": None if result.error else label_mismatches(example, parsed),
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "reasoning_tokens": result.reasoning_tokens, "total_ms": result.total_ms, "started_at": result.started_at,
            "cost_usd": call_cost_usd(price, result.prompt_tokens, result.completion_tokens, result.cached_tokens or 0)
            if has_usage else None}


def verify_labels(client, config: Config, examples: list[Example], prices: dict, run: RunDir) -> list[dict[str, Any]]:
    """Reasoning Super and Ultra check every label; transport failures get at most two sequential retries."""
    text = prompts()["verifier"].text
    cands = [config.with_reasoning(config.candidate(n), "on") for n in ("super", "ultra")]
    lock = threading.Lock()

    def job(ex: Example, cand) -> Any:
        def run_one() -> dict[str, Any]:
            rec = verify_one(client, config, cand, prices[cand.name], text, ex)
            with lock:
                run.append("calls.jsonl", rec)
            return rec
        return run_one

    records = run_parallel([job(ex, c) for ex in examples for c in cands], concurrency=8, rpm=300)
    by_id = {e.id: e for e in examples}
    for round_ in range(2):
        latest = {(r["example_id"], r["candidate"]): r for r in records}
        failed = [r for r in latest.values() if r["error"]]
        if not failed:
            break
        print(f"Verifier retry round {round_ + 1}: {len(failed)} transport failures, sequential 30 RPM", flush=True)
        pacer = Pacer(30)
        for r in failed:
            pacer.wait()
            cand = next(c for c in cands if c.label == r["candidate"])
            rec = verify_one(client, config, cand, prices[cand.name], text, by_id[r["example_id"]])
            run.append("retry_calls.jsonl", rec)
            records.append(rec)
        time.sleep(30 if round_ == 0 else 0)
    return list({(r["example_id"], r["candidate"]): r for r in records}.values())


def prepare(which: str) -> None:
    out = DIRS[which]
    if (out / "source.json").exists():
        raise ValueError(f"{out} is already prepared; never regenerate a workload")
    if which == "confirm":
        _require_part_b()
        plan = json.loads((ROOT / "plan.json").read_text())
        seed = plan["confirm_seed"]
        if seed != confirm_seed():
            raise ValueError("selection.json changed after the plan")
        per_family = {f: plan["planned_per_family"] for f in plan["families"]}
        sheets = challenge_sheets(seed, per_family, "v5c") + representative_sheets(seed, REPRESENTATIVE_PER_FAMILY, "v5r")
    else:
        seed, sheets = DEV_SEED, challenge_sheets(DEV_SEED, 8, "v5d")
    out.mkdir(parents=True, exist_ok=True)
    (out / "factsheets.jsonl").write_text("".join(json.dumps(s.to_dict()) + "\n" for s in sheets))
    run = RunDir.create("dateswrite")
    config = load_config()
    load_dotenv(REPO_ROOT / ".env")
    fetch_snapshot(TokenFactoryClient(base_url=config.base_url), config.base_url, run.path / "prices.json")
    config, client, prices = context(run.path / "prices.json")
    run.write_manifest(study=STUDY, set=which, seed=seed, planned_n=len(sheets), sources=frozen_sources(),
                       protocol_sha256=sha256_file(PROTOCOL), factsheets_sha256=sha256_file(out / "factsheets.jsonl"))
    print(f"Writer run {run.path}: {len(sheets)} fact sheets", flush=True)
    records = write_all(client, config, sheets, prices, concurrency=8, rpm=360, verify_fn=challenge_verify,
                        sink=lambda r: run.append("writers.jsonl", r))
    examples = to_examples([r for r in records if r["accepted"]])
    dump(run.path / "generation_summary.json", {
        "accepted": len(examples), "planned": len(sheets), "writer_cost_usd": client.spent,
        "accepted_by_family": dict(Counter(e.tags[0] for e in examples)),
        "rejected": [{"id": r["id"], "problems": r["attempts"][-1]["problems"]} for r in records if not r["accepted"]]})
    print(f"{len(examples)}/{len(sheets)} accepted; verifying labels with reasoning Super and Ultra", flush=True)
    vrun = RunDir.create("datesverify")
    vrun.write_manifest(study=STUDY, set=which, writer_run=run.experiment_id, n_examples=len(examples),
                        verifier_prompt_sha256=sha256_file(PROMPTS["verifier"]))
    checks = verify_labels(client, config, examples, prices, vrun)
    by_id = {e.id: e for e in examples}
    queue = defaultdict(dict)
    for r in checks:
        if r["error"] or r["mismatches"]:
            fields = r["mismatches"] or ["transport failure"]
            queue[r["example_id"]][r["candidate"]] = {f: (r["parsed"] or {}).get(f) for f in fields} | {
                "refund_date_status": (r["parsed"] or {}).get("refund_date_status")}
    dump(out / "adjudication_queue.json", [{
        "id": eid, "family": by_id[eid].tags[0], "requires_review": requires_review(by_id[eid]),
        "input": by_id[eid].input, "label": by_id[eid].expected, "verifier_answers": answers}
        for eid, answers in sorted(queue.items())])
    unresolved = sum(bool(r["error"]) for r in checks)
    dump(out / "source.json", {"study": STUDY, "set": which, "seed": seed, "writer_run": run.experiment_id,
                               "verifier_run": vrun.experiment_id, "unresolved_verifier_failures": unresolved,
                               "queued": len(queue), "api_cost_usd": client.spent})
    print(f"Queue: {len(queue)} conversations for blinded review; unresolved verifier failures: {unresolved}; "
          f"spend ${client.spent:.3f}", flush=True)


def _require_part_b() -> None:
    if "## Part B" not in PROTOCOL.read_text():
        raise ValueError("Part B must be appended to the protocol before confirmation data exists")
    tracked = [PROTOCOL, ROOT / "selection.json", ROOT / "plan.json"]
    for path in tracked:
        rel = str(path.relative_to(REPO_ROOT))
        if subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=REPO_ROOT, capture_output=True).returncode \
                or subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=REPO_ROOT).returncode:
            raise ValueError(f"{rel} must be committed unchanged before confirmation data exists")


def build(which: str) -> None:
    out = DIRS[which]
    if (out / "seal.json").exists() or (out / "examples.jsonl").exists():
        raise ValueError(f"{out} is already built")
    source = json.loads((out / "source.json").read_text())
    if source["unresolved_verifier_failures"]:
        raise ValueError("Label verification is incomplete")
    queue = json.loads((out / "adjudication_queue.json").read_text())
    decisions_path = out / "adjudication.jsonl"
    decisions = {d["id"]: d for d in read_jsonl(decisions_path)} if decisions_path.exists() else {}
    missing = [q["id"] for q in queue if q["id"] not in decisions]
    if missing:
        raise ValueError(f"Unresolved blinded label review: {missing}")
    if any(d.get("decision") not in ("keep", "drop") or not d.get("reason") for d in decisions.values()):
        raise ValueError("Each review needs a keep/drop decision and a reason")
    drop = {i for i, d in decisions.items() if d["decision"] == "drop"}
    writers = read_jsonl(REPO_ROOT / "runs" / source["writer_run"] / "writers.jsonl")
    examples = sorted((e for e in to_examples([r for r in writers if r["accepted"]]) if e.id not in drop), key=lambda e: e.id)
    (out / "examples.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in examples))
    splits = ({"dev": [e.id for e in examples]} if which == "dev" else
              {"challenge": [e.id for e in examples if e.id.startswith("v5c-")],
               "representative": [e.id for e in examples if e.id.startswith("v5r-")]})
    dump(out / "splits.json", splits)
    dump(out / "MANIFEST.json", {**source, "n_examples": len(examples), "dropped_by_review": len(drop),
                                 "by_split": {k: len(v) for k, v in splits.items()},
                                 "by_family": dict(Counter(e.tags[0] for e in examples)),
                                 "review_required": sum(REVIEW_TAG in e.tags for e in examples),
                                 "dataset_sha256": sha256_file(out / "examples.jsonl"),
                                 "split_sha256": sha256_file(out / "splits.json"),
                                 "source": "synthetic fact sheets; model-written prose; agent-reviewed labels"})
    if which == "confirm":
        plan = json.loads((ROOT / "plan.json").read_text())
        if plan["sources"] != frozen_sources():
            changed = sorted(k for k in plan["sources"] if plan["sources"][k] != frozen_sources().get(k))
            raise ValueError(f"Frozen code or prompts changed after Part B: {changed}")
        price_path = REPO_ROOT / "runs" / source["writer_run"] / "prices.json"
        dump(out / "seal.json", {
            "study": STUDY, "created_before_confirmation_outcomes": True, "candidate": plan["candidate"],
            "arms": [*ARMS, plan["candidate"]], "sources": frozen_sources(), "protocol_sha256": sha256_file(PROTOCOL),
            "data_sha256": sha256_file(out / "examples.jsonl"), "split_sha256": sha256_file(out / "splits.json"),
            "price_path": str(price_path.relative_to(REPO_ROOT)), "price_sha256": sha256_file(price_path),
            "seed": source["seed"], "deadline_ms": 4000})
    print(f"Built {len(examples)} examples ({', '.join(f'{k}: {len(v)}' for k, v in splits.items())}); "
          f"dropped {len(drop)}", flush=True)


def load_examples(which: str, split: str | None = None) -> list[Example]:
    out = DIRS[which]
    ids = set(json.loads((out / "splits.json").read_text())[split]) if split else None
    return [Example(**r) for r in read_jsonl(out / "examples.jsonl") if ids is None or r["id"] in ids]


# ---------------------------------------------------------------- development

def dev_run() -> None:
    examples = load_examples("dev")
    source = json.loads((DIRS["dev"] / "source.json").read_text())
    config, client, prices = context(REPO_ROOT / "runs" / source["writer_run"] / "prices.json")
    p = prompts()
    run = RunDir.create("datesdev")
    run.write_manifest(study=STUDY, set="dev", n_examples=len(examples), sources=frozen_sources(),
                       data_sha256=sha256_file(DIRS["dev"] / "examples.jsonl"))
    print(f"Development run {run.path}", flush=True)
    run_benchmark(client, config, examples, [(config.candidate(n), prices[n]) for n in ("nano", "super")],
                  {"extraction": p["v4"]}, run, concurrency=8, rpm=300)
    _fallbacks(client, config, prices, p["fallback"], [(ex, m) for ex in examples for m in ("nano", "super", "ultra")], run)
    print(f"Development spend ${client.spent:.3f}", flush=True)


def dev_retry() -> None:
    """Retry, once and sequentially, only development grounded calls that failed in transport; keep the originals."""
    run = RunDir.existing(max((REPO_ROOT / "runs").glob("datesdev-*")))
    if (run.path / "retry_calls.jsonl").exists():
        raise ValueError("Development transport retries already ran")
    failed = [CallRecord(**r) for r in read_jsonl(run.path / "calls.jsonl") if r["error"]]
    source = json.loads((DIRS["dev"] / "source.json").read_text())
    config, client, prices = context(REPO_ROOT / "runs" / source["writer_run"] / "prices.json")
    v4 = prompts()["v4"]
    by_id = {e.id: e for e in load_examples("dev")}
    pacer = Pacer(30)
    from routewise.execution import call_and_score
    for rec in failed:
        pacer.wait()
        run.append("retry_calls.jsonl", call_and_score(client, config, config.candidate(rec.candidate), v4,
                                                         prices[rec.candidate], by_id[rec.example_id]))
    retried = read_jsonl(run.path / "retry_calls.jsonl")
    print(f"Retried {len(failed)} transport failures; still failing: {sum(bool(r['error']) for r in retried)}; "
          f"spend ${client.spent:.3f}", flush=True)


def _fallbacks(client, config, prices, prompt, jobs, run: RunDir) -> list[CallRecord]:
    lock = threading.Lock()

    def job(ex: Example, model: str):
        def run_one() -> CallRecord:
            cand = config.with_reasoning(config.candidate(model), "on")
            rec = call_fallback(client, config, cand, prompt, prices[model], example_id=ex.id, text=ex.input)
            with lock:
                run.append("fallback.jsonl", rec)
            return rec
        return run_one

    return run_parallel([job(ex, m) for ex, m in jobs], concurrency=8, rpm=240)  # type: ignore[return-value]


class _Missing(Exception):
    pass


def outcome(example: Example, d: Decision, latency_ms: float | None = None) -> RequestOutcome:
    s = score("extraction", example.expected, d.final_output)
    return RequestOutcome(
        example_id=example.id, family=example.tags[0], review_required=requires_review(example), released=d.released,
        correct=s.task_correct, field_accuracy=s.field_accuracy, cost_usd=sum(c.cost_usd or 0 for c in d.calls),
        latency_ms=latency_ms if latency_ms is not None else sum(c.total_ms for c in d.calls),
        cost_complete=all(c.cost_usd is not None for c in d.calls), escalated=d.escalated,
        fallback=any(c.reasoning == "on" for c in d.calls))


def _replay_all(examples: list[Example], index: dict) -> tuple[dict[str, list[RequestOutcome]], set]:
    outcomes, missing = defaultdict(list), set()
    guarded = _Guard(index)
    for strategy in STRATEGIES:
        for ex in examples:
            try:
                outcomes[strategy].append(outcome(ex, replay(strategy, ex.input, guarded, ex.id)))
            except _Missing as exc:
                missing.add(exc.args[0])
    return outcomes, missing


class _Guard(dict):
    def __init__(self, index: dict) -> None:
        super().__init__(index)

    def __missing__(self, key):
        raise _Missing(key)


def _v4_index() -> tuple[list[Example], dict]:
    examples = [Example(**r) for r in read_jsonl(REPO_ROOT / "data/v4/examples.jsonl")]
    index = {}
    for r in read_jsonl(V4_TUNE_CALLS):
        rec = CallRecord(**r)
        index[(rec.candidate, rec.example_id)] = rec
    observations = sorted(read_jsonl(V4_HELDOUT_RUN / "observations.jsonl"), key=lambda o: o["window"])
    arm_label = {"fixed_grounded_nano": "nano", "fixed_grounded_super": "super"}
    for o in observations:
        label = arm_label.get(o["trace"]["strategy"])
        if label:
            rec = CallRecord(**o["trace"]["calls"][0])
            index.setdefault((label, rec.example_id), rec)
    ids = {k[1] for k in index}
    return [e for e in examples if e.id in ids], index


def select() -> None:
    if (ROOT / "selection.json").exists():
        raise ValueError("Selection is final; a revision needs a protocol amendment")
    dev = load_examples("dev")
    dev_run_dir = max((REPO_ROOT / "runs").glob("datesdev-*"))
    dev_index = {}
    for name in ("calls.jsonl", "retry_calls.jsonl", "fallback.jsonl"):  # a successful retry supersedes a transport failure
        if not (dev_run_dir / name).exists():
            continue
        for r in read_jsonl(dev_run_dir / name):
            rec = CallRecord(**r)
            dev_index[(rec.candidate, rec.example_id)] = rec
    v4, v4_index = _v4_index()
    for prior in sorted((REPO_ROOT / "runs").glob("datesv4fb-*")):
        for r in read_jsonl(prior / "fallback.jsonl"):
            rec = CallRecord(**r)
            v4_index.setdefault((rec.candidate, rec.example_id), rec)
    _, missing = _replay_all(v4, v4_index)
    if missing:
        source = json.loads((DIRS["dev"] / "source.json").read_text())
        config, client, prices = context(REPO_ROOT / "runs" / source["writer_run"] / "prices.json")
        run = RunDir.create("datesv4fb")
        run.write_manifest(study=STUDY, purpose="reasoning fallbacks for the exposed v4 replay", n=len(missing))
        by_id = {e.id: e for e in v4}
        for rec in _fallbacks(client, config, prices, prompts()["fallback"],
                              [(by_id[eid], label.split("+")[0]) for label, eid in sorted(missing)], run):
            v4_index[(rec.candidate, rec.example_id)] = rec
        print(f"{len(missing)} v4 fallback calls, ${client.spent:.3f}", flush=True)
    dev_out, dev_missing = _replay_all(dev, dev_index)
    v4_out, v4_missing = _replay_all(v4, v4_index)
    if dev_missing or v4_missing:
        raise RuntimeError(f"Missing recorded calls: {sorted(dev_missing | v4_missing)[:5]}")
    dev_m = {s: summarize(o) for s, o in dev_out.items()}
    v4_m = {s: summarize(o) for s, o in v4_out.items()}
    control_dev, control_v4 = dev_m["control"], v4_m["control"]
    eligible = [c for c in CANDIDATES
                if dev_m[c]["undetected_errors"] <= control_dev["undetected_errors"]
                and v4_m[c]["undetected_errors"] <= control_v4["undetected_errors"]
                and v4_m[c]["cac_rate"] >= control_v4["cac_rate"] - 0.005
                and v4_m[c]["p95_latency_ms"] <= 4000]
    selected = None
    if eligible:
        best = max(dev_m[c]["cac_rate"] for c in eligible)
        near = [c for c in eligible if dev_m[c]["cac_rate"] >= best - 0.03]
        selected = min(near, key=lambda c: (v4_m[c]["cost_per_1k_tasks"], v4_m[c]["p95_latency_ms"]))
    vs_control = {c: paired(dev_out[c], dev_out["control"], "cac") for c in CANDIDATES}
    result = {"study": STUDY, "selected": selected, "eligible": eligible,
              "rule": "Part A selection rule, experiments/M7-relative-date-recovery.md",
              "dev_run": dev_run_dir.name, "dev_data_sha256": sha256_file(DIRS["dev"] / "examples.jsonl"),
              "sources": frozen_sources(), "dev": dev_m, "v4_replay": v4_m, "dev_cac_vs_control": vs_control,
              "dev_by_family": _by_family(dev_out)}
    dump(ROOT / "selection.json", result)
    print(json.dumps({"selected": selected, "eligible": eligible,
                      "dev_cac": {s: round(m["cac_rate"], 3) for s, m in dev_m.items()},
                      "dev_undetected": {s: m["undetected_errors"] for s, m in dev_m.items()},
                      "v4_cac": {s: round(m["cac_rate"], 4) for s, m in v4_m.items()},
                      "v4_cost": {s: round(m["cost_per_1k_tasks"], 4) for s, m in v4_m.items()},
                      "v4_p95": {s: round(m["p95_latency_ms"]) for s, m in v4_m.items()}}, indent=1), flush=True)


def _by_family(out: dict[str, list[RequestOutcome]]) -> list[dict[str, Any]]:
    rows = []
    for strategy, outs in out.items():
        for family in sorted({o.family for o in outs}):
            m = summarize([o for o in outs if o.family == family])
            rows.append({"strategy": strategy, "family": family, "n": m["n"], "cac_rate": m["cac_rate"],
                         "review_rate": m["review_rate"], "undetected_errors": m["undetected_errors"],
                         "cost_per_1k_tasks": m["cost_per_1k_tasks"], "p95_latency_ms": m["p95_latency_ms"]})
    return rows


def plan() -> None:
    if (ROOT / "plan.json").exists():
        raise ValueError("The plan is final")
    selection = json.loads((ROOT / "selection.json").read_text())
    candidate = selection["selected"]
    if candidate is None:
        raise ValueError("No eligible candidate; Part A stops here without confirmation")
    p = selection["dev_cac_vs_control"][candidate]
    discordant = (p["a_only"] + p["b_only"]) / p["n_pairs"]
    result = {"study": STUDY, "candidate": candidate, **sample_size(p["diff"], discordant),
              "families": list(CHALLENGE_FAMILIES), "representative_per_family": REPRESENTATIVE_PER_FAMILY,
              "confirm_seed": confirm_seed(), "selection_sha256": sha256_file(ROOT / "selection.json"),
              "sources": frozen_sources()}
    dump(ROOT / "plan.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "sources"}, indent=1), flush=True)


# ---------------------------------------------------------------- confirmation

def evaluate(run_path: Path | None = None) -> Path:
    seal = json.loads((DIRS["confirm"] / "seal.json").read_text())
    if seal["sources"] != frozen_sources() or seal["protocol_sha256"] != sha256_file(PROTOCOL):
        raise ValueError("Frozen code, prompts or protocol changed after sealing")
    if seal["data_sha256"] != sha256_file(DIRS["confirm"] / "examples.jsonl") or \
            seal["split_sha256"] != sha256_file(DIRS["confirm"] / "splits.json") or \
            seal["price_sha256"] != sha256_file(REPO_ROOT / seal["price_path"]):
        raise ValueError("Sealed data, split or prices changed")
    config, client, prices = context(REPO_ROOT / seal["price_path"])
    p = prompts()
    splits = json.loads((DIRS["confirm"] / "splits.json").read_text())
    set_of = {i: name for name, ids in splits.items() for i in ids}
    examples = load_examples("confirm")
    Random(seal["seed"]).shuffle(examples)
    candidate = seal["candidate"]
    routers = {
        "control": Router(client, config, prices, {"extraction": p["v4"]}, {"extraction": RouteOption("nano", "super")},
                          strategy="control"),
        "fixed_grounded_super": Router(client, config, prices, {"extraction": p["v4"]},
                                       {"extraction": RouteOption("super")}, strategy="fixed_grounded_super"),
        candidate: RecoveryRouter(client, config, prices, p["v4"], p["fallback"], strategy=candidate),
    }
    if run_path:
        run = RunDir.existing(run_path)
        if json.loads((run.path / "manifest.json").read_text())["seal_sha256"] != sha256_file(DIRS["confirm"] / "seal.json"):
            raise ValueError("Resume seal mismatch")
    else:
        for m in sorted((REPO_ROOT / "runs").glob("datesconfirm-*/manifest.json")):
            if json.loads(m.read_text()).get("seal_sha256") == sha256_file(DIRS["confirm"] / "seal.json"):
                raise ValueError(f"A run for this seal exists; resume with --run {m.parent}")
        run = RunDir.create("datesconfirm")
        run.write_manifest(study=STUDY, seal=seal, seal_sha256=sha256_file(DIRS["confirm"] / "seal.json"),
                           arms=list(routers), n_unique=len(examples), by_set={k: len(v) for k, v in splits.items()},
                           n_expected_observations=len(examples) * len(routers))
    print(f"Confirmation run {run.path}", flush=True)
    path = run.path / "observations.jsonl"
    done = {(r["trace"]["example_id"], r["trace"]["strategy"]) for r in read_jsonl(path)} if path.exists() else set()
    jobs = []
    for ex in examples:
        names = list(routers)
        Random(f"{seal['seed']}:{ex.id}").shuffle(names)
        jobs += [(ex, n) for n in names if (ex.id, n) not in done]
    nano_id = config.candidate("nano").model_id
    client.pacers[nano_id] = Pacer(0)  # admission below is outside the request timer
    admission = Pacer(90)
    lock = threading.Lock()

    def job(ex: Example, name: str) -> None:
        delivered = []
        start = time.perf_counter()
        trace = routers[name].route("extraction", ex.input, example_id=ex.id, expected=ex.expected,
                                    on_answer=lambda _: delivered.append((time.perf_counter() - start) * 1000))
        trace.latency_ms = delivered[0]
        record = {"set": set_of[ex.id], "family": ex.tags[0], "review_required": requires_review(ex),
                  "wall_duration_ms": (time.perf_counter() - start) * 1000, "trace": trace.model_dump()}
        with lock:
            run.append("observations.jsonl", record)
            done.add((ex.id, name))
            if len(done) % 60 == 0:
                print(f"  {len(done)} completed requests; spend ${client.spent:.3f}", flush=True)

    print(f"{len(jobs)} randomized requests", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for ex, name in jobs:
            if name != "fixed_grounded_super":
                admission.wait()
            futures.append(pool.submit(job, ex, name))
        for f in as_completed(futures):
            f.result()
    report(run.path)
    return run.path


def trace_outcome(row: dict[str, Any]) -> RequestOutcome:
    t = Trace(**row["trace"])
    s = t.score or {"task_correct": False, "field_accuracy": 0.0}
    return RequestOutcome(example_id=t.example_id, family=row["family"], review_required=row["review_required"],
                          released=t.final_valid, correct=bool(s["task_correct"]), field_accuracy=float(s["field_accuracy"]),
                          cost_usd=t.cost_usd, latency_ms=t.latency_ms, cost_complete=t.cost_complete,
                          escalated=t.escalated, fallback=any(c.reasoning == "on" for c in t.calls))


def report(run_path: Path) -> dict[str, Any]:
    manifest = json.loads((run_path / "manifest.json").read_text())
    seal = manifest["seal"]
    candidate = seal["candidate"]
    rows = read_jsonl(run_path / "observations.jsonl")
    primary: dict[tuple[str, str], dict] = {}
    for r in rows:
        primary.setdefault((r["trace"]["example_id"], r["trace"]["strategy"]), r)
    by = defaultdict(lambda: defaultdict(list))
    for r in primary.values():
        by[r["set"]][r["trace"]["strategy"]].append(trace_outcome(r))
    arms = manifest["arms"]
    complete = all(len(by[s][a]) == n for s, n in manifest["by_set"].items() for a in arms)
    result: dict[str, Any] = {
        "study": STUDY, "display_milestone": "M6", "run": run_path.name, "candidate": candidate, "complete": complete,
        "n_observations": len(rows), "n_expected_observations": manifest["n_expected_observations"],
        "metrics": {s: {a: summarize(o) for a, o in arms_.items()} for s, arms_ in by.items()},
        "families": {s: _by_family(arms_) for s, arms_ in by.items()},
        "paired": {s: {f"{candidate} - {other}": {m: paired(arms_[candidate], arms_[other], m)
                                                  for m in ("cac", "undetected", "review", "cost_per_1k")}
                       for other in ARMS} for s, arms_ in by.items()},
        "total_api_cost_usd": sum(r["trace"]["cost_usd"] for r in rows),
        "incomplete_cost_requests": sum(not r["trace"]["cost_complete"] for r in rows),
    }
    result["decision"] = gates(by["challenge"], by["representative"], candidate) if complete else None
    dump(run_path / "summary.json", result)
    print(json.dumps({"complete": complete, "decision": result["decision"] and result["decision"]["checks"],
                      "adopt": result["decision"] and result["decision"]["adopt"],
                      "total_api_cost_usd": round(result["total_api_cost_usd"], 4)}, indent=1), flush=True)
    return result


def main(stage: str, which: str | None = None, run: Path | None = None) -> None:
    if stage == "prepare":
        prepare(which or "dev")
    elif stage == "build":
        build(which or "dev")
    elif stage == "dev-run":
        dev_run()
    elif stage == "dev-retry":
        dev_retry()
    elif stage == "select":
        select()
    elif stage == "plan":
        plan()
    elif stage == "evaluate":
        evaluate(run)
    elif stage == "report":
        if not run:
            raise ValueError("report requires --run")
        report(run)
    else:
        raise ValueError(stage)
