# NDF 知識構造・Kiro CLI 仕様

## 概要

NDF の知識配置、Serena MCP 分離、Kiro CLI 対応に関する確定仕様。

Skill の挙動仕様は本ディレクトリでは管理しない。Skill は `plugins/ndf-shared/skills/*/SKILL.md` を編集元とし、Kiro では build 済みの `plugins/ndf-kiro/skills/*/SKILL.md` を配布物として使う。

## 仕様化の扱い

本仕様は、完了済み issues / plans の内容を統合した現行仕様である。元の `issues/*` ファイルは完了後に削除されるため、マージ後の正は本ファイル、`AGENTS.md`、`KIRO.md`、`plugins/ndf-claude/README.md`、`docs/ndf-plugin-reference.md` とする。過去の検討履歴が必要な場合は、この仕様を追加した commit の git 履歴を参照する。

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

Kiro CLI 用設定は `.kiro/agents/ndf.json` で管理する。agent 設定は `AGENTS.md` と `README.md` を `file://` resource として読み込む。`.kiro/skills/**/SKILL.md` の `skill://` 指定は組み込み agent の読み込み対象と重複するため持たない。常時適用したい指示は agent 選択に依存しない `.kiro/steering/ndf-policies.md` へ置く。

Kiro CLI では `plugins/ndf-kiro/install.sh` が `plugins/ndf-kiro/skills/` から `.kiro/skills/` への symlink、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成する。`ndf-policies` は steering の生成元としてのみ使い、`.kiro/skills/` へは symlink しない。Kiro は `.kiro/skills/*/SKILL.md` と `.kiro/steering/**/*.md` の両方を文脈へ読み込むため、両方に置くと同じ内容が 2 回注入されるからである。manifest（`plugins/ndf-shared/manifests/kiro-skills.txt`）には steering の生成元として残す。`--scope global` を指定した場合の生成先は `~/.kiro/` 配下になる。生成した agent は既定にならないため、`--set-default` を指定したときだけ `kiro-cli agent set-default ndf` を実行する。`kiro-cli` は workspace agent を cwd 配下の `.kiro/agents/` からのみ検出するため、この呼び出しは導入先（`workspace` なら `--project` のパス、`global` なら `$HOME`）で行い、`agent list` で反映を検証する（`set-default` は agent 未検出でも終了コード 0 を返すため）。`agentSpawn` hook は初期化時の案内に使い、`--with-slack` 指定時のみ `stop` hook 相当の Slack 通知を有効化する。Kiro の stop hook payload に `assistant_response` が含まれる場合、`plugins/ndf-kiro/scripts/slack-notify.js` は transcript よりも `assistant_response` を優先して要約に使う。

Codex 連携は MCP サーバではなく `/ndf:external-ai` skill と `corder` エージェント経由の Codex CLI 直接実行を標準とする。Kiro 用 `--with-codex` は Kiro セッションから Codex CLI を扱う場合の補助設定である。

## データ・設定

| 設定 | 用途 |
|---|---|
| `cleanupPeriodDays` | Claude Code transcript 保持期間。NDF hook が 90 日以上に保つ |
| `.kiro/agents/ndf.json` | Kiro CLI 用 agent 設定 |
| `.kiro/steering/ndf-policies.md` | agent 選択に依存しない常時指示 |
| `.kiro/skills/` | Kiro CLI 用 Skill symlink 配置 |

## 外部連携

| 連携 | 仕様 |
|---|---|
| Kiro CLI | `.kiro/agents/ndf.json`、`.kiro/steering/`、`.kiro/skills/` で Skill / hook / MCP 設定を提供 |
| Serena MCP | `mcp-serena` プラグインとして分離提供 |
| Codex CLI | `/ndf:external-ai` skill と `corder` エージェントから直接実行 |
| Slack | Claude Code / Kiro / Codex の終了通知に使用 |

## テスト観点

| 領域 | 確認内容 |
|---|---|
| Kiro CLI | `.kiro/agents/ndf.json` が resources と hooks を持ち、`kiro-cli agent list` に `ndf` が現れること。`mcpServers` は `--with-codex` 指定時、または既存の利用者管理設定を引き継いだ場合にのみ現れる |
| ドキュメント | `AGENTS.md` / `CLAUDE.md` / `KIRO.md` / `docs/` の役割が重複しすぎていないこと |

## 関連リンク

- [mcp-serena README](../../plugins/mcp/shared/mcp-serena/README.md)
- [NDF Plugin リファレンス](../ndf-plugin-reference.md)
- [NDF README](../../plugins/ndf-claude/README.md)
