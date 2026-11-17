"""利用者の入力で数え手を数え直す guard（#3345 の決定 92）。中継が送った入力（マーカーがある）では数え直さない。

中継は入力を送る前に `relay.markers.mark` で `<状態の保存先>/relay/markers/<セッション>.json` を書く。ここで
`relay.markers.take` がマーカーを消せたら、その入力は中継が送ったものである（決定 111）。
"""

from __future__ import annotations

from ndf.domains.hooks.verdict import Verdict, passing
from ndf.domains.relay import markers


def _relayed(ctx, svc) -> bool:
    try:
        return markers.take(svc.state_dir, ctx.session_id)
    except markers.MarkerError:  # noqa: NDF401 ファイル: 形の違うセッションの ID にはマーカーが無い（利用者の入力）
        return False


def check(ctx, svc) -> Verdict:
    source = svc.counter.reset(source="relay" if _relayed(ctx, svc) else "human")
    return passing(turn_source=source)
