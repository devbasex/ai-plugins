"""改善項目の打ち消し（#2457 決定 6・I5・I9・受け入れ条件 12・19）を**実際の git** で確かめる。

| 受け入れ条件・不変条件 | 確かめること |
| --- | --- |
| 12・I5 | 打ち消すのは項目のコミットだけで、着手の後に取り込んだベースブランチのマージの変更は残る（#2323 の形） |
| 19・I9 | 打ち消しが衝突したら、打ち消しの前の HEAD へ戻して `UndoConflict` を返し、衝突した項目を外さない |
| I5 | 隣り合う項目（同じ行を触った後の項目）を外して続けない（`ejected` は無い） |
"""

from __future__ import annotations

import pytest

from crossref_helpers import commit_with_trailers, git, item_trailers, make_state_v2, read_state

LINES = [f"line{i}\n" for i in range(1, 41)]


def _init(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    for rel in ("src/foo.py", "src/bar.py"):
        (work / rel).write_text("".join(LINES), encoding="utf-8")
    return work, commit_with_trailers(work, "init", {})


def _commit(work, rel, index, text, item_id=None):
    path = work / rel
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[index] = text + "\n"
    path.write_text("".join(lines), encoding="utf-8")
    return commit_with_trailers(work, text, item_trailers(item_id) if item_id else {})


def _item(item_id, implement, status="failing", rank=1):
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/foo.py",
        "status": status,
        "commits": {"test": None, "implement": implement, "fix": []},
    }


def test_only_the_items_commits_are_reverted_and_the_merged_base_stays(tmp_path, undo):
    """#2323 の形: 着手の後にベースブランチを取り込んだマージの変更は、項目を打ち消しても消えない。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    git("checkout", "-q", "-b", "base-update", base, cwd=work)
    _commit(work, "src/bar.py", 10, "from-base")
    git("checkout", "-q", "-", cwd=work)
    git("merge", "-q", "--no-edit", "base-update", cwd=work)
    b = _commit(work, "src/foo.py", 29, "by-B", "I-B")
    path = make_state_v2(
        tmp_path, work, items=[_item("I-A", [a]), _item("I-B", [b], status="verified")], plan={"base_sha": base}, phase="verify"
    )

    record = undo.revert_items(path, read_state(path), ["I-A"], "成立しないと判定した")

    foo = (work / "src" / "foo.py").read_text(encoding="utf-8")
    assert "by-A" not in foo and "by-B" in foo
    assert "from-base" in (work / "src" / "bar.py").read_text(encoding="utf-8")
    assert record["mode"] == "revert" and record["reverted"] == 1
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-A"]["status"] == "reverted" and items["I-A"]["failure_reason"] == "成立しないと判定した"
    assert items["I-B"]["status"] == "implemented", "打ち消しで HEAD が変わったら確かめ直す"
    assert git("cat-file", "-p", "HEAD", cwd=work).stdout.count("parent ") == 1, "revert のコミットを積む（履歴を書き換えない）"


def test_a_conflicting_revert_restores_head_and_keeps_every_item(tmp_path, undo):
    """19・I9: 後の項目が同じ行を書き換えていて打ち消しが衝突したら、HEAD を戻して止まる。隣の項目を外さない。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    b = _commit(work, "src/foo.py", 2, "by-B", "I-B")
    path = make_state_v2(
        tmp_path, work, items=[_item("I-A", [a]), _item("I-B", [b], status="verified")], plan={"base_sha": base}, phase="verify"
    )

    with pytest.raises(undo.UndoConflict) as e:
        undo.revert_items(path, read_state(path), ["I-A"], "成立しないと判定した")

    assert e.value.item_id == "I-A"
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == b
    assert git("status", "--porcelain", cwd=work).stdout.strip() == ""
    statuses = {i["id"]: i["status"] for i in read_state(path)["items"]}
    assert statuses == {"I-A": "failing", "I-B": "verified"}
    assert not any(d.get("ejected") for d in read_state(path).get("drops") or [])


def test_a_conflict_after_an_earlier_revert_keeps_every_item_state(tmp_path, undo):
    """先の項目を打ち消せた後で次の項目が衝突したら、HEAD と一緒に項目の状態も開始前のまま残す。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    b = _commit(work, "src/foo.py", 29, "by-B", "I-B")
    c = _commit(work, "src/foo.py", 2, "by-C", "I-C")
    items = [_item("I-A", [a]), _item("I-B", [b], rank=2), _item("I-C", [c], status="verified", rank=3)]
    path = make_state_v2(tmp_path, work, items=items, plan={"base_sha": base}, phase="verify")

    with pytest.raises(undo.UndoConflict):
        undo.revert_items(path, read_state(path), ["I-A", "I-B"], "成立しないと判定した")

    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == c
    statuses = {i["id"]: i["status"] for i in read_state(path)["items"]}
    assert statuses == {"I-A": "failing", "I-B": "failing", "I-C": "verified"}


def test_uncommitted_changes_stop_the_revert_without_discarding_them(tmp_path, undo):
    """C4: 衝突の巻き戻し（`reset --hard`）で消さないよう、未コミットの変更があれば打ち消さずに止まる。"""
    work, base = _init(tmp_path)
    a = _commit(work, "src/foo.py", 2, "by-A", "I-A")
    path = make_state_v2(tmp_path, work, items=[_item("I-A", [a])], plan={"base_sha": base}, phase="verify")
    (work / "src" / "bar.py").write_text("edited\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        undo.revert_items(path, read_state(path), ["I-A"], "成立しないと判定した")
    with pytest.raises(SystemExit):
        undo.revert_unattributed(path, read_state(path), [a], "手順の違反")

    assert (work / "src" / "bar.py").read_text(encoding="utf-8") == "edited\n"
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == a
