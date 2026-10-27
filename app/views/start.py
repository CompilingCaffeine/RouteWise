"""A guided, evidence-scoped model deployment decision."""

import json

import pandas as pd
import streamlit as st

from adopted_demo import render_live, render_recorded
from routewise import artifacts
from routewise.deployment import (ARM_LABELS, adopted_evidence, deployment_decision, explore_requirements)
from ui import money, pct, tradeoff_chart

st.title('RouteWise')
st.markdown('### Make AI inference cheaper. Keep your reliability requirements.')
st.markdown('Not every AI request needs your most powerful model. RouteWise benchmarks NVIDIA Nemotron '
            'models on Nebius Token Factory and routes requests to the least expensive configuration '
            'that meets your accuracy and latency requirements.')

try:
    evidence = adopted_evidence()
except (FileNotFoundError, ValueError, KeyError) as exc:
    st.info('The adopted-profile evidence bundle is unavailable. Open Evidence archive for the available studies.')
    st.stop()

representative = evidence['summary']['metrics']['representative']
adopted, super_model = representative['deterministic'], representative['fixed_grounded_super']
saving = 1 - adopted['cost_per_1k_tasks'] / super_model['cost_per_1k_tasks']
st.subheader('Measured results · adopted refund-extraction profile')
hero = st.columns(3)
hero[0].metric('Lower inference cost vs grounded Super', pct(saving))
hero[1].metric('Correct without human review', pct(adopted['cac_rate']))
hero[2].metric('Wrong answers released', adopted['undetected_errors'])
st.markdown(r'**At 1 million requests/month: \$80 RouteWise vs \$349 grounded Super — \$269 projected savings.**')
st.caption('312 representative synthetic conversations. Independent human validation completed for this dataset. '
           'Recorded inference usage costs; hosting and human review excluded. The 4-second deadline gate '
           'passed in this confirmation; the later noise study missed it. These are measured results, not production guarantees.')

st.subheader('1 · Choose your requirements')
st.caption('Explore the 312-conversation representative confirmation set. Changing requirements here '
           'does not retune a policy or create a fresh confirmation. The 367-case date challenge stays separate.')
cols = st.columns(3)
accuracy = cols[0].slider('Required task accuracy (%)', 80., 100., 95., .5, key='start-accuracy') / 100
field = cols[1].slider('Required field accuracy (%)', 90., 100., 95., .5, key='start-field') / 100
deadline = cols[2].slider('p95 deadline (ms)', 1000, 8000, 4000, 250, key='start-deadline')
metrics, selected = explore_requirements(evidence, accuracy=accuracy, field_accuracy=field, deadline_ms=deadline)

st.subheader('2 · Inspect the measured choice')
if selected:
    st.success(f'Lowest-cost qualifying configuration: {ARM_LABELS[selected]}')
else:
    st.warning('No measured configuration clears every requirement. There is no deployment recommendation.')
rows = []
for name, m in sorted(metrics.items(), key=lambda item: item[1]['cost_per_1k_tasks']):
    rows.append({'configuration': ARM_LABELS.get(name, name),
                 'task accuracy (lower 95%)': f"{pct(m['task_accuracy'])} ({pct(m['accuracy_lower95'])})",
                 'correct without review': pct(m['cac_rate']), 'review': pct(m['review_rate']),
                 'wrong releases': m['undetected_errors'], '$ / 1k': money(m['cost_per_1k_tasks'], 3),
                 'p95': f"{m['p95_latency_ms']/1000:.2f} s",
                 'deadline-miss upper 95%': pct(m['deadline_miss_upper95']),
                 'choice': 'Selected' if name == selected else ''})
st.dataframe(rows, hide_index=True, width='stretch')
st.caption('Qualification uses the one-sided 95% task-accuracy lower bound, field accuracy point estimate, '
           'a deadline-miss upper bound of at most 5%, and complete billing. Task accuracy scores answerable '
           'conversations; correct without review uses all incoming requests. A review is not an autonomous answer.')
frame = pd.DataFrame([{'configuration': ARM_LABELS.get(name, name), **m} for name, m in metrics.items()])
frame['label'] = frame['configuration']
st.altair_chart(tradeoff_chart(frame, series='configuration', domain=list(frame['configuration']),
                              threshold=accuracy, emphasize=[ARM_LABELS[selected]] if selected else None,
                              y_floor=.95, label_field='label', height=220), width='stretch')

volume = st.number_input('Monthly extraction volume', 1000, 100_000_000, 1_000_000, 100_000)
if selected:
    m, fixed = metrics[selected], metrics['fixed_grounded_super']
    cols = st.columns(3)
    cols[0].metric('Projected selected inference cost / month', money(m['cost_per_1k_tasks'] * volume / 1000, 2))
    cols[1].metric('Grounded Super inference cost / month', money(fixed['cost_per_1k_tasks'] * volume / 1000, 2))
    cols[2].metric('Projected inference saving / month', money(
        (fixed['cost_per_1k_tasks'] - m['cost_per_1k_tasks']) * volume / 1000, 2))
    st.caption('Projection at recorded token lengths and prices; excludes hosting, labeling, development and human review. '
               'The Super reference may not qualify under edited requirements.')
st.caption('The adopted route had 98.4% correct autonomous completions and zero observed wrong releases on this '
           'synthetic representative set. Its 4-second deadline gate passed narrowly. These are not production guarantees.')

with st.expander('What does the model add beyond rules?'):
    diagnostics = [r for r in artifacts.list_runs('rulesdiagnostic') if (r.path / 'summary.json').exists()]
    if diagnostics:
        diagnostic = artifacts.load_json(diagnostics[0].path / 'summary.json')
        rep = diagnostic['metrics']['representative']
        st.dataframe([{'configuration': label, 'correct without review': pct(rep[name]['cac_rate']),
                       'review': pct(rep[name]['review_rate']), 'wrong releases': rep[name]['undetected_errors']}
                      for name, label in [('rules_only', 'Rules only'), ('deterministic', 'Adopted profile')]],
                     hide_index=True, width='stretch')
        st.caption(f"RETROSPECTIVE DIAGNOSTIC · {diagnostic['run']} · 312 previously exposed synthetic conversations. "
                   'The model arm is a historical recording. This simple rules baseline is not the best possible '
                   'parser; the comparison does not establish independent real-world performance.')
    st.markdown('[Rules-only results and reproducible diagnostic](https://github.com/CompilingCaffeine/RouteWise/blob/main/results/rules-only-report.md)')

decision = deployment_decision(evidence, metrics, selected, accuracy=accuracy, field_accuracy=field, deadline_ms=deadline)
st.download_button('Download deployment decision', json.dumps(decision, indent=2),
                   'routewise-deployment-decision.json', 'application/json')
if decision['cli']:
    with st.expander('Use this configuration in your application'):
        st.code(decision['cli'], language='bash', wrap_lines=True)
        st.markdown('Use the JSON trace only when `final_valid` is true. Otherwise send the request to a person. '
                    'The downloaded decision includes requirements, profile, source hashes, price snapshot and evidence scope.')
        st.markdown('[Setup and integration guide](https://github.com/CompilingCaffeine/RouteWise/blob/main/docs/deployment.md)')

st.subheader('See how the selected profile handles a request')
render_recorded(evidence, key='start-recorded')

st.subheader('3 · Try the measured configuration live')
if selected:
    render_live(evidence, key='start-live', choice=selected)
else:
    st.info('Adjust the requirements to explore a qualifying option, or inspect the recorded examples above.')

with st.expander('Evidence, limitations and independent validation'):
    st.caption(f"Source confirmation: {evidence['policy']['validation_run']}. "
               'The original experiments remain available in Evidence archive.')
    st.page_link('views/overview.py', label='Open the complete evidence archive')
    st.page_link('views/workload.py', label='Explore the earlier matched Nano / escalation / hedge comparison')
    st.markdown('[Independent pilot handoff](https://github.com/CompilingCaffeine/RouteWise/blob/main/docs/validation-pilot-handoff.md) '
                '· [Private validation guide](https://github.com/CompilingCaffeine/RouteWise/blob/main/docs/workload-validation.md)')
    st.markdown('Independent human validation is complete for the 312-conversation representative dataset, '
                'as confirmed by the project owner. The conversations remain synthetic. A separate pilot on '
                'fresh customer conversations and measurements across more serving days are next. '
                'The rules-only diagnostic remains separate from the original confirmations.')
