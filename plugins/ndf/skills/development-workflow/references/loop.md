# 開発ループ（`--loop`）

スプリントを 1 つ終えるたびに、棚卸しの順位から次のスプリントを機械で選び、`pace: auto` で周回を続ける進め方の手順。
決定の理由・契約・テスト観点は、ai-plugins の確定仕様 `docs/specifications/ndf-workflow-development-loop.md` にある。

## 具体例: ループ `l20261025` の 1 周

利用者が `/goal /ndf:development-workflow --loop l20261025` で始めた。マイルストーン「01 近い」の順位は上から #11・#12・#13、
切り出しの境界は #12 だった。

1. `sprint-state.py loop start l20261025` が使ってよい条件を確かめ、`.ndf/loop/l20261025/loop.json` を作る
2. `loop select l20261025` が `upkeep.py rank` を打ち、#11 と #12 を選ぶ。#13 は「順位（切り出しの境界の外）」で外す
3. conductor がモードを `standard`、版を `10.20.0-dev.1` と決め、`loop sprint l20261025 --mode standard --dev 10.20.0-dev.1` を打つ
4. 返った `pipeline` をそのまま流す。設計 → 承認ゲート 1（MVV 判定）→ 実装 → 検査 → 開発版 → 本番（承認ゲート 2）→
   まとめ（確定仕様化・振り返り・課題を閉じる・棚卸し）→ `loop lap`
5. #12 の設計が承認ゲート 1 で「判定できない」だった。`loop lap`（または `loop gate`）が #12 を人の判断待ちへ移し、
   課題の本文に `## 人の判断待ち（開発ループ l20261025）` を書き、#11 だけで続きの `pipeline` を返す
6. `loop lap` が周回を締め、止まる条件に当たらなければ `ndf-next` を返す。ラッパーが次のセッションを起動し、手順 1 から続ける

## 起動の引数

```text
/goal /ndf:development-workflow --loop [<ループ名>] [--milestones <M>,<M>...] [--budget-laps N] [--budget-usd X] [--budget-hours H]
```

| 引数 | 意味 | 無いとき |
| --- | --- | --- |
| `<ループ名>` | 記録の名前（英数字・`-`・`_`）。同じ名前の記録があれば再開する | `l<UTC の YYYYMMDD>` |
| `--milestones` | 選定の対象を絞る | 開いているマイルストーンすべて |
| `--budget-*` | 予算を上書きする（記録の `budget_history` に残る） | `.ndf/pace.json` の `loop.budget` |

`ndf-next` と再開のコマンドは `/goal /ndf:development-workflow --loop <ループ名> -- 終わり: [ndf-loop] stopped <ループ名> の行を出したとき`
の形で、`sprint-state.py loop` の結果の `resume` をそのまま使う。`/goal` で始めていなければ `loop start --no-goal` を打つ。
`--` から後ろは `/goal` の終わりの条件で、引数として読まない。

## 周回ごとに打つ順

`$SS` は `python3 <NDF のスクリプト>/sprint-state.py` を指す。どの副命令も結果は 1 行の JSON で、終了コードは
0 = 進める / 1 = 断った・順の誤り / 3 = 止まる条件に当たった（失敗と数えない）。

| 順 | 打つもの | conductor の判断 |
| --- | --- | --- |
| 1 | `$SS loop start <名> [--milestones ...] [--budget-* ...]` | 1 なら断った理由と 1 スプリントの起動の形（`metrics.next`）を示して終える。3 なら下の「止まったとき」。`in_lap` なら引継ぎ文書 `loop-<名>` の「次にやること」から続ける |
| 2 | `$SS loop select <名>` | 選んだ課題（`metrics.selected`）のモードを判定し（手順 2）、版を決める（`release` の規則）。`auto` が許さないモード・MVV に反する疑いの課題は `$SS loop exclude <名> <課題> --reason <理由>` で外す（選定をやり直す） |
| 3 | `$SS loop sprint <名> --mode <モード> --dev <開発版> [--prod <本番の版>] [-- <new sprint に足す引数>]` | 判断は無い。返った `metrics.pipeline` をそのまま流す |
| 4 | `metrics.pipeline`（`supervise.py queue ... --done <D>` → `wait <D>` → `loop lap <名> --done <D>`） | `wait` が attention を返したら、今の `supervise.py` の手順で扱う |
| 5 | `loop lap` の結果 | 0 で `metrics.ndf_next` があれば、それを出してセッションを切る。0 で `metrics.pipeline` があれば（承認ゲート 1 で課題を外した続き）それを流す。3 なら下の「止まったとき」 |

**品質を上げる工程は今の上限で打ち切る。** クロスレビュー・クロスリファクタリング・検査が上限（ラウンド・時間の予算・
`timeout`）に達して残した指摘は、その周回で続けずに `out-of-scope` で Issue に起こし（`.ndf/backlog.json` の
`quality.label` のラベルを付ける）、番号を `$SS loop lap <名> --deferred-issues <番号...>` で周回の記録へ書く。
起こした Issue は次の周回の棚卸しで順位が付いてから選ばれる。人を害する・秘密・利用者のデータ・戻せない操作に当たる
指摘だけは、上限に達してもその周回で直すか、レッドラインとして人へ戻す。

**周回を先へ進められないと判断したら `$SS loop lap <名> --failed` を打つ。** 同じ失敗（課題の組・計画の種類・止まったところ）が
`loop.same_failure` 回続くとループが止まる。

## 承認ゲート

周回のスプリントは `pace: auto` で組まれ、承認ゲート 1・2 は `mvv-gate.py check` の判定だけで通る。ループは承認ゲートの記録を
自分で書かない。

| 止まった所 | 扱い |
| --- | --- |
| 承認ゲート 1（設計のプラン）で、判定が「従う」以外・レッドライン・判定を読めない | その課題を人の判断待ちへ移し、残りの課題で続ける。残りが無ければ周回を「人へ戻した」で締めて次の周回へ進む。利用者へ問わない |
| 承認ゲート 1 で、プロジェクト MVV が開始時と違う | `mvv_changed` で止まる |
| 承認ゲート 2（本番のプラン） | `gate2_returned` で止まる。今の承認ゲートの出し方（`AskUserQuestion`。`metadata.source` は `ndf-gate:2`）で利用者の承認を求める |

**人の判断待ちの課題には利用者が答える。** `$SS loop answer <名> <課題> --approved [--pr <設計 PR>]` は承認の記録を書き、
設計 PR がマージされた後の周回で選定の対象に戻す（その課題は設計のステージを置かない）。`--rejected` は差し戻しの記録を書き、
以後選ばない。答えるまで、その課題は選ばない。

**スプリント MVV を持つマイルストーンは、利用者が承認したときだけ選ぶ。** 承認は
`$SS loop approve-mvv <名> --milestone <題>` で、説明の Mission / Vision / Value の sha256 を記録する。説明が変われば承認し直す。

## 止まる条件

止まる条件は周回の境（選定の前と周回を締めた後）でだけ判定し、最初に当たったものを止まった理由にする。

| 順 | 条件（`stop.condition`） | 判定 | 再開で解けるか |
| --- | --- | --- | --- |
| 1 | `user_stop` | `.ndf/loop/<名>/stop` がある（`$SS loop stop <名>` が置く） | 再開でファイルを消して解ける |
| 2 | `mvv_changed` | プロジェクト MVV が開始時と違う・承認と一致しない | 解けない。新しい名前でループを始め直す |
| 3 | `same_failure` | 直近の失敗の周回が同じ鍵で `loop.same_failure` 回続く（完了の周回で数え直す） | 再開の起動で解け、数え直す |
| 4 | `budget_laps` / `budget_usd` / `budget_hours` | 終わった周回の数・費用の和・動いていた時間が予算以上 | `--budget-*` で上書きした予算で判定し直す |
| 5 | `no_issue` / `outside_mvv` | 選定の候補が 0 件 / 候補がすべて外れた | 再開で選定をやり直す |

周回の途中で止まるのは `gate2_returned`・`mvv_changed`・`refused`（`new sprint` などが断った）だけで、周回は進行中のまま残る。
`gate2_returned` は利用者が承認すれば `loop start` の `metrics.next` の `pipeline`（承認ゲート 2 で止まった計画を経路に合う
ステップから続け（雛形の本番は `--from bump`、昇格は `--from promote-approved`、マージで止まった計画は `--from merge-approved`）→
同じ queue の残りのステージ → まとめ → `loop lap`）で続け、差し戻せばその周回を「人へ戻した」で締める。`refused` は同じ周回で `loop sprint` をやり直す。

止まっていた時間は経過時間に入れない。周回の数・費用・経過時間は再開をまたいで引き継ぐ。

## 止まったとき

**`ndf-next` のブロックを出さない。** 出すとラッパーが次のセッションを起動し、同じ理由で止まり直す。
本文に止まった条件・判定に使った値・再開のコマンド（結果の `items`）を書き、**最後の行に `metrics.stop_line`
（`[ndf-loop] stopped <名> <condition>`）をそのまま出して**応答を終える。再開のコマンドは引継ぎ文書 `loop-<名>` の
「次に実行するコマンド」にもスクリプトが書く。

| 止まった理由 | 利用者へ問うか |
| --- | --- |
| `gate2_returned` | 問う（承認ゲート 2） |
| それ以外 | 問わない。本文で理由と再開のコマンドを示して終える |

ラッパーが 1 日の起動回数の上限で止めたときは、`$SS loop status <名>` がそのことと再開のコマンドを出す。

## 記録

| 置き場 | 中身 |
| --- | --- |
| `.ndf/loop/<名>/loop.json` | 正本。周回（選定と理由・承認ゲートの判定の写し・版・振り返りの置き場・棚卸しの持ち越しと順位の変化・費用と秒・後へ回した Issue・結果）・人の判断待ち・除外・止まった理由 |
| `.ndf/loop/<名>/laps.md` | 人が読む描画。書き換えるたびに作り直す |
| `.ndf/handoff/loop-<名>.md` | 引継ぎ文書。現在地と次に実行するコマンドだけ |

どれもメインディレクトリに置き、コミットしない。周回の一覧は `$SS loop list <名>`、今の状態は `$SS loop status <名>` で出す。
前の周回の棚卸しの持ち越し（`metrics.deferred`）は、次の周回のまとめの棚卸しへ `new close --carry` で渡る。
