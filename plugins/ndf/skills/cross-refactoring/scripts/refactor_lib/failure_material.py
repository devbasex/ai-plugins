"""落ちたテストの材料（#2457 決定 12・受け入れ条件 13）。

材料は ID・ログの置き場・走らせ直すコマンドの 3 つで、修正・最終ゲート修正・判定の担い手のプロンプトへ渡る。
ID は JUnit（手元の suite・CI の成果物）から読み、読めなければログの要約の行（`FAILED` / `ERROR` の後のノード ID）から取る。
走らせ直すコマンドは、見分けが返したものがあればそれ、無ければ全体テストのコマンドである。
**純粋な処理だけを置く**（ログの本文は呼ぶ側が読んで渡す）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

_SUMMARY = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def ids_from_log(text: str) -> list[str]:
    """ログの要約の行から落ちたテストの ID を取る（`FAILED tests/x.py::t - 理由` → `tests/x.py::t`）。"""
    out: list[str] = []
    for found in _SUMMARY.findall(text or ""):
        tid = found.rstrip(":")
        if tid not in out:
            out.append(tid)
    return out


def build(
    junit_ids: Optional[Iterable[str]],
    log_text: str,
    log_place: str,
    rerun: Optional[Iterable[str]],
    whole: Iterable[str],
) -> dict[str, Any]:
    """材料を組む。ID が JUnit とログのどちらからも取れなければ `ids` は空で `source` は `none`。"""
    ids = [str(i) for i in junit_ids or []]
    source = "junit" if ids else ""
    if not ids:
        ids = ids_from_log(log_text)
        source = "log" if ids else "none"
    commands = [str(c) for c in rerun or [] if c] or [str(c) for c in whole if c]
    return {"ids": ids, "source": source, "log": log_place, "commands": commands}


def as_markdown(material: dict[str, Any]) -> str:
    """修正担当へ渡す節。"""
    ids = material.get("ids") or []
    shown = ", ".join(f"`{i}`" for i in ids[:20]) + (f" ほか {len(ids) - 20} 件" if len(ids) > 20 else "") if ids else "（取れなかった）"
    commands = " / ".join(f"`{c}`" for c in material.get("commands") or []) or "（無し）"
    return "\n".join(
        [
            f"- 落ちたテスト（{material.get('source')}）: {shown}",
            f"- ログの置き場: {material.get('log') or '（無し）'}",
            f"- 走らせ直すコマンド: {commands}",
        ]
    )
