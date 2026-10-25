"""Price snapshots taken from Token Factory's /v1/models?verbose=true. Never hand-typed."""

from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from routewise.client import TokenFactoryClient
from routewise.cost import Price

_KEPT_FIELDS = (
    "name",
    "context_length",
    "quantization",
    "pricing",
    "status",
    "supported_features",
    "supported_sampling_parameters",
    "regions",
)


class PriceNotFound(KeyError):
    pass


def build_snapshot(models: list[dict[str, Any]], base_url: str) -> dict[str, Any]:
    return {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": f"{base_url.rstrip('/')}/models?verbose=true",
        "unit": "USD per token",
        "models": {m["id"]: {k: m.get(k) for k in _KEPT_FIELDS} for m in sorted(models, key=lambda m: m["id"])},
    }


def fetch_snapshot(client: TokenFactoryClient, base_url: str, path: Path) -> dict[str, Any]:
    snapshot = build_snapshot(client.list_models_verbose(), base_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n")
    return snapshot


def load_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `routewise prices` first")
    return json.loads(path.read_text())


def price_for(snapshot: dict[str, Any], model_id: str) -> Price:
    models = snapshot["models"]
    if model_id not in models:
        close = difflib.get_close_matches(model_id, list(models), n=3, cutoff=0.4)
        hint = f"; closest ids: {close}" if close else ""
        raise PriceNotFound(f"{model_id!r} is not in the price snapshot{hint}")
    pricing = models[model_id].get("pricing") or {}
    cache_read = pricing.get("input_cache_read")
    return Price(
        model_id=model_id,
        prompt=float(pricing.get("prompt", 0)),
        completion=float(pricing.get("completion", 0)),
        input_cache_read=float(cache_read) if cache_read not in (None, "") else None,
        request=float(pricing.get("request", 0) or 0),
    )


def nvidia_models(snapshot: dict[str, Any]) -> list[str]:
    return [mid for mid in snapshot["models"] if "nvidia" in mid.lower() or "nemotron" in mid.lower()]
