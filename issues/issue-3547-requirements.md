# #3547: #1142 S1d: google-workspace のスクリプト（gws-check・slides-thumbnails）を ndf の副命令として作り直す

正は課題の本文（#3547）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 目的

新しい NDF（`plugins/ndf-next/`）と今の `ndf` を入れ替える（入れ替え S2 #3304）前に、google-workspace の Skill が呼ぶ 2 つの機能を、新しいエントリポイント `bin/ndf` のサブコマンドとして動かし、ndf-next だけで通したことを試走（T2 #3411）の記録に残す。

- 2 つの機能: gws（Google Workspace CLI）の有無と認証の状態の確認（今の `gws-check.py`。移行先は `ndf gws check`）と、Google Slides の全ページのサムネイルの保存（今の `slides-thumbnails.py`。移行先は `ndf gws thumbnails`）
- 達成したい状態: ndf-next の Skill の本文が今のスクリプトのパスを 1 つも参照せず、2 つのサブコマンドだけで流れ（確かめる → 同意のうえ入れる → 認証を案内する → 操作する）を通せる。機能の対応表（`scripts/ndf-next-parity/parity/features.json`）の試走の判定で、2 つの機能の行と document-systems の Skill の行が通る（`passed`）

## 今の状態（2026-11-16 に確かめた）

この課題は 2026-11-15 にスプリント lr-s1d で一度通っている（設計 PR #3574・実装 PR #3577・確定仕様化 PR #3582。いずれもマージ済み）。2 つのサブコマンド・テスト・google-workspace の Skill の本文の書き換えは develop にある。**既にできているものは作り直さない。** この本文の受け入れ条件は、済んだものを `[x]` と根拠で示し、残っている差だけを `[ ]` で示す。

試走 T2 の 2 回目の記録（`scripts/ndf-next-parity/parity/trial/2026-11-16.json`）と、その記録から書いた機能の対応表の判定は次のとおりである。

| 機能の対応表の行 | 判定 | 原因 |
| --- | --- | --- |
| `entrypoint:skills/google-workspace/scripts/gws-check.py` | `not_run` | 試走の記録の `passed` にこの行が無い（ndf-next だけで通したことを記録していない） |
| `entrypoint:skills/google-workspace/scripts/slides-thumbnails.py` | `failed` | `plugins/ndf-next/skills/document-systems/references/system-gdrive.md` の 72 行目が旧い `slides-thumbnails.py` を名前で呼ぶ |
| `skill:document-systems` | `failed` | 同じ 72 行目 |

確かめた結果（`origin/develop` 0be66cb77 の上）:

| 確かめたこと | コマンド | 結果 |
| --- | --- | --- |
| 試走の判定の再現 | `python -m parity.trial.probe` と同じ関数 `judge` を、今の本文で呼ぶ | 上の表のとおり（`not_run`・`failed`・`failed`） |
| 72 行目を直したときの判定 | 72 行目の呼び出しを `ndf gws thumbnails` へ置き換えた一時的なコピーを `--plugin-root` に渡して `judge` を呼ぶ | 3 行とも `not_run`。試走の記録の `passed` に 3 行を足すと 3 行とも `passed` |
| ndf-next の本文の旧い呼び出し | `grep -rln 'slides-thumbnails\|gws-check' plugins/ndf-next` | 4 ファイル。旧いスクリプトを呼ぶのは `system-gdrive.md` だけ。ほかの 3 ファイル（`docs/contracts/commands.md`・`src/ndf/domains/records/cli.py`・`contracts/schemas/io/records.args.schema.json`）は別のサブコマンド `ndf records gws-check` の名前で、試走の判定は `.py` 付きの名前だけを数える |
| 実の環境での確認のサブコマンド | `plugins/ndf-next/bin/ndf gws check` | `gate`・終了コード 11・`metrics.state` が `unauthenticated`（`credential_source` が `client_secret.json`、`auth_method` が `oauth2`） |
| 実の環境の gws の認証 | `gws drive files list`（Slides のファイルを 1 件だけ読む） | 1 件を返した。gws は認証済みで動く。`gws auth status` は `has_refresh_token`・`token_valid` が真 |
| 旧い許可ファイルが使われているか | `python3 scripts/check-ndf-contracts.py` | `ok`。例外リストで許した件数 256 件が `scripts/contract-allow/` のファイル 256 件と一致し、使われていない例外は 0 件 |

**実の環境の確認で、判定の不具合が 1 件見つかった。** 前の回の受け入れ条件 1・2 は「`credential_source` が `client_secret.json` なら未認証」とした。この形は #2067 の再現（`auth_method` が `none`）だけでなく、OAuth でログインした状態（`auth_method` が `oauth2`）も未認証にする。gws は認証済みで動くのに `ndf gws check` が終了コード 11 を返すため、利用者は要らない `gws auth login` を頼まれ、試走でも確認のサブコマンドが `authenticated` を返せない。受け入れ条件 1・2 を書き換え（下の「受け入れ条件」に取り消し線で残す）、前提 7 を改める。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめる（MVV 判定が承認ゲートを省いたときはその判定が確かめた記録になる）まで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

この課題の本文は、題・背景・やること・受け入れ条件・進め方の決まりのすべてを AI（レーン lrebuild の conductor と、並列の計画 p6 のレーン lr-s1d のオーガナイザー）が書いた。S12・S13 は、やり直しの指摘（2026-11-16）の経緯の欄の条件を拾ったものである。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 2 本を `bin/ndf` のサブコマンドとして作り直す（グループは設計で決める。候補は新しいグループ `ndf gws`） | 本文「やること」（conductor）・「進め方の決まり」（オーガナイザー） | 受け入れ条件 1〜11。名前は `ndf gws check`・`ndf gws thumbnails` に決まり、develop にある |
| S2 | 結果は新しいコントラクトに合わせる（承認ゲートの結果は `routes` が必須・ルートの `argv` の先頭は `ndf`） | 本文「背景」「進め方の決まり」 | 受け入れ条件 4・10・12 |
| S3 | 確認は今の `gws-check.py` と同じ判定で返す | 本文「受け入れ条件」1 行目 | 受け入れ条件 1〜7。既知の不具合 #2067 は引き継がない（前提 7。利用者の変更要求 2026-11-15）。OAuth でログインした状態の判定は今の `gws-check.py` と同じ（認証済み）に戻す（前提 7） |
| S4 | サムネイルの取得は今の `slides-thumbnails.py` と同じ出力を返す | 本文「受け入れ条件」2 行目 | 受け入れ条件 8〜11。結果 JSON の外枠は新しいコントラクトに従う（前提 2） |
| S5 | 新しいサブコマンドの引数と結果のスキーマを lspec（#3518・#3519）の仕組みに載せる | 本文「進め方の決まり」 | 受け入れ条件 12・13 |
| S6 | 今の `plugins/ndf/` は変えない | 本文「対象範囲」「進め方の決まり」 | 受け入れ条件 16・含まない |
| S7 | 後方互換は意識しない | 本文「進め方の決まり」 | 前提 3 |
| S8 | 時間で区切った負荷試験は書かない | 本文「進め方の決まり」 | 含まない |
| S9 | 触る場所は `src/ndf/domains/gws/`・`tests/domains/gws/`・`skills/google-workspace/`・機能の対応表 | 本文「進め方の決まり」 | 前提とする取り決めのプロジェクト構造。やり直しでは `skills/document-systems/` と試走の記録・確定仕様を足す（「対象範囲」） |
| S10 | 対象範囲に共通部品 `gws_common.py` を含めるが、サブコマンドにするのは 2 本だけ | 本文「対象範囲」と「やること」の食い違い | 前提 4 |
| S11 | 入れ替え S2 #3304 の前に閉じる。試走 T2 #3411 と同時に進む | 本文「先に要るもの」「進め方の決まり」 | 前提 5 |
| S12 | 機能の対応表の 2 行は、`now` が旧いスクリプトのパス・`to` が `S1d` のままであることが未達の原因である | やり直しの指摘「経緯」 | 採らない。`now` は旧い機能の場所で、試走の判定が旧い呼び出しを見つける手がかりに使う。`to` は移行先の子で、その保存先（`src/ndf/domains/gws/`）がある。どちらも変えない。通った行の前例 `entrypoint:scripts/project-decl.py` も `now` と `to`（`S1e`）を変えずに `passed` になっている。未達の原因は上の「今の状態」の表のとおり（受け入れ条件 15・19〜21） |
| S13 | 旧い側の許可ファイル `scripts/contract-allow/plugins__ndf__skills__google-workspace__scripts__gws-check.py--core-entry.json` は残っていてはいけない | やり直しの指摘「経緯」 | 採らない。残す。この許可は今の `plugins/ndf/` のスクリプト（変えない）の外れを許すもので、消すと `check-ndf-contracts.py` が `core-entry` の違反を返す。消すのは今の `plugins/ndf/` を外す入れ替え S2 #3304（受け入れ条件 23） |

## 前提

- 前提 1: 今の 2 本の振る舞いは、スクリプトの冒頭の説明と、今のテスト（`plugins/ndf/skills/google-workspace/tests/` の 18 件）の名前が示すものを正とする
- 前提 2: 「同じ判定」「同じ出力」は、状態の名前（`metrics.state`）・終了コード・`metrics` と `items` の中身・保存するファイルが同じであることを指す。結果 JSON の外枠（`status` と `cause`・`routes`・`next` の形）は新しいコントラクト（`plugins/ndf-next/contracts/schemas/result.schema.json`）に従う。利用者への案内（`gws auth login` の打ち方、OAuth クライアントが無いときの `gws auth setup` の案内）は、Skill の本文の手順と `summary` が持つ
- 前提 3: 今のスクリプトのパスで呼ぶ互換の経路を、新しい NDF に持たない。新しい NDF はまだ利用者へ配っていないため、壊れる呼び出し元は無い
- 前提 4: 共通部品 `gws_common.py` の 2 つの処理はサブコマンドにしない。stopped の結果は新しい NDF の結果の部品（`ndf.contracts.result`）で組み、出力の末尾の切り出しは `ndf gws` の中の 1 か所に置く（develop にある）
- 前提 5: この課題は入れ替え S2 #3304 より先に閉じる
- 前提 6: gws の呼び出しと導入の上限の秒数は、新しい NDF にある上限の定義（`plugins/ndf-next/contracts/limits.json` の `google-workspace.call`・`google-workspace.install`・`google-workspace.thumbnails`）を使い、値を変えない
- 前提 7: 認証済みかの判定は、既知の不具合 #2067（クライアントの設定だけがありトークンが無い状態を認証済みと判定する）を引き継がない（利用者の変更要求 2026-11-15。#3395 の決定 127「同等だが既知の不具合を引き継がない」）。トークンを環境変数 `GOOGLE_WORKSPACE_CLI_TOKEN` で渡していないとき、未認証は次の 2 つの形だけである。それ以外は認証済みとする
  - `credential_source` が `none`
  - `credential_source` が `client_secret.json` で、`auth_method` が `none`（#2067 の再現の形。gws 0.22.5 の実測）

  `credential_source` が `client_secret.json` で `auth_method` が `oauth2` の状態は、OAuth でログインした状態で、gws は認証済みで動く（2026-11-16 に gws 0.22.5 で実測。「今の状態」）。読むキーは `gws auth status` の 3 つ（`credential_source`・`auth_method`・`client_config_exists`）のままで、`has_refresh_token` などほかのキーは読まない。判定は記録の部品（`plugins/ndf-next/src/ndf/domains/records/gws.py` の `authenticated`）の 1 か所だけにあり、2 つのサブコマンドと `ndf records gws-check` がそれを呼ぶ
- 前提 8: 試走の判定（`passed`・`failed`・`not_run`）は、試走の判定のコマンド（`scripts/ndf-next-parity` で `python -m parity.trial.probe --write`）だけが機能の対応表の `trial` の列へ書く。手で書き換えない。`passed` は、試走の記録（`scripts/ndf-next-parity/parity/trial/<日付>.json`）の `passed` にその行の id があり、ndf-next の本文が旧いスクリプトを呼ばないときだけ付く
- 前提 9: document-systems の Skill の本文のうち、NDF のスクリプトに頼るのは `system-gdrive.md` の Google スライドの描画の手段（サムネイルの保存）の 1 か所だけである（試走の判定の理由の文が挙げる旧いスクリプトは `slides-thumbnails.py` の 1 つだけ）。ほかの手順は gws・Notion などの外部のコマンドの案内で、NDF を通らない。このため、その 1 か所を ndf-next だけで通した記録を、document-systems の Skill の行を通した記録として扱う
- 前提 10: 試走の記録はこのリポジトリ（公開）に入る。記録には、プレゼンテーションの ID・Google のアカウント・ファイルの名前と中身を書かない。`<プレゼンテーションの ID>` のように置き換えて書く

## 対象範囲

含む:
- 確認のサブコマンドと `ndf records gws-check` が使う、認証済みかの判定の直し（前提 7）と、そのテスト・確定仕様の判定の記述
- `plugins/ndf-next/skills/document-systems/references/system-gdrive.md` の Google スライドの行の書き換え（`ndf gws thumbnails` を呼ぶ）
- 実の gws で 2 つのサブコマンドを ndf-next だけで打ち、試走の記録に残すこと
- 試走の判定のコマンドで、機能の対応表の `trial` の列を書き直すこと
- 済んだ受け入れ条件（2 つのサブコマンド・スキーマ・テスト・google-workspace の Skill の本文）が変わらず成り立つこと

含まない:
- 2 つのサブコマンドの作り直し（develop にある。判定の直しの 1 か所を除いて変えない）
- 今の `plugins/ndf/` の変更（スクリプト・テスト・Skill の本文・確定仕様 `docs/specifications/ndf-google-workspace-gws.md` の今の NDF の判定の記述のどれも変えない）
- 旧い許可ファイル（`scripts/contract-allow/` の gws-check.py の 1 件）の削除（S13。入れ替え S2 #3304）
- 機能の対応表の `now`・`to` の書き換え（S12）
- google-workspace の Skill の操作の対応表にある gws のコマンドをサブコマンドにすること
- 試走 T2 #3411 のほかの行と、試走のスプリントそのもの
- 時間で区切った負荷試験

## ドメインイベント

E1〜E11 は 2 つのサブコマンドの中で起きること（develop にある）、E12〜E15 はこのやり直しで足す、試走で起きることである。

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | gws を PATH の上で探した | 確認のサブコマンドを打った | — | — |
| E2 | gws の認証の状態を 1 回読んだ（`gws auth status`） | E1 で gws が見つかった・E5 の後に見つかった | 起動できない・0 以外で終わる・JSON でない・3 つのキーが欠ける → 読めない（終了コード 2） | E1 |
| E3 | 認証済みか未認証かを決めた（前提 7） | E2 | — | E2 |
| E4 | 導入の同意を求める承認資料を書いた | E1 で gws が無く、npm があり、導入の同意のフラグが無い | npm も無い → 導入できない（終了コード 3） | E1 |
| E5 | npm で gws を全体インストールした | E1 で gws が無く、導入の同意のフラグがある | npm が失敗する・入れた後も PATH に gws が無い → 失敗（終了コード 1） | E1。同じ起動の中で E2 へ戻る |
| E6 | 確認の結果を返した | E3・E4・E5 の失敗・E2 の失敗 | — | E3 か E4 か失敗 |
| E7 | 引数（プレゼンテーションの ID・出力のディレクトリ・大きさ）を読んだ | サムネイルのサブコマンドを打った | 引数の誤り → 読めない（終了コード 2） | — |
| E8 | ページの ID をページ順に取った | E7 の後、gws と curl がある | gws か curl が無い → 前提が無い（終了コード 3）。gws が失敗した → E11 | E7 |
| E9 | 各ページのサムネイルの URL を取り、画像を保存した | E8 | gws が失敗した → E11。応答に URL が無い・ページの ID が読めない → 読めない（終了コード 2）。画像を取れない → 失敗（終了コード 1） | E8 |
| E10 | サムネイルの結果を返した | E9 で全ページを保存した | — | E9 |
| E11 | gws の失敗が未認証によるものかを決めた（前提 7 の同じ判定） | E8・E9 の gws が 0 以外で終わった | 認証の状態を読めない → 一般の失敗（終了コード 1） | E8 か E9 |
| E12 | 試走で、ndf-next だけを読む起動から確認のサブコマンドを打った | 実装の後、実の gws がある環境で | `authenticated` 以外が返った → その行を記録の `passed` に載せない（受け入れ条件 20） | E1〜E6 |
| E13 | 試走で、`system-gdrive.md` の Google スライドの手順に従ってサムネイルのサブコマンドを打った | E12 の後 | `ok` 以外が返った・ページ数と保存したファイルの数が合わない → 2 行を記録の `passed` に載せない | E7〜E10 |
| E14 | 試走の記録に、打ったコマンド・結果・`passed` の行の id を書いた | E12・E13 | — | E12・E13 |
| E15 | 試走の判定のコマンドで、機能の対応表の `trial` の列を書き直した | E14 | 3 行のどれかが `passed` にならない → 原因を直すまで閉じない | E14 と、`system-gdrive.md` の書き換え |

## 用語

| 用語 | 意味 |
| --- | --- |
| gws | Google Workspace CLI（npm の `@googleworkspace/cli`）。Google の公式サポート外の CLI で、バージョンは 0.x |
| 機能の対応表 | 用語集の語。この課題では `scripts/ndf-next-parity/parity/features.json` を指す |
| 入れ替えの前の試走 | 用語集の語。本文では「試走」と書く。打ったコマンドと結果・通した行の id（`passed`）は試走の記録（`scripts/ndf-next-parity/parity/trial/<日付>.json`）に残り、行ごとの判定は試走の判定のコマンド（`python -m parity.trial.probe`。`--write` で `trial` の列を書く）が決める |

## 受け入れ条件

確認のサブコマンド `ndf gws check`（今の `gws-check.py` と同じ判定。前提 2・7）:

- ~~1. gws が PATH にあり、トークンを環境変数 `GOOGLE_WORKSPACE_CLI_TOKEN` で渡しているか、`gws auth status` の `credential_source` が `none`・`client_secret.json` のどちらでもないとき、`status` が `ok`、終了コードが 0、`metrics.state` が `authenticated` になり、`metrics` に `credential_source`・`auth_method`・`client_config_exists` の 3 つが入る~~ → 下の 1 へ変更（2026-11-16、実の環境で OAuth でログインした状態が未認証になると分かったため。「今の状態」）
- [ ] 1. gws が PATH にあり、トークンを環境変数 `GOOGLE_WORKSPACE_CLI_TOKEN` で渡しているか、`gws auth status` が前提 7 の未認証の 2 つの形のどちらでもないとき、`status` が `ok`、終了コードが 0、`metrics.state` が `authenticated` になり、`metrics` に `credential_source`・`auth_method`・`client_config_exists` の 3 つが入る。`credential_source` が `client_secret.json`・`auth_method` が `oauth2`・`client_config_exists` が真の状態をテストで確かめる
- ~~2. gws が PATH にあり、トークンを環境変数で渡しておらず、`credential_source` が `none` か `client_secret.json` のとき、`status` が `gate`、終了コードが 11、`metrics.state` が `unauthenticated` になり、`metrics.client_config_exists` に gws が返した値が入る（前提 7）。次の 2 つの状態をそれぞれテストで確かめる: (a) トークンが無い（`auth_method` が `none`・`credential_source` が `none`）、(b) `client_secret.json` だけがありトークンが無い（`auth_method` が `none`・`credential_source` が `client_secret.json`・`client_config_exists` が真。#2067 の再現の形）~~ → 下の 2 へ変更（2026-11-16、同じ理由）
- [ ] 2. gws が PATH にあり、トークンを環境変数で渡しておらず、`gws auth status` が前提 7 の未認証の 2 つの形のどちらかのとき、`status` が `gate`、終了コードが 11、`metrics.state` が `unauthenticated` になり、`metrics.client_config_exists` に gws が返した値が入る。次の 2 つの状態をそれぞれテストで確かめる: (a) トークンが無い（`auth_method` が `none`・`credential_source` が `none`）、(b) `client_secret.json` だけがありトークンが無い（`auth_method` が `none`・`credential_source` が `client_secret.json`・`client_config_exists` が真。#2067 の再現の形）
- [x] 3. gws が PATH に無く npm があり、導入の同意のフラグが無いとき、npm を打たずに、`status` が `gate`、終了コードが 10、`metrics.state` が `missing` になり、`presentation_path` に承認資料（取得元・打つコマンド `npm install -g @googleworkspace/cli`・入る先・戻し方）のファイルがある（根拠: `plugins/ndf-next/tests/domains/gws/test_check.py` の `test_missing_without_install_writes_the_material_and_does_not_install`。PR #3577 の CI は 23 件が成功）
- [x] 4. 受け入れ条件 2・3 の結果は `routes` を持ち、そのキーは回答の語彙（`contracts/facts/answers.json`）の中にあり、各ルートの `argv` の先頭が `ndf` である。受け入れ条件 3 の結果には、導入の同意のフラグを付けて確認のサブコマンドを打ち直すルートがある（根拠: 同じテストと、`ndf check --base origin/develop` が終了コード 0。実の環境の出力の `routes` は `{"retry": {"argv": ["ndf", "gws", "check"]}, "cancel": null}`）
- [x] 5. gws も npm も PATH に無いとき、`status` が `stopped`、終了コードが 3、`metrics.state` が `uninstallable` になる（根拠: `test_uninstallable_without_npm`）
- [x] 6. 導入の同意のフラグを付けて打ち、gws が無いとき、`npm install -g @googleworkspace/cli` を 1 回打ち、同じ起動の中で受け入れ条件 1・2 の判定を返す。npm が 0 以外で終わったときと、入れた後も gws が PATH に無いときは `status` が `stopped`、終了コードが 1 になる。gws があるときは npm を打たない（根拠: `test_install_then_rechecks`・`test_install_failure_stops_with_1_and_shows_the_tail`・`test_installed_but_not_on_path_stops_with_1`・`test_install_when_gws_present_does_nothing`）
- [x] 7. `gws auth status` を起動できない・0 以外で終わる・JSON でない・3 つのキーのどれかが欠けるとき、`status` が `stopped`、終了コードが 2 になる。どの場合も gws のサブコマンドは `auth status` 以外を打たず、`metrics` に 3 つのキー以外の値を写さない（根拠: `test_unreadable_status_stops_with_2_and_shows_stderr`・`test_does_not_copy_other_keys`）

サムネイルのサブコマンド `ndf gws thumbnails`（今の `slides-thumbnails.py` と同じ出力。前提 2）:

- [x] 8. ページが N 枚のプレゼンテーションで打つと、出力のディレクトリに `p1.png`〜`pN.png` をページ順に保存し、`status` が `ok`、終了コードが 0、`items` がページごとに `name`（`p<番号>`）・`objectId`・保存先を持ち、`metrics` が `pages`（N）・`size`・`out_dir` を持つ。成功したときは `gws auth status` を打たない（根拠: `test_thumbnails.py` の `test_saves_every_page_in_order`）
- [x] 9. 大きさは `SMALL`・`MEDIUM`・`LARGE` の 3 つだけを受け、指定が無ければ `LARGE` になる。引数が足りない・3 つ以外の大きさを渡したときは `status` が `stopped`、終了コードが 2 になる（根拠: `test_size_is_passed_to_gws`・`test_bad_arguments_return_2`）
- [ ] 10. gws が 0 以外で終わり、`gws auth status` の結果が受け入れ条件 2 と同じ判定で未認証のとき、`status` が `gate`、終了コードが 11、`metrics.state` が `unauthenticated` になり、`routes` を持つ（キーは回答の語彙の中、`argv` の先頭は `ndf`）。前の回の判定では `test_unauthenticated_returns_11` が満たしている。判定を前提 7 へ直した後に、`credential_source` が `client_secret.json`・`auth_method` が `oauth2` の状態が未認証にならず受け入れ条件 11 の失敗になることを足して確かめる
- [x] 11. gws が 0 以外で終わり、未認証でないとき、`status` が `stopped`、終了コードが 1 になり、`items` に gws の出力の末尾（20 行まで）が載る。gws か curl が PATH に無いときは終了コードが 3 になる（根拠: `test_gws_failure_stops_with_1_and_shows_the_tail`・`test_missing_gws_or_curl_returns_3`）

コントラクトと Skill の本文:

- [x] 12. 2 つのサブコマンドの引数と結果のスキーマが lspec の仕組みに載り、生成されたサブコマンドの一覧（`plugins/ndf-next/docs/contracts/commands.md`）に 2 つのサブコマンドの行がある（根拠: `commands.md` の 73〜80 行目に `## ndf gws`・`ndf gws check`・`ndf gws thumbnails`）
- [ ] 13. `plugins/ndf-next/bin/ndf check --base origin/develop` が終了コード 0 で終わる（2026-11-16 の develop 0be66cb77 では終了コード 0・「1099 ファイルに違反なし」。実装の後にもう一度確かめる）
- [x] 14. google-workspace の Skill の本文（`plugins/ndf-next/skills/google-workspace/SKILL.md`）に `gws-check.py`・`slides-thumbnails.py`・`$SKILL_DIR/scripts/`・`#3547` の文字列が 0 件で、流れの手順 1・2 と操作の対応表のサムネイルの行がサブコマンドを打つ（根拠: `grep -c` が 0。20・34・44・66 行目が `ndf gws check`・`ndf gws thumbnails`）
- ~~15. 機能の対応表の 2 行が、作ったサブコマンドを試走 T2 #3411 が確かめられる形で指している（書き方は設計が決める。未決 3）~~ → 下の 15 へ変更（2026-11-16、書き方を決めたため。S12）
- [ ] 15. 機能の対応表の 2 行（`entrypoint:skills/google-workspace/scripts/gws-check.py`・`entrypoint:skills/google-workspace/scripts/slides-thumbnails.py`）の `now` と `to`（`S1d`）は変わらず、試走の判定のコマンドが 2 行を `passed` と判定し、`record` が受け入れ条件 20 の試走の記録を指す
- [ ] 16. 今の `plugins/ndf/` に差分が無い（`git diff --stat origin/develop -- plugins/ndf/` が空。2026-11-16 の時点で空。実装の後にもう一度確かめる）
- [ ] 17. 認証済みかの判定は新しい NDF の中で 1 つの関数（記録の部品の `authenticated`）にあり、確認のサブコマンド・サムネイルのサブコマンド・`ndf records gws-check` が同じ関数を呼ぶ（2026-11-16 の時点で、`check.py` の 71 行目・`thumbnails.py` の 132 行目・`records/cli.py` の 190 行目が呼ぶ。判定を直した後にもう一度確かめる）

試走で通す（やり直しで足した条件）:

- [ ] 18. `plugins/ndf-next/skills/document-systems/references/system-gdrive.md` の Google スライドの描画の手段の行が `ndf gws thumbnails` を打つ形で書かれ、`slides-thumbnails.py` の文字列が 0 件である。ndf-next の Skill・エージェント・hook の本文で、`gws-check.py`・`slides-thumbnails.py` を呼ぶファイルが 0 件である（試走の判定のコマンドが 2 行と `skill:document-systems` の行を `failed` にしない）
- [ ] 19. 試走の判定のコマンドが `skill:document-systems` の行を `passed` と判定し、`record` が受け入れ条件 20 の試走の記録を指す（前提 9）
- [ ] 20. 試走の記録に次の 2 つの実行があり、`passed` に 3 行（受け入れ条件 15 の 2 行と `skill:document-systems`）の id がある。記録に前提 10 の値が無い
  - (a) 実の gws が認証済みの環境で、ndf-next だけを読む起動から `plugins/ndf-next/bin/ndf gws check` を打ち、`ok`・終了コード 0・`metrics.state` が `authenticated` だった
  - (b) 同じ環境で、`system-gdrive.md` の Google スライドの手順のとおりに `plugins/ndf-next/bin/ndf gws thumbnails <プレゼンテーションの ID> <出力のディレクトリ>` を打ち、`ok`・終了コード 0 で、`metrics.pages` と保存した `p<番号>.png` の数が一致した
- [ ] 21. 機能の対応表の `trial` の列は、試走の判定のコマンドを `--write` 付きで打った結果である（実装の後に打ち直しても機能の対応表に差分が出ない）
- [ ] 22. 確定仕様 `docs/specifications/ndf-next-gws-subcommands.md` の認証済みの判定の記述と、環境変数の定義（`plugins/ndf-next/contracts/domains/env.json` の `GOOGLE_WORKSPACE_CLI_TOKEN` の行と、そこから生成する `plugins/ndf-next/docs/contracts/env.md`）が、前提 7 の判定と同じことを書く
- [ ] 23. 旧い許可ファイル `scripts/contract-allow/plugins__ndf__skills__google-workspace__scripts__gws-check.py--core-entry.json` が残り、`python3 scripts/check-ndf-contracts.py` が `ok` で、使われていない例外が 0 件である（S13）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 失敗したときは gws・npm・curl の出力の末尾（20 行まで）を `items` に載せ、利用者が原因を切り分けられる（受け入れ条件 7・11） |
| セキュリティ | gws の資格情報のファイルとトークンを開かない・写さない。`gws auth status` から読むのは 3 つのキーだけ（受け入れ条件 7、前提 7）。gws の導入（共通原則の C6）は導入の同意のフラグがあるときだけ行い、`sudo` を打たず、PATH と設定のファイルを書き換えない。試走の記録（公開のリポジトリ）にプレゼンテーションの ID・アカウント・ファイルの名前と中身を書かない（前提 10） |
| システム環境 | gws・npm・curl は PATH の上で探す。上限の秒数は前提 6 のとおり |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 無い。サブコマンドの名前・引数・結果のスキーマは変えない |
| 既存の振る舞い | `credential_source` が `client_secret.json` で `auth_method` が `none` 以外の状態で、`ndf gws check`・`ndf gws thumbnails` の失敗のときの判定・`ndf records gws-check` が、未認証から認証済みに変わる。#2067 の形（`auth_method` が `none`）は未認証のまま |
| データ | 試走の記録が 1 件増えるか、既存の記録に実行が増える（未決 1）。機能の対応表の `trial` の列が書き直される |
| 今の NDF | 無い（受け入れ条件 16） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `plugins/ndf-next/bin/ndf gws check`・`plugins/ndf-next/bin/ndf gws thumbnails --help` |
| テスト | `plugins/ndf-next/tests/domains/gws/` と `plugins/ndf-next/tests/domains/records/` のテスト（gws・npm・curl は偽のコマンドで差し替える。CI で走る。手元では打たない） |
| 静的解析・型検査 | `plugins/ndf-next/bin/ndf check --base origin/develop`・`python3 scripts/check-ndf-contracts.py` |
| 試走の判定 | `scripts/ndf-next-parity` で、ndf-next の依存の環境の Python から `python -m parity.trial.probe`（判定の一覧）と `--write`（機能の対応表へ書く） |
| 実の環境の確認 | 受け入れ条件 20 の (a)・(b)。実の gws と Google のアカウントで、読むだけの操作（書き込みはしない） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 2 つのサブコマンドは `plugins/ndf-next/src/ndf/domains/gws/`、テストは `plugins/ndf-next/tests/domains/gws/`、認証済みの判定は `plugins/ndf-next/src/ndf/domains/records/gws.py`。試走の記録と機能の対応表は `scripts/ndf-next-parity/parity/`。今の `plugins/ndf/` には置かない |
| コーディング規約 | 新しい NDF の結果のコントラクト（`contracts/schemas/result.schema.json`）・終了コードと停止理由（`docs/contracts/exit-codes.md`）に従い、`ndf check` で確かめる |
| テスト戦略 | 判定の分かれ目（受け入れ条件 1・2・10）は、偽の gws を PATH に置いた単体のテストで確かめる。文書の文言を検証するテストは書かない（`AGENTS.md`）。受け入れ条件 18・19・21 は試走の判定のコマンドが、12〜14 は `ndf check` が確かめる。受け入れ条件 20 は実の環境で 1 回打つ |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 偽のコマンドでのテスト、`ndf check`、試走の判定のコマンドでの `trial` の列の書き直し |
| 確認してから行う | 認証済みの判定の変更（設計承認ゲートで見る） |
| 行わない | 今の `plugins/ndf/` の変更、旧い許可ファイルの削除、機能の対応表の `now`・`to` と `trial` の列の手での書き換え、実の Google のアカウントへの書き込み、試走の記録へのプレゼンテーションの ID・アカウントの記載 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 試走の記録を新しい日付のファイルにするか、既存の `trial/2026-11-16.json` に実行を足すか。新しいファイルにすると、試走の判定のコマンドはほかの `not_run` の行の `record` も新しいファイルへ向け直す（最新の記録を指すため） | 設計（`design`） | 設計承認ゲート |
| 2. 受け入れ条件 20 (b) で使うプレゼンテーションの選び方（実の環境のアカウントで読めるもの。ID は記録に書かない） | 実装（計画 p8 のレーン ls1d） | 実装の Pull Request |

## 依頼（原文）

> #3547。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。本文を書いたら `spec-copy.py write 3547 issues/issue-3547-requirements.md` で写しを作り、コミットする（push しない）。

（書き手: AI の起動指示、日付: 2026-11-15 と 2026-11-16。書き手が利用者でない文のため、利用者の指示の原文として扱わない）

> # #3547 の設計のやり直し: 指摘（利用者の指示 2026-11-16 16:3x JST、レーン ld-s1d）
>
> - 既にできているもの（ndf gws check・thumbnails、Skill 本文の書き換え、テスト）を作り直さない。要求は残っている差を受け入れ条件にし、済んだ受け入れ条件は検証のコマンドで確かめて済んだと記す
> - 試走 T2 の記録（scripts/ndf-next-parity/parity/trial/2026-11-16.json）が失敗・未実施とした 3 行（features.json の gws-check.py・slides-thumbnails.py の 2 行と skill:document-systems）を通る形にする。system-gdrive.md の 72 行の旧い slides-thumbnails.py の呼び出しを含む
> - ndf-next（plugins/ndf-next/）の上で設計し、今の plugins/ndf/ は変えない
> - 用語は #3613 の対応表（issues/issue-3613-design.md・issues/issue-3613-design-detail.md）に合わせ、置き換えると決めた語は置き換え先で書く
> - 詳細設計のファイル配置の表に、新しく作るファイルだけでなく、消すファイル・書き換える既存のファイルとテストをすべて載せる（実装のプランの触る範囲はこの表から作られる。旧い部品が表に無いと範囲の外として止まる）。scripts/contract-allow/ の旧い許可ファイルと features.json の行も 1 行ずつ消すか残すかを決める
> - 実装はこのスプリントでは行わない（計画 p8 のレーン ls1d が行う）。設計 PR は develop 宛て

（書き手: AI の起動指示（設計承認ゲートの変更要求として渡された項目。見出しは「利用者の指示 2026-11-16 16:3x JST」で、項目の文が利用者の原文そのものかは確かめられない）、日付: 2026-11-16）

> ## 目的
>
> 新しい NDF（`plugins/ndf-next/`）と今の `ndf` を入れ替える前に、google-workspace の Skill が呼ぶスクリプトを新しい作りの上で動かす。
>
> ## 対象範囲
>
> `plugins/ndf/skills/google-workspace/scripts/` の 3 本と、`plugins/ndf-next/` の google-workspace の Skill 本文・機能の対応表。今の `plugins/ndf/` は変えない。
>
> ## 背景
>
> #1142 の S1a #3303（Skill 本文とエージェント定義を `plugins/ndf-next/` へ移す）の後半で、google-workspace の Skill だけはスクリプトを移せなかった。
>
> - 対象: `plugins/ndf/skills/google-workspace/scripts/` の `gws-check.py`（182 行）・`slides-thumbnails.py`（166 行）・`gws_common.py`（27 行）
> - 移せない理由: 旧い共通部品（`step_result`・`limits`）に依存し、新しい結果の契約（関門の結果は `routes` が必須・argv の先頭は `ndf`）に合わない。そのまま写すと `ndf check` が落ちる
> - S1a は範囲（本文とパスの書き換えだけ。決定 126）を広げず、Skill 本文に「副命令はこの Issue で作る」と注記して移した
> - 機能の対応表 `plugins/ndf-next/src/ndf/parity/features.json` の 2 行（`gws-check.py`・`slides-thumbnails.py`）の行き先は、すでに S1d（この課題）を指している（develop 6fb221dae の 123・124 行の `"to": "S1d"`）
>
> ## やること
>
> - 2 本を `bin/ndf` の副命令として作り直す（置き場の群は設計で決める。候補: 新しい群 `ndf gws`）
> - google-workspace の Skill 本文の注記を消し、コマンドを副命令へ書き換える
>
> ## 受け入れ条件
>
> - [ ] `ndf <群> check` 相当が、gws の有無・認証の状態を今の `gws-check.py` と同じ判定で返す（結果は新しい契約の JSON）
> - [ ] スライドの縮小画像の取得が、今の `slides-thumbnails.py` と同じ出力を返す
> - [ ] `plugins/ndf-next/bin/ndf check --base origin/develop` が通る
> - [ ] google-workspace の Skill 本文に旧いスクリプトの参照が残らない
>
> ## 進め方の決まり
>
> 並列の計画 p6 のレーン lr-s1d のオーガナイザーの指示（2026-11-15 JST）。
>
> - 置き場の群（候補 `ndf gws`）は設計で決める。結果の契約（関門の結果は `routes` が必須・argv の先頭は `ndf`）に合わせ、`ndf check` を通す
> - 今の `plugins/ndf/` は変えない
> - lspec（#3518・#3519）が副命令の引数と結果のスキーマを足した。新しい副命令のスキーマも lspec の仕組み（`ndf check` の規則 io・skill）に載せる
> - 後方互換は意識しない。時間で区切った負荷試験は書かない
> - 触る場所: `plugins/ndf-next/src/ndf/domains/gws/`・`plugins/ndf-next/tests/domains/gws/`・`plugins/ndf-next/skills/google-workspace/`・`plugins/ndf-next/src/ndf/parity/features.json`（副命令の登録とスキーマの置き場は設計で決める）
> - T2 #3411（レーン lrebuild）と同時に進む。入れ替え S2 #3304 の前に閉じる
>
> ## 先に要るもの
>
> S1a #3303（閉じている）。入れ替え S2 #3304 の前に閉じる。
>
> Refs #1142 #3303

（書き手: 起票者（レーン lrebuild の conductor と、並列の計画 p6 のレーン lr-s1d のオーガナイザー。いずれも AI）、日付: 2026-11-15。書き手が利用者でない文のため、利用者の指示の原文として扱わない）
