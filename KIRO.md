# AI Plugins - Kiro CLI開発ガイドライン

共通の開発ガイドラインは **@AGENTS.md** を参照してください。

このファイルにはKiro CLI固有の設定のみを記載します。

## NDFプラグインのセットアップ

### 前提条件
- Kiro CLI がインストール済み
- Node.js（Slack通知を使う場合）
- Codex CLI（`/ndf:external-ai` skill で外部AI委譲を使う場合、または `--with-codex` で Kiro に Codex MCP サーバ設定を生成する場合）: `npm install -g @openai/codex`

### インストール

```bash
# 基本（Skills + agentSpawn / userPromptSubmit フック）
bash plugins/ndf/dev.kiro/install.sh

# Slack通知も有効化
bash plugins/ndf/dev.kiro/install.sh --with-slack

# 全部入り（Slack通知 + Kiro 側 Codex MCP 設定生成）
# 注: NDF の外部 AI 委譲は /ndf:external-ai skill が CLI を直接実行し、Codex MCP に依存しない。
# --with-codex は Kiro セッションで `mcp__codex__*` を直接呼びたい場合だけ付ける
# （Codex MCP サーバの設定と .kiro/prompts/codex.md を追加する）。
bash plugins/ndf/dev.kiro/install.sh --with-slack --with-codex
```

インストーラーは `plugins/ndf/skills/` から `.kiro/skills/` への symlink、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成します。リンクする Skill は `plugins/ndf/manifests/kiro-skills.txt` が決めます。`ndf-policies` だけは steering の生成元として使い、`.kiro/skills/` へは symlink しません（Skill と steering の二重注入を避けるため）。

`.kiro/agents/ndf.json` の `hooks` には worktree 運用の 2 つが入ります。

| hook | 実行するもの | 起きること |
|---|---|---|
| `agentSpawn` | `scripts/worktree-session.sh` | メインディレクトリに残った未コミット変更の提示。ブランチの追従は既定では行わず、`.ndf/worktree.json` の `follow_branch: true` で有効にする |
| `userPromptSubmit` | `scripts/worktree-guard.sh` | worktree で作業する旨の案内 |

Kiro CLI では tool 実行前の hook を使いません。この事象でモデルへ案内を渡す手段が終了コード 2 に限られ、それが tool の実行を拒否するためです。拒否しない方針のもとでは置けないため、パスを見ない案内をプロンプト送信時の hook が担います。

**どちらもリポジトリ側に `.ndf/worktree.json` があるときだけ動きます。** 無ければ何も出力せず終了コード 0 で終わります。書き方は `plugins/ndf/README.md` の「Hooks」節にあります。

### Slack通知の設定（オプション）

`.env` に以下を設定：
```
SLACK_CHANNEL_ID=C0123456789
SLACK_BOT_TOKEN=xoxb-...
SLACK_USER_MENTION=<@U0123456789>
```

### 利用方法

- スキルはdescriptionのキーワードに基づいて自動参照されます
- ワークフロー系スキル（pr, fix, review等）は手動で指示してください
  - 例: 「prスキルの手順に従ってPRを作成して」
- `plugins/ndf/manifests/kiro-skills.txt` に skill が追加された場合、再度インストーラーを実行してください
