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
