import json

import httpx
import pytest

from fakes import MODELS, completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config
from routewise.execution import call_and_score
from routewise.grounding import evidence, grounded_schema
from routewise.prices import build_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.schema import Example, output_json_schema


@pytest.mark.parametrize('oid', ['ORD-12345', 'web_98765', 'AB/12345/UK', '78000123', 'q12345z', 'SHOP-12345-B'])
def test_complete_identifiers_and_no_product_or_tracking(oid):
    text = f'[2026-04-01 09:00] Customer: The TrailMax 2 tent for order reference {oid} arrived damaged. Tracking number TRK-12345.'
    assert evidence(text)['order_ids'] == [oid]
    schema = grounded_schema(output_json_schema('extraction'), text)
    assert schema['properties']['order_id']['enum'] == [oid, None]
    assert schema['properties']['refund_amount']['enum'] == [None]


def test_dates_and_product_digits_are_not_ids_and_contact_is_first_customer():
    text = ('[2026-03-31 08:00] System: Ticket opened.\n'
            '[2026-04-01 09:00] Customer: TrailMax 2 tent bought on 2026-03-20. No order number.\n'
            '[2026-04-02 10:00] Customer: Please refund me.')
    ev = evidence(text)
    assert ev['order_ids'] == []
    assert ev['contact_date'] == '2026-04-01'


@pytest.mark.parametrize('phrase,identifier', [('ordered under AB/12345/UK', 'AB/12345/UK'),
                                              ('ordered with reference AB/12345/UK', 'AB/12345/UK'),
                                              ('refund for q12345z', 'q12345z')])
def test_development_paraphrases(phrase, identifier):
    assert evidence(f'[2026-04-01 09:00] Customer: Please help with the item I {phrase}.')['order_ids'] == [identifier]


def test_constrained_request_and_raw_normalization_audit():
    text = '[2026-04-01 09:00] Customer: Refund my order reference web_12345. No amount is stated.'
    expected = dict(refund_requested=True, refund_amount=None, currency=None, order_id='web_12345', contact_date='2026-04-01', refund_date=None)
    raw = dict(expected, contact_date='2026-04-02', refund_amount=999, currency='USD')
    bodies = []
    def chat(body):
        bodies.append(body)
        return httpx.Response(200, json=completion_body(json.dumps(raw)))
    config = load_config()
    client = TokenFactoryClient(api_key='test', http_client=httpx.Client(transport=transport(chat)))
    cand = config.candidate('nano')
    price = price_for(build_snapshot(MODELS, 'https://fake/v1/'), cand.model_id)
    prompt = load_prompt(REPO_ROOT/'prompts/extraction_v3.txt', 'extraction')
    record = call_and_score(client, config, cand, prompt, price, Example(id='x', task='extraction', difficulty='easy', input=text, expected=expected))
    assert record.score['task_correct']
    assert record.grounding['raw_parsed'] == raw
    assert set(record.grounding['changed_fields']) == {'refund_amount', 'currency', 'contact_date'}
    assert bodies[0]['response_format']['json_schema']['schema']['properties']['order_id']['enum'] == ['web_12345', None]
    assert 'expected' not in bodies[0]['messages'][0]['content']
