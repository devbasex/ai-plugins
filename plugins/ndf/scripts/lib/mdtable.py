"""Markdown の表の組み立ての包み（#1142 の決定 19・種類 4）。tabulate を呼ぶのはこのモジュールだけである。

tabulate が値の文字列化と欠けた値を持ち、この包みがリポジトリの表の書き方との差を吸収する。

- 列の幅を揃える空白を入れない（`| a | b |`。差分を小さく保つ。markdown-writing の表の書き方）
- 区切りの行は `---`、右寄せは `---:`、中央は `:---:`
- セルの `|` は `\\|` に、改行は空白に直す（1 行 1 行の表を壊さない）
- 値がすべて数（`int` / `float`）の列は既定で右寄せ（`align` で列ごとに指定できる）。数の字面は書き換えない

    table_markdown(["名前", "件数"], [["a", 3], ["b", 10]])
    # | 名前 | 件数 |
    # | --- | ---: |
    # | a | 3 |
    # | b | 10 |

使う側は `deps.require("mdtable")` を先に呼ぶ。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from tabulate import DataRow, Line, TableFormat, tabulate

_ALIGNS = {"left", "right", "center"}


def _row(cells: list[str], _widths: list[int], _aligns: list[str]) -> str:
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def _rule(_widths: list[int], aligns: list[str]) -> str:
    marks = {"right": "---:", "decimal": "---:", "center": ":---:"}
    return "| " + " | ".join(marks.get(a, "---") for a in aligns) + " |"


_FORMAT = TableFormat(lineabove=None, linebelowheader=_rule, linebetweenrows=None, linebelow=None,
                      headerrow=_row, datarow=_row, padding=0, with_header_hide=None)


def cell_text(value: Any) -> str:
    """セルの字面（`|` を `\\|` へ、改行を空白へ。None は空）。"""
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\n", " ").replace("|", "\\|")


def table_markdown(headers: Sequence[Any], rows: Iterable[Sequence[Any]],
                   align: Sequence[str | None] | None = None) -> str:
    """Markdown の表を返す（末尾の改行なし）。`align` は列ごとに `left` / `right` / `center` / None（自動）。"""
    headers = [cell_text(h) for h in headers]
    body = []
    for r in rows:
        cells = list(r) + [None] * (len(headers) - len(r))
        body.append([c if isinstance(c, (int, float)) and not isinstance(c, bool) else cell_text(c)
                     for c in cells[:len(headers)]])
    colalign = None
    if align is not None:
        bad = [a for a in align if a is not None and a not in _ALIGNS]
        if bad:
            raise ValueError(f"表の寄せ方は left / right / center のどれか: {bad}")
        colalign = [a or _auto_align(body, k) for k, a in enumerate(list(align) + [None] * len(headers))][:len(headers)]
    else:
        colalign = [_auto_align(body, k) for k in range(len(headers))]
    if not body:  # 行が無いと tabulate は寄せ方を捨てる
        return _row(headers, [], []) + "\n" + _rule([], colalign)
    return tabulate(body, headers=headers, tablefmt=_FORMAT, colalign=colalign,
                    disable_numparse=True, missingval="")


def _auto_align(body: list[list[Any]], k: int) -> str:
    """列の値がすべて数（空を除く）なら右寄せ、ほかは左寄せ。"""
    vals = [r[k] for r in body if k < len(r) and r[k] not in ("", None)]
    return "right" if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals) else "left"
