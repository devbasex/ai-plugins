"""利用者の入力で数え手を数え直す guard（#3345 の決定 92）。中継が送った入力（印がある）では数え直さない。

中継は入力を送る前に `relay.marks.mark` で `<状態の置き場>/relay/marks/<セッション>.json` を書く。ここで
`relay.marks.take` が印を消せたら、その入力は中継が送ったものである（決定 111）。
"""

from __future__ import annotations

from ndf.domains.hooks.verdict import Verdict, passing
from ndf.domains.relay import marks


def _relayed(ctx, svc) -> bool:
    try:
        return marks.take(svc.state_dir, ctx.session_id)
    except marks.MarkError:  # noqa: NDF401 ファイル: 形の違うセッションの ID には印が無い（利用者の入力）
        return False


def check(ctx, svc) -> Verdict:
    source = svc.counter.reset(source="relay" if _relayed(ctx, svc) else "human")
    return passing(turn_source=source)
