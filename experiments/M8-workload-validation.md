# M8: Independent workload validation (displayed as M7)

**Status:** Part A, committed before any pilot conversation was labelled. No data has been supplied yet. Part B, the confirmation design, is appended by `routewise validation preregister` after the pilot and is committed before any held-out conversation is drawn. Neither part may be edited after the data it governs exists; corrections are appended as dated amendments.

The internal study key is `m8`, and the product displays the study as **M7**. The completed studies are unchanged: M5 (key `m6`), M6 (key `m7`), the aborted key `m5`, and the original v1 headline. This study reads their deployment profiles and modifies nothing of theirs.

## Why

Every result so far comes from synthetic conversations:
- Nemotron models wrote them from fact sheets.
- A coding agent reviewed the labels.
- The study author fixed the phrase inventory.

M5's family-cluster sensitivity interval reaches below the 95% accuracy target. M6 confirmed date normalization only over a known expression inventory. Whether the adopted profile meets its requirements on real customer language, labelled by people, is the largest open question for the product claim.

## Question

On conversations from the user's own workload, labelled by independent people:
- Does the adopted profile, `policies/deployment/m7-extraction.json`, meet the declared requirements?
- How does it compare with the previous M5 profile, `policies/deployment/m6-extraction.json`, and with grounded Super on identical conversations?

## Field contract

The labeling guide is the contract: [`data/validation-template/README.md`](../data/validation-template/README.md). It restates:
- the prompt-v4 field rules;
- the M6 `refund_date` contract: store statements only, anchoring to the containing message, arrival dates excluded, the latest correction wins, and review when the statement does not fix one date;
- the general rule that review is required when a careful person cannot determine one value for some field.

Six review-reason codes classify why.

**Known before any data.** The deployed issue-date analyzer passes "soon", "shortly" and "no later than the 10th" as no issue date. The guide counts them as vague periods or bounds, so they require review. If such wording occurs, the adopted profile will release null and it will be counted as a wrong release. This was found by running the analyzer on the guide's examples, and it is recorded here rather than fixed, because the profile under test is frozen.

## Arms, frozen now

| Arm | Definition | SHA-256 |
|---|---|---|
| `adopted` | `policies/deployment/m7-extraction.json`: grounded Nano → Super, deterministic issue-date normalization | `2fabd9a1ecce…` |
| `previous` | `policies/deployment/m6-extraction.json`: grounded Nano → Super, deployed validator | `40127078efa5…` |
| `grounded_super` | Super only | — |

All arms use prompt `extraction_v4` (`dccc7e223d24…`) and `config/grounded.yaml` (`843f71ca945d…`).
- Profiles are run exactly as declared, through `profile_routers` in `src/routewise/evaluation.py`.
- Every conversation receives each arm once.
- The conversation order and each conversation's arm order are randomized from a recorded seed, which balances time and prompt-cache effects.
- Admission is paced at 90 requests per minute outside the request timer, with four workers.

## Outcomes per request

These are the M6 definitions, implemented in `recovery_metrics.py`:

- **Released.** The arm answers without review.
- **Correct autonomous completion (CAC).** Released, the label does not require review, and all six fields are correct.
- **Wrong release.** Released, and either the label requires review or a field is wrong.
- **Review.** Not released: *necessary* when the label requires review, *avoidable* otherwise.
- **Task and field accuracy.** Over answerable conversations, whether the final output is correct, released or not.
- **Cost.** Every call, including escalations. Unpriced calls make cost incomplete.
- **Latency.** Answer-callback time. A deadline miss is anything over 4 s, or no answer.

Every rate uses all requests as its denominator. Outcomes are scored against the labels at report time.

## Labels

- **Who labels.** Two people label each conversation independently, using only the guide and the conversation. They see no model output, trace or dashboard result for that conversation. They do not consult each other before both have submitted. They use no AI assistance.
- **Adjudication.** A third person adjudicates every metric-changing disagreement under the same conditions. The importer refuses to build a dataset while any such disagreement is open.
- **Exclusions.** The adjudicator excludes a conversation only when it cannot be used, never because it is ambiguous. Exclusions are counted.
- **No model labels.** No model-generated label is used, and no model verifies labels. That is the difference from M5 and M6.
- **Agreement.** Percent agreement per field, and Cohen's κ for review status, `refund_requested` and currency, are recorded before any model run.

## Part A — the diagnostic pilot

**Data.**
- 50–100 development conversations, sampled at random from a frame the user describes.
- They are labelled and adjudicated as above, before the pilot run.
- They are development data permanently: they are never held out.

**Purpose.** The pilot is descriptive. It is not powered for, and it makes, no requirement claim. It estimates:
- the scenario mix and review prevalence;
- label agreement and the exclusion rate;
- per-arm rates;
- the paired discordance between the adopted and previous profiles;
- the fields and causes behind wrong releases and reviews;
- the issue-date analyzer's agreement with the labels.

With 80 conversations, a failure mode that affects 3% of requests is observed at least once with 91% probability.

**Run.** A single run of all three arms on every pilot conversation, with `routewise validation run`. Smoke runs on a few conversations check plumbing only, and they cannot inform the design. If the pilot exposes a defect, fixing it creates a new profile, which needs its own development and confirmation. The frozen profile is not tuned against the pilot.

## Rule that turns the pilot into Part B, fixed now

`routewise validation propose` (in `validation_design.py`) applies this rule mechanically.

**Primary endpoint.** The adopted profile's wrong-release rate on held-out requests. Success requires a one-sided 95% Wilson upper bound of at most **2%**. A wrong release reaches a customer record without human review, so the default is stricter than the 95% accuracy requirement.

**Gates.** Every gate must pass for the adopted profile to count as validated.

| Check | Criterion | Default |
|---|---|---|
| Requirements | Task accuracy, one-sided 95% Wilson lower bound | ≥ 95% |
| | Field accuracy | ≥ 95% |
| | 4 s deadline misses, one-sided 95% upper bound | ≤ 5% |
| | Incomplete-cost requests | none |
| Versus previous profile | Paired CAC difference, two-sided 95% conversation-bootstrap lower bound | ≥ −2 points |
| | Paired wrong-release difference, upper bound | ≤ +1 point |
| Versus grounded Super | Paired cost difference per 1,000 requests, upper bound | < 0 |

**Why these margins.** M6's representative gate used −1 point for CAC and +1 point for wrong releases. Here the CAC margin is widened to −2 points, because a lost autonomous completion costs a review, not a wrong customer record. The safety margin stays at +1 point.

**Defaults are changeable before the pilot data exists.** The 2% limit and the two margins are business decisions. The user may change them by a dated amendment to this part before any pilot label exists. After the pilot, `propose` accepts other values, but Part B must then state each departure and its reason. Every proposal shows the sample size under other limits and margins.

**Planning values.** These are pilot estimates on the unfavourable side:
- one-sided 80% Wilson limits for the wrong-release rate, the deadline-miss rate, the answerable fraction and task accuracy;
- the 80% upper limit of each paired discordant proportion;
- favourable paired differences halved (development optimism, as in M6), and unfavourable differences kept whole;
- for cost, the pilot mean and standard deviation of paired differences.

**Sample size.**
- **One-sample checks.** The smallest n whose exact binomial power for the Wilson bound is at least 90% at that n and the next 25 sizes. Exact power saw-tooths in n.
- **Paired checks.** n = ⌈(z₀.₉₇₅ + z₀.₉₀)² × (q − Δ²) / (Δ + margin)²⌉, where Δ is the planning difference in the favourable direction and q is the discordant proportion.
- **Cost.** n = ⌈((z₀.₉₇₅ + z₀.₉₀) × sd / |mean|)²⌉.
- **Required accepted size.** The largest of these.
- **Size to collect.** The required size divided by (1 − the 80% upper limit of the pilot exclusion rate).

If any check cannot reach 90% power at its planning value, or needs more than 5,000 conversations, the proposal recommends not starting a confirmation. One example is a pilot accuracy limit below 95%. The same applies if the pilot's field accuracy is below 95%, or if any pilot cost is incomplete.

## Confirmation procedure, fixed now

- **Order of events.** Part B and its `design.json` are committed and pushed before any held-out conversation is drawn. Then the held-out conversations are sampled from the same frame, labelled and adjudicated as above, and built as a separate dataset.
- **Sealing.** `routewise validation seal` refuses if any of these hold:
  - the labels were built before the design commit;
  - any conversation overlaps another dataset;
  - fewer than the required n survived.

  It hashes the data, design, protocol, profiles, prompt, configuration and code. The public copy of the seal, `experiments/M8-workload-validation.seal.json`, holds hashes only and is committed before the run.
- **Run.** One run, with an order seed taken from the design hash.
- **Stopping rule.**
  - One analysis, at the sealed size. There are no interim looks, no extension, and no replacement of excluded conversations.
  - Transport failures are outcomes, and a resumed run fills only missing outcomes.
  - The run stops early only for an authentication or accounting failure, or the budget guard. An early stop is reported as an abort, with no claim.
- **Analysis.** `routewise validation report` produces:
  - the outcomes above;
  - paired conversation-bootstrap intervals (4,000 resamples), with primary-scenario cluster intervals as a sensitivity check;
  - descriptive tables: per tag, wrong fields, review causes, and analyzer coverage.
- **Decision.** If every check passes, the adopted profile is validated for this workload. Otherwise it is not, and the measured results are reported. Neither comparator is validated by this study; switching to one needs its own confirmation.

## Privacy

Conversation text, labels, traces and per-conversation diagnostics stay in the git-ignored workspace, `private/validation/`. Only these may be published:
- the protocol and design;
- the hash-only seal;
- aggregate `summary.json` results.

The importer flags likely personal data, but de-identification before labeling is the user's responsibility.

## Limitations declared in advance

- Labels are truth under this field contract. If the business defines a field differently, the results do not transfer.
- Agreement between labelers is not accuracy. Two people can share a misunderstanding, and adjudication reduces this but does not remove it.
- Scenario frequencies are those of the sampling frame. Latency comes from the day of the run, and prices from the seal date.
- The study author wrote the guide, the illustrative rows and the design rule, but no data labels.

## Reporting

The report goes in `results/M8-report.md`, and the dashboard shows the study as "M7 · Workload validation". `results/runs.yaml` records `display_milestone: M7`. The deployed profile changes only on a separate, preregistered decision.
