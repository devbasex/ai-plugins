"""結果を残さなかったレビュアーを収束と判定しないことのテスト（#196）。

判定は、結果ファイルを残さなかったレビュアーを、指定によるスキップと同じ値として
扱っていた。もう一方が承認していれば、ラウンド全体が収束する。レビューが行われて
いないのに、行われて承認されたのと同じ出口へ進む。

| ラウンドの状態 | 出口 | 終了コード |
| --- | --- | --- |
| 結果なしがあり、そのレビュアーをまだ起動し直していない | 起動し直す | 7 |
| 結果なしがあり、既に起動し直している | 中断（final = error） | 1 |
| 通った、かつ引き継いだ指摘なし | 収束（final = approved） | 0 |
| それ以外 | 修正へ | 2 |

用語は `issues/parallel-batch-03/02-issue-196.md` に従う。レビュアーは codex と
agy の 2 つを指す。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 6196
REPO = "o/r"


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
    entry: dict = {"round": no, "pr": PR, "started_at": "2026-09-01T00:00:00+00:00"}
    entry.update(over)
    return entry


def _approve() -> dict:
    return {
        "intent": "APPROVE",
        "posted_as": "APPROVE",
        "comments": 0,
        "by_severity": {"critical": 0, "major": 0},
    }


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


# ---------------- 受け入れ条件 1 / 2 / 3: 収束の判定 ----------------


def test_a_round_with_a_missing_result_does_not_converge(tmp_dir, state_mod, capsys):
    """片方が承認し、もう片方が結果を残さなかったラウンドは収束しない。"""
    _write(tmp_dir, _state([_round(codex=_approve())]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code != 0
    assert _read(tmp_dir)["final"] != "approved"


def test_only_codex_converges_on_a_codex_approval(tmp_dir, state_mod):
    """`--only codex` を指定したラウンドは、codex の承認だけで収束する。"""
    _write(tmp_dir, _state([_round(codex=_approve())], only="codex"))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    assert _read(tmp_dir)["final"] == "approved"


def test_the_output_separates_a_requested_skip_from_a_missing_result(
    tmp_dir, state_mod, capsys
):
    """指定によるスキップは `SKIP`、結果なしは `NO_RESULT` として出力される。"""
    _write(tmp_dir, _state([_round(codex=_approve())], only="codex"))
    with pytest.raises(SystemExit):
        state_mod.cmd_judge(argparse.Namespace(pr=PR))
    assert "AGY_INTENT=SKIP" in capsys.readouterr().out

    _write(tmp_dir, _state([_round(codex=_approve())]))
    with pytest.raises(SystemExit):
        state_mod.cmd_judge(argparse.Namespace(pr=PR))
    assert "AGY_INTENT=NO_RESULT" in capsys.readouterr().out


# ---------------- 受け入れ条件 4: 結果なしをラウンドへ残す ----------------


def _read_result(state_mod, rfile: pathlib.Path, agent: str = "agy") -> int:
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(
            argparse.Namespace(pr=PR, agent=agent, file=str(rfile))
        )
    return int(e.value.code or 0)


def test_a_missing_result_file_is_recorded_on_the_round(tmp_dir, state_mod):
    """結果ファイルが無いとき、`missing` としてラウンドへ残る。"""
    _write(tmp_dir, _state([_round()]))
    rfile = tmp_dir / "absent-result.json"

    assert _read_result(state_mod, rfile) == 1

    entry = _read(tmp_dir)["rounds"][-1]["agy"]
    assert entry["intent"] == "NO_RESULT"
    assert entry["no_result_reason"] == "missing"


def test_an_unreadable_result_file_is_recorded_on_the_round(tmp_dir, state_mod):
    """JSON として読めない / dict でない結果は、`unparsable` として残る。"""
    _write(tmp_dir, _state([_round()]))
    rfile = tmp_dir / "result.json"

    rfile.write_text("{ not json")
    assert _read_result(state_mod, rfile) == 3
    assert _read(tmp_dir)["rounds"][-1]["agy"]["no_result_reason"] == "unparsable"

    _write(tmp_dir, _state([_round()]))
    rfile.write_text(json.dumps([{"event": "APPROVE"}]))
    assert _read_result(state_mod, rfile) == 3
    assert _read(tmp_dir)["rounds"][-1]["agy"]["no_result_reason"] == "unparsable"


def test_a_result_without_a_verdict_is_recorded_on_the_round(tmp_dir, state_mod):
    """`event` も `intent` も無い結果は、`no_verdict` として残る。"""
    _write(tmp_dir, _state([_round()]))
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({"comments_count": 0}))

    assert _read_result(state_mod, rfile) == 1

    entry = _read(tmp_dir)["rounds"][-1]["agy"]
    assert entry["intent"] == "NO_RESULT"
    assert entry["no_result_reason"] == "no_verdict"


# ---------------- 受け入れ条件 5 / 6 / 7: 起動し直しと中断 ----------------


def test_the_first_missing_result_asks_for_one_relaunch(tmp_dir, state_mod, capsys):
    """初回の結果なしは、そのレビュアーだけを起動し直す指示になる。"""
    _write(tmp_dir, _state([_round(codex=_approve())]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 7
    out = capsys.readouterr().out
    assert "RELAUNCH_AGENTS='agy'" in out
    assert "RELAUNCH_TARGET=agy" in out
    assert _read(tmp_dir)["rounds"][-1]["relaunched"] == ["agy"]


def test_both_missing_results_ask_for_both(tmp_dir, state_mod, capsys):
    """両方が結果を残さなかったときは、対象が `both` になる。"""
    _write(tmp_dir, _state([_round()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 7
    out = capsys.readouterr().out
    assert "RELAUNCH_AGENTS='codex agy'" in out
    assert "RELAUNCH_TARGET=both" in out
    assert _read(tmp_dir)["rounds"][-1]["relaunched"] == ["codex", "agy"]


def test_a_missing_result_after_the_relaunch_stops_the_review(tmp_dir, state_mod):
    """起動し直した後も結果が残らなければ、`final = error` で中断する。"""
    _write(
        tmp_dir,
        _state([_round(codex=_approve(), relaunched=["agy"])]),
    )

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 1
    st = _read(tmp_dir)
    assert st["final"] == "error"
    assert st["rounds"][-1]["verdict"] == "no_result"


def test_the_round_stays_unconverged_when_the_relaunch_is_ignored(tmp_dir, state_mod):
    """起動し直しの指示を読み落としても、そのラウンドは収束しない。"""
    _write(tmp_dir, _state([_round(codex=_approve())]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 7
    st = _read(tmp_dir)
    assert st["final"] is None
    assert st["rounds"][-1]["verdict"] == "no_result"


# ---------------- 受け入れ条件 8 / 9: 次のラウンドの開始 ----------------


def test_a_no_result_round_needs_no_fix_record(tmp_dir, state_mod):
    """結果なしのラウンドの次を開始するとき、修正の記録を求められない。"""
    _write(
        tmp_dir,
        _state([_round(codex=_approve(), relaunched=["agy"], verdict="no_result")]),
    )

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 2


def test_a_round_without_a_verdict_and_a_missing_agent_can_be_followed(
    tmp_dir, state_mod
):
    """判定の結果を持たない古い状態ファイルでも、項目が欠けたラウンドの次を開始できる。"""
    _write(tmp_dir, _state([_round(codex=_approve())]))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 2


# ---------------- 受け入れ条件 10: ラウンドサマリ ----------------


def test_the_round_summary_shows_the_missing_result(tmp_dir, state_mod, capsys):
    """ラウンドサマリに結果なしが出る。"""
    _write(
        tmp_dir,
        _state(
            [
                _round(
                    codex=_approve(),
                    agy={"intent": "NO_RESULT", "no_result_reason": "missing"},
                    verdict="no_result",
                )
            ]
        ),
    )

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    assert "NO_RESULT" in capsys.readouterr().out
