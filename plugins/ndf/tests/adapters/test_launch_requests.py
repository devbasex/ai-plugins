"""#1959: 承認のリクエストと回答、設定の候補と反映（置き換えの手順・退避の片付け・反映の完了の記録）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ndf.adapters import launch_approval as la

SHA = "a" * 64
OLD = b'{"version": 1}\n'
NEW = b'{"version": 1, "permissions": {"steps": {"*": {"load": ["LSP"]}}}}\n'


def _worktree(tmp_path: Path, content: bytes | None = OLD) -> Path:
    target = tmp_path / "wt" / ".ndf" / "launch.json"
    target.parent.mkdir(parents=True)
    if content is not None:
        target.write_bytes(content)
    return target


def _question(request_id: str) -> dict:
    return {
        "questions": [{"question": f"承認するか（リクエスト {request_id}）", "header": "起動の承認", "options": [], "multiSelect": False}]
    }


def _request(request_id: str, target: Path) -> dict:
    return {"request_id": request_id, "repo": "/r", "sha256": SHA, "target": str(target), "session": "s1"}


def _answer(request_id: str, label: str) -> None:
    """hook `launch-answer` が書く行（リクエストの識別子・回答・質問の id）。"""
    la.write_row(
        la.state_file(la.ANSWERS), {"at": "2026-11-26T00:00:00+00:00", "request_id": request_id, "label": label, "question_id": "toolu_1"}
    )


def _asked(target: Path, request_id: str = "q1", candidate: Path | None = None, label: str | None = la.APPROVE_LABEL.format("人")) -> None:
    la.put_question(_request(request_id, target), _question(request_id), candidate)
    if label is not None:
        _answer(request_id, label)


def _approvals() -> list[dict]:
    path = la.state_file(la.APPROVALS)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _candidate(target: Path, content: bytes = NEW) -> Path:
    candidate = la.new_candidate("/r", target)
    candidate.write_bytes(content)
    return candidate


def test_an_approved_answer_adds_one_row_and_the_retry_reuses_it(tmp_path):
    target = _worktree(tmp_path)
    _asked(target)
    with la.records_lock(la.state_file(la.APPROVALS)):
        assert la.approve_by_answer("/r", SHA, "q1") == "人"
        assert la.approve_by_answer("/r", SHA, "q1") == "人"
    assert [(r["action"], r["approver"], r["request_id"]) for r in _approvals()] == [("approve", "人", "q1")]
    assert la.is_approved("/r", SHA)


@pytest.mark.parametrize("case", ["unanswered", "denied", "other_repo", "other_hash", "newer", "target_changed", "revoked"])
def test_an_answer_that_cannot_be_used_adds_no_row(tmp_path, case):
    target = _worktree(tmp_path)
    _asked(target, label={"unanswered": None, "denied": la.DENY_LABEL}.get(case, la.APPROVE_LABEL.format("人")))
    repo, sha = ("/other" if case == "other_repo" else "/r"), ("b" * 64 if case == "other_hash" else SHA)
    if case == "newer":
        _asked(target, "q2", label=None)
    if case == "target_changed":
        target.write_bytes(b'{"version": 1, "x": 1}\n')
    if case == "revoked":
        la.append("/r", SHA, la.Action.REVOKE)
    with la.records_lock(la.state_file(la.APPROVALS)):
        assert la.approve_by_answer(repo, sha, "q1") is None
    assert [r for r in _approvals() if r["action"] == "approve"] == []


def test_an_answer_long_after_the_request_is_still_pending_until_reflected(tmp_path):
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    la.put_question(_request("q1", target), _question("q1"), candidate)
    assert la.pending_request("/r", target) is None
    _answer("q1", la.APPROVE_LABEL.format("人"))
    assert la.pending_request("/r", target)["candidate"] == str(candidate)

    def record() -> None:
        assert la.approve_by_answer("/r", SHA, "q1") == "人"

    assert la.reflect(candidate, record, SHA, "q1")
    assert la.pending_request("/r", target) is None


def test_two_candidates_are_separate_files_and_record_the_target_and_base(tmp_path):
    target = _worktree(tmp_path)
    first, second = la.new_candidate("/r", target), la.new_candidate("/r", target)
    assert first != second and first.read_bytes() == second.read_bytes() == OLD
    assert json.loads((first.parent / la.SOURCE_NAME).read_text()) == {"target": str(target), "base": la.content_version(target)}
    missing = _worktree(tmp_path / "x", content=None)
    assert json.loads(la.new_candidate("/r", missing).read_text()) == {"version": 1}


def test_draft_sweeps_candidates_of_a_removed_worktree_and_of_a_denied_request(tmp_path):
    gone, kept = _worktree(tmp_path / "a"), _worktree(tmp_path / "b")
    stale, denied = la.new_candidate("/r", gone), la.new_candidate("/r", kept)
    _asked(kept, candidate=denied, label=la.DENY_LABEL)
    (gone.parent / "launch.json").unlink()
    gone.parent.rmdir()
    gone.parents[1].rmdir()
    fresh = la.new_candidate("/r", kept)
    assert not stale.exists() and not denied.exists() and fresh.exists()


def test_reflect_replaces_the_target_once_and_the_retry_succeeds_without_writing(tmp_path):
    target = _worktree(tmp_path)
    candidate, other = _candidate(target), _candidate(target, b"{}\n")
    calls: list[int] = []
    assert la.reflect(candidate, lambda: calls.append(1), SHA)
    assert target.read_bytes() == NEW and calls == [1]
    assert not candidate.exists() and not other.exists()  # 反映元のバージョンが合わなくなった候補も消す
    assert not target.with_name("launch.json.reflecting").exists()
    assert la.reflected(candidate, SHA, None)
    target.write_bytes(OLD)
    assert not la.reflected(candidate, SHA, None)


def test_reflect_into_a_missing_target(tmp_path):
    target = _worktree(tmp_path, content=None)
    assert la.reflect(_candidate(target), lambda: None, SHA)
    assert target.read_bytes() == NEW


def test_a_target_changed_after_the_draft_is_not_overwritten(tmp_path):
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    target.write_bytes(b'{"version": 1, "mine": true}\n')
    assert not la.reflect(candidate, lambda: pytest.fail("記録しない"), SHA)
    assert target.read_bytes() == b'{"version": 1, "mine": true}\n' and candidate.exists()


def test_a_save_between_the_check_and_the_rename_is_kept(tmp_path, monkeypatch):
    """照らした後、置き換えの前に保存されたら、保存した中身が残り、置かない。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    rename = os.rename

    def saved_first(src, dst):
        if str(dst).endswith(la.REFLECTING):
            Path(src).write_bytes(b'{"version": 1, "saved": 1}\n')
        rename(src, dst)

    monkeypatch.setattr(la.os, "rename", saved_first)
    assert not la.reflect(candidate, lambda: None, SHA)
    assert target.read_bytes() == b'{"version": 1, "saved": 1}\n'
    assert not target.with_name("launch.json.reflecting").exists()


def test_a_save_into_the_aside_after_placing_is_restored(tmp_path, monkeypatch):
    """候補を置いた後、退避の側へエディタが書けば、その中身を反映先へ戻して止まる。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    link = os.link

    def editor_writes(src, dst):
        link(src, dst)
        Path(str(dst) + la.REFLECTING).write_bytes(b'{"version": 1, "editor": 1}\n')

    monkeypatch.setattr(la.os, "link", editor_writes)
    assert not la.reflect(candidate, lambda: None, SHA)
    assert target.read_bytes() == b'{"version": 1, "editor": 1}\n'


def _interrupted(tmp_path: Path) -> tuple[Path, Path, Path]:
    """候補を置いた直後（退避を消す前）で中断した状態。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    aside = target.with_name("launch.json.reflecting")
    pending = {
        "candidate": candidate.parent.name,
        "base": la.content_version(target),
        "content": la.content_version(candidate),
        "sha256": SHA,
        "request_id": None,
    }
    la.write_launch_record(la.reflected_file(target, la.PENDING), pending)
    os.rename(target, aside)
    target.write_bytes(NEW)
    return target, candidate, aside


def test_after_an_interruption_right_after_placing_the_retry_succeeds(tmp_path):
    target, candidate, aside = _interrupted(tmp_path)
    assert la.reflect(candidate, lambda: pytest.fail("反映は済んでいる"), SHA)
    assert target.read_bytes() == NEW and not aside.exists()


def test_after_an_interruption_a_save_into_the_aside_is_put_back(tmp_path):
    target, candidate, aside = _interrupted(tmp_path)
    aside.write_bytes(b'{"version": 1, "saved": 1}\n')
    assert not la.reflect(candidate, lambda: None, SHA)
    assert target.read_bytes() == b'{"version": 1, "saved": 1}\n' and not aside.exists()


def test_recover_keeps_both_when_target_and_aside_hold_the_users_contents(tmp_path):
    target, _, aside = _interrupted(tmp_path)
    target.write_bytes(b'{"version": 1, "a": 1}\n')
    aside.write_bytes(b'{"version": 1, "b": 1}\n')
    saved = la.recover(target)
    assert target.read_bytes() == b'{"version": 1, "a": 1}\n' and saved.read_bytes() == b'{"version": 1, "b": 1}\n'
    assert saved.name.startswith("launch.json.saved-") and not aside.exists()


@pytest.mark.parametrize("left", ["aside", "pending"])
def test_recover_with_only_the_aside_or_only_the_pending_record(tmp_path, left):
    target, _, aside = _interrupted(tmp_path)
    target.unlink()
    if left == "pending":
        aside.unlink()
    assert la.recover(target) is None
    assert (target.read_bytes() == OLD) if left == "aside" else not target.exists()
    assert not la.reflected_file(target, la.PENDING).exists()
