"""M5 lifecycle: prepare, build, tune, evaluate, report. Run with python -m routewise.confirmation."""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from random import Random
from typing import Any

from routewise.benchmark import Pacer, run_benchmark
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT, load_config, load_dotenv
from routewise.cost import call_cost_usd
from routewise.evaluation import trace_outcome
from routewise.factsheets import FactSheet
from routewise.fresh_workload import SEED, fresh_sheets, save_plan, split_name
from routewise.metrics import summarize
from routewise.policy import RouteOption, simulate
from routewise.prices import fetch_snapshot, load_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.router import Router, Trace
from routewise.runlog import RunDir, read_jsonl, sha256_file
from routewise.schema import CallRecord, Example
from routewise.statistics import cheapest_qualifying, cluster_ci, wilson
from routewise.synth import adjudication_queue, write_all

DATA = REPO_ROOT / 'data/v3'
PROTOCOL = REPO_ROOT / 'experiments/M5-grounded-inference.md'
ACTIVE_STUDY = 'm5'
GROUNDED_VERSION = 'v3'
OPTIONS = {
    'fixed_nano': ('v2', RouteOption('nano')),
    'fixed_super': ('v2', RouteOption('super')),
    'fixed_ultra': ('v2', RouteOption('ultra')),
    'fixed_grounded_nano': ('v3', RouteOption('nano')),
    'fixed_grounded_super': ('v3', RouteOption('super')),
    'current': ('v2', RouteOption('nano', 'super')),
    'grounding_only': ('v3', RouteOption('nano', 'super')),
    'hedging_only': ('v2', RouteOption('nano', 'super', 1337)),
    'grounded_hedged': ('v3', RouteOption('nano', 'super', 1337)),
}
TARGET = {'task_accuracy': 0.95, 'field_accuracy': 0.95, 'deadline_ms': 4000}


def configure(study: str) -> None:
    global DATA, PROTOCOL, ACTIVE_STUDY, GROUNDED_VERSION, SEED, fresh_sheets, split_name, save_plan, OPTIONS
    if study == 'm6':
        from routewise import issue_workload
        DATA = REPO_ROOT/'data/v4'
        PROTOCOL = REPO_ROOT/'experiments/M6-issue-date-confirmation.md'
        ACTIVE_STUDY, GROUNDED_VERSION, SEED = 'm6', 'v4', issue_workload.SEED
        fresh_sheets, split_name, save_plan = issue_workload.fresh_sheets, issue_workload.split_name, issue_workload.save_plan
        OPTIONS = {k: ('v4' if v == 'v3' else v, option) for k, (v, option) in OPTIONS.items()}


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + '\n')


def source_hashes() -> dict[str, str]:
    files = [*sorted((REPO_ROOT/'src/routewise').glob('*.py')),
             REPO_ROOT/'prompts/extraction_v2.txt', REPO_ROOT/'prompts/extraction_v3.txt', PROTOCOL,
             REPO_ROOT/'config/routewise.yaml']
    if ACTIVE_STUDY == 'm6':
        files.append(REPO_ROOT/'prompts/extraction_v4.txt')
    return {str(p.relative_to(REPO_ROOT)): sha256_file(p) for p in files}


class StudyClient:
    """Pace every model call including escalations; fail loudly on token/accounting issues."""
    def __init__(self, config, snapshot, budget: float = 10) -> None:
        self.client = TokenFactoryClient(base_url=config.base_url, max_attempts=2, timeout_s=45)
        self.pacers = {c.model_id: Pacer(90 if c.name == 'nano' else 240) for c in config.candidates}
        self.prices = {c.model_id: price_for(snapshot, c.model_id) for c in config.candidates}
        self.spent, self.budget = 0.0, budget
        self.lock = threading.Lock()

    def chat(self, **kwargs):
        with self.lock:
            if self.spent >= self.budget:
                raise RuntimeError('M5 API budget reached; completed calls remain on disk')
        self.pacers[kwargs['model']].wait()
        result = self.client.chat(**kwargs)
        if result.error and any(s in result.error.lower() for s in ('401', '403', 'authentication', 'invalid api')):
            raise RuntimeError('Nebius authentication failed; check the API token')
        if result.prompt_tokens is None or result.completion_tokens is None:
            if not result.error:
                raise RuntimeError('Nebius omitted usage; refusing an unpriced result')
        else:
            cost = call_cost_usd(self.prices[kwargs['model']], result.prompt_tokens, result.completion_tokens,
                                 result.cached_tokens or 0)
            with self.lock:
                self.spent += cost
        return result


def context(snapshot_path: Path):
    config = load_config()
    load_dotenv(REPO_ROOT/'.env')
    snapshot = load_snapshot(snapshot_path)
    client = StudyClient(config, snapshot)
    prices = {c.label: price_for(snapshot, c.model_id) for c in config.candidates}
    prompts = {v: load_prompt(REPO_ROOT/f'prompts/extraction_{v}.txt', 'extraction') for v in ('v2', GROUNDED_VERSION)}
    return config, snapshot, client, prices, prompts


def prepare() -> None:
    if (DATA/'source.json').exists() or (DATA/'seal.json').exists():
        raise ValueError('v3 is already prepared; use the recorded artifacts instead of replacing a workload')
    save_plan(DATA)
    config = load_config()
    load_dotenv(REPO_ROOT/'.env')
    sheets = fresh_sheets()
    run = RunDir.create('fresh6' if ACTIVE_STUDY == 'm6' else 'fresh')
    fetch_snapshot(TokenFactoryClient(base_url=config.base_url), config.base_url, run.path/'prices.json')
    config, snapshot, client, prices, prompts = context(run.path/'prices.json')
    run.write_manifest(study=ACTIVE_STUDY, protocol_sha256=sha256_file(PROTOCOL), sources=source_hashes(), seed=SEED,
                       factsheets_sha256=sha256_file(DATA/'factsheets.jsonl'), planned_n=len(sheets),
                       price_sha256=sha256_file(run.path/'prices.json'), purpose='v3 synthesis and label verification')
    print(f'Synthesis run: {run.path}', flush=True)
    records = write_all(client, config, sheets, prices, concurrency=8, rpm=360,
                        sink=lambda r: run.append('writers.jsonl', r))
    examples = [Example(**r['example']) for r in records if r['accepted']]
    dump(run.path/'generation_summary.json', {'accepted': len(examples), 'planned': len(sheets),
         'accepted_by_family': dict(Counter(e.tags[0] for e in examples)),
         'rejected': [{'id': r['id'], 'problems': r['attempts'][-1]['problems']} for r in records if not r['accepted']],
         'writer_cost_usd': client.spent})
    print(f'{len(examples)}/{len(sheets)} accepted; checking labels with reasoning Super and Ultra', flush=True)
    verifier_run = RunDir.create('freshverify6' if ACTIVE_STUDY == 'm6' else 'freshverify')
    verifier_run.write_manifest(study=ACTIVE_STUDY, source_run=run.experiment_id, source_hashes=source_hashes(), n_examples=len(examples))
    candidates = [(config.with_reasoning(config.candidate(name), 'on'), prices[name]) for name in ('super', 'ultra')]
    verification = run_benchmark(client, config, examples, candidates, {'extraction': prompts['v2']}, verifier_run,
                                 concurrency=8, rpm=360)
    queue = adjudication_queue(examples, verification)
    dump(run.path/'adjudication_queue.json', queue)
    # Preserve all disagreements for a broader blinded label audit before Nano inference.
    by_id = {e.id: e for e in examples}
    any_wrong = defaultdict(dict)
    for r in verification:
        if not r.score['task_correct']:
            any_wrong[r.example_id][r.candidate] = {f: (r.parsed or {}).get(f) for f, ok in r.score['fields'].items() if not ok}
    dump(run.path/'all_disagreements.json', [{'id': eid, 'input': by_id[eid].input,
         'label': by_id[eid].expected, 'disagreements': answers} for eid, answers in any_wrong.items()])
    dump(run.path/'verification.json', {'run': verifier_run.experiment_id, 'queued': len(queue),
         'any_disagreement': sum(not r.score['task_correct'] for r in verification),
         'total_cost_usd': client.spent})
    dump(DATA/'source.json', {'study': ACTIVE_STUDY, 'run': run.experiment_id, 'verifier_run': verifier_run.experiment_id})
    print(f'Queue: {len(queue)}. Review {run.path}/adjudication_queue.json, then build.', flush=True)


def build() -> None:
    if (DATA/'seal.json').exists():
        raise ValueError('Cannot replace a sealed dataset')
    source = json.loads((DATA/'source.json').read_text())
    run_path = REPO_ROOT/'runs'/source['run']
    queue = json.loads((run_path/'adjudication_queue.json').read_text())
    decisions_path = DATA/'adjudication.jsonl'
    decisions = {d['id']: d for d in read_jsonl(decisions_path)} if decisions_path.exists() else {}
    missing = [q['id'] for q in queue if q['id'] not in decisions]
    if missing:
        raise ValueError(f'Unresolved blinded label adjudication: {missing}')
    if any(d.get('decision') not in ('keep', 'drop') or not d.get('reason') for d in decisions.values()):
        raise ValueError('Each adjudication needs a keep/drop decision and reason')
    drop = {eid for eid, d in decisions.items() if d['decision'] == 'drop'}
    recs = [r for r in read_jsonl(run_path/'writers.jsonl') if r['accepted'] and r['id'] not in drop]
    recs.sort(key=lambda r: r['id'])
    (DATA/'examples.jsonl').write_text(''.join(json.dumps(r['example'], ensure_ascii=False)+'\n' for r in recs))
    sheets = {s.id: s for s in fresh_sheets()}
    dump(DATA/'splits.json', {name: [r['id'] for r in recs if split_name(sheets[r['id']]) == name]
                             for name in ('tune', 'heldout')})
    dump(DATA/'MANIFEST.json', {**source, 'n_examples': len(recs), 'dropped_by_adjudication': len(drop),
         'protocol_sha256': sha256_file(PROTOCOL), 'dataset_sha256': sha256_file(DATA/'examples.jsonl'),
         'split_sha256': sha256_file(DATA/'splits.json'), 'source': 'synthetic fact sheets; model-written prose'})
    if not decisions_path.exists():
        decisions_path.write_text('')
    print(f'Built {len(recs)} examples: {DATA}', flush=True)


def examples_for(split: str) -> list[Example]:
    ids = set(json.loads((DATA/'splits.json').read_text())[split])
    return [Example(**r) for r in read_jsonl(DATA/'examples.jsonl') if r['id'] in ids]


def deployment_metrics(outcomes, deadline=4000):
    m = summarize(outcomes)
    n = len(outcomes)
    m['accuracy_lower95'] = wilson(sum(o.correct for o in outcomes), n, one_sided=True)[0]
    misses = sum(o.latency_ms is None or o.latency_ms > deadline for o in outcomes)
    m['deadline_misses'] = misses
    m['deadline_miss_rate'] = misses / n
    m['deadline_miss_upper95'] = wilson(misses, n, one_sided=True)[1]
    m['deadline_ms'] = deadline
    return m


def tune() -> None:
    if (DATA/'seal.json').exists():
        raise ValueError('Tuning is already sealed; a new revision needs a new protocol and fresh test data')
    meta = json.loads((DATA/'MANIFEST.json').read_text())
    snapshot_path = REPO_ROOT/'runs'/meta['run']/'prices.json'
    config, snapshot, client, prices, prompts = context(snapshot_path)
    examples = examples_for('tune')
    run = RunDir.create('confirmtune')
    run.write_manifest(study=ACTIVE_STUDY, dataset=DATA.name, split='tune', n_examples=len(examples), sources=source_hashes(),
                       protocol_sha256=sha256_file(PROTOCOL), data_sha256=sha256_file(DATA/'examples.jsonl'))
    print(f'Tuning run: {run.path}', flush=True)
    for version in ('v2', GROUNDED_VERSION):
        candidates = [(c, prices[c.label]) for c in config.candidates if version == 'v2' or c.name != 'ultra']
        # Each version gets a distinct log; candidate labels remain real model names.
        class Log:
            def append(self, name, record):
                run.append(f'{version}-'+name, record)
        run_benchmark(client, config, examples, candidates, {'extraction': prompts[version]}, Log(), concurrency=8, rpm=300)
    indexes = {v: {(r.candidate, r.example_id): r for x in read_jsonl(run.path/f'{v}-calls.jsonl')
                   if (r := CallRecord(**x))} for v in ('v2', GROUNDED_VERSION)}
    metrics = {name: deployment_metrics(simulate(opt, examples, indexes[v])) for name, (v, opt) in OPTIONS.items()}
    fixed = {k: v for k, v in metrics.items() if k.startswith('fixed_')}
    comparator = cheapest_qualifying(fixed, accuracy=.95, deadline_ms=4000)
    seal = {'study': ACTIVE_STUDY, 'tune_run': run.experiment_id, 'created_before_heldout': True, 'sources': source_hashes(),
            'data_sha256': sha256_file(DATA/'examples.jsonl'), 'split_sha256': sha256_file(DATA/'splits.json'),
            'price_sha256': sha256_file(snapshot_path), 'price_path': str(snapshot_path.relative_to(REPO_ROOT)),
            'comparator': comparator, 'requirements': TARGET, 'hedge_after_ms': 1337,
            'tuning_metrics': metrics, 'selected_on_tune': cheapest_qualifying(metrics, accuracy=.95, deadline_ms=4000)}
    dump(run.path/'summary.json', metrics)
    dump(DATA/'seal.json', seal)
    print(f'Frozen comparator: {comparator}; lowest-cost qualifying option: {seal["selected_on_tune"]}', flush=True)


def evaluate(run_path: Path | None = None, gap_s: float = 60) -> Path:
    seal = json.loads((DATA/'seal.json').read_text())
    if seal['sources'] != source_hashes() or seal['data_sha256'] != sha256_file(DATA/'examples.jsonl'):
        raise ValueError('Frozen source/data changed after tune. Do not run heldout with an unregistered revision.')
    if seal['split_sha256'] != sha256_file(DATA/'splits.json') or seal['price_sha256'] != sha256_file(REPO_ROOT/seal['price_path']):
        raise ValueError('Frozen split or prices changed after tune')
    config, snapshot, client, prices, prompts = context(REPO_ROOT/seal['price_path'])
    examples = examples_for('heldout')
    Random(SEED).shuffle(examples)
    blocks = [examples[i::3] for i in range(3)]
    sentinels = blocks[0][:24]
    for i in (1, 2):
        blocks[i] = blocks[i] + sentinels
        Random(SEED+i).shuffle(blocks[i])
    if run_path:
        run = RunDir.existing(run_path)
        if (run.path/'status.json').exists():
            raise ValueError('This run was aborted; it cannot be resumed or represented as confirmation')
        old = json.loads((run.path/'manifest.json').read_text())
        if old['seal_sha256'] != sha256_file(DATA/'seal.json'):
            raise ValueError('Resume seal mismatch')
    else:
        for p in sorted((REPO_ROOT/'runs').glob('confirmation-*/manifest.json')):
            if json.loads(p.read_text()).get('seal_sha256') == sha256_file(DATA/'seal.json'):
                raise ValueError(f'A run for this seal already exists; resume with --run {p.parent}')
        run = RunDir.create('confirmation')
        run.write_manifest(study=ACTIVE_STUDY, dataset=DATA.name, split='heldout', seal=seal, seal_sha256=sha256_file(DATA/'seal.json'),
                           n_unique=len(examples), n_sentinels=len(sentinels), arms=list(OPTIONS),
                           n_expected_observations=sum(len(b) for b in blocks)*len(OPTIONS),
                           price_path=seal['price_path'], block_gap_seconds=gap_s, randomized_seed=SEED)
    print(f'Confirmation run: {run.path}', flush=True)
    routers = {name: Router(client, config, prices, {'extraction': prompts[v]}, {'extraction': opt}, strategy=name)
               for name, (v, opt) in OPTIONS.items()}
    existing = read_jsonl(run.path/'observations.jsonl') if (run.path/'observations.jsonl').exists() else []
    done = {(r['window']-1, r['trace']['example_id'], r['trace']['strategy']) for r in existing}
    # Load-generator admission is outside the request timer. Otherwise a conservative
    # experiment RPM budget would masquerade as model serving latency.
    nano_id = config.candidate('nano').model_id
    client.pacers[nano_id] = Pacer(0)
    nano_admission = Pacer(90)
    lock = threading.Lock()
    for block, exs in enumerate(blocks):
        jobs = []
        for ex in exs:
            names = list(OPTIONS)
            Random(f'{SEED}:{block}:{ex.id}').shuffle(names)
            for name in names:
                if (block, ex.id, name) not in done:
                    jobs.append((ex, name))
        if not jobs:
            continue
        if block:
            print(f'Window {block+1}: waiting {gap_s:g}s before the next block', flush=True)
            time.sleep(gap_s)
        started = time.time()
        print(f'Window {block+1}: {len(jobs)} randomized requests', flush=True)

        def job(ex, name):
            delivered = []
            start = time.perf_counter()
            def receive(answer):
                delivered.append((time.perf_counter()-start)*1000)
            trace = routers[name].route('extraction', ex.input, example_id=ex.id, expected=ex.expected, on_answer=receive)
            # The primary latency is the externally observed callback, including local pacing/backoff.
            trace.latency_ms = delivered[0]
            record = {'window': block+1, 'sentinel': ex in sentinels, 'family': ex.tags[0],
                      'wall_duration_ms': (time.perf_counter()-start)*1000, 'trace': trace.model_dump()}
            with lock:
                run.append('observations.jsonl', record)
                done.add((block, ex.id, name))
                if len(done) % 30 == 0:
                    print(f'  {len(done)} completed requests; stage API spend ${client.spent:.3f}', flush=True)
            return record

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for ex, name in jobs:
                if OPTIONS[name][1].primary == 'nano':
                    nano_admission.wait()
                futures.append(pool.submit(job, ex, name))
            for future in as_completed(futures):
                future.result()
        run.append('windows.jsonl', {'window': block+1, 'started_unix': started, 'ended_unix': time.time(), 'n_jobs': len(jobs)})
        print(f'Window {block+1} complete', flush=True)
    report(run.path)
    return run.path


def report(run_path: Path) -> dict:
    manifest = json.loads((run_path/'manifest.json').read_text())
    observations = read_jsonl(run_path/'observations.jsonl')
    observations.sort(key=lambda r: (r['window'], r['trace']['example_id'], r['trace']['strategy']))
    primary = {}
    for row in observations:
        primary.setdefault((row['trace']['example_id'], row['trace']['strategy']), row)
    by_arm = defaultdict(list)
    for row in primary.values():
        by_arm[row['trace']['strategy']].append(row)
    metrics, family_rows, window_rows = {}, [], []
    expected = manifest['n_unique']
    complete = (all(len(by_arm[n]) == expected for n in OPTIONS)
                and len(observations) == manifest['n_expected_observations'])
    for name, rows in by_arm.items():
        outs = [trace_outcome(Trace(**r['trace'])) for r in rows]
        m = deployment_metrics(outs)
        m['accuracy_family_cluster_ci95'] = cluster_ci([float(o.correct) for o in outs], [r['family'] for r in rows])
        m['wall_p95_ms'] = summarize([o.__class__(**{**vars(o), 'latency_ms': r['wall_duration_ms']})
                                      for o, r in zip(outs, rows)])['p95_latency_ms']
        m['wrong_fields'] = dict(Counter(f for r in rows for f, ok in r['trace']['score']['fields'].items() if not ok))
        metrics[name] = m
        for family in sorted({r['family'] for r in rows}):
            sub = [trace_outcome(Trace(**r['trace'])) for r in rows if r['family'] == family]
            family_rows.append({'strategy': name, 'family': family, **deployment_metrics(sub)})
        for window in (1, 2, 3):
            sub = [trace_outcome(Trace(**r['trace'])) for r in observations if r['window'] == window and r['trace']['strategy'] == name]
            if sub:
                window_rows.append({'strategy': name, 'window': window, **deployment_metrics(sub)})
    paired = {}
    comparator = manifest['seal']['comparator']
    for other in ['current', 'grounding_only', 'hedging_only', comparator]:
        if not other or other not in by_arm or 'grounded_hedged' not in by_arm:
            continue
        a = {r['trace']['example_id']: r for r in by_arm['grounded_hedged']}
        b = {r['trace']['example_id']: r for r in by_arm[other]}
        ids = sorted(a.keys() & b.keys())
        acc = [float(a[i]['trace']['score']['task_correct'])-float(b[i]['trace']['score']['task_correct']) for i in ids]
        costs = [(a[i]['trace']['cost_usd']-b[i]['trace']['cost_usd'])*1000 for i in ids]
        paired[other] = {'n_pairs': len(ids), 'accuracy_diff': sum(acc)/len(acc),
                         'accuracy_diff_ci95': cluster_ci(acc, ids),
                         'cost_diff_per_1k': sum(costs)/len(costs), 'cost_diff_per_1k_ci95': cluster_ci(costs, ids)}
    choice = cheapest_qualifying(metrics, accuracy=.95, deadline_ms=4000) if complete else None
    fixed_choice = cheapest_qualifying({k:v for k,v in metrics.items() if k.startswith('fixed_')}, accuracy=.95, deadline_ms=4000) if complete else None
    intervention = metrics.get('grounded_hedged')
    qualifies = bool(intervention and cheapest_qualifying({'grounded_hedged': intervention}, accuracy=.95, deadline_ms=4000))
    cost_ok = bool(comparator and comparator in paired and paired[comparator]['cost_diff_per_1k_ci95'][1] < 0)
    repeat_groups = defaultdict(list)
    for r in observations:
        if r['sentinel']:
            repeat_groups[r['trace']['strategy']].append(r)
    repeats = {}
    for name, rows in repeat_groups.items():
        values = [float(r['trace']['score']['task_correct']) for r in rows]
        repeats[name] = {'n_outcomes': len(rows), 'n_conversations': len({r['trace']['example_id'] for r in rows}),
                         'task_accuracy': sum(values)/len(values),
                         'accuracy_cluster_ci95': cluster_ci(values, [r['trace']['example_id'] for r in rows])}
    result = {'study': manifest.get('study', 'm5'), 'dataset': manifest.get('dataset', 'v3'),
              'complete': complete, 'n_unique': expected, 'n_observations': len(observations), 'requirements': TARGET,
              'metrics': metrics, 'families': family_rows, 'windows': window_rows, 'paired': paired,
              'sentinels': repeats, 'frozen_fixed_comparator': comparator, 'cheapest_qualifying': choice,
              'cheapest_qualifying_fixed': fixed_choice, 'intervention_qualifies': qualifies,
              'hypothesis_supported': complete and qualifies and cost_ok,
              'total_api_cost_usd': sum(r['trace']['cost_usd'] for r in observations),
              'incomplete_cost_requests': sum(not r['trace']['cost_complete'] for r in observations)}
    dump(run_path/'summary.json', result)
    print(json.dumps({k: result[k] for k in ('complete','cheapest_qualifying','frozen_fixed_comparator','hypothesis_supported','total_api_cost_usd')}, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('prepare', 'build', 'tune', 'evaluate', 'report'))
    parser.add_argument('--run', type=Path)
    parser.add_argument('--study', choices=('m5','m6'), default='m5')
    args = parser.parse_args()
    configure(args.study)
    if args.stage == 'prepare': prepare()
    elif args.stage == 'build': build()
    elif args.stage == 'tune': tune()
    elif args.stage == 'evaluate': evaluate(args.run)
    else:
        if not args.run: parser.error('report requires --run')
        report(args.run)


if __name__ == '__main__':
    main()
