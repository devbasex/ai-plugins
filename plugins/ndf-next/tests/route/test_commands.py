"""雛形のステップのコマンドを偽の gh・ndf と本物の git で打ち、プランの実行と同じ読み方で止まった理由を確かめる。"""

from __future__ import annotations

import json
import shlex

from ._flow import fake_bin, plan_of, repo_with_base, run_step, step_of


def test_conflict_check_reports_dirty_pr_as_base_conflict(tmp_path):
    """#2752・#2680・#2273: 宛先と衝突した PR（mergeStateStatus が DIRTY）は待たずに base_conflict で返す。"""
    step = step_of(plan_of("implement", branch="feat/x"), "conflict-check")
    dirty = fake_bin(tmp_path, "gh", "echo DIRTY")
    assert run_step(step, tmp_path, dirty)["kind"] == "base_conflict"
    clean = fake_bin(tmp_path, "gh", "echo CLEAN")
    assert run_step(step, tmp_path, clean)["status"] == "ok"


def test_done_check_skips_merged_branch(tmp_path):
    """#2349: ブランチの PR が取り込み済み（MERGED）なら not_applicable で返し、プランを流し直さない。"""
    step = step_of(plan_of("implement", branch="feat/x"), "done-check")
    assert run_step(step, tmp_path, fake_bin(tmp_path, "gh", "echo MERGED"))["kind"] == "not_applicable"
    assert run_step(step, tmp_path, fake_bin(tmp_path, "gh", "echo OPEN"))["status"] == "ok"


def test_changes_check_finds_no_changes(tmp_path):
    """#3039: 宛先から変更の無いブランチは not_applicable（PR を作らない）。変更があれば ok。"""
    step = step_of(plan_of("close", base="develop"), "changes-check")
    same = repo_with_base(tmp_path / "a", {})
    assert run_step(step, same)["kind"] == "not_applicable"
    changed = repo_with_base(tmp_path / "b", {"x.py": "x = 1\n"})
    assert run_step(step, changed)["status"] == "ok"


def test_assess_detects_docs_only_diff(tmp_path):
    """#3023: 差分が文書（.md）だけなら not_applicable（全体テストを飛ばす）。コードを含めば ok。"""
    step = step_of(plan_of("check", base="develop"), "assess")
    docs = repo_with_base(tmp_path / "a", {"guide.md": "# g\n"})
    assert run_step(step, docs)["kind"] == "not_applicable"
    code = repo_with_base(tmp_path / "b", {"guide.md": "# g\n", "a.py": "a = 1\n"})
    assert run_step(step, code)["status"] == "ok"


def _ci_result(path, nodes):
    found = {"tool": "ci", "status": "stopped", "summary": "落ちた", "items": [], "metrics": {"failed_tests": nodes}}
    path.write_text("CI を待っている\n" + json.dumps(found) + "\n", encoding="utf-8")


def test_ci_local_runs_only_failed_tests_and_reports_failure(tmp_path):
    """#3188: CI の修正の後に、CI で落ちたテストの node id だけを手元のテストのコマンドへ足して打つ。落ちれば test_failed。"""
    state = tmp_path / "state"
    state.mkdir()
    _ci_result(state / "ci.json", ["tests/a.py::t1", "tests/b.py::t2"])
    seen = tmp_path / "seen.txt"
    step = step_of(plan_of("implement", test_command="pytest -q"), "ci-local")
    ok_bin = fake_bin(tmp_path, "ndf", f'printf "%s\\n" "$@" > {shlex.quote(str(seen))}')
    assert run_step(step, tmp_path, ok_bin, {"state_dir": str(state)})["status"] == "ok"
    assert seen.read_text().splitlines() == ["test-slot", "run", "--", "pytest -q tests/a.py::t1 tests/b.py::t2"]
    failing = fake_bin(tmp_path, "ndf", "exit 1")
    assert run_step(step, tmp_path, failing, {"state_dir": str(state)})["kind"] == "test_failed"


def test_ci_local_without_nodes_runs_whole_local_command(tmp_path):
    """#3188: 落ちたテストを読めなかったときは、手元のテストのコマンドを丸ごと打ってから push する。"""
    state = tmp_path / "state"
    state.mkdir()
    _ci_result(state / "ci.json", [])
    seen = tmp_path / "seen.txt"
    step = step_of(plan_of("implement", test_command="pytest -q"), "ci-local")
    ok_bin = fake_bin(tmp_path, "ndf", f'printf "%s\\n" "$@" > {shlex.quote(str(seen))}')
    run_step(step, tmp_path, ok_bin, {"state_dir": str(state)})
    assert seen.read_text().splitlines()[-1] == "pytest -q"
