"""benchmark -> policy -> evaluate against a fake endpoint with scripted model behaviour."""

import json
import shutil
from types import SimpleNamespace

import httpx
import pytest

import routewise.cli as cli
import routewise.policy as policy_mod
import routewise.runlog as runlog
from fakes import completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT
from routewise.dataset import load_examples
from routewise.textfacts import analyze

BY_INPUT = {e.input: e for e in load_examples()}
NANO, SUPER, ULTRA = (
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/Nemotron-3-Ultra-550b-a55b",
)


def scripted_answer(model: str, text: str) -> dict:
    ex = BY_INPUT[text]
    answer = dict(ex.expected)
    if model != NANO or ex.difficulty != "adversarial":
        return answer
    # The small model fails in characteristic ways on adversarial items.
    if ex.task == "classification":
        return {"category": "other"}
    if "distractor_amount" in ex.tags and answer["refund_amount"] is not None:
        answer["refund_amount"] = max(m.amount for m in analyze(text).money)
    if "relative_date" in ex.tags:
        answer["refund_date"] = None
    return answer


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config", ignore=shutil.ignore_patterns("prices.snapshot.json"))
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    monkeypatch.setattr(runlog, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(policy_mod, "POLICIES_DIR", tmp_path / "policies")
    calls = []

    def chat(body):
        calls.append(body["model"])
        answer = scripted_answer(body["model"], body["messages"][-1]["content"])
        tokens = {NANO: 40, SUPER: 50, ULTRA: 60}[body["model"]]
        return httpx.Response(200, json=completion_body(json.dumps(answer), completion_tokens=tokens))

    def fake_client(**kwargs):
        return TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(chat)), **kwargs)

    monkeypatch.setattr(cli, "TokenFactoryClient", fake_client)
    return SimpleNamespace(root=tmp_path, calls=calls)


def run_cli(sandbox, *args):
    return cli.main(["--config", str(sandbox.root / "config" / "routewise.yaml"), *args])


def test_end_to_end(sandbox, capsys):
    assert run_cli(sandbox, "benchmark", "--split", "tune", "--limit", "12", "--rpm", "0") == 0
    (bench,) = (sandbox.root / "runs").glob("benchmark-*")
    assert len(runlog.read_jsonl(bench / "calls.jsonl")) == 24 * 3

    calls_before_policy = len(sandbox.calls)
    assert run_cli(sandbox, "policy") == 0
    assert len(sandbox.calls) == calls_before_policy, "policy optimization must not call the API"
    (policy_path,) = (sandbox.root / "policies").glob("policy-*.json")
    policy = json.loads(policy_path.read_text())
    for task, entry in policy["tasks"].items():
        chosen = next(o for o in entry["options"] if o["label"] == entry["choice_label"])
        assert chosen["qualifies"] == (entry["status"] == "ok"), task
    # nano alone is wrong on adversarial items, so it cannot be the extraction choice on its own
    assert policy["tasks"]["extraction"]["choice"] != {"primary": "nano", "escalate_to": None}

    assert run_cli(sandbox, "evaluate", "--limit", "12", "--rpm", "0") == 0
    (ev,) = (sandbox.root / "runs").glob("eval-*")
    traces = runlog.read_jsonl(ev / "traces.jsonl")
    assert len(traces) == 24 * 3
    assert {t["strategy"] for t in traces} == {"always_smallest", "always_strongest", "routewise"}
    for t in traces:
        assert len(t["calls"]) <= 2
        assert t["escalated"] == (len(t["calls"]) == 2)
        if t["escalated"]:
            assert t["escalation_reason"].startswith("validator failed")
            assert t["validations"][0]["passed"] is False

    summary = json.loads((ev / "summary.json").read_text())
    strongest = summary["strategies"]["always_strongest"]["overall"]
    assert strongest["task_accuracy"] == 1.0
    assert summary["verdict"]["summary"]
    assert (ev / "summary.csv").read_text().startswith("strategy,scope,")
    out = capsys.readouterr().out
    assert "Verdict:" in out


def test_policy_reports_no_qualifying_option(sandbox, capsys):
    cfg = sandbox.root / "config" / "routewise.yaml"
    cfg.write_text(cfg.read_text().replace("min_task_accuracy: 0.95", "min_task_accuracy: 1.01"))
    assert run_cli(sandbox, "benchmark", "--limit", "6", "--rpm", "0") == 0
    assert run_cli(sandbox, "policy") == 0
    (policy_path,) = (sandbox.root / "policies").glob("policy-*.json")
    entry = json.loads(policy_path.read_text())["tasks"]["classification"]
    assert entry["status"] == "no_qualifying_policy"
    assert "task accuracy" in entry["reason"]


def test_dashboard_pages_render_from_artifacts(sandbox, monkeypatch):
    from streamlit.testing.v1 import AppTest

    assert run_cli(sandbox, "benchmark", "--limit", "8", "--rpm", "0") == 0
    assert run_cli(sandbox, "policy") == 0
    assert run_cli(sandbox, "evaluate", "--limit", "8", "--rpm", "0") == 0
    monkeypatch.setenv("ROUTEWISE_ARTIFACTS", str(sandbox.root))
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)

    at = AppTest.from_file(str(REPO_ROOT / "app" / "streamlit_app.py"), default_timeout=60)
    at.run()
    for page in ["views/overview.py", "views/benchmark.py", "views/live_router.py", "views/evaluation.py"]:
        at.switch_page(page)
        at.run()
        assert not at.exception, (page, [e.value for e in at.exception])
        assert at.title, page
