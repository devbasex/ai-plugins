# NDF Plugin リファレンス

## 概要

NDF は Claude Code / Codex / Kiro CLI 向けの開発支援プラグイン群です。marketplace 上の plugin 名は全ランタイムで `ndf` を維持し、配布物は `plugins/ndf/` の 1 ディレクトリにまとまっています。

| ランタイム | 導入方法 | 公開 Skill |
|---|---|---|
| Claude Code | `.claude-plugin/marketplace.json` の `ndf` | 27 個 |
| Codex | `.claude-plugin/marketplace.json` の `ndf` | 25 個 |
| Kiro CLI | `plugins/ndf/dev.kiro/install.sh` | 26 個 |

Skill の実体は `plugins/ndf/skills/` の 1 箇所だけです。ランタイム別の複製はありません。Skill や
共通スクリプトを変更する場合は `plugins/ndf/` を直接編集します。

## ディレクトリ構造

```text
plugins/ndf/
├── .claude-plugin/plugin.json   # Claude Code（agents / hooks / skills 配列）
├── .codex-plugin/plugin.json    # Codex（hooks / skills 配列）
├── skills/                      # 配布 Skill の唯一の実体（27 個）
├── optional-skills/             # どの配布先にも載せない Skill（4 個）
├── manifests/                   # ランタイム別の配布 Skill 一覧
├── agents/                      # Claude Code のサブエージェント（8 個）
├── hooks/claude.json            # Claude Code の SessionStart / Stop hook
├── hooks/codex.json             # Codex の Stop hook
├── scripts/                     # hook と Skill から呼ぶスクリプト
└── dev.kiro/                    # Kiro CLI の installer・エージェント定義・プロンプト
```

`dev.kiro` は Agent Plugins 1.0.0 §8.2 が定めるクライアント拡張ディレクトリです。生成物は
`skills/<名前>/agents/openai.yaml`（Codex の暗黙起動ポリシー）だけで、commit 対象です。利用者が
plugin install 時に build を実行する必要はありません。

## Runtime 別の同梱内容

| Runtime | 読むもの |
|---|---|
| Claude Code | `.claude-plugin/plugin.json`（`agents/` 8 個、`hooks/claude.json`、`skills` 配列 32 個）、`scripts/` |
| Codex | `.codex-plugin/plugin.json`（`hooks/codex.json`、`skills` 配列 30 個）、`scripts/` |
| Kiro CLI | `dev.kiro/`（installer・agent config template・workflow prompts）、`manifests/kiro-skills.txt`、`skills/`、`scripts/` |
| agy | `dev.agy/`（マニフェスト・`hooks.json`・`skills/` の symlink・`agents` と `scripts` への symlink） |

同じディレクトリを 4 ランタイムが共有しますが、読む対象はマニフェストと installer が決めるため、
公開される Skill と hook はランタイムごとに異なります。Claude Code 専用の agents / statusline /
transcript retention hook は Codex の `skills` 配列と `hooks/codex.json` には入りません。

## Skills

NDF の Skill 実装は `plugins/ndf/skills/` にあります。公開セットは manifest で管理します。

| Manifest | 読む側 |
|---|---|
| `plugins/ndf/manifests/claude-skills.txt` | `.claude-plugin/plugin.json` の `skills` 配列 |
| `plugins/ndf/manifests/codex-skills.txt` | `.codex-plugin/plugin.json` の `skills` 配列 |
| `plugins/ndf/manifests/kiro-skills.txt` | `dev.kiro/install.sh` が張る symlink |

どの manifest にも載せない Skill は `plugins/ndf/optional-skills/` へ置きます。`skills/` を配布 Skill の
実体だけに保つことで、絞り込みの結果によらず公開数が変わりません。

主な Skill 領域:

- PR / review workflow: `pr`, `pr-tests`, `fix`, `pr-review`, `cross-review`, `cross-refactoring`
- branch / release workflow: `worktree`, `deploy`, `cherry-pick-pr`, `merged`, `release`,
  `release-verification`
- planning / documentation: `implementation-plan`, `issue-plan-strategy`, `plan-to-spec`,
  `markdown-writing`, `investigation-rules`
- development methodology: `development-workflow`, `requirements-design`, `tdd-cycle`,
  `refactoring`, `quality-gates`, `retrospective`, `out-of-scope`
- quality / execution: `docker-container-access`, `qa-security-scan`
  （Playwright による E2E テストは v7.0.0 で `playwright-kit` プラグインへ分離）
- external services: `external-ai`, `official-skills-autoloader`（Claude Code のみ）
- runtime 設定: `statusline`（Claude Code と Kiro CLI のみ）
- policy: `ndf-policies`, `problem-solving`, `logging-guidelines`

`optional-skills/` に置く `google-auth` / `google-drive` / `ml-model-structure` / `skill-stats`
はどの manifest にも載らないため、この一覧には含めていません。

## MCP Plugins

MCP plugin も `plugins/mcp/<plugin-name>/` の 1 ディレクトリにまとまっています。サーバ定義は `.mcp.json` の 1 箇所だけで、3 runtime が同じファイルを読みます。

Claude Code と Codex は marketplace から同じ plugin 名で install します。Kiro CLI は `plugins/mcp/<plugin-name>/dev.kiro/install.sh` で対象プロジェクトの `.mcp.json` と必要な Kiro agent 設定を更新します。

## Build / Validation

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
claude plugin validate plugins/ndf
python3 -m json.tool plugins/ndf/.codex-plugin/plugin.json >/dev/null
bash plugins/ndf/dev.kiro/install.sh --dry-run
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro
```

`--check` は生成物（`plugins/<プラグイン名>/skills/<Skill 名>/agents/openai.yaml` と `plugins/mcp/<プラグイン名>/dev.kiro/install.sh`）が最新であることを検証します。`validate-runtime-plugins.sh` は生成物同期、JSON / manifest、marketplace source、Kiro installer、Markdown ローカルリンクをまとめて確認します。

`runtime-smoke-test.sh` は Docker コンテナ内に repo copy、`/tmp/runtime-home`、`/tmp/runtime-project`、`/tmp/runtime-secrets` を分離して作成し、Claude / Codex / Kiro それぞれで `ndf` と `mcp-bigquery` 相当の実 install、Skill / MCP / hook / agent config の assertion、JUnit / log artifact 出力を確認します。PR CI は `--with-secrets=off` の非認証 smoke のみを実行し、認証付き smoke は `runtime-plugin-authenticated-smoke.yml` の protected workflow で実行します。

ローカル hook を使う場合は `bash scripts/install-dev-hooks.sh` で `.githooks/` を有効化します。

## 外部 AI 委譲

Codex MCP サーバは廃止済みです。外部 AI 委譲は `/ndf:external-ai` Skill と Claude Code 版の `corder` エージェントから Codex / Gemini CLI を直接呼び出す方式を標準とします。

## Slack 通知

| Runtime | 通知方法 |
|---|---|
| Claude Code | Stop hook で `plugins/ndf/scripts/slack-notify.js` を実行 |
| Codex | `NDF_CODEX_SLACK_NOTIFY=true` の場合のみ Stop hook で通知 |
| Kiro CLI | `plugins/ndf/dev.kiro/install.sh --with-slack` で通知 hook を生成 |

機密情報は環境変数で管理し、リポジトリにはコミットしません。

## 関連ドキュメント

- [Claude Code版 README](../plugins/ndf/README.md)
- [Codex版 README](../plugins/ndf/README.md)
- [Kiro CLI版 README](../plugins/ndf/README.md)
- [NDF プラグイン README](../plugins/ndf/README.md)
- [runtime plugin container smoke 仕様](specifications/runtime-plugin-container-smoke.md)
