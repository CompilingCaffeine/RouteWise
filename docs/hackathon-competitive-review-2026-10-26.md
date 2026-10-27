# RouteWise competitive review — October 26, 2026

RouteWise's strongest opportunity is to turn its measured routing system into an immediately understandable deployment decision for a specific user. Prioritize the current-profile demo, independent workload evidence and a clear adoption path before adding more routing mechanisms.

## Scope and confidence

The hackathon's [project gallery](https://nebiusglobalaihackathon.devpost.com/project-gallery) currently says it has not been published. This is a partial review of publicly discoverable project descriptions, not a ranking of the full field. Search visibility is a selection bias. Public descriptions can change before the deadline.

Reviewed public write-ups for JOULE, Tablecast, Mandat, Hakobu, FlowFit, BlackBox, BountyPilot, PolicyOS, Wornpage Projects, AEGIS, Noah Nvidia, ForgeXi and Postmortem. Their reported results below are author claims; competitor code, videos and live execution were not independently audited. ForgeXi explicitly targets the event, although its page does not display the same submitted-to section as most of the sample.

For RouteWise, reviewed README, Devpost draft, video script, experiment plans and reports, deployment profiles and relevant dashboard source. Opened the deployed Overview, Workload optimizer and Live router in a browser. This was a read-only review; no fresh model calls or new benchmark results were produced.

## What the judges are actually scoring

The [official rules](https://nebiusglobalaihackathon.devpost.com/rules) give equal weight to technological implementation, design, potential impact and quality of the idea. The [judging update](https://nebiusglobalaihackathon.devpost.com/updates/46204-here-s-how-judging-works) emphasizes complete workflows for Best Apps and Agents. Consequently, another experiment alone is unlikely to address RouteWise's biggest judging gaps.

The [organizer's submission advice](https://nebiusglobalaihackathon.devpost.com/updates/46205-how-to-build-a-winning-project) emphasizes a problem-led video, the functioning product, a specific audience, and spoken identification of Nebius and NVIDIA. Deadline: **Friday, October 30, 2026, 10:00 a.m. Pacific** ([event page](https://nebiusglobalaihackathon.devpost.com/)).

Best Apps and Agents remains a defensible fit: model selection and inference form the application workflow. Moving to Coding and Agentic Engineering merely because developers are the audience would be a weaker fit with its stated code-writing/execution/testing focus.

## Competitive signals that matter

| Project | What its public page presents | Implication for RouteWise |
|---|---|---|
| [JOULE The AI Compute Governor](https://devpost.com/software/joule-the-ai-compute-governor) | Direct conceptual overlap: efficient-model routing, escalation, a learned logistic-regression policy, quality-equivalence gates, cost per successful outcome and shadow exploration. Its creator says real Nebius/NVIDIA integration is not yet complete. | A cheap-first router is not distinctive by itself. Emphasize RouteWise's completed Token Factory measurements, matched fixed-model alternatives, confidence gates and held-out results. Do not infer that JOULE's learned policy fails from RouteWise's separate M3 result. |
| [Tablecast](https://devpost.com/software/tastecraft-qiahys) | Restaurant planning with usable exports, a short judge walkthrough, visible source checks, model comparisons and held-out evaluations. Its later audit distinguishes verified claims from actually correct claims. | This is a useful benchmark for combining evidence with product value. RouteWise needs a useful decision/output in the first minute, and must preserve the distinction between validator pass and correctness. |
| [Mandat on Nemotron](https://devpost.com/software/mandat-on-nemotron) | Budget-constrained errands, sandbox payment operations and explicit judge instructions; model roles are tied to steps in the user's mission. | Make the user outcome memorable. Show what a developer can now deploy, and the cost/quality tradeoff, before explaining experimental history. |
| [Hakobu](https://devpost.com/software/hakobu) | A narrowly defined Korea–Japan parcel workflow, role-specific Nano/Super/Ultra calls, an explicit model panel, and separate LIVE/MOCK/TABLE provenance. | Using three model tiers is already common. A focused audience and clear provenance are more useful differentiators than a larger model list. |
| [BountyPilot](https://devpost.com/software/bountypilot-nemikf) | A source-quoted listing reader with a rules baseline, showing a blocker the model finds that rules miss. | Prepare a similarly concrete answer to why the model adds value beyond deterministic extraction. Source membership alone does not prove the right value was selected. |
| [FlowFit — Verified Execution Infrastructure for AI Agents](https://devpost.com/software/flowfit-verified-execution-infrastructure-for-ai-agents) | A staged operational workflow and runtime telemetry; a LIVE label depends on a successful inference call. | Make route, provider, profile version, validation and review state legible in one trace. |
| [BlackBox: AI Repo Reverse Engineer & Agent Memory](https://devpost.com/software/blackbox-ai-repo-reverse-engineer-agent-memory) | Interactive repository maps, architecture investigation and downloadable context/audit outputs. | RouteWise competes for attention against immediately visual products. Its own useful visual is the cost–quality tradeoff with a clear selection and a source-backed correction. |
| [PolicyOS — Verify the Policy Before You Act](https://devpost.com/software/policyos-verify-the-policy-before-you-act) | Evidence-oriented verification, report exports, dated recordings and explicit live judge access. | Provide a reliable, clearly labelled recorded path alongside fresh inference. A recording demonstrates historical behavior, not current availability. |
| [Wornpage Projects — Nebius edition](https://devpost.com/software/wornpage-projects-nebius-edition) | A coherent task-to-tool loop with bounded repair and evidence review; its current public preview has AI execution disabled. | A working, easy-to-reach inference path can matter, but it must actually expose RouteWise's adopted profile. |
| [AEGIS: Secure AI Agent Cockpit](https://devpost.com/software/aegis-secure-ai-agent-cockpit) | Persistent memory, deterministic action gating and visible approval UI. | Deterministic guardrails are not unique in this field. RouteWise's distinction must be what it measures and how those measurements change deployment. |

Additional context: [Noah Nvidia](https://devpost.com/software/noah-nvidia) carefully distinguishes synthetic public behavior from recorded/live inference; [ForgeXi](https://devpost.com/software/forgexi) describes an evidence-driven coding loop while listing unfinished live submission gates; [Postmortem](https://devpost.com/software/postmortem-d8cpu4) also describes tiered Nemotron allocation. These reinforce the importance of demonstrable completion and the limited novelty of model tiering alone.

## RouteWise's current strengths and gaps

| Criterion | Defensible strengths | Largest opportunity |
|---|---|---|
| Technological implementation | Real Token Factory traces; complete call billing; matched grounded controls; preregistered confirmations; rejected learned policy; explicit uncertainty. | Align the hosted live paths with the adopted profile and demonstrate why model inference is needed beyond source parsing. |
| Design | Requirements controls, comparison table, source traces and monthly projections already exist. | Present one guided workflow; reduce the need to understand v1/M5/M6 and internal m6/m7 identifiers. |
| Potential impact | A concrete refund-extraction task and lower measured inference costs. | Independent labels, an identifiable user, and evidence of adoption value beyond a percentage saving. |
| Quality of the idea | Workload-specific qualification can select a fixed model or no configuration; rules replace expensive reasoning where justified. | Explain that decision clearly and substantiate how much extends beyond this particular refund contract. |

This is a qualitative assessment, not a predicted judge score or probability of winning.

## Priority 0: make the advertised system the system judges can try

**Finding:** the deployed Live router explicitly reproduces the original policy (`policy-20261025T171555Z-5ab287`), and its source loads the legacy config/policy flow. The Workload optimizer explores M5 and constructs its live calls from M5 options. M6's adopted date-normalization profile is available through the documented CLI; its results appear on the dashboard, but the two principal Streamlit live paths do not invoke that profile.

Evidence: `app/views/live_router.py:18`, `app/views/workload.py:46`, `app/views/workload.py:136`, `README.md:240`, `policies/deployment/m7-extraction.json`. The Devpost draft's line 9, saying both implementations are published in the demo, should be made precise.

**Recommended change:** make the adopted extraction profile the default Try RouteWise path. Keep historical policies explicitly labelled in an experiment/archive view. Display the active profile and validation run. Use the same execution path as the CLI rather than recreating recovery behavior in the UI.

**Done when:** a new visitor can run a supported relative date and see the normalized date, run an ambiguous expression and see review, and inspect raw model output, normalized output and final release status. Those behaviors should agree with the documented CLI. A fresh call must not be presented as a reproduction of a historical measured outcome.

## Priority 1: give judges a useful first minute

Create a default guided page with three stages: **choose requirements → inspect the measured choice → try the selected profile**. Put the existing cost–quality plot and matched comparisons near the decision. Move the full experimental chronology behind an evidence link while preserving every result and limitation.

Offer three labelled examples: a straightforward answer, a supported correction/escalation, and an ambiguous date requiring review. Recorded examples should carry the run and timestamp; live responses should carry live-call metadata. A deterministic validator passing is not a ground-truth guarantee.

Suggested positioning:

> RouteWise helps teams choose a lower-cost Nemotron configuration for refund extraction, using their labelled conversations, declared quality and latency requirements, and an auditable held-out comparison.

Suggested short line:

> Find the lowest-cost model configuration your workload evidence supports.

The current short line is already good. The surrounding experience should deliver it quickly. Avoid a promise that every request meets a quality target: the target is evaluated statistically on a workload, and the current evidence is synthetic.

## Priority 2: obtain independent evidence before expanding features

The largest credibility gap is already documented: all headline datasets are synthetic and agent-reviewed. The date grammar's writer-chosen paraphrase family has only seven usable cases. More model-generated examples from the same process will not close that gap.

Use the prepared [private workload-validation workflow](workload-validation.md). If lawful access to de-identified real conversations and the required people is available, prioritize the planned 50–100-conversation diagnostic pilot, with two independent human labelers and adjudication. Keep labelers blind to model outputs. This pilot informs a later confirmation; it does not establish a new 95% reliability claim.

If real conversations are unavailable, use independently human-authored conversations and label their provenance accurately. Seek varied wording, multiple amounts/order references, revised promises, missing facts and ambiguity without sharing the parser's expression inventory with authors. Keep development and fresh evaluation separate.

Add one short usability observation from an intended user: could they identify the choice, understand its caveats and run it in their own workflow? Record observed behavior and permissioned feedback; do not invent a testimonial or represent a demonstration as a deployment.

## Priority 3: answer the hard technical questions

**Why is an LLM necessary here?** The current mechanism combines model selection with substantial deterministic candidate extraction, metadata handling and date normalization. I did not find a reported full-task rules-only baseline in the reviewed experiments. A fair deterministic-only baseline using the same field contract would establish where semantic interpretation adds value. Give it a review option and report coverage, wrong releases and complete task correctness; otherwise it could appear safe by declining everything. Freeze any new baseline before testing it on fresh data. Do not rewrite past confirmations.

**What is routing adding beyond a carefully configured fixed model?** The M5 matched grounded comparison already answers this better than an always-Ultra comparison. Lead with that evidence. Preserve the observed accuracy difference and the fact that one-point non-inferiority was not established. M6's normalization adds a separate benefit; describe it separately.

**Does this transfer to other tasks?** The public workload importer requires the six-field refund-extraction contract. Describe it as refund extraction today. If time remains after the critical work, demonstrate one genuinely separate extraction contract with its own scorer and fresh labels. That would support a broader platform claim; adding arbitrary JSON without task scoring would not.

**What happens after the demonstration?** Put the existing adopted-profile CLI recipe next to the result. Provide a downloadable, versioned deployment decision containing the requirements, selected profile, prompt/validator versions, price snapshot, source run and qualification scope. This is a small extension of existing artifacts, not a reason to build a new gateway or SDK before the deadline.

## Priority 4: make the impact numerically credible

From the deployed M5 calculator at one million requests/month: **$102.91 routed versus $339.47 grounded Super**, a projected **$236.56/month** reduction in inference usage cost. The roughly 70% reduction is meaningful, but the absolute amount puts a limit on a standalone cost-saving pitch. Hosting, labeling, development and review costs are excluded. Do not describe this as a customer saving.

M6 separately reports **98.4% correct without review**, **1.6% review**, **zero observed wrong releases** and about **$0.080/1,000** on 312 synthetic representative conversations. Its task-accuracy metric is 100%, which is not the same as 100% autonomous completion. Its p95 is 3.61 seconds, and its deadline-miss confidence gate only narrowly passes. The challenge set's 75.5% autonomous completion and zero observed wrong releases belong to a different, deliberately difficult mix.

The product case should combine measured inference economics, avoided incorrect outputs, visible review burden and reduced effort to make a model decision. Measure any human time saving before claiming it. Never blend M5 accuracy, M6 cost and a different study's latency into one headline configuration.

## Suggested remaining schedule

| Date, Pacific | Deliverable | Acceptance criterion |
|---|---|---|
| Oct 26 | Current-profile live entry point and consistent wording | Hosted default, CLI and submitted claims identify the same adopted extraction behavior. |
| Oct 27 | Guided example flow and deployment recipe | A fresh visitor reaches an answer, evidence and a usable next step within roughly one minute. |
| Oct 27–28, if people/data are available | Independent diagnostic pilot; rules-only comparison where feasible | Provenance, blind labels, paired outcomes and limitations reported. No rushed production claim. |
| Oct 28 | Evidence and impact summary | One matched cost/quality comparison, autonomous completion/review metrics and absolute projected savings are easy to understand. |
| Oct 29 | Record public YouTube video and complete submission | Under three minutes; actual product footage; audible Nebius/NVIDIA attribution; links tested in a fresh session. |
| Oct 30, before 10 a.m. | Submission buffer and access check | Demo, repository, license and video remain accessible. |

Run multi-day latency checks alongside the product work if practical; the M6 latency gate is close enough to make additional serving-day evidence useful. Do not let a new research program displace the working demo or video.

## Proposed three-minute story

| Time | Show |
|---|---|
| 0:00–0:20 | A refund conversation with an amount/date mistake that would create bad downstream data; name the intended developer/user. |
| 0:20–0:55 | The adopted profile's source evidence, correction and review behavior, with an honestly labelled recorded example if necessary. |
| 0:55–1:30 | Fixed Nano, grounded Super and the measured route; explain confidence qualification and the observed tradeoff. |
| 1:30–2:00 | A fresh call to the adopted profile; name NVIDIA Nemotron on Nebius Token Factory aloud and show the actual route/cost. |
| 2:00–2:30 | Change requirements to demonstrate a fixed-model choice or no qualifying option; identify this as exploration of recorded evidence. |
| 2:30–2:55 | Show the deployment recipe and concise evidence/provenance statement, including the synthetic-data limit or newly completed independent pilot. |

Do not depend on a stochastic live call failing in exactly the way the script needs. Use a real recorded trace for a particular failure/correction, labelled as recorded, and a separate fresh call to establish that inference works.

## Defer until after the submission

Broad multi-provider support, a new learned router without transfer evidence, a large frontend rewrite, always-on hedging without an actual deadline benefit, or Tavily added solely to pursue a bonus prize. Each competes with higher-value work on coherence and credibility. Keep the strong, specific platform feedback already in the submission draft; a small addition about onboarding and whether the team would use the tools again would complete that story.

**Recommended allocation:** fix the hosted profile mismatch first, then improve the guided product/pitch while independent labeling proceeds if available. RouteWise's most defensible distinction is an auditable model deployment decision backed by measurements that can also say “fixed model” or “insufficient evidence.”
