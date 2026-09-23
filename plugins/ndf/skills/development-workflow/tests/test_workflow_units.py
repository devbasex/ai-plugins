"""判定の単位と、`light` が通る工程を固定する（#418 / #420 / #422 / #392）。

**Pull Request を出す以上、その差分は誰かがレビューする。** `light` は本番の振る舞いも
本番コードの構造も変えない変更だが、**変えないことの確認**が要る。

**判定の単位は Pull Request である。** 束ねたときにどのモードを採るかという問いは、
単位を Pull Request にすると生じない。代わりに、Pull Request に何が入るかが決まらないと
判定できないため、**要求と受け入れ条件が判定より前に来る**。
"""
from __future__ import annotations

import shlex

import pytest

from workflow_helpers import (
    base_env,
    init_repo,
    run_lib,
    run_stage_check,
    state_file,
)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("light", "1"),
        ("operation", "2"),
        ("legacy-refactor", "3"),
        ("standard", "4"),
        ("documentation", "5"),
    ],
)
def test_mode_height_for_each_known_mode(mode: str, expected: str) -> None:
    """現状固定: 5 モードそれぞれの高さの実測値を固定する。"""
    result = run_lib(f"wf_mode_height {shlex.quote(mode)}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_mode_height_for_unknown_mode() -> None:
    """現状固定: 未知のモードは 0 を出力し、非 0 で終了する。"""
    result = run_lib("wf_mode_height unknown-mode")
    assert result.returncode != 0
    assert result.stdout.strip() == "0"


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("", "standard", "standard"),
        ("standard", "", "standard"),
        ("standard", "standard", "standard"),
        ("unknown-first", "unknown-second", "unknown-first"),
    ],
)
def test_higher_mode_at_empty_and_equal_height_boundaries(
    first: str, second: str, expected: str
) -> None:
    """現状固定: 空なら非空側、同じ高さなら先に渡した側を返す。"""
    result = run_lib(f"wf_higher_mode {shlex.quote(first)} {shlex.quote(second)}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a\\b", r"a\\b"),
        ('a"b', r'a\"b'),
        ("a\nb", r"a\nb"),
        ("a\tb", r"a\tb"),
        ("a\rb", r"a\rb"),
    ],
    ids=["backslash", "double-quote", "newline", "tab", "carriage-return"],
)
def test_json_escape_replaces_each_special_character(value: str, expected: str) -> None:
    """現状固定: 各エスケープ対象文字を含む入力の実測出力を固定する。"""
    result = run_lib(f"wf_json_escape {shlex.quote(value)}")
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_json_escape_replaces_all_five_special_characters_together() -> None:
    """現状固定: 5 種のエスケープ対象文字（\\, ", \\n, \\t, \\r）をすべて含む入力の実測出力を固定する。"""
    value = 'back=\\ quote=" nl=\n tab=\t cr=\r'
    expected = r'back=\\ quote=\" nl=\n tab=\t cr=\r'
    result = run_lib(f"wf_json_escape {shlex.quote(value)}")
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


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


# --- R2-001: 証跡の検査が見る工程の並び（現状固定） -------------------------

# `wf_stages_before_pr` が返す工程の並びを、そのまま正解として記録する。
# 証跡の報告（`_wf_missing_before_pr`）はこの並びを終点に使う。`WF_PR_EXEMPT_STAGE`
# の改名や WF_STAGE_MATRIX の並べ替えは、何を証跡として確かめるかを黙って変える。
# ここで並びを固定し、変わったときに落ちるようにする。
STAGES_BEFORE_PR = [
    "要求と受け入れ条件",
    "作業場所の用意",
    "設計",
    "素材の収集と出典の確定",
    "ドキュメント再構成",
    "ドキュメントレビュー",
    "計画",
    "実装",
    "構造改善",
    "完了判定",
]


def test_stages_before_pr_returns_the_recorded_ordered_list() -> None:
    """現状固定: 正確な並びを記録した値と一致する。"""
    result = run_lib("wf_stages_before_pr")
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line != ""]
    assert lines == STAGES_BEFORE_PR


def test_stages_before_pr_excludes_the_exempt_and_pr_stages() -> None:
    """免除される実装レビューと終点の Pull Request は並びに現れない。"""
    result = run_lib("wf_stages_before_pr")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "実装レビュー" not in lines
    assert "Pull Request" not in lines


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
