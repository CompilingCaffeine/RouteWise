import json
from datetime import date

import pytest

from routewise.config import REPO_ROOT
from routewise.dataset import load_examples
from routewise.schema import Example, parse_output
from routewise.textfacts import analyze, dates_in, money_mentions
from routewise.validate import validate

# Ground-truth answers the validator rejects. Each is a measured false positive, kept on purpose:
# ext-h09 states the amount in a sentence without a refund cue ("...for $59.00 - it keeps losing connection.").
KNOWN_FALSE_POSITIVES = {"ext-h09"}

PROBE = Example.model_validate_json((REPO_ROOT / "data/probe/extraction_probe.json").read_text())


def check(answer: dict, example: Example = PROBE):
    return validate(example.task, example.input, parse_output(example.task, json.dumps(answer)))


def test_ground_truth_passes_except_known_false_positives():
    rejected = {
        e.id
        for e in load_examples()
        if not validate(e.task, e.input, parse_output(e.task, json.dumps(e.expected))).passed
    }
    assert rejected == KNOWN_FALSE_POSITIVES


def test_probe_truth_passes():
    assert check(PROBE.expected).passed


def test_quoted_price_instead_of_refund_is_caught():
    v = check(dict(PROBE.expected, refund_amount=389.99))
    assert v.failed_rules == ["amount_refund_context"]


def test_hallucinated_amount_is_caught():
    assert "amount_in_source" in check(dict(PROBE.expected, refund_amount=100.0)).failed_rules


def test_currency_must_agree_with_source_marker():
    assert check(dict(PROBE.expected, currency="EUR")).failed_rules == ["currency_consistent"]
    assert check(dict(PROBE.expected, currency=None)).failed_rules == ["currency_consistent"]


def test_missing_stated_refund_date_is_caught():
    assert check(dict(PROBE.expected, refund_date=None)).failed_rules == ["refund_date_matches_statement"]


def test_unstated_date_is_caught():
    v = check(dict(PROBE.expected, refund_date="2026-03-18"))
    assert v.failed_rules == ["dates_in_source", "refund_date_matches_statement"]


def test_order_date_is_not_a_contact_date():
    # March 2 is stated (order date), so it passes dates_in_source, but no customer wrote on that day.
    assert check(dict(PROBE.expected, contact_date="2026-03-02")).failed_rules == ["contact_date_valid"]


def test_order_date_is_not_the_refund_date():
    assert check(dict(PROBE.expected, refund_date="2026-03-02")).failed_rules == ["refund_date_matches_statement"]


def test_relative_date_resolved_from_wrong_message_is_caught():
    # "tomorrow" in the agent's 07-05 message means 07-06; 07-05 is a stated date but not the refund date.
    ex = {e.id: e for e in load_examples()}["ext-036"]
    assert check(dict(ex.expected, refund_date="2026-07-05"), ex).failed_rules == ["refund_date_matches_statement"]


def test_order_id_must_come_from_the_source():
    ex = {e.id: e for e in load_examples()}["ext-030"]  # no order id in this conversation
    assert check(dict(ex.expected, order_id="ORD-48213"), ex).failed_rules == ["order_id_in_source"]
    assert check(dict(ex.expected, order_id="Halo baby monitor"), ex).failed_rules == ["order_id_in_source"]
    assert check(dict(PROBE.expected, order_id="#ord-48213")).passed


def test_schema_failure_short_circuits():
    v = validate("extraction", PROBE.input, parse_output("extraction", '{"refund_requested": "yes"}'))
    assert [c.rule for c in v.checks] == ["schema"] and not v.passed


def test_classification_only_checks_schema():
    assert validate("classification", "x", parse_output("classification", '{"category": "other"}')).passed
    assert not validate("classification", "x", parse_output("classification", '{"category": "nope"}')).passed


@pytest.mark.parametrize(
    "text, expected",
    [
        ("charged CA$1,249.00 today", [(1249.0, "CAD")]),
        ("refund of 149.99 CAD will be sent", [(149.99, "CAD")]),
        ("I paid ¥8,900.", [(8900.0, "JPY")]),
        ("We'll refund the CHF 25.00 fee", [(25.0, "CHF")]),
        ("A$89.95 and £36.00 and €12.50 and $5", [(89.95, "AUD"), (36.0, "GBP"), (12.5, "EUR"), (5.0, "USD")]),
        ("order ORD-48213 at 09:12, size 10.5", []),
    ],
)
def test_money_mentions(text, expected):
    assert money_mentions(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("sent on Sept 4.", {date(2026, 9, 4)}),
        ("issued on 24 March 2026.", {date(2026, 3, 24)}),
        ("issued on March 17, 2026, and", {date(2026, 3, 17)}),
        ("processed in three days.", {date(2026, 7, 5)}),
        ("processed in 3 days.", {date(2026, 7, 5)}),
        ("refund it tomorrow", {date(2026, 7, 3)}),
        ("on 2026-02-30 or 2026-03-01", {date(2026, 3, 1)}),
        ("took 3 weeks instead of 5 days", set()),
    ],
)
def test_dates_in(text, expected):
    assert dates_in(text, date(2026, 7, 2)) == expected


def test_analyze_tracks_customer_dates_and_refund_statements():
    facts = analyze(PROBE.input)
    assert facts.customer_dates == {date(2026, 3, 14)}
    assert facts.refund_date_stated
    assert {date(2026, 3, 2), date(2026, 3, 14), date(2026, 3, 17)} <= facts.dates
