# RouteWise

**Find the lowest-cost Nemotron configuration your workload evidence supports.**

RouteWise benchmarks several NVIDIA open models on *your* labelled workload. It then routes each request to the cheapest model that meets the quality and latency targets you declared. Every answer is checked by a deterministic validator, and a failed answer escalates once to a stronger model. The hypothesis it tests on held-out data:

> For a structured information-extraction workload, RouteWise reduces total inference cost while meeting a stated quality target.

Built for the Nebius × NVIDIA Global AI Hackathon (Best Apps and Agents track). All inference runs on **Nebius Token Factory** with **NVIDIA Nemotron 3 Nano, Super and Ultra**.

**Live demo:** https://nebius-routewise.streamlit.app/

**Start here:** the guided dashboard compares the adopted refund-extraction profile with matched alternatives,
shows a real recorded correction or review decision, and lets you try a fresh conversation on Nebius Token Factory.
Download the deployment decision with its requirements, profile, price snapshot and evidence hashes.
The **Live router** uses the adopted date-normalization profile; earlier M5 and v1 paths are explicitly labelled.
See the [one-minute walkthrough and integration recipe](docs/deployment.md).

**What the model adds:** a new [rules-only retrospective diagnostic](results/rules-only-report.md) on the same
312 representative synthetic conversations found 70.5% correct autonomous completions with rules versus
98.4% for the recorded adopted profile, with 29 versus zero wrong releases. This is a simple baseline on
previously exposed data, not independent validation. The [human pilot handoff](docs/validation-pilot-handoff.md)
is ready; independent conversations and labelers are still needed.

The dashboard includes the original benchmark and held-out evidence, live routing, experiments, and a **Workload optimizer** for the earlier M5 study. Import labelled extraction data, freeze a tuning choice, test it on held-out conversations, or explore recorded configurations with confidence bounds and monthly-cost projections. Both guided comparisons can choose a fixed model or report that none qualifies.

## Results

**Latest confirmation:** source-grounded Nano → Super achieved **98.2% task accuracy** (lower 95% bound **96.5%**), **2.44 s p95**, and **$0.103 / 1k tasks**—about **70% below the qualifying grounded-Super control**. The original v1 result remains below; see [M5 details](results/M6-report.md).

**Relative-date recovery (M6):** deterministic issue-date normalization was preregistered, selected on development data and adopted on fresh conversations.
- **Relative-date challenge set (367 conversations):** correct answers without review rose from **30.8% to 75.5%** (+44.7 points, 95% CI 39.5–49.6), and wrong answers released fell from 153 to **0**.
- **Representative workload (312 conversations):** 98.4% correct without review and 100% task accuracy, at $0.080 / 1k tasks, 23% below the M5 route.

See the [M6 report](results/M7-report.md).

**Next: independent workload validation (M7, prepared; no data yet).** Every result above uses synthetic, model-written conversations with agent-reviewed labels. M7 provides:
- a human-labeling template and import format;
- a three-arm comparison of the adopted profile, the M5 profile and grounded Super on identical conversations;
- a 50–100-conversation diagnostic pilot that proposes a preregistered confirmation size.

It needs real conversations and two independent human labelers plus an adjudicator; no model labels are accepted. See [docs/workload-validation.md](docs/workload-validation.md).

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

Every v1 held-out run is listed here. M5 is summarized below; the complete experiment registry is retained. The roles are recorded in `results/runs.yaml`.

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

### Follow-up experiments

Each experiment was pre-registered in [`experiments/`](experiments/), with its question, design, hypotheses and decision rule committed before it ran. Each has raw records in `runs/`, and all four appear on the dashboard's **Experiments** page. None of them changes the headline result.

- **M4 · Is validation worth it?** (`validators-20261025T182255Z-812767`). We compared validators on 186 held-out Nano answers, simulating each one end to end with Super escalations and billing every judge call.
  - **The deterministic validator** is precise and nearly free: 100% escalation precision, 0 false alarms, and +28% cost over no validation.
  - **A Nemotron 3 Super judge** adds recall, mostly on classification, where the rules only check the schema. It costs 3.5× more per task and adds about 0.8 s to *every* request, which pushes the extraction p95 over the 4 s ceiling.
  - **A Nano judge** flags 31–43% of correct answers.
  - **The pre-registered decision rule picks "no validation"**, because pooled Nano alone clears the lenient 85% bar. We report that as is, along with the rule's weaknesses.
- **M1 · Where does latency go?** (`latency-20261025T182959Z-f1a8c6`). We streamed 144 calls across 3 models × 3 prompt sizes × 2 output shapes, with prefix caching defeated by a per-call nonce.
  - **Time to first token has a fixed floor** of about 0.74 s for Nano and 1.1 s for Super and Ultra. It is flat up to ~1,800 prompt tokens and makes up 68–92% of an extraction call.
  - **Decode speed per token doesn't follow model size:** Ultra is fastest at ≈1.9 ms/token.
  - **Super's tail events appeared in both phases:** a slow first token in one case, a mid-stream decode stall in another. That supports hedging on total elapsed time.
- **M3 · Can a cheap model predict Nano's failures before calling it?** (`risk-20261025T200624Z-c74ab3`). We built a new workload, `data/v2`: 1,198 conversations in 16 scenario families. Nemotron 3 Super and Ultra wrote them from programmatic fact sheets, so the labels come from the facts. Every conversation passed a deterministic verifier, and a semantic check by two reasoning verifiers left an empty adjudication queue. We then trained a calibrated logistic regression on 20 pre-route text features and tested it on **four families it never saw**.
  - **It does not transfer.** Test AUC is 0.48, at chance; the Brier score is worse than a constant guess; and it adds +0.2 points of accuracy (CI 0.0 to +0.5). **The pre-registered decision is not to adopt it.**
  - **The cause is family leakage.** With random folds the same model scores AUC 0.80; grouped by family, 0.49. It learns shortcuts that flip on new families. For example, relative-date phrases mark easy training cases but are the core difficulty of an unseen test family.
  - **The validator already does the job.** On those unseen families it catches 87% of Nano's errors with 1.3% false alarms. The validator policy reaches 95.7% accuracy against an oracle ceiling of 99.0%, at 87% below always-Ultra's cost.
- **M2-lite · Latency under load** (`load-20261025T183316Z-43e65f`). We ran a closed-loop sweep at 1/4/8/16 concurrent requests for Nano and Super.
  - **Throughput scales sub-linearly:** Nano reaches 10.4 req/s at c = 16, with p95 ≤ 1.8 s.
  - **Super's tail was noisier than Nano's** in this run, so hedging is only as good as the backup model's own tail.
  - **No rate-limit pressure was reached** at 48 requests per level.

### Original-study limitations

- **Sample size:** 62 held-out tasks (41 extraction). The accuracy CIs are wide, and the p95 is roughly the third-slowest request.
- **Classification is too easy:** every model scored 100% on the classification tuning items, so they don't separate the models.
- **Synthetic labels:** most extraction items come from templates, with labels correct by construction. 10 items are hand-written.
- **Single-day measurements:** latency was measured on one day against shared serverless endpoints.
- **Total API spend:** $0.36 for the main experiment (benchmarks, policy, three held-out runs). The original experiments and v2 workload used approximately **$6.00** for 10,843 model calls. M5 used **$2.37** of known priced API usage; the complete follow-up cost ledger is retained in the [report appendix](results/M6-report.md). M6 used **$5.48**, of which $4.62 went to writing 824 and label-verifying 787 conversations ([M6 report](results/M7-report.md#accounting)).

### Fresh-data confirmation: M5 (`confirmation-20261025T212745Z-ac61b1`)

On **330 fresh held-out conversations**, with nine live arms across three time windows, the confidence-aware optimizer selects **source-grounded Nano → Super, without hedging**. Requirements: task accuracy ≥95% using a one-sided confidence bound, field accuracy ≥95%, and p95 ≤4 seconds using a bound on deadline misses. The predeclared grounding-plus-hedging hypothesis is **supported**.

| Configuration | Task acc. | Lower 95% | p95 | $ / 1k |
|---|---:|---:|---:|---:|
| Grounded Nano | 90.3% | 87.3% | 2.08 s | $0.076 |
| Grounded Nano → Super | 98.2% | 96.5% | 2.44 s | $0.103 |
| Grounded Nano → Super + hedge | 98.2% | 96.5% | 2.23 s | $0.133 |
| Grounded Super | 98.8% | 97.3% | 1.74 s | $0.339 |

- **Escalation earns its cost:** grounded Nano alone reaches 90.3% task accuracy; adding Super escalation reaches 98.2%.
- **Routing is the lower-cost qualifying choice:** $0.103 versus $0.339 per 1,000 tasks for grounded Super. The tradeoff is 98.2% versus 98.8% accuracy and 2.44 s versus 1.74 s p95; one-point non-inferiority was not established.
- **Hedging is optional at this deadline:** it lowers p95 from 2.44 s to 2.23 s but adds 28.8% cost. Both routes meet the four-second budget and have the same observed accuracy.

The four comparisons share clarified field rules, source constraints and metadata-derived contact dates. They isolate the value of escalation, hedging and a fixed stronger model. M5 uses explicit issue-date statements and arrival-only negatives. The data is synthetic and agent-reviewed, not human/customer validation. M5 added $2.37 of known priced API usage. All six selected-route errors were flagged relative-date failures; the family-cluster sensitivity interval is **94.2–100%**, so the result depends on this workload mix.

See the [full M5 report](results/M6-report.md), [protocol](experiments/M6-issue-date-confirmation.md), and **Workload optimizer** in the dashboard for all nine arms, qualification gates, paired intervals, per-family/window results and source evidence.

### Relative-date recovery: M6 (`datesconfirm-20261026T133902Z-ab36f4`)

M5's six remaining errors were relative refund dates. Both models returned null or the message date for "in four days". M6 compared two fixes:

- **Deterministic normalization.** An analyzer resolves the store's issue-date statement against that message's own timestamp. It ignores arrival dates and customer restatements, applies explicit corrections, and sends ambiguous timing to review: business days, ranges, "next Friday".
- **Reasoning fallback.** A reasoning-enabled model call is made when date validation fails.

**Choice and freeze.**
- **Development:** on 92 development conversations plus a replay of the exposed M5 data, both approaches reached the same accuracy. The reasoning fallback cost 1.5–3.5× more, or missed the 4 s p95 criterion with Nano.
- **Freeze:** normalization was frozen, with code hashes and a power-based sample size, before any confirmation conversation existed ([protocol](experiments/M7-relative-date-recovery.md)).

**Relative-date challenge set** (367 conversations; the 90 with deliberately ambiguous timing must go to review):

| Route | Correct, no review | Wrong answers released | Review (all necessary?) | $ / 1k | p95 |
|---|---:|---:|---|---:|---:|
| M5 route: grounded Nano → Super | 30.8% | 153 | 27.5% (none necessary) | $0.204 | 4.28 s |
| Grounded Super | 28.3% | 139 | 33.8% (none necessary) | $0.353 | 1.59 s |
| **Date normalization** | **75.5%** | **0** | 24.5% (all necessary) | **$0.077** | 3.31 s |

**Representative workload** (312 conversations, evaluated separately):

| Route | Correct, no review | Wrong released | Task acc. (lower 95%) | $ / 1k | Deadline-miss upper 95% |
|---|---:|---:|---:|---:|---:|
| M5 route | 97.4% | 0 | 99.4% (98.1%) | $0.104 | 5.7% |
| Grounded Super | 96.5% | 1 | 98.4% (96.7%) | $0.349 | 1.4% |
| **Date normalization** | **98.4%** | **0** | **100% (99.1%)** | **$0.080** | **4.9%** |

**Gates.** The primary criterion (lower bound above +10 points) and all four safety gates passed, so normalization is now the deployed extraction profile.
- **Latency.** The deadline gate passed narrowly (4.9% against 5%). Nano served more slowly today than in M5 (call p95 3.35 s versus 2.06 s), and the M5 route itself would have missed that gate.
- **Scope.** The data is synthetic and agent-reviewed, and the study author fixed the expression inventory. The one family with writer-chosen wording kept only 7 conversations, so wording the parser cannot read is still untested.

[Recommended next experiments](docs/next-experiments.md): independent workload labels ([prepared as M7](docs/workload-validation.md)), then multi-day latency validation.

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

## Use the measured extraction route

The original default CLI config/policies preserve v1; the dashboard's default live experience uses the adopted profile. The adopted M6 extraction profile uses prompt v4, source grounding, Nano → Super escalation without hedging, and deterministic issue-date normalization:

```bash
routewise --config config/grounded.yaml route --policy policies/deployment/m7-extraction.json --task extraction --text '[2026-04-01 09:00] Customer: Refund order web_12345 please, I paid $40.00.
[2026-04-02 10:00] Agent: Approved. The refund will be issued this Friday.'
```

Check `final_valid`; a failed final answer, or an issue date that needs review, goes to a person. The M5 profile without normalization remains at `policies/deployment/m6-extraction.json`. Both profiles are workload-specific; see the [M5](results/M6-report.md) and [M6](results/M7-report.md) reports for their limits. The dashboard's Workload optimizer offers the M5 configurations and supports importing a new labelled workload.

To test the adopted profile on your own conversations, follow [docs/workload-validation.md](docs/workload-validation.md). Customer data stays in two directories outside every git repository. The console shows aggregates only, and results leave only as reviewed aggregate exports. Labelers fill in [`data/validation-template/labeling-template.csv`](data/validation-template/README.md), and then:

```bash
routewise validation drill --output ~/routewise-private/drill   # rehearse the protections on synthetic data first
export ROUTEWISE_PRIVATE_INPUT=~/routewise-private/input ROUTEWISE_PRIVATE_OUTPUT=~/routewise-private/output
routewise validation import --name pilot               # reads input/pilot/labels/ and input/pilot/adjudication/
routewise validation run --name pilot                  # adopted (M6), previous (M5) and grounded Super, same conversations
routewise validation export --run valpilot-…           # stage an aggregate for review; --approve <sha256> publishes it
routewise validation propose --run valpilot-…          # confirmation design and sample size
```

## Reproduce

```bash
routewise prices                       # snapshot live prices → config/prices.snapshot.json
routewise probe --reasoning both       # one example × every candidate; checks JSON mode, usage, reasoning toggle
routewise benchmark --split tune       # every candidate × every tuning example → runs/benchmark-*/
routewise policy                       # cheapest qualifying route per task → policies/policy-*.json (no API calls)
routewise evaluate                     # baselines + RouteWise (+ hedged variant) × held-out split, live → runs/eval-*/
routewise evaluate --inject-stalls nano:0.10:3000-6000   # controlled fault injection (seeded, identical per strategy)
routewise confirmation report --study m6 --run runs/confirmation-20261025T212745Z-ac61b1
routewise recovery report --run runs/datesconfirm-20261026T133902Z-ab36f4   # M6 (study key m7)
routewise validation report --run valpilot-…   # M7 (study key m8): private mode, once you have run it on your data
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
