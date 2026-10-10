"""承認資料の「含む PR」の組み立て（#1274）。`release-steps.py` の approval-facts と notes --approval が使う。

範囲の PR は、直前の正式版のタグから承認したコミットまでにベースブランチへ入った PR（`lib/merged_range.py`）から、
版上げの PR（head が `release/` で始まる）と PR でない番号を外したもの。「含む PR」は範囲の PR と `--prs` の和を
番号の昇順に並べる。notes --approval は承認資料の「含む PR」の欄から番号を読み、範囲を求め直さない。

使う側は `lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import re
from pathlib import Path

import approved_commit as ac
import merged_range
from step_result import EXIT_PRECONDITION, StepError

ROW = "含む PR"
RELEASE_PREFIX = "release/"  # 配布の工程が作る版上げのブランチ（release/v<版>）


def range_prs(root: Path, prev: str, to: str) -> list[int]:
    """`prev..to` でベースブランチへ入った PR の番号（版上げの PR と PR でない番号を外す）。"""
    merges = merged_range.with_heads(root, merged_range.merges_in(root, prev, to))
    return [m.number for m in merges if m.branch and not m.branch.startswith(RELEASE_PREFIX)]


def included_prs(found, extra) -> list[int]:
    """範囲の PR と `--prs` の和（同じ番号は 1 つ）を番号の昇順で。"""
    return sorted({*found, *extra})


def read_included(path) -> list[int]:
    """承認資料の「含む PR」の欄の各行の先頭の `#N`。欄が `—` なら空。
    資料か欄が無い: StepError（3）。`#N` で始まらない行がある: StepError（2）。"""
    try:
        _, rows = ac._cells(path)  # 承認資料の表の読み取りは approved_commit の 1 か所に置く
    except ValueError as e:
        raise StepError(f"{e}（先に approval-facts を走らせる）", EXIT_PRECONDITION)
    if ROW not in rows:
        raise StepError(f"承認資料 {path} に「{ROW}」の欄が無い（approval-facts で作り直す）", EXIT_PRECONDITION)
    cell = rows[ROW][1]
    if cell == "—":
        return []
    out = []
    for line in cell.split("<br>"):
        m = re.match(r"#(\d+)\b", line.strip())
        if not m:
            raise StepError(f"承認資料の「{ROW}」の行を読めない: {line.strip()[:100]}", 2)
        out.append(int(m.group(1)))
    return out
