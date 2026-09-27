# #1334: テストの走らせ方を文字列の解析でなく戦略の宣言で決め、長い全体テストのプロジェクトでも cross-refactoring を回せるようにする

正は課題の本文（#1334）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

**この節は「何を満たすか」だけを扱う。** どう作るか（戦略の一覧の確定・宣言のキー・JUnit の読み方・時間の算出式）は設計が決める。
マイルストーンは「23 汎用性の回復」。版は配布の工程が決める（`release`）。

## 例: carmo-system-console で `/ndf:cross-refactoring` を回したとき

今（ndf 10.17.34）:

1. `--baseline-test "docker compose exec -T app ./vendor/bin/phpunit"` を渡すと、`refactor_lib/testcmd.py` が文字列から実行器を探し、pytest / jest / vitest のどれでもないので `--round-test` を求めて `init` で止まる
2. `--round-test` を足しても、着手前の全体テスト（直列で約 64 分）が予算の 0.10 倍（30 分なら 180 秒）の上限で打ち切られて止まる
3. 記録では 2026-09-18・09-22 の 2 回とも、本体を回す前に `refactoring` へ退避した

変えた後:

1. `init` は `.ndf/project.json`（#1333 の宣言）を読み、テストの戦略 `local-scoped-ci-whole` を選ぶ。コマンドの文字列は解析しない
2. 項目ごとの範囲テストは、宣言の `scope_command`（`docker compose exec -T app ./vendor/bin/phpunit {paths}`）の `{paths}` へ計画の `test_targets` を入れて走らせる
3. 全体テストは手元で走らせず、CI に任せる。CI が落ちたら JUnit から落ちたテストのファイルを取り、手元でそのファイルだけを走らせ直して、フレーキーか変更起因かを見分ける

## 依頼（原文）

> cross-refactoring（と supervise の検査・実装のプラン）のテストの走らせ方を、テストのコマンドの文字列を解析して決めるのをやめる。戦略を複数用意し、#1333 のプロジェクトの解析で決めた戦略を宣言から読む。（2026-09-27 利用者の指示: 「このやり方（文字列から探す）を続けるのに無理がある」「全てのプロジェクトで統一案は無理」）

課題の元の「設計で決めること」:

> - 戦略の一覧と、それぞれの宣言の項目（`{paths}` の雛形・JUnit の出し方・全体テストの置き場）。supervise と同じ宣言・同じ関数を共有する
> - 落ちたテストの ID の読み方（JUnit XML に寄せるか）
> - `local-scoped-ci-whole` / `ci-only` の危険フラグ（D1〜D5）の全体テストの扱い。項目ごとに CI を回すと待ちが積み上がるため、最終ゲートへ寄せるか
> - 予算（`--budget-minutes`）からの時間の算出に、CI の待ちをどう入れるか
> - `testcmd.py` の解析（`runner_index` / `is_known` / `build` / `failed_nodes` / `rerun_command`）を消す範囲

課題の元の受け入れ条件:

> - テストのコマンドの文字列から実行器を推測するコードが無い
> - ai-plugins（`local-full`）と carmo-system-console（`local-scoped-ci-whole`）で cross-refactoring の `init` から範囲テストまでが止まらずに通る
> - `env` / `uv run` / `docker compose exec` などの前置きの有無で振る舞いが変わらない
>
> 関連: #1333（戦略を決める解析）、#1312（この課題へ吸収して閉じた）

## 今の状態（2026-09-27 に調べた）

- `refactor_lib/testcmd.py` は `--baseline-test` の文字列から実行器（pytest / jest / vitest の 3 つに固定）と差し替えの位置を推測する。前置きを読めないと止まる（#1312。`env` だけは PR 1329 で読み飛ばした）。使うのは `commands/setup.py`・`commands/plan.py`・`commands/implement.py`・`triage.py` の 4 本
- supervise はテストのコマンドを `{paths}` の雛形で受ける（`.ndf/supervise.json` の `test.command`、置き換えは `supervise_lib/paths.py`）が、`new check` は `{paths}` を埋めてから cross-refactoring へ渡し、cross-refactoring が文字列を解析し直す
- pytest 以外では、`--round-test` が無いと `init` で止まり、項目ごとに範囲を絞れず、全体テストが落ちたときの見分け（落ちたテストだけ走らせ直す）は pytest の出力の形だけを読む（`triage.py` の `classify`、`testcmd.py` の `failed_nodes`）
- 全体テストを手元で走らせる前提は、長いプロジェクトで成り立たない。carmo-system-console は CI の直近の成功 run 36125664723 の JUnit 24 本の合計で 13,178 件・3,827 秒（直列で約 64 分）。CI は 24 本に分けて壁時計約 6 分。1 ファイルは中央値 1.6 秒・上位 10% で 6.1 秒・最大 105 秒。ai-plugins は全体 6,660 件で 140 秒
- **#1333 はマージ済みである**（PR 1346・1347・1348）。ai-plugins の `.ndf/project.json` には `test.suites[]`（`runner`・`command`・`scope_command`・`container`・`needs`・`paths`）・`test_duration.measured[]`（`source` は `ndf-record` / `ci-junit` / `ci-steps`）・`ci`・`services` がある。戦略そのものを表すキーは無い。schema は `plugins/ndf/skills/development-workflow/schemas/project.schema.json`
- carmo-system-console の `.ndf/` には `worktree.json` しかなく、`project.json` はまだ無い（2026-09-27 に `ls` で確かめた）
- ai-plugins では、同じテストのコマンドが `.ndf/supervise.json` の `test.command` と `.ndf/project.json` の `test.suites[0].scope_command` の 2 箇所にある

## 汎用性の調査で分かったこと（2026-09-27 追記）

根拠は `issues/genericity-survey-2026-09-27.md` の G2。戦略の設計で合わせて扱う。行番号は調査の時点の値で、今はずれている箇所がある。

- **supervise の雛形の時間が固定**: `test-limited` 900 秒・`test-all` 1800 秒（`supervise_lib/templates.py`）、CI の待ち 3600 秒（`merged-steps.py`、`paths.py` の `MERGE_CMD` が `--timeout` を渡さない）。宣言で変えられない
- **`new check` が `--round-test` を渡さない**（`templates.py`）。pytest / jest / vitest 以外ではリファクタリングのステップが必ず止まる
- **落ちたテストの走らせ直しが pytest だけ**: supervise は `PYTEST_ADDOPTS` に `--lf` を足す（`steps.py`、`plan.py` の `rerun_failed`）。pytest 以外やコンテナの中の pytest には届かず、全体を走らせ直して 2 回目に通れば「フレーキー」として成功にする
- **cross-refactoring の時間**: 着手前のテストの上限が予算の 0.10 倍（`timeline.py`、30 分なら 180 秒）。予備に全体テスト 2 回分を引く（`budget.py`）。配分の初期値が ai-plugins の実測（`allocation-defaults.json`、verify 10 秒）
- **危険フラグが PHP とコンテナで立たない**（`/tmp/ndfprobe` で実測）: D3 の照合が PHP の `use App\Services\UserService;` に当たらない（`danger.py`）。`docker compose exec -T app php artisan test tests/Unit` の `app` を範囲の起点と読み、本体の `app/Services/UserService.php` をテストの側に入れて D4 が立たない（`scope.py`）
- **`--scope` がテストの置き場所のディレクトリを必須にする**（`scope.py`）。テストを同じ場所に置く構成（`src/**/*.spec.ts`）で止まる
- **着手前のテストが red なら止まる**（`commands/setup.py` の `status == "red"`）。既存失敗を抱えたまま運用しているプロジェクトでは一度も起動できない
- 実例: carmo-system-console で `/ndf:cross-refactoring` を 2 回起動し、2 回とも本体を回す前に `refactoring` へ退避した（2026-09-18・09-22。手元で phpunit を回さない方針）

## 目的

- **テストの走らせ方を、コマンドの文字列から推測しない。** 前置き（`env`・`uv run`・`docker compose exec`・`./scripts/*.sh`）や実行器の種類が増えるたびに解析を足す方式をやめる
- **1 つの走らせ方を全プロジェクトに課さない。** 全体テストが数分で終わるプロジェクトと、1 時間かかり CI で分割しているプロジェクトとで、別の戦略を選べるようにする
- **cross-refactoring と supervise が、同じ宣言から同じ関数でテストの走らせ方を決める**（cross 系の共通ロジックは 1 つに）

## 解釈

| 依頼文の語 | 具体化 |
| --- | --- |
| 文字列を解析して決めるのをやめる | 実行器の名前・差し替えの位置・落ちたテストの ID を、テストのコマンドの文字列やその出力の形（pytest の `FAILED` の行など）から取り出すコードを消す。コマンドは宣言か引数の `{paths}` の雛形として受け、`{paths}` を置き換えるだけにする |
| 戦略 | テストを「どこで・どの範囲で・いつ」走らせるかの組。範囲テストの走らせ方・全体テストの置き場（手元か CI か）・落ちたテストの見分け方の 3 つを持つ。候補は下の表の 4 つで、確定は設計 |
| 宣言から読む | #1333 の `.ndf/project.json`（P2〜P5）と、必要なら戦略を表すキーを読む。戦略を宣言に直に書くか P2〜P5 から導くかは設計 |
| 止まらずに通る | cross-refactoring の `init` → `plan` → 最初の項目の範囲テストの実行までが、テストの走らせ方を理由に終了しない（テストが落ちるのは止まる理由に含めない） |
| 前置きで振る舞いが変わらない | 同じ宣言で前置きだけを変えた 2 つのコマンドに対し、選ぶ戦略・走らせる範囲・落ちたテストの見分けの結果が同じになる |
| 長い全体テスト | 宣言の `test_duration` が、予算から出す全体テストの上限を超えるもの。carmo-system-console（約 3,827 秒）が実例 |

**戦略の候補**（課題の元の案。確定は設計）:

| 戦略 | 向くプロジェクト | 範囲のテスト | 全体テスト |
| --- | --- | --- | --- |
| `local-full` | 全体が数分で終わる（ai-plugins） | `{paths}` の雛形へ対象を入れる | 手元で走らせる |
| `local-scoped-ci-whole` | 全体が長い（carmo） | `{paths}` の雛形へ対象を入れる（コンテナ越しでよい） | CI に任せる。JUnit で落ちたファイルを取り、手元で走らせ直してフレーキーか変更起因かを見分ける |
| `round-only` | パスでテストを選べない | `--round-test` をそのまま走らせる | 手元で走らせる |
| `ci-only` | 手元でテストが走らない | 走らせない（静的な検査だけ） | CI に任せる |

## 前提

- 前提 1: 戦略の入力は #1333 の `.ndf/project.json` である。#1333 はマージ済みで、schema は `development-workflow/schemas/project.schema.json` にある。戦略を表すキーを足すなら、この schema と `project-decl.py` の解析の答えの形を広げる（新しい宣言のファイルは作らない）
- 前提 2: 宣言に `test` が無い・不明（`{"unknown": ...}`）のときは、ai-plugins の値で埋めない（#1333 の不変条件 I4）。引数の `--round-test` があれば `round-only` として進み、無ければ `init` が「宣言の `test` が無い（か不明）」と直し方を出して止まる
- 前提 3: 引数（`--baseline-test` / `--round-test`）は宣言より優先する。`{paths}` を含む引数は範囲テストの雛形として、含まない引数はそのまま走らせるコマンドとして扱う。どちらも文字列の中身は解析しない。廃止する引数があれば、今の廃止の扱い（知らせて無視する）に合わせる
- 前提 4: 落ちたテストの ID は、実行器の出力の文字列ではなく、実行器が書く JUnit XML から読む（課題の案）。JUnit を出せない戦略・実行器では、落ちたテストの見分けを「全体を走らせ直して同じ結果か」に落とし、その旨を計画と結果に書く
- 前提 5: CI に任せる全体テストは、PR の CI の結果を読む。**CI を起動するために push するのはオーケストレーターだけ**で、実装担当は push しない（cross-refactoring の今の取り決め）。CI の待ちは NDF の既存の待ち（`merge-when-green` / `supervise.py wait` の系統）を使い、手書きの待ちを増やさない
- 前提 6: 危険フラグ（D1〜D5）が立ったときの全体テストは、全体テストを CI に任せる戦略では項目ごとに CI を回さず、最終ゲートの 1 回へ寄せる（課題の案）。寄せたことは計画と結果に書く。寄せ方の細部（D が立った項目を最終ゲートで落ちたときに取り消す順）は設計
- 前提 7: 着手前のテストで既に落ちているテストは「既存失敗」として記録し、着手を止めない。完了の判定は「既存失敗の外で新しく落ちたテストが無いこと」で行う。既存失敗を直すことはこの課題の範囲に含めない
- 前提 8: 検証で carmo-system-console に置く `.ndf/project.json` は、#1333 の解析で作ったものを使い、サンプルへはコミットしない（#1333 の前提 7 と同じ）。サンプルのworktreeへ書いて確かめた後で消すか、一時ディレクトリに置く
- 前提 9: 残る 3 つのサンプル（carmo-contractors-app・project-trygroup-prd・with-ai-dev）での実測はこの課題の受け入れ条件にしない。戦略の一覧がこの 3 つにも当てはまるか（どれを選ぶか）を設計に表で示すだけにする
- 前提 10: 言語ごとの判定（テストのパスの目印・PR の分類・指標の言語表）は G6 が扱う。この課題が扱う言語の判定は、危険フラグ D3 の import の照合と D4 の範囲の起点だけである（調査が G2 に入れたため）

## 対象範囲

含む:

- 戦略の一覧の確定と、各戦略が宣言から読む項目・宣言に足すキー（schema を含む）
- cross-refactoring の `init`・`plan`・`implement`（範囲テスト・危険フラグの全体テスト・最終ゲート）・`triage` の、戦略に沿った作り直し
- supervise の `new check` と実装・検査の雛形（範囲テスト・全体テスト・落ちたテストの走らせ直し・打ち切りの時間）を、同じ宣言と同じ関数へ寄せること
- `testcmd.py` の文字列の解析（`runner_index` / `is_known` / `build` / `failed_nodes` / `rerun_command` と前置きの読み飛ばし）の削除
- 落ちたテストの ID を JUnit XML から読むこと
- 予算からの時間の算出に、宣言の所要（`test_duration`）と CI の待ちを入れること。着手前のテストの上限（予算の 0.10 倍）・予備の全体テスト 2 回分・配分の初期値（ai-plugins の実測）の見直し
- supervise の固定の打ち切り（900 / 1800 / 3600 秒）を宣言から出すこと
- 危険フラグ D3 の PHP の `use` への照合と、D4 の範囲の起点をコマンドの文字列から読まないこと
- `--scope` が、テストを本体と同じ場所に置く構成（`src/**/*.spec.ts`）で止まらないこと
- 着手前のテストが red のときに、既存失敗として記録して進めること
- ai-plugins と carmo-system-console での実測

含まない:

- 宣言を作る解析そのもの（#1333。戦略のキーを足すなら、その答えの形を広げるところまでは含む）
- 言語ごとの判定（G6）、worktreeの環境（`vendor/` など。G4）、マージと PR の運用（G5）、配布（G3）、cross-review の再開と hook（G8）
- 残る 3 つのサンプルでの実測（前提 9）
- 既存失敗を直すこと（前提 7）
- CI のワークフローの書き換え（JUnit を出していないプロジェクトの CI に JUnit を足すなど）。足すべきと分かったときは案内だけにする

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | テストの戦略を選んだ | cross-refactoring の `init`、supervise の `new check` / `new implement` | 宣言に `test` が無い・不明で、引数も無い → 直し方を出して止まる（前提 2・AC4） | 宣言（#1333）が読める |
| E2 | 着手前のテストを走らせた | E1 の後、計画の前 | 時間を超えた → 戦略の全体テストの上限と所要を出して止まる。落ちた → 既存失敗として記録して進む（前提 7・AC10） | E1。全体テストを CI に任せる戦略では、着手前の全体テストは CI の直近の結果で代える（AC3） |
| E3 | 項目の範囲テストを走らせた | 項目の実装の後 | 落ちた → 締め切りまで直す（今と同じ） | E2 |
| E4 | 危険フラグが立ち、全体テストを走らせた（か最終ゲートへ寄せた） | D1〜D5 の検出 | 落ちた → JUnit から落ちたテストを取り、走らせ直してフレーキー・既存失敗・変更起因に分ける（AC6） | E3。CI に任せる戦略では最終ゲートへ寄せる（前提 6） |
| E5 | 最終ゲートの全体テストの結果が出た | 手元の全体テストの終了、または CI の結果 | CI が時間内に終わらない → 待ちの上限と経過を出して「判断が要る」で終える。落ちた → E4 と同じ見分け | E3・E4 の後。CI の場合は push の後（前提 5） |
| E6 | 落ちたテストを見分けた | E4・E5 の失敗 | JUnit が無い・読めない → 全体の走らせ直しに落とし、その旨を書く（前提 4） | E4・E5 |

E1 の宣言の欠けは前提 2 へ、E2 の red は前提 7 へ、E5 の push の担い手は前提 5 へ移した。

## 用語

| 用語 | 意味 |
| --- | --- |
| テストの戦略 | 範囲テストの走らせ方・全体テストの置き場（手元か CI か）・落ちたテストの見分け方の組。候補は `local-full` / `local-scoped-ci-whole` / `round-only` / `ci-only` |
| 範囲テスト | 計画の `test_targets` だけを走らせるテスト。`{paths}` の雛形へ対象を入れるか、`--round-test` をそのまま走らせる |

## 受け入れ条件

- [ ] AC1: cross-refactoring と supervise のコードに、テストのコマンドの文字列から実行器の名前・差し替えの位置を推測するコードが無い。`testcmd.py` の `runner_index` / `is_known` / `build` / `failed_nodes` / `rerun_command` と、`PYTEST_ADDOPTS` に `--lf` を足して落ちたテストだけを走らせ直す処理が無い（`grep` で確かめる）。コマンドへの加工は `{paths}` の置き換えだけである
- [ ] AC2: ai-plugins で、宣言（`.ndf/project.json`）だけを入力に cross-refactoring の `init` → `plan` → 最初の項目の範囲テストまでが止まらずに通り、戦略が `local-full` になる。範囲テストは `scope_command` の `{paths}` を計画の `test_targets` に置き換えたコマンドで走る
- [ ] AC3: carmo-system-console で、#1333 の解析で作った宣言を置き、cross-refactoring の `init` → `plan` → 最初の項目の範囲テストまでが止まらずに通り、戦略が `local-scoped-ci-whole` になる。範囲テストはコンテナ越し（`docker compose exec -T app ... {paths}`）で走り、全体テストは手元で走らない（着手前も最終ゲートも）。`--round-test` を渡さなくてよい
- [ ] AC4: 宣言に `test` が無い（か不明の）リポジトリで、引数も無く cross-refactoring の `init` を打つと、ai-plugins の値（`uv run ... pytest`）で埋めずに、欠けた宣言のキーと直し方を出して止まる。`--round-test` を渡すと `round-only` として進む
- [ ] AC5: 同じ宣言で、範囲テストのコマンドの前置きだけを変えた 4 通り（前置き無し・`env X=1`・`uv run`・`docker compose exec -T app`）を与えると、選ぶ戦略・範囲テストへ入る対象のパス・落ちたテストの見分けの結果が 4 通りとも同じになる（単体テストで確かめる）
- [ ] AC6: 全体テストが落ちたときの見分け（フレーキー・既存失敗・変更起因）が、pytest と PHPUnit の両方で、JUnit XML から落ちたテストを取り出して働く。JUnit が無いときは、全体の走らせ直しに落としたことが結果に出る
- [ ] AC7: supervise の `new check` と実装・検査の雛形が、cross-refactoring と同じ関数で戦略と範囲テストのコマンドを決める。carmo-system-console の宣言で `new check` を作ると、リファクタリングのステップが `--round-test` 無しで `init` を通る。範囲テスト・全体テスト・CI の待ちの打ち切りが 900 / 1800 / 3600 秒の固定でなく、宣言の所要と予算から出た値になり、その値が計画に書かれる
- [ ] AC8: 全体テストを CI に任せる戦略で危険フラグ（D1〜D5）が立っても、項目ごとに CI を回さず、最終ゲートの 1 回へ寄せる。寄せたこと・どの項目で立ったかが計画と結果に残る。最終ゲートの CI が落ちて変更起因と分かったときは、今と同じく危険フラグの項目を新しい順に取り消す
- [ ] AC9: 予算（`--budget-minutes`）からの時間の算出が、宣言の全体テストの所要と CI の待ちを使う。carmo-system-console の宣言で `--budget-minutes 30` を渡しても、着手前のテストの上限（予算の 0.10 倍 = 180 秒）で止まらない。予備の時間に、手元で走らせない全体テストの時間を引かない
- [ ] AC10: 着手前のテストが落ちていても `init` は止まらず、落ちたテストを既存失敗として状態ファイルと計画へ書く。最終ゲートでは、既存失敗の外で新しく落ちたテストが無ければ通る
- [ ] AC11: 危険フラグ D3 が PHP の `use App\Services\UserService;` の形の import に当たる。D4 の範囲の起点を、テストのコマンドの文字列（`docker compose exec -T app ...` の `app`）から読まない。調査の `/tmp/ndfprobe` と同じ形の仮のリポジトリで、D3・D4 が立つ
- [ ] AC12: テストを本体と同じ場所に置く構成（`src/**/*.spec.ts`、テストのディレクトリが無い）で、`--scope src` を渡した `init` が止まらない
- [ ] AC13: ai-plugins で、今の振る舞いが変わらない。`uv run --frozen --project . --all-extras pytest . -q -n 4` が通り、ai-plugins の既存の宣言（`.ndf/supervise.json`・`.ndf/project.json`）を書き換えずに AC2 が通る（宣言に戦略のキーを足す場合は、足した差分だけが出る）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | CI に任せる戦略で CI が時間内に終わらない・`gh` が使えないときは、止まった理由と待った時間を結果に出し、「判断が要る」で終える。黙って成功にしない |
| 性能・拡張性 | carmo-system-console で `--budget-minutes 30` の 1 回の実行が、全体テストを手元で走らせないことで予算の中に収まる。戦略の選択は決定論で、LLM を呼ばない |
| 運用・保守性 | 選んだ戦略・その根拠（宣言のどのキーか）・範囲テストのコマンド・打ち切りの時間・危険フラグの全体テストを寄せたかが、計画と結果に残る。戦略を足すときに、コマンドの文字列の解析を足さなくてよい |
| 移行性 | 廃止する引数・キーは、今の廃止の扱い（知らせて無視する）に合わせる。ai-plugins の既存の宣言と履歴（配分テーブル）を読める |
| セキュリティ | テストのコマンドは宣言か引数から受け、シェルへ渡す形は今と変えない。宣言の値を LLM が書き換えない |
| システム環境 | 4 ランタイムから同じに呼べる。戦略の選択と JUnit の読み取りは Python 3 標準で動く |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。cross-refactoring の `--baseline-test` / `--round-test` の意味（`{paths}` の雛形として受ける）、計画と結果の JSON に戦略・既存失敗の欄が加わる。supervise の雛形の打ち切りが宣言から決まる |
| データ | `.ndf/project.json` に戦略のキーが加わる場合がある（schema も）。状態ファイルに既存失敗が加わる |
| 既存の振る舞い | pytest 以外のプロジェクトで `init` が止まらなくなる。着手前が red でも進む。CI に任せる戦略では手元で全体テストを走らせない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`。仮のリポジトリ（pytest・PHPUnit 風・同じ場所に置くテスト・宣言無し）と、前置き 4 通り・JUnit XML の有無・red の着手前を与える |
| 静的解析・構造 | `python3 scripts/check-script-structure.py`、`bash scripts/build-runtime-plugins.sh --check`、`claude plugin validate .` |
| 実測 | ai-plugins と carmo-system-console で cross-refactoring の `init` → 最初の範囲テストまでを走らせる（AC2・AC3）。carmo では supervise の `new check` も作って通す（AC7）。コマンド・選んだ戦略・所要を課題か Pull Request に残す |
| 手動確認 | 無し（すべてコマンドの出力とファイルで判定する） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 安定版の置き場（`skills/cross-refactoring/scripts/`・`scripts/supervise_lib/`・`scripts/lib/`）。既定で働くので実験版には置かない（`AGENTS.md` の「安定版と実験版」） |
| コーディング規約 | `AGENTS.md`、`plugins/ndf/skills/AUTHORING.md`。宣言は `lib/project_decl.py` を通して読む。cross-refactoring と supervise で同じ役割の関数を分けない |
| テスト戦略 | 戦略の選択・`{paths}` の置き換え・JUnit の読み取り・時間の算出は単体テストで固定する。carmo の実測は受け入れ条件の確認として 1 回行い、テストにしない。`.md` の文言テストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 選んだ戦略とその根拠を出力に出す。宣言に無い値を ai-plugins の値で埋めない。既存失敗を記録する |
| 確認してから行う | `.ndf/project.json` の schema を広げること（#1333 の読み手へ影響する）。cross-refactoring の引数の意味を変えること |
| 行わない | テストのコマンドの文字列を解析する処理を残す・足す。サンプルへのコミット。サンプルの CI の書き換え。実装担当の push |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 戦略の一覧の確定と、戦略を宣言に直に書くか P2〜P5 から導くか（導くなら閾値） | 設計（`design`） | 設計 PR |
| `.ndf/supervise.json` の `test.command` と `.ndf/project.json` の `test.suites[].scope_command` のどちらを正にするか。複数の suite（carmo-contractors-app の Jest と Vitest）を持つときの範囲テストの選び方 | 設計 | 設計 PR |
| JUnit XML の出し方（実行器ごとの引数を宣言に持つか）と、CI の JUnit の取り方（成果物の名前） | 設計 | 設計 PR |
| 予算の算出に CI の待ちを入れる式と、`test_duration` の 3 つの出所（`ndf-record` / `ci-junit` / `ci-steps`）のどれを使うか | 設計 | 設計 PR |
| 最終ゲートを CI に任せるとき、cross-refactoring がいつ push して CI を待つか（今の「公開はオーケストレーターだけ」の流れのどこへ入れるか） | 設計 | 設計 PR |
| 既存失敗がフレーキーで通ったとき・既存失敗の数が変わったときの扱い | 設計 | 設計 PR |
| `ci-only` を今回実装するか、一覧に置くだけにするか（サンプル 4 つに当てはまる例があるか） | 設計 | 設計 PR |

関連: #1333（戦略を決める解析。マージ済み）、#1312（この課題へ吸収して閉じた）、`issues/genericity-survey-2026-09-27.md` の G2
