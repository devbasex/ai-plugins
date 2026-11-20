"""質問が閉じたら中継の質問のファイルを消す guard（`ask-answered`・`ask-closed`。#3814 の決定 3・I5）。

回答は `AskUserQuestion` の PostToolUse（同じ `tool_use_id` の質問だけを消す）、回答されずに閉じた質問は Stop か
UserPromptSubmit（今の起動の hook なら中身を問わず消す）で知る。消すのはロックの中で、hook の起動の ID が今の起動の
記録と同じときだけである（`signals.drop_question`）。止めない。結果は計測の `notes` の `cleared` に残す。
"""

from __future__ import annotations

from ndf.domains.hooks import tables
from ndf.domains.hooks.verdict import Verdict, passing
from ndf.domains.relay import signals


def check(ctx, svc) -> Verdict:
    relay = signals.relay_dir(ctx.env)
    launch = signals.relay_launch(ctx.env)
    if relay is None or launch is None or not signals.question_present(relay):
        return passing()
    match = None
    if ctx.event == "PostToolUse":
        tool_use_id = ctx.payload.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return passing()
        match = {"tool_use_id": tool_use_id}
    wait = float(tables.policy_value("hook.ask-forward", "seconds"))
    return passing(cleared=signals.drop_question(relay, match, wait, launch=launch))
