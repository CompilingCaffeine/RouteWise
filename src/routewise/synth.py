"""v2 workload synthesis: writer models turn fact sheets into conversations, then strict verification.

Labels come from the fact sheet. A conversation is accepted only if it contains every required
string in the planned message and nothing the fact sheet does not allow (extra amounts, dates,
time expressions, order IDs). Semantic disagreements found later by two strong verifiers go to
a human-readable adjudication queue instead of being silently dropped.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from random import Random
from typing import Any

from routewise.client import TokenFactoryClient
from routewise.config import Config
from routewise.cost import Price, call_cost_usd
from routewise.factsheets import FactSheet
from routewise.schema import Example
from routewise.textfacts import dates_in, money_mentions

WRITER_SYSTEM = """You write realistic customer-support chat messages for a test dataset.
You receive a numbered plan. Write exactly one message per plan item, in the same order.

Rules:
- Write each message in the first person, in the voice of its speaker. The customer bought the product and is asking the store for help; the agent works for the store and answers the customer. System messages are short automated notices.
- Each message says what its plan item describes, naturally, in 1-4 sentences.
- Copy every quoted string exactly as given: same characters, digits, punctuation and currency format.
- Do not mention any other prices, money amounts, decimal numbers, dates, weekdays, clock times, relative time expressions (for example "yesterday", "next week", "in a few days", "48 hours") or order/reference numbers beyond the quoted strings.
- Do not write timestamps, speaker names or labels: only the message text.
- Tone: {tone}.{typos}

Return JSON {{"messages": [...]}} with exactly {n} strings."""

TIME_WORDS = re.compile(
    r"\b(yesterday|today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend"
    r"|next (?:week|month|year)|last (?:week|month|year|night)|this (?:week|month|morning|afternoon|evening)"
    r"|\d+\s*(?:-\s*\d+\s*)?(?:business |working )?(?:days?|weeks?|months?|hours?)"
    r"|(?:one|two|three|four|five|six|seven|eight|nine|ten|a few|few|couple of)\s+(?:business |working )?(?:days?|weeks?|months?|hours?)"
    r"|(?:a|an)\s+(?:day|week|month|hour)|\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm))\b",
    re.IGNORECASE,
)
ORDER_LIKE = re.compile(r"\b[A-Z]{2,5}-\d{3,}\b|#\d{4,}\b")
DECIMAL = re.compile(r"\d[\d,]*\.\d{2}\b")
THOUSANDS = re.compile(r"\b\d{1,3}(?:,\d{3})+\b(?!\.\d)")


def writer_messages(sheet: FactSheet) -> list[dict[str, str]]:
    typos = " Include a few small, natural typos." if sheet.typos else ""
    system = WRITER_SYSTEM.format(tone=sheet.tone, typos=typos, n=len(sheet.messages))
    lines = []
    for i, m in enumerate(sheet.messages, start=1):
        must = f" Must include exactly: {', '.join(json.dumps(s, ensure_ascii=False) for s in m.must)}." if m.must else ""
        voice = {"Customer": "the customer", "Agent": "the support agent", "System": "an automated system notice"}[m.speaker]
        lines.append(f"{i}. {m.speaker} message, written by {voice}: {voice} {m.guidance}.{must}")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def writer_schema(n: int) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": "conversation", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["messages"],
        "properties": {"messages": {"type": "array", "items": {"type": "string"}, "minItems": n, "maxItems": n}},
    }}}


def clean_texts(sheet: FactSheet, texts: Sequence[str]) -> list[str]:
    out = []
    for m, text in zip(sheet.messages, texts):
        text = " ".join(str(text).split())
        for prefix in (f"{m.speaker}:", f"{m.speaker.lower()}:", "Agent:", "Customer:", "System:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        out.append(text)
    return out


def message_times(sheet: FactSheet) -> list[datetime]:
    rng = Random(sheet.id)
    contact = date.fromisoformat(sheet.contact)
    times, current = [], datetime.fromisoformat(sheet.start)
    for i, m in enumerate(sheet.messages):
        day = contact + timedelta(days=m.day)
        if i == 0:
            current = datetime.combine(day, current.time())
        elif current.date() != day:
            current = datetime.combine(day, time(hour=rng.randint(8, 17), minute=rng.randint(0, 59)))
        else:
            current += timedelta(minutes=rng.randint(2, 25))
        times.append(current)
    return times


def render(sheet: FactSheet, texts: Sequence[str]) -> str:
    return "\n".join(f"[{t:%Y-%m-%d %H:%M}] {m.speaker}: {text}"
                     for t, m, text in zip(message_times(sheet), sheet.messages, texts))


def verify(sheet: FactSheet, texts: Sequence[str]) -> list[str]:
    """Deterministic acceptance check; returns the list of problems (empty = accepted)."""
    if len(texts) != len(sheet.messages):
        return [f"expected {len(sheet.messages)} messages, got {len(texts)}"]
    problems = []
    allowed_values = [a for a, _ in sheet.allowed_amounts]
    allowed_dates = {date.fromisoformat(d) for d in sheet.allowed_dates}
    phrases = [p.lower() for p in sheet.allowed_phrases]
    contact = date.fromisoformat(sheet.contact)
    for i, (m, text) in enumerate(zip(sheet.messages, texts), start=1):
        for s in m.must:
            present = s.lower() in text.lower() if s.lower() in phrases else s in text
            if not present:
                problems.append(f"message {i} is missing {s!r}")
        for amount, currency in money_mentions(text):
            if not any(math.isclose(amount, a, abs_tol=0.005) and currency == c for a, c in sheet.allowed_amounts):
                problems.append(f"message {i} mentions unexpected money {amount} {currency}")
        for num in DECIMAL.findall(text) + THOUSANDS.findall(text):
            value = float(num.replace(",", ""))
            if not any(math.isclose(value, a, abs_tol=0.005) for a in allowed_values):
                problems.append(f"message {i} has an unexpected number {num}")
        extra_dates = dates_in(text, contact + timedelta(days=m.day)) - allowed_dates
        if extra_dates:
            problems.append(f"message {i} states unexpected dates {sorted(d.isoformat() for d in extra_dates)}")
        remainder = text
        for p in sorted(sheet.allowed_phrases, key=len, reverse=True):
            remainder = re.sub(re.escape(p), " ", remainder, flags=re.IGNORECASE)
        bad_time = TIME_WORDS.search(remainder)
        if bad_time:
            problems.append(f"message {i} uses a time expression {bad_time.group(0)!r}")
        for oid in ORDER_LIKE.findall(text):
            if not any(oid == allowed or (allowed.startswith(oid) and allowed[len(oid):len(oid)+1] in ('-', '_', '/'))
                       for allowed in sheet.order_ids):
                problems.append(f"message {i} mentions an unexpected reference {oid!r}")
    return problems


def to_example(sheet: FactSheet, texts: Sequence[str]) -> Example:
    tags = [sheet.family, f"writer:{sheet.writer}", f"tone:{sheet.tone}"] + (["typos"] if sheet.typos else [])
    return Example(id=sheet.id, task="extraction", difficulty=sheet.difficulty, tags=tags,
                   input=render(sheet, texts), expected=sheet.expected)


def write_one(
    client: TokenFactoryClient, config: Config, sheet: FactSheet, price: Price, *, max_attempts: int = 3,
    seed: int = 20261026,
) -> dict[str, Any]:
    """Generate one conversation, retrying until it verifies; returns a provenance record."""
    cand = config.candidate(sheet.writer)
    attempts = []
    for attempt in range(1, max_attempts + 1):
        result = client.chat(model=cand.model_id, messages=writer_messages(sheet), max_tokens=1200, temperature=0.8,
                             seed=seed + attempt, response_format=writer_schema(len(sheet.messages)),
                             extra_body=cand.extra_body)
        cost = (call_cost_usd(price, result.prompt_tokens, result.completion_tokens, result.cached_tokens or 0)
                if result.prompt_tokens is not None and result.completion_tokens is not None else None)
        texts, problems = [], []
        if result.error or not result.content:
            problems = [f"writer error: {result.error or 'empty response'}"]
        else:
            try:
                texts = clean_texts(sheet, json.loads(result.content)["messages"])
                problems = verify(sheet, texts)
            except (ValueError, KeyError, TypeError) as exc:
                problems = [f"unparseable writer output: {exc}"]
        attempts.append({"attempt": attempt, "cost_usd": cost, "latency_ms": result.latency_ms, "problems": problems})
        if not problems:
            return {"id": sheet.id, "accepted": True, "texts": texts, "attempts": attempts,
                    "example": to_example(sheet, texts).model_dump(), "factsheet": sheet.to_dict()}
    return {"id": sheet.id, "accepted": False, "texts": texts, "attempts": attempts, "factsheet": sheet.to_dict()}


def write_all(
    client: TokenFactoryClient, config: Config, sheets: Sequence[FactSheet], prices: dict[str, Price], *,
    concurrency: int, rpm: float, sink: Callable[[dict[str, Any]], None], progress: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    from routewise.benchmark import run_parallel

    lock = threading.Lock()
    done = [0]

    def job(sheet: FactSheet) -> Callable[[], dict[str, Any]]:
        def run_one() -> dict[str, Any]:
            rec = write_one(client, config, sheet, prices[sheet.writer])
            with lock:
                sink(rec)
                done[0] += 1
                if done[0] % 100 == 0 or done[0] == len(sheets):
                    progress(f"  {done[0]}/{len(sheets)} conversations")
            return rec

        return run_one

    return run_parallel([job(s) for s in sheets], concurrency=concurrency, rpm=rpm)  # type: ignore[return-value]


def disputed_fields(records_by_verifier: dict[str, Any]) -> list[str]:
    """Fields that *every* verifier got 'wrong' against the label (candidates for a bad label)."""
    wrong_sets = [{f for f, ok in r.score["fields"].items() if not ok} for r in records_by_verifier.values()]
    return sorted(set.intersection(*wrong_sets)) if wrong_sets else []


def adjudication_queue(examples: Sequence[Example], records: Sequence[Any]) -> list[dict[str, Any]]:
    by_example: dict[str, dict[str, Any]] = {}
    for r in records:
        by_example.setdefault(r.example_id, {})[r.candidate] = r
    queue = []
    for ex in examples:
        recs = by_example.get(ex.id, {})
        fields = disputed_fields(recs)
        if fields:
            queue.append({
                "id": ex.id,
                "family": ex.tags[0],
                "disputed_fields": fields,
                "input": ex.input,
                "label": {f: ex.expected[f] for f in fields},
                "verifier_answers": {name: {f: (r.parsed or {}).get(f) for f in fields} for name, r in recs.items()},
            })
    return queue
