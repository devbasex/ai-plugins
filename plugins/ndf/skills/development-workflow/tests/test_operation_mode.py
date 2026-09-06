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
from pathlib import Path

from workflow_helpers import LIB, SKILL_DIR, run_lib

SKILL = SKILL_DIR / "SKILL.md"
REFS = SKILL_DIR / "references"
MODES_REF = REFS / "workflow-modes.md"
OPERATION_REF = REFS / "operation-run.md"
APPROVAL_REF = REFS / "approval-request.md"
STAGE_NOTES_REF = REFS / "stage-notes.md"
PROJECTS_COMMON = SKILL_DIR.parents[1] / "scripts" / "lib" / "projects-common.sh"
SPEC = SKILL_DIR.parents[3] / "docs" / "specifications" / "ndf-workflow-unit-and-gates.md"

EXPECTED_MODES = ["light", "operation", "legacy-refactor", "standard"]
JUDGEMENT_HEADING = "### 2. 上から順に条件を判定する"
WORKFLOW_TABLE_HEADING = "## モードごとに起動する Skill"

# `operation` 列の期待値。工程表の 16 行すべてに値が入る（受け入れ条件 B3）。
EXPECTED_OPERATION_COLUMN = {
    "要求と受け入れ条件": "R",
    "作業場所の用意": "C",
    "設計": "-",
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
    "リリース後テスト": "C",
    "振り返り": "C",
}


def lib() -> str:
    return LIB.read_text(encoding="utf-8")


def skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def declared_modes() -> list[str]:
    found = re.search(r"WF_MODES=\$'([^']*)'", lib())
    assert found, f"モードの一覧を読み取れない: {LIB}"
    return found.group(1).split("\\t")


def declared_heights() -> list[tuple[str, int]]:
    found = re.search(r"WF_MODE_HEIGHT=\$'(.*?)'\n", lib(), re.DOTALL)
    assert found, f"高さの表を読み取れない: {LIB}"
    rows = [line.split("\\t") for line in found.group(1).split("\n") if line]
    return [(name, int(value)) for name, value in rows]


def _table_rows(body: str, heading: str) -> tuple[list[str], list[list[str]]]:
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            if rows:
                break
            continue
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    assert header and rows, f"表を読み取れない: {heading}"
    return header, rows


# --- B1: 一覧と判定の基準の表 ------------------------------------------------


def test_the_mode_is_declared() -> None:
    assert "operation" in declared_modes()


def test_the_judgement_table_gained_a_row() -> None:
    """判定の基準の表に 1 行加わる。"""
    _, rows = _table_rows(skill(), JUDGEMENT_HEADING)
    modes = [row[1].strip("`") for row in rows]
    assert "operation" in modes, modes


def test_the_mode_is_judged_first() -> None:
    """アクセス権の設定は条件だけ見れば `standard` に当たる。工程は当たらない。"""
    _, rows = _table_rows(skill(), JUDGEMENT_HEADING)
    assert rows[0][1].strip("`") == "operation", rows[0]
    assert rows[0][0] == "1", rows[0]


def test_the_judgement_order_is_numbered_from_one() -> None:
    _, rows = _table_rows(skill(), JUDGEMENT_HEADING)
    assert [row[0] for row in rows] == ["1", "2", "3", "4"]


def test_the_condition_is_the_state_of_an_external_system() -> None:
    _, rows = _table_rows(skill(), JUDGEMENT_HEADING)
    condition = next(row[2] for row in rows if row[1].strip("`") == "operation")
    assert "外部の系" in condition, condition
    assert "本番コードも文書も変えず" in condition, condition


def test_the_judgement_does_not_name_a_hosting_feature() -> None:
    """判定の根拠に、その機能を持たない対象では成り立たない名前を使わない。"""
    _, rows = _table_rows(skill(), JUDGEMENT_HEADING)
    condition = next(row[2] for row in rows if row[1].strip("`") == "operation")
    for name in ("GitHub", "ruleset", "GitLab", "AWS", "Slack"):
        assert name not in condition, name


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


def test_the_workflow_table_gained_a_column_in_height_order() -> None:
    header, _ = _table_rows(skill(), WORKFLOW_TABLE_HEADING)
    assert [c.strip("`") for c in header[1:]] == EXPECTED_MODES


def test_every_stage_has_a_value_for_the_new_column() -> None:
    """15 行すべてに必須・条件付き・対象外のいずれかが入る。"""
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


def test_the_column_covers_the_whole_workflow_table() -> None:
    """期待値の側が工程表と同じ行を持つ。行が増えたら、この検査が先に落ちる。"""
    _, rows = _table_rows(skill(), WORKFLOW_TABLE_HEADING)
    assert [row[0] for row in rows] == list(EXPECTED_OPERATION_COLUMN)


def test_the_implementation_stage_points_at_the_run_reference() -> None:
    """`operation` の「実装」は実行そのものである。呼ぶ手順は参照が持つ。"""
    header, rows = _table_rows(skill(), WORKFLOW_TABLE_HEADING)
    column = header.index("`operation`")
    cell = next(row[column] for row in rows if row[0] == "実装")
    assert "operation-run.md" in cell, cell


# --- 盤面の値 ----------------------------------------------------------------


def test_the_board_accepts_the_new_mode() -> None:
    body = PROJECTS_COMMON.read_text(encoding="utf-8")
    found = re.search(r"PJ_MODES=\$'([^']*)'", body)
    assert found, f"モードの一覧を読み取れない: {PROJECTS_COMMON}"
    assert found.group(1).split("\\n") == EXPECTED_MODES


# --- B2: 境界事例 ------------------------------------------------------------


def test_the_boundary_cases_include_the_new_mode() -> None:
    body = MODES_REF.read_text(encoding="utf-8")
    rows = [line for line in body.splitlines() if line.startswith("|")]
    hits = [line for line in rows if "`operation`" in line]
    assert len(hits) >= 3, hits


def test_the_boundary_with_standard_is_written() -> None:
    """本番コードを触るなら `operation` の条件を外れる。"""
    body = MODES_REF.read_text(encoding="utf-8")
    assert re.search(r"本番コードを変えるため.{0,20}`operation`", body)


def test_the_boundary_with_light_is_written() -> None:
    body = MODES_REF.read_text(encoding="utf-8")
    assert "外部の系を触らない" in body


def test_the_reference_has_a_section_for_the_mode() -> None:
    body = MODES_REF.read_text(encoding="utf-8")
    assert "### `operation`" in body


# --- B4: 関門 ----------------------------------------------------------------


def test_the_gate_covers_both_release_and_the_run() -> None:
    """関門は 2 つのまま。2 つ目が配布と運用モードの実行の両方を含む。"""
    body = skill()
    assert "本番の系へ届く操作" in body
    assert re.search(r"配布の工程、および\s*`operation`\s*の実装の工程", body)


def test_the_gate_is_decided_by_the_destination() -> None:
    body = skill()
    assert "届く先が本番の系かどうかで決まる" in body


def test_the_gate_count_did_not_change() -> None:
    body = skill()
    assert re.search(r"関門は\s*\*{0,2}2 つ", body)


def test_the_approval_reference_lists_what_to_show_for_the_run() -> None:
    body = APPROVAL_REF.read_text(encoding="utf-8")
    assert "operation" in body
    assert "取り消せない単位" in body


def test_the_specification_follows_the_same_criterion() -> None:
    """Skill の本文と確定仕様が同じ基準を述べる（受け入れ条件 D8）。"""
    body = SPEC.read_text(encoding="utf-8")
    assert "本番の系へ届く操作" in body
    assert "届く先が本番の系かどうかで決まる" in body
    assert "本番の系" in body.split("## 用語")[1].split("## 背景")[0]


# --- B5 / B6 / B7 / B8: 実行の手順 -------------------------------------------


def operation_ref() -> str:
    return OPERATION_REF.read_text(encoding="utf-8")


def test_the_run_reference_exists_and_is_linked() -> None:
    assert OPERATION_REF.is_file()
    assert "references/operation-run.md" in skill()


def test_the_run_is_split_into_units() -> None:
    body = operation_ref()
    assert "単位" in body
    assert re.search(r"##\s*1\.\s*実行の範囲", body)


def test_each_unit_keeps_the_command_output_and_exit_code() -> None:
    """B5. 保全先と書式が定まり、コマンド・出力・終了コードを残す。"""
    body = operation_ref()
    for token in ("コマンド", "出力", "終了コード"):
        assert token in body, token
    assert "終了コード: 0" in body


def test_a_failure_stops_the_run(  ) -> None:
    """B6. 失敗したらその時点で止め、部分適用の範囲を確定させる。"""
    body = operation_ref()
    assert "その時点で止める" in body
    assert "部分適用の範囲" in body
    assert "戻したかどうか" in body


def test_an_irreversible_unit_is_declared_before_the_run() -> None:
    """B7. 取り消せない操作を含むときの扱い。"""
    body = operation_ref()
    assert "取り消せない単位は、実行の前にそのことを示す" in body
    assert "影響が及ぶ範囲" in body


def test_the_record_location_is_searched_not_assumed() -> None:
    """B8. 対象リポジトリを仮定しない。候補は順序ではなく一覧である。"""
    body = operation_ref()
    assert "候補は順序ではなく一覧" in body
    assert "見つからないときは" in body
    assert "issues/" not in body
    assert "docs/" not in body


def test_the_verification_method_is_searched_not_named() -> None:
    body = operation_ref()
    assert "照会の手段を名指ししない" in body
    assert "確かめられない" in body


def test_secrets_are_kept_out_of_the_record() -> None:
    body = operation_ref()
    assert "機微情報" in body
    assert "値を書かず" in body


def test_the_run_reference_stays_within_the_line_limit() -> None:
    """分割の基準は 501 行以上（`markdown-writing`）。"""
    assert len(operation_ref().splitlines()) <= 500


def test_the_stage_notes_list_the_conditional_stages() -> None:
    """条件付きの 4 つは、発動する条件を持つ。"""
    body = STAGE_NOTES_REF.read_text(encoding="utf-8")
    section = body[body.index("## `operation` の工程") :]
    for stage in ("作業場所の用意", "確定仕様化", "リリース後テスト", "振り返り"):
        assert re.search(rf"^\|\s*{re.escape(stage)}\s*\|", section, re.MULTILINE), stage


def test_the_quality_gate_has_a_definition_of_done() -> None:
    dod = Path(SKILL_DIR.parents[0] / "quality-gates" / "references" / "definition-of-done.md")
    body = dod.read_text(encoding="utf-8")
    assert "## `operation`" in body
