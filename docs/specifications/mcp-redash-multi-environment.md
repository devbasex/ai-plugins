# Redash MCP マルチ環境 Plugin 仕様

## 概要

`mcp-redash` は、Redash MCP server を Claude Code / Codex / Kiro CLI から利用するための MCP plugin である。配布ディレクトリは 1 つで、3 runtime が同じ `.mcp.json` を読む。

単一環境では plugin 同梱の `redash` MCP server だけを使い、複数環境が必要な project では suffix 付きの `redash-*` MCP server を project `.mcp.json` に追加する。これにより、導入直後の MCP 一覧を最小に保ちながら、必要な Redash 環境だけを明示的に増やせる。

## 対象範囲

対象 runtime と配布先:

| runtime | 配布先 |
|---|---|
| Claude Code | `plugins/mcp/mcp-redash` |
| Codex | `plugins/mcp/mcp-redash` |
| Kiro CLI | `plugins/mcp/mcp-redash` |

配布ディレクトリは `plugins/mcp/mcp-redash` の 1 つとする。配布構造は [Runtime Plugin Distribution 仕様](runtime-plugin-distribution.md) に従う。

## 仕様

### Plugin 同梱 MCP

plugin は suffix なしの `redash` MCP server を 1 つだけ同梱する。`redash-dev`、`redash-stg` などの追加環境は plugin 同梱 `.mcp.json` には含めない。

Claude Code / shared / Kiro の `.mcp.json` は `mcpServers.redash` を定義する。Codex の `.mcp.json` は runtime 標準に合わせ、top-level の `redash` entry として定義する。

`redash` は次の環境変数を参照する。

| MCP 名 | URL | API key |
|---|---|---|
| `redash` | `REDASH_URL` | `REDASH_API_KEY` |

### 追加 MCP

追加環境は `/redash-add <suffix>` により project `.mcp.json` に追加する。suffix を `dev` とした場合、MCP 名は `redash-dev`、参照環境変数は `REDASH_DEV_URL` と `REDASH_DEV_API_KEY` になる。

suffix と名前の変換:

| suffix 例 | MCP 名 | URL | API key |
|---|---|---|---|
| `dev` | `redash-dev` | `REDASH_DEV_URL` | `REDASH_DEV_API_KEY` |
| `stg` | `redash-stg` | `REDASH_STG_URL` | `REDASH_STG_API_KEY` |
| `prod2` | `redash-prod2` | `REDASH_PROD2_URL` | `REDASH_PROD2_API_KEY` |
| `sandbox-1` | `redash-sandbox-1` | `REDASH_SANDBOX_1_URL` | `REDASH_SANDBOX_1_API_KEY` |

suffix は `^[a-z0-9][a-z0-9-]*$` に一致する英小文字、数字、ハイフンで指定する。入力 suffix は script 内で小文字化され、環境変数名ではハイフンをアンダースコアに変換する。`default` と空 suffix は、plugin 同梱の `redash` と衝突するため追加・削除できない。

### Skill

`mcp-redash` は次の user-invocable Skill を提供する。

| Skill | 用途 |
|---|---|
| `redash-add` | 指定 suffix の `redash-*` MCP server を project `.mcp.json` に追加する |
| `redash-remove` | 指定 suffix の `redash-*` MCP server を project `.mcp.json` から削除する |
| `redash-list` | plugin 同梱 `redash` と project `.mcp.json` 上の `redash-*` を一覧表示する |
| `redash-status` | 各 Redash MCP が必要とする環境変数名と未設定警告を表示する |
| `redash-guide` | 利用手順と命名規則を表示する |

各操作 Skill は `scripts/redash-mcp-config.js` を呼び出す。`redash-guide` は利用者向けの説明を持つ。

### 設定操作 script

`scripts/redash-mcp-config.js` は project root の `.mcp.json` を操作する Node.js script である。

project root の解決は、`PROJECT_ROOT`、`WORKSPACE_ROOT`、`GIT_WORK_TREE`、`CLAUDE_PROJECT_DIR`、`CODEX_WORKSPACE_ROOT`、`KIRO_WORKSPACE_ROOT` の環境変数を優先し、未指定の場合は `cwd` から `.git` または最寄りの `.mcp.json` を探索する。

`add` は `.mcp.json` が存在しない場合に新規作成する。Codex runtime では top-level map、その他 runtime では `{ "mcpServers": {} }` を初期形とする。既存 `.mcp.json` に `mcpServers` または `mcp_servers` がある場合はその server map を使い、`mcp_servers` は書き込み時に `mcpServers` へ正規化する。どちらもない object は top-level server map として扱う。

`remove` は対象 entry が存在する場合だけ削除する。存在しない場合は変更なしの成功扱いにする。

`list` は常に `redash (plugin bundled)` を表示し、project `.mcp.json` にある `redash-*` entry を名前順で表示する。

`status` は `redash` と project `.mcp.json` にある `redash-*` entry について、必要な環境変数名と未設定警告を表示する。環境変数の値は表示しない。

### Kiro 同期

Kiro runtime では、`redash-mcp-config.js` が `.mcp.json` の Redash MCP server 定義を `.kiro/agents/default.json` の `mcpServers` に同期する。

同期時は `.kiro/agents/default.json` 上の既存 Redash server entry を削除し、project `.mcp.json` 上の `redash` / `redash-*` entry を反映する。`.kiro/agents/default.json` が存在しない場合は、`name: default` と `resources: ["skill://.kiro/skills/**/SKILL.md"]` を持つ初期 agent 設定を作成する。

## データ・設定

`plugins/mcp/mcp-redash` は次のファイルを持つ。

| ファイル | 内容 |
|---|---|
| `.claude-plugin/plugin.json` | Claude Code plugin manifest |
| `.mcp.json` | plugin 同梱 `redash` MCP server 定義 |
| `.env.example` | `REDASH_URL` / `REDASH_API_KEY` と suffix 付き環境変数の例 |
| `README.md` | runtime ごとの導入方法、使い方、トラブルシューティング |
| `scripts/redash-mcp-config.js` | project `.mcp.json` の追加・削除・一覧・状態確認 |
| `skills/*/SKILL.md` | `redash-add` / `redash-remove` / `redash-list` / `redash-status` / `redash-guide` |

runtime 配布物は shared から生成され、Claude Code は `.claude-plugin/plugin.json`、Codex は `.codex-plugin/plugin.json`、Kiro CLI は `install.sh` を導入入口として持つ。

## 外部連携

MCP server の実行コマンドは `npx -y @suthio/redash-mcp` である。接続先 Redash URL と API key は環境変数から渡す。

Redash への接続可否や API 権限は、利用者が設定した `REDASH_*_URL` と `REDASH_*_API_KEY` に依存する。

## エラー処理

- suffix が空または `default` の場合、`add` / `remove` は非 0 で終了する。
- suffix が `^[a-z0-9][a-z0-9-]*$` に一致しない場合、`add` / `remove` は非 0 で終了する。
- `.mcp.json` の JSON が壊れている場合、`add` / `remove` は中断し、既存ファイルを上書きしない。
- `.mcp.json` の server map を解釈できない場合、`add` は非 0 で終了する。
- 追加対象が既に存在する場合、`add` は変更なしの成功扱いにする。
- 削除対象が存在しない場合、`remove` は変更なしの成功扱いにする。
- Kiro 同期時に `.kiro/agents/default.json` の JSON が壊れている場合、処理を中断する。

## セキュリティ

- API key や secret の実値は repository に含めない。
- `.mcp.json` は `${REDASH_API_KEY}` や `${REDASH_DEV_API_KEY}` などの placeholder だけを保持する。
- `.env.example` はサンプル値だけを保持する。
- `redash-status` は必要な環境変数名と未設定警告だけを表示し、環境変数の値は表示しない。

## 運用

単一 Redash 環境だけを使う project では、plugin を導入し、project 環境に `REDASH_URL` と `REDASH_API_KEY` を設定する。

複数 Redash 環境を使う project では、`/redash-add dev` のように suffix を指定して追加し、表示された環境変数名を project `.env` など利用者環境に設定する。設定状況は `/redash-list` と `/redash-status` で確認する。不要になった追加環境は `/redash-remove dev` で project `.mcp.json` から削除する。

## テスト観点

- plugin 導入直後の同梱 MCP が `redash` だけであること。
- `/redash-add dev` が project `.mcp.json` に `redash-dev` を追加し、`REDASH_DEV_URL` と `REDASH_DEV_API_KEY` を参照すること。
- 任意 suffix の追加で MCP 名と環境変数名が衝突しないこと。
- 既存 suffix の再追加が変更なしの成功扱いになること。
- `/redash-remove dev` が `redash-dev` を削除すること。
- 存在しない suffix の削除が変更なしの成功扱いになること。
- `.mcp.json` が壊れている場合に上書きしないこと。
- `redash-status` が secret 値を出さず、未設定の環境変数名だけを警告すること。
- `bash scripts/validate-runtime-plugins.sh` が runtime 配布物の同期、manifest、`.mcp.json`、installer、docs link を検証すること。

## 関連リンク

- [Runtime Plugin Distribution 仕様](runtime-plugin-distribution.md)
- [mcp-redash shared README](../../plugins/mcp/mcp-redash/README.md)
- [mcp-redash shared config script](../../plugins/mcp/mcp-redash/scripts/redash-mcp-config.js)
