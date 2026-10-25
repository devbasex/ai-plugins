# #1914: pace: normal のスプリントでも、本番の配布のプランを new sprint が書き出す

正は課題の本文（#1914）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 要求
>
> `pace: normal` の `supervise.py new sprint` が、release の雛形で配る経路（`リリースの経路: template / package-plugin`）のとき、開発版の配布のステージの後に承認ゲート 2 と本番の配布のステージを書き出す。`waiting.md` の手順 8〜9 を、承認の後のキューと待ちだけにする。本番の版はスプリントの版（`--version`）から接尾辞を外したもの、出す PR はスプリントの PR（検査のプランの報告）から読む。
>
> 今は fast / auto だけが本番のプランを書き、normal は「7 配布」で終わる。conductor が `supervise.py new release --channel prod` で `8-release-prod.json` を手で作る（m1693・m1385・m744・m653・m1683。m1385 では作る前にキューを打って空振りした）。
>
> ## 今の作り
>
> - `plugins/ndf/scripts/supervise_lib/sprint.py:325-330`: normal の `sprint_plans` は `has_release_template(a)` のとき「配布」（`plan_sprint_release`、開発版）だけを足して終わる
> - `plugins/ndf/scripts/supervise_lib/sprint.py:88-93`（fast）・`122-128`（auto）: 「開発版」の後に「本番」（`plan_mvv_release(..., prod_version(a.version), "prod", ...)`）を置く。auto は出す PR を `{queue_pr:check}` で渡す
> - `plugins/ndf/scripts/supervise_lib/sprint.py:45-46`: `prod_version` が版の接尾辞を外す
> - `plugins/ndf/skills/development-workflow/references/waiting.md:167-169`: 手順 8（承認ゲート 2）・9（conductor が `sv new release --version … --prs <スプリントの PR 番号> --channel prod … --out $O/8-release-prod.json` を打ってからキュー）
>
> ## 受け入れ条件
>
> - [ ] 1. normal（release の雛形の経路）の `new sprint` の `sprint.json` が、「配布」の後に「関門 2」（`gate`）と「本番」のステージを持ち、本番のプランは `--channel prod`・版は `--version` の接尾辞を外したものになる
> - [ ] 2. 本番のプランの出す PR は、検査のステージのプランの報告から読む（`{queue_pr:check}` と同じ置き場。関門の後に本番のプランだけを単独で流しても埋まる）。一時ディレクトリで検査の報告を置き、本番のプランを単独でキューに入れて確かめる
> - [ ] 3. release の雛形の無い経路（`route_waves`）の出力は変わらない
> - [ ] 4. `waiting.md` の手順 9 が「`sprint.json` の本番のステージの `command` に `--done` を足して打つ」と `wait` だけになる。書いたコマンドを打って確かめる
> - [ ] 5. 既存のテストが通る
>
> ## 由来
>
> #1426 の「同じ形の箇所」の表の 4 行目（受け入れ条件 4 のうち表 4）。
>
> ## 依存
>
> - 先に: #1915（normal の配布のプランの PR の置き場を `{queue_pr:check}` に揃える。本番も同じ置き場を使う）。済み（PR #1930）。`plugins/ndf/scripts/supervise_lib/sprint_waves.py:315` の配布のプランが `{queue_pr:check}` を使う
> - 後に: #1920（本番のプランの先頭に承認の記録を置く）
>
> ## 見積
>
> Size 3。ステージの追加は fast / auto の形を写すだけだが、関門 2 のステージ（normal は利用者が承認する。`normal_gate_2` の note の扱い）・`--state` の有無で助言の MVV 判定を挟む分岐・`waiting.md` と `relay.md` の手順の書き直しがある。

（書き手: 起票者、日付: 2026-10-11。#1426 の表から AI が起票した本文で、利用者の指示の原文は含まない。下の「今の作り」は 2026-10-24 の版で読み直したもの）

> 人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-10-24）

## 起票者の推定

題・本文はすべて起票者が #1426 から推して書いたもので、利用者の指示の原文は無い。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | normal でも release の雛形の経路では、`new sprint` が本番のプランまで書き出す | #1426 の「同じ形の箇所」の表 4 行目、conductor の手当て（m1693・m1385・m744・m653・m1683） | 受け入れ条件 1 |
| S2 | 本番の版は `--version` から接尾辞を外したもの | fast / auto の `prod_version` | 受け入れ条件 1 |
| S3 | 本番の出す PR は検査の報告から読む（`{queue_pr:check}`） | #1915（PR #1930）の開発版の置き場 | 受け入れ条件 2 |
| S4 | 本番を単独でキューに入れて確かめる | 起票時の受け入れ条件 2 | 前提 3 へ置き換えた（本物の配布が走るため、置き換えの処理を単独で通して確かめる） |
| S5 | release の雛形の無い経路の出力は変わらない | 起票時の受け入れ条件 3 | 受け入れ条件 5 |
| S6 | `waiting.md` の手順 9 をキューと待ちだけにし、書いたコマンドを打って確かめる | 起票時の受け入れ条件 4 | 受け入れ条件 6・前提 4 |
| S7 | `relay.md` の手順も書き直す | 起票時の見積 | 受け入れ条件 7（`relay.md` と `agent-layers.md` の、本番のプランを目録の外で作る記述） |
| S8 | 本番のプランの先頭に承認の記録を置くのは #1920 が扱う | 起票時の依存 | 含まない |

## 目的

- `pace: normal` のスプリントで、承認ゲート 2 の後に conductor が組み立てるコマンドを無くし、`sprint.json` のステージの `command` に `--done` を足して打つだけで本番の配布が流れるようにする。conductor が本番のプランを手で作る手当て（5 スプリントで記録あり。1 回はプランを作る前にキューを打って空振りした）を、スクリプトの側へ移す

## 今の作り

- `plugins/ndf/scripts/supervise_lib/sprint.py` の `sprint_plans`（normal）: `has_release_template(a)` のとき「配布」（`plan_sprint_release`、開発版、計画名 `release`、`then_of: 検査`）だけを足して終わる。`--state` があれば `normal_gate_2` の文を「配布」の `note` に置く
- 同じファイルの `fast_sprint_plans`・`auto_sprint_plans`: 「開発版」の後に「本番」（`plan_mvv_release(..., prod_version(a.version), "prod", ...)`、計画名 `release-prod`）を置く。auto は出す PR を `{queue_pr:check}` で渡す
- `plugins/ndf/scripts/supervise_lib/sprint_routes.py` の `record_pr_ref`: 雛形の経路では配布の記録の PR を `{queue_pr:release-prod}` と読む（本番の計画名は `release-prod` を前提にしている）
- `plugins/ndf/scripts/supervise_lib/queue.py` の `fill_queue_pr`: 前のステージに名前の一致する計画が無ければ、同じディレクトリの計画の報告を読む（承認ゲートの後に単独で流すとき）
- `plugins/ndf/skills/development-workflow/references/waiting.md` の手順 8・9: 承認ゲート 2 の後、conductor が `sv new release --version … --prs <スプリントの PR 番号> --channel prod … --out $O/8-release-prod.json` を打ってからキューに入れる
- `plugins/ndf/skills/development-workflow/references/agent-layers.md` の表の「仕上げ」の行: 本番のリリースプランを「目録（`<out>/sprint.json`）のステージの外」と書く
- `plugins/ndf/skills/development-workflow/references/relay.md` の「カットポイントの引継ぎ文書と ndf-next はスクリプトで作る」の例: 本番のプランを `$O/plan-release-prod.json` と書く

## 前提

- 前提 1: 本番のプランは承認ゲート 2 の承認の後に conductor が単独で流す。開発版の配布の queue の `--then` には連結しない（normal の承認ゲート 2 は利用者が答える。連結すると承認を待たずに本番が流れる）
- 前提 2: 本番のプランは MVV 判定のステップを持たない（`pace: normal` の承認ゲート 2 は利用者の承認であり、助言の MVV 判定は開発版の配布の承認資料に載る。#1400）。承認したコミットは、開発版の配布が書いた承認資料（`issues/approval-<プラグイン>-v<版>.md`）から今の `release --channel prod --approval` と同じ形で読む
- 前提 3: 受け入れ条件 2 の確かめは、本物の配布を走らせずに行う。queue がステージを流す前に `{queue_pr:<名>}` を埋める処理を、一時ディレクトリに置いた本番のプランと検査の報告に単独で当て、埋まった PR 番号を見る（本番のプランを本当にキューへ入れると本番への配布が走る。共通原則 C4・C5 に当たる操作を検査で起こさない）
- 前提 4: 受け入れ条件 6 の「書いたコマンドを打って確かめる」は、`new sprint` を一時ディレクトリへ打ち、書き出した `sprint.json` の本番のステージの `command` が手順 9 の形と一致すること、その `command` のプランのファイルが存在することを見る。本番のキューは打たない（前提 3 と同じ理由）
- 前提 5: 検査の報告が無い・PR が無いときに `{queue_pr:check}` が `0` に埋まった後の振る舞いは、開発版の配布（#1915）と同じにする。この課題で新しい扱いを足さない
- 前提 6: `--version` が接尾辞を持たないときも `prod_version` をそのまま使う（fast / auto と同じ。開発版と本番が同じ版になる扱いはこの課題で変えない）

## 対象範囲

含む:
- `pace: normal` で release の雛形の経路があるときの `new sprint` の出力（承認ゲート 2 のステージと本番のステージの追加）
- `--state` の有無による承認ゲート 2 の文（助言の MVV 判定と、利用者の答えを状態へ書くコマンド）の置き場
- `waiting.md` の手順 8・9、`agent-layers.md` の「仕上げ」の行、`relay.md` の例の、本番のプランの作り方の記述

含まない:
- `pace: fast` / `pace: auto` のステージ（既に本番を持つ）
- release の雛形の無い経路（`route_waves`）・手動反映の本番系の形（`manual_production_waves`）
- 本番のプランの先頭に承認の記録を置くこと（#1920）
- `supervise.py new release --channel prod` を単独で打つ経路の削除（スプリントの外でリリースする場面で使う）
- 承認ゲートの数と置き場所の変更（`development-workflow` の「人手の承認を求める承認ゲート」が持つ）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `new sprint` が配布・承認ゲート 2・本番のステージを `sprint.json` へ書き出した | conductor が `pace: normal` で `new sprint` を打った（release の雛形の経路あり） | 設定の不足なら今と同じく止まり、ステージを書かない | — |
| E2 | 検査のプランがスプリントの PR をマージし、報告に PR を残した | 検査のステージの `command` | 止まった / 承認ゲートを返したなら配布へ進まない（今と同じ） | E1 |
| E3 | 開発版の配布が承認資料を書き、承認ゲートを返した | 検査の queue の `--then` | 止まったなら本番の承認を求めない | E2 |
| E4 | 利用者が本番への配布を承認した（`--state` があれば `sprint-state.py gate … "関門 2" --by user` を打った） | conductor が承認資料を示して問うた | 差し戻されたら本番のステージを打たない | E3 |
| E5 | 本番のプランの `{queue_pr:check}` が検査の報告の PR 番号に埋まった | conductor が本番のステージの `command` に `--done` を足して打った | 報告が無ければ `0`（前提 5） | E2。E4 の後に打つことはスクリプトが強制しない（#1920 が記録を置く） |
| E6 | 本番の配布・リリース後テスト・後片付けが終わった | E5 に続くプランのステップ | プランが止まり、conductor が報告を読む（今と同じ） | E5 |

## 受け入れ条件

- [ ] 1. `pace: normal` で release の雛形の経路があるとき、`new sprint` の `sprint.json` の `ステージ` が「配布」の後に、`gate` を持つ承認ゲート 2 のステージ（名前 `関門 2`）と、計画名 `release-prod` のプランを持つ本番のステージ（名前 `本番`）をこの順に持つ。本番のプランは `--channel prod` の形で、版は `--version` から接尾辞を外したもの（`10.18.0-dev.1` → `10.18.0`）になる
- [ ] 2. 本番のステージは `then_of` を持たず、自分の `command` を持つ（どの queue の `--then` にも連結されない）
- [ ] 3. 本番のプランの出す PR は `{queue_pr:check}` で書かれ、一時ディレクトリに検査の計画の報告（PR 番号つき）と本番のプランだけを置いて、前のステージの結果を渡さずに置き換えの処理を当てると、その PR 番号に埋まる
- [ ] 4. `--state` を渡したとき、承認ゲート 2 のステージの文に、利用者の答えを状態へ書くコマンド（`sprint-state.py gate <状態> "関門 2" … --by user`）と助言の MVV 判定の扱いが載る。`--state` を渡さないとき、その文は承認資料を示して本番の承認を取ることだけを書き、`sprint-state.py` のコマンドを含まない
- [ ] 5. release の雛形の経路が無いとき（`route_waves` の経路）、および `pace: fast` / `pace: auto` のとき、`new sprint` が書き出す `sprint.json` とプランのファイルは変更の前と同じになる
- [ ] 6. `waiting.md` の手順 9 が、`sprint.json` の本番のステージの `command` に `--done` を足して打つことと `wait` だけを書き、`sv new release` を含まない
- [ ] 7. `development-workflow` の参照文書（`waiting.md`・`agent-layers.md`・`relay.md`）に、`pace: normal` のスプリントで本番のプランを目録の外で作る、または `sv new release --channel prod` で作るとする記述が残らない
- [ ] 8. 既存のテストが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 承認ゲート 2 の後に conductor が組み立てるコマンドが 0 本になる（打つのは `sprint.json` の `command` と `wait` だけ） |
| 移行性 | 既に書き出した `sprint.json` を持つ進行中のスプリントは、今までどおり `new release --channel prod` で本番のプランを作れる（単独の `new release` は残す） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`new sprint`（normal、雛形の経路）の出力にステージが 2 つ増える。既存のステージの名前・番号・`command` は変わらない（追加のみで互換あり） |
| データ | 無し |
| 既存の振る舞い | normal の雛形の経路で、承認ゲート 2 の文の置き場が変わりうる（設計で決める）。fast / auto・雛形の無い経路は変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `python3 plugins/ndf/scripts/supervise.py new sprint --name t --worktree . --issue <番号> --version 10.18.0-dev.1 --out <一時ディレクトリ>` を打ち、`sprint.json` を読む |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`（`.ndf/project.json` の `test`） |
| 静的解析・型検査 | `bash scripts/check-lint.sh`、`python3 scripts/check-script-structure.py` |
| 手動確認 | 次の `pace: normal` のスプリントの承認ゲート 2 の後に、conductor が `sprint.json` の本番のステージの `command` だけで本番の配布を流せたか（リリース後テストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 組み立ては `plugins/ndf/scripts/supervise_lib/` に置く。`AGENTS.md` の「安定版と実験版」の安定版（既定で働く）に当たり、`experimental/` に置かない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。同じ役割の関数を pace ごとに分けない（MVV の Value 6）。fast / auto の本番のステージの組み立てと共通にできる部分は共通にする |
| テスト戦略 | `new sprint` の出力（ステージの並び・`then_of`・プランの版とチャネル）と、`{queue_pr:check}` の単独の置き換えを単体テストで見る。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、リンタの適用、手順に書くコマンドを書く前に打って確かめる |
| 確認してから行う | `new sprint` の出力の形の変更（承認ゲート 1・2 で人が見る） |
| 行わない | 本番のプランを開発版の queue へ連結すること、承認ゲートの数を変えること、本物の本番の配布を検査で走らせること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `--state` のときの助言の MVV 判定の文（今の `normal_gate_2`）を「配布」の `note` に残すか、承認ゲート 2 のステージの `gate` へ移すか | 設計（`design`）。承認ゲート 1 で利用者が確かめる | 設計 PR |
| 本番のプランを `plan_sprint_release` にチャネルと版を渡して作るか、`plan_mvv_release` を MVV なしで使うか（Value 6 の共通化の仕方） | 設計（`design`） | 設計 PR |
