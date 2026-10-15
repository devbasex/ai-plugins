# AI Plugins

Claude Code / Codex / Kiro CLI / agy で使うプラグインを配るマーケットプレイスです。
入れると、Pull Request の作成・レビュー・修正といった開発の作業を、手順どおりに AI へ任せられます。
よく使う外部サービス（Notion・BigQuery・データベースなど）へつなぐ設定も入ります。

## 入れる

まず主要プラグインの **ndf** を入れます。使っている AI ツールの手順を 1 つ選んでください。

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

### Kiro CLI

Kiro CLI はマーケットプレイスから入れられません。clone したディレクトリでインストーラーを実行します。

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
bash plugins/ndf/dev.kiro/install.sh
```

起動の方法は [plugins/ndf/README.md の「Kiro CLI」](./plugins/ndf/README.md#kiro-cli) にあります。

### agy

agy もマーケットプレイスから入れられません。clone したディレクトリから導入します。

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
agy plugin install plugins/ndf/dev.agy
```

hook を効かせる手順は [plugins/ndf/README.md の「agy」](./plugins/ndf/README.md#agy) にあります。

更新の方法と Slack 通知の設定は [plugins/ndf/README.md](./plugins/ndf/README.md) にあります。

## 使ってみる

Claude Code では `/ndf:<Skill 名>` と打って呼びます。**Skill** は、AI が読んで従う作業手順です。

| やりたいこと | 打つもの |
| --- | --- |
| 変更をコミットして Pull Request を出す | `/ndf:pr` |
| Pull Request を別の AI 2 者にレビューさせ、指摘が出なくなるまで直す | `/ndf:cross-review <PR番号>` |
| Pull Request に付いたレビューコメントに対応する | `/ndf:fix <PR番号>` |
| Pull Request をレビューしてもらう | `/ndf:pr-review <PR番号>` |
| 変更の大きさに合った進め方（要求の整理から配布まで）を決めて進める | `/ndf:development-workflow` |
| 作業用の git worktree を用意する | `/ndf:worktree` |

呼ばなくても、依頼の内容に合う Skill を AI が選んで使うこともあります（例: 「PR を作って」）。

## 何が入っているか

| プラグイン名 | バージョン | できること | 詳細 |
| --- | --- | --- | --- |
| **ndf** | 10.17.75 | 開発の作業手順（Skill）と専門のサブエージェント。4 つの AI ツールへ同じ名前で入る | [README](./plugins/ndf/README.md) |
| **playwright-kit** | 3.0.1 | Playwright による E2E テストの計画・スクリプト作成・証跡のレポート | [README](./plugins/playwright-kit/README.md) |

外部サービスへつなぐ MCP サーバーの設定のプラグインが 10 個あります（Claude Code / Codex / Kiro CLI 向け）。
使うものだけを入れます（Claude Code なら `/plugin install <プラグイン名>@ai-plugins`）。

| プラグイン名 | つなぐ先 |
| --- | --- |
| [mcp-serena](./plugins/mcp/mcp-serena/) | シンボル単位のコードの読み書き |
| [mcp-notion](./plugins/mcp/mcp-notion/) | Notion |
| [mcp-bigquery](./plugins/mcp/mcp-bigquery/) | BigQuery |
| [mcp-dbhub](./plugins/mcp/mcp-dbhub/) | データベース（SQL） |
| [mcp-redash](./plugins/mcp/mcp-redash/) | Redash |
| [mcp-aws-docs](./plugins/mcp/mcp-aws-docs/) | AWS の公式ドキュメント |
| [mcp-chrome-devtools](./plugins/mcp/mcp-chrome-devtools/) | Chrome DevTools によるブラウザ操作 |
| [mcp-playwright](./plugins/mcp/mcp-playwright/) | Playwright によるブラウザ操作 |
| [mcp-devin](./plugins/mcp/mcp-devin/) | DeepWiki のリポジトリ解説 |
| [mcp-markitdown](./plugins/mcp/mcp-markitdown/) | PDF・Office 文書の Markdown への変換 |

### NDF プラグインの Skill の一覧

**NDFプラグイン v10.17.75** に入る Skill の数は、AI ツールごとに違います。
Claude Code向け core 47個、Kiro向け core 44個、Codex向け core 43個、agy向け core 43個です。
元Skills（47個）の内訳は次のとおりです。
  - Pull Request とレビュー (7): pr, pr-tests, fix, pr-review, cherry-pick-pr, deploy, merged
  - 開発の進め方 (16): development-workflow, requirements-design, design, document-restructuring, document-sources, document-drafting, tdd-cycle, refactoring, quality-gates, release, release-verification, retrospective, out-of-scope, progress-tracking, backlog-refinement, layout-review
  - 原則と書き方 (11): ndf-policies, implementation-plan, plan-to-spec, investigation-rules, decision-request, problem-solving, logging-guidelines, markdown-writing, notion-writing, issue-plan-strategy, ml-model-structure
  - 品質と実行環境 (3): qa-security-scan, docker-container-access, official-skills-autoloader
  - 外部サービス (2): google-workspace, document-systems
  - 複数の AI によるレビューとリファクタリング (3): cross-review, cross-refactoring, external-ai
  - 作業場所 (1): worktree
  - 運用 (4): skill-stats, statusline, install-wrapper, restart

Skill のほかに、次のものが入ります。

| 部品 | できること |
| --- | --- |
| サブエージェント（Claude Code） | 調査・データ分析・デバッグ・コードレビュー・CI/CD などを任せる専門のサブエージェント 8 個 |
| 外部 AI への委譲 | Codex や agy をバックグラウンドで動かし、別の AI の意見を得る |
| hook | 会話の記録を 90 日以上残す。回答や承認を待つときに Slack へ知らせる。リポジトリに `.ndf/worktree.json` があるときは git worktree での作業を案内する |

## もっと知る

| 知りたいこと | 文書 |
| --- | --- |
| NDF の使い方の詳細 | [plugins/ndf/README.md](./plugins/ndf/README.md)、[docs/ndf-plugin-reference.md](./docs/ndf-plugin-reference.md) |
| 版ごとの変更点 | [CHANGELOG.md](./CHANGELOG.md) |
| 過去の版へ戻す | [docs/versioning-and-distribution.md の「利用者が過去の版へ戻る」](./docs/versioning-and-distribution.md#利用者が過去の版へ戻る) |
| 質問・不具合の報告・要望 | [SUPPORT.md](./SUPPORT.md) |
| 脆弱性の報告 | [SECURITY.md](./SECURITY.md)。**公開の issue には書かないでください** |
| 開発に参加する | [CONTRIBUTING.md](./CONTRIBUTING.md)、[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)、[GOVERNANCE.md](./GOVERNANCE.md) |

## ライセンス

MIT License。詳細は [LICENSE](./LICENSE) にあります。

---

**作成者:** takemi-ohama - https://github.com/takemi-ohama
