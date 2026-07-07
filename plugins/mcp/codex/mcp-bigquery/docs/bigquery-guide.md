# BigQuery MCP ガイド

BigQuery MCPサーバーでGoogle BigQueryのデータ分析を行います。

## 環境変数（必須）

| 変数 | 説明 |
|------|------|
| `BIGQUERY_PROJECT` | GCPプロジェクトID |
| `BIGQUERY_LOCATION` | データセットのロケーション（例: `asia-northeast1`） |
| `BIGQUERY_DATASET` | デフォルトデータセット名 |
| `BIGQUERY_KEY_FILE` | サービスアカウントキーファイルのパス |

## 主要ツール

- `execute-query` - SQLクエリを実行し結果を取得
- `list-tables` - データセット内のテーブル一覧を取得
- `describe-table` - テーブルのスキーマ情報を取得

## 使用例

```
# テーブル一覧の取得
mcp__plugin_mcp-bigquery_bigquery__list-tables

# テーブルのスキーマ確認
mcp__plugin_mcp-bigquery_bigquery__describe-table table_name="users"

# SQLクエリ実行
mcp__plugin_mcp-bigquery_bigquery__execute-query query="SELECT * FROM users LIMIT 10"
```
