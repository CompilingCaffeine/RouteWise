# M5 development log, before production inference

1. Protocol, initial implementation and 600-item plan committed as `94c3551` before any v3 production benchmark.
2. Synthesis accepted 599/600. One tracking-distractor writer repeatedly added an unplanned "today"; it stayed rejected.
3. Reasoning Super and Ultra verified all 599 accepted conversations: 11 conversations had at least one disagreement; two had a joint field disagreement. Blinded review of all 11 dropped four unsupported labels (two product/SKU codes not identified as order IDs; two order-issue dates incorrectly labelled refund dates). No labels changed. See `data/v3/adjudication.jsonl`.
4. Final workload: 119 tune / 476 heldout, 595 distinct conversations, no exact duplicates of v1/v2 inputs.
5. Before any production model call, a **tuning-only** deterministic candidate-coverage check found seven missing order-ID candidates. Development inputs used "ordered under", "ordered with reference", or an opaque alphanumeric identifier in a refund request. The source parser was extended to handle these patterns and regression tests added. No held-out candidate coverage or Nano answers were inspected for this change. The revised intervention is fixed in the protocol before tune/heldout production runs.
