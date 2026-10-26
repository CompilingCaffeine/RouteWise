"""Reviewed aggregate export: the only validation output meant to leave the private output directory.

`stage` builds exports/<run>/aggregate.json from a run's report under an allowlist of keys and value
types: counts, rates, numbers, booleans, hashes, dates and fixed vocabularies. Nothing in it is free
text or describes a single conversation. Per-tag cells below a minimum size, and tags outside the
labeling guide's shared list, are withheld. REVIEW.md shows every value that would be published.
A person approves that exact file by its SHA-256; `approve` re-checks it against the allowlist and
the private data, then writes it with the reviewer's stamp to results/M8-validation/.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from routewise.config import REPO_ROOT
from routewise.evaluate import FIELDS
from routewise.labels import REVIEW_REASONS, SPLITS, TAG_VOCABULARY
from routewise.private_mode import PrivateDirs, SafeError, needles_from, private_note, publish, refuse_private_root
from routewise.recovery_metrics import METRICS
from routewise.runlog import sha256_file

FORMAT = "routewise-validation-aggregate/1"
PUBLIC_DIR = REPO_ROOT / "results/M8-validation"
MIN_CELL, MIN_CELL_FLOOR = 10, 5
ARMS = ("adopted", "previous", "grounded_super")
COMPARISONS = ("adopted - previous", "adopted - grounded_super")
REVIEWER = r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}"
# review_cause() in workload_validation: the recovery actions and the deployed validator's rule names
ISSUE_DATE_CAUSES = ("ambiguous issue date", "unrecognized issue-date language",
                     "conflicting issue dates without a correction", "fallback needs review", "fallback unusable",
                     "fallback disagrees with source")
VALIDATION_RULES = ("schema", "amount_in_source", "amount_refund_context", "contact_date_valid", "currency_consistent",
                    "dates_in_source", "order_id_in_source", "refund_date_matches_statement")
ANALYZER_STATUSES = ("resolved", "none", "review (known ambiguity)", "review (unrecognized wording)")
LABEL_KINDS = ("review required", "issue date", "no issue date")
DECISION_CHECKS = ("primary_wrong_release", "requirements", "vs_previous", "cost_vs_grounded_super")


# ---------------------------------------------------------------- the allowlist


def _count(v: Any) -> bool:
    return type(v) is int and v >= 0


def _number(v: Any) -> bool:
    return v is None or (type(v) in (int, float) and math.isfinite(v))


def _rate(v: Any) -> bool:
    return v is None or (_number(v) and 0 <= v <= 1)


def _flag(v: Any) -> bool:
    return type(v) is bool


def _hash(v: Any) -> bool:
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", v) is not None


def _date(v: Any) -> bool:
    return isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) is not None


def _interval(v: Any) -> bool:
    return v is None or (isinstance(v, list) and len(v) == 2 and all(x is not None and _number(x) for x in v))


def _one_of(*values: str) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and v in values


def _matches(pattern: str) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, str) and re.fullmatch(pattern, v) is not None


def _maybe(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda v: v is None or check(v)


def _review_cause(key: Any) -> bool:
    kind, _, rest = str(key).partition(": ")
    return (kind == "issue date" and rest in ISSUE_DATE_CAUSES) or (
        kind == "validation" and (rest == "no answer" or set(rest.split(", ")) <= set(VALIDATION_RULES)))


@dataclass(frozen=True)
class Opt:
    spec: Any


@dataclass(frozen=True)
class Each:
    key: Callable[[Any], bool]
    value: Any


@dataclass(frozen=True)
class Rows:
    item: Any


_ARM_METRICS = {
    "n": _count, "n_review_required": _count, "correct_autonomous": _count, "cac_rate": _rate, "review_rate": _rate,
    "necessary_review_rate": _rate, "avoidable_review_rate": _rate, "undetected_errors": _count,
    "undetected_rate": _rate, "undetected_upper95": _rate, "released_accuracy": _rate, "task_accuracy": _rate,
    "accuracy_lower95": _rate, "field_accuracy": _rate, "escalation_rate": _rate, "fallback_rate": _rate,
    "cost_usd_total": _number, "cost_per_1k_tasks": _number, "incomplete_cost_count": _count,
    "p50_latency_ms": _number, "p95_latency_ms": _number, "deadline_misses": _count, "deadline_miss_rate": _rate,
    "deadline_miss_upper95": _rate}
_AGREEMENT = {
    "n_conversations": _count,
    "review_required": {"agreement": _rate, "kappa": _number, "both": _count, "one": _count},
    "fields_when_both_answerable": Each(_one_of(*FIELDS["extraction"]),
                                        {"n": _count, "agreement": _rate, "kappa": Opt(_number)}),
    "metric_disagreements": _count, "metric_agreement_rate": _rate,
    "disagreements_by_field": Each(_one_of(*FIELDS["extraction"], "review_required"), _count)}
SPEC: dict[str, Any] = {
    "format": _one_of(FORMAT), "study": _one_of("m8"), "display_milestone": _one_of("M7"),
    "kind": _one_of("pilot", "smoke", "confirmation"), "split": _one_of(*SPLITS),
    "run": _matches(r"val(?:pilot|smoke|confirm)-\d{8}T\d{6}Z-[0-9a-f]{6}"), "run_date": _date,
    "synthetic": _flag, "n_conversations": _count, "complete": _flag,
    "provenance": {"dataset_sha256": _hash, "labels_revised_after_run": _flag, "code_commit": _maybe(_hash),
                   "code_dirty": _maybe(_flag), "adopted_profile_sha256": _hash, "previous_profile_sha256": _hash,
                   "prompt_sha256": _hash, "config_sha256": _hash, "prices_sha256": _hash},
    "labels": {"n_labelled": _count, "n_excluded": _count, "n_adjudicated": _count, "n_review_required": _count,
               "review_reasons": Each(_one_of(*REVIEW_REASONS), _count),
               "primary_scenarios": Each(_one_of(*TAG_VOCABULARY), _count), "agreement": _AGREEMENT},
    "metrics": Each(_one_of(*ARMS), {k: check if k == "n" else Opt(check) for k, check in _ARM_METRICS.items()}),
    "paired": Each(_one_of(*COMPARISONS), Each(_one_of(*METRICS), {
        "n_pairs": _count, "diff": Opt(_number), "ci95": Opt(_interval), "family_cluster_ci95": Opt(_interval),
        "a_only": Opt(_count), "b_only": Opt(_count)})),
    "by_tag": Rows({"tag": _one_of(*TAG_VOCABULARY), "arm": _one_of(*ARMS), "n": _count, "cac_rate": _rate,
                    "review_rate": _rate, "wrong_releases": _count, "task_accuracy": _rate}),
    "withheld": {"min_cell": _count, "tag_cells_below_min_cell": _count, "tags_outside_vocabulary": _count},
    "wrong_fields": Each(_one_of(*ARMS), Each(_one_of(*FIELDS["extraction"]), _count)),
    "review_causes": Each(_one_of(*ARMS), Each(_review_cause, _count)),
    "issue_date_coverage": {
        "table": Rows({"analyzer": _one_of(*ANALYZER_STATUSES), "label": _one_of(*LABEL_KINDS), "n": _count}),
        "agrees_with_label": _count, "disagrees_with_label": _count, "unrecognized_wording": _count},
    "cost": {"total_api_cost_usd": _number, "incomplete_cost_requests": _count},
    "decision": Opt({"checks": Each(_one_of(*DECISION_CHECKS), _flag), "validated": _flag}),
    "review": Opt({"reviewer": _matches(REVIEWER), "approved_on": _date, "reviewed_sha256": _hash}),
}


def problems(value: Any, spec: Any = SPEC, path: str = "aggregate") -> list[str]:
    """Departures from the allowlist. Messages name only allowed keys, never a value or an unexpected key."""
    if isinstance(spec, Opt):
        spec = spec.spec
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return [f"{path}: expected an object"]
        out = [f"{path}: unexpected field"] * sum(k not in spec for k in value)
        for key, sub in spec.items():
            if key in value:
                out += problems(value[key], sub, f"{path}.{key}")
            elif not isinstance(sub, Opt):
                out.append(f"{path}.{key}: missing")
        return out
    if isinstance(spec, Each):
        if not isinstance(value, dict):
            return [f"{path}: expected an object"]
        out = [f"{path}: key outside the allowed vocabulary"] * sum(not spec.key(k) for k in value)
        return out + [p for k, v in value.items() if spec.key(k) for p in problems(v, spec.value, f"{path}.{k}")]
    if isinstance(spec, Rows):
        if not isinstance(value, list):
            return [f"{path}: expected a list"]
        return [p for i, v in enumerate(value) for p in problems(v, spec.item, f"{path}[{i}]")]
    return [] if spec(value) else [f"{path}: value not allowed"]


# ---------------------------------------------------------------- staging and approval


def build(summary: dict[str, Any], run_manifest: dict[str, Any], dataset_manifest: dict[str, Any],
          min_cell: int = MIN_CELL) -> dict[str, Any]:
    vocabulary = set(TAG_VOCABULARY)
    rows = [r for r in summary["by_tag"] if r["tag"] in vocabulary]
    scenarios = {t: n for t, n in dataset_manifest["primary_scenarios"].items() if t in vocabulary}
    kept_rows = [r for r in rows if r["n"] >= min_cell]
    kept_scenarios = {t: n for t, n in scenarios.items() if n >= min_cell}
    arms, git = run_manifest["arms"], run_manifest.get("git") or {}
    aggregate = {
        "format": FORMAT, "study": summary["study"], "display_milestone": summary["display_milestone"],
        "kind": summary["kind"], "split": summary["split"], "run": summary["run"],
        "run_date": run_manifest["created_at"][:10], "synthetic": bool(dataset_manifest.get("synthetic")),
        "n_conversations": summary["n"], "complete": summary["complete"],
        "provenance": {
            "dataset_sha256": summary["labels"]["dataset_sha256_at_run"],
            "labels_revised_after_run": summary["labels"]["revised_after_run"],
            "code_commit": git.get("sha"), "code_dirty": git.get("dirty"),
            "adopted_profile_sha256": arms["adopted"]["sha256"], "previous_profile_sha256": arms["previous"]["sha256"],
            "prompt_sha256": run_manifest["prompt"]["sha256"], "config_sha256": run_manifest["config"]["sha256"],
            "prices_sha256": run_manifest["prices"]["sha256"]},
        "labels": {"n_labelled": dataset_manifest["n_labelled"], "n_excluded": dataset_manifest["n_excluded"],
                   "n_adjudicated": dataset_manifest["n_adjudicated"],
                   "n_review_required": dataset_manifest["review_required"],
                   "review_reasons": dataset_manifest["review_reasons"], "primary_scenarios": kept_scenarios,
                   "agreement": dataset_manifest["agreement"]},
        "metrics": summary["metrics"], "paired": summary["paired"], "by_tag": kept_rows,
        "withheld": {"min_cell": min_cell,
                     "tag_cells_below_min_cell": len(rows) - len(kept_rows) + len(scenarios) - len(kept_scenarios),
                     "tags_outside_vocabulary": sum(t not in vocabulary for t in dataset_manifest["scenario_tags"])},
        "wrong_fields": summary["wrong_fields"], "review_causes": summary["review_causes"],
        "issue_date_coverage": summary["issue_date_coverage"],
        "cost": {"total_api_cost_usd": summary["total_api_cost_usd"],
                 "incomplete_cost_requests": summary["incomplete_cost_requests"]},
    }
    if "decision" in summary:
        aggregate["decision"] = summary["decision"]
    return json.loads(json.dumps(aggregate))  # tuples become lists, exactly as published


def _refuse(dirs: PrivateDirs, value: dict[str, Any], what: str) -> None:
    found = problems(value)
    if found:
        note = private_note(dirs.output, "export-check", "\n".join(found) + "\n")
        raise SafeError(f"{what} breaks the export allowlist in {len(found)} place(s); nothing was exported. "
                        f"Details: {note}")
    hits = needles_from(dirs).find(json.dumps(value))
    if hits:
        note = private_note(dirs.output, "export-check", "".join(f"{c}: {w}\n" for c, w in hits))
        raise SafeError(f"{what} contains {len(hits)} value(s) found in the private data; nothing was exported. "
                        f"Details: {note}")


def stage(dirs: PrivateDirs, run_path: Path, summary: dict[str, Any], min_cell: int = MIN_CELL) -> tuple[Path, str]:
    if min_cell < MIN_CELL_FLOOR:
        raise SafeError(f"--min-cell must be at least {MIN_CELL_FLOOR}")
    run_manifest = json.loads((run_path / "manifest.json").read_text())
    dataset = dirs.output / "data" / run_manifest["dataset"]["name"] / "MANIFEST.json"
    aggregate = build(summary, run_manifest, json.loads(dataset.read_text()), min_cell)
    _refuse(dirs, aggregate, "The aggregate")
    out = dirs.output / "exports" / run_path.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "aggregate.json").write_text(json.dumps(aggregate, indent=2) + "\n")
    digest = sha256_file(out / "aggregate.json")
    (out / "REVIEW.md").write_text(review_sheet(aggregate, digest))
    return out, digest


def approve(dirs: PrivateDirs, run: str, digest: str, reviewer: str | None, dest_dir: Path = PUBLIC_DIR) -> Path:
    staged = dirs.output / "exports" / run / "aggregate.json"
    if not staged.exists():
        raise SafeError(f"Nothing is staged for {run}; run `routewise validation export --run {run}` and review it")
    if sha256_file(staged) != (digest or "").lower():
        raise SafeError(f"{staged} does not have the approved SHA-256; review the current REVIEW.md and approve its hash")
    if not re.fullmatch(REVIEWER, reviewer or ""):
        raise SafeError("--reviewer must be a role or pseudonym: 1-40 letters, digits, spaces, '.', '_' or '-'")
    value = json.loads(staged.read_text())
    _refuse(dirs, value, "The staged aggregate")
    dest_dir = dest_dir.expanduser().resolve()
    refuse_private_root(dest_dir)
    if value["synthetic"] and dest_dir.is_relative_to(REPO_ROOT):
        raise SafeError("Synthetic drill results are never published to the repository")
    dest = dest_dir / f"{run}.json"
    if dest.exists():
        if json.loads(dest.read_text()).get("review", {}).get("reviewed_sha256") == digest:
            return dest
        raise SafeError(f"{dest} already holds a different export; remove it deliberately before replacing it")
    value["review"] = {"reviewer": reviewer, "approved_on": datetime.now(UTC).date().isoformat(),
                       "reviewed_sha256": digest}
    if not dest_dir.exists():
        dest_dir.mkdir(parents=True)
        dest_dir.chmod(0o755)
    publish({dest: json.dumps(value, indent=2) + "\n"}, dirs)
    return dest


# ---------------------------------------------------------------- the review sheet


def _flatten(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict) and value:
        for key, v in value.items():
            yield from _flatten(v, f"{path}.{key}" if path else key)
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        for i, v in enumerate(value):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        yield path, value


def review_sheet(aggregate: dict[str, Any], digest: str) -> str:
    w, run = aggregate["withheld"], aggregate["run"]
    rows = "\n".join(f"| `{path}` | {json.dumps(v)} |" for path, v in _flatten(aggregate))
    return f"""# Export review: {run}

`routewise validation export --approve` publishes exactly the values below, from `aggregate.json` (SHA-256 `{digest}`), plus your reviewer stamp. Nothing else leaves the private output directory.

**Never exported:** conversation text; conversation, order and customer identifiers; labels, evidence and notes; model outputs and traces; per-conversation diagnostics; labeler and adjudicator IDs; file names; personal-data warnings.

**Withheld from this export:** {w['tag_cells_below_min_cell']} per-tag cell(s) with fewer than {w['min_cell']} conversations, and {w['tags_outside_vocabulary']} scenario tag(s) outside the labeling guide's shared list. They remain in the private `summary.json`.
{"" if not aggregate["synthetic"] else chr(10) + "**Synthetic drill data.** This export can never be published to the repository." + chr(10)}
Approve only if every value may be public:

```bash
routewise validation export --run {run} --approve {digest} --reviewer "<role or pseudonym>"
```

| Value | |
|---|---:|
{rows}
"""
