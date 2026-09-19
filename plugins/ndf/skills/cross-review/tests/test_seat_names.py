"""席の名前を受け口が通すか（#727 の AC21、結果の受け口の部分）。

担当の単位は「席の名前」になった（設計の決定 10）。形は `assignment.SEAT_PATTERN`
（ランタイム名か、その名前に `-2`〜`-9` を付けたもの）。使える者が 2 者に満たない
ラウンドでは、同じランタイムの 2 つ目（`claude-2`）が席に入る。**受け口がこの形を
弾くと、結果を残した担当が「結果なし」として扱われる。**

綴りの検査は argparse の型が行い、通らなければ終了コード 2 になる。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 4243
SEAT = "claude-2"


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def review_posted(monkeypatch, state_mod):
    """投稿の実在確認は届いた前提にする。ここで見るのは席の名前である。"""
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: True)


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-09-19T00:00:00+00:00",
                    "reviewers": ["codex", SEAT]}],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


# ---------------- 引数の検査 ----------------

def test_the_parser_accepts_a_second_seat(state_mod):
    args = state_mod.build_parser().parse_args(["read-result", "1", SEAT])
    assert args.agent == SEAT


def test_the_parser_still_accepts_every_runtime(state_mod):
    parser = state_mod.build_parser()
    for runtime in state_mod.assignment.ALL_RUNTIMES:
        assert parser.parse_args(["read-result", "1", runtime]).agent == runtime


@pytest.mark.parametrize("seat", ["gemini", "claude-1", "claude-10", "claude_2", ""])
def test_a_name_outside_the_seat_pattern_exits_with_two(state_mod, seat):
    with pytest.raises(SystemExit) as e:
        state_mod.build_parser().parse_args(["read-result", "1", seat])
    assert e.value.code == 2


# ---------------- 記録の鍵 ----------------

def test_the_result_of_a_second_seat_is_recorded_under_its_seat_name(tmp_dir, state_mod):
    """AC21: `read-result <pr> claude-2` の結果は `rounds[-1]["claude-2"]` に入る。"""
    _seed_state(tmp_dir)
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({
        "event": "APPROVE", "posted_as": "APPROVE", "comments_count": 0,
        "review_url": "https://example/pr/1#1", "by_severity": {},
    }))

    state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent=SEAT, file=str(rfile)))

    st = json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())
    assert st["rounds"][-1][SEAT]["intent"] == "APPROVE"
