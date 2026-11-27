"""`mvv_gate._judged` の手順 (8)（省くかの決定）の振る舞いの固定。

記録を書けない・`may_skip` の理由・進め方・承認ゲートの区分が重なったときの `skip` と `why` の並びを、`gate` を通して確かめる。
並びは「記録を書けない理由 → may_skip の理由」の後、進め方の理由を先頭へ、区分の理由を末尾へ足す。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ndf.domains.records import mvv, mvv_gate
from ndf.domains.records import mvv_material_decide as mm

from ._mvv_gate_records import HOST
from . import test_mvv_gate
from .test_mvv_gate import DESIGN_2213, DOCS, FOLLOW, NORMAL, Ask, _hub, _inputs

# 判定の場所と PR のコメントのフィクスチャを test_mvv_gate から読み込む（引数の名前と同じ名前で import すると F811 になる）
repo, notes = test_mvv_gate.repo, test_mvv_gate.notes

REDLINE = "レッドラインに当たる: " + " / ".join(DESIGN_2213["verdict"]["boundary"])
NO_REF = "スプリントの状態に承認済み MVV の参照が無い"
UNWRITABLE = "判定を記録（mvv-validation.jsonl）に書けない: " + mm.STOP_REASONS["records_unwritable"]


@pytest.fixture
def unwritable(monkeypatch: pytest.MonkeyPatch) -> None:
    """判定の行の追記だけを OSError にする（記録は読める）。"""
    real = mvv._append_record

    def append(path: Path, row: Any, kind: str) -> dict:
        if kind == "mvv-validation":
            raise OSError(f"/home/someone/{HOST} に書けない")
        return real(path, row, kind)

    monkeypatch.setattr(mvv, "_append_record", append)


def _gate(repo: Path, gate_name: str, verdict: dict[str, Any], **inputs: Any) -> dict[str, Any]:
    extra = {"pr": 5, **inputs} if gate_name == "release" else inputs
    return mvv_gate.gate(repo, gate_name, _inputs(**extra), _hub(5, DOCS), Ask(verdict))


@pytest.mark.parametrize(
    ("gate_name", "verdict", "inputs", "expected"),
    [
        # 進め方の理由は先頭、区分の理由は末尾に付き、may_skip の理由は間に残る
        ("release", DESIGN_2213["verdict"], {"pace": "normal"}, [NORMAL, REDLINE, mvv_gate.RELEASE_KEPT]),
        ("release", FOLLOW, {"pace": "normal"}, [NORMAL, mvv_gate.RELEASE_KEPT]),
        ("release", FOLLOW, {"mvv_ref": None}, [NO_REF, mvv_gate.RELEASE_KEPT]),
        ("design", DESIGN_2213["verdict"], {"pace": "normal"}, [NORMAL, REDLINE]),
        ("design", DESIGN_2213["verdict"], {}, [REDLINE]),
        ("design", FOLLOW, {"pace": "normal", "mvv_ref": None}, [NORMAL, NO_REF]),
    ],
)
def test_the_reasons_not_to_skip_are_ordered_by_pace_checks_and_gate(
    repo: Path, gate_name: str, verdict: dict[str, Any], inputs: dict[str, Any], expected: list[str]
) -> None:
    out = _gate(repo, gate_name, verdict, **inputs)
    assert (out["skip"], out["why"], out["verdict"]) == (False, expected, verdict)
    # 省かない判定は承認の対象を持たない（`_bound` が何も読まない）
    assert "target" not in out


def test_a_pace_that_is_not_given_keeps_the_gate_with_its_value_in_the_reason(repo: Path) -> None:
    inputs = _inputs()
    del inputs["pace"]
    out = mvv_gate.gate(repo, "design", inputs, _hub(5, DOCS), Ask(FOLLOW))
    assert (out["skip"], out["why"]) == (False, ["進め方 None は承認ゲートを MVV 判定で省かない"])
    assert "target" not in out


@pytest.mark.parametrize(
    ("gate_name", "verdict", "inputs", "tail"),
    [
        # 記録を書けない理由は may_skip の理由より前、進め方の理由より後に並ぶ
        ("design", FOLLOW, {}, []),
        ("design", DESIGN_2213["verdict"], {}, [REDLINE]),
        ("release", FOLLOW, {}, [mvv_gate.RELEASE_KEPT]),
    ],
)
def test_a_verdict_that_cannot_be_recorded_is_listed_before_the_other_reasons(
    repo: Path, unwritable: None, gate_name: str, verdict: dict[str, Any], inputs: dict[str, Any], tail: list[str]
) -> None:
    out = _gate(repo, gate_name, verdict, **inputs)
    assert (out["skip"], out["verdict"], len(out["why"])) == (False, verdict, 1 + len(tail))
    assert out["why"][0].startswith(UNWRITABLE) and out["why"][1:] == tail
    assert "target" not in out


def test_normal_pace_puts_its_reason_before_the_unrecorded_one(repo: Path, unwritable: None) -> None:
    out = _gate(repo, "design", FOLLOW, pace="normal")
    assert (out["skip"], out["why"][0], len(out["why"])) == (False, NORMAL, 2)
    assert out["why"][1].startswith(UNWRITABLE)


def test_only_a_fast_design_follow_without_reasons_skips_and_is_bound(repo: Path) -> None:
    out = _gate(repo, "design", FOLLOW)
    assert (out["skip"], out["why"]) == (True, []) and "target" in out
