#!/usr/bin/env python3
"""
DeepWiki生データファイルを `# Page:` マーカーで機械的に分割し、
セクション番号prefix付きのファイル名でリネームして出力するスクリプト。

分割時に内容は一切変更しない（`# Page: {タイトル}` → `# {タイトル}` の置換のみ）。

使用方法:
  python split_pages.py /tmp/deepwiki_raw.md ./deepWiki/

  # 構造確認のみ（ファイル出力なし）
  python split_pages.py /tmp/deepwiki_raw.md ./deepWiki/ --dry-run

  # Wiki構造ファイルからセクション順序を指定
  python split_pages.py /tmp/deepwiki_raw.md ./deepWiki/ --structure /tmp/deepwiki_structure.md
"""

import argparse
import os
import re
import sys


def sanitize_filename(title: str) -> str:
    """タイトルをファイル名に変換する（スペース→アンダースコア、特殊文字除去）"""
    name = title.strip()
    name = name.replace(" ", "_")
    name = name.replace("/", "_")
    name = name.replace("\\", "_")
    # ファイル名として安全な文字のみ残す（英数字、日本語、アンダースコア、ハイフン、ドット）
    name = re.sub(r"[^\w\-.]", "", name, flags=re.UNICODE)
    return name


def parse_raw_file(filepath: str) -> list[tuple[str, str]]:
    """
    生データファイルを読み込み、`# Page:` マーカーで分割する。

    戻り値: [(タイトル, 内容), ...] のリスト
    内容の先頭行は `# Page: {タイトル}` → `# {タイトル}` に置換済み
    """
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    # `# Page: ` で始まる行を境界として分割
    pages = []
    current_title = None
    current_lines = []

    for line in raw.splitlines(keepends=True):
        if line.startswith("# Page: "):
            # 前のページを保存
            if current_title is not None:
                pages.append((current_title, "".join(current_lines)))
            # 新しいページ開始
            current_title = line[len("# Page: "):].strip()
            # `# Page: {タイトル}` → `# {タイトル}` に置換
            current_lines = [f"# {current_title}\n"]
        else:
            if current_title is not None:
                current_lines.append(line)

    # 最後のページを保存
    if current_title is not None:
        pages.append((current_title, "".join(current_lines)))

    return pages


def parse_structure_file(filepath: str) -> list[str]:
    """
    Wiki構造ファイルからページタイトルの順序リストを取得する。
    read_wiki_structure の出力を解析する。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    titles = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # 番号付きリスト、ハイフンリスト、または単純なタイトル行を検出
        # 例: "1. Overview", "- Overview", "  1.1 System Architecture"
        match = re.match(r"^[\d.\-\s]*\s*(.+)$", line)
        if match:
            title = match.group(1).strip()
            if title and not title.startswith("#") and not title.startswith("```"):
                titles.append(title)

    return titles


def strip_section_number(title: str) -> str:
    """タイトルからセクション番号部分を除去する（ファイル名生成用）"""
    # "1.1 System Architecture" → "System Architecture"
    # "2. API Reference" → "API Reference"
    # "Overview" → "Overview"（変更なし）
    stripped = re.sub(r"^\d+\.\d+\.?\s+", "", title)
    stripped = re.sub(r"^\d+\.?\s+", "", stripped)
    return stripped if stripped else title


def assign_prefixes(pages: list[tuple[str, str]], structure_titles: list[str] | None = None) -> list[tuple[str, str, str]]:
    """
    各ページにセクション番号prefixを割り当てる。

    structure_titles が指定されている場合、その順序に従う。
    未指定の場合、ページの出現順に連番を振る。

    戻り値: [(prefix, タイトル, 内容), ...] のリスト
    タイトルはセクション番号を除去済み（ファイル名生成用）
    """
    # ページタイトルからセクション構造を推定
    # DeepWikiの構造: トップレベルページ → サブセクションページの順
    result = []
    section_num = 0
    subsection_counts = {}

    if structure_titles:
        # 構造ファイルからの順序に基づいてprefix割り当て
        title_to_content = {title: content for title, content in pages}
        ordered_titles = []

        # 構造ファイルのタイトルとページタイトルをマッチング
        for struct_title in structure_titles:
            for page_title, _ in pages:
                if _titles_match(struct_title, page_title):
                    ordered_titles.append(page_title)
                    break

        # マッチしなかったページを末尾に追加
        matched = set(ordered_titles)
        for page_title, _ in pages:
            if page_title not in matched:
                ordered_titles.append(page_title)

        pages_ordered = [(t, title_to_content.get(t, "")) for t in ordered_titles if t in title_to_content]
    else:
        pages_ordered = pages

    # セクション構造の推定とprefix割り当て
    # DeepWikiの典型的な構造:
    #   - 最初のページはOverview（トップレベル）
    #   - その後はセクション番号付きのページが続く
    #   - サブセクションはメインセクションの直後に配置される
    prev_section = 0
    section_map = {}  # タイトル → (section, subsection)

    for title, content in pages_ordered:
        # タイトルからセクション番号を推定
        # パターン: "1.1 Title", "1.1. Title", "Section 1: Title" など
        subsection_match = re.match(r"^(\d+)\.(\d+)\.?\s", title)
        section_match = re.match(r"^(\d+)\.?\s", title)

        if subsection_match:
            sec = int(subsection_match.group(1))
            sub = int(subsection_match.group(2))
            prefix = f"{sec:02d}_{sub}_"
            prev_section = sec
        elif section_match:
            sec = int(section_match.group(1))
            prefix = f"{sec:02d}_"
            prev_section = sec
        else:
            # 番号なしのページ（Overviewなど）
            section_num += 1
            # 既に番号付きセクションが出現している場合、連番を続ける
            if prev_section > 0 and section_num <= prev_section:
                section_num = prev_section + 1
            prefix = f"{section_num:02d}_"
            prev_section = section_num

        # ファイル名用にセクション番号を除去したタイトルを使用
        clean_title = strip_section_number(title)
        result.append((prefix, clean_title, content))

    return result


def _titles_match(struct_title: str, page_title: str) -> bool:
    """構造ファイルのタイトルとページタイトルが一致するか判定する"""
    # 完全一致
    if struct_title == page_title:
        return True
    # 正規化して比較（スペース、ハイフン、アンダースコアの違いを無視）
    normalize = lambda s: re.sub(r"[\s\-_]+", " ", s.lower().strip())
    return normalize(struct_title) == normalize(page_title)


def main():
    parser = argparse.ArgumentParser(
        description="DeepWiki生データを # Page: マーカーでファイル分割する"
    )
    parser.add_argument(
        "input",
        help="入力ファイルパス（fetch_wiki.py の出力ファイル）",
    )
    parser.add_argument(
        "output_dir",
        help="出力先ディレクトリパス（例: ./deepWiki/）",
    )
    parser.add_argument(
        "--structure",
        default=None,
        help="Wiki構造ファイルパス（read_wiki_structure の出力、セクション順序の決定に使用）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ファイル出力せず、分割結果のみ表示する",
    )
    args = parser.parse_args()

    # 入力ファイル読み込み・分割
    if not os.path.exists(args.input):
        print(f"エラー: 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"入力ファイル読み込み中: {args.input}")
    pages = parse_raw_file(args.input)
    print(f"検出ページ数: {len(pages)}")

    if not pages:
        print("エラー: ページが検出されませんでした。入力ファイルに `# Page:` マーカーが含まれているか確認してください。", file=sys.stderr)
        sys.exit(1)

    # 構造ファイルがあればセクション順序を取得
    structure_titles = None
    if args.structure:
        if os.path.exists(args.structure):
            structure_titles = parse_structure_file(args.structure)
            print(f"構造ファイルから {len(structure_titles)} タイトルを読み込みました")
        else:
            print(f"警告: 構造ファイルが見つかりません: {args.structure}", file=sys.stderr)

    # prefix割り当て
    prefixed_pages = assign_prefixes(pages, structure_titles)

    # 出力先ディレクトリ作成
    if not args.dry_run:
        os.makedirs(args.output_dir, exist_ok=True)

    # ファイル出力
    print("\n--- 分割結果 ---")
    for prefix, title, content in prefixed_pages:
        filename = f"{prefix}{sanitize_filename(title)}.md"
        filepath = os.path.join(args.output_dir, filename)

        content_size = len(content.encode("utf-8"))
        line_count = content.count("\n")
        print(f"  {filename}  ({content_size:,} bytes, {line_count} lines)")

        if not args.dry_run:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

    print(f"\n合計: {len(prefixed_pages)} ファイル")
    if args.dry_run:
        print("（dry-runモード: ファイルは出力されていません）")
    else:
        print(f"出力先: {args.output_dir}")


if __name__ == "__main__":
    main()
