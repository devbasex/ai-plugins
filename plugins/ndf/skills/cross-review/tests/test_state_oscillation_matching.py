"""同じ箇所の指摘を、位置・近傍・本文の 3 つで数えることのテスト（#246）。

位置の完全一致だけで測ると、指摘の趣旨が同じでも行が 1 行ずれれば別の指摘として数える。
レビューを行うのは codex / agy であり、同じ箇所を指すときに選ぶ行は毎回同じとは
限らない。修正で行が前後にずれた場合も一致しない。

| 一致 | 条件 |
| --- | --- |
| 位置の一致 | ファイルが同じで、行が同じ |
| 近傍の一致 | ファイルが同じで、行の差が 3 以内 |
| 本文の一致 | ファイルが同じで、正規化した本文が同じ |

閾値は 0.5 のまま変えない。一致の条件を広げると重なりの比は大きくなる方向にしか動かない
ため、閾値も同時に動かすとどちらの変更が結果を変えたのかが分からなくなる。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 7246


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "rounds": [
            {"round": 1, "pr": PR, "started_at": "2026-09-02T00:00:00+00:00"},
            {"round": 2, "pr": PR, "started_at": "2026-09-02T00:10:00+00:00"},
        ],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _payload(tmp_dir: pathlib.Path, round_no: int, comments: list[dict]) -> None:
    (tmp_dir / f"codex-review-pr{PR}-round{round_no}-payload.json").write_text(
        json.dumps({"comments": comments}, ensure_ascii=False)
    )


def _run(tmp_dir: pathlib.Path, state_mod, prev: list[dict], curr: list[dict]):
    _seed(tmp_dir)
    _payload(tmp_dir, 1, prev)
    _payload(tmp_dir, 2, curr)
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_check_oscillation(argparse.Namespace(pr=PR))
    return e.value.code


def test_the_same_position_counts_as_the_same_place(tmp_dir, state_mod, capsys):
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "直す"}],
        [{"path": "a.py", "line": 10, "body": "直す"}],
    )
    assert code == 4
    assert "位置=1" in capsys.readouterr().err


def test_a_line_that_moved_by_one_counts_as_the_same_place(tmp_dir, state_mod, capsys):
    """行が 1 行ずれても同じ箇所として数える。これが従来は拾えなかった形である。"""
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "引数の検査が抜けている"}],
        [{"path": "a.py", "line": 11, "body": "まったく別の指摘の文面"}],
    )
    assert code == 4
    assert "近傍=1" in capsys.readouterr().err


def test_the_same_body_far_away_counts_as_the_same_place(tmp_dir, state_mod, capsys):
    """行が離れていても、同じファイルで本文が同じなら同じ箇所として数える。"""
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "引数の検査が抜けている"}],
        [{"path": "a.py", "line": 400, "body": "引数の検査が抜けている"}],
    )
    assert code == 4
    assert "本文=1" in capsys.readouterr().err


def test_a_different_place_is_not_counted(tmp_dir, state_mod, capsys):
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "引数の検査が抜けている"}],
        [{"path": "b.py", "line": 10, "body": "戻り値の型が合っていない"}],
    )
    assert code == 2
    assert "overlap=0/1" in capsys.readouterr().err


def test_a_line_far_enough_away_is_not_counted(tmp_dir, state_mod):
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "引数の検査が抜けている"}],
        [{"path": "a.py", "line": 14, "body": "戻り値の型が合っていない"}],
    )
    assert code == 2


def test_two_japanese_bodies_do_not_collapse_into_one(tmp_dir, state_mod):
    """日本語だけで書かれた別々の指摘が、本文の一致で結びつかない。

    ASCII の英数字だけを残す正規化では本文が空になり、別の指摘どうしが一致してしまう。
    """
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "引数の検査が抜けている"}],
        [{"path": "a.py", "line": 400, "body": "戻り値の型が合っていない"}],
    )
    assert code == 2


def test_the_breakdown_is_printed(tmp_dir, state_mod, capsys):
    code = _run(
        tmp_dir, state_mod,
        [
            {"path": "a.py", "line": 10, "body": "あ"},
            {"path": "b.py", "line": 20, "body": "い"},
        ],
        [
            {"path": "a.py", "line": 10, "body": "あ"},
            {"path": "b.py", "line": 22, "body": "う"},
            {"path": "c.py", "line": 30, "body": "え"},
        ],
    )
    err = capsys.readouterr().err
    assert "overlap=2/3 (67%)" in err
    assert "位置=1 近傍=1 本文=0" in err
    assert code == 4


def test_the_threshold_stays_at_half(tmp_dir, state_mod):
    """重なりが半分に満たなければ続行する。"""
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "あ"}],
        [
            {"path": "a.py", "line": 10, "body": "あ"},
            {"path": "b.py", "line": 20, "body": "い"},
            {"path": "c.py", "line": 30, "body": "う"},
        ],
    )
    assert code == 2


def test_the_body_normalization_keeps_letters_of_any_language(state_mod):
    assert state_mod._normalized_body("引数の検査が抜けている（`a.py:10`）") == "引数の検査が抜けているapy10"
    assert state_mod._normalized_body(None) == ""
    assert state_mod._normalized_body("!!!") == ""


def test_a_comment_with_an_unreadable_line_is_skipped(tmp_dir, state_mod):
    code = _run(
        tmp_dir, state_mod,
        [{"path": "a.py", "line": 10, "body": "あ"}],
        [{"path": "a.py", "line": "ten", "body": "あ"}, {"path": "a.py", "line": 10, "body": "あ"}],
    )
    assert code == 4


def test_new_finding_count_characterization_conditions(tmp_dir, state_mod):
    """_new_finding_count の完全一致・近傍境界・離れた同一本文・別ファイル・空本文・不一致の現在の件数を固定する。"""
    _seed(tmp_dir)
    prev = [
        {"path": "a.py", "line": 10, "body": "本文A"},
        {"path": "a.py", "line": 50, "body": "本文B"},
        {"path": "c.py", "line": 30, "body": "本文C"},
    ]
    curr = [
        {"path": "a.py", "line": 10, "body": "本文A"},        # 1. 完全一致 -> 一致
        {"path": "a.py", "line": 13, "body": "別の本文1"},      # 2. 近傍境界 (+3) -> 一致
        {"path": "a.py", "line": 7, "body": "別の本文2"},       # 2. 近傍境界 (-3) -> 一致
        {"path": "a.py", "line": 14, "body": "別の本文3"},      # 2. 近傍境界外 (+4) -> 不一致（新規）
        {"path": "a.py", "line": 200, "body": "本文B"},        # 3. 離れた同一本文 -> 一致
        {"path": "b.py", "line": 10, "body": "本文A"},         # 4. 別ファイル -> 不一致（新規）
        {"path": "a.py", "line": 300, "body": "!!!"},          # 5. 空本文（正規化で空） -> 不一致（新規）
        {"path": "c.py", "line": 80, "body": "異なる本文"},    # 6. 通常の不一致 -> 不一致（新規）
    ]
    _payload(tmp_dir, 1, prev)
    _payload(tmp_dir, 2, curr)
    st = state_mod._load(PR)
    count, measurable = state_mod._new_finding_count(st, PR)
    assert measurable is True
    assert count == 4


def test_oscillation_matching_priority_exact_over_near_and_body(tmp_dir, state_mod, capsys):
    """前ラウンドに複数候補があるとき、完全一致が近傍や本文一致より優先してカウントされることを固定する。"""
    prev = [
        {"path": "a.py", "line": 10, "body": "本文A"},
        {"path": "a.py", "line": 12, "body": "本文B"},
        {"path": "a.py", "line": 50, "body": "本文C"},
    ]
    curr = [
        {"path": "a.py", "line": 10, "body": "本文C"},
    ]
    code = _run(tmp_dir, state_mod, prev, curr)
    assert code == 4
    err = capsys.readouterr().err
    assert "位置=1 近傍=0 本文=0" in err


def test_oscillation_matching_priority_near_over_body(tmp_dir, state_mod, capsys):
    """前ラウンドに複数候補があるとき、近傍一致が本文一致より優先してカウントされることを固定する。"""
    prev = [
        {"path": "a.py", "line": 20, "body": "本文X"},
        {"path": "a.py", "line": 80, "body": "本文Y"},
    ]
    curr = [
        {"path": "a.py", "line": 18, "body": "本文Y"},
    ]
    code = _run(tmp_dir, state_mod, prev, curr)
    assert code == 4
    err = capsys.readouterr().err
    assert "位置=0 近傍=1 本文=0" in err

