---
name: researcher-report-templates
description: |
  Generate structured research reports with findings, comparisons, recommendations, and citations. Use when documenting investigation results or creating technical comparisons.

  This skill provides comprehensive research report templates:
  - Investigation findings with proper structure
  - Technology comparison tables
  - Best practices summaries
  - Citation and reference management

  Triggers: "create report", "summarize findings", "compare technologies", "research report", "調査レポート", "技術比較", "ベストプラクティス"
allowed-tools:
  - Read
  - Write
---

# Researcher Report Templates Skill

## 概要

researcherエージェントが調査結果を構造化されたレポートにまとめる際に使用します。調査レポート、技術比較、ベストプラクティス文書などのテンプレートを提供します。

## クイックリファレンス

### テンプレート構造

**調査レポート**:
1. エグゼクティブサマリー（3-5行）
2. 調査目的・方法
3. 調査結果
4. 技術比較（表形式）
5. 推奨事項
6. 参考リンク
7. 結論

**技術比較**:
1. 概要
2. 比較表（項目別）
3. 詳細分析（長所・短所）
4. 推奨と理由
5. 移行計画

### 基本的な使い方

```bash
node scripts/generate-report.js research-notes.md
```

## ベストプラクティス

| DO | DON'T |
|----|-------|
| 構造化された形式を使用 | 主観的な評価 |
| 客観的な事実に基づく | 情報源の記載漏れ |
| 引用元を明記 | 冗長な説明 |
| 比較表で視覚化 | 不完全な比較 |
| エグゼクティブサマリーを最初に | 結論を最後まで隠す |

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-templates.md` | 調査レポート、技術比較、ベストプラクティスのテンプレート |
| `02-examples.md` | AWS Lambda調査、データベース選定の実例 |

## 関連リソース

- **scripts/generate-report.js**: レポート生成スクリプト
