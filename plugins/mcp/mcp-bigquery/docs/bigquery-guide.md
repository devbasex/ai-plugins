# BigQuery MCP ガイド

Google 公式のリモート BigQuery MCP サーバーで Google BigQuery のデータ分析を行います。

## 認証（必須）

- gcloud CLI にユーザーとしてログインしていること（`gcloud auth login`）
- 対象プロジェクトで `roles/mcp.toolUser`・`roles/bigquery.jobUser`・`roles/bigquery.dataViewer` があること
- Kiro CLI のみ、起動前に `BIGQUERY_ACCESS_TOKEN` へ `gcloud auth print-access-token` の値を入れる（1 時間で切れる）

## 主要ツール

- `execute_sql_readonly` - 読み取り専用の SQL を実行し結果を取得（結果は 3,000 行まで、3 分で打ち切り）
- `execute_sql` - 書き込みを含む SQL を実行
- `list_dataset_ids` / `list_table_ids` - データセット・テーブルの一覧を取得
- `get_dataset_info` / `get_table_info` - データセット・テーブルの情報（スキーマ）を取得

どのツールも `projectId` を引数で受け取ります。SQL のテーブル名は `project.dataset.table` の完全修飾名で書きます。

## 使用例

```
# テーブル一覧の取得
mcp__plugin_mcp-bigquery_bigquery__list_table_ids projectId="my-project" datasetId="sales"

# テーブルのスキーマ確認
mcp__plugin_mcp-bigquery_bigquery__get_table_info projectId="my-project" datasetId="sales" tableId="users"

# SQLクエリ実行
mcp__plugin_mcp-bigquery_bigquery__execute_sql_readonly projectId="my-project" query="SELECT * FROM `my-project.sales.users` LIMIT 10"
```
