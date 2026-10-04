# cross-refactoring: 着手前のテストが長いと提案者が余裕だけの上限で打ち切られ、CI に任せる戦略で範囲が広いと init が打ち切られていた → 手順の枠を着手前のテストの後から数え、範囲テストの所要が分かれば着手前の上限をそれより下げない

## 目的

- **既定の想定最大時間（`--budget-minutes 30`）のまま、着手前のテストが長いリポジトリでも提案者が提案の枠を丸ごと使える。**
  提案とリファクタリング計画の枠は着手前のテストの終わりから数え、全体の締め切り（`started_at + B`）は動かさない。
  伸びた分はリファクタリング計画で採る件数が減ることで吸収する
- **枠が想定最大時間に収まらないときは、提案者を起動せずに止まる。** 要る想定最大時間の下限を出し、予算を広げて
  `init` を打ち直すと、着手前のテストを走らせ直さずに続く
- **CI に任せる戦略の着手前のテストの上限は、範囲テストの所要が分かればそれを下回らない。** 所要は同じ範囲の履歴の実測か、
  範囲が全体テストの suite を覆うときの w から取る。範囲の広いスプリントでも、予算を広げずに init を通る

共通の原則は「**着手前のテストの上限と、その後の手順の枠を、同じ算術の層（`test_strategy.limits` と `timeline`）で入力だけから決める**」である。

例 1（#1385）: 戦略 `local-full`、`--budget-minutes 30`、着手前の全体テストの実測 357 秒（#1370 の検査・スプリント m464 と同じ形）。

| 値（開始からの秒） | 変更前 | 今 |
| --- | ---: | ---: |
| 提案の枠の終わり（`propose_end_at`） | 360 | 717 |
| テストの直後に打つ `start-phase propose` の監視の上限 | 90（余裕だけ） | 450（`0.20·B` + 余裕） |
| テストの直後に打つ `measure` で使える時間 | 0 | 90 |
| リファクタリング計画の枠の終わり（`plan_end_at`） | 540 | 897 |
| 全体の終わり（`final_end_at`） | 1800 | 1800 |

例 2（U1）: 同じ条件で着手前のテストが 1300 秒かかると、計画の枠の終わり（開始 + 1840 秒）が全体の終わりを越える。
`init` は状態を保存し、止めた印を残して終了コード 4 で止まり、「`--budget-minutes` を 39 以上にして 5 分以内に init を
打ち直す」と出す。開始 + 1360 秒に `--budget-minutes 39` で打ち直すと、着手前のテストを走らせずに枠を打ち直した時刻から数え、
提案の枠の終わりは開始 + 1828 秒になる。開始 + 1700 秒まで遅れると下限 48 分を出して再び止まる。

例 3（#1555）: 戦略 `local-scoped-ci-whole`、`--budget-minutes 30`、範囲テストが実測 320 秒かかる範囲。

| 実行 | 範囲テストの所要 s（出所） | 着手前の上限 | 結果と履歴に足す行 |
| --- | --- | ---: | --- |
| 1 回目（履歴に行が無く、範囲が suite の `paths` を覆わない） | 無し | 180 | 180 秒で打ち切り。`seconds: 180.0`・`timed_out: true` の行を足して終了コード 4 |
| 2 回目 | 180（`history`） | 540 | 320 秒で通る。`seconds: 320.0` の行を足す |
| 3 回目 | 320（`history`。直近 10 行の最大） | 960 | 通る |
| 範囲が suite の `paths` を覆い、宣言の w が 424 秒（履歴に行が無い） | 424（`whole`） | 1272 | 通る |

**式・係数と止まり方は Skill が正である。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| 着手前のテストの上限・提案の枠・リファクタリング計画の枠の式と係数、s と o の定義、指標の測定に使える時間 | [`docs/02-plan-and-implement.md`](../../plugins/ndf/skills/cross-refactoring/docs/02-plan-and-implement.md) の「締め切り」 |
| 枠が想定最大時間に収まらないときの止まり方・下限の式・打ち直しで枠を数え直すこと、着手前のテストの打ち切りを履歴に残すこと | 同上 |
| 履歴の 2 種類の行（`run` と `init_test`）と、実行の行の `whole_test.init` の扱い | [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md) の「配分テーブル」 |
| 終了コード 4 の理由と打ち直しの読み方 | [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md) の「実行」の終了コードの表 |
| 利用者向けの要約 | [`CLAUDE.md`](../../CLAUDE.md) の「cross-refactoring」 |

この文書が扱うのは、Skill に書かない決定の理由、常に成り立つ条件、部品（関数・履歴の行・状態ファイルの欄）の契約、テスト観点である。

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「着手前のテスト」「手順の枠」「範囲テストの所要」「想定最大時間」「範囲テスト」「全体テスト」「テストの戦略」
「配分テーブル」「リファクタリング計画」である。式の記号（B・w・x・s・o）は正本の「締め切り」に従う。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| 着手前のテスト 1 回の上限（`init_test_timeout`）に、CI に任せる戦略で範囲テストの所要を入れること（#1555） | 係数（`0.20·B`・`0.10·B`・`3·w` など）の値そのもの |
| 提案の枠の終わり（`propose_end_at`）とリファクタリング計画の枠の終わり（`plan_end_at`）の起点を、着手前のテストの終わりへずらすこと（#1385） | `--budget-minutes` の既定値（#1522） |
| 指標の測定に使える時間が、起点の変更に従って 0 にならないこと | 危険フラグ・最終ゲートの全体テスト、範囲テスト 1 回（`test_timeout`）、CI の待ちの上限の式 |
| 枠が想定最大時間に収まらないとき、提案者を起動せずに止め、打ち直しで続けること | 着手前のテストを速くすること（並列数・対象の絞り込み） |
| 着手前のテストの所要を範囲・戦略とともに履歴へ残し、上限の入力を `limits.basis` と計画のコメントに出すこと | supervise（予算を持たない呼び出し）の上限 |

## 背景

`started_at` は着手前のテストの前に取る（#968。テストの所要も想定最大時間に入れる）。各手順の終わりの時刻は
`started_at` に配分を足して決めていたため、着手前のテストの所要がそのまま提案の枠から引かれた。#1370 の検査
（PR #1383、2026-09-27）では着手前の全体テスト 356.7 秒の後に提案者が `hard timeout 90s` で打ち切られて提案 0 件になり、
スプリント m464（PR 1701、2026-10-03）でも既定の 30 分のまま提案の上限が 93 秒になって 3 者とも打ち切られた。
回避は検査のプランごとに `--budget-minutes 60` へ広げることだった。

CI に任せる戦略では、着手前に走らせるのは範囲テストなのに上限は `0.10·B` だけで決まっていた。スプリント m1142c
（PR https://github.com/devbasex/ai-plugins/pull/1554 、2026-09-30）では範囲 `plugins/ndf` の着手前のテストが 180 秒で打ち切られ、
init が終了コード 4 で止まった。同じ範囲の実測は 320 秒（6632 passed）で、回避は `--budget-minutes 75` だった。

2 つは同じ関数（`test_strategy.limits`・`timeline.compute`・`setup._verify_init`）と同じ表を触るため、#1555 を #1385 に取り込んで 1 本で直した。

## 決定と理由

- **手順の枠の起点を `started_at + x`（着手前のテストの実測）にする。** `started_at` と `final_end_at` は #968 のまま変えない。
  起点にテストを終えた時刻（`baseline_test.checked_at`）を使わないのは、認証確認などテスト以外の準備まで枠から外れて
  テストが 0 秒のときに今と同じ値にならず、起点が状態に残った秒の値から決まらなくなるためである
- **伸びた分は採る件数で吸収し、直しと取り消しの締め切りは `started_at` から数えたままにする。** 提案と計画の枠を削ると、
  #1385 と同じ打ち切りに戻る
- **枠が想定最大時間に収まらなければ、`init` で状態を保存してから止める。** 枠を残りの時間へ縮めて起動すると、縮めた枠
  （90 秒）で提案 0 件になった実測がある。予算を自動で広げないのは、想定最大時間が利用者の与えた上限だからである。
  保存しておけば、予算を広げた打ち直しは着手前のテストを走らせ直さずに続く
- **止めた後の打ち直しでだけ、枠の起点を打ち直した時刻へずらす。** 起点を `started_at + x` のまま残すと、止まってから人が
  打ち直すまでの数分が提案の枠から引かれ、下限どおりの予算で提案者が余裕だけで起動される。提案や計画の途中で落ちた再開まで
  ずらすと、提案が揃っているのに枠が想定最大時間を越えて止まる。計画を取り込んだ後は採った件数が期限に合わせてあるため、
  保存した期限で続ける
- **要る想定最大時間の下限には、人が打ち直すまでの 300 秒（`RESUME_GRACE_SECONDS`）を足す。** 足さないと、出した下限で
  打ち直したときに経過の分だけ o が伸びて再び収まらない
- **範囲テストの所要は、着手前のテストの行を履歴に別の種類（`kind: init_test`）として足して実測から引く。** 実行の行は
  最終ゲートを通った実行だけが書くため、中断した実行と init で打ち切られた実行が残らず、範囲が suite の `paths` を覆わない
  リポジトリではいつまでも所要が分からない。打ち切りの行は上限の秒を下限の実測として残し、次の実行は `3·上限` で走らせる
- **「同じ範囲」は戦略とテストの置き場所の集合の一致にし、直近 10 行の最大を採る。** 走らせるのは `--scope` そのものでなく
  そこから求めた置き場所の範囲テストで、`--scope` の並びではテストを含まないパスの増減や並びの違いで同じテストを別の範囲とみなす。
  上限には見込みの上側だけが要り、打ち切りの行（下限）と実測の行が混ざっても大きい側が正しい。10 行は配分テーブルの `WINDOW` と揃える
- **履歴の実測を宣言の w より先に採る。** w は CI の step の合計（`ci-steps`）などで、手元の並列数で走らせる範囲テストとは
  測り方が違う。宣言の所要の出所の順（手元の実測が先）とも揃う
- **実行の行の `whole_test.init` は全体テストを走らせたときだけ書く。** `measure_ci.ndf_record` はこの値を全体テストの所要 w
  （`ndf-record`）として読むため、範囲テストの秒が入ると w を取り違える
- **範囲テストの所要は cross-refactoring が求めて `test_strategy.limits` へ数値で渡す。** `test_strategy` は supervise と共有の層で、
  cross-refactoring の履歴の置き場所を知らない。置き場所が suite を覆うかの判定（`covers_whole`）は suite の `paths` の読み方と
  同じ層に置くため `test_strategy` に持たせる

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | `final_end_at = started_at + B`。着手前のテストの所要に左右されない | 単体テストで落とす |
| I2 | `propose_end_at = started_at + o + 0.20·B`、`plan_end_at = propose_end_at + 0.10·B`。o は `resumed_at` が無ければ x（`baseline_test.seconds`。無ければ 0）、あれば `max(x, resumed_at − started_at)` | 単体テストで落とす |
| I3 | `resumed_at` を書くのは、止めた印（`window_stopped_at`）を持つ状態をリファクタリング計画の前に打ち直したときだけである。印の無い再開と計画を取り込んだ後の再開は `resumed_at` を変えない | `init` のテストで落とす |
| I4 | `fix_end_at`・`stop_revert_end_at`・項目の締め切りは `started_at` から数え、x に依らない | 単体テストで落とす |
| I5 | `plan_end_at > final_end_at` の状態で提案者を起動しない。`init`（新規と、計画の前の再開）は止めた印を残して状態を保存し、要る想定最大時間の下限を出して終了コード 4 で止まる。収まれば印を消す | `init` のテストで落とす |
| I6 | 計画を取り込んだ後の再開は `limits` を組み直さず、`window_problem` も見ない | `init` のテストで落とす |
| I7 | CI に任せる戦略の `init_test_timeout` は、範囲テストの所要 s が分かれば `max(0.10·B, 3·s)`、分からなければ `0.10·B`。ほかの戦略と予算なしの値は s を渡しても変わらない | 単体テストで落とす |
| I8 | s は、同じ戦略・同じ置き場所の集合の `init_test` 行の直近 10 行の秒の最大 → 置き場所が全体テストの suite の `paths` を覆うときの w → 無し、の順に採る。CI に任せる戦略でなければ履歴を読まない | 単体テストで落とす |
| I9 | 新規の `init` では、着手前のテストに使った上限と、状態から組み直した `init_test_timeout` が一致する（s は `baseline_test` に残る）。再開で予算を置き換えたときは置き換えた後の予算から組み直す | 単体テストで落とす |
| I10 | 着手前のテストは成否と打ち切りを問わず `init_test` 行を 1 行足してから次へ進むか止まる | `init` のテストで落とす |
| I11 | `init_test` 行は配分テーブルにも `ndf_record` にも数えない。`schema` が 2 未満の行と `kind` を持たない行は実行の行として読み、範囲の一致に使わない。読んでも落ちない | 単体テストで落とす |

### 時間の上限の表（`plugins/ndf/scripts/lib/test_strategy.py`）

| 関数 | 契約 |
| --- | --- |
| `limits(strategy, budget_minutes, ..., scope_seconds=None, scope_source=None)` | キーワード引数 2 つを足した（既定 `None` で、今の呼び出しはそのまま通る）。予算があるときだけ `basis` に `scope_seconds` / `scope_source` を書く（`scope_seconds` が `None` なら両方 `None`）。予算が無いとき（supervise）は表も `basis` の形も変えない。`test_timeout` と `whole_timeout` の代わりの値（x・w が無いとき）は s を入れない着手前の上限のまま |
| `covers_whole(strategy, locations, kind=TEST)` | `kind` の suite の `paths` の全要素を、置き場所のどれかが同じか祖先として覆えば真（純粋）。置き場所は正規化して比べる。`.` と glob の要素は置き場所 `.` だけが覆う。`kind` の suite が無いか置き場所が無ければ偽 |

### 手順の枠（`refactor_lib/timeline.py`）

| 名前 | 契約 |
| --- | --- |
| `compute(..., offset_seconds=None)` | 提案と計画の枠を `started_at + offset_seconds` から数える（負と `None` は 0）。ほかの行は `started_at` から数える（I1・I4） |
| `window_offset(state)` | o（秒）。I2 の式で、状態の `baseline_test.seconds`・`started_at`・`resumed_at` だけを読む |
| `of_state(state)` | `window_offset(state)` を `compute` へ渡す。`test_limits(state)` は `baseline_test.scope_seconds` / `scope_source` を `limits` へ渡す（I9） |
| `RESUME_GRACE_SECONDS` | 300。人が文を読んで打ち直すまでの見込み |
| `required_budget_minutes(offset_seconds)` | `ceil((o + RESUME_GRACE_SECONDS) / (60 · (1 − PROPOSE_SHARE − PLAN_SHARE)))`。o = 1300 なら 39、1700 なら 48 |
| `window_problem(limits)` | `plan_end_at > final_end_at` なら止める理由の文（`limits` から o を逆算し、下限を `required_budget_minutes` で出す）、収まれば `None`（純粋） |

### 着手前のテスト（`refactor_lib/baseline.py`・`refactor_lib/init_test.py`）

| 名前 | 契約 |
| --- | --- |
| `baseline.locations_of(strategy, scope, work)` | CI に任せる戦略（`round-only` を除く）で着手前に走らせる範囲テストの置き場所を、正規化して並べ替えた集合で返す。ほかの戦略は空。`commands_of` も同じ関数で置き場所を求める |
| `baseline.run_baseline(...)` | 上限を超えたら止めずに記録を返す（`timed_out: true`・`seconds` は上限の秒。既存失敗の読み取りは行わない）。止めるのは起動の失敗だけ。記録に `locations`（`scope` のときだけ）と `timed_out` を足した |
| `init_test.resolve_scope_seconds(prep, scope, w)` | `(s, 出所)`。I8 の順で `history` / `whole` / `(None, None)`。CI に任せる戦略でなければ履歴を読まずに `(None, None)` |
| `init_test.append_init_test(prep, budget_minutes, pr, baseline)` | `init_test` 行を 1 行追記する。書けなければ知らせて続ける |
| `init_test.abort_timed_out(strategy, budget_minutes, baseline, timeout)` | 打ち切りの文で終了コード 4 にする。`scope` の CI に任せる戦略なら、次の実行の上限の見込み（`3·上限` を s とした `limits`）を添える |
| `init_test.stop_if_window_short(state_file, state)` | `window_problem` が文を返せば `window_stopped_at` を書いて保存し、終了コード 4。`None` なら印を消し、消したときだけ保存する |

`refactor.py init` の順序（`commands/setup.py`）:

1. `_verify_init`: w と c を読み、`resolve_scope_seconds` で s を求めて `limits` から上限を出す → `run_baseline` →
   `baseline_test` に `scope_seconds` / `scope_source` を残す → `append_init_test` → 打ち切りなら `abort_timed_out`
2. `cmd_init`: `_save_initial_state` で状態を保存した後に `stop_if_window_short`。止まらなければ出力（`PHASE=` など）へ進む
3. `_resume`: 計画の前（`state.plan` が無い）なら、印があるときだけ `resumed_at` を今にし、`limits` を `of_state` で組み直して
   `stop_if_window_short`。計画の後は何もしない（I6）

### 履歴の行（`refactor_lib/allocation.py`）

`ROW_SCHEMA = 2`。`RUN_KIND = "run"`、`INIT_TEST_KIND = "init_test"`。

| 名前 | 契約 |
| --- | --- |
| `run_rows(rows)` | 実行の行だけ（`kind` の無い行を含む）。`build_table` はこれだけを集計する |
| `build_row(state)` | `kind: run` を足す。`whole_test.init` は `baseline_test.mode` が `scope` なら `null` |
| `init_test_row(baseline, strategy, budget_minutes, pr)` | 下の表の行 |
| `scope_seconds(rows, strategy, locations)` | I8 の 1 段目。`schema` 2 以上・`kind: init_test`・`mode: scope`・同じ戦略・同じ置き場所の集合で、秒が正の行の直近 `WINDOW` 行の最大。無ければ `None`。置き場所が空なら `None` |

`init_test` 行の列:

| 列 | 型 | 中身 |
| --- | --- | --- |
| `schema` | int | 2 |
| `kind` | str | `init_test` |
| `at` | str | `baseline_test.checked_at` |
| `pr` | int | 対象の Pull Request（取れなければ `null`） |
| `budget_minutes` | int | B |
| `strategy` | str | 戦略の名前 |
| `mode` | str | `whole` / `scope` / `round` |
| `locations` | list[str] | `scope` のときだけ、並べ替えて重複を除いた置き場所。ほかは `null` |
| `seconds` | float | 実測の秒。打ち切りなら上限の秒 |
| `timed_out` | bool | 上限で打ち切ったか |

`measure_ci.ndf_record`（`plugins/ndf/scripts/project_lib/measure_ci.py`）は `kind` が `run` でない行を読まない（`kind` の無い行は読む）。

### 状態ファイルに足した欄

既存の欄は消さず、意味も変えない。

| 置き場 | 欄 | 中身 | 書く者 | 読む者 |
| --- | --- | --- | --- | --- |
| `baseline_test` | `locations` | 着手前に走らせた範囲テストの置き場所（`scope` 以外は `null`） | `baseline.run_baseline` | `allocation.init_test_row` |
| `baseline_test` | `timed_out` | 上限で打ち切ったか | 同上 | `setup._verify_init`・`allocation.init_test_row` |
| `baseline_test` | `scope_seconds` / `scope_source` | 上限に使った s と出所（`history` / `whole`。分からなければ両方 `null`） | `setup._verify_init` | `timeline.test_limits` |
| `limits.basis` | `scope_seconds` / `scope_source` | 同上の写し | `test_strategy.limits` | `plan.limits_section`（計画のコメントの「入力:」の行） |
| 状態の根 | `window_stopped_at` | 枠が収まらずに止めた時刻（止めた印） | `init_test.stop_if_window_short` | `setup._resume` |
| 状態の根 | `resumed_at` | 止めた後に打ち直した時刻 | `setup._resume` | `timeline.window_offset` |

`limits` を持つ旧い状態ファイルは書き出した値を使い、持たないものは `of_state` が新しい式で組む。`baseline_test.scope_seconds` が
無ければ所要は分からないものとして組む。

## テスト観点

- `local-full`・B 30・x 357 で、提案の枠の終わりが開始 + 717 秒、計画の枠の終わりが + 897 秒、全体の終わりが + 1800 秒のまま。
  開始 + 357 秒の手順の監視の上限が 450 秒、指標の測定に使える時間が 90 秒（`test_the_windows_start_after_the_init_test`）
- x が `None` か 0 なら枠の終わりは開始 + 360 / + 540 秒（`test_a_short_init_test_keeps_the_windows`）。`seconds` の無い旧い状態も同じ
  （`test_of_state_offsets_the_windows_by_the_measured_test`）
- 計画の後の `fix_end_at`・`stop_revert_end_at`・`final_end_at`・テストの追加と実装の終わりが x に依らない
  （`test_the_deadlines_after_the_plan_do_not_move`）
- `resumed_at` があれば o = `max(x, resumed_at − started_at)`（`test_a_restart_after_the_stop_starts_the_windows_at_the_restart`）
- `window_problem` が x 1300・B 30 で下限 39、B 39 で開始 + 1700 秒の打ち直しなら 48 を出し、+ 1360 / + 1600 秒と x 1260（`0.70·B` ちょうど）は
  収まる。`RESUME_GRACE_SECONDS` は 300（`test_window_problem_stops_when_the_windows_do_not_fit`・`test_the_required_budget_adds_the_restart_grace`）
  （以上 `plugins/ndf/skills/cross-refactoring/tests/test_timeline.py`）
- x 1300・B 30 の `init` が止めた印を残して終了コード 4 で止まり、提案へ進む出力を出さない。開始 + 1360 秒に B 39 で打ち直すと
  着手前のテストを走らせずに進み、提案の枠の終わりが開始 + 1828 秒、印が消える
  （`test_init_stops_before_the_proposers_when_the_windows_do_not_fit`）。+ 1600 秒・B 39 と + 1500 秒・B 40 でも進む
  （`test_a_restart_within_the_grace_continues`）。+ 1700 秒・B 39 では下限 48 で再び止まる（`test_a_late_restart_stops_again_with_a_new_lower_bound`）
- 印の無い再開（x 200 で計画の手順中に開始 + 1300 秒）は `resumed_at` を書かず、提案の枠の終わりが開始 + 560 秒のまま
  （`test_a_restart_without_the_stop_keeps_the_windows`）。計画を取り込んだ後の再開は `limits` と予算を変えない
  （`test_a_restart_after_the_plan_keeps_the_saved_deadlines`）
- CI に任せる戦略の `_verify_init` は、初回 180 秒で走らせて `init_test` 行（戦略・置き場所・320 秒）を足し、2 回目は 960 秒で走らせる。
  `baseline_test` から組み直した `init_test_timeout` も 960（`test_the_scope_test_seconds_feed_the_next_init_limit`）
- 打ち切りでも `seconds: 180.0`・`timed_out: true` の行を足してから終了コード 4 で止まり、次の上限は 540
  （`test_a_timed_out_init_test_is_recorded_before_stopping`）
- 置き場所が suite の `paths` を覆い履歴に一致が無ければ s = w、w が無ければ無し（`test_the_whole_test_covers_the_scope`）
  （以上 `plugins/ndf/skills/cross-refactoring/tests/test_init.py`）
- `limits` が CI に任せる戦略・B 30 で s 320 → 960、424 → 1272、無し → 180、30 → 180 を返し、`basis` に s と出所が残る
  （`test_the_ci_strategy_init_limit_does_not_fall_below_the_scope_test`）。s で `test_timeout`・`whole_timeout`・`ci_wait_timeout` は変わらない
  （`test_the_scope_test_does_not_change_the_other_limits`）
- `local-full` / `round-only` は s を渡しても `max(0.10·B, 3·w)`（`test_other_strategies_ignore_the_scope_test`）。予算なしの表は
  s を渡しても変わらず、`basis` に `scope_seconds` を持たない（`test_supervise_limits_stay_the_same`）
- `covers_whole` が祖先・正規化・片方だけ・子・`.`・glob・空の置き場所・テストの suite が無いときを区別する
  （`test_covers_whole`・`test_covers_whole_needs_a_test_suite`）（以上 `plugins/ndf/scripts/tests/test_lib_test_strategy.py`）
- 状態から組み直した `limits` が s を運び、予算 60 分・s 不明なら 360。計画のコメントの「入力:」の行に `s 320.0（history）` が出る
  （`test_timeline.py` の `test_the_rebuilt_limits_carry_the_scope_test`・`test_the_plan_comment_shows_the_scope_test`）
- `init_test` 行が上の列を持ち、置き場所を並べ替えた集合で残す。`scope_seconds` は 11 行前・戦略違い・置き場所違いを使わず、
  打ち切りの行を混ぜても最大を採る。`schema` 1 と `kind` の無い行は範囲の一致に使わず、実行の行として読む。`init_test` 行を混ぜても
  配分テーブルは変わらない。`scope` の実行の行は `whole_test.init` を持たず、`round` は持つ
  （`plugins/ndf/skills/cross-refactoring/tests/test_allocation.py`）
- `ndf_record` が `init_test` 行と `whole_test.init` が `null` の実行の行を数えない
  （`plugins/ndf/scripts/tests/test_project_decl.py` の `test_ndf_record_skips_the_init_test_rows_and_scope_runs`）
- `run_baseline` が上限に届くと止めずに `timed_out: true`・`seconds` = 上限の記録を返す
  （`plugins/ndf/skills/cross-refactoring/tests/test_whole_timeout_shared.py` の `test_run_baseline_shares_the_limit_across_suites`）
- 既存の cross-refactoring のテスト（`plugins/ndf/skills/cross-refactoring/tests/`）と `python3 plugins/ndf/scripts/instructions-check.py --root .` が通ること

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 準備の時間 | `started_at` から着手前のテストの開始までの準備（作業ディレクトリと参加者の認証確認。認証は 1 者 120 秒まで）は今と同じく提案の枠から引かれる。長くなる実例が出たら起点の取り方を見直す |
| 初回の打ち切り | 範囲が suite の `paths` を覆わず履歴にも行が無い最初の実行は `0.10·B` で走り、範囲が広ければ 1 度打ち切られる。2 回目から `3·上限` になる。実物で何回目に通るかはリリース後テストで見る |
| 採れる件数 | 既定の 30 分のまま x が 300 秒を超えるリポジトリでは、提案は出ても採れる項目が 0 件に近くなりうる。実物の検証は「提案が 1 件以上出る」までで、採用の件数は測って記録する |
| 古い `whole_test.init` | `scope` の実行で範囲テストの秒が入った既存の行は直らず、直近 10 行の中央値から押し出されるまで `ndf-record` の w に混ざる |

## 関連リンク

- [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md)
- [`docs/02-plan-and-implement.md`](../../plugins/ndf/skills/cross-refactoring/docs/02-plan-and-implement.md)
- [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md)
- [`plugins/ndf/scripts/lib/test_strategy.py`](../../plugins/ndf/scripts/lib/test_strategy.py)
- [cross-refactoring-time-budget.md](cross-refactoring-time-budget.md)（想定最大時間と配分テーブル）
- 課題: #1385（取り込んだ課題 #1555。関連 #968・#1522・#1689）・Pull Request #1729
