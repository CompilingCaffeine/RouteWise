# M3: Can a cheap predictor spot Nano's failures before calling it?

Pre-registered 2026-10-25, before any v2 conversation is generated or any model is trained.

## Question

Can a lightweight model that uses **only features available before any model call** estimate `P(Nemotron 3 Nano's extraction answer is wrong)` well enough to help? The question is whether sending high-risk requests straight to Nemotron 3 Super improves on the current policy, where Nano answers first and a validator-triggered escalation goes to Super. Better means accuracy, cost or latency, on scenario families the predictor has never seen.

## Why a new dataset

The v1 workload is too small: about 10 Nano failures on the tuning split. Its templated phrasing would also let a predictor learn *which template* produced an example instead of *what makes a request hard*. So M3 uses a new, separate dataset (`data/v2`). The v1 results are untouched.

## Data: `data/v2` (extraction only)

- **Scenario families.** There are 16, with 75 conversations each, about 1,200 in total. 14 families mirror the v1 templates; 2 are new:
  - `multi_item_sum`: the refund is the sum of two items.
  - `two_orders`: the refund applies to one of two orders.
- **Fact sheets.** A seeded program samples each conversation's facts:
  - product, currency, amounts
  - distractor amounts and dates
  - the order ID, or none
  - the refund date and how it is expressed (absolute, or a relative phrase in a named message)
  - whether a refund is requested
  - tone (neutral, terse, chatty, frustrated, formal), light typos or none, and message count
- **Writers.** Nemotron 3 Super or Ultra writes the messages (alternating within each family) as JSON: speaker, day offset, text. We render the timestamps, so `contact_date` is correct by construction. **Labels come from the fact sheet.**
- **Deterministic verification, required.**
  - Every must-include string (refund amount, price, order ID, date strings, relative-date phrase) appears in the specified message.
  - No money amount, explicit date or order-ID-like token appears beyond the fact sheet's.
  - A failed conversation is regenerated up to twice, then dropped.
- **Semantic verification.** Nemotron 3 Ultra and Super, both with reasoning on, extract every conversation. A conversation where **both** disagree with the label on the same field goes to an adjudication queue. The coding agent (Claude) reviews that queue by hand, decides keep or drop for each item, and records a reason in `data/v2/adjudication.jsonl`. Drop rates are reported per family.
  - Conversations are **not** filtered with RouteWise's own deterministic validator, so its false-positive rate stays measurable.
- **Splits.** Families are grouped and fixed by seed (`Random(31)`) before any generation:
  - **Test (4):** `large_amount`, `refund_in_past`, `partial_refund_distractor`, `relative_refund_date`
  - **Calibration (2):** `multi_item_sum`, `system_message_first`
  - **Train (10):** `multi_date`, `shipping_fee_only`, `refund_no_date`, `two_orders`, `full_refund_absolute`, `negated_refund`, `replacement_only`, `currency_symbol`, `no_amount_stated`, `counteroffer`

## Labels and recorded answers

- **Calls on every v2 conversation**, all in the production configuration (prompt `extraction_v2`, JSON schema, reasoning off):
  - Nemotron 3 Nano: **two independent samples**
  - Nemotron 3 Super: one
  - Nemotron 3 Ultra: one
- **Training target:** `y = 1` if Nano's answer is not task-correct. Both Nano samples become training rows.

## Features (pre-route only; never a model answer or validator output)

All are computed from the conversation text by deterministic parsing:

- **Structure:** message count, customer and agent message counts, characters, days spanned, whether the first message is not from the customer.
- **Money:** count of mentions, distinct amounts, amounts in refund sentences, non-USD marker, symbol-only currency.
- **Dates:** explicit dates, relative-date phrases, distinct dates, whether a refund-date statement exists.
- **Order IDs:** count of order-ID-like tokens.
- **Cue words:** negated refund cues, counter-offer cues, fee/shipping cues, multi-item cues.

Family IDs, writer, tone and fact-sheet fields are **never** features.

## Models

- **Primary:** L2 logistic regression on standardized features. `C ∈ {0.1, 1, 10}` is chosen by family-grouped cross-validated log-loss on the train families.
- **Secondary baseline:** a depth-3 decision tree.
- **Calibration:** Platt scaling fitted on the calibration families.

## Policies compared (replayed from recorded calls on the test families, every call billed)

| Policy | Rule |
| --- | --- |
| `always_smallest` | Nano answer |
| `always_strongest` | Ultra answer |
| `validator` (current RouteWise) | Nano; Super if the deterministic validator fails |
| `risk_only` | Super if p ≥ τ, else Nano |
| `risk+validator` | Super if p ≥ τ, else Nano with validator escalation |
| `oracle` (upper bound, not deployable) | Super if and only if Nano is wrong |

**Threshold τ** is chosen on the calibration families, from τ ∈ {0.05, 0.10, …, 0.95}. It maximizes `risk+validator` task accuracy subject to cost ≤ 2× the `validator` policy's cost; ties go to the lower cost.

## Hypotheses

- **H3a.** On the test families (unseen), the calibrated logistic model's ROC-AUC is ≥ 0.70.
- **H3b, the leakage check.** On the train families, random 5-fold CV AUC exceeds family-grouped CV AUC by ≥ 0.05. If so, a random split would overstate how well the predictor generalizes.
- **H3c.** On the test families, `risk+validator` has higher task accuracy than `validator`, at ≤ 2× its cost per 1k tasks.
- **H3d.** On the test families, the calibrated model's Brier score is lower than that of a constant predictor at the training base rate.

## Decision rule

Recommend adding the predictor to the RouteWise policy only if both of these hold, pooled over both Nano samples:

- the paired bootstrap 95% CI of the accuracy difference (`risk+validator` − `validator`) excludes zero;
- the cost ratio is ≤ 2×.

Otherwise, report that it is not worth deploying.

## Budget and limitations, declared in advance

- Expected API spend is about $6–10.
- The conversations are synthetic and written by Nemotron models, so Nemotron readers may find them easier or harder than human-written text.
- Adjudication is done by the coding agent, not a human.
- There are four test families. Generalization to unseen families is measured, but only on these four.

## Generation log

- **Full synthesis run:** `synth-20261025T193042Z-3f4d3b`. 1,200 fact sheets; writer cost $0.55.
- **Initial acceptance:** 1,159 of 1,200. Most rejections (40 of 41) came from `full_refund_absolute`, where writers paraphrased the planned duration "after a week" as "after just a week" or "after one week". The verifier flagged these as time expressions.
- **Rule fix:** a duration creates no date, so the family now also allows "a week" and "one week". This is a verifier strictness fix, not a label change.
- **Re-verification:** the stored writer texts were re-checked under the corrected rules (`reverified.jsonl` in the run directory), with no regeneration. The result is **1,198 of 1,200 accepted**. The two remaining rejections, one in `full_refund_absolute` and one in `partial_refund_distractor`, stay dropped.

## Adjudication protocol

Fixed before the queue was produced.

- **What gets reviewed.** Each queued item is a conversation where both reasoning verifiers disagree with the fact-sheet label on the same field.
- **What the reviewer sees.** The conversation text, the disputed fields, the label, and both verifiers' values. Nano's answers are **not** shown, so the review stays blind to the model being predicted.
- **Keep:** the text supports the label under the `extraction_v2` field rules, read literally. The label is what a careful reader applying those rules would extract, even if both verifiers read it differently.
- **Drop:** the text contradicts the label, or does not support it as the reasonable reading. Typical causes are a writer deviating from the plan:
  - the customer written in the agent's voice
  - a refund request worded so it no longer asks for money back
  - an extra fact that changes the answer
- **No relabelling.** A label is never edited to match the text; a mismatched item is dropped. This keeps every label traceable to its fact sheet.
- **Records.** Each decision is recorded in `data/v2/adjudication.jsonl` with a one-sentence reason. The reviewer is the coding agent (Claude), not a human.

## Semantic verification: `synthverify-20261025T193647Z-4e6543`

| Verifier | Agreement with fact-sheet labels (all 6 fields) | Cost |
| --- | --- | --- |
| Ultra, reasoning on | 99.4% of 1,198 conversations | $2.32 |
| Super, reasoning on | 99.1% | $0.74 |

**The adjudication queue was empty.** On no conversation did both verifiers disagree with the label on the same field, so `adjudication.jsonl` has no decisions and nothing was dropped.

**Extra sanity review** (not required by the protocol). I read all 18 conversations where *either* verifier disagreed:
- **Verifier glitches:** an empty answer (`v2-03045`) and an impossible date (`2026-28-20`, `v2-10005`).
- **Super inferring dates that aren't there:**
  - `v2-01050` gets a refund date from "approved", but the text only promises a confirmation email.
  - `v2-07036` gets a wrong contact date.
- **Systematic single-verifier readings in two families:**
  - `relative_refund_date`, 8 cases: Super returns a null amount when only the customer states the refund amount and the agent confirms the refund without repeating it.
  - `refund_in_past`, 6 cases: Ultra reads "I requested a refund last week and it hasn't arrived" as not requesting money back.

  Under the `extraction_v2` rules ("the customer asks for money back anywhere"; an amount "stated" by the customer for the refund) the labels stand. These are nonetheless subtle readings, and Nano may disagree with them in the same way, which is relevant because both families are test families.

## Production answers on v2

| Run | Model | Task accuracy | Cost |
| --- | --- | --- | --- |
| `benchmark-20261025T193706Z-f623b3` | Nano, sample 1 | 77.5% | |
| `benchmark-20261025T195333Z-f05929` | Nano, sample 2 | 77.0% | |
| `benchmark-20261025T194746Z-b88ebe` | Super, Ultra | 98.6%, 99.9% | |

The Nano runs used the identical `v2c` pool.

Nano fails 22.5% of conversations overall. Failure rates by family range from 100% (`no_amount_stated`, where Nano always invents an amount) down to 6.7%.

## Results: `risk-20261025T200624Z-c74ab3` (model in `models/`)

| | Value |
| --- | --- |
| Failure rate train / calibration / test | 23.5% / 17.7% / 23.6% |
| Chosen C (family-grouped CV log-loss) | 0.1 |
| **Random-split CV AUC** (train families) | **0.800** |
| **Family-grouped CV AUC** (train families) | **0.494** |
| Test AUC, calibrated logistic (unseen families) | **0.484** |
| Test AUC, depth-3 tree (secondary, exploratory) | 0.769 |
| Test Brier vs constant base rate | 0.189 vs 0.180 |
| τ chosen on calibration families | 0.25 |

**Policies on the unseen test families** (299 conversations × 2 Nano samples, every call billed):

| Policy | Task acc. | $ / 1k tasks | p95 | Super calls | Nano errors shipped |
| --- | --- | --- | --- | --- | --- |
| always_smallest | 76.4% | $0.066 | 3555 ms | 0% | 141 |
| validator (RouteWise) | 95.7% | $0.128 | 4812 ms | 22% | 18 |
| risk + validator | 95.8% | $0.131 | 4786 ms | 25% | 17 |
| risk only | 82.8% | $0.087 | 3477 ms | 10% | 101 |
| oracle (not deployable) | 99.0% | $0.119 | 3564 ms | 24% | 0 |
| always_strongest | 99.7% | $1.010 | 1208 ms | – | 0 |

The p95 figures include a Nano latency spike in sample 1, where its p95 was 17 s.

**Hypotheses**

- **H3a, not supported.** The test AUC is 0.48, at chance.
- **H3b, supported, strongly.** Random folds overstate the AUC by 0.31: the predictor recognizes *families*, not difficulty.
- **H3c, met by the letter, practically negligible.** The accuracy gain is +0.17 points (95% CI 0.0 to +0.5) at 1.02× cost.
- **H3d, not supported.** The calibrated probabilities score worse than a constant guess on unseen families. The reliability bins are non-monotonic (observed failure rates of 41%, 12% and 66%).

**Decision (pre-registered): do not adopt.** The paired bootstrap CI's lower bound is 0.0, so it does not exclude zero.

**Why it fails: family shift.** The learned weights encode family-specific shortcuts.
- `n_relative_dates` has a strongly *negative* weight. In the training families, relative-date phrases appear in easy cases, such as "the replacement ships tomorrow" or "we'll email a label today". In the unseen test family `relative_refund_date`, relative dates are the core difficulty, and Nano fails 45% of them. The predictor therefore rates the hardest test family as low-risk.
- `multi_item_cue` is the top positive weight, largely because "two open orders" identifies the `two_orders` training family.

This is the leakage the experiment was designed to expose. It would have been invisible with a random split, where the AUC is 0.80.

**What already works.** On the same unseen families, the deterministic validator catches **87.2%** of Nano's wrong first answers (123 of 141), with 95.3% precision and a 1.3% false-positive rate. The `validator` policy therefore reaches 95.7% accuracy at 87% lower cost than always-Ultra. The remaining headroom up to the oracle is 3.3 points, and 12 of the 18 misses are in `partial_refund_distractor`: the known blind spot where the quoted price and the agreed partial refund both sit in refund sentences.

**Exploratory, not pre-registered.** The depth-3 tree's 0.77 test AUC suggests that a few threshold rules on amount and date counts may transfer better than a linear model. It was not the pre-registered model and comes from one test split, so it is a hypothesis for a future pre-registered run, not a result.

**Cost of M3.** $0.55 for the writers, $3.06 for verification, $0.16 for the two Nano passes, and $1.54 for the Super and Ultra passes: about **$5.3** in total.
