"""本文の待ちの guard（#2324・#4000）。

最後の応答が AskUserQuestion を使わずに本文で人の回答を待って終えたら変更を要求する。判定はルール（`reply_wait`）を
前に置き、ルールが待ちと読んだ本文はモデルを呼ばずに変更を要求する。ルールに当たらない本文だけを、呼ばない条件を
見たうえで軽いモデル（`reply_judge.ask`）へ渡す（#4000 の決定 1・5・6・10・11）。条件は詳細設計の「処理の手順」の
表の順に見て、最初に当たった 1 つを notes の `judge` に書く。本文は notes に入れない。

別の場所（ほかのセッション・レーン）の待ちを報告しただけの応答は変更を要求しない。変更要求の上限の後はエントリポイントが
通して待ちの知らせを書く（#3345 の決定 92）。上限の後に変更要求を返すのは、待ちの知らせへ渡すためだけである。
"""

from __future__ import annotations

import os
import time
from typing import Optional

from ndf.domains.hooks import budget, reply_judge
from ndf.domains.hooks.verdict import Verdict, passing

REASON = (
    "本文で人の回答を待って終えた。人に問うときは AskUserQuestion を使う（中継とオーガナイザーへ質問が届き、"
    "待ちの知らせも送られる）。問わずに進められるなら続ける"
)
# 判定の子を起動するのに要る残りの秒の下限（詳細設計の「処理の手順」）
MIN_SECONDS = 0.5
# 上限の後でもモデルの wait のフラグがあれば変更要求を返す理由（待ちの知らせへ渡す。決定 5）
_AFTER_LIMIT = ("skip:no_prose", "skip:limit")
# 締め切りの時計（`svc.deadline` と同じ基準）と、休みのマーカーの時計（別のプロセスと分け合う）。テストで差し替える
monotonic = time.monotonic
wall = time.time


def _result(reply: Optional[str], judge: str, wait: bool, judged: Optional[reply_judge.Judged] = None) -> Verdict:
    """判定と notes。`reply` が None（ルールを当てていない）なら notes に入れない。"""
    notes: dict = {"judge": judge, "judge_ms": round(judged.ms, 3) if judged else 0, "judge_calls": judged.calls if judged else 0}
    if reply is not None:
        notes = {"reply": reply, **notes}
    return Verdict("pushback", reason=REASON, notes=notes) if wait else passing(**notes)


def _quick_skip(ctx, svc, text: str) -> Optional[str]:
    """`/proc` も時計も読まずに分かる、モデルを呼ばない理由。無ければ None。"""
    if not text:
        return "skip:no_prose"
    if ctx.background_tasks:
        return "skip:background"
    if ctx.runtime != "claude":
        return "skip:runtime"
    if not svc.counter.pushback_left():
        return "skip:limit"
    if reply_judge.resting(svc.state_dir, wall()):
        return "skip:rest"
    return None


def _seconds(ctx, svc) -> float:
    """判定に使える秒: 締め切りから余白を 1 回だけ引いた残りと、設定の秒の小さい方。予算が無ければ 0。"""
    found = budget.budget_for(ctx.runtime, ctx.event)
    if found is None:
        return 0.0
    return min(svc.deadline - found.margin - monotonic(), float(reply_judge._judge("seconds")))


def _ask(svc, text: str, seconds: float) -> tuple[str, bool, Optional[reply_judge.Judged]]:
    """親の起動を確かめてからモデルへ問う。返りは（`judge`, 変更要求か, 判定の結果）。"""
    launch = reply_judge.parent_launch(os.environ, os.getpid())
    if launch is None:
        return "skip:endpoint", False, None
    if reply_judge.endpoint_resting(svc.state_dir, wall(), os.environ, launch):
        return "skip:rest", False, None
    judged = reply_judge.ask(text, seconds, os.environ, launch)
    if judged.kind == "wait":
        svc.counter.mark_model_wait()
        return "model", True, judged
    if judged.kind == "report":
        return "model", False, judged
    reply_judge.mark_rest(svc.state_dir, str(judged.failure), wall(), os.environ, launch)
    return f"fallback:{judged.failure}", False, judged


def check(ctx, svc) -> Verdict:
    if not ctx.has_reply_prose:
        return _result("none", "skip:empty", False)
    if ctx.asked_this_turn:
        return _result(None, "skip:asked", False)
    kind = ctx.last_reply_kind
    if kind == "elsewhere_wait":
        return _result(kind, "skip:elsewhere", False)
    if kind == "prose_wait":
        return _result(kind, "rule", True)
    text = reply_judge.judge_text(ctx.last_reply, ctx.reply_code_lines, int(reply_judge._judge("max_chars")))
    skipped = _quick_skip(ctx, svc, text)
    if skipped is not None:
        waited = skipped in _AFTER_LIMIT and not svc.counter.pushback_left() and svc.counter.model_waited()
        return _result(kind, skipped, waited)
    seconds = _seconds(ctx, svc)
    if seconds < MIN_SECONDS:
        return _result(kind, "skip:time", False)
    judge, wait, judged = _ask(svc, text, seconds)
    return _result(kind, judge, wait, judged)
