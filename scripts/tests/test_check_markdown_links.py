"""リンク検査の見出しへの参照の照合を固定する（#445）。

**名前の規則は GitHub の生成規則に合わせる。** 小文字にし、文字・数字・結合文字・空白・
`-`・`_` 以外を落とし、空白を `-` にし、重複へ `-1` から連番を付ける。規則と決定の理由は
`issues/issue-445-design.md` にある。

検査は一時ディレクトリへ作った木に対して別プロセスで実行する。スクリプト名が `-` を含み、
そのままでは import できないためである。
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-markdown-links.py"


def run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(CHECK), "--root", str(root)],
        capture_output=True, text=True,
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
        "`[文言](位置)` の書き方と `<a href=\"無い.md\">` の書き方\n"
        "``[x](無い.md) と ` を含む``\n",
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


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("a `b` c", "a   c"),
        ("a ``b ` c`` d", "a   d"),
        ("a ``b` c", "a ``b` c"),
        ("`a`[x](y.md)`b`", " [x](y.md) "),
        ("[x](y.md)", "[x](y.md)"),
    ],
)
def test_strip_inline_code(line: str, expected: str) -> None:
    """#543: インラインコードの範囲を空白 1 つへ置き換える。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.strip_inline_code(line) == expected


def test_reference_inside_code_fence_is_ignored(tmp_path: Path) -> None:
    write(tmp_path, "docs/a.md", "# 手順\n\n```markdown\n[飛ぶ](#無い見出し)\n```\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


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
    """決定 2: 検査の対象外の文書（`notes/`）は、見出しを読みに行かない。"""
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


def test_link_targets_extracts_html_and_excludes_images() -> None:
    """link_targets の現状を固定する（#445, R1-003）。

    標準の Markdown リンクに加え、ダブルクォート・シングルクォートの
    `<a href="...">` を抽出し、画像リンク記法 `![alt](...)` は除外する。
    抽出は Markdown リンクが先、インライン HTML が後の順に並ぶ。
    """
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = (
        "標準リンク [a](std.md) と "
        '<a href="dq.md">dq</a> と '
        "<a href='sq.md'>sq</a> と "
        "画像 ![alt](img.png) を含む\n"
    )
    targets = module.link_targets(text)

    assert "dq.md" in targets
    assert "sq.md" in targets
    assert "img.png" not in targets
    assert targets == ["std.md", "dq.md", "sq.md"]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("<path>", "path"),
        ('path "title"', "path"),
        ("path 'title'", "path"),
        ("path (title)", "path"),
        ('<path> "title"', "<path>"),
        ("  <path>  ", "path"),
        ('  path "title"  ', "path"),
    ],
)
def test_strip_title_current_behavior(target: str, expected: str) -> None:
    """strip_title の山括弧・タイトル除去の現状を固定する（R1-005）。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.strip_title(target) == expected


def test_should_skip() -> None:
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.should_skip("") is True
    assert module.should_skip("/path") is True
    assert module.should_skip("//domain/path") is True
    assert module.should_skip("mailto:user@example.com") is True
    assert module.should_skip("{repo}/blob/{branch}") is True
    assert module.should_skip("path/to/file.md") is False


def test_anchor_refs_empty_or_missing_fragment_skipped() -> None:
    """anchor_refs の空フラグメント等の境界値経路の現状を固定する（R2-001）。

    'file.md#' や '#' のように sep は存在するが fragment が空のリンク、
    および 'file.md' のように sep 自体が存在しないリンクはスキップされ空リストを返す。
    """
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.anchor_refs("[x](file.md#)\n") == []
    assert module.anchor_refs("[x](#)\n") == []
    assert module.anchor_refs("[x](file.md)\n") == []


def test_anchor_refs_extracts_same_and_relative_document_fragments() -> None:
    """同一文書と相対文書のアンカー抽出の現状を固定する（R1-001）。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = "[同一文書](#sec)\n[相対文書](doc.md#other-sec)\n"

    assert module.anchor_refs(text) == [
        ("", "sec", "#sec"),
        ("doc.md", "other-sec", "doc.md#other-sec"),
    ]


def test_anchor_refs_skips_external_and_absolute_path_fragments() -> None:
    """スキップ対象のパスを持つアンカー参照の現状を固定する（R1-001）。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = (
        "[外部](https://example.com/doc.md#sec)\n"
        "[絶対パス](/doc.md#sec)\n"
    )

    assert module.anchor_refs(text) == []


def test_heading_anchors_collision_with_explicit_numbered_heading() -> None:
    """heading_anchors の連番衝突解決の while ループ反復経路を固定する（R2-002）。

    自動付番される名前（`手順-1`）と同名の見出しが文書内に明示的に書かれているとき、
    後続の同名見出しは while ループが複数回実行され、次の空き連番（`手順-2`）まで
    探索して解決する。明示的な `## 手順-1` を先頭に置くことで、2 つ目の `## 手順` が
    `手順` → `手順-1`（衝突）→ `手順-2` と 2 回反復する経路を通す。
    """
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "a.md"
        path.write_text("## 手順-1\n\n## 手順\n\n## 手順\n", encoding="utf-8")
        anchors = module.heading_anchors(path)

    assert anchors == {"手順", "手順-1", "手順-2"}


def test_iter_markdown_files_collects_root_files_and_scan_dirs(tmp_path: Path) -> None:
    """iter_markdown_files の探索範囲の現状を固定する（R2-003）。

    ルート直下の所定ファイルは個別に、`docs/` と `plugins/` は配下を再帰で集める。
    `issues/` も配下を再帰で集める（#543）。
    所定外のルート直下ファイル・対象外ディレクトリ（`notes/`）・`.md` 以外は含めず、
    戻り値はソート済みで重複を持たない。
    """
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    write(tmp_path, "README.md", "# r\n")
    write(tmp_path, "OTHER.md", "# o\n")
    write(tmp_path, "docs/b.md", "# b\n")
    write(tmp_path, "docs/sub/a.md", "# a\n")
    write(tmp_path, "docs/note.txt", "n\n")
    write(tmp_path, "plugins/p/README.md", "# p\n")
    write(tmp_path, "issues/plan.md", "# i\n")
    write(tmp_path, "notes/plan.md", "# n\n")

    files = module.iter_markdown_files(tmp_path)

    assert files == [
        tmp_path / "README.md",
        tmp_path / "docs" / "b.md",
        tmp_path / "docs" / "sub" / "a.md",
        tmp_path / "issues" / "plan.md",
        tmp_path / "plugins" / "p" / "README.md",
    ]
    assert files == sorted(set(files))


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
    """slugify の空白展開・記号保持・記号除去の境界値規則を固定する（R2-005）。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.slugify(text) == expected


def test_target_path_current_behavior(tmp_path: Path) -> None:
    """target_path のリンク解決・スキップ判定の現状を固定する（R4-001）。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = tmp_path / "docs" / "index.md"

    # スキップ対象は None
    assert module.target_path(source, "") is None
    assert module.target_path(source, "https://example.com/foo.md") is None
    assert module.target_path(source, "/root/file.md") is None

    # 同一文書内アンカー（path_part が空）は None
    assert module.target_path(source, "#heading") is None
    assert module.target_path(source, "#") is None

    # 相対パス（フラグメント・タイトル・山括弧付き含む）は解決先 Path
    assert module.target_path(source, "other.md") == (tmp_path / "docs" / "other.md").resolve()
    assert module.target_path(source, "other.md#heading") == (tmp_path / "docs" / "other.md").resolve()
    assert module.target_path(source, "../readme.md") == (tmp_path / "readme.md").resolve()
    assert module.target_path(source, '<other.md>') == (tmp_path / "docs" / "other.md").resolve()
    assert module.target_path(source, 'other.md "title"') == (tmp_path / "docs" / "other.md").resolve()
    assert module.target_path(source, '<other.md> "title"') == (tmp_path / "docs" / "<other.md>").resolve()


def test_visible_lines_skips_code_fences_and_quotes(tmp_path: Path) -> None:
    """visible_lines のコードフェンスおよび引用行スキップの現状を固定する（R1-002）。"""
    spec = importlib.util.spec_from_file_location("check_markdown_links", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    path = tmp_path / "test.md"
    body = (
        "可視行 1\n"
        "```\n"
        "フェンス内の行\n"
        "```\n"
        "> 引用（> 始まり）行\n"
        "  > インデント後に > を持つ行\n"
        "可視行 2\n"
    )
    path.write_text(body, encoding="utf-8")

    result = module.visible_lines(path)

    assert result == ["可視行 1", "可視行 2"]


