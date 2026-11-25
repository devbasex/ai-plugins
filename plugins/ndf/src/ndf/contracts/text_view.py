"""結果を人が読む文にする（#4003）。

標準出力が端末のとき、`result.emit` は 1 行の結果 JSON の代わりにこの文を出す。形は全サブコマンドで共通で、
1 行目に `summary`、`items` の表、`metrics`、`warnings`、`stopped` の `cause`（種類と説明）、`next` と `routes` のコマンドを並べる。
表の列は、サブコマンドが `Column` の並びで決めればその順と見出しを使い、無ければ items のキーを出てきた順に使う。
列の幅は表示の幅（全角は 2）でそろえる。入れ子の値は 1 つのセルに収まる短い文字列にする。
結果は `emit` が形を確かめた後に渡るので、必須の項目（summary・items・metrics と cause の kind・detail）は在るものとして読む。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from typing import Any, Union

# 表の列: 見出しと、行（items の辞書）のキーか、行から値を引く呼び出し
Column = tuple[str, Union[str, Callable[[dict[str, Any]], Any]]]

# 値の無いセル
EMPTY_CELL = "-"
# 1 つのセルの表示の幅の上限（超えた分は省略記号で切る）
CELL_WIDTH = 48
ELLIPSIS = "…"
# 列の間の空白
COLUMN_GAP = "  "
# 表示の幅が 2 になる East Asian Width
_WIDE = ("W", "F")


def display_width(text: str) -> int:
    """表示の幅（全角は 2、結合文字は 0）。"""
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in _WIDE else 1 for ch in text)


def _clip(text: str, limit: int = CELL_WIDTH) -> str:
    if display_width(text) <= limit:
        return text
    out, used = "", display_width(ELLIPSIS)
    for ch in text:
        if used + display_width(ch) > limit:
            break
        out, used = out + ch, used + display_width(ch)
    return out + ELLIPSIS


def cell_text(value: Any) -> str:
    """1 つのセルの文字列（無い値は `-`、真偽は はい/いいえ、入れ子は短い文字列。改行は空白にする）。"""
    return _clip(" ".join(_cell_source(value).split()))


def _cell_source(value: Any) -> str:
    if value is None or (isinstance(value, (str, list, tuple, dict)) and not value):
        return EMPTY_CELL
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, dict):
        if "argv" in value:  # コマンド（next・routes の値）
            return command_line(value)
        return ", ".join(f"{k}={_cell_source(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell_source(v) for v in value)
    return str(value)


def command_line(value: dict[str, Any]) -> str:
    """`next`・`routes` の値をそのまま打てる 1 行にする（打つ側が埋めるキーは `<キー>`）。"""
    words = [str(w) for w in value["argv"]]
    asked = [f"<{k}>" for k in value["ask"]] if "ask" in value else []
    return " ".join(words + asked)


def _columns_of(rows: list[dict[str, Any]], given: Sequence[Column] | None) -> list[Column]:
    if given:
        return list(given)
    keys = dict.fromkeys(k for row in rows for k in row)
    return [(k, k) for k in keys]


def _column_value(row: dict[str, Any], how: Union[str, Callable[[dict[str, Any]], Any]]) -> Any:
    return how(row) if callable(how) else row.get(how)


def _padded(text: str, size: int) -> str:
    return text + " " * (size - display_width(text))


def table_lines(rows: list[dict[str, Any]], given: Sequence[Column] | None = None) -> list[str]:
    """辞書の並びを表の行にする（見出しの行と区切りの行を先頭に置く）。"""
    cols = _columns_of(rows, given)
    grid = [[cell_text(_column_value(row, how)) for _, how in cols] for row in rows]
    heads = [head for head, _ in cols]
    sizes = [max([display_width(h)] + [display_width(line[i]) for line in grid]) for i, h in enumerate(heads)]
    out = [COLUMN_GAP.join(_padded(h, s) for h, s in zip(heads, sizes)).rstrip()]
    out.append(COLUMN_GAP.join("-" * s for s in sizes))
    out += [COLUMN_GAP.join(_padded(c, s) for c, s in zip(line, sizes)).rstrip() for line in grid]
    return out


def _item_lines(items: list[Any], given: Sequence[Column] | None) -> list[str]:
    rows = [i for i in items if isinstance(i, dict)]
    lines = table_lines(rows, given) if rows else []
    return lines + [f"- {cell_text(i)}" for i in items if not isinstance(i, dict)]


def _tail_lines(obj: dict[str, Any]) -> list[str]:
    """表の後ろの行（警告・停止理由・次のコマンド・回答ごとのコマンド）。"""
    lines = [f"警告: {cell_text(w)}" for w in obj["warnings"]] if "warnings" in obj else []
    if "cause" in obj:
        lines += ["", f"停止理由: {obj['cause']['kind']}", f"  {obj['cause']['detail']}"]
    if "next" in obj:
        lines.append(f"次: {command_line(obj['next'])}")
    if "routes" in obj:
        lines.append("回答ごとのコマンド:")
        lines += [f"  {key}: {command_line(val) if isinstance(val, dict) else EMPTY_CELL}" for key, val in obj["routes"].items()]
    return lines


def render_text(obj: dict[str, Any], columns: Sequence[Column] | None = None) -> str:
    """形を確かめた結果を人が読む文にする（末尾の改行を含まない）。"""
    lines = [obj["summary"]]
    if obj["items"]:
        lines += ["", *_item_lines(obj["items"], columns)]
    if obj["metrics"]:
        lines += ["", "計数: " + ", ".join(f"{k}={cell_text(v)}" for k, v in obj["metrics"].items())]
    return "\n".join(lines + _tail_lines(obj))
