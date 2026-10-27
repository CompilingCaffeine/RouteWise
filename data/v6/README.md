# M7 noise component: synthetic conversations with measured real-world writing noise

Protocol: the 2026-10-26 amendment to [`experiments/M8-workload-validation.md`](../../experiments/M8-workload-validation.md) (study `m8`, displayed as M7). Code: [`src/routewise/noise.py`](../../src/routewise/noise.py) and [`noise_study.py`](../../src/routewise/noise_study.py).

| File | Contents |
|---|---|
| `noise-profile.json` | Aggregate writing-noise rates per speaker role, measured offline in every eligible, locally de-identified conversation of the user's private support mailbox. A named person reviewed it and approved it by SHA-256 before publication. It contains counts, rates and a table of common misspelling pairs seen in at least 5 conversations. It contains no sentence or per-conversation value. |
| `base-profile.json` | The same measurement of the clean base set. |
| `examples.jsonl` | One noisy rewrite of each of the 679 sealed M6 confirmation conversations (`data/v5/confirm`), with the same labels. The ids end in `~noisy`, and the tag `base:<id>` names the clean original. |
| `edits.jsonl` | Every edit the channel made: conversation, message, role, kind, before and after. |
| `MANIFEST.json` | Counts, the seed, and calibration: each feature's workload, clean and achieved noisy rate. |
| `prices.json`, `seal.json` | The price snapshot and the seal, both written before any model call on noisy data. |

## How the noise is added

- **Never edited.** Amounts, numbers, dates written with digits, order references, all-caps codes, timestamps, speaker roles and System messages.
- **Misspellings** are non-words one edit away from the intended word, so a careful reader recovers it.
- **Other changes** come from fixed, meaning-preserving lists: dropped apostrophes, chat abbreviations and common grammatical slips. Others drop or repeat punctuation, change casing, repeat words, join sentences, or split one message into two turns a minute apart.
- **Rates.** Each feature is added at the rate measured in the mailbox, minus what the clean text already shows. Rates vary by writer.
- **Label words.** Words a date or refund reading may hinge on ("tomorrow", "Friday", "issued") can be misspelled or abbreviated like any other word. Such conversations are tagged `noise:label_word`.
- **Checks.** Every rewrite passed `noise.invariants` before the set was sealed.

## Limits

- The base prose is model-written, and its labels were reviewed by a coding agent, not by people.
- The rates come from one support mailbox, and only the listed features are modelled.
- Real-word errors, missing words, and tense or agreement errors are not modelled.
- This set tests robustness to measured noise. It is not evidence about real conversations.
