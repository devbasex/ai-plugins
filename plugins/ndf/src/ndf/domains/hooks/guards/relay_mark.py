"""中継のマーカーの guard（#1877・#2438）。

中継の下で最後の応答が次のセグメントのブロック（囲みの名前は表の `params.fence`）を持てば、次のセグメントのマーカー
（`next.json`）を書く。`/goal` の続き（`stop_hook_active` が真）でも同じに書く。背景の作業が残る間はマーカーを書かずに
変更要求、終わるのを待たせる（変更要求はセッションの数え手が利用者の入力ごとに数える）。

中継の下では、どの戻りより前に活動（背景の作業の一覧とセッション）を `activity.json` へ書く（#4036）。入力の
`background_tasks` は正規化の前の値を渡し、明示の空のリストと観測できなかった値を分けて残す。終えた応答の
`uuid`（会話の記録の最後の `assistant`）も残し、読む側はそれで今の応答の記録かを確かめる。`uuid` は一覧がリストのときだけ
読み、観測できなかったときは `reply` を `null` にする（読む側は `tasks` が `null` の記録を使わないので、会話の記録を開かない）。
"""

from __future__ import annotations

from ndf.domains.hooks import tables
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
    if not ctx.last_reply:
        return None
    block = blocks.last_block(ctx.last_reply, svc.params["fence"])
    if block is None:
        return None
    if ctx.background_tasks:
        return pushback(
            f"背景の作業が {len(ctx.background_tasks)} 件残っているため、次のセグメントへ切り替えない。終わるのを待ってから、"
            "次のセグメントのブロックを書いて終える"
        )
    wait = float(tables.policy_value("hook.counter-lock", "seconds"))
    written = signals.write_next(directory, ctx.session_id, block, ctx.stop_hook_active, wait)
    return passing(relay_next=written, goal_active=ctx.stop_hook_active)
