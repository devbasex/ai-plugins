"""sleep の guard。前景で `hook.sleep` の秒を超えて待つ sleep と、sleep の後に打つ NDF のコマンドを止める。

待つときは Monitor（条件が成り立つまで見回る）か、Bash の `run_in_background`（終わると知らせが届く）を使う。
背景で起動した呼び出しでも、sleep の後に NDF のコマンドを打つ並びは止める（#1919。時間で待たずに条件で待つ）。
"""

from __future__ import annotations

import re

from ndf.domains.hooks import tables
from ndf.domains.hooks.guards.shell import background_call, is_ndf
from ndf.domains.hooks.verdict import Verdict, deny

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
_HELP = "条件で待つなら Monitor を、終わりを待つなら Bash の run_in_background を使う"


def seconds(args: tuple) -> float:
    """sleep の引数の合計の秒。読めない引数は無限とみなす。"""
    total = 0.0
    for arg in args:
        m = _DURATION.match(arg)
        if m is None:
            return float("inf")
        total += float(m.group(1)) * _UNIT[m.group(2)]
    return total


def check(ctx, svc) -> Verdict | None:
    commands = ctx.shell.commands
    limit = float(tables.policy_value("hook.sleep", "seconds"))
    for i, cmd in enumerate(commands):
        if not cmd.argv or cmd.argv[0] != "sleep":
            continue
        if any(is_ndf(later) for later in commands[i + 1 :]):
            return deny(f"sleep で時間を待ってから NDF のコマンドを打つ並びを止めた。{_HELP}")
        if not background_call(ctx.tool_input) and not cmd.background and seconds(cmd.argv[1:]) > limit:
            return deny(f"前景で {limit:g} 秒を超えて待つ sleep を止めた。{_HELP}")
    return None
