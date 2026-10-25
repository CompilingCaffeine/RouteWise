"""Experiment identity and on-disk run artifacts (runs/<experiment_id>/...)."""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from routewise.config import REPO_ROOT

RUNS_DIR = REPO_ROOT / "runs"


def new_experiment_id(kind: str) -> str:
    return f"{kind}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_revision() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.stdout.strip()

    status = run("status", "--porcelain")
    return {"sha": run("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


class RunDir:
    def __init__(self, experiment_id: str, root: Path | None = None) -> None:
        self.experiment_id = experiment_id
        self.path = (root or RUNS_DIR) / experiment_id
        self.path.mkdir(parents=True, exist_ok=False)

    @classmethod
    def create(cls, kind: str, root: Path | None = None) -> RunDir:
        return cls(new_experiment_id(kind), root)

    def write_manifest(self, **fields: Any) -> Path:
        manifest = {
            "experiment_id": self.experiment_id,
            "created_at": datetime.now(UTC).isoformat(),
            "git": git_revision(),
            **fields,
        }
        out = self.path / "manifest.json"
        out.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
        return out

    def append(self, name: str, record: BaseModel | dict[str, Any]) -> None:
        line = record.model_dump_json() if isinstance(record, BaseModel) else json.dumps(record, default=str)
        with (self.path / name).open("a") as fh:
            fh.write(line + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
