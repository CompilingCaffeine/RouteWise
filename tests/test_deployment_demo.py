import json

import httpx
import pytest

from fakes import completion_body, transport
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT
from routewise.deployment import (adopted_evidence, adopted_router, deployment_decision, explore_requirements)
from routewise.rules_baseline import extract
from routewise.runlog import sha256_file

TEXT = ('[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.\n'
        '[2026-04-02 10:00] Agent: Your refund of $40.00 will be issued in four days. '
        'It should arrive in your account on April 10.')
ANSWER = {'refund_requested': True, 'refund_amount': 40., 'currency': 'USD', 'order_id': 'ORD-12345',
          'contact_date': '2026-04-01', 'refund_date': None}


def client_for(answer):
    return TokenFactoryClient(api_key='test', http_client=httpx.Client(transport=transport(
        lambda body: httpx.Response(200, json=completion_body(json.dumps(answer))))))


def test_dashboard_builder_uses_the_cli_adopted_profile_and_contract():
    router = adopted_router(client_for(ANSWER), REPO_ROOT)
    t = router.route('extraction', TEXT)
    assert t.final_valid and t.final_output['refund_date'] == '2026-04-06'
    assert t.calls[0].parsed['refund_date'] is None
    assert len(t.calls) == 1 and t.calls[0].grounding
    t = router.route('extraction', TEXT.replace('in four days', 'in 3 business days'))
    assert not t.final_valid and t.recovery['analysis']['status'] == 'review'


def test_requirement_exploration_can_choose_fixed_model_and_abstain():
    e = adopted_evidence(REPO_ROOT)
    m, selected = explore_requirements(e, accuracy=.95, field_accuracy=.95, deadline_ms=4000)
    assert selected == 'deterministic'
    _, faster = explore_requirements(e, accuracy=.95, field_accuracy=.95, deadline_ms=2500)
    assert faster == 'fixed_grounded_super'
    _, impossible = explore_requirements(e, accuracy=1., field_accuracy=1., deadline_ms=1000)
    assert impossible is None
    # Exploration never alters the archived study's deadlines or outcomes.
    assert e['summary']['metrics']['representative']['deterministic']['deadline_misses'] == 9
    decision = deployment_decision(e, m, selected, accuracy=.95, field_accuracy=.95, deadline_ms=4000, root=REPO_ROOT)
    assert decision['profile']['date_normalization'] == 'deterministic'
    assert '--policy policies/deployment/m7-extraction.json' in decision['cli']
    assert decision['source_sha256']['src/routewise/issue_dates.py'] == sha256_file(REPO_ROOT/'src/routewise/issue_dates.py')
    assert 'synthetic' in decision['evidence']['provenance']
    fixed = deployment_decision(e, m, 'fixed_grounded_super', accuracy=.95, field_accuracy=.95, deadline_ms=2500, root=REPO_ROOT)
    assert '--strategy always_super' in fixed['cli'] and fixed['profile'] is None
    none = deployment_decision(e, m, None, accuracy=1., field_accuracy=1., deadline_ms=1000, root=REPO_ROOT)
    assert none['cli'] is None and none['profile'] is None and none['metrics'] is None


def test_rules_need_no_model_and_preserve_the_shared_field_contract():
    answer = extract(TEXT)
    assert answer.released and answer.output == {**ANSWER, 'refund_date': '2026-04-06'}
    purchase = extract('[2026-04-01 09:00] Customer: I paid $50 for order ORD-12345. Please repair it.')
    assert purchase.released and not purchase.output['refund_requested']
    assert purchase.output['refund_amount'] is None and purchase.output['currency'] is None
    no = extract('[2026-04-01 09:00] Customer: I do not want a refund for order ORD-12345.')
    assert no.released and not no.output['refund_requested']


@pytest.mark.parametrize('text', [
    TEXT.replace('in four days', 'in 3 business days'),
    TEXT + '\n[2026-04-02 11:00] Agent: The other order ORD-67890 is separate.',
    TEXT + '\n[2026-04-02 11:00] Agent: A partial refund of $20.00 is another offer.',
    'not a timestamped support conversation',
])
def test_rules_request_review_rather_than_guess_on_ambiguity(text):
    answer = extract(text)
    assert not answer.released and answer.review_reasons


def test_guided_ui_runs_the_adopted_profile_and_explains_review(monkeypatch):
    import sys
    from streamlit.testing.v1 import AppTest

    sys.path.insert(0, str(REPO_ROOT / 'app'))
    import routewise.client as client_module
    monkeypatch.delenv('ROUTEWISE_ARTIFACTS', raising=False)
    monkeypatch.setenv('NEBIUS_API_KEY', 'test')
    monkeypatch.setattr(client_module, 'TokenFactoryClient', lambda **kwargs: client_for(ANSWER))
    at = AppTest.from_file(str(REPO_ROOT / 'app/streamlit_app.py'), default_timeout=60).run()
    assert not at.exception
    sample = next(s for s in at.selectbox if s.label == 'Example conversation')
    sample.select('Relative issue date').run()
    next(b for b in at.button if b.label == 'Run on Nebius Token Factory').click().run()
    assert not at.exception
    trace = at.session_state['start-live-last-result']['trace']
    assert trace['final_valid'] and trace['final_output']['refund_date'] == '2026-04-06'
    assert any('Final output after normalization' in m.value for m in at.markdown)
    next(s for s in at.selectbox if s.label == 'Example conversation').select('Ambiguous date · review').run()
    # An edited input must not retain the old live answer.
    assert not any('LIVE · request' in c.value for c in at.caption)
    next(b for b in at.button if b.label == 'Run on Nebius Token Factory').click().run()
    assert not at.exception
    assert not at.session_state['start-live-last-result']['trace']['final_valid']
    assert any('Held for human review' in w.value for w in at.warning)
    next(s for s in at.slider if s.label == 'Required task accuracy (%)').set_value(100.).run()
    assert not at.exception
    assert any('No measured configuration' in w.value for w in at.warning)
    assert not at.button  # no runnable deployment recipe when none qualifies
