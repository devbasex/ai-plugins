# mcp-serena

Serena MCP サーバ（`serena-agent==2.1.0`）を Claude Code・Codex で使えるようにするプラグインです。
シンボル単位の読み書き（定義・参照・シンボル単位の置換）を、Serena の REPL から行います。

## 使い方

Serena 2.1.0 の既定のインタフェースは REPL で、MCP のツールは 2 つだけです。

1. **コードの作業の前に `initial_instructions` を呼ぶ。** 手引きと、`serena_repl` に渡す `session` の ID が返る
2. **`serena_repl` に Python を渡して操作する**（例 `s.lsp.find_symbol("Foo", depth=1)`）。使い方は `s.info("lsp")` で引く

詳細は [docs/serena-guide.md](docs/serena-guide.md) にあります。

## インストール

```bash
# Claude Code
/plugin install mcp-serena@ai-plugins

# Codex
codex plugin add mcp-serena@ai-plugins
```

**Codex では、プラグインの hook は信頼するまで動きません。** 対話の Codex で `/hooks` を開き、mcp-serena の hook を信頼してください。

前提は `uv`（`uvx`）です。Serena と hook はどちらも `uvx --from serena-agent==2.1.0` で起動します。
言語サーバの設定（`.serena/project.yml`）は Serena がプロジェクトごとに自動で作ります。

### 更新

```bash
# Claude Code
/plugin marketplace update ai-plugins
/plugin update mcp-serena@ai-plugins

# Codex
codex plugin marketplace upgrade ai-plugins
codex plugin add mcp-serena@ai-plugins
```

## 起動の定義

| ランタイム | 定義 | 文脈 |
| --- | --- | --- |
| Claude Code | `.mcp.json` | `claude-code` |
| Codex | `.codex.mcp.json` | `codex` |

どちらも `--project-from-cwd` で起動したディレクトリのプロジェクトを有効にし、`no-memories`・`no-onboarding` で動きます。

## hook

Serena 公式の `serena-hooks` だけを、[公式の手引き](https://oraios.github.io/serena/02-usage/030_clients.html)どおりに置きます。

| ランタイム | イベント | コマンド |
| --- | --- | --- |
| Claude Code | SessionStart | `activate`（プロジェクトの有効化と手引きを読むよう促す） |
| Claude Code | PreToolUse（全ツール） | `remind`（grep・読み込みが続くと 1 度止めて Serena を促す。Serena を呼ぶと数が戻る） |
| Claude Code | SessionEnd | `cleanup`（セッションの記録を消す） |
| Codex | SessionStart（`startup\|resume`） | `activate` |
| Codex | PreToolUse（`Bash`） | `remind` |

**Serena の呼び出しを自動で許可する hook（公式の `auto-approve`）は置きません。** REPL からは
`s.shell.execute_shell_command` でシェルのコマンドを実行できるため、自動で許可すると Claude Code の
Bash の許可と NDF の Bash の guard を通らずにコマンドが走ります。

## 公式の `serena` プラグインと併用しない

公式マーケットプレイスにも `serena` があります。両方を入れると Serena が 2 つ起動するため、どちらか一方にしてください。

## 注意事項

- `serena_repl` は Serena の公式で BETA です
- Serena の memory は使いません。知識は `docs/` に、手順は `skills/` に置いてください

## 更新情報

版ごとの変更と移行の手順は [CHANGELOG.md](https://github.com/devbasex/ai-plugins/blob/main/CHANGELOG.md) の `[mcp-serena <版>]` の節にあります。
