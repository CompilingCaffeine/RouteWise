# Independent validation pilot: coordinator handoff

**Status: independent human validation completed for the existing representative dataset; the separate fresh-customer pilot remains ready for people and data.** The project owner confirmed the former on October 26; see the [validation scope](dataset-validation.md). This handoff describes a new workload study, which is not claimed to have run as part of that existing-dataset check.

This handoff uses the existing [private validation workflow](workload-validation.md), [field contract and templates](../data/validation-template/README.md), and [three-arm protocol](../experiments/M8-workload-validation.md). It does not alter that protocol or add an arm after the fact.

## Coordinator checklist

1. Recruit two independent labelers and a third person to adjudicate disagreements. Keep every person blind to model outputs. Do not use AI to write, check or adjudicate labels.
2. Confirm the field contract and the business thresholds in protocol Part A before labeling: 95% task and field accuracy, four-second deadline, maximum wrong-release rate and non-inferiority margins. Record actual people, sampling frame and consent/authorization as appropriate for your source.
3. Randomly sample 50–100 representative development conversations from a documented source/date window. De-identify before distribution. If real conversations are unavailable, independently human-authored data can be used, but must be described as such. Do not pass authors the parser's expression inventory.
4. Put the identical, timestamped text into a separate labeling-template copy for each labeler. Each labeler fills their own fields, evidence and review disposition without consulting the other.
5. Keep inputs and results in separate private, unsynced directories outside Git. Rehearse `routewise validation drill` first. Do not upload conversations to the public dashboard or commit them to this repository.
6. Import both label files, resolve the generated adjudication queue, then run the three existing arms: adopted profile, previous profile, grounded Super.

```bash
export ROUTEWISE_PRIVATE_INPUT=~/routewise-private/input
export ROUTEWISE_PRIVATE_OUTPUT=~/routewise-private/output
routewise validation drill --output ~/routewise-private/drill
routewise validation import --name pilot
# Complete any adjudication queue, then import again.
routewise validation run --name pilot
routewise validation report --run valpilot-...
routewise validation export --run valpilot-...
routewise validation propose --run valpilot-...
```

Use the detailed guide for directory preparation, adjudication, export review and confirmation registration. Outputs include correct autonomous completions, necessary/avoidable review, wrong releases, field/task accuracy, all-call usage cost and deadline misses. Publish only reviewed aggregates; the pilot is diagnostic and does not establish a fresh 95% production reliability claim.

## Confirmation and the rules baseline

Freeze the design proposed from the pilot before collecting a separate fresh held-out sample. Do not reuse pilot conversations as confirmation evidence. If a rules-only arm is to be included in a future confirmation, declare that separately and freeze the baseline and analysis before new confirmation data exists. The current retrospective rules-only diagnostic is not part of the sealed three-arm protocol.

## One practical usability observation

Ask an intended developer, without coaching, to identify the selected configuration, explain its limitations, download the decision and run the recipe on synthetic input. Note completion time, where they got stuck, whether they understood review and whether they confused recorded output with live inference. Share permissioned observations, not a fabricated endorsement or unmeasured time-saving claim.
