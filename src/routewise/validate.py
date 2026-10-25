"""Deterministic answer validation. Never sees ground truth; only the source text and the answer.

Rules (extraction):
  schema                  JSON parses and matches the output model (types, enums, ISO dates)
  amount_in_source        a non-null refund_amount appears as a money value in the source
  amount_refund_context   ...in a sentence that talks about a refund
  currency_consistent     amount and currency are both null or both set, and agree with the source marker
  order_id_in_source      a non-null order_id appears verbatim in the source and contains a digit
  dates_in_source         every non-null date is stated in, or derivable from, the source
  contact_date_valid      contact_date is the date of a customer message
  refund_date_matches_statement
                          when the source says when a refund is/was issued, refund_date is one of the
                          dates in those sentences (relative dates resolved from that message)

Known blind spot: a well-formed answer that is supported by the text but semantically wrong
(e.g. the quoted price instead of the agreed partial refund, if both sit in refund sentences).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from routewise.schema import ParsedOutput, Task
from routewise.textfacts import SourceFacts, analyze


@dataclass(frozen=True)
class Check:
    rule: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class Validation:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_rules(self) -> list[str]:
        return [c.rule for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failed_rules": self.failed_rules, "checks": [asdict(c) for c in self.checks]}


def validate(task: Task, source: str, parsed: ParsedOutput) -> Validation:
    if not parsed.schema_valid:
        detail = parsed.parse_error or parsed.schema_error or "invalid"
        return Validation(checks=(Check("schema", False, detail),))
    checks = [Check("schema", True)]
    if task == "extraction":
        checks += _extraction_checks(analyze(source), parsed.data)
    return Validation(checks=tuple(checks))


def _extraction_checks(facts: SourceFacts, data: dict[str, Any]) -> list[Check]:
    amount, currency = data["refund_amount"], data["currency"]
    matches = [m for m in facts.money if amount is not None and math.isclose(m.amount, amount, abs_tol=0.005)]

    if amount is None:
        in_source = Check("amount_in_source", True, "no amount extracted")
        context = Check("amount_refund_context", True, "no amount extracted")
    else:
        in_source = Check("amount_in_source", bool(matches), "" if matches else f"{amount} is not a money value in the source")
        in_refund = any(m.refund_context for m in matches)
        context = Check(
            "amount_refund_context",
            in_refund,
            "" if in_refund else f"{amount} never appears in a sentence about a refund",
        )

    if (amount is None) != (currency is None):
        currency_check = Check("currency_consistent", False, "refund_amount and currency must both be set or both null")
    elif matches and currency not in {m.currency for m in matches}:
        seen = sorted({m.currency for m in matches})
        currency_check = Check("currency_consistent", False, f"source marks {amount} as {seen}, answer says {currency}")
    else:
        currency_check = Check("currency_consistent", True)

    bad_dates = [
        f"{field}={data[field]}"
        for field in ("contact_date", "refund_date")
        if data[field] is not None and date.fromisoformat(data[field]) not in facts.dates
    ]
    dates_check = Check("dates_in_source", not bad_dates, "; ".join(f"{b} not stated in source" for b in bad_dates))

    contact = data["contact_date"]
    if not facts.customer_dates:
        contact_check = Check("contact_date_valid", True, "no customer messages")
    elif contact is None:
        contact_check = Check("contact_date_valid", False, "contact_date is null but the conversation has customer messages")
    elif date.fromisoformat(contact) not in facts.customer_dates:
        contact_check = Check("contact_date_valid", False, f"contact_date={contact} is not the date of any customer message")
    else:
        contact_check = Check("contact_date_valid", True)

    refund_date = data["refund_date"]
    stated = sorted(d.isoformat() for d in facts.refund_statement_dates)
    if not stated:
        refund_check = Check("refund_date_matches_statement", True, "no refund-date statement")
    elif refund_date is None:
        refund_check = Check("refund_date_matches_statement", False,
                             f"refund_date is null but the source states when a refund is/was issued ({', '.join(stated)})")
    elif refund_date not in stated:
        refund_check = Check("refund_date_matches_statement", False,
                             f"refund_date={refund_date} is not a date the refund statement gives ({', '.join(stated)})")
    else:
        refund_check = Check("refund_date_matches_statement", True)

    order_id = data["order_id"]
    if order_id is None:
        order_check = Check("order_id_in_source", True, "no order id extracted")
    else:
        normalized = order_id.strip().lstrip("#").strip().casefold()
        found = bool(normalized) and normalized in facts.text.casefold()
        has_digit = any(ch.isdigit() for ch in normalized)
        detail = "" if found and has_digit else (
            f"order_id={order_id!r} does not appear in the source" if not found else f"order_id={order_id!r} contains no digits"
        )
        order_check = Check("order_id_in_source", found and has_digit, detail)

    return [in_source, context, currency_check, order_check, dates_check, contact_check, refund_check]
