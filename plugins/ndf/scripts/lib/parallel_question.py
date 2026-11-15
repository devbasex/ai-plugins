"""並列の計画でレーンから届いた問いのまとめ方（#2436 の異常系 5）。`parallel_run` の問いの受け付けが使う。"""

from __future__ import annotations

import json


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


def asked_of(rows: list[dict], lane: str) -> list[dict]:
    """一時レーン `lane` が送った依頼（`gate2`・`ask`）と、その答えの一覧（台帳の順。#3597 の決定 8）。`rows` は台帳の行で、
    最初の `opened` の行より後のものを渡す。起き直した一時レーンは、答えのある問いを出し直さずにこの答えで進む。"""
    answers = {r.get("id"): r.get("msg") or {} for r in rows if r.get("kind") == "answer"}
    out = []
    for r in rows:
        msg = r.get("msg") or {}
        if r.get("kind") not in ("gate2", "ask") or msg.get("lane") != lane:
            continue
        item = {"id": r.get("id"), "kind": r.get("kind"), "source": msg.get("source"), "target": msg.get("target"), "at": r.get("at")}
        if (a := answers.get(r.get("id"))) is not None:
            item["answer"] = {"result": a.get("result"), "choice": list(a.get("choice") or [])}
        out.append(item)
    return out
