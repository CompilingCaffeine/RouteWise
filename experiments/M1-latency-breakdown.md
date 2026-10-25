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
