# #4068 の詳細設計: 起こす hook と起こす記録

設計文書 [issue-4068-design.md](./issue-4068-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。#3995・lracct（#3604・#3864）・#4049 の実装の後に、その上で実装する（判断開始の値は `context_thresholds.thresholds` の返り、背景の作業の分け方は #4049 の `relay_busy.task_kind`・`work_count`）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── hooks/entry.py
├── src/ndf/domains/
│   ├── hooks/
│   │   ├── registration.py・budget.py
│   │   ├── rewake.py *
│   │   └── guards/turn.py
│   └── relay/
│       ├── keep_cache.py *
│       ├── cli.py・cli_cache.py *
│       ├── signals.py
│       └── wrapper.py
├── contracts/
│   ├── limits.json・schemas/limits.schema.json
│   ├── hooks/runtimes.json
│   ├── domains/ndf-files.json・env.json
│   └── schemas/io/relay.args.schema.json・relay.result.schema.json
├── hooks/claude.json（再生成）
├── docs/contracts/hooks.md・files.md・env.md・commands.md（再生成）
├── skills/development-workflow/references/relay.md
├── src/ndf/contracts/limits.py
├── src/ndf/adapters/cli_floor.py
└── tests/domains/
    ├── hooks/test_rewake.py *・test_registration.py・test_budget.py・test_guards.py
    └── relay/test_keep_cache.py *・test_relay_wrapper.py・test_relay_cli_parse.py
（`cli_floor` のテストは今の置き場の `tests/` の下のファイルに足す）
```

依存の向き: `hooks/rewake.py` → `relay/keep_cache.py`・`relay/signals.py`・`relay/markers.py`・`relay/observe.py`（hook → 中継の向きで、今の `hooks/guards/turn.py` → `relay/markers.py` と同じ）。`keep_cache.py` は標準ライブラリだけで書き、判定を純粋な関数に閉じる。`cli_cache.py` は `keep_cache.py` と `signals` を使い、費用は行に書かれた倍率で出す。

| ファイル | 種別 | 責務（1 文） | 依存 | 公開シンボル | 関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/keep_cache.py` | 新設 | 起こす時点・上限・次の手を決め、会話の記録の最後の応答を読む | 標準ライブラリ・`relay.agents`（末尾の読み取り） | `LastTurn`・`TurnRead`・`Reply`・`Ratios`・`Step`・モジュール `keep_cache` | `last_turn`・`wake_reply`・`is_wake_input`・`ratios`・`wake_at`・`n_max`・`next_step`・`hit`（新設） |
| `src/ndf/domains/hooks/rewake.py` | 新設 | Stop の背景で起動し、今回の応答を確かめて世代を書き、眠って確かめ直し、起こすなら終了コード 2 で終わる | `keep_cache`・`signals`・`markers`・`hooks.tables`・`relay_busy` | モジュール `rewake` | `run`・`_target`・`_settle`・`_wait`・`_finish`（新設） |
| `src/ndf/domains/hooks/guards/turn.py` | 変更 | 中継の下の UserPromptSubmit で起こす状態の世代を入力の時刻に書き換え、起こす文の入力を中継の入力と見分ける（決定 10 の実測で決めた形） | `signals`・`keep_cache` | — | `check`（変更） |
| `hooks/entry.py` | 変更 | `argv[3]` が `rewake` なら `rewake.run` の終了コードを返す | — | — | `main`（変更） |
| `src/ndf/domains/hooks/registration.py` | 変更 | 事象の行に `async_rewake` があれば、その事象へ 2 つ目の登録（`asyncRewake: true`・`timeout` は背景の予算から）を足す | `hooks.tables`・`hooks.budget` | — | `render`（変更）・`rewake_entry`（新設） |
| `src/ndf/domains/hooks/budget.py` | 変更 | 背景の登録を `async_rewake` の予算と上限で検査し、同期の登録は今の予算と上限で検査する | `hooks.tables` | — | `rewake_budget`（新設）・`budget_registrations`・`_file_registrations`・`_registration_errors`（変更） |
| `src/ndf/adapters/cli_floor.py` | 変更 | `require` が下限の確かめに使った版を返す | 標準ライブラリ・`semver` | — | `require`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 起こす状態（会話ごと）と起こす記録の読み書き。世代は新しい時刻でだけ書き換える | 標準ライブラリ | — | `read_keep`・`bump_generation`・`update_keep`・`append_cache_log`（新設）、定数 `KEEP_PREFIX`・`CACHE_LOG`・`REWAKE_ENV`・`SESSION_ID_RE` |
| `src/ndf/domains/relay/wrapper.py` | 変更 | `_start` の版の確かめ（`cli_floor.require`）の版が足りれば子へ `NDF_RELAY_REWAKE=1` を渡し、足りなければ受け継いだ変数を消す | `signals`・`adapters.cli_floor` | `Wrapper` | `_start`（変更） |
| `src/ndf/domains/relay/cli_cache.py` | 新設 | `ndf relay cache report` | `keep_cache`・`signals` | モジュール `cli_cache` | `report`・`run`（新設） |
| `src/ndf/domains/relay/cli.py` | 変更 | サブコマンド `cache report` を足す | `cli_cache` | — | `build_parser`（変更） |
| `contracts/limits.json`・`contracts/schemas/limits.schema.json`・`src/ndf/contracts/limits.py` | 変更 | `cache.wake`・`cache.write-ratio`・`cache.read-ratio`・`cache.output-ratio`・`cache.wake-cost`・`cache.hit`・`cache.rewake`・`hook.budget.stop-rewake` を足し、スキーマと `Policy` に `ratios`・`default`・`ratio`・`version` の項目を足す | — | `Policy` | `text`（新設）・`value`（数でない値で `LimitsError`） |
| `contracts/hooks/runtimes.json` | 変更 | claude の Stop に `async_rewake`（予算 `stop-rewake`・背景の登録だけの `timeout_max` 3,300）を足す | — | — | — |
| `contracts/domains/ndf-files.json`・`env.json` | 変更 | `relay-keep`（`<名前>/keep-<会話>.json`）・`relay-cache-log`（`<名前>/cache-log.jsonl`）と `NDF_RELAY_REWAKE` の行。どちらもラッパーの保存先と同じ掃除の対象 | — | — | — |
| `contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` | 変更 | `cache report` の引数と結果 | — | — | — |
| `hooks/claude.json`・`docs/contracts/*.md` | 変更（再生成） | `ndf hooks render`・`ndf check --write-spec` で作り直す | — | — | — |
| `skills/development-workflow/references/relay.md` | 変更 | hook の一覧に起こす hook、記録の種類に起こす記録、見直しの手順（20 件ごと）を足す | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更（`glossary.md` は再生成） | 要求の用語 7 語と設計の 2 語を足す | — | — | — |


## 判定（`keep_cache`）

純粋な関数で、時刻・値・記録の文字を引数で受ける。

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `last_turn` | `src/ndf/domains/relay/keep_cache.py` | 新設 | `last_turn(tail: str, known_ttl: Optional[str], last_message: Optional[str]) -> TurnRead` | 記録の末尾の最後の `assistant` の行（同じ `message.id` は 1 つ）を読む。その応答の `text` をつないだ文が `last_message` と違う・`last_message` が None なら `TurnRead(None, "unrecorded")`（今回の応答が未記録。一時的）。同じで項目が欠けた・型が違えば、前の行へさかのぼらずに `TurnRead(None, "form")`。そろえば `TurnRead(LastTurn, None)`。寿命は設計の決定 2 の表で決め、両方 0 なら `known_ttl`。時刻はその `message.id` の最後の行の `timestamp` |
| `wake_reply` | 同上 | 新設 | `wake_reply(tail: str, after_id: str) -> Reply` | `after_id` の応答の直後にある最初の入力の行だけを見る。起床の文の形（設計の決定 7 の実測 8 で決めた形を `is_wake_input` に閉じる）なら、その次の応答で `Reply("wake", LastTurn)`。利用者の入力か背景の作業の知らせの形なら、その先を探さずに `Reply("preempted", None)`。どちらとも決められない・`after_id` が見つからないなら `Reply("unknown", None)` |
| `is_wake_input` | 同上 | 新設 | `is_wake_input(line: Mapping[str, Any]) -> Optional[bool]` | 入力の行 1 つが起床の文なら真、通常の入力なら偽、決められなければ None。実測 8 の形だけを知る関数で、実測の前は常に None（版の門も閉じている） |
| `ratios` | 同上 | 新設 | `ratios(get: Callable[[str, str], Any], model: str, ttl: str) -> Ratios` | `get(name, key)` で上限の設定の値を読む（hook は `tables.policy_value`、集計は `limits` の読み手を包んだ関数を渡す）。`cache.write-ratio` の `ratios`（辞書）から `w`、`cache.read-ratio` の `ratios`（辞書。表に無いモデルは `default`）から `r`、`d`・出力の倍率・余白・確かめる間隔・当たりの境。辞書でない・数でない値は `tables.TableError` |
| `wake_at` | 同上 | 新設 | `wake_at(turn: LastTurn, margin: float) -> Optional[float]` | `turn.at + TTL_SECONDS[ttl] − margin`。`margin` が寿命以上なら None（5 分の寿命は起こさない） |
| `n_max` | 同上 | 新設 | `n_max(tokens: int, q: Ratios) -> int` | `floor((w − r) × S / (r × S + d))`。0 未満にしない |
| `hit` | 同上 | 新設 | `hit(turn: LastTurn, before: int, ratio: float) -> bool` | `turn.cache_read >= before × ratio` |
| `next_step` | 同上 | 新設 | `next_step(k: int, limit: int, monitors_only: bool, tokens: int, decide_from: int) -> Step` | `k + 1 > limit` → `stop`。`k + 1 == limit` かつ `monitors_only` かつ `tokens >= decide_from` → `end_wait`。ほかは `wake`。`limit` は待ちの始まりの `S` で決めた状態の `n_max`、`tokens` は判定の時の（今回の応答の）`S` |

### 内部のデータと定数

| 名前 | 種別 | 置き場所 | 値・形 |
| --- | --- | --- | --- |
| `LastTurn` | 型（`NamedTuple`） | `keep_cache.py` | `message_id`・`at`（`timestamp` の UNIX 秒）・`tokens`（入力・読み出し・書き込みの合計）・`ttl`（`1h`・`5m`）・`model`・`cache_read`・`cache_write`・`output` |
| `TurnRead` | 型（`NamedTuple`） | 同上 | `turn`（`LastTurn` か None）・`reason`（`unrecorded`・`form` か None） |
| `Reply` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`preempted`・`unknown`）・`turn` |
| `Ratios` | 型（`NamedTuple`） | 同上 | `w`・`r`・`d`・`out`・`margin`・`interval`・`hit` |
| `Step` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`end_wait`・`stop`）・`reason` |
| `TTL_SECONDS` | 定数 | 同上 | `{"1h": 3600, "5m": 300}`（公式の寿命。値段ではないので `limits.json` に置かない） |
| `WAKE_HEAD` | 定数 | 同上 | `【ndf-relay】`（起こす文と引継ぎへ切り替える文の頭。起きた応答の見分けに使う） |
| `WAKE_TEXT`・`END_WAIT_TEXT` | 定数 | 同上 | 設計の「処理の流れ」の 2 つの文 |

### 異常系の処理

| 関数 | 失敗の種類 | 原因 | 検出する場所 | 検出したときの処理 | 利用者への通知 | 回復の手段 |
| --- | --- | --- | --- | --- | --- | --- |
| `last_turn` | 最後の応答が載っていない | Stop の時点で未記録（PRD3 の版） | `last_message` との文の照らし | `reason` `unrecorded` | 無し（呼び手が `stop_waking`（`scope` `once`）を会話で 1 度書く。止めない） | 次の Stop で読み直す |
| `last_turn` | 形が違う | Claude Code の記録の形の変化 | `json.loads`・項目の型 | `reason` `form` | 無し（呼び手が `stopped` と `stop_waking`（`scope` `session`）を 1 度書く） | 読み方を直す |
| `ratios` | 行が無い・値が数でない | 上限の設定の壊れ | 型の照らし | `tables.TableError` を投げる | 無し（呼び手が起こさない） | 上限の設定を直す（`ndf check` が落とす） |

### 単体テストの観点

| 関数 | 観点 |
| --- | --- |
| `last_turn` | 実測の形の見本を読める。最後の `assistant` の行に `usage` が無い見本では、前の行が完全でも `form`。完全な前の応答だけが残り `last_message` が違う見本では `unrecorded`。`last_message` が None でも `unrecorded`。寿命の表の 4 行 |
| `wake_reply` | 起床の文の後の応答を返す。印の直後の最初の入力が利用者の入力の見本では、その後に起床の文の応答があっても `preempted`。最初の入力の形を決められない見本では `unknown` |
| `n_max` | Opus 5.5・`1h`・`d` 2,000 で `S` 400,000 → 35、500,000 → 36、20,000 → 13。読み出し 0.1 で 400,000 → 18 |
| `wake_at` | `1h`・余白 600 で `at + 3000`、`5m` で None |
| `next_step` | `limit` 35 で `k` 33 → `wake`、`k` 34 → 監視だけ・`tokens` 250,000・`decide_from` 250,000 なら `end_wait`・`tokens` 249,999 なら `wake`、`k` 35 → `stop`。作業の子は `k` 34 でも `wake`。`limit` 0 → `stop`（純粋な関数の境界。`limit` を式から作る組の試験は設計のテスト設計の 5） |

## 起こす hook（`hooks/rewake.py`）

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `run` | `src/ndf/domains/hooks/rewake.py` | 新設 | `run(argv: list[str], stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr, environ: Mapping[str, str] = os.environ, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> int` | 終了コード。起こすなら標準エラーへ文を書いて 2、ほかは 0。例外はすべて 0（Stop を壊さない） |
| `_target` | 同上 | 新設 | `_target(payload: Mapping[str, Any], environ: Mapping[str, str]) -> Optional[Target]` | `NDF_RELAY_REWAKE`・中継の保存先（`NDF_RELAY_DIR`）・起動の ID・会話の ID・`transcript_path` がそろい、入力の `session_id` が #4049 の `signals.relay_session(environ)`（`NDF_RELAY_SESSION`）と同じで、会話の ID が UUID の形（`SESSION_ID_RE`）のときだけ `Target`（`background_tasks` を #4049 の `task_kind` で分けた作業と監視の件数、`last_assistant_message`、起動のたびに作る `hook_id`（`uuid4().hex`）を持つ）。ほかは None（環境を受け継いだ入れ子の claude は `session_id` が違うので通らない） |
| `_settle` | 同上 | 新設 | `_settle(t: Target, turn: LastTurn, tail: str, q_for, clock) -> Settled` | 子を止める排他を取り、その中で次を 1 つの区間で行う。(1) `bump_generation` で世代を `turn.at`・`t.hook_id` にする。より新しい世代か同じ時刻の先の書き手があれば、何も書かずに `Settled(stale=True)`。(2) `signals.current_launch` が `t.launch` でなければ何も書かずに `stale`。(3) 起こした印があれば `wake_reply` で照らし、`wake` なら `wake_result` を書いて当たらなければ `stopped`、`preempted` なら判じずに印を消す、`unknown` なら判じずに印を消して `wait_stop` を「起床を照らせない」にする。(4) 待ちがあって今の Stop が待つ子でない、または今回の応答が起床の応答でないなら、`wait_end` を書いて `wait_id`・`k`・`n_max`・`wait_stop` を消す。(5) 待つ子で待ちが無ければ、`wait_id` を世代の時刻、`k` を 0、`n_max` を式の値にして `wait_start` を書く。(6) `last` を `turn` にする。新しい状態と起こさない理由を返す |
| `_wait` | 同上 | 新設 | `_wait(t: Target, gen: Generation, until: float, interval: float, clock, sleep) -> bool` | `until` まで `interval` ごとに世代だけを読み、自分のままなら真。会話の記録は読まない |
| `_finish` | 同上 | 新設 | `_finish(t: Target, gen: Generation, turn: LastTurn, stderr: TextIO, clock) -> int` | 子を止める排他を取り、その中で 世代・起動・質問のファイル を照らし、外れたら何も書かずに 0。そろえば状態を読み直して `next_step` で手を決める。`stop` なら `wait_stop` を「上限」にして `stop_waking`（`scope` `wait`）を書いて 0。`wake`・`end_wait` なら設計の決定 6 の順で (1) 中継の入力の印（決定 10 でマーカーの形のとき） (2) 起こした印（`wake_id`）・`k + 1`・世代（起こした時刻）・`end_wait` なら `stopped` に「引継ぎを求めた」 (3) `wake` の行 を書き、文を出して 2 |

`Target` は `relay_dir`・`launch`・`session_id`・`transcript`・`works`・`monitors`・`last_message`・`hook_id` を持つ（`NamedTuple`）。`Generation` は `at`・`by` を持つ。`Settled` は `keep`・`stale`・`reason` を持つ。

### 処理の手順

1. `_target` が None なら 0（受け入れ条件 2・3）
2. 状態を読む（排他を取らない読み取り）。状態に待ちも起こした印も無く、`works + monitors` が 0 なら、会話の記録を読まずに 0（受け入れ条件 9）。眠る hook も無いので世代を書く要が無い
3. 会話の記録の末尾を読み、`last_turn` を打つ。読めない（`OSError`）・`unrecorded` なら、起こさず止めずに 0。その理由を `noted` に持たなければ、排他の中で照らしてから `stop_waking`（`scope` `once`）と `noted` を書く（受け入れ条件 7）。`form` なら、排他の中で照らしてから、書いていなければ `stopped` と `stop_waking`（`scope` `session`）を書いて 0
4. `_settle` を打つ。`stale` なら 0（受け入れ条件 2）。`stopped` か `wait_stop` があれば 0（受け入れ条件 4・5・8）。待つ子でなければ 0
5. `wake_at` が None なら 0。`wake_at` が今の時刻以前なら、起こさず止めずに 0（手順 3 と同じく `once` を 1 度）。`_wait` が偽なら 0（受け入れ条件 1・2）
6. `_finish` を打つ（受け入れ条件 4・5・10・11）

### 異常系の処理

| 関数 | 失敗の種類 | 原因 | 検出する場所 | 検出したときの処理 | 利用者への通知 | 回復の手段 |
| --- | --- | --- | --- | --- | --- | --- |
| `run` | 例外 | 想定外の入力・読み書きの失敗 | 一番外の `except` | 0 を返す | 無し | 次の Stop で起動し直る |
| `bump_generation` | 書けない・排他を取れない | 権限・ディスク・ラッパーが子を止める最中 | `signals.held`・`replace_json` | 0 を返す（世代の無い hook は古い hook と見分けられないので眠らない） | 無し | 次の Stop |
| `_finish` | 排他を取れない | ラッパーが子を止める最中 | `signals.held` | 起こさずに 0 | 無し | 次の起動で始め直る |
| `_finish` | 中継の入力の印を立てられない | 権限・ディスク | `markers.mark`（マーカーの形のとき） | 何も書かずに 0（印の無い起床は利用者の入力として数えられる） | 無し | 次の Stop |
| `_finish` | 状態を書けない | 権限・ディスク | `replace_json` | 立てたマーカーを消して 0（`k`・`pending` は書かれていない） | 無し | 次の Stop |
| `_finish` | `wake` の行を書けない | 権限・ディスク | `append_cache_log` | 2 のまま（状態は起こしたことを持つ。集計は `wake` の無い `wake_result` を数えて外す） | 無し | — |
| `_settle`・`_finish` | 照らしが外れた | 新しい世代・起動の替わり | 排他の中の照らし | 状態も起こす記録も書かずに 0 | 無し | — |

### 並行と排他

- 世代・状態・起こした印・起こす記録の読み書きは、すべて子を止める排他（`signals.STOP_LOCK`。#4049 で `wrapper.QUESTION_LOCK` から移す）の中で行う。ラッパーが子を止めるのと、UserPromptSubmit の guard の世代の書き換えと直列にする。書き込みのある区間は、どれも同じ区間の初めに世代と起動を照らす（`_settle`・`_finish`・手順 3 の `stop_waking`）。排他を出てから書く経路は無い
- 世代は時刻と書き手の ID の組で、時刻が状態のものより新しいときだけ書き換える（`bump_generation`）。同じ時刻なら先に書いた書き手を残す。時刻の取得元は、起こす hook の Stop では今回の応答と確かめた行の `timestamp`、UserPromptSubmit の guard では guard の `clock()`、起床では排他の中の `clock()` である（設計の決定 6）。hook が動いた時刻は使わない
- 待つ子かは Stop の入力の `background_tasks` を自分で分けて決める。同じ Stop で動く #4049 の同期の guard（`relay-marker`）が書く記録のファイルは読まないので、2 つの hook の実行の順に依らない
- 眠る hook は世代のファイルを `interval` ごとに読むだけで、排他を取らない（読み違えても、起こす直前に排他の中で照らし直す）

### ログと記録の出力

| 出来事 | 出力先 | 項目 |
| --- | --- | --- |
| 待ちの始まり | `cache-log.jsonl` | `kind: wait_start`・`wait_id`・`tokens`・`ttl`・`model`・`n_max` |
| 起こした | 同上 | `kind: wake`・`wait_id`・`wake_id`（`uuid4().hex`。状態の `pending` にも置く）・`tokens`・`age`・`k`・`n_max`・`text`（`wake`・`end_wait`） |
| 起きた応答 | 同上 | `kind: wake_result`・`wait_id`・`wake_id`・`hit`・`tokens`（印の `S`）・`cache_read`・`cache_write`・`output`・`model`・`ttl`・`w`・`r`・`out` |
| 待ちの終わり | 同上 | `kind: wait_end`・`wait_id`・`age`・`k`・`hit`・`tokens`（状態の `last.tokens`。待ちの終わりの直前の `S`）・`cache_read`・`cache_write`・`output`・`model`・`ttl`・`w`・`r`・`out` |
| やめた・一時的に起こさなかった | 同上 | `kind: stop_waking`・`wait_id`・`reason`・`scope`（`session`・`wait`・`once`）・`k`・`n_max` |

どの行も `at`・`launch`・`session_id` を持つ。ラッパーの `log.jsonl` には書かない（書き手を 1 つにする）。

### 既存の部品の再利用

| 新設するもの | 見つけた既存の部品 | 見つけ方 | 使う・使わない理由 |
| --- | --- | --- | --- |
| 会話の記録の末尾の読み取り | `observe.context_tokens`（`agents.tail`） | `grep -n "def context_tokens" src/ndf/domains/relay/observe.py` | 同じ `agents.tail` を使う。`context_tokens` は `usage` の読める行までさかのぼるので、最後の応答の取り違えを避けるため使わない |
| 背景の作業の分け方 | #4049 の `relay_busy.task_kind`・`work_count` | #4049 の詳細設計 | そのまま使う |
| 中継のマーカー | `markers.mark` | `grep -n "def mark" src/ndf/domains/relay/markers.py` | マーカーの形のときに使う |
| 排他・起動の照らし | `signals.held`・`STOP_LOCK`・`current_launch` | #4049 の詳細設計 | そのまま使う |
| 版の確かめ | `adapters/cli_floor.require` | `grep -n "cli_floor" src/ndf/domains/relay/wrapper.py` | 確かめに使った版を返すよう変えて、`_start` が打つ結果の版を使う（「ラッパー」の節） |
| 対象の会話の限定 | #4049 の `signals.relay_session`・`SESSION_ENV` | #4049 の詳細設計 | そのまま使う。会話の ID の形は `SESSION_ID_RE`（`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`）を `signals` に足し、`keep-<会話>.json` の名前を作る前に照らす |

### 単体テストの観点

| 関数 | 観点 |
| --- | --- |
| `run` | 変数なしで眠りの関数 0 回・記録の読み取り 0 回で 0。`session_id` が `NDF_RELAY_SESSION` と違う入力・UUID の形でない会話の ID では、状態を読まず書かずに 0。状態も待ちも無い Stop で読み取り 0 回。待つ子で 3,000 秒後に 2、標準エラーに起こす文。眠る間に新しい Stop・UserPromptSubmit で世代が替わると 0。古い応答の時刻を持つ Stop の hook が guard の後に動くと、世代を戻さず何も書かずに 0。質問のファイルが在ると 0。2 つの hook を同じ時刻まで眠らせても 2 は 1 つ。完全な前の応答だけが残り `last_assistant_message` が違う入力では、眠りの関数 0 回・`stopped` 無しで 0。起こす時点が過去になる見本でも 0 |
| `_settle` | 起こす文から始まった応答の読み出しが前の `S` の 0.89 倍で `stopped`、0.9 倍で `hit`。印の直後の入力が通常の入力なら判じない。形を決められなければ判じず `wait_stop`。待ちの後の Stop で `wait_end` が 1 行で、`tokens` が `last.tokens`。`wait_end` の後の待つ子の Stop で `k` 0・`n_max` を決め直した `wait_start`。新しい世代がある状態で打つと、`pending`・`wait_stop`・起こす記録のどれも変えない |
| `_finish` | `end_wait` の後の Stop で、次の待ちでも起こさない。上限の `stop` は `wait_stop` だけを書き、`stopped` を書かない。新しい世代がある状態で眠りから戻ると、上限でも `wait_stop`・`stop_waking` を書かない。マーカーが書けない順で状態も記録も変わらず 0、状態が書けない順でマーカーが消えて 0 |

## hook の登録（`registration`・`runtimes.json`・`entry.py`）

- `runtimes.json` の claude の `Stop` に `"async_rewake": {"budget": "stop-rewake", "timeout_max": 3300}` を足す。`timeout_max` は背景の登録だけの上限で、事象の `timeout_max`（同期の Stop の登録の上限。ランタイムの 600）は変えない
- `limits.json` に `hook.budget.stop-rewake` を今の `hook.budget.*` の形（`p95_ms`・`deadline`・`margin`・`timeout`・`inject_chars`）で置く。`timeout` 3,300 秒（起こす時点の最大 3,000 秒 + 確かめる間隔 60 秒 + 余り 240 秒）・`deadline` 3,300・`margin` 240・`p95_ms` 500（待つ子でない Stop で終わるまで）・`inject_chars` 1,000（起こす文の長さの上限）。3,300 秒を受け付けない版では、設計の決定 7 のとおり版の門を開けない
- `budget` に `rewake_budget(runtime, event, limits=None) -> Optional[Budget]` を足す。事象の行の `async_rewake.budget` から `hook.budget.<名前>` を引く（`policy_name` と同じくランタイム別の行を先に見る）。`async_rewake` の無い事象は None
- `registration.render` は `async_rewake` を持つ事象に、2 つ目の登録 `{"type": "command", "command": <command に rewake を足したもの>, "asyncRewake": true, "timeout": <rewake_budget の timeout>}` を足す。timeout は宣言（`limits.json`）から読み、3,300 を埋め込まない
- 検査: `_file_registrations` は登録ごとに `async_rewake`（登録の `asyncRewake` が真か）を行に足す。`budget_registrations` は `async_rewake` を持つ事象に背景の行を 1 つ足す。`_registration_errors` は、`async_rewake` の行では `rewake_budget` の締め切りと `async_rewake.timeout_max` で照らし、ほかの行は今のとおり事象の予算と `timeout_max` で照らす。背景の行があって事象の行に `async_rewake` が無い・`rewake_budget` が None なら破れとする
- `entry.py` は `argv[3] == "rewake"` なら `call("ndf.domains.hooks.rewake", "run", argv)` の値を返す（ほかは今のとおり 0）

| 観点 | テスト |
| --- | --- |
| 登録 | `render("claude")` の Stop が 2 つの登録を持ち、2 つ目だけが `asyncRewake: true`・`timeout`（`hook.budget.stop-rewake` の値）を持つ。`limits.json` の値を差し替えると 2 つ目の `timeout` が変わる。codex の登録は変わらない |
| 予算の検査 | 生成した登録で `check_budget` が空。同期の Stop の登録の timeout を 601 にすると破れ（上限 600 のまま）。背景の登録の timeout を 3,301 にすると破れ。背景の登録の timeout を同期の予算の締め切り未満にしても、背景の予算の締め切り以上なら破れない。`async_rewake` の無い事象に `asyncRewake` の登録があると破れ |
| 入口 | `rewake` の引数で `run` の 2 がそのまま返り、`run` を読めなければ 0 |

## UserPromptSubmit の guard（`turn`）

- 中継の下（`NDF_RELAY_REWAKE` と中継の保存先があり、入力の `session_id` が `NDF_RELAY_SESSION` と同じで、会話の ID が `SESSION_ID_RE` の形）の UserPromptSubmit で、子を止める排他の中で `bump_generation(Generation(at=clock(), by="turn"))` を打つ（眠る hook の予約を失効させる）。状態のファイルが無い会話では作らない（眠る hook が無い）
- 起こす文の見分け: 設計の決定 10 の実測で、入力の `prompt` に起こす文が載ると分かれば、頭が `WAKE_HEAD` の入力を中継の入力として数え直さない。載らなければ今の `markers.take` のままにする

| 観点 | テスト |
| --- | --- |
| 失効 | 眠る hook の世代より新しい入力で世代が替わり、眠る hook が 0 で終わる |
| 対象の限定 | `session_id` が `NDF_RELAY_SESSION` と違う入力（入れ子の claude）では世代を替えず、眠る hook は起こす |
| 見分け | 起きた応答の入力で数え手が数え直さない |

## ラッパー（`wrapper._start`）

- `adapters/cli_floor.require(name, argv0, env, cwd) -> Optional[str]` に変える。下限の確かめ（古ければ `TooOld`）は今のまま保ち、確かめに使った版（`_version` が同じ実パス・更新時刻・大きさで持つ値。表に無い名前・見つからない・読めないなら None）を返す。`refuse_old` は返り値を捨てるだけで変えない。`--version` の起動は増えない
- `_start` は `cli_floor.require("claude", CLAUDE, env, self.s.cwd)` の返り値を版として使う。版が `limits.text("cache.rewake", "version")` 以上なら子の環境に `NDF_RELAY_REWAKE=1` を足す。足りない・None・`cache.rewake.version` が空なら、子の環境から `NDF_RELAY_REWAKE` を消す（親から受け継いだ値を渡さない）
- 見回りでは版を読まない

| 観点 | テスト |
| --- | --- |
| 版の門 | 版 2.1.329 / 2.1.330（`cache.rewake.version` を 2.1.330 にした値で）・読めない・空の 4 つで、渡す・渡さない・消すが決まる |
| 版の取得 | `require` が下限の確かめに読んだ版を返し、下限より古ければ今のとおり `TooOld`。`_start` から通した 1 回の起動で、差し替えた `--version` の実行が 1 回だけ |

## 集計（`ndf relay cache report`）

| 関数 | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- |
| `report` | 新設 | `report(rows: Iterable[Mapping[str, Any]], since: Optional[float] = None) -> dict[str, Any]` | `wait_id`（と `session_id`）で待ちにまとめ、`wake` と `wake_result` を `wake_id` で結ぶ。待ちの数・起こした回数・上限で止めた待ちの数・`age` の幅（30・45・50・55・60 分）ごとの当たりの率（`age` は結べた `wake` の値、分母は結べた `wake_result`）・起こす応答 1 回の費用（各行の `cache_write × w + output × out`）の中央値・待ちの長さ（`wait_start` から `wait_end`）の中央・75・90 パーセンタイル・避けた書き直し（1 つの待ちで最大 1 回。寿命を越えて続き `wait_end` が当たった待ちだけ。token は `wait_end` の `tokens`、費用の差は `(w − r) × tokens` − その待ちの起こす費用の合計で、倍率は各行の `w`・`r`・`out`）。`since` があれば `wait_start` の `at` がそれ以後の待ちだけを集める。`metrics` に `partial_waits`（`wait_start` が `since` より前か無い待ち）・`unmatched_wakes`（`wake_result` の無い `wake`）・`orphan_results`（`wake` の無い `wake_result`）を出し、どれも集計に入れない |
| `run` | 新設 | `run(args: argparse.Namespace) -> int` | 中継の保存先の全ラッパーの `cache-log.jsonl` を読み、`--since` の日時を `report` に渡し、統一の JSON レポート（`{"tool": "relay", "status", "summary", "items", "metrics"}`）で返す。読めない行は数えて `metrics.skipped` に出す。費用は行の倍率で出すので `limits.json` を読まない |

## 上限の設定の行（`limits.json`）

| 行 | 形 |
| --- | --- |
| `cache.wake` | `{"summary": …, "seconds": 600, "interval": 60}` |
| `cache.write-ratio` | `{"summary": …, "ratios": {"1h": 2.0, "5m": 1.25}}` |
| `cache.read-ratio` | `{"summary": …, "default": 0.1, "ratios": {"claude-opus-5-5": 0.05, "claude-sonnet-5-5": 0.05, "claude-fable-5-1": 0.025, "claude-fable-5-2": 0.025, "claude-mythos-5-1": 0.025, "claude-mythos-5-2": 0.025}}` |
| `cache.output-ratio` | `{"summary": …, "default": 5}` |
| `cache.wake-cost` | `{"summary": …, "count": 2000}` |
| `cache.hit` | `{"summary": …, "ratio": 0.9}` |
| `cache.rewake` | `{"summary": …, "version": ""}`（実装の最初の実測の後に、通った版を書く） |
| `hook.budget.stop-rewake` | 今の `hook.budget.*` の形で `timeout` 3,300 |

`limits.schema.json` の行の形に `ratios`（文字列から数への表）・`default`（数）・`ratio`（0 より大きく 1 以下）・`version`（空か `数.数.数`）を足し、`contracts/limits.py` の `Policy` に同じ項目（`ratios: Optional[dict[str, float]]`・`default: Optional[float]`・`ratio: Optional[float]`・`version: Optional[str]`）を足す。

読み手は入口ごとに次のとおり決める。

| 入口 | 取得の API | 返る型 |
| --- | --- | --- |
| 起こす hook（`rewake`・`keep_cache.ratios` へ渡す） | `hooks/tables.policy_value(name, key)`（今のまま。行の 1 つの値を JSON のまま返す） | `ratios` は `dict[str, float]`、`version` は `str`、ほかは数 |
| 集計（`cli_cache`） | 読まない。起こす hook が `tables.policy_value` で読んで行に書いた倍率（`w`・`r`・`out`）を使う | — |
| ラッパー（`wrapper._start`） | 新設の `contracts/limits.text(name, key) -> str` | `str`（`version`） |

`limits.value` は、値が数でない（`ratios`・`version`）ときに `LimitsError` を上げる。テスト（設計のテスト設計の 13）は、1 つの差し替えた `limits.json` から、hook の `ratios` の結果（辞書の `ratios` と `default`）・hook が書いた行の倍率・その行から集計が出す費用・ラッパーの版の門（文字列の `version`）が、差し替えた値どおりになることを確かめる。
