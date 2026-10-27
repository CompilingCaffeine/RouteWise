# RouteWise — Agent Handoff

> Historical planning brief, retained during the October 26, 2026 git cleanup.
> This describes the original intended build, not the current implementation or
> completed results. Use README.md, the dated study reports and deployment guide
> for the current state. Optional milestones below are not new work requests.

## Mission

Build **RouteWise**, a workload-specific inference optimizer for the Nebius × NVIDIA hackathon.

It should prove one measurable claim:

> For a structured information-extraction workload, RouteWise can reduce total inference cost while meeting a stated quality target.

This is a developer tool, not a general chatbot and not a generic “easy prompts go to a small model” router.

## Product scope

Given a labelled evaluation dataset, RouteWise:

1. Benchmarks 2–3 eligible NVIDIA open-source models via Nebius Token Factory.
2. Measures field-level quality, task accuracy, latency, token use, and estimated cost by task category.
3. Selects the cheapest model that meets configured quality and latency constraints.
4. Runs task-specific deterministic validation on each answer.
5. Retries once or escalates a failed request to a stronger model.
6. Compares the resulting policy with two baselines: always-smallest and always-strongest.

The interface should make the evidence visible: benchmark results, the live routing decision, validation/escalation details, and final evaluation results.

## Sharp differentiation

OpenRouter already offers general-purpose automatic routing and fallbacks. RouteWise differentiates itself by optimizing for a **specific, labelled workload**:

- Routing is based on the project’s own controlled benchmark data, not popularity or generic task inference.
- Users declare measurable requirements, such as “at least 95% field accuracy and p95 latency below 2 seconds.”
- Escalation occurs when an answer fails semantic or structural checks—not merely on provider failure.
- The product reports whether its policy actually beats fixed-model baselines on held-out data.

Do not claim a novel routing algorithm. The credible story is evidence-based, quality-constrained optimization for an application’s real workload.

## Recommended MVP workload

Start with synthetic customer-support conversations and two objective task categories:

- **Structured extraction:** extract refund amount, currency, contact date, refund date, and related fields.
- **Ticket classification:** classify a ticket into a known category.

Use 50–100 examples with known ground truth. Split them into a policy-tuning set and a held-out evaluation set; never report final results solely on the data used to choose the policy.

Structured extraction is the priority because it enables cheap, deterministic validation. Include adversarial examples: multiple dates or amounts, quoted versus actual refund values, missing optional values, and ambiguous phrasing.

## Core architecture

```text
Dataset ──> benchmark runner ──> per-model metrics ──> routing policy
                                                        │
Incoming typed task ──> selected least-cost model ──> validator
                                                        │
                              valid ───────────────────┴──> response + trace
                              invalid ──> bounded escalation ──> revalidate + trace
```

Keep the first policy deterministic. If the task category is known, route with a table generated from benchmark results. Do not introduce an LLM classifier until the core experiment works.

### Required components

| Component | Responsibilities |
| --- | --- |
| Model client/registry | Token Factory calls, model metadata, normalized request/response and usage fields |
| Dataset | Versioned JSON/JSONL labelled examples, category, input, expected structured output |
| Benchmark runner | Run every candidate model with the same prompt, capture raw output, latency, tokens, cost, evaluator result |
| Evaluator | Exact/normalized field-level scoring and task-level correctness against ground truth |
| Policy optimizer | Select the lowest-cost model satisfying each category’s thresholds; clearly report when none qualifies |
| Validator | JSON/schema checks, required-field checks, and source-evidence checks where deterministic |
| Router | Apply policy, perform at most one retry/escalation, record every decision and expense |
| Dashboard | Benchmark, live-router trace, and held-out comparison views |

## Validation and escalation

Validation must be meaningful, not merely “valid JSON.” For extraction tasks, implement:

- Schema/type validation.
- Required-field checks.
- Normalization-aware field comparison where appropriate.
- Evidence rules such as: if the source states a date, an extracted required date cannot be `null`; an amount must be supported by the source.

On failure, escalate once to a stronger preselected model (or retry once with a corrective prompt). Record the reason, model chain, validator outcomes, latency, and total request cost. No unbounded retries.

The key question is whether validation is cheap enough to justify escalation. Prefer deterministic checks; an LLM judge on every response can erase savings and must be fully counted if used.

## Metrics and evaluation

For every call, retain model, category, prompt version, output, latency, input/output tokens, estimated cost, validation result, and escalation status/reason.

Report on the same **held-out** dataset:

| Strategy | Purpose |
| --- | --- |
| Always smallest model | Cost-first baseline |
| Always strongest model | Quality-first baseline |
| RouteWise | Policy + validation + bounded escalation |

Report task accuracy, field accuracy, median latency, p95 latency, cost per 1,000 tasks, escalation rate, and savings against the strongest-model baseline. Include every routing, validation, retry, and escalation cost.

RouteWise is successful only if it meets the configured quality requirement and improves a meaningful tradeoff versus a baseline. A negative result is still valid if documented honestly.

## Implementation order

### 1. Feasibility first

- Confirm Token Factory credentials, available hackathon-eligible NVIDIA models, pricing/usage data, and rate limits.
- Call 2–3 models with one identical extraction prompt.
- Verify that response timing, token usage, and enough pricing information are available.
- Deliver a minimal script plus comparable records before touching the frontend.

**Decision gate:** if two usable models or comparable metrics are unavailable, narrow immediately to two models and one category.

### 2. Reproducible benchmark

- Create the labelled dataset and fixed prompt template.
- Implement runner, evaluator, logs, and cost calculation.
- Produce the first per-model benchmark report.

### 3. Policy and router

- Generate deterministic per-category policy from measurements and user thresholds.
- Implement no-qualifying-model handling; never silently relax constraints.
- Expose an API/CLI that returns the selected model and measurement-based reason.

### 4. Validation and escalation

- Add deterministic validator, one bounded escalation, and complete request tracing.
- Test deliberately difficult examples and verify that escalations correct failures often enough to be worthwhile.

### 5. Dashboard and proof

- Benchmark page: quality, cost, latency by model/category.
- Live router page: input, selected model, answer, validation, escalation, and cost trace.
- Evaluation page: RouteWise vs both baselines plus a cost-vs-accuracy chart.

## Explicit non-goals

Do **not** spend MVP time on speculative decoding, dynamic batching, semantic caching, fine-tuning, reinforcement learning, complex agent planning, multi-cloud routing, production auth/billing, or autonomous online learning.

These are different problems and dilute the core experiment. An offline failure-analysis loop is optional only after the baseline, router, evaluation, and demo work.

## Hackathon deliverables to verify

The prior plan assumed a working app using Nebius Token Factory or Nebius AI Cloud with an eligible NVIDIA open-source model, plus a public repository, open-source license, and public YouTube demo under three minutes. **Verify the current official rules, eligible models, deadline, deployment requirements, and submission checklist before committing.**

Use Token Factory for model inference. LangSmith is optional for tracing/debugging, but the reproducible benchmark dataset and calculations must remain in the repository. Tavily, Toloka, and Tandem are not part of the MVP unless they add a real, demonstrable capability.

## Demo narrative (under 3 minutes)

1. Explain the fixed-model cost/quality tradeoff.
2. Show actual benchmark measurements.
3. Send a live typed extraction request and show the selected model and reason.
4. Demonstrate a validation failure and bounded escalation.
5. Show held-out RouteWise vs baseline results.
6. State the Token Factory / NVIDIA model integration.

## First agent task

Inspect the repository and establish the feasibility path before designing UI. Return a short report containing:

1. Available models and the exact API integration approach.
2. Proposed data schema and directory layout.
3. The first three deterministic extraction validation rules.
4. A concrete command or script that benchmarks one example across every candidate model.
5. Any blocker that requires a human credential, product, or scope decision.

Do not fabricate model capabilities, price, benchmark results, or hackathon compliance. Use real measurements and label all assumptions.

## Definition of done

A judge can run or view a public demo, see actual results on a held-out workload, follow one routed request through validation/escalation, and understand exactly why RouteWise meets (or fails to meet) its cost-quality hypothesis.

## Ambition roadmap — learn inference by adding one measured systems layer at a time

Do not attempt every milestone before the MVP works. Each layer must preserve the same discipline: define a hypothesis, instrument the system, compare a control and treatment on a held-out or repeatable workload, and retain the raw results.

The long-term framing is:

> **RouteWise is a workload-aware inference control plane that learns when inexpensive models are safe, escalates likely failures, and enforces quality and latency objectives under load.**

### Milestone 0 — reproducible quality/cost router (required MVP)

This is the existing core: benchmark models on a labelled workload, choose a policy from the tuning split, validate outputs, perform one bounded escalation, and evaluate the three strategies on a held-out split.

**Implementation detail**

- Store an immutable `experiment_id`, dataset version/hash, prompt version/hash, model configuration, policy version, and code revision with every run.
- Write one newline-delimited request trace per task. A trace should include `request_id`, category, input length, selected model, model-chain, timestamps, tokens, raw/parsed output, evaluator result, validator result, escalation reason, and fully loaded cost.
- Use a single cost function for all strategies; no dashboard-only calculations.

**Acceptance criterion:** rerunning the same configuration produces comparable metrics and an inspectable trace for every sample.

### Milestone 1 — serving observability and token-level latency

**Question:** Is apparent request latency hiding meaningful differences in prefill, decoding, and tail behavior?

Add metrics beyond a single wall-clock duration:

| Metric | Definition | Why it matters |
| --- | --- | --- |
| Queue wait | Arrival to dispatch | Separates router/scheduler delay from model execution |
| TTFT | Dispatch to first generated token, if streaming is supported | Mostly exposes prompt processing/prefill and queueing |
| Decode duration | First token to final token | Captures generation speed |
| TPOT | Decode duration / generated tokens | Normalizes output-length effects |
| End-to-end latency | Arrival to final validated response | User-facing outcome |
| p50/p95/p99 | Percentiles for each relevant duration | Shows tail-latency behavior |
| Throughput | Successful tasks/sec and output tokens/sec | Measures system capacity |

**Implementation detail**

- Add monotonic timestamps at `received`, `dispatched`, `first_token`, `model_complete`, `validation_complete`, and `response_complete`.
- For non-streaming APIs, record `first_token: null` rather than inventing TTFT; report end-to-end latency and clearly label the limitation.
- Record input and generated token counts where the provider exposes them. Never infer token counts from character length for a headline metric.
- Build an aggregation command that emits a machine-readable summary and a chart-ready CSV grouped by model, category, concurrency, and strategy.

**Experiment:** send matched short/medium/long prompts with controlled output limits to every candidate model. Plot TTFT, TPOT, and end-to-end latency against prompt/output length.

**Acceptance criterion:** the dashboard and experiment report distinguish model time from queue time, and show at least p50/p95 latency for repeatable runs.

### Milestone 2 — concurrency, load generation, and SLO-aware routing

**Question:** Can RouteWise protect quality and tail latency as demand rises?

Add a reproducible load generator rather than manually clicking the UI. It should replay a fixed workload mix at configurable arrival rate and concurrency.

**Implementation detail**

- Define `LoadProfile` with `concurrency`, `requests_per_second` or inter-arrival distribution, task-category mix, prompt-length mix, maximum duration, random seed, and output-token limit.
- Use a bounded worker pool and capture arrival/queue/dispatch/completion timestamps for every task. Do not allow unbounded in-memory queues.
- Implement a small policy interface, for example `select(request, runtime_state) -> Decision`; `runtime_state` initially includes queue depth, active requests, recent p95 latency, and per-model rolling error/validation-failure rates.
- Declare explicit objectives such as `quality >= 0.95`, `p95_end_to_end_ms <= 2000`, and a per-request or per-1,000-task cost target.
- Start with interpretable rules, not reinforcement learning: normal load uses the cheapest qualified model; rising queue chooses a model known to meet the latency objective; high-priority tasks use a stronger model; no eligible option returns an explicit SLO-violation decision.

**Controls:** always-smallest, always-strongest, static RouteWise policy, and load-aware RouteWise policy.

**Experiment:** sweep concurrency (for example 1, 5, 20, 50) over an identical seeded workload. Report quality, throughput, queue time, p95/p99 latency, rejection/defer rate, and cost.

**Acceptance criterion:** a saved result demonstrates the tradeoff between throughput, tail latency, and cost; the router’s decision trace records which runtime signal changed its choice.

### Milestone 3 — learned failure prediction (selective routing)

**Question:** Can a cheap predictor identify requests for which the inexpensive model is likely to fail?

The initial deterministic table routes by known category. Extend it with a lightweight supervised risk model that estimates `P(small_model_validation_failure | request features)` before the expensive call.

**Implementation detail**

- Construct training labels from logged cheap-model results: `1` if deterministic validation or ground-truth evaluation fails; `0` otherwise.
- Begin with transparent features: task category, normalized input length, count of dates/currency amounts/entities, multiple-issue indicator, missing-field cues, and prompt/template version. Avoid target leakage: do not use the candidate model’s answer or post-call validator result as a pre-route feature.
- Use a simple baseline first: logistic regression or a shallow decision tree. Save feature definitions, training-split identity, hyperparameters, calibration method, and model artifact.
- Add a threshold policy: if predicted risk is below threshold, call the cheap model; otherwise call the stronger model directly. Tune that threshold only on the tuning split according to the declared cost-quality objective.
- Evaluate on held-out requests and include a calibration plot, precision/recall curve, false-negative rate, escalation rate, end-to-end accuracy, latency, and total cost.

**Critical comparison:** deterministic category-only policy vs learned risk policy vs always-smallest vs always-strongest. The learned model is worthwhile only if it reduces expensive calls or post-hoc failures without violating the quality requirement.

**Acceptance criterion:** a held-out report establishes whether the predictor improves the policy, and exposes the cost of its false negatives instead of reporting only classifier accuracy.

### Milestone 4 — validation as a selective-prediction experiment

**Question:** Which validator catches enough real errors to justify its own cost and latency?

Compare validators rather than assuming an LLM judge is better.

| Validator | Implementation | Expected economics |
| --- | --- | --- |
| Schema-only | JSON parsing, types, enum/range checks | Nearly free; misses semantically wrong but well-formed answers |
| Deterministic evidence | Required fields, source string/regex evidence, cross-field consistency | Cheap and explainable for structured extraction |
| Model judge | A separate model scores output against source/rubric | Potentially broader coverage; adds cost, delay, and its own error |

**Implementation detail**

- Maintain a manually reviewed evaluation subset with known true answer correctness; validator performance cannot be measured solely by agreement with itself.
- For each validator, calculate error recall, false-positive rate, precision of escalation, validator latency/cost, correction rate after escalation, and end-to-end task accuracy/cost.
- Treat a validator as a classifier: an escalation is its positive prediction. Plot the operating point or threshold tradeoff where applicable.
- When testing model judges, version the rubric and judge model, make the output structured, and count every judge token and call in total cost.

**Acceptance criterion:** the final policy uses the cheapest validator that supports the quality target, with empirical evidence for that choice.

### Milestone 5 — serving-runtime investigation (advanced systems track)

**Question:** How do serving-engine choices affect the prefill/decode, throughput, and tail-latency tradeoffs that the router sees?

This milestone moves from application-level inference economics into model serving. Run it only if you can legally and practically deploy an eligible model under a controllable serving engine; it is separate from the Token Factory integration requirement and must not jeopardize it.

**Candidate investigations**

- Continuous/dynamic batching: effect on throughput and tail latency under different arrival rates.
- Prefix/KV-cache reuse: effect of a shared system prompt or long document prefix.
- Quantization: quality, memory footprint, throughput, and latency tradeoffs for the same model.
- Prefill versus decode: vary input length and output length independently to observe their distinct costs.

**Implementation detail**

- Make the serving engine and version, model revision, hardware/GPU type, quantization configuration, maximum sequence length, batch/concurrency settings, and cache configuration part of each experiment configuration.
- Use a local or controlled benchmark harness that fixes prompt sets, output limits, warm-up policy, random seed where supported, and measurement window.
- Run a warm-up phase separately; do not mix cold-start/model-load time into steady-state inference metrics unless that is the experiment.
- Measure TTFT, TPOT, tokens/sec, queue delay, memory use if observable, and p50/p95/p99 end-to-end latency at multiple offered loads.

**Acceptance criterion:** publish a small experiment report answering one focused systems question, such as “At what offered load does dynamic batching increase throughput enough to worsen our p95 latency SLO?”

### Milestone 6 — offline policy-improvement loop (optional capstone)

**Question:** Can logged failures safely produce a better routing rule over time?

Do this offline; do not autonomously alter a production policy from a handful of failures.

**Implementation detail**

- Add a failure-review dataset containing request features, route, output, validator result, true correctness where available, escalation outcome, and a human-review label for ambiguous samples.
- Cluster or group failures by interpretable characteristics, then propose a specific policy adjustment: a new feature rule, risk threshold, prompt revision, or stronger-model assignment.
- Re-evaluate every candidate policy against a frozen held-out set before promotion. Keep a champion/challenger report and never overwrite the last known-good policy.
- Require a minimum sample size and confidence interval (or bootstrap interval) for the reported quality and cost difference before adopting a change.

**Acceptance criterion:** a policy promotion is reproducible, evidence-backed, reversible, and evaluated on unseen examples.

## Suggested learning sequence

| Phase | What to build | What it teaches |
| --- | --- | --- |
| 1 | MVP benchmark, validator, and router | Evaluation design, quality/cost accounting, controlled experiments |
| 2 | Instrumentation and load generator | Queueing, tail latency, prefill versus decode, throughput measurement |
| 3 | SLO-aware scheduling | Multi-objective policy design and workload management |
| 4 | Failure predictor and validator study | Supervised learning, calibration, selective prediction, error analysis |
| 5 | Controlled serving runtime benchmark | Batching, KV cache, quantization, and hardware/runtime-level tradeoffs |

For each phase, maintain an experiment note with: hypothesis, control, treatment, fixed variables, raw-result location, chart, conclusion, and the next decision. The fastest way to learn inference is to make every feature answer a measurable question.
