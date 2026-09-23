"""反証のプロンプトの語を固定する（#732）。"""
from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent
CRITIQUE_SH = HERE / "scripts/critique.sh"


def test_the_critique_prompt_says_insufficient_evidence_does_not_drop_the_finding() -> None:
    """「立証できない」を返しても指摘は数から落ちないことを、プロンプトが担当へ言う（AC19）。"""
    assert "数から落ち" in CRITIQUE_SH.read_text(encoding="utf-8")
