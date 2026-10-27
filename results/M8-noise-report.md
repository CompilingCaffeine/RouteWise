# M7 noise component: real-workload writing noise on synthetic conversations

**Decision: not shown robust under the preregistered rule.** Four of the five checks passed. N2 failed on one part only: the 4-second deadline. The clean copies of the same conversations failed that same deadline in this run. Noise changed no answer from the adopted profile on the representative set. On the date-challenge set it moved one conversation from correct to review, and it released no wrong answers on either set.

The internal study key is `m8`, and the product displays the study as M7. This is the noise component that the 2026-10-26 amendment added to [the protocol](../experiments/M8-workload-validation.md). It does not replace the human-labelled pilot. Nothing here is evidence about real conversations or human labels.

## What was tested

- **Order of events.** Each step was committed and pushed before the next existed:
  1. The amendment, with checks N1–N5 and their power (`e554473`).
  2. A dated correction to the misspelling detector (`e03adf8`).
  3. The reviewed noise profile (`bb30177`).
  4. The sealed noisy set (`0024f20`).
  5. The run, `valnoise-20261027T021106Z-4ef4da`.
- **The noise.** It was measured offline in all 1,386 eligible, locally de-identified conversations of the user's support mailbox. Only aggregate rates left the private folder. Lazare Herzi reviewed the profile and approved it by its SHA-256.
- **The base.** The 679 sealed M6 confirmation conversations (312 representative, 367 date-challenge). Each was rewritten once by a seeded channel. The channel never edits digits, codes, timestamps, speaker roles or System messages. Every rewrite passed the label invariants, so labels are unchanged.
- **The run.** The three M7 arms ran on every clean and noisy conversation: 4,074 requests in one randomized run. Every planned outcome was recorded.

### What the mailbox's noise looks like

Rates per message unless stated:

| Feature | Customers | Agents | Clean M6 text (customers) | Noisy set (customers) |
|---|---:|---:|---:|---:|
| No terminal punctuation | 63.8% | 62.1% | 6.3% | 53.5% |
| Spaced hyphen instead of a dash (share of dashes) | 67.1% | 55.8% | 4.6% | 67.8% |
| All-lowercase message | 14.7% | 0.5% | 1.0% | 13.7% |
| Lowercase message start (messages with another capital) | 11.3% | 0.2% | 0.9% | 12.1% |
| Dropped apostrophe (share of contractions) | 12.5% | 0.1% | 7.3% | 15.5% |
| Message split over several turns | 13.0% | 16.3% | 0.0% | 12.2% |
| Emoji or emoticon | 4.2% | 12.8% | 0.0% | 4.0% |
| Misspelling (share of words of 4+ letters) | 0.23% | 0.05% | 0.08% | 0.21% |

- **Structural noise dominates.** The channel made 2,313 edits. Most were dropped final punctuation (1,210), lowercase messages (330), split turns (322), emoji (197) and dashes (114).
- **Misspellings are rare in this mailbox**, so only 13 were injected. Only 9 conversations had a date or refund word edited.
- **This result therefore speaks mainly to structural and casing noise**, not to heavy misspelling.
- **Calibration** for every feature is in [`data/v6/MANIFEST.json`](../data/v6/MANIFEST.json).

## Representative set

There are 312 conversations. Each cell compares the clean copies with the noisy copies.

| Route | Correct, no review | Wrong answers released | Task accuracy (lower 95%) | $ / 1k | p95 | Deadline-miss upper 95% |
|---|---|---|---|---|---|---|
| **Adopted profile** | **98.4% → 98.4%** | **0 → 0** | 100% (99.1%) → 100% (99.1%) | $0.080 → $0.080 | 4.32 s → 4.88 s | 10.9% → 15.9% |
| Previous profile | 96.8% → 96.8% | 0 → 0 | 98.7% (97.2%) → 98.7% (97.2%) | $0.105 → $0.107 | 4.73 s → 5.13 s | 12.7% → 16.3% |
| Grounded Super | 96.5% → 96.2% | 0 → 1 | 98.4% (96.7%) → 98.1% (96.3%) | $0.346 → $0.347 | 2.75 s → 2.55 s | 4.1% → 4.1% |

### The preregistered checks

All apply to the adopted profile.

| Check | Result | Criterion | Pass |
|---|---|---|---|
| N1: wrong releases, noisy | 0 of 312; upper bound 0.86% | ≤ 2% | ✅ |
| N2: requirements, noisy | Task accuracy lower bound 99.1%, field accuracy 100%, no incomplete cost; **deadline-miss upper bound 15.9%** (39 of 312 over 4 s) | ≥ 95%, ≥ 95%, none, **≤ 5%** | ❌ |
| N3: noisy − clean | Correct without review +0.0 points (CI 0.0 to 0.0); wrong releases +0.0 (0.0 to 0.0); no discordant pair | ≥ −2; ≤ +1 | ✅ |
| N4: versus previous profile, noisy | Correct without review +1.6 points (CI +0.3 to +3.2); wrong releases +0.0 (0.0 to 0.0) | ≥ −2; ≤ +1 | ✅ |
| N4: versus grounded Super, noisy | −$0.267 per 1,000 requests (CI −$0.273 to −$0.260) | < 0 | ✅ |
| N5: challenge set, noisy − clean | Wrong releases +0.0 points (CI 0.0 to 0.0) | ≤ +2 | ✅ |

**Why N2 failed.** The deadline failed, and only the deadline. The clean copies, run interleaved in the same randomized order, also failed it (25 of 312 over 4 s; upper bound 10.9%). The cause is Nano serving latency on the day of the run:
- the adopted profile's Nano calls had a p95 of 4.5–4.8 s;
- 128 clean and 149 noisy Nano calls exceeded 3 s;
- M6's confirmation recorded a Nano p95 of 3.35 s on its own slow day.

The rule is applied as written, so the profile is not shown to be robust.

## Date-challenge set

There are 367 conversations, 90 of which require review.

| Route | Correct, no review | Review | Wrong answers released | $ / 1k |
|---|---|---|---|---|
| **Adopted profile** | **75.5% → 75.2%** | 24.5% → 24.8% | **0 → 0** | $0.077 → $0.078 |
| Previous profile | 29.7% → 30.2% | 28.6% → 27.0% | 153 → 157 | $0.194 → $0.195 |
| Grounded Super | 28.6% → 28.1% | 34.1% → 33.8% | 137 → 140 | $0.349 → $0.351 |

- **The adopted profile's one change.** One conversation (`v5c-00025`) went from a correct answer to review. Nano's answer to the noisy copy failed the schema check, and Super's escalated answer failed it too. No wrong answer was released in either condition.
- **The analyzer was unaffected.** The deployed issue-date analyzer agreed with the label on all 679 conversations in both conditions. Measured noise at this mailbox's rates did not disturb the deterministic date reading.

## Noise effect on each route (descriptive)

Each figure is the noisy minus clean difference, with a 95% paired conversation-bootstrap interval.

| Set | Route | Correct, no review | Wrong releases |
|---|---|---|---|
| Representative | Adopted | 0.0 (0.0 to 0.0) | 0.0 (0.0 to 0.0) |
| | Previous | 0.0 (−1.0 to +1.0) | 0.0 (0.0 to 0.0) |
| | Grounded Super | −0.3 (−1.9 to +1.0) | +0.3 (0.0 to +1.0) |
| Challenge | Adopted | −0.3 (−0.8 to 0.0) | 0.0 (0.0 to 0.0) |
| | Previous | +0.5 (−1.9 to +3.0) | +1.1 (−0.8 to +3.3) |
| | Grounded Super | −0.5 (−2.2 to +1.1) | +0.8 (−0.5 to +2.2) |

The model-only routes moved in both directions, as expected from sampling variation. The adopted profile's deterministic date normalization made its answers the most stable.

**Latency (post hoc, not preregistered).**
- On the representative set, the adopted profile's noisy copies were 218 ms slower on average, with a 95% CI of −4 to +443 ms. The noisy inputs are about 4% longer: split turns add a timestamp line, and emoji are added.
- Deadline misses were mostly unpaired. On the representative set, 38 conversations missed only when noisy and 24 only when clean; on the challenge set, the counts were 28 and 31.
- The pattern fits serving jitter more than an effect of noise. A small latency cost of noise is possible but not established.

## What this does and does not show

- **It shows** that, at the rates measured in this mailbox, writing noise did not change the adopted profile's answers or its safety on synthetic conversations. Wrong releases stayed at 0, as did paired differences in correct completions and in wrong releases.
- **It does not show** robustness to heavy misspelling. This mailbox misspells rarely, so only 13 misspellings and 9 label-word edits occurred.
- **It does not validate the profile on real conversations.** The prose is model-written, and the labels were reviewed by a coding agent. The human-labelled pilot remains necessary for any claim about real language.
- **The deployed profile is unchanged.** The 4-second deadline remains an open issue, independent of noise. It also failed on the clean copies here, and it passed narrowly in M6.
- **The detector correction.** Before the build, the first reviewed profile counted "heard" and "iphone" as misspellings. It was withdrawn and re-measured with a corrected detector. This happened before any noisy text existed, and is recorded as a dated amendment.

## Accounting

- **API usage.** The run used $0.788. The adopted profile had no request with incomplete usage. Nine comparator requests lacked a priced usage record and count as incomplete cost for those arms.
- **No model was used for measurement or generation.** The noise was measured and injected locally.

## Reproduce

```bash
routewise noise report --run runs/valnoise-20261027T021106Z-4ef4da   # no API calls
python -m pytest tests/test_noise.py
```

The seal (`data/v6/seal.json`) hashes every source module on disk at build time. That includes three modules the run does not use, which were then uncommitted in the working tree: the rules-baseline hunk of `cli.py`, `deployment.py` and `rules_baseline.py`. Their hashes may not match a later checkout. The modules that decide outcomes match their committed versions.
