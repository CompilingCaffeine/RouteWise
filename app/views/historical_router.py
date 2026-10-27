import streamlit as st

from routewise import artifacts, dataset
from routewise.client import TokenFactoryClient
from routewise.config import load_config
from routewise.evaluation import build_routers
from routewise.prices import price_for
from routewise.prompts import load_prompt
from routewise.schema import TASKS
from ui import MAX_LIVE_ROUTES, STRATEGY_LABELS, api_key, render_trace

MAX_INPUT_CHARS = 4000

config = load_config()
st.title("Historical router · v1")
st.markdown("Send one support conversation through RouteWise and follow it end to end: the policy's choice and the "
            "measurements behind it, the model answer, every deterministic check, and the escalation if one happens.")
st.caption('This page reproduces the original policy. Workload optimizer includes the new source-grounded configurations '
           'and their fixed-model comparisons.')

policies = artifacts.list_policies()
if not policies:
    st.info("No routing policy yet. Run `routewise benchmark` then `routewise policy`.", icon=":material/hourglass_empty:")
    st.stop()
evals = artifacts.list_runs("eval")
latest_eval = artifacts.headline_eval(evals)
policy = artifacts.load_json(policies[0])  # the current (newest) policy is what routes live requests
st.caption(f"Live requests use policy `{policy['policy_id']}`.")

key = api_key()
live_tab, replay_tab = st.tabs(["Route a request live", "Replay a recorded request"])

with live_tab:
    if not key:
        st.info("Live routing is off on this deployment (no Token Factory key configured). "
                "Use **Replay a recorded request** to inspect real recorded traces.", icon=":material/key_off:")
    heldout = dataset.load_split("heldout")
    escalated_ids = set()
    if latest_eval:
        escalated_ids = {t["example_id"] for t in artifacts.load_records(latest_eval, "traces.jsonl")
                         if t["strategy"].startswith("routewise") and t["escalated"]}
    source = st.segmented_control("Input", ["Held-out example", "Paste a conversation"], default="Held-out example")
    expected = None
    example_id = None
    if source == "Paste a conversation":
        task = st.segmented_control("Task", list(TASKS), default="extraction")
        text = st.text_area(
            "Conversation", height=180, max_chars=MAX_INPUT_CHARS,
            placeholder="[2026-03-14 09:12] Customer: Hi, my order ORD-12345 arrived broken. I'd like a refund...\n"
                        "[2026-03-14 10:05] Agent: ...",
        )
    else:
        only_esc = st.toggle(f"Show examples that escalated in the latest evaluation ({len(escalated_ids)})",
                             value=bool(escalated_ids))
        pool = [e for e in heldout if not only_esc or e.id in escalated_ids] or heldout
        ex = st.selectbox("Example", pool, format_func=lambda e: f"{e.id} · {e.task} · {e.difficulty} · {', '.join(e.tags)}")
        task, text, expected, example_id = ex.task, ex.input, ex.expected, ex.id
        st.code(text, language=None, wrap_lines=True)

    hedging = any(e["choice"].get("hedge_after_ms") for e in policy["tasks"].values())
    available = [s for s in STRATEGY_LABELS if s != "routewise_hedged" or hedging]
    strategy = st.segmented_control("Strategy", available, default=available[-1], format_func=STRATEGY_LABELS.get)
    used = st.session_state.get("live_routes", 0)
    disabled = not key or not text or not task or not strategy or used >= MAX_LIVE_ROUTES
    if st.button("Route it", type="primary", disabled=disabled, icon=":material/send:"):
        client = TokenFactoryClient(api_key=key, base_url=config.base_url)
        snapshot = artifacts.load_prices()
        prices = {c.label: price_for(snapshot, c.model_id) for c in config.candidates}
        prompts = {t: load_prompt(p, t) for t, p in config.prompts.items()}
        routers = build_routers(client, config, policy, prices, prompts)
        ready = st.empty()
        def show_answer(answer):
            with ready.container():
                st.caption(f"Answer ready in {answer['latency_ms']:,.0f} ms · {answer['answered_by']}; finishing cost accounting…")
                st.json(answer['output'])
        with st.spinner("Calling Nebius Token Factory…"):
            trace = routers[strategy].route(task, text, example_id=example_id, expected=expected, on_answer=show_answer)
        ready.empty()
        st.session_state["live_routes"] = used + 1
        st.session_state["live_trace"] = trace.model_dump()
    st.caption(f"{used}/{MAX_LIVE_ROUTES} live requests used this session.")
    if "live_trace" in st.session_state:
        trace = st.session_state["live_trace"]
        reason = policy["tasks"][trace["task"]]["reason"] if trace["strategy"].startswith("routewise") else "Fixed baseline."
        truth = next((e.expected for e in heldout if e.id == trace.get("example_id")), None)
        render_trace(trace, policy_reason=reason, expected=truth)

with replay_tab:
    registry = artifacts.run_registry()
    cited_ids = [e["run"] for e in registry.get("evaluations", [])]
    replayable = [r for r in evals if r.id in cited_ids] or evals
    if not replayable:
        st.info("No recorded evaluation traces yet.", icon=":material/hourglass_empty:")
    else:
        roles = {e["run"]: e["role"] for e in registry.get("evaluations", [])}
        run = st.selectbox("Evaluation run", replayable, index=replayable.index(latest_eval) if latest_eval in replayable else 0,
                           format_func=lambda r: f"{roles.get(r.id, r.id)} · {r.id}{r.tag}")
        traces = artifacts.load_records(run, "traces.jsonl")
        variants = [s for s in ("routewise_hedged", "routewise") if any(t["strategy"] == s for t in traces)]
        strategy = st.segmented_control("Strategy", variants, default=variants[0], format_func=STRATEGY_LABELS.get,
                                        key="replay_strategy")
        recorded = [t for t in traces if t["strategy"] == strategy]
        recorded.sort(key=lambda t: (not (t["escalated"] or t.get("hedged")), t["example_id"]))
        pick = st.selectbox(
            f"Recorded {STRATEGY_LABELS[strategy]} request", recorded,
            format_func=lambda t: f"{t['example_id']} · {t['route']}"
            + (" · escalated" if t["escalated"] else "") + (" · hedged" if t.get("hedged") else ""),
        )
        picked = next(e for e in dataset.load_examples() if e.id == pick["example_id"])
        st.code(picked.input, language=None, wrap_lines=True)
        run_policy = next((artifacts.load_json(p) for p in policies if p.stem == pick["policy_id"]), policy)
        render_trace(pick, policy_reason=run_policy["tasks"][pick["task"]]["reason"], expected=picked.expected)
        st.caption(f"From evaluation run `{run.id}`")
