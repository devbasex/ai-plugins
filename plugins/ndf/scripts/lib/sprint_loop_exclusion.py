"""開発ループの選定で課題を外す理由: 判断待ち・保留・前提の実装待ち・ループの 1 回の除外・ループの 1 回の中の起票・容量。"""

from __future__ import annotations

from sprint_loop_record import OVER_SCALE, Env, pending_issues

PENDING_REASON = "保留したループの 1 回の課題"
PRODUCTION_REASON = "設計 PR の宛先が本番（リリース承認ゲートで人がマージする）"


def prerequisite_reason(row: dict) -> str:
    """前提の課題の実装待ちの理由。open の前提の課題の番号を昇順で載せる。"""
    return "前提の課題の実装待ち（" + " ".join(f"#{b}" for b in sorted(row["prerequisites"])) + "）"


def waiting_reason(env: Env, rec: dict, n: int) -> str | None:
    """課題 n の待ち行から外す理由を読む。承認済みで未マージの行は PR の状態を読み、マージ済みなら merged_at を書く。"""
    for w in rec.get("waiting", []):
        if w["number"] != n:
            continue
        if not w.get("answer"):
            return "人の判断待ち"
        if w["answer"] == "rejected":
            return "差し戻し"
        if w["answer"] == "approved" and not w.get("merged_at"):
            if w.get("pr") and env.pr_state(int(w["pr"])) == "MERGED":
                w["merged_at"] = env.now()
            else:
                return merge_wait_reason(w)
    return None


def exclusion_reason(env: Env, rec: dict, row: dict, lap_n: int, lap_created: set, prior: dict | tuple = ()) -> str | None:
    n = row["number"]
    if n in prior:  # 問いの一覧に出ている前のループの待ち（#2510 の受け入れ条件 13）。棚卸しの判断待ちより先に見る
        return f"人の判断待ち（開発ループ {prior[n]}）"
    if row.get("waiting_decision"):
        return "人の判断待ち（棚卸し）"
    if n in pending_issues(rec):  # 締め直すまで選ばない（選ぶと保留中の実装と作業が重なる。#2436 の 6a）
        return PENDING_REASON
    if why := waiting_reason(env, rec, n):
        return why
    for e in rec.get("excluded", []):
        if e["number"] == n and e.get("lap") == lap_n:
            return e["reason"]
    if row.get("prerequisites"):  # 前提の課題が閉じるまで選ばない（同じループの 1 回に選ばれていても外す。#2604）
        return prerequisite_reason(row)
    if n in lap_created:
        return "ループの 1 回の中で起票された（ループの次の 1 回の棚卸しの後に選ぶ）"
    if row.get("size") == OVER_SCALE:
        return "容量（尺度を超える）"
    return None


def merge_wait_reason(w: dict) -> str:
    """承認済みで未マージの課題を外す理由。マージの結果の理由を添える（#2507）。"""
    m = w.get("merge") or {}
    if m.get("result") == "production":
        return PRODUCTION_REASON
    return f"設計 PR がマージ待ち（{m['reason']}）" if m.get("reason") else "設計 PR がマージ待ち"
