"""Loads config/routewise.yaml into typed objects."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from routewise.client import PROVIDERS, provider_for_url
from routewise.schema import TASKS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "routewise.yaml"


@dataclass(frozen=True)
class Candidate:
    """A routable option: a model plus the request settings that change its cost/quality."""

    name: str
    model_id: str
    reasoning: str
    max_tokens: int
    extra_body: dict[str, Any]
    host: str | None = None  # OpenRouter endpoint slug that must serve every call, e.g. "nebius/fp8"

    @property
    def label(self) -> str:
        return self.name if self.reasoning == "off" else f"{self.name}+think"


@dataclass(frozen=True)
class Thresholds:
    min_task_accuracy: float
    min_field_accuracy: float | None = None
    max_p95_latency_ms: float | None = None


@dataclass(frozen=True)
class Config:
    path: Path
    base_url: str
    prices_snapshot: Path
    temperature: float
    seed: int | None
    response_format: str
    reasoning_modes: dict[str, dict[str, Any]]
    candidates: tuple[Candidate, ...]
    baselines: dict[str, str]
    thresholds: dict[str, Thresholds]
    prompts: dict[str, Path]
    hedge_after_percentile: float | None = None  # hedge escalation options after this primary-latency percentile
    provider: str = "nebius"

    def candidate(self, name: str) -> Candidate:
        for cand in self.candidates:
            if cand.name == name:
                return cand
        raise KeyError(f"unknown candidate {name!r}; configured: {[c.name for c in self.candidates]}")

    def with_reasoning(self, cand: Candidate, mode: str) -> Candidate:
        settings = self.reasoning_modes[mode]
        return replace(
            cand,
            reasoning=mode,
            max_tokens=int(settings["max_tokens"]),
            extra_body=request_body(settings, cand.host),
        )


def request_body(mode: dict[str, Any], host: str | None) -> dict[str, Any]:
    """A reasoning mode's extra_body, plus OpenRouter routing pinned to `host`.

    One host per candidate keeps latency and p95 comparable (no silent fallback to another host), and
    require_parameters admits only a host that honours json_schema, seed and temperature.
    """
    body = dict(mode.get("extra_body") or {})
    if host:
        body["provider"] = {"order": [host], "allow_fallbacks": False, "require_parameters": True}
    return body


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a gitignored .env without overriding the real environment."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    path = Path(path).resolve()
    root = path.parent.parent
    raw = yaml.safe_load(path.read_text())

    provider = raw.get("provider") or provider_for_url(raw["base_url"])
    if provider not in PROVIDERS or provider != provider_for_url(raw["base_url"]):
        raise ValueError(f"provider {provider!r} does not match base_url {raw['base_url']!r}")
    modes = {str(k): v for k, v in raw["reasoning_modes"].items()}
    candidates = []
    for c in raw["candidates"]:
        mode = str(c.get("reasoning", "off"))
        if mode not in modes:
            raise ValueError(f"candidate {c['name']!r} uses unknown reasoning mode {mode!r}")
        host = c.get("host")
        if provider == "openrouter" and not host:
            raise ValueError(f"candidate {c['name']!r} needs a pinned `host` on OpenRouter (an endpoint slug such as "
                             "nebius/fp8; see `routewise prices`)")
        if provider != "openrouter" and host:
            raise ValueError(f"candidate {c['name']!r}: `host` pinning applies to OpenRouter only")
        candidates.append(
            Candidate(
                name=c["name"],
                model_id=c["model_id"],
                reasoning=mode,
                max_tokens=int(modes[mode]["max_tokens"]),
                extra_body=request_body(modes[mode], host),
                host=host,
            )
        )

    thresholds = {task: Thresholds(**raw["thresholds"][task]) for task in raw["thresholds"]}
    unknown = set(thresholds) - set(TASKS)
    if unknown:
        raise ValueError(f"thresholds for unknown tasks: {sorted(unknown)}")

    gen = raw.get("generation", {})
    return Config(
        path=path,
        base_url=raw["base_url"],
        prices_snapshot=root / raw["prices_snapshot"],
        temperature=float(gen.get("temperature", 0.0)),
        seed=gen.get("seed"),
        response_format=gen.get("response_format", "json_schema"),
        reasoning_modes=modes,
        candidates=tuple(candidates),
        baselines=dict(raw["baselines"]),
        thresholds=thresholds,
        prompts={task: root / p for task, p in raw["prompts"].items()},
        hedge_after_percentile=(raw.get("hedging") or {}).get("after_primary_percentile")
        if (raw.get("hedging") or {}).get("enabled")
        else None,
        provider=provider,
    )
