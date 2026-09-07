"""テスト整備ラウンド（#436 決定 4 / 決定 9）のテスト。

**構造改善の提案を集める前に、足りていないテストを足す。** 提案ラウンドと同じ形
（提案 → 採否 → 適用ラウンド → 検証 → 修正ラウンド）を持ち、**適用ラウンドと
修正ラウンドは共有する**。

**新しい語彙は作らない。** 固定する経路の種類（`case`）と階層（`level`）だけを
閉じ、値は既存の 3 本の参照が持つ分類をそのまま使う。重複排除の鍵は
`target` + `case` の 2 つで、17 種の兆候に当たるものは要らない。
"""
from __future__ import annotations

import pytest

from crossref_helpers import make_state, read_state, write_result


def tprop(target="src/foo.py#handle", case="branch", **over):
    """テスト項目の提案 1 件。"""
    base = {
        "path": "tests/test_foo.py", "target": target, "case": case,
        "level": "unit", "rationale": "分岐 3 本のうち 2 本が固定されていない",
        "plan": "1. 正常系を書く\n2. 分岐ごとに入力を作る",
    }
    base.update(over)
    return base


def _merge(refactor, proposals, **kw):
    return refactor.merge_test_proposals(proposals, **kw)


# ---------- 重複排除の鍵は `target` + `case` ----------

def test_the_same_target_and_case_from_two_runtimes_merge_into_one(refactor):
    adopted, _ = _merge(refactor, {
        "codex": [tprop()],
        "agy": [tprop(rationale="もっと具体的な理由（分岐 3 本のうち 2 本が固定されておらず、"
                              "境界値も無い）をこちらが書いている")],
    })
    assert len(adopted) == 1
    assert adopted[0]["proposed_by"] == ["codex", "agy"]
    assert adopted[0]["rationale"].startswith("もっと具体的")  # 長い方を採る


def test_the_same_target_with_a_different_case_stays_separate(refactor):
    adopted, _ = _merge(refactor, {
        "codex": [tprop(case="branch"), tprop(case="boundary")],
    })
    assert len(adopted) == 2


def test_a_different_target_stays_separate(refactor):
    adopted, _ = _merge(refactor, {
        "codex": [tprop(target="src/a.py#f"), tprop(target="src/b.py#g")],
    })
    assert len(adopted) == 2


def test_the_level_is_not_part_of_the_key(refactor):
    """同じ経路を別の階層で 2 度固定させない。**低い階層を採る。**

    「上の階層へ持ち上げない」が採否の基準である（`testing-levels.md`）。
    """
    adopted, _ = _merge(refactor, {
        "codex": [tprop(level="e2e")],
        "agy": [tprop(level="unit")],
    })
    assert len(adopted) == 1
    assert adopted[0]["level"] == "unit"


# ---------- 語彙外は降格する ----------

def test_a_case_outside_the_vocabulary_is_deferred(refactor):
    adopted, deferred = _merge(refactor, {"codex": [tprop(case="分岐")]})
    assert adopted == []
    assert deferred[0]["case"] == "unknown"
    assert "語彙外" in deferred[0]["defer_reason"]


def test_a_level_outside_the_vocabulary_is_deferred(refactor):
    adopted, deferred = _merge(refactor, {"codex": [tprop(level="ui")]})
    assert adopted == []
    assert deferred[0]["level"] == "unknown"


def test_a_proposal_without_a_target_is_dropped(refactor):
    adopted, deferred = _merge(refactor, {"codex": [tprop(target="")]})
    assert adopted == [] and deferred == []


def test_the_vocabulary_comes_from_the_existing_references(refactor):
    """`case` は現状固定テストの表、`level` はテストの階層から採る（決定 9）。"""
    assert list(refactor.TEST_CASES) == ["normal", "branch", "boundary", "error"]
    assert list(refactor.TEST_LEVELS) == ["unit", "integration", "contract", "e2e"]
    assert "smells" not in refactor.test_vocabulary()


# ---------- 対象外と採用上限 ----------

def test_a_deferred_test_item_is_excluded_next_time(refactor):
    """決定 10 — 取り消した項目を次の提案から外す。鍵は `target` + `case`。"""
    adopted, deferred = _merge(
        refactor, {"codex": [tprop()]},
        excluded_keys={("src/foo.py#handle", "branch")},
    )
    assert adopted == []
    assert "対象外" in deferred[0]["defer_reason"]


def test_the_adoption_cap_applies(refactor):
    adopted, deferred = _merge(
        refactor,
        {"codex": [tprop(target=f"src/a.py#f{n}") for n in range(4)]},
        max_items=2,
    )
    assert len(adopted) == 2 and len(deferred) == 2


def test_the_most_agreed_test_item_comes_first(refactor):
    adopted, _ = _merge(refactor, {
        "codex": [tprop(target="src/a.py#alone"), tprop(target="src/b.py#agreed")],
        "agy": [tprop(target="src/b.py#agreed")],
    })
    assert adopted[0]["target"] == "src/b.py#agreed"


# ---------- 適用ラウンドを共有する ----------

def test_test_items_are_split_by_the_file_the_test_goes_into(refactor):
    """割り当ては改善項目と同じ関数が行う。見るのは**テストを足す先**である。"""
    adopted, _ = _merge(refactor, {"codex": [
        tprop(target="src/a.py#f", path="tests/test_a.py"),
        tprop(target="src/a.py#g", path="tests/test_a.py"),
        tprop(target="src/b.py#h", path="tests/test_b.py"),
    ]})
    groups = refactor.assign_apply_rounds(adopted)
    assert [[i["target"] for i in g] for g in groups] == [
        ["src/a.py#f", "src/b.py#h"], ["src/a.py#g"],
    ]


# ---------- 鍵と表示 ----------

def test_the_key_differs_by_the_kind_of_item(refactor):
    assert refactor.item_key({
        "kind": "test", "target": "src/a.py#f", "case": "branch"}) == (
        "src/a.py#f", "branch")
    assert refactor.item_key({
        "path": "src/a.py", "symbol": "f", "smell": "long_method"}) == (
        "src/a.py", "f", "long_method")


def test_the_label_names_the_file_and_the_symbol(refactor):
    """外へ出す文章は内部の識別子だけで書かない。"""
    assert refactor.item_label({
        "kind": "test", "target": "src/a.py#f", "path": "tests/test_a.py",
    }) == "src/a.py#f"
    assert refactor.item_label({"path": "src/a.py", "symbol": "f"}) == "src/a.py#f"


# ---------- ラウンドの開始 ----------

def _round(round_no, kind, **over):
    base = {
        "round": round_no, "kind": kind, "impl": "codex",
        "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "merged": 1, "adopted": 1,
        "deferred": 0, "items": [], "apply": {"applied": [], "failed": []},
        "fix_rounds": 0, "durations": {}, "reviews": [],
        "proposal_keys": [["src/a.py#f", "branch"]],
    }
    base.update(over)
    return base


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


def test_the_first_round_is_a_test_round(refactor, tmp_path, env_tmp_dir, capsys):
    """B4 — 初期化の後、最初の提案ラウンドの前にテスト整備ラウンドを置く。"""
    state_path = make_state(tmp_path, round_kind="test", max_test_rounds=2)
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())

    out = capsys.readouterr()
    assert "ROUND_KIND=test" in out.out
    assert "PROPOSE_PHASE=propose-tests" in out.out
    assert "テスト整備ラウンド 1 / 2" in out.err
    assert read_state(state_path)["rounds"][0]["kind"] == "test"


def test_a_state_without_the_declaration_opens_a_structure_round(
    refactor, tmp_path, env_tmp_dir, capsys
):
    """宣言の無い状態ファイル（前の版）は構造改善の提案ラウンドとして読む。"""
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())
    assert "PROPOSE_PHASE=propose" in capsys.readouterr().out
    assert read_state(state_path)["rounds"][0]["kind"] == "structure"


def test_the_structure_cap_counts_only_structure_rounds(
    refactor, tmp_path, env_tmp_dir
):
    """テスト整備ラウンドは `--max-outer-rounds` を食わない。"""
    state_path = make_state(
        tmp_path, max_outer_rounds=1, round_kind="structure",
        rounds=[_round(1, "test"), _round(2, "test")],
    )
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())
    assert read_state(state_path)["final"] is None


# ---------- 収束と上限（B4） ----------

def test_no_adopted_test_item_moves_on_to_the_structure_rounds(
    refactor, tmp_path, env_tmp_dir
):
    """収束の条件は**採用 0 件**。提案ラウンドと同じ形にする。"""
    state_path = make_state(
        tmp_path, round_kind="test", max_test_rounds=2,
        rounds=[_round(1, "test", adopted=0)],
    )
    env_tmp_dir(state_path)
    refactor.cmd_advance(_args())

    state = read_state(state_path)
    assert state["round_kind"] == "structure"
    assert state["final"] is None, "テスト整備の収束で進行を終わらせない"
    assert state["test_rounds_final"] == "no_more_test_proposals"


def test_the_test_rounds_stop_at_the_cap_even_with_items_left(
    refactor, tmp_path, env_tmp_dir
):
    """B4 — 上限に達したら採用が残っていても提案ラウンドへ進む。"""
    state_path = make_state(
        tmp_path, round_kind="test", max_test_rounds=2,
        rounds=[_round(1, "test"), _round(2, "test")],
    )
    env_tmp_dir(state_path)
    refactor.cmd_advance(_args())

    state = read_state(state_path)
    assert state["round_kind"] == "structure"
    assert state["test_rounds_final"] == "max_test_rounds"


def test_another_test_round_follows_while_items_are_still_adopted(
    refactor, tmp_path, env_tmp_dir
):
    state_path = make_state(
        tmp_path, round_kind="test", max_test_rounds=2,
        rounds=[_round(1, "test")],
    )
    env_tmp_dir(state_path)
    refactor.cmd_advance(_args())
    assert read_state(state_path)["round_kind"] == "test"


def test_the_duplicate_rate_compares_rounds_of_the_same_kind(
    refactor, tmp_path, env_tmp_dir
):
    """テスト整備ラウンドの提案を、構造改善の重複率の相手にしない。

    鍵の形が違うため重なりは常に 0 になり、**収束の判定が働かなくなる**。
    """
    keys = [["src/a.py", "A", "long_method"], ["src/b.py", "B", "duplication"]]
    state_path = make_state(
        tmp_path, max_outer_rounds=5, round_kind="structure",
        rounds=[
            _round(1, "structure", proposal_keys=keys),
            _round(2, "test", proposal_keys=[["src/a.py#f", "branch"]]),
            _round(3, "structure", proposal_keys=keys),
        ],
    )
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit):
        refactor.cmd_advance(_args())
    assert read_state(state_path)["final"] == "duplicate_proposals"


# ---------- 提案の取り込み ----------

def _entry(round_no=1, kind="test"):
    return {
        "round": round_no, "kind": kind, "impl": "codex",
        "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "merged": 0, "adopted": 0,
        "deferred": 0, "items": [],
        "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
        "fix_rounds": 0, "durations": {}, "reviews": [],
    }


def _run_merge(refactor, tmp_path, env_tmp_dir, monkeypatch, proposals, **over):
    state_path = make_state(
        tmp_path, rounds=[_entry()], phase="propose", outer_round=1,
        round_kind="test", max_test_rounds=2, **over)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: "base0")
    write_result(state_path, "codex-propose-rf130-r1", {"items": proposals})
    for runtime in ("agy", "kiro"):
        write_result(state_path, f"{runtime}-propose-rf130-r1", {"items": []})
    refactor.cmd_merge_proposals(_args())
    return read_state(state_path)


def test_merge_proposals_records_test_items_and_their_apply_rounds(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state = _run_merge(refactor, tmp_path, env_tmp_dir, monkeypatch, [
        tprop(target="src/a.py#f", path="tests/test_a.py"),
        tprop(target="src/a.py#g", path="tests/test_a.py"),
    ])
    assert [i["kind"] for i in state["items"]] == ["test", "test"]
    groups = state["rounds"][0]["apply_rounds"]
    assert [g["items"] for g in groups] == [["R1-001"], ["R1-002"]]
    assert state["phase"] == "apply"


def test_an_empty_test_round_does_not_end_the_run(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """テスト整備の採用 0 件は**構造改善へ進む合図**であって、終了ではない。"""
    state = _run_merge(refactor, tmp_path, env_tmp_dir, monkeypatch, [])
    assert state["final"] is None
    assert state["phase"] == "propose"


def test_an_empty_structure_round_still_ends_the_run(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = make_state(
        tmp_path, rounds=[_entry(kind="structure")], phase="propose",
        outer_round=1, round_kind="structure")
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: "base0")
    for runtime in ("codex", "agy", "kiro"):
        write_result(state_path, f"{runtime}-propose-rf130-r1", {"items": []})
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_proposals(_args())
    assert e.value.code == 2
    assert read_state(state_path)["final"] == "no_more_proposals"


def test_the_excluded_keys_of_a_test_round_use_target_and_case(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """見送った記録が次のラウンドの「対象外」へ入る（決定 10）。"""
    state = _run_merge(
        refactor, tmp_path, env_tmp_dir, monkeypatch,
        [tprop(target="src/a.py#f", case="branch")],
        deferred_items=[{
            "item_id": "R0-001", "kind": "test", "path": "tests/test_a.py",
            "target": "src/a.py#f", "case": "branch", "round": 0,
            "defer_reason": "適用結果の検証を通らなかった",
        }],
    )
    assert state["items"] == []
    assert state["rounds"][0]["adopted"] == 0
