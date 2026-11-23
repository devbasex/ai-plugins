"""本文の待ちの guard（#2324・#4000）。

最後の応答が AskUserQuestion を使わずに本文で人の回答を待って終えたら変更を要求する。判定はルール（`reply_wait`）を
前に置き、ルールが待ちと読んだ本文はモデルを呼ばずに変更を要求する。ルールに当たらない本文だけを、呼ばない条件を
見たうえで軽いモデル（`reply_judge.ask`）へ渡す（#4000 の決定 1・5・6・10・11）。条件は詳細設計の「処理の手順」の
表の順に見て、最初に当たった 1 つを notes の `judge` に書く。本文は notes に入れない。

別の場所（ほかのセッション・レーン）の待ちを報告しただけの応答と、定型の結び（「引き続きよろしくお願いします。」）の前が
完了の報告だけの応答は、モデルを呼ばずに通す。見出しで承認・選択を明示的に求め、後に本文が続く応答（`heading_wait`）は
モデルの判定に従い、モデルを使えない Stop（Codex・休み・時間切れ・回答なしなど）ではルールの待ちとして変更を要求する。
モデルへ渡す本文が字数の上限で切られても、判定を委ねた見出しは先頭に残す。変更要求の上限の後はエントリポイントが
通して待ちの知らせを書く（#3345 の決定 92）。上限の後に変更要求を返すのは、待ちの知らせへ渡すためだけである。

質問の Tool が無いランタイム（Codex）の対話実行で本文の最後の行に区分の行を置いた応答は正規の問いとして通し、その Stop で
エントリポイントが待ちの知らせを書く。人のいない実行（Factory）ではセグメントの報告で問いを返す案内にし、報告を置いた
応答は guard `unattended` に任せて通す。
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

from ndf.domains.hooks import budget, reply_judge
from ndf.domains.hooks import report as ur
from ndf.domains.hooks.guards import unattended
from ndf.domains.hooks.verdict import Verdict, passing

# 変更要求の文（#4000 の受け入れ条件 11）。10 系の `relay_lib/mark.py` の `WAIT_REASON` を出発点にした。区分の一覧は写さず
# `development-workflow` の `references/asking.md` を指す。`{matched}` はルールで止めたときだけ当たった文を入れ、軽いモデルで
# 止めたときは空にする。`{ask}` は実行形態（ランタイム）ごとの問い方で、`ASK` から選ぶ
REASON = (
    "本文で人の回答を待って終えた{matched}。本文の問いは中継とオーガナイザーへ届かず、待ちの知らせも出ず、/goal も止まらない。"
    "人へ問えるのは承認ゲート（設計の承認は ndf-gate:1、本番系へ届く操作の承認は ndf-gate:2）と、development-workflow の "
    "references/asking.md の「区分」に当たる問いだけである。{ask}"
    "当たらなければ人へ回さず推奨の案で決め、決めたことと理由を報告に 1 行で書いて続ける。"
    "問いを出した後も、答えを待たずに進められる作業を続ける。人の回答を待っていない（引用・ほかの場所の待ちの報告）なら、"
    "問いの形の文を書き換えて終える。"
)
# ランタイムごとの問い方。質問の Tool が無いランタイム（Codex。`contracts/hooks/runtimes.json` の skipped）は、本文の最後の
# 行に区分の行（`区分: ndf-gate:1`）を置いて終えれば、その Stop を通して待ちの知らせを書く（`CATEGORY_RE`）。表に無い
# ランタイムは質問の Tool が無いものとして扱う
ASK = {
    "claude": "当たるなら、AskUserQuestion の metadata.source に区分を付けて出し直す（材料は本文に書いてよい）。",
}
ASK_WITHOUT_TOOL = (
    "このランタイムには AskUserQuestion が無い。当たるなら、問いの後の本文の最後の行に「区分: ndf-gate:1」の形で区分を書いて"
    "終える（その Stop は止めずに通し、待ちの知らせを出す）。承認を得るまで承認の要る操作（マージ・本番系へ届く操作）を行わない。"
)
# 人のいない実行（Factory。`unattended.is_conductor`）の問い方。人は本文を読まず、ランナーはセグメントの報告を読む
ASK_FACTORY = (
    "これは人のいない実行で、本文の問いは人へ届かない。当たるなら、最後の応答の末尾のセグメントの報告に「結果: 判断が要る」と"
    "区分・質問・材料を書いて終える（development-workflow の references/asking.md の「人のいない実行」）。"
)
# Codex の対話実行で正規の問いと読む区分の行（本文の最後の行。前に `- ` を許し、区分はバッククォートで囲んでもよい）
CATEGORY_RE = re.compile(r"^区分[ \t]*[:：][ \t]*`?(ndf-gate:\d+|ndf-ask:[A-Za-z0-9][\w:.-]*)`?[ \t]*$")
# 区分の行で問いを受けた Stop の notes のキー（値は区分）。エントリポイントがこれを見て待ちの知らせを書く
ASKED_NOTE = "prose_ask"
# 問い方を選ぶ鍵のうち、人のいない実行を表す値（ランタイムの名前と重ならない）
FACTORY = "factory"
# 計測と待ちの知らせへ渡す理由。応答の本文（当たった文）を含めない
LOGGED = "本文で人の回答を待って終えた（stop-wait）"
# 当たった文として変更要求の文に添える字数の上限（長い文は末尾を「…」で切る）
MATCHED_CHARS = 120
# 判定の子を起動するのに要る残りの秒の下限（詳細設計の「処理の手順」）
MIN_SECONDS = 0.5
# 上限の後でもモデルの wait のフラグがあれば変更要求を返す理由（待ちの知らせへ渡す。決定 5）
_AFTER_LIMIT = ("skip:no_prose", "skip:limit")
# 締め切りの時計（`svc.deadline` と同じ基準）と、休みのマーカーの時計（別のプロセスと分け合う）。テストで差し替える
monotonic = time.monotonic
wall = time.time


def reason(matched: tuple[str, ...] = (), runtime: str = "claude") -> str:
    """変更要求の文。`matched`（ルールが当たった文）があれば先頭の 1 文を「当たった文」として添え、無ければ括弧ごと省く。

    問い方は `runtime` で選ぶ（`FACTORY` は人のいない実行のセグメントの報告、質問の Tool が無いランタイムは区分の行の案内）。
    """
    ask = ASK_FACTORY if runtime == FACTORY else ASK.get(runtime, ASK_WITHOUT_TOOL)
    if not matched:
        return REASON.format(matched="", ask=ask)
    first = " ".join(matched[0].split())
    if len(first) > MATCHED_CHARS:
        first = first[: MATCHED_CHARS - 1] + "…"
    return REASON.format(matched=f"（当たった文:「{first}」）", ask=ask)


def _result(
    runtime: str,
    reply: Optional[str],
    judge: str,
    wait: bool,
    judged: Optional[reply_judge.Judged] = None,
    matched: tuple[str, ...] = (),
) -> Verdict:
    """判定と notes。`reply` が None（ルールを当てていない）なら notes に入れない。`matched` はルールで止めたときだけ渡す。

    当たった文は会話へ返す `reason` にだけ入れ、計測と待ちの知らせへ渡す `logged` は本文を含まない固定の文にする。
    """
    notes: dict = {"judge": judge, "judge_ms": round(judged.ms, 3) if judged else 0, "judge_calls": judged.calls if judged else 0}
    if reply is not None:
        notes = {"reply": reply, **notes}
    return Verdict("pushback", reason=reason(matched, runtime), notes=notes, logged=LOGGED) if wait else passing(**notes)


def _quick_skip(ctx, svc, text: str) -> Optional[str]:
    """`/proc` も時計も読まずに分かる、モデルを呼ばない理由。無ければ None。"""
    if not text:
        return "skip:no_prose"
    if ctx.background_tasks:
        return "skip:background"
    if ctx.runtime not in reply_judge._judge("runtimes"):
        return "skip:runtime"
    if not svc.counter.pushback_left():
        return "skip:limit"
    if svc.decided:
        return "skip:decided"
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


def _mode(ctx, svc) -> str:
    """問い方を選ぶ鍵。人のいない実行の conductor なら `FACTORY`、ほかはランタイムの名前。"""
    return FACTORY if unattended.is_conductor(ctx, svc) else ctx.runtime


def _asked_category(ctx) -> Optional[str]:
    """本文の最後の行が区分の行（`CATEGORY_RE`）なら区分。質問の Tool があるランタイムでは読まない（None）。"""
    if ctx.runtime in ASK:
        return None
    m = CATEGORY_RE.match(ctx.reply_prose_lines[-1].text)
    return m.group(1) if m else None


def check(ctx, svc) -> Verdict:
    if not ctx.has_reply_prose:
        return _result(ctx.runtime, "none", "skip:empty", False)
    if ctx.asked_this_turn:
        return _result(ctx.runtime, None, "skip:asked", False)
    mode = _mode(ctx, svc)
    # 人のいない実行でセグメントの報告を置いた応答は、形を unattended の guard が確かめる（本文の問いを待ちと読まない）
    if mode == FACTORY and ur.result_value(ctx.last_reply):
        return _result(mode, None, "skip:report", False)
    # 質問の Tool が無いランタイムの対話実行で区分の行を置いた応答は正規の問い。通して待ちの知らせを書く（エントリポイント）
    category = _asked_category(ctx) if mode != FACTORY else None
    if category is not None:
        return passing(**{"judge": "skip:category", "judge_ms": 0, "judge_calls": 0, ASKED_NOTE: category})
    kind = ctx.last_reply_kind
    if kind == "elsewhere_wait":
        return _result(mode, kind, "skip:elsewhere", False)
    if kind == "closing_report":
        return _result(mode, kind, "skip:closing", False)
    reading = ctx.reply_reading
    if kind == "prose_wait":
        return _result(mode, kind, "rule", True, matched=reading.matched)
    # 見出しで承認・選択を明示的に求める本文（`heading_wait`）は、モデルを使えない Stop ではルールの待ちとして変更を要求する
    rule_wait = kind == "heading_wait"
    # ルールの待ちとして止めるときだけ当たった見出しを添える（モデルの wait で止めたときは添えない）
    heading = reading.matched if rule_wait else ()
    keep = reading.delegated
    text = reply_judge.judge_text(ctx.last_reply, ctx.reply_code_lines, int(reply_judge._judge("max_chars")), keep)
    skipped = _quick_skip(ctx, svc, text)
    if skipped is not None:
        waited = skipped in _AFTER_LIMIT and not svc.counter.pushback_left() and svc.counter.model_waited()
        return _result(mode, kind, skipped, waited or rule_wait, matched=heading)
    seconds = _seconds(ctx, svc)
    if seconds < MIN_SECONDS:
        return _result(mode, kind, "skip:time", rule_wait, matched=heading)
    judge, wait, judged = _ask(svc, text, seconds)
    return _result(mode, kind, judge, wait or (rule_wait and judge != "model"), judged, matched=heading if judge != "model" else ())
