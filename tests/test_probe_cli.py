import json
import shutil

import httpx
import pytest

import routewise.cli as cli
import routewise.runlog as runlog
from fakes import completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT

ANSWERS = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": dict(
        refund_requested=True, refund_amount=389.99, currency="USD", order_id="ORD-48213",
        contact_date="2026-03-14", refund_date="2026-03-17",
    ),
    "nvidia/nemotron-3-super-120b-a12b": dict(
        refund_requested=True, refund_amount=120.0, currency="USD", order_id="ORD-48213",
        contact_date="2026-03-14", refund_date="2026-03-17",
    ),
}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config", ignore=shutil.ignore_patterns("prices.snapshot.json"))
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    monkeypatch.setattr(runlog, "RUNS_DIR", tmp_path / "runs")

    def chat(body):
        answer = ANSWERS.get(body["model"])
        if answer is None:
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        return httpx.Response(200, json=completion_body(json.dumps(answer), model=body["model"]))

    def fake_client(**kwargs):
        return TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(chat)), **kwargs)

    monkeypatch.setattr(cli, "TokenFactoryClient", fake_client)
    return tmp_path


def test_probe_writes_scored_priced_records(sandbox, capsys):
    code = cli.main(["--config", str(sandbox / "config" / "routewise.yaml"), "probe", "--candidates", "nano,super"])
    assert code == 0

    (run_dir,) = (sandbox / "runs").iterdir()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["prompts"]["extraction"]["version"] == "extraction_v1"
    assert [c["label"] for c in manifest["candidates"]] == ["nano", "super"]

    records = runlog.read_jsonl(run_dir / "calls.jsonl")
    by_model = {r["model_id"]: r for r in records}
    nano, sup = by_model["nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"], by_model["nvidia/nemotron-3-super-120b-a12b"]
    assert nano["score"]["fields"]["refund_amount"] is False  # fell for the $389.99 distractor
    assert sup["score"]["task_correct"] is True
    assert sup["cost_usd"] == pytest.approx(500 * 0.3e-6 + 60 * 0.9e-6)
    assert "usable distinct models:   2" in capsys.readouterr().out


def test_probe_refuses_unknown_model_ids(sandbox, capsys):
    cfg = sandbox / "config" / "routewise.yaml"
    cfg.write_text(cfg.read_text().replace("nvidia/Nemotron-3-Ultra-550b-a55b", "nvidia/does-not-exist"))
    assert cli.main(["--config", str(cfg), "probe"]) == 1
    assert "does-not-exist" in capsys.readouterr().err
    assert not (sandbox / "runs").exists()
