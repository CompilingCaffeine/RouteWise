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
export OPENROUTER_API_KEY="YOUR_KEY"   # https://openrouter.ai/settings/keys

.venv/bin/routewise --config config/openrouter.yaml route \
  --policy policies/deployment/m7-extraction.json \
  --task extraction --json \
  --text '[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.
[2026-04-02 10:00] Agent: Your refund of $40.00 will be issued in four days. It should arrive on April 10.'
```

The issue date should resolve to April 6, from the **April 2 store message**, while the arrival date stays separate. Fresh model answers are stochastic; inspect the actual trace. “In 3 business days” requires review under this field contract because it does not determine one calendar date.

Read `final_output`, `final_valid`, `recovery`, `calls`, `cost_usd` and `cost_complete`. Use the final output in downstream automation **only if `final_valid` is true**. Otherwise hold the candidate for a person. A passed validator is not a correctness guarantee, and a missing usage charge is not zero cost.

The profile name contains the internal study key `m7`; its product-facing study label is M6. Both the CLI and the dashboard's adopted live path call the shared `build_routers` implementation. The earlier M5 route remains at `policies/deployment/m6-extraction.json`, while **Historical router · v1** preserves the original demonstration.

## Integrate from Python

```python
from routewise.client import ChatClient
from routewise.config import REPO_ROOT, load_dotenv
from routewise.deployment import adopted_router, live_config

load_dotenv(REPO_ROOT / ".env")
config = live_config(REPO_ROOT)  # config/openrouter.yaml unless ROUTEWISE_PROVIDER=nebius
router = adopted_router(ChatClient(base_url=config.base_url), REPO_ROOT, config)
trace = router.route("extraction", conversation)
if trace.final_valid:
    consume(trace.final_output)
else:
    queue_for_human_review(trace.final_output, trace.recovery)
```

Use the downloaded recipe if the measured choice is a fixed model instead. `--strategy always_super` uses the grounded Super comparator, without silently adding date normalization. No CLI recipe is issued when no option qualifies.

## Providers and pinned hosts

Nebius retires its serverless Nemotron 3 Nano and Super endpoints on November 9, 2026. Every archived result was measured there with `config/grounded.yaml`, which stays in the repository to reproduce those runs. Fresh requests use `config/openrouter.yaml`: the same prompt, grounding, escalation, date normalization, thresholds, seed and JSON schema, with OpenRouter model slugs and `reasoning: {enabled: false}` in place of Nebius's `chat_template_kwargs`.

OpenRouter can send each call to a different upstream host, and each host bills its own price. Each candidate therefore pins one host (`host:` in the config). The client sends `provider: {order: [host], allow_fallbacks: false, require_parameters: true}`, so a call is served by that host or fails visibly, and only a host that honours `json_schema`, `seed` and `temperature` is accepted. Every call records the host that answered (`served_by`) and OpenRouter's reported charge (`reported_cost_usd`), which `routewise probe` and `routewise provider-check` compare with `cost.py`.

| Role | OpenRouter slug | Pinned host | USD per 1M in / out | Nebius price used by the archived runs |
|---|---|---|---:|---:|
| Nano | `nvidia/nemotron-3-nano-30b-a3b` | `nebius/fp8` | 0.06 / 0.24 | 0.06 / 0.24 (fp8) |
| Super | `nvidia/nemotron-3-super-120b-a12b` | `dekallm/fp8` | 0.08 / 0.45 | 0.30 / 0.90 (fp4) |
| Ultra (baseline only) | `nvidia/nemotron-3-ultra-550b-a55b` | `deepinfra/fp4` | 0.50 / 2.20 | 1.00 / 3.00 (fp4) |

Prices come from `config/openrouter-prices.snapshot.json` (snapshot of 2026-10-30; never hand-typed). A host's price can differ from the model's listed price: Super lists at $0.30 / $2.40, which is another host's price.

**Nano host caveat.** On 2026-10-30 the only OpenRouter host that serves Nano with JSON-schema output and `seed` is `nebius/fp8`, at Nebius Token Factory's fp8 price. It may be retired together with Nebius's serverless Nano. The alternative, `novita/fp4`, supports JSON mode but not JSON-schema output. Using it would change the adopted profile's generation contract and would need its own validation.

To change hosts, run `routewise --config config/openrouter.yaml prices`. It lists every host with its price, quantization, JSON-schema and seed support and recent uptime, and it fails when a pinned host cannot serve the configured requests. Then edit `host:` and run `routewise --config config/openrouter.yaml provider-check` before relying on the result.

**Dashboard.** The live demo reads `ROUTEWISE_PROVIDER` (`openrouter` by default, or `nebius`) and the matching key, `OPENROUTER_API_KEY` or `NEBIUS_API_KEY`, from Streamlit secrets or the environment. The recorded evidence and the requirement explorer always use the archived Nebius measurements. The Historical router · v1 and Workload optimizer pages still call Nebius through the original v1 configuration.

## Evidence and costs

The adopted profile's 312-conversation representative set recorded 98.4% correct autonomous completion, 1.6% review, zero observed wrong releases, about $0.080 per 1,000 requests and 3.61-second p95, all on Nebius Token Factory. Its four-second deadline gate passed narrowly. These are workload-specific historical measurements, not production service guarantees. The separate 367-conversation date challenge must not be pooled into a production accuracy claim.

For M5, one million requests at the recorded token mix projects $102.91 routed versus $339.47 with grounded Super. The dashboard's adopted comparison uses M6 costs separately. All projections exclude hosting, development, labeling and human review.

The new [rules-only diagnostic](../results/rules-only-report.md) asks what a no-model parser can do on the exposed synthetic workload. It is retrospective, not independent validation. Follow the [pilot handoff](validation-pilot-handoff.md) for the next evidence step.
