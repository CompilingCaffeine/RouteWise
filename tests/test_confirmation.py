"""A resumed confirmation must not double-count sentinels or accept a partial run."""
import json
from pathlib import Path

import httpx

from fakes import MODELS, completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config
from routewise.confirmation import OPTIONS, report
from routewise.prices import build_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.router import Router


def test_report_uses_unique_conversations_and_requires_all_repeated_outcomes(tmp_path):
    probe = json.loads((REPO_ROOT/'data/probe/extraction_probe.json').read_text())
    cfg = load_config()
    client = TokenFactoryClient(api_key='test', http_client=httpx.Client(transport=transport(
        lambda body: httpx.Response(200, json=completion_body(json.dumps(probe['expected']))))))
    prices = {c.label: price_for(build_snapshot(MODELS, 'https://fake/v1/'), c.model_id) for c in cfg.candidates}
    prompt = load_prompt(cfg.prompts['extraction'], 'extraction')
    rows = []
    for i in range(3):
        for name, (_, option) in OPTIONS.items():
            router = Router(client, cfg, prices, {'extraction': prompt}, {'extraction': option}, strategy=name)
            trace = router.route('extraction', probe['input'], example_id=f'e{i}', expected=probe['expected']).model_dump()
            rows.append({'window': i+1, 'family': f'f{i}', 'sentinel': i == 0, 'trace': trace, 'wall_duration_ms': trace['latency_ms']})
    manifest = {'n_unique': 3, 'n_expected_observations': 45, 'seal': {'comparator': 'fixed_ultra'}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    def save():
        (tmp_path/'observations.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    save()
    partial = report(tmp_path)
    assert not partial['complete'] and not partial['hypothesis_supported']
    for window in (2, 3):
        rows.extend([{**r, 'window': window} for r in rows[:9]])
    save()
    full = report(tmp_path)
    assert full['complete']
    assert full['metrics']['current']['n'] == 3
    assert full['sentinels']['current']['n_outcomes'] == 3
    assert full['sentinels']['current']['n_conversations'] == 1
    assert full['cheapest_qualifying'] is None, 'three correct answers do not establish 95% accuracy'
