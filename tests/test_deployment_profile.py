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
