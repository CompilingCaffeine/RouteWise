"""Synthetic noise component of the workload validation (study m8, displayed as M7). Run `routewise noise <stage>`.

Protocol: the 2026-10-26 amendment to experiments/M8-workload-validation.md. The workload's own conversations never
leave the private root: only an aggregate profile does, after a person approves it by its SHA-256.

profile   measure writing noise in every eligible, locally de-identified conversation of the private mailbox;
          writes the profile to <private-root>/results/, unapproved, and prints aggregates only (offline)
publish   copy a profile a person reviewed and approved by its SHA-256 into data/v6/ (offline)
build     inject the committed profile into the M6 confirmation conversations with a seed taken from its hash,
          check every label invariant, and seal data, code, prompts, profiles and prices
run       the three M7 arms on every clean and noisy conversation, in one randomized run
report    the preregistered analysis (no API calls)
"""

from __future__ import annotations

import json
import re
import secrets
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from typing import Any

from routewise import mailbox_prep, noise
from routewise.benchmark import Pacer
from routewise.confirmation import StudyClient
from routewise.config import REPO_ROOT, load_config, load_dotenv
from routewise.date_challenge import requires_review
from routewise.evaluate import score
from routewise.evaluation import profile_routers, run_evaluation
from routewise.issue_dates import analyze_issue_date
from routewise.private_mode import SafeError, session
from routewise.prices import load_snapshot, price_for
from routewise.prompts import load_prompt
from routewise.recovery_metrics import RequestOutcome, paired, summarize
from routewise.router import Trace
from routewise.runlog import RunDir, read_jsonl, sha256_file
from routewise.schema import Example
from routewise.workload_validation import (ARMS, CONFIG, LOAD, PROFILES, PROTOCOL, _rel, _require_committed, dump,
                                           fetch_prices, frozen_sources)

STUDY, DISPLAY, COMPONENT = "m8", "M7", "noise"
DATA = REPO_ROOT / "data/v6"
PROFILE = DATA / "noise-profile.json"
BASE = REPO_ROOT / "data/v5/confirm"
DICTIONARY = Path("/usr/share/dict/words")
SPLITS = ("representative", "challenge")
CONDITIONS = ("clean", "noisy")
NOISY = "~noisy"  # id suffix of a noisy conversation; the clean one keeps its v5 id
GATES = {"wrong_release_limit": 0.02, "accuracy": 0.95, "field_accuracy": 0.95, "deadline_miss_limit": 0.05,
         "cac_margin": 0.02, "wrong_release_margin": 0.01, "challenge_wrong_release_margin": 0.02}


# ---------------------------------------------------------------- profile (private, offline)


def profile(mbox: Path | str, private_root: Path | str) -> Path:
    dirs = mailbox_prep.private_layout(private_root)
    mbox = Path(mbox).expanduser().resolve()
    if not mbox.is_file() or mbox.is_symlink() or not mbox.is_relative_to(dirs["raw"]):
        raise SafeError(f"The archive must be a regular file inside {dirs['raw']}")
    out = dirs["results"] / f"noiseprofile-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    out.mkdir(mode=0o700)
    with session(out):
        archive = mailbox_prep.load_archive(mbox)
        zone = mailbox_prep.detect_render_zone(archive)
        config, _ = mailbox_prep.load_config(dirs["mappings"] / mailbox_prep.CONFIG_NAME,
                                             mailbox_prep.propose_config(archive, zone))
        config["render_timezone"] = config.get("render_timezone") or zone["zone"]
        if not config["render_timezone"] or not config.get("store_timezone", {}).get("value"):
            raise SafeError("Set render_timezone and store_timezone.value in the private config first")
        _, records, _ = mailbox_prep.eligible(archive, config, secrets.randbits(32))
        # Every word of every value the redactor replaced (names, emails, addresses, schools) and every listed
        # proper name: neither side of a published misspelling pair may be one of them. Held in memory only.
        exclude = {w.lower() for r in records.values() for values in r["redactor"].originals().values()
                   for v in values for w in re.findall(r"[A-Za-z]+", v)}
        exclude |= {w.lower() for w in mailbox_prep._wordlist(str(DICTIONARY.parent / "propernames"))}
        result = noise.measure((r["input"] for r in records.values()), mailbox_prep.dictionary(), frozenset(exclude))
        result["source"] = {
            "frame": "every eligible conversation of the private Intercom mailbox, de-identified locally with the "
                     "rules of `routewise mailbox prepare`",
            "author_roles_confirmed": all(a.get("confirmed") for a in config.get("authors", {}).values()),
            "store_timezone_confirmed": bool(config["store_timezone"].get("confirmed")),
            "dictionary_sha256": sha256_file(DICTIONARY)}
        path = mailbox_prep._json(out / "noise-profile.json", result)
        problems = noise.publication_problems(result, mailbox_prep.dictionary())
        mailbox_prep._json(out / "PROFILE-STATUS.json", {
            "profile": str(path), "sha256": sha256_file(path), "approved": False, "approved_by": None,
            "publication_check": problems or "passed",
            "required_before_publication": [
                "read every misspelling pair and confirm none is a name, product, code or other identifying value",
                "confirm every other entry is a count or rate",
                "approve publication with `routewise noise publish --profile <path> --approve <sha256> --reviewer <name>`"]})
    _print_profile(result)
    print(f"Private profile: {path}\nSHA-256: {sha256_file(path)}\nThe profile is UNAPPROVED. Review its "
          "`misspellings` table locally before publishing.", flush=True)
    return path


def _print_profile(p: dict) -> None:
    """Aggregates only: counts and rates, never a token."""
    print(f"Profiled {p['conversations']} conversations; {len(p['misspellings'])} misspelling pairs pass the "
          "publication thresholds.", flush=True)
    for role, s in p["roles"].items():
        rates = {f: f"{v['rate']:.2%} (writer sd {v['writer_var'] ** 0.5:.2%})" for f, v in s["features"].items()}
        print(json.dumps({"role": role, "messages": s["messages"], "words": s["words"],
                          "words_per_message": s["words_per_message"], "rates": rates,
                          "typo_kinds": s["typo_kinds"]}, indent=1), flush=True)


def publish(path: Path | str, approve: str, reviewer: str) -> Path:
    path = Path(path).expanduser().resolve()
    if path.name != "noise-profile.json" or not path.parent.name.startswith("noiseprofile-"):
        raise SafeError("Pass the noise-profile.json of a `routewise noise profile` run")
    if sha256_file(path) != approve:
        raise SafeError("The SHA-256 does not match the profile; review the current file and approve its hash")
    if not reviewer.strip():
        raise SafeError("Name the person who reviewed the profile with --reviewer")
    if PROFILE.exists():
        raise SafeError(f"{_rel(PROFILE)} is already published; a new profile needs a new dataset version")
    value = json.loads(path.read_text())
    if problems := noise.publication_problems(value, mailbox_prep.dictionary()):
        raise SafeError(f"The profile fails {len(problems)} publication check(s): {problems[:5]}")
    value["publication"] = {"private_sha256": approve, "reviewer": reviewer.strip(),
                            "approved_at": datetime.now(UTC).isoformat()}
    DATA.mkdir(exist_ok=True)
    PROFILE.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
    status = path.parent / "PROFILE-STATUS.json"
    mailbox_prep._json(status, {**json.loads(status.read_text()), "approved": True, "approved_by": reviewer.strip(),
                                "published_to": _rel(PROFILE)})
    print(f"Published the reviewed profile to {_rel(PROFILE)} (SHA-256 {sha256_file(PROFILE)}). Commit it with the "
          "protocol before `routewise noise build`.", flush=True)
    return PROFILE


# ---------------------------------------------------------------- build and seal


def base_examples() -> list[Example]:
    seal = json.loads((BASE / "seal.json").read_text())
    if seal["data_sha256"] != sha256_file(BASE / "examples.jsonl"):
        raise SafeError("The M6 confirmation data changed after its seal")
    return [Example(**r) for r in read_jsonl(BASE / "examples.jsonl")]


def seed() -> int:
    return int(sha256_file(PROFILE)[:8], 16)


def build() -> Path:
    if (DATA / "seal.json").exists() or (DATA / "examples.jsonl").exists():
        raise SafeError(f"{_rel(DATA)} is built; it never changes")
    _require_committed([PROTOCOL, PROFILE])
    words = mailbox_prep.dictionary()
    real = json.loads(PROFILE.read_text())
    base = base_examples()
    clean = noise.measure([e.input for e in base], words)
    noisy, log = [], []
    for e in base:
        text, edits, splits = noise.inject(e.input, real, clean, Random(f"{seed()}:{e.id}"), words)
        if problems := noise.invariants(e.input, text, splits):
            raise ValueError(f"{e.id}: {problems}")
        tags = [*e.tags, "condition:noisy", f"base:{e.id}"] + (
            ["noise:label_word"] if any(x["label_word"] for x in edits) else [])
        noisy.append(Example(id=e.id + NOISY, task=e.task, difficulty=e.difficulty, tags=tags, input=text,
                             expected=e.expected))
        log += [{"id": e.id, **x} for x in edits]
    DATA.mkdir(exist_ok=True)
    (DATA / "examples.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in noisy))
    (DATA / "edits.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in log))
    dump(DATA / "base-profile.json", clean)
    achieved = noise.measure([e.input for e in noisy], words)
    calibration = {role: {f: {"workload": real["roles"][role]["features"][f]["rate"],
                              "clean": clean["roles"][role]["features"][f]["rate"],
                              "noisy": achieved["roles"][role]["features"][f]["rate"]} for f in noise.FEATURES}
                   for role in noise.ROLES}
    splits = json.loads((BASE / "splits.json").read_text())
    dump(DATA / "MANIFEST.json", {
        "study": STUDY, "component": COMPONENT, "display_milestone": DISPLAY, "seed": seed(),
        "n_conversations": len(noisy), "by_split": {k: len(v) for k, v in splits.items()},
        "with_label_word_edit": sum("noise:label_word" in e.tags for e in noisy),
        "split_messages": sum(x["kind"] == "split_message" for x in log),
        "edits_by_kind": dict(Counter(x["kind"] for x in log).most_common()),
        "edits_by_role": dict(Counter(x["role"] for x in log)),
        "calibration": calibration,
        "dataset_sha256": sha256_file(DATA / "examples.jsonl"),
        "source": "M6 confirmation conversations (synthetic, model-written, fact-sheet labels) rewritten by a "
                  "seeded noise channel calibrated to the aggregate profile of the user's de-identified mailbox"})
    config = load_config(CONFIG)
    load_dotenv(REPO_ROOT / ".env")
    fetch_prices(config, DATA / "prices.json")
    dump(DATA / "seal.json", {
        "study": STUDY, "component": COMPONENT, "created_before_noisy_outcomes": True, "arms": list(ARMS),
        "sources": frozen_sources(), "protocol_sha256": sha256_file(PROTOCOL), "profile_sha256": sha256_file(PROFILE),
        "base_sha256": sha256_file(BASE / "examples.jsonl"), "base_split_sha256": sha256_file(BASE / "splits.json"),
        "data_sha256": sha256_file(DATA / "examples.jsonl"), "edits_sha256": sha256_file(DATA / "edits.jsonl"),
        "dictionary_sha256": sha256_file(DICTIONARY), "price_sha256": sha256_file(DATA / "prices.json"),
        "seed": seed(), "gates": GATES, "load": LOAD, "deadline_ms": 4000})
    print(json.dumps({"built": len(noisy), "with_label_word_edit": sum("noise:label_word" in e.tags for e in noisy),
                      "edits_by_kind": dict(Counter(x["kind"] for x in log).most_common())}, indent=1), flush=True)
    print(f"Sealed {_rel(DATA / 'seal.json')}. Commit data/v6 before `routewise noise run`.", flush=True)
    return DATA


def check_seal() -> dict:
    if not (DATA / "seal.json").exists():
        raise SafeError("Run `routewise noise build` first")
    _require_committed([PROTOCOL, PROFILE, DATA / "seal.json", DATA / "examples.jsonl", DATA / "prices.json"])
    value = json.loads((DATA / "seal.json").read_text())
    current = frozen_sources()
    if value["sources"] != current:
        raise SafeError(f"Frozen code, prompts or profiles changed after sealing: "
                        f"{sorted(k for k in value['sources'].keys() | current.keys() if value['sources'].get(k) != current.get(k))}")
    for key, path in (("protocol_sha256", PROTOCOL), ("profile_sha256", PROFILE), ("base_sha256", BASE / "examples.jsonl"),
                      ("data_sha256", DATA / "examples.jsonl"), ("price_sha256", DATA / "prices.json")):
        if value[key] != sha256_file(path):
            raise SafeError(f"{_rel(path)} changed after sealing")
    return value


# ---------------------------------------------------------------- run


def examples() -> list[Example]:
    return base_examples() + [Example(**r) for r in read_jsonl(DATA / "examples.jsonl")]


def run(resume: Path | None = None, budget: float = 5.0) -> Path:
    seal = check_seal()
    items = examples()
    config = load_config(CONFIG)
    load_dotenv(REPO_ROOT / ".env")
    run_dir = RunDir.existing(resume) if resume else None
    if run_dir and json.loads((run_dir.path / "manifest.json").read_text())["seal_sha256"] != sha256_file(DATA / "seal.json"):
        raise SafeError("This run belongs to a different seal")
    if not run_dir:
        for m in sorted((REPO_ROOT / "runs").glob("valnoise-*/manifest.json")):
            if json.loads(m.read_text()).get("seal_sha256") == sha256_file(DATA / "seal.json"):
                raise SafeError(f"A run for this seal exists; resume it with --run {m.parent}")
        run_dir = RunDir.create("valnoise")
    snapshot = load_snapshot(DATA / "prices.json")
    client = StudyClient(config, snapshot, budget)
    client.pacers = {model: Pacer(0) for model in client.pacers}  # admission is paced below, outside the timer
    prices = {c.label: price_for(snapshot, c.model_id) for c in config.candidates}
    prompts = {task: load_prompt(path, task) for task, path in config.prompts.items()}
    profiles = {arm: json.loads(path.read_text()) for arm, path in PROFILES.items()}
    routers = profile_routers(client, config, profiles, prices, prompts)
    if not resume:
        run_dir.write_manifest(study=STUDY, component=COMPONENT, display_milestone=DISPLAY,
                               seal_sha256=sha256_file(DATA / "seal.json"), n_conversations=len(items) // 2,
                               n_expected_requests=len(items) * len(routers), arms=list(routers),
                               order_seed=seal["seed"], load=LOAD, budget_usd=budget)
    path = run_dir.path / "traces.jsonl"
    done = {(t["example_id"], t["strategy"]) for t in read_jsonl(path)} if path.exists() else set()
    print(f"Noise run {_rel(run_dir.path)}: {len(items)} conversations x {len(routers)} arms, "
          f"{len(items) * len(routers) - len(done)} requests to make", flush=True)
    run_evaluation(routers, items, run_dir, concurrency=LOAD["concurrency"], rpm=LOAD["rpm"],
                   order_seed=seal["seed"], skip=done)
    print(f"API spend ${client.spent:.4f}", flush=True)
    report(run_dir.path)
    return run_dir.path


# ---------------------------------------------------------------- report


def _outcome(example: Example, trace: Trace) -> RequestOutcome:
    s = score("extraction", example.expected, trace.final_output)
    return RequestOutcome(
        example_id=example.id.removesuffix(NOISY), family=example.tags[0], review_required=requires_review(example),
        released=trace.final_valid, correct=s.task_correct, field_accuracy=s.field_accuracy,
        cost_usd=trace.cost_usd, latency_ms=trace.latency_ms, cost_complete=trace.cost_complete,
        escalated=trace.escalated, fallback=any(c.reasoning == "on" for c in trace.calls))


def _counts(outcomes: list[RequestOutcome]) -> dict[str, int]:
    return {"n": len(outcomes), "cac": sum(o.cac for o in outcomes),
            "wrong_releases": sum(o.undetected for o in outcomes)}


def _analyzer(example: Example) -> str:
    """The deployed issue-date analyzer against the label: agree, or how it disagrees."""
    a = analyze_issue_date(example.input)
    if requires_review(example):
        return "agree" if a.status == "review" else f"no review ({a.status})"
    if a.status == "review":
        return "review (known ambiguity)" if a.known_ambiguity else "review (unrecognized wording)"
    return "agree" if (a.date if a.status == "resolved" else None) == example.expected["refund_date"] else "wrong date"


def decision(cells: dict[str, dict[str, dict[str, list[RequestOutcome]]]]) -> dict[str, Any]:
    """Checks N1-N5 exactly as preregistered in the amendment."""
    g = GATES
    rep, ch = cells["representative"], cells["challenge"]
    noisy = summarize(rep["noisy"]["adopted"])
    detail = {
        "N1_N2_adopted_noisy_representative": noisy,
        "N3_cac_noisy_minus_clean": paired(rep["noisy"]["adopted"], rep["clean"]["adopted"], "cac"),
        "N3_wrong_release_noisy_minus_clean": paired(rep["noisy"]["adopted"], rep["clean"]["adopted"], "undetected"),
        "N4_cac_adopted_minus_previous": paired(rep["noisy"]["adopted"], rep["noisy"]["previous"], "cac"),
        "N4_wrong_release_adopted_minus_previous": paired(rep["noisy"]["adopted"], rep["noisy"]["previous"], "undetected"),
        "N4_cost_adopted_minus_grounded_super": paired(rep["noisy"]["adopted"], rep["noisy"]["grounded_super"],
                                                       "cost_per_1k"),
        "N5_challenge_wrong_release_noisy_minus_clean": paired(ch["noisy"]["adopted"], ch["clean"]["adopted"],
                                                               "undetected"),
    }
    checks = {
        "N1": noisy["undetected_upper95"] <= g["wrong_release_limit"],
        "N2": (noisy["accuracy_lower95"] >= g["accuracy"] and noisy["field_accuracy"] >= g["field_accuracy"]
               and noisy["deadline_miss_upper95"] <= g["deadline_miss_limit"] and noisy["incomplete_cost_count"] == 0),
        "N3": (detail["N3_cac_noisy_minus_clean"]["ci95"][0] >= -g["cac_margin"]
               and detail["N3_wrong_release_noisy_minus_clean"]["ci95"][1] <= g["wrong_release_margin"]),
        "N4": (detail["N4_cac_adopted_minus_previous"]["ci95"][0] >= -g["cac_margin"]
               and detail["N4_wrong_release_adopted_minus_previous"]["ci95"][1] <= g["wrong_release_margin"]
               and detail["N4_cost_adopted_minus_grounded_super"]["ci95"][1] < 0),
        "N5": detail["N5_challenge_wrong_release_noisy_minus_clean"]["ci95"][1] <= g["challenge_wrong_release_margin"],
    }
    return {"checks": checks, "robust": all(checks.values()), "detail": detail}


def report(run_path: Path) -> dict[str, Any]:
    manifest = json.loads((run_path / "manifest.json").read_text())
    items = {e.id: e for e in examples()}
    split_of = {i: name for name, ids in json.loads((BASE / "splits.json").read_text()).items() for i in ids}
    first: dict[tuple[str, str], Trace] = {}
    for row in read_jsonl(run_path / "traces.jsonl") if (run_path / "traces.jsonl").exists() else []:
        first.setdefault((row["example_id"], row["strategy"]), Trace(**row))
    cells: dict[str, dict[str, dict[str, list[RequestOutcome]]]] = {
        s: {c: {a: [] for a in ARMS} for c in CONDITIONS} for s in SPLITS}
    for (eid, arm), trace in sorted(first.items()):
        e = items[eid]
        cells[split_of[eid.removesuffix(NOISY)]]["noisy" if eid.endswith(NOISY) else "clean"][arm].append(
            _outcome(e, trace))
    complete = len(first) == manifest["n_expected_requests"]
    label_word = {e.id.removesuffix(NOISY) for e in items.values() if "noise:label_word" in e.tags}
    result: dict[str, Any] = {
        "study": STUDY, "component": COMPONENT, "display_milestone": DISPLAY, "run": run_path.name,
        "complete": complete, "n_requests": len(first), "n_expected_requests": manifest["n_expected_requests"],
        "metrics": {s: {c: {a: summarize(o) for a, o in arms.items()} for c, arms in conds.items()}
                    for s, conds in cells.items()},
        "noise_effect": {s: {a: {m: paired(cells[s]["noisy"][a], cells[s]["clean"][a], m)
                                 for m in ("cac", "undetected", "review", "cost_per_1k")} for a in ARMS}
                         for s in SPLITS},
        "label_word_edits": {s: {group: {c: summarize([o for o in cells[s][c]["adopted"]
                                                       if (o.example_id in label_word) == (group == "edited")])
                                         for c in CONDITIONS} for group in ("edited", "not_edited")} for s in SPLITS},
        "by_family": {s: {f: {c: {a: _counts([o for o in cells[s][c][a] if o.family == f]) for a in ARMS}
                              for c in CONDITIONS}
                          for f in sorted({o.family for o in cells[s]["clean"]["adopted"]})} for s in SPLITS},
        "issue_date_analyzer": {s: {c: dict(Counter(_analyzer(e) for e in items.values()
                                                    if split_of[e.id.removesuffix(NOISY)] == s
                                                    and e.id.endswith(NOISY) == (c == "noisy")))
                                    for c in CONDITIONS} for s in SPLITS},
        "total_api_cost_usd": sum(t.cost_usd for t in first.values()),
        "incomplete_cost_requests": sum(not t.cost_complete for t in first.values()),
    }
    result["decision"] = decision(cells) if complete else None
    dump(run_path / "summary.json", result)
    print(json.dumps({"complete": complete, "checks": result["decision"] and result["decision"]["checks"],
                      "robust": result["decision"] and result["decision"]["robust"],
                      "total_api_cost_usd": round(result["total_api_cost_usd"], 4)}, indent=1), flush=True)
    return result


def main(args) -> None:
    if args.stage == "profile":
        if not args.mbox or not args.private_root:
            raise SafeError("profile needs --mbox and --private-root")
        profile(args.mbox, args.private_root)
    elif args.stage == "publish":
        if not args.profile or not args.approve:
            raise SafeError("publish needs --profile, --approve <sha256> and --reviewer")
        publish(args.profile, args.approve, args.reviewer or "")
    elif args.stage == "build":
        build()
    elif args.stage == "run":
        run(args.run, args.budget)
    elif args.stage == "report":
        if not args.run:
            raise SafeError("report needs --run")
        report(args.run)
