"""Pre-route request features for the failure predictor (M3).

Only the raw conversation text is used: never a model's answer, a validator result, or any
dataset metadata (family, writer, tone). Every feature is a deterministic count or flag.
"""

from __future__ import annotations

import re

from routewise.textfacts import _DMY, _IN_DAYS, _ISO, _MDY, _TODAY, _TOMORROW, analyze, sentences

FEATURES = (
    "n_messages", "n_customer_messages", "n_agent_messages", "chars", "days_spanned", "first_not_customer",
    "n_money_mentions", "n_distinct_amounts", "n_amounts_in_refund_sentences", "has_non_usd", "has_currency_symbol_only",
    "n_explicit_dates", "n_relative_dates", "n_distinct_dates", "refund_date_statement",
    "n_order_like", "negated_refund_cue", "counteroffer_cue", "fee_cue", "multi_item_cue",
)

_ORDER_LIKE = re.compile(r"\b[A-Z]{2,5}-\d{3,}\b|#\d{4,}\b")
_NEGATED = re.compile(r"\b(?:don'?t|do not|not|no|rather than)\b[^.!?]{0,40}\b(?:refund|money back)", re.IGNORECASE)
_COUNTER = re.compile(r"\b(?:instead|only (?:offer|refund)|can offer|partial)\b", re.IGNORECASE)
_FEE = re.compile(r"\b(?:shipping|fee|delivery charge|express)\b", re.IGNORECASE)
_MULTI = re.compile(r"\b(?:both|two items|each item|two orders|two open orders)\b", re.IGNORECASE)
_SYMBOL_ONLY = re.compile(r"(?:CA\$|A\$|€|£|¥)\s?\d")


def extract(text: str) -> dict[str, float]:
    facts = analyze(text)
    msgs = facts.messages
    days = [m.when.date() for m in msgs]
    explicit = relative = 0
    for m in msgs:
        for sent in sentences(m.text):
            explicit += len(_ISO.findall(sent)) + len(_MDY.findall(sent)) + len(_DMY.findall(sent))
            relative += len(_TODAY.findall(sent)) + len(_TOMORROW.findall(sent)) + len(_IN_DAYS.findall(sent))
    amounts = {round(m.amount, 2) for m in facts.money}
    return {
        "n_messages": len(msgs),
        "n_customer_messages": sum(m.speaker.lower() == "customer" for m in msgs),
        "n_agent_messages": sum(m.speaker.lower() == "agent" for m in msgs),
        "chars": len(text),
        "days_spanned": (max(days) - min(days)).days if days else 0,
        "first_not_customer": float(bool(msgs) and msgs[0].speaker.lower() != "customer"),
        "n_money_mentions": len(facts.money),
        "n_distinct_amounts": len(amounts),
        "n_amounts_in_refund_sentences": len({round(m.amount, 2) for m in facts.money if m.refund_context}),
        "has_non_usd": float(any(m.currency != "USD" for m in facts.money)),
        "has_currency_symbol_only": float(bool(_SYMBOL_ONLY.search(text))),
        "n_explicit_dates": explicit,
        "n_relative_dates": relative,
        "n_distinct_dates": len(facts.dates),
        "refund_date_statement": float(facts.refund_date_stated),
        "n_order_like": len(set(_ORDER_LIKE.findall(text))),
        "negated_refund_cue": float(bool(_NEGATED.search(text))),
        "counteroffer_cue": float(bool(_COUNTER.search(text))),
        "fee_cue": float(bool(_FEE.search(text))),
        "multi_item_cue": float(bool(_MULTI.search(text))),
    }


def vector(text: str) -> list[float]:
    f = extract(text)
    return [float(f[name]) for name in FEATURES]
