# Demo video: script and shot list (target 2:45, hard limit 3:00)

Record the screen at https://nebius-routewise.streamlit.app/ in a 1400 px-wide window. Each spoken line is roughly as long as its time slot.

| Time | Screen | Voice-over |
| --- | --- | --- |
| 0:00–0:20 | **Overview**, top of page | "Most teams pick one LLM and live with the trade-off: the strongest model overpays on easy requests, the cheapest ships wrong answers. RouteWise asks a narrow question with evidence: on *your* labelled workload, can a cheap NVIDIA Nemotron model do most of the work, with bad answers caught and fixed, inside declared quality and latency targets?" |
| 0:20–0:45 | **Benchmark** page: extraction chart, then the policy table | "We benchmarked Nemotron 3 Nano, Super and Ultra on Nebius Token Factory, priced from Token Factory's live pricing API. On refund extraction, Ultra is perfect but costs about fifteen times as much as Nano per task. The optimizer simulates every route from the recorded calls and picks the cheapest one meeting our pre-declared targets: Nano first, Super only when the validator rejects the answer." |
| 0:45–1:10 | **Live router** → pick an extraction example → **Route it** | "Here's a live request. Nano answers in about a second for a fraction of a cent. Every deterministic check passes, so we're done: one call." |
| 1:10–1:35 | **Replay** tab → Controlled fault injection run → RouteWise + hedging → pick `ext-014` (escalated: `order_id_in_source`) | "When Nano gets it wrong, the validator says why. Here Nano put the product name, 'Lumen desk lamp', in the order-ID field. That value isn't an order ID anywhere in the conversation, so RouteWise escalated once to Super, which got every field right. Both calls are billed." |
| 1:35–1:55 | Same tab → a **hedged** request (`ext-018`) | "And here the cheap model stalled. After 1.3 seconds, its own p95, RouteWise started Super in parallel and used the first valid answer: 2.5 seconds instead of 5." |
| 1:55–2:30 | **Held-out evaluation** (primary run), then **Overview → Evidence trail** | "On held-out data RouteWise met every accuracy target at 91% lower cost than always using Ultra. It missed our 4-second latency target, though, because Nano's endpoint had two short slow periods, and we report that as a failed hypothesis. We then added hedged escalation and tested it under controlled, identical stalls: without hedging p95 was 4.3 seconds; with it, 2.7 seconds, still 89% cheaper than Ultra." |
| 2:30–2:45 | GitHub README → `runs/` folder | "Everything runs on Nebius Token Factory with NVIDIA Nemotron 3, with JSON-schema outputs and live pricing. Thresholds and policy were frozen in git before testing, and every request has a raw trace in the repo. The whole experiment cost thirty-six cents." |

## Recording checklist

- [ ] Use the deployed app. The Live router allows 15 calls per session, so do a practice take first.
- [ ] Before recording, open **Replay** → *Controlled fault injection* → *RouteWise + hedging*, and check that `ext-014` (escalated) and `ext-018` (hedged) are there.
- [ ] Keep browser zoom at 100% and hide bookmarks. Light mode reads best on YouTube.
- [ ] Upload to YouTube as **Public** (or Unlisted if the rules allow), under 3:00, and paste the link into Devpost.
