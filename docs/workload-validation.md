# Validating the adopted profile on your workload

This workflow tests the adopted extraction profile, displayed as M6 ([`policies/deployment/m7-extraction.json`](../policies/deployment/m7-extraction.json)), on conversations you supply. People you choose label them. The study is displayed as **M7**, with internal key `m8`. The protocol is [`experiments/M8-workload-validation.md`](../experiments/M8-workload-validation.md), and the labeling guide is [`data/validation-template/README.md`](../data/validation-template/README.md).

Every conversation runs through three arms:

| Arm | Route |
|---|---|
| Adopted profile (M6) | Grounded Nano → Super with deterministic issue-date normalization |
| Previous profile (M5) | Grounded Nano → Super ([`m6-extraction.json`](../policies/deployment/m6-extraction.json)) |
| Grounded Super | Super only, with the same prompt and grounding |

All three see identical conversations, in a randomized per-conversation order. Outcomes are scored against the human labels and use the M6 definitions:
- correct autonomous completions;
- review rate, split into necessary and avoidable;
- wrong releases;
- task and field accuracy;
- complete cost, including escalations;
- latency and 4-second deadline misses.

A review is never a correct answer, and every rate uses all requests as its denominator.

## What you must supply

Nothing in this workflow generates or checks labels with a model. The study cannot start until you provide the following.

1. **Decisions, before any pilot conversation is labeled.** Confirm or change them in Part A of the protocol.
   - The largest acceptable wrong-release rate. The default is 2%.
   - The non-inferiority margins against the M5 profile. The defaults are 2 points for correct autonomous completions and 1 point for wrong releases.
   - The existing requirements: 95% task accuracy, 95% field accuracy and a 4-second deadline.
   - That the field contract in the labeling guide matches your business, especially the `refund_date` rules and what requires review.
2. **50–100 development conversations for the pilot.** Sample them at random from a frame you can describe and repeat: source system, date window, inclusion rules. De-identify them and format them as the guide describes. Representative conversations are worth more than hard ones; the pilot estimates your real mix.
3. **People.**
   - At least two labelers and one adjudicator, trained on the guide and its illustrative rows only.
   - None of them may see model outputs, traces or dashboard results for these conversations, or use an AI system to suggest or check labels.
4. **Labels.**
   - Two complete, independent label files covering every pilot conversation.
   - A completed adjudication file for every conversation the importer queues.
5. **After the pilot:**
   - a description of the sampling frame and the labelers, to complete Part B;
   - a fresh held-out sample, drawn only after Part B is committed. It must be the size `propose` gives, come from the same frame, have no overlap with the pilot, and be labeled and adjudicated the same way.
6. **A Nebius API key** in `.env` as `NEBIUS_API_KEY`.
   - From the M6 measurements, the three arms together cost about $0.0005 per conversation: about $0.05 for a 100-conversation pilot, and a few dollars at most for a large confirmation.
   - Each run has a `--budget` guard. The default is $5.

## Steps

All files with conversation text stay under the git-ignored workspace, `private/validation/` by default; you can change it with `--workspace`.

```bash
# 1. Labelers each fill in a copy of data/validation-template/labeling-template.csv (CSV or JSONL).
routewise validation import --name pilot --labels labels-L1.csv labels-L2.csv
#    Agreement statistics are written to private/validation/data/pilot/agreement.json.
#    If labels disagree, the adjudicator completes data/pilot/adjudication_queue.csv:
routewise validation import --name pilot --labels labels-L1.csv labels-L2.csv --adjudication adjudicated.csv

# 2. Optional plumbing check on a few conversations. It is never a pilot and cannot inform the design.
routewise validation run --name pilot --smoke 3

# 3. The diagnostic pilot: every conversation, all three arms.
routewise validation run --name pilot
routewise validation report --run private/validation/runs/valpilot-...   # rerun at any time, no API calls

# 4. Read the pilot's evidence and propose the confirmation design.
routewise validation propose --run private/validation/runs/valpilot-...
#    Writes proposals/<run>/design.json and part-b.md. Replace each "[TO COMPLETE: ...]" item, then:
routewise validation preregister --proposal private/validation/proposals/valpilot-...
git add experiments/M8-workload-validation.md experiments/M8-workload-validation.design.json && git commit && git push

# 5. Only now draw, label and adjudicate the held-out conversations (split = heldout), as a separate dataset.
routewise validation import --name heldout --labels heldout-L1.csv heldout-L2.csv --adjudication heldout-adj.csv
routewise validation seal --name heldout
git add experiments/M8-workload-validation.seal.json && git commit && git push

# 6. The single preregistered confirmation run and its decision.
routewise validation run --name heldout --split heldout
```

An interrupted run continues with `routewise validation run --run <run dir>`. It fills only the missing outcomes.

## What each stage checks

- **`import`** rejects a file that breaks the format or the field contract, listing the file and row of each problem. It checks:
  - the message format and speaker roles;
  - that `contact_date` is the first Customer message;
  - that evidence quotes appear in the text;
  - that a review reason is present exactly when review is required;
  - that each conversation has two labels from two different labelers, with identical text and split;
  - for duplicate conversations.

  It writes the dataset only when every metric-changing disagreement has been adjudicated. It warns about likely personal data.
- **`run`** accepts 50–100 development conversations as a pilot. Held-out conversations run only after `seal`, and only once.
- **`propose`** accepts only a complete pilot run. It recomputes the report first, and if labels were revised after the run, it records that in the design.
- **`preregister`** refuses an infeasible design, an unfinished placeholder, or an existing Part B.
- **`seal`** requires:
  - Part B and the design to be committed;
  - the held-out labels to be built after the design was committed;
  - no overlap with any other dataset in the workspace;
  - at least the required number of conversations.

  It freezes the hashes of:
  - the held-out data, design and protocol;
  - the profiles, prompt and configuration;
  - every `src/routewise` module except the orchestration module.

  It also fixes the price snapshot. The confirmation run refuses to start if any of these changed, or if the public seal copy is not committed.

## Outputs

| File | Content | Shareable |
|---|---|---|
| `data/<name>/examples.jsonl` | Built labels with their provenance | No: conversation text |
| `data/<name>/MANIFEST.json`, `agreement.json` | Counts, label agreement (Cohen's κ), personal-data warnings, file hashes | Aggregates, plus labeler pseudonyms |
| `runs/<run>/traces.jsonl` | Every call, as recorded | No: extracted values |
| `runs/<run>/summary.json` | Per-arm metrics, paired comparisons, per-tag rates, wrong fields, review causes, issue-date analyzer coverage | Yes: aggregates only |
| `runs/<run>/diagnostics.json` | Every wrong release, avoidable review, arm disagreement and analyzer–label mismatch, by conversation | No |
| `proposals/<run>/design.json`, `part-b.md` | The proposed confirmation design | Yes |

## What the pilot can and cannot tell you

**It can:**
- estimate your scenario mix, review prevalence and label agreement;
- show which fields fail and why requests go to review;
- show how often the issue-date analyzer meets wording it does not recognize;
- provide the paired discordance that the sample size depends on.

With 80 conversations, a failure mode that affects 3% of requests appears at least once with 91% probability.

**It cannot:**
- establish that any requirement is met. Its bounds are too wide, and its conversations are development data from then on;
- serve as held-out data, even after the design changes.

If the pilot shows a problem, fix it as a new development step, which means a new profile and new evidence. Do not tune the frozen profile against the pilot and then confirm it.

## Sample size in brief

`propose` plans every check at 90% power, using 80% pilot confidence limits on the unfavourable side and halving any favourable paired difference. The required number is the largest of these. Then:
- the number to collect allows for the pilot's exclusion rate;
- the design lists the labels and adjudications needed and the expected API spend;
- it shows how the size changes under other wrong-release limits and margins.

If a check cannot reach 90% power at its planning value, the proposal recommends not starting the confirmation. One example is a pilot accuracy lower limit below 95%.

Small pilots with no wrong releases still imply large confirmations. With 0 of 60 wrong releases, the pilot cannot rule out a rate of about 1.2%. Demonstrating at most 2% then takes about 2,000 conversations, while at most 5% takes about 175. That is a property of bounding rare events, not of this tool. Choose the limit from the cost of a wrong release in your process.
