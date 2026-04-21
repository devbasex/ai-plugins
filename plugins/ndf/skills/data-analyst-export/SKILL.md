---
name: data-analyst-export
description: |
  Export query results to various formats (CSV, JSON, Excel, Markdown tables) with proper formatting and headers. Use when saving analysis results to files.

  This skill provides data export utilities for multiple formats:
  - CSV: Comma-separated with headers, customizable delimiters
  - JSON: Structured data with pretty-print option
  - Excel: Multiple sheets, cell formatting
  - Markdown: Tables for documentation

  Triggers: "export data", "save results", "output CSV", "output JSON", "output Excel", "データ出力", "結果保存", "エクスポート"
allowed-tools:
  - Write
  - Bash
---

# Data Analyst Export Skill

## 概要

data-analystエージェントがクエリ結果を様々な形式でエクスポートする際に使用します。CSV、JSON、Excel、Markdownテーブルなど、用途に応じた最適な形式で出力できます。

## クイックリファレンス

### 形式別コマンド

```bash
# CSV出力
node scripts/export-csv.js input.json output.csv

# JSON出力
node scripts/export-json.js input.csv output.json --pretty

# Excel出力
node scripts/export-excel.js input.json output.xlsx

# Markdownテーブル出力
node scripts/export-markdown.js input.json output.md
```

### 形式選択ガイド

| 形式 | 用途 |
|------|------|
| CSV | 単純なデータ、他システム連携 |
| JSON | API連携、構造化データ |
| Excel | 複雑なレポート、書式設定 |
| Markdown | ドキュメント埋め込み |

## ベストプラクティス

| DO | DON'T |
|----|-------|
| 適切な形式を選択 | 全データをメモリに展開 |
| ヘッダーを含める | Excel形式で巨大データ（100万行制限） |
| 大きなデータはストリーミング | 日付フォーマットの不統一 |
| UTF-8 BOMを付与（Excel用CSV） | 特殊文字のエスケープ忘れ |

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-formats.md` | 各形式のスクリプト詳細、オプション |
| `02-examples.md` | BigQuery結果のCSVエクスポート、Excelレポート作成の実例 |

## 関連リソース

- **scripts/export-csv.js**: CSV出力スクリプト
- **scripts/export-json.js**: JSON出力スクリプト
- **scripts/export-excel.js**: Excel出力スクリプト
- **scripts/export-markdown.js**: Markdownテーブル出力スクリプト
