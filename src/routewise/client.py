"""Thin Token Factory client: the one place that times calls, normalizes usage and retries.

Only transport failures (HTTP 429, 5xx, connection errors) are retried here, and every
attempt is counted. Quality-driven escalation is the router's job, not the client's.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import openai
from openai import OpenAI

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
RATELIMIT_HEADERS = (
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-over-limit",
)
_RETRYABLE = (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError)
_MAX_BACKOFF_S = 60.0


class MissingCredentials(RuntimeError):
    pass


@dataclass
class ChatResult:
    model_id: str
    started_at: str  # UTC wall clock at first dispatch
    latency_ms: float | None  # dispatch -> response for the attempt that succeeded
    total_ms: float  # first dispatch -> final outcome, including backoff
    attempts: int
    content: str | None = None
    reasoning_content: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    ratelimit: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    injected_delay_ms: float = 0.0  # synthetic stall added by routewise.faults (experiments only)
    streamed: bool = False
    ttft_ms: float | None = None  # dispatch -> first content/reasoning token (streaming only)
    decode_ms: float | None = None  # first token -> stream end (streaming only)


class TokenFactoryClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        *,
        max_attempts: int = 4,
        timeout_s: float = 120.0,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = api_key or os.environ.get("NEBIUS_API_KEY")
        if not key:
            raise MissingCredentials(
                "NEBIUS_API_KEY is not set. Create a key at https://tokenfactory.nebius.com "
                "and export it, or put NEBIUS_API_KEY=... in the repo's .env file."
            )
        self._key = key
        self._base_url = base_url.rstrip("/") + "/"
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._http = http_client or httpx.Client(timeout=timeout_s)
        self._openai = OpenAI(
            api_key=key,
            base_url=self._base_url,
            max_retries=0,
            timeout=timeout_s,
            http_client=self._http,
        )

    def list_models_verbose(self) -> list[dict[str, Any]]:
        resp = self._http.get(
            self._base_url + "models",
            params={"verbose": "true"},
            headers={"Authorization": f"Bearer {self._key}"},
        )
        resp.raise_for_status()
        return resp.json()["data"]

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
        seed: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> ChatResult:
        """One chat completion. With `stream=True`, also records time-to-first-token and decode time."""
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if seed is not None:
            params["seed"] = seed
        if response_format is not None:
            params["response_format"] = response_format
        if extra_body:
            params["extra_body"] = extra_body
        if stream:
            params["stream"] = True
            params["stream_options"] = {"include_usage": True}

        started_at = datetime.now(UTC).isoformat()
        t_start = time.perf_counter()
        attempts = 0
        last_error = "no attempt made"
        while attempts < self._max_attempts:
            attempts += 1
            t_dispatch = time.perf_counter()
            try:
                raw = self._openai.chat.completions.with_raw_response.create(**params)
            except _RETRYABLE as exc:
                last_error = _describe(exc)
                if attempts < self._max_attempts:
                    self._sleep(_backoff_s(exc, attempts))
                continue
            except openai.APIStatusError as exc:
                return ChatResult(
                    model_id=model,
                    started_at=started_at,
                    latency_ms=None,
                    total_ms=_ms_since(t_start),
                    attempts=attempts,
                    error=_describe(exc),
                )
            if stream:
                result = _consume_stream(model, started_at, raw.parse(), raw.headers, t_dispatch)
            else:
                result = _to_result(model, started_at, raw.parse(), raw.headers)
            latency_ms = _ms_since(t_dispatch)
            result.latency_ms = latency_ms
            result.total_ms = _ms_since(t_start)
            result.attempts = attempts
            return result

        return ChatResult(
            model_id=model,
            started_at=started_at,
            latency_ms=None,
            total_ms=_ms_since(t_start),
            attempts=attempts,
            error=f"gave up after {attempts} attempts: {last_error}",
        )


def _to_result(model: str, started_at: str, completion: Any, headers: httpx.Headers) -> ChatResult:
    choice = completion.choices[0] if completion.choices else None
    message = choice.message if choice else None
    extra = (message.model_extra or {}) if message else {}
    usage = completion.usage
    completion_details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    return ChatResult(
        model_id=model,
        started_at=started_at,
        latency_ms=None,
        total_ms=0.0,
        attempts=0,
        content=message.content if message else None,
        reasoning_content=extra.get("reasoning_content") or extra.get("reasoning"),
        finish_reason=choice.finish_reason if choice else None,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", None),
        cached_tokens=getattr(prompt_details, "cached_tokens", None),
        ratelimit={h: headers[h] for h in RATELIMIT_HEADERS if h in headers},
        error=None if choice else "response contained no choices",
    )


def _consume_stream(model: str, started_at: str, stream: Any, headers: httpx.Headers, t_dispatch: float) -> ChatResult:
    """Drain a streamed completion. Errors mid-stream are recorded, not retried (tokens may be billed)."""
    content, reasoning = [], []
    finish_reason = None
    usage = None
    t_first = None
    error = None
    try:
        for chunk in stream:
            if chunk.usage is not None:
                usage = chunk.usage
            for choice in chunk.choices:
                delta = choice.delta
                extra = (delta.model_extra or {}) if delta else {}
                piece = (delta.content if delta else None) or ""
                rpiece = extra.get("reasoning_content") or extra.get("reasoning") or ""
                if (piece or rpiece) and t_first is None:
                    t_first = time.perf_counter()
                content.append(piece)
                reasoning.append(rpiece)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
    except (openai.APIError, httpx.HTTPError) as exc:
        error = f"stream interrupted: {_describe(exc)}"
    t_end = time.perf_counter()
    completion_details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    return ChatResult(
        model_id=model,
        started_at=started_at,
        latency_ms=None,
        total_ms=0.0,
        attempts=0,
        content="".join(content) or None,
        reasoning_content="".join(reasoning) or None,
        finish_reason=finish_reason,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", None),
        cached_tokens=getattr(prompt_details, "cached_tokens", None),
        ratelimit={h: headers[h] for h in RATELIMIT_HEADERS if h in headers},
        error=error,
        streamed=True,
        ttft_ms=round((t_first - t_dispatch) * 1000, 1) if t_first is not None else None,
        decode_ms=round((t_end - t_first) * 1000, 1) if t_first is not None else None,
    )


def _backoff_s(exc: Exception, attempt: int) -> float:
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_S)
        except ValueError:
            pass
    return min(2.0**attempt, _MAX_BACKOFF_S)


def _describe(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    prefix = f"HTTP {status} " if status else ""
    return f"{prefix}{type(exc).__name__}: {exc}"[:500]


def _ms_since(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
