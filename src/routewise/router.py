"""Applies a routing option to one request: call, validate, escalate or hedge (at most 2 calls), trace everything."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from typing import Any
from collections.abc import Callable

from pydantic import BaseModel

from routewise.client import TokenFactoryClient
from routewise.config import Candidate, Config
from routewise.cost import Price
from routewise.evaluate import score
from routewise.execution import call_candidate
from routewise.policy import RouteOption
from routewise.prompts import PromptTemplate
from routewise.schema import CallRecord, Task
from routewise.validate import Validation, validate


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
    hedged: bool = False
    answered_by: str | None = None


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
        on_answer: Callable[[dict[str, Any]], None] | None = None,
    ) -> Trace:
        t0 = time.perf_counter()
        timeline: dict[str, float] = {"received": 0.0}
        option = self._options[task]
        results: dict[int, tuple[CallRecord, Validation]] = {}

        def hop(n: int, label: str) -> int:
            timeline[f"dispatched_{n}"] = _ms(t0)
            record = call_candidate(
                self._client, self._config, self._candidates[label], self._prompts[task], self._prices[label],
                example_id=example_id or "adhoc", text=text,
            )
            timeline[f"model_complete_{n}"] = _ms(t0)
            check = validate(task, text, record.parsed_output())
            timeline[f"validation_complete_{n}"] = _ms(t0)
            results[n] = (record, check)
            return n

        answer, escalated, hedged = 1, False, False
        published = False

        def publish() -> None:
            nonlocal published
            timeline.setdefault("response_complete", _ms(t0))
            if on_answer is not None and not published:
                record, check = results[answer]
                on_answer({'output': record.parsed, 'valid': check.passed, 'answered_by': record.candidate,
                           'latency_ms': timeline['response_complete']})
            published = True
        if option.escalate_to is None:
            hop(1, option.primary)
        elif option.hedge_after_ms is None:
            hop(1, option.primary)
            if not results[1][1].passed:
                escalated, answer = True, hop(2, option.escalate_to)
        else:
            # Leaving the pool waits for any unused call, so its cost is still recorded.
            with ThreadPoolExecutor(max_workers=2) as pool:
                primary = pool.submit(hop, 1, option.primary)
                if primary in wait([primary], timeout=option.hedge_after_ms / 1000).done:
                    if not results[1][1].passed:
                        escalated, answer = True, hop(2, option.escalate_to)
                else:
                    hedged = True
                    timeline["hedge_dispatched"] = _ms(t0)
                    backup = pool.submit(hop, 2, option.escalate_to)
                    first = next(as_completed([primary, backup])).result()
                    if results[first][1].passed:
                        answer = first
                    else:
                        wait([primary, backup])
                        answer = 1 if results[1][1].passed else 2
                        escalated = answer == 2
                    timeline["response_complete"] = _ms(t0)
                    publish()  # deliver the answer before waiting for the unused call's billing
        timeline.setdefault("response_complete", _ms(t0))
        publish()
        timeline['accounting_complete'] = _ms(t0)

        calls = [results[n][0] for n in sorted(results)]
        validations = [results[n][1].to_dict() for n in sorted(results)]
        final, final_check = results[answer]
        escalation_reason = ("validator failed: " + ", ".join(results[1][1].failed_rules)) if escalated else None
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
            escalated=escalated,
            escalation_reason=escalation_reason,
            hedged=hedged,
            answered_by=final.candidate,
            final_output=final.parsed,
            final_valid=final_check.passed,
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
