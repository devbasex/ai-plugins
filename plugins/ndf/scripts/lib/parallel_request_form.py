"""並列の計画のリリースの依頼のメッセージの形（`contracts/forms/parallel-request.json`）。`parallel_lane` から分けた純粋な検査。"""

from __future__ import annotations

import re

REQUEST_ID_RE = re.compile(r"^([a-z0-9][a-z0-9_-]{0,31})-r\d{12}$")


def request_errors(msg: object) -> list[str]:
    """リリースの依頼のメッセージの形の誤り（`contracts/forms/parallel-request.json`）。空なら形に合う。"""
    if not isinstance(msg, dict):
        return ["オブジェクトでない"]
    errs = []
    if msg.get("kind") != "request":
        errs.append("kind が request でない")
    m = REQUEST_ID_RE.match(str(msg.get("id") or ""))
    if not m:
        errs.append("id が <レーン名>-r<数字 12 桁> でない")
    elif m.group(1) != msg.get("lane"):
        errs.append("id のレーン名が lane と違う")
    for k in ("prs", "issues"):
        v = msg.get(k)
        if not isinstance(v, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            errs.append(f"{k} が整数の配列でない")
    for k in ("sprint_state", "at"):
        if not isinstance(msg.get(k), str) or not msg.get(k):
            errs.append(f"{k} が空でない文字列でない")
    return errs
