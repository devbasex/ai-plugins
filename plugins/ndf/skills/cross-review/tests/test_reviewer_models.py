"""レビュー担当が実際に動かしたモデルを、ラウンドごとに状態ファイルへ残す（#759 AC8）。

取り込み（`read-result`）は結果の検証より前に `models.observed_model` を呼び、
`rounds[-1].reviewer_models.<席>` へ実測値か取れなかった理由を書く。
"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest
import review_lib.commands.read_result

PR = 7759
EARLIER = {"claude-2": {"requested": None, "observed": "claude-old", "unobserved": None}}


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "rounds": [
            {"round": 1, "pr": PR, "started_at": "2026-10-02T08:00:00+09:00", "reviewer_models": EARLIER},
            {"round": 2, "pr": PR, "started_at": "2026-10-02T09:00:00+09:00"},
        ],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _rounds(tmp_dir: pathlib.Path) -> list[dict]:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())["rounds"]


def _read_result(seat: str, tmp_dir: pathlib.Path) -> int:
    args = argparse.Namespace(pr=PR, agent=seat, file=str(tmp_dir / "absent-result.json"))
    with pytest.raises(SystemExit) as e:
        review_lib.commands.read_result.cmd_read_result(args)
    return int(e.value.code or 0)


def test_the_seat_model_is_recorded_even_when_no_result_is_left(tmp_dir, state_mod):
    """`claude-2` の席でも claude として取り、結果が無くて止まる経路でも実測値が残る。"""
    _seed_state(tmp_dir)
    stem = tmp_dir / f"claude-2-review-pr{PR}"
    pathlib.Path(f"{stem}-launch.json").write_text(
        json.dumps({"runtime": "claude", "workdir": str(tmp_dir), "started_at": "2026-10-02T00:00:01Z"}), encoding="utf-8"
    )
    usage = {"claude-haiku-4-5": {"inputTokens": 3944, "outputTokens": 28}, "claude-opus-5[1m]": {"inputTokens": 22, "cacheReadInputTokens": 639525}}
    pathlib.Path(f"{stem}-stdout.log").write_text(json.dumps({"modelUsage": usage}), encoding="utf-8")

    assert _read_result("claude-2", tmp_dir) == 1

    rounds = _rounds(tmp_dir)
    assert rounds[-1]["reviewer_models"]["claude-2"] == {"requested": None, "observed": "claude-opus-5[1m]", "unobserved": None}
    assert rounds[-1]["claude-2"]["intent"] == "NO_RESULT"
    assert rounds[0]["reviewer_models"] == EARLIER, "前のラウンドの値を書き換えない"


def test_without_records_the_reason_is_recorded(tmp_dir, state_mod):
    """AC10 — 記録が無くても取り込みは続き、取れなかった理由が残る。"""
    _seed_state(tmp_dir)

    assert _read_result("codex", tmp_dir) == 1

    assert _rounds(tmp_dir)[-1]["reviewer_models"]["codex"] == {"requested": None, "observed": None, "unobserved": "no_record"}


def test_kiro_is_recorded_as_unsupported(tmp_dir, state_mod):
    _seed_state(tmp_dir)

    assert _read_result("kiro", tmp_dir) == 1

    assert _rounds(tmp_dir)[-1]["reviewer_models"]["kiro"]["unobserved"] == "unsupported"
