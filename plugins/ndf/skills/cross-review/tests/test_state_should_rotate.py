"""ローテーション要否の判定を固定する（`cmd_should_rotate`、現状固定）。

公開サブコマンド `should-rotate` は `--help` に存在することだけがこれまで固定されて
おり（`test_state_subcommand_help.py`）、判定そのもの（`round_in_pr >= rotate_after`
かつ `total < max_rounds` で rotate、それ以外で keep）を実行で確かめる経路が無かった。

docstring が定める契約:

    round_in_pr >= rotate_after && total < max_rounds  → rotate (exit 0, CURRENT_PR/ROUND_IN_PR を出力)
    それ以外                                            → keep   (exit 2)
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 6100
REPO = "o/r"


def _state(rounds: list[dict], *, rotate_after: int = 8, max_rounds: int = 12,
           current_pr: int = PR) -> dict:
    return {
        "current_pr": current_pr,
        "repo": REPO,
        "max_rounds": max_rounds,
        "rotate_after": rotate_after,
        "rounds": rounds,
    }


def _rounds(pr: int, count: int) -> list[dict]:
    return [{"round": i, "pr": pr} for i in range(1, count + 1)]


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def test_reaching_rotate_after_under_the_round_cap_signals_rotate(
        tmp_dir, state_mod, capsys) -> None:
    """round_in_pr が rotate_after 以上、かつ total が max_rounds 未満で rotate。"""
    _write(tmp_dir, _state(_rounds(PR, 8), rotate_after=8, max_rounds=12))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_should_rotate(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    out = capsys.readouterr().out
    assert f"CURRENT_PR={PR}" in out
    assert "ROUND_IN_PR=8" in out


def test_fewer_rounds_than_rotate_after_keeps(tmp_dir, state_mod) -> None:
    """round_in_pr が rotate_after 未満のときは keep。"""
    _write(tmp_dir, _state(_rounds(PR, 7), rotate_after=8, max_rounds=12))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_should_rotate(argparse.Namespace(pr=PR))

    assert e.value.code == 2


def test_reaching_the_round_cap_keeps_even_past_rotate_after(tmp_dir, state_mod) -> None:
    """total が max_rounds に達していると、rotate_after を満たしても keep。"""
    _write(tmp_dir, _state(_rounds(PR, 12), rotate_after=8, max_rounds=12))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_should_rotate(argparse.Namespace(pr=PR))

    assert e.value.code == 2


def test_round_in_pr_counts_only_the_current_pr_after_a_rotation(
        tmp_dir, state_mod, capsys) -> None:
    """ローテーション後は current_pr が変わり、round_in_pr は新 PR の分だけを数える。"""
    new_pr = PR + 1
    rounds = _rounds(PR, 8) + _rounds(new_pr, 8)
    _write(tmp_dir, _state(rounds, rotate_after=8, max_rounds=20, current_pr=new_pr))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_should_rotate(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    out = capsys.readouterr().out
    assert f"CURRENT_PR={new_pr}" in out
    assert "ROUND_IN_PR=8" in out


# ---- total 側の境界だけを動かす対（round_in_pr は両方 rotate_after ちょうどで固定） ----
#
# 上の 4 つは round_in_pr と total が同じ値か、max_rounds から遠い状態しか通らない。
# そのため `total = len(st["rounds"])` を `total = round_in_pr` に書き換えても全て通る。
# total が **前の PR の round も数える**ことと、その境界が `<` であることを、
# round_in_pr を動かさずに固定する。

def test_the_round_cap_counts_rounds_of_earlier_prs_too(tmp_dir, state_mod) -> None:
    """round_in_pr が rotate_after ちょうどでも、前の PR を含めた total が
    max_rounds に達していれば keep。"""
    new_pr = PR + 1
    rounds = _rounds(PR, 4) + _rounds(new_pr, 8)   # total=12, round_in_pr=8
    _write(tmp_dir, _state(rounds, rotate_after=8, max_rounds=12, current_pr=new_pr))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_should_rotate(argparse.Namespace(pr=PR))

    assert e.value.code == 2


def test_one_round_below_the_cap_still_rotates(tmp_dir, state_mod, capsys) -> None:
    """同じ round_in_pr のまま total だけを 1 減らすと rotate へ切り替わる。"""
    new_pr = PR + 1
    rounds = _rounds(PR, 3) + _rounds(new_pr, 8)   # total=11, round_in_pr=8
    _write(tmp_dir, _state(rounds, rotate_after=8, max_rounds=12, current_pr=new_pr))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_should_rotate(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    out = capsys.readouterr().out
    assert f"CURRENT_PR={new_pr}" in out
    assert "ROUND_IN_PR=8" in out
