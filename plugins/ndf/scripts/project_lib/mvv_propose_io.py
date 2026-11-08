"""mvv_propose_io.py: `project-mvv.py propose` の入出力（材料の読み込みと候補の書き出し）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonio
import project_mvv as pm
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError

from project_lib import mvv_candidates as mc


def load_materials(a, root):
    """材料を読み、--current の有無から現行の本文と種別（new / revision）を決める。"""
    mpath = Path(a.materials).resolve()
    try:
        mats = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StepError(f"材料を読めない: {mpath}: {e}", EXIT_UNREADABLE) from None
    current = None
    kind = "new"
    if a.current:
        mvv = pm.load_mvv(root)
        if not mvv.approved:
            raise StepError(f"--current には承認済みのプロジェクト MVV が要る（今は {pm.STATUS_LABEL[mvv.status]}）", EXIT_PRECONDITION)
        current, kind = mvv.body, "revision"
    return mpath, mats, current, kind


def write_candidates(out_dir, mpath, kind, current, mats, data):
    """候補ごとの本文・candidates.json・candidates.md を書き出す。"""
    bodies = {}
    for c in data["candidates"]:
        p = out_dir / f"candidate-{re.sub(r'[^0-9A-Za-z_-]+', '', str(c.get('id') or len(bodies) + 1))}.md"
        p.write_text(mc.candidate_body(c), encoding="utf-8")
        bodies[c.get("id")] = str(p)
    cj = out_dir / "candidates.json"
    jsonio.write_atomic(cj, {"materials": str(mpath), "kind": kind, **data, "bodies": bodies})
    cm = out_dir / "candidates.md"
    cm.write_text(mc.candidates_md(data, mats, kind, current, bodies), encoding="utf-8")
    return bodies, cj, cm
