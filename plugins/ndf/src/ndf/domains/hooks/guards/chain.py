"""NDF のコマンドをシェルでつないで待つ呼び出しを止める guard（#1919）。

conductor が NDF のコマンドを `&&`・`;`・パイプ・ループでつなぐと、1 本の Bash が長く走り、途中の結果を読めない。
計画にまとめて計画の待ちのサブコマンドを背景で打つか、1 つずつ打つよう案内する。
運用上限の guard が通す引継ぎのコマンドだけの連結（`ndf relay context record … && ndf records handoff check …`）は通す（#3995）。
"""

from __future__ import annotations

from ndf.domains.hooks.guards import context_limit
from ndf.domains.hooks.guards.shell import is_ndf
from ndf.domains.hooks.verdict import Verdict, deny

_HELP = "計画にまとめて ndf plan wait を Bash の run_in_background で打つか、NDF のコマンドを 1 つずつ打つ"


def check(ctx, svc) -> Verdict | None:
    script = ctx.shell
    calls = [c for c in script.commands if is_ndf(c)]
    if not calls:
        return None
    if script.loops:
        return deny(f"ループの中で NDF のコマンドを打って待つ呼び出しを止めた。{_HELP}")
    if len(calls) >= 2 and not context_limit.handoff_bash(ctx, context_limit.params()):
        return deny(f"NDF のコマンドをシェルでつないだ呼び出しを止めた。{_HELP}")
    return None
