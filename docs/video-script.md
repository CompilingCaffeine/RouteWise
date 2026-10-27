# Devpost video: savings first

**Revision 4 · 1:38.82 · 1920 × 1080 · Neural narration and timed captions.**

[Watch the new recording](video/routewise-devpost.mp4) · [Captions](video/captions.vtt) · [Recording details](video/README.md)

The opening states the value proposition and leads with 77.1% lower inference usage cost versus grounded Super, followed by 98.4% correct completion without review and zero observed wrong releases on the same 312-conversation representative dataset. Results precede methodology. Independent human validation for this existing dataset is complete, based on the project owner’s direct confirmation; its synthetic provenance remains explicit.

Only the opening footage was replaced in revision 4: the monthly extraction volume and projected costs/savings now appear at the top. Narration, timings, and all later scenes remain unchanged from revision 3, including its genuine recorded Nebius request. Historical study examples remain labelled RECORDED. Narration uses Microsoft Ava. The hosted app must contain this build before submission.

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:17 | 77.1% lower inference cost | RouteWise makes AI inference cheaper while enforcing your reliability requirements. In our benchmark, it cut inference cost by seventy-seven percent compared with grounded Super. That’s about eighty dollars instead of three hundred forty-nine per million requests. |
| 0:17–0:31 | 98.4% correct without review. Zero wrong releases. | On the same three hundred twelve synthetic conversations, ninety-eight point four percent were completed correctly without review, with zero wrong answers released. Independent human validation is complete for this dataset. |
| 0:31–0:49 | Benchmark first. Then route. | Not every request needs your most powerful model. We benchmark NVIDIA Nemotron models on Nebius Token Factory, using the same workload and declared accuracy and latency targets. Nano handles the easy cases; Super handles requests that fail validation. |
| 0:49–0:59 | Choose the cheapest qualifying setup | Tighten the response-time target, and the recommendation switches to Super. Ask for more certainty than the data supports, and RouteWise recommends nothing. |
| 0:59–1:10 | Correct dates. Hold ambiguous answers. | Reliability also means knowing when to stop. Here, the recorded route corrects a missing refund date from the store’s timestamp. An ambiguous date goes to human review. |
| 1:10–1:20 | Fresh NVIDIA Nemotron call on Nebius | Here’s a fresh call to Nebius Token Factory. You can inspect the model’s raw response, the final answer, the checks, and the full request cost. |
| 1:20–1:38 | Take the decision into your application | Download the chosen setup, requirements, and evidence. These savings cover inference usage; hosting and review cost extra. The dataset is synthetic, and a later noise test missed its latency target. RouteWise makes the savings, tradeoffs, and limits visible. |

Before submitting:

- Push/deploy the guided build and verify Start here and Live router in a fresh hosted session.
- Review pronunciation and playback, upload the MP4 publicly to YouTube, and insert its URL in Devpost.
- Keep NVIDIA Nemotron and Nebius Token Factory in Built With. Both are named aloud in the video.
- Use the matched 77.1% comparison for the adopted profile. Do not attach its reliability numbers to the original v1 91% saving, which belongs to a different experiment.
- Independent human validation is complete for the existing representative dataset ([scope](dataset-validation.md)); a separate fresh-customer pilot and more serving days remain future work. The later noise study’s latency failure remains disclosed.
- Submit before **October 30, 2026, 10 a.m. Pacific**. See the [official rules](https://nebiusglobalaihackathon.devpost.com/rules) and [organizer’s video guidance](https://nebiusglobalaihackathon.devpost.com/updates/46205-how-to-build-a-winning-project), checked October 26.
