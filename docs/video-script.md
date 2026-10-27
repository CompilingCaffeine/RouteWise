# Devpost video: a measured deployment decision

**Revision 2 · 1:40.32 · 1920 × 1080 · Neural narration and timed captions.**

[Watch the revised recording](video/routewise-devpost.mp4) · [Captions](video/captions.vtt) · [Recording details](video/README.md)

The script is shorter and more conversational, voiced by Microsoft Ava at its natural speaking rate. It reuses the October 26 local app footage, including the real Nebius Token Factory request captured then. No new inference request is implied. Historical study examples remain labelled RECORDED. The public app still needs deployment of the guided build.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:09 | A cheaper model, backed by evidence | Refund extraction looks simple, until the model picks the wrong date. RouteWise helps developers choose a cheaper model setup, backed by evidence. |
| 0:09–0:20 | Fix the missing date | Here’s a recorded synthetic example. The old route misses the refund date. RouteWise reads “this Friday” against the store’s timestamp, and gets April twenty-fourth. |
| 0:20–0:28 | Hold ambiguous answers for review | If the date is ambiguous, it holds the answer for review. You can inspect the source, raw response, and every check. |
| 0:28–0:50 | Set your requirements | Choose your accuracy and response-time targets. RouteWise compares NVIDIA Nemotron models on Nebius Token Factory, then picks the cheapest setup the evidence supports. On three hundred twelve synthetic conversations, this setup completed ninety-eight point four percent correctly without review, with no incorrect answers released. |
| 0:50–1:00 | Let the evidence decide | Need a faster response? The recommendation switches to Super. Ask for more certainty than the data supports, and it recommends nothing. |
| 1:00–1:12 | Inspect a real API call | This call was captured while recording. Nano leaves the date blank; RouteWise resolves it to April sixth. The trace includes the actual response time and every model-call charge. |
| 1:12–1:24 | See the projected cost | At a million requests a month, recorded inference costs project to about eighty dollars, versus three hundred forty-nine for Super. Hosting and human review cost extra. |
| 1:24–1:40 | Export the decision | Download the decision and integration recipe. These results use synthetic data. Independent human validation is still pending, and the latest noise test missed its response-time target. RouteWise shows those limits alongside its recommendation. |

Before submitting:

- Push/deploy the guided build and verify Start here and Live router in a fresh hosted session.
- Review pronunciation and playback, then upload the MP4 publicly to YouTube and insert its URL in Devpost.
- Keep NVIDIA Nemotron and Nebius Token Factory in Built With. Both are named aloud in the video.
- Keep representative synthetic results separate from the date challenge. Independent human validation remains pending, and the latest noise study missed its latency gate.
- Submit before **October 30, 2026, 10 a.m. Pacific**. See the [official rules](https://nebiusglobalaihackathon.devpost.com/rules) and [organizer’s video guidance](https://nebiusglobalaihackathon.devpost.com/updates/46205-how-to-build-a-winning-project), checked October 26.
