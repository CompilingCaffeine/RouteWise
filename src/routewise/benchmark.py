"""Runs every candidate on every example of a split and records each call."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from routewise.client import TokenFactoryClient
from routewise.config import Candidate, Config
from routewise.cost import Price
from routewise.execution import call_and_score
from routewise.metrics import Outcome, summarize
from routewise.prompts import PromptTemplate
from routewise.runlog import RunDir
from routewise.schema import CallRecord, Example


class Pacer:
    """Spaces dispatches to stay under a requests-per-minute budget across threads."""

    def __init__(self, rpm: float) -> None:
        self._interval = 60.0 / rpm if rpm > 0 else 0.0
        self._next = time.monotonic()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        if slot > now:
            time.sleep(slot - now)


def run_parallel(jobs: Sequence[Callable[[], object]], *, concurrency: int, rpm: float) -> list[object]:
    pacer = Pacer(rpm)

    def paced(job: Callable[[], object]) -> object:
        pacer.wait()
        return job()

    results: list[object] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(paced, job) for job in jobs]
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


def run_benchmark(
    client: TokenFactoryClient,
    config: Config,
    examples: Sequence[Example],
    candidates: Sequence[tuple[Candidate, Price]],
    prompts: dict[str, PromptTemplate],
    run: RunDir,
    *,
    concurrency: int = 4,
    rpm: float = 50,
    progress: Callable[[str], None] = print,
) -> list[CallRecord]:
    lock = threading.Lock()
    done = [0]
    total = len(examples) * len(candidates)

    def job(example: Example, cand: Candidate, price: Price) -> Callable[[], CallRecord]:
        def run_one() -> CallRecord:
            record = call_and_score(client, config, cand, prompts[example.task], price, example)
            with lock:
                run.append("calls.jsonl", record)
                done[0] += 1
                if done[0] % 10 == 0 or done[0] == total:
                    progress(f"  {done[0]}/{total} calls")
            return record

        return run_one

    # Interleave candidates per example so no single model endpoint takes a burst.
    jobs = [job(ex, cand, price) for ex in examples for cand, price in candidates]
    return run_parallel(jobs, concurrency=concurrency, rpm=rpm)  # type: ignore[return-value]


def record_outcome(record: CallRecord) -> Outcome:
    score = record.score or {"task_correct": False, "field_accuracy": 0.0}
    return Outcome(
        example_id=record.example_id,
        task=record.task,
        correct=bool(score["task_correct"]),
        field_accuracy=float(score["field_accuracy"]),
        cost_usd=record.cost_usd or 0.0,
        latency_ms=record.latency_ms,
        cost_complete=record.cost_usd is not None,
    )


def candidate_metrics(records: Sequence[CallRecord]) -> dict[tuple[str, str], dict]:
    """Per (candidate label, task) metrics for single-model use, from benchmark records."""
    groups: dict[tuple[str, str], list[CallRecord]] = defaultdict(list)
    for r in records:
        groups[(r.candidate, r.task)].append(r)
    out = {}
    for key, recs in sorted(groups.items()):
        m = summarize([record_outcome(r) for r in recs])
        m["schema_valid_rate"] = sum(r.parsed is not None and not r.schema_error for r in recs) / len(recs)
        m["error_rate"] = sum(r.error is not None for r in recs) / len(recs)
        m["mean_completion_tokens"] = _mean([r.completion_tokens for r in recs])
        m["model_id"] = recs[0].model_id
        out[key] = m
    return out


def _mean(values: Sequence[int | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None
