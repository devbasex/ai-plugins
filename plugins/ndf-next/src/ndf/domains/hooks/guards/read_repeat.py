"""同じファイルの同じ範囲を読み続けるのを止める guard。

同じファイル（更新時刻まで同じ）の同じ範囲を `hook.read-repeat` の回数を超えて読むと止める。止める文は範囲ごとに
同じなので、同じ拒否が続けば数え手が案内へ下げる（#3345 の決定 92）。
"""

from __future__ import annotations

import os

from ndf.domains.hooks import tables
from ndf.domains.hooks.verdict import Verdict, deny


def check(ctx, svc) -> Verdict | None:
    path = ctx.tool_input.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:  # noqa: NDF401 ファイル: 無いファイルは Read 自身が失敗を返す
        return None
    span = f"{path}:{ctx.tool_input.get('offset')}:{ctx.tool_input.get('limit')}"
    count = svc.counter.bump(f"read:{span}:{mtime}")
    if count <= int(tables.policy_value("hook.read-repeat", "attempts")):
        return None
    return deny(
        f"{path} の同じ範囲を続けて読んだ。前に読んだ中身を使うか、Grep で位置を探し、Read の offset と limit で"
        "要る範囲だけを読む",
        key=span,
    )
