"""適用ラウンドの割り当て（#436 決定 1）のテスト。

**採用した項目を、書き換えるファイルが重ならない群へ分ける。** 群の中の項目は
互いに独立していて 1 コミットへまとめられる。群と群は同じファイルを直列に
書き換えるため、順序に依存する。

分ける基準は**書き換えるファイルの一致**だけで、行の範囲は見ない
（提案の時点で行の範囲は分からない）。
"""
from __future__ import annotations

import pytest

from crossref_helpers import make_state, read_state, write_result


def prop(path="src/a.py", symbol="f", **over):
    base = {
        "path": path, "symbol": symbol, "smell": "long_method",
        "technique": "extract_method", "severity": "major",
        "rationale": "r", "plan": "p", "test_gap": False,
        "estimated_diff_lines": 10, "proposed_by": ["codex"],
    }
    base.update(over)
    return base


# ---------- 割り当ての規則 ----------

def test_items_on_different_files_share_one_apply_round(refactor):
    """書き換えるファイルが重ならない項目は、同じ群へ入る。"""
    groups = refactor.assign_apply_rounds(
        [prop(path="src/a.py"), prop(path="src/b.py"), prop(path="src/c.md")]
    )
    assert len(groups) == 1
    assert [i["path"] for i in groups[0]] == ["src/a.py", "src/b.py", "src/c.md"]


def test_items_on_the_same_file_go_to_different_apply_rounds(refactor):
    """A2 — 同じファイルを触る項目が同じ適用ラウンドに入らない。"""
    groups = refactor.assign_apply_rounds(
        [prop(path="src/a.py", symbol="f"), prop(path="src/a.py", symbol="g")]
    )
    assert len(groups) == 2
    assert [len(g) for g in groups] == [1, 1]


def test_a_third_item_on_the_same_file_needs_a_third_apply_round(refactor):
    """群の数に上限は置かない。**重なり方が決める。**"""
    groups = refactor.assign_apply_rounds([prop(path="src/a.py")] * 3)
    assert len(groups) == 3


def test_the_split_follows_the_design_example(refactor):
    """設計の例（X/Y が同じファイル、Z/W は別）と同じ分かれ方になる。"""
    x = prop(path="a.sh", symbol="X")
    y = prop(path="a.sh", symbol="Y")
    z = prop(path="b.py", symbol="Z")
    w = prop(path="c.md", symbol="W")
    groups = refactor.assign_apply_rounds([x, y, z, w])
    assert [[i["symbol"] for i in g] for g in groups] == [["X", "Z", "W"], ["Y"]]


def test_the_group_order_follows_the_adoption_order(refactor):
    """群の順序は採否の優先度（合意した数 → 重要度 → 差分行数）の順。

    `merge_proposals` が既にその順へ並べているため、割り当ては**渡された順**を
    崩さない。
    """
    first = prop(path="src/a.py", symbol="first")
    second = prop(path="src/a.py", symbol="second")
    third = prop(path="src/a.py", symbol="third")
    groups = refactor.assign_apply_rounds([first, second, third])
    assert [g[0]["symbol"] for g in groups] == ["first", "second", "third"]


def test_only_the_path_decides_the_split(refactor):
    """A3 — 分け方は提案が持つ `path` だけで決まる。

    同じファイルの別シンボル・別兆候でも、重なるものとして分かれる。
    """
    groups = refactor.assign_apply_rounds([
        prop(path="src/a.py", symbol="f", smell="long_method",
             estimated_diff_lines=999),
        prop(path="src/a.py", symbol="g", smell="duplicated_code",
             estimated_diff_lines=1),
    ])
    assert len(groups) == 2


def test_no_adopted_item_makes_no_apply_round(refactor):
    assert refactor.assign_apply_rounds([]) == []


# ---------- 状態への記録 ----------

def _round(items=()):
    return {
        "round": 1, "impl": "codex", "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {},
        "proposed": {}, "merged": 0, "adopted": 0, "deferred": 0,
        "items": list(items),
        "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
        "fix_rounds": 0, "durations": {}, "reviews": [],
    }


def _run_merge_proposals(refactor, tmp_path, env_tmp_dir, monkeypatch, proposals):
    state_path = make_state(tmp_path, rounds=[_round()], phase="propose",
                            outer_round=1)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: "base0")
    write_result(state_path, "codex-propose-rf130-r1", {"items": proposals})
    for runtime in ("agy", "kiro"):
        write_result(state_path, f"{runtime}-propose-rf130-r1", {"items": []})
    refactor.cmd_merge_proposals(type("A", (), {"id": 130})())
    return read_state(state_path)


def test_merge_proposals_records_the_apply_rounds(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """採用した項目が群として状態へ残る。"""
    state = _run_merge_proposals(refactor, tmp_path, env_tmp_dir, monkeypatch, [
        prop(path="src/a.py", symbol="f"),
        prop(path="src/a.py", symbol="g"),
        prop(path="src/b.py", symbol="h"),
    ])
    groups = state["rounds"][0]["apply_rounds"]
    assert [g["apply_round"] for g in groups] == [1, 2]
    assert [g["items"] for g in groups] == [["R1-001", "R1-003"], ["R1-002"]]


def test_every_item_knows_its_apply_round(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state = _run_merge_proposals(refactor, tmp_path, env_tmp_dir, monkeypatch, [
        prop(path="src/a.py", symbol="f"),
        prop(path="src/a.py", symbol="g"),
    ])
    assert [i["apply_round"] for i in state["items"]] == [1, 2]


def test_the_apply_round_cursor_starts_before_the_first_group(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """`next-apply-round` が最初の群を開くまで、進行中の群は無い。"""
    state = _run_merge_proposals(refactor, tmp_path, env_tmp_dir, monkeypatch,
                                 [prop()])
    assert state["rounds"][0]["apply_round"] == 0


def test_each_apply_round_gets_its_own_impl(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """適用の担当は適用ラウンドごとに輪番を進める。

    1 つの提案ラウンドが複数の群を持つとき、群ごとに次の担当へ渡す。提案ラウンド
    単位で 1 者に固定すると、群の数だけ 1 者が連続で適用することになる。
    """
    state = _run_merge_proposals(refactor, tmp_path, env_tmp_dir, monkeypatch, [
        prop(path="src/a.py", symbol="f"),
        prop(path="src/a.py", symbol="g"),
    ])
    groups = state["rounds"][0]["apply_rounds"]
    assert groups[0]["impl"] != groups[1]["impl"]


# ---------- 群を開く（next-apply-round） ----------

def _round_with_groups(groups, apply_round=0, items=("R1-001", "R1-002")):
    return {
        "round": 1, "impl": "codex", "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {},
        "proposed": {}, "merged": 2, "adopted": 2, "deferred": 0,
        "items": list(items),
        "apply_rounds": groups,
        "apply_round": apply_round,
        "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
        "fix_rounds": 0, "durations": {}, "reviews": [],
    }


def _two_groups():
    return [
        {"apply_round": 1, "impl": "codex",
         "impl_model": {"requested": None, "observed": None},
         "items": ["R1-001"], "status": "pending",
         "base_sha": None, "head_sha": None, "fix_rounds": 0},
        {"apply_round": 2, "impl": "agy",
         "impl_model": {"requested": None, "observed": None},
         "items": ["R1-002"], "status": "pending",
         "base_sha": None, "head_sha": None, "fix_rounds": 0},
    ]


def _open_next(refactor, tmp_path, env_tmp_dir, monkeypatch, entry, head="HEAD_NOW"):
    state_path = make_state(tmp_path, rounds=[entry], phase="apply", outer_round=1)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: head)
    refactor.cmd_next_apply_round(type("A", (), {"id": 130, "round": 1})())
    return state_path


def test_next_apply_round_opens_the_first_pending_group(
    refactor, tmp_path, env_tmp_dir, monkeypatch, capsys
):
    state_path = _open_next(refactor, tmp_path, env_tmp_dir, monkeypatch,
                            _round_with_groups(_two_groups()))
    out = capsys.readouterr().out
    assert "APPLY_ROUND=1" in out
    assert "IMPL=codex" in out
    assert "APPLY_ITEMS=R1-001" in out

    entry = read_state(state_path)["rounds"][0]
    assert entry["apply_round"] == 1
    # **群の起点はここで確定する。** 後続の群は先行の群を適用した後を読む
    assert entry["apply_base_sha"] == "HEAD_NOW"
    assert entry["apply_rounds"][0]["base_sha"] == "HEAD_NOW"


def test_next_apply_round_skips_the_groups_already_handled(
    refactor, tmp_path, env_tmp_dir, monkeypatch, capsys
):
    groups = _two_groups()
    groups[0]["status"] = "verified"
    state_path = _open_next(refactor, tmp_path, env_tmp_dir, monkeypatch,
                            _round_with_groups(groups, apply_round=1))
    assert "APPLY_ROUND=2" in capsys.readouterr().out
    assert read_state(state_path)["rounds"][0]["apply_round"] == 2


def test_next_apply_round_resets_the_fix_rounds(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """`--max-fix-rounds` は 1 つの適用ラウンドあたりの上限である。"""
    groups = _two_groups()
    groups[0]["status"] = "dropped"
    entry = _round_with_groups(groups, apply_round=1)
    entry["fix_rounds"] = 3
    state_path = _open_next(refactor, tmp_path, env_tmp_dir, monkeypatch, entry)
    assert read_state(state_path)["rounds"][0]["fix_rounds"] == 0


def test_next_apply_round_exits_1_when_no_group_is_left(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    groups = _two_groups()
    for g in groups:
        g["status"] = "verified"
    state_path = make_state(
        tmp_path, rounds=[_round_with_groups(groups, apply_round=2)],
        phase="apply", outer_round=1)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: "HEAD")
    with pytest.raises(SystemExit) as e:
        refactor.cmd_next_apply_round(type("A", (), {"id": 130, "round": 1})())
    assert e.value.code == 1


def test_next_apply_round_reopens_a_group_that_was_applied_but_not_verified(
    refactor, tmp_path, env_tmp_dir, monkeypatch, capsys
):
    """取り込み済みで検証前に落ちた群を飛ばさないこと。

    飛ばすと、その群の項目が採用でも取り消しでもないまま残る。再開できることは
    収束ループの前提である。**起点も修正の回数も動かさない。**
    """
    groups = _two_groups()
    groups[0]["status"] = "applied"
    groups[0]["base_sha"] = "BASE_OF_GROUP_1"
    entry = _round_with_groups(groups, apply_round=1)
    entry["fix_rounds"] = 2
    state_path = _open_next(refactor, tmp_path, env_tmp_dir, monkeypatch, entry)

    assert "APPLY_ROUND=1" in capsys.readouterr().out
    saved = read_state(state_path)["rounds"][0]
    assert saved["apply_base_sha"] == "BASE_OF_GROUP_1"
    assert saved["fix_rounds"] == 2
