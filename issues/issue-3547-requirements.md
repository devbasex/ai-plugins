# #3547: #1142 S1d: google-workspace のスクリプト（gws-check・slides-thumbnails）を ndf の副命令として作り直す

正は課題の本文（#3547）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 目的

新しい NDF（`plugins/ndf-next/`）と今の `ndf` を入れ替える（入れ替え S2 #3304）前に、google-workspace の Skill が呼ぶ 2 つの機能を、新しいエントリポイント `bin/ndf` の副命令として動かす。

- 2 つの機能: gws（Google Workspace CLI）の有無と認証の状態の確認（今の `gws-check.py`。この文書では行き先を「確認の副命令」と呼ぶ）と、Google Slides の全ページの縮小画像（サムネイル）の保存（今の `slides-thumbnails.py`。行き先を「縮小画像の副命令」と呼ぶ）
- 達成したい状態: 新しい NDF の google-workspace の Skill 本文が、今のスクリプトのパスを 1 つも参照せず、副命令だけで流れ（確かめる → 同意のうえ入れる → 認証を案内する → 操作する）を通せる

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめる（MVV 判定が承認ゲートを省いたときはその判定が確かめた記録になる）まで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

この課題の本文は、題・背景・やること・受け入れ条件・進め方の決まりのすべてを AI（レーン lrebuild の conductor と、並列の計画 p6 のレーン lr-s1d のオーガナイザー）が書いた。利用者の指示の原文は本文に無い。下の行は、その中で振る舞いを決める条件を拾ったものである。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 2 本を `bin/ndf` の副命令として作り直す（置き場の群は設計で決める。候補は新しい群 `ndf gws`） | 本文「やること」（conductor）・「進め方の決まり」（オーガナイザー） | 受け入れ条件 1〜11。群の名前は未決 1（設計が決める） |
| S2 | 結果は新しい契約に合わせる（承認ゲートの結果は `routes` が必須・`next` の argv の先頭は `ndf`） | 本文「背景」「進め方の決まり」 | 受け入れ条件 4・12 |
| S3 | 確認は今の `gws-check.py` と同じ判定で返す | 本文「受け入れ条件」1 行目 | 受け入れ条件 1〜7（判定の同じさを、状態・終了コード・`metrics` のキーで観測できる形にした） |
| S4 | 縮小画像の取得は今の `slides-thumbnails.py` と同じ出力を返す | 本文「受け入れ条件」2 行目 | 受け入れ条件 8〜11（同じさを、保存するファイル・`items`・`metrics`・終了コードで観測できる形にした）。結果 JSON の外枠は新しい契約に従うため、外枠は同じにしない（前提 2） |
| S5 | 新しい副命令の引数と結果のスキーマを lspec（#3518・#3519）の仕組みに載せる | 本文「進め方の決まり」 | 受け入れ条件 12・13 |
| S6 | 今の `plugins/ndf/` は変えない | 本文「対象範囲」「進め方の決まり」 | 受け入れ条件 16・含まない |
| S7 | 後方互換は意識しない | 本文「進め方の決まり」 | 前提 3（今のスクリプトのパスで呼ぶ互換の経路を新しい NDF に持たない） |
| S8 | 時間で区切った負荷試験は書かない | 本文「進め方の決まり」 | 含まない |
| S9 | 触る場所は `src/ndf/domains/gws/`・`tests/domains/gws/`・`skills/google-workspace/`・`src/ndf/parity/features.json`（副命令の登録とスキーマの置き場は設計で決める） | 本文「進め方の決まり」 | 前提とする取り決めのプロジェクト構造。登録とスキーマの置き場は未決 2 |
| S10 | 対象範囲に共通部品 `gws_common.py`（27 行）を含めるが、副命令にするのは 2 本だけ | 本文「対象範囲」と「やること」の食い違い | 前提 4（共通部品は副命令にしない。新しい NDF の結果の部品で置き換え、同じ処理を 2 か所に持たない） |
| S11 | 入れ替え S2 #3304 の前に閉じる。試走 T2 #3411 と同時に進む | 本文「先に要るもの」「進め方の決まり」 | 前提 5 |

## 前提

- 前提 1: 今の 2 本の振る舞いは、スクリプトの冒頭の説明と、今のテスト（`plugins/ndf/skills/google-workspace/tests/` の 18 件）の名前が示すものを正とする。手元ではテストを走らせない決まり（`.ndf/project.json` の `test.ci.only`）のため、この要求を書く時点では走らせて確かめていない。今のテストの名前は次のとおりである
  - `test_gws_check.py`: `test_authenticated_by_token_env_var`・`test_unauthenticated_is_gate_11`・`test_unauthenticated_without_client_config_guides_setup_first`・`test_missing_without_install_does_not_install`・`test_uninstallable_without_npm`・`test_install_then_rechecks`・`test_install_when_gws_present_does_nothing`・`test_install_failure_stops_with_1`・`test_installed_but_not_on_path_stops_with_1`・`test_unreadable_status_stops_with_2`・`test_never_calls_gws_other_than_auth_status`・`test_does_not_copy_other_keys`・`test_unknown_argument_is_rejected`
  - `test_slides_thumbnails.py`: `test_saves_every_page_in_order`・`test_gws_failure_stops_with_1_and_shows_output`・`test_unauthenticated_returns_11`・`test_success_never_calls_auth_status`・`test_bad_arguments_return_2`
- 前提 2: 「同じ判定」「同じ出力」は、状態の名前（`metrics.state`）・終了コード・`metrics` と `items` の中身・保存するファイルが同じであることを指す。結果 JSON の外枠（`status` と `cause`・`routes`・`next` の形）は新しい契約（`plugins/ndf-next/contracts/schemas/result.schema.json`）に従う。新しい契約の `next` はそのまま打てるコマンドで文を持てないため、今の `next` の文が持つ利用者への案内（`gws auth login` の打ち方、OAuth クライアントが無いときの `gws auth setup` の案内）は、Skill 本文の手順と `summary` が持つ。案内の分かれ目（OAuth クライアントの有無）は `metrics.client_config_exists` で読める
- 前提 3: 今のスクリプトのパス（`scripts/gws-check.py`・`scripts/slides-thumbnails.py`）で呼ぶ互換の経路を、新しい NDF に持たない。新しい NDF はまだ利用者へ配っていないため、壊れる呼び出し元は無い
- 前提 4: 共通部品 `gws_common.py` の 2 つの処理（出力の末尾 20 行を切り出す・stopped の結果を出して終える）は副命令にしない。stopped の結果は新しい NDF の結果の部品（`ndf.contracts.result`）で組み、出力の末尾の切り出しは新しい群の中の 1 か所に置く
- 前提 5: この課題は入れ替え S2 #3304 より先に閉じる。試走 T2 #3411 が機能の対応表の 2 行を確かめるとき、行は作った副命令を指している
- 前提 6: gws の呼び出しと導入の上限の秒数は、新しい NDF にすでにある上限の宣言（`plugins/ndf-next/contracts/limits.json` の `google-workspace.call`・`google-workspace.install`・`google-workspace.thumbnails`）を使い、値を変えない

## 対象範囲

含む:
- gws の有無と認証の状態を確かめる副命令（導入の同意を得たときの導入を含む）
- Google Slides の全ページの縮小画像を保存する副命令
- 2 つの副命令の引数と結果のスキーマを、副命令の引数と結果の仕組み（`ndf check` の規則 io・skill）に載せること
- 新しい NDF の google-workspace の Skill 本文の書き換え（移したときの注記を消し、コマンドを副命令へ書き換える）
- 機能の対応表 `plugins/ndf-next/src/ndf/parity/features.json` の 2 行の更新
- 2 つの副命令のテスト（今のテスト 18 件が確かめている振る舞いを、新しい副命令に対して確かめる）

含まない:
- 今の `plugins/ndf/` の変更（スクリプト・テスト・Skill 本文のどれも変えない）
- google-workspace の Skill の操作の対応表にある gws のコマンド（エクスポート・アップロード・共有範囲の確認など）を副命令にすること。Skill 本文から gws を直接打つまま残す
- 判定の変更（状態の数・判定の条件・導入のコマンド・導入の同意の扱いを変えない）
- 今のスクリプトのパスで呼ぶ互換の経路（前提 3）
- 時間で区切った負荷試験
- 入れ替え S2 #3304 と試走 T2 #3411 の作業

## ドメインイベント

確認の副命令（E1〜E6）と、縮小画像の副命令（E7〜E11）で起きることを時間の順に並べる。導入の同意の印は、利用者が gws の導入に同意したことを示す引数（今の `--install`）で、付いたときだけ npm で入れる。

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | gws を PATH の上で探した | 確認の副命令を打った | — | — |
| E2 | gws の認証の状態を 1 回読んだ（`gws auth status`） | E1 で gws が見つかった・E5 の後に見つかった | 起動できない・0 以外で終わる・JSON でない・3 つのキーが欠ける → 読めない（終了コード 2） | E1 |
| E3 | 認証済みか未認証かを決めた | E2 | — | E2 |
| E4 | 導入の同意を求める承認資料を書いた | E1 で gws が無く、npm があり、導入の同意の印が無い | npm も無い → 導入できない（終了コード 3） | E1 |
| E5 | npm で gws を全体インストールした | E1 で gws が無く、導入の同意の印がある | npm が失敗する → 失敗（終了コード 1）。入れた後も PATH に gws が無い → 失敗（終了コード 1） | E1。同じ起動の中で E2 へ戻る |
| E6 | 確認の結果を返した | E3・E4・E5 の失敗・E2 の失敗 | — | E3 か E4 か失敗 |
| E7 | 引数（プレゼンテーションの ID・出力のディレクトリ・大きさ）を読んだ | 縮小画像の副命令を打った | 引数の誤り → 読めない（終了コード 2） | — |
| E8 | ページの ID をページ順に取った | E7 の後、gws と curl がある | gws か curl が無い → 前提が無い（終了コード 3）。gws が失敗した → E11 | E7 |
| E9 | 各ページの縮小画像の URL を取り、画像を保存した | E8 | gws が失敗した → E11。応答に URL が無い・ページの ID が読めない → 読めない（終了コード 2）。画像を取れない → 失敗（終了コード 1） | E8。ページ順に 1 から番号を振る |
| E10 | 縮小画像の結果を返した | E9 で全ページを保存した | — | E9 |
| E11 | gws の失敗が未認証によるものかを決めた | E8・E9 の gws が 0 以外で終わった | 認証の状態を読めない → 未認証ではない一般の失敗（終了コード 1） | E8 か E9 |

## 用語

| 用語 | 意味 |
| --- | --- |
| gws | Google Workspace CLI（npm の `@googleworkspace/cli`）。Google の公式サポート外の CLI で、版は 0.x |

## 受け入れ条件

確認の副命令（今の `gws-check.py` と同じ判定。前提 2）:

- [ ] 1. gws が PATH にあり、`gws auth status` の `credential_source` が `none` でないとき、`status` が `ok`、終了コードが 0、`metrics.state` が `authenticated` になり、`metrics` に `credential_source`・`auth_method`・`client_config_exists` の 3 つが入る
- [ ] 2. gws が PATH にあり、`credential_source` が `none` のとき、`status` が `gate`、終了コードが 11、`metrics.state` が `unauthenticated` になり、`metrics.client_config_exists` に gws が返した値が入る
- [ ] 3. gws が PATH に無く npm があり、導入の同意の印が無いとき、npm を打たずに、`status` が `gate`、終了コードが 10、`metrics.state` が `missing` になり、`presentation_path` に承認資料（取得元・打つコマンド `npm install -g @googleworkspace/cli`・入る先・戻し方）のファイルがある
- [ ] 4. 受け入れ条件 2・3 の結果は `routes` を持ち、そのキーは答えの語彙（`contracts/facts/answers.json`）の中にあり、各行き先の `argv` の先頭が `ndf` である。受け入れ条件 3 の結果には、導入の同意の印を付けて確認の副命令を打ち直す行き先がある
- [ ] 5. gws も npm も PATH に無いとき、`status` が `stopped`、終了コードが 3、`metrics.state` が `uninstallable` になる
- [ ] 6. 導入の同意の印を付けて打ち、gws が無いとき、`npm install -g @googleworkspace/cli` を 1 回打ち、同じ起動の中で受け入れ条件 1・2 の判定を返す。npm が 0 以外で終わったときと、入れた後も gws が PATH に無いときは `status` が `stopped`、終了コードが 1 になる。gws があるときは npm を打たない
- [ ] 7. `gws auth status` を起動できない・0 以外で終わる・JSON でない・3 つのキーのどれかが欠けるとき、`status` が `stopped`、終了コードが 2 になる。どの場合も gws の副命令は `auth status` 以外を打たず、`metrics` に 3 つのキー以外の `gws auth status` の値を写さない

縮小画像の副命令（今の `slides-thumbnails.py` と同じ出力。前提 2）:

- [ ] 8. ページが N 枚のプレゼンテーションで打つと、出力のディレクトリに `p1.png`〜`pN.png` をページ順に保存し、`status` が `ok`、終了コードが 0、`items` がページごとに `name`（`p<番号>`）・`objectId`・保存先を持ち、`metrics` が `pages`（N）・`size`・`out_dir` を持つ。成功したときは `gws auth status` を打たない
- [ ] 9. 大きさは `SMALL`・`MEDIUM`・`LARGE` の 3 つだけを受け、指定が無ければ `LARGE` になる。引数が足りない・3 つ以外の大きさを渡したときは `status` が `stopped`、終了コードが 2 になる
- [ ] 10. gws が 0 以外で終わり、`gws auth status` の `credential_source` が `none` のとき、`status` が `gate`、終了コードが 11、`metrics.state` が `unauthenticated` になり、`routes` を持つ（キーは答えの語彙の中、`argv` の先頭は `ndf`）
- [ ] 11. gws が 0 以外で終わり、未認証でないとき、`status` が `stopped`、終了コードが 1 になり、`items` に gws の出力の末尾（20 行まで）が載る。gws か curl が PATH に無いときは終了コードが 3 になる

契約と Skill 本文:

- [ ] 12. 2 つの副命令の引数と結果のスキーマが副命令の引数と結果の仕組みに載り、生成された副命令の一覧（`plugins/ndf-next/docs/contracts/commands.md`）に 2 つの副命令の行がある
- [ ] 13. `plugins/ndf-next/bin/ndf check --base origin/develop` が終了コード 0 で終わる
- [ ] 14. 新しい NDF の google-workspace の Skill 本文（`plugins/ndf-next/skills/google-workspace/SKILL.md`）に `gws-check.py`・`slides-thumbnails.py`・`$SKILL_DIR/scripts/` の文字列が 0 件で、移したときの注記（「副命令は #3547 で作る」）が無く、流れの手順 1・2 と操作の対応表の縮小画像の行が副命令を打つ
- [ ] 15. 機能の対応表の 2 行（`entrypoint:skills/google-workspace/scripts/gws-check.py`・`entrypoint:skills/google-workspace/scripts/slides-thumbnails.py`）が、作った副命令を試走 T2 #3411 が確かめられる形で指している（書き方は設計が決める。未決 3）
- [ ] 16. 今の `plugins/ndf/` に差分が無い（`git diff --stat origin/develop -- plugins/ndf/` が空）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 失敗したときは gws・npm・curl の出力の末尾（20 行まで）を `items` に載せ、利用者が原因を切り分けられる（受け入れ条件 7・11 と今の振る舞いと同じ） |
| セキュリティ | gws の資格情報のファイルとトークンを開かない・写さない。`gws auth status` から読むのは 3 つのキーだけ（受け入れ条件 7）。gws の導入（利用者の環境への全体インストール。共通原則の C6）は導入の同意の印があるときだけ行い、`sudo` を打たず、PATH と設定のファイルを書き換えない |
| システム環境 | gws・npm・curl は PATH の上で探す。gws の呼び出しと導入の上限の秒数は前提 6 のとおり |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 新しい NDF に副命令が 2 つ増える。新しい NDF はまだリリースしていないため、今の利用者への影響は無い。今の `ndf` のスクリプトは変わらない |
| データ | 無い |
| 既存の振る舞い | 新しい NDF の google-workspace の Skill 本文の手順 1・2 と縮小画像の行のコマンドが変わる。判定と終了コードは変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `plugins/ndf-next/bin/ndf <群> <副命令> --help`（群と副命令の名前は設計が決める） |
| テスト | `plugins/ndf-next/tests/domains/gws/` のテスト（gws・npm・curl は偽のコマンドで差し替える。CI で走る） |
| 静的解析・型検査 | `plugins/ndf-next/bin/ndf check --base origin/develop` と、`test-run.py scope --paths <変えたパス>` |
| 手動確認 | 実の gws と Google のアカウントで、確認の副命令が `authenticated` を返し、縮小画像の副命令が実のプレゼンテーションの全ページを保存する（入れ替えの後のリリース後テストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 実装は `plugins/ndf-next/src/ndf/domains/gws/`、テストは `plugins/ndf-next/tests/domains/gws/` に置く（機能の対応表の置き場 `S1d` がこのディレクトリを指す）。副命令の登録は副命令の表（`plugins/ndf-next/src/ndf/cli.py` の `COMMANDS`）に 1 行足す。今の `plugins/ndf/` には置かない |
| コーディング規約 | 新しい NDF の結果の契約（`contracts/schemas/result.schema.json`）・終了コードと止まった理由（`docs/contracts/exit-codes.md`）に従い、`ndf check` で確かめる |
| テスト戦略 | 判定の分かれ目（受け入れ条件 1〜11）は、偽の gws・npm・curl を PATH に置いた単体のテストで確かめる。文書の文言を照合するテストは書かない（`AGENTS.md`）。受け入れ条件 12〜14 は `ndf check` が確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 偽のコマンドでのテスト、`ndf check`、判定と終了コードを今のスクリプトと揃えること |
| 確認してから行う | 新しい NDF の副命令の追加（設計承認ゲートで見る） |
| 行わない | 今の `plugins/ndf/` の変更、判定の変更、操作の対応表の gws のコマンドの副命令化、実の Google のアカウントへの書き込み |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 群と副命令の名前（候補: `ndf gws check`・`ndf gws thumbnails`） | 設計（`design`） | 設計承認ゲート |
| 2. 引数と結果のスキーマの置き場（`contracts/schemas/io/` に生成するか、手書きの引数の表に置くか） | 設計（`design`） | 設計承認ゲート |
| 3. 機能の対応表の 2 行の書き方（`now`・`to`・`trial` のどれを変えるか） | 設計（`design`） | 設計承認ゲート |
| 4. 受け入れ条件 2・3・10 の `routes` に置く答えのキーと行き先（例: 承認する → 導入の同意の印を付けて打ち直す、打ち直す → 引数なしで打ち直す） | 設計（`design`） | 設計承認ゲート |

## 依頼（原文）

> #3547。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。本文を書いたら `spec-copy.py write 3547 issues/issue-3547-requirements.md` で写しを作り、コミットする（push しない）。

（書き手: AI の起動指示、日付: 2026-11-15。書き手が利用者でない文のため、利用者の指示の原文として扱わない）

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
