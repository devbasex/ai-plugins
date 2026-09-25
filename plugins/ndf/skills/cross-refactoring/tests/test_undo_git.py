"""項目の単位の取り消しと積み直しを**実際の git** で確かめる（#933 の AC15、実装計画 I6）。

| 位置関係 | 結果 |
| --- | --- |
| 別ファイル / 離れた行 | 項目だけを取り消し、残す項目は積み直せる（`item`） |
| 同一ファイルの隣接行 | 積み直せない。同じファイルを触った項目まで広げる（`widened`） |

**隣接する変更は git だけでは分離できない。** 取り消した側の行が消えると、残す側の
パッチが前提にしている文脈も消えるためである。広げてでも Pull Request を決定的な
状態に保つことを優先する。
"""
from __future__ import annotations

import copy
import json

import pytest

from crossref_helpers import commit_with_trailers, git, item_trailers, make_state_v2, read_state

LINES = [f"line{i}\n" for i in range(1, 41)]


def _repo(tmp_path, second_line, second_file="foo.py"):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    git("init", "-q", str(work), cwd=tmp_path)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    (work / "src" / "foo.py").write_text("".join(LINES), encoding="utf-8")
    (work / "src" / "bar.py").write_text("".join(LINES), encoding="utf-8")
    base = commit_with_trailers(work, "init", {})

    lines = list(LINES)
    lines[2] = "line3-by-I-001\n"
    (work / "src" / "foo.py").write_text("".join(lines), encoding="utf-8")
    c1 = commit_with_trailers(work, "I-001", item_trailers("I-001"))

    target = work / "src" / second_file
    other = list(target.read_text(encoding="utf-8").splitlines(keepends=True))
    other[second_line] = "changed-by-I-002\n"
    target.write_text("".join(other), encoding="utf-8")
    c2 = commit_with_trailers(work, "I-002", item_trailers("I-002"))
    return work, base, c1, c2


def _state(tmp_path, work, base, c1, c2):
    items = [
        {"id": "I-001", "rank": 1, "path": "src/foo.py", "symbol": "a", "status": "implemented",
         "commits": {"test": None, "implement": c1, "fix": []}},
        {"id": "I-002", "rank": 2, "path": "src/foo.py", "symbol": "b", "status": "implemented",
         "commits": {"test": None, "implement": c2, "fix": []}},
    ]
    return make_state_v2(tmp_path, work, items=items, plan={"base_sha": base})


@pytest.mark.parametrize(("line", "file"), [(30, "foo.py"), (2, "bar.py")])
def test_a_distant_or_separate_change_is_dropped_alone(tmp_path, undo, line, file):
    work, base, c1, c2 = _repo(tmp_path, line, file)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)

    record = undo.drop(path, state, ["I-001"], "テスト")

    assert record["mode"] == "item"
    foo = (work / "src" / "foo.py").read_text(encoding="utf-8")
    assert "line3-by-I-001" not in foo
    changed = (work / "src" / file).read_text(encoding="utf-8")
    assert "changed-by-I-002" in changed
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-001"]["status"] == "reverted"
    assert items["I-002"]["status"] == "implemented"
    # 積み直した SHA を記録へ書き戻す。
    new_sha = items["I-002"]["commits"]["implement"]
    assert new_sha != c2
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == new_sha
    assert read_state(path)["pending_drop"] is None


def test_an_adjacent_change_widens_to_the_items_touching_the_same_file(tmp_path, undo):
    work, base, c1, c2 = _repo(tmp_path, 3)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)

    record = undo.drop(path, state, ["I-001"], "テスト")

    assert record["mode"] == "widened"
    assert (work / "src" / "foo.py").read_text(encoding="utf-8") == "".join(LINES)
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-002"]["status"] == "reverted"
    assert "巻き込まれた" in items["I-002"]["failure_reason"]


def test_an_item_without_commits_is_closed_without_touching_git(tmp_path, undo):
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    state["items"].append({"id": "I-003", "rank": 3, "status": "planned",
                           "commits": {"test": None, "implement": None, "fix": []}})
    head = git("rev-parse", "HEAD", cwd=work).stdout.strip()

    record = undo.drop(path, state, ["I-003"], "テスト")

    assert record["mode"] == "skip"
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == head


def test_an_unowned_extra_commit_is_removed_while_item_commits_are_replayed(tmp_path, undo):
    """現状固定: extra_shas は消え、項目に属する履歴だけが積み直される。"""
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    (work / "src" / "extra.py").write_text("unowned\n", encoding="utf-8")
    extra = commit_with_trailers(work, "unowned", {})

    record = undo.drop(path, state, [], "計画外", [extra])

    assert record == {
        "at": record["at"],
        "mode": "item",
        "reason": "計画外",
        "dropped": [],
        "extra": [extra],
        "reverted_commits": 3,
        "replayed": 2,
    }
    assert not (work / "src" / "extra.py").exists()
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-001"]["status"] == "implemented"
    assert items["I-002"]["status"] == "implemented"
    assert items["I-001"]["commits"]["implement"] != c1
    assert items["I-002"]["commits"]["implement"] != c2
    assert read_state(path)["pending_drop"] is None


def test_an_interrupted_drop_is_redone_on_resume(tmp_path, undo):
    """印（`pending_drop`）が残ったまま再開したら、同じ取り消しをやり直す。"""
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    state["pending_drop"] = {"items": ["I-001"], "extra": [], "reason": "中断"}

    undo.resume_pending_drop(path, state)

    assert "line3-by-I-001" not in (work / "src" / "foo.py").read_text(encoding="utf-8")
    assert read_state(path)["pending_drop"] is None


def test_a_drop_interrupted_after_replay_keeps_the_remaining_item_on_resume(tmp_path, undo):
    """積み直しの後・状態の保存の前に落ちても、再開で残す項目を失わない。

    取り消しは revert と cherry-pick を積むだけで履歴を書き換えないため、中断後も
    旧 SHA は起点から HEAD の範囲に残り、状態の所有者表と対応が付く。
    """
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    before = copy.deepcopy(read_state(path))
    undo.drop(path, read_state(path), ["I-001"], "中断")
    # git は積み直しまで進み、状態は着手直後（印だけ立った旧 SHA のまま）で残った
    before["pending_drop"] = {"items": ["I-001"], "extra": [], "reason": "中断"}
    path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

    undo.resume_pending_drop(path, read_state(path))

    assert "line3-by-I-001" not in (work / "src" / "foo.py").read_text(encoding="utf-8")
    assert "changed-by-I-002" in (work / "src" / "foo.py").read_text(encoding="utf-8")
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-001"]["status"] == "reverted"
    assert items["I-002"]["status"] == "implemented"
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == items["I-002"]["commits"]["implement"]
    assert read_state(path)["pending_drop"] is None


def test_a_replay_conflict_without_a_shared_file_drops_every_item(tmp_path, undo):
    """広げる相手が無いまま積み直しが競合したら、計画の項目をすべて取り消す（`all`）。

    残す I-002 は、取り消す計画外のコミットの隣の行を触っている。I-002 は I-001 と
    同じファイルを触らないため広げる相手にならず、積み直しは競合する。
    """
    work, base, c1, _ = _repo(tmp_path, 30)
    git("reset", "-q", "--hard", c1, cwd=work)
    bar = list(LINES)
    bar[2] = "line3-unowned\n"
    (work / "src" / "bar.py").write_text("".join(bar), encoding="utf-8")
    extra = commit_with_trailers(work, "unowned", {})
    bar[3] = "changed-by-I-002\n"
    (work / "src" / "bar.py").write_text("".join(bar), encoding="utf-8")
    c2 = commit_with_trailers(work, "I-002", item_trailers("I-002"))
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    state["items"][1]["path"] = "src/bar.py"

    record = undo.drop(path, state, ["I-001"], "テスト", [extra])

    assert record["mode"] == "all"
    assert sorted(record["dropped"]) == ["I-001", "I-002"]
    for name in ("foo.py", "bar.py"):
        assert (work / "src" / name).read_text(encoding="utf-8") == "".join(LINES)
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-001"]["status"] == "reverted"
    assert items["I-002"]["status"] == "reverted"
    assert "巻き込まれた" in items["I-002"]["failure_reason"]
    assert read_state(path)["pending_drop"] is None
