"""state.py cmd_read_result の result.json スキーマ揺れに対するテスト。

カバー範囲:
  1. 正規スキーマ (`event` / `comments_count`) → state にマージされる
  2. 変則スキーマ (`intent` / `comment_count`) → 同等にマージされる (フォールバック)
  3. event / intent いずれも欠落 → die(exit 1) で fail + 結果なしがラウンドへ残る
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest


PR = 4242
AGENT = "agy"


def _seed_state(tmp_dir: pathlib.Path) -> dict:
    state = {
        "current_pr": PR,
        "rounds": [
            {"round": 1, "pr": PR, "started_at": "2026-05-21T00:00:00+00:00"}
        ],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    return state


def _make_args(file_path: pathlib.Path) -> argparse.Namespace:
    return argparse.Namespace(pr=PR, agent=AGENT, file=str(file_path))


def _read_state(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


@pytest.fixture()
def patched_tmp_dir(monkeypatch, tmp_path, state_mod):
    """`CROSS_REVIEW_TMP_DIR` を tmp_path に向ける。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def review_posted(monkeypatch, state_mod):
    """投稿の実在確認は届いた前提にする。ここで見るのはスキーマの揺れである。"""
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: True)


def test_canonical_schema(patched_tmp_dir, state_mod):
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    result = {
        "event": "APPROVE",
        "posted_as": "APPROVE",
        "comments_count": 0,
        "review_url": "https://example/pr/1#1",
        "by_severity": {"critical": 0, "major": 0, "minor": 0, "nit": 0},
    }
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps(result))

    state_mod.cmd_read_result(_make_args(rfile))

    st = _read_state(tmp_dir)
    merged = st["rounds"][-1][AGENT]
    assert merged["intent"] == "APPROVE"
    assert merged["posted_as"] == "APPROVE"
    assert merged["comments"] == 0
    assert merged["review_url"] == "https://example/pr/1#1"
    assert merged["by_severity"]["critical"] == 0


def test_alias_schema_intent_and_comment_count(patched_tmp_dir, state_mod):
    """agy が `intent` / `comment_count` で書き出すパターンも受理する。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    result = {
        "intent": "APPROVE",
        "comment_count": 3,
        "review_url": "https://example/pr/2#2",
        "by_severity": {"critical": 0, "major": 0, "minor": 2, "nit": 1},
    }
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps(result))

    state_mod.cmd_read_result(_make_args(rfile))

    st = _read_state(tmp_dir)
    merged = st["rounds"][-1][AGENT]
    assert merged["intent"] == "APPROVE"
    # posted_as は別名 result.json には存在しないので intent と同値にフォールバック
    assert merged["posted_as"] == "APPROVE"
    assert merged["comments"] == 3


def test_missing_event_and_intent_dies(patched_tmp_dir, state_mod):
    """event / intent いずれも無ければ exit 1 で fail し、結果なしが残ること。

    判定はこの記録を読んで、起動し直しか中断かを決める（#196）。判定の値を持たない
    結果はレビューが行われなかったのと同じであり、収束させない。
    """
    tmp_dir = patched_tmp_dir
    seeded = _seed_state(tmp_dir)
    result = {"comments_count": 0}
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps(result))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(_make_args(rfile))
    assert e.value.code == 1

    st = _read_state(tmp_dir)
    assert st["rounds"][-1][AGENT]["intent"] == "NO_RESULT"
    assert st["rounds"][-1][AGENT]["no_result_reason"] == "no_verdict"
    assert st["rounds"][-1]["round"] == seeded["rounds"][-1]["round"]


def test_empty_result_file_dies(patched_tmp_dir, state_mod):
    """空 result.json → die (result 未生成扱い)。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    rfile = tmp_dir / "result.json"
    rfile.write_text("")

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(_make_args(rfile))
    assert e.value.code == 1


# ---------------- PLAN21 round 5: non-dict / 不正 JSON 防御 ----------------


def test_non_dict_result_json_dies(patched_tmp_dir, state_mod, capsys):
    """result.json が list 等の非 dict なら die(code=3)。

    gemini round 4 指摘: `r.get(...)` で AttributeError になる前に明示的に止める。
    """
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    rfile = tmp_dir / "result.json"
    # JSON valid だが dict ではない
    rfile.write_text(json.dumps([{"event": "APPROVE"}]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(_make_args(rfile))
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "dict ではない" in captured.err
    # 使える結果が無いことがラウンドへ残る（#196）
    st = _read_state(tmp_dir)
    assert st["rounds"][-1][AGENT]["intent"] == "NO_RESULT"
    assert st["rounds"][-1][AGENT]["no_result_reason"] == "unparsable"


def test_invalid_json_result_file_dies(patched_tmp_dir, state_mod, capsys):
    """JSON parse 不能なら die(code=3)。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    rfile = tmp_dir / "result.json"
    rfile.write_text("{ this is not valid json")

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(_make_args(rfile))
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "parse" in captured.err.lower() or "parse" in captured.err
