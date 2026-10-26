"""Private validation mode: customer data stays in two directories outside every git repository.

The input directory holds what people supply: each dataset's label files in <input>/<name>/labels/
and adjudication files in <input>/<name>/adjudication/. The output directory holds everything the
validation writes: built labels, traces with model outputs, diagnostics, proposals, exports awaiting
review and logs. Neither may be inside a git work tree, so nothing in them can be committed, deployed
with the dashboard or read as a run artifact. The output directory is readable by its owner only.

The console shows counts, rates, hashes and paths. SafeError marks messages written to that rule;
any other failure may quote a conversation, a label or a model output (a pydantic error quotes its
input, for example), so `session` writes its details to <output>/logs and the console names only its
type. `Needles` holds values taken from the private data; `leak_scan` looks for them in a repository,
and `publish` refuses to write a public file that contains one.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import subprocess
import traceback
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from routewise.config import REPO_ROOT

INPUT_ENV, OUTPUT_ENV = "ROUTEWISE_PRIVATE_INPUT", "ROUTEWISE_PRIVATE_OUTPUT"
MARKER = ".routewise-private"
SYNTHETIC_PREFIX = "SYNTH-"  # drill conversations: never mixed with real ones, never published or sealed
RUN_WORDS = 8  # consecutive words of private text that count as a match
CACHE_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}

_COARSE = re.compile(r"[\w#/.@+-]+")
_SEPARATOR = re.compile(r"([/.#])")
_WORD = re.compile(r"[^\W_]+")
_DIGIT_RUN = re.compile(r"\+?\d[\d ().-]{7,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MESSAGE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] [A-Za-z]+: ", re.M)


class SafeError(ValueError):
    """A failure whose message holds no customer data: counts, paths, hashes and fixed text only."""


@dataclass(frozen=True)
class PrivateDirs:
    input: Path | None
    output: Path


# ---------------------------------------------------------------- directories


def git_work_tree(path: Path) -> Path | None:
    """The git work tree containing `path` or its nearest existing ancestor."""
    probe = path
    while not probe.is_dir():
        probe = probe.parent
    try:
        out = subprocess.run(["git", "-C", str(probe), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    except OSError:
        return None
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None


def _check_location(role: str, path: Path) -> None:
    if path.is_relative_to(REPO_ROOT):
        raise SafeError(f"The {role} directory {path} is inside this repository; use a directory outside it")
    if REPO_ROOT.is_relative_to(path):
        raise SafeError(f"The {role} directory {path} contains this repository; use a separate directory")
    tree = git_work_tree(path)
    if tree:
        raise SafeError(f"The {role} directory {path} is inside the git work tree {tree}; "
                        "use a directory outside every repository")


def _prepare_output(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise SafeError(f"The output path {path} is not a directory")
        if not (path / MARKER).exists() and any(path.iterdir()):
            raise SafeError(f"The output directory {path} is not empty and was not created by RouteWise; "
                            "use a new directory")
        if path.stat().st_mode & 0o077:
            raise SafeError(f"The output directory {path} is accessible to other users; run `chmod 700 '{path}'`")
    else:
        path.mkdir(parents=True)
        path.chmod(0o700)
    marker = path / MARKER
    if not marker.exists():
        marker.write_text("RouteWise private validation output: customer conversations, labels, model outputs and "
                          "traces. Keep this directory outside every git repository and dashboard; publish only "
                          "reviewed exports (routewise validation export).\n")
        marker.chmod(0o600)


def resolve_dirs(input_dir: Path | str | None, output_dir: Path | str | None, *, need_input: bool) -> PrivateDirs:
    given = {"input": input_dir or os.environ.get(INPUT_ENV), "output": output_dir or os.environ.get(OUTPUT_ENV)}
    missing = [role for role in (("input", "output") if need_input else ("output",)) if not given[role]]
    if missing:
        raise SafeError("Private mode needs " + " and ".join(
            f"--{role} (or ${INPUT_ENV if role == 'input' else OUTPUT_ENV})" for role in missing)
            + ": directories outside every git repository")
    paths = {role: Path(value).expanduser().resolve() for role, value in given.items() if value}
    for role, path in paths.items():
        _check_location(role, path)
    if len(paths) == 2:
        a, b = paths["input"], paths["output"]
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise SafeError("The input and output directories must be separate; neither may contain the other")
    if "input" in paths and not paths["input"].is_dir():
        if need_input:
            raise SafeError(f"The input directory {paths['input']} does not exist")
        del paths["input"]
    _prepare_output(paths["output"])
    return PrivateDirs(input=paths.get("input"), output=paths["output"])


def refuse_private_root(root: Path) -> None:
    """Dashboards and run listings never read a private output directory or anything inside one."""
    root = root.expanduser().resolve()
    for path in (root, *root.parents):
        if (path / MARKER).exists():
            raise SafeError(f"{root} is a private validation directory; dashboards read only the repository")


# ---------------------------------------------------------------- console and logs


def private_note(output: Path, kind: str, text: str) -> Path:
    path = output / "logs" / f"{kind}-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path


@contextmanager
def session(output: Path) -> Iterator[None]:
    """Owner-only files, library log records in the private log, and failures reduced to their type."""
    old_umask = os.umask(0o077)
    (output / "logs").mkdir(exist_ok=True)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    handler = logging.FileHandler(output / "logs" / "routewise.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.handlers = [handler]
    root.setLevel(logging.WARNING)
    logging.captureWarnings(True)
    try:
        yield
    except SafeError:
        raise
    except Exception as exc:
        path = private_note(output, "error", "".join(traceback.format_exception(exc)))
        raise SafeError(f"{type(exc).__name__}; its message may quote private data, so it is in {path}") from None
    finally:
        logging.captureWarnings(False)
        root.handlers = saved[0]
        root.setLevel(saved[1])
        handler.close()
        os.umask(old_umask)


# ---------------------------------------------------------------- private values


def _id_candidates(text: str) -> Iterator[str]:
    """Every identifier-like span: tokens, and runs of their '/', '.' or '#'-separated parts."""
    for coarse in _COARSE.findall(text):
        parts = _SEPARATOR.split(coarse)
        segments, separators = parts[0::2], parts[1::2]
        if len(segments) == 1:
            yield coarse
            continue
        if len(segments) > 10:
            yield coarse
            yield from segments
            continue
        for i in range(len(segments)):
            span = segments[i]
            yield span
            for j in range(i + 1, len(segments)):
                span += separators[j - 1] + segments[j]
                yield span


def _digits(text: str) -> Iterator[str]:
    for m in _DIGIT_RUN.finditer(text):
        if not _DATE.search(m.group()):
            digits = re.sub(r"\D", "", m.group())
            if len(digits) >= 10:
                yield digits


def _bodies(text: str) -> list[str]:
    """Message bodies without their timestamps; the whole text when it is not a conversation."""
    parts = _MESSAGE.split(text)
    return [p for p in parts if p.strip()] if len(parts) > 1 else [text]


@dataclass
class Needles:
    """Values from private files, each mapped to a private description of where it came from.

    Text is matched as runs of RUN_WORDS consecutive words, stored by hash to keep memory small.
    """

    ids: dict[str, str] = field(default_factory=dict)
    digits: dict[str, str] = field(default_factory=dict)
    runs: dict[int, str] = field(default_factory=dict)
    firsts: set[str] = field(default_factory=set)
    unreadable: int = 0
    _owner: dict[int, str] = field(default_factory=dict, repr=False)
    _shared: set[int] = field(default_factory=set, repr=False)

    def add_id(self, value: object, where: str) -> None:
        v = str(value or "").strip().casefold().lstrip("#")
        if v.isdigit() and len(v) >= 9:
            self.digits.setdefault(v, where)
        elif len(v) >= 5 and ("@" in v or (any(c.isdigit() for c in v) and any(c.isalpha() for c in v))):
            self.ids.setdefault(v, where)

    def add_text(self, value: object, where: str, owner: str) -> None:
        for body in _bodies(str(value or "")):
            for email in _EMAIL.findall(body):
                self.add_id(email, f"{where} (email)")
            for digits in _digits(body):
                self.digits.setdefault(digits, f"{where} (phone or card number)")
                if len(digits) == 11 and digits.startswith("1"):
                    self.digits.setdefault(digits[1:], f"{where} (phone number)")
            words = _WORD.findall(body.casefold())
            for i in range(len(words) - RUN_WORDS + 1):
                key = hash(tuple(words[i:i + RUN_WORDS]))
                self.runs.setdefault(key, where)
                self.firsts.add(words[i])
                if self._owner.setdefault(key, owner) != owner:
                    self._shared.add(key)

    def drop_boilerplate(self) -> None:
        """Wording shared by several private conversations is a template, not a fingerprint of one."""
        for key in self._shared:
            self.runs.pop(key, None)
        self._owner.clear()
        self._shared.clear()

    def __len__(self) -> int:
        return len(self.ids) + len(self.digits) + len(self.runs)

    def find(self, text: str) -> list[tuple[str, str]]:
        """(category, private description) for every private value in `text`."""
        hits: dict[object, tuple[str, str]] = {}
        folded = text.casefold()
        if self.ids:
            for token in _id_candidates(folded):
                if token in self.ids:
                    hits.setdefault(token, ("identifier or email", self.ids[token]))
        if self.digits:
            for digits in _digits(text):
                if digits in self.digits:
                    hits.setdefault(digits, ("phone or card number", self.digits[digits]))
        if self.runs:
            words = _WORD.findall(folded)
            for i in range(len(words) - RUN_WORDS + 1):
                if words[i] in self.firsts:
                    key = hash(tuple(words[i:i + RUN_WORDS]))
                    if key in self.runs:
                        hits.setdefault(key, ("conversation or label text", self.runs[key]))
        return list(hits.values())


_ID_COLUMNS = ("conversation_id", "id", "order_id")
_REFERENCE_COLUMNS = ("ref_label_a", "ref_label_b", "ref_differs")  # copies of label rows, read from the input


def _rows(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(io.StringIO(text, newline="")))
    rows = []
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            rows += [row] if isinstance(row, dict) else []
    return rows


def _add_row(needles: Needles, row: dict[str, object], source: str) -> None:
    cid = str(row.get("conversation_id") or row.get("id") or "").strip()
    where = f"{source} ({cid or 'no id'})"
    for column in _ID_COLUMNS:
        needles.add_id(row.get(column), f"{where}: {column}")
    expected = row.get("expected")
    if isinstance(expected, dict):
        needles.add_id(expected.get("order_id"), f"{where}: order_id")
    for column, value in row.items():
        if column in ("expected", *_ID_COLUMNS, *_REFERENCE_COLUMNS):
            continue
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, str) and len(v) > 4:
                needles.add_text(v, f"{where}: {column}", owner=cid or source)


def private_files(dirs: PrivateDirs) -> list[Path]:
    """Every file that holds supplied or built labels: inputs, built datasets, queues and exclusions."""
    files = sorted((dirs.output / "data").glob("*/examples.jsonl")) + sorted((dirs.output / "data").glob("*/*.csv"))
    files += sorted((dirs.output / "data").glob("*/excluded.json"))
    if dirs.input:
        files += sorted(p for p in dirs.input.glob("*/*/*") if p.suffix.lower() in (".csv", ".jsonl"))
    return files


def needles_from(dirs: PrivateDirs) -> Needles:
    needles = Needles()
    for path in private_files(dirs):
        try:
            rows = json.loads(path.read_text()) if path.suffix == ".json" else _rows(path)
        except (OSError, ValueError):
            needles.unreadable += 1
            continue
        for row in rows:
            if isinstance(row, dict):
                _add_row(needles, row, path.name)
    needles.drop_boilerplate()
    return needles


def publish(files: dict[Path, str], dirs: PrivateDirs) -> None:
    """Write files meant to be public, after checking that none holds a value from the private data."""
    needles = needles_from(dirs)
    hits = [(path, category, where) for path, text in files.items() for category, where in needles.find(text)]
    if hits:
        note = private_note(dirs.output, "blocked-publication",
                            "".join(f"{path}: {category}: {where}\n" for path, category, where in hits))
        raise SafeError(f"Refused to write {', '.join(sorted({str(p) for p, _, _ in hits}))}: "
                        f"{len(hits)} value(s) found in the private data "
                        f"({', '.join(sorted({c for _, c, _ in hits}))}); nothing was written. Details: {note}")
    for path, text in files.items():
        path.write_text(text)
        path.chmod(0o644)  # a public file, written under the session's owner-only umask


# ---------------------------------------------------------------- leak scan


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def _walk(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
    elif path.is_dir() and path.name not in CACHE_DIRS and not path.name.endswith(".egg-info"):
        for child in sorted(path.iterdir()):
            yield from _walk(child)


def repository_files(repo: Path) -> Iterator[Path]:
    """Tracked, untracked and ignored files, except git internals, virtual environments and caches."""
    seen: set[Path] = set()
    listings = [_git(repo, "ls-files", "-z"), _git(repo, "ls-files", "-z", "--others", "--exclude-standard"),
                _git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory")]
    for entry in (e for listing in listings for e in listing.split("\0") if e):
        path = repo / entry
        if any(part in CACHE_DIRS for part in Path(entry).parts):
            continue
        for f in _walk(path):
            if f not in seen:
                seen.add(f)
                yield f


def first_seen(dirs: PrivateDirs) -> datetime | None:
    """When the private data first reached either directory (the earliest file modification time)."""
    times = [p.stat().st_mtime for p in private_files(dirs)]
    return datetime.fromtimestamp(min(times), UTC) if times else None


@dataclass
class Finding:
    location: str
    location_is_private: bool  # the path itself holds a private value, so the console must not show it
    categories: Counter
    details: list[str]


def leak_scan(dirs: PrivateDirs, repo: Path = REPO_ROOT) -> tuple[list[Finding], dict[str, int]]:
    """Private values in the repository's files, and in commits made since the private data appeared."""
    needles = needles_from(dirs)
    findings: list[Finding] = []
    stats = Counter(values=len(needles), unreadable_private_files=needles.unreadable)
    if not needles:
        return findings, dict(stats)

    def check(location: str, text: str) -> None:
        named = needles.find(location)
        hits = needles.find(text) + [(f"{c} in the file name", w) for c, w in named]
        if hits:
            findings.append(Finding(location, bool(named), Counter(c for c, _ in hits),
                                    [f"{c}: {w}" for c, w in hits]))

    for path in repository_files(repo):
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            stats["binary_files_skipped"] += 1
            continue
        stats["files"] += 1
        check(str(path.relative_to(repo)), data.decode("utf-8", "replace"))
    since = first_seen(dirs)
    if since:
        log = _git(repo, "log", "--all", "-p", "--no-color", f"--since={since.isoformat()}", "--format=%x00%H")
        for chunk in (c for c in log.split("\0") if c.strip()):
            sha, _, diff = chunk.partition("\n")
            stats["commits"] += 1
            check(f"commit {sha[:12]}", "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+")))
    return findings, dict(stats)

