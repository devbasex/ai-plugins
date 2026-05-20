# インストール方法と利用方法

## インストール方法

### 前提条件

**必須**:
- Kiro CLI がインストール済み
- Git
- Python 3.10以上（BigQuery MCP用）
- `uvx` がインストール済み（`pip install uv`）

**オプション**:
- Node.js（DBHub、Chrome DevTools MCP用）
- Codex CLI（Codex CLI MCP用）

### ステップ1: リポジトリのクローン

```bash
git clone https://github.com/takemi-ohama/kiro-ndf-config.git
cd kiro-ndf-config
```

### ステップ2: 自動インストール

```bash
./scripts/install.sh
```

このスクリプトは以下を実行します：
1. `.kiro/`ディレクトリをホームディレクトリにコピー
2. `.env`ファイルを作成（`.env.example`から）
3. 必要なツールのインストール確認

### ステップ3: 環境変数の設定

`.env`ファイルを編集し、必要な認証情報を設定します：

```bash
# Serena MCP (推奨)
SERENA_HOME=.serena
GOOGLE_API_KEY=your_google_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key

# Notion MCP (オプション)
NOTION_TOKEN=your_notion_token

# BigQuery MCP (オプション)
BIGQUERY_PROJECT=your_project_id
BIGQUERY_LOCATION=US
BIGQUERY_KEY_FILE=/path/to/service-account-key.json

# DBHub MCP (オプション)
DSN=your_database_connection_string

# Slack通知 (オプション)
SLACK_BOT_TOKEN=your_slack_bot_token
SLACK_CHANNEL_ID=your_channel_id
SLACK_USER_MENTION=<@U0123456789>
```

### ステップ4: MCP設定の確認

```bash
kiro-cli mcp
```

7つのMCPサーバーが表示されることを確認します。

### ステップ5: エージェントの確認

```bash
kiro-cli agent list
```

6つのエージェント（director、data-analyst、corder、researcher、scanner、qa）が表示されることを確認します。

## 利用方法

### 基本的な使い方

#### 1. エージェントの起動

```bash
# Directorエージェント（タスク統括）
kiro-cli chat --agent director

# データ分析エージェント
kiro-cli chat --agent data-analyst

# コーディングエージェント
kiro-cli chat --agent corder

# 調査エージェント
kiro-cli chat --agent researcher

# ファイル読み取りエージェント
kiro-cli chat --agent scanner

# 品質管理エージェント
kiro-cli chat --agent qa
```

#### 2. プロンプトの使用

プロンプトは`@`で呼び出します：

```bash
# PR作成ワークフロー
@pr

# Test Plan自動実行
@pr-tests

# レビュー指摘修正
@fix

# コードレビュー
@review

# マージ後処理
@merged

# ブランチクリーンアップ
@clean

# Memory戦略レビュー
@mem-review

# Memory記録
@mem-capture

# Serena MCP操作ガイド
@serena
```

#### 3. スキルプロンプトの使用

```bash
# SQL最適化
@sql-optimization

# コードテンプレート
@code-templates

# テスト生成
@test-generation

# PDF解析
@pdf-analysis

# Excel抽出
@excel-extraction

# セキュリティスキャン
@security-scan

# Markdown文書作成
@markdown-writing

# Python実行環境判定
@python-execution

# Dockerコンテナアクセス
@docker-access
```

### エージェント別の使用例

#### Directorエージェント（タスク統括）

複雑なタスクを分解し、適切なサブエージェントに委譲します。

```bash
kiro-cli chat --agent director

> 新しいREST APIを実装してください。認証、データベース接続、テストも含めて。

# Directorが以下のように分解:
# 1. Corderエージェント: API実装
# 2. Data-analystエージェント: データベース設計
# 3. QAエージェント: テスト作成
```

#### Data-analystエージェント（データ分析）

BigQuery、DBHubを使用したデータ分析。

```bash
kiro-cli chat --agent data-analyst

> BigQueryでユーザーの行動分析を実行してください

# BigQuery MCPを使用してクエリ実行
# 結果の可視化と分析
```

#### Corderエージェント（コーディング）

GitHub、Serenaを使用したコーディング支援。

```bash
kiro-cli chat --agent corder

> ユーザー認証機能を実装してください

# Serena MCPでコード構造を理解
# GitHub MCPでPR作成
```

#### Researcherエージェント（調査）

Web検索、AWS Docsを使用した調査。

```bash
kiro-cli chat --agent researcher

> AWS Lambdaのベストプラクティスを調査してください

# AWS Docs MCPで公式ドキュメント検索
# Web検索で最新情報収集
```

#### Scannerエージェント（ファイル読み取り）

PDF、Excelファイルの解析。

```bash
kiro-cli chat --agent scanner

> この請求書PDFから金額を抽出してください

# PDF解析スキルを使用
# 構造化データとして出力
```

#### QAエージェント（品質管理）

セキュリティスキャン、コードレビュー。

```bash
kiro-cli chat --agent qa

> このコードのセキュリティ脆弱性をチェックしてください

# セキュリティスキャンスキルを使用
# OWASP Top 10チェック
```

### ワークフロー例

#### PR作成ワークフロー

```bash
kiro-cli chat --agent corder

> @pr

# 1. 変更内容の確認
# 2. コミットメッセージの作成
# 3. PRタイトルと説明の生成
# 4. GitHub MCPでPR作成
```

#### レビュー対応ワークフロー

```bash
kiro-cli chat --agent corder

> @fix

# 1. PRのレビューコメント取得
# 2. 指摘事項の修正
# 3. コミット
# 4. レビュー対応コメント
```

#### マージ後処理ワークフロー

```bash
kiro-cli chat --agent director

> @merged

# 1. マージ確認
# 2. ローカルブランチの削除
# 3. リモートブランチの削除
# 4. mainブランチの更新
```

### MCP統合の活用

#### Serena MCP（セマンティックコード操作）

```bash
kiro-cli chat --agent corder

> Serenaを使ってこのファイルのシンボル一覧を取得してください

# Serena MCPのget_symbols_overviewを使用
```

#### Notion MCP（Notion統合）

```bash
kiro-cli chat --agent researcher

> Notionのプロジェクトページを更新してください

# Notion MCPでページ更新
```

#### BigQuery MCP（BigQueryクエリ）

```bash
kiro-cli chat --agent data-analyst

> BigQueryでユーザーテーブルから集計してください

# BigQuery MCPでクエリ実行
```

### フックの活用

#### Slack通知

エージェント終了時に自動的にSlack通知が送信されます。

```bash
# .envファイルでSlack設定
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>

# エージェント終了時に自動通知
kiro-cli chat --agent director
> タスク完了
> /quit

# Slackに通知が送信される
```

## トラブルシューティング

### MCPサーバーが起動しない

```bash
# MCP設定の確認
kiro-cli mcp

# ログの確認
kiro-cli logdump

# 環境変数の確認
cat .env
```

### エージェントが見つからない

```bash
# エージェント一覧の確認
kiro-cli agent list

# エージェント設定ファイルの確認
ls ~/.kiro/agents/
```

### プロンプトが動作しない

```bash
# プロンプト一覧の確認
kiro-cli prompts

# プロンプトファイルの確認
ls ~/.kiro/prompts/
```

## アップデート

```bash
cd kiro-ndf-config
git pull
./scripts/install.sh
```

## アンインストール

```bash
# エージェント設定の削除
rm -rf ~/.kiro/agents/director.json
rm -rf ~/.kiro/agents/data-analyst.json
rm -rf ~/.kiro/agents/corder.json
rm -rf ~/.kiro/agents/researcher.json
rm -rf ~/.kiro/agents/scanner.json
rm -rf ~/.kiro/agents/qa.json

# プロンプトの削除
rm -rf ~/.kiro/prompts/pr.md
rm -rf ~/.kiro/prompts/pr-tests.md
rm -rf ~/.kiro/prompts/fix.md
rm -rf ~/.kiro/prompts/review.md
rm -rf ~/.kiro/prompts/merged.md
rm -rf ~/.kiro/prompts/clean.md
rm -rf ~/.kiro/prompts/mem-review.md
rm -rf ~/.kiro/prompts/mem-capture.md
rm -rf ~/.kiro/prompts/serena.md

# MCP設定の削除
rm -rf ~/.kiro/mcp.json
```

## サポート

問題が発生した場合：
1. [トラブルシューティングガイド](docs/troubleshooting.md)を確認
2. [GitHubイシュー](https://github.com/takemi-ohama/kiro-ndf-config/issues)を作成
3. [ドキュメント](docs/)を参照

## 参考リンク

- [Kiro CLI公式ドキュメント](https://kiro.dev/docs/cli/)
- [MCP仕様](https://modelcontextprotocol.io)
- [Serena MCP](https://github.com/oraios/serena)
- [元のNDFプラグイン](https://github.com/takemi-ohama/ai-plugins/tree/main/plugins/ndf)
