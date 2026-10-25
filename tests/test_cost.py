import math

import pytest

from routewise.cost import Price, call_cost_usd, cost_per_1k_tasks
from routewise.prices import PriceNotFound, build_snapshot, price_for

PRICE = Price(model_id="m", prompt=0.06e-6, completion=0.24e-6, input_cache_read=0.03e-6)


def test_cost_without_cache():
    assert math.isclose(call_cost_usd(PRICE, 1000, 200), 1000 * 0.06e-6 + 200 * 0.24e-6)


def test_cached_tokens_use_cached_rate():
    expected = 400 * 0.06e-6 + 600 * 0.03e-6 + 200 * 0.24e-6
    assert math.isclose(call_cost_usd(PRICE, 1000, 200, cached_tokens=600), expected)


def test_cached_tokens_fall_back_to_input_rate():
    no_cache = Price(model_id="m", prompt=0.06e-6, completion=0.24e-6)
    assert math.isclose(call_cost_usd(no_cache, 1000, 200, cached_tokens=600), call_cost_usd(no_cache, 1000, 200))


def test_invalid_counts_raise():
    with pytest.raises(ValueError):
        call_cost_usd(PRICE, 10, 5, cached_tokens=11)
    with pytest.raises(ValueError):
        cost_per_1k_tasks(1.0, 0)


def test_price_snapshot_round_trip():
    models = [
        {
            "id": "nvidia/model-a",
            "name": "A",
            "context_length": 131072,
            "pricing": {"prompt": "0.00000006", "completion": "0.00000024", "input_cache_read": None, "request": "0"},
        }
    ]
    snapshot = build_snapshot(models, "https://api.example/v1/")
    price = price_for(snapshot, "nvidia/model-a")
    assert math.isclose(price.prompt_per_million, 0.06) and math.isclose(price.completion_per_million, 0.24)
    assert price.input_cache_read is None
    with pytest.raises(PriceNotFound, match="closest ids"):
        price_for(snapshot, "nvidia/model-b")
