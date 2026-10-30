"""Provider-migration sanity check: the adopted profile on an evenly spaced sample of the M6 representative set.

This checks that a port works. It is not a new result: it writes only its own `providercheck-*` run, and it
reports the archived Nebius outcomes for the same conversations beside the fresh ones, never pooled with them.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from routewise import artifacts
from routewise.benchmark import Pacer
from routewise.client import PROVIDERS, ChatClient
from routewise.config import Config
from routewise.cost import billing_matches
from routewise.date_challenge import requires_review
from routewise.deployment import PROFILE, load_adopted, profile_router
from routewise.prices import load_snapshot
from routewise.recovery_metrics import summarize
from routewise.recovery_study import load_examples, trace_outcome
from routewise.runlog import RunDir, read_jsonl, sha256_file
from routewise.schema import Example

STATUS = "port sanity check on a sample of the M6 representative set; not a new result and never pooled with M6"


def sample(n: int) -> list[Example]:
    """N evenly spaced representative conversations in (family, id) order, so every family is covered."""
    ordered = sorted(load_examples("confirm", "representative"), key=lambda e: (e.tags[0], e.id))
    step = len(ordered) / n
    return [ordered[int(i * step)] for i in range(min(n, len(ordered)))]


def run_check(client: ChatClient, config: Config, *, n: int = 50, budget_usd: float = 1.5, concurrency: int = 4,
              rpm: float = 60, root: Path | None = None) -> Path:
    root = root or artifacts.artifacts_root()
    _, policy = load_adopted(root)
    examples = sample(n)
    router = profile_router(client, "deterministic", root, config)
    snapshot = load_snapshot(config.prices_snapshot)
    run = RunDir.create("providercheck")
    run.write_manifest(
        kind="provider_check", status=STATUS, provider=config.provider, base_url=config.base_url,
        config=_rel(config.path, root), config_sha256=sha256_file(config.path),
        hosts={c.label: c.host for c in config.candidates},
        prices={"path": _rel(config.prices_snapshot, root),
                "sha256": sha256_file(config.prices_snapshot), "fetched_at": snapshot["fetched_at"]},
        policy={"id": policy["policy_id"], "path": PROFILE.as_posix(), "sha256": sha256_file(root / PROFILE)},
        reference_run=policy["validation_run"],
        sample={"set": "representative", "rule": "evenly spaced in (family, id) order", "n": len(examples),
                "ids": [e.id for e in examples]},
        load={"concurrency": concurrency, "rpm": rpm}, budget_usd=budget_usd)
    print(f"Provider check {run.path}: {len(examples)} representative conversations on {PROVIDERS[config.provider].label}",
          flush=True)

    spent = [0.0]
    lock = threading.Lock()

    def job(ex: Example) -> None:
        with lock:
            if spent[0] >= budget_usd:
                return
        delivered = []
        start = time.perf_counter()
        trace = router.route("extraction", ex.input, example_id=ex.id, expected=ex.expected,
                             on_answer=lambda _: delivered.append((time.perf_counter() - start) * 1000))
        if delivered:
            trace.latency_ms = delivered[0]  # time to answer, as in the M6 confirmation
        row = {"set": "representative", "family": ex.tags[0], "review_required": requires_review(ex),
               "wall_duration_ms": (time.perf_counter() - start) * 1000, "trace": trace.model_dump()}
        with lock:
            run.append("observations.jsonl", row)
            spent[0] += trace.cost_usd

    pacer = Pacer(rpm)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for ex in examples:
            pacer.wait()
            futures.append(pool.submit(job, ex))
        for f in as_completed(futures):
            f.result()
    report(run.path, root)
    return run.path


def report(run_path: Path, root: Path | None = None) -> dict[str, Any]:
    """Recompute the summary from recorded observations (no API calls)."""
    root = root or artifacts.artifacts_root()
    manifest = json.loads((run_path / "manifest.json").read_text())
    ids = manifest["sample"]["ids"]
    path = run_path / "observations.jsonl"
    rows = read_jsonl(path) if path.exists() else []
    archived: dict[str, dict[str, Any]] = {}
    for r in read_jsonl(root / "runs" / manifest["reference_run"] / "observations.jsonl"):
        t = r["trace"]
        if r["set"] == "representative" and t["strategy"] == "deterministic" and t["example_id"] in ids:
            archived.setdefault(t["example_id"], r)
    fresh = {r["trace"]["example_id"]: r for r in rows}
    reference = [archived[i] for i in ids if i in fresh]  # only conversations the fresh run reached

    calls = [c for r in rows for c in r["trace"]["calls"]]
    checked = [c for c in calls if billing_matches(c["cost_usd"], c.get("reported_cost_usd")) is not None]
    drift = [{"example_id": r["trace"]["example_id"], "candidate": c["candidate"], "cost_py_usd": c["cost_usd"],
              "reported_usd": c["reported_cost_usd"]}
             for r in rows for c in r["trace"]["calls"]
             if billing_matches(c["cost_usd"], c.get("reported_cost_usd")) is False]
    outcome = {i: trace_outcome(fresh[i]) for i in ids if i in fresh}
    before = {r["trace"]["example_id"]: trace_outcome(r) for r in reference}
    changed = [{"example_id": i, "family": fresh[i]["family"],
                "archived": {"released": before[i].released, "correct": before[i].correct},
                "fresh": {"released": outcome[i].released, "correct": outcome[i].correct}}
               for i in outcome if (outcome[i].released, outcome[i].correct) != (before[i].released, before[i].correct)]
    m_new, m_old = summarize(list(outcome.values())), summarize(list(before.values()))
    compared = bool(m_new.get("n") and m_old.get("n"))
    escalate = []
    if m_new.get("undetected_errors"):
        escalate.append(f"{m_new['undetected_errors']} wrong answers released")
    if compared and m_new["cac_rate"] < m_old["cac_rate"]:
        escalate.append(f"correct without review {m_new['cac_rate']:.1%} vs {m_old['cac_rate']:.1%} on the same conversations")
    if compared and (m_new["task_accuracy"] or 0) < (m_old["task_accuracy"] or 0):
        escalate.append(f"task accuracy {m_new['task_accuracy']:.1%} vs {m_old['task_accuracy']:.1%} on the same conversations")
    if drift:
        escalate.append(f"{len(drift)} calls billed differently from cost.py")
    if m_new.get("incomplete_cost_count"):
        escalate.append(f"{m_new['incomplete_cost_count']} requests lack a usage charge")

    summary = {
        "kind": "provider_check", "status": STATUS, "run": run_path.name, "provider": manifest["provider"],
        "hosts": manifest["hosts"], "complete": len(rows) == len(ids), "n_expected": len(ids), "n_observed": len(rows),
        "fresh": m_new,
        "reference": {"run": manifest["reference_run"], "provider": "nebius", "same_conversations": m_old},
        "changed_outcomes": changed,
        "served_by": dict(Counter(f"{c['candidate']}={c.get('served_by')}" for c in calls)),
        "billing": {"calls": len(calls), "with_reported_charge": len(checked), "matches": len(checked) - len(drift),
                    "drift": drift},
        "spend_usd": {"cost_py": sum(c["cost_usd"] or 0 for c in calls),
                      "reported": sum(c.get("reported_cost_usd") or 0 for c in calls)},
        "escalate": escalate,
    }
    (run_path / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
