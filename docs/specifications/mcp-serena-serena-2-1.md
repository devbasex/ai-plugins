# mcp-serena: Serena 2.1.0 の REPL と公式の hook だけで使う

## 目的

**mcp-serena は Serena（`serena-agent==2.1.0`）を、Serena の最新の流儀のまま Claude Code と Codex で使えるようにする。**
自前のスクリプト・テスト・Skill は持たない。足すのは起動の定義と、公式の `serena-hooks` の配線と短い説明だけである。

利用者向けの使い方は [mcp-serena の README](../../plugins/mcp/mcp-serena/README.md) と
[serena-guide](../../plugins/mcp/mcp-serena/docs/serena-guide.md) が正である。この文書は決定と理由だけを持つ。

## 構成

| ファイル | 中身 |
| --- | --- |
| `.mcp.json` / `.codex.mcp.json` | `uvx --from serena-agent==2.1.0 serena start-mcp-server`。文脈は `claude-code` / `codex`、`--project-from-cwd`・`no-memories`・`no-onboarding` |
| `hooks/hooks.json`（Claude Code） | SessionStart → `activate`、PreToolUse（空の matcher）→ `remind`、SessionEnd → `cleanup` |
| `hooks/codex.json`（Codex） | SessionStart（`startup\|resume`、5 秒）→ `activate`、PreToolUse（`Bash`、5 秒）→ `remind` |
| `README.md` / `docs/serena-guide.md` | 使い方 |

hook のコマンドはどれも `uvx --from serena-agent==2.1.0 serena-hooks <コマンド> --client=<claude-code|codex>` で、
起動の定義と同じ版を指す。

## 決定と理由

### 1. 2.0 系の言語サーバの Skill・スクリプト・テストを捨てる

2.0 系の mcp-serena は、言語を検出して `.serena/project.yml` を書く Skill（`language-servers`）・`serena-lsp.py`・
自前の誘導の hook とそのテスト・自分の `pyproject.toml` と `uv.lock` を持っていた。2.1.0 は `project.yml` を自分で作り、
誘導の hook（`serena-hooks remind`）も同梱する。同じ役割を自前で持つと、Serena の版を上げるたびに食い違う。

### 2. 2.1.0 の既定のインタフェース（REPL）のまま使う

MCP のツールは `initial_instructions` と `serena_repl` の 2 つだけになる。`serena_repl` は `session` の引数が必須で、
その ID は `initial_instructions` の戻り値にしか無いため、コードの作業の前に `initial_instructions` を呼ぶ。
操作は `serena_repl` に Python を渡して facade（`cfg`・`fs`・`edit`・`shell`・`lsp`）から行い、使い方は `s.info(...)` で引く。
`serena_repl` は公式で BETA である。

### 3. Serena の呼び出しを自動で許可する hook を置かない

REPL からは `s.shell.execute_shell_command` でシェルのコマンドを実行できる（`claude-code` の文脈でも）。公式の
`auto-approve` を置くと、Claude Code の Bash の許可と NDF の Bash の guard を通らずにコマンドが走る。
`serena_repl` の呼び出しは Claude Code の通常の許可を通す。

### 4. Claude Code の `remind` は公式どおり空の matcher にする

`remind` は Serena のツール（名前に `serena` を含む）を見たときに数を戻す。`Grep|Read|Bash` に絞ると
`serena_repl` の呼び出しが届かず、Serena を使った後も数が戻らない。数えない Tool（Edit など）では何もしないため、
全 Tool に当てても振る舞いは変わらない。1 回の起動は約 0.1 秒である。

### 5. Codex は公式の最小の配線に `remind` だけを足す

Codex は SessionStart（`startup|resume`）の `activate` と、PreToolUse（`Bash`）の `remind` だけを置く。公式の完全な配線にある
`reset`（PostToolUse）・`plan-guard`・`plan-context`・`cleanup`（SessionEnd、Codex 0.145.0 以上）は置かない。
このため Codex では Serena を呼んでも `remind` の数は戻らず、止めた時点か一定の時間の後にだけ戻る。

## 確かめたこと

各コマンドに見本の JSON を標準入力で渡して実行した（2.1.0）。

| コマンド | 結果 |
| --- | --- |
| `activate --client=claude-code` / `--client=codex` | 終了コード 0。SessionStart の `additionalContext` に手引きを読むよう促す文 |
| `remind --client=claude-code`（`Grep` を 3 回） | 3 回目に `permissionDecision: deny` と `additionalContext`。終了コード 0 |
| `remind --client=claude-code`（`Read` 2 回 → `serena_repl` → `Read`・`cat`） | `serena_repl` で数が戻り、止めない |
| `remind --client=codex`（`Bash` の `rg` を 3 回） | 3 回目に deny（`additionalContext` は付かない） |
| `cleanup --client=claude-code` | `~/.serena/hook_data/<session_id>` を消す。終了コード 0 |

## 関連

- [Serena の公式の手引き（クライアントごとの設定）](https://oraios.github.io/serena/02-usage/030_clients.html)
- [mcp-serena の README](../../plugins/mcp/mcp-serena/README.md)
- [serena-guide](../../plugins/mcp/mcp-serena/docs/serena-guide.md)
