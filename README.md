# AI Plugins

Claude Code / Codex / Kiro CLI / agy 向けのスキル・MCP設定を共有するための内部マーケットプレイスです。

## 概要

このマーケットプレイスは、チーム全体でAI開発ツール（Claude Code / Codex / Kiro CLI / agy）の導入を加速するための事前設定されたプラグインを提供します。

**NDFプラグイン v10.5.1-dev.1** は、同じ `ndf@ai-plugins` という名前で Claude Code / Codex / Kiro CLI / agy へ配布されるプラグインです。配布物は `plugins/ndf/` の1ディレクトリにまとまっており、Skill の実体は `plugins/ndf/skills/` の1箇所だけです。どのランタイムへ配るかは `plugins/ndf/manifests/*-skills.txt` が決めます。

- **公開Skills**: Claude Code向け core 40個、Kiro向け core 39個、Codex向け core 38個、agy向け core 38個に分離。
- **元Skills（40個）**:
  - PR/レビューワークフロー (7): pr, pr-tests, fix, pr-review, cherry-pick-pr, deploy, merged
  - 開発方法論 (12): development-workflow, requirements-design, design, tdd-cycle, refactoring, quality-gates, release, release-verification, retrospective, out-of-scope, progress-tracking, issue-upkeep
  - 原則・ガイドライン (10): ndf-policies, implementation-plan, plan-to-spec, investigation-rules, problem-solving, logging-guidelines, markdown-writing, notion-writing, issue-plan-strategy, ml-model-structure
  - データ分析・品質・環境 (4): qa-security-scan, docker-container-access, google-auth, official-skills-autoloader
  - 外部サービス連携 (1): google-drive
  - AIクロスレビュー (3): cross-review, cross-refactoring, external-ai
  - 開発環境 (1): worktree
  - 運用 (2): skill-stats, statusline
- **8つの専門エージェント**: director, data-analyst, corder, researcher, qa, debugger, devops-engineer, code-reviewer
- **自動フック**: 作業ツリー運用（Claude Code / Codex は PreToolUse + SessionStart、Kiro CLI は userPromptSubmit + agentSpawn、agy は PreToolUse + PreInvocation。リポジトリに `.ndf/worktree.json` があるときだけ動く）、SessionStart (transcript保持期間を最低90日に保つ)、Stop (AI要約生成+Slack通知)
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
[開発版を試す](#開発版を試す開発者向け)にあります。

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

開発版を試す場合は[開発版を試す](#開発版を試す開発者向け)を参照してください。**ローカルの
ディレクトリを同じ名前で追加しないでください。** 取得元が置き換わります。

### Kiro CLI

#### 1. リポジトリをクローン

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
```

#### 2. インストーラーを実行

```bash
# 基本（Skills + agentSpawnフックのみ）
bash plugins/ndf/dev.kiro/install.sh

# Slack通知も有効化
bash plugins/ndf/dev.kiro/install.sh --with-slack

# 全部入り（Slack + Codex CLI 連携）
bash plugins/ndf/dev.kiro/install.sh --with-slack --with-codex
```

インストーラーは `plugins/ndf/skills/` から `.kiro/skills/` への symlink、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成します。

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

既定エージェントとして使いたい場合は `bash plugins/ndf/dev.kiro/install.sh --set-default` を実行します。

詳細は [KIRO.md](./KIRO.md) を参照。

### agy

**agy はマーケットプレイスの経路を持ちません。** clone したディレクトリから
`plugins/ndf/dev.agy` を直接導入します。

#### 1. リポジトリをクローン

```bash
git clone https://github.com/devbasex/ai-plugins.git
cd ai-plugins
```

#### 2. プラグインの導入

```bash
agy plugin install plugins/ndf/dev.agy
```

導入すると Skill 33 個・エージェント 8 個・hook 1 個が
`~/.gemini/config/plugins/ndf/` へ複製されます。リポジトリ側の symlink は実体へ解決されるため、
clone を消しても導入した内容は残ります。

#### 3. 確認

```bash
agy plugin list
# => {"imports":[{"name":"ndf","source":"antigravity","components":["skills","agents","hooks"]}]}
```

**新しい版へ入れ替える手段は `uninstall` と `install` の組み合わせです**（[#289](https://github.com/devbasex/ai-plugins/issues/289)）。

### 開発版を試す（開発者向け）

**開発版は `develop` ブランチに載ります。** `main` へ進めるのは正式版を出すときだけなので、
`develop` にはマージ済みで未リリースの変更が入っています。

取得元は名前ごとに 1 つしか登録できないため、**正式版と開発版は同時に入れられません。** 常用
している環境で試すときは、一時的に登録し直すか、取得元を書き換えない手段（後述）を使います。

#### Claude Code

```bash
claude plugin marketplace add https://github.com/devbasex/ai-plugins.git#develop
claude plugin install ndf@ai-plugins
```

#### Codex

```bash
codex plugin marketplace add devbasex/ai-plugins --ref develop
codex plugin add ndf@ai-plugins
```

#### Kiro CLI

clone した作業ディレクトリから導入するため、ref にあたるのは checkout です。

```bash
git -C <クローン先> checkout develop
bash plugins/ndf/dev.kiro/install.sh --project <検証用ディレクトリ> --yes
```

#### agy

Kiro CLI と同じく、ref にあたるのは clone の checkout です。

```bash
git -C <クローン先> checkout develop
agy plugin install <クローン先>/plugins/ndf/dev.agy
```

#### 取得元を書き換えずに確かめる

リポジトリを clone してある場合は、取得元の登録に触れずに読み込めます。

```bash
claude --plugin-dir plugins/ndf                                            # Claude Code
bash plugins/ndf/dev.kiro/install.sh --project <検証用ディレクトリ> --yes  # Kiro CLI
agy plugin validate plugins/ndf/dev.agy                                    # agy（読み込みの確認）
```

**ローカルのディレクトリを同じ名前でマーケットプレイスとして追加しないでください。** 登録の鍵は
取得元ではなく `marketplace.json` の `name` で、1 つの名前につき 1 つしか登録できません。
`--scope local` を指定しても**利用者の取得元が置き換わり**、続けて `marketplace remove` すると
clone と導入記録まで消えます。

#### 開発に参加する場合

Pull Request の宛先は **`develop`** です。既定ブランチは `main`（正式版）なので、`gh pr create`
には `--base develop` を付けます。

`main` 宛の Pull Request は `develop` から出たものだけが継続的統合を通ります
（`scripts/check-pr-base.sh`）。詳細は [AGENTS.md](./AGENTS.md) を参照してください。

### 過去の版へ戻す

**版数を指定してインストールする手段はありません。** どのコードを取るかは取得元の git ref が
決めます。各プラグインの正式版へ `{プラグイン名}--v<版>`（NDF なら `ndf--v<版>`）のタグを
打つので、取得元をそのタグへ固定します。

```bash
claude plugin marketplace add devbasex/ai-plugins@<タグ>
```

**最初のタグは `ndf--v9.5.0` です。** それより前の版（9.4.0 以前）はタグを打っていないため、
**タグでは戻せません**。戻したい場合は、その版のコミットを自分で調べて ref に指定することに
なります。手元にあるタグは `git tag -l` で確かめられます。

**同じ取得元の他のプラグインも同時に過去の状態になります。** NDF だけを戻したい場合は、別名の
マーケットプレイスを用意して対象のディレクトリと ref を直接指します。手順は
[docs/plugin-development-guide.md](./docs/plugin-development-guide.md#利用者が過去の版へ戻る)
にあります。

固定した版と最新版を同時に有効にしないでください。どちらの `/ndf:*` が使われるかが定まりません。

### 利用可能なプラグイン

| プラグイン名 | バージョン | 説明 | 詳細 |
|------------|----------|------|------|
| **ndf** | 10.5.1-dev.1 | Claude Code / Codex / Kiro CLI / agy へ 1 ディレクトリから配布する NDF プラグイン。8個の専門エージェント（Claude版）、公開Skills（Claude Code向け core 40個、Kiro向け core 39個、Codex向け core 38個、agy向け core 38個）、4ランタイム共通の作業ツリー運用フック（PreToolUse / SessionStart / userPromptSubmit / agentSpawn / PreInvocation）、Claude Stopフック、Codex/Kiro向け通知・実行補助を提供。v4.0.0 で Codex MCP サーバを廃止し、`/ndf:external-ai` skill + `corder` エージェント経由の CLI 直接実行に一本化。 | [README](./plugins/ndf/README.md) |
| **playwright-kit** | 2.0.3-dev.1 | Playwright による E2E テストの計画・実装・証跡管理を提供するプラグイン。ページ役割からのテスト計画、動画 / trace 付きスクリプト実装、レポート生成と Drive 保管、playwright_kit ランタイム（init、a11y / CWV スキャン）の 4 Skill。NDF v7.0.0 で分離。 | [README](./plugins/playwright-kit/README.md) |

### 変更履歴

版ごとの変更点は [CHANGELOG.md](./CHANGELOG.md) にある。判断の理由と、その版で決めた規約は
[CLAUDE.md](./CLAUDE.md) の版ごとの段落にある。

## 開発ガイドライン

### プラグイン開発

#### ディレクトリ構造

```
ai-plugins/
├── .claude-plugin/
│   └── marketplace.json          # マーケットプレイス定義（Claude Code / Codex 共通）
├── plugins/
│   ├── ndf/                      # NDF（4ランタイム共通の単一ディレクトリ）
│   ├── playwright-kit/           # playwright-kit（3ランタイム共通の単一ディレクトリ）
│   └── mcp/
│       └── mcp-*/               # MCPプラグイン10個（3ランタイム共通）
├── README.md
└── CLAUDE.md                     # AIエージェント向けガイドライン
```

#### Runtime plugin の検証

プラグインを変更した場合は、生成物の同期と manifest / link 検証を実行します。

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

- [CHANGELOG.md](./CHANGELOG.md) - 版ごとの変更点
- [GOVERNANCE.md](./GOVERNANCE.md) - 役割・決め方・メンテナーになる道
- [CONTRIBUTING.md](./CONTRIBUTING.md) - 参加の手引き（開発の進め方・手元での検証）
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
