# Independent human validation: representative dataset

**Status: completed for the 312-conversation representative dataset used in the
submission.** The project owner confirmed this directly on October 26, 2026:
“independent human validation completed for this data set (I checked).”

This update applies to the existing M6 representative refund-extraction dataset:
the `representative` split in `data/v5/confirm/splits.json`, evaluated in
`datesconfirm-20261026T133902Z-ab36f4`. It is recorded in
[`data/v5/validation-status.json`](../data/v5/validation-status.json).

The conversations remain synthetic and model-written. Their original labels
were created from fact sheets and reviewed by an agent before the benchmark;
the owner's confirmation records subsequent independent human validation of
this existing dataset. Original run records, labels, scores, protocols and
preregistration chronology are retained unchanged.

This is not a claim that the separate fresh-customer pilot was run, that the
367-conversation challenge set was independently validated, or that new model
measurements were collected. The next external-validity step remains a separate
fresh customer workload and measurements across additional serving days. The
later noise study's failed latency gate remains reported.
