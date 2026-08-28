# AWS Docs MCP ガイド

AWS Documentation MCPサーバーでAWS公式ドキュメントを検索・参照します。

## 主要機能

- AWSサービスのドキュメント検索
- ベストプラクティスの参照
- API仕様の確認
- トラブルシューティング情報の取得

## 用途

- **researcherエージェント**: AWS公式ドキュメントの調査、ベストプラクティスの確認

## 使用例

```
# Lambda関数のベストプラクティスを検索
mcp__plugin_mcp-aws-docs_awslabs.aws-docs__search query="Lambda best practices"

# S3バケットポリシーのドキュメントを参照
mcp__plugin_mcp-aws-docs_awslabs.aws-docs__search query="S3 bucket policy"
```

## 注意事項

- 認証情報は不要（公開ドキュメントへのアクセス）
- 静的な公式ドキュメントの参照に特化。動的コンテンツにはChrome DevTools MCPを使用
