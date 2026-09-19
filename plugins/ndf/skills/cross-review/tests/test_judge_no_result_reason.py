"""判定が結果なしの理由を出し、起動し直せない理由があれば止めることのテスト（#729 AC14〜AC17）。

| ラウンドの結果なし | 出口 | 終了コード |
| --- | --- | --- |
| 理由がすべて起動し直してよいもの、まだ起動し直していない | `RELAUNCH_AGENTS` を出して起動し直す | 7 |
| 理由がすべて起動し直してよいもの、既に起動し直している | 中断（`final = error`） | 1 |
| 起動し直せない理由（利用上限）を 1 つでも含む | **起動し直さず**中断（`final = error`） | 1 |

どの出口でも、判定は先に標準出力へ `NO_RESULT_REASONS='<担当>=<理由> ...'` の 1 行を出す。
起動し直しの可否は結末の共通層（`monitor_outcome.relaunch_same_agent`）だけが決め、
判定は `usage_limit` という値を知らない（AC12）。報告の表は結果なしの担当を
`<担当>=NO_RESULT(<理由>)` の形で出す（AC17）。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 8729
REPO = "o/r"
DETAIL = "Monthly request limit reached"


# ---------------- 足場 ----------------


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    """`CROSS_REVIEW_TMP_DIR` を tmp_path に向ける。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _state(rounds: list[dict], **over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": rounds,
        "pr_history": [{"pr": PR, "rounds": len(rounds)}],
        "deferred_nits": [],
        "final": None,
    }
    state.update(over)
    return state


def _round(no: int = 1, **over) -> dict:
    entry: dict = {"round": no, "pr": PR, "started_at": "2026-09-19T00:00:00+00:00"}
    entry.update(over)
    return entry


def _approve() -> dict:
    return {
        "intent": "APPROVE",
        "posted_as": "APPROVE",
        "comments": 0,
        "by_severity": {"critical": 0, "major": 0},
    }


def _no_result(reason: str, detail: str | None = None) -> dict:
    """`read-result` が残す結果なしの形（`test_read_result_reason.py` が固定する）。"""
    entry: dict = {
        "intent": "NO_RESULT",
        "no_result_reason": reason,
        "posted_as": None,
        "comments": None,
        "review_url": None,
        "by_severity": {},
    }
    if detail:
        entry["monitor_detail"] = detail
    return entry


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _judge(state_mod) -> int:
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))
    return int(e.value.code or 0)


# ---------------- AC14: 理由の行 ----------------


def test_judge_prints_the_reason_of_each_agent_without_a_result(tmp_dir, state_mod, capsys):
    """結果なしの担当があると、標準出力に `NO_RESULT_REASONS='<担当>=<理由> ...'` が出る。"""
    _write(tmp_dir, _state([_round(codex=_no_result("timeout"), agy=_no_result("stalled"))]))

    assert _judge(state_mod) == 7

    out = capsys.readouterr().out
    assert "NO_RESULT_REASONS='codex=timeout agy=stalled'" in out


def test_an_agent_without_an_entry_is_reported_as_missing(tmp_dir, state_mod, capsys):
    """`read-result` すら記録を残していない担当は、理由 `missing` として出る。"""
    _write(tmp_dir, _state([_round(codex=_approve())]))

    assert _judge(state_mod) == 7

    assert "NO_RESULT_REASONS='agy=missing'" in capsys.readouterr().out


def test_the_reason_line_comes_before_the_relaunch_line(tmp_dir, state_mod, capsys):
    """理由の行は起動し直しの指示より先に出る。骨組みが読む順序を固定する。"""
    _write(tmp_dir, _state([_round(codex=_approve(), agy=_no_result("cli_timeout"))]))

    assert _judge(state_mod) == 7

    out = capsys.readouterr().out
    assert out.index("NO_RESULT_REASONS=") < out.index("RELAUNCH_AGENTS=")


# ---------------- AC15: 起動し直せない理由があれば止める ----------------


def test_a_usage_limit_stops_the_review_without_a_relaunch(tmp_dir, state_mod, capsys):
    """利用上限の担当があれば、起動し直さず `final = error` で 1 で止まる。"""
    _write(tmp_dir, _state([
        _round(codex=_approve(), agy=_no_result("usage_limit", DETAIL)),
    ]))

    assert _judge(state_mod) == 1

    st = _read(tmp_dir)
    assert st["final"] == "error"
    assert st["ended_at"]
    assert st["rounds"][-1]["verdict"] == "no_result"
    assert "relaunched" not in st["rounds"][-1]
    captured = capsys.readouterr()
    assert "NO_RESULT_REASONS='agy=usage_limit'" in captured.out
    assert "RELAUNCH_AGENTS" not in captured.out
    assert "agy" in captured.err
    assert "usage_limit" in captured.err
    assert DETAIL in captured.err


def test_a_usage_limit_stops_even_when_another_agent_could_be_relaunched(
    tmp_dir, state_mod, capsys
):
    """起動し直してよい担当が混ざっていても、利用上限が 1 つあれば誰も起動し直さない。"""
    _write(tmp_dir, _state([
        _round(codex=_no_result("timeout"), agy=_no_result("usage_limit")),
    ]))

    assert _judge(state_mod) == 1

    st = _read(tmp_dir)
    assert st["final"] == "error"
    assert "relaunched" not in st["rounds"][-1]
    captured = capsys.readouterr()
    assert "NO_RESULT_REASONS='codex=timeout agy=usage_limit'" in captured.out
    assert "RELAUNCH_AGENTS" not in captured.out


def test_a_usage_limit_without_monitor_detail_still_stops(tmp_dir, state_mod, capsys):
    """`monitor_detail` の鍵が無くても、担当と理由が標準エラーに出て止まる。"""
    _write(tmp_dir, _state([_round(codex=_approve(), agy=_no_result("usage_limit"))]))

    assert _judge(state_mod) == 1

    err = capsys.readouterr().err
    assert "agy" in err
    assert "usage_limit" in err


def test_the_verdict_reads_relaunchability_from_the_common_layer(
    tmp_dir, state_mod, monkeypatch, capsys
):
    """可否を決めるのは共通層の集合であり、判定は理由の値を見ない（AC12）。

    共通層の「起動し直せない理由」に `timeout` を足すと、判定は `timeout` でも止まる。
    判定が `usage_limit` を直に比べていれば、この変更は届かず 7 になる。
    """
    monkeypatch.setattr(
        state_mod.monitor_outcome, "NO_RELAUNCH_REASONS", frozenset({"timeout"}))
    _write(tmp_dir, _state([_round(codex=_approve(), agy=_no_result("timeout"))]))

    assert _judge(state_mod) == 1
    assert _read(tmp_dir)["final"] == "error"

    # 逆に `usage_limit` を外せば、判定は起動し直す
    monkeypatch.setattr(state_mod.monitor_outcome, "NO_RELAUNCH_REASONS", frozenset())
    _write(tmp_dir, _state([_round(codex=_approve(), agy=_no_result("usage_limit"))]))

    assert _judge(state_mod) == 7
    assert "RELAUNCH_AGENTS='agy'" in capsys.readouterr().out


# ---------------- AC16: 起動し直してよい理由なら従来どおり ----------------


def test_relaunchable_reasons_ask_for_one_relaunch(tmp_dir, state_mod, capsys):
    """理由がすべて起動し直してよいものなら、1 度目は 7 で `RELAUNCH_AGENTS` を返す。"""
    _write(tmp_dir, _state([
        _round(codex=_no_result("cli_timeout", "print timeout after 60m"),
               agy=_no_result("early_error", "fatal: something")),
    ]))

    assert _judge(state_mod) == 7

    out = capsys.readouterr().out
    assert "RELAUNCH_AGENTS='codex agy'" in out
    assert "RELAUNCH_TARGET=both" in out
    st = _read(tmp_dir)
    assert st["rounds"][-1]["relaunched"] == ["codex", "agy"]
    assert st["final"] is None


def test_a_second_no_result_with_relaunchable_reasons_stops(tmp_dir, state_mod, capsys):
    """起動し直した後も結果が残らなければ、従来どおり `final = error` で 1 で止まる。"""
    _write(tmp_dir, _state([
        _round(codex=_approve(), agy=_no_result("timeout"), relaunched=["agy"]),
    ]))

    assert _judge(state_mod) == 1

    st = _read(tmp_dir)
    assert st["final"] == "error"
    assert st["rounds"][-1]["verdict"] == "no_result"
    assert "NO_RESULT_REASONS='agy=timeout'" in capsys.readouterr().out


def test_rounds_without_a_no_result_do_not_print_the_reason_line(tmp_dir, state_mod, capsys):
    """結果なしが無いラウンドでは理由の行が出ず、終了コード 0 の枝は変わらない。"""
    _write(tmp_dir, _state([_round(codex=_approve(), agy=_approve())]))

    assert _judge(state_mod) == 0

    assert "NO_RESULT_REASONS" not in capsys.readouterr().out
    assert _read(tmp_dir)["final"] == "approved"


# ---------------- AC17: 報告の表 ----------------


def test_the_round_summary_shows_the_reason_of_a_no_result(tmp_dir, state_mod, capsys):
    """報告の表で、結果なしの担当は `<担当>=NO_RESULT(<理由>)` の形で出る。"""
    _write(tmp_dir, _state([
        _round(codex=_approve(), agy=_no_result("usage_limit", DETAIL),
               verdict="no_result"),
    ], final="error"))

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    out = capsys.readouterr().out
    assert "agy=NO_RESULT(usage_limit)" in out
    assert "NO_RESULT (" not in out
    # 使える結果を残した担当の形は変わらない
    assert "codex=APPROVE (0)" in out


def test_the_round_summary_shows_a_dash_when_the_reason_is_unknown(
    tmp_dir, state_mod, capsys
):
    """理由の鍵を持たない古い記録は `NO_RESULT(-)` として出る。"""
    _write(tmp_dir, _state([
        _round(codex=_approve(), agy={"intent": "NO_RESULT"}, verdict="no_result"),
    ]))

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    assert "agy=NO_RESULT(-)" in capsys.readouterr().out
