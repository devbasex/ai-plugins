"""branch_strategy_conflicts.py: 測ったブランチ戦略の値が、組み合わせの規則と文書（README など）の述べる値に食い違う箇所を問いにする（#3672 の決定 6・I11）。

`branch_strategy_measure.measure_strategy` が測った値と根拠を渡して呼ぶ。
"""

from __future__ import annotations

import re
from pathlib import Path

from project_lib import branch_strategy_write as bw

DOCS = ("README.md", "CONTRIBUTING.md", "AGENTS.md")
DOC_BASE = re.compile(r"--base\s+`?([A-Za-z0-9._/\-]+)`?")


def doc_claims(root: Path) -> list[dict]:
    """README・CONTRIBUTING・AGENTS.md から PR の宛先（`--base <名前>`）を述べる行を拾う。"""
    claims = []
    for name in DOCS:
        p = Path(root) / name
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines, 1):
            for m in DOC_BASE.finditer(line):
                claims.append({"pointer": "/branches/base", "value": m.group(1), "ref": f"{name}:{i}"})
    return claims


def rule_conflicts(values: dict, evidence: dict) -> list[dict]:
    """測った値の組が組み合わせの規則に反するかを、規則に出るキーがすべて測ったキーである規則だけで調べる（例は R10）。"""
    rel = values.get("release", {})
    if "cd" in rel and "tag" in rel and rel.get("cd") is None and (rel.get("tag") is not None or rel.get("github_release")):
        return [
            {
                "pointer": "/release/cd",
                "why": "conflict",
                "candidates": [{"release.cd": "CD のワークフローを足す"}, {"release.tag": None, "release.github_release": False}],
                "evidence": evidence.get("/release/tag", []) + evidence.get("/release/cd", []),
            }
        ]
    return []


def doc_conflicts(root: Path, values: dict, evidence: dict) -> list[dict]:
    """文書（README など）の述べる PR の宛先が、測った値と食い違う箇所を問いにする。"""
    conflicts = []
    for c in doc_claims(root):
        if bw.get_pointer(values, c["pointer"]) != c["value"]:
            conflicts.append(
                {
                    "pointer": c["pointer"],
                    "why": "conflict",
                    "candidates": [bw.get_pointer(values, c["pointer"]), c["value"]],
                    "evidence": evidence.get(c["pointer"], []) + [{"source": "doc", "ref": c["ref"], "value": c["value"]}],
                }
            )
    return conflicts
