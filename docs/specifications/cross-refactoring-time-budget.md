# cross-refactoring: ラウンドを上限まで回し、所要を時間で指定できず、テストの整備が半分を占めていた → 想定最大時間に収まる改修計画を 1 回だけ立てて実行し、時間の値をすべて予算から逆算する

## 目的

**利用者は所要の上限を時間（`--budget-minutes`。既定 30 分）で指定する。** 上限で止めるの
ではなく、上限に収まる改修計画を最初に立てる。

**提案 → 改修計画 → テスト追加 → 実装 → 検証/修正の 5 つの手順を、1 回の実行で 1 回ずつ行う。**
提案だけが参加者の全員で、改修計画以降は選ばれた 1 者（実装担当）が通す。ラウンド・群・輪番・
テスト整備ラウンドは無い（検証/修正の中の繰り返しを除く）。

**足すテストは、採った改善項目の検証に要る分だけである。** 網羅のテストを提案させない。

**見積りは実績から学ぶ。** 種類ごとの所要を実行のたびに履歴へ残し、次の改修計画は直近 10 回から
集計した配分テーブルで見積もる。

**時間に関わる数値は、想定最大時間 B と着手前の全体のテストの実測 w から算術だけで出す。**
改修計画の終わりまでに状態ファイルと改修計画へ書き出し、以後の手順は書き出した値と時計の比較だけで
進む。**改修計画の後で LLM が動くのは作業の CLI（テストの追加・実装・直し・最終ゲートの修正）
だけ**で、判断のために LLM へ問わない。

**判断のうち等級・同じ変更か・公開の入出力（D5）は、使えるときは Jev に問う。** 使えなければ
実装担当が同じ判断をし、進行は止まらない。

例: PR #917 と同じ範囲・同じ参加者（claude / codex / kiro、ホストは claude）で
`--budget-minutes 60` を渡すと、次のように見積もる（値は #917 の実行の要約の実測）。

| 時点 | 所要 | 経過 |
| --- | ---: | ---: |
| `init`（着手前の全体のテスト 1 回とラウンドのテスト 1 回） | 約 1.5 分 | 1.5 分 |
| 提案（3 者が並行。律速は codex） | 約 4.5 分 | 6 分 |
| 改修計画（実装担当 claude が 1 回） | 約 3 分 | 9 分 |

残り 51 分から予備時間（全体のテスト 1 分 × 2、修正 5.5 分 × 2）の 13 分を引いた 38 分に、項目を
順位の順に詰める。テストを足さない項目は 1.5 分、足す項目は 4.2 分で見積もるため、半分が
テストを足す項目なら 13 件前後が入る。変更の前（v10.17.5）の #917 は、テスト整備 2 ラウンドに
約 36 分を使い、5 ラウンド・25 項目・約 71 分で提案ラウンドの上限に達して止まった。

**手順・引数の表・式と係数は
[`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md) と
[`docs/02-plan-and-implement.md` の「締め切り」](../../plugins/ndf/skills/cross-refactoring/docs/02-plan-and-implement.md#締め切り)
が正である。** ここに式を複製きらない。この文書が扱うのは、そこに書かない決定の理由と、
改修計画と時間の内部の契約である。検証・取り消し・最終ゲートは
[検証と最終ゲートの確定仕様](cross-refactoring-verify-and-final-gate.md) が持つ。

## 用語

本文は左の業務用語で書く。識別子は表・コードブロック・業務用語の初出の括弧書きにだけ置く。

| 業務用語 | 識別子 | 何を指すか |
| --- | --- | --- |
| 想定最大時間 | `--budget-minutes` / `budget_minutes`（B） | 利用者が与える所要の上限（分）。目安の上限で、改修計画はこの中に収まるように立てる |
| 手順 | `phase` / `phases.<名前>` | `propose` / `plan` / `add-tests` / `implement` / `verify`（と `fix` / `final-fix`）。状態・履歴・起動の部品・上限の表・雛形で同じ語を使う |
| 実装担当 | `implementer` | 改修計画・テスト追加・実装・修正・最終ゲートの修正を通して担う 1 ランタイム |
| 候補 | `candidates[]` | 提案を鍵（`path` + `symbol` + `smell`）でまとめたもの。改修計画へ渡す |
| 改善項目 | `items[]` | 改修計画が採った候補。`I-001` の形の ID を持つ。1 改善項目 = 1 コミット（テストを足す項目は 2 コミット） |
| 種類 | `test` / `structure/<technique>` / `verify` / `fix` | 配分テーブルの行の単位 |
| 配分テーブル | `plan.table` | 種類ごとの 1 件あたりの所要（分）。履歴から改修計画のたびに集計し、保存しない |
| 履歴 | `cross-refactoring-allocation.jsonl` | 1 回の実行ごとに種類ごとの件数と所要を残した 1 行 |
| 予備時間 | `plan.reserve`（R） | 使える時間から先に差し引く 4 つの時間 |
| 着手の締め切り | `items[].start_deadline` / `test_start_deadline` | 実装・テストの追加にその項目を着手してよい最後の時刻 |
| 完了の締め切り | — | 着手の締め切り + その項目の見積り。取り込みはコミットの時刻をこれと比べる |
| 時間の上限の表 | `state["limits"]` | 余裕・テスト 1 回の上限・手順ごとの終わりの時刻。`init` と `merge-plan` が書く |
| 手順の監視の上限 | `PHASE_TIMEOUT` / `phases.<手順>.timeout` | その手順の終わりまでの残り + 余裕。無音の打ち切りも同じ値 |
| 判断の主 | `judge` | Jev を使うか（`kind`）、使わなかった理由（`reason`）、呼び出しの失敗の数（`failures`） |
| 範囲テスト | `items[].command` | 項目が触った箇所の範囲テスト。組み立ては検証の確定仕様 |
| 危険フラグ | `items[].danger`（D1〜D5） | 範囲テストで覆えない変更。立ったら全体のテストを 1 度走らせる（検証の確定仕様） |

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| 5 つの手順の 1 回の実行、引数（増えた 2 つと廃止した 5 つ）、実装担当の決め方 | 参加者の母集合と認証の確認（[参加者の確定仕様](cross-refactoring-participants.md)） |
| 候補の切り出し、改修計画の取り込み、見積り・予備時間・件数の選び方・締め切り | 検証・危険フラグ・取り消し・最終ゲート（[検証と最終ゲートの確定仕様](cross-refactoring-verify-and-final-gate.md)） |
| 時間の上限の表と手順の監視の上限、固定のまま残す値 | 監視そのものの結末の語彙（[起動 1 回の結末](cross-review-launch-outcome.md)） |
| Jev を使う箇所・条件・送る中身 | Jev の導入と鍵の配布（#841） |
| 配分テーブル・履歴・初期値、状態ファイルの版 2、報告 | 最終ゲートの `cross-review` の中身、駆動の bash を `scripts/` へ移すこと（#560 #870） |

## 背景

**所要を時間の側から制御できなかった。** v10.17.5 までは提案ラウンドと適用ラウンドを上限まで
回した。#917 では 5 ラウンド・25 項目・約 71 分で `--max-outer-rounds` の上限に達し、見送り
18 件の理由はすべて上限だった。収束したのか時間が足りなかったのかを利用者は選べなかった。

**テストの整備が所要の約半分を占めた。** テスト整備ラウンドは参加者が網羅的にテストを提案し、
#917 では 2 ラウンドで約 36 分を使った。

**担当の申告と実測が食い違った。** 担当が書く所要（`durations.apply`）は監視の実測と合わな
かった（申告 1420 秒・実測 1081 秒、申告 300 秒・実測 616 秒）。

**上限が予算と連動しなかった。** 監視の上限の表（提案と改修計画 1200 秒・書き換える手順 3600 秒）・
余裕 600 秒・テスト 1 回の上限 900 秒・無進捗の余白 900 秒・修正の回数 3 が固定値で、30 分の
予算でも最長 40 分を使えた。

## 決定と理由

番号は #933 の決定の番号である。コードのコメントの「決定 N」はこの番号を指す。検証・取り消し・
最終ゲートの決定（13〜16・20〜22）は [検証と最終ゲートの確定仕様](cross-refactoring-verify-and-final-gate.md#決定と理由) にある。

| # | 決定 | 理由 |
| ---: | --- | --- |
| 1 | 実装担当は名指し → ホスト → 参加者の先頭の順で 1 者に決める。輪番は廃止する | #917 の 1 件あたりの適用は claude 36〜66 秒・codex 102〜125 秒・kiro 120〜216 秒で、ホストを既定にすれば速い者が選ばれやすい。決め方が状態に依らず、再開しても変わらない |
| 2 | Jev は判断の主として使い、確信度の下限を置き、公開リポジトリに限る | 実装担当は同じ判断を必ず返すため、Jev が失敗しても答えが欠けない。送った中身は外部を通り、#841 は規約を確かめるまで顧客のリポジトリで使わないと定める |
| 3 | #754 は #933 の実装で目的を果たし、#755（群の中の並列の適用）は前提が無くなった | 枠の持ち越し・採用上限・ラウンドの上限は対象が消えた。並列の適用は「実装は 1 者」と衝突する |
| 4 | 引数は `--budget-minutes`（既定 30。2026-09-24 利用者の指示で 60 から変更）の分の整数 | #933 と #754 が分で語る。単位を名前の末尾に置くと秒の引数と読み違えにくい |
| 5 | ラウンド制の引数は、この変更を含む版では知らせて無視し、次の版で外す | 利用者の手元の手順が版を上げただけで壊れない |
| 6 | 配分テーブルの種類は `test` と `structure/<technique>`（と `verify` / `fix`） | 手数は手法で決まる。テストを経路で分けると 1 種類の標本が 1〜5 件になり値が定まらない |
| 7 | 配分テーブルは保存せず、利用者の手元の履歴からリポジトリごとに改修計画のたびに集計する | 表を別に持つと履歴と食い違う。テストの所要はリポジトリで大きく違う。リポジトリの中や状態ファイルに置くと対象のリポジトリへ混ざるか次の実行へ渡らない |
| 8 | 所要は進行側の時計とコミットの時刻で測り、担当の申告を使わない | 申告と実測が食い違った。1 項目 = 1 コミットにすれば項目ごとの所要が git から出る |
| 9 | 初期値は #917 の適用の CLI の実測を件数で割った値にする | 提案と採否の時間は別に測って経過として差し引くため、含めると二重に数える |
| 10 | 件数は順位の順に入る項目を入れ、入らない項目を飛ばして詰める | 打ち切ると大きな項目の後ろの小さな項目が入らず時間が余る。最適化の差より見積りの誤差が大きい |
| 11 | 順位は（等級, 賛同した者の数, 重要度）で決め、等級を Jev か実装担当が付ける | 数え上げと比較はスクリプト、等級の判断は Jev が得意。投票は全員の CLI をもう 1 度起動することになる |
| 12 | 実装は 1 回の起動で順位の順に行い、1 項目 = 1 コミット、着手は項目ごとの締め切りで止める | 項目ごとに起動すると文脈の読み直しでトークンと時間が項目の数だけ増える |
| 17 | 提案は観点を並べて観点ごとに探させ、件数の上限を示さない。改修計画へ渡す候補は `path` + `symbol` の組で上位 30 組・組の中の 3 件まで | 一覧が無いと目についた兆候に偏る（#917 は `long_method` が 10 件）。上限を示すと出す前に絞られる。組で切れば統合で組の数が減らない |
| 18 | 旧い形（ラウンド制）の状態ファイルは読み替えずに止める | ラウンドと群の途中を手順へ読み替えると、コミットと項目の対応を作り直すことになる |
| 23 | 締め切りはスクリプトが守らせる。手順の監視の上限は手順の終わり + 余裕で、直しは回数でなく締め切りまで試みる | LLM の申告に時間を預けると予算の外まで走る。回数で切ると時間が残っていても止まる |
| 24 | 時間に関わる数値はすべて B から算術で逆算し、改修計画の終わりまでに書き出す。`--max-fix-rounds` と `--test-timeout` も廃止する | 固定値は予算と連動しない。上書きの口を残すと連動しない経路が戻る |
| 25 | 改修計画の後で LLM が動くのは作業の CLI だけにする。D5 の Jev は改修計画へ前倒しし、機械で決まらないテストの差分はレビューへ引き継ぐ | 改修計画の後に問い続けると、見積もった時間の外で所要が伸び、応答で結果が変わる |
| 26 | 最終ゲートの修正 1 回分（`final_fix`）を予備時間に足し、1 回目の最終ゲートの修正は時計で打ち切らない | 検証の直しが予備時間を使い切ると、最終ゲートが落ちたときに 1 度も直せずに終わる。全体のテストが落ちたまま残るのが最も悪い結末である |

**クラス図は持たない**（決定 19）。`refactor_lib` は関数の集まりで型を定義しない。状態は
辞書として状態ファイルに持つ。

## 仕様

### 5 つの手順と駆動

| 手順 | 誰が | 起動と取り込み | 取り込みの終了コード |
| --- | --- | --- | --- |
| 提案 | 参加者の全員（並行） | `start-phase propose` → CLI → `merge-proposals` | 0 / 2（候補 0 件。最終ゲートへ） |
| 改修計画 | 実装担当 | `start-phase plan` → CLI → `merge-plan`（`TESTS_NEEDED=0\|1`） | 0 / 2（項目 0 件。最終ゲートへ）/ 4 |
| テスト追加 | 実装担当（足す項目があるときだけ） | `start-phase add-tests` → CLI → `merge-tests` | 0 / 2（残る項目 0 件） |
| 実装 | 実装担当 | `start-phase implement` → CLI → `merge-implement` | 0 / 2（残る項目 0 件） |
| 検証/修正 | 進行側と実装担当 | `verify`（`VERIFY=done\|fix`）→ `start-phase fix` → CLI → `merge-fix` の繰り返し | 0 / 4 |

**駆動の繰り返しは検証と修正の 1 つだけである。** 最終ゲートの修正の繰り返しは最終ゲートの側に
ある。`init` は再開の地点を `PHASE` として返し、駆動は終わった手順を飛ばす。各取り込みは
取り込み済みの目印で冪等である。

**手順の所要は進行側の時計で測る。** 開始は `start-phase` が CLI の起動の直前に書き
（2 度目は書き換えない。修正と最終ゲートの修正は起動ごとに `launch_started_at` を更新）、
終わりは各取り込みと `verify` が書く（`phases.<名前>.started_at` / `ended_at` / `seconds`）。

**提案の雛形は観点の一覧（`vocabulary.VIEWPOINTS` の 8 つ: 重複・責務の混在・分岐の表し方・
名前・依存の向き・テストの書きにくさ・データの形・大きさ）を並べ、観点ごとに探すよう求める。**
件数の上限は示さない。テスト整備の提案の雛形（`propose-tests.md`）は無い。

### 引数

| 引数 | 扱い | 既定 |
| --- | --- | --- |
| `--budget-minutes N` | 1 以上の整数。それ以外は `init` が終了コード 4（argparse の 2 にしない） | 30（`vocabulary.DEFAULT_BUDGET_MINUTES`） |
| `--implementer NAME` | 参加者の中の 1 者。参加者に無ければ終了コード 4 | 下の決め方 |
| `--round-test CMD` | 範囲テストを組み立てる元。**`--baseline-test` の実行器が既知でなければ必須**（無ければ提案の前に終了コード 4） | `--baseline-test` を元にする |
| `--max-test-rounds` / `--max-outer-rounds` / `--max-items-per-round` / `--max-fix-rounds` / `--test-timeout` | 廃止。受け取ると標準エラーへ `⚠ <引数> は廃止しました（#933）。--budget-minutes で所要を決めます` を出し、値を使わずに続ける（`setup.DEPRECATED_ARGS`） | — |
| そのほか（`--scope` / `--baseline-test` / `--host` / `--exclude` / `--include` / `--require-all` / `--model` / `--ci-check` / `--workflow-step` / `--severity-threshold` / `--sync-command` / `--plan-file`） | 変わらない | — |

既知の実行器は `pytest` / `python -m pytest` / `python3 -m pytest` / `jest` / `vitest` で、前置きの
`uv run [オプション]` / `poetry run` / `npx` は読み飛ばす（`testcmd.is_known`）。既知でない実行器
（`npm test`・`make -C backend test`・`cargo test`・ラッパー）から範囲テストを組み立てると、
提案と改修計画に時間を使った後に全項目が `no_target` になるため、着手前に止める。

### 実装担当の決め方

共通層の `assignment.choose_implementer(participants, host, named)` が決め、決め方を
`named` / `host` / `first` で返す。`init` が状態の `implementer` / `implementer_reason` /
`implementer_named` に書く。

| 場面 | 扱い |
| --- | --- |
| 名指しがある | 名指し。参加者に無ければ終了コード 4 |
| 名指しが無く、ホストが参加者にいる | ホスト |
| それ以外 | 参加者の先頭（ランタイムの固定の順） |
| 再開で `--implementer` を渡した | 置き換えない。違えば知らせるだけ |
| 再開で参加者を作り直し、実装担当が外れた | 改修計画の前なら決め方を当て直して `resume_changes` に残す。改修計画の後なら終了コード 4 |

**最終ゲートの修正も実装担当が担う**（`gate._final_fix_impl`）。

### 候補の切り出し（`merge-proposals`）

1. 鍵（`path` + `symbol` + `smell`）が同じ提案を機械的に統合する。賛同した者（`proposed_by`）を
   足し、重要度は高い方、見積りの行数は大きい方、文は詳しい方を採る
2. 語彙の外の提案は `vocabulary`、しきい値未満は `threshold` で見送る
3. （賛同した者の数, 重要度）の降順に並べ、`path` + `symbol` の組の単位で上位 30 組
   （`CANDIDATE_GROUPS`）を取り、組の中は上位 3 件（`CANDIDATES_PER_GROUP`）まで渡す。
   外れたものは `rank` で見送る

**意味の上で同じ提案かはここで決めない。** 改修計画の中で、同じ組の中の 2 件にだけ問う。1 者の
結果が欠けても全体は止めない。

### 改修計画の取り込み（`merge-plan`）

実装担当は候補の全件について `tier`（`high` / `medium` / `low`）・`tests`（足すテストの
置き場所）・`test_targets`（範囲テストの対象）・`merge_into`（同じ変更の相手の `key`）・
`risk`（公開の入出力が変わりうるか）を返す。**コマンドは返さない。** 改修計画を読めなくても止めず、
等級は既定の `medium`（または Jev）、テストは足さず、範囲テストは `--round-test` をそのまま使う。

| 順 | すること |
| ---: | --- |
| 1 | 等級を決める（Jev か実装担当。下の「Jev の使い方」） |
| 2 | 同じ変更を統合する。統合された候補は `duplicate` で見送り、賛同した者を統合先へ足す |
| 3 | 範囲テストを組み立てる。組み立てられず `--round-test` も無い候補は `no_target` で見送る |
| 4 | 見積もる。`test`（足すテストがあるときだけ）+ `structure/<手法>` + `verify` |
| 5 | 順位を決める。（等級, 賛同した者の数, 重要度）の降順、同じなら見積りの合計の昇順（`budget.rank_key`） |
| 6 | 使える時間に収まる項目を選ぶ。入らない項目は飛ばして次を見る（`budget.select`。`budget` で見送る） |
| 7 | 項目ごとの着手の締め切りを出し、`I-001` の形の ID を振る |
| 8 | 採った項目ごとに D5 を決めて `items[].public_io`（出所は `public_io_source`）に残す |
| 9 | 時間の上限の表を `state["limits"]` へ書き出す |

**改修計画は叩き直しても作り直さない。** 採用の件数・締め切り・予備時間は取り込んだ時点の予算で固定する。

### 時間の決め方

```text
経過 E          = 今 − started_at
予備時間 R          = danger_whole_test + final_whole_test + fix + final_fix
使える時間 A    = B − E − R
項目 i の見積り = (tests があれば test) + structure/<technique> + verify
終わり T        = started_at + B − R
```

| 予備時間（`plan.reserve`） | 値（分） |
| --- | --- |
| `danger_whole_test` | 着手前の全体のテストの秒（`baseline_test.seconds`）。測れていなければ 0 |
| `final_whole_test` | 同上。`--ci-check` があれば 0（継続的統合が想定最大時間の外で見る） |
| `fix` | 配分テーブルの `fix`（検証の直しの起動 1 回分） |
| `final_fix` | 同じ値。最終ゲートの修正 1 回分（決定 26） |

締め切りは T から逆算する（`budget.deadlines`）。実装の締め切りは後順位ほど遅く、テストの
追加の締め切りは実装と検証の全件を先に差し引いた時刻である。足すテストが無い項目の
`test_start_deadline` は `null`。

**時間の上限の表の要点**（係数は `refactor_lib/timeline.py` にだけ置く。式の正本は
[docs/02 の「締め切り」](../../plugins/ndf/skills/cross-refactoring/docs/02-plan-and-implement.md#締め切り)）。

| 値 | 要点 | 係数・関数 |
| --- | --- | --- |
| 余裕 | 0.05·B | `MARGIN_SHARE` |
| 着手前のテスト 1 回の上限 | 0.10·B（w はまだ無い） | `INIT_TEST_SHARE` |
| テスト 1 回の上限 | max(3·w, 0.01·B) | `TEST_FACTOR` / `TEST_FLOOR_SHARE` |
| 提案の枠の終わり / 改修計画の枠の終わり | 開始 + 0.20·B / さらに + 0.10·B（開始 + 0.30·B） | `PROPOSE_SHARE` / `PLAN_SHARE` |
| テストの追加・実装の終わり | 最後の項目の完了の締め切り | `timeline._completion` |
| 直しの試行の打ち切り | 開始 + B − `danger_whole_test` − `final_whole_test` − `final_fix`（予備時間の `fix` は引かない） | `budget.fix_end` |
| 最終ゲートの修正の打ち切り | 開始 + B。1 回目は打ち切らない | `final_end_at` / `gate._final_fix_stop` |
| 最終ゲートの修正の 1 回目の長さの下限 | 予備時間の `final_fix` を秒へ直した値 | `final_fix_seconds` |

**提案と改修計画の枠を予算の比率にしたのは**、#917 を 60 分に当てた例（提案 4.5 分・改修計画 3 分）の
倍の余裕があり、配分テーブルは提案と改修計画の所要を持たないためである。**テスト 1 回の上限を
3·w にしたのは**、範囲テストは全体のテストの部分で w を超えることは本来無く、3 倍は負荷の
揺れの幅だからである。

### 手順の監視の上限

`start-phase <ID> <手順>` が、`state["limits"]` のその手順の終わり（`timeline.PHASE_END_KEYS`）
までの残り + 余裕を `PHASE_TIMEOUT` として返し、`phases.<手順>.timeout` と
`phases.<手順>.cli_timeout`（+ 余裕）に残す。終わりを過ぎていれば余裕だけを返す。

| 渡す先 | 値 |
| --- | --- |
| 監視の `--timeout` と `--stall-timeout`（無音の打ち切り） | `PHASE_TIMEOUT`（同じ値） |
| CLI の上限（skill 側の `launch-cli.sh` が読む） | `cli_timeout` |
| 最終ゲートの修正の 1 回目 | max(残り, `final_fix_seconds`) + 余裕（`timeline.final_fix_timeout`） |

**無音の打ち切りを手順の上限と同じ値にするのは**、ランタイムごとの無音の性質（claude は完了まで
何も出さない）を下限に残すと固定値が残り、実装担当が claude のときは手順の途中で止まるため
である。締め切りが待ちの長さを上から抑える。

**止めたときの取り込みは変えない。** 未コミットの変更は取り込みの前に捨て、コミット済みの
項目は完了の締め切りとコミットの時刻で判定する。止めたかは監視の結果ファイルの `reason`
（`timeout` / `stalled`）から読み（`gitfacts.note_stopped`）、`phases.<手順>.stopped` に残して
報告に「監視が止めた手順」の 1 行を出す。

**共通層の上限の表（`plugins/ndf/scripts/lib/limits.py`）は cross-review と共有するため、値を
変えない。** cross-refactoring の手順の名前（`plan` / `add-tests` / `implement`）を表に足したのは、
`start-phase` を通らない起動の既定の下限としてである。

**固定のまま残す値**（`timeline.FIXED_VALUES`。報告に並ぶ）。OS の後始末と通信の待ちで、予算と
性質が違う。

| 値 | 置き場所 |
| --- | --- |
| 打ち切ったプロセスグループへ SIGKILL を送るまで 5 秒 | `gitfacts.run_with_timeout` の `kill_grace` |
| 監視が止めた CLI へ SIGKILL を送るまで 3 秒 | `monitor.py` |
| 結果ファイルを書き終えたとみなす経過 30 秒 | `monitor.py` の `RESULT_AGE_GRACE` |
| 監視の見回りの間隔 15 秒 | `monitor.py` |
| Jev の通信 1 回の待ち 10 / 20 秒 | `jev.py` の `PROBE_TIMEOUT` / `ASK_TIMEOUT` |
| 認証の確認 1 回の待ち 120 秒 | `auth.py` の `AUTH_PROBE_TIMEOUT` |

### Jev の使い方

共通層の `plugins/ndf/scripts/lib/jev.py` が Vercel AI Gateway の `POST /v1/evaluate` へ
1 問ずつ問う（モデル `typesafe-ai/jev`）。失敗は `None` を返し、例外を外へ出さない。

**使える条件は 4 つがそろうことである。** `init` が 1 度だけ確かめ、状態の `judge` に残す。

| 条件 | 満たさないときの `judge.reason` |
| --- | --- |
| 環境変数 `AI_GATEWAY_API_KEY` がある | `no_key` |
| `NDF_JEV` が `0` でない | `disabled` |
| 対象が公開リポジトリ（`gh repo view --json visibility` が `PUBLIC`） | `private_repo` |
| 疎通の確認（固定の `boolean` の問い 1 回、10 秒）が通る | `probe_failed` |

**問うのは改修計画の手順だけである。**

| 箇所 | 問いの型 | 採る確信度の下限 | 下回る・失敗したとき |
| --- | --- | --- | --- |
| 等級（`tier`） | `score`（low / medium / high） | 0.6（`JEV_TIER_CONFIDENCE`） | 実装担当の等級 |
| 同じ変更か（同じ `path` + `symbol` の 2 件だけ） | `boolean` | 0.8（`JEV_DUPLICATE_CONFIDENCE`） | 実装担当の `merge_into` |
| 公開の入出力が変わりうるか（D5） | `boolean` | 0.7（`JEV_RISK_CONFIDENCE`） | 実装担当の `risk` |

**D5 は改修計画へ前倒しした**（決定 25）。改修計画の時点では差分がまだ無いため、提案の文だけで問う。
検証は `items[].public_io` を読むだけである。見落としは最終ゲートが拾う。

**送るのは提案のフィールド（`path` / `symbol` / `smell` / `technique` / `rationale` / `plan`）と
賛同した者の数だけである。** 差分・ファイルの本文・テストの出力は送らない。鍵の値は環境変数から
だけ読み、状態・報告・ログへ書かない。使える実行の中で 1 回の呼び出しが失敗したら、その問いは
実装担当の答えで決め、`judge.failures` に数える（`judge.kind` は `jev` のまま）。

### 配分テーブルと履歴

| 項目 | 内容 |
| --- | --- |
| 履歴の置き場所 | `<metrics>/<owner>--<repo>/cross-refactoring-allocation.jsonl`（`allocation.history_path`）。根は実行の要約と同じ（`NDF_METRICS_DIR` → `$XDG_STATE_HOME/ndf/metrics` → `~/.local/state/ndf/metrics`）。`NDF_METRICS=0` でも書く（改修計画の材料であって計測ではない） |
| 1 行の形 | `schema`（1）・`run`・`at`・`pr`・`implementer`・`budget_minutes`・`elapsed_seconds`・`phases`（手順ごとの秒）・`kinds`（種類ごとの `count` と `seconds`）・`verify`（`items` / `seconds`）・`fix`（`launches` / `seconds`）・`whole_test`（`init` / `danger` / `final`。走らなかった場所は `null`） |
| 数える項目 | コミットがあり、取り消し・見送りでない項目だけ。所要は `items[].seconds`（コミットの時刻から測った値） |
| 集計 | 種類ごとに、**その種類を含む行**の直近 10 行（`allocation.WINDOW`）の Σ秒 ÷ Σ件数。`verify` は検証した改善項目の数、`fix` は起動の数で割る |
| 足りないとき | `structure/<手法>` は `structure/*` をまとめた値 → 初期値。ほかは初期値。読めない行は飛ばす。ファイルが無い・全行が読めなければ初期値で改修計画し、`plan.table_source` を `defaults` にして 1 行知らせる |
| 初期値 | `plugins/ndf/skills/cross-refactoring/data/allocation-defaults.json`。`test` 2.7・`structure` 1.3・`verify` 0.2・`fix` 5.5（分）と、出所（#917 の記録の URL）と式を持つ。`fix` は #917 で修正が 0 回だったため適用の群の中央値で代えた未確認の値である |
| 実装担当ごとに分けない | 分けると標本が 3 分の 1 になる。行には残すため、後で集計を変えられる |

**追記は `finalize` が行う。** 最終ゲートが通った実行だけを 1 行追記し、`history_written` で
二重に書かない。追記に失敗しても終了コード 0 で知らせるだけである。追記する時点と条件は
[検証と最終ゲートの確定仕様](cross-refactoring-verify-and-final-gate.md#履歴へ追記する時点) にある。

### 報告

`refactor.py report` は次を出す。

- 手順ごとの所要と、想定最大時間との差（`cross-review` の所要は含めない）
- 改善項目の表（`<ファイル>#<シンボル>`・兆候・手法・等級・見積り・状態・危険フラグ・修正の回数）
- 採用・取り消し・見送りの件数と、見送りの理由別の件数（内訳は改修計画の生の URL）
- 着手前の全体のテストの結果（通過か失敗・秒・HEAD の短い SHA）と、検証の中で全体のテストを
  走らせたか・その理由の危険フラグ・落ちたときの見分けと結末
- 判断に Jev を使ったか（使わなかった理由・呼び出しの失敗の数）
- 最終ゲートの結果、監視が止めた手順、固定のまま残した値

改修計画のコメントには「時間の上限」の表と項目ごとの締め切りも載る。読み手は実行の前に
すべての時刻を見られる。

## データ・設定

### 状態ファイル（版 2）

置き場所は `<work>/.cross_refactoring/cross-refactoring-rf<番号>-state.json` で、最上位に
`schema: 2`（`setup.SCHEMA`）を持つ。

| キー | 中身 |
| --- | --- |
| `schema` / `budget_minutes` / `started_at` | 版・想定最大時間・`init` の開始 |
| `phase` | `propose` / `plan` / `add-tests` / `implement` / `verify` / `final` / `done` |
| `phases.<名前>` | `started_at` / `ended_at` / `seconds`。起動する手順は `launch_started_at` / `base_sha` / `timeout` / `cli_timeout`、止めたら `stopped`（`reason` / `timeout` / `at`） |
| `participants` / `runtimes` / `models` / `resume_changes` | 参加者（[参加者の確定仕様](cross-refactoring-participants.md)） |
| `implementer` / `implementer_reason` / `implementer_named` / `implementer_model` | 実装担当と決め方・名指しの記録・要求と観測のモデル |
| `judge` | `{kind: "jev" \| "runtime", reason, failures}` |
| `candidates[]` | 統合した提案 |
| `plan` | `base_sha`・`elapsed_minutes`・`available_minutes`・`reserve`（4 キー）・`table_source`・`table`・`selected[]`・`end_at` |
| `items[]` | 採った項目（下の表） |
| `deferred_items[]` | 見送り。理由は `budget` / `rank` / `duplicate` / `vocabulary` / `threshold` / `no_target` / `test_failed` / `not_done` の 8 つ（`vocabulary.DEFER_REASONS`） |
| `limits` | 時間の上限の表（下の表） |
| `baseline_test` / `round_test` | 着手前のテスト。`baseline_test` は `seconds` と `head` も持つ |
| `whole_test` / `verify_stats` / `fix_stats` / `fix` / `drops` / `pending_drop` | 検証と取り消し（検証の確定仕様） |
| `final_gate` / `plan_comment` / `pending_push` / `sync_command` | 最終ゲートと公開 |
| `history_written` | 履歴へ追記したか |

`items[]` の 1 件の主なキー: `id`・`rank`・`kind`（`structure/<technique>`）・`tier` と
`tier_source`・`estimate`（`test` / `implement` / `verify` の分）・`tests[]`・`test_targets`・
`command` と `command_source`・`start_deadline`・`test_start_deadline`・`public_io` と
`public_io_source`・`status`・`commits`（`test` / `implement` / `fix[]`）・`seconds`
（`test` / `implement`）・`fix_count`・`danger[]`・`review_test_judgements`・`failure_reason`。

`state["limits"]` のキー（`timeline.compute`）:

| キー | 書く時点 |
| --- | --- |
| `budget_minutes` / `margin_seconds` / `init_test_timeout` / `test_timeout` | `init`（改修計画の前に予算を置き換えた再開も組み直す） |
| `propose_end_at` / `plan_end_at` / `final_end_at` | `init` |
| `add_tests_end_at` / `implement_end_at` / `fix_end_at` / `final_fix_seconds` | `merge-plan`（改修計画の前は `null`） |

### 旧い状態ファイルと再開

| 前回の状態 | 扱い |
| --- | --- |
| 無い・版 2 で `phase` が `done` | 新しく始める |
| 版 2 で終わっていない | 再開する。`PHASE` を返し、駆動は終わった手順を飛ばす |
| 旧い形（`schema` を持たず `rounds` を持つ）で `final` が空 | 終了コード 4。旧い版（v10.17.5 以前）で終えるか状態ファイルを消して始め直すよう案内する |
| 旧い形で `final` が入っている | 新しく始め、版 2 の形で作り直す |

**再開で `--budget-minutes` を置き換えるのは改修計画の前だけである**（`phase` が `propose` か
`plan` で `plan` が空）。置き換えたら時間の上限の表を組み直す。改修計画の後は知らせるだけにする。
採用の件数・締め切り・予備時間が改修計画の時点の予算で固定されているためである。

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| 予算の既定・1 以上の整数でない値で終了コード 4・廃止した 5 引数の知らせと無視・既知でない実行器で `--round-test` が無いと止まること | `plugins/ndf/skills/cross-refactoring/tests/test_init.py` |
| 実装担当が名指し → ホスト → 先頭で決まり、再開の前後で規則どおりに当て直すか止まること | 同 `test_init.py` / `tests/test_assignment.py` / `plugins/ndf/scripts/tests/test_lib_assignment.py` |
| 旧い形の状態ファイルで止まる・終わった旧い状態を作り直す・再開の地点を返すこと | 同 `test_init.py` |
| 5 つの手順が 1 回ずつ走り、提案だけが全員で、履歴へ 1 行追記されること | 同 `tests/test_flow_git.py` |
| 組の単位で 30 組・組の中の 3 件まで渡り、外れた提案が `rank` になること | 同 `tests/test_merge_proposals.py` |
| 見積り・予備時間（#917 の例）・飛ばして詰める・締め切り・直しの終わりが `fix` を引かず `final_fix` を引くこと | 同 `tests/test_budget.py` |
| 改修計画の取り込みが順位・見積り・テスト・対象を持ち、改修計画を読めなくても止まらず、叩き直しで作り直さず、実行時の値をすべて書き出すこと | 同 `tests/test_merge_plan.py` |
| Jev の等級・同じ変更か・D5 が確信度の下限で採られ、使えなければ実装担当の答えになること。非公開と鍵なしで Jev を使わないこと | 同 `test_merge_plan.py` / `test_init.py` |
| 時間の上限がすべて予算に従い、テストの上限が w で伸び、手順の上限が残り + 余裕であること | 同 `tests/test_timeline.py` |
| `start-phase` が開始を 1 度だけ書き、最終ゲートの修正の 1 回目に予備時間の長さを渡すこと | 同 `test_init.py` |
| 監視が止めた手順が締め切りで取り込まれ、報告に出ること | 同 `tests/test_phases_git.py` |
| 改修計画の後で Jev と判定の CLI が呼ばれないこと | 同 `tests/test_after_plan_no_llm_git.py` |
| 履歴の置き場所・集計の窓と代わり・初期値と出所・取り消しと見送りを数えないこと | 同 `tests/test_allocation.py`（根を引数で渡し、利用者の手元を読み書きしない） |
| 報告が手順・理由別の件数・全体のテスト・Jev を持つこと | 同 `tests/test_report_phases.py` |
| 雛形に観点の語彙・締め切り・対象の項目が渡ること | 同 `tests/test_propose_prompt.py` |

## 関連リンク

- [issue #933](https://github.com/devbasex/ai-plugins/issues/933) — 想定最大時間に収める 1 回の改修計画実行
- [PR #941](https://github.com/devbasex/ai-plugins/pull/941)（設計） / [PR #951](https://github.com/devbasex/ai-plugins/pull/951)（実装）
- [issue #754](https://github.com/devbasex/ai-plugins/issues/754) / [issue #755](https://github.com/devbasex/ai-plugins/issues/755) — 枠と並列の適用（決定 3）
- [issue #841](https://github.com/devbasex/ai-plugins/issues/841) — Jev
- [#917 の実行の記録](https://github.com/devbasex/ai-plugins/pull/917#issuecomment-5796914428) — 初期値の出所
- [検証と最終ゲートの確定仕様](cross-refactoring-verify-and-final-gate.md)
- [cross-refactoring の参加者](cross-refactoring-participants.md)
- [ラウンドのテストと assess](cross-refactoring-round-tests-and-assess.md)
- [取り込みの共通手順](cross-refactoring-apply-intake.md)
- [`cross-refactoring` の手順](../../plugins/ndf/skills/cross-refactoring/SKILL.md)
- [`cross-refactoring` の改修計画・テスト追加・実装](../../plugins/ndf/skills/cross-refactoring/docs/02-plan-and-implement.md)
