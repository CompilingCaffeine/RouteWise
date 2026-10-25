"""Aggregation shared by the benchmark, policy optimizer, evaluation and dashboard."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from random import Random

from routewise.cost import cost_per_1k_tasks


@dataclass(frozen=True)
class Outcome:
    """The final result of one task under one strategy (possibly several model calls)."""

    example_id: str
    task: str
    correct: bool
    field_accuracy: float
    cost_usd: float
    latency_ms: float | None
    escalated: bool = False
    hedged: bool = False
    cost_complete: bool = True  # False if any call lacked usage data


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (numpy's default), q in [0, 100]."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    pos = (len(data) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def summarize(outcomes: Sequence[Outcome]) -> dict[str, float | int | None]:
    n = len(outcomes)
    if n == 0:
        return {"n": 0}
    total_cost = sum(o.cost_usd for o in outcomes)
    latencies = [o.latency_ms for o in outcomes if o.latency_ms is not None]
    return {
        "n": n,
        "task_accuracy": sum(o.correct for o in outcomes) / n,
        "field_accuracy": sum(o.field_accuracy for o in outcomes) / n,
        "cost_usd_total": total_cost,
        "cost_per_1k_tasks": cost_per_1k_tasks(total_cost, n),
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "escalation_rate": sum(o.escalated for o in outcomes) / n,
        "hedge_rate": sum(o.hedged for o in outcomes) / n,
        "incomplete_cost_count": sum(not o.cost_complete for o in outcomes),
    }


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs),
    n_resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float] | None:
    if not values:
        return None
    rng = Random(seed)
    k = len(values)
    stats = sorted(stat([values[rng.randrange(k)] for _ in range(k)]) for _ in range(n_resamples))
    return stats[int(alpha / 2 * n_resamples)], stats[int((1 - alpha / 2) * n_resamples) - 1]
