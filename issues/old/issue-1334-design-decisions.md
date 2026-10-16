# #1334: テストの戦略 — 決定の記録・テスト設計・未確認

設計の本体は `issues/issue-1334-design.md` にある。この文書は、その設計で選んだ結論と理由、受け入れ条件と不変条件ごとの確かめ方、
確かめられなかったことを持つ。

## 決定の記録

### 決定 1: 戦略は `local-full` / `local-scoped-ci-whole` / `round-only` の 3 つにし、`ci-only` は置かない

サンプル 4 つ（ai-plugins を含めて 5 つ）のどれも、手元でテストが走らない構成ではない（下の表）。当てはまる例の無い値を schema に置くと、
それを前提にした分岐（範囲テストを走らせない検証・静的な検査だけの合否）を、確かめる対象の無いまま持つことになる。
必要になったときは `resolve` の表に 1 行を足せば入る（戦略を足すときに文字列の解析を足さない条件は、この形で満たす）。

| リポジトリ | テストの形（2026-09-27 に調べた） | 選ぶ戦略 |
| --- | --- | --- |
| ai-plugins | pytest。全体は手元で 140 秒、宣言の所要は `ci-steps` 424 秒 | `local-full`（導く） |
| carmo-system-console | PHPUnit をコンテナ越し。CI の JUnit 24 本の合計 3,827 秒、CI は 24 本に分けて約 6 分。成果物 `junit-dlite-*` を `if: always()` で上げる | `local-scoped-ci-whole` |
| carmo-contractors-app | `contractors/` の Vitest と Jest（suite 2 つ）。CI の `react-test.yml` は成果物を上げるが JUnit ではない（推測） | `local-full` |
| project-trygroup-prd | pytest を `./scripts/run-python-tests.sh` で 3 つのディレクトリへ回す。CI に JUnit は無い | `local-full`（`scope_command` を suite ごとに書く）か `round-only`（ラッパーをそのまま） |
| with-ai-dev | サブプロジェクトごとの pytest。CI は無い | `local-full` |

### 決定 2: 戦略は宣言の `test.strategy` を正とし、無ければ同じ関数が所要から導く

戦略は所要だけでは決まらない判断を含む（carmo の「手元で全体の phpunit を回さない方針」）。解析の答え（P2）で conductor が決めて書くのが正で、
`propose` の値を答えの既定として示す。キーが無い宣言（#1333 の直後の宣言・手で書いた宣言）でも止めないよう、同じ `propose` で導き、
根拠を `derived:test_duration` と出す。ai-plugins の宣言を書き換えずに AC2 を通すのはこの経路である。

`resolve` の表（上から順に当てる）:

| 入力 | 戦略 | 根拠 |
| --- | --- | --- |
| `--round-test` が `{paths}` を含まない | `round-only`（ラウンドテスト = その値） | `args` |
| `--round-test` か `--baseline-test` が `{paths}` を含む | 宣言の `test.strategy` があればそれ、無ければ `local-full`（雛形 = その値） | `args` |
| `--baseline-test` だけが `{paths}` を含まない | `round-only`（ラウンドテスト = 全体テスト = その値。項目ごとに全体を走らせると知らせる） | `args` |
| 宣言の `test.strategy` | その値 | `test.strategy` |
| 宣言の suite に `scope_command` が 1 つも無い | `round-only`（ラウンドテスト = suite の `command` ） | `derived:test.suites` |
| 所要 `w` > 600 秒で、宣言の `ci` が読める | `local-scoped-ci-whole` | `derived:test_duration` |
| それ以外 | `local-full` | `derived:test_duration` |
| 宣言の `test` が無い・不明 | 止める（I3） | — |

**所要 `w` の出所は `ndf-record` → `ci-junit` → `ci-steps` の順に採る。** `ndf-record` は手元で走らせた実測で、残り 2 つは CI の直列の合計である。
CI の合計は手元の並列（`-n 4` ）より大きく出るが、戦略を手元の側へ誤って倒さない向きの誤差である。

**境界の 600 秒は、既定の予算 30 分の 1/3 である。** `local-full` は全体テストを着手前・危険フラグ・最終ゲートの最大 3 回走らせるため、
3 回で予算を使い切らない所要の上限になる。予算で戦略を変える形（実行ごとに `w ≤ k·B` を見る）は採らない。同じプロジェクトで
予算を変えるたびに戦略が変わり、履歴と既存失敗の見方が実行ごとに揺れる。

### 決定 3: テストのコマンドの正は `project.json` の `test.suites[]` にし、`supervise.json` の `test.command` / `test.all` を廃止する

同じコマンドが 2 か所にあると、片方だけを直したときに cross-refactoring と supervise が別のテストを走らせる（AC7 の「同じ関数」が成り立たない）。
`project.json` を正にするのは、戦略・所要・CI・コンテナがすべてそこにあり、#1333 の解析が書き直せるためである。
`supervise.json` の `test.command` / `test.all` は、`project.json` に `test` があれば知らせて無視し、無いときだけ 1 つの suite として読む
（移行性）。`test.no_reports` は pytest の成果物を止める今の仕組みのまま残す（この課題の範囲外）。

`supervise.json` を正にする形は採らない。所要・CI・コンテナを持たず、戦略を導けない。

### 決定 4: JUnit はコマンドへ引数を足さず、宣言の `suites[].junit` の置き場から読む

NDF がコマンドへ `--junitxml` や `--log-junit` を足すと、実行器ごとの引数を NDF が知ることになり、文字列の解析をやめる目的に反する（AC1）。
宣言のコマンドに JUnit の引数を書き、書き先を `junit` に書く。コンテナ越しでも、作業ディレクトリからの相対パスならホストから読める。
NDF は走らせる前に置き場のファイルを消し、走らせた後に読む。`git check-ignore` が通らない置き場は、読んだ後に消して知らせる。

`{junit}` の雛形を足して一時ディレクトリを渡す形は採らない。コンテナの中からホストの一時ディレクトリへ書けない。

**pytest は `-o junit_family=xunit1` を宣言のコマンドに書く。** 既定の `xunit2` は `testcase` に `file` を持たない（2026-09-27 に pytest で実測。
`xunit1` は `file="tests/sub/test_a.py"` を持ち、`pytest-xdist` の `-n 2` と併用しても同じ）。`classname` からファイルを推測する処理は置かない。

### 決定 5: 落ちたテストはファイルの単位で走らせ直し、ID は `file::classname::name` にする

範囲テストの雛形が受け取るのはパスであり、実行器ごとのテストの名前の指定（`::name`・`--filter` ）は解析になる。ファイルで走らせ直せば、
pytest と PHPUnit を同じ手順で扱える。ID に `classname` と `name` を含めるのは、同じファイルの中で別のテストが落ちたときに見分けるためである。

`file` が絶対パス（PHPUnit は CI で `/home/runner/work/carmo-system-console/carmo-system-console/tests/...` 、コンテナで `/var/www/html/...` を書く。
CI の成果物で実測）のときは、`git ls-files` の追跡ファイルに末尾が一致する最短の部分へ直す。一致しなければその ID は読めないものとして扱う。

### 決定 6: CI の JUnit は宣言の `test.ci` のチェックと成果物の名前で取り、無ければ必須のチェックと解析の名前の規則を使う

見るチェックを名前で絞るのは、別のチェック（carmo の `codex-review` ）の成否で合否を決めないためである（今の `--ci-check` と同じ理由）。
成果物の取得は #1333 の `measure_ci._junit_of_run` と同じ読み取りを `lib/junit.py` へ移して共有する。`test.ci` が無いときは `ci.required_checks` の
すべてを待ち、成果物は `JUNIT_NAME` の正規表現で選ぶ。

### 決定 7: CI に任せる戦略では、危険フラグの全体テストを最終ゲートの 1 回へ寄せる

項目ごとに CI を回すと、1 回に 6 分以上の待ちが項目の数だけ積み上がり、予算の中に収まらない。検証の中で走らせる全体テストの役割
（範囲テストでは覆えない変更を見る）は、最終ゲートの CI が同じ HEAD で果たす。寄せた項目と旗は `whole_test.deferred` に残し、最終ゲートで
変更起因が出たときは、今の `_fix_or_narrow` と同じく締め切りまで直し、過ぎたら危険フラグの項目を新しい順に取り消す。取り消しの判定は、
変更起因のファイルを手元で走らせ直して行い、取り消した後に push して CI を待ち直す。

最終ゲートで寄せた項目をすべて取り消す形は採らない。変更起因が 1 項目に由来するときに、ほかの項目まで失う。

### 決定 8: 時間の上限は予算 B・所要 w・着手前の実測・CI の壁時計 c からの算術で出す

| 値 | 式 | 今 |
| --- | --- | --- |
| 着手前のテスト 1 回の上限 | `local-full` / `round-only` は `max(0.10·B, 3·w)`、`local-scoped-ci-whole` は `0.10·B`（走らせるのは置き場所の範囲テストだけ） | `0.10·B` |
| テスト 1 回の上限 | `max(3·x, 0.01·B)`。x は着手前に手元で走らせたテストの実測（`local-scoped-ci-whole` は置き場所の範囲テストの秒） | x = 全体テストの実測 |
| CI の待ちの上限 | `max(3·c, 0.05·B)`。c は宣言の `ci.workflows[].wall_seconds` のうち `test.ci.check` を持つもの、分からなければ最大の値。どれも無ければ `0.20·B` | 待たない（1 回読む） |
| バッファの危険フラグの全体テスト | 手元の戦略は w（着手前の実測）、`local-scoped-ci-whole` は 0（寄せる） | w |
| バッファの最終ゲートの全体テスト | 手元の戦略は w、CI で見るとき（戦略か `--ci-check` ）は c | `--ci-check` なら 0 |
| 項目の検証の見積り | `max(配分の verify, 着手前の範囲テストの実測)` | 配分の verify（初期値 10 秒） |

係数（0.10・3・0.01・0.05・0.20）は `timeline.py` の今の係数の置き場に並べ、`lib/test_strategy.limits` が同じ値を使う。**固定の秒は持たない**（今の決定 24）。
最終ゲートの CI の待ちは、予算を使い切った後でも 1 回は上限まで待つ（今の最終ゲート修正の 1 回目と同じ扱い）。

**バッファに CI の待ちを入れる。** 待ちは外で走っていても、同じ実行の壁時計を使う。今の `--ci-check` で 0 にしているのを改める。

supervise は予算を持たない。`test_strategy.limits` を B 無しで呼ぶと、テストは `3·w`、CI は `3·c` を上限にし、所要が不明なら今の値
（範囲 900・全体 1800・CI 3600 秒）を「所要が不明のときの値」として計画に書く。

### 決定 9: CI の待ちは `lib/waits.py` の `wait_until` で、最終ゲートの push の後だけで行う

`merge-when-green` の `GreenWatch` は待ちとマージと draft の解除を 1 つに持つため、マージしない最終ゲートでは使えない。待ちの間隔を
伸ばす仕組みは同じ `wait_until` を使い、手書きの待ちを増やさない（前提 5）。読む相手は今の `github.check_run_result` で、`pending` の間だけ待つ。
push は今どおり公開の手順（オーケストレーター）が行い、最終ゲートは push 済みの HEAD の SHA のチェックを待つ（I9）。

### 決定 10: 共通の関数は `scripts/lib/` に置き、supervise は `test-run.py` から呼ぶ

cross-refactoring は `refactor_lib/__init__.py` が既に `scripts/lib` を import の道に入れている。supervise の run のステップはシェルのコマンドなので、
関数を呼ぶ入口の 1 本（`test-run.py` ）を置く。`PYTEST_ADDOPTS` に `--lf` を足す走らせ直しは、同じ `test_triage.classify` に置き換える。

supervise の run のステップに Python の関数を直に持たせる形は採らない。run のステップは LLM を使わないシェルの実行という約束であり、
雛形ごとに特例を持たせることになる。

### 決定 11: 既存失敗は `init` で 1 度記録し、最終ゲートは既存失敗の外の変更起因だけで落とす

`init` で止めると、既存失敗を抱えたまま運用するプロジェクトでは 1 度も起動できない（前提 7）。既存失敗の扱い:

| 状態 | 扱い |
| --- | --- |
| 既存失敗が最終ゲートで通った | 失敗ではない。報告に「既存失敗が通った」として件数を出す |
| 既存失敗が最終ゲートでまた落ちた | 既存失敗（取り消さない） |
| `init` に無い ID が落ちた | 見分けの 3 で着手前の HEAD でも落ちれば既存失敗、通れば変更起因 |
| `init` で JUnit が無く red だった | 既存失敗を `null` で書く。最終ゲートが red で ID を読めなければ「判断が要る」で終える |

### 決定 12: `--scope` の検査からコマンドの実行集合の判定を外し、本体と同じ場所のテストを置き場所に数える

実行集合の判定（`baseline_search_roots`・`round_test_roots` ）はコマンドの語を読む解析であり、雛形では `{paths}` に置き場所が直に入るため要らない。
`round-only` のラウンドテストが置き場所を走らせるかは確かめられないが、最終ゲートの全体テストが見る（今の「ラッパーの中身は解析しない」と同じ扱い）。
置き場所は、名前（`tests/` など）か、配下の追跡ファイルがテストの名前の形（`*.spec.*` など）に当たるディレクトリにする。

### 決定 13: D3 は区切りに `\` と `::` を足し、D4 の範囲テストのファイルは `--scope` の置き場所から取る

D3 の照合は親と語幹の組で探しており、区切りを足せば言語の表を持たずに PHP の名前空間（`use App\Services\UserService;` ）と Rust のパスに当たる。
`git grep -E 'Services\\UserService'` が PHP の `use` の行に当たることを 2026-09-27 に実測した。D4 の起点を `round-only` のコマンドの語から読むと、
`docker compose exec -T app ...` の `app` を起点と取り、本体の `app/` をテストの側に入れる。`--scope` の置き場所は利用者が与えた範囲で、この読み違いが起きない。

### 決定 14: `{paths}` は空白で区切った 1 語として置き換え、埋め込みを受けない

範囲テストは今どおり語の並び（`shell=False` ）で走らせる。雛形を `shlex.split` で語に分け、`{paths}` の語を対象の語の並びへ差し替える。
1 語でなければ（`--filter={paths}` ）、対象を語の途中へ入れる規則が要り、パスの区切りと引用の扱いを NDF が決めることになるため止める。
全体テストは `{paths}` を `.` にした文字列をシェルで走らせる（今の `with_paths(cmd, ".")` と同じ）。

### 決定 15: ai-plugins の宣言に JUnit の置き場を足すことを、設計の Pull Request で確認する

AC2 と AC13 は宣言を書き換えずに通る（決定 2）。ただし JUnit が無いと、危険フラグと最終ゲートの見分けが全体の走らせ直しへ落ち、今の
pytest の `FAILED` の行による見分けより粗くなる。今の見分けの細かさを保つには、`suites[0]` の `command` と `scope_command` に
`-o junit_family=xunit1 --junitxml=.ndf/tmp/junit.xml` を足し、`junit` に同じパスを書く。キーの追加とコマンドの語の追加であり、
**宣言の書き換えを伴うため、承認を得てから実装で行う**（要求の境界「確認してから行う」）。

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| AC1・I2 | cross-refactoring と supervise の配布物を `grep` し、`runner_index` / `is_known` / `failed_nodes` / `rerun_command` / `--lf` / `KNOWN_RUNNERS` が無い。範囲テストの語は雛形の語から `{paths}` を除いたものを含む | 雛形の語を並べ替える・語を足す処理を戻すと落ちる |
| AC2 | ai-plugins の宣言の写しで `resolve` が `local-full`・根拠 `derived:test_duration` を返し、1 項目の範囲テストの語が `scope_command` の `{paths}` を `test_targets` にしたもの | 所要の出所の順を変えて `ci-steps` 以外を読むようにすると戦略が変わり落ちる |
| AC3・I4 | carmo の形の宣言で、`init`・`verify`（危険フラグ付き）・`final-gate` が全体テストのコマンドを 1 度も起動しない。`--round-test` を渡さない | `local-scoped-ci-whole` で `_local_gate` を呼ぶと落ちる |
| AC4・I3 | 宣言に `test` が無い・`{"unknown": ...}` で、引数無しの `init` が終了コード 4 と欠けたキーを出す。`--round-test` 付きは `round-only` で進む | 既定のコマンドで埋めると落ちる |
| AC5・I1 | 前置き 4 通り（無し・`env X=1`・`uv run`・`docker compose exec -T app` ）の雛形で、戦略・範囲の対象の語・見分けの分類が同じ | 前置きを見て分岐すると落ちる |
| AC6・I6 | pytest（`xunit1` ）と PHPUnit（絶対パスの `file` ）の JUnit で、落ちた ID を読み、HEAD で通る・着手前でも落ちる・HEAD だけ落ちるを 3 つに分ける。JUnit が無いと `fallback_reason` が結果に出る | 出力の文字列から ID を読む・`fallback_reason` を書かないと落ちる |
| AC7・I10 | carmo の形の宣言で `new check` の `refactor` が `--baseline-test` を持たず、`test-limited` / `test-all` / `merge` の `timeout` が `limits` の値で、計画に `テストの時間` がある。cross-refactoring と supervise の範囲テストの語が同じ入力で同じ | 雛形に 900 / 1800 / 3600 を戻す・supervise に別の組み立てを置くと落ちる |
| AC8・I8 | `local-scoped-ci-whole` で危険フラグが立つと検証が CI を待たず `whole_test.deferred` を書く。最終ゲートで変更起因が出ると寄せた項目を新しい順に取り消す | 検証の中で `wait_check` を呼ぶ・全件を取り消すと落ちる |
| AC9・I7 | carmo の宣言（w = 3,827 秒）と B = 30 で、着手前の上限・バッファが決定 8 の式の値になり、バッファに w が入らない | バッファに w を入れると項目が 0 件になり落ちる |
| AC10・I5 | 着手前が red（JUnit 有り）で `init` が進み、既存失敗を状態ファイルと計画に書く。最終ゲートで既存失敗だけが落ちれば通り、ほかが落ちれば落ちる | red で止める・既存失敗で落とすと落ちる |
| AC11 | `/tmp/ndfprobe` と同じ形の仮のリポジトリで、`app/Services/UserService.php` を触った項目に D3 と D4 が立つ | 区切り `\` を外す・D4 をコマンドの語から読むと落ちる |
| AC12 | `src/**/*.spec.ts` だけでテストのディレクトリが無い仮のリポジトリで、`--scope src` の検査が通る | 置き場所をディレクトリの名前だけで数えると落ちる |
| AC13 | `uv run --frozen --project . --all-extras pytest . -q -n 4` が通り、ai-plugins の宣言を書き換えずに AC2 の単体テストが通る | — |
| I9 | 最終ゲートの CI の待ちは公開の手順の push の後だけで、実装担当の結果に push があれば今どおり取り込まない | 待ちの前に push を足すと落ちる |
| 決定 14 | `--filter={paths}` の雛形で `init` と `new` が止まる | 語の途中を置き換えると落ちる |

**carmo の実測は受け入れ条件の確かめとして 1 回行い、テストにしない**（要求の前提とする取り決め）。

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| コンテナのマウント | `docker compose exec -T app` はメインディレクトリをマウントしたコンテナで走り、cross-refactoring の作業ディレクトリ（`<worktree_base>/<repo>/rf<PR>/work` ）の変更を試さない見込みが高い（G4 の「偽の green」）。#1337 が扱う。AC3 の「止まらずに通る」はこの課題で確かめ、範囲テストが作業ディレクトリの変更を試すかは #1337 の後に確かめる |
| carmo の CI のチェックの名前 | `test-results` はジョブの ID で、check-runs の名前が同じかは解析（`ci.required_checks` ）を通すまで分からない。carmo の宣言を作るときに決める |
| JUnit の成果物の保存期間 | 成果物は期限切れになる（`expired` ）。最終ゲートは push 直後の run を読むため当たらない見込みだが、`init` の既存失敗の取り込みは期限切れなら読まない |
| PHPUnit のファイル単位の走らせ直し | ファイルごとにコンテナの起動と bootstrap が掛かる。落ちたファイルが多いときの所要は carmo の実測で見る |
| supervise の範囲テストの上限 | 予算を持たないため `3·w`（carmo は 3 時間を超える）になり、止まったテストを長く待つ。実装の実測で緩すぎれば、範囲の所要を宣言に足すかを別の課題にする |
