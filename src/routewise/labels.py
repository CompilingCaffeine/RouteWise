"""Human labels for workload validation: the label-row import format, agreement and adjudication.

No model is called and no model output is read here. Each conversation needs two independent
human labels; every disagreement that changes a metric is resolved by a third person (the
adjudicator) before the dataset can be built. Labels follow the field contract in
data/validation-template/README.md, which is the contract the deployed profiles are scored on.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from routewise.evaluate import FIELDS, score
from routewise.schema import Example, ExtractionOutput
from routewise.textfacts import parse_messages

SPLITS = ("development", "heldout")
SPEAKERS = ("Customer", "Agent", "System")
REVIEW_REASONS = ("issue_date_ambiguous", "conflicting_statements", "amount_ambiguous", "order_ambiguous",
                  "request_ambiguous", "other")
EXTRACTION_FIELDS = FIELDS["extraction"]
LABEL_COLUMNS = ("conversation_id", "split", "text", *EXTRACTION_FIELDS, "review_required", "review_reason",
                 "evidence", "scenario_tags", "labeler_id", "notes")
ADJUDICATION_COLUMNS = ("conversation_id", "decision", "adjudicator_id", "reason", *EXTRACTION_FIELDS,
                        "review_required", "review_reason", "evidence", "scenario_tags")
EXAMPLE_PREFIX = "EXAMPLE-"  # illustrative template rows; never evidence
MAX_ERRORS_SHOWN = 40
# The guide's shared scenario tags. Labelers may add others; only these appear in exported aggregates.
TAG_VOCABULARY = ("relative_issue_date", "absolute_issue_date", "past_issue_date", "no_issue_date", "arrival_date_only",
                  "business_days", "range_or_bound", "vague_timing", "corrected_date", "conflicting_statements",
                  "multiple_orders", "partial_refund", "agreed_amount", "full_refund_no_amount", "no_refund_request",
                  "replacement_or_repair", "cancellation", "damaged_item", "late_delivery", "return_received")

_TAG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\] ([A-Za-z]+): \S")
_AMOUNT = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_TRUE, _FALSE = {"true", "yes", "y", "1"}, {"false", "no", "n", "0"}
_PII = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\+?\d[\d ().-]{8,}\d"),
    "card": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
}


class Label(BaseModel):
    """One person's label for one conversation: a row of the labeling template."""

    conversation_id: str
    split: Literal["development", "heldout"]
    text: str
    expected: dict[str, Any]
    review_required: bool
    review_reason: str | None
    evidence: list[str]
    scenario_tags: list[str]
    labeler_id: str
    notes: str = ""


class LabeledConversation(BaseModel):
    """One conversation of a built dataset: the agreed or adjudicated label and its provenance."""

    id: str
    split: Literal["development", "heldout"]
    input: str
    expected: dict[str, Any]
    review_required: bool
    review_reason: str | None
    evidence: list[str]
    tags: list[str]
    labelers: list[str]
    adjudicator: str | None = None

    def example(self) -> Example:
        return Example(id=self.id, task="extraction", difficulty="adversarial", tags=self.tags,
                       input=self.input, expected=self.expected)

    @property
    def family(self) -> str:
        return self.tags[0]


class LabelError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        shown = errors[:MAX_ERRORS_SHOWN]
        more = f"\n  ... and {len(errors) - len(shown)} more" if len(errors) > len(shown) else ""
        super().__init__(f"{len(errors)} problem(s):\n  " + "\n  ".join(shown) + more)
        self.errors = errors


# ---------------------------------------------------------------- reading files


def read_rows(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Rows of a CSV or JSONL file with a location for error messages; `ref_` columns are ignored."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        rows = [(f"{path.name}:{i}", json.loads(line)) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
    elif path.suffix.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(text, newline=""))
        rows = [(f"{path.name}:row {i}", row) for i, row in enumerate(reader, 2)]
    else:
        raise ValueError(f"{path}: use .csv or .jsonl")
    return [(where, {k.strip(): v for k, v in row.items() if k and not k.strip().startswith("ref_")})
            for where, row in rows]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"expected true or false, got {value!r}")


def _amount(value: Any) -> float | None:
    if _blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    v = str(value).strip()
    if not _AMOUNT.match(v):
        raise ValueError(f"refund_amount {value!r} must be a plain number with a decimal point (e.g. 1234.50)")
    return float(v.replace(",", ""))


def _text(value: Any) -> str | None:
    return None if _blank(value) else str(value).strip()


def _currency(value: Any) -> str | None:
    return (_text(value) or "").upper() or None


def _list(value: Any, sep: str) -> list[str]:
    if _blank(value):
        return []
    items = value if isinstance(value, list) else str(value).split(sep)
    return [str(i).strip() for i in items if str(i).strip()]


def normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def text_sha256(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def check_conversation(text: str) -> list[str]:
    """The deployed parser needs one timestamped line per message and known speaker roles."""
    problems, last = [], None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ["conversation text is empty"]
    if len(text) > 20000:
        problems.append("conversation exceeds 20,000 characters")
    for i, line in enumerate(lines, 1):
        m = _LINE.match(line)
        if not m:
            problems.append(f"line {i} is not '[YYYY-MM-DD HH:MM] Speaker: message' (join wrapped lines into one)")
            continue
        if m.group(3) not in SPEAKERS:
            problems.append(f"line {i} speaker {m.group(3)!r} must be one of {', '.join(SPEAKERS)}")
    if problems:
        return problems
    try:
        messages = parse_messages(text)
    except ValueError as exc:
        return [f"invalid timestamp: {exc}"]
    for msg in messages:
        if last is not None and msg.when < last:
            problems.append(f"messages are out of time order at {msg.when:%Y-%m-%d %H:%M}")
        last = msg.when
    return problems


def first_customer_date(text: str) -> str | None:
    customers = [m for m in parse_messages(text) if m.speaker == "Customer"]
    return customers[0].when.date().isoformat() if customers else None


def label_fields(row: dict[str, Any], text: str) -> tuple[dict[str, Any], list[str]]:
    """The label part of a row (six fields, review, evidence, tags), checked against the field contract."""
    errors: list[str] = []
    out: dict[str, Any] = {}
    expected: dict[str, Any] = {}
    converters = {"refund_requested": _bool, "refund_amount": _amount, "currency": _currency, "order_id": _text,
                  "contact_date": _text, "refund_date": _text, "review_required": _bool}
    for name, convert in converters.items():
        try:
            (out if name == "review_required" else expected)[name] = convert(row.get(name))
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
    if errors:
        return {}, errors
    try:
        ExtractionOutput.model_validate(expected)
    except ValidationError as exc:
        return {}, [f"{e['loc'][0]}: {e['msg']}" for e in exc.errors()]
    out["expected"] = expected
    out["review_reason"] = _text(row.get("review_reason"))
    out["evidence"] = _list(row.get("evidence"), "|")
    out["scenario_tags"] = [t.lower() for t in _list(row.get("scenario_tags"), ";")]

    if expected["currency"] and expected["refund_amount"] is None:
        errors.append("currency is set but refund_amount is empty; currency belongs to the refund amount")
    contact = first_customer_date(text)
    if expected["contact_date"] != contact:
        errors.append(f"contact_date must be the first Customer message date ({contact}), got {expected['contact_date']}")
    reason = out["review_reason"]
    if out["review_required"]:
        code = (reason or "").split(":", 1)[0].strip()
        if code not in REVIEW_REASONS:
            errors.append(f"review_reason must start with one of {', '.join(REVIEW_REASONS)}")
        elif code == "other" and not (reason or "").split(":", 1)[1:]:
            errors.append("review_reason 'other' needs a description: 'other: ...'")
    elif reason:
        errors.append("review_reason is set but review_required is false")
    needs_evidence = (expected["refund_requested"] or out["review_required"]
                      or any(expected[f] is not None for f in ("refund_amount", "order_id", "refund_date")))
    if needs_evidence and not out["evidence"]:
        errors.append("evidence: quote the message text that supports the label (separate quotes with |)")
    haystack = normalized(text)
    for quote in out["evidence"]:
        if normalized(quote) not in haystack:
            errors.append(f"evidence quote not found verbatim in the conversation: {quote[:60]!r}")
    if not out["scenario_tags"]:
        errors.append("scenario_tags: give at least one tag; the first is the primary scenario")
    for tag in out["scenario_tags"]:
        if not _TAG.match(tag):
            errors.append(f"scenario tag {tag!r} must use lowercase letters, digits, '-' or '_'")
    return out, errors


def parse_label(where: str, row: dict[str, Any]) -> tuple[Label | None, list[str]]:
    unknown = set(row) - set(LABEL_COLUMNS)
    if unknown:
        return None, [f"{where}: unknown column(s) {sorted(unknown)}"]
    cid = _text(row.get("conversation_id")) or ""
    at = f"{where} ({cid or 'no id'})"
    if not cid:
        return None, [f"{at}: conversation_id is empty"]
    if cid.upper().startswith(EXAMPLE_PREFIX):
        return None, [f"{at}: illustrative template rows (EXAMPLE-...) cannot be imported; delete them"]
    split = _text(row.get("split"))
    if split not in SPLITS:
        return None, [f"{at}: split must be development or heldout"]
    text = "\n".join(line.rstrip() for line in str(row.get("text") or "").strip().splitlines())
    problems = check_conversation(text)
    labeler = _text(row.get("labeler_id"))
    if not labeler:
        problems.append("labeler_id is empty")
    if problems:
        return None, [f"{at}: {p}" for p in problems]
    fields, errors = label_fields(row, text)
    if errors:
        return None, [f"{at}: {e}" for e in errors]
    return Label(conversation_id=cid, split=split, text=text, labeler_id=labeler,
                 notes=_text(row.get("notes")) or "", **fields), []


# ---------------------------------------------------------------- agreement and adjudication


def disagreement(a: Label, b: Label) -> list[str]:
    """Differences that change a metric. Field values are not scored when review is required."""
    if a.review_required != b.review_required:
        return ["review_required"]
    if a.review_required:
        return []
    return [f for f, same in score("extraction", a.expected, b.expected).fields.items() if not same]


def cohen_kappa(pairs: list[tuple[Any, Any]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[k] * right[k] for k in left) / (n * n)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def agreement(pairs: list[tuple[Label, Label]]) -> dict[str, Any]:
    both = [(a, b) for a, b in pairs if not a.review_required and not b.review_required]
    per_field = {}
    for f in EXTRACTION_FIELDS:
        same = [score("extraction", {f: a.expected[f]}, {f: b.expected[f]}).fields[f] for a, b in both]
        per_field[f] = {"n": len(same), "agreement": sum(same) / len(same) if same else None}
    for f in ("refund_requested", "currency"):
        per_field[f]["kappa"] = cohen_kappa([(a.expected[f], b.expected[f]) for a, b in both])
    differing = [disagreement(a, b) for a, b in pairs]
    return {
        "n_conversations": len(pairs),
        "review_required": {"agreement": sum(a.review_required == b.review_required for a, b in pairs) / len(pairs)
                            if pairs else None,
                            "kappa": cohen_kappa([(a.review_required, b.review_required) for a, b in pairs]),
                            "both": sum(a.review_required and b.review_required for a, b in pairs),
                            "one": sum(a.review_required != b.review_required for a, b in pairs)},
        "fields_when_both_answerable": per_field,
        "metric_disagreements": sum(bool(d) for d in differing),
        "metric_agreement_rate": sum(not d for d in differing) / len(pairs) if pairs else None,
        "disagreements_by_field": dict(Counter(f for d in differing for f in d)),
    }


@dataclass
class Assembly:
    records: list[LabeledConversation] = field(default_factory=list)
    queue: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    agreement: dict[str, Any] = field(default_factory=dict)
    pii_warnings: list[dict[str, Any]] = field(default_factory=list)
    adjudicated: int = 0


def _merge(a: Label, b: Label) -> dict[str, Any]:
    """An agreed label; when both require review, fields they disagree on become null (never scored)."""
    fields = score("extraction", a.expected, b.expected).fields
    expected = {f: a.expected[f] if fields[f] else None for f in EXTRACTION_FIELDS}
    reasons = list(dict.fromkeys(r for r in (a.review_reason, b.review_reason) if r))
    return {"expected": expected, "review_required": a.review_required,
            "review_reason": " | ".join(reasons) if reasons else None,
            "evidence": list(dict.fromkeys(a.evidence + b.evidence)),
            "tags": list(dict.fromkeys(a.scenario_tags + b.scenario_tags))}


def pii_flags(text: str) -> list[str]:
    """Likely personal data in message bodies (timestamps excluded). A warning, not a de-identification."""
    body = "\n".join(m.text for m in parse_messages(text))
    kinds = []
    for kind, pattern in _PII.items():
        for m in pattern.finditer(body):
            digits = re.sub(r"\D", "", m.group())
            if kind == "email" or (kind == "phone" and len(digits) >= 10) or (kind == "card" and _luhn(digits)):
                kinds.append(kind)
                break
    return kinds


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return 13 <= len(digits) <= 19 and total % 10 == 0


def assemble(label_rows: list[tuple[str, dict[str, Any]]],
             adjudication_rows: list[tuple[str, dict[str, Any]]] = ()) -> Assembly:
    """Validate every row, pair the two labels per conversation, apply adjudication, and build the dataset."""
    errors: list[str] = []
    by_id: dict[str, list[Label]] = defaultdict(list)
    for where, row in label_rows:
        label, problems = parse_label(where, row)
        errors += problems
        if label:
            by_id[label.conversation_id].append(label)
    pairs: dict[str, tuple[Label, Label]] = {}
    texts: dict[str, str] = {}
    for cid, labels in sorted(by_id.items()):
        labelers = [lb.labeler_id for lb in labels]
        if len(labels) != 2 or len(set(labelers)) != 2:
            errors.append(f"{cid}: needs exactly two labels from two different labelers, got {labelers}")
            continue
        a, b = sorted(labels, key=lambda lb: lb.labeler_id)
        if a.text != b.text or a.split != b.split:
            errors.append(f"{cid}: text or split differs between the two labelers' copies")
            continue
        digest = text_sha256(a.text)
        if digest in texts:
            errors.append(f"{cid}: same conversation text as {texts[digest]}; remove the duplicate")
            continue
        texts[digest] = cid
        pairs[cid] = (a, b)

    decisions: dict[str, dict[str, Any]] = {}
    for where, row in adjudication_rows:
        unknown = set(row) - set(ADJUDICATION_COLUMNS)
        cid = _text(row.get("conversation_id")) or ""
        at = f"{where} ({cid or 'no id'})"
        if unknown:
            errors.append(f"{at}: unknown column(s) {sorted(unknown)}")
            continue
        decision, who = _text(row.get("decision")), _text(row.get("adjudicator_id"))
        if not decision and _blank(row.get("review_required")):
            continue  # an untouched queue row
        if cid not in pairs:
            errors.append(f"{at}: not a labelled conversation")
            continue
        if cid in decisions:
            errors.append(f"{at}: adjudicated more than once")
            continue
        if decision not in ("label", "exclude") or not who or not _text(row.get("reason")):
            errors.append(f"{at}: needs decision (label or exclude), adjudicator_id and reason")
            continue
        if who in (pairs[cid][0].labeler_id, pairs[cid][1].labeler_id):
            errors.append(f"{at}: the adjudicator must not be one of the conversation's labelers")
            continue
        entry = {"decision": decision, "adjudicator": who, "reason": _text(row.get("reason"))}
        if decision == "label":
            fields, problems = label_fields(row, pairs[cid][0].text)
            if problems:
                errors += [f"{at}: {p}" for p in problems]
                continue
            entry["label"] = fields
        decisions[cid] = entry
    if errors:
        raise LabelError(errors)

    out = Assembly(agreement=agreement(list(pairs.values())))
    for cid, (a, b) in pairs.items():
        flags = pii_flags(a.text)
        if flags:
            out.pii_warnings.append({"id": cid, "kinds": flags})
        decided = decisions.get(cid)
        provenance = {"id": cid, "split": a.split, "input": a.text, "labelers": [a.labeler_id, b.labeler_id]}
        if decided and decided["decision"] == "exclude":
            out.excluded.append({"id": cid, "adjudicator": decided["adjudicator"], "reason": decided["reason"]})
        elif decided:
            fields = decided["label"]
            out.adjudicated += 1
            out.records.append(LabeledConversation(
                **provenance, expected=fields["expected"], review_required=fields["review_required"],
                review_reason=fields["review_reason"], evidence=fields["evidence"], tags=fields["scenario_tags"],
                adjudicator=decided["adjudicator"]))
        elif disagreement(a, b):
            out.queue.append({"conversation_id": cid, "ref_differs": ", ".join(disagreement(a, b)),
                              "ref_text": a.text, "ref_label_a": _summary(a), "ref_label_b": _summary(b)})
        else:
            out.records.append(LabeledConversation(**provenance, **_merge(a, b)))
    return out


def _summary(label: Label) -> str:
    return json.dumps({"labeler_id": label.labeler_id, **label.expected, "review_required": label.review_required,
                       "review_reason": label.review_reason, "evidence": label.evidence,
                       "scenario_tags": label.scenario_tags})


def write_queue(path: Path, queue: list[dict[str, Any]]) -> None:
    """The adjudication queue is the adjudication template, pre-filled with both labels for reference."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*ADJUDICATION_COLUMNS, "ref_differs", "ref_text",
                                                "ref_label_a", "ref_label_b"])
        writer.writeheader()
        writer.writerows(queue)
