# RouteWise: Devpost submission text

**Track:** Best Apps and Agents
**Demo:** https://nebius-routewise.streamlit.app/
**Code:** https://github.com/CompilingCaffeine/RouteWise (MIT)

---

## Inspiration

Teams that put an LLM into a production workflow usually pick **one** model and live with the trade-off:

- the strongest model, which overpays on easy requests;
- the cheapest model, which quietly ships wrong answers.

Generic routers pick models by popularity or guess the task type. None of them knows whether a cheap model is good enough for *your* data, or what to do when it isn't.

We wanted to answer a narrow question with evidence: **for a real structured-extraction workload, can a cheap NVIDIA Nemotron model handle most requests, while bad answers are caught and fixed, at a fraction of the cost and within declared quality and latency targets?**

## What it does

RouteWise is a workload-aware inference router for Nebius Token Factory:

1. **Benchmark.** It runs Nemotron 3 Nano, Super and Ultra on your labelled tuning data. For every call it records accuracy, latency, tokens and cost, priced from Token Factory's live `/v1/models?verbose=true` pricing.
2. **Optimize.** It picks, per task, the cheapest route that meets the requirements you declare up front, for example "≥ 85% task accuracy, ≥ 95% field accuracy, p95 ≤ 4 s". The optimizer simulates every route option from the recorded calls, with no extra API spend. It never relaxes a threshold silently.
3. **Validate.** Every answer goes through deterministic checks that never see ground truth:
   - the amount must appear in a sentence about a refund;
   - the order ID must occur in the conversation;
   - the refund date must be one the conversation states, with relative dates resolved from the right message.
4. **Escalate, or hedge.** An answer that fails validation is retried **once** on a stronger model. If the cheap model is slower than its own p95, the stronger model is started in parallel and the first valid answer wins. A request never makes more than 2 calls, and every call is billed.
5. **Prove it.** RouteWise compares itself against always-cheapest and always-strongest on **held-out** data. It reports accuracy, p50/p95 latency, cost per 1,000 tasks, escalation rate, validator precision/recall and a plain verdict, and keeps one trace per request.

The dashboard shows the benchmark, the policy and the reasoning behind it, and a live router that calls Token Factory. You can follow one request through validation, escalation and hedging, and see the full evidence trail of every held-out run.

## Results (measured, held-out)

The thresholds were committed before any benchmark. The policy was committed before any held-out example was seen.

| Primary run, 62 held-out tasks | Task acc. | Extraction p95 | $ / 1k tasks |
| --- | --- | --- | --- |
| Always Nemotron 3 Nano | 88.7% | 4.4 s | $0.050 |
| Always Nemotron 3 Ultra | 100% | 1.3 s | $0.769 |
| **RouteWise** | **95.2%** | **5.0 s** | **$0.068** |

- **Quality:** RouteWise met every accuracy target, escalating 6.5% of requests.
- **Cost:** **91% less** than always using Ultra.
- **Latency:** **missed** the declared 4 s extraction p95. The cause was two short windows where Nano's serverless endpoint took 4–7 s per call. **The pre-registered hypothesis is therefore reported as not supported.**

What we did next is all in the repo's evidence trail:

- **Hedged escalation.** We added it after that run.
- **Follow-up run.** The provider was fast, so hedging never fired. It still showed how much accuracy varies run to run: 92.7% vs 97.6% for identical behaviour.
- **Controlled fault-injection run.** We added a seeded 3–6 s stall to 10% of Nano calls, identical for every strategy:
  - unhedged RouteWise missed the ceiling (p95 4.3 s);
  - **hedged RouteWise held it (p95 2.7 s)** at about 20% extra cost, still **89% cheaper than always-Ultra**.

Every number above links to raw traces in `runs/`, and the whole experiment cost **$0.36** in API calls.

### Four pre-registered follow-up experiments (dashboard → Experiments)

- **Is validation worth it?** On 186 held-out answers:
  - **The deterministic validator** never escalated a correct answer, at +28% cost over no validation.
  - **A Nemotron 3 Super judge** catches more errors, mostly classification ones, but costs 3.5× more per task. It adds ~0.8 s to every request, enough to break the latency SLO by itself.
  - **A Nano judge** flags a third of correct answers.
- **Where does latency go?** In 144 streamed calls, a fixed ~0.74–1.1 s time-to-first-token floor makes up 68–92% of an extraction call. Ultra decodes fastest per token. Provider tail events showed up both as slow first tokens and as mid-stream decode stalls, which is why RouteWise hedges on total elapsed time.
- **Can we predict failures before calling Nano?** We generated a new 1,198-conversation workload in 16 scenario families, written by Nemotron 3 Super and Ultra from fact sheets with verified labels. Then we trained a calibrated predictor and tested it on four families it never saw.
  - **It failed honestly.** Test AUC is 0.48, and the pre-registered decision is not to adopt it.
  - **The reason is family leakage.** On a random split the same model looks good (AUC 0.80). The deterministic validator, meanwhile, already catches 87% of Nano's errors on those unseen families.
- **Latency under load.** Throughput scales sub-linearly to 10 req/s at 16 concurrent requests for Nano, with p95 under 1.8 s. The escalation target, Super, had the noisier tail.

## How we built it

- **Inference:** everything runs on **Nebius Token Factory** through its OpenAI-compatible API, using the standard `openai` Python SDK.
- **Models:** **NVIDIA Nemotron 3 Nano 30B-A3B**, **Super 120B-A12B** and **Ultra 550B-A55B**, all with reasoning off (`chat_template_kwargs.enable_thinking=false`, verified by a probe) and **JSON-schema constrained output** (`response_format: json_schema`).
- **Workload:** 120 labelled support conversations (refund extraction and ticket classification).
  - Most are seeded templates whose labels are correct by construction, plus 10 hand-written edge cases.
  - Adversarial cases include quoted price vs. agreed partial refund, counter-offers, multiple dates, relative dates across days, and currency symbols.
  - They are split into tuning and held-out halves.
- **Code:** Python, Pydantic, Streamlit and Altair. 68 tests run against a fake Token Factory endpoint, including a full benchmark → policy → evaluate → dashboard run, with no network calls.
- **Reproducibility:** every run writes a manifest with:
  - the git SHA
  - the dataset hash
  - prompt hashes
  - the price-snapshot hash
  - the thresholds

  It also writes one JSON line per call or request. The reported total cost is asserted to equal the sum of per-trace costs.

## Challenges we ran into

- **The cheap model invents values.** Error analysis on the tuning split showed Nemotron 3 Nano copying example values out of our prompt (`ORD-48213`, `1250.0`) into fields that had no value, and returning product names as order IDs. A prompt fix (v2) and two new validator rules caught all of these on the tuning set. On held-out data the validator caught 43–71% of Nano's errors with **zero false alarms**.
- **Tail latency, not quality, broke the SLO.** We didn't expect the cheapest model's serving tail to be the binding constraint. Measuring it honestly, and then testing a fix under controlled conditions, became the most interesting part of the project.
- **Staying honest under time pressure.** We kept the failed pre-registered result as the headline and labelled every later run for what it is. Validator blind spots found on held-out data were documented, not patched.

## Accomplishments we're proud of

- **A reproducible experiment:** thresholds frozen in git before benchmarking, a policy frozen before held-out data, and raw traces for every request.
- **A router that knows when cheap is safe:** 91% cost reduction at 95% task accuracy, on a workload where always-cheap drops to about 85–90%.
- **A controlled demonstration** that hedged escalation contains provider stalls while keeping almost all of the savings.

## What we learned

- **A latency SLO needs its own evidence.** Measure the tail separately from quality, because a small tuning set will not reveal a provider's tail.
- **Deterministic validation is cheap and precise but not complete.** Report recall against ground truth instead of assuming it.
- **Run-to-run variance at temperature 0 is real.** On 41 examples it moved accuracy by about 5 points, so confidence intervals matter more than single numbers.

## What's next

- **Load-aware routing:** switch to Super directly when the cheap model's recent p95 rises, instead of waiting per request.
- **A learned failure predictor** that decides whether to start with the cheap model.
- **Larger and real-world held-out sets**, run repeatedly to measure the latency tail.
- **Model-judge validators**, compared against the deterministic validator on cost and recall.

## Feedback on Nebius Token Factory, AI Cloud and NVIDIA models

**What worked well**

- **Drop-in OpenAI compatibility.** The `openai` SDK worked unchanged, including `with_raw_response` for rate-limit headers.
- **`/v1/models?verbose=true`.** It returns machine-readable per-token prices, including cached-input prices, plus context length and features. We used it as the *only* price source, so all costs are computed from live published prices rather than typed by hand.
- **JSON-schema constrained output.** Every Nemotron call in our probe and benchmarks returned schema-valid JSON.
- **Detailed usage blocks.** They include cached-token counts, and informative `x-ratelimit-*` headers came back on every response.
- **Low cost.** Nemotron 3 Nano at $0.06 / $0.24 per million input/output tokens kept all 10,843 calls behind every committed experiment, including generating and double-verifying a 1,198-conversation dataset, at $6.00.

**Friction we hit**

- **Inconsistent model-ID casing across the Nemotron family:** `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3-Ultra-550b-a55b`. Our first guesses at IDs failed. Consistent IDs, or case-insensitive matching, would help.
- **`supported_features` doesn't list JSON mode.** It reported only `tools,reasoning`, while the docs say to look for a "JSON mode" tag. JSON-schema output did work on all three models.
- **Reasoning tokens are reported inconsistently.** Nemotron 3 Super and Ultra report `completion_tokens_details.reasoning_tokens`; Nano did not, so reasoning cost has to be inferred from completion tokens.
- **Nano's serverless endpoint had latency bursts.** Over two ~3-second windows, calls producing about 80 output tokens took 4–7 s. No rate limiting was involved. A server-timing or queue-time header would make tail latency diagnosable.
- **Cached-input pricing differs by model.** Nano has no cached-input price, while Super and Ultra do. That's fine, but worth surfacing next to the model in the catalogue.
- **Docs:** the structured-output Python example has a syntax error (missing commas around `response_format`). It also nests the schema directly under `json_schema` rather than the `{name, schema}` shape the OpenAI SDK sends.

**NVIDIA Nemotron 3**

- **The thinking switch is reliable.** The `enable_thinking` toggle via `chat_template_kwargs` worked on all three models. Thinking added 300–550 output tokens (roughly 2–3× cost and latency) with no accuracy gain on our extraction probe, so we ran reasoning-off throughout.
- **Nano** is strong on clear cases and very cheap. Its main weakness on this workload was filling absent fields with plausible-looking values, which deterministic validation can catch.
- **Super** was near-perfect after a prompt revision, making it an ideal escalation target. **Ultra** was perfect on this workload.

**Nebius AI Cloud:** not used. All inference ran on Token Factory serverless endpoints.

## Built with

python · nebius-token-factory · nvidia-nemotron · openai-sdk · pydantic · streamlit · altair · pytest
