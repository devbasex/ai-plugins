"""フェーズの報告（report.md）の欄の読み方。`sprint-state.py` と `supervise_lib/commands.py` の `note_row` が使う。

欄を書くのは `supervise_lib/state.py` の `RunState.write_report` の 1 か所である。欄の形（`- 名前: 値`）を変えるときは、ここだけを直す。
"""

from __future__ import annotations

import re


def report_field(report: str, name: str) -> str:
    """`- 名前: 値` の値。欄が無ければ空。"""
    m = re.search(rf"^- {re.escape(name)}: (.*)$", report, re.M)
    return m.group(1).strip() if m else ""


def cost(report: str) -> str:
    """「LLM の使用量」の末尾の費用（`/ $` の後の数字の並び）。無ければ空。"""
    m = re.search(r"/ \$([0-9.]+)\s*$", report_field(report, "LLM の使用量"))
    return m.group(1) if m else ""


def reason_shown(report: str, result: str) -> str:
    """結果が完了でなく、理由が空でも「無し」でもないときだけ理由を返す。ほかは空。"""
    reason = report_field(report, "理由")
    return reason if result != "完了" and reason not in ("", "無し") else ""
