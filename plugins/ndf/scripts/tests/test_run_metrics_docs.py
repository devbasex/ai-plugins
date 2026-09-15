"""実行の要約を指す文書の文言（#662 の AC24）。

利用者が作業ツリーを消す手順と、測定の説明の 2 か所が、要約が残ることを書いているかを見る。
"""
from __future__ import annotations

import pathlib
import re

SKILLS = pathlib.Path(__file__).resolve().parents[2] / "skills"


def _step(text: str, number: int) -> str:
    """番号付きの手順 1 つ（次の番号の行の手前まで）を取り出す。"""
    m = re.search(rf"^{number}\. .*?(?=^{number + 1}\. )", text, re.MULTILINE | re.DOTALL)
    assert m, f"手順 {number} が見つかりません"
    return m.group(0)


def test_merged_step4_says_summaries_survive_the_review_worktree() -> None:
    step = _step((SKILLS / "merged" / "SKILL.md").read_text(encoding="utf-8"), 4)
    assert "レビュー用を消しても" in step
    assert "実行の要約は残る" in step
    assert "run_metrics.py aggregate" in step


def test_cross_review_skill_says_measure_runs_on_every_save() -> None:
    lines = (SKILLS / "cross-review" / "SKILL.md").read_text(encoding="utf-8").splitlines()
    [line] = [l for l in lines if l.startswith("- [scripts/measure.py]")]
    assert "状態を保存するたびに実行の要約として呼ばれ" in line
