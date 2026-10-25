"""Read-only access to committed run artifacts, for the dashboard and reports."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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

    @property
    def is_fault_injection(self) -> bool:
        return bool(self.manifest.get("injection"))

    @property
    def tag(self) -> str:
        tags = [t for t, on in (("smoke, --limit", not self.is_full), ("fault injection", self.is_fault_injection)) if on]
        return f"  ({', '.join(tags)})" if tags else ""


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
    """Newest full run without fault injection, else the newest run."""
    return next((r for r in runs if r.is_full and not r.is_fault_injection), runs[0] if runs else None)


def run_registry(root: Path | None = None) -> dict[str, Any]:
    """results/runs.yaml: which evaluation runs are cited, their roles, and the headline."""
    path = (root or artifacts_root()) / "results" / "runs.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def headline_eval(runs: list[RunInfo], root: Path | None = None) -> RunInfo | None:
    """The registry's headline run if present, else the preferred run."""
    wanted = run_registry(root).get("headline")
    return next((r for r in runs if r.id == wanted), None) or preferred(runs)


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
