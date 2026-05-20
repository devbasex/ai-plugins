# NDF Plugin - 開発者向けガイドライン

## 概要

**NDFプラグインの開発・メンテナンス**を行うAIエージェント向けガイドライン。

## プラグイン情報

- **名前**: ndf
- **現在バージョン**: 4.4.0
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
├── .mcp.json                    # MCPサーバー定義（Codex CLI）
├── hooks/
│   └── hooks.json               # プロジェクトフック定義
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
├── skills/                      # スキル（39個）
│   # PRワークフロー系
│   ├── pr/                      # commit+push+PR作成/更新
│   ├── pr-tests/                # Test Plan自動実行
│   ├── fix/                     # PRコメント修正対応
│   ├── review/                  # PR単位レビュー（Approve/RC判定）
│   ├── review-branch/           # ローカル差分レビュー（PR前）
│   ├── review-pr-comments/      # PRコメント分類（READ-ONLY）
│   ├── resolve-pr-comments/     # 対応済みコメント返信+Resolve
│   ├── cherry-pick-pr/          # 環境ブランチへのcherry-pick PR
│   ├── deploy/                  # 環境ブランチへのデプロイPR
│   ├── sync-main/               # main取り込み
│   ├── merged/                  # マージ後クリーンアップ
│   ├── clean/                   # マージ済みブランチ一括削除
│   # 原則・ガイドライン系
│   ├── ndf-policies/            # ポリシー常時注入
│   ├── branch-fix-strategy/     # ブランチ修正適用戦略
│   ├── issue-plan-strategy/     # issue→plan→multi-PR ワークフロー (release branch + draft PR + worktree)
│   ├── implementation-plan/     # 実装プラン管理(issues/)
│   ├── investigation-rules/     # 調査時のエビデンス主義
│   ├── problem-solving/         # 根本原因分析・多層防御
│   ├── logging-guidelines/      # ログ運用ガイドライン(言語非依存)
│   # データ分析・品質
│   ├── data-analyst-sql-optimization/
│   ├── data-analyst-export/
│   ├── qa-security-scan/
│   # ドキュメント・環境
│   ├── markdown-writing/
│   ├── python-execution/
│   ├── docker-container-access/
│   ├── deepwiki-transfer/
│   ├── knowledge-reorg/
│   ├── git-gh-operations/
│   ├── google-auth/
│   ├── browser-test/            # ブラウザ動作確認(Playwright/Chrome DevTools)
│   ├── codex/                   # Codex CLI直接実行（MCP版との使い分け）
│   ├── playwright-scenario-test/ # Playwright+curl Web シナリオE2E並列ランナー
│   ├── google-drive/            # Google Drive エクスポート/DL/UP（google-auth依存）
│   ├── google-chat/             # Google Chat メッセージ取得（google-auth依存）
│   # Anthropic公式連携
│   ├── mcp-builder/             # Anthropic公式（Apache-2.0）
│   └── official-skills-autoloader/  # 公式Skill自動ロード
├── AGENTS.md                    # このファイル（開発者向け）
└── README.md                    # プラグイン説明書
```

## 一般的な開発タスク

### 新しいスキルの追加

1. `skills/{skill-name}/SKILL.md` を作成（YAMLフロントマター必須）
2. `plugin.json` の `skills` 配列に `"./skills/{skill-name}"` を追加
3. `plugin.json` のバージョンをMINOR上げ
4. テスト・コミット

### 新しいサブエージェントの追加

1. `agents/{agent-name}.md` を作成（YAMLフロントマター必須）
2. `plugin.json` の `agents` 配列に追加
3. バージョンMINOR上げ → テスト・コミット

### MCPサーバーの追加・更新

1. `.mcp.json` の `mcpServers` に追加
2. README.mdに説明追加
3. バージョン更新 → テスト・コミット

## 検証チェックリスト

- [ ] plugin.jsonが有効なJSON
- [ ] バージョン番号が適切にインクリメント
- [ ] すべてのスキル/エージェントファイルが存在
- [ ] YAMLフロントマターが正しい
- [ ] .mcp.jsonが有効なJSON
- [ ] README.md が最新

## トラブルシューティング

| 問題 | 対処 |
|------|------|
| エージェントが認識されない | plugin.jsonのagents配列、ファイルパス、YAMLフロントマターを確認 |
| スキルが表示されない | plugin.jsonのskills配列、SKILL.mdのフロントマターを確認、`/plugin reload ndf` |
| MCPサーバーが起動しない | .mcp.jsonの構文、コマンドパス、環境変数を確認 |
| フックが動作しない | hooks.jsonの構文、スクリプト実行権限を確認 |

## 開発履歴

バージョン履歴は [CHANGELOG.md](CHANGELOG.md) を参照。
