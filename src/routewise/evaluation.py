"""Held-out comparison: always-smallest vs always-strongest vs RouteWise, run live."""

from __future__ import annotations

import csv
import json
import math
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from routewise.benchmark import run_parallel
from routewise.client import TokenFactoryClient
from routewise.config import Config
from routewise.cost import Price
from routewise.evaluate import score
from routewise.metrics import Outcome, bootstrap_ci, summarize
from routewise.policy import RouteOption, policy_options, violations
from routewise.prompts import PromptTemplate
from routewise.router import Router, Trace
from routewise.runlog import RunDir
from routewise.schema import Example

STRATEGIES = ("always_smallest", "always_strongest", "routewise")


def build_routers(
    client: TokenFactoryClient,
    config: Config,
    policy: dict[str, Any],
    prices: dict[str, Price],
    prompts: dict[str, PromptTemplate],
) -> dict[str, Router]:
    tasks = list(policy["tasks"])
    smallest = config.candidate(config.baselines["smallest"]).label
    strongest = config.candidate(config.baselines["strongest"]).label
    fixed = {
        "always_smallest": {t: RouteOption(smallest) for t in tasks},
        "always_strongest": {t: RouteOption(strongest) for t in tasks},
    }
    routers = {
        name: Router(client, config, prices, prompts, options, strategy=name) for name, options in fixed.items()
    }
    routers["routewise"] = Router(
        client, config, prices, prompts, policy_options(policy), strategy="routewise", policy=policy
    )
    return routers


def run_evaluation(
    routers: dict[str, Router],
    examples: Sequence[Example],
    run: RunDir,
    *,
    concurrency: int = 4,
    rpm: float = 50,
    progress: Callable[[str], None] = print,
) -> list[Trace]:
    lock = threading.Lock()
    total = len(examples) * len(routers)
    done = [0]

    def job(router: Router, ex: Example) -> Callable[[], Trace]:
        def run_one() -> Trace:
            trace = router.route(ex.task, ex.input, example_id=ex.id, expected=ex.expected)
            with lock:
                run.append("traces.jsonl", trace)
                done[0] += 1
                if done[0] % 10 == 0 or done[0] == total:
                    progress(f"  {done[0]}/{total} routed requests")
            return trace

        return run_one

    jobs = [job(routers[name], ex) for ex in examples for name in routers]
    return run_parallel(jobs, concurrency=concurrency, rpm=rpm)  # type: ignore[return-value]


def trace_outcome(trace: Trace) -> Outcome:
    score = trace.score or {"task_correct": False, "field_accuracy": 0.0}
    return Outcome(
        example_id=trace.example_id or trace.request_id,
        task=trace.task,
        correct=bool(score["task_correct"]),
        field_accuracy=float(score["field_accuracy"]),
        cost_usd=trace.cost_usd,
        latency_ms=trace.latency_ms,
        escalated=trace.escalated,
        cost_complete=trace.cost_complete,
    )


def validator_analysis(traces: Sequence[Trace], examples: dict[str, Example]) -> dict[str, Any]:
    """Treat the validator as a classifier of first-call errors (positive = 'escalate')."""
    tp = fp = fn = tn = 0
    corrected = 0
    for t in traces:
        ex = examples[t.example_id]
        first_correct = score(ex.task, ex.expected, t.calls[0].parsed).task_correct
        flagged = not t.validations[0]["passed"]
        if flagged and not first_correct:
            tp += 1
            if t.escalated and t.score and t.score["task_correct"]:
                corrected += 1
        elif flagged:
            fp += 1
        elif not first_correct:
            fn += 1
        else:
            tn += 1
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "error_recall": tp / (tp + fn) if tp + fn else None,
        "escalation_precision": tp / (tp + fp) if tp + fp else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "correction_rate": corrected / tp if tp else None,
    }


def summarize_evaluation(
    traces: Sequence[Trace], examples: Sequence[Example], config: Config, policy: dict[str, Any]
) -> dict[str, Any]:
    by_id = {e.id: e for e in examples}
    tasks = list(policy["tasks"])
    by_strategy: dict[str, list[Trace]] = {s: [] for s in STRATEGIES}
    for t in traces:
        by_strategy[t.strategy].append(t)

    results: dict[str, Any] = {}
    for strategy, ts in by_strategy.items():
        entry: dict[str, Any] = {"overall": summarize([trace_outcome(t) for t in ts])}
        for task in tasks:
            task_ts = [t for t in ts if t.task == task]
            m = summarize([trace_outcome(t) for t in task_ts])
            m["threshold_violations"] = violations(m, config.thresholds[task]) if task_ts else ["no data"]
            m["meets_thresholds"] = not m["threshold_violations"]
            m["task_accuracy_ci95"] = bootstrap_ci([float(trace_outcome(t).correct) for t in task_ts])
            entry[task] = m
        entry["validator"] = validator_analysis(ts, by_id)
        results[strategy] = entry

    # Accounting check: reported totals must equal the sum of per-trace costs.
    for strategy, ts in by_strategy.items():
        traced = sum(t.cost_usd for t in ts)
        reported = results[strategy]["overall"].get("cost_usd_total", 0.0)
        if not math.isclose(traced, reported, rel_tol=1e-9, abs_tol=1e-12):
            raise AssertionError(f"{strategy}: summary cost {reported} != traced cost {traced}")

    comparison = _paired_comparison(by_strategy["routewise"], by_strategy["always_strongest"])
    return {
        "policy_id": policy["policy_id"],
        "n_examples": len(examples),
        "strategies": results,
        "routewise_vs_strongest": comparison,
        "verdict": _verdict(results, comparison, tasks),
    }


def _paired_comparison(rw: Sequence[Trace], strong: Sequence[Trace]) -> dict[str, Any]:
    s = {t.example_id: t for t in strong}
    pairs = [(t, s[t.example_id]) for t in rw if t.example_id in s]
    if not pairs:
        return {}
    cost_rw = sum(a.cost_usd for a, _ in pairs)
    cost_st = sum(b.cost_usd for _, b in pairs)
    cost_diffs = [a.cost_usd - b.cost_usd for a, b in pairs]
    acc_diffs = [float(bool(a.score and a.score["task_correct"])) - float(bool(b.score and b.score["task_correct"])) for a, b in pairs]
    return {
        "n_pairs": len(pairs),
        "cost_savings_fraction": 1 - cost_rw / cost_st if cost_st else None,
        "cost_diff_per_1k_tasks": sum(cost_diffs) / len(pairs) * 1000,
        "cost_diff_per_1k_ci95": _scale(bootstrap_ci(cost_diffs), 1000),
        "task_accuracy_diff": sum(acc_diffs) / len(pairs),
        "task_accuracy_diff_ci95": bootstrap_ci(acc_diffs),
    }


def _verdict(results: dict[str, Any], comparison: dict[str, Any], tasks: list[str]) -> dict[str, Any]:
    rw = results["routewise"]
    unmet = {t: rw[t]["threshold_violations"] for t in tasks if not rw[t]["meets_thresholds"]}
    savings = comparison.get("cost_savings_fraction")
    cheaper = savings is not None and savings > 0
    smallest_ok = all(results["always_smallest"][t]["meets_thresholds"] for t in tasks)
    supported = not unmet and cheaper
    lines = []
    if unmet:
        lines.append("RouteWise did NOT meet the declared thresholds on held-out data: "
                     + "; ".join(f"{t}: {', '.join(v)}" for t, v in unmet.items()))
    else:
        lines.append("RouteWise met every declared threshold on held-out data.")
    if savings is not None:
        lines.append(f"Its total cost was {abs(savings) * 100:.1f}% {'lower' if cheaper else 'higher'} than always using the strongest model.")
    if smallest_ok:
        lines.append("Note: always-smallest also met the thresholds, so routing added cost without a quality need.")
    return {"hypothesis_supported": supported, "summary": " ".join(lines)}


def _scale(ci: tuple[float, float] | None, k: float) -> tuple[float, float] | None:
    return (ci[0] * k, ci[1] * k) if ci else None


def write_summary(summary: dict[str, Any], run: RunDir) -> None:
    (run.path / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    fields = ["strategy", "scope", "n", "task_accuracy", "field_accuracy", "cost_per_1k_tasks", "cost_usd_total",
              "p50_latency_ms", "p95_latency_ms", "escalation_rate", "meets_thresholds"]
    with Path(run.path / "summary.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for strategy, entry in summary["strategies"].items():
            for scope, m in entry.items():
                if scope == "validator":
                    continue
                writer.writerow({"strategy": strategy, "scope": scope, **m})
