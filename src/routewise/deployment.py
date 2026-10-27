"""The adopted extraction profile and a portable, evidence-scoped deployment decision."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from routewise import artifacts
from routewise.config import Config, load_config
from routewise.evaluation import build_routers
from routewise.prices import load_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.runlog import sha256_file
from routewise.statistics import cheapest_qualifying, wilson

PROFILE = Path('policies/deployment/m7-extraction.json')
CONFIG = Path('config/grounded.yaml')
VALIDATION_STATUS = Path('data/v5/validation-status.json')
ARM_LABELS = {'deterministic': 'Adopted route · date normalization',
              'control': 'Previous route · without date normalization',
              'fixed_grounded_super': 'Grounded Super'}


def load_adopted(root: Path | None = None) -> tuple[Config, dict]:
    root = root or artifacts.artifacts_root()
    config = load_config(root / CONFIG)
    policy = artifacts.load_json(root / PROFILE)
    prompt = load_prompt(config.prompts['extraction'], 'extraction')
    if policy['prompts']['extraction'] != prompt.version:
        raise ValueError('The adopted profile and extraction prompt versions differ')
    return config, policy


def adopted_router(client, root: Path | None = None):
    """Use the same builder as the documented CLI, including recovery and review decisions."""
    return profile_router(client, 'deterministic', root)


def profile_router(client, choice: str, root: Path | None = None):
    if choice not in ARM_LABELS:
        raise ValueError(f'Unknown measured configuration: {choice}')
    config, policy = load_adopted(root)
    if choice == 'control':
        policy = artifacts.load_json((root or artifacts.artifacts_root()) / 'policies/deployment/m6-extraction.json')
    snapshot = load_snapshot(config.prices_snapshot)
    prices = {c.label: price_for(snapshot, c.model_id) for c in config.candidates}
    prompts = {t: load_prompt(p, t) for t, p in config.prompts.items()}
    return build_routers(client, config, policy, prices, prompts)[
        'always_super' if choice == 'fixed_grounded_super' else 'routewise']


def adopted_evidence(root: Path | None = None) -> dict:
    root = root or artifacts.artifacts_root()
    config, policy = load_adopted(root)
    path = root / 'runs' / policy['validation_run']
    summary = artifacts.load_json(path / 'summary.json')
    if not summary.get('complete') or not summary.get('decision', {}).get('adopt'):
        raise ValueError('No complete adopted-profile confirmation is available')
    observations = artifacts.load_records(artifacts.RunInfo(path, {}), 'observations.jsonl')
    examples = {e['id']: e for e in artifacts.load_records(
        artifacts.RunInfo(root / 'data/v5/confirm', {}), 'examples.jsonl')}
    return {'config': config, 'policy': policy, 'summary': summary,
            'observations': observations, 'examples': examples,
            'human_validation': artifacts.load_json(root / VALIDATION_STATUS)}


def explore_requirements(evidence: dict, *, accuracy: float, field_accuracy: float, deadline_ms: float) -> tuple[dict, str | None]:
    """Explore representative M6 evidence only; never pool in the challenge set or retune a policy."""
    metrics = copy.deepcopy(evidence['summary']['metrics']['representative'])
    for name, m in metrics.items():
        traces = [r['trace'] for r in evidence['observations']
                  if r['set'] == 'representative' and r['trace']['strategy'] == name]
        if len(traces) != m['n']:
            raise ValueError(f'Incomplete representative traces for {name}')
        misses = sum(t['latency_ms'] is None or t['latency_ms'] > deadline_ms for t in traces)
        m.update(deadline_misses=misses, deadline_miss_rate=misses / len(traces),
                 deadline_miss_upper95=wilson(misses, len(traces), one_sided=True)[1])
    selected = cheapest_qualifying(metrics, accuracy=accuracy, field_accuracy=field_accuracy, deadline_ms=deadline_ms)
    return metrics, selected


def deployment_decision(evidence: dict, metrics: dict, selected: str | None, *, accuracy: float,
                        field_accuracy: float, deadline_ms: float, root: Path | None = None) -> dict[str, Any]:
    root = root or artifacts.artifacts_root()
    config, adopted = evidence['config'], evidence['policy']
    profiles = {'deterministic': PROFILE, 'control': Path('policies/deployment/m6-extraction.json')}
    chosen_path = profiles.get(selected)
    chosen = artifacts.load_json(root / chosen_path) if chosen_path else None
    sources = [CONFIG, PROFILE, VALIDATION_STATUS, Path('policies/deployment/m6-extraction.json'),
               config.prompts['extraction'].relative_to(root), config.prices_snapshot.relative_to(root),
               Path('src/routewise/recovery.py'), Path('src/routewise/issue_dates.py'),
               Path('src/routewise/validate.py'), Path('src/routewise/grounding.py'),
               Path('src/routewise/deployment.py'), Path('src/routewise/evaluation.py'),
               Path('src/routewise/router.py'), Path('src/routewise/execution.py'),
               Path('runs') / adopted['validation_run'] / 'summary.json',
               Path('runs') / adopted['validation_run'] / 'observations.jsonl']
    return {
        'schema_version': 'routewise.deployment-decision.v1',
        'decision': selected, 'configuration': ARM_LABELS.get(selected),
        'requirements': {'task_accuracy': accuracy, 'field_accuracy': field_accuracy,
                         'deadline_ms': deadline_ms, 'max_deadline_miss_upper95': .05},
        'evidence': {'run': adopted['validation_run'], 'set': 'representative',
                     'provenance': 'synthetic, model-written; initially agent-reviewed; independent human validation completed for this representative dataset (project-owner confirmation)',
                     'human_validation': evidence['human_validation'],
                     'status': 'exploration of recorded evidence; new requirements are not a fresh confirmation'},
        'metrics': metrics.get(selected) if selected else None,
        'profile': chosen,
        'runtime': {'config': str(CONFIG), 'profile': str(chosen_path) if chosen_path else None,
                    'fixed_model': 'super' if selected == 'fixed_grounded_super' else None,
                    'prompt': 'prompts/extraction_v4.txt', 'release_condition': 'final_valid must be true'},
        'cli': (f'routewise --config {CONFIG} route --policy {chosen_path or PROFILE}'
                f'{" --strategy always_super" if selected == "fixed_grounded_super" else ""}'
                ' --task extraction --text "YOUR TIMESTAMPED CONVERSATION" --json') if selected else None,
        'source_sha256': {str(p): sha256_file(root / p) for p in sources},
        'limits': ['Refund extraction only; six-field contract.',
                   'Review is not an autonomous completion; validator pass is not proof of correctness.',
                   'Usage-cost projections exclude hosting, labeling, development and human review.',
                   'Validate independent conversations and additional serving days before production use.'],
    }
