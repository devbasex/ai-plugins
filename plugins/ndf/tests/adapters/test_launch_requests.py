"""#1959: 承認のリクエストと回答、設定の候補と反映（置き換えの手順・退避の片付け・反映の完了の記録）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ndf.adapters import launch_approval as la, launch_candidates as lc
from ndf.contracts import internal

SHA = "a" * 64
OLD = b'{"version": 1}\n'
NEW = b'{"version": 1, "permissions": {"steps": {"*": {"load": ["LSP"]}}}}\n'


def _worktree(tmp_path: Path, content: bytes | None = OLD) -> Path:
    target = tmp_path / "wt" / ".ndf" / "launch.json"
    target.parent.mkdir(parents=True)
    if content is not None:
        target.write_bytes(content)
    return target


OPTIONS = [{"label": la.APPROVE_LABEL.format("人"), "description": ""}, {"label": la.DENY_LABEL, "description": ""}]


def _question(request_id: str) -> dict:
    return {
        "questions": [
            {"question": f"承認するか（リクエスト {request_id}）", "header": "起動の承認", "options": OPTIONS, "multiSelect": False}
        ]
    }


def _request(request_id: str, target: Path) -> dict:
    return {"request_id": request_id, "repo": "/r", "sha256": SHA, "target": str(target), "session": "s1"}


def _answer(request_id: str, label: str) -> None:
    """hook `launch-answer` が書く行（リクエストの識別子・回答・質問の id）。"""
    la.write_row(
        la.state_file(la.ANSWERS), {"at": "2026-11-26T00:00:00+00:00", "request_id": request_id, "label": label, "question_id": "toolu_1"}
    )


def _asked(target: Path, request_id: str = "q1", candidate: Path | None = None, label: str | None = la.APPROVE_LABEL.format("人")) -> None:
    la.put_question(_request(request_id, target), lambda: _question(request_id), candidate)
    if label is not None:
        _answer(request_id, label)


def _reflect(candidate: Path, record, sha256: str, request_id: str | None = None, kept: list[Path] | None = None) -> la.Reflection:
    """CLI と同じく、候補の今の中身と、候補の記録の反映先を照らしたものとして渡す。別の名前で残した保存は `kept` に入る。"""
    expected = Path(la.candidate_destination(candidate))
    kept = [] if kept is None else kept
    return la.reflect(candidate, record, (sha256, request_id), data=candidate.read_bytes(), expected=expected, kept=kept)


def _approvals() -> list[dict]:
    path = la.state_file(la.APPROVALS)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _candidate(target: Path, content: bytes = NEW) -> Path:
    candidate = la.new_candidate("/r", target).candidate
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


@pytest.mark.parametrize(
    "case", ["unanswered", "denied", "unlisted_name", "other_repo", "other_hash", "newer", "target_changed", "revoked"]
)
def test_an_answer_that_cannot_be_used_adds_no_row(tmp_path, case):
    target = _worktree(tmp_path)
    labels = {"unanswered": None, "denied": la.DENY_LABEL, "unlisted_name": la.APPROVE_LABEL.format("別の人")}
    _asked(target, label=labels.get(case, la.APPROVE_LABEL.format("人")))
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


def test_a_revoke_written_before_the_request_does_not_cancel_its_answer(tmp_path):
    """取り消しの直後（同じ秒でも）に作り直したリクエストへの回答は、承認の行を足す。"""
    target = _worktree(tmp_path)
    la.append("/r", SHA, la.Action.REVOKE)
    _asked(target)
    with la.records_lock(la.state_file(la.APPROVALS)):
        assert la.approve_by_answer("/r", SHA, "q1") == "人"


def test_an_answer_long_after_the_request_is_still_pending_until_reflected(tmp_path):
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    la.put_question(_request("q1", target), lambda: _question("q1"), candidate)
    assert la.pending_request("/r", target) is None
    _answer("q1", la.APPROVE_LABEL.format("人"))
    assert la.pending_request("/r", target)["candidate"] == str(candidate)

    def record() -> None:
        assert la.approve_by_answer("/r", SHA, "q1") == "人"

    assert _reflect(candidate, record, SHA, "q1")
    assert la.pending_request("/r", target) is None


def test_two_candidates_are_separate_files_and_record_the_target_and_base(tmp_path):
    target = _worktree(tmp_path)
    first, second = la.new_candidate("/r", target).candidate, la.new_candidate("/r", target).candidate
    assert first != second and first.read_bytes() == second.read_bytes() == OLD
    source = {"target": str(target), "base": la.content_version(target), "written": la.content_version(target)}
    assert json.loads((first.parent / lc.SOURCE_NAME).read_text()) == source
    assert [p.name for p in first.parents[1].iterdir() if p.name.startswith(lc.DRAFTING)] == []
    missing = _worktree(tmp_path / "x", content=None)
    assert json.loads(la.new_candidate("/r", missing).candidate.read_text()) == {"version": 1}


def test_draft_sweeps_candidates_of_a_removed_worktree_and_of_a_denied_request(tmp_path):
    gone, kept = _worktree(tmp_path / "a"), _worktree(tmp_path / "b")
    stale, denied = la.new_candidate("/r", gone).candidate, la.new_candidate("/r", kept).candidate
    _asked(kept, candidate=denied, label=la.DENY_LABEL)
    (gone.parent / "launch.json").unlink()
    gone.parent.rmdir()
    gone.parents[1].rmdir()
    fresh = la.new_candidate("/r", kept).candidate
    assert not stale.exists() and not denied.exists() and fresh.exists()


def test_draft_keeps_edited_candidates(tmp_path):
    gone, kept = _worktree(tmp_path / "a"), _worktree(tmp_path / "b")
    edited, denied = _candidate(gone), la.new_candidate("/r", kept).candidate
    _asked(kept, candidate=denied, label=la.DENY_LABEL)
    denied.write_bytes(NEW)  # 「承認しない」と回答した後に直した
    (gone.parent / "launch.json").unlink()
    gone.parent.rmdir()
    gone.parents[1].rmdir()
    la.new_candidate("/r", kept).candidate
    assert edited.exists() and denied.exists()


def test_draft_keeps_a_denied_candidate_that_was_asked_again(tmp_path):
    """否認の後に同じ候補で作り直したリクエストが回答待ちなら、否認と同じ中身でも消さない。"""
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    _asked(target, "q2", candidate=candidate, label=None)
    la.new_candidate("/r", target).candidate
    assert candidate.exists()


def _aside(candidate: Path) -> Path:
    return candidate.parents[1] / f"{lc.REMOVING}{candidate.parent.name}" / lc.CANDIDATE_NAME


def test_a_swept_candidate_is_set_aside_and_deleted_by_the_next_sweep_only_if_still_untouched(tmp_path):
    """掃除は候補をその場で消さずに退避する。次の掃除で、直されていなければ消し、直されていれば元へ戻す。"""
    target = _worktree(tmp_path)
    kept, gone = la.new_candidate("/r", target).candidate, la.new_candidate("/r", target).candidate
    _asked(target, candidate=kept, label=la.DENY_LABEL)
    _asked(target, "q2", candidate=gone, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    assert _aside(kept).exists() and _aside(gone).exists()
    _aside(kept).write_bytes(NEW)  # 退避の後も開いていたファイルへの保存
    la.new_candidate("/r", target).candidate
    assert kept.read_bytes() == NEW and not _aside(gone).parent.exists() and not gone.exists()


def test_asking_again_with_a_set_aside_candidate_puts_it_back(tmp_path):
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    assert not candidate.exists()
    _asked(target, "q2", candidate=candidate, label=None)
    assert candidate.exists() and not (candidate.parent / lc.DISCARD_NAME).exists()
    la.new_candidate("/r", target).candidate
    assert candidate.exists()


def test_a_denied_candidate_edited_before_the_request_is_kept(tmp_path):
    """「承認しない」は反映を断る回答で、リクエストの前に書いた設定案を消す許可ではない。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    la.new_candidate("/r", target).candidate
    assert candidate.read_bytes() == NEW


def test_a_set_aside_candidate_held_open_is_put_back_instead_of_deleted(tmp_path):
    """比べてから消すまでの間の書き込みを検出できない候補（ほかに開いたままの fd がある）は、消さずに元の名前へ戻す。"""
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    with open(_aside(candidate), "rb"):
        drafted = la.new_candidate("/r", target)
    assert candidate.read_bytes() == OLD  # 戻した候補は、同じ掃除で退避し直さない
    assert drafted.cleanup_targets == []  # 開いたままで戻した候補は、手で消す一覧にも載せない
    la.new_candidate("/r", target).candidate
    assert not candidate.exists() and _aside(candidate).exists()  # 閉じた後の掃除で、もう一度退避する


def test_set_aside_candidates_are_checked_for_open_files_in_one_scan(tmp_path, monkeypatch):
    """退避した候補が何件あっても、開いたままのプロセスの走査は掃除 1 回につき 1 回。"""
    target = _worktree(tmp_path)
    first, second = la.new_candidate("/r", target).candidate, la.new_candidate("/r", target).candidate
    _asked(target, candidate=first, label=la.DENY_LABEL)
    _asked(target, "q2", candidate=second, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    scans, scan = [], lc._open_inodes
    monkeypatch.setattr(lc, "_open_inodes", lambda wanted: scans.append(len(wanted)) or scan(wanted))
    la.new_candidate("/r", target).candidate
    assert len(scans) == 1 and not _aside(first).parent.exists() and not _aside(second).parent.exists()


def test_a_question_is_not_made_for_a_candidate_that_is_gone(tmp_path):
    """候補が消えていれば、質問の中身を作らず、リクエストも足さない。"""
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    la.new_candidate("/r", target).candidate
    assert not candidate.exists() and not _aside(candidate).exists()
    with pytest.raises(internal.InternalError) as raised:
        la.put_question(_request("q2", target), lambda: pytest.fail("質問を作らない"), candidate)
    assert raised.value.kind == "precondition"
    assert [r["request_id"] for r in la.records.whole_rows(la.state_file(la.QUESTIONS))] == ["q1"]


def _denied_and_set_aside(target: Path, request_id: str = "q1") -> Path:
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, request_id, candidate=candidate, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    assert _aside(candidate).exists()
    return candidate


def test_a_set_aside_candidate_with_a_users_file_beside_it_is_put_back(tmp_path):
    """掃除が消すのは NDF が作ったファイルだけ。候補の隣に利用者のファイルがあれば、候補ごと元の名前へ戻す。"""
    target = _worktree(tmp_path)
    candidate = _denied_and_set_aside(target)
    (_aside(candidate).parent / "mine.json").write_bytes(b"{}\n")
    la.new_candidate("/r", target).candidate
    assert candidate.exists() and (candidate.parent / "mine.json").read_bytes() == b"{}\n"


def test_a_dangling_symlink_beside_a_candidate_does_not_stop_the_sweep(tmp_path):
    """状態を読めない項目のある候補は元の名前へ戻し、ほかの候補の片付けと候補の作成は続ける。"""
    target = _worktree(tmp_path)
    linked, plain = la.new_candidate("/r", target).candidate, la.new_candidate("/r", target).candidate
    _asked(target, candidate=linked, label=la.DENY_LABEL)
    _asked(target, "q2", candidate=plain, label=la.DENY_LABEL)
    la.new_candidate("/r", target).candidate
    (_aside(linked).parent / "link").symlink_to(tmp_path / "gone")
    assert la.new_candidate("/r", target).candidate.exists()
    assert linked.exists() and (linked.parent / "link").is_symlink()
    assert not plain.exists() and not _aside(plain).parent.exists()


def test_without_proc_a_cleanup_target_stays_at_its_path_and_is_listed(tmp_path, monkeypatch):
    """消してよいかを確かめられない環境では退避せず、何度 draft を打っても元のパスに残り、理由と共に一覧に出る。"""
    monkeypatch.setattr(lc, "_settleable", lambda: False)
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    for _ in range(3):
        la.new_candidate("/r", target).candidate
        assert candidate.exists() and not _aside(candidate).exists()
    assert la.cleanup_targets("/r") == [{"candidate": str(candidate), "reason": "denied"}]


def test_a_record_replaced_by_the_user_is_not_deleted_by_the_sweep(tmp_path):
    """`source.json` を利用者が同じ名前の別のファイルへ置き換えた候補は、NDF が作ったままと確かめられないので消さずに戻す。"""
    target = _worktree(tmp_path)
    candidate = _denied_and_set_aside(target)
    mine = b'{"target": "mine"}\n'
    (_aside(candidate).parent / lc.SOURCE_NAME).write_bytes(mine)
    la.new_candidate("/r", target)
    assert candidate.exists() and (candidate.parent / lc.SOURCE_NAME).read_bytes() == mine


def test_a_directory_named_like_the_discard_record_is_kept_and_the_draft_continues(tmp_path):
    """`discard.json` という名前の利用者のディレクトリがある候補は、そのディレクトリに触れずに戻し、候補の作成は続ける。"""
    target = _worktree(tmp_path)
    candidate = _denied_and_set_aside(target)
    (_aside(candidate).parent / lc.DISCARD_NAME).mkdir()
    assert la.new_candidate("/r", target).candidate.exists()
    assert candidate.exists() and (candidate.parent / lc.DISCARD_NAME).is_dir()
    assert la.new_candidate("/r", target).candidate.exists()


def test_cleanup_targets_leave_out_candidates_with_the_users_items(tmp_path, monkeypatch):
    """一覧は利用者が消す手がかりなので、候補の隣に利用者の項目があるディレクトリは載せない。"""
    monkeypatch.setattr(lc, "_settleable", lambda: False)
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    (candidate.parent / "mine.json").write_bytes(b"{}\n")
    assert la.cleanup_targets("/r") == [] and la.new_candidate("/r", target).cleanup_targets == []


def _by_path(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: item["candidate"])


def test_without_proc_reflected_and_outdated_candidates_are_listed(tmp_path, monkeypatch):
    """`/proc` の無い環境では、反映した候補と、反映で古くなった直していない候補を一覧に載せる。直した候補は載せない。"""
    monkeypatch.setattr(lc, "_settleable", lambda: False)
    target = _worktree(tmp_path)
    candidate, edited, untouched = _candidate(target), _candidate(target, b"{}\n"), la.new_candidate("/r", target).candidate
    assert _reflect(candidate, lambda: None, SHA)
    assert candidate.exists() and untouched.exists()
    expected = [{"candidate": str(candidate), "reason": "reflected"}, {"candidate": str(untouched), "reason": "outdated"}]
    assert _by_path(la.cleanup_targets("/r")) == _by_path(expected)
    assert _by_path(la.new_candidate("/r", target).cleanup_targets) == _by_path(expected)
    assert edited.exists()


def test_draft_reads_the_requests_once_for_both_the_sweep_and_the_list(tmp_path, monkeypatch):
    """`draft` の一覧は掃除の判定の結果から作り、リクエストと候補を読み直さない。"""
    monkeypatch.setattr(lc, "_settleable", lambda: False)
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    calls, reasons = [], la._sweep_reasons
    monkeypatch.setattr(la, "_sweep_reasons", lambda home: calls.append(home) or reasons(home))
    assert la.new_candidate("/r", target).cleanup_targets == [{"candidate": str(candidate), "reason": "denied"}]
    assert len(calls) == 1


def test_an_unreadable_old_target_or_candidate_only_leaves_that_candidate_out(tmp_path, monkeypatch):
    """過去の反映先や候補がディレクトリに置き換わっていても、その候補だけを外し、別の worktree の draft と show は続ける。"""
    monkeypatch.setattr(lc, "_settleable", lambda: False)
    first, second, other = _worktree(tmp_path / "a"), _worktree(tmp_path / "b"), _worktree(tmp_path / "c")
    broken_candidate, broken_target = la.new_candidate("/r", first).candidate, la.new_candidate("/r", second).candidate
    first.write_bytes(NEW)
    broken_candidate.unlink()
    broken_candidate.mkdir()
    second.unlink()
    second.mkdir()
    assert la.new_candidate("/r", other).cleanup_targets == [] and la.cleanup_targets("/r") == []
    assert broken_candidate.is_dir() and broken_target.exists()


def test_the_list_reuses_the_candidate_read_by_the_sweep(tmp_path, monkeypatch):
    """反映元のバージョンが変わった候補は、判定で読んだ中身のハッシュを一覧でも使い、読み直さない。"""
    monkeypatch.setattr(lc, "_settleable", lambda: False)
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    target.write_bytes(NEW)
    monkeypatch.setattr(lc, "_untouched", lambda *a: pytest.fail("候補を読み直さない"))
    assert la.cleanup_targets("/r") == [{"candidate": str(candidate), "reason": "outdated"}]


@pytest.mark.parametrize("change", ["save", "sibling"])
def test_a_candidate_changed_after_the_judgment_is_not_listed(tmp_path, monkeypatch, change):
    """判定の後に候補が保存されたか、隣に別案が置かれた候補は、判定の結果が古いので一覧に載せない。"""
    monkeypatch.setattr(lc, "_settleable", lambda: False)
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    target.write_bytes(NEW)  # 反映元のバージョンが変わり、判定で候補を読む（outdated）
    judged = la._sweep_reasons

    def then_changed(home):
        reasons = judged(home)
        if change == "save":
            candidate.write_bytes(b'{"version": 1, "mine": true}\n')
        else:
            (candidate.parent / "mine.json").write_bytes(b"{}\n")
        return reasons

    monkeypatch.setattr(la, "_sweep_reasons", then_changed)
    assert la.cleanup_targets("/r") == []


def test_an_unreadable_denied_candidate_is_left_out_and_other_drafts_continue(tmp_path, monkeypatch):
    """否認した候補の candidate.json が読めなくても、その候補だけを外し、別の worktree の draft と show は続ける。"""
    first, other = _worktree(tmp_path / "a"), _worktree(tmp_path / "b")
    candidate = la.new_candidate("/r", first).candidate
    _asked(first, candidate=candidate, label=la.DENY_LABEL)
    read_regular = lc._read_regular

    def denied_read(path, strict=True):
        if path == candidate:
            raise PermissionError(13, "Permission denied", str(path))
        return read_regular(path, strict)

    monkeypatch.setattr(lc, "_read_regular", denied_read)
    assert la.new_candidate("/r", other).cleanup_targets == [] and la.cleanup_targets("/r") == []
    assert candidate.parent.is_dir()


def test_candidates_outside_the_cleanup_conditions_are_not_inspected(tmp_path, monkeypatch):
    """履歴と反映先だけで片付けの条件に当たらないと決まる候補は、ディレクトリを調べない。"""
    target = _worktree(tmp_path)
    la.new_candidate("/r", target)
    calls, ownership = [], lc.ownership
    monkeypatch.setattr(lc, "ownership", lambda folder, known=None: calls.append(folder) or ownership(folder, known))
    la.new_candidate("/r", target)
    assert la.cleanup_targets("/r") == [] and calls == []


# 中断した作成途中の候補の名前（`new_candidate` と同じ乱数の形）
HALF = "0123456789abcdef0123456789abcdef"


def test_draft_reclaims_what_an_interrupted_draft_or_sweep_left(tmp_path):
    """候補の錠の中で見つかる作業中のディレクトリは中断の残り。記録のある作成途中は消し、消す途中で直されていた候補は元へ戻す。"""
    target = _worktree(tmp_path)
    edited = _candidate(target)
    home = edited.parents[1]
    drafting = lc.start_draft(home, HALF)
    removing = home / f"{lc.REMOVING}{edited.parent.name}"
    edited.parent.rename(removing)
    la.new_candidate("/r", target).candidate
    assert not drafting.exists() and not removing.exists() and edited.read_bytes() == NEW


def test_draft_reclaims_only_what_the_draft_record_identifies(tmp_path):
    """Value 6: 作成途中の名前の形だけでは消さない。記録と実体が合う作成途中は NDF のファイルだけを消し、利用者の項目を残す。
    記録の無い作成途中は、空でも中身があっても消さない。"""
    target = _worktree(tmp_path)
    home = la.new_candidate("/r", target).candidate.parents[1]
    users, empty = home / f"{lc.DRAFTING}users", home / f"{lc.DRAFTING}empty"
    users.mkdir()
    empty.mkdir()
    (users / lc.CANDIDATE_NAME).write_text("利用者の設定案", encoding="utf-8")
    recorded = lc.start_draft(home, HALF)
    (recorded / lc.CANDIDATE_NAME).write_bytes(NEW)
    (recorded / "利用者のメモ").write_text("x", encoding="utf-8")
    la.new_candidate("/r", target)
    assert (users / lc.CANDIDATE_NAME).read_text(encoding="utf-8") == "利用者の設定案" and empty.is_dir()
    assert sorted(p.name for p in recorded.iterdir()) == ["利用者のメモ"]
    (recorded / "利用者のメモ").unlink()
    la.new_candidate("/r", target)
    assert not recorded.exists() and not recorded.with_name(recorded.name + lc.DRAFT_RECORD).exists()


# 作成途中の記録の中身ごとに、片付けの後に残るもの（ディレクトリ, 記録）。ディレクトリは空で作っておく
INTERRUPTED = {
    "empty": (True, False),  # 記録を作った直後に落ちた。ディレクトリは作る前なので、その名前のディレクトリは NDF のものでない
    "torn_head": (True, False),  # 名前の行の途中で落ちた。同じくディレクトリは残す
    "head": (False, False),  # 名前の行の後、実体の行の前に落ちた。NDF はまだ中に何も書いていないので、空のディレクトリだけを消す
    "torn_body": (False, False),  # 実体の行の途中で落ちた。同じく空のディレクトリだけを消す
    "foreign": (True, True),  # NDF の記録の形でない
}


@pytest.mark.parametrize("state", list(INTERRUPTED))
def test_draft_reclaims_a_record_left_at_each_step(tmp_path, state):
    """作成途中の記録は一時ファイルを使わずに書くので、どこで落ちても隣の記録 1 つだけが残り、中身で片付け方が決まる。"""
    target = _worktree(tmp_path)
    home = la.new_candidate("/r", target).candidate.parents[1]
    drafting = home / f"{lc.DRAFTING}{HALF}"
    drafting.mkdir()
    head = lc._draft_head(HALF)
    texts = {"empty": "", "torn_head": head[:5], "head": head, "torn_body": head + '{"dev": 12', "foreign": "利用者のメモ\n"}
    record = drafting.with_name(drafting.name + lc.DRAFT_RECORD)
    record.write_text(texts[state], encoding="utf-8")
    la.new_candidate("/r", target)
    assert (drafting.exists(), record.exists()) == INTERRUPTED[state]
    assert not [p.name for p in home.iterdir() if p.name.startswith("..")]  # 記録の一時ファイルを残さない


@pytest.mark.parametrize("finished", [False, True])
def test_draft_keeps_a_directory_it_cannot_identify_as_its_own(tmp_path, finished):
    """Value 6: 作成を中断した後、同じパスへ利用者がディレクトリを戻していれば、中の `candidate.json` を消さない。
    実体の行まで書いた記録は実体と照らし、実体の行の前の記録では中身のあるディレクトリを NDF が作ったものと見なさない。"""
    target = _worktree(tmp_path)
    home = la.new_candidate("/r", target).candidate.parents[1]
    drafting = lc.start_draft(home, HALF)
    record = drafting.with_name(drafting.name + lc.DRAFT_RECORD)
    if not finished:
        record.write_text(lc._draft_head(HALF), encoding="utf-8")
    users = home / "利用者のディレクトリ"  # 元のディレクトリがあるうちに作り、inode の使い回しで実体が合わないようにする
    users.mkdir()
    (users / lc.CANDIDATE_NAME).write_text("利用者の設定案", encoding="utf-8")
    drafting.rmdir()
    users.rename(drafting)
    la.new_candidate("/r", target)
    assert (drafting / lc.CANDIDATE_NAME).read_text(encoding="utf-8") == "利用者の設定案" and not record.exists()


def test_draft_leaves_a_record_whose_name_is_not_a_random_id(tmp_path):
    """Value 6: 乱数の形でない名前の記録とディレクトリは、利用者が置いたものかもしれないので、中身が名前の行でも消さない。"""
    target = _worktree(tmp_path)
    home = la.new_candidate("/r", target).candidate.parents[1]
    drafting = home / f"{lc.DRAFTING}mine"
    drafting.mkdir()
    record = drafting.with_name(drafting.name + lc.DRAFT_RECORD)
    record.write_text(lc._draft_head("mine"), encoding="utf-8")
    la.new_candidate("/r", target)
    assert drafting.is_dir() and record.exists()


def test_reflect_replaces_the_target_once_and_the_retry_succeeds_without_writing(tmp_path):
    target = _worktree(tmp_path)
    candidate, edited, untouched = _candidate(target), _candidate(target, b"{}\n"), la.new_candidate("/r", target).candidate
    calls: list[int] = []
    assert _reflect(candidate, lambda: calls.append(1), SHA)
    assert target.read_bytes() == NEW and calls == [1]
    assert not candidate.exists() and not untouched.exists()  # 反映元のバージョンが合わなくなった、直していない候補は消す
    assert edited.exists()  # 直した候補は、反映元のバージョンが合わなくなっても残す
    assert not target.with_name("launch.json.reflecting").exists()
    assert la.reflected(candidate, target, SHA, None)
    target.write_bytes(OLD)
    assert not la.reflected(candidate, target, SHA, None)


def test_reflect_into_a_missing_target(tmp_path):
    target = _worktree(tmp_path, content=None)
    assert _reflect(_candidate(target), lambda: None, SHA)
    assert target.read_bytes() == NEW


def test_a_target_changed_after_the_draft_is_not_overwritten(tmp_path):
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    target.write_bytes(b'{"version": 1, "mine": true}\n')
    kept: list[Path] = []
    result = _reflect(candidate, lambda: pytest.fail("記録しない"), SHA, kept=kept)
    assert not result and kept == []
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
    assert not _reflect(candidate, lambda: None, SHA)
    assert target.read_bytes() == b'{"version": 1, "saved": 1}\n'
    assert not target.with_name("launch.json.reflecting").exists()


def test_a_save_into_the_aside_after_placing_is_kept_under_another_name(tmp_path, monkeypatch):
    """候補を置いた後、退避の側へエディタが書けば、候補を置いたまま完了とし、退避を別の名前で残してパスを返す。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    placed = candidate.read_bytes()
    link = os.link

    def editor_writes(src, dst):
        link(src, dst)
        if Path(dst) == target:
            Path(str(dst) + la.REFLECTING).write_bytes(b'{"version": 1, "editor": 1}\n')

    monkeypatch.setattr(la.os, "link", editor_writes)
    kept: list[Path] = []
    assert _reflect(candidate, lambda: None, SHA, kept=kept)
    assert target.read_bytes() == placed and [p.read_bytes() for p in kept] == [b'{"version": 1, "editor": 1}\n']


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
        "approver": "山田",
        "route": "terminal",
    }
    os.rename(target, aside)
    target.write_bytes(NEW)
    placed = os.lstat(target)
    pending["placed"] = [placed.st_dev, placed.st_ino]  # NDF が置いた候補のファイルの実体
    lc.write_launch_record(la.reflected_file(target, la.PENDING), pending)
    return target, candidate, aside


def _done(target: Path) -> dict:
    return la.read_launch_record(la.reflected_file(target, la.DONE))


def test_after_an_interruption_right_after_placing_the_retry_succeeds(tmp_path):
    """同じ操作の打ち直しは、途中の記録から完了を確定し、record を呼ばない（同意した人と経路は中断の前のまま）。"""
    target, candidate, aside = _interrupted(tmp_path)
    assert _reflect(candidate, lambda: pytest.fail("確認を取り直した"), SHA)
    assert target.read_bytes() == NEW and not aside.exists()
    assert (_done(target)["approver"], _done(target)["route"]) == ("山田", "terminal")


def test_after_an_interruption_without_an_aside_the_pending_record_completes(tmp_path):
    """退避を消した直後か、反映先が無い状態から置いた直後の中断でも、途中の記録から完了の記録を書く。"""
    target, candidate, aside = _interrupted(tmp_path)
    aside.unlink()
    assert la.finished(candidate, target, SHA, None, candidate.read_bytes(), [])
    assert _done(target)["approver"] == "山田" and not la.reflected_file(target, la.PENDING).exists()


def test_finished_is_false_for_another_operation_or_a_changed_target(tmp_path):
    target, candidate, _ = _interrupted(tmp_path)
    assert not la.finished(candidate, target, SHA, "q-other", candidate.read_bytes(), [])  # 別のリクエストの操作
    assert la.reflected_file(target, la.PENDING).exists()  # 別の操作の途中の記録は片付けない
    target.write_bytes(b'{"version": 1, "a": 1}\n')
    assert not la.finished(candidate, target, SHA, None, candidate.read_bytes(), [])


def test_a_hand_saved_copy_of_the_candidate_still_asks_for_consent(tmp_path):
    """利用者が候補と同じ中身を手で保存した場合は、完了の記録が無いので record を呼ぶ。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    target.write_bytes(candidate.read_bytes())
    calls: list[int] = []
    assert _reflect(candidate, lambda: calls.append(1), SHA) and calls == [1]


@pytest.mark.parametrize("route", ["reflect", "finished"])
def test_after_an_interruption_a_save_into_the_aside_is_kept_and_the_consent_carries_over(tmp_path, route):
    """置いた直後の中断で退避に利用者の保存があれば、反映先の候補を消さず、退避を別の名前で残し、中断の前の同意から完了にする（Value 1・3）。"""
    target, candidate, aside = _interrupted(tmp_path)
    aside.write_bytes(b'{"version": 1, "saved": 1}\n')
    placed = target.read_bytes()
    kept: list[Path] = []
    if route == "reflect":
        assert _reflect(candidate, lambda: pytest.fail("確認を取り直した"), SHA, kept=kept)
    else:  # 端末の経路は、名前とハッシュを求める前に完了を確かめる
        assert la.finished(candidate, target, SHA, None, candidate.read_bytes(), kept)
    assert target.read_bytes() == placed and not aside.exists()
    assert [p.read_bytes() for p in kept] == [b'{"version": 1, "saved": 1}\n']
    assert (_done(target)["approver"], _done(target)["saved"]) == ("山田", str(kept[0]))
    # 結果を受け取る前に止まって打ち直しても、照らした完了の記録から残したパスを返せる
    assert la.reflected(candidate, target, SHA, None).saved == kept[0]


@pytest.mark.parametrize("stop", ["killed", "oserror"])
def test_the_placing_copy_is_removed_after_a_forced_stop_or_a_failed_write(tmp_path, monkeypatch, stop):
    """置き換えの一時ファイルは作る前に名前を途中の記録へ残すので、強制終了の後の片付けも、書き込みの失敗の片付けも、作業場所に設定案の写しを残さない。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    users = target.with_name(".launch.json.placing-mine")  # 利用者が置いた同じ頭のファイル（Value 6: 消さない）
    users.write_bytes(b"")
    made: list[Path] = []

    def fail(_target, tmp, _base):
        made.append(tmp)
        raise KeyboardInterrupt if stop == "killed" else OSError("書けない")

    monkeypatch.setattr(la, "_replace", fail)
    with pytest.raises(KeyboardInterrupt if stop == "killed" else internal.InternalError):
        _reflect(candidate, lambda: None, SHA)
    if stop == "killed":
        assert made[0].exists()  # 強制終了ではその場で消せない
        la.recover(target)
    assert not made[0].exists() and target.read_bytes() == OLD and users.exists()


def test_the_placing_copy_is_removed_when_its_identity_cannot_be_recorded(tmp_path, monkeypatch):
    """一時ファイルの実体を途中の記録へ書けずに止まったら、次の片付けでは確かめられないので、その実行が手元の実体でその場で消す（Value 6）。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    write = lc.write_launch_record
    made: list[Path] = []

    def fail_on_the_identity(path, rec):
        if "placed" in rec:
            made.append(Path(rec["placing"]))
            raise OSError("書けない")
        write(path, rec)

    monkeypatch.setattr(lc, "write_launch_record", fail_on_the_identity)
    with pytest.raises(internal.InternalError):
        _reflect(candidate, lambda: None, SHA)
    assert made and not made[0].exists() and target.read_bytes() == OLD


def test_a_failed_identity_write_leaves_no_temporary_record(tmp_path, monkeypatch):
    """実体を書いた途中の記録の付け替えに失敗しても、記録の一時ファイルと作業場所の一時ファイルを残さない（Value 6）。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    pending = la.reflected_file(target, la.PENDING)
    replace = os.replace
    made: list[Path] = []

    def fail_on_the_identity(src, dst):
        if Path(dst) == pending and "placed" in json.loads(Path(src).read_bytes()):
            made.append(Path(json.loads(Path(src).read_bytes())["placing"]))
            raise OSError("付け替えられない")
        replace(src, dst)

    monkeypatch.setattr(lc.os, "replace", fail_on_the_identity)
    with pytest.raises(internal.InternalError):
        _reflect(candidate, lambda: None, SHA)
    assert made and not made[0].exists() and target.read_bytes() == OLD
    assert not list(pending.parent.glob(f".{pending.name}.*"))


def test_the_cleanup_keeps_a_file_put_under_the_name_after_it_was_checked(tmp_path, monkeypatch):
    """確かめた後に一時ファイルの名前へ利用者のファイルが置かれたら、消さずに元の名前へ戻す（Value 6）。"""
    target = _worktree(tmp_path)
    tmp = target.with_name(".launch.json.placing-0")
    tmp.write_bytes(b"ndf")
    found = os.lstat(tmp)
    made = la._made

    def swapped(path, placed):
        result = made(path, placed)
        if path == tmp:  # 確かめた直後に、利用者が同じ名前へ保存する
            users = target.with_name("users")
            users.write_bytes(b"mine")
            os.replace(users, tmp)
        return result

    monkeypatch.setattr(la, "_made", swapped)
    la._drop_placing(target, {"placing": str(tmp), "placed": [found.st_dev, found.st_ino]})
    assert tmp.read_bytes() == b"mine"
    assert not list(target.parent.glob(".launch.json.dropping-*"))


def test_the_cleanup_removes_the_copy_only_after_moving_it_into_its_own_directory(tmp_path, monkeypatch):
    """一時ファイルは共有のディレクトリの名前のまま消さず、NDF が作った専用のディレクトリへ移してから確かめて消す（Value 6）。"""
    target = _worktree(tmp_path)
    tmp = target.with_name(".launch.json.placing-0")
    tmp.write_bytes(b"ndf")
    found = os.lstat(tmp)
    rename = os.rename
    moves: list[tuple[Path, bool]] = []

    def watched(src, dst):
        moves.append((Path(dst), Path(dst).parent.is_dir() and not os.listdir(Path(dst).parent)))
        rename(src, dst)

    monkeypatch.setattr(la.os, "rename", watched)
    la._drop_placing(target, {"placing": str(tmp), "placed": [found.st_dev, found.st_ino]})
    box = target.with_name(".launch.json.dropping-0")
    assert moves == [(box / la._DROPPING_FILE, True)]  # 移す先は、作ったばかりの空の専用のディレクトリの中
    assert not tmp.exists() and not box.exists()


def test_the_cleanup_finishes_a_move_before_removal_only_for_the_recorded_name(tmp_path):
    """消す前に専用のディレクトリへ移した直後に止まった一時ファイルは、記録した名前から決まるディレクトリだけを片付け、ほかは見ない（Value 6）。"""
    target = _worktree(tmp_path)
    box = target.with_name(".launch.json.dropping-0")  # 記録した名前 `.launch.json.placing-0` を移す専用のディレクトリ
    box.mkdir()
    moved = box / la._DROPPING_FILE
    moved.write_bytes(b"ndf")
    other = target.with_name(".launch.json.dropping-1")  # 記録に無い名前は、実体が同じでも見ない
    os.link(moved, other)
    found = os.lstat(moved)
    rec = {"placing": str(target.with_name(".launch.json.placing-0"))}
    la._drop_placing(target, rec)  # 実体が記録に無ければ移した先も消さない
    assert moved.exists()
    la._drop_placing(target, {**rec, "placed": [found.st_dev, found.st_ino]})
    assert not box.exists() and other.exists()


@pytest.mark.parametrize("body", [b'{"version": 1, "mine": 1}\n', b""])
def test_the_cleanup_keeps_a_file_the_record_names_but_ndf_did_not_make(tmp_path, body):
    """途中の記録が名前を残していても、記録した実体と違うファイルは、利用者の作ったものかもしれないので消さない（Value 6）。

    空のファイルで実体の記録が無い場合（名前を記録した後に利用者の空のファイルと重なり、次の名前を記録する前に止まった）も消さない。
    """
    target, _, aside = _interrupted(tmp_path)
    aside.unlink()
    users = target.with_name(".launch.json.placing-" + "0" * 32)
    users.write_bytes(body)
    pending = dict(la.read_launch_record(la.reflected_file(target, la.PENDING)))
    if not body:
        pending.pop("placed", None)
    lc.write_launch_record(la.reflected_file(target, la.PENDING), {**pending, "placing": str(users)})
    la.recover(target)
    assert users.read_bytes() == body


def test_an_interruption_after_removing_the_aside_still_reports_the_saved_path(tmp_path, monkeypatch):
    """退避を別の名前で残し、退避を消した直後に中断しても、次の片付けがそのパスを返し、途中の記録の同意から完了にする。"""
    target, candidate, aside = _interrupted(tmp_path)
    aside.write_bytes(b'{"version": 1, "saved": 1}\n')
    unlink = Path.unlink

    def stop_after_the_aside(self, *args, **kwargs):
        unlink(self, *args, **kwargs)
        if self == aside:
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "unlink", stop_after_the_aside)
    with pytest.raises(KeyboardInterrupt):
        la.recover(target)
    monkeypatch.setattr(Path, "unlink", unlink)
    saved = la.recover(target)
    assert saved is not None and saved.read_bytes() == b'{"version": 1, "saved": 1}\n'
    assert _done(target)["approver"] == "山田"


def test_a_hand_save_of_the_candidate_while_placing_is_not_taken_as_placed(tmp_path, monkeypatch):
    """手順 4 の間に利用者が候補と同じ中身を手で保存したら、NDF が置いたものでないので反映済みにせず止める。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    link = os.link

    def hand_save_first(src, dst):
        if Path(dst) == target:
            target.write_bytes(candidate.read_bytes())
        link(src, dst)

    monkeypatch.setattr(la.os, "link", hand_save_first)
    assert not _reflect(candidate, lambda: None, SHA)
    assert target.read_bytes() == candidate.read_bytes() and _done(target) == {}


def test_recover_keeps_both_when_target_and_aside_hold_the_users_contents(tmp_path):
    target, _, aside = _interrupted(tmp_path)
    target.write_bytes(b'{"version": 1, "a": 1}\n')
    aside.write_bytes(b'{"version": 1, "b": 1}\n')
    saved = la.recover(target)
    assert target.read_bytes() == b'{"version": 1, "a": 1}\n' and saved.read_bytes() == b'{"version": 1, "b": 1}\n'
    assert saved.name.startswith("launch.json.saved-") and not aside.exists()


@pytest.mark.parametrize("taken", [False, True])
def test_putting_the_aside_back_never_overwrites_an_existing_file(tmp_path, monkeypatch, taken):
    """退避を戻すときも別の名前で残すときも、既にあるファイル（照らした後の保存・前に残した保存）を上書きしない（Value 6）。"""
    target, _, aside = _interrupted(tmp_path)
    aside.write_bytes(b'{"version": 1, "b": 1}\n')
    target.write_bytes(b'{"version": 1, "a": 1}\n')
    earlier = target.with_name("launch.json.saved-00000000")
    earlier.write_bytes(b'{"version": 1, "old": 1}\n')
    values = iter(["0" * 32, "1" * 32] if taken else ["1" * 32])
    monkeypatch.setattr(la.uuid, "uuid4", lambda: la.uuid.UUID(next(values)))
    saved = la._link_back(aside, target)
    assert target.read_bytes() == b'{"version": 1, "a": 1}\n' and earlier.read_bytes() == b'{"version": 1, "old": 1}\n'
    assert saved.name == "launch.json.saved-11111111" and saved.read_bytes() == b'{"version": 1, "b": 1}\n' and not aside.exists()


@pytest.mark.parametrize("left", ["aside", "pending"])
def test_recover_with_only_the_aside_or_only_the_pending_record(tmp_path, left):
    target, _, aside = _interrupted(tmp_path)
    target.unlink()
    if left == "pending":
        aside.unlink()
    assert la.recover(target) is None
    assert (target.read_bytes() == OLD) if left == "aside" else not target.exists()
    assert not la.reflected_file(target, la.PENDING).exists()


def test_reflect_tells_where_the_users_save_in_the_aside_was_kept(tmp_path):
    """反映先と退避の両方に利用者の保存があれば、拒否の結果に退避を残したパスが入る。"""
    target, candidate, aside = _interrupted(tmp_path)
    target.write_bytes(b'{"version": 1, "a": 1}\n')
    aside.write_bytes(b'{"version": 1, "b": 1}\n')
    kept: list[Path] = []
    result = _reflect(candidate, lambda: pytest.fail("記録しない"), SHA, kept=kept)
    assert not result and [p.read_bytes() for p in kept] == [b'{"version": 1, "b": 1}\n']


@pytest.mark.parametrize("locked", [True, False])
def test_a_short_write_is_retried_as_a_whole_line_without_breaking_other_rows(tmp_path, monkeypatch, locked):
    """書ききれなかった行は続きを足さず、同じ行を 1 回の write で書き直す。間に入った別の行も読める。"""
    path = la.state_file(la.ANSWERS)
    write, calls = os.write, []

    def short_then_other(fd, data):
        calls.append(data)
        if len(calls) == 1:
            written = write(fd, data[: len(data) // 2])
            la.write_row(path, {"request_id": "other"}, locked=False)  # 書きかけの後に入った別の hook の追記
            return written
        return write(fd, data)

    monkeypatch.setattr(os, "write", short_then_other)
    la.write_row(path, {"request_id": "mine"}, locked=locked)
    assert [row["request_id"] for row in la.records.whole_rows(path)] == ["other", "mine"]


def _matching_request(tmp_path: Path, label: str | None) -> tuple[Path, Path]:
    """候補にリクエストを作り（`label` が None なら回答待ち）、別の編集で反映先を候補と同じ中身にする。"""
    target = _worktree(tmp_path)
    candidate = _candidate(target)
    _asked(target, candidate=candidate, label=label)
    target.write_bytes(NEW)
    return target, candidate


@pytest.mark.parametrize("label", [None, la.APPROVE_LABEL.format("人")])
def test_a_candidate_with_a_request_still_usable_is_neither_set_aside_nor_listed(tmp_path, label):
    """回答待ちか承認済みで、まだ使えるリクエストの候補は、反映先と同じ中身でも掃除せず、人が回答した後の approve で反映を済ませられる。"""
    target, candidate = _matching_request(tmp_path, label)
    assert la.new_candidate("/r", target).cleanup_targets == [] and la.cleanup_targets("/r") == []
    assert candidate.exists()
    if label is None:
        _answer("q1", la.APPROVE_LABEL.format("人"))
    assert _reflect(candidate, lambda: None, SHA, "q1")


def test_a_candidate_whose_approval_was_revoked_after_the_request_is_swept(tmp_path):
    """リクエストの後に同じ内容を取り消した候補は、回答が使えないので、反映先と同じ中身なら片付ける。"""
    target, candidate = _matching_request(tmp_path, la.APPROVE_LABEL.format("人"))
    la.append("/r", SHA, la.Action.REVOKE)
    la.new_candidate("/r", target)
    assert not candidate.exists() and _aside(candidate).exists()


def test_the_sweep_after_a_reflection_keeps_another_candidate_with_a_usable_approval(tmp_path):
    """別の候補の反映で反映先が承認済みの候補と同じ中身になっても、反映の後の掃除はその候補を退避しない。"""
    target = _worktree(tmp_path)
    other, approved = _candidate(target), _candidate(target)
    _asked(target, candidate=approved)
    assert _reflect(other, lambda: None, SHA, None)
    assert approved.exists()
    assert _reflect(approved, lambda: None, SHA, "q1")


def test_a_record_replaced_by_a_fifo_does_not_stop_draft_or_show(tmp_path):
    """候補の source.json が書き手のいない FIFO に置き換わっていても、draft と show は待たずに続け、その候補には触れない。"""
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    (candidate.parent / lc.SOURCE_NAME).unlink()
    os.mkfifo(candidate.parent / lc.SOURCE_NAME)
    assert la.new_candidate("/r", target).cleanup_targets == [] and la.cleanup_targets("/r") == []
    assert candidate.exists()


def test_a_record_replaced_by_a_fifo_after_the_ownership_check_does_not_stop_draft(tmp_path, monkeypatch):
    """所有を確かめた直後に記録が FIFO に置き換わっても、退避の判定は確かめた時に読んだ記録を使い、開き直さない。"""
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate, label=la.DENY_LABEL)
    ownership = lc.ownership

    def then_fifo(folder, known=None):
        owned = ownership(folder, known)
        source = folder / lc.SOURCE_NAME
        if known is None and source.is_file():
            source.unlink()
            os.mkfifo(source)
        return owned

    monkeypatch.setattr(lc, "ownership", then_fifo)
    la.new_candidate("/r", target)


def test_an_approved_candidate_that_can_no_longer_be_placed_is_swept(tmp_path):
    """回答で承認の行を書いた候補でも、反映先が反映元のバージョンとも候補とも違えば反映できないので、作ったときの中身のままなら片付ける。"""
    target = _worktree(tmp_path)
    candidate = la.new_candidate("/r", target).candidate
    _asked(target, candidate=candidate)
    with la.records_lock(la.state_file(la.APPROVALS)):
        assert la.approve_by_answer("/r", SHA, "q1") == "人"
    target.write_bytes(NEW)
    la.new_candidate("/r", target)
    assert not candidate.exists() and _aside(candidate).exists()


def test_the_sweep_reads_the_approvals_only_when_a_candidate_has_a_request(tmp_path, monkeypatch):
    """リクエストの無い候補しか無い掃除は、承認の記録を読まない。"""
    target = _worktree(tmp_path)
    la.new_candidate("/r", target)
    target.write_bytes(NEW)
    whole_rows, reads = la.records.whole_rows, []
    monkeypatch.setattr(la.records, "whole_rows", lambda path: reads.append(path) or whole_rows(path))
    la.new_candidate("/r", target)
    la.cleanup_targets("/r")
    assert la.state_file(la.APPROVALS) not in reads
