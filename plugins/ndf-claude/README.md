# NDF Claude Code Plugin

Claude Code 向けの NDF プラグインです。PR 運用、レビュー、調査、実装計画、仕様書化、開発方法論（要求定義・テスト駆動・構造改善・完了判定）、statusline、Codex CLI 委譲、Slack 通知を Claude Code の plugin として提供します。

## インストール

Claude Code で marketplace を追加し、`ndf` をインストールします。

```bash
/plugin marketplace add https://github.com/devbasex/ai-plugins
/plugin install ndf@ai-plugins
```


## Playwright テストについて

v7.0.0 で Playwright による E2E テストの 4 Skill を **`playwright-kit` プラグイン**へ分離しました。
Skill 名は変わらないため `/playwright-` まで打てば従来どおり候補に出ますが、プラグインを別途
インストールする必要があります。

```bash
/plugin install playwright-kit@ai-plugins
```

移行先の対応表は予告どおり v8.0.0 で `ndf-policies` から削除しました。リポジトリ root の
[README.md](../../README.md) の「NDF v7.0.0 の主な変更（非互換）」を参照してください。

## 同梱内容

- `.claude-plugin/plugin.json`: Claude Code plugin manifest
- `agents/`: director、data-analyst、corder、researcher、qa、debugger、devops-engineer、code-reviewer
- `skills/`: Claude Code 向けに公開する NDF skills
- `hooks/hooks.json`: SessionStart / Stop hook
- `scripts/`: hook と skill から利用する同梱スクリプト

## Hooks

SessionStart hook は以下を行います。

- `~/.claude/settings.json` の `cleanupPeriodDays` を 90 日以上に保つ
- statusline 未設定時に NDF 標準 statusline を設定する

Stop hook は Claude Code 終了時に Slack 通知スクリプトを実行します。通知に必要な環境変数が未設定の場合は送信せず終了します。

## Slack 通知

Slack 通知を使う場合は、利用プロジェクト側で以下の環境変数を設定します。

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>
```

`SLACK_USER_MENTION` は任意です。機密値は `.env` などで管理し、リポジトリへコミットしないでください。

## 外部 AI 委譲

`/ndf:external-ai` skill または `corder` エージェントから外部 AI 委譲を使う場合は、利用環境に Codex CLI をインストールしてログインします。

```bash
npm install -g @openai/codex
codex login
```

`/ndf:pr-review <PR番号> gemini` や `/ndf:cross-review` で Gemini 委譲を使う場合は、利用環境に Gemini CLI をインストールしてログインします。

```bash
npm install -g @google/gemini-cli
gemini
```

## 開発者向け

`skills/` と `scripts/` は `plugins/ndf-shared` から生成される commit 対象の生成物です。Skill や共通スクリプトを変更する場合は `plugins/ndf-shared` を編集し、build を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
claude plugin validate plugins/ndf-claude
```
