#!/usr/bin/env python3
"""stage-skills.py: 工程から、worker へ届ける Skill の名前と SKILL.md の絶対パスを引く（#2376）。

    python3 stage-skills.py lookup <工程>

工程は工程表の行名（`development-workflow` の「モードごとに起動する Skill」の表）。supervisor は `ndf:worker` の
起動指示の「読む Skill」をこの出力で決める。対応表は `lib/stage-skills.json` にある。

出力は統一 JSON レポート。`items` は `{"name", "path"}` の一覧で、対応表に無い工程は空の一覧（`ok`）。
終了コード: 0 = 引けた / 1 = 対応表か SKILL.md を読めない（`items` に読めなかった名前かファイル）/ 2 = 引数の誤り。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import stage_skills  # noqa: E402
import step_result  # noqa: E402

TOOL = "stage-skills"


def lookup(stage: str) -> tuple[dict, int]:
    try:
        table = stage_skills.load_table()
    except stage_skills.SkillsError as e:
        return step_result.result(TOOL, "stopped", e.what, [{"name": None, "path": str(stage_skills.TABLE)}]), step_result.EXIT_VIOLATION
    names = stage_skills.resolve({"stage": stage}, table)
    items = [{"name": n, "path": str(stage_skills.skill_path(n))} for n in names]
    missing = [i for i in items if not Path(i["path"]).is_file()]
    if missing:
        return step_result.result(TOOL, "stopped", f"{stage}: SKILL.md が無い {len(missing)} 件", missing), step_result.EXIT_VIOLATION
    return step_result.result(TOOL, "ok", f"{stage}: {len(items)} 件", items, {"count": len(items)}), step_result.EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lookup", help="工程から Skill の名前とパスを引く").add_argument("stage", help="工程の名前")
    a = ap.parse_args(argv)
    out, code = lookup(a.stage)
    step_result.emit(out, code)


if __name__ == "__main__":
    sys.exit(main())
