"""pr_review_findings.py: `pr-review-steps.py` の指摘ファイルの検査・本来の判定・payload と報告の組み立て（#860）。

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


def _prefixed(c: dict) -> str:
    sev, cat, body = c["severity"], str(c.get("category") or "").strip(), c["body"].strip()
    if body.startswith((f"[{sev} /", f"[{sev}]")):
        return body
    return f"[{sev} / {cat}] {body}" if cat else f"[{sev}] {body}"


def _finding_place(c: dict) -> str:
    path, line = c.get("path"), c.get("line")
    return f"`{path}:{line}` " if path and line is not None else (f"`{path}` " if path else "")


def _payload_summary(data: dict, spec: list[dict]) -> str:
    """payload の summary。仕様適合の見出しと一覧（あれば）に総評を続ける。"""
    parts = []
    if spec:
        parts.append("### 仕様適合（満たさない）\n\n" + "\n".join(f"- {_finding_place(c)}{_prefixed(c)}" for c in spec))
    if str(data.get("summary") or "").strip():
        parts.append(data["summary"].strip())
    return "\n\n".join(parts)


def _skip_in_payload(c: dict) -> bool:
    """位置を持たない仕様適合の指摘は summary にだけ載せ、comments から落とす。"""
    return _stage(c) == "spec" and not (c.get("path") and c.get("line") is not None)


def _payload_comment(c: dict) -> dict:
    item = {"severity": c["severity"], "body": _prefixed(c)}
    for k in ("path", "line"):
        if c.get(k) is not None:
            item[k] = c[k]
    return item


def build_payload(data: dict) -> dict:
    """指摘ファイルから `review-post` へ渡す payload を組む。指摘ファイルは書き換えない（決定 9）。"""
    comments = data["comments"]
    spec = [c for c in comments if _stage(c) == "spec"]
    return {"summary": _payload_summary(data, spec), "comments": [_payload_comment(c) for c in comments if not _skip_in_payload(c)]}


def branch_report(data: dict, verdict: dict) -> str:
    comments = data["comments"]

    def lines(pick) -> str:
        got = [f"- {_finding_place(c)}{_prefixed(c)}" for c in comments if pick(c)]
        return "\n".join(got) or "- なし"

    by = verdict["by_severity"]
    return (
        "## レビュー結果\n\n"
        "### 概要\n\n- 件数: " + " / ".join(f"{s} {by[s]}" for s in SEVERITIES) + "\n\n"
        "### 第 1 段: 仕様適合（満たさない）\n\n" + lines(lambda c: _stage(c) == "spec") + "\n\n"
        "### 第 2 段: Issues（要修正）\n\n" + lines(lambda c: _stage(c) == "quality" and c["severity"] in BLOCKING) + "\n\n"
        "### 第 2 段: Suggestions（改善提案）\n\n"
        + lines(lambda c: _stage(c) == "quality" and c["severity"] not in BLOCKING)
        + "\n\n"
        + (f"### 総評\n\n{data['summary'].strip()}\n" if str(data.get("summary") or "").strip() else "")
    )


def load_findings(path: Path) -> tuple[dict | None, list[str]]:
    if not path.is_file():
        return None, [f"指摘ファイルが無い: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"指摘ファイルが JSON として読めない（{e}）"]
    errs = check_findings(data)
    return (None, errs) if errs else (data, [])
