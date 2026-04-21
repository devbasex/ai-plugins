# Progressive Disclosure（多段階読み込み）

## 設計思想

コンテキストウィンドウの効率的な利用のため、情報を段階的に読み込む設計。全情報を一度に読み込まず、必要な時に必要な分だけ読み込む。

## 3段階の読み込みフロー

| 段階 | 読み込み内容 | タイミング | トークン消費 |
|-----|------------|----------|------------|
| **第1段階** | `name` + `description`のみ | 常時（システムプロンプト） | 最小 |
| **第2段階** | `SKILL.md`全体 | スキル呼び出し時 | 中 |
| **第3段階** | 参照ファイル | Claudeが必要と判断時 | 必要分のみ |

## ディレクトリ構成

### 推奨構成

```
my-skill/
├── SKILL.md              # メイン指示（500行以下推奨）
├── 01-quick-start.md     # クイックスタート
├── 02-detailed-guide.md  # 詳細ガイド
├── 03-api-reference.md   # APIリファレンス
├── 04-examples.md        # 使用例
└── scripts/
    └── helper.sh         # 実行用スクリプト（読み込まず実行）
```

### 順序prefixのルール

- `01-`, `02-`, `03-`... で読み込み順序を示す
- 番号順に重要度/基本度が高い
- Claudeは番号順に読み込みを検討

## SKILL.mdでの参照方法

### テーブル形式（推奨）

```markdown
## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-quick-start.md` | 基本的な使い方 |
| `02-detailed-guide.md` | 詳細な設定方法 |
| `03-api-reference.md` | API仕様 |
```

### リンク形式

```markdown
## 追加リソース

- 詳細は [01-quick-start.md](01-quick-start.md) を参照
- API仕様は [03-api-reference.md](03-api-reference.md) を参照
```

## 実装例

### SKILL.md（メインファイル）

```yaml
---
name: database-ops
description: |
  データベース操作のヘルパー。クエリ最適化、マイグレーション、バックアップを支援。

  Use when working with databases, writing SQL queries, running migrations, or managing backups.
allowed-tools:
  - Read
  - Bash(psql *)
---

# Database Operations

## 概要

データベース操作を支援するスキルです。

## クイックリファレンス

| 操作 | コマンド |
|-----|---------|
| 接続テスト | `psql -h host -U user -d db` |
| バックアップ | `pg_dump db > backup.sql` |

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-connection.md` | 接続設定 |
| `02-optimization.md` | クエリ最適化 |
| `03-migration.md` | マイグレーション |
```

### 01-connection.md（詳細ファイル）

```markdown
# 接続設定ガイド

## 環境変数

```bash
export PGHOST=localhost
export PGUSER=postgres
export PGDATABASE=mydb
```

## 接続文字列

```
postgresql://user:password@host:5432/database
```
...
```

## コンテキスト管理の注意点

### 上限

- description単体は**250文字**でtruncateされる
- 全スキルのdescription合計はコンテキストウィンドウの**1%**（フォールバック**8,000文字**）を超えると一部除外
- 環境変数`SLASH_COMMAND_TOOL_CHAR_BUDGET`で調整可能

### 確認方法

```bash
# 除外されたスキルを確認
/context
```

## ベストプラクティス

### DO（推奨）

- **SKILL.mdは500行以下**に保つ（公式推奨）
- **詳細は順序prefix付きファイル**に分割
- **テーブル形式で参照ファイルを明示**
- **クイックリファレンスをSKILL.mdに含める**

### DON'T（非推奨）

- **500行超のSKILL.md**（公式上限）
- **全情報を1ファイルに詰め込む**
- **参照ファイルへのリンクなし**
- **曖昧なファイル名（`details.md`等）**
