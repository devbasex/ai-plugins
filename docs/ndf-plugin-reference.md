# NDF Plugin リファレンス

## 概要

NDF は Claude Code / Codex / Kiro CLI 向けの開発支援プラグイン群です。marketplace 上の plugin 名は全ランタイムで `ndf` を維持し、配布物はランタイム別ディレクトリに分けます。

| 用途 | ディレクトリ | 配布方法 |
|---|---|---|
| 共通編集元 | `plugins/ndf-shared/` | 直接 install しない |
| Claude Code | `plugins/ndf-claude/` | `.claude-plugin/marketplace.json` の `ndf` |
| Codex | `plugins/ndf-codex/` | `.agents/plugins/marketplace.json` の `ndf` |
| Kiro CLI | `plugins/ndf-kiro/` | `plugins/ndf-kiro/install.sh` |

旧 monolithic NDF ディレクトリは廃止済みです。Skill や共通スクリプトを変更する場合は `plugins/ndf-shared/` を編集し、`bash scripts/build-runtime-plugins.sh` で runtime 配布物を再生成します。

## ディレクトリ構造

```text
plugins/
├── ndf-shared/
│   ├── skills/
│   ├── scripts/
│   └── manifests/
├── ndf-claude/
│   ├── .claude-plugin/plugin.json
│   ├── agents/
│   ├── hooks/
│   ├── skills/
│   └── scripts/
├── ndf-codex/
│   ├── .codex-plugin/plugin.json
│   ├── hooks/
│   ├── skills/
│   └── scripts/
└── ndf-kiro/
    ├── install.sh
    ├── agents/default.json.template
    ├── prompts/
    ├── skills/
    └── scripts/
```

生成物は commit 対象です。利用者が plugin install 時に build を実行する必要はありません。

## Runtime 別の同梱内容

| Runtime | 同梱内容 |
|---|---|
| Claude Code | 8個の専門エージェント、Claude向け公開Skills、SessionStart/Stop hook、statusline、Slack通知スクリプト |
| Codex | Codex向け公開Skills、Stop hook、任意Slack通知スクリプト |
| Kiro CLI | Kiro向け公開Skills、agent config template、workflow prompts、installer |

Claude Code 専用の agents / statusline / transcript retention hook は Codex 版と Kiro 版には含めません。Codex 版と Kiro 版は、それぞれの runtime が読むディレクトリだけで完結します。

## Skills

NDF の Skill 実装は `plugins/ndf-shared/skills/` が編集元です。公開セットは manifest で管理します。

| Manifest | 出力先 |
|---|---|
| `plugins/ndf-shared/manifests/claude-skills.txt` | `plugins/ndf-claude/skills/` |
| `plugins/ndf-shared/manifests/codex-skills.txt` | `plugins/ndf-codex/skills/` |
| `plugins/ndf-shared/manifests/kiro-skills.txt` | `plugins/ndf-kiro/skills/` |

主な Skill 領域:

- PR / review workflow: `pr`, `pr-tests`, `fix`, `review`, `cross-review`, `resolve-pr-comments`
- branch / release workflow: `deploy`, `cherry-pick-pr`, `sync-main`, `merged`, `clean`
- planning / documentation: `implementation-plan`, `issue-plan-strategy`, `plan-to-spec`, `markdown-writing`
- quality / execution: `playwright-*`, `python-execution`, `docker-container-access`, `git-gh-operations`
- external services: `google-drive`, `google-chat`, `data-analyst-*`
- policy: `ndf-policies`, `problem-solving`, `logging-guidelines`

## MCP Plugins

MCP plugin も runtime 別に配布します。共通編集元は `plugins/mcp/shared/<plugin-name>/`、配布物は `plugins/mcp/claude|codex|kiro/<plugin-name>/` です。

Claude Code と Codex は marketplace から同じ plugin 名で install します。Kiro CLI は `plugins/mcp/kiro/<plugin-name>/install.sh` で対象プロジェクトの `.mcp.json` と必要な Kiro agent 設定を更新します。

## Build / Validation

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
claude plugin validate plugins/ndf-claude
python3 -m json.tool plugins/ndf-codex/.codex-plugin/plugin.json >/dev/null
bash plugins/ndf-kiro/install.sh --dry-run
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro
```

`--check` は `plugins/ndf-*` と `plugins/mcp/claude|codex|kiro` の生成物が共通編集元と同期していることを検証します。`validate-runtime-plugins.sh` は生成物同期、JSON / manifest、marketplace source、Kiro installer、Markdown ローカルリンクをまとめて確認します。

`runtime-smoke-test.sh` は Docker コンテナ内に repo copy、`/tmp/runtime-home`、`/tmp/runtime-project`、`/tmp/runtime-secrets` を分離して作成し、Claude / Codex / Kiro それぞれで `ndf` と `mcp-bigquery` 相当の実 install、Skill / MCP / hook / agent config の assertion、JUnit / log artifact 出力を確認します。PR CI は `--with-secrets=off` の非認証 smoke のみを実行し、認証付き smoke は `runtime-plugin-authenticated-smoke.yml` の protected workflow で実行します。

ローカル hook を使う場合は `bash scripts/install-dev-hooks.sh` で `.githooks/` を有効化します。

## 外部 AI 委譲

Codex MCP サーバは廃止済みです。外部 AI 委譲は `/ndf:codex` Skill と Claude Code 版の `corder` エージェントから Codex CLI を直接呼び出す方式を標準とします。

## Slack 通知

| Runtime | 通知方法 |
|---|---|
| Claude Code | Stop hook で `plugins/ndf-claude/scripts/slack-notify.js` を実行 |
| Codex | `NDF_CODEX_SLACK_NOTIFY=true` の場合のみ Stop hook で通知 |
| Kiro CLI | `plugins/ndf-kiro/install.sh --with-slack` で通知 hook を生成 |

機密情報は環境変数で管理し、リポジトリにはコミットしません。

## 関連ドキュメント

- [Claude Code版 README](../plugins/ndf-claude/README.md)
- [Codex版 README](../plugins/ndf-codex/README.md)
- [Kiro CLI版 README](../plugins/ndf-kiro/README.md)
- [共通編集元 README](../plugins/ndf-shared/README.md)
- [runtime plugin container smoke 仕様](specifications/runtime-plugin-container-smoke.md)
