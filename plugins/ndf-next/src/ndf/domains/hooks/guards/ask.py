"""AskUserQuestion の guard（#3345 の決定 88・93・#3035）。

問いを中継のシグナルファイル（中継の下）とオーガナイザーの問いの置き場（オーガナイザーのレーン）へ書き、待ちの知らせを
送信待ちへ書く。転送を待つのは中継とオーガナイザーで、hook は待たない。止めない。
"""

from __future__ import annotations

from ndf.domains.hooks import outbox, tables
from ndf.domains.relay import signals
from ndf.domains.hooks.verdict import Verdict


def _first_question(tool_input: dict) -> str:
    questions = tool_input.get("questions")
    first = questions[0] if isinstance(questions, list) and questions else None
    text = first.get("question") if isinstance(first, dict) else None
    return text if isinstance(text, str) else ""


def check(ctx, svc) -> Verdict:
    wait = float(tables.policy_value("hook.ask-forward", "seconds"))
    forwarded = []
    relay = signals.relay_dir(ctx.env)
    if relay is not None and signals.write_question(relay / signals.QUESTION_FILE, ctx.session_id, ctx.payload, wait):
        forwarded.append("relay")
    lane = signals.organizer_question_path(ctx.env)
    if lane is not None and signals.write_question(lane, ctx.session_id, ctx.payload, wait):
        forwarded.append("organizer")
    if outbox.notify_configured(ctx.env):
        outbox.enqueue(svc.state_dir, "question", {"text": _first_question(ctx.tool_input), "cwd": ctx.cwd, "session_id": ctx.session_id})
        outbox.kick(svc.state_dir)
        forwarded.append("notify")
    return Verdict("pass", notes={"forwarded": forwarded})
