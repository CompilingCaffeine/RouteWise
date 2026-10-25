# M2-lite: How do latency and throughput change with concurrency?

Pre-registered 2026-10-25, before any load run.

## Question

How do Nemotron 3 Nano's (primary) and Super's (escalation/hedge target) tail latency and throughput change as concurrency rises? Does the hedge target stay fast in the range where the primary degrades?

## Method

- **Load generator:** closed loop with a bounded worker pool. Each worker sends its next request when the previous one finishes. Concurrency c ∈ {1, 4, 8, 16}, 48 requests per level.
- **Requests:** held-out extraction inputs cycled in a seeded order, with the production extraction prompt (v2), JSON-schema output, reasoning off and temperature 0.
- **Timing:** streaming, so TTFT is captured alongside end-to-end latency. Each request records scheduled, dispatched, first-token and completed timestamps, plus the attempt count (429 retries) and `x-ratelimit-over-limit`.
- **Order:** levels run in increasing order, Nano first and then Super, with a 65 s cool-down between levels so each level starts in a fresh rate-limit window. Nano's observed limit is 100 RPM; Super's is 300 RPM.
- **Raw results:** `runs/load-*/calls.jsonl`. Summary per model × concurrency:
  - throughput (completed requests/s, output tokens/s)
  - p50/p95/p99 end-to-end and TTFT
  - retried requests, over-limit responses

## Hypotheses

- **H2a.** Throughput rises with concurrency for both models, sub-linearly at c = 16.
- **H2b.** p95 end-to-end latency rises with concurrency.
- **H2c.** At c = 16, Nano shows rate-limit pressure (429 retries or over-limit responses) before Super does.

## Scope

This is a measurement sweep only. No load-aware routing policy is built or tuned here; that stays future work.
