---
name: redash-guide
description: Redash MCP プラグインの使い方ガイド
disable-model-invocation: false
user-invocable: true
allowed-tools: []
---

# Redash MCP プラグイン ガイド

## 基本設定（1環境のみ）

Redash を1つだけ使う場合は、プロジェクトの `.env` に以下を設定するだけです。

```bash
REDASH_URL=https://redash.example.com
REDASH_API_KEY=your-api-key
```

プラグインインストール後、`/mcp` に `redash` が自動的に表示されます。

## 複数環境の追加

dev / stg / prod など複数の Redash を使う場合:

### 1. 環境を追加

```
/redash-add dev
/redash-add stg
```

### 2. 環境変数を設定

`.env` に追加分の変数を設定します。

```bash
# デフォルト
REDASH_URL=https://redash.example.com
REDASH_API_KEY=your-api-key

# dev 環境
REDASH_DEV_URL=https://redash-dev.example.com
REDASH_DEV_API_KEY=your-dev-api-key

# stg 環境
REDASH_STG_URL=https://redash-stg.example.com
REDASH_STG_API_KEY=your-stg-api-key
```

### 3. 確認

```
/redash-list     # 有効な MCP 一覧
/redash-status   # 環境変数の設定状況
```

## suffix 命名ルール

| suffix | MCP 名 | 環境変数 |
|--------|--------|----------|
| dev | redash-dev | REDASH_DEV_URL, REDASH_DEV_API_KEY |
| stg | redash-stg | REDASH_STG_URL, REDASH_STG_API_KEY |
| prod2 | redash-prod2 | REDASH_PROD2_URL, REDASH_PROD2_API_KEY |
| sandbox | redash-sandbox | REDASH_SANDBOX_URL, REDASH_SANDBOX_API_KEY |

- suffix は英数字で自由に指定可能
- `default` は予約語のため使用不可（plugin 同梱の `redash` が該当）

## 環境の削除

不要になった環境は削除できます。

```
/redash-remove dev
```

plugin 同梱の `redash` は削除できません。

## スラッシュコマンド一覧

| コマンド | 説明 |
|---------|------|
| `/redash-add <suffix>` | Redash MCP を追加 |
| `/redash-remove <suffix>` | Redash MCP を削除 |
| `/redash-list` | 有効な MCP 一覧 |
| `/redash-status` | 設定状況の詳細確認 |
