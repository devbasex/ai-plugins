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


def test_heading_with_angle_brackets_in_inline_code_resolves(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/a.md",
        "### `<worktree-base>` の解決順\n\n[飛ぶ](#worktree-base-の解決順)\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


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
    """決定 2: 検査の対象外の文書（`issues/`）は、見出しを読みに行かない。"""
    write(tmp_path, "issues/plan.md", "# 在る見出し\n")
    write(tmp_path, "docs/a.md", "[飛ぶ](../issues/plan.md#無い見出し)\n")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr


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
