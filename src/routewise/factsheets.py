"""Fact sheets for the v2 workload: everything true about a conversation, decided before it is written.

A fact sheet fixes the labels, the message plan (speaker, day, guidance, exact strings that must
appear) and what the text is *allowed* to contain (amounts, dates, time phrases, order IDs).
A writer model only turns the plan into natural messages; the verifier checks the result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from random import Random
from typing import Any

from routewise.dataset import DATE_STYLES, FX, NUMBER_WORDS, PARTS, PRODUCTS, fmt_date, fmt_money

FAMILIES = (
    "full_refund_absolute", "refund_no_date", "replacement_only", "partial_refund_distractor", "multi_date",
    "relative_refund_date", "system_message_first", "counteroffer", "shipping_fee_only", "currency_symbol",
    "no_amount_stated", "large_amount", "refund_in_past", "negated_refund", "multi_item_sum", "two_orders",
)
EASY_FAMILIES = {"full_refund_absolute", "refund_no_date", "replacement_only"}
TONES = ("neutral", "terse", "chatty", "frustrated", "formal")
WRITERS = ("super", "ultra")
SPLIT = {  # fixed by Random(31) before generation; see experiments/M3-failure-predictor.md
    "test": ("large_amount", "refund_in_past", "partial_refund_distractor", "relative_refund_date"),
    "calibration": ("multi_item_sum", "system_message_first"),
    "train": ("multi_date", "shipping_fee_only", "refund_no_date", "two_orders", "full_refund_absolute",
              "negated_refund", "replacement_only", "currency_symbol", "no_amount_stated", "counteroffer"),
}


@dataclass
class PlanMessage:
    speaker: str  # Customer | Agent | System
    day: int  # offset from the contact day
    guidance: str
    must: list[str] = field(default_factory=list)


@dataclass
class FactSheet:
    id: str
    family: str
    writer: str
    tone: str
    typos: bool
    contact: str  # ISO date of the first customer message
    start: str  # ISO datetime of the first message
    messages: list[PlanMessage]
    expected: dict[str, Any]
    allowed_amounts: list[tuple[float, str]]
    allowed_dates: list[str]
    allowed_phrases: list[str]
    order_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FactSheet:
        d = dict(d)
        d["messages"] = [PlanMessage(**m) for m in d["messages"]]
        d["allowed_amounts"] = [tuple(a) for a in d["allowed_amounts"]]
        return cls(**d)

    @property
    def split(self) -> str:
        return next(name for name, fams in SPLIT.items() if self.family in fams)

    @property
    def difficulty(self) -> str:
        return "easy" if self.family in EASY_FAMILIES else "adversarial"


class _Ctx:
    def __init__(self, rng: Random, *, currency: str | None = None, min_usd: float = 19, max_usd: float = 450,
                 money_style: str | None = None, need_order: bool = False) -> None:
        self.rng = rng
        self.currency = currency or rng.choices(list(FX), weights=[10, 3, 3, 1, 1, 1, 1])[0]
        self.money_style = money_style or rng.choice(["symbol", "symbol", "code_after", "code_before"])
        self.date_style = rng.choice(DATE_STYLES)
        self.product = rng.choice(PRODUCTS)
        self.part = rng.choice(PARTS)
        self.order_id = f"ORD-{rng.randint(10000, 99999)}" if need_order or rng.random() < 0.85 else None
        self.contact = date(2026, 1, 20) + timedelta(days=rng.randint(0, 240))
        self.price = self.amount(rng.uniform(min_usd, max_usd))
        self.amounts: list[tuple[float, str]] = []
        self.dates: list[date] = [self.contact]
        self.phrases: list[str] = []

    def amount(self, usd: float) -> float:
        v = usd * FX[self.currency]
        return float(round(v, -2)) if self.currency == "JPY" else round(int(v) + 0.99, 2)

    def partial(self) -> float:
        v = self.price * self.rng.choice([0.2, 0.25, 0.3, 0.4, 0.5])
        return float(round(v, -2)) if self.currency == "JPY" else round(round(v * 2) / 2, 2)

    def money(self, amount: float) -> str:
        self.amounts.append((amount, self.currency))
        return fmt_money(amount, self.currency, self.money_style)

    def date(self, d: date) -> str:
        self.dates.append(d)
        return fmt_date(d, self.date_style)

    def allow(self, *phrases: str) -> None:
        self.phrases.extend(phrases)

    def order_ref(self) -> list[str]:
        return [self.order_id] if self.order_id else []

    def expected(self, requested: bool, amount: float | None, refund: date | None, order_id: str | None = None) -> dict:
        return {
            "refund_requested": requested,
            "refund_amount": amount,
            "currency": self.currency if amount is not None else None,
            "order_id": order_id if order_id is not None else self.order_id,
            "contact_date": self.contact.isoformat(),
            "refund_date": refund.isoformat() if refund else None,
        }


def _order_hint(ctx: _Ctx) -> str:
    return f" and mentions their order number {ctx.order_id}" if ctx.order_id else " (no order number is mentioned anywhere)"


# Each family returns (plan, expected). `d` is a day offset; dates are relative to ctx.contact.

def _full_refund_absolute(ctx: _Ctx):
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 7))
    ctx.allow("after a week", "a week", "one week")  # a duration, never a date
    plan = [PlanMessage("Customer", 0, f"says their {ctx.product} stopped working after a week{_order_hint(ctx)}, and asks for a full refund", ctx.order_ref()),
            PlanMessage("Agent", 0, "apologizes, approves a full refund of the stated amount and says on which date the refund will be issued",
                        [ctx.money(ctx.price), ctx.date(rd)])]
    return plan, ctx.expected(True, ctx.price, rd)


def _refund_no_date(ctx: _Ctx):
    plan = [PlanMessage("Customer", 0, f"says they returned the {ctx.product}{_order_hint(ctx)}, asks to get their money back and says what they paid",
                        [*ctx.order_ref(), ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, "approves a refund of that amount and says a confirmation email will follow once it is processed, without giving any date",
                        [ctx.money(ctx.price)])]
    return plan, ctx.expected(True, ctx.price, None)


def _replacement_only(ctx: _Ctx):
    ship = ctx.rng.choice(["today", "tomorrow"])
    ctx.allow(ship)
    ctx.dates.append(ctx.contact + timedelta(days=1 if ship == "tomorrow" else 0))
    plan = [PlanMessage("Customer", 0, f"says the {ctx.product} arrived with a broken {ctx.part}{_order_hint(ctx)} and asks for a replacement (they do not ask for money back)", ctx.order_ref()),
            PlanMessage("Agent", 0, f"confirms a replacement will ship {ship} and tracking will follow by email", [ship])]
    return plan, ctx.expected(False, None, None)


def _partial_refund_distractor(ctx: _Ctx):
    ordered = ctx.contact - timedelta(days=ctx.rng.randint(5, 20))
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 5))
    q = ctx.partial()
    plan = [PlanMessage("Customer", 0, f"says they bought the {ctx.product}{_order_hint(ctx)} on a given date for a given price, it arrived with a damaged {ctx.part}, and they want some money back for that",
                        [*ctx.order_ref(), ctx.date(ordered), ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, f"offers either a partial refund of the given amount for the damaged {ctx.part} or a full replacement", [ctx.money(q)]),
            PlanMessage("Customer", 0, "accepts the partial refund"),
            PlanMessage("Agent", 0, "confirms the partial refund of that amount will be issued on the given date", [ctx.money(q), ctx.date(rd)])]
    return plan, ctx.expected(True, q, rd)


def _multi_date(ctx: _Ctx):
    ordered = ctx.contact - timedelta(days=ctx.rng.randint(10, 20))
    delivered = ordered + timedelta(days=ctx.rng.randint(2, 6))
    rd = ctx.contact + timedelta(days=ctx.rng.randint(2, 6))
    plan = [PlanMessage("Customer", 0, f"says they ordered the {ctx.product}{_order_hint(ctx)} on one date, it was delivered on another, it doesn't work, and asks for a refund of what they paid",
                        [*ctx.order_ref(), ctx.date(ordered), ctx.date(delivered), ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, "confirms the return and says the refund of that amount will be processed on the given date", [ctx.money(ctx.price), ctx.date(rd)])]
    return plan, ctx.expected(True, ctx.price, rd)


def _relative_refund_date(ctx: _Ctx):
    n = ctx.rng.randint(1, 5)
    rel = "tomorrow" if n == 1 else f"in {ctx.rng.choice([str(n), NUMBER_WORDS[n]])} days"
    ctx.allow(rel)
    rd = ctx.contact + timedelta(days=1 + n)
    ctx.dates += [ctx.contact + timedelta(days=1), rd]
    plan = [PlanMessage("Customer", 0, f"says they sent back the {ctx.product}{_order_hint(ctx)} and asks for a refund of what they paid", [*ctx.order_ref(), ctx.money(ctx.price)]),
            PlanMessage("Agent", 1, f"(written the next day) says the return was received and the refund will be issued {rel}", [rel])]
    return plan, ctx.expected(True, ctx.price, rd)


def _system_message_first(ctx: _Ctx):
    ctx.order_id = ctx.order_id or f"ORD-{ctx.rng.randint(10000, 99999)}"
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 6))
    ctx.dates.append(ctx.contact - timedelta(days=1))
    plan = [PlanMessage("System", -1, "an automated notice that the order was delivered (one short sentence, no greeting)", [ctx.order_id]),
            PlanMessage("Customer", 0, f"says the {ctx.product} they received is missing parts and asks for a refund of what they paid", [ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, "apologizes and says the refund of that amount will be issued on the given date", [ctx.money(ctx.price), ctx.date(rd)])]
    return plan, ctx.expected(True, ctx.price, rd)


def _counteroffer(ctx: _Ctx):
    q = ctx.partial()
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 5))
    plan = [PlanMessage("Customer", 0, f"says the {ctx.product}{_order_hint(ctx)} isn't what they expected and asks for a refund of the full price", [*ctx.order_ref(), ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, "explains the item was used, so they can refund only the given smaller amount instead of the full price", [ctx.money(q)]),
            PlanMessage("Customer", 0, "reluctantly accepts the smaller amount"),
            PlanMessage("Agent", 0, "confirms the refund of the smaller amount will be issued on the given date", [ctx.money(q), ctx.date(rd)])]
    return plan, ctx.expected(True, q, rd)


def _shipping_fee_only(ctx: _Ctx):
    fee_local = ctx.rng.choice([4.99, 7.5, 9.99, 12.5, 14.95]) * FX[ctx.currency]
    fee = float(max(100, round(fee_local, -2))) if ctx.currency == "JPY" else round(fee_local, 2)
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 5))
    ctx.allow("two weeks late")
    plan = [PlanMessage("Customer", 0, f"says the {ctx.product}{_order_hint(ctx)} cost the given price and arrived two weeks late, and asks at least for the shipping fee back",
                        [*ctx.order_ref(), ctx.money(ctx.price), "two weeks late"]),
            PlanMessage("Agent", 0, "agrees the delay was their fault and says they will refund the given shipping fee, issued on the given date", [ctx.money(fee), ctx.date(rd)])]
    return plan, ctx.expected(True, fee, rd)


def _currency_symbol(ctx: _Ctx):
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 7))
    plan = [PlanMessage("Customer", 0, f"says the {ctx.product}{_order_hint(ctx)} they bought for the given price is faulty and asks for their money back", [*ctx.order_ref(), ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, "approves a refund of that amount and says the refund will be issued on the given date", [ctx.money(ctx.price), ctx.date(rd)])]
    return plan, ctx.expected(True, ctx.price, rd)


def _no_amount_stated(ctx: _Ctx):
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 6))
    plan = [PlanMessage("Customer", 0, f"asks to cancel and refund their order{_order_hint(ctx)} because they changed their mind about the {ctx.product}; they do not state any price", ctx.order_ref()),
            PlanMessage("Agent", 0, "says a refund for the full order amount has been submitted (without stating any amount) and will be processed on the given date", [ctx.date(rd)])]
    return plan, ctx.expected(True, None, rd)


def _large_amount(ctx: _Ctx):
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 7))
    plan = [PlanMessage("Customer", 0, f"says the {ctx.product}{_order_hint(ctx)} was not as described and asks for a full refund", ctx.order_ref()),
            PlanMessage("Agent", 0, "approves a full refund of the given amount and says the refund will be processed on the given date", [ctx.money(ctx.price), ctx.date(rd)])]
    return plan, ctx.expected(True, ctx.price, rd)


def _refund_in_past(ctx: _Ctx):
    issued = ctx.contact - timedelta(days=ctx.rng.randint(2, 6))
    ctx.allow("last week", "3-5 business days")
    target = f"order {ctx.order_id}" if ctx.order_id else f"the {ctx.product}"
    plan = [PlanMessage("Customer", 0, f"says they requested a refund for {target} last week and it still hasn't arrived", [*ctx.order_ref(), "last week"]),
            PlanMessage("Agent", 0, "checks and says the refund of the given amount was already issued on the given date and can take 3-5 business days to appear",
                        [ctx.money(ctx.price), ctx.date(issued), "3-5 business days"])]
    return plan, ctx.expected(True, ctx.price, issued)


def _negated_refund(ctx: _Ctx):
    ctx.allow("today", "within two weeks")
    ctx.dates.append(ctx.contact)
    plan = [PlanMessage("Customer", 0, f"says the {ctx.product}{_order_hint(ctx)} they bought for the given price has a faulty {ctx.part}; they explicitly do NOT want their money back, they want it repaired",
                        [*ctx.order_ref(), ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, "agrees to repair it: a prepaid label will be emailed today and the repair done within two weeks", ["today", "within two weeks"])]
    return plan, ctx.expected(False, None, None)


def _multi_item_sum(ctx: _Ctx):
    ctx.order_id = ctx.order_id or f"ORD-{ctx.rng.randint(10000, 99999)}"
    p1 = ctx.price
    p2 = ctx.amount(ctx.rng.uniform(5, 40))
    total = round(p1 + p2, 2)
    second = ctx.rng.choice(["cable", "case", "filter pack", "bulb pack", "strap"])
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 5))
    plan = [PlanMessage("Customer", 0, f"says two items from their order arrived broken: the {ctx.product} and a {second}, each with its price, and asks for a refund of both",
                        [ctx.order_id, ctx.money(p1), ctx.money(p2)]),
            PlanMessage("Agent", 0, "says one refund of the combined total for both items will be issued on the given date", [ctx.money(total), ctx.date(rd)])]
    return plan, ctx.expected(True, total, rd)


def _two_orders(ctx: _Ctx):
    oid1, oid2 = (f"ORD-{ctx.rng.randint(10000, 99999)}" for _ in range(2))
    other = ctx.rng.choice([p for p in PRODUCTS if p != ctx.product])
    p_other = ctx.amount(ctx.rng.uniform(19, 300))
    ctx.order_id = oid2
    rd = ctx.contact + timedelta(days=ctx.rng.randint(1, 5))
    plan = [PlanMessage("Customer", 0, f"says they have two open orders, {oid1} (the {other}, with its price) and {oid2} (the {ctx.product}, with its price); the {ctx.product} is faulty and they want their money back for that one only",
                        [oid1, ctx.money(p_other), oid2, ctx.money(ctx.price)]),
            PlanMessage("Agent", 0, f"says a refund of the {ctx.product}'s price has been started for order {oid2} and will be issued on the given date",
                        [ctx.money(ctx.price), oid2, ctx.date(rd)])]
    return plan, ctx.expected(True, ctx.price, rd, order_id=oid2)


_BUILDERS = {name: globals()[f"_{name}"] for name in FAMILIES}


def _context(rng: Random, family: str) -> _Ctx:
    if family == "currency_symbol":
        return _Ctx(rng, currency=rng.choice(["EUR", "GBP", "JPY", "CAD", "AUD"]), money_style="symbol")
    if family == "large_amount":
        return _Ctx(rng, currency=rng.choice(["USD", "EUR", "GBP"]), min_usd=1100, max_usd=2600)
    return _Ctx(rng, need_order=family in {"system_message_first", "multi_item_sum"})


def _with_filler(rng: Random, plan: list[PlanMessage]) -> list[PlanMessage]:
    """Optionally add an agent acknowledgement and/or a closing customer thank-you (no facts)."""
    plan = list(plan)
    first_customer = next(i for i, m in enumerate(plan) if m.speaker == "Customer")
    if rng.random() < 0.35 and len(plan) > first_customer + 1:
        day = plan[first_customer].day
        plan.insert(first_customer + 1, PlanMessage("Agent", day, "acknowledges the message and says they are looking into it (no facts, numbers or dates)"))
    if rng.random() < 0.5:
        plan.append(PlanMessage("Customer", plan[-1].day, "thanks the agent briefly (no facts, numbers or dates)"))
    return plan


def sample_factsheets(per_family: int = 75, seed: int = 20261026) -> list[FactSheet]:
    sheets = []
    for f_index, family in enumerate(FAMILIES):
        rng = Random(seed * 100 + f_index)
        for i in range(per_family):
            ctx = _context(rng, family)
            plan, expected = _BUILDERS[family](ctx)
            plan = _with_filler(rng, plan)
            start = datetime.combine(ctx.contact, datetime.min.time()) + timedelta(hours=rng.randint(7, 19), minutes=rng.randint(0, 59))
            if plan[0].speaker == "System":
                start -= timedelta(days=1)
            sheets.append(FactSheet(
                id=f"v2-{f_index:02d}{i:03d}",
                family=family,
                writer=WRITERS[i % 2],
                tone=rng.choice(TONES),
                typos=rng.random() < 0.3,
                contact=ctx.contact.isoformat(),
                start=start.isoformat(timespec="minutes"),
                messages=plan,
                expected=expected,
                allowed_amounts=sorted(set(ctx.amounts)),
                allowed_dates=sorted({d.isoformat() for d in ctx.dates}),
                allowed_phrases=sorted(set(ctx.phrases)),
                order_ids=sorted({oid for oid in [ctx.order_id, *[s for m in plan for s in m.must if s.startswith("ORD-")]] if oid}),
            ))
    return sheets
