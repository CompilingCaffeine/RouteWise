import json

import httpx

from fakes import completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.dataset import load_split
from routewise.faults import StallingClient, parse_spec, plan_stalls

NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def test_parse_spec():
    assert parse_spec("nano:0.1:3000-6000") == ("nano", 0.1, 3000.0, 6000.0)


def test_plan_is_seeded_and_limited_to_task():
    examples = load_split("heldout")
    a = plan_stalls(examples, model_label="nano", model_id=NANO, fraction=0.1, min_ms=3000, max_ms=6000, seed=7, task="extraction")
    b = plan_stalls(examples, model_label="nano", model_id=NANO, fraction=0.1, min_ms=3000, max_ms=6000, seed=7, task="extraction")
    assert a.delays_ms == b.delays_ms
    n_extraction = sum(e.task == "extraction" for e in examples)
    assert len(a.delays_ms) == round(0.1 * n_extraction)
    assert all(eid.startswith("ext-") for eid in a.delays_ms)
    assert all(3000 <= ms <= 6000 for ms in a.delays_ms.values())


def test_stall_applies_only_to_planned_model_and_request_and_is_recorded():
    examples = load_split("heldout")
    plan = plan_stalls(examples, model_label="nano", model_id=NANO, fraction=0.1, min_ms=3000, max_ms=3000, seed=1, task="extraction")
    stalled_id = next(iter(plan.delays_ms))
    stalled = next(e for e in examples if e.id == stalled_id)
    other = next(e for e in examples if e.task == "extraction" and e.id not in plan.delays_ms)

    inner = TokenFactoryClient(api_key="t", http_client=httpx.Client(
        transport=transport(lambda body: httpx.Response(200, json=completion_body(json.dumps({}))))))
    slept = []
    client = StallingClient(inner, plan, examples, sleep=slept.append)

    def call(model, ex):
        return client.chat(model=model, messages=[{"role": "system", "content": "s"}, {"role": "user", "content": ex.input}], max_tokens=8)

    hit = call(NANO, stalled)
    assert slept == [3.0]
    assert hit.injected_delay_ms == 3000 and hit.latency_ms >= 3000 and hit.total_ms >= 3000
    assert call(NANO, other).injected_delay_ms == 0
    assert call("nvidia/nemotron-3-super-120b-a12b", stalled).injected_delay_ms == 0
    assert slept == [3.0]
