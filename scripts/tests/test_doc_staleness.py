"""説明文書の記載を崩すと検査が失敗することを確かめる。

記号（A〜F）は `issues/issue-178-doc-staleness-checks.md` の受け入れ条件に対応する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from doc_staleness_helpers import REPO_ROOT, output_of, edit, run_check


def root_readme(tree: Path) -> Path:
    return tree / "README.md"


def plugin_readme(tree: Path) -> Path:
    return tree / "plugins/ndf/README.md"


def test_consistent_tree_passes(tree: Path) -> None:
    """突き合わせ元と突き合わせ先が一致していれば終了コード 0 で終わる。"""
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_real_repository_passes() -> None:
    """実物のリポジトリでも通る。検査を入れた時点で落ちる状態を作らない。"""
    result = run_check(REPO_ROOT)
    assert result.returncode == 0, output_of(result)


# --- A: README.md のランタイム別の公開 Skill 数 ---


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("Claude Code向け core 5個", "Claude Code向け core 4個", "5"),
        ("Kiro向け core 4個", "Kiro向け core 3個", "4"),
        ("Codex向け core 3個", "Codex向け core 2個", "3"),
    ],
)
def test_runtime_skill_count_mismatch_fails(tree: Path, before: str, after: str, expected: str) -> None:
    edit(root_readme(tree), before, after)
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "README.md" in out
    assert expected in out


@pytest.mark.parametrize(
    "fragment",
    ["Claude Code向け core 5個、", "Kiro向け core 4個、", "Codex向け core 3個"],
)
def test_runtime_skill_count_removed_fails(tree: Path, fragment: str) -> None:
    """記載を消して検査を通せる状態を作らない。"""
    edit(root_readme(tree), fragment, "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


# --- B: README.md の元 Skill 数 ---


def test_source_skill_count_mismatch_fails(tree: Path) -> None:
    edit(root_readme(tree), "元Skills（7個）", "元Skills（8個）")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "README.md" in out
    assert "8" in out and "7" in out


def test_source_skill_count_removed_fails(tree: Path) -> None:
    edit(root_readme(tree), "- **元Skills（7個）**:\n", "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


# --- C: README.md のカテゴリ内訳 ---


def test_category_total_mismatch_fails(tree: Path) -> None:
    """行ごとの数と名前の数が揃っていても、合計が食い違えば失敗する。"""
    edit(
        root_readme(tree),
        "  - 第2群 (3): echo, xray, yankee\n",
        "  - 第2群 (4): echo, xray, yankee, foxtrot\n",
    )
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "8" in out and "7" in out


def test_category_line_count_mismatch_fails(tree: Path) -> None:
    """1 行の中で、宣言された数と並ぶ Skill 名の数が食い違えば失敗する。"""
    edit(root_readme(tree), "  - 第1群 (4): ", "  - 第1群 (5): ")
    result = run_check(tree)
    assert result.returncode != 0
    assert "第1群" in output_of(result)


def test_category_breakdown_removed_fails(tree: Path) -> None:
    edit(
        root_readme(tree),
        "  - 第1群 (4): alpha, bravo, charlie, delta\n  - 第2群 (3): echo, xray, yankee\n",
        "",
    )
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


# --- D: plugins/ndf/README.md の配布先の表 ---


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("| Claude Code | 5 個 |", "| Claude Code | 4 個 |", "5"),
        ("| Codex | 3 個 |", "| Codex | 2 個 |", "3"),
        ("| Kiro CLI | 4 個 |", "| Kiro CLI | 9 個 |", "4"),
    ],
)
def test_distribution_table_mismatch_fails(tree: Path, before: str, after: str, expected: str) -> None:
    edit(plugin_readme(tree), before, after)
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "plugins/ndf/README.md" in out
    assert expected in out


@pytest.mark.parametrize(
    "row",
    [
        "| Claude Code | 5 個 | `.claude-plugin/plugin.json` |\n",
        "| Codex | 3 個 | `.codex-plugin/plugin.json` |\n",
        "| Kiro CLI | 4 個 | `dev.kiro/install.sh` |\n",
    ],
)
def test_distribution_table_row_removed_fails(tree: Path, row: str) -> None:
    edit(plugin_readme(tree), row, "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "plugins/ndf/README.md" in output_of(result)


# --- E: plugins/ndf/README.md のレイアウト図 ---


def test_layout_skill_count_mismatch_fails(tree: Path) -> None:
    edit(plugin_readme(tree), "唯一の実体（5 個）", "唯一の実体（6 個）")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "plugins/ndf/README.md" in out
    assert "6" in out and "5" in out


def test_layout_optional_count_mismatch_fails(tree: Path) -> None:
    edit(plugin_readme(tree), "載せない Skill（2 個）", "載せない Skill（3 個）")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "plugins/ndf/README.md" in out
    assert "3" in out and "2" in out


def test_layout_counts_removed_fails(tree: Path) -> None:
    edit(plugin_readme(tree), "唯一の実体（5 個）", "唯一の実体")
    result = run_check(tree)
    assert result.returncode != 0
    assert "plugins/ndf/README.md" in output_of(result)


# --- F: 更新案内の見出しの版数 ---


def test_upgrade_heading_version_stale_fails(tree: Path) -> None:
    edit(plugin_readme(tree), "## v9.3.0 へ更新するとき", "## v9.2.1 へ更新するとき")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "plugins/ndf/README.md" in out
    assert "9.2.1" in out and "9.3.0" in out


def test_upgrade_heading_removed_fails(tree: Path) -> None:
    edit(plugin_readme(tree), "## v9.3.0 へ更新するとき\n", "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "plugins/ndf/README.md" in output_of(result)


def test_upgrade_heading_duplicated_fails(tree: Path) -> None:
    """前の版の節を残したままにすると、どちらが現行かを読み手が決められない。"""
    body = plugin_readme(tree).read_text(encoding="utf-8")
    plugin_readme(tree).write_text(body + "\n## v9.2.1 へ更新するとき\n\n前の版の本文。\n", encoding="utf-8")
    result = run_check(tree)
    assert result.returncode != 0
    assert "plugins/ndf/README.md" in output_of(result)


# --- 失敗の出力に含めるもの ---


def test_failure_output_names_file_label_and_both_values(tree: Path) -> None:
    """どのファイルのどの記載が、どの値と食い違ったかが出力に含まれる。"""
    edit(root_readme(tree), "Claude Code向け core 5個", "Claude Code向け core 9個")
    result = run_check(tree)
    out = output_of(result)
    assert "README.md" in out
    assert "Claude Code" in out
    assert "9" in out
    assert "5" in out
    assert "claude-skills.txt" in out


# --- 突き合わせ先そのものが欠けている場合 ---


def test_missing_manifest_fails(tree: Path) -> None:
    """数える相手が無いことを、読み取れた値と一致しているとみなさない。"""
    (tree / "plugins/ndf/manifests/kiro-skills.txt").unlink()
    result = run_check(tree)
    assert result.returncode != 0
    assert "kiro-skills.txt" in output_of(result)


def test_missing_plugin_json_fails(tree: Path) -> None:
    (tree / "plugins/ndf/.claude-plugin/plugin.json").unlink()
    result = run_check(tree)
    assert result.returncode != 0
    assert "plugin.json" in output_of(result)
