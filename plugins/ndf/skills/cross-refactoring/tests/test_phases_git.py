"""テストの追加・実装・検証/修正・最終ゲートの取り込みを**実際の git**で確かめる（#933）。

| 受け入れ条件 | 確かめること |
| --- | --- |
| AC10 | 足したテストが今のコードで落ちた項目は `test_failed`、項目に紐づかないコミットは取り消す |
| AC11 | 1 改善項目 = 1 コミット（テストを足す項目は 2 コミット）。2 コミット以上は取り消す |
| AC12 | コミットの無い項目・完了の締め切りを過ぎた項目は `not_done`。テストのコミットも取り消す |
| AC13 AC14 | 検証は限ったテストだけ。全体のテストは印が立ったときに 1 度だけ |
| AC15 AC16 | 修正の上限で項目だけを取り消す。共有した項目は新しい方から 1 件ずつ |
| AC16b | 最終ゲートは、検証の中の全体のテストが通り HEAD が進んでいなければ使い回す |
| AC17 | 単独起動は `cross-review` が `approved` のときだけ履歴へ追記する |
"""
from __future__ import annotations

import argparse
import json

import pytest

from crossref_helpers import (
    CALC,
    TEST_TOTAL,
    build_git_flow,
    commit_with_trailers,
    git,
    item_trailers,
    read_state,
    write_state,
)

FAR = "2099-01-01T00:00:00+00:00"
PAST = "2000-01-01T00:00:00+00:00"


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(item_id, rank, symbol="total", *, tests=(), targets=("tests/test_calc.py",),
          deadline=FAR, test_deadline=FAR, estimated_diff_lines=20, technique="extract_method"):
    return {
        "id": item_id, "rank": rank, "path": "src/calc.py", "symbol": symbol,
        "smell": "long_method", "technique": technique, "severity": "major",
        "proposed_by": ["codex"], "tier": "high", "risk": False,
        "tests": list(tests), "test_targets": list(targets),
        "command": ["pytest", "-q", *targets], "command_source": "targets",
        "estimate": {"test": 2.7 if tests else 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": deadline, "test_start_deadline": test_deadline if tests else None,
        "status": "planned", "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {}, "fix_count": 0, "danger": [],
        "estimated_diff_lines": estimated_diff_lines,
    }


def _plan(flow, *items, **plan_overrides):
    state = read_state(flow["path"])
    state["items"] = list(items)
    state["plan"] = {
        "base_sha": git("rev-parse", "HEAD", cwd=flow["work"]).stdout.strip(),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
        "end_at": FAR, "table_source": "defaults", **plan_overrides,
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
    _write(work, "src/calc.py", CALC.replace(
        "    result = 0\n    for v in values:\n        result = add(result, v)\n    return result\n",
        "    return sum(values)\n"))


def _break_total(work):
    _write(work, "src/calc.py", CALC.replace("return result", "return result + 1"))


def _items(flow):
    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def _deferred(flow):
    return {d.get("item_id"): d["defer_reason"] for d in read_state(flow["path"])["deferred_items"]}


# ---------- テストの追加（merge-tests） ----------

def test_a_test_failing_on_the_current_code_defers_the_item_as_test_failed(
        flow, cmd_setup, cmd_implement):
    """AC10: 今のコードで落ちるテストを足した項目は見送り、そのテストのコミットを取り消す。"""
    work = flow["work"]
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]),
          _item("I-002", 2, symbol="add"))
    _call(cmd_setup, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL.replace("== 6", "== 7"))
    commit_with_trailers(work, "Test", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_tests")

    assert _deferred(flow) == {"I-001": "test_failed"}
    # 見送った項目は取り消しと別に数える（設計の状態遷移 planned --> deferred）。
    assert _items(flow)["I-001"]["status"] == "deferred"
    assert _items(flow)["I-002"]["status"] == "planned"
    assert not (work / "tests" / "test_total.py").exists()


def test_an_item_without_a_test_commit_is_not_done(flow, cmd_setup, cmd_implement):
    """AC12: テストを足すはずの項目にコミットが無ければ `not_done`。"""
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]),
          _item("I-002", 2, symbol="add"))
    _call(cmd_setup, "cmd_start_phase", phase="add-tests")

    _call(cmd_implement, "cmd_merge_tests")

    assert _deferred(flow) == {"I-001": "not_done"}


def test_a_test_commit_after_its_completion_deadline_is_not_done(flow, cmd_setup, cmd_implement):
    """AC12: 完了の締め切り（着手の締め切り + 見積り）を過ぎたコミットは取り込まない。"""
    work = flow["work"]
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"],
                      test_deadline=PAST),
          _item("I-002", 2, symbol="add"))
    _call(cmd_setup, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL)
    commit_with_trailers(work, "Test", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_tests")

    assert _deferred(flow) == {"I-001": "not_done"}
    assert _items(flow)["I-001"]["status"] == "deferred"
    assert not (work / "tests" / "test_total.py").exists()


def test_a_commit_without_a_planned_item_is_reverted_alone(flow, cmd_setup, cmd_implement):
    """AC10: 計画に無いテストのコミットだけを取り消し、項目のテストは残す。"""
    work = flow["work"]
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]))
    _call(cmd_setup, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL)
    kept = commit_with_trailers(work, "Test", item_trailers("I-001"))
    _write(work, "tests/test_extra.py", "def test_x():\n    assert True\n")
    commit_with_trailers(work, "Test: 計画に無い", {"Impl-Runtime": "claude", "Impl-Model": "m"})

    _call(cmd_implement, "cmd_merge_tests")

    items = _items(flow)
    assert items["I-001"]["status"] == "tested"
    assert (work / "tests" / "test_total.py").exists()
    assert not (work / "tests" / "test_extra.py").exists()
    # 積み直しで SHA が変わっても、記録は履歴にあるコミットを指す。
    recorded = items["I-001"]["commits"]["test"]
    assert git("cat-file", "-t", recorded, cwd=work).stdout.strip() == "commit"
    assert recorded != kept          # 積み直したコミットを指す


def test_a_test_commit_touching_production_code_rejects_the_item(flow, cmd_setup, cmd_implement):
    """I5: テストの追加でテスト以外を変えた項目は取り消す。見送りには入れない。"""
    work = flow["work"]
    _plan(flow, _item("I-001", 1, tests=["tests/test_total.py"], targets=["tests/test_total.py"]),
          _item("I-002", 2, symbol="add"))
    _call(cmd_setup, "cmd_start_phase", phase="add-tests")
    _write(work, "tests/test_total.py", TEST_TOTAL)
    _refactor_total(work)
    commit_with_trailers(work, "Test", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_tests")

    assert _items(flow)["I-001"]["status"] == "reverted"
    assert "テスト以外" in _items(flow)["I-001"]["failure_reason"]
    assert _deferred(flow) == {}


# ---------- 実装（merge-implement） ----------

def _implement_phase(flow, cmd_setup, *items):
    _plan(flow, *items)
    state = read_state(flow["path"])
    state["phase"] = "implement"
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")


def test_one_commit_per_item_and_a_missing_item_is_not_done(flow, cmd_setup, cmd_implement):
    """AC11 AC12: 1 項目 = 1 コミット。コミットの無い項目は `not_done`。"""
    work = flow["work"]
    _implement_phase(flow, cmd_setup, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    sha = commit_with_trailers(work, "Refactor", item_trailers("I-001"))

    _call(cmd_implement, "cmd_merge_implement")

    items = _items(flow)
    assert items["I-001"]["status"] == "implemented"
    assert items["I-001"]["commits"]["implement"] == sha
    assert items["I-001"]["seconds"]["implement"] is not None
    assert _deferred(flow) == {"I-002": "not_done"}
    assert items["I-002"]["status"] == "deferred"


def test_two_commits_for_one_item_reject_it(flow, cmd_setup, cmd_implement):
    work = flow["work"]
    _implement_phase(flow, cmd_setup, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    commit_with_trailers(work, "Refactor 1", item_trailers("I-001"))
    _write(work, "src/calc.py", (work / "src" / "calc.py").read_text() + "\n")
    commit_with_trailers(work, "Refactor 2", item_trailers("I-001"))
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-002"))

    _call(cmd_implement, "cmd_merge_implement")

    items = _items(flow)
    assert items["I-001"]["status"] == "reverted"
    assert "2 コミット" in items["I-001"]["failure_reason"]
    assert items["I-002"]["status"] == "implemented"
    assert "sum(values)" not in (work / "src" / "calc.py").read_text()
    assert (work / "src" / "other.py").exists()


def test_a_commit_outside_the_scope_rejects_the_item(flow, cmd_setup, cmd_implement):
    work = flow["work"]
    _implement_phase(flow, cmd_setup, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    _write(work, "docs/note.txt", "x\n")
    commit_with_trailers(work, "Refactor", item_trailers("I-001"))

    with pytest.raises(SystemExit) as exc:
        _call(cmd_implement, "cmd_merge_implement")

    assert exc.value.code == 2          # 残る項目 0 件で最終ゲートへ
    assert "対象範囲の外" in _items(flow)["I-001"]["failure_reason"]


def test_a_changed_expectation_rejects_the_item(flow, cmd_setup, cmd_implement):
    """期待値を変えた実装は振る舞いの変更として取り消す（#443 の段 1）。"""
    work = flow["work"]
    _implement_phase(flow, cmd_setup, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _write(work, "src/calc.py", CALC.replace("return a + b", "return a + b + 0"))
    _write(work, "tests/test_calc.py", (work / "tests" / "test_calc.py").read_text().replace("== 3", "== 4"))
    commit_with_trailers(work, "Refactor", item_trailers("I-001"))
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-002"))

    _call(cmd_implement, "cmd_merge_implement")

    assert "期待する振る舞い" in _items(flow)["I-001"]["failure_reason"]


# ---------- 検証と修正（verify / merge-fix） ----------

def _verify_phase(flow, cmd_setup, cmd_implement, *items, change=_refactor_total, item_id="I-001"):
    _implement_phase(flow, cmd_setup, *items)
    change(flow["work"])
    commit_with_trailers(flow["work"], "Refactor", item_trailers(item_id))
    _call(cmd_implement, "cmd_merge_implement")


def test_a_failing_item_goes_to_fix_and_returns_after_the_fix(
        flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    work = flow["work"]
    _verify_phase(flow, cmd_setup, cmd_implement,
                  _item("I-001", 1, targets=["tests/test_total.py"], tests=["tests/test_total.py"]),
                  change=lambda w: (_write(w, "tests/test_total.py", TEST_TOTAL), _break_total(w)))
    # 実装にテストのファイルを含めた形でも、期待値を変えなければ取り込む。
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "fix"
    assert _items(flow)["I-001"]["status"] == "failing"
    assert read_state(flow["path"])["fix"]["items"] == ["I-001"]

    _call(cmd_setup, "cmd_start_phase", phase="fix")
    _write(work, "src/calc.py", CALC)
    commit_with_trailers(work, "Fix", item_trailers("I-001"))
    _call(cmd_converge, "cmd_merge_fix")
    items = _items(flow)
    assert items["I-001"]["fix_count"] == 1
    assert len(items["I-001"]["commits"]["fix"]) == 1

    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done"
    assert _items(flow)["I-001"]["status"] == "verified"
    stats = read_state(flow["path"])["fix_stats"]
    assert stats["launches"] == 1


def test_an_item_at_the_fix_limit_is_reverted_alone(
        flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC15: 上限に達した項目だけを取り消し、他の項目のコミットは残す。"""
    work = flow["work"]
    first = _item("I-001", 1, symbol="add", targets=["tests/test_calc.py"])
    broken = _item("I-002", 2, targets=["tests/test_total.py"], tests=["tests/test_total.py"])
    _implement_phase(flow, cmd_setup, first, broken)
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-001"))
    _write(work, "tests/test_total.py", TEST_TOTAL)
    _break_total(work)
    commit_with_trailers(work, "Refactor total", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")
    state = read_state(flow["path"])
    for item in state["items"]:
        if item["id"] == "I-002":
            item["fix_count"] = 3
    write_state(flow["path"], state)

    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    items = _items(flow)
    assert items["I-001"]["status"] == "verified"
    assert items["I-002"]["status"] == "reverted"
    assert (work / "src" / "other.py").exists()
    assert "return result + 1" not in (work / "src" / "calc.py").read_text()


def test_items_sharing_a_command_are_reverted_newest_first_until_it_passes(
        flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC15: 同じ語の並びを共有した項目は新しい方から 1 件ずつ取り消し、通った時点で止める。"""
    work = flow["work"]
    older = _item("I-001", 1, symbol="add", targets=["tests/test_calc.py"])
    newer = _item("I-002", 2, targets=["tests/test_calc.py"])
    _implement_phase(flow, cmd_setup, older, newer)
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "Refactor add", item_trailers("I-001"))
    _write(work, "tests/test_calc.py",
           (work / "tests" / "test_calc.py").read_text() + "\nfrom src.calc import total\n\n\n"
           "def test_total_is_sum():\n    assert total([1, 2]) == 3\n")
    _break_total(work)
    commit_with_trailers(work, "Refactor total", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")
    state = read_state(flow["path"])
    for item in state["items"]:
        item["fix_count"] = 3
    write_state(flow["path"], state)

    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")

    items = _items(flow)
    assert items["I-002"]["status"] == "reverted"
    assert items["I-001"]["status"] == "verified"
    assert (work / "src" / "other.py").exists()


def test_the_whole_test_runs_once_when_a_danger_flag_is_raised_and_the_gate_reuses_it(
        flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate, capsys):
    """AC13 AC14 AC16b: ファイルを消した項目（D2）で全体のテストを 1 度。最終ゲートは使い回す。"""
    work = flow["work"]

    def delete(w):
        git("rm", "-q", "src/__init__.py", cwd=w)

    _verify_phase(flow, cmd_setup, cmd_implement, _item("I-001", 1), change=delete)
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


def test_a_failing_whole_test_reverts_the_flagged_items_and_the_gate_runs_again(
        flow, cmd_setup, cmd_implement, cmd_converge, cmd_gate):
    """決定 15: 印を持つ項目を取り消し、検証の中では走らせ直さない。最終ゲートが走らせる。"""
    work = flow["work"]

    def rename_and_break_outside(w):
        # `total` を消す（限ったテスト tests/test_calc.py は `add` しか見ない）。
        git("rm", "-q", "src/__init__.py", cwd=w)
        _write(w, "src/calc.py", "def add(a, b):\n    return a + b\n")

    item = _item("I-001", 1, symbol="add", targets=["tests/test_calc.py"])
    _write(work, "tests/test_total.py", TEST_TOTAL)
    commit_with_trailers(work, "既存のテスト", {})
    _verify_phase(flow, cmd_setup, cmd_implement, item, change=rename_and_break_outside)
    _call(cmd_converge, "cmd_verify")
    state = read_state(flow["path"])
    assert state["whole_test"]["status"] == "fail"
    assert state["whole_test"]["reverted"] is True
    assert _items(flow)["I-001"]["status"] == "reverted"

    _call(cmd_gate, "cmd_final_gate")
    gate = read_state(flow["path"])["final_gate"]
    assert len(gate["checks"]) == 1
    assert gate["status"] == "passed"


# ---------- 最終ゲートと履歴（final-gate / finalize） ----------

def test_standalone_runs_cross_review_and_appends_history_only_when_approved(
        flow, cmd_gate, cmd_report, capsys):
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
    _call(cmd_report, "cmd_finalize", review_status="approved")
    assert len(history.read_text(encoding="utf-8").splitlines()) == 1
    row = json.loads(history.read_text(encoding="utf-8"))
    assert row["whole_test"]["final"] is not None


def test_a_failed_gate_does_not_append_history(flow, cmd_gate, cmd_report, monkeypatch):
    state = read_state(flow["path"])
    state["baseline_test"]["command"] = "false"
    write_state(flow["path"], state)
    with pytest.raises(SystemExit):
        _call(cmd_gate, "cmd_final_gate")
    _call(cmd_report, "cmd_finalize", review_status=None)
    assert not (flow["metrics"] / "acme--demo" / "cross-refactoring-allocation.jsonl").exists()


def test_a_resumed_intake_reuses_its_conclusion_and_does_not_drop_twice(
        flow, cmd_setup, cmd_implement):
    """取り消しの後に落ちて再開しても、積み直したコミットを 2 コミット目と数えない。"""
    work = flow["work"]
    _implement_phase(flow, cmd_setup, _item("I-001", 1), _item("I-002", 2, symbol="add"))
    _refactor_total(work)
    commit_with_trailers(work, "Refactor", item_trailers("I-001"))
    _write(work, "src/other.py", "X = 1\n")
    commit_with_trailers(work, "計画に無い", {"Impl-Runtime": "claude", "Impl-Model": "m"})
    _call(cmd_implement, "cmd_merge_implement")
    head = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    state = read_state(flow["path"])
    state["phases"]["implement"].pop("ended_at")      # 終わりを書く前に落ちたことにする
    write_state(flow["path"], state)

    _call(cmd_implement, "cmd_merge_implement")

    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == head
    assert _items(flow)["I-001"]["status"] == "implemented"
    assert len(read_state(flow["path"])["drops"]) == 1
