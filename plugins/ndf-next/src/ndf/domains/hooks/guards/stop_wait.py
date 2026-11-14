"""本文の待ちの guard（#2324）。

最後の応答が AskUserQuestion を使わずに本文で人の答えを待って終えたら差し戻す。別の場所（ほかのセッション・レーン）の
待ちを報告しただけの応答は差し戻さない。差し戻しの上限の後はエントリポイントが通して待ちの知らせを書く（#3345 の決定 92）。
"""

from __future__ import annotations

from ndf.domains.hooks.verdict import Verdict, passing, pushback


def check(ctx, svc) -> Verdict:
    kind = ctx.last_reply_kind
    if kind == "prose_wait":
        return pushback(
            "本文で人の答えを待って終えた。人に問うときは AskUserQuestion を使う（中継とオーガナイザーへ問いが届き、"
            "待ちの知らせも送られる）。問わずに進められるなら続ける"
        )
    return passing(reply=kind)
