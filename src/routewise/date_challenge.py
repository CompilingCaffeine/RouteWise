"""M7 workloads: the relative-date challenge set and a fresh representative v4-style set.

Every label and disposition is fixed on the fact sheet before a writer model sees it. Review
families state an issue timing that does not determine one calendar date; no autonomous answer
is correct for them. The paraphrase family is the only one where the writer chooses the wording.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from random import Random

from routewise.factsheets import TONES, FactSheet, PlanMessage, _Ctx, _with_filler
from routewise.fresh_workload import fresh_sheets as base_sheets
from routewise.issue_workload import arrival_sheets, bind_issue_dates
from routewise.schema import Example
from routewise.synth import TIME_WORDS, verify
from routewise.textfacts import dates_in

CHALLENGE_FAMILIES = (
    "boundary_relative", "week_offset", "this_weekday", "day_after_tomorrow", "past_relative",
    "customer_restatement", "corrected_promise", "issue_and_arrival", "paraphrased_offset",
    "arrival_only_relative", "business_days", "range_or_bound", "next_or_vague",
)
REVIEW_FAMILIES = frozenset({"business_days", "range_or_bound", "next_or_vague"})
REVIEW_TAG = "disposition:review"
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven"]
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
BOUNDARY_ANCHORS = (
    date(2026, 1, 31), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30),
    date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30),
    date(2026, 12, 30), date(2026, 12, 31), date(2027, 2, 28), date(2028, 2, 27), date(2028, 2, 28),
)
ISSUE_GUIDANCE = (" Copy the planned issue-date statement exactly. It is when the refund is ISSUED, not when "
                  "money arrives; do not add any other date or time expression.")


def requires_review(example: Example) -> bool:
    return REVIEW_TAG in example.tags


def _n(rng: Random, n: int) -> str:
    return rng.choice([str(n), WORDS[n]])


def _issue(future: bool, phrase: str) -> str:
    return f"The refund {'will be' if future else 'was'} issued {phrase}"


def _opening(ctx: _Ctx) -> PlanMessage:
    ref = [f"order reference {ctx.order_id}"] if ctx.order_id else []
    hint = " and gives the order reference" if ctx.order_id else " (no order number is mentioned anywhere)"
    return PlanMessage("Customer", 0, f"says they sent back the {ctx.product}{hint}, and asks for a refund of "
                       "what they paid", [*ref, ctx.money(ctx.price)])


def _store(day: int, statement: str, ctx: _Ctx, extra: Sequence[str] = ()) -> PlanMessage:
    return PlanMessage("Agent", day, "confirms the return and the refund of the stated amount, and gives the "
                       "planned refund timing" + ISSUE_GUIDANCE, [ctx.money(ctx.price), statement, *extra])


def _build(family: str, ctx: _Ctx, rng: Random) -> tuple[date, list[PlanMessage], date | None, list[str], dict]:
    """Returns (contact, plan, refund issue date, allowed phrases, extra date allowances {day: [dates]})."""
    k = rng.choice([0, 1, 1, 2, 3])
    contact = ctx.contact
    extra: dict[int, list[date]] = {}
    if family == "boundary_relative":
        anchor = rng.choice(BOUNDARY_ANCHORS)
        k = rng.choice([0, 1, 2])
        contact = anchor - timedelta(days=k)
        n = rng.randint(1, 4)
        phrase = "tomorrow" if n == 1 else f"in {_n(rng, n)} days"
        return contact, [_opening(ctx), _store(k, _issue(True, phrase), ctx)], anchor + timedelta(days=n), [phrase], extra
    if family == "week_offset":
        phrase, weeks = rng.choice([("in a week", 1), ("in one week", 1), ("in two weeks", 2), ("in 2 weeks", 2),
                                    ("a week from today", 1), ("two weeks from today", 2)])
        return contact, [_opening(ctx), _store(k, _issue(True, phrase), ctx)], contact + timedelta(days=k + 7 * weeks), [phrase], extra
    if family == "this_weekday":
        anchor = contact + timedelta(days=k)
        while anchor.weekday() > 3:  # Monday-Thursday, so "this <day>" stays within the same week
            contact += timedelta(days=1)
            anchor = contact + timedelta(days=k)
        target = anchor + timedelta(days=rng.randint(1, 4 - anchor.weekday()))
        name = DAY_NAMES[target.weekday()]
        phrase = rng.choice([f"this {name}", f"on {name}"])
        return contact, [_opening(ctx), _store(k, _issue(True, phrase), ctx)], target, [phrase, name], extra
    if family == "day_after_tomorrow":
        phrase = "the day after tomorrow"
        return contact, [_opening(ctx), _store(k, _issue(True, phrase), ctx)], contact + timedelta(days=k + 2), [phrase], extra
    if family == "past_relative":
        phrase, back = rng.choice([("yesterday", 1), ("two days ago", 2), ("2 days ago", 2), ("three days ago", 3),
                                   ("earlier today", 0)])
        k = rng.choice([0, 1])
        ref = [f"order reference {ctx.order_id}"] if ctx.order_id else []
        ask = PlanMessage("Customer", 0, f"says they returned the {ctx.product}{' and gives the order reference' if ctx.order_id else ''}, "
                          "says they asked for a refund of what they paid (stating that amount), and asks whether the "
                          "refund has been issued yet", [*ref, ctx.money(ctx.price)])
        store = PlanMessage("Agent", k, "checks and confirms the refund of the stated amount has already been issued"
                            + ISSUE_GUIDANCE, [ctx.money(ctx.price), _issue(False, phrase)])
        return contact, [ask, store], contact + timedelta(days=k - back), [phrase], extra
    if family == "customer_restatement":
        n = rng.randint(2, 5)
        phrase = f"in {_n(rng, n)} days"
        k = rng.choice([0, 1])
        later = k + rng.choice([1, 2])
        plan = [_opening(ctx), _store(k, _issue(True, phrase), ctx),
                PlanMessage("Customer", later, "follows up, quoting what the agent said earlier: that the refund would be "
                            f"issued {phrase}. Asks whether that is still the plan", [phrase]),
                PlanMessage("Agent", later, "confirms the refund is still on schedule as previously promised, without "
                            "repeating any date, weekday or time expression")]
        return contact, plan, contact + timedelta(days=k + n), [phrase], extra
    if family == "corrected_promise":
        k = rng.choice([0, 1])
        first_n = rng.randint(1, 3)
        first = "tomorrow" if first_n == 1 else f"in {_n(rng, first_n)} days"
        later = k + rng.choice([0, 1])
        anchor = contact + timedelta(days=later)
        if rng.random() < 0.5:
            second_n = rng.choice([m for m in range(2, 7) if later + m != k + first_n])
            second = f"in {_n(rng, second_n)} days"
            corrected, phrases = anchor + timedelta(days=second_n), [first, second]
        else:
            corrected = anchor + timedelta(days=rng.choice([m for m in range(2, 8) if later + m != k + first_n]))
            second, phrases = f"on {ctx.date(corrected)}", [first]
        cue = rng.choice(["Correction:", "Update:", "I made a mistake earlier."])
        plan = [_opening(ctx), _store(k, _issue(True, first), ctx),
                PlanMessage("Agent", later, "sends a correction: the earlier refund issue date was wrong. Copy the cue "
                            "and the corrected statement exactly, and add no other date or time expression",
                            [cue, f"The refund will now be issued {second}"])]
        return contact, plan, corrected, phrases, extra
    if family == "issue_and_arrival":
        issue_phrase, offset = rng.choice([("today", 0), ("tomorrow", 1), ("in 2 days", 2), ("in two days", 2)])
        m = rng.randint(3, 7)
        arrival = rng.choice([f"It should reach your account in {_n(rng, m)} days",
                              f"The money should arrive in {_n(rng, m)} days",
                              f"It should appear on your statement in {_n(rng, m)} days",
                              f"The funds should show up in {_n(rng, m)} days"])
        phrase = arrival.split(" in ")[-1]
        plan = [_opening(ctx), _store(k, _issue(True, issue_phrase), ctx, [arrival])]
        return contact, plan, contact + timedelta(days=k + offset), [issue_phrase, f"in {phrase}"], extra
    if family == "paraphrased_offset":
        n, described = rng.choice([(2, "two days"), (3, "three days"), (7, "one week"), (14, "two weeks")])
        store = PlanMessage("Agent", k, "confirms the return and the refund of the stated amount, and says the refund "
                            f"will be issued exactly {described} after the date of this message. Express that timing "
                            f"in your own natural words as a relative time, not with the literal words 'in {described}', "
                            "and without any calendar date, weekday, range, deadline, approximation or business/working "
                            "days. Do not mention when money arrives. This message is the one exception to the rule "
                            "about time expressions: it must state that timing, in your own words",
                            [ctx.money(ctx.price), "will be issued"])
        extra[k] = [contact + timedelta(days=k), contact + timedelta(days=k + 1)]
        return contact, [_opening(ctx), store], contact + timedelta(days=k + n), [], extra
    if family == "arrival_only_relative":
        m = rng.randint(1, 6)
        when = "tomorrow" if m == 1 else f"in {_n(rng, m)} days"
        arrival = rng.choice([f"The refund should arrive in your account {when}", f"It should reach you {when}",
                              f"The money should show up {when}"])
        plan = [_opening(ctx), _store(k, "The refund issue date is not confirmed yet", ctx, [arrival])]
        plan[1].guidance = ("confirms the return and the refund of the stated amount, says when the money should "
                            "arrive, and says the issue date is not confirmed. Copy both statements exactly and add no "
                            "other date or time expression")
        return contact, plan, None, [when], extra
    if family == "business_days":
        n = rng.randint(2, 5)
        phrase = rng.choice([f"in {_n(rng, n)} business days", f"in {_n(rng, n)} working days",
                             f"within {_n(rng, n)} business days"])
        return contact, [_opening(ctx), _store(k, _issue(True, phrase), ctx)], None, [phrase], extra
    if family == "range_or_bound":
        a = rng.randint(2, 3)
        phrase = rng.choice([f"in {a}-{a + 2} days", f"in {a} to {a + 2} days", f"in {WORDS[a]} to {WORDS[a + 1]} days",
                             f"within {_n(rng, a + 1)} days", f"within the next {_n(rng, a + 2)} days"])
        return contact, [_opening(ctx), _store(k, _issue(True, phrase), ctx)], None, [phrase], extra
    if family == "next_or_vague":
        day = rng.choice(DAY_NAMES[:5])
        phrase = rng.choice([f"next {day}", "next week", "early next week", "by the end of the week", "later this week"])
        return contact, [_opening(ctx), _store(k, _issue(True, phrase), ctx)], None, [phrase], extra
    raise KeyError(family)


def challenge_sheets(seed: int, per_family: int | dict[str, int], prefix: str) -> list[FactSheet]:
    sheets = []
    for f_index, family in enumerate(CHALLENGE_FAMILIES):
        count = per_family[family] if isinstance(per_family, dict) else per_family
        rng = Random(f"{seed}:challenge:{family}")
        for i in range(count):
            ctx = _Ctx(rng, need_order=True)
            if rng.random() < 0.2:
                ctx.order_id = None
            contact, plan, refund, phrases, extra = _build(family, ctx, rng)
            ctx.contact = contact
            plan = _with_filler(rng, plan)
            allowed = {contact, *([refund] if refund else [])}
            for msg in plan:
                anchor = contact + timedelta(days=msg.day)
                for must in msg.must:
                    allowed |= dates_in(must, anchor)
                allowed |= set(extra.get(msg.day, [])) if "will be issued" in msg.must else set()
            start = datetime.combine(contact, datetime.min.time()) + timedelta(hours=rng.randint(7, 17), minutes=rng.randint(0, 59))
            expected = ctx.expected(True, ctx.price, refund)
            sheets.append(FactSheet(
                id=f"{prefix}-{f_index:02d}{i:03d}", family=family, writer=("super", "ultra")[i % 2],
                tone=rng.choice(TONES), typos=rng.random() < 0.2, contact=contact.isoformat(),
                start=start.isoformat(timespec="minutes"), messages=plan, expected=expected,
                allowed_amounts=sorted(set(ctx.amounts)), allowed_dates=sorted(d.isoformat() for d in allowed),
                allowed_phrases=sorted(set(phrases)), order_ids=[ctx.order_id] if ctx.order_id else [],
            ))
    return sheets


_PARAPHRASE_FORBIDDEN = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|business|working|within|next|couple|few|"
    r"several|end of|or so|up to|no later|at least|at most|until|before|soon|shortly|asap|approximately|roughly|"
    r"(?:about|around)\s+(?:a|an|\d+|one|two|three|seven|fourteen)|january|february|march|april|june|july|august|"
    r"september|october|november|december|\d+\s*(?:-|–|to)\s*\d+|arriv\w*|reach\w*|appear\w*)\b", re.IGNORECASE)
_TIMING = re.compile(TIME_WORDS.pattern + r"|\bfortnight\b|\bhours?\b|\bweeks?\b|\bdays?\b", re.IGNORECASE)


def challenge_verify(sheet: FactSheet, texts: Sequence[str]) -> list[str]:
    """synth.verify, except the paraphrase message may choose its own (unambiguous) timing words."""
    problems = verify(sheet, texts)
    if sheet.family != "paraphrased_offset" or len(texts) != len(sheet.messages):
        return problems
    index = next(i for i, m in enumerate(sheet.messages) if "will be issued" in m.must)
    text = texts[index]
    problems = [p for p in problems if not p.startswith(f"message {index + 1} uses a time expression")]
    if bad := _PARAPHRASE_FORBIDDEN.search(text):
        problems.append(f"paraphrase uses a forbidden or ambiguous form {bad.group(0)!r}")
    if not _TIMING.search(text):
        problems.append("paraphrase states no timing")
    days = (date.fromisoformat(sheet.expected["refund_date"]) - date.fromisoformat(sheet.contact)).days \
        - sheet.messages[index].day
    if re.search(rf"\bin\s+(?:{days}|{WORDS[days] if days < len(WORDS) else days})\s+days\b", text, re.IGNORECASE):
        problems.append("paraphrase copies the literal day count")
    return problems


def representative_sheets(seed: int, per_family: int, prefix: str) -> list[FactSheet]:
    """The 22 v4 scenario families at equal frequency, rebuilt from a new seed and identifier range."""
    sheets = base_sheets(seed=seed, per_family=per_family, version=prefix, id_base=1540000, new_id_base=1670000,
                         fixed_id_offset=40000)
    bind_issue_dates(sheets)
    start = date(2026, 6, 2) + timedelta(days=seed % 60)
    return sheets + arrival_sheets(prefix, start=start, id_base=1780000, amount_base=52, per_family=per_family)


def to_examples(records: Sequence[dict], review_families: frozenset[str] = REVIEW_FAMILIES) -> list[Example]:
    out = []
    for r in records:
        ex = Example(**r["example"])
        if ex.tags[0] in review_families:
            ex.tags.append(REVIEW_TAG)
        out.append(ex)
    return out
