"""AskUserQuestion の guard（#3345 の決定 88・93・#3035）。

質問を中継のシグナルファイル（中継の下）とオーガナイザーの質問の保存先（オーガナイザーのレーン）へ書き、待ちの知らせを
送信待ちへ書く。転送を待つのは中継とオーガナイザーで、hook は待たない。

中継の下では、ロックの中で今の起動（`NDF_RELAY_LAUNCH` と `launch.json`）の質問として書けたときだけ通し、書けなければ
拒否する（#3814 の I7）。拒否にするのはこの記録の失敗だけで、オーガナイザーへの書き込みと待ちの知らせの失敗（例外を含む）は
通し、例外の種類を計測の `forward_errors` に残す（guard の表の `fail_closed` で例外が拒否にならないよう、ここで捕まえる）。
コントラクトの誤りで中継の変数の名前を引けなければ（`signals.keys_known`）、どこにも書かずに通す（#4126 の決定 3）。
中継へ記録できた後に締め切りの割り込みが来たとき（オーガナイザーのロックの待ちなど）も、記録済みの質問は消さずに通す。
"""

from __future__ import annotations

from typing import Optional

from ndf.domains.hooks import outbox, tables
from ndf.domains.relay import signals
from ndf.domains.hooks.verdict import Verdict, deny, passing

_recorded: Optional[dict] = None
"""中継へ記録できた事象の payload（同じ実行の `on_deadline` が受け取る payload と同じオブジェクト）。実行ごとに payload は
新しく解析されるので、前の実行の記録と取り違えない。"""

QUESTION_NOT_RECORDED = "中継が子を止めている途中で、質問を記録できなかった。止まらずに続いていれば、もう一度 AskUserQuestion を呼ぶ"


def forward_wait() -> float:
    """質問のファイルのロックを待つ秒数（`hook.ask-forward` の `seconds`）。`ask-answered`・`ask-closed` と共通。"""
    return float(tables.policy_value("hook.ask-forward", "seconds"))


def _first_question(tool_input: dict) -> str:
    questions = tool_input.get("questions")
    first = questions[0] if isinstance(questions, list) and questions else None
    text = first.get("question") if isinstance(first, dict) else None
    return text if isinstance(text, str) else ""


def check(ctx, svc) -> Verdict:
    global _recorded
    if not signals.keys_known():
        return passing(forwarded=[])
    wait = forward_wait()
    forwarded: list[str] = []
    relay = signals.relay_dir(ctx.env)
    if relay is not None:
        if not _write_relay(relay, ctx, wait):
            return deny(QUESTION_NOT_RECORDED)
        _recorded = ctx.payload
        forwarded.append("relay")
    errors = {}
    for name, forward in (("organizer", _to_organizer), ("notify", _to_notify)):
        try:
            if forward(ctx, svc, wait):
                forwarded.append(name)
        except Exception as exc:  # noqa: BLE001 ファイル: 中継の記録の後の転送の失敗は質問を止めず、種類を計測に残す
            errors[name] = type(exc).__name__
    return passing(forwarded=forwarded, forward_errors=errors) if errors else passing(forwarded=forwarded)


def on_deadline(env, payload) -> Optional[str]:
    """エントリポイントの締め切りを過ぎたときの拒否の理由。中継の下なら拒否し、外なら None（通す）。

    この実行の `check` が中継へ記録し終えていれば、その後の転送・知らせの途中の割り込みなので None（記録を残して通す）。
    割り込みが質問を書いた後に来たときに備え、同じ `tool_use_id` の質問をロックを待たずに消す（取れなければ残し、
    `ask-closed` が閉じた質問として消す）。
    """
    if not signals.keys_known():
        return None
    relay = signals.relay_dir(env)
    if relay is None or (_recorded is not None and _recorded is payload):
        return None
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str) and tool_use_id:
        try:
            signals.drop_question(relay, {"tool_use_id": tool_use_id}, 0, launch=signals.relay_launch(env))
        except OSError:  # 消せなくても拒否は出す（残った質問は ask-closed が消す）
            return QUESTION_NOT_RECORDED
    return QUESTION_NOT_RECORDED


def _to_organizer(ctx, svc, wait: float) -> bool:
    """オーガナイザーのレーンなら質問の保存先へ書けたか。"""
    lane = signals.organizer_question_path(ctx.env)
    return lane is not None and signals.write_question(lane, ctx.session_id, ctx.payload, wait)


def _to_notify(ctx, svc, wait: float) -> bool:
    """知らせの送り先があれば待ちの知らせを送信待ちへ書いたか。"""
    if not outbox.notify_configured(ctx.env):
        return False
    payload = {"text": _first_question(ctx.tool_input), "cwd": ctx.cwd, "session_id": ctx.session_id}
    outbox.enqueue(svc.state_dir, "question", payload, ctx.env)
    outbox.kick(svc.state_dir, ctx.env)
    return True


def _write_relay(relay, ctx, wait: float) -> bool:
    """中継の質問のファイルへ今の起動の質問として書けたか。書けない `OSError` も偽（質問を拒否する側へ倒す）。"""
    try:
        return signals.write_question(
            relay / signals.QUESTION_FILE, ctx.session_id, ctx.payload, wait, launch=signals.relay_launch(ctx.env)
        )
    except OSError:  # noqa: NDF401 ファイル: 書けなければ質問を拒否する（I7）
        return False
