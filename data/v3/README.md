# Fresh M5 workload

Synthetic customer-support conversations written by Nemotron Super/Ultra from fresh seeded fact sheets. Labels are computed from those facts. These are **not human-written or real customer conversations**.

The plan and splits are fixed before any production inference. See `experiments/M5-grounded-inference.md` for identifier formats, new families, strict synthesis verification, blind semantic adjudication, confidence gates and limitations. `source.json` links to every writer and verifier record. `MANIFEST.json` pins accepted data and splits; `seal.json` pins the selected fixed comparator, prompts, prices and source before held-out inference.

Real customer samples and independent human/domain review are still needed to assess external validity. Duplicate conversations from old datasets must not be introduced into this test set.
