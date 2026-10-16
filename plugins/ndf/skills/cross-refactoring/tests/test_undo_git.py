"""項目の単位の取り消しと積み直しを**実際の git** で確かめる（#933 の AC15、実装計画 I6）。

| 位置関係 | 結果 |
| --- | --- |
| 別ファイル / 離れた行 | 項目だけを取り消し、残す項目は積み直せる（`item`） |
| 同一ファイルの隣接行 | 積み直せない。衝突したコミットの項目だけを外す（`ejected`。#1793 の R2） |
| 外した項目に依存する項目 | 同じく衝突して外れる。止まらずに続ける |

**隣接する変更は git だけでは分離できない。** 取り消した側の行が消えると、残す側の
パッチが前提にしている文脈も消えるためである。外すのは衝突したコミットの項目だけで、
ファイルが同じというだけでは外さない（`test_undo_eject_git.py`）。
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
        {
            "id": "I-001",
            "rank": 1,
            "path": "src/foo.py",
            "symbol": "a",
            "status": "implemented",
            "commits": {"test": None, "implement": c1, "fix": []},
        },
        {
            "id": "I-002",
            "rank": 2,
            "path": "src/foo.py",
            "symbol": "b",
            "status": "implemented",
            "commits": {"test": None, "implement": c2, "fix": []},
        },
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
    [new_sha] = items["I-002"]["commits"]["implement"]
    assert new_sha != c2
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == new_sha
    assert read_state(path)["pending_drop"] is None


def test_an_adjacent_change_ejects_the_item_whose_commit_conflicted(tmp_path, undo):
    """I2: 衝突したコミットの項目を外し、理由に取り消した項目と衝突したコミットの 12 桁を残す。"""
    work, base, c1, c2 = _repo(tmp_path, 3)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    state["items"][1]["failure_reason"] = "自分の範囲テストで落ちた"

    record = undo.drop(path, state, ["I-001"], "テスト")

    assert record["mode"] == "ejected"
    assert record["ejected"] == [{"item": "I-002", "commit": c2, "by": ["I-001"]}]
    assert (work / "src" / "foo.py").read_text(encoding="utf-8") == "".join(LINES)
    saved = read_state(path)
    items = {i["id"]: i for i in saved["items"]}
    assert items["I-002"]["status"] == "reverted"
    assert items["I-002"]["failure_reason"] == f"I-001 の取り消しで {c2[:12]} の積み直しが衝突したため外した"
    assert [d["mode"] for d in saved["drops"]] == ["ejected"]


def test_an_item_without_commits_is_closed_without_touching_git(tmp_path, undo):
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    state["items"].append({"id": "I-003", "rank": 3, "status": "planned", "commits": {"test": None, "implement": None, "fix": []}})
    head = git("rev-parse", "HEAD", cwd=work).stdout.strip()

    record = undo.drop(path, state, ["I-003"], "テスト")

    assert record["mode"] == "skip"
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == head


def test_an_unowned_extra_commit_is_removed_and_older_commits_are_kept(tmp_path, undo):
    """どの項目にも記録されていないコミットは消え、それより古い項目のコミットは積み直さずに残る。"""
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    (work / "src" / "extra.py").write_text("unowned\n", encoding="utf-8")
    extra = commit_with_trailers(work, "unowned", {})

    record = undo.discard_range(path, state, "改修計画外")

    assert record == {
        "at": record["at"],
        "mode": "item",
        "reason": "改修計画外",
        "round": 1,
        "dropped": [],
        "ejected": [],
        "recheck": [],
        "extra": [extra],
        "origin": c2,
        "removed": 1,
        "replayed": 0,
        "reverted": 0,
    }
    assert not (work / "src" / "extra.py").exists()
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-001"]["commits"]["implement"] == [c1]
    assert items["I-002"]["commits"]["implement"] == [c2]
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == c2
    assert read_state(path)["pending_drop"] is None


def test_an_interrupted_drop_is_redone_on_resume(tmp_path, undo):
    """フラグ（`pending_drop`）が残ったまま再開したら、同じ取り消しをやり直す。"""
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    state["pending_drop"] = {"items": ["I-001"], "extra": [], "reason": "中断"}

    undo.resume_pending_drop(path, state)

    assert "line3-by-I-001" not in (work / "src" / "foo.py").read_text(encoding="utf-8")
    assert read_state(path)["pending_drop"] is None


def test_a_drop_interrupted_after_replay_keeps_the_remaining_item_on_resume(tmp_path, undo):
    """積み直しの後・状態の保存の前に落ちても、再開で残す項目を失わない（I7）。

    再開は記録した取り消しの前の HEAD（`pending_drop.before`）へ戻してから計画を作り直す。
    """
    work, base, c1, c2 = _repo(tmp_path, 30)
    path = _state(tmp_path, work, base, c1, c2)
    before = copy.deepcopy(read_state(path))
    undo.drop(path, read_state(path), ["I-001"], "中断")
    finished = read_state(path)
    # git は積み直しまで進み、状態は着手直後（フラグだけ立った旧 SHA のまま）で残った
    before["pending_drop"] = {"items": ["I-001"], "reason": "中断", "before": c2}
    path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

    undo.resume_pending_drop(path, read_state(path))

    assert "line3-by-I-001" not in (work / "src" / "foo.py").read_text(encoding="utf-8")
    assert "changed-by-I-002" in (work / "src" / "foo.py").read_text(encoding="utf-8")
    resumed = read_state(path)
    items = {i["id"]: i for i in resumed["items"]}
    assert items["I-001"]["status"] == "reverted"
    assert items["I-002"]["status"] == "implemented"
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == items["I-002"]["commits"]["implement"][-1]
    assert (
        git("rev-parse", "HEAD^{tree}", cwd=work).stdout.strip()
        == git("rev-parse", f"{finished['items'][1]['commits']['implement'][-1]}^{{tree}}", cwd=work).stdout.strip()
    )
    assert len(resumed["drops"]) == 1
    assert resumed["pending_drop"] is None


def test_items_depending_on_an_ejected_item_are_ejected_in_turn(tmp_path, undo):
    """依存の連なり: I-002 は I-001 の隣の行、I-003 は I-002 の bar.py の隣の行を触る。I-001 を取り消すと
    I-002 が外れ、続けて I-003 も外れる。終了コード 4 で止まらない。"""
    work, base, c1, _ = _repo(tmp_path, 30)
    git("reset", "-q", "--hard", c1, cwd=work)
    foo = (work / "src" / "foo.py").read_text(encoding="utf-8").splitlines(keepends=True)
    foo[3] = "changed-by-I-002\n"
    (work / "src" / "foo.py").write_text("".join(foo), encoding="utf-8")
    bar = list(LINES)
    bar[2] = "line3-by-I-002\n"
    (work / "src" / "bar.py").write_text("".join(bar), encoding="utf-8")
    c2 = commit_with_trailers(work, "I-002", item_trailers("I-002"))
    bar[3] = "line4-by-I-003\n"
    (work / "src" / "bar.py").write_text("".join(bar), encoding="utf-8")
    c3 = commit_with_trailers(work, "I-003", item_trailers("I-003"))
    path = _state(tmp_path, work, base, c1, c2)
    state = read_state(path)
    state["items"].append(
        {"id": "I-003", "rank": 3, "path": "src/bar.py", "status": "implemented", "commits": {"test": None, "implement": c3, "fix": []}}
    )

    record = undo.drop(path, state, ["I-001"], "テスト")

    assert record["ejected"] == [{"item": "I-002", "commit": c2, "by": ["I-001"]}, {"item": "I-003", "commit": c3, "by": ["I-001"]}]
    assert record["dropped"] == ["I-001", "I-002", "I-003"]
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == base
    items = {i["id"]: i for i in read_state(path)["items"]}
    assert [items[i]["status"] for i in ("I-001", "I-002", "I-003")] == ["reverted"] * 3


def test_remap_rewrites_item_commits_and_saved_points_through_full_shas(tmp_path, undo):
    """現状固定: `_remap` は短い SHA も完全な SHA へ解決して引き、対応表に無い値はそのまま残す。

    項目のコミット・`fix` の並び・オーケストレーターのコミットは `mapping` で、フェーズと修正と
    最終ゲートの起点は `points` で書き直す。最終ゲートの `fix_commits` は `mapping` で書き直す。
    """
    work, base, c1, c2 = _repo(tmp_path, 30)
    mapping = {c1: "new-c1", c2: "new-c2"}
    points = {c1: "point-c1", base: "point-base"}
    state = {
        "items": [
            {"id": "I-001", "commits": {"test": c1[:10], "implement": c2, "fix": [c1[:8], "unknown"]}},
            {"id": "I-002", "commits": {"test": None, "implement": base}},
            {"id": "I-003"},
        ],
        "phases": {"a": {"base_sha": c1[:12]}, "b": {"base_sha": ""}, "c": "not-a-dict", "d": {"base_sha": c2}},
        "fix": {"base_sha": base},
        "final_gate": {"fix_base_sha": c1, "fix_commits": [c2, "zzz"]},
        "ledger": {"orchestrator_commits": [c1, "other"]},
    }

    undo._remap(state, str(work), mapping, points)

    assert state["items"][0]["commits"] == {"test": "new-c1", "implement": ["new-c2"], "fix": ["new-c1", "unknown"]}
    assert state["items"][1]["commits"] == {"test": None, "implement": [base], "fix": []}
    assert state["items"][2] == {"id": "I-003"}
    assert state["phases"] == {"a": {"base_sha": "point-c1"}, "b": {"base_sha": ""}, "c": "not-a-dict", "d": {"base_sha": c2}}
    assert state["fix"] == {"base_sha": "point-base"}
    assert state["final_gate"] == {"fix_base_sha": "point-c1", "fix_commits": ["new-c2", "zzz"]}
    # オーケストレーターのコミットは完全な SHA のまま引く（短い SHA を解決しない）
    assert state["ledger"]["orchestrator_commits"] == ["new-c1", "other"]


def test_remap_leaves_a_state_without_optional_sections_alone(tmp_path, undo):
    """現状固定: `phases` / `fix` / `final_gate` が無い・空でも落ちず、台帳だけが用意される。"""
    work, _, c1, _ = _repo(tmp_path, 30)
    state = {"items": None, "fix": "not-a-dict", "final_gate": {}}

    undo._remap(state, str(work), {c1: "x"}, {})

    assert state == {"items": None, "fix": "not-a-dict", "final_gate": {}, "ledger": {"orchestrator_commits": []}}
