# Devin MCP

リポジトリのドキュメントとコード知識にアクセスするためのMCPサーバープラグインです。

## 概要

このプラグインは、[Devin MCP](https://mcp.devin.ai/mcp)サーバーを利用して、GitHubなどのリポジトリのドキュメント構造や内容をAIエージェントから直接参照できるようにします。Devin APIキー（`DEVIN_API_KEY`）が必要です。

## 機能

- **`read_wiki_structure`** - リポジトリのドキュメントトピック一覧を取得
- **`read_wiki_contents`** - ドキュメントの内容を閲覧
- **`ask_question`** - リポジトリに関するAI質問回答

## 前提条件

- Devin APIキー（環境変数 `DEVIN_API_KEY`）

## 環境変数の設定

プロジェクトルートの`.env`ファイルに以下を設定してください：

```bash
# Devin API Key
DEVIN_API_KEY=your-devin-api-key
```

## インストール

```bash
/plugin install mcp-devin@ai-plugins
```

## 使用方法

### 基本的な使用例

```bash
# リポジトリのドキュメント構造を取得
mcp__plugin_mcp-devin__devin__read_wiki_structure "owner/repo"

# ドキュメント内容を読み取り
mcp__plugin_mcp-devin__devin__read_wiki_contents "owner/repo" "topic"

# リポジトリについて質問
mcp__plugin_mcp-devin__devin__ask_question "owner/repo" "How does authentication work?"
```

### CLIで直接追加する場合

```bash
claude mcp add -s user -t http devin https://mcp.devin.ai/mcp --header "Authorization: Bearer $DEVIN_API_KEY"
```

## 推奨される使用シーン

- OSSライブラリの仕様・アーキテクチャ調査
- 依存パッケージの内部実装の理解
- リポジトリのドキュメント横断検索
- 技術選定時のコード比較調査

## NDFエージェントとの連携

DeepWiki MCPは、NDFプラグインの`ndf:researcher`エージェントと連携して使用することを推奨します。

```bash
Task(
  subagent_type="ndf:researcher",
  prompt="DeepWikiを使ってlangchain/langchainリポジトリのRAG実装を調査してください",
  description="Research RAG implementation"
)
```

## 注意事項

- `DEVIN_API_KEY` の設定が必要です
- HTTPトランスポートを使用しています（Devin公式MCPサーバー `https://mcp.devin.ai/mcp`）
- `Authorization: Bearer {DEVIN_API_KEY}` ヘッダーで認証します
- レート制限がある場合があります

## 参考リンク

- [DeepWiki](https://deepwiki.com)
- [DeepWiki MCP ドキュメント](https://docs.devin.ai/ja/work-with-devin/deepwiki-mcp)
