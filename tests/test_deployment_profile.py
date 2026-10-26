import json

import httpx

from fakes import MODELS, completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config
from routewise.evaluation import build_routers
from routewise.prices import build_snapshot, price_for
from routewise.prompts import load_prompt


def test_measured_profile_routes_with_grounding_and_no_hedge():
    cfg = load_config(REPO_ROOT/'config/grounded.yaml')
    policy = json.loads((REPO_ROOT/'policies/deployment/m6-extraction.json').read_text())
    probe = json.loads((REPO_ROOT/'data/probe/extraction_probe.json').read_text())
    bodies = []
    def chat(body):
        bodies.append(body)
        return httpx.Response(200, json=completion_body(json.dumps(probe['expected'])))
    client = TokenFactoryClient(api_key='test', http_client=httpx.Client(transport=transport(chat)))
    prices = {c.label: price_for(build_snapshot(MODELS, 'https://fake/v1/'),c.model_id) for c in cfg.candidates}
    prompts = {t:load_prompt(p,t) for t,p in cfg.prompts.items()}
    routers = build_routers(client,cfg,policy,prices,prompts)
    trace = routers['routewise'].route('extraction',probe['input'],expected=probe['expected'])
    assert trace.score['task_correct'] and trace.final_valid
    assert trace.calls[0].grounding and trace.calls[0].prompt_version == 'extraction_v4'
    assert not trace.hedged and len(trace.calls) == 1
    assert 'enum' in bodies[0]['response_format']['json_schema']['schema']['properties']['order_id']


def test_adopted_profile_normalizes_relative_issue_dates():
    cfg = load_config(REPO_ROOT/'config/grounded.yaml')
    policy = json.loads((REPO_ROOT/'policies/deployment/m7-extraction.json').read_text())
    text = ("[2026-08-11 17:57] Customer: I returned the backpack, order reference ORD-1140324, and want a refund of $246.99.\n"
            "[2026-08-12 10:07] Agent: Your return has been received and the refund will be issued in four days.")
    answer = {'refund_requested': True, 'refund_amount': 246.99, 'currency': 'USD', 'order_id': 'ORD-1140324',
              'contact_date': '2026-08-11', 'refund_date': None}
    client = TokenFactoryClient(api_key='test', http_client=httpx.Client(transport=transport(
        lambda body: httpx.Response(200, json=completion_body(json.dumps(answer))))))
    prices = {c.label: price_for(build_snapshot(MODELS, 'https://fake/v1/'), c.model_id) for c in cfg.candidates}
    prompts = {t: load_prompt(p, t) for t, p in cfg.prompts.items()}
    trace = build_routers(client, cfg, policy, prices, prompts)['routewise'].route('extraction', text)
    assert trace.final_valid and trace.final_output['refund_date'] == '2026-08-16' and len(trace.calls) == 1
