import json
from collections import Counter

from routewise import dataset
from routewise.schema import OUTPUT_MODELS


def test_generation_is_deterministic_and_matches_committed_data():
    regenerated = "".join(e.model_dump_json() + "\n" for e in dataset.generate())
    committed = (dataset.DATA_DIR / dataset.VERSION / "examples.jsonl").read_text()
    assert regenerated == committed, "data/v1 is stale: run `routewise data`"


def test_labels_are_valid_outputs_and_ids_unique():
    examples = dataset.load_examples()
    assert len({e.id for e in examples}) == len(examples) == 120
    for e in examples:
        OUTPUT_MODELS[e.task].model_validate(e.expected)


def test_split_is_disjoint_complete_and_stratified():
    splits = json.loads((dataset.DATA_DIR / dataset.VERSION / "splits.json").read_text())
    tune, heldout = set(splits["tune"]), set(splits["heldout"])
    assert not tune & heldout
    assert tune | heldout == {e.id for e in dataset.load_examples()}

    def strata(ids):
        return Counter((e.task, e.difficulty) for e in dataset.load_examples() if e.id in ids)

    t, h = strata(tune), strata(heldout)
    for key in t | h:
        assert abs(t[key] - h[key]) <= 1, key


def test_every_category_is_represented_in_both_splits():
    for name in ("tune", "heldout"):
        cats = {e.expected["category"] for e in dataset.load_split(name) if e.task == "classification"}
        assert len(cats) == 7, name
