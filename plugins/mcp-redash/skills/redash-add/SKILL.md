---
name: redash-add
description: 任意 suffix の Redash MCP を project .mcp.json に追加する
disable-model-invocation: true
user-invocable: true
arguments:
  - name: suffix
    description: "環境識別子（dev, stg, prod2, sandbox など）"
allowed-tools:
  - Bash
---

# /redash-add

任意 suffix の Redash MCP をプロジェクトの `.mcp.json` に追加します。

## 実行方法

以下のコマンドを実行してください。`$ARGUMENTS` にはユーザーが指定した suffix が入ります。

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/redash-mcp-config.js" add $ARGUMENTS
```

## 実行後

コマンドの出力をそのままユーザーに表示してください。
