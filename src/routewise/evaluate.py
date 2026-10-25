"""Ground-truth scoring. Normalizes formatting (case, '#', number style) but not meaning.

Scoring reads the raw JSON object, so a schema-invalid answer can still earn credit for
the fields it got right; schema validity itself is the validator's concern.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from routewise.schema import Task

FIELDS: dict[str, tuple[str, ...]] = {
    "extraction": ("refund_requested", "refund_amount", "currency", "order_id", "contact_date", "refund_date"),
    "classification": ("category",),
}


@dataclass(frozen=True)
class Score:
    fields: dict[str, bool]
    field_accuracy: float
    task_correct: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score(task: Task, expected: dict[str, Any], data: dict[str, Any] | None) -> Score:
    names = FIELDS[task]
    got = data or {}
    fields = {name: data is not None and _field_equal(name, expected.get(name), got.get(name)) for name in names}
    correct = sum(fields.values())
    return Score(fields=fields, field_accuracy=correct / len(names), task_correct=correct == len(names))


def _field_equal(name: str, expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    match name:
        case "refund_requested":
            return isinstance(actual, bool) and actual == expected
        case "refund_amount":
            a, e = _to_number(actual), _to_number(expected)
            return a is not None and e is not None and math.isclose(a, e, abs_tol=0.005)
        case "currency" | "category":
            return isinstance(actual, str) and actual.strip().lower() == str(expected).strip().lower()
        case "order_id":
            return isinstance(actual, str) and _norm_order_id(actual) == _norm_order_id(str(expected))
        case "contact_date" | "refund_date":
            a, e = _to_date(actual), _to_date(expected)
            return a is not None and a == e
    raise KeyError(f"no comparison rule for field {name!r}")


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _norm_order_id(value: str) -> str:
    return value.strip().lstrip("#").strip().upper()


def _to_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
