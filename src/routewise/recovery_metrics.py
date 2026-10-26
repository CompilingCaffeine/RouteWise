"""M7 outcomes, paired comparisons, adoption gates and the declared sample-size formula.

Every rate uses all incoming requests as its denominator, so sending more requests to review
cannot look like an improvement. Review is never a correct autonomous answer.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

from routewise.metrics import percentile
from routewise.statistics import cluster_ci, wilson

MUI = 0.10
DEADLINE_MS = 4000
N_FAMILIES = 13


@dataclass(frozen=True)
class RequestOutcome:
    example_id: str
    family: str
    review_required: bool
    released: bool
    correct: bool  # all six fields match the label (meaningless when review is required)
    field_accuracy: float
    cost_usd: float
    latency_ms: float | None
    cost_complete: bool
    escalated: bool = False
    fallback: bool = False

    @property
    def cac(self) -> bool:
        return self.released and not self.review_required and self.correct

    @property
    def undetected(self) -> bool:
        return self.released and (self.review_required or not self.correct)

    @property
    def missed_deadline(self) -> bool:
        return self.latency_ms is None or self.latency_ms > DEADLINE_MS


def summarize(outcomes: Sequence[RequestOutcome]) -> dict[str, Any]:
    n = len(outcomes)
    if not n:
        return {"n": 0}
    answerable = [o for o in outcomes if not o.review_required]
    released = [o for o in outcomes if o.released]
    cac = sum(o.cac for o in outcomes)
    undetected = sum(o.undetected for o in outcomes)
    correct = sum(o.correct for o in answerable)
    misses = sum(o.missed_deadline for o in outcomes)
    latencies = [o.latency_ms for o in outcomes if o.latency_ms is not None]
    cost = sum(o.cost_usd for o in outcomes)
    return {
        "n": n,
        "n_review_required": n - len(answerable),
        "correct_autonomous": cac,
        "cac_rate": cac / n,
        "review_rate": (n - len(released)) / n,
        "necessary_review_rate": sum(o.review_required and not o.released for o in outcomes) / n,
        "avoidable_review_rate": sum(not o.review_required and not o.released for o in outcomes) / n,
        "undetected_errors": undetected,
        "undetected_rate": undetected / n,
        "undetected_upper95": wilson(undetected, n, one_sided=True)[1],
        "released_accuracy": cac / len(released) if released else None,
        "task_accuracy": correct / len(answerable) if answerable else None,
        "accuracy_lower95": wilson(correct, len(answerable), one_sided=True)[0] if answerable else None,
        "field_accuracy": sum(o.field_accuracy for o in answerable) / len(answerable) if answerable else None,
        "escalation_rate": sum(o.escalated for o in outcomes) / n,
        "fallback_rate": sum(o.fallback for o in outcomes) / n,
        "cost_usd_total": cost,
        "cost_per_1k_tasks": cost / n * 1000,
        "incomplete_cost_count": sum(not o.cost_complete for o in outcomes),
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "deadline_misses": misses,
        "deadline_miss_rate": misses / n,
        "deadline_miss_upper95": wilson(misses, n, one_sided=True)[1],
    }


METRICS = {
    "cac": lambda o: float(o.cac),
    "undetected": lambda o: float(o.undetected),
    "review": lambda o: float(not o.released),
    "cost_per_1k": lambda o: o.cost_usd * 1000,
}


def paired(a: Sequence[RequestOutcome], b: Sequence[RequestOutcome], metric: str) -> dict[str, Any]:
    """a minus b over shared conversations, with a conversation bootstrap and a family-cluster sensitivity CI."""
    fa, fb = {o.example_id: o for o in a}, {o.example_id: o for o in b}
    ids = sorted(fa.keys() & fb.keys())
    if not ids:
        return {"n_pairs": 0}
    f = METRICS[metric]
    diffs = [f(fa[i]) - f(fb[i]) for i in ids]
    out = {"n_pairs": len(ids), "diff": sum(diffs) / len(diffs), "ci95": cluster_ci(diffs, ids),
           "family_cluster_ci95": cluster_ci(diffs, [fa[i].family for i in ids])}
    if metric in ("cac", "undetected", "review"):
        out["a_only"] = sum(d > 0 for d in diffs)
        out["b_only"] = sum(d < 0 for d in diffs)
    return out


def sample_size(delta_dev: float, discordant_dev: float) -> dict[str, Any]:
    """Part A formula: halve the development effect, inflate for family clustering, clip per family."""
    z = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.90)
    delta = max(0.5 * delta_dev, 0.15)
    q = max(discordant_dev, delta + 0.05)
    n = math.ceil(1.5 * z * z * (q - delta * delta) / (delta - MUI) ** 2)
    per_family = min(max(math.ceil(n / N_FAMILIES), 15), 30)
    planned = math.ceil(per_family / 0.85)
    achieved = per_family * N_FAMILIES
    se = math.sqrt(1.5 * (q - delta * delta) / achieved)
    power = NormalDist().cdf((delta - MUI) / se - NormalDist().inv_cdf(0.975))
    return {"delta_dev": delta_dev, "discordant_dev": discordant_dev, "planning_delta": delta, "planning_q": q,
            "n_required": n, "accepted_per_family": per_family, "planned_per_family": planned,
            "n_accepted_target": achieved, "power_at_target": power, "cap_binds": math.ceil(n / N_FAMILIES) > 30}


def gates(challenge: dict[str, list[RequestOutcome]], representative: dict[str, list[RequestOutcome]],
          candidate: str) -> dict[str, Any]:
    """Primary criterion and safety gates S1-S4, exactly as preregistered."""
    primary = paired(challenge[candidate], challenge["control"], "cac")
    s1 = paired(challenge[candidate], challenge["control"], "undetected")
    rep = summarize(representative[candidate])
    s3_cac = paired(representative[candidate], representative["control"], "cac")
    s3_ue = paired(representative[candidate], representative["control"], "undetected")
    s4 = paired(representative[candidate], representative["fixed_grounded_super"], "cost_per_1k")
    checks = {
        "primary": primary["ci95"][0] > MUI,
        "S1": s1["ci95"][1] <= 0.02,
        "S2": (rep["accuracy_lower95"] >= 0.95 and rep["field_accuracy"] >= 0.95
               and rep["deadline_miss_upper95"] <= 0.05 and rep["incomplete_cost_count"] == 0),
        "S3": s3_cac["ci95"][0] >= -0.01 and s3_ue["ci95"][1] <= 0.01,
        "S4": s4["ci95"][1] < 0,
    }
    return {"checks": checks, "adopt": all(checks.values()),
            "detail": {"primary_cac_diff": primary, "S1_undetected_diff": s1, "S2_representative": rep,
                       "S3_cac_diff": s3_cac, "S3_undetected_diff": s3_ue, "S4_cost_diff_vs_grounded_super": s4}}
