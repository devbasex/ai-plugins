"""中継のマーカーの guard（#1877・#2438・#4049）。

中継の下で最後の応答が次のセグメントのブロック（囲みの名前は表の `params.fence`）を持てば、次のセグメントのマーカー
（`next.json`）を書く。`/goal` の続き（`stop_hook_active` が真）でも同じに書く。作業が残る間はマーカーを書かずに
変更要求、終わるのを待たせる（変更要求はセッションの数え手が利用者の入力ごとに数える）。監視だけが残るならマーカーを書く
（次のセグメントを始めるラッパーが、子とともに監視を止める。作業と監視の分け方は `relay_busy.task_kind`）。
一覧を観測できなかった Stop では、起動ごとの記録に作業が残る間、同じくマーカーを書かずに変更要求する（記録が無ければ書く）。

中継の下では、どの戻りより前に活動（バックグラウンドの作業の一覧とセッション）を `activity.json` へ書く（#4036）。入力の
`background_tasks` は正規化の前の値を渡し、明示の空のリストと観測できなかった値を分けて残す。終えた応答の
`uuid`（会話の記録の最後の `assistant`）も残し、読む側はそれで今の応答の記録かを確かめる。`uuid` は一覧がリストのときだけ
読み、観測できなかったときは `reply` を `null` にする（読む側は `tasks` が `null` の記録を使わないので、会話の記録を開かない）。

続けて、子の会話の Stop では作業と監視の件数を起動ごとの記録へ書く（#4049）。一覧が無い Stop と、開始が記録されない環境
（`relay-busy` を止めた環境）では書かない。開始の記録が無いまま 0 件を書くと、ラッパーが作業を抱えた子を止めうる。
"""

from __future__ import annotations

from pathlib import Path

from ndf.domains.hooks import tables
from ndf.domains.hooks.guards import relay_busy
from ndf.domains.relay import blocks, signals
from ndf.domains.hooks.verdict import Verdict, passing, pushback


def check(ctx, svc) -> Verdict | None:
    directory = signals.relay_dir(ctx.env)
    if directory is None:
        return None
    tasks = ctx.payload.get("background_tasks")
    # 一覧を観測できない（`tasks: null`）記録は読む側が使わないので、会話の記録を開いて uuid を読まない
    reply = ctx.last_reply_id if isinstance(tasks, list) else None
    signals.write_activity(directory, tasks, ctx.payload.get("session_id"), reply)
    counted = relay_busy.work_count(tasks, relay_busy.table_params()) if _counts(ctx, tasks) else None
    notes = {}
    if counted is not None:
        notes["relay_busy"] = relay_busy.record_count(ctx.env, ctx.session_id, counted[0], "stop", counted[1])
    block = blocks.last_block(ctx.last_reply, svc.params["fence"]) if ctx.last_reply else None
    if block is None:
        return passing(**notes)
    if counted is None:
        counted = relay_busy.work_count(tasks, relay_busy.table_params()) if isinstance(tasks, list) else _recorded(ctx, directory)
    work, monitors = counted
    if work:
        return pushback(
            f"バックグラウンドの作業が {work} 件残っているため、次のセグメントへ切り替えない（監視 {monitors} 件は数えない）。"
            "終わるのを待ってから、次のセグメントのブロックを書いて終える",
            notes,
        )
    wait = float(tables.policy_value("hook.counter-lock", "seconds"))
    written = signals.write_next(directory, ctx.session_id, block, ctx.stop_hook_active, wait)
    return passing(relay_next=written, goal_active=ctx.stop_hook_active, **notes)


def _recorded(ctx, directory: Path) -> tuple[int, int]:
    """起動ごとの記録の（作業の件数, 監視の件数）。記録が無い・この会話のものでないなら 0 件。"""
    launch = signals.relay_launch(ctx.env)
    found = signals.read_busy(directory, launch, ctx.session_id) if launch is not None else None
    return (found[0], found[3]) if found is not None else (0, 0)


def _counts(ctx, tasks) -> bool:
    """起動ごとの記録へ件数を書く Stop か（子の会話で、一覧があり、`relay-busy` を止めていない）。"""
    off = tables.off_names(ctx.env.get("NDF_HOOK_OFF"))
    return isinstance(tasks, list) and relay_busy._target(ctx.env, ctx.session_id) and not tables.is_off(relay_busy.GUARD_NAME, off)
