"""工程「実装」を実装計画のレビューの単位ごとに流す変更（#4369）の、工程から作業を呼ぶ層のテスト。

どの課題の実装計画にも「レビューの単位」の見出しが無いスプリントは、課題が複数でも実装のプランを 1 本だけ流し、
ブランチは作業場所が今いるものを使う（受け入れ条件 2）。文脈の `branch`（工程「作業場所の用意」が登録したブランチ）は
差し込みに使わない。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import sprint_fixture

from ndf.contracts import result
from ndf.contracts.worker_kinds import read_values
from ndf.core import route
from ndf.progress import definition, invoke, issue_values

PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
    "workflow": {"launch": {"review": ["ndf-review"]}},
}
PLAN_WITHOUT_UNITS = "# 3・4: 実装計画\n\n## タスク分解\n\n### Task 1: 直す\n- **対象ファイル:** `a.py`\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True)


def _sprint(tmp_path: Path, plan_text: str | None) -> Path:
    """ブランチ sprint/s1 に居る作業場所。`plan_text` があれば、課題 3・4 の実装計画としてコミットする。"""
    repo = sprint_fixture.sprint_repo(tmp_path, PROJECT)
    if plan_text is not None:
        (repo / "issues").mkdir()
        (repo / "issues" / "issue-3-4-plan.md").write_text(plan_text, encoding="utf-8")
        _git(repo, "add", "issues")
        _git(repo, "commit", "-q", "-m", "plan")
    return repo


@pytest.mark.parametrize("plan_text", [PLAN_WITHOUT_UNITS, None], ids=["見出しの無い実装計画", "実装計画が無い"])
def test_a_sprint_without_review_units_runs_one_impl_plan_on_the_current_branch(tmp_path, monkeypatch, plan_text):
    repo = _sprint(tmp_path, plan_text)
    sprint_fixture.with_worker(monkeypatch)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    stage = definition.load_definition("standard").stage("実装")
    context = {"issues": [3, 4], "worktree": str(repo), "mode": "standard", "branch": "sprint/registered"}
    run = invoke.stage_run(stage, stage.steps[0], invoke.resolve_inputs(stage, context))
    calls: list[list[str]] = []

    def runner(argv, cwd, env):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result.result("plan", result.OK, "流した")), "")

    work = tmp_path / "w"
    got = invoke.invoke(run, repo, work, runner=runner)

    assert got.status == "ok", got.report
    assert run.name == "impl" and len(calls) == 1
    held = read_values(work)
    assert held is not None and held["values"]["branch"] == "sprint/s1"
    argv = calls[0]
    sets = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "--set")
    # `--set` に載るのは工程の入力だけで、ブランチは埋めたプランが持つ
    assert "branch" not in sets and sets["issues"] == "[3, 4]"
    assert json.loads(Path(argv[argv.index("next") + 1]).read_text())["name"] == "impl-sprint/s1"
