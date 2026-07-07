---
name: redash-remove
description: 指定 suffix の Redash MCP を project .mcp.json から削除する
disable-model-invocation: true
user-invocable: true
arguments:
  - name: suffix
    description: "削除する環境識別子（dev, stg, prod2, sandbox など）"
allowed-tools:
  - Bash
---

# /redash-remove

指定 suffix の Redash MCP をプロジェクトの `.mcp.json` から削除します。

## 実行方法

以下のコマンドを実行してください。`$ARGUMENTS` にはユーザーが指定した suffix が入ります。

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/redash-mcp-config.js" remove $ARGUMENTS
```

## 実行後

コマンドの出力をそのままユーザーに表示してください。
