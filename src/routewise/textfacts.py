"""Deterministic facts read from a support conversation: messages, money mentions and dates.

The grammar covers the formats used by this workload: ISO dates, "March 17[, 2026]",
"17 March [2026]", "Mar 17", and the relative forms "today", "tomorrow", "in N days".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache

_MSG = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\] ([A-Za-z]+): (.*)$")

MARKER_CURRENCY = {
    "US$": "USD",
    "CA$": "CAD",
    "A$": "AUD",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    **{code: code for code in ("USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF")},
}
_PRE = r"US\$|CA\$|A\$|\$|€|£|¥|USD|EUR|GBP|CAD|AUD|JPY|CHF"
_POST = r"USD|EUR|GBP|CAD|AUD|JPY|CHF|€"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_MONEY = re.compile(rf"(?:(?P<pre>{_PRE})\s?(?P<n1>{_NUM}))|(?:(?P<n2>{_NUM})\s?(?P<post>{_POST})\b)")

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("Jan", "January"), ("Feb", "February"), ("Mar", "March"), ("Apr", "April"),
            ("May",), ("Jun", "June"), ("Jul", "July"), ("Aug", "August"),
            ("Sep", "Sept", "September"), ("Oct", "October"), ("Nov", "November"), ("Dec", "December"),
        ],
        start=1,
    )
    for m in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MDY = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?")
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})\b(?:,?\s+(\d{{4}}))?")
_NUMBER_WORDS = {w: i for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"])}
_IN_DAYS = re.compile(r"\bin (\d+|" + "|".join(_NUMBER_WORDS) + r") days?\b", re.IGNORECASE)
_TODAY = re.compile(r"\btoday\b", re.IGNORECASE)
_TOMORROW = re.compile(r"\btomorrow\b", re.IGNORECASE)

REFUND_CUE = re.compile(r"\brefund|\breimburs|money back|\bcredited\b|pay (?:you|me) back", re.IGNORECASE)
_ISSUE_CUE = re.compile(r"\b(?:issu|process|sent\b|send|initiat|go(?:es)? out|went out)", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Message:
    when: datetime
    speaker: str
    text: str


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str
    sentence: str
    refund_context: bool


@dataclass(frozen=True)
class SourceFacts:
    messages: tuple[Message, ...]
    money: tuple[Money, ...]
    dates: frozenset[date]  # every date stated or derivable (timestamps, absolute, relative)
    customer_dates: frozenset[date]  # timestamp dates of customer messages
    refund_date_stated: bool  # some sentence says when a refund is/was issued


def parse_messages(text: str) -> list[Message]:
    messages = []
    for line in text.splitlines():
        m = _MSG.match(line.strip())
        if m:
            when = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
            messages.append(Message(when=when, speaker=m.group(3), text=m.group(4)))
    return messages


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def parse_amount(raw: str) -> float:
    return float(raw.replace(",", ""))


def money_mentions(sentence: str) -> list[tuple[float, str]]:
    found = []
    for m in _MONEY.finditer(sentence):
        marker = m.group("pre") or m.group("post")
        number = m.group("n1") or m.group("n2")
        found.append((parse_amount(number), MARKER_CURRENCY[marker]))
    return found


def dates_in(sentence: str, anchor: date) -> set[date]:
    """Absolute and relative dates in one sentence; `anchor` is the message date."""
    found: set[date] = set()
    for y, mo, d in _ISO.findall(sentence):
        found |= _safe_date(int(y), int(mo), int(d))
    for mon, d, y in _MDY.findall(sentence):
        found |= _safe_date(int(y) if y else anchor.year, _MONTHS[mon], int(d))
    for d, mon, y in _DMY.findall(sentence):
        found |= _safe_date(int(y) if y else anchor.year, _MONTHS[mon], int(d))
    if _TODAY.search(sentence):
        found.add(anchor)
    if _TOMORROW.search(sentence):
        found.add(anchor + timedelta(days=1))
    for n in _IN_DAYS.findall(sentence):
        days = int(n) if n.isdigit() else _NUMBER_WORDS[n.lower()]
        found.add(anchor + timedelta(days=days))
    return found


@lru_cache(maxsize=4096)
def analyze(text: str) -> SourceFacts:
    messages = parse_messages(text)
    money: list[Money] = []
    all_dates: set[date] = set()
    refund_date_stated = False
    for msg in messages:
        anchor = msg.when.date()
        all_dates.add(anchor)
        for sent in sentences(msg.text):
            refund_cue = bool(REFUND_CUE.search(sent))
            for amount, currency in money_mentions(sent):
                money.append(Money(amount=amount, currency=currency, sentence=sent, refund_context=refund_cue))
            sent_dates = dates_in(sent, anchor)
            all_dates |= sent_dates
            if refund_cue and _ISSUE_CUE.search(sent) and sent_dates:
                refund_date_stated = True
    return SourceFacts(
        messages=tuple(messages),
        money=tuple(money),
        dates=frozenset(all_dates),
        customer_dates=frozenset(m.when.date() for m in messages if m.speaker.lower() == "customer"),
        refund_date_stated=refund_date_stated,
    )


def _safe_date(year: int, month: int, day: int) -> set[date]:
    try:
        return {date(year, month, day)}
    except ValueError:
        return set()
