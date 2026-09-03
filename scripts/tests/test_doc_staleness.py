"""説明文書の記載を崩すと検査が失敗することを確かめる。

記号（A〜F）は `issues/old/issue-178-doc-staleness-checks.md` の受け入れ条件に、
記号（G〜M）は `issues/parallel-batch-03/04-issue-209.md` の「検査する記載」に対応する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from doc_staleness_helpers import (
    REPO_ROOT,
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


# --- G〜M: 説明文書の本文に書かれた版数 ---
#
# 記号は `issues/parallel-batch-03/04-issue-209.md` の「検査する記載」に対応する。


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
    """本文の版数を前の版へ書き換えると失敗する（点の検査が 1 箇所ずつ働く）。"""
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
        ("G", "README.md", "**NDFプラグイン v9.3.0** の検査用の最小構成です。\n", 1),
        ("I", "AGENTS.md", "主要プラグインです（v9.3.0）", 1),
        ("K", "plugins/ndf/README.md", "# => NDF統合開発エージェント（Kiro CLI用 / v9.3.0）\n", 1),
        ("L", "plugins/ndf/README.md", "~/.codex/plugins/cache/ai-plugins/ndf/9.3.0/skills/deploy/SKILL.md", 2),
        ("M", "plugins/ndf/README.md", "# => ndf@ai-plugins  installed, enabled  9.3.0  <path>\n", 1),
    ],
)
def test_body_version_removed_fails(
    tree: Path, mark: str, document: str, fragment: str, occurrences: int
) -> None:
    """記載を消して検査を通せる状態にしない。"""
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


def test_plugin_table_row_removed_fails(tree: Path) -> None:
    """一覧表から NDF の行を消して検査を通せる状態にしない。"""
    edit(root_readme(tree), "| **ndf** | 9.3.0 | 検査用の最小構成 |\n", "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "README.md" in output_of(result)


# --- J: AGENTS.md の「版の付け方と開発版の配布」節（区間の検査） ---


def add_to_version_section(tree: Path, line: str) -> None:
    """版の付け方の節の末尾へ 1 行足す。"""
    edit(
        agents_md(tree),
        "- 接尾辞は次に出す正式版の版数へ付ける。`9.3.0` の次を開発するなら `9.4.0-dev.1`\n",
        "- 接尾辞は次に出す正式版の版数へ付ける。`9.3.0` の次を開発するなら `9.4.0-dev.1`\n"
        f"{line}\n",
    )


def test_version_section_stale_example_fails(tree: Path) -> None:
    """節の中に現行版より古い基底の版数があれば失敗し、行番号が出力に入る。"""
    add_to_version_section(tree, "- 前の版の例。`9.2.1` はもう使わない")
    result = run_check(tree)
    assert result.returncode != 0
    out = output_of(result)
    assert "AGENTS.md" in out
    assert "9.2.1" in out and "9.3.0" in out
    assert "L13" in out


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
    """節を消して検査を通せる状態にしない。"""
    edit(agents_md(tree), "### 版の付け方と開発版の配布\n", "")
    result = run_check(tree)
    assert result.returncode != 0
    assert "AGENTS.md" in output_of(result)


def test_version_section_stops_at_a_higher_level_heading(tree: Path) -> None:
    """節の直後が上位の見出し（`## `）でも区間を抜ける。

    自身と同じ深さの見出しだけで区切ると、次が `## ` のときに区間が閉じない。閉じなければ
    走査は文書の末尾まで続き、変更履歴に並ぶ前の版の版数を現行版と比べてしまう。
    """
    edit(agents_md(tree), "### 検査が突き合わせる箇所\n", "## 検査が突き合わせる箇所\n")
    body = agents_md(tree).read_text(encoding="utf-8")
    assert "v8.5.4" in body and "8.4.0" in body
    result = run_check(tree)
    assert result.returncode == 0, output_of(result)


def test_other_software_version_in_the_section_is_ignored(tree: Path) -> None:
    """節の中の他ソフトの版数を拾わない。

    節は配布の手順を説明するため、CLI の名前と版数を並べて書くことがある。前後の 1 文字だけで
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
    """版の付け方の節の先頭へ、シェルのコメントを含む実行例を置く。"""
    edit(
        agents_md(tree),
        "### 版の付け方と開発版の配布\n",
        "### 版の付け方と開発版の配布\n"
        "\n"
        "```bash\n"
        "# 常用する利用者（正式版）\n"
        "claude plugin marketplace add https://example.invalid/fixture\n"
        "```\n",
    )


def test_code_fence_comment_does_not_close_the_section(tree: Path) -> None:
    """囲みの中の `# ` 始まりで区間を閉じない（実物の節は実行例を含む）。"""
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


# --- 突き合わせ先の版数そのものが読めないとき ---


@pytest.mark.parametrize("version", ["1.0", "9.3", "v9.3.0", "９.３.０", ""])
def test_malformed_plugin_version_is_reported_as_a_failure(tree: Path, version: str) -> None:
    """`plugin.json` の版数が semver の形でなければ、例外ではなく検査の失敗として出す。

    途中で例外を投げると他の記載の判定まで巻き添えで消える。読めないこと自体を 1 件の
    食い違いとして数え、他の検査と同じ出力の形で返す。
    """
    bump_plugin_version(tree, version)
    result = run_check(tree)
    out = output_of(result)
    assert result.returncode != 0
    assert "Traceback" not in out
    assert "plugin.json" in out and "ERROR: " in out


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
    """`Claim` を広げても、行番号を持たない数の検査の出力は変わらない。"""
    edit(root_readme(tree), "Claude Code向け core 5個", "Claude Code向け core 9個")
    result = run_check(tree)
    out = output_of(result)
    assert (
        "ERROR: README.md: 公開Skills の Claude Code の数が食い違う"
        "（記載: 9 / plugins/ndf/manifests/claude-skills.txt: 5）"
    ) in out


def test_missing_agents_md_fails(tree: Path) -> None:
    """検査の対象の説明文書が無いこと自体を失敗として扱う。"""
    agents_md(tree).unlink()
    result = run_check(tree)
    assert result.returncode != 0
    assert "AGENTS.md" in output_of(result)
