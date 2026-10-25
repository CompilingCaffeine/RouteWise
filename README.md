# RouteWise

**Workload-specific, quality-constrained model routing on Nebius Token Factory with NVIDIA Nemotron models.**

RouteWise benchmarks several NVIDIA open models on *your* labelled workload. It then routes each request to the cheapest model that meets the quality and latency targets you declared. Every answer is checked by a deterministic validator, and a failed answer escalates once to a stronger model. The hypothesis it tests on held-out data:

> For a structured information-extraction workload, RouteWise reduces total inference cost while meeting a stated quality target.

> **Status:** in active development for the Nebius × NVIDIA Global AI Hackathon. Measured results will be published here once the held-out evaluation has run. Nothing in this README is a claimed result yet.

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
routewise evaluate                     # 3 strategies × held-out split, live → runs/eval-*/summary.json
routewise route --example ext-025      # follow one request through validation/escalation
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
