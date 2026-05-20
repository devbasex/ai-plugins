# AI Plugins - Kiro CLI開発ガイドライン

共通の開発ガイドラインは **@AGENTS.md** を参照してください。

このファイルにはKiro CLI固有の設定のみを記載します。

## NDFプラグインのセットアップ

### 前提条件
- Kiro CLI がインストール済み
- Node.js（Slack通知を使う場合）
- Codex CLI（`/ndf:codex` skill で外部AI委譲を使う場合、または `--with-codex` で Kiro に Codex MCP サーバ設定を生成する場合）: `npm install -g @openai/codex`

### インストール

```bash
# 基本（Skills + agentSpawnフックのみ）
bash scripts/install-kiro.sh

# Slack通知も有効化
bash scripts/install-kiro.sh --with-slack

# 全部入り（Slack通知 + Kiro 側 Codex MCP 設定生成）
# 注: NDF v4.0.0 本体は Codex MCP に依存せず、/ndf:codex skill 経由で
# CLI 直接実行に一本化。--with-codex は Kiro セッションで
# `mcp__codex__*` を直接呼びたい場合のみ有効化すればよい。
bash scripts/install-kiro.sh --with-slack --with-codex
```

インストーラーは `plugin.json` からskills一覧を読み取り、`.kiro/agents/default.json` を生成します。

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
- plugin.jsonにskillが追加された場合、再度インストーラーを実行してください

## Kiro CLIでのコード探索

### 基本ワークフロー

#### 1. プロジェクト構造の把握
```bash
# ディレクトリ構造を確認
ls -la plugins/

# 特定のプラグインディレクトリを確認
ls -la plugins/ndf/
```

#### 2. コードインテリジェンスの活用

**シンボル検索**:
```
# プラグイン名でシンボルを検索
code search_symbols "ndf"

# 特定のファイル内のシンボル一覧
code get_document_symbols plugins/ndf/.claude-plugin/plugin.json
```

**パターン検索**:
```
# バージョン情報を検索
grep "version" --include="*.json"

# 特定のキーワードを検索
grep "MCP" --include="*.md"
```

#### 3. ファイル読み取り

```
# ファイル全体を読む
fs_read plugins/ndf/.claude-plugin/plugin.json

# 特定の行範囲を読む
fs_read plugins/ndf/README.md --start_line=1 --end_line=50
```

## Kiro CLI特有の注意事項

### サブエージェントの活用

複雑なタスクは専門のサブエージェントに委譲：

```
# 複数の独立したタスクを並列実行
use_subagent InvokeSubagents
```

### LSP統合

コードインテリジェンスを最大限活用：

```bash
# プロジェクトルートでLSP初期化（必要に応じて）
/code init
```

### AWS統合

AWS CLIを使用してリソース管理：

```bash
# S3バケット一覧
use_aws s3 list-buckets

# Lambda関数一覧
use_aws lambda list-functions
```
