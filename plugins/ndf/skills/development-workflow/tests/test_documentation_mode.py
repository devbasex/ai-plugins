"""`documentation` モードの値と工程を固定する（#507）。

**足すのは 5 番目のモードと 2 つの工程である。** 既存の 4 モードの判定と、16 個の工程の
並びは変わらない。この検査は新しい値だけを見て、既存の値が動いていないことも同時に見る。

工程表と分類表の突き合わせは `test_workflow_stage_matrix.py` が、工程名の並びの 4 箇所の
一致は `test_stage_values.py` が持つ。ここでは重ねない。
"""
from __future__ import annotations

import re
import shlex

import pytest

from workflow_helpers import LIB, run_lib

MODE = "documentation"
NEW_STAGES = ("素材の収集と出典の確定", "体裁レビュー")


def test_the_mode_is_known() -> None:
    assert run_lib(f"wf_is_mode {MODE}").returncode == 0


def test_the_existing_modes_are_unchanged() -> None:
    """既存の 4 つを外さない。並びも変えない。"""
    body = LIB.read_text(encoding="utf-8")
    found = re.search(r"WF_MODES=\$'([^']*)'", body)
    assert found, "モードの一覧を読み取れない"
    assert found.group(1).split("\\t") == [
        "light", "operation", "legacy-refactor", "standard", MODE,
    ]


def test_the_height_is_five() -> None:
    result = run_lib(f"wf_mode_height {MODE}")
    assert result.returncode == 0
    assert result.stdout.strip() == "5"


@pytest.mark.parametrize("mode", ["unknown-mode", ""])
def test_an_unknown_or_empty_mode_has_zero_height_and_fails(mode: str) -> None:
    """現状固定: 高さが未定義なら 0 を出力し、終了コード 1 を返す。"""
    result = run_lib(f"wf_mode_height {shlex.quote(mode)}")
    assert result.stdout.strip() == "0"
    assert result.returncode == 1


def test_the_height_is_higher_than_standard() -> None:
    """モードが混ざったときに `documentation` の側で検査する。"""
    result = run_lib(f"wf_higher_mode standard {MODE}")
    assert result.stdout.strip() == MODE


def test_the_reason_for_the_height_is_written() -> None:
    """高さの根拠を残す。必須の工程の数ではないことを明記する。"""
    body = LIB.read_text(encoding="utf-8")
    found = re.search(r"WF_MODE_HEIGHT=\$'", body)
    assert found
    head = body[: found.start()]
    assert MODE in head, "高さの理由が `WF_MODE_HEIGHT` の手前に書かれていない"


def test_the_new_stages_are_known() -> None:
    for stage in NEW_STAGES:
        assert run_lib(f"wf_is_stage {stage!r}").returncode == 0, stage


def test_the_new_stages_sit_where_the_design_says() -> None:
    """位置を固定する。盤面の単一選択は並びが工程の順序を表す。"""
    result = run_lib("wf_stages")
    assert result.returncode == 0
    stages = [line for line in result.stdout.splitlines() if line]
    assert stages[stages.index("設計") + 1] == "素材の収集と出典の確定"
    assert stages[stages.index("配布") + 1] == "体裁レビュー"
    assert len(stages) == 18


def test_the_new_stages_are_required_only_for_documentation() -> None:
    for stage in NEW_STAGES:
        for mode in ("light", "operation", "legacy-refactor", "standard"):
            result = run_lib(f"wf_stage_class {mode} {stage!r}")
            assert result.stdout.strip() == "-", f"{mode} / {stage}"
        result = run_lib(f"wf_stage_class {MODE} {stage!r}")
        assert result.stdout.strip() == "R", stage


def test_the_fifth_column_is_read() -> None:
    """`wf_stage_class` の `case` が 5 列目を読む。"""
    assert run_lib(f"wf_stage_class {MODE} 構造改善").stdout.strip() == "-"
    assert run_lib(f"wf_stage_class {MODE} 実装").stdout.strip() == "R"
    assert run_lib(f"wf_stage_class {MODE} 計画").stdout.strip() == "C"


def test_the_existing_columns_are_unchanged() -> None:
    """5 列目を足しても既存の列がずれない。"""
    assert run_lib("wf_stage_class standard 構造改善").stdout.strip() == "R"
    assert run_lib("wf_stage_class light 構造改善").stdout.strip() == "-"
    assert run_lib("wf_stage_class operation 計画").stdout.strip() == "R"
    assert run_lib("wf_stage_class legacy-refactor 要求と受け入れ条件").stdout.strip() == "-"
