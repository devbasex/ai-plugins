# #2711 の設計: データ構造と入出力の契約

設計文書の頭のファイルは [issue-2711-design.md](issue-2711-design.md) にある。 `F` で始まる番号は頭のファイルの機能一覧を指す。 `Stopped` と `SlotConfigError` は頭のファイルの「構造」にある。

## データ構造

枠の置き場は `NDF_TEST_SLOTS_DIR` → `${XDG_STATE_HOME}/ndf/test-slots` → `~/.local/state/ndf/test-slots` の順に決まる。

```text
<枠の置き場>/
├── slot-0.lock  slot-1.lock …   # 枠 1 つにつき 1 つ。消さない
├── slot-0.json  slot-1.json …   # 今の持ち主の記述
├── stop.json                    # 最後にメモリで止めた時刻（.lock で守る）
└── events.jsonl                 # 枠の記録（.lock で守る）
```

持ち主の記述（`slot-<番号>.json`）:

| 項目 | 型 | 空 | 内容 |
| --- | --- | --- | --- |
| `started_at` | 数 | 可 | テストを始めた epoch 秒。メモリを待つ間は `null` |
| `worktree` | 文字列 | 不可 | 実行の作業ディレクトリ |
| `pid` | 数 | 不可 | 持ち主のプロセス番号。読むのは人だけで、判定には使わない（PID 名前空間が違うと通じないため） |

記録の 1 行（`events.jsonl`）。重い処理 1 本の終わりに 1 行を足す。

| 項目 | 型 | 空 | 内容 |
| --- | --- | --- | --- |
| `at` | 文字列 | 不可 | 終わった時刻（UTC の ISO 8601） |
| `worktree` | 文字列 | 不可 | 実行の作業ディレクトリ |
| `kind` | 文字列 | 不可 | `whole` / `scope` / `direct`（判定の結果） |
| `waited_seconds` | 数 | 不可 | 枠とメモリを待った秒 |
| `available_mib` | 数 | 可 | 終わりか止めた時の使えるメモリ。測れなければ `null` |
| `measured` | 真偽 | 不可 | メモリを測れたか |
| `oom_kill` | 数 | 可 | その時点の OOM Killer の回数 |
| `result` | 文字列 | 不可 | `finished` / `gave_up` / `memory_stopped` / `interrupted` |

CRUD:

| 機能 | 枠のロック | 持ち主の記述 | `stop.json` | 記録 |
| --- | --- | --- | --- | --- |
| F1・F2 | 作る・消す（解除） | 作る・更新・消す | — | 作る |
| F3 | 読む | 読む | 読む・更新 | 作る |
| F6 | — | — | — | 読む |

## 入出力の契約

### 設定 `.ndf/resources.json`

| キー | 型 | 既定 | 誤りの扱い |
| --- | --- | --- | --- |
| `test_slots.max_runs` | 1 以上の整数 | 次の式 | 0 以下・数でないと `SlotConfigError` |
| `test_slots.memory_floor_mib` | 1 以上の整数 | 次の式 | 同上 |
| `test_slots.wait_limit_seconds` | 1 以上の整数 | 1800 | 同上 |

```text
max_runs の既定         = max(1, min(CPU の数 // 4, 全体のメモリ // 14 GiB))
memory_floor_mib の既定 = max(2 GiB, 全体のメモリの 10%)
```

`max_runs` が N の実行は `slot-0` から `slot-<N-1>` までの枠だけを取る。同じ置き場で値が違っても開始前に拒否せず、調停もしない（決定 14）。

CPU の数は cgroup の上限があればその値、全体のメモリは cgroup の上限があれば物理メモリとの小さい方を使う。設定の読み取りは `decl.setting_path` の順（呼ばれた木 → メインディレクトリ）に従う。設定ファイルを書き換えるコマンドは作らない。

### `test-slot.py run`

| 項目 | 内容 |
| --- | --- |
| 入力 | `test-slot.py run [--wait-limit <秒>] -- <シェルのコマンド 1 つ>`。作業ディレクトリは呼ばれた位置 |
| 出力（走った） | コマンドの標準出力と標準エラーをそのまま流し、コマンドの終了コードを返す |
| 失敗の形 | 諦めた・メモリで止めた: 標準エラーへ共通の結果の JSON（`status: stopped`、`summary` に待った秒と理由）、終了コード 2。設定の誤り: 同じ JSON で `items` に直す箇所を載せ、走らせずに終了コード 2 |
| シグナル | SIGTERM・SIGINT・SIGHUP を受けるとプロセスグループを止めて枠を返し、記録に `interrupted` を残す |
| 互換性 | 新設 |

### `test-slot.py report`

| 項目 | 内容 |
| --- | --- |
| 入力 | `test-slot.py report [--since <7d のような期間>]`。既定は 7 日 |
| 出力 | 共通の結果の JSON。`metrics` に `waited_runs`・`waited_seconds`・`memory_stopped`・`gave_up`・`oom_kill_increase` |
| 失敗の形 | 記録が無ければ 0 件で終了コード 0。期間の誤りは終了コード 2 |
| 互換性 | 新設 |

`oom_kill_increase` は、期間の中の行と今の回数を時刻の順に並べ、隣どうしの差のうち正の値を足した値である。再起動で回数が減っても負にならない。

### `test-run.py` の結果

| 項目 | 内容 |
| --- | --- |
| 追加 | `metrics.waited_seconds`（`scope` と `whole`） |
| 待ちの諦めとメモリの停止 | `Stopped` と `SlotConfigError` を受けて `status: stopped`・終了コード 2。`summary` に待った秒と理由、設定の誤りなら直す箇所を載せる |
| 互換性 | 既存のキーと項目は変えない。通ると 0、落ちると 1 を保つ |

### hook の拒否

Bash の重い処理に `permissionDecision: deny` を返す。理由の文に、次の形のそのまま打てるコマンドと、待ちが長いときは Bash の上限を延ばすことを載せる。

```text
python3 <プラグインの根>/scripts/test-slot.py run -- '<元のコマンド>'
```

`test-slot.py` と `test-run.py` を含むコマンド、枠を持つ実行の中（`NDF_TEST_SLOT_HELD` がある）、宣言が読めないときは通す。

### 重さの判定

`test_weight.is_heavy(words_by_command, suites)` は次のどれかで真を返す。`suites` は宣言の種別がテストの suite だけを渡す。

| # | 条件 | 例 |
| --- | --- | --- |
| 1 | 文字列が宣言の `command` と一致するか、単純コマンドの語の並びが宣言の `command` の最後の単純コマンドと一致する | `npm --prefix factory run check` |
| 2 | 宣言の `runner` がコマンドの位置にあり、並列の指定を持つ | `python3 -m pytest a.py -n 3` |
| 3 | 宣言の `runner` がコマンドの位置にあり、対象が無いか `.` だけである | `uv run pytest -q` |

- コマンドの位置は、ラッパー（`env`・`timeout` など）と `uv run`・`python -m`・`npx` の後ろの語、またはパスの最後の要素が `runner` と一致する語を指す
- 並列の指定は `-n`・`--numprocesses`・`-j`・`--jobs`・`--parallel`・`--maxWorkers` の表を `test_weight.py` だけが持つ
- 語の並びは `lib/shparse.py` の構文木から作る。語の並び（`list`）で渡されたコマンドはそのまま読む
