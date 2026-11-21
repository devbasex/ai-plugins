"""リンクチェックの見出しへの参照の照合を固定する（#445）。

**名前の規則は GitHub の生成規則に合わせる。** 小文字にし、文字・数字・結合文字・空白・
`-`・`_` 以外を落とし、空白を `-` にし、重複へ `-1` から連番を付ける。規則と決定の理由は
`docs/specifications/doc-consistency-checks.md` にある。

チェックは一時ディレクトリへ作った木に対して別プロセスで実行する。スクリプト名が `-` を含み、
そのままでは import できないためである。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-markdown-links.py"
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import md  # noqa: E402  見出しのアンカーの規則の持ち主（#1142 の D8）


@pytest.fixture
def check_module():
    """scripts/check-markdown-links.py をテストごとに新しいモジュールとして読み込む。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root)],
        capture_output=True,
        text=True,
    )


def write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def failure_lines(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.startswith("- ")]


def test_same_document_missing_heading_fails(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "# 手順\n\n[飛ぶ](#無い見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- docs/a.md: missing heading anchor: #無い見出し"]


def test_same_document_existing_heading_passes(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "## Design Notes\n\n[飛ぶ](#design-notes)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_other_document_missing_heading_fails(tmp_path: Path) -> None:
    write(tmp_path, "docs/b.md", "# 在る見出し\n")
    write(tmp_path, "docs/a.md", "[飛ぶ](b.md#在る見出し)\n[飛ぶ](b.md#無い見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- docs/a.md: missing heading anchor: b.md#無い見出し"]


def test_duplicate_heading_resolves_with_suffix(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "## 手順\n\n## 手順\n\n[1つ目](#手順)\n[2つ目](#手順-1)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_duplicate_heading_beyond_count_fails(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "## 手順\n\n## 手順\n\n[3つ目](#手順-2)\n")
    assert run(tmp_path).returncode == 1


def test_japanese_heading_resolves_raw_and_percent_encoded(tmp_path: Path) -> None:
    anchor = "scripts-を決める"
    write(tmp_path, "docs/b.md", "## `$SCRIPTS` を決める\n")
    write(
        tmp_path,
        "docs/a.md",
        f"[そのまま](b.md#{anchor})\n[符号化](b.md#{quote(anchor)})\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_percent_encoded_document_path_and_heading_resolve(tmp_path: Path) -> None:
    """パスと見出しを符号化した相対リンクの現状を固定する（R2-004）。"""
    document = "reference guide.md"
    anchor = "利用-方法"
    write(tmp_path, f"docs/{document}", "## 利用 方法\n")
    write(
        tmp_path,
        "docs/a.md",
        f"[飛ぶ]({quote(document)}#{quote(anchor)})\n",
    )

    result = run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert failure_lines(result) == []


def test_heading_with_angle_brackets_in_inline_code_resolves(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/a.md",
        "### `<worktree-base>` の解決順\n\n[飛ぶ](#worktree-base-の解決順)\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_link_inside_inline_code_is_ignored(tmp_path: Path) -> None:
    """#543 決定 4: インラインコードの中の記法の例はリンクとして読まない。"""
    write(
        tmp_path,
        "docs/a.md",
        '`[文言](位置)` の書き方と `<a href="無い.md">` の書き方\n``[x](無い.md) と ` を含む``\n',
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_link_outside_inline_code_on_same_line_fails(tmp_path: Path) -> None:
    """#543: 同じ行でも、インラインコードの外にある解決できないリンクは落ちる。"""
    write(tmp_path, "docs/a.md", "`[文言](位置)` と [x](無い.md)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- docs/a.md: missing link target: 無い.md"]


def test_unclosed_backtick_run_does_not_hide_link(tmp_path: Path) -> None:
    """#543: 同じ本数の列で閉じないバッククォートは、後ろのリンクを隠さない。"""
    write(tmp_path, "docs/a.md", "`` 閉じない ` と [x](無い.md)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- docs/a.md: missing link target: 無い.md"]


def test_reference_inside_code_fence_is_ignored(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "# 手順\n\n```markdown\n[飛ぶ](#無い見出し)\n```\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_broken_links_inside_block_quote_are_ignored(tmp_path: Path) -> None:
    """引用内の Markdown・HTML リンクを除外する現状を固定する（R1-005）。"""
    write(
        tmp_path,
        "docs/a.md",
        '> [Markdown](missing-markdown.md)\n> <a href="missing-html.md">HTML</a>\n',
    )

    result = run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert failure_lines(result) == []


def test_missing_document_reports_only_missing_file(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "[飛ぶ](無い.md#見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- docs/a.md: missing link target: 無い.md#見出し"]


def test_relative_link_escaping_repository_fails(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "[out](../../outside.md)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == [
        "- docs/a.md: link escapes repository: ../../outside.md",
    ]


def test_line_starting_with_hash_without_space_is_not_heading(tmp_path: Path) -> None:
    """`#183 の指摘は…` のような課題番号で始まる文は見出しにしない。"""
    write(tmp_path, "docs/a.md", "#183 の指摘\n\n[飛ぶ](#183-の指摘)\n")
    assert run(tmp_path).returncode == 1


def test_heading_inside_quote_or_fence_is_not_heading(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/a.md",
        "> ## 引用の見出し\n\n```bash\n# コメント\n```\n\n[引用](#引用の見出し)\n[コメント](#コメント)\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert len(failure_lines(result)) == 2


def test_document_outside_scan_scope_is_not_checked_for_headings(tmp_path: Path) -> None:
    """決定 2: チェックの対象外の文書（`notes/`）は、見出しを読みに行かない。"""
    write(tmp_path, "notes/plan.md", "# 在る見出し\n")
    write(tmp_path, "docs/a.md", "[飛ぶ](../notes/plan.md#無い見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_issues_document_missing_heading_fails(tmp_path: Path) -> None:
    """#543: `issues/` の文書の無い見出しを指す参照は落ちる。"""
    write(tmp_path, "issues/plan.md", "# 在る見出し\n")
    write(tmp_path, "docs/a.md", "[飛ぶ](../issues/plan.md#在る見出し)\n[飛ぶ](../issues/plan.md#無い見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == [
        "- docs/a.md: missing heading anchor: ../issues/plan.md#無い見出し",
    ]


@pytest.mark.parametrize("rel", ["issues/plan.md", "issues/old/batch/00.md"])
def test_issues_document_missing_link_target_fails(tmp_path: Path, rel: str) -> None:
    """#543: `issues/` 直下と `issues/old/` の入れ子の文書の壊れた参照は落ちる。"""
    write(tmp_path, rel, "[x](無い.md)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == [f"- {rel}: missing link target: 無い.md"]


def test_external_url_with_fragment_is_ignored(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "[外](https://example.com/x.md#無い見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_relative_link_without_fragment_to_existing_file_passes(tmp_path: Path) -> None:
    """見出しへの参照を持たない通常の相対リンクの現状を固定する（R1-004）。"""
    write(tmp_path, "docs/b.md", "# 参照先\n")
    write(tmp_path, "docs/a.md", "[飛ぶ](b.md)\n[飛ぶ](../docs/b.md)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == "Markdown local links are valid\n"


def test_link_targets_extracts_html_and_excludes_images(check_module) -> None:
    """link_targets の現状を固定する（#445, R1-003）。

    標準の Markdown リンクに加え、ダブルクォート・シングルクォートの
    `<a href="...">` を抽出し、画像リンク記法 `![alt](...)` は除外する。
    抽出は Markdown リンクが先、インライン HTML が後の順に並ぶ。
    """
    text = "標準リンク [a](std.md) と <a href=\"dq.md\">dq</a> と <a href='sq.md'>sq</a> と 画像 ![alt](img.png) を含む\n"
    targets = check_module.link_targets(text)

    assert "dq.md" in targets
    assert "sq.md" in targets
    assert "img.png" not in targets
    assert targets == ["std.md", "dq.md", "sq.md"]


def test_link_targets_returns_empty_list_for_plain_text_and_empty_string(check_module) -> None:
    """link_targets の空文字列やプレーンテキストに対する境界値の現状を固定する（R2-005）。

    リンク記法（Markdown または HTML）を含まないテキストや空文字列に対して、
    link_targets が空リストを返すことを確認する。
    """
    assert check_module.link_targets("") == []
    assert check_module.link_targets("リンク記法を含まない通常のプレーンテキスト") == []
    assert check_module.link_targets("Markdown や HTML のタグのない複数行\nテキストです。") == []


def test_should_skip(check_module) -> None:
    assert check_module.should_skip("") is True
    assert check_module.should_skip("/path") is True
    assert check_module.should_skip("//domain/path") is True
    assert check_module.should_skip("mailto:user@example.com") is True
    assert check_module.should_skip("{repo}/blob/{branch}") is True
    assert check_module.should_skip("path/to/file.md") is False


def test_heading_anchors_matches_levels_and_skips_non_headings(tmp_path: Path, check_module) -> None:
    """heading_anchors の見出し行判定分岐の現状を固定する（R1-003）。

    `#` 1〜6 個の各レベルの行は見出しとして slugify されアンカー集合へ入り、
    見出しでない行（空行・通常テキスト・`#` を空白なしで始める行・`#` 7 個以上の行）は
    `HEADING_RE.match` が None を返して除外される。
    """
    body = (
        "# Level One\n"
        "## Level Two\n"
        "### Level Three\n"
        "#### Level Four\n"
        "##### Level Five\n"
        "###### Level Six\n"
        "\n"
        "plain text line\n"
        "####### Seven Hashes\n"
        "#nospace after hash\n"
    )
    path = tmp_path / "a.md"
    path.write_text(body, encoding="utf-8")

    anchors = check_module.heading_anchors(path)

    assert anchors == {
        "level-one",
        "level-two",
        "level-three",
        "level-four",
        "level-five",
        "level-six",
    }


def test_anchor_refs_empty_or_missing_fragment_skipped(check_module) -> None:
    """anchor_refs の空フラグメント等の境界値経路の現状を固定する（R2-001）。

    'file.md#' や '#' のように sep は存在するが fragment が空のリンク、
    および 'file.md' のように sep 自体が存在しないリンクはスキップされ空リストを返す。
    """
    assert check_module.anchor_refs("[x](file.md#)\n") == []
    assert check_module.anchor_refs("[x](#)\n") == []
    assert check_module.anchor_refs("[x](file.md)\n") == []


def test_anchor_refs_extracts_same_and_relative_document_fragments(check_module) -> None:
    """同一文書と相対文書のアンカー抽出の現状を固定する（R1-001）。"""
    text = "[同一文書](#sec)\n[相対文書](doc.md#other-sec)\n"

    assert check_module.anchor_refs(text) == [
        ("", "sec", "#sec"),
        ("doc.md", "other-sec", "doc.md#other-sec"),
    ]


def test_anchor_refs_skips_external_and_absolute_path_fragments(check_module) -> None:
    """スキップ対象のパスを持つアンカー参照の現状を固定する（R1-001）。"""
    text = "[外部](https://example.com/doc.md#sec)\n[絶対パス](/doc.md#sec)\n"

    assert check_module.anchor_refs(text) == []


def test_heading_anchors_collision_with_explicit_numbered_heading(check_module) -> None:
    """heading_anchors の連番衝突解決の while ループ反復経路を固定する（R2-002）。

    自動付番される名前（`手順-1`）と同名の見出しが文書内に明示的に書かれているとき、
    後続の同名見出しは while ループが複数回実行され、次の空き連番（`手順-2`）まで
    探索して解決する。明示的な `## 手順-1` を先頭に置くことで、2 つ目の `## 手順` が
    `手順` → `手順-1`（衝突）→ `手順-2` と 2 回反復する経路を通す。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "a.md"
        path.write_text("## 手順-1\n\n## 手順\n\n## 手順\n", encoding="utf-8")
        anchors = check_module.heading_anchors(path)

    assert anchors == {"手順", "手順-1", "手順-2"}


def test_iter_markdown_files_collects_root_files_and_scan_dirs(tmp_path: Path, check_module) -> None:
    """iter_markdown_files の探索範囲の現状を固定する（R2-003）。

    ルート直下の所定ファイルは個別に、`docs/` と `plugins/` は配下を再帰で集める。
    `issues/` も配下を再帰で集める（#543）。
    所定外のルート直下ファイル・対象外ディレクトリ（`notes/`）・`.md` 以外は含めず、
    戻り値はソート済みで重複を持たない。
    """
    write(tmp_path, "README.md", "# r\n")
    write(tmp_path, "OTHER.md", "# o\n")
    write(tmp_path, "docs/b.md", "# b\n")
    write(tmp_path, "docs/sub/a.md", "# a\n")
    write(tmp_path, "docs/note.txt", "n\n")
    write(tmp_path, "plugins/p/README.md", "# p\n")
    write(tmp_path, "issues/plan.md", "# i\n")
    write(tmp_path, "notes/plan.md", "# n\n")

    files = check_module.iter_markdown_files(tmp_path)

    assert files == [
        tmp_path / "README.md",
        tmp_path / "docs" / "b.md",
        tmp_path / "docs" / "sub" / "a.md",
        tmp_path / "issues" / "plan.md",
        tmp_path / "plugins" / "p" / "README.md",
    ]
    assert files == sorted(set(files))


def test_iter_markdown_files_returns_empty_list_for_empty_root(tmp_path: Path, check_module) -> None:
    """既定の走査対象が存在しない空ルートの現状を固定する（R2-004）。"""
    assert check_module.iter_markdown_files(tmp_path) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a   b", "a---b"),
        ("A_B C-D", "a_b-c-d"),
        ("Hello, World!", "hello-world"),
        ("  test  ", "test"),
    ],
)
def test_slugify_boundary_and_character_retention(text: str, expected: str) -> None:
    """見出しのアンカーの空白展開・記号保持・記号除去の境界値規則を固定する（R2-005）。"""
    assert md.heading_anchor(text) == expected


def test_slugify_keeps_combining_mark(tmp_path: Path) -> None:
    """slugify が M(結合文字)カテゴリの文字を保持する分岐を固定する（R2-002）。

    既存の境界値テストは L(字母)・N(数字)・空白・記号だけを通し、
    SLUG_KEEP_CATEGORIES='LMN' のうち M(結合文字)の分岐を一度も通っていない。
    結合アキュートアクセント（U+0301）を含む文字列を渡し、その結合文字が
    落とされずに残る現状の振る舞いを記録する。
    """
    assert md.heading_anchor("Cafe\u0301 Menu") == "cafe\u0301-menu"


def test_target_path_current_behavior(tmp_path: Path, check_module) -> None:
    """target_path のリンク解決・スキップ判定の現状を固定する（R4-001）。"""
    source = tmp_path / "docs" / "index.md"

    # スキップ対象は None
    assert check_module.target_path(source, "") is None
    assert check_module.target_path(source, "https://example.com/foo.md") is None
    assert check_module.target_path(source, "/root/file.md") is None

    # 同一文書内アンカー（path_part が空）は None
    assert check_module.target_path(source, "#heading") is None
    assert check_module.target_path(source, "#") is None

    # 相対パス（フラグメント付き含む）は解決先 Path。タイトルと山括弧は lib/md.py が外した後の値を受ける
    assert check_module.target_path(source, "other.md") == (tmp_path / "docs" / "other.md").resolve()
    assert check_module.target_path(source, "other.md#heading") == (tmp_path / "docs" / "other.md").resolve()
    assert check_module.target_path(source, "../readme.md") == (tmp_path / "readme.md").resolve()


def test_no_scanned_markdown_files_passes(tmp_path: Path) -> None:
    """走査対象の Markdown が 0 件のとき成功する現状を固定する（R1-004）。"""
    write(tmp_path, "notes/a.md", "[x](無い.md)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert failure_lines(result) == []
    assert result.stdout == "Markdown local links are valid\n"


def test_html_anchor_with_missing_heading_fragment_fails(tmp_path: Path) -> None:
    """フラグメント付き HTML リンクの見出し参照失敗の現状を固定する（R2-001）。"""
    write(tmp_path, "docs/b.md", "# 在る見出し\n")
    write(tmp_path, "docs/a.md", '<a href="b.md#無い見出し">x</a>\n')
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- docs/a.md: missing heading anchor: b.md#無い見出し"]


def test_title_and_angle_brackets_are_left_to_the_markdown_parser(tmp_path: Path) -> None:
    """#1142 の D8: リンク先のタイトルと山括弧は lib/md.py（CommonMark）が外す。"""
    write(tmp_path, "docs/other.md", "# o\n")
    write(tmp_path, "docs/a.md", '[x](<other.md> "t") [y](other.md \'t\') [z](other.md (t))\n[w](<無い.md> "t")\n')
    result = run(tmp_path)
    assert failure_lines(result) == ["- docs/a.md: missing link target: 無い.md"]


def test_reference_links_are_checked_and_spaces_need_angle_brackets(tmp_path: Path) -> None:
    """#1142 の D8 で変わる入力: 参照の形のリンクも確かめる（移す前は読まなかった）。空白を含むリンク先は
    山括弧で囲んだときだけリンクになる（移す前は `[x](a b.md)` も読んだ）。"""
    write(tmp_path, "docs/a.md", "[x][r] [y](無い 1.md)\n\n[r]: 無い2.md\n")
    result = run(tmp_path)
    assert failure_lines(result) == ["- docs/a.md: missing link target: 無い2.md"]


def test_setext_headings_have_anchors(tmp_path: Path) -> None:
    """#1142 の D8 で変わる入力: 下線の形（Setext）の見出しにもアンカーがある（移す前は `#` の形だけ）。"""
    write(tmp_path, "docs/a.md", "見出し\n===\n\n[x](#見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    write(root, ".gitignore", ".venv/\n")


def test_ignored_markdown_in_git_repository_is_not_scanned(tmp_path: Path) -> None:
    """git の管理下では無視されたディレクトリ（`.venv/`）の .md を走査しない。

    `plugins/ndf/.venv/` に入った依存の README の壊れたリンクで pre-push が落ちた（2026-09-28）。
    """
    _git_repo(tmp_path)
    write(tmp_path, "plugins/ndf/.venv/x/README.md", "[壊れた](missing.md)\n")
    write(tmp_path, "docs/a.md", "# a\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/a.md"], check=True)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_tracked_and_new_markdown_in_git_repository_are_checked(tmp_path: Path) -> None:
    """追跡済みの .md と、まだ add していない新しい .md の壊れたリンクは従来どおり検出する。"""
    _git_repo(tmp_path)
    write(tmp_path, "docs/a.md", "[壊れた](missing.md)\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/a.md"], check=True)
    write(tmp_path, "docs/new.md", "[壊れた](gone.md)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == [
        "- docs/a.md: missing link target: missing.md",
        "- docs/new.md: missing link target: gone.md",
    ]


# --- I-017: `<a href>` の抽出（_html_hrefs）の現状固定。入口は link_targets と CLI ---


def test_link_targets_html_block_yields_every_href_in_order(check_module) -> None:
    """HTML ブロックの中の `<a href>` は、大文字・別の属性・単引用符でも出現順にすべて拾う。"""
    text = '<div>\n<a href="one.md">1</a> <A HREF="two.md">2</A>\n<a class="x" href=\'three.md\'>3</a>\n</div>\n'
    assert check_module.link_targets(text) == ["one.md", "two.md", "three.md"]


def test_link_targets_inline_html_after_all_markdown_links(check_module) -> None:
    """段落の中のインライン HTML は、文書のすべての Markdown リンクより後に並ぶ。"""
    text = '<a href="first-html.md">h</a> と [m1](m1.md)\n\n[m2](m2.md) と <a href="second-html.md">h</a>\n'
    assert check_module.link_targets(text) == ["m1.md", "m2.md", "first-html.md", "second-html.md"]


def test_link_targets_html_in_code_and_quote_is_left_out(check_module) -> None:
    """コードスパン・コードフェンス・引用の中の `<a href>` は拾わない。"""
    text = (
        '`<a href="span.md">x</a>`\n\n'
        "```\n<a href=\"fence.md\">x</a>\n```\n\n"
        '> <a href="quote.md">x</a>\n\n'
        '<a href="kept.md">x</a>\n'
    )  # fmt: skip
    assert check_module.link_targets(text) == ["kept.md"]


def test_link_targets_html_block_inside_quote_is_left_out(check_module) -> None:
    """引用の中の HTML ブロックは拾わず、引用の外の HTML ブロックだけを拾う。"""
    text = '> <div>\n> <a href="q.md">x</a>\n> </div>\n\n<div><a href="k.md">k</a></div>\n'
    assert check_module.link_targets(text) == ["k.md"]


def test_link_targets_anchor_without_href_or_with_empty_href_is_left_out(check_module) -> None:
    """href の無い `<a>` と、空の href は拾わない（正規表現が 1 文字以上を求める）。"""
    text = 'text <a name="x">x</a> <a href="">empty</a> <a href="ok.md">ok</a>\n'
    assert check_module.link_targets(text) == ["ok.md"]


def test_html_block_missing_target_is_reported(tmp_path: Path) -> None:
    """HTML ブロックの `<a href>` も、リンク先が無ければ CLI が落とす。"""
    write(tmp_path, "docs/a.md", '<p>\n<a href="nope.md">x</a>\n</p>\n')
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- docs/a.md: missing link target: nope.md"]


# --- I-019: main の現状固定。失敗の 3 種の並びと、見出しを見ないリンク ---


def test_main_reports_link_failures_before_anchor_failures_per_document(tmp_path: Path) -> None:
    """文書ごとに、リンク先の失敗（はみ出し・不在）を先に、見出しの失敗を後に出す。

    リンク先が無い文書への `#` 付きリンクと、走査の外の文書への `#` 付きリンクは、
    見出しの失敗として出さない。stdout には何も出さない。
    """
    write(
        tmp_path,
        "docs/a.md",
        "# 見出し\n\n"
        "[esc](../../outside.md) [miss](nope.md) [ok](b.md#ある) [bad](b.md#無い) "
        "[self](#見出し) [selfbad](#x)\n\n"
        "[miss-anchor](nope.md#y) [out](../other/c.md#z)\n",
    )
    write(tmp_path, "docs/b.md", "# ある\n\n[back](a.md#無い2)\n")
    write(tmp_path, "other/c.md", "# c\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines()[0] == "Markdown link check failed:"
    assert failure_lines(result) == [
        "- docs/a.md: link escapes repository: ../../outside.md",
        "- docs/a.md: missing link target: nope.md",
        "- docs/a.md: missing link target: nope.md#y",
        "- docs/a.md: missing heading anchor: b.md#無い",
        "- docs/a.md: missing heading anchor: #x",
        "- docs/b.md: missing heading anchor: a.md#無い2",
    ]


def test_main_same_target_document_checked_from_several_documents(tmp_path: Path) -> None:
    """同じ文書の見出しを複数の文書から参照しても、文書ごとに同じ判定になる。"""
    write(tmp_path, "docs/t.md", "# 的\n")
    write(tmp_path, "docs/a.md", "[ok](t.md#的) [ng](t.md#外れ)\n")
    write(tmp_path, "docs/b.md", "[ok](t.md#的) [ng](t.md#外れ)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == [
        "- docs/a.md: missing heading anchor: t.md#外れ",
        "- docs/b.md: missing heading anchor: t.md#外れ",
    ]


def _shipped_skill(root: Path, name: str) -> None:
    """プラグインの manifests に配る Skill を 1 つ書く（#3910）。"""
    write(root, "plugins/ndf/manifests/skills.json", '{"skills": [{"name": "%s", "runtimes": ["claude"]}]}\n' % name)


def test_shipped_skill_link_outside_plugin_fails(tmp_path: Path) -> None:
    """配る Skill からプラグインの外を指すリンクは、リポジトリの中で開けても落ちる（#3910）。"""
    _shipped_skill(tmp_path, "a")
    write(tmp_path, "docs/specifications/x.md", "# x\n")
    write(tmp_path, "plugins/ndf/skills/a/references/r.md", "[表](../../../../../docs/specifications/x.md)\n")
    result = run(tmp_path)
    assert result.returncode == 1
    assert failure_lines(result) == ["- plugins/ndf/skills/a/references/r.md: link escapes plugin: ../../../../../docs/specifications/x.md"]


def test_shipped_skill_link_inside_plugin_passes(tmp_path: Path) -> None:
    """配る Skill からプラグインの中を指すリンクと、配らない Skill の外へのリンクは通る（#3910）。"""
    _shipped_skill(tmp_path, "a")
    write(tmp_path, "docs/x.md", "# x\n")
    write(tmp_path, "plugins/ndf/docs/y.md", "# y\n")
    write(tmp_path, "plugins/ndf/skills/a/SKILL.md", "[y](../../docs/y.md)\n")
    write(tmp_path, "plugins/ndf/skills/b/SKILL.md", "[x](../../../../docs/x.md)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


# --- #3412: `--removing` は消すファイルを指すリンクを、消す前に挙げる ---

GH = "https://github.com/devbasex/ai-plugins"
SHA40 = "0123456789abcdef0123456789abcdef01234567"


def run_removing(root: Path, *removing: str) -> subprocess.CompletedProcess:
    args = [arg for rel in removing for arg in ("--removing", rel)]
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root), *args],
        capture_output=True,
        text=True,
    )


def _removing_repo(root: Path) -> None:
    """消すファイル `issues/issue-9-design.md` と、それを指す文書を置く git の木。"""
    _git_repo(root)
    write(root, "issues/issue-9-design.md", "# 設計\n\n## 物差し\n")


def test_removing_lists_relative_and_mutable_github_links(tmp_path: Path) -> None:
    """消すファイルへの `../issues/…` の相対リンクと、`blob/main/…` の GitHub のリンクを挙げる。"""
    _removing_repo(tmp_path)
    write(
        tmp_path,
        "docs/a.md",
        "# a\n\n"
        "[設計](../issues/issue-9-design.md#物差し)\n"
        f"[GH]({GH}/blob/main/issues/issue-9-design.md#物差し)\n",
    )
    write(tmp_path, "issues/issue-10.md", "[同じ場所](issue-9-design.md)\n")
    write(tmp_path, "issues/old/x.md", f'<a href="{GH}/tree/v1.0/issues/issue-9-design.md">x</a>\n')
    result = run_removing(tmp_path, "issues/issue-9-design.md")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [
        "docs/a.md:3: (a) ../issues/issue-9-design.md#物差し",
        f"docs/a.md:4: (b) {GH}/blob/main/issues/issue-9-design.md#物差し",
        "issues/issue-10.md:1: (a) issue-9-design.md",
        f"issues/old/x.md:1: (b) {GH}/tree/v1.0/issues/issue-9-design.md",
    ]


def test_removing_leaves_pinned_sha_and_code_and_lists_quote(tmp_path: Path) -> None:
    """40 桁の SHA のリンクとコードの書式の中のパスは挙げない。引用の中のリンクは挙げる。"""
    _removing_repo(tmp_path)
    write(
        tmp_path,
        "docs/a.md",
        f"[固定]({GH}/blob/{SHA40}/issues/issue-9-design.md)\n\n"
        "`[コード](../issues/issue-9-design.md)`\n\n"
        "```\n[囲み](../issues/issue-9-design.md)\n```\n\n"
        f"> [引用]({GH}/blob/develop/issues/issue-9-design.md)\n",
    )
    result = run_removing(tmp_path, "issues/issue-9-design.md")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [f"docs/a.md:9: (b) {GH}/blob/develop/issues/issue-9-design.md"]


def test_removing_short_sha_and_branch_with_slash_are_mutable(tmp_path: Path) -> None:
    """短い SHA と `/` を含むブランチ名は可変の ref として挙げる。別のリポジトリのリンクは挙げない。"""
    _removing_repo(tmp_path)
    write(
        tmp_path,
        "docs/a.md",
        f"[短い]({GH}/blob/0123abc/issues/issue-9-design.md)\n"
        f"[枝]({GH}/raw/sprint/x-r1/issues/issue-9-design.md)\n"
        "[他所](https://github.com/other/repo/blob/main/issues/issue-9-design.md)\n",
    )
    result = run_removing(tmp_path, "issues/issue-9-design.md")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [
        f"docs/a.md:1: (b) {GH}/blob/0123abc/issues/issue-9-design.md",
        f"docs/a.md:2: (b) {GH}/raw/sprint/x-r1/issues/issue-9-design.md",
    ]


def test_removing_skips_removed_documents_and_passes_without_hits(tmp_path: Path) -> None:
    """消すファイルどうしのリンクは読まない。当たりが無ければ終了コード 0 で、壊れたリンクは見ない。"""
    _removing_repo(tmp_path)
    write(tmp_path, "issues/issue-9-plan.md", "[設計](issue-9-design.md)\n")
    write(tmp_path, "docs/a.md", "[壊れた](missing.md) [別](../issues/issue-10.md)\n")
    result = run_removing(tmp_path, "issues/issue-9-design.md", "issues/issue-9-plan.md")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["No links to the removed files"]


def test_removing_reads_documents_outside_default_scan_targets(tmp_path: Path) -> None:
    """既定の走査の範囲（docs・plugins・issues など）の外の文書も読む。消したファイルは作業ツリーに無くてよい。"""
    _git_repo(tmp_path)
    write(tmp_path, "scripts/README.md", "[設計](../issues/issue-9-design.md)\n")
    result = run_removing(tmp_path, "issues/issue-9-design.md")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == ["scripts/README.md:1: (a) ../issues/issue-9-design.md"]
