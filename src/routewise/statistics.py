"""Uncertainty for deployment decisions, with conversation-clustered comparisons."""

from __future__ import annotations

import math
from collections import defaultdict
from random import Random
from statistics import NormalDist
from typing import Sequence


def wilson(successes: int, n: int, confidence: float = 0.95, *, one_sided: bool = False) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    z = NormalDist().inv_cdf(confidence if one_sided else (1 + confidence) / 2)
    p = successes / n
    center = p + z * z / (2 * n)
    width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denominator = 1 + z * z / n
    return max(0.0, (center - width) / denominator), min(1.0, (center + width) / denominator)


def cluster_ci(values: Sequence[float], groups: Sequence[str], *, resamples: int = 4000,
               seed: int = 71) -> tuple[float, float]:
    """Percentile CI for a mean; repeat samples of a conversation travel together."""
    if len(values) != len(groups) or not values:
        raise ValueError('values and groups must have equal, nonzero lengths')
    clusters = defaultdict(list)
    for value, group in zip(values, groups):
        clusters[group].append(value)
    aggregates = [(sum(v), len(v)) for v in clusters.values()]
    rng = Random(seed)
    estimates = []
    for _ in range(resamples):
        draws = [aggregates[rng.randrange(len(aggregates))] for _ in aggregates]
        estimates.append(sum(s for s, _ in draws) / sum(n for _, n in draws))
    estimates.sort()
    return estimates[int(0.025 * resamples)], estimates[int(0.975 * resamples) - 1]


def cheapest_qualifying(metrics: dict, *, accuracy: float, deadline_ms: float,
                        field_accuracy: float = 0.95, confident: bool = True) -> str | None:
    """Abstain when no measured option clears every declared requirement."""
    eligible = []
    for name, m in metrics.items():
        quality = m['accuracy_lower95'] if confident else m['task_accuracy']
        latency_ok = (m['deadline_miss_upper95'] <= 0.05 if confident
                      else m['p95_latency_ms'] is not None and m['p95_latency_ms'] <= deadline_ms)
        if quality >= accuracy and m['field_accuracy'] >= field_accuracy and latency_ok and not m['incomplete_cost_count']:
            eligible.append(name)
    return min(eligible, key=lambda n: metrics[n]['cost_per_1k_tasks']) if eligible else None
