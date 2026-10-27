# RouteWise: Devpost submission draft

**Track:** Best Apps and Agents

**Demo:** https://nebius-routewise.streamlit.app/

**Code:** https://github.com/CompilingCaffeine/RouteWise (MIT)

The committed app's Start here and Live router paths use the adopted date-normalization profile; historical M5 and v1 paths are labelled separately. The [updated demo video](video/routewise-devpost.mp4) is recorded (1:38.82, local app with real Nebius inference and computer-generated narration). **Before submission:** deploy this build, verify the hosted app, upload the video publicly to YouTube and insert its URL. The hosted app still showed the older navigation when checked October 26.

## RouteWise in one minute

**RouteWise makes AI inference cheaper while enforcing your reliability requirements.** Not every AI request needs your most powerful model. RouteWise benchmarks NVIDIA Nemotron models on Nebius Token Factory and routes requests to the least expensive configuration that meets your accuracy and latency requirements.

**Results first:** on the same 312-conversation representative dataset, the adopted profile used **77.1% less inference cost than grounded Super**, completed **98.4% correctly without review**, and released **zero observed wrong answers**. Recorded usage cost was **$0.080 vs $0.349 per 1,000 requests**. At one million requests/month, that projects to **$80 vs $349**, or about **$269 saved**, excluding hosting and human review.

**Independent human validation is complete for this dataset**, as confirmed by the project owner. The conversations remain synthetic; this is validation of the existing representative dataset, not a new customer-data pilot. [Validation scope](dataset-validation.md).

The workflow is choose requirements → compare measured configurations → try the selected profile on Nebius Token Factory → export a deployment decision. It can recommend a route, a fixed model, or no qualifying configuration. The four-second deadline gate passed in the original confirmation; the later noise study missed its latency gate.

A separate retrospective rules-only diagnostic completed 70.5% correctly without review and released 29 wrong answers on the same conversations. This simple baseline is not a claim about all deterministic systems. When measured mailbox writing noise was added in M7, the adopted profile's answers stayed unchanged, although the latency gate failed.

## Inspiration

A developer building refund extraction faces a concrete decision: which model can return the correct amount, order identifier and issue date within the application's latency and cost limits? Choosing a large model can overpay; choosing a small one can quietly return the wrong identifier or invent an amount.

We built RouteWise to make that decision measurable on an application's own labelled workload. The answer can be a fixed model, a validated route with escalation, or no qualifying configuration.

## What it does

1. **Bring a workload.** Import labelled refund-extraction conversations with separate tuning and held-out splits. Labels never reach the inference prompt.
2. **Declare requirements.** Set task/field accuracy and a p95 latency deadline before benchmarking.
3. **Measure alternatives.** Compare NVIDIA Nemotron Nano, Super and Ultra on Nebius Token Factory, including source-grounded controls and routing options. Every call records tokens, latency, usage-based cost and an answer trace.
4. **Ground fields in evidence.** Select complete order identifiers and numeric amounts from source candidates; derive the first customer date from message metadata. Preserve raw model answers and any normalization. An arrival date is not a refund issue date.
5. **Escalate and hedge selectively.** Deterministic failures escalate once. If the cheap model stalls, the backup can start after a fixed delay. An actual answer callback delivers the winner before final billing finishes; both calls are priced.
6. **Validate separately.** Freeze a configuration on tuning data, then test it on untouched held-out conversations against fixed models. Confidence bounds can reject an option whose point estimate looks good.

The Workload optimizer shows the lowest-cost qualifying configuration, every fixed-model comparison, monthly cost at a chosen volume, source evidence, downloadable results and live inference. It explicitly abstains when no configuration meets the requirements.

## Results

The original preregistered v1 experiment remains unchanged: on 62 held-out tasks, RouteWise achieved 95.2% overall accuracy at 91% lower cost than Ultra but missed its four-second extraction p95 deadline. We report that hypothesis as not supported.

M5 used **330 entirely fresh held-out conversations**, nine independent live arms and three randomized time windows, plus repeated sentinel conversations. Requirements were frozen at task accuracy ≥95%, field accuracy ≥95% and p95 ≤4 seconds. Task accuracy and deadline misses have one-sided 95% confidence gates. The fixed comparator was selected on tuning data before any held-out answer.

| Configuration | Task acc. | Lower 95% | p95 | $ / 1k |
|---|---:|---:|---:|---:|
| Grounded Nano | 90.3% | 87.3% | 2.08 s | $0.076 |
| Grounded Nano → Super | 98.2% | 96.5% | 2.44 s | $0.103 |
| Grounded Nano → Super + hedge | 98.2% | 96.5% | 2.23 s | $0.133 |
| Grounded Super | 98.8% | 97.3% | 1.74 s | $0.339 |

**Measured deployment choice: source-grounded Nano → Super, without hedging.** These four matched controls explain why:

- **Escalation earns its cost:** grounded Nano alone reaches 90.3% task accuracy; adding Super escalation reaches 98.2%.
- **Routing is the lower-cost qualifying choice:** $0.103 versus $0.339 per 1,000 tasks for grounded Super. The tradeoff is 98.2% versus 98.8% accuracy and 2.44 s versus 1.74 s p95; one-point non-inferiority was not established.
- **Hedging is optional at this deadline:** it lowers p95 from 2.44 s to 2.23 s but adds 28.8% cost. Both routes meet the four-second budget and have the same observed accuracy.

The completed M5 study passes its declared quality, latency and cost gates. The complete nine-arm table, paired intervals, per-family/window results and raw traces are in `results/M6-report.md` and `runs/confirmation-20261025T212745Z-ac61b1/`.

Known priced API usage for M5 was **$2.37**, including generation, verification, tuning and every confirmation arm. These are usage-priced estimates, not a reconciled provider invoice.

The selected route used $0.103 per 1,000 tasks versus $0.339 for the qualifying matched grounded-Super control: about 70% less, with observed accuracy of 98.2% versus 98.8%. This does not establish one-point non-inferiority. Hedging added about 29% cost while both routes met the deadline.

### M6: relative dates without wrong answers

All six of M5's remaining errors were relative refund dates such as "in four days". Both Nano and Super returned null or the message date. M6 compared two fixes:

- **Deterministic issue-date normalization.** An analyzer resolves the store's statement from that message's timestamp, ignores arrival dates and customer restatements, and applies explicit corrections. It sends business days, ranges and "next Friday" to review.
- **A reasoning fallback.** A reasoning-enabled call is made when date validation fails.

On development data both reached the same accuracy, but the fallback cost 1.5–3.5× more, or missed the latency criterion. Normalization was frozen, along with a power-based sample size, before any confirmation conversation was written.

| Fresh confirmation | Route | Correct, no review | Wrong answers released | $ / 1k |
|---|---|---:|---:|---:|
| Relative-date challenge (367) | M5 route | 30.8% | 153 | $0.204 |
| | Grounded Super | 28.3% | 139 | $0.353 |
| | **Date normalization** | **75.5%** | **0** | **$0.077** |
| Representative workload (312) | M5 route | 97.4% | 0 | $0.104 |
| | Grounded Super | 96.5% | 1 | $0.349 |
| | **Date normalization** | **98.4%** | **0** | **$0.080** |

The primary improvement was +44.7 points (95% CI 39.5–49.6, preregistered threshold +10). All four safety gates passed, and normalization is now the deployed extraction profile. Every one of its challenge-set reviews was a deliberately ambiguous case, and it answered all 277 answerable challenge conversations correctly. The 4-second deadline gate passed narrowly on a day when Nano served more slowly than during M5. M6 used $5.48 of known priced API usage. Full results: `results/M7-report.md`.

### M7: real-world writing noise

Model-written test conversations are cleaner than real support chat. To measure the gap without exposing customer data, we profiled every eligible conversation in a real support mailbox (1,386 conversations) offline, after local de-identification. Only aggregate rates per speaker role left the machine, after a person approved them by hash. No mailbox text was sent to any model.

Real writing turned out to be mostly structural noise. Customers left 64% of messages without final punctuation, wrote 15% entirely in lowercase, dropped the apostrophe in 12.5% of contractions, and split 13% of messages across several turns. Misspellings were rare: 0.23% of words.

A seeded channel rewrote the 679 sealed M6 conversations at those rates. It never edits amounts, codes, digits, timestamps or speaker roles, so every label stays exact. All three routes ran on both copies: 4,074 requests, costing $0.79. The checks were preregistered before any noisy text existed.

| Representative (312), clean → noisy | Correct, no review | Wrong answers released | Requests over 4 s |
|---|---:|---:|---:|
| **Adopted profile** | **98.4% → 98.4%** | **0 → 0** | 25 → 39 |
| M5 route | 96.8% → 96.8% | 0 → 0 | 30 → 40 |
| Grounded Super | 96.5% → 96.2% | 0 → 1 | 7 → 7 |

- **Answers held.** Noise changed none of the adopted profile's 312 representative answers. It released no wrong answer on the 367 date-challenge conversations either, and the deterministic date analyzer read all 679 noisy conversations correctly.
- **Four of five preregistered checks passed.** The fifth failed on the 4-second deadline. Nano served slowly that day, and the clean copies failed the same bound. Under the preregistered rule, robustness is therefore not shown.
- **Heavy misspelling is untested**, because this mailbox rarely misspells.

Full results: `results/M8-noise-report.md`.

## How we built it

All model inference, synthetic writing and reasoning verification run on Nebius Token Factory with NVIDIA Nemotron 3 Nano, Super and Ultra. The OpenAI-compatible Python SDK handles requests; no OpenAI model inference is used. Structured JSON output, model-specific reasoning controls, cached-token usage and live published prices are recorded.

Python/Pydantic implement source analysis, scoring, billing and the bounded router. Streamlit/Altair provide the benchmark, live traces, evidence trail and workload import. The implementation includes offline fake-endpoint tests for grounding, confidence gates, clustered uncertainty, answer delivery, billing and the complete pipeline.

## The hardest problem was valid measurement

A field-level audit found that all 18 missed Nano errors on the earlier M3 test split were order-ID errors. Our write-up had incorrectly inferred amount-selection errors from a scenario-family name. We corrected the diagnosis and made the source evidence explicit.

Refund issue dates and arrival dates are different facts. M5 uses explicitly bound issue-date statements, plus arrival-only and distinct issue/arrival cases. All verifier disagreements were reviewed before Nano production inference. We retained cases where strong verifiers were demonstrably wrong instead of filtering the benchmark to their answers.

## What we learned

- Model agreement is not ground truth. Define the field contract and inspect the source.
- Benchmark the cheapest qualifying fixed model, including the same evidence/prompt treatment, alongside the strongest-model baseline.
- Tail latency and quality need separate evidence. A low point-estimate p95 is not sufficient to establish a deadline requirement.
- A learned pre-route predictor can learn scenario shortcuts: M3 scored AUC 0.80 on random folds and 0.48 on unseen families, so we did not adopt it.
- The best configuration may be a fixed model. The product should recommend the measured choice and expose uncertainty.
- Count reviews and wrong releases separately. The M5 route's validator released null for "in 3 business days" and "this Friday", wrong answers it could not see. Measuring correct autonomous completions per incoming request kept "send more to review" from looking like an improvement.
- When a deterministic check already knows the answer, use it. A reasoning fallback matched normalization's development accuracy at several times the cost and latency.
- Measure real noise before simulating it. In one support mailbox, missing punctuation, lowercase writing and fragmented turns were common, while misspellings were rare. At those rates, the deterministic date reader did not change a single answer.

## Limits and next steps

The benchmark prose is synthetic and model-written. Labels originally came from fact sheets and source checks, with initial adjudication by a coding agent. The project owner has since confirmed independent human validation of the 312-conversation representative dataset. Original records and scores are unchanged; this does not claim independent validation of every historical study or of the separate challenge set. Explicit fact statements improve label defensibility but narrow language diversity. All six errors from the M5 route were relative-date failures flagged by validation. That family scored 12/18 correct, and the family-cluster sensitivity interval is 94.2–100%, crossing the target. M6 addresses that family on a separate challenge set. However, the study author fixed its expression inventory, and the one family with writer-chosen wording kept only 7 conversations, so arbitrary customer phrasing is still untested. Three same-day time windows are not multi-day availability evidence. M7 adds writing noise measured in a real mailbox, but the conversations remain synthetic, so it is not evidence about real customer language. The existing representative dataset’s subsequent human validation is recorded separately. The 4-second deadline also remains open: it passed narrowly in M6 and failed on a slow serving day in M7, for clean and noisy copies alike.

The next validation step is independently labelled, de-identified customer conversations, followed by multi-day measurements at realistic arrival rates. The import workflow is built so a team can perform that evaluation on its own workload.

## What changed during this build

We implemented workload import and separate validation, offline noise profiling of a private mailbox (aggregates only), source-grounded extraction, actual early answer delivery with complete hedge billing, a confidence-aware optimizer, matched fixed-model controls, fresh semantic verification and auditable experiment seals. The original result, experiment protocols and complete audit records remain available in the repository.

## Feedback on Nebius Token Factory, AI Cloud and NVIDIA models

**What worked well**

- **Drop-in OpenAI compatibility.** The `openai` SDK worked unchanged, including `with_raw_response` for rate-limit headers.
- **`/v1/models?verbose=true`.** It returns machine-readable per-token prices, including cached-input prices, plus context length and features. We used it as the *only* price source, so all costs are computed from live published prices rather than typed by hand.
- **JSON-schema constrained output.** Successful extraction completions in our probes and production benchmarks were schema-valid JSON.
- **Detailed usage blocks.** They include cached-token counts, and informative `x-ratelimit-*` headers came back on every response.
- **Low cost.** Nemotron 3 Nano at $0.06 / $0.24 per million input/output tokens kept the original 10,843 calls behind v1/M1–M4 at about $6. M5, M6 and M7 costs are separately reported with all traces.

**Friction we hit**

- **Inconsistent model-ID casing across the Nemotron family:** `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3-Ultra-550b-a55b`. Our first guesses at IDs failed. Consistent IDs, or case-insensitive matching, would help.
- **`supported_features` doesn't list JSON mode.** It reported only `tools,reasoning`, while the docs say to look for a "JSON mode" tag. JSON-schema output did work on all three models.
- **Reasoning tokens are reported inconsistently.** Nemotron 3 Super and Ultra report `completion_tokens_details.reasoning_tokens`; Nano did not, so reasoning cost has to be inferred from completion tokens.
- **Nano's serverless endpoint had latency bursts.** Over two ~3-second windows, calls producing about 80 output tokens took 4–7 s. No rate limiting was involved. A server-timing or queue-time header would make tail latency diagnosable.
- **Cached-input pricing differs by model.** Nano has no cached-input price, while Super and Ultra do. That's fine, but worth surfacing next to the model in the catalogue.

- **Temporary overload.** Seven Super reasoning-verification requests exhausted two attempts with HTTP 529 "System is overloaded". Sequential retries at 30 RPM completed all seven. Original failures remain in the artifacts; the final 3,402-request production comparison had no API errors.

**NVIDIA Nemotron 3**

- **The thinking switch is reliable.** The `enable_thinking` toggle via `chat_template_kwargs` worked on all three models. Thinking added 300–550 output tokens (roughly 2–3× cost and latency) with no accuracy gain on our extraction probe, so production extraction runs use reasoning-off. Dedicated semantic-verification calls use reasoning-on.
- **Nano** is strong on clear cases and very cheap. Its main weakness on this workload was filling absent fields with plausible-looking values, which deterministic validation can catch.
- **Super** was near-perfect after a prompt revision, making it an ideal escalation target. **Ultra** was perfect on the original v1 workload. Later date-semantics tests show that strong-model agreement alone does not establish label correctness.

**Nebius AI Cloud:** not used. All inference ran on Token Factory serverless endpoints.

## Built with

python · nebius-token-factory · nvidia-nemotron · openai-sdk · pydantic · streamlit · altair · pytest
