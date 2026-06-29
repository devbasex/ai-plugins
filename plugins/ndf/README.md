# NDF Plugin

Claude Code / Codex開発環境を**オールインワン**で強化する統合プラグインです。

## 概要

このプラグイン1つで、以下の機能を利用できます：

1. **コアMCP**: なし (v4.0.0 で Codex MCP 廃止 / Serena MCP は `mcp-serena` プラグインに分離)
2. **Skills**: Claude Code/Kiro向け core 27個、Codex向け core 29個を公開。元の48個は `skills/` に保持し、ランタイム別の除外候補は `skills-optional/README.md` で管理。
3. **専門エージェント**: 8つの特化型AIエージェント（director、data-analyst、corder、researcher、qa、debugger、devops-engineer、code-reviewer）
4. **自動フック**: Slack通知、デフォルトstatusline設定（未設定時のみ）

> **Note (v2.7.0)**: commandsとskillsが統合されました。全ワークフロー（`/ndf:pr`等）はskillsとして実装されています。追加のMCP（BigQuery、Chrome DevTools、AWS Docs、DBHub、Notion）は個別プラグインとしてインストール可能です。

## インストール

### 前提条件

- Claude Code がインストール済み
- Python 3.10以上（Serena MCP `mcp-serena` プラグイン用、別途インストール）
- `uvx` がインストール済み（`pip install uv`）
- Codex CLI（`/ndf:codex` skill で外部AI委譲を使う場合、オプション）: `npm install -g @openai/codex`

### 公式プラグインのインストール（推奨）

GitHub、Context7 MCPは公式プラグインとして提供されています：

```bash
# Claude Codeで実行
/plugin install github@anthropics/claude-plugins-official
/plugin install context7@anthropics/claude-plugins-official
```

### 追加MCPプラグインのインストール（オプション）

用途に応じて個別のMCPプラグインをインストールできます：

```bash
# ブラウザ自動化とテスト
/plugin install mcp-chrome-devtools@ai-plugins

# データ分析
/plugin install mcp-bigquery@ai-plugins

# AWS公式ドキュメント調査
/plugin install mcp-aws-docs@ai-plugins

# データベース操作
/plugin install mcp-dbhub@ai-plugins

# Notion統合
/plugin install mcp-notion@ai-plugins
```

### ステップ1: マーケットプレイスの追加

```bash
# Claude Codeで実行
/plugin marketplace add https://github.com/devbasex/ai-plugins
```

### ステップ2: プラグインのインストール

```bash
# Claude Codeで実行
/plugin install ndf@ai-plugins
```

### Codexでのインストール

```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

Codex版ではSkillsに加えて、Codex向けSlack終了通知hookを同梱します。通知は明示的に `NDF_CODEX_SLACK_NOTIFY=true` を設定した場合のみ送信されます。Claude Code向けのstatusline設定、transcript保持期間設定、Claude CLIによるSlack要約通知hookはCodexでは自動有効化しません。

Claude Code/Kiro版で初期表示するSkillsは、PR運用・レビュー・調査・実装計画・browser smoke test・statusline・Codex CLI委譲・cross-reviewなどの core 27個に絞っています。Codex版は、Codex自体からの単発再委譲・Claude専用statusline・Claude transcript統計を外し、PR cross-review と Playwrightの計画・作成・実行・レポート・ツール操作の5個を含めた core 29個にしています。Google連携、DeepWiki転送、MLモデル構造、高度なPlaywright連携などは通常利用時のskills context budgetを圧迫しないよう初期公開から外し、`skills-optional/README.md` に整理しています。

Codex向けSlack通知を使う場合は、Claude Code向けSlack通知と同じ環境変数を使います。プロジェクトの `.env` などに以下を設定してください。

```bash
NDF_CODEX_SLACK_NOTIFY=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>  # オプション
```

Codexのhookは初回実行前に `/hooks` で信頼設定が必要です。

### ステップ3: .envファイルの作成

プロジェクトルートに `.env` ファイルを作成し、必要な認証情報を設定します。

```bash
# Serena MCP (セマンティックコード操作 - 推奨)
SERENA_HOME=.serena

# Slack通知 (オプション)
# Slack Appセットアップ手順は下記の詳細設定を参照
SLACK_BOT_TOKEN=
SLACK_CHANNEL_ID=
SLACK_USER_MENTION=  # 例: <@U0123456789>

# 注意:
# - Serena MCP は別プラグイン `mcp-serena` として提供 (常時有効化推奨、GOOGLE_API_KEY/ANTHROPIC_API_KEY不要)
# - Codex CLI は `/ndf:codex` skill から使う場合のみ必要: `npm install -g @openai/codex` → `codex login`
#   (v4.0.0 で Codex MCP サーバは廃止。skill 経由の CLI 直接実行に一本化)
# - GitHub MCP、Context7 MCPは公式プラグインを使用してください
# - 追加のMCP（BigQuery、Notion、AWS Docs、DBHub、Chrome DevTools）は
#   個別プラグインとしてインストール可能です（下記参照）
```

#### .envファイルの保護

`.env` ファイルには機密情報が含まれるため、必ず `.gitignore` に追加してください：

```bash
echo ".env" >> .gitignore
```

#### 各認証情報の詳細設定

<details>
<summary><strong>DSN（DATABASE_DSN）の設定方法（DBHub MCP用）</strong></summary>

DBHub MCPは複数のデータベースに対応しています。環境変数名は`DSN`または`DATABASE_DSN`のどちらでも使用可能です。

**PostgreSQL:**
```bash
DSN="postgres://USERNAME:PASSWORD@HOST:PORT/DATABASE?sslmode=disable"
```

SSL接続が必要な場合：
```bash
DSN="postgres://USERNAME:PASSWORD@HOST:PORT/DATABASE?sslmode=require"
```

**MySQL / MariaDB:**
```bash
DSN="mysql://USERNAME:PASSWORD@HOST:PORT/DATABASE"
```

SSH踏み台サーバー経由で接続する場合：
1. SSHトンネルを作成（ローカルポート転送）
   ```bash
   ssh -L 3307:DB_HOST:3306 USER@BASTION_HOST -N
   ```
2. ローカルポート経由で接続
   ```bash
   DSN="mysql://USERNAME:PASSWORD@localhost:3307/DATABASE"
   ```

**SQLite:**
```bash
DSN="sqlite:///PATH/TO/DATABASE.db"
```

**SQL Server:**
```bash
DSN="sqlserver://USERNAME:PASSWORD@HOST:PORT?database=DATABASE"
```

**注意事項:**
- パスワードに特殊文字が含まれる場合は、URLエンコードが必要（例: `@` → `%40`, `#` → `%23`）
- ローカルデータベースの場合は `localhost` を使用
- `DSN`と`DATABASE_DSN`は同じ意味（どちらを使用してもOK）

</details>

<details>
<summary><strong>SLACK_BOT_TOKEN と SLACK_CHANNEL_ID の設定方法</strong></summary>

Slack通知を有効にするには、Slack Appを作成してBot Tokenとチャンネル IDを取得する必要があります。

**ステップ1: Slack Appの作成**

1. https://api.slack.com/apps にアクセス
2. "Create New App" をクリック
3. "From scratch" を選択
4. App名を入力（例: "Claude Code Notifier"）
5. Workspaceを選択

**ステップ2: Bot Token Scopesの追加**

1. 左メニューから "OAuth & Permissions" を選択
2. "Scopes" セクションまでスクロール
3. "Bot Token Scopes" で以下を追加：
   - `chat:write` （必須 - メッセージ投稿用）
   - `chat:write.public` （推奨 - 公開チャンネル投稿用）
   - `channels:read` （オプション - チャンネル情報取得用）

**ステップ3: Workspaceへのインストール**

1. ページ上部の "Install to Workspace" をクリック
2. 権限を確認して "Allow" をクリック
3. **Bot User OAuth Token** をコピー（`xoxb-`で始まる）
   → これを `.env` の `SLACK_BOT_TOKEN` に設定

**ステップ4: Botをチャンネルに追加**

1. Slackで通知先チャンネルを開く
2. チャンネル名をクリック → "Integrations" タブ
3. "Add apps" をクリック
4. 作成したアプリを選択して追加

**ステップ5: チャンネルIDの取得**

1. 通知先チャンネルを開く
2. チャンネル名をクリック
3. 下部の「その他」→ チャンネルIDをコピー（`C`で始まる）
   → これを `.env` の `SLACK_CHANNEL_ID` に設定

**ステップ6: ユーザーIDの取得（オプション - 通知音用）**

1. Slackでプロフィールを開く
2. "その他" → "メンバーIDをコピー"（`U`で始まる）
3. メンション形式で設定：
   ```bash
   SLACK_USER_MENTION="<@U0123456789>"
   ```

**最終的な.env設定例:**
```bash
SLACK_BOT_TOKEN="xoxb-YOUR-BOT-TOKEN-HERE"
SLACK_CHANNEL_ID="C0123456789"
SLACK_USER_MENTION="<@U0123456789>"  # オプション
```

**セキュリティ注意:**
- Bot Tokenは絶対にGitにコミットしない
- トークンが漏洩した場合は即座に無効化（Revoke）
- 最小限の権限（Scope）のみを付与

</details>

### ステップ4: Claude Codeを再起動

`.env` ファイルに値を入力したら、Claude Codeを再起動してMCPサーバーとフックをロードします。

## 利用方法

セットアップが完了したら、Claude Codeで自然言語でリクエストするだけです：

```
このリポジトリのオープンなPRを確認して
```

Claude Codeが自動的に適切なMCPツールを選択・利用します。

また、ワークフロースキルをスラッシュコマンドで呼び出せます：

```
/ndf:pr
/ndf:review 123
/ndf:merged
```

## 機能詳細

> **v2.7.0**: commandsとskillsが統合されました。ワークフロー（`/ndf:*`）もモデル起動型機能もすべてskillsとして実装されています。

### 1. MCP統合

このプラグイン自体は v4.0.0 で **コアMCP サーバを同梱しなくなりました**。関連MCPは個別プラグインとしてインストールしてください。

> **Note (v4.0.0 BREAKING)**: Codex MCP サーバは削除。Codex CLI は `/ndf:codex` skill から直接呼び出す方式に一本化。Serena MCP は別プラグイン `mcp-serena` へ分離済み。

**外部AI委譲（MCP 非使用）:**
- 🔧 **Codex CLI** - `/ndf:codex` skill または `corder` エージェント経由で `codex exec` をバックグラウンド実行

**個別プラグインとして提供（5つ）:**
- 📦 **Chrome DevTools MCP** (`mcp-chrome-devtools`) - Web調査、パフォーマンステスト
- 📦 **BigQuery MCP** (`mcp-bigquery`) - BigQueryデータ分析
- 📦 **AWS Docs MCP** (`mcp-aws-docs`) - AWS公式ドキュメント検索
- 📦 **DBHub MCP** (`mcp-dbhub`) - データベース操作
- 📦 **Notion MCP** (`mcp-notion`) - Notionドキュメント管理

> **Tip**: GitHub MCP、Context7 MCPは公式プラグインからインストールして使用してください：
> ```bash
> /plugin install github@anthropics/claude-plugins-official
> /plugin install context7@anthropics/claude-plugins-official
> ```

#### 追加MCPのインストール方法

必要に応じて個別のMCPプラグインをインストールできます：

```bash
# ブラウザ自動化とテスト
/plugin install mcp-chrome-devtools@ai-plugins

# データ分析
/plugin install mcp-bigquery@ai-plugins

# AWS公式ドキュメント調査
/plugin install mcp-aws-docs@ai-plugins

# データベース操作
/plugin install mcp-dbhub@ai-plugins

# Notion統合
/plugin install mcp-notion@ai-plugins
```

各プラグインのインストール後、対応する環境変数を`.env`に設定してClaude Codeを再起動してください。

**注意事項:**
- 各MCPプラグインは独立しているため、必要なものだけをインストールできます
- コンテキスト使用量を最適化するため、使わないMCPはインストールしないことを推奨します
- 各プラグインの詳細な設定方法は、個別のREADMEを参照してください

### 2. 専門エージェント (8種類)

**サブエージェントの活用方針:**
- **複雑なタスクは`director`に委譲** - directorがMain Agentに報告し、Main Agentが他のエージェントを起動
- **単純なタスクは専門エージェントに直接委譲**
- **directorはMain Agentに報告する** - メモリエラー防止のため直接呼び出しは行わない

詳細はメインセッションに自動注入される `ndf-policies` skill および `plugins/ndf/AGENTS.md` を参照してください。

#### `director` エージェント（指揮者）
**専門領域:** タスク統括・設計立案・エージェント調整

**特徴:**
- **Main Agentに報告** - 必要なエージェントをMain Agentに報告し、Main Agentがサブエージェントを起動（メモリエラー防止）
- Claude Code機能（Plan Mode、Explore Agent、TodoWrite）を活用
- タスク規模に応じた適切な対応（小規模→直接処理、大規模→Plan Mode）
- Main agentのコンテキスト消費を最小化
- **計画・調査結果をファイルに保存** - 途中停止からの復帰を可能に

**機能:**
- タスク分析と規模判定（小/中/大）
- 複数エージェントの並列/順次実行の計画立案
- 進捗管理（TodoWrite）
- 設計計画の策定と**ファイル保存**（`issues/`, `docs/`, `specs/`）
- **Main Agentへの報告** - 必要なエージェントと実行順序を明示

**使用例:**
```
@director ユーザー認証機能を追加してください。ベストプラクティスを調査し、実装してセキュリティレビューも行ってください
```
→ directorが調査・計画後、Main Agentに「researcher → corder → qaの順次実行が必要」と報告
→ Main Agentが報告に基づきエージェントを起動

#### `data-analyst` エージェント
**専門領域:** データ分析とSQL操作

**使用MCPツール:**
- BigQuery MCP
- DBHub MCP

**機能:**
- SQL生成と実行
- クエリ結果の分析と解釈
- データの傾向とパターン発見
- CSV/JSON/Excel形式でのデータ出力
- レポート生成とデータ可視化の準備

**使用例:**
```
@data-analyst BigQueryで過去1ヶ月の売上データを分析してください
```

#### `corder` エージェント
**専門領域:** 高品質コード生成

**使用MCPツール:**
- Codex CLI (`/ndf:codex` skill または `corder` エージェント経由、MCP非使用)（コードレビュー）

> **Note (v2.0.0)**: Serena MCP、Context7 MCPは公式プラグインに移行しました。これらは引き続きcorderエージェントで使用可能ですが、別途インストールが必要です。

**機能:**
- クリーンで読みやすいコードの作成
- 設計パターンとアーキテクチャの適用
- AIによるコードレビューと品質保証
- セキュリティ脆弱性のチェック
- リファクタリング提案

**使用例:**
```
@corder ユーザー認証機能を実装してください。セキュリティとテストも考慮して
```

#### `researcher` エージェント
**専門領域:** 情報収集と分析

**使用MCPツール:**
- Codex CLI (`/ndf:codex` skill または `corder` エージェント経由、MCP非使用)（コードベース分析）
- AWS Documentation MCP（AWS公式ドキュメント）
- Chrome DevTools MCP（Webスクレイピング）

**機能:**
- 技術ドキュメントの調査と要約
- Webサイトからの情報収集
- コードベースのアーキテクチャ分析
- 複数ソースからの情報統合
- スクリーンショットとPDF取得

**使用例:**
```
@researcher AWS Lambda関数のベストプラクティスを調査してください
```

#### `qa` エージェント
**専門領域:** 品質管理とテスト

**使用MCPツール:**
- Codex CLI (`/ndf:codex` skill または `corder` エージェント経由、MCP非使用)（コードレビュー、セキュリティチェック）
- Serena MCP（コードベース分析）
- Chrome DevTools MCP（パフォーマンステスト）
- Claude Code MCP（プラグイン品質検証）

**機能:**
- コード品質レビューとリファクタリング提案
- セキュリティ脆弱性検出（OWASP Top 10対応）
- パフォーマンステスト（Core Web Vitals評価）
- テストカバレッジとエッジケース検証
- ドキュメント品質チェック
- Claude Codeプラグイン仕様準拠確認

**使用例:**
```
@qa このコードの品質とセキュリティをレビューしてください
@qa Webアプリケーションのパフォーマンスを測定してください
@qa プラグインがClaude Code仕様に準拠しているか確認してください
```

#### `debugger` エージェント
**専門領域:** エラー・バグの根本原因分析

**機能:**
- スタックトレース・ログ・失敗テストからの原因特定
- 症状ではなく根本原因の究明
- 再現手順の整理
- 最小限の修正案の提示

**使用例:**
```
@debugger このスタックトレースから例外の原因を特定してください
```

#### `devops-engineer` エージェント
**専門領域:** コンテナ・CI/CD・インフラ

**機能:**
- Dockerfile / docker-compose の作成・最適化
- GitHub Actions ワークフロー設計
- Kubernetes マニフェスト作成
- CI/CD パイプラインのデバッグ
- インフラ IaC

**使用例:**
```
@devops-engineer このアプリのDockerfileを最適化してください
```

#### `code-reviewer` エージェント
**専門領域:** git diff / PR差分の一般コードレビュー

**機能:**
- 可読性・設計・バグ・セキュリティ・テスト観点のチェック
- 重要度順の指摘整理
- Claude 単体で外部AIを使わず素早くレビュー（外部AI第二意見が必要な場合は `corder` を利用）

**使用例:**
```
@code-reviewer 直近のコミットの差分をレビューしてください
```

### 3. PR/コードレビューワークフロースキル（13個）

開発の各段階で使用するスキル群です。`/ndf:*` のスラッシュコマンドで呼び出します。

#### commit / push / PR作成
| スキル | 用途 | 引数 |
|--------|------|------|
| `/ndf:pr` | commit, push, PR作成/既存PR説明更新を一括実行 | `[--draft] [base-branch] or [msg]` |
| `/ndf:pr-tests` | PRのTest Planを自動実行 | `[PR番号]` |
| `/ndf:cherry-pick-pr` | 環境ブランチへのcherry-pick PR作成 | `<base-branch>` |
| `/ndf:deploy` | 環境ブランチへのデプロイPR作成（ブランチ全体をmain経由で） | `<env-branch>` |
| `/ndf:sync-main` | 最新mainを現在のブランチに取り込み | - |

#### レビュー/修正
| スキル | 用途 | 引数 |
|--------|------|------|
| `/ndf:review` | PR単位レビュー（Approve/Request Changes判定） | `[PR番号]` |
| `/ndf:review-branch` | ローカル差分レビュー（PR前、mainとの比較） | `[focus-area]` |
| `/ndf:review-pr-comments` | PRコメント分類・優先度判定（READ-ONLY） | `[PR番号]` |
| `/ndf:fix` | PRレビューコメントの修正対応 | `[PR番号]` |
| `/ndf:resolve-pr-comments` | 対応済みコメント返信＋スレッドResolve | `[PR番号]` |
| `/ndf:browser-test` | ブラウザで動作確認（Playwright/Chrome DevTools MCP必要） | `[url]` |

#### マージ後クリーンアップ
| スキル | 用途 | 引数 |
|--------|------|------|
| `/ndf:merged` | PRマージ後のローカルブランチクリーンアップ | `[PR番号]` |
| `/ndf:clean` | マージ済みブランチの一括削除 | - |

### 4. 原則・ガイドライン系スキル（9個）

モデル起動型のガイドラインスキル。該当する文脈で自動参照される。

| スキル名 | 概要 |
|---------|------|
| `ndf-policies` | プラグイン基本ポリシー（常時注入） |
| `branch-fix-strategy` | 複数ブランチへの修正適用戦略（cherry-pick） |
| `implementation-plan` | `issues/`配下の実装プラン管理 |
| `investigation-rules` | 調査レポートのエビデンス主義 |
| `problem-solving` | 根本原因分析・上流修正・多層防御 |
| `logging-guidelines` | ログ運用ガイドライン（言語非依存） |
| `markdown-writing` | Markdown文書作成（mermaid/plantUML） |
| `issue-plan-strategy` | issue→plan作成・multi-PR実行のワークフロー戦略 |
| `ml-model-structure` | MLモデル構築・推論API開発の標準ディレクトリ構造（版内feature SSoT / train↔serve契約） |

### 5. データ分析・品質・環境系スキル（12個）

Claudeが自律的に判断して起動するスキル群。

| カテゴリ | スキル名 | 概要 |
|---------|---------|------|
| Data Analyst | `data-analyst-sql-optimization` | SQL最適化パターン（N+1、INDEX、JOIN） |
| | `data-analyst-export` | CSV/JSON/Excel/Markdownエクスポート |
| QA | `qa-security-scan` | OWASP Top 10セキュリティスキャン |
| Dev環境 | `python-execution` | Python実行環境の自動判定 |
| | `docker-container-access` | Dockerコンテナアクセス判定 |
| | `git-gh-operations` | git/gh操作の共通パターン |
| | `google-auth` | Google API OAuth2認証 |
| | `codex` | Codex CLI直接実行（corderエージェントとの使い分け） |
| 知識管理 | `deepwiki-transfer` | DeepWikiからの知識転送 |
| | `knowledge-reorg` | 知識再編成 |
| 公式連携 | `mcp-builder` | MCPサーバー作成ガイド（Anthropic公式、Apache-2.0） |
| | `official-skills-autoloader` | Anthropic公式Skillの自動ロード |

### 6. E2Eテスト（Playwright）系スキル（6個）

pytest-playwright ベースの E2E テストワークフロー。

| スキル名 | 概要 |
|---------|------|
| `playwright-test-planning` | HTSM/ISTQB に基づくテスト計画立案・page role 分類 |
| `playwright-script-creation` | 再現可能なテストスクリプト作成 |
| `playwright-execution` | テスト実行＋エビデンス収集（動画/trace/a11y/CWV） |
| `playwright-report` | テスト結果の Markdown レポート生成 |
| `playwright-kit-ops` | playwright_kit スクリプト操作（init/分類/スキャン） |
| `playwright-scenario-test` | フル E2E ワークフロー統括（計画→作成→実行→レポート） |

### 7. 外部サービス連携系スキル（2個）

| スキル名 | 概要 |
|---------|------|
| `google-drive` | Google Drive/Docs のエクスポート・アップロード（公開共有リンク付与） |
| `google-chat` | Google Chat スペースのメッセージ取得・スペース一覧 |

### 8. AIクロスレビュー系スキル（2個）

外部 AI に第二意見レビュー/調査を委譲する。

| スキル名 | 概要 |
|---------|------|
| `cross-review` | PR を codex/gemini 両方でレビューし APPROVE まで自動ループ。Gemini 進捗 heartbeat、追加レビュー観点、自動レビュー観点テンプレートに対応 |
| `gemini` | gemini CLI 直接実行（コード生成/レビュー/調査） |

`cross-review` の代表的な呼び出し例:

```bash
/ndf:cross-review 123
/ndf:cross-review 123 --focus "ドキュメントとコードの整合性を重点的に確認"
/ndf:cross-review 123 --extra-instructions-file /tmp/review-focus.md
```

v4.18.0 以降、`cross-review` は PR の変更ファイルを GitHub API で全件取得し、
docs only / code / DB migration / test / dependency / CI設定 / API契約 / 認証認可 /
frontend / performance / deletion / generated / i18n / infra を自動分類する。
分類結果に応じて、ドキュメント妥当性、コード品質、DB設計、型、セキュリティ、
可読性、重複、言語らしさ、関数/ファイルサイズ、設定差分などの観点を codex /
gemini 両方のプロンプトに追加する。`--focus` と `--extra-instructions-file` は、
この自動テンプレートの後ろに上乗せされる。

Gemini 実行中は `progress.log` の短い進捗マーカーを `monitor.py` が heartbeat に
表示する。内部推論は出さず、`scan` / `analyze` / `post` / `done` などの作業段階、
無更新秒数、stdout/stderr/progress log サイズを確認できる。

### 9. 運用系スキル（2個）

| スキル名 | 概要 |
|---------|------|
| `skill-stats` | Skill 利用統計の集計（呼び出し数/ヒット率） |
| `statusline` | NDF標準statusline（コンテナ名/ホスト名 + project_dir + コンテキスト使用率）の切り替え・復元・状態確認 |

### 10. 自動フック

Claude Codeの起動時と終了時に自動的に以下が実行されます：

#### SessionStart: デフォルトstatusline設定

セッション開始時に `~/.claude/settings.json` を検査し、`statusLine` が**未設定の場合のみ**
NDF標準statusline（コンテナ名/ホスト名 + project_dir + コンテキスト使用率）を設定します。

- 既にstatuslineが設定されている場合はそちらを優先し、何も変更しません
- `/ndf:statusline set` / `/ndf:statusline restore` でいつでも切り替え・復元できます（既存設定は自動バックアップ）
- NDF標準statusline利用中は、プラグイン更新時にスクリプト（`~/.claude/ndf-statusline.sh`）が自動で追従します

#### Stop: Slack通知

作業終了時にSlackへ要約通知を送信します（`SLACK_BOT_TOKEN`設定時のみ）。

**機能:**
- Claude Codeとのやり取りをAIが自動要約（40文字）
- Claude CLI + `--no-session-persistence`を使用（要約生成時に追加のセッションログを作成しない）
- Claude Codeの認証設定を自動継承（API KeyでもBedrockでも対応）
- 会話履歴、transcriptから最適な情報源を自動選択
- リポジトリ名とタイムスタンプも含めて通知

**設定:**
- `.env`に`SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID`、`SLACK_USER_MENTION`を設定
- 詳細な設定手順は上記の[SLACK_BOT_TOKENとSLACK_CHANNEL_IDの設定方法](#各認証情報の詳細設定)を参照
- 設定後、Claude Codeを再起動で有効化

**注意:**
- プラグイン更新後も再起動が必要です

## 環境変数リファレンス

各MCPサーバーが使用する環境変数の完全な一覧です。太字は必須、通常テキストはオプションです。

### 📋 環境変数テンプレート

プロジェクトルートに以下の`.env`ファイルを作成してください：

```bash
# ============================================
# Serena MCP - セマンティックコード操作
# ============================================
SERENA_HOME=.serena

# ============================================
# Codex CLI - /ndf:codex skill 経由で使用 (v4.0.0でMCPサーバは廃止)
# ============================================
# すべてオプション。npm install -g @openai/codex → codex login で利用可
# CODEX_HOME=/path/to/codex/home
# OPENAI_API_KEY=your-openai-api-key
# OPENAI_BASE_URL=https://api.openai.com/v1

# ============================================
# Slack通知 - 自動フック
# ============================================
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
SLACK_CHANNEL_ID=C0123456789
SLACK_USER_MENTION=<@U0123456789>
```

**追加のMCPプラグインが必要な場合:**

以下のMCPは個別プラグインとしてインストール可能です。必要に応じて環境変数を設定してください。

```bash
# ============================================
# Chrome DevTools MCP (mcp-chrome-devtools)
# ============================================
# 専用環境変数なし - envFileのみ

# ============================================
# Notion MCP (mcp-notion)
# ============================================
NOTION_TOKEN=your-notion-token-here

# ============================================
# AWS Docs MCP (mcp-aws-docs)
# ============================================
# FASTMCP_LOG_LEVEL=ERROR
# AWS_DOCUMENTATION_PARTITION=aws

# ============================================
# BigQuery MCP (mcp-bigquery)
# ============================================
BIGQUERY_PROJECT=your-gcp-project-id
BIGQUERY_LOCATION=US
# BIGQUERY_KEY_FILE=/path/to/service-account-key.json

# ============================================
# DBHub MCP (mcp-dbhub)
# ============================================
DSN=mysql://user:password@host:3306/database
```

### 📊 MCPサーバー別環境変数詳細

> **Note (v2.6.0)**: NDFプラグインはコアMCP（Serena、Codex）のみを含みます。GitHub MCP、Context7 MCPは公式プラグイン（`anthropics/claude-plugins-official`）から、その他のMCP（BigQuery、Notion、AWS Docs、DBHub、Chrome DevTools）は個別プラグインとしてインストールしてください。

#### 1. Serena MCP（コアMCP）

| 環境変数 | 必須/オプション | デフォルト値 | 説明 |
|---------|--------------|------------|------|
| SERENA_HOME | オプション | `.serena` | Serenaのホームディレクトリ |

**注意:**
- GOOGLE_API_KEY、ANTHROPIC_API_KEYは不要です（自動検出）
- Claude CodeのAPI設定を自動的に継承します

#### 2. Codex CLI（`/ndf:codex` skill 経由、v4.0.0 でMCPサーバは廃止）

| 環境変数 | 必須/オプション | デフォルト値 | 説明 |
|---------|--------------|------------|------|
| CODEX_HOME | オプション | `~/.codex` | Codex CLIのホームディレクトリ |
| OPENAI_API_KEY | オプション | - | OpenAI APIキー (`codex login` 済みなら不要) |
| OPENAI_BASE_URL | オプション | `https://api.openai.com/v1` | OpenAI APIのベースURL |

**注意:** `/ndf:codex` skill や `corder` エージェントを使う場合、事前に Codex CLI をインストールし、`codex login` を実行してください。

```bash
npm install -g @openai/codex
codex login
```

---

**以下のMCPは個別プラグインとしてインストール可能です:**

#### 3. Chrome DevTools MCP (mcp-chrome-devtools)

```bash
/plugin install mcp-chrome-devtools@ai-plugins
```

**専用環境変数なし** - `.env`ファイルの`envFile`設定のみ使用。

#### 4. Notion MCP (mcp-notion)

```bash
/plugin install mcp-notion@ai-plugins
```

| 環境変数 | 必須/オプション | デフォルト値 | 説明 |
|---------|--------------|------------|------|
| **NOTION_TOKEN** | **必須** | - | Notion Internal Integration Token<br>取得: https://www.notion.so/my-integrations |

#### 5. AWS Docs MCP (mcp-aws-docs)

```bash
/plugin install mcp-aws-docs@ai-plugins
```

| 環境変数 | 必須/オプション | デフォルト値 | 説明 |
|---------|--------------|------------|------|
| FASTMCP_LOG_LEVEL | オプション | `ERROR` | ログレベル |
| AWS_DOCUMENTATION_PARTITION | オプション | `aws` | AWSパーティション |

**注意:** 認証不要でAWS公式ドキュメントにアクセスできます。

#### 6. BigQuery MCP (mcp-bigquery)

```bash
/plugin install mcp-bigquery@ai-plugins
```

| 環境変数 | 必須/オプション | デフォルト値 | 説明 |
|---------|--------------|------------|------|
| **BIGQUERY_PROJECT** | **必須** | - | GCPプロジェクトID |
| BIGQUERY_LOCATION | オプション | `US` | BigQueryデータセットのロケーション |
| BIGQUERY_KEY_FILE | オプション | - | サービスアカウントキーファイルのパス |

#### 7. DBHub MCP (mcp-dbhub)

```bash
/plugin install mcp-dbhub@ai-plugins
```

| 環境変数 | 必須/オプション | デフォルト値 | 説明 |
|---------|--------------|------------|------|
| **DSN** | **必須** | - | データベース接続文字列<br>例: `mysql://user:pass@host:3306/db` |

DSN形式の詳細については、各プラグインのREADMEを参照してください。

### 🔐 Slack通知の環境変数

| 環境変数 | 必須/オプション | デフォルト値 | 説明 |
|---------|--------------|------------|------|
| SLACK_BOT_TOKEN | 必須（Slack通知用） | - | Slack Bot User OAuth Token（`xoxb-`で始まる） |
| SLACK_CHANNEL_ID | 必須（Slack通知用） | - | 通知先チャンネルID（`C`で始まる） |
| SLACK_USER_MENTION | オプション | - | メンション対象ユーザーID（`<@U0123456789>`形式） |

詳細な設定手順は[SLACK_BOT_TOKENとSLACK_CHANNEL_IDの設定方法](#各認証情報の詳細設定)を参照してください。

### 🛡️ セキュリティのベストプラクティス

- ✅ `.env` ファイルを `.gitignore` に追加
- ✅ 最小限のスコープ/権限を使用
- ✅ トークンを定期的にローテーション
- ✅ チーム内で環境変数を安全に共有（1Password、AWS Secrets Manager等）
- ❌ トークンをコードやドキュメントにコミットしない
- ❌ トークンをSlack/メール等で平文送信しない

## 推奨プラグイン併用

### affaan-m プラグイン

NDFプラグインと併用することで、以下の機能が追加されます：

- **コンテキスト管理**: `/context-status`でコンテキスト使用率を監視
- **品質保証**: 自動フォーマット、console.log検出、シークレットスキャン
- **TDDワークフロー**: `/tdd`コマンドで5段階TDDプロセスをガイド
- **セキュリティチェック**: OWASP Top 10準拠の脆弱性検出

### インストール方法

```bash
/plugin install affaan-m@ai-plugins
```

### 併用による相乗効果

| プラグイン | 役割 |
|-----------|------|
| **NDFプラグイン** | MCP統合、公開スキル（Claude/Kiro core 27個、Codex core 29個）、専門エージェント |
| **affaan-mプラグイン** | コンテキスト管理、品質保証、TDDワークフロー |

詳細は[affaan-mプラグインREADME](../affaan-m/README.md)を参照してください。

## セキュリティのベストプラクティス

- ✅ 環境変数でトークンを管理
- ✅ `.env` ファイルを `.gitignore` に追加
- ✅ 最小限のスコープ/権限を使用
- ✅ トークンを定期的にローテーション
- ❌ トークンをコードやドキュメントにコミットしない

## サポート

問題が発生した場合：
1. 上記のトラブルシューティングセクションを確認
2. GitHubリポジトリでイシューを作成: https://github.com/devbasex/ai-plugins/issues

## ライセンス

MIT License

## 作者

takemi-ohama - https://github.com/takemi-ohama

## 貢献

プルリクエストを歓迎します。大きな変更の場合は、まずイシューを開いて変更内容を議論してください。
