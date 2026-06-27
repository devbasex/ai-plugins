---
name: review-branch
description: "現在ブランチをmainとの差分でレビューする。"
when_to_use: "PR作成前にローカルブランチの実装をセルフレビューしたいとき。Triggers: 'ブランチをレビュー', 'PR前にレビュー', 'セルフレビュー', 'review my branch', 'review before PR', 'self review', 'pre-PR review'"
argument-hint: "[focus-area] (例: security, performance, tests)"
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# ブランチ実装レビューコマンド

現在のブランチで実装された変更を**PR作成前に**コードレビューする。mainブランチとの差分を分析し、コード品質・セキュリティ・パフォーマンスの観点でフィードバックを返す。

## `/ndf:review` との使い分け

| 観点 | review-branch | review |
|---|---|---|
| 対象 | ローカルブランチの差分（PR前） | GitHub上の既存PR |
| 判定 | フィードバックを返す | Approve / Request Changes を判定 |
| 用途 | PR作成前のセルフレビュー | PR作成後のレビュー |

## 使用方法

```
/ndf:review-branch                     # 全般レビュー
/ndf:review-branch security            # セキュリティに焦点
/ndf:review-branch performance         # パフォーマンスに焦点
/ndf:review-branch tests               # テスト網羅性に焦点
/ndf:review-branch "ビジネスロジック"    # 任意のフォーカス
```

## レビュー手順

### 1. 変更の把握

```bash
git diff main --name-only          # 変更ファイル一覧
git diff main --stat               # 差分の統計
git log main..HEAD --oneline       # コミット履歴
```

### 2. 変更内容の分析

各変更ファイルに対して以下を確認:

- **追加・変更されたロジック**: 意図が明確か、正しく実装されているか
- **テストカバレッジ**: 適切なテストが追加されているか
- **コーディング規約**: プロジェクトの規約に準拠しているか

### 3. 品質チェック観点

#### コード品質
- 命名規則の一貫性
- 関数/メソッドの責務（単一責任原則）
- DRY原則（重複コードの排除）
- 可読性・保守性
- 過剰な抽象化がないか（YAGNI）

#### セキュリティ
- SQLインジェクション対策
- XSS対策
- CSRF対策
- 入力値バリデーション
- 認証・認可の適切性
- 機密情報（トークン、キー、PII）の取り扱い

#### パフォーマンス
- N+1 クエリの有無
- 不要なデータベースアクセス
- メモリ使用量
- インデックスの活用

#### エラーハンドリング
- 例外が適切に捕捉されているか
- ログ出力の妥当性（詳細は `/ndf:logging-guidelines`）
- リトライ/タイムアウトの設計

### 4. レビュー結果の報告

```markdown
## レビュー結果

### 概要
- 変更ファイル数: X
- 追加行数: +XXX
- 削除行数: -XXX

### Good（良い点）
- ...

### Suggestions（改善提案）
- `path/to/file.ext:123` — 提案内容

### Issues（要修正）
- `path/to/file.ext:456` — 問題点と修正方針
```

## 使用例

```bash
# 全般的なレビュー
/ndf:review-branch

# セキュリティ重視（認証系変更など）
/ndf:review-branch security

# N+1クエリ等のパフォーマンス問題に焦点
/ndf:review-branch performance

# テストの網羅性を確認
/ndf:review-branch tests
```

## 注意事項

- 大量の変更がある場合、重要な変更から優先的にレビューする
- 自動品質チェック（linter, formatter, type checker）は事前実行済みを前提とする
- レビュー結果は提案であり、最終判断は開発者が行う
- **コード修正は行わない**（分析とフィードバックのみ。修正は `/ndf:fix` で別途実行）

## 関連

- `/ndf:review` — PR単位レビュー (Approve/Request Changes判定)
- `/ndf:review-pr-comments` — 既存PRコメントの分類
- `/ndf:fix` — PRレビューコメントの修正対応
- `/ndf:logging-guidelines` — ログ設計
