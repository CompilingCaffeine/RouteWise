"""Seeded, template-based workload generator plus loaders.

Ground truth is correct by construction: each template computes the expected answer from
the same values it renders into the conversation. Hand-written edge cases live in
data/handwritten/ and are merged in unchanged. No LLM is used to create labels.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

from routewise.config import REPO_ROOT
from routewise.schema import Example

VERSION = "v1"
SEED = 20261025
DATA_DIR = REPO_ROOT / "data"
HANDWRITTEN = DATA_DIR / "handwritten" / "extraction.jsonl"

PRODUCTS = [
    "TrailMax 2 tent", "AeroPress coffee maker", "Nimbus running shoes", "Lumen desk lamp",
    "Orbit wireless earbuds", "Cascade water filter", "Atlas hiking backpack", "Pixel smart thermostat",
    "Harbor wool blanket", "Vertex mechanical keyboard", "Solace noise-cancelling headphones",
    "Ember cast-iron skillet", "Drift stand-up paddleboard", "Quill fountain pen set", "Halo baby monitor",
]
PARTS = ["lid", "strap", "base", "handle", "zipper", "cable", "seam", "hinge", "panel"]
FX = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "CAD": 1.37, "AUD": 1.52, "CHF": 0.88, "JPY": 150.0}
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "CA$", "AUD": "A$"}
DATE_STYLES = ["long", "long_noyear", "dmy", "short", "iso"]
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven"]


# ---------------------------------------------------------------- formatting


def fmt_money(amount: float, currency: str, style: str) -> str:
    num = f"{round(amount):,}" if currency == "JPY" else f"{amount:,.2f}"
    if style == "symbol" and currency in SYMBOLS:
        return f"{SYMBOLS[currency]}{num}"
    if style == "code_before" or currency not in SYMBOLS:
        return f"{currency} {num}"
    return f"{num} {currency}"


def fmt_date(d: date, style: str) -> str:
    match style:
        case "long":
            return f"{d:%B} {d.day}, {d.year}"
        case "long_noyear":
            return f"{d:%B} {d.day}"
        case "dmy":
            return f"{d.day} {d:%B} {d.year}"
        case "short":
            return f"{d:%b} {d.day}"
        case "iso":
            return d.isoformat()
    raise ValueError(style)


@dataclass
class Convo:
    lines: list[str] = field(default_factory=list)

    def say(self, when: datetime, speaker: str, text: str) -> None:
        self.lines.append(f"[{when:%Y-%m-%d %H:%M}] {speaker}: {text}")

    def render(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Ctx:
    """Random values shared by one example's template."""

    rng: Random
    product: str
    part: str
    currency: str
    price: float
    order_id: str | None
    start: datetime
    money_style: str
    date_style: str

    def money(self, amount: float) -> str:
        return fmt_money(amount, self.currency, self.money_style)

    def date(self, d: date) -> str:
        return fmt_date(d, self.date_style)

    def order_clause(self) -> str:
        if not self.order_id:
            return ""
        return self.rng.choice([f" (order #{self.order_id})", f" (order {self.order_id})", f" (order number {self.order_id})"])

    def later(self, when: datetime, lo: int = 8, hi: int = 95) -> datetime:
        return when + timedelta(minutes=self.rng.randint(lo, hi))

    def convert(self, usd: float) -> float:
        value = usd * FX[self.currency]
        return float(round(value, -2)) if self.currency == "JPY" else round(value, 2)

    def partial(self) -> float:
        """A partial refund clearly different from the price."""
        share = self.rng.choice([0.2, 0.25, 0.3, 0.4, 0.5])
        value = self.price * share
        return float(round(value, -2)) if self.currency == "JPY" else round(round(value * 2) / 2, 2)

    def expected(self, requested: bool, amount: float | None, refund_date: date | None, contact: date | None = None) -> dict[str, Any]:
        return {
            "refund_requested": requested,
            "refund_amount": amount,
            "currency": self.currency if amount is not None else None,
            "order_id": self.order_id,
            "contact_date": (contact or self.start.date()).isoformat(),
            "refund_date": refund_date.isoformat() if refund_date else None,
        }


def _ctx(rng: Random, *, currency: str | None = None, min_usd: float = 19.0, max_usd: float = 450.0,
         money_style: str | None = None, date_style: str | None = None, need_order: bool = False) -> Ctx:
    cur = currency or rng.choices(list(FX), weights=[10, 3, 3, 1, 1, 1, 1])[0]
    usd = rng.uniform(min_usd, max_usd)
    ctx = Ctx(
        rng=rng,
        product=rng.choice(PRODUCTS),
        part=rng.choice(PARTS),
        currency=cur,
        price=0.0,
        order_id=f"ORD-{rng.randint(10000, 99999)}" if need_order or rng.random() < 0.85 else None,
        start=datetime(2026, 1, 15, 8) + timedelta(days=rng.randint(0, 250), minutes=rng.randint(0, 660)),
        money_style=money_style or rng.choice(["symbol", "symbol", "code_after", "code_before"]),
        date_style=date_style or rng.choice(DATE_STYLES),
    )
    ctx.price = float(round(usd * FX[cur], -2)) if cur == "JPY" else round(int(usd * FX[cur]) + 0.99, 2)
    return ctx


def _refund_date_sentence(ctx: Ctx, d: date) -> str:
    return ctx.rng.choice([
        f"The refund will be issued on {ctx.date(d)}.",
        f"Your refund will be processed on {ctx.date(d)}.",
        f"We will send the refund on {ctx.date(d)}.",
    ])


# ---------------------------------------------------------------- extraction templates
# Each returns (conversation text, expected, tags).

Template = Callable[[Random], tuple[str, dict[str, Any], list[str]]]


def full_refund_absolute(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    rd = t0.date() + timedelta(days=rng.randint(1, 7))
    c.say(t0, "Customer", f"Hi, my {ctx.product}{ctx.order_clause()} stopped working after a week. I'd like a full refund please.")
    c.say(ctx.later(t0), "Agent", f"I'm sorry to hear that. I've approved a full refund of {ctx.money(ctx.price)}. {_refund_date_sentence(ctx, rd)}")
    return c.render(), ctx.expected(True, ctx.price, rd), ["full_refund"]


def refund_no_date(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    c.say(t0, "Customer", f"I returned the {ctx.product}{ctx.order_clause()} last week but haven't heard anything. Can I get my money back? It was {ctx.money(ctx.price)}.")
    c.say(ctx.later(t0), "Agent", f"Thanks for your patience. I've approved a refund of {ctx.money(ctx.price)}. You'll receive a confirmation email once it has been processed.")
    return c.render(), ctx.expected(True, ctx.price, None), ["no_refund_date"]


def replacement_only(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    c.say(t0, "Customer", f"My {ctx.product}{ctx.order_clause()} arrived with a broken {ctx.part}. Could you send me a replacement?")
    c.say(ctx.later(t0), "Agent", f"Absolutely, a replacement will ship {rng.choice(['today', 'tomorrow'])} and you'll get tracking by email.")
    return c.render(), ctx.expected(False, None, None), ["no_refund"]


def partial_refund_distractor(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    ordered = t0.date() - timedelta(days=rng.randint(5, 20))
    rd = t0.date() + timedelta(days=rng.randint(1, 5))
    q = ctx.partial()
    c.say(t0, "Customer", f"Hi, I bought the {ctx.product}{ctx.order_clause()} on {ctx.date(ordered)} for {ctx.money(ctx.price)}. It arrived with a damaged {ctx.part}. I'd like some money back for that.")
    t1 = ctx.later(t0)
    c.say(t1, "Agent", f"Sorry about that! I can offer a partial refund of {ctx.money(q)} for the damaged {ctx.part}, or a full replacement.")
    t2 = ctx.later(t1, 3, 30)
    c.say(t2, "Customer", "The partial refund works, thanks.")
    c.say(ctx.later(t2, 3, 30), "Agent", f"Done. The {ctx.money(q)} refund will be issued on {ctx.date(rd)}, and should reach your card within 3-5 business days after that.")
    return c.render(), ctx.expected(True, q, rd), ["distractor_amount", "distractor_date", "partial_refund"]


def multi_date(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    ordered = t0.date() - timedelta(days=rng.randint(10, 20))
    delivered = ordered + timedelta(days=rng.randint(2, 6))
    rd = t0.date() + timedelta(days=rng.randint(2, 6))
    c.say(t0, "Customer", f"I ordered the {ctx.product}{ctx.order_clause()} on {ctx.date(ordered)} and it was delivered on {ctx.date(delivered)}, but it doesn't work. Please refund the {ctx.money(ctx.price)} I paid.")
    c.say(ctx.later(t0), "Agent", f"I've confirmed the return. Your refund of {ctx.money(ctx.price)} will be processed on {ctx.date(rd)}.")
    return c.render(), ctx.expected(True, ctx.price, rd), ["distractor_date"]


def relative_refund_date(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    t1 = datetime.combine(t0.date() + timedelta(days=1), datetime.min.time()) + timedelta(hours=rng.randint(8, 16), minutes=rng.randint(0, 59))
    n = rng.randint(1, 5)
    rel = "tomorrow" if n == 1 else f"in {rng.choice([str(n), NUMBER_WORDS[n]])} days"
    c.say(t0, "Customer", f"Hello, I sent back the {ctx.product}{ctx.order_clause()}. Please refund the {ctx.money(ctx.price)} I paid.")
    c.say(t1, "Agent", f"Thanks for waiting. Your return was received and your refund will be issued {rel}.")
    return c.render(), ctx.expected(True, ctx.price, t1.date() + timedelta(days=n)), ["relative_date", "multi_day"]


def system_message_first(rng: Random):
    ctx = _ctx(rng, need_order=True)
    c, t0 = Convo(), ctx.start
    rd = t0.date() + timedelta(days=rng.randint(1, 6))
    c.say(t0 - timedelta(days=1, minutes=rng.randint(0, 300)), "System", f"Order {ctx.order_id} was delivered.")
    c.say(t0, "Customer", f"The {ctx.product} I received is missing parts. I want a refund of {ctx.money(ctx.price)}.")
    c.say(ctx.later(t0), "Agent", f"Sorry about that. The refund of {ctx.money(ctx.price)} will be issued on {ctx.date(rd)}.")
    return c.render(), ctx.expected(True, ctx.price, rd), ["system_message", "distractor_date"]


def counteroffer(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    q = ctx.partial()
    rd = t0.date() + timedelta(days=rng.randint(1, 5))
    c.say(t0, "Customer", f"The {ctx.product}{ctx.order_clause()} isn't what I expected. Please refund the full {ctx.money(ctx.price)}.")
    t1 = ctx.later(t0)
    c.say(t1, "Agent", f"Since the item was used, we can refund {ctx.money(q)} instead of the full amount.")
    t2 = ctx.later(t1, 3, 40)
    c.say(t2, "Customer", "Fine, I accept that.")
    c.say(ctx.later(t2, 3, 30), "Agent", f"Great, the refund of {ctx.money(q)} will be issued on {ctx.date(rd)}.")
    return c.render(), ctx.expected(True, q, rd), ["distractor_amount", "counteroffer"]


def shipping_fee_only(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    fee = ctx.convert(rng.choice([4.99, 7.5, 9.99, 12.5, 14.95]))
    rd = t0.date() + timedelta(days=rng.randint(1, 5))
    c.say(t0, "Customer", f"My {ctx.product}{ctx.order_clause()} cost {ctx.money(ctx.price)} and arrived two weeks late. At least refund the shipping fee.")
    c.say(ctx.later(t0), "Agent", f"You're right, that delay was on us. We'll refund the {ctx.money(fee)} shipping fee, and it will be issued on {ctx.date(rd)}.")
    return c.render(), ctx.expected(True, fee, rd), ["distractor_amount", "fee_only"]


def currency_symbol(rng: Random):
    ctx = _ctx(rng, currency=rng.choice(["EUR", "GBP", "JPY", "CAD", "AUD"]), money_style="symbol")
    c, t0 = Convo(), ctx.start
    rd = t0.date() + timedelta(days=rng.randint(1, 7))
    c.say(t0, "Customer", f"Hello, the {ctx.product}{ctx.order_clause()} I bought for {ctx.money(ctx.price)} is faulty. Please give me my money back.")
    c.say(ctx.later(t0), "Agent", f"Apologies! A refund of {ctx.money(ctx.price)} has been approved. {_refund_date_sentence(ctx, rd)}")
    return c.render(), ctx.expected(True, ctx.price, rd), ["currency_symbol"]


def no_amount_stated(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    rd = t0.date() + timedelta(days=rng.randint(1, 6))
    target = f"order {ctx.order_id}" if ctx.order_id else "my order"
    c.say(t0, "Customer", f"Please cancel and refund {target}, I changed my mind about the {ctx.product}.")
    c.say(ctx.later(t0), "Agent", f"No problem. I've submitted a refund for the full order amount, and it will be processed on {ctx.date(rd)}.")
    return c.render(), ctx.expected(True, None, rd), ["amount_not_stated"]


def large_amount(rng: Random):
    ctx = _ctx(rng, currency=rng.choice(["USD", "EUR", "GBP"]), min_usd=1100, max_usd=2600, date_style="dmy")
    c, t0 = Convo(), ctx.start
    rd = t0.date() + timedelta(days=rng.randint(1, 7))
    c.say(t0, "Customer", f"The {ctx.product}{ctx.order_clause()} was not as described and I'd like a full refund.")
    c.say(ctx.later(t0), "Agent", f"Understood. A full refund of {ctx.money(ctx.price)} has been approved. {_refund_date_sentence(ctx, rd)}")
    return c.render(), ctx.expected(True, ctx.price, rd), ["large_amount"]


def refund_in_past(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    issued = t0.date() - timedelta(days=rng.randint(2, 6))
    target = f"order {ctx.order_id}" if ctx.order_id else f"the {ctx.product}"
    c.say(t0, "Customer", f"I requested a refund for {target} last week and it still hasn't arrived.")
    c.say(ctx.later(t0), "Agent", f"I checked your account: the refund of {ctx.money(ctx.price)} was issued on {ctx.date(issued)}. It can take 3-5 business days to appear on your statement.")
    return c.render(), ctx.expected(True, ctx.price, issued), ["past_refund_date"]


def negated_refund(rng: Random):
    ctx = _ctx(rng)
    c, t0 = Convo(), ctx.start
    c.say(t0, "Customer", f"The {ctx.product}{ctx.order_clause()} I bought for {ctx.money(ctx.price)} has a faulty {ctx.part}. I don't want my money back, I'd just like it repaired.")
    c.say(ctx.later(t0), "Agent", "Understood. We'll email a prepaid label today and repair it within two weeks.")
    return c.render(), ctx.expected(False, None, None), ["negated_refund", "distractor_amount"]


EXTRACTION_PLAN: list[tuple[Template, str, int]] = [
    (full_refund_absolute, "easy", 9),
    (refund_no_date, "easy", 8),
    (replacement_only, "easy", 7),
    (partial_refund_distractor, "adversarial", 6),
    (multi_date, "adversarial", 4),
    (relative_refund_date, "adversarial", 6),
    (system_message_first, "adversarial", 4),
    (counteroffer, "adversarial", 5),
    (shipping_fee_only, "adversarial", 4),
    (currency_symbol, "adversarial", 5),
    (no_amount_stated, "adversarial", 3),
    (large_amount, "adversarial", 3),
    (refund_in_past, "adversarial", 3),
    (negated_refund, "adversarial", 3),
]


# ---------------------------------------------------------------- classification templates

CLASSIFICATION_PLAN: dict[str, list[tuple[str, str]]] = {
    "refund_request": [
        ("easy", "I returned the {product} two weeks ago. Please refund my money."),
        ("easy", "I'd like a refund for order {oid}. The {product} isn't what I expected."),
        ("easy", "Can I get my money back for the {product}? I never used it."),
        ("adversarial", "The {product} arrived with a shattered {part}. I don't want a replacement, I just want my money back."),
        ("adversarial", "My {product} showed up three weeks late and I no longer need it. Please refund the {amount} I paid."),
        ("adversarial", "I was charged {amount} for the {product}, which was the right price, but it stopped working after two days. Refund me please."),
    ],
    "shipping_delay": [
        ("easy", "Order {oid} was supposed to arrive last Monday and it still hasn't. Where is it?"),
        ("easy", "My {product} hasn't been delivered yet. Can you give me a status update?"),
        ("easy", "Tracking for order {oid} hasn't updated in 8 days. When will it arrive?"),
        ("adversarial", "I paid {amount} extra for express shipping and the {product} still isn't here after 10 days. When will it arrive?"),
        ("adversarial", "I don't want to cancel or get a refund, I just need to know when my {product} will finally be delivered."),
        ("adversarial", "My account shows order {oid} as shipped, but nothing has arrived. Can you check where the package is?"),
    ],
    "damaged_item": [
        ("easy", "The {product} arrived with a cracked {part}. Can you send a replacement?"),
        ("easy", "My {product} is defective and won't turn on. Please repair or replace it."),
        ("easy", "You sent me the wrong color {product}. Please exchange it for the black one."),
        ("adversarial", "I paid {amount} for the {product} and it arrived broken. I don't want a refund, just send me a working one."),
        ("adversarial", "The box for order {oid} was crushed and the {product} inside is dented. I'd like a replacement shipped as soon as possible."),
        ("adversarial", "My {product} arrived two days late and the {part} is broken. Please replace the broken {part}."),
    ],
    "billing_error": [
        ("easy", "I was charged twice for order {oid}. Please fix this."),
        ("easy", "There's a charge of {amount} on my card that I don't recognize. What is it?"),
        ("easy", "My invoice shows the wrong amount for the {product}. Please correct it."),
        ("adversarial", "My card shows two charges of {amount} for the same order. Please refund the duplicate charge."),
        ("adversarial", "I was charged {amount2} but the price at checkout was {amount}. Please reverse the difference."),
        ("adversarial", "I cancelled my subscription last month but was still charged {amount} this month. Please correct this charge."),
    ],
    "account_access": [
        ("easy", "I can't log in; the password reset email never arrives."),
        ("easy", "My account is locked after too many login attempts. Please unlock it."),
        ("adversarial", "I want to check the status of order {oid}, but my account is locked. Please unlock it so I can see it."),
        ("adversarial", "I got a new phone and now I can't get past the two-factor code to sign in and check my refund status."),
        ("adversarial", "I'm trying to cancel my subscription but I can't sign in: it says my password is wrong and the reset link has expired. Please help me get back into my account."),
    ],
    "cancellation": [
        ("easy", "Please cancel my subscription before it renews on {date}."),
        ("easy", "I placed order {oid} by mistake an hour ago. Please cancel it before it ships."),
        ("easy", "Cancel my monthly plan, please. I don't need it anymore."),
        ("adversarial", "I was charged {amount} for the annual plan last year and it renews next week. Please cancel it so I'm not charged again."),
        ("adversarial", "Order {oid} hasn't shipped yet and I found the {product} cheaper elsewhere. Please cancel the order."),
        ("adversarial", "My {product} order is stuck in the warehouse and hasn't shipped. I'd rather cancel it than wait."),
    ],
    "other": [
        ("easy", "Do you ship to Iceland?"),
        ("easy", "What are your customer service hours on weekends?"),
        ("adversarial", "I love my {product}! Do you offer a discount for bulk orders of 50 units?"),
        ("adversarial", "I'd like to update the shipping address on my account for future orders."),
        ("adversarial", "Is the {product} compatible with the older model's accessories? I'm thinking about buying a second one."),
    ],
}


def _classification_example(rng: Random, template: str) -> str:
    ctx = _ctx(rng, need_order=True)
    text = template.format(
        product=ctx.product,
        part=ctx.part,
        oid=ctx.order_id,
        amount=ctx.money(ctx.price),
        amount2=ctx.money(round(ctx.price * 1.25, 2) if ctx.currency != "JPY" else ctx.price + 1000),
        date=fmt_date(ctx.start.date() + timedelta(days=rng.randint(3, 20)), "long"),
    )
    c = Convo()
    c.say(ctx.start, "Customer", text)
    return c.render()


# ---------------------------------------------------------------- build, split, load


def generate(seed: int = SEED) -> list[Example]:
    rng = Random(seed)
    examples: list[Example] = []
    n = 0
    for template, difficulty, count in EXTRACTION_PLAN:
        for _ in range(count):
            n += 1
            text, expected, tags = template(rng)
            examples.append(Example(id=f"ext-{n:03d}", task="extraction", difficulty=difficulty,
                                    tags=list(dict.fromkeys([template.__name__, *tags])), input=text, expected=expected))
    for line in HANDWRITTEN.read_text().splitlines():
        if line.strip():
            examples.append(Example.model_validate_json(line))
    n = 0
    for category, templates in CLASSIFICATION_PLAN.items():
        for difficulty, template in templates:
            n += 1
            examples.append(Example(id=f"cls-{n:03d}", task="classification", difficulty=difficulty,
                                    tags=[category], input=_classification_example(rng, template),
                                    expected={"category": category}))
    return examples


def split(examples: list[Example], seed: int = SEED) -> dict[str, list[str]]:
    """50/50 split, stratified by task, difficulty and hand-written vs generated."""
    strata: dict[tuple, list[str]] = defaultdict(list)
    for ex in examples:
        strata[(ex.task, ex.difficulty, "handwritten" in ex.tags)].append(ex.id)
    rng = Random(seed + 1)
    tune, heldout = [], []
    for key in sorted(strata):
        ids = sorted(strata[key])
        rng.shuffle(ids)
        half = len(ids) // 2
        tune += ids[:half]
        heldout += ids[half:]
    return {"tune": sorted(tune), "heldout": sorted(heldout)}


def write(version: str = VERSION, seed: int = SEED) -> dict[str, Any]:
    out = DATA_DIR / version
    out.mkdir(parents=True, exist_ok=True)
    examples = generate(seed)
    ids = [e.id for e in examples]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate example ids")
    (out / "examples.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in examples))
    splits = split(examples, seed)
    (out / "splits.json").write_text(json.dumps({"seed": seed, **splits}, indent=2) + "\n")

    counts: dict[str, int] = defaultdict(int)
    split_of = {i: s for s, members in splits.items() for i in members}
    for e in examples:
        counts[f"{split_of[e.id]}/{e.task}/{e.difficulty}"] += 1
    manifest = {
        "version": version,
        "seed": seed,
        "n_examples": len(examples),
        "counts": dict(sorted(counts.items())),
        "examples_sha256": _sha256(out / "examples.jsonl"),
        "splits_sha256": _sha256(out / "splits.json"),
        "generator_sha256": _sha256(Path(__file__)),
        "handwritten_sha256": _sha256(HANDWRITTEN),
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_examples(version: str = VERSION) -> list[Example]:
    path = DATA_DIR / version / "examples.jsonl"
    return [Example.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


def load_split(name: str, version: str = VERSION) -> list[Example]:
    splits = json.loads((DATA_DIR / version / "splits.json").read_text())
    wanted = set(splits[name])
    return [e for e in load_examples(version) if e.id in wanted]


def dataset_sha256(version: str = VERSION) -> str:
    return _sha256(DATA_DIR / version / "examples.jsonl")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
