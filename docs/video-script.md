# Demo video: updated script and shot list

Target 2:55, maximum 3:00. Record the published app. Confirm the Workload optimizer shows `confirmation-20261025T212745Z-ac61b1` before recording.

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:20 | Workload optimizer | "If you're extracting refund information, the hard question is which model meets your application's quality and latency limits at the lowest cost. RouteWise measures that on your own labelled workload. The answer can be a fixed model, a route with escalation, or none." |
| 0:20–0:45 | Requirements at 95% task, 95% field, four-second deadline; complete table | "We compare every fixed model alongside grounded controls and routing. Here are the accuracy confidence bounds, deadline misses, latency and cost. The measured lowest-cost qualifying choice is source-grounded Nano followed by Super when validation fails, without hedging." |
| 0:45–1:05 | Monthly-volume calculator; change deadline to show another choice or abstention | "Cost is shown at a volume you choose, so the saving is concrete. Tightening a requirement can change the choice or make every option fail. Editing these recorded results is exploration; a new deployment needs a fresh held-out test." |
| 1:05–1:30 | Grounded configuration → source-evidence trace | "Nano used to drop order-ID prefixes or treat a product name containing a digit as an identifier. Now it selects complete source candidates, keeps null when no amount is stated, and derives the first customer date from message metadata. The raw model output and normalization are visible." |
| 1:30–1:55 | Live inference with an order reference, explicit issue date and separate arrival date | "This is a live call to NVIDIA Nemotron on Nebius Token Factory. The issue date and arrival date are separate facts. The answer is displayed as soon as it's ready; if a hedge runs, final cost accounting includes both calls." |
| 1:55–2:20 | Experiments → M5, four matched comparisons | "These comparisons explain the choice. Nano alone misses the quality target. Escalation raises accuracy to 98.2 percent at about 70 percent below grounded Super's cost. Hedging adds 29 percent cost for a modest latency gain, so we leave it off at our four-second deadline. Relative-date failures still need review." |
| 2:20–2:45 | Use your labelled workload tab; JSONL format, frozen tuning choice and held-out workflow | "Import labelled conversations, declare requirements, benchmark the tuning split and freeze the choice before testing held-out data. Labels never reach the model. Every call and result can be downloaded for review." |
| 2:45–2:55 | GitHub: MIT license, README, protocol and runs | "The code, protocols and traces are public. Everything runs on Nebius Token Factory with NVIDIA Nemotron. Relative-date failures remain; real customer data and multi-day reliability checks are the next validation step." |

Before recording:

- Check the published app shows the complete M5 results and the four matched comparisons.
- Use a 1400-pixel-wide window and readable zoom; hide credentials and unrelated tabs.
- Practice the live call once; use a harmless synthetic conversation with distinct issue and arrival dates.
- Show the actual selected configuration and result; do not claim routing is necessary if a fixed model wins.
- Keep the clip under three minutes and upload it as a public YouTube video, as required by the hackathon.
