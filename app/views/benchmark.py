import pandas as pd
import streamlit as st

from routewise import artifacts
from routewise.config import load_config
from ui import money, ms, pct, tradeoff_chart

config = load_config()
st.title("Benchmark")
st.markdown("Every candidate model answers every **tuning** example with the same prompt. "
            "These measurements are the only input to the routing policy.")

runs = artifacts.list_runs("benchmark")
if not runs:
    st.info("No benchmark run yet. Run `routewise benchmark --split tune`.", icon=":material/hourglass_empty:")
    st.stop()

default = artifacts.preferred(runs)
run = st.selectbox("Benchmark run", runs, index=runs.index(default),
                   format_func=lambda r: f"{r.id}{r.tag}")
metrics = artifacts.load_json(run.path / "metrics.json") if (run.path / "metrics.json").exists() else []
df = pd.DataFrame(metrics)
st.caption(f"{len(run.manifest['example_ids'])} {run.manifest['split']} examples · prompts "
           + ", ".join(f"{t} `{p['version']}`" for t, p in run.manifest["prompts"].items())
           + f" · prices fetched {run.manifest['prices']['fetched_at'][:10]}")

ladder = [c["label"] for c in run.manifest["candidates"]]
for task in [t for t in config.thresholds if t in set(df.get("task", []))]:
    th = config.thresholds[task]
    sub = df[df["task"] == task].copy()
    st.subheader(task.capitalize())
    st.altair_chart(
        tradeoff_chart(sub, series="candidate", domain=[c for c in ladder if c in set(sub["candidate"])],
                       threshold=th.min_task_accuracy),
        width="stretch",
    )
    table = pd.DataFrame({
        "candidate": sub["candidate"],
        "task acc": sub["task_accuracy"].map(pct),
        "field acc": sub["field_accuracy"].map(pct),
        "valid JSON": sub["schema_valid_rate"].map(pct),
        "p50": sub["p50_latency_ms"].map(ms),
        "p95": sub["p95_latency_ms"].map(ms),
        "$ / 1k tasks": sub["cost_per_1k_tasks"].map(money),
    })
    st.dataframe(table, hide_index=True, width="stretch")
    req = [f"task accuracy ≥ {th.min_task_accuracy:.0%}"]
    if th.min_field_accuracy is not None:
        req.append(f"field accuracy ≥ {th.min_field_accuracy:.0%}")
    if th.max_p95_latency_ms is not None:
        req.append(f"p95 ≤ {th.max_p95_latency_ms:,.0f} ms")
    st.caption("Required: " + ", ".join(req))

st.divider()
st.header("Routing policy")
policies = [p for p in artifacts.list_policies() if artifacts.load_json(p)["source_run"] == run.id]
if not policies:
    st.info("No policy has been derived from this run yet. Run `routewise policy`.", icon=":material/hourglass_empty:")
    st.stop()
policy = artifacts.load_json(policies[0])
st.markdown("Each option is **simulated from the recorded calls above** (no new API spend): "
            "the primary model answers; if the validator rejects the answer, the recorded answer of the escalation "
            "model is used and both calls are paid for. The cheapest option meeting every requirement wins.")
for task, entry in policy["tasks"].items():
    with st.container(border=True):
        st.markdown(f"**{task.capitalize()} → `{entry['choice_label']}`**")
        if entry["status"] == "ok":
            st.caption(entry["reason"])
        else:
            st.warning(entry["reason"], icon=":material/warning:")
        rows = []
        for o in sorted(entry["options"], key=lambda r: r["metrics"]["cost_per_1k_tasks"]):
            m = o["metrics"]
            rows.append({
                "": "✓ chosen" if o["label"] == entry["choice_label"] else "",
                "option": o["label"],
                "task acc": pct(m["task_accuracy"]),
                "field acc": pct(m["field_accuracy"]),
                "p95": ms(m["p95_latency_ms"]),
                "$ / 1k tasks": money(m["cost_per_1k_tasks"]),
                "escalation rate": pct(m["escalation_rate"]),
                "meets requirements": "yes" if o["qualifies"] else "no: " + "; ".join(o["violations"]),
            })
        st.dataframe(rows, hide_index=True, width="stretch")
st.caption(f"Policy `{policy['policy_id']}` · derived from `{policy['source_run']}`")
