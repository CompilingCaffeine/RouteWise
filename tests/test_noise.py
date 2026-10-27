"""Writing-noise measurement and the label-preserving channel, tested only with synthetic conversations."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from random import Random

import pytest
from test_mailbox_prep import generated_values, private_root, run_cli, scratch_repo, synthetic_archive

from routewise import mailbox_prep, noise, noise_study
from routewise.config import REPO_ROOT
from routewise.private_mode import SafeError
from routewise.runlog import read_jsonl, sha256_file
from routewise.textfacts import analyze, parse_messages

WORDS = mailbox_prep.dictionary()
BASE = [r["input"] for r in read_jsonl(REPO_ROOT / "data/v5/confirm/examples.jsonl")]


def conversation(*messages, start="2026-03-02 09:00", gap=10):
    t0 = datetime.fromisoformat(start)
    return "\n".join(f"[{t0 + timedelta(minutes=gap * i):%Y-%m-%d %H:%M}] {role}: {text}"
                     for i, (role, text) in enumerate(messages))


def loud(base, rate=0.4, misspelling=0.1):
    """The clean profile with every feature raised, so each edit kind occurs often."""
    p = copy.deepcopy(base)
    for role in noise.ROLES:
        for name, f in p["roles"][role]["features"].items():
            f["rate"], f["writer_var"] = (misspelling, misspelling / 20) if name == "misspelling" else (rate, rate / 10)
        for group in ("misspelling_by_length", "abbreviations"):
            for cell in p["roles"][role][group].values():
                cell["k"], cell["n"] = 4, 10
    p["misspellings"] = [["recieve", "receive", 9]]
    return p


@pytest.fixture(scope="module")
def clean_profile():
    return noise.measure(BASE, WORDS)


def test_single_edits_are_classified_by_kind_and_position():
    assert noise.classify("refnd", "refund") == ("deletion", "interior")
    assert noise.classify("reffund", "refund") == ("insertion_double", "interior")
    assert noise.classify("refundx", "refund") == ("insertion_other", "last")
    assert noise.classify("rwefund", "refund") == ("insertion_other", "interior")
    assert noise.classify("reufnd", "refund") == ("transposition", "interior")
    assert noise.classify("refunf", "refund") == ("substitution_adjacent", "last")
    assert noise.classify("refanl", "refund") is None
    assert noise.classify("tefund", "refund") == ("substitution_adjacent", "first")


def test_inflections_of_dictionary_words_are_known():
    for word in ("refunded", "processing", "stopped", "replies", "receives", "quickly"):
        assert noise.known(word, WORDS), word
    assert not noise.known("recieve", WORDS) and not noise.known("refnd", WORDS)


def test_measure_counts_casing_punctuation_contractions_abbreviations_and_splits():
    text = conversation(
        ("Customer", "hi i dont have my refund yet, can u check pls"),
        ("Customer", "its been a while!!"),
        ("Agent", "Hello. We're checking now, thanks for waiting."),
        ("Agent", "Your refund was issued today. You'll see it soon."), gap=3)
    p = noise.measure([text], WORDS)
    c, a = p["roles"]["Customer"]["features"], p["roles"]["Agent"]["features"]
    assert c["message_all_lowercase"]["k"] == 2 and c["message_all_lowercase"]["n"] == 2
    assert c["apostrophe_drop"]["k"] == 1 and c["apostrophe_drop"]["n"] == 1
    assert p["roles"]["Customer"]["abbreviations"]["you"]["k"] == 1
    assert p["roles"]["Customer"]["abbreviations"]["please"]["k"] == 1
    assert c["no_terminal_punctuation"]["k"] == 1 and c["repeated_punctuation"]["k"] == 1
    assert c["split_message"]["k"] == a["split_message"]["k"] == 1 and c["split_message"]["n"] == 2
    assert noise.measure([text.replace(":03]", ":08]")], WORDS)["roles"]["Customer"]["features"]["split_message"]["k"] == 0
    assert a["message_lowercase_start"]["k"] == 0 and a["message_lowercase_start"]["n"] == 2
    assert a["straight_apostrophe"]["k"] == a["straight_apostrophe"]["n"] == 2


def test_misspellings_need_a_much_more_frequent_correction_and_skip_names_and_acronyms():
    good = "Please let me know when I receive the receipt and when you receive the refund details."
    bad = conversation(("Customer", "I did not recieve it from Zanthor, the LSAT office or lsat staff."),
                       ("Agent", "Sorry about that."))
    texts = [conversation(("Customer", good), ("Agent", good)) for _ in range(16)] + [bad] * 6
    p = noise.measure(texts, WORDS)
    assert ["recieve", "receive", 6] in p["misspellings"]
    assert all(row[0] not in ("lsat", "zanthor") for row in p["misspellings"])  # acronym and mid-sentence name
    assert p["roles"]["Customer"]["features"]["misspelling"]["k"] == 6
    assert p["roles"]["Customer"]["typo_kinds"]["transposition"] == 6
    assert noise.measure(texts, WORDS, frozenset({"recieve"}))["misspellings"] == []
    assert noise.measure(texts[:20], WORDS)["misspellings"] == []  # four conversations are too few to publish


def test_real_words_missing_from_the_dictionary_and_mixed_case_names_are_never_misspellings():
    hear = "Happy to hear that, glad to hear it and sorry to hear about the phone, phone or phone."
    texts = [conversation(("Customer", hear), ("Agent", hear)) for _ in range(12)]
    texts += [conversation(("Customer", "I heard nothing, my iPhone broke and the iphone held the code. dont worry"),
                           ("Agent", "Thanks for the help.")) for _ in range(6)]
    p = noise.measure(texts, WORDS)
    assert p["misspellings"] == [] and p["roles"]["Customer"]["features"]["misspelling"]["k"] == 0
    assert noise.publication_problems({**p, "misspellings": [["heard", "hear", 38]]}, WORDS)


def test_between_writer_variance_separates_consistent_and_mixed_writers():
    uniform = noise._feature([(5, 100)] * 20)
    mixed = noise._feature([(0, 100)] * 10 + [(10, 100)] * 10)
    assert uniform["rate"] == mixed["rate"] == 0.05
    assert uniform["writer_var"] == 0 and mixed["writer_var"] > 0.002


def test_injection_keeps_every_label_invariant_and_writes_only_non_word_misspellings(clean_profile):
    profile, kinds = loud(clean_profile), set()
    for seed in range(3):
        for text in BASE[::3]:
            noisy, log, splits = noise.inject(text, profile, clean_profile, Random(f"{seed}:{text[:30]}"), WORDS)
            assert noise.invariants(text, noisy, splits) == []
            assert [(m.amount, m.currency) for m in analyze(text).money] == \
                   [(m.amount, m.currency) for m in analyze(noisy).money]
            for clean_msg, noisy_msg in zip(parse_messages(text), parse_messages(noisy)):
                if clean_msg.speaker == "System":
                    assert noisy_msg.text == clean_msg.text
            for edit in log:
                kinds.add(edit["kind"])
                if edit["kind"] == "misspelling":
                    assert not noise.known(edit["after"].lower(), WORDS)
    assert kinds >= {"misspelling", "apostrophe_drop", "abbreviation", "message_all_lowercase", "split_message",
                     "no_terminal_punctuation", "comma_no_space", "repeated_word", "emoticon"}


def test_injection_is_deterministic_and_adds_nothing_where_the_workload_matches_the_clean_text(clean_profile):
    profile = loud(clean_profile)
    text = BASE[7]
    first = noise.inject(text, profile, clean_profile, Random("s"), WORDS)
    assert first == noise.inject(text, profile, clean_profile, Random("s"), WORDS)
    same = copy.deepcopy(clean_profile)
    for role in noise.ROLES:
        for f in same["roles"][role]["features"].values():
            f["writer_var"] = 0.0
    assert all(noise.inject(t, same, same, Random(1), WORDS)[0] == t for t in BASE[:100])


def test_splits_keep_the_date_and_never_touch_protected_tokens(clean_profile):
    profile = loud(clean_profile, rate=1.0)
    text = conversation(("Customer", "I paid for item two, A$29.99 in total. Please send it back."),
                        ("Agent", "Done. The refund will be issued tomorrow."), start="2026-03-02 23:59")
    noisy, log, splits = noise.inject(text, profile, clean_profile, Random(3), WORDS)
    assert noise.invariants(text, noisy, splits) == []
    assert "two,A$29.99" not in noisy  # a removed space never glues a word to an amount
    assert all(m.when.date().isoformat() == "2026-03-02" for m in parse_messages(noisy)[:1])
    assert 0 not in splits  # 23:59 + 1 minute is another day


def test_invariants_catch_a_changed_amount_role_or_reference():
    clean = conversation(("Customer", "Refund ORD-12345 for $40.00 please."), ("Agent", "Sure."))
    assert noise.invariants(clean, clean.replace("$40.00", "$40.50"), []) != []
    assert noise.invariants(clean, clean.replace("ORD-12345", "ORD-12346"), []) != []
    assert noise.invariants(clean, clean.replace("] Agent:", "] Customer:"), []) != []
    assert noise.invariants(clean, clean.replace("please", "pls"), []) == []


def test_publication_check_rejects_values_that_are_not_counts_or_safe_pairs(clean_profile):
    ok = {**clean_profile, "misspellings": [["recieve", "receive", 5]]}
    assert noise.publication_problems(ok, WORDS) == []
    for bad in ([["recieve", "receive", 4]], [["receive", "receive", 9]], [["zxqv", "zxqvw", 9]],
                [["Recieve", "receive", 9]]):
        assert noise.publication_problems({**ok, "misspellings": bad}, WORDS)
    leaked = copy.deepcopy(ok)
    leaked["roles"]["Customer"]["features"]["misspelling"]["note"] = "a customer wrote this"
    assert noise.publication_problems(leaked, WORDS)
    assert noise.publication_problems({**ok, "examples": []}, WORDS)


def test_profile_stage_prints_aggregates_only_and_publishes_only_an_approved_hash(tmp_path, monkeypatch):
    v = generated_values(31)
    scratch_repo(tmp_path, monkeypatch)
    root = private_root(tmp_path)
    mbox = synthetic_archive(root, v)
    code, out, err = run_cli(["noise", "profile", "--mbox", str(mbox), "--private-root", str(root)])
    assert code == 0, err
    for canary in [v[k] for k in ("customer", "agent", "email", "phone", "street", "order", "file")] + \
            v["customer"].split() + ["NOTE-CANARY", "refund of", "money back"]:
        assert canary not in out and canary not in err, canary
    run = next(p for p in (root / "results").iterdir() if p.name.startswith("noiseprofile-"))
    path = run / "noise-profile.json"
    assert path.stat().st_mode & 0o077 == 0
    status = json.loads((run / "PROFILE-STATUS.json").read_text())
    assert status["approved"] is False and status["sha256"] == sha256_file(path)
    profile = json.loads(path.read_text())
    assert profile["conversations"] == 5 and v["customer"] not in path.read_text()
    monkeypatch.setattr(noise_study, "DATA", tmp_path / "v6")
    monkeypatch.setattr(noise_study, "PROFILE", tmp_path / "v6" / "noise-profile.json")
    code, _, err = run_cli(["noise", "publish", "--profile", str(path), "--approve", "0" * 64, "--reviewer", "R"])
    assert code == 1 and "does not match" in err and not noise_study.PROFILE.exists()
    code, _, err = run_cli(["noise", "publish", "--profile", str(path), "--approve", status["sha256"],
                            "--reviewer", "Reviewer"])
    assert code == 0, err
    published = json.loads(noise_study.PROFILE.read_text())
    assert published["publication"]["private_sha256"] == status["sha256"]
    assert json.loads((run / "PROFILE-STATUS.json").read_text())["approved"] is True


def test_profile_stage_refuses_an_archive_outside_the_private_raw_folder(tmp_path):
    root = private_root(tmp_path)
    stray = tmp_path / "Intercom.mbox"
    stray.write_text("")
    with pytest.raises(SafeError):
        noise_study.profile(stray, root)


def test_build_refuses_until_the_protocol_and_profile_are_committed(tmp_path, monkeypatch):
    monkeypatch.setattr(noise_study, "DATA", tmp_path / "v6")
    monkeypatch.setattr(noise_study, "PROFILE", tmp_path / "v6" / "noise-profile.json")
    with pytest.raises(SafeError):
        noise_study.build()
    assert not (tmp_path / "v6").exists()


def test_the_noise_base_is_the_sealed_m6_confirmation_set():
    examples = noise_study.base_examples()
    assert len(examples) == 679 and all(not e.id.endswith(noise_study.NOISY) for e in examples)
    assert Path(noise_study.BASE / "splits.json").exists()


def test_achieved_rates_track_the_workload_rate_including_features_with_few_editable_spots(clean_profile):
    target = copy.deepcopy(clean_profile)
    raised = ("message_all_lowercase", "no_terminal_punctuation", "split_message", "comma_no_space",
              "message_lowercase_start", "pronoun_i_lowercase", "emoticon")
    for role in noise.ROLES:
        for name in raised:
            target["roles"][role]["features"][name].update(rate=0.2, writer_var=0.0)
    noisy = [noise.inject(t, target, clean_profile, Random(f"c:{i}"), WORDS)[0] for i, t in enumerate(BASE)]
    achieved = noise.measure(noisy, WORDS)["roles"]["Customer"]["features"]
    for name in raised:
        assert 0.14 <= achieved[name]["rate"] <= 0.26, (name, achieved[name]["rate"])


def test_a_sentence_initial_pronoun_counts_only_as_a_lowercase_i():
    p = noise.measure([conversation(("Customer", "i paid. i want it back. it was broken. Thanks"),
                                    ("Agent", "Sure."))], WORDS)["roles"]["Customer"]["features"]
    assert p["pronoun_i_lowercase"]["k"] == 2
    assert p["message_lowercase_start"]["n"] == 0
    assert p["sentence_lowercase_start"]["k"] == 1 and p["sentence_lowercase_start"]["n"] == 2
