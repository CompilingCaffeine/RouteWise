"""Read-only access to committed run artifacts, for the dashboard and reports."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from routewise.config import REPO_ROOT
from routewise.runlog import read_jsonl


def artifacts_root() -> Path:
    return Path(os.environ.get("ROUTEWISE_ARTIFACTS", REPO_ROOT))


@dataclass(frozen=True)
class RunInfo:
    path: Path
    manifest: dict[str, Any]

    @property
    def id(self) -> str:
        return self.manifest["experiment_id"]

    @property
    def is_full(self) -> bool:
        return not self.manifest.get("limit")


def list_runs(kind: str, root: Path | None = None) -> list[RunInfo]:
    """Runs of one kind ("benchmark", "eval", "probe"), newest first."""
    runs_dir = (root or artifacts_root()) / "runs"
    out = []
    for path in sorted(runs_dir.glob(f"{kind}-*"), reverse=True):
        manifest_path = path / "manifest.json"
        if manifest_path.exists():
            out.append(RunInfo(path=path, manifest=json.loads(manifest_path.read_text())))
    return out


def preferred(runs: list[RunInfo]) -> RunInfo | None:
    """Newest full (non --limit) run, else the newest run."""
    return next((r for r in runs if r.is_full), runs[0] if runs else None)


def list_policies(root: Path | None = None) -> list[Path]:
    return sorted(((root or artifacts_root()) / "policies").glob("policy-*.json"), reverse=True)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def load_records(run: RunInfo, name: str) -> list[dict[str, Any]]:
    path = run.path / name
    return read_jsonl(path) if path.exists() else []


def load_prices(root: Path | None = None) -> dict[str, Any] | None:
    path = (root or artifacts_root()) / "config" / "prices.snapshot.json"
    return load_json(path) if path.exists() else None
