"""state.py cmd_check_oscillation の non-dict 防御テスト (PLAN21 round 5).

gemini round 4 指摘: payload.json が dict 以外 (list 等) の場合、
`payload.get("comments", [])` で AttributeError になる前に明示的に止める。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest


PR = 7777


def _seed_state_two_rounds(tmp_dir: pathlib.Path) -> None:
    """同一 PR で 2 round 分のエントリを seed する (oscillation 判定の前提)。"""
    state = {
        "current_pr": PR,
        "rounds": [
            {"round": 1, "pr": PR, "started_at": "2026-05-23T00:00:00+00:00"},
            {"round": 2, "pr": PR, "started_at": "2026-05-23T00:10:00+00:00"},
        ],
        "deferred_nits": [],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _make_args() -> argparse.Namespace:
    return argparse.Namespace(pr=PR)


@pytest.fixture()
def patched_tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _write_payload(
    tmp_dir: pathlib.Path, agent: str, round_no: int, data: object
) -> pathlib.Path:
    p = tmp_dir / f"{agent}-review-pr{PR}-round{round_no}-payload.json"
    p.write_text(json.dumps(data))
    return p


def test_non_dict_payload_dies(patched_tmp_dir, state_mod, capsys):
    """payload.json が list 等の非 dict なら die(code=3)。

    `payload.get(...)` を呼ぶ前に明示的に止めることで、launcher 出力バグや
    別実行の残骸を黙って見逃さない。
    """
    tmp_dir = patched_tmp_dir
    _seed_state_two_rounds(tmp_dir)

    # round 1 / round 2 の codex payload を dict で置く (正常 = 比較ベース)
    _write_payload(tmp_dir, "codex", 1, {"comments": [{"path": "a.py", "line": 1}]})
    _write_payload(tmp_dir, "codex", 2, {"comments": [{"path": "a.py", "line": 1}]})
    # round 2 の gemini payload は不正 (list)
    _write_payload(tmp_dir, "gemini", 2, [{"comments": "should_be_ignored"}])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_check_oscillation(_make_args())
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "dict ではない" in captured.err


def test_non_dict_comment_entry_dies(patched_tmp_dir, state_mod, capsys):
    """payload.comments のエントリが dict ではない場合も die(code=3)。"""
    tmp_dir = patched_tmp_dir
    _seed_state_two_rounds(tmp_dir)

    # round 1 / round 2 とも codex の payload は dict だが、
    # round 2 の comments エントリが str (本来 dict)
    _write_payload(tmp_dir, "codex", 1, {"comments": [{"path": "a.py", "line": 1}]})
    _write_payload(tmp_dir, "codex", 2, {"comments": ["not-a-dict-entry"]})

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_check_oscillation(_make_args())
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "dict ではない" in captured.err


def test_valid_dict_payloads_dont_die(patched_tmp_dir, state_mod):
    """正常な dict payload (regression guard): non-dict 検査が誤検知しないこと。"""
    tmp_dir = patched_tmp_dir
    _seed_state_two_rounds(tmp_dir)

    # 全 agent の payload を正規の dict で置く (overlap < 50% で continue 期待)
    _write_payload(tmp_dir, "codex", 1, {"comments": [{"path": "a.py", "line": 1}]})
    _write_payload(tmp_dir, "codex", 2, {"comments": [{"path": "b.py", "line": 2}]})

    # exit code 2 (continue) で正常終了
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_check_oscillation(_make_args())
    assert e.value.code == 2
