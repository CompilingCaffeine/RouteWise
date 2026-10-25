import pandas as pd
import streamlit as st

from routewise import artifacts
from routewise.config import load_config
from ui import line_chart, money, ms, pct, stacked_bar, tradeoff_chart

config = load_config()
st.title("Experiments")
st.markdown(
    "Three follow-up experiments from the project's roadmap. Each was pre-registered (question, design, hypotheses) "
    "in [`experiments/`](https://github.com/CompilingCaffeine/RouteWise/tree/main/experiments) before it ran, and "
    "each has raw per-call records in `runs/`. None of them changes the headline result."
)

tab4, tab1, tab2 = st.tabs(["M4 · Is validation worth it?", "M1 · Where does latency go?", "M2 · Latency under load"])

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

        shown = ["none", "deterministic", "judge_nano", "judge_super", "deterministic+judge_super"]
        df = pd.DataFrame([{"validator": name, **v[name]["extraction"]} for name in shown])
        st.markdown("**Extraction: accuracy vs. cost per 1,000 tasks**")
        st.altair_chart(tradeoff_chart(df, series="validator", domain=shown,
                                       threshold=config.thresholds["extraction"].min_task_accuracy,
                                       emphasize=["deterministic", "deterministic+judge_super"]), width="stretch")
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
                                       x_title="concurrent requests (log scale)", y_title="p95 latency (ms)", x_log=True),
                            width="stretch")
        with right:
            st.markdown("**Throughput**")
            st.altair_chart(line_chart(df, x="concurrency", y="throughput (req/s)", series="model", domain=models,
                                       x_title="concurrent requests (log scale)", y_title="requests per second",
                                       x_log=True, y_format=",.1f"), width="stretch")
        st.dataframe([{
            "model": r["model"], "concurrency": r["concurrency"], "requests": r["n"], "errors": r["errors"],
            "req/s": f"{r['throughput_rps']:.2f}", "output tok/s": f"{r['output_tokens_per_s']:,.0f}",
            "TTFT p50 / p95": f"{r['ttft_ms']['p50']:,.0f} / {r['ttft_ms']['p95']:,.0f} ms",
            "end-to-end p50 / p95 / p99": f"{r['e2e_ms']['p50']:,.0f} / {r['e2e_ms']['p95']:,.0f} / {r['e2e_ms']['p99']:,.0f} ms",
            "429 retries": r["retried_requests"], "over-limit": r["over_limit_responses"],
        } for r in rows], hide_index=True, width="stretch")
        st.caption(f"Run `{run.id}`")
