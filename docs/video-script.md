# Devpost video: a measured deployment decision

**Recorded October 26, 2026 · 2:52.52 · 1920 × 1080 · English narration and captions.**

[Watch the recording](video/routewise-devpost.mp4) · [Captions](video/captions.vtt) · [Recording details](video/README.md)

The footage shows the local guided Streamlit build with a real Nebius Token Factory request. The public hosted app still showed the older navigation when checked. The recording is edited from actual viewport captures with cuts and held views; narration is computer-generated. Historical examples are explicitly labelled RECORDED in the app; the fresh inference carries a LIVE request identifier.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:15 | A measured model choice | For a developer extracting refunds, a plausible answer can quietly become bad data. RouteWise helps choose the lowest-cost NVIDIA Nemotron configuration that the workload evidence supports, using Nebius Token Factory. |
| 0:15–0:33 | Recorded evidence: correct the issue date | This is a real recorded synthetic conversation. The previous route released a missing issue date. The adopted profile resolves the store’s statement from its own timestamp. It keeps the refund issue date separate from when the money arrives. The source and both outputs remain inspectable. |
| 0:33–0:49 | Ambiguity goes to review | When the conversation does not determine one calendar date, RouteWise holds the answer for human review. Every model call, raw answer, normalization, and validation check is visible. Passing a validator is not a guarantee of correctness. |
| 0:49–1:16 | Compare cost, quality and review | Declare quality and latency requirements, then inspect matched alternatives. The adopted profile combines grounded Nano, selective Super escalation, and deterministic date normalization. On three hundred twelve representative synthetic conversations, it completed ninety-eight point four percent correctly without review, with zero observed wrong releases. Review and task accuracy are reported separately. |
| 1:16–1:36 | The answer can be Super—or no recommendation | Tighten the deadline to twenty-five hundred milliseconds, and fixed Super becomes the measured choice. Require one hundred percent accuracy with a confidence bound, and no configuration qualifies. These controls explore recorded evidence. Changing a slider does not create a fresh confirmation. |
| 1:36–1:58 | Fresh inference on Nebius Token Factory | Now we run a fresh request through the adopted profile. This calls NVIDIA Nemotron on Nebius Token Factory using the same implementation as the command line. The trace shows the actual model output, final issue date, release status, latency, and usage cost across every call. Live answers can vary. |
| 1:58–2:23 | Make the tradeoff explicit | At one million requests a month, the recorded token mix projects about eighty dollars in inference cost for the adopted profile, versus three hundred forty-nine for grounded Super. Hosting and human review are excluded. A separate retrospective rules-only comparison completed seventy point five percent correctly without review, versus ninety-eight point four for the recorded adopted profile. |
| 2:23–2:52 | Take the decision into your application | Download the selected profile, requirements, price snapshot, and evidence hashes, then use the integration recipe. Release an answer only when the final-valid flag is true. The evidence is synthetic; independent human validation is still needed. The latest noise study preserved answers but failed its latency gate. RouteWise makes those limits visible alongside the decision. The code and complete evidence archive are open source. |

Before submitting:

- Push/deploy the committed build and verify Start here and Live router in a fresh hosted session.
- Review the complete video, including synthesized pronunciation; the source narration is in `video/narration.json`.
- Upload the MP4 publicly to YouTube and add the public video URL to Devpost. Verify playback while signed out.
- Keep NVIDIA Nemotron and Nebius Token Factory in Built With. Both are named aloud in the video.
- Keep correct autonomous completion separate from task accuracy, and representative results separate from the date challenge. Independent human validation remains pending; the latest noise study failed its latency gate.
- Submit before **October 30, 2026, 10 a.m. Pacific**. The [official rules](https://nebiusglobalaihackathon.devpost.com/rules) require a public YouTube video and functioning-product footage; the [organizer’s video guidance](https://nebiusglobalaihackathon.devpost.com/updates/46205-how-to-build-a-winning-project) asks for audible Nebius/NVIDIA attribution. These requirements were checked October 26.
