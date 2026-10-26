"""M7 candidate routes: deterministic issue-date normalization (D) and a reasoning fallback (R).

Both keep grounded prompt v4 and at most two model calls per request. One decision function
serves live routing and replay over recorded calls, so development replays and the
confirmation run apply identical logic. The control is the deployed Router, replayed here
only for development.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from routewise.client import TokenFactoryClient
from routewise.config import Candidate, Config
from routewise.cost import Price, call_cost_usd
from routewise.evaluate import score
from routewise.execution import call_candidate
from routewise.issue_dates import IssueDateAnalysis, analyze_issue_date
from routewise.prompts import PromptTemplate
from routewise.router import Trace
from routewise.schema import CallRecord, ParsedOutput, parse_output
from routewise.textfacts import analyze, parse_messages
from routewise.validate import Check, Validation, validate

DATE_RULES = ("dates_in_source", "refund_date_matches_statement")
CONTRACT_RULE = "issue_date_contract"
STATUSES = ("issue_date", "no_issue_date", "needs_review")
FALLBACK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["status", "refund_date", "evidence"],
    "properties": {"status": {"type": "string", "enum": list(STATUSES)},
                   "refund_date": {"anyOf": [{"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}, {"type": "null"}]},
                   "evidence": {"type": "string"}},
}
# Strategy name -> (kind, fallback model). Control/fixed arms are replayed here for development only.
STRATEGIES = {
    "control": ("control", None),
    "fixed_grounded_super": ("fixed", None),
    "deterministic": ("deterministic", None),
    "reasoning_nano": ("reasoning", "nano"),
    "reasoning_super": ("reasoning", "super"),
    "reasoning_ultra": ("reasoning", "ultra"),
}

Caller = Callable[[str], CallRecord]  # "nano" | "super" | "fallback"


def issue_date_check(refund_date: str | None, analysis: IssueDateAnalysis, text: str) -> Check:
    if analysis.status == "review":
        return Check(CONTRACT_RULE, False, analysis.reason)
    if analysis.status == "resolved":
        ok = refund_date == analysis.date
        return Check(CONTRACT_RULE, ok, "" if ok else f"refund_date={refund_date} but the store states {analysis.date}")
    if refund_date is None:
        return Check(CONTRACT_RULE, True, "no issue date stated")
    if refund_date in analysis.arrival_dates:
        return Check(CONTRACT_RULE, False, f"refund_date={refund_date} is an arrival date, not an issue date")
    if date.fromisoformat(refund_date) not in analyze(text).dates:
        return Check(CONTRACT_RULE, False, f"refund_date={refund_date} is not stated in the source")
    return Check(CONTRACT_RULE, True)


def contract_validation(text: str, parsed: ParsedOutput, analysis: IssueDateAnalysis,
                        override: Check | None = None) -> Validation:
    """The deployed rules, with the two refund-date rules replaced by the issue-date contract."""
    base = validate("extraction", text, parsed)
    if not parsed.schema_valid:
        return base
    checks = [c for c in base.checks if c.rule not in DATE_RULES]
    checks.append(override or issue_date_check(parsed.data.get("refund_date"), analysis, text))
    return Validation(checks=tuple(checks))


def normalize_issue_date(data: dict[str, Any], analysis: IssueDateAnalysis) -> tuple[dict[str, Any], str]:
    if analysis.status == "resolved":
        return {**data, "refund_date": analysis.date}, "kept" if data.get("refund_date") == analysis.date else "set"
    if analysis.status == "none" and data.get("refund_date") in analysis.arrival_dates:
        return {**data, "refund_date": None}, "arrival_removed"
    return data, "kept"


def store_messages(text: str) -> str:
    lines = []
    for i, msg in enumerate(parse_messages(text), start=1):
        if msg.speaker.lower() != "customer":
            lines.append(f"[{i}] {msg.when.date().isoformat()} ({msg.when:%A}) {msg.speaker}: {msg.text}")
    return "\n".join(lines) or "(none)"


def fallback_messages(prompt: PromptTemplate, text: str) -> list[dict[str, str]]:
    system = (prompt.text.replace("{store_messages}", store_messages(text))
              .replace("{schema}", json.dumps(FALLBACK_SCHEMA, separators=(",", ":"))))
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def parse_fallback(content: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if not content or not content.strip():
        return None, "empty response"
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return data if isinstance(data, dict) else None, "missing or unknown status"
    rd = data.get("refund_date")
    if rd is not None:
        try:
            date.fromisoformat(rd)
        except (TypeError, ValueError):
            return data, "refund_date is not an ISO date"
    if (data["status"] == "issue_date") != (rd is not None):
        return data, "status and refund_date disagree"
    return data, None


def call_fallback(client: TokenFactoryClient, config: Config, candidate: Candidate, prompt: PromptTemplate,
                  price: Price, *, example_id: str, text: str) -> CallRecord:
    result = client.chat(model=candidate.model_id, messages=fallback_messages(prompt, text),
                         max_tokens=candidate.max_tokens, temperature=config.temperature, seed=config.seed,
                         response_format={"type": "json_schema", "json_schema": {
                             "name": "issue_date", "schema": FALLBACK_SCHEMA, "strict": True}},
                         extra_body=candidate.extra_body)
    data, problem = parse_fallback(result.content) if not result.error else (None, None)
    has_usage = result.prompt_tokens is not None and result.completion_tokens is not None
    return CallRecord(
        call_id=uuid.uuid4().hex, example_id=example_id, task="extraction", candidate=candidate.label,
        model_id=candidate.model_id, reasoning=candidate.reasoning, prompt_version=prompt.version,
        prompt_sha256=prompt.sha256, response_format=config.response_format, started_at=result.started_at,
        latency_ms=result.latency_ms, total_ms=result.total_ms, attempts=result.attempts,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        reasoning_tokens=result.reasoning_tokens, cached_tokens=result.cached_tokens,
        cost_usd=call_cost_usd(price, result.prompt_tokens, result.completion_tokens, result.cached_tokens or 0)
        if has_usage else None,
        finish_reason=result.finish_reason, content=result.content,
        reasoning_chars=len(result.reasoning_content) if result.reasoning_content else None,
        parsed=data, parse_error=problem if data is None else None, schema_error=problem if data is not None else None,
        error=result.error, ratelimit=result.ratelimit,
    )


def _trusted(fallback: dict[str, Any], text: str) -> Check:
    """Release a reasoning answer the parser cannot check only if it quotes a store message and is plausible."""
    quote = " ".join((fallback.get("evidence") or "").split()).casefold()
    messages = parse_messages(text)
    store = [" ".join(m.text.split()).casefold() for m in messages if m.speaker.lower() != "customer"]
    if fallback["status"] != "issue_date" or len(quote) < 3 or not any(quote in s for s in store):
        return Check(CONTRACT_RULE, False, "reasoning answer lacks a verbatim store quote")
    when = date.fromisoformat(fallback["refund_date"])
    first, last = messages[0].when.date(), messages[-1].when.date()
    if not first - timedelta(days=60) <= when <= last + timedelta(days=90):
        return Check(CONTRACT_RULE, False, f"reasoning date {when} is implausibly far from the conversation")
    return Check(CONTRACT_RULE, True, "unparsed issue-date language; reasoning answer quotes the store")


@dataclass
class Decision:
    calls: list[CallRecord] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)
    final_output: dict[str, Any] | None = None
    released: bool = False
    answered_by: str | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    recovery: dict[str, Any] = field(default_factory=dict)


def decide(kind: str, text: str, call: Caller) -> Decision:
    """Apply one strategy: 'control', 'fixed', 'deterministic' or 'reasoning'."""
    d = Decision()
    analysis = analyze_issue_date(text)
    if kind in ("deterministic", "reasoning"):
        d.recovery = {"analysis": {k: v for k, v in analysis.to_dict().items() if k != "mentions"},
                      "issue_mentions": [m.expression for m in analysis.issue_mentions], "action": None}

    def attempt(label: str, normalize: bool) -> tuple[ParsedOutput, Validation]:
        record = call(label)
        d.calls.append(record)
        parsed = record.parsed_output()
        if kind in ("control", "fixed"):
            check = validate("extraction", text, parsed)
        else:
            if normalize and parsed.schema_valid:
                data, action = normalize_issue_date(parsed.data, analysis)
                d.recovery[f"normalization_{label}"] = action
                parsed = parse_output("extraction", json.dumps(data))
            check = contract_validation(text, parsed, analysis)
        d.validations.append(check.to_dict())
        d.final_output, d.answered_by = parsed.data, record.candidate
        return parsed, check

    if kind == "fixed":
        _, v = attempt("super", False)
        d.released = v.passed
        return d
    normalize = kind == "deterministic"
    first, v1 = attempt("nano", normalize)
    if v1.passed:
        d.released = True
        if kind != "control":
            d.recovery["action"] = "normalized" if d.recovery.get("normalization_nano") not in (None, "kept") else "none"
        return d
    if kind != "control" and analysis.status == "review" and (normalize or analysis.known_ambiguity):
        d.recovery["action"] = "review: " + analysis.reason
        return d
    failed = set(v1.failed_rules)
    if kind == "reasoning" and first.schema_valid and failed == {CONTRACT_RULE}:
        record = call("fallback")
        d.calls.append(record)
        fb = record.parsed if record.schema_error is None and record.parse_error is None and not record.error else None
        d.recovery["fallback"] = {"candidate": record.candidate, "answer": record.parsed,
                                  "problem": record.error or record.parse_error or record.schema_error}
        if fb is None or fb["status"] == "needs_review":
            d.validations.append({"passed": False, "failed_rules": ["fallback"], "checks": []})
            d.recovery["action"] = "review: fallback " + ("needs review" if fb else "unusable")
            return d
        data = {**first.data, "refund_date": fb["refund_date"]}
        parsed = ParsedOutput(data=data)
        override = _trusted(fb, text) if analysis.status == "review" else None
        v2 = contract_validation(text, parsed, analysis, override)
        d.validations.append(v2.to_dict())
        d.final_output, d.answered_by, d.released = data, record.candidate, v2.passed
        d.recovery["action"] = "fallback released" if v2.passed else "review: fallback disagrees with source"
        return d
    d.escalated = True
    d.escalation_reason = "validator failed: " + ", ".join(v1.failed_rules)
    _, v2 = attempt("super", normalize)
    d.released = v2.passed
    if kind != "control":
        d.recovery["action"] = "escalated" if v2.passed else "review: escalation failed validation"
    return d


class RecoveryRouter:
    """Live candidate route with the Router's trace format; the answer callback fires once, at the end."""

    def __init__(self, client: TokenFactoryClient, config: Config, prices: dict[str, Price], prompt: PromptTemplate,
                 fallback_prompt: PromptTemplate | None, *, strategy: str) -> None:
        self.kind, model = STRATEGIES[strategy]
        self.strategy = strategy
        self._client, self._config, self._prices, self._prompt = client, config, prices, prompt
        self._fallback_prompt = fallback_prompt
        self._fallback = config.with_reasoning(config.candidate(model), "on") if model else None

    def route(self, task: str, text: str, *, example_id: str | None = None, expected: dict[str, Any] | None = None,
              on_answer: Callable[[dict[str, Any]], None] | None = None) -> Trace:
        t0 = time.perf_counter()
        timeline: dict[str, float] = {"received": 0.0}
        n = [0]

        def call(label: str) -> CallRecord:
            n[0] += 1
            timeline[f"dispatched_{n[0]}"] = _ms(t0)
            if label == "fallback":
                record = call_fallback(self._client, self._config, self._fallback, self._fallback_prompt,
                                       self._prices[self._fallback.name], example_id=example_id or "adhoc", text=text)
            else:
                record = call_candidate(self._client, self._config, self._config.candidate(label), self._prompt,
                                        self._prices[label], example_id=example_id or "adhoc", text=text)
            timeline[f"model_complete_{n[0]}"] = _ms(t0)
            return record

        d = decide(self.kind, text, call)
        timeline["response_complete"] = _ms(t0)
        if on_answer is not None:
            on_answer({"output": d.final_output, "valid": d.released, "answered_by": d.answered_by,
                       "latency_ms": timeline["response_complete"]})
        timeline["accounting_complete"] = _ms(t0)
        trace = Trace(
            request_id=uuid.uuid4().hex, strategy=self.strategy, task="extraction", example_id=example_id,
            policy_id=None, policy_status=None, route=self.strategy, input_chars=len(text),
            model_chain=[c.candidate for c in d.calls], calls=d.calls, validations=d.validations,
            escalated=d.escalated, escalation_reason=d.escalation_reason, final_output=d.final_output,
            final_valid=d.released, timeline_ms=timeline, latency_ms=timeline["response_complete"],
            cost_usd=sum(c.cost_usd or 0.0 for c in d.calls), cost_complete=all(c.cost_usd is not None for c in d.calls),
            answered_by=d.answered_by, recovery=d.recovery,
        )
        if expected is not None:
            trace.score = score("extraction", expected, d.final_output).to_dict()
        return trace


def replay(strategy: str, text: str, index: dict[tuple[str, str], CallRecord], example_id: str) -> Decision:
    """Development replay over recorded calls; latency is the sum of sequential call durations."""
    kind, model = STRATEGIES[strategy]

    def call(label: str) -> CallRecord:
        key = (f"{model}+think", example_id) if label == "fallback" else (label, example_id)
        return index[key]

    return decide(kind, text, call)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
