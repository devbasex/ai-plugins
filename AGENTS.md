# AI Plugins - 開発ガイドライン

## プロジェクト概要

**Claude Code / Codex / Kiro CLI 向けプラグインマーケットプレイス**の開発プロジェクトです。チーム全体で AI 開発ツールの導入を加速するための事前設定されたプラグインを提供します。

**リポジトリ**: https://github.com/devbasex/ai-plugins

## ポリシー

### 言語とコミュニケーション
- すべてのAIエージェントとのやり取りは**日本語**で行う
- ドキュメント、コミットメッセージ、PR説明も日本語

### Git運用ルール
- **mainブランチへの直接コミット/プッシュ禁止**
- 必ずfeatureブランチを作成して作業
- Pull Requestを通じてレビュー・マージ
- ユーザーの許可なくPRを承認しない

### セキュリティ要件

**絶対にコミットしてはいけないもの**:
- APIトークン、パスワード、認証情報、秘密鍵、個人特定情報

**実施すべきこと**:
- 認証情報は環境変数で管理
- `.env.example`でテンプレートを提供
- `.gitignore`に機密ファイルを追加

### 最小限のコード実装
- 要件を満たす最小限のコードのみを記述
- 冗長な実装を避ける
- シンプルで明確な実装を優先

## マーケットプレイスの構造

```
ai-plugins/
├── .claude-plugin/
│   └── marketplace.json          # マーケットプレイス定義（必須）
├── plugins/
│   ├── ndf-shared/               # NDF共通編集元（直接installしない）
│   ├── ndf-claude/               # Claude Code版NDF
│   ├── ndf-codex/                # Codex版NDF
│   ├── ndf-kiro/                 # Kiro CLI版NDF
│   └── mcp/
│       ├── shared/               # MCPプラグイン共通編集元
│       ├── claude/               # Claude Code版MCP配布物
│       ├── codex/                # Codex版MCP配布物
│       └── kiro/                 # Kiro CLI版MCP配布物
├── docs/                         # リポジトリ知識
├── AGENTS.md                     # 共通エントリポイント
├── CLAUDE.md                     # Claude Code固有設定
├── KIRO.md                       # Kiro CLI固有設定
└── README.md                     # プロジェクト説明
```

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [docs/project-overview.md](docs/project-overview.md) | プロジェクト概要・インストール方法 |
| [docs/plugin-development-guide.md](docs/plugin-development-guide.md) | プラグイン開発ガイド（構造、plugin.json、バージョン管理、検証） |
| [docs/ndf-plugin-reference.md](docs/ndf-plugin-reference.md) | NDFプラグイン詳細リファレンス |
| [docs/specifications/](docs/specifications/) | 完了済みplan/issue由来の確定仕様 |
| [docs/claude-code-skills-survey.md](docs/claude-code-skills-survey.md) | Claude Code Skills調査レポート |
| [docs/development-history/](docs/development-history/) | 開発履歴と知見 |
| [plugins/ndf-claude/README.md](plugins/ndf-claude/README.md) | Claude Code版NDFプラグイン |
| [plugins/ndf-codex/README.md](plugins/ndf-codex/README.md) | Codex版NDFプラグイン |
| [plugins/ndf-kiro/README.md](plugins/ndf-kiro/README.md) | Kiro CLI版NDF installer |

## NDFプラグインについて

**NDFプラグイン**は、このマーケットプレイスの主要プラグインです（v4.20.1）。plugin 名は全ランタイムで `ndf` を維持し、配布物は `plugins/ndf-claude` / `plugins/ndf-codex` / `plugins/ndf-kiro` に分離しています。
- 共通編集元は `plugins/ndf-shared/`
- Claude Code版は 8個の専門サブエージェント、公開Skills、SessionStart/Stopフックを提供
- Codex版は Codex向け公開Skillsと任意Slack通知hookを提供
- Kiro版は installer で `.kiro/skills/` と `.kiro/agents/default.json` を生成
- 外部AI委譲は `/ndf:codex` skill と `corder` エージェント経由で Codex CLI を呼び出し（v4.0.0 で Codex MCP サーバは廃止）

詳細は各 runtime README と `docs/ndf-plugin-reference.md` を参照。

## ベストプラクティス

### DO（推奨）
- コードインテリジェンスツールを活用してコード構造を理解
- ファイル全体を読む前にシンボル概要を取得
- セマンティックバージョニングに従う
- 包括的なドキュメントを提供
- 変更前にテスト
- featureブランチで作業、PRを通じてマージ

### DON'T（非推奨）
- ファイル全体を無闇に読み込む
- mainブランチに直接コミット
- バージョン番号の更新を忘れる
- ドキュメントをスキップ
- 機密情報をコミット
- テストをスキップ
- ユーザーの許可なくPRを承認

## Git運用フロー

### ブランチ戦略
```bash
git checkout -b feature/{feature-name}  # 新機能開発
git checkout -b fix/{bug-name}          # バグ修正
git checkout -b docs/{doc-name}         # ドキュメント更新
```

### コミットメッセージ
日本語で明確に記述：
```
Add: 新機能追加
Update: 既存機能の更新
Fix: バグ修正
Docs: ドキュメント更新
Refactor: リファクタリング
Test: テスト追加・修正
```

### PR作成フロー
1. featureブランチで作業完了
2. 変更をコミット
3. リモートにプッシュ
4. GitHubでPR作成
5. レビュー依頼
6. ユーザーの承認後にマージ

## 参考リンク

- [Claude Code公式ドキュメント](https://docs.claude.com/en/docs/claude-code)
- [プラグインマーケットプレイス](https://code.claude.com/docs/ja/plugin-marketplaces)
- [プラグインスキル](https://docs.claude.com/en/docs/claude-code/skills)
- [MCP仕様](https://modelcontextprotocol.io)

## 検証

```bash
claude plugin validate
```
