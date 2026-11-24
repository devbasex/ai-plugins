# 自動反映の本番チャネルへのマージ（リリース承認ゲート）

`SKILL.md` の「本番系へ届く操作」のうち、Pull Request のマージがリリース承認ゲートに当たる場面の細部。
次の 3 つの場合は、承認を得てからマージする。詳しい判定は下の「マージ先ごとの判定」の表にある。

- 設定から本番チャネルを特定できない（マージ先を問わない）
- 本番チャネル宛てで、自動で反映する設定がある
- 本番チャネル宛てで、反映の方法の設定（`release.deliveries`）が無いか形が違う

## 例: project-trygroup-prd の 2 本のマージ

次の例は、`release.deliveries` に自動で反映する設定を書いた構成である。自動で反映する設定は、`release.deliveries` の
`channel: production` で `kind` が `manual` でない行である。
ブランチ戦略の設定（`.ndf/project.json` の `branch_strategy`）が `branches.base: develop`・`branches.production: main` を持ち、
`release.deliveries` に、stg へ自動で反映する `channel: base`・`kind: auto` の行と、prd へ自動で反映する `channel: production`・`kind: auto` の行を持つとき:

1. 実装の Pull Request（`develop` 宛て）は、`develop` が本番チャネルでないため止まらずにマージする
2. リリースの Pull Request（`develop` → `main`）は止まる。マージ先の `main` が `branches.production` に設定され、`release.deliveries` に
   `channel: production` の自動反映の行があるためである。マージのステップ（`ndf ci merge`）は承認の証跡を見つけられずに承認待ち（終了コード 10）で止まり、承認資料を書く
3. 利用者が承認したら、返された経路（`--answer approved --by human`）で回答し、証跡を書いてからマージのステップを打ち直す

**設計 Pull Request のマージ先が自動反映の本番チャネルなら、そのマージは本番への反映も起動する。** 設計承認ゲートの承認資料に
「このマージで本番へ出る」ことを書き、1 回の承認（`AskUserQuestion` の `metadata.source` は `ndf-gate:1`）で両方を通す。承認を得たら conductor が
回答を記録し（`ndf answer --by human`。記録の前に証跡を書く）、工程の `design-merge` がその証跡でマージする。1 回のマージに 2 回の承認を求めない。

## リリースの場合ごとの承認の場所とマージの主体

**本番チャネルへのマージのリリース承認を取る場所とマージする主体は、リリースの場合で決まる**（4 つの場合の意味は `release` の Skill の「リリースの場合」）。

| 場合 | リリース承認を取る場所 | マージする主体 | NDF が打つもの |
| --- | --- | --- | --- |
| CD があり、レビューなしでマージできる（`cd-no-review`） | マージの前。リリースの PR へ承認資料と承認の証跡をコメントで置く | 利用者のアカウントで NDF が `--admin` なしにマージする。main の必須チェック `ndf/release-approval` が証跡を確かめる | `release --channel prod`（承認の前は 10）→ `approval-post` → 承認 → `approve --pr` → `release --channel prod`。リリースの Pull Request の経路は `promote --gate-approved` がマージの前に証跡を書く |
| CD があり、レビューが必須（`cd-review`） | リリースの PR のレビューの承認 | 利用者のアカウントで NDF が `--admin` なしにマージする | 証跡を書かない。承認のレビューのコミットが HEAD と一致したときだけマージする |
| CD があり、レビューが必須で、迂回の設定がある（`cd-admin`） | マージの前。リリースの PR へ承認資料と利用者の承認の証跡（`--by user`）をコメントで置く | 利用者のアカウントで NDF が管理者の迂回（`--admin`）でマージする | `cd-no-review` と同じ並び。MVV 判定のプランでも承認ゲートで止まる。マージの後に迂回の記録を置く |
| CD が無い（`no-cd`） | 手元の承認資料 | NDF（下の「マージ先ごとの判定」のとおり） | 今の `release` の工程 |

CD のある場合のマージの後は、CD の workflow がタグと GitHub Release を作り、NDF は `cd-result` で結果を読んでリリース記録へ残す。
会話の中でタグを打たない。マージの前のテストは CI の必須チェックが受け持ち、リリースの手順は全体テストを流し直さない。

## マージ先ごとの判定

| マージ先 | 判定 | NDF が行うマージ |
| --- | --- | --- |
| 本番チャネルでない | 当たらない | そのまま進む |
| 自動反映の本番チャネル | 当たる | 承認を得るまで進めない |
| 本番チャネルで、`release.deliveries` が無い・形が違う | 当たるとして扱う。`release.deliveries` が無ければ `release.cd` を読み、CD の設定がある（自動で反映する）、`null`（未設定）、無い・形が違う（決められない）のどれでも当たる | 承認を得るまで進めない。`release.deliveries` を設定すれば次から判定できる |
| 設定を読めず、本番チャネルを特定できない（設定のファイルが無い・JSON として読めない・`branches.production` が無い） | マージ先にかかわらず当たるとして扱う | 承認を得るまで進めない。設定を直せば次から判定できる |
| 本番チャネルで、`release.deliveries` に `channel: production` で `kind` が `manual` でない行が無い（手で反映・リリースしない） | 当たらない | そのまま進む。本番への反映は `release` の工程で承認を取る |

**判定は PR のベースリポジトリのマージ先のブランチにある `branch_strategy` とマージ先だけで決める。** 判定の規則は
`ndf.core.production_merge` の 1 か所にあり、プランのマージのステップが打つ `ndf ci merge` と、エージェントが直接打つ
`gh pr merge` を止める hook の guard `merge-production`（`contracts/hooks/guards.json`）が同じ規則を使う。作業場所や
手元の clone の設定は読まない（マージする PR 自身が設定を書き換えても判定を外せない）。人の手によるマージ（端末・GitHub の画面）は止めない。

**当たるマージは、PR に残ったマージの承認の証跡（マーカー `ndf:merge-approval`）があるときだけ `ndf ci merge` がマージする。**
証跡は承認した PR・HEAD・マージ先に結び付き、承認の後に HEAD かマージ先が変われば使わない。証跡を書くのは次の 3 つの経路だけで、
エージェントが `gh pr comment`・`gh api` で書く操作は hook が止める。

| 回答の取り方 | 証跡を書く側 | 担い手 |
| --- | --- | --- |
| 工程の承認ゲート（設計承認ゲート・リリース承認ゲート）への `ndf answer --by human`（オーガナイザーが利用者の回答を中継したときを含む） | 回答を記録する前の `ndf answer` | `user` |
| `pace: fast` / `auto` の MVV 判定が承認ゲートを通した | MVV 判定の記録のコメント（同じ 1 件に行を入れる） | `mvv` |
| 単発のプランの承認待ちへの `ndf plan next <プラン> --answer <回答> --by human` | ステップ `approve-*`・`reject-*` の `ndf ci answer` | `user` |

**証跡が無いときの終え方は、プランを組んだ所で決まる**（`ndf ci merge --production-gate`）。

| プラン | 終え方 | 承認を取る所 |
| --- | --- | --- |
| 工程の Pull Request（`pr-merge`）・ドキュメントレビュー（`docs-merge`）・確定仕様化・承認ゲートの無い設計（light） | `defer`: マージせずに `ok`（`metrics.merge: deferred`）で工程を先へ進める。PR は開いたまま | デプロイのリリース承認ゲート。承認の後、テンプレート `release` の最初のステップ `release-merge` が同じ PR をマージする |
| 工程の設計 PR のマージ（`design-merge`）・デプロイの `release-merge` | `stale`: 承認の後に対象が変わった・証跡が無いときは `approval_stale` で止まる | 工程の結果の `routes.retry`（工程を最初から流し直す記録）と `next`（工程を流す）のコマンドを順に打ち、同じ工程の承認ゲートが今の対象で質問し直す |
| 単発のプラン（`ndf route run` の `fix`・`close`・`merge`） | `stop`: 承認待ち（終了コード 10、`metrics.gate: production-merge`）で止まる | 結果の `routes` の `ndf plan next ... --answer approved --by human` を打つと、`approve-merge` が証跡を書いて `merge` を打ち直す |

承認ゲートを既に持つ単発のプラン（`check`・`design-merge`）は、`merge-gate`（`ndf ci ask`）への回答の後に `approve-gate` が証跡を書くので、
`merge` でもう一度止まらない。どの承認資料も、承認でマージが本番へ反映され得るときは「この承認で PR がマージされ、本番へ反映され得る」と書く。

**ベースブランチと本番チャネルが同じリポジトリでは、リリース承認ゲートはスプリントで 1 回になる。** スプリントの PR は工程の中では
マージされず、デプロイのリリース承認ゲートの承認の後にデプロイの工程がマージする。工程の順はデプロイ → クリーンアップで、
クリーンアップの時点で PR はマージ済みである。スプリントを通さない `fix`・`close` は Pull Request ごとに止まる
（1 本のマージが 1 回の本番への反映である）。

## 選んで集めるリリース方式のリリース

ブランチ戦略の設定の `release_strategy` が `release_branch` のスプリントは、ベースブランチを本番チャネルへ入れない。
スプリントの開始時に目録へ書いたスナップショットで、リリースのプランが次の順に組む。

1. 開発版のデプロイ（ベースブランチ）の後、承認資料の前にリリースのワークフロー（`ndf release-steps assemble`）がリリースブランチ `release/v<バージョン>` を
   `origin/<本番チャネル>` から切り、そのスプリントのブランチだけを `--no-ff` でマージして push する。衝突すれば止まり、承認資料を出さない
2. 承認資料（`ndf release-steps materials`）の範囲は前の本番のタグからリリースブランチの先頭までで、承認したコミットはその先頭である。
   ベースブランチへ入れた検査の Pull Request は範囲に入らない
3. リリースのワークフローはリリースブランチから worktree を作り、バージョンを上げ、`release --release-strategy release_branch` が `release/v<バージョン>` → 本番チャネルの
   Pull Request を出す。CD の無い場合は、HEAD が承認したコミットに版上げのコミットだけを積んだものであるときだけマージし、承認の後に別のブランチを
   マージした HEAD は承認の外として承認ゲート（10）で止める。CD のある場合は版上げまで積んだ本番の PR の HEAD を承認に出し、HEAD と
   PR の上の承認（証跡かレビュー）が完全に一致するときだけマージする。承認の後のコミットの追加や組み直しで HEAD が動けば止まる。
   組み直すには承認資料を作り直して承認を取り直す
4. 本番チャネルへ入れてタグと GitHub Release を作った後、`release/v<バージョン>-sync` に `origin/<ベースブランチ>` をマージ（版数の衝突は
   本番のバージョンを、CHANGELOG.md の同じバージョンのセクションの衝突はリリースブランチのセクションを取る）、`release/v<バージョン>-sync` → ベースブランチの Pull Request で
   戻す。戻すマージで止まっても本番は出ており、結果は `stopped` で戻すマージの項目だけを失敗として出す。打ち直すと本番のマージと
   公開は済みとして飛ばし、戻すマージからやり直す

`pace: fast` はスプリントブランチを作らないため、このリリース方式と組めない（`new sprint` が計画を書かずに止まる）。まとめたリリースも
このリリース方式では組めない（`parallel.md` の「リリースの組み方」）。
