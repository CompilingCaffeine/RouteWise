"""Typed records shared by the dataset, benchmark, router and dashboard."""

from __future__ import annotations

import json
import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints, ValidationError

Task = Literal["extraction", "classification"]
TASKS: tuple[Task, ...] = ("extraction", "classification")

Currency = Literal["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"]


def _real_date(value: str) -> str:
    date.fromisoformat(value)  # rejects impossible dates such as 2026-02-30
    return value


IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(_real_date)]


class TicketCategory(StrEnum):
    REFUND_REQUEST = "refund_request"
    SHIPPING_DELAY = "shipping_delay"
    DAMAGED_ITEM = "damaged_item"
    BILLING_ERROR = "billing_error"
    ACCOUNT_ACCESS = "account_access"
    CANCELLATION = "cancellation"
    OTHER = "other"


class ExtractionOutput(BaseModel):
    """Every key is required; absent values must be an explicit null."""

    model_config = ConfigDict(extra="forbid")

    refund_requested: bool
    refund_amount: float | None
    currency: Currency | None
    order_id: str | None
    contact_date: IsoDate | None
    refund_date: IsoDate | None


class ClassificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: TicketCategory


OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "extraction": ExtractionOutput,
    "classification": ClassificationOutput,
}


def output_json_schema(task: Task) -> dict[str, Any]:
    return OUTPUT_MODELS[task].model_json_schema()


class Example(BaseModel):
    """One labelled workload item. `expected` is ground truth for the task's output model."""

    id: str
    task: Task
    difficulty: Literal["easy", "adversarial"]
    tags: list[str] = []
    input: str
    expected: dict[str, Any]


class CallRecord(BaseModel):
    """One model call, exactly as measured. The unit of every benchmark and trace."""

    call_id: str
    example_id: str
    task: Task
    candidate: str
    model_id: str
    reasoning: str
    prompt_version: str
    prompt_sha256: str
    response_format: str
    started_at: str
    latency_ms: float | None
    total_ms: float
    attempts: int
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    cached_tokens: int | None
    cost_usd: float | None
    finish_reason: str | None
    content: str | None
    reasoning_chars: int | None
    parsed: dict[str, Any] | None
    parse_error: str | None
    schema_error: str | None
    error: str | None
    ratelimit: dict[str, str] = {}
    score: dict[str, Any] | None = None
    injected_delay_ms: float = 0.0  # synthetic stall included in latency_ms (fault-injection runs only)

    def parsed_output(self) -> ParsedOutput:
        if self.error is not None and self.parsed is None:
            return ParsedOutput(data=None, parse_error=f"call failed: {self.error}")
        return ParsedOutput(data=self.parsed, parse_error=self.parse_error, schema_error=self.schema_error)


class ParsedOutput(BaseModel):
    """Result of reading a model response. `data` is the raw JSON object (if any);
    `schema_error` is set when it does not satisfy the task's output model."""

    data: dict[str, Any] | None
    parse_error: str | None = None
    schema_error: str | None = None

    @property
    def schema_valid(self) -> bool:
        return self.data is not None and self.schema_error is None


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_output(task: Task, text: str | None) -> ParsedOutput:
    """Parse a response into a JSON object, tolerating only a markdown code fence.

    The same leniency applies to every model, so it does not bias comparisons."""
    if not text or not text.strip():
        return ParsedOutput(data=None, parse_error="empty response")
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return ParsedOutput(data=None, parse_error=f"invalid JSON: {exc.msg}")
    if not isinstance(data, dict):
        return ParsedOutput(data=None, parse_error="JSON is not an object")
    try:
        OUTPUT_MODELS[task].model_validate(data)
    except ValidationError as exc:
        return ParsedOutput(data=data, schema_error=_summarize(exc))
    return ParsedOutput(data=data)


def _summarize(exc: ValidationError) -> str:
    parts = [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()]
    return "; ".join(parts)
