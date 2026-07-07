# NDF Kiro Plugin

Kiro CLI 向けの NDF 配布物です。`plugins/ndf-shared` から生成された `skills/`、Kiro agent template、workflow prompt、通知用 script を同梱します。

## インストール

リポジトリ root で実行します。

```bash
# 基本（Skills + agentSpawn hook）
bash plugins/ndf-kiro/install.sh

# Slack通知も有効化
bash plugins/ndf-kiro/install.sh --with-slack

# Slack通知 + Kiro側 Codex MCP 設定も生成
bash plugins/ndf-kiro/install.sh --with-slack --with-codex
```

動作確認だけを行う場合:

```bash
bash plugins/ndf-kiro/install.sh --dry-run
```

installer は `.claude-plugin/plugin.json` を読みません。公開 Skill は build 済みの `plugins/ndf-kiro/skills/` を source として `.kiro/skills/` へ symlink します。

## 開発

Skill を変更する場合は `plugins/ndf-shared/skills/` を編集し、runtime plugin を再生成します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
```
