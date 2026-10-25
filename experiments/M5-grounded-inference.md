# M5: Source-grounded extraction and live tail protection

Protocol fixed before any v3 production answer. This is a new experiment; the v1 primary result and thresholds remain unchanged.

## Motivation and correction

An audit of the two v2 Nano passes found 442 order-ID errors among 546 wrong answers. All 18 wrong Nano answers accepted by the deterministic validator on the M3 test split have only order_id wrong. The M3 note incorrectly inferred an amount-selection blind spot from the scenario-family name. This diagnosis uses exposed v2 data and is development evidence only.

## Intervention

Extraction v3 constrains order IDs to complete identifiers found in explicit order/purchase contexts (including "ordered under/with reference"), conventional ORD-/ORDER-/PO- tokens, or opaque alphanumeric identifiers with at least four digits in order/refund request messages. It constrains numeric amounts to source money candidates, uses null when there is no candidate, and computes the first customer date from message metadata. The model still selects the relevant order/amount and resolves refund semantics. Candidate presence is evidence of a possible identifier, not proof that it is the relevant order. Raw completions and deterministic normalization are retained. No family, writer, fact-sheet or expected-label metadata reaches inference.

The hedge delay is 1,337 ms, frozen from the original v1 tuning split, not optimized on v3. A failed answer escalates once to Super; a slow Nano call starts Super in parallel. Every call is billed, including the unused call. The answer callback fires when a valid answer is ready; accounting completion is recorded separately. Legacy synchronous callers wait for final accounting.

## Fresh workload and verification

600 fresh fact sheets, seed 2026102517: 16 existing families × 30 plus four new families × 30 (tracking distractor, superseded order, numeric order, product/date not an ID). Identifier formats include ORD-123, web_123, AB/123/UK, numeric-only, mixed alphanumeric, and SHOP-123-B. Super and Ultra alternate as writers. Labels come from fact sheets, never from Nano. Existing strict synthesis verification applies, with prefix-fragment recognition for compound identifiers.

Before generation, first 10 items in each of 12 development families are tune (120 planned); remaining items and all four original M3 test families plus all four new families are heldout (480 planned). Rejected writer outputs stay rejected. Two reasoning verifiers (Super and Ultra, prompt v2) check every accepted conversation. Every joint field disagreement requires a blinded keep/drop decision, with no Nano answer visible. No relabelling. Publish acceptance/drop rates and single-verifier disagreement counts. These are synthetic, model-written examples, not human or customer data. Human/domain review remains an external-validity requirement.

## Arms and controls

Nine arms, run live with independent model samples: fixed Nano/Super/Ultra with v2; fixed grounded Nano/Super with v3; current Nano→Super with v2; grounding-only Nano→Super with v3; hedging-only Nano→Super with v2; grounding+hedging Nano→Super with v3. The two-by-two routed ablation separates grounding and hedging. Grounded fixed-model controls receive the same evidence as the intervention.

Tune calls cover both prompt variants on Nano/Super and v2 on Ultra. Replay all nine options for tuning only. Freeze code/prompt/data/price/protocol hashes and choose the cheapest qualifying fixed model before heldout inference. No test-guided revisions, model substitutions, hedge-delay changes or stopping for a favorable result.

## Primary question and gates

Does grounding+hedging meet task accuracy ≥95%, field accuracy ≥95%, and p95 ≤4 seconds at lower cost than the cheapest qualifying fixed model? This stricter 95% task target belongs only to M5.

The primary verdict requires a one-sided 95% Wilson accuracy lower bound ≥95%, field accuracy point estimate ≥95%, and one-sided 95% upper bound on P(latency>4s) ≤5%. Cost must be below the fixed comparator chosen on tune, with paired 95% cost-difference CI entirely below zero. Also report the cheapest fixed model qualifying on heldout (descriptive selection), and whether routing is cheaper than it. Do not claim superiority in accuracy over Ultra. If no route qualifies, the product recommends none.

Primary: all unique heldout conversations, one outcome per arm. Randomized, interleaved arms within conversation. Seeded random conversation order, three time blocks with 60-second gaps. Repeat 24 sentinel conversations from block 1 in blocks 2 and 3; repeated outcomes are a secondary stability analysis. Per-model pacing: Nano 90 RPM; Super/Ultra 240 RPM; at most 8 task workers, and 2 calls per routed task. Nano load-generator admission occurs before the request timer so the experiment's conservative RPM budget does not masquerade as serving latency. Backup admission, retries/backoff and failures are included in observed response latency and cost completeness.

## Uncertainty and power

At n=480, 98% observed accuracy has an approximate one-sided Wilson lower bound of 96.7%, allowing a test of a 95% requirement. This design is not powered to establish a one-percentage-point non-inferiority margin against Super, or M3's 0.17-point predictor gain. There is no optional extension after seeing outcomes.

Report paired differences, conversation-clustered bootstrap 95% intervals (4,000 resamples), family-clustered accuracy sensitivity intervals, per-family outcomes, field errors, per-window latency/deadline exceedances, and repeated-sentinel outcomes. All arms for a conversation and all repeated answers travel together. Use Wilson intervals for unique-conversation absolute accuracy and deadline misses; they describe this workload, not arbitrary family shifts. The primary deployment verdict is a conjunction of the quality, latency and cost gates. Ablation comparisons are secondary and exploratory; do not hunt significant subgroups.

## Budget and limitations

Up to $10 of new API spend per stage guarded in the client, expected total below $10; abort on authentication errors or missing usage. A failed provider call counts as an incorrect request; do not silently drop it. The run is resumable without replacing completed outcomes. Three blocks on one day are not multi-day reliability evidence. Shared serverless endpoints and warmed caches reflect deployment conditions; prefix caching is recorded and not defeated. The workload is synthetic and model-written; novel families and identifier formats do not establish real-world validity.

## Product output

Show all fixed models and routed alternatives, the least-cost qualifying choice or explicit abstention, editable requirements evaluated without additional model calls, evidence/normalization traces, observed cost and deadline uncertainty, and downloadable source artifacts. Preserve the original primary headline and clearly label M5.
