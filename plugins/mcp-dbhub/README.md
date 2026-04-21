# DBHub MCP

データベース操作とSQLクエリを実行するためのMCPサーバープラグインです。

## 概要

このプラグインは、MySQL、PostgreSQL、SQLiteなどの各種データベースへのアクセスを提供します。

## 機能

- データベース接続管理
- SQLクエリの実行
- テーブルスキーマの取得
- データベース一覧の取得
- 複数データベースへの同時接続（TOML設定）

## インストール

```bash
/plugin install mcp-dbhub@ai-plugins
```

## 設定

2つの設定方法があり、`dbhub.toml`が優先されます。

### 方法1: dbhub.toml（複数DB対応・推奨）

プロジェクトルートに `dbhub.toml` を配置すると自動検出されます。

```toml
# 単一データベース
[[sources]]
id = "mydb"
description = "本番 MySQL"
dsn = "mysql://user:password@db-host:3306/dbname"
```

```toml
# 複数データベース
[[sources]]
id = "production"
description = "本番 MySQL"
dsn = "mysql://user:password@prod-db:3306/myapp"

[[sources]]
id = "staging"
description = "ステージング MySQL"
dsn = "mysql://user:password@staging-db:3306/myapp_stg"
```

複数ソース定義時、ツール名に自動でサフィックスが付与されます（例: `execute_sql_production`, `execute_sql_staging`）。

#### SSH踏み台設定（TOML内）

ソースごとにSSH踏み台を定義できます。

```toml
[[sources]]
id = "production"
description = "本番 MySQL（踏み台経由）"
dsn = "mysql://user:password@db.internal:3306/myapp"
ssh_host = "bastion.example.com"
ssh_port = 22
ssh_user = "ubuntu"
ssh_key = "~/.ssh/id_rsa"
# ssh_passphrase = "鍵のパスフレーズ（暗号化されている場合）"
```

マルチホップSSH（多段踏み台）にも対応しています。

```toml
[[sources]]
id = "prod_multihop"
dsn = "postgres://user:password@10.0.1.100:5432/myapp"
ssh_host = "internal-server.company.com"
ssh_user = "deploy"
ssh_key = "~/.ssh/id_ed25519"
ssh_proxy_jump = "bastion.company.com,admin@jump2.internal:2222"
```

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `ssh_host` | Yes | 踏み台ホスト名 |
| `ssh_port` | No | ポート（デフォルト: 22） |
| `ssh_user` | Yes | SSHユーザー名 |
| `ssh_key` | ※ | 秘密鍵パス（`~`展開可） |
| `ssh_password` | ※ | パスワード認証用 |
| `ssh_passphrase` | No | 秘密鍵のパスフレーズ |
| `ssh_proxy_jump` | No | マルチホップ（カンマ区切り） |

※ `ssh_key` または `ssh_password` のいずれかが必須

### 方法2: .env（単一DB・簡易設定）

`dbhub.toml`が存在しない場合、`.env`の環境変数にフォールバックします。

```bash
# データベース接続文字列
DBHUB_DSN=mysql://user:password@db-host:3306/dbname

# SSH踏み台（必要な場合）
DBHUB_SSH_HOST=bastion.example.com
DBHUB_SSH_PORT=22
DBHUB_SSH_USER=ubuntu
DBHUB_SSH_KEY=~/.ssh/id_rsa
```

DSNのホスト名は踏み台から見た内部ホスト名を指定してください。

### 優先順位

1. `dbhub.toml`（プロジェクトルートに存在すれば自動読み込み）
2. `.env`の`DBHUB_DSN`環境変数（tomlが無い場合のフォールバック）

## 使用例

```bash
# SQLクエリを実行
mcp__plugin_mcp-dbhub_dbhub__execute_sql "SELECT * FROM users LIMIT 10"

# テーブル一覧を取得
mcp__plugin_mcp-dbhub_dbhub__search_objects --object_type table
```

## ndf:data-analystエージェントとの連携

DBHub MCPは、NDFプラグインの`ndf:data-analyst`エージェントと連携して使用することを推奨します。

```bash
Task(
  subagent_type="ndf:data-analyst",
  prompt="Analyze user activity in the local database",
  description="Analyze user activity"
)
```

## 注意事項

- `dbhub.toml`と`.env`は`.gitignore`に追加し、認証情報をコミットしないでください
- SSH踏み台経由の場合、DSNのホスト名は踏み台から見た内部ホスト名を指定します

## 参考リンク

- [takemi-ohama/dbhub](https://github.com/takemi-ohama/dbhub) - 現在使用中のフォーク版（SSH keep-alive対応）
- [@bytebase/dbhub](https://www.npmjs.com/package/@bytebase/dbhub) - 本家パッケージ
- [Bytebase](https://www.bytebase.com/)
