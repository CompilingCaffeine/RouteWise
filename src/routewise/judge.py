"""LLM-judge validator: a Nemotron model reviews a candidate answer against the source text."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, Candidate, Config
from routewise.cost import Price, call_cost_usd
from routewise.schema import Task

JUDGE_PROMPTS_DIR = REPO_ROOT / "prompts"
JUDGE_PROMPT_FILES = {"extraction": "judge_extraction_v1.txt", "classification": "judge_classification_v1.txt"}


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["correct", "incorrect"]
    wrong_fields: list[str]


@dataclass(frozen=True)
class JudgePrompt:
    task: Task
    version: str
    sha256: str
    text: str

    def messages(self, source: str, answer: dict[str, Any]) -> list[dict[str, str]]:
        schema = json.dumps(JudgeVerdict.model_json_schema(), separators=(",", ":"))
        return [
            {"role": "system", "content": self.text.replace("{schema}", schema)},
            {"role": "user", "content": f"Conversation:\n{source}\n\nCandidate answer:\n{json.dumps(answer, ensure_ascii=False)}"},
        ]


def load_judge_prompts(directory: Path = JUDGE_PROMPTS_DIR) -> dict[str, JudgePrompt]:
    prompts = {}
    for task, name in JUDGE_PROMPT_FILES.items():
        data = (directory / name).read_bytes()
        prompts[task] = JudgePrompt(task=task, version=Path(name).stem, sha256=hashlib.sha256(data).hexdigest(),
                                    text=data.decode("utf-8"))
    return prompts


class JudgeRecord(BaseModel):
    """One judge call. `flagged` means "escalate"; a judge that fails to return a verdict flags too."""

    judge: str
    model_id: str
    sample_id: str
    example_id: str
    task: Task
    prompt_version: str
    prompt_sha256: str
    latency_ms: float | None
    total_ms: float
    attempts: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    content: str | None
    verdict: str | None
    wrong_fields: list[str]
    flagged: bool
    error: str | None


def run_judge(
    client: TokenFactoryClient,
    config: Config,
    judge: Candidate,
    prompt: JudgePrompt,
    price: Price,
    *,
    sample_id: str,
    example_id: str,
    source: str,
    answer: dict[str, Any],
) -> JudgeRecord:
    result = client.chat(
        model=judge.model_id,
        messages=prompt.messages(source, answer),
        max_tokens=judge.max_tokens,
        temperature=config.temperature,
        seed=config.seed,
        response_format={"type": "json_schema",
                         "json_schema": {"name": "judge_verdict", "schema": JudgeVerdict.model_json_schema(), "strict": True}},
        extra_body=judge.extra_body,
    )
    verdict, wrong, error = None, [], result.error
    if result.content:
        try:
            parsed = JudgeVerdict.model_validate_json(result.content)
            verdict, wrong = parsed.verdict, parsed.wrong_fields
        except ValueError as exc:
            error = error or f"unparseable verdict: {str(exc)[:200]}"
    has_usage = result.prompt_tokens is not None and result.completion_tokens is not None
    return JudgeRecord(
        judge=judge.label,
        model_id=judge.model_id,
        sample_id=sample_id,
        example_id=example_id,
        task=prompt.task,
        prompt_version=prompt.version,
        prompt_sha256=prompt.sha256,
        latency_ms=result.latency_ms,
        total_ms=result.total_ms,
        attempts=result.attempts,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=call_cost_usd(price, result.prompt_tokens, result.completion_tokens, result.cached_tokens or 0)
        if has_usage else None,
        content=result.content,
        verdict=verdict,
        wrong_fields=wrong,
        flagged=verdict != "correct",
        error=error,
    )
