"""並列の計画の問いの判定（純粋な関数。ほかの `lib/` を読まない）。

- 同じ原因の問いのまとめ方（#2436 の異常系 5）: `cause_of`。`parallel_run` の問いの受け付けが使う
- 一時レーンの依頼と答えの一覧（#3597 の決定 8）: `asked_of`。`parallel.py helper` が使う
- 区分つきの問いの対象の行の読み取り・同じ問いの前の依頼の引き当て・判定の記録の id からの結果の復元（#3603 の決定 7・8）:
  `target_line`・`prior_of`・`outcome_of`。`parallel.py request-ask` と問いの hook が使う
"""

from __future__ import annotations

import json
from dataclasses import dataclass

KIND_GATE1, KIND_REUSED, KIND_ASK = "gate1", "reused", "ask"  # 送り分けの結果の種類（台帳の行の kind と同じ。`parallel_ask.route_ask`）


def cause_of(msg: dict) -> str:
    """同じ原因の問いの鍵（異常系 5）: 区分と選択肢と、空白をまとめてレーン名・パス・番号を伏せた問いの文面。

    まとめるのは人にしかできない操作（`ndf-ask:human-action`。認証切れ・ディスク不足・ネットワークなど環境の問題）だけで、
    取り消せない操作・レッドライン・費用の問いは番号の違いで別の対象になり得るため、まとめない（空の鍵を返す）。
    まとめた問いには各レーンの元の文面（`merged`）を残し、オーガナイザーが問いに並べる。"""
    import re

    if msg.get("kind") != "ask" or msg.get("source") != "ndf-ask:human-action":
        return ""
    text = str(msg.get("question") or "")
    if msg.get("lane"):
        text = text.replace(str(msg["lane"]), "<lane>")
    text = re.sub(r"(/[^\s（）()]+)+", "<path>", text)
    text = re.sub(r"\d+", "<n>", text)
    return f"{msg.get('source')}|{json.dumps(msg.get('options') or [], ensure_ascii=False)}|{' '.join(text.split())}"


def asked_of(rows: list[dict], lane: str, settled: set = frozenset()) -> list[dict]:
    """一時レーン `lane` が送った依頼（`gate2`・`ask`）と、その答えの一覧（台帳の順。#3597 の決定 8）。`rows` は台帳の行で、
    最初の `opened` の行より後のものを渡す。`settled` は処理済みのリリース承認ゲートの id（`parallel_ledger.settled_gates`）で、
    確定か締め（`answer` の行を持たない処理）で済んだ `gate2` に `settled: true` を付ける。

    答えの中身（`answer`）を載せるのは、id で問いの決まる `gate2` と、問いの対象（`target`）を持つ `ask` である（#3603 の決定 9）。
    対象を持たない `ask` は、前の答えを今の問いへ当てられないため、答えがあっても `answered: true` だけを付ける（戻せない操作の答えを、
    対象の分からない問いへ当てない）。判定の正本は `request-ask` の同じ問いの判定（`prior_of`）である。"""
    answers = answers_by_id(rows)
    out = []
    for r in rows:
        msg = r.get("msg") or {}
        if r.get("kind") not in ("gate2", "ask") or msg.get("lane") != lane:
            continue
        item = {"id": r.get("id"), "kind": r.get("kind"), "source": msg.get("source"), "target": msg.get("target"), "at": r.get("at")}
        if (a := answers.get(r.get("id"))) is not None:
            if r.get("kind") == "gate2" or msg.get("target"):
                item["answer"] = a
            else:
                item["answered"] = True
        if r.get("kind") == "gate2" and r.get("id") in settled:
            item["settled"] = True
        out.append(item)
    return out


def answers_by_id(rows: list[dict]) -> dict[str, dict]:
    """依頼の id ごとの答えの中身 `{result, choice}`（同じ id の答えは 1 行だけ）。"""
    return {
        r.get("id"): {"result": (r.get("msg") or {}).get("result"), "choice": list((r.get("msg") or {}).get("choice") or [])}
        for r in rows
        if r.get("kind") == "answer"
    }


TARGET_PREFIX = "対象: "  # 対象の行の頭（区分つきの問いの最初の問いの文に置く 1 行。#3603 の決定 7）


@dataclass(frozen=True)
class AskTarget:
    """対象の行の値（`raw`）と、先端を足した問いの対象（`target`。`<ブランチ名>@<40 桁>` か `#<番号>`。求められなければ空）。"""

    raw: str
    target: str


@dataclass(frozen=True)
class Prior:
    """同じ問いの前の依頼の id と答え（`{result, choice}`。答え待ちなら None）。"""

    id: str
    answer: dict | None


def first_question(tool_input: dict) -> str:
    """最初の問いの文（`questions[0].question`）。無ければ空。"""
    qs = (tool_input or {}).get("questions") or []
    first = qs[0] if qs and isinstance(qs[0], dict) else {}
    return str(first.get("question") or "")


def target_line(tool_input: dict) -> str:
    """最初の問いの文の最後の対象の行（`対象: <ブランチ名>` か `対象: #<番号>`）の値。前後の空白を除く。無ければ空。"""
    got = ""
    for line in first_question(tool_input).splitlines():
        s = line.strip()
        if s.startswith(TARGET_PREFIX.strip()):
            got = s[len(TARGET_PREFIX.strip()) :].strip()
    return got


def prior_of(rows: list[dict], lane: str, source: str, target: str) -> Prior | None:
    """同じ一時レーンの名前・区分・問いの対象の `ask` の依頼のうち最も新しいものと、その答え（#3603 の決定 8）。
    `rows` は最初の `opened` の行より後の台帳の行。`target` が空なら比べない（None。I6）。"""
    if not target:
        return None
    hit = None
    for r in rows:
        msg = r.get("msg") or {}
        if r.get("kind") == "ask" and msg.get("lane") == lane and msg.get("source") == source and msg.get("target") == target:
            hit = r
    if hit is None:
        return None
    return Prior(str(hit.get("id") or ""), answers_by_id(rows).get(hit.get("id")))


def outcome_of(rows: list[dict], ledger_id: str) -> dict | None:
    """判定の記録の id `ledger_id` を持つ `gate1`・`ask`・`reused` の最後の行から、`request-ask` の `metrics` と同じ形
    （`kind`・`id`・`answer`・`target`・`sprint_state`）を戻す（hook が上限の秒を過ぎた後に保留の側へ倒すため。I10）。無ければ None。"""
    if not ledger_id:
        return None
    hit = None
    for r in rows:
        if r.get("kind") in (KIND_GATE1, KIND_ASK, KIND_REUSED) and (r.get("msg") or {}).get("ledger_id") == ledger_id:
            hit = r
    if hit is None:
        return None
    msg = hit.get("msg") or {}
    out = {"kind": hit["kind"], "id": str(msg.get("id") or hit.get("id") or ""), "target": msg.get("target")}
    if hit["kind"] == KIND_GATE1:
        out["sprint_state"] = msg.get("sprint_state")
    if hit["kind"] == KIND_REUSED and msg.get("answered") and (a := answers_by_id(rows).get(out["id"])) is not None:
        out["answer"] = a
    return out
