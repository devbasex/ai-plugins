# AWS Documentation MCP

AWS公式ドキュメントにアクセスするためのMCPサーバープラグインです。

## 概要

このプラグインは、AWS公式ドキュメントを検索・取得する機能を提供します。

## 機能

- AWS公式ドキュメントの検索
- ドキュメントページの取得
- 関連ドキュメントの推薦
- サービス別ドキュメントへのアクセス

## v2.0.0 へ更新するとき

配布ディレクトリが `plugins/mcp/{shared,claude,codex,kiro}/mcp-aws-docs/` から
`plugins/mcp/mcp-aws-docs/` へ変わりました。マーケットプレイスの参照先が変わるため、**導入済みの
環境では再インストールが要ります**。Kiro CLI の installer は `dev.kiro/install.sh` へ移りました。

MCP サーバの定義（`.mcp.json`）の内容は変えていません。

## インストール

### Claude Code

```bash
/plugin install mcp-aws-docs@ai-plugins
```

### Codex

```bash
codex plugin add mcp-aws-docs@ai-plugins
```

### Kiro CLI

Kiro では repository clone 後、対象 plugin の installer を project root で実行します。

```bash
bash plugins/mcp/mcp-aws-docs/dev.kiro/install.sh
```

### 必要な環境変数

- 追加の環境変数は不要です。

## 使用方法

### 基本的な使用例

```bash
# AWS Lambdaのドキュメントを検索
mcp__plugin_aws-docs-mcp__awslabs.aws-docs__search_documentation "AWS Lambda best practices"

# 特定のドキュメントページを読み込み
mcp__plugin_aws-docs-mcp__awslabs.aws-docs__read_documentation "https://docs.aws.amazon.com/lambda/..."
```

## 推奨される使用シーン

- AWSサービスの調査
- ベストプラクティスの確認
- API仕様の参照
- トラブルシューティング

## ndf:researcherエージェントとの連携

AWS Docs MCPは、NDFプラグインの`ndf:researcher`エージェントと連携して使用することを推奨します。

```bash
# researcherエージェントにAWSドキュメント調査を依頼
Task(
  subagent_type="ndf:researcher",
  prompt="Research AWS Lambda best practices for performance",
  description="Research Lambda practices"
)
```

## 参考リンク

- [aws-documentation-mcp-server](https://pypi.org/project/awslabs.aws-documentation-mcp-server/)
- [AWS Documentation](https://docs.aws.amazon.com/)
