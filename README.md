# RouteWise

**Workload-specific, quality-constrained model routing on Nebius Token Factory with NVIDIA Nemotron models.**

RouteWise benchmarks several NVIDIA open models on *your* labelled workload. It then routes each request to the cheapest model that meets the quality and latency targets you declared. Every answer is checked by a deterministic validator, and a failed answer escalates once to a stronger model. The hypothesis it tests on held-out data:

> For a structured information-extraction workload, RouteWise reduces total inference cost while meeting a stated quality target.

Built for the Nebius × NVIDIA Global AI Hackathon (Best Apps and Agents track). All inference runs on **Nebius Token Factory** with **NVIDIA Nemotron 3 Nano, Super and Ultra**.

**Live demo:** https://nebius-routewise.streamlit.app/

The demo has four pages: the benchmark, the routing policy, a live router that calls Token Factory, and the held-out evaluation with every recorded trace.

## Results

All numbers below are measured on held-out data and committed with raw per-request traces in `runs/`. The quality and latency thresholds were frozen in git before any benchmark ran (`a7dc972`). The routing policy was frozen before any held-out example was seen (`4cd6cad`).

**Declared requirements.** Extraction: task accuracy ≥ 85%, field accuracy ≥ 95%, p95 ≤ 4 s. Classification: task accuracy ≥ 95%, p95 ≤ 3 s.

### Primary result (pre-registered): `eval-20261025T170223Z-7e4682`

| Strategy (62 held-out tasks) | Task acc. | Extraction acc. / field acc. | Extraction p95 | $ per 1k tasks | Meets all thresholds |
| --- | --- | --- | --- | --- | --- |
| Always Nemotron 3 Nano | 88.7% | 85.4% / 97.6% | 4434 ms | $0.050 | no (latency) |
| Always Nemotron 3 Ultra | 100% | 100% / 100% | 1319 ms | $0.769 | yes |
| **RouteWise** (Nano; validator-triggered escalation to Super) | **95.2%** | **95.1% / 99.2%** | **5010 ms** | **$0.068** | **no (latency)** |

**Verdict: hypothesis not supported as declared.**

- **Quality:** RouteWise met every accuracy target, escalating 6.5% of requests.
- **Cost:** 91.2% below always-Ultra. The paired bootstrap 95% CI on the cost difference is −$0.76 to −$0.64 per 1k tasks.
- **Latency:** it **missed the 4 s extraction p95 ceiling**. The cause was two ~3-second windows in which Nano's serverless endpoint answered in 4–7 s.
  - There were no retries, no rate limiting, and no contribution from escalation.
  - Always-Nano missed the same ceiling.
- **Accuracy vs Ultra:** RouteWise traded 4.8 points of overall task accuracy for that saving (95% CI −11.3 to 0 points).

### Evidence trail

Every held-out run is listed here. Nothing is omitted. The roles are recorded in `results/runs.yaml`.

| Run | Role | Strategy | Extraction acc. | Extraction p95 | Cost vs always-Ultra | Meets thresholds |
| --- | --- | --- | --- | --- | --- | --- |
| `…170223Z-7e4682` | Primary (pre-registered) | RouteWise | 95.1% | 5010 ms | −91.2% | no |
| `…171615Z-4ca4e4` | Follow-up (post-hoc) | RouteWise | 92.7% | 1653 ms | −91.8% | yes |
| `…171615Z-4ca4e4` | Follow-up (post-hoc) | RouteWise + hedging | 97.6% | 1769 ms | −90.6% | yes |
| `…174144Z-d1e7da` | Controlled fault injection | RouteWise | 95.1% | 4268 ms | −91.1% | no |
| `…174144Z-d1e7da` | Controlled fault injection | RouteWise + hedging | 92.7% | **2680 ms** | −89.4% | **yes** |

- **Hedged escalation** was added *after* the primary run. If Nano has not answered by its own tuning-split p95 (1337 ms), Super is started in parallel and the first valid answer wins. Both calls are billed, and a request never makes more than 2 calls.
- **Follow-up run:** no latency bursts occurred, so hedging never fired. The two RouteWise variants behaved identically there. Their 92.7% vs 97.6% gap is pure run-to-run variance; outputs are not deterministic even at temperature 0.
- **Fault-injection run:** a seeded 10% of extraction requests (4 of 41) had a synthetic 3–6 s stall added to every Nano call, identically for every strategy.
  - Hedging fired on exactly those 4 requests. Super answered each one correctly in 2.5–2.9 s instead of 4.0–5.2 s.
  - The cost of those requests rose because both calls are billed.
  - The synthetic stalls are included in the recorded latency and flagged on each call.

### What the validator buys

- **Precision:** across all held-out runs the deterministic validator never escalated a correct answer (0 false positives), and every error it caught was corrected by escalation.
- **Recall:** it caught 43–71% of the cheap model's wrong first answers, depending on the run.
- **Errors it misses:**
  - a date returned as an order ID
  - a relative date ("tomorrow") anchored to the wrong message when no "issue/process" verb is present
  - classification errors, where only the schema is checked

  These were found on held-out data and deliberately **not** patched, to avoid tuning on the test set.
- **Error analysis on the tuning split** found that Nano copied example values from the v1 prompt into absent fields (`ORD-48213`, `1250.0`). That led to prompt v2 and two new rules (`order_id_in_source`, `refund_date_matches_statement`).

### Limitations

- **Sample size:** 62 held-out tasks (41 extraction). The accuracy CIs are wide, and the p95 is roughly the third-slowest request.
- **Classification is too easy:** every model scored 100% on the classification tuning items, so they don't separate the models.
- **Synthetic labels:** most extraction items come from templates, with labels correct by construction. 10 items are hand-written.
- **Single-day measurements:** latency was measured on one day against shared serverless endpoints.
- **Total API spend** for every committed run: **$0.36**.

## Why not just use a generic router?

Generic routers pick models by popularity, task guesses or provider health. RouteWise optimizes for **one application's own labelled workload**:

- **Routing comes from your benchmark.** Policies are built from controlled measurements on your data.
- **Requirements are explicit.** You declare them up front, for example "≥ 95% task accuracy, p95 ≤ 2 s". The optimizer never relaxes them silently.
- **Escalation is semantic.** It happens when an answer fails deterministic semantic or structural checks, not only when a provider errors.
- **The claim is tested.** RouteWise reports whether its policy actually beats fixed-model baselines on **held-out** data, with every call, retry and escalation billed.

RouteWise does not claim a novel routing algorithm. The contribution is evidence-based, quality-constrained optimization for an application's real workload.

## How it works

```text
labelled workload ─► benchmark every candidate (tune split) ─► replay-based policy optimizer
                                                                  │  cheapest route meeting thresholds
incoming request (known task) ─► selected Nemotron model ─► deterministic validator
                                                                  ├─ pass ─► answer + trace
                                                                  └─ fail ─► escalate once ─► answer + trace
```

| Component | File |
| --- | --- |
| Token Factory client (timing, usage, 429/5xx retries) | `src/routewise/client.py` |
| The single cost function (input, cached-input, output, per-request) | `src/routewise/cost.py` |
| Live price snapshot from `/v1/models?verbose=true` | `src/routewise/prices.py` |
| Seeded workload generator + hand-labelled edge cases | `src/routewise/dataset.py`, `data/handwritten/` |
| Ground-truth scoring (field-level, format-normalized) | `src/routewise/evaluate.py` |
| Deterministic validator (never sees ground truth) | `src/routewise/validate.py`, `src/routewise/textfacts.py` |
| Benchmark runner | `src/routewise/benchmark.py` |
| Policy optimizer (simulates every option from recorded calls) | `src/routewise/policy.py` |
| Router (≤ 1 escalation, full trace) | `src/routewise/router.py` |
| Held-out evaluation vs. both baselines | `src/routewise/evaluation.py` |
| Dashboard | `app/` |

### Validation rules (extraction)

| Rule | Check |
| --- | --- |
| `schema` | JSON parses; types, currency enum and real ISO dates |
| `amount_in_source` | A non-null refund amount appears as a money value in the conversation |
| `amount_refund_context` | ...in a sentence that talks about a refund |
| `currency_consistent` | Amount and currency are both set or both null, and agree with the source's marker |
| `order_id_in_source` | A non-null order ID appears verbatim in the conversation and contains a digit |
| `dates_in_source` | Every non-null date is stated in, or derivable from, the conversation |
| `contact_date_valid` | `contact_date` is the date of a customer message |
| `refund_date_matches_statement` | When the conversation says when a refund is or was issued, `refund_date` is one of the dates in those sentences, with relative dates resolved from that message |

Known blind spot: a well-formed answer that the text supports but that is semantically wrong. An example is picking the customer's requested amount instead of the agent's counter-offer when both appear in refund sentences. The evaluation measures this as validator recall against ground truth.

## Workload

`data/v1` holds 120 labelled support conversations: 80 refund-extraction and 40 ticket-classification.

- **Generation:** examples come from seeded templates whose labels are computed from the same values rendered into the text, plus 10 hand-labelled edge cases. No LLM writes labels.
- **Adversarial cases:**
  - quoted price vs. agreed partial refund
  - counter-offers
  - shipping-fee-only refunds
  - several dates
  - relative dates across a multi-day conversation
  - system messages that pre-date the customer
  - currency symbols (`CA$`, `A$`, `¥`)
  - negated refund requests
- **Split:** 50/50 into `tune` (used to choose the policy) and `heldout` (used only for the final comparison), stratified by task, difficulty and source.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,dev]"
echo 'NEBIUS_API_KEY=...' > .env      # gitignored; or export NEBIUS_API_KEY
```

## Reproduce

```bash
routewise prices                       # snapshot live prices → config/prices.snapshot.json
routewise probe --reasoning both       # one example × every candidate; checks JSON mode, usage, reasoning toggle
routewise benchmark --split tune       # every candidate × every tuning example → runs/benchmark-*/
routewise policy                       # cheapest qualifying route per task → policies/policy-*.json (no API calls)
routewise evaluate                     # baselines + RouteWise (+ hedged variant) × held-out split, live → runs/eval-*/
routewise evaluate --inject-stalls nano:0.10:3000-6000   # controlled fault injection (seeded, identical per strategy)
routewise report --run runs/eval-…     # recompute a summary from recorded traces (no API calls)
routewise route --example ext-025      # follow one request through validation/escalation/hedging
streamlit run app/streamlit_app.py     # dashboard
```

Every run directory contains:

- a `manifest.json` with the git SHA, the dataset hash, prompt hashes, the price snapshot hash, the candidates and the thresholds
- raw JSONL records for every call or trace

The summary's total cost is asserted to equal the sum of per-trace costs.

## Tests

```bash
.venv/bin/python -m pytest        # no network, no credits; includes a fake-endpoint end-to-end run
routewise data --check            # committed dataset matches the seeded generator
```

## License

MIT. See [LICENSE](LICENSE).
