# Kiro CLI移植計画

## プロジェクト概要

**目的**: Claude Code用NDFプラグインをKiro CLI向けに移植し、同等の開発体験を提供する

**アプローチ**: 別リポジトリで新規プロジェクトとして移植

**新リポジトリ名**: `kiro-ndf-config`

## Phase 1: プロジェクト基盤構築

### 1.1 リポジトリ作成

**タスク**:
- [ ] GitHubリポジトリ作成: `kiro-ndf-config`
- [ ] 基本ディレクトリ構造作成
- [ ] README.md作成
- [ ] LICENSE追加（MIT）
- [ ] .gitignore設定

**ディレクトリ構造**:
```
kiro-ndf-config/
├── .kiro/
│   ├── agents/              # エージェント設定
│   ├── prompts/             # プロンプト（コマンド代替）
│   └── mcp.json             # MCP設定
├── scripts/                 # ヘルパースクリプト
├── docs/                    # ドキュメント
├── .env.example             # 環境変数テンプレート
├── README.md
└── LICENSE
```

### 1.2 ドキュメント作成

**タスク**:
- [ ] README.md: プロジェクト概要、インストール手順
- [ ] INSTALL.md: 詳細インストールガイド
- [ ] USAGE.md: 使用方法
- [ ] AGENTS.md: エージェント説明
- [ ] MCP.md: MCP設定ガイド

## Phase 2: MCP統合

### 2.1 MCP設定ファイル作成

**タスク**:
- [ ] `.kiro/mcp.json`作成
- [ ] 7つのMCPサーバー設定を移植:
  - [ ] Serena MCP
  - [ ] Notion MCP
  - [ ] BigQuery MCP
  - [ ] DBHub MCP
  - [ ] Chrome DevTools MCP
  - [ ] AWS Docs MCP
  - [ ] Codex CLI MCP

**設定例**:
```json
{
  "mcpServers": {
    "serena": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/oraios/serena",
        "serena",
        "start-mcp-server",
        "--context",
        "ide-assistant",
        "--enable-web-dashboard",
        "False"
      ],
      "env": {
        "SERENA_HOME": "${SERENA_HOME:-.serena}",
        "GOOGLE_API_KEY": "${GOOGLE_API_KEY}",
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"
      }
    }
  }
}
```

### 2.2 環境変数テンプレート

**タスク**:
- [ ] `.env.example`作成
- [ ] 各MCPサーバーの必要な環境変数を記載
- [ ] コメントで取得方法を説明

## Phase 3: エージェント移植

### 3.1 基本エージェント作成

**タスク**:
- [ ] `.kiro/agents/director.json` - タスク統括
- [ ] `.kiro/agents/data-analyst.json` - データ分析
- [ ] `.kiro/agents/corder.json` - コーディング
- [ ] `.kiro/agents/researcher.json` - 調査
- [ ] `.kiro/agents/scanner.json` - ファイル読み取り
- [ ] `.kiro/agents/qa.json` - 品質管理

**エージェント設定例** (director.json):
```json
{
  "description": "タスク統括・指揮者エージェント。複雑なタスクを分解し、適切なサブエージェントに委譲します。",
  "tools": ["@builtin", "@serena/*", "@github/*"],
  "allowedTools": ["fs_read", "fs_write", "execute_bash", "use_subagent"],
  "toolsSettings": {
    "execute_bash": {
      "autoAllowReadonly": true
    }
  },
  "resources": [
    "file://README.md",
    "file://docs/**/*.md"
  ],
  "hooks": {
    "agentSpawn": [
      {
        "command": "echo 'Director agent activated'",
        "description": "エージェント起動通知"
      }
    ]
  }
}
```

### 3.2 エージェント間連携

**タスク**:
- [ ] `use_subagent`ツールの活用方法をドキュメント化
- [ ] エージェント選択ガイドライン作成
- [ ] サブエージェント呼び出しパターン例

## Phase 4: プロンプト作成（コマンド代替）

### 4.1 開発ワークフロープロンプト

**タスク**:
- [ ] `.kiro/prompts/pr.md` - PR作成ワークフロー
- [ ] `.kiro/prompts/pr-tests.md` - Test Plan自動実行
- [ ] `.kiro/prompts/fix.md` - レビュー指摘修正
- [ ] `.kiro/prompts/review.md` - コードレビュー
- [ ] `.kiro/prompts/merged.md` - マージ後処理
- [ ] `.kiro/prompts/clean.md` - ブランチクリーンアップ

**プロンプト例** (pr.md):
```markdown
---
name: pr
description: Pull Request作成ワークフロー
---

# PR作成ワークフロー

このプロンプトは、Pull Request作成を支援します。

## 手順

1. 変更内容の確認
2. コミットメッセージの作成
3. PRタイトルと説明の生成
4. テストの実行確認

## 使用方法

```
@pr
```

## 実行内容

[詳細な手順...]
```

### 4.2 Memory管理プロンプト

**タスク**:
- [ ] `.kiro/prompts/mem-review.md` - Memory戦略レビュー
- [ ] `.kiro/prompts/mem-capture.md` - Memory記録

### 4.3 Serena操作プロンプト

**タスク**:
- [ ] `.kiro/prompts/serena.md` - Serena MCP操作ガイド

## Phase 5: スキル相当機能

### 5.1 スキルプロンプト作成

**タスク**:
- [ ] `.kiro/prompts/skills/sql-optimization.md`
- [ ] `.kiro/prompts/skills/code-templates.md`
- [ ] `.kiro/prompts/skills/test-generation.md`
- [ ] `.kiro/prompts/skills/pdf-analysis.md`
- [ ] `.kiro/prompts/skills/excel-extraction.md`
- [ ] `.kiro/prompts/skills/security-scan.md`
- [ ] `.kiro/prompts/skills/markdown-writing.md`
- [ ] `.kiro/prompts/skills/python-execution.md`
- [ ] `.kiro/prompts/skills/docker-access.md`

**スキルプロンプト例**:
```markdown
---
name: sql-optimization
description: SQL最適化支援
---

# SQL最適化

このプロンプトは、SQLクエリの最適化を支援します。

## 最適化パターン

1. インデックス活用
2. JOIN最適化
3. サブクエリ改善

[詳細...]
```

## Phase 6: フック実装

### 6.1 Slack通知フック

**タスク**:
- [ ] `scripts/slack-notify.sh`作成
- [ ] エージェント設定に`stop`フック追加
- [ ] Slack Webhook URL設定ガイド

**フック設定例**:
```json
{
  "hooks": {
    "stop": [
      {
        "command": "bash ${KIRO_CONFIG_ROOT}/scripts/slack-notify.sh",
        "description": "Slack通知送信"
      }
    ]
  }
}
```

### 6.2 セットアップフック

**タスク**:
- [ ] `scripts/setup-memory.sh`作成
- [ ] エージェント設定に`agentSpawn`フック追加

## Phase 7: インストールスクリプト

### 7.1 自動セットアップスクリプト

**タスク**:
- [ ] `scripts/install.sh`作成
- [ ] 以下の処理を自動化:
  - [ ] `.kiro/`ディレクトリのコピー
  - [ ] `.env`ファイルの作成（`.env.example`から）
  - [ ] MCP設定の確認
  - [ ] 必要なツールのインストール確認

**インストールスクリプト例**:
```bash
#!/bin/bash
set -e

echo "Kiro NDF Config インストール開始..."

# .kiro/ディレクトリをコピー
cp -r .kiro ~/.kiro/ndf-config

# .envファイル作成
if [ ! -f .env ]; then
  cp .env.example .env
  echo ".envファイルを作成しました。必要な環境変数を設定してください。"
fi

echo "インストール完了！"
```

## Phase 8: ドキュメント整備

### 8.1 ユーザーガイド

**タスク**:
- [ ] `docs/getting-started.md` - 初心者向けガイド
- [ ] `docs/agents-guide.md` - エージェント使用ガイド
- [ ] `docs/prompts-guide.md` - プロンプト使用ガイド
- [ ] `docs/mcp-setup.md` - MCP設定詳細
- [ ] `docs/troubleshooting.md` - トラブルシューティング

### 8.2 開発者ガイド

**タスク**:
- [ ] `docs/development.md` - カスタマイズガイド
- [ ] `docs/contributing.md` - コントリビューションガイド

## Phase 9: テストとデバッグ

### 9.1 機能テスト

**タスク**:
- [ ] 各エージェントの動作確認
- [ ] MCP統合の動作確認
- [ ] プロンプトの動作確認
- [ ] フックの動作確認

### 9.2 ドキュメントレビュー

**タスク**:
- [ ] インストール手順の検証
- [ ] 使用例の検証
- [ ] トラブルシューティングの検証

## Phase 10: リリース

### 10.1 バージョン管理

**タスク**:
- [ ] CHANGELOG.md作成
- [ ] バージョン1.0.0リリース
- [ ] GitHubリリースノート作成

### 10.2 公開

**タスク**:
- [ ] README.mdの最終確認
- [ ] リポジトリの公開設定
- [ ] コミュニティへの告知

## 成功基準

### 機能要件
- ✅ 7つのMCPサーバーが正常に動作
- ✅ 6つのエージェントが利用可能
- ✅ 9つのプロンプト（コマンド代替）が動作
- ✅ Slack通知フックが動作

### ドキュメント要件
- ✅ インストール手順が明確
- ✅ 使用方法が理解しやすい
- ✅ トラブルシューティングが充実

### ユーザー体験
- ✅ Claude Code NDFプラグインと同等の機能
- ✅ Kiro CLI固有の利点を活用
- ✅ 簡単にセットアップ可能

## リスクと対策

### リスク1: MCP互換性
**対策**: 各MCPサーバーの動作確認を徹底

### リスク2: エージェント設定の複雑さ
**対策**: デフォルト設定を提供、段階的なカスタマイズを推奨

### リスク3: ドキュメント不足
**対策**: 豊富な例とトラブルシューティングを用意

## タイムライン

- **Week 1-2**: Phase 1-3（基盤、MCP、エージェント）
- **Week 3-4**: Phase 4-6（プロンプト、スキル、フック）
- **Week 5**: Phase 7-8（インストール、ドキュメント）
- **Week 6**: Phase 9-10（テスト、リリース）

## 次のステップ

1. GitHubリポジトリ作成
2. 基本ディレクトリ構造の作成
3. MCP設定ファイルの作成
4. 最初のエージェント（director）の実装
