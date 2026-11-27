"""`ndf usage-limit`（終わった起動の利用上限の判定。#1959）。ログのファイルを書いて読ませ、子プロセスを起動しない。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli
from ndf.contracts import causes


def _run(tmp_path: Path, capsys, runtime: str, out: str | None, err: str | None) -> tuple[int, dict]:
    for name, text in (("o", out), ("e", err)):
        if text is not None:
            (tmp_path / name).write_text(text, encoding="utf-8")
    code = cli.main(["usage-limit", "--runtime", runtime, "--stdout", str(tmp_path / "o"), "--stderr", str(tmp_path / "e")])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("runtime", "out", "err", "hit"),
    [
        ("codex", "", "ERROR: You've hit your usage limit.\n", True),
        ("codex", "quota exceeded は引用しただけ\n", "", False),  # codex の標準出力（調査の結果）は読まない
        ("codex", "", "log: the word quota exceeded in the middle\n", False),
        ("codex", "", '{"type":"result","api_error_status":429}\n', False),  # 標準エラーの JSON の行は利用上限の行でない
        ("claude", '{"type":"result","api_error_status":429}\n', "", True),
        ("claude", '{"type":"result","api_error_status":200}\n', "", False),
        ("claude", '{"type":"result","result":{"cases":[{"api_error_status":429}]}}\n', "", False),  # 入れ子の 429 は結果のデータ
    ],
)
def test_judges_with_the_monitor_conditions(tmp_path: Path, capsys, runtime: str, out: str, err: str, hit: bool) -> None:
    code, got = _run(tmp_path, capsys, runtime, out, err)
    assert code == 0 and got["status"] == "ok"
    assert got["metrics"]["usage_limit"] is hit and ("line" in got["metrics"]) is hit


def test_stops_when_a_log_is_missing(tmp_path: Path, capsys) -> None:
    code, got = _run(tmp_path, capsys, "codex", "", None)
    assert code == causes.exit_code("precondition") and got["status"] == "stopped"


def test_both_logs_are_required(capsys) -> None:
    assert cli.main(["usage-limit", "--runtime", "codex", "--stdout", "x"]) != 0
