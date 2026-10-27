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

  It hashes the data, design, protocol, profiles, prompt, configuration and code. The code hash excludes the orchestration and privacy modules, which decide no outcome. The public copy of the seal, `experiments/M8-workload-validation.seal.json`, holds hashes only and is committed before the run.
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

As amended on 2026-10-26, before any data existed (see the amendment at the end).

**Where data lives.** Conversation text, labels, model outputs, traces and per-conversation diagnostics stay in two directories outside every git repository:
- an input directory for the label and adjudication files;
- an output directory for everything RouteWise writes, readable by its owner only.

The tool refuses either directory inside a git work tree.

**Console and dashboard.**
- The console shows counts, rates, hashes and paths only.
- Messages that may quote data go to a private log.
- The dashboard refuses to read a private output directory.

**What may be published.** Only these, each checked against every private value before it is written to the repository:
- the protocol and design;
- the hash-only seal;
- aggregate exports that a named reviewer approved by their SHA-256.

**What a published aggregate may contain.**
- Allowed: counts, rates, numbers, booleans, hashes, dates and fixed vocabularies.
- Withheld: per-tag cells below 10 conversations, and tags outside the guide's list.

**Rehearsal.** `routewise validation drill` exercises these protections on synthetic, sensitive-looking data before company data is loaded.

**Your responsibility.** The importer flags likely personal data, but de-identification before labeling is the user's responsibility.

## Limitations declared in advance

- Labels are truth under this field contract. If the business defines a field differently, the results do not transfer.
- Agreement between labelers is not accuracy. Two people can share a misunderstanding, and adjudication reduces this but does not remove it.
- Scenario frequencies are those of the sampling frame. Latency comes from the day of the run, and prices from the seal date.
- The study author wrote the guide, the illustrative rows and the design rule, but no data labels.

## Reporting

The report goes in `results/M8-report.md`, and the dashboard shows the study as "M7 · Workload validation". `results/runs.yaml` records `display_milestone: M7`. The deployed profile changes only on a separate, preregistered decision.

## Amendments

**2026-10-26: private mode.** Made before any conversation was supplied or labelled; no data, design or seal exists.
- **Storage.** The git-ignored in-repository workspace, `private/validation/`, is replaced by external input and output directories (see Privacy). Ignore rules alone do not stop a copy, a forced add or a deployment, so the workspace now lives outside the repository.
- **Exports.** Results leave the output directory only as reviewed, allowlisted aggregate exports. The run's `summary.json` stays private.
- **What the seal covers.** The seal's code hash now excludes `private_mode.py`, `validation_export.py` and `privacy_drill.py`, as well as `workload_validation.py`.

No arm, outcome, label rule, design rule or decision rule changed.

**2026-10-26: synthetic noise component.** Requested by the user and made before any pilot conversation was supplied or labelled; no pilot data, design or seal exists. This text was written before the noise profile was measured. No noisy conversation, and no model output on one, existed when it was committed. It adds a component and changes no arm, outcome, label rule, design rule or decision rule above. The human-labelled pilot and confirmation remain the only evidence about real language and real labels.

*Why.* Every completed study used model-written prose, which is cleaner than real support writing. This component asks a narrower question that needs no labeler: does the adopted profile stay within its requirements when the synthetic conversations carry the misspellings, grammatical errors and other noise measured in the user's own mailbox?

*Privacy and copyright.*
- Mailbox text never leaves the private root and is never sent to any model.
- `routewise noise profile` reads every eligible conversation of the private Intercom mailbox offline, after the same local de-identification as `routewise mailbox prepare`. It writes only aggregate counts and rates per speaker role.
- It also writes one table: misspelling pairs (for example recieve → receive) that occur in at least 5 conversations. A pair is kept only if its correction is a known word and neither side is a word of any redacted value or a listed proper name.
- A named person reviews the profile locally and approves its SHA-256. Only then does `routewise noise publish` copy it to `data/v6/noise-profile.json`.
- No sentence, phrase or per-conversation value is copied. The noisy text is the M6 synthetic text rewritten by rules.

*What is measured* (`src/routewise/noise.py`), per role (Customer, Agent):
- **Spelling.** The rate of non-word misspellings among words of at least four letters, by word length. Also the edit kinds and positions: deletion, doubled or other insertion, keyboard-adjacent or other substitution, transposition.
- **Grammar and wording.** Dropped apostrophes in 21 fixed contractions; 11 fixed chat abbreviations ("u", "pls", "thx", "tmrw"); 10 fixed grammatical errors ("could of", "alot", "your welcome", "more then"); repeated words.
- **Casing.** All-lowercase messages; lowercase message and sentence starts; lowercase "i".
- **Punctuation.** Missing terminal punctuation; "!!" and "??"; commas without a following space; straight versus curly apostrophes; spaced hyphens versus dashes; emoticons.
- **Structure.** Run-ons, meaning messages of at least 20 words with no internal sentence break. Messages split into several turns, meaning the same role writes again within 5 minutes.
- **Writer spread.** For each feature, the between-writer variance across conversations, by the method of moments.

Real-word errors (their/there), missing words, and tense or agreement errors are not detected or modelled.

*The noisy set.*
- **Base.** All 679 conversations of the sealed M6 confirmation set (`data/v5/confirm`): 312 representative and 367 date-challenge. Their labels and review dispositions are unchanged.
- **One rewrite each.** The seed is the first 8 hex digits of the committed profile's SHA-256.
- **Writers.** Each role in a conversation gets one writer, drawn from the measured spread (beta distribution). A feature is added only as far as the clean text falls short of the workload's rate.
- **Never edited.** Digits and every token containing one; all-caps tokens (currency codes, order prefixes); timestamps; speaker roles; System messages. No edit may remove the space next to such a token.
- **Misspellings** are non-words at edit distance 1. They come from the published table where it has the word, otherwise from the profiled edit kinds and positions.
- **Splits.** A split message's second part is one minute later, on the same date, and before the next message.
- **Label words.** Edits to words a date or refund reading may hinge on (month and weekday names, relative-time words, refund and issue cues) are allowed, because a careful reader still recovers them. They are tagged `noise:label_word` for a descriptive breakdown.
- **Invariants.** Every rewrite must pass `noise.invariants`: same roles and timestamps, same protected tokens, amounts and references, and unchanged System messages. Otherwise the build stops.
- **The label claim.** The clean label is the noisy label. This is a design argument checked by those invariants, not a human relabel.

*Arms and run.*
- The three arms of Part A, built by `profile_routers` with prompt `extraction_v4` and `config/grounded.yaml`.
- Every clean and noisy conversation goes through every arm: 1,358 conversations × 3 arms = 4,074 requests. This is one run, in an order randomized from the seal seed, at Part A's load (90 admissions per minute, 4 workers) and with a $5 budget guard.
- `routewise noise build` writes `data/v6/seal.json`: data, profile, base, code, prompts, profiles, dictionary and prices. It is committed before the run.
- There is one analysis, with no interim looks. Transport failures are outcomes.

*Outcomes.*
- The definitions are Part A's.
- The representative and challenge sets are analysed separately and never pooled.
- The clean and noisy versions of a conversation are paired.
- Intervals are two-sided 95% conversation-bootstrap intervals (4,000 resamples).

*Checks, fixed now.* All apply to the adopted profile unless stated.

| Check | Set | Criterion |
|---|---|---|
| N1 | Representative, noisy | Wrong-release one-sided 95% Wilson upper bound ≤ 2% |
| N2 | Representative, noisy | Task accuracy lower bound ≥ 95%, field accuracy ≥ 95%, 4 s deadline-miss upper bound ≤ 5%, no incomplete cost |
| N3 | Representative, noisy − clean | Correct autonomous completions lower bound ≥ −2 points; wrong releases upper bound ≤ +1 point |
| N4 | Representative, noisy | Versus previous profile: CAC lower bound ≥ −2 points, wrong releases upper bound ≤ +1 point. Versus grounded Super: cost per 1,000 requests upper bound < 0 |
| N5 | Challenge, noisy − clean | Wrong releases upper bound ≤ +2 points (M6's S1 limit) |

*Decision.*
- If every check passes, the adopted profile is reported as robust to the measured noise on synthetic conversations. Otherwise it is not shown to be robust, and the failing checks and their causes are reported.
- Neither result is a claim about real conversations or human labels.
- The deployed profile changes only by a separate, preregistered decision. A fix creates a new profile, which needs its own study.

*Power, stated in advance.* The sample size is fixed by the base set.
- **N1** passes only with at most 2 wrong releases out of 312. Its power is 93% at a true rate of 0.3%, 79% at 0.5%, and 40% at 1%.
- **N3's wrong-release margin** allows about one net noise-induced wrong release. At zero true effect, its power is 42% if 1% of pairs are discordant, and 24% at 2%. A failed N3 is therefore weak evidence of harm. A pass is reported with its interval.
- **N5** has power 97% at 1% discordant pairs and 77% at 2%.

*Descriptive, not gates.*
- Outcomes by family.
- Conversations with and without a label-word edit.
- The issue-date analyzer's agreement with the labels, clean versus noisy.
- The noise effect on each arm.
- Calibration: each feature's workload, clean and achieved noisy rate.

*Limitations.*
- The base is synthetic: model-written prose with agent-reviewed labels.
- The rates come from one mailbox (a test-preparation support inbox, not e-commerce refunds) and cover only the listed features.
- Features are drawn independently per writer, so no correlation between features is modelled.
- When the mailbox is profiled, its author roles and store timezone are not yet human-confirmed. Unconfirmed roles can shift rates between Customer and Agent.
- The channel only adds noise:
  - It cannot remove a feature the clean text already shows in excess.
  - Writer draws above the mean raise the achieved average slightly, and the build reports by how much.

*Reporting.* The report goes in `results/M8-noise-report.md`, and `results/runs.yaml` records `display_milestone: M7`.
