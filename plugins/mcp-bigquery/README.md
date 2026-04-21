# BigQuery MCP

BigQuery MCPサーバーをClaude Codeで利用するためのプラグインです。

## 概要

このプラグインは、Google Cloud BigQueryへのアクセスを提供し、SQLクエリの実行やデータ分析を可能にします。

## 機能

- BigQueryテーブルの一覧取得
- SQLクエリの実行
- クエリ結果の取得
- テーブルスキーマの取得
- データセットの操作

## 前提条件

- Google Cloud Projectの作成
- BigQuery APIの有効化
- サービスアカウントキーの作成

## 環境変数の設定

プロジェクトルートに`.env`ファイルを作成し、以下の環境変数を設定してください：

```bash
# BigQuery Project ID
BIGQUERY_PROJECT=your-project-id

# BigQuery Location (例: asia-northeast1)
BIGQUERY_LOCATION=asia-northeast1

# 利用するデータセット（カンマ区切り）
BIGQUERY_DATASETS=dataset1,dataset2

# サービスアカウントキーファイルのパス
BIGQUERY_KEY_FILE=/path/to/service-account-key.json
```

## インストール

```bash
/plugin install mcp-bigquery@ai-plugins
```

## 使用方法

### 基本的な使用例

```bash
# テーブル一覧を取得
mcp__plugin_bigquery-mcp__bigquery__list_tables

# SQLクエリを実行
mcp__plugin_bigquery-mcp__bigquery__execute_query "SELECT * FROM dataset.table LIMIT 10"
```

## 推奨される使用シーン

- データ分析
- レポート生成
- SQLクエリの最適化
- データ抽出

## ndf:data-analystエージェントとの連携

BigQuery MCPは、NDFプラグインの`ndf:data-analyst`エージェントと連携して使用することを推奨します。

```bash
# data-analystエージェントにBigQueryクエリを依頼
Task(
  subagent_type="ndf:data-analyst",
  prompt="Analyze last month's sales data in BigQuery",
  description="Analyze sales data"
)
```

## 参考リンク

- [mcp-server-bigquery](https://pypi.org/project/mcp-server-bigquery/)
- [Google Cloud BigQuery](https://cloud.google.com/bigquery)
