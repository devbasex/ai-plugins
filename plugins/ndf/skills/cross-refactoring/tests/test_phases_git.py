"""テストの追加・実装・検証/修正・最終ゲートの取り込みを**実際の git**で確かめる（#933）。

| 受け入れ条件 | 確かめること |
| --- | --- |
| AC10 | 足したテストが今のコードで落ちた項目は `test_failed`、項目に紐づかないコミットは取り消す |
| AC11 | 1 改善項目 = 1 コミット（テストを足す項目は 2 コミット）。2 コミット以上は取り消す |
| AC12 | コミットの無い項目は持ち越し（`carried`。#1743 I15）。テストのコミットも取り消す。コミットの時刻では見送らない |
| AC13 AC14 | 検証は範囲テストだけ。全体のテストは危険フラグが立ったときに 1 度だけ（落ちたときの扱いは `test_whole_test_triage_git.py`） |
| AC15 AC16 | 修正の締め切りで項目だけを取り消す。共有した項目は新しい方から 1 件ずつ |
| AC16b | 最終ゲートは、検証の中の全体のテストが通り HEAD が進んでいなければ使い回す |
| AC17 | 単独起動は `cross-review` が `approved` のときだけ履歴へ追記する |
"""

from __future__ import annotations

import argparse
import json

import pytest

from crossref_helpers import (
    verify_judged,
    CALC,
    TEST_TOTAL,
    build_git_flow,
    commit_with_trailers,
    git,
    item_trailers,
    read_state,
    write_state,
)

PAST = "2000-01-01T00:00:00+00:00"


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(
    item_id,
    rank,
    symbol="total",
    *,
    tests=(),
    targets=("tests/test_calc.py",),
    estimated_diff_lines=20,
    technique="extract_method",
):
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/calc.py",
        "symbol": symbol,
        "smell": "long_method",
        "technique": technique,
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": list(tests),
        "test_targets": list(targets),
        "command": ["pytest", "-q", *targets],
        "command_source": "targets",
        "estimate": {"test": 2.7 if tests else 0.0, "implement": 1.3, "verify": 0.2},
        "round": 1,
        "status": "planned",
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": estimated_diff_lines,
    }


def _plan(flow, *items, **plan_overrides):
    state = read_state(flow["path"])
    state["items"] = list(items)
    state["plan"] = {
        "base_sha": git("rev-parse", "HEAD", cwd=flow["work"]).stdout.strip(),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
        "table_source": "defaults",
        **plan_overrides,
    }
    state["phase"] = "add-tests"
    write_state(flow["path"], state)


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _emitted(capsys, key):
    out = capsys.readouterr().out
    values = [line.split("=", 1)[1] for line in out.splitlines() if line.startswith(key + "=")]
    return values[-1] if values else None


def _write(work, rel, text):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _refactor_total(work):
    _write(
        work,
        "src/calc.py",
        CALC.replace(
            "    result = 0\n    for v in values:\n        result = add(result, v)\n    return result\n", "    return sum(values)\n"
        ),
    )


def _break_total(work):
    _write(work, "src/calc.py", CALC.replace("return result", "return result + 1"))


def _items(flow):
    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def _deferred(flow):
    return {d.get("item_id"): d["defer_reason"] for d in read_state(flow["path"])["deferred_items"]}


# ---------- テストの追加（merge-tests） ----------


def test_a_test_failing_on_the_current_code_is_sent_back_not_deferred(flow, cmd_setup, cmd_phases, cmd_implement):
    """#2457 の棚卸し #20: 今のコードで落ちるテストを足した項目は見送らず、テストを残して差し戻しに記録する。"""
    work = flow["work"]
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]), _item("I-002", 2, symbol="add"))
    _call(cmd_phases, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL.replace("== 6", "== 7"))
    commit_with_trailers(work, "Test", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_tests")

    assert _deferred(flow) == {}
    item = _items(flow)["I-001"]
    assert item["status"] == "tested"
    assert [r["kind"] for r in item["sendbacks"]] == ["test_failed"]
    assert _items(flow)["I-002"]["status"] == "planned"
    assert (work / "tests" / "test_total.py").exists()


def test_an_item_without_a_test_commit_is_carried(flow, cmd_setup, cmd_phases, cmd_implement):
    """AC12・I15: テストを足すはずの項目にコミットが無ければ持ち越し、見送りには入れない（採り直しの候補へ戻る）。"""
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]), _item("I-002", 2, symbol="add"))
    _call(cmd_phases, "cmd_start_phase", phase="add-tests")

    _call(cmd_implement, "cmd_merge_tests")

    assert _deferred(flow) == {}
    assert _items(flow)["I-001"]["status"] == "carried"


def test_a_late_test_commit_is_taken_in(flow, cmd_setup, cmd_phases, cmd_implement):
    """I13: 旧い状態ファイルの項目の着手期限を過ぎていても、コミットの時刻では見送らない。"""
    work = flow["work"]
    late = {**_item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]), "test_start_deadline": PAST}
    _plan(flow, late, _item("I-002", 2, symbol="add"))
    _call(cmd_phases, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL)
    commit_with_trailers(work, "Test", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_tests")

    assert _deferred(flow) == {}
    assert _items(flow)["I-001"]["status"] == "tested"
    assert (work / "tests" / "test_total.py").exists()


def test_a_commit_without_a_planned_item_is_reverted_alone(flow, cmd_setup, cmd_phases, cmd_implement):
    """AC10・#2457 の棚卸し #22: 改修計画に無いコミットだけを git revert で打ち消し、項目のテストは残す。"""
    work = flow["work"]
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]))
    _call(cmd_phases, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL)
    kept = commit_with_trailers(work, "Test", item_trailers("I-001"))
    _write(work, "tests/test_extra.py", "def test_x():\n    assert True\n")
    commit_with_trailers(work, "Test: 改修計画に無い", {"Impl-Runtime": "claude", "Impl-Model": "m"})

    _call(cmd_implement, "cmd_merge_tests")

    items = _items(flow)
    assert items["I-001"]["status"] == "tested"
    assert (work / "tests" / "test_total.py").exists()
    assert not (work / "tests" / "test_extra.py").exists()
    # 打ち消しは revert のコミットを積む（履歴を書き換えない）。項目のコミットの記録は元のまま
    recorded = items["I-001"]["commits"]["test"]
    assert recorded == kept
    assert git("rev-parse", "HEAD~2", cwd=work).stdout.strip() == kept
    assert [r["kind"] for r in read_state(flow["path"])["sendbacks"]] == ["unattributed"]


def test_a_test_commit_touching_production_code_rejects_the_item(flow, cmd_setup, cmd_phases, cmd_implement):
    """#2457 の棚卸し #21: テストの追加でテスト以外を変えても取り消さず、差し戻しとして報告に載せる。"""
    work = flow["work"]
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]), _item("I-002", 2, symbol="add"))
    _call(cmd_phases, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL)
    _refactor_total(work)
    commit_with_trailers(work, "Test", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_tests")

    item = _items(flow)["I-001"]
    assert item["status"] == "tested"
    assert "テスト以外" in item["sendbacks"][0]["reason"]
    assert _deferred(flow) == {}


# ---------- 実装（merge-implement） ----------


def _implement_phase(flow, cmd_setup, cmd_phases, *items):
    _plan(flow, *items)
    state = read_state(flow["path"])
    state["phase"] = "implement"
    write_state(flow["path"], state)
    _call(cmd_phases, "cmd_start_phase", phase="implement")


def test_one_commit_per_item_and_a_missing_item_is_carried(flow, cmd_setup, cmd_phases, cmd_implement):
    """AC11 AC12: 1 項目 = 1 コミット。コミットの無い項目は持ち越す（I15）。"""
    work = flow["work"]
    _implement_phase(flow, cmd_setup, cmd_phases, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    sha = commit_with_trailers(work, "Refactor", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_implement")

    items = _items(flow)
    assert items["I-001"]["status"] == "implemented"
    assert items["I-001"]["commits"]["implement"] == [sha]
    assert items["I-001"]["seconds"]["implement"] is not None
    assert _deferred(flow) == {}
    assert items["I-002"]["status"] == "carried"


def test_two_commits_for_one_item_are_taken_as_one_item(flow, cmd_setup, cmd_phases, cmd_implement):
    """#1814 AC6a: 同じ `Item-Id` の 2 コミット（改名と中身の変更など）を 1 項目として採る。所要は最後のコミットまで。"""
    work = flow["work"]
    _implement_phase(flow, cmd_setup, cmd_phases, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    first = commit_with_trailers(work, "Refactor 1", item_trailers("I-001"))
    _write(work, "src/calc.py", (work / "src" / "calc.py").read_text() + "\n")
    second = commit_with_trailers(work, "Refactor 2", item_trailers("I-001"))
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-002"))

    _call(cmd_implement, "cmd_merge_implement")

    items = _items(flow)
    assert items["I-001"]["status"] == "implemented"
    assert items["I-001"]["commits"]["implement"] == [first, second]
    assert items["I-002"]["status"] == "implemented"
    assert "sum(values)" in (work / "src" / "calc.py").read_text()


def test_a_commit_outside_the_scope_rejects_the_item(flow, cmd_setup, cmd_phases, cmd_implement):
    work = flow["work"]
    _implement_phase(flow, cmd_setup, cmd_phases, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    # 範囲の外の既存のファイルを、項目と関係なく変える（新しいファイルは範囲の中として扱う。#1814）
    _write(work, ".gitignore", (work / ".gitignore").read_text() + "build/\n")
    commit_with_trailers(work, "Refactor", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_implement")

    # #2457 受け入れ条件 8: 範囲の外の変更で取り消さず、`outside_scope` に残す
    item = _items(flow)["I-001"]
    assert item["status"] == "implemented"
    assert [r["path"] for r in item["outside_scope"]] == [".gitignore"]


def test_a_changed_expectation_rejects_the_item(flow, cmd_setup, cmd_phases, cmd_implement):
    """#2457 の棚卸し #25: 期待値を変えた実装は取り消さず、期待値を戻すよう修正へ差し戻す（#443 の一次の判定）。"""
    work = flow["work"]
    _implement_phase(flow, cmd_setup, cmd_phases, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _write(work, "src/calc.py", CALC.replace("return a + b", "return a + b + 0"))
    _write(work, "tests/test_calc.py", (work / "tests" / "test_calc.py").read_text().replace("== 3", "== 4"))
    commit_with_trailers(work, "Refactor", item_trailers("I-001"))
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-002"))

    _call(cmd_implement, "cmd_merge_implement")

    item = _items(flow)["I-001"]
    assert item["status"] == "failing" and item["failed_check"]["kind"] == "sendback"
    assert "期待する振る舞い" in item["sendbacks"][0]["reason"]


# ---------- 検証と修正（verify / merge-fix） ----------


def _verify_phase(flow, cmd_setup, cmd_phases, cmd_implement, *items, change=_refactor_total, item_id="I-001"):
    _implement_phase(flow, cmd_setup, cmd_phases, *items)
    change(flow["work"])
    commit_with_trailers(flow["work"], "Refactor", item_trailers(item_id))
    _call(cmd_implement, "cmd_merge_implement")


def test_a_failing_item_goes_to_fix_and_returns_after_the_fix(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, cmd_merge_fix, capsys):
    work = flow["work"]
    _verify_phase(
        flow,
        cmd_setup, cmd_phases,
        cmd_implement,
        _item("I-001", 1, targets=["tests/test_total.py"], tests=["tests/test_total.py"]),
        change=lambda w: (_write(w, "tests/test_total.py", TEST_TOTAL), _break_total(w)),
    )
    # 実装にテストのファイルを含めた形でも、期待値を変えなければ取り込む。
    # 初めて落ちた項目は実装担当と別のランタイムが成立を判定してから修正へ回る（#2457 決定 3）
    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    item = _items(flow)["I-001"]
    assert item["status"] == "failing"
    assert item["feasibility"][0]["feasible"] is True and item["feasibility"][0]["by"] != "claude"
    assert read_state(flow["path"])["fix"]["items"] == ["I-001"]

    _call(cmd_phases, "cmd_start_phase", phase="fix")
    _write(work, "src/calc.py", CALC)
    commit_with_trailers(work, "Fix", item_trailers("I-001"))
    _call(cmd_merge_fix, "cmd_merge_fix")
    items = _items(flow)
    assert items["I-001"]["fix_count"] == 1
    assert len(items["I-001"]["commits"]["fix"]) == 1

    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done"
    assert _items(flow)["I-001"]["status"] == "verified"
    stats = read_state(flow["path"])["fix_stats"]
    assert stats["launches"] == 1


def _two_items_one_broken(flow, cmd_setup, cmd_phases, cmd_implement):
    work = flow["work"]
    first = _item("I-001", 1, symbol="add", targets=["tests/test_calc.py"])
    broken = _item("I-002", 2, targets=["tests/test_total.py"], tests=["tests/test_total.py"])
    _implement_phase(flow, cmd_setup, cmd_phases, first, broken)
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-001"))
    _write(work, "tests/test_total.py", TEST_TOTAL)
    _break_total(work)
    commit_with_trailers(work, "Refactor total", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")
    return work


def test_an_item_judged_infeasible_is_reverted_alone(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys):
    """#2457 受け入れ条件 6・9: 成立しないと判定した項目だけを打ち消し、他の項目のコミットは残す。判定と根拠が残る。"""
    work = _two_items_one_broken(flow, cmd_setup, cmd_phases, cmd_implement)

    assert verify_judged(cmd_converge, flow["path"], capsys, feasible={"I-002": False}) == "done"

    items = _items(flow)
    assert items["I-001"]["status"] == "verified"
    assert items["I-002"]["status"] == "reverted" and items["I-002"]["revert_reason"] == "infeasible"
    assert items["I-002"]["feasibility"][-1]["feasible"] is False and items["I-002"]["feasibility"][-1]["evidence"]
    assert (work / "src" / "other.py").exists()
    assert "return result + 1" not in (work / "src" / "calc.py").read_text()


def test_time_does_not_revert_a_failing_item(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys):
    """#2457 受け入れ条件 5・7: 実装の予算をとうに超えていても、成立している項目は修正へ回り、取り消さない。"""
    _two_items_one_broken(flow, cmd_setup, cmd_phases, cmd_implement)
    state = read_state(flow["path"])
    state["started_at"] = "2000-01-01T00:00:00"
    state["budget"] = {"minutes": 1, "extension": None, "effective_minutes": 1}
    write_state(flow["path"], state)

    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    items = _items(flow)
    assert items["I-002"]["status"] == "failing" and items["I-001"]["status"] == "verified"


def test_a_stalled_fix_switches_the_fixer_once_then_reverts(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, cmd_merge_fix, capsys):
    """#2457 受け入れ条件 16・17: 落ちたテストが変わらない修正は停滞。判定し直して修正担当を 1 回替え、替えた後も停滞なら打ち消す。"""
    _two_items_one_broken(flow, cmd_setup, cmd_phases, cmd_implement)
    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    first_fixer = read_state(flow["path"])["fix"]["impl"]

    def no_fix():
        _call(cmd_phases, "cmd_start_phase", phase="fix")
        _call(cmd_merge_fix, "cmd_merge_fix")  # コミットを作らない（直さなかった。停滞として数える）

    no_fix()
    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    state = read_state(flow["path"])
    item = next(i for i in state["items"] if i["id"] == "I-002")
    assert item["fix_history"][-1]["progress"] == "stalled"
    assert item["fixer_switched"] is True and state["fix"]["impl"] != first_fixer
    assert [f["rejudge"] for f in item["feasibility"]] == [False, True]

    no_fix()
    assert verify_judged(cmd_converge, flow["path"], capsys) == "done"
    item = _items(flow)["I-002"]
    assert item["status"] == "reverted" and item["revert_reason"] == "stalled_after_switch"


def test_items_sharing_a_failing_command_both_go_to_fix(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys):
    """#2457 の棚卸し #30: 同じ範囲テストを共有した項目は新しい順に取り消さず、どちらも修正へ回して項目ごとに判定する。"""
    work = flow["work"]
    older = _item("I-001", 1, symbol="add", targets=["tests/test_calc.py"])
    newer = _item("I-002", 2, targets=["tests/test_calc.py"])
    _implement_phase(flow, cmd_setup, cmd_phases, older, newer)
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-001"))
    _write(
        work,
        "tests/test_calc.py",
        (work / "tests" / "test_calc.py").read_text() + "\nfrom src.calc import total\n\n\n"
        "def test_total_is_sum():\n    assert total([1, 2]) == 3\n",
    )
    _break_total(work)
    commit_with_trailers(work, "Refactor total", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")

    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    items = _items(flow)
    assert {items["I-001"]["status"], items["I-002"]["status"]} == {"failing"}
    assert all(i["feasibility"] for i in items.values())
    assert sorted(read_state(flow["path"])["fix"]["items"]) == ["I-001", "I-002"]


def test_the_whole_test_runs_once_when_a_danger_flag_is_raised_and_the_gate_reuses_it(
    flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, cmd_gate, capsys
):
    """AC13 AC14 AC16b: ファイルを消した項目（D2）で全体のテストを 1 度。最終ゲートは使い回す。"""

    def delete(w):
        git("rm", "-q", "src/__init__.py", cwd=w)

    _verify_phase(flow, cmd_setup, cmd_phases, cmd_implement, _item("I-001", 1), change=delete)
    _call(cmd_converge, "cmd_verify")
    state = read_state(flow["path"])
    assert state["whole_test"]["ran"] is True
    assert "D2" in state["whole_test"]["flags"]
    assert state["whole_test"]["status"] == "pass"

    _call(cmd_gate, "cmd_final_gate")
    gate = read_state(flow["path"])["final_gate"]
    assert gate["status"] == "passed"
    assert gate.get("whole_test_reused") is True
    assert gate["checks"] == []


def test_a_whole_test_failure_goes_to_fix_without_reverting(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys):
    """#2457 の棚卸し #34: 危険フラグの全体テストの変更起因は、締め切りで取り消さず原因の項目の修正へ回す。"""
    work = flow["work"]

    def rename_and_break_outside(w):
        # `total` を消す（範囲テスト tests/test_calc.py は `add` しか見ない）。
        git("rm", "-q", "src/__init__.py", cwd=w)
        _write(w, "src/calc.py", "def add(a, b):\n    return a + b\n")

    item = _item("I-001", 1, symbol="add", targets=["tests/test_calc.py"])
    _write(work, "tests/test_total.py", TEST_TOTAL)
    commit_with_trailers(work, "既存のテスト", {})
    _verify_phase(flow, cmd_setup, cmd_phases, cmd_implement, item, change=rename_and_break_outside)
    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    state = read_state(flow["path"])
    assert state["whole_test"]["status"] == "fail" and not state["whole_test"].get("reverted")
    assert _items(flow)["I-001"]["status"] == "failing"


# ---------- 最終ゲートと履歴（final-gate / finalize） ----------


def test_standalone_runs_cross_review_and_appends_history_only_when_approved(flow, cmd_gate, cmd_report, capsys):
    """AC16b AC17: 単独起動は全体のテストの後に cross-review。approved のときだけ追記する。"""
    state = read_state(flow["path"])
    state["workflow_step"] = False
    state["phase"] = "final"
    write_state(flow["path"], state)

    capsys.readouterr()
    _call(cmd_gate, "cmd_final_gate")
    assert _emitted(capsys, "FINAL_GATE") == "cross-review"
    history = flow["metrics"] / "acme--demo" / "cross-refactoring-allocation.jsonl"

    _call(cmd_report, "cmd_finalize", review_status=None)
    assert not history.exists()
    _call(cmd_report, "cmd_finalize", review_status="max_rounds")
    assert not history.exists()
    _call(cmd_report, "cmd_finalize", review_status="unverified")  # スイープが未検証の承認は通った実行にしない（#1656）
    assert not history.exists()
    _call(cmd_report, "cmd_finalize", review_status="approved")
    assert len(history.read_text(encoding="utf-8").splitlines()) == 1
    row = json.loads(history.read_text(encoding="utf-8"))
    assert row["whole_test"]["final"] is not None


def test_a_failed_gate_does_not_append_history(flow, cmd_gate, cmd_report, monkeypatch):
    state = read_state(flow["path"])
    # 最終ゲートの全体テストは戦略の suite の `command`（#1334）。
    state["strategy"]["suites"][0]["command"] = "false"
    write_state(flow["path"], state)
    with pytest.raises(SystemExit):
        _call(cmd_gate, "cmd_final_gate")
    _call(cmd_report, "cmd_finalize", review_status=None)
    assert not (flow["metrics"] / "acme--demo" / "cross-refactoring-allocation.jsonl").exists()


def test_a_resumed_intake_reuses_its_conclusion_and_does_not_drop_twice(flow, cmd_setup, cmd_phases, cmd_implement):
    """取り消しの後に落ちて再開しても、積み直したコミットを 2 コミット目と数えない。"""
    work = flow["work"]
    _implement_phase(flow, cmd_setup, cmd_phases, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    commit_with_trailers(work, "Refactor", item_trailers("I-001"))
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "改修計画に無い", {"Impl-Runtime": "claude", "Impl-Model": "m"})
    _call(cmd_implement, "cmd_merge_implement")
    head = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    state = read_state(flow["path"])
    state["phases"]["implement"].pop("ended_at")  # 終わりを書く前に落ちたことにする
    write_state(flow["path"], state)

    _call(cmd_implement, "cmd_merge_implement")

    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == head
    assert _items(flow)["I-001"]["status"] == "implemented"
    assert len(read_state(flow["path"])["drops"]) == 1


# ---------- 監視が手順の上限で CLI を止めたとき（決定 23 / I15） ----------


def test_a_phase_stopped_by_the_monitor_takes_in_every_commit_and_reports_it(flow, cmd_setup, cmd_phases, cmd_implement, cmd_report, capsys):
    """止めたときは未コミットの変更を捨て、コミット済みの項目は時刻に依らず取り込み、報告に出す（#1743 I13）。"""
    work = flow["work"]
    done = _item("I-001", 1, symbol="add")
    late = {**_item("I-002", 2), "start_deadline": PAST}  # 旧い状態ファイルの着手期限は読まない
    _implement_phase(flow, cmd_setup, cmd_phases, done, late)
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-001"))
    _refactor_total(work)
    commit_with_trailers(work, "Refactor total", item_trailers("I-002"))
    _write(work, "src/half.py", "Y = 1\n")  # 止められた時点の書きかけ
    (flow["path"].parent / "claude-implement-rf130-monitor.json").write_text(
        json.dumps({"reason": "timeout", "ended_at": "2026-09-24T10:40:00"}), encoding="utf-8"
    )

    _call(cmd_implement, "cmd_merge_implement")

    state = read_state(flow["path"])
    assert state["phases"]["implement"]["stopped"]["reason"] == "timeout"
    assert not (work / "src" / "half.py").exists()
    assert _items(flow)["I-001"]["status"] == "implemented"
    assert _items(flow)["I-002"]["status"] == "implemented"
    assert "I-002" not in _deferred(flow)
    capsys.readouterr()
    _call(cmd_report, "cmd_report", metrics=False)
    assert "implement（上限" in capsys.readouterr().out
