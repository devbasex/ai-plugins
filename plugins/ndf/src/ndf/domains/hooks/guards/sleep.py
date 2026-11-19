"""sleep の guard。前景の `while` / `until` のループの本体の sleep（秒によらない）と、前景で `hook.sleep` の秒を超えて
待つ sleep と、sleep の後に打つ NDF のコマンドを止める（#3851）。

sleep は `bash -c` 系・`eval` の中身とラッパーのコマンド（`timeout 590` など）の後ろも見る（`ShellScript.waits` の
`body_argv`）。`for` のループの中は上限だけで判定する。
待つときは Monitor（条件が成り立つまで見回る）か、Bash の `run_in_background`（終わると知らせが届く）を使う。
背景で起動した呼び出しでも、sleep の後に NDF のコマンドを打つ並びは止める（#1919。時間で待たずに条件で待つ）。
"""

from __future__ import annotations

import os
import re

from ndf.domains.hooks import tables
from ndf.domains.hooks.guards.shell import background_call, is_ndf
from ndf.domains.hooks.verdict import Verdict, deny

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")
_HELP = "条件で待つなら Monitor を、終わりを待つなら Bash の run_in_background を使う"


def seconds(args: tuple) -> float:
    """sleep の引数の合計の秒。読めない引数は無限とみなす。"""
    unit = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    total = 0.0
    for arg in args:
        m = _DURATION.match(arg)
        if m is None:
            return float("inf")
        total += float(m.group(1)) * unit[m.group(2)]
    return total


def check(ctx, svc) -> Verdict | None:
    script = ctx.shell
    for i, cmd in enumerate(script.commands):
        if cmd.argv and cmd.argv[0] == "sleep" and any(is_ndf(later) for later in script.commands[i + 1 :]):
            return deny(f"sleep で時間を待ってから NDF のコマンドを打つ並びを止めた。{_HELP}")
    if background_call(ctx.tool_input):
        return None
    limit = float(tables.policy_value("hook.sleep", "seconds"))
    for cmd in script.waits:
        if not cmd.body_argv or os.path.basename(cmd.body_argv[0]) != "sleep" or cmd.background:
            continue
        if cmd.loop_body:
            return deny(f"ループの中で sleep して待つ呼び出しを止めた。{_HELP}")
        if seconds(cmd.body_argv[1:]) > limit:
            return deny(f"前景で {limit:g} 秒を超えて待つ sleep を止めた。{_HELP}")
    return None
