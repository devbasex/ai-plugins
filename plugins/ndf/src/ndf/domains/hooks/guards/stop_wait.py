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

人へ問う Tool の無いランタイム（コントラクト表の `ask_tool` が null。Codex）では、区分の名前を先頭に書いた本文の質問が
問い合わせの経路である（`asking.md` の「ランタイムごとの扱い」）。引用を除いた本文で質問のある行がどれも認める区分
（組み込みと `.ndf/ask.json` の `extra`）で始まる応答を問うた応答として通し、変更要求の文もその形での出し直しを求める。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from ndf.domains.hooks import budget, reply_judge, reply_wait, tables
from ndf.domains.hooks.strict import _repo_root
from ndf.domains.hooks.verdict import Verdict, passing

# 変更要求の文（#4000 の受け入れ条件 11）。Stop で差し戻すたびに会話へ入るので、その場で要る動作と参照だけにし、理由
# （本文の質問が届かない先・区分の一覧・実行の形ごとの経路）は `development-workflow` の `references/asking.md` に置く。
# `{matched}` はルールで止めたときだけ当たった文を入れ、軽いモデルで止めたときは空にする
REASON = (
    "本文で人の回答を待って終えた{matched}。development-workflow の references/asking.md の「区分」に当たるなら"
    "{route}、当たらなければ推奨の案で決めて理由を報告に 1 行で書き、続ける。"
    "待っていない（引用・ほかの場所の待ちの報告）なら質問の形の文を書き換えて終える。"
)
# 区分に当たる質問の出し直し先。`{tool}` はランタイムの `ask_tool`。Tool の無いランタイムは本文の質問で出す
ROUTE_TOOL = " {tool}（metadata.source に区分）で出し直し"
ROUTE_PROSE = "、区分の名前（ndf-gate:1・ndf-ask:cost など）を先頭に書いた本文の質問で出し直して終え"
# 本文の質問の先頭の区分（`asking.md` の「区分」の表の名前）。行頭の強調・コード・括弧と「区分:」の前置きを許す。
# 名前はトークン全体で照らす（`ndf-gate:10`・`ndf-ask:redline:C9` を認めない）。後ろに空白か行末が続く半角コロンは
# 質問との区切りとして認める（`ndf-gate:1: マージしてよいですか？`）。形は `docs/specifications/ndf-ask-guard.md` の
# 区分の表で、`ndf-ask:<名前>` の名前は組み込みの区分と `.ndf/ask.json` の `extra` にあるものだけを認める（`allowed`）
CATEGORY = re.compile(
    r"^[\s*_`\[(（「【]*(?:区分\s*[:：]\s*[*_`]*)?"
    r"(?P<token>ndf-gate:[12]|ndf-ask:redline:(?:C[1-8]|P[0-9]+|R[0-9]+)|ndf-ask:(?P<name>[a-z0-9][a-z0-9-]*))"
    r"(?![A-Za-z0-9_-]|:(?!\s|$))"
)
# 組み込みの `ndf-ask:<名前>` の区分の SSOT（`allow` の既定値）と、設定 `.ndf/ask.json` の保存先の行
ASK_SCHEMA = "schemas/ndf-settings/ask.schema.json"
ASK_FILE = "ask"
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


def ask_tool(runtime: str) -> Optional[str]:
    """ランタイムの人へ問う Tool（コントラクト表の `ask_tool`）。無いランタイム・表に無いランタイムは None。"""
    return tables.runtimes().get(runtime, {}).get("ask_tool")


def reason(matched: tuple[str, ...] = (), runtime: str = "claude") -> str:
    """変更要求の文。`matched`（ルールが当たった文）があれば先頭の 1 文を「当たった文」として添え、無ければ括弧ごと省く。

    区分に当たる質問の出し直し先は `runtime` の `ask_tool` で、無ければ区分を先頭に書いた本文の質問にする。
    """
    tool = ask_tool(runtime)
    route = ROUTE_TOOL.format(tool=tool) if tool else ROUTE_PROSE
    if not matched:
        return REASON.format(matched="", route=route)
    first = " ".join(matched[0].split())
    if len(first) > MATCHED_CHARS:
        first = first[: MATCHED_CHARS - 1] + "…"
    return REASON.format(matched=f"（当たった文:「{first}」）", route=route)


def builtin_names() -> frozenset[str]:
    """組み込みの `ndf-ask:<名前>` の区分の名前（設定のスキーマの `allow` の既定値）。"""
    return frozenset(tables.load(ASK_SCHEMA)["properties"]["allow"]["default"])


def _ask_path(cwd: str) -> Optional[Path]:
    """作業ディレクトリのリポジトリの設定 `.ndf/ask.json`。リポジトリの根が無ければ None（git を起動しない）。"""
    root = _repo_root(Path(cwd))
    if root is None:
        return None
    row = next(r for r in tables.load("domains/ndf-files.json")["rows"] if r["id"] == ASK_FILE)
    location = next(r for r in tables.load("domains/locations.json")["rows"] if r["id"] == row["location"])
    return root / location["path"] / row["path"]


def extra_names(cwd: str) -> frozenset[str]:
    """設定 `.ndf/ask.json` の `extra` の名前。設定が無い・読めない・形が違えば空（足す区分は無い）。"""
    path = _ask_path(cwd)
    if path is None or not path.is_file():
        return frozenset()
    try:
        got = tables.json_object(path.read_text(encoding="utf-8"))
    except OSError:  # 読めない設定は足す区分が無いものとして扱う
        return frozenset()
    extra = got.get("extra") if got else None
    if not isinstance(extra, list):
        return frozenset()
    return frozenset(e["name"] for e in extra if isinstance(e, dict) and isinstance(e.get("name"), str))


def allowed(names: set[str], cwd: str) -> bool:
    """`ndf-ask:<名前>` の名前がどれも認める区分か。組み込みに無い名前があるときだけ `.ndf/ask.json` を読む。"""
    if not names:
        return True
    if "redline" in names:
        return False
    rest = names - builtin_names()
    return not rest or rest <= extra_names(cwd)


def asked_in_prose(ctx) -> bool:
    """人へ問う Tool の無いランタイムで、区分付きの本文の質問で問うたか。

    かぎ括弧の引用を除いた行ごとに質問の文を探し（`reply_wait.line_questions`）、質問のある行がどれも認める区分の
    名前で始まる（名前が引用の中に無い）応答だけを問うた応答とする。区分の無い行の質問（見出し・最後の 3 行より前を
    含む）・区分だけの行の後に別の行で問う・未宣言の区分の応答は、ルールの判定へ回す。区分の宣言は、組み込みに無い
    `ndf-ask:<名前>` の質問の行があるときだけ読む。
    """
    if ask_tool(ctx.runtime):
        return False
    lines = ctx.reply_prose_lines
    found = [CATEGORY.match(line.text) for line in lines]
    if not any(found):
        return False
    asked = False
    names: set[str] = set()
    for got, line in zip(found, reply_wait.line_questions(lines)):
        if not line.asks:
            continue
        asked = True
        if got is None:
            return False
        start, end = got.span("token")
        if line.unquoted[start:end] != line.masked[start:end]:
            # 区分の名前が引用の中にある（「ndf-gate:1」の件…）行は、区分の無い行の質問とする
            return False
        if got.group("name") is not None:
            names.add(got.group("name"))
    return asked and allowed(names, ctx.cwd)


def _result(
    reply: Optional[str],
    judge: str,
    wait: bool,
    judged: Optional[reply_judge.Judged] = None,
    matched: tuple[str, ...] = (),
    runtime: str = "claude",
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
    if ctx.runtime not in reply_judge.judge_setting("runtimes"):
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
    return min(svc.deadline - found.margin - monotonic(), float(reply_judge.judge_setting("seconds")))


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
    if ctx.asked_this_turn or asked_in_prose(ctx):
        return _result(None, "skip:asked", False)
    # ルールだけで決まる判定: 本文の種類 → （`judge`, 変更要求か）
    by_rule = {
        "elsewhere_wait": ("skip:elsewhere", False),
        "closing_report": ("skip:closing", False),
        "prose_wait": ("rule", True),
    }
    kind = ctx.last_reply_kind
    reading = ctx.reply_reading
    if kind in by_rule:
        # ルールで止めたときだけ当たった文を添える
        return _result(kind, *by_rule[kind], matched=reading.matched, runtime=ctx.runtime)
    # 見出しで承認・選択を明示的に求める本文（`heading_wait`）は、モデルを使えない Stop ではルールの待ちとして変更を要求する
    rule_wait = kind == "heading_wait"
    # ルールの待ちとして止めるときだけ当たった見出しを添える（モデルの wait で止めたときは添えない）
    heading = reading.matched if rule_wait else ()
    keep = reading.delegated
    text = reply_judge.judge_text(ctx.last_reply, ctx.reply_code_lines, int(reply_judge.judge_setting("max_chars")), keep)
    skipped = _quick_skip(ctx, svc, text)
    if skipped is not None:
        waited = skipped in _AFTER_LIMIT and not svc.counter.pushback_left() and svc.counter.model_waited()
        return _result(kind, skipped, waited or rule_wait, matched=heading, runtime=ctx.runtime)
    seconds = _seconds(ctx, svc)
    if seconds < MIN_SECONDS:
        return _result(kind, "skip:time", rule_wait, matched=heading, runtime=ctx.runtime)
    judge, wait, judged = _ask(svc, text, seconds)
    matched = heading if judge != "model" else ()
    return _result(kind, judge, wait or (rule_wait and judge != "model"), judged, matched=matched, runtime=ctx.runtime)
