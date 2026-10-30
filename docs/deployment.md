# Use the measured refund-extraction profile

RouteWise currently supports a specific six-field refund-extraction contract. The adopted profile uses grounded Nemotron Nano, one Super escalation when validation fails, and deterministic issue-date normalization. It was confirmed on synthetic conversations. Independent human validation is now complete for the existing 312-conversation representative dataset, as confirmed by the project owner ([scope](dataset-validation.md)); a separate fresh customer study remains the next generalization step.

## Try it in one minute

1. Open **Start here** in the dashboard. Set **Monthly extraction volume** at the top to compare projected inference cost and savings.
2. Choose quality and latency requirements. The comparison uses the representative confirmation set, separately from the difficult date challenge. A fixed Super configuration or no qualifying configuration may be the result.
3. Inspect the recorded correction or review example, download the deployment decision and run a fresh synthetic conversation. The decision includes the selected profile, requirements, source run, price snapshot and source hashes. Editing requirements explores recorded evidence; it does not create a fresh confirmation.

## Install and call the adopted profile

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,dev]"
export NEBIUS_API_KEY="YOUR_KEY"

.venv/bin/routewise --config config/grounded-nemotron-3.5.yaml route \
  --policy policies/deployment/m7-extraction.json \
  --task extraction --json \
  --text '[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.
[2026-04-02 10:00] Agent: Your refund of $40.00 will be issued in four days. It should arrive on April 10.'
```

The issue date should resolve to April 6, from the **April 2 store message**, while the arrival date stays separate. Fresh model answers are stochastic; inspect the actual trace. “In 3 business days” requires review under this field contract because it does not determine one calendar date.

Read `final_output`, `final_valid`, `recovery`, `calls`, `cost_usd` and `cost_complete`. Use the final output in downstream automation **only if `final_valid` is true**. Otherwise hold the candidate for a person. A passed validator is not a correctness guarantee, and a missing usage charge is not zero cost.

**Models.** Nebius removes Nemotron 3 Nano and Super from Token Factory Serverless on November 9, 2026, and names Nemotron 3.5 Lightning and Nemotron 3.5 Super-VL as [their replacements](https://docs.tokenfactory.nebius.com/model-deprecations-november-2026). `config/grounded-nemotron-3.5.yaml` runs the adopted profile on those replacements and changes nothing else; Ultra is not retired and is unchanged. Every recorded result was measured on Nemotron 3 with `config/grounded.yaml`. The replacements have not been re-validated on this workload, so treat a fresh answer as a demonstration of the profile, not of the recorded accuracy. Refresh their prices with `routewise --config config/grounded-nemotron-3.5.yaml prices`, which writes only `config/grounded-nemotron-3.5-prices.snapshot.json`. That file started as a copy of the 2026-10-25 Token Factory snapshot, which already listed both replacements.

The profile name contains the internal study key `m7`; its product-facing study label is M6. Both the CLI and the dashboard's adopted live path call the shared `build_routers` implementation. The earlier M5 route remains at `policies/deployment/m6-extraction.json`, while **Historical router · v1** preserves the original demonstration.

## Integrate from Python

```python
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_dotenv
from routewise.deployment import adopted_router

load_dotenv(REPO_ROOT / ".env")
router = adopted_router(TokenFactoryClient(), REPO_ROOT)  # the live models; pass config= to choose others
trace = router.route("extraction", conversation)
if trace.final_valid:
    consume(trace.final_output)
else:
    queue_for_human_review(trace.final_output, trace.recovery)
```

Use the downloaded recipe if the measured choice is a fixed model instead. `--strategy always_super` uses the grounded Super comparator, without silently adding date normalization. No CLI recipe is issued when no option qualifies.

## Evidence and costs

The adopted profile's 312-conversation representative set recorded 98.4% correct autonomous completion, 1.6% review, zero observed wrong releases, about $0.080 per 1,000 requests and 3.61-second p95, all on Nemotron 3 Nano and Super. Its four-second deadline gate passed narrowly. These are workload-specific historical measurements, not production service guarantees. The separate 367-conversation date challenge must not be pooled into a production accuracy claim.

For M5, one million requests at the recorded token mix projects $102.91 routed versus $339.47 with grounded Super. The dashboard's adopted comparison uses M6 costs separately. All projections exclude hosting, development, labeling and human review.

The new [rules-only diagnostic](../results/rules-only-report.md) asks what a no-model parser can do on the exposed synthetic workload. It is retrospective, not independent validation. Follow the [pilot handoff](validation-pilot-handoff.md) for the next evidence step.
