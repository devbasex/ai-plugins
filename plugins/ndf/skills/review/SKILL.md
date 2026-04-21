---
name: review
description: "PRを専門家としてレビューし、Approve/Request Changesを判定する"
argument-hint: "[PR番号]"
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# PRレビューコマンド

直前PR、または引数で指定されたPRを専門家としてレビュー。

## 実行

- 問題点・改善点あり → 「Request Changes」
- 指摘なし → 「Approve」

## 観点

言語慣用性（Idiomatic）・可読性・コード品質・保守性・セキュリティ・テストカバレッジ
- 上から順に優先して指摘

## 作業完了報告（必須）

以下を報告:
- レビューサマリー（PRタイトル、レビュー結果、変更の種類）
- 良い点
- 指摘事項（カテゴリ、重要度、ファイル、指摘内容、推奨対応）
- レビュー観点別評価（言語慣用性、可読性、コード品質、保守性、セキュリティ、テストカバレッジ）
- 統計（指摘問題数、重要度別内訳）
- PR URL
