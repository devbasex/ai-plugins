# AWS Documentation MCP

AWS公式ドキュメントにアクセスするためのMCPサーバープラグインです。

## 概要

このプラグインは、AWS公式ドキュメントを検索・取得する機能を提供します。

## 機能

- AWS公式ドキュメントの検索
- ドキュメントページの取得
- 関連ドキュメントの推薦
- サービス別ドキュメントへのアクセス

## インストール

```bash
/plugin install mcp-aws-docs@ai-plugins
```

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
