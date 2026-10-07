"""pr_review_findings.py: `pr-review-steps.py` の指摘ファイルの検査と本来の判定（#860）。

外部へアクセスしない。指摘ファイルは書き換えない（決定 9）。
"""

from __future__ import annotations

import json
from pathlib import Path

SEVERITIES = ("critical", "major", "minor", "nit")
BLOCKING = ("critical", "major")
STAGES = ("spec", "quality")
DEFAULT_STAGE = "quality"  # 段を書かない指摘の段


def _stage(c: dict) -> str:
    """指摘の段。書かれていなければ DEFAULT_STAGE。"""
    return c.get("stage", DEFAULT_STAGE)


def check_findings(data) -> list[str]:
    """指摘ファイルの中身の誤り（I3）。空なら読める。"""
    if not isinstance(data, dict):
        return ["最上位がオブジェクトでない"]
    errs: list[str] = []
    if not isinstance(data.get("summary", ""), str):
        errs.append("summary が文字列でない")
    comments = data.get("comments")
    if not isinstance(comments, list):
        return [*errs, "comments が配列でない"]
    for n, c in enumerate(comments):
        if not isinstance(c, dict):
            errs.append(f"comments[{n}]: オブジェクトでない")
            continue
        if c.get("severity") not in SEVERITIES:
            errs.append(f"comments[{n}]: 重要度 {c.get('severity')!r} は {' / '.join(SEVERITIES)} のどれでもない")
        if _stage(c) not in STAGES:
            errs.append(f"comments[{n}]: 段 {c.get('stage')!r} は {' / '.join(STAGES)} のどれでもない")
        if not isinstance(c.get("body"), str) or not c["body"].strip():
            errs.append(f"comments[{n}]: body が空")
    return errs


def decide_event(comments: list[dict]) -> dict:
    """指摘の集合から本来の判定を決める（I4）。副作用を持たない。"""
    by_severity = {s: sum(1 for c in comments if c.get("severity") == s) for s in SEVERITIES}
    spec_unmet = sum(1 for c in comments if _stage(c) == "spec")
    if spec_unmet or any(by_severity[s] for s in BLOCKING):
        intent = "REQUEST_CHANGES"
    elif comments:
        intent = "COMMENT"
    else:
        intent = "APPROVE"
    return {"intent": intent, "by_severity": by_severity, "spec_unmet": spec_unmet}


def load_findings(path: Path) -> tuple[dict | None, list[str]]:
    if not path.is_file():
        return None, [f"指摘ファイルが無い: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"指摘ファイルが JSON として読めない（{e}）"]
    errs = check_findings(data)
    return (None, errs) if errs else (data, [])
