from routewise.evaluate import score
from routewise.schema import parse_output

EXPECTED = {
    "refund_requested": True,
    "refund_amount": 120.0,
    "currency": "USD",
    "order_id": "ORD-48213",
    "contact_date": "2026-03-14",
    "refund_date": "2026-03-17",
}


def test_exact_answer_is_fully_correct():
    s = score("extraction", EXPECTED, dict(EXPECTED))
    assert s.task_correct and s.field_accuracy == 1.0


def test_formatting_differences_are_normalized():
    got = dict(EXPECTED, refund_amount="120.00", currency="usd", order_id="#ord-48213")
    assert score("extraction", EXPECTED, got).task_correct


def test_distractor_amount_is_wrong():
    s = score("extraction", EXPECTED, dict(EXPECTED, refund_amount=389.99))
    assert not s.task_correct
    assert s.fields["refund_amount"] is False
    assert s.field_accuracy == 5 / 6


def test_null_handling():
    expected = dict(EXPECTED, refund_date=None)
    assert score("extraction", expected, dict(expected)).fields["refund_date"]
    assert not score("extraction", expected, dict(expected, refund_date="2026-03-17")).fields["refund_date"]
    assert not score("extraction", EXPECTED, dict(EXPECTED, refund_date=None)).fields["refund_date"]


def test_bool_is_not_coerced_from_string():
    assert not score("extraction", EXPECTED, dict(EXPECTED, refund_requested="true")).fields["refund_requested"]


def test_unparseable_output_scores_zero():
    s = score("extraction", EXPECTED, None)
    assert s.field_accuracy == 0 and not s.task_correct


def test_classification():
    assert score("classification", {"category": "billing_error"}, {"category": "Billing_Error"}).task_correct
    assert not score("classification", {"category": "billing_error"}, {"category": "refund_request"}).task_correct


def test_parse_output_accepts_fence_and_flags_schema_errors():
    fenced = parse_output("classification", '```json\n{"category": "other"}\n```')
    assert fenced.schema_valid

    bad = parse_output("extraction", '{"refund_requested": true}')
    assert bad.data is not None and not bad.schema_valid and "refund_amount" in bad.schema_error

    bad_date = parse_output("extraction", __import__("json").dumps(dict(EXPECTED, refund_date="March 17")))
    assert not bad_date.schema_valid

    assert parse_output("extraction", "not json").parse_error.startswith("invalid JSON")
    assert parse_output("extraction", "").parse_error == "empty response"
