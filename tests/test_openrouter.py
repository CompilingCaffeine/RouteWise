"""OpenRouter as a second provider: keys, headers, host pinning, per-host prices and billing checks (offline)."""

import json
import shutil
from types import SimpleNamespace

import httpx
import pytest

import routewise.cli as cli
import routewise.runlog as runlog
from fakes import (MODELS, OPENROUTER_ENDPOINTS, OPENROUTER_MODELS, completion_body, openrouter_completion, sse_body,
                   transport)
from routewise.client import (DEFAULT_BASE_URL, OPENROUTER_BASE_URL, ChatClient, MissingCredentials,
                              TokenFactoryClient)
from routewise.config import REPO_ROOT, load_config
from routewise.cost import billing_matches, call_cost_usd
from routewise.execution import call_candidate
from routewise.prices import (PriceNotFound, build_openrouter_snapshot, build_snapshot, host_problems, price_for,
                              to_price)
from routewise.prompts import load_prompt

TEXT = ('[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.\n'
        '[2026-04-02 10:00] Agent: Your refund of $40.00 will be issued in four days. '
        'It should arrive in your account on April 10.')
ANSWER = {'refund_requested': True, 'refund_amount': 40., 'currency': 'USD', 'order_id': 'ORD-12345',
          'contact_date': '2026-04-01', 'refund_date': None}
PIN = {"allow_fallbacks": False, "require_parameters": True}


def answer(content: str):
    """Fake chat: OpenRouter-shaped when the request pins a host, Nebius-shaped otherwise."""
    def chat(body):
        if "provider" in body:
            return httpx.Response(200, json=openrouter_completion(content, body))
        return httpx.Response(200, json=completion_body(content, model=body["model"]))
    return chat


def openrouter_snapshot():
    return build_openrouter_snapshot(OPENROUTER_MODELS, OPENROUTER_ENDPOINTS, OPENROUTER_BASE_URL)


# ---------------------------------------------------------------- provider and key selection


def test_provider_follows_the_base_url_and_reads_its_own_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-key")
    seen = []
    client = ChatClient(base_url=OPENROUTER_BASE_URL, http_client=httpx.Client(transport=transport(answer("{}"), seen)))
    assert client.provider.name == "openrouter"
    client.chat(model="nvidia/x", messages=[{"role": "user", "content": "hi"}], max_tokens=8)
    request = seen[-1]
    assert request.headers["authorization"] == "Bearer or-key"
    assert request.headers["http-referer"] == "https://github.com/CompilingCaffeine/RouteWise"
    assert request.headers["x-title"] == "RouteWise"

    seen.clear()
    nebius = ChatClient(http_client=httpx.Client(transport=transport(answer("{}"), seen)))
    assert nebius.provider.name == "nebius" and nebius._base_url == DEFAULT_BASE_URL
    nebius.chat(model="nvidia/x", messages=[{"role": "user", "content": "hi"}], max_tokens=8)
    assert seen[-1].headers["authorization"] == "Bearer nebius-key"
    assert "x-title" not in seen[-1].headers
    assert ChatClient(provider="openrouter")._base_url == OPENROUTER_BASE_URL
    assert TokenFactoryClient is ChatClient


@pytest.mark.parametrize("base_url, variable, link", [
    (OPENROUTER_BASE_URL, "OPENROUTER_API_KEY", "https://openrouter.ai/settings/keys"),
    (DEFAULT_BASE_URL, "NEBIUS_API_KEY", "https://tokenfactory.nebius.com"),
])
def test_missing_key_names_the_providers_variable(monkeypatch, base_url, variable, link):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    with pytest.raises(MissingCredentials) as exc:
        ChatClient(base_url=base_url)
    assert variable in str(exc.value) and link in str(exc.value)


def test_missing_nebius_ratelimit_headers_are_not_an_error_and_host_and_cost_are_recorded():
    def chat(body):
        return httpx.Response(200, json=completion_body('{"a":1}', provider="DekaLLM", cost=0.0000671))

    result = ChatClient(api_key="t", base_url=OPENROUTER_BASE_URL,
                        http_client=httpx.Client(transport=transport(chat))).chat(
        model="nvidia/x", messages=[{"role": "user", "content": "hi"}], max_tokens=8)
    assert result.error is None and result.ratelimit == {}
    assert result.served_by == "DekaLLM" and result.reported_cost_usd == pytest.approx(0.0000671)


def test_streaming_records_host_and_reported_cost():
    body = sse_body('{"a":1}', completion_tokens=7).replace(b'"choices": [], "usage": {',
                                                           b'"provider": "Nebius", "choices": [], "usage": {"cost": 0.00001, ')

    def chat(_):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    result = ChatClient(api_key="t", base_url=OPENROUTER_BASE_URL,
                        http_client=httpx.Client(transport=transport(chat))).chat(
        model="nvidia/x", messages=[{"role": "user", "content": "hi"}], max_tokens=8, stream=True)
    assert result.served_by == "Nebius" and result.reported_cost_usd == pytest.approx(0.00001)


# ---------------------------------------------------------------- config: reasoning mapping and host pinning


def test_reasoning_flags_map_to_openrouters_unified_parameter_and_every_candidate_is_pinned():
    cfg = load_config(REPO_ROOT / "config/openrouter.yaml")
    assert cfg.provider == "openrouter" and cfg.base_url == OPENROUTER_BASE_URL
    nano, sup = cfg.candidate("nano"), cfg.candidate("super")
    assert nano.extra_body == {"reasoning": {"enabled": False}, "provider": {"order": ["nebius/fp8"], **PIN}}
    assert sup.extra_body["provider"] == {"order": ["dekallm/fp8"], **PIN}
    on = cfg.with_reasoning(nano, "on")
    assert on.extra_body == {"reasoning": {"enabled": True}, "provider": {"order": ["nebius/fp8"], **PIN}}
    assert on.host == "nebius/fp8" and on.max_tokens == 4096

    nebius = load_config(REPO_ROOT / "config/grounded.yaml")
    assert nebius.provider == "nebius"
    assert nebius.candidate("nano").extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert all(c.host is None and "provider" not in c.extra_body for c in nebius.candidates)


def test_the_openrouter_profile_differs_from_the_measured_one_only_in_provider_details():
    measured, live = load_config(REPO_ROOT / "config/grounded.yaml"), load_config(REPO_ROOT / "config/openrouter.yaml")
    for field in ("temperature", "seed", "response_format", "thresholds", "baselines", "hedge_after_percentile"):
        assert getattr(live, field) == getattr(measured, field), field
    assert {t: p.name for t, p in live.prompts.items()} == {t: p.name for t, p in measured.prompts.items()}
    assert [c.name for c in live.candidates] == [c.name for c in measured.candidates]
    assert [c.max_tokens for c in live.candidates] == [c.max_tokens for c in measured.candidates]


@pytest.mark.parametrize("edit, message", [
    (lambda t: t.replace("  host: dekallm/fp8\n", ""), "needs a pinned `host`"),
    (lambda t: t.replace("provider: openrouter", "provider: nebius"), "does not match base_url"),
])
def test_openrouter_configs_must_pin_hosts_and_name_the_right_provider(tmp_path, edit, message):
    path = tmp_path / "config" / "c.yaml"
    path.parent.mkdir()
    path.write_text(edit((REPO_ROOT / "config/openrouter.yaml").read_text()))
    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_host_pinning_is_refused_on_nebius(tmp_path):
    path = tmp_path / "config" / "c.yaml"
    path.parent.mkdir()
    path.write_text((REPO_ROOT / "config/grounded.yaml").read_text().replace(
        "  reasoning: 'off'\n- name: super", "  reasoning: 'off'\n  host: nebius/fp8\n- name: super"))
    with pytest.raises(ValueError, match="OpenRouter only"):
        load_config(path)


def test_the_pinned_request_reaches_the_wire_and_is_billed_at_the_hosts_price():
    cfg = load_config(REPO_ROOT / "config/openrouter.yaml")
    prompt = load_prompt(cfg.prompts["extraction"], "extraction")
    bodies = []

    def chat(body):
        bodies.append(body)
        return httpx.Response(200, json=openrouter_completion(json.dumps(ANSWER), body))

    client = ChatClient(api_key="t", base_url=cfg.base_url, http_client=httpx.Client(transport=transport(chat)))
    snapshot = openrouter_snapshot()
    for name, provider_name in (("nano", "Nebius"), ("super", "DekaLLM")):
        cand = cfg.candidate(name)
        record = call_candidate(client, cfg, cand, prompt, price_for(snapshot, cand.model_id, cand.host),
                                example_id="x", text=TEXT)
        body = bodies[-1]
        assert body["provider"] == {"order": [cand.host], **PIN}
        assert body["reasoning"] == {"enabled": False} and "chat_template_kwargs" not in body
        assert body["seed"] == 7 and body["temperature"] == 0.0 and body["response_format"]["type"] == "json_schema"
        assert record.served_by == provider_name and record.error is None
        assert billing_matches(record.cost_usd, record.reported_cost_usd)
    # Super's list price (Crusoe, $2.40/M out) would overstate DekaLLM's bill more than fivefold.
    assert record.cost_usd == pytest.approx(500 * 0.08e-6 + 60 * 0.45e-6)


# ---------------------------------------------------------------- prices


def test_openrouter_price_strings_map_to_floats_and_a_missing_cache_price_is_none():
    p = to_price("m", {"prompt": "0.00000008", "completion": "0.00000045", "request": "0", "internal_reasoning": "0"})
    assert (p.prompt, p.completion, p.request, p.input_cache_read) == (8e-8, 4.5e-7, 0.0, None)
    assert to_price("m", {"prompt": "1e-7", "completion": "2e-7", "input_cache_read": ""}).input_cache_read is None
    assert to_price("m", {"prompt": "1e-7", "completion": "2e-7", "input_cache_read": "5e-8"}).input_cache_read == 5e-8
    with pytest.raises(PriceNotFound, match="no fixed per-token price"):
        to_price("nvidia/switchyard", {"prompt": "-1", "completion": "-1"})


def test_openrouter_prices_come_from_the_pinned_host_not_the_list_price():
    snapshot = openrouter_snapshot()
    assert snapshot["provider"] == "openrouter"
    assert snapshot["source"] == "https://openrouter.ai/api/v1/models"
    assert "other/model" not in snapshot["models"] and "nvidia/switchyard" in snapshot["models"]
    sup = "nvidia/nemotron-3-super-120b-a12b"
    assert set(snapshot["models"][sup]["endpoints"]) == {"dekallm/fp8", "crusoe/fp8"}
    assert price_for(snapshot, sup, "dekallm/fp8").completion == 4.5e-7
    assert price_for(snapshot, sup, "crusoe/fp8").completion == 2.4e-6
    with pytest.raises(PriceNotFound, match="pin a listed host"):
        price_for(snapshot, sup, None)
    with pytest.raises(PriceNotFound, match="dekallm/fp8"):
        price_for(snapshot, sup, "deepinfra/bf16")
    with pytest.raises(PriceNotFound, match="OpenRouter snapshots only"):
        price_for(build_snapshot(MODELS, "https://fake/v1/"), "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nebius/fp8")
    required = ["structured_outputs", "seed"]
    assert host_problems(snapshot, "nvidia/nemotron-3-nano-30b-a3b", "nebius/fp8", required) == []
    assert host_problems(snapshot, "nvidia/nemotron-3-nano-30b-a3b", "novita/fp4", required) == [
        "novita/fp4 lacks structured_outputs"]


def test_committed_openrouter_snapshot_prices_every_pinned_candidate():
    cfg = load_config(REPO_ROOT / "config/openrouter.yaml")
    snapshot = json.loads(cfg.prices_snapshot.read_text())
    assert snapshot["source"].startswith("https://openrouter.ai/api/v1/")
    for cand in cfg.candidates:
        assert price_for(snapshot, cand.model_id, cand.host).prompt > 0
        assert host_problems(snapshot, cand.model_id, cand.host, cli._required_parameters(cfg)) == []


def test_billing_check_tolerates_rounding_only():
    assert billing_matches(6.84e-5, 6.84e-5 * (1 + 1e-4))
    assert not billing_matches(6.84e-5, 6.84e-5 * 1.02)
    assert billing_matches(None, 1e-5) is None and billing_matches(1e-5, None) is None


# ---------------------------------------------------------------- CLI end to end, both providers


PROVIDER_CONFIGS = [("grounded.yaml", "nebius"), ("openrouter.yaml", "openrouter")]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    monkeypatch.setattr(runlog, "RUNS_DIR", tmp_path / "runs")
    bodies = []

    def fake_client(**kwargs):
        def chat(body):
            bodies.append(body)
            return answer(json.dumps(ANSWER))(body)
        return TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(chat)), **kwargs)

    monkeypatch.setattr(cli, "TokenFactoryClient", fake_client)
    return SimpleNamespace(root=tmp_path, bodies=bodies)


@pytest.mark.parametrize("config, provider", PROVIDER_CONFIGS)
def test_route_runs_the_adopted_profile_on_either_provider(sandbox, capsys, config, provider):
    code = cli.main(["--config", str(sandbox.root / "config" / config), "route", "--policy",
                     str(REPO_ROOT / "policies/deployment/m7-extraction.json"), "--task", "extraction",
                     "--text", TEXT, "--json"])
    assert code == 0
    trace = json.loads(capsys.readouterr().out)
    assert trace["final_valid"] and trace["final_output"]["refund_date"] == "2026-04-06"
    call = trace["calls"][0]
    assert call["model_id"] == load_config(sandbox.root / "config" / config).candidate("nano").model_id
    if provider == "openrouter":
        assert sandbox.bodies[0]["provider"]["order"] == ["nebius/fp8"]
        assert call["served_by"] == "Nebius" and billing_matches(call["cost_usd"], call["reported_cost_usd"])
    else:
        assert "provider" not in sandbox.bodies[0] and call["served_by"] is None


@pytest.mark.parametrize("config, provider", PROVIDER_CONFIGS)
def test_probe_runs_on_either_provider_and_checks_the_port(sandbox, capsys, config, provider):
    assert cli.main(["--config", str(sandbox.root / "config" / config), "probe"]) == 0
    out = capsys.readouterr().out
    (run_dir,) = (sandbox.root / "runs").iterdir()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["provider"]["name"] == provider
    assert "reasoning off honoured:   3/3" in out
    if provider == "openrouter":
        assert [c["host"] for c in manifest["candidates"]] == ["nebius/fp8", "dekallm/fp8", "deepinfra/fp4"]
        assert "served by pinned host:    3/3" in out and "billing matches cost.py:  3/3" in out
    else:
        assert "served by pinned host" not in out


def test_prices_snapshots_every_host_and_flags_a_host_without_json_schema(sandbox, capsys):
    cfg = sandbox.root / "config" / "openrouter.yaml"
    assert cli.main(["--config", str(cfg), "prices"]) == 0
    snapshot = json.loads((sandbox.root / "config" / "openrouter-prices.snapshot.json").read_text())
    assert set(snapshot["models"]["nvidia/nemotron-3-nano-30b-a3b"]["endpoints"]) == {"nebius/fp8", "novita/fp4"}
    out = capsys.readouterr().out
    assert "* dekallm/fp8" in out and "ok       super" in out

    cfg.write_text(cfg.read_text().replace("host: nebius/fp8", "host: novita/fp4"))
    assert cli.main(["--config", str(cfg), "prices"]) == 1
    assert "novita/fp4 lacks structured_outputs" in capsys.readouterr().out


def test_provider_check_reports_beside_the_archived_nebius_outcomes(sandbox, capsys, monkeypatch):
    from routewise import provider_check
    from routewise.recovery_study import load_examples

    expected = {e.input: e.expected for e in load_examples("confirm", "representative")}

    def fake_client(**kwargs):
        def chat(body):
            return httpx.Response(200, json=openrouter_completion(json.dumps(expected[body["messages"][-1]["content"]]),
                                                                  body))
        return TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(chat)), **kwargs)

    monkeypatch.setattr(cli, "TokenFactoryClient", fake_client)
    code = cli.main(["--config", str(REPO_ROOT / "config/openrouter.yaml"), "provider-check", "--n", "4", "--rpm", "0"])
    assert code == 0
    (run_dir,) = (sandbox.root / "runs").glob("providercheck-*")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["reference_run"] == "datesconfirm-20261026T133902Z-ab36f4"
    assert manifest["hosts"]["nano"] == "nebius/fp8" and len(manifest["sample"]["ids"]) == 4
    assert len({provider_check.sample(4)[i].tags[0] for i in range(4)}) == 4  # spread across families
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["complete"] and summary["fresh"]["n"] == summary["reference"]["same_conversations"]["n"] == 4
    assert summary["billing"]["matches"] == summary["billing"]["with_reported_charge"] == summary["billing"]["calls"]
    assert "not a new result" in summary["status"]
    assert "escalate to the owner" in capsys.readouterr().out
