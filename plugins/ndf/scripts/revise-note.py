#!/usr/bin/env python3
"""revise-note.py: 出し直しの設計の指摘への対応の表を、設計 PR の本文へ写して突き合わせる（#2510）。

設計文書の節「出し直しの指摘への対応」を設計 PR の本文へ写し、出し直しの指摘の入力の項目と突き合わせる。
0 項目がそろった / 10 表が無い・行の無い項目がある・対応か箇所の欄が空の項目がある / 2 PR・設計文書・入力を読めない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md")
import design_pr_merge  # noqa: E402
import revise_note  # noqa: E402
from step_result import emit, result  # noqa: E402

TOOL = "revise-note"


def cmd_check(a) -> tuple[dict, int]:
    try:
        data = revise_note.read_input(a.notes)
        doc = Path(a.doc).read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        return result(TOOL, "stopped", f"指摘の入力か設計文書を読めない: {e}", [], {"pr": a.pr}), revise_note.CHECK_UNREADABLE
    got = revise_note.check_table(design_pr_merge.run_cmd, ".", a.pr, doc, data)
    if got["code"] == revise_note.CHECK_UNREADABLE:
        return result(TOOL, "stopped", got["reason"], [], {"pr": a.pr}), got["code"]
    if got["code"] == revise_note.CHECK_MISSING:
        items = [{"item": i, "result": "missing"} for i in got["missing"]]
        summary = f"出し直しの指摘の項目のうち {len(items)} 件が表に示されていない"
        return result(TOOL, "gate", summary, items, {"pr": a.pr, "missing": len(items)}, next="承認ゲートで止める"), got["code"]
    return result(TOOL, "ok", "出し直しの指摘の項目がすべて表にある", [], {"pr": a.pr, "items": len(revise_note.note_item_ids(data))}), 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--pr", required=True, type=int, help="設計 PR の番号")
    c.add_argument("--doc", required=True, help="設計文書の頭のファイル")
    c.add_argument("--notes", required=True, help="出し直しの指摘の入力（revise-<課題>.json）")
    a = ap.parse_args()
    out, code = cmd_check(a)
    emit(out, code)


if __name__ == "__main__":
    main()
