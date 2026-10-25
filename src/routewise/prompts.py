"""Versioned prompt templates. The version is the file stem; the hash pins exact content."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from routewise.schema import Task, output_json_schema


@dataclass(frozen=True)
class PromptTemplate:
    task: Task
    version: str
    sha256: str
    text: str

    def messages(self, ticket: str) -> list[dict[str, str]]:
        schema = json.dumps(output_json_schema(self.task), separators=(",", ":"))
        return [
            {"role": "system", "content": self.text.replace("{schema}", schema)},
            {"role": "user", "content": ticket},
        ]


def load_prompt(path: Path, task: Task) -> PromptTemplate:
    data = path.read_bytes()
    return PromptTemplate(
        task=task,
        version=path.stem,
        sha256=hashlib.sha256(data).hexdigest(),
        text=data.decode("utf-8"),
    )


def response_format(task: Task, mode: str) -> dict[str, Any] | None:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": f"{task}_output", "schema": output_json_schema(task), "strict": True},
        }
    if mode == "json_object":
        return {"type": "json_object"}
    if mode == "none":
        return None
    raise ValueError(f"unknown response_format mode {mode!r}")
