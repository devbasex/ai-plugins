"""用語集の文書の組み立てと、意味の説明に残った廃止した語の検出（純粋な関数。N8a・#1616・fix:3190）。

用語集の正本（JSON）から、コンテキストごとのファイル（`<document_dir>/<コンテキストの id>.md`）と、各ファイルへの
索引（`document`）を作る。1 つの文書に全コンテキストを並べると上限の行数を超え続けるため、コンテキストで分ける。
1 つのコンテキストが上限を超えたら書かずに止める（さらに分けるかは人が決める）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# 表の空のセル
EMPTY_CELL = "—"
# 1 文字の前後が漢字か（単独で照合する廃止した語は、前後が漢字の出現に当てない）
KANJI = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf々]")
# 行内のコード（意味の説明の検査はコードの中を見ない）
INLINE_CODE = re.compile(r"`[^`]*`")
# コンテキストのファイルの表の列
COLUMNS = ("語", "識別子", "意味", "廃止した語", "廃止した識別子", "正本")


class RenderError(ValueError):
    """上限の行数を超えるコンテキストがある。`over` はコンテキストの id → 行数。"""

    def __init__(self, over: Mapping[str, int], limit: int) -> None:
        self.over = dict(over)
        self.limit = limit
        listed = "、".join(f"{cid}（{n} 行）" for cid, n in sorted(self.over.items()))
        super().__init__(f"上限 {limit} 行を超えるコンテキスト: {listed}")


@dataclass(frozen=True)
class Layout:
    """文書の置き場。`document` は索引のパス、`document_dir` はコンテキストごとのファイルの置き場（どちらも根からの相対）。"""

    source: str
    document: str
    document_dir: str
    max_lines: int


def _cell(value: Any) -> str:
    if value is None or value == [] or value == "":
        return EMPTY_CELL
    text = "、".join(str(v) for v in value) if isinstance(value, list) else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _code(value: Any) -> str:
    if not value:
        return EMPTY_CELL
    items = value if isinstance(value, list) else [value]
    return "、".join(f"`{v}`" for v in items)


def _deprecated(term: Mapping[str, Any]) -> list[str]:
    plain = list(term["deprecated"]) if "deprecated" in term else []
    alone = [f"{w}（単独）" for w in term["deprecated_standalone"]] if "deprecated_standalone" in term else []
    return plain + alone


def _row(term: Mapping[str, Any]) -> str:
    source = term["source"] if "source" in term else None
    cells = [
        _cell(term["term"]),
        _code(term["code"] if "code" in term else None),
        _cell(term["meaning"]),
        _cell(_deprecated(term)),
        _code(term["deprecated_code"] if "deprecated_code" in term else None),
        f"`{source}`" if source else EMPTY_CELL,
    ]
    return "| " + " | ".join(cells) + " |"


def context_path(layout: Layout, cid: str) -> str:
    """コンテキストのファイルのパス（根からの相対）。"""
    return f"{layout.document_dir.rstrip('/')}/{cid}.md"


def _context_text(layout: Layout, ctx: Mapping[str, Any], terms: list[Mapping[str, Any]]) -> str:
    lines = [f"# 用語集: {ctx['name']}（`{ctx['id']}`）", ""]
    lines += [f"この文書は `{layout.source}` から作る。手で直さない。索引は `{layout.document}` にある。", ""]
    if "meaning" in ctx and ctx["meaning"]:
        lines += [str(ctx["meaning"]), ""]
    lines += ["| " + " | ".join(COLUMNS) + " |", "| " + " | ".join("---" for _ in COLUMNS) + " |"]
    lines += [_row(t) for t in terms]
    return "\n".join(lines) + "\n"


def _index_text(layout: Layout, rows: list[tuple[Mapping[str, Any], int]]) -> str:
    lines = ["# 用語集", "", f"この文書は `{layout.source}` から作る。手で直さない。コンテキストごとのファイルへの索引である。", ""]
    lines += ["| コンテキスト | 意味 | 語の数 | ファイル |", "| --- | --- | ---: | --- |"]
    for ctx, count in rows:
        path = context_path(layout, ctx["id"])
        meaning = _cell(ctx["meaning"] if "meaning" in ctx else None)
        lines.append(f"| {_cell(ctx['name'])}（`{ctx['id']}`） | {meaning} | {count} | `{path}` |")
    return "\n".join(lines) + "\n"


def render_files(g: Mapping[str, Any], layout: Layout) -> dict[str, str]:
    """用語集からコンテキストごとのファイルと索引の本文を作り、パス → 本文で返す。

    どのファイルも上限の行数以下。超えるコンテキストがあれば `RenderError`（ファイルを 1 つも返さない）。
    語の無いコンテキストもファイルを持つ（索引からたどれる先を欠かさない）。
    """
    by_ctx: dict[str, list[Mapping[str, Any]]] = {c["id"]: [] for c in g["contexts"]}
    for t in g["terms"]:
        by_ctx[t["context"]].append(t)
    out: dict[str, str] = {}
    over: dict[str, int] = {}
    for ctx in g["contexts"]:
        text = _context_text(layout, ctx, by_ctx[ctx["id"]])
        lines = text.count("\n")
        if lines > layout.max_lines:
            over[ctx["id"]] = lines
        out[context_path(layout, ctx["id"])] = text
    if over:
        raise RenderError(over, layout.max_lines)
    out[layout.document] = _index_text(layout, [(c, len(by_ctx[c["id"]])) for c in g["contexts"]])
    return out


def stale(rendered: Mapping[str, str], current: Mapping[str, str | None]) -> list[str]:
    """作った本文と今のファイルの中身（無ければ None）が違うパス。"""
    return sorted(p for p, text in rendered.items() if current.get(p) != text)


def _live(g: Mapping[str, Any]) -> list[str]:
    return sorted({t["term"] for t in g["terms"]}, key=len, reverse=True)


def _masked(text: str, live: Iterable[str]) -> str:
    out = INLINE_CODE.sub(lambda m: " " * len(m.group(0)), text)
    for word in live:
        out = out.replace(word, " " * len(word))
    return out


def _alone_at(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (KANJI.match(before) or KANJI.match(after))


def _hits(text: str, word: str, standalone: bool) -> bool:
    for m in re.finditer(re.escape(word), text):
        if not standalone or _alone_at(text, m.start(), m.end()):
            return True
    return False


def deprecated_in_meanings(g: Mapping[str, Any]) -> list[dict[str, str]]:
    """意味の説明に残った廃止した語（単独で照合する語は前後が漢字の出現を除く）。fix:3190。

    今の語（ほかの語の `term`）の中に現れる廃止した語は数えない。行内のコードの中も見ない。
    """
    live = _live(g)
    words = [(w, False) for t in g["terms"] for w in (t["deprecated"] if "deprecated" in t else [])]
    words += [(w, True) for t in g["terms"] for w in (t["deprecated_standalone"] if "deprecated_standalone" in t else [])]
    found: list[dict[str, str]] = []
    for t in g["terms"]:
        text = _masked(str(t["meaning"]), live)
        found += [{"context": t["context"], "term": t["term"], "word": w} for w, alone in words if _hits(text, w, alone)]
    return found
