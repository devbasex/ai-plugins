"""pr のステップの `materials`（#1485 の決定 4・決定 7）。PR 本文の材料を集めて節を組む。

- `manual`: プランの `課題` の要求から手動確認の行を集め、「手動確認」の節を作る（印は今の本文から引き継ぐ）
- `collect`: このブランチへマージした実装の PR の「利用者向けの変化」を集める。変化のある PR の行だけを
  「利用者向けの変化」へ並べ、すべての PR を「集めた実装の PR」へ並べる
- `design_results`: `design-results.json`。「課題と設計」の設計 PR と、Closes の材料
- `closes`: 必ず Closes の行にする課題（スプリントの課題）"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import design_results
import gh_call
import gh_sections
import manual_checks
from supervise_lib.procedures import requirements_path

CHANGES_HEADING = "## 利用者向けの変化"  # 配布の説明文（release-steps.py notes）の材料になる PR 本文の節
COLLECTED_HEADING = "## 集めた実装の PR"
CLOSES_HEADING = "## 閉じる課題"


@dataclass
class Materials:
    changes: list[str] | None = None  # 「利用者向けの変化」の箇条（collect のときだけ。None なら今の本文のまま）
    design: list[str] = field(default_factory=list)  # 「課題と設計」へ足す行
    sections: list[str] = field(default_factory=list)  # 署名の前へ足す節


def collect_changes(cwd: str, branch: str) -> tuple[list[str], list[str]]:
    """branch へマージした PR を古い順に読み、(利用者向けの変化の箇条, 集めた実装の PR の行) を返す。"""
    p = gh_call.gh(["pr", "list", "--base", branch, "--state", "merged", "--limit", "200", "--json", "number,body"], cwd=cwd)
    try:
        prs = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        prs = None
    if not isinstance(prs, list):
        return [], [f"- {branch} へマージした実装の PR を読めなかった"]
    changes, rows = [], []
    for pr in sorted((x for x in prs if isinstance(x, dict) and "number" in x), key=lambda x: x["number"]):
        n, body = pr["number"], pr.get("body")
        if not isinstance(body, str) or not body.strip():
            rows.append(f"- #{n}: 本文を読めなかった")
            continue
        items = gh_sections.section_items(body, CHANGES_HEADING, n)
        changes += items
        rows.append(f"- #{n}: {'変化あり' if items else '利用者向けの変化なし'}")
    return changes, rows


def gather_materials(cwd: str, issues: list[int], materials: dict, old_body: str = "") -> Materials:
    """ステップの `materials` から本文の材料を集める。"""
    out = Materials()
    if materials.get("collect"):
        out.changes, rows = collect_changes(cwd, materials["collect"])
        if rows:
            out.sections.append(COLLECTED_HEADING + "\n\n" + "\n".join(rows))
    if "design_results" in materials:
        res = design_results.load_results(materials["design_results"]) or design_results.DesignResults()
        prs = " ".join(f"#{n}" for n in res.design_prs)
        out.design.append(f"- 設計: {prs}" if prs else "- 設計: 設計なし")
        if res.unread:
            out.design.append("- 設計の結果を読めなかった設計の課題: " + " ".join(f"#{n}" for n in res.unread))
        closes = sorted({*map(int, materials.get("closes") or []), *res.closes()})
        if closes:
            out.sections.append(CLOSES_HEADING + "\n\n" + "\n".join(f"Closes #{n}" for n in closes))
    if materials.get("manual", True):
        rows = manual_checks.issue_rows(cwd, issues, requirements_path)
        manual = manual_checks.manual_section(rows, old_body)
        if manual:
            out.sections.append(manual)
    return out
