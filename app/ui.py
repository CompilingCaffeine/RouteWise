"""Shared dashboard helpers: theme-aware palette, charts, loaders and the trace view."""

from __future__ import annotations

import json
import os
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from routewise.config import REPO_ROOT, load_dotenv

# Reference palette (validated: first three slots pass all-pairs CVD checks in both modes).
SERIES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a"],
    "dark": ["#3987e5", "#d95926", "#199e70"],
}
INK = {
    "light": {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "baseline": "#c3c2b7"},
    "dark": {"primary": "#f0efec", "secondary": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "baseline": "#383835"},
}
STRATEGY_LABELS = {
    "always_smallest": "Always smallest",
    "always_strongest": "Always strongest",
    "routewise": "RouteWise",
    "routewise_hedged": "RouteWise + hedging",
}
MAX_LIVE_ROUTES = 15


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def api_key() -> str | None:
    try:
        key = st.secrets.get("NEBIUS_API_KEY")
    except Exception:
        key = None
    if key:
        return key
    load_dotenv(REPO_ROOT / ".env")
    return os.environ.get("NEBIUS_API_KEY")


def money(v: float | None, digits: int = 4) -> str:
    return "–" if v is None else f"${v:,.{digits}f}"


def pct(v: float | None, digits: int = 1) -> str:
    return "–" if v is None else f"{v * 100:.{digits}f}%"


def ms(v: float | None) -> str:
    return "–" if v is None else f"{v:,.0f} ms"


def tradeoff_chart(
    df: pd.DataFrame,
    *,
    series: str,
    domain: list[str],
    threshold: float | None,
    emphasize: list[str] | None = None,
    height: int = 300,
) -> alt.LayerChart:
    """Cost (log x) vs task accuracy (y), one point per series, direct-labelled.

    With `emphasize`, those series take slots 1, 2, ... in order and the rest recede to muted ink.
    """
    m = mode()
    ink = INK[m]
    if emphasize:
        slot = {name: SERIES[m][i] for i, name in enumerate(emphasize)}
        colors = [slot.get(d, ink["muted"]) for d in domain]
    else:
        colors = SERIES[m][: len(domain)]
    surface = "#1a1a19" if m == "dark" else "#fcfcfb"
    y_min = max(0.0, min(0.5, float(df["task_accuracy"].min()) - 0.05))

    y_scale = alt.Scale(domain=[y_min, 1.0], zero=False, nice=False)
    base = alt.Chart(df).encode(
        x=alt.X(
            "cost_per_1k_tasks:Q",
            scale=alt.Scale(type="log", nice=False, padding=28),
            axis=alt.Axis(title="USD per 1,000 tasks (log scale)", format="$.3~f", grid=False,
                          domainColor=ink["baseline"], tickColor=ink["baseline"],
                          labelColor=ink["secondary"], titleColor=ink["secondary"]),
        ),
        y=alt.Y(
            "task_accuracy:Q",
            scale=y_scale,
            axis=alt.Axis(title="Task accuracy", format=".0%", gridColor=ink["grid"],
                          domainColor=ink["baseline"], tickColor=ink["baseline"],
                          labelColor=ink["secondary"], titleColor=ink["secondary"]),
        ),
    )
    tooltip = [
        alt.Tooltip(f"{series}:N", title="Option"),
        alt.Tooltip("task_accuracy:Q", title="Task accuracy", format=".1%"),
        alt.Tooltip("field_accuracy:Q", title="Field accuracy", format=".1%"),
        alt.Tooltip("cost_per_1k_tasks:Q", title="USD / 1k tasks", format="$.4f"),
        alt.Tooltip("p95_latency_ms:Q", title="p95 latency (ms)", format=",.0f"),
    ]
    points = base.mark_circle(size=140, opacity=1, stroke=surface, strokeWidth=2).encode(
        color=alt.Color(f"{series}:N", scale=alt.Scale(domain=domain, range=colors),
                        legend=alt.Legend(title=None, orient="bottom", labelColor=ink["secondary"])),
        tooltip=tooltip,
    )
    labels = base.mark_text(align="left", dx=10, dy=-8, fontSize=12, color=ink["secondary"]).encode(
        text=f"{series}:N", tooltip=tooltip
    )
    layers = [points, labels]
    if threshold is not None:
        rule_df = pd.DataFrame({"y": [threshold], "label": [f"required ≥ {threshold:.0%}"]})
        rule_y = alt.Y("y:Q", scale=y_scale)
        rule = alt.Chart(rule_df).mark_rule(color=ink["muted"], strokeWidth=1).encode(y=rule_y)
        rule_text = alt.Chart(rule_df).mark_text(align="left", baseline="top", dx=4, dy=4, fontSize=11,
                                                 color=ink["muted"]).encode(y=rule_y, x=alt.value(0), text="label:N")
        layers = [rule, rule_text, *layers]
    return alt.layer(*layers).properties(height=height).configure_view(stroke=None)


def status_badge(ok: bool, yes: str, no: str) -> None:
    if ok:
        st.badge(yes, icon=":material/check_circle:", color="green")
    else:
        st.badge(no, icon=":material/error:", color="red")


def render_trace(trace: dict[str, Any], policy_reason: str | None = None) -> None:
    """One routed request: decision, every hop with validator checks, and the totals."""
    head = st.columns(4)
    head[0].metric("Answered by", trace.get("answered_by") or trace["calls"][-1]["candidate"])
    head[1].metric("Time to answer", ms(trace["latency_ms"]))
    head[2].metric("Total cost (all calls)", money(trace["cost_usd"], 6))
    head[3].metric("Model calls", len(trace["calls"]))
    st.caption(f"Route: `{trace['route']}`")
    if policy_reason:
        st.caption(f"**Why this route:** {policy_reason}")
    if trace.get("policy_status") == "no_qualifying_policy":
        st.warning("No option met the declared thresholds on the tuning data; this route is the most accurate fallback.",
                   icon=":material/warning:")

    for hop, (call, check) in enumerate(zip(trace["calls"], trace["validations"]), start=1):
        with st.container(border=True):
            title = f"**Hop {hop} · {call['candidate']}** · `{call['model_id']}`"
            st.markdown(title)
            c = st.columns(4)
            c[0].metric("Latency", ms(call["latency_ms"]))
            c[1].metric("Tokens in / out", f"{call['prompt_tokens'] or '–'} / {call['completion_tokens'] or '–'}")
            c[2].metric("Cost", money(call["cost_usd"], 6))
            c[3].metric("Validator", "pass" if check["passed"] else "fail")
            left, right = st.columns([3, 2])
            with left:
                st.caption("Model output")
                if call["parsed"] is not None:
                    st.json(call["parsed"], expanded=True)
                else:
                    st.code(call["content"] or call.get("error") or "(empty)", language=None)
            with right:
                st.caption("Deterministic checks")
                for chk in check["checks"]:
                    icon = ":material/check_circle:" if chk["passed"] else ":material/cancel:"
                    detail = f" — {chk['detail']}" if chk["detail"] and not chk["passed"] else ""
                    st.markdown(f"{icon} `{chk['rule']}`{detail}")
        if hop == 1 and trace.get("hedged"):
            started = trace["timeline_ms"].get("hedge_dispatched")
            st.info(f"Hedged: no answer after {started:,.0f} ms, so the escalation model was started in parallel. "
                    "The first valid answer is used; both calls are billed.", icon=":material/call_split:")
        if hop == 1 and trace["escalated"]:
            st.info(f"Escalated: {trace['escalation_reason']}", icon=":material/arrow_downward:")

    if trace.get("score"):
        s = trace["score"]
        wrong = [f for f, ok in s["fields"].items() if not ok]
        if s["task_correct"]:
            st.success("Final answer matches ground truth on every field.", icon=":material/check_circle:")
        else:
            st.error(f"Final answer differs from ground truth on: {', '.join(wrong)}", icon=":material/cancel:")


def trace_json(trace: dict[str, Any]) -> str:
    return json.dumps(trace, indent=2)
