# AI Plugins

Claude Code / Codex / Kiro CLI / agy で使うプラグインを配るマーケットプレイスです。
入れると、開発の進め方（要求の整理・設計・テスト・レビュー・リリース）を AI へ任せる
Skill が使えるようになります。よく使う外部サービスへつなぐ MCP サーバーの設定も入ります。

## 何が入っているか

| プラグイン名 | バージョン | できること | 詳細 |
| --- | --- | --- | --- |
| **ndf** | 10.17.75 | 開発の工程を Skill とサブエージェントとして持つ主要プラグイン。4 つのランタイムへ同じ名前で入る | [README](./plugins/ndf/README.md) |
| **playwright-kit** | 3.0.1 | Playwright による E2E テストの計画・スクリプト作成・証跡のレポート | [README](./plugins/playwright-kit/README.md) |

MCP サーバーを設定するプラグインは 10 個あります（Claude Code / Codex / Kiro CLI 向け）。

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

## はじめかた

NDF プラグインを入れる最初の 1 手です。ランタイムごとの選択肢と、更新・hook・Slack 通知の設定は
[plugins/ndf/README.md](./plugins/ndf/README.md) にあります。

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

## NDF プラグインの中身

**NDFプラグイン v10.17.75** は、Skill・サブエージェント・hook の 3 つでできています。

**Skill** は、AI が読む作業手順です。ランタイムごとに入る数が違います
（Claude Code向け core 47個、Kiro向け core 44個、Codex向け core 43個、agy向け core 43個）。
元Skills（47個）の内訳は次のとおりです。
  - Pull Request とレビュー (7): pr, pr-tests, fix, pr-review, cherry-pick-pr, deploy, merged
  - 開発の進め方 (16): development-workflow, requirements-design, design, document-restructuring, document-sources, document-drafting, tdd-cycle, refactoring, quality-gates, release, release-verification, retrospective, out-of-scope, progress-tracking, backlog-refinement, layout-review
  - 原則と書き方 (11): ndf-policies, implementation-plan, plan-to-spec, investigation-rules, decision-request, problem-solving, logging-guidelines, markdown-writing, notion-writing, issue-plan-strategy, ml-model-structure
  - 品質と実行環境 (3): qa-security-scan, docker-container-access, official-skills-autoloader
  - 外部サービス (2): google-workspace, document-systems
  - 複数の AI によるレビューとリファクタリング (3): cross-review, cross-refactoring, external-ai
  - 作業場所 (1): worktree
  - 運用 (4): skill-stats, statusline, install-wrapper, restart

Claude Code では `/ndf:<Skill 名>` で呼びます（例: `/ndf:cross-review 123`）。

| 部品 | できること |
| --- | --- |
| サブエージェント（Claude Code） | 調査・データ分析・デバッグ・コードレビュー・CI/CD などを任せる専門のサブエージェント 8 個と、作業を 1 つずつ受け持つ worker |
| 複数の AI によるレビュー | Pull Request のレビューを Claude Code・Codex・Kiro CLI のうち 2 者へ毎回依頼し、新しい指摘が出なくなるまで直す |
| 外部 AI への委譲 | Codex や agy をバックグラウンドで動かし、別の AI の意見を得る |
| hook | git の worktree での作業を案内する（リポジトリに `.ndf/worktree.json` があるときだけ）。会話の記録を 90 日以上残す。回答や承認を待つときに Slack へ知らせる |

## もっと知る

| 知りたいこと | 文書 |
| --- | --- |
| NDF の使い方の詳細 | [plugins/ndf/README.md](./plugins/ndf/README.md)、[docs/ndf-plugin-reference.md](./docs/ndf-plugin-reference.md) |
| 版ごとの変更点 | [CHANGELOG.md](./CHANGELOG.md) |
| 過去の版へ戻す | [docs/versioning-and-distribution.md の「利用者が過去の版へ戻る」](./docs/versioning-and-distribution.md#利用者が過去の版へ戻る) |
| プラグインを作る・直す | [docs/plugin-development-guide.md](./docs/plugin-development-guide.md) |
| Claude Code のプラグインの仕組み | [プラグインマーケットプレイス](https://code.claude.com/docs/ja/plugin-marketplaces)、[Skill](https://docs.claude.com/en/docs/claude-code/skills)、[MCP の仕様](https://modelcontextprotocol.io) |

## 参加する

参加の手順・手元での検証・Pull Request の出し方は [CONTRIBUTING.md](./CONTRIBUTING.md) にあります。
守ることは [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)、誰がどう決めているかとメンテナーになる道は
[GOVERNANCE.md](./GOVERNANCE.md) にあります。

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

MIT License。詳細は [LICENSE](./LICENSE) にあります。

---

**作成者:** takemi-ohama - https://github.com/takemi-ohama
