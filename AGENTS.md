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
- **開発の変更は `.worktrees/<ブランチ名>` の作業ツリーの中で行う**（`/ndf:worktree`）。clone したディレクトリ（主ディレクトリ）は編集対象から外す
  - `issues/` `docs/` と各ランタイムの設定は主ディレクトリで編集してよい
  - 主ディレクトリの編集は拒否されない。案内が出ても操作は成立する
- 必ずfeatureブランチを作成して作業
- Pull Requestを通じてレビュー・マージ
- ユーザーの許可なくPRを承認しない

### 版の付け方と開発版の配布

**利用者へ届く版は `main` に載ったものだけである。** マーケットプレイスはこのリポジトリを
clone して `main` を読むため、ref やブランチを指定して別の版を配る手段は無い
（`claude plugin marketplace add` に ref の指定は無く、`claude plugin install` に版の指定も
無い。実機で確認）。**開発版も `main` へ出す。区別は版数で伝える。**

| 版 | 形 | 意味 |
| --- | --- | --- |
| 正式版 | `9.5.0` | 利用者が常用してよい |
| 開発版 | `9.5.0-dev.1` | 検証中。入れたくない利用者は取得を控えられる |
| 公開前の確認版 | `9.5.0-rc.1` | 正式版の候補。残るのは確認だけ |

- 接尾辞は**次に出す正式版の版数へ付ける**。`9.4.0` の次を開発するなら `9.5.0-dev.1`
- 連番は開発版を出すたびに増やす。**同じ版数で中身を差し替えない**。差し替えると、利用者の
  手元にある版と `main` の版が同じ番号で別物になり、何を確かめたのかが分からなくなる
- **正式版を出すときは接尾辞を外す。** `9.5.0-dev.3` の次は `9.5.0`
- 順序は semver に従い `9.5.0-dev.1` < `9.5.0-rc.1` < `9.5.0` になる

**版数は 4 箇所にある。すべて揃える。** 揃っていないと `scripts/check-doc-staleness.py` と
`scripts/validate-runtime-plugins.sh` が落ちる。

| 箇所 | 何を書くか |
| --- | --- |
| `plugins/ndf/.claude-plugin/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/.codex-plugin/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `.claude-plugin/marketplace.json` の該当プラグインの `description` | 同上 |
| `plugins/ndf/README.md` の更新案内の見出し | `## vX.Y.Z へ更新するとき` |

接尾辞の付いた版でも検査は通る（形式としては妥当な版数のため）。**接尾辞の付け忘れ・外し忘れは
検査では捕まらない。** 出す前に版数を読み直す。

**開発版の公開は `release` の「検証への配布」にあたる。** 正式版の公開が「本番への配布」で、
そちらは承認が要る（`/ndf:release`）。

**ローカルのディレクトリをマーケットプレイスとして追加しない。** `marketplace.json` の `name`
が同じであるため、`claude plugin marketplace add <ローカルパス>` は `--scope local` を指定しても
**利用者のグローバルな取得元を上書きする**。続けて `marketplace remove` すると clone と導入記録
まで消える（実機で踏んだ）。手元での確認は Kiro の installer（`--project <検証用ディレクトリ>`）
か、開発版を `main` へ出してから通常の取得経路で行う。

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
│   ├── ndf/                      # NDF（3ランタイム共通の単一ディレクトリ）
│   ├── playwright-kit/           # playwright-kit（3ランタイム共通の単一ディレクトリ）
│   └── mcp/
│       └── mcp-*/               # MCPプラグイン10個（3ランタイム共通）
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
| [docs/presentations/](docs/presentations/) | 勉強会などで使うスライド資料（Marp形式）とビルド手順。**発表日時点の記録で、以後の構成変更には追随しない** |
| [docs/claude-code-skills-survey.md](docs/claude-code-skills-survey.md) | Claude Code Skills調査レポート |
| [docs/development-history/](docs/development-history/) | 開発履歴と知見 |
| [plugins/ndf/README.md](plugins/ndf/README.md) | NDFプラグイン（3ランタイム共通） |

## NDFプラグインについて

**NDFプラグイン**は、このマーケットプレイスの主要プラグインです（v9.4.0）。plugin 名は全ランタイムで `ndf` を維持し、配布物は `plugins/ndf/` の1ディレクトリにまとまっています。
- Skill の実体は `plugins/ndf/skills/` の1箇所。配布先は `plugins/ndf/manifests/*-skills.txt` が決める
- Claude Code版は 8個の専門サブエージェント、公開Skills、PreToolUse/SessionStart/Stopフックを提供
- Codex版は Codex向け公開Skillsと任意Slack通知hookを提供
- Kiro版は `plugins/ndf/dev.kiro/install.sh` で `.kiro/skills/`、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成
- 外部AI委譲は `/ndf:external-ai` skill と `corder` エージェント経由で Codex / Gemini CLI を呼び出し（v4.0.0 で Codex MCP サーバは廃止）

詳細は各 runtime README と `docs/ndf-plugin-reference.md` を参照。

## ベストプラクティス

### DO（推奨）
- コードインテリジェンスツールを活用してコード構造を理解
- ファイル全体を読む前にシンボル概要を取得
- セマンティックバージョニングに従う
- 包括的なドキュメントを提供
- 変更前にテスト
- 手順・指示書・README に書くコマンドは、書く前に実行して結果を確かめる
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
