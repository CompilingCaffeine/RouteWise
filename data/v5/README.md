# M7 relative-date workloads (displayed as M6)

Protocol: `experiments/M7-relative-date-recovery.md`. Labels and dispositions are fixed on fact sheets before a writer model sees them, and never edited to match a model answer.

| Folder | Generated | Contents |
|---|---|---|
| `dev/` | Before Part B, seed 2026102601 | 13 relative-date challenge families. Used to develop the analyzer and to choose between date normalization and reasoning fallbacks. |
| `confirm/` | After Part B, seed 2306332526, from the SHA-256 of `selection.json` | Two held-out sets that are never pooled: the `challenge` split (same 13 families, `v5c-*`) and the `representative` split (the 22 v4 scenario families at equal frequency, `v5r-*`). |

`selection.json` records the development replay and the selection rule's decision. `plan.json` records the sample size and the hashes of the frozen code and prompts. `confirm/seal.json` binds the frozen candidate to the confirmation data, splits and prices before any production call.

## Review-required labels

Three challenge families state an issue timing that does not determine one calendar date:
- business- or working-day offsets;
- ranges and bounds;
- "next <weekday>" and vague periods.

Their examples carry the tag `disposition:review`. No autonomous answer is correct for them, and their `refund_date` of null is a placeholder that is never scored. Every other example's `refund_date` follows the protocol's field contract:
- it is the store's issue date;
- relative expressions resolve from the containing store message;
- arrival dates are excluded;
- explicit corrections win.

## Quality control and limits

- Writer output must contain every planned string and nothing the fact sheet does not allow.
- Reasoning Super and Ultra re-extract every conversation with `prompts/date_contract_verifier_v1.txt`.
- Every disagreement is reviewed blind to production answers and kept or dropped (`adjudication.jsonl`). The reviewer is a coding agent, not a human domain expert.
- The prose is synthetic and model-written. The study author chose the phrase inventory, except in the paraphrase family, where the writer chooses the wording.
