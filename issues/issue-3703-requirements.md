# #3703: ブランチ戦略: 本番リリースのマージを land へつなぎ、trunk の計画から開発版のステージを外す（#3672 の手順 1 で保留した 2 件）

正は課題の本文（#3703）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 時期は、ai-plugins の移行の手順 2（移行の PR）の後、手順 4 より前 | `issues/issue-3672-design-ai-plugins.md` の移行の順序 | 前提 4 |
| S2 | CD のある trunk では開発版のステージを外し、本番のプランの承認資料の材料を集めるステップ（facts）の前に `verify-install --ref release/v<版>` を入れる | 課題の本文の「決めること 2」の案（#3672 の決定 17 から推した組み立て） | 受け入れ条件 6 の形の 1 つとして設計で採否を決める。受け入れ条件はステップの名前と位置を固定しない |
| S3 | 本番リリースのマージを `land_approved` へつなぐのは、trunk だけでなく本番ブランチへ入れるすべての経路に当たる | 課題の本文の「今は 10 系の本番リリースが `wait_and_merge` で入り」と受け入れ条件「本番リリースのマージが宣言の `merge` の主体・方式に従う」に経路の限定が無いこと | 前提 1 |

## 目的

- ブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）で選んだマージの主体と方式（`merge.actor`・`merge.method`・`merge.commit`）どおりに、本番リリースのマージが行われる状態にする
- trunk の宣言（ベースブランチと本番ブランチが同じ）のリポジトリで、スプリントの計画が使えない開発版のステージを作らず、承認前の試用（版上げの PR の head からの導入の確認。#3672 の決定 15・17）を通って本番まで流れる状態にする
- ai-plugins は 2026-11-17 の時点で宣言が trunk・`merge.method: merge-queue` に移っており（`.ndf/project.json`）、この 2 件が無いと、スプリントの計画が開発版のプランで止まり、本番リリースが merge queue を通らない

## 背景

#3672 の移行の手順 1（実装の PR https://github.com/devbasex/ai-plugins/pull/3701 ）の cross-review で、major の指摘が 2 件保留になった。管理者の迂回を宣言から決める部分は手順 1 で直した（コミット 6a2606fc6）。残りの 2 件は設計（`issues/issue-3672-design-detail.md` ）に組み立てが無いため、この課題で設計を足してから実装する。

指摘への返信: https://github.com/devbasex/ai-plugins/pull/3701#issuecomment-6914891693

今の振る舞い（2026-11-17、コミット 45731015e で確かめた）:

| 箇所 | 今の振る舞い | 根拠 |
| --- | --- | --- |
| 本番リリースのマージ（CD の無い場合） | `release-steps.py` の `_merge_prod_pr` が `wait_and_merge`（`merged-steps.py merge-when-green`）でマージする。宣言の `merge` を読まない | `plugins/ndf/scripts/release-steps.py:657` |
| 本番リリースのマージ（CD のある場合） | `release_lib/reviewed.py` の `release_prod` が `ProdOps.wait_and_merge` でマージする | `plugins/ndf/scripts/release_lib/reviewed.py:99` |
| 宣言どおりに入れる関数 | `release_lib/land.py` の `land_approved` は `release-steps.py land` からだけ呼ばれ、CI を待たない | `plugins/ndf/scripts/release_lib/land.py:154`・`release-steps.py:848` |
| trunk の開発版 | `cmd_release` は trunk で `--channel dev` を受けると前提の不足（終了コード 3）で止まる | `plugins/ndf/scripts/release-steps.py:672` |
| trunk のスプリントの計画 | `sprint_plans` は trunk でも「デプロイ」のステージに開発版のプラン（チャネル `dev`）を置く。契約の写し `tests/fixtures/supervise-contract/plans/sprint/8-release.json` の「版」の「チャネル」が `dev` | `plugins/ndf/scripts/supervise_lib/sprint.py:307` |
| 本番の版 | 本番のプランは明示の版か、系列（`release`）の開発版のプランの版の記録の base で埋める。どちらも無いと止まる | `plugins/ndf/scripts/supervise_lib/queue_fill.py:398` |
| 試用の ref | `.ndf/pace.json` の `fast.verify`・`auto.verify` は `--ref {ref}` を持つが、`{ref}` を置き換える処理は 10 系に無い | `grep -rn '{ref}' plugins/ndf/scripts/supervise_lib` で当たるのは `release_templates.py:300` の f 文字列だけ（置き換えではない） |
| 導入の確認 | `verify-install --ref` は本番ブランチ宛ての開いた版上げの PR の head（`release/v<版>`）を受ける | `plugins/ndf/scripts/release-verification-steps.py:237` |

## 前提

- 前提 1: 「本番リリースのマージ」は、本番ブランチへ入れるマージ（trunk の版上げの PR、ベースブランチと本番ブランチが分かれた宣言の本番の PR、CD のある場合の本番の PR）のすべてを指す。ベースブランチへ入れるマージ（開発版の PR と、版数の衝突を解く `-sync` の PR）は含まず、今の `wait_and_merge` のまま
- 前提 2: 承認の証跡と照らす head は、`land_approved` の決まりどおり承認した head と PR の今の head の一致で判定する（#3672 の I5）。承認の後に先端が動いたときの扱いは設計で決め、承認の外のコミットを本番ブランチへ入れないことだけを受け入れ条件で固定する
- 前提 3: 管理者の迂回の判定（`merge.admin`）は #3672 の手順 1 で直したものを使い、この課題で変えない
- 前提 4: ai-plugins では、この課題の変更が手順 4 より前に main へ入る。手順 1 は旧い NDF の develop→main の出し方で出したため、この課題の影響を受けない
- 前提 5: 11.0（`plugins/ndf-next/`）の同じ振る舞いは #3681 が扱い、この課題の変更は 10 系（`plugins/ndf/`）だけに入れる

## 対象範囲

含む:
- 本番リリース（`release-steps.py release --channel prod`）で本番ブランチへ入れるマージを、宣言の `merge` に従う取り込み（`land_approved`）へつなぐこと。CI の待ち・先端が動いたとき・キューへ入れた（`queued`）ときの公開の時期を含む
- trunk の宣言で、`supervise.py new sprint` が開発版のステージを作らずに本番まで組むこと。版上げの PR を開いて止める手段・本番の版の決め方・承認前の試用・CD の無い trunk の承認資料を作る主体を含む
- 上の 2 つの組み立てを書いた設計文書（`issues/issue-3703-design*.md`）
- 変えた振る舞いを確かめるテストと、契約の写し（`tests/fixtures/supervise-contract/`）の更新

含まない:
- 11.0（`plugins/ndf-next/`。#3681）
- 管理者の迂回の判定（#3672 の手順 1 で直した）
- ベースブランチへ入れるマージ（前提 1）
- `release-steps.py land` の単独の呼び出しの振る舞い（受ける引数と結果の形を変えない）
- 開発ループ（`--loop`）と relay の `--dev` の扱い（#3672 の決定 15 の残り）
- ai-plugins の `.ndf/` の宣言の値の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | スプリントの計画を作った | 人か conductor が `supervise.py new sprint` を打つ | 宣言が読めなければ止まり、計画を書かない | — |
| E2 | 版上げのブランチ（`release/v<版>`）で版を上げ、push した | 本番のプランの bump・changelog のステップ | 版を決められなければ止まる（受け入れ条件 5） | E1。trunk では開発版の版の記録が無い |
| E3 | 版上げの PR を開いて止まった | 本番のプランの release のステップ（承認を得る前の 1 回目） | PR を作れなければ止まる。打ち直すと開いた PR を使い回す | E2 |
| E4 | 版上げの PR の head から導入を確かめた（承認前の試用） | E3 の後のステップ | 導入に失敗したら承認資料へ進まずに止まる | E3（PR が開いていないと `verify-install` が ref を受けない） |
| E5 | 承認資料を作った | E4 の後のステップ（CD の無い trunk も同じ） | 作れなければリリース承認ゲートへ進まない | E4 |
| E6 | リリースを承認した（証跡が PR の head にある） | 人の承認、または `pace: auto`・`fast` の MVV 判定 | 差し戻しなら E7 へ進まない | E5 |
| E7 | 承認した head の CI がそろった | 本番のプランの release のステップ（2 回目）の CI の待ち | 中身の失敗は 1、CI の基盤の待ちは 75 で止まる | E6 |
| E8 | 宣言の主体と方式で本番ブランチへ入れた（`merged`・`queued`・`human`・`stopped`） | `land_approved` | `stopped` は理由を添えて止まる。`human` は人のマージを待って止まる | E7 |
| E9 | キューか人が版上げの PR をマージした | merge queue・人の操作 | キューから外れたら公開へ進まない | E8 が `queued`・`human` |
| E10 | タグと GitHub Release を作った | CD（宣言に `release.cd` があるとき）か NDF | 作れなければ止まり、打ち直すと無いものだけを作る | E8 が `merged`、または E9 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 版上げの PR | 版上げのブランチ（`release/v<版>`）から本番ブランチへの Pull Request。trunk では本番の PR を兼ねる |
| 承認前の試用 | 本番へ届く前に、変更を入れて使えるところで確かめること（#3672 の決定 15）。trunk の `plugin` の宣言では版上げの PR の head からの導入の確認 |

## 受け入れ条件

本番リリースのマージ（前提 1 の範囲）:

- [ ] 1. 前提: 宣言が `merge.actor: ndf`・`merge.method: merge-queue`、承認の証跡が版上げの PR の今の head にあり、その head の検査がすべて成功している
      操作: `release-steps.py release --channel prod` を打つ
      結果: PR をマージキューへ入れ、PR を直接マージしない。結果 JSON に `queued` の行が載り、タグと GitHub Release をまだ作らない
- [ ] 2. 前提: 1 で `queued` になった後、キューが PR をマージした
      操作: 同じ `release-steps.py release --channel prod` を打ち直す
      結果: マージ済みの PR を見つけて公開へ進み、宣言に `release.cd` があればタグと GitHub Release を NDF が作らず（`metrics.publisher` が `cd`）、無ければ NDF が作る
- [ ] 3. 前提: 宣言が `merge.actor: human`
      結果: NDF はマージもキューへの投入もしない。人のマージを待つ理由を添えて、終了コード 0 以外で止まる。人がマージした後に打ち直すと公開へ進む
- [ ] 4. 前提: 宣言が `merge.actor: ndf` で `merge.method` が `strict` か `direct`
      結果: 宣言の `merge.commit`（`merge`・`squash`・`rebase`）の方式で、承認した head を本番ブランチへ入れる
- [ ] 5. 承認した head の検査が実行中なら、`--ci-wait` の秒数を上限に待ってから入れる。検査の中身が失敗したら終了コード 1、上限まで基盤の待ちが続いたら 75 で止まり、どちらも本番ブランチを変えない
- [ ] 6. 承認の後に版上げのブランチへ承認していないコミットが積まれたとき、そのコミットを本番ブランチへ入れない（キューへも入れない）。止まるときは承認ゲート（終了コード 10）で、承認を取り直す依頼を出す
- [ ] 7. CD のある場合（`release_lib/reviewed.py` の `release_prod`）と CD の無い場合のどちらの経路でも、本番ブランチへ入れるマージに `wait_and_merge` を使わない。テストで両方の経路を通し、マージの呼び出しが `land_approved` を経ることを確かめる

trunk のスプリントの計画:

- [ ] 8. 前提: trunk の宣言（`branches.base` と `branches.production` が同じ）
      操作: `supervise.py new sprint` を `--pace normal`・`fast`・`auto` で打つ
      結果: どの進め方でも、書いた計画の中に「版」の「チャネル」が `dev` のプランが 0 件
- [ ] 9. 8 の計画で、本番のプランは開発版のプランの版の記録を読まずに版を決める。`--version` を渡さないときも、キューが本番のプランを流す時点に版の差し込みを埋められずに止まることが無い
- [ ] 10. 8 の計画で、本番のプランは版上げの PR を開いた後、承認資料を作る前に、その PR の head（`release/v<版>`）から `verify-install` で導入を確かめるステップを持つ（`pace: fast`・`auto` では `.ndf/pace.json` の `verify` の `{ref}` をその head のブランチ名へ置き換える）
- [ ] 11. 8 の計画で、CD の有無によらず、リリース承認ゲートより前に承認資料（`issues/approval-<plugin>-v<版>.md`）を書くステップがあり、承認資料に「承認前の試用で確かめたこと」として ref・版・結果が載る
- [ ] 12. 8 の計画の本番のプランは、承認を得る前の 1 回目に版上げの PR を開いて止まり、承認の後に打ち直すと受け入れ条件 1〜6 の取り込みへ進む

退行しないこと:

- [ ] 13. ベースブランチと本番ブランチが分かれた宣言（`release_strategy` が trunk 以外）で、`supervise.py new sprint` の計画に今までどおり開発版のプランが残り、開発版の PR のベースブランチへのマージは今までどおり `wait_and_merge` で入る
- [ ] 14. `release-steps.py land --pr <番号>` の引数・結果 JSON の形・終了コードが変わらない
- [ ] 15. 変更の範囲のテスト（`release-steps.py`・`release_lib/`・`supervise_lib/` のテストと契約の写し）がすべて通る

設計文書:

- [ ] 16. 設計文書に、課題の「決めること」の 7 つの問い（CI の待ちの置き場・先端が動いたときの入れ直し・`queued` のときの公開の時期・版上げの PR を開いて止める手段・開発版のプランが無いときの本番の版・CD の無い trunk の承認資料を作る主体・起票者の案 S2 の採否）それぞれに、採った答えと理由が書かれている

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 止まったときの結果 JSON に、止まった理由と次に打つコマンドが載る（受け入れ条件 3・5・6）。本番ブランチへ入れるマージの経路が `land_approved` の 1 か所になる |
| 移行性 | ai-plugins の宣言（`.ndf/project.json`）と `.ndf/pace.json` は書き換えずに、この変更を入れた NDF で本番まで流れる |
| セキュリティ | 承認した head の外のコミットを本番ブランチへ入れない（受け入れ条件 6）。必ず人の承認が要る操作（C1〜C8・P1・P2）の扱いを変えない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`release-steps.py release --channel prod` の結果に `queued`・`human` の待ちが加わる。trunk の `supervise.py new sprint` の計画のステージの並びが変わる（開発版のプランが無くなる）。後方互換の経路は持たない（普及のフェーズまで後方互換を求めない。MVV の Value 4） |
| データ | 変わらない |
| 既存の振る舞い | 本番ブランチへのマージが merge queue・人のマージ・宣言の方式に従う。trunk のスプリントの計画からデプロイ（開発版）のステージが外れる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4 -k "release or land or supervise or sprint"`（範囲のテスト。全体は CI） |
| 静的解析・型検査 | `uv run --frozen --project . ruff check plugins/ndf/scripts`・CI の型検査 |
| 契約の写し | `tests/fixtures/supervise-contract/plans/sprint/` の trunk の計画に、チャネル `dev` のプランが無いことをテストで確かめる |
| 手動確認 | ai-plugins の次の正式版のリリースで、版上げの PR が merge queue を通って main へ入り、CD がタグと GitHub Release を作ることをリリース後テストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 10 系の変更は `plugins/ndf/scripts/` の下に置く。11.0（`plugins/ndf-next/`）は触らない。設計文書は `issues/issue-3703-design*.md` |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストを書かない |
| テスト戦略 | `land_approved` への接続は偽の Port・偽の gh で単体テストにする。計画の組み立ては `supervise.py new sprint` の結果（計画のファイル）を読むテストと契約の写しで確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 変更の範囲のテストの実行、契約の写しの更新、設計文書への決定と理由の記録 |
| 確認してから行う | `release-steps.py release` の結果 JSON の形の変更、計画のステージの並びの変更（設計承認ゲート・リリース承認ゲートで人が見る） |
| 行わない | 11.0 の変更、`.ndf/` の宣言の書き換え、管理者の迂回の判定の変更、ベースブランチへ入れるマージの変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| CI の待ちを `cmd_release` と `land_approved` のどちらの前に置くか（`land_approved` は待たない） | 設計 | 設計承認ゲート |
| 承認の後に先端が動いたとき、許容リスト（#2221。版上げの外のコミットでないもの）で入れ直すか、常に承認を取り直すか | 設計 | 設計承認ゲート |
| `queued` のとき、公開を打ち直しで行うか、キューのマージを待ってから行うか | 設計 | 設計承認ゲート |
| trunk の本番のプランで、版上げの PR を開いて止める手段（今は CD のある場合の本番のプランだけが開いて止まれる） | 設計 | 設計承認ゲート |
| 開発版のプランが無いときの本番の版の決め方（`queue_fill` の本番の差し込みを変えるか、本番のプランが開発版と同じ決め方を持つか） | 設計 | 設計承認ゲート |
| CD の無い trunk で承認資料を作る主体 | 設計 | 設計承認ゲート |

## 依頼（原文）

> 本番リリースのマージと trunk のスプリントの計画を、#3672 の宣言（`branch_strategy` の `merge`・`flow`）どおりに動かす。今は 10 系の本番リリースが `wait_and_merge` で入り、trunk の宣言でも開発版のステージを作る。宣言で trunk と merge の主体・方式を選んだリポジトリに効く。

> #3672 の移行の手順 1（実装の PR https://github.com/devbasex/ai-plugins/pull/3701 ）の cross-review で、major の指摘が 2 件保留になった。管理者の迂回を宣言から決める部分は手順 1 で直した（コミット 6a2606fc6）。残りは設計（`issues/issue-3672-design-detail.md` ）に組み立てが無いため、設計を足してから実装する。

> ### 1. 本番リリースのマージを `land_approved` へつなぐ（`plugins/ndf/scripts/release-steps.py` の `cmd_release`）
>
> `merge.actor: human`・`merge.commit`・strict と merge-queue の入れ方を宣言どおりにするには、`wait_and_merge` でなく `land_approved` を通す必要がある。設計の関数表には `cmd_release` から land への接続が無い。
>
> - CI の待ちをどこで持つか（`land_approved` は待たない）
> - 先端が動いたとき、許容リストで入れ直すか
> - merge-queue で `queued` を返したとき、公開（タグ・GitHub Release）をいつ行うか
>
> ### 2. trunk で開発版のステージを作らない計画（`plugins/ndf/scripts/supervise_lib/sprint.py` の 307 行付近）
>
> 決定 15・17 の試用のステージの具体の組み立てが設計に無い。
>
> - 版上げの PR を開いて止める手段（今は CD のある場合の本番のプランだけが開いて止まれる）
> - 開発版のプランが無いとき、本番の版をどう決めるか（`queue_fill` は開発版の系列か明示の版を求める）
> - CD の無い trunk で、承認資料をだれが作るか
> - 案: CD のある trunk では開発版のステージを外し、本番のプランの facts の前に `verify-install --ref release/v<版>` を入れる

> - 上の 2 つの組み立てが設計文書に書かれ、実装とテストが入る
> - trunk の宣言で `supervise.py new sprint` が開発版のプランを作らない
> - 本番リリースのマージが宣言の `merge` の主体・方式に従う

> ai-plugins では手順 2（移行の PR）の後、手順 4 より前に要る。手順 1 は旧い NDF の develop→main の出し方で出すため、この 2 件の影響を受けない。

（書き手: 起票者（PR #3701 の cross-review の保留を起票した AI）、日付: 2026-11-16。課題の題と、本文の目的・背景・決めること・受け入れ条件・時期の節。利用者の指示の原文は含まない）

> #3703。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示（スプリント sprint-lbase-guard-1 の設計のステップ）、日付: 2026-11-17）
