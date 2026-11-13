"""sprint_loop_table.py: 工程の進行表で流した開発ループの 1 回を締める（#3092）。`loop lap` が呼ぶ。LLM を呼ばない。"""

from __future__ import annotations

from pathlib import Path

import jsonio
from sprint_loop_argv import next_pipeline
from sprint_loop_gate import after_close, close_lap, failure_key, loop_ok
from sprint_loop_record import DONE, FAILED, Env, resume_command, selected_issues, update_handoff, write_record


def lap_by_table(env: Env, rec: dict, before: dict, lap: dict, failed: bool, done: str | None, deferred_issues: list[int]) -> dict:
    """工程の進行表で流したループの 1 回を、`progress.py next` の出力（done）で締める（#3092）。

    完了なら完了、工程の失敗・中止なら失敗で締める。答え待ち（`gate`）では締めず、答えの後に同じ 1 行を打ち直す。"""
    rep = jsonio.read(done, missing={}, broken={}, want=dict) if done else {}
    status = rep.get("status")
    if status == "gate" and not failed:
        pipeline = next_pipeline(rec, lap["table"], Path(done))
        write_record(env, rec, before)
        update_handoff(env, rec, f"ループの {lap['n']} 回目 の進行表が答えを待っている", resume_command(rec), pipeline)
        return loop_ok(
            f"ループの {lap['n']} 回目 の進行表が答えを待っている: {rep.get('summary', '')}",
            rec,
            rep.get("items") or [],
            status="gate",
            next_=f"progress.py answer で答えてから: {pipeline}",
            table=lap["table"],
            pipeline=pipeline,
        )
    state = jsonio.read(lap["state_path"], missing={}, broken={}, want=dict) if lap.get("state_path") else {}
    ok = status == "ok" if done else failure_key(state) is None  # done を渡さない締めは、今までどおりスプリント状態の行で決める
    outcome = DONE if ok and not failed else FAILED
    close_lap(env, rec, lap, outcome, state, deferred_issues)
    rows = [i.get("row") for i in rep.get("items") or [] if i.get("kind") == "failed" and i.get("row")]
    if outcome == FAILED and rows:  # 同じ失敗の判定（failure_streak）が読む鍵: 課題・失敗した工程（理由の文は含めない）
        lap["failure"] = {"issues": selected_issues(lap), "kind": rows[0], "step": "失敗"}
    return after_close(env, rec, before, f"ループの {lap['n']} 回目 を {outcome} で締めた")
