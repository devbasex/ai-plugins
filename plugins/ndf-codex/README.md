# NDF Codex Plugin

Codex CLI 向けの NDF プラグインです。PR 運用、レビュー、cross-review、実装計画、仕様書化、Playwright テスト運用、Docker container access、GitHub 操作補助などの Codex 用 skills と、Codex 終了時の任意 Slack 通知 hook を提供します。

## インストール

Codex で marketplace を追加し、`ndf` をインストールします。

```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

## 同梱内容

- `.codex-plugin/plugin.json`: Codex plugin manifest
- `skills/`: Codex 向けに公開する NDF skills
- `hooks/hooks.json`: Codex Stop hook
- `scripts/`: hook と skill から利用する同梱スクリプト

Claude Code 専用の agents、statusline 自動設定、transcript retention 自動設定は含めません。Codex runtime が読むファイルはこの `plugins/ndf-codex` 配下だけで完結します。

## Slack 通知

Codex 版の Stop hook は `NDF_CODEX_SLACK_NOTIFY=true` が設定されている場合だけ Slack 通知を送ります。通知を使う場合は、利用プロジェクト側で以下の環境変数を設定します。

```bash
NDF_CODEX_SLACK_NOTIFY=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>
```

`SLACK_USER_MENTION` は任意です。機密値は `.env` などで管理し、リポジトリへコミットしないでください。

Codex の hook は初回実行前に Codex 側の hooks trust 設定が必要になる場合があります。`/hooks` で対象 hook を確認し、利用するプロジェクトで明示的に有効化してください。

## 検証

Codex plugin schema を検証できる CLI が利用できる場合は、Codex 側の validate / install smoke を実行してください。CLI に検証コマンドが無い環境では、manifest JSON と参照パスの存在を確認します。

```bash
python3 -m json.tool plugins/ndf-codex/.codex-plugin/plugin.json >/dev/null
python3 -m json.tool plugins/ndf-codex/hooks/hooks.json >/dev/null
test -d plugins/ndf-codex/skills
test -d plugins/ndf-codex/scripts
```

install smoke を行う場合は、別の一時プロジェクトで marketplace から `ndf@ai-plugins` を追加し、代表 skill が読み込まれることと Stop hook が trust 対象として表示されることを確認します。

## 開発者向け

`skills/` と `scripts/` は `plugins/ndf-shared` から生成される commit 対象の生成物です。Skill や共通スクリプトを変更する場合は `plugins/ndf-shared` を編集し、build を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
```
