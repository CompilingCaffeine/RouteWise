"""Fresh requests run on Nebius's replacements for the retiring Nemotron 3 models; evidence stays on the measured ones."""

import json

import httpx

import routewise.cli as cli
from fakes import completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config
from routewise.deployment import (CONFIG, LIVE_CONFIG, adopted_evidence, adopted_router, deployment_decision,
                                  explore_requirements, live_config)
from routewise.prices import load_snapshot, price_for

LIGHTNING, SUPER_VL = "nvidia/Nemotron-3_5-Lightning", "nvidia/Nemotron-3.5-Super-VL"
TEXT = ('[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.\n'
        '[2026-04-02 10:00] Agent: Your refund of $40.00 will be issued in four days. '
        'It should arrive in your account on April 10.')
ANSWER = {'refund_requested': True, 'refund_amount': 40., 'currency': 'USD', 'order_id': 'ORD-12345',
          'contact_date': '2026-04-01', 'refund_date': None}


def test_live_config_changes_only_the_two_retiring_model_ids():
    measured, live = load_config(REPO_ROOT / CONFIG), load_config(REPO_ROOT / LIVE_CONFIG)
    assert {c.name: c.model_id for c in live.candidates} == {
        "nano": LIGHTNING, "super": SUPER_VL, "ultra": measured.candidate("ultra").model_id}
    for field in ("base_url", "temperature", "seed", "response_format", "reasoning_modes", "baselines", "thresholds",
                  "prompts", "hedge_after_percentile"):
        assert getattr(live, field) == getattr(measured, field), field
    assert [(c.name, c.reasoning, c.max_tokens, c.extra_body) for c in live.candidates] == \
           [(c.name, c.reasoning, c.max_tokens, c.extra_body) for c in measured.candidates]
    assert live.prices_snapshot != measured.prices_snapshot  # refreshing live prices never touches archived ones


def test_live_prices_come_from_token_factory_for_every_candidate():
    live = live_config(REPO_ROOT)
    snapshot = load_snapshot(live.prices_snapshot)
    assert snapshot["source"] == "https://api.tokenfactory.nebius.com/v1/models?verbose=true"
    for cand in live.candidates:
        assert price_for(snapshot, cand.model_id).prompt > 0, cand.model_id


def test_fresh_requests_use_the_replacements_and_escalate_between_them():
    models = []

    def chat(body):
        models.append(body["model"])
        if body["model"] == LIGHTNING:
            return httpx.Response(200, json=completion_body("not json"))
        return httpx.Response(200, json=completion_body(json.dumps(ANSWER)))

    client = TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(chat)))
    trace = adopted_router(client, REPO_ROOT).route("extraction", TEXT)
    assert models == [LIGHTNING, SUPER_VL]
    assert trace.escalated and trace.final_valid and trace.final_output["refund_date"] == "2026-04-06"
    assert trace.cost_complete and trace.cost_usd > 0


def test_evidence_stays_on_the_measured_models_and_the_recipe_runs_the_live_ones():
    evidence = adopted_evidence(REPO_ROOT)
    assert evidence["config"].path.name == "grounded.yaml"
    metrics, selected = explore_requirements(evidence, accuracy=.95, field_accuracy=.95, deadline_ms=4000)
    decision = deployment_decision(evidence, metrics, selected, accuracy=.95, field_accuracy=.95, deadline_ms=4000,
                                   root=REPO_ROOT)
    assert decision["cli"].startswith("routewise --config config/grounded-nemotron-3.5.yaml route")
    assert decision["evidence"]["config"] == "config/grounded.yaml"
    assert decision["evidence"]["models"]["nano"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert decision["runtime"]["models"]["nano"] == LIGHTNING
    assert "config/grounded-nemotron-3.5.yaml" in decision["source_sha256"]


def test_cli_recipe_routes_on_the_replacements(capsys, monkeypatch):
    def fake_client(**kwargs):
        return TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(
            lambda body: httpx.Response(200, json=completion_body(json.dumps(ANSWER), model=body["model"])))), **kwargs)

    monkeypatch.setattr(cli, "TokenFactoryClient", fake_client)
    code = cli.main(["--config", str(REPO_ROOT / LIVE_CONFIG), "route", "--policy",
                     str(REPO_ROOT / "policies/deployment/m7-extraction.json"), "--task", "extraction",
                     "--text", TEXT, "--json"])
    assert code == 0
    trace = json.loads(capsys.readouterr().out)
    assert trace["final_valid"] and trace["calls"][0]["model_id"] == LIGHTNING
