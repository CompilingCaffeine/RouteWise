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
7. **Two private directories** on a machine you control, outside every git repository and every synced or shared folder: an input directory for the label files and an output directory for results. Run `routewise validation drill` there first, as described below.

## Private mode

Customer data never enters this repository. Every `routewise validation` stage reads from two directories that you choose, and each refuses to run if either is inside a git work tree:

| Directory | Holds | Who writes it |
|---|---|---|
| Input, `--input` or `$ROUTEWISE_PRIVATE_INPUT` | `<name>/labels/`: one CSV or JSONL file per labeler.<br>`<name>/adjudication/`: the adjudicator's completed files. | The coordinator |
| Output, `--output` or `$ROUTEWISE_PRIVATE_OUTPUT` | Built labels, traces with model outputs, per-conversation diagnostics, proposals, exports awaiting review, and logs. | RouteWise only |

The input and output directories must be separate, and neither may contain the other. RouteWise creates the output directory readable by you only, and every file it writes there is readable by you only. It refuses an existing output directory that other users can read or that it did not create.

**What stays private:**
- **The console** shows counts, rates, hashes and paths, never a conversation, label, ID or model output. Label problems are listed by file and row in `label-problems.txt`. Any other error may quote the data, so the console shows only its type, and the full message goes to `<output>/logs/`.
- **The dashboard** refuses to read a private output directory, even if `ROUTEWISE_ARTIFACTS` points at one. It never reads the validation's results.
  - Its *Use your labelled workload* tab is part of the public demo. Never upload customer conversations or labels there.
- **The repository** receives only:
  - reviewed aggregate exports;
  - the design and Part B;
  - the hash-only seal.

  Each is checked against every value in your private files before it is written:
  - conversation and order IDs, emails, phone and card numbers;
  - any run of 8 consecutive words that is unique to one private conversation.

  If any value matches, nothing is written.
- **Nebius Token Factory** receives the conversation text when the arms run, which is unavoidable for the study. Its handling of that data is governed by your Nebius account terms, not by this repository.

**What you are still responsible for:**
- De-identifying conversations before labeling. The importer only flags likely emails, phone numbers and card numbers.
- Keeping the input and output directories out of synced or shared folders.
- Never copying their files into the repository yourself. `leak-scan` looks for such copies.

### Rehearse first, with synthetic data

Run the drill before loading any company data. Give it a new directory next to where your real output will live:

```bash
routewise validation drill --output ~/routewise-private/drill
```

The drill sends nothing real anywhere.
- **What it generates:** for each run, fresh random sensitive-looking values: names, emails, phone numbers, Luhn-valid card numbers, street addresses, order IDs, labeler notes and adjudication reasons. These go into eight synthetic conversations, labelled by two pretend labelers who disagree twice.
- **What it runs:** the real command line, end to end. That includes a label file with errors and a smoke run against Nebius, which costs well under a cent. It also includes a forced unexpected failure, export staging and approval, and a leak scan.
- **What it checks (16 checks):**
  - no generated value reached the console, the repository or the export;
  - the dashboard refuses the output directory;
  - every private file is readable by its owner only;
  - the leak scan does find values that the drill plants in a scratch repository;
  - synthetic results can never be proposed from, sealed or published to the repository.

Any failed check means stop. Every step's console output is kept in `<drill>/console/` for inspection. Delete the drill directory afterwards.

## Steps

```bash
export ROUTEWISE_PRIVATE_INPUT=~/routewise-private/input     # outside every git repository
export ROUTEWISE_PRIVATE_OUTPUT=~/routewise-private/output

# 1. Each labeler fills in a copy of data/validation-template/labeling-template.csv.
#    The coordinator puts both files in $ROUTEWISE_PRIVATE_INPUT/pilot/labels/.
routewise validation import --name pilot
#    If labels disagree, the adjudicator completes output/data/pilot/adjudication_queue.csv.
#    The coordinator saves it in input/pilot/adjudication/, then:
routewise validation import --name pilot

# 2. Optional plumbing check on a few conversations. It is never a pilot and cannot inform the design.
routewise validation run --name pilot --smoke 3

# 3. The diagnostic pilot: every conversation, all three arms.
routewise validation run --name pilot
routewise validation report --run valpilot-...    # rerun at any time, no API calls

# 4. Share only reviewed aggregates (optional, see below).
routewise validation export --run valpilot-...

# 5. Read the pilot's evidence and propose the confirmation design.
routewise validation propose --run valpilot-...
#    Writes output/proposals/<run>/design.json and part-b.md. Replace each "[TO COMPLETE: ...]" item, then:
routewise validation preregister --proposal valpilot-...
git add experiments/M8-workload-validation.md experiments/M8-workload-validation.design.json && git commit && git push

# 6. Only now draw, label and adjudicate the held-out conversations (split = heldout), in input/heldout/.
routewise validation import --name heldout
routewise validation seal --name heldout
git add experiments/M8-workload-validation.seal.json && git commit && git push

# 7. The single preregistered confirmation run and its decision.
routewise validation run --name heldout --split heldout

# Before any commit while private data exists:
routewise validation leak-scan
```

`--run` and `--proposal` take a name inside the output directory, or a path in it. An interrupted run continues with `routewise validation run --run <run>`. It fills only the missing outcomes.

## Exporting aggregate results

Nothing in the output directory is meant to be shared. Results leave it only as a reviewed aggregate.

1. **Stage.** `routewise validation export --run <run>` recomputes the report and writes `exports/<run>/aggregate.json` and `REVIEW.md`.
   - **The allowlist:** counts, rates, numbers, booleans, hashes, dates and fixed vocabularies, such as arm names, field names, review-reason codes and the guide's scenario tags.
   - **Never included:** free text, or anything that describes a single conversation.
   - **Withheld:**
     - per-tag cells with fewer than 10 conversations (`--min-cell`, at least 5);
     - tags outside the guide's shared list.

   They stay in the private `summary.json`.
2. **Review.** A person reads every value in `REVIEW.md` and decides it may be public.
3. **Approve.** `routewise validation export --run <run> --approve <SHA-256> --reviewer "<role or pseudonym>"`:
   - publishes only the file with that hash;
   - re-checks it against the allowlist and the private data;
   - stamps it with the reviewer and date;
   - writes it to `results/M8-validation/<run>.json`.

   Nothing is committed: commit the file only if it should be public. Synthetic drill results are never written to the repository.

## What each stage checks

- **`import`** rejects a file that breaks the format or the field contract, listing the file and row of each problem in a private file. It checks:
  - the message format and speaker roles;
  - that `contact_date` is the first Customer message;
  - that evidence quotes appear in the text;
  - that a review reason is present exactly when review is required;
  - that each conversation has two labels from two different labelers, with identical text and split;
  - for duplicate conversations;
  - that synthetic drill conversations are not mixed with real ones.

  It writes the dataset only when every metric-changing disagreement has been adjudicated. It warns about likely personal data, by count.
- **`run`** accepts 50–100 development conversations as a pilot. Held-out conversations run only after `seal`, and only once.
- **`export`** stages an allowlisted aggregate and publishes it only with the reviewed hash.
- **`propose`** accepts only a complete pilot run on real data. It recomputes the report first, and if labels were revised after the run, it records that in the design.
- **`preregister`** refuses:
  - an infeasible design;
  - an unfinished placeholder;
  - an existing Part B;
  - any value from the private data.
- **`seal`** requires:
  - Part B and the design to be committed;
  - the held-out labels to be built after the design was committed;
  - no overlap with any other dataset in the output directory;
  - at least the required number of conversations;
  - real, not synthetic, data.

  It freezes the hashes of:
  - the held-out data, design and protocol;
  - the profiles, prompt and configuration;
  - every `src/routewise` module except the orchestration and privacy modules (`workload_validation.py`, `private_mode.py`, `validation_export.py`, `privacy_drill.py`), which decide no outcome.

  It also fixes the price snapshot. The confirmation run refuses to start if any of these changed, or if the public seal copy is not committed.
- **`leak-scan`** looks for every private value in this repository: tracked, untracked and git-ignored files (except virtual environments and caches), file names, and the diffs of every commit made since the private data first appeared. It names locations and kinds of match, never the values. The details go to a private log.

## Outputs

All of these are in the output directory, readable by you only.

| File | Content | Leaves the output directory |
|---|---|---|
| `data/<name>/examples.jsonl` | Built labels with their provenance | Never |
| `data/<name>/MANIFEST.json`, `agreement.json` | Counts, label agreement (Cohen's κ), personal-data warnings by conversation, file hashes | Only as allowlisted aggregates in an export |
| `data/<name>/label-problems.txt`, `adjudication_queue.csv` | Label errors and disagreements, quoting the labels | Never |
| `runs/<run>/traces.jsonl` | Every call, with model outputs | Never |
| `runs/<run>/summary.json` | Per-arm metrics, paired comparisons, per-tag rates, wrong fields, review causes, analyzer coverage | Only through a reviewed export |
| `runs/<run>/diagnostics.json` | Every wrong release, avoidable review, arm disagreement and analyzer–label mismatch, by conversation | Never |
| `exports/<run>/aggregate.json`, `REVIEW.md` | The allowlisted aggregate and its review sheet | `aggregate.json`, once approved by its hash |
| `proposals/<run>/design.json`, `part-b.md` | The proposed confirmation design | Through `preregister`, after a check against the private data |
| `logs/` | Error details and library log records, which may quote private data | Never |

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
