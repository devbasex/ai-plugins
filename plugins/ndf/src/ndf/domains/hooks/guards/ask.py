"""AskUserQuestion の guard（#3345 の決定 88・93・#3035）。

質問を中継のシグナルファイル（中継の下）とオーガナイザーの質問の保存先（オーガナイザーのレーン）へ書き、待ちの知らせを
送信待ちへ書く。転送を待つのは中継とオーガナイザーで、hook は待たない。

中継の下では、ロックの中で今の起動（`NDF_RELAY_LAUNCH` と `launch.json`）の質問として書けたときだけ通し、書けなければ
拒否する（#3814 の I7）。中継の外とオーガナイザー・待ちの知らせの失敗は今と同じく通す。
"""

from __future__ import annotations

from ndf.domains.hooks import outbox, tables
from ndf.domains.relay import signals
from ndf.domains.hooks.verdict import Verdict, deny, passing

QUESTION_NOT_RECORDED = "中継が子を止めている途中で、質問を記録できなかった。止まらずに続いていれば、もう一度 AskUserQuestion を呼ぶ"


def _first_question(tool_input: dict) -> str:
    questions = tool_input.get("questions")
    first = questions[0] if isinstance(questions, list) and questions else None
    text = first.get("question") if isinstance(first, dict) else None
    return text if isinstance(text, str) else ""


def check(ctx, svc) -> Verdict:
    wait = float(tables.policy_value("hook.ask-forward", "seconds"))
    forwarded = []
    relay = signals.relay_dir(ctx.env)
    if relay is not None:
        if not _write_relay(relay, ctx, wait):
            return deny(QUESTION_NOT_RECORDED)
        forwarded.append("relay")
    lane = signals.organizer_question_path(ctx.env)
    if lane is not None and signals.write_question(lane, ctx.session_id, ctx.payload, wait):
        forwarded.append("organizer")
    if outbox.notify_configured(ctx.env):
        payload = {"text": _first_question(ctx.tool_input), "cwd": ctx.cwd, "session_id": ctx.session_id}
        outbox.enqueue(svc.state_dir, "question", payload, ctx.env)
        outbox.kick(svc.state_dir, ctx.env)
        forwarded.append("notify")
    return passing(forwarded=forwarded)


def _write_relay(relay, ctx, wait: float) -> bool:
    """中継の質問のファイルへ今の起動の質問として書けたか。書けない `OSError` も偽（質問を拒否する側へ倒す）。"""
    try:
        return signals.write_question(
            relay / signals.QUESTION_FILE, ctx.session_id, ctx.payload, wait, launch=signals.relay_launch(ctx.env)
        )
    except OSError:  # noqa: NDF401 ファイル: 書けなければ質問を拒否する（I7）
        return False
