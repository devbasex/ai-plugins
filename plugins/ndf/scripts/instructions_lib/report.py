"""指摘の扱いの判定と出力（`instructions-check.py` から分けた。#1142 の C7）。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from instructions_lib.model import Criteria, Finding, Target
from instructions_lib.collect import display_path


ACTION_FIX = "直す"
ACTION_FILE = "起票"
ACTION_REPORT = "報告"


# --- 扱いの判定 --------------------------------------------------------------

def action_of(finding: Finding, in_ndf_repo: bool) -> str:
    """扱いは**スコープと、NDF の開発リポジトリかどうか**の 2 つで決まる。"""
    if in_ndf_repo:
        return ACTION_FIX
    if finding.scope in ("project", "user"):
        return ACTION_FIX
    if finding.source is not None and finding.source.ndf:
        return ACTION_FILE
    return ACTION_REPORT


def format_finding(finding: Finding, action: str, criteria: Criteria) -> str:
    if finding.path and finding.line:
        where = f"{finding.path}:{finding.line}: "
    elif finding.path:
        where = f"{finding.path}: "
    else:
        # どちらも持たないものは `<位置>: ` ごと省く。
        where = ""
    line = f"ERROR: [{action}] {where}{finding.message}"
    if action == ACTION_REPORT:
        # **報告だけで終わらせない。** 何を更新すればよいかと、助言の出どころを添える。
        # **持っている値だけを `/` で連ねる** ── 配布元を持たない指摘で区切りだけが残らない。
        parts: list[str] = []
        if finding.source is not None:
            parts.append(f"配布元 {finding.source.name} {finding.source.version}")
            parts.append(finding.source.origin)
            parts.append(f"更新: {finding.source.update}")
        parts.append(f"観点 {finding.criterion_id}")
        origins = criteria.origins_of(finding.criterion_id)
        if origins:
            parts.append("出典 " + "、".join(origins))
        line += "（" + " / ".join(parts) + "）"
    if action == ACTION_FILE and finding.source is not None:
        line += f"（起票 {issue_title(finding)} / 宛先 {finding.source.origin}）"
    return line


def issue_title(finding: Finding) -> str:
    return f"[instructions] {finding.criterion_id} {finding.path or '-'}"


@dataclass
class Measurements:
    """対象ごとの計測と判定の結果。手順の間で持ち回る。"""

    findings: list[Finding] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    breakdowns: dict[str, list[tuple[str, int]]] = field(default_factory=dict)


@dataclass
class ReportInput:
    """報告に必要な対象・計測結果・判定条件。"""

    targets: list[Target]
    measurements: Measurements
    notes: list[str]
    criteria: Criteria
    in_ndf_repo: bool
    with_report: bool


def report(report_input: ReportInput) -> int:
    targets = report_input.targets
    measurements = report_input.measurements
    roots = [t for t in targets if t.is_root_file]
    print(f"指示書 {len(targets)} 本（根 {len(roots)} / 配下 {len(targets) - len(roots)}）")
    show_size = report_input.criteria.enabled("read-size")
    show_count = report_input.criteria.enabled("instruction-count")
    for target in targets:
        key = display_path(target)
        parts = [key]
        if show_size and target.is_root_file:
            parts.append(f"{measurements.sizes[key]:,} バイト")
        if show_count:
            parts.append(f"指示 {measurements.counts[key]}")
        if len(parts) > 1:
            print("  ".join(parts))
        if report_input.with_report and show_size and target.is_root_file:
            for rel, size in measurements.breakdowns[key]:
                print(f"    {rel}  {size:,} バイト")
    for note in report_input.notes:
        print(note)

    failed = 0
    for finding in measurements.findings:
        action = action_of(finding, report_input.in_ndf_repo)
        line = format_finding(finding, action, report_input.criteria)
        if report_input.criteria.is_error(finding.criterion_id):
            print(line, file=sys.stderr)
            failed += 1
        else:
            print(line.replace("ERROR:", "NOTE:", 1))
    return 1 if failed else 0
