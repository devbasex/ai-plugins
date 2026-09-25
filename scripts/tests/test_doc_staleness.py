"""説明文書の記載を崩すとチェックが失敗することを確かめる。

記号（A〜F）は `issues/old/issue-178-doc-staleness-checks.md` の受け入れ条件に、
記号（G〜M）は `issues/parallel-batch-03/04-issue-209.md` の「チェックする記載」に対応する。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from doc_staleness_helpers import (
    CHECKER,
    REPO_ROOT,
    VERSIONING_MD_PATH,
    bump_plugin_version,
    edit,
    edit_all,
    output_of,
    retarget_version,
    run_check,
)


def root_readme(tree: Path) -> Path:
    return tree / "README.md"


def plugin_readme(tree: Path) -> Path:
    return tree / "plugins/ndf/README.md"


def test_consistent_tree_passes(tree: Path) -> None:
    """突き合わせ元と突き合わせ先が一致していれば終了コード 0 で終わる。"""
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_manifest_skill_with_inline_comment_counts_as_one(tree: Path) -> None:
    """現状固定: Skill 名の後ろにコメントがあっても 1 件として数える。"""
    manifest = tree / "plugins/ndf/manifests/claude-skills.txt"
    edit(manifest, "alpha\n", "alpha # 注記\n")
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_real_repository_passes() -> None:
    """実物のリポジトリでも通る。チェックを入れた時点で落ちる状態を作らない。"""
    result = run_check(REPO_ROOT)
    assert result.returncode == 0, output_of(result)


def test_root_defaults_to_current_directory() -> None:
    """現状固定: `--root` を省くとカレントディレクトリをリポジトリの根としてチェックする。"""
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, output_of(result)
    assert result.stdout == "documented skill counts and versions are up to date\n"


# --- A: README.md のランタイム別の公開 Skill 数 ---


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("Claude Code向け core 5個", "Claude Code向け core 4個", "5"),
        ("Kiro向け core 4個", "Kiro向け core 3個", "4"),
        ("Codex向け core 3個", "Codex向け core 2個", "3"),
        ("agy向け core 2個", "agy向け core 1個", "2"),
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
    [
        "Claude Code向け core 5個、",
        "Kiro向け core 4個、",
        "Codex向け core 3個、",
        "agy向け core 2個",
    ],
)
def test_runtime_skill_count_removed_fails(tree: Path, fragment: str) -> None:
    """記載を消してチェックを通せる状態を作らない。"""
    edit(root_readme(tree), fragment, "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


# --- B: README.md の元 Skill 数 ---


def test_source_skill_count_mismatch_fails(tree: Path) -> None:
    edit(root_readme(tree), "元Skills（5個）", "元Skills（8個）")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "README.md" in out
    assert "8" in out and "5" in out


def test_source_skill_count_removed_fails(tree: Path) -> None:
    edit(root_readme(tree), "- **元Skills（5個）**:\n", "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


# --- C: README.md のカテゴリ内訳 ---


def test_category_total_mismatch_fails(tree: Path) -> None:
    """行ごとの数と名前の数が揃っていても、合計が食い違えば失敗する。"""
    edit(
        root_readme(tree),
        "  - 第2群 (1): echo\n",
        "  - 第2群 (2): echo, foxtrot\n",
    )
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "6" in out and "5" in out


def test_category_line_count_mismatch_fails(tree: Path) -> None:
    """1 行の中で、宣言された数と並ぶ Skill 名の数が食い違えば失敗する。"""
    edit(root_readme(tree), "  - 第1群 (4): ", "  - 第1群 (5): ")
    result = run_check(tree)
    assert result.returncode != 0
    assert "第1群" in output_of(result)


def test_category_breakdown_removed_fails(tree: Path) -> None:
    edit(
        root_readme(tree),
        "  - 第1群 (4): alpha, bravo, charlie, delta\n  - 第2群 (1): echo\n",
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
        ("| agy | 2 個 |", "| agy | 7 個 |", "2"),
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
        "| agy | 2 個 | `dev.agy/plugin.json` |\n",
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


@pytest.mark.parametrize("version", ["9.7.0-dev.1", "9.7.0-rc.1", "9.6.0"])
def test_upgrade_heading_matches_whole_version(tree: Path, version: str) -> None:
    """見出しの版数を接尾辞まで 1 つの値として読む。

    数字 3 つだけで拾うと、`## v9.7.0-dev.1 へ更新するとき` を見出しとして読めない。
    接尾辞の無い版も同じ経路で通ることを、同じテストで確かめる。
    """
    retarget_version(tree, version)
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_upgrade_heading_without_suffix_fails(tree: Path) -> None:
    """接尾辞を落とした見出しは古い版として弾く。

    見出しを読めない状態を直すだけでは足りない。接尾辞を外して書けば通るようにすると、
    `plugin.json` と見出しが別の版を指したまま配布できてしまう。
    """
    retarget_version(tree, "9.7.0-dev.1")
    edit(plugin_readme(tree), "## v9.7.0-dev.1 へ更新するとき", "## v9.7.0 へ更新するとき")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "plugins/ndf/README.md" in out
    # 「見出しが無い」ではなく「版数が古い」として出す。読めていないのか食い違って
    # いるのかで、直し方が変わる。
    assert "見出し: v9.7.0 " in out
    assert "9.7.0-dev.1" in out


def test_upgrade_heading_stale_prerelease_fails(tree: Path) -> None:
    """接尾辞の連番だけが古い見出しも拾う。"""
    retarget_version(tree, "9.7.0-dev.2")
    edit(plugin_readme(tree), "## v9.7.0-dev.2 へ更新するとき", "## v9.7.0-dev.1 へ更新するとき")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "9.7.0-dev.1" in out and "9.7.0-dev.2" in out


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


def test_missing_root_readme_fails(tree: Path) -> None:
    """現状固定: 対象の説明文書 `README.md` が無いこと自体を失敗として扱い、そのパスを出す。"""
    root_readme(tree).unlink()
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


def test_missing_plugin_readme_fails(tree: Path) -> None:
    """現状固定: 対象の説明文書 `plugins/ndf/README.md` が無いこと自体を失敗として扱う。"""
    plugin_readme(tree).unlink()
    result = run_check(tree)
    assert result.returncode != 0
    assert "plugins/ndf/README.md" in output_of(result)


def test_missing_skills_dir_fails(tree: Path) -> None:
    """現状固定: Skill の実体を数える `plugins/ndf/skills` が無いことを失敗として扱う。"""
    shutil.rmtree(tree / "plugins/ndf/skills")
    result = run_check(tree)
    assert result.returncode != 0
    assert "plugins/ndf/skills" in output_of(result)


# --- G〜M: 説明文書の本文に書かれた版数 ---
#
# 記号は `issues/parallel-batch-03/04-issue-209.md` の「チェックする記載」に対応する。


def agents_md(tree: Path) -> Path:
    return tree / "AGENTS.md"


@pytest.mark.parametrize(
    ("mark", "document", "before", "after"),
    [
        ("G", "README.md", "**NDFプラグイン v9.3.0**", "**NDFプラグイン v9.2.1**"),
        ("I", "AGENTS.md", "主要プラグインです（v9.3.0）", "主要プラグインです（v9.2.1）"),
        ("K", "plugins/ndf/README.md", "Kiro CLI用 / v9.3.0）", "Kiro CLI用 / v9.2.1）"),
        (
            "M",
            "plugins/ndf/README.md",
            "installed, enabled  9.3.0",
            "installed, enabled  9.2.1",
        ),
    ],
)
def test_body_version_stale_fails(tree: Path, mark: str, document: str, before: str, after: str) -> None:
    """本文の版数を前の版へ書き換えると失敗する（点のチェックが 1 箇所ずつ働く）。"""
    edit(tree / document, before, after)
    result = run_check(tree)
    assert result.returncode != 0, mark
    out = output_of(result)
    assert document in out
    assert "9.2.1" in out and "9.3.0" in out


def test_codex_cache_path_partial_stale_fails(tree: Path) -> None:
    """L は同じ書き方が 2 箇所にある。片方だけ古くなっても失敗する。"""
    edit(
        plugin_readme(tree),
        "ai-plugins/ndf/9.3.0/skills/deploy/SKILL.md を読んでください。",
        "ai-plugins/ndf/9.2.1/skills/deploy/SKILL.md を読んでください。",
    )
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "plugins/ndf/README.md" in out
    assert "9.2.1" in out and "9.3.0" in out


@pytest.mark.parametrize(
    ("mark", "document", "fragment", "occurrences"),
    [
        ("G", "README.md", "**NDFプラグイン v9.3.0** のチェック用の最小構成です。\n", 1),
        ("I", "AGENTS.md", "主要プラグインです（v9.3.0）", 1),
        ("K", "plugins/ndf/README.md", "# => NDF統合開発エージェント（Kiro CLI用 / v9.3.0）\n", 1),
        ("L", "plugins/ndf/README.md", "~/.codex/plugins/cache/ai-plugins/ndf/9.3.0/skills/deploy/SKILL.md", 2),
        ("M", "plugins/ndf/README.md", "# => ndf@ai-plugins  installed, enabled  9.3.0  <path>\n", 1),
    ],
)
def test_body_version_removed_fails(
    tree: Path, mark: str, document: str, fragment: str, occurrences: int
) -> None:
    """記載を消してチェックを通せる状態にしない。"""
    edit_all(tree / document, fragment, "", occurrences)
    result = run_check(tree)
    assert result.returncode != 0, mark
    assert document in output_of(result)


# --- H: README.md のプラグイン一覧表 ---


def test_plugin_table_ndf_version_stale_fails(tree: Path) -> None:
    edit(root_readme(tree), "| **ndf** | 9.3.0 |", "| **ndf** | 9.2.1 |")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "README.md" in out
    assert "9.2.1" in out and "9.3.0" in out


def test_plugin_table_other_plugin_version_stale_fails(tree: Path) -> None:
    """行ごとに、その名前の `plugin.json` と突き合わせる。"""
    edit(root_readme(tree), "| **fixture-kit** | 1.4.2 |", "| **fixture-kit** | 1.4.1 |")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "fixture-kit" in out
    assert "1.4.1" in out and "1.4.2" in out


def test_plugin_table_unknown_plugin_fails(tree: Path) -> None:
    """突き合わせ先が無いことを、一致しているとみなさない。"""
    edit(root_readme(tree), "| **fixture-kit** | 1.4.2 |", "| **ghost-kit** | 1.4.2 |")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "ghost-kit" in out


def test_plugin_table_malformed_plugin_json_fails(tree: Path) -> None:
    """`plugin.json` が構文不正な場合、例外ではなくチェックの失敗として出す。"""
    (tree / "plugins/fixture-kit/.claude-plugin/plugin.json").write_text(
        "{\n  not json\n", encoding="utf-8"
    )
    result = run_check(tree)
    out = output_of(result)
    assert result.returncode != 0
    assert "Traceback" not in out
    assert "fixture-kit" in out
    assert "plugin.json" in out


def test_plugin_table_row_removed_fails(tree: Path) -> None:
    """一覧表から NDF の行を消してチェックを通せる状態にしない。"""
    edit(root_readme(tree), "| **ndf** | 9.3.0 | チェック用の最小構成 |\n", "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


# --- J: 正本の「版の付け方と開発版の配布」章（節のチェック） ---
#
# 版数の扱いの正本は `docs/versioning-and-distribution.md` である（#499）。チェック J は
# `AGENTS.md` ではなく正本の章 2 を読み、失敗も正本のパスで報告する。


def versioning_md(tree: Path) -> Path:
    return tree / VERSIONING_MD_PATH


def add_to_version_section(tree: Path, line: str) -> None:
    """版の付け方の章の末尾へ 1 行足す。"""
    edit(
        versioning_md(tree),
        "- 接尾辞は次に出す正式版の版数へ付ける。`9.3.0` の次を開発するなら `9.4.0-dev.1`\n",
        "- 接尾辞は次に出す正式版の版数へ付ける。`9.3.0` の次を開発するなら `9.4.0-dev.1`\n"
        f"{line}\n",
    )


def test_version_section_stale_example_fails(tree: Path) -> None:
    """章の中に現行版より古い基底の版数があれば失敗し、行番号が出力に入る。"""
    add_to_version_section(tree, "- 前の版の例。`9.2.1` はもう使わない")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "9.2.1" in out and "9.3.0" in out
    assert "L18" in out


def test_version_section_failure_names_the_canonical_document(tree: Path) -> None:
    """報告先は正本のパスである。`AGENTS.md` の失敗として出さない。"""
    add_to_version_section(tree, "- 前の版の例。`9.2.1` はもう使わない")
    out = output_of(run_check(tree))
    assert f"ERROR: {VERSIONING_MD_PATH}: 版の付け方の節の版数が現行版より古い" in out
    assert "ERROR: AGENTS.md" not in out


def test_version_section_newer_example_passes(tree: Path) -> None:
    """次の版を指す例を誤検出しない。"""
    add_to_version_section(tree, "- 次の版の例。`9.4.0-dev.1` を出す")
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_version_section_prerelease_of_current_passes(tree: Path) -> None:
    """semver の順序ではなく基底で比べている（`9.3.0-dev.1` < `9.3.0` でも通る）。"""
    add_to_version_section(tree, "- 開発版の例。`9.3.0-dev.9` は検証中である")
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_version_section_two_digit_minor_passes(tree: Path) -> None:
    """基底を文字列ではなく整数の組で比べている（文字列比較なら `"9.10.0" < "9.3.0"`）。"""
    add_to_version_section(tree, "- 先の版の例。`9.10.0` へ進む")
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_version_section_heading_removed_fails(tree: Path) -> None:
    """章を消してチェックを通せる状態にしない。"""
    edit(versioning_md(tree), "## 版の付け方と開発版の配布\n", "")
    result = run_check(tree)
    assert result.returncode != 0
    assert VERSIONING_MD_PATH in output_of(result)


def test_missing_versioning_document_fails(tree: Path) -> None:
    """正本そのものが無いことを失敗として扱い、正本のパスを出す。"""
    versioning_md(tree).unlink()
    result = run_check(tree)
    assert result.returncode != 0
    assert VERSIONING_MD_PATH in output_of(result)


def test_versions_after_the_section_do_not_fail(tree: Path) -> None:
    """章 2 より後ろの章に囲んだ古い版数があっても落ちない（節は次の同位の見出しで閉じる）。"""
    body = versioning_md(tree).read_text(encoding="utf-8")
    assert "`8.4.0`" in body.split("## 版数を持つ 15 箇所", 1)[1]
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_version_section_stops_at_a_higher_level_heading(tree: Path) -> None:
    """章の直後が上位の見出し（`# `）でも節を抜ける。

    自身と同じ深さの見出しだけで区切ると、次が上位の見出しのときに節が閉じない。閉じなければ
    走査は文書の末尾まで続き、後ろの章に並ぶ前の版の版数を現行版と比べてしまう。
    """
    edit(versioning_md(tree), "## 版数を持つ 15 箇所\n", "# 版数を持つ 15 箇所\n")
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_subheading_inside_the_section_does_not_close_it(tree: Path) -> None:
    """章 2 の中の `### ` 小見出しで節を閉じない。

    終端を深さを 3 に固定して取ると、`## ` の章の中の小見出しで章が途切れ、その後ろの古い版数を
    見落とす。終端は位置決めの見出しの深さから導く。
    """
    add_to_version_section(tree, "\n### 接尾辞の規則\n\n- 前の版の例。`9.2.1` はもう使わない")
    result = run_check(tree)
    assert result.returncode != 0
    assert "9.2.1" in output_of(result)


def test_other_software_version_in_the_section_is_ignored(tree: Path) -> None:
    """章の中の他ソフトの版数を拾わない。

    章は配布の手順を説明するため、CLI の名前と版数を並べて書くことがある。前後の 1 文字だけで
    位置を決めると、`codex-cli 0.146.1` の `0.146.1` のように空白で区切られた値が走査へ入り、
    現行版より小さい基底として誤検出になる。
    """
    add_to_version_section(tree, "- 確認に使った CLI は `codex-cli 0.146.1` である")
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_backticked_stale_prerelease_is_still_found(tree: Path) -> None:
    """位置を固定しても、接尾辞の付いた版数は従来どおり拾う。"""
    add_to_version_section(tree, "- 前の版の開発版。`9.2.0-dev.1` はもう使わない")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "9.2.0-dev.1" in out and "9.3.0" in out


def test_backticked_current_versions_pass(tree: Path) -> None:
    """現行版と、その接尾辞付き・次の版の例は通る（囲みの中でも拾えている）。"""
    add_to_version_section(
        tree,
        "- 例。`9.3.0` と `v9.3.0` と `9.3.0-rc.1` と `9.7.0-dev.1` はいずれも現行版以上",
    )
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def add_code_fence_to_version_section(tree: Path) -> None:
    """版の付け方の章の先頭へ、シェルのコメントを含む実行例を置く。"""
    edit(
        versioning_md(tree),
        "## 版の付け方と開発版の配布\n",
        "## 版の付け方と開発版の配布\n"
        "\n"
        "```bash\n"
        "# 常用する利用者（正式版）\n"
        "claude plugin marketplace add https://example.invalid/fixture\n"
        "```\n",
    )


def test_code_fence_comment_does_not_close_the_section(tree: Path) -> None:
    """囲みの中の `# ` 始まりで節を閉じない（実物の章は実行例を含む）。"""
    add_code_fence_to_version_section(tree)
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_stale_example_after_a_code_fence_is_still_found(tree: Path) -> None:
    """囲みより後ろも走査の対象に残る。閉じてしまうと後続の版数を見落とす。"""
    add_code_fence_to_version_section(tree)
    add_to_version_section(tree, "- 前の版の例。`9.2.1` はもう使わない")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "9.2.1" in out and "9.3.0" in out


def test_stale_example_after_a_tilde_code_fence_is_still_found(tree: Path) -> None:
    """~~~ の囲みより後ろも走査の対象に残る。閉じてしまうと後続の版数を見落とす。"""
    edit(
        versioning_md(tree),
        "## 版の付け方と開発版の配布\n",
        "## 版の付け方と開発版の配布\n"
        "\n"
        "~~~bash\n"
        "# 常用する利用者（正式版）\n"
        "claude plugin marketplace add https://example.invalid/fixture\n"
        "~~~\n",
    )
    add_to_version_section(tree, "- 前の版の例。`9.2.1` はもう使わない")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "9.2.1" in out and "9.3.0" in out


# --- J: 版の形の表と次の開発の例を、例どうしで比べる（#566） ---
#
# 雛形の章 2 は L13 が正式版 `9.3.0`、L14 が開発版 `9.4.0-dev.1`、L15 が公開前の確認版
# `9.4.0-rc.1`、L17 が「`9.3.0` の次を開発するなら `9.4.0-dev.1`」である。

J_ERROR = f"ERROR: {VERSIONING_MD_PATH}: 版の付け方の節の"
STABLE_ROW = "| 正式版 | `9.3.0` | 利用者が常用してよい |\n"
DEV_ROW = "| 開発版 | `9.4.0-dev.1` | 検証中 |\n"
RC_ROW = "| 公開前の確認版 | `9.4.0-rc.1` | 正式版の候補 |\n"
NEXT_EXAMPLE = "`9.3.0` の次を開発するなら `9.4.0-dev.1`"


def j_errors(result) -> list[str]:
    """チェック J の失敗の行だけを取り出す。"""
    return [line for line in output_of(result).splitlines() if line.startswith(J_ERROR)]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (DEV_ROW, "| 開発版 | `9.3.0` | 検証中 |\n", "開発版の行の接尾辞が違う（記載: 9.3.0（L14） / 求める形: -dev.<連番>）"),
        (
            RC_ROW,
            "| 公開前の確認版 | `9.4.0` | 正式版の候補 |\n",
            "公開前の確認版の行の接尾辞が違う（記載: 9.4.0（L15） / 求める形: -rc.<連番>）",
        ),
        (
            STABLE_ROW,
            "| 正式版 | `9.3.0-dev.1` | 利用者が常用してよい |\n",
            "正式版の行の接尾辞が違う（記載: 9.3.0-dev.1（L13） / 求める形: 接尾辞なし）",
        ),
    ],
    ids=["dev-without-suffix", "rc-without-suffix", "stable-with-suffix"],
)
def test_version_form_row_with_wrong_suffix_fails(tree: Path, before: str, after: str, expected: str) -> None:
    """AC1: 版の形の表の行の接尾辞が、その行の語が求める形でなければ行番号付きで落ちる。"""
    edit(versioning_md(tree), before, after)
    result = run_check(tree)
    assert result.returncode == 1
    assert J_ERROR + expected in j_errors(result), output_of(result)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (
            DEV_ROW,
            "| 開発版 | `9.3.0-dev.1` | 検証中 |\n",
            "開発版の行が正式版の行より新しい版を指していない（記載: 9.3.0-dev.1（L14） / 正式版: 9.3.0（L13））",
        ),
        (
            RC_ROW,
            "| 公開前の確認版 | `9.2.0-rc.1` | 正式版の候補 |\n",
            "公開前の確認版の行が正式版の行より新しい版を指していない（記載: 9.2.0-rc.1（L15） / 正式版: 9.3.0（L13））",
        ),
    ],
    ids=["dev-same-base", "rc-older-base"],
)
def test_version_form_row_not_newer_than_stable_fails(tree: Path, before: str, after: str, expected: str) -> None:
    """AC2: 開発版か公開前の確認版の行の基底が、正式版の行の基底以下なら落ちる。"""
    edit(versioning_md(tree), before, after)
    result = run_check(tree)
    assert result.returncode == 1
    assert J_ERROR + expected in j_errors(result), output_of(result)


@pytest.mark.parametrize("right", ["9.3.0-dev.1", "9.4.0", "9.2.0-dev.1"])
def test_next_development_example_not_pointing_to_next_version_fails(tree: Path, right: str) -> None:
    """AC3: 次の開発の例の右側が、左側より新しい基底の開発版でなければ落ちる。"""
    edit(versioning_md(tree), NEXT_EXAMPLE, f"`9.3.0` の次を開発するなら `{right}`")
    result = run_check(tree)
    assert result.returncode == 1
    expected = f"次の開発の例が次の版を指していない（記載: 9.3.0 → {right}（L17））"
    assert J_ERROR + expected in j_errors(result), output_of(result)


def test_issue_form_current_version_left_in_place_fails(tree: Path) -> None:
    """AC4: 現行版のまま、表の開発版の行と次の開発の例の右側が現行版を指す形（issue の 1 つ目）。"""
    edit(versioning_md(tree), DEV_ROW, "| 開発版 | `9.3.0` | 検証中 |\n")
    edit(versioning_md(tree), NEXT_EXAMPLE, "`9.3.0` の次を開発するなら `9.3.0-dev.1`")
    result = run_check(tree)
    assert result.returncode == 1
    assert j_errors(result) == [
        J_ERROR + "開発版の行の接尾辞が違う（記載: 9.3.0（L14） / 求める形: -dev.<連番>）",
        J_ERROR + "開発版の行が正式版の行より新しい版を指していない（記載: 9.3.0（L14） / 正式版: 9.3.0（L13））",
        J_ERROR + "次の開発の例が次の版を指していない（記載: 9.3.0 → 9.3.0-dev.1（L17））",
    ]


def test_issue_form_bulk_replacement_fails(tree: Path) -> None:
    """AC4: 現行版を一括で次の版へ置換し、開発版の行の接尾辞が消えた形（issue の 2 つ目）。

    置換は木のすべてのファイルへ行う。現行版を指す他の記載はすべて揃うため、落ちるのは章 2 の
    例だけになる。
    """
    for path in tree.rglob("*"):
        if path.is_file():
            body = path.read_text(encoding="utf-8")
            path.write_text(body.replace("9.3.0", "9.4.0"), encoding="utf-8")
    edit(versioning_md(tree), DEV_ROW, "| 開発版 | `9.4.0` | 検証中 |\n")
    result = run_check(tree)
    assert result.returncode == 1
    assert output_of(result).count("ERROR: ") == len(j_errors(result)), output_of(result)
    assert j_errors(result) == [
        J_ERROR + "開発版の行の接尾辞が違う（記載: 9.4.0（L14） / 求める形: -dev.<連番>）",
        J_ERROR + "開発版の行が正式版の行より新しい版を指していない（記載: 9.4.0（L14） / 正式版: 9.4.0（L13））",
        J_ERROR + "公開前の確認版の行が正式版の行より新しい版を指していない（記載: 9.4.0-rc.1（L15） / 正式版: 9.4.0（L13））",
        J_ERROR + "次の開発の例が次の版を指していない（記載: 9.4.0 → 9.4.0-dev.1（L17））",
    ]


@pytest.mark.parametrize(
    ("row", "label"),
    [(STABLE_ROW, "正式版"), (DEV_ROW, "開発版"), (RC_ROW, "公開前の確認版")],
    ids=["stable", "dev", "rc"],
)
def test_version_form_row_removed_fails(tree: Path, row: str, label: str) -> None:
    """AC5: 表の行を消して規則を外せない。"""
    edit(versioning_md(tree), row, "")
    result = run_check(tree)
    assert result.returncode == 1
    expected = f"版の形の表を読み取れない（無い行: {label}。| {label} | `<版>` | ... | の形で書く）"
    assert J_ERROR + expected in j_errors(result), output_of(result)


def test_version_form_row_duplicated_fails(tree: Path) -> None:
    """AC5: 同じ語の行が 2 つあると比べる相手が決まらないため落ちる。"""
    edit(versioning_md(tree), DEV_ROW, DEV_ROW + "| 開発版 | `9.4.0-dev.2` | 検証中 |\n")
    result = run_check(tree)
    assert result.returncode == 1
    expected = "版の形の表に同じ行が複数ある（開発版: L14, L15）"
    assert J_ERROR + expected in j_errors(result), output_of(result)


def test_next_development_example_removed_fails(tree: Path) -> None:
    """AC5: 次の開発の例を消して規則を外せない。"""
    edit(versioning_md(tree), f"。{NEXT_EXAMPLE}\n", "\n")
    result = run_check(tree)
    assert result.returncode == 1
    expected = "次の開発の例を読み取れない（`<版>` の次を開発するなら `<版>-dev.<連番>` の形で書く）"
    assert J_ERROR + expected in j_errors(result), output_of(result)


def test_version_form_row_inside_code_fence_is_not_counted(tree: Path) -> None:
    """AC5: 囲みの中の表の行と例は、章 2 の例として数えない（実行例を足しても落ちない）。"""
    add_to_version_section(
        tree,
        "\n```text\n| 開発版 | `9.3.0` | 検証中 |\n`9.3.0` の次を開発するなら `9.3.0` と書かない\n```",
    )
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_version_section_heading_removed_reports_only_the_existing_failure(tree: Path) -> None:
    """章の見出しが無いときは、既存の「版数を読み取れない」だけを出し、表と例の報告を重ねない。"""
    edit(versioning_md(tree), "## 版の付け方と開発版の配布\n", "")
    result = run_check(tree)
    assert result.returncode == 1
    assert [line for line in j_errors(result) if "版数を読み取れない" not in line] == []


# --- 突き合わせ先の版数そのものが読めないとき ---


@pytest.mark.parametrize("version", ["1.0", "9.3", "v9.3.0", "９.３.０", ""])
def test_malformed_plugin_version_is_reported_as_a_failure(tree: Path, version: str) -> None:
    """`plugin.json` の版数が semver の形でなければ、例外ではなくチェックの失敗として出す。

    途中で例外を投げると他の記載の判定まで巻き添えで消える。読めないこと自体を 1 件の
    食い違いとして数え、他のチェックと同じ出力の形で返す。
    """
    bump_plugin_version(tree, version)
    result = run_check(tree)
    out = output_of(result)
    assert result.returncode != 0
    assert "Traceback" not in out
    assert "plugin.json" in out and "ERROR: " in out


def test_missing_plugin_version_is_reported_as_a_failure(tree: Path) -> None:
    """`version` キーが無ければ、例外ではなくチェックの失敗として出す。"""
    (tree / "plugins/ndf/.claude-plugin/plugin.json").write_text(
        '{\n  "name": "ndf"\n}\n', encoding="utf-8"
    )
    result = run_check(tree)
    out = output_of(result)
    assert result.returncode != 0
    assert "Traceback" not in out
    assert "plugin.json" in out


def test_versions_outside_the_section_do_not_fail(tree: Path) -> None:
    """変更履歴・履歴の説明にある古い版数を誤検出しない。"""
    body = agents_md(tree).read_text(encoding="utf-8")
    assert "v8.5.4" in body and "8.4.0" in body
    root_body = root_readme(tree).read_text(encoding="utf-8")
    assert "### NDF v9.0.0 の主な変更（非互換）" in root_body and "v4.0.0" in root_body
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


# --- 版だけを上げたときに、古くなった記載がすべて挙がる ---


def test_bumping_only_the_plugin_version_reports_every_body_claim(tree: Path) -> None:
    """この課題が起きた経路そのもの。説明文書を直さずに版だけ上げると 7 種類が挙がる。"""
    bump_plugin_version(tree, "9.4.0")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    for subject in (
        "概要の版数",
        "プラグイン一覧表の ndf の版数",
        "「主要プラグインです（v<版>）」の版数",
        "版の付け方の節の版数",
        "Kiro の確認例の版数",
        "Codex のキャッシュパスの例の版数",
        "`codex plugin list` の出力例の版数",
    ):
        assert subject in out, f"{subject} が出力に無い\n{out}"


# --- 失敗の出力の形 ---


def test_version_failure_output_names_path_subject_line_and_both_values(tree: Path) -> None:
    """直す場所が出力だけで決まる。"""
    edit(root_readme(tree), "**NDFプラグイン v9.3.0**", "**NDFプラグイン v9.2.1**")
    result = run_check(tree)
    out = output_of(result)
    assert (
        "ERROR: README.md: 概要の版数が食い違う"
        "（記載: 9.2.1（L3） / plugins/ndf/.claude-plugin/plugin.json: 9.3.0）"
    ) in out


def test_count_failure_output_is_unchanged(tree: Path) -> None:
    """`Claim` を広げても、行番号を持たない数のチェックの出力は変わらない。"""
    edit(root_readme(tree), "Claude Code向け core 5個", "Claude Code向け core 9個")
    result = run_check(tree)
    out = output_of(result)
    assert (
        "ERROR: README.md: 公開Skills の Claude Code の数が食い違う"
        "（記載: 9 / plugins/ndf/manifests/claude-skills.txt: 5）"
    ) in out


def test_missing_agents_md_fails(tree: Path) -> None:
    """チェックの対象の説明文書が無いこと自体を失敗として扱う。"""
    agents_md(tree).unlink()
    result = run_check(tree)
    assert result.returncode != 0
    assert "AGENTS.md" in output_of(result)


# --- base_of: バージョン文字列を基底タプルへ分解する（単体）---


def _load_checker():
    """`scripts/check-doc-staleness.py` を module として読み込む。

    ファイル名にハイフンを含むため通常の import では取り込めない。
    リポジトリ内の他のテスト（`test_doc_line_limit.py` など）と同じく
    `spec_from_file_location` で読み込む。
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("check_doc_staleness", CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclass の解決は `cls.__module__` を `sys.modules` から引くため、
    # exec_module の前に登録しておく。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_named_plugin_version_returns_none_for_non_string_version(tmp_path: Path) -> None:
    """version が文字列でなければ None を返す（現状固定）。"""
    module = _load_checker()
    plugin_json = tmp_path / "plugins/test-plugin/.claude-plugin/plugin.json"
    plugin_json.parent.mkdir(parents=True)
    plugin_json.write_text('{"version": 123}\n', encoding="utf-8")

    assert module.named_plugin_version(tmp_path, "test-plugin") is None


def test_base_of_drops_suffix_and_splits_into_int_triple() -> None:
    """接尾辞あり・なしのどちらの入力も、同じ整数の 3 つ組へ分解される（現状固定）。"""
    module = _load_checker()
    assert module.base_of("9.6.0-dev.1") == (9, 6, 0)
    assert module.base_of("9.6.0") == (9, 6, 0)
    assert module.base_of("9.6.0-dev.1") == module.base_of("9.6.0")


# --- manifest_skill_count: 有効な Skill 名が 0 件の境界（単体）---


def test_manifest_skill_count_with_only_blank_and_comment_lines_returns_zero(tmp_path: Path) -> None:
    """空行とコメント行だけのマニフェストは 0 を返し、エラーを記録しない（現状固定）。

    有効な Skill 名が 1 件も無い境界において、`manifest_skill_count` が `None` ではなく
    0 を返し、`report.errors` が空のまま戻ることを固定する。ファイルが存在しないときの
    `None` と区別されている経路である。
    """
    module = _load_checker()
    manifest = tmp_path / "plugins/ndf/manifests/kiro-skills.txt"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("# コメント行\n\n   \n# 別のコメント\n", encoding="utf-8")

    report = module.Report()
    assert module.manifest_skill_count(tmp_path, "kiro", report) == 0
    assert report.errors == []


# --- check_version_section: 節に版数が 1 件も無い境界（単体）---


def test_version_section_without_any_version_reports_once_and_returns() -> None:
    """見出しだけで囲みの版数が 0 件なら、読み取れない旨の 1 件だけを記録して戻る（現状固定）。"""
    module = _load_checker()
    report = module.Report()
    module.check_version_section(f"{module.VERSION_SECTION_HEADING}\n", "9.3.0", report)
    assert report.errors == [
        f"{module.VERSIONING_MD}: 版の付け方の節の版数を読み取れない"
        f"（`{module.VERSION_SECTION_HEADING}` の節へ版数の例を囲みで置く。"
        f"{module.PLUGIN_JSON}: 9.3.0）"
    ]


# --- check_version_section: 現行版数が取得できない分岐（単体）---


def test_version_section_with_no_current_version_returns_true_without_error() -> None:
    """節の版数を読めても現行版数が無ければ、比較せず正常終了する（現状固定）。"""
    module = _load_checker()
    report = module.Report()
    body = f"{module.VERSION_SECTION_HEADING}\n\n開発版の例は `9.3.0` である。\n"

    assert module.check_version_section(body, None, report) is True
    assert report.errors == []


# --- check_version_section: 節の後ろに終端の見出しが無い境界（単体）---


def test_version_section_at_end_of_document_scans_until_eof() -> None:
    """節が文書の末尾で終端の見出しが無くても、EOF まで走査して古い版数を記録する（現状固定）。"""
    module = _load_checker()
    report = module.Report()
    body = f"{module.VERSION_SECTION_HEADING}\n\n開発版の例は `9.2.1` である。\n"
    module.check_version_section(body, "9.3.0", report)
    assert report.errors == [
        f"{module.VERSIONING_MD}: 版の付け方の節の版数が現行版より古い"
        f"（記載: 9.2.1（L3） / {module.PLUGIN_JSON}: 9.3.0）"
    ]


def test_version_section_finds_stale_version_inside_code_fence() -> None:
    """囲みの中の版数も走査し、古い版数なら記録する（現状固定）。"""
    module = _load_checker()
    report = module.Report()
    body = (
        f"{module.VERSION_SECTION_HEADING}\n"
        "\n"
        "```text\n"
        "古い版の例は `9.2.1` である。\n"
        "```\n"
        "\n"
        "現行版の例は `9.3.0` である。\n"
    )
    module.check_version_section(body, "9.3.0", report)
    assert report.errors == [
        f"{module.VERSIONING_MD}: 版の付け方の節の版数が現行版より古い"
        f"（記載: 9.2.1（L4） / {module.PLUGIN_JSON}: 9.3.0）"
    ]



def test_category_breakdown_ideographic_comma_names_are_split() -> None:
    """Skill 名の区切りが読点「、」でも分割され、個数が計上される（現状固定）。

    `NAME_SEPARATOR` は `[,、]` で、半角カンマと読点のどちらも区切りとして扱う。
    読点で区切った本文を渡しても、宣言数と並ぶ名前の数が一致し、合計も総数と一致すれば
    Report にエラーが追加されないことを固定する。
    """
    module = _load_checker()
    report = module.Report()
    body = (
        "- **元Skills（5個）**:\n"
        "  - 第1群 (4): alpha、bravo、charlie、delta\n"
        "  - 第2群 (1): echo\n"
        "- 次の行\n"
    )
    module.check_category_breakdown(body, 5, "src", report)
    assert report.errors == []


# --- category_lines: 元Skills行が無い境界（単体）---


def test_category_lines_without_source_count_returns_none() -> None:
    """本文中に元Skills行が存在しない場合、None を返す（現状固定）。"""
    module = _load_checker()
    markdown = (
        "# ドキュメント\n"
        "\n"
        "- カテゴリA: 3個\n"
        "- カテゴリB: 2個\n"
    )
    assert module.category_lines(markdown) is None


def test_category_lines_stops_at_first_non_category_line() -> None:
    """カテゴリ内訳が途切れた後のカテゴリ行は拾わない（現状固定）。"""
    module = _load_checker()
    markdown = (
        "- **元Skills（5個）**:\n"
        "  - 第1群 (4): alpha, bravo, charlie, delta\n"
        "通常文\n"
        "  - 第2群 (1): echo\n"
    )
    matched = module.category_lines(markdown)
    assert matched is not None
    assert [found.group(0) for found in matched] == [
        "  - 第1群 (4): alpha, bravo, charlie, delta"
    ]


# --- location_of: index が lines の要素数以上である境界（単体）---


def test_location_of_with_index_out_of_bounds_returns_empty_string() -> None:
    """指定された index が lines の要素数以上である境界値において、空文字列を返す（現状固定）。"""
    module = _load_checker()
    claim = module.Claim(
        path="test.md",
        subject="テスト",
        wording="テスト",
        described=[10],
        expected=10,
        source="source",
        lines=[10],
    )
    assert module.location_of(claim, 1) == ""


# --- check_version_examples: 正式版の行が重複したとき比較をスキップする（単体）---


def test_version_examples_skips_base_comparison_when_stable_row_is_duplicated() -> None:
    """正式版の行が重複している場合、開発版との基底比較をスキップする（現状固定）。

    正式版の行が 1 つのときだけ比較を行う分岐（len(rows["正式版"]) == 1）において、
    正式版が重複したとき比較をスキップする経路を固定する。
    開発版が正式版より古い基底を持っていても、「正式版の行より新しい版を指していない」
    というエラーは出ず、正式版の重複エラーのみが報告される。
    """
    module = _load_checker()
    report = module.Report()
    body = (
        f"{module.VERSION_SECTION_HEADING}\n"
        "| 正式版 | `9.3.0` | 利用者が常用してよい |\n"
        "| 正式版 | `9.3.1` | 利用者が常用してよい |\n"
        "| 開発版 | `9.2.0-dev.1` | 検証中 |\n"
        "| 公開前の確認版 | `9.4.0-rc.1` | 正式版の候補 |\n"
        "\n"
        "`9.3.0` の次を開発するなら `9.4.0-dev.1`\n"
    )
    module.check_version_examples(body, report)
    assert report.errors == [
        f"{module.VERSIONING_MD}: 版の付け方の節の版の形の表に同じ行が複数ある（正式版: L2, L3）"
    ]
    assert not any("正式版の行より新しい版を指していない" in err for err in report.errors)


# --- compare_plugin_table_row: 記載版数と plugin.json の版数が食い違う分岐（単体）---


def test_compare_plugin_table_row_with_mismatched_version_records_error(
    tmp_path: Path,
) -> None:
    """一覧表の記載版数と plugin.json の版数が食い違う場合、行番号と双方の版数を含むエラーを記録する（現状固定）。"""
    module = _load_checker()
    plugin_json = tmp_path / "plugins/fixture-kit/.claude-plugin/plugin.json"
    plugin_json.parent.mkdir(parents=True)
    plugin_json.write_text('{"version": "1.4.2"}\n', encoding="utf-8")

    report = module.Report()
    module.compare_plugin_table_row(tmp_path, "fixture-kit", "1.4.1", 42, report)
    assert report.errors == [
        f"{module.ROOT_README}: プラグイン一覧表の fixture-kit の版数が食い違う"
        f"（記載: 1.4.1（L42） / {module.plugin_json_path('fixture-kit')}: 1.4.2）"
    ]

