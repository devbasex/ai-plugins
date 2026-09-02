# AI Plugins - 開発ガイドライン

## プロジェクト概要

**Claude Code / Codex / Kiro CLI 向けプラグインマーケットプレイス**の開発プロジェクトです。チーム全体で AI 開発ツールの導入を加速するための事前設定されたプラグインを提供します。

**リポジトリ**: https://github.com/devbasex/ai-plugins

## ポリシー

### 言語とコミュニケーション
- すべてのAIエージェントとのやり取りは**日本語**で行う
- ドキュメント、コミットメッセージ、PR説明も日本語

### Git運用ルール
- **`main` / `develop` への直接コミット・プッシュ禁止。** Pull Request の宛先は
  **`develop`**（開発版チャネル）。`main`（正式版チャネル）へ進めるのは配布の工程だけ
  （「版の付け方と開発版の配布」）
  - **起点は `.ndf/worktree.json` の `base_branch` が宣言する。** 作業ツリーの起点と、
    主ディレクトリの追従先と、宛先の検査がこの宣言を読む。宣言が無いリポジトリは
    既定ブランチのまま動く
  - **`--base develop` の付け忘れは継続的統合が塞ぐ。** `main` 宛の Pull Request は
    `develop` から出たものだけを通す（`scripts/check-pr-base.sh`）。判定は宣言に起点が
    書かれていて、そのブランチが origin にあるときだけ働く
- **開発の変更は `.worktrees/<ブランチ名>` の作業ツリーの中で行う**（`/ndf:worktree`）。clone したディレクトリ（主ディレクトリ）は編集対象から外す
  - `issues/` `docs/` と各ランタイムの設定は主ディレクトリで編集してよい
  - 主ディレクトリの編集は拒否されない。案内が出ても操作は成立する
- 必ずfeatureブランチを作成して作業
- Pull Requestを通じてレビュー・マージ
- ユーザーの許可なくPRを承認しない

### 版の付け方と開発版の配布

**配布のチャネルは 2 つに分ける。`main` が正式版、`develop` が開発版である。**
開発の変更は `develop` へマージし、正式版として出すときだけ `main` を `develop` の位置へ
進める。**これにより、マージと配布が別の操作になる。**

分けない場合、マージした時点で常用する利用者へ届く。レビューを通った変更であっても、利用者の
環境で動くかは配布した後にしか分からない。`release` が定めた「検証への配布 → 本番への配布」の
2 段階が、チャネルが 1 つでは 1 段階に潰れる。

| チャネル | ref | 何が載るか | 誰が登録するか |
| --- | --- | --- | --- |
| 正式版 | `main`（既定ブランチ） | 正式版として承認された版だけが載る | 常用する利用者 |
| 開発版 | `develop` | マージされた変更がそのまま載る | 開発者と、検証に参加する利用者 |

**正式版を既定ブランチへ載せるのは、利用者の取得手順を変えないためである。** 取得元の登録に
ref は保存されず（`known_marketplaces.json` は URL だけを持つ）、clone は登録した時点の既定
ブランチに固定される（fetch の refspec が `+refs/heads/main:refs/remotes/origin/main` だけになる）。
そのため**既定ブランチを別の名前へ移しても、すでに登録した利用者は `main` を追い続ける**。
正式版を `main` に置けば、既存の利用者も新しい利用者も登録し直さなくてよい。

```bash
# 常用する利用者（正式版）— これまでと同じ。ref を指定しない
claude plugin marketplace add https://github.com/devbasex/ai-plugins
codex plugin marketplace add devbasex/ai-plugins

# 検証に参加する利用者（開発版）— ref を明示する
claude plugin marketplace add https://github.com/devbasex/ai-plugins.git#develop
codex plugin marketplace add devbasex/ai-plugins --ref develop
```

ref の書き方は `owner/repo@ref` と `git-url#ref` の 2 つで、
[公式ドキュメント](https://code.claude.com/docs/en/plugin-marketplaces)に記載がある。
`claude plugin marketplace add --help` には出ないが実機で動く。Codex は `--ref` を持ち、
`owner/repo@ref` も受け取る。`claude plugin install` に版を指定する手段は無い（これは確定）。

**Kiro はマーケットプレイスの経路を持たない。** clone した作業ディレクトリから導入するため、
ref にあたるのは clone の checkout である。clone 直後は既定ブランチ（`main`）なので、正式版を
使うだけなら追加の操作は要らない。

```bash
git -C <clone> checkout develop   # 開発版を試すときだけ
bash plugins/ndf/dev.kiro/install.sh --project <ディレクトリ> --yes
```

**`develop` は開発の本流で、`main` は動かした先である。** 正式版のたびに `main` を `develop` の
位置へ fast-forward する。`main` へ直接コミットしない。

**Pull Request のベースは `develop` である。** 既定ブランチが `main` であるため、`gh pr create`
は指定しないと `main` を宛先にする。**`--base develop` を必ず付ける。**

**`main` を進めるのが `release` の「本番への配布」である。** そちらには承認が要る。`develop`
へのマージは「検証への配布」にあたり、承認なしで進めてよい（`/ndf:release`）。

**1 人の利用者は片方のチャネルしか持てない。** 取得元は名前ごとに 1 つしか登録できないため
（後述）、正式版と開発版を同時には入れられない。常用する利用者が開発版を試すときは、一時的に
登録し直すか、`claude --plugin-dir` で読み込む。

**接尾辞は人が読むための印である。** Claude Code の直接インストール経路は版数を
**キャッシュキーとしての文字列一致**でしか見ず、`-dev` や `-rc` を prerelease として
扱わない。Codex と Kiro も同様で、Agent Plugins Specification には解釈の規定が無い。
semver の順序で除外されるのは、プラグイン間の依存解決（`dependencies`）の経路だけである。
それでも接尾辞を付けるのは、**入れたくない利用者が版数を見て判断できるようにする**ためである。

**接尾辞には、チャネルを分けるうえでの役割もある。** 公式ドキュメントは release channel の
注意として、**2 つのチャネルが同じ版数へ解決されると更新が飛ばされる**と書いている。
`develop` の版数へ接尾辞を付けておけば、`main` の版数と必ず異なる。

| 版 | 形 | 意味 |
| --- | --- | --- |
| 正式版 | `9.6.0` | 利用者が常用してよい |
| 開発版 | `9.6.0-dev.1` | 検証中。入れたくない利用者は取得を控えられる |
| 公開前の確認版 | `9.6.0-rc.1` | 正式版の候補。残るのは確認だけ |

- 接尾辞は**次に出す正式版の版数へ付ける**。`9.6.0` の次を開発するなら `9.7.0-dev.1`
- 連番は開発版を出すたびに増やす。**同じ版数で中身を差し替えない**。差し替えると、利用者の
  手元にある版と `main` の版が同じ番号で別物になり、何を確かめたのかが分からなくなる
- **正式版を出すときは接尾辞を外す。** `9.6.0-dev.3` の次は `9.6.0`
- 順序は semver に従い `9.6.0-dev.1` < `9.6.0-rc.1` < `9.6.0` になる

**版数を持つ箇所は 2 種類ある。** 検査が突き合わせる 6 箇所と、**検査に載らず手で直す箇所**である。

### 検査が突き合わせる 6 箇所

揃っていないと `scripts/check-doc-staleness.py` と `scripts/validate-runtime-plugins.sh` が
落ちる。**2 つの `plugin.json` はどちらも `version` と `description` の両方に版数を持つ**ため、
片方だけ直すと検査で止まる。

| 箇所 | 何を書くか |
| --- | --- |
| `plugins/ndf/.claude-plugin/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/.claude-plugin/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `plugins/ndf/.codex-plugin/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/.codex-plugin/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `.claude-plugin/marketplace.json` の該当プラグインの `description` | 同上 |
| `plugins/ndf/README.md` の更新案内の見出し | `## vX.Y.Z へ更新するとき` |

**版を決めるのは `plugins/ndf/.claude-plugin/plugin.json` の `version` だけである。** 他の
5 箇所は読み手向けの記載と検査のための突き合わせ先で、取得する版を変えない。
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。

### 検査に載らず、手で直す箇所

**検査が見るのは説明文書の Skill 数と更新案内の見出しの版数までで、本文中の版数は対象外である。**
次は現行版を指すため、版を上げるたびに読み直す。**v9.5.0 の配布でここを取りこぼした**（#209）。

| 箇所 | 何を書いているか |
| --- | --- |
| `README.md` の概要 | 「NDFプラグイン vX.Y.Z は」 |
| `README.md` のプラグイン一覧表 | 版数の列 |
| `AGENTS.md` の「NDFプラグインについて」 | 「主要プラグインです（vX.Y.Z）」 |
| `AGENTS.md` の接尾辞の例 | 次に開発する版の例 |
| `plugins/ndf/README.md` の Kiro の確認例 | 期待出力の版数 |
| `plugins/ndf/README.md` の Codex のパス例 | `~/.codex/plugins/cache/.../ndf/X.Y.Z/` |
| `plugins/ndf/README.md` の `codex plugin list` の出力例 | 期待出力の版数 |

**すべての版数を機械的に置換しない。** 履歴（`CLAUDE.md` の版ごとの段落、
`docs/development-history/`）、記録（`issues/`）、意図的に前の版を指す文（取り消しの説明）は
そのまま残す。現行版を指しているかは文脈で決まる。

接尾辞の付いた版でも検査は通る（形式としては妥当な版数のため）。**接尾辞の付け忘れ・外し忘れは
検査では捕まらない。** 出す前に版数を読み直す。

**版を上げるのは、まとまり単位でマージが終わった後である。** Pull Request ごとには上げない。
担い手と時期の決まりは `release` にある（`/ndf:release`）。

**ローカルのディレクトリを同じ名前でマーケットプレイスとして追加しない。** 登録の鍵は取得元では
なく `marketplace.json` の `name` で、**1 つの名前につき 1 つしか登録できない**（公式ドキュメントに
"Each user can register only one marketplace per name" とある）。そのため
`claude plugin marketplace add <ローカルパス>` は `--scope local` を指定しても**利用者の取得元を
置き換える**。続けて `marketplace remove` すると clone と導入記録まで消える（実機で踏んだ）。

名前が違えば併存できるが、**同名のプラグインが両方とも有効になる**ため、どちらが使われるかが
定まらず検証の手段にならない。手元での確認は次のどちらかで行う。どちらも取得元を書き換えない。

```bash
bash plugins/ndf/dev.kiro/install.sh --project <検証用ディレクトリ> --yes   # Kiro
claude --plugin-dir plugins/ndf                                             # Claude Code
```

**正式版を出したらリリースタグを打つ。** 利用者が過去の版へ戻るときの目印になる。

```bash
claude plugin tag plugins/ndf --dry-run   # 打つ内容を確認する
claude plugin tag plugins/ndf --push      # ndf--v<版> を作って origin へ送る
```

`{プラグイン名}--v{版}` の形で作られ、打つ前に `plugin.json` の版とマーケットプレイスの項目が
食い違っていないかを検査する。

**版を持つのは `plugins/<名前>/.claude-plugin/plugin.json` だけである。**
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。マーケットプレイス側の
`description` に書かれた `(vX.Y.Z)` は読み手向けの記載で、取得する版を決める値ではない。
両方に版を持たせると `plugin.json` が無警告で優先され、食い違いに気づけなくなる。

**取り消しは利用者の側の操作になる。** こちらから前の版へ戻す手段は無い。取得元をタグへ固定
するか、別名のマーケットプレイスで対象だけを固定する。手順は
`docs/plugin-development-guide.md` の「利用者が過去の版へ戻る」にある。**配布の完了報告へ
書く「取り消しの手段」は、この 2 つとその限界を指す。**

**サードパーティのマーケットプレイスは自動更新が既定で無効である。** `main` へ出した版が
即座に全利用者へ届くわけではなく、利用者が `marketplace update` を実行した時点で届く。

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

**NDFプラグイン**は、このマーケットプレイスの主要プラグインです（v9.6.0）。plugin 名は全ランタイムで `ndf` を維持し、配布物は `plugins/ndf/` の1ディレクトリにまとまっています。
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
claude plugin validate
```
