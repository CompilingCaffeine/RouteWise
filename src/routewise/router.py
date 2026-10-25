"""Applies a routing option to one request: call, validate, escalate at most once, trace everything."""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel

from routewise.client import TokenFactoryClient
from routewise.config import Candidate, Config
from routewise.cost import Price
from routewise.evaluate import score
from routewise.execution import call_candidate
from routewise.policy import RouteOption
from routewise.prompts import PromptTemplate
from routewise.schema import CallRecord, Task
from routewise.validate import validate


class Trace(BaseModel):
    request_id: str
    strategy: str
    task: Task
    example_id: str | None
    policy_id: str | None
    policy_status: str | None
    route: str
    input_chars: int
    model_chain: list[str]
    calls: list[CallRecord]
    validations: list[dict[str, Any]]
    escalated: bool
    escalation_reason: str | None
    final_output: dict[str, Any] | None
    final_valid: bool
    timeline_ms: dict[str, float]
    latency_ms: float
    cost_usd: float
    cost_complete: bool
    score: dict[str, Any] | None = None


class Router:
    def __init__(
        self,
        client: TokenFactoryClient,
        config: Config,
        prices: dict[str, Price],
        prompts: dict[str, PromptTemplate],
        options: dict[str, RouteOption],
        *,
        strategy: str,
        policy: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._prices = prices
        self._prompts = prompts
        self._options = options
        self._candidates: dict[str, Candidate] = {c.label: c for c in config.candidates}
        self.strategy = strategy
        self.policy = policy

    def option(self, task: str) -> RouteOption:
        return self._options[task]

    def route(
        self,
        task: Task,
        text: str,
        *,
        example_id: str | None = None,
        expected: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> Trace:
        t0 = time.perf_counter()
        timeline: dict[str, float] = {"received": 0.0}
        option = self._options[task]
        chain = [option.primary] + ([option.escalate_to] if option.escalate_to else [])
        calls: list[CallRecord] = []
        validations = []
        escalation_reason = None

        for hop, label in enumerate(chain, start=1):
            timeline[f"dispatched_{hop}"] = _ms(t0)
            record = call_candidate(
                self._client, self._config, self._candidates[label], self._prompts[task], self._prices[label],
                example_id=example_id or "adhoc", text=text,
            )
            timeline[f"model_complete_{hop}"] = _ms(t0)
            check = validate(task, text, record.parsed_output())
            timeline[f"validation_complete_{hop}"] = _ms(t0)
            calls.append(record)
            validations.append(check.to_dict())
            if check.passed or hop == len(chain):
                break
            escalation_reason = "validator failed: " + ", ".join(check.failed_rules)

        timeline["response_complete"] = _ms(t0)
        final = calls[-1]
        policy_status = None
        if self.policy is not None:
            policy_status = self.policy["tasks"][task]["status"]
        trace = Trace(
            request_id=request_id or uuid.uuid4().hex,
            strategy=self.strategy,
            task=task,
            example_id=example_id,
            policy_id=self.policy["policy_id"] if self.policy else None,
            policy_status=policy_status,
            route=option.label,
            input_chars=len(text),
            model_chain=[c.candidate for c in calls],
            calls=calls,
            validations=validations,
            escalated=len(calls) > 1,
            escalation_reason=escalation_reason,
            final_output=final.parsed,
            final_valid=validations[-1]["passed"],
            timeline_ms=timeline,
            latency_ms=timeline["response_complete"],
            cost_usd=sum(c.cost_usd or 0.0 for c in calls),
            cost_complete=all(c.cost_usd is not None for c in calls),
        )
        if expected is not None:
            trace.score = score(task, expected, final.parsed).to_dict()
        return trace


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
