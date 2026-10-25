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

## Results: `load-20261025T183316Z-43e65f`

The run sent 384 streamed requests, for $0.05.

| Model | c | Throughput (req/s) | TTFT p50 / p95 | End-to-end p50 / p95 / p99 | 429 retries / over-limit |
| --- | --- | --- | --- | --- | --- |
| Nano | 1 | 0.97 | 734 / 851 ms | 1019 / 1146 / 1196 ms | 0 / 0 |
| Nano | 4 | 3.44 | 796 / 851 ms | 1140 / 1278 / 1390 ms | 0 / 0 |
| Nano | 8 | 6.49 | 808 / 894 ms | 1236 / 1321 / 1363 ms | 0 / 0 |
| Nano | 16 | 10.42 | 923 / 1005 ms | 1468 / 1734 / 1770 ms | 0 / 0 |
| Super | 1 | 0.66 | 1152 / 1470 ms | 1381 / 1992 / 3939 ms | 0 / 0 |
| Super | 4 | 2.86 | 1040 / 1294 ms | 1347 / 1758 / 1818 ms | 0 / 0 |
| Super | 8 | 4.47 | 1028 / 2023 ms | 1457 / 3280 / 4992 ms | 0 / 0 |
| Super | 16 | 7.62 | 1143 / 1685 ms | 1593 / 2837 / 2891 ms | 0 / 0 |

**Hypotheses**

- **H2a, supported.** Throughput rises sub-linearly with concurrency. Nano reaches 10.7× at 16× concurrency, Super 11.5×.
- **H2b, supported for Nano and not for Super.** Nano's p95 rises steadily (1146 → 1734 ms). Super's p95 does not rise monotonically (1992 → 1758 → 3280 → 2837 ms): its tail spikes look like transient provider events, not load-driven queueing in this range.
- **H2c, not supported, and not really tested.** Neither model saw a 429 or an over-limit response. With 48 requests per level, no level crossed Nano's 100 RPM budget, so this design did not reach the throttling regime. Sustaining c = 16 for a full minute would.

**Observations, not pre-registered**

- **The hedge target has its own tail.** Here Super, RouteWise's escalation and hedge target, had the heavier tail (p99 up to 5.0 s), while Nano stayed within 1.8 s. In the primary held-out run it was the other way round. A hedged answer arrives at the hedge delay plus Super's own latency, so hedging bounds the tail only as well as the backup model's tail allows.
- **Prefix caching warmed up on Super.** Mean cached prompt tokens grew from 43 to 656 across levels as the production system prompt was cached, and cost per level fell from $0.013 to $0.006. Nano reported no cached tokens. Later levels therefore ran with a warmer cache, which confounds latency comparisons across levels.
