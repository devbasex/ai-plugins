"""生成物の同期と、実装担当が残した未コミット変更の扱いを**実際の git** で確かめる。

同期は `--sync-command` を持つリポジトリで push の直前に走る、進行側の責務である。
ここが落ちると取り消しを Pull Request へ反映できないため、進行そのものが止まる。

| 確かめること | なぜ |
| --- | --- |
| 変更のパスを 1 文字も欠かさず拾う | `git status --porcelain` は固定幅。先頭の空白を削ると 1 行目がずれる |
| 同期の後段で落ちても差分を残さない | 残すと次の実行が清浄性の検査で必ず止まる |
| 実装担当の置き土産を捨ててから取り込む | 検証を受けていない変更なので公開しない。止まる理由にもしない |
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import make_state, read_state, write_result

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が必要")


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def _commit(repo, message):
    _git("add", "-A", cwd=repo)
    _git("-c", "user.email=t@e.st", "-c", "user.name=test",
         "commit", "-qm", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def _make_work(tmp_path):
    """`work` を本物のリポジトリとして作り、状態ファイルを添えて返す。"""
    work = tmp_path / "work"
    (work / "generated").mkdir(parents=True)
    _git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    (work / "src.py").write_text("x = 1\n", encoding="utf-8")
    (work / "generated" / "out.py").write_text("x = 1\n", encoding="utf-8")
    _commit(work, "init")
    return work


def _state_with_sync(tmp_path, work, command="true"):
    return make_state(
        tmp_path,
        worktrees={"work": str(work), "codex": str(tmp_path / "codex"),
                   "gemini": str(tmp_path / "gemini"), "kiro": str(tmp_path / "kiro")},
        sync_command=command,
    )


# ---------- 変更のパスを 1 文字も欠かさず拾う ----------

def test_unstaged_change_on_first_line_keeps_full_path(refactor, tmp_path):
    """先頭が空白の状態コード（` M`）でも、パスの先頭文字が消えない。

    `git status --porcelain` は「状態 2 文字 + 空白 + パス」の固定幅で、
    未 stage の変更は 1 文字目が空白になる。出力全体を `strip()` してから
    固定幅で切り出すと、**1 行目だけ**パスが 1 文字短くなる。
    """
    work = _make_work(tmp_path)
    (work / "src.py").write_text("x = 2\n", encoding="utf-8")

    changes = refactor._worktree_changes(str(work))

    assert "src.py" in changes


def test_every_changed_path_is_addable(refactor, tmp_path):
    """拾ったパスは、そのまま `git add` に渡して通る。"""
    work = _make_work(tmp_path)
    (work / "src.py").write_text("x = 2\n", encoding="utf-8")
    (work / "generated" / "out.py").write_text("x = 2\n", encoding="utf-8")
    state = read_state(_state_with_sync(tmp_path, work))

    paths = refactor._dirty_paths(state, str(work))

    assert paths == ["generated/out.py", "src.py"]
    _git("add", "--", *paths, cwd=work)


# ---------- 同期コミット ----------

def test_sync_commits_generated_changes(refactor, tmp_path):
    """同期コマンドが作った差分は、進行側のコミットとして積まれる。"""
    work = _make_work(tmp_path)
    state = read_state(_state_with_sync(
        tmp_path, work, command="printf 'x = 2\\n' > generated/out.py"))

    refactor._sync_generated(state)

    assert _git("status", "--porcelain", cwd=work).stdout == ""
    subject = _git("log", "-1", "--format=%s", cwd=work).stdout.strip()
    assert subject == refactor.SYNC_COMMIT_MESSAGE.splitlines()[0]


def test_sync_without_changes_makes_no_commit(refactor, tmp_path):
    """差分が出ない同期はコミットを作らない。"""
    work = _make_work(tmp_path)
    before = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    state = read_state(_state_with_sync(tmp_path, work, command="true"))

    refactor._sync_generated(state)

    assert _git("rev-parse", "HEAD", cwd=work).stdout.strip() == before


# ---------- 同期の後段で落ちたとき ----------

def test_failure_after_sync_discards_produced_changes(refactor, tmp_path, monkeypatch):
    """`git add` / `git commit` が落ちても、同期が作った差分を残さない。

    残すと次の実行は清浄性の検査で必ず止まり、保留中の push を再試行できない。
    """
    work = _make_work(tmp_path)
    state = read_state(_state_with_sync(
        tmp_path, work, command="printf 'x = 2\\n' > generated/out.py"))
    monkeypatch.setattr(refactor, "_sh",
                        lambda *a, **k: refactor.die("commit に失敗しました"))

    with pytest.raises(SystemExit):
        refactor._sync_generated(state)

    assert _git("status", "--porcelain", cwd=work).stdout == ""


def test_failed_sync_command_discards_partial_changes(refactor, tmp_path):
    """同期コマンド自身が落ちたときも、途中まで書き換えた差分を残さない。"""
    work = _make_work(tmp_path)
    state = read_state(_state_with_sync(
        tmp_path, work, command="printf 'x = 2\\n' > generated/out.py; exit 1"))

    with pytest.raises(SystemExit):
        refactor._sync_generated(state)

    assert _git("status", "--porcelain", cwd=work).stdout == ""


# ---------- 実装担当が残した未コミット変更 ----------

def test_leftover_changes_are_discarded_before_merge(refactor, tmp_path):
    """実装担当が残した未コミット変更は、取り込みの前に捨てる。

    公開は進行側が検証を通してから行うので、コミットされなかった変更は
    **検証を受けていない**。残したまま進むと、清浄性の検査で進行が止まる。
    """
    work = _make_work(tmp_path)
    (work / "src.py").write_text("直しかけ\n", encoding="utf-8")
    state = read_state(_state_with_sync(tmp_path, work))

    refactor._discard_impl_leftovers(state, str(work))

    assert _git("status", "--porcelain", cwd=work).stdout == ""
    assert (work / "src.py").read_text(encoding="utf-8") == "x = 1\n"


def test_discard_keeps_control_directory(refactor, tmp_path):
    """制御用ディレクトリ（状態・結果・ログ）は捨てない。"""
    work = _make_work(tmp_path)
    control = work / ".cross_refactoring"
    control.mkdir()
    (control / "keep.json").write_text("{}", encoding="utf-8")
    (work / ".gitignore").write_text(".cross_refactoring/\n", encoding="utf-8")
    _commit(work, "ignore control dir")
    (work / "src.py").write_text("直しかけ\n", encoding="utf-8")
    state = read_state(make_state(
        tmp_path,
        worktrees={"work": str(work)},
        tmp_dir=str(control),
    ))

    refactor._discard_impl_leftovers(state, str(work))

    assert (control / "keep.json").exists()
    assert _git("status", "--porcelain", cwd=work).stdout == ""


def test_merge_fix_continues_when_impl_left_changes(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """修正フェーズの置き土産があっても、`merge-fix` は中断しない。

    実装担当がコミットを作れずに終えると作業ツリーへ差分が残る。これを理由に
    止めると、修正 0 件として先へ進むこともできなくなる。
    """
    work = _make_work(tmp_path)
    head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    state_path = make_state(
        tmp_path,
        worktrees={"work": str(work), "codex": str(tmp_path / "codex"),
                   "gemini": str(tmp_path / "gemini"), "kiro": str(tmp_path / "kiro")},
        rounds=[{
            "round": 1, "impl": "codex", "impl_model": None,
            "reviewers": ["gemini", "kiro"], "reviewer_models": {},
            "items": ["R1-001"], "adopted": 1, "proposed": 1, "merged": 1,
            "apply": {"merged_at": "2026-08-18T00:00:00", "applied": ["R1-001"],
                      "failed": []},
            "apply_base_sha": head, "apply_progress": [], "drops": [],
            "reviews": [], "fix_rounds": 0, "fix_attempts": 1,
            "fix_base_sha": head, "deferred": [], "durations": {},
            "proposal_keys": [], "pending_drop": [], "pending_push": False,
            "started_at": "2026-08-18T00:00:00",
        }],
        items=[{"item_id": "R1-001", "round": 1, "path": "src.py",
                "symbol": "f", "smell": "long_method", "technique": "extract_method",
                "severity": "major", "rationale": "", "plan": "", "test_gap": False,
                "estimated_diff_lines": 10, "proposed_by": ["codex"],
                "status": "applied", "commits": []}],
    )
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_push_head", lambda state: None)
    write_result(state_path, "codex-fix-r1",
                 {"resolved_thread_ids": [], "unresolved": [], "commits": []})
    (work / "src.py").write_text("直しかけ\n", encoding="utf-8")

    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    assert _git("status", "--porcelain", cwd=work).stdout == ""
    assert read_state(state_path)["rounds"][0]["fix_rounds"] == 1
