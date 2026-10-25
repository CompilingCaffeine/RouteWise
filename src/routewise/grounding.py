"""Source evidence for extraction v3; no labels or scenario metadata are read."""

from __future__ import annotations

import copy
import re
from typing import Any

from routewise.textfacts import analyze

# Context is required: a date or a product containing a digit is not an order ID.
_ORDER = re.compile(
    r"\b(?:order(?:ed)?(?:\s+(?:under|with))?|purchase)(?:\s+(?:number|identifier|id|reference|ref|no\.?))?"
    r"\s*(?:(?:is|was)\s+)?[:=#\s]*[\"'`]?"
    r"(?P<id>[A-Za-z0-9][A-Za-z0-9_/-]*\d[A-Za-z0-9_/-]*|\d+)\b",
    re.IGNORECASE,
)
_PREFIXED = re.compile(r"\b[A-Za-z]{2,10}[-_][A-Za-z0-9][A-Za-z0-9_/-]*\b")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OPAQUE = re.compile(r'\b[A-Za-z]{1,10}\d{4,}[A-Za-z]{0,8}\b')
_REQUEST_CONTEXT = re.compile(r'\b(?:order|ordered|purchase|refund)\b', re.IGNORECASE)


def evidence(text: str) -> dict[str, Any]:
    facts = analyze(text)
    ids = []
    for msg in facts.messages:
        spans = [m.group('id') for m in _ORDER.finditer(msg.text)]
        if _REQUEST_CONTEXT.search(msg.text):
            spans.extend(m.group() for m in _OPAQUE.finditer(msg.text))
        # A conventional prefixed identifier is also allowed when explicitly quoted
        # without an adjacent order cue. Tracking/invoice cues alone do not qualify.
        for m in _PREFIXED.finditer(msg.text):
            token = m.group()
            if token.upper().startswith(('ORD-', 'ORDER-', 'PO-')):
                spans.append(token)
        for token in spans:
            if any(c.isdigit() for c in token) and not _DATE.fullmatch(token) and token not in ids:
                ids.append(token)
    customers = [m for m in facts.messages if m.speaker.lower() == 'customer']
    return {
        'order_ids': ids,
        'money': [{'amount': m.amount, 'currency': m.currency, 'sentence': m.sentence} for m in facts.money],
        'contact_date': customers[0].when.date().isoformat() if customers else None,
    }


def grounded_schema(schema: dict[str, Any], text: str) -> dict[str, Any]:
    schema = copy.deepcopy(schema)
    ev = evidence(text)
    schema['properties']['order_id']['enum'] = [*ev['order_ids'], None]
    schema['properties']['refund_amount']['enum'] = sorted({m['amount'] for m in ev['money']}) + [None]
    if not ev['money']:
        schema['properties']['currency']['enum'] = [None]
    schema['properties']['contact_date']['enum'] = [ev['contact_date']]
    return schema


def normalize(data: dict[str, Any], text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Enforce evidence even on endpoints that ignore schema enums; retain an audit."""
    ev = evidence(text)
    out = dict(data)
    out['contact_date'] = ev['contact_date']
    if out.get('order_id') not in ev['order_ids']:
        out['order_id'] = None
    if not ev['money']:
        out['refund_amount'] = out['currency'] = None
    return out, {'evidence': ev, 'raw_parsed': data,
                 'changed_fields': [k for k in out if out[k] != data.get(k)]}
