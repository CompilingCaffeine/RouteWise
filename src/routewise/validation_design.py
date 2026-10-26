"""Confirmation planning and decision rules for workload validation (study m8, displayed as M7).

Planning values are 80% pilot confidence limits on the unfavourable side, so a 50-100
conversation pilot cannot make a confirmation look smaller than it needs to be. One-sample
requirements use exact binomial power for the one-sided 95% Wilson bounds of the deployment
gates; paired comparisons use the normal approximation for two-sided 95% paired intervals.
"""

from __future__ import annotations

import math
from statistics import NormalDist, fmean, stdev
from typing import Any

from routewise.statistics import wilson

POWER = 0.90
PLANNING_CONFIDENCE = 0.80
MAX_N = 5000
PRACTICAL_N = 500
DEADLINE_MS = 4000  # recovery_metrics.DEADLINE_MS; the declared extraction deadline
DEFAULTS = {
    "max_wrong_release_rate": 0.02,
    "min_task_accuracy": 0.95,
    "min_field_accuracy": 0.95,
    "max_deadline_miss_rate": 0.05,
    "cac_margin_vs_previous": 0.02,
    "wrong_release_margin_vs_previous": 0.01,
}
ALTERNATIVES = {
    "max_wrong_release_rate": (0.01, 0.02, 0.03, 0.05),
    "cac_margin_vs_previous": (0.01, 0.02, 0.03, 0.05),
    "wrong_release_margin_vs_previous": (0.005, 0.01, 0.02),
}
_Z_PAIRED = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(POWER)


# ---------------------------------------------------------------- exact binomial power


def _log_pmf(x: int, n: int, p: float) -> float:
    return math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1) + x * math.log(p) + (n - x) * math.log1p(-p)


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0:
        return 1.0
    if p >= 1:
        return 0.0
    return min(1.0, sum(math.exp(_log_pmf(x, n, p)) for x in range(k + 1)))


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k), summed over the upper tail."""
    if k <= 0:
        return 1.0
    if k > n or p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    return min(1.0, sum(math.exp(_log_pmf(x, n, p)) for x in range(k, n + 1)))


def _largest_passing_count(n: int, limit: float) -> int:
    """Largest event count whose one-sided 95% Wilson upper bound is at most `limit` (-1 if none)."""
    lo, hi = -1, n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if wilson(mid, n, one_sided=True)[1] <= limit:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _smallest_passing_count(n: int, limit: float) -> int:
    """Smallest success count whose one-sided 95% Wilson lower bound is at least `limit` (n+1 if none)."""
    lo, hi = 0, n + 1
    while lo < hi:
        mid = (lo + hi) // 2
        if wilson(mid, n, one_sided=True)[0] >= limit:
            hi = mid
        else:
            lo = mid + 1
    return lo


def power_rate_below(n: int, rate: float, limit: float) -> float:
    """Probability that the one-sided 95% upper bound is at most `limit` when the true rate is `rate`."""
    return binom_cdf(_largest_passing_count(n, limit), n, rate)


def power_rate_above(n: int, rate: float, limit: float) -> float:
    """Probability that the one-sided 95% lower bound is at least `limit` when the true rate is `rate`."""
    return binom_sf(_smallest_passing_count(n, limit), n, rate)


def smallest_n(power, feasible: bool, *, stable: int = 25) -> int | None:
    """Smallest n whose power, and that of the next `stable` sizes, is at least 90% (exact power saw-tooths)."""
    if not feasible:
        return None
    start = None
    for n in range(1, MAX_N + stable + 1):
        if power(n) >= POWER:
            start = start or n
            if n - start >= stable:
                return start if start <= MAX_N else None
        else:
            start = None
    return None


# ---------------------------------------------------------------- paired comparisons


def paired_n(delta: float, q: float, margin: float) -> int | None:
    """Conversations for a two-sided 95% paired interval to clear -margin with 90% power.

    `delta` is the planning difference in the favourable direction; `q` the discordant-pair proportion.
    """
    effect = delta + margin
    if effect <= 0:
        return None
    return max(1, math.ceil(_Z_PAIRED ** 2 * max(q - delta * delta, 1e-9) / effect ** 2))


def paired_power(n: int, delta: float, q: float, margin: float) -> float:
    se = math.sqrt(max(q - delta * delta, 1e-9) / n)
    return NormalDist().cdf((delta + margin) / se - NormalDist().inv_cdf(0.975))


def mean_power(n: int, effect: float, sd: float) -> float:
    """Power for a two-sided 95% interval of a mean difference to exclude zero, `effect` > 0 favourable."""
    if sd == 0:
        return 1.0 if effect > 0 else 0.0
    return NormalDist().cdf(effect / (sd / math.sqrt(n)) - NormalDist().inv_cdf(0.975))


def planning_difference(observed: float) -> float:
    """Halve a favourable pilot difference (development optimism, as in M7); keep an unfavourable one whole."""
    return 0.5 * observed if observed > 0 else observed


# ---------------------------------------------------------------- the proposal


def _upper80(k: int, n: int) -> float:
    return wilson(k, n, PLANNING_CONFIDENCE, one_sided=True)[1]


def _lower80(k: int, n: int) -> float:
    return wilson(k, n, PLANNING_CONFIDENCE, one_sided=True)[0]


def wrong_release(pilot: dict[str, Any], limit: float, conservative: bool = True) -> dict[str, Any]:
    a, n = pilot["adopted"], pilot["n"]
    rate = _upper80(a["wrong_releases"], n) if conservative else a["wrong_releases"] / n
    return {"pilot": f"{a['wrong_releases']}/{n}", "planning_rate": rate, "limit": limit,
            "n": smallest_n(lambda k: power_rate_below(k, rate, limit), rate < limit)}


def task_accuracy(pilot: dict[str, Any], limit: float, conservative: bool = True) -> dict[str, Any]:
    a, n = pilot["adopted"], pilot["n"]
    fraction = _lower80(a["answerable"], n) if conservative else a["answerable"] / n
    accuracy = (_lower80(a["correct"], a["answerable"]) if conservative else a["correct"] / a["answerable"]) \
        if a["answerable"] else 0.0
    m = smallest_n(lambda k: power_rate_above(k, accuracy, limit), accuracy > limit and fraction > 0)
    return {"pilot": f"{a['correct']}/{a['answerable']}", "planning_accuracy": accuracy,
            "planning_answerable_fraction": fraction, "limit": limit, "n_answerable": m,
            "n": math.ceil(m / fraction) if m else None}


def deadline_misses(pilot: dict[str, Any], limit: float, conservative: bool = True) -> dict[str, Any]:
    a, n = pilot["adopted"], pilot["n"]
    rate = _upper80(a["deadline_misses"], n) if conservative else a["deadline_misses"] / n
    return {"pilot": f"{a['deadline_misses']}/{n}", "planning_rate": rate, "limit": limit, "deadline_ms": DEADLINE_MS,
            "n": smallest_n(lambda k: power_rate_below(k, rate, limit), rate < limit)}


def versus_previous(pilot: dict[str, Any], metric: str, margin: float, conservative: bool = True) -> dict[str, Any]:
    """Non-inferiority to the previous profile; CAC favours positive differences, wrong releases negative."""
    sign = 1 if metric == "cac" else -1
    p = pilot["vs_previous"][metric]
    discordant = p["a_only"] + p["b_only"]
    favourable = planning_difference(sign * p["diff"]) if conservative else sign * p["diff"]
    q = _upper80(discordant, p["n_pairs"]) if conservative else discordant / p["n_pairs"]
    return {"pilot_difference": p["diff"], "pilot_discordant": f"{discordant}/{p['n_pairs']}",
            "planning_difference": sign * favourable, "planning_discordance": q, "margin": margin,
            "n": paired_n(favourable, q, margin)}


def cost_versus_super(pilot: dict[str, Any]) -> dict[str, Any]:
    diffs = pilot["cost_diffs_vs_grounded_super"]
    mean, sd = fmean(diffs), stdev(diffs) if len(diffs) > 1 else 0.0
    n = None
    if mean < 0:
        n = 2 if sd == 0 else max(2, math.ceil((_Z_PAIRED * sd / -mean) ** 2))
    return {"pilot_mean_per_1k": mean * 1000, "pilot_sd_per_1k": sd * 1000, "n": n}


def criteria(pilot: dict[str, Any], params: dict[str, float], *, conservative: bool = True) -> dict[str, dict[str, Any]]:
    """Per-criterion planning values and sample sizes. `conservative=False` plans with point estimates."""
    return {
        "primary_wrong_release": wrong_release(pilot, params["max_wrong_release_rate"], conservative),
        "task_accuracy": task_accuracy(pilot, params["min_task_accuracy"], conservative),
        "deadline_misses": deadline_misses(pilot, params["max_deadline_miss_rate"], conservative),
        "cac_vs_previous": versus_previous(pilot, "cac", params["cac_margin_vs_previous"], conservative),
        "wrong_release_vs_previous": versus_previous(pilot, "undetected", params["wrong_release_margin_vs_previous"],
                                                     conservative),
        "cost_vs_grounded_super": cost_versus_super(pilot),
    }


def powers(n: int, crit: dict[str, dict[str, Any]]) -> dict[str, float]:
    c = crit
    acc = c["task_accuracy"]
    out = {
        "primary_wrong_release": power_rate_below(n, c["primary_wrong_release"]["planning_rate"],
                                                  c["primary_wrong_release"]["limit"]),
        "task_accuracy": power_rate_above(math.floor(n * acc["planning_answerable_fraction"]),
                                          acc["planning_accuracy"], acc["limit"]),
        "deadline_misses": power_rate_below(n, c["deadline_misses"]["planning_rate"], c["deadline_misses"]["limit"]),
    }
    for key, sign in (("cac_vs_previous", 1), ("wrong_release_vs_previous", -1)):
        out[key] = paired_power(n, sign * c[key]["planning_difference"], c[key]["planning_discordance"], c[key]["margin"])
    cost = c["cost_vs_grounded_super"]
    out["cost_vs_grounded_super"] = mean_power(n, -cost["pilot_mean_per_1k"], cost["pilot_sd_per_1k"])
    return out


def propose(pilot: dict[str, Any], params: dict[str, float] | None = None) -> dict[str, Any]:
    """The confirmation design a completed pilot implies; nothing here is a result."""
    params = {**DEFAULTS, **(params or {})}
    crit = criteria(pilot, params)
    blocked = {k: v for k, v in crit.items() if v["n"] is None}
    risks = []
    a = pilot["adopted"]
    if a["field_accuracy"] is not None and a["field_accuracy"] < params["min_field_accuracy"]:
        risks.append(f"pilot field accuracy {a['field_accuracy']:.3f} is below {params['min_field_accuracy']}")
    if a["incomplete_cost"]:
        risks.append(f"{a['incomplete_cost']} pilot request(s) had incomplete cost")
    design: dict[str, Any] = {"parameters": params, "power": POWER, "planning_confidence": PLANNING_CONFIDENCE,
                              "criteria": crit, "risks": risks,
                              "optimistic_n": {k: v["n"] for k, v in criteria(pilot, params, conservative=False).items()}}
    design["alternatives"] = {
        "max_wrong_release_rate": {str(v): wrong_release(pilot, v)["n"] for v in ALTERNATIVES["max_wrong_release_rate"]},
        "cac_margin_vs_previous": {str(v): versus_previous(pilot, "cac", v)["n"]
                                   for v in ALTERNATIVES["cac_margin_vs_previous"]},
        "wrong_release_margin_vs_previous": {str(v): versus_previous(pilot, "undetected", v)["n"]
                                             for v in ALTERNATIVES["wrong_release_margin_vs_previous"]},
    }
    if blocked or risks:
        design.update(feasible=False, n_required=None, n_collect=None,
                      recommendation="Do not start the confirmation. " + "; ".join(
                          [f"{k} cannot reach 90% power at its planning value" for k in blocked] + risks)
                      + ". Diagnose the pilot, change the route or the requirement on development data, and pilot again.")
        return design
    n_required = max(v["n"] for v in crit.values())
    pw = powers(n_required, crit)
    exclusion = _upper80(pilot["n_excluded"], pilot["n_collected"])
    n_collect = math.ceil(n_required / (1 - exclusion))
    design.update(
        feasible=True, n_required=n_required, binding=max(crit, key=lambda k: crit[k]["n"]),
        power_at_n_required=pw, joint_power_if_independent=math.prod(pw.values()),
        planning_exclusion_rate=exclusion, n_collect=n_collect,
        labels_needed=2 * n_collect,
        expected_adjudications=math.ceil(pilot["label_disagreement_rate"] * n_collect),
        api_cost_estimate_usd=pilot["cost_per_conversation_usd"] * n_collect,
        recommendation=(f"Collect {n_collect} fresh held-out conversations" + (
            " (large: compare the alternatives before committing to this labeling effort)"
            if n_collect > PRACTICAL_N else "") + "."))
    return design


# ---------------------------------------------------------------- the confirmation decision


def decide(params: dict[str, float], adopted: dict[str, Any], vs_previous: dict[str, Any],
           vs_super: dict[str, Any]) -> dict[str, Any]:
    """Every check must pass for the adopted profile to be validated on this workload."""
    lower = adopted["accuracy_lower95"]
    checks = {
        "primary_wrong_release": adopted["undetected_upper95"] <= params["max_wrong_release_rate"],
        "requirements": (lower is not None and lower >= params["min_task_accuracy"]
                         and (adopted["field_accuracy"] or 0) >= params["min_field_accuracy"]
                         and adopted["deadline_miss_upper95"] <= params["max_deadline_miss_rate"]
                         and adopted["incomplete_cost_count"] == 0),
        "vs_previous": (vs_previous["cac"]["ci95"][0] >= -params["cac_margin_vs_previous"]
                        and vs_previous["undetected"]["ci95"][1] <= params["wrong_release_margin_vs_previous"]),
        "cost_vs_grounded_super": vs_super["cost_per_1k"]["ci95"][1] < 0,
    }
    return {"checks": checks, "validated": all(checks.values())}
