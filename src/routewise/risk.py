"""M3: learned failure predictor. Trains on train families, calibrates and picks a threshold on
calibration families, and evaluates routing policies on unseen test families by replaying
recorded calls (every call billed with the single cost function).

Training needs scikit-learn (the `ml` extra); the saved model is plain JSON with pure-Python
inference so the router and dashboard never import scikit-learn.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any

from routewise.evaluate import score
from routewise.features import FEATURES, vector
from routewise.metrics import percentile
from routewise.schema import CallRecord, Example
from routewise.validate import validate

POLICIES = ("always_smallest", "always_strongest", "validator", "risk_only", "risk+validator", "oracle")
THRESHOLDS = tuple(round(0.05 * i, 2) for i in range(1, 20))


# ---------------------------------------------------------------- portable model


@dataclass(frozen=True)
class RiskModel:
    """Standardized logistic regression + Platt calibration, as plain numbers."""

    features: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    platt_a: float
    platt_b: float
    threshold: float

    def raw_score(self, x: Sequence[float]) -> float:
        return self.intercept + sum(c * (v - m) / s for c, v, m, s in zip(self.coef, x, self.means, self.scales))

    def probability(self, text: str) -> float:
        z = self.platt_a * self.raw_score(vector(text)) + self.platt_b
        return 1 / (1 + math.exp(-z))

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RiskModel:
        return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def load(cls, path: Path) -> RiskModel:
        return cls.from_dict(json.loads(path.read_text())["model"])


# ---------------------------------------------------------------- data


@dataclass(frozen=True)
class Row:
    example: Example
    sample: int  # which Nano sample (0, 1)
    nano: CallRecord
    super_: CallRecord
    ultra: CallRecord
    nano_wrong: bool
    validator_flags: bool


def build_rows(examples: Sequence[Example], nano_runs: Sequence[dict[str, CallRecord]],
               super_run: dict[str, CallRecord], ultra_run: dict[str, CallRecord]) -> list[Row]:
    rows = []
    for ex in examples:
        for s, nano_run in enumerate(nano_runs):
            nano = nano_run[ex.id]
            rows.append(Row(
                example=ex, sample=s, nano=nano, super_=super_run[ex.id], ultra=ultra_run[ex.id],
                nano_wrong=not score(ex.task, ex.expected, nano.parsed).task_correct,
                validator_flags=not validate(ex.task, ex.input, nano.parsed_output()).passed,
            ))
    return rows


def _lat(r: CallRecord) -> float:
    return r.latency_ms if r.latency_ms is not None else r.total_ms


def _correct(r: CallRecord, ex: Example) -> tuple[bool, float]:
    s = score(ex.task, ex.expected, r.parsed)
    return s.task_correct, s.field_accuracy


def replay(policy: str, row: Row, p: float | None = None, tau: float | None = None) -> dict[str, Any]:
    """Final answer, cost and latency of one request under one policy."""
    ex, nano, sup = row.example, row.nano, row.super_
    if policy == "always_smallest":
        calls, final = [nano], nano
    elif policy == "always_strongest":
        calls, final = [row.ultra], row.ultra
    elif policy == "validator":
        calls = [nano, sup] if row.validator_flags else [nano]
        final = calls[-1]
    elif policy == "risk_only":
        calls = [sup] if p >= tau else [nano]
        final = calls[-1]
    elif policy == "risk+validator":
        calls = [sup] if p >= tau else ([nano, sup] if row.validator_flags else [nano])
        final = calls[-1]
    elif policy == "oracle":
        calls = [sup] if row.nano_wrong else [nano]
        final = calls[-1]
    else:
        raise ValueError(policy)
    correct, field_acc = _correct(final, ex)
    return {"correct": correct, "field_accuracy": field_acc, "cost": sum(c.cost_usd or 0 for c in calls),
            "latency": sum(_lat(c) for c in calls), "super_calls": sum(c is sup for c in calls),
            "missed_error": row.nano_wrong and final is nano}


def policy_metrics(rows: Sequence[Row], policy: str, probs: Sequence[float] | None = None, tau: float | None = None) -> dict[str, Any]:
    outs = [replay(policy, r, probs[i] if probs is not None else None, tau) for i, r in enumerate(rows)]
    n = len(outs)
    return {
        "n": n,
        "task_accuracy": sum(o["correct"] for o in outs) / n,
        "field_accuracy": sum(o["field_accuracy"] for o in outs) / n,
        "cost_per_1k_tasks": sum(o["cost"] for o in outs) / n * 1000,
        "p50_latency_ms": percentile([o["latency"] for o in outs], 50),
        "p95_latency_ms": percentile([o["latency"] for o in outs], 95),
        "super_call_rate": sum(o["super_calls"] for o in outs) / n,
        "missed_errors": sum(o["missed_error"] for o in outs),
        "_correct": [o["correct"] for o in outs],
        "_cost": [o["cost"] for o in outs],
    }


# ---------------------------------------------------------------- training and evaluation


def _xy(rows: Sequence[Row]):
    import numpy as np

    return np.array([vector(r.example.input) for r in rows]), np.array([int(r.nano_wrong) for r in rows])


def _auc(y, p) -> float | None:
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, p)) if len(set(y)) > 1 else None


def _pipeline(c: float):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=5000))


def cross_validated(rows: Sequence[Row], c: float, grouped: bool, seed: int = 7) -> dict[str, float | None]:
    """Out-of-fold AUC and log-loss; folds are random or grouped by scenario family."""
    import numpy as np
    from sklearn.metrics import log_loss
    from sklearn.model_selection import GroupKFold, KFold

    x, y = _xy(rows)
    groups = np.array([r.example.tags[0] for r in rows])
    # Keep both Nano samples of an example in the same fold even when folds are random.
    ex_ids = np.array([r.example.id for r in rows])
    unique = sorted(set(ex_ids))
    if grouped:
        splitter, split_on, split_groups = GroupKFold(n_splits=5), np.arange(len(rows)), groups
        folds = splitter.split(split_on, y, split_groups)
    else:
        kf = KFold(n_splits=5, shuffle=True, random_state=seed)
        fold_of = {eid: k for k, (_, test) in enumerate(kf.split(unique)) for eid in np.array(unique)[test]}
        folds = [(np.where([fold_of[e] != k for e in ex_ids])[0], np.where([fold_of[e] == k for e in ex_ids])[0])
                 for k in range(5)]
    oof = np.zeros(len(rows))
    for train_idx, test_idx in folds:
        model = _pipeline(c).fit(x[train_idx], y[train_idx])
        oof[test_idx] = model.predict_proba(x[test_idx])[:, 1]
    return {"auc": _auc(y, oof), "log_loss": float(log_loss(y, np.clip(oof, 1e-6, 1 - 1e-6)))}


def fit_platt(raw: Sequence[float], y: Sequence[int]) -> tuple[float, float]:
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    if len(set(y)) < 2:
        return 1.0, 0.0
    lr = LogisticRegression(C=1e6, max_iter=5000).fit(np.array(raw).reshape(-1, 1), np.array(y))
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def choose_threshold(rows: Sequence[Row], probs: Sequence[float]) -> tuple[float, list[dict[str, Any]]]:
    """Maximize risk+validator accuracy subject to cost <= 2x the validator policy; ties -> cheaper."""
    budget = 2 * policy_metrics(rows, "validator")["cost_per_1k_tasks"]
    table = []
    for tau in THRESHOLDS:
        m = policy_metrics(rows, "risk+validator", probs, tau)
        table.append({"tau": tau, "task_accuracy": m["task_accuracy"], "cost_per_1k_tasks": m["cost_per_1k_tasks"],
                      "within_budget": m["cost_per_1k_tasks"] <= budget})
    eligible = [t for t in table if t["within_budget"]] or table
    best = max(eligible, key=lambda t: (t["task_accuracy"], -t["cost_per_1k_tasks"]))
    return best["tau"], table


def brier(probs: Sequence[float], y: Sequence[int]) -> float:
    return sum((p - t) ** 2 for p, t in zip(probs, y)) / len(y)


def reliability(probs: Sequence[float], y: Sequence[int], bins: int = 10) -> list[dict[str, Any]]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx), "mean_predicted": sum(probs[i] for i in idx) / len(idx),
                        "observed_failure_rate": sum(y[i] for i in idx) / len(idx)})
    return out


def paired_bootstrap(a: Sequence[float], b: Sequence[float], n: int = 4000, seed: int = 0) -> tuple[float, float]:
    rng = Random(seed)
    k = len(a)
    diffs = sorted(sum(a[j] - b[j] for j in (rng.randrange(k) for _ in range(k))) / k for _ in range(n))
    return diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]


def run_study(train: Sequence[Row], calib: Sequence[Row], test: Sequence[Row]) -> tuple[RiskModel, dict[str, Any]]:
    import numpy as np
    from sklearn.metrics import average_precision_score
    from sklearn.tree import DecisionTreeClassifier

    # 1. C by family-grouped cross-validated log-loss on the train families.
    cv = {c: cross_validated(train, c, grouped=True) for c in (0.1, 1.0, 10.0)}
    c_best = min(cv, key=lambda c: cv[c]["log_loss"])
    # 2. H3b: random vs grouped folds, same model.
    leakage = {"random_cv_auc": cross_validated(train, c_best, grouped=False)["auc"],
               "grouped_cv_auc": cv[c_best]["auc"]}

    x_tr, y_tr = _xy(train)
    pipe = _pipeline(c_best).fit(x_tr, y_tr)
    scaler, lr = pipe.named_steps["standardscaler"], pipe.named_steps["logisticregression"]

    def raw(rows):
        x, _ = _xy(rows)
        return lr.decision_function(scaler.transform(x)).tolist()

    # 3. Platt calibration on the calibration families.
    x_cal, y_cal = _xy(calib)
    a, b = fit_platt(raw(calib), y_cal.tolist())
    model = RiskModel(features=FEATURES, means=tuple(scaler.mean_.tolist()), scales=tuple(scaler.scale_.tolist()),
                      coef=tuple(lr.coef_[0].tolist()), intercept=float(lr.intercept_[0]), platt_a=a, platt_b=b,
                      threshold=0.5)

    def probs(rows):
        return [1 / (1 + math.exp(-(a * z + b))) for z in raw(rows)]

    # 4. Threshold on the calibration families.
    p_cal = probs(calib)
    tau, tau_table = choose_threshold(calib, p_cal)
    model = RiskModel(**{**model.__dict__, "threshold": tau})

    # 5. Test families: discrimination, calibration, policies.
    _, y_te = _xy(test)
    p_te = probs(test)
    base_rate = float(y_tr.mean())
    tree = DecisionTreeClassifier(max_depth=3, random_state=0).fit(x_tr, y_tr)
    x_te, _ = _xy(test)
    policies = {name: policy_metrics(test, name, p_te, tau) for name in POLICIES}
    rv, val = policies["risk+validator"], policies["validator"]
    acc_ci = paired_bootstrap([float(c) for c in rv["_correct"]], [float(c) for c in val["_correct"]])
    per_sample = {}
    for s in sorted({r.sample for r in test}):
        idx = [i for i, r in enumerate(test) if r.sample == s]
        sub = [test[i] for i in idx]
        sub_p = [p_te[i] for i in idx]
        per_sample[s] = {name: {k: v for k, v in policy_metrics(sub, name, sub_p, tau).items() if not k.startswith("_")}
                         for name in ("validator", "risk+validator")}
    for m in policies.values():
        m.pop("_correct"), m.pop("_cost")

    summary = {
        "n_rows": {"train": len(train), "calibration": len(calib), "test": len(test)},
        "failure_rate": {"train": base_rate, "calibration": float(y_cal.mean()), "test": float(y_te.mean())},
        "model_selection": {"cv": cv, "chosen_C": c_best},
        "leakage": {**leakage, "gap": (leakage["random_cv_auc"] or 0) - (leakage["grouped_cv_auc"] or 0)},
        "calibration": {"platt_a": a, "platt_b": b},
        "threshold": {"tau": tau, "table": tau_table},
        "test": {
            "auc": _auc(y_te.tolist(), p_te),
            "average_precision": float(average_precision_score(y_te, p_te)) if y_te.sum() else None,
            "tree_auc": _auc(y_te.tolist(), tree.predict_proba(x_te)[:, 1].tolist()),
            "brier": brier(p_te, y_te.tolist()),
            "brier_base_rate": brier([base_rate] * len(y_te), y_te.tolist()),
            "reliability": reliability(p_te, y_te.tolist()),
            "policies": policies,
            "per_sample": per_sample,
            "risk_vs_validator": {
                "accuracy_diff": rv["task_accuracy"] - val["task_accuracy"],
                "accuracy_diff_ci95": acc_ci,
                "cost_ratio": rv["cost_per_1k_tasks"] / val["cost_per_1k_tasks"] if val["cost_per_1k_tasks"] else None,
            },
        },
        "coefficients": dict(zip(FEATURES, lr.coef_[0].tolist())),
    }
    h = summary["test"]
    summary["hypotheses"] = {
        "H3a_auc_ge_0.70": h["auc"] is not None and h["auc"] >= 0.70,
        "H3b_leakage_gap_ge_0.05": summary["leakage"]["gap"] >= 0.05,
        "H3c_more_accurate_within_2x_cost": h["risk_vs_validator"]["accuracy_diff"] > 0 and (h["risk_vs_validator"]["cost_ratio"] or 99) <= 2,
        "H3d_brier_beats_base_rate": h["brier"] < h["brier_base_rate"],
    }
    lo, _ = acc_ci
    summary["decision"] = {"adopt": lo > 0 and (h["risk_vs_validator"]["cost_ratio"] or 99) <= 2,
                           "rule": "pooled paired-bootstrap 95% CI of accuracy diff excludes 0 and cost ratio <= 2"}
    return model, summary
