import pandas as pd
import streamlit as st

from routewise import artifacts
from routewise.config import load_config
from ui import (CONFIGURATION_LABELS, line_chart, money, ms, pct, reliability_chart, render_confirmation_takeaways,
                render_date_recovery, signed_bars, stacked_bar, tradeoff_chart, useful_comparison_rows)

config = load_config()
st.title("Experiments")
st.markdown("What each experiment taught us about quality, latency and cost. "
            "The comparisons below explain which changes are worth using.")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "M1 · Where does latency go?",
    "M2 · Latency under load",
    "M3 · Can we predict failures?",
    "M4 · Is validation worth it?",
    "M5 · Grounding + escalation",
    "M6 · Relative-date recovery",
])

with tab6:
    runs = [r for r in artifacts.list_runs('datesconfirm') if r.manifest.get('study') == 'm7'
            and (r.path/'summary.json').exists()]
    selection_path = artifacts.artifacts_root()/'data/v5/selection.json'
    if not runs:
        st.info('The M6 confirmation has not been run yet.')
    else:
        render_date_recovery(artifacts.load_json(runs[0].path/'summary.json'),
                             artifacts.load_json(selection_path) if selection_path.exists() else None)

with tab5:
    runs = [r for r in artifacts.list_runs('confirmation') if r.manifest.get('study') == 'm6'
            and (r.path/'summary.json').exists()
            and artifacts.load_json(r.path/'summary.json').get('complete')]
    if not runs:
        st.info('No completed M5 confirmation study is available yet.')
    else:
        run = runs[0]
        s = artifacts.load_json(run.path/'summary.json')
        selected = s['cheapest_qualifying']
        st.subheader('Which additions earn their cost?')
        if selected:
            label = CONFIGURATION_LABELS.get(selected, selected)
            if selected == 'grounding_only':
                label += ' without hedging'
            st.success(f"Recommended for this workload: {label}.")
        else:
            st.warning('No measured configuration clears every requirement.')
        st.caption(f"{s['n_unique']} fresh held-out conversations · three time windows · "
                   'task accuracy ≥95%, field accuracy ≥95%, p95 ≤4 s.')
        st.dataframe(useful_comparison_rows(s['metrics'], selected), hide_index=True, width='stretch')
        st.caption('All four share clarified field rules, source-backed values and metadata-derived contact dates. '
                   'Accuracy is shown with its one-sided 95% lower bound; qualification also includes the deadline-miss bound.')
        render_confirmation_takeaways(s)
        st.markdown('**Decision:** escalation earns its cost. Hedging buys a modest latency improvement, '
                    'but its extra cost is unnecessary for the tested four-second budget. '
                    'Grounded Super is faster and slightly more accurate, at a higher cost.')
        m = s['metrics'].get(selected) if selected else None
        if m and m['accuracy_family_cluster_ci95'][0] < .95:
            low, high = m['accuracy_family_cluster_ci95']
            st.warning(f"The remaining weakness is relative refund dates: 12/18 correct. "
                       f"The family-mix sensitivity interval is {pct(low)}–{pct(high)}. "
                       'All six final errors were flagged for review; validate the actual workload mix before deployment.')
        st.caption('Evidence is synthetic and agent-reviewed. Three same-day windows do not establish production availability.')
        st.markdown('Open **Workload optimizer** to change requirements, inspect source evidence or try the selected route live.')
        with st.expander('Study design and all nine configurations'):
            st.markdown('The four matched grounded configurations above isolate escalation and hedging. '
                        'The remaining controls use the original prompt and show the combined effect of changing '
                        'the prompt, source constraints and metadata-derived fields. They do not isolate those components individually.')
            st.dataframe([{
                'configuration': CONFIGURATION_LABELS.get(name, name),
                'prompt': 'Grounded v4' if name in ('fixed_grounded_nano','fixed_grounded_super','grounding_only','grounded_hedged') else 'Original v2',
                'accuracy': pct(m['task_accuracy']), 'lower 95%': pct(m['accuracy_lower95']),
                '$ / 1k': money(m['cost_per_1k_tasks']), 'p95': ms(m['p95_latency_ms']),
                'deadline-miss upper 95%': pct(m['deadline_miss_upper95']),
            } for name, m in s['metrics'].items()], hide_index=True, width='stretch')
            result = 'supported' if s['hypothesis_supported'] else 'not supported'
            st.caption(f"The predeclared grounding-plus-hedging test was {result}; the ablation identifies the cheaper unhedged choice. "
                       f"Fixed comparator selected on tuning data: {s['frozen_fixed_comparator']}. Run: {run.id}.")
            st.markdown('[Protocol](https://github.com/CompilingCaffeine/RouteWise/blob/main/experiments/M6-issue-date-confirmation.md) · '
                        '[Full report and audit records](https://github.com/CompilingCaffeine/RouteWise/blob/main/results/M6-report.md)')

# ---------------------------------------------------------------- M4
with tab4:
    runs = [r for r in artifacts.list_runs("validators") if r.manifest.get("split") == "heldout"]
    if not runs:
        st.info("No held-out validator study yet (`routewise validator-study`).", icon=":material/hourglass_empty:")
    else:
        run = runs[0]
        s = artifacts.load_json(run.path / "summary.json")
        v = s["validators"]
        st.markdown(
            f"Each validator is treated as a classifier of **wrong first answers** from Nemotron 3 Nano "
            f"({s['n_samples']} held-out answers over {s['n_examples']} conversations), then simulated end to end: "
            "flagged answers are replaced by Nemotron 3 Super's answer, and every judge and escalation call is billed. "
            "LLM judges are Nemotron 3 Nano or Super with a fixed rubric (prompts frozen on the tuning split)."
        )
        det, judge = v["deterministic"]["extraction"], v["judge_super"]["extraction"]
        c = st.columns(4)
        c[0].metric("Deterministic precision (extraction)", pct(det["escalation_precision"]),
                    help="Share of escalations that were needed. No correct answer was escalated.")
        c[1].metric("Deterministic recall (extraction)", pct(det["error_recall"]))
        c[2].metric("Super-judge recall (extraction)", pct(judge["error_recall"]))
        c[3].metric("Cost of adding a Super judge", f"{v['deterministic+judge_super']['overall']['cost_per_1k_tasks'] / v['deterministic']['overall']['cost_per_1k_tasks']:.1f}×",
                    help="Cost per 1k tasks, deterministic + Super judge vs deterministic alone.")

        # judge_super sits on top of deterministic+judge_super for extraction; the table lists every validator.
        shown = ["none", "deterministic", "judge_nano", "deterministic+judge_super"]
        df = pd.DataFrame([{"validator": name, **v[name]["extraction"]} for name in shown])
        st.markdown("**Extraction: accuracy vs. cost per 1,000 tasks**")
        st.altair_chart(tradeoff_chart(df, series="validator", domain=shown,
                                       threshold=config.thresholds["extraction"].min_task_accuracy,
                                       emphasize=["deterministic", "deterministic+judge_super"], y_floor=0.8),
                        width="stretch")
        rows = []
        for name, scopes in v.items():
            o, e, k = scopes["overall"], scopes.get("extraction", {}), scopes.get("classification", {})
            rows.append({
                "validator": name,
                "recall": pct(o["error_recall"]), "precision": pct(o["escalation_precision"]),
                "false alarms": pct(o["false_positive_rate"]), "errors fixed": pct(o["correction_rate"]),
                "extraction acc": pct(e.get("task_accuracy")), "classification acc": pct(k.get("task_accuracy")),
                "$ / 1k tasks": money(o["cost_per_1k_tasks"]), "judge p50": ms(o["judge_p50_ms"]),
                "extraction p95": ms(e.get("p95_latency_ms")),
            })
        st.dataframe(rows, hide_index=True, width="stretch")
        st.markdown(
            "- **The deterministic validator is the precise, near-free option.** It never escalated a correct "
            "answer, but it only checks the schema on classification and misses semantically wrong answers that "
            "the conversation still supports.\n"
            "- **The Super judge adds recall, mostly on classification.** It costs about 3.5× more per task and about "
            "0.8 s more on *every* request, which pushes extraction p95 over the 4 s ceiling.\n"
            "- **The Nano judge is not usable as a validator.** It flags about 31–43% of correct answers.\n"
            f"- **The pre-registered decision rule picks `{s['recommended']}`.** It chooses the cheapest validator "
            "meeting the accuracy thresholds, and pooled over three samples Nano alone clears the lenient 85% "
            "extraction bar. We report that as is. A better rule would use a lower confidence bound and the latency "
            "SLO; see the experiment note."
        )
        st.caption(f"Run `{run.id}` · escalation answers from `{run.manifest['escalation']}`")

# ---------------------------------------------------------------- M3
with tab3:
    runs = artifacts.list_runs("risk")
    v2_manifest = artifacts.artifacts_root() / "data" / "v2" / "MANIFEST.json"
    if not runs or not v2_manifest.exists():
        st.info("No failure-predictor study yet (`routewise risk-study`).", icon=":material/hourglass_empty:")
    else:
        run = runs[0]
        s = artifacts.load_json(run.path / "summary.json")
        m2 = artifacts.load_json(v2_manifest)
        t = s["test"]
        rv = t["risk_vs_validator"]
        st.markdown(
            f"A logistic-regression predictor estimates **P(Nemotron 3 Nano gets this extraction wrong)** from "
            f"{len(run.manifest['features'])} features of the raw conversation (counts of amounts, dates, order IDs and "
            "cue words), with no model call and no metadata. It is trained on a new workload of "
            f"**{m2['n_examples']:,} conversations** in {len(m2['per_family'])} scenario families. Nemotron 3 Super or "
            "Ultra wrote each conversation from a fact sheet; the labels come from the fact sheet, checked "
            "deterministically and by two strong verifiers. It is evaluated on **four families it never saw**. "
            "High-risk requests skip Nano and go straight to Super."
        )
        c = st.columns(4)
        c[0].metric("AUC on unseen families", f"{t['auc']:.2f}" if t["auc"] is not None else "–")
        c[1].metric("Random-split AUC (leaky)", f"{s['leakage']['random_cv_auc']:.2f}",
                    help=f"Family-grouped CV AUC: {s['leakage']['grouped_cv_auc']:.2f}. The gap is how much a random split would overstate.")
        c[2].metric("Accuracy vs. validator-only", f"{rv['accuracy_diff'] * 100:+.1f} pts",
                    help=f"95% paired bootstrap CI {rv['accuracy_diff_ci95'][0] * 100:+.1f} to {rv['accuracy_diff_ci95'][1] * 100:+.1f} pts")
        c[3].metric("Cost vs. validator-only", f"{rv['cost_ratio']:.2f}×")
        lo, hi = rv["accuracy_diff_ci95"]
        rule = "adopt only if the accuracy gain's 95% CI excludes zero at ≤ 2× the validator's cost"
        if s["decision"]["adopt"]:
            st.success(f"Pre-registered decision: **adopt** ({rule}; CI {lo * 100:+.1f} to {hi * 100:+.1f} pts).", icon=":material/check_circle:")
        else:
            st.warning(f"Pre-registered decision: **do not adopt**. The rule is to {rule}; the CI is {lo * 100:+.1f} to "
                       f"{hi * 100:+.1f} pts, so it does not exclude zero.", icon=":material/do_not_disturb_on:")

        names = ["always_smallest", "validator", "risk+validator", "risk_only", "oracle", "always_strongest"]
        df = pd.DataFrame([{"policy": n, **t["policies"][n]} for n in names])
        # validator and risk+validator land on the same point; one honest label instead of two colliding ones.
        df["label"] = df["policy"].map({"validator": "validator ≈ risk+validator", "risk+validator": ""}).fillna(df["policy"])
        st.markdown(f"**Unseen test families ({t['policies']['validator']['n']} requests × Nano samples): accuracy vs. cost**")
        st.altair_chart(tradeoff_chart(df, series="policy", domain=names, threshold=0.85,
                                       emphasize=["risk+validator", "validator"], y_floor=0.8, label_field="label"),
                        width="stretch")
        st.dataframe([{
            "policy": n, "task acc": pct(t["policies"][n]["task_accuracy"]), "field acc": pct(t["policies"][n]["field_accuracy"]),
            "$ / 1k tasks": money(t["policies"][n]["cost_per_1k_tasks"]), "p95": ms(t["policies"][n]["p95_latency_ms"]),
            "Super calls": pct(t["policies"][n]["super_call_rate"]), "Nano errors shipped": t["policies"][n]["missed_errors"],
        } for n in names], hide_index=True, width="stretch")
        left, right = st.columns(2)
        with left:
            st.markdown("**Calibration on unseen families**")
            st.altair_chart(reliability_chart(pd.DataFrame(t["reliability"])), width="stretch")
            st.caption(f"Brier {t['brier']:.4f} vs {t['brier_base_rate']:.4f} for a constant base-rate guess.")
        with right:
            st.markdown("**What the predictor learned** (standardized coefficients)")
            coef = pd.DataFrame([{"feature": k, "coefficient": v} for k, v in s["coefficients"].items()])
            st.altair_chart(signed_bars(coef, label="feature", value="coefficient", x_title="← lowers risk · raises risk →"),
                            width="stretch")
        st.markdown(
            f"- **It learned the families, not the difficulty.** With random folds it looks useful (AUC "
            f"{s['leakage']['random_cv_auc']:.2f}); grouped by family it is at chance ({s['leakage']['grouped_cv_auc']:.2f}), "
            f"and on the four unseen families it scores {t['auc']:.2f}. Relative-date phrases signal *easy* cases in the "
            "training families but are the core difficulty of an unseen test family, so the predictor rates its hardest "
            "test family as low-risk.\n"
            "- **The validator already captures most of the available headroom.** On the same unseen families it catches "
            "87% of Nano's wrong answers with a 1.3% false-alarm rate. The validator policy reaches 95.7% accuracy "
            "against an oracle ceiling of 99.0%, at roughly the oracle's cost and 87% below always-Ultra.\n"
            "- **Exploratory only:** a depth-3 decision tree scored 0.77 AUC on the test families. It wasn't the "
            "pre-registered model and comes from a single split, so it's a lead for a future run, not a result."
        )
        st.caption(f"Run `{run.id}` · threshold τ = {s['threshold']['tau']} chosen on calibration families · "
                   f"dataset v2: {m2['queued_for_adjudication']} conversations adjudicated, {m2['dropped_by_adjudication']} dropped")

# ---------------------------------------------------------------- M1
with tab1:
    runs = artifacts.list_runs("latency")
    if not runs:
        st.info("No latency profile yet (`routewise latency-profile`).", icon=":material/hourglass_empty:")
    else:
        run = runs[0]
        rows = artifacts.load_json(run.path / "summary.json")
        models = [m for m in ("nano", "super", "ultra") if any(r["model"] == m for r in rows)]
        st.markdown(
            "Every call is **streamed**, so latency splits into *time to first token* (network, provider queue and "
            "prefill) and *decode*. A unique nonce at the start of each prompt defeats prefix caching, so every call "
            "pays full prefill."
        )
        short_json = [r for r in rows if r["input_size"] == "short" and r["output_shape"] == "json"]
        parts = pd.DataFrame(
            [{"model": r["model"], "phase": "time to first token", "ms": r["ttft_ms"]["p50"]} for r in short_json]
            + [{"model": r["model"], "phase": "decode", "ms": r["decode_ms"]["p50"]} for r in short_json]
        )
        st.markdown("**The extraction workload (~760 prompt tokens, ~80 output tokens): median latency by phase**")
        st.altair_chart(stacked_bar(parts, category="model", segment="phase", value="ms",
                                    seg_domain=["time to first token", "decode"], x_title="milliseconds (median)"),
                        width="stretch")
        ttft = pd.DataFrame([{"model": r["model"], "prompt tokens": r["prompt_tokens"], "TTFT p50 (ms)": r["ttft_ms"]["p50"]}
                             for r in rows if r["output_shape"] == "json"])
        st.markdown("**Time to first token vs. prompt length (JSON output)**")
        st.altair_chart(line_chart(ttft, x="prompt tokens", y="TTFT p50 (ms)", series="model", domain=models,
                                   x_title="prompt tokens (log scale)", y_title="TTFT p50 (ms)", x_log=True),
                        width="stretch")
        st.dataframe([{
            "model": r["model"], "input": r["input_size"], "output": r["output_shape"],
            "prompt tok": f"{r['prompt_tokens']:,.0f}", "output tok": f"{r['completion_tokens']:,.0f}",
            "TTFT p50 / p95": f"{r['ttft_ms']['p50']:,.0f} / {r['ttft_ms']['p95']:,.0f} ms",
            "TPOT p50": f"{r['tpot_ms']['p50']:.1f} ms", "end-to-end p50 / p95": f"{r['e2e_ms']['p50']:,.0f} / {r['e2e_ms']['p95']:,.0f} ms",
            "TTFT share": pct(r["ttft_share_median"]),
        } for r in rows], hide_index=True, width="stretch")
        st.markdown(
            "- **A fixed time-to-first-token floor dominates.** It is about 0.74 s for Nano and 1.1 s for Super and "
            "Ultra, flat from ~760 to ~1,800 prompt tokens, and makes up 68–92% of an extraction call.\n"
            "- **Decode per token doesn't track model size.** Ultra decodes fastest per token (≈1.9 ms).\n"
            "- **Super's tail events occurred in both phases.** One was a slow first token, one a mid-stream decode "
            "stall. That supports hedging on total elapsed time rather than on first-token time alone."
        )
        st.caption(f"Run `{run.id}` · {sum(r['n'] for r in rows)} streamed calls · ${sum(r['cost_usd_total'] for r in rows):.2f}")

# ---------------------------------------------------------------- M2
with tab2:
    runs = artifacts.list_runs("load")
    if not runs or not (runs[0].path / "summary.json").exists():
        st.info("No completed concurrency sweep yet (`routewise load-sweep`).", icon=":material/hourglass_empty:")
    else:
        run = runs[0]
        rows = artifacts.load_json(run.path / "summary.json")
        models = [m for m in ("nano", "super") if any(r["model"] == m for r in rows)]
        levels = sorted({r["concurrency"] for r in rows})
        st.markdown(
            f"A closed-loop load generator holds *c* requests in flight ({run.manifest['requests_per_level']} "
            "requests per level, the production extraction prompt, streamed), with a cool-down between levels so "
            "each one starts in a fresh rate-limit window."
        )
        df = pd.DataFrame([{"model": r["model"], "concurrency": r["concurrency"], "p95 latency (ms)": r["e2e_ms"]["p95"],
                            "throughput (req/s)": r["throughput_rps"]} for r in rows])
        left, right = st.columns(2)
        with left:
            st.markdown("**p95 end-to-end latency**")
            st.altair_chart(line_chart(df, x="concurrency", y="p95 latency (ms)", series="model", domain=models,
                                       x_title="concurrent requests (log scale)", y_title="p95 latency (ms)", x_log=True,
                                       x_values=levels),
                            width="stretch")
        with right:
            st.markdown("**Throughput**")
            st.altair_chart(line_chart(df, x="concurrency", y="throughput (req/s)", series="model", domain=models,
                                       x_title="concurrent requests (log scale)", y_title="requests per second",
                                       x_log=True, y_format=",.1f", x_values=levels), width="stretch")
        st.dataframe([{
            "model": r["model"], "concurrency": r["concurrency"], "requests": r["n"], "errors": r["errors"],
            "req/s": f"{r['throughput_rps']:.2f}", "output tok/s": f"{r['output_tokens_per_s']:,.0f}",
            "TTFT p50 / p95": f"{r['ttft_ms']['p50']:,.0f} / {r['ttft_ms']['p95']:,.0f} ms",
            "end-to-end p50 / p95 / p99": f"{r['e2e_ms']['p50']:,.0f} / {r['e2e_ms']['p95']:,.0f} / {r['e2e_ms']['p99']:,.0f} ms",
            "429 retries": r["retried_requests"], "over-limit": r["over_limit_responses"],
        } for r in rows], hide_index=True, width="stretch")
        st.markdown(
            "- **Throughput scales sub-linearly.** Nano reaches 10.7× at 16× concurrency; its p95 rises steadily but "
            "stays under 1.8 s.\n"
            "- **Super's tail is noisier than its load.** Its p95 is not monotonic in concurrency (it peaks at c = 8). "
            "Because Super is RouteWise's escalation and hedge target, hedging can only bound the tail as well as "
            "Super's own tail allows.\n"
            "- **No rate-limit pressure appeared.** 48 requests per level never crossed Nano's 100-requests-per-minute "
            "budget, so the throttling regime was not reached. Super's prefix cache warmed up across levels "
            "(cost per level halved), which confounds comparisons across levels."
        )
        st.caption(f"Run `{run.id}` · {sum(r['n'] for r in rows)} streamed requests · ${sum(r['cost_usd_total'] for r in rows):.2f}")
