"""A conservative full-task, no-model extraction baseline and retrospective diagnostic.

The extractor sees source text only. Labels and existing model traces are read only by the
offline scoring harness. Exposed synthetic data cannot establish fresh generalization.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from routewise.config import REPO_ROOT
from routewise.evaluate import score
from routewise.grounding import evidence
from routewise.issue_dates import analyze_issue_date
from routewise.recovery import contract_validation
from routewise.recovery_metrics import RequestOutcome, summarize
from routewise.runlog import RunDir, read_jsonl, sha256_file
from routewise.schema import parse_output
from routewise.textfacts import REFUND_CUE, analyze

VERSION = 'rules-only-v1'
_NEGATED = re.compile(r"\b(?:no|not|never|don't|do not|don't want|do not want)\b.{0,35}\brefund\b", re.I)
_POSITIVE = re.compile(
    r"\b(?:refund (?:me|my|the|order)|(?:want|like|need|request|seeking|please|asking).{0,45}"
    r"(?:refund|money back|reimburs)|(?:can|could) you.{0,25}refund|money back)", re.I)


@dataclass(frozen=True)
class RulesAnswer:
    output: dict
    released: bool
    review_reasons: tuple[str, ...]


def extract(text: str) -> RulesAnswer:
    """Fixed text rules, source candidates and the same date/validation contract as the adopted route."""
    facts, source = analyze(text), evidence(text)
    reasons = []
    customers = [m for m in facts.messages if m.speaker.lower() == 'customer']
    if not customers:
        reasons.append('no timestamped customer message')
    requested = False
    for msg in customers:
        if REFUND_CUE.search(msg.text):
            positive, negative = bool(_POSITIVE.search(msg.text)), bool(_NEGATED.search(msg.text))
            if positive and not negative:
                requested = True
            elif not negative:
                reasons.append('refund intent not resolved by the fixed rules')
    ids = source['order_ids']
    if len(ids) > 1:
        reasons.append('multiple order candidates')
    # Select only a unique numeric value explicitly in refund context. Purchase-only amounts stay null.
    amounts = {(m.amount, m.currency) for m in facts.money if m.refund_context}
    if len(amounts) > 1:
        reasons.append('multiple refund amount/currency candidates')
    amount, currency = next(iter(amounts)) if len(amounts) == 1 else (None, None)
    timing = analyze_issue_date(text)
    if timing.status == 'review':
        reasons.append(timing.reason)
    output = {'refund_requested': requested, 'refund_amount': amount, 'currency': currency,
              'order_id': ids[0] if len(ids) == 1 else None, 'contact_date': source['contact_date'],
              'refund_date': timing.date if timing.status == 'resolved' else None}
    check = contract_validation(text, parse_output('extraction', json.dumps(output)), timing)
    reasons.extend(check.failed_rules)
    return RulesAnswer(output, not reasons, tuple(dict.fromkeys(reasons)))


def run_diagnostic(root: Path = REPO_ROOT, output_root: Path | None = None) -> Path:
    """Score committed public v5 data; leave prior study artifacts and private validation untouched."""
    data = root / 'data/v5/confirm'
    source_run = root / 'runs/datesconfirm-20261026T133902Z-ab36f4'
    run = RunDir.create('rulesdiagnostic', root=output_root or root / 'runs')
    sources = [Path(__file__), root / 'src/routewise/grounding.py', root / 'src/routewise/textfacts.py',
               root / 'src/routewise/issue_dates.py', root / 'src/routewise/recovery.py',
               root / 'src/routewise/validate.py', root / 'src/routewise/schema.py',
               root / 'src/routewise/evaluate.py', root / 'src/routewise/recovery_metrics.py',
               root / 'src/routewise/statistics.py', root / 'src/routewise/metrics.py',
               data / 'examples.jsonl', data / 'splits.json', source_run / 'observations.jsonl']
    # Record implementation/input hashes before reading any scoring outcomes.
    run.write_manifest(kind='rulesdiagnostic', version=VERSION, evidence_role='retrospective diagnostic',
                       limitation='Previously exposed synthetic data; not a preregistered confirmation or independent validation.',
                       source_run=source_run.name,
                       source_sha256={str(p.relative_to(root)): sha256_file(p) for p in sources})
    examples = {e['id']: e for e in read_jsonl(data / 'examples.jsonl')}
    splits = json.loads((data / 'splits.json').read_text())
    recorded = read_jsonl(source_run / 'observations.jsonl')
    results = {}
    for split in ('challenge', 'representative'):
        outcomes = []
        recorded_outcomes = {name: [] for name in ('deterministic', 'control', 'fixed_grounded_super')}
        for row in recorded:
            if row['set'] != split:
                continue
            t = row['trace']
            ex = examples[t['example_id']]
            s = score('extraction', ex['expected'], t['final_output'])
            recorded_outcomes[t['strategy']].append(RequestOutcome(
                example_id=ex['id'], family=row['family'], review_required=row['review_required'],
                released=t['final_valid'], correct=s.task_correct, field_accuracy=s.field_accuracy,
                cost_usd=t['cost_usd'], latency_ms=t['latency_ms'], cost_complete=t['cost_complete']))
        metadata = {r['trace']['example_id']: r for r in recorded if r['set'] == split}
        for eid in splits[split]:
            ex, meta = examples[eid], metadata[eid]
            started = time.perf_counter()
            answer = extract(ex['input'])
            elapsed = (time.perf_counter() - started) * 1000
            s = score('extraction', ex['expected'], answer.output)
            outcomes.append(RequestOutcome(example_id=eid, family=meta['family'], review_required=meta['review_required'],
                                           released=answer.released, correct=s.task_correct, field_accuracy=s.field_accuracy,
                                           cost_usd=0., latency_ms=elapsed, cost_complete=True))
            run.append('outcomes.jsonl', {'set': split, 'example_id': eid, 'output': answer.output,
                                         'released': answer.released, 'review_reasons': answer.review_reasons,
                                         'score': s.to_dict(), 'local_cpu_ms': elapsed})
        if any(len(v) != len(outcomes) or {o.example_id for o in v} != set(splits[split])
               for v in recorded_outcomes.values()):
            raise ValueError('The comparison arms do not cover the same conversations exactly once')
        rules = summarize(outcomes)
        rules['local_cpu_p95_ms'] = rules.pop('p95_latency_ms')
        rules['p50_latency_ms'] = None
        rules['latency_comparison'] = 'Local rule CPU time is not comparable to recorded remote model latency.'
        results[split] = {'rules_only': rules, **{n: summarize(v) for n, v in recorded_outcomes.items()}}
    summary = {'schema_version': 'routewise.rules-diagnostic.v1', 'run': run.experiment_id,
               'evidence_role': 'retrospective diagnostic on previously exposed synthetic data',
               'version': VERSION, 'metrics': results, 'fresh_model_calls': 0,
               'limits': ['Not independent validation or a fresh confirmation.',
                          'Rules-only inference has zero API usage cost; hosting and human review are excluded.',
                          'Model arms are historical recordings, not simultaneous reruns.',
                          'Review and wrong releases use all requests as denominator; review is not a correct autonomous answer.']}
    (run.path / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return run.path
