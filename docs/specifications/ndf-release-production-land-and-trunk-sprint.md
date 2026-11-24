# 本番の取り込みと trunk のスプリントの計画: 宣言のマージの主体・方式で本番ブランチへ入れ、開発版を挟まずに本番まで流す

本番ブランチへ入れるマージを 1 か所（本番の取り込み）に集め、ブランチ戦略の宣言（`.ndf/project.json` の
`branch_strategy`）が決めるマージの主体・方式・履歴の作り方で入れる仕組みと、trunk の宣言のスプリントの計画が
開発版のステージを作らずに本番まで流れる仕組みの、決定と理由・常に成り立つ条件・入出力の契約を残す。
対象は NDF 10 系（`plugins/ndf/`）である。

**打ち方と終了コードの説明は Skill が正である。ここに書き写さない。**

| 何の正か | 置き場 |
| --- | --- |
| `release-steps.py release` の打ち方、公開の欠け（6）、trunk の本番の流れ | [`release` の references/release-steps.md](../../plugins/ndf/skills/release/references/release-steps.md) |
| 承認資料の欄（「承認前の試用で確かめたこと」） | [`development-workflow` の references/approval-request.md](../../plugins/ndf/skills/development-workflow/references/approval-request.md) |

表のパスは、先頭が `plugins/` か `docs/` のものを除き `plugins/ndf/scripts/` から数える。

## 概要

- **本番の取り込み**（`release_lib/prod_land.py` の `land`）は、承認した head の検査を待ち、PR の今の head と照らしてから、
  宣言の主体と方式で入れる関数（`release_lib/land.py` の `land_approved`）を呼ぶ。本番ブランチ宛てのマージは、どの経路もここを通る
- merge queue へ入れた・人のマージを待つときは、タグと GitHub Release を作らずに「公開の欠け」（`stopped`・終了コード 6）で返す。
  打ち直すとマージ済みを見つけて公開する
- trunk の宣言（ベースブランチと本番ブランチが同じ）では、スプリントの計画に開発版のプランを置かない。本番のプランが版上げの PR を
  開いて止まり、その head（`release/v<版>`）から導入を確かめ（承認前の試用）、承認資料を作ってから承認を待つ
- 開発版のプランが無い本番のプランの版は、キューが流す時点に origin の状態から決めて記録する

## 用語

語の定義は用語集（[docs/glossary.md](../glossary.md) ）が正である。この文書で使う主な語は「本番の取り込み」「承認前の試用」
「版上げの PR」「マージの方式」「ブランチ戦略の宣言」である。この文書の中だけで使う語は次の 2 つである。

| 語 | 意味 |
| --- | --- |
| 公開の欠け | 本番ブランチへまだ入っていないため、タグと GitHub Release を作らずに `stopped`・終了コード 6 で返すこと |
| 分かれた経路 | ベースブランチと本番ブランチが分かれた取り込み方の `release --channel prod` |

## 背景

ブランチ戦略の宣言の `merge`（`actor`・`method`・`commit`・`admin`）は、人のマージ・merge queue・履歴の作り方を選べる。
本番ブランチ宛てのマージが `merge-when-green` の直接のマージのままだと、宣言の主体と方式が効かない。
また trunk の宣言では開発版のチャネルを持てない（`release --channel dev` は前提の不足 3 で止まる）。
計画が開発版のプランを作ると、そのステージから先へ進めない。

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| CI の待ちは本番の取り込みの中（`land_approved` の手前）に置き、`land_approved` は待たない | `release-steps.py land` の単独の呼び出しの引数・結果・終了コードを変えないため。`land_approved` の検査の確かめは実行中と失敗を分けず、1 と 75 を分けられない |
| trunk は承認した head と PR の今の head の完全な一致だけで入れる。分かれた経路は、承認したコミットから比べて許容に収まった先端を入れる | trunk の承認は版上げの PR の head そのものを対象にするため、承認の後の差は必ず承認の外である。分かれた経路の承認は開発版の時点のコミットを対象にし、本番のプランの版上げ・CHANGELOG が必ず後に積まれるため、許容の比べ方を承認の延長として扱う |
| `queued` では公開せず、打ち直しで公開する | merge queue へ入れた PR は合成のブランチの検査が通った後に入る。1 回目の中で待つと、CI の待ちと合わせて上限が 2 倍になる |
| `queued` と `human` を同じ公開の欠け（6）で返し、新しい終了コードを足さない | CD のある本番のプランは 10 を「承認がまだ無い」として扱っており、承認ゲートの範囲の値を足すと経路ごとに扱いが分かれる |
| trunk の `release --channel prod` は「PR を開く → 承認を照らす → 取り込む」の順で動く | 1 つのコマンドが承認の有無で止まる形を CD のある本番が既に持つ。開く用と入れる用にコマンドを分けると、打ち直しの説明が経路ごとに分かれる |
| 系列の無い本番のプランの版はキューが origin から決める | trunk には開発版のチャネルが無く、接尾辞つきの版は出す先が無い |
| CD の無い trunk の承認資料は本番のプラン自身が作り、承認は手元の承認資料の記録で取る | CD の有無から決まるリリースの場合（`release_case`）はほかの判断にも使われ、承認の場所だけを取り込み方で変えると同じ名前が 2 つの意味を持つ |
| 承認前の試用は CD の有無によらず、release の 1 回目の後・承認資料の前に置く | trunk には開発版のチャネルが無く、試せる場所は版上げの PR の head だけである |
| 迂回の記録は `land_approved` だけが書く | CD の経路が取り込みの後に書くと同じ記録が 2 回書かれる |
| `new close`（スプリントの終わりの計画）にも同じ判定を当てる | 同じ計画の組み立てを使うため、外さないと trunk のスプリントの終わりが 3 で止まる |

`merge-when-green` に主体と方式の分岐を足す形と、`gh pr merge --auto` を直接打つ形は採らない。主体・方式・履歴の作り方・
ruleset との照合・迂回の記録・早送りは `land_approved` が持ち、2 か所に持つと宣言の読み方が分かれるためである。

## 対象範囲

- 含む: `release-steps.py release --channel prod` の本番ブランチへのマージ、trunk の宣言の `supervise.py new sprint`・`new close` の計画、
  系列の無い本番のプランの版の決め方、承認資料の欄の名前
- 含まない: ベースブランチへ入れるマージ（開発版の PR・`-sync` の PR・戻すマージ）は `wait_and_merge` のまま。`release-steps.py land` の
  単独の呼び出しの振る舞い。管理者の迂回の判定。11.0（`plugins/ndf-next/`）

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | 本番ブランチへ入れるマージは、下の「本番ブランチへ入れる経路」の 4 か所だけで、すべて `prod_land.land` を通って `land_approved` に入る。本番ブランチ宛ての PR に `wait_and_merge` を使わない | 該当の経路のテストが落ちる |
| I2 | trunk の経路では、本番ブランチへ入る head は承認の SHA と完全に一致する。マージ済みからの再開でも、承認の SHA とマージされた版上げの PR の head（`headRefOid`）の一致で確かめ、マージコミットとの祖先の判定は使わない。履歴の作り方が squash・rebase でも、承認した head をマージしていれば公開へ進む | 承認ゲート（10）で止まり、承認の取り直しを求める。マージ済みで head が違えば 1 で止まり公開しない |
| I3 | 分かれた CD の無い経路では、本番ブランチへ入る head は比べて許容に収まった先端（`verdict.tip`）と同じ | 先端が動けば比べ直し、`HEAD_MOVED_RETRIES` 回を超えたら 10 で止まる |
| I4 | 承認した head の検査がそろうまで入れない | 中身の失敗は 1、上限までの基盤の待ちは 75。どちらも本番ブランチを変えない |
| I5 | 取り込みの結果が `queued`・`human` のとき、タグと GitHub Release を作らない | 公開の欠け（6）で返し、打ち直しでマージ済みを見つけてから公開する |
| I6 | trunk の宣言では、どの進め方（normal・fast・auto）の計画にも、`new close` の計画にも、「版」の「チャネル」が `dev` のプランが無い | 計画のテストと契約のコピーが落ちる |
| I7 | trunk の本番のプランは、版上げの PR を開いた後・承認資料を作る前に、その head から導入を確かめるステップ `trial` を持つ | 計画のテストが落ちる |
| I8 | 系列の無い本番のプランの版は、明示の版か、記録が無いときに origin から決めた版である。一度書いた記録は決め直さない | 版が既に出ていれば流さずに止まる（`taken`） |

### 本番ブランチへ入れる経路

| # | 呼び出し | 経路 |
| --- | --- | --- |
| 1 | `release-steps.py` の `_merge_prod_pr` | 分かれた経路の CD の無い本番 |
| 2 | `release_lib/reviewed.py` の `merge_on_pr_approval`（呼び出し元は `reviewed.release_prod` と `strategy_route._merge_exact`） | CD のある本番（trunk・分かれた経路・選んで集める取り込み方） |
| 3 | `release_lib/strategy_route.py` の `_merge_approved` | 選んで集める取り込み方の CD の無い本番 |
| 4 | `release_lib/trunk_release.py` の `release_trunk` | CD の無い trunk の本番 |

CD のある経路は操作の集まり `ProdOps` の欄 `land` を、選んで集める取り込み方は `ops.land` を通して `prod_land.land` を呼ぶ。

### 本番の取り込みの判定（`prod_land.land`）

merge queue の状態は GitHub の GraphQL API の PR の項目 `merged`・`state`・`isInMergeQueue` を 1 回の照会で読み、
`merged`・`queued`・`open`・`closed` の 4 値に写す（`queue_state`）。読めなければ止まり、merge queue に無いとはみなさない
（merge queue に残る PR を二重に入れないため）。判定は上から順に当てる。

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | `queued` | `--ci-wait` を上限に読み直す（`wait_queued`）。上限まで `queued` なら 75。抜けた状態で 2 以降を当てる |
| 2 | `merged` | マージされた PR の head が承認した head と同じことを確かめ、マージコミットを返す。違えば 1 で止まる |
| 3 | `closed` | 1 で止まる |
| 4 | `open` | 承認の証跡のチェック（`ndf/release-approval`）を除く検査を待ち、証跡のチェックを待つ |
| 5 | PR の今の head が承認した head と違う | `HeadMoved` を呼び手へ返す（trunk・CD のある経路は 10、分かれた経路は比べ直し） |
| 6 | `land_approved` が `merged` | マージコミットを返す。ruleset の無い `strict` の早送りは PR を閉じるだけなので、本番ブランチの取り込みの記録から引く |
| 7 | `land_approved` が `queued`・`human` | `LandPending` を呼び手へ返す |
| 8 | `land_approved` が `stopped` | 理由を添えて 1 で止まる |

### trunk の本番（`release_lib/trunk_release.py` の `release_trunk`）

`cmd_release` は、チャネル `prod`・取り込み方が `sync` を持たない・CD 無し・単位を選ばない取り込み方のとき、承認を読む前に
`release_trunk` へ回す。承認したコミットは `--approved-sha`、無ければ `--approval` の承認資料の承認の記録から読む。

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | 版上げの PR がマージ済み。または閉じていて、本番ブランチに取り込みの記録（head が承認の SHA・tree が一致・最初の親が先端）がある | 承認の記録が無い、または PR の head が承認の SHA と違えば 1。同じなら `already_merged` の行で公開へ渡す |
| 2 | タグか本番ブランチの版が既にある | 1 |
| 3 | 開いた版上げの PR を使い回すか作る | 4 へ |
| 4 | 承認が無いか、承認の SHA が PR の head と違う | 検査を待ってから承認ゲート（10）。`next` に承認資料の作り直しと承認の取り直し |
| 5 | 承認の SHA が PR の head と同じ | 本番の取り込みへ渡す。`HeadMoved` なら今の head を添えて 10 |

### `release-steps.py release --channel prod` の結果

| 場合 | status | 終了コード | 公開 |
| --- | --- | --- | --- |
| 本番ブランチへ入れた（迂回・早送りを含む） | `ok` | 0 | 行う（CD の宣言があれば `metrics.publisher: cd`） |
| merge queue へ入れた | `stopped` | 6 | 行わない。`gaps` に `{"kind": "publish", "name": "<タグ>", "reason": "merge queue のマージを待つ"}`、`next` に打ち直し |
| 人のマージを待つ | `stopped` | 6 | 行わない。reason は「人のマージを待つ」 |
| 打ち直しで merge queue にあり、上限までにマージされない | `stopped` | 75 | 行わない |
| 打ち直しで merge queue から外れていた | 入れ直した結果のとおり | 同左 | 同左 |
| マージ済み（merge queue・人・前の実行） | `ok` | 0 | 無いものだけを行う |
| 承認が無い・承認した head と今の head が違う | `gate` | 10 | 行わない |
| 検査の中身の失敗 / 上限まで基盤の待ち / `land_approved` の `stopped` / PR が閉じている | `stopped` | 1 / 75 / 1 / 1 | 行わない |

打ち直しのコマンド（`prod_land.release_retry_command`）は、開始時の `--plugins`・`--release-strategy`・`--approval` か
`--approved-sha` を引き継ぐ。`release-steps.py land --pr <番号>` の引数・結果 JSON・終了コードは変えない。

### trunk の判定

計画の側は `supervise_lib/decl.py` の `trunk_release(a)` が、計画の引数のブランチ戦略のスナップショットから
「版上げの PR があり、開発版の PR を作らない取り込み方か」を返す。リリースの側は `strategy_route.route_of` の `sync` が偽であることで読む。
どちらも同じ正本（`strategy_route.route_of`）から決まる。スナップショットが無い・壊れていれば `DeclError` で計画を書かずに止まる。

### trunk のスプリントの計画

- `sprint_plans`（normal）は「デプロイ」のステージと、CD の無い場合のリリース承認ゲートのステージを置かない
- `fast_sprint_plans`・`auto_sprint_plans`・`close_waves` は「開発版」のステージを置かない
- 本番のステージは `sprint_routes.trunk_prod_waves` が組む。本番のプランへは系列を渡さず、fast・auto では試用のコマンドを渡す（`sprint_waves._release_fields`）

### 本番のプランの種類とステップ

`supervise_lib/release_templates.py` の `plan_release_package_plugin` が本番の種類を決める。どの種類も release の 6 は
`release-landed`（同じコマンドを打ち直し、そこでの 6 は止める）へ進む。

| CD | trunk | MVV 判定 | 種類 | release の 10 の行き先 |
| --- | --- | --- | --- | --- |
| 無し | 偽 | 無し | `prod` | `gate_next: end` |
| 無し | 偽 | あり | `prod-mvv` | `gate_next: handoff` |
| 無し | 真 | 無し | `prod-trunk` | `gate_next: trial` |
| 無し | 真 | あり | `prod-trunk` | `on_exit` の `"10": "trial"` |
| あり | 偽 | 問わない | `prod-cd` | `facts` |
| あり | 真 | 問わない | `prod-cd` | `trial`（`release_cd.cd_prod_steps` の先頭） |

種類 `prod-trunk` のステップの並びは次のとおりである。

```text
bump → bump-others → changelog → notes → snapshot →（sync）→ release → trial → facts → explain → others →（MVV 判定: mvv → note）→ approve → release
```

- `trial`（`supervise_lib/release_trunk.py` の `trial_step`）: normal は `verify-install --ref release/v<版> --expect <版> --runtimes <並び>`。
  fast・auto は `.ndf/pace.json` の `verify` の `{ref}` を `release/v{release_version}` へ計画を組む時点で置き換え、版の差し込みはキューが埋める。失敗すれば `judge` へ進み、承認資料を作らない
- facts・others は `--head release/v<版>`、explain は `notes --approval <承認資料> --verified <並び> --ref release/v<版>` で、承認資料の欄「承認前の試用で確かめたこと」に ref・版・結果を書く。bump の後の notes は `--approval` を渡さず、この欄を書かない
- approve は `--facts-from {state_dir}` で自分のプランの facts の出力から承認した SHA を読み、`next: release` へ進む

### リリース承認ゲートでの止まり方（CD の無い trunk）

計画にリリース承認ゲートのステージを置かず、本番のプランの結果を「承認ゲート」にして止める。新しい終了コードや状態は足さない。

| 進め方 | 止まり方 |
| --- | --- |
| normal | release の 1 回目の 10 を `gate_next: trial` で受け、実行の基盤（`supervise_lib/engine.py`）が承認ゲートとして記録する。others の `next: end` で完了しても結果は「承認ゲート」、キューの結果は `gate` で、後続のステージは流れない。承認の後は `run <本番のプラン> --from approve` で続ける |
| fast・auto | 10 を `on_exit` で trial へ送り、others の後の MVV 判定が通せば approve → release へ進む。通さなければ MVV 判定が承認ゲートを返す |

## データ・設定

| 名前 | 置き場 | 値 |
| --- | --- | --- |
| `TRIAL_ROW` | `release_lib/materials.py` | `承認前の試用で確かめたこと`（承認資料の欄の名前。`cmd_approval_facts` もここから引く） |
| `PENDING_REASONS` | `release_lib/prod_land.py` | `queued` → 「merge queue のマージを待つ」、`human` → 「人のマージを待つ」 |
| `RELEASE_KINDS["prod-trunk"]` | `supervise_lib/release_templates.py` | 承認の手順 `trial`・facts の並び・`approve`、手順の組み立て `_trunk_prod_approvals`、承認ゲート無し |
| merge queue の待ちの間隔 | 方針 [`release-steps.ci-wait`](https://github.com/devbasex/ai-plugins/blob/86a28c3c08cda2f5ebdb3de18934c3951dcac2e8/plugins/ndf/contracts/limits/release-steps.ci-wait.json) の `interval`（NDF 11.x のコードはこの値を使わないため、このファイルは削除した。リンクは削除前のもの） | 15 秒。上限は `--ci-wait` |
| 系列の無い本番の版 | `lib/release_version.py` の `decide_release` | origin の最新のタグ・本番ブランチの版・ベースブランチの版の基底のうち最も大きいものを上げ幅で上げた正式版。記録の `how` は `decided` |

`supervise_lib/queue_fill.py` の `_fill_prod` は、「版」の欄の明示も系列も無いとき `decide_release` で版を決め、本番のプランの状態ディレクトリの
`version.json` へ書く。記録があれば決め直さない。origin を読めなければ `unreadable`、決めた版が出ていれば `taken` で止まる。

## 外部連携

| 相手 | 使うもの |
| --- | --- |
| GitHub GraphQL API | PR の `merged`・`state`・`isInMergeQueue`（`prod_land.PR_QUEUE_QUERY`） |
| GitHub merge queue | `land_approved` が承認した head（`expectedHeadOid`）を固定してキューへ入れる。取り込み方の食い違いは `land_approved` の `ruleset_mismatch` で止まる |

## セキュリティ

- 承認した head の外のコミットを本番ブランチへ入れない（I2・I3）。merge queue へ入れる前にも `land_approved` が同じ照合をする
- 管理者の迂回は `land_approved` の判定（`merge.method: direct` と `merge.admin: true`）だけで決まる

## 運用

- 同じ版の `release` を 2 つの実行が打っても新しい排他は取らない。2 回目は `queue_state` が `queued` を返して待ちへ入り、二重に入れない
- 止まったときの結果 JSON は、公開の欠け（6）・承認ゲート（10）・基盤の待ち（75）のどれも `next` に打ち直しのコマンドを載せる
- 未確認: merge queue がマージした後の PR の REST の `merged`、merge queue に入っている PR の `isInMergeQueue` の真の値、merge queue での
  `queued` から公開までの所要は、照会の時点で merge queue が空だったため観測できていない。trunk・merge queue の宣言での最初の正式版のリリース後テストで確かめる

## テスト観点

- 宣言 `merge-queue` で release を打つと、キューへの投入が 1 回・直接のマージが 0 回・`queued` の行・終了コード 6・タグを作る呼び出しが 0 回であること
- マージ済みにして打ち直すと公開へ進み、CD の宣言があれば `metrics.publisher` が `cd` であること
- `merge.actor: human` でマージもキューへの投入もせず 6 で止まり、マージ済みにして打ち直すと公開へ進むこと
- `strict`・`direct` と `merge.commit` の 3 値で、マージに渡る履歴の作り方が宣言と同じであること
- 検査が実行中なら待ってから入れ、失敗で 1・上限で 75、どちらもマージ・キューへの投入が 0 回であること
- 承認の後に head が動いたとき、trunk は 10 でマージ 0 回・キューへの投入 0 回、分かれた経路は回数を超えると 10 であること
- 4 つの経路のそれぞれで本番ブランチ宛てのマージが `land_approved` を 1 回呼び、`wait_and_merge` を呼ばないこと
- 打ち直しで `queue_state` が `queued` なら待ち、`open` なら `land_approved` を打ち直し、`closed` なら 1 であること
- trunk の宣言で `new sprint`（normal・fast・auto）と `new close` の計画にチャネル `dev` のプランが 0 件であること
- `--version` を渡さずに版の差し込みを埋めると止まらずに版が決まり、2 回目は記録の版を使うこと
- trunk の本番のプランで release の 10 の行き先が trial、trial が facts の前で、fast・auto のコマンドに `{ref}` が残らないこと
- 承認資料の欄に `release/v<版>` の ref・版・結果が載り、bump の後の notes が `--approval` を持たないこと
- normal の trunk の本番のプランが others の後に「承認ゲート」で終わって approve を打たず、`--from approve` で続けると取り込みへ進むこと
- 分かれた宣言の計画に開発版のプランが残り、開発版の PR と `-sync` の PR のマージが `wait_and_merge` を呼ぶこと
- `release-steps.py land` の既存のテストが変更なしで通ること

テストは `tests/test_release_steps.py`・`test_release_cd.py`・`test_release_selected.py`・`test_release_production_flow.py`・
`test_release_version.py`・`test_release_materials.py`・`test_supervise_new.py`・`test_supervise_contract.py` と、
契約のコピー `tests/fixtures/supervise-contract/plans/` にある。

## 関連リンク

- [ndf-release-via-cd.md](ndf-release-via-cd.md) （CD のある本番）
- [ndf-release-production-admin-merge.md](ndf-release-production-admin-merge.md) （管理者の迂回）
- [ndf-release-approved-commit.md](ndf-release-approved-commit.md) （承認したコミットと許容の比べ方）
- [ndf-release-version-at-run-time.md](ndf-release-version-at-run-time.md) （キューが流す時点の版の決め方）
- [ndf-branch-flow-declaration.md](ndf-branch-flow-declaration.md) （旧いブランチ運用の宣言と、ブランチ戦略の宣言への置き換え）
