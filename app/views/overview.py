import streamlit as st

from routewise import artifacts
from routewise.config import load_config
from routewise.cost import Price
from routewise.dataset import DATA_DIR, VERSION
from ui import money, pct, status_badge

config = load_config()
evals = artifacts.list_runs("eval")
latest_eval = artifacts.preferred(evals)

st.title("RouteWise")
st.markdown(
    "#### Pay for the strong model only when the cheap one gets it wrong.\n"
    "RouteWise benchmarks NVIDIA Nemotron models on Nebius Token Factory against **your labelled workload**, "
    "picks the cheapest route that meets the quality and latency targets you declare, validates every answer "
    "with deterministic checks, and escalates a failed answer **once** to a stronger model."
)

with st.container(border=True):
    st.markdown(
        "**Hypothesis under test** — for a structured information-extraction workload, RouteWise reduces total "
        "inference cost while meeting a stated quality target, measured on held-out data against two fixed-model "
        "baselines (always-smallest, always-strongest)."
    )

if latest_eval and (latest_eval.path / "summary.json").exists():
    summary = artifacts.load_json(latest_eval.path / "summary.json")
    verdict = summary["verdict"]
    comp = summary["routewise_vs_strongest"]
    lead = summary.get("lead_strategy", "routewise")
    rw = summary["strategies"][lead]["overall"]
    strong = summary["strategies"]["always_strongest"]["overall"]
    st.subheader("Latest held-out result")
    status_badge(verdict["hypothesis_supported"], "Hypothesis supported", "Hypothesis not supported")
    cols = st.columns(4)
    cols[0].metric("Cost saved vs always-strongest", pct(comp.get("cost_savings_fraction")))
    cols[1].metric("Task accuracy", pct(rw["task_accuracy"]),
                   delta=f"{(rw['task_accuracy'] - strong['task_accuracy']) * 100:+.1f} pts vs strongest")
    cols[2].metric("Escalation rate", pct(rw["escalation_rate"]))
    cols[3].metric("Held-out tasks", summary["n_examples"])
    st.caption(f"{verdict['summary']}  ·  run `{latest_eval.id}`" + ("" if latest_eval.is_full else " (smoke run, --limit)"))
else:
    st.info("No held-out evaluation has been run yet. Results appear here once `routewise evaluate` has run.",
            icon=":material/hourglass_empty:")

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
left, right = st.columns(2)
with left:
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
with right:
    st.markdown("**Declared requirements** (fixed before tuning)")
    st.dataframe(
        [{"task": t, **{k: v for k, v in vars(th).items()}} for t, th in config.thresholds.items()],
        hide_index=True, width="stretch",
    )
    manifest_path = DATA_DIR / VERSION / "MANIFEST.json"
    if manifest_path.exists():
        m = artifacts.load_json(manifest_path)
        st.markdown(f"**Workload** — {m['n_examples']} labelled support conversations (dataset `{VERSION}`, seed {m['seed']}).")
        st.dataframe([{"split / task / difficulty": k, "examples": v} for k, v in m["counts"].items()],
                     hide_index=True, width="stretch")
