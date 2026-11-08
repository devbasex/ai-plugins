"""mvv_propose_io.py: `project-mvv.py propose` の入出力（材料の読み込み）。"""

from __future__ import annotations

import json
from pathlib import Path

import project_mvv as pm
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError


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
