"""`mvv_gate._judged` の手順 (8)（省くかの決定）の振る舞いの固定。

記録を書けない・`may_skip` の理由・進め方・承認ゲートの区分が重なったときの `skip` と `why` の並びを、純粋な関数
`_skip_decision` に入力を渡して確かめる。並びは「記録を書けない理由 → may_skip の理由」の後、進め方の理由を先頭へ、
区分の理由を末尾へ足す。実 Git を使う `gate` の通し（省いて承認の対象を持つ・記録を書けないときに省かない）は
`test_mvv_gate.py` の結合テストが確かめる。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from ndf.domains.records import mvv_gate
from ndf.progress.gate.checks import MvvFacts

SHA = "a" * 64
FACTS = MvvFacts(status="approved", current=SHA, approved=SHA, referenced=SHA, mode="standard")
NO_REF_FACTS = replace(FACTS, referenced=None)
FOLLOW = {"verdict": "follow", "reasons": ["Value 1 に沿う"], "boundary": [], "basis": ["Value 1"]}
REDLINE_VERDICT = {**FOLLOW, "boundary": ["C4 の削除"]}

REDLINE = "レッドラインに当たる: C4 の削除"
NO_REF = "スプリントの状態に承認済み MVV の参照が無い"
NORMAL = "進め方 normal は承認ゲートを MVV 判定で省かない"
UNWRITABLE = "判定を記録（mvv-validation.jsonl）に書けない: 書けない"
KEPT = mvv_gate.RELEASE_KEPT


@pytest.mark.parametrize(
    ("facts", "verdict", "gate_name", "pace", "unwritable", "expected"),
    [
        # 進め方の理由は先頭、区分の理由は末尾に付き、may_skip の理由は間に残る
        (FACTS, REDLINE_VERDICT, "release", "normal", False, [NORMAL, REDLINE, KEPT]),
        (FACTS, FOLLOW, "release", "normal", False, [NORMAL, KEPT]),
        (NO_REF_FACTS, FOLLOW, "release", "fast", False, [NO_REF, KEPT]),
        (FACTS, REDLINE_VERDICT, "design", "normal", False, [NORMAL, REDLINE]),
        (FACTS, REDLINE_VERDICT, "design", "fast", False, [REDLINE]),
        (NO_REF_FACTS, FOLLOW, "design", "normal", False, [NORMAL, NO_REF]),
        # 進め方が渡らないときも省かず、その値を理由に載せる
        (FACTS, FOLLOW, "design", None, False, ["進め方 None は承認ゲートを MVV 判定で省かない"]),
        # 記録を書けない理由は may_skip の理由より前、進め方の理由より後に並ぶ
        (FACTS, REDLINE_VERDICT, "design", "fast", True, [UNWRITABLE, REDLINE]),
        (FACTS, FOLLOW, "release", "fast", True, [UNWRITABLE, KEPT]),
        (FACTS, FOLLOW, "design", "normal", True, [NORMAL, UNWRITABLE]),
        # 4 つの理由がすべて重なったときの並び
        (NO_REF_FACTS, REDLINE_VERDICT, "release", "normal", True, [NORMAL, UNWRITABLE, NO_REF, KEPT]),
    ],
)
def test_the_reasons_not_to_skip_are_ordered_by_pace_record_checks_and_gate(
    facts: MvvFacts, verdict: dict[str, Any], gate_name: str, pace: Any, unwritable: bool, expected: list[str]
) -> None:
    why = [UNWRITABLE] if unwritable else []
    assert mvv_gate._skip_decision(facts, verdict, gate_name, pace, recorded=not unwritable, why=why) == (False, expected)
