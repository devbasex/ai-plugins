"""工程「ドキュメント再構成」の追加と、工程名の改名 2 件を固定する（#391）。

工程名の並びそのものは `test_stage_values.py` と `test_workflow_stage_matrix.py` が 4 か所で
突き合わせる。ここが見るのは、その並びの外にある 3 つである。

| 見るもの | なぜ並びの検査に載らないか |
| --- | --- |
| `WF_PR_EXEMPT_STAGE` が指す工程 | 値は 1 つで、並びを持たない。工程表の行名から外れても並びは一致したままになる |
| `document-restructuring` の配布 | 配布一覧は工程表とは別のファイルが持つ |
| 旧い工程名が実装に残っていないこと | 並びが揃っていても、本文や別の定数へ旧い名前が残りうる |

**旧い名前を新しい名前として読む表は持たない**（設計の決定 3）。移行で 1 度だけ書き換える
方針であるため、読み替えの仕組みが実装へ入っていないことも併せて見る。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from workflow_helpers import LIB, SKILL_DIR

PLUGIN_ROOT = SKILL_DIR.parents[1]
MANIFESTS = PLUGIN_ROOT / "manifests"
PROJECTS_COMMON = PLUGIN_ROOT / "scripts/lib/projects-common.sh"

NEW_STAGE = "ドキュメント再構成"
RENAMED = {"設計レビュー": "ドキュメントレビュー", "レビュー": "実装レビュー"}
RUNTIMES = ("claude", "codex", "kiro", "agy")


def lib_body() -> str:
    return LIB.read_text(encoding="utf-8")


def stage_matrix_rows() -> list[list[str]]:
    found = re.search(r"WF_STAGE_MATRIX=\$'(.*?)'\n", lib_body(), re.DOTALL)
    assert found, f"分類表を読み取れない: {LIB}"
    return [line.split("\\t") for line in found.group(1).split("\n") if line]


def stage_names() -> list[str]:
    return [row[0] for row in stage_matrix_rows()]


def scalar(name: str) -> str:
    found = re.search(rf"^{name}='([^']*)'", lib_body(), re.MULTILINE)
    assert found, f"{name} を読み取れない: {LIB}"
    return found.group(1)


# --- C2: 工程の追加 ---------------------------------------------------------


def test_the_restructuring_stage_sits_between_design_and_document_review() -> None:
    """組み直しはレビューの前に置く。後だと構成と内容の指摘が混ざる。"""
    names = stage_names()
    assert names.index("設計") < names.index(NEW_STAGE) < names.index("ドキュメントレビュー")


@pytest.mark.parametrize(
    "mode_index, expected",
    [(1, "-"), (2, "-"), (3, "C"), (4, "R")],  # light / operation / legacy-refactor / standard
)
def test_the_restructuring_row_holds_the_designed_cells(mode_index: int, expected: str) -> None:
    row = next(r for r in stage_matrix_rows() if r[0] == NEW_STAGE)
    assert row[mode_index] == expected


# --- C3 / C13: 改名 ---------------------------------------------------------


@pytest.mark.parametrize("old", sorted(RENAMED))
def test_the_old_stage_names_are_gone(old: str) -> None:
    assert old not in stage_names()


@pytest.mark.parametrize("new", sorted(RENAMED.values()))
def test_the_new_stage_names_are_present(new: str) -> None:
    assert new in stage_names()


def test_the_pr_gate_exempts_the_implementation_review() -> None:
    """`cross-review` は Pull Request が無いと回せないため、この 1 つだけを外す。"""
    assert scalar("WF_PR_EXEMPT_STAGE") == "実装レビュー"


def test_the_exempt_stage_is_a_row_of_the_workflow_table() -> None:
    """工程表に無い値を外すと、外れる工程が 1 つも無くなる（気づく手段が無い）。"""
    assert scalar("WF_PR_EXEMPT_STAGE") in stage_names()


@pytest.mark.parametrize("name, value", [
    ("WF_DESIGN_PREFIX", "design/"),
    ("WF_APPROVAL_LABEL", "design-approved"),
])
def test_the_design_pull_request_marks_do_not_move(name: str, value: str) -> None:
    """工程名とブランチ名は別の名前の集まりである。印は改名の巻き添えにしない。"""
    assert scalar(name) == value


# --- 決定 3: 読み替えの表を持たない -----------------------------------------


@pytest.mark.parametrize("alias", ["WF_STAGE_ALIASES", "WF_MODE_ALIASES", "PJ_STAGE_ALIASES"])
def test_no_alias_table_exists(alias: str) -> None:
    """旧い名前を新しい名前として読む表は、いつ消すかを決める工程を新たに必要とする。"""
    assert alias not in lib_body()
    assert alias not in PROJECTS_COMMON.read_text(encoding="utf-8")


# --- C1: 配布 ---------------------------------------------------------------


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_the_skill_is_distributed_to_every_runtime(runtime: str) -> None:
    """工程表の行になる Skill は、どのランタイムからも起動できる必要がある（決定 9）。"""
    manifest = MANIFESTS / f"{runtime}-skills.txt"
    names = manifest.read_text(encoding="utf-8").split()
    assert "document-restructuring" in names, manifest


def test_the_skill_directory_exists() -> None:
    assert (SKILL_DIR.parent / "document-restructuring" / "SKILL.md").is_file()


# --- C11: 言語ごとの数え方 --------------------------------------------------


def test_the_skill_does_not_enumerate_the_language_files() -> None:
    """言語を 1 つ足すときに他のファイルを変更しない。一覧を持つと SKILL.md が動く。"""
    body = (SKILL_DIR.parent / "document-restructuring" / "SKILL.md").read_text(encoding="utf-8")
    references = Path(SKILL_DIR.parent / "document-restructuring" / "references")
    assert list(references.glob("lang-*.md")), "言語ごとの参照が 1 本も無い"
    for path in references.glob("lang-*.md"):
        assert path.name not in body, f"SKILL.md が {path.name} を名指ししている"
