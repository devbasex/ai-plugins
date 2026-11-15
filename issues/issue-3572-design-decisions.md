# #3572 の決定の記録: リポジトリごとのブランチ戦略で承認済みのコミットだけを本番へ入れ、同時の承認は merge queue で確かめる

設計文書 [issue-3572-design.md](issue-3572-design.md) の決定の記録（決定 1〜31）である。不変条件（I1〜I18）・外部の調査（STD1〜STD13・PRD1〜PRD4・LIB1〜LIB4）・
用語は、設計文書の同じ名前の章の意味で使う。確かめ 1〜8 は [issue-3572-design-evidence.md](issue-3572-design-evidence.md) にある。

置き換えた決定は消さずに残し、見出しの直後に状態の行（`状態: superseded（決定 N）` か `状態: 一部 superseded（…は決定 N）`）を置く。
状態の行の無い決定は有効である。

## 決定の記録

### 決定 1: 承認の外のコミットが積まれる経路を無くすため、リリースのワークフローから worker を外し、サブコマンドを並べた 4 ステップにする

リリースのワークフローの `work`（`{{worker}} release`）は、worker にリリースの作業全体を任せるため、CI が落ちたときにコードを直して push する
判断も worker に残る。リリース 8・14 はこの裁量で起きた。ステップを `ndf release-steps` の `merge` → `confirm` → `record` → `cleanup` だけにすれば、
承認の後にコードを変える主体がワークフローの中に居なくなる。`confirm` は CD のバージョンアップとタグを待って確かめるだけで、コードもブランチも変えない。
この 4 ステップはどのブランチ戦略・どのマージの方式でも同じで、戦略と方式の違いは `merge` と `confirm` の中で選ぶ（決定 16〜19）。
手順書だけで止める形は 2 回とも止まらなかったため採らない（要求の「含まない」）。ruleset でブランチをロックする形は、利用者の「ロックの方針」で採らない。
承認するのは本番の PR の head で、マージと merge queue への追加は head の一致の確認付き（`expect_head`・`expectedHeadOid`）で実行する。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD1 / STD10 / STD3

根拠: Value 3 / Value 7（MVV 版 7）

### 決定 2: 本番の PR を修正用の worker を含むワークフローでマージしないため、`merge` のコンポーネントの直前に `release-guard` を置き、テンプレートの検査で縛る

`release_plan.forbidden(plan)` は、`release: true` のプランについて次の 4 つを返す。

- worker を起動するステップ
- `push` を使うステップ（`push`・`fix-push`・`ci-fix`・`fix-base`・`base-sync`）
- 次のバージョンの用意のステップ
- 最初が `ndf release-steps merge` でないこと

`release: true` でないテンプレートについては、`merge` のコンポーネント（`check.json` では `merge-gate`）の直前に `release-guard` が無いことを返す。

`trunk` 戦略ではスプリントの PR が本番の PR を兼ね、既存のブランチ戦略ではリリースブランチの PR が本番の PR である。どちらもマージ先が本番ブランチの PR で、
`release-guard` はマージ先で本番の PR を見分ける。承認の前の CI の修正はこれらのテンプレートで行ってよく（承認の対象はその後の head である）、
マージだけをデプロイの工程へ渡せば足りる。本番の PR なら終了コード 3 を原因 `production_pr` にして `end` へ進め、結果に「デプロイの工程で実行する」を出す。

| 比べた候補 | 採否と理由 |
| --- | --- |
| `release-guard` を `merge.json` の先頭に置く | 採らない。承認の前の CI の修正が通らなくなり、`check`・`close` のテンプレートの穴も残る |
| プランを読む核（`core/plan/model.py`）や `ndf ci merge` で同じ判定を強制する | 採らない。核と CI の領域がリリースの語を持ち、リリースのドメインの判定を外へ漏らす |
| コンポーネント `release-guard` をマージの直前に置き、テンプレートの検査で全テンプレートを縛る | 採る |

- 作り方: 自前で作る
- 比べた候補: STD3

根拠: Value 3 / Value 11（MVV 版 7）

### 決定 3: 止まった本番の PR の次の手を `rerun` と `reject` の 2 つにし、同じ head で通らない止まり方では `reject` だけを出す

| 次の手 | 何をするか | 既定の手（`outputs.suggested`）になる止まった理由 |
| --- | --- | --- |
| `rerun` | 同じ承認・同じ head のまま `merge` を再実行する | `infra_wait`・`ci_pending`（チェックの一時的な失敗・待ちの切れ） |
| `reject`（差し戻し） | 本番の PR を開いたまま、修正を同じ PR へ入れ、新しい head で承認を取り直す | `test_failed`・`check_failed`・`refused`・`queue_removed`・`behind` |

`refused`（head が動いた）・`queue_removed`（キューから外れた）・`behind`（`strict` で HEAD を含まない）では、`routes` に `reject` だけを出す。
`refused` は PR の head が承認済みのコミットと違い、同じ承認で再実行しても GitHub が同じく断る。キューは同じ head では同じ組み合わせで落ち（I16）、
`behind` は head を進めない限り解けないためである（決定 18・19）。ほかの止まった理由では 2 つとも出す（受け入れ条件 4）。
`rerun` は止まった理由から機械的に決まるため、オーガナイザーか一時レーンが実行してよい。`reject` の後の修正は承認の前の工程（スプリントの経路）で
入れ、新しい head で `assemble`・`present` から取り直す。本番の PR を閉じて次のバージョンへ回す手は持たない。

- 作り方: 標準に倣う
- 比べた候補: STD3 / STD7 / PRD1

根拠: Value 1 / Value 7（MVV 版 7）

### 決定 4: 承認ゲートを質問し直さずに人のマージを承認として扱うため、検知を工程の承認ゲートの待ちの読み直しに置く

リリース承認ゲートの待ちは、回答の確認のために本番の PR を GitHub から読み直している（`github_observe`）。この読み直しで PR が
マージ済みなら、マージした head への承認（経路 `github-merge`、回答者は `merged_by` のログイン）とする。キューへ入れてマージされた PR では、
`merged_by` はキューへ入れた人である（確かめ 7）。キューへ入れただけでまだマージされていない PR は承認として扱わず、待ちを続ける。
判定は `merge_decide` が持つ。承認ゲートは `release-steps answer --route github-merge` を呼んで承認の記録を書かせる。リリースのワークフローは
最初の `merge` でマージ済みの PR から再開し（`land._resume_merged`）、人のマージの記録を行う。`pull_request` の `closed` で動く workflow は、
承認ゲートの回答を書く経路を持たないため採らない。人のマージは、承認ゲートで別の head へ与えた承認を引き継がない（受け入れ条件 8）。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD2 / STD7

根拠: Value 1 / Value 3（MVV 版 7）

### 決定 5: GitHub flow を選べるようにするため、ブランチ戦略の一覧に `trunk` 戦略を足す

状態: 一部 superseded（`trunk` 戦略を既定にすること・ndf-next のリリースで `trunk` だけを扱うこと・11.0.0 の入れ替えで `merge_base_branch`・`release_branch` を表から外すことは決定 16）

| 列 | 値 | 意味 |
| --- | --- | --- |
| `release_strategy` | `trunk` | スプリントの PR を本番ブランチへ直接マージする |
| `start` | `["production"]` | スプリントブランチは本番ブランチから切る |
| `doc_prs` | `["sprint"]` | 設計 PR と確定仕様化の PR はスプリントブランチへ（文書だけで本番へマージしてリリースを起こさない） |
| `integrate` | `["release"]` | スプリントブランチは、スプリントの PR のマージ（リリース）で本番ブランチへ入る |
| `keeps_sprint` | `false` | マージした時点でスプリントブランチを残さない |
| `release_from` | `production` | バージョンアップの PR のブランチは本番ブランチから作る |
| `release_units` | `false` | 集めるスプリントを選ばない（1 つのスプリントの PR が 1 回のリリース） |
| `review_kinds` | `["impl", "sprint", "design", "spec"]` | リリースブランチの PR（`release`）の行を持たない |

この戦略を選ぶ設定ファイル（`.ndf/worktree.json`）は、`base_branch` と `production_branch` を同じ本番ブランチにする。

（superseded の部分）`trunk` 戦略を既定にし、`merge_base_branch`・`release_branch` は ndf-next のリリースで扱わず、11.0.0 の入れ替えで表から外す。
それまで ndf-next のリリースのサブコマンドは、設定ファイルの戦略が `trunk` でなければ原因 `precondition` で止まる。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 設定ファイルを変えず、`merge_base_branch` のまま `base_branch` を main にする | 採らない。`merge_base_branch` は文書の PR を `base`（= main）へ入れ、文書だけのマージでリリースが起きる。行の意味と運用が食い違う |
| 常設のリリースブランチの行（`standing`） | 採らない。GitHub flow を選ぶリポジトリは、スプリントのマージ先を本番ブランチにする |
| `trunk` 戦略を足す | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 4 / Value 11 / P1（MVV 版 7）

### 決定 6: `trunk` 戦略で main へのマージをそのままリリースにするため、スプリントの PR を本番の PR にし、承認資料をその PR の本文に書く

リリースのためのブランチを作成せず、スプリントの PR（main 宛て）がリリース承認ゲートの対象になる。マージ＝リリース承認＝マージ承認である。
`assemble` はブランチとバージョンアップのコミットを作らず、範囲（main の HEAD..PR の head）から承認資料を作成して PR の本文の承認資料のセクションへ書く。
本文の書き換えは head を動かさないため、承認の対象を変えない。予定のバージョンはバージョン種別のラベル（決定 7）から出し、確定のバージョンは CD が決める。
この振る舞いは `trunk` 戦略だけのもので、既存のブランチ戦略の `assemble`（リリースブランチの組み立て・開発版チャネルの組み立て `--channel dev`）は今のまま残す（決定 16）。

- 作り方: 標準に倣う
- 比べた候補: STD3 / PRD1

根拠: Value 1 / Value 5（MVV 版 7）

### 決定 7: `trunk` 戦略でバージョンをマージの時点に CD が上げるため、バージョン種別をラベルで受け、バージョンアップの PR を head の一致の確認付きで main へ入れてからタグを作る

状態: 一部 superseded（手順 3 の書き換え先を `plugin.json` に決め打つ部分は決定 22、手順 4 のマージとキューの関係は決定 20）

main の ruleset（確かめ 6）は、迂回を管理者の役割（`RepositoryRole` 5）だけに、形 `pull_request` で許す。PR を通さない main への
push には迂回が効かず、承認 1 と必須のチェックが掛かる。そのため CD はバージョンアップのコミットを main へ直接 push せず、バージョンアップの PR で入れる。

1. main への push で `bump` が動く。最後のバージョンアップのコミットのバージョンにタグが無ければ、先にそのコミットを `publish` へ渡し、その後で範囲を読み直す。範囲（前のバージョンアップのコミットの分岐元..main の HEAD）にリリース対象の変化があれば、バージョン種別を決める。main への push の run は workflow の `concurrency` で 1 本ずつ実行する。バージョンアップの PR のマージからタグまでの間に入った PR も、同じ run か次の run でバージョンアップへ進み、後の push を待たない
2. バージョン種別は、範囲にマージされた PR のラベル（`release:major`・`release:minor`・`release:patch`）の最大である。ラベルが無い PR は PATCH とする（要求の起票者の推定 S8 を採る。付け忘れでマージを止めないため）
3. main の HEAD から `release/v<バージョン>` を作り、バージョンの保存先（決定 22）と CHANGELOG のバージョンのセクション（決定 11）と手順の書き先だけを変えたコミットを足す。バージョンアップの PR は `NDF_RELEASE_TOKEN` で作る（`github.token` で作ると CI が起動しない）
4. `bump_decide` が差分を保存先・CHANGELOG・手順の書き先に限られると判定し、必須のチェックの後にマージする（キューとの関係は決定 20）
5. バージョンアップの PR のマージの push で、`bump` は HEAD のバージョンにタグが無いと読んで上げず、`publish` がタグと GitHub Release を作る

同じバージョンの PR が開いていれば使い直し（I8）、`concurrency` で workflow を 1 本ずつ実行する。PR の検査（`pr-check`）は戦略が `trunk` のときだけ働き、
既存のブランチ戦略ではリリースブランチの PR がバージョンを上げるため 0 を返して何も見ない。

| 比べた候補 | 採否と理由 |
| --- | --- |
| CD が main へ直接 push する | 採らない。ruleset の迂回の形が `pull_request` で、直接 push は断られる |
| ruleset の迂回の形を `always` にする | 採らない。ruleset の書き換えで、承認ゲートを通らない機械の push を許す |
| `plugin.json` から `version` を外し、コミットの SHA で更新を検知させる（STD4） | 採らない。バージョンを読むスクリプトがすべて書き換わり、利用者がバージョンで過去へ戻れない |
| release-please-action（LIB1）・semantic-release（LIB2） | 採らない。バージョン種別をコミットの書式から読み、このリポジトリの書式と CHANGELOG の形に合わない |
| バージョンアップの PR を自前の `bump` で作る | 採る |

- 作り方: 標準に倣う・自前で作る
- 比べた候補: PRD1 / PRD2 / LIB1 / LIB2 / STD4 / STD6

根拠: Value 3 / Value 5 / C2 / P1（MVV 版 7）

### 決定 8: 人のマージの記録を承認の記録と同じ場所で読めるよう、本番の PR のコメントに 1 回だけ書く

承認の記録と迂回の記録は本番の PR のコメントにあり、リリース記録とリリース後テストもそこから読む。人のマージの記録も同じ PR に置けば、
1 つの PR を読めば承認の経路が分かる。書くのは `land` のマージ済みからの再開の 1 か所で、マーカーのあるコメントがあれば書かない。
キューを通したマージでは、マージの方式（`merge-queue`）とマージコミット（キューの一時マージコミット）も同じコメントに書く。

- 作り方: 自前で作る
- 比べた候補: STD2

根拠: Value 13 / Value 14（MVV 版 7）

### 決定 9: マージの前に試せるようにするため、本番ブランチ宛ての開いた PR を集めた検証ブランチを workflow が作成し、検証マーケットプレイスがそれを指す

状態: 一部 superseded（検証環境を検証マーケットプレイスの 1 種類に決め打つ部分は決定 21。下の表は種類 `marketplace` の値として残る）

#188（2026-08-31）で main と develop に分けたのは、マージした時点で常用する利用者へ届くのを避け、届ける前に試す場所を持つためである。
`trunk` 戦略ではマージがリリースになるため、試す場所をマージの前へ移す。Claude Code にはリリースチャネルの概念が無く、名前の違う 2 つの
マーケットプレイスが違う ref を指す形を勧めている（STD4）。確かめ 1〜3 で、同じリポジトリを違う名前と ref で登録すると両方が残り、
作り直しが更新で入り、正式版の登録が変わらないことを見た。

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| ブランチの名前 | 設定ファイルの `verify.ref`（ai-plugins は `verify/next`） | 名前で用途が分かり、`release/v` と `sprint/` の規則と重ならない |
| マーケットプレイスの名前 | 設定ファイルの `verify.marketplace`（ai-plugins は `ai-plugins-next`） | 1 つの名前に 1 つの登録しか持てない（STD5）。正式版と同じ名前は登録できないことを確かめた |
| 作成する主体と時期 | `ndf-next-release.yml` が、本番ブランチ宛ての PR の作成・更新・閉じ（`pull_request_target`）と手動の起動で `verify-ref` を実行する | PR のイベントが中身の変わる時点である |
| 含める PR | 本番ブランチ宛ての開いた PR のうち、下書きでなく、head が検証ブランチでもバージョンアップの PR でもフォークでもないもの（番号の順） | 衝突すれば作成せずに止め、衝突した PR を返す |
| 検証のバージョン | `<main のバージョン>-next.<組んだ時刻 UTC の YYYYMMDDHHMMSS>` | バージョンの変わらない ref の移動は利用者のキャッシュに残る（STD4） |
| 進め方 | 前の検証ブランチの head を親に足したコミットで fast-forward だけで進める | 強制 push（C4）を使わない。本番ブランチへは戻さない（I9・I12） |
| 利用者の手順 | `claude plugin marketplace add devbasex/ai-plugins#verify/next` で登録し、試す間は `claude plugin disable ndf@ai-plugins` で正式版を止める | 同じ名前のプラグインが 2 つとも有効になることを確かめた |

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: STD4 / STD5

根拠: Value 5 / Value 13 / P1（MVV 版 7）

### 決定 10: `trunk` 戦略では同期の PR を作らない

`trunk` 戦略では `land._sync`（本番ブランチからベースブランチ宛ての PR）を呼ばず、結果に `sync_pr` を出さない。
develop が無く、main へ入ったバージョンアップのコミットを戻す先が無いためである。既存のブランチ戦略（ベースブランチが本番ブランチと違う）では今のまま作る（要求の前提 9。決定 16）。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 6 / Value 11（MVV 版 7）

### 決定 11: `CHANGELOG.md` を残し、CD がバージョンアップのコミットでバージョンのセクションを書き、GitHub Release の本文を同じセクションから作る

PR は `CHANGELOG.md` を触らない。バージョンアップのコミット（決定 7）で、範囲に入った PR から今の `changelog_decide` がバージョンのセクションを作成する。
GitHub Release の本文は今の `tagging` のとおりそのセクションから作り、同じ元から 2 つを作る。配らないもの（入れ替えの前の ndf-next）のセクションは書かない。
セクションの見出しの形を設定ファイルで変えるのは #3615 の範囲である（決定 22）。

| 観点 | 残して CD が書く | 廃止する |
| --- | --- | --- |
| 読むスクリプト | 書き換えない。テストを除いて 25 ファイルが読む（`rg -l CHANGELOG --type py plugins scripts -g '!**/tests/**'`、517a733d6 の時点。下の表） | 25 ファイルを GitHub Release の API で読む形へ書き換える。うち `plugins/ndf/` の 14 ファイルは 11.0.0 まで変えられない |
| 手元での検索 | `git grep`・`git show <ref>:CHANGELOG.md` でバージョンの変化を読める | 読むたびに API を呼ぶ。2026-11-15 にも GraphQL の利用上限に当たった |
| 重複 | GitHub Release の本文と同じ中身を 2 か所に持つ。ただし CD が同じセクションから作るため食い違わない | 正本が GitHub Release の 1 か所になる |
| PR どうしの衝突 | PR が触らないため起きない | 起きない |

廃止すると書き換える 25 ファイル:

| 領域 | ファイル |
| --- | --- |
| ndf-next のリリース（5） | `plugins/ndf-next/src/ndf/domains/release/` の `assemble.py`・`changelog_decide.py`・`declaration.py`・`__init__.py`・`tagging.py` |
| 今の NDF（14） | `plugins/ndf/scripts/` の `design-match.py`・`doc-lint.py`・`instructions_lib/versions.py`・`pr-steps.py`・`release_lib/names.py`・`release_lib/others.py`・`release_lib/port_mark.py`・`release_lib/promotion.py`・`release_lib/publish.py`・`release_lib/replaced.py`・`release_lib/version_merge.py`・`release-steps.py`・`supervise_lib/prompts.py`、`plugins/ndf/skills/cross-review/scripts/review_lib/review_focus.py` |
| リポジトリの検査と計測（6） | `scripts/check-doc-line-limit.py`・`scripts/check-doc-staleness.py`・`scripts/lib/release_map.py`・`scripts/measure/claude-p-usage.py`・`scripts/token-usage.py`・`scripts/token-usage-snapshot.py` |

残す形を採る。重複は同じ元から作ることで食い違いを防げ、廃止の書き換えは 11.0.0 まで終えられないためである。採否は P1 として
設計承認ゲートで人が見る（設計文書の「人へ残す判断」）。

- 作り方: 標準に倣う
- 比べた候補: PRD1 / LIB1

根拠: Value 2 / Value 11 / P1（MVV 版 7）

### 決定 12: `AGENTS.md` とバージョンの正本の書き直しは、11.0.0 からの運用を正本の別のセクションに書き、`AGENTS.md` からはそのセクションを指す 1 段落だけにする

今の NDF は 11.0.0 の入れ替えまで develop と main で動くため、今の運用の記述を書き換えると、入れ替えまでの読み手に食い違いが生じる。

| 文書 | この課題の実装で書くこと | 11.0.0 の入れ替え（#3304）で行うこと |
| --- | --- | --- |
| `docs/versioning-and-distribution.md` | セクション「11.0.0 からの運用（GitHub flow）」を足す。マージした変更が利用者へ届くのは main へのマージの時点、バージョンとタグは CD がマージの時点に作る、マージの前に試すには検証マーケットプレイスを登録する（手順は決定 9 の行）、同時に承認された PR の組み合わせは merge queue が確かめる。#188 で main と develop に分けた理由と、それを検証環境で受けることを書く | develop と開発版チャネルのセクションを外し、足したセクションを本文にする |
| `AGENTS.md` の「版とリリースの方針」 | 「11.0.0 の入れ替えで GitHub flow へ移る」1 段落と、正本のセクションへのリンク | チャネルの表を GitHub flow の形へ書き換える |

今の `AGENTS.md` の「取得元の登録に ref は保存されない」は、確かめ 1 の登録（`known_marketplaces.json` に `ref` が残る）と食い違う。
そのため、同じ実装で正本の該当の段落と合わせて直す。ai-plugins がどの戦略を選ぶかは ai-plugins の設定ファイルの値で、NDF の製品の仕様として書かない。

- 作り方: 標準に倣う
- 比べた候補: STD4

根拠: Value 4 / Value 14 / C7（MVV 版 7）

### 決定 13: #3576 を閉じる

#3576 は常設のリリースブランチのブランチ戦略（`standing`）と、リリースブランチから develop への同期の PR を足す課題である。
ブランチ戦略の一覧の 2 つの戦略を残すこと（決定 16）は #3576 の扱いを変えない。既存の 2 つの戦略はどちらも常設のリリースブランチを持たず、
develop を持つリポジトリの同期の PR は既存のブランチ戦略の `land._sync` が既に作る。`trunk` 戦略と検証環境は #3572 の受け入れ条件 12・15・20 が持つため、
#3576 を書き換えて残すと同じ条件を 2 つの課題に持つ。実装のステップで、理由を本文に書いて閉じる。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 6 / Value 11（MVV 版 7）

### 決定 14: #3119 の設計の決定 21 を、ai-plugins の設定ファイルを `trunk` 戦略にする決定へ置き換える

状態: superseded（決定 25）

#3119 の設計の決定 21（常設のリリースブランチ `standing`）を、`trunk` 戦略を ai-plugins の設定ファイルにする決定へ書き換える。
設定ファイルの書き換えは 11.0.0 の入れ替えと同じ時点で行い、ブランチ戦略の一覧から `merge_base_branch`・`release_branch` を外すのも同じ時点とする。

根拠: Value 4 / P1（MVV 版 7）

### 決定 15: #3587 を閉じずに直す（33 ファイルの残す・外すの一覧）

状態: superseded（決定 24）

#3587（当時の head 441dc236e、33 ファイル）を閉じずに直し、常設のリリースブランチの部分（`prepare.py`・`assemble.py` の作り直し）を外し、
決定 5〜7・9・10 の実装を足す。

根拠: Value 2 / Value 5（MVV 版 7）

### 決定 16: ブランチ戦略をリポジトリごとに選べるようにするため、ブランチ戦略の一覧の 3 つの戦略を残して既定を `merge_base_branch` のままにし、リリースのサブコマンドは戦略ごとに振る舞いを選ぶ

状態: 一部 superseded（`branch_flow` を `.ndf/worktree.json` に置く部分と、戦略が許す値をブランチ戦略の一覧に置く部分は決定 27・28。戦略ごとの振る舞いの表は有効）

ブランチ戦略はリポジトリの決まりで、NDF の制約にしない（#3119・#3121 の「リポジトリの決まりは設定の値で受ける」）。develop と main を分ける Web・アプリの
リポジトリは `merge_base_branch`・`release_branch` を選ぶ。GitHub flow を採るリポジトリは `trunk` を選ぶ。ai-plugins が `trunk` を選ぶのは
ai-plugins の設定ファイルの値である。既定の戦略（`branch_flow` の無い設定ファイルが使う行）は `merge_base_branch` のままにする。既定を変えると、
設定ファイルの無いリポジトリのスプリントの分岐元とマージ先が黙って変わるためである。

リリースのサブコマンドは、ブランチ戦略の一覧の列で振る舞いを選ぶ。`require_github_flow`（戦略が `trunk` でなければ止まる）は外す。

| 振る舞い | `trunk` | `merge_base_branch`・`release_branch` |
| --- | --- | --- |
| 本番の PR | スプリントの PR | リリースブランチの PR（今のまま） |
| `assemble` | 本文の承認資料のセクションだけ（決定 6） | リリースブランチの組み立て（今のまま） |
| 承認後のワークフロー | 4 ステップ（決定 1） | 同じ 4 ステップ |
| マージ | マージの方式が必須（決定 17） | 方式を書けば ruleset と突き合わせて検証してから使い、無ければ今のマージ |
| 同期の PR | 作らない（決定 10） | 今のまま作る |
| バージョン | CD がバージョンアップの PR で上げる（決定 7） | リリースブランチで上げる（今のまま） |
| `pr-check`・`bump` | 働く | 0 を返して何も見ない |
| `publish`（タグと GitHub Release） | main への push の workflow で、バージョンアップのコミットに作る（決定 7） | main への push の workflow で、本番の PR のマージコミットに、リリースブランチで上げたバージョンで作る（今の `tagging.publish` のまま） |
| `confirm` | バージョンアップの PR のマージ・タグ・GitHub Release を待つ | タグと GitHub Release を待つ |

今の workflow は、自分の run の中でマージした後に `publish` を続けてタグを作る。承認の後のマージはリリースのワークフローの `merge` が実行するため、その run が無い。
そのため、main への push で `bump` と `publish` を実行する workflow の仕事を既存のブランチ戦略にも当て、既存のブランチ戦略のタグの作り手を `publish` にする。`publish` は
あるタグを作らないため、手動の起動（`step=publish`）と重なっても 1 つしか作らない。

既存のブランチ戦略ではマージの方式を必須にしない。本番の PR はリリースブランチの PR で、1 度に 1 つしか開かない。組み合わせはベースブランチ（か選んで集めた
リリースブランチ）で既に CI を通っている。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 11.0.0 の入れ替えで 2 つの戦略を外し、ndf-next のリリースは `trunk` だけを扱う（決定 5 の superseded の部分） | 採らない。develop を持つリポジトリが ndf-next のリリースを使えなくなり、受けられる運用を減らす |
| `trunk` を既定の戦略にする | 採らない。設定ファイルの無いリポジトリの振る舞いが黙って変わる |
| 3 つの戦略を残し、戦略ごとに振る舞いを選ぶ | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 4 / Value 10 / P1（MVV 版 7）

### 決定 17: 同時に承認された PR の組み合わせを確かめてから入れるため、マージの方式を `.ndf/project.json` の `production_merge.method` で選び、本番ブランチの ruleset と突き合わせて検証する

状態: 一部 superseded（保存先を `.ndf/project.json` の `production_merge` にする部分と、その理由の段落は決定 27。検証の入力は決定 28。値・ruleset との検証・レビューの承認の数は有効）

GitHub flow ではスプリントの PR が直接本番ブランチへ入る。ほぼ同時に承認された A と B のうち後の B は、`strict` が偽なら A を取り込まずにマージできる。
そのため A+B の組み合わせが CI を通らずに本番へ出る（確かめ 6）。組み合わせの確かめは GitHub の merge queue に任せる（LIB3）。
merge queue を使えないのは、Organization の public か Enterprise Cloud の private でないリポジトリである（STD9）。そこには、`strict`（最新の取り込みを必須にする）を同じ設定の別の値として持たせる。

| 値 | 本番ブランチの ruleset に要るもの | `land` の動き |
| --- | --- | --- |
| `merge-queue` | merge queue の規則（GraphQL `mergeQueue(branch)` が null でない） | merge queue への追加と待ち（決定 18） |
| `strict` | 必須のチェックの `strict_required_status_checks_policy` が真 | HEAD の取り込みの確かめとマージ（決定 19） |

保存先を `production_merge` にするのは、本番のマージの振る舞い（迂回の `admin`。#3506）を既に持つ設定で、同じ読み手（`land`）が読むためである。
`.ndf/worktree.json` の `branch_flow` はスプリントの経路も読む設定で、マージの方式を置くとスプリントの経路が使わない値を持つ。
`trunk` 戦略では方式の記述を必須にし、無い・表に無い値・ruleset と食い違う、のどれでも `land` が原因 `precondition` で止まる（I15）。
既定の方式へ倒さないのは、倒した先が ruleset の実際と食い違えば、組み合わせの確かめが黙って抜けるためである。
ruleset は読むだけで、書き換えない。読み取りには `GET /repos/{o}/{r}/rules/branches/{branch}`（本番ブランチに効く規則の一覧）と GraphQL の `mergeQueue(branch)` を使う。

**レビューの承認の数。** merge queue へ入れられるのも `strict` でマージできるのも、PR が保護の条件（レビューの承認の数を含む）を満たした後である（STD7）。
NDF の承認ゲートの承認は PR のコメントで、GitHub のレビューの承認ではない。そのため、本番ブランチの PR の規則が承認 1 以上を求めると、
迂回を使わない（I17）`land` は追加もマージもできない。マージの方式を使うリポジトリは、本番ブランチの PR の規則の承認の数を 0 にする
（ai-plugins は 11.0.0 の入れ替えのとき。決定 23・設計文書の「人へ残す判断」の C2 の行）。人の承認は、リリース承認ゲートと、それと等価な人の GitHub でのマージが持つ。
`land` は検証で、規則の `required_approving_review_count` が 1 以上で、PR の `reviewDecision` が `APPROVED` でないかを見る。
そのときは追加もマージもせずに止まり、原因 `precondition`・理由に「本番ブランチの規則がレビューの承認を求め、PR に承認が無い」を出す（I15）。承認の数を残したまま、
ほかの人が GitHub のレビューで承認するリポジトリは、承認が付けば通る。

| 承認の数を満たす候補 | 採否と理由 |
| --- | --- |
| 承認ゲートの回答で、NDF が GitHub のレビューの承認を書く | 採らない。NDF の token の持ち主が PR の作者と同じとき（利用者が自分の token で PR を作るとき）、GitHub は作者の承認を受けない。承認が 2 か所に分かれる |
| 迂回の役割を持つ token でキューへ入れる | 採らない。迂回の役割で、保護の条件を満たさない PR をキューへ入れられるかは未確認で、迂回は必須のチェックと最新の取り込みも越える（I14 が破れる） |
| 本番ブランチの PR の規則の承認の数を 0 にし、人の承認を承認ゲートと人のマージで持つ | 採る。必須のチェック・キュー・最新の取り込みの規則は残る |

| 比べた候補 | 採否と理由 |
| --- | --- |
| ndf-next が自前で組み合わせのブランチを作成して確かめる（常設のリリースブランチ・bors の形） | 採らない。GitHub の merge queue が同じことを持ち（STD7）、bors-ng も merge queue へ移るよう案内している（LIB4） |
| `strict` だけにする | 採らない。後にマージする PR ごとに取り込みと承認の取り直しが要り、同時の承認が多いほど承認の回数が増える。merge queue は作者の取り込みなしで同じ利点を得る（STD7） |
| 設定を書かせず ruleset から方式を推す | 採らない。リポジトリの持ち主の意図と ruleset の食い違いを検出できない |
| 設定ファイルで選び、ruleset と突き合わせて検証する | 採る |

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: LIB3 / LIB4 / STD7 / STD9 / PRD3

根拠: Value 4 / Value 7 / Value 12（MVV 版 7）

### 決定 18: `merge-queue` では承認済みのコミットを `expectedHeadOid` に渡してキューへ入れ、マージかキューから外れるのを待ち、外れたら `reject` だけを返す

`land` は承認の記録の読み直しと ruleset の検証の後、PR の必須のチェックを待つ。
その後に `enqueuePullRequest(pullRequestId, expectedHeadOid=<承認した head>)` を実行する（STD10・確かめ 4）。
GitHub は PR の head が違えば追加を断り、`land` は `refused` で止まる。追加が通ったら、入れた head を本番の PR の merge queue への追加の記録のコメントに残す。
`land` は始めにこの記録を読み、承認済みのコミットの記録があり、PR がマージされておらずキューにも無ければ、入れ直さずに `queue_removed` で止まる（I16）。
`ci_pending` の後の `rerun` で始まった `land` は、今のキューの所属と承認だけでは初めての追加と見分けられないため、記録で見分ける。追加の後は `ci_budget` の秒まで、PR の `merged`・`isInMergeQueue`・`mergeQueueEntry.state` を読む。

| 読んだ状態 | `land` の扱い |
| --- | --- |
| `merged` が真 | マージした head（PR の head）が承認済みのコミットと一致すれば、マージコミット（キューの一時マージコミット）を記録して `ok`（経路 (a)）。違えば人の操作のマージ（経路 (b)）として、人のマージの記録（承認済みのコミットはマージした head、承認ゲートの head は承認済みのコミット）を書いて `ok` にする（I5） |
| `isInMergeQueue` が偽で `merged` が偽 | キューから外れた（チェックの失敗・時間切れ・衝突・取り外し）。原因 `queue_removed` で止まり、キューの一時マージコミットの落ちたチェックを読めれば `items` に出す。次の手は `reject` だけ（決定 3・I16） |
| キューの中のまま `ci_budget` を過ぎた | 原因 `ci_pending` で止まり、次の手は `rerun`（同じ承認で待ちを続ける。キューへは入れ直さない。入っているため） |

キューから外れた PR は、同じ head で入れ直さない。キューの一時マージコミットは本番ブランチの HEAD と前に並ぶ PR と合わせたもので、外れた理由が
組み合わせの失敗なら、同じ head で入れ直しても同じ組み合わせで落ちる。修正を入れた新しい head の承認を取り直してから入れる（受け入れ条件 25）。
キューの組み合わせは GitHub が決めるため、B のレーンは A が外れても何もしない（GitHub が B を確かめ直す。STD7）。

**経路 (a) と (b) の見分け。** `land` が実行する追加とマージは承認済みのコミットだけを渡し、GitHub は head が違えば断る。そのため、承認済みのコミットと違う head の
マージは `land` の操作では起こらず、人がキューから外して入れ直したか、GitHub で直接マージした操作である。`land` はマージした head だけで見分け、
キューへ入れた人（`merged_by`）やイベントの主は使わない。NDF の token の持ち主と利用者が同じことがあり、見分けられないためである。

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: STD7 / STD10 / STD11 / LIB3

根拠: Value 3 / Value 7（MVV 版 7）

### 決定 19: `strict` では head が本番ブランチの HEAD を含まなければマージせずに止め、取り込んだ新しい head で承認を取り直す

状態: 一部 superseded（迂回の設定の保存先 `production_merge.admin` は決定 27 の `branch_strategy.production_merge.admin`。振る舞いは有効）

`land` は ruleset の `strict` が真であることを確かめた後、PR の head が本番ブランチの HEAD を祖先に持つかを確かめる（`compare` の `behind_by` が 0）。
含まなければ原因 `behind` で止まり、次の手は `reject` だけを出す。直す手はスプリントの経路が本番ブランチを取り込んで head を進めることで、
取り込んだ head は承認済みのコミットと違うため、承認は `stale` になり取り直す（受け入れ条件 23）。マージそのものは `expect_head` 付きで、迂回せずに実行する（`production_merge.admin` が真でも。I17）。
迂回は最新の取り込みの規則も越えるため、迂回しないことで、確かめとマージの間に本番ブランチが動けば GitHub が断る（strict の規則）。そのとき `land` は確かめへ戻って `behind` で止まる。
取り直しは GitHub のレビューの設定に依らない。ruleset の `dismiss_stale_reviews_on_push` が偽でも（確かめ 6）、NDF の承認は head ごとの判定で `stale` になるためである。

- 作り方: 標準に倣う
- 比べた候補: STD1 / STD7 / STD9

根拠: Value 3 / Value 4（MVV 版 7）

### 決定 20: バージョンアップの PR も、マージの方式があるときは迂回せずに本番の PR と同じ方式で入れる

状態: 一部 superseded（迂回の設定の保存先 `production_merge.admin` は決定 27 の `branch_strategy.production_merge.admin`。振る舞いは有効）

バージョンアップの PR は人の承認を持たない。決定 17 で本番ブランチの PR の規則の承認の数を 0 にするため、マージの方式があるリポジトリでは
バージョンアップの PR も迂回せずにキューへ入れられ、`strict` でもマージできる。main の ruleset の迂回の形は `pull_request` である（確かめ 6）。
この迂回は PR のマージにだけ効き、PR を通さない ref の更新（`PATCH /git/refs/heads/main`）には効かない（STD12）。PR のマージで迂回すると、
必須のチェックと最新の取り込みの規則も越える。確かめの後にキューが別の PR を main へ入れれば、確かめていない組み合わせが main へ入る。
`expect_head` が固定するのは PR の head だけで、main の HEAD は固定しないためである。迂回せずに方式を通すと、組み合わせの確かめはキュー
（`merge-queue`）か最新の取り込みの規則（`strict`）が持つ。

| 守り | どこで守るか |
| --- | --- |
| マージの方式があるとき、迂回のマージを実行しない | `land` と `land_bump` は、`production_merge.admin` が真でも `merge_as_admin` を呼ばない（I17）。方式の無い既存のブランチ戦略は今の規則のまま |
| キューで前に並んだ PR がバージョンの範囲から外れない | 先頭へ入れても追い越されたマージコミット（第 1 親がバージョンアップの PR の head の祖先でない）には、`publish` がタグを作らず、`bump` がそのコミットの分岐元からの範囲で上げ直す（`tagging.overtaken`） |
| バージョンアップの PR にコードが混ざらない | `bump_decide` が、差分がバージョンの保存先（決定 22）・CHANGELOG・手順の書き先に限られると判定したときだけ入れる |
| 確かめた組み合わせだけが入る | `land_bump` も `land` と同じく設定ファイルの方式と ruleset を突き合わせて検証し、食い違えば `precondition` で止まる。`merge-queue` では head が main の HEAD を含むときだけ head を `expectedHeadOid` に渡してキューの先頭へ入れ（`jump`）、キューの一時マージコミットで必須のチェックが走る。`strict` では head が main の HEAD を含むときだけ `expect_head` 付きでマージし、確かめの後に main が動けば GitHub が断る。外れた・断られたら `land_bump` は `queue_removed` か `behind` で止まり、次の push の `bump` に任せる |

キューを通すと、main へのマージからタグまでに CI がもう 1 回（キューの一時マージコミットの分）走り、リリースとバージョンの間の時間
（設計文書の「main へのマージの後（CD）」）が延びる。この費用は、確かめていない組み合わせを main へ入れないために払う。

人が GitHub の画面で管理者の迂回を使ってコードの PR をマージすることは、利用者の操作（経路 (b)）で、工程は止めない。人のマージの記録に
マージの方式とマージコミットが残る（決定 8）。

| 比べた候補 | 採否と理由 |
| --- | --- |
| main の ref を head へ fast-forward で進める（`PATCH /git/refs/heads/main`） | 採らない。迂回の形 `pull_request` は PR を通さない更新を許さず（STD12）、迂回の役割を持つ token でも断られる |
| ruleset の迂回の形を `always` にして ref を進める | 採らない。ruleset の書き換えで、承認ゲートを通らない機械の push を許す（決定 7 の採らなかった案と同じ） |
| 祖先を確かめてから PR のマージで迂回する | 採らない。確かめとマージの間に main が動くと、確かめていない組み合わせが入る。迂回は最新の取り込みの規則も越えるため GitHub も断らない |
| バージョンアップの PR をやめ、タグだけを作る（バージョンのファイルを持たない） | 採らない。決定 7 の採らなかった案と同じ理由 |
| バージョンアップの PR も迂回せず、本番の PR と同じ方式で入れる | 採る。キューの CI が 1 回増える代わりに、組み合わせの確かめと PR を通した迂回の形の両方が保たれる |

- 作り方: 標準に倣う
- 比べた候補: STD6 / STD7 / STD11 / STD12

根拠: Value 3 / Value 7（MVV 版 7）

### 決定 21: 検証環境をリポジトリごとに選べるようにするため、`delivery` の検証の行の `verify.kind` で種類（`marketplace`・`deploy`）を選ぶ

状態: 一部 superseded（保存先を `.ndf/project.json` の `delivery` の検証の行にする部分と、その理由の文は決定 27 の `branch_strategy.verify`。種類と配り方は有効）

マージの前に試す場所はリポジトリのリリースの形で違い、プラグインなら検証マーケットプレイス、Web なら staging 環境である。どちらも
「本番ブランチ宛ての開いた PR を集めた検証ブランチを作成し、それを検証環境へ配る」の形で、違うのは配り方だけである。そのため、検証ブランチの
作成（決定 9 の含める PR・fast-forward・衝突の止め方）を共通にし、配り方を種類で選ぶ。

| 種類 | 設定のキー | 配り方 |
| --- | --- | --- |
| `marketplace` | `ref`・`marketplace` | 検証ブランチの `marketplace.json` の名前とバージョンを書き換えたコミットを足す（決定 9） |
| `deploy` | `ref`・`command`・`timeout_seconds` | 検証ブランチを push した後、`{ref}`・`{head}` を置き換えたコマンドを実行する。`marketplace.json` を読まない |

検証の行が無いリポジトリは検証環境を使わず、`verify-ref` は `not_applicable` を返す。種類が表に無ければ、取れる値を理由に出して止まる（受け入れ条件 20）。
`deploy` の先（どのクラウドへ、どう配るか）は要求の「含まない」で、ndf-next はコマンドを実行するだけである。コマンドは本番ブランチの設定ファイルにあるものだけを実行する。
保存先を `delivery` にするのは、リリース対象をどこへどう届けるかを既に持つ設定で（`channel`・`trigger`）、検証環境もその 1 つの届け先だからである。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 検証マーケットプレイスだけを持つ（決定 9 の superseded の部分） | 採らない。Web の staging 環境を書けない |
| 種類ごとに検証ブランチの作成を分ける | 採らない。含める PR と fast-forward の規則が 2 か所になる |
| GitHub の Environments の「Require deployments to succeed」を必須にする（STD6 の規則の 1 つ） | 採らない。ruleset の書き換えが要り、マージの前に試す場所であってマージの条件ではない |
| 作成を共通にし、配り方を種類で選ぶ | 採る |

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD4 / STD5 / STD6

根拠: Value 4 / Value 5 / Value 11（MVV 版 7）

### 決定 22: バージョンアップの対象をプラグインに決め打たないため、`.ndf/release.json` の `version_store` でバージョンの保存先のアダプタを選び、#3615 と境を分ける

状態: 一部 superseded（設定のキーを `.ndf/release.json` の `version_store` にする部分は決定 27 の `branch_strategy.release.version_store`、#3615 との境の表の「設定のキー `version_store`」が入る場所と #3615 が足すキーの場所は決定 30。インタフェースとアダプタは有効）

#3587 のバージョンアップ（`bump.py`、d7d5fe0b1 の時点）は、`write_bump`・`allowed_paths`・`planned_for`・`Bumper._tip`・`Bumper._plan` がどれも
`plugins.load`（`.claude-plugin/marketplace.json` の読み）を呼び、`plugin.json` のバージョンを上げる。`pr_check._versions`・`cli._plugin_of`・
`gather.version_at` も同じである。`marketplace.json` を持たないリポジトリでは、ここで `PluginsError` が原因 `precondition` になる（#3615 の点検 1）。
設定ファイルの手順（`steps`）と `version_files` は既にリポジトリごとの書き換えを受けるが、今のバージョンを読む元とリリースの単位がプラグインに決め打たれている。

バージョンの保存先をアダプタのインタフェースにし、設定ファイルで選ぶ。

| メソッド | 意味 |
| --- | --- |
| `units()` | リリースの単位（名前と、変化を数える根のディレクトリ）の一覧 |
| `read(ref, unit)` | ある ref の単位のバージョン |
| `write(unit, new)` | 単位のバージョンを書き、書いたパスを返す |
| `paths()` | バージョンアップの PR が変えてよいパス（I17 の判定に使う） |

| `kind` | 持つもの | この課題で作るか |
| --- | --- | --- |
| `plugin` | 今の `plugins.py`（マーケットプレイスの登録・`plugin.json`・`version_files`）を包む | 作る（ai-plugins が選ぶ） |
| `json` | `path`・`key`・`name`。1 つの JSON のファイルの 1 つの鍵（`package.json`・`composer.json`・`app.json` など） | 作る（受け入れ条件 21） |
| ほか（`pubspec.yaml`・Gradle・タグだけ など） | — | #3615 が足す |

`bump`・`pr-check`・`confirm`・`planned_for` はインタフェースだけを呼び、`plugins.py` を直接呼ばない（I18）。保存先が無ければ `trunk` の `bump` は止まる。
設定ファイルの手順（`steps` の `production`）はインタフェースの `write` の後に今のまま実行され、`package-lock.json` のような付随のファイルを書き換える。

**#3615 との境。**

| 決めること | 持ち主 |
| --- | --- |
| いつ・誰がバージョンを上げるか（`trunk` の CD・バージョンアップの PR・キューとの関係） | #3572（決定 7・20） |
| バージョンの保存先のアダプタのインタフェースと、設定のキー `version_store` | #3572（この決定） |
| アダプタ `plugin`・`json` | #3572 |
| ほかのリリースの形のアダプタ（今の NDF のリリースの形 11 種との対応） | #3615 |
| タグ・CHANGELOG の見出し・リリースブランチの名前の形を設定ファイルで変えること | #3615（それまでは今の `<名前>--v<バージョン>` の形） |
| 既存のブランチ戦略の承認資料・リリースブランチの組み立て・タグ（`assemble`・`gather`・`tagging`）をアダプタのインタフェースへ移すこと | #3615（この課題では今のまま） |
| リリースを利用者のリポジトリから起動する経路（workflow のテンプレート） | #3615 |
| 配らないファイルの既定を `test-files.json` から作ること | #3615 |

この課題の範囲で CD を起動する workflow は ai-plugins の `.github/workflows/ndf-next-release.yml` だけである。そのため要求の受け入れ条件 21
（`marketplace.json` を持たないリポジトリで「CD のバージョンアップを実行する」）は、サブコマンド `ndf release-steps bump` を保存先 `json` で実行することで満たす。
確かめ方は設計文書の「テスト設計」の受け入れ条件 21 の行で、アダプタを差し替えた単体テストと、コードの検索の結果である。
ほかのリポジトリが main への push で `bump`・`publish` を呼ぶ workflow を持つのは、#3615 のテンプレートが入った後である。
それまでは利用者が自分の workflow からサブコマンドを呼ぶ。

| 比べた候補 | 採否と理由 |
| --- | --- |
| `version_files` と手順だけで受け、保存先のアダプタを作らない | 採らない。今のバージョンを読む元とリリースの単位が残り、`marketplace.json` を読む処理が消えない |
| release-please の release type（PRD1）をそのまま使う | 採らない。バージョン種別をコミットの書式から読む（LIB1）。種類で選ぶ形だけを倣う |
| リリースの形のアダプタ一式をこの課題で作る | 採らない。#3615 の範囲で、11 種との対応の表を持たずに作ると #3615 と食い違う |
| インタフェースと 2 つのアダプタをこの課題で作り、残りを #3615 へ | 採る |

- 作り方: 自前で作る・標準に倣う
- 比べた候補: PRD1 / LIB1

根拠: Value 4 / Value 9 / Value 11（MVV 版 7）

### 決定 23: ruleset へ merge queue を足すのは 11.0.0 の入れ替えの PR と同じ時点にし、必須のチェックの workflow への `merge_group` はこの課題の実装で足す

状態: 一部 superseded（入れ替えの PR で書き換える ai-plugins の設定ファイルは決定 27 の `.ndf/branch-strategy.json`。時点と ruleset の設定の案は有効）

必須のチェックを出す workflow は `merge_group` を持たない（確かめ 6）。キューを足す前に `merge_group` が無ければ、キューの一時マージコミットに
チェックが報告されず、マージが失敗する（STD7・STD8）。`merge_group` の追加はキューが無い間は何も起こさないため、この課題の実装で先に足す。
PR の本文やマージ先を読むチェック（`pr-body-decisions-check`・`guard` など）は、キューの一時マージコミットでは PR の時点の結論を引き継いで通す。
キューに入れられるのは PR の時点で必須のチェックを満たした PR だけだからである（STD7）。

ruleset の書き換えは、ai-plugins の設定ファイルを `trunk`・`merge-queue` へ書き換える入れ替えの PR と同じ時点にする。キューを先に足すと、
今の NDF のリリース（develop から main への PR を `production_merge.admin` で迂回してマージする）がキューの規則に当たる。これは 11.0.0 まで今の運用を
変えない前提（要求の前提 10）と食い違う。実行は承認ゲートを通らない外部の系の設定の変更で、実行の前に止めて人へ戻す（設計文書の「人へ残す判断」の C2 の行）。

足す設定の案:

| 項目 | 値 | 理由 |
| --- | --- | --- |
| マージの方式 | `MERGE` | ruleset の `allowed_merge_methods` が `merge` だけ（確かめ 6） |
| グループの作り方 | `ALLGREEN`（すべての PR が必須のチェックを満たすこと） | 落ちた PR を組み合わせに含めない |
| 同時にビルドする数（build concurrency） | 5 | 並列は 6 本まで（同時に走らせるレーンの上限）。1 つの組み合わせの CI が 17 個のチェックを持つ |
| 1 回にマージする数 | 最小 1・最大 5、待ち 5 分 | 1 つでも待たずに出す。同時の承認をまとめて確かめる |
| チェックの待ち | 60 分 | CI の 1 回（必須のチェック 17 個）が収まる値。入れ替えの後の実測で見直す |
| PR の規則の承認の数 | 1 から 0 へ | NDF の承認ゲートの承認は GitHub のレビューの承認ではなく、承認 1 のままでは `land` が追加できない（決定 17） |

この値は ai-plugins の値で、ndf-next のコードに持ち込まない。ndf-next は ruleset に merge queue があるかだけを読む（決定 17）。

- 作り方: ライブラリやサービスを使う
- 比べた候補: STD7 / STD8 / LIB3

根拠: Value 3 / Value 12 / C2（MVV 版 7）

### 決定 24: #3587 を閉じずに直し、d7d5fe0b1 に足す変更を次の一覧にする

#3587 はマージ先 develop、head d7d5fe0b1、66 ファイル・73 コミットである（2026-11-15）。数えたコマンドは次の 2 つである。

```bash
git diff --stat origin/develop...origin/sprint/lrel-lock
git log --oneline origin/develop..origin/sprint/lrel-lock | wc -l
```

#3587 は、決定 1〜4・6〜8・10〜12 の実装と、ブランチ戦略の一覧の 3 つの戦略（`trunk`・`merge_base_branch`・`release_branch`）を既に持つ。この差分は cross-review と cross-refactoring を通った。閉じて出し直すと、残す部分の
レビューとリファクタリングをやり直す。ndf-next は 11.0.0 の入れ替えまで develop で開発するため、マージ先 develop も変えない。
次の変更を同じ PR へ足し、変わった差分を再びレビューへ通す。
12・13 の行の実装は、ブランチ戦略のセクションを決めたこの設計（決定 27〜30）の承認の後に行う。

| # | 変更 | 決定 |
| --- | --- | --- |
| 1 | `branch-flows.json` の既定の戦略を `merge_base_branch` へ戻す。`declaration.require_github_flow` を外し、`assemble`・`bump`・`cli` の呼び出しを戦略ごとの振る舞いの選択に置き換える | 16 |
| 2 | 既存のブランチ戦略の経路（`assemble` のリリースブランチの組み立てと `--channel`・`--source`、`land._sync`、結果の `sync_pr`）を develop の形へ戻し、`trunk` 戦略でだけ外す | 6・10・16 |
| 3 | `production_merge.method` のスキーマ・`merge_method.py`・アダプタの `branch_rules`・`land` の検証（原因 `precondition`） | 17 |
| 4 | アダプタの `enqueue`・`queue_entry`、`land` の merge queue への追加と待ち、原因 `queue_removed`、次の手の表 | 3・18 |
| 5 | `land` の HEAD の取り込みの確かめ、原因 `behind` | 19 |
| 6 | `land` で `merge-queue` の本番の PR に迂回を呼ばない。`land_bump` はマージの方式があるとき迂回せずにキューか `strict` のマージで入れ、入れる条件にバージョンの保存先の判定を使う | 20 |
| 7 | `version_store.py`（インタフェースと `plugin`・`json`）、`release.json` のスキーマの `version_store`。`bump.py`・`pr_check.py`・`cli._plugin_of`・`planned_for` をインタフェースへ移す | 22 |
| 8 | `delivery` の検証の行の `verify.kind`、`verify_ref` の `deploy` の配り方 | 21 |
| 9 | 必須のチェックを出す workflow へ `merge_group` を足し、PR の本文を読むチェックの引き継ぎ | 23 |
| 10 | テストを足す（設計文書の「テスト設計」の受け入れ条件 18・20〜26 の行）。既存のブランチ戦略のテスト（リリースブランチの組み立て・同期の PR）を develop から戻す | 16〜23 |
| 11 | #3613 の改名が develop へ入った後に develop を取り込み、#3587 が足した戦略・値・スキーマの名前を `release_strategy`・`trunk` へそろえる | 26 |
| 12 | 設定の解決を `.ndf/branch-strategy.json` のセクション `branch_strategy` から読む形へ直す。`declaration.py`（ブランチ戦略・迂回の設定・検証環境の読み）・`merge_method.py`（入力をセクションの `production_merge.method` にする）・`version_store.py`（セクションの `release.version_store` でアダプタを選ぶ）・セクションのスキーマ `contracts/schemas/declarations/branch-strategy.schema.json` の新設と、`worktree.schema.json`・`project.schema.json` から移したキーを外すこと（`release.schema.json` には `version_store` を足さない）・保存先の表（`contracts/domains/ndf-files.json`）の行 `branch-strategy`。セクションを読むほかの箇所（`domains/worktree/declaration.py`・`core/route` の `default_base`・`domains/project` の `check`・`write`・`cli`・`measure_flow`・`progress/command.py`・`progress/gate/release.py`・`domains/release/records_trail.py` の迂回の記録の文言）も同じ読み取りの関数（`core/branch_strategy.py`）を通す。`domains/records/mvv.py` はベースブランチを引数 `--base` で受け、設定ファイルを読まないため変えない。配る Skill の説明（`skills/worktree`・`skills/development-workflow`・`skills/merged`・`skills/release`）と生成する契約のページ（`docs/contracts/declarations.md`・`files.md`）もセクションの保存先へ直す。3・7・8 の行のスキーマの保存先は、この行のセクションのスキーマである | 27・28 |
| 13 | ブランチ戦略の一覧（`branch-flows.json`）から戦略が許す値の列（`start`・`doc_prs`・`integrate`・`review_kinds`）を外し、セクションのスキーマの `if`/`then` へ移す。表に残すのは振る舞いの列（`release_strategy`・`default`・`keeps_sprint`・`release_from`・`release_units`）である | 28 |

| 比べた候補 | 採否と理由 |
| --- | --- |
| #3587 を閉じて main から出し直す | 採らない。残す部分のレビューとリファクタリングをやり直し、ndf-next の開発の場所（develop）とも合わない |
| #3587 を直して出す | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 2 / Value 5（MVV 版 7）

### 決定 25: #3119 の設計の決定 21 を、ai-plugins の設定ファイルが `trunk` 戦略を選び、一覧は 3 つの戦略を残す決定へ見直す

状態: 一部 superseded（ai-plugins の設定の保存先は決定 27。見直しの 3 点と書き換えの時点は有効）

#3119 の決定 21 は、ai-plugins の設定ファイルを `trunk` 戦略にすることを決めている。見直すのは次の 3 点である。

1. ブランチ戦略の一覧は `merge_base_branch`・`release_branch` を残し、`trunk` は選ぶ行の 1 つである（決定 16）
2. マージの前の検証は、ai-plugins が選ぶ検証環境の種類 `marketplace` である（決定 21）
3. ai-plugins のマージの方式は `merge-queue` で、ruleset の変更は入れ替えの PR と同じ時点に人の承認の後に行う（決定 23）

設定ファイルの書き換えを 11.0.0 の入れ替えと同じ時点にすることは変えない。差分は同じ設計 PR の `issues/issue-3119-design.md` の決定 21 にある。

- 作り方: 標準に倣う
- 比べた候補: STD3 / STD7

根拠: Value 4 / P1（MVV 版 7）

### 決定 26: ブランチ戦略のキーと値は #3613 の改名の後の名前で書き、改名と移行は #3613 が持つ

状態: 一部 superseded（キーの保存先 `.ndf/worktree.json` の `branch_flow.release_strategy` は決定 27 の `branch_strategy.release_strategy`。キーと値の名前と #3613 との境は有効で、移行の範囲は決定 29 が足す）

ブランチ戦略のキーは `.ndf/worktree.json` の `branch_flow.release_strategy` である。値とブランチ戦略の一覧の戦略の名前は次の 3 つである
（利用者の決定、2026-11-16。#3613）。

- `merge_base_branch`: ベースブランチを丸ごと本番ブランチへマージする
- `release_branch`: 承認したスプリントだけを、本番ブランチから作ったリリースブランチで出す
- `trunk`: スプリントの PR を直接本番ブランチへ入れる

この設計の文書は、どのセクションもこの名前で書く。

| 何を決めるか | 持つ課題 |
| --- | --- |
| develop にある 2 つの戦略のキー・値・戦略の名前の改名、スキーマの改名、利用者の設定を新しいキーへ書き換える移行のコマンド、移行の後に残る旧いキーで止める読み取り | #3613 |
| `trunk` 戦略を新しい名前で足すこと、`trunk` 戦略のリリースの振る舞い（決定 5〜7・9・10・16〜23） | #3572 |

#3587 は `trunk` 戦略を改名の前の名前で持つ。#3613 の改名が develop へ入った後に develop を #3587 へ取り込み、#3587 が足した戦略・値・スキーマの名前を
新しい名前へそろえる（決定 24 の 11 行目）。#3613 の改名より先に #3587 をマージしない。先にマージすると、develop に旧い名前の行と新しい名前の行が混ざる。
この順序は要求に無い課題どうしの制約で、設計承認ゲートで人が見る（設計文書の「人へ残す判断」）。

要求の本文と利用者の指示の原文は改名の前の名前で書かれている。要求は書き換えず、設計文書の「適用範囲」の読み替えの表で読む。
表は `promote-whole` → `merge_base_branch`・`promote-selected` → `release_branch`・`github-flow` → `trunk` の 3 行である。

| 比べた候補 | 採否と理由 |
| --- | --- |
| #3572 の実装で改名も行う | 採らない。改名と移行のコマンドは利用者の設定の書き換え（C7）を伴い、#3613 が 1 か所で持つ（Value 11） |
| 改名の前の名前で書き、改名の後に文書を直す | 採らない。承認の資料と実装の名前が食い違い、読み手が 2 つの名前を対応づける手間が残る |
| #3613 の改名の後の名前で書き、#3587 は改名の後に取り込む | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 11 / Value 14 / C7（MVV 版 7）

### 決定 27: 利用者が 1 か所でブランチ戦略を読めるよう、ブランチ戦略・マージの方式・検証環境・バージョンの保存先を新しい設定ファイル `.ndf/branch-strategy.json` のセクション `branch_strategy` にまとめる

決定 16〜22 は、4 つの設定を次の 3 つのファイルに分けた。

| 設定 | 決定 16〜22 の保存先 |
| --- | --- |
| ブランチ戦略 | `.ndf/worktree.json`（`branch_flow`・`base_branch`・`production_branch`） |
| マージの方式 | `.ndf/project.json`（`production_merge`） |
| 検証環境 | `.ndf/project.json` の `delivery` の検証の行 |
| バージョンの保存先 | `.ndf/release.json`（`version_store`） |

利用者が自分のリポジトリのブランチ戦略を読むのに 3 つのファイルが要る。
「`trunk` ならマージの方式とバージョンの保存先が要る」という組み合わせの検査もファイルをまたぐ（利用者の決定、2026-11-16 03:4x JST）。4 つを 1 つのファイルの 1 つのセクションにまとめる。

ファイルは `.ndf/branch-strategy.json`、セクションの名前は `branch_strategy` である。ファイルの最上位は `$schema`・`version`・`branch_strategy` の 3 つだけを持つ。
セクションのキーと、6 つの質問（スプリントの分岐元・PR のマージ先・リリースの形・マージの方式・マージの前に試す場所・バージョンの上げ方）との対応は、設計文書の
「ブランチ戦略のセクション」の表が持つ。キーの名前は移す前の名前をそのまま使い（`branch_flow` の下のキーはセクションの直下へ上げる）、`version_store` だけを
リリースの形の設定の入る `release` の下へ置く（決定 30）。検証環境は `delivery` の行の形をやめ、セクションのキー `verify`（中身は決定 21 の `verify` のまま）で持つ。
`delivery` の行の `channel`・`target`・`trigger` は、検証環境では種類（`kind`）と検証ブランチ（`ref`）が決めるためである。

セクションはメインディレクトリ（既定ブランチの worktree）のファイルから読む（保存先の表の `main_keys` に `branch_strategy`）。移す前は `production_branch`・
`branch_flow`・`production_merge` がメインディレクトリから読むキーだった。`base_branch` だけが、呼ばれた worktree のファイルから読まれていた。セクションにまとめると
`base_branch` もメインディレクトリから読む。スプリントの PR のマージ先を PR の変更で動かせなくするためで、`main` 宛ての検査を `main` の設定で判定する
規則（#3119 の I6）と同じ向きである。

**要求との差（前提 18・受け入れ条件 2n・18）。** 前提 18 と受け入れ条件 2n は、置き換えた決定とこの決定で保存先とキーの名前のほかに差が無いことを、
受け入れ条件 18 は既存のブランチ戦略が今と同じに動くことを求める。今の ndf-next は `base_branch` だけを、呼ばれた worktree の `.ndf/worktree.json` から読む
（`core/route` の `default_base`）。スプリントの PR がこのファイルを書き換えればマージ先が変わる。セクションをメインディレクトリから読むと、マージ先は既定ブランチの
ファイルで決まり、PR の変更で動かない。差が出るのは、スプリントブランチのファイルとメインディレクトリのファイルで `base_branch` が違うときだけである。
差を受けるのは、マージ先を PR の変更で動かせなくする向き（上の段落）が、`main` 宛ての検査を `main` の設定で判定する #3119 の I6 と揃うためである。
セクションの中で `base_branch` だけを呼ばれた worktree のファイルから読む形は、差を消せるが、1 つのセクションのキーを 2 つの場所から解決することになり、採らない（下の表）。

セクションへ移さず、今のファイルに残す設定:

| 設定 | 残すファイル | 残す理由 |
| --- | --- | --- |
| `guard`・`follow_branch`・`deps`・`localenv`・`testenv` | `.ndf/worktree.json`（と各自の機械の `worktree.local.json`） | worktree の作り方とメインディレクトリの扱いだけの設定で、6 つの質問のどれにも対応しない。`follow_branch`・`localenv`・`testenv` は機械ごとに上書きされ、セクション（どの機械でも同じ値）と性質が違う |
| `delivery` の検証環境でない行（`channel: "production"` など） | `.ndf/project.json` | リリース対象をどこへ届けるかの設定で、今の NDF の CD の行と #3615 のリリースの形が読む。マージの前に試す場所ではない |
| `steps`・`changelog`・`version_files`・`undistributed`・`bump_undistributed` | `.ndf/release.json` | リリースのステップごとに実行するコマンドと書き換えるファイルの一覧で、今の NDF も読む。`release` の下へ移すかは #3615 が決める（決定 30） |

| 比べた候補 | 採否と理由 |
| --- | --- |
| 新しいファイル `.ndf/branch-strategy.json` のセクション `branch_strategy` | 採る。ファイルの名前が利用者の質問「どのファイルにブランチ戦略を書くか」への回答にそのままなる。ファイルのスキーマがセクションのスキーマになり、組み合わせの検査がほかの設定のスキーマと混ざらない（決定 28）。ファイル全体をメインディレクトリから読める。release-please も、リリースの種類とパッケージごとの設定を 1 つの設定ファイル（`release-please-config.json`）に持つ（PRD4） |
| `.ndf/worktree.json` のセクション | 採らない。ファイルの名前が worktree の作り方を指し、マージの方式・バージョンの保存先を探す利用者がたどり着かない。同じファイルに残る機械ごとの設定（`follow_branch` など）と、どの機械でも同じ値のセクションが 1 つのファイルに混ざる |
| `.ndf/project.json` のセクション | 採らない。15 のキーを持つファイルで、名前から質問の回答が分からず、セクションを探すのにほかのキーを読む。スキーマが大きく、セクションの検査の誤りがほかのキーの誤りと 1 つの結果に並ぶ |
| `.ndf/release.json` のセクション | 採らない。今の NDF のリリースの手順が読むファイルで、名前がリリースのステップを指す。スプリントの分岐元と PR のマージ先はリリースの前の工程が読む |
| 3 つのファイルのまま、まとめて表示するコマンドを足す | 採らない。利用者の決定（1 つにまとめる）に反し、組み合わせの検査がファイルをまたいだまま残る |
| セクションはメインディレクトリから読み、`base_branch` だけを呼ばれた worktree のファイルから読む（今の読み方を残す） | 採らない。前提 18・受け入れ条件 18 との差は消えるが、1 つのセクションのキーを 2 つの場所から解決し、セクションの読み取り（`core/branch_strategy.py`）が呼ばれた worktree を受け取る。スプリントの PR が自分のマージ先を書き換えられる経路も残る |

- 作り方: 自前で作る
- 比べた候補: PRD4 / STD13

根拠: Value 1 / Value 8 / Value 11 / Vision（MVV 版 7）

### 決定 28: 組み合わせの検査をブランチ戦略のセクションのスキーマと ruleset の読み取りだけで行うため、戦略が許す値をブランチ戦略の一覧からセクションのスキーマへ移す

組み合わせの検査は次の 3 つである。どれも入力は `.ndf/branch-strategy.json` の 1 つで、2 つ以上の設定ファイルを読む検査は無い。キーの形の検査と合わせた
一覧と入力の列は、設計文書の「ブランチ戦略のセクション」の「組み合わせの検査」の表が持つ。

1. **戦略が許す値**: `release_strategy` の値ごとに `start`・`doc_prs`・`integrate` と `reviews` に書ける PR の種類を、セクションのスキーマの `if`/`then`
   （JSON Schema 2020-12 の条件付きの部分スキーマ。STD13）で縛る。今は同じ事実をブランチ戦略の一覧の列が持つ。列は `start`・`doc_prs`・`integrate`・`review_kinds` で、
   Python（`measure_flow.item_problem` など）が表を引いて確かめる。スキーマへ移し、表からこの 4 列を外す。同じ事実を 2 か所に持たないためである
2. **`trunk` 戦略で必須のキー**: `production_merge.method` と `release.version_store` を、同じ `if`/`then` の `required` で縛る。決定 17 の
   「既定の方式へ倒さない」と決定 22 の「保存先が無ければ止まる」は、`land`・`bump` を実行する前に、設定ファイルを読んだ時点でも止まる。
   逆に、どの経路も読まない値も同じ `if`/`then` で落とす。`trunk` でない戦略の `release.version_store`（既存のブランチ戦略のバージョンはリリースブランチの経路が上げる）と、
   マージの方式と一緒に書いた `production_merge.admin` の真（方式があるとき迂回のマージは使われない。決定 20・I17）である。書いた値が黙って使われない設定を許さない
3. **ruleset との検証**: `merge_method` はセクションの `production_merge.method` と、アダプタが読んだ本番ブランチの規則（merge queue の有無・
   `strict`・レビューの承認の数）だけを受ける（決定 17 の表と I15）。ruleset は GitHub の設定で、スキーマでは縛れないため、実行の時点に読む

ブランチ戦略の一覧（`branch-flows.json`）には、戦略ごとの振る舞いの列だけが残る。残る列は `release_strategy`・`default`・`keeps_sprint`・`release_from`・`release_units` である。
表はリリースとスプリントの経路が同じ行を読む公表された言語のままで（設計文書の「コンテキスト」）、許す値の正本だけがスキーマへ移る。
既定の戦略（`merge_base_branch`）の値は、スキーマの各キーの `default` が持つ。

スキーマの案と、2 つの設定の例が通り、組み合わせを壊した 13 の例が落ちることは確かめ 8 にある。

| 比べた候補 | 採否と理由 |
| --- | --- |
| ブランチ戦略の一覧に許す値を残し、セクションを読んだ後に Python が表を引いて確かめる | 採らない。検査の入力に表が加わり、利用者がセクションのスキーマを読んでも許す値が分からない。エディタの補完とスキーマの検査（`ndf check` の spec-decl）でも組み合わせの誤りが出ない |
| スキーマと表の両方に許す値を持ち、一致をテストで縛る | 採らない。同じ事実を 2 か所に持つ（Value 11） |
| 許す値をスキーマの `if`/`then` へ移し、表は振る舞いの列だけにする | 採る |

- 作り方: 標準に倣う
- 比べた候補: STD13

根拠: Value 8 / Value 11（MVV 版 7）

### 決定 29: #3613 との境と実装の受け持ちを分け、保存先とキーの名前を #3613 の設計へ同じ設計 PR で入れる

| 何を決めるか・作るか | 持つ課題と PR |
| --- | --- |
| ブランチ戦略のセクションの保存先（`.ndf/branch-strategy.json`）・セクションの名前（`branch_strategy`）・セクションのキーの名前・検証環境の持ち方（セクションのキー `verify`）・セクションのスキーマ | #3572（決定 27・28） |
| キーと値の改名（`promotion` → `release_strategy` など）・移行のコマンド（`ndf settings migrate`）・改名表・旧いキーで止まる読み取り・移行の対象と承認の取り方（リポジトリごとに差分を示して承認） | #3613（#3613 の決定 4・12・15） |
| 旧い 3 つのファイルの値をセクションへ移す改名表の行と、改名表の列 `to_file`・`where` の実装 | #3613 の続きの PR（レーン lterms） |
| セクションを読む側（設定の解決・スキーマ・ブランチ戦略の一覧の列）の実装 | #3572 の実装 PR（#3587。決定 24 の 12・13 の行） |

この設計で決めた値は、#3613 の設計（`issues/issue-3613-design.md` の「ブランチ戦略のセクションへ移す行」）へ入れた。
入れた先は、移行の側の差分の記号（保存先のファイル `<F>`・セクションの名前 `<S>`）の位置である。両方の文書で、保存先のファイル・セクションの名前・キーの名前が一致することは確かめ 9 の検索で確かめた。
検証環境を `delivery` の行でなくセクションのキー `verify` で持つため、改名表に列 `transform`（変換の関数の ID。`delivery` の行を `verify` のオブジェクトへ組み替え、`channel` を落とす）を足し、
当たった 1 要素を変換して置いて要素を消す（0 要素なら移さず、2 要素以上なら止める。#3613 の設計）。新しいファイルを作るときの中身は `{"version": 1}` にする。セクションのスキーマが `version` を必須にし、`{}` では通らないためである。

**取り込みの順序。** 移す行を持たない改名表のまま、#3587 が `worktree.schema.json` などから移したキーを外すとする。すると ai-plugins の CI の規約の規則
spec-decl（`.ndf/` の実物を改名表で変換してからスキーマで確かめる。#3613 の決定 13）が、ai-plugins の `.ndf/worktree.json` の `base_branch`・`branch_flow` を
旧いスキーマに無いキーとして落とす。逆に、移す行を先に develop へ入れると、改名表とスキーマの突き合わせ（#3613 の I9）が、まだ無いセクションのスキーマを
指す行として落とす。そのため、#3613 の続きの PR は #3587 の head ブランチ（`sprint/lrel-lock`）をマージ先にし、#3587 のマージで一緒に develop へ入れる。
#3613 の最初の実装 PR（今の 4 行）は、決定 26 のとおり #3587 より先に develop へ入る。

- 作り方: 標準に倣う
- 比べた候補: STD3

根拠: Value 11 / Value 14 / C7（MVV 版 7）

### 決定 30: #3615 のリリースの形の設定は、ブランチ戦略のセクションのキー `release` の下に置く

セクションのキー `release` は、リリースの形（何をどこへどの形で出すか）の設定の入る場所である。この課題が置くのは `release.version_store`（決定 22 の
バージョンの保存先のアダプタの選択）だけで、`release` の下のほかのキーは #3615 が決める。

| `release` の下のキー | 持ち主 |
| --- | --- |
| `version_store`（`kind: "plugin"`・`kind: "json"`） | #3572 |
| `version_store` の `kind` の足し（`pubspec.yaml`・Gradle・タグだけ など） | #3615 |
| リリースの形（#3615 の本文の `release.form`）・タグ・CHANGELOG の見出し・リリースブランチの名前の形 | #3615 |
| `.ndf/release.json` の `steps`・`changelog`・`version_files`・`undistributed` を `release` の下へ移すか | #3615 |

`release` の下にほかのキーを足すときも、組み合わせの検査はセクションのスキーマの `if`/`then` で書く（決定 28）。`release` は `additionalProperties: false` で、
#3615 が足すまではほかのキーを書いた設定が落ちる。

- 作り方: 標準に倣う
- 比べた候補: PRD1 / PRD4

根拠: Value 4 / Value 11（MVV 版 7）

### 決定 31: 既存のブランチ戦略（`merge_base_branch`・`release_branch`）のバージョンの上げ方はセクションに置かず、リリースブランチの経路と `.ndf/release.json` に残し、セクションへ移すのは #3615 に任せる

既存のブランチ戦略では、リリースブランチの経路（`assemble`・`gather`・`tagging`）がバージョンを上げる。読むのはプラグインの登録（`.claude-plugin/marketplace.json`）と `.ndf/release.json`
（`version_files`・`steps`）である。セクションの `release.version_store` は読まない。既存のブランチ戦略の `bump` は `not_applicable` を返す。読まれない値を書けないよう、
セクションのスキーマは `trunk` でない戦略で `release.version_store` を落とす（決定 28 の 2）。develop を持つ Web サービスの設定の例（設計文書の「ブランチ戦略のセクション」）は
`release.version_store` を持たない。

**要求との差（受け入れ条件 2l・2r）。** 受け入れ条件 2l はセクションだけを読めばバージョンの上げ方が分かることを、2r は develop を持つ Web サービスの例に
`package.json` のバージョンを求める。既存のブランチ戦略では、どちらもセクションの中では満たさない。既存のブランチ戦略でセクションの `release.version_store` を読むと、既存のブランチ戦略のリリースブランチの経路を
バージョンの保存先のアダプタへ移すことになる。これは決定 22 の表で #3615 の範囲にした変更で、受け入れ条件 18（既存のブランチ戦略は今と同じに動く）にも当たる。
差を受けるのは、受け入れ条件 18 と #3615 との境（決定 22・30）を守るためである。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 既存のブランチ戦略でもセクションの `release.version_store` を読み、リリースブランチの経路をバージョンの保存先のアダプタへ移す | 採らない。受け入れ条件 2l・2r は満たすが、既存のブランチ戦略の振る舞いが変わり受け入れ条件 18 に当たる。決定 22 の表で #3615 の範囲にした変更を先取りし、#3615 の 11 種との対応の表を持たずに作ると食い違う |
| 既存のブランチ戦略でも `release.version_store` を書けるようにし、今の経路は読まない | 採らない。書いた値が黙って使われない設定を許す（決定 28 の 2） |
| 既存のブランチ戦略のバージョンの上げ方をセクションの外に残し、`trunk` でない戦略の値をスキーマで落とし、セクションへ移すのは #3615 に任せる | 採る |

- 作り方: 標準に倣う
- 比べた候補: PRD1

根拠: Value 4 / Value 11（MVV 版 7）
