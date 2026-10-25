"""Replay-based policy optimizer.

Because the benchmark runs every candidate on every tuning example, any routing option
(primary model, optional validation-triggered escalation) can be simulated exactly from the
recorded calls, with no new API spend. The optimizer picks, per task, the cheapest option that
meets every declared threshold, and says so plainly when none does.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from routewise.config import REPO_ROOT, Config, Thresholds
from routewise.metrics import Outcome, percentile, summarize
from routewise.runlog import new_experiment_id
from routewise.schema import CallRecord, Example
from routewise.validate import validate

POLICIES_DIR = REPO_ROOT / "policies"


@dataclass(frozen=True)
class RouteOption:
    primary: str
    escalate_to: str | None = None
    hedge_after_ms: float | None = None  # also start the escalation call if the primary is this slow

    @property
    def label(self) -> str:
        if self.escalate_to is None:
            return self.primary
        base = f"{self.primary} -> {self.escalate_to} on fail"
        return base if self.hedge_after_ms is None else f"{base}, hedge @ {self.hedge_after_ms:.0f} ms"

    def without_hedge(self) -> RouteOption:
        return replace(self, hedge_after_ms=None)


def record_validation(record: CallRecord, example: Example):
    return validate(example.task, example.input, record.parsed_output())


def simulate(option: RouteOption, examples: Sequence[Example], index: dict[tuple[str, str], CallRecord]) -> list[Outcome]:
    """Replay an option over recorded calls, mirroring the router's control flow and billing."""
    return [_simulate_one(option, ex, index) for ex in examples]


def _simulate_one(option: RouteOption, ex: Example, index: dict[tuple[str, str], CallRecord]) -> Outcome:
    p = index[(option.primary, ex.id)]
    if option.escalate_to is None:
        return _outcome(ex, final=p, calls=[p], latency=_lat(p))
    e = index[(option.escalate_to, ex.id)]
    p_ok = record_validation(p, ex).passed
    t = option.hedge_after_ms
    if t is None or _lat(p) <= t:
        if p_ok:
            return _outcome(ex, final=p, calls=[p], latency=_lat(p))
        return _outcome(ex, final=e, calls=[p, e], latency=_lat(p) + _lat(e), escalated=True)
    # Hedged: the escalation call starts at t; the first valid answer wins; both are billed.
    e_done = t + _lat(e)
    if _lat(p) <= e_done:
        if p_ok:
            return _outcome(ex, final=p, calls=[p, e], latency=_lat(p), hedged=True)
        return _outcome(ex, final=e, calls=[p, e], latency=e_done, escalated=True, hedged=True)
    if record_validation(e, ex).passed:
        return _outcome(ex, final=e, calls=[p, e], latency=e_done, hedged=True)
    if p_ok:
        return _outcome(ex, final=p, calls=[p, e], latency=_lat(p), hedged=True)
    return _outcome(ex, final=e, calls=[p, e], latency=_lat(p), escalated=True, hedged=True)


def _lat(record: CallRecord) -> float:
    return record.latency_ms if record.latency_ms is not None else record.total_ms


def _outcome(ex: Example, *, final: CallRecord, calls: list[CallRecord], latency: float,
             escalated: bool = False, hedged: bool = False) -> Outcome:
    score = final.score or {"task_correct": False, "field_accuracy": 0.0}
    return Outcome(
        example_id=ex.id,
        task=ex.task,
        correct=bool(score["task_correct"]),
        field_accuracy=float(score["field_accuracy"]),
        cost_usd=sum(c.cost_usd or 0.0 for c in calls),
        latency_ms=latency,
        escalated=escalated,
        hedged=hedged,
        cost_complete=all(c.cost_usd is not None for c in calls),
    )


def violations(metrics: dict[str, Any], t: Thresholds) -> list[str]:
    out = []
    if metrics["task_accuracy"] < t.min_task_accuracy:
        out.append(f"task accuracy {metrics['task_accuracy']:.3f} < {t.min_task_accuracy}")
    if t.min_field_accuracy is not None and metrics["field_accuracy"] < t.min_field_accuracy:
        out.append(f"field accuracy {metrics['field_accuracy']:.3f} < {t.min_field_accuracy}")
    p95 = metrics["p95_latency_ms"]
    if t.max_p95_latency_ms is not None and (p95 is None or p95 > t.max_p95_latency_ms):
        out.append(f"p95 latency {p95 if p95 is None else round(p95)} ms > {t.max_p95_latency_ms} ms")
    if metrics["incomplete_cost_count"]:
        out.append(f"{metrics['incomplete_cost_count']} tasks without usage data (cost unknown)")
    return out


def candidate_options(ladder: Sequence[str], hedge_after_ms: dict[str, float] | None = None) -> list[RouteOption]:
    """Every single model, plus every model with escalation to any stronger model.

    With `hedge_after_ms` (per primary), escalation options also hedge slow primaries."""
    hedge = hedge_after_ms or {}
    options = [RouteOption(p) for p in ladder]
    options += [RouteOption(p, e, hedge.get(p)) for i, p in enumerate(ladder) for e in ladder[i + 1 :]]
    return options


def optimize(
    config: Config,
    records: Sequence[CallRecord],
    examples: Sequence[Example],
    *,
    source_run: str,
    dataset_sha256: str,
) -> dict[str, Any]:
    index = {(r.candidate, r.example_id): r for r in records}
    ladder = [c.label for c in config.candidates if any(r.candidate == c.label for r in records)]
    if not ladder:
        raise ValueError("no benchmark records match the configured candidates")
    ids_with_all = {ex.id for ex in examples if all((c, ex.id) in index for c in ladder)}

    tasks: dict[str, Any] = {}
    for task, thresholds in config.thresholds.items():
        task_examples = [ex for ex in examples if ex.task == task and ex.id in ids_with_all]
        if not task_examples:
            continue
        hedge_after_ms = None
        if config.hedge_after_percentile is not None:
            hedge_after_ms = {
                c: round(percentile([_lat(index[(c, ex.id)]) for ex in task_examples], config.hedge_after_percentile))
                for c in ladder
            }
        rows = []
        for option in candidate_options(ladder, hedge_after_ms):
            m = summarize(simulate(option, task_examples, index))
            v = violations(m, thresholds)
            rows.append({"option": asdict(option), "label": option.label, "metrics": m, "qualifies": not v, "violations": v})
        qualifying = sorted(
            (r for r in rows if r["qualifies"]),
            key=lambda r: (r["metrics"]["cost_per_1k_tasks"], -r["metrics"]["task_accuracy"]),
        )
        if qualifying:
            best = qualifying[0]
            m = best["metrics"]
            status = "ok"
            reason = (
                f"cheapest of {len(qualifying)} qualifying options on {m['n']} tuning examples: "
                f"task acc {m['task_accuracy']:.3f}, field acc {m['field_accuracy']:.3f}, "
                f"p95 {m['p95_latency_ms']:.0f} ms, ${m['cost_per_1k_tasks']:.4f} per 1k tasks"
            )
        else:
            best = max(rows, key=lambda r: (r["metrics"]["task_accuracy"], -r["metrics"]["cost_per_1k_tasks"]))
            status = "no_qualifying_policy"
            reason = (
                "no option meets the declared thresholds; falling back to the most accurate option "
                f"({best['label']}) and flagging every routed request. Gaps: " + "; ".join(best["violations"])
            )
        tasks[task] = {
            "status": status,
            "choice": best["option"],
            "choice_label": best["label"],
            "reason": reason,
            "thresholds": asdict(thresholds),
            "options": rows,
        }

    return {
        "policy_id": new_experiment_id("policy"),
        "created_at": datetime.now(UTC).isoformat(),
        "source_run": source_run,
        "dataset_sha256": dataset_sha256,
        "examples_used": len(ids_with_all),
        "ladder": ladder,
        "hedging": {"after_primary_percentile": config.hedge_after_percentile},
        "tasks": tasks,
    }


def save_policy(policy: dict[str, Any], directory: Path | None = None) -> Path:
    directory = directory or POLICIES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{policy['policy_id']}.json"
    path.write_text(json.dumps(policy, indent=2) + "\n")
    return path


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def policy_options(policy: dict[str, Any]) -> dict[str, RouteOption]:
    return {task: RouteOption(**entry["choice"]) for task, entry in policy["tasks"].items()}
