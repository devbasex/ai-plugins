# NDF 知識構造・Kiro CLI 仕様

## 概要

NDF の知識配置、Serena MCP 分離、Kiro CLI 対応に関する確定仕様。

Skill の挙動仕様は本ディレクトリでは管理しない。Skill は `plugins/ndf-shared/skills/*/SKILL.md` を編集元とし、Kiro では build 済みの `plugins/ndf-kiro/skills/*/SKILL.md` を配布物として使う。

## 仕様化の扱い

本仕様は、完了済み issues / plans の内容を統合した現行仕様である。元の `issues/*` ファイルは完了後に削除されるため、マージ後の正は本ファイル、`AGENTS.md`、`KIRO.md`、`plugins/ndf/README.md`、`docs/ndf-plugin-reference.md` とする。過去の検討履歴が必要な場合は、この仕様を追加した commit の git 履歴を参照する。

## NDF 知識構造

AI エージェント向けの知識は以下の層で管理する。

| 層 | 役割 |
|---|---|
| `AGENTS.md` | リポジトリ共通のエントリポイント、ポリシー、ドキュメント案内 |
| `CLAUDE.md` / `KIRO.md` | 実行環境固有の補足設定 |
| `docs/` | リポジトリ知識、仕様、開発ガイド |
| `plugins/*/skills/` | 実行可能なワークフロー |

`CLAUDE.ndf.md` 注入と Serena memory 依存は廃止済みである。Serena は `mcp-serena` プラグインとして分離し、シンボル検索・参照検索・安全なリファクタリングなどのコード操作用途に限定する。知識は `docs/`、手順は `skills/` に置く。

`SessionStart` hook は `~/.claude/settings.json` の `cleanupPeriodDays` を 90 日以上に保ち、statusline 未設定時は NDF 標準 statusline を設定する。

## Kiro CLI 対応

Kiro CLI 用設定は `.kiro/agents/default.json` で管理する。agent 設定は `AGENTS.md` と `README.md` を `file://` resource として読み込み、`.kiro/skills/**/SKILL.md` を `skill://` resource として参照する。

Kiro CLI では `plugins/ndf-kiro/install.sh` が `plugins/ndf-kiro/skills/` から `.kiro/skills/` への symlink と `.kiro/agents/default.json` を生成する。`agentSpawn` hook は初期化時の案内に使い、`--with-slack` 指定時のみ `stop` hook 相当の Slack 通知を有効化する。Kiro の stop hook payload に `assistant_response` が含まれる場合、`plugins/ndf-kiro/scripts/slack-notify.js` は transcript よりも `assistant_response` を優先して要約に使う。

Codex 連携は MCP サーバではなく `/ndf:codex` skill と `corder` エージェント経由の Codex CLI 直接実行を標準とする。Kiro 用 `--with-codex` は Kiro セッションから Codex CLI を扱う場合の補助設定である。

## データ・設定

| 設定 | 用途 |
|---|---|
| `cleanupPeriodDays` | Claude Code transcript 保持期間。NDF hook が 90 日以上に保つ |
| `.kiro/agents/default.json` | Kiro CLI 用 agent 設定 |
| `.kiro/skills/` | Kiro CLI 用 Skill symlink 配置 |

## 外部連携

| 連携 | 仕様 |
|---|---|
| Kiro CLI | `.kiro/agents/default.json` と `.kiro/skills/` で Skill / hook / MCP 設定を提供 |
| Serena MCP | `mcp-serena` プラグインとして分離提供 |
| Codex CLI | `/ndf:codex` skill と `corder` エージェントから直接実行 |
| Slack | Claude Code / Kiro / Codex の終了通知に使用 |

## テスト観点

| 領域 | 確認内容 |
|---|---|
| Kiro CLI | `.kiro/agents/default.json` が resources / hooks / mcpServers を持つこと |
| ドキュメント | `AGENTS.md` / `CLAUDE.md` / `KIRO.md` / `docs/` の役割が重複しすぎていないこと |

## 関連リンク

- [mcp-serena README](../../plugins/mcp-serena/README.md)
- [NDF Plugin リファレンス](../ndf-plugin-reference.md)
- [NDF README](../../plugins/ndf/README.md)
