# AI Plugins

Claude Code / Codex / Kiro CLI向けのスキル・MCP設定を共有するための内部マーケットプレイスです。

## 概要

このマーケットプレイスは、チーム全体でAI開発ツール（Claude Code / Codex / Kiro CLI）の導入を加速するための事前設定されたプラグインを提供します。

**NDFプラグイン v5.0.0** は、同じ `ndf@ai-plugins` という名前で Claude Code / Codex / Kiro CLI へ配布されるランタイム別プラグインです。共通ソースは `plugins/ndf-shared/` に集約し、利用者が install する配布物は `plugins/ndf-claude/` / `plugins/ndf-codex/` / `plugins/ndf-kiro/` に分かれています。

- **公開Skills**: Claude Code向け core 25個、Kiro向け core 24個、Codex向け core 23個に分離。
- **元Skills（29個）**:
  - PR/レビューワークフロー (7): pr, pr-tests, fix, review, cherry-pick-pr, deploy, merged
  - 原則・ガイドライン (9): ndf-policies, implementation-plan, plan-to-spec, investigation-rules, problem-solving, logging-guidelines, markdown-writing, issue-plan-strategy, ml-model-structure
  - データ分析・品質・環境 (4): qa-security-scan, docker-container-access, google-auth, official-skills-autoloader
  - E2Eテスト/Playwright (4): playwright-planning, playwright-authoring, playwright-evidence, playwright-kit-ops
  - 外部サービス連携 (1): google-drive
  - AIクロスレビュー (2): cross-review, external-ai
  - 運用 (2): skill-stats, statusline
- **8つの専門エージェント**: director, data-analyst, corder, researcher, qa, debugger, devops-engineer, code-reviewer
- **自動フック**: SessionStart (transcript保持期間を最低90日に保つ) + Stop (AI要約生成+Slack通知)
- **外部AI委譲**: `/ndf:external-ai` skill + `corder` エージェント経由で Codex / Gemini CLI をバックグラウンド実行 (v4.0.0 で Codex MCP サーバは廃止)
- **AIクロスレビュー強化**: `/ndf:cross-review` は codex/gemini 両方に PR レビューを委譲し、Gemini の進捗 heartbeat、`--focus` / `--extra-instructions-file`、PR 種別別の自動レビュー観点テンプレートに対応
- **Kiro CLI対応**: `plugins/ndf-kiro/install.sh` によるワンコマンドセットアップ
- **MCPプラグイン**: `plugins/mcp/shared/` を編集元とし、Claude / Codex / Kiro 向け配布物を `plugins/mcp/{claude,codex,kiro}/` に生成

## 利用方法

### Claude Code

#### 1. マーケットプレイスの追加

```bash
/plugin marketplace add https://github.com/devbasex/ai-plugins
```

#### 2. プラグインのインストール

```bash
# NDFプラグイン（オールインワン統合プラグイン）
/plugin install ndf@ai-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

ローカルで検証する場合:

```bash
codex plugin marketplace add ./local/path/to/ai-plugins
codex plugin add ndf@ai-plugins
```

### Kiro CLI

#### 1. リポジトリをクローン

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
```

#### 2. インストーラーを実行

```bash
# 基本（Skills + agentSpawnフックのみ）
bash plugins/ndf-kiro/install.sh

# Slack通知も有効化
bash plugins/ndf-kiro/install.sh --with-slack

# 全部入り（Slack + Codex CLI 連携）
bash plugins/ndf-kiro/install.sh --with-slack --with-codex
```

インストーラーは `plugins/ndf-kiro/skills/` から `.kiro/skills/` への symlink、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成します。

#### 3. Slack通知の設定（オプション）

`.env` に以下を設定：
```
SLACK_CHANNEL_ID=C0123456789
SLACK_BOT_TOKEN=xoxb-...
SLACK_USER_MENTION=<@U0123456789>
```

#### 4. 起動

```bash
kiro-cli chat --agent ndf
```

既定エージェントとして使いたい場合は `bash plugins/ndf-kiro/install.sh --set-default` を実行します。

詳細は [KIRO.md](./KIRO.md) を参照。

### 利用可能なプラグイン

| プラグイン名 | バージョン | 説明 | 詳細 |
|------------|----------|------|------|
| **ndf** | 5.0.0 | Claude Code / Codex / Kiro CLI 向けに runtime 別配布物を提供する NDF プラグイン。8個の専門エージェント（Claude版）、公開Skills（Claude Code向け core 25個、Kiro向け core 24個、Codex向け core 23個）、Claude SessionStart/Stopフック、Codex/Kiro向け通知・実行補助を提供。v4.0.0 で Codex MCP サーバを廃止し、`/ndf:external-ai` skill + `corder` エージェント経由の CLI 直接実行に一本化。 | [Claude](./plugins/ndf-claude/README.md) / [Codex](./plugins/ndf-codex/README.md) / [Kiro](./plugins/ndf-kiro/README.md) |

### NDF v5.0.0 の主な変更（非互換）

Skill を利用実績にもとづいて棚卸し、**49 個から 29 個へ整理**しました。旧コマンド名から新コマンド名への対応表は `ndf-policies` skill に 1 リリース分だけ載せています（v6.0.0 で削除）。

- **統合（-11）**: 重複していた Skill を、利用実績の多い側の名前を残して統合しました。`/ndf:review-branch` → `/ndf:review --branch`、`/ndf:review-pr-comments` `/ndf:resolve-pr-comments` → `/ndf:fix`、`/ndf:clean` `/ndf:sync-main` → `/ndf:merged`、`/ndf:branch-fix-strategy` → `/ndf:cherry-pick-pr`、`/ndf:codex` `/ndf:gemini` → `/ndf:external-ai`、ブラウザ自動テスト 9 個 → 4 個。
- **削除（-9）**: 起動実績がなく、現在のモデルの標準能力か汎用コマンドで足りるものを削除しました（`/ndf:git-gh-operations` `/ndf:python-execution` など）。このうち `/ndf:sync-main` は内容を `/ndf:merged` へ吸収しているため移行先があります。移行先を用意せず消したのは 8 件です。
- **自然文で発動するようになりました**: `merged` / `pr` / `pr-tests` から明示指示専用の設定を外しました。取り消しの難しい手順の前には対象を提示して確認を取ります。`review` も同じ設定にしましたが、Claude Code では組み込みの `code-review` が同じ用途を持つため自然文では選ばれません。`/ndf:review` で明示的に起動してください。
- **frontmatter 規約と機械検査**: 発動判定に必要な情報を `description` へ集約し、`scripts/check-skill-frontmatter.py` で CI 検査します（規約は `plugins/ndf-shared/skills/README.md`）。
- **Kiro CLI**: エージェント名が `default` → `ndf` に変わりました。`install.sh` の再実行が必要です。`--set-default` と `--scope workspace|global` を追加し、常時指示を `.kiro/steering/` へ移しました。
- **Codex**: 明示指示専用の Skill に `agents/openai.yaml` を生成し、暗黙起動を抑止します。プラグイン配布の Skill は抑止すると `$<skill 名>` も効かないため、起動するには SKILL.md のパスを示します（`plugins/ndf-codex/README.md`）。

### NDF v4.20.1 の主な変更

- Kiro CLI 版のエージェント定義に `tools` を宣言しました。未宣言のままでは Kiro CLI がツールを1つも持たないエージェントとして読み込むため、skill が SKILL.md を読むことも git / gh を実行することもできませんでした。
- runtime smoke test に、生成された Kiro エージェント定義が `tools` を宣言しているかの検査を追加しました。従来はファイルの生成有無しか見ておらず、この欠落を検出できませんでした。

### NDF v4.20.0 の主な変更

- `markdown-writing` skill を、体裁ルールから**第三者可読性のルール**へ拡張しました。適用対象に仕様書・PR 本文・調査レポート・レビューコメントを追加しています。
- 説明文にテーブル名・カラム名などの内部識別子や、会話中に作ったローカル略語を持ち込まないルールを追加しました。「何のために」「何をやったか」の説明で識別子を使うと、書いた側は説明した気になり読み手には伝わらないためです。
- 検討過程の痕跡（案A / Option A 等）と変更履歴（「以前は〜だったが変更した」）を本文に残さないルール、否定的な結論にエビデンスを必須とするルール、個人情報・認証情報を文書に含めないルールを追加しました。
- 書き終えた後の grep セルフチェックとチェックリストを整備しました。

### NDF v4.19.0 の主な変更

- `plan-to-spec` skill を追加し、実装完了後の plan を `docs/` 配下の確定仕様書へ移動・リライト・レビューする標準フローを定義しました。
- 完了報告テンプレートを追加し、元 plan、確定仕様書、レビュー結果、検証内容を一貫した形式で報告できるようにしました。

## 開発ガイドライン

### プラグイン開発

#### ディレクトリ構造

```
ai-plugins/
├── .agents/
│   └── plugins/
│       └── marketplace.json      # Codexマーケットプレイスメタデータ
├── .claude-plugin/
│   └── marketplace.json          # Claude Codeマーケットプレイスメタデータ
├── plugins/
│   ├── ndf-shared/               # NDF共通編集元（直接installしない）
│   ├── ndf-claude/               # Claude Code版NDF配布物
│   ├── ndf-codex/                # Codex版NDF配布物
│   ├── ndf-kiro/                 # Kiro CLI版NDF配布物/installer
│   └── mcp/
│       ├── shared/               # MCPプラグイン共通編集元
│       ├── claude/               # Claude Code版MCP配布物
│       ├── codex/                # Codex版MCP配布物
│       └── kiro/                 # Kiro CLI版MCP配布物/installer
├── README.md
└── CLAUDE.md                     # AIエージェント向けガイドライン
```

#### Runtime plugin の検証

共通ソースや runtime 配布物を変更した場合は、生成物同期と manifest / link 検証を実行します。

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/validate-runtime-plugins.sh
```

実ランタイムのインストール経路を確認する場合は、Docker コンテナ内で smoke test を実行します。

```bash
bash scripts/runtime-smoke-test.sh
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
bash scripts/runtime-smoke-test.sh --runtime kiro
```

ローカル hook を使う場合は以下を実行します。

```bash
bash scripts/install-dev-hooks.sh
```

#### 新しいプラグインの作成手順

**1. プラグインディレクトリを作成:**

```bash
mkdir -p plugins/{plugin-name}/{.claude-plugin,commands,agents,skills}
```

**2. `plugin.json` を作成:**

```json
{
  "name": "plugin-name",
  "version": "1.0.0",
  "description": "プラグインの説明",
  "author": {
    "name": "作者名",
    "url": "https://github.com/username"
  },
  "skills": [
    {
      "path": "skills/skill-name/SKILL.md"
    }
  ]
}
```

**3. プロジェクトスキルを作成（オプション）:**

`skills/{skill-name}/SKILL.md` を作成：

```markdown
---
name: スキル名
description: スキルの説明（自動起動のキーワードを含める）
---

# スキル名

スキルの詳細説明とドキュメント...
```

**4. `marketplace.json` に登録:**

`.claude-plugin/marketplace.json` に追加：

```json
{
  "name": "ai-plugins",
  "owner": {
    "name": "takemi-ohama",
    "email": "takemi.ohama@example.com"
  },
  "plugins": [
    {
      "name": "plugin-name",
      "source": "./plugins/plugin-name",
      "description": "プラグインの簡単な説明"
    }
  ]
}
```

**5. README.md を作成:**

`plugins/{plugin-name}/README.md` を作成し、以下を含める：
- プラグインの概要
- インストール手順（マーケットプレイス追加を含む）
- 使用方法
- トラブルシューティング

**6. テストとコミット:**

```bash
# ローカルでテスト
/plugin marketplace add file:///path/to/ai-plugins
/plugin install plugin-name@ai-plugins

# 動作確認後、コミット
git add .
git commit -m "Add plugin-name plugin"
git push
```

#### 開発のベストプラクティス

**実施すること:**
- ✅ セマンティックバージョニング（MAJOR.MINOR.PATCH）に従う
- ✅ `plugin.json` に完全なメタデータを含める
- ✅ YAMLフロントマター付きの `SKILL.md` を作成
- ✅ 包括的なドキュメント（README.md）を提供
- ✅ 環境変数で認証情報を管理
- ✅ `.env` を `.gitignore` に追加
- ✅ インストール手順をテスト
- ✅ プラグイン追加時は `marketplace.json` を更新

**してはいけないこと:**
- ❌ 機密トークンや認証情報をコミット
- ❌ ドキュメントをスキップ
- ❌ バージョンインクリメントを忘れる
- ❌ 一貫性のない命名規則を使用

### マーケットプレイス管理

#### プラグインの更新

```bash
# 1. プラグインファイルを修正
# 2. plugin.json のバージョンをインクリメント
vim plugins/{plugin-name}/.claude-plugin/plugin.json

# 3. 変更をコミット
git add plugins/{plugin-name}
git commit -m "Update plugin-name to v1.1.0"
git push
```

ユーザーは Claude Code UI から更新を確認できます。

#### プラグインの削除

```bash
# 1. marketplace.json から削除
vim .claude-plugin/marketplace.json

# 2. オプションでプラグインディレクトリを削除
rm -rf plugins/{plugin-name}

# 3. 変更をコミット
git add .
git commit -m "Remove plugin-name from marketplace"
git push
```

#### バージョン管理ルール

セマンティックバージョニング（`MAJOR.MINOR.PATCH`）に従います：

- **MAJOR**: 破壊的変更（後方互換性なし）
- **MINOR**: 後方互換性のある新機能追加
- **PATCH**: バグフィックスのみ

例：
- `1.0.0 → 1.0.1`: バグ修正
- `1.0.1 → 1.1.0`: 新機能追加
- `1.1.0 → 2.0.0`: 破壊的変更

### リファレンス

#### 公式ドキュメント

- [Claude Code ドキュメント](https://docs.claude.com/en/docs/claude-code)
- [プラグインマーケットプレイス](https://code.claude.com/docs/ja/plugin-marketplaces)
- [プラグイン開発ガイド](https://docs.claude.com/en/docs/claude-code/plugins)
- [スキルドキュメント](https://docs.claude.com/en/docs/claude-code/skills)
- [MCP仕様](https://modelcontextprotocol.io)

#### MCPサーバー公式リポジトリ

- [GitHub MCP](https://github.com/github/github-mcp-server)
- [Serena MCP](https://github.com/oraios/serena)
- [Notion MCP](https://mcp.notion.com)
- [BigQuery MCP](https://github.com/ergut/mcp-server-bigquery)
- [DBHub MCP](https://github.com/bytebase/dbhub)
- [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)
- [AWS Documentation MCP](https://github.com/awslabs/aws-documentation-mcp-server)

#### プロジェクト内ドキュメント

- [CLAUDE.md](./CLAUDE.md) - AIエージェント向けガイドライン（Claude Code）
- [KIRO.md](./KIRO.md) - AIエージェント向けガイドライン（Kiro CLI）
- [docs/specifications/](./docs/specifications/) - 完了済みplan/issue由来の確定仕様
- [LICENSE](./LICENSE) - MITライセンス

## コントリビューション

1. このリポジトリをフォーク
2. 新しいプラグインを作成または既存のものを改善
3. プルリクエストを送信
4. 新しいプラグインを追加する場合は `marketplace.json` を更新

## サポート

問題が発生した場合：
1. 各プラグインの README.md を確認
2. 公式ドキュメントを参照
3. このリポジトリにイシューを開く
4. プラグイン作者に連絡（`plugin.json` を参照）

## ライセンス

MIT License - 詳細は [LICENSE](./LICENSE) ファイルを参照

---

**作成者:** takemi-ohama - https://github.com/takemi-ohama
