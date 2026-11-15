# #3572 の決定の記録: GitHub flow を土台にし、承認した先端だけを main へ入れ、承認の後に直しのステップを持たない

設計文書 [issue-3572-design.md](issue-3572-design.md) の決定の記録である。不変条件（I1〜I13）・外部の調査（STD1〜STD6・PRD1・PRD2・LIB1・LIB2）・
用語は、設計文書の同じ名前の章の意味で使う。

## 決定の記録

### 決定 1: 承認の外のコミットが積まれる経路を無くすため、本番のプランから worker を外し、副命令を並べた 4 ステップにする

本番のプランの `work`（`{{worker}} release`）は、worker にリリースの作業全体を任せるため、CI が落ちたときにコードを直して push する
判断も worker に残る。リリース 8・14 はこの裁量で起きた。ステップを `ndf release-steps` の `merge` → `confirm` → `record` → `cleanup` だけにすれば、
承認の後にコードを変える主体がプランの中に居なくなる。`confirm` は CD の版上げとタグを待って確かめるだけで、コードもブランチも変えない。
要求の受け入れ条件 7 と E10・E11 の順（CD の確かめの後に後片付けと終わりの記録）に合わせ、`record` と `cleanup` を `confirm` の後に置く。
手順書だけで止める形は 2 回とも止まらなかったため採らない（要求の「含まない」）。
GitHub の ruleset でブランチをロックする形は、利用者の「ロックの方針」で採らない。承認した先端の外は先端の照合（STD1）が止める。
承認するのは本番の PR の先端で、マージは先端の照合つき（`--match-head-commit` に当たる `expect_head`）で打つ。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD1 / STD3

根拠: Value 3 / Value 7（MVV 版 7）

### 決定 2: 本番の PR を直しのステップのあるプランでマージしないため、`merge` の部品の直前に `release-guard` を置き、雛形の検査で縛る

`release_plan.forbidden(plan)` は、`release: true` のプランについて、worker を起動するステップ・`push` を使うステップ
（`push`・`fix-push`・`ci-fix`・`fix-base`・`base-sync`）・次の版の用意と同期のステップ・最初が `ndf release-steps merge` でないことを返す。
`release: true` でない雛形については、`merge` の部品（`check.json` では `merge-gate`）の直前に `release-guard` が無いことを返す。

GitHub flow ではスプリントの PR が本番の PR を兼ねるため、スプリントの経路の雛形（`merge`・`check`・`close`）も本番の PR を流す。
承認の前の CI の直しはこれらの雛形で行ってよく（承認の対象はその後の先端である）、マージだけをデプロイの工程へ渡せば足りる。
そのため `release-guard` を雛形の先頭ではなくマージの直前に置く。本番の PR なら終了コード 3 を原因 `production_pr` にして `end` へ進め、
結果に「デプロイの工程で流す」を出す。止まった扱いにしないのは、承認の前の工程として正しく終わったためである。

| 比べた候補 | 採否と理由 |
| --- | --- |
| `release-guard` を `merge.json` の先頭に置く | 採らない。スプリントの PR の承認の前の CI の直しが通らなくなり、`check`・`close` の雛形の穴も残る |
| プランを読む核（`core/plan/model.py`）や `ndf ci merge` で同じ判定を強制する | 採らない。核と CI の領域がリリースの語を持ち、リリースのドメインの判定を外へ漏らす |
| 部品 `release-guard` をマージの直前に置き、雛形の検査で全雛形を縛る | 採る |

- 作り方: 自前で作る
- 比べた候補: STD3

根拠: Value 3 / Value 11（MVV 版 7）

### 決定 3: 止まった本番の PR の次の手を `rerun` と `reject` の 2 つにする

`merge` の止まった結果の次の手は次の 2 つで、結果の `routes` に常に 2 つとも出す（受け入れ条件 4）。

| 次の手 | 何をするか | 既定の手（`outputs.suggested`）になる止まった理由 |
| --- | --- | --- |
| `rerun` | 同じ承認・同じ先端のまま `merge` を打ち直す | `infra_wait`・`ci_pending`（チェックの一時的な失敗・待ちの切れ） |
| `reject`（差し戻し） | 本番の PR を開いたまま、直しを同じ PR へ入れ、新しい先端で承認を取り直す | `test_failed`・`check_failed`・`refused` |

`rerun` は止まった理由から機械的に決まるため、オーガナイザーか一時レーンが打ってよい。`reject` の後の直しは承認の前の工程（スプリントの経路）で
入れ、新しい先端で `materials`・`present` から取り直す。本番の PR を閉じて次の版へ回す手は持たない。GitHub flow では版ごとのブランチが無く、
出さないスプリントの PR は開いたまま残せば足りるためである。

- 作り方: 標準に倣う
- 比べた候補: STD3 / PRD1

根拠: Value 1 / Value 7（MVV 版 7）

### 決定 4: 承認ゲートを問い直さずに人のマージを承認として扱うため、検知を工程の承認ゲートの待ちの読み直しに置く

リリース承認ゲートの待ちは、答えの照合のために本番の PR を GitHub から読み直している（`github_observe`）。この読み直しで PR が
マージ済みなら、マージした先端への承認（経路 `github-merge`、答えた者は `merged_by` のログイン）とする。判定は `merge_decide` が持つ。
承認ゲートは `release-steps answer --route github-merge` を呼んで証跡を書かせ、自分は承認ゲートの答えの状態だけを書く。本番のプランは
最初の `merge` でマージ済みの PR から再開し（`land._resume_merged`）、人のマージの記録を行う。`pull_request` の `closed` で動く workflow は、
工程の承認ゲートの答えを書く経路を持たず、待ちを別に解く仕組みが要るため採らない。人のマージは、マージした先端への承認であり、
承認ゲートで別の先端へ与えた承認を引き継がない（受け入れ条件 8）。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD2

根拠: Value 1 / Value 3（MVV 版 7）

### 決定 5: 土台を GitHub flow にするため、取り込み方の表に行 `github-flow` を足して既定にし、develop と常設のリリースブランチを持たない

| 列 | 値 | 意味 |
| --- | --- | --- |
| `promotion` | `direct` | スプリントの PR を本番ブランチへ直接マージする |
| `start` | `["production"]` | スプリントブランチは本番ブランチ（main）から切る |
| `doc_prs` | `["sprint"]` | 設計 PR と確定仕様化の PR はスプリントブランチへ（文書だけで main へマージしてリリースを起こさない） |
| `integrate` | `["release"]` | スプリントブランチは、スプリントの PR のマージ（リリース）で本番ブランチへ入る |
| `keeps_sprint` | `false` | マージした時点でスプリントブランチを残さない |
| `release_from` | `production` | 版上げの PR のブランチは本番ブランチから作る |
| `release_units` | `false` | 集めるスプリントを選ばない（1 つのスプリントの PR が 1 回のリリース） |
| `review_kinds` | `["impl", "sprint", "design", "spec"]` | リリースブランチの PR（`release`）の行を持たない。スプリントの PR が本番の PR を兼ねる |

宣言（`.ndf/worktree.json`）は `base_branch` と `production_branch` を同じ main にする。`promote-whole`・`promote-selected` の 2 行は、
ベースブランチが本番ブランチと違う運用（ベースブランチを丸ごと出す・選んで出す）を表し、ndf-next のリリースは同期の PR を作らない
（決定 10）ため、ndf-next のリリースでは扱わない。ベースブランチを持つリポジトリをどう扱うかは「未確認のまま残ること」に置き、範囲外の課題で決める。

表から 2 行を外す時点は、ai-plugins の宣言を `github-flow` へ書き換える 11.0.0 の入れ替え（#3304、決定 14）にそろえる。
今の ai-plugins の宣言は `promotion: whole`・`base_branch: develop` で、入れ替えまでスプリントの経路（`domains/worktree`）はこの宣言を読む。
取り込み方の表には、表に合わない宣言を既定へ倒さずに止める規則（`branch-flows.json` の `summary`）がある。
そのため、この課題の実装で 2 行を外すと、ndf-next のスプリントの経路が入れ替えまで ai-plugins で止まる。そのため、入れ替えまでの扱いは次のとおりにする。

| 時点 | 取り込み方の表 | ai-plugins の宣言 | ndf-next のリリースの副命令 |
| --- | --- | --- | --- |
| この課題の実装から入れ替えまで | 3 行（`github-flow` を既定に足し、2 行を残す） | `promote-whole`（今のまま） | 宣言の行が `github-flow` でなければ、原因 `precondition`・理由「取り込み方 <行> は ndf-next のリリースで扱わない」で止まる。既定の行へ倒さない。`pr-check` は 0 を返して何も見ない（決定 7） |
| 入れ替え（#3304） | `github-flow` の 1 行 | `github-flow`（決定 14） | 宣言どおりに動く |

入れ替えまでは、ndf-next のリリースの workflow も変数 `NDF_RELEASE_RUNNER` が ndf-next でないため動かない。止まる経路は、手で副命令を打ったときだけ通る。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 宣言を変えず、`promote-whole` のまま `base_branch` を main にする | 採らない。`promote-whole` は文書の PR を `base`（= main）へ入れ、文書だけのマージでリリースが起きる。行の意味（ベースブランチを丸ごと出す）と運用が食い違う |
| 常設のリリースブランチの行（`standing`） | 採らない。利用者の方針（GitHub flow）で、スプリントの宛先を main にする |
| 行 `github-flow` を足して既定にする | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 4 / Value 11 / P1（MVV 版 7）

### 決定 6: main へのマージをそのままリリースにするため、スプリントの PR を本番の PR にし、承認資料をその PR の本文に書く

リリースのためのブランチを組まず、スプリントの PR（main 宛て）がリリース承認ゲートの対象になる。マージ＝リリース承認＝マージ承認である。
`assemble` はブランチと版上げのコミットを作らず、範囲（main の先端..PR の先端）から承認資料を組んで PR の本文の承認資料の節へ書く。
本文の書き換えは先端を動かさないため、承認の対象（PR の先端）を変えない。予定の版は上げ幅の宣言（決定 7）から出し、確定の版は CD が決める。
開発版チャネルの組み立て（`assemble --channel dev`）は、develop が無くなるため外す。

- 作り方: 標準に倣う
- 比べた候補: STD3 / PRD1

根拠: Value 1 / Value 5（MVV 版 7）

### 決定 7: 版数をマージの時点に CD が上げるため、上げ幅をラベルで受け、版上げの PR を照合付きで main へ入れてからタグを作る

main の ruleset（2026-11-15 の API の応答）は、迂回を管理者の役割（`RepositoryRole` 5）だけに、形 `pull_request` で許す。PR を通さない main への
push には迂回が効かず、承認 1 と必須のチェックが掛かる。そのため CD は版上げのコミットを main へ直接 push せず、版上げの PR で入れる。

1. main への push で `bump` が動く。先端の版にタグがあり、範囲（前の版上げのコミットの起点..main の先端。設計文書の「main へのマージの後（CD）」）にリリース対象の変化があれば、上げ幅を決める
2. 上げ幅は、範囲にマージされた PR のラベル（`release:major`・`release:minor`・`release:patch`）の最大で、
   ラベルが無い PR は PATCH とする（要求の起票者の推定 S8 を採る。宣言の付け忘れでマージを止めないため）
3. main の先端から `release/v<版>` を作り、`plugin.json` の `version` と CHANGELOG の版の節（決定 11）だけを変えたコミットを足す。
   版上げの PR は `NDF_RELEASE_TOKEN` で作る（`github.token` で作ると CI が起動しない）
4. `bump_decide` が差分を版数の文字列と CHANGELOG の版の節に限られると判定し、必須のチェックの後に、迂回の宣言で照合付きでマージする
5. 版上げの PR のマージの push で、`bump` は先端の版にタグが無いと読んで版を上げず、`publish` がタグと GitHub Release を作る

同じ版の版上げの PR が開いていれば使い直し（I8）、`concurrency` で workflow を 1 本ずつ流す。版上げの PR が開いている間に別の PR が main へ
入ったときの進め方（版が同じなら同じ PR へ main を取り込み、版が変われば作り直す）と、CD が止まったときの扱いは、設計文書の
「main へのマージの後（CD）」にある。1 つの PR に上げ幅のラベルが 2 つ以上あれば
`pr-check` が PR の時点で落とす。PR の検査（`pr-check`）は宣言の行が `github-flow` のときだけ働き、ほかの行では 0 を返して何も見ない。
11.0.0 の入れ替えまでは、取り込み方の表に `promote-whole` の行が残り（決定 5）、ai-plugins の宣言もその行である。この間は今の NDF の
リリースの PR が版を上げるため、検査を掛けない。

| 比べた候補 | 採否と理由 |
| --- | --- |
| CD が main へ直接 push する | 採らない。ruleset の迂回の形が `pull_request` で、直接 push は断られる。ruleset の書き換えは要求の「含まない」 |
| ruleset の迂回の形を `always` にする | 採らない。ruleset の書き換えに当たる |
| `plugin.json` から `version` を外し、コミットの SHA で更新を検知させる（STD4） | 採らない。版数を読むスクリプト（タグ・CHANGELOG・状態行）がすべて書き換わり、利用者が版で過去へ戻れない |
| release-please-action（LIB1）・semantic-release（LIB2） | 採らない。上げ幅をコミットの書式から読み、このリポジトリの書式と CHANGELOG の形に合わない |
| 版上げの PR を自前の `bump` で作り、迂回の宣言でマージする | 採る |

- 作り方: 標準に倣う・自前で作る
- 比べた候補: PRD1 / PRD2 / LIB1 / LIB2 / STD4 / STD6

根拠: Value 3 / Value 5 / C2 / P1（MVV 版 7）

### 決定 8: 人のマージの記録を承認の証跡と同じ場所で読めるよう、本番の PR のコメントに 1 回だけ書く

承認の証跡と迂回の記録は本番の PR のコメントにあり、リリース記録とリリース後テストもそこから読む。人のマージの記録も同じ PR に置けば、
1 つの PR を読めば承認の経路が分かる。書くのは `land` のマージ済みからの再開の 1 か所で、印のあるコメントがあれば書かない。

- 作り方: 自前で作る
- 比べた候補: STD2

根拠: Value 13 / Value 14（MVV 版 7）

### 決定 9: マージの前に試せるようにするため、main 宛ての開いた PR を集めた検証の ref を workflow が組み、名前の違う 2 つ目のマーケットプレイスがそれを指す

#188（2026-08-31）で main と develop に分けたのは、マージした時点で常用する利用者へ届くのを避け、届ける前に試す場所を持つためである。
GitHub flow ではマージがリリースになるため、試す場所をマージの前へ移す。Claude Code にはリリースチャネルの概念が無く、名前の違う 2 つの
マーケットプレイスが違う ref を指す形を勧めている（STD4）。実物の確かめで、同じリポジトリを違う名前と ref で登録すると両方が残り、
作り直しが更新で入り、正式版の登録が変わらないことを見た（設計文書の「実物の確かめ」）。

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| ref の名前 | `verify/next`（宣言 `.ndf/project.json` の `delivery` の検証の行の `verify.ref`） | 名前で用途が分かり、`release/v` と `sprint/` の名前の規則と重ならない |
| マーケットプレイスの名前 | `ai-plugins-next`（同じ行の `verify.marketplace`） | 1 つの名前に 1 つの登録しか持てない（STD5）。正式版と同じ名前は登録できないことを確かめた |
| 組む主体と時期 | `ndf-next-release.yml` が、main 宛ての PR の作成・更新・閉じ（引き金は `pull_request_target`）と手動の起動で `verify-ref` を打つ | 人が組み直しを忘れない。PR の出来事が中身の変わる時点である。`pull_request` は PR 側の workflow の定義で動き、秘密を持つジョブを PR が書き換えられるため採らない（設計文書の「非機能の実現方式」） |
| 含める PR | main 宛ての開いた PR のうち、下書きでなく、head が検証の ref でも版上げの PR でもないもの（番号の順） | 「main へマージする前の PR を集めた」形。衝突すれば組まずに止め、衝突した PR を返す |
| 検証の版 | `<main の版>-next.<組んだ時刻 UTC の YYYYMMDDHHMMSS>` | 版が変わらない ref の移動は利用者のキャッシュに残る（STD4） |
| 進め方 | 前の検証の ref の先端を親に足したコミットで fast-forward だけで進める | 強制 push（C4）を使わない。main へは戻さない（I9・I12） |
| 利用者の手順 | `claude plugin marketplace add devbasex/ai-plugins#verify/next` で登録し、試す間は `claude plugin disable ndf@ai-plugins` で正式版を止める | 同じ名前のプラグインが 2 つとも有効になることを確かめた |

`marketplace.json` を置く場所は検証の ref の中で、`verify-ref` が組むたびに `name` を書き換える。main の `marketplace.json` は変えない。

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: STD4 / STD5

根拠: Value 5 / Value 13 / P1（MVV 版 7）

### 決定 10: 同期の PR を作らない

develop が無く、main へ入った版上げのコミットを戻す先が無いため、`land._sync`（本番ブランチからベースブランチ宛ての PR）を外し、結果から
`sync_pr` を外す。#3551 の形（版数を戻す同期の PR）は仕組みごと無くなる。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 6 / Value 11（MVV 版 7）

### 決定 11: `CHANGELOG.md` を残し、CD が版上げのコミットで版の節を書き、GitHub Release の本文を同じ節から作る

PR は `CHANGELOG.md` を触らない。版上げのコミット（決定 7）で、範囲（前の版上げのコミットの起点..main の先端）に入った PR から今の `changelog_decide` が版の節を組む。
GitHub Release の本文は今の `tagging` のとおりその節から作り、同じ元から 2 つを作る。配らないもの（入れ替えの前の ndf-next）の節は書かない。

| 観点 | 残して CD が書く | 廃止する |
| --- | --- | --- |
| 読むスクリプト | 書き換えない。テストを除いて 25 ファイルが読む（`rg -l CHANGELOG --type py plugins scripts -g '!**/tests/**'`、517a733d6 の時点。下の表） | 25 ファイルを GitHub Release の API で読む形へ書き換える。うち `plugins/ndf/` の 14 ファイルは 11.0.0 まで変えられない |
| 手元での検索 | `git grep`・`git show <ref>:CHANGELOG.md` で版の変化を読める | 読むたびに API を呼ぶ。2026-11-15 にも GraphQL の利用上限に当たった |
| 重複 | GitHub Release の本文と同じ中身を 2 か所に持つ。ただし CD が同じ節から作るため食い違わない | 正本が GitHub Release の 1 か所になる |
| PR どうしの衝突 | PR が触らないため起きない | 起きない |

廃止すると書き換える 25 ファイル:

| 置き場 | ファイル |
| --- | --- |
| ndf-next のリリース（5） | `plugins/ndf-next/src/ndf/domains/release/` の `assemble.py`・`changelog_decide.py`・`declaration.py`・`__init__.py`・`tagging.py` |
| 今の NDF（14） | `plugins/ndf/scripts/` の `design-match.py`・`doc-lint.py`・`instructions_lib/versions.py`・`pr-steps.py`・`release_lib/names.py`・`release_lib/others.py`・`release_lib/port_mark.py`・`release_lib/promotion.py`・`release_lib/publish.py`・`release_lib/replaced.py`・`release_lib/version_merge.py`・`release-steps.py`・`supervise_lib/prompts.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/review_focus.py` |
| リポジトリの検査と計測（6） | `scripts/check-doc-line-limit.py`・`scripts/check-doc-staleness.py`・`scripts/lib/release_map.py`・`scripts/measure/claude-p-usage.py`・`scripts/token-usage.py`・`scripts/token-usage-snapshot.py` |

残す形を採る。重複は同じ元から作ることで食い違いを防げ、廃止の書き換えは 11.0.0 まで終えられないためである。採否は P1 として
設計承認ゲートで人が見る（設計文書の「人へ残す判断」）。

- 作り方: 標準に倣う
- 比べた候補: PRD1 / LIB1

根拠: Value 2 / Value 11 / P1（MVV 版 7）

### 決定 12: `AGENTS.md` と版数の正本の書き直しは、11.0.0 からの運用を版数の正本の別の節に書き、`AGENTS.md` からはその節を指す 1 段落だけにする

今の NDF は 11.0.0 の入れ替えまで develop と main で動くため、今の運用の記述を書き換えると、入れ替えまでの読み手に食い違いが生じる。

| 文書 | この課題の実装で書くこと | 11.0.0 の入れ替え（#3304）で行うこと |
| --- | --- | --- |
| `docs/versioning-and-distribution.md` | 節「11.0.0 からの運用（GitHub flow）」を足す。マージした変更が利用者へ届くのは main へのマージの時点、版数とタグは CD がマージの時点に作る、マージの前に試すには 2 つ目のマーケットプレイスを登録する（手順は決定 9 の行）。#188 で main と develop に分けた理由と、それを検証の ref で受けることを書く | develop と開発版チャネルの節を外し、足した節を本文にする |
| `AGENTS.md` の「版とリリースの方針」 | 「11.0.0 の入れ替えで GitHub flow へ移る」1 段落と、版数の正本の節へのリンク | チャネルの表を GitHub flow の形へ書き換える |

`CLAUDE.md` の「の次の版で」の段落の形は、版が決まる前の決定を `CLAUDE.md` に置くためのもので、運用の正本の文書には使わない。
今の `AGENTS.md` の「取得元の登録に ref は保存されない」は、確かめ 1 の登録（`known_marketplaces.json` に `ref` が残る）と食い違う。
そのため、同じ実装で版数の正本の該当の段落と合わせて直す。

- 作り方: 標準に倣う
- 比べた候補: STD4

根拠: Value 14 / C7（MVV 版 7）

### 決定 13: #3576 を閉じる

#3576 は常設のリリースブランチの取り込み方の行（`standing`）と、リリースブランチから develop への同期の PR を足す課題である。
どちらも GitHub flow で無くなる（決定 5・10）。残る取り込み方の行 `github-flow` と 2 つ目のマーケットプレイスの宣言は、#3572 の
受け入れ条件 12・15 が持つため、#3576 を書き換えて残すと同じ条件を 2 つの課題に持つ。実装のステップで、理由を本文に書いて閉じる。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 6 / Value 11（MVV 版 7）

### 決定 14: #3119 の設計の決定 21 を、ai-plugins の宣言を行 `github-flow` にする決定へ置き換える

#3119 の設計の決定 21（常設のリリースブランチ `standing`）を、決定 5 の行 `github-flow` を ai-plugins の宣言にする決定へ書き換える。
宣言の書き換えは 11.0.0 の入れ替えと同じ時点で行い、`base_branch` を main にする。取り込み方の表から `promote-whole`・`promote-selected` を
外すのも同じ時点である（決定 5）。差分は同じ設計 PR の `issues/issue-3119-design.md` と
`issues/issue-3119-design-detail.md` にある。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 4 / P1（MVV 版 7）

### 決定 15: #3587 を閉じずに直す

#3587（宛先 develop、先端 441dc236e、33 ファイル）は、新しい土台でも残る部分が大半で、その部分は cross-review と cross-refactoring を通った。
閉じて出し直すと、残す部分のレビューと 21 件のリファクタリングのコミットをやり直す（件数は `git log --oneline origin/develop..origin/sprint/lrel-lock | grep -c "Refactor:"`）。
ndf-next は 11.0.0 の入れ替えまで develop で開発するため、
宛先 develop も変えない。外す部分を取り除くコミットと、決定 5〜7・9・10 の実装を同じ PR へ足し、変わった差分を再びレビューへ通す。

| 分け | ファイル | 何が残るか・何を外すか |
| --- | --- | --- |
| 残す | `contracts/templates/release.json`・`params.json` | 本番のプラン。`prepare-next` を `cleanup` へ置き換え、`merge` の後に `confirm` を足して 4 ステップにする |
| 残す | `contracts/templates/merge.json` | `release-guard` を先頭からマージの直前へ移す（決定 2） |
| 残す | `src/ndf/domains/release/release_plan.py`・`merge_decide.py`・`required_checks.py`・`shapes.py`・`trail.py` | 雛形の検査・人のマージの判定・必須のチェックの読み・人のマージの記録 |
| 残す | `src/ndf/progress/gate/release.py`・`answering.py`・`asking.py` | 人のマージの検知 |
| 残す | `src/ndf/domains/release/land.py` | 照合付きのマージ・止まった次の手・マージ済みからの再開。同期の PR（`_sync`）と `defer` を外す |
| 残す | `src/ndf/domains/release/cli.py`・`__init__.py`・`declaration.py` | `guard`・`answer --route`・`merge` の結果。`prepare-next`・`defer`・`--source` を外す |
| 残す | `contracts/schemas/io/release-steps.args.schema.json`・`release-steps.result.schema.json`・`plugins/ndf-next/docs/contracts/commands.md` | 残す副命令の契約。外す副命令と引数の行を消す |
| 残す | `.github/workflows/ndf-next-release.yml`・`docs/specifications/ndf-release-approval-on-pr.md`・`docs/specifications/ndf-release-production-admin-merge.md` | 結果の値の読み方の直し |
| 残す | テスト `_fakes.py`・`test_release_approval.py`・`test_release_plan.py`・`test_release_queue.py`・`test_release_files.py`・`test_gate_release.py`・`test_gate_present.py`・`tests/route/test_flows.py` | 残す振る舞いのテスト。`defer` と同期の PR のテストの行を消す |
| 外す | `src/ndf/domains/release/prepare.py`・`tests/domains/release/test_release_prepare.py` | 次の版のリリースブランチの用意 |
| 外す（書き直す） | `src/ndf/domains/release/assemble.py`・`tests/domains/release/test_release_assemble.py` | 常設のリリースブランチの組み立てと組み直し（`--source release`・既にあるブランチの進め）を外し、決定 6 の承認資料だけの形へ書き直す |

残すは 29 ファイル、外すは 2 ファイル（`prepare.py` とそのテスト）で、書き直すのは 2 ファイル（`assemble.py` とそのテスト）である。

| 比べた候補 | 採否と理由 |
| --- | --- |
| #3587 を閉じて main から出し直す | 採らない。残す部分のレビューとリファクタリングをやり直し、ndf-next の開発の場所（develop）とも合わない |
| #3587 を直して出す | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 2 / Value 5（MVV 版 7）
