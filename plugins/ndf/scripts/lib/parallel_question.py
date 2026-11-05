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
