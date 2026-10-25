# M6: Confirmation with semantically bound issue dates

Preregistered before generation, production inference or inspection of M6 answers. This replaces M5 as a new experiment; it does not erase or repair M5's frozen dataset.

## Why M5 was aborted

A tuning-only audit after sealing M5 found that some writers replaced a planned refund **issue** date with when funds would **arrive/clear**. Both reasoning verifiers had accepted those labels under prompt v2. Prompt v3 more often returned null, so a semantically careful answer could score wrong. Examples: tune IDs v3-00004, v3-07000, v3-15008. M5 was stopped for invalid measurement before inspecting its heldout quality results. All completed calls, cost and the reason are preserved in its status.json; no successful hypothesis is claimed. This is a post-registration measurement abort, not one of the original planned stopping conditions. M5 does not count as confirmation, and its test set is not reused.

## New data and label controls

Seed 2026102518, data/v4. 440 entirely fresh fact sheets: 16 original families ×20, four new M5 families ×20, and two new date families ×20. Twelve development families contribute the first eight items each to tune (96 planned); all remaining examples and all test-only families are heldout (344 planned). Model writers alternate Super/Ultra.

For every non-null refund_date, the fact sheet now requires an **exact issue-date statement**, bound to the specific message and date expression: e.g. "The refund will be issued tomorrow" or "The refund was issued on May 10". The deterministic must-string verifier rejects substitution with an arrival date. Relative dates retain their message-specific anchor. A labelled order ID must also be explicitly identified in an order-reference context, preventing the product/SKU ambiguity found in M5. New test-only families cover (a) arrival date with explicitly no issue date, labelled null, and (b) distinct issue and arrival dates, labelled with the issue date. This deliberately constrains writing to make labels defensible; it is not a claim of natural customer coverage.

Every accepted conversation is checked by reasoning Super and Ultra using v2. **All** conversations where either verifier disagrees receive a blinded keep/drop review before Nano production inference. Drop unsupported or genuinely ambiguous labels; never relabel. Publish every decision. The coding agent is the reviewer, not a human expert. No old v1/v2/v3 source text may be duplicated. Rejected items stay rejected. Sample size is the accepted count; no outcome-driven replenishment.

Pre-production transport recovery amendment: Super returned HTTP 529 overload failures during verification. Retain these original records and retry only transport-failed verifier calls, sequentially at 30 RPM, for at most two additional rounds. Successful replacements establish label verification; original failures remain in cost/availability records. If either required verifier remains unavailable, do not build or benchmark the dataset. This recovery applies only to label verification, never to replacing production evaluation outcomes.

## Intervention and controls

The same source-grounding code, numeric source candidates, first-customer-date calculation and frozen 1,337 ms hedge delay as M5. Prompt v4 restores explicit ISO date/year instructions, preserves extraction of an order ID for replacement/repair requests, and explicitly distinguishes refund issue from arrival. Original v2 baselines remain unchanged.

Nine live arms: fixed v2 Nano/Super/Ultra; fixed v4 Nano/Super; current v2 Nano→Super; grounding-only v4 Nano→Super; hedging-only v2 Nano→Super; grounding+hedging v4 Nano→Super. Same two-by-two routed ablation and grounded fixed-model controls. No learned risk model or always-on judge is added.

## Frozen question, gates and uncertainty

Does grounding+hedging meet task accuracy ≥95%, field accuracy ≥95%, and p95 ≤4 seconds at lower cost than the cheapest qualifying fixed model selected on tune? Use one-sided 95% Wilson lower bound on task accuracy ≥95%, field accuracy point estimate ≥95%, and one-sided 95% Wilson upper bound on P(response latency>4s) ≤5%. Require paired bootstrap 95% cost-difference CI entirely below zero versus the frozen fixed comparator. Also report every fixed model and the cheapest one qualifying on heldout, descriptively. Do not imply accuracy superiority over Ultra.

At n≈344, 98% observed accuracy yields a one-sided lower bound around 96.3%, sufficient to test a 95% quality requirement; this is not powered for a one-point non-inferiority claim. Choose thresholds, comparator, code, data, price and prompt hashes before test. No test-guided revision or extension. If the intervention fails, report it. The optimizer may select a fixed model or abstain.

One primary outcome per unique conversation/arm. Three randomized, interleaved time blocks separated by 60 seconds; repeat 24 block-1 sentinel conversations in blocks 2 and 3 as a secondary analysis. Nine independent model samples per conversation. Bootstrap paired differences by conversation with all repeated answers together (4,000 resamples), report family-clustered sensitivity intervals, field errors, per-family results, per-window deadline misses, and sentinel stability. Wilson gates use only unique primary observations. Shared model-written families limit population inference.

Nano admission 90 RPM before request timing, Super/Ultra admission 240 RPM, at most eight workers and two calls per route. The actual answer callback measures deliverable response time; unused-call accounting completion is separate and all calls are billed. Failures and retries/backoff count. Resume only missing outcomes, never replace completed ones. Budget guard $10 per stage, expected new total below $5. Abort on authentication/accounting failures and report any other necessary abort explicitly.

## Product and limitations

Publish the complete measured comparison in Workload optimizer, retain the original v1 headline and M5 abort, update submission text/video script, and expose source evidence and confidence-aware selection for imported labelled workloads. The data is synthetic and model-written, not human/real customer data; explicit issue statements improve label validity but reduce language diversity. Independent human/domain review and multi-day real-workload measurements remain necessary. Three same-day blocks do not establish production availability.
