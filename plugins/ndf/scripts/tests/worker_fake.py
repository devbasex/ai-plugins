"""偽の worker が作業の結果ファイルを書く（#2337 の決定 7）。テストの中の偽物と、偽の claude のスクリプトが使う。

起動指示の「作業の結果ファイル: <パス>」の行から置き場を取り、偽の報告の「結果:」の語から状態を決めて 1 行を書く。
報告に「結果:」が無ければ書かない（作業の結果ファイルを書かずに応答を終えた worker を演じる）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RESULT_LINE = re.compile(r"作業の結果ファイル: (\S+)")

# 偽の claude のスクリプトへ埋める行（`prompt` と `text` の変数を使う。テストの置き場を読まずに動く）
SNIPPET = r"""
import re as _re
_m = _re.search(r"作業の結果ファイル: (\S+)", prompt)
_st = "ok" if "結果: 完了" in text else "gate" if "判断が要る" in text else "stopped" if "できなかった" in text else None
if _m and _st:
    _o = {"tool": "worker", "status": _st, "summary": "偽の worker", "items": [], "metrics": {}}
    if _st == "gate":
        _o["next"] = "判断が要る"
    if _st == "stopped":
        _o["gaps"] = [{"kind": "work", "name": "fake", "reason": "できなかった"}]
    open(_m.group(1), "w").write(json.dumps(_o, ensure_ascii=False))
"""


def result_path(prompt: str) -> str | None:
    m = RESULT_LINE.search(prompt or "")
    return m.group(1) if m else None


def write_result(prompt: str, text: str, path: str | None = None) -> str | None:
    """起動指示（か渡した path）の置き場へ、報告の語に合う結果を書く。置き場を返す（起こし直しで使い回す）。"""
    path = result_path(prompt) or path
    ns = {"prompt": f"作業の結果ファイル: {path}" if path else "", "text": text or "", "json": json}
    exec(SNIPPET, ns)  # noqa: S102 偽のスクリプトと同じ行を通す
    return path


def ok_result(path: Path) -> None:
    path.write_text(json.dumps({"tool": "worker", "status": "ok", "summary": "済んだ", "items": [], "metrics": {}}))
