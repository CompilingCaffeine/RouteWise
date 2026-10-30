"""Price snapshots taken from the provider's model listing. Never hand-typed.

Nebius Token Factory: /v1/models?verbose=true, one price per model.
OpenRouter: /api/v1/models, plus /api/v1/models/{id}/endpoints for each configured model, because every
upstream host bills its own price and RouteWise pins one host per candidate (config `host`).
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from routewise.client import ChatClient, provider_for_url
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
_OPENROUTER_MODEL_FIELDS = ("name", "canonical_slug", "context_length", "pricing", "supported_parameters")
_OPENROUTER_ENDPOINT_FIELDS = (
    "provider_name",
    "quantization",
    "context_length",
    "max_completion_tokens",
    "pricing",
    "supported_parameters",
    "status",
    "uptime_last_30m",
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


def build_openrouter_snapshot(
    models: list[dict[str, Any]], endpoints: dict[str, list[dict[str, Any]]], base_url: str
) -> dict[str, Any]:
    """NVIDIA models from the catalogue, with every host's endpoint for the models in `endpoints`."""
    base = base_url.rstrip("/")
    kept = {m["id"]: {k: m.get(k) for k in _OPENROUTER_MODEL_FIELDS}
            for m in models if m["id"] in endpoints or _is_nvidia(m["id"])}
    for model_id, eps in endpoints.items():
        kept.setdefault(model_id, {})["endpoints"] = {
            e["tag"]: {k: e.get(k) for k in _OPENROUTER_ENDPOINT_FIELDS} for e in sorted(eps, key=lambda e: e["tag"])
        }
    return {
        "fetched_at": datetime.now(UTC).isoformat(),
        "provider": "openrouter",
        "source": f"{base}/models",
        "endpoints_source": f"{base}/models/{{model_id}}/endpoints",
        "scope": "NVIDIA models; per-host endpoints for the configured candidates. A pinned host bills its endpoint price.",
        "unit": "USD per token",
        "models": dict(sorted(kept.items())),
    }


def fetch_snapshot(client: ChatClient, base_url: str, path: Path, model_ids: Iterable[str] = ()) -> dict[str, Any]:
    if provider_for_url(base_url) == "openrouter":
        snapshot = build_openrouter_snapshot(
            client.list_models_verbose(), {m: client.list_endpoints(m) for m in dict.fromkeys(model_ids)}, base_url)
    else:
        snapshot = build_snapshot(client.list_models_verbose(), base_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n")
    return snapshot


def load_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `routewise prices` first")
    return json.loads(path.read_text())


def price_for(snapshot: dict[str, Any], model_id: str, host: str | None = None) -> Price:
    """The billed price: per model on Nebius, per pinned host (endpoint slug) on OpenRouter."""
    models = snapshot["models"]
    if model_id not in models:
        close = difflib.get_close_matches(model_id, list(models), n=3, cutoff=0.4)
        hint = f"; closest ids: {close}" if close else ""
        raise PriceNotFound(f"{model_id!r} is not in the price snapshot{hint}")
    if snapshot.get("provider") != "openrouter":
        if host is not None:
            raise PriceNotFound(f"{model_id!r}: host pinning applies to OpenRouter snapshots only")
        return to_price(model_id, models[model_id].get("pricing") or {})
    hosts = models[model_id].get("endpoints") or {}
    if host not in hosts:
        raise PriceNotFound(f"{model_id!r} has no price for host {host!r} in the snapshot (hosts: {sorted(hosts)}). "
                            "Each OpenRouter host bills its own price: pin a listed host or re-run `routewise prices`")
    return to_price(model_id, hosts[host].get("pricing") or {})


def to_price(model_id: str, pricing: dict[str, Any]) -> Price:
    """Map a pricing block to Price. Both providers publish USD-per-token strings under OpenAI-style names
    (prompt, completion, input_cache_read, request); a missing, null or empty cache price means none is published.
    OpenRouter marks dynamically priced routers with -1, which has no fixed per-token price."""
    usd = {k: None if pricing.get(k) in (None, "") else float(pricing[k])
           for k in ("prompt", "completion", "input_cache_read", "request")}
    if any(v is not None and v < 0 for v in usd.values()):
        raise PriceNotFound(f"{model_id!r} has no fixed per-token price: {pricing}")
    return Price(
        model_id=model_id,
        prompt=usd["prompt"] or 0.0,
        completion=usd["completion"] or 0.0,
        input_cache_read=usd["input_cache_read"],
        request=usd["request"] or 0.0,
    )


def host_problems(snapshot: dict[str, Any], model_id: str, host: str, required: Iterable[str]) -> list[str]:
    """Why a pinned OpenRouter host cannot serve the configured requests (empty when it can)."""
    endpoint = ((snapshot["models"].get(model_id) or {}).get("endpoints") or {}).get(host)
    if endpoint is None:
        return [f"{host} does not serve {model_id}"]
    supported = set(endpoint.get("supported_parameters") or [])
    return [f"{host} lacks {p}" for p in required if p not in supported]


def nvidia_models(snapshot: dict[str, Any]) -> list[str]:
    return [mid for mid in snapshot["models"] if _is_nvidia(mid)]


def _is_nvidia(model_id: str) -> bool:
    return "nvidia" in model_id.lower() or "nemotron" in model_id.lower()
