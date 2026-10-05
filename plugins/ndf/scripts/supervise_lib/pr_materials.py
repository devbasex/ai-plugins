"""pr のステップの `materials`（#1485 の決定 4・決定 7）。PR 本文の材料を集めて節を組む。

- `manual`: プランの `課題` の要求から手動確認の行を集め、「手動確認」の節を作る（印は今の本文から引き継ぐ）
- `collect`: このブランチへマージした実装の PR の「利用者向けの変化」と「移行の手順」を集める。変化のある PR の
  行だけを「利用者向けの変化」へ並べ、すべての PR を「集めた実装の PR」へ並べる
- `migration`: プランの `課題` の要求の写しの非機能の条件の「移行性」の行を読む。同じ要求の「影響」の
  「公開インタフェース」の行に互換なしの印があれば、その行を「移行の手順」の箇条にする（#1752 の I8）
- `design_results`: `design-results.json`。「課題と設計」の設計 PR と、Closes の材料
- `closes`: 必ず Closes の行にする課題（スプリントの課題）"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field

import design_results
import gh_call
import gh_sections
import manual_checks
from release_lib.others import IMPACT, PUBLIC, breaking, migration_items, row_value
from supervise_lib.procedures import requirements_path

CHANGES_HEADING = "## 利用者向けの変化"  # 配布の説明文（release-steps.py notes）の材料になる PR 本文の節
COLLECTED_HEADING = "## 集めた実装の PR"
CLOSES_HEADING = "## 閉じる課題"
NONFUNCTIONAL, MIGRATION_ROW = "非機能の条件", "移行性"


@dataclass
class Materials:
    changes: list[str] | None = None  # 「利用者向けの変化」の箇条（collect のときだけ。None なら今の本文のまま）
    design: list[str] = field(default_factory=list)  # 「課題と設計」へ足す行
    sections: list[str] = field(default_factory=list)  # 署名の前へ足す節
    migration: list[str] | None = None  # 「移行の手順」の箇条（collect か、互換なしの印のある要求の移行性の行）
    migration_notes: list[str] = field(default_factory=list)  # PR_SYSTEM へ渡す要求の移行性の行（印の有無によらない）


def requirement_migration(cwd: str, issues: list[int]) -> tuple[list[str], list[str]]:
    """要求の写しの「移行性」の行を読み、(移行の手順の箇条, PR_SYSTEM へ渡す行) を返す。箇条は「公開インタフェース」の
    行に互換なしの印がある課題の行だけである（印の無い移行性の行は利用者の操作が要らない旨が多く、手順にしない）。"""
    items, notes = [], []
    for n in issues:
        f = Path(cwd) / requirements_path(n)
        text = f.read_text(encoding="utf-8") if f.is_file() else ""
        line = row_value(text, NONFUNCTIONAL, MIGRATION_ROW)
        if not line:
            continue
        mark = breaking(row_value(text, IMPACT, PUBLIC))
        notes.append(f"#{n} の要求の移行性: {line}" + (f"（公開インタフェース: {mark}）" if mark else ""))
        if mark:
            items.append(f"{line}（#{n}の要求の移行性）")
    return items, notes


def collect_changes(cwd: str, branch: str) -> tuple[list[str], list[str], list[str]]:
    """branch へマージした PR を古い順に読み、(利用者向けの変化の箇条, 集めた実装の PR の行, 移行の手順の箇条) を返す。"""
    p = gh_call.gh(["pr", "list", "--base", branch, "--state", "merged", "--limit", "200", "--json", "number,body"], cwd=cwd)
    try:
        prs = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        prs = None
    if not isinstance(prs, list):
        return [], [f"- {branch} へマージした実装の PR を読めなかった"], []
    changes, rows, migration = [], [], []
    for pr in sorted((x for x in prs if isinstance(x, dict) and "number" in x), key=lambda x: x["number"]):
        n, body = pr["number"], pr.get("body")
        if not isinstance(body, str) or not body.strip():
            rows.append(f"- #{n}: 本文を読めなかった")
            continue
        items = gh_sections.section_items(body, CHANGES_HEADING, n)
        changes += items
        migration += migration_items(body, n)
        rows.append(f"- #{n}: {'変化あり' if items else '利用者向けの変化なし'}")
    return changes, rows, migration


def gather_materials(cwd: str, issues: list[int], materials: dict, old_body: str = "") -> Materials:
    """ステップの `materials` から本文の材料を集める。"""
    out = Materials()
    if materials.get("collect"):
        out.changes, rows, out.migration = collect_changes(cwd, materials["collect"])
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
    if materials.get("migration"):
        out.migration, out.migration_notes = requirement_migration(cwd, issues)
    if materials.get("manual", True):
        rows = manual_checks.issue_rows(cwd, issues, requirements_path)
        manual = manual_checks.manual_section(rows, old_body)
        if manual:
            out.sections.append(manual)
    return out
