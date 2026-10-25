import pytest

from routewise.statistics import cheapest_qualifying, cluster_ci, wilson


def test_one_sided_bounds_support_declared_sample_size():
    assert wilson(470, 480, one_sided=True)[0] > .95
    assert wilson(456, 480, one_sided=True)[0] < .95
    assert wilson(0, 480, one_sided=True)[1] < .05
    assert wilson(24, 480, one_sided=True)[1] > .05


def test_repeated_answers_do_not_inflate_independent_sample_size():
    independent = cluster_ci([0, 1, 0, 1], ['a', 'b', 'c', 'd'])
    duplicated = cluster_ci([0, 0, 1, 1, 0, 0, 1, 1], ['a', 'a', 'b', 'b', 'c', 'c', 'd', 'd'])
    assert duplicated == independent
    with pytest.raises(ValueError):
        cluster_ci([0, 1], ['a'])


def test_policy_abstains_on_uncertainty_and_incomplete_cost():
    cheap = dict(accuracy_lower95=.94, task_accuracy=.96, field_accuracy=.99, deadline_miss_upper95=.02,
                 p95_latency_ms=2000, cost_per_1k_tasks=.1, incomplete_cost_count=0)
    strong = {**cheap, 'accuracy_lower95': .98, 'cost_per_1k_tasks': .3}
    assert cheapest_qualifying({'cheap': cheap, 'strong': strong}, accuracy=.95, deadline_ms=4000) == 'strong'
    assert cheapest_qualifying({'cheap': cheap}, accuracy=.95, deadline_ms=4000) is None
    assert cheapest_qualifying({'strong': {**strong, 'incomplete_cost_count': 1}}, accuracy=.95, deadline_ms=4000) is None
