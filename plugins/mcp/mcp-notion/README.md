# Notion MCP

NotionワークスペースにアクセスするためのMCPサーバープラグインです。

## 概要

このプラグインは、Notionのページ、データベース、ブロックへのアクセスを提供します。

## 機能

- Notionページの作成・読み取り・更新
- データベースのクエリ
- ブロックの操作
- ページ階層の取得
- コメントの追加

## 前提条件

- Notionアカウント
- Notion Integration（内部統合）の作成
- 統合トークンの取得

## 環境変数の設定

プロジェクトルートに`.env`ファイルを作成し、以下の環境変数を設定してください：

```bash
# Notion Integration Token
NOTION_TOKEN=<your-notion-integration-token>
```

## v2.0.0 へ更新するとき

配布ディレクトリが `plugins/mcp/{shared,claude,codex,kiro}/mcp-notion/` から
`plugins/mcp/mcp-notion/` へ変わりました。マーケットプレイスの参照先が変わるため、**導入済みの
環境では再インストールが要ります**。Kiro CLI の installer は `dev.kiro/install.sh` へ移りました。

MCP サーバの定義（`.mcp.json`）の内容は変えていません。

## インストール

### Claude Code

```bash
/plugin install mcp-notion@ai-plugins
```

### Codex

```bash
codex plugin add mcp-notion@ai-plugins
```

### Kiro CLI

Kiro では repository clone 後、対象 plugin の installer を project root で実行します。

```bash
bash plugins/mcp/mcp-notion/dev.kiro/install.sh
```

### 必要な環境変数

- 追加の環境変数は不要です。

## 使用方法

### 基本的な使用例

```bash
# ページを読み取り
mcp__plugin_notion-mcp__notion__read_page "page-id"

# データベースをクエリ
mcp__plugin_notion-mcp__notion__query_database "database-id"

# 新しいページを作成
mcp__plugin_notion-mcp__notion__create_page "parent-page-id" "New Page Title"
```

## 推奨される使用シーン

- ドキュメント管理
- ナレッジベースの構築
- タスク管理
- プロジェクト情報の同期

## 注意事項

- Notion IntegrationをワークスペースやページにShareする必要があります
- HTTPトランスポートを使用しています（公式のNotion MCPサーバー）

## 参考リンク

- [Notion MCP Server](https://mcp.notion.com)
- [Notion API](https://developers.notion.com/)
- [Notion Integration Setup](https://www.notion.so/help/create-integrations-with-the-notion-api)
