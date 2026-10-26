"""Rehearse private validation mode on synthetic, sensitive-looking conversations before any real data.

Every value that looks personal is generated at random for each drill, so none of it can already be in
the repository: names, email addresses, phone numbers, Luhn-valid card numbers, street addresses,
order and conversation IDs, labeler notes and adjudication reasons. The drill sends these synthetic
conversations, and nothing else, to Nebius Token Factory. It drives the real command line through
import (with label errors, then disagreements, then adjudication), a smoke run, report, an unexpected
failure, export staging and approval, and a leak scan. It then checks that none of the generated values
reached the console, the repository, the dashboard or the export, and that every file in the private
output directory is readable by its owner only. Synthetic datasets can never be proposed from, sealed
or published to the repository.
"""

from __future__ import annotations

import csv
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from routewise import labels as label_io
from routewise.config import REPO_ROOT, load_dotenv
from routewise.private_mode import (CACHE_DIRS, OUTPUT_ENV, SYNTHETIC_PREFIX, PrivateDirs, SafeError, _check_location,
                                    needles_from)
from routewise.runlog import sha256_file

CONVERSATIONS = 8
BUDGET_USD = 0.05
# (store wording, issue date as days after the message or None, review reason, primary tag); each used twice
PROMISES = (("in three days", 3, None, "relative_issue_date"),
            ("today", 0, None, "relative_issue_date"),
            ("within 3-5 business days", None, "issue_date_ambiguous: business-day range", "business_days"),
            ("soon", None, "issue_date_ambiguous: vague timing", "vague_timing"))
_SYLLABLES = ("ka", "lo", "ve", "ri", "sa", "mon", "tel", "dra", "qui", "zen", "fa", "bor", "ny", "ush", "pel", "tor")


@dataclass
class Workload:
    """Two labelers' rows, the adjudicator's rows, rows with label errors, and every generated private value."""

    first: list[dict[str, str]] = field(default_factory=list)
    second: list[dict[str, str]] = field(default_factory=list)
    adjudication: list[dict[str, str]] = field(default_factory=list)
    broken: list[dict[str, str]] = field(default_factory=list)
    canaries: list[str] = field(default_factory=list)


def _name(rng: random.Random) -> str:
    return "".join(rng.choice(_SYLLABLES) for _ in range(3)).capitalize()


def _card(rng: random.Random) -> str:
    """A 16-digit number that passes the Luhn check, as a real card number would."""
    payload = [4] + [rng.randrange(10) for _ in range(14)]
    total = 0
    for i, d in enumerate(reversed(payload)):  # the check digit goes on the right, so even positions double
        total += (d * 2 - 9 if d > 4 else d * 2) if i % 2 == 0 else d
    digits = "".join(map(str, payload + [(10 - total % 10) % 10]))
    return " ".join(digits[i:i + 4] for i in range(0, 16, 4))


def synthetic_workload(rng: random.Random) -> Workload:
    w = Workload()
    for i in range(CONVERSATIONS):
        first, last = _name(rng), _name(rng)
        name, email = f"{first} {last}", f"{first}.{last}.{rng.randrange(100, 999)}@example.com".lower()
        phone, card = f"+1 {rng.randrange(201, 989)} 555 {rng.randrange(1000, 9999)}", _card(rng)
        address, order = f"{rng.randrange(10, 9999)} {_name(rng)} Street", f"RW{rng.randrange(10**6, 10**7)}K"
        cid, amount = f"{SYNTHETIC_PREFIX}{rng.randrange(16**8):08x}", f"{rng.randrange(2000, 48000) / 100:.2f}"
        promise, offset, reason, tag = PROMISES[i // 2]
        day = date(2026, 3, 2) + timedelta(days=i)
        text = (f"[{day} 09:1{i}] Customer: Hi, this is {name} ({email}, {phone}). Please refund {amount} USD for "
                f"order {order}; my card {card} was charged twice. I live at {address}.\n"
                f"[{day} 10:2{i}] Agent: Thanks {first}. Order {order} is confirmed. Your refund of {amount} USD "
                f"will be issued {promise}.")
        label = {"conversation_id": cid, "split": "development", "text": text, "refund_requested": "true",
                 "refund_amount": amount, "currency": "USD", "order_id": order, "contact_date": day.isoformat(),
                 "refund_date": (day + timedelta(days=offset)).isoformat() if offset is not None else "",
                 "review_required": str(reason is not None).lower(), "review_reason": reason or "",
                 "evidence": f"Please refund {amount} USD for order {order} | will be issued {promise}",
                 "scenario_tags": tag + (f";vip-{last.lower()}" if i == 0 else ""),  # a tag outside the guide's list
                 "labeler_id": "L1", "notes": f"{name} called twice about card {card}"}
        second = {**label, "labeler_id": "L2", "notes": f"{first} sounded upset; call back on {phone}"}
        if i == 1:  # disagrees on the issue date
            second["refund_date"] = (day + timedelta(days=4)).isoformat()
        if i == 4:  # disagrees on whether review is required
            second.update(review_required="false", review_reason="", refund_date=(day + timedelta(days=5)).isoformat())
        w.first.append(label)
        w.second.append(second)
        if i == 1:
            w.adjudication.append({**{k: label[k] for k in label_io.ADJUDICATION_COLUMNS if k in label},
                                   "decision": "label", "adjudicator_id": "A1",
                                   "reason": f"Agent wrote {promise}; checked with {name}"})
        if i == 4:
            w.adjudication.append({"conversation_id": cid, "decision": "exclude", "adjudicator_id": "A1",
                                   "reason": f"Cannot de-identify {address}"})
        if i == 0:
            w.broken += [{**label, "refund_amount": f"{amount} {last}"},
                         {**second, "evidence": f"{email} asked twice"}]
        if i == 2:
            w.broken.append(label)  # one labeler only
        w.canaries += [name, email, phone, card, address, order, cid, label["notes"], second["notes"]]
    w.canaries += [f"vip-{w.first[0]['scenario_tags'].split('-', 1)[1]}"] + [a["reason"] for a in w.adjudication]
    return w


def _write(path: Path, rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------- running the real command line


def capture(argv: list[str], path: Path) -> tuple[int, str]:
    """Run the command line in-process with stdout and stderr (Python and file descriptors) sent to `path`."""
    from routewise import cli
    sys.stdout.flush()
    sys.stderr.flush()
    saved_fds, saved_streams = (os.dup(1), os.dup(2)), (sys.stdout, sys.stderr)
    with path.open("w+", encoding="utf-8") as fh:
        os.dup2(fh.fileno(), 1)
        os.dup2(fh.fileno(), 2)
        sys.stdout = sys.stderr = fh
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:  # never print its message: it is what the drill is testing for
            fh.write(f"uncaught {type(exc).__name__}\n")
            code = 1
        finally:
            fh.flush()
            sys.stdout, sys.stderr = saved_streams
            for fd, saved in zip((1, 2), saved_fds):
                os.dup2(saved, fd)
                os.close(saved)
        fh.seek(0)
        return code, fh.read()


def repository_state(repo: Path = REPO_ROOT) -> str:
    out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all",
                          "--ignored=matching"], capture_output=True, text=True, check=True).stdout
    return "\n".join(line for line in sorted(out.splitlines())
                     if not CACHE_DIRS.intersection(Path(line[3:].strip('"')).parts))


def _private_modes(output: Path) -> list[Path]:
    return [p for p in [output, *output.rglob("*")] if p.stat().st_mode & 0o077]


@dataclass
class Drill:
    root: Path
    workload: Workload
    console: list[str] = field(default_factory=list)
    results: list[tuple[str, bool]] = field(default_factory=list)

    @property
    def input(self) -> Path:
        return self.root / "input"

    @property
    def output(self) -> Path:
        return self.root / "output"

    def cli(self, *argv: str, dirs: bool = True) -> tuple[int, str]:
        step = len(self.console) + 1
        args = ["validation", *argv] + (["--input", str(self.input), "--output", str(self.output)] if dirs else [])
        code, text = capture(args, self.root / "console" / f"{step:02d}-{argv[0]}.txt")
        self.console.append(text)
        return code, text

    def leaks(self, text: str) -> bool:
        folded = text.casefold()
        dirs = PrivateDirs(self.input, self.output) if self.output.exists() else None
        return any(c.casefold() in folded for c in self.workload.canaries) or bool(
            dirs and needles_from(dirs).find(text))

    def check(self, description: str, test: Callable[[], bool]) -> bool:
        try:
            passed = bool(test())
        except Exception as exc:  # a failed check reports its type only
            passed = False
            description += f" ({type(exc).__name__})"
        self.results.append((description, passed))
        print(f"  {'PASS' if passed else 'FAIL'}  {description}", flush=True)
        return passed


def run_drill(root: Path) -> Drill:
    root = root.expanduser().resolve()
    _check_location("drill", root)
    if root.exists() and any(root.iterdir()):
        raise SafeError(f"The drill directory {root} is not empty; use a new directory")
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    (root / "console").mkdir(mode=0o700)
    workload = synthetic_workload(random.Random(secrets.randbits(64)))
    d = Drill(root, workload)
    _write(d.input / "drill" / "labels" / "L1.csv", workload.first, label_io.LABEL_COLUMNS)
    _write(d.input / "drill" / "labels" / "L2.csv", workload.second, label_io.LABEL_COLUMNS)
    _write(d.input / "drill-errors" / "labels" / "L1.csv", workload.broken, label_io.LABEL_COLUMNS)
    before = repository_state()
    data, public = d.output / "data", root / "public-stand-in"
    print(f"Privacy drill in {root} ({CONVERSATIONS} synthetic conversations; nothing real is used)", flush=True)

    inside = REPO_ROOT / "private" / f"drill-{secrets.token_hex(3)}"
    d.check("refuses input and output directories inside the repository", lambda: d.cli(
        "import", "--name", "drill", "--input", str(inside / "in"), "--output", str(inside / "out"), dirs=False)[0] == 1
        and not inside.exists())
    d.check("label errors: console shows a count and a private path; details stay private", lambda: d.cli(
        "import", "--name", "drill-errors")[0] == 1 and d.leaks((data / "drill-errors" / "label-problems.txt").read_text()))
    d.check("disagreements are queued for a third person", lambda: d.cli("import", "--name", "drill")[0] == 0
            and len(label_io.read_rows(data / "drill" / "adjudication_queue.csv")) == 2)
    _write(d.input / "drill" / "adjudication" / "A1.csv", workload.adjudication, label_io.ADJUDICATION_COLUMNS)
    d.check("adjudicated labels build a synthetic dataset", lambda: d.cli("import", "--name", "drill")[0] == 0
            and json.loads((data / "drill" / "MANIFEST.json").read_text())["synthetic"])
    n = CONVERSATIONS - 1
    d.check(f"smoke run of {n} synthetic conversations x 3 arms keeps traces private", lambda: d.cli(
        "run", "--name", "drill", "--smoke", str(n), "--budget", str(BUDGET_USD))[0] == 0
        and len(_traces(d)) == 3 * n)
    run = _run(d)

    def unexpected_failure() -> bool:
        broken = d.output / "runs" / f"{run.name}-broken"
        shutil.copytree(run, broken)
        with (broken / "traces.jsonl").open("a") as fh:  # pydantic quotes the bad value when it rejects this row
            fh.write(json.dumps({"example_id": workload.first[0]["conversation_id"], "strategy": "adopted",
                                 "latency_ms": workload.canaries[0]}) + "\n")
        code, _ = d.cli("report", "--run", broken.name)
        shutil.rmtree(broken)
        return code == 1 and any(d.leaks(p.read_text()) for p in (d.output / "logs").glob("error-*.txt"))
    d.check("an unexpected failure shows its type only; its message goes to a private log", unexpected_failure)

    staged = d.output / "exports" / run.name

    def stage() -> bool:
        from routewise import validation_export
        code, _ = d.cli("export", "--run", run.name)
        aggregate = json.loads((staged / "aggregate.json").read_text())
        return (code == 0 and not validation_export.problems(aggregate) and aggregate["synthetic"]
                and aggregate["withheld"]["tags_outside_vocabulary"] >= 1
                and not d.leaks((staged / "aggregate.json").read_text() + (staged / "REVIEW.md").read_text()))
    d.check("staged export passes the allowlist and holds no generated value", stage)
    digest = sha256_file(staged / "aggregate.json") if (staged / "aggregate.json").exists() else "0" * 64
    d.check("approval refuses a hash other than the reviewed one", lambda: d.cli(
        "export", "--run", run.name, "--approve", "f" * 64, "--reviewer", "drill")[0] == 1)
    d.check("synthetic results are never published to the repository", lambda: d.cli(
        "export", "--run", run.name, "--approve", digest, "--reviewer", "drill")[0] == 1
        and not (REPO_ROOT / "results/M8-validation" / f"{run.name}.json").exists())

    def published() -> bool:
        code, _ = d.cli("export", "--run", run.name, "--approve", digest, "--reviewer", "drill",
                        "--export-dir", str(public))
        text = (public / f"{run.name}.json").read_text()
        return code == 0 and json.loads(text)["review"]["reviewed_sha256"] == digest and not d.leaks(text)
    d.check("an approved export is written outside the private directory, values unchanged", published)
    d.check("leak scan finds no generated value in the repository", lambda: d.cli("leak-scan")[0] == 0)
    d.check("leak scan does find generated values planted in a scratch repository", lambda: _scan_control(d))
    d.check("the dashboard refuses to read the private output directory", lambda: _dashboard_refuses(d.output))
    d.check("every file in the private output directory is readable by its owner only",
            lambda: not _private_modes(d.output))
    d.check("the repository is unchanged", lambda: repository_state() == before)
    d.check("no generated value appeared on the console", lambda: not d.leaks("\n".join(d.console)))
    return d


def _traces(d: Drill) -> list[str]:
    runs = sorted((d.output / "runs").glob("valsmoke-*"))
    return (runs[-1] / "traces.jsonl").read_text().splitlines() if runs else []


def _run(d: Drill) -> Path:
    runs = sorted((d.output / "runs").glob("valsmoke-*"))
    return runs[-1] if runs else d.output / "runs" / "missing"


def _scan_control(d: Drill) -> bool:
    from routewise.private_mode import leak_scan
    repo = d.root / "scan-control"
    repo.mkdir(mode=0o700)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    first = d.workload.first[0]
    (repo / "committed.txt").write_text(f"order {first['order_id']}\n")
    subprocess.run(["git", "-C", str(repo), "add", "committed.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=drill", "-c", "user.email=drill@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "planted"], check=True)
    (repo / "untracked.md").write_text(first["text"].splitlines()[1] + "\n")
    findings, _ = leak_scan(PrivateDirs(d.input, d.output), repo)
    return {f.location for f in findings} >= {"committed.txt", "untracked.md"}


def _dashboard_refuses(output: Path) -> bool:
    from routewise import artifacts
    saved = os.environ.get("ROUTEWISE_ARTIFACTS")
    try:
        refused = 0
        for root in (output, output / "runs"):
            os.environ["ROUTEWISE_ARTIFACTS"] = str(root)
            try:
                artifacts.artifacts_root()
            except SafeError:
                refused += 1
        return refused == 2
    finally:
        if saved is None:
            os.environ.pop("ROUTEWISE_ARTIFACTS", None)
        else:
            os.environ["ROUTEWISE_ARTIFACTS"] = saved


def main(root: Path | None) -> None:
    if not root:
        raise SafeError("drill requires --output: a new directory outside every repository (not "
                        f"${OUTPUT_ENV}); it will hold synthetic data only")
    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("NEBIUS_API_KEY"):
        raise SafeError("The drill's smoke run sends its synthetic conversations to Nebius Token Factory; "
                        "set NEBIUS_API_KEY in .env")
    d = run_drill(Path(root))
    failed = [name for name, ok in d.results if not ok]
    (d.root / "drill-report.json").write_text(json.dumps(
        {"checks": [{"check": name, "passed": ok} for name, ok in d.results], "passed": not failed}, indent=2) + "\n")
    if failed:
        raise SafeError(f"{len(failed)} of {len(d.results)} drill checks failed; each step's console output is in "
                        f"{d.root / 'console'}. Do not load company data until they pass.")
    print(f"All {len(d.results)} checks passed. {d.root} holds synthetic data only; delete it when you are done.",
          flush=True)

