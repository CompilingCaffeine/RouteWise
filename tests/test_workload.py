import json

import pytest

from routewise.workload import parse_workload
from routewise.fresh_workload import fresh_sheets, split_name, NEW_FAMILIES


def row(n, split):
    return {'id': str(n), 'split': split, 'input': f'Conversation {n}', 'expected': {
        'refund_requested': False, 'refund_amount': None, 'currency': None, 'order_id': None,
        'contact_date': None, 'refund_date': None}}


def test_import_preserves_explicit_blind_split_and_rejects_cross_split_duplicates():
    a, b = row(1, 'tune'), row(2, 'heldout')
    examples, splits = parse_workload('\n'.join(map(json.dumps, [a,b])))
    assert len(examples) == 2 and splits == {'tune': ['1'], 'heldout': ['2']}
    b['input'] = a['input']
    with pytest.raises(ValueError, match='Duplicate conversation'):
        parse_workload('\n'.join(map(json.dumps, [a,b])))


def test_import_rejects_missing_ground_truth_fields():
    a, b = row(1, 'tune'), row(2, 'heldout')
    del a['expected']['currency']
    with pytest.raises(ValueError):
        parse_workload('\n'.join(map(json.dumps, [a,b])))


def test_fresh_plan_is_disjoint_and_all_new_families_are_test_only():
    sheets = fresh_sheets()
    assert len(sheets) == 600 and len({s.id for s in sheets}) == 600
    assert sum(split_name(s) == 'tune' for s in sheets) == 120
    assert all(split_name(s) == 'heldout' for s in sheets if s.family in NEW_FAMILIES)
    assert fresh_sheets()[0].to_dict() == sheets[0].to_dict()
