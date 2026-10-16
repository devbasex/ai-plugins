"""取り消しの積み直しで衝突したコミットの項目だけを外す規則（#1793 の R2）を**実際の git** で確かめる。

| 受け入れ条件・不変条件 | 確かめること |
| --- | --- |
| R2 AC1（I1） | A・B（A の行の上）・C（同じファイルの離れた行）・D（別のファイル）で A を取り消すと B だけが外れる |
| R2 AC2（I2） | 外した理由と `drops[].ejected` が残り、`mode` に `widened` は現れない |
| 外した項目の前のコミット | テストと実装の 2 コミットの項目は、実装のコミットが衝突してもテストのコミットごと消える |
| R2 AC4・AC5（I4） | 項目のコミットの衝突では止まらない。公開済みの revert・項目に属さないコミットの衝突では HEAD を戻して止まる |
| 最終ゲートの中（I3） | `verified` を戻さない（`recheck` は空） |
| R2 AC6（I8） | `pending_drop` を残して止めた実行を再開すると、止めずに通したときと同じ項目が外れ、同じ項目が残る |
| R2 AC7 | `drops[].mode` が `widened` の旧い状態ファイルを報告が読める |
"""

from __future__ import annotations

import argparse
import copy

import pytest

from crossref_helpers import commit_with_trailers, git, item_trailers, make_state_v2, read_state, write_state

LINES = [f"line{i}\n" for i in range(1, 41)]


def _init(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "tests").mkdir()
    git("init", "-q", str(work), cwd=tmp_path)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    for rel in ("src/foo.py", "src/bar.py", "tests/test_foo.py"):
        (work / rel).write_text("".join(LINES), encoding="utf-8")
    return work, commit_with_trailers(work, "init", {})


def _edit(work, rel, index, text):
    path = work / rel
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[index] = text + "\n"
    path.write_text("".join(lines), encoding="utf-8")


def _commit(work, rel, index, text, item_id=None):
    _edit(work, rel, index, text)
    return commit_with_trailers(work, text, item_trailers(item_id) if item_id else {})


def _item(item_id, rank, implement, test=None, status="verified"):
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/foo.py",
        "status": status,
        "commits": {"test": test, "implement": implement, "fix": []},
    }


def _abcd(tmp_path, phase="verify"):
    """A は foo.py の 3 行目、B は A が書いた 3 行目の上、C は foo.py の 30 行目、D は bar.py を触る。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    b = _commit(work, "src/foo.py", 2, "by-B", "I-B")
    c = _commit(work, "src/foo.py", 29, "by-C", "I-C")
    d = _commit(work, "src/bar.py", 2, "by-D", "I-D")
    items = [_item("I-A", 1, a), _item("I-B", 2, b), _item("I-C", 3, c), _item("I-D", 4, d)]
    items[1]["failure_reason"] = "自分の範囲テストで落ちた"  # 外した理由で上書きされる（決定 7）
    path = make_state_v2(tmp_path, work, items=items, plan={"base_sha": base}, phase=phase)
    return work, base, path, {"I-A": a, "I-B": b, "I-C": c, "I-D": d}


def _statuses(path):
    return {i["id"]: i["status"] for i in read_state(path)["items"]}


def test_only_the_item_whose_commit_conflicted_is_ejected(tmp_path, undo):
    """R2 AC1・AC2（I1・I2）: ファイルが同じというだけでは外さない。"""
    work, _, path, shas = _abcd(tmp_path)

    record = undo.drop(path, read_state(path), ["I-A"], "範囲テストが落ちた")

    assert _statuses(path) == {"I-A": "reverted", "I-B": "reverted", "I-C": "implemented", "I-D": "implemented"}
    foo = (work / "src" / "foo.py").read_text(encoding="utf-8")
    assert "by-A" not in foo and "by-B" not in foo and "by-C" in foo
    assert "by-D" in (work / "src" / "bar.py").read_text(encoding="utf-8")
    assert record["mode"] == "ejected"
    assert record["ejected"] == [{"item": "I-B", "commit": shas["I-B"], "by": ["I-A"]}]
    assert record["dropped"] == ["I-A", "I-B"]
    assert record["recheck"] == ["I-C", "I-D"], "最終ゲートより前の取り消しの後は確かめ直す"
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-B"]["failure_reason"] == f"I-A の取り消しで {shas['I-B'][:12]} の積み直しが衝突したため外した"
    assert items["I-A"]["failure_reason"] == "範囲テストが落ちた"
    assert "widened" not in [d["mode"] for d in read_state(path)["drops"]]


def test_an_ejected_item_loses_its_test_commit_as_well(tmp_path, undo):
    """外した項目のテストのコミットは、衝突した実装のコミットより前に積み直し終えていても消える（R1）。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    b_test = _commit(work, "tests/test_foo.py", 5, "test-by-B", "I-B")
    b = _commit(work, "src/foo.py", 2, "by-B", "I-B")
    items = [_item("I-A", 1, a), _item("I-B", 2, b, test=b_test)]
    path = make_state_v2(tmp_path, work, items=items, plan={"base_sha": base}, phase="verify")

    record = undo.drop(path, read_state(path), ["I-A"], "範囲テストが落ちた")

    assert record["ejected"] == [{"item": "I-B", "commit": b, "by": ["I-A"]}]
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == base
    assert "test-by-B" not in (work / "tests" / "test_foo.py").read_text(encoding="utf-8")


def test_an_unowned_commit_conflict_restores_head_and_stops(tmp_path, undo, capsys):
    """R2 AC5（I4）: 項目に属さないコミット（オーケストレーター）の積み直しが衝突したら HEAD を戻して終了コード 4。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    sync = _commit(work, "src/foo.py", 2, "by-sync")
    path = make_state_v2(tmp_path, work, items=[_item("I-A", 1, a)], plan={"base_sha": base}, ledger={"orchestrator_commits": [sync]})

    with pytest.raises(SystemExit) as e:
        undo.drop(path, read_state(path), ["I-A"], "範囲テストが落ちた")

    assert e.value.code == 4
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == sync
    assert sync[:12] in capsys.readouterr().err
    assert _statuses(path) == {"I-A": "verified"}

    with pytest.raises(undo.DropConflict):
        undo.drop(path, read_state(path), ["I-A"], "範囲テストが落ちた", on_conflict=undo.ON_CONFLICT_RAISE)
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == sync


def test_a_published_revert_conflict_restores_head_and_stops(tmp_path, undo):
    """R2 AC5（I4）: 公開済みのコミットの revert が衝突したら、外さずに HEAD を戻して終了コード 4。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    b = _commit(work, "src/foo.py", 2, "by-B", "I-B")
    path = make_state_v2(
        tmp_path,
        work,
        items=[_item("I-A", 1, a), _item("I-B", 2, b)],
        plan={"base_sha": base},
        ledger={"orchestrator_commits": [], "published_sha": b},
        phase="final",
    )

    with pytest.raises(SystemExit) as e:
        undo.drop(path, read_state(path), ["I-A"], "最終ゲート")

    assert e.value.code == 4
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == b
    assert _statuses(path) == {"I-A": "verified", "I-B": "verified"}


def test_a_drop_inside_the_final_gate_keeps_verified_items(tmp_path, undo):
    """最終ゲートの中の取り消し（I3・決定 5）は `verified` を戻さない。最終ゲートが HEAD を確かめ直す。"""
    _, _, path, _ = _abcd(tmp_path, phase="final")

    record = undo.drop(path, read_state(path), ["I-A"], "打ち切り")

    assert record["recheck"] == []
    assert _statuses(path) == {"I-A": "reverted", "I-B": "reverted", "I-C": "verified", "I-D": "verified"}


def test_resuming_an_interrupted_drop_ejects_the_same_items(tmp_path, undo):
    """R2 AC6（I8）: `pending_drop` は指定した項目だけを持ち、再開で外す項目を計算し直して同じ結果になる。"""
    work, _, path, shas = _abcd(tmp_path)
    pristine = read_state(path)
    undo.drop(path, copy.deepcopy(pristine), ["I-A"], "範囲テストが落ちた")
    finished = read_state(path)
    finished_tree = git("rev-parse", "HEAD^{tree}", cwd=work).stdout.strip()
    # git は積み直しまで進み、状態は `pending_drop` を保存した直後のまま残った
    pristine["pending_drop"] = {"items": ["I-A"], "reason": "範囲テストが落ちた", "before": shas["I-D"]}
    write_state(path, pristine)

    undo.resume_pending_drop(path, read_state(path))

    resumed = read_state(path)
    assert git("rev-parse", "HEAD^{tree}", cwd=work).stdout.strip() == finished_tree
    assert _statuses(path) == {i["id"]: i["status"] for i in finished["items"]}
    assert resumed["drops"][-1]["ejected"] == finished["drops"][-1]["ejected"]
    assert resumed["pending_drop"] is None


def test_the_report_reads_an_old_widened_record(tmp_path, cmd_report, env_tmp_dir, capsys):
    """R2 AC7: 旧い状態ファイルの `drops[].mode: widened` を報告がエラーなく読む。"""
    path = make_state_v2(
        tmp_path,
        tmp_path / "work",
        items=[
            {
                "id": "I-001",
                "path": "src/a.py",
                "symbol": "f",
                "status": "reverted",
                "failure_reason": "x（widened の取り消しに巻き込まれた）",
            },
        ],
        drops=[{"at": "2026-10-06T11:04:35", "mode": "widened", "reason": "x", "dropped": ["I-001"], "extra": [], "removed": 2}],
    )
    env_tmp_dir(path)

    cmd_report.cmd_report(argparse.Namespace(id=130, metrics=True))

    assert "I-001" in capsys.readouterr().out
