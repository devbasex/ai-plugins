# #1457: pace: auto / fast の merge・promote の経路で .ndf/pace.json の <節>.verify（導入の確認）が 1 度も走らない

正は課題の本文（#1457）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

**この節は「何を満たすか」だけを扱う。** どう作るか（確認を置くプランとステップ・承認資料の形）は設計が決める。
マイルストーンは「23 汎用性の回復」。版は配布の工程が決める（`release`）。

## 例: 起点が `develop`・本番が `main` で、`release.form` を持たないリポジトリ

宣言が `.ndf/worktree.json` の `base_branch: develop`・`production_branch: main` と、`.ndf/project.json` の
`delivery: [{"kind": "auto", "branch": "develop", "target": "stg"}, {"kind": "auto", "branch": "main", "target": "prd"}]`
を持つ（project-trygroup-prd の形）。`.ndf/pace.json` の `auto.verify` に導入の確認のコマンドがある。

今（develop の dfeb3517）:

1. `supervise.py new sprint --pace auto` は、`auto.verify` が書かれていることを確かめて通す（`pace_refusal`）
2. 検査のスプリントの Pull Request が `develop` へマージされ、stg へ届く（経路 `merge`。ステージを置かない）
3. 「本番」のステージの昇格のプラン（`plan_promote`）が `prepare` → `mvv`（承認ゲート 2 の MVV 判定）→ `note` → `promote` と進み、
   `develop` → `main` の Pull Request をマージする。**`auto.verify` はどのステップからも走らない**

変えた後:

1. 2 のマージの後、承認ゲート 2 の MVV 判定より前に `auto.verify` が 1 度走る
2. 確認が 0 以外で終われば、MVV 判定へ進まずに止まり、`main` へのマージは起きない
3. 確認の終了コードが承認資料に載り、MVV 判定と人が読める

## 依頼（原文）

> ## 何を見つけたか
>
> `.ndf/pace.json` の `<節>.verify`（導入の確認のコマンド）が走るのは、手で届ける経路だけである。`plugins/ndf/scripts/supervise_lib/sprint_routes.py:95-100` の `manual_production_waves` が `sec["verify"]` を承認ゲート 2 の `plan_gate_2`（`release_templates.py:379`、`deploy-facts --verify`）へ渡す（#1454、閉じた）。
>
> `merge`・`promote` の経路では、`supervise.py new sprint --pace auto|fast` の `pace_refusal`（`plugins/ndf/scripts/supervise_lib/sprint.py:199-214`）が「書かれているか」を確かめるだけで、どのプランのステップからも実行されない。`release_templates.py:302` の `plan_promote` のステップ（prepare / promote / promote-approved）に verify は無い。**この課題の範囲は `merge` / `promote` の経路だけである。**
>
> - `release.form` の雛形（`package-plugin`）の経路では、雛形自身の `verify-install` のステップが代わりに走る
> - `delivery` から組む `merge`（ベースブランチへのマージで反映）・`promote`（昇格の Pull Request）の経路の `auto` / `fast` では、導入の確認が 1 度も走らないまま承認ゲート 2 の MVV 判定へ進む
>
> ## どこで見つけたか
>
> #1454（閉じた）の設計中（`plugins/ndf/scripts/supervise_lib/sprint.py` の `pace_refusal`・`route_waves`、`supervise_lib/release_templates.py` の `plan_promote`）。
>
> ## なぜこの変更の範囲外なのか
>
> #1454 の受け入れ条件（AC6）が `<節>.verify` を走らせる置き場を求めるのは、起点と本番チャネルが同じで本番系へ届く行がすべて手動の形だけである。`merge` / `promote` の経路は対象範囲の「起点と本番チャネルが違うリポジトリの工程の変更」を含まない側に当たる。根拠: Value 3 / Value 6（MVV 版 1）
>
> ## 直さないと何が起きるか
>
> `pace.md` の「使ってよい条件」は「インストール確認がある」を条件に数えるが、`merge` / `promote` の経路の `auto` / `fast` では確認が走らないため、導入が壊れた変更でも MVV 判定が承認ゲート 2 を通しうる。影響は `release.form` を持たず `delivery` で経路を組むリポジトリの `auto` / `fast` に限る。
>
> ## 由来
>
> issue #1454

## 今の状態（2026-09-30 に develop の dfeb3517 で確かめた）

- `supervise_lib/sprint_routes.py` の `route_waves` が `new sprint` と `new close`（`sprint.py` の `close_waves`）の検査の後のステージを組む
  - 開発版のチャネルが手動反映の本番系（`manual-production`）なら `manual_production_waves` が承認ゲート 2 のプラン `gate-2` を置き、
    その `facts`（`release-steps.py deploy-facts --verify`）が `<節>.verify` を走らせる
  - それ以外（起点と本番チャネルが違う `separate-branch`）では、経路 `promote` があれば「本番」のステージに `plan_promote` を置き、
    経路 `manual` があれば手で行う「リリース」のステージを置く。経路 `merge`（`merged-by-check`。ベースブランチへのマージで届く）と
    `none` はステージを置かない
- `plan_promote` の `fast` / `auto` のステップは `prepare`（`merged-steps.py promote --prepare`。Pull Request と承認資料
  `{state_dir}/work/approval-promote.md`）→ `mvv` → `note` → `promote`（`--gate-approved mvv`）→ `promote-approved`・`handoff` で、
  `<節>.verify` を渡す引数も走らせるステップも無い
- `pace_refusal` は `<節>.verify` が空でないことだけを確かめる
- `release-steps.py deploy-facts`（`release_lib/deploy.py`）は、メインディレクトリをベースブランチの先頭へ fast-forward し、HEAD が先頭と違う・
  追跡中の変更があれば確認を走らせずに 3、確認が 0 以外なら 1 で止まる。確認の出力は承認資料へ載せず、所有者だけが読める
  `<承認資料>.verify.log` へ分ける
- ai-plugins は `release.form: package-plugin` を持つため、経路は雛形（`template`）で、この課題の対象の経路を通らない

## 目的

- **`auto` / `fast` で経路 `merge` / `promote` を持つリポジトリでも、`pace.md` の「インストール確認がある」が実際に確認を走らせる意味になる。**
  導入が壊れた変更が、確認なしに MVV 判定だけで承認ゲート 2 を通らない
- **確認の置き方を、手動反映の本番系（#1454）の承認ゲート 2 と揃える。** 同じ役割の確認を経路ごとに別の形で持たない（Value 6）

## 解釈

| 依頼文の語 | 具体化 |
| --- | --- |
| `merge` の経路 | `lib/delivery.py` の `routes` が `STAGE_MERGED_BY_CHECK` を返す経路。`delivery` の `kind: auto` の行で `branch` がベースブランチのもの（開発版のチャネルへ届く） |
| `promote` の経路 | `routes` が `STAGE_PROMOTE` を返す経路。`kind: auto` の行で `branch` が本番チャネル（ベースブランチと違う）のもの |
| 導入の確認（`pace.md` の「インストール確認」と同じもの） | `.ndf/pace.json` の `<節>.verify` に書かれたコマンドを走らせること |
| 昇格のプラン | 経路 `promote` のための「本番」のステージのプラン（`plan_promote`）。ベースブランチ → 本番チャネルの Pull Request を作り、承認ゲート 2 の後にマージする |
| 導入の確認が走る | 選んだ進め方の節（`auto` か `fast`）の `<節>.verify` を、プランのステップが `sh -c` で実行し、その終了コードでステップの合否が決まる |
| 承認ゲート 2 の MVV 判定へ進む | 昇格のプランの `mvv` ステップ（`mvv-gate.py check --gate release`）が走る |

## 前提

- 前提 1: 対象は `supervise.py new sprint` / `new close` の `--pace auto|fast` で組むプランだけで、`normal` は変えない（`normal` の承認ゲート 2 は人が決める）
- 前提 2: 確認を走らせるのは、検査の Pull Request がベースブランチへマージされた後で、確認するコミットはベースブランチの先頭である（`deploy-facts` と同じ）。
  開発版のチャネルへの反映（CI などによるデプロイ）を待つのは `<節>.verify` のコマンドの側の責務で、NDF は反映の完了を待たない
- 前提 3: 開発版のチャネルが `separate-branch` で、経路に `promote` も `merge` も無いスプリント（経路が `manual` か `none` だけ）はこの課題の対象外とする
  （`manual` は手で行う `/ndf:release` のステージが担う）
- 前提 4: 1 つのスプリント（`new sprint` か `new close` の 1 回）の中で、確認は承認ゲート 2 の前に 1 度走れば足りる。`merge` と `promote` の両方の経路があっても 2 度走らせない
- 前提 5: 確認の出力は秘密を含みうるものとして扱い、承認資料（MVV 判定で外部の LLM へ渡る）へ載せない（#1454 と同じ）

## 対象範囲

含む:
- `auto` / `fast` で経路 `promote` を持つスプリントの昇格のプランで、MVV 判定より前に `<節>.verify` を走らせること
- `auto` / `fast` で経路 `merge` を持ち `promote` を持たないスプリント（`separate-branch`）で、検査の後に `<節>.verify` を走らせること
- 確認の終了コードを承認資料へ載せること、確認の出力を承認資料の外へ分けること
- `new sprint` と `new close` の両方
- `pace.md` の経路の表と「使ってよい条件」の説明を、確認が走る置き場に合わせること

含まない:
- 手動反映の本番系（`manual-production`）の経路（#1454 で済んでいる）
- `release.form` の雛形（`package-plugin`）の経路（雛形の `verify-install` が走る）
- `normal` の進め方
- 経路 `manual` の手で行う「リリース」のステージ
- `<節>.verify` のコマンドそのものの中身と、開発版のチャネルへの反映の完了を待つ仕組み
- `pace_refusal` の条件（`<節>.verify` が書かれているか）を変えること

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | スプリントのプランを組んだ（`new sprint` / `new close --pace auto\|fast`） | conductor が打つ | `pace_refusal` が断れば `stopped`（今のまま） | — |
| E2 | 検査の Pull Request をベースブランチへマージした | 検査のプラン | 今のまま（検査のプランが止まる） | E1 |
| E3 | 開発版のチャネルへ反映した | ベースブランチへの push（CI など。NDF の外） | NDF は観測しない。E4 の確認が落ちる形で現れる（前提 2） | E2 |
| E4 | 導入の確認を走らせた | E2 の後のプランのステップ | 0 以外なら止まり、E5・E6・E7 へ進まない。worktree が先頭と違う・追跡中の変更があれば確認を走らせずに止まる | E2（E3 は前提 2） |
| E5 | 確認の終了コードを承認資料へ書いた | E4 | 書けなければ止まる | E4 |
| E6 | 承認ゲート 2 の MVV 判定をした | 昇格のプランの `mvv` | 今のまま（判定が止めれば承認ゲートへ落ちる） | E5（`promote` の経路だけ） |
| E7 | 本番チャネルへの昇格の Pull Request をマージした | 昇格のプランの `promote` | 今のまま（`handoff`） | E6 |

## 受け入れ条件

- [ ] AC1: 開発版のチャネルが `separate-branch` で経路 `promote` を持つ宣言で `new sprint --pace auto` を組むと、昇格のプランに `auto.verify` を走らせるステップがあり、そのステップは `mvv` ステップより前に実行される順で並ぶ
- [ ] AC2: AC1 と同じ宣言で `--pace fast` を組むと、確認のステップが走らせるのは `fast.verify` で、`auto.verify` ではない（2 つの値が違う宣言で確かめる）
- [ ] AC3: 確認のコマンドが 0 以外で終わると、昇格のプランは `mvv`・`note`・`promote` のどれも実行せずに止まり、本番チャネル宛ての Pull Request はマージされない
- [ ] AC4: 確認のコマンドが 0 で終わると、昇格のプランは今と同じ順（`mvv` → `note` → `promote`）で続く
- [ ] AC5: 確認のステップを通った後、`mvv` ステップが読む承認資料に、確認のコマンドとその終了コード、確認したベースブランチの先頭のコミットが載っている
- [ ] AC6: 確認の出力（標準出力・標準エラー）は承認資料に載らず、承認資料と別の、所有者だけが読めるファイル（権限 0600）に残る
- [ ] AC7: 確認を走らせるworktree の HEAD がベースブランチの先頭と違うか、追跡中のファイルに変更があるとき、確認のコマンドは実行されず、プランは `mvv` へ進まずに止まる
- [ ] AC8: 開発版のチャネルが `separate-branch` で、経路に `merge` があり `promote` が無い宣言で `new sprint --pace auto|fast` を組むと、検査の後に `<節>.verify` を走らせるステージが置かれ、確認が 0 以外なら止まる
- [ ] AC9: 経路に `merge` と `promote` の両方がある宣言で組むと、1 つのスプリントの中で `<節>.verify` を走らせるステップは 1 つだけである
- [ ] AC10: `new close --pace auto|fast`（`close_waves`）でも AC1〜AC9 が成り立つ。最終の検査で変更が無く本番のステージが流れないとき（`changed` の skip）は、確認も走らない
- [ ] AC11: `--pace` を付けない（`normal`）とき、昇格のプランのステップは変わらない（`promote` → `promote-approved`）
- [ ] AC12: 手動反映の本番系（`manual-production`）の `gate-2` のプランと、`release.form` を持つ宣言（ai-plugins の `package-plugin`）で組むステージは変わらない
- [ ] AC13: `pace.md` の経路の表が、`merge` / `promote` の経路の `auto` / `fast` で導入の確認がどこで走り、落ちたら何が起きるかを書いている（`doc-lint` が通る）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 確認のステップの上限は、手動反映の本番系の `facts`（1500 秒）と同じ値を使う |
| セキュリティ | 確認の出力を承認資料へ載せない（AC6）。承認資料は MVV 判定で外部の LLM へ渡る |
| 運用・保守性 | 確認を走らせて承認資料へ書く処理は、手動反映の本番系と同じ部品を使い、経路ごとに別の関数を持たない（Value 6） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `supervise.py new sprint` / `new close` の引数は変わらない。組まれるプランのステップが増える |
| データ | 無し。承認資料（`{state_dir}/work/` の下）に確認の行が加わる |
| 既存の振る舞い | `auto` / `fast` の `merge` / `promote` の経路で、確認が落ちると本番チャネルへの昇格が止まる（今は止まらない） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`（`test_supervise_new.py`・`test_supervise_pace_manual.py` の近くに、プランの形と、確認のコマンドを `true` / `false` にして実行したときの止まり方を足す） |
| 静的解析・型検査 | `.ndf/project.json` の宣言のとおり（CI） |
| 文書 | `python3 plugins/ndf/scripts/doc-lint.py`（`pace.md` の変更） |
| 手動確認 | 無し（すべてテストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`。安定版の経路（`supervise_lib/` と `release_lib/` の変更。実験版の置き場は使わない） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストは書かない |
| テスト戦略 | プランを組む関数の単体テスト（ステップの並びと引数）と、`supervise.py run` で確認のコマンドを差し替えて流す結合のテスト |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テストの実行、`pace.md` の更新 |
| 確認してから行う | `pace_refusal` の条件や `.ndf/pace.json` の形を変えること |
| 行わない | `normal` の進め方・手動反映の本番系・`package-plugin` の雛形の変更、`<節>.verify` のコマンドの中身を NDF が決めること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 確認を置く場所（昇格のプランの `prepare` と `mvv` の間か、昇格のプランの前の別のステージか）と、`merge` だけの経路で置くステージの形 | 設計（`design`） | 設計 PR |
| `deploy-facts` をそのまま使うか、昇格の承認資料（`approval-promote.md`）へ確認の行を足す形にするか | 設計（`design`） | 設計 PR |
