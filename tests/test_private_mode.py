"""Private validation mode, tested with synthetic, sensitive-looking data before any company data is loaded."""

import contextlib
import io
import json
import logging
import os
import re
import subprocess
import warnings

import httpx
import pytest

from fakes import MODELS, completion_body, transport
from routewise import artifacts, cli, private_mode as pm, privacy_drill, validation_export as ve
from routewise import workload_validation as wv
from routewise.client import TokenFactoryClient
from routewise.config import REPO_ROOT
from routewise.labels import TAG_VOCABULARY
from routewise.prices import build_snapshot


def drill_answer(text):
    """A fake model that copies the synthetic order ID and amount, so model outputs hold private values too."""
    amount, order = re.search(r"(\d+\.\d{2}) USD", text), re.search(r"RW\d+K", text)
    first = re.search(r"\[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\] Customer", text)
    return {"refund_requested": True, "refund_amount": float(amount.group(1)) if amount else None,
            "currency": "USD" if amount else None, "order_id": order.group() if order else None,
            "contact_date": first.group(1) if first else None, "refund_date": None}


def offline(mp):
    def snapshot(config, path):
        path.write_text(json.dumps(build_snapshot(MODELS, config.base_url)))

    def study_client(config, snap, budget):
        def chat(body):
            return httpx.Response(200, json=completion_body(json.dumps(drill_answer(body["messages"][-1]["content"]))))
        client = TokenFactoryClient(api_key="test", http_client=httpx.Client(transport=transport(chat)))
        client.pacers, client.spent = {c.model_id: None for c in config.candidates}, 0.0
        return client

    mp.setattr(wv, "fetch_prices", snapshot)
    mp.setattr(wv, "StudyClient", study_client)
    mp.setattr(wv, "LOAD", {"concurrency": 4, "rpm": 0})


@pytest.fixture(scope="module")
def drill(tmp_path_factory):
    """The full drill, offline: the real command line on synthetic conversations with a fake model."""
    printed = io.StringIO()
    with pytest.MonkeyPatch.context() as mp, contextlib.redirect_stdout(printed):
        offline(mp)
        d = privacy_drill.run_drill(tmp_path_factory.mktemp("privacy") / "drill")
    d.printed = printed.getvalue()
    return d


def test_every_drill_check_passes_offline(drill):
    assert [name for name, ok in drill.results if not ok] == []
    assert len(drill.results) == 16 and not drill.leaks(drill.printed)
    assert "PASS" in drill.printed and len(drill.workload.canaries) > 70


def test_private_values_reached_only_private_files(drill):
    """Positive controls: the values the console never showed are where they belong."""
    run = privacy_drill._run(drill)
    traces = (run / "traces.jsonl").read_text()
    assert drill.workload.first[0]["order_id"] in traces  # model outputs, kept in the output directory
    diagnostics = json.loads((run / "diagnostics.json").read_text())
    assert diagnostics["wrong_releases"]["adopted"]  # "soon" is released as no date: the known analyzer gap
    queue = (drill.output / "data" / "drill" / "adjudication_queue.csv")
    assert not queue.exists()  # removed once adjudicated
    assert drill.leaks((drill.output / "data" / "drill" / "examples.jsonl").read_text())
    manifest = json.loads((drill.output / "data" / "drill" / "MANIFEST.json").read_text())
    assert len(manifest["pii_warnings"]) == 8 and manifest["n_excluded"] == 1 and manifest["n_adjudicated"] == 1
    warned = [c for c in drill.console if "8 conversation(s) may contain personal data" in c]
    assert len(warned) == 1 and "{'email': 8, 'phone': 8, 'card': 8}" in warned[0]  # counts only, never values


def test_export_allowlist_names_problems_without_quoting_them(drill):
    staged = json.loads((drill.output / "exports" / privacy_drill._run(drill).name / "aggregate.json").read_text())
    assert ve.problems(staged) == []
    secret = drill.workload.first[0]["order_id"]
    bad = json.loads(json.dumps(staged))
    bad["notes"] = secret
    bad["metrics"]["adopted"]["cac_rate"] = secret
    bad["by_tag"].append({"tag": "vip-" + secret.lower(), "arm": "adopted", "n": 50, "cac_rate": 1.0,
                          "review_rate": 0.0, "wrong_releases": 0, "task_accuracy": 1.0})
    bad["review_causes"]["adopted"]["issue date: unrecognized issue-date language: " + secret] = 1
    bad["wrong_fields"]["adopted"][secret] = 1
    bad["provenance"]["dataset_sha256"] = secret
    found = ve.problems(bad)
    assert len(found) == 6 and not any(secret.casefold() in f.casefold() for f in found)
    assert "aggregate: unexpected field" in found and "aggregate.by_tag[0].tag: value not allowed" in found


def test_small_cells_and_unlisted_tags_are_withheld(drill):
    run = privacy_drill._run(drill)
    summary = json.loads((run / "summary.json").read_text())
    manifest = json.loads((run / "manifest.json").read_text())
    dataset = json.loads((drill.output / "data" / "drill" / "MANIFEST.json").read_text())
    assert any(t not in TAG_VOCABULARY for t in dataset["scenario_tags"])  # the vip- tag
    everything = ve.build(summary, manifest, dataset, min_cell=1)
    assert {r["tag"] for r in everything["by_tag"]} <= set(TAG_VOCABULARY) and everything["withheld"]["tag_cells_below_min_cell"] == 0
    default = ve.build(summary, manifest, dataset)
    assert default["by_tag"] == [] and default["labels"]["primary_scenarios"] == {}  # 7 conversations: all below 10
    assert default["withheld"]["tag_cells_below_min_cell"] == len(everything["by_tag"]) + len(everything["labels"]["primary_scenarios"])
    with pytest.raises(pm.SafeError, match="at least 5"):
        ve.stage(pm.PrivateDirs(drill.input, drill.output), run, summary, min_cell=3)


def test_publication_refuses_private_values_and_writes_nothing(drill, tmp_path):
    dirs = pm.PrivateDirs(drill.input, drill.output)
    first = drill.workload.first[0]
    phone = first["text"].split(", ")[1].split(")")[0]
    for text in (first["conversation_id"], phone.replace(" ", "-"), first["text"].splitlines()[1][19:]):
        with pytest.raises(pm.SafeError, match="found in the private data") as err:
            pm.publish({tmp_path / "public.md": "ok", tmp_path / "design.json": f"see {text}"}, dirs)
        assert not drill.leaks(str(err.value)) and not list(tmp_path.iterdir())
    pm.publish({tmp_path / "design.json": json.dumps({"n_required": 120})}, dirs)
    assert (tmp_path / "design.json").stat().st_mode & 0o777 == 0o644


def test_leak_scan_reports_locations_not_values(drill, tmp_path, capsys):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("private/\n")
    first = drill.workload.first[0]
    (repo / f"{first['conversation_id']}.json").write_text("{}\n")  # the file name itself is private
    (repo / "private").mkdir()
    (repo / "private" / "copy.txt").write_text(first["text"])  # git-ignored, but inside the repository
    (repo / "notes.md").write_text("Nothing private here.\n")
    dirs = pm.PrivateDirs(drill.input, drill.output)
    findings, stats = pm.leak_scan(dirs, repo)
    assert {f.location for f in findings} == {f"{first['conversation_id']}.json", "private/copy.txt"}
    assert [f.location_is_private for f in findings if f.location.endswith(".json")] == [True]
    with pytest.raises(pm.SafeError, match="2 location"):
        wv.leak_scan(dirs, repo)
    shown = capsys.readouterr().out
    assert "a path that itself contains private data" in shown and "private/copy.txt" in shown
    assert not drill.leaks(shown) and stats["files"] == 4


def test_private_directories_must_be_outside_every_repository(tmp_path, monkeypatch):
    def refused(match, *paths, need_input=False):
        with pytest.raises(pm.SafeError, match=match):
            pm.resolve_dirs(*paths, need_input=need_input)

    monkeypatch.delenv(pm.INPUT_ENV, raising=False)
    monkeypatch.delenv(pm.OUTPUT_ENV, raising=False)
    refused("needs --output", None, None)
    refused("inside this repository", None, REPO_ROOT / "private" / "out")
    refused("contains this repository", None, REPO_ROOT.parent)
    subprocess.run(["git", "init", "-q", str(tmp_path / "other")], check=True)
    refused("inside the git work tree", None, tmp_path / "other" / "out")
    (tmp_path / "link").symlink_to(REPO_ROOT)
    refused("inside this repository", None, tmp_path / "link" / "out")
    (tmp_path / "in").mkdir()
    refused("separate", tmp_path / "in", tmp_path / "in" / "out")
    refused("does not exist", tmp_path / "missing", tmp_path / "out", need_input=True)
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "file.txt").write_text("x")
    refused("not empty and was not created by RouteWise", None, tmp_path / "busy")
    assert not (REPO_ROOT / "private" / "out").exists()

    monkeypatch.setenv(pm.OUTPUT_ENV, str(tmp_path / "out"))
    dirs = pm.resolve_dirs(None, None, need_input=False)
    assert dirs.output == (tmp_path / "out").resolve() and dirs.input is None
    assert dirs.output.stat().st_mode & 0o777 == 0o700 and (dirs.output / pm.MARKER).exists()
    dirs.output.chmod(0o750)
    refused("accessible to other users", None, None)


def test_session_keeps_failures_and_warnings_off_the_console(tmp_path, capfd):
    out = pm.resolve_dirs(None, tmp_path / "out", need_input=False).output
    umask = os.umask(0o022)
    os.umask(umask)
    handlers = logging.getLogger().handlers[:]
    with pytest.raises(pm.SafeError) as err, pm.session(out):
        warnings.warn("customer Savequi Ritorpel at 511 Kakaka Street")
        logging.getLogger("openai").warning("request for RW7395545K")
        (out / "made.txt").write_text("x")
        raise KeyError("savequi.ritorpel.583@example.com")
    assert str(err.value).startswith("KeyError; its message may quote private data") and "savequi" not in str(err.value)
    shown = capfd.readouterr()
    assert "Savequi" not in shown.out + shown.err and "RW7395545K" not in shown.out + shown.err
    log = (out / "logs" / "routewise.log").read_text()
    assert "Kakaka Street" in log and "RW7395545K" in log
    assert "savequi.ritorpel" in next((out / "logs").glob("error-*.txt")).read_text()
    assert (out / "made.txt").stat().st_mode & 0o077 == 0
    assert os.umask(umask) == umask and logging.getLogger().handlers == handlers


def test_command_line_withholds_messages_it_cannot_vouch_for(tmp_path, capfd, monkeypatch):
    monkeypatch.delenv(pm.OUTPUT_ENV, raising=False)
    assert cli.main(["validation", "report", "--run", "x"]) == 1
    assert "needs --output" in capfd.readouterr().err

    def explode(args):
        raise ValueError("Savequi Ritorpel, card 4317 0669 0743 9153")
    monkeypatch.setattr(wv, "main", explode)
    assert cli.main(["validation", "report", "--run", "x", "--output", str(tmp_path / "out")]) == 1
    err = capfd.readouterr().err
    assert "ValueError" in err and "Savequi" not in err and "4317" not in err


def test_dashboard_never_reads_a_private_directory(tmp_path, monkeypatch):
    out = pm.resolve_dirs(None, tmp_path / "out", need_input=False).output
    for root in (out, out / "runs" / "valpilot-x"):
        monkeypatch.setenv("ROUTEWISE_ARTIFACTS", str(root))
        with pytest.raises(pm.SafeError, match="private validation directory"):
            artifacts.artifacts_root()
    monkeypatch.setenv("ROUTEWISE_ARTIFACTS", str(tmp_path))
    assert artifacts.artifacts_root() == tmp_path
    app = "\n".join(p.read_text() for p in (REPO_ROOT / "app").rglob("*.py"))
    for reference in ("ROUTEWISE_PRIVATE", "workload_validation", "privacy_drill", "validation_export", "valpilot",
                      "valconfirm", "valsmoke", "M8-validation"):
        assert reference not in app


def test_published_validation_exports_pass_the_allowlist():
    for path in sorted((REPO_ROOT / "results" / "M8-validation").glob("*.json")):
        value = json.loads(path.read_text())
        assert ve.problems(value) == [] and "review" in value and not value["synthetic"], path


def test_vocabularies_match_their_sources():
    guide = (REPO_ROOT / "data/validation-template/README.md").read_text()
    listed = re.findall(r"`([a-z][a-z_]*)`", guide[guide.index("## Scenario tags"):guide.index("## Adjudication")])
    assert tuple(listed) == TAG_VOCABULARY
    rules = set(re.findall(r'Check\("([a-z_]+)"', (REPO_ROOT / "src/routewise/validate.py").read_text()))
    assert rules == set(ve.VALIDATION_RULES)
    analyzer = (REPO_ROOT / "src/routewise/issue_dates.py").read_text()
    recovery = (REPO_ROOT / "src/routewise/recovery.py").read_text()
    reasons = set(re.findall(r'IssueDateAnalysis\("review", None, "([^":]+)', analyzer))
    reasons |= {"fallback needs review", "fallback unusable"} if '"review: fallback " + ("needs review" if fb else ' \
        '"unusable")' in recovery else {"(fallback wording changed)"}
    reasons |= set(re.findall(r'"review: (fallback [a-z ]+)"', recovery))
    assert reasons == set(ve.ISSUE_DATE_CAUSES)
