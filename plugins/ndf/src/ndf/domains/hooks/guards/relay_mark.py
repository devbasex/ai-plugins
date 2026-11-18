"""中継のマーカーの guard（#1877・#2438）。

中継の下で最後の応答が次のセグメントのブロック（囲みの名前は表の `params.fence`）を持てば、次のセグメントのマーカー
（`next.json`）を書く。`/goal` の続き（`stop_hook_active` が真）でも同じに書く。背景の作業が残る間はマーカーを書かずに
変更要求、終わるのを待たせる（変更要求はセッションの数え手が利用者の入力ごとに数える）。
"""

from __future__ import annotations

from ndf.domains.hooks import tables
from ndf.domains.relay import blocks, signals
from ndf.domains.hooks.verdict import Verdict, passing, pushback


def check(ctx, svc) -> Verdict | None:
    directory = signals.relay_dir(ctx.env)
    if directory is None or not ctx.last_reply:
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
