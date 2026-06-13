# NDF Plugin リファレンス

## 概要

NDF プラグインは、Claude Code / Kiro CLI 向けのオールインワン開発支援プラグイン。エージェント、Skills、フックを統合して提供する。

**現行バージョン**: **v4.15.0** — cross-review の worktree 生成先を非永続領域 `<システム tmpdir>/ndf-worktrees/<owner>--<repo>/pr<N>` に変更（永続 volume 消費・他リポジトリの PR 番号衝突・残骸流用事故を解消。`NDF_WORKTREE_BASE` env で明示オーバーライド可）。**v4.14.0** で `statusline` skill とデフォルト statusline 設定 hook を追加（47→48個）。statusLine 未設定時のみ NDF 標準 statusline（コンテナ名/ホスト名 + project_dir + コンテキスト使用率）を自動設定し、`/ndf:statusline set|restore|status` で切り替え・復元できる。**v4.13.0** で `issue-plan-strategy` の release PR body を self-contained 必須化。**v4.12.0** で Playwright E2E に `/ndf:playwright-browser-connect`（CDP リモートブラウザ接続）と `/ndf:playwright-evidence-drive`（Google Drive エビデンスアーカイブ）の 2 skill を追加（45→47個）。直前の **v4.11.0** で `/ndf:cross-review` の堅牢性改善（monitor.py の EARLY_ERROR 誤検知を解消: テスト用文字列リテラル / grep 形式ソース引用行を benign 自動判定し、ループ終了時の最終スイープで残 open review thread を全 Resolve）を実施。**v4.10.0** で `ml-model-structure` skill（MLモデル構築・推論API開発の標準ディレクトリ構造: 版内feature SSoT / train↔serve契約）を追加。`/ndf:fix` の修正ポリシー刷新（minor/nit のうち performance/readability/duplication は積極修正、+30 行超は要問い合わせ）、CI 完了待ち廃止、PR範囲外 flaky テストも修正対象。`/ndf:cross-review` 内のサブエージェントプロンプトも同期。重要度ラベルは AI agent の付与を鵜呑みにせず独自再判定。完了報告には PR URL 必須。詳細は [CHANGELOG.md](../plugins/ndf/CHANGELOG.md)。`/ndf:codex` skill + `corder` エージェント経由の Codex CLI 直接実行に一本化、Serena MCP は別プラグイン `mcp-serena` に分離済み、Playwright シナリオ E2E、Google Drive / Chat 連携 skill を提供。

## ディレクトリ構造

```
plugins/ndf/
├── .claude-plugin/
│   └── plugin.json          # プラグインメタデータ
├── hooks/
│   └── hooks.json           # SessionStart (保持期間管理/デフォルトstatusline) / Stop (Slack通知)
├── scripts/
│   ├── ensure-retention.sh  # cleanupPeriodDays >= 90 を保つ
│   ├── statusline.sh        # NDF標準statusline本体
│   ├── statusline-switch.sh # statusline の導入・切替・復元 (ensure/set/restore/status)
│   └── slack-notify.js      # Slack通知スクリプト
├── agents/                  # 専門エージェント（8個）
├── skills/                  # Skills（48個）
├── CLAUDE.md                # プラグイン開発者向けガイド
└── README.md                # 利用者向けドキュメント
```

## 機能

### 1. MCP サーバー

NDF プラグイン本体はコア MCP サーバを**同梱しない**（v4.0.0 で Codex MCP を廃止）。関連 MCP は個別プラグインとして提供：

| MCP | 提供プラグイン | 用途 |
|---|---|---|
| Serena MCP | `mcp-serena` | セマンティックコード操作 |
| GitHub MCP | Anthropic 公式 | GitHub 操作 |
| Context7 MCP | Anthropic 公式 | 最新ライブラリドキュメント |
| Chrome DevTools MCP | `mcp-chrome-devtools` | ブラウザ自動化・パフォーマンス |
| BigQuery MCP | `mcp-bigquery` | BigQuery データ分析 |
| AWS Docs MCP | `mcp-aws-docs` | AWS 公式ドキュメント |
| DBHub MCP | `mcp-dbhub` | 汎用データベース |
| Notion MCP | `mcp-notion` | Notion 連携 |

### 2. ワークフロー Skills（スラッシュコマンド）

`/ndf:<name>` でユーザーから直接起動する Skill 群：

| Skill | 用途 |
|---|---|
| `/ndf:pr` | commit+push+PR 作成 / 既存 PR 説明更新 |
| `/ndf:pr-tests` | PR の Test Plan を自動実行 |
| `/ndf:fix` | レビューコメントの修正対応 |
| `/ndf:review` | PR を Approve/Request Changes 判定 |
| `/ndf:review-branch` | PR 前のローカル差分レビュー |
| `/ndf:review-pr-comments` | PR コメントの分類 (READ-ONLY) |
| `/ndf:resolve-pr-comments` | 対応済みコメント返信+Resolve |
| `/ndf:cherry-pick-pr` | 環境ブランチへの cherry-pick PR |
| `/ndf:deploy` | 環境ブランチへのデプロイ PR |
| `/ndf:sync-main` | main を現ブランチに取り込み |
| `/ndf:merged` | マージ後のクリーンアップ |
| `/ndf:clean` | マージ済みブランチ一括削除 |
| `/ndf:browser-test` | Playwright/Chrome DevTools での動作確認 |
| `/ndf:skill-stats` | Skill 利用統計の集計（期間/プロジェクト別） |
| `/ndf:statusline` | NDF標準statuslineの切り替え・復元・状態確認 (`set`/`restore`/`status`) |

### 3. 原則・ガイドライン Skills（モデル起動型）

該当文脈で自動的に参照される Skill 群：

| Skill | 対象領域 |
|---|---|
| `ndf-policies` | プラグイン共通ポリシー（常時注入） |
| `branch-fix-strategy` | 複数ブランチ適用戦略 (cherry-pick) |
| `implementation-plan` | `issues/` 配下の実装プラン管理 |
| `investigation-rules` | 調査時のエビデンス主義 |
| `problem-solving` | 根本原因分析・多層防御 |
| `logging-guidelines` | ログ運用 (言語非依存) |
| `markdown-writing` | Markdown 文書の体裁 |
| `issue-plan-strategy` | issue→plan 作成・multi-PR 実行のワークフロー戦略 |
| `ml-model-structure` | ML モデル構築・推論API の標準ディレクトリ構造 (版内 feature SSoT / train↔serve 契約) |

### 4. 補助 Skills

| Skill | 用途 |
|---|---|
| `data-analyst-sql-optimization` | SQL 最適化パターン |
| `data-analyst-export` | CSV/JSON/Excel 出力 |
| `qa-security-scan` | OWASP Top 10 チェック |
| `python-execution` | Python 実行環境の自動判定 |
| `docker-container-access` | Docker コンテナ接続判定 |
| `git-gh-operations` | git/gh 操作パターン |
| `google-auth` | Google API OAuth2 |
| `codex` | Codex CLI 直接実行ガイド |
| `deepwiki-transfer` | DeepWiki 知識転送 |
| `knowledge-reorg` | 知識再編成 |
| `mcp-builder` | MCP サーバ作成（Anthropic 公式） |
| `official-skills-autoloader` | Anthropic 公式 Skill の自動ロード |
| `playwright-test-planning` | E2E テスト計画立案 (HTSM/ISTQB) |
| `playwright-script-creation` | E2E テストスクリプト作成 |
| `playwright-execution` | E2E 実行+エビデンス収集 (動画/a11y/CWV) |
| `playwright-report` | E2E テスト結果の Markdown レポート |
| `playwright-kit-ops` | playwright_kit スクリプト操作 |
| `playwright-scenario-test` | フル E2E ワークフロー統括 |
| `google-drive` | Google Drive/Docs 操作 |
| `google-chat` | Google Chat メッセージ取得 |
| `cross-review` | codex/gemini 両方でレビュー自動ループ |
| `gemini` | gemini CLI 直接実行 |

### 5. 専門エージェント（8個、モデル階層化）

| エージェント | モデル | 役割 |
|-------------|------|------|
| **director** | opus | タスク統括・設計立案 |
| **corder** | sonnet | Codex CLI 経由の独立レビュー・大規模調査 |
| **data-analyst** | sonnet | データ分析・SQL |
| **researcher** | sonnet | AWS Docs / Chrome DevTools 調査 |
| **qa** | sonnet | 品質・セキュリティ検証 |
| **debugger** | sonnet | 根本原因分析 |
| **devops-engineer** | sonnet | Docker/CI/CD/K8s |
| **code-reviewer** | sonnet | git diff/PR レビュー（Codex 非使用） |

### 6. 自動フック

| イベント | 用途 |
|---|---|
| `SessionStart` (matcher: `startup`) | `~/.claude/settings.json` の `cleanupPeriodDays` を最低 90 日に保つ (7日タイムスタンプガード + flock でアトミック更新) |
| `SessionStart` (matcher: `startup`) | `statusLine` 未設定時のみ NDF 標準 statusline を設定 (既存設定優先、NDF 版利用中はスクリプト更新のみ追従) |
| `Stop` | AI 要約を生成して Slack に通知 (`SLACK_BOT_TOKEN` 設定時のみ) |

## 環境変数

### Slack 通知（推奨）
- `SLACK_BOT_TOKEN` — Bot User OAuth Token (`xoxb-...`)
- `SLACK_CHANNEL_ID` — 通知先チャンネル (`C...`)
- `SLACK_USER_MENTION` — メンション対象ユーザー (`<@U...>`)

### Codex CLI（`/ndf:codex` / `corder` エージェント利用時）
- `CODEX_HOME` — Codex CLI のホーム (default: `~/.codex`)
- `OPENAI_API_KEY` — `codex login` 済みなら不要

### 個別 MCP プラグイン（利用する場合）
各プラグイン README を参照。

## 実装上の知見

### Stop Hook 無限ループ防止

Stop hook 内で Claude CLI を呼び出す際は `--settings` で hooks と plugins を両方無効化する：

```bash
claude -p --settings '{"disableAllHooks": true, "disableAllPlugins": true}' --output-format text
```

- `CLAUDE_DISABLE_HOOKS` 環境変数 → 存在しない
- `stop_hook_active` フィールド → 実際には送信されない
- `--settings` で両方無効化 → 確実に動作

### 要約生成の3段階フォールバック

1. **Claude CLI**（優先） — AI による高品質要約
2. **transcript 解析**（フォールバック1） — セッションログから抽出
3. **git diff**（フォールバック2） — ファイル変更から推測

### 保持期間管理の実装

`SessionStart` hook で `~/.claude/settings.json` の `cleanupPeriodDays` を検査し、90 未満なら 90 に更新する：

- 実行は `~/.claude/.ndf-retention-checked` の 7 日タイムスタンプで抑止
- 書き込みは `flock` で排他ロックし、並列セッションでの lost update を防止 (flock 不在環境は atomic rename に依存)
- Claude Code の公開 API には「プラグインインストール時」hook が存在しないため、`SessionStart + startup` matcher が実用上の最適解

### デフォルト statusline の実装（v4.14.0）

`SessionStart` hook で `statusline-switch.sh ensure` を実行する：

- プラグイン同梱の `statusline.sh` を `~/.claude/ndf-statusline.sh` にコピー（差分時のみ。プラグイン更新に追従させるため、settings からはプラグインキャッシュパスではなく固定パスを参照）
- `statusLine` が未設定の場合のみ `bash ~/.claude/ndf-statusline.sh` を設定。既存設定があれば何もしない（既存優先）
- `/ndf:statusline set` は既存設定を `~/.claude/.ndf-statusline-backup.json` にバックアップしてから切り替え、`restore` で復元（バックアップ無しなら `statusLine` キーを削除）
- 書き込みは `flock` + atomic rename（保持期間管理と同パターン）

### Codex の扱い（v4.0.0）

- Codex MCP サーバは廃止
- `/ndf:codex` skill に CLI 直接実行の詳細手順（サンドボックス、プロンプト設計、バックグラウンド実行、stderr/stdout 回収）を記載
- `corder` エージェントは本 skill を参照して `codex exec` を呼び出す

### バージョン変遷（抜粋）

| バージョン | 主な変更 |
|-----------|---------|
| v1.0.0 | 初期リリース |
| v2.0.0 | 公式プラグインへの MCP 重複解消 |
| v2.6.0 | NDF コア MCP 最小化 (Serena + Codex) |
| v2.7.0 | commands → skills 統合 (Claude Code 2.1.3 対応) |
| v3.0.0 | Serena MCP 分離 (`mcp-serena`)、memory 系 Skill 廃止、CLAUDE.ndf.md 注入廃止 |
| v3.1.0 | Kiro CLI 対応、`google-auth` skill 追加 |
| v3.5.0 | Agent/Skill 再編、モデル階層化、公式 Skill 連携 |
| v3.6.0 | 汎用 skill 13 個追加 (原則系・PR ワークフロー系・codex) |
| v3.7.0 | transcript 保持期間自動管理 hook、`/ndf:skill-stats` skill |
| **v4.0.0 (BREAKING)** | **Codex MCP 廃止 → CLI 直接実行一本化**、レガシー CLAUDE.ndf.md 救済機構削除、skill-stats にプロジェクト別/日付範囲フィルタ追加 |
| **v4.1.0** | `playwright-scenario-test` / `google-drive` / `google-chat` skill 追加、`google-auth` v0.2.0 (永続トークン `~/.config/gcloud/google_token.json` + `get_credentials()` API + 手動 copy-paste フロー) |
| **v4.10.0** | `ml-model-structure` skill 追加 (MLモデル構築・推論API開発の標準ディレクトリ構造、版内 feature SSoT / train↔serve 契約) |
| **v4.11.0** | `/ndf:cross-review` 堅牢性改善: monitor.py EARLY_ERROR 誤検知解消 (文字列リテラル / grep ソース引用行を benign 判定) + ループ終了時の最終スイープ (残 open thread 全 Resolve) 必須化 |
| **v4.12.0** | Playwright E2E に `playwright-browser-connect` (CDP リモートブラウザ接続) / `playwright-evidence-drive` (Google Drive エビデンスアーカイブ) の 2 skill 追加 (45→47個) |
| **v4.12.1** | `/ndf:cross-review` デフォルト調整: `--max-rounds` 6→12 / `--rotate-after` 5→8、`--rotate-mode squash` 説明から「既存挙動」表記を削除 |
| **v4.13.0** | `issue-plan-strategy`: release PR body の self-contained 必須化 (レビュアー視点の原則明文化、子 PR チェックリストを `<details>` に格下げ、Ready 前の body 最終化ステップ追加) |
| **v4.14.0** | `statusline` skill + デフォルト statusline 設定 hook 追加 (47→48個)。statusLine 未設定時のみ NDF 標準 statusline を自動設定、`/ndf:statusline set\|restore\|status` で切替・復元 |
| **v4.15.0** | cross-review: worktree 生成先を `<システム tmpdir>/ndf-worktrees/<owner>--<repo>/pr<N>` に変更 (非永続化 + リポジトリ別分離)。未登録パスの残骸は `.stale-<ts>` に退避して作り直すガード追加 |
