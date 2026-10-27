"""Prepare an Intercom notification mailbox (MBOX) for a private, exploratory RouteWise batch, entirely offline.

The archive holds Intercom teammate notifications, not the participants' own emails. Each notification's HTML
lists the conversation's parts, newest first, typed by Intercom as a customer message (`user`), a teammate reply
(`admin`), an internal note (`admin note`) or an event (`action`). This module streams the archive, parses that
structure with the standard library (nothing is fetched), unions each conversation's parts across its
notifications and quarantines what it cannot reconstruct with confidence. It formats the conversations as
`[YYYY-MM-DD HH:MM] Customer|Agent|System: message` in the configured store timezone, de-identifies them with
local rules and private, reversible mappings, checks every payload for the original values it knows of, and
writes a small batch marked unapproved for a person to review.

Nothing here calls a network service or a model. The console shows counts and paths; everything else goes to
owner-only files under the private root: `raw/`, `mappings/`, `quarantine/`, `deid-input/` and `results/`.
Rules cannot prove that all personal data is gone, so every record still needs a person's review.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import functools
import hashlib
import json
import os
import random
import re
import secrets
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from routewise.config import REPO_ROOT
from routewise.private_mode import Needles, SafeError, _check_location, repository_files, session

ROOT_DIRS = ("raw", "mappings", "quarantine", "deid-input", "results")
SYNCED = ("Library/Mobile Documents", "Library/CloudStorage", "Dropbox", "Google Drive", "OneDrive", "Box Sync")
CONFIG_NAME = "prep-config.json"  # in mappings/: the person-confirmed timezone and roles
MAX_LINE_BYTES = 1 << 20
MAX_MESSAGE_BYTES = 16 << 20
MAX_HTML_CHARS = 8_000_000
MAX_HTML_NODES = 400_000
MAX_PART_CHARS = 60_000
MAX_INPUT_CHARS = 20_000  # data/validation-template/README.md
SPEAKERS = ("Customer", "Agent", "System")
ZONES = ("America/Los_Angeles", "America/Vancouver", "America/Tijuana", "America/Denver", "America/Phoenix",
         "America/Chicago", "America/New_York", "America/Toronto", "America/Halifax", "America/Anchorage",
         "Pacific/Honolulu", "America/Mexico_City", "America/Sao_Paulo", "Europe/London", "Europe/Dublin",
         "Europe/Lisbon", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Athens", "Europe/Helsinki",
         "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Shanghai", "Asia/Tokyo", "Australia/Perth",
         "Australia/Sydney", "Pacific/Auckland", "UTC")
SUPPORTED_CURRENCY = ("USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF")
# Exclusion reasons that mean "out of scope"; every other reason quarantines a conversation as unreliable.
OUT_OF_SCOPE = {"no_customer_message", "no_store_message", "too_long", "unsupported_currency"}

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), 1)}
_CALENDAR = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december", "sept", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
             "sunday", "today", "tomorrow", "yesterday", *_MONTHS, "mon", "tue", "tues", "wed", "thu", "thur",
             "thurs", "fri", "sat", "sun"}
_STOP = _CALENDAR | {
    "customer", "support", "team", "admin", "student", "user", "the", "and", "via", "from", "intercom", "hello",
    "hi", "hey", "thanks", "thank", "mr", "mrs", "ms", "dr", "miss", "sir", "madam", "inc", "llc", "co", "prep",
    "test", "help", "info", "billing", "noreply", "reply", "mail", "email", "gmail", "yahoo", "hotmail", "outlook",
    "icloud", "aol", "com", "net", "org", "edu", "unknown", "there", "all", "everyone", "folks", "guys", "dear",
    "visitor", "lead", "guest", "anonymous", "i'm", "i'll", "i've", "i'd"}
_INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060\u00ad"), None)
_SPACE = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_EMAIL_OBFUSCATED = re.compile(r"(?i)\b[\w.+-]+\s*[\[(]at[\])]\s*[\w-]+\s*[\[(]dot[\])]\s*[a-z]{2,6}\b")
_REFUND = re.compile(r"(?i)\brefund|\bmoney back\b|\breimburs|\bcharge ?backs?\b")


# ---------------------------------------------------------------- private root


def private_layout(root: Path | str) -> dict[str, Path]:
    """The private root's five directories, created owner-only, outside every repository and synced folder."""
    root = Path(root).expanduser().resolve()
    _check_location("private root", root)
    if any(marker in str(root) for marker in SYNCED):
        raise SafeError(f"The private root {root} is inside a synced folder; use an unsynced local directory")
    if not root.is_dir():
        raise SafeError(f"The private root {root} does not exist")
    dirs = {name: root / name for name in ROOT_DIRS}
    for path in (root, *dirs.values()):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise SafeError(f"{path} must be a real directory, not a file or a link")
        if not path.exists():
            path.mkdir(mode=0o700)
        if path.stat().st_mode & 0o077:
            raise SafeError(f"{path} is accessible to other users; run `chmod 700 '{path}'`")
    return dirs


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def _json(path: Path, value: object) -> Path:
    return _write(path, json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------- archive


class Quarantined(Exception):
    """A message or conversation that cannot be used; `reason` is a fixed code, never data."""

    def __init__(self, reason: str, conversation: str = ""):
        super().__init__(reason)
        self.reason, self.conversation = reason, conversation


def iter_mbox(path: Path) -> Iterator[tuple[int, bytes | None, int]]:
    """(position, message bytes or None when larger than MAX_MESSAGE_BYTES, size), streamed line by line."""
    buf: list[bytes] = []
    size, position, prev_blank, started = 0, 0, True, False
    with path.open("rb") as f:
        while line := f.readline(MAX_LINE_BYTES):
            if line.startswith(b"From ") and prev_blank:
                if started:
                    yield position, (b"".join(buf) if size <= MAX_MESSAGE_BYTES else None), size
                    position += 1
                buf, size, started, prev_blank = [], 0, True, False
                continue
            size += len(line)
            if size <= MAX_MESSAGE_BYTES:
                buf.append(line[1:] if re.match(rb">+From ", line) else line)  # undo mboxrd escaping
            elif buf:
                buf = []
            prev_blank = line in (b"\n", b"\r\n")
    if started:
        yield position, (b"".join(buf) if size <= MAX_MESSAGE_BYTES else None), size


# ---------------------------------------------------------------- HTML structure

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_SKIP = {"script", "style", "head", "title", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table", "ul",
          "ol", "hr", "section", "article"}
_ATTACHMENTS = {"attachments", "intercom-attachments"}
_NOT_BODY = _ATTACHMENTS | {"attribution", "label", "conversation-part-metadata", "conversation-part-avatar",
                            "conversation-part-avatar-wrapper", "warning-part", "warning", "user-role-badge", "badge",
                            "conversation-participants", "icon"}


class _Node:
    __slots__ = ("tag", "cls", "attrs", "kids")

    def __init__(self, tag: str, cls: frozenset[str], attrs: frozenset[str]):
        self.tag, self.cls, self.attrs, self.kids = tag, cls, attrs, []


class _Tree(HTMLParser):
    """A minimal element tree. Only tag names, classes and attribute names are kept: never a URL to fetch."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", frozenset(), frozenset())
        self.stack = [self.root]
        self.nodes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > MAX_HTML_NODES:
            raise Quarantined("html_too_complex")
        values = dict(attrs)
        node = _Node(tag, frozenset((values.get("class") or "").split()), frozenset(values))
        self.stack[-1].kids.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].kids.append(data)


def _walk(node: _Node) -> Iterator[_Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(k for k in reversed(n.kids) if isinstance(k, _Node))


def _flat(node: _Node) -> str:
    out, stack = [], list(reversed(node.kids))
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif item.tag not in _SKIP:
            out.append(" ")
            stack.extend(reversed(item.kids))
    return _SPACE.sub(" ", "".join(out).translate(_INVISIBLE)).strip()


def _render(node: _Node, stats: Counter) -> str:
    """Visible text with line breaks at block elements; images, citations and attachments are left out."""
    out: list[str] = []
    stack: list[_Node | str] = list(reversed(node.kids))
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        if item.tag in _SKIP or item.cls & _NOT_BODY:
            continue
        if item.tag == "img":
            stats["images"] += 1
            continue
        if item.tag == "a" and "data-inline-citation" in item.attrs:
            stats["citations"] += 1
            continue
        if item.tag in _BLOCK:
            out.append("\n")
            stack.append("\n")
        if item.tag == "li":
            out.append("- ")
        stack.extend(reversed(item.kids))
    lines = (_SPACE.sub(" ", ln).strip() for ln in "".join(out).translate(_INVISIBLE).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------- notifications

_META = re.compile(r"^(.*?)\s+on\s+(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{1,2}):(\d{2})\s*([ap]m)\s*$", re.I)
_ATTRIBUTION = re.compile(r"^(?:>\s?)*On (?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,? ([A-Z][a-z]{2,8})\.? (\d{1,2}),? "
                          r"(\d{4}),? at (\d{1,2}):(\d{2}) ?([AP]M),? (.{0,300}?)\s*wrote:\s*$")
_SUBJECT_WITH = re.compile(r"\b(?:conversation|ticket) with (.{1,160}?)(?: was [a-z]+)?\s*$", re.I)
_ENCRYPTED = {"multipart/encrypted", "application/pkcs7-mime", "application/x-pkcs7-mime", "application/pgp-encrypted"}
_TRUNCATION = ("view earlier", "earlier messages", "older messages", "show older")


@dataclass
class Part:
    kind: str  # user | admin | note | action | unknown
    author: str
    stamp: tuple[int, int, int, int] | None  # month, day, hour, minute in the render timezone; no year
    text: str
    emails: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    attachments: int = 0
    images: int = 0
    citations: int = 0
    others: tuple[str, ...] = ()  # people in a "Sent to ..." line
    key: tuple = ()


@dataclass
class Notification:
    position: int
    message_id: str
    thread: str
    conversation: str
    sent: datetime
    reason: str
    keys: list[tuple]  # its parts, newest first
    dates: frozenset[date]  # absolute dates of the plain-text attribution lines
    names: frozenset[str]  # display names from headers, the subject and attributions
    emails: frozenset[str]
    customer_hint: str
    references: tuple[str, ...]
    recipient_domain: str
    sender_domain: str
    app_id: str
    truncated: bool
    digest: str


def _kind(cls: frozenset[str]) -> str:
    if "note" in cls:
        return "note"
    if "action" in cls:
        return "action"
    if "admin" in cls:
        return "admin"
    return "user" if cls & {"user", "lead", "o__user", "o__lead"} else "unknown"


def _part(node: _Node) -> Part:
    stats: Counter = Counter()
    kind, author, stamp = _kind(node.cls), "", None
    meta = next((d for d in _walk(node) if "conversation-part-metadata" in d.cls), None)
    if meta is not None and (m := _META.match(_flat(meta))):
        month = _MONTHS.get(m.group(3).lower())
        if month:
            author = m.group(1).strip()
            stamp = (month, int(m.group(2)), int(m.group(4)) % 12 + (12 if m.group(6).lower() == "pm" else 0),
                     int(m.group(5)))
    body = next((d for d in _walk(node) if "conversation-part-body" in d.cls), None)
    text = _render(body, stats) if body is not None else ""
    if len(text) > MAX_PART_CHARS:
        raise Quarantined("oversized_part")
    emails, files, others, attachments = [], [], [], 0
    for d in _walk(node):
        if d.cls & _ATTACHMENTS:
            attachments += 1
            files += [_flat(x) for x in _walk(d) if "label" in x.cls and _flat(x)]
        elif "attribution" in d.cls:
            emails += _EMAIL.findall(_flat(d))
        elif "conversation-participants" in d.cls:
            line = re.sub(r"^\s*sent to\s+", "", _flat(d), flags=re.I)
            others += [p.strip() for p in re.split(r",\s*|\s+and\s+", line) if p.strip()]
    part = Part(kind, author, stamp, text, tuple(emails), tuple(files), attachments, stats["images"],
                stats["citations"], tuple(others))
    digest = hashlib.sha1("\x1f".join((text, *files, str(attachments))).encode()).hexdigest()
    part.key = (kind, author, stamp, digest)
    return part


def _addresses(values: Iterable[str]) -> list[tuple[str, str]]:
    return [(n.strip(), a.strip().lower()) for n, a in email.utils.getaddresses([str(v) for v in values])]


def raw_conversation(raw: bytes) -> str:
    """The conversation a message belongs to, read from its header bytes, when it cannot be parsed."""
    head = raw[:raw.find(b"\n\n") if b"\n\n" in raw[:262144] else 262144]
    m = re.search(rb'"conversation_id"\s*:\s*"?(\d{1,30})', head)
    return m.group(1).decode() if m else ""


def parse_notification(raw: bytes, position: int = 0) -> tuple[Notification, list[Part]]:
    """One notification and its parts (newest first). Raises Quarantined with a fixed reason code."""
    try:
        return _parse_notification(raw, position)
    except Quarantined as q:
        q.conversation = q.conversation or raw_conversation(raw)
        raise


def _parse_notification(raw: bytes, position: int) -> tuple[Notification, list[Part]]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    if msg.get("Message-ID") is None and msg.get("Date") is None:
        raise Quarantined("malformed_mime")
    if any(p.get_content_type() in _ENCRYPTED for p in msg.walk()):
        raise Quarantined("encrypted")
    if msg.get("X-Intercom-App-Id") is None:
        raise Quarantined("not_intercom_notification")
    try:
        conversation = str(json.loads(str(msg.get("X-Mailgun-Variables") or ""))["conversation_id"]).strip()
    except (ValueError, KeyError, TypeError):
        raise Quarantined("no_conversation_id") from None
    if not re.fullmatch(r"\d{1,30}", conversation):
        raise Quarantined("no_conversation_id")
    try:
        sent = email.utils.parsedate_to_datetime(str(msg.get("Date")))
    except (TypeError, ValueError, IndexError):
        raise Quarantined("bad_date") from None
    if sent.tzinfo is None:
        raise Quarantined("bad_date")
    html_part = msg.get_body(preferencelist=("html",))
    if html_part is None:
        raise Quarantined("no_html_alternative")
    try:
        html = html_part.get_content()
    except (LookupError, UnicodeError, ValueError):
        raise Quarantined("undecodable") from None
    if len(html) > MAX_HTML_CHARS:
        raise Quarantined("oversized_body")
    tree = _Tree()
    tree.feed(html)
    tree.close()
    parts = [_part(n) for n in _walk(tree.root) if "conversation-part" in n.cls]
    if not parts:
        raise Quarantined("no_conversation_parts")
    plain = msg.get_body(preferencelist=("plain",))
    try:
        plain_text = plain.get_content() if plain is not None else ""
    except (LookupError, UnicodeError, ValueError):
        plain_text = ""
    dates, names, emails = set(), set(), set()
    for line in plain_text.translate(_INVISIBLE).replace("\u202f", " ").splitlines():
        if "wrote:" not in line or not (m := _ATTRIBUTION.match(line.strip())):
            continue
        try:
            dates.add(date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))))
        except (KeyError, ValueError):
            pass
        who = m.group(7)
        emails.update(e.lower() for e in _EMAIL.findall(who))
        name = re.sub(r"[\"<>]", " ", _EMAIL.sub(" ", who)).strip(" ,")
        if name:
            names.add(_SPACE.sub(" ", name))
    for name, addr in _addresses(msg.get_all("From", []) + msg.get_all("To", []) + msg.get_all("Cc", [])):
        if addr:
            emails.add(addr)
        name = re.sub(r"\s+(?:via|from)\s+.*$", "", name).strip(" \"'")
        if name and "@" not in name:
            names.add(name)
    subject = _SPACE.sub(" ", str(msg.get("Subject") or ""))
    hint = (m.group(1).strip(" \"'") if (m := _SUBJECT_WITH.search(subject)) else "")
    recipients = _addresses(msg.get_all("Delivered-To", []) + msg.get_all("To", []))
    sender = _addresses(msg.get_all("From", []))
    lowered = html.lower()
    note = Notification(
        position=position, message_id=str(msg.get("Message-ID") or "").strip(),
        thread=str(msg.get("X-GM-THRID") or "").strip(), conversation=conversation, sent=sent.astimezone(UTC),
        reason=str(msg.get("X-Intercom-Notification-Reason") or "").strip()[:40],
        keys=[p.key for p in parts], dates=frozenset(dates), names=frozenset(names), emails=frozenset(emails),
        customer_hint=hint, references=tuple(str(msg.get("References") or "").split()),
        recipient_domain=recipients[0][1].rpartition("@")[2] if recipients else "",
        sender_domain=sender[0][1].rpartition("@")[2] if sender else "",
        app_id=str(msg.get("X-Intercom-App-Id") or "").strip(),
        truncated=any(marker in lowered for marker in _TRUNCATION), digest=hashlib.sha256(raw).hexdigest())
    return note, parts


@dataclass
class Archive:
    notifications: dict[str, list[Notification]] = field(default_factory=lambda: defaultdict(list))
    parts: dict[str, dict[tuple, Part]] = field(default_factory=lambda: defaultdict(dict))
    quarantined: list[tuple[int, str]] = field(default_factory=list)  # (position, reason)
    tainted: set[str] = field(default_factory=set)  # conversations with a quarantined notification
    conflicts: set[str] = field(default_factory=set)  # conversations with a conflicting duplicate Message-ID
    counts: Counter = field(default_factory=Counter)
    recipients: Counter = field(default_factory=Counter)


def load_archive(path: Path) -> Archive:
    archive = Archive()
    seen: dict[str, tuple[str, str]] = {}
    for position, raw, size in iter_mbox(path):
        archive.counts["messages"] += 1
        archive.counts["bytes"] += size
        if raw is None:
            archive.quarantined.append((position, "oversized_message"))
            archive.tainted.add("")  # its conversation is unknown: the run reports it as a lost message
            continue
        try:
            note, parts = parse_notification(raw, position)
        except Quarantined as q:
            archive.quarantined.append((position, q.reason))
            archive.tainted.add(q.conversation)
            continue
        except Exception as exc:  # noqa: BLE001 - the reason is a type name, never the message
            archive.quarantined.append((position, f"malformed:{type(exc).__name__}"))
            archive.tainted.add(raw_conversation(raw))
            continue
        if note.message_id in seen:
            if seen[note.message_id][0] == note.digest:
                archive.counts["duplicate_message_id_identical"] += 1
                continue
            archive.counts["duplicate_message_id_conflict"] += 1
            archive.conflicts.update((note.conversation, seen[note.message_id][1]))
        seen[note.message_id] = (note.digest, note.conversation)
        archive.recipients[note.recipient_domain] += 1
        archive.notifications[note.conversation].append(note)
        store = archive.parts[note.conversation]
        for p in parts:
            store.setdefault(p.key, p)
    return archive


# ---------------------------------------------------------------- time


def resolve(stamp: tuple[int, int, int, int], reference: datetime) -> datetime | None:
    """The stamp's wall time in the year that puts it at or before `reference` (a naive local time) plus a day."""
    month, day, hour, minute = stamp
    for year in (reference.year, reference.year - 1):
        try:
            when = datetime(year, month, day, hour, minute)
        except ValueError:
            continue
        if when <= reference + timedelta(days=1):
            return when
    return None


def detect_render_zone(archive: Archive) -> dict:
    """The IANA zone whose offsets best explain each notification's newest part time against its UTC send time.

    Intercom renders part times in one zone; a notification goes out within a minute or two of the part that
    triggered it, so the right zone makes (send time in that zone - part time) fall in [0, 3) minutes.
    """
    samples = []
    for conv, notes in archive.notifications.items():
        store = archive.parts[conv]
        for n in notes:
            stamps = [store[k].stamp for k in n.keys if store[k].stamp][:3]  # newest first
            if stamps:
                samples.append((n.sent, stamps))
    scores = Counter()
    for name in ZONES:
        zone = ZoneInfo(name)
        for sent, stamps in samples:
            local = sent.astimezone(zone).replace(tzinfo=None)
            times = [t for t in (resolve(s, local) for s in stamps) if t]
            if times and timedelta(0) <= local - max(times) < timedelta(minutes=3):
                scores[name] += 1
    if not scores:
        return {"zone": None, "samples": len(samples), "matched": 0, "share": 0.0, "ties": []}
    best, top = scores.most_common(1)[0]
    return {"zone": best, "samples": len(samples), "matched": top, "share": round(top / len(samples), 4),
            "ties": sorted(z for z, s in scores.items() if s == top and z != best),
            "runner_up": next(((z, s) for z, s in scores.most_common() if s < top), None)}


# ---------------------------------------------------------------- configuration


def owner_domain(archive: Archive) -> str:
    return archive.recipients.most_common(1)[0][0] if archive.recipients else ""


def author_stats(archive: Archive) -> dict[str, dict]:
    """Per teammate author: parts, conversations, cited (Fin-style) parts and templated parts. Private."""
    convs_by_digest: dict[str, set[str]] = defaultdict(set)
    for conv, store in archive.parts.items():
        for key, p in store.items():
            if p.kind == "admin":
                convs_by_digest[key[3]].add(conv)
    stats: dict[str, Counter] = defaultdict(Counter)
    convs: dict[str, set[str]] = defaultdict(set)
    for conv, store in archive.parts.items():
        for key, p in store.items():
            if p.kind in ("admin", "note") and p.author:
                s = stats[p.author]
                s[p.kind] += 1
                s["cited"] += p.kind == "admin" and p.citations > 0
                s["templated"] += p.kind == "admin" and len(convs_by_digest[key[3]]) >= 10
                convs[p.author].add(conv)
    return {a: {"replies": s["admin"], "notes": s["note"], "conversations": len(convs[a]),
                "cited_share": round(s["cited"] / s["admin"], 3) if s["admin"] else 0.0,
                "templated_share": round(s["templated"] / s["admin"], 3) if s["admin"] else 0.0}
            for a, s in stats.items()}


_BOT_NAME = re.compile(r"(?i)^(?:fin|operator)\b|\bbot\b|\bai agent\b|\bassistant\b")


def automated(name: str, s: dict) -> bool:
    """A proposal only: an AI agent cites articles; a workflow sends templates and writes almost no notes."""
    return bool(_BOT_NAME.search(name)) or s["cited_share"] >= 0.05 or (
        s["templated_share"] >= 0.5 and s["notes"] <= 0.05 * s["replies"])


def propose_config(archive: Archive, zone: dict) -> dict:
    authors = {}
    for name, s in sorted(author_stats(archive).items()):
        authors[name] = {"role": "System" if automated(name, s) else "Agent", "confirmed": False, **s}
    return {
        "schema": "routewise.mailbox-prep-config.v1",
        "note": "Private. Confirm each value locally, set its `confirmed` to true and rerun `routewise mailbox "
                "prepare`. Nothing here is sent anywhere.",
        "store_timezone": {"value": zone["zone"], "confirmed": False,
                           "evidence": "Zone that best explains Intercom's rendered part times against UTC send times; "
                                       "it may be a teammate's display setting rather than the store's zone.",
                           "detection": zone},
        "render_timezone": zone["zone"],
        "owner_domain": owner_domain(archive),
        "internal_notes": "exclude",
        "date_shift_weeks": 0,
        "forwarded_content": "keep_in_forwarder_turn_and_flag",
        "keep_customer_ending_snapshots": False,
        "keep_customer_ending_snapshots_note": "A teammate is not notified of their own replies, so a conversation "
                                               "whose last captured message is the customer's may lack the store's "
                                               "final answer. Such conversations are quarantined unless this is true.",
        "roles": {"user": "Customer", "admin": "Agent unless the author is set to System below", "note": "excluded",
                  "action": "excluded"},
        "authors": authors,
    }


def load_config(path: Path, proposal: dict) -> tuple[dict, bool]:
    """The person-confirmed configuration, or the proposal (written there) when none exists yet."""
    if not path.exists():
        _json(path, proposal)
        return proposal, True
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("internal_notes", "exclude") != "exclude":
        raise SafeError("Only internal_notes = \"exclude\" is supported: notes are never shown to the customer")
    if config.get("date_shift_weeks", 0):
        raise SafeError("Date shifting is not implemented; set date_shift_weeks to 0 or extend and test it first")
    for name, entry in proposal["authors"].items():
        config.setdefault("authors", {}).setdefault(name, {**entry, "confirmed": False})
    return config, False


# ---------------------------------------------------------------- reconstruction


@dataclass
class Turn:
    role: str
    when: datetime  # store-local wall time
    text: str
    author: str
    kind: str
    attachments: int = 0
    images: int = 0


@dataclass
class Thread:
    conversation: str
    turns: list[Turn] = field(default_factory=list)
    quarantine: list[str] = field(default_factory=list)
    flags: Counter = field(default_factory=Counter)
    excluded: Counter = field(default_factory=Counter)
    customers: list[str] = field(default_factory=list)
    staff: list[str] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    hints: set[str] = field(default_factory=set)  # customer names in notification subjects
    emails: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    source_ids: set[str] = field(default_factory=set)  # app IDs and sender domains: never in a payload
    threads: set[str] = field(default_factory=set)
    message_ids: list[str] = field(default_factory=list)
    references: set[str] = field(default_factory=set)
    sent: tuple[datetime, datetime] | None = None


def _other_years(d: date) -> set[date]:
    out = set()
    for year in (d.year - 1, d.year + 1):
        try:
            out.add(d.replace(year=year))
        except ValueError:  # 29 February
            pass
    return out


def reconstruct(conv: str, archive: Archive, config: dict) -> Thread:
    notes = sorted(archive.notifications[conv], key=lambda n: (n.sent, n.position))
    store = archive.parts[conv]
    render, target = ZoneInfo(config["render_timezone"]), ZoneInfo(config["store_timezone"]["value"])
    t = Thread(conv, threads={n.thread for n in notes}, message_ids=[n.message_id for n in notes],
               references={n.references[0] for n in notes if n.references}, sent=(notes[0].sent, notes[-1].sent))
    if conv in archive.conflicts:
        t.quarantine.append("duplicate_message_id_conflict")
    if conv in archive.tainted:
        t.quarantine.append("sibling_message_quarantined")
    if any(n.truncated for n in notes):
        t.quarantine.append("truncated_history")
    for n in notes:
        t.names |= n.names
        t.emails |= n.emails
        t.source_ids |= {x for x in (n.app_id, n.sender_domain, n.recipient_domain) if x}
        if n.customer_hint:
            t.hints.add(n.customer_hint)
    # When is each part, by the latest notification that shows it, and is that date confirmed by a plain-text line?
    latest: dict[tuple, Notification] = {}
    shown: dict[tuple, list[int]] = defaultdict(list)
    for i, n in enumerate(notes):
        for k in n.keys:
            latest[k] = n
            shown[k].append(i)
    when: dict[tuple, datetime | None] = {}
    for k, n in latest.items():
        p = store[k]
        when[k] = resolve(p.stamp, n.sent.astimezone(render).replace(tzinfo=None)) if p.stamp else None
        if p.kind in ("user", "admin") and when[k]:
            days = set().union(*(notes[i].dates for i in shown[k]))
            d = when[k].date()
            if d in days:
                t.flags["year_confirmed"] += 1
            elif _other_years(d) & days:
                t.quarantine.append("year_conflict")
            else:
                t.flags["year_unconfirmed"] += 1
    # Variants of one part (same kind, author and minute) that never appear together are a duplicate-history conflict.
    groups: dict[tuple, list[tuple]] = defaultdict(list)
    for k in latest:
        if store[k].kind in ("user", "admin", "note"):
            groups[k[:3]].append(k)
    dropped: set[tuple] = set()
    for variants in (v for v in groups.values() if len(v) > 1):
        for a in variants:
            for b in variants:
                if a < b and not set(shown[a]) & set(shown[b]):
                    ta, tb = store[a].text, store[b].text
                    short, long_ = (a, b) if len(ta) <= len(tb) else (b, a)
                    if store[short].text and store[short].text in store[long_].text:
                        dropped.add(short)
                        t.flags["duplicate_variant_merged"] += 1
                    else:
                        t.quarantine.append("duplicate_history_conflict")
    # Order: the latest notification's parts, oldest first, then parts only older notifications showed, by time.
    order = [k for k in reversed(notes[-1].keys)]
    present = set(order)
    for n in reversed(notes[:-1]):
        for k in reversed(n.keys):
            if k in present:
                continue
            present.add(k)
            t.flags["parts_from_older_notifications"] += 1
            at = when.get(k)
            index = next((i for i, o in enumerate(order) if at and when.get(o) and when[o] > at), len(order))
            order.insert(index, k)
    authors_cfg = config.get("authors", {})
    for k in order:
        p = store[k]
        if k in dropped:
            continue
        if p.kind in ("note", "action"):
            t.excluded["internal_notes" if p.kind == "note" else "events"] += 1
            if p.kind == "note" and p.author and p.author not in t.staff:
                t.staff.append(p.author)
            continue
        if p.kind == "unknown":
            t.quarantine.append("unknown_part_type")
            continue
        if not p.author or when.get(k) is None:
            t.quarantine.append("part_without_author_or_time")
            continue
        if p.kind == "user":
            role = "Customer"
            if p.author not in t.customers:
                t.customers.append(p.author)
        else:
            entry = authors_cfg.get(p.author, {})
            role = entry.get("role", "Agent")
            if role not in ("Agent", "System"):
                t.excluded["excluded_author"] += 1
                continue
            if not entry.get("confirmed"):
                t.flags["author_role_unconfirmed"] += 1
            if p.author not in t.staff:
                t.staff.append(p.author)
        local = when[k].replace(tzinfo=render, fold=0)
        if local.utcoffset() != when[k].replace(tzinfo=render, fold=1).utcoffset():
            t.flags["dst_ambiguous_time"] += 1
        t.emails.update(e.lower() for e in p.emails)
        t.files.update(p.files)
        t.names.update(p.others)
        t.turns.append(Turn(role, local.astimezone(target).replace(tzinfo=None), p.text, p.author, p.kind,
                            p.attachments, p.images))
    if set(t.customers) & set(t.staff):
        t.quarantine.append("role_name_conflict")
    if len(t.customers) > 1:
        t.flags["multiple_customer_authors"] += 1
    if not config["store_timezone"].get("confirmed"):
        t.flags["timezone_unconfirmed"] += 1
    t.turns.sort(key=lambda turn: turn.when)  # stable: same-minute turns keep Intercom's order
    if t.turns and t.turns[-1].role == "Customer":
        # Teammates are not notified of their own replies, so the store's final answer may be missing.
        t.flags["ends_with_customer_message"] += 1
        if not config.get("keep_customer_ending_snapshots"):
            t.quarantine.append("may_lack_final_store_reply")
    return t


# ---------------------------------------------------------------- cleaning

_SENT_FROM = re.compile(r"(?i)^\s*(?:sent from my \w+|sent from (?:yahoo|mail|outlook)\b|get outlook for \w+)")
_QUOTE_LINE = re.compile(r"(?i)^\s*(?:>|on\s.{3,200}\bwrote:\s*$|-{2,}\s*original message\s*-{2,}\s*$|_{8,}\s*$)")
_FORWARD = re.compile(r"(?i)^\s*(?:-{2,}\s*forwarded message\s*-{2,}|begin forwarded message:)\s*$")
_HEADER_LINE = re.compile(r"(?i)^\s*(?:from|to|cc|bcc|date|sent|subject|reply-to)\s*:\s")
_SIGNOFF = re.compile(r"(?i)^\s*(?:best(?: regards| wishes)?|kind regards|warm(?:est)? regards|regards|warmly|"
                      r"sincerely|cheers|thanks(?: again| so much)?|thank you(?: so much)?|many thanks|all the best|"
                      r"take care|respectfully)\s*[,.!-]?\s*(.{0,80})$")
_KEEP = re.compile(r"(?i)\b(?:refund\w*|issued|processed|credit\w*|charge\w*|today|tomorrow|yesterday|days?|weeks?|"
                   r"business|mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b|"
                   r"\d+[./-]\d+|[$€£¥]")
_WORDS = re.compile(r"[^\W_]+")


def _grams(text: str, n: int = 6) -> list[tuple[str, ...]]:
    words = _WORDS.findall(text.casefold())
    return [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]


def clean(text: str, history: str, flags: Counter) -> str:
    """Drop device footers, quoted history that verifiably repeats earlier turns, and short trailing signatures."""
    lines = [ln for ln in text.splitlines() if not _SENT_FROM.match(ln)]
    if removed := len(text.splitlines()) - len(lines):
        flags["device_footer_removed"] += removed
    forward = next((i for i, ln in enumerate(lines) if _FORWARD.match(ln)), None)
    quote = next((i for i, ln in enumerate(lines) if _QUOTE_LINE.match(ln) or (
        _HEADER_LINE.match(ln) and ln.lower().lstrip().startswith("from")
        and any(re.match(r"(?i)^\s*(?:sent|date)\s*:", x) for x in lines[i + 1:i + 4]))), None)
    if forward is not None and (quote is None or forward <= quote):
        body = lines[forward + 1:]
        while body and (not body[0].strip() or _HEADER_LINE.match(body[0])):
            body.pop(0)
        lines = lines[:forward] + ["[Forwarded message]"] + body
        flags["forwarded_content"] += 1
    elif quote is not None:
        quoted = "\n".join(re.sub(r"^\s*>+ ?", "", ln) for ln in lines[quote:] if not re.match(
            r"(?i)^\s*(?:>\s*)*on\s.{3,200}\bwrote:\s*$", ln))
        grams, known = _grams(quoted), set(_grams(history))
        if not grams or sum(g in known for g in grams) >= 0.9 * len(grams):
            lines = lines[:quote]
            flags["quoted_history_removed"] += 1
        else:
            flags["quote_kept_unverified"] += 1
    nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
    for i in reversed(nonempty[1:]):
        m = _SIGNOFF.match(lines[i])
        if not m:
            continue
        tail = [ln for ln in lines[i + 1:] if ln.strip()]
        if (m.group(1) or tail) and len(tail) <= 4 and all(len(ln) <= 80 for ln in tail) \
                and not _KEEP.search(" ".join([m.group(1), *tail])):
            lines = lines[:i]
            flags["signature_removed"] += 1
        break
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- de-identification


@functools.cache
def _wordlist(path: str) -> frozenset[str]:
    try:
        return frozenset(w.strip() for w in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
                         if w.strip())
    except OSError:
        return frozenset()


def dictionary() -> frozenset[str]:
    return frozenset(w for w in _wordlist("/usr/share/dict/words") if w.islower())


def given_names() -> frozenset[str]:
    words = dictionary()
    return frozenset(w for w in _wordlist("/usr/share/dict/propernames") if w[:1].isupper() and w.lower() not in words)


def name_variants(name: str) -> set[str]:
    """The full name, first + last, and each token; for an address, its alphabetic local-part tokens."""
    name = _SPACE.sub(" ", re.sub(r"[\"<>()\[\]]", " ", name)).strip(" ,.'")
    if not name:
        return set()
    if "@" in name:
        local = name.split("@")[0]
        return {t for t in re.split(r"[^A-Za-z]+", local) if len(t) >= 3 and t.lower() not in _STOP} | (
            {local} if len(local) >= 4 else set())
    tokens = [t.strip(".,'") for t in re.split(r"[\s,]+", name) if t.strip(".,'")]
    variants = {name} if len(name) >= 2 else set()
    if len(tokens) >= 2:
        variants.add(f"{tokens[0]} {tokens[-1]}")
    variants.update(t for t in tokens if len(t) >= 2 and t.lower() not in _STOP and not t.isdigit())
    return {v for v in variants if v.lower() not in _STOP}


_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_DOMAIN = re.compile(r"(?i)\b(?:[a-z0-9-]+\.)+(?:com|org|net|edu|io|co|us|gov|info|biz|me|app|ai|ly|gl|link|page|"
                     r"site|uk|ca|au)\b(/[^\s<>\"']*)?")
_IP = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
_CREDENTIAL = re.compile(r"(?i)\b(password|passcode|pass code|pin|verification code|security code|access code|"
                         r"one[- ]time code|otp|2fa code|login code|temporary password|username|user name)"
                         r"(\s*(?:is|was|:|=|-)\s*)(\S+)")
_TOKEN = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{24,}\b|\b[0-9a-fA-F]{16,}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_ORDER = re.compile(r"(?i)\b(order|invoice|receipt|transaction|confirmation|purchase|reference|ref|booking|payment)"
                    r"(\s*(?:number|no\.?|num|id|#)?\s*[:#]?\s*#?\s*)([A-Za-z0-9][A-Za-z0-9_-]{2,40})")
_ACCOUNT = re.compile(r"(?i)\b(account|acct|member(?:ship)?|student|customer|user|client|policy|license|passport|"
                      r"tax)(\s*(?:number|no\.?|num|id|#)\s*[:#]?\s*#?\s*)([A-Za-z0-9][A-Za-z0-9_-]{2,40})")
_CARD = re.compile(r"(?<![\d-])(?:(?:\d{4}[ -]){3}\d{1,7}|\d{4}[ -]\d{6}[ -]\d{5}|\d{13,19})(?![\d-])")
_LAST4 = re.compile(r"(?i)\b(ending in|ends in|last (?:4|four)(?: digits)?(?: of [\w ]{0,20})?(?: is| are|:)?)\s*(\d{4})\b")
_PHONE = re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)|"
                    r"(?<![\w])\+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){2,5}\d{2,4}(?!\d)|(?<![\d.,$])\b\d{3}-\d{4}\b")
_STATES = ("AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
           "OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY")
_ADDRESS = (re.compile(r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z0-9][\w'-]*\.?\s+){1,4}(?:Street|St|Avenue|Ave|Road|Rd|"
                       r"Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way|Place|Pl|Parkway|Pkwy|Highway|Hwy|Circle|Cir|"
                       r"Terrace|Ter|Trail|Trl|Square|Sq|Loop)\b\.?(?:,?\s*(?:Apt|Apartment|Suite|Ste|Unit|Fl|Floor|#)"
                       r"\.?\s*[\w-]+)?"),
            re.compile(r"(?i)\bP\.?\s?O\.?\s+Box\s+\d+\b"),
            re.compile(rf"\b(?:[A-Z][a-zA-Z.'-]+\s){{0,3}}[A-Z][a-zA-Z.'-]+,?\s+(?:{_STATES})\s+\d{{5}}(?:-\d{{4}})?\b"),
            re.compile(rf"\b(?:{_STATES})\s+\d{{5}}(?:-\d{{4}})?\b"))
_IDENT = re.compile(r"(?<![\w$€£¥\ue000-\uf8ff#])#?([A-Za-z0-9][A-Za-z0-9_-]{3,40})(?![\w])")
_DATEISH = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{8}")
_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{2,30}\b")
_SCHOOL = (re.compile(r"\b(?:University|College|Institute|Academy|School) of(?: (?:the )?[A-Z][\w&'.-]+){1,4}"),
           re.compile(r"\b(?:[A-Z][\w&'.-]+ ){1,4}(?:University|College|Institute|High School|Academy|Medical School|"
                      r"Law School|School of Medicine)\b"))
_NAME = r"(?:[A-Z][a-z]{1,20}|[A-Z]['’][A-Z][a-z]{1,20})(?:[-'’][A-Z]?[a-z]{1,20})?"
_GREETING = re.compile(rf"\b(?:[Hh]i|[Hh]ello|[Hh]ey|[Dd]ear|[Hh]iya|[Gg]ood (?:morning|afternoon|evening)|[Tt]hanks|"
                       rf"[Tt]hank you)[ ,]+({_NAME})(?:\s+({_NAME}))?\b")
_INTRO = re.compile(rf"\b(?:[Mm]y name is|[Tt]his is|I am|I'm)\s+({_NAME})(?:\s+({_NAME}))?\b")
_CAPITAL = re.compile(rf"(?<![\w'’])({_NAME})(?:\s+({_NAME}))?\b")
_MID_CAPITAL = re.compile(rf"(?<=[a-z0-9,;:] )({_NAME})\b")  # a capitalized word inside a sentence
COMMON_TERM_CONVERSATIONS = 15  # a capitalized word in this many conversations is a term, not a person


def _luhn(digits: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(digits)):
        d = int(c) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _is_identifier(token: str) -> bool:
    return sum(c.isdigit() for c in token) >= 5 and not _DATEISH.fullmatch(token)


def identifiers(text: str) -> set[str]:
    """Identifier-like tokens: what a surrogate must never collide with."""
    found = {m.group(3) for m in _ORDER.finditer(text) if sum(c.isdigit() for c in m.group(3)) >= 2}
    return found | {m.group(1) for m in _IDENT.finditer(text) if _is_identifier(m.group(1))}


class Surrogates:
    """Shape-preserving, collision-checked replacements for identifiers, consistent within a conversation."""

    def __init__(self, originals: Iterable[str], rng: random.Random):
        self.originals = {o.casefold() for o in originals}
        self.rng, self.issued, self.used = rng, {}, set()

    def __call__(self, conversation: str, value: str) -> str:
        key = (conversation, value.casefold())
        if key not in self.issued:
            prefix = m.group() if (m := re.match(r"[A-Za-z]{1,5}[-_#]", value)) else ""
            for _ in range(1000):
                body = "".join(self._char(c, i == 0) for i, c in enumerate(value[len(prefix):]))
                candidate = prefix + body
                if candidate.casefold() not in self.originals | self.used and candidate != value:
                    break
            else:
                raise SafeError("Could not draw a collision-free identifier surrogate")
            self.issued[key] = candidate
            self.used.add(candidate.casefold())
        return self.issued[key]

    def _char(self, c: str, first: bool) -> str:
        if c.isdigit():
            return str(self.rng.randint(1 if first and c != "0" else 0, 9))
        if c.isalpha() and c.isascii():
            letter = chr(self.rng.randint(0, 25) + 65)
            return letter if c.isupper() else letter.lower()
        return c


@dataclass
class Person:
    group: str  # customer | staff | other
    variants: set[str]
    label: str = ""


class Redactor:
    """Replaces personal values in one conversation and keeps the private mapping from placeholder to original."""

    def __init__(self, conversation: str, customers: Iterable[str], staff: Iterable[str], others: Iterable[str],
                 global_staff: Iterable[str], surrogates: Surrogates):
        self.conversation, self.surrogates = conversation, surrogates
        self.counts: Counter = Counter()
        self.mapping: dict[str, set[str]] = defaultdict(set)
        self.sentinels: list[str] = []  # surrogates, by sentinel index
        self.people: list[Person] = []
        self.next: Counter = Counter()
        for group, names in (("customer", customers), ("staff", staff), ("staff", global_staff), ("other", others)):
            for name in names:
                self._add(group, name_variants(name))
        self._compile()

    def _add(self, group: str, variants: set[str]) -> None:
        known = {v.casefold() for p in self.people for v in p.variants}
        variants = {v for v in variants if v.casefold() not in known}
        if variants:
            self.people.append(Person(group, variants))

    def _compile(self) -> None:
        words = dictionary()
        self.owner: dict[str, Person] = {}
        loose, strict = [], []
        for p in self.people:
            for v in p.variants:
                self.owner.setdefault(" ".join(v.split()).casefold(), p)
                (strict if " " not in v and v.lower() in words else loose).append(v)
        self.loose = _names_pattern(loose, re.I)
        self.strict = _names_pattern([v[0].upper() + v[1:] for v in strict], 0)

    def learn(self, texts: Iterable[str], common: frozenset[str] = frozenset()) -> None:
        """Pass one: names that greetings, introductions, sign-offs or the given-name list reveal, and rare
        capitalized non-dictionary words inside sentences (`common` holds those frequent across conversations)."""
        words, given = dictionary(), given_names()
        ok = lambda g: g and g.lower() not in _STOP and (g.lower() not in words or g in given)  # noqa: E731
        found: set[str] = set()
        for text in texts:
            for rx in (_GREETING, _INTRO):
                for m in rx.finditer(text):
                    found.update(g for g in (m.group(1), m.group(2)) if ok(g))
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            for i, ln in enumerate(lines):
                if m := _SIGNOFF.match(ln):
                    tail = m.group(1) or (lines[i + 1] if i + 1 < len(lines) else "")
                    tokens = tail.split()
                    if 1 <= len(tokens) <= 3 and all(re.fullmatch(r"[A-Z][a-z'’-]{1,20}", t) for t in tokens):
                        found.update(t for t in tokens if ok(t))
            for m in _MID_CAPITAL.finditer(text):
                if m.group(1) not in common and m.group(1).lower() not in words and m.group(1).lower() not in _STOP:
                    found.add(m.group(1))
            for m in _CAPITAL.finditer(text):
                if m.group(1) in given and m.group(1).lower() not in _STOP:
                    found.add(m.group(1))
                    if m.group(2) and m.group(2).lower() not in words and m.group(2).lower() not in _STOP:
                        found.add(f"{m.group(1)} {m.group(2)}")
        for name in sorted(found, key=len, reverse=True):
            self._add("other", name_variants(name) if " " in name else {name})
        self._compile()

    def _person(self, matched: str) -> Person:
        """The person a matched name belongs to; a spelling no variant explains becomes a new, unnamed person."""
        key = " ".join(matched.split()).casefold()
        if key not in self.owner:
            self.owner[key] = Person("other", {matched})
            self.people.append(self.owner[key])
        return self.owner[key]

    def _label(self, person: Person) -> str:
        if not person.label:
            self.next[person.group] += 1
            n = self.next[person.group]
            person.label = {"customer": "[CUSTOMER]" if n == 1 else f"[CUSTOMER_{n}]",
                            "staff": f"[AGENT_{chr(64 + n) if n <= 26 else n}]"}.get(person.group, f"[NAME_{n}]")
        return person.label

    def _put(self, placeholder: str, original: str) -> str:
        self.mapping[placeholder].add(original)
        return placeholder

    def _sub(self, rx: re.Pattern | None, category: str, text: str, repl) -> str:
        if rx is None:
            return text

        def apply(m: re.Match) -> str:
            out = repl(m)
            self.counts[category] += out != m.group(0)
            return out
        return rx.sub(apply, text)

    def _surrogate(self, value: str) -> str:
        self.sentinels.append(self.surrogates(self.conversation, value))
        self.mapping[self.sentinels[-1]].add(value)
        return chr(0xE100 + len(self.sentinels) - 1)

    def __call__(self, text: str) -> str:
        s, put = self._sub, self._put
        digits = lambda v: sum(c.isdigit() for c in v)  # noqa: E731
        text = s(_EMAIL, "email", text, lambda m: put("[EMAIL]", m.group()))
        text = s(_EMAIL_OBFUSCATED, "email", text, lambda m: put("[EMAIL]", m.group()))
        text = s(_URL, "url", text, lambda m: put("[URL]", m.group()))
        text = s(_DOMAIN, "url", text, lambda m: put("[URL]" if m.group(1) and len(m.group(1)) > 1 else "[DOMAIN]",
                                                     m.group()))
        text = s(_IP, "ip_address", text, lambda m: put("[IP]", m.group()))
        text = s(_CARD, "payment_card", text, lambda m: put("[CARD]", m.group()) if _luhn(re.sub(r"\D", "", m.group()))
                 else m.group())
        text = s(_LAST4, "payment_card", text, lambda m: f"{m.group(1)} {put('[CARD_LAST4]', m.group(2))}")
        text = s(_CREDENTIAL, "credential", text, lambda m: m.group(1) + m.group(2) + put("[CREDENTIAL]", m.group(3)))
        text = s(_TOKEN, "token", text, lambda m: put("[TOKEN]", m.group()))
        text = s(_SSN, "government_id", text, lambda m: put("[SSN]", m.group()))
        text = s(_ORDER, "order_or_transaction_id", text, lambda m: m.group(0) if digits(m.group(3)) < 2
                 else m.group(1) + m.group(2) + self._surrogate(m.group(3)))
        text = s(_ACCOUNT, "account_id", text, lambda m: m.group(0) if digits(m.group(3)) < 2
                 else m.group(1) + m.group(2) + put("[ACCOUNT_ID]", m.group(3)))
        text = s(_PHONE, "phone", text, lambda m: put("[PHONE]", m.group()))
        for rx in _ADDRESS:
            text = s(rx, "postal_address", text, lambda m: put("[ADDRESS]", m.group()))
        text = s(_IDENT, "other_identifier", text, self._identifier)
        for rx in (self.loose, self.strict):
            text = s(rx, "name", text, lambda m: put(self._label(self._person(m.group(1))), m.group(1)))
        text = s(_HANDLE, "social_handle", text, lambda m: put("[HANDLE]", m.group()))
        for rx in _SCHOOL:
            text = s(rx, "school", text, lambda m: put("[SCHOOL]", m.group()))
        return re.sub("[\ue100-\uf8ff]", lambda m: self.sentinels[ord(m.group()) - 0xE100], text)

    def _identifier(self, m: re.Match) -> str:
        token = m.group(1)
        after = m.string[m.end():m.end() + 3]
        before = m.string[max(0, m.start() - 5):m.start()].lower()
        if not _is_identifier(token) or re.match(r"[.,]\d", after) or re.search(r"[$€£¥]|usd|eur|gbp|cad", before):
            return m.group(0)
        return m.group(0)[:m.start(1) - m.start()] + self._surrogate(token)

    def originals(self) -> dict[str, set[str]]:
        """Replaced originals by kind (surrogated identifiers under `identifier`), for the payload check."""
        out: dict[str, set[str]] = defaultdict(set)
        for key, values in self.mapping.items():
            kind = ("identifier" if key in self.sentinels else "name" if key.startswith(("[CUSTOMER", "[AGENT", "[NAME"))
                    else key.strip("[]").lower())
            out[kind] |= values
        return out


def _names_pattern(variants: list[str], flags: int) -> re.Pattern | None:
    if not variants:
        return None
    body = "|".join(re.escape(v).replace(r"\ ", r"\s+") for v in sorted(set(variants), key=len, reverse=True))
    return re.compile(rf"(?<![\w@.])({body})(?![\w@])", flags)


# ---------------------------------------------------------------- formatting and checks

_LINE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] (?:Customer|Agent|System): \S.*$")
_CODES = "NZD|INR|BRL|MXN|SGD|HKD|CNY|RMB|KRW|PHP|SEK|NOK|DKK|PLN|ZAR|TRY|ILS|AED"
_UNSUPPORTED_AMOUNT = re.compile(  # case-sensitive and next to a number, so "try" or "php" in prose never count
    rf"(?:NZ\$|HK\$|(?<![A-Za-z])S\$|R\$|MX\$|₹|₩|₱|₺|₪|₫|₦)\s?\d|\d[\d,.]*\s?(?:₹|₩|₱|₺|₪|₫|₦|zł|kr\b)"
    rf"|\b(?:{_CODES})\s?\d|\d[\d,.]*\s?(?:{_CODES})\b")
_HEADERS = re.compile(r"(?i)\b(?:message-id|x-gm-thrid|in-reply-to|x-intercom-[\w-]+|delivered-to|x-mailgun-[\w-]+|"
                      r"references:|return-path)\b|<[^<>\s]+@[^<>\s]+>")


def format_turns(turns: list[Turn], texts: list[str]) -> str:
    lines = []
    for turn, text in zip(turns, texts):
        body = " ".join(text.split())
        if turn.attachments:
            body = f"{body} [ATTACHMENT]".strip()
        if not body and turn.images:
            body = "[IMAGE]"
        if body:
            lines.append(f"[{turn.when:%Y-%m-%d %H:%M}] {turn.role}: {body}")
    return "\n".join(lines)


def payload_problems(text: str, needles: dict[str, set[str]], surrogates: Iterable[str]) -> Counter:
    """Original values, or values that look personal, still present in a candidate payload.

    Name needles match the way the redactor does: dictionary-word tokens only when capitalized. Detector hits that
    fall inside an issued surrogate are not counted, since surrogates are meant to look like identifiers.
    """
    problems: Counter = Counter()
    words = dictionary()
    surrogates = [x for x in surrogates if x]
    for category, values in needles.items():
        loose = [v for v in values if len(v) >= 3 and not (" " not in v and v.lower() in words)]
        strict = [v[0].upper() + v[1:] for v in values if len(v) >= 3 and " " not in v and v.lower() in words]
        for rx in (_names_pattern(loose, re.I), _names_pattern(strict, 0)):
            if rx:
                problems[f"original_{category}"] += len(rx.findall(text))
    outside = lambda ms: [m for m in ms if not any(m.group() in x for x in surrogates)]  # noqa: E731
    for category, rx in (("email", _EMAIL), ("url", _URL), ("ip_address", _IP), ("government_id", _SSN),
                         ("source_header_or_id", _HEADERS), ("domain", _DOMAIN), ("phone", _PHONE)):
        problems[f"detected_{category}"] += len(outside(rx.finditer(text)))
    problems["detected_card"] += sum(_luhn(re.sub(r"\D", "", m.group())) for m in outside(_CARD.finditer(text)))
    problems["detected_long_number"] += len(outside(re.finditer(r"\d{7,}", text)))
    return +problems


def review_signals(text: str) -> Counter:
    """Weak signals for the human reviewer: remaining capitalized words that are not dictionary words."""
    words, given = dictionary(), given_names()
    signals: Counter = Counter()
    for line in text.splitlines():
        body = line.split(": ", 1)[-1]
        for m in re.finditer(r"(?<=[a-z,;] )([A-Z][a-z'’-]{2,20})\b", body):
            token = m.group(1)
            if token.lower() in _STOP:
                continue
            if token in given:
                signals["given_name_list_word"] += 1
            elif token.lower() not in words and token.lower() not in _STOP:
                signals["capitalized_non_dictionary_word"] += 1
    signals["intercom_mention"] += len(re.findall(r"(?i)intercom", text))
    return +signals


def suitability(text: str) -> Counter:
    """Aggregate fit for refund extraction; keyword hits nominate, they never label."""
    store = "\n".join(ln for ln in text.splitlines() if "] Agent: " in ln or "] System: " in ln)
    return Counter({
        "refund_keyword": bool(_REFUND.search(text)),
        "amount_with_currency": bool(re.search(r"(?:[$€£¥]\s?\d|\d\s?(?:usd|eur|gbp|dollars?)\b)", text, re.I)),
        "unsupported_currency": bool(_UNSUPPORTED_AMOUNT.search(text)),
        "order_reference": bool(re.search(r"(?i)\b(?:order|invoice|receipt|transaction)\b", text)),
        "store_date_expression": bool(re.search(
            r"(?i)\b(?:today|tomorrow|yesterday|business days?|working days?|next week|monday|tuesday|wednesday|"
            r"thursday|friday|within \d+|in \d+ days?)\b", store)),
    })


def opaque_id(taken: set[str]) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "MX-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if any(c.isdigit() for c in code[3:]) and any(c.isalpha() for c in code[3:]) and code not in taken:
            taken.add(code)
            return code


# ---------------------------------------------------------------- the preparation run


def prepare(mbox: Path | str, private_root: Path | str, *, size: int = 20, refund_share: float = 0.75,
            seed: int | None = None) -> dict:
    if not 1 <= size <= 20:
        raise SafeError("The exploratory batch holds 1 to 20 conversations")
    dirs = private_layout(private_root)
    mbox = Path(mbox).expanduser().resolve()
    if not mbox.is_file() or mbox.is_symlink() or not mbox.is_relative_to(dirs["raw"]):
        raise SafeError(f"The archive must be a regular file inside {dirs['raw']}")
    run = f"mboxprep-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    out = {k: dirs[k] / run for k in ("mappings", "quarantine", "deid-input", "results")}
    for path in out.values():
        path.mkdir(mode=0o700)
    with session(out["results"]):
        return _prepare(mbox, dirs, out, run, size, refund_share, secrets.randbits(32) if seed is None else seed)


def _prepare(mbox: Path, dirs: dict[str, Path], out: dict[str, Path], run: str, size: int, refund_share: float,
             seed: int) -> dict:
    archive = load_archive(mbox)
    zone = detect_render_zone(archive)
    if not zone["zone"]:
        raise SafeError("No render timezone explains the notification times; set render_timezone in the config")
    proposal = propose_config(archive, zone)
    _json(out["mappings"] / "config-proposal.json", proposal)
    config, created = load_config(dirs["mappings"] / CONFIG_NAME, proposal)
    config["render_timezone"] = config.get("render_timezone") or zone["zone"]
    if not config.get("store_timezone", {}).get("value"):
        raise SafeError("Set store_timezone.value in the private config before preparing a batch")
    conv_status, records, totals = eligible(archive, config, seed)
    lost = {c for c in archive.tainted if c not in archive.notifications}
    totals["conversations_without_any_usable_notification"] = len(lost)
    # Stratified simple random sample of eligible conversations.
    strata = {"refund_keyword": sorted(c for c, r in records.items() if r["fit"]["refund_keyword"]),
              "no_refund_keyword": sorted(c for c, r in records.items() if not r["fit"]["refund_keyword"])}
    quota = {"refund_keyword": round(size * refund_share)}
    quota["no_refund_keyword"] = size - quota["refund_keyword"]
    sampler = random.Random(seed)
    chosen: list[tuple[str, str]] = []
    for name, pool in strata.items():
        order = pool[:]
        sampler.shuffle(order)
        chosen += [(name, c) for c in order[:quota[name]]]
    taken: set[str] = set()
    batch, provenance, replacements, originals_out, per_record = [], {}, {}, [], []
    for stratum, conv in chosen:
        r = records[conv]
        t: Thread = r["thread"]
        rid = opaque_id(taken)
        batch.append({"id": rid, "input": r["input"]})
        originals_out.append({"id": rid, "original": r["original"]})
        notes = archive.notifications[conv]
        provenance[rid] = {"conversation_id": conv, "gmail_thread_ids": sorted(t.threads),
                           "notification_message_ids": t.message_ids, "notification_positions": [n.position for n in notes],
                           "notification_sent_utc": [t.sent[0].isoformat(), t.sent[1].isoformat()],
                           "render_timezone": config["render_timezone"],
                           "store_timezone": config["store_timezone"]["value"],
                           "turns": [{"role": x.role, "store_local": f"{x.when:%Y-%m-%d %H:%M}", "kind": x.kind}
                                     for x in t.turns],
                           "history_scope": "snapshot through the last notification in the archive"}
        replacements[rid] = {k: sorted(v) for k, v in r["redactor"].mapping.items()}
        per_record.append({"id": rid, "stratum": stratum, "turns": dict(r["roles"]), "chars": len(r["input"]),
                           "redactions": dict(+r["redactor"].counts), "flags": dict(+t.flags),
                           "excluded_parts": dict(t.excluded), "review_signals": dict(r["signals"]),
                           "review_status": "unreviewed"})
    batch_path = _write(out["deid-input"] / "batch.jsonl", "".join(json.dumps(b, ensure_ascii=False) + "\n"
                                                                     for b in batch))
    _json(out["mappings"] / "provenance.json", provenance)
    _json(out["mappings"] / "replacements.json", replacements)
    _write(out["mappings"] / "originals.jsonl", "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in originals_out))
    _json(out["quarantine"] / "messages.json", [{"position": p, "reason": r} for p, r in archive.quarantined])
    _json(out["quarantine"] / "conversations.json", {c: r for c, r in conv_status.items() if set(r) - OUT_OF_SCOPE})
    _json(out["results"] / "exclusions.json", dict(Counter(r for rs in conv_status.values() for r in rs)))
    status_counts = Counter(r for rs in conv_status.values() for r in rs)
    n_conv = len(conv_status)
    quarantined_convs = sum(1 for rs in conv_status.values() if set(rs) - OUT_OF_SCOPE)
    excluded_convs = sum(1 for rs in conv_status.values() if rs and not set(rs) - OUT_OF_SCOPE)
    selection = {
        "frame": "Every Intercom conversation with at least one teammate notification in the private archive "
                 "(Gmail label export); notification reasons as Intercom recorded them.",
        "archive_sha256_manifest": str(mbox) + ".manifest.json",
        "inclusion": ["reconstructed without conflicts or truncation", "at least one Customer and one store message",
                      f"at most {MAX_INPUT_CHARS} characters after de-identification", "no unsupported currency",
                      "no residual original or detected sensitive value"],
        "strata": {k: len(v) for k, v in strata.items()}, "quota": quota, "seed": seed,
        "method": "simple random sample without replacement within each stratum (random.Random(seed).shuffle of "
                  "sorted conversation IDs)", "challenge_set": "none selected",
        "keyword_note": "Refund keywords nominate a stratum only; they are not labels and do not make a conversation "
                        "in scope.",
        "selected": [{"id": b["id"], "stratum": s} for b, (s, _) in zip(batch, chosen)],
    }
    _json(out["results"] / "selection.json", selection)
    _json(out["results"] / "records.json", per_record)
    _json(out["results"] / "BATCH-STATUS.json", {
        "batch": str(batch_path), "records": len(batch), "approved": False, "approved_by": None,
        "destination": None, "required_before_any_transmission": [
            "review every record against mappings/<run>/originals.jsonl", "confirm redactions and residual values",
            "confirm the store timezone and every author role in mappings/prep-config.json",
            "confirm authorization to send these de-identified conversations to the named provider",
            "explicitly approve this batch file by its SHA-256"],
        "batch_sha256": hashlib.sha256(batch_path.read_bytes()).hexdigest()})
    config_complete = bool(config["store_timezone"].get("confirmed")) and all(
        config["authors"].get(a, {}).get("confirmed") for r in (records[c] for _, c in chosen) for a in r["thread"].staff)
    report = {
        "run": run, "archive_messages": archive.counts["messages"], "archive_bytes": archive.counts["bytes"],
        "messages_quarantined": dict(Counter(r for _, r in archive.quarantined)),
        "duplicate_message_ids": {k: archive.counts[k] for k in ("duplicate_message_id_identical",
                                                                 "duplicate_message_id_conflict")},
        "notifications_used": sum(len(v) for v in archive.notifications.values()),
        "conversations": n_conv, "conversations_lost": totals["conversations_without_any_usable_notification"],
        "conversations_quarantined": quarantined_convs,
        "conversations_excluded": excluded_convs, "conversations_eligible": len(records),
        "reasons": dict(status_counts),
        "gmail_threads": len({n.thread for v in archive.notifications.values() for n in v}),
        "conversations_spanning_several_gmail_threads": sum(
            len({n.thread for n in v}) > 1 for v in archive.notifications.values()),
        "render_timezone": {k: zone[k] for k in ("zone", "samples", "matched", "share", "ties")},
        "store_timezone": {"value": config["store_timezone"]["value"],
                           "confirmed": bool(config["store_timezone"].get("confirmed"))},
        "config": {"created_now": created, "authors": len(config.get("authors", {})),
                   "authors_proposed_system": sum(a.get("role") == "System" for a in config["authors"].values()),
                   "authors_confirmed": sum(bool(a.get("confirmed")) for a in config["authors"].values()),
                   "complete_for_batch": config_complete},
        "totals": dict(sorted(totals.items())),
        "batch": {"records": len(batch), "strata": dict(Counter(s for s, _ in chosen)), "approved": False,
                  "redactions": dict(sum((Counter(r["redactions"]) for r in per_record), Counter())),
                  "review_signals": dict(sum((Counter(r["review_signals"]) for r in per_record), Counter()))},
    }
    archive_time = min(mbox.stat().st_mtime, getattr(mbox.stat(), "st_birthtime", mbox.stat().st_mtime))
    report["checks"] = run_checks(out, batch, [records[c] for _, c in chosen], archive_time)
    _json(out["results"] / "readiness.json", report)
    _write(out["results"] / "REVIEW-CHECKLIST.md", checklist(run, out, per_record, report))
    return {"report": report, "paths": {k: str(v) for k, v in out.items()} | {"batch": str(batch_path),
                                                                                 "config": str(dirs["mappings"] / CONFIG_NAME)}}


def eligible(archive: Archive, config: dict, seed: int) -> tuple[dict[str, list[str]], dict[str, dict], Counter]:
    """Reconstruct, clean and de-identify every conversation: (exclusion reasons, eligible records, totals)."""
    global_staff = sorted(config.get("authors", {}))
    originals = set()
    for store in archive.parts.values():
        for p in store.values():
            originals |= identifiers(p.text)
    surrogates = Surrogates(originals, random.Random(seed))
    frequency: Counter = Counter()
    for store in archive.parts.values():
        frequency.update({m.group(1) for p in store.values() for m in _MID_CAPITAL.finditer(p.text)})
    common = frozenset(w for w, n in frequency.items() if n >= COMMON_TERM_CONVERSATIONS)
    conv_status: dict[str, list[str]] = {}
    records: dict[str, dict] = {}
    totals: Counter = Counter()
    for conv in sorted(archive.notifications):
        t = reconstruct(conv, archive, config)
        if t.quarantine:
            conv_status[conv] = sorted(set(t.quarantine))
        else:
            conv_status[conv], record = _deidentify(t, config, global_staff, surrogates, common, totals)
            if record:
                records[conv] = record
        totals.update({f"flag:{k}": v for k, v in t.flags.items() if v})
        totals.update({f"excluded_part:{k}": v for k, v in t.excluded.items() if v})
    return conv_status, records, totals


def _deidentify(t: Thread, config: dict, global_staff: list[str], surrogates: Surrogates, common: frozenset[str],
                totals: Counter) -> tuple[list[str], dict | None]:
    """Clean, de-identify and check one reconstructed conversation: (exclusion reasons, eligible record)."""
    history, texts = "", []
    for turn in t.turns:
        texts.append(clean(turn.text, history, t.flags))
        history += "\n" + texts[-1]
    roles = Counter(turn.role for turn, text in zip(t.turns, texts) if text or turn.attachments or turn.images)
    customers = t.customers + sorted(t.hints - set(t.customers))
    others = sorted((t.names - set(customers) - set(t.staff)) | t.emails)
    redactor = Redactor(t.conversation, customers, t.staff, others, global_staff, surrogates)
    redactor.learn(texts, common)
    original = format_turns(t.turns, texts)
    deid = format_turns(t.turns, [redactor(x) for x in texts])
    replaced = redactor.originals()
    needles = {
        "name": {v for p in redactor.people for v in p.variants if " " in v or p.group != "other"},
        "email": t.emails | {e.split("@")[0] for e in t.emails if len(e.split("@")[0]) >= 4},
        "identifier": {v for k in ("identifier", "account_id", "card", "card_last4", "phone", "ssn", "token", "ip")
                       for v in replaced.get(k, ()) if len(v) >= 4},
        "address_or_school": replaced.get("address", set()) | replaced.get("school", set()),
        "url": replaced.get("url", set()) | replaced.get("domain", set()),
        "attachment_name": {f for f in t.files if len(f) >= 6},
        "source_id": {t.conversation, *t.threads, *t.source_ids, *(m.strip("<>") for m in t.message_ids if m),
                      *(r.strip("<>") for r in t.references), *((config.get("owner_domain"),) if config.get(
                          "owner_domain") else ())},
    }
    problems = payload_problems(deid, needles, redactor.sentinels)
    fit = suitability(original)
    reasons = [r for r, bad in (("no_customer_message", not roles["Customer"]),
                                ("no_store_message", not roles["Agent"] + roles["System"]),
                                ("too_long", len(deid) > MAX_INPUT_CHARS),
                                ("unsupported_currency", fit["unsupported_currency"]),
                                ("residual_sensitive_value", bool(problems)),
                                ("format_check_failed", any(not _LINE.match(ln) for ln in deid.splitlines())))
               if bad]
    totals.update({f"redaction:{k}": v for k, v in redactor.counts.items()})
    totals.update({f"residual:{k}": v for k, v in problems.items()})
    totals.update({f"fit:{k}": v for k, v in fit.items()})
    if reasons:
        return reasons, None
    return [], {"thread": t, "original": original, "input": deid, "redactor": redactor, "fit": fit, "roles": roles,
                "signals": review_signals(deid)}


def run_checks(out: dict[str, Path], batch: list[dict], chosen: list[dict], archive_time: float) -> dict:
    """Local privacy checks on what was written: payload shape, file modes, and the repository leak scan.

    Repository hits name a file and a kind of match (never the value) in a private log, with whether the file was
    last changed before the archive's own modification time: such a hit is a shared phrase or an older copy of
    the same data, which a person must judge.
    """
    checks: dict[str, object] = {}
    checks["batch_keys_only_id_and_input"] = all(set(b) == {"id", "input"} for b in batch)
    checks["batch_at_most_20"] = len(batch) <= 20
    checks["batch_lines_well_formed"] = all(_LINE.match(ln) for b in batch for ln in b["input"].splitlines())
    checks["no_expected_labels"] = not any("expected" in b for b in batch)
    bad_modes = [p for d in out.values() for p in [d, *d.rglob("*")]
                 if (p.stat().st_mode & 0o077) or (p.is_dir() and p.stat().st_mode & 0o700 != 0o700)]
    checks["owner_only_files"] = not bad_modes
    checks["passed"] = all(checks.values())
    needles = Needles()
    names: dict[str, str] = {}
    for r in chosen:
        t: Thread = r["thread"]
        for e in t.emails:
            needles.add_id(e, "email")
        needles.add_id(t.conversation, "conversation id")
        for vs in r["redactor"].mapping.values():
            for v in vs:
                needles.add_id(v, "replaced value")
        needles.add_text(r["original"], "original conversation", owner=t.conversation)
        for person in r["redactor"].people:
            for v in person.variants:
                if " " in v:
                    names.setdefault(" ".join(v.split()).casefold(), person.group)
    needles.drop_boilerplate()
    hits, older, details = 0, 0, []
    for path in repository_files(REPO_ROOT):
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", "replace")
        folded = " ".join(text.split()).casefold()
        lines = [" ".join(ln.split()).casefold() for ln in text.splitlines()]
        found = [c for c, _ in needles.find(text)] + [
            f"full name ({g}) near line {next((i + 1 for i, ln in enumerate(lines) if n in ln), '?')}"
            for n, g in names.items() if n in folded]
        if found:
            before = path.stat().st_mtime < archive_time
            hits += len(found)
            older += len(found) * before
            details.append(f"{path.relative_to(REPO_ROOT)}: {sorted(set(found))}"
                           f"{' (file last changed before the archive)' if before else ''}")
    checks["repository_hits"] = hits
    checks["repository_hits_in_files_older_than_archive"] = older
    checks["repository_clean"] = hits == 0
    if details:
        _write(out["results"] / "logs" / "repository-hits.txt", "\n".join(details) + "\n")
    return checks


def checklist(run: str, out: dict[str, Path], records: list[dict], report: dict) -> str:
    rows = "\n".join(
        f"| {r['id']} | {r['stratum']} | {r['turns'].get('Customer', 0)}/{r['turns'].get('Agent', 0)}/"
        f"{r['turns'].get('System', 0)} | {r['chars']} | {sum(r['redactions'].values())} | "
        f"{', '.join(sorted(k for k in r['flags'] if k not in ('year_confirmed',))) or '-'} | "
        f"{sum(r['review_signals'].values())} | [ ] |" for r in records)
    return f"""# Local review checklist: {run}

**Status: UNAPPROVED.** Nothing from this batch may be sent to Nebius Token Factory or any other service until a
person has reviewed every record below and explicitly approved the batch file by its SHA-256
(`{out['results'] / 'BATCH-STATUS.json'}`).

Review locally only. Do not paste records into a chat, an AI tool, a ticket or a shared document.

## Files

- Proposed payload: `{out['deid-input'] / 'batch.jsonl'}` (one object per conversation: `id`, `input`)
- Originals for side-by-side comparison (private): `{out['mappings'] / 'originals.jsonl'}`
- Placeholder and surrogate mapping (private): `{out['mappings'] / 'replacements.json'}`
- Provenance and timestamps (private): `{out['mappings'] / 'provenance.json'}`
- Per-record counts and flags: `{out['results'] / 'records.json'}`
- Selection frame, seed and strata: `{out['results'] / 'selection.json'}`

## Decisions to confirm first (private config: `mappings/{CONFIG_NAME}`)

1. **Store timezone.** Proposed `{report['store_timezone']['value']}`; confirmed: {report['store_timezone']['confirmed']}.
   It is the zone in which Intercom rendered part times; confirm that it is where the store's agents meant
   "today" and "tomorrow".
2. **Author roles.** Every teammate author is `Agent` unless set to `System` (automated, such as an AI agent or
   workflow). {report['config']['authors_proposed_system']} of {report['config']['authors']} authors are proposed as
   System; {report['config']['authors_confirmed']} are confirmed.
3. **Internal notes and events are excluded.** They are never shown to the customer.
4. **Dates are not shifted.** Amounts, currencies, dates and the store's own name remain; they can still identify
   a conversation in context. This batch is de-identified, not anonymous.
5. **Authorization.** Confirm you may send these de-identified customer conversations to the named provider under
   your organization's policies and the provider's terms.

## For every record

- [ ] No name, email, phone, postal address, account or card number, credential, URL or file name remains.
- [ ] Placeholders are consistent: `[CUSTOMER]`, `[AGENT_A]`, `[NAME_1]` refer to the same person throughout.
- [ ] Speakers are right: Customer vs Agent/System, and no internal note appears.
- [ ] Timestamps are in the store timezone and in order.
- [ ] Nothing that matters for refunds was removed: requests, amounts, order references, issue dates, corrections.
      Compare with the original; quoted history and signatures were removed only when repeated or short.
- [ ] Forwarded content (flag `forwarded_content`) is attributed acceptably.

| id | stratum | turns C/A/S | chars | redactions | flags | weak name signals | reviewed |
|---|---|---|---|---|---|---|---|
{rows}

Mark each record's `review_status` in `records.json` as `approved` or `rejected`. Records inspected for debugging or
by a model are exploration material, never untouched confirmation data. No labels exist for this batch.
"""


def main(args) -> None:
    result = prepare(args.mbox, args.private_root, size=args.size, refund_share=args.refund_share, seed=args.seed)
    report = result["report"]
    print(json.dumps({k: report[k] for k in ("run", "archive_messages", "messages_quarantined", "conversations",
                                             "conversations_quarantined", "conversations_excluded",
                                             "conversations_eligible", "reasons", "render_timezone",
                                             "store_timezone", "config", "batch", "checks")}, indent=1))
    print("Private paths:")
    for k, v in result["paths"].items():
        print(f"  {k}: {v}")
    print("The batch is UNAPPROVED. Review it locally before any transmission.")
