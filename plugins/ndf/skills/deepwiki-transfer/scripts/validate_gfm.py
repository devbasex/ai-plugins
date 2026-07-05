#!/usr/bin/env python3
"""
Markdownファイルを GitHub Flavored Markdown (GFM) Spec に準拠するよう検証・修正するスクリプト。

主な修正:
  - 言語指定のないコードブロックに適切な言語を推定・付与
  - テーブルのパイプ区切りとアライメント行の修正
  - 見出し前後の空行挿入
  - 末尾改行の統一

使用方法:
  # 単一ファイルを検証・修正
  python validate_gfm.py ./deepWiki/01_Overview.md

  # ディレクトリ内の全 .md ファイルを一括処理
  python validate_gfm.py ./deepWiki/

  # 検証のみ（修正しない）
  python validate_gfm.py ./deepWiki/ --check-only

  # 修正内容の詳細表示
  python validate_gfm.py ./deepWiki/ --verbose
"""

import argparse
import glob
import os
import re
import sys


# コードブロックの言語推定パターン
LANGUAGE_PATTERNS = {
    "mermaid": [
        re.compile(r"^\s*(graph\s+(TD|TB|BT|RL|LR)|flowchart\s+(TD|TB|BT|RL|LR)|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|gitGraph|journey)", re.MULTILINE),
    ],
    "php": [
        re.compile(r"<\?php"),
        re.compile(r"\$this->"),
        re.compile(r"(function|class|namespace|use)\s+\w+"),
        re.compile(r"->(get|set|find|create|update|delete)\w*\("),
    ],
    "sql": [
        re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|FROM|WHERE|JOIN|GROUP BY|ORDER BY)\s", re.MULTILINE | re.IGNORECASE),
    ],
    "json": [
        re.compile(r'^\s*\{[\s\S]*"[^"]+"\s*:', re.MULTILINE),
    ],
    "yaml": [
        re.compile(r"^\w+:\s*$", re.MULTILINE),
        re.compile(r"^\s+-\s+\w+:", re.MULTILINE),
    ],
    "bash": [
        re.compile(r"^\s*(#!/bin/bash|#!/bin/sh|export\s|source\s|\$\(|apt-get|npm|pip|composer|docker|kubectl)", re.MULTILINE),
        re.compile(r"^\s*\$\s+\w+", re.MULTILINE),
    ],
    "javascript": [
        re.compile(r"(const|let|var|function|=>\s*\{|import\s+.*from|require\()"),
    ],
    "typescript": [
        re.compile(r"(interface\s+\w+|type\s+\w+\s*=|:\s*(string|number|boolean|any)\b)"),
    ],
    "html": [
        re.compile(r"<(!DOCTYPE|html|head|body|div|span|table|form)\b", re.IGNORECASE),
    ],
    "css": [
        re.compile(r"^\s*[.#@]\w+.*\{", re.MULTILINE),
        re.compile(r"(color|background|font-size|margin|padding|display)\s*:"),
    ],
    "python": [
        re.compile(r"(def\s+\w+|class\s+\w+|import\s+\w+|from\s+\w+\s+import)"),
    ],
    "xml": [
        re.compile(r"<\?xml\s"),
        re.compile(r"<\w+\s+xmlns[=:]"),
    ],
}


def detect_language(code_content: str) -> str | None:
    """コードブロックの内容から言語を推定する"""
    # スコアベースで最も適合する言語を選択
    scores = {}
    for lang, patterns in LANGUAGE_PATTERNS.items():
        score = 0
        for pattern in patterns:
            if pattern.search(code_content):
                score += 1
        if score > 0:
            scores[lang] = score

    if not scores:
        return None

    # 最高スコアの言語を返す
    return max(scores, key=scores.get)


def fix_code_blocks(content: str, verbose: bool = False) -> tuple[str, list[str]]:
    """コードブロックに適切な言語指定を付与する"""
    fixes = []
    lines = content.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # 言語指定ありのコードブロック開始 → 閉じフェンスまでスキップ
        if re.match(r"^```\w+", line):
            result.append(line)
            i += 1
            while i < len(lines) and not re.match(r"^```\s*$", lines[i]):
                result.append(lines[i])
                i += 1
            if i < len(lines):
                result.append(lines[i])  # 閉じフェンス
                i += 1
            continue

        # 言語指定なしのコードブロック開始を検出
        if re.match(r"^```\s*$", line):
            # コードブロックの内容を収集
            code_lines = []
            j = i + 1
            while j < len(lines) and not re.match(r"^```\s*$", lines[j]):
                code_lines.append(lines[j])
                j += 1

            code_content = "\n".join(code_lines)
            detected = detect_language(code_content)

            if detected:
                result.append(f"```{detected}")
                fixes.append(f"行{i+1}: ``` → ```{detected}")
                if verbose:
                    preview = code_content[:80].replace("\n", " ")
                    fixes[-1] += f"  (内容: {preview}...)"
            else:
                result.append(line)

            # コンテンツ行を追加
            for cl in code_lines:
                result.append(cl)

            # 閉じフェンスを追加してインデックスを進める
            if j < len(lines):
                result.append(lines[j])  # 閉じフェンス
                i = j + 1
            else:
                i = j
            continue

        result.append(line)
        i += 1

    return "\n".join(result), fixes


def fix_table_formatting(content: str) -> tuple[str, list[str]]:
    """テーブルのパイプ区切りとアライメント行を修正する"""
    fixes = []
    lines = content.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # テーブルヘッダー行の検出（パイプで区切られた行）
        if "|" in line and i + 1 < len(lines):
            # 次の行がアライメント行かチェック
            next_line = lines[i + 1]
            if re.match(r"^\s*\|[\s\-:|]+\|\s*$", next_line):
                # アライメント行の列数がヘッダーと一致するか確認
                header_cols = len([c for c in line.split("|") if c.strip()])
                sep_cols = len([c for c in next_line.split("|") if c.strip() or c == ""])

                # アライメント行のフォーマットを統一
                sep_parts = [p.strip() for p in next_line.split("|")]
                sep_parts = [p for p in sep_parts if p or p == ""]

                result.append(line)
            else:
                result.append(line)
        else:
            result.append(line)

        i += 1

    return "\n".join(result), fixes


def fix_heading_spacing(content: str) -> tuple[str, list[str]]:
    """見出し前後に適切な空行を挿入する"""
    fixes = []
    lines = content.split("\n")
    result = []

    for i, line in enumerate(lines):
        # 見出し行の検出（# で始まる行）
        if re.match(r"^#{1,6}\s+", line):
            # 前の行が空行でない場合、空行を挿入（ファイル先頭を除く）
            if result and result[-1].strip():
                result.append("")
                fixes.append(f"行{i+1}: 見出し前に空行を挿入")

        result.append(line)

        # 見出し行の次が空行でない場合、空行を挿入
        if re.match(r"^#{1,6}\s+", line) and i + 1 < len(lines):
            next_line = lines[i + 1]
            if next_line.strip() and not re.match(r"^#{1,6}\s+", next_line):
                # 次のループで追加されるので、ここでは何もしない
                pass

    return "\n".join(result), fixes


def fix_trailing_newline(content: str) -> tuple[str, list[str]]:
    """ファイル末尾の改行を統一する（1つの改行で終わる）"""
    fixes = []
    if not content.endswith("\n"):
        content += "\n"
        fixes.append("ファイル末尾に改行を追加")
    elif content.endswith("\n\n"):
        content = content.rstrip("\n") + "\n"
        fixes.append("ファイル末尾の余分な改行を削除")
    return content, fixes


def validate_and_fix(filepath: str, check_only: bool = False, verbose: bool = False) -> list[str]:
    """Markdownファイルを検証・修正する"""
    with open(filepath, "r", encoding="utf-8") as f:
        original = f.read()

    content = original
    all_fixes = []

    # 1. コードブロックの言語指定
    content, fixes = fix_code_blocks(content, verbose)
    all_fixes.extend(fixes)

    # 2. テーブルフォーマット
    content, fixes = fix_table_formatting(content)
    all_fixes.extend(fixes)

    # 3. 見出し前後の空行
    content, fixes = fix_heading_spacing(content)
    all_fixes.extend(fixes)

    # 4. 末尾改行
    content, fixes = fix_trailing_newline(content)
    all_fixes.extend(fixes)

    # 修正を適用
    if all_fixes and not check_only:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    return all_fixes


def main():
    parser = argparse.ArgumentParser(
        description="MarkdownファイルをGFM準拠に検証・修正する"
    )
    parser.add_argument(
        "path",
        help="対象ファイルまたはディレクトリ",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="検証のみ行い、ファイルを修正しない",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="修正内容の詳細を表示する",
    )
    args = parser.parse_args()

    # 対象ファイル一覧を取得
    if os.path.isfile(args.path):
        files = [args.path]
    elif os.path.isdir(args.path):
        files = sorted(glob.glob(os.path.join(args.path, "*.md")))
    else:
        print(f"エラー: パスが見つかりません: {args.path}", file=sys.stderr)
        sys.exit(1)

    if not files:
        print(f"対象の .md ファイルが見つかりません: {args.path}")
        sys.exit(0)

    total_fixes = 0
    files_with_fixes = 0

    for filepath in files:
        filename = os.path.basename(filepath)
        fixes = validate_and_fix(filepath, args.check_only, args.verbose)

        if fixes:
            files_with_fixes += 1
            total_fixes += len(fixes)
            status = "要修正" if args.check_only else "修正済"
            print(f"  [{status}] {filename}: {len(fixes)} 件")
            if args.verbose:
                for fix in fixes:
                    print(f"    - {fix}")
        else:
            if args.verbose:
                print(f"  [OK] {filename}")

    print(f"\n合計: {len(files)} ファイル, {files_with_fixes} ファイルに {total_fixes} 件の{'問題' if args.check_only else '修正'}")

    if args.check_only and total_fixes > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
