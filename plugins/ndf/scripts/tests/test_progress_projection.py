"""進捗記録の生成（lib/progress_projection.py。#3092 の受け入れ条件 6・決定 14）。"""

from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import progress_model as pm  # noqa: E402
import progress_projection as pj  # noqa: E402
import workflow_def as wd  # noqa: E402


def test_passed_line_follows_finished_marks():
    snap = wd.load_mode_definition("light")
    rows = {
        "要求と受け入れ条件": pm.RowState(pm.FINISHED, at="t1"),
        "作業場所の用意": pm.RowState(pm.DISABLED, reason="不要"),
        "設計": pm.RowState(pm.FAILED),
        "実装": pm.RowState(pm.FINISHED, at="t2"),
    }
    text = pj.render_passed(snap, rows)
    first = text.splitlines()[0]
    assert first.endswith("通した工程: 要求と受け入れ条件 → 実装")  # 無効と失敗の行は入れない
    assert "- [x] 要求と受け入れ条件 — t1" in text
    assert "- [ ] 作業場所の用意（無効: 不要）" in text
    assert "- [ ] 設計" in text and "- [x] 設計" not in text
    assert pj.finished_stage_names(snap, {}) == []
