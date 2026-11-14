"""`ndf manifests` の副命令（配る Skill の表からランタイムの定義を作る）。

ndf manifests render [--check]   # 作り直す（--check は書かずに照らし、違えば stopped）
"""

from __future__ import annotations

import argparse

from ndf import cli as top
from ndf import identity
from ndf.contracts import causes, result
from ndf.domains.manifests import front_matter, render, table

TOOL = top.tool_of(__name__)


def _render(check: bool) -> int:
    root = identity.ROOT
    try:
        changed = render.stale(root) if check else render.write(root)
    except (table.TableError, render.RenderError, front_matter.FrontMatterError) as exc:
        return result.emit(result.stopped(TOOL, "config_broken", str(exc)))
    items = [{"path": str(p.relative_to(root))} for p in changed]
    if check and changed:
        summary = f"作り直した結果と違うファイルが {len(changed)} 件ある（ndf manifests render で作り直す）"
        return result.emit(result.stopped(TOOL, "check_failed", summary, items=items))
    summary = "ランタイムの定義は配る Skill の表と同じ" if check else f"ランタイムの定義を {len(changed)} 件書いた・消した"
    return result.emit(result.result(TOOL, result.OK, summary, items=items))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="配る Skill の表からランタイムの定義を作る")
    sub = parser.add_subparsers(dest="action", required=True)
    ren = sub.add_parser("render", help="Codex の定義の skills と Skill ごとの agents/openai.yaml を作り直す")
    ren.add_argument("--check", action="store_true", help="書かずに、作り直した結果と違えば stopped")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    return _render(args.check)
