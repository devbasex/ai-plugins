"""最終ゲートとテンプレートの試走の判断。

- #3038: 最終ゲートの直しが、全体テストの失敗を直すためにリファクタリング計画の対象範囲の外を変える
- #2680: 最終ゲートが、衝突して CI の始まらない Pull Request を上限まで待って止まる
- #2158: 着手前のテストが上限の近くで通ると、テンプレートの試走の残りが数秒になり init が止まる
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ndf.adapters import ci
from ndf.contracts import causes
from ndf.domains.crossrefactor import final_gate, final_gate_decide, probe_decide

from failed_hub import FakeHub, art, xml, zip_of


def test_fix_that_changes_a_file_outside_the_plan_is_not_taken():
    # volareinc/carte の PR #81 の形: 範囲は backend なのに、直しが frontend/vite.config.ts を変えた
    got = final_gate_decide.verify_final_fix(
        {"a1": ["backend/app/models.py"], "b2": ["backend/app/views.py", "frontend/vite.config.ts"]},
        ["backend/app"],
    )
    assert not got.ok
    assert got.accepted == ("a1",)
    assert got.outside == (("b2", "frontend/vite.config.ts"),)
    assert got.record(["tests/test_ui.py::test_build"]) == {
        "failed_tests": ["tests/test_ui.py::test_build"],
        "outside_files": ["frontend/vite.config.ts"],
        "commits": ["b2"],
    }


def test_fix_inside_the_plan_is_taken():
    got = final_gate_decide.verify_final_fix({"a1": ["backend/app/models.py"]}, ["backend/app"])
    assert got.ok and got.accepted == ("a1",)


def test_new_file_outside_the_plan_counts_as_outside():
    got = final_gate_decide.verify_final_fix({"c3": ["scripts/new_helper.py"]}, ["backend"])
    assert got.outside == (("c3", "scripts/new_helper.py"),)


def test_missing_target_scope_is_an_error_not_a_pass():
    with pytest.raises(ValueError, match="target_scope"):
        final_gate_decide.verify_final_fix({"a1": ["x.py"]}, [])


def test_stop_causes_route_outside_fixes_to_a_human_and_conflicts_to_the_base_merge():
    outside = final_gate_decide.verify_final_fix({"b2": ["frontend/vite.config.ts"]}, ["backend"]).cause()
    assert causes.row(outside["kind"]).route == "human" and "frontend/vite.config.ts" in outside["detail"]
    assert final_gate_decide.before_ci_wait("CONFLICTING", "DIRTY").cause()["kind"] == "base_conflict"
    assert final_gate_decide.before_ci_wait("MERGEABLE", "CLEAN").cause() is None


@pytest.mark.parametrize(
    ("mergeable", "state", "kind"),
    [
        ("CONFLICTING", "DIRTY", "conflict"),
        ("MERGEABLE", "DIRTY", "conflict"),
        ("UNKNOWN", "UNKNOWN", "recheck"),
        (None, None, "recheck"),
        ("MERGEABLE", "BLOCKED", "wait"),
        ("MERGEABLE", "CLEAN", "wait"),
    ],
)
def test_conflicting_pull_request_is_not_waited_for(mergeable, state, kind):
    # PR #2676・#2979 の形: CONFLICTING・DIRTY の PR は CI が 1 本も始まらない
    assert final_gate_decide.before_ci_wait(mergeable, state).kind == kind


def test_probe_budget_keeps_the_floor_when_the_baseline_used_almost_everything():
    assert probe_decide.probe_budget(540, 100, 120) == 440
    assert probe_decide.probe_budget(540, 533.9, 120) == 120


def test_probe_floor_comes_from_the_limits_declaration():
    assert probe_decide.probe_floor() == 120


# --- 最後の確かめが落ちたテストを読む（#3616） ---

FAILED = ci.Reading([ci.Check("pytest", "done", "failure", 1)], [], [], [], [], [], True)
PASSED = ci.Reading([], [], [], [], [], [], True)


class _Git:
    """最後の確かめが読む git（直しのコミットは無い）。`cwd` は設定ファイルを置いた作業場所。"""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def rev_parse(self, ref: str) -> str:
        return "a" * 40

    def run(self, *args: str) -> str:
        return ""


def _artifact_hub(**kw: Any) -> FakeHub:
    """ジョブ 1 は実行 9（pytest.yml）で、成果物 junit-0 に落ちたテストが 1 件ある偽の GitHub。"""
    return _NoLog(
        {1: 9},
        {9: ".github/workflows/pytest.yml"},
        artifacts={9: [art(31)]},
        zips={31: zip_of(xml(("tests/test_a.py", "t")))},
        **kw,
    )


class _NoLog(FakeHub):
    def job_log(self, job_id: int) -> str:
        raise AssertionError("成果物から読めたのにログを読んだ")


def test_final_gate_passes_the_work_declaration_to_the_failed_test_assembly(tmp_path, monkeypatch):
    decl = {"test": {"ci": {"junit_artifacts": "junit-*"}, "suites": [{"name": "pytest", "runner": "pytest", "command": "pytest"}]}}
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    monkeypatch.setattr(final_gate, "send", lambda g, branch: "b" * 40)
    monkeypatch.setattr(final_gate, "ensure_mergeable", lambda hub, pr, sleep: None)
    results = [ci.WatchResult(FAILED, 1, 0.0, 0.0), ci.WatchResult(PASSED, 1, 0.0, 0.0)]
    seen: list[Any] = []

    def failed_tests(hub: Any, watched: ci.WatchResult, source: ci.FailedTestSource) -> list[str]:
        seen.append(source)
        # 追跡するファイルは git の無い作業場所では空なので、照らす先を足して既定の組み立てへ渡す
        return final_gate._failed_tests(
            hub, watched, ci.FailedTestSource(source.junit_artifacts, source.suites, frozenset({"tests/test_a.py"}))
        )

    fixed: list[Any] = []
    port = final_gate.CiPort(lambda hub, sha, cfg: results.pop(0), failed_tests)
    cfg = final_gate.GateConfig(7, "b", ("pytest",), ("pytest",), ("tests",), 1)
    got = final_gate.run_gate(_Git(tmp_path), _artifact_hub(), cfg, lambda failed, n: fixed.append(failed) or True, lambda s: None, port)  # type: ignore[arg-type]
    assert got["status"] == "passed"
    assert seen[0].junit_artifacts == "junit-*" and [s["name"] for s in seen[0].suites] == ["pytest"]
    assert fixed == [["tests/test_a.py::t"]]


def test_final_gate_default_reader_returns_empty_when_nothing_is_read():
    watched = ci.WatchResult(FAILED, 1, 0.0, 0.0)
    assert final_gate._failed_tests(_artifact_hub(broken_jobs=frozenset({1})), watched, ci.FailedTestSource()) == []  # type: ignore[arg-type]
