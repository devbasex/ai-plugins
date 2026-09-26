# AI Plugins

Claude Code / Codex / Kiro CLI / agy 向けのスキル・MCP設定を共有するための内部マーケットプレイスです。

## 概要

このマーケットプレイスは、チーム全体でAI開発ツール（Claude Code / Codex / Kiro CLI / agy）の導入を加速するための事前設定されたプラグインを提供します。

**NDFプラグイン v10.17.31-dev.2** は、同じ `ndf@ai-plugins` という名前で Claude Code / Codex / Kiro CLI / agy へ配布されるプラグインです。配布物は `plugins/ndf/` の1ディレクトリにまとまっており、Skill の実体は `plugins/ndf/skills/` の1箇所だけです。どのランタイムへ配るかは `plugins/ndf/manifests/*-skills.txt` が決めます。

- **公開Skills**: Claude Code向け core 48個、Kiro向け core 45個、Codex向け core 44個、agy向け core 44個に分離。
- **元Skills（48個）**:
  - PR/レビューワークフロー (7): pr, pr-tests, fix, pr-review, cherry-pick-pr, deploy, merged
  - 開発方法論 (16): development-workflow, requirements-design, design, document-restructuring, document-sources, document-drafting, tdd-cycle, refactoring, quality-gates, release, release-verification, retrospective, out-of-scope, progress-tracking, issue-upkeep, layout-review
  - 原則・ガイドライン (11): ndf-policies, implementation-plan, plan-to-spec, investigation-rules, decision-request, problem-solving, logging-guidelines, markdown-writing, notion-writing, issue-plan-strategy, ml-model-structure
  - データ分析・品質・環境 (4): qa-security-scan, docker-container-access, google-auth, official-skills-autoloader
  - 外部サービス連携 (2): google-drive, document-systems
  - AIクロスレビュー (3): cross-review, cross-refactoring, external-ai
  - 開発環境 (1): worktree
  - 運用 (4): skill-stats, statusline, install-wrapper, restart
- **8つの専門エージェント**: director, data-analyst, corder, researcher, qa, debugger, devops-engineer, code-reviewer
- **3 層の worker の定義 1 個**: worker（supervisor が 1 つの作業を渡す先。Skill と Agent のツールを外してある）
- **自動フック**: worktree 運用（Claude Code / Codex は PreToolUse + SessionStart、Kiro CLI は userPromptSubmit + agentSpawn、agy は PreToolUse + PreInvocation。リポジトリに `.ndf/worktree.json` があるときだけ動く）、SessionStart (transcript保持期間を最低90日に保つ)、回答・承認待ちの Slack 通知（Stop・Notification・PermissionRequest ほか）
- **外部AI委譲**: `/ndf:external-ai` skill + `corder` エージェント経由で Codex / agy をバックグラウンド実行 (v4.0.0 で Codex MCP サーバは廃止)
- **AIクロスレビュー強化**: `/ndf:cross-review` は codex/agy 両方に PR レビューを委譲し、agy の進捗 heartbeat、`--focus` / `--extra-instructions-file`、PR 種別別の自動レビュー観点テンプレートに対応
- **Kiro CLI対応**: `plugins/ndf/dev.kiro/install.sh` によるワンコマンドセットアップ
- **agy 対応**: `plugins/ndf/dev.agy/` を `agy plugin install` で導入
- **MCPプラグイン**: `plugins/mcp/<プラグイン名>/` の1ディレクトリで3ランタイムへ配布（agy は対象外）

## 利用方法

**配布のチャネルは 2 つあります。** 常用する場合は取得元をそのまま登録します（正式版）。
**手順はこれまでと変わりません。** 開発版を試すときだけ、取得元へ ref を足します。

| チャネル | 何が載るか | 取得元 | 向いている人 |
| --- | --- | --- | --- |
| **正式版** | 正式版として承認された版だけ | `https://github.com/devbasex/ai-plugins` | 常用する人 |
| 開発版 | マージされた変更がそのまま | 同じ URL に `#develop` を足す | 検証に参加する人 |

開発版は検証中の版です。版数に `-dev.<連番>` が付き、壊れていることがあります。手順は
[開発版を試す](#開発版を試す)にあります。

以下は NDF プラグインを正式版から導入する最初の 1 手です。導入の選択肢と、その後の更新・hook・
通知の設定は [plugins/ndf/README.md](./plugins/ndf/README.md) にあります。

### Claude Code

```bash
/plugin marketplace add https://github.com/devbasex/ai-plugins
/plugin install ndf@ai-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

**ローカルのディレクトリを同じ名前で追加しないでください。** 取得元が置き換わります。
ランタイムごとの振る舞いは
[docs/versioning-and-distribution.md の「ランタイムごとの取得と導入」](./docs/versioning-and-distribution.md#ランタイムごとの取得と導入)
にあります。

### Kiro CLI

Kiro CLI はマーケットプレイスの経路を持ちません。clone したディレクトリで installer を実行します。

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
bash plugins/ndf/dev.kiro/install.sh
```

Slack 通知・Codex CLI 連携の選択肢と起動の方法は
[plugins/ndf/README.md の「Kiro CLI」](./plugins/ndf/README.md#kiro-cli)、既定エージェントへの
切り替えは [plugins/ndf/docs/kiro-cli.md](./plugins/ndf/docs/kiro-cli.md) にあります。

### agy

agy もマーケットプレイスの経路を持ちません。clone したディレクトリから `plugins/ndf/dev.agy` を
直接導入します。

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
agy plugin install plugins/ndf/dev.agy
```

hook を効かせる手順と、新しい版へ入れ替える手順は
[plugins/ndf/README.md の「agy」](./plugins/ndf/README.md#agy) にあります。

### 開発版を試す

取得元へ `#develop`（Codex は `--ref develop`）を足して登録します。正式版と開発版は同時に
入れられません。ランタイムごとの手順は
[docs/versioning-and-distribution.md の「開発版を試す」](./docs/versioning-and-distribution.md#開発版を試す)、
取得元を書き換えずに手元で確かめる方法は
[「ランタイムごとの取得と導入」](./docs/versioning-and-distribution.md#ランタイムごとの取得と導入)
にあります。

### 過去の版へ戻す

**版数を指定してインストールする手段はありません。** 取得元をリリースタグ（NDF なら
`ndf--v<版>`）へ固定します。手順と、タグで戻せない版・他のプラグインも戻る点・固定した版と
最新版を同時に有効にしない点は
[docs/versioning-and-distribution.md の「利用者が過去の版へ戻る」](./docs/versioning-and-distribution.md#利用者が過去の版へ戻る)
にあります。

### 利用可能なプラグイン

| プラグイン名 | バージョン | 説明 | 詳細 |
|------------|----------|------|------|
| **ndf** | 10.17.31-dev.2 | Claude Code / Codex / Kiro CLI / agy へ 1 ディレクトリから配布する NDF プラグイン。8個の専門エージェントと 3 層の worker の定義 1 個（Claude版）、公開Skills（Claude Code向け core 48個、Kiro向け core 45個、Codex向け core 44個、agy向け core 44個）、4ランタイム共通の worktree 運用フック（PreToolUse / SessionStart / userPromptSubmit / agentSpawn / PreInvocation）、Claude Stopフック、Codex/Kiro向け通知・実行補助を提供。v4.0.0 で Codex MCP サーバを廃止し、`/ndf:external-ai` skill + `corder` エージェント経由の CLI 直接実行に一本化。 | [README](./plugins/ndf/README.md) |
| **playwright-kit** | 2.0.4 | Playwright による E2E テストの計画・実装・証跡管理を提供するプラグイン。ページ役割からのテスト計画、動画 / trace 付きスクリプト実装、レポート生成と Drive 保管、playwright_kit ランタイム（init、a11y / CWV スキャン）の 4 Skill。NDF v7.0.0 で分離。 | [README](./plugins/playwright-kit/README.md) |

### 変更履歴

版ごとの変更点は [CHANGELOG.md](./CHANGELOG.md) にある。リリース済み版の判断の理由と、その版で決めた
規約は [docs/ndf-version-decisions.md](./docs/ndf-version-decisions.md) にある。現行版の分だけは
[CLAUDE.md](./CLAUDE.md) にあり、リリースした時点で退避先へ移る。

## リファレンス

### 公式ドキュメント

- [Claude Code ドキュメント](https://docs.claude.com/en/docs/claude-code)
- [プラグインマーケットプレイス](https://code.claude.com/docs/ja/plugin-marketplaces)
- [プラグイン開発ガイド](https://docs.claude.com/en/docs/claude-code/plugins)
- [スキルドキュメント](https://docs.claude.com/en/docs/claude-code/skills)
- [MCP仕様](https://modelcontextprotocol.io)

### MCPサーバー公式リポジトリ

- [GitHub MCP](https://github.com/github/github-mcp-server)
- [Serena MCP](https://github.com/oraios/serena)
- [Notion MCP](https://mcp.notion.com)
- [BigQuery MCP](https://github.com/ergut/mcp-server-bigquery)
- [DBHub MCP](https://github.com/bytebase/dbhub)
- [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)
- [AWS Documentation MCP](https://github.com/awslabs/aws-documentation-mcp-server)

### プロジェクト内ドキュメント

- [CHANGELOG.md](./CHANGELOG.md) - 版ごとの変更点
- [GOVERNANCE.md](./GOVERNANCE.md) - 役割・決め方・メンテナーになる道
- [CONTRIBUTING.md](./CONTRIBUTING.md) - 参加の手引き（開発の進め方・手元での検証）
- [docs/plugin-development-guide.md](./docs/plugin-development-guide.md) - プラグインの作成・更新・削除の手順
- [docs/versioning-and-distribution.md](./docs/versioning-and-distribution.md) - 版の付け方・開発版と正式版のリリース・過去の版へ戻る手順
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) - 行動規範
- [SECURITY.md](./SECURITY.md) - 脆弱性の報告
- [SUPPORT.md](./SUPPORT.md) - 質問と不具合の報告
- [CLAUDE.md](./CLAUDE.md) - AIエージェント向けガイドライン（Claude Code）
- [KIRO.md](./KIRO.md) - AIエージェント向けガイドライン（Kiro CLI）
- [docs/specifications/](./docs/specifications/) - 完了済みplan/issue由来の確定仕様
- [LICENSE](./LICENSE) - MITライセンス

## コントリビューション

参加の手順・手元での検証・Pull Request の出し方は [CONTRIBUTING.md](./CONTRIBUTING.md) に
あります。参加するうえで守ることは [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) にあります。
誰がどう決めているかと、メンテナーになる道は [GOVERNANCE.md](./GOVERNANCE.md) にあります。

### 手伝ってくれる方を探しています

**このリポジトリのメンテナーは 1 人です。** 次の 3 つは特に人手が足りていません。

| 手伝ってほしいこと | 具体的には |
| --- | --- |
| Skill の追加・改善 | 手順の誤りの修正、扱っていない工程の追加。実体は `plugins/ndf/skills/` の 1 か所にあります |
| 実機での検証 | Claude Code / Codex / Kiro CLI / agy の 4 つは挙動が違います。**どれか 1 つの環境があれば参加できます** |
| 文書の改善と翻訳 | 説明の不足の補い、用語の統一。現在の文書はすべて日本語です |

着手しやすい issue には [`good first issue`](https://github.com/devbasex/ai-plugins/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)、
実機の環境が要る issue には [`help wanted`](https://github.com/devbasex/ai-plugins/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
を付けています。

## サポート

質問・不具合の報告・要望の出し方は [SUPPORT.md](./SUPPORT.md) にあります。脆弱性の報告は
[SECURITY.md](./SECURITY.md) の手順に従ってください。**公開の issue には書かないでください。**

## ライセンス

MIT License - 詳細は [LICENSE](./LICENSE) ファイルを参照

---

**作成者:** takemi-ohama - https://github.com/takemi-ohama
