"""routewise command line."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from random import Random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from routewise import dataset, factsheets, faults, judge as judge_mod, latency, policy as policy_mod, runlog, synth, validator_study
from routewise.benchmark import candidate_metrics, run_benchmark
from routewise.client import MissingCredentials, TokenFactoryClient
from routewise.config import REPO_ROOT, Candidate, Config, load_config, load_dotenv
from routewise.cost import Price
from routewise.evaluation import STRATEGIES, build_routers, run_evaluation, summarize_evaluation, write_summary
from routewise.execution import call_and_score
from routewise.policy import load_policy, optimize, save_policy
from routewise.prices import PriceNotFound, fetch_snapshot, load_snapshot, nvidia_models, price_for
from routewise.prompts import PromptTemplate, load_prompt
from routewise.runlog import RunDir, read_jsonl, sha256_file
from routewise.router import Trace
from routewise.schema import TASKS, CallRecord, Example

DEFAULT_PROBE_EXAMPLE = REPO_ROOT / "data" / "probe" / "extraction_probe.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="routewise", description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="path to routewise.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    data = sub.add_parser("data", help="regenerate the seeded dataset (data/v1)")
    data.add_argument("--check", action="store_true", help="verify committed data matches the generator")

    sub.add_parser("prices", help="snapshot live model prices from /v1/models?verbose=true")

    probe = sub.add_parser("probe", help="call every candidate once on one labelled example")
    probe.add_argument("--example", type=Path, default=DEFAULT_PROBE_EXAMPLE)
    probe.add_argument("--reasoning", choices=["off", "on", "both"], default="off")
    probe.add_argument("--candidates", help="comma-separated candidate names (default: all)")

    bench = sub.add_parser("benchmark", help="run every candidate on every example of a split")
    bench.add_argument("--split", default="tune", choices=["tune", "heldout"])
    bench.add_argument("--candidates", help="comma-separated candidate names (default: all)")
    _add_load_args(bench)

    pol = sub.add_parser("policy", help="choose the cheapest qualifying route per task from a benchmark run")
    pol.add_argument("--run", type=Path, help="benchmark run directory (default: latest)")

    route = sub.add_parser("route", help="route one request and print its trace")
    src = route.add_mutually_exclusive_group(required=True)
    src.add_argument("--example", help="dataset example id")
    src.add_argument("--text", help="raw conversation text")
    route.add_argument("--task", choices=TASKS, default="extraction")
    route.add_argument("--policy", type=Path, help="policy file (default: latest)")
    route.add_argument("--strategy", choices=STRATEGIES, default="routewise")
    route.add_argument("--json", action="store_true", help="print the full trace as JSON")

    ev = sub.add_parser("evaluate", help="compare RouteWise with both baselines on held-out data")
    ev.add_argument("--policy", type=Path, help="policy file (default: latest)")
    ev.add_argument("--split", default="heldout", choices=["tune", "heldout"])
    ev.add_argument("--inject-stalls", metavar="MODEL:FRACTION:MIN-MAX",
                    help="fault injection: stall a fixed, seeded subset of MODEL's calls by MIN-MAX ms (e.g. nano:0.1:3000-6000)")
    ev.add_argument("--inject-task", default="extraction", choices=TASKS, help="task whose examples may be stalled")
    ev.add_argument("--inject-seed", type=int, default=7)
    _add_load_args(ev)

    vs = sub.add_parser("validator-study", help="M4: compare validators, incl. LLM judges, on recorded first answers")
    vs.add_argument("--answers", type=Path, nargs="+", required=True,
                    help="evaluation runs (always-smallest first answers) or benchmark runs")
    vs.add_argument("--escalation", type=Path, required=True, help="benchmark run with the escalation model's answers")
    vs.add_argument("--primary", default="nano")
    vs.add_argument("--escalate-to", default="super")
    vs.add_argument("--judges", default="nano,super", help="comma-separated judge candidates")
    vs.add_argument("--concurrency", type=int, default=6)
    vs.add_argument("--rpm", type=float, default=90)

    lp = sub.add_parser("latency-profile", help="M1: TTFT vs decode across models, input sizes and output shapes")
    lp.add_argument("--models", default="nano,super,ultra")
    lp.add_argument("--reps", type=int, default=8)
    lp.add_argument("--seed", type=int, default=7)
    lp.add_argument("--concurrency", type=int, default=2)
    lp.add_argument("--rpm", type=float, default=60)

    ls = sub.add_parser("load-sweep", help="M2-lite: closed-loop concurrency sweep with streaming timing")
    ls.add_argument("--models", default="nano,super")
    ls.add_argument("--levels", default="1,4,8,16")
    ls.add_argument("--requests", type=int, default=48, help="requests per concurrency level")
    ls.add_argument("--cooldown", type=float, default=65, help="seconds between levels")
    ls.add_argument("--seed", type=int, default=7)

    sy = sub.add_parser("synth", help="M3: write the v2 workload from fact sheets (writer models + verification)")
    sy.add_argument("--per-family", type=int, default=75)
    sy.add_argument("--limit-per-family", type=int, help="pilot: only the first N sheets of each family")
    sy.add_argument("--concurrency", type=int, default=8)
    sy.add_argument("--rpm", type=float, default=240)

    rep = sub.add_parser("report", help="recompute an evaluation's summary from its recorded traces (no API calls)")
    rep.add_argument("--run", type=Path, help="evaluation run directory (default: latest)")

    args = parser.parse_args(argv)
    config = load_config(args.config) if args.config else load_config()
    load_dotenv(config.path.parent.parent / ".env")
    commands = {
        "data": cmd_data,
        "prices": cmd_prices,
        "probe": cmd_probe,
        "benchmark": cmd_benchmark,
        "policy": cmd_policy,
        "route": cmd_route,
        "evaluate": cmd_evaluate,
        "report": cmd_report,
        "validator-study": cmd_validator_study,
        "latency-profile": cmd_latency_profile,
        "load-sweep": cmd_load_sweep,
        "synth": cmd_synth,
    }
    try:
        return commands[args.command](config, args)
    except MissingCredentials as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _add_load_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--rpm", type=float, default=50, help="client-side request budget per minute")
    p.add_argument("--limit", type=int, help="only the first N examples per task (smoke runs)")


# ---------------------------------------------------------------- commands


def cmd_data(config: Config, args: argparse.Namespace) -> int:
    if args.check:
        regenerated = "".join(e.model_dump_json() + "\n" for e in dataset.generate())
        committed = (dataset.DATA_DIR / dataset.VERSION / "examples.jsonl").read_text()
        ok = regenerated == committed
        print("data/v1 matches the generator" if ok else "data/v1 is STALE: run `routewise data`")
        return 0 if ok else 1
    manifest = dataset.write()
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_prices(config: Config, args: argparse.Namespace) -> int:
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = fetch_snapshot(client, config.base_url, config.prices_snapshot)
    print(f"Saved {len(snapshot['models'])} models to {_display(config.prices_snapshot)}")
    print(f"fetched_at {snapshot['fetched_at']}\n")

    rows = []
    for model_id in nvidia_models(snapshot):
        price = price_for(snapshot, model_id)
        meta = snapshot["models"][model_id]
        rows.append(
            [
                model_id,
                f"{price.prompt_per_million:.3f}",
                f"{price.input_cache_read * 1e6:.3f}" if price.input_cache_read is not None else "-",
                f"{price.completion_per_million:.3f}",
                str(meta.get("context_length") or "-"),
                ",".join(meta.get("supported_features") or []) or "-",
            ]
        )
    print("NVIDIA models (USD per 1M tokens):")
    _print_table(["model_id", "input", "cached", "output", "context", "features"], rows)

    print("\nConfigured candidates:")
    ok = True
    for cand in config.candidates:
        try:
            price_for(snapshot, cand.model_id)
            print(f"  ok       {cand.name:<8} {cand.model_id}")
        except PriceNotFound as exc:
            ok = False
            print(f"  MISSING  {cand.name:<8} {exc}")
    return 0 if ok else 1


def cmd_probe(config: Config, args: argparse.Namespace) -> int:
    example = Example.model_validate_json(args.example.read_text())
    prompt = load_prompt(config.prompts[example.task], example.task)
    modes = ["off", "on"] if args.reasoning == "both" else [args.reasoning]
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = _snapshot(client, config)
    plan = _priced_candidates(config, snapshot, args.candidates, modes)
    if plan is None:
        return 1

    run = RunDir.create("probe")
    run.write_manifest(
        kind="probe",
        example=_display(args.example),
        example_sha256=sha256_file(args.example),
        prompts={example.task: _prompt_meta(prompt)},
        **_common_manifest(config, snapshot, plan),
    )
    records: list[CallRecord] = []
    for cand, price in plan:
        print(f"calling {cand.label} ({cand.model_id}) ...", flush=True)
        record = call_and_score(client, config, cand, prompt, price, example)
        run.append("calls.jsonl", record)
        records.append(record)

    print(f"\nProbe {run.experiment_id} on {example.id}:")
    _print_table(
        ["candidate", "latency_ms", "in_tok", "out_tok", "reason_tok", "cost_usd", "schema", "field_acc", "error"],
        [_probe_row(r) for r in records],
    )
    _print_probe_checks(records, both_modes=len(modes) == 2)
    print(f"\nRaw records: {_display(run.path / 'calls.jsonl')}")
    return 0


def cmd_benchmark(config: Config, args: argparse.Namespace) -> int:
    examples = _limit(dataset.load_split(args.split), args.limit)
    prompts = _prompts(config)
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = _snapshot(client, config)
    plan = _priced_candidates(config, snapshot, args.candidates, None)
    if plan is None:
        return 1
    unset = [t for t, th in config.thresholds.items() if th.max_p95_latency_ms is None]
    if unset:
        print(f"warning: no latency threshold declared for {unset}; set it before tuning to keep the policy honest")

    run = RunDir.create("benchmark")
    run.write_manifest(
        kind="benchmark",
        split=args.split,
        limit=args.limit,
        dataset={"version": dataset.VERSION, "sha256": dataset.dataset_sha256()},
        example_ids=[e.id for e in examples],
        prompts={t: _prompt_meta(p) for t, p in prompts.items()},
        load={"concurrency": args.concurrency, "rpm": args.rpm},
        **_common_manifest(config, snapshot, plan),
    )
    print(f"Benchmark {run.experiment_id}: {len(examples)} examples x {len(plan)} candidates")
    records = run_benchmark(client, config, examples, plan, prompts, run, concurrency=args.concurrency, rpm=args.rpm)

    metrics = candidate_metrics(records)
    (run.path / "metrics.json").write_text(
        json.dumps([{"candidate": c, "task": t, **m} for (c, t), m in metrics.items()], indent=2) + "\n"
    )
    _print_metrics_table(metrics)
    print(f"\nRaw records: {_display(run.path / 'calls.jsonl')}")
    return 0


def cmd_policy(config: Config, args: argparse.Namespace) -> int:
    run_dir = args.run or _latest(runlog.RUNS_DIR, "benchmark-")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest["dataset"]["sha256"] != dataset.dataset_sha256():
        print("error: benchmark was run on a different dataset version", file=sys.stderr)
        return 1
    records = [CallRecord.model_validate(r) for r in read_jsonl(run_dir / "calls.jsonl")]
    wanted = set(manifest["example_ids"])
    examples = [e for e in dataset.load_examples() if e.id in wanted]
    policy = optimize(config, records, examples, source_run=manifest["experiment_id"],
                      dataset_sha256=manifest["dataset"]["sha256"])
    path = save_policy(policy)

    for task, entry in policy["tasks"].items():
        th = entry["thresholds"]
        print(f"\n{task}  (thresholds: {', '.join(f'{k}={v}' for k, v in th.items() if v is not None)})")
        rows = []
        for row in sorted(entry["options"], key=lambda r: r["metrics"]["cost_per_1k_tasks"]):
            m = row["metrics"]
            rows.append([
                ("* " if row["label"] == entry["choice_label"] else "  ") + row["label"],
                f"{m['task_accuracy']:.3f}", f"{m['field_accuracy']:.3f}",
                _fmt_ms(m["p95_latency_ms"]), f"{m['cost_per_1k_tasks']:.4f}", f"{m['escalation_rate']:.2f}",
                "yes" if row["qualifies"] else "no",
            ])
        _print_table(["option", "task_acc", "field_acc", "p95_ms", "$/1k", "esc_rate", "qualifies"], rows)
        print(f"  -> {entry['status']}: {entry['choice_label']} — {entry['reason']}")
    print(f"\nPolicy saved to {_display(path)}")
    return 0


def cmd_route(config: Config, args: argparse.Namespace) -> int:
    policy = load_policy(args.policy or _latest(policy_mod.POLICIES_DIR, "policy-", suffix=".json"))
    if args.example:
        ex = {e.id: e for e in dataset.load_examples()}[args.example]
        task, text, expected = ex.task, ex.input, ex.expected
    else:
        task, text, expected = args.task, args.text, None
    client = TokenFactoryClient(base_url=config.base_url)
    routers = build_routers(client, config, policy, _label_prices(config, _snapshot(client, config)), _prompts(config))
    trace = routers[args.strategy].route(task, text, example_id=args.example, expected=expected)
    if args.json:
        print(trace.model_dump_json(indent=2))
        return 0
    print(f"strategy {trace.strategy}  route {trace.route}  policy {trace.policy_id} ({trace.policy_status})")
    reason = policy["tasks"][task]["reason"] if args.strategy == "routewise" else "fixed baseline"
    print(f"why: {reason}")
    for call, check in zip(trace.calls, trace.validations):
        print(f"\n[{call.candidate}] {call.latency_ms} ms, {call.prompt_tokens}+{call.completion_tokens} tokens, ${call.cost_usd:.6f}")
        print(f"  output: {json.dumps(call.parsed) if call.parsed is not None else call.content}")
        for c in check["checks"]:
            print(f"  {'pass' if c['passed'] else 'FAIL'}  {c['rule']}{(': ' + c['detail']) if c['detail'] else ''}")
    if trace.escalated:
        print(f"\nescalated: {trace.escalation_reason}")
    print(f"\nfinal valid={trace.final_valid}  total {trace.latency_ms:.0f} ms  ${trace.cost_usd:.6f}")
    if trace.score:
        print(f"ground truth: task_correct={trace.score['task_correct']} field_acc={trace.score['field_accuracy']:.2f}")
    return 0


def cmd_evaluate(config: Config, args: argparse.Namespace) -> int:
    policy_path = args.policy or _latest(policy_mod.POLICIES_DIR, "policy-", suffix=".json")
    policy = load_policy(policy_path)
    if policy["dataset_sha256"] != dataset.dataset_sha256():
        print("error: policy was tuned on a different dataset version", file=sys.stderr)
        return 1
    examples = _limit(dataset.load_split(args.split), args.limit)
    prompts = _prompts(config)
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = _snapshot(client, config)
    stalls = None
    if args.inject_stalls:
        label, fraction, lo, hi = faults.parse_spec(args.inject_stalls)
        stalls = faults.plan_stalls(examples, model_label=label, model_id=config.candidate(label).model_id,
                                    fraction=fraction, min_ms=lo, max_ms=hi, seed=args.inject_seed, task=args.inject_task)
        client = faults.StallingClient(client, stalls, examples)
        print(f"Fault injection: stalling {label} on {len(stalls.delays_ms)} {args.inject_task} examples: {stalls.delays_ms}")
    routers = build_routers(client, config, policy, _label_prices(config, snapshot), prompts)

    run = RunDir.create("eval")
    run.write_manifest(
        kind="evaluation",
        injection=stalls.to_manifest() if stalls else None,
        split=args.split,
        limit=args.limit,
        policy={"id": policy["policy_id"], "path": _display(policy_path), "sha256": sha256_file(policy_path)},
        dataset={"version": dataset.VERSION, "sha256": dataset.dataset_sha256()},
        example_ids=[e.id for e in examples],
        prompts={t: _prompt_meta(p) for t, p in prompts.items()},
        strategies={name: {t: r.option(t).label for t in policy["tasks"]} for name, r in routers.items()},
        load={"concurrency": args.concurrency, "rpm": args.rpm},
        **_common_manifest(config, snapshot, [(c, price_for(snapshot, c.model_id)) for c in config.candidates]),
    )
    print(f"Evaluation {run.experiment_id}: {len(examples)} {args.split} examples x {len(routers)} strategies")
    traces = run_evaluation(routers, examples, run, concurrency=args.concurrency, rpm=args.rpm)
    summary = summarize_evaluation(traces, examples, config, policy)
    write_summary(summary, run)
    _print_evaluation(summary, policy)
    print(f"\nResults: {_display(run.path)}/summary.json, traces.jsonl")
    return 0


def cmd_report(config: Config, args: argparse.Namespace) -> int:
    run_dir = args.run or _latest(runlog.RUNS_DIR, "eval-")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    policy = load_policy(REPO_ROOT / manifest["policy"]["path"])
    wanted = set(manifest["example_ids"])
    examples = [e for e in dataset.load_examples() if e.id in wanted]
    traces = [Trace.model_validate(t) for t in read_jsonl(run_dir / "traces.jsonl")]
    summary = summarize_evaluation(traces, examples, config, policy)
    write_summary(summary, RunDir.existing(run_dir))
    _print_evaluation(summary, policy)
    print(f"\nRewrote {_display(run_dir)}/summary.json and summary.csv from {len(traces)} recorded traces")
    return 0


def cmd_validator_study(config: Config, args: argparse.Namespace) -> int:
    examples = {e.id: e for e in dataset.load_examples()}
    samples = validator_study.load_samples(args.answers, args.primary, examples)
    escalation = validator_study.load_escalation(args.escalation, args.escalate_to)
    missing = sorted({s.example.id for s in samples} - set(escalation))
    if not samples or missing:
        print(f"error: {len(samples)} samples; escalation answers missing for {missing[:10]}", file=sys.stderr)
        return 1
    judges = args.judges.split(",") if args.judges else []
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = _snapshot(client, config)
    prompts = judge_mod.load_judge_prompts()
    splits = json.loads((dataset.DATA_DIR / dataset.VERSION / "splits.json").read_text())
    ids = {s.example.id for s in samples}
    split = next((name for name in ("tune", "heldout") if ids <= set(splits[name])), "mixed")

    run = RunDir.create("validators")
    run.write_manifest(
        kind="validator_study",
        split=split,
        answers=[_display(p) for p in args.answers],
        escalation=_display(args.escalation),
        primary=args.primary,
        escalate_to=args.escalate_to,
        judges=judges,
        judge_prompts={t: {"version": p.version, "sha256": p.sha256} for t, p in prompts.items()},
        n_samples=len(samples),
        dataset={"version": dataset.VERSION, "sha256": dataset.dataset_sha256()},
        load={"concurrency": args.concurrency, "rpm": args.rpm},
        **_common_manifest(config, snapshot, [(c, price_for(snapshot, c.model_id)) for c in config.candidates]),
    )
    print(f"Validator study {run.experiment_id}: {len(samples)} first answers ({split}), judges {judges}")
    records = validator_study.run_judges(client, config, samples, judges, _label_prices(config, snapshot), prompts,
                                         run, concurrency=args.concurrency, rpm=args.rpm)
    judged = {(r.judge, r.sample_id): r for r in records}
    summary = validator_study.evaluate_validators(config, samples, judges, judged, escalation)
    summary["split"] = split
    (run.path / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    rows = []
    for name, scopes in summary["validators"].items():
        o = scopes["overall"]
        rows.append([
            name, _pct(o["error_recall"]), _pct(o["escalation_precision"]), _pct(o["false_positive_rate"]),
            _pct(o["correction_rate"]),
            *[f"{scopes[t]['task_accuracy']:.3f}" for t in sorted(k for k in scopes if k != "overall")],
            f"{o['cost_per_1k_tasks']:.4f}", _fmt_ms(o["judge_p50_ms"]),
            "yes" if all(scopes[t]["meets_quality"] for t in scopes if t != "overall") else "no",
        ])
    tasks = sorted(k for k in next(iter(summary["validators"].values())) if k != "overall")
    _print_table(["validator", "recall", "precision", "FPR", "fixed", *[f"{t[:5]}_acc" for t in tasks], "$/1k",
                  "judge_p50", "meets"], rows)
    print(f"\nRecommended (cheapest meeting the quality thresholds): {summary['recommended']}")
    print(f"Results: {_display(run.path)}/summary.json, judge_calls.jsonl")
    return 0


def cmd_latency_profile(config: Config, args: argparse.Namespace) -> int:
    models = [config.candidate(m) for m in args.models.split(",")]
    heldout = [e for e in dataset.load_split("heldout") if e.task == "extraction"]
    example = Random(args.seed).choice(heldout)
    prompt = _prompts(config)["extraction"]
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = _snapshot(client, config)
    run = RunDir.create("latency")
    run.write_manifest(
        kind="latency_profile", example_id=example.id, input_sizes=latency.INPUT_SIZES, output_shapes=latency.OUTPUT_SHAPES,
        reps=args.reps, seed=args.seed, prompt=_prompt_meta(prompt), load={"concurrency": args.concurrency, "rpm": args.rpm},
        **_common_manifest(config, snapshot, [(m, price_for(snapshot, m.model_id)) for m in models]),
    )
    n = len(models) * len(latency.INPUT_SIZES) * len(latency.OUTPUT_SHAPES) * args.reps
    print(f"Latency profile {run.experiment_id}: {n} streamed calls on {example.id}")
    records = latency.run_profile(client, config, models, _label_prices(config, snapshot), prompt, example,
                                  reps=args.reps, seed=args.seed, concurrency=args.concurrency, rpm=args.rpm,
                                  sink=lambda r: run.append("calls.jsonl", r))
    rows = latency.summarize_profile(records)
    (run.path / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    _print_table(
        ["model", "input", "output", "in_tok", "out_tok", "cached", "ttft_p50", "ttft_p95", "tpot_p50", "e2e_p50", "e2e_p95", "ttft_share"],
        [[r["model"], r["input_size"], r["output_shape"], f"{r['prompt_tokens'] or 0:.0f}", f"{r['completion_tokens'] or 0:.0f}",
          f"{r['cached_tokens'] or 0:.0f}", _fmt_ms(r["ttft_ms"]["p50"]), _fmt_ms(r["ttft_ms"]["p95"]),
          f"{r['tpot_ms']['p50']:.1f}" if r["tpot_ms"]["p50"] is not None else "-", _fmt_ms(r["e2e_ms"]["p50"]),
          _fmt_ms(r["e2e_ms"]["p95"]), _pct(r["ttft_share_median"])] for r in rows],
    )
    print(f"\nResults: {_display(run.path)}/summary.json, calls.jsonl")
    return 0


def cmd_load_sweep(config: Config, args: argparse.Namespace) -> int:
    models = [config.candidate(m) for m in args.models.split(",")]
    levels = [int(x) for x in args.levels.split(",")]
    workload = [e for e in dataset.load_split("heldout") if e.task == "extraction"]
    prompt = _prompts(config)["extraction"]
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = _snapshot(client, config)
    prices = _label_prices(config, snapshot)
    run = RunDir.create("load")
    run.write_manifest(
        kind="load_sweep", levels=levels, requests_per_level=args.requests, cooldown_s=args.cooldown, seed=args.seed,
        workload=[e.id for e in workload], prompt=_prompt_meta(prompt),
        **_common_manifest(config, snapshot, [(m, price_for(snapshot, m.model_id)) for m in models]),
    )
    print(f"Load sweep {run.experiment_id}: models {[m.label for m in models]}, levels {levels}, {args.requests} requests each")
    records = []
    first = True
    for model in models:
        for level in levels:
            if not first:
                time.sleep(args.cooldown)
            first = False
            print(f"  {model.label} c={level} ...", flush=True)
            records += latency.run_load_level(client, config, model, prices[model.label], prompt, workload,
                                              concurrency=level, n_requests=args.requests, seed=args.seed,
                                              sink=lambda r: run.append("calls.jsonl", r))
    rows = latency.summarize_load(records)
    (run.path / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
    _print_table(
        ["model", "c", "n", "err", "req/s", "out_tok/s", "ttft_p50", "ttft_p95", "e2e_p50", "e2e_p95", "e2e_p99", "retried", "over_limit"],
        [[r["model"], str(r["concurrency"]), str(r["n"]), str(r["errors"]), f"{r['throughput_rps']:.2f}",
          f"{r['output_tokens_per_s']:.0f}", _fmt_ms(r["ttft_ms"]["p50"]), _fmt_ms(r["ttft_ms"]["p95"]),
          _fmt_ms(r["e2e_ms"]["p50"]), _fmt_ms(r["e2e_ms"]["p95"]), _fmt_ms(r["e2e_ms"]["p99"]),
          str(r["retried_requests"]), str(r["over_limit_responses"])] for r in rows],
    )
    print(f"\nResults: {_display(run.path)}/summary.json, calls.jsonl")
    return 0


def cmd_synth(config: Config, args: argparse.Namespace) -> int:
    sheets = factsheets.sample_factsheets(per_family=args.per_family)
    if args.limit_per_family:
        sheets = [s for s in sheets if int(s.id[-3:]) < args.limit_per_family]
    client = TokenFactoryClient(base_url=config.base_url)
    snapshot = _snapshot(client, config)
    writers = sorted({s.writer for s in sheets})
    run = RunDir.create("synth")
    run.write_manifest(
        kind="synth", per_family=args.per_family, limit=args.limit_per_family, n_sheets=len(sheets),
        writer_prompt_sha256=hashlib.sha256(synth.WRITER_SYSTEM.encode()).hexdigest(),
        factsheets_sha256=sha256_file(Path(factsheets.__file__)),
        load={"concurrency": args.concurrency, "rpm": args.rpm},
        **_common_manifest(config, snapshot, [(config.candidate(w), price_for(snapshot, config.candidate(w).model_id)) for w in writers]),
    )
    print(f"Synthesis {run.experiment_id}: {len(sheets)} fact sheets, writers {writers}")
    records = synth.write_all(client, config, sheets, _label_prices(config, snapshot), concurrency=args.concurrency,
                              rpm=args.rpm, sink=lambda r: run.append("records.jsonl", r))
    by_family: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_family.setdefault(r["factsheet"]["family"], []).append(r)
    rows = [[fam, str(len(rs)), str(sum(r["accepted"] for r in rs)),
             f"{sum(len(r['attempts']) for r in rs) / len(rs):.2f}"] for fam, rs in sorted(by_family.items())]
    _print_table(["family", "sheets", "accepted", "mean attempts"], rows)
    problems = Counter(p.split(" ", 2)[-1].split("'")[0].split(" [")[0] for r in records for a in r["attempts"] for p in a["problems"])
    cost = sum(a["cost_usd"] or 0 for r in records for a in r["attempts"])
    print(f"\naccepted {sum(r['accepted'] for r in records)}/{len(records)}, writer cost ${cost:.3f}")
    print("most common problems:", problems.most_common(8))
    print(f"Records: {_display(run.path)}/records.jsonl")
    return 0


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.0f}%"


def _print_evaluation(summary: dict[str, Any], policy: dict[str, Any]) -> None:
    rows = []
    for strategy, entry in summary["strategies"].items():
        for scope in ["overall", *policy["tasks"]]:
            m = entry[scope]
            rows.append([
                strategy, scope, f"{m['task_accuracy']:.3f}", f"{m['field_accuracy']:.3f}",
                f"{m['cost_per_1k_tasks']:.4f}", _fmt_ms(m["p50_latency_ms"]), _fmt_ms(m["p95_latency_ms"]),
                f"{m['escalation_rate']:.2f}", f"{m['hedge_rate']:.2f}",
                "" if scope == "overall" else ("yes" if m["meets_thresholds"] else "no"),
            ])
    _print_table(["strategy", "scope", "task_acc", "field_acc", "$/1k", "p50_ms", "p95_ms", "esc_rate", "hedge_rate", "meets"], rows)
    lead = summary["lead_strategy"]
    print(f"\nValidator ({lead} first calls): {summary['strategies'][lead]['validator']}")
    for strategy, verdict in summary["verdicts"].items():
        print(f"\nVerdict [{strategy}]: {verdict['summary']}")


# ---------------------------------------------------------------- helpers


def _snapshot(client: TokenFactoryClient, config: Config) -> dict[str, Any]:
    if not config.prices_snapshot.exists():
        fetch_snapshot(client, config.base_url, config.prices_snapshot)
    return load_snapshot(config.prices_snapshot)


def _priced_candidates(
    config: Config, snapshot: dict[str, Any], names: str | None, modes: list[str] | None
) -> list[tuple[Candidate, Price]] | None:
    chosen = [config.candidate(n) for n in names.split(",")] if names else list(config.candidates)
    plan, problems = [], []
    for cand in chosen:
        try:
            price = price_for(snapshot, cand.model_id)
        except PriceNotFound as exc:
            problems.append(str(exc))
            continue
        variants = [config.with_reasoning(cand, m) for m in modes] if modes else [cand]
        plan.extend((v, price) for v in variants)
    if problems:
        print("Fix model ids in config/routewise.yaml:", *problems, sep="\n  ", file=sys.stderr)
        return None
    return plan


def _label_prices(config: Config, snapshot: dict[str, Any]) -> dict[str, Price]:
    return {c.label: price_for(snapshot, c.model_id) for c in config.candidates}


def _prompts(config: Config) -> dict[str, PromptTemplate]:
    return {task: load_prompt(path, task) for task, path in config.prompts.items()}


def _prompt_meta(p: PromptTemplate) -> dict[str, str]:
    return {"version": p.version, "sha256": p.sha256}


def _common_manifest(config: Config, snapshot: dict[str, Any], plan: list[tuple[Candidate, Price]]) -> dict[str, Any]:
    return {
        "config_sha256": sha256_file(config.path),
        "prices": {"sha256": sha256_file(config.prices_snapshot), "fetched_at": snapshot["fetched_at"]},
        "generation": {"temperature": config.temperature, "seed": config.seed, "response_format": config.response_format},
        "thresholds": {t: vars(th) for t, th in config.thresholds.items()},
        "candidates": [
            {"label": c.label, "model_id": c.model_id, "max_tokens": c.max_tokens, "extra_body": c.extra_body,
             "usd_per_1m_input": p.prompt_per_million, "usd_per_1m_output": p.completion_per_million}
            for c, p in plan
        ],
    }


def _limit(examples: list[Example], limit: int | None) -> list[Example]:
    """N evenly spaced examples per task, so smoke runs still span every template."""
    if not limit:
        return examples
    out = []
    for task in TASKS:
        pool = [e for e in examples if e.task == task]
        step = max(len(pool) / limit, 1)
        out += [pool[int(i * step)] for i in range(min(limit, len(pool)))]
    return out


def _latest(directory: Path, prefix: str, suffix: str = "") -> Path:
    found = sorted(p for p in directory.glob(f"{prefix}*{suffix}"))
    if not found:
        raise SystemExit(f"error: nothing matching {prefix}*{suffix} in {_display(directory)}")
    return found[-1]


def _probe_row(r: CallRecord) -> list[str]:
    reason = r.reasoning_tokens if r.reasoning_tokens is not None else (f"~{r.reasoning_chars}ch" if r.reasoning_chars else "-")
    return [
        r.candidate,
        f"{r.latency_ms:.0f}" if r.latency_ms is not None else "-",
        str(r.prompt_tokens if r.prompt_tokens is not None else "-"),
        str(r.completion_tokens if r.completion_tokens is not None else "-"),
        str(reason),
        f"{r.cost_usd:.6f}" if r.cost_usd is not None else "-",
        "ok" if r.parsed is not None and not r.schema_error else (r.parse_error or "invalid"),
        f"{r.score['field_accuracy']:.2f}" if r.score else "-",
        (r.error or "")[:60],
    ]


def _print_probe_checks(records: list[CallRecord], both_modes: bool) -> None:
    ok_calls = [r for r in records if r.error is None]
    print("\nFeasibility checks:")
    print(f"  calls succeeded:          {len(ok_calls)}/{len(records)}")
    print(f"  usage block present:      {sum(r.prompt_tokens is not None for r in ok_calls)}/{len(ok_calls)}")
    print(f"  schema-valid JSON:        {sum(r.parsed is not None and not r.schema_error for r in ok_calls)}/{len(ok_calls)}")
    usable_models = {r.model_id for r in ok_calls if r.prompt_tokens is not None and r.parsed is not None}
    print(f"  usable distinct models:   {len(usable_models)} (gate: >= 2)")
    if both_modes:
        print("  reasoning toggle (completion tokens off -> on):")
        by_model: dict[str, dict[str, CallRecord]] = {}
        for r in records:
            by_model.setdefault(r.model_id, {})[r.reasoning] = r
        for model_id, pair in by_model.items():
            off, on = pair.get("off"), pair.get("on")
            if off and on:
                print(f"    {model_id}: {off.completion_tokens} -> {on.completion_tokens} "
                      f"(reasoning chars {off.reasoning_chars or 0} -> {on.reasoning_chars or 0})")


def _print_metrics_table(metrics: dict[tuple[str, str], dict]) -> None:
    rows = [
        [c, t, str(m["n"]), f"{m['task_accuracy']:.3f}", f"{m['field_accuracy']:.3f}", f"{m['schema_valid_rate']:.2f}",
         _fmt_ms(m["p50_latency_ms"]), _fmt_ms(m["p95_latency_ms"]), f"{m['cost_per_1k_tasks']:.4f}", f"{m['error_rate']:.2f}"]
        for (c, t), m in metrics.items()
    ]
    _print_table(["candidate", "task", "n", "task_acc", "field_acc", "schema", "p50_ms", "p95_ms", "$/1k", "errors"], rows)


def _fmt_ms(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}"


def _display(path: Path) -> str:
    path = path.resolve()
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))


if __name__ == "__main__":
    raise SystemExit(main())
