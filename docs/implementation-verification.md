# Hackathon-readiness implementation verification

Implemented locally on October 26, 2026. These changes have not been pushed or deployed to the public Streamlit app.

## Product changes

- Start here now leads with a real recorded date correction, clear refund and ambiguity/review example. Recorded provenance and source text are inspectable.
- Requirements exploration uses the adopted profile's representative M6 set, separately from the date challenge, and can select fixed Super or no configuration.
- Start here and Live router call the same adopted-profile builder as the documented CLI. The older paths are labelled Workload optimizer · M5 and Historical router · v1.
- The trace viewer distinguishes raw model output, normalized final output, and withheld candidates. It identifies incomplete usage billing.
- Deployment decisions include requirements, configuration, scope, source hashes, evidence run and recorded price snapshot. A CLI/Python handoff and pilot coordinator guide are included.
- The rules-only diagnostic, report and raw records are separate from all original confirmations. The submission draft and three-minute video script reflect the guided product.

## Verification

The complete offline suite passed **231 tests** (rechecked during the git/video cleanup), including the new adopted-profile UI flow, normalized issue date, ambiguity/review decision, stale-output clearing, fixed-model selection, abstention and deployment decision. All original tests remain passing. Whitespace/diff checks passed.

Browser verification ran against the local app with real Nebius Token Factory inference on illustrative synthetic conversations:

| Case | Observed outcome | Call evidence |
|---|---|---|
| Store message April 2: refund issued in four days, arrival April 10 | Nano returned a null issue date; the adopted route normalized it to April 6 and released the final answer after checks. | Request `909b672731704be8a4fd6628f897a81b`, 2026-10-26 19:39 UTC; 1 call, 1,580 ms, $0.000069 usage estimate. |
| Store message April 2: refund issued in three business days | The candidate remained held for human review with no single issue date. | Request `50f22d96c7464844acc62b9d38bb46e9`, 2026-10-26 19:40 UTC; 1 call, 1,030 ms, $0.000068 usage estimate. |

These two live checks establish functionality of the modified local UI; they are not a new accuracy or latency benchmark. Screenshots: [normalization](screenshots/adopted-live-normalization.png), [review](screenshots/adopted-live-review.png).

The offline [rules-only diagnostic](../results/rules-only-report.md) used all 679 previously exposed synthetic conversations, retaining challenge and representative results separately. It made no fresh model calls. On the 312 representative conversations, the simple rules baseline completed 70.5% correctly without review and released 29 wrong answers; the recorded adopted profile completed 98.4% correctly without review with zero observed wrong releases.

## Remaining human/external steps

Independent validation still needs de-identified conversations, two independent human labelers and an adjudicator. No independent labels, customer testimonial, or pilot outcome was fabricated. Use the [pilot handoff](validation-pilot-handoff.md).

The subsequent October 26 git/video cleanup produced the [1:40 recording](video/routewise-devpost.mp4) and [updated script](video-script.md). Deploy the reviewed changes, verify the public app in a fresh session, and upload the recording publicly to YouTube. No public deployment, external Devpost edit or video publication has been performed.
