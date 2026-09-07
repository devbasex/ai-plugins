"""判定の単位と、`light` が通る工程を固定する（#418 / #420 / #422 / #392）。

**Pull Request を出す以上、その差分は誰かがレビューする。** `light` は本番の振る舞いも
本番コードの構造も変えない変更だが、**変えないことの確認**が要る。

**判定の単位は Pull Request である。** 束ねたときにどのモードを採るかという問いは、
単位を Pull Request にすると生じない。代わりに、Pull Request に何が入るかが決まらないと
判定できないため、**要求と受け入れ条件が判定より前に来る**。
"""
from __future__ import annotations

import re

import pytest

from workflow_helpers import (
    SKILL_DIR,
    base_env,
    init_repo,
    run_lib,
    run_stage_check,
    state_file,
)

SKILL = SKILL_DIR / "SKILL.md"
MODES_REF = SKILL_DIR / "references" / "workflow-modes.md"
COMPLETENESS_REF = SKILL_DIR / "references" / "stage-completeness.md"
WORKFLOW_TABLE_HEADING = "## モードごとに起動する Skill"


def workflow_table() -> tuple[list[str], list[list[str]]]:
    """工程表の見出しと本文の行を返す。読み取れないことは失敗として扱う。"""
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == WORKFLOW_TABLE_HEADING),
        None,
    )
    assert start is not None, f"見出しが見つからない: {WORKFLOW_TABLE_HEADING}"
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
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
    assert header and rows, "工程表を読み取れない"
    return header, rows


def cell(stage: str, mode: str) -> str:
    header, rows = workflow_table()
    column = header.index(f"`{mode}`")
    row = next((r for r in rows if r[0] == stage), None)
    assert row is not None, f"工程が見つからない: {stage}"
    return row[column]


def stages() -> list[str]:
    return [row[0] for row in workflow_table()[1]]


# --- A1 / B3: `light` が通る工程 -------------------------------------------


def test_light_is_reviewed_by_the_converging_loop() -> None:
    """レビューの深さをモードで変える根拠が無い。差分の小ささはラウンド数が吸収する。"""
    assert "cross-review" in cell("実装レビュー", "light")


def test_light_carries_requirements_too() -> None:
    """判定の入力になるため、モードによらず通る。"""
    assert cell("要求と受け入れ条件", "light") not in ("—", "-", "")


# --- B2: 工程の順序 ---------------------------------------------------------


def test_requirements_come_before_preparing_the_workspace() -> None:
    """**Pull Request に何が入るかが決まらないと判定できない。**"""
    order = stages()
    assert order.index("要求と受け入れ条件") < order.index("作業場所の用意")


def test_the_judgement_is_not_a_stage_of_its_own() -> None:
    """盤面の値を増やさない。順序は本文が書く。"""
    assert "モード判定" not in stages()


def test_the_body_places_the_judgement_between_the_two() -> None:
    body = SKILL.read_text(encoding="utf-8")
    assert re.search(r"要求と受け入れ条件.*モード判定.*作業場所の用意", body, re.DOTALL)


# --- B1 / B4 / B5: 判定の単位と、分ける基準 ---------------------------------


def test_the_unit_of_judgement_is_the_pull_request() -> None:
    body = SKILL.read_text(encoding="utf-8")
    assert "判定する単位" in body
    assert re.search(r"判定する単位は\*{0,2}\s*Pull Request", body)


def test_splitting_is_decided_by_the_files_that_are_touched() -> None:
    """**束ねる理由は競合の回避であって、モードを混ぜたいからではない。**"""
    body = SKILL.read_text(encoding="utf-8")
    assert "触るファイルが重ならない" in body


def test_one_pull_request_carries_one_mode() -> None:
    body = SKILL.read_text(encoding="utf-8")
    assert re.search(r"1 つの Pull Request .{0,20}モードは 1 つ", body, re.DOTALL)


# --- B6: 控えの契約 ---------------------------------------------------------


def test_the_note_keeps_its_key_on_the_issue() -> None:
    """判定の時点で Pull Request の番号は無い。鍵ではなく値の一致で表す。"""
    body = COMPLETENESS_REF.read_text(encoding="utf-8")
    assert "課題番号" in body
    assert "食い違" in body


# --- A4 / A5: 図と参照 ------------------------------------------------------


def flow_diagram() -> str:
    body = SKILL.read_text(encoding="utf-8")
    found = re.search(r"## 標準フロー.*?```mermaid\n(.*?)```", body, re.DOTALL)
    assert found, "標準フローの図を読み取れない"
    return found.group(1)


def test_the_flow_routes_light_through_a_review() -> None:
    """`light` の経路がレビューのノードへ入る。"""
    diagram = flow_diagram()
    review_ids = {
        found.group(1)
        for found in re.finditer(r"(\w+)\[[^\]]*レビュー[^\]]*\]", diagram)
    }
    assert review_ids, "レビューのノードが図に無い"
    light_targets = {
        found.group(1)
        for found in re.finditer(r"-\.->\|[^|]*light[^|]*\|\s*(\w+)", diagram)
    }
    assert light_targets & review_ids, (
        f"`light` の破線がレビューへ入っていない: {sorted(light_targets)}"
    )


def test_the_reference_does_not_justify_by_the_absence_of_a_review() -> None:
    """`light` もレビューを通るようになるため、その根拠は使えない。"""
    body = MODES_REF.read_text(encoding="utf-8")
    assert "レビューの工程を通らないため" not in body


def test_the_reference_says_light_is_reviewed() -> None:
    body = MODES_REF.read_text(encoding="utf-8")
    section = body[body.index("### `light`") : body.index("### `standard`")]
    assert "cross-review" in section


# --- #392: 重複した段落 -----------------------------------------------------


def openings(body: str) -> list[str]:
    """段落の書き出し。**ほぼ同一**の段落は、末尾の 1 文だけが違う形で並ぶ。"""
    found: list[str] = []
    for block in body.split("\n\n"):
        flat = " ".join(block.split())
        if len(flat) < 40 or flat.startswith(("|", "-", "```", "#")):
            continue
        found.append(flat[:40])
    return found


def test_no_paragraph_is_repeated() -> None:
    found = openings(SKILL.read_text(encoding="utf-8"))
    duplicates = sorted({p for p in found if found.count(p) > 1})
    assert duplicates == [], f"ほぼ同一の段落が 2 つ並んでいる: {duplicates}"


# --- A6: 報告が `light` のレビューを必須として扱う ---------------------------


@pytest.fixture()
def repo(tmp_path):
    return init_repo(tmp_path / "repo")


def test_the_report_requires_a_review_for_light(repo, tmp_path) -> None:
    state_dir = tmp_path / "state"
    env = base_env(state_dir)
    run_stage_check("record", "31", "mode", "light", cwd=repo, env=env)
    run_stage_check("record", "31", "stage", "配布", cwd=repo, env=env)

    result = run_stage_check("report", "31", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    missing = next(
        (line for line in result.stdout.splitlines() if "記録なし:" in line), ""
    )
    assert "実装レビュー" in missing, result.stdout
    assert "要求と受け入れ条件" in missing, result.stdout
    assert state_file(state_dir, 31).is_file()


def test_repo_slug_reads_an_ssh_origin(tmp_path) -> None:
    repo = init_repo(tmp_path / "ssh", remote="git@github.com:devbasex/ai-plugins.git")

    result = run_lib(f"wf_repo_slug {repo}")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "devbasex/ai-plugins"


def test_repo_slug_rejects_a_repository_without_origin(tmp_path) -> None:
    repo = init_repo(tmp_path / "bare", remote=None)

    result = run_lib(f"wf_repo_slug {repo}")

    assert result.returncode == 1
    assert result.stdout.strip() == ""
