"""Bring-your-own extraction workload: strict import and confidence-aware replay."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from routewise.confirmation import deployment_metrics
from routewise.policy import candidate_options, simulate
from routewise.schema import CallRecord, Example, ExtractionOutput
from routewise.statistics import cheapest_qualifying


def parse_workload(text: str) -> tuple[list[Example], dict[str, list[str]]]:
    examples, splits, seen_text = [], {'tune': [], 'heldout': []}, set()
    raw = [json.loads(line) for line in text.splitlines() if line.strip()]
    if len(raw) < 2 or len(raw) > 1000:
        raise ValueError('Provide 2–1,000 JSONL records.')
    ids = set()
    for i, record in enumerate(raw, 1):
        if record.get('task', 'extraction') != 'extraction':
            raise ValueError('This optimizer accepts extraction workloads only')
        ex = Example.model_validate({'id': record.get('id', f'item-{i:04d}'), 'task': 'extraction',
                                    'difficulty': record.get('difficulty', 'adversarial'),
                                    'input': record['input'], 'expected': record['expected']})
        ExtractionOutput.model_validate(ex.expected)
        if ex.id in ids:
            raise ValueError(f'Duplicate ID: {ex.id}')
        digest = hashlib.sha256(ex.input.strip().encode()).hexdigest()
        if digest in seen_text:
            raise ValueError('Duplicate conversation text; keep repeated samples together instead of splitting them.')
        if not ex.input.strip() or len(ex.input) > 20000:
            raise ValueError(f'{ex.id}: conversation must contain 1–20,000 characters')
        split = record.get('split')
        if split is None:
            split = 'tune' if int(digest[:8], 16) % 2 == 0 else 'heldout'
        if split not in splits:
            raise ValueError(f'{ex.id}: split must be tune or heldout')
        ids.add(ex.id)
        seen_text.add(digest)
        examples.append(ex)
        splits[split].append(ex.id)
    if not all(splits.values()):
        raise ValueError('Both tune and heldout need examples. Assign split explicitly on small workloads.')
    return examples, splits


def measured_options(examples: list[Example], records: list[CallRecord], *, accuracy: float,
                     field_accuracy: float, deadline_ms: float, hedge_ms: float = 1337) -> dict[str, Any]:
    index = {(r.candidate, r.example_id): r for r in records}
    ladder = [name for name in ('nano', 'super', 'ultra') if all((name, e.id) in index for e in examples)]
    options = candidate_options(ladder)
    options += [o for o in candidate_options(ladder, {name: hedge_ms for name in ladder}) if o.hedge_after_ms]
    metrics, mapping = {}, {}
    for option in options:
        outcomes = simulate(option, examples, index)
        metrics[option.label] = deployment_metrics(outcomes, deadline_ms)
        mapping[option.label] = vars(option)
    selected = cheapest_qualifying(metrics, accuracy=accuracy, field_accuracy=field_accuracy, deadline_ms=deadline_ms)
    return {'selected': selected, 'metrics': metrics, 'options': mapping,
            'requirements': {'accuracy': accuracy, 'field_accuracy': field_accuracy, 'deadline_ms': deadline_ms},
            'evidence': 'tuning replay; live heldout validation still required'}
