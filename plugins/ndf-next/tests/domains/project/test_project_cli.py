"""`ndf project check`・`schema` の判定と終了コード（#3569）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ndf.domains.project import check, cli


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


def _declare(repo: Path, analysis: dict | None) -> None:
    data: dict = {"version": 1}
    if analysis is not None:
        data["analysis"] = analysis
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "project.json").write_text(json.dumps(data), encoding="utf-8")


def _fresh_analysis(repo: Path) -> dict:
    g = check.git.Git(repo)
    return {"analyzer": check.ANALYZER, "at": "2026-11-15T00:00:00+00:00", "inputs": check.inputs(g), "branches": check.branch_state(g)}


def test_inputs_pick_the_input_table():
    assert check.is_input("pyproject.toml")
    assert check.is_input("sub/package.json")
    assert check.is_input(".github/workflows/ci.yml")
    assert not check.is_input("a/b/package.json")
    assert not check.is_input("README.md")


def test_missing_fresh_stale_and_force(repo, capsys, last_result):
    assert cli.main(["check", "--root", str(repo)]) == 3
    assert last_result(capsys.readouterr().out)["items"][0]["kind"] == "missing"
    _declare(repo, _fresh_analysis(repo))
    assert cli.main(["check", "--root", str(repo)]) == 0
    assert last_result(capsys.readouterr().out)["metrics"] == {"inputs": 1}
    assert cli.main(["check", "--force", "--root", str(repo)]) == 3
    (repo / "pyproject.toml").write_text("[project]\nname = 'y'\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "change")
    assert cli.main(["check", "--root", str(repo)]) == 3
    got = last_result(capsys.readouterr().out)
    assert got["cause"]["kind"] == "not_applicable"
    assert [i["kind"] for i in got["items"]] == ["stale"]


def test_old_analyzer_and_hand_written_are_stale(repo, capsys, last_result):
    _declare(repo, None)
    assert cli.main(["check", "--root", str(repo)]) == 3
    _declare(repo, {**_fresh_analysis(repo), "analyzer": check.ANALYZER - 1})
    assert cli.main(["check", "--root", str(repo)]) == 3
    assert len(last_result(capsys.readouterr().out)["items"]) == 1


def test_broken_declarations_are_2(repo, capsys, last_result):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert cli.main(["check", "--root", str(repo)]) == 2
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    (repo / ".ndf" / "project.json").write_text(json.dumps({"version": 9}), encoding="utf-8")
    assert cli.main(["check", "--root", str(repo)]) == 2
    _declare(repo, _fresh_analysis(repo))
    (repo / ".ndf" / "worktree.json").write_text("[]", encoding="utf-8")
    assert cli.main(["check", "--root", str(repo)]) == 2


def test_outside_git_cannot_judge(tmp_path, capsys, last_result):
    assert cli.main(["check", "--root", str(tmp_path)]) == 1
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "unknown"


def test_check_reads_the_main_directory_from_a_linked_worktree(repo, tmp_path):
    _declare(repo, _fresh_analysis(repo))
    _git(repo, "add", ".ndf")
    _git(repo, "commit", "-qm", "decl")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", str(linked), "-b", "side")
    (repo / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert check.judge(linked, False).kind == check.BROKEN


def test_schema_prints_or_writes(tmp_path, capsys, last_result):
    assert cli.main(["schema"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert json.loads("\n".join(out[:-1]))["$id"] == "declarations/project.schema.json"
    target = tmp_path / "s" / "project.schema.json"
    assert cli.main(["schema", "--out", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["type"] == "object"


def test_unknown_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["measure-all"])
    assert exc.value.code == 2


def _measure_file(tmp_path: Path, items: dict) -> Path:
    path = tmp_path / "measure.json"
    path.write_text(json.dumps({"analyzer": check.ANALYZER, "items": items, "inputs": {}, "notes": ["注記"]}), encoding="utf-8")
    return path


def _answers(tmp_path: Path, answers: dict) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return path


def test_measure_writes_outside_the_repo_only(repo, tmp_path, capsys, last_result):
    assert cli.main(["measure", "--root", str(repo), "--out", str(repo / "m.json")]) == 1
    out = tmp_path / "out" / "m.json"
    assert cli.main(["measure", "--root", str(repo), "--out", str(out), "--budget", "30"]) == 0
    got = last_result(capsys.readouterr().out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["items"]["ci"]["status"] == "unknown"  # origin が GitHub でない
    assert data["items"]["languages"]["status"] == "measured"
    assert got["metrics"]["out"] == str(out)


def test_write_creates_then_keeps_hand_values(repo, tmp_path, capsys, last_result):
    measured = _measure_file(
        tmp_path,
        {"checks": {"status": "measured", "value": {"tools": []}}, "test": {"status": "question", "candidates": {}, "evidence": []}},
    )
    answers = _answers(tmp_path, {"test": {"value": {"suites": []}}, "nope": {"value": 1}})
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(answers), "--dry-run"]) == 0
    assert not (repo / ".ndf" / "project.json").exists()
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(answers)]) == 0
    got = last_result(capsys.readouterr().out)
    kinds = {r["key"]: r["kind"] for r in got["items"] if "key" in r}
    assert kinds["checks"] == "written" and kinds["test"] == "written" and kinds["nope"] == "ignored"
    decl = json.loads((repo / ".ndf" / "project.json").read_text(encoding="utf-8"))
    assert decl["checks"] == {"tools": []} and decl["analysis"]["analyzer"] == check.ANALYZER
    decl["checks"] = {"tools": [{"name": "ruff", "config": "ruff.toml"}]}  # 人が書き換えた値
    (repo / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(answers)]) == 0
    rows = {r["key"]: r for r in last_result(capsys.readouterr().out)["items"] if "key" in r}
    assert rows["checks"]["kind"] == "mismatch"
    assert json.loads((repo / ".ndf" / "project.json").read_text(encoding="utf-8"))["checks"] == decl["checks"]


def test_write_refuses_bad_flow_answer_and_broken_declaration(repo, tmp_path, capsys, last_result):
    measured = _measure_file(tmp_path, {})
    bad = _answers(tmp_path, {"branch_flow": {"value": {"promotion": "whole", "start": "production"}}})
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(bad)]) == 1
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "check_failed"
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured)]) == 2


def test_write_adds_missing_branches_to_worktree(repo, tmp_path, capsys, last_result):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop"}), encoding="utf-8")
    measured = _measure_file(tmp_path, {"branches": {"status": "question", "candidates": {}, "evidence": []}})
    answers = _answers(tmp_path, {"branches": {"value": {"base": "main", "production": "main"}}})
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(answers)]) == 0
    rows = {r["key"]: r["kind"] for r in last_result(capsys.readouterr().out)["items"] if "key" in r}
    assert rows["worktree.json#base_branch"] == "mismatch"
    assert rows["worktree.json#production_branch"] == "written"
    wt = json.loads((repo / ".ndf" / "worktree.json").read_text(encoding="utf-8"))
    assert wt == {"version": 1, "base_branch": "develop", "production_branch": "main"}


def test_drift_compares_with_the_default_row(repo, tmp_path, capsys, last_result):
    flow = {"fields": {"promotion": {"value": "selected", "confidence": "高", "evidence": []}}}
    measured = _measure_file(tmp_path, {"branch_flow": flow})
    assert cli.main(["drift", "--root", str(repo), "--measure", str(measured)]) == 0
    got = last_result(capsys.readouterr().out)
    assert got["metrics"] == {"drifted": 1, "declared": False}
    assert got["items"][0]["name"] == "branch_flow.promotion"
    assert cli.main(["drift", "--root", str(repo), "--measure", str(tmp_path / "none.json")]) == 1
