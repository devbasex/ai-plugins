# #4068 の詳細設計: 起こす hook と起こす記録

設計文書 [issue-4068-4199-design.md](./issue-4068-4199-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。#3995・lracct（#3604・#3864）・#4049 の実装の後に、その上で実装する。使うものは次のとおりである。

| 使うもの | 取り方 |
| --- | --- |
| 判断開始の値 | `context_thresholds.thresholds` の返り |
| 背景の作業の分け方 | #4049 の `relay_busy.task_kind`・`work_count` |

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
│   ├── domains/ndf-files.json
│   └── schemas/io/relay.args.schema.json・relay.result.schema.json
├── hooks/claude.json（再生成）
├── docs/contracts/hooks.md・files.md・commands.md（再生成）
├── skills/development-workflow/references/relay.md
├── src/ndf/contracts/limits.py
├── src/ndf/adapters/cli_floor.py
└── tests/domains/
    ├── hooks/test_rewake.py *・test_registration.py・test_budget.py・test_guards.py
    └── relay/test_keep_cache.py *・test_relay_wrapper.py・test_relay_cli_parse.py
（`cli_floor` のテストは今の置き場の `tests/` の下のファイルに足す）
```

依存の向き:

| 依存 | 向きと決まり |
| --- | --- |
| `hooks/rewake.py` → `relay/keep_cache.py`・`relay/signals.py`・`relay/markers.py`・`relay/observe.py` | hook → 中継の向きで、今の `hooks/guards/turn.py` → `relay/markers.py` と同じ |
| `keep_cache.py` | 標準ライブラリだけで書き、判定を純粋な関数に閉じる |
| `cli_cache.py` → `keep_cache.py`・`signals` | 費用は行に書かれた倍率で出す |
| `relay/wrapper.py` → `hooks/registration.py`（`rewake_settings`） | 中継 → hook の向きの唯一の依存。`registration` は `relay` を読まないので輪にならない |

| ファイル | 種別 | 責務（1 文） | 依存 | 公開シンボル | 関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/keep_cache.py` | 新設 | 起こす時点・上限・次の手を決め、会話の記録の最後の応答を読む | 標準ライブラリ・`relay.agents`（末尾の読み取り） | `LastTurn`・`TurnRead`・`Usage`・`Reply`・`Ratios`・`Step`・モジュール `keep_cache` | `last_turn`・`wake_reply`・`opened_by`・`is_wake_input`・`ratios`・`wake_at`・`n_max`・`next_step`・`hit`（新設） |
| `src/ndf/domains/hooks/rewake.py` | 新設 | Stop の背景で起動し、今回の応答を確かめて世代を書き、眠って確かめ直し、起こすなら終了コード 2 で終わる | `keep_cache`・`signals`・`markers`・`hooks.tables`・`relay_busy` | モジュール `rewake` | `run`・`_target`・`_settle`・`_wait`・`_finish`（新設） |
| `src/ndf/domains/hooks/guards/turn.py` | 変更 | 中継の下の UserPromptSubmit で、今の起動を照らしてから起こす状態の世代を入力の時刻に書き換え（状態が無ければ世代だけの状態を作る）、起こす文の入力を中継の入力と見分ける（決定 10 の実測で決めた形） | `signals`・`keep_cache` | — | `check`（変更） |
| `hooks/entry.py` | 変更 | `argv[3]` が `rewake` なら `rewake.run` の終了コードを返す | — | — | `main`（変更） |
| `src/ndf/domains/hooks/registration.py` | 変更 | 中継の子へ `--settings` で渡す起こす hook の登録（Stop の `asyncRewake: true` 1 つ・`timeout` は背景の予算から・command は `hooks/entry.py` の絶対パス）を作る。プラグインの登録（`render`）には足さない | `hooks.tables`・`hooks.budget` | — | `rewake_entry`・`rewake_settings`（新設）。`render` は変えない |
| `src/ndf/domains/hooks/budget.py` | 変更 | 背景の登録（`rewake_entry` と同じく `rewake_budget` の `timeout` を持つ行）を `async_rewake` の予算と上限で検査し、プラグインの登録のファイルに `asyncRewake` の登録があれば破れとする。同期の登録は今の予算と上限で検査する | `hooks.tables`（`registration` は読まない。`registration` が `budget` を読むので輪を作らない） | — | `rewake_budget`（新設）・`budget_registrations`・`_file_registrations`・`_registration_errors`（変更） |
| `src/ndf/adapters/cli_floor.py` | 変更 | `require` が下限の確かめに使った版を返す | 標準ライブラリ・`semver` | — | `require`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 起こす状態（会話ごと）と起こす記録の読み書き。世代は新しい時刻でだけ書き換える。今の起動の記録（`launch.json`）に起こす hook の登録を渡したか（`rewake`）を持つ | 標準ライブラリ | — | `read_keep`・`bump_generation`・`update_keep`・`append_cache_log`・`rewake_on`（新設）・`set_launch`（`rewake` の引数を足す）、定数 `KEEP_PREFIX`・`CACHE_LOG`・`SESSION_ID_RE` |
| `src/ndf/domains/relay/wrapper.py` | 変更 | `_start` の版の確かめ（`cli_floor.require`）の版が `cache.rewake` の範囲の内なら、子の起動の引数に `--settings <rewake_settings の JSON>` を足す | `signals`・`adapters.cli_floor`・`hooks.registration`（`rewake_settings`） | `Wrapper` | `_start`（変更） |
| `src/ndf/domains/relay/cli_cache.py` | 新設 | `ndf relay cache report` | `keep_cache`・`signals` | モジュール `cli_cache` | `report`・`run`（新設） |
| `src/ndf/domains/relay/cli.py` | 変更 | サブコマンド `cache report` を足す | `cli_cache` | — | `build_parser`（変更） |
| `contracts/limits.json`・`contracts/schemas/limits.schema.json`・`src/ndf/contracts/limits.py` | 変更 | `cache.wake`・`cache.write-ratio`・`cache.read-ratio`・`cache.output-ratio`・`cache.wake-cost`・`cache.hit`・`cache.rewake`・`hook.budget.stop-rewake` を足し、スキーマと `Policy` に `ratios`・`default`・`ratio`・`versions` の項目を足す | — | `Policy` | `text`（新設）・`value`（数でない値で `LimitsError`） |
| `contracts/hooks/runtimes.json` | 変更 | claude の Stop に `async_rewake`（予算 `stop-rewake`・背景の登録だけの `timeout_max` 3,300）を足す。プラグインの登録の生成には使わず、`rewake_entry` と予算の検査が読む | — | — | — |
| `contracts/domains/ndf-files.json`・`env.json` | 変更 | `ndf-files.json` に `relay-keep`（`<名前>/keep-<会話>.json`）・`relay-cache-log`（`<名前>/cache-log.jsonl`）の行。どちらもラッパーの保存先と同じ掃除の対象。`env.json` は変えない（起こす hook のための環境変数は足さない） | — | — | — |
| `contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` | 変更 | `cache report` の引数と結果 | — | — | — |
| `hooks/claude.json`・`docs/contracts/*.md` | 変更（再生成） | `ndf hooks render`・`ndf check --write-spec` で作り直す | — | — | — |
| `skills/development-workflow/references/relay.md` | 変更 | hook の一覧に起こす hook、記録の種類に起こす記録、見直しの手順（20 件ごと）を足す | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更（`glossary.md` は再生成） | 要求の用語 7 語と設計の 2 語を足す | — | — | — |

## 判定（`keep_cache`）

純粋な関数で、時刻・値・記録の文字を引数で受ける。

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `last_turn` | `src/ndf/domains/relay/keep_cache.py` | 新設 | `last_turn(tail: str, known_ttl: Optional[str], last_message: Optional[str], started: float) -> TurnRead` | 記録の末尾の最後の `assistant` の行（同じ `message.id` は 1 つ）を読む。その応答の `text` をつないだ文が `last_message` と違う・`last_message` が None なら `TurnRead(None, "unrecorded")`（今回の応答が未記録。一時的）。同じで項目が欠けた・型が違えば、前の行へさかのぼらずに `TurnRead(None, "form")`。同じで、その応答の時刻が `started`（hook の動き始めの時刻）より後なら `TurnRead(None, "newer")`（自分の Stop の後に書かれた同じ文の応答。設計の決定 4。実測 9 で Stop の入力に応答の ID が載ると分かれば、文と時刻の代わりにその ID と `message.id` で照らす）。そろえば `TurnRead(LastTurn, None)`。寿命は設計の決定 2 の表で決め、両方 0 なら `known_ttl`。時刻はその `message.id` の最後の行の `timestamp` |
| `wake_reply` | 同上 | 新設 | `wake_reply(tail: str, after_id: str) -> Reply` | `after_id` の応答の直後にある最初の入力の行だけを見る（Tool の結果の行は入力に数えない）。起床の文の形（設計の決定 7 の実測 8 で決めた形を `is_wake_input` に閉じる）なら、その次から次の入力の手前（無ければ末尾）までの `assistant` の `message.id` をその起床の呼び出しとし、`Reply("wake", <最初の呼び出しの LastTurn>, <全呼び出しの Usage>)`。利用者の入力か背景の作業の知らせの形なら、その先を探さずに `Reply("preempted", None, None)`。どちらとも決められない・`after_id` が見つからないなら `Reply("unknown", None, None)` |
| `opened_by` | 同上 | 新設 | `opened_by(tail: str, message_id: str) -> Optional[bool]` | `message_id` の応答を始めた入力（さかのぼって最初の、Tool の結果でない入力の行）に `is_wake_input` を打った値。起床の文なら真、通常の入力なら偽、決められない・見つからないなら None。`_settle` が待ちを明けるかを決めるのに使う |
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
| `TurnRead` | 型（`NamedTuple`） | 同上 | `turn`（`LastTurn` か None）・`reason`（`unrecorded`・`form`・`newer` か None） |
| `Reply` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`preempted`・`unknown`）・`turn`（起床の最初の呼び出し。当たりの判定に使う）・`usage`（`Usage`。起床に属する全呼び出しの合計） |
| `Usage` | 型（`NamedTuple`） | 同上 | `calls`（呼び出しの数）・`cache_read`・`cache_write`・`output`（どれも合計） |
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
| `last_turn` | 次の応答を読んだ | 遅れて動いた古い hook | 応答の時刻と `started` の比べ | `reason` `newer` | 無し（呼び手は何も書かずに 0） | — |
| `ratios` | 行が無い・値が数でない | 上限の設定の壊れ | 型の照らし | `tables.TableError` を投げる | 無し（呼び手が起こさない） | 上限の設定を直す（`ndf check` が落とす） |

### 単体テストの観点

| 関数 | 観点 |
| --- | --- |
| `last_turn` | 実測の形の見本を読める。最後の `assistant` の行に `usage` が無い見本では、前の行が完全でも `form`。完全な前の応答だけが残り `last_message` が違う見本では `unrecorded`。`last_message` が None でも `unrecorded`。同じ文で時刻が `started` の 1 秒後なら `newer`、`started` と同じなら読める。寿命の表の 4 行 |
| `wake_reply` | 起床の文の後の応答を返す。Tool を 2 回使った起床の見本では `turn` が最初の呼び出し、`usage.calls` が 3 で合計が 3 つの和、Tool の結果の行で区切らない。起床の後に利用者の入力が続く見本では、その入力の後の応答を含めない。印の直後の最初の入力が利用者の入力の見本では、その後に起床の文の応答があっても `preempted`。最初の入力の形を決められない見本では `unknown` |
| `opened_by` | 利用者の入力で始まり Tool を使った応答で偽（Tool の結果の行を飛ばす）。起床の文で始まった応答で真。形を決められない入力で None |
| `n_max` | Opus 5.5・`1h`・`d` 2,000 で `S` 400,000 → 35、500,000 → 36、20,000 → 13。読み出し 0.1 で 400,000 → 18 |
| `wake_at` | `1h`・余白 600 で `at + 3000`、`5m` で None |
| `next_step` | `limit` 35 で `k` 33 → `wake`、`k` 34 → 監視だけ・`tokens` 250,000・`decide_from` 250,000 なら `end_wait`・`tokens` 249,999 なら `wake`、`k` 35 → `stop`。作業の子は `k` 34 でも `wake`。`limit` 0 → `stop`（純粋な関数の境界。`limit` を式から作る組の試験は設計のテスト設計の 5） |

## 起こす hook（`hooks/rewake.py`）

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `run` | `src/ndf/domains/hooks/rewake.py` | 新設 | `run(argv: list[str], stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr, environ: Mapping[str, str] = os.environ, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> int` | 終了コード。起こすなら標準エラーへ文を書いて 2、ほかは 0。例外はすべて 0（Stop を壊さない）。最初に `clock()` を打ち、その値を動き始めの時刻（`started`）として `Target` に持たせる |
| `_target` | 同上 | 新設 | `_target(payload: Mapping[str, Any], environ: Mapping[str, str]) -> Optional[Target]` | 中継の保存先（`NDF_RELAY_DIR`）・起動の ID・会話の ID・`transcript_path` がそろい、入力の `session_id` が #4049 の `signals.relay_session(environ)`（`NDF_RELAY_SESSION`）と同じで、会話の ID が UUID の形（`SESSION_ID_RE`）のときだけ `Target`（`background_tasks` を #4049 の `task_kind` で分けた作業と監視の件数、`last_assistant_message`、起動のたびに作る `hook_id`（`uuid4().hex`）・`started` を持つ）。ほかは None（入れ子の claude は `--settings` の登録を受け継がないのでこの hook が起動しない。起動しても `session_id` が違うので通らない） |
| `_settle` | 同上 | 新設 | `_settle(t: Target, turn: LastTurn, tail: str, q_for, clock) -> Settled` | 子を止める排他を取り、その中で次を 1 つの区間で行う。(1) `signals.current_launch` が `t.launch` でなければ、世代も含めて何も書かずに `Settled(stale=True)`。(2) `bump_generation` で世代を `turn.at`・`t.hook_id` にする。より新しい世代か同じ時刻の先の書き手があれば、何も書かずに `stale`。(3) 起こした印があれば `wake_reply` で照らし、`wake` なら `wake_result`（`hit` は `Reply.turn`、`cache_read`・`cache_write`・`output`・`calls` は `Reply.usage`、`text` は印から）を書いて当たらなければ `stopped`、`preempted` なら判じずに印を消す、`unknown` なら判じずに印を消して `wait_stop` を「起床を照らせない」にする。(4) 待ちがあり、今の Stop が待つ子でないか、`opened_by(tail, turn.message_id)` が偽（今回の応答を通常の入力が始めた）なら、`wait_end` を書いて `wait_id`・`k`・`n_max`・`wait_stop` を消す。`opened_by` が真（起床の応答）か None（決められない）なら待ちを明けず、`k`・`n_max`・`wait_stop` を保つ（(3) で立てた「起床を照らせない」も消えない）。(5) 待つ子で待ちが無ければ、`wait_id` を世代の時刻、`k` を 0、`n_max` を式の値にして `wait_start` を書く。(4) で待ちを明けなかった Stop では、待ちが残るので (5) は書かない。(6) `last` を `turn` にする。新しい状態と起こさない理由を返す |
| `_wait` | 同上 | 新設 | `_wait(t: Target, gen: Generation, until: float, interval: float, clock, sleep) -> bool` | `until` まで `interval` ごとに世代だけを読み、自分のままなら真。会話の記録は読まない |
| `_finish` | 同上 | 新設 | `_finish(t: Target, gen: Generation, turn: LastTurn, stderr: TextIO, clock) -> int` | 子を止める排他を取り、その中で 世代・起動・質問のファイル を照らし、外れたら何も書かずに 0。そろえば状態を読み直して `next_step` で手を決める。`stop` なら `wait_stop` を「上限」にして `stop_waking`（`scope` `wait`）を書いて 0。`wake`・`end_wait` なら設計の決定 6 の順で (1) 中継の入力の印（決定 10 でマーカーの形のとき） (2) 起こした印（`wake_id`）・`k + 1`・世代（起こした時刻）・`end_wait` なら `stopped` に「引継ぎを求めた」 (3) `wake` の行 を書き、文を出して 2 |

内部の型（どれも `NamedTuple`）:

| 型 | 項目 |
| --- | --- |
| `Target` | `relay_dir`・`launch`・`session_id`・`transcript`・`works`・`monitors`・`last_message`・`hook_id`・`started` |
| `Generation` | `at`・`by` |
| `Settled` | `keep`・`stale`・`reason` |

### 処理の手順

1. `_target` が None なら 0（受け入れ条件 2・3）
2. 状態を読む（排他を取らない読み取り）。状態に待ちも起こした印も無く、`works + monitors` が 0 なら、会話の記録を読まずに 0（受け入れ条件 9）。眠る hook も無いので世代を書く要が無い
3. 会話の記録の末尾を読み、`last_turn(tail, ttl, t.last_message, t.started)` を打つ。`newer` なら何も書かずに 0（遅れて動いた古い hook。受け入れ条件 2）。読めない（`OSError`）・`unrecorded` なら、起こさず止めずに 0。その理由を `noted` に持たなければ、排他の中で照らしてから `stop_waking`（`scope` `once`）と `noted` を書く（受け入れ条件 7）。`form` なら、排他の中で照らしてから、書いていなければ `stopped` と `stop_waking`（`scope` `session`）を書いて 0
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
| `_finish` | `wake` の行を書けない | 権限・ディスク | `append_cache_log` | 2 のまま（状態は起こしたことを持つ。集計は `wake` の無い `wake_result` を数え、当たりの率と `d` から外す） | 無し | — |
| `_settle`・`_finish` | 照らしが外れた | 新しい世代・起動の替わり | 排他の中の照らし | 状態も起こす記録も書かずに 0 | 無し | — |

### 並行と排他

- 世代・状態・起こした印・起こす記録の読み書きは、すべて子を止める排他（`signals.STOP_LOCK`。#4049 で `wrapper.QUESTION_LOCK` から移す）の中で行う。ラッパーが子を止めるのと、UserPromptSubmit の guard の世代の書き換えと直列にする。書き込みのある区間は、どれも同じ区間の初めに起動、次に世代を照らす（`_settle`・`_finish`・手順 3 の `stop_waking`・guard の世代の書き換え）。起動が外れたら世代も書かない。排他を出てから書く経路は無い
- 世代は時刻と書き手の ID の組で、時刻が状態のものより新しいときだけ書き換える（`bump_generation`）。同じ時刻なら先に書いた書き手を残す。時刻の取得元は次の 3 つである（設計の決定 6）。hook が動いた時刻は使わない
  - 起こす hook の Stop: 今回の応答と確かめた行の `timestamp`
  - UserPromptSubmit の guard: guard の `clock()`
  - 起床: 排他の中の `clock()`
- 待つ子かは Stop の入力の `background_tasks` を自分で分けて決める。同じ Stop で動く #4049 の同期の guard（`relay-marker`）が書く記録のファイルは読まないので、2 つの hook の実行の順に依らない
- 眠る hook は世代のファイルを `interval` ごとに読むだけで、排他を取らない（読み違えても、起こす直前に排他の中で照らし直す）

### ログと記録の出力

| 出来事 | 出力先 | 項目 |
| --- | --- | --- |
| 待ちの始まり | `cache-log.jsonl` | `kind: wait_start`・`wait_id`・`tokens`・`ttl`・`model`・`n_max` |
| 起こした | 同上 | `kind: wake`・`wait_id`・`wake_id`（`uuid4().hex`。状態の `pending` にも置く）・`tokens`・`age`・`k`・`n_max`・`text`（`wake`・`end_wait`） |
| 起きた応答 | 同上 | `kind: wake_result`・`wait_id`・`wake_id`・`text`（印から）・`hit`（最初の呼び出し）・`tokens`（印の `S`）・`first_read`・`calls`・`cache_read`・`cache_write`・`output`（起床に属する全呼び出しの合計）・`model`・`ttl`・`w`・`r`・`out` |
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
| `run` | 中継の保存先の変数なしで眠りの関数 0 回・記録の読み取り 0 回で 0。`session_id` が `NDF_RELAY_SESSION` と違う入力・UUID の形でない会話の ID では、状態を読まず書かずに 0。状態も待ちも無い Stop で読み取り 0 回。待つ子で 3,000 秒後に 2、標準エラーに起こす文。眠る間に新しい Stop・UserPromptSubmit で世代が替わると 0。古い応答の時刻を持つ Stop の hook が guard の後に動くと、世代を戻さず何も書かずに 0。質問のファイルが在ると 0。2 つの hook を同じ時刻まで眠らせても 2 は 1 つ。完全な前の応答だけが残り `last_assistant_message` が違う入力では、眠りの関数 0 回・`stopped` 無しで 0。起こす時点が過去になる見本でも 0。作業 1 件の Stop の hook の `started` の後に、作業 0 件の Stop の同じ文の応答が記録された見本では、古い hook が世代も状態も書かずに 0 で、新しい hook が作業 0 件で `wait_end` を書く |
| `_settle` | 起こす文から始まった応答の最初の呼び出しの読み出しが前の `S` の 0.89 倍で `stopped`、0.9 倍で `hit`。Tool を使った引継ぎの起床で `wake_result` の量が全呼び出しの合計。印の直後の入力が通常の入力なら判じない。形を決められなければ判じず `wait_stop` を立て、同じ Stop で `wait_end` も `wait_start` も書かず、`k`・`n_max`・`wait_stop` が残る。続く Stop で `opened_by` が None・真の間も残り、偽の Stop で初めて `wait_end`。通常の入力で始まった待ちの後の Stop で `wait_end` が 1 行で、`tokens` が `last.tokens`。`wait_end` の後の待つ子の Stop で `k` 0・`n_max` を決め直した `wait_start`。新しい世代がある状態で打つと、`pending`・`wait_stop`・起こす記録のどれも変えない。`current_launch` が別の起動の状態で打つと、世代も含めて何も変えない（世代が新しい時刻でも） |
| `_finish` | `end_wait` の後の Stop で、次の待ちでも起こさない。上限の `stop` は `wait_stop` だけを書き、`stopped` を書かない。新しい世代がある状態で眠りから戻ると、上限でも `wait_stop`・`stop_waking` を書かない。マーカーが書けない順で状態も記録も変わらず 0、状態が書けない順でマーカーが消えて 0 |

## hook の登録（`registration`・`runtimes.json`・`entry.py`）

起こす hook はプラグインの登録（`hooks/claude.json`）に足さず、ラッパーが中継の子の起動にだけ `--settings` で渡す。中継の外の会話の Stop では、この hook のプロセスは起動しない（設計の決定 1）。

- `runtimes.json` の claude の `Stop` に `"async_rewake": {"budget": "stop-rewake", "timeout_max": 3300}` を足す。`timeout_max` は背景の登録だけの上限で、事象の `timeout_max`（同期の Stop の登録の上限。ランタイムの 600）は変えない。この行は `render` が読まず、`rewake_entry` と予算の検査だけが読む
- `limits.json` に `hook.budget.stop-rewake` を今の `hook.budget.*` の形（`p95_ms`・`deadline`・`margin`・`timeout`・`inject_chars`）で置く。値は次のとおりである。3,300 秒を受け付けない版では、設計の決定 7 のとおり版の範囲に入れない

  | 項目 | 値 |
  | --- | --- |
  | `timeout` | 3,300 秒（起こす時点の最大 3,000 秒 + 確かめる間隔 60 秒 + 余り 240 秒） |
  | `deadline` | 3,300 |
  | `margin` | 240 |
  | `p95_ms` | 500（待つ子でない Stop で終わるまで） |
  | `inject_chars` | 1,000（起こす文の長さの上限） |

- `budget` に `rewake_budget(runtime, event, limits=None) -> Optional[Budget]` を足す。事象の行の `async_rewake.budget` から `hook.budget.<名前>` を引く（`policy_name` と同じくランタイム別の行を先に見る）。`async_rewake` の無い事象は None
- `registration.rewake_entry(runtime: str, event: str, plugin_root: Path = tables.PLUGIN_ROOT) -> dict[str, Any]` を足す。返す値は次の形である
  - `{"type": "command", "command": <command>, "asyncRewake": true, "timeout": <rewake_budget の timeout>}`
  - `<command>` は今の command と同じ文で、プラグインの根を環境変数でなく plugin_root の絶対パス（shlex.quote）にし、末尾に rewake を足したもの

  `--settings` の hook にはプラグインの根の環境変数が渡らないので、絶対パスで書く。timeout は宣言（`limits.json`）から読み、3,300 を埋め込まない。事象の行に `async_rewake` が無い・`rewake_budget` が None なら `tables.TableError`
- `registration.rewake_settings(plugin_root: Path) -> dict[str, Any]` を足す。返す値は `{"hooks": {"Stop": [{"hooks": [rewake_entry("claude", "Stop", plugin_root)]}]}}` である。`render` は変えない（プラグインの登録に `asyncRewake` は現れない）
- 検査: `budget_registrations` は、`async_rewake` を持つ事象に背景の行を 1 つ足す（登録のファイルからは読まない。`timeout` は `rewake_entry` が使うのと同じ `rewake_budget` の `timeout`）。`_file_registrations` は登録のファイルの登録ごとに `async_rewake`（登録の `asyncRewake` が真か）を行に足す。`_registration_errors` は、背景の行を `rewake_budget` の締め切りと `async_rewake.timeout_max` で照らす。ほかの行は今のとおり事象の予算と `timeout_max` で照らす。登録のファイルに `asyncRewake` が真の登録があれば、事象によらず破れとする（プラグインを入れた全ての会話で起動させない）
- `entry.py` は `argv[3] == "rewake"` なら `call("ndf.domains.hooks.rewake", "run", argv)` の値を返す（ほかは今のとおり 0）

| 観点 | テスト |
| --- | --- |
| 中継の外で起動しない | `render("claude")` と生成した `hooks/claude.json` のどの事象にも `asyncRewake` の登録が無く、Stop の登録は 1 つ。`hooks/claude.json` の Stop に `asyncRewake: true` の登録を足すと `check_budget` が破れとする |
| 背景の登録 | `rewake_settings(<一時の根>)` の Stop の登録が 1 つで、`asyncRewake: true`・`timeout`（`hook.budget.stop-rewake` の値）を持ち、command が `<一時の根>/hooks/entry.py` の絶対パスと `rewake` を含み、`${CLAUDE_PLUGIN_ROOT}` を含まない。空白を含む根でも引数が 1 つに保たれる。`limits.json` の値を差し替えると `timeout` が変わる。codex の登録は変わらない |
| 予算の検査 | 生成した登録で `check_budget` が空。同期の Stop の登録の timeout を 601 にすると破れ（上限 600 のまま）。背景の予算の timeout を 3,301 にすると破れ。背景の予算の timeout を同期の予算の締め切り未満にしても、背景の予算の締め切り以上なら破れない。`async_rewake` の行を消すと `rewake_entry` が `TableError` |
| 入口 | `rewake` の引数で `run` の 2 がそのまま返り、`run` を読めなければ 0 |

## UserPromptSubmit の guard（`turn`）

- 中継の下の UserPromptSubmit で、子を止める排他の中で次の 2 つを行う。中継の下とは、中継の保存先と起動の ID（`NDF_RELAY_LAUNCH`）があり、入力の `session_id` が `NDF_RELAY_SESSION` と同じで、会話の ID が `SESSION_ID_RE` の形のときである
  1. `signals.rewake_on(<保存先>, <自分の起動の ID>)` が偽なら何も書かない。偽になるのは次の 2 つのときである
     - 今の起動（`launch.json`）が自分の起動でない（起動し直した後の旧い起動の guard が、新しい起動の予約を失効させない）
     - 今の起動に起こす hook の登録を渡していない（版の範囲の外の子で状態を作らない）
  2. `bump_generation(Generation(at=clock(), by="turn"))` を打つ（眠る hook の予約を失効させる）。状態のファイルが無い会話では、世代だけを持つ状態を作る。最初の待ちで背景の hook より先に入力が届いても、後から動く hook は入力より古い応答の時刻しか持たず、世代を取れない
- この guard はプラグインの登録の今の UserPromptSubmit の上で動くので、プロセスは増えない。状態のファイルを作るのは、起こす hook の登録を渡した中継の子だけである
- 起こす文の見分け: 設計の決定 10 の実測で、入力の `prompt` に起こす文が載ると分かれば、頭が `WAKE_HEAD` の入力を中継の入力として数え直さない。載らなければ今の `markers.take` のままにする

| 観点 | テスト |
| --- | --- |
| 失効 | 眠る hook の世代より新しい入力で世代が替わり、眠る hook が 0 で終わる |
| 最初の待ちの順 | 状態のファイルが無い会話で guard が先に動くと、世代（`by` `turn`）だけの状態ができる。その後に入力より前の応答の時刻を持つ Stop の hook が動くと、世代を取らず、状態も起こす記録も書かずに 0 |
| 起動の照らし | `launch.json` が別の起動の ID の状態で guard を打つと、世代は変わらず、状態が無ければ作らない。新しい起動の眠る hook は起こす。`launch.json` の `rewake` が偽の起動では、状態を作らず世代も書かない |
| 対象の限定 | `session_id` が `NDF_RELAY_SESSION` と違う入力（入れ子の claude）では世代を替えず、眠る hook は起こす |
| 見分け | 起きた応答の入力で数え手が数え直さない |

## ラッパー（`wrapper._start`）

- `adapters/cli_floor.require(name, argv0, env, cwd) -> Optional[str]` に変える。下限の確かめ（古ければ `TooOld`）は今のまま保ち、確かめに使った版（`_version` が同じ実パス・更新時刻・大きさで持つ値。表に無い名前・見つからない・読めないなら None）を返す。`refuse_old` は返り値を捨てるだけで変えない。`--version` の起動は増えない
- `_start` は `cli_floor.require("claude", CLAUDE, env, self.s.cwd)` の返り値を版として使う。次の 2 つがそろえば、起動の引数の `head` の後に `--settings` と `json.dumps(registration.rewake_settings(<根>))` を足す
  - 版の文字列が `limits.texts("cache.rewake", "versions")` のどれかと完全に一致する（`semver` の大小では比べない）
  - 受け継ぐ引数（`claude_args.Parsed.carried`）に `--settings` の語も `--settings=` で始まる語も無い

  `<根>` は `self.launched_root` があればそれ、無ければ `tables.PLUGIN_ROOT`。一覧に無い・None・一覧が空・利用者の `--settings` があるなら足さない。利用者の `--settings` があるときは、受け継ぐ引数を並びも中身も変えずに渡す（`--settings` は最後の 1 つだけが効くので、重ねると利用者の設定が効かなくなる。#1543 の実測）。`--resume` で起動し直すときも同じ判定で毎回足す（`--settings` は起動ごとの引数で、会話に残らない）。足したかは `signals.set_launch(self.dir, launch, rewake=<足したか>)` で今の起動の記録に書く。UserPromptSubmit の guard が `signals.rewake_on` で読む
- 見回りでは版を読まない

| 観点 | テスト |
| --- | --- |
| 版の門 | `versions` を `["2.1.330", "2.1.332"]` にした値で、版 2.1.330 と 2.1.332 は足す・2.1.331（両端の間の測っていない版）・2.1.329・2.1.333 は足さない。読めない・一覧が空でも足さない。足した引数の JSON が `rewake_settings` と同じ |
| 利用者の `--settings` | 版が一覧に在り、受け継ぐ引数に `--settings=x.json` があるときと `--settings x.json` があるときのどちらも、初回の起動と `--resume` の再開の引数に `--settings` が 1 つだけで、利用者の値がそのまま並び、`rewake_settings` の JSON が無い。今の起動の記録の `rewake` が偽 |
| 版の取得 | `require` が下限の確かめに読んだ版を返し、下限より古ければ今のとおり `TooOld`。`_start` から通した 1 回の起動で、差し替えた `--version` の実行が 1 回だけ |

## 集計（`ndf relay cache report`）

| 関数 | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- |
| `report` | 新設 | `report(rows: Iterable[Mapping[str, Any]], since: Optional[float] = None) -> dict[str, Any]` | `wait_id`（と `session_id`）で待ちにまとめ、`wake` と `wake_result` を `wake_id` で結ぶ。出す値と分母は次の表のとおり。倍率は各行の `w`・`r`・`out` を使う。`since` があれば `wait_start` の `at` がそれ以後の待ちだけを集める。`metrics` に `partial_waits`（`wait_start` が `since` より前か無い待ち。どの集計にも入れない）・`unmatched_wakes`（`wake_result` の無い `wake`）・`orphan_results`（`wake` の無い `wake_result`）・`unpriced_waits`（`wake_result` の無い `wake` を持つ待ち）を出す |
| `run` | 新設 | `run(args: argparse.Namespace) -> int` | 中継の保存先の全ラッパーの `cache-log.jsonl` を読み、`--since` の日時を `report` に渡し、統一の JSON レポート（`{"tool": "relay", "status", "summary", "items", "metrics"}`）で返す。読めない行は数えて `metrics.skipped` に出す。費用は行の倍率で出すので `limits.json` を読まない |

| 値 | 何から出すか |
| --- | --- |
| 待ちの数・上限で止めた待ちの数 | `wait_start`・`stop_waking`（`scope` `wait`） |
| 起こした回数 | `wake` の行の数（結べない `wake` も数える） |
| `age` の幅（30・45・50・55・60 分）ごとの当たりの率 | 結べた組だけ。`age` は `wake`、`hit` は `wake_result`、分母は組の数 |
| `d` の中央値 | 結べた組のうち `text` が `wake` のものの `cache_write × w + output × out`（起床に属する全呼び出しの合計。引継ぎの起床は入れない） |
| 起床 1 回の実費の中央値（`text` の別） | 結べた組の `cache_read × r + cache_write × w + output × out`（全呼び出しの合計。読み出しを含む） |
| 待ちの長さの中央・75・90 パーセンタイル | `wait_start` から `wait_end` |
| 避けた書き直し（1 つの待ちで最大 1 回） | 待ちの長さが `wait_start` の `ttl` の寿命を越え、その待ちに `hit` が真の `wake_result`（結べた組）が 1 つ以上あり、`wait_end` の `hit` が真の待ちだけ。`unpriced_waits` の待ちは入れない。token は `wait_end` の `tokens`、費用の差は `(w − r) × tokens` − その待ちの全ての `wake_result`（`wake` の無いものも含む）の実費の合計。`wake` の無いまま寿命を越えて自然に当たった待ちは数えない |

## 上限の設定の行（`limits.json`）

| 行 | 形 |
| --- | --- |
| `cache.wake` | `{"summary": …, "seconds": 600, "interval": 60}` |
| `cache.write-ratio` | `{"summary": …, "ratios": {"1h": 2.0, "5m": 1.25}}` |
| `cache.read-ratio` | `{"summary": …, "default": 0.1, "ratios": {"claude-opus-5-5": 0.05, "claude-sonnet-5-5": 0.05, "claude-fable-5-1": 0.025, "claude-fable-5-2": 0.025, "claude-mythos-5-1": 0.025, "claude-mythos-5-2": 0.025}}` |
| `cache.output-ratio` | `{"summary": …, "default": 5}` |
| `cache.wake-cost` | `{"summary": …, "count": 2000}` |
| `cache.hit` | `{"summary": …, "ratio": 0.9}` |
| `cache.rewake` | `{"summary": …, "versions": []}`（実装の最初の実測の後に、通った版を一覧に書く。新しい版は測り直して通ってから一覧に足す） |
| `hook.budget.stop-rewake` | 今の `hook.budget.*` の形で `timeout` 3,300 |

`limits.schema.json` の行の形と、`contracts/limits.py` の `Policy` に、次の項目を足す。

| 項目 | `limits.schema.json` の形 | `Policy` の型 |
| --- | --- | --- |
| `ratios` | 文字列から数への表 | `Optional[dict[str, float]]` |
| `default` | 数 | `Optional[float]` |
| `ratio` | 0 より大きく 1 以下 | `Optional[float]` |
| `versions` | `数.数.数` の文字列の配列。重複なし。空も可 | `Optional[tuple[str, ...]]` |

読み手は入口ごとに次のとおり決める。

| 入口 | 取得の API | 返る型 |
| --- | --- | --- |
| 起こす hook（`rewake`・`keep_cache.ratios` へ渡す） | `hooks/tables.policy_value(name, key)`（今のまま。行の 1 つの値を JSON のまま返す） | `ratios` は `dict[str, float]`、`versions` は `list[str]`、ほかは数 |
| 集計（`cli_cache`） | 読まない。起こす hook が `tables.policy_value` で読んで行に書いた倍率（`w`・`r`・`out`）を使う | — |
| ラッパー（`wrapper._start`） | 新設の `contracts/limits.texts(name, key) -> tuple[str, ...]` | `tuple[str, ...]`（`versions`） |

`limits.value` は、値が数でない（`ratios`・`versions`）ときに `LimitsError` を上げる。テスト（設計のテスト設計の 13）は、1 つの差し替えた `limits.json` から、次の 4 つが差し替えた値どおりになることを確かめる。

- hook の `ratios` の結果（辞書の `ratios` と `default`）
- hook が書いた行の倍率
- その行から集計が出す費用
- ラッパーの版の門（文字列の配列の `versions`）
