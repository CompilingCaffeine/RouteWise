import streamlit as st

from routewise import artifacts
from routewise.config import load_config
from routewise.cost import Price
from routewise.dataset import DATA_DIR, VERSION
from ui import money, pct, status_badge

config = load_config()
evals = artifacts.list_runs("eval")
latest_eval = artifacts.headline_eval(evals)

st.title("RouteWise")
st.markdown(
    "#### Find the lowest-cost model configuration your evidence supports.\n"
    "RouteWise benchmarks NVIDIA Nemotron models on Nebius Token Factory against **your labelled workload**, "
    "picks the cheapest route that meets the quality and latency targets you declare, validates every answer "
    "with deterministic checks, and escalates a failed answer **once** to a stronger model."
)

with st.container(border=True):
    st.markdown(
        "**Hypothesis under test** — for a structured information-extraction workload, RouteWise reduces total "
    "inference cost while meeting a stated quality target. The original experiment used smallest/strongest "
    "baselines; M5 adds Super and grounded fixed-model controls on fresh data."
    )

confirmations = [r for r in artifacts.list_runs('confirmation') if (r.path/'summary.json').exists()
                 and artifacts.load_json(r.path/'summary.json').get('complete')]
if confirmations:
    fresh = artifacts.load_json(confirmations[0].path/'summary.json')
    st.subheader(f"Fresh-data confirmation · {fresh.get('study', 'm5').upper()}")
    selected = fresh['cheapest_qualifying']
    names = {'grounding_only': 'Grounded Nano → Super (without hedging)',
             'grounded_hedged': 'Grounded Nano → Super + hedge'}
    st.markdown(f"Lowest-cost qualifying configuration: **{names.get(selected, selected) or 'none'}**.")
    if selected:
        measured = fresh['metrics'][selected]
        cols = st.columns(4)
        cols[0].metric('Task accuracy', pct(measured['task_accuracy']))
        cols[1].metric('Accuracy lower 95%', pct(measured['accuracy_lower95']))
        cols[2].metric('p95 response', f"{measured['p95_latency_ms']:,.0f} ms")
        cols[3].metric('Cost / 1,000 tasks', money(measured['cost_per_1k_tasks']))
    result = 'supported' if fresh['hypothesis_supported'] else 'not supported'
    st.caption(f"{fresh['n_unique']} fresh synthetic conversations; 95% task target and 4 s p95 deadline. "
               f"The predeclared grounding + hedging hypothesis is {result}. The selected configuration is a "
               'descriptive comparison of the measured options. Open Workload optimizer for all baselines, '
               'bounds, traces and limitations. The original experiment below is unchanged.')
    if selected and fresh['metrics'][selected]['accuracy_family_cluster_ci95'][0] < .95:
        low, high = fresh['metrics'][selected]['accuracy_family_cluster_ci95']
        st.caption(f'Family-mix sensitivity interval: {pct(low)}–{pct(high)}. The result is conditional on '
                   'this synthetic workload; validate real customer data and additional serving days.')

if latest_eval and (latest_eval.path / "summary.json").exists():
    summary = artifacts.load_json(latest_eval.path / "summary.json")
    verdict = summary["verdict"]
    comp = summary["routewise_vs_strongest"]
    lead = summary.get("lead_strategy", "routewise")
    rw = summary["strategies"][lead]["overall"]
    strong = summary["strategies"]["always_strongest"]["overall"]
    st.subheader("Original pre-registered result · v1")
    status_badge(verdict["hypothesis_supported"], "Hypothesis supported", "Hypothesis not supported")
    cols = st.columns(4)
    cols[0].metric("Cost saved vs always-strongest", pct(comp.get("cost_savings_fraction")))
    cols[1].metric("Task accuracy", pct(rw["task_accuracy"]),
                   delta=f"{(rw['task_accuracy'] - strong['task_accuracy']) * 100:+.1f} pts vs strongest")
    cols[2].metric("Escalation rate", pct(rw["escalation_rate"]))
    cols[3].metric("Held-out tasks", summary["n_examples"])
    st.caption(f"{verdict['summary']}  ·  run `{latest_eval.id}`{latest_eval.tag}")
else:
    st.info("No held-out evaluation has been run yet. Results appear here once `routewise evaluate` has run.",
            icon=":material/hourglass_empty:")

registry = artifacts.run_registry()
cited = [(e, next((r for r in evals if r.id == e["run"]), None)) for e in registry.get("evaluations", [])]
cited = [(e, r) for e, r in cited if r and (r.path / "summary.json").exists()]
if cited:
    st.subheader("Evidence trail")
    st.caption("Every held-out run, in the order it was run. None is hidden; the original pre-registered result remains unchanged.")
    rows = []
    for entry, r in cited:
        s = artifacts.load_json(r.path / "summary.json")
        rows_by = s["strategies"]
        for strat in [k for k in ("routewise", "routewise_hedged") if k in rows_by]:
            m = rows_by[strat]["extraction"]
            rows.append({
                "run": r.id,
                "role": entry["role"],
                "strategy": "RouteWise + hedging" if strat == "routewise_hedged" else "RouteWise",
                "extraction acc": pct(m["task_accuracy"]),
                "extraction p95": f"{m['p95_latency_ms']:,.0f} ms",
                "cost vs always-strongest": f"−{s['comparisons'][strat]['cost_savings_fraction'] * 100:.1f}%"
                if "comparisons" in s else f"−{s['routewise_vs_strongest']['cost_savings_fraction'] * 100:.1f}%",
                "meets thresholds": "yes" if s.get("verdicts", {strat: s["verdict"]})[strat]["hypothesis_supported"] else "no",
            })
    st.dataframe(rows, hide_index=True, width="stretch")
    for entry, r in cited:
        st.markdown(f"- **{entry['role']}** `{r.id}`: {entry['note']}")

st.subheader("How it works")
st.graphviz_chart(
    """
    digraph {
      rankdir=LR; bgcolor="transparent";
      node [shape=box style="rounded" fontname="Helvetica" fontsize=11 color="#898781" fontcolor="#898781"];
      edge [color="#898781" fontname="Helvetica" fontsize=10 fontcolor="#898781"];
      data [label="Labelled workload\\n(tune / held-out)"];
      bench [label="Benchmark every\\ncandidate (tune)"];
      policy [label="Policy: cheapest route\\nmeeting thresholds"];
      req [label="Incoming request\\n(known task)"];
      call [label="Selected model\\n(Token Factory)"];
      val [label="Deterministic\\nvalidator"];
      esc [label="Escalate once to\\nstronger model"];
      out [label="Answer + full trace"];
      data -> bench -> policy;
      policy -> call [style=dashed label="route table"];
      req -> call -> val;
      val -> out [label="pass"];
      val -> esc [label="fail"];
      esc -> out;
    }
    """
)

st.subheader("Setup")
st.markdown("**Candidate models** (NVIDIA open models on Nebius Token Factory)")
prices = artifacts.load_prices()
rows = []
for c in config.candidates:
    meta = (prices or {}).get("models", {}).get(c.model_id)
    p = meta["pricing"] if meta else None
    price = Price(c.model_id, float(p["prompt"]), float(p["completion"])) if p else None
    rows.append({
        "candidate": c.label,
        "model id": c.model_id,
        "input $/1M tok": money(price.prompt_per_million, 2) if price else "not in snapshot",
        "output $/1M tok": money(price.completion_per_million, 2) if price else "–",
    })
st.dataframe(rows, hide_index=True, width="stretch")
if prices:
    st.caption(f"Prices from `/v1/models?verbose=true`, fetched {prices['fetched_at'][:16].replace('T', ' ')} UTC.")
else:
    st.caption("Price snapshot not taken yet (`routewise prices`).")
st.markdown("**Declared requirements** (fixed before tuning)")
st.dataframe(
    [{
        "task": task,
        "task accuracy ≥": f"{th.min_task_accuracy:.0%}",
        "field accuracy ≥": f"{th.min_field_accuracy:.0%}" if th.min_field_accuracy is not None else "–",
        "p95 latency ≤": f"{th.max_p95_latency_ms:,.0f} ms" if th.max_p95_latency_ms is not None else "–",
    } for task, th in config.thresholds.items()],
    hide_index=True, width="stretch",
)
manifest_path = DATA_DIR / VERSION / "MANIFEST.json"
if manifest_path.exists():
    m = artifacts.load_json(manifest_path)
    st.markdown(f"**Workload** — {m['n_examples']} labelled support conversations (dataset `{VERSION}`, seed {m['seed']}).")
    st.dataframe([{"split / task / difficulty": k, "examples": v} for k, v in m["counts"].items()],
                 hide_index=True, width="stretch")
