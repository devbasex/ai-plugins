"""取り消した項目を次の提案の「対象外」へ入れる（#436 決定 10）。

**除外しないと、同じ項目が毎ラウンド再提案されて上限を消費する。** 実測では適用で
失敗した項目が 3 ランタイム全員から再提案され、合意数が最大になって最優先で採用
された。

対象は**改善項目とテスト項目の両方**である。鍵は改善項目が `path` + `symbol` +
`smell`、テスト項目が `target` + `case`（`rounds.item_key`）。
"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state, write_result

RUNTIMES = ["codex", "agy", "kiro"]


def _structure_item(item_id="R1-001", status="applied"):
    return {
        "item_id": item_id, "round": 1, "path": "src/foo.py",
        "symbol": "Foo.handle", "smell": "long_method",
        "technique": "extract_method", "severity": "major",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 10, "proposed_by": ["codex"],
        "status": status, "commits": ["sha-1"],
    }


def _test_item(item_id="R1-001", status="applied"):
    return {
        "item_id": item_id, "round": 1, "kind": "test",
        "path": "tests/test_foo.py", "target": "src/foo.py#Foo.handle",
        "case": "branch", "level": "unit",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 0, "proposed_by": ["codex"],
        "status": status, "commits": ["sha-1"],
    }


def _round(kind, item_ids):
    return {
        "round": 1, "kind": kind, "impl": "codex", "reviewers": ["agy", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "merged": 1, "adopted": 1,
        "deferred": 0, "items": list(item_ids),
        "apply_rounds": [{
            "apply_round": 1, "impl": "codex",
            "impl_model": {"requested": None, "observed": None},
            "items": list(item_ids), "status": "applied",
            "base_sha": "base0", "head_sha": None, "fix_rounds": 3,
        }],
        "apply_round": 1,
        "apply": {"apply_round": 1, "applied": list(item_ids), "failed": []},
        "fix_rounds": 3, "durations": {}, "reviews": [],
    }


def _abandon(refactor, state_path):
    refactor.cmd_abandon_items(
        type("A", (), {"id": 130, "round": 1, "dry_run": False})())
    return read_state(state_path)


# ---------- 取り消しが「対象外」へ残ること ----------

def test_an_abandoned_structure_item_is_recorded_with_its_key(
    refactor, tmp_path, env_tmp_dir, no_git
):
    state_path = make_state(tmp_path, items=[_structure_item()],
                            rounds=[_round("structure", ["R1-001"])])
    env_tmp_dir(state_path)

    state = _abandon(refactor, state_path)

    record = state["deferred_items"][0]
    assert (record["path"], record["symbol"], record["smell"]) == (
        "src/foo.py", "Foo.handle", "long_method")


def test_an_abandoned_test_item_is_recorded_with_its_key(
    refactor, tmp_path, env_tmp_dir, no_git
):
    """**テスト項目の鍵は `target` + `case` である**（決定 9）。"""
    state_path = make_state(tmp_path, items=[_test_item()],
                            rounds=[_round("test", ["R1-001"])])
    env_tmp_dir(state_path)

    state = _abandon(refactor, state_path)

    record = state["deferred_items"][0]
    assert record["kind"] == "test"
    assert (record["target"], record["case"]) == ("src/foo.py#Foo.handle", "branch")


# ---------- 次の提案から落ちること ----------

def _propose(refactor, state_path, payloads, round_no=2, kind="structure"):
    """次のラウンドを開いた状態で提案を統合し、その状態を返す。

    構造改善のラウンドは採用 0 件で終了コード 2 を返す（繰り返しを終える合図）。
    ここでは統合の結果だけを見るので、その終了は握って状態を読み直す。
    """
    state = read_state(state_path)
    state["rounds"].append({**_round(kind, []), "round": round_no,
                            "items": [], "adopted": 0, "merged": 0,
                            "apply_rounds": [], "apply_round": 0})
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    for runtime in RUNTIMES:
        write_result(state_path, f"{runtime}-propose-rf130-r{round_no}",
                     {"items": payloads})
    try:
        refactor.cmd_merge_proposals(type("A", (), {"id": 130})())
    except SystemExit as e:
        assert e.code == 2, f"想定外の終了コード: {e.code}"
    return read_state(state_path)


def test_an_abandoned_structure_item_is_not_adopted_again(
    refactor, tmp_path, env_tmp_dir, no_git
):
    state_path = make_state(tmp_path, items=[_structure_item()],
                            rounds=[_round("structure", ["R1-001"])])
    env_tmp_dir(state_path)
    _abandon(refactor, state_path)

    state = _propose(refactor, state_path, [{
        "path": "src/foo.py", "symbol": "Foo.handle", "smell": "long_method",
        "technique": "extract_method", "severity": "critical",
        "rationale": "また出す", "plan": "同じ手順", "test_gap": False,
        "estimated_diff_lines": 10,
    }])

    assert state["rounds"][1]["adopted"] == 0, "取り消した項目が再び採用されている"
    reasons = [d.get("defer_reason") for d in state["deferred_items"]]
    assert any("対象外" in str(r) for r in reasons)


def test_an_abandoned_test_item_is_not_adopted_again(
    refactor, tmp_path, env_tmp_dir, no_git
):
    """**改善項目とテスト項目の両方が対象である**（決定 10）。"""
    state_path = make_state(tmp_path, items=[_test_item()],
                            rounds=[_round("test", ["R1-001"])],
                            round_kind="test")
    env_tmp_dir(state_path)
    _abandon(refactor, state_path)

    state = _propose(refactor, state_path, [{
        "path": "tests/test_foo.py", "target": "src/foo.py#Foo.handle",
        "case": "branch", "level": "integration",
        "rationale": "また出す", "plan": "同じ手順",
    }], kind="test")

    assert state["rounds"][1]["adopted"] == 0, "取り消したテスト項目が再び採用されている"


def test_a_different_case_on_the_same_target_is_still_adopted(
    refactor, tmp_path, env_tmp_dir, no_git
):
    """鍵は `target` + `case` である。**入口だけで落とさない。**"""
    state_path = make_state(tmp_path, items=[_test_item()],
                            rounds=[_round("test", ["R1-001"])],
                            round_kind="test")
    env_tmp_dir(state_path)
    _abandon(refactor, state_path)

    state = _propose(refactor, state_path, [{
        "path": "tests/test_foo.py", "target": "src/foo.py#Foo.handle",
        "case": "boundary", "level": "unit",
        "rationale": "別の経路", "plan": "境界値を作る",
    }], kind="test")

    assert state["rounds"][1]["adopted"] == 1


# ---------- 提案プロンプトへ「対象外」として渡ること ----------

def _prompt_with_deferred(tmp_path, refactor, phase, deferred):
    """`launch-cli.sh` に提案プロンプトを組み立てさせ、本文を返す。"""
    import os
    import pathlib
    import subprocess

    launch = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
    runtime = "codex"
    state_path = make_state(tmp_path, deferred_items=deferred,
                            vocabulary=refactor.vocabulary(),
                            test_vocabulary=refactor.test_vocabulary())
    for name in ("work", runtime):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / runtime
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    subprocess.run(
        [str(launch), runtime, phase, "130", "1"],
        env={**os.environ,
             "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
             "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
        check=True, capture_output=True, text=True,
    )
    return (state_path.parent / f"{runtime}-propose-rf130-r1-prompt.md").read_text(
        encoding="utf-8")


def test_the_excluded_structure_item_reaches_the_prompt(refactor, tmp_path):
    text = _prompt_with_deferred(tmp_path, refactor, "propose", [{
        "item_id": "R1-001", "round": 1, "path": "src/foo.py",
        "symbol": "Foo.handle", "smell": "long_method",
        "defer_reason": "差分予算を超えた",
    }])
    assert "src/foo.py#Foo.handle" in text and "差分予算を超えた" in text


def test_the_excluded_test_item_reaches_the_prompt(refactor, tmp_path):
    """**見送りの記録は種類で形が違う。** `null` を並べた一覧にしない。"""
    text = _prompt_with_deferred(tmp_path, refactor, "propose-tests", [{
        "item_id": "R1-001", "round": 1, "kind": "test",
        "path": "tests/test_foo.py", "target": "src/foo.py#Foo.handle",
        "case": "branch", "defer_reason": "テストが通らなかった",
    }])
    assert "src/foo.py#Foo.handle" in text and "branch" in text
    assert "null" not in text
