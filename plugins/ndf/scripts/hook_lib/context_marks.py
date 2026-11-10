"""「引継ぎ開始」と「復元完了」の印を判定の記録へ書く（#2908 の決定 2）。

呼び出し側（`token_guard.guard_context`）がセッションの排他を取ってから呼ぶ。
- 引継ぎ開始: ラッパーの直接の子を止めたとき、そのセッションで 1 回だけ（I3）
- 復元完了: 引継ぎ文書で始まったセッションの、エントリポイントの Skill を除く最初の起動で 1 回だけ（I1・I2）。
  引継ぎ文書で始まったかは会話の記録の先頭を 1 セッションに 1 回だけ読み、状態のファイルに持つ
"""

from __future__ import annotations

ENTRY_SKILLS = ("skill:development-workflow", "skill:issue-plan-strategy")  # 状態を戻す途中のエントリポイント（復元完了を書かない）


def record_marks(sid: str, tp: str, stage: str, row: dict, repo: str, handoff: bool) -> None:
    """排他の中で印を書く。失敗は呼び出し側が飲む（I4）。"""
    import context_decisions as cd

    if handoff and not cd.handoff_marked(sid) and cd.set_handoff_marked(sid):
        cd.append_decision(mark_row(row, cd.MARK_HANDOFF), repo)
    state = cd.read_restore_state(sid)
    if state in (cd.RESTORE_DONE, cd.RESTORE_NA):
        return
    if state is None:
        started = cd.started_by_handoff(tp)
        if started is None:
            return  # 先頭が読めない・発言がまだ無い: 未判定のまま次の起動で読み直す
        state = cd.RESTORE_PENDING if started else cd.RESTORE_NA
        if state == cd.RESTORE_NA or stage in ENTRY_SKILLS:
            cd.write_restore_state(sid, state)
            return
    elif stage in ENTRY_SKILLS:
        return
    if cd.write_restore_state(sid, cd.RESTORE_DONE):  # 状態を書けなければ 2 行目を避けて印も書かない
        cd.append_decision(mark_row(row, cd.MARK_RESTORED), repo)


def mark_row(row: dict, event: str) -> dict:
    """判定の行と同じセッション・作業・文脈の量・方針で、印の 1 行を作る（判定の列は空）。"""
    import context_decisions as cd

    keep = ("source", "session_id", "transcript", "model", "work", "context", "window", "applied", "relay")
    return cd.boundary_row(**{k: row[k] for k in keep if k in row}, event=event)
