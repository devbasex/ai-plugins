# BigQuery MCP

Google が運用するリモートの BigQuery MCP サーバー（`https://bigquery.googleapis.com/mcp`）を
Claude Code・Codex・Kiro CLI から使うためのプラグインです。

## 概要

ローカルにサーバーを立てず、Google 公式のリモート MCP サーバーへ Streamable HTTP で接続します。
認証は gcloud にログインしている利用者の OAuth アクセストークンです。

## 機能

| ツール | 内容 |
|---|---|
| `list_dataset_ids` / `get_dataset_info` | データセットの一覧と詳細 |
| `list_table_ids` / `get_table_info` | テーブルの一覧とスキーマ |
| `execute_sql_readonly` | 読み取り専用の SQL（DML・DDL を拒否） |
| `execute_sql` | 書き込みを含む SQL |
| `get_job` / `get_query_results` / `cancel_job` | ジョブの状態・結果の取得・取り消し |

ツールはクエリの対象プロジェクト（`projectId`）を引数で受け取ります。

制限は次のとおりです（[公式の説明](https://docs.cloud.google.com/bigquery/docs/use-bigquery-mcp)）。

- クエリは既定で 3 分で取り消される
- 結果は 3,000 行まで
- Google Drive の外部テーブルは扱えない

大量のデータを取り出すときは `bq` CLI やクライアントライブラリを使ってください。

## 前提条件

1. gcloud CLI にユーザーとしてログインしている

   ```bash
   gcloud auth login
   ```

2. 対象プロジェクトで、自分のアカウントに次の IAM ロールがある

   | ロール | 用途 |
   |---|---|
   | `roles/mcp.toolUser` | MCP のツールを呼ぶ（`mcp.tools.call`） |
   | `roles/bigquery.jobUser` | クエリのジョブを作る |
   | `roles/bigquery.dataViewer` | テーブルを読む |

   `roles/mcp.toolUser` は BigQuery のロールに含まれません。無いと
   `User does not have mcp.tools.call permission` で拒否されます。

   ```bash
   gcloud projects add-iam-policy-binding <PROJECT_ID> \
     --member=user:<メールアドレス> --role=roles/mcp.toolUser
   ```

3. 対象プロジェクトで BigQuery API が有効（リモート MCP サーバーも一緒に有効になる）

## 書き込みの `execute_sql` を止める

**読み取りだけで使うなら、導入したら最初に `execute_sql` を止めてください。** 認証は利用者本人の
トークンなので、本人が Editor や Owner を持つプロジェクトではエージェントがテーブルを書き換え・
削除できます。止めても `execute_sql_readonly` は使えます。

| ランタイム | 設定の場所 | 書く内容 |
|---|---|---|
| Claude Code | `~/.claude/settings.json`（またはプロジェクトの `.claude/settings.json`） | `"permissions": {"deny": ["mcp__plugin_mcp-bigquery_bigquery__execute_sql", "mcp__bigquery__execute_sql"]}` |
| Codex | `~/.codex/config.toml` | 下の例 |
| Kiro CLI | `.kiro/agents/default.json` の `mcpServers.bigquery` | `"disabledTools": ["execute_sql"]`（`install.sh` を流し直したら書き直す） |

```toml
[plugins."mcp-bigquery@ai-plugins".mcp_servers.bigquery]
disabled_tools = ["execute_sql"]
```

Claude Code の 2 つ目の名前は、Kiro CLI の `install.sh` がプロジェクト直下の `.mcp.json` にも
`bigquery` を書くためです。同じプロジェクトで Claude Code を使うと、プロジェクトのサーバーとして
`mcp__bigquery__execute_sql` の名前で読まれ、1 つ目の名前の deny では止まりません。

どれもツールの一覧から `execute_sql` が消えます（Claude Code 2.1.295・Codex 0.160.0・
kiro-cli 2.24.1 で確認）。組織として止めるときは、IAM の deny ポリシーで `execute_sql` を禁じます。

## ランタイムごとの認証

アクセストークンは 1 時間で切れます。ランタイムごとに取り直し方が違います。

| ランタイム | 設定のキー | トークンの取り直し |
|---|---|---|
| Claude Code | `headersHelper` | 接続のたびに `gcloud auth print-access-token` を実行する（401/403 でも取り直す） |
| Codex | `http_headers_helper` | 同上 |
| Kiro CLI | `headers` の `${BIGQUERY_ACCESS_TOKEN}` | **自動では取り直さない**。起動前に環境変数へ入れる |

Kiro CLI では、起動の前に次を実行してください。1 時間たったら入れ直して再起動します。

```bash
export BIGQUERY_ACCESS_TOKEN=$(gcloud auth print-access-token)
```

Codex は `http_headers_helper` を、`HOME`・`PATH` など限られた環境変数だけを渡して実行します。
`CLOUDSDK_CONFIG` で gcloud の設定の場所を変えていると、helper の中の gcloud は既定の場所
（`~/.config/gcloud`）を見てしまい、`You do not currently have an active account selected.` で
失敗して接続できません。その場合は、既定の場所から設定が見えるようにします。

```bash
ln -s "$CLOUDSDK_CONFIG" ~/.config/gcloud   # ~/.config/gcloud がまだ無いとき
```

`headersHelper` と `http_headers_helper` は、使わないランタイムでは読み飛ばされます。

## リモート MCP へ移ったときの変更

v2 までのローカルサーバー（`mcp-server-bigquery`）をやめ、リモートの BigQuery MCP サーバーへ
切り替えました。**後方互換はありません。**

| 項目 | 以前 | 現在 |
|---|---|---|
| サーバー | `uvx mcp-server-bigquery`（stdio） | `https://bigquery.googleapis.com/mcp`（HTTP） |
| 認証 | サービスアカウントの鍵ファイル | gcloud にログインした利用者のトークン |
| 環境変数 | `BIGQUERY_PROJECT` `BIGQUERY_LOCATION` `BIGQUERY_DATASET` `BIGQUERY_KEY_FILE` | 不要（Kiro CLI のみ `BIGQUERY_ACCESS_TOKEN`） |
| ツール名 | `execute-query` `list-tables` `describe-table` | `execute_sql_readonly` `list_table_ids` `get_table_info` など |

v2 は依存する `mcp` パッケージの 2 系で起動できなくなっていました（`'Server' object has no
attribute 'list_tools'`）。

## インストール

### Claude Code

```bash
/plugin install mcp-bigquery@ai-plugins
```

### Codex

```bash
codex plugin add mcp-bigquery@ai-plugins
```

### Kiro CLI

Kiro では repository clone 後、対象 plugin の installer を project root で実行します。

```bash
bash plugins/mcp/mcp-bigquery/dev.kiro/install.sh
```

## 更新情報

版ごとの変更と移行の手順は [CHANGELOG.md](https://github.com/devbasex/ai-plugins/blob/main/CHANGELOG.md) の `[mcp-bigquery <版>]` の節にあります。

## 使用方法

```
# データセットの一覧
mcp__plugin_mcp-bigquery_bigquery__list_dataset_ids projectId="my-project"

# テーブルのスキーマ
mcp__plugin_mcp-bigquery_bigquery__get_table_info projectId="my-project" datasetId="sales" tableId="orders"

# 読み取り専用のクエリ
mcp__plugin_mcp-bigquery_bigquery__execute_sql_readonly projectId="my-project" query="SELECT COUNT(*) FROM `my-project.sales.orders`"
```

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

- [Use the BigQuery remote MCP server](https://docs.cloud.google.com/bigquery/docs/use-bigquery-mcp)
- [MCP Reference: bigquery.googleapis.com](https://docs.cloud.google.com/bigquery/docs/reference/mcp)
- [Authenticate to Google Cloud MCP servers](https://docs.cloud.google.com/mcp/authenticate-mcp)
