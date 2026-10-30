"""A fake Token Factory / OpenRouter endpoint for tests (no network, no credits)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

MODELS = [
    {
        "id": model_id,
        "name": model_id,
        "context_length": 262144,
        "pricing": {"prompt": prompt, "completion": completion, "input_cache_read": None, "request": "0"},
        "supported_features": ["json_mode"],
    }
    for model_id, prompt, completion in [
        ("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "0.00000006", "0.00000024"),
        ("nvidia/nemotron-3-super-120b-a12b", "0.0000003", "0.0000009"),
        ("nvidia/Nemotron-3-Ultra-550b-a55b", "0.000001", "0.000003"),
    ]
]

_PARAMS = ["max_tokens", "temperature", "reasoning", "response_format", "structured_outputs", "seed"]
# OpenRouter: the model-level price is one host's; each host (endpoint tag) bills its own.
OPENROUTER_ENDPOINTS = {
    "nvidia/nemotron-3-nano-30b-a3b": [
        {"tag": "nebius/fp8", "provider_name": "Nebius", "quantization": "fp8", "supported_parameters": _PARAMS,
         "pricing": {"prompt": "0.00000006", "completion": "0.00000024", "input_cache_read": "0.000000012"}},
        {"tag": "novita/fp4", "provider_name": "Novita", "quantization": "fp4",
         "supported_parameters": [p for p in _PARAMS if p != "structured_outputs"],
         "pricing": {"prompt": "0.00000005", "completion": "0.0000002"}},
    ],
    "nvidia/nemotron-3-super-120b-a12b": [
        {"tag": "dekallm/fp8", "provider_name": "DekaLLM", "quantization": "fp8", "supported_parameters": _PARAMS,
         "pricing": {"prompt": "0.00000008", "completion": "0.00000045"}},
        {"tag": "crusoe/fp8", "provider_name": "Crusoe", "quantization": "fp8", "supported_parameters": _PARAMS,
         "pricing": {"prompt": "0.0000003", "completion": "0.0000024", "input_cache_read": "0.00000015"}},
    ],
    "nvidia/nemotron-3-ultra-550b-a55b": [
        {"tag": "deepinfra/fp4", "provider_name": "DeepInfra", "quantization": "fp4", "supported_parameters": _PARAMS,
         "pricing": {"prompt": "0.0000005", "completion": "0.0000022", "input_cache_read": "0.0000001"}},
    ],
}
OPENROUTER_MODELS = [
    {"id": model_id, "name": model_id, "context_length": 262144, "supported_parameters": _PARAMS,
     "pricing": {**eps[-1]["pricing"], "request": "0"}}
    for model_id, eps in OPENROUTER_ENDPOINTS.items()
] + [{"id": "nvidia/switchyard", "name": "router", "pricing": {"prompt": "-1", "completion": "-1"}},
     {"id": "other/model", "name": "not nvidia", "pricing": {"prompt": "0.000001", "completion": "0.000001"}}]


def completion_body(content: str, prompt_tokens: int = 500, completion_tokens: int = 60, model: str = "m",
                    **extra) -> dict:
    """`extra` adds OpenRouter-style fields: provider="DekaLLM" at the top level, cost=... inside usage."""
    cost = extra.pop("cost", None)
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 0},
            **({"cost": cost} if cost is not None else {}),
        },
        **extra,
    }


def openrouter_completion(content: str, body: dict, prompt_tokens: int = 500, completion_tokens: int = 60) -> dict:
    """A response from the host pinned in `body`, billed at that host's fake endpoint price."""
    host = body["provider"]["order"][0]
    endpoint = next(e for e in OPENROUTER_ENDPOINTS[body["model"]] if e["tag"] == host)
    price = endpoint["pricing"]
    cost = prompt_tokens * float(price["prompt"]) + completion_tokens * float(price["completion"])
    return completion_body(content, prompt_tokens, completion_tokens, model=body["model"],
                           provider=endpoint["provider_name"], cost=cost)


def transport(chat: Callable[[dict], httpx.Response], seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        openrouter = request.url.host == "openrouter.ai"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": OPENROUTER_MODELS if openrouter else MODELS})
        if request.url.path.endswith("/endpoints"):
            model_id = request.url.path.split("/models/", 1)[1].removesuffix("/endpoints")
            if model_id not in OPENROUTER_ENDPOINTS:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"data": {"id": model_id, "endpoints": OPENROUTER_ENDPOINTS[model_id]}})
        if request.url.path.endswith("/chat/completions"):
            return chat(json.loads(request.content))
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def sse_body(content: str, prompt_tokens: int = 500, completion_tokens: int = 60, model: str = "m", pieces: int = 3) -> bytes:
    """A streamed chat completion as server-sent events, ending with a usage-only chunk."""
    step = max(1, len(content) // pieces)
    parts = [content[i : i + step] for i in range(0, len(content), step)]
    events = []
    for i, part in enumerate(parts):
        events.append({
            "id": "c", "object": "chat.completion.chunk", "created": 0, "model": model,
            "choices": [{"index": 0, "delta": {"content": part},
                         "finish_reason": "stop" if i == len(parts) - 1 else None}],
        })
    events.append({
        "id": "c", "object": "chat.completion.chunk", "created": 0, "model": model, "choices": [],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens, "prompt_tokens_details": {"cached_tokens": 0}},
    })
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode() + b"data: [DONE]\n\n"
