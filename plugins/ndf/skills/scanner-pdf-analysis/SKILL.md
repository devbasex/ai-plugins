---
name: scanner-pdf-analysis
description: |
  Analyze PDF documents with table extraction, section identification, and content summarization. Use when reading technical documents, reports, or papers.

  This skill provides PDF analysis capabilities:
  - Text extraction and OCR
  - Table detection and CSV conversion
  - Section and heading identification
  - Key points summarization

  Triggers: "analyze PDF", "extract tables", "summarize document", "read PDF", "PDF解析", "テーブル抽出", "ドキュメント要約"
allowed-tools:
  - Bash
  - Write
---

# Scanner PDF Analysis Skill

## 概要

scannerエージェントがPDFドキュメントを分析し、構造化されたデータを抽出する際に使用します。テーブル抽出、セクション識別、要約生成などの機能を提供します。

## ツール優先順位

1. **MarkItDown MCP（最優先）** - `mcp-markitdown@ai-plugins` プラグイン
2. **Python スクリプト（フォールバック）** - MarkItDown MCPが利用できない場合

## クイックリファレンス

### 方法1: MarkItDown MCP（推奨）

```bash
# ローカルPDFをMarkdownに変換
mcp__plugin_mcp-markitdown_markitdown__convert_to_markdown uri="file:///path/to/report.pdf"

# URLからPDFを変換
mcp__plugin_mcp-markitdown_markitdown__convert_to_markdown uri="https://example.com/report.pdf"
```

MarkItDown MCPはPDFのテキスト・テーブルをMarkdownに変換します。追加ライブラリのインストールは不要です。

### 方法2: Python スクリプト（フォールバック）

MarkItDown MCPが利用できない場合や、テーブル個別抽出など高度な処理が必要な場合に使用します。

```bash
# 基本的な分析
python scripts/analyze-pdf.py report.pdf

# テーブル抽出 + 要約
python scripts/analyze-pdf.py report.pdf --extract-tables --summarize

# 出力ファイル指定
python scripts/analyze-pdf.py report.pdf --output=analysis-result.md
```

**必要なライブラリ**:
```bash
pip install PyPDF2 tabula-py pdfplumber
```

### 出力形式

```markdown
# report.pdf 分析結果

## 概要
- ページ数: 25
- テーブル数: 3

## 重要ポイント
1. [ポイント1]
2. [ポイント2]

## 抽出テーブル
[テーブルデータ]
```

## ベストプラクティス

| DO | DON'T |
|----|-------|
| 高品質なPDF（テキストベース） | スキャンPDFに直接適用 |
| ページ範囲指定（必要な部分のみ） | 複雑なレイアウト |
| テーブル抽出結果を検証 | 暗号化PDF |
| OCR使用（画像ベースPDF） | 大量ページの一括処理 |

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-usage-guide.md` | スクリプト詳細、ライブラリの使い分け |
| `02-examples.md` | 技術仕様書、論文、請求書の解析例 |

## 関連Skill

- **scanner-excel-extraction**: Excelファイル解析
- **data-analyst-export**: 抽出データのエクスポート
