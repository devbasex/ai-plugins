# DBHub MCP ガイド

DBHub MCPサーバーで各種データベースへのSQL操作を行います。

## 接続設定

DBHubは接続文字列（DSN）でデータベースに接続します。
環境変数またはツール引数で接続先を指定してください。

## 主要ツール

- `execute_sql_{suffix}` - 指定環境でSQLクエリを実行
- `search_objects_{suffix}` - データベースオブジェクト（テーブル、カラム等）を検索

suffixはインストール時の設定に依存します（例: `production`, `alpha`, `delta`）。

## 使用例

```
# テーブル・カラムの検索
mcp__plugin_mcp-dbhub_dbhub__search_objects_production keyword="users"

# SQLクエリ実行
mcp__plugin_mcp-dbhub_dbhub__execute_sql_production sql="SELECT * FROM users LIMIT 10"
```

## 注意事項

- 本番環境への書き込みクエリ（INSERT, UPDATE, DELETE）は慎重に実行してください
- 大量データの取得時はLIMIT句を使用してください
