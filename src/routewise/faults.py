"""Fault injection for controlled latency experiments (synthetic provider stalls).

A stall is declared up front for specific (model, request) pairs. It is keyed on the
request content, not call order, so every strategy sees the identical stall for the same
example. Injected time is added to the recorded latency and flagged on the call, so
accounting stays honest and the stall is visible in every trace.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from random import Random
from typing import Any

from routewise.client import ChatResult
from routewise.schema import Example


def content_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class StallPlan:
    model_label: str
    model_id: str
    fraction: float
    min_ms: float
    max_ms: float
    seed: int
    task: str
    delays_ms: dict[str, float]  # example id -> injected delay

    def to_manifest(self) -> dict[str, Any]:
        return {
            "model": self.model_label,
            "model_id": self.model_id,
            "fraction": self.fraction,
            "delay_range_ms": [self.min_ms, self.max_ms],
            "seed": self.seed,
            "task": self.task,
            "stalled_examples": self.delays_ms,
        }


def parse_spec(spec: str) -> tuple[str, float, float, float]:
    """'nano:0.10:3000-6000' -> ('nano', 0.10, 3000.0, 6000.0)."""
    label, fraction, delay = spec.split(":")
    lo, hi = delay.split("-")
    return label, float(fraction), float(lo), float(hi)


def plan_stalls(
    examples: Sequence[Example], *, model_label: str, model_id: str, fraction: float,
    min_ms: float, max_ms: float, seed: int, task: str,
) -> StallPlan:
    pool = sorted(e.id for e in examples if e.task == task)
    rng = Random(seed)
    k = max(1, round(fraction * len(pool))) if pool else 0
    chosen = sorted(rng.sample(pool, k))
    delays = {eid: float(round(rng.uniform(min_ms, max_ms))) for eid in chosen}
    return StallPlan(model_label, model_id, fraction, min_ms, max_ms, seed, task, delays)


class StallingClient:
    """Wraps a TokenFactoryClient; stalls declared (model, request) pairs before dispatch."""

    def __init__(self, inner: Any, plan: StallPlan, examples: Sequence[Example],
                 sleep: Callable[[float], None] = time.sleep) -> None:
        by_id = {e.id: e for e in examples}
        self._inner = inner
        self._sleep = sleep
        self._delays = {
            (plan.model_id, content_key(by_id[eid].input)): ms for eid, ms in plan.delays_ms.items()
        }

    def chat(self, *, model: str, messages: list[dict[str, str]], **kwargs: Any) -> ChatResult:
        delay = self._delays.get((model, content_key(messages[-1]["content"])), 0.0)
        if delay:
            self._sleep(delay / 1000)
        result = self._inner.chat(model=model, messages=messages, **kwargs)
        if delay:
            result.injected_delay_ms = delay
            result.total_ms += delay
            if result.latency_ms is not None:
                result.latency_ms += delay
        return result
