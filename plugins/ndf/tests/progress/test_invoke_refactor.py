"""工程「リファクタリング」の差し込み `refactor`（`ndf.progress.refactor_values`）。

実装計画のスプリントの終わりの候補を cross-refactoring へ渡し（#3879 構成要素 5）、設定が無ければ既定の起動を組む（#3908 の決定 7）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import strategy_fixture
from sprint_fixture import sprint_repo as _sprint_repo
from sprint_fixture import with_worker as _with_worker

from ndf.adapters.git import Git
from ndf.core import branch_strategy, route
from ndf.progress import command, definition, invoke


# #3882 の implementation-plan の「リファクタリングの時機」の表の形（時機 | 対象 | 理由）
SPRINT_END_PLAN = """# 実装計画: #{n}

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルは 120 行でテストは 8 件 |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | {target} | {reason} |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| スプリントの終わり | 表の外の行は読まない |
"""
REFACTOR_PROJECT = {"workflow": {"launch": {"refactor": ["ndf-refactor", "--pr", "7"]}}}
# `workflow` のキーを持たないリポジトリの設定（読み込み済みの `.ndf/project.json` の中身。既定の起動を組む材料）
DEFAULT_PROJECT = {"branch_strategy": strategy_fixture.section("web-develop"), "ci": {"required_checks": ["test"]}}


def _write_plan(repo: Path, n: int, target: str, reason: str = "3 つの単位が同じ関数へ足す") -> None:
    (repo / "issues").mkdir(exist_ok=True)
    (repo / "issues" / f"issue-{n}-plan.md").write_text(SPRINT_END_PLAN.format(n=n, target=target, reason=reason), encoding="utf-8")


def _refactor_values(tmp_path: Path, monkeypatch, issues: list[int], prepare, project: dict = REFACTOR_PROJECT) -> tuple[dict, Path]:
    _with_worker(monkeypatch)
    repo = _sprint_repo(tmp_path, project)
    prepare(repo)
    work = tmp_path / "w"
    work.mkdir()
    inputs = {"worktree": str(repo), "branch": "sprint/s1", "issues": issues}
    run = invoke.StageRun(invoke.PLAN, "cross-refactoring", inputs, (), "none", "refactor")
    t = invoke.template("cross-refactoring")
    assert t is not None
    return invoke.template_values(t, run, work, repo, project=project, local_tests=True), work


def test_cross_refactoring_gets_the_sprint_end_rows_of_the_implementation_plans_as_candidates(tmp_path, monkeypatch):
    # #3879 構成要素 5: 実装計画の「スプリントの終わり」の行を課題の番号を添えて書き、refactor の後ろに --candidates を足す
    def prepare(repo: Path) -> None:
        _write_plan(repo, 1, "`invoke.py` の補いの並び")
        _write_plan(repo, 3, "しない", "単位が 1 つ")

    values, work = _refactor_values(tmp_path, monkeypatch, [1, 2, 3], prepare)
    candidates = work / "refactor-candidates.md"
    assert values["refactor"] == ["ndf-refactor", "--pr", "7", "--candidates", str(candidates)]
    assert candidates.read_text(encoding="utf-8") == "- #1: `invoke.py` の補いの並び（理由: 3 つの単位が同じ関数へ足す）\n"


def test_cross_refactoring_keeps_a_sprint_end_row_with_escaped_pipes(tmp_path, monkeypatch):
    # エスケープしたパイプは列を区切らない（正しい Markdown の表の行を黙って落とさない）
    def prepare(repo: Path) -> None:
        _write_plan(repo, 1, "`a \\| b` の分岐", "A \\| B の両方で使う")

    values, work = _refactor_values(tmp_path, monkeypatch, [1], prepare)
    assert values["refactor"][-1] == str(work / "refactor-candidates.md")
    assert (work / "refactor-candidates.md").read_text(encoding="utf-8") == "- #1: `a | b` の分岐（理由: A | B の両方で使う）\n"


def test_cross_refactoring_without_sprint_end_candidates_keeps_the_launch_and_writes_no_file(tmp_path, monkeypatch):
    def prepare(repo: Path) -> None:
        _write_plan(repo, 1, "しない", "単位が 1 つ")
        # 「リファクタリングの時機」の見出しの無い実装計画（表の外のスプリントの終わりの行は読まない）
        (repo / "issues" / "issue-2-plan.md").write_text("# 実装計画: #2\n\n| スプリントの終わり | x | y |\n", encoding="utf-8")

    values, work = _refactor_values(tmp_path, monkeypatch, [1, 2], prepare)
    assert values["refactor"] == ["ndf-refactor", "--pr", "7"]
    assert not (work / "refactor-candidates.md").exists()


def test_refactor_stage_of_the_workflow_builds_the_plan_with_the_candidates(tmp_path, monkeypatch):
    # 手で組んだ StageRun でなく、実際の工程の定義から入力を取り、プランを組む
    _with_worker(monkeypatch)
    repo = _sprint_repo(tmp_path, REFACTOR_PROJECT)
    _write_plan(repo, 1, "`command.py` の作成の関数")
    stage = definition.load_definition("standard").stage("リファクタリング")
    context = {"issues": [1], "worktree": str(repo), "branch": "sprint/s1", "mode": "standard"}
    run = invoke.stage_run(stage, stage.steps[0], invoke.resolve_inputs(stage, context))
    assert run.inputs["issues"] == [1]
    work = tmp_path / "w"
    work.mkdir()
    argv = invoke.plan_argv(run, work, None, repo)
    plan = json.loads(Path(argv[argv.index("next") + 1]).read_text())
    refactor = next(s for s in plan["steps"] if s["id"] == "refactor")
    assert refactor["run"][-2:] == ["--candidates", str(work / "refactor-candidates.md")]


def _watch_load(monkeypatch) -> list[Path]:
    """`branch_strategy.load`（設定のファイルの読み直し）を呼んだら記録する見張り。"""
    seen: list[Path] = []
    held = branch_strategy.load
    monkeypatch.setattr(branch_strategy, "load", lambda root: seen.append(Path(root)) or held(root))
    return seen


def test_refactor_without_the_workflow_settings_is_the_default_launch(tmp_path, monkeypatch):
    # #3908 の決定 7・受け入れ条件 2: `.ndf/project.json` に workflow が無ければ、既定の起動（REFACTOR_WITH_PR の包み）を組む。
    # 起点は読み込み済みの設定のマージ先（develop）を、工程の条件 code_in_diff と同じ規則で参照にしたもの
    seen = _watch_load(monkeypatch)
    values, _ = _refactor_values(tmp_path, monkeypatch, [1], lambda repo: None, DEFAULT_PROJECT)
    assert values["refactor"] == route.refactor_launch("sprint/s1", "origin/develop", ["test"])
    assert seen == []


def test_the_default_launch_takes_the_remote_of_the_worktree_as_the_stage_condition_does(tmp_path, monkeypatch):
    # 手元にマージ先のブランチが無いリポジトリでは、作業場所のリモート名を付けた参照にする（`command.base_ref` の 1 か所）
    def prepare(repo: Path) -> None:
        (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "remote": "upstream"}), encoding="utf-8")

    values, _ = _refactor_values(tmp_path, monkeypatch, [1], prepare, DEFAULT_PROJECT)
    repo = tmp_path / "repo"
    assert values["refactor"] == route.refactor_launch("sprint/s1", "upstream/develop", ["test"])
    assert command.base_ref(Git(repo), "develop") == "upstream/develop"


def test_the_default_launch_gets_the_candidates_after_it(tmp_path, monkeypatch):
    values, work = _refactor_values(tmp_path, monkeypatch, [1], lambda repo: _write_plan(repo, 1, "`invoke.py`"), DEFAULT_PROJECT)
    launch = route.refactor_launch("sprint/s1", "origin/develop", ["test"])
    assert values["refactor"] == [*launch, "--candidates", str(work / "refactor-candidates.md")]


def test_refactor_without_the_required_checks_is_named_as_missing(tmp_path, monkeypatch):
    # 必須のチェックが無ければ既定を組まず、refactor を欠けとして挙げて起動の前に止まる
    project = {"branch_strategy": DEFAULT_PROJECT["branch_strategy"]}
    with pytest.raises(invoke.TemplateValuesMissing) as got:
        _refactor_values(tmp_path, monkeypatch, [1], lambda repo: None, project)
    assert got.value.missing["refactor"] == invoke.FROM_PROJECT
