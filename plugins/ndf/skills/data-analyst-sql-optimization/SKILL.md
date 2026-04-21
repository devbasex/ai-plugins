---
name: data-analyst-sql-optimization
description: |
  Apply SQL optimization patterns including index usage, query rewriting, JOIN optimization, and window functions. Use when improving query performance or analyzing slow queries.

  This skill provides comprehensive SQL optimization patterns:
  - N+1 query elimination
  - Index optimization strategies
  - JOIN vs subquery performance
  - Window functions for complex aggregations

  Triggers: "optimize SQL", "slow query", "improve performance", "SQL最適化", "クエリ改善", "パフォーマンス向上"
---

# Data Analyst SQL Optimization Skill

## 概要

data-analystエージェントがSQLクエリのパフォーマンスを改善する際に使用します。実績のある最適化パターンとベストプラクティスを提供します。

## クイックリファレンス

### 最適化パターン一覧

| # | パターン | 問題 | 解決策 |
|---|---------|------|--------|
| 1 | N+1削減 | ループ内SQL | JOINで1回に統合 |
| 2 | インデックス | フルスキャン | WHERE/JOIN列にインデックス |
| 3 | JOIN最適化 | 不要な大規模JOIN | 必要な列のみ取得 |
| 4 | ウィンドウ関数 | 複雑なサブクエリ | ROW_NUMBER(), RANK()使用 |
| 5 | EXISTS vs IN | 遅いIN句 | EXISTSに変更 |
| 6 | LIMIT活用 | 全件取得 | SQLでページング |

### 基本的な使い方

1. 遅いクエリを特定
2. EXPLAINで実行計画を確認
3. 該当する最適化パターンを適用
4. 再度EXPLAINで改善を確認

## ベストプラクティス

| DO | DON'T |
|----|-------|
| EXPLAINで実行計画を確認 | 不要なDISTINCT |
| インデックスは選択的に作成 | 関数をWHERE句の列に適用 |
| 必要な列のみSELECT | 過剰なJOIN |
| 早期フィルタリング | サブクエリの多用 |
| 統計情報を更新 | インデックスの作り過ぎ |

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-patterns.md` | 各最適化パターンの詳細説明 |
| `02-examples.md` | Before/After実例集 |
