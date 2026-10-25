"""M5 fresh, pre-outcome fact sheets and split. All prose is synthetic, not human data."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from random import Random

from routewise.factsheets import FactSheet, PlanMessage, sample_factsheets

SEED = 2026102517
NEW_FAMILIES = ('tracking_distractor', 'superseded_order', 'numeric_order', 'product_and_date_not_id')
ID_FORMATS = ('ORD-{n}', 'web_{n}', 'AB/{n}/UK', '{n}', 'q{n}z', 'SHOP-{n}-B')


def fresh_sheets() -> list[FactSheet]:
    sheets = sample_factsheets(30, seed=SEED)
    for i, sheet in enumerate(sheets):
        sheet.id = sheet.id.replace('v2-', 'v3-')
        replacements = {old: ID_FORMATS[(i + j) % len(ID_FORMATS)].format(n=840000 + i * 3 + j)
                        for j, old in enumerate(sheet.order_ids)}
        for old, new in replacements.items():
            for msg in sheet.messages:
                msg.guidance = msg.guidance.replace(old, new)
                msg.must = [s.replace(old, new) for s in msg.must]
            if sheet.expected['order_id'] == old:
                sheet.expected['order_id'] = new
        sheet.order_ids = list(replacements.values())
    rng = Random(SEED)
    for family in NEW_FAMILIES:
        for i in range(30):
            day = date(2026, 2, 1) + timedelta(days=rng.randrange(200))
            refund = day + timedelta(days=3)
            oid = ID_FORMATS[i % len(ID_FORMATS)].format(n=970000 + i)
            amount = float(20 + i)
            money = f'USD {amount:.2f}'
            if family == 'tracking_distractor':
                tracking = f'TRK-{600000 + i}'
                plan = [PlanMessage('Customer', 0, 'asks for money back for the damaged item, distinguishing the order reference from the carrier tracking number', [f'order reference {oid}', f'tracking number {tracking}']),
                        PlanMessage('Agent', 0, 'confirms the refund amount and its issue date', [money, refund.isoformat()])]
            elif family == 'superseded_order':
                old = f'OLD-{500000 + i}'
                plan = [PlanMessage('Customer', 0, 'asks to refund the current order, explicitly saying the old cancelled order is unrelated', [f'old order {old}', f'current order {oid}']),
                        PlanMessage('Agent', 0, 'confirms the refund for the current order only and states its issue date', [f'order {oid}', money, refund.isoformat()])]
            elif family == 'numeric_order':
                oid = str(78000000 + i)
                plan = [PlanMessage('Customer', 0, 'asks for a refund and gives the numeric order identifier, which is not a price', [f'order number {oid}']),
                        PlanMessage('Agent', 0, 'confirms the numeric refund amount and issue date', [money, refund.isoformat()])]
            else:
                oid = None
                plan = [PlanMessage('Customer', 0, 'asks for money back for the product; explicitly has no order number available. The product name and purchase date are not order IDs', ['TrailMax 2 tent', (day - timedelta(days=5)).isoformat()]),
                        PlanMessage('Agent', 0, 'confirms the refund amount and issue date without assigning any order identifier', [money, refund.isoformat()])]
            allowed_ids = [s for s in (oid, f'TRK-{600000 + i}' if family == 'tracking_distractor' else None,
                                       f'OLD-{500000 + i}' if family == 'superseded_order' else None) if s]
            expected = {'refund_requested': True, 'refund_amount': amount, 'currency': 'USD', 'order_id': oid,
                        'contact_date': day.isoformat(), 'refund_date': refund.isoformat()}
            sheets.append(FactSheet(id=f'v3-new-{NEW_FAMILIES.index(family)}-{i:03d}', family=family,
                writer=('super', 'ultra')[i % 2], tone=('terse', 'formal', 'frustrated')[i % 3], typos=i % 4 == 0,
                contact=day.isoformat(), start=f'{day.isoformat()}T09:15', messages=plan, expected=expected,
                allowed_amounts=[(amount, 'USD')], allowed_dates=[day.isoformat(), refund.isoformat(), (day-timedelta(days=5)).isoformat()],
                allowed_phrases=[], order_ids=allowed_ids))
    return sheets


def split_name(sheet: FactSheet) -> str:
    # Existing M3 test families stay test-only. Four additional families are entirely unseen in M5 development.
    test_only = {'large_amount', 'refund_in_past', 'partial_refund_distractor', 'relative_refund_date', *NEW_FAMILIES}
    return 'heldout' if sheet.family in test_only or int(sheet.id[-3:]) >= 10 else 'tune'


def save_plan(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    sheets = fresh_sheets()
    (root/'factsheets.jsonl').write_text(''.join(json.dumps(s.to_dict())+'\n' for s in sheets))
    (root/'planned_splits.json').write_text(json.dumps({k: [s.id for s in sheets if split_name(s) == k]
                                                      for k in ('tune', 'heldout')}, indent=2)+'\n')
