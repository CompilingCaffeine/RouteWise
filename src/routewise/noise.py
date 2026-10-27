"""Writing noise measured in a real, de-identified workload and injected into labelled synthetic conversations.

Noise component of study m8 (displayed as M7); protocol: the 2026-10-26 amendment to
experiments/M8-workload-validation.md.

measure     aggregate counts per speaker role from formatted conversations; neither a conversation nor a
            per-conversation value is ever part of the result
inject      a seeded channel that rewrites one conversation the way the measured writers would, logging every edit
invariants  checks that a noisy conversation kept everything its label depends on

The channel changes how a message is written, not what it says:
- digits and every token containing one, all-caps tokens (currency codes, order prefixes), timestamps and speaker
  roles are never edited, and System messages are never touched;
- a misspelling is a non-word at edit distance 1 from a dictionary word, so a careful reader recovers one word;
- substitutions (dropped apostrophes, chat abbreviations, "could of") come from fixed, meaning-preserving lists;
- splitting a message keeps its date, and a run-on only drops full stops.
So the clean conversation's label is also the noisy conversation's label.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import timedelta
from random import Random
from typing import Any

from routewise.synth import ORDER_LIKE
from routewise.textfacts import _MONTHS, money_mentions, parse_messages

VERSION = 1
ROLES = ("Customer", "Agent")
MIN_LETTERS = 4  # shorter words are never misspelled, in the measurement or the channel
LENGTH_BUCKETS = {"4-5": (4, 5), "6-7": (6, 7), "8+": (8, 99)}
SPLIT_WITHIN = timedelta(minutes=5)  # a message followed this soon by the same role counts as split
RUN_ON_WORDS = 20  # run-ons are counted among messages at least this long
TABLE_MIN_CONVERSATIONS = 5  # a misspelling pair is published only if this many conversations contain it
RIGHT_MIN_COUNT = 10
RIGHT_TO_WRONG = 10  # a correction must be this many times more frequent than the misspelling
FEATURES = ("misspelling", "apostrophe_drop", "abbreviation", "confusion", "repeated_word", "pronoun_i_lowercase",
            "message_all_lowercase", "message_lowercase_start", "sentence_lowercase_start", "no_terminal_punctuation",
            "repeated_punctuation", "comma_no_space", "run_on", "split_message", "straight_apostrophe",
            "spaced_hyphen", "emoticon")
EDITABLE = ("split_message", "no_terminal_punctuation", "comma_no_space", "message_lowercase_start")
TYPO_KINDS = ("deletion", "insertion_double", "insertion_other", "substitution_adjacent", "substitution_other",
              "transposition")
TYPO_POSITIONS = ("first", "interior", "last")
CONTRACTIONS = ("don't", "didn't", "doesn't", "isn't", "wasn't", "weren't", "aren't", "haven't", "hasn't", "couldn't",
                "wouldn't", "shouldn't", "can't", "won't", "i'm", "i've", "you're", "they're", "that's", "what's",
                "there's")
ABBREVIATIONS = {  # full form -> written forms, longest first so "thank you" is seen before "you"
    "thank you": ("ty",), "tomorrow": ("tmrw", "tmr"), "because": ("bc", "cuz"), "message": ("msg",),
    "people": ("ppl",), "please": ("pls", "plz"), "thanks": ("thx", "thnx"), "though": ("tho",), "your": ("ur",),
    "with": ("w/",), "you": ("u",)}
CONFUSIONS = {"could have": "could of", "would have": "would of", "should have": "should of", "a lot": "alot",
              "you're welcome": "your welcome", "rather than": "rather then", "more than": "more then",
              "less than": "less then", "better than": "better then", "other than": "other then"}
EMOTICONS = (":)", "🙂", "🙏")
# Words the dictionary lacks: irregular verb and plural forms ("heard" is not a typo of "hear"), and contractions
# without their apostrophe, which are counted as dropped apostrophes instead.
IRREGULAR = frozenset("""
arose arisen awoke awoken bore borne beat beaten became begun began bent bit bitten bled blew blown broke broken bred
brought built burnt burst bought caught chose chosen clung came crept dealt drew drawn dreamt drank drunk drove driven
ate eaten fell fallen felt fought found fled flung flew flown forbade forbidden forgot forgotten forgave forgiven froze
frozen gotten gave given went gone ground grew grown hung heard hid hidden held kept knelt knew known laid leant leapt
learnt left lent lain lost made meant paid proven rode ridden rang rung rose risen said seen sought sold sent sewn shook
shaken shone shot shown shrank shrunk slept slid slung spoke spoken sped spent spilt spun spat sprang sprung stood stole
stolen stuck stung stank stunk strode struck strung strove striven swore sworn swept swollen swam swum swung took taken
taught tore torn told thought threw thrown trod trodden understood undertook undertaken woke woken wore worn wove woven
wept wound withdrew withdrawn wrote written children women feet teeth mice geese people""".split()) | frozenset(
    c.replace("'", "") for c in CONTRACTIONS)
NEGATIONS = frozenset({"not", "no", "never", "nothing", "none", "neither", "nor", "cannot"})
# Words a date or refund reading may hinge on; edits to them are tagged for the descriptive breakdown only.
LABEL_WORDS = frozenset({m.lower() for m in _MONTHS} | {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "today", "tonight", "tomorrow",
    "yesterday", "day", "days", "week", "weeks", "weekend", "month", "months", "hour", "hours", "business", "working",
    "fortnight", "next", "this", "last", "within", "until", "before", "after", "refund", "refunds", "refunded",
    "refunding", "issue", "issued", "issuing", "process", "processed", "processing", "send", "sent", "sending",
    "initiated", "submitted", "released", "arrive", "arrives", "arrived", "arrival", "reach", "appear", "credit",
    "credited", "correction", "corrected", "instead", "actually", "update", "updated", "revised", "changed", "back"})

_PROTECTED = re.compile(r"\S*\d\S*|\b[A-Z]{2,}\b|\[[A-Z][A-Z_0-9]*\]")
_ELIGIBLE = re.compile(r"(?<![\w'’-])((?:[A-Z][a-z]{3,}|[a-z]{4,}))(?![\w'’-])")
_TOKEN = re.compile(r"[A-Za-z]+")
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)")
_INTERNAL = re.compile(r"[.!?]\s+\S")
_RUN_ON_STOP = re.compile(r"[.!]\s+([A-Za-z][\w'’]*)")
_MID_CAPITAL = re.compile(r"(?<=[a-z,;:] )([A-Z][a-z]{3,})\b")
_ALL_CAPS = re.compile(r"\b[A-Z]{2,}\b")
_MIXED_CASE = re.compile(r"\b[A-Za-z]*[a-z][A-Z][A-Za-z]*\b")  # iPhone, eBay, PayPal: names, never typos
_APOSTROPHE = re.compile(r"(?<=[A-Za-z])['’](?=[A-Za-z])")
_CURLY = re.compile(r"(?<=[A-Za-z])’(?=[A-Za-z])")
_DASH = re.compile(r"\s*[—–]\s*")
_SPACED_HYPHEN = re.compile(r"\s-\s")
_EMOTICON = re.compile(r"[:;]-?[)(DPp](?!\w)|<3|[\U0001F300-\U0001FAFF☀-➿]")
_TRAILING = re.compile(r"(?:\s|[)\"'”’]|" + _EMOTICON.pattern + r")+$")
_PRONOUN = re.compile(r"(?<![\w'’])[Ii](?=['’](?:m|ve|ll|d)\b|m\b|ve\b|(?![\w'’]))")
_CAPITALIZED = re.compile(r"\b[A-Za-z]*[A-Z][A-Za-z]*\b")
_NOT_PRONOUN = r"(?![Ii](?:['’](?:m|ve|ll|d)|m|ve)?\b)"  # the pronoun is counted and edited only as `pronoun_i`
_MESSAGE_START = re.compile(rf"^[\"'“(]*{_NOT_PRONOUN}([A-Z][a-z'’]*)\b")
_SENTENCE_START = re.compile(rf"(?<=[.!?] ){_NOT_PRONOUN}([A-Z][a-z'’]*)\b")
_COMMA = re.compile(r",\s+(?=[A-Za-z])")
_REPEATABLE = re.compile(r"(?<!['’])\b([A-Za-z]{2,})\b(?!['’])")
_REPEATED_OK = frozenset({"that", "had"})


def _phrase(text: str) -> str:
    return r"\s+".join(re.escape(w).replace("'", "['’]") for w in text.split())


def _whole(alternatives: Iterable[str]) -> re.Pattern[str]:
    body = "|".join(_phrase(a) for a in sorted(alternatives, key=len, reverse=True))
    return re.compile(rf"(?<![\w'’/@-])(?:{body})(?![\w'’/@-])", re.IGNORECASE)


_CONTRACTED = _whole(CONTRACTIONS)
_DROPPED = _whole(c.replace("'", "") for c in CONTRACTIONS)
_FULL = {full: _whole([full]) for full in ABBREVIATIONS}
_SHORT = {full: _whole(forms) for full, forms in ABBREVIATIONS.items()}
_CORRECT = {right: _whole([right] + (["you are welcome"] if right == "you're welcome" else [])) for right in CONFUSIONS}
_WRONG = {right: _whole([wrong]) for right, wrong in CONFUSIONS.items()}

_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
_KEYS = {c: (r, i + (0, 0.25, 0.75)[r]) for r, row in enumerate(_ROWS) for i, c in enumerate(row)}
NEIGHBORS = {a: "".join(b for b in _KEYS if b != a and math.dist(_KEYS[a], _KEYS[b]) <= 1.3) for a in _KEYS}
_VOWELS = "aeiou"


# ---------------------------------------------------------------- vocabulary


def known(word: str, words: frozenset[str]) -> bool:
    """A dictionary word, an irregular form, or a regular inflection of a dictionary word."""
    if word in words or word in IRREGULAR:
        return True
    for suffix, endings in (("ies", ("y",)), ("ied", ("y",)), ("ing", ("", "e")), ("ed", ("", "e")), ("es", ("",)),
                            ("s", ("",)), ("er", ("", "e")), ("est", ("", "e")), ("ly", ("",)), ("ment", ("",)),
                            ("ness", ("",)), ("ful", ("",))):
        stem = word[:-len(suffix)]
        if word.endswith(suffix) and len(stem) >= 2:
            if any(stem + e in words for e in endings) or (stem[-1] == stem[-2] and stem[:-1] in words):
                return True
    return False


def edits1(word: str) -> set[str]:
    letters = "abcdefghijklmnopqrstuvwxyz"
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    return ({a + b[1:] for a, b in splits if b} | {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
            | {a + c + b[1:] for a, b in splits if b for c in letters} | {a + c + b for a, b in splits for c in letters})


def classify(wrong: str, right: str) -> tuple[str, str] | None:
    """The single edit turning `right` into `wrong`: (kind, position), or None if there is none."""
    n = len(right)

    def where(i: int, last: int) -> str:
        return "first" if i == 0 else "last" if i >= last else "interior"

    if len(wrong) == n - 1:
        for i in range(n):
            if right[:i] + right[i + 1:] == wrong:
                return "deletion", where(i, n - 1)
    elif len(wrong) == n + 1:
        for i in range(n + 1):
            if wrong[:i] + wrong[i + 1:] == right:
                double = (i > 0 and wrong[i] == wrong[i - 1]) or (i + 1 < len(wrong) and wrong[i] == wrong[i + 1])
                return ("insertion_double" if double else "insertion_other"), where(i, n)
    elif len(wrong) == n and wrong != right:
        diff = [i for i in range(n) if wrong[i] != right[i]]
        if len(diff) == 2 and diff[1] == diff[0] + 1 and wrong[diff[0]] == right[diff[1]] \
                and wrong[diff[1]] == right[diff[0]]:
            return "transposition", where(diff[0], n - 2)
        if len(diff) == 1:
            i = diff[0]
            adjacent = wrong[i] in NEIGHBORS.get(right[i], "")
            return ("substitution_adjacent" if adjacent else "substitution_other"), where(i, n - 1)
    return None


def _bucket(n: int) -> str:
    return next(name for name, (lo, hi) in LENGTH_BUCKETS.items() if lo <= n <= hi)


# ---------------------------------------------------------------- measurement


def _feature(pairs: list[tuple[int, int]]) -> dict[str, Any]:
    """Pooled rate and between-writer variance (method of moments for a beta-binomial)."""
    used = [(k, n) for k, n in pairs if n]
    k, n = sum(k for k, _ in used), sum(n for _, n in used)
    p = k / n if n else 0.0
    var = 0.0
    if len(used) > 1 and 0 < p < 1:
        spread = sum(m * (j / m - p) ** 2 for j, m in used)
        denominator = n - sum(m * m for _, m in used) / n
        if denominator > 0:
            var = min(max(0.0, (spread - p * (1 - p) * (len(used) - 1)) / denominator), 0.99 * p * (1 - p))
    return {"k": k, "n": n, "rate": p, "writer_var": var, "writers": len(used)}


def _all_lowercase(body: str) -> bool:
    letters = [ch for ch in _PROTECTED.sub(" ", body) if ch.isalpha()]
    return len(letters) >= 10 and not any(ch.isupper() for ch in letters)


def _count(rx: re.Pattern[str], text: str) -> int:
    return sum(1 for m in rx.finditer(text) if not (len(m.group()) > 1 and m.group().isupper()))


def _message_counts(body: str, c: Counter) -> None:
    """Every per-message count except misspellings and splits, added to `c` as (feature, 'k'|'n')."""
    free = _PROTECTED.sub(" ", body)
    lower = _all_lowercase(body)
    c["message_all_lowercase", "n"] += 1
    c["message_all_lowercase", "k"] += lower
    if not lower:
        if m := re.match(rf"[\"'“(]*{_NOT_PRONOUN}([A-Za-z])", body):
            c["message_lowercase_start", "n"] += 1
            c["message_lowercase_start", "k"] += m.group(1).islower()
            i = m.start(1)
            c["editable:message_lowercase_start", "n"] += 1
            c["editable:message_lowercase_start", "k"] += m.group(1).isupper() and not _all_lowercase(
                body[:i] + body[i].lower() + body[i + 1:])
        for m in re.finditer(rf"(?<=[.!?])\s+{_NOT_PRONOUN}([A-Za-z])([A-Za-z]*)", body):
            if not (m.group(2) and (m.group(1) + m.group(2)).isupper()):
                c["sentence_lowercase_start", "n"] += 1
                c["sentence_lowercase_start", "k"] += m.group(1).islower()
        for m in _PRONOUN.finditer(body):
            c["pronoun_i_lowercase", "n"] += 1
            c["pronoun_i_lowercase", "k"] += m.group() == "i"
    dropped, full = _count(_DROPPED, body), _count(_CONTRACTED, body)
    c["apostrophe_drop", "k"] += dropped
    c["apostrophe_drop", "n"] += dropped + full
    for name, (short, long) in {**{f: (_SHORT[f], _FULL[f]) for f in ABBREVIATIONS},
                                **{r: (_WRONG[r], _CORRECT[r]) for r in CONFUSIONS}}.items():
        feature = "abbreviation" if name in ABBREVIATIONS else "confusion"
        k = _count(short, body)
        c[feature, "k"] += k
        c[feature, "n"] += k + _count(long, body)
        c[f"{feature}:{name}", "k"] += k
        c[f"{feature}:{name}", "n"] += k + _count(long, body)
    for form in CONTRACTIONS:
        k = _count(_whole([form.replace("'", "")]), body)
        c[f"contraction:{form}", "k"] += k
        c[f"contraction:{form}", "n"] += k + _count(_whole([form]), body)
    tokens = [t.lower() for t in _TOKEN.findall(free)]
    c["repeated_word", "n"] += max(0, len(tokens) - 1)
    c["repeated_word", "k"] += sum(a == b and a not in _REPEATED_OK for a, b in zip(tokens, tokens[1:]))
    stripped = _TRAILING.sub("", body)
    if any(ch.isalpha() for ch in stripped):
        c["no_terminal_punctuation", "n"] += 1
        c["no_terminal_punctuation", "k"] += not stripped.endswith((".", "!", "?", "…"))
    if re.search(r"[!?]", body):
        c["repeated_punctuation", "n"] += 1
        c["repeated_punctuation", "k"] += bool(re.search(r"[!?]{2,}", body))
    tight = len(re.findall(r",(?=[A-Za-z])", body))
    c["comma_no_space", "k"] += tight
    c["comma_no_space", "n"] += tight + len(_COMMA.findall(body))
    mask = _protected(body) + [False]
    c["editable:comma_no_space", "k"] += sum(not any(mask[max(0, m.start() - 1):m.end() + 1])
                                             for m in _COMMA.finditer(body))
    c["editable:comma_no_space", "n"] += len(_COMMA.findall(body))
    c["editable:no_terminal_punctuation", "k"] += body.endswith((".", "!")) and not mask[len(body) - 1]
    c["editable:split_message", "k"] += len(_BOUNDARY.split(body)) >= 2
    c["editable:no_terminal_punctuation", "n"] += 1
    c["editable:split_message", "n"] += 1
    if len(_TOKEN.findall(body)) >= RUN_ON_WORDS:
        c["run_on", "n"] += 1
        c["run_on", "k"] += not _INTERNAL.search(body.rstrip())
    apostrophes = _APOSTROPHE.findall(body)
    c["straight_apostrophe", "n"] += len(apostrophes)
    c["straight_apostrophe", "k"] += apostrophes.count("'")
    hyphens, dashes = len(_SPACED_HYPHEN.findall(body)), len(re.findall(r"[—–]", body))
    c["spaced_hyphen", "n"] += hyphens + dashes
    c["spaced_hyphen", "k"] += hyphens
    c["emoticon", "n"] += 1
    c["emoticon", "k"] += bool(_EMOTICON.search(body))


def measure(conversations: Iterable[str], words: frozenset[str], exclude: frozenset[str] = frozenset()) -> dict:
    """Aggregate writing-noise profile of formatted conversations, per role.

    Misspellings are tokens of at least four letters that are not known words (`known`) and are never seen
    capitalized inside a sentence, in capitals or in mixed case anywhere (names, products, acronyms), and whose most frequent edit-distance-1 neighbour
    in the corpus is a known word at least ten times more frequent. Pairs in at least five conversations, whose words
    are not in `exclude`, form the published table. Real-word errors (their/there) are not detected.
    """
    convs = [[(m.speaker, m.when, m.text) for m in parse_messages(text)] for text in conversations]
    freq: Counter = Counter()
    proper: set[str] = set()
    for conv in convs:
        for _, _, body in conv:
            free = _PROTECTED.sub(" ", body)
            freq.update(m.group(1).lower() for m in _ELIGIBLE.finditer(free))
            proper.update(m.group(1).lower() for m in _MID_CAPITAL.finditer(body))
            proper.update(t.lower() for t in _ALL_CAPS.findall(body))
            proper.update(t.lower() for t in _MIXED_CASE.findall(body))
    frequent = {w: n for w, n in freq.items() if n >= RIGHT_MIN_COUNT and known(w, words)}
    corrections: dict[str, str | None] = {}

    def correction(token: str) -> str | None:
        if token not in corrections:
            right = None
            if token not in proper and not known(token, words):
                candidates = [(frequent[w], w) for w in edits1(token) if w in frequent]
                if candidates:
                    count, best = max(candidates)
                    right = best if count >= RIGHT_TO_WRONG * freq[token] else None
            corrections[token] = right
        return corrections[token]

    roles: dict[str, Any] = {r: {"messages": 0, "words": 0, "per_writer": defaultdict(list), "totals": Counter(),
                                  "kinds": Counter(), "positions": Counter(), "lengths": []} for r in ROLES}
    pairs: dict[tuple[str, str], set[int]] = defaultdict(set)
    for index, conv in enumerate(convs):
        per_role: dict[str, Counter] = {r: Counter() for r in ROLES}
        for i, (role, when, body) in enumerate(conv):
            if role not in ROLES:
                continue
            c, stats = per_role[role], roles[role]
            stats["messages"] += 1
            stats["words"] += len(_TOKEN.findall(body))
            stats["lengths"].append(len(_TOKEN.findall(body)))
            _message_counts(body, c)
            c["split_message", "n"] += 1
            c["split_message", "k"] += i + 1 < len(conv) and conv[i + 1][0] == role and \
                conv[i + 1][1] - when <= SPLIT_WITHIN
            for m in _ELIGIBLE.finditer(_PROTECTED.sub(" ", body)):
                token = m.group(1).lower()
                bucket = _bucket(len(token))
                c["misspelling", "n"] += 1
                c[f"misspelling:{bucket}", "n"] += 1
                if right := correction(token):
                    c["misspelling", "k"] += 1
                    c[f"misspelling:{bucket}", "k"] += 1
                    edit = classify(token, right)
                    if edit:
                        stats["kinds"][edit[0]] += 1
                        stats["positions"][edit[1]] += 1
                    pairs[token, right].add(index)
        for role, c in per_role.items():
            roles[role]["totals"].update(c)
            for feature in FEATURES:
                roles[role]["per_writer"][feature].append((c[feature, "k"], c[feature, "n"]))
    out: dict[str, Any] = {"kind": "routewise-noise-profile", "version": VERSION, "conversations": len(convs),
                           "thresholds": {"min_letters": MIN_LETTERS, "table_min_conversations": TABLE_MIN_CONVERSATIONS,
                                          "right_min_count": RIGHT_MIN_COUNT, "right_to_wrong": RIGHT_TO_WRONG,
                                          "run_on_words": RUN_ON_WORDS,
                                          "split_within_minutes": SPLIT_WITHIN.total_seconds() / 60},
                           "roles": {}}
    for role, stats in roles.items():
        t = stats["totals"]

        def group(prefix: str, names: Iterable[str]) -> dict[str, dict[str, int]]:
            return {n: {"k": t[f"{prefix}:{n}", "k"], "n": t[f"{prefix}:{n}", "n"]} for n in names}

        lengths = sorted(stats["lengths"])
        out["roles"][role] = {
            "messages": stats["messages"], "words": stats["words"],
            "words_per_message": {f"p{q}": lengths[min(len(lengths) - 1, int(q / 100 * len(lengths)))]
                                  for q in (10, 50, 90)} if lengths else {},
            "features": {f: _feature(stats["per_writer"][f]) for f in FEATURES},
            "misspelling_by_length": group("misspelling", LENGTH_BUCKETS),
            "typo_kinds": {k: stats["kinds"][k] for k in TYPO_KINDS},
            "typo_positions": {p: stats["positions"][p] for p in TYPO_POSITIONS},
            "abbreviations": group("abbreviation", ABBREVIATIONS), "confusions": group("confusion", CONFUSIONS),
            "contractions": group("contraction", CONTRACTIONS),
            # Share of opportunities the channel can edit: multi-sentence messages, messages ending in an unprotected
            # "." or "!", commas not touching a protected token, and capitalized starts that leave another capital.
            "editable": {f: t[f"editable:{f}", "k"] / t[f"editable:{f}", "n"] if t[f"editable:{f}", "n"] else 0.0
                         for f in EDITABLE}}
    out["misspellings"] = sorted(
        ([wrong, right, len(ids)] for (wrong, right), ids in pairs.items()
         if len(ids) >= TABLE_MIN_CONVERSATIONS and wrong not in exclude and right not in exclude),
        key=lambda row: (-row[2], row[1], row[0]))
    return out


def publication_problems(profile: dict, words: frozenset[str]) -> list[str]:
    """Reasons a profile may not be published; fixed codes and row numbers, never values."""
    problems = []
    if profile.get("kind") != "routewise-noise-profile" or profile.get("version") != VERSION:
        problems.append("not a noise profile of this version")
    allowed = {"kind", "version", "conversations", "thresholds", "roles", "misspellings", "source"}
    problems += [f"unexpected top-level key #{i}" for i, k in enumerate(profile) if k not in allowed]
    for i, row in enumerate(profile.get("misspellings", [])):
        ok = (isinstance(row, list) and len(row) == 3 and all(isinstance(x, str) and re.fullmatch(r"[a-z]{4,}", x)
                                                              for x in row[:2]) and isinstance(row[2], int))
        if not ok or row[2] < TABLE_MIN_CONVERSATIONS or known(row[0], words) or not known(row[1], words) \
                or classify(row[0], row[1]) is None:
            problems.append(f"misspelling row {i} fails the publication rule")

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if not re.fullmatch(r"[a-z0-9_:'+ -]+|Customer|Agent", str(k)):
                    problems.append(f"unexpected key under {path}")
                walk(v, f"{path}/{k}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"unexpected value under {path}")

    walk(profile.get("roles", {}), "roles")
    return problems


# ---------------------------------------------------------------- the channel


def _delta(target: float, base: float) -> float:
    """Rate at which to add a feature so text that already shows it at `base` shows it at `target`."""
    return max(0.0, min(1.0, (target - base) / (1 - base))) if base < 1 else 0.0


def _writer(stats: dict, rng: Random) -> dict[str, float]:
    """One writer's rates, drawn from the measured between-writer spread."""
    out = {}
    for f in FEATURES:
        s = stats["features"][f]
        p, v = s["rate"], s["writer_var"]
        c = p * (1 - p) / v - 1 if v > 0 else 0
        out[f] = rng.betavariate(p * c, (1 - p) * c) if v > 0 and 0 < p < 1 and c > 0 else p
    return out


def _rates(real: dict, base: dict, w: dict[str, float]) -> dict[str, Any]:
    def scaled(feature: str, group: dict, base_group: dict, name: str) -> float:
        pooled = real["features"][feature]["rate"]
        cell, base_cell = group[name], base_group[name]
        target = min(1.0, (cell["k"] / cell["n"] if cell["n"] else 0.0) * (w[feature] / pooled if pooled else 0.0))
        return _delta(target, base_cell["k"] / base_cell["n"] if base_cell["n"] else 0.0)

    rates = {f: _delta(w[f], base["features"][f]["rate"]) for f in FEATURES}
    split = rates["split_message"]
    rates["split_message"] = split / (1 - split) if split < 1 else 1.0  # each split adds a message
    for f, share in base.get("editable", {}).items():
        rates[f] = min(1.0, rates[f] / share) if share else 0.0
    rates["misspelling_by_length"] = {b: scaled("misspelling", real["misspelling_by_length"],
                                                base["misspelling_by_length"], b) for b in LENGTH_BUCKETS}
    rates["abbreviations"] = {a: scaled("abbreviation", real["abbreviations"], base["abbreviations"], a)
                              for a in ABBREVIATIONS}
    return rates


def _protected(text: str) -> list[bool]:
    mask = [False] * len(text)
    for m in _PROTECTED.finditer(text):
        mask[m.start():m.end()] = [True] * (m.end() - m.start())
    return mask


def _label_word(text: str) -> bool:
    return any(t.lower() in LABEL_WORDS for t in _TOKEN.findall(text))


def _like(original: str, new: str) -> str:
    return new[:1].upper() + new[1:] if original[:1].isupper() and new else new


def _typo(word: str, stats: dict, rng: Random, words: frozenset[str]) -> str | None:
    kinds = [stats["typo_kinds"].get(k, 0) + 1 for k in TYPO_KINDS]  # add-one: every kind stays possible
    positions = [stats["typo_positions"].get(p, 0) + 1 for p in TYPO_POSITIONS]
    n = len(word)
    for _ in range(20):
        kind = rng.choices(TYPO_KINDS, kinds)[0]
        place = rng.choices(TYPO_POSITIONS, positions)[0]
        i = 0 if place == "first" else n - 1 if place == "last" else rng.randrange(1, n - 1)
        c = word[i]
        if kind == "deletion":
            new = word[:i] + word[i + 1:]
        elif kind == "insertion_double":
            new = word[:i] + c + word[i:]
        elif kind == "insertion_other":
            new = word[:i] + rng.choice(NEIGHBORS.get(c) or "e") + word[i:]
        elif kind == "substitution_adjacent":
            new = word[:i] + rng.choice(NEIGHBORS.get(c) or "e") + word[i + 1:]
        elif kind == "substitution_other":
            pool = [v for v in _VOWELS if v != c] if c in _VOWELS else \
                [x for x in "bcdfghjklmnpqrstvwxyz" if x != c and x not in NEIGHBORS.get(c, "")]
            new = word[:i] + rng.choice(pool) + word[i + 1:]
        else:
            i = min(i, n - 2)
            new = word[:i] + word[i + 1] + word[i] + word[i + 2:]
        if new != word and not known(new, words):
            return new
    return None


class _Editor:
    """Applies edits to one message outside protected spans and logs them."""

    def __init__(self, rng: Random, log: list[dict], message: int, role: str):
        self.rng, self.log, self.message, self.role = rng, log, message, role

    def apply(self, rx: re.Pattern[str], text: str, rate: float, repl, kind: str, *, touching: bool = False) -> str:
        """`touching` also refuses a match next to a protected span (for edits that remove a space)."""
        if rate <= 0:
            return text
        mask = _protected(text) + [False]
        out, last = [], 0
        for m in rx.finditer(text):
            edge = touching and (mask[m.end()] or (m.start() > 0 and mask[m.start() - 1]))
            if any(mask[m.start():m.end()]) or edge or self.rng.random() >= rate:
                continue
            new = repl(m)
            if new is None or new == m.group():
                continue
            out += [text[last:m.start()], new]
            last = m.end()
            self.note(kind, m.group(), new)
        return "".join(out) + text[last:]

    def note(self, kind: str, before: str, after: str) -> None:
        self.log.append({"message": self.message, "role": self.role, "kind": kind, "before": before, "after": after,
                         "label_word": _label_word(before)})


def _noisy_message(text: str, ed: _Editor, real: dict, rates: dict, table: dict[str, list[tuple[str, int]]],
                   words: frozenset[str]) -> str:
    rng = ed.rng
    for right, wrong in CONFUSIONS.items():
        text = ed.apply(_CORRECT[right], text, rates["confusion"], lambda m, w=wrong: _like(m.group(), w), "confusion")
    for full, forms in ABBREVIATIONS.items():
        text = ed.apply(_FULL[full], text, rates["abbreviations"][full],
                        lambda m, f=forms: _like(m.group(), rng.choice(f)), "abbreviation")
    text = ed.apply(_CONTRACTED, text, rates["apostrophe_drop"], lambda m: re.sub("['’]", "", m.group()),
                    "apostrophe_drop")

    def misspell(m: re.Match[str]) -> str | None:
        word = m.group(1)
        low = word.lower()
        if not known(low, words) or rng.random() >= rates["misspelling_by_length"][_bucket(len(low))]:
            return None
        options = table.get(low)
        new = rng.choices([w for w, _ in options], [n for _, n in options])[0] if options else \
            _typo(low, real, rng, words)
        return _like(word, new) if new else None

    text = ed.apply(_ELIGIBLE, text, 1.0, misspell, "misspelling")
    text = ed.apply(_REPEATABLE, text, rates["repeated_word"],
                    lambda m: None if m.group(1).lower() in NEGATIONS else f"{m.group(1)} {m.group(1)}", "repeated_word")
    if rng.random() < rates["message_all_lowercase"]:
        text = ed.apply(_CAPITALIZED, text, 1.0, lambda m: m.group().lower(), "message_all_lowercase")
    else:  # measured among messages that keep a capital, so an edit that would leave none is undone
        before, logged = text, len(ed.log)
        lower_first = lambda m: (m.group()[:m.start(1) - m.start()] + m.group(1)[0].lower()  # noqa: E731
                                 + m.group(1)[1:])
        text = ed.apply(_MESSAGE_START, text, rates["message_lowercase_start"], lower_first, "message_lowercase_start")
        text = ed.apply(_SENTENCE_START, text, rates["sentence_lowercase_start"], lower_first,
                        "sentence_lowercase_start")
        text = ed.apply(_PRONOUN, text, rates["pronoun_i_lowercase"], lambda m: "i", "pronoun_i_lowercase")
        if _all_lowercase(text) and not _all_lowercase(before):
            text = before
            del ed.log[logged:]
    text = ed.apply(_COMMA, text, rates["comma_no_space"], lambda m: ",", "comma_no_space", touching=True)
    if rng.random() < rates["repeated_punctuation"]:
        mask = _protected(text)
        marks = [i for i, ch in enumerate(text) if ch in "!?" and not mask[i]]
        if marks:
            i = marks[-1]
            text = text[:i + 1] + text[i] + text[i + 1:]
            ed.note("repeated_punctuation", text[i], text[i] * 2)
    if rng.random() < rates["no_terminal_punctuation"]:
        mask = _protected(text)
        if text.endswith((".", "!")) and not mask[-1]:
            stripped = text.rstrip(".!")
            ed.note("no_terminal_punctuation", text[len(stripped):], "")
            text = stripped
    text = ed.apply(_CURLY, text, rates["straight_apostrophe"], lambda m: "'", "straight_apostrophe")
    text = ed.apply(_DASH, text, rates["spaced_hyphen"], lambda m: " - ", "spaced_hyphen")
    if rng.random() < rates["emoticon"]:
        mark = rng.choice(EMOTICONS)
        ed.note("emoticon", "", mark)
        text = f"{text} {mark}"
    return text


def inject(text: str, profile: dict, base: dict, rng: Random, words: frozenset[str]) -> tuple[str, list[dict], list[int]]:
    """A noisy rewrite of one formatted conversation: (text, edit log, indices of split clean messages).

    `profile` is the real workload's measurement and `base` the same measurement of the clean set, so each feature is
    added only as far as the clean text falls short of the real rate. Each role gets one writer per conversation.
    """
    messages = parse_messages(text)
    writers = {r: _writer(profile["roles"][r], rng) for r in ROLES}
    rates = {r: _rates(profile["roles"][r], base["roles"][r], writers[r]) for r in ROLES}
    table: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for wrong, right, n in profile.get("misspellings", []):
        table[right].append((wrong, n))
    lines, log, splits = [], [], []
    for index, m in enumerate(messages):
        stamp = f"{m.when:%Y-%m-%d %H:%M}"
        if m.speaker not in ROLES:
            lines.append(f"[{stamp}] {m.speaker}: {m.text}")
            continue
        ed, r = _Editor(rng, log, index, m.speaker), rates[m.speaker]
        later = m.when + timedelta(minutes=1)
        can_split = later.date() == m.when.date() and (index + 1 == len(messages) or messages[index + 1].when > later)
        sentences = _BOUNDARY.split(m.text)
        parts = [m.text]
        if len(sentences) >= 2 and can_split and rng.random() < r["split_message"]:
            cut = rng.randrange(1, len(sentences))
            parts = [" ".join(sentences[:cut]), " ".join(sentences[cut:])]
            ed.note("split_message", "", "")
            splits.append(index)
        elif len(sentences) >= 2 and len(_TOKEN.findall(m.text)) >= RUN_ON_WORDS and rng.random() < r["run_on"]:
            def join(x: re.Match[str]) -> str:
                word = x.group(1)
                keep = word == "I" or word.startswith(("I'", "I’")) or (len(word) > 1 and word.isupper())
                return " " + (word if keep else word[0].lower() + word[1:])
            parts = [ed.apply(_RUN_ON_STOP, m.text, 1.0, join, "run_on")]
        parts = [_noisy_message(p, ed, profile["roles"][m.speaker], r, table, words).strip() for p in parts]
        lines.append(f"[{stamp}] {m.speaker}: {parts[0]}")
        if len(parts) > 1:
            lines.append(f"[{later:%Y-%m-%d %H:%M}] {m.speaker}: {parts[1]}")
    return "\n".join(lines), log, splits


def invariants(clean: str, noisy: str, splits: Iterable[int]) -> list[str]:
    """Label-preservation problems of a noisy conversation (empty when it kept everything the label depends on)."""
    a, b, splits = parse_messages(clean), parse_messages(noisy), set(splits)
    if len(b) != len(a) + len(splits) or len(noisy.splitlines()) != len(b):
        return ["the message count differs from the clean count plus logged splits"]
    problems, j = [], 0
    for i, m in enumerate(a):
        parts = b[j:j + 1 + (i in splits)]
        j += len(parts)
        if parts[0].speaker != m.speaker or parts[0].when != m.when:
            problems.append(f"message {i + 1}: speaker or time changed")
        if i in splits and (parts[1].speaker != m.speaker or parts[1].when != m.when + timedelta(minutes=1)
                            or parts[1].when.date() != m.when.date()
                            or (i + 1 < len(a) and parts[1].when >= a[i + 1].when)):
            problems.append(f"message {i + 1}: split part out of place")
        body = " ".join(p.text for p in parts)
        if m.speaker not in ROLES and body != m.text:
            problems.append(f"message {i + 1}: a {m.speaker} message changed")
        if _PROTECTED.findall(m.text) != _PROTECTED.findall(body):
            problems.append(f"message {i + 1}: a protected token changed")
        if money_mentions(m.text) != money_mentions(body) or ORDER_LIKE.findall(m.text) != ORDER_LIKE.findall(body):
            problems.append(f"message {i + 1}: an amount or reference changed")
    return problems
