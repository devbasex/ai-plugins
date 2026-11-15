# #3641 の決定の記録: 今の NDF に GitHub flow を入れ、ai-plugins を移す

設計文書は [issue-3641-design.md](issue-3641-design.md) にある。切り替えの手順は [issue-3641-design-switch.md](issue-3641-design-switch.md) にある。
外部の調査の番号（`STD<数>`・`PRD<数>`・`LIB<数>`）は設計文書の「外部の調査」の表を指す。

## 決定の記録

### 決定 1: 今の NDF で GitHub flow を選べるよう、ブランチ戦略の一覧に `trunk` を足し、キーは今の `promotion` のまま値の名前を #3572 にそろえる

今の NDF のブランチ戦略は `branch_flow.promotion` の値で行を選ぶ（`lib/branch_flow.py` の `PROMOTIONS`）。ブランチ戦略を 1 つ足せば、分岐元・マージ先・残すかの判定を読む今のコード（`worktree-branch.sh` の `wt_start_branch`・`ResolvedFlow`）はそのままブランチ戦略の値に従う。
値の名前は #3572 の設計と #3613 の改名表が使う `trunk` にする。`start`・`integrate` の値も #3572 のブランチ戦略 `trunk` と同じ（`production`・`release`）にする。11.0 の移行では、改名表の行 `/branch_flow/promotion` がキーを `release_strategy` へ移し、値は `trunk` のまま残る（設計文書の「設定ファイルの形」の表）。

キーを今 `release_strategy` へ改めることは採らない。今の NDF の読み手（`branch_flow.py`・`check-pr-base.sh`・`ci-heavy-skip.py`・`worktree-branch.sh`）を、2 つの名前で読むように変える必要がある。改名は #3613 が 11.0 で持つ（要求の「含まない」）。
`whole` のまま `base_branch` を main にすることも採らない。`whole` は開発版のステージと同期の PR を作成し、スプリントの PR をマージのコンポーネントで承認ゲートを通さずに main へ入れる。

- 作り方: 標準に倣う
- 比べた候補: STD2

根拠: Value 4 / Value 11 / P1（MVV 版 7）

### 決定 2: 文書だけでリリースが起きないよう、文書の PR は main へ入れ、承認ゲートを通らないマージには配るプラグインを変える PR を通さない

ブランチ戦略 `trunk` の文書の PR のマージ先は `base`（= main）にする。#3572 のブランチ戦略 `trunk` は `sprint`（スプリントブランチ）だけを許す。一方、今の NDF のまとめのステージ（確定仕様化の PR）はスプリントの PR のマージの後に実行され、ブランチ戦略 `trunk` ではその時点でスプリントブランチが消えている。マージ先を `sprint` にすると確定仕様化の PR のマージ先が無くなる。
文書だけの PR が main へ入っても、CD はリリース対象のプラグインに変化が無ければバージョンを上げない（I9）。そこで、承認ゲートを通らないマージ（`merge-when-green` の `--gate-approved` の無いマージ）が、配るプラグインの中身を変える PR を止める（I5）。版上げの PR は版数と説明だけの差分で通す（`ci-heavy-skip.py` の `version_only` と同じ判定を使う）。
11.0 の移行では、ブランチ戦略 `trunk` に限って `doc_prs` を `base` から `sprint` へ書き換える行が #3613 の改名表に要る（設計文書の「影響」）。

設計 PR をスプリントブランチへ入れ、確定仕様化をスプリントの PR の中で行う形は採らない。スプリントのステージの並び（設計 PR のマージ → スプリントブランチ → … → まとめ）を `trunk` だけ並べ替えることになり、最小限の範囲を超える。

根拠: Value 1 / Value 4（MVV 版 7）

### 決定 3: main へのマージをそのままリリースにするため、スプリントの PR を本番の PR にし、検査のプランのマージを外して本番のステージへ渡す

ブランチ戦略 `trunk` では、検査のステージ（`plan_sprint_check`）は main 宛てのスプリントの PR を作り、リファクタリング（cross-refactoring）と cross-review を通す。マージのステップ（`merge-gate`・`merge`）は持たない。リリース承認ゲートの後の本番のステージが、その PR（`{queue_pr:check}`）を本番の PR として承認・マージする。
開発版のステージは、どの進め方（`normal`・`auto`）でも置かない。開発版チャネルはベースブランチに載るチャネルで、ブランチ戦略 `trunk` ではベースブランチが本番ブランチと同じ（I1）であり、main 以外に変更の載るブランチが無いためである。開発版を外す層はブランチ戦略で、リポジトリごとの設定の値ではない（I8・I12）。ブランチ戦略 `whole`・`selected` の開発版のデプロイの機能は変えない。
並列のレーンは、`selected` と同じくまとめたリリースへリクエストを送らず（`sprint.py` の `release_lane` が `None` を返す）、自分のリリースのワークフローで出す。まとめたリリースは develop からリリースブランチを作成する形で、ブランチ戦略 `trunk` には当てはまらない。
進め方 `fast` は断る（`fast_flow_refusal`）。`fast` はスプリントブランチを作らずに分岐元へ直接入れ、`trunk` では分岐元が main のため、承認ゲートの前に main へ入る。
即時修正のプランなど、スプリントの外のプランが main 宛てにコードの PR を出したときは、マージのコンポーネントが I5 で止め、リリースのワークフロー（`trunk` の種類、PR の番号を受ける）へ戻す。

- 作り方: 標準に倣う
- 比べた候補: STD2 / PRD1

根拠: Value 1 / Value 5（MVV 版 7）

### 決定 4: 承認の外のコミットが積まれないよう、`trunk` のリリースのワークフローをサブコマンドだけで作成する

リリースのワークフローの種類 `trunk` の並びは次のとおりで、作業のステップ（`type: work`）と修正ステップを持たない（I7）。

1. 承認資料
2. リリース承認ゲート
3. `trunk-steps.py land`
4. `trunk-steps.py confirm`
5. 導入の確かめ（`release-verification-steps.py verify-install --ref <本番ブランチ>`）
6. 記録
7. クリーンアップ

CI が落ちた・キューから外れた・head が動いたときは止まり、スプリントの経路（実装と検査のプラン）へ差し戻す。#3592 で今の NDF のリリースのワークフローから修正ステップを外した形（`judge_fix_steps(fix=False)`）を引き継ぐ。
承認資料の範囲は「main の HEAD..本番の PR の head」で、リリースブランチも版上げのコミットも作成しない。予定のバージョンはバージョン種別のラベルから出し、確定のバージョンは CD が決める。

根拠: Value 3 / Value 13（MVV 版 7）

### 決定 5: 同時に承認された PR の組み合わせを確かめてから入れるため、マージの方式を `.ndf/project.json` の `production_merge.method` で選び、ruleset との一致を確認する。ai-plugins は `merge-queue` を選ぶ

ブランチ戦略 `trunk` では、スプリントの PR が直接 main へ入る。ruleset の `strict_required_status_checks_policy` は偽である（要求の前提 4）。このままでは、ほぼ同時に承認された 2 本の後の 1 本が、先の 1 本を取り込まずに CI を通らない組み合わせで入る。
組み合わせの確かめは GitHub の merge queue に任せる（LIB1）。`strict` は、merge queue を使えないリポジトリのために、同じ設定の別の値として持たせる。使えないのは、Organization の public でも GitHub Enterprise Cloud の private でもないリポジトリである。キーは #3572 の決定 17 と同じ `production_merge.method` で、11.0 の移行でキーの位置を変えずに読める。
ブランチ戦略 `trunk` では方式の記述を必須にし、無い・表に無い値・ruleset と食い違う、のどれでもマージの前に止まる（I6）。既定の方式で代用しないのは、代用した方式が ruleset の実際と食い違えば、組み合わせの確かめが黙って抜けるためである。

ai-plugins は `merge-queue` を選ぶ。`strict` では、CD の版上げの PR がマージされるたびに、開いて承認を待つほかのスプリントの PR がすべて main の HEAD を含まなくなり、取り込みと承認の取り直しが要る。並列のレーンは最大 6 本で、人の承認の回数がリリースの回数に比例して増える（Value 1）。merge queue は作者の取り込みなしで同じ確かめを行う（STD3）。

| 比べた候補 | 採否と理由 |
| --- | --- |
| 自前で組み合わせのブランチを作成して確かめる | 採らない。merge queue が同じことを持つ（STD3） |
| 設定を書かせず ruleset から方式を推す | 採らない。リポジトリの持ち主の意図と ruleset の食い違いを検出できない |
| `merge-queue` だけを作る | 採らない。merge queue を使えない GitHub flow のリポジトリ（Organization の public でも GitHub Enterprise Cloud の private でもないもの）が選べない |

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: LIB1 / STD3 / STD1

根拠: Value 1 / Value 4 / Value 12（MVV 版 7）

### 決定 6: 承認済みのコミットだけを入れるため、`merge-queue` では承認済みのコミットを `expectedHeadOid` に渡してキューへ入れ、`strict` では祖先を確かめて `sha` 付きでマージし、どちらも迂回しない

`land` は始めに PR の状態を読む。マージ済みなら人のマージとして、マーカーのあるコメントが無いときだけ人のマージの記録（承認済みのコミット・マージコミット・方式）を書いて `ok` にする（I11）。人の GitHub でのマージはリリース承認と同じ扱いである。
マージされていなければ、PR の head と承認済みのコミットを比べ、違えば `head_moved` で止まる（I4）。続けて設定の方式と ruleset（`GET /repos/{o}/{r}/rules/branches/{branch}` と GraphQL の `mergeQueue(branch)`）の一致を確認する。ruleset がレビューの承認を求め（`required_approving_review_count` が 1 以上）、PR の `reviewDecision` が `APPROVED` でなければ、`precondition` で止まる（I6）。
`merge-queue` では `enqueuePullRequest(expectedHeadOid=<承認した head>)` を実行する（STD7）。追加の成否は応答の形に頼らず、追加の後の PR の `isInMergeQueue` で読む。すぐに入れられない PR に `enqueuePullRequest` がエラーを返すか、自動マージに切り替わって成功を返すかはページに書かれていない（設計文書の「未確認のまま残ること」）。キューに無ければ、`autoMergeRequest` があるとき `disablePullRequestAutoMerge` で外し、`not_enqueued` で止まる。追加の後は `ci_budget` の秒まで PR の `merged`・`isInMergeQueue` を読み、外れたら `queue_removed` で止まって差し戻す。同じ head で入れ直さない（外れた理由が組み合わせの失敗なら、同じ組み合わせで落ちる）。
`strict` では head が main の HEAD を祖先に持つか（`compare` の `behind_by` が 0）を確かめ、含まなければ `behind` で止まる。含めば承認済みのコミットを `sha` に渡して（STD1）マージする。
どちらの方式でも、`production_merge.admin` が真でも `--admin` を実行しない。迂回は必須のチェックと最新の取り込みの規則も越え、確かめていない組み合わせが入る（STD6）。

`gh pr merge --match-head-commit` に本番の PR の merge queue への追加を任せることは採らない。チェックの前なら自動マージを有効にする（STD8）ため、`land` の終わりの後に GitHub が入れることになり、`land` の待ちと止まった理由の外でマージが起きる。

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: STD1 / STD4 / STD7 / STD8 / LIB1

根拠: Value 3 / Value 7 / C2（MVV 版 7）

### 決定 7: PR がバージョンを触らずに済むよう、CD がマージの後に版上げの PR を作り、自動マージで入れてからタグを作る

main への push で `trunk-steps.py cd` が動く（設計文書の「処理の流れ」の CD の 1 回の実行）。HEAD のバージョンにタグが無ければ、今の `publish` でタグと GitHub Release を作る。範囲（最後の版上げのコミット..HEAD）にリリース対象の変化があり、開いた版上げの PR が無ければ、main の HEAD から `release/v<バージョン>` を作る。
書き換えは今のバージョンアップ（`release-steps.py` の `plan_bump` が書く `plugin.json`・`marketplace.json`・README の表）と CHANGELOG のセクション（決定 11）である。今のリリースブランチの版上げのコミットと同じファイルを書く。バージョン種別は範囲の PR のラベルの最大で、ラベルの無い PR は PATCH にする（#3572 の決定 7。付け忘れでマージを止めない）。
PR を作った後に `gh pr merge --merge --match-head-commit <SHA>` で自動マージをリクエストする。キューのあるブランチでは、必須のチェックの前なら自動マージが有効になり、後ならキューへ入る（STD8・LIB4）。CD の実行は PR のチェックを待たずに終わる。
`strict` では、版上げの PR が開いている間に main が進むと、版上げの PR が main の HEAD を含まなくなり、マージされない。そこで main への push ごとの CD の実行が、範囲の判定の前に、開いた版上げの PR へ main を取り込む（`update-branch` に読んだ head を渡す）。取り込んだ後は、新しい head で自動マージを再リクエストする（設計文書の「処理の流れ」の手順 2）。版上げの PR へ書くのは CD の実行だけで、`concurrency` の `release-cd` が 1 本ずつ走らせるため、書き手が重ならない。
版上げの PR は人の承認を持たない。中身は版数と説明だけで（I5 の判定）、PR の検査（I3）が版上げの PR に限って版数の変更を通す。

| 比べた候補 | 採否と理由 |
| --- | --- |
| CD が main へ直接 push する | 採らない。ruleset の迂回の形が「For pull requests only」で、直接の push は断られる（STD6） |
| ruleset の迂回に GitHub Actions を `always` で足し、直接 push する | 採らない。書き込みの権限を持つ者がほかのブランチの workflow から main の保護を越えられるようになる |
| スプリントの PR の中でバージョンを上げる | 採らない。要求の受け入れ条件 4 が禁じ、同時に承認された PR が同じバージョンを取り合う |
| release-please-action（LIB2）・semantic-release（LIB3） | 採らない。バージョン種別をコミットの書式から読み、このリポジトリの書式と CHANGELOG の形に合わない |
| 今のバージョンアップと CHANGELOG の書き換えを使い、PR を CD が作る | 採る |

- 作り方: 標準に倣う・自前で作る
- 比べた候補: PRD1 / PRD2 / LIB2 / LIB3 / LIB4 / STD6 / STD8

根拠: Value 3 / Value 11 / P1（MVV 版 7）

### 決定 8: 版上げの PR の CI を起こすため、シークレット `NDF_RELEASE_TOKEN` を足す

`GITHUB_TOKEN` で作った PR では、`pull_request` の実行が承認待ちの状態で作られ、必須のチェックが自動で走らない（STD5）。そのため版上げの PR の作成と push は、別のトークンで行う。
ai-plugins にはリポジトリのシークレットも Organization のシークレットも無い。次の 2 つの照会がどちらも `0` を返した（2026-11-16）。

```text
gh api repos/devbasex/ai-plugins/actions/secrets --jq .total_count
gh api repos/devbasex/ai-plugins/actions/organization-secrets --jq .total_count
```

要求の非機能の条件が挙げる `secrets.NDF_RELEASE_TOKEN` は、`ndf-next-release.yml` が参照するが置かれていない。この課題で同じ名前のシークレットを 1 つ足し、11.0 の ndf-next のリリース（#3572 の決定 7）も同じシークレットを使う。
種類は fine-grained PAT にし、対象をこのリポジトリだけ、権限を `contents` と `pull_requests` の書き込みに限る。シークレットが無ければ `cd` はタグの作成までを済ませ、版上げの PR を作らずに `precondition` で止まり、理由にシークレットの名前を出す。マージそのものは止まらず、シークレットを置いた後の push で残りの範囲がバージョンアップへ進む。

GitHub App のトークン（App の ID と鍵の 2 つのシークレット）は採らない。シークレットが 2 つになり、#3572 の workflow が参照する名前とも合わない。

根拠: Value 2 / C1（MVV 版 7）

### 決定 9: キューの一時マージコミットにチェックが報告されるよう、必須のチェックを出す workflow 8 本に `merge_group` を実装の PR で足す

merge queue は `merge_group` で起動しない workflow のチェックを受け取れず、マージが失敗する（STD3）。必須のチェック 17 個は 8 本の workflow が出す。`.ndf/project.json` の `ci.required_checks` のジョブの名前を `.github/workflows` で検索した結果である。

- `runtime-plugin-validate.yml`・`glossary.yml`・`pr-base-guard.yml`・`lint.yml`
- `pr-body-decisions.yml`・`pytest.yml`・`script-structure.yml`・`runtime-plugin-smoke.yml`

`merge_group` はキューが無い間は何も起こさないため、ruleset を変える前に実装の PR で足す。PR の本文やマージ先を読むチェック（`guard`・`pr-body-decisions-check`）は、キューの一時マージコミットでは判定を行わずに成功で終わる。キューへ入れられるのは、PR の時点で必須のチェックを満たした PR だけだからである（STD3）。

- 作り方: ライブラリやサービスを使う
- 比べた候補: STD3 / LIB1

根拠: Value 3 / Value 12（MVV 版 7）

### 決定 10: 11.0 で置き換える範囲を 1 か所で外せるよう、GitHub flow の処理を新しいエントリポイント `trunk-steps.py` と新設のモジュールに置き、ai-plugins の設定の書き換えは別の PR にする

`release-steps.py` は行数の上限の例外（1425 行）ちょうどで、`merged_lib/merge.py` は 500 行である。どちらも足すと構造の検査（`scripts/check-script-structure.py` の `lines`）が落ちる。`land`・`confirm`・`cd`・`pr-check` は `trunk-steps.py` に置く。処理は `release_lib/merge_method.py`（main へのマージ）・`trunk_cd.py`（main のバージョン）に置く。既存の 2 ファイルには呼び出しだけを足す。11.0 の入れ替えで ndf-next の `land`・`bump` に置き換わるとき、外すのは新設のファイルと呼び出しの行になる。
バージョンアップ・CHANGELOG・`publish`・承認の記録の読みは、今の `release-steps.py`・`release_lib/` の関数を呼び、写さない。

次の変更は実装の PR に入れない。

- ai-plugins の設定（`.ndf/worktree.json`・`.ndf/project.json`・`.ndf/supervise.json`）
- `AGENTS.md`・`CONTRIBUTING.md`・`docs/versioning-and-distribution.md`
- 開発版の workflow（`release-dev.yml`）の削除

実装の PR は develop へ入り、develop の設定が `trunk` へ変わると、main へ移る前に新しいスプリントが main から切られる。設定の PR は切り替えの手順で出す。最後のリリースの後に main 宛てに出す（ [issue-3641-design-switch.md](issue-3641-design-switch.md) の手順 4）。

根拠: Value 9 / Value 10 / Value 11 / C7（MVV 版 7）

### 決定 11: CHANGELOG を残し、CD が版上げのコミットでバージョンのセクションを書く

PR は `CHANGELOG.md` を触らない。版上げのコミットで、範囲に入った PR から今の CHANGELOG の書き換え（`release-steps.py` の `cmd_changelog`・`cmd_notes` の関数）がバージョンのセクションを作成する。GitHub Release の本文は今の `publish` のとおりそのセクションから作る。
CHANGELOG を読むスクリプトは、テストを除いて今の NDF だけで 14 ファイルある（#3572 の決定 11 の表）。廃止すると 11.0 までに読み手をすべて書き換えることになる。

| 比べた候補 | 採否と理由 |
| --- | --- |
| CHANGELOG をやめ、GitHub Release を正本にする | 採らない。読み手の書き換えが最小限の範囲を超える |
| 今の書き換えの関数を版上げのコミットで呼ぶ | 採る |

- 作り方: 標準に倣う
- 比べた候補: PRD1

根拠: Value 2 / Value 11（MVV 版 7）

### 決定 12: develop にある変更を利用者へ届けてから移るため、最後の develop→main のリリースを今の `whole` の手順で出し、その後に設定を書き換える

起票の時点で develop にあって main に無いコミットは 133（要求の前提 2。2026-11-16 の設計の時点で `git rev-list --count origin/main..origin/develop` は 167）ある。最後のリリースを今の手順で出せば、承認資料・リリース承認ゲート・CD のタグがどれも今の仕組みのまま使え、新しい経路を作らない。
設定の PR は、最後のリリースと同期の PR のマージの後に `origin/main` から切る。今の main の設定（`whole`）の PR のマージ先の検査は、develop か develop の祖先の `release/v` 以外の head を断る。そのため、設定の PR は必須のチェックの `guard` が落ちる。人が GitHub で、今の ruleset の迂回（管理者・PR に限る）を使ってマージする。人のマージはリリース承認と同じ扱いで、C7・P1 の変更を人が見る場になる。

develop の変更を cherry-pick で main 宛ての PR に移す形は採らない。133 以上のコミットを選び直すことになり、承認の範囲が今の承認資料の組み立てと食い違う。
設定の PR を develop へ入れてから最後のリリースに含める形も採らない。develop の設定が `trunk` へ変わった時点で、新しいスプリントとまとめたリリースが main を分岐元に作成される。

根拠: Value 3 / Value 5 / P1 / C7（MVV 版 7）

### 決定 13: 開発版の利用者が壊れずに残るよう、develop は消さずに更新を止め、移ることは最後のリリースの説明と正本の文書で知らせる

develop を消すと、取得元に `#develop` を登録した利用者（要求の前提 9）は更新のたびに取得に失敗する。残せば、最後の開発版のまま動き続ける。消すことは戻せない操作（C4）で、この課題では行わない。
develop の ruleset（承認 0）は変えない。切り替えの後は NDF の工程が develop 宛ての PR を作らない（マージ先が main になる）。
知らせる場所は次の 3 つにする。

- 最後のリリースの CHANGELOG のセクションと GitHub Release の本文
- `docs/versioning-and-distribution.md` の取得元のセクション
- `AGENTS.md` の「版とリリースの方針」

書く内容は「develop は更新を止めた。`marketplace add devbasex/ai-plugins`（ref なし）で登録し直す」で、登録し直しの手順は正本の文書が持つ。

利用者の環境（SessionStart の通知など）へ知らせる形は採らない。最後の開発版へ通知のコードを入れるには develop へもう 1 回リリースが要り、開発版をやめる決定と食い違う。

根拠: Value 1 / Value 6 / C4 / C6（MVV 版 7）
