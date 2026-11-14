"""中継の印の guard（#1877・#2438）。

中継の下で最後の応答が次の区間のブロック（囲みの名前は表の `params.fence`）を持てば、次の区間の印
（`next.json`）を書く。`/goal` の続き（`stop_hook_active` が真）でも同じに書く。背景の作業が残る間は印を書かずに
差し戻し、終わるのを待たせる（差し戻しはセッションの数え手が利用者の入力ごとに数える）。
"""

from __future__ import annotations

from ndf.domains.hooks import tables
from ndf.domains.relay import blocks, signals
from ndf.domains.hooks.verdict import Verdict, pushback


def check(ctx, svc) -> Verdict | None:
    directory = signals.relay_dir(ctx.env)
    if directory is None or not ctx.last_reply:
        return None
    block = blocks.last_block(ctx.last_reply, svc.params["fence"])
    if block is None:
        return None
    if ctx.background_tasks:
        return pushback(
            f"背景の作業が {len(ctx.background_tasks)} 件残っているため、次の区間へ切り替えない。終わるのを待ってから、"
            "次の区間のブロックを書いて終える"
        )
    wait = float(tables.policy_value("hook.counter-lock", "seconds"))
    written = signals.write_next(directory, ctx.session_id, block, ctx.stop_hook_active, wait)
    return Verdict("pass", notes={"relay_next": written, "goal_active": ctx.stop_hook_active})
