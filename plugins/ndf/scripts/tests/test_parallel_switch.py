"""長い待ちの切り替えの判定（#3006）: `parallel.py wait` が上限で戻るとき、`metrics.switch` に判定と材料を載せ、
`items` に `status` と同じレーンごとの行を載せる。判定の規則は `parallel_switch.judge` の材料の組み合わせで確かめる。"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_parallel_hold_flow as tph  # noqa: E402
import test_parallel_run as tpr  # noqa: E402

import context_policy as cp  # noqa: E402
import durable  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402
import parallel_switch as psw  # noqa: E402
import parallel_status as pst  # noqa: E402

env = tpr.env
HOUR = 3600.0
# 実測（計画 p5 の最初の会話）: 最後の文脈 474,303・最初の文脈 46,000・Opus 5.5
BIG, BASE, OPUS = 474_303, 46_000, "claude-opus-5-5"


def inputs(**over) -> psw.SwitchInputs:
    """切り替えに当たる材料（答えていない問い 0 件・ラッパーの子・残りの見込み 6 時間・大きな文脈）。"""
    body = dict(unanswered=0, relay="relay", gaps_s=[7 * HOUR, 7 * HOUR, 7 * HOUR], elapsed_s=HOUR, context=BIG, model=OPUS,
                base_context=BASE, interval_s=1800.0)  # fmt: skip
    body.update(over)
    return psw.SwitchInputs(**body)


def test_a_long_wait_with_a_big_context_switches_and_names_its_materials():
    v = psw.judge(inputs())
    assert v.verdict == psw.SWITCH and v.wait_s == 6 * HOUR and v.samples == 3
    assert v.cost["reports"] == 12 and v.cost["stay"] > v.cost["switch"]
    assert set(v.defaults) == {"handoff_calls", "handoff_output", "restore_calls", "restore_context"}


def test_the_cost_follows_the_formula_with_the_defaults():
    v = psw.judge(inputs())
    r, n, c, q = 0.05, 12, BIG, BASE
    assert v.cost["stay"] == round(n * r * c)
    assert v.cost["switch"] == round(3 * r * c + 5 * 2_000 + 2 * q + 5 * r * q + n * r * q)


def test_the_measured_context_switches_after_four_hours_and_not_before():
    """設計の実測: C = 474,303 では W が 4 時間を超える（n = 9）と切り替え、4 時間以下（n = 8）では報告して待つ。"""
    assert psw.judge(inputs(gaps_s=[5 * HOUR] * 3, elapsed_s=HOUR)).verdict == psw.STAY  # W = 4 時間・n = 8
    assert psw.judge(inputs(gaps_s=[5 * HOUR + 60] * 3, elapsed_s=HOUR)).verdict == psw.SWITCH  # n = 9


def test_calibrated_estimates_replace_the_defaults():
    est = cp.Estimates.from_dict({"restore_context": 60_000, "restore_calls": 10, "handoff_calls": 8, "handoff_output": 4_000,
                                  "samples": {"restore": 3, "handoff": 3}})  # fmt: skip
    v = psw.judge(inputs(estimates=est))
    assert v.defaults == [] and v.base_context == 60_000


@pytest.mark.parametrize("unanswered", [1, 2, None])
def test_an_unanswered_question_never_switches(unanswered):
    """受け入れ条件 3・I1: 他の材料が切り替えに当たっても、答えていない問いがあれば（読めなくても）報告して待つ。"""
    v = psw.judge(inputs(unanswered=unanswered, gaps_s=[100 * HOUR] * 5))
    assert v.verdict == psw.STAY and "答えていない問い" in v.reason


@pytest.mark.parametrize("where", ["outside", None])
def test_outside_the_wrapper_never_switches(where):
    """受け入れ条件 8・I2"""
    v = psw.judge(inputs(relay=where))
    assert v.verdict == psw.STAY and "ラッパー" in v.reason


def test_fewer_than_three_gaps_longer_than_the_elapsed_time_do_not_switch():
    """I3: 経過を超える間隔だけを標本にし、3 件未満なら待ちの見込みを出さない。"""
    v = psw.judge(inputs(gaps_s=[60.0] * 10 + [7 * HOUR] * 2, elapsed_s=HOUR))
    assert v.verdict == psw.STAY and v.wait_s is None and v.samples == 2 and "wait_s" in v.missing
    assert psw.remaining_wait([60.0, 3 * HOUR, 4 * HOUR, 5 * HOUR], HOUR) == (3 * HOUR, 3)
    assert psw.remaining_wait([], None) == (None, 0)


@pytest.mark.parametrize("over", [{"context": None}, {"base_context": None}])
def test_without_a_context_the_wait_alone_decides_at_four_hours(over):
    """要求の前提 4: 文脈 C か Q を読めなければ、W ≥ 4 時間で切り替え、未満で報告して待つ。`cost` は null。"""
    long_ = psw.judge(inputs(**over, gaps_s=[5 * HOUR] * 3, elapsed_s=HOUR))
    short = psw.judge(inputs(**over, gaps_s=[5 * HOUR - 1] * 3, elapsed_s=HOUR))
    assert (long_.verdict, short.verdict) == (psw.SWITCH, psw.STAY) and long_.cost is None and short.cost is None


def test_a_small_context_keeps_reporting():
    assert psw.judge(inputs(context=60_000)).verdict == psw.STAY


def test_the_judgement_does_not_touch_the_outside(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("外部を呼んだ")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(Path, "read_text", boom)
    assert psw.judge(inputs()).verdict == psw.SWITCH


def test_question_gaps_are_read_from_the_question_rows_in_time_order():
    rows = [
        {"kind": "ask", "at": "2026-11-11T03:00:00Z"},
        {"kind": "answer", "at": "2026-11-11T01:30:00Z"},
        {"kind": "gate1", "at": "2026-11-11T01:00:00Z"},
        {"kind": "gate2", "at": "2026-11-11T02:00:00Z"},
        {"kind": "unmatched", "at": "壊れた"},
    ]
    now = psw._at("2026-11-11T04:00:00Z")
    assert psw.question_gaps(rows, now) == ([HOUR, HOUR], HOUR)
    assert psw.question_gaps([], now) == ([], None)


# ---------------------------------------------------------------- wait の上限の経路


@pytest.fixture
def waiting(env, monkeypatch, capsys):
    main = env["main"]
    cli = tph.load_cli()
    monkeypatch.setattr(cli, "main_of", lambda: main)
    monkeypatch.setattr(pl, "record_exists", lambda *_a: True)
    waited: list[float] = []

    def wait_from(*a, **_k):
        waited.append(a[5])
        return durable.Outcome("timeout")

    monkeypatch.setattr(durable, "wait_from", wait_from)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)

    def call(*argv):
        return tph.wait(cli, capsys, *argv)

    return main, waited, call


def test_the_timeout_defaults_to_1800_and_returns_the_status_rows_and_the_judgement(waiting, env):
    """受け入れ条件 1・5・I4・I6: 既定の上限は 1800 秒。上限で戻ると status と同じ行と、材料つきの判定を返す。"""
    main, waited, call = waiting
    env["states"].write_text(json.dumps({"la": "running", "lb": "running"}))
    code, out = call("--after", "4")
    assert code == 3 and waited == [1800] and out["metrics"]["next"].endswith("--after 4")
    assert out["items"] == pst.lane_rows(main, "p1", pp.load_valid_plan(main, "p1"))
    assert "レーン 2 本（動いている 2" in out["summary"]
    sw = out["metrics"]["switch"]
    assert sw["verdict"] == psw.STAY and sw["record"] is None and sw["reason"]
    assert sw["inputs"]["relay"] == "outside" and sw["inputs"]["unanswered"] == 0 and sw["inputs"]["interval_s"] == 1800
    assert {"context", "base_context"} <= set(sw["missing"])


def test_a_switch_verdict_carries_the_record_line_and_keeps_next(waiting, monkeypatch):
    """受け入れ条件 2: 判定が「切り替える」なら、`metrics.next` は --after <seq> の 1 行のまま、記録の 1 行が付く。"""
    main, _waited, call = waiting
    monkeypatch.setattr(psw, "read_inputs", lambda *_a, **_k: inputs())
    code, out = call("--after", "9")
    sw = out["metrics"]["switch"]
    assert code == 3 and sw["verdict"] == psw.SWITCH and out["metrics"]["next"].endswith("wait p1 --after 9")
    assert "--reason 待ち" in sw["record"] and '--work "parallel p1"' in sw["record"] and f"--context {BIG}" in sw["record"]
    assert sw["cost"]["stay"] > sw["cost"]["switch"] and sw["inputs"]["wait_s"] == 6 * HOUR


def test_an_unreadable_material_keeps_the_exit_code_and_next(waiting, monkeypatch):
    """I5: 材料の読み取りが例外を出しても、終了コード 3 と `metrics.next` は変わらず、判定は報告して待つ。"""
    _main, _waited, call = waiting

    def boom(*_a, **_k):
        raise RuntimeError("壊れた")

    monkeypatch.setattr(psw, "read_inputs", boom)
    code, out = call("--after", "2")
    assert code == 3 and out["metrics"]["next"].endswith("--after 2") and out["metrics"]["switch"]["verdict"] == psw.STAY
    assert out["metrics"]["switch"]["missing"] == ["all"]


def test_unreadable_ledger_and_relay_become_missing_materials(env, monkeypatch):
    import parallel_ledger as plg

    def boom(*_a, **_k):
        raise OSError("読めない")

    monkeypatch.setattr(plg, "read_ledger_rows", boom)
    monkeypatch.setattr(psw, "_relay_position", boom)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    x = psw.read_inputs(env["main"], "p1", 1800)
    assert x.unanswered is None and x.relay is None and {"unanswered", "relay", "context"} <= set(x.missing)
    assert psw.judge(x).verdict == psw.STAY


def test_an_unanswered_question_in_the_ledger_is_counted(env, monkeypatch):
    """決定 6: 計画の台帳の答えていない問いを `status` と同じ一覧で数える。"""
    main = env["main"]
    pl.request_ask(main, "p1", "la", tpr.ASK, str(main), "s1")
    x = psw.read_inputs(main, "p1", 1800)
    assert x.unanswered == 1 and x.elapsed_s is not None and x.elapsed_s < 60


def test_the_context_is_read_from_the_session_transcript(env, monkeypatch, tmp_path):
    proj = tmp_path / "claude" / "projects" / "-repo"
    proj.mkdir(parents=True)

    def line(total: int) -> str:
        usage = {"input_tokens": total, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        return json.dumps({"type": "assistant", "message": {"model": OPUS, "usage": usage}}) + "\n"

    (proj / "sid1.jsonl").write_text(line(40_000) + line(300_000))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid1")
    x = psw.read_inputs(env["main"], "p1", 1800)
    assert (x.context, x.base_context, x.model) == (300_000, 40_000, OPUS)


def test_a_question_return_carries_no_judgement(env, monkeypatch, capsys):
    """決定 2: 知らせで戻った `wait` には `metrics.switch` が無い。"""
    main = env["main"]
    cli = tph.load_cli()
    monkeypatch.setattr(cli, "main_of", lambda: main)
    monkeypatch.setattr(pl, "record_exists", lambda *_a: True)
    ev = durable.Outcome("event", {"key": "answer-hold", "seq": 5, "action": "answer_hold", "n": 1, "line": "最後", "hold": {}})
    monkeypatch.setattr(durable, "wait_from", lambda *_a, **_k: ev)
    code, out = tph.wait(cli, capsys)
    assert code == 0 and "switch" not in out["metrics"]


def test_the_judgement_of_ten_lanes_adds_under_a_second(env, monkeypatch):
    """非機能（性能）: レーン 10 本の計画の台帳と大きな会話の記録で、材料の読み取りと判定が 1 秒以内。"""
    import parallel_ledger as plg

    main = env["main"]
    for i in range(200):
        plg.append_ledger_row(main, "p1", "ask", f"ask:{i}", {"id": f"q{i}", "kind": "ask"}, by=f"l{i % 10}", rid=f"q{i}")
        plg.append_ledger_row(main, "p1", "answer", f"answer:{i}", {"id": f"q{i}"}, by="organizer", rid=f"q{i}")
    t = time.monotonic()
    psw.report(main, "p1", 1800)
    assert time.monotonic() - t < 1.0
