"""M4: compare validators as classifiers of wrong first answers, and simulate them end to end.

First answers and escalation answers are recorded calls; only judge calls are new. Every
validator is scored against ground truth (which no validator ever sees) and priced with the
single cost function: first call + judge call (if any) + escalation call (if flagged).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from routewise.benchmark import run_parallel
from routewise.client import TokenFactoryClient
from routewise.config import Config
from routewise.cost import Price
from routewise.evaluate import score
from routewise.judge import JudgePrompt, JudgeRecord, run_judge
from routewise.metrics import Outcome, percentile, summarize
from routewise.policy import violations
from routewise.runlog import RunDir, read_jsonl
from routewise.schema import CallRecord, Example
from routewise.validate import validate

DETERMINISTIC_VALIDATORS = ("none", "schema", "deterministic")


def validator_names(judges: Sequence[str]) -> list[str]:
    return [*DETERMINISTIC_VALIDATORS, *(f"judge_{j}" for j in judges), *(f"deterministic+judge_{j}" for j in judges)]


@dataclass(frozen=True)
class Sample:
    sample_id: str
    run_id: str
    example: Example
    first: CallRecord


def load_samples(run_dirs: Sequence[Path], primary: str, examples: dict[str, Example]) -> list[Sample]:
    """First answers by `primary`: always-smallest traces of evaluation runs, or benchmark records."""
    samples = []
    for run_dir in run_dirs:
        manifest = json.loads((run_dir / "manifest.json").read_text())
        run_id = manifest["experiment_id"]
        if manifest["kind"] == "evaluation":
            firsts = [CallRecord.model_validate(t["calls"][0]) for t in read_jsonl(run_dir / "traces.jsonl")
                      if t["strategy"] == "always_smallest"]
        else:
            firsts = [CallRecord.model_validate(r) for r in read_jsonl(run_dir / "calls.jsonl")]
        for rec in firsts:
            if rec.candidate == primary and rec.example_id in examples:
                samples.append(Sample(f"{run_id}:{rec.example_id}", run_id, examples[rec.example_id], rec))
    return sorted(samples, key=lambda s: s.sample_id)


def load_escalation(run_dir: Path, label: str) -> dict[str, CallRecord]:
    return {r["example_id"]: CallRecord.model_validate(r) for r in read_jsonl(run_dir / "calls.jsonl")
            if r["candidate"] == label}


def run_judges(
    client: TokenFactoryClient,
    config: Config,
    samples: Sequence[Sample],
    judges: Sequence[str],
    prices: dict[str, Price],
    prompts: dict[str, JudgePrompt],
    run: RunDir,
    *,
    concurrency: int,
    rpm: float,
    progress: Callable[[str], None] = print,
) -> list[JudgeRecord]:
    """Judge every schema-valid first answer once per judge model."""
    lock = threading.Lock()
    todo = [(s, j) for s in samples if s.first.parsed_output().schema_valid for j in judges]
    done = [0]

    def job(sample: Sample, judge: str) -> Callable[[], JudgeRecord]:
        def run_one() -> JudgeRecord:
            rec = run_judge(client, config, config.candidate(judge), prompts[sample.example.task], prices[judge],
                            sample_id=sample.sample_id, example_id=sample.example.id, source=sample.example.input,
                            answer=sample.first.parsed)
            with lock:
                run.append("judge_calls.jsonl", rec)
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == len(todo):
                    progress(f"  {done[0]}/{len(todo)} judge calls")
            return rec

        return run_one

    return run_parallel([job(s, j) for s, j in todo], concurrency=concurrency, rpm=rpm)  # type: ignore[return-value]


@dataclass(frozen=True)
class Decision:
    flagged: bool
    cost_usd: float
    latency_ms: float
    judge_ran: bool


def decide(validator: str, sample: Sample, judged: dict[tuple[str, str], JudgeRecord]) -> Decision:
    parsed = sample.first.parsed_output()
    if validator == "none":
        return Decision(False, 0.0, 0.0, False)
    if validator == "schema":
        return Decision(not parsed.schema_valid, 0.0, 0.0, False)
    deterministic_ok = validate(sample.example.task, sample.example.input, parsed).passed
    if validator == "deterministic":
        return Decision(not deterministic_ok, 0.0, 0.0, False)
    combined = validator.startswith("deterministic+")
    judge = validator.split("judge_", 1)[1]
    # Judges only see schema-valid answers; the combined validator is a cascade that skips the
    # judge when the deterministic rules already reject the answer.
    gate_ok = deterministic_ok if combined else parsed.schema_valid
    if not gate_ok:
        return Decision(True, 0.0, 0.0, False)
    rec = judged[(judge, sample.sample_id)]
    latency = rec.latency_ms if rec.latency_ms is not None else rec.total_ms
    return Decision(rec.flagged, rec.cost_usd or 0.0, latency, True)


def evaluate_validators(
    config: Config,
    samples: Sequence[Sample],
    judges: Sequence[str],
    judged: dict[tuple[str, str], JudgeRecord],
    escalation: dict[str, CallRecord],
) -> dict[str, Any]:
    tasks = sorted({s.example.task for s in samples})
    results: dict[str, Any] = {}
    for validator in validator_names(judges):
        per_scope: dict[str, Any] = {}
        for scope in ["overall", *tasks]:
            scoped = [s for s in samples if scope == "overall" or s.example.task == scope]
            outcomes, judge_latencies = [], []
            tp = fp = fn = tn = fixed = 0
            for s in scoped:
                d = decide(validator, s, judged)
                first_score = score(s.example.task, s.example.expected, s.first.parsed)
                esc = escalation[s.example.id] if d.flagged else None
                final_score = score(s.example.task, s.example.expected, esc.parsed) if esc else first_score
                wrong = not first_score.task_correct
                tp += d.flagged and wrong
                fp += d.flagged and not wrong
                fn += (not d.flagged) and wrong
                tn += (not d.flagged) and not wrong
                fixed += d.flagged and wrong and final_score.task_correct
                if d.judge_ran:
                    judge_latencies.append(d.latency_ms)
                first_lat = s.first.latency_ms if s.first.latency_ms is not None else s.first.total_ms
                esc_lat = (esc.latency_ms if esc.latency_ms is not None else esc.total_ms) if esc else 0.0
                outcomes.append(Outcome(
                    example_id=s.sample_id,
                    task=s.example.task,
                    correct=final_score.task_correct,
                    field_accuracy=final_score.field_accuracy,
                    cost_usd=(s.first.cost_usd or 0.0) + d.cost_usd + ((esc.cost_usd or 0.0) if esc else 0.0),
                    latency_ms=first_lat + d.latency_ms + esc_lat,
                    escalated=d.flagged,
                    cost_complete=s.first.cost_usd is not None and (esc is None or esc.cost_usd is not None),
                ))
            m = summarize(outcomes)
            m.update({
                "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
                "error_recall": tp / (tp + fn) if tp + fn else None,
                "escalation_precision": tp / (tp + fp) if tp + fp else None,
                "false_positive_rate": fp / (fp + tn) if fp + tn else None,
                "correction_rate": fixed / tp if tp else None,
                "judge_calls": len(judge_latencies),
                "judge_p50_ms": percentile(judge_latencies, 50),
                "judge_p95_ms": percentile(judge_latencies, 95),
            })
            if scope != "overall":
                quality = [v for v in violations(m, config.thresholds[scope]) if not v.startswith("p95")]
                m["quality_violations"] = quality
                m["meets_quality"] = not quality
            per_scope[scope] = m
        results[validator] = per_scope

    qualifying = [v for v in results if all(results[v][t]["meets_quality"] for t in tasks)]
    recommended = min(qualifying, key=lambda v: results[v]["overall"]["cost_per_1k_tasks"]) if qualifying else None
    return {
        "n_samples": len(samples),
        "n_examples": len({s.example.id for s in samples}),
        "runs": sorted({s.run_id for s in samples}),
        "validators": results,
        "recommended": recommended,
        "qualifying": qualifying,
    }
