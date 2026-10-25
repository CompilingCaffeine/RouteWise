import json

from routewise.factsheets import FAMILIES, sample_factsheets
from routewise.synth import clean_texts, message_times, verify, writer_messages

SHEETS = {s.id: s for s in sample_factsheets(per_family=3)}
REL = SHEETS["v2-05000"]  # relative_refund_date


def faithful(sheet):
    """A minimal conversation that says exactly what the plan says, with every required string."""
    return [f"{m.guidance}. " + "; ".join(m.must) for m in sheet.messages]


def test_every_family_samples_and_faithful_text_verifies():
    assert {s.family for s in SHEETS.values()} == set(FAMILIES)
    for sheet in SHEETS.values():
        texts = faithful(sheet)
        assert verify(sheet, texts) == [], (sheet.id, sheet.family, verify(sheet, texts))


def test_rendered_timestamps_match_the_plan():
    times = message_times(REL)
    assert times[0].date().isoformat() == REL.contact
    for t, m in zip(times, REL.messages):
        assert (t.date() - times[0].date()).days == m.day
    assert times == sorted(times)


def test_verifier_rejects_embellishment():
    texts = faithful(REL)
    assert any("missing" in p for p in verify(REL, [texts[0].replace("ORD-", "ORDER "), *texts[1:]]))
    assert any("unexpected money" in p for p in verify(REL, [texts[0] + " I also paid $12.00 shipping.", *texts[1:]]))
    assert any("time expression" in p for p in verify(REL, [texts[0] + " I sent it yesterday.", *texts[1:]]))
    assert any("unexpected dates" in p for p in verify(REL, [texts[0] + " It shipped on March 3.", *texts[1:]]))
    assert any("unexpected reference" in p for p in verify(REL, [texts[0] + " Ticket TCK-12345.", *texts[1:]]))
    assert verify(REL, texts[:-1])[0].startswith("expected")


def test_writer_prompt_lists_every_required_string_and_cleaning_strips_prefixes():
    content = writer_messages(REL)[1]["content"]
    for m in REL.messages:
        for s in m.must:
            assert json.dumps(s, ensure_ascii=False) in content
    cleaned = clean_texts(REL, ["Customer:  hello\nthere", "Agent: ok", "Agent: done", "Customer: thanks"][: len(REL.messages)])
    assert cleaned[0] == "hello there"
