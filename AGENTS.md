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
ref は保存されず（Claude Code の `known_marketplaces.json` は URL だけを持つ）、登録した時点の
ref がそのまま基準になる。そのため**既定ブランチを別の名前へ移しても、すでに登録した利用者は
`main` を追い続ける**。正式版を `main` に置けば、既存の利用者も新しい利用者も登録し直さなくてよい。

**clone が取得する範囲はランタイムで違う。** 結論は同じだが、根拠となる値は別である。

| ランタイム | clone の fetch の refspec | 実測したコマンド |
| --- | --- | --- |
| Claude Code | `+refs/heads/main:refs/remotes/origin/main` | `git -C ~/.claude/plugins/marketplaces/ai-plugins config --get remote.origin.fetch` |
| Codex | `+refs/heads/*:refs/remotes/origin/*` | `git -C ~/.codex/.tmp/marketplaces/ai-plugins config --get remote.origin.fetch` |

Codex は全ブランチを取得するが、登録した ref を基準にするため、clone を手で別のブランチへ
切り替えても `codex plugin list` の版数は変わらない。

**取得元の登録と、プラグインの導入は別の操作である。** 登録は取得元の一覧を書き換えるだけで、
導入済みの実体には触れない。実体は版ごとのディレクトリ（`plugins/cache/<取得元>/<名前>/<版>/`）
に置かれ、導入の記録がそのうち 1 つを指す。**登録を切り替えただけでは、その記録は元の版を
指したままである。** 版数の表示も変わらないため、切り替わっていないことに気づく手がかりが無い。
取得元を切り替えたら、続けて導入の操作を実行する。

```bash
# 常用する利用者（正式版）— これまでと同じ。ref を指定しない
claude plugin marketplace add https://github.com/devbasex/ai-plugins
claude plugin install ndf@ai-plugins

codex plugin marketplace add devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

```bash
# 検証に参加する利用者（開発版）— ref を明示し、続けて導入する
claude plugin marketplace add https://github.com/devbasex/ai-plugins.git#develop
claude plugin install ndf@ai-plugins   # 導入済みなら claude plugin update ndf@ai-plugins

# Codex は同名の取得元の上書きを拒むため、先に外す
codex plugin marketplace remove ai-plugins
codex plugin marketplace add devbasex/ai-plugins --ref develop
codex plugin add ndf@ai-plugins
```

`claude plugin update` は「restart required to apply」と表示する。実体を入れ替えるだけで、
動いているセッションには反映されない。

ref の書き方は `owner/repo@ref` と `git-url#ref` の 2 つで、
[公式ドキュメント](https://code.claude.com/docs/en/plugin-marketplaces)に記載がある。
`claude plugin marketplace add --help` には出ないが実機で動く。Codex は `--ref` を持ち、
`owner/repo@ref` も受け取る。`claude plugin install` に版を指定する手段は無い（これは確定）。

**Kiro と agy はマーケットプレイスの経路を持たない。** clone した作業ディレクトリから
導入するため、ref にあたるのは clone の checkout である。clone 直後は既定ブランチ（`main`）
なので、正式版を使うだけなら追加の操作は要らない。

```bash
git -C <clone> checkout develop   # 開発版を試すときだけ
bash plugins/ndf/dev.kiro/install.sh --project <ディレクトリ> --yes
agy plugin uninstall ndf && agy plugin install <clone>/plugins/ndf/dev.agy
```

**agy には入れ替えの操作が無い。** 導入済みの実体を新しくするには、外してから入れ直す。

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
| 正式版 | `10.3.1` | 利用者が常用してよい |
| 開発版 | `10.3.1-dev.1` | 検証中。入れたくない利用者は取得を控えられる |
| 公開前の確認版 | `10.3.1-rc.1` | 正式版の候補。残るのは確認だけ |

- 接尾辞は**次に出す正式版の版数へ付ける**。`10.3.1` の次を開発するなら `10.4.0-dev.1`
- 連番は開発版を出すたびに増やす。**同じ版数で中身を差し替えない**。差し替えると、利用者の
  手元にある版と `main` の版が同じ番号で別物になり、何を確かめたのかが分からなくなる
- **正式版を出すときは接尾辞を外す。** `10.3.1-dev.3` の次は `10.3.1`
- 順序は semver に従い `10.3.1-dev.1` < `10.3.1-rc.1` < `10.3.1` になる

**版数を持つ箇所は 2 種類ある。** 検査が突き合わせる 15 箇所と、**検査に載らず手で直す箇所**である。

### 検査が突き合わせる 15 箇所

揃っていないと `scripts/check-doc-staleness.py` と `scripts/validate-runtime-plugins.sh` が
落ちる。**記載を消しても検査は通らない。** 位置を決める語が見つからなければ、読み取れない
こととして落ちる。

**定義ファイルと更新案内の見出しが 8 箇所である。** 3 つの `plugin.json` はいずれも
`version` と `description` の両方に版数を持つため、片方だけ直すと検査で止まる。

| 箇所 | 何を書くか |
| --- | --- |
| `plugins/ndf/.claude-plugin/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/.claude-plugin/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `plugins/ndf/.codex-plugin/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/.codex-plugin/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `plugins/ndf/dev.agy/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/dev.agy/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `.claude-plugin/marketplace.json` の該当プラグインの `description` | 同上 |
| `plugins/ndf/README.md` の更新案内の見出し | `## vX.Y.Z へ更新するとき` |

**説明文書の本文が 7 箇所である。** いずれも利用者が読む入口にあり、古いまま残ると現行版を
誤って伝えるか、書かれたとおりに実行できない。

| 箇所 | 何を書くか | 突き合わせ先 |
| --- | --- | --- |
| `README.md` の概要 | `**NDFプラグイン vX.Y.Z**` | `ndf` の `plugin.json` |
| `README.md` のプラグイン一覧表 | 版数の列 | 行ごとに `plugins/<名前>/.claude-plugin/plugin.json` |
| `AGENTS.md` の「NDFプラグインについて」 | 「主要プラグインです（vX.Y.Z）」 | `ndf` の `plugin.json` |
| `AGENTS.md` の「版の付け方と開発版の配布」節 | 版数の例 | `ndf` の `plugin.json`（基底の比較） |
| `plugins/ndf/README.md` の Kiro の確認例 | 期待出力の版数 | `ndf` の `plugin.json` |
| `plugins/ndf/README.md` の Codex のパス例 | `~/.codex/plugins/cache/.../ndf/X.Y.Z/`（2 箇所） | `ndf` の `plugin.json` |
| `plugins/ndf/README.md` の `codex plugin list` の出力例 | 期待出力の版数 | `ndf` の `plugin.json` |

**「版の付け方と開発版の配布」節だけは扱いが違う。** この節には現行版そのもの・接尾辞を
付けたもの・次に出す版を指すものが混ざるため、1 つの値とは照合しない。節に並ぶ版数の
**基底**（接尾辞を除いた数字 3 つ）が現行版の基底より小さければ落ちる。次の版を指す例は通る。

**この節の版数は囲みを付けて書く。** 検査が拾うのは `` `9.6.0` `` のように囲まれた版数だけ
である。節には配布に使う CLI の名前と版数を並べて書くことがあり、`codex-cli 0.146.1` のような
他のソフトの版数まで現行版と比べると誤検出になる。囲まずに書いた版数は走査に入らない。

**版を決めるのは `plugins/ndf/.claude-plugin/plugin.json` の `version` だけである。** 他の
14 箇所は読み手向けの記載と検査のための突き合わせ先で、取得する版を変えない。
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。

### 検査に載らず、手で直す箇所

**検査が見るのは版数そのものの一致までで、記載の中身までは見ない。** 次の 2 つは版を
上げるたびに人が読み直す。

| 箇所 | 何を確かめるか |
| --- | --- |
| `plugins/ndf/README.md` の更新案内の本文 | その版の変更を説明しているか。見出しの版数は検査が見るが、本文が何を説明しているかは機械では判定できない |
| 接尾辞の付け忘れ・外し忘れ | 接尾辞の付いた版でも検査は通る（形式としては妥当な版数のため）。出す前に版数を読み直す |

**すべての版数を機械的に置換しない。** 履歴（`CLAUDE.md` の版ごとの段落、
`docs/development-history/`）、記録（`issues/`）、意図的に前の版を指す文（取り消しの説明）は
そのまま残す。現行版を指しているかは文脈で決まる。**検査が突き合わせるのは、周囲の固定の語で
位置を決めた記載だけである。** 履歴と記録は最初から走査に入らない。

**v9.5.0 の配布では、説明文書の本文に書かれた版数を取りこぼした**（#209）。上の 7 箇所を
検査の対象へ入れたのは、この取りこぼしを次から機械が拾うためである。

**版を上げるのは、まとまり単位でマージが終わった後である。** Pull Request ごとには上げない。
担い手と時期の決まりは `release` にある（`/ndf:release`）。

**ローカルのディレクトリを同じ名前でマーケットプレイスとして追加しない。** 登録の鍵は取得元では
なく `marketplace.json` の `name` で、**1 つの名前につき 1 つしか登録できない**（公式ドキュメントに
"Each user can register only one marketplace per name" とある）。

**同名の登録に対する振る舞いはランタイムで違う。** どちらも、いま登録している取得元を
別のものへ向けることになる。

| ランタイム | 同名で別の取得元を追加したとき | `marketplace remove` が消すもの |
| --- | --- | --- |
| Claude Code | `claude plugin marketplace add <ローカルパス>` は `--scope local` を指定しても利用者の取得元を置き換える（実機で踏んだ） | clone と導入記録まで消える（実機で踏んだ） |
| Codex | `marketplace 'ai-plugins' is already added from a different source; remove it before adding this source` を返して拒む | clone だけを消す。導入の記録（`~/.codex/config.toml` の `[plugins."ndf@ai-plugins"]`）は残るため、`add` し直せば有効な状態に戻る |

名前が違えば併存できるが、**同名のプラグインが両方とも有効になる**ため、どちらが使われるかが
定まらず検証の手段にならない。手元での確認は次のどちらかで行う。どちらも取得元を書き換えない。

```bash
bash plugins/ndf/dev.kiro/install.sh --project <検証用ディレクトリ> --yes   # Kiro
claude --plugin-dir plugins/ndf                                             # Claude Code
agy plugin validate plugins/ndf/dev.agy                                     # agy（読み込みの確認）
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
| [docs/plugin-development-guide.md](docs/plugin-development-guide.md) | プラグイン開発ガイド（構造、plugin.json、バージョン管理、検証） |
| [docs/ndf-plugin-reference.md](docs/ndf-plugin-reference.md) | NDFプラグイン詳細リファレンス |
| [docs/specifications/](docs/specifications/) | 完了済みplan/issue由来の確定仕様 |
| [docs/presentations/](docs/presentations/) | 勉強会などで使うスライド資料（Marp形式）とビルド手順。**発表日時点の記録で、以後の構成変更には追随しない** |
| [docs/claude-code-skills-survey.md](docs/claude-code-skills-survey.md) | Claude Code Skills調査レポート |
| [docs/development-history/](docs/development-history/) | 2026-09-02 までの開発履歴と知見。**以降の振り返りは、起点の issue か Pull Request のコメントに残す**（`/ndf:retrospective`） |
| [plugins/ndf/README.md](plugins/ndf/README.md) | NDFプラグイン（4ランタイム共通） |

## NDFプラグインについて

**NDFプラグイン**は、このマーケットプレイスの主要プラグインです（v10.3.1）。plugin 名は全ランタイムで `ndf` を維持し、配布物は `plugins/ndf/` の1ディレクトリにまとまっています。
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
