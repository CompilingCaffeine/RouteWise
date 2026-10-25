"""Runs one candidate on one input and returns a fully priced CallRecord.

Shared by the probe, benchmark and router so every path measures calls identically.
"""

from __future__ import annotations

import uuid

from routewise.client import TokenFactoryClient
from routewise.config import Candidate, Config
from routewise.cost import Price, call_cost_usd
from routewise.evaluate import score
from routewise.prompts import PromptTemplate, response_format
from routewise.schema import CallRecord, Example, parse_output


def call_candidate(
    client: TokenFactoryClient,
    config: Config,
    candidate: Candidate,
    prompt: PromptTemplate,
    price: Price,
    *,
    example_id: str,
    text: str,
) -> CallRecord:
    result = client.chat(
        model=candidate.model_id,
        messages=prompt.messages(text),
        max_tokens=candidate.max_tokens,
        temperature=config.temperature,
        seed=config.seed,
        response_format=response_format(prompt.task, config.response_format),
        extra_body=candidate.extra_body,
    )
    parsed = parse_output(prompt.task, result.content)
    has_usage = result.prompt_tokens is not None and result.completion_tokens is not None
    cost = (
        call_cost_usd(price, result.prompt_tokens, result.completion_tokens, result.cached_tokens or 0)
        if has_usage
        else None
    )
    return CallRecord(
        call_id=uuid.uuid4().hex,
        example_id=example_id,
        task=prompt.task,
        candidate=candidate.label,
        model_id=candidate.model_id,
        reasoning=candidate.reasoning,
        prompt_version=prompt.version,
        prompt_sha256=prompt.sha256,
        response_format=config.response_format,
        started_at=result.started_at,
        latency_ms=result.latency_ms,
        total_ms=result.total_ms,
        attempts=result.attempts,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        reasoning_tokens=result.reasoning_tokens,
        cached_tokens=result.cached_tokens,
        cost_usd=cost,
        finish_reason=result.finish_reason,
        content=result.content,
        reasoning_chars=len(result.reasoning_content) if result.reasoning_content else None,
        parsed=parsed.data,
        parse_error=parsed.parse_error,
        schema_error=parsed.schema_error,
        error=result.error,
        ratelimit=result.ratelimit,
        injected_delay_ms=result.injected_delay_ms,
    )


def call_and_score(
    client: TokenFactoryClient,
    config: Config,
    candidate: Candidate,
    prompt: PromptTemplate,
    price: Price,
    example: Example,
) -> CallRecord:
    record = call_candidate(client, config, candidate, prompt, price, example_id=example.id, text=example.input)
    record.score = score(example.task, example.expected, record.parsed).to_dict()
    return record
