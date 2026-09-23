# AI Plugins - 開発ガイドライン

## プロジェクト概要

**Claude Code / Codex / Kiro CLI / agy 向けプラグインマーケットプレイス**の開発プロジェクトです。チーム全体で AI 開発ツールの導入を加速するための事前設定されたプラグインを提供します。

**リポジトリ**: https://github.com/devbasex/ai-plugins

## ポリシー

### 言語とコミュニケーション
- すべてのAIエージェントとのやり取りは**日本語**で行う
- ドキュメント、コミットメッセージ、PR説明も日本語

### Git運用ルール
- **`main` / `develop` への直接コミット・プッシュ禁止。** Pull Request の宛先は
  **`develop`**（開発版チャネル）。`main`（正式版チャネル）へ進めるのは配布の工程だけ
  （「版と配布の方針」）
  - **起点は `.ndf/worktree.json` の `base_branch` が宣言する。** 作業ツリーの起点と、
    宛先の検査がこの宣言を読む（`follow_branch: true` のときは主ディレクトリの追従先にも
    なる）。宣言が無いリポジトリは既定ブランチのまま動く
  - **`--base develop` の付け忘れは継続的統合が塞ぐ。** `main` 宛の Pull Request は
    `develop` から出たものだけを通す（`scripts/check-pr-base.sh`）。判定は宣言に起点が
    書かれていて、そのブランチが origin にあるときだけ働く
- **開発の変更は `.worktrees/<ブランチ名>` の作業ツリーの中で行う**（`/ndf:worktree`）。clone したディレクトリ（主ディレクトリ）は編集対象から外す
  - `issues/` `docs/` と各ランタイムの設定は主ディレクトリで編集してよい
  - 主ディレクトリの編集は拒否されない。案内が出ても操作は成立する
- 必ずfeatureブランチを作成して作業
- Pull Requestを通じてレビュー・マージ
- ユーザーの許可なくPRを承認しない

### 版と配布の方針

**判断の基準だけをここに置く。** 版数と配布の手順・実測・一覧は
[docs/versioning-and-distribution.md](docs/versioning-and-distribution.md) にある（版数の扱いの正本）。

**配布のチャネルは 2 つに分ける。** マージと配布を別の操作にするためである。

| チャネル | ref | 何が載るか |
| --- | --- | --- |
| 正式版 | `main`（既定ブランチ） | 正式版として承認された版だけが載る |
| 開発版 | `develop` | マージされた変更がそのまま載る |

**正式版は既定ブランチ（`main`）へ置く。** 取得元の登録に ref は保存されないため、既定ブランチに
置けば既存の利用者も新しい利用者も登録し直さなくてよい。

**開発版の版数には接尾辞を付ける。** 接尾辞は人が読むための印で、入れたくない利用者が版数を見て
判断できるようにするためである。次に出す正式版の版数へ付け、正式版を出すときに外す（形の一覧は正本の「版の付け方と開発版の配布」）。

**版を上げるのは、まとまり単位でマージが終わった後である。** Pull Request ごとには上げない。
担い手と時期の決まりは `release` にある（`/ndf:release`）。

**マイルストーンの名前は版数を持たない。** 版数を決めるのはまとまりの中身であり、名前を
付ける時点では決まっていない。

| 状態 | 名前の形 | 何を表すか |
| --- | --- | --- |
| open | `<2 桁の連番> <主題>`（例: `01 リファクタリングの方法論と構造`） | **着手の順序だけ。** 出す版は決まっていない |
| closed | `v<版数>` | **出た版。** タグ（`ndf--v<版数>`）と `CHANGELOG.md` と一致する |

**閉じるときに改名する。** 配布で版が決まった時点が、名前を版数へ変えられる最初の時点である。

**連番は再利用しない。** `01` を閉じて版数へ改名しても、次に作るのは番号の続きである。詰め直すと、
以前の記録が指す番号が別のまとまりを指す。

**名前を版数にすると 2 つが壊れる。** 名前は着手の前に付き、版の大小は中身が固まるまで決まらない
ため、見積が外れると名前と出た版が食い違う。**食い違いを直そうとしても、その版数を着手待ちの
マイルストーンが使っていれば改名できない**（GitHub のマイルストーンのタイトルは一意である）。
あわせて、タイトルの辞書順が着手の順序と一致しなくなる（10.10.0 は 10.5.3 より前に来る）。

**版を決めるのは `plugins/<名前>/.claude-plugin/plugin.json` の `version` だけである。**
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。

**ローカルのディレクトリを同じ名前でマーケットプレイスとして追加しない。** 1 つの名前につき
1 つしか登録できず、利用者の取得元が置き換わる（ランタイムごとの振る舞いは正本の「ランタイムごとの取得と導入」）。

**正式版を出したらリリースタグを打つ。** 利用者が過去の版へ戻るときの目印になる（手順は正本の「正式版を出す」）。

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
│   ├── ndf/                      # NDF（4ランタイム共通の単一ディレクトリ）
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
| [docs/plugin-development-guide.md](docs/plugin-development-guide.md) | プラグイン開発ガイド（構造、plugin.json、検証） |
| [docs/versioning-and-distribution.md](docs/versioning-and-distribution.md) | 版と配布（チャネル、版の付け方、ランタイムごとの取得と導入、版数を持つ 15 箇所、過去の版へ戻る）。版数の扱いの正本 |
| [docs/ndf-plugin-reference.md](docs/ndf-plugin-reference.md) | NDFプラグイン詳細リファレンス |
| [docs/specifications/](docs/specifications/) | 完了済みplan/issue由来の確定仕様 |
| [docs/presentations/](docs/presentations/) | 勉強会などで使うスライド資料（Marp形式）とビルド手順。**発表日時点の記録で、以後の構成変更には追随しない** |
| [docs/articles/](docs/articles/README.md) | 社外ブログへ載せる記事の正本。**書いた日時点の記録で、以後の構成変更には追随しない** |
| [docs/claude-code-skills-survey/](docs/claude-code-skills-survey/01-findings.md) | Claude Code Skills調査レポート（3 本） |
| [docs/development-history/](docs/development-history/) | 2026-09-02 までの開発履歴と知見。**以降の振り返りは、起点の issue か Pull Request のコメントに残す**（`/ndf:retrospective`） |
| [plugins/ndf/README.md](plugins/ndf/README.md) | NDFプラグイン（4ランタイム共通） |

## NDFプラグインについて

**NDFプラグイン**は、このマーケットプレイスの主要プラグインです（v10.17.1-dev.1）。plugin 名は全ランタイムで `ndf` を維持し、配布物は `plugins/ndf/` の1ディレクトリにまとまっています。
- Skill の実体は `plugins/ndf/skills/` の1箇所。配布先は `plugins/ndf/manifests/*-skills.txt` が決める
- Claude Code版は 8個の専門サブエージェント、公開Skills、PreToolUse/SessionStart/Stopフックを提供
- Codex版は Codex向け公開Skillsと任意Slack通知hookを提供
- Kiro版は `plugins/ndf/dev.kiro/install.sh` で `.kiro/skills/`、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成
- agy版は `plugins/ndf/dev.agy/` を `agy plugin install` で導入し、公開Skills・エージェント定義・PreToolUse/PreInvocationフックを提供
- 外部AI委譲は `/ndf:external-ai` skill と `corder` エージェント経由で Codex / agy を呼び出し（v4.0.0 で Codex MCP サーバは廃止）

詳細は各 runtime README と `docs/ndf-plugin-reference.md` を参照。

## ベストプラクティス

### DO（推奨）
- コードインテリジェンスツールを活用してコード構造を理解
- ファイル全体を読む前にシンボル概要を取得
- セマンティックバージョニングに従う
- 包括的なドキュメントを提供
- 変更前にテスト
- 手順・指示書・README に書くコマンドは、書く前に実行して結果を確かめる
- **コードやスクリプトが呼ぶ外部コマンドも、書く前に実行して挙動を確かめる。** 確かめる観点は
  パターンの一致範囲・失敗時の終了コードと出力・解決の順序の 3 つで、いずれも直感と食い違う
  ことがある。**テストを書いても防げない**（書き手が同じ思い込みを持てば、期待値も同じ形に
  なって通る）
- **確かめる対象は外部コマンドに限らない。自分で書いた側の入力と出力の形も同じように
  確かめる。** 落ちるのは形の組み合わせであって、呼ぶ相手ではない
  - **その入力を実際に作る側（このリポジトリの Skill・手順書）が定める形を、1 度そのまま
    通す。** テストの入力はその形から作る。1 行の値とファイル渡しだけで試すと、手順が必須と
    定める複数行の形が一度も通らないまま通過する
  - **内部で決めた区切り（改行 / NUL / タブ）は、読む側と書く側の両方を同時に見る。** 片方
    だけを見ると、書いた側が 1 つの語として渡した値が読む側で割れても、失敗として現れない
- featureブランチで作業、`develop` 宛の PR を通じてマージ

### DON'T（非推奨）
- ファイル全体を無闇に読み込む
- `main` / `develop` に直接コミット
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
claude plugin validate .
```

**`path` は必須の位置引数である。** 省くと `error: missing required argument 'path'` で
終了コード 1 になる（Claude Code 2.1.261 で実測）。マーケットプレイスの根で `.` を渡すと
`marketplace.json` と各プラグインの定義を検査し、終了コード 0 で終わる。`policy` と
`interface` は Claude Code が読み込み時に無視するため未知フィールドの警告が出るが、
**警告は終了コードを変えない**。合否は出力ではなく終了コードで見る。
