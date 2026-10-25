# M1: Where does the latency go? (TTFT vs. decode)

Pre-registered 2026-10-25, before any streaming measurement.

## Question

How much of Nemotron latency on Token Factory is time-to-first-token (network + provider queue + prefill) versus decode? When tail events occur, do they live in TTFT or in decode? The primary held-out run's 4–7 s Nano stalls motivate this.

## Method

- **Streaming:** each call uses `stream=True` with `stream_options.include_usage`. Timestamps are taken client-side with a monotonic clock.
  - **TTFT:** dispatch → first content or reasoning token.
  - **Decode:** first token → last token.
  - **TPOT:** decode ÷ (completion tokens − 1).
  - **End-to-end:** dispatch → stream end.
- **Matrix:** 3 models (Nano, Super, Ultra) × 3 input sizes × 2 output shapes × 8 repetitions = 144 calls.
  - **Input sizes:** short ≈ 400, medium ≈ 2,000 and long ≈ 8,000 prompt tokens. Sizes are built by prepending neutral earlier messages to a held-out extraction conversation.
  - **Output shapes:** `json`, the extraction answer (~70 tokens); `text`, a free-text summary capped at 400 tokens.
- **Execution:** concurrency 2, seeded shuffled order, reasoning off, temperature 0.
- **Fixed variables:** prompt template, models, region (`api.tokenfactory.nebius.com`), and client host.
- **Raw results:** `runs/latency-*/calls.jsonl`. Summary: p50/p95/p99 TTFT, TPOT and end-to-end per cell.

## Hypotheses

- **H1a.** TTFT grows with input length for every model (prefill).
- **H1b.** TPOT is roughly independent of input length. It differs by model but not necessarily in size order, because these are MoE models with different active-parameter counts.
- **H1c.** For the RouteWise extraction workload (~800 input, ~70 output tokens), TTFT accounts for most of the end-to-end time.
- **H1d (exploratory).** Any slow outliers (> 3 s) for Nano sit in TTFT, i.e. queueing or prefill, not decode.

## Limitations, declared in advance

- Client-side TTFT cannot separate provider queueing from prefill.
- One region, one day, one client host.
- Eight repetitions make p99 a near-maximum.

## Results: `latency-20261025T182959Z-f1a8c6`

The run made 144 streamed calls on `ext-043`, for $0.21. Cached tokens were 0 on every call, so the nonce worked. The "short" JSON cell is ~760 tokens because the production prompt alone is that long.

| Model | TTFT p50 short → long (JSON) | TPOT p50 (JSON / text) | TTFT share of end-to-end (JSON) |
| --- | --- | --- | --- |
| Nemotron 3 Nano | 741 → 990 ms | 3.6–4.3 / 3.3–3.9 ms | 68–76% |
| Nemotron 3 Super | 1099 → 1436 ms | 3.2–3.4 / 5.4–6.5 ms | 82–85% |
| Nemotron 3 Ultra | 1102 → 1695 ms | 1.8–1.9 / 3.0–3.1 ms | 86–92% |

**Hypotheses**

- **H1a, partly supported.** TTFT has a large fixed floor: about 0.74 s for Nano and 1.1 s for Super and Ultra. That floor covers the network, the provider's queue and short prefill, and it is flat from ~760 to ~1,800 prompt tokens. It grows only 0.25–0.6 s at ~6,300 tokens.
- **H1b, supported.** TPOT does not depend on input length. It is not in model-size order: Ultra decodes fastest per token.
- **H1c, supported.** For the extraction workload, TTFT is 68–92% of end-to-end latency. The output is only ~80 tokens, so decode is a small share.
- **H1d, untestable in this run.** Nano had no slow outliers (JSON TTFT max 1.06 s, decode max 0.57 s).

**Exploratory observation.** Super's two JSON tail events split between the two phases:
- one was TTFT-dominated (2.9 s TTFT);
- one was decode-dominated (2.9 s to decode 74 tokens, ~39 ms per token against a normal 3.3 ms).

Provider tail events are therefore not confined to queueing or prefill. This supports RouteWise hedging on *total elapsed time* rather than on TTFT alone.
