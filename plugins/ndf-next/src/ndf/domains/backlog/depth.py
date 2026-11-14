"""深さ（見直し `review`・大整理 `overhaul`）の 1 回の秒の上限と、打ち切りの記録（#2423）。

秒の上限は `.ndf/backlog.json` の `depths.<深さ>.seconds` を先に読み、無ければ上限の宣言の既定
（`backlog-refinement.depth-<深さ>`）を使う。費用（ドル）の宣言は持たない（読み手が無い）。宣言の形が違えば
既定へ倒さずに止まる。

工程の本体を秒の上限で打ち切ったら、残りの番号を深さの記録（depths.jsonl）へ `timed_out` の行で書く。次の
候補の収集は、最後の行が `timed_out` ならその残りを前回の持ち越しとして拾う。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ndf.adapters.git import Git, GitError
from ndf.contracts import facts, limits
from ndf.domains.backlog import state
from ndf.domains.backlog.model import OVERHAUL, RAN, REVIEW, TIMED_OUT, is_positive_int

DEPTHS = (REVIEW, OVERHAUL)
EVENTS = (TIMED_OUT, RAN)


class DepthError(Exception):
    """宣言の形が違う・宣言を読めない。"""


def declaration_path(cwd: Path) -> Path:
    """今の worktree の `.ndf/backlog.json`。"""
    try:
        top = Git(cwd).run("rev-parse", "--show-toplevel").strip()
    except (GitError, OSError) as exc:
        raise DepthError(f"リポジトリの根を解けない: {exc}") from exc
    return Path(top) / facts.value("places", "ndf", "path") / facts.value("ndf-files", "backlog", "path")


def declaration(path: Path) -> dict[str, Any]:
    """宣言を読む。無ければ空。JSON のオブジェクトでなければ `DepthError`。"""
    if not path.exists():
        return {}
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DepthError(f"{path} を読めない: {exc}") from exc
    if not isinstance(got, dict):
        raise DepthError(f"{path} は JSON のオブジェクトで書く")
    return got


def depth_entry(decl: dict[str, Any], depth: str) -> Any:
    """宣言の `depths.<深さ>`（無ければ空）。深さが違う・`depths` がオブジェクトでなければ `DepthError`。"""
    if depth not in DEPTHS:
        raise DepthError(f"深さは {' / '.join(DEPTHS)} のどれか: {depth!r}")
    depths = decl["depths"] if "depths" in decl else {}
    if not isinstance(depths, dict):
        raise DepthError("depths はオブジェクトで書く")
    return depths[depth] if depth in depths else {}


def depth_seconds(decl: dict[str, Any], depth: str) -> int:
    """深さの 1 回の秒の上限。宣言の `depths.<深さ>.seconds` は正の整数で書く。"""
    entry = depth_entry(decl, depth)
    if not isinstance(entry, dict) or ("seconds" in entry and not is_positive_int(entry["seconds"])):
        raise DepthError(f"depths.{depth}.seconds は正の整数で書く")
    if "seconds" in entry:
        return entry["seconds"]
    return int(limits.seconds(f"backlog-refinement.depth-{depth}"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(sd: Path, depth: str, event: str, left: list[int]) -> dict[str, Any]:
    """深さの記録へ 1 行を足す。`timed_out` は残りの番号を持つ。"""
    if depth not in DEPTHS or event not in EVENTS:
        raise DepthError(f"深さは {' / '.join(DEPTHS)}、事象は {' / '.join(EVENTS)} のどれか")
    row = {"at": _now(), "depth": depth, "event": event, "left": sorted(set(left)) if event == TIMED_OUT else []}
    state.append(sd, "backlog-depths", row)
    return row


def left_over(sd: Path) -> list[int]:
    """最後の行が打ち切り（`timed_out`）なら、その残りの番号。"""
    rows = state.read_lines(sd, "backlog-depths")
    if not rows or rows[-1].get("event") != TIMED_OUT:
        return []
    left = rows[-1].get("left")
    return [n for n in left if isinstance(n, int)] if isinstance(left, list) else []
