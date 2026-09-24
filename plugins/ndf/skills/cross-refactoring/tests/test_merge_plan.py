"""計画の取り込み（#933 の AC7 AC8 AC9 AC10b AC22 AC23）。

段と「同じ変更か」は、Jev が使えるときは Jev の答え（確信度が下限以上）、それ以外は
実装担当の答えで決まる。数え上げ・見積り・飛ばして詰める・締め切りはスクリプトが行う。
Jev の呼び出しは偽の関数へ差し替え、実際の HTTP を呼ばない。
"""
from __future__ import annotations

import argparse
import datetime as dt

import pytest

from crossref_helpers import make_state_v2, read_state, write_result, write_state


def _candidate(n, symbol, *, smell="long_method", technique="extract_method",
               agreed=("codex",), severity="major"):
    return {"id": f"C-{n:03d}", "path": "src/a.py", "symbol": symbol, "smell": smell,
            "technique": technique, "severity": severity, "rationale": "r", "plan": "p",
            "test_gap": False, "estimated_diff_lines": 10, "proposed_by": list(agreed)}


def _key(c):
    return f"{c['path']}#{c['symbol']}#{c['smell']}"


@pytest.fixture
def planned(tmp_path, env_tmp_dir, refactor):
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "src").mkdir()
    (work / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")

    def _make(candidates, answers, **overrides):
        started = (dt.datetime.now() - dt.timedelta(minutes=5)).isoformat(timespec="seconds")
        path = make_state_v2(tmp_path, work, candidates=candidates, started_at=started,
                             phase="plan", **overrides)
        env_tmp_dir(path)
        if answers is not None:
            write_result(path, "claude-plan-rf130", {"items": answers})
        return path
    return _make


def _run(cmd_plan):
    cmd_plan.cmd_merge_plan(argparse.Namespace(id=130))


def _answer(c, **over):
    return {"key": _key(c), "tier": "medium", "tests": [], "test_targets": ["tests/test_a.py"],
            "merge_into": None, "risk": False, **over}


def test_items_carry_rank_estimate_tests_and_targets(planned, cmd_plan, capsys):
    """AC7: 採った項目は順位・見積り・足すテスト・限ったテストの対象を持つ。"""
    a, b = _candidate(1, "f"), _candidate(2, "g", agreed=("codex", "kiro"))
    path = planned([a, b], [_answer(a, tier="high", tests=["tests/test_new.py"],
                                    test_targets=["tests/test_new.py"]),
                            _answer(b)])
    _run(cmd_plan)
    state = read_state(path)
    items = state["items"]
    assert [i["candidate_id"] for i in items] == ["C-001", "C-002"]      # 段が先
    assert items[0]["tests"] == ["tests/test_new.py"]
    assert items[0]["command"] == ["pytest", "-q", "tests/test_new.py"]
    assert items[0]["estimate"] == {"test": 2.7, "implement": 1.3, "verify": 0.2}
    assert items[0]["test_start_deadline"] is not None
    assert items[1]["test_start_deadline"] is None
    assert state["plan"]["table_source"] == "defaults"
    assert "TESTS_NEEDED=1" in capsys.readouterr().out


def test_items_that_do_not_fit_are_skipped_and_the_rest_packed(planned, cmd_plan):
    """AC8 / 決定 10: 入らない項目は飛ばし、後ろの小さな項目を詰める。"""
    big, small = _candidate(1, "big"), _candidate(2, "small")
    # 使える時間 = 14 − 経過 5 − 控え（0.1 + 0.1 + 5.5）≒ 3.3 分。big は 4.2 分、small は 1.5 分。
    path = planned([big, small], [
        _answer(big, tier="high", tests=[f"tests/t{n}.py" for n in range(1)]),
        _answer(small, tier="low"),
    ], budget_minutes=14)
    _run(cmd_plan)
    state = read_state(path)
    assert [i["candidate_id"] for i in state["items"]] == ["C-002"]
    assert [(d["path"], d["symbol"], d["defer_reason"]) for d in state["deferred_items"]] == [
        ("src/a.py", "big", "budget")]
    reserve = state["plan"]["reserve"]
    assert reserve["danger_whole_test"] == pytest.approx(0.1)
    assert reserve["final_whole_test"] == pytest.approx(0.1)
    assert reserve["fix"] == pytest.approx(5.5)


def test_the_final_reserve_is_zero_with_a_ci_check(planned, cmd_plan):
    a = _candidate(1, "f")
    path = planned([a], [_answer(a)], ci_check="ci")
    _run(cmd_plan)
    assert read_state(path)["plan"]["reserve"]["final_whole_test"] == 0


def test_an_item_without_a_limited_test_is_no_target(planned, cmd_plan):
    """AC10b: 対象が範囲の外・実在しない・シェルの文字を含み、--round-test も無ければ no_target。"""
    cands = [_candidate(n, s) for n, s in enumerate(["a", "b", "c", "d"], start=1)]
    path = planned(cands, [
        _answer(cands[0], test_targets=["../outside.py"]),
        _answer(cands[1], test_targets=["tests/missing.py"]),
        _answer(cands[2], test_targets=["tests/test_a.py;rm"]),
        _answer(cands[3]),
    ])
    _run(cmd_plan)
    state = read_state(path)
    assert [i["candidate_id"] for i in state["items"]] == ["C-004"]
    assert sorted(d["defer_reason"] for d in state["deferred_items"]) == ["no_target"] * 3


def test_a_round_test_is_used_as_is_when_targets_cannot_be_built(planned, cmd_plan):
    a = _candidate(1, "f")
    path = planned([a], [_answer(a, test_targets=[])],
                   round_test={"command": "make test-unit", "status": "green"})
    _run(cmd_plan)
    item = read_state(path)["items"][0]
    assert item["command"] == ["make", "test-unit"]
    assert item["command_source"] == "round_test"


def test_a_missing_plan_result_uses_defaults_and_does_not_stop(planned, cmd_plan):
    a = _candidate(1, "f")
    path = planned([a], None, round_test={"command": "pytest -q tests", "status": "green"})
    _run(cmd_plan)
    item = read_state(path)["items"][0]
    assert (item["tier"], item["tests"], item["command_source"]) == ("medium", [], "round_test")


def test_runtime_merge_into_defers_the_duplicate(planned, cmd_plan):
    a = _candidate(1, "f", agreed=("codex", "kiro"))
    b = _candidate(2, "f", smell="deep_nesting", agreed=("claude",))
    path = planned([a, b], [_answer(a), _answer(b, merge_into=_key(a))])
    _run(cmd_plan)
    state = read_state(path)
    assert [i["candidate_id"] for i in state["items"]] == ["C-001"]
    assert state["items"][0]["proposed_by"] == ["codex", "kiro", "claude"]
    assert [d["defer_reason"] for d in state["deferred_items"]] == ["duplicate"]


# ---------- Jev（AC22 AC23） ----------

def _jev_state(planned, cands, answers):
    return planned(cands, answers, judge={"kind": "jev", "reason": None, "failures": 0})


def test_jev_tier_wins_when_confident(planned, cmd_plan, monkeypatch):
    a, b = _candidate(1, "f"), _candidate(2, "g")
    path = _jev_state(planned, [a, b], [_answer(a, tier="low"), _answer(b, tier="high")])
    replies = {"f": ("high", 0.9), "g": ("low", 0.4)}
    monkeypatch.setattr(cmd_plan.jev, "ask_score",
                        lambda text, *a, **k: replies["f" if '"f"' in text else "g"])
    monkeypatch.setattr(cmd_plan.jev, "ask_boolean", lambda *a, **k: (False, 0.9))
    _run(cmd_plan)
    items = {i["symbol"]: i for i in read_state(path)["items"]}
    assert (items["f"]["tier"], items["f"]["tier_source"]) == ("high", "jev")
    # 確信度が下限（0.6）に満たなければ実装担当の段。
    assert (items["g"]["tier"], items["g"]["tier_source"]) == ("high", "runtime")


def test_jev_duplicate_needs_confidence_and_only_asks_within_a_group(planned, cmd_plan, monkeypatch):
    a = _candidate(1, "f")
    b = _candidate(2, "f", smell="deep_nesting")
    c = _candidate(3, "other")
    path = _jev_state(planned, [a, b, c], [_answer(a), _answer(b), _answer(c)])
    asked = []
    monkeypatch.setattr(cmd_plan.jev, "ask_score", lambda *a, **k: None)

    def boolean(text, question, *args, **kwargs):
        if "same code change" in question:
            asked.append(text)
        return (True, 0.85)

    monkeypatch.setattr(cmd_plan.jev, "ask_boolean", boolean)
    _run(cmd_plan)
    state = read_state(path)
    assert len(asked) == 1                         # 同じ組の中の 1 組だけ
    assert [i["candidate_id"] for i in state["items"]] == ["C-001", "C-003"]
    # 呼び出しの失敗は数え、段は実装担当の答えで決まる。
    assert state["judge"]["failures"] == 3
    assert state["judge"]["kind"] == "jev"


def test_the_plan_is_not_rebuilt_on_resume(planned, cmd_plan):
    a = _candidate(1, "f")
    path = planned([a], [_answer(a)])
    _run(cmd_plan)
    first = read_state(path)["plan"]
    state = read_state(path)
    state["budget_minutes"] = 1
    write_state(path, state)
    _run(cmd_plan)
    assert read_state(path)["plan"] == first


# ---------- 実行時の値を計画の終わりまでに書き出す（決定 24・25） ----------

def test_the_plan_writes_every_runtime_value_to_the_state(planned, cmd_plan):
    """計画の後の段は、状態ファイルの値と時計の比較だけで進む。値はすべて計画で出そろう。"""
    a, b = _candidate(1, "f"), _candidate(2, "g")
    path = planned([a, b], [_answer(a, tests=["tests/test_new.py"], test_targets=["tests/test_new.py"]),
                            _answer(b)])
    _run(cmd_plan)
    state = read_state(path)
    limits = state["limits"]
    for key in ("margin_seconds", "init_test_timeout", "test_timeout", "propose_end_at",
                "plan_end_at", "add_tests_end_at", "implement_end_at", "fix_end_at", "final_end_at"):
        assert limits[key] is not None, key
    for item in state["items"]:
        assert item["start_deadline"] and "public_io" in item


def test_d5_is_decided_by_jev_at_the_plan(planned, cmd_plan, monkeypatch):
    """決定 25: 公開の入出力が変わりうるか（D5）は計画の時点で Jev に問い、答えを項目に残す。"""
    a, b = _candidate(1, "f"), _candidate(2, "g")
    path = _jev_state(planned, [a, b], [_answer(a, risk=True), _answer(b)])
    monkeypatch.setattr(cmd_plan.jev, "ask_score", lambda *a, **k: None)
    monkeypatch.setattr(cmd_plan.jev, "ask_boolean",
                        lambda text, question, *a, **k: (
                            ('"g"' in text, 0.9) if "public input" in question else (False, 0.9)))
    _run(cmd_plan)
    items = {i["symbol"]: i for i in read_state(path)["items"]}
    assert (items["f"]["public_io"], items["f"]["public_io_source"]) == (False, "jev")
    assert (items["g"]["public_io"], items["g"]["public_io_source"]) == (True, "jev")


def test_d5_falls_back_to_the_runtime_risk_without_jev(planned, cmd_plan):
    a = _candidate(1, "f")
    path = planned([a], [_answer(a, risk=True)])
    _run(cmd_plan)
    item = read_state(path)["items"][0]
    assert (item["public_io"], item["public_io_source"]) == (True, "runtime")


def test_d5_keeps_the_runtime_risk_when_jev_is_not_confident(planned, cmd_plan, monkeypatch):
    """決定 2: 確信度 0.7 未満の Jev の答えは使わず、実装担当の `risk` を残す。"""
    a, b = _candidate(1, "f"), _candidate(2, "g")
    path = _jev_state(planned, [a, b], [_answer(a, risk=True), _answer(b)])
    monkeypatch.setattr(cmd_plan.jev, "ask_score", lambda *a, **k: None)
    monkeypatch.setattr(cmd_plan.jev, "ask_boolean",
                        lambda text, question, *a, **k: (
                            (True, 0.6) if "public input" in question else (False, 0.9)))
    _run(cmd_plan)
    items = {i["symbol"]: i for i in read_state(path)["items"]}
    assert (items["f"]["public_io"], items["f"]["public_io_source"]) == (True, "runtime")
    assert (items["g"]["public_io"], items["g"]["public_io_source"]) == (False, "runtime")
