"""unified diff の解釈の包み（#1142 の決定 19・種類 11）。

unidiff を呼ぶのはこのモジュールだけである。

- `diff_added_lines(diff)`: `git diff` の出力から、ファイルごとに足した行の番号（新しい側・1 始まり）を返す。
  消したファイル（`+++ /dev/null`）は含めない。パスは `b/` を外した字面

使う側は `deps.require("textparse")` を先に呼ぶ。
"""

from __future__ import annotations

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError


class DiffParseError(ValueError):
    """unified diff として読めない。"""


def diff_added_lines(diff: str) -> dict[str, set[int]]:
    """unified diff の足した行の番号を、ファイルごとに返す（足した行の無いファイルは含めない）。"""
    try:
        patch = PatchSet(diff.splitlines(keepends=True))
    except UnidiffParseError as exc:
        raise DiffParseError(f"unified diff として読めない: {exc}") from None
    out: dict[str, set[int]] = {}
    for f in patch:
        if f.is_removed_file:
            continue
        path = f.target_file[2:] if f.target_file.startswith("b/") else f.target_file
        added = {ln.target_line_no for hunk in f for ln in hunk if ln.is_added and ln.target_line_no}
        if added:
            out.setdefault(path, set()).update(added)
    return out
