# NDF Plugin リファレンス

## 概要

NDFプラグインは、3つの既存プラグイン（mcp-integration、install-slack-hook、workflow-commands）を統合したオールインワンプラグインです。

## ディレクトリ構造

```
plugins/ndf/
├── .claude-plugin/
│   └── plugin.json          # プラグインメタデータ
├── .mcp.json                # MCPサーバー定義
├── hooks/
│   └── hooks.json           # Slack通知フック
├── scripts/
│   └── slack-notify.sh      # Slack通知スクリプト
├── commands/                # スラッシュコマンド（6個）
├── agents/                  # 専門エージェント（6個）
├── skills/                  # Skills（23個）
├── CLAUDE.ndf.md            # プラグイン利用者向けガイドライン
└── README.md                # 統合ドキュメント
```

## 機能

### 1. MCPサーバー

**注意**: GitHub MCP, Serena MCP, Context7 MCPは公式プラグインに移行済み。

NDF固有のMCPサーバー:
- **notion** (HTTP) - Notion統合
- **awslabs.aws-documentation-mcp-server** (stdio) - AWS公式ドキュメント
- **mcp-server-bigquery** (stdio) - BigQuery統合
- **dbhub** (stdio) - ユニバーサルデータベースゲートウェイ
- **chrome-devtools-mcp** (stdio) - Chromeブラウザ自動化
- **codex** (stdio) - AIコードレビュー

### 2. 開発ワークフローコマンド（6個）

- `/ndf:serena` - 開発記憶の記録
- `/ndf:pr` - PR作成
- `/ndf:fix` - PR修正対応
- `/ndf:review` - PRレビュー
- `/ndf:merge` - マージ後クリーンアップ
- `/ndf:clean` - ブランチクリーンアップ

### 3. 専門エージェント（6個）

| エージェント | 役割 |
|-------------|------|
| **director** | タスク統括・設計立案（オーケストレーター） |
| **data-analyst** | データ分析・SQL操作 |
| **corder** | 高品質コード生成 |
| **researcher** | 情報収集・分析 |
| **scanner** | ファイル読み取り（PDF、画像、Office） |
| **qa** | 品質管理・テスト |

### 4. Slack通知

Stopフックで自動実行。AIが作業内容を40文字以内の日本語で要約してSlackに通知。

**通知メカニズム:**
1. メンション付き投稿（通知音）
2. メッセージ削除
3. メンションなし再投稿（クリーンな履歴）

## 環境変数

**必須:**
- `GITHUB_PERSONAL_ACCESS_TOKEN` - GitHub MCP用

**オプション:**
- `NOTION_API_KEY` - Notion MCP用
- `GOOGLE_APPLICATION_CREDENTIALS` - BigQuery MCP用
- `DATABASE_DSN` - DBHub MCP用
- `SLACK_BOT_TOKEN` - Slack通知用（Bot User OAuth Token）
- `SLACK_CHANNEL_ID` - Slack通知送信先チャンネルID
- `SLACK_USER_MENTION` - Slackメンション対象ユーザーID

## 実装上の知見

### Stop Hook無限ループ防止

Stop hookスクリプト内でClaude CLIを呼び出す際は、`--settings`で無限ループを防止:

```bash
claude -p --settings '{"disableAllHooks": true, "disableAllPlugins": true}' --output-format text
```

**試行錯誤の結果:**
- `CLAUDE_DISABLE_HOOKS`環境変数 → 存在しない
- `stop_hook_active`フィールド → 実際には送信されない
- `--settings`でhooksとplugins両方を無効化 → 確実に動作

### 要約生成の3段階フォールバック

1. **Claude CLI**（優先）- AIによる高品質な要約
2. **transcript解析**（フォールバック1）- セッションログから抽出
3. **git diff**（フォールバック2）- ファイル変更から推測

### バージョン変遷

| バージョン | 主な変更 |
|-----------|---------|
| v1.0.0 | 初期リリース |
| v1.0.1 | 3プラグインを統合 |
| v1.0.6 | directorエージェント追加 |
| v1.2.0 | Skills導入（10個） |
| v2.0.0 | 公式プラグインとの重複解消（MCP 10→7） |
| v2.1.0 | directorエージェント再定義、Slack通知簡略化 |
| v3.0.0 | CLAUDE.ndf.md廃止、Serena MCP分離、3層構造移行 |
