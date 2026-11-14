"""文脈の量の guard（SF12・#2595・#2789）。

会話の記録の末尾の最後の応答の文脈の量（token）が `hook.context-limit` を超えたら、次の 1 手だけを残して止める。
中継の下では応答の最後に次の区間のブロックを書いて区間を切り替え、外では引き継ぎの文書を書いて新しいセッションで
続ける。拒否は同じ理由で数えられ、続けば案内へ下がる（#3345 の決定 92）。

計測の印 `under_relay` は判定したときだけ書く（判定しなかった行と中継の外を見分ける。#2789）。
"""

from __future__ import annotations

from ndf.domains.hooks import tables
from ndf.domains.hooks.context import context_tokens
from ndf.domains.relay.signals import relay_dir
from ndf.domains.hooks.verdict import Verdict, deny, passing


def _next_step(ctx, handoff_dir: str) -> bool:
    """引き継ぎの文書を書く Tool の呼び出しか。"""
    path = ctx.tool_input.get("file_path")
    return ctx.tool in ("Write", "Edit") and isinstance(path, str) and handoff_dir in path


def check(ctx, svc) -> Verdict | None:
    tokens = context_tokens(ctx.transcript_tail)
    if tokens is None:
        return None
    limit = int(tables.policy_value("hook.context-limit", "count"))
    if tokens < limit:
        return passing(context_tokens=tokens)
    handoff_dir = svc.params["handoff_dir"]
    if _next_step(ctx, handoff_dir):
        return passing(context_tokens=tokens)
    under = relay_dir(ctx.env) is not None
    if under:
        step = "Tool を呼ばずに、応答の最後に次の区間のブロックを書いて終える（中継が新しいセッションで続ける）"
    else:
        step = f"引き継ぎの文書を {handoff_dir} の下へ Write で書き、新しいセッションで続ける"
    reason = f"文脈の量が上限（{limit} token）を超えた。次の 1 手: {step}"
    return deny(reason, notes={"context_tokens": tokens, "under_relay": under})
