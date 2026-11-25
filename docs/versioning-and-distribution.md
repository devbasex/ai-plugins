# 版とリリース

NDF プラグインの版数の付け方と、正式版のリリースの手順・実測・一覧を持つ。**この主題の正本である。**
判断の基準（何を選ぶか）は [AGENTS.md](../AGENTS.md) の「版とリリースの方針」にあり、この文書は
どう動くかとどう確かめるかを扱う。リリース済み版ごとの判断は確定仕様（[specifications/](specifications/)）にある（v10.17.51 までは [archive/version-decisions/](archive/version-decisions/)）。

## チャネルと ref

**チャネルは正式版の 1 つで、`main`（既定ブランチ）に置く。** 開発は `main` だけで行い（ブランチ戦略 `trunk`。
宣言は `.ndf/project.json` の `branch_strategy`）、スプリントの Pull Request は `main` から切って `main` へ戻す。
正式版は、`main` から切った版上げの Pull Request（`release/v<版>`）のマージで出る。

| チャネル | ref | 何が載るか | 誰が登録するか |
| --- | --- | --- | --- |
| 正式版 | `main`（既定ブランチ） | 版上げの Pull Request で版数を上げた正式版と、その後にマージしたスプリントの変更 | 利用者 |

**10.27.75 までは `develop` の開発版チャネルを持っていた。** やめたのは、長く残る 2 本のブランチの間で
版数と検査の範囲を運ぶ事故を無くすためである（#3672）。入れていた利用者の戻し方は「開発版から正式版へ戻す」にある。

**`main` に載ったスプリントの変更が届く時点はランタイムで違う。** Claude Code は版数でキャッシュするため、
既存の利用者へ届くのは版上げの Pull Request で版数が変わった時点である。新しく入れる利用者と入れ直す利用者は、
`main` の先端を前の版数の表示のまま受け取る。Codex は起動時のマーケットプレイスの自動更新で、`main` が進むたびに
版数を比べずに入れ直すため、スプリントの変更は版上げを待たずに届く。スプリントの Pull Request は
設計承認ゲート・cross-review・CI を通ってから入る。

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

## 版の付け方と開発版のデプロイ

**セマンティックバージョニング**:
- **MAJOR**: 破壊的変更
- **MINOR**: 後方互換性のある新機能
- **PATCH**: バグフィックス

**接尾辞は人が読むための目印である。** Claude Code の直接インストール経路は版数を
**キャッシュキーとしての文字列一致**でしか見ず、`-dev` や `-rc` を prerelease として
扱わない。Codex も同様で、Agent Plugins Specification には解釈の規定が無い。
semver の順序で除外されるのは、プラグイン間の依存解決（`dependencies`）の経路だけである。
それでも接尾辞を付けるのは、**入れたくない利用者が版数を見て判断できるようにする**ためである。

**接尾辞には、チャネルを分けるうえでの役割もある。** 公式ドキュメントは release channel の
注意として、**2 つのチャネルが同じ版数へ解決されると更新が飛ばされる**と書いている。
開発版チャネルの版数へ接尾辞を付けておけば、正式版の版数と必ず異なる。

**ai-plugins は 10.27.75 の後、開発版を出さない**（チャネルが正式版の 1 つになったため）。下の表と規則は、
NDF が開発版チャネルを持つリポジトリで使う版の形として残す。リリース前の直しを使いたいときは、
スプリントの終わりを待たずに直しを含む正式版の PATCH を版上げの Pull Request で出す。

| 版 | 形 | 意味 |
| --- | --- | --- |
| 正式版 | `11.0.37` | 利用者が常用してよい |
| 開発版 | `11.1.0-dev.1` | 検証中。入れたくない利用者は取得を控えられる |
| 公開前の確認版 | `11.1.0-rc.1` | 正式版の候補。残るのは確認だけ |

- 接尾辞は**次に出す正式版の版数へ付ける**。`11.0.37` の次を開発するなら `11.1.0-dev.1`
- 連番は開発版を出すたびに増やす。**同じ版数で中身を差し替えない**。差し替えると、利用者の
  手元にある版と `main` の版が同じ番号で別物になり、何を確かめたのかが分からなくなる
- **正式版を出すときは接尾辞を外す。** `11.1.0-dev.3` の次は `11.1.0`
- 順序は semver に従い `11.1.0-dev.1` < `11.1.0-rc.1` < `11.1.0` になる

## 11.0.0 の入れ替え

**11.0.0 は、作り直した NDF を `plugins/ndf/` へ入れ替えた MAJOR の版である**（#1142・#3304）。プラグイン名 `ndf`・
取得元 `ai-plugins`・置き場 `plugins/ndf/`・チャネル（正式版の 1 つ、`main`）は変わらない。変わるのは中身で、
10 系の `scripts/` は無くなり、手順は 1 つの入口 `plugins/ndf/bin/ndf <副命令>` から打つ。

| 項目 | 11.0.0 での扱い |
| --- | --- |
| 版数 | `plugins/ndf/.claude-plugin/plugin.json` と `.codex-plugin/plugin.json` の `version` が `11.0.0`（版上げの PR と入れ替えの PR は同じ 1 本） |
| 出し方 | `release-cd.yml` の 1 本が、版上げの PR の承認の証跡の確かめ（commit status `ndf/release-approval`）・merge queue への投入・タグ `ndf--v11.0.0` と GitHub Release を持つ。`release-approval.yml`・`ndf-next*.yml` は無い |
| 利用者の更新 | 同じ設定ディレクトリを使うコンテナは同じ日にまとめて上げる。手順（U1〜U5）は `CHANGELOG.md` の 11.0.0 の節にある |
| 10 系へ戻す | 10 系の中身（`ndf--v10.27.77` と同じ）を `11.0.1` として出す。版数を下げると取得元が中身を受け取らないため、戻す版も PATCH で上げる（#3341 の決定 10） |
| 10 系のタグに留まる | 下の「利用者が過去の版へ戻る」の手順で `ndf--v10.27.77` へ固定する |
| 11.x へ送ったもの | 保存期間の保持と待ちの通知は 11.0.0 で動かない。10 系のタグのスクリプトを打っていた CI の検査 4 本は、用語集の検査を 11 系の `ndf records glossary` へ移し、残りの 3 つ（設計 PR の本文の突き合わせ・指示書の検査・Issue の記載項目のテンプレート）はやめた |

## ランタイムごとの取得と導入

**導入先のランタイムは Claude Code と Codex の 2 つである。** Kiro CLI と agy へは配らず（#2231。最後に配ったのは
`10.27.4`）、cross-review / cross-refactoring の相手役として CLI を起動するだけである。前の版で入れた手元の扱いは
[plugins/ndf/README.md の「agy・Kiro CLI へのデプロイの終了」](../plugins/ndf/README.md#agykiro-cli-へのデプロイの終了)にある。

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
claude --plugin-dir plugins/ndf                                             # Claude Code
```

## 開発版から正式版へ戻す

**`#develop` / `--ref develop` で登録していた利用者は、`main` へ登録し直す。** `develop` は 10.27.75 の後は
更新されないため、登録したままでは新しい版が届かない。登録を切り替えたら、続けて導入の操作を実行する。

```bash
claude plugin marketplace add https://github.com/devbasex/ai-plugins
claude plugin update ndf@ai-plugins    # 未導入なら claude plugin install ndf@ai-plugins

# Codex は同名の取得元の上書きを拒むため、先に外す
codex plugin marketplace remove ai-plugins
codex plugin marketplace add devbasex/ai-plugins
codex plugin add ndf@ai-plugins
```

`claude plugin update` は「restart required to apply」と表示する。実体を入れ替えるだけで、
動いているセッションには反映されない。

ref の書き方は `owner/repo@ref` と `git-url#ref` の 2 つで、
[公式ドキュメント](https://code.claude.com/docs/en/plugin-marketplaces)に記載がある。
`claude plugin marketplace add --help` には出ないが実機で動く。Codex は `--ref` を持ち、
`owner/repo@ref` も受け取る。`claude plugin install` に版を指定する手段は無い（これは確定）。

**承認の前に版上げの Pull Request の中身を試すときは、その head（`release/v<版>`）から隔離した設定ディレクトリへ入れる。**
`release-verification-steps.py verify-install --ref release/v<版>` が行い、手順は「取得元の登録を確かめる」にある。

**サードパーティのマーケットプレイスは自動更新が既定で無効である。** `main` へ出した版が
即座に全利用者へ届くわけではなく、利用者が `marketplace update` を実行した時点で届く。

## 正式版を出す

**正式版のたびに、`main` から `release/v<版>` を切って版上げ・変更履歴・版数の記載・トークン消費の記録のコミットを積み、
`main` 宛の Pull Request（版上げの PR）を作る。** 組み立ては `release-cd.yml` の `assemble`（`ndf release-steps assemble`）が行い、
版数の記載とトークン消費の記録は `.ndf/release.json` の手順が書く。リリース承認はこの PR の HEAD に対して**マージの前に**取る。
`main` へ直接コミットしない。スプリントの Pull Request も `main` から切って `main` へ出す。

**リリース承認ゲートは版上げの PR での 1 回である。** `main` の ruleset はレビューの承認を求めない（承認の数 0）。
承認資料と承認の証跡（承認したコミットの 40 桁）を版上げの PR へコメントで置き、誰がいつ承認したかは、
GitHub が付けた証跡のコメントの作成者と作成時刻で読む。`release-cd.yml` の `approval-check` が証跡を確かめて
PR の HEAD へ必須チェック `ndf/release-approval` を付け、証跡の無い PR・証跡と HEAD の違う PR・書き込みの権限の
無い人の証跡だけの PR は `main` へマージできない。

**マージは NDF が merge queue へ入れて行い、管理者の迂回（`--admin`）を使わない。** マージの方式は merge commit である。
承認の後に `release/v<版>` へコミットが足されれば HEAD が動き、承認を取り直すまでマージされない。

**`main` へ直接 push しない。** `main` を守る ruleset は Pull Request のマージだけを通す。必須のチェックの一覧は
`gh api repos/<owner>/<repo>/rules/branches/main` で読み、ワークフローのジョブとの食い違いは
突き合わせのチェック（`required-checks-check`、`scripts/check-required-checks.py`）が Pull Request ごとに知らせる。

```bash
gh workflow run release-cd.yml --ref main -f step=assemble -f level=patch       # 版上げの PR を組み立てる
plugins/ndf/bin/ndf release-steps present --root . --pr <番号>                  # 承認資料を PR へ置く
plugins/ndf/bin/ndf release-steps answer --root . --pr <番号> --answer approved --presented <HEAD> --by user   # 承認を得てから
```

証跡のコメントで `approval-check` と `merge` が動き、HEAD と承認が一致すれば merge queue へ入れる。承認の判定を
打ち直したとき（PR の本文の編集など、コメント以外で動いたとき）は、`merge` を
`gh workflow run release-cd.yml --ref main -f step=merge -f pr=<番号>` で起動する。

**`release-cd.yml` は利用者の token（秘密 `NDF_RELEASE_TOKEN`）を使う。** fine-grained の personal access token で、
対象のリポジトリへ次の権限を持たせる。足りないと、組み立て・証跡の確かめ・マージのどれかが 403 で止まる。

| 権限 | 使う所 |
| --- | --- |
| Contents: Read and write | 版上げのブランチの push・マージ |
| Pull requests: Read and write | 版上げの PR の作成・コメント・merge queue への投入 |
| Commit statuses: Read and write | `approval-check` が `ndf/release-approval` を付ける |
| Metadata: Read-only | 証跡の書き手の権限を読む（必須の権限） |

**Pull Request の宛先は `main` である。** 既定ブランチが `main` のため、`gh pr create` は指定しなくても `main` を宛先にする。

**リリースの Pull Request では、重いチェック（`pytest` と `runtime-smoke (*)`）を省く。** 同じ中身を
2 度試さないためである。判定は各 workflow の `ci-scope` のジョブ（チェックの名前は `pytest-scope`・
`runtime-smoke-scope`）が `scripts/ci-heavy-skip.py` で行い、次のどちらかのときだけ省く。

- 差分が版数と説明だけ（版上げの `release/v<版>` → `main`）。説明は `CHANGELOG.md`・
  `README.md`・`AGENTS.md`・`plugins/**/README.md`・この文書・`docs/metrics/ndf-token-usage/`
  の記録で、`plugin.json` と `marketplace.json` は `version` / `description` の行だけ
- 宛先のブランチへの push でその workflow を通ったコミットがあり、head がそこから版数と説明しか
  変えていない

コードやテストを 1 行でも変えた差分では省かない。`main` への push では常に回す。
省いても必須のチェックの名前は変わらず、結果は成功で返る。`pytest` はまとめのジョブが、
`runtime-smoke (*)` はステップだけを飛ばしたジョブが返す。版数の整合のチェック
（`runtime-plugin-validate` など）は常に回す。

**版上げの PR のマージが `release` の「本番リリース」である。** そちらには承認が要る。スプリントの Pull Request の
`main` へのマージは承認なしで進めてよい（`/ndf:release`）。

**正式版のリリースの Pull Request には、版ごとのトークン消費の記録が載る。** `release` の手順 3 で
`.ndf/release.json` のリリースコマンド（`scripts/token-usage-snapshot.py --released <版>`）が走り、
`docs/metrics/ndf-token-usage/<集計日>.md` / `.json` を書く。前の行との比が ±30% を超えた版の
読み取りだけを、[手引き](metrics/ndf-token-usage/README.md) に従って書き足す。

**正式版のリリースタグと GitHub Release は CD が作る。** タグは利用者が過去の版へ戻るときの目印になる。
`main` へのマージで `.github/workflows/release-cd.yml` が `ndf release-steps publish --revision <マージのコミット>` を走らせ、
そのコミットの `plugin.json` の版で `ndf--v<版>` のタグと、`CHANGELOG.md` の版の節を本文にした GitHub Release の
うち足りないものを作る。版に接尾辞（開発版）があれば何も作らない。宣言は `.ndf/project.json` の `branch_strategy.release` にある。
**公開したタグの更新と削除は ruleset で禁じる。**

**手元からタグを打たない。** 同じ名前のタグが別のコミットを指して origin にあると、CD の publish は
「origin のタグが別のコミットを指す」で止まり、直すにはタグの付け替え（強制 push）が要る。
手で動かすのは CD が失敗したときと CD を外したときだけで、`main` のコミットを指定して同じ処理を呼ぶ。

```bash
gh workflow run release-cd.yml --ref main -f step=publish
```

## 版数を持つ箇所

**版数を持つ箇所は 2 種類ある。** チェックが突き合わせる箇所と、**チェックに載らず手で直す箇所**である。

揃っていないと `scripts/check-doc-staleness.py` と `scripts/validate-runtime-plugins.sh` が
落ちる。**記載を消してもチェックは通らない。** 位置を決める語が見つからなければ、読み取れない
こととして落ちる。

**定義ファイルが 5 箇所である。** 2 つの `plugin.json` はいずれも
`version` と `description` の両方に版数を持つため、片方だけ直すとチェックで止まる。

| 箇所 | 何を書くか |
| --- | --- |
| `plugins/ndf/.claude-plugin/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/.claude-plugin/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `plugins/ndf/.codex-plugin/plugin.json` の `version` | 版数そのもの |
| `plugins/ndf/.codex-plugin/plugin.json` の `description` | `(vX.Y.Z)` の形で版数 |
| `.claude-plugin/marketplace.json` の該当プラグインの `description` | 同上 |

**plugin の README は版ごとの更新の節（`## vX.Y.Z へ更新するとき`）を持たない。** README は現行版の説明と、
CHANGELOG.md を参照する固定の「`## 更新情報`」の節だけを持ち、版ごとの変更と移行の手順は CHANGELOG.md の版の節に
置く。リリースの手順（`ndf release-steps assemble`）は README の版数だけをそろえ、ほかの記述に触れない。節が戻ると
`scripts/check-doc-staleness.py` が落とす（`plugins/*/README.md`・`plugins/mcp/*/README.md`）。

**説明文書の本文が 5 箇所である。** いずれも利用者が読む入口にあり、古いまま残ると現行版を
誤って伝えるか、書かれたとおりに実行できない。

**指示書（`AGENTS.md` / `CLAUDE.md`）には版数を書かない。** 版上げの `bump` が版数を書き換えると、
Pull Request で上限に収まった指示書の読み込み量が版上げの時点で伸び、指示書のチェックで止まるためである。

| 箇所 | 何を書くか | 突き合わせ先 |
| --- | --- | --- |
| `README.md` の概要 | `**NDFプラグイン vX.Y.Z**` | `ndf` の `plugin.json` |
| `README.md` のプラグイン一覧表 | 版数の列 | 行ごとに `plugins/<名前>/.claude-plugin/plugin.json` |
| `docs/versioning-and-distribution.md` の「版の付け方と開発版のデプロイ」章 | 版数の例 | `ndf` の `plugin.json`（基底の比較） |
| `plugins/ndf/README.md` の Codex のパス例 | `~/.codex/plugins/cache/.../ndf/X.Y.Z/`（2 箇所） | `ndf` の `plugin.json` |
| `plugins/ndf/README.md` の `codex plugin list` の出力例 | 期待出力の版数 | `ndf` の `plugin.json` |

**「版の付け方と開発版のデプロイ」章だけは扱いが違う。** この章には現行版そのもの・接尾辞を
付けたもの・次に出す版を指すものが混ざるため、1 つの値とは照合しない。章に並ぶ版数の
**基底**（接尾辞を除いた数字 3 つ）が現行版の基底より小さければ落ちる。次の版を指す例は通る。

**この章の版数は囲みを付けて書く。** チェックが拾うのは `` `9.6.0` `` のように囲まれた版数だけ
である。章にはリリースに使う CLI の名前と版数を並べて書くことがあり、`codex-cli 0.146.1` のような
他のソフトの版数まで現行版と比べると誤検出になる。囲まずに書いた版数は走査に入らない。

**章の中の次に出す版を指す例は、同じ章の例どうしで比べる。** 版数を一括で置換すると、次に出す版を
指すはずの例が現行版を指す形へ崩れる（v10.10.0 は、開発版の行が正式版の行と同じ値のままリリースされた。#566）。
崩れた値は現行版そのものと等しいことがあり、基底の規則では拾えない。そこで次の 2 つを、周囲の語で
位置を決めて拾う。

| 位置を決める語 | 求める形 |
| --- | --- |
| 版の形の表の 1 列目の `正式版` / `開発版` / `公開前の確認版` | 3 つの行が 1 つずつある。正式版の行は接尾辞を持たず、開発版の行は `-dev.<連番>`、公開前の確認版の行は `-rc.<連番>` で終わる。開発版と公開前の確認版の行の基底は、正式版の行の基底より大きい |
| 「の次を開発するなら」 | 1 つ以上ある。右側の基底が左側の基底より大きく、右側が `-dev.<連番>` で終わる |

**この 2 つは現行版と比べない。** 開発版チャネルを持つリポジトリの版は接尾辞付きになりうるため、現行版との一致を求めると
開発版を出すたびに正しい例が落ちる。正式版の行を現行版へ結び付けるのは、上の基底の規則である。

**位置を決める語は言い換えられない。** 言い換えると読み取れないこととして落ちる。1 列目に同じ語を持つ
表を章へ足すと、同じ行が複数あるとして落ちる。コードの囲みの中の表と文は、実行例か出力例として数えない。

**版を決めるのは `plugins/ndf/.claude-plugin/plugin.json` の `version` だけである。** 他の
箇所は読み手向けの記載とチェックのための突き合わせ先で、取得する版を変えない。
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。

**版を持つのは `plugins/<名前>/.claude-plugin/plugin.json` だけである。**
`.claude-plugin/marketplace.json` に `version` フィールドは置かない。マーケットプレイス側の
`description` に書かれた `(vX.Y.Z)` は読み手向けの記載で、取得する版を決める値ではない。
両方に版を持たせると `plugin.json` が無警告で優先され、食い違いに気づけなくなる。

`scripts/validate-runtime-plugins.sh` が突き合わせるのは、説明文書に書かれた Skill の数と、
**版数を書いた箇所**です。定義ファイルが 5 箇所、説明文書の本文が
5 箇所あります（`README.md` の概要とプラグイン一覧表、正本に 1 箇所、
`plugins/ndf/README.md` の Codex の確認例 2 種類）。**記載を消してもチェックは通りません。**
一覧は上の 2 つの表にあります。

## チェックに載らず手で直す箇所

**チェックが見るのは版数そのものの一致までで、記載の中身までは見ない。** 次の 2 つは版を
上げるたびに人が読み直す。

| 箇所 | 何を確かめるか |
| --- | --- |
| 接尾辞の付け忘れ・外し忘れ | 接尾辞の付いた版でもチェックは通る（形式としては妥当な版数のため）。出す前に版数を読み直す |
| `CHANGELOG.md` の版の節 | その版で何が変わったかを書く。版数と日付は機械で確かめられるが、内容は書かないと残らない |

**バージョン更新時の手順**:
1. `plugin.json`のバージョンをインクリメント
2. 変更内容をドキュメント化
3. `CHANGELOG.md` の先頭へその版の節を足す。書式は Keep a Changelog に従い、変更点を
   `追加` / `変更` / `修正` / `削除` へ分類して 1〜2 行で書く。判断の理由は
   確定仕様（`docs/specifications/`）が持つ
4. Skill の数が増減した場合は、`README.md` と `plugins/ndf/README.md` に書かれた数を書き直す
5. `python3 scripts/check-doc-staleness.py --root .` を実行し、説明文書に残った古い版数を
   出力の行番号のとおりに直す
6. `python3 plugins/ndf/scripts/instructions-check.py --root .` を実行し、リリース済み版の段落が
   指示書に残っていないかを見る。落ちた段落はその主題の確定仕様（`docs/specifications/`）へ移す
7. 破壊的変更がある場合は明示
8. テストを実行
9. **正式版として `main` を進めた後、CD がリリースタグと GitHub Release を作ったことを確かめる**（手で打たない。上の「正式版を出す」）

**すべての版数を機械的に置換しない。** 履歴（`docs/archive/version-decisions/`、確定仕様、`CLAUDE.md` の
現行版の段落、`docs/development-history/`）、記録（`issues/`）、意図的に前の版を指す文（取り消しの説明）は
そのまま残す。現行版を指しているかは文脈で決まる。**チェックが突き合わせるのは、周囲の固定の語で
位置を決めた記載だけである。** 履歴と記録は最初から走査に入らない。

**説明文書の本文に書かれた版数は取りこぼしやすい。** 上の本文の 5 箇所を
チェックの対象へ入れ、この取りこぼしを機械が拾う。

## 取得元の登録を確かめる

**取得元の登録そのものを確かめるときは、設定ディレクトリを隔離する。** 「ランタイムごとの取得と導入」の手元での確認（`claude --plugin-dir`）はリリース対象を
直接読み込む確認で、取得元の登録から導入までの経路は通らない。版上げの PR の head を承認の前に試す手順は
その経路そのものであるため、別の設定ディレクトリを与えて確かめる。**利用者の登録には触れない**
（claude 2.1.261 / codex-cli 0.153.2 で実測）。

```bash
ISO=$(mktemp -d "$HOME/.ndf-channel-check.XXXXXX"); mkdir -p "$ISO/claude" "$ISO/codex"

CLAUDE_CONFIG_DIR="$ISO/claude" claude plugin marketplace add \
  'https://github.com/devbasex/ai-plugins.git#release/v<版>'
CLAUDE_CONFIG_DIR="$ISO/claude" claude plugin install ndf@ai-plugins
CLAUDE_CONFIG_DIR="$ISO/claude" claude plugin list          # 版数を見る

CODEX_HOME="$ISO/codex" codex plugin marketplace add devbasex/ai-plugins --ref release/v<版>
CODEX_HOME="$ISO/codex" codex plugin add ndf@ai-plugins
CODEX_HOME="$ISO/codex" codex plugin list                   # 版数を見る

rm -rf "$ISO"
```

- **`CODEX_HOME` は先に作っておく。** 存在しないパスを渡すと
  `CODEX_HOME points to "..." , but that path does not exist` で落ちる
- **隔離先は `$HOME` の下に置く。** `/tmp` の下に置くと Codex が
  `Refusing to create helper binaries under temporary dir "/tmp"` を出す（登録と導入は通る）
- 取れたことの裏付けは clone の checkout で見る。`release/v<版>` の ref で登録すると
  `git -C "$ISO/claude/plugins/marketplaces/ai-plugins" config --get remote.origin.fetch` は
  登録した ref を指す refspec を返す（`develop` で実測した値は `+refs/heads/develop:refs/remotes/origin/develop`。正式版の登録は `main` のまま）
- NDF の工程では `release-verification-steps.py verify-install --ref <ref>` が同じ手順を行う

## 利用者が過去の版へ戻る

**取り消しは利用者の側の操作になる。** こちらから前の版へ戻す手段は無い。取得元をタグへ固定
するか、別名のマーケットプレイスで対象だけを固定する。手順は
この章の次の 2 節にある。**リリースの完了報告へ
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
