# Rules-only diagnostic

This is a retrospective diagnostic, implemented after the M6 results and synthetic data were already exposed. It is not a preregistered experiment, independent validation, or a modification to M5/M6. Do not use it to claim fresh generalization or to select a new production profile.

Question: how much of this six-field extraction task can a simple no-model system complete, and where does its review burden or wrong-release rate differ from the adopted profile?

`src/routewise/rules_baseline.py` uses source text only. It detects explicit customer refund requests, takes a unique order candidate and unique refund-context amount/currency, derives contact date from metadata, and applies the same issue-date analyzer and final validation contract as the adopted route. Multiple candidates, unclear intent, unknown date language and validation failures go to review. No reference labels or model outputs reach the extractor. This is a conservative baseline, not a claim to be the best possible deterministic parser.

Run `routewise rules-baseline`. The harness records source/input hashes before scoring the baseline on the public M6 confirmation conversations. It reports the challenge and representative sets separately, next to the three historical model arms on the exact same IDs. It checks arm coverage. All input conversations count in correct-autonomous, review and wrong-release rates. Review is never an autonomous completion. Field and task accuracy are also retained.

API usage cost for rules is zero; hosting and human review are excluded. Local rule CPU latency is not comparable to historical network inference latency. Historical model arms are not rerun and are clearly labelled as recordings. All output records, review reasons and scoring results remain inspectable.

Freeze the rules and compare them on independently labelled fresh conversations in a separate future study before treating the diagnostic as evidence of real-world model value. Keep the existing three-arm human-validation protocol unchanged; add any extra arm only under a separately declared design before new confirmation data exists.
