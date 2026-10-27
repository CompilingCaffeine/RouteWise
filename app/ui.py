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
    "always_super": "Always Super",
    "always_strongest": "Always strongest",
    "routewise": "RouteWise",
    "routewise_hedged": "RouteWise + hedging",
}
MAX_LIVE_ROUTES = 15
CONFIGURATION_LABELS = {
    'fixed_nano': 'Nano (original prompt)', 'fixed_super': 'Super (original prompt)',
    'fixed_ultra': 'Ultra (original prompt)', 'fixed_grounded_nano': 'Grounded Nano',
    'fixed_grounded_super': 'Grounded Super', 'current': 'Nano → Super (original)',
    'grounding_only': 'Grounded Nano → Super',
    'hedging_only': 'Nano → Super + hedge (original)',
    'grounded_hedged': 'Grounded Nano → Super + hedge',
}
USEFUL_COMPARISONS = ('fixed_grounded_nano', 'grounding_only', 'grounded_hedged', 'fixed_grounded_super')


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


def useful_comparison_rows(metrics: dict, selected: str | None, *, accuracy: float = .95,
                           field_accuracy: float = .95, deadline_ms: float = 4000) -> list[dict]:
    """Matched controls isolate escalation, hedging and the fixed stronger model."""
    from routewise.statistics import cheapest_qualifying
    names = [n for n in USEFUL_COMPARISONS if n in metrics]
    if selected and selected not in names:
        names.append(selected)
    rows = []
    for name in names:
        m = metrics[name]
        qualifies = cheapest_qualifying({name: m}, accuracy=accuracy, field_accuracy=field_accuracy,
                                        deadline_ms=deadline_ms) is not None
        rows.append({
            'configuration': CONFIGURATION_LABELS.get(name, name),
            'accuracy (lower 95%)': f"{pct(m['task_accuracy'])} ({pct(m['accuracy_lower95'])})",
            '$ / 1k tasks': money(m['cost_per_1k_tasks'], 3),
            'p95': f"{m['p95_latency_ms']/1000:.2f} s",
            'requirements': '✓ selected' if name == selected else 'meets' if qualifies else 'does not meet',
        })
    return rows


def render_confirmation_takeaways(summary: dict) -> None:
    """Translate the four matched configurations into the deployment tradeoffs."""
    m = summary['metrics']
    if not all(n in m for n in USEFUL_COMPARISONS):
        return
    nano, route, hedge, strong = (m[n] for n in USEFUL_COMPARISONS)
    gain = (route['task_accuracy']-nano['task_accuracy'])*100
    saving = 1-route['cost_per_1k_tasks']/strong['cost_per_1k_tasks']
    premium = hedge['cost_per_1k_tasks']/route['cost_per_1k_tasks']-1
    cols = st.columns(3)
    cols[0].metric('Escalation closes the quality gap', f'+{gain:.1f} pts')
    cols[0].caption(f"Grounded Nano alone: {pct(nano['task_accuracy'])}. With Super escalation: "
                    f"{pct(route['task_accuracy'])}.")
    cols[1].metric('Routing saves vs grounded Super', pct(saving))
    cols[1].caption(f"Accuracy: {pct(route['task_accuracy'])} vs {pct(strong['task_accuracy'])}. "
                    f"p95: {route['p95_latency_ms']/1000:.2f} s vs {strong['p95_latency_ms']/1000:.2f} s.")
    cols[2].metric('Hedging adds cost', f'+{pct(premium)}')
    cols[2].caption(f"Accuracy: {pct(hedge['task_accuracy'])} vs {pct(route['task_accuracy'])}. "
                    f"p95 improves to {hedge['p95_latency_ms']/1000:.2f} s; both routes meet the 4 s deadline.")


RECOVERY_LABELS = {
    'control': 'Current route · grounded Nano → Super',
    'fixed_grounded_super': 'Grounded Super',
    'deterministic': 'Date normalization (frozen candidate)',
    'reasoning_nano': 'Reasoning fallback · Nano', 'reasoning_super': 'Reasoning fallback · Super',
    'reasoning_ultra': 'Reasoning fallback · Ultra',
}
GATE_TEXT = {
    'primary': 'Challenge: correct autonomous completions improve by more than 10 points (95% lower bound)',
    'S1': 'Challenge: wrong answers released rise by at most 2 points (95% upper bound)',
    'S2': 'Representative: 95% accuracy and field gates, 4 s deadline, complete cost',
    'S3': 'Representative: no loss of correct autonomous answers (−1 pt) or rise in wrong releases (+1 pt)',
    'S4': 'Representative: cheaper than grounded Super (95% upper bound below zero)',
}


def recovery_rows(metrics: dict, arms: list[str]) -> list[dict]:
    rows = []
    for name in arms:
        m = metrics[name]
        rows.append({
            'route': RECOVERY_LABELS.get(name, name),
            'correct, no review': pct(m['cac_rate']),
            'sent to review': f"{pct(m['review_rate'])} ({pct(m['necessary_review_rate'])} needed)",
            'wrong answers released': f"{m['undetected_errors']} ({pct(m['undetected_rate'])})",
            'accuracy when released': pct(m['released_accuracy']),
            '$ / 1k': money(m['cost_per_1k_tasks'], 3),
            'p95': '–' if m['p95_latency_ms'] is None else f"{m['p95_latency_ms']/1000:.2f} s",
            '> 4 s': m['deadline_misses'],
        })
    return rows


def render_date_recovery(summary: dict, selection: dict | None) -> None:
    """M6 (internal study m7): preregistered relative-date recovery, kept as two separate evaluations."""
    cand = summary['candidate']
    arms = ['control', 'fixed_grounded_super', cand]
    decision = summary.get('decision')
    ch, rep = summary['metrics']['challenge'], summary['metrics']['representative']
    st.subheader('Can relative refund dates be resolved without releasing wrong answers?')
    if not summary['complete'] or decision is None:
        st.warning('The confirmation run is incomplete; no decision is shown.')
        return
    primary = decision['detail']['primary_cac_diff']
    lo, hi = primary['ci95']
    if decision['adopt']:
        st.success(f"Adopt date normalization. On fresh relative-date conversations it answers "
                   f"{primary['diff']*100:+.1f} points more requests correctly without review "
                   f"(95% CI {lo*100:+.1f} to {hi*100:+.1f}), and it passes every safety gate.")
    else:
        failed = [k for k, ok in decision['checks'].items() if not ok]
        st.warning('Keep the current route. The frozen candidate did not pass: ' + ', '.join(failed) + '.')
    cols = st.columns(3)
    cols[0].metric('Correct without review, challenge set', pct(ch[cand]['cac_rate']),
                   f"{(ch[cand]['cac_rate']-ch['control']['cac_rate'])*100:+.1f} pts vs current")
    cols[1].metric('Wrong answers released, challenge set', ch[cand]['undetected_errors'],
                   f"{ch[cand]['undetected_errors']-ch['control']['undetected_errors']:+d} vs current", delta_color='inverse')
    cols[2].metric('Cost per 1k, representative', money(rep[cand]['cost_per_1k_tasks'], 3),
                   f"{(rep[cand]['cost_per_1k_tasks']/rep['control']['cost_per_1k_tasks']-1)*100:+.0f}% vs current",
                   delta_color='inverse')
    st.markdown(f"**Relative-date challenge set** · {ch[cand]['n']} fresh conversations, "
                f"{ch[cand]['n_review_required']} of them deliberately ambiguous (business days, ranges, "
                "\"next Friday\"). For those, the correct outcome is review.")
    st.dataframe(recovery_rows(ch, arms), hide_index=True, width='stretch')
    st.markdown(f"**Representative workload** · {rep[cand]['n']} fresh conversations at the declared "
                "scenario mix, evaluated separately and never pooled with the challenge set.")
    st.dataframe(recovery_rows(rep, arms), hide_index=True, width='stretch')
    st.caption('Review is never counted as a correct answer. Every rate uses all incoming requests, so sending more '
               'requests to review cannot look like an improvement. Costs include every model call.')
    st.dataframe([{'preregistered gate': GATE_TEXT[k], 'result': '✓ pass' if ok else '✗ fail'}
                  for k, ok in decision['checks'].items()], hide_index=True, width='stretch')
    with st.expander('Challenge set by scenario family'):
        fams = [r for r in summary['families']['challenge'] if r['strategy'] in arms]
        table = {}
        for r in fams:
            row = table.setdefault(r['family'], {'family': r['family'], 'n': r['n']})
            row[RECOVERY_LABELS.get(r['strategy'], r['strategy'])] = f"{pct(r['cac_rate'], 0)} · {r['undetected_errors']} wrong"
        st.dataframe(list(table.values()), hide_index=True, width='stretch')
        st.caption('Each cell: correct without review · wrong answers released.')
    if selection:
        with st.expander('How the candidate was chosen on development data'):
            dev, v4 = selection['dev'], selection['v4_replay']
            st.dataframe([{'route': RECOVERY_LABELS.get(n, n), 'dev: correct, no review': pct(dev[n]['cac_rate']),
                           'dev: wrong released': dev[n]['undetected_errors'],
                           'v4 replay: $ / 1k': money(v4[n]['cost_per_1k_tasks'], 3),
                           'v4 replay: p95': f"{v4[n]['p95_latency_ms']/1000:.2f} s",
                           'eligible': '✓ selected' if n == selection['selected'] else 'yes' if n in selection['eligible'] else
                           ('–' if n in ('control', 'fixed_grounded_super') else 'no')}
                          for n in dev], hide_index=True, width='stretch')
            st.caption('Reasoning fallbacks matched date normalization on development accuracy but cost more; the Nano '
                       'reasoning fallback missed the 4 s p95 criterion. The cheapest eligible route was frozen before '
                       'any confirmation data existed.')
    st.caption(f"Synthetic, model-written conversations; labels fixed before writing and agent-reviewed, not human-validated. "
               f"Run {summary['run']}; internal study key m7.")
    st.markdown('[Protocol](https://github.com/CompilingCaffeine/RouteWise/blob/main/experiments/M7-relative-date-recovery.md) · '
                '[Full report](https://github.com/CompilingCaffeine/RouteWise/blob/main/results/M7-report.md)')


def tradeoff_chart(
    df: pd.DataFrame,
    *,
    series: str,
    domain: list[str],
    threshold: float | None,
    emphasize: list[str] | None = None,
    height: int = 300,
    y_floor: float = 0.5,
    label_field: str | None = None,
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
    y_min = max(0.0, min(y_floor, float(df["task_accuracy"].min()) - 0.05))

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
        text=f"{label_field or series}:N", tooltip=tooltip
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


def render_trace(trace: dict[str, Any], policy_reason: str | None = None,
                 expected: dict[str, Any] | None = None) -> None:
    """One routed request: decision, every hop with validator checks, and the totals.

    With `expected` (dataset examples only), a ground-truth check follows; the router never sees it."""
    if not trace.get('final_valid', True):
        st.warning('Held for human review. The candidate output must not be used automatically.')
    else:
        st.success('Released after deterministic checks. Passing checks does not guarantee correctness.')
    if trace.get('recovery'):
        recovery = trace['recovery']
        st.markdown('**Final output after normalization**' if trace.get('final_valid')
                    else '**Candidate output · withheld for review**')
        st.json(trace.get('final_output'))
        analysis = recovery.get('analysis', {})
        st.caption(f"Issue-date analysis: {analysis.get('status')} · "
                   f"{analysis.get('date') or 'no single date'} · {analysis.get('reason', '')}")
        st.caption(f"Action: {recovery.get('action')}")
    head = st.columns(4)
    head[0].metric("Answered by", trace.get("answered_by") or trace["calls"][-1]["candidate"])
    head[1].metric("Time to answer", ms(trace["latency_ms"]))
    complete_cost = trace.get('cost_complete', True)
    head[2].metric("Total cost (all calls)" if complete_cost else "Known charges · billing incomplete",
                   money(trace["cost_usd"], 6))
    if not complete_cost:
        st.warning('Some usage charges are missing. The displayed amount is not a complete request cost.')
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
            injected = call.get("injected_delay_ms") or 0
            c[0].metric("Latency", ms(call["latency_ms"]),
                        help=f"Includes a synthetic {injected:,.0f} ms stall (fault-injection run)" if injected else None)
            if injected:
                st.caption(f":material/science: synthetic stall of {injected:,.0f} ms injected on this call")
            c[1].metric("Tokens in / out", f"{call['prompt_tokens'] or '–'} / {call['completion_tokens'] or '–'}")
            c[2].metric("Cost", money(call["cost_usd"], 6))
            c[3].metric("Validator", "pass" if check["passed"] else "fail")
            left, right = st.columns([3, 2])
            with left:
                st.caption("Model output before issue-date normalization" if trace.get('recovery') else "Model output")
                if call["parsed"] is not None:
                    st.json(call["parsed"], expanded=True)
                else:
                    st.code(call["content"] or call.get("error") or "(empty)", language=None)
                if call.get('grounding'):
                    with st.expander('Source evidence and raw model output'):
                        st.json(call['grounding'])
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
        render_ground_truth(trace, expected)
    if 'accounting_complete' in trace.get('timeline_ms', {}):
        st.caption(f"Answer ready: {ms(trace['latency_ms'])} · final accounting: "
                   f"{ms(trace['timeline_ms']['accounting_complete'])}. Unused hedge calls are included in cost.")


def render_ground_truth(trace: dict[str, Any], expected: dict[str, Any] | None) -> None:
    """Evaluation-only check of the shipped answer, with the reason a wrong answer got through."""
    s = trace["score"]
    st.markdown("**Ground-truth check** · evaluation only; the router never sees the correct answer")
    if s["task_correct"]:
        st.success("The released answer matches every labelled field." if trace.get('final_valid', True)
                   else "The candidate matches the labelled fields, but remains held for review.", icon=":material/check_circle:")
        return
    final = trace.get("final_output") or {}
    wrong = [f for f, ok in s["fields"].items() if not ok]
    rows = [{"field": f, "released answer" if trace.get('final_valid', True) else "withheld candidate": json.dumps(final.get(f)),
             "correct value": json.dumps(expected.get(f)) if expected else "–"} for f in wrong]
    st.dataframe(rows, hide_index=True)
    if not trace.get('final_valid', True):
        st.info('The candidate differs from the label and was held for review; it was not released automatically.')
        return
    last_check_passed = trace.get('final_valid', trace["validations"][-1]["passed"])
    answered_by = trace.get("answered_by") or trace["calls"][-1]["candidate"]  # older traces lack answered_by
    routed = ' -> ' in trace.get('route', '') or trace['strategy'].startswith('routewise')
    if not routed:
        why = ("This fixed baseline never escalates, so its answer ships whether or not the validator flags it."
               if not last_check_passed else "This fixed baseline never escalates, and the validator did not flag this answer.")
    elif trace["escalated"] or answered_by != trace["calls"][0]["candidate"]:
        why = "The stronger model answered, and its answer was also wrong on these fields."
    else:
        why = ("Every deterministic check passed, so RouteWise did not escalate: a known validator blind spot "
               "(see the validator analysis on the Held-out evaluation page).")
    st.error(f"Shipped a wrong answer. {why}", icon=":material/cancel:")


def trace_json(trace: dict[str, Any]) -> str:
    return json.dumps(trace, indent=2)


def _axis(ink: dict[str, str], title: str, fmt: str | None = None, grid: bool = True,
          values: list[float] | None = None) -> alt.Axis:
    style = {"format": fmt} if fmt else {}
    if values:
        style["values"] = values
    return alt.Axis(title=title, grid=grid, gridColor=ink["grid"], domainColor=ink["baseline"],
                    tickColor=ink["baseline"], labelColor=ink["secondary"], titleColor=ink["secondary"], **style)


def line_chart(df: pd.DataFrame, *, x: str, y: str, series: str, domain: list[str], x_title: str, y_title: str,
               x_log: bool = False, y_format: str = ",.0f", height: int = 260,
               x_values: list[float] | None = None) -> alt.LayerChart:
    """One line per series with >=8px markers, the last point direct-labelled, one y-axis."""
    m = mode()
    ink = INK[m]
    surface = "#1a1a19" if m == "dark" else "#fcfcfb"
    color = alt.Color(f"{series}:N", scale=alt.Scale(domain=domain, range=SERIES[m][: len(domain)]),
                      legend=alt.Legend(title=None, orient="bottom", labelColor=ink["secondary"]))
    x_enc = alt.X(f"{x}:Q", scale=alt.Scale(type="log" if x_log else "linear", zero=False, nice=False, padding=16),
                  axis=_axis(ink, x_title, grid=False, values=x_values))
    y_enc = alt.Y(f"{y}:Q", axis=_axis(ink, y_title, y_format), scale=alt.Scale(zero=True))
    tooltip = [alt.Tooltip(f"{series}:N"), alt.Tooltip(f"{x}:Q", title=x_title, format=",.0f"),
               alt.Tooltip(f"{y}:Q", title=y_title, format=y_format)]
    base = alt.Chart(df).encode(x=x_enc, y=y_enc, color=color, tooltip=tooltip)
    last = df.loc[df.groupby(series)[x].idxmax()]
    labels = alt.Chart(last).mark_text(align="left", dx=8, fontSize=12, color=ink["secondary"]).encode(
        x=x_enc, y=y_enc, text=f"{series}:N")
    return alt.layer(base.mark_line(strokeWidth=2), base.mark_circle(size=80, opacity=1, stroke=surface, strokeWidth=2),
                     labels).properties(height=height).configure_view(stroke=None)


def stacked_bar(df: pd.DataFrame, *, category: str, segment: str, value: str, seg_domain: list[str],
                x_title: str, step: int = 34) -> alt.Chart:
    """Horizontal stacked bars (two segments), separated by a 2px surface gap."""
    m = mode()
    ink = INK[m]
    surface = "#1a1a19" if m == "dark" else "#fcfcfb"
    order = {s: i for i, s in enumerate(seg_domain)}
    df = df.assign(_order=df[segment].map(order))
    return alt.Chart(df).mark_bar(stroke=surface, strokeWidth=2, cornerRadiusEnd=4).encode(
        y=alt.Y(f"{category}:N", sort=None, axis=_axis(ink, "", grid=False)),
        x=alt.X(f"{value}:Q", stack="zero", axis=_axis(ink, x_title, ",.0f")),
        color=alt.Color(f"{segment}:N", scale=alt.Scale(domain=seg_domain, range=SERIES[m][: len(seg_domain)]),
                        legend=alt.Legend(title=None, orient="bottom", labelColor=ink["secondary"])),
        order=alt.Order("_order:Q"),
        tooltip=[alt.Tooltip(f"{category}:N"), alt.Tooltip(f"{segment}:N"), alt.Tooltip(f"{value}:Q", format=",.0f")],
    ).properties(height=alt.Step(step)).configure_view(stroke=None)


def reliability_chart(df: pd.DataFrame, height: int = 280) -> alt.LayerChart:
    """Mean predicted risk vs observed failure rate per bin, against the perfect-calibration diagonal."""
    m = mode()
    ink = INK[m]
    surface = "#1a1a19" if m == "dark" else "#fcfcfb"
    axis_x = alt.X("mean_predicted:Q", scale=alt.Scale(domain=[0, 1]), axis=_axis(ink, "predicted failure probability", ".0%"))
    axis_y = alt.Y("observed_failure_rate:Q", scale=alt.Scale(domain=[0, 1]), axis=_axis(ink, "observed failure rate", ".0%"))
    diag = alt.Chart(pd.DataFrame({"mean_predicted": [0, 1], "observed_failure_rate": [0, 1]})).mark_line(
        color=ink["muted"], strokeWidth=1).encode(x=axis_x, y=axis_y)
    tooltip = [alt.Tooltip("bin:N"), alt.Tooltip("n:Q", title="requests"),
               alt.Tooltip("mean_predicted:Q", format=".1%"), alt.Tooltip("observed_failure_rate:Q", format=".1%")]
    base = alt.Chart(df).encode(x=axis_x, y=axis_y, tooltip=tooltip)
    line = base.mark_line(color=SERIES[m][0], strokeWidth=2)
    pts = base.mark_circle(color=SERIES[m][0], opacity=1, stroke=surface, strokeWidth=2).encode(
        size=alt.Size("n:Q", legend=None, scale=alt.Scale(range=[60, 400])))
    return alt.layer(diag, line, pts).properties(height=height).configure_view(stroke=None)


def signed_bars(df: pd.DataFrame, *, label: str, value: str, x_title: str, step: int = 22) -> alt.Chart:
    """One series of horizontal bars extending left/right of zero (sign carried by direction)."""
    m = mode()
    ink = INK[m]
    return alt.Chart(df).mark_bar(color=SERIES[m][0], cornerRadiusEnd=4).encode(
        y=alt.Y(f"{label}:N", sort="-x", axis=_axis(ink, "", grid=False)),
        x=alt.X(f"{value}:Q", axis=_axis(ink, x_title, ".2f")),
        tooltip=[alt.Tooltip(f"{label}:N"), alt.Tooltip(f"{value}:Q", format="+.3f")],
    ).properties(height=alt.Step(step)).configure_view(stroke=None)
