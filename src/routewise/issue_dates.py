"""Refund issue-date analysis for M7 (displayed as M6). Reads only the source text, never labels.

Only store messages (Agent/System) can state the issue date. Each temporal expression is
classified by the clause it sits in (issue, arrival or another event) and resolved against the
timestamp of its own message. Expressions that do not determine one calendar date are marked
ambiguous; temporal language the grammar cannot read is marked unrecognized. An explicit
correction in the latest store message supersedes earlier issue statements.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from routewise.textfacts import _DMY, _ISO, _MDY, _MONTHS, parse_messages, sentences

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WD = "|".join(WEEKDAYS)
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "twenty": 20,
          "thirty": 30}
_N = r"(\d+|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_I = re.IGNORECASE


def _num(token: str) -> int:
    return int(token) if token.isdigit() else _WORDS[token.lower()]


# Each rule: (kind, pattern, resolver). Resolvers get (match, anchor, past) and return a date
# for kind "relative"; ambiguous rules return None and carry a reason instead.
_AMBIGUOUS: list[tuple[str, re.Pattern[str]]] = [
    ("business days", re.compile(rf"\b(?:in|within|after)?\s*(?:the\s+next\s+)?{_N}(?:\s*(?:-|–|to|or)\s*{_N})?"
                                 r"\s+(?:business|working|banking)\s+days?\b", _I)),
    ("range", re.compile(rf"\b{_N}\s*(?:-|–|to|or)\s*{_N}\s+(?:days?|weeks?)\b", _I)),
    ("bound", re.compile(rf"\bwithin\s+(?:the\s+next\s+)?{_N}\s+(?:days?|weeks?|hours?)\b", _I)),
    ("bound", re.compile(rf"\b(?:by|no later than|before|until)\s+(?:(?:this|the|next)\s+)?(?:{_WD}|tomorrow|"
                         rf"the end of\b|\d{{4}}-\d{{2}}-\d{{2}}|(?:{_MON})\.?\s+\d|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MON})\b)", _I)),
    ("next weekday", re.compile(rf"\bnext\s+(?:{_WD})\b", _I)),
    ("last weekday", re.compile(rf"\blast\s+(?:{_WD})\b", _I)),
    ("vague period", re.compile(r"\b(?:(?:early|late|later|mid)\s+)?(?:next|this|the following|the coming)\s+"
                                r"(?:week|weekend|month)\b", _I)),
    ("vague period", re.compile(r"\b(?:the\s+)?(?:end|beginning|start|middle)\s+of\s+(?:the|this|next)\s+"
                                r"(?:week|month)\b", _I)),
    ("vague period", re.compile(r"\b(?:in\s+)?(?:a\s+few|a\s+couple\s+of|couple\s+of|several|the\s+next\s+few)"
                                r"\s+(?:days|weeks)\b", _I)),
]

_RELATIVE: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"\b(?:the\s+)?day\s+after\s+tomorrow\b", _I), lambda m, a, p: a + timedelta(days=2)),
    (re.compile(r"\b(?:the\s+)?day\s+before\s+yesterday\b", _I), lambda m, a, p: a - timedelta(days=2)),
    (re.compile(r"\btomorrow\b", _I), lambda m, a, p: a + timedelta(days=1)),
    (re.compile(r"\byesterday\b", _I), lambda m, a, p: a - timedelta(days=1)),
    (re.compile(r"\b(?:earlier\s+|later\s+)?today\b|\bthis\s+(?:morning|afternoon|evening)\b|\btonight\b", _I),
     lambda m, a, p: a),
    (re.compile(rf"\bin\s+{_N}\s+days?(?:'s?\s+time)?\b", _I), lambda m, a, p: a + timedelta(days=_num(m.group(1)))),
    (re.compile(rf"\b{_N}\s+days?\s+from\s+(?:now|today)\b", _I), lambda m, a, p: a + timedelta(days=_num(m.group(1)))),
    (re.compile(rf"\b{_N}\s+days?\s+ago\b", _I), lambda m, a, p: a - timedelta(days=_num(m.group(1)))),
    (re.compile(rf"\bin\s+{_N}\s+weeks?(?:'s?\s+time)?\b", _I), lambda m, a, p: a + timedelta(weeks=_num(m.group(1)))),
    (re.compile(rf"\b{_N}\s+weeks?\s+from\s+(?:now|today)\b", _I), lambda m, a, p: a + timedelta(weeks=_num(m.group(1)))),
    (re.compile(rf"\b{_N}\s+weeks?\s+ago\b", _I), lambda m, a, p: a - timedelta(weeks=_num(m.group(1)))),
    (re.compile(r"\bin\s+a\s+fortnight\b|\ba\s+fortnight\s+from\s+(?:now|today)\b", _I),
     lambda m, a, p: a + timedelta(days=14)),
    (re.compile(rf"\b(?:in\s+{_N}\s+hours|{_N}\s+hours\s+from\s+now)\b", _I), lambda m, a, p: _hours(m, a)),
    # A bare/"on"/"this" weekday is the next such day (the most recent one for past events);
    # a weekday followed by a calendar date is left to the absolute rule.
    (re.compile(rf"\b(?:this\s+(?:coming\s+)?|on\s+|coming\s+)?({_WD})\b(?!,?\s+(?:\d|(?:{_MON})\b))", _I),
     lambda m, a, p: _weekday(m.group(1), a, p)),
]

_TEMPORAL_HINT = re.compile(
    rf"\b(?:{_WD}|weekend|fortnight|week|weeks|month|months|days?|hours?|tonight|morning|afternoon|evening|"
    r"noon|midnight|asap|same day)\b", _I)

_ISSUE = re.compile(r"\b(?:issu\w*|process\w*|sen[dt]\b|sending|initiat\w*|submit\w*|releas\w*|"
                    r"go(?:es)?\s+out|went\s+out|gone\s+out|go(?:es)?\s+through|went\s+through|"
                    r"schedul\w*|refunded|be\s+refunded|we(?:'ll|\s+will)?\s+refund|reimburs\w*|"
                    r"pa(?:y|id)\s+(?:you\s+)?back|put\s+through)", _I)
_ARRIVAL = re.compile(r"\b(?:arriv\w*|reach\w*|appear\w*|show(?:s|ing)?\s+up|show(?:s)?\s+(?:in|on)|"
                      r"clear\w*|settl\w*|post(?:s|ed)?\s+(?:to|on|in)|land\w*|hit\w*\s+your|reflect\w*|visible|"
                      r"be\s+credited|credited\s+(?:to|back)|in\s+your\s+(?:account|bank|card|statement)|"
                      r"(?:you|it)(?:'ll|\s+will|\s+should|\s+would|\s+may|\s+might)?\s+(?:receive|get|see)\b)", _I)
_OTHER_EVENT = re.compile(r"\b(?:approv\w*|ship\w*|deliver\w*|dispatch\w*|email\w*|inspect\w*|"
                          r"we(?:'ve|\s+have)?\s+received|received\s+(?:your|the)\s+(?:return|item|package)|"
                          r"return\s+(?:was|has\s+been)\s+received|order(?:ed)?\s+on|bought|purchased|"
                          r"repair\w*|replac\w*|label)", _I)
_REFUND_CONTEXT = re.compile(r"\brefund|\breimburs|money\s+back|\bcredit(?:ed)?\b|pay\s+(?:you|me)\s+back|"
                             r"\bthe\s+(?:full\s+|partial\s+)?amount\b|\byour\s+money\b|\bthe\s+(?:funds|money)\b", _I)
_BOUND_BEFORE = re.compile(r"\b(?:by|no\s+later\s+than|before|until)\s+(?:this\s+time\s+|the\s+)?$", _I)
_WEEKDAY_BEFORE_DATE = re.compile(rf"\b(?:{_WD}),?\s+(?=\d|(?:{_MON})\b)", _I)
_PRONOUN_START = re.compile(r"^\s*(?:it|this|that|these|they)\b", _I)
_PAST = re.compile(r"\b(?:was|were|has\s+been|have\s+been|had\s+been|went\s+out|already)\b", _I)
_CORRECTION = re.compile(r"\b(?:correct\w*|updat\w*|actually|instead|revis\w*|chang\w*|now\s+be|rather\s+than|"
                         r"mistake|error|amend\w*|re-?schedul\w*|moved|pushed\s+back|brought\s+forward)\b", _I)
_CLAUSE_SPLIT = re.compile(r"\s*(?:;|:|—|–\s|\s-\s|,?\s+\b(?:and|but|while|whereas|although|so)\b\s+|,\s+(?=which\b))\s*", _I)


@dataclass(frozen=True)
class Mention:
    message_index: int
    speaker: str
    anchor: str  # ISO date of the message timestamp
    sentence: str
    expression: str
    role: str  # issue | arrival | other | unknown
    kind: str  # absolute | relative | ambiguous | unrecognized
    resolved: str | None  # ISO date when kind is absolute/relative
    reason: str = ""


@dataclass(frozen=True)
class IssueDateAnalysis:
    status: str  # resolved | none | review
    date: str | None
    reason: str
    known_ambiguity: bool  # review because of a declared ambiguous form or a conflict, not unreadable text
    mentions: tuple[Mention, ...] = field(default_factory=tuple)

    @property
    def arrival_dates(self) -> frozenset[str]:
        return frozenset(m.resolved for m in self.mentions if m.role == "arrival" and m.resolved)

    @property
    def issue_mentions(self) -> tuple[Mention, ...]:
        return tuple(m for m in self.mentions if m.role == "issue")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "date": self.date, "reason": self.reason,
                "known_ambiguity": self.known_ambiguity, "mentions": [asdict(m) for m in self.mentions]}


def _hours(m: re.Match[str], anchor: date) -> date | None:
    hours = _num(m.group(1) or m.group(2))
    return anchor + timedelta(days=hours // 24) if hours % 24 == 0 else None


def _weekday(name: str, anchor: date, past: bool) -> date | None:
    target = WEEKDAYS.index(name.lower())
    if target == anchor.weekday():
        return None  # "on Friday" said on a Friday: today or a week away
    if past:
        return anchor - timedelta(days=(anchor.weekday() - target) % 7)
    return anchor + timedelta(days=(target - anchor.weekday()) % 7)


def _absolute(text: str, anchor: date) -> list[tuple[int, int, date | None]]:
    found = []
    for m in _ISO.finditer(text):
        found.append((m.start(), m.end(), _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    for m in _MDY.finditer(text):
        year = int(m.group(3)) if m.group(3) else anchor.year
        found.append((m.start(), m.end(), _safe(year, _MONTHS[m.group(1)], int(m.group(2)))))
    for m in _DMY.finditer(text):
        year = int(m.group(3)) if m.group(3) else anchor.year
        found.append((m.start(), m.end(), _safe(year, _MONTHS[m.group(2)], int(m.group(1)))))
    return found


def _safe(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _expressions(clause: str, anchor: date, past: bool) -> list[tuple[int, int, str, date | None, str]]:
    """Non-overlapping (start, end, kind, date, reason) spans; ambiguity wins over a contained relative form."""
    spans: list[tuple[int, int, str, date | None, str]] = []
    for reason, pattern in _AMBIGUOUS:
        for m in pattern.finditer(clause):
            # A bound on a calendar date ("arrive by May 5") keeps that date for arrival bookkeeping only.
            inner = [d for _, _, d in _absolute(m.group(0), anchor) if d]
            spans.append((m.start(), m.end(), "ambiguous", inner[0] if inner else None, reason))
    spans += [(s, e, "absolute", d, "" if d else "impossible date") for s, e, d in _absolute(clause, anchor)]
    for pattern, resolve in _RELATIVE:
        for m in pattern.finditer(clause):
            d = resolve(m, anchor, past)
            spans.append((m.start(), m.end(), "relative", d, "" if d else "weekday or hour count is not one date"))
    # "by this time in a fortnight": a date introduced by a bound word is a deadline, not one date.
    spans = [(s, e, "ambiguous", d, "bound") if k != "ambiguous" and _BOUND_BEFORE.search(clause[:s]) else (s, e, k, d, r)
             for s, e, k, d, r in spans]
    priority = {"ambiguous": 0, "absolute": 1, "relative": 2}
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0]), priority[s[2]]))
    chosen: list[tuple[int, int, str, date | None, str]] = []
    for span in sorted(spans, key=lambda s: (priority[s[2]], s[0], -(s[1] - s[0]))):
        if all(span[1] <= c[0] or span[0] >= c[1] for c in chosen):
            chosen.append(span)
    return sorted(chosen)


def _role(clause: str, sentence: str, start: int, offset: int) -> str:
    """Issue vs arrival vs other event, by the cue nearest before the expression (clause, then sentence)."""
    for text, at in ((clause, start), (sentence, offset + start)):
        cues = [(m.start(), "issue") for m in _ISSUE.finditer(text)]
        cues += [(m.start(), "arrival") for m in _ARRIVAL.finditer(text)]
        cues += [(m.start(), "other") for m in _OTHER_EVENT.finditer(text)]
        if not cues:
            continue
        before = [c for c in cues if c[0] < at]
        pick = max(before) if before else min(cues)
        return pick[1]
    return "unknown"


@lru_cache(maxsize=4096)
def analyze_issue_date(text: str) -> IssueDateAnalysis:
    mentions: list[Mention] = []
    messages = parse_messages(text)
    for index, msg in enumerate(messages):
        if msg.speaker.lower() == "customer":
            continue
        anchor = msg.when.date()
        previous_refund = False
        for sent in sentences(msg.text):
            refund_here = bool(_REFUND_CONTEXT.search(sent))
            if not (refund_here or (previous_refund and _PRONOUN_START.search(sent))):
                previous_refund = refund_here
                continue
            previous_refund = True
            position = 0
            for clause in _CLAUSE_SPLIT.split(sent):
                if not clause:
                    continue
                offset = sent.find(clause, position)
                position = max(position, offset)
                past = bool(_PAST.search(clause))
                found = _expressions(clause, anchor, past)
                for start, end, kind, resolved, reason in found:
                    role = _role(clause, sent, start, max(offset, 0))
                    mentions.append(Mention(index, msg.speaker, anchor.isoformat(), sent, clause[start:end], role,
                                            kind if resolved or kind == "ambiguous" else "ambiguous",
                                            resolved.isoformat() if resolved else None, reason))
                residue = clause
                removed = [(s, e) for s, e, *_ in found] + [m.span() for m in _WEEKDAY_BEFORE_DATE.finditer(clause)]
                for start, end in sorted(removed, reverse=True):
                    residue = residue[:start] + " " + residue[end:]
                hint = _TEMPORAL_HINT.search(residue)
                if hint and _role(clause, sent, hint.start(), max(offset, 0)) == "issue":
                    mentions.append(Mention(index, msg.speaker, anchor.isoformat(), sent, hint.group(0), "issue",
                                            "unrecognized", None, "temporal language the parser cannot resolve"))
    return _decide(tuple(mentions), messages)


def _decide(mentions: tuple[Mention, ...], messages) -> IssueDateAnalysis:
    issue = [m for m in mentions if m.role == "issue"]
    if not issue:
        return IssueDateAnalysis("none", None, "no store issue-date statement", False, mentions)
    last = max(m.message_index for m in issue)
    corrected = any(m.message_index != last for m in issue) and bool(_CORRECTION.search(messages[last].text))
    considered = [m for m in issue if m.message_index == last] if corrected else issue
    ambiguous = [m for m in considered if m.kind == "ambiguous"]
    if ambiguous:
        reasons = sorted({m.reason for m in ambiguous})
        return IssueDateAnalysis("review", None, "ambiguous issue date: " + ", ".join(reasons), True, mentions)
    unreadable = [m for m in considered if m.kind == "unrecognized"]
    if unreadable:
        words = sorted({m.expression.lower() for m in unreadable})
        return IssueDateAnalysis("review", None, "unrecognized issue-date language: " + ", ".join(words), False, mentions)
    dates = sorted({m.resolved for m in considered if m.resolved})
    if len(dates) == 1:
        how = "latest corrected store statement" if corrected else "store statement"
        return IssueDateAnalysis("resolved", dates[0], how, False, mentions)
    return IssueDateAnalysis("review", None, "conflicting issue dates without a correction: " + ", ".join(dates),
                             True, mentions)
