# M6: relative-date recovery

**Decision: adopt deterministic issue-date normalization.** The preregistered primary criterion and all four safety gates passed.

The internal study key is `m7`, and the dashboard displays it as M6. Run `datesconfirm-20261026T133902Z-ab36f4` covered 679 fresh held-out conversations with three live arms each, in randomized order. Every one of the 2,037 planned outcomes was recorded. The candidate, its code and prompt hashes, and the sample size were frozen ([Part B](../experiments/M7-relative-date-recovery.md), commit `85b4dc5`) before any confirmation conversation existed. The data was sealed (commit `6914066`) before any production call. The completed M5 results are unchanged.

## Relative-date challenge set

There are 367 conversations in 13 families. 90 of them are deliberately ambiguous (business days, ranges, "next Friday"), and for those the correct outcome is review.

| Route | Correct, no review | Review (necessary) | Wrong answers released | Accuracy when released | $ / 1k | p95 | > 4 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current: grounded Nano → Super | 30.8% | 27.5% (0.0%) | 153 (41.7%) | 42.5% | $0.204 | 4.28 s | 23 |
| Grounded Super | 28.3% | 33.8% (0.0%) | 139 (37.9%) | 42.8% | $0.353 | 1.59 s | 2 |
| **Date normalization** | **75.5%** | 24.5% (24.5%) | **0 (0.0%)** | **100%** | **$0.077** | 3.31 s | 2 |

- **Primary endpoint.** Correct autonomous completions rose by **+44.7 points** (95% paired bootstrap CI +39.5 to +49.6). The criterion required a lower bound above +10. In 164 conversations only the candidate was right; there were none where only the current route was right. The family-cluster sensitivity interval is +22.6 to +67.9.
- **S1.** Wrong answers released changed by −41.7 points (CI −46.9 to −36.8), against a limit of at most +2.
- **Review volume barely moved.** The total changed by −3.0 points (CI −10.4 to +4.6). Every candidate review was necessary: the 90 ambiguous conversations. Every current-route review was avoidable.
- **Answerable conversations.** All 277 were correct (one-sided 95% lower bound 99.0%).
- **Versus grounded Super.** The candidate scored +47.1 points of correct autonomous completions (CI +42.0 to +52.0) and cost $0.276 less per 1,000 requests.

### By family

Each cell shows correct without review and wrong answers released.

| Family | n | Current route | Grounded Super | Date normalization |
|---|---:|---|---|---|
| boundary_relative (month/year ends, leap day) | 30 | 60.0% · 0 | 50.0% · 0 | 100% · 0 |
| week_offset | 30 | 0.0% · 20 | 0.0% · 20 | 100% · 0 |
| this_weekday | 30 | 0.0% · 30 | 0.0% · 25 | 100% · 0 |
| day_after_tomorrow | 30 | 0.0% · 4 | 0.0% · 0 | 100% · 0 |
| past_relative | 30 | 26.7% · 0 | 26.7% · 0 | 100% · 0 |
| customer_restatement | 30 | 40.0% · 1 | 23.3% · 0 | 100% · 0 |
| corrected_promise | 30 | 56.7% · 2 | 53.3% · 0 | 100% · 0 |
| issue_and_arrival | 30 | 100% · 0 | 100% · 0 | 100% · 0 |
| paraphrased_offset | 7 | 0.0% · 6 | 0.0% · 4 | 100% · 0 |
| arrival_only_relative | 30 | 93.3% · 0 | 93.3% · 0 | 100% · 0 |
| business_days (review required) | 30 | 0% · 30 | 0% · 30 | 0% · 0 |
| range_or_bound (review required) | 30 | 0% · 30 | 0% · 30 | 0% · 0 |
| next_or_vague (review required) | 30 | 0% · 30 | 0% · 30 | 0% · 0 |

The models often return null or the message date for a relative expression. The deployed validator then lets through anything its grammar cannot see. It releases null for business days, ranges, "next Friday", weekdays and week offsets ("in a week", "in two weeks"). "The day after tomorrow" is mostly sent to review, but four wrong answers of message date + 1 were released, because the validator reads "tomorrow" inside the phrase. Date normalization resolves supported expressions against the store message's own timestamp, ignores customer restatements and arrival dates, applies explicit corrections, and sends the ambiguous forms to review without another call.

## Representative workload

There are 312 conversations from the 22 v4 scenario families at equal frequency. The set is evaluated separately and never pooled with the challenge set.

| Route | Correct, no review | Review | Wrong released | Task accuracy (lower 95%) | Field acc. | $ / 1k | p95 | Deadline-miss upper 95% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Current: grounded Nano → Super | 97.4% | 2.6% | 0 | 99.4% (98.1%) | 99.9% | $0.104 | 3.64 s | 5.7% |
| Grounded Super | 96.5% | 3.2% | 1 | 98.4% (96.7%) | 99.7% | $0.349 | 1.70 s | 1.4% |
| **Date normalization** | **98.4%** | 1.6% | **0** | **100% (99.1%)** | 100% | **$0.080** | 3.61 s | **4.9%** |

- **S2.** The candidate cleared the deployment gates: task accuracy lower bound ≥ 95%, field accuracy ≥ 95%, deadline-miss upper bound ≤ 5% at 4 s, and complete cost. **The latency gate passed narrowly**: 9 of 312 requests exceeded 4 s, giving an upper bound of 4.9%.
- **Today's serving was slower.** Grounded Nano calls today had a median of 1.36 s and a p95 of 3.35 s, with 122 calls over 3 s. In M5 the figures were 0.96 s, 2.06 s and 5 calls. The current route would have failed the same deadline bound today (5.7%). Super latency was unchanged.
- **S3.** Correct autonomous completions changed by +1.0 point (CI 0.0 to +2.2), against a requirement of at least −1. Wrong releases changed by 0.0 (CI 0.0 to 0.0), against a limit of at most +1. Three requests the current route sent to review were answered correctly: two relative dates and one arrival-only case.
- **S4.** Cost per 1,000 requests versus grounded Super changed by −$0.269 (CI −$0.274 to −$0.264), against a requirement below zero. Versus the current route it changed by −$0.023 (CI −$0.034 to −$0.014), 23% cheaper, because date failures no longer escalate to Super. The escalation rate was 1.6%, against 10.6% for the current route.
- **The remaining 5 reviews** come from the deployed amount-context rule, outside this study's scope.

## How the candidate was chosen

Development used 92 fresh development conversations plus a replay of the 421 exposed M5 conversations. Part B in the protocol has the full tables.

- **Development accuracy.** Date normalization and the reasoning fallbacks (Nano, Super and Ultra, each with reasoning enabled) reached the same accuracy: 72.8–73.9% correct without review and no wrong releases. The analyzer resolved every development statement, so the reasoning call only confirmed it.
- **Latency.** Reasoning Nano failed the 4 s p95 criterion on the replay (5.19 s).
- **Cost.** Reasoning Super and Ultra were eligible but cost 1.5× and 3.5× more than normalization on the replay.
- **Rule outcome.** The preregistered rule chose the lowest-cost eligible route.

This confirmation did not test the reasoning fallback. Its potential advantage is wording that the parser cannot read. The one family designed to test that, writer-chosen paraphrases, ended with only 7 usable conversations, which is too few to evaluate it.

## Data validity

**Challenge set.**
- 390 fact sheets were planned, and 374 were accepted by deterministic verification.
- The paraphrase family accepted only 14 of 30: the Super writer kept writing "shortly".
- Every family other than paraphrase accepted all 30 planned sheets, more than the 25-per-family target. Part A analyzes every accepted conversation.

**Representative set.** 313 of 330 were accepted.

**Label review.**
- Reasoning Super and Ultra disagreed with a label in 63 conversations. All were reviewed blind to production answers, before any production call. Eight were dropped, never relabelled.
- Seven of the drops were paraphrases that turned out to be bounds or not to name one date ("by the day after tomorrow", "half a month from now", "once a fortnight has passed").
- One was a representative conversation in which the customer never asked for money back.
- The verifiers often called fixed relative offsets ambiguous ("in a week", "in 3 days"). Under the contract they are not, and those labels were kept. Readers who consider "in a week" approximate should discount the `week_offset` family.

**Who reviewed.** Writer models produced the prose, and a coding agent reviewed it. Neither is human or domain validation. The study author chose the phrase inventory, except for paraphrases. So this confirms the mechanism on fresh conversations over a known expression inventory, not on arbitrary customer language.

## Accounting

The confirmation's three arms used **$0.399**. Generation and label verification used $4.62 for development and confirmation data together. Development calls and the v4 fallback replay used $0.46. The total recorded M7 spend is **$5.48**, plus under $0.01 of development smoke tests. No production request had incomplete usage.

During development, Nano had a transient slowdown, and 23 of 92 development calls timed out. They were retried once, sequentially, and the originals were retained. No decision used development latency. Confirmation transport failures would have counted as outcomes; there were none.

## Reproduce

```bash
routewise recovery report --run runs/datesconfirm-20261026T133902Z-ab36f4
python -m pytest
streamlit run app/streamlit_app.py   # Experiments → "M6 · Relative-date recovery"
```

The adopted route ships as `policies/deployment/m7-extraction.json`. The M5 profile `m6-extraction.json` is retained unchanged.

<details>
<summary>Study identity and naming</summary>

The product displays this study as **M6**. Its preregistered key is `m7`, because the completed confirmation displayed as M5 has the key `m6`. Protocol and report filenames, run manifests and data paths (`data/v5`) use the internal key. The M5 report, data, runs and dashboard tab are unchanged.

</details>
