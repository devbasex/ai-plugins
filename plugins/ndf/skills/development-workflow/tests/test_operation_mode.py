"""運用モード `operation` を固定する（#423）。

**`operation` は「本番コードも文書も変えず、外部の系の状態だけを変える」変更のモード**
である。判定では 1 番に来るが、高さは `light` の 1 つ上に置く。工程の重さが `light` と
`legacy-refactor` の間にあるためで、**判定の順序と高さは別の値である**。

**列の並びは高さと同じ順である**（決定 10）。ただし**高さは `WF_MODE_HEIGHT` が持ち、
列の位置からは計算しない**（v10.5.1 の決定 2-b）。この検査は 2 つが一致していることを
見るだけで、片方をもう片方から導かない。
"""
from __future__ import annotations

import re

from workflow_helpers import LIB, SKILL_DIR, run_lib

PROJECTS_COMMON = SKILL_DIR.parents[1] / "scripts" / "lib" / "projects-common.sh"

EXPECTED_MODES = ["light", "operation", "legacy-refactor", "standard", "documentation"]

# `operation` 列の期待値。工程表の全行に値が入る（受け入れ条件 B3）。
# **行数を書かない。** 工程を足すたびに書き換える記述を残さない。
EXPECTED_OPERATION_COLUMN = {
    "要求と受け入れ条件": "R",
    "作業場所の用意": "C",
    "設計": "C",
    "素材の収集と出典の確定": "-",
    "ドキュメント再構成": "-",
    "ドキュメントレビュー": "-",
    "計画": "R",
    "実装": "R",
    "構造改善": "-",
    "実装レビュー": "R",
    "完了判定": "R",
    "Pull Request": "R",
    "確定仕様化": "C",
    "後片付け": "R",
    "配布": "R",
    "体裁レビュー": "-",
    "リリース後テスト": "C",
    "振り返り": "C",
}


def lib() -> str:
    return LIB.read_text(encoding="utf-8")


def declared_modes() -> list[str]:
    found = re.search(r"WF_MODES=\$'([^']*)'", lib())
    assert found, f"モードの一覧を読み取れない: {LIB}"
    return found.group(1).split("\\t")


def declared_heights() -> list[tuple[str, int]]:
    found = re.search(r"WF_MODE_HEIGHT=\$'(.*?)'\n", lib(), re.DOTALL)
    assert found, f"高さの表を読み取れない: {LIB}"
    rows = [line.split("\\t") for line in found.group(1).split("\n") if line]
    return [(name, int(value)) for name, value in rows]


# --- B1: 一覧と判定の基準の表 ------------------------------------------------


def test_the_mode_is_declared() -> None:
    assert "operation" in declared_modes()


# --- A9 / 決定 10: 並びと高さ ------------------------------------------------


def test_the_modes_are_listed_in_height_order() -> None:
    assert declared_modes() == EXPECTED_MODES


def test_every_mode_has_a_height() -> None:
    assert [name for name, _ in declared_heights()] == EXPECTED_MODES


def test_the_heights_are_distinct_and_ascending() -> None:
    values = [value for _, value in declared_heights()]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_the_operation_mode_sits_between_light_and_legacy_refactor() -> None:
    heights = dict(declared_heights())
    assert heights["light"] < heights["operation"] < heights["legacy-refactor"]


def test_the_height_is_not_derived_from_the_column_position() -> None:
    """高さは `WF_MODE_HEIGHT` が持つ。列の位置から計算しない。"""
    body = lib()
    assert "WF_MODE_HEIGHT=" in body
    assert "列の位置からは導かない" in body


def test_raising_from_light_reaches_operation() -> None:
    result = run_lib("wf_higher_mode light operation")
    assert result.stdout.strip() == "operation", result.stderr


def test_raising_from_operation_reaches_standard() -> None:
    result = run_lib("wf_higher_mode operation standard")
    assert result.stdout.strip() == "standard", result.stderr


def test_raising_from_an_empty_mode_uses_the_other_mode() -> None:
    result = run_lib("wf_higher_mode '' standard")
    assert result.stdout.strip() == "standard", result.stderr


def test_raising_to_an_empty_mode_keeps_the_first_mode() -> None:
    result = run_lib("wf_higher_mode light ''")
    assert result.stdout.strip() == "light", result.stderr


def test_raising_modes_with_the_same_height_keeps_the_first_mode() -> None:
    result = run_lib("wf_higher_mode light light")
    assert result.stdout.strip() == "light", result.stderr


def test_the_mode_is_accepted_by_the_library() -> None:
    result = run_lib("wf_is_mode operation && echo yes")
    assert result.stdout.strip() == "yes", result.stderr


# --- B3: 工程表の列 ----------------------------------------------------------


def test_every_stage_has_a_value_for_the_new_column() -> None:
    """工程表の全行に必須・条件付き・対象外のいずれかが入る。"""
    for stage, expected in EXPECTED_OPERATION_COLUMN.items():
        result = run_lib(f'wf_stage_class operation "{stage}"')
        assert result.returncode == 0, (stage, result.stderr)
        assert result.stdout.strip() == expected, (stage, result.stdout)


def test_stage_class_rejects_an_unknown_mode_without_output() -> None:
    """現状固定: 知らないモードでは失敗し、標準出力へ何も書かない。"""
    result = run_lib('wf_stage_class unknown "実装"')
    assert result.returncode == 1
    assert result.stdout == ""


def test_stage_class_rejects_an_unknown_stage_without_output() -> None:
    """現状固定: 工程表にない工程では失敗し、標準出力へ何も書かない。"""
    result = run_lib('wf_stage_class operation "存在しない工程"')
    assert result.returncode == 1
    assert result.stdout == ""


# --- ボードの値 ----------------------------------------------------------------


def test_the_board_accepts_the_new_mode() -> None:
    body = PROJECTS_COMMON.read_text(encoding="utf-8")
    found = re.search(r"PJ_MODES=\$'([^']*)'", body)
    assert found, f"モードの一覧を読み取れない: {PROJECTS_COMMON}"
    assert found.group(1).split("\\n") == EXPECTED_MODES
