"""Independent workload validation of the adopted extraction profile (study m8, displayed as M7).

Run with `routewise validation <stage>`. People write every label (labels.py); no model output
is ever used as, or compared against, a label before the labels are built.

import       two independent human labels per conversation, plus adjudication -> a labelled dataset
run          adopted M6 profile, previous M5 profile and grounded Super on identical conversations
report       outcomes per request, paired comparisons and diagnostics (no API calls)
export       stage a reviewable aggregate, or publish one a person approved by its SHA-256
propose      the confirmation design and sample size a complete pilot implies (no API calls)
preregister  copy an accepted proposal into the protocol as Part B, to commit before held-out data exists
seal         freeze a held-out dataset with the design, code, prompts, profiles and prices
leak-scan    look for private values in this repository's files and recent commits
drill        rehearse all of this on synthetic, sensitive-looking conversations (privacy_drill.py)

Private mode (private_mode.py): labels are read from an input directory and everything is written to an
output directory, both outside every git repository. Only reviewed aggregate exports, the design and the
hash-only seal are written into the repository, each after a check against the private data.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from routewise import labels as label_io
from routewise import private_mode, validation_design, validation_export
from routewise.benchmark import Pacer
from routewise.client import TokenFactoryClient
from routewise.confirmation import StudyClient
from routewise.config import REPO_ROOT, Config, load_config, load_dotenv
from routewise.evaluate import score
from routewise.evaluation import profile_routers, run_evaluation
from routewise.issue_dates import analyze_issue_date
from routewise.labels import LabeledConversation
from routewise.private_mode import PrivateDirs, SafeError
from routewise.prices import fetch_snapshot, load_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.recovery_metrics import METRICS, RequestOutcome, paired, summarize
from routewise.router import Trace
from routewise.runlog import RunDir, read_jsonl, sha256_file

STUDY, DISPLAY = "m8", "M7"
PROTOCOL = REPO_ROOT / "experiments/M8-workload-validation.md"
DESIGN = REPO_ROOT / "experiments/M8-workload-validation.design.json"
PUBLIC_SEAL = REPO_ROOT / "experiments/M8-workload-validation.seal.json"
CONFIG = REPO_ROOT / "config/grounded.yaml"
PROFILES = {"adopted": REPO_ROOT / "policies/deployment/m7-extraction.json",
            "previous": REPO_ROOT / "policies/deployment/m6-extraction.json"}
ARMS = ("adopted", "previous", "grounded_super")
ARM_LABELS = {"adopted": "Adopted profile (M6)", "previous": "Previous profile (M5)", "grounded_super": "Grounded Super"}
PILOT_SIZE = (50, 100)
RUN_KINDS = {"pilot": "valpilot", "smoke": "valsmoke", "confirmation": "valconfirm"}
PLACEHOLDER = "[TO COMPLETE:"
LOAD = {"concurrency": 4, "rpm": 90}  # admissions per minute, paced outside the request timer
# Orchestration and privacy plumbing: tracked by git, not sealed, because they decide no outcome or analysis.
UNSEALED = {"workload_validation.py", "private_mode.py", "validation_export.py", "privacy_drill.py"}


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def frozen_sources() -> dict[str, str]:
    """Everything that decides an outcome or its analysis."""
    files = [p for p in sorted((REPO_ROOT / "src/routewise").glob("*.py")) if p.name not in UNSEALED]
    files += [CONFIG, load_config(CONFIG).prompts["extraction"], *PROFILES.values()]
    return {_rel(p): sha256_file(p) for p in files}


def fetch_prices(config: Config, path: Path) -> None:
    fetch_snapshot(TokenFactoryClient(base_url=config.base_url), config.base_url, path)


def _committed_at(path: Path) -> datetime:
    stamp = subprocess.run(["git", "log", "-1", "--format=%cI", "--", _rel(path)], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()
    return datetime.fromisoformat(stamp)


def _require_committed(paths: list[Path]) -> None:
    for path in paths:
        rel = _rel(path)
        if not path.exists() or subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=REPO_ROOT,
                                               capture_output=True).returncode \
                or subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=REPO_ROOT).returncode:
            raise SafeError(f"{rel} must be committed unchanged first")


# ---------------------------------------------------------------- import


def _label_files(folder: Path, *, required: bool) -> list[Path]:
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")) if folder.is_dir() else []
    if any(p.suffix.lower() not in (".csv", ".jsonl") for p in files):
        raise SafeError(f"{folder} may hold only .csv and .jsonl label files")
    if required and not files:
        raise SafeError(f"Put each labeler's file (CSV or JSONL) in {folder}")
    return files


def import_labels(dirs: PrivateDirs, name: str) -> Path:
    assert dirs.input is not None
    source, out = dirs.input / name, dirs.output / "data" / name
    if (out / "seal.json").exists():
        raise SafeError(f"{out} is sealed; sealed labels cannot change")
    label_files = _label_files(source / "labels", required=True)
    adjudication = _label_files(source / "adjudication", required=False)
    label_rows = [row for path in label_files for row in label_io.read_rows(path)]
    synthetic = {str(row.get("conversation_id") or "").strip().upper().startswith(private_mode.SYNTHETIC_PREFIX)
                 for _, row in label_rows}
    if len(synthetic) > 1:
        raise SafeError(f"{source} mixes synthetic drill conversations ({private_mode.SYNTHETIC_PREFIX}...) with real ones")
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = label_io.assemble(label_rows, [row for path in adjudication for row in label_io.read_rows(path)])
    except label_io.LabelError as exc:  # its messages quote label values, so they stay in the output directory
        (out / "label-problems.txt").write_text("\n".join(exc.errors) + "\n")
        raise SafeError(f"{len(exc.errors)} problem(s) in the label files, each listed by file and row in "
                        f"{out / 'label-problems.txt'}. Nothing was built.") from None
    (out / "label-problems.txt").unlink(missing_ok=True)
    dump(out / "agreement.json", result.agreement)
    if result.queue:
        label_io.write_queue(out / "adjudication_queue.csv", result.queue)
        print(f"{len(result.queue)} conversation(s) need adjudication: {out / 'adjudication_queue.csv'}\n"
              "A third person fills decision, adjudicator_id, reason and (for 'label') the label columns, saves "
              f"the file in {source / 'adjudication'}, and reruns import. No dataset was built.", flush=True)
        return out
    (out / "adjudication_queue.csv").unlink(missing_ok=True)
    previous = json.loads((out / "MANIFEST.json").read_text()) if (out / "MANIFEST.json").exists() else None
    records = sorted(result.records, key=lambda r: r.id)
    (out / "examples.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in records))
    dump(out / "excluded.json", result.excluded)
    digest = sha256_file(out / "examples.jsonl")
    history = (previous or {}).get("previous_versions", [])
    if previous and previous["dataset_sha256"] != digest:
        history = [*history, {"dataset_sha256": previous["dataset_sha256"], "built_at": previous["built_at"]}]
    dump(out / "MANIFEST.json", {
        "study": STUDY, "dataset": name, "built_at": datetime.now(UTC).isoformat(),
        "n_labelled": len(records) + len(result.excluded), "n_examples": len(records),
        "n_excluded": len(result.excluded), "n_adjudicated": result.adjudicated,
        "by_split": dict(Counter(r.split for r in records)),
        "review_required": sum(r.review_required for r in records),
        "review_reasons": dict(Counter((r.review_reason or "").split(":")[0].split(" | ")[0]
                                       for r in records if r.review_required)),
        "primary_scenarios": dict(Counter(r.family for r in records)),
        "scenario_tags": dict(Counter(t for r in records for t in r.tags)),
        "labelers": sorted({x for r in records for x in r.labelers}),
        "adjudicators": sorted({r.adjudicator for r in records if r.adjudicator}
                               | {e["adjudicator"] for e in result.excluded}),
        "agreement": result.agreement, "pii_warnings": result.pii_warnings,
        "label_files": {p.name: sha256_file(p) for p in label_files},
        "adjudication_files": {p.name: sha256_file(p) for p in adjudication},
        "dataset_sha256": digest, "previous_versions": history, "synthetic": synthetic == {True},
        "source": "synthetic drill: generated conversations and labels, never evidence" if synthetic == {True} else
                  "human labels: two independent labelers per conversation; a third person adjudicated disagreements",
    })
    agree = result.agreement
    print(f"Built {len(records)} conversations ({dict(Counter(r.split for r in records))}); excluded "
          f"{len(result.excluded)}; adjudicated {result.adjudicated}. Labelers agreed on "
          f"{agree['metric_agreement_rate']:.1%} of conversations (review-required kappa "
          f"{_fmt(agree['review_required']['kappa'])}).", flush=True)
    if result.pii_warnings:
        print(f"Warning: {len(result.pii_warnings)} conversation(s) may contain personal data "
              f"({dict(Counter(k for w in result.pii_warnings for k in w['kinds']))}); see {out / 'MANIFEST.json'}. "
              "De-identify before any run: conversation text is sent to Nebius Token Factory.", flush=True)
    return out


def load_dataset(data: Path, split: str | None = None) -> list[LabeledConversation]:
    path = data / "examples.jsonl"
    if not path.exists():
        raise SafeError(f"{data} has no built dataset; run `routewise validation import` first")
    return [LabeledConversation(**r) for r in read_jsonl(path) if split is None or r["split"] == split]


# ---------------------------------------------------------------- live run


def run(output: Path, name: str | None, split: str, *, smoke: int | None = None, resume: Path | None = None,
        budget: float = 5.0) -> Path:
    if resume:
        manifest = json.loads((resume / "manifest.json").read_text())
        name, split, kind = manifest["dataset"]["name"], manifest["split"], manifest["kind"]
    data = output / "data" / str(name)
    records = load_dataset(data, split)
    if resume:
        if manifest["dataset"]["sha256"] != sha256_file(data / "examples.jsonl"):
            raise SafeError("The labels changed since this run started; start a new run instead of resuming")
        wanted = set(manifest["example_ids"])
        records = [r for r in records if r.id in wanted]
    else:
        kind = "confirmation" if split == "heldout" else ("smoke" if smoke else "pilot")
        if kind == "confirmation" and smoke:
            raise SafeError("Held-out conversations are never used for smoke tests")
        if kind != "confirmation" and smoke:
            records = records[:smoke]
        if kind == "pilot" and not PILOT_SIZE[0] <= len(records) <= PILOT_SIZE[1]:
            raise SafeError(f"A diagnostic pilot uses {PILOT_SIZE[0]}-{PILOT_SIZE[1]} development conversations; "
                             f"{name} has {len(records)}. Use --smoke N for a plumbing check.")
    if not records:
        raise SafeError(f"{name} has no {split} conversations")
    seal = check_seal(data) if kind == "confirmation" else None
    if seal and not resume:
        if len(records) != seal["n"]:
            raise SafeError("The held-out dataset does not match its seal")
        for m in sorted((output / "runs").glob("valconfirm-*/manifest.json")):
            if json.loads(m.read_text()).get("seal_sha256") == sha256_file(data / "seal.json"):
                raise SafeError(f"A confirmation run for this seal exists; resume it with --run {m.parent}")
    seed: int = seal["seed"] if seal else int(sha256_file(data / "examples.jsonl")[:8], 16)
    config = load_config(CONFIG)
    load_dotenv(REPO_ROOT / ".env")
    run_dir = RunDir.existing(resume) if resume else RunDir.create(RUN_KINDS[kind], root=output / "runs")
    price_path = data / "prices.json" if seal else run_dir.path / "prices.json"
    if not price_path.exists():
        fetch_prices(config, price_path)
    snapshot = load_snapshot(price_path)
    client = StudyClient(config, snapshot, budget)
    client.pacers = {model: Pacer(0) for model in client.pacers}  # admission is paced below, outside the request timer
    prices = {c.label: price_for(snapshot, c.model_id) for c in config.candidates}
    prompts = {task: load_prompt(path, task) for task, path in config.prompts.items()}
    profiles = {arm: json.loads(path.read_text()) for arm, path in PROFILES.items()}
    routers = profile_routers(client, config, profiles, prices, prompts)
    if not resume:
        run_dir.write_manifest(
            study=STUDY, display_milestone=DISPLAY, kind=kind, split=split,
            dataset={"name": name, "sha256": sha256_file(data / "examples.jsonl"), "n": len(records)},
            example_ids=[r.id for r in records], arms={
                **{arm: {"profile": _rel(PROFILES[arm]), "sha256": sha256_file(PROFILES[arm]),
                         "policy_id": profiles[arm]["policy_id"]} for arm in PROFILES},
                "grounded_super": {"route": "super", "prompt": prompts["extraction"].version}},
            prompt={"version": prompts["extraction"].version, "sha256": prompts["extraction"].sha256},
            config={"path": _rel(CONFIG), "sha256": sha256_file(CONFIG)},
            prices={"path": _rel(price_path), "sha256": sha256_file(price_path)},
            order_seed=seed, seal_sha256=sha256_file(data / "seal.json") if seal else None,
            protocol_sha256=sha256_file(PROTOCOL) if PROTOCOL.exists() else None,
            load=LOAD, budget_usd=budget)
    path = run_dir.path / "traces.jsonl"
    done = {(t["example_id"], t["strategy"]) for t in read_jsonl(path)} if path.exists() else set()
    print(f"{kind} run {_rel(run_dir.path)}: {len(records)} conversations x {len(routers)} arms, "
          f"{len(records) * len(routers) - len(done)} requests to make", flush=True)
    run_evaluation(routers, [r.example() for r in records], run_dir, concurrency=LOAD["concurrency"],
                   rpm=LOAD["rpm"], order_seed=seed, skip=done)
    print(f"API spend ${client.spent:.4f}", flush=True)
    report(run_dir.path)
    return run_dir.path


# ---------------------------------------------------------------- report


def outcome(record: LabeledConversation, trace: Trace) -> RequestOutcome:
    """Scored against the current labels, so a disclosed pilot relabel is reflected."""
    s = score("extraction", record.expected, trace.final_output)
    return RequestOutcome(
        example_id=record.id, family=record.family, review_required=record.review_required,
        released=trace.final_valid, correct=s.task_correct, field_accuracy=s.field_accuracy,
        cost_usd=trace.cost_usd, latency_ms=trace.latency_ms, cost_complete=trace.cost_complete,
        escalated=trace.escalated, fallback=any(c.reasoning == "on" for c in trace.calls))


def review_cause(trace: Trace) -> str:
    action = (trace.recovery or {}).get("action") or ""
    if action.startswith("review: ") and action != "review: escalation failed validation":
        return "issue date: " + action.removeprefix("review: ").split(":")[0]
    failed = trace.validations[-1]["failed_rules"] if trace.validations else []
    return "validation: " + (", ".join(sorted(failed)) or "no answer")


def label_kind(record: LabeledConversation) -> str:
    if record.review_required:
        return "review required"
    return "issue date" if record.expected["refund_date"] else "no issue date"


def issue_date_coverage(records: list[LabeledConversation]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The deployed issue-date analyzer against the human labels; deterministic, no model involved."""
    table: Counter = Counter()
    agree, mismatches = 0, []
    for r in records:
        a = analyze_issue_date(r.input)
        status = a.status if a.status != "review" else (
            "review (known ambiguity)" if a.known_ambiguity else "review (unrecognized wording)")
        table[(status, label_kind(r))] += 1
        if r.review_required:
            same = a.status == "review"
        else:
            same = a.status != "review" and (a.date if a.status == "resolved" else None) == r.expected["refund_date"]
        agree += same
        if not same:
            mismatches.append({"id": r.id, "analyzer": status, "analyzer_date": a.date, "reason": a.reason,
                               "label": label_kind(r), "label_refund_date": r.expected["refund_date"],
                               "review_reason": r.review_reason})
    rows = [{"analyzer": s, "label": lab, "n": n} for (s, lab), n in sorted(table.items())]
    return {"table": rows, "agrees_with_label": agree, "disagrees_with_label": len(mismatches),
            "unrecognized_wording": sum(n for (s, _), n in table.items() if s == "review (unrecognized wording)")}, \
        mismatches


def _outcomes(run_path: Path) -> tuple[dict[str, Any], dict[str, LabeledConversation], dict[str, dict[str, tuple]]]:
    manifest = json.loads((run_path / "manifest.json").read_text())
    data = run_path.parent.parent / "data" / manifest["dataset"]["name"]
    wanted = set(manifest["example_ids"])
    records = {r.id: r for r in load_dataset(data, manifest["split"]) if r.id in wanted}
    first: dict[tuple[str, str], Trace] = {}
    for row in read_jsonl(run_path / "traces.jsonl") if (run_path / "traces.jsonl").exists() else []:
        first.setdefault((row["example_id"], row["strategy"]), Trace(**row))
    by_arm: dict[str, dict[str, tuple]] = defaultdict(dict)
    for (eid, arm), trace in first.items():
        if eid in records:
            by_arm[arm][eid] = (outcome(records[eid], trace), trace)
    return manifest, records, by_arm


def report(run_path: Path) -> dict[str, Any]:
    manifest, records, by_arm = _outcomes(run_path)
    data = run_path.parent.parent / "data" / manifest["dataset"]["name"]
    current = sha256_file(data / "examples.jsonl")
    ids = sorted(records)
    outcomes = {arm: [by_arm[arm][i][0] for i in ids if i in by_arm[arm]] for arm in ARMS}
    complete = len(ids) == len(manifest["example_ids"]) and all(len(o) == len(ids) for o in outcomes.values())
    coverage, coverage_detail = issue_date_coverage([records[i] for i in ids])
    dataset_manifest = json.loads((data / "MANIFEST.json").read_text())
    summary: dict[str, Any] = {
        "study": STUDY, "display_milestone": DISPLAY, "kind": manifest["kind"], "run": run_path.name,
        "split": manifest["split"], "n": len(ids), "complete": complete,
        "labels": {"dataset_sha256_at_run": manifest["dataset"]["sha256"], "dataset_sha256_now": current,
                   "revised_after_run": current != manifest["dataset"]["sha256"],
                   "agreement": dataset_manifest["agreement"], "n_excluded": dataset_manifest["n_excluded"],
                   "n_adjudicated": dataset_manifest["n_adjudicated"]},
        "arms": {arm: ARM_LABELS[arm] for arm in ARMS},
        "metrics": {arm: summarize(o) for arm, o in outcomes.items()},
        "paired": {f"adopted - {other}": {m: paired(outcomes["adopted"], outcomes[other], m) for m in METRICS}
                   for other in ("previous", "grounded_super")},
        "by_tag": _by_tag(outcomes, records),
        "wrong_fields": {arm: _wrong_fields(by_arm[arm].values(), records) for arm in ARMS},
        "review_causes": {arm: dict(Counter(review_cause(t) for o, t in by_arm[arm].values() if not o.released))
                          for arm in ARMS},
        "issue_date_coverage": coverage,
        "total_api_cost_usd": sum(t.cost_usd for arm in by_arm.values() for _, t in arm.values()),
        "incomplete_cost_requests": sum(not t.cost_complete for arm in by_arm.values() for _, t in arm.values()),
    }
    if manifest["kind"] == "confirmation" and complete:
        design = json.loads(DESIGN.read_text())
        sealed = json.loads((data / "seal.json").read_text())
        if manifest["seal_sha256"] != sha256_file(data / "seal.json") or summary["labels"]["revised_after_run"] \
                or sealed["design_sha256"] != sha256_file(DESIGN):
            raise SafeError("The seal, the design or the held-out labels changed after the confirmation run")
        summary["decision"] = validation_design.decide(
            design["parameters"], summary["metrics"]["adopted"], summary["paired"]["adopted - previous"],
            summary["paired"]["adopted - grounded_super"])
    dump(run_path / "summary.json", summary)
    dump(run_path / "diagnostics.json", {
        "note": "Names conversations and shows extracted values; keep private.",
        "wrong_releases": {arm: [_explain(records[o.example_id], t) for o, t in by_arm[arm].values() if o.undetected]
                           for arm in ARMS},
        "avoidable_reviews": {arm: [{"id": o.example_id, "cause": review_cause(t),
                                     "recovery": (t.recovery or {}).get("action")}
                                    for o, t in by_arm[arm].values() if not o.released and not o.review_required]
                              for arm in ARMS},
        "adopted_vs_previous": [
            {"id": i, "adopted": _state(by_arm["adopted"][i][0]), "previous": _state(by_arm["previous"][i][0])}
            for i in ids if i in by_arm["adopted"] and i in by_arm["previous"]
            and _state(by_arm["adopted"][i][0]) != _state(by_arm["previous"][i][0])],
        "issue_date_analyzer_vs_labels": coverage_detail,
    })
    _print(summary)
    return summary


def _wrong_fields(pairs, records: dict[str, LabeledConversation]) -> dict[str, int]:
    """Which fields are wrong in released answers to answerable conversations."""
    wrong: Counter = Counter()
    for o, t in pairs:
        if o.released and not o.review_required and not o.correct:
            fields = score("extraction", records[o.example_id].expected, t.final_output).fields
            wrong.update(f for f, ok in fields.items() if not ok)
    return dict(wrong)


def _state(o: RequestOutcome) -> str:
    return "correct" if o.cac else ("wrong release" if o.undetected else "review")


def _explain(record: LabeledConversation, trace: Trace) -> dict[str, Any]:
    fields = score("extraction", record.expected, trace.final_output).fields
    return {"id": record.id, "review_reason": record.review_reason,
            "wrong": {f: {"label": record.expected[f], "released": (trace.final_output or {}).get(f)}
                      for f, ok in fields.items() if not ok and not record.review_required},
            "released": trace.final_output, "recovery": (trace.recovery or {}).get("action"),
            "answered_by": trace.answered_by}


def _by_tag(outcomes: dict[str, list[RequestOutcome]], records: dict[str, LabeledConversation]) -> list[dict]:
    rows = []
    for tag in sorted({t for r in records.values() for t in r.tags}):
        for arm, outs in outcomes.items():
            m = summarize([o for o in outs if tag in records[o.example_id].tags])
            rows.append({"tag": tag, "arm": arm, "n": m["n"], "cac_rate": m["cac_rate"],
                         "review_rate": m["review_rate"], "wrong_releases": m["undetected_errors"],
                         "task_accuracy": m["task_accuracy"]})
    return rows


def _fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:.1%}" if pct else f"{v:.2f}"


def _print(summary: dict[str, Any]) -> None:
    print(f"\n{summary['kind']} {summary['run']}: {summary['n']} conversations, complete={summary['complete']}")
    for arm, m in summary["metrics"].items():
        if not m.get("n"):
            continue
        print(f"  {ARM_LABELS[arm]:<22} CAC {_fmt(m['cac_rate'], True):>6}  review {_fmt(m['review_rate'], True):>6} "
              f"(avoidable {_fmt(m['avoidable_review_rate'], True)})  wrong releases {m['undetected_errors']:>3}  "
              f"task acc {_fmt(m['task_accuracy'], True)} (LB {_fmt(m['accuracy_lower95'], True)})  "
              f"${m['cost_per_1k_tasks']:.3f}/1k  p95 {m['p95_latency_ms'] or 0:,.0f} ms  "
              f"misses UB {_fmt(m['deadline_miss_upper95'], True)}  incomplete cost {m['incomplete_cost_count']}")
    if summary["labels"]["revised_after_run"]:
        print("  Labels were revised after this run; outcomes use the current labels.")
    if summary.get("decision"):
        print(f"  Decision: {'VALIDATED' if summary['decision']['validated'] else 'NOT validated'} "
              f"{summary['decision']['checks']}")
    print(f"  API cost ${summary['total_api_cost_usd']:.4f}", flush=True)


# ---------------------------------------------------------------- design


def pilot_inputs(run_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = report(run_path)
    manifest, records, by_arm = _outcomes(run_path)
    if manifest["kind"] != "pilot" or not summary["complete"]:
        raise SafeError("Only a complete diagnostic pilot (50-100 development conversations) can inform the design")
    ids = sorted(records)
    out = {arm: [by_arm[arm][i][0] for i in ids] for arm in ARMS}
    adopted = out["adopted"]
    answerable = [o for o in adopted if not o.review_required]
    data_manifest = json.loads((run_path.parent.parent / "data" / manifest["dataset"]["name"] / "MANIFEST.json")
                               .read_text())
    pilot = {
        "n": len(ids), "n_collected": data_manifest["n_labelled"], "n_excluded": data_manifest["n_excluded"],
        "adopted": {"wrong_releases": sum(o.undetected for o in adopted), "answerable": len(answerable),
                    "correct": sum(o.correct for o in answerable),
                    "field_accuracy": summary["metrics"]["adopted"]["field_accuracy"],
                    "deadline_misses": sum(o.missed_deadline for o in adopted),
                    "incomplete_cost": sum(not o.cost_complete for o in adopted)},
        "vs_previous": {m: paired(adopted, out["previous"], m) for m in ("cac", "undetected")},
        "cost_diffs_vs_grounded_super": [a.cost_usd - s.cost_usd for a, s in zip(adopted, out["grounded_super"])],
        "label_disagreement_rate": 1 - data_manifest["agreement"]["metric_agreement_rate"],
        "cost_per_conversation_usd": summary["total_api_cost_usd"] / len(ids),
        "labels_revised_after_run": summary["labels"]["revised_after_run"],
    }
    return pilot, manifest


def _synthetic(data: Path) -> bool:
    return bool(json.loads((data / "MANIFEST.json").read_text()).get("synthetic"))


def propose(run_path: Path, overrides: dict[str, float]) -> Path:
    pilot, manifest = pilot_inputs(run_path)
    if _synthetic(run_path.parent.parent / "data" / manifest["dataset"]["name"]):
        raise SafeError("Synthetic drill data cannot inform a confirmation design")
    design = validation_design.propose(pilot, overrides)
    design.update(study=STUDY, display_milestone=DISPLAY, created_at=datetime.now(UTC).isoformat(),
                  pilot_run=run_path.name, pilot_dataset_sha256=manifest["dataset"]["sha256"],
                  pilot=pilot | {"cost_diffs_vs_grounded_super": len(pilot["cost_diffs_vs_grounded_super"])},
                  arms=manifest["arms"], prompt=manifest["prompt"], config=manifest["config"])
    out = run_path.parent.parent / "proposals" / run_path.name
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "design.json", design)
    (out / "part-b.md").write_text(part_b(design))
    print(json.dumps({k: design.get(k) for k in ("feasible", "n_required", "binding", "n_collect", "labels_needed",
                                                 "expected_adjudications", "recommendation")}, indent=1))
    print(f"Proposal: {out}/design.json and part-b.md. Replace each {PLACEHOLDER} ...] item in part-b.md, "
          "then run `routewise validation preregister`.", flush=True)
    return out


def part_b(d: dict[str, Any]) -> str:
    p = d["parameters"]
    if not d["feasible"]:
        return f"# No confirmation proposed\n\n{d['recommendation']}\n"
    names = {"primary_wrong_release": "Primary: adopted wrong-release rate",
             "task_accuracy": "Task accuracy (answerable conversations)", "deadline_misses": "Deadline misses at 4 s",
             "cac_vs_previous": "CAC vs previous profile", "wrong_release_vs_previous": "Wrong releases vs previous profile",
             "cost_vs_grounded_super": "Cost vs grounded Super"}
    rows = [f"| {names[key]} | {pilot} | {planning} | {c['n']} | {d['power_at_n_required'][key]:.2f} |"
            for key, c in d["criteria"].items() for pilot, planning in [_planning_cells(key, c)]]
    alt = "; ".join(f"{k}: " + ", ".join(f"{v} → {n or 'not reachable within 5,000'}" for v, n in vals.items())
                    for k, vals in d["alternatives"].items())
    arms = d["arms"]
    revised = ("\n\nPilot labels were revised after the pilot run, with model outputs visible; the planning values "
               "may be optimistic." if d["pilot"]["labels_revised_after_run"] else "")
    changed = [f"{k} {validation_design.DEFAULTS[k]} → {v}" for k, v in p.items() if v != validation_design.DEFAULTS[k]]
    departures = (f"\n\n**Departures from the Part A defaults.** {'; '.join(changed)}. "
                  f"{PLACEHOLDER} the reason for each departure.]" if changed else "")
    return f"""## Part B — confirmation design

Generated on {d['created_at'][:10]} by `routewise validation propose` from the complete diagnostic pilot `{d['pilot_run']}` ({d['pilot']['n']} development conversations, dataset `{d['pilot_dataset_sha256'][:12]}`). The computed values below become the plan once this part is committed.{revised}

**Sampling frame.** [TO COMPLETE: source system, date window, inclusion and exclusion rules, and the random selection procedure.] Use the pilot's frame. Draw held-out conversations only after this part is committed. Conversations must not overlap the pilot; avoid pilot customers and threads where possible.

**Labeling.** As in Part A: two independent people label every conversation, blind to model outputs and to each other, and a third person adjudicates every disagreement that changes a metric. Labelers and adjudicator: [TO COMPLETE: roles, and any relationship to the study author.]

**Frozen arms.** Adopted profile `{arms['adopted']['profile']}` (`{arms['adopted']['sha256'][:12]}`), previous profile `{arms['previous']['profile']}` (`{arms['previous']['sha256'][:12]}`), and grounded Super. Prompt `{d['prompt']['version']}` (`{d['prompt']['sha256'][:12]}`), config `{d['config']['path']}` (`{d['config']['sha256'][:12]}`). `routewise validation seal` hashes these and every module of `src/routewise` except the orchestration module.

**Primary endpoint.** Adopted-profile wrong releases per incoming held-out request: released answers that are wrong, or whose label requires review. Success requires a one-sided 95% Wilson upper bound of at most **{p['max_wrong_release_rate']:.1%}**.{departures}

**Gates.** All must pass for the adopted profile to count as validated on this workload.

| Check | Criterion |
|---|---|
| Primary | Wrong-release rate upper bound ≤ {p['max_wrong_release_rate']:.1%} |
| Requirements | Task accuracy one-sided 95% lower bound ≥ {p['min_task_accuracy']:.0%}; field accuracy ≥ {p['min_field_accuracy']:.0%}; 4 s deadline-miss upper bound ≤ {p['max_deadline_miss_rate']:.0%}; no incomplete-cost request |
| Versus previous profile | Paired CAC difference, 95% lower bound ≥ −{p['cac_margin_vs_previous']:.1%}; paired wrong-release difference, 95% upper bound ≤ +{p['wrong_release_margin_vs_previous']:.1%} |
| Versus grounded Super | Paired cost difference per 1,000 requests, 95% upper bound < 0 |

**Sample size.** Planning values are 80% pilot confidence limits on the unfavourable side; favourable paired differences are halved. One-sample checks use exact binomial power; paired checks the normal approximation. Each n gives at least 90% power.

| Criterion | Pilot | Planning value | n | Power at n = {d['n_required']} |
|---|---|---:|---:|---:|
{chr(10).join(rows)}

- **Required accepted conversations: {d['n_required']}** (binding: {names[d['binding']].lower()}). Joint power if the checks were independent: {d['joint_power_if_independent']:.2f}.
- **Collect {d['n_collect']}**, allowing a {d['planning_exclusion_rate']:.1%} exclusion rate. This means {d['labels_needed']} independent labels, about {d['expected_adjudications']} adjudications, and about ${d['api_cost_estimate_usd']:.2f} of API spend.
- Alternatives considered (threshold → n for that criterion): {alt}.

**Stopping rule.** One analysis, after every collected conversation is labelled, built, sealed and run. No interim looks, no extension and no replacement of excluded conversations. If fewer than {d['n_required']} conversations survive adjudication, the seal refuses and the study is reported as underpowered rather than topped up. Transport failures are outcomes; a resumed run fills only missing outcomes.

**Analysis.** `routewise validation report` on the confirmation run: per-request outcomes over all held-out requests; paired conversation-bootstrap intervals (4,000 resamples) with primary-scenario cluster intervals as a sensitivity check; per-tag tables, wrong fields, review causes and issue-date analyzer coverage, all descriptive.

**Decision.** If every check passes, the adopted profile is validated for this workload. Otherwise it is not validated and the measured results are reported. Neither comparator is validated by this study; switching to one needs its own confirmation.
"""


def _planning_cells(key: str, c: dict[str, Any]) -> tuple[str, str]:
    if key == "task_accuracy":
        return c["pilot"], f"{c['planning_accuracy']:.3f} of {c['planning_answerable_fraction']:.0%} answerable"
    if key.endswith("_vs_previous"):
        return (f"{c['pilot_difference']:+.3f} ({c['pilot_discordant']} discordant)",
                f"{c['planning_difference']:+.3f}, discordance {c['planning_discordance']:.3f}, margin {c['margin']:.3f}")
    if key == "cost_vs_grounded_super":
        return f"{c['pilot_mean_per_1k']:+.4f} $/1k (sd {c['pilot_sd_per_1k']:.4f})", "pilot mean and sd"
    return c["pilot"], f"{c['planning_rate']:.4f}"


def preregister(proposal: Path, dirs: PrivateDirs) -> None:
    design = json.loads((proposal / "design.json").read_text())
    text = (proposal / "part-b.md").read_text()
    if not design["feasible"]:
        raise SafeError("This pilot does not support a confirmation; see the proposal's recommendation")
    if PLACEHOLDER in text:
        raise SafeError(f"Replace every {PLACEHOLDER} ...] item in {proposal / 'part-b.md'} first")
    if DESIGN.exists() or "## Part B" in PROTOCOL.read_text():
        raise SafeError("Part B is already registered; append a dated amendment instead")
    private_mode.publish({DESIGN: json.dumps(design, indent=2, default=str) + "\n",
                          PROTOCOL: PROTOCOL.read_text().rstrip() + "\n\n" + text.strip() + "\n"}, dirs)
    print(f"Wrote {_rel(DESIGN)} and appended Part B to {_rel(PROTOCOL)}. Commit and push both before "
          "drawing any held-out conversation.", flush=True)


# ---------------------------------------------------------------- seal


def seal(dirs: PrivateDirs, name: str) -> Path:
    data = dirs.output / "data" / name
    if (data / "seal.json").exists() or PUBLIC_SEAL.exists():
        raise SafeError("The confirmation is already sealed")
    if _synthetic(data):
        raise SafeError("Synthetic drill data is never sealed")
    if "## Part B" not in PROTOCOL.read_text():
        raise SafeError("Preregister Part B before sealing held-out data")
    _require_committed([PROTOCOL, DESIGN])
    design = json.loads(DESIGN.read_text())
    built = json.loads((data / "MANIFEST.json").read_text())["built_at"]
    if datetime.fromisoformat(built) <= _committed_at(DESIGN):
        raise SafeError("These labels were built before the design was committed; held-out data must come after it")
    records = load_dataset(data)
    if any(r.split != "heldout" for r in records):
        raise SafeError("A held-out dataset must contain only heldout conversations")
    if len(records) < design["n_required"]:
        raise SafeError(f"{len(records)} accepted conversations; the design requires {design['n_required']}. "
                         "Report the study as underpowered or amend the protocol; do not top up from another frame.")
    texts, ids = {label_io.text_sha256(r.input) for r in records}, {r.id for r in records}
    for other in sorted((dirs.output / "data").glob("*/examples.jsonl")):
        if other.parent == data:
            continue
        overlap = [r.id for r in load_dataset(other.parent) if r.id in ids or label_io.text_sha256(r.input) in texts]
        if overlap:
            note = private_mode.private_note(dirs.output, "overlap", "\n".join(overlap) + "\n")
            raise SafeError(f"{len(overlap)} conversation(s) of {other.parent.name} overlap the held-out set; "
                            f"their IDs are in {note}")
    config = load_config(CONFIG)
    load_dotenv(REPO_ROOT / ".env")
    fetch_prices(config, data / "prices.json")
    value = {"study": STUDY, "display_milestone": DISPLAY, "dataset": name, "n": len(records),
             "created_at": datetime.now(UTC).isoformat(), "created_before_heldout_outcomes": True,
             "design_sha256": sha256_file(DESIGN), "protocol_sha256": sha256_file(PROTOCOL),
             "data_sha256": sha256_file(data / "examples.jsonl"), "price_sha256": sha256_file(data / "prices.json"),
             "sources": frozen_sources(), "seed": int(sha256_file(DESIGN)[:8], 16)}
    dump(data / "seal.json", value)
    private_mode.publish({PUBLIC_SEAL: json.dumps(value, indent=2, default=str) + "\n"}, dirs)
    print(f"Sealed {len(records)} held-out conversations. Commit and push {_rel(PUBLIC_SEAL)} (hashes only), then "
          f"run `routewise validation run --name {name} --split heldout`.", flush=True)
    return data / "seal.json"


def check_seal(data: Path) -> dict[str, Any]:
    if not (data / "seal.json").exists():
        raise SafeError("Held-out conversations run only after `routewise validation seal`")
    value = json.loads((data / "seal.json").read_text())
    _require_committed([PROTOCOL, DESIGN, PUBLIC_SEAL])
    if json.loads(PUBLIC_SEAL.read_text()) != value:
        raise SafeError(f"{_rel(PUBLIC_SEAL)} does not match this dataset's seal")
    current = frozen_sources()
    if value["sources"] != current:
        raise SafeError(f"Frozen code, prompts or profiles changed after sealing: "
                         f"{sorted(k for k in value['sources'].keys() | current.keys() if value['sources'].get(k) != current.get(k))}")
    for key, path in (("design_sha256", DESIGN), ("protocol_sha256", PROTOCOL), ("data_sha256", data / "examples.jsonl"),
                      ("price_sha256", data / "prices.json")):
        if value[key] != sha256_file(path):
            raise SafeError(f"{_rel(path)} changed after sealing")
    return value


def export(dirs: PrivateDirs, run_path: Path, args: Any) -> None:
    if args.approve:
        dest = validation_export.approve(dirs, run_path.name, args.approve, args.reviewer,
                                         args.export_dir or validation_export.PUBLIC_DIR)
        print(f"Published the reviewed aggregate to {_rel(dest)}. Nothing was committed; commit it only if it "
              "should be public.", flush=True)
        return
    out, digest = validation_export.stage(dirs, run_path, report(run_path), args.min_cell or validation_export.MIN_CELL)
    print(f"\nStaged {out / 'aggregate.json'} (SHA-256 {digest}).\nCheck every value in {out / 'REVIEW.md'}; to "
          f"publish exactly that file:\n  routewise validation export --run {run_path.name} --approve {digest} "
          '--reviewer "<role or pseudonym>"', flush=True)


def leak_scan(dirs: PrivateDirs, repo: Path = REPO_ROOT) -> None:
    findings, stats = private_mode.leak_scan(dirs, repo)
    if not stats["values"]:
        raise SafeError("There is no private data to scan for yet; import a dataset first")
    print(f"Checked {stats.get('files', 0)} files and {stats.get('commits', 0)} commit(s) since the private data "
          f"appeared, for {stats['values']} private values", flush=True)
    if stats["unreadable_private_files"]:
        print(f"Warning: {stats['unreadable_private_files']} private file(s) could not be read, so their values "
              "were not looked for", flush=True)
    if not findings:
        print("No private value was found in the repository.", flush=True)
        return
    note = private_mode.private_note(dirs.output, "leak-scan", "".join(
        f"{f.location}\n" + "".join(f"  {d}\n" for d in f.details) for f in findings))
    for f in findings:
        where = "(a path that itself contains private data)" if f.location_is_private else f.location
        print(f"  {where}: " + ", ".join(f"{n} {c}" for c, n in f.categories.items()), flush=True)
    raise SafeError(f"{len(findings)} location(s) hold values from the private data; details in {note}")


def _inside(output: Path, value: Path | str, folder: str) -> Path:
    """A run or proposal given by name (in <output>/<folder>) or by a path inside the output directory."""
    given = Path(value)
    path = (output / folder / given if len(given.parts) == 1 else given.expanduser()).resolve()
    if not path.is_relative_to(output):
        raise SafeError(f"{path} is not inside the output directory {output}")
    if not path.is_dir():
        raise SafeError(f"{path} does not exist")
    return path


def main(args: Any) -> None:
    stage = args.stage
    if stage == "drill":
        from routewise import privacy_drill
        privacy_drill.main(args.output)
        return
    dirs = private_mode.resolve_dirs(args.input, args.output, need_input=stage == "import")
    with private_mode.session(dirs.output):
        _dispatch(stage, dirs, args)


def _dispatch(stage: str, dirs: PrivateDirs, args: Any) -> None:
    output = dirs.output
    if stage == "import":
        if not args.name:
            raise SafeError("import requires --name")
        import_labels(dirs, args.name)
    elif stage == "run":
        if not args.name and not args.run:
            raise SafeError("run requires --name (or --run to resume)")
        resume = _inside(output, args.run, "runs") if args.run else None
        name = args.name or json.loads((resume / "manifest.json").read_text())["dataset"]["name"]
        run(output, name, args.split, smoke=args.smoke, resume=resume, budget=args.budget)
    elif stage in ("report", "propose", "export"):
        if not args.run:
            raise SafeError(f"{stage} requires --run")
        run_path = _inside(output, args.run, "runs")
        if stage == "report":
            report(run_path)
        elif stage == "export":
            export(dirs, run_path, args)
        else:
            overrides = {k: v for k, v in (("max_wrong_release_rate", args.max_wrong_release),
                                           ("cac_margin_vs_previous", args.cac_margin),
                                           ("wrong_release_margin_vs_previous", args.wrong_release_margin))
                         if v is not None}
            propose(run_path, overrides)
    elif stage == "preregister":
        if not args.proposal:
            raise SafeError("preregister requires --proposal")
        preregister(_inside(output, args.proposal, "proposals"), dirs)
    elif stage == "seal":
        if not args.name:
            raise SafeError("seal requires --name")
        seal(dirs, args.name)
    elif stage == "leak-scan":
        leak_scan(dirs)
    else:
        raise SafeError(f"unknown stage {stage}")
