import json
import math

import httpx

from fakes import MODELS, sse_body, transport
from routewise import latency
from routewise.client import TokenFactoryClient
from routewise.config import load_config
from routewise.dataset import load_split
from routewise.prices import build_snapshot, price_for
from routewise.prompts import load_prompt

CONFIG = load_config()
EXAMPLES = [e for e in load_split("heldout") if e.task == "extraction"]


def streaming_client(seen):
    def chat(body):
        seen.append(body)
        prompt_chars = sum(len(m["content"]) for m in body["messages"])
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse_body(json.dumps({"ok": True}), prompt_tokens=prompt_chars // 4, completion_tokens=20))

    return TokenFactoryClient(api_key="t", http_client=httpx.Client(transport=transport(chat)))


def prices():
    snapshot = build_snapshot(MODELS, "https://fake/v1/")
    return {c.label: price_for(snapshot, c.model_id) for c in CONFIG.candidates}


def test_profile_covers_the_matrix_with_unique_uncacheable_prompts():
    seen, sunk = [], []
    prompt = load_prompt(CONFIG.prompts["extraction"], "extraction")
    records = latency.run_profile(streaming_client(seen), CONFIG, list(CONFIG.candidates), prices(), prompt, EXAMPLES[0],
                                  reps=1, seed=1, concurrency=2, rpm=0, sink=sunk.append)
    assert len(records) == len(sunk) == 3 * 3 * 2
    systems = [b["messages"][0]["content"] for b in seen]
    assert len(set(systems)) == len(systems), "every call must start with a unique nonce"
    assert all(b["stream"] for b in seen)
    sizes = {}
    for b in seen:
        n = sum(len(m["content"]) for m in b["messages"])
        # nearest target on a log scale; "short" json is ~660 tokens because the production prompt alone is that long
        size = min(latency.INPUT_SIZES, key=lambda k: abs(math.log(n / 4) - math.log(latency.INPUT_SIZES[k])))
        sizes.setdefault(size, []).append(n)
    assert set(sizes) == set(latency.INPUT_SIZES)
    rows = latency.summarize_profile(records)
    assert len(rows) == 18 and all(r["ttft_ms"]["p50"] is not None for r in rows)
    assert {r["output_shape"] for r in rows} == {"json", "text"}


def test_load_level_is_closed_loop_and_summarized():
    seen, sunk = [], []
    prompt = load_prompt(CONFIG.prompts["extraction"], "extraction")
    records = latency.run_load_level(streaming_client(seen), CONFIG, CONFIG.candidate("nano"), prices()["nano"], prompt,
                                     EXAMPLES, concurrency=4, n_requests=8, seed=1, sink=sunk.append)
    assert len(records) == 8 and all(r.concurrency == 4 and r.ttft_ms is not None for r in records)
    (row,) = latency.summarize_load(records)
    assert row["n"] == 8 and row["throughput_rps"] > 0 and row["errors"] == 0
