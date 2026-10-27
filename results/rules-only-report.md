# Rules-only retrospective diagnostic

Run: `rulesdiagnostic-20261026T193407Z-8b152d`. No fresh model calls.

This diagnostic was implemented after the synthetic data and M6 outcomes were already exposed. It is not an independent validation or a preregistered confirmation. Original M5/M6 results and the prepared human-label protocol are unchanged.

The fixed baseline uses explicit refund-intent rules, unique source candidates, metadata-derived contact date and the same issue-date analyzer and validation contract as the adopted profile. It asks for review when rules cannot select a unique value. No label or model answer reaches its extractor. It is a simple conservative baseline, not the best possible deterministic system.

## Challenge set

| Configuration | n | Correct without review | Review | Wrong releases | Task accuracy | Field accuracy | API cost / 1k |
|---|---:|---:|---:|---:|---:|---:|---:|
| Rules only | 367 | 60.2% | 39.0% | 3 (0.8%) | 90.3% | 98.4% | $0.000 |
| Adopted profile | 367 | 75.5% | 24.5% | 0 (0.0%) | 100.0% | 100.0% | $0.077 |
| Previous profile | 367 | 30.8% | 27.5% | 153 (41.7%) | 59.6% | 93.3% | $0.204 |
| Grounded Super | 367 | 28.3% | 33.8% | 139 (37.9%) | 59.9% | 93.3% | $0.353 |

## Representative set

| Configuration | n | Correct without review | Review | Wrong releases | Task accuracy | Field accuracy | API cost / 1k |
|---|---:|---:|---:|---:|---:|---:|---:|
| Rules only | 312 | 70.5% | 20.2% | 29 (9.3%) | 71.5% | 92.5% | $0.000 |
| Adopted profile | 312 | 98.4% | 1.6% | 0 (0.0%) | 100.0% | 100.0% | $0.080 |
| Previous profile | 312 | 97.4% | 2.6% | 0 (0.0%) | 99.4% | 99.9% | $0.103 |
| Grounded Super | 312 | 96.5% | 3.2% | 1 (0.3%) | 98.4% | 99.7% | $0.349 |

## Interpretation

On the 312 representative synthetic conversations, the adopted profile completed 98.4% correctly without review versus 70.5% for these rules, a descriptive difference of 27.9 percentage points. Rules sent 20.2% to review and released 29 wrong answers; the recorded adopted profile sent 1.6% to review and released no wrong answers. This supports inspecting where semantic selection helps on this dataset, not a universal claim that rules cannot work.

The 367-case date challenge is reported separately. Review is never counted as a correct autonomous completion. Task and field accuracy describe answerable conversations, including candidate outputs withheld for review; they must not be confused with correct autonomous completion.

Rules make no paid inference calls. Hosting, development and human-review costs are excluded for every arm. Local rule CPU time is retained in the artifact and is not comparable to historical network inference latency. Model outcomes are historical recordings on the exact same IDs, not simultaneous reruns.

## Reproduce and inspect

`routewise rules-baseline`

[Manifest and source/input hashes](../runs/rulesdiagnostic-20261026T193407Z-8b152d/manifest.json) · [Every rules-only outcome and review reason](../runs/rulesdiagnostic-20261026T193407Z-8b152d/outcomes.jsonl) · [Full metrics](../runs/rulesdiagnostic-20261026T193407Z-8b152d/summary.json) · [Diagnostic design](../experiments/rules-only-diagnostic.md)

Before a fresh confirmation, freeze this baseline and a separate design and collect independently labelled conversations. The current pilot handoff remains ready for human labelers and data; this diagnostic does not fill that gap.
