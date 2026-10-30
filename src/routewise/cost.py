"""The single cost function. Every strategy, report and dashboard view prices calls here.

Billing model (Token Factory serverless, per docs.tokenfactory.nebius.com/cached-tokens):
  uncached prompt tokens x input price
+ cached prompt tokens   x cached-input price (falls back to input price if none is published)
+ completion tokens      x output price (reasoning tokens are part of completion tokens)
+ flat per-request price (usually 0)

OpenRouter bills the same structure at the pinned host's endpoint price, and reports its charge on every
response (usage.cost); `billing_matches` compares that charge with this function.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """USD per token, as published by /v1/models?verbose=true."""

    model_id: str
    prompt: float
    completion: float
    input_cache_read: float | None = None
    request: float = 0.0

    @property
    def prompt_per_million(self) -> float:
        return self.prompt * 1_000_000

    @property
    def completion_per_million(self) -> float:
        return self.completion * 1_000_000


def call_cost_usd(
    price: Price,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    if min(prompt_tokens, completion_tokens, cached_tokens) < 0:
        raise ValueError("token counts must be non-negative")
    if cached_tokens > prompt_tokens:
        raise ValueError("cached_tokens cannot exceed prompt_tokens")
    cached_rate = price.input_cache_read if price.input_cache_read is not None else price.prompt
    return (
        (prompt_tokens - cached_tokens) * price.prompt
        + cached_tokens * cached_rate
        + completion_tokens * price.completion
        + price.request
    )


def billing_matches(computed_usd: float | None, reported_usd: float | None) -> bool | None:
    """Provider-reported charge vs call_cost_usd, within rounding. None when either side is unknown."""
    if computed_usd is None or reported_usd is None:
        return None
    return math.isclose(computed_usd, reported_usd, rel_tol=1e-3, abs_tol=1e-9)


def cost_per_1k_tasks(total_cost_usd: float, n_tasks: int) -> float:
    if n_tasks <= 0:
        raise ValueError("n_tasks must be positive")
    return total_cost_usd / n_tasks * 1000
