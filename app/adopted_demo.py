"""Shared recorded and live experiences for the deployed extraction profiles."""

import json

import streamlit as st

from routewise.client import TokenFactoryClient
from routewise.deployment import ARM_LABELS, live_config, profile_router
from ui import MAX_LIVE_ROUTES, api_key, render_trace

SAMPLES = {
    'Clear refund': ('[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.\n'
                     '[2026-04-01 10:00] Agent: Your refund of $40.00 will be issued on April 3.'),
    'Relative issue date': ('[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.\n'
                            '[2026-04-02 10:00] Agent: Your refund of $40.00 will be issued in four days. '
                            'It should arrive in your account on April 10.'),
    'Ambiguous date · review': ('[2026-04-01 09:00] Customer: Please refund order ORD-12345 for $40.00.\n'
                               '[2026-04-02 10:00] Agent: Your refund of $40.00 will be issued in 3 business days.'),
}


def recorded_examples(evidence: dict) -> dict:
    grouped = {}
    for row in evidence['observations']:
        grouped.setdefault(row['trace']['example_id'], {})[row['trace']['strategy']] = row
    selected = {}
    for arms in grouped.values():
        row = arms.get('deterministic')
        if row is None:
            continue
        t = row['trace']
        old = arms.get('control', {}).get('trace', {})
        if row['review_required'] and not t['final_valid']:
            selected.setdefault('Ambiguity held for review', arms)
        elif t['final_valid'] and (t.get('score') or {}).get('task_correct'):
            if (t.get('recovery') or {}).get('action') == 'normalized' and old.get('final_valid') \
                    and not (old.get('score') or {}).get('task_correct'):
                selected.setdefault('Wrong date corrected', arms)
            elif row['set'] == 'representative' and not t.get('escalated'):
                selected.setdefault('Straightforward refund', arms)
    order = ('Wrong date corrected', 'Straightforward refund', 'Ambiguity held for review')
    return {name: selected[name] for name in order if name in selected}


def render_recorded(evidence: dict, *, key: str) -> None:
    examples = recorded_examples(evidence)
    if not examples:
        st.info('Recorded examples are unavailable in this artifact bundle.')
        return
    name = st.segmented_control('Recorded example', list(examples), default=next(iter(examples)), key=f'{key}-example')
    arms = examples[name or next(iter(examples))]
    row = arms['deterministic']
    trace = dict(row['trace'])
    example = evidence['examples'].get(trace['example_id'])
    st.caption(f"RECORDED · {evidence['policy']['validation_run']} · "
               f"{trace['calls'][0]['started_at']} · {row['set']} set · synthetic conversation")
    previous = arms.get('control', {}).get('trace')
    if previous and name == 'Wrong date corrected':
        cols = st.columns(2)
        cols[0].metric('Previous route · wrong issue date released', previous['final_output'].get('refund_date') or 'Missing')
        cols[1].metric('Adopted route · correct issue date', trace['final_output']['refund_date'])
        st.caption('The recorded label comparison identifies the previous date as wrong. '
                   'The adopted route resolves the store’s statement from that message’s own timestamp.')
    if row['review_required']:
        trace['score'] = None  # null date is a label placeholder, not a correct autonomous answer
        st.info('The labelled outcome is human review: the conversation does not determine one issue date.')
    if name != 'Wrong date corrected':
        if trace['final_valid']:
            st.success('Released after deterministic checks. Inspect the label comparison in the trace.')
            st.json(trace['final_output'])
        else:
            st.warning('Held for human review · ' + (trace.get('recovery') or {}).get('action', 'validation failed'))
    if example:
        with st.expander('Read the source conversation'):
            st.code(example['input'], language=None, wrap_lines=True)
    with st.expander('Inspect every model call, normalization and validation check'):
        render_trace(trace, expected=example['expected'] if example and not row['review_required'] else None)


def render_live(evidence: dict, *, key: str, choice: str = 'deterministic') -> None:
    policy = evidence['policy']
    config = live_config()
    roles = ('super',) if choice == 'fixed_grounded_super' else ('nano', 'super')
    models = ' → '.join(config.candidate(r).model_id.removeprefix('nvidia/') for r in roles)
    st.caption(f"Configuration: {ARM_LABELS[choice]} · prompt extraction_v4 · Nebius Token Factory · {models}")
    if choice == 'deterministic':
        st.caption(f"Active profile: {policy['policy_id']} · validation run: {policy['validation_run']}")
    st.caption('Recorded results were measured on Nemotron 3 Nano and Super, which Nebius removes from Token Factory '
               "Serverless on November 9, 2026. Fresh requests use Nebius's announced replacements, which have not "
               'been re-validated on this workload.')
    name = st.selectbox('Example conversation', [*SAMPLES, 'Write a conversation'], key=f'{key}-sample')
    text = st.text_area('Conversation', value=SAMPLES.get(name, ''), height=170, max_chars=12000,
                        key=f'{key}-text-{name}')
    st.caption('Use timestamped Customer and Agent messages. Samples are illustrative synthetic inputs. '
               'Live model outputs can differ from the recorded examples. Do not enter customer data in this public demo.')
    token = api_key()
    used = st.session_state.get('live_routes', 0)
    if not token:
        st.info('Live inference is unavailable on this deployment. Recorded examples above remain inspectable.')
    if used >= MAX_LIVE_ROUTES:
        st.info('The live-call allowance for this session is used. You can still inspect recorded evidence.')
    identity = (choice, text)
    state_key = f'{key}-last-result'
    if st.button('Run on Nebius Token Factory', type='primary', key=f'{key}-run',
                 disabled=not token or not text.strip() or used >= MAX_LIVE_ROUTES):
        st.session_state.pop(state_key, None)
        st.session_state['live_routes'] = used + 1
        try:
            with st.spinner('Calling NVIDIA Nemotron on Nebius Token Factory…'):
                client = TokenFactoryClient(api_key=token, base_url=config.base_url)
                trace = profile_router(client, choice, config=config).route('extraction', text)
            st.session_state[state_key] = {'identity': identity, 'trace': trace.model_dump()}
        except Exception:
            st.error('The live request could not complete. No usable answer was produced. '
                     'Please retry or inspect a recorded example.')
    result = st.session_state.get(state_key)
    if result and result['identity'] == identity:
        trace = result['trace']
        st.caption(f"LIVE · request {trace['request_id']} · {trace['calls'][0]['started_at']}")
        render_trace(trace)
        st.download_button('Download this live trace', json.dumps(trace, indent=2),
                           'routewise-live-trace.json', 'application/json', key=f'{key}-trace')
