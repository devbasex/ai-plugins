"""sprint-close.py がボードの記録の結果 JSON を読む（#2227 の決定 7）。

`projects-sync.sh` が ok 以外を返したら、理由を board_note に載せ、課題を閉じる処理は続ける。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
_spec = importlib.util.spec_from_file_location("sprint_close_board", SCRIPTS / "sprint-close.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def line(status, summary, gaps=None):
    obj = {"tool": "projects-sync", "status": status, "summary": summary, "items": [], "metrics": {}}
    if gaps:
        obj["gaps"] = gaps
    return json.dumps(obj, ensure_ascii=False) + "\n"


def test_ok_result_has_no_note():
    assert sc.step_read.not_ok_reason(line("ok", "#1 Status = Done（ボード）"), 0) is None


def test_stopped_result_carries_the_gap_reason():
    gap = {"kind": "board", "name": "#1 Status", "reason": "GraphQL: 偽の書き込みの失敗"}
    why = sc.step_read.not_ok_reason(line("stopped", "#1 の進行の一部を書けなかった（1 件）", [gap]), 6)
    assert why and "exit=6" in why and "GraphQL: 偽の書き込みの失敗" in why


def test_missing_result_is_not_read_as_ok():
    why = sc.step_read.not_ok_reason("NOTE: 古い形の行\n", 0, "落ちた")
    assert why and "結果 JSON が無い" in why


def test_close_one_notes_a_failed_board_and_keeps_closing(monkeypatch, tmp_path):
    gap = {"kind": "board", "name": "#7 Status", "reason": "HTTP 502"}
    states = iter(["OPEN", "CLOSED"])  # 閉じる前と後
    monkeypatch.setattr(sc, "issue_state", lambda root, repo, n: next(states))
    monkeypatch.setattr(sc, "_close_if_open", lambda root, repo, n, comment: None)

    class P:
        stdout = line("stopped", "#7 の進行の一部を書けなかった（1 件）", [gap])
        stderr = ""
        returncode = 6

    monkeypatch.setattr(sc, "run", lambda *a, **k: P)
    ctx = sc.CloseContext(tmp_path, "o/r", "閉じる")
    it = sc.close_one(ctx, "o/r", 7)
    assert it["result"] == "closed"
    assert ctx.notes and "HTTP 502" in ctx.notes[0]["reason"]
