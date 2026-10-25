import pandas as pd
import streamlit as st

from routewise import artifacts
from routewise.config import load_config
from ui import STRATEGY_LABELS, money, ms, pct, render_trace, status_badge, tradeoff_chart

config = load_config()
st.title("Held-out evaluation")
st.markdown("RouteWise and two fixed-model baselines answer the same **held-out** examples, live. "
            "None of these examples were used to choose the policy. Every model call, retry and escalation is billed.")

runs = artifacts.list_runs("eval")
if not runs:
    st.info("No evaluation run yet. Run `routewise evaluate`.", icon=":material/hourglass_empty:")
    st.stop()
default = artifacts.preferred(runs)
run = st.selectbox("Evaluation run", runs, index=runs.index(default),
                   format_func=lambda r: f"{r.id}{'' if r.is_full else '  (smoke, --limit)'}")
summary = artifacts.load_json(run.path / "summary.json")
traces = artifacts.load_records(run, "traces.jsonl")
policy_id = summary["policy_id"]

verdict = summary["verdict"]
status_badge(verdict["hypothesis_supported"], "Hypothesis supported", "Hypothesis not supported")
st.markdown(verdict["summary"])

lead = summary.get("lead_strategy", "routewise")
comp = summary["routewise_vs_strongest"]
rw = summary["strategies"][lead]
if len(summary.get("verdicts", {})) > 1:
    for s, v in summary["verdicts"].items():
        if s != lead:
            st.caption(f"Same-run control, {STRATEGY_LABELS[s]}: {v['summary']}")
st.markdown(f"Headline figures are for **{STRATEGY_LABELS[lead]}**.")
cols = st.columns(4)
cols[0].metric("Cost saved vs always-strongest", pct(comp.get("cost_savings_fraction")))
acc_ci = comp.get("task_accuracy_diff_ci95")
cols[1].metric("Accuracy vs always-strongest", f"{comp.get('task_accuracy_diff', 0) * 100:+.1f} pts",
               help=f"95% bootstrap CI: {acc_ci[0] * 100:+.1f} to {acc_ci[1] * 100:+.1f} pts" if acc_ci else None)
cols[2].metric("Escalation rate", pct(rw["overall"]["escalation_rate"]))
cols[3].metric("Validator error recall", pct(rw["validator"]["error_recall"]),
               help="Share of wrong first answers the deterministic validator caught.")

strategies = [s for s in STRATEGY_LABELS if s in summary["strategies"]]
emphasized = [STRATEGY_LABELS[lead]] + [STRATEGY_LABELS[s] for s in ("routewise", "routewise_hedged")
                                        if s in summary["strategies"] and s != lead]
for task, th in config.thresholds.items():
    if task not in rw:
        continue
    st.subheader(task.capitalize())
    rows = []
    for s in strategies:
        m = summary["strategies"][s][task]
        rows.append({"strategy": STRATEGY_LABELS[s], **m})
    df = pd.DataFrame(rows)
    st.altair_chart(
        tradeoff_chart(df, series="strategy", domain=[STRATEGY_LABELS[s] for s in strategies],
                       threshold=th.min_task_accuracy, emphasize=emphasized),
        width="stretch",
    )
    st.dataframe(pd.DataFrame({
        "strategy": df["strategy"],
        "task acc": df["task_accuracy"].map(pct),
        "field acc": df["field_accuracy"].map(pct),
        "p50": df["p50_latency_ms"].map(ms),
        "p95": df["p95_latency_ms"].map(ms),
        "$ / 1k tasks": df["cost_per_1k_tasks"].map(money),
        "escalations": df["escalation_rate"].map(pct),
        "hedged": df["hedge_rate"].map(pct),
        "meets requirements": df["meets_thresholds"].map(lambda b: "yes" if b else "no"),
    }), hide_index=True, width="stretch")
    for _, r in df.iterrows():
        if r["threshold_violations"] and r["strategy"] in emphasized:
            st.warning(f"{r['strategy']} misses: " + "; ".join(r["threshold_violations"]), icon=":material/warning:")

st.subheader("Is validation worth it?")
v = rw["validator"]
st.markdown("The validator is treated as a classifier of wrong first answers; a positive means *escalate*.")
c = st.columns(4)
c[0].metric("Errors caught (recall)", pct(v["error_recall"]))
c[1].metric("Escalations that were needed (precision)", pct(v["escalation_precision"]))
c[2].metric("Correct answers escalated anyway", pct(v["false_positive_rate"]))
c[3].metric("Caught errors fixed by escalation", pct(v["correction_rate"]))
st.dataframe([
    {"": "validator: fail → escalate", "first answer wrong": v["true_positive"], "first answer right": v["false_positive"]},
    {"": "validator: pass → keep", "first answer wrong": v["false_negative"], "first answer right": v["true_negative"]},
], hide_index=True)

st.subheader("Trace explorer")
ids = sorted({t["example_id"] for t in traces})
escalated = sorted({t["example_id"] for t in traces if t["strategy"] == lead and (t["escalated"] or t.get("hedged"))})
only_esc = st.toggle(f"Only requests {STRATEGY_LABELS[lead]} escalated or hedged ({len(escalated)})", value=bool(escalated))
choice = st.selectbox("Request", escalated if only_esc and escalated else ids)
tabs = st.tabs([STRATEGY_LABELS[s] for s in strategies])
for tab, s in zip(tabs, strategies):
    with tab:
        match = [t for t in traces if t["example_id"] == choice and t["strategy"] == s]
        if match:
            render_trace(match[0])
st.caption(f"Run `{run.id}` · policy `{policy_id}` · raw traces in `runs/{run.id}/traces.jsonl`")
