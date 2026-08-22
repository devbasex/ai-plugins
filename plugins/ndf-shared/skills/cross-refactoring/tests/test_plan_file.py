"""改修計画をリポジトリ内のファイルへ残すテスト。

提案の理由と手順は状態ファイルにしか残らず、そのディレクトリは差分から除外される。
**Pull Request を読む側からは、なぜ直したのかも、どう直す計画だったのかも見えない。**
計画を差分の中へ置き、公開は生成物の同期と同じ経路（進行側の 1 コミット）に乗せる。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import make_state, read_state

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が必要")


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def _commit(repo, message):
    _git("add", "-A", cwd=repo)
    _git("-c", "user.email=t@e.st", "-c", "user.name=test",
         "commit", "-qm", message, cwd=repo)


def _make_work(tmp_path):
    work = tmp_path / "work"
    (work / "generated").mkdir(parents=True)
    _git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    (work / "src.py").write_text("x = 1\n", encoding="utf-8")
    (work / "generated" / "out.py").write_text("x = 1\n", encoding="utf-8")
    _commit(work, "init")
    return work


def _item(**over):
    base = {
        "item_id": "R1-001", "round": 1, "path": "src/foo.py", "symbol": "Foo.handle",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "rationale": "1 関数が 6 段の処理を通しで行っている",
        "plan": "1. 範囲の確定を切り出す 2. 検証を切り出す",
        "test_gap": False, "estimated_diff_lines": 40,
        "proposed_by": ["codex", "gemini"], "status": "done", "commits": ["abc1234"],
    }
    base.update(over)
    return base


def _state(tmp_path, work=None, **over):
    rounds = over.pop("rounds", [{
        "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "items": ["R1-001"], "reviews": [], "fix_rounds": 0,
    }])
    items = over.pop("items", [_item()])
    worktrees = {"work": str(work or tmp_path / "work")}
    for r in ("codex", "gemini", "kiro"):
        worktrees[r] = str(tmp_path / r)
    path = make_state(tmp_path, rounds=rounds, items=items,
                      worktrees=worktrees, **over)
    return path, read_state(path)


# ---------- 計画の本文 ----------

def test_plan_names_the_item_and_the_target(refactor, tmp_path):
    _, state = _state(tmp_path)
    text = refactor.format_plan(state)
    assert "R1-001" in text and "src/foo.py" in text and "Foo.handle" in text


def test_plan_carries_the_reason_and_the_steps(refactor, tmp_path):
    """なぜ直すのか・どう直すのかは、提案の時点でしか残らない。"""
    _, state = _state(tmp_path)
    text = refactor.format_plan(state)
    assert "1 関数が 6 段の処理を通しで行っている" in text
    assert "1. 範囲の確定を切り出す" in text


def test_plan_shows_the_smell_and_the_technique(refactor, tmp_path):
    _, state = _state(tmp_path)
    text = refactor.format_plan(state)
    assert "long_method" in text and "extract_method" in text


def test_plan_records_who_proposed_it(refactor, tmp_path):
    _, state = _state(tmp_path)
    assert "codex" in refactor.format_plan(state)


def test_plan_marks_an_abandoned_item(refactor, tmp_path):
    """取り消した項目も残す。同じ提案が再び来たときの判断材料になる。"""
    _, state = _state(tmp_path, items=[_item(status="abandoned", commits=[])])
    text = refactor.format_plan(state)
    assert "取り消し" in text


def test_plan_groups_items_by_round(refactor, tmp_path):
    rounds = [
        {"round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
         "items": ["R1-001"], "reviews": [], "fix_rounds": 0},
        {"round": 2, "impl": "kiro", "reviewers": ["codex", "gemini"],
         "items": ["R2-001"], "reviews": [], "fix_rounds": 0},
    ]
    items = [_item(), _item(item_id="R2-001", round=2)]
    _, state = _state(tmp_path, rounds=rounds, items=items)
    text = refactor.format_plan(state)
    assert text.index("ラウンド 1") < text.index("ラウンド 2")


def test_plan_names_the_implementer_of_each_round(refactor, tmp_path):
    _, state = _state(tmp_path)
    assert "codex" in refactor.format_plan(state)


def test_plan_is_stable_for_the_same_state(refactor, tmp_path):
    """同じ状態からは同じ本文が出る。差分が出続けると毎回コミットが積まれる。"""
    _, state = _state(tmp_path)
    assert refactor.format_plan(state) == refactor.format_plan(state)


# ---------- 置き場所 ----------

def test_the_default_plan_file_lives_under_issues(refactor):
    assert refactor.default_plan_file(136) == "issues/refactoring-plan-rf136.md"


def test_the_plan_file_is_written_inside_the_work_dir(refactor, tmp_path):
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md")

    refactor._write_plan_file(state, str(work), "issues/plan.md")

    written = (work / "issues" / "plan.md").read_text(encoding="utf-8")
    assert "R1-001" in written


# ---------- 公開 ----------

def test_the_plan_lands_in_one_commit_with_the_generated_files(refactor, tmp_path):
    """計画書と生成物で 2 コミットに分けない。"""
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md",
                      sync_command="printf 'x = 2\\n' > generated/out.py")

    refactor._sync_generated(state)

    subject = _git("log", "-1", "--format=%s", cwd=work).stdout.strip()
    files = _git("show", "--name-only", "--format=", "HEAD", cwd=work).stdout.split()
    assert "issues/plan.md" in files and "generated/out.py" in files
    assert subject and "cross-refactoring" in subject


def test_a_repository_without_a_sync_command_still_records_the_plan(
    refactor, tmp_path
):
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md",
                      sync_command=None)

    refactor._sync_generated(state)

    files = _git("show", "--name-only", "--format=", "HEAD", cwd=work).stdout.split()
    assert "issues/plan.md" in files


def test_an_unchanged_plan_does_not_add_a_commit(refactor, tmp_path):
    """状態が動いていないのにコミットを積まない。"""
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md",
                      sync_command=None)
    refactor._sync_generated(state)
    before = _git("rev-parse", "HEAD", cwd=work).stdout.strip()

    refactor._sync_generated(state)

    assert _git("rev-parse", "HEAD", cwd=work).stdout.strip() == before


def test_an_empty_plan_file_setting_turns_the_record_off(refactor, tmp_path):
    """計画を差分へ入れたくないリポジトリのために、無効にできる。"""
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="", sync_command=None)

    refactor._sync_generated(state)

    assert not (work / "issues").exists()
