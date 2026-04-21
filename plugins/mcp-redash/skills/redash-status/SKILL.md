---
name: redash-status
description: Redash MCP の設定状況と環境変数の確認
disable-model-invocation: true
user-invocable: true
allowed-tools:
  - Bash
---

# /redash-status

Redash MCP の設定状況を詳細表示します。各 MCP が必要とする環境変数と、未設定の警告を確認できます。

## 実行方法

以下のコマンドを実行してください。

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/redash-mcp-config.js" status
```

## 実行後

コマンドの出力をそのままユーザーに表示してください。
