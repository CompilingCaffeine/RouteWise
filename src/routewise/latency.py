"""M1 latency breakdown (TTFT vs decode) and M2-lite concurrency sweep, both via streaming calls."""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from random import Random
from typing import Any

from pydantic import BaseModel

from routewise.benchmark import run_parallel
from routewise.client import ChatResult, TokenFactoryClient
from routewise.config import Candidate, Config
from routewise.cost import Price, call_cost_usd
from routewise.metrics import percentile
from routewise.prompts import PromptTemplate, response_format
from routewise.schema import Example

# Approximate prompt sizes in tokens (actual counts are recorded from usage).
INPUT_SIZES = {"short": 400, "medium": 2000, "long": 8000}
OUTPUT_SHAPES = ("json", "text")
TEXT_INSTRUCTION = (
    "Summarize the conversation below for a support supervisor in about 250 words: what the customer wanted, "
    "what was agreed, and any follow-up needed. Plain prose, no lists."
)
# Neutral filler used to grow the prompt; it never changes what the task asks for.
FILLER = (
    "Store policy reference. Returns are accepted within 30 days of delivery when items are unused and in their "
    "original packaging. Refunds go back to the original payment method. Exchanges for a different size or colour "
    "are free of charge. Damaged deliveries should be reported within 7 days with a photo of the packaging. "
    "Subscription plans renew automatically unless cancelled before the renewal date. "
)
CHARS_PER_TOKEN = 4.0


class LatencyRecord(BaseModel):
    experiment: str  # "profile" or "load"
    model: str
    model_id: str
    input_size: str | None = None
    output_shape: str | None = None
    concurrency: int | None = None
    rep: int
    example_id: str
    started_at: str
    dispatch_offset_ms: float | None = None  # load sweep: dispatch time relative to level start
    complete_offset_ms: float | None = None
    ttft_ms: float | None
    decode_ms: float | None
    tpot_ms: float | None
    e2e_ms: float | None
    total_ms: float
    attempts: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_tokens: int | None
    cost_usd: float | None
    over_limit: bool
    finish_reason: str | None
    error: str | None


def _record(result: ChatResult, price: Price, **fields: Any) -> LatencyRecord:
    tokens = result.completion_tokens
    tpot = result.decode_ms / (tokens - 1) if result.decode_ms is not None and tokens and tokens > 1 else None
    has_usage = result.prompt_tokens is not None and result.completion_tokens is not None
    return LatencyRecord(
        **fields,
        model_id=result.model_id,
        started_at=result.started_at,
        ttft_ms=result.ttft_ms,
        decode_ms=result.decode_ms,
        tpot_ms=round(tpot, 2) if tpot is not None else None,
        e2e_ms=result.latency_ms,
        total_ms=result.total_ms,
        attempts=result.attempts,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cached_tokens=result.cached_tokens,
        cost_usd=call_cost_usd(price, result.prompt_tokens, result.completion_tokens, result.cached_tokens or 0)
        if has_usage else None,
        over_limit=result.ratelimit.get("x-ratelimit-over-limit") == "yes",
        finish_reason=result.finish_reason,
        error=result.error,
    )


def padded_conversation(text: str, target_tokens: int, base_tokens: int) -> str:
    """Prepend neutral policy text so the whole prompt reaches roughly `target_tokens`."""
    missing_chars = max(0, int((target_tokens - base_tokens) * CHARS_PER_TOKEN))
    if missing_chars == 0:
        return text
    filler = (FILLER * (missing_chars // len(FILLER) + 1))[:missing_chars]
    return f"{filler.strip()}\n\nConversation:\n{text}"


def profile_messages(prompt: PromptTemplate, example: Example, size: str, shape: str) -> list[dict[str, str]]:
    # A unique nonce first defeats prefix caching, so every call pays full prefill.
    nonce = f"[request {secrets.token_hex(8)}]\n"
    if shape == "json":
        system = nonce + prompt.messages("")[0]["content"]
    else:
        system = nonce + TEXT_INSTRUCTION
    base_tokens = int((len(system) + len(example.input)) / CHARS_PER_TOKEN)
    return [{"role": "system", "content": system},
            {"role": "user", "content": padded_conversation(example.input, INPUT_SIZES[size], base_tokens)}]


def run_profile(
    client: TokenFactoryClient,
    config: Config,
    models: Sequence[Candidate],
    prices: dict[str, Price],
    prompt: PromptTemplate,
    example: Example,
    *,
    reps: int,
    seed: int,
    concurrency: int,
    rpm: float,
    sink: Callable[[LatencyRecord], None],
) -> list[LatencyRecord]:
    cells = [(m, size, shape, rep) for m in models for size in INPUT_SIZES for shape in OUTPUT_SHAPES for rep in range(reps)]
    Random(seed).shuffle(cells)
    lock = threading.Lock()

    def job(m: Candidate, size: str, shape: str, rep: int) -> Callable[[], LatencyRecord]:
        def run_one() -> LatencyRecord:
            result = client.chat(
                model=m.model_id,
                messages=profile_messages(prompt, example, size, shape),
                max_tokens=m.max_tokens if shape == "json" else 400,
                temperature=config.temperature,
                seed=config.seed,
                response_format=response_format("extraction", config.response_format) if shape == "json" else None,
                extra_body=m.extra_body,
                stream=True,
            )
            rec = _record(result, prices[m.label], experiment="profile", model=m.label, input_size=size,
                          output_shape=shape, rep=rep, example_id=example.id)
            with lock:
                sink(rec)
            return rec

        return run_one

    return run_parallel([job(*c) for c in cells], concurrency=concurrency, rpm=rpm)  # type: ignore[return-value]


def run_load_level(
    client: TokenFactoryClient,
    config: Config,
    model: Candidate,
    price: Price,
    prompt: PromptTemplate,
    examples: Sequence[Example],
    *,
    concurrency: int,
    n_requests: int,
    seed: int,
    sink: Callable[[LatencyRecord], None],
) -> list[LatencyRecord]:
    """Closed loop: `concurrency` workers, each sending its next request when the previous one ends."""
    order = list(examples)
    Random(seed).shuffle(order)
    workload = [order[i % len(order)] for i in range(n_requests)]
    lock = threading.Lock()
    t0 = time.perf_counter()

    def one(i: int, ex: Example) -> LatencyRecord:
        dispatch = (time.perf_counter() - t0) * 1000
        result = client.chat(
            model=model.model_id,
            messages=prompt.messages(ex.input),
            max_tokens=model.max_tokens,
            temperature=config.temperature,
            seed=config.seed,
            response_format=response_format(ex.task, config.response_format),
            extra_body=model.extra_body,
            stream=True,
        )
        rec = _record(result, price, experiment="load", model=model.label, concurrency=concurrency, rep=i,
                      example_id=ex.id, dispatch_offset_ms=round(dispatch, 1),
                      complete_offset_ms=round((time.perf_counter() - t0) * 1000, 1))
        with lock:
            sink(rec)
        return rec

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(lambda args: one(*args), enumerate(workload)))


def _stats(values: Sequence[float | None]) -> dict[str, float | None]:
    vals = [v for v in values if v is not None]
    return {"p50": percentile(vals, 50), "p95": percentile(vals, 95), "p99": percentile(vals, 99),
            "max": max(vals) if vals else None}


def summarize_profile(records: Sequence[LatencyRecord]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[LatencyRecord]] = defaultdict(list)
    for r in records:
        groups[(r.model, r.input_size, r.output_shape)].append(r)
    rows = []
    for (model, size, shape), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], list(INPUT_SIZES).index(kv[0][1]), kv[0][2])):
        ok = [r for r in rs if r.error is None]
        shares = [r.ttft_ms / r.e2e_ms for r in ok if r.ttft_ms is not None and r.e2e_ms]
        rows.append({
            "model": model, "input_size": size, "output_shape": shape, "n": len(rs), "errors": len(rs) - len(ok),
            "prompt_tokens": _mean([r.prompt_tokens for r in ok]),
            "completion_tokens": _mean([r.completion_tokens for r in ok]),
            "cached_tokens": _mean([r.cached_tokens for r in ok]),
            "ttft_ms": _stats([r.ttft_ms for r in ok]),
            "tpot_ms": _stats([r.tpot_ms for r in ok]),
            "decode_ms": _stats([r.decode_ms for r in ok]),
            "e2e_ms": _stats([r.e2e_ms for r in ok]),
            "ttft_share_median": percentile(shares, 50),
            "cost_usd_total": sum(r.cost_usd or 0 for r in rs),
        })
    return rows


def summarize_load(records: Sequence[LatencyRecord]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[LatencyRecord]] = defaultdict(list)
    for r in records:
        groups[(r.model, r.concurrency)].append(r)
    rows = []
    for (model, c), rs in sorted(groups.items()):
        ok = [r for r in rs if r.error is None]
        span_s = (max(r.complete_offset_ms for r in rs) - min(r.dispatch_offset_ms for r in rs)) / 1000
        rows.append({
            "model": model, "concurrency": c, "n": len(rs), "errors": len(rs) - len(ok),
            "throughput_rps": len(ok) / span_s if span_s > 0 else None,
            "output_tokens_per_s": sum(r.completion_tokens or 0 for r in ok) / span_s if span_s > 0 else None,
            "ttft_ms": _stats([r.ttft_ms for r in ok]),
            "e2e_ms": _stats([r.e2e_ms for r in ok]),
            "retried_requests": sum(r.attempts > 1 for r in rs),
            "over_limit_responses": sum(r.over_limit for r in rs),
            "cached_tokens_mean": _mean([r.cached_tokens for r in ok]),
            "cost_usd_total": sum(r.cost_usd or 0 for r in rs),
        })
    return rows


def _mean(values: Sequence[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None
