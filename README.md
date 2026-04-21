# AI Plugins

Claude Codeプラグイン、プロジェクトスキル、MCP（Model Context Protocol）設定を共有するための内部マーケットプレイスです。

## 概要

このマーケットプレイスは、チーム全体でClaude Codeの導入を加速するための事前設定されたプラグインを提供します。

**NDFプラグイン**は、以下の機能を**オールインワン**で提供する統合プラグインです：

- **2つのコアMCPサーバー**: Serena、Codex CLI
- **9つの開発ワークフローコマンド**: `/ndf:pr`, `/ndf:pr-tests`, `/ndf:fix`, `/ndf:review`, `/ndf:merged`, `/ndf:clean`, `/ndf:serena`, `/ndf:mem-review`, `/ndf:mem-capture`
- **6つの専門エージェント**: **director指揮者**、データ分析、コーディング、調査、ファイル読み取り、品質管理
- **13個のSkills**: SQL最適化、データエクスポート、コードテンプレート、テスト生成、Python実行、Docker判定、Skill開発、調査レポート、PDF解析、Excel抽出、セキュリティスキャン、Markdown文書作成、記憶戦略
- **自動フック**: Claude Code終了時にAI要約生成とSlack通知

> **Note (v2.6.0)**: 追加のMCP（BigQuery、Chrome DevTools、AWS Docs、DBHub、Notion）は個別プラグインとしてインストール可能です。GitHub MCP、Context7 MCPは公式プラグインから利用できます。

## 利用方法

### 1. マーケットプレイスの追加

Claude Codeでマーケットプレイスを追加します：

```bash
/plugin marketplace add https://github.com/devbasex/ai-plugins
```

### 2. プラグインのインストール

利用したいプラグインをインストールします：

```bash
# NDFプラグイン（オールインワン統合プラグイン）
/plugin install ndf@ai-plugins
```

このプラグイン1つで、MCP統合、開発ワークフロー、専門エージェント、自動フックのすべてが利用可能です。

### 利用可能なプラグイン

| プラグイン名 | バージョン | 説明 | 詳細 |
|------------|----------|------|------|
| **ndf** | 2.6.0 | Claude Code開発環境を**オールインワン**で強化する統合プラグイン。2つのコアMCPサーバー（Serena、Codex CLI）、9つの開発ワークフローコマンド、6つの専門エージェント（**director指揮者**、データ分析、コーディング、調査、ファイル読み取り、品質管理）、13個のSkills、Stopフック（AI要約生成とSlack通知）を提供。追加のMCPは個別プラグインとしてインストール可能。 | [README](./plugins/ndf/README.md) |

## 開発ガイドライン

### プラグイン開発

#### ディレクトリ構造

```
ai-plugins/
├── .claude-plugin/
│   └── marketplace.json          # マーケットプレイスメタデータ
├── plugins/
│   └── {plugin-name}/
│       ├── .claude-plugin/
│       │   └── plugin.json       # プラグインメタデータ（必須）
│       ├── commands/             # スラッシュコマンド (*.md)
│       ├── agents/               # サブエージェント (*.md)
│       └── skills/               # プロジェクトスキル
│           └── {skill-name}/
│               └── SKILL.md      # エントリポイント（必須）
├── README.md
└── CLAUDE.md                     # AIエージェント向けガイドライン
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

- [CLAUDE.md](./CLAUDE.md) - AIエージェント向けガイドライン
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
