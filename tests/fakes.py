"""A fake Token Factory endpoint for tests (no network, no credits)."""

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


def completion_body(content: str, prompt_tokens: int = 500, completion_tokens: int = 60, model: str = "m") -> dict:
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
        },
    }


def transport(chat: Callable[[dict], httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": MODELS})
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
