# M4: Is validation worth it? (validator study)

Pre-registered 2026-10-25, before any judge call on held-out data.

## Question

Which validator catches enough wrong first answers to justify its own cost and latency? Specifically, does an LLM judge add recall, or does running it on every request erase the savings?

## Data

- **First answers:** Nemotron 3 Nano's first answers from the `always_smallest` strategy of the three cited held-out evaluation runs (`results/runs.yaml`). That is 186 answers over the 62 held-out examples, reported pooled and per run. Fault injection changes latency only, not content.
- **Escalation answers:** one Nemotron 3 Super pass over the held-out split (new benchmark run, Super only), so every validator can be simulated end to end.
- **Truth:** ground-truth labels from `data/v1`. A first answer is *wrong* if it isn't task-correct.

## Validators (treatments)

| Id | Validator | Added cost per request |
| --- | --- | --- |
| `none` | accept every first answer | 0 |
| `schema` | JSON parse + output schema only | 0 |
| `deterministic` | current rules (`src/routewise/validate.py`), unchanged | 0 |
| `judge_nano` | Nemotron 3 Nano judges the answer against the source with a rubric | one Nano call |
| `judge_super` | Nemotron 3 Super judges the answer | one Super call |
| `deterministic+judge_nano` | escalate if either flags | one Nano call |
| `deterministic+judge_super` | escalate if either flags | one Super call |

Judge prompts (`prompts/judge_*_v1.txt`) are written using **tuning-split** Nano answers only. They get at most one revision, then are frozen in git before any held-out judge call. Judges run reasoning-off with JSON-schema output, and every judge token is billed.

## Metrics (per task and pooled)

- error recall
- escalation precision
- false-positive rate
- correction rate (flagged-and-wrong answers that Super fixes)
- end-to-end task and field accuracy
- cost per 1k tasks (Nano + judge + escalation)
- judge latency p50/p95, which the judge adds to every request

## Hypotheses

- **H4a.** `deterministic` has escalation precision ≥ either judge.
- **H4b.** `judge_super` has higher error recall than `deterministic` on extraction.
- **H4c.** Running `judge_super` on every request costs more per 1k tasks than the escalations it avoids. Combined with `deterministic`, RouteWise stays cheaper than always-Ultra but loses more than half of its savings.

## Decision rule

The recommended validator is the cheapest one whose simulated end-to-end held-out accuracy meets the declared thresholds (`config/routewise.yaml`), as in the handoff's acceptance criterion.

## Limitations, declared in advance

- The 186 answers come from 62 distinct examples (three samples each), so they are not independent.
- Super's escalation answers come from a single pass.

## Judge design pass (tuning split): `validators-20261025T182020Z-a57d2a`

| Validator | Error recall | False-positive rate | $ / 1k tasks |
| --- | --- | --- | --- |
| deterministic | 100% (in-sample) | 2% | $0.081 |
| judge_nano | 88% | 40% | $0.171 |
| judge_super | 88% | 8% | $0.263 |

- **Nano judge:** its false alarms are spread across every field (refund_date 12, currency 8, refund_amount 7, ...). That is general unreliability, not a misreading a prompt can fix.
- **Super judge:** its 4 false alarms are definition disagreements, such as a wrong-colour item counted as `damaged_item`, and the full-refund amount in `ext-h09`.
- **Decision:** the allowed revision was **not used**. `judge_*_v1` prompts are frozen as committed before any held-out judge call.

## Results (held-out): `validators-20261025T182255Z-812767`

The 186 Nano first answers come from the three cited evaluation runs. Escalation answers come from `benchmark-20261025T182208Z-53fc8a`, a single Super pass over held-out.

| Validator | Extraction recall / precision / FPR | Extraction acc. | Classification acc. | $ / 1k tasks (all) | Judge latency p50 | Simulated extraction p95 |
| --- | --- | --- | --- | --- | --- | --- |
| none | – | 87.8% | 95.2% | $0.050 | – | 3984 ms |
| deterministic | 60% / 100% / 0% | 95.1% | 95.2% | $0.064 | – | 3984 ms |
| judge_nano | 67% / 18% / 43% | 93.5% | 100% | $0.178 | 457 ms | 5880 ms |
| judge_super | 67% / 83% / 2% | 95.9% | 100% | $0.230 | 775 ms | 4742 ms |
| deterministic + judge_nano | 87% / 22% / 43% | 95.9% | 100% | $0.181 | 457 ms | 5835 ms |
| deterministic + judge_super | 67% / 83% / 2% | 95.9% | 100% | $0.221 | 776 ms | 4742 ms |

Every error the deterministic rules caught, the Super judge also caught.

**Hypotheses**

- **H4a, supported.** Deterministic precision is 100%, against 83–87% for the Super judge and 18–20% for the Nano judge.
- **H4b, supported but marginal.** On extraction, the Super judge's recall is 66.7% against 60.0% for the deterministic rules: 10 vs 9 of 15 errors.
- **H4c, not supported.** The deterministic + Super-judge cascade keeps 78% of the savings versus always-Ultra ($0.221 vs $0.769 per 1k), not less than half. It costs 3.5× the deterministic route for +2.1 points of overall accuracy.

**Decision rule (as pre-registered): `none`.** Pooled over 186 answers, Nano alone meets the declared accuracy thresholds (extraction 87.8% ≥ 85%, classification 95.2% ≥ 95%). We report this as the rule's output.

The rule has two weaknesses, which we note without changing it:
- It uses point estimates. Nano alone scored 85.4% in the primary run, and its CI lower bound falls below 85%.
- It ignores latency. Any judge pushes the simulated extraction p95 over the 4 s ceiling.

A future rule should use a lower confidence bound and include the latency SLO.

**Observations, not pre-registered**

- The judges' value is concentrated in **classification**, where the deterministic rules only check the schema. Both judges caught all 3 classification errors, and the Super judge did so with no false alarms.
- A per-task validator choice (deterministic rules for extraction, Super judge for classification) is a hypothesis for a future pre-registered run. It is not a result.
