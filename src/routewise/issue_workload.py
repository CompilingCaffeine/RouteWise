"""M6: fresh fact sheets that explicitly bind issue dates, plus arrival-date negatives."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from routewise.factsheets import FactSheet, PlanMessage
from routewise.fresh_workload import fresh_sheets as base_sheets, NEW_FAMILIES
from routewise.textfacts import dates_in

SEED = 2026102518
ARRIVAL_FAMILIES = ('arrival_without_issue', 'arrival_versus_issue')


def fresh_sheets():
    sheets = base_sheets(seed=SEED, per_family=20, version='v4')
    bind_issue_dates(sheets)
    return sheets + arrival_sheets('v4', start=date(2026, 4, 1), id_base=1380000, amount_base=35, per_family=20)


def bind_issue_dates(sheets):
    """Make every non-null issue date an exact issue statement and every order ID an explicit order reference."""
    for sheet in sheets:
        oid = sheet.expected['order_id']
        if oid:
            # Avoid the product-code ambiguity also found in the M5 blind audit.
            # This is label construction before writing, never a model-answer filter.
            first_customer = next(m for m in sheet.messages if m.speaker == 'Customer')
            first_customer.must.append(f'order reference {oid}')
            first_customer.guidance += ' Explicitly identify the planned order reference as an order, not a product or SKU code.'
        rd = sheet.expected['refund_date']
        if rd is None:
            continue
        expected_date = date.fromisoformat(rd)
        found = False
        for msg in reversed(sheet.messages):
            if msg.speaker != 'Agent':
                continue
            anchor = date.fromisoformat(sheet.contact) + timedelta(days=msg.day)
            date_strings = [s for s in msg.must if expected_date in dates_in(s, anchor)]
            if not date_strings:
                continue
            phrase = date_strings[-1]
            relative = phrase.lower() in ('today', 'tomorrow') or phrase.lower().startswith('in ')
            prep = '' if relative else 'on '
            statement = (f'The refund was issued {prep}{phrase}' if expected_date < anchor
                         else f'The refund will be issued {prep}{phrase}')
            msg.must = [s for s in msg.must if s not in date_strings] + [statement]
            msg.guidance += (' The date is specifically the refund ISSUE date, not receipt, arrival, settlement, '
                             'or approval. Copy the full planned issue-date statement exactly. Do not replace '
                             'it with when the money should arrive or clear.')
            found = True
            break
        if not found:
            raise ValueError(f'No planned issue-date message: {sheet.id}')


def arrival_sheets(version, *, start, id_base, amount_base, per_family):
    """Arrival date with explicitly no issue date (null), and distinct issue and arrival dates."""
    sheets = []
    for family in ARRIVAL_FAMILIES:
        for i in range(per_family):
            day = start + timedelta(days=i*3)
            issue, arrival = day+timedelta(days=1), day+timedelta(days=5)
            oid, amount = f'CASE-{id_base+i}', float(amount_base+i)
            plan = [PlanMessage('Customer', 0, 'asks for a full refund and names the order', [f'order {oid}']),
                    PlanMessage('Agent', 0, 'confirms the amount and states exactly the planned date facts; do not infer or invent a different issue date',
                                [f'USD {amount:.2f}', f'The money should arrive by {arrival.isoformat()}',
                                 'No refund issue date has been provided' if family == 'arrival_without_issue'
                                 else f'The refund will be issued on {issue.isoformat()}'])]
            sheets.append(FactSheet(id=f'{version}-arrival-{ARRIVAL_FAMILIES.index(family)}-{i:03d}',family=family,
                writer=('super','ultra')[i%2],tone=('terse','formal')[i%2],typos=False,contact=day.isoformat(),
                start=f'{day.isoformat()}T09:00',messages=plan,
                expected={'refund_requested':True,'refund_amount':amount,'currency':'USD','order_id':oid,
                          'contact_date':day.isoformat(),'refund_date':None if family == 'arrival_without_issue' else issue.isoformat()},
                allowed_amounts=[(amount,'USD')],allowed_dates=[day.isoformat(),arrival.isoformat()] +
                    ([issue.isoformat()] if family == 'arrival_versus_issue' else []),
                allowed_phrases=[],order_ids=[oid]))
    return sheets


def split_name(sheet):
    test_only = {'large_amount','refund_in_past','partial_refund_distractor','relative_refund_date',*NEW_FAMILIES,*ARRIVAL_FAMILIES}
    return 'heldout' if sheet.family in test_only or int(sheet.id[-3:]) >= 8 else 'tune'


def save_plan(root: Path):
    root.mkdir(parents=True,exist_ok=True)
    sheets=fresh_sheets()
    (root/'factsheets.jsonl').write_text(''.join(json.dumps(s.to_dict())+'\n' for s in sheets))
    (root/'planned_splits.json').write_text(json.dumps({k:[s.id for s in sheets if split_name(s)==k] for k in ('tune','heldout')},indent=2)+'\n')
