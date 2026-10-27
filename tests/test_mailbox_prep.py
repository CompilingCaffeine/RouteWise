"""Offline Intercom-mailbox preparation, tested only with synthetic notifications and generated personal values."""

import contextlib
import io
import json
import random
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import format_datetime
from zoneinfo import ZoneInfo

import pytest

from routewise import cli, mailbox_prep as mp
from routewise.config import REPO_ROOT
from routewise.private_mode import SafeError

LA = ZoneInfo("America/Los_Angeles")
POLICY = default_policy.clone(linesep="\n")
_SYLLABLES = ("ka", "lo", "ve", "ri", "sa", "mon", "tel", "dra", "qui", "zen", "fa", "bor", "ny", "ush", "pel", "tor")


def fake_name(rng):
    return " ".join("".join(rng.choice(_SYLLABLES) for _ in range(3)).capitalize() for _ in range(2))


def luhn_card(rng):
    digits = [4] + [rng.randint(0, 9) for _ in range(14)]
    for check in range(10):
        if mp._luhn("".join(map(str, digits + [check]))):
            return "".join(map(str, digits + [check]))


def part(kind, author="", when=None, body="", files=(), emails=()):
    return {"kind": kind, "author": author, "when": when, "body": body, "files": files, "emails": emails}


def _stamp(w):
    return f"{w.day} {w:%b}, {w.hour % 12 or 12}:{w:%M} {'pm' if w.hour >= 12 else 'am'}"


def notification(conv, thread, msgid, parts, *, sent=None, reason="assigned", customer="", plain_extra="",
                 html_extra="", headers=None):
    """An Intercom-style teammate notification; `parts` newest first, times as naive Los Angeles wall times."""
    newest = next(p["when"] for p in parts if p["when"])
    sent = sent or newest.replace(tzinfo=LA) + timedelta(seconds=40)
    classes = {"user": "conversation-part user", "admin": "conversation-part admin",
               "note": "conversation-part admin note", "action": "conversation-part action admin"}
    html, plain = [], ["Reply above to reply", ""]
    for p in parts:
        meta = (f'<div class="conversation-part-metadata">{p["author"]} on {_stamp(p["when"])}</div>'
                if p["kind"] != "action" else "")
        files = "".join(f'<div class="attachments intercom-attachments"><a href="https://files.invalid/x">'
                        f'<span class="label">{f}</span></a></div>' for f in p["files"])
        attribution = "".join(f'<div class="attribution"><span class="label">EMAIL</span> {e}</div>' for e in p["emails"])
        html.append(f'<div class="{classes[p["kind"]]}"><div class="conversation-part-avatar-wrapper">'
                    f'<img class="conversation-part-avatar" src="https://avatars.invalid/a.png"></div>{meta}'
                    f'<div class="conversation-part-body">{p["body"]}</div>{files}{attribution}</div>')
        if p["kind"] in ("user", "admin") and p["when"]:
            w = p["when"]
            plain.append(f'> On {w:%a}, {w:%b} {w.day}, {w.year} at {w.hour % 12 or 12}:{w:%M}\u202f'
                         f'{"PM" if w.hour >= 12 else "AM"}, "{p["author"]}" <someone@mail.invalid> wrote:')
            plain.append("> " + re.sub(r"<[^>]+>", " ", p["body"]))
    msg = EmailMessage(policy=POLICY)
    msg["From"] = "Synthetic Actor via Intercom <a1b2c3d4e5@intercom-mail.invalid>"
    msg["To"] = "Owner Person <owner@store.invalid>"
    msg["Delivered-To"] = "owner@store.invalid"
    msg["Reply-To"] = "a1b2c3d4e5@intercom-mail.invalid"
    msg["Subject"] = f"Store: Someone replied to a conversation with {customer or 'Visitor'}"
    msg["Date"] = format_datetime(sent.astimezone(ZoneInfo("UTC")))
    msg["Message-ID"] = f"<{msgid}@notify.invalid>"
    msg["References"] = f"<conv-root-{conv}@notify.invalid> <{msgid}-parent@notify.invalid>"
    msg["In-Reply-To"] = f"<{msgid}-parent@notify.invalid>"
    msg["X-GM-THRID"] = thread
    msg["X-Gmail-Labels"] = "Archived,Intercom"
    msg["X-Intercom-App-Id"] = "synthapp42"
    msg["X-Intercom-Notification-Reason"] = reason
    msg["X-Mailgun-Variables"] = json.dumps({"app_id": "synthapp42", "conversation_id": conv,
                                             "intercom_message_id": 7})
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content("\n".join(plain) + plain_extra + "\n")
    msg.add_alternative(f'<html><body><div class="mail_body">{"".join(html)}</div>{html_extra}</body></html>',
                        subtype="html")
    return msg.as_bytes(policy=POLICY)


def write_mbox(path, messages):
    with path.open("wb") as f:
        for i, raw in enumerate(messages):
            f.write(f"From 17{i:017d}@xxx Mon Oct 06 12:00:00 +0000 2025\n".encode())
            f.write(re.sub(rb"(?m)^(>*From )", rb">\1", raw).rstrip(b"\n") + b"\n\n")
    return path


def config(**extra):
    return {"render_timezone": "America/Los_Angeles", "authors": {},
            "store_timezone": {"value": "America/Los_Angeles", "confirmed": True}} | extra


def archive_of(tmp_path, messages):
    return mp.load_archive(write_mbox(tmp_path / "a.mbox", messages))


# ---------------------------------------------------------------- archive and structure


def test_mbox_stream_unescapes_from_lines_and_bounds_message_size(tmp_path, monkeypatch):
    t0 = datetime(2025, 10, 6, 9, 0)
    body = part("user", "Kalo Vemon", t0, "<p>hello</p>")
    small = notification("101", "9001", "m1", [body], plain_extra="\nFrom here on it is fine")
    large = notification("102", "9002", "m2", [body], plain_extra="\n" + "x" * 5000)
    path = write_mbox(tmp_path / "a.mbox", [small, large])
    monkeypatch.setattr(mp, "MAX_MESSAGE_BYTES", len(small) + 200)
    out = list(mp.iter_mbox(path))
    assert [o[0] for o in out] == [0, 1]
    assert out[0][1] is not None and b"\nFrom here on it is fine" in out[0][1]
    assert out[1][1] is None and out[1][2] > len(small)


def test_conversation_is_rebuilt_across_notifications_and_gmail_threads(tmp_path):
    t0 = datetime(2025, 10, 6, 9, 5)
    ask = part("user", "Kalo Vemon", t0, "<p>Can I get a refund for my course?</p>")
    reply = part("admin", "Dra Quibor", t0 + timedelta(hours=2), "<p>Yes, we issued it today.</p>")
    note = part("note", "Dra Quibor", t0 + timedelta(hours=1), "<p>INTERNAL-CANARY approve</p>")
    event = part("action", body="<p>Dra Quibor assigned this conversation</p>")
    first = notification("201", "9101", "n1", [ask], customer="Kalo Vemon")
    second = notification("201", "9102", "n2", [reply, event, note, ask], customer="Kalo Vemon")
    archive = archive_of(tmp_path, [first, second])
    t = mp.reconstruct("201", archive, config())
    assert not t.quarantine
    assert [(x.role, f"{x.when:%Y-%m-%d %H:%M}") for x in t.turns] == [
        ("Customer", "2025-10-06 09:05"), ("Agent", "2025-10-06 11:05")]
    assert t.excluded == Counter(internal_notes=1, events=1)
    assert t.threads == {"9101", "9102"} and t.flags["year_confirmed"] == 2
    text = mp.format_turns(t.turns, [x.text for x in t.turns])
    assert "INTERNAL-CANARY" not in text and "assigned" not in text


def test_a_snapshot_ending_with_the_customer_is_quarantined_unless_kept(tmp_path):
    t0 = datetime(2025, 10, 6, 9, 5)
    reply = part("admin", "Dra Quibor", t0, "<p>We can help.</p>")
    follow_up = part("user", "Kalo Vemon", t0 + timedelta(hours=1), "<p>So when is my refund issued?</p>")
    archive = archive_of(tmp_path, [notification("251", "9151", "k1", [follow_up, reply])])
    assert "may_lack_final_store_reply" in mp.reconstruct("251", archive, config()).quarantine
    kept = mp.reconstruct("251", archive, config(keep_customer_ending_snapshots=True))
    assert not kept.quarantine and kept.flags["ends_with_customer_message"] == 1


def test_only_the_html_structure_is_used_never_both_alternatives(tmp_path):
    t0 = datetime(2025, 10, 7, 8, 0)
    raw = notification("301", "9201", "p1", [part("user", "Telu Shpel", t0, "<p>Where is my order?</p>")],
                       plain_extra="\nPLAIN-ONLY-CANARY sentence")
    t = mp.reconstruct("301", archive_of(tmp_path, [raw]), config())
    assert [x.text for x in t.turns] == ["Where is my order?"]


def test_part_seen_only_in_an_older_notification_is_merged_in_time_order(tmp_path):
    t0 = datetime(2025, 10, 8, 10, 0)
    a = part("user", "Zen Fa", t0, "<p>first</p>")
    b = part("admin", "Dra Quibor", t0 + timedelta(minutes=30), "<p>second</p>")
    c = part("user", "Zen Fa", t0 + timedelta(hours=1), "<p>third</p>")
    older = notification("401", "9301", "q1", [b, a])
    newer = notification("401", "9301", "q2", [c, a], sent=(t0 + timedelta(hours=1, seconds=30)).replace(tzinfo=LA))
    t = mp.reconstruct("401", archive_of(tmp_path, [older, newer]), config())
    assert [x.text for x in t.turns] == ["first", "second", "third"]
    assert t.flags["parts_from_older_notifications"] == 1


def test_duplicate_message_ids_are_dropped_or_quarantined(tmp_path):
    t0 = datetime(2025, 10, 9, 10, 0)
    one = notification("501", "9401", "dup", [part("user", "Ri Sa", t0, "<p>hello</p>")])
    other = notification("502", "9402", "dup", [part("user", "Ri Sa", t0, "<p>different</p>")])
    archive = archive_of(tmp_path, [one, one, other])
    assert archive.counts["duplicate_message_id_identical"] == 1
    assert archive.counts["duplicate_message_id_conflict"] == 1
    assert "duplicate_message_id_conflict" in mp.reconstruct("501", archive, config()).quarantine


def test_unusable_messages_are_quarantined_with_codes_and_taint_their_conversation(tmp_path, monkeypatch):
    t0 = datetime(2025, 10, 10, 10, 0)
    good = notification("601", "9501", "g1", [part("user", "Mon Tel", t0, "<p>hi</p>")])
    encrypted = (b"Message-ID: <enc@notify.invalid>\nDate: Fri, 10 Oct 2025 17:00:00 +0000\n"
                 b"X-Intercom-App-Id: synthapp42\nX-Mailgun-Variables: {\"conversation_id\": \"601\"}\n"
                 b"Content-Type: multipart/encrypted; protocol=\"application/pgp-encrypted\"; boundary=\"b\"\n\n"
                 b"--b\nContent-Type: application/pgp-encrypted\n\nVersion: 1\n--b\n"
                 b"Content-Type: application/octet-stream\n\nopaque\n--b--\n")
    not_intercom = b"Message-ID: <x@mail.invalid>\nDate: Fri, 10 Oct 2025 17:00:00 +0000\nSubject: hi\n\nbody\n"
    no_html = (b"Message-ID: <y@notify.invalid>\nDate: Fri, 10 Oct 2025 17:00:00 +0000\nX-Intercom-App-Id: a\n"
               b"X-Mailgun-Variables: {\"conversation_id\": \"602\"}\nContent-Type: text/plain\n\nonly text\n")
    garbage = b"\x00\x01\x02 not a message at all"
    truncated = notification("603", "9503", "t1", [part("user", "Mon Tel", t0, "<p>hi</p>")],
                             html_extra="<p>View earlier messages in Intercom</p>")
    big = notification("604", "9504", "b1", [part("user", "Mon Tel", t0, "<p>" + "y" * 400 + "</p>")])
    monkeypatch.setattr(mp, "MAX_PART_CHARS", 300)
    archive = archive_of(tmp_path, [good, encrypted, not_intercom, no_html, garbage, truncated, big])
    assert Counter(r for _, r in archive.quarantined) == Counter(
        encrypted=1, not_intercom_notification=1, no_html_alternative=1, malformed_mime=1, oversized_part=1)
    assert "sibling_message_quarantined" in mp.reconstruct("601", archive, config()).quarantine
    assert "truncated_history" in mp.reconstruct("603", archive, config()).quarantine
    assert {"602", "604"} <= archive.tainted and "602" not in archive.notifications


# ---------------------------------------------------------------- time


def test_render_zone_dst_boundary_year_boundary_and_ambiguous_hour(tmp_path):
    msgs = []
    for i, w in enumerate([datetime(2025, 10, 31, 9, 0), datetime(2025, 11, 1, 15, 0), datetime(2025, 11, 3, 9, 0),
                           datetime(2025, 11, 4, 16, 30), datetime(2025, 12, 1, 10, 0)]):
        msgs.append(notification(f"70{i}", f"960{i}", f"z{i}", [part("user", "Fa Bor", w, "<p>x</p>")]))
    dec31 = part("user", "Pel Tor", datetime(2025, 12, 31, 23, 50), "<p>last year</p>")
    jan2 = part("admin", "Dra Quibor", datetime(2026, 1, 2, 9, 0), "<p>new year</p>")
    msgs.append(notification("710", "9610", "y1", [jan2, dec31]))
    ambiguous = part("user", "Ush Ny", datetime(2025, 11, 2, 1, 30), "<p>during the repeated hour</p>")
    msgs.append(notification("711", "9611", "y2", [ambiguous], sent=datetime(2025, 11, 2, 9, 31, tzinfo=ZoneInfo("UTC"))))
    archive = archive_of(tmp_path, msgs)
    zone = mp.detect_render_zone(archive)
    assert zone["zone"] == "America/Los_Angeles" and zone["share"] >= 0.8
    assert {"America/Vancouver", "America/Tijuana"} >= set(zone["ties"])
    t = mp.reconstruct("710", archive, config())
    assert [f"{x.when:%Y-%m-%d %H:%M}" for x in t.turns] == ["2025-12-31 23:50", "2026-01-02 09:00"]
    assert mp.reconstruct("711", archive, config()).flags["dst_ambiguous_time"] == 1
    eastern = mp.reconstruct("710", archive, config(store_timezone={"value": "America/New_York", "confirmed": True}))
    assert [f"{x.when:%Y-%m-%d %H:%M}" for x in eastern.turns] == ["2026-01-01 02:50", "2026-01-02 12:00"]


# ---------------------------------------------------------------- cleaning


def test_quoted_history_is_removed_only_when_it_repeats_earlier_turns():
    history = "\nWe have processed your refund of $40.00 and it will be issued tomorrow morning for you."
    repeated = ("Thanks, that works.\nOn Mon, Oct 6, 2025 at 9:00 AM Store Team <help@store.invalid> wrote:\n"
                "> We have processed your refund of $40.00 and it will be issued tomorrow morning for you.")
    flags = Counter()
    assert mp.clean(repeated, history, flags) == "Thanks, that works."
    unknown = ("Thanks.\n> Correction: the refund was actually issued on March 7 instead of the date we "
               "said before, sorry about that.")
    assert mp.clean(unknown, history, flags) == unknown
    assert flags == Counter(quoted_history_removed=1, quote_kept_unverified=1)


def test_forwarded_content_is_kept_and_flagged_and_signatures_are_trimmed_conservatively():
    flags = Counter()
    forwarded = ("See below.\n---------- Forwarded message ---------\nFrom: Someone <s@mail.invalid>\n"
                 "Date: Mon, Oct 6, 2025\nSubject: receipt\nTo: me@mail.invalid\n\nYour total was $25.00.")
    assert mp.clean(forwarded, "", flags) == "See below.\n[Forwarded message]\nYour total was $25.00."
    assert mp.clean("All set.\nBest,\nDra Quibor\nSupport Lead", "", flags) == "All set."
    keeps = "All set.\nThanks,\nP.S. the refund is issued tomorrow"
    assert mp.clean(keeps, "", flags) == keeps
    assert mp.clean("Sounds good\nSent from my iPhone", "", flags) == "Sounds good"
    assert flags["forwarded_content"] == 1 and flags["signature_removed"] == 1


# ---------------------------------------------------------------- de-identification


def generated_values(seed=7):
    rng = random.Random(seed)
    return {"customer": fake_name(rng), "agent": fake_name(rng), "friend": fake_name(rng).split()[0],
            "email": f"{fake_name(rng).split()[0].lower()}.{rng.randint(10, 99)}@mail.invalid",
            "phone": f"({rng.randint(201, 989)}) {rng.randint(200, 999)}-{rng.randint(1000, 9999)}",
            "card": luhn_card(rng), "street": f"{rng.randint(10, 9999)} {fake_name(rng).split()[0]} Street",
            "zip": f"CA {rng.randint(90001, 96162)}", "ip": f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.7",
            "account": f"{rng.randint(1000000, 9999999)}", "order": f"ORD-{rng.randint(10000, 99999)}",
            "token_url": f"https://pay.store.invalid/r/{rng.getrandbits(64):x}?t=abc",
            "file": f"{fake_name(rng).split()[0]}_receipt.pdf", "ssn": f"{rng.randint(100, 899)}-12-{rng.randint(1000, 9999)}"}


def test_redaction_replaces_generated_personal_values_and_keeps_refund_facts():
    v = generated_values()
    texts = [f"Hi, I'm {v['customer']}. Please refund $129.99 for order {v['order']} within 3-5 business days.",
             f"Card {v['card']}, phone {v['phone']}, email {v['email']}, mail to {v['street']}, Springfield, {v['zip']}.",
             f"Student ID {v['account']}; SSN {v['ssn']}; IP {v['ip']}; link {v['token_url']}; my friend "
             f"{v['friend']} at University of Somewhere Far said ask @{v['friend'].lower()}99.",
             f"Hi {v['customer'].split()[0]}, refund for {v['order']} issued 2026-03-02 (March 5 at the latest). "
             f"Best, {v['agent'].split()[0]}"]
    surrogates = mp.Surrogates(mp.identifiers(" ".join(texts)), random.Random(1))
    red = mp.Redactor("c1", [v["customer"]], [v["agent"]], [v["email"]], [], surrogates)
    red.learn(texts)
    out = [red(x) for x in texts]
    joined = "\n".join(out)
    for key in ("customer", "agent", "email", "phone", "card", "street", "zip", "ip", "account", "order",
                "token_url", "ssn"):
        assert v[key] not in joined, key
    for token in v["customer"].split() + v["agent"].split() + [v["friend"]]:
        assert not re.search(rf"\b{token}\b", joined), token
    for kept in ("$129.99", "3-5 business days", "2026-03-02", "March 5"):
        assert kept in joined
    assert out[0].count("[CUSTOMER]") == 1 and out[3].startswith("Hi [CUSTOMER],")
    surrogate = re.search(r"ORD-\d{5}", out[0]).group()
    assert surrogate != v["order"] and surrogate in out[3]
    assert {"[CARD]", "[PHONE]", "[EMAIL]", "[ADDRESS]", "[ACCOUNT_ID]", "[SSN]", "[IP]", "[URL]", "[SCHOOL]",
            "[HANDLE]"} <= set(re.findall(r"\[[A-Z_]+\]", joined))
    assert not mp.payload_problems(joined, {"name": {v["customer"], v["agent"]}, "email": {v["email"]},
                                            "identifier": {v["order"], v["account"]}}, red.sentinels)


def test_names_split_across_lines_or_spaces_or_other_cases_map_to_one_placeholder():
    v = generated_values(17)
    first, last = v["customer"].split()
    red = mp.Redactor("c2", [v["customer"]], [], [], [], mp.Surrogates(set(), random.Random(2)))
    out = red(f"{first}\n{last} wrote, then {first.upper()}  {last.upper()} and {first.lower()} again")
    assert first not in out and last not in out and first.lower() not in out and first.upper() not in out
    assert set(re.findall(r"\[[A-Z_]+\]", out)) == {"[CUSTOMER]"}


def test_surrogates_are_consistent_shape_preserving_and_collision_checked():
    rng = random.Random(3)
    probe = mp.Surrogates(set(), random.Random(3))
    first_draw = probe("c", "ORD-12345")
    s = mp.Surrogates({first_draw}, rng)
    value = s("c", "ORD-12345")
    assert value != first_draw and re.fullmatch(r"ORD-\d{5}", value) and value != "ORD-12345"
    assert s("c", "ord-12345") == value and s("other", "ORD-12345") != value
    with pytest.raises(SafeError):
        mp.Surrogates({str(d) for d in range(1, 10)}, random.Random(0))("c", "7")


def test_unsupported_currency_needs_an_amount_and_suitability_is_only_a_nomination():
    def fit(text):
        return mp.suitability(f"[2025-10-06 09:00] Customer: {text}")
    assert not fit("I will try again, see the php page or pay $40")["unsupported_currency"]
    assert fit("It cost ₹500")["unsupported_currency"] and fit("It cost 500 INR")["unsupported_currency"]
    assert fit("refund please")["refund_keyword"] and not fit("thanks")["refund_keyword"]


def test_authors_are_proposed_as_automated_only_on_strong_signals():
    human = {"replies": 400, "notes": 300, "cited_share": 0.0, "templated_share": 0.3}
    workflow = {"replies": 6000, "notes": 10, "cited_share": 0.0, "templated_share": 0.6}
    assert not mp.automated("Dra Quibor", human)
    assert mp.automated("Dra Quibor", workflow) and mp.automated("Fin", human)
    assert mp.automated("Helper", human | {"cited_share": 0.1})


def test_payload_check_reports_categories_never_values():
    v = generated_values(11)
    problems = mp.payload_problems(f"[2025-10-06 09:00] Customer: call {v['phone']} or {v['email']}",
                                   {"email": {v["email"]}}, [])
    assert problems["original_email"] == 1 and problems["detected_phone"] == 1 and problems["detected_email"] == 1
    assert all(v["phone"] not in k and v["email"] not in k for k in problems)


# ---------------------------------------------------------------- end to end


def private_root(tmp_path):
    root = tmp_path / "private"
    (root / "raw").mkdir(parents=True)
    root.chmod(0o700)
    (root / "raw").chmod(0o700)
    return root


def synthetic_archive(root, v):
    t0 = datetime(2026, 1, 12, 9, 0)
    customer, agent = v["customer"], v["agent"]
    convs = []
    ask = part("user", customer, t0, f"<p>Hello, I want a refund of $129.99 for order {v['order']}.</p>"
                                     f"<p>Call me at {v['phone']}. I live at {v['street']}.</p>",
               files=[v["file"]], emails=[v["email"]])
    note = part("note", agent, t0 + timedelta(minutes=20), "<p>NOTE-CANARY check the ledger</p>")
    reply = part("admin", agent, t0 + timedelta(minutes=40),
                 f"<p>Hi {customer.split()[0]}, your refund for {v['order']} will be issued tomorrow.</p>"
                 f"<p>Best,</p><p>{agent}</p>")
    convs.append(notification("8801", "9801", "e1", [ask], customer=customer))
    convs.append(notification("8801", "9802", "e2", [reply, part("action", body="<p>closed</p>"), note, ask],
                              customer=customer))
    for i, (ask_text, answer) in enumerate([("Can I get my money back? It was charged twice.",
                                             "We will refund it within 3-5 business days."),
                                            ("Is the refund window 30 days?", "Yes, refunds are available for 30 days."),
                                            ("When does the next course start?", "It starts on Monday."),
                                            ("Do you have weekend sessions?", "Yes, on Saturdays.")]):
        w = t0 + timedelta(days=i + 1)
        convs.append(notification(f"88{10 + i}", f"98{10 + i}", f"f{i}",
                                  [part("admin", agent, w + timedelta(hours=1), f"<p>{answer}</p>"),
                                   part("user", f"Cust Omer{i}", w, f"<p>{ask_text}</p>")]))
    convs.append(notification("8820", "9820", "g1", [part("user", "Lone Asker", t0, "<p>anyone there?</p>")]))
    path = write_mbox(root / "raw" / "Intercom.mbox", convs)
    path.chmod(0o600)
    return path


def scratch_repo(tmp_path, monkeypatch, planted=""):
    """A throwaway git repository for the leak scan, so the synthetic texts in this test file do not count."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("Scratch repository for the mailbox preparation test.\n" + planted)
    monkeypatch.setattr(mp, "REPO_ROOT", repo)
    return repo


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_prepare_writes_an_unapproved_private_batch_and_leaks_nothing(tmp_path, monkeypatch):
    v = generated_values(23)
    scratch_repo(tmp_path, monkeypatch)
    root = private_root(tmp_path)
    mbox = synthetic_archive(root, v)
    code, out, err = run_cli(["mailbox", "prepare", "--mbox", str(mbox), "--private-root", str(root),
                              "--size", "3", "--refund-share", "0.67", "--seed", "5"])
    assert code == 0, err
    canaries = [v[k] for k in ("customer", "agent", "email", "phone", "street", "order", "file")] + [
        "NOTE-CANARY", "8801", "9801", "synthapp42", "e1@notify.invalid", "store.invalid", "intercom-mail.invalid"]
    for c in canaries:
        assert c not in out and c not in err, c
    run = next((root / "results").iterdir())
    report = json.loads((run / "readiness.json").read_text())
    assert report["checks"]["passed"] and report["checks"]["repository_clean"]
    assert report["batch"]["records"] == 3 and report["batch"]["approved"] is False
    assert report["conversations"] == 6 and report["conversations_eligible"] == 5
    assert report["reasons"] == {"may_lack_final_store_reply": 1}
    batch = [json.loads(line) for line in (root / "deid-input" / run.name / "batch.jsonl").read_text().splitlines()]
    assert len(batch) == 3 and all(set(b) == {"id", "input"} for b in batch)
    assert all(re.fullmatch(r"MX-[A-Z2-9]{6}", b["id"]) for b in batch)
    payload = "\n".join(b["input"] for b in batch)
    for c in canaries + v["customer"].split() + v["agent"].split():
        assert c not in payload, c
    assert all(mp._LINE.match(line) for line in payload.splitlines())
    status = json.loads((run / "BATCH-STATUS.json").read_text())
    assert status["approved"] is False and status["destination"] is None
    selection = json.loads((run / "selection.json").read_text())
    assert selection["strata"] == {"refund_keyword": 3, "no_refund_keyword": 2} and selection["seed"] == 5
    config = json.loads((root / "mappings" / mp.CONFIG_NAME).read_text())
    assert config["store_timezone"]["confirmed"] is False and config["store_timezone"]["value"] == "America/Los_Angeles"
    assert all(entry["confirmed"] is False for entry in config["authors"].values())
    for d in ("mappings", "quarantine", "deid-input", "results"):
        for p in [root / d / run.name, *(root / d / run.name).rglob("*")]:
            assert p.stat().st_mode & 0o077 == 0, p
    assert "UNAPPROVED" in (run / "REVIEW-CHECKLIST.md").read_text()


def test_repository_scan_finds_a_planted_value_and_reports_only_its_location(tmp_path, monkeypatch):
    v = generated_values(23)
    scratch_repo(tmp_path, monkeypatch, planted=f"contact {v['email']}\n")
    root = private_root(tmp_path)
    mbox = synthetic_archive(root, v)
    code, out, err = run_cli(["mailbox", "prepare", "--mbox", str(mbox), "--private-root", str(root),
                              "--size", "3", "--refund-share", "0.67", "--seed", "5"])
    assert code == 0 and v["email"] not in out + err
    run = next((root / "results").iterdir())
    checks = json.loads((run / "readiness.json").read_text())["checks"]
    assert checks["repository_hits"] >= 1 and checks["repository_clean"] is False and checks["passed"] is True
    hits = (run / "logs" / "repository-hits.txt").read_text()
    assert "README.md" in hits and v["email"] not in hits


def test_unexpected_failures_show_only_their_type(tmp_path, monkeypatch):
    v = generated_values(29)
    root = private_root(tmp_path)
    mbox = synthetic_archive(root, v)

    def explode(path):
        raise ValueError(f"cannot parse {v['email']} {v['customer']}")

    monkeypatch.setattr(mp, "load_archive", explode)
    code, out, err = run_cli(["mailbox", "prepare", "--mbox", str(mbox), "--private-root", str(root)])
    assert code == 1 and "ValueError" in err
    assert v["email"] not in out + err and v["customer"] not in out + err
    logs = list((root / "results").glob("*/logs/error-*.txt"))
    assert logs and v["email"] in logs[0].read_text()


def test_private_root_must_be_outside_the_repository_and_owner_only(tmp_path):
    with pytest.raises(SafeError):
        mp.private_layout(REPO_ROOT / "private-root-test")
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(SafeError):
        mp.private_layout(shared)
    root = private_root(tmp_path)
    with pytest.raises(SafeError):
        mp.prepare(tmp_path / "elsewhere.mbox", root)
