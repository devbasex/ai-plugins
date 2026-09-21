"""適用担当が結果を残さなかったときの試行と担当の交代（#647 / #728）。

**結果ファイルを置かずに「群を開く → 取り込む」を繰り返す。** 変更前は同じ群が
上限なしに開き直され、担当が作ったコミットは検証を受けずに残っていた。
"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state, write_result

_ARGS_OPEN = {"id": 130, "round": 1}
_ARGS_MERGE = {"id": 130, "round": 1, "dry_run": False}


def _group(n, impl, items, **over):
    base = {
        "apply_round": n, "impl": impl,
        "impl_model": {"requested": None, "observed": None},
        "items": list(items), "status": "pending",
        "base_sha": None, "head_sha": None, "fix_rounds": 0, "attempt": 0,
    }
    base.update(over)
    return base


def _entry(groups, items):
    return {
        "round": 1, "impl": "codex", "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "merged": len(items),
        "adopted": len(items), "deferred": 0,
        "items": list(items),
        "apply_rounds": groups, "apply_round": 0,
        "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
        "fix_rounds": 0, "durations": {}, "reviews": [],
    }


def _item(item_id, path="src/a.py", symbol="f"):
    return {
        "item_id": item_id, "round": 1, "path": path, "symbol": symbol,
        "smell": "long_method", "technique": "extract_method",
        "severity": "major", "status": "pending", "commits": [],
        "estimated_diff_lines": 10,
    }


@pytest.fixture
def two_groups(tmp_path):
    """担当の違う 2 つの群を持つ状態ファイル。"""
    groups = [_group(1, "agy", ["R1-001"]), _group(2, "codex", ["R1-002"])]
    return make_state(
        tmp_path,
        items=[_item("R1-001"), _item("R1-002", path="src/b.py")],
        rounds=[_entry(groups, ["R1-001", "R1-002"])],
        phase="apply", outer_round=1, apply_seq=2,
    )


@pytest.fixture
def run(patch_lib, cmd_apply, env_tmp_dir, no_git):
    """群を開く・取り込むを、git を呼ばずに実行する。"""
    def _make(state_path, head="HEAD_NOW", in_range=None):
        env_tmp_dir(state_path)
        patch_lib("git_out", lambda work, args, **k: head)
        patch_lib("commits_in_range", lambda work, base, head_: list(in_range or []))

        def _open():
            return _exit_code(cmd_apply.cmd_next_apply_round,
                              type("A", (), dict(_ARGS_OPEN))())

        def _merge():
            return _exit_code(cmd_apply.cmd_merge_apply,
                              type("A", (), dict(_ARGS_MERGE))())

        return _open, _merge
    return _make


def _exit_code(fn, args):
    try:
        fn(args)
    except SystemExit as e:
        return e.code
    return 0


# ---------- 結果を残さない担当（AC9 / AC10 / AC12） ----------

def test_the_first_failure_swaps_the_agent_and_keeps_the_group_pending(
    two_groups, run
):
    """AC9: 1 回目の結果なしでは、群は未着手のまま担当が替わる。"""
    open_round, merge = run(two_groups)

    assert open_round() == 0
    assert merge() == 2

    group = read_state(two_groups)["rounds"][0]["apply_rounds"][0]
    assert group["status"] == "pending"
    assert group["impl"] != "agy"
    assert group["attempt"] == 1
    assert [(r["phase"], r["attempt"], r["impl"], r["reason"])
            for r in group["failed_attempts"]] == [("apply", 1, "agy", "missing")]


def test_the_second_failure_drops_the_group_and_defers_its_items(two_groups, run):
    """AC10: 2 回目も残さなければ取り消し、見送りの理由に担当と理由を並べる。"""
    open_round, merge = run(two_groups)
    open_round(); merge()
    second = read_state(two_groups)["rounds"][0]["apply_rounds"][0]["impl"]

    assert open_round() == 0
    assert merge() == 2

    state = read_state(two_groups)
    group = state["rounds"][0]["apply_rounds"][0]
    assert (group["status"], group["drop_reason"]) == ("dropped", "no_result")
    assert group["attempt"] == 2
    assert state["items"][0]["status"] == "abandoned"
    assert state["deferred_items"][0]["defer_reason"] == (
        f"実装担当が結果を残しませんでした（agy: missing → {second}: missing）"
    )


def test_a_broken_result_file_is_recorded_as_unparsable(two_groups, run):
    """AC12: JSON として読めない結果と配列の結果も、結果なしとして閉じる。"""
    open_round, merge = run(two_groups)
    open_round()
    (two_groups.parent / "agy-apply-r1-result.json").write_text(
        '{"items": [', encoding="utf-8")

    assert merge() == 2

    group = read_state(two_groups)["rounds"][0]["apply_rounds"][0]
    assert group["status"] == "pending"
    assert [r["reason"] for r in group["failed_attempts"]] == ["unparsable"]


def test_a_json_array_result_is_also_unparsable(two_groups, run):
    """AC12: 配列の結果ファイルも同じ扱いになる。"""
    open_round, merge = run(two_groups)
    open_round()
    write_result(two_groups, "agy-apply-r1", [{"item_id": "R1-001"}])

    assert merge() == 2

    group = read_state(two_groups)["rounds"][0]["apply_rounds"][0]
    assert [r["reason"] for r in group["failed_attempts"]] == ["unparsable"]


# ---------- 繰り返しが有限回で終わる（AC11） ----------

def test_the_rounds_run_out_after_two_attempts_per_group(two_groups, run):
    """AC11: 結果を 1 つも置かなければ、開く操作は 5 回目で尽きる。"""
    open_round, merge = run(two_groups)

    opened = 0
    while open_round() == 0:
        opened += 1
        merge()
        assert opened <= 8, "群を開く操作が止まらない"

    assert opened == 4
    groups = read_state(two_groups)["rounds"][0]["apply_rounds"]
    assert [g["status"] for g in groups] == ["dropped", "dropped"]


# ---------- 交代先の決め方（AC13 / AC14） ----------

def test_the_replacement_differs_from_the_agent_that_failed(tmp_path, run):
    """AC13: 輪番が 1 周しても、失敗した担当とは違う担当が出る。"""
    groups = [_group(n, "codex" if n == 1 else "agy", [f"R1-00{n}"])
              for n in range(1, 5)]
    state_path = make_state(
        tmp_path,
        items=[_item(f"R1-00{n}", path=f"src/{n}.py") for n in range(1, 5)],
        rounds=[_entry(groups, [f"R1-00{n}" for n in range(1, 5)])],
        phase="apply", outer_round=1, apply_seq=4,
    )
    open_round, merge = run(state_path)

    open_round(); merge()

    saved = read_state(state_path)["rounds"][0]["apply_rounds"]
    assert saved[0]["impl"] != "codex"
    assert [g["impl"] for g in saved[1:]] == ["agy", "agy", "agy"]
    assert read_state(state_path)["apply_seq"] > 4


def test_no_replacement_left_with_a_usage_limit_drops_the_group_at_once(
    two_groups, run, patch_lib
):
    """AC14: 交代先が無く利用上限なら、1 回目の失敗で群を取り消す。"""
    patch_lib("impl_for_seq", lambda state, seq: ("agy", None))
    open_round, merge = run(two_groups)
    open_round()
    (two_groups.parent / "agy-apply-r1-monitor.json").write_text(
        json.dumps({"reason": "usage_limit", "detail": "上限"}), encoding="utf-8")

    assert merge() == 2

    group = read_state(two_groups)["rounds"][0]["apply_rounds"][0]
    assert (group["status"], group["drop_reason"]) == ("dropped", "no_result")


def test_no_replacement_left_without_a_usage_limit_keeps_the_same_agent(
    two_groups, run, patch_lib
):
    """AC14: 交代先が無く、起動し直せる結末なら同じ担当で 2 回目を開く。"""
    patch_lib("impl_for_seq", lambda state, seq: ("agy", None))
    open_round, merge = run(two_groups)
    open_round()

    assert merge() == 2

    group = read_state(two_groups)["rounds"][0]["apply_rounds"][0]
    assert (group["status"], group["impl"]) == ("pending", "agy")
    assert open_round() == 0
    assert read_state(two_groups)["rounds"][0]["apply_rounds"][0]["attempt"] == 2


# ---------- 再開と試行の番号（AC15 / AC16 / AC17 / AC18） ----------

def test_opening_twice_without_a_merge_does_not_advance_the_attempt(two_groups, run):
    """AC15: 取り込みを挟まずに 2 回開いても、試行の番号は進まない。"""
    open_round, _ = run(two_groups)

    open_round()
    open_round()

    assert read_state(two_groups)["rounds"][0]["apply_rounds"][0]["attempt"] == 1


def test_an_unverified_baseline_stops_before_reading_the_result(
    tmp_path, patch_lib, cmd_apply, env_tmp_dir, no_git
):
    """AC16: 着手前のテストが成功と確認できていなければ、結果を読まずに止まる。"""
    groups = [_group(1, "agy", ["R1-001"])]
    state_path = make_state(
        tmp_path, items=[_item("R1-001")],
        rounds=[_entry(groups, ["R1-001"])], phase="apply", outer_round=1,
        baseline_test={"command": "pytest -q", "status": "red", "checked_at": "x"},
    )
    env_tmp_dir(state_path)
    patch_lib("git_out", lambda work, args, **k: "HEAD_NOW")
    read_calls: list[str] = []
    patch_lib("read_result",
              lambda *a, **k: read_calls.append("読んだ") or (_ for _ in ()).throw(
                  AssertionError("結果を読んではいけない")))

    code = _exit_code(cmd_apply.cmd_merge_apply, type("A", (), dict(_ARGS_MERGE))())

    assert code == 4
    assert read_calls == []
    assert read_state(state_path)["items"][0]["status"] == "blocked"


def test_every_exit_2_leaves_the_group_dropped_or_pending_with_a_record(
    two_groups, run
):
    """AC17: 終了コード 2 の後の群は、取り消し済みか、記録を持つ未着手である。"""
    open_round, merge = run(two_groups)

    for _ in range(4):
        if open_round() != 0:
            break
        assert merge() == 2
        for group in read_state(two_groups)["rounds"][0]["apply_rounds"]:
            if group["status"] == "pending" and group.get("attempt"):
                assert group.get("failed_attempts"), group
            else:
                assert group["status"] in {"pending", "dropped"}, group


def test_both_sides_follow_the_reopening_decision(two_groups, run, patch_lib):
    """AC18: 開き直しの判定を差し替えると、開く側も取り込み側も従う。"""
    patch_lib("group_reopening", lambda group: "exhausted")
    open_round, _ = run(two_groups)

    assert open_round() == 1

    groups = read_state(two_groups)["rounds"][0]["apply_rounds"]
    assert [g["status"] for g in groups] == ["dropped", "dropped"]
    assert [g["drop_reason"] for g in groups] == ["no_result", "no_result"]


# ---------- 輪番から担当を引く関数（AC49） ----------

def test_the_group_assignment_goes_through_the_single_rotation_function(
    rounds, monkeypatch
):
    """AC49: 輪番から担当を引く関数を差し替えると、群の担当がそれに従う。"""
    state = {"host": "claude", "models": {"kiro": "auto"}}

    impl, requested = rounds.impl_for_seq(state, 3)

    assert impl in {"claude", "codex", "agy", "kiro"}
    assert requested == state["models"].get(impl)
