# NDF Plugin - 開発者向けガイドライン

## 概要

**NDFプラグインの開発・メンテナンス**を行うAIエージェント向けガイドライン。

## プラグイン情報

- **名前**: ndf
- **現在バージョン**: 4.18.0
- **種類**: 統合プラグイン（Skills + Agents + Hooks / v4.0.0 で Codex MCP 廃止）
- **リポジトリ**: https://github.com/devbasex/ai-plugins

> **Note (v3.0.0)**: Serena MCPは`mcp-serena`プラグインに分離。memory系スキルは廃止。CLAUDE.ndf.md注入は廃止。

## 開発ルール

- ドキュメント・コミットメッセージ・PR説明は**日本語**
- **mainブランチへの直接コミット禁止**（featureブランチ+PR）
- **セマンティックバージョニング**: MAJOR（破壊的変更）、MINOR（新機能）、PATCH（バグ修正）

## ディレクトリ構造

```
plugins/ndf/
├── .claude-plugin/
│   └── plugin.json              # プラグインメタデータ
├── .codex-plugin/
│   └── plugin.json              # Codexプラグインメタデータ
├── hooks/
│   ├── hooks.json               # Claude Codeプロジェクトフック定義
│   └── codex-hooks.json         # Codex hook定義
├── scripts/
│   └── slack-notify.js          # Slack通知スクリプト
├── agents/                      # サブエージェント（8個、モデル階層化）
│   ├── director.md              # opus: 計画・統括
│   ├── corder.md                # sonnet: Codex第二意見レビュー
│   ├── data-analyst.md          # sonnet: BigQuery/SQL
│   ├── researcher.md            # sonnet: AWS Docs/Chrome DevTools
│   ├── qa.md                    # sonnet: セキュリティ/品質
│   ├── debugger.md              # sonnet: 根本原因分析
│   ├── devops-engineer.md       # sonnet: Docker/CI/K8s
│   └── code-reviewer.md         # sonnet: diff/PRレビュー
├── skills/                      # 全Skill実体（48個、Claude Code/Kiroはmanifest配列で公開対象を指定）
├── skills-codex/                # Codex向け公開Skill（core 29個、marketplace cache向け実ディレクトリ）
├── skills-optional/             # ランタイム別除外候補リスト
├── AGENTS.md                    # このファイル（開発者向け）
└── README.md                    # プラグイン説明書
```

## 一般的な開発タスク

### 新しいスキルの追加

1. `skills/{skill-name}/SKILL.md` を作成（YAMLフロントマター必須）
2. Claude Code/Kiroで初期公開する場合は `.claude-plugin/plugin.json` の `skills` 配列に `"./skills/{skill-name}"` を追加
3. Codexで初期公開する場合は `skills-codex/{skill-name}` に実ディレクトリとしてコピーする（`.codex-plugin/plugin.json` は `./skills-codex/` ディレクトリを参照）
4. 低頻度・保守用に留める場合は `skills-optional/README.md` の候補リストへ追加
5. plugin.json のバージョンをMINOR上げ
6. テスト・コミット

### 新しいサブエージェントの追加

1. `agents/{agent-name}.md` を作成（YAMLフロントマター必須）
2. `plugin.json` の `agents` 配列に追加
3. バージョンMINOR上げ → テスト・コミット

## 検証チェックリスト

- [ ] plugin.jsonが有効なJSON
- [ ] バージョン番号が適切にインクリメント
- [ ] すべてのスキル/エージェントファイルが存在
- [ ] YAMLフロントマターが正しい
- [ ] README.md が最新

## トラブルシューティング

| 問題 | 対処 |
|------|------|
| エージェントが認識されない | plugin.jsonのagents配列、ファイルパス、YAMLフロントマターを確認 |
| スキルが表示されない | plugin.jsonのskills配列、SKILL.mdのフロントマターを確認、`/plugin reload ndf` |
| フックが動作しない | hooks.jsonの構文、スクリプト実行権限を確認 |

## 開発履歴

バージョン履歴は [CHANGELOG.md](CHANGELOG.md) を参照。
