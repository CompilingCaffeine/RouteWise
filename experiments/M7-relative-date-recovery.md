# M7: Relative-date recovery (displayed as M6)

Part A was committed before any development conversation was generated. Part B, appended below it, freezes the selected candidate and the confirmation sample size before any confirmation conversation exists. Neither part may be edited after the data it governs exists; corrections are appended as dated amendments.

The internal study key is `m7`. The product displays this study as **M6** because the completed confirmation, internal key `m6`, is displayed as M5. That study's protocol, `data/v4`, runs, report and dashboard view are the baseline here and are not reanalysed or modified.

## Why

The route selected in M5, grounded Nano→Super, answered 98.2% of 330 held-out conversations correctly. All six errors were relative refund dates ("in four days", "tomorrow"): both models returned null or the message date. The deterministic validator had already resolved the correct date and flagged every one, so they went to review. Development reading of the deployed validator shows three further gaps it cannot see:

- Expressions outside its grammar, such as "the day after tomorrow", "yesterday", weekdays and weeks. "Tomorrow" inside "the day after tomorrow" even resolves to the wrong date.
- Customer restatements, which add a second, later anchor that the validator also accepts.
- Statements that do not determine a single date, such as business days, ranges and "next Friday". The validator lets these through as null.

## Field contract for `refund_date`

This contract applies to labels, verification and the scoring of every arm.

1. `refund_date` is the calendar date on which the store issued or will issue the refund, as stated by the store (Agent or System message). Customer restatements never establish the date or a new anchor.
2. A relative expression resolves against the timestamp date of the store message that contains it.
3. A date on which money should arrive, appear, clear, post or reach the customer is not an issue date. If only an arrival date is given, `refund_date` is null.
4. When the store explicitly corrects or updates an issue date, the latest corrected statement wins.
5. **Review required.** No autonomous answer is correct when the store's issue statement does not determine one calendar date. This covers:
   - business- or working-day offsets, because no merchant holiday calendar is available;
   - ranges and bounds ("3-5 days", "within 3 days");
   - "next <weekday>";
   - vague periods ("next week", "by the end of the week");
   - two store statements that conflict without a correction.
6. Otherwise, null when no issue date is stated.

## Candidates

All candidates keep prompt v4, grounding, the same Nano and Super models, and at most two model calls per request.

- **Control:** the current grounded Nano→Super route with the deployed validator, unchanged.
- **D — deterministic issue-date normalization.** A source analyzer reads store messages and separates issue clauses from arrival clauses. It resolves supported expressions against the containing message's timestamp, applies explicit corrections, and marks unsupported, ambiguous or conflicting statements. After each model answer:
  - **Resolved:** `refund_date` is set to the analyzer's date.
  - **Ambiguous, unsupported or conflicting:** the request goes to review with no further call.
  - **No issue statement:** the model's value stands, except that an arrival-only date becomes null.

  Non-date validation failures escalate to grounded Super exactly as in the control, and the same normalization applies to Super's answer.
- **R — reasoning fallback on failed date validation.** R uses the same analyzer, but only as a validator. When Nano's answer fails a date rule, one reasoning-enabled call receives the conversation, the message timestamps and the analyzer's issue and arrival sentences. It returns `{status, refund_date, evidence}`. The answer is released only in two cases:
  - it agrees with an analyzer-resolved date; or
  - the analyzer found issue-date language it cannot parse, and the reasoning model states an issue date while quoting a store message verbatim.

  Known ambiguity (business days, ranges, next-weekday, vague periods) goes to review without a call. Non-date failures escalate to grounded Super as in the control. Nano, Super and Ultra, each with reasoning enabled, are the three R variants. Development data chooses at most one.

## Outcomes per request

A request is **released** when the arm returns its answer without review. The control and grounded Super release exactly when their final answer passes the deployed validator, which is the M5 interface behavior. A candidate releases when its final answer passes its validation and the analyzer has not required review.

| Outcome | Definition |
|---|---|
| Correct autonomous completion (CAC) | Released, the label does not require review, and all six fields are correct. |
| Undetected error | Released, and either the label requires review or any field is wrong. |
| Review | Not released. Reviews are *necessary* when the label requires review and *avoidable* otherwise. A review is never a correct autonomous answer. |
| Task / field accuracy | Among requests whose label does not require review: whether the final output, released or not, has all six fields correct. This continues the M5 metric. |
| Accuracy among released answers | CAC divided by released requests. |
| Cost | Every model call: primary, escalation and fallback, including failed calls that report usage. Unpriced calls make cost incomplete. |
| Latency | Answer-callback time, as in M5. Deadline miss means latency over 4 s or no answer. |

All unconditional rates use every incoming request as the denominator, so rejecting more requests cannot appear as improvement.

## Part A — development data and selection

**New development challenge set.** This is `data/v5/dev`, seed 2026102601: 13 families with 8 planned conversations each (104). Writers alternate Super and Ultra.

| Expected disposition | Family | Expression |
|---|---|---|
| Release with a date | `boundary_relative` | "tomorrow" / "in N days" across month ends, a year end and a leap day |
| | `week_offset` | "in a week", "in two weeks", "a week from today" |
| | `this_weekday` | "this Friday", "on Friday" within the same week |
| | `day_after_tomorrow` | "the day after tomorrow" |
| | `past_relative` | "was issued yesterday" / "N days ago" / "earlier today" |
| | `customer_restatement` | A store relative date, later restated by the customer |
| | `corrected_promise` | A store date, later explicitly corrected |
| | `issue_and_arrival` | A relative issue date plus a relative arrival date |
| | `paraphrased_offset` | The writer chooses the phrasing for an offset of N days. Deterministic checks exclude weekdays, ranges, business days, bounds and calendar dates. |
| Release with null | `arrival_only_relative` | Relative arrival only; the issue date is not confirmed |
| Review | `business_days` | "in N business days" / "working days" |
| | `range_or_bound` | "in 3-5 days", "in 2 to 4 days", "within 3 days" |
| | `next_or_vague` | "next Friday", "next week", "early next week", "by the end of the week" |

**Exposed M5 data, replay only.** This is `data/v4`: 330 held-out and 91 tuning conversations, with their recorded grounded Nano and grounded Super calls. New reasoning-fallback calls are made only where an R variant triggers.

**Label quality control is as in M5:**
- Labels come from fact sheets, never from model answers.
- Writer output is verified deterministically.
- Reasoning Super and Ultra re-extract each conversation with a contract verifier prompt that can return `needs_review`.
- Every disagreement is reviewed blind to production answers and kept or dropped, never relabelled.
- The reviewer is a coding agent, not a human domain expert.

The analyzer, the fallback prompt and the R validation rules may be revised while reading development conversations and development answers. No confirmation data exists during development.

**Selection rule.** A candidate is eligible only if both conditions hold:
- On the development challenge set, its undetected errors do not exceed the control's.
- On the replay of all 421 exposed v4 conversations:
  - its undetected errors do not exceed the control's;
  - its CAC is at least the control's minus 0.5 percentage points;
  - its p95 latency is at most 4 s.

Among eligible candidates, choose the highest development-challenge CAC. If several are within 3 percentage points of the best, choose the lowest replay cost per 1,000 tasks, then the lower p95. If none is eligible, stop and report a negative development result, with no confirmation. The decision and its inputs are written to `data/v5/selection.json`.

## Confirmation design, fixed now

Part B fills in only the sample size, using the formula below, and records the frozen candidate and source hashes.

**Data.** Confirmation data is generated only after Part B is committed. The seed is the first 8 hexadecimal digits of the SHA-256 of `data/v5/selection.json`, read as an integer. Two sets are kept separate in every analysis and never pooled:

- **Relative-date challenge set.** The same 13 families, with equal allocation and n from the sample-size formula. It tests the mechanism and the minimum useful improvement.
- **Representative workload set.** The 22 v4 scenario families at equal frequency: 15 planned per family, 330 in total. It uses the same construction as M5 (issue-date binding, explicit order references, the arrival families), with a new seed and new identifiers. Relative dates are about 1 in 22 requests. These are the frequencies of the declared synthetic workload, not measured customer frequencies.

Label quality control is the same as in development, completed before any production call. There is no replenishment: the sample size is the accepted count.

**Arms.** Three arms run:
- the control;
- grounded Super (prompt v4, fixed);
- the frozen candidate.

Every conversation receives each arm once, in a per-conversation random order. Challenge and representative conversations are interleaved in one shuffled sequence, so time effects are balanced. Pacing is as in M5:
- Nano admission at 90 RPM, outside the request timer;
- Super and Ultra at 240 RPM;
- at most eight workers.

**Primary endpoint.** On the challenge set, the paired difference Δ = CAC(candidate) − CAC(control), per incoming request. The minimum useful improvement (MUI) is **+10 percentage points**. In M5 the control answered 12 of 18 relative-date conversations autonomously. Recovering at least a third of the remaining shortfall is the smallest gain we consider worth an added code path; smaller differences could come from writer-phrase variation alone. The primary success criterion is a lower bound above +10 points for the two-sided 95% paired-bootstrap interval (4,000 resamples by conversation).

**Safety gates.** All gates must pass for adoption.

| Gate | Set | Criterion |
|---|---|---|
| S1 | Challenge | Undetected-error difference (candidate − control): 95% upper bound ≤ +2 points. |
| S2 | Representative | The candidate meets the deployment gates: one-sided 95% Wilson lower bound on task accuracy ≥ 95%; field accuracy ≥ 95%; one-sided 95% upper bound on 4-second deadline misses ≤ 5%; no incomplete-cost request. |
| S3 | Representative | Versus the control: CAC difference 95% lower bound ≥ −1 point, and undetected-error difference 95% upper bound ≤ +1 point. |
| S4 | Representative | Cost per 1,000 tasks versus grounded Super: the 95% upper bound of the difference is below zero. |

Adopt the candidate as the deployed extraction profile only if the primary criterion and S1–S4 all pass. Otherwise keep the current route and report the result as measured.

**Sample size.** The planning formula is:

n = ⌈1.5 × (z₀.₉₇₅ + z₀.₉₀)² × (q − δ²) / (δ − 0.10)²⌉

- δ = max(0.5 × Δ_dev, 0.15). Δ_dev is the selected candidate's development-challenge CAC difference against the control. It is halved to allow for development optimism.
- q = max(q_dev, δ + 0.05). q_dev is the development discordant-pair proportion.
- 1.5 is a design-effect allowance for shared family templates.

The confirmation uses 90% power at a two-sided α of 0.05. Each family receives clip(⌈n/13⌉, 15, 30) accepted conversations. Planned fact sheets per family are ⌈that/0.85⌉, allowing for writer rejection. If the cap of 30 binds, Part B reports the resulting power.

**Stopping rule.** There is one analysis, at the planned n. There are no interim looks, no extension and no replenishment. The study stops early only for an authentication or accounting failure, the API budget guard ($10 per stage), or an invalid measurement. An early stop is reported as an abort, with no success claimed. Transport failures are recorded outcomes. A resumed run fills only missing outcomes and never replaces completed ones.

**Analysis.**
- The unit is the unique conversation, with one primary outcome per arm.
- Paired differences use a conversation bootstrap. Family-cluster bootstrap intervals are reported as a sensitivity check.
- Per-family and per-set tables report: CAC, review rate (necessary and avoidable), undetected errors, task and field accuracy, accuracy among released answers, cost per 1,000 including every fallback and escalation call, p50/p95 latency, and deadline misses.
- The candidate is also compared with grounded Super on both sets. Those comparisons are descriptive, with paired intervals; no non-inferiority claim is made.

## Reporting and product

The report goes in `results/M7-report.md`, and the dashboard shows the study as "M6 · Relative-date recovery". `results/runs.yaml` records `display_milestone: M6`. All M5 artifacts, the original v1 headline and the M5 abort record are kept. The deployed extraction profile changes only on adoption.

## Limitations declared in advance

- All conversations are synthetic and written by models. Labels come from fact sheets, and a coding agent reviews disagreements; neither is human or domain validation.
- The study author fixed the phrase inventory, except in the paraphrase family. So the confirmation tests fresh conversations over a known expression inventory, not arbitrary customer language.
- Measurements come from one day of serving.
