"""工程「リファクタリング」の実行条件（#4566 の受け入れ条件 1・2・5・6・7・8・9・10 と不変条件 I1）。

条件の語 `tests_verifiable` の値と、既定の起動を組めるかが、どの設定でも一致する。
"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf.progress import command, definition, stages
from ndf.testing import means

SUITE = {"name": "unit", "runner": "pytest", "command": "pytest -q"}
ROWS = [
    # (設定, 手段)。手段の表の 4 行と、CI テストの設定のキーが無い・false の両方
    ({"ci": {"required_checks": ["test"]}, "test": {"suites": [SUITE]}}, means.Kind.REQUIRED_CHECKS),
    ({"ci": {"required_checks": ["test"]}, "test": {"ci": {"only": False}, "suites": [SUITE]}}, means.Kind.REQUIRED_CHECKS),
    ({"ci": {"required_checks": ["test"]}, "test": {"ci": {"only": True}, "suites": [SUITE]}}, means.Kind.REQUIRED_CHECKS),
    ({"test": {"ci": {"only": True}, "suites": [SUITE]}}, means.Kind.NONE),
    ({"test": {"suites": [SUITE]}}, means.Kind.LOCAL_TEST),
    ({"test": {"ci": {"only": False}, "suites": [SUITE]}}, means.Kind.LOCAL_TEST),
    ({"test": {"suites": [{**SUITE, "kind": "lint"}]}}, means.Kind.NONE),
    ({}, means.Kind.NONE),
]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    path = tmp_path / "repo"
    git_sandbox.sh(tmp_path, "init", "-q", str(path))
    return path


def _write(repo: Path, project: dict) -> None:
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "project.json").write_text(json.dumps(project), encoding="utf-8")


@pytest.mark.parametrize(("project", "kind"), ROWS)
def test_tests_verifiable_follows_the_means(repo, project, kind):
    _write(repo, project)
    assert command.facts_of(("tests_verifiable",), {}, repo) == {"tests_verifiable": kind != means.Kind.NONE}


def test_ci_declared_is_still_computed_for_old_definitions(repo):
    # 決定 7: 更新の前に作ったスプリントの定義は ci_declared を持つ
    _write(repo, {"test": {"ci": {"only": True}, "suites": [SUITE]}})
    assert command.facts_of(("ci_declared", "tests_verifiable"), {}, repo) == {"ci_declared": True, "tests_verifiable": False}


@pytest.mark.parametrize("mode", ["standard", "legacy-refactor"])
def test_the_refactor_stage_runs_when_code_changed_and_tests_are_verifiable(mode):
    snap = definition.load_definition(mode)
    assert snap.stage("リファクタリング").when == ("code_in_diff", "tests_verifiable")
    facts = {w: True for s in snap.stages for w in s.when}
    assert stages.resolve_enabled(snap, facts, {}, {})["リファクタリング"].enabled
    # 手段が無ければ理由を付けて飛ばす（条件 5・6）
    got = stages.resolve_enabled(snap, {**facts, "tests_verifiable": False}, {}, {})["リファクタリング"]
    assert got == stages.Enablement(False, "tests_verifiable", "definition")
    # 差分が文書だけなら先の語で飛ぶ（条件 9）
    docs = stages.resolve_enabled(snap, {**facts, "code_in_diff": False, "tests_verifiable": False}, {}, {})
    assert docs["リファクタリング"] == stages.Enablement(False, "code_in_diff", "definition")


def test_the_project_settings_win_over_the_definition():
    snap = definition.load_definition("standard")
    facts = {w: True for s in snap.stages for w in s.when}
    off = {"リファクタリング": {"enabled": False, "reason": "手元のテストが重い"}}
    assert stages.resolve_enabled(snap, facts, off, {})["リファクタリング"] == stages.Enablement(False, "手元のテストが重い", "project")
    on = {"リファクタリング": {"enabled": True, "reason": ""}}
    none = {**facts, "tests_verifiable": False}
    assert stages.resolve_enabled(snap, none, on, {})["リファクタリング"] == stages.Enablement(True, None, "project")


@pytest.mark.parametrize("mode", ["light", "operation", "documentation"])
def test_other_modes_have_no_refactor_stage(mode):
    assert "リファクタリング" not in {s.name for s in definition.load_definition(mode).stages}
