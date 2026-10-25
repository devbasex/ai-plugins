"""drive のステップが駆動の stopped で失敗したとき、止まった理由に駆動の summary と元の終了コードが載る（#1593 I5）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
from supervise_lib import engine  # noqa: E402
from test_supervise_limits import reason_of  # noqa: E402

PY = sys.executable
WHY = "review: 最終スイープの指示を組めない: .ndf/project.json の test が不正: test.suites[0].kind は test か lint で書く（今: unit）"


def said(status, metrics, code):
    obj = {"tool": "t", "status": status, "summary": WHY, "items": [], "metrics": metrics}
    return f"{PY} -c 'print({json.dumps(json.dumps(obj, ensure_ascii=False))}); raise SystemExit({code})'"


def run(tmp_path, steps):
    return engine.Engine({"フェーズ": "検査", "作業場所": str(tmp_path), "steps": steps}, tmp_path / "state").run(None)


@pytest.mark.parametrize("kind", ["drive", "run"])
def test_the_stop_reason_carries_the_original_exit(tmp_path, kind):
    text = run(tmp_path, [{"id": "review", "type": kind, "cmd": said("stopped", {"exit": 2}, 1), "next": "end"}])
    reason = reason_of(text)
    assert "ステップ review が失敗した" in reason and f"{WHY}（元の終了コード 2）" in reason, text


def test_the_abort_route_carries_the_original_exit_of_the_review(tmp_path):
    review = {"id": "review", "type": "drive", "cmd": said("stopped", {"exit": 2}, 1), "next": "end", "on_fail": "abort"}
    abort = {"id": "abort", "type": "run", "cmd": "false", "next": "end"}
    reason = reason_of(run(tmp_path, [review, abort]))
    assert "ステップ abort が失敗した" in reason and f"review: {WHY}（元の終了コード 2）" in reason, reason


@pytest.mark.parametrize(
    "status, metrics",
    [("stopped", {}), ("stopped", {"exit": "2"}), ("ok", {"exit": 2})],
)
def test_no_exit_is_added_without_a_stopped_integer_exit(tmp_path, status, metrics):
    reason = reason_of(run(tmp_path, [{"id": "review", "type": "run", "cmd": said(status, metrics, 1), "next": "end"}]))
    assert WHY in reason and "元の終了コード" not in reason, reason


def test_the_counts_line_carries_the_unverified_ci(tmp_path):
    """#1593 I3・受け入れ条件 3 — 駆動の件数の `ci`・`ci_reason` が報告の件数の行に載り、`null` のキーは出ない。"""
    metrics = {"rounds": 3, "review_status": "approved", "ci": "unverified", "ci_reason": "チェックジョブ 0 件", "unresolved": None}
    text = run(tmp_path, [{"id": "review", "type": "drive", "cmd": said("ok", metrics, 0), "next": "end"}])
    assert "ci unverified / ci_reason チェックジョブ 0 件" in text and "unresolved" not in text, text
