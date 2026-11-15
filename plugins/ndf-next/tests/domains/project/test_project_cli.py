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
