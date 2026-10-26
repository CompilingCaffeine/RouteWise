# Recommended next experiments

These are proposals, not preregistered studies or new measured results. The completed confirmation is now displayed as M5; its original study key remains `m6`.

## 1. Recover relative dates without releasing wrong answers

**Completed as M6** (internal key `m7`): [protocol](../experiments/M7-relative-date-recovery.md), [report](../results/M7-report.md).
- **Outcome.** Deterministic issue-date normalization was selected on development data and confirmed on fresh conversations. Correct autonomous completions on the challenge set rose by +44.7 points, with no wrong answers released. The representative workload passed every gate, and the profile is adopted.
- **Still open.** The reasoning fallback was not confirmed. Its possible advantage, wording the parser cannot read, needs a larger writer-chosen-phrasing set than the 7 conversations that survived review.

The original proposal follows.

The selected route achieved 98.2% overall task accuracy, but all six errors came from relative refund dates: 12/18 correct in that family. Every final error was flagged by validation, including after Super escalation. The next useful question is whether we can resolve those cases cheaply and safely.

Use the exposed results for development only. Compare three approaches on separate development examples:

- The current grounded Nano → Super route, unchanged.
- Extract the refund issue expression and its message timestamp, then normalize supported expressions deterministically. Abstain on ambiguous expressions; never substitute the arrival event for the issue event.
- Invoke a reasoning-enabled fallback only when date validation fails, with the relevant source evidence and timestamp. Count its extra calls, latency and tokens.

Include month/year boundaries, leap days, multiple message anchors, corrected promises, business-day ambiguity, and distinct issue/arrival dates. Have people label ambiguous cases before seeing model answers. Keep development examples separate from the eventual held-out set.

Choose one candidate on development data and freeze it. Confirm just three arms on fresh conversations: current route, frozen candidate, and grounded Super. Use the same conversations and randomized arm order. Give the fixed control the same field contract and source information.

Keep two evaluation sets distinct:

- A relative-date challenge set, which tests the proposed mechanism and a declared minimum useful improvement.
- A representative workload set, which tests overall quality, cost and latency at the intended scenario frequencies. Do not pool an oversampled challenge set into a claimed production accuracy.

Report correct autonomous completions per incoming request, review/abstention rate, wrong answers released without review, full task and field accuracy, p95/deadline misses, and complete cost including fallback calls. Review is not a correct autonomous answer. Show both unconditional outcomes and accuracy among automatically released answers so that rejecting more requests cannot masquerade as improvement.

Before collecting confirmation outcomes, specify the primary endpoint, useful effect, safety/coverage gates, sample size, stopping rule and analysis. Use pilot variance and paired discordances to plan sample size; account for shared scenario structures. Keep the existing 95% task/field and four-second gates unless a real use case justifies different requirements. A one-percentage-point non-inferiority claim against Super needs its own justified margin and power calculation; the current study does not establish it.

## 2. Validate a realistic workload with independent labels

This is the largest evidence gap for the product claim. The current data is synthetic and agent-reviewed, and its family-cluster sensitivity interval reaches below the 95% target.

Start with a small diagnostic pilot of human-written or appropriately de-identified real support conversations. Ask independent reviewers to label them without seeing model outputs, adjudicate disagreements, and retain the ambiguous cases with an explicit field contract. A diagnostic pilot does not establish a 95% reliability claim.

Use the pilot to understand actual scenario frequencies, language diversity, review burden and labeling disagreement. Freeze requirements and the selected route on development data, then collect a separate held-out evaluation sized for the desired uncertainty. If customer examples are unavailable, call the evidence human-authored or independently reviewed synthetic data; do not describe it as customer validation.

## 3. Test hedging where it could change the decision

At the tested four-second deadline, hedging adds 28.8% cost for a p95 change from 2.44 s to 2.23 s, with the same observed accuracy. It is lower priority than date recovery and workload validity.

For a latency-sensitive use case, compare the frozen unhedged and hedged routes across several days and controlled concurrency levels. Run both within each time/load block and randomize their order. Measure deadline misses, p95/p99, errors, hedge activation, total charged tokens and cost. Report injected stalls separately from naturally observed provider behavior. Repeated requests help assess serving variability, but do not become new independent quality examples.

Choose the deadline from an actual product need, freeze the hedge delay on development measurements, and decide whether the extra cost buys a useful reliability improvement. Do not tighten the deadline after seeing the results just to make hedging win.

## Recommended order

Date recovery is done. Obtain independent labels next, then confirm on fresh data. Run a multi-day hedging comparison only if latency risk is material to the intended user. The goal is a defensible deployment decision with fewer, more informative comparisons.

Methods references: [NIST sample-size planning](https://www.itl.nist.gov/div898/handbook/prc/section2/prc242.htm) and [NIST randomized block designs](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm). The hackathon's [judging criteria](https://nebiusglobalaihackathon.devpost.com/) emphasize a coherent product and a credible case for a real audience; independent workload evidence supports those goals.
