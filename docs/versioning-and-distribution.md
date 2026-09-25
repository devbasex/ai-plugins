# 版と配布

NDF プラグインの版数の付け方と、正式版・開発版の配布の手順・実測・一覧を持つ。**この主題の正本である。**
判断の基準（何を選ぶか）は [AGENTS.md](../AGENTS.md) の「版と配布の方針」にあり、この文書は
どう動くかとどう確かめるかを扱う。出た版ごとの判断は [ndf-version-decisions.md](ndf-version-decisions.md) にある。

## チャネルと ref

**配布のチャネルは 2 つに分ける。`main` が正式版、`develop` が開発版である。**
開発の変更は `develop` へマージし、正式版として出すときだけ `develop` の内容を `main` へ
送る。**これにより、マージと配布が別の操作になる。**

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

**1 人の利用者は片方のチャネルしか持てない。** 取得元は名前ごとに 1 つしか登録できないため
（後述）、正式版と開発版を同時には入れられない。常用する利用者が開発版を試すときは、一時的に
登録し直すか、`claude --plugin-dir` で読み込む。

## 版の付け方と開発版の配布

**セマンティックバージョニング**:
- **MAJOR**: 破壊的変更
- **MINOR**: 後方互換性のある新機能
- **PATCH**: バグフィックス

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
| 正式版 | `10.17.12` | 利用者が常用してよい |
| 開発版 | `10.18.0-dev.1` | 検証中。入れたくない利用者は取得を控えられる |
| 公開前の確認版 | `10.18.0-rc.1` | 正式版の候補。残るのは確認だけ |

- 接尾辞は**次に出す正式版の版数へ付ける**。`10.17.12` の次を開発するなら `10.18.0-dev.1`
- 連番は開発版を出すたびに増やす。**同じ版数で中身を差し替えない**。差し替えると、利用者の
  手元にある版と `main` の版が同じ番号で別物になり、何を確かめたのかが分からなくなる
- **正式版を出すときは接尾辞を外す。** `10.18.0-dev.3` の次は `10.18.0`
- 順序は semver に従い `10.18.0-dev.1` < `10.18.0-rc.1` < `10.18.0` になる

## ランタイムごとの取得と導入

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

**agy には入れ替えの操作が無い。** `agy plugin --help` の副コマンドは
`list` / `import` / `install` / `uninstall` / `enable` / `disable` / `validate` / `link` /
`help` で、導入済みを新しい版へ入れ替えるものが無い。他の 3 ランタイムは持つ
（Claude Code は `claude plugin update`、Codex は取得元を更新してから `codex plugin add`、
Kiro CLI はインストーラの再実行）。

**`install` は既存の導入を拒まず、上書きする。ただし消えたファイルは残る**（agy 1.1.26 /
2026-09-05 に実測）。同じ名前で導入し直すと `plugin.json` の版数も Skill の中身も入れ替わるが、
**取得元から取り除いたファイルは実体に残り続ける**。配布物が減る版へ上げると、消したはずの
Skill が利用者の手元で生き続ける。そのため案内は外してから入れ直す 2 手のままにする。

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
定まらず検証の手段にならない。手元での確認は次のいずれかで行う。どれも取得元を書き換えない。

```bash
bash plugins/ndf/dev.kiro/install.sh --project <検証用ディレクトリ> --yes   # Kiro
claude --plugin-dir plugins/ndf                                             # Claude Code
agy plugin validate plugins/ndf/dev.agy                                     # agy（読み込みの確認）
```

**agy は導入したプラグインの `hooks.json` を読み込まない。** 読む先は
`~/.gemini/config/hooks.json` の 1 か所だけで、プラグイン配下とプロジェクト直下のどちらに
置いても `loaded 1 named hooks from 1 hooks.json file(s)` のまま変わらない（agy 1.1.26 で
実測）。導入した hook を効かせるには `bash plugins/ndf/dev.agy/install-hooks.sh` を実行して
その 1 か所へ差し込む。冪等で、他の項目には触れない。

## 開発版を試す

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

**サードパーティのマーケットプレイスは自動更新が既定で無効である。** `main` へ出した版が
即座に全利用者へ届くわけではなく、利用者が `marketplace update` を実行した時点で届く。

## 正式版を出す

**`develop` は開発の本流で、`main` は動かした先である。** 正式版のたびに `develop` から `main`
への Pull Request を作り、管理者の bypass でマージする。`main` へ直接コミットしない。

**`git push origin develop:main` は使えない。** `main` / `develop` を守る ruleset の bypass は
`pull_request` で作ってあり、**このモードは Pull Request のマージだけを通し、直接 push は
管理者でも拒む**。必須の検査 12 個は Pull Request で走るため、`develop` の先端のコミットには
`push` 起動の 1 個しか結果が付いていない。

```console
$ git push origin develop:main
remote:
remote: - 10 of 11 required status checks have not succeeded: .
remote:
 ! [remote rejected] develop -> main (push declined due to repository rule violations)
```

この例は必須の検査が 11 個だったときの観測である。`instruction-files-check` を足して 12 個に
なった後は `11 of 12` になる。

**この手順では `main` に `develop` へ無いマージコミットが 1 つ積まれる。** そのため `main` は
`develop` の fast-forward ではなくなる。**それでよい。** 配布した版を指すのは `main` の先端で
あって、両者が同じコミットを指していることではない。中身が一致していることは差分で確かめる。

```bash
gh pr create --base main --head develop --title "Release: v<版>" --body "..."
gh pr merge <番号> --merge --admin      # 承認 1 件必須。メンテナーが 1 人の間は bypass で通す
git fetch origin && git diff --stat origin/develop origin/main   # 空であること
```

**`--admin` を使うのは、GitHub が自分の Pull Request を自分で承認できないためである。**
承認 1 件必須のもとでメンテナーが 1 人の間は、これが唯一の経路になる。承認できる人が増えたら
`--admin` を外し、通常の承認を経てマージする。

**Pull Request のベースは `develop` である。** 既定ブランチが `main` であるため、`gh pr create`
は指定しないと `main` を宛先にする。**`--base develop` を必ず付ける。**

**`main` を進めるのが `release` の「本番への配布」である。** そちらには承認が要る。`develop`
へのマージは「検証への配布」にあたり、承認なしで進めてよい（`/ndf:release`）。

**正式版の配布の Pull Request には、版ごとのトークン消費の記録が載る。** `release` の手順 3 で
`.ndf/release.json` の段（`scripts/token-usage-snapshot.py --released <版>`）が走り、
`docs/metrics/ndf-token-usage/<集計日>.md` / `.json` を書く。前の行との比が ±30% を超えた版の
読み取りだけを、[手引き](metrics/ndf-token-usage/README.md) に従って書き足す。開発版の配布では残さない。

**正式版を出したらリリースタグを打つ。** 利用者が過去の版へ戻るときの目印になる。

```bash
claude plugin tag plugins/ndf --dry-run   # 打つ内容を確認する
claude plugin tag plugins/ndf --push      # ndf--v<版> を作って origin へ送る
```

`{プラグイン名}--v{版}` の形で作られ、打つ前に `plugin.json` の版とマーケットプレイスの項目が
食い違っていないかを検査する。

## 版数を持つ 15 箇所

**版数を持つ箇所は 2 種類ある。** 検査が突き合わせる 15 箇所と、**検査に載らず手で直す箇所**である。

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
| `docs/versioning-and-distribution.md` の「版の付け方と開発版の配布」章 | 版数の例 | `ndf` の `plugin.json`（基底の比較） |
| `plugins/ndf/README.md` の Kiro の確認例 | 期待出力の版数 | `ndf` の `plugin.json` |
| `plugins/ndf/README.md` の Codex のパス例 | `~/.codex/plugins/cache/.../ndf/X.Y.Z/`（2 箇所） | `ndf` の `plugin.json` |
| `plugins/ndf/README.md` の `codex plugin list` の出力例 | 期待出力の版数 | `ndf` の `plugin.json` |

**「版の付け方と開発版の配布」章だけは扱いが違う。** この章には現行版そのもの・接尾辞を
付けたもの・次に出す版を指すものが混ざるため、1 つの値とは照合しない。章に並ぶ版数の
**基底**（接尾辞を除いた数字 3 つ）が現行版の基底より小さければ落ちる。次の版を指す例は通る。

**この章の版数は囲みを付けて書く。** 検査が拾うのは `` `9.6.0` `` のように囲まれた版数だけ
である。章には配布に使う CLI の名前と版数を並べて書くことがあり、`codex-cli 0.146.1` のような
他のソフトの版数まで現行版と比べると誤検出になる。囲まずに書いた版数は走査に入らない。

**章の中の次に出す版を指す例は、同じ章の例どうしで比べる。** 版数を一括で置換すると、次に出す版を
指すはずの例が現行版を指す形へ崩れる（v10.10.0 は、開発版の行が正式版の行と同じ値のまま配布された。#566）。
崩れた値は現行版そのものと等しいことがあり、基底の規則では拾えない。そこで次の 2 つを、周囲の語で
位置を決めて拾う。

| 位置を決める語 | 求める形 |
| --- | --- |
| 版の形の表の 1 列目の `正式版` / `開発版` / `公開前の確認版` | 3 つの行が 1 つずつある。正式版の行は接尾辞を持たず、開発版の行は `-dev.<連番>`、公開前の確認版の行は `-rc.<連番>` で終わる。開発版と公開前の確認版の行の基底は、正式版の行の基底より大きい |
| 「の次を開発するなら」 | 1 つ以上ある。右側の基底が左側の基底より大きく、右側が `-dev.<連番>` で終わる |

**この 2 つは現行版と比べない。** `develop` の版は接尾辞付きになりうるため、現行版との一致を求めると
開発版を出すたびに正しい例が落ちる。正式版の行を現行版へ結び付けるのは、上の基底の規則である。

**位置を決める語は言い換えられない。** 言い換えると読み取れないこととして落ちる。1 列目に同じ語を持つ
表を章へ足すと、同じ行が複数あるとして落ちる。コードの囲みの中の表と文は、実行例か出力例として数えない。

**版を決めるのは `plugins/ndf/.claude-plugin/plugin.json` の `version` だけである。** 他の
14 箇所は読み手向けの記載と検査のための突き合わせ先で、取得する版を変えない。
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。

**版を持つのは `plugins/<名前>/.claude-plugin/plugin.json` だけである。**
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。マーケットプレイス側の
`description` に書かれた `(vX.Y.Z)` は読み手向けの記載で、取得する版を決める値ではない。
両方に版を持たせると `plugin.json` が無警告で優先され、食い違いに気づけなくなる。

`scripts/validate-runtime-plugins.sh` が突き合わせるのは、説明文書に書かれた Skill の数と、
**版数を書いた 15 箇所**です。定義ファイルと更新案内の見出しが 8 箇所、説明文書の本文が
7 箇所あります（`README.md` の概要とプラグイン一覧表、`AGENTS.md` と正本に 1 箇所ずつ、
`plugins/ndf/README.md` の Kiro と Codex の確認例 3 種類）。**記載を消しても検査は通りません。**
一覧は上の 2 つの表にあります。

## 検査に載らず手で直す箇所

**検査が見るのは版数そのものの一致までで、記載の中身までは見ない。** 次の 3 つは版を
上げるたびに人が読み直す。

| 箇所 | 何を確かめるか |
| --- | --- |
| `plugins/ndf/README.md` の更新案内の本文 | その版の変更を説明しているか。見出しの版数は検査が見るが、本文が何を説明しているかは機械では判定できない |
| 接尾辞の付け忘れ・外し忘れ | 接尾辞の付いた版でも検査は通る（形式としては妥当な版数のため）。出す前に版数を読み直す |
| `CHANGELOG.md` の版の節 | その版で何が変わったかを書く。版数と日付は機械で確かめられるが、内容は書かないと残らない |

**バージョン更新時の手順**:
1. `plugin.json`のバージョンをインクリメント
2. 変更内容をドキュメント化
3. `plugins/ndf/README.md` の「v&lt;版&gt; へ更新するとき」の節を開き、**本文をその版の変更内容へ書き直す**。見出しの版数だけを置き換えて、本文を前の版の説明のまま残さない
4. `CHANGELOG.md` の先頭へその版の節を足す。書式は Keep a Changelog に従い、変更点を
   `追加` / `変更` / `修正` / `削除` へ分類して 1〜2 行で書く。判断の理由は
   `docs/ndf-version-decisions.md` へ置く
5. Skill の数が増減した場合は、`README.md` と `plugins/ndf/README.md` に書かれた数を書き直す
6. `python3 scripts/check-doc-staleness.py --root .` を実行し、説明文書に残った古い版数を
   出力の行番号のとおりに直す
7. `python3 plugins/ndf/scripts/instructions-check.py --root .` を実行し、出た版の段落が
   指示書に残っていないかを見る。落ちた段落は `docs/ndf-version-decisions.md` へ移す
8. 破壊的変更がある場合は明示
9. テストを実行
10. **正式版として `main` を進めた後、リリースタグを打つ**

**すべての版数を機械的に置換しない。** 履歴（`docs/ndf-version-decisions.md`、`CLAUDE.md` の
現行版の段落、`docs/development-history/`）、記録（`issues/`）、意図的に前の版を指す文（取り消しの説明）は
そのまま残す。現行版を指しているかは文脈で決まる。**検査が突き合わせるのは、周囲の固定の語で
位置を決めた記載だけである。** 履歴と記録は最初から走査に入らない。

**v9.5.0 の配布では、説明文書の本文に書かれた版数を取りこぼした**（#209）。上の 7 箇所を
検査の対象へ入れたのは、この取りこぼしを次から機械が拾うためである。

## 取得元の登録を確かめる

**取得元の登録そのものを確かめるときは、設定ディレクトリを隔離する。** 「ランタイムごとの取得と導入」の手元での確認の 3 つは配布物を
直接読み込む確認で、取得元の登録から導入までの経路は通らない。開発版チャネルの取得手順は
その経路そのものであるため、別の設定ディレクトリを与えて確かめる。**利用者の登録には触れない**
（claude 2.1.261 / codex-cli 0.153.2 で実測）。

```bash
ISO=$(mktemp -d "$HOME/.ndf-channel-check.XXXXXX"); mkdir -p "$ISO/claude" "$ISO/codex"

CLAUDE_CONFIG_DIR="$ISO/claude" claude plugin marketplace add \
  'https://github.com/devbasex/ai-plugins.git#develop'
CLAUDE_CONFIG_DIR="$ISO/claude" claude plugin install ndf@ai-plugins
CLAUDE_CONFIG_DIR="$ISO/claude" claude plugin list          # 版数を見る

CODEX_HOME="$ISO/codex" codex plugin marketplace add devbasex/ai-plugins --ref develop
CODEX_HOME="$ISO/codex" codex plugin add ndf@ai-plugins
CODEX_HOME="$ISO/codex" codex plugin list                   # 版数を見る

rm -rf "$ISO"
```

- **`CODEX_HOME` は先に作っておく。** 存在しないパスを渡すと
  `CODEX_HOME points to "..." , but that path does not exist` で落ちる
- **隔離先は `$HOME` の下に置く。** `/tmp` の下に置くと Codex が
  `Refusing to create helper binaries under temporary dir "/tmp"` を出す（登録と導入は通る）
- 取れたことの裏付けは clone の checkout で見る。`develop` の ref で登録すると
  `git -C "$ISO/claude/plugins/marketplaces/ai-plugins" config --get remote.origin.fetch` は
  `+refs/heads/develop:refs/remotes/origin/develop` を返す（正式版の登録は `main` のまま）

## 利用者が過去の版へ戻る

**取り消しは利用者の側の操作になる。** こちらから前の版へ戻す手段は無い。取得元をタグへ固定
するか、別名のマーケットプレイスで対象だけを固定する。手順は
この章の次の 2 節にある。**配布の完了報告へ
書く「取り消しの手段」は、この 2 つとその限界を指す。**

**版数を書き換えても、過去の版のコードには戻りません。** `plugin.json` の `version` は
更新の判定に使う識別子で、どのコードを取るかは**取得元の git ref** が決めます。
`claude plugin install` に版を指定する手段はありません。

### 取得元ごとリリースタグへ固定する

マーケットプレイスの項目は `./plugins/ndf` のような相対パスで実体を指すため、リポジトリを
過去のタグへ固定すれば、その時点のプラグインが入ります。

```bash
claude plugin marketplace add devbasex/ai-plugins@<タグ>
```

**最初のタグは `ndf--v9.5.0` です**（手元のタグは `git tag -l` で確かめられます）。
それより前の版（9.4.0 以前）はタグを打っていないため、**タグでは戻せません**。戻すなら、
その版のコミットを調べて ref に指定します。

**同じ取得元の他のプラグインも同時に過去の状態になります。** `playwright-kit` や `mcp-*` を
最新のまま使いたい場合は次の方法を採ります。

### 対象のプラグインだけを固定する

別名のマーケットプレイスを 1 つ用意し、`git-subdir` で対象のディレクトリと ref を直接指します。

```json
{
  "name": "ai-plugins-pinned",
  "owner": {"name": "takemi-ohama"},
  "plugins": [
    {"name": "ndf",
     "source": {"source": "git-subdir",
                "url": "https://github.com/devbasex/ai-plugins.git",
                "path": "plugins/ndf",
                "ref": "<タグ>"}}
  ]
}
```

`ref` はブランチまたはタグ、`sha` は 40 文字のコミット。両方あるときは `sha` が効きます。
この形が `claude plugin validate` を通ることは確認済みです。

この定義を読み込ませて導入します。**JSON ファイルのパスを直接渡せます**（実機で確認）。

```bash
claude plugin marketplace add <この JSON のパス> --scope local
claude plugin install ndf@ai-plugins-pinned
```

**`--scope local` を付けます。** 固定は一時的な操作なので、利用者全体の設定へ残しません。
戻すときは `claude plugin marketplace remove ai-plugins-pinned` です。名前が `ai-plugins` と
違うため、通常の取得元は消えません。

**固定した版と最新版を同時に有効にしないでください。** どちらの `/ndf:*` が使われるかが
定まりません。切り替えるときは、先に一方を無効にします。

**名前は必ず変えます。** `ai-plugins` のまま追加すると、利用者の取得元が置き換わります
（「ランタイムごとの取得と導入」）。
