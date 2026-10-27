"""Measured deployment choices and a bring-your-own workload workflow."""
import hashlib
import json

import pandas as pd
import streamlit as st

from routewise import artifacts
from routewise.benchmark import run_benchmark
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config
from routewise.confirmation import OPTIONS, deployment_metrics
from routewise.evaluation import run_evaluation, trace_outcome
from routewise.policy import RouteOption
from routewise.prices import price_for
from routewise.prompts import load_prompt
from routewise.router import Router
from routewise.statistics import cheapest_qualifying, wilson
from routewise.workload import measured_options, parse_workload
from ui import (CONFIGURATION_LABELS, api_key, money, ms, pct, render_trace,
                tradeoff_chart, useful_comparison_rows)

st.title('Workload optimizer')
st.info('This page explores the earlier M5 study, without the adopted date-normalization profile. '
        'Start here and Live router use the adopted profile; the original comparisons are retained below.')
st.page_link('views/start.py', label='Explore the adopted profile and its current deployment recipe')
st.markdown('Choose requirements, compare fixed models with routing, and inspect the evidence. '
            'RouteWise recommends **no configuration** when the measurements cannot establish that your requirements are met.')
explore, upload = st.tabs(['Explore measured configurations', 'Use your labelled workload'])
LABELS = CONFIGURATION_LABELS


def show_metrics(metrics, selected):
    rows = []
    for name, m in sorted(metrics.items(), key=lambda x: x[1]['cost_per_1k_tasks']):
        rows.append({'choice': '✓' if name == selected else '', 'configuration': LABELS.get(name,name),
                     '$ / 1k': money(m['cost_per_1k_tasks']), 'accuracy': pct(m['task_accuracy']),
                     'lower 95%': pct(m['accuracy_lower95']), 'p95': ms(m['p95_latency_ms']),
                     'field accuracy': pct(m['field_accuracy']), 'miss upper 95%': pct(m['deadline_miss_upper95']),
                     'deadline misses': pct(m['deadline_miss_rate'])})
    if selected:
        st.success(f'Lowest-cost qualifying configuration: {LABELS.get(selected,selected)}')
    else:
        st.warning('No configuration clears every requirement with the declared confidence bounds.')
    st.dataframe(rows, hide_index=True, width='stretch')
    st.caption('Eligibility uses a one-sided 95% accuracy lower bound and a one-sided 95% upper bound of 5% '
               'on the deadline-miss rate. Field accuracy uses its point estimate.')


with explore:
    runs = [r for r in artifacts.list_runs('confirmation') if r.manifest.get('study') == 'm6' and (r.path/'summary.json').exists()
            and artifacts.load_json(r.path/'summary.json').get('complete')]
    if not runs:
        st.info('No completed M5 confirmation study is available yet.')
    else:
        run = st.selectbox('Confirmation run', runs, format_func=lambda r: r.id) if len(runs)>1 else runs[0]
        result = artifacts.load_json(run.path/'summary.json')
        st.caption(f"{result['n_unique']} unique fresh conversations · synthetic data · three time windows. "
                   'Edits below explore this recorded test set; they do not constitute a new confirmation experiment.')
        cols = st.columns(3)
        accuracy = cols[0].slider('Required task accuracy (%)', 80.0, 100.0, 95.0, .5, format='%.1f')/100
        field = cols[1].slider('Required field accuracy (%)', 90.0, 100.0, 95.0, .5, format='%.1f')/100
        deadline = cols[2].slider('p95 deadline (ms)', 1000, 8000, 4000, 250)
        records = artifacts.load_records(run, 'observations.jsonl')
        first = {}
        for r in sorted(records, key=lambda r: r['window']):
            t = r['trace']
            first.setdefault((t['example_id'], t['strategy']), t)
        metrics = {k: dict(v) for k, v in result['metrics'].items()}
        for name, m in metrics.items():
            traces = [t for (_, arm), t in first.items() if arm == name]
            misses = sum(t['latency_ms'] > deadline for t in traces)
            m.update(deadline_misses=misses, deadline_miss_rate=misses/len(traces),
                     deadline_miss_upper95=wilson(misses, len(traces), one_sided=True)[1], deadline_ms=deadline)
        selected = cheapest_qualifying(metrics, accuracy=accuracy, field_accuracy=field, deadline_ms=deadline)
        if selected:
            st.success(f'Lowest-cost qualifying configuration: {LABELS.get(selected,selected)}')
        else:
            st.warning('No configuration clears every requirement with the declared confidence bounds.')
        st.dataframe(useful_comparison_rows(metrics, selected, accuracy=accuracy,
                     field_accuracy=field, deadline_ms=deadline), hide_index=True, width='stretch')
        st.caption('Four matched grounded configurations isolate the value of escalation, hedging and a fixed stronger model. '
                   'Accuracy includes its one-sided 95% lower bound; qualification also checks field accuracy and deadline misses.')
        if selected:
            low, high = metrics[selected]['accuracy_family_cluster_ci95']
            if low < accuracy:
                st.warning(f'Family-mix sensitivity interval: {pct(low)}–{pct(high)}. Its lower end is below your '
                           'target. The result depends on the observed family mix; validate your own workload before deployment.')
            weak = [r for r in result['families'] if r['strategy'] == selected and r['task_accuracy'] < accuracy]
            for family in sorted(weak, key=lambda r:r['task_accuracy'])[:2]:
                st.caption(f"Observed weak family: {family['family'].replace('_',' ')} — "
                           f"{pct(family['task_accuracy'])} task accuracy on {family['n']} conversations.")
        fixed = cheapest_qualifying({k:v for k,v in metrics.items() if k.startswith('fixed_')},
                                   accuracy=accuracy, field_accuracy=field, deadline_ms=deadline)
        if selected and fixed:
            volume = st.number_input('Monthly extraction volume', 1000, 100_000_000, 1_000_000, 100_000)
            monthly = metrics[selected]['cost_per_1k_tasks']*volume/1000
            baseline = metrics[fixed]['cost_per_1k_tasks']*volume/1000
            cols = st.columns(3)
            cols[0].metric('Selected configuration / month', money(monthly,2))
            cols[1].metric('Cheapest qualifying fixed model / month', money(baseline,2))
            cols[2].metric('Measured cost saving / month', money(baseline-monthly,2))
            st.caption('Projection uses the observed workload token lengths and recorded prices; infrastructure and development costs are excluded.')
            if selected == fixed:
                st.info('A fixed model is the cheapest qualifying choice here. Routing adds no demonstrated value at these requirements.')
        with st.expander('All nine configurations and the cost–quality plot'):
            st.caption(f"Run `{run.id}`. Original-prompt controls measure the combined effect of the clarified prompt, "
                       'source constraints and metadata-derived fields; the four matched controls share the same treatment.')
            show_metrics(metrics, selected)
            frame = pd.DataFrame([{'configuration': k, **v} for k, v in metrics.items()])
            labels = {selected, 'fixed_super', 'fixed_ultra', 'fixed_grounded_super'}
            frame['label'] = frame['configuration'].map(lambda n: LABELS[n] if n in labels else '')
            st.altair_chart(tradeoff_chart(frame, series='configuration', domain=list(metrics), threshold=accuracy,
                                          emphasize=[selected] if selected else ['grounded_hedged'], y_floor=.8,
                                          label_field='label'), width='stretch')
        st.markdown('Confidence bounds describe the measured synthetic workload. Real customer data and measurements '
                    'on additional days are needed before making a production reliability claim.')
        st.download_button('Download full study results', json.dumps(result, indent=2), 'routewise-m5-confirmation-results.json', 'application/json')
        with st.expander('Per-family, per-window and repeated-conversation evidence'):
            st.dataframe(result['families'], hide_index=True)
            st.dataframe(result['windows'], hide_index=True)
            st.json(result['sentinels'])
        st.subheader('Inspect an answer and its evidence')
        names = list(metrics)
        arm = st.selectbox('Configuration', names, index=names.index(selected) if selected else names.index('grounded_hedged'),
                           format_func=lambda n: LABELS.get(n,n))
        choices = [t for (_, name), t in first.items() if name == arm]
        chosen = st.selectbox('Recorded request', choices, format_func=lambda t: f"{t['example_id']} · {'correct' if t['score']['task_correct'] else 'incorrect'}")
        data_path = artifacts.artifacts_root()/'data'/run.manifest.get('dataset','v3')/'examples.jsonl'
        examples = {r['id']: r for r in (json.loads(l) for l in data_path.read_text().splitlines())} if data_path.exists() else {}
        ex = examples.get(chosen['example_id'])
        if ex:
            st.code(ex['input'], language=None, wrap_lines=True)
        render_trace(chosen, expected=ex['expected'] if ex else None)
        key = api_key()
        text = st.text_area('Try a new conversation with this configuration', max_chars=12000,
                            placeholder='[2026-04-01 09:00] Customer: …')
        used = st.session_state.get('live_routes', 0)
        if st.button('Run this M5 configuration live', disabled=not key or not text or used >= 15):
            cfg = load_config()
            version, option = OPTIONS[arm]
            if run.manifest.get('study') == 'm6' and version == 'v3':
                version = 'v4'
            snapshot = artifacts.load_prices()
            prices = {c.label: price_for(snapshot, c.model_id) for c in cfg.candidates}
            prompt = load_prompt(REPO_ROOT/f'prompts/extraction_{version}.txt', 'extraction')
            router = Router(TokenFactoryClient(api_key=key, base_url=cfg.base_url), cfg, prices,
                            {'extraction': prompt}, {'extraction': option}, strategy=arm)
            ready = st.empty()
            def display(answer):
                with ready.container():
                    st.caption(f"Answer ready in {answer['latency_ms']:,.0f} ms; finishing accounting…")
                    st.json(answer['output'])
            with st.spinner('Calling Nebius Token Factory…'):
                trace = router.route('extraction', text, on_answer=display)
            ready.empty()
            st.session_state['live_routes'] = used + 1
            render_trace(trace.model_dump())

with upload:
    st.markdown('Upload **JSONL** with `id`, `input`, `expected` (all six extraction fields), and optionally '
                '`split` (`tune` or `heldout`). Without split labels, a stable text hash assigns the halves. '
                'Exact duplicate conversations are rejected. Uploads and results are not saved to the repository. '
                'Benchmarking sends conversation text to Nebius Token Factory; labels stay in local scoring.')
    st.warning('This is a public demo. Do not upload customer conversations or labels here: validate them on '
               'your own machine with private validation mode '
               '([guide](https://github.com/CompilingCaffeine/RouteWise/blob/main/docs/workload-validation.md)).')
    sample = {'id': 'your-ticket-001', 'split': 'tune',
              'input': '[2026-04-01 09:00] Customer: Refund order web_12345.\n[2026-04-01 10:00] Agent: USD 25 will be refunded on 2026-04-03.',
              'expected': {'refund_requested': True, 'refund_amount': 25, 'currency': 'USD', 'order_id': 'web_12345',
                           'contact_date': '2026-04-01', 'refund_date': '2026-04-03'}}
    st.download_button('Download record format', json.dumps(sample)+'\n', 'routewise-format.jsonl', 'application/jsonl')
    uploaded = st.file_uploader('Labelled extraction workload', type=['jsonl'])
    own_key = st.text_input('Your Nebius API key (optional)', type='password',
                            help='Use your own key for larger workloads. It is used only for API calls and is never written to run artifacts.')
    if uploaded:
        raw = uploaded.getvalue()
        try:
            examples, splits = parse_workload(raw.decode('utf-8'))
        except (ValueError, KeyError, TypeError) as exc:
            st.error(f'Cannot import workload: {exc}')
        else:
            tune_examples = [e for e in examples if e.id in set(splits['tune'])]
            heldout = [e for e in examples if e.id in set(splits['heldout'])]
            st.caption(f'{len(tune_examples)} tuning conversations · {len(heldout)} held-out conversations. '
                       'Ground truth is used only for scoring after inference.')
            cols = st.columns(3)
            acc = cols[0].number_input('Task accuracy target (%)', 50., 100., 95., .5)/100
            fld = cols[1].number_input('Field accuracy target (%)', 50., 100., 95., .5)/100
            deadline = cols[2].number_input('Latency deadline (ms)', 500, 15000, 4000, 250)
            identity = hashlib.sha256(raw+json.dumps([acc, fld, deadline]).encode()).hexdigest()
            state_key = 'workload-'+identity
            state = st.session_state.setdefault(state_key, {})
            cfg = load_config()
            snapshot = artifacts.load_prices()
            key = own_key or api_key()
            budget = 2000 if own_key else 500
            used = st.session_state.get('workload_calls', 0)
            need = len(tune_examples)*3
            total_need = need + len(heldout)*5
            tested = hashlib.sha256(raw).hexdigest() in st.session_state.get('tested_workloads', set())
            if used+total_need > budget and 'plan' not in state:
                st.info('This workload exceeds the session call budget. Use fewer records or supply your own key.')
            if tested and 'evaluation' not in state:
                st.info('This workload has already been tested in this session. Further tuning needs fresh held-out conversations.')
            if st.button(f'Benchmark tuning split · {need} model calls',
                         disabled=not key or used+total_need > budget or tested):
                st.session_state['workload_calls'] = used+need
                records = []
                class MemoryLog:
                    def append(self, name, record): records.append(record)
                prices = {c.label: price_for(snapshot, c.model_id) for c in cfg.candidates}
                prompt = load_prompt(REPO_ROOT/'prompts/extraction_v4.txt', 'extraction')
                with st.spinner('Benchmarking Nano, Super and Ultra on the tuning split…'):
                    run_benchmark(TokenFactoryClient(api_key=key, base_url=cfg.base_url), cfg, tune_examples,
                                  [(c, prices[c.label]) for c in cfg.candidates], {'extraction': prompt}, MemoryLog(),
                                  concurrency=4, rpm=90, progress=lambda _: None)
                state['plan'] = measured_options(tune_examples, records, accuracy=acc, field_accuracy=fld, deadline_ms=deadline)
                state['records'] = [r.model_dump() for r in records]
                state.pop('evaluation', None)
            if state.get('plan'):
                plan = state['plan']
                st.caption('Tuning replay estimates policy latency from separate calls. A live held-out comparison is the next step.')
                show_metrics(plan['metrics'], plan['selected'])
                st.download_button('Download tuning calls and frozen choice', json.dumps(state, indent=2), 'routewise-tuning.json', 'application/json')
                selected = plan['selected']
                need = len(heldout)*5  # three fixed models + selected route, at most two calls
                used = st.session_state.get('workload_calls', 0)
                if selected and used+need > budget and 'evaluation' not in state:
                    st.info(f'The held-out comparison needs up to {need} calls; {budget-used} remain in this session.')
                if selected and st.button(f'Test frozen choice on held-out split · up to {need} calls',
                                          disabled=not key or used+need > budget or 'evaluation' in state or tested):
                    st.session_state['workload_calls'] = used+need
                    prices = {c.label: price_for(snapshot, c.model_id) for c in cfg.candidates}
                    prompt = load_prompt(REPO_ROOT/'prompts/extraction_v4.txt', 'extraction')
                    client = TokenFactoryClient(api_key=key, base_url=cfg.base_url)
                    options = {f'fixed_{c.label}': RouteOption(c.label) for c in cfg.candidates}
                    options['selected_route'] = RouteOption(**plan['options'][selected])
                    routers = {name: Router(client, cfg, prices, {'extraction': prompt}, {'extraction': opt}, strategy=name)
                               for name, opt in options.items()}
                    class MemoryLog:
                        def append(self, name, trace): pass
                    with st.spinner('Running the frozen configuration against every fixed model…'):
                        traces = run_evaluation(routers, heldout, MemoryLog(), concurrency=4, rpm=90, progress=lambda _: None)
                    metrics = {name: deployment_metrics([trace_outcome(t) for t in traces if t.strategy == name], deadline)
                               for name in routers}
                    state['evaluation'] = {'metrics': metrics, 'traces': [t.model_dump() for t in traces],
                                           'frozen_choice': selected, 'requirements': plan['requirements']}
                    st.session_state.setdefault('tested_workloads', set()).add(hashlib.sha256(raw).hexdigest())
                if state.get('evaluation'):
                    evaluation = state['evaluation']
                    eligible = cheapest_qualifying(evaluation['metrics'], accuracy=acc, field_accuracy=fld, deadline_ms=deadline)
                    st.subheader('Live held-out result')
                    show_metrics(evaluation['metrics'], eligible)
                    st.caption('The configuration was frozen before testing. Any further tuning requires fresh held-out data.')
                    st.download_button('Download held-out results and every trace', json.dumps(evaluation, indent=2),
                                       'routewise-heldout.json', 'application/json')
