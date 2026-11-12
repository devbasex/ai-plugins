# supervise の負荷の記録: 進捗ログに負荷とメモリを残し、queue の結果にキューで待った秒を理由ごとに残す

## 概要

**supervise の進捗ログ（`progress.jsonl`）の `alive` と `step` の行は、マシンの負荷とメモリの値とマシンの上の pytest の数を
`host` に持つ。** **`supervise.py queue` の結果の JSON は、この queue で流したプランの項目に、キューで待った秒を 5 つの理由
ごとに `waits` で持つ。** 全体テストを同時に流したときのメモリの山は、実験版の `whole-test-timing.py peak` で測る。

これらは並列の上限と並べ方を、測った値で決め直すための記録である。上限の値（プランの本数の合計の上限 6・queue の `--max`
の既定）は変えていない。測ったメモリの山は [ndf-parallel-organizer.md](ndf-parallel-organizer.md) の「上限の根拠の実測」にある。

例（`alive` の行）:

```json
{"kind": "alive", "at": "2026-11-11T12:00:00+09:00", "step": "test", "type": "run", "elapsed": 612.4, "worker": "無し", "host": {"load": [5.35, 5.61, 5.84], "mem_available_mib": 17971, "swap_total_mib": 8192, "swap_free_mib": 313, "pytest": 2, "pytest_workers": 8}}
```

例（queue の結果のプランの項目）:

```json
{"plan": ".ndf/sprint-1/2-impl.json", "result": "完了", "exit": 0, "seconds": 1210.3, "overlap": [{"with": ".ndf/sprint-1/1-impl.json", "paths": ["a.py", "a.py"]}], "waits": {"overlap": 905.2, "plans": 0.0, "resource": 3.1, "deps": 0.0, "plan_limit": 0.0}}
```

## 用語

「負荷とメモリの値」「マシンの上の pytest の数」「待ちの理由」「待ちの秒」「メモリの山」の定義は用語集（[docs/glossary.md](../glossary.md)）が正である。

## 仕様

### 常に成り立つ条件

| 条件 | 破れたときの扱い |
| --- | --- |
| `alive` と `step` の行は、負荷とメモリの値を読めなくても書かれ、既存の項目（`step`・`type`・`elapsed`・`worker`・`last_output`、`step` の行の `exit`・`seconds` ほか）を保つ | 読み取りの失敗は `host` の鍵の省略に変え、行の書き込みを止めない |
| `host` は読めた値だけを持つ。読めなかった値を 0 や空で埋めない。すべて読めなければ `host` ごと省く | テストが落とす |
| この queue で流したプランの項目は、`waits` に 5 つの理由の鍵をすべて持ち、値は 0 以上の秒である。待たなかった理由は 0 | テストが落とす |
| この queue で流さなかったプラン（前の実行で完了か承認ゲートの記録があるもの・前提のプランで止めたもの）の項目は `waits` を持たない | テストが落とす |
| 待ちの秒は耐久の記録の時刻から導き、同じ queue を打ち直しても同じ値を返す | 耐久ステップの記録を返す |
| メモリの山の計測は、ほかの supervise の queue と pytest が動いていないときだけ始め、途中で MemAvailable が下限を割ったら残りの本数を流さない | 始める前に断る（終了コード 3）。割ったら動いている全体テストを止める（終了コード 1） |

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| [`lib/procs.py`](../../plugins/ndf/scripts/lib/procs.py) の `host_sample` | `/proc/loadavg` と `/proc/meminfo` を直に読み、psutil でプロセスの一覧を 1 回だけ回して、負荷とメモリの値と pytest の数を 1 つの辞書で返す。読めない値の鍵を省く。読み取り元のパスを引数で受ける |
| `supervise_lib/state.py` の `RunState.progress_write` | `kind` が `alive` か `step` の行へ、`host_sample` の結果を `host` として足す |
| `lib/durable.py` の `times` | 耐久ワークフローの ID の接頭辞から、作った時刻・最後に耐久キューから出した時刻・耐久キューの名前を返す。DBOS を読むのはこのモジュールだけ |
| [`supervise_lib/queue_waits.py`](../../plugins/ndf/scripts/supervise_lib/queue_waits.py) | 待ちの理由の名前（`REASONS`）を持ち、プランごとの待ちの秒を耐久ステップ 1 つで導く |
| `supervise_lib/flow.py` の `run_stage` | 結果を集めた後に待ちの秒の耐久ステップを呼び、流したプランの項目へ `waits` を足す |
| [`scripts/experimental/whole-test-timing.py`](../../plugins/ndf/scripts/experimental/whole-test-timing.py) の `peak`（実験版） | 用意した worktree で全体テストを同時に流し、メモリの山と所要の秒を出す |

### 決定と理由

| 決定 | 理由 |
| --- | --- |
| 値は書き手（`progress_write`）で `kind` を見て足す | `alive` の行は 1 か所、`step` の行は 2 か所で作られる。作る側ごとに足すと、後から行を書く経路が増えたときに漏れる |
| 値は `host` の入れ子に置き、読めない値は鍵ごと省く | 平らに足すと既存の鍵と名前がぶつかる余地が増え、どれがマシンの値かも読めない。0 で埋めると「メモリが 0」と読まれ、集計を誤らせる |
| load average とメモリは psutil を使わず `/proc` のファイルを直に読む | 読み取り元を差し替えて試すためである。psutil の `getloadavg()` は Linux で `os.getloadavg` を呼び、`psutil.PROCFS_PATH` を差し替えても実際の値を返す。`procs.py` は `/proc/pressure/memory` と `/proc/vmstat` も同じ形で読む |
| メモリの余裕は MemAvailable（「スワップせずに新しいアプリケーションを始めるのに使える量の見積もり」。`proc_meminfo(5)`）、スワップは SwapTotal と SwapFree で、kB を 1,024 で割って切り捨てた MiB で残す。名前は Linux の項目名に寄せ、OpenTelemetry のシステムの計測値の名前には寄せない | 読み手は Linux の値の意味で読む |
| pytest の本体は命令行の形で見分け、xdist の作業プロセスは本体の直下の子として別に数える | 命令行に `pytest` を含むかで数えると、`uv run` の親と命令を文字列で持つシェルを重ねて数え、命令行に `pytest` を含まない作業プロセスを落とす |
| 待ちの秒は DBOS の耐久の記録の `created_at`・`dequeued_at` から導き、耐久ステップ 1 つにまとめる | 待ちの始まりと終わりをワークフローの中で書かずに済む。ワークフローの中で時刻を読むと、打ち直しで流し直されて値が変わる。理由の分け方は Slurm の待ちの理由（資源・前提・枠の空き・上限）と同じである |
| 流したプランは 5 つの理由をすべて書き、流さなかったプランは `waits` を持たない | 集計で鍵の有無を確かめずに和と平均が取れる。0 と「測っていない」を区別する |
| 計画の上限の待ちの名前 `plan_limit` を `REASONS` に置き、今は 0 を書く | 計画の上限による待ちを導くときに、記録の形を変えずに導き方を 1 行足せる |
| メモリの山の計測は実験版の副命令にし、別々の worktree を引数で受ける | 既定の工程から呼ばない手動の計測で、全体テストの宣言の読み取りを共有できる。同じ worktree で同時に流すと junit の出力が衝突し、レーンごとに別の worktree という実際の並列とも条件が違う。worktree の用意は `ndf:worktree` が行う |
| 打ち切る MemAvailable の下限は 2,048 MiB | スワップの空きが 300 MiB 台のマシンではスワップが山を吸収できない。同じマシンのほかのプロセスの揺れの予備 1,024 MiB と、測る間隔 1 秒の間に増えうる分 1,024 MiB の和である |
| ほかの queue と pytest が動いていれば計測を始めない | 山にほかの作業の分が混ざり、本数ごとの差が読めない。始める前の使用中のメモリとスワップの使用量も出力に載せ、山との差を読めるようにする |

## データ・設定

### 進捗ログの `host`

| 項目 | 型 | 単位 | 出所 |
| --- | --- | --- | --- |
| `load` | 3 つの数の配列 | — | `/proc/loadavg` の先頭の 3 つ（1・5・15 分） |
| `mem_available_mib` | 整数 | MiB | `/proc/meminfo` の MemAvailable |
| `swap_total_mib` / `swap_free_mib` | 整数 | MiB | 同 SwapTotal / SwapFree |
| `pytest` | 整数 | 本 | 実行ファイルの名前が `pytest` か `py.test` のもの、または Python のインタプリタで最初の引数が `pytest` か `-m pytest` のもの |
| `pytest_workers` | 整数 | 本 | pytest の本体の直下の子のうち Python のインタプリタのもの |

数えられる pytest は、supervise と同じ PID の名前空間から見えるものに限る。同じマシンの別のコンテナの pytest は数えない。
load average とメモリはコンテナの中からもマシン全体の値が見える。

### queue の結果の `waits`

| 理由 | 何の秒か |
| --- | --- |
| `overlap` | 重なりの組の中で、前の員が終わるのを待った秒 |
| `plans` | 耐久キュー `plans` の空き（`--max`）を待った秒 |
| `resource` | 資源の枠の耐久キュー `res-<タグ>` の空きを待った秒の、ステップすべての和 |
| `deps` | 同じステージの前提のプランの終わりを `wait_workflow` で待った秒 |
| `plan_limit` | 計画の上限を待った秒。導き方はまだ無く、常に 0 |

値は小数 1 桁の秒である。負の値（時計の誤差）と、耐久キューを通らなかった待ちは 0 にする。耐久キューの見回りで出るまでの
遅れ（`PICKUP_SECONDS` = 2.0 秒以下）は、`plans` と `resource` では待たなかったとみなして 0 にする。

| 理由 | 直に入れたプラン | 重なりか前提の員を持つ組（`group_workflow`） | 前提のプランを待つ組（`wait_workflow`） |
| --- | --- | --- | --- |
| `plans` | プランの `dequeued_at` − `created_at` | 組の `dequeued_at` − `created_at` | 同左 |
| `overlap` | 0 | 員の `created_at` − 組の `dequeued_at` | 同左 |
| `deps` | 0 | 0 | 組の `created_at` − `wait_workflow` の `created_at` |
| `resource` | ID が `<プランの ID>-t` で始まり、耐久キューの名前が `res-` で始まる耐久ワークフローの `dequeued_at` − `created_at` の和 | 同左 | 同左 |

### `whole-test-timing.py peak`

```bash
python3 plugins/ndf/scripts/experimental/whole-test-timing.py peak --root <dir> [--root <dir>]... [--floor-mib N] [--interval S]
```

| 項目 | 決まり |
| --- | --- |
| 入力 | `--root`: 全体テストを流す worktree（1〜3 個。並べた数が最大の本数）。`--floor-mib`: MemAvailable の下限（既定 2,048）。`--interval`: 測る間隔の秒（既定 1） |
| 処理 | 本数 1・2・…の順に、`.ndf/project.json` の `test.suites` の `command` を先頭から k 個の worktree で同時に起動し、すべてが終わるまで `host_sample` を間隔ごとに読む |
| 出力 | `lib/step_result.py` の形の 1 行の JSON（`tool: "whole-test-timing"`）。`metrics` に計測の日・CPU 数・MemTotal・NDF の版・始める前の使用中のメモリとスワップの使用量。`items` に本数ごとの 1 件（`runs`・`seconds`・`peak_used_mib`・`peak_swap_used_mib`・`min_mem_available_mib`・各 worktree の終了コード） |
| 打ち切り | 下限を割った本数の項目に `aborted: true` と、その時点の `mem_available_mib`・`swap_free_mib` を載せ、残りの本数の項目を作らない。`status` は `stopped` |
| 終了コード | 0 測れた / 1 下限を割って打ち切った / 2 引数の誤り / 3 ほかの supervise の queue か pytest が動いている |

## 運用

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| 落ちた後に続けた queue の待ちの秒 | `dequeued_at` は最後に耐久キューから出した時刻である。落ちた後の起動で耐久ワークフローが耐久キューへ戻ると、待ちの秒に止まっていた時間が混ざるかは未確認 |
| 1 秒の間のメモリの増え方 | 下限 2,048 MiB のうち間隔の分の 1,024 MiB は見込みである。`peak` の出力の最小の MemAvailable から後で確かめられる |
| 計画の上限の待ち | `plan_limit` は常に 0 で、計画の上限で待った秒はまだ数えていない |

## テスト観点

- 読み取り元を偽のファイルにした `host_sample` の値が、`alive` の行の `host` に載ること（load の 3 つ・MemAvailable・スワップの合計と空き。kB から MiB への換算を含む）
- 読み取り元を存在しないパスにすると、`alive` の行が `host` の無いまま書かれ、例外を上げないこと。片方のファイルだけ読めるときは読めた鍵だけが載ること
- `step` の行（ステップの終わりと「実行条件」の行）に、`alive` と同じ `host` が載ること
- pytest を 1 本（xdist で作業プロセス 2 本）動かしている間、`pytest` が 1 以上、`pytest_workers` が 2 以上で、`uv run` の親とシェルを本体に数えないこと
- 流したプランの `waits` が 5 つの鍵をすべて持ち、値が 0 以上であること。`plan_limit` が 0 であること
- 同じファイルを触る 2 本を `--max 2` で流すと、後のプランの `overlap` が前のプランの流れていた秒に近く、`plans` が 0 であること
- 資源の枠 1 本のタグを持つステップの 2 本を同時に流すと、後のプランの `resource` が前のステップの流れていた秒に近いこと
- 流さなかったプランの項目に `waits` が無いこと。同じ queue を打ち直すと `waits` が同じ値であること
- `peak` が、ほかの queue か pytest が動いていると終了コード 3 で返り、全体テストを起動しないこと。下限を割ると動いている全体テストを止め、残りの本数を流さず `aborted` を載せること
- プロセスを 300 本以上見せた `host_sample` が 1 秒以内に返ること
- `alive` の行の既存の鍵が残ること

テストは `plugins/ndf/scripts/tests/test_progress_host.py`・`test_queue_waits.py`・`test_whole_test_timing.py` にある。

## 関連リンク

- [ndf-parallel-organizer.md](ndf-parallel-organizer.md) — 上限の根拠の実測
- [ndf-whole-test-duration.md](ndf-whole-test-duration.md) — `whole-test-timing.py` の副命令
- `plugins/ndf/skills/development-workflow/references/waiting.md` — 進捗ログの行の読み方
- Issue #2998
