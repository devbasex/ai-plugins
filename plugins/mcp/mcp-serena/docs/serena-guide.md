# Serena MCP ガイド

Serena（`serena-agent==2.1.0`）はシンボル単位でコードを読み書きするための MCP サーバです。
コード操作だけに使い、memory は使いません。

## ツールは 2 つ

2.1.0 の既定のインタフェースは REPL で、MCP のツールは次の 2 つだけです。

| ツール | 役割 |
| --- | --- |
| `initial_instructions` | 手引きを返す。`serena_repl` に渡す `session` の ID もここにしか無い |
| `serena_repl` | `session` と Python のコードを受けて実行する（公式で BETA） |

ツール名の接頭辞は Claude Code が `mcp__plugin_mcp-serena_serena__`、Codex が `mcp__serena__` です。

## 使う順序

1. **コードの作業の前に `initial_instructions` を呼ぶ。** 返った手引きを読み、`session` の ID を控える
2. **`serena_repl` に Python を渡す。** facade の `s` から操作する

```python
s.info("lsp")                       # 使い方を引く（cfg・fs・edit・shell・lsp）
s.lsp.find_symbol("Foo", depth=1)   # シンボルを探す
```

使えるメソッドと引数は版で変わるため、ここに写さず `s.info(...)` で引きます。

## facade

| facade | 用途 |
| --- | --- |
| `s.lsp` | シンボルの概要・検索・参照・実装・診断 |
| `s.edit` | 内容の置換と、シンボル単位・行単位の編集 |
| `s.fs` | ファイルの読み取り・作成・一覧・パターン検索 |
| `s.shell` | シェルのコマンドの実行（`execute_shell_command`） |
| `s.cfg` | 今の設定の表示 |

**`s.shell` はシェルのコマンドを実行できる。** このため、Serena の呼び出しを自動で許可する hook
（公式の `auto-approve`）は置きません。置くと Claude Code の Bash の許可と NDF の Bash の guard を通らずに
コマンドが走ります。`serena_repl` の呼び出しは、通常どおり Claude Code の許可を通ります。

## 誘導の hook

公式の `serena-hooks remind` が、grep やコードのファイルの読み込みが続くと 1 度だけ止めて Serena を
促します。止めた時点で数が戻るため、必要ならそのまま同じ操作を続けて構いません。Claude Code では
Serena を呼ぶと数が戻ります。
