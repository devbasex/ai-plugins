---
name: redash-list
description: 現在有効な Redash MCP を一覧表示する
disable-model-invocation: true
user-invocable: true
allowed-tools:
  - Bash
---

# /redash-list

現在有効な Redash MCP の一覧を表示します。

## 実行方法

以下のコマンドを実行してください。

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/redash-mcp-config.js" list
```

## 実行後

コマンドの出力をそのままユーザーに表示してください。
