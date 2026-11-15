# #3572 の決定の記録: リポジトリごとのブランチ戦略で承認した head だけを本番へ入れ、同時の承認は merge queue で確かめる

設計文書 [issue-3572-design.md](issue-3572-design.md) の決定の記録（決定 1〜26）である。不変条件（I1〜I18）・外部の調査（STD1〜STD11・PRD1〜PRD3・LIB1〜LIB4）・
用語は、設計文書の同じ名前の章の意味で使う。確かめ 1〜7 は [issue-3572-design-evidence.md](issue-3572-design-evidence.md) にある。

置き換えた決定は消さずに残し、見出しの直後に状態の行（`状態: superseded（決定 N）` か `状態: 一部 superseded（…は決定 N）`）を置く。
状態の行の無い決定は有効である。

## 決定の記録

### 決定 1: 承認の外のコミットが積まれる経路を無くすため、本番のプランから worker を外し、サブコマンドを並べた 4 ステップにする

本番のプランの `work`（`{{worker}} release`）は、worker にリリースの作業全体を任せるため、CI が落ちたときにコードを直して push する
判断も worker に残る。リリース 8・14 はこの裁量で起きた。ステップを `ndf release-steps` の `merge` → `confirm` → `record` → `cleanup` だけにすれば、
承認の後にコードを変える主体がプランの中に居なくなる。`confirm` は CD のバージョン上げとタグを待って確かめるだけで、コードもブランチも変えない。
この 4 ステップはどの取り込み方の行・どのマージの方式でも同じで、行と方式の違いは `merge` と `confirm` の中で選ぶ（決定 16〜19）。
手順書だけで止める形は 2 回とも止まらなかったため採らない（要求の「含まない」）。ruleset でブランチをロックする形は、利用者の「ロックの方針」で採らない。
承認するのは本番の PR の head で、マージとキューへの投入は head の一致の確認付き（`expect_head`・`expectedHeadOid`）で打つ。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD1 / STD10 / STD3

根拠: Value 3 / Value 7（MVV 版 7）

### 決定 2: 本番の PR を fix の worker のあるプランでマージしないため、`merge` の部品の直前に `release-guard` を置き、テンプレートの検査で縛る

`release_plan.forbidden(plan)` は、`release: true` のプランについて次の 4 つを返す。

- worker を起動するステップ
- `push` を使うステップ（`push`・`fix-push`・`ci-fix`・`fix-base`・`base-sync`）
- 次のバージョンの用意のステップ
- 最初が `ndf release-steps merge` でないこと

`release: true` でないテンプレートについては、`merge` の部品（`check.json` では `merge-gate`）の直前に `release-guard` が無いことを返す。

`trunk` の行ではスプリントの PR が本番の PR を兼ね、今の行ではリリースブランチの PR が本番の PR である。どちらも宛先が本番ブランチの PR で、
`release-guard` は宛先で本番の PR を見分ける。承認の前の CI の直しはこれらのテンプレートで行ってよく（承認の対象はその後の head である）、
マージだけをデプロイの工程へ渡せば足りる。本番の PR なら終了コード 3 を原因 `production_pr` にして `end` へ進め、結果に「デプロイの工程で流す」を出す。

| 比べた候補 | 採否と理由 |
| --- | --- |
| `release-guard` を `merge.json` の先頭に置く | 採らない。承認の前の CI の直しが通らなくなり、`check`・`close` のテンプレートの穴も残る |
| プランを読む核（`core/plan/model.py`）や `ndf ci merge` で同じ判定を強制する | 採らない。核と CI の領域がリリースの語を持ち、リリースのドメインの判定を外へ漏らす |
| 部品 `release-guard` をマージの直前に置き、テンプレートの検査で全テンプレートを縛る | 採る |

- 作り方: 自前で作る
- 比べた候補: STD3

根拠: Value 3 / Value 11（MVV 版 7）

### 決定 3: 止まった本番の PR の次の手を `rerun` と `reject` の 2 つにし、同じ head で通らない止まり方では `reject` だけを出す

| 次の手 | 何をするか | 既定の手（`outputs.suggested`）になる止まった理由 |
| --- | --- | --- |
| `rerun` | 同じ承認・同じ head のまま `merge` を打ち直す | `infra_wait`・`ci_pending`（チェックの一時的な失敗・待ちの切れ） |
| `reject`（差し戻し） | 本番の PR を開いたまま、直しを同じ PR へ入れ、新しい head で承認を取り直す | `test_failed`・`check_failed`・`refused`・`queue_removed`・`behind` |

`refused`（head が動いた）・`queue_removed`（キューから外れた）・`behind`（`strict` で先端を含まない）では、`routes` に `reject` だけを出す。
`refused` は PR の head が承認した head と違い、同じ承認で打ち直しても GitHub が同じく断る。キューは同じ head では同じ組み合わせで落ち（I16）、
`behind` は head を進めない限り解けないためである（決定 18・19）。ほかの止まった理由では 2 つとも出す（受け入れ条件 4）。
`rerun` は止まった理由から機械的に決まるため、オーガナイザーか一時レーンが打ってよい。`reject` の後の直しは承認の前の工程（スプリントの経路）で
入れ、新しい head で `assemble`・`present` から取り直す。本番の PR を閉じて次のバージョンへ回す手は持たない。

- 作り方: 標準に倣う
- 比べた候補: STD3 / STD7 / PRD1

根拠: Value 1 / Value 7（MVV 版 7）

### 決定 4: 承認ゲートを問い直さずに人のマージを承認として扱うため、検知を工程の承認ゲートの待ちの読み直しに置く

リリース承認ゲートの待ちは、答えの確認のために本番の PR を GitHub から読み直している（`github_observe`）。この読み直しで PR が
マージ済みなら、マージした head への承認（経路 `github-merge`、答えた者は `merged_by` のログイン）とする。キューへ入れてマージされた PR では、
`merged_by` はキューへ入れた人である（確かめ 7）。キューへ入れただけでまだマージされていない PR は承認として扱わず、待ちを続ける。
判定は `merge_decide` が持つ。承認ゲートは `release-steps answer --route github-merge` を呼んで承認の記録を書かせる。本番のプランは
最初の `merge` でマージ済みの PR から再開し（`land._resume_merged`）、人のマージの記録を行う。`pull_request` の `closed` で動く workflow は、
承認ゲートの答えを書く経路を持たないため採らない。人のマージは、承認ゲートで別の head へ与えた承認を引き継がない（受け入れ条件 8）。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD2 / STD7

根拠: Value 1 / Value 3（MVV 版 7）

### 決定 5: GitHub flow を選べるようにするため、取り込み方の表に行 `trunk` を足す

状態: 一部 superseded（行を既定にすること・ndf-next のリリースで `trunk` だけを扱うこと・11.0.0 の入れ替えで `merge_base_branch`・`release_branch` を表から外すことは決定 16）

| 列 | 値 | 意味 |
| --- | --- | --- |
| `release_strategy` | `trunk` | スプリントの PR を本番ブランチへ直接マージする |
| `start` | `["production"]` | スプリントブランチは本番ブランチから切る |
| `doc_prs` | `["sprint"]` | 設計 PR と確定仕様化の PR はスプリントブランチへ（文書だけで本番へマージしてリリースを起こさない） |
| `integrate` | `["release"]` | スプリントブランチは、スプリントの PR のマージ（リリース）で本番ブランチへ入る |
| `keeps_sprint` | `false` | マージした時点でスプリントブランチを残さない |
| `release_from` | `production` | バージョン上げの PR のブランチは本番ブランチから作る |
| `release_units` | `false` | 集めるスプリントを選ばない（1 つのスプリントの PR が 1 回のリリース） |
| `review_kinds` | `["impl", "sprint", "design", "spec"]` | リリースブランチの PR（`release`）の行を持たない |

この行を選ぶ設定ファイル（`.ndf/worktree.json`）は、`base_branch` と `production_branch` を同じ本番ブランチにする。

（superseded の部分）行 `trunk` を既定にし、`merge_base_branch`・`release_branch` は ndf-next のリリースで扱わず、11.0.0 の入れ替えで表から外す。
それまで ndf-next のリリースのサブコマンドは、設定ファイルの行が `trunk` でなければ原因 `precondition` で止まる。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 設定ファイルを変えず、`merge_base_branch` のまま `base_branch` を main にする | 採らない。`merge_base_branch` は文書の PR を `base`（= main）へ入れ、文書だけのマージでリリースが起きる。行の意味と運用が食い違う |
| 常設のリリースブランチの行（`standing`） | 採らない。GitHub flow を選ぶリポジトリは、スプリントの宛先を本番ブランチにする |
| 行 `trunk` を足す | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 4 / Value 11 / P1（MVV 版 7）

### 決定 6: `trunk` の行で main へのマージをそのままリリースにするため、スプリントの PR を本番の PR にし、承認資料をその PR の本文に書く

リリースのためのブランチを組まず、スプリントの PR（main 宛て）がリリース承認ゲートの対象になる。マージ＝リリース承認＝マージ承認である。
`assemble` はブランチとバージョン上げのコミットを作らず、範囲（main の先端..PR の head）から承認資料を組んで PR の本文の承認資料の節へ書く。
本文の書き換えは head を動かさないため、承認の対象を変えない。予定のバージョンは上げ幅のラベル（決定 7）から出し、確定のバージョンは CD が決める。
この振る舞いは `trunk` の行だけのもので、今の行の `assemble`（リリースブランチの組み立て・開発版チャネルの組み立て `--channel dev`）は今のまま残す（決定 16）。

- 作り方: 標準に倣う
- 比べた候補: STD3 / PRD1

根拠: Value 1 / Value 5（MVV 版 7）

### 決定 7: `trunk` の行でバージョンをマージの時点に CD が上げるため、上げ幅をラベルで受け、バージョン上げの PR を head の一致の確認付きで main へ入れてからタグを作る

状態: 一部 superseded（手順 3 の書き換え先を `plugin.json` に決め打つ部分は決定 22、手順 4 のマージとキューの関係は決定 20）

main の ruleset（確かめ 6）は、迂回を管理者の役割（`RepositoryRole` 5）だけに、形 `pull_request` で許す。PR を通さない main への
push には迂回が効かず、承認 1 と必須のチェックが掛かる。そのため CD はバージョン上げのコミットを main へ直接 push せず、バージョン上げの PR で入れる。

1. main への push で `bump` が動く。最後のバージョン上げのコミットのバージョンにタグが無ければ、先にそのコミットを `publish` へ渡し、その後で範囲を読み直す。範囲（前のバージョン上げのコミットの起点..main の先端）にリリース対象の変化があれば、上げ幅を決める。main への push の run は workflow の `concurrency` で 1 本ずつ流す。バージョン上げの PR のマージからタグまでの間に入った PR も、同じ run か次の run でバージョン上げへ進み、後の push を待たない
2. 上げ幅は、範囲にマージされた PR のラベル（`release:major`・`release:minor`・`release:patch`）の最大で、ラベルが無い PR は PATCH とする（要求の起票者の推定 S8 を採る。付け忘れでマージを止めないため）
3. main の先端から `release/v<バージョン>` を作り、バージョンの置き場（決定 22）と CHANGELOG のバージョンの節（決定 11）と手順の書き先だけを変えたコミットを足す。バージョン上げの PR は `NDF_RELEASE_TOKEN` で作る（`github.token` で作ると CI が起動しない）
4. `bump_decide` が差分を置き場・CHANGELOG・手順の書き先に限られると判定し、必須のチェックの後にマージする（キューとの関係は決定 20）
5. バージョン上げの PR のマージの push で、`bump` は先端のバージョンにタグが無いと読んで上げず、`publish` がタグと GitHub Release を作る

同じバージョンの PR が開いていれば使い直し（I8）、`concurrency` で workflow を 1 本ずつ流す。PR の検査（`pr-check`）は行が `trunk` のときだけ働き、
今の行ではリリースブランチの PR がバージョンを上げるため 0 を返して何も見ない。

| 比べた候補 | 採否と理由 |
| --- | --- |
| CD が main へ直接 push する | 採らない。ruleset の迂回の形が `pull_request` で、直接 push は断られる |
| ruleset の迂回の形を `always` にする | 採らない。ruleset の書き換えで、承認ゲートを通らない機械の push を許す |
| `plugin.json` から `version` を外し、コミットの SHA で更新を検知させる（STD4） | 採らない。バージョンを読むスクリプトがすべて書き換わり、利用者がバージョンで過去へ戻れない |
| release-please-action（LIB1）・semantic-release（LIB2） | 採らない。上げ幅をコミットの書式から読み、このリポジトリの書式と CHANGELOG の形に合わない |
| バージョン上げの PR を自前の `bump` で作る | 採る |

- 作り方: 標準に倣う・自前で作る
- 比べた候補: PRD1 / PRD2 / LIB1 / LIB2 / STD4 / STD6

根拠: Value 3 / Value 5 / C2 / P1（MVV 版 7）

### 決定 8: 人のマージの記録を承認の記録と同じ場所で読めるよう、本番の PR のコメントに 1 回だけ書く

承認の記録と迂回の記録は本番の PR のコメントにあり、リリース記録とリリース後テストもそこから読む。人のマージの記録も同じ PR に置けば、
1 つの PR を読めば承認の経路が分かる。書くのは `land` のマージ済みからの再開の 1 か所で、マーカーのあるコメントがあれば書かない。
キューを通したマージでは、マージの方式（`merge-queue`）とマージコミット（キューの一時のコミット）も同じコメントに書く。

- 作り方: 自前で作る
- 比べた候補: STD2

根拠: Value 13 / Value 14（MVV 版 7）

### 決定 9: マージの前に試せるようにするため、本番ブランチ宛ての開いた PR を集めた検証用ブランチを workflow が組み、検証用マーケットプレイスがそれを指す

状態: 一部 superseded（検証環境を検証用マーケットプレイスの 1 種類に決め打つ部分は決定 21。下の表は種類 `marketplace` の値として残る）

#188（2026-08-31）で main と develop に分けたのは、マージした時点で常用する利用者へ届くのを避け、届ける前に試す場所を持つためである。
`trunk` の行ではマージがリリースになるため、試す場所をマージの前へ移す。Claude Code にはリリースチャネルの概念が無く、名前の違う 2 つの
マーケットプレイスが違う ref を指す形を勧めている（STD4）。確かめ 1〜3 で、同じリポジトリを違う名前と ref で登録すると両方が残り、
作り直しが更新で入り、正式版の登録が変わらないことを見た。

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| ブランチの名前 | 設定ファイルの `verify.ref`（ai-plugins は `verify/next`） | 名前で用途が分かり、`release/v` と `sprint/` の規則と重ならない |
| マーケットプレイスの名前 | 設定ファイルの `verify.marketplace`（ai-plugins は `ai-plugins-next`） | 1 つの名前に 1 つの登録しか持てない（STD5）。正式版と同じ名前は登録できないことを確かめた |
| 組む主体と時期 | `ndf-next-release.yml` が、本番ブランチ宛ての PR の作成・更新・閉じ（`pull_request_target`）と手動の起動で `verify-ref` を打つ | PR の出来事が中身の変わる時点である |
| 含める PR | 本番ブランチ宛ての開いた PR のうち、下書きでなく、head が検証用ブランチでもバージョン上げの PR でもフォークでもないもの（番号の順） | 衝突すれば組まずに止め、衝突した PR を返す |
| 検証のバージョン | `<main のバージョン>-next.<組んだ時刻 UTC の YYYYMMDDHHMMSS>` | バージョンの変わらない ref の移動は利用者のキャッシュに残る（STD4） |
| 進め方 | 前の検証用ブランチの head を親に足したコミットで fast-forward だけで進める | 強制 push（C4）を使わない。本番ブランチへは戻さない（I9・I12） |
| 利用者の手順 | `claude plugin marketplace add devbasex/ai-plugins#verify/next` で登録し、試す間は `claude plugin disable ndf@ai-plugins` で正式版を止める | 同じ名前のプラグインが 2 つとも有効になることを確かめた |

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: STD4 / STD5

根拠: Value 5 / Value 13 / P1（MVV 版 7）

### 決定 10: `trunk` の行では同期の PR を作らない

`trunk` の行では `land._sync`（本番ブランチからベースブランチ宛ての PR）を呼ばず、結果に `sync_pr` を出さない。
develop が無く、main へ入ったバージョン上げのコミットを戻す先が無いためである。今の行（ベースブランチが本番ブランチと違う）では今のまま作る（要求の前提 9。決定 16）。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 6 / Value 11（MVV 版 7）

### 決定 11: `CHANGELOG.md` を残し、CD がバージョン上げのコミットでバージョンの節を書き、GitHub Release の本文を同じ節から作る

PR は `CHANGELOG.md` を触らない。バージョン上げのコミット（決定 7）で、範囲に入った PR から今の `changelog_decide` がバージョンの節を組む。
GitHub Release の本文は今の `tagging` のとおりその節から作り、同じ元から 2 つを作る。配らないもの（入れ替えの前の ndf-next）の節は書かない。
節の見出しの形を設定ファイルで変えるのは #3615 の範囲である（決定 22）。

| 観点 | 残して CD が書く | 廃止する |
| --- | --- | --- |
| 読むスクリプト | 書き換えない。テストを除いて 25 ファイルが読む（`rg -l CHANGELOG --type py plugins scripts -g '!**/tests/**'`、517a733d6 の時点。下の表） | 25 ファイルを GitHub Release の API で読む形へ書き換える。うち `plugins/ndf/` の 14 ファイルは 11.0.0 まで変えられない |
| 手元での検索 | `git grep`・`git show <ref>:CHANGELOG.md` でバージョンの変化を読める | 読むたびに API を呼ぶ。2026-11-15 にも GraphQL の利用上限に当たった |
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

### 決定 12: `AGENTS.md` とバージョンの正本の書き直しは、11.0.0 からの運用を正本の別の節に書き、`AGENTS.md` からはその節を指す 1 段落だけにする

今の NDF は 11.0.0 の入れ替えまで develop と main で動くため、今の運用の記述を書き換えると、入れ替えまでの読み手に食い違いが生じる。

| 文書 | この課題の実装で書くこと | 11.0.0 の入れ替え（#3304）で行うこと |
| --- | --- | --- |
| `docs/versioning-and-distribution.md` | 節「11.0.0 からの運用（GitHub flow）」を足す。マージした変更が利用者へ届くのは main へのマージの時点、バージョンとタグは CD がマージの時点に作る、マージの前に試すには検証用マーケットプレイスを登録する（手順は決定 9 の行）、同時に承認された PR の組み合わせは merge queue が確かめる。#188 で main と develop に分けた理由と、それを検証環境で受けることを書く | develop と開発版チャネルの節を外し、足した節を本文にする |
| `AGENTS.md` の「版とリリースの方針」 | 「11.0.0 の入れ替えで GitHub flow へ移る」1 段落と、正本の節へのリンク | チャネルの表を GitHub flow の形へ書き換える |

今の `AGENTS.md` の「取得元の登録に ref は保存されない」は、確かめ 1 の登録（`known_marketplaces.json` に `ref` が残る）と食い違う。
そのため、同じ実装で正本の該当の段落と合わせて直す。ai-plugins がどの行を選ぶかは ai-plugins の設定ファイルの値で、NDF の製品の仕様として書かない。

- 作り方: 標準に倣う
- 比べた候補: STD4

根拠: Value 4 / Value 14 / C7（MVV 版 7）

### 決定 13: #3576 を閉じる

#3576 は常設のリリースブランチの取り込み方の行（`standing`）と、リリースブランチから develop への同期の PR を足す課題である。
取り込み方の表の 2 行を残すこと（決定 16）は #3576 の扱いを変えない。今の 2 行はどちらも常設のリリースブランチを持たず、
develop を持つリポジトリの同期の PR は今の行の `land._sync` が既に作る。`trunk` の行と検証環境は #3572 の受け入れ条件 12・15・20 が持つため、
#3576 を書き換えて残すと同じ条件を 2 つの課題に持つ。実装のステップで、理由を本文に書いて閉じる。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 6 / Value 11（MVV 版 7）

### 決定 14: #3119 の設計の決定 21 を、ai-plugins の設定ファイルを行 `trunk` にする決定へ置き換える

状態: superseded（決定 25）

#3119 の設計の決定 21（常設のリリースブランチ `standing`）を、行 `trunk` を ai-plugins の設定ファイルにする決定へ書き換える。
設定ファイルの書き換えは 11.0.0 の入れ替えと同じ時点で行い、取り込み方の表から `merge_base_branch`・`release_branch` を外すのも同じ時点とする。

根拠: Value 4 / P1（MVV 版 7）

### 決定 15: #3587 を閉じずに直す（33 ファイルの残す・外すの一覧）

状態: superseded（決定 24）

#3587（当時の head 441dc236e、33 ファイル）を閉じずに直し、常設のリリースブランチの部分（`prepare.py`・`assemble.py` の組み直し）を外し、
決定 5〜7・9・10 の実装を足す。

根拠: Value 2 / Value 5（MVV 版 7）

### 決定 16: ブランチ戦略をリポジトリごとに選べるようにするため、取り込み方の表の 3 行を残して既定を `merge_base_branch` のままにし、リリースのサブコマンドは行ごとに振る舞いを選ぶ

ブランチ戦略はリポジトリの決まりで、NDF の制約にしない（#3119・#3121 の「リポジトリの決まりは設定の値で受ける」）。develop と main を分ける Web・アプリの
リポジトリは `merge_base_branch`・`release_branch` を選び、GitHub flow を採るリポジトリは `trunk` を選ぶ。ai-plugins が `trunk` を選ぶのは
ai-plugins の設定ファイルの値である。既定の行（`branch_flow` の無い設定ファイルが使う行）は `merge_base_branch` のままにする。既定を変えると、
設定ファイルの無いリポジトリのスプリントの起点と宛先が黙って変わるためである。

リリースのサブコマンドは、行の列で振る舞いを選ぶ。`require_github_flow`（行が `trunk` でなければ止まる）は外す。

| 振る舞い | `trunk` | `merge_base_branch`・`release_branch` |
| --- | --- | --- |
| 本番の PR | スプリントの PR | リリースブランチの PR（今のまま） |
| `assemble` | 本文の承認資料の節だけ（決定 6） | リリースブランチの組み立て（今のまま） |
| 承認の後のプラン | 4 ステップ（決定 1） | 同じ 4 ステップ |
| マージ | マージの方式が必須（決定 17） | 方式を書けば照らして使い、無ければ今のマージ |
| 同期の PR | 作らない（決定 10） | 今のまま作る |
| バージョン | CD がバージョン上げの PR で上げる（決定 7） | リリースブランチで上げる（今のまま） |
| `pr-check`・`bump` | 働く | 0 を返して何も見ない |
| `publish`（タグと GitHub Release） | main への push の workflow で、バージョン上げのコミットに作る（決定 7） | main への push の workflow で、本番の PR のマージコミットに、リリースブランチで上げたバージョンで作る（今の `tagging.publish` のまま） |
| `confirm` | バージョン上げの PR のマージ・タグ・GitHub Release を待つ | タグと GitHub Release を待つ |

今の workflow は、自分の run の中でマージした後に `publish` を続けてタグを作る。承認の後のマージはプランの `merge` が打つため、その run が無い。
そのため、main への push で `bump` と `publish` を打つ workflow の仕事を今の行にも当て、今の行のタグの作り手を `publish` にする。`publish` は
あるタグを作らないため、手動の起動（`step=publish`）と重なっても 1 つしか作らない。

今の行ではマージの方式を必須にしない。本番の PR はリリースブランチの PR で、1 度に 1 つしか開かない。組み合わせはベースブランチ（か選んで集めた
リリースブランチ）で既に CI を通っている。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 11.0.0 の入れ替えで 2 行を外し、ndf-next のリリースは `trunk` だけを扱う（決定 5 の superseded の部分） | 採らない。develop を持つリポジトリが ndf-next のリリースを使えなくなり、受けられる運用を減らす |
| `trunk` を既定の行にする | 採らない。設定ファイルの無いリポジトリの振る舞いが黙って変わる |
| 3 行を残し、行ごとに振る舞いを選ぶ | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 4 / Value 10 / P1（MVV 版 7）

### 決定 17: 同時に承認された PR の組み合わせを確かめてから入れるため、マージの方式を `.ndf/project.json` の `production_merge.method` で選び、本番ブランチの ruleset と照らす

GitHub flow ではスプリントの PR が直接本番ブランチへ入る。ほぼ同時に承認された A と B のうち後の B は、`strict` が偽なら A を取り込まずにマージできる。
そのため A+B の組み合わせが CI を通らずに本番へ出る（確かめ 6）。組み合わせの確かめは GitHub の merge queue に任せる（LIB3）。
merge queue を使えないリポジトリ（STD9: Organization の public か Enterprise Cloud の private でない）には、`strict`（最新の取り込みを必須にする）を同じ設定の別の値として持たせる。

| 値 | 本番ブランチの ruleset に要るもの | `land` の動き |
| --- | --- | --- |
| `merge-queue` | merge queue の規則（GraphQL `mergeQueue(branch)` が null でない） | キューへの投入と待ち（決定 18） |
| `strict` | 必須のチェックの `strict_required_status_checks_policy` が真 | 先端の取り込みの確かめとマージ（決定 19） |

置き場を `production_merge` にするのは、本番のマージの振る舞い（迂回の `admin`。#3506）を既に持つ設定で、同じ読み手（`land`）が読むためである。
`.ndf/worktree.json` の `branch_flow` はスプリントの経路も読む設定で、マージの方式を置くとスプリントの経路が使わない値を持つ。
`trunk` の行では方式の記述を必須にし、無い・表に無い値・ruleset と食い違う、のどれでも `land` が原因 `precondition` で止まる（I15）。
既定の方式へ倒さないのは、倒した先が ruleset の実際と食い違えば、組み合わせの確かめが黙って抜けるためである。
ruleset の読み取りは `GET /repos/{o}/{r}/rules/branches/{branch}`（本番ブランチに効く規則の一覧）と GraphQL の `mergeQueue(branch)` で行い、書き換えない。

**レビューの承認の数。** merge queue へ入れられるのも `strict` でマージできるのも、PR が保護の条件（レビューの承認の数を含む）を満たした後である（STD7）。
NDF の承認ゲートの承認は PR のコメントで、GitHub のレビューの承認ではない。そのため、本番ブランチの PR の規則が承認 1 以上を求めると、
迂回を使わない（I17）`land` は投入もマージもできない。マージの方式を使うリポジトリは、本番ブランチの PR の規則の承認の数を 0 にする
（ai-plugins は 11.0.0 の入れ替えのとき。決定 23・設計文書の「人へ残す判断」の C2 の行）。人の承認は、リリース承認ゲートと、それと等価な人の GitHub でのマージが持つ。
`land` は照らし合わせで、規則の `required_approving_review_count` が 1 以上で、PR の `reviewDecision` が `APPROVED` でないかを見る。
そのときは投入もマージもせずに止まり、原因 `precondition`・理由に「本番ブランチの規則がレビューの承認を求め、PR に承認が無い」を出す（I15）。承認の数を残したまま、
ほかの人が GitHub のレビューで承認するリポジトリは、承認が付けば通る。

| 承認の数を満たす候補 | 採否と理由 |
| --- | --- |
| 承認ゲートの答えで、NDF が GitHub のレビューの承認を書く | 採らない。NDF の token の持ち主が PR の作者と同じとき（利用者が自分の token で PR を作るとき）、GitHub は作者の承認を受けない。承認が 2 か所に分かれる |
| 迂回の役割を持つ token でキューへ入れる | 採らない。迂回の役割で、保護の条件を満たさない PR をキューへ入れられるかは未確認で、迂回は必須のチェックと最新の取り込みも越える（I14 が破れる） |
| 本番ブランチの PR の規則の承認の数を 0 にし、人の承認を承認ゲートと人のマージで持つ | 採る。必須のチェック・キュー・最新の取り込みの規則は残る |

| 比べた候補 | 採否と理由 |
| --- | --- |
| ndf-next が自前で組み合わせのブランチを組んで確かめる（常設のリリースブランチ・bors の形） | 採らない。GitHub の merge queue が同じことを持ち（STD7）、bors-ng も merge queue へ移るよう案内している（LIB4） |
| `strict` だけにする | 採らない。後にマージする PR ごとに取り込みと承認の取り直しが要り、同時の承認が多いほど承認の回数が増える。merge queue は作者の取り込みなしで同じ利点を得る（STD7） |
| 設定を書かせず ruleset から方式を推す | 採らない。リポジトリの持ち主の意図と ruleset の食い違いを検出できない |
| 設定ファイルで選び、ruleset と照らす | 採る |

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: LIB3 / LIB4 / STD7 / STD9 / PRD3

根拠: Value 4 / Value 7 / Value 12（MVV 版 7）

### 決定 18: `merge-queue` では承認した head を `expectedHeadOid` に渡してキューへ入れ、マージかキューから外れるのを待ち、外れたら `reject` だけを返す

`land` は承認の記録の読み直しと ruleset の照らし合わせの後、PR の必須のチェックを待つ。
その後に `enqueuePullRequest(pullRequestId, expectedHeadOid=<承認した head>)` を打つ（STD10・確かめ 4）。
GitHub は PR の head が違えば投入を断り、`land` は `refused` で止まる。投入の後は `ci_budget` の秒まで、PR の `merged`・`isInMergeQueue`・`mergeQueueEntry.state` を読む。

| 読んだ状態 | `land` の扱い |
| --- | --- |
| `merged` が真 | マージした head（PR の head）が承認した head と一致すれば、マージコミット（キューの一時のコミット）を記録して `ok`（経路 (a)）。違えば人の操作のマージ（経路 (b)）として、人のマージの記録（承認した head はマージした head、承認ゲートの head は承認した head）を書いて `ok` にする（I5） |
| `isInMergeQueue` が偽で `merged` が偽 | キューから外れた（チェックの失敗・時間切れ・衝突・取り外し）。原因 `queue_removed` で止まり、キューの一時のコミットの落ちたチェックを読めれば `items` に出す。次の手は `reject` だけ（決定 3・I16） |
| キューの中のまま `ci_budget` を過ぎた | 原因 `ci_pending` で止まり、次の手は `rerun`（同じ承認で待ちを続ける。キューへは入れ直さない。入っているため） |

キューから外れた PR は、同じ head で入れ直さない。キューの一時のコミットは本番ブランチの先端と前に並ぶ PR と合わせたもので、外れた理由が
組み合わせの失敗なら、同じ head で入れ直しても同じ組み合わせで落ちる。直しを入れた新しい head の承認を取り直してから入れる（受け入れ条件 25）。
キューの組み合わせは GitHub が決めるため、B のレーンは A が外れても何もしない（GitHub が B を確かめ直す。STD7）。

**経路 (a) と (b) の見分け。** `land` が打つ投入とマージは承認した head だけを渡し、GitHub は head が違えば断る。そのため、承認した head と違う head の
マージは `land` の操作では起こらず、人がキューから外して入れ直したか、GitHub で直接マージした操作である。`land` はマージした head だけで見分け、
キューへ入れた人（`merged_by`）や出来事の主は使わない。NDF の token の持ち主と利用者が同じことがあり、見分けられないためである。

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: STD7 / STD10 / STD11 / LIB3

根拠: Value 3 / Value 7（MVV 版 7）

### 決定 19: `strict` では head が本番ブランチの先端を含まなければマージせずに止め、取り込んだ新しい head で承認を取り直す

`land` は ruleset の `strict` が真であることを確かめた後、PR の head が本番ブランチの先端を祖先に持つかを確かめる（`compare` の `behind_by` が 0）。
含まなければ原因 `behind` で止まり、次の手は `reject` だけを出す。直す手はスプリントの経路が本番ブランチを取り込んで head を進めることで、
取り込んだ head は承認した head と違うため、承認は `stale` になり取り直す（受け入れ条件 23）。マージそのものは `expect_head` 付きで、迂回せずに打つ（`production_merge.admin` が真でも。I17）。
迂回は最新の取り込みの規則も越えるため、迂回しないことで、確かめとマージの間に本番ブランチが動けば GitHub が断る（strict の規則）。そのとき `land` は確かめへ戻って `behind` で止まる。
ruleset の `dismiss_stale_reviews_on_push` が偽でも（確かめ 6）、NDF の承認は head ごとの判定で `stale` になるため、取り直しは GitHub のレビューの設定に依らない。

- 作り方: 標準に倣う
- 比べた候補: STD1 / STD7 / STD9

根拠: Value 3 / Value 4（MVV 版 7）

### 決定 20: バージョン上げの PR は merge queue を通さず迂回してマージし、本番の PR はマージの方式があるとき迂回しない

バージョン上げの PR は人の承認を持たない。決定 17 で本番ブランチの承認の数を 0 にすればキューへ入れられるが、キューを通すとキューの CI がもう 1 回走り、リリースとバージョンの間の時間（設計文書の
「main へのマージの後（CD）」）が延びる。一方、迂回しても組み合わせの確かめは抜けない。`land_bump` は main の先端がバージョン上げの PR の head の
祖先であるときだけマージするため、マージした後の main は、バージョン上げの PR の必須のチェックが確かめた木と同じになる。

| 守り | どこで守るか |
| --- | --- |
| 迂回するのはバージョン上げの PR だけ | `land` はマージの方式（`merge-queue`・`strict`）のある本番の PR で、`production_merge.admin` が真でも `merge_as_admin` を呼ばない（I17）。迂回は必須のチェックと最新の取り込みの規則も越え、`strict` でも I14 が破れるためである。マージの方式の無い今の行の本番の PR は今の規則のまま |
| バージョン上げの PR にコードが混ざらない | `bump_decide` が、差分がバージョンの置き場（決定 22）・CHANGELOG・手順の書き先に限られると判定したときだけ迂回する |
| 確かめた木がそのまま入る | main の先端が head の祖先であることを確かめてから、`expect_head` 付きで迂回する |
| 迂回の許し | 設定ファイルの `production_merge.admin` が真のときだけ（#3506 の今の規則） |

人が GitHub の画面で管理者の迂回を使ってコードの PR をマージすることは、利用者の操作（経路 (b)）で、工程は止めない。人のマージの記録に
マージの方式とマージコミットが残る（決定 8）。

| 比べた候補 | 採否と理由 |
| --- | --- |
| バージョン上げの PR もキューへ入れる | 採らない。キューの CI が 1 回増え、リリースとバージョンの間の時間が延びる。祖先の確かめで、迂回しても確かめた木と同じものが入る |
| バージョン上げの PR をやめ、タグだけを作る（バージョンのファイルを持たない） | 採らない。決定 7 の採らなかった案と同じ理由 |
| 3 つの守りを付けて迂回する | 採る |

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD6 / STD7 / STD11

根拠: Value 3 / Value 5 / C2（MVV 版 7）

### 決定 21: 検証環境をリポジトリごとに選べるようにするため、`delivery` の検証の行の `verify.kind` で種類（`marketplace`・`deploy`）を選ぶ

マージの前に試す場所はリポジトリのリリースの形で違い、プラグインなら検証用マーケットプレイス、Web なら staging 環境である。どちらも
「本番ブランチ宛ての開いた PR を集めた検証用ブランチを組み、それを検証環境へ配る」の形で、違うのは配り方だけである。そのため、検証用ブランチの
組み立て（決定 9 の含める PR・fast-forward・衝突の止め方）を共通にし、配り方を種類で選ぶ。

| 種類 | 設定のキー | 配り方 |
| --- | --- | --- |
| `marketplace` | `ref`・`marketplace` | 検証用ブランチの `marketplace.json` の名前とバージョンを書き換えたコミットを足す（決定 9） |
| `deploy` | `ref`・`command`・`timeout_seconds` | 検証用ブランチを push した後、`{ref}`・`{head}` を置き換えたコマンドを流す。`marketplace.json` を読まない |

検証の行が無いリポジトリは検証環境を使わず、`verify-ref` は `not_applicable` を返す。種類が表に無ければ、取れる値を理由に出して止まる（受け入れ条件 20）。
`deploy` の先（どのクラウドへ、どう配るか）は要求の「含まない」で、ndf-next はコマンドを流すだけである。コマンドは本番ブランチの設定ファイルにあるものだけを流す。
置き場を `delivery` にするのは、リリース対象をどこへどう届けるかを既に持つ設定で（`channel`・`trigger`）、検証環境もその 1 つの届け先だからである。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 検証用マーケットプレイスだけを持つ（決定 9 の superseded の部分） | 採らない。Web の staging 環境を書けない |
| 種類ごとに検証用ブランチの組み立てを分ける | 採らない。含める PR と fast-forward の規則が 2 か所になる |
| GitHub の Environments の「Require deployments to succeed」を必須にする（STD6 の規則の 1 つ） | 採らない。ruleset の書き換えが要り、マージの前に試す場所であってマージの条件ではない |
| 組み立てを共通にし、配り方を種類で選ぶ | 採る |

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD4 / STD5 / STD6

根拠: Value 4 / Value 5 / Value 11（MVV 版 7）

### 決定 22: バージョン上げの対象をプラグインに決め打たないため、`.ndf/release.json` の `version_store` でバージョンの置き場のアダプタを選び、#3615 と境を分ける

#3587 のバージョン上げ（`bump.py`、d7d5fe0b1 の時点）は、`write_bump`・`allowed_paths`・`planned_for`・`Bumper._tip`・`Bumper._plan` がどれも
`plugins.load`（`.claude-plugin/marketplace.json` の読み）を呼び、`plugin.json` のバージョンを上げる。`pr_check._versions`・`cli._plugin_of`・
`gather.version_at` も同じである。`marketplace.json` を持たないリポジトリでは、ここで `PluginsError` が原因 `precondition` になる（#3615 の点検 1）。
設定ファイルの手順（`steps`）と `version_files` は既にリポジトリごとの書き換えを受けるが、今のバージョンを読む元とリリースの単位がプラグインに決め打たれている。

バージョンの置き場をアダプタのインタフェースにし、設定ファイルで選ぶ。

| メソッド | 意味 |
| --- | --- |
| `units()` | リリースの単位（名前と、変化を数える根のディレクトリ）の一覧 |
| `read(ref, unit)` | ある ref の単位のバージョン |
| `write(unit, new)` | 単位のバージョンを書き、書いたパスを返す |
| `paths()` | バージョン上げの PR が変えてよいパス（I17 の判定に使う） |

| `kind` | 持つもの | この課題で作るか |
| --- | --- | --- |
| `plugin` | 今の `plugins.py`（マーケットプレイスの登録・`plugin.json`・`version_files`）を包む | 作る（ai-plugins が選ぶ） |
| `json` | `path`・`key`・`name`。1 つの JSON のファイルの 1 つの鍵（`package.json`・`composer.json`・`app.json` など） | 作る（受け入れ条件 21） |
| ほか（`pubspec.yaml`・Gradle・タグだけ など） | — | #3615 が足す |

`bump`・`pr-check`・`confirm`・`planned_for` はインタフェースだけを呼び、`plugins.py` を直接呼ばない（I18）。置き場が無ければ `trunk` の `bump` は止まる。
設定ファイルの手順（`steps` の `production`）はインタフェースの `write` の後に今のまま流れ、`package-lock.json` のような付随のファイルを書き換える。

**#3615 との境。**

| 決めること | 持ち主 |
| --- | --- |
| いつ・誰がバージョンを上げるか（`trunk` の CD・バージョン上げの PR・キューとの関係） | #3572（決定 7・20） |
| バージョンの置き場のアダプタのインタフェースと、設定のキー `version_store` | #3572（この決定） |
| アダプタ `plugin`・`json` | #3572 |
| ほかのリリースの形のアダプタ（今の NDF のリリースの形 11 種との対応） | #3615 |
| タグ・CHANGELOG の見出し・リリースブランチの名前の形を設定ファイルで変えること | #3615（それまでは今の `<名前>--v<バージョン>` の形） |
| 今の行の承認資料・リリースブランチの組み立て・タグ（`assemble`・`gather`・`tagging`）をアダプタのインタフェースへ移すこと | #3615（この課題では今のまま） |
| リリースを利用者のリポジトリから起動する経路（workflow のテンプレート） | #3615 |

この課題の範囲で CD を起動する workflow は ai-plugins の `.github/workflows/ndf-next-release.yml` だけである。そのため要求の受け入れ条件 21
（`marketplace.json` を持たないリポジトリで「CD のバージョン上げを流す」）は、サブコマンド `ndf release-steps bump` を置き場 `json` で流すことで満たす
（設計文書の「テスト設計」の受け入れ条件 21 の行。アダプタを差し替えた単体テストと、コードの検索の結果）。ほかのリポジトリが main への push で
`bump`・`publish` を呼ぶ workflow を持つのは #3615 のテンプレートが入った後で、それまでは利用者が自分の workflow からサブコマンドを呼ぶ。
| 配らないファイルの既定を `test-files.json` から作ること | #3615 |

| 比べた候補 | 採否と理由 |
| --- | --- |
| `version_files` と手順だけで受け、置き場のアダプタを作らない | 採らない。今のバージョンを読む元とリリースの単位が残り、`marketplace.json` を読む処理が消えない |
| release-please の release type（PRD1）をそのまま使う | 採らない。上げ幅をコミットの書式から読む（LIB1）。種類で選ぶ形だけを倣う |
| リリースの形のアダプタ一式をこの課題で作る | 採らない。#3615 の範囲で、11 種との対応の表を持たずに作ると #3615 と食い違う |
| インタフェースと 2 つのアダプタをこの課題で作り、残りを #3615 へ | 採る |

- 作り方: 自前で作る・標準に倣う
- 比べた候補: PRD1 / LIB1

根拠: Value 4 / Value 9 / Value 11（MVV 版 7）

### 決定 23: ruleset へ merge queue を足すのは 11.0.0 の入れ替えの PR と同じ時点にし、必須のチェックの workflow への `merge_group` はこの課題の実装で足す

必須のチェックを出す workflow は `merge_group` を持たない（確かめ 6）。キューを足す前に `merge_group` が無ければ、キューの一時のコミットに
チェックが報告されず、マージが失敗する（STD7・STD8）。`merge_group` の追加はキューが無い間は何も起こさないため、この課題の実装で先に足す。
PR の本文や宛先を読むチェック（`pr-body-decisions-check`・`guard` など）は、キューの一時のコミットでは PR の時点の結論を引き継いで通す。
キューに入れられるのは PR の時点で必須のチェックを満たした PR だけだからである（STD7）。

ruleset の書き換えは、ai-plugins の設定ファイルを `trunk`・`merge-queue` へ書き換える入れ替えの PR と同じ時点にする。キューを先に足すと、
今の NDF のリリース（develop から main への PR を `production_merge.admin` で迂回してマージする）がキューの規則に当たる。これは 11.0.0 まで今の運用を
変えない前提（要求の前提 10）と食い違う。実行は承認ゲートを通らない外部の系の設定の変更で、実行の前に止めて人へ戻す（設計文書の「人へ残す判断」の C2 の行）。

足す設定の案:

| 項目 | 値 | 理由 |
| --- | --- | --- |
| マージの方式 | `MERGE` | ruleset の `allowed_merge_methods` が `merge` だけ（確かめ 6） |
| グループの作り方 | `ALLGREEN`（すべての PR が必須のチェックを満たすこと） | 落ちた PR を組み合わせに含めない |
| 同時に組む数（build concurrency） | 5 | 並列は 6 本まで（同時に走らせるレーンの上限）。1 つの組み合わせの CI が 17 個のチェックを持つ |
| 1 回にマージする数 | 最小 1・最大 5、待ち 5 分 | 1 つでも待たずに出す。同時の承認をまとめて確かめる |
| チェックの待ち | 60 分 | CI の 1 回（必須のチェック 17 個）が収まる値。入れ替えの後の実測で見直す |
| PR の規則の承認の数 | 1 から 0 へ | NDF の承認ゲートの承認は GitHub のレビューの承認ではなく、承認 1 のままでは `land` が投入できない（決定 17） |

この値は ai-plugins の値で、ndf-next のコードに持ち込まない。ndf-next は ruleset に merge queue があるかだけを読む（決定 17）。

- 作り方: ライブラリやサービスを使う
- 比べた候補: STD7 / STD8 / LIB3

根拠: Value 3 / Value 12 / C2（MVV 版 7）

### 決定 24: #3587 を閉じずに直し、d7d5fe0b1 に足す変更を次の一覧にする

#3587（宛先 develop、head d7d5fe0b1、66 ファイル・73 コミット。`git diff --stat origin/develop...origin/sprint/lrel-lock` と
`git log --oneline origin/develop..origin/sprint/lrel-lock | wc -l`、2026-11-15）は、決定 1〜4・6〜8・10〜12 の実装と、取り込み方の表の 3 行
（`trunk`・`merge_base_branch`・`release_branch`）を既に持つ。この差分は cross-review と cross-refactoring を通った。閉じて出し直すと、残す部分の
レビューとリファクタリングをやり直す。ndf-next は 11.0.0 の入れ替えまで develop で開発するため、宛先 develop も変えない。
次の変更を同じ PR へ足し、変わった差分を再びレビューへ通す。

| # | 変更 | 決定 |
| --- | --- | --- |
| 1 | `branch-flows.json` の既定の行を `merge_base_branch` へ戻す。`declaration.require_github_flow` を外し、`assemble`・`bump`・`cli` の呼び出しを行ごとの振る舞いの選択に置き換える | 16 |
| 2 | 今の行の経路（`assemble` のリリースブランチの組み立てと `--channel`・`--source`、`land._sync`、結果の `sync_pr`）を develop の形へ戻し、`trunk` の行でだけ外す | 6・10・16 |
| 3 | `production_merge.method` のスキーマ・`merge_method.py`・アダプタの `branch_rules`・`land` の照らし合わせ（原因 `precondition`） | 17 |
| 4 | アダプタの `enqueue`・`queue_entry`、`land` のキューへの投入と待ち、原因 `queue_removed`、次の手の表 | 3・18 |
| 5 | `land` の先端の取り込みの確かめ、原因 `behind` | 19 |
| 6 | `land` で `merge-queue` の本番の PR に迂回を呼ばない。`land_bump` の迂回の条件にバージョンの置き場の判定を使う | 20 |
| 7 | `version_store.py`（インタフェースと `plugin`・`json`）、`release.json` のスキーマの `version_store`。`bump.py`・`pr_check.py`・`cli._plugin_of`・`planned_for` をインタフェースへ移す | 22 |
| 8 | `delivery` の検証の行の `verify.kind`、`verify_ref` の `deploy` の配り方 | 21 |
| 9 | 必須のチェックを出す workflow へ `merge_group` を足し、PR の本文を読むチェックの引き継ぎ | 23 |
| 10 | テストを足す（設計文書の「テスト設計」の受け入れ条件 18・20〜26 の行）。今の行のテスト（リリースブランチの組み立て・同期の PR）を develop から戻す | 16〜23 |
| 11 | #3613 の改名が develop へ入った後に develop を取り込み、#3587 が足した行・値・スキーマの名前を `release_strategy`・`trunk` へそろえる | 26 |

| 比べた候補 | 採否と理由 |
| --- | --- |
| #3587 を閉じて main から出し直す | 採らない。残す部分のレビューとリファクタリングをやり直し、ndf-next の開発の場所（develop）とも合わない |
| #3587 を直して出す | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 2 / Value 5（MVV 版 7）

### 決定 25: #3119 の設計の決定 21 を、ai-plugins の設定ファイルが `trunk` の行を選び、表は 3 行を残す決定へ見直す

#3119 の決定 21 は、ai-plugins の設定ファイルを行 `trunk` にすることを決めている。見直すのは次の 3 点である。

1. 取り込み方の表は `merge_base_branch`・`release_branch` を残し、`trunk` は選ぶ行の 1 つである（決定 16）
2. マージの前の検証は、ai-plugins が選ぶ検証環境の種類 `marketplace` である（決定 21）
3. ai-plugins のマージの方式は `merge-queue` で、ruleset の変更は入れ替えの PR と同じ時点に人の承認の後に行う（決定 23）

設定ファイルの書き換えを 11.0.0 の入れ替えと同じ時点にすることは変えない。差分は同じ設計 PR の `issues/issue-3119-design.md` の決定 21 にある。

- 作り方: 標準に倣う
- 比べた候補: STD3 / STD7

根拠: Value 4 / P1（MVV 版 7）

### 決定 26: ブランチ戦略のキーと値は #3613 の改名の後の名前で書き、改名と移行は #3613 が持つ

ブランチ戦略のキーは `.ndf/worktree.json` の `branch_flow.release_strategy` である。値と取り込み方の表の行の名前は次の 3 つである
（利用者の決定、2026-11-16。#3613）。

- `merge_base_branch`: ベースブランチを丸ごと本番ブランチへマージする
- `release_branch`: 承認したスプリントだけを、本番ブランチから作ったリリースブランチで出す
- `trunk`: スプリントの PR を直接本番ブランチへ入れる

この設計の文書は、どの節もこの名前で書く。

| 何を決めるか | 持つ課題 |
| --- | --- |
| develop にある 2 行のキー・値・行の名前の改名、スキーマの改名、利用者の設定を新しいキーへ書き換える移行のコマンド、移行の後に残る旧いキーで止める読み取り | #3613 |
| 行 `trunk` を新しい名前で足すこと、`trunk` の行のリリースの振る舞い（決定 5〜7・9・10・16〜23） | #3572 |

#3587 は行 `trunk` を改名の前の名前で持つ。#3613 の改名が develop へ入った後に develop を #3587 へ取り込み、#3587 が足した行・値・スキーマの名前を
新しい名前へそろえる（決定 24 の 11 行目）。#3613 の改名より先に #3587 をマージしない。先にマージすると、develop に旧い名前の行と新しい名前の行が混ざる。
この順序は要求に無い課題どうしの制約で、設計承認ゲートで人が見る（設計文書の「人へ残す判断」）。

要求の本文と利用者の指示の原文は改名の前の名前で書かれている。要求は書き換えず、設計文書の「適用範囲」の読み替えの表
（`promote-whole` → `merge_base_branch`・`promote-selected` → `release_branch`・`github-flow` → `trunk`）で読む。

| 比べた候補 | 採否と理由 |
| --- | --- |
| #3572 の実装で改名も行う | 採らない。改名と移行のコマンドは利用者の設定の書き換え（C7）を伴い、#3613 が 1 か所で持つ（Value 11） |
| 改名の前の名前で書き、改名の後に文書を直す | 採らない。承認の資料と実装の名前が食い違い、読み手が 2 つの名前を対応づける手間が残る |
| #3613 の改名の後の名前で書き、#3587 は改名の後に取り込む | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 11 / Value 14 / C7（MVV 版 7）
