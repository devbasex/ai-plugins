# #4068 の詳細設計: 起こす hook と起こす記録

設計文書 [issue-4068-4199-design.md](./issue-4068-4199-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。#3995・lracct（#3604・#3864）・#4049 の実装の後に、その上で実装する。使うものは次のとおりである。

| 使うもの | 取り方 |
| --- | --- |
| 判断開始の値 | `context_thresholds.thresholds` の返り |
| 背景の作業の分け方 | #4049 の `relay_busy.task_kind`・`work_count` |
| 会話の記録の末尾と最後の応答 | #4089 の `adapters/transcript.read_tail`・`last_context`・`timestamp_seconds`（#4089 の詳細設計の「#4068 との受け渡し」） |

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
│   ├── limits/cache.wake.json・cache.write-ratio.json・cache.read-ratio.json・cache.output-ratio.json・cache.wake-cost.json・cache.hit.json・cache.handoff.json・cache.rewake.json・hook.budget.stop-rewake.json *（`hook.transcript-tail.json` は `read_by` だけ変更）
│   ├── schemas/limits.schema.json
│   ├── hooks/runtimes.json
│   ├── domains/ndf-files.json・env.json
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
| `hooks/rewake.py` → `relay/keep_cache.py`・`relay/signals.py`・`relay/markers.py`・`adapters/transcript.py`（`read_tail`） | hook → 中継の向きで、今の `hooks/guards/turn.py` → `relay/markers.py` と同じ |
| `keep_cache.py` → `adapters/transcript.py`（`last_context`・`timestamp_seconds`） | 判定を純粋な関数に閉じる。末尾の文字列は引数で受け、ファイルを開かない |
| `cli_cache.py` → `keep_cache.py`・`signals` | 費用は行に書かれた倍率で出す |
| `relay/wrapper.py` → `hooks/registration.py`（`rewake_settings`） | 中継 → hook の向きの唯一の依存。`registration` は `relay` を読まないので輪にならない |

| ファイル | 種別 | 責務（1 文） | 依存 | 公開シンボル | 関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/keep_cache.py` | 新設 | 起こす時点・上限・次の手を決め、渡された会話の記録の末尾から最後の応答・起床の呼び出し・待ちを明けた入力を読む | 標準ライブラリ・`adapters.transcript`（`last_context`・`timestamp_seconds`） | `LastTurn`・`TurnRead`・`Usage`・`Reply`・`Opening`・`Ratios`・`Step`・モジュール `keep_cache` | `last_turn`・`wake_reply`・`opening`・`is_wake_input`・`ratios`・`wake_at`・`n_max`・`handoff_fits`・`next_step`・`hit`（新設） |
| `src/ndf/domains/hooks/rewake.py` | 新設 | Stop の背景で起動し、今回の応答を確かめて世代を書き、眠って確かめ直し、起こすなら終了コード 2 で終わる | `keep_cache`・`signals`・`markers`・`hooks.tables`・`relay_busy`・`adapters.transcript`（`read_tail`） | モジュール `rewake` | `run`・`_target`・`_settle`・`_wait`・`_finish`（新設） |
| `src/ndf/domains/hooks/guards/turn.py` | 変更 | 中継の下の UserPromptSubmit で、今の起動を照らしてから起こす状態の世代を入力の時刻に書き換え（状態が無ければ世代だけの状態を作る）、起こす文の入力を中継の入力と見分ける（決定 10 の実測で決めた形） | `signals`・`keep_cache` | — | `check`（変更） |
| `hooks/entry.py` | 変更 | `argv[3]` が `rewake` なら `rewake.run` の終了コードを返す | — | — | `main`（変更） |
| `src/ndf/domains/hooks/registration.py` | 変更 | 中継の子へ `--settings` で渡す起こす hook の登録（Stop の `asyncRewake: true` 1 つ・`timeout` は背景の予算から・command は `hooks/entry.py` の絶対パス）を作る。プラグインの登録（`render`）には足さない | `hooks.tables`・`hooks.budget` | — | `rewake_entry`・`rewake_settings`・`rewake_unreachable`（新設。`budget.rewake_reach` をそのまま返す）。`render` は変えない |
| `src/ndf/domains/hooks/budget.py` | 変更 | 背景の登録（`rewake_entry` と同じく `rewake_budget` の `timeout` を持つ行）を `async_rewake` の予算と上限で検査し、`cache.wake` の起こす時点と確かめる間隔が背景の `timeout` の内に届くか（`rewake_reach`）を確かめ、プラグインの登録のファイルに `asyncRewake` の登録があれば破れとする。同期の登録は今の予算と上限で検査する | `hooks.tables`・`relay.keep_cache`（`TTL_SECONDS` だけ）（`registration` は読まない。`registration` が `budget` を読むので輪を作らない） | — | `rewake_budget`・`rewake_reach`（新設）・`budget_registrations`・`_file_registrations`・`_registration_errors`（変更） |
| `src/ndf/adapters/cli_floor.py` | 変更 | `require` が下限の確かめに使った版を返す | 標準ライブラリ・`semver` | — | `require`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 起こす状態（会話ごと）と起こす記録の読み書き。世代は新しい時刻でだけ書き換え、同じ応答の取り合いを `contested` にする。今の起動の記録（`launch.json`）に起こす hook の登録を渡したか（`rewake`）を持つ | 標準ライブラリ | — | `read_keep`・`bump_generation`・`update_keep`・`append_cache_log`・`rewake_on`（新設）・`set_launch`（`rewake` の引数を足す）、定数 `KEEP_PREFIX`・`CACHE_LOG`・`SESSION_ID_RE` |
| `src/ndf/domains/relay/wrapper.py` | 変更 | `_start` の版の確かめ（`cli_floor.require`）の版が `cache.rewake` の `versions` のどれかと完全に一致し、利用者の `--settings` が無く、`registration.rewake_unreachable()` が None なら、子の起動の引数に `--settings <rewake_settings の JSON>` を、子の環境に `NDF_RELAY_REWAKE=1` を足す | `signals`・`adapters.cli_floor`・`hooks.registration`（`rewake_settings`） | `Wrapper` | `_start`（変更） |
| `src/ndf/domains/relay/cli_cache.py` | 新設 | `ndf relay cache report` | `keep_cache`・`signals` | モジュール `cli_cache` | `report`・`run`（新設） |
| `src/ndf/domains/relay/cli.py` | 変更 | サブコマンド `cache report` を足す | `cli_cache` | — | `build_parser`（変更） |
| `contracts/limits/<方針の名前>.json`・`contracts/schemas/limits.schema.json`・`src/ndf/contracts/limits.py` | 新設（方針のファイル）・変更 | 方針のファイル `cache.wake`・`cache.write-ratio`・`cache.read-ratio`・`cache.output-ratio`・`cache.wake-cost`・`cache.hit`・`cache.handoff`・`cache.rewake`・`hook.budget.stop-rewake`（どれも `contracts/limits/` に 1 方針 1 ファイル）を足し、スキーマと `Policy` に `ratios`・`default`・`ratio`・`versions`・`calls`・`start` の項目を足す | — | `Policy` | `texts`（新設）・`value`（数でない値で `LimitsError`） |
| `contracts/hooks/runtimes.json` | 変更 | claude の Stop に `async_rewake`（予算 `stop-rewake`・背景の登録だけの `timeout_max` 3,300）を足す。プラグインの登録の生成には使わず、`rewake_entry` と予算の検査が読む | — | — | — |
| `contracts/domains/ndf-files.json`・`env.json` | 変更 | `ndf-files.json` に `relay-keep`（`<名前>/keep-<会話>.json`）・`relay-cache-log`（`<名前>/cache-log.jsonl`）の行。どちらもラッパーの保存先と同じ掃除の対象。`env.json` に `NDF_RELAY_REWAKE`（ラッパーが起こす hook の登録を渡した子にだけ `1` で渡す。起こす hook が読む）の行を足す | — | — | — |
| `contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` | 変更 | `cache report` の引数と結果 | — | — | — |
| `hooks/claude.json`・`docs/contracts/*.md` | 変更（再生成） | `ndf hooks render`・`ndf check --write-spec` で作り直す | — | — | — |
| `skills/development-workflow/references/relay.md` | 変更 | hook の一覧に起こす hook、記録の種類に起こす記録、見直しの手順（20 件ごと）を足す | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更（`glossary.md` は再生成） | 要求の用語 7 語と設計の 2 語を足す | — | — | — |

## 判定（`keep_cache`）

純粋な関数で、時刻・値・記録の文字を引数で受ける。

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `last_turn` | `src/ndf/domains/relay/keep_cache.py` | 新設 | `last_turn(tail: str, last_message: Optional[str], started: float) -> TurnRead` | `tail` は `read_tail` の文字列。中で `last_context(tail, Reply(last_message))` を 1 回だけ呼び、状態から `TurnRead` への対応と項目の取り出し元は #4089 の詳細設計の「#4068 との受け渡し」の表に従う（今回の応答が未記録・読めない（空の文字列）なら `TurnRead(None, "unrecorded")`。照らした応答の形が違えば、前の行へさかのぼらずに `TurnRead(None, "form")`。そろい、その応答の時刻が `started`（hook の動き始めの時刻）より後なら `TurnRead(None, "newer")`。そろえば `TurnRead(LastTurn, None)`）。寿命は応答の寿命別の書き込みだけで決め（1 時間が正なら `1h`、1 時間が 0 で 5 分が正なら `5m`）、両方 0 なら `ttl` を None（未定）で返す。状態の寿命は受けない（排他の外で読んだ状態を補いに使わない。設計の決定 13）。補うのは `_settle` だけである。実測 9 で Stop の入力に応答の ID が載ると分かれば、文と時刻の代わりにその ID と `message.id` で照らす |
| `wake_reply` | 同上 | 新設 | `wake_reply(tail: str, after_id: str) -> Reply` | `tail` は `last_turn` と同じ `read_tail` の文字列（読み直さない）。`after_id` の応答の直後にある最初の入力の行だけを見る（Tool の結果の行は入力に数えない）。起床の文の形（設計の決定 7 の実測 8 で決めた形を `is_wake_input` に閉じる）なら、その次から次の入力の手前（無ければ末尾）までの `assistant` の `message.id` をその起床の呼び出しとし、`Reply("wake", <最初の呼び出しの読み出し>, <全呼び出しの Usage>)`。最初の呼び出しの書き込みが両方 0 でも `wake` で返す。利用者の入力か背景の作業の知らせの形なら、その先を探さずに `Reply("preempted", None, None)`。どちらとも決められないなら `Reply("unknown", None, None)`。`after_id` の応答が `tail` に無い（起床の後の大きな Tool の結果で、読み取りの上限の外へ押し出された）なら `Reply("truncated", None, None)`。`after_id` が `tail` に在れば、その後の行は末尾まで連続して `tail` に在るので、途中の呼び出しだけが欠けることは無い |
| `opening` | 同上 | 新設 | `opening(tail: str, message_id: str) -> Opening` | `message_id` の応答を始めた入力（さかのぼって最初の、Tool の結果でない入力の行）と、その入力の直後の最初のモデル呼び出しを返す。`kind` はその入力に `is_wake_input` を打った値から `wake`（起床の文）・`normal`（通常の入力）・`unknown`（決められない・入力の行が `tail` に無い）。`normal` のときだけ `at`（入力の行の `timestamp` の UNIX 秒）と `first_read`（最初の呼び出しの読み出し）を持つ。`_settle` が待ちを明けるかと、`wait_end` の当たり・時刻を決めるのに使う。今回の応答の最後の呼び出しや Stop の時刻は使わない |
| `is_wake_input` | 同上 | 新設 | `is_wake_input(line: Mapping[str, Any]) -> Optional[bool]` | 入力の行 1 つが起床の文なら真、通常の入力なら偽、決められなければ None。実測 8 の形だけを知る関数で、実測の前は常に None（版の門も閉じている） |
| `ratios` | 同上 | 新設 | `ratios(get: Callable[[str, str], Any], model: str, ttl: str) -> Ratios` | `get(name, key)` で上限の設定の値を読む（hook は `tables.policy_value` を渡す。集計は読まない）。`cache.write-ratio` の `ratios`（辞書）から待ちの寿命の `w` と、`1h`・`5m` の `w_1h`・`w_5m`、`cache.read-ratio` の `ratios`（辞書。表に無いモデルは `default`）から `r`、`d`・出力の倍率・余白・確かめる間隔・当たりの境、`cache.handoff` の `calls`・`count`・`start`。辞書でない・数でない値は `tables.TableError`。確かめる間隔（`cache.wake.interval`）が 0 以下か、余白（`cache.wake.seconds`）が 0 未満か 1 時間の寿命（`TTL_SECONDS["1h"]`）以上でも `tables.TableError`（スキーマの `interval`・`seconds` はほかの方針と共通で 0 を許すので、ここで拒む。`_wait` が休まずに世代を読み続けない） |
| `wake_at` | 同上 | 新設 | `wake_at(at: float, ttl: str, margin: float) -> Optional[float]` | `at + TTL_SECONDS[ttl] − margin`。`ttl` は `_settle` が補った寿命（未定のまま呼ばない）。`margin` が寿命以上なら None（5 分の寿命は起こさない） |
| `n_max` | 同上 | 新設 | `n_max(tokens: int, q: Ratios) -> int` | `floor((w − r) × S / (r × S + d))`。0 未満にしない |
| `handoff_fits` | 同上 | 新設 | `handoff_fits(spent: float, tokens: int, q: Ratios) -> bool` | 引継ぎの起床の費用 `C_h(S) = calls × r × S + count + w × start` として、`spent + C_h(S) <= (w − r) × S`（`S` は判定の時の `tokens`）。待ちの始まりの `S` は使わない（設計の決定 5） |
| `hit` | 同上 | 新設 | `hit(read: int, before: int, ratio: float) -> bool` | `read >= before × ratio`。`read` は起床か待ちを明けた入力の最初の呼び出しの読み出し |
| `next_step` | 同上 | 新設 | `next_step(k: int, limit: int, spent: float, monitors_only: bool, tokens: int, decide_from: int, q: Ratios) -> Step` | `k + 1 > limit` → `stop`。`monitors_only` かつ `tokens >= decide_from` かつ `handoff_fits(spent, tokens, q)` で、`k + 1 == limit` か `not handoff_fits(spent + r × tokens + d, tokens, q)`（もう 1 回起こすと収まらない）なら `end_wait`。ほかは `wake`。`limit` は待ちの始まりの `S` で決めた状態の `n_max`、`spent` は状態の累計、`tokens` は判定の時の（今回の応答の）`S` |

### 内部のデータと定数

| 名前 | 種別 | 置き場所 | 値・形 |
| --- | --- | --- | --- |
| `LastTurn` | 型（`NamedTuple`） | `keep_cache.py` | `message_id`・`at`（`timestamp` の UNIX 秒）・`tokens`（入力・読み出し・書き込みの合計）・`ttl`（`1h`・`5m`・None（未定。`_settle` が補う））・`model`・`cache_read`・`cache_write`・`output` |
| `TurnRead` | 型（`NamedTuple`） | 同上 | `turn`（`LastTurn` か None）・`reason`（`unrecorded`・`form`・`newer` か None） |
| `Reply` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`preempted`・`unknown`・`truncated`）・`first_read`（起床の最初の呼び出しの読み出し。当たりの判定に使う。`wake` のときだけ）・`usage`（`Usage`。起床に属する全呼び出しの合計。`wake` のときだけ） |
| `Usage` | 型（`NamedTuple`） | 同上 | `calls`（呼び出しの数）・`cache_read`・`output`（どれも合計）・`cache_write_1h`・`cache_write_5m`（寿命別の書き込みの合計。`priced` が偽なら None）・`priced`（真偽。全呼び出しで `cache_creation` の `ephemeral_1h_input_tokens`・`ephemeral_5m_input_tokens` が 0 以上の整数で在り、2 つの和が `cache_creation_input_tokens` と同じとき真。1 つでも欠ける・整数でない・負・和が違えば偽で、推し量らない） |
| `Opening` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`normal`・`unknown`）・`at`（待ちを明けた入力の行の時刻）・`first_read`（その入力の直後の最初の呼び出しの読み出し） |
| `Ratios` | 型（`NamedTuple`） | 同上 | `w`（待ちの寿命の書き込みの倍率）・`w_1h`・`w_5m`・`r`・`d`・`out`・`margin`・`interval`・`hit`・`h_calls`・`h_count`・`h_start`（`cache.handoff`） |
| `Step` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`end_wait`・`stop`）・`reason` |
| `TTL_SECONDS` | 定数 | 同上 | `{"1h": 3600, "5m": 300}`（公式の寿命。値段ではないので上限の設定に置かない） |
| `WAKE_HEAD` | 定数 | 同上 | `【ndf-relay】`（起こす文と引継ぎへ切り替える文の頭。起きた応答の見分けに使う） |
| `WAKE_TEXT`・`END_WAIT_TEXT` | 定数 | 同上 | 設計の「処理の流れ」の 2 つの文 |

### 異常系の処理

| 関数 | 失敗の種類 | 原因 | 検出する場所 | 検出したときの処理 | 利用者への通知 | 回復の手段 |
| --- | --- | --- | --- | --- | --- | --- |
| `last_turn` | 最後の応答が載っていない・記録が読めない | Stop の時点で未記録（PRD3 の版）・`read_tail` が空の文字列を返した（権限・ファイルが無い） | `last_context` の状態（`behind`・`none`（`empty` など）） | `reason` `unrecorded` | 無し（呼び手が `stop_waking`（`scope` `once`）を会話で 1 度書く。止めない） | 次の Stop で読み直す |
| `last_turn` | 形が違う | Claude Code の記録の形の変化 | `last_context` の `form`・#4089 の表の項目の型 | `reason` `form` | 無し（呼び手が `stopped` と `stop_waking`（`scope` `session`）を 1 度書く） | 読み方を直す |
| `last_turn` | 次の応答を読んだ | 遅れて動いた古い hook | 応答の時刻と `started` の比べ | `reason` `newer` | 無し（呼び手は何も書かずに 0） | — |
| `wake_reply` | 起床の呼び出しが末尾の範囲に無い | 起床の後の大きな Tool の結果が `hook.transcript-tail` の大きさを越えた | `after_id` の応答が `tail` に無い | `kind` `truncated` | 無し（呼び手が `hit` null・`priced` 偽の `wake_result` を書いて印を消す。止めない） | — |
| `wake_reply` | 寿命別の書き込みが読めない | 記録の形の変化・和の食い違い | 各呼び出しの `cache_creation` の照らし | `Usage.priced` 偽 | 無し（集計が費用から外して `unpriced_waits` に数える） | 読み方を直す |
| `ratios` | 行が無い・値が数でない・間隔が 0 以下・余白が範囲の外 | 上限の設定の壊れ・利用者の宣言 | 型と範囲の照らし | `tables.TableError` を投げる | 無し（呼び手が起こさない） | 上限の設定を直す（`ndf check` が落とす） |

### 単体テストの観点

| 関数 | 観点 |
| --- | --- |
| `last_turn` | 実測の形の見本を読める。最後の `assistant` の行に `usage` が無い見本では、前の行が完全でも `form`。完全な前の応答だけが残り `last_message` が違う見本では `unrecorded`。`last_message` が None でも `unrecorded`。同じ文で時刻が `started` の 1 秒後なら `newer`、`started` と同じなら読める。空の文字列（`read_tail` が読めなかった）で `unrecorded`。寿命: 1 時間が正で `1h`、5 分だけ正で `5m`、両方 0 で None（引数に状態の寿命を取らない）、`cache_creation` が無い・負で `form` |
| `wake_reply` | 起床の文の後の応答を返す。Tool を 2 回使った起床の見本では `first_read` が最初の呼び出しの読み出し、`usage.calls` が 3 で合計が 3 つの和、Tool の結果の行で区切らない。1 回目が 1 時間へ 300・2 回目が 5 分へ 1,000 書いた見本で `cache_write_1h` 300・`cache_write_5m` 1,000・`priced` 真。最初の呼び出しの書き込みが両方 0 の見本でも `wake` で `first_read` を返す。どれかの呼び出しの寿命別の項目が欠ける・整数でない・負・和が合計と違う見本で `priced` 偽・2 つの合計が None。起床の後に利用者の入力が続く見本では、その入力の後の応答を含めない。印の直後の最初の入力が利用者の入力の見本では、その後に起床の文の応答があっても `preempted`。最初の入力の形を決められない見本では `unknown`。引継ぎの起床で 2 回目の Tool の結果が `tail` の大きさを越え、`after_id` の応答が `tail` の外へ押し出された見本では `truncated`。同じ大きな結果でも `after_id` が `tail` に残る大きさでは `wake` で 3 つの和 |
| `opening` | 利用者の入力で始まり Tool を使った応答で `normal`（Tool の結果の行を飛ばす）、`at` が入力の行の時刻、`first_read` が入力の直後の最初の呼び出しの読み出し（最初の呼び出しの読み出し 20,000・後の Tool の呼び出しの読み出し 101,000 の見本で 20,000）。起床の文で始まった応答で `wake`。形を決められない入力・入力の行が `tail` に無い見本で `unknown` |
| `ratios` | `cache.wake` の `interval` を 0・-1 にした見本、`seconds` を -1・3,600 にした見本で `tables.TableError`（hook は眠らずに 0）。`interval` 60・`seconds` 600 で通る |
| `n_max` | Opus 5.5・`1h`・`d` 2,000 で `S` 400,000 → 35、500,000 → 36、20,000 → 13。読み出し 0.1 で 400,000 → 18 |
| `wake_at` | `1h`・余白 600 で `at + 3000`、`5m` で None |
| `handoff_fits` | Opus 5.5・`1h`・`d` 2,000・`calls` 4・`count` 25,000・`start` 60,000 で、`S` 250,000・`spent` 292,500 → 真（487,500 ちょうど）、292,501 → 偽。`count` を 400,000 にすると `spent` 0 でも偽 |
| `next_step` | 同じ値・`limit` 33 で、`tokens` 250,000 のまま `k` 19・`spent` 275,500 → `wake`、`k` 20・`spent` 290,000 → 監視だけ・`decide_from` 250,000 なら `end_wait`・`tokens` 249,999 なら `wake`。`tokens` が起床ごとに 300 増える列（`k` 20・`spent` 292,850・`tokens` 256,000）でも `end_wait`、`k` 19・`spent` 278,065・`tokens` 255,700 は `wake`。`k` 32 → `wake`、`k` 33 → `stop`。作業の子は `k` 20 でも `wake`。`count` 400,000 なら監視だけでも `k` 20 で `wake`。`limit` 0 → `stop`（純粋な関数の境界。`limit` を式から作る組の試験は設計のテスト設計の 5） |

## 起こす hook（`hooks/rewake.py`）

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `run` | `src/ndf/domains/hooks/rewake.py` | 新設 | `run(argv: list[str], stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr, environ: Mapping[str, str] = os.environ, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> int` | 終了コード。起こすなら標準エラーへ文を書いて 2、ほかは 0。例外はすべて 0（Stop を壊さない）。最初に `clock()` を打ち、その値を動き始めの時刻（`started`）として `Target` に持たせる。`started` は Claude Code が hook を起動した時刻より遅れ得る（インタプリタの立ち上がり）ので、今回の応答の照らしは `started` だけに頼らず、`_settle` の取り合いの照らしを重ねる |
| `_target` | 同上 | 新設 | `_target(payload: Mapping[str, Any], environ: Mapping[str, str]) -> Optional[Target]` | 最初に `environ` の `NDF_RELAY_REWAKE` が `1` かを確かめ、違えば None（ラッパーが登録を渡した起動でだけ渡す変数。設計の決定 7。登録の無い起動から入口を直接呼んでも眠らない）。続いて、中継の保存先（`NDF_RELAY_DIR`）・起動の ID・会話の ID・`transcript_path` がそろい、入力の `session_id` が #4049 の `signals.relay_session(environ)`（`NDF_RELAY_SESSION`）と同じで、会話の ID が UUID の形（`SESSION_ID_RE`）のときだけ `Target`（`background_tasks` を #4049 の `task_kind` で分けた作業と監視の件数、`last_assistant_message`、起動のたびに作る `hook_id`（`uuid4().hex`）・`started` を持つ）。ほかは None（入れ子の claude は `--settings` の登録を受け継がないのでこの hook が起動しない。起動しても `session_id` が違うので通らない） |
| `_settle` | 同上 | 新設 | `_settle(t: Target, turn: LastTurn, tail: str, q_for, clock) -> Settled` | 子を止める排他を取り、その中で次を 1 つの区間で、この順に行う。(1) `signals.current_launch` が `t.launch` でなければ、世代も含めて何も書かずに `Settled(stale=True)`。(2) `bump_generation(Generation(at=turn.at, by=t.hook_id, message_id=turn.message_id))`。より新しい世代か同じ時刻の先の書き手があれば、何も書かずに `stale`。世代の `message_id` が `turn.message_id` と同じで書き手が別なら、世代の `by` を `contested` に替えるだけで、ほかは何も書かずに `stale`（同じ応答の取り合い。どちらの hook も起こさない。設計の決定 4）。(3) 状態を読み直し、寿命を決める。`turn.ttl` が在ればそれ、None なら状態の `ttl`。それも無ければ寿命は未定で、(5) と (6) の待ちの始まりを書かない。決まった寿命は状態の `ttl` に書き、未定なら `ttl` を書き換えない。以下の `turn` は寿命を補った値で、`Settled.turn` に入れて返す。(4) 起こした印があれば `wake_reply(tail, <印の ID>)` で照らす。`wake` なら `wake_result`（`hit` は `hit(Reply.first_read, <印の S>, q.hit)`、`first_read`・`calls`・`cache_read`・`cache_write_1h`・`cache_write_5m`・`output`・`priced` は `Reply`、`w_1h`・`w_5m`・`r`・`out` は待ちの寿命とモデルの倍率、`text` は印から）を書いて、当たらなければ `stopped`。`preempted` なら判じずに印を消す。`unknown` なら判じずに印を消して `wait_stop` を「起床を照らせない」にする。`truncated` なら `hit` null・`priced` 偽の `wake_result` を書いて印を消し、`stopped` も `wait_stop` も書かない。(5) 待ちがあれば `opening(tail, turn.message_id)` を打つ。`kind` が `normal` のときだけ待ちを明ける。`wait_end` の `tokens`・`ttl`・`model` は更新前の状態の `last`、`w`・`r` はその寿命とモデルの倍率、`hit` は `hit(Opening.first_read, last.tokens, q.hit)`、`cache_read` は `Opening.first_read`、`opened_at` は `Opening.at`、`age` は `Opening.at − last.at` で書き、`wait_id`・`k`・`n_max`・`spent`・`wait_stop` を消す。`unknown` でも、状態の `opened`（guard が待ちの間の通常の入力で書いた時刻）が `last.at` より後なら待ちを明ける。そのときの `wait_end` は `opened_at` を `opened`、`age` を `opened − last.at`、`hit` と `cache_read` を null にし（費用と当たりの証拠が無いので推し量らない）、ほかは `normal` と同じに書いて同じ項目を消す。待ちを明けたら `opened` も消す。`wake` と、`opened` の無い `unknown` なら、今の Stop が待つ子でなくても（背景の作業が 0 件でも）待ちを明けず、`k`・`n_max`・`spent`・`wait_stop` を保つ（(4) で立てた「起床を照らせない」も消えない。背景の件数より、今回の応答を始めた入力の照らしを先にする）。(6) 待つ子で待ちが無く、(3) で寿命が決まったときだけ、`wait_id` を世代の時刻、`k` と `spent` を 0、`n_max` を式の値にして `wait_start` を書く。寿命が未定なら、`noted` に「寿命がまだ分からない」が無いときだけ `stop_waking`（`scope` `once`）を書いて `noted` に足し、待ちを始めない。(5) で待ちを明けなかった Stop では、待ちが残るので書かない。(7) 最後に `last` を `turn` にする（`wait_end` を書いた後。寿命が未定なら `last.ttl` は None で、待ちは無い）。新しい状態・補った `turn`・起こさない理由を返す |
| `_wait` | 同上 | 新設 | `_wait(t: Target, gen: Generation, until: float, interval: float, clock, sleep) -> bool` | `until` まで `interval` ごとに世代だけを読み、自分のままなら真。会話の記録は読まない |
| `_finish` | 同上 | 新設 | `_finish(t: Target, gen: Generation, turn: LastTurn, stderr: TextIO, clock) -> int` | 子を止める排他を取り、その中で 世代（`by` が自分か。`contested` なら外れ）・起動・質問のファイル を照らし、外れたら何も書かずに 0。そろえば状態を読み直して `next_step(k, n_max, spent, monitors_only, turn.tokens, decide_from, q)` で手を決める。`stop` なら `wait_stop` を「上限」にして `stop_waking`（`scope` `wait`）を書いて 0。`wake`・`end_wait` なら設計の決定 6 の順で (1) 中継の入力の印（決定 10 でマーカーの形のとき） (2) 起こした印（`wake_id`）・`k + 1`・`spent + r × turn.tokens + d`・世代（起こした時刻）・`end_wait` なら `stopped` に「引継ぎを求めた」 (3) `wake` の行 を書き、文を出して 2 |

内部の型（どれも `NamedTuple`）:

| 型 | 項目 |
| --- | --- |
| `Target` | `relay_dir`・`launch`・`session_id`・`transcript`・`works`・`monitors`・`last_message`・`hook_id`・`started` |
| `Generation` | `at`・`by`（`contested` は取り合いの印）・`message_id`（起こす hook の Stop が取った応答。guard と起床は None） |
| `Settled` | `keep`・`turn`（寿命を補った `LastTurn`）・`stale`・`reason` |

### 処理の手順

1. `_target` が None なら 0（受け入れ条件 2・3）
2. 状態を読む（排他を取らない読み取り）。状態に待ちも起こした印も無く、`works + monitors` が 0 なら、会話の記録を読まずに 0（受け入れ条件 9）。眠る hook も無いので世代を書く要が無い
3. 会話の記録の末尾を `transcript.read_tail(t.transcript, tables.policy_value("hook.transcript-tail", "count"))` で 1 回だけ読み、`last_turn(tail, t.last_message, t.started)` を打つ（読み取りの上限は利用者が直した宣言の値がそのまま効く。この `tail` を `_settle` の `wake_reply`・`opening` にも渡し、読み直さない）。`newer` なら何も書かずに 0（遅れて動いた古い hook。受け入れ条件 2）。`unrecorded`（記録が読めず空の文字列が返ったときも含む。`read_tail` は `OSError` を投げない）なら、起こさず止めずに 0。その理由を `noted` に持たなければ、排他の中で照らしてから `stop_waking`（`scope` `once`）と `noted` を書く（受け入れ条件 7）。`form` なら、排他の中で照らしてから、書いていなければ `stopped` と `stop_waking`（`scope` `session`）を書いて 0
4. `_settle` を打つ。`stale` なら 0（受け入れ条件 2。取り合いも含む）。`stopped` か `wait_stop` があれば 0（受け入れ条件 4・5・8）。待つ子でなければ 0。寿命が未定（`Settled.turn.ttl` が None）なら 0（`_settle` が `stop_waking` を 1 度書いている。#4199 の受け入れ条件 4）
5. `wake_at(Settled.turn.at, Settled.turn.ttl, q.margin)` が None なら 0。`wake_at` が今の時刻以前なら、起こさず止めずに 0（手順 3 と同じく `once` を 1 度）。`_wait` が偽なら 0（受け入れ条件 1・2）
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
- 世代は時刻・書き手の ID・取った応答の `message_id` の組で、時刻が状態のものより新しいときだけ書き換える（`bump_generation`）。同じ時刻なら先に書いた書き手を残す。起こす hook の Stop が、世代の `message_id` と同じ応答を別の書き手として取りに来たら、`by` を `contested` に替える（同じ応答を 2 つの hook が取り合ったので、どちらも起こさない。眠る hook は `_wait` で替わったのを見て終わる）。時刻の比べは、どれも同じ機械のシステムの時計（記録の `timestamp` は `timestamp_seconds` で UTC の UNIX 秒に直す）どうしで行う（設計の決定 6）。時刻の取得元は次の 3 つである（設計の決定 6）。hook が動いた時刻は使わない
  - 起こす hook の Stop: 今回の応答と確かめた行の `timestamp`
  - UserPromptSubmit の guard: guard の `clock()`
  - 起床: 排他の中の `clock()`
- 待つ子かは Stop の入力の `background_tasks` を自分で分けて決める。同じ Stop で動く #4049 の同期の guard（`relay-marker`）が書く記録のファイルは読まないので、2 つの hook の実行の順に依らない
- 眠る hook は世代のファイルを `interval` ごとに読むだけで、排他を取らない（読み違えても、起こす直前に排他の中で照らし直す）

### ログと記録の出力

| 出来事 | 出力先 | 項目 |
| --- | --- | --- |
| 待ちの始まり | `cache-log.jsonl` | `kind: wait_start`・`wait_id`・`tokens`・`ttl`・`model`・`n_max` |
| 起こした | 同上 | `kind: wake`・`wait_id`・`wake_id`（`uuid4().hex`。状態の `pending` にも置く）・`tokens`・`age`・`k`・`n_max`・`spent`（この起床を足した後）・`text`（`wake`・`end_wait`） |
| 起きた応答 | 同上 | `kind: wake_result`・`wait_id`・`wake_id`・`text`（印から）・`hit`（最初の呼び出しの読み出しで決める。`truncated` なら null）・`tokens`（印の `S`）・`first_read`・`calls`・`cache_read`・`cache_write_1h`・`cache_write_5m`・`output`（起床に属する全呼び出しの合計。`priced` が偽なら `cache_write_1h`・`cache_write_5m` を書かない）・`priced`・`model`・`ttl`（待ちの寿命）・`w_1h`・`w_5m`・`r`・`out` |
| 待ちの終わり | 同上 | `kind: wait_end`・`wait_id`・`opened_at`（待ちを明けた入力の行の時刻）・`age`（`opened_at − last.at`）・`k`・`hit`（待ちを明けた最初の呼び出しの読み出しが `tokens` の `cache.hit.ratio` 以上か）・`cache_read`（その読み出し）・`tokens`・`ttl`・`model`（どれも更新前の状態の `last`。待ちの終わりの直前の応答）・`w`・`r`（`last.ttl` の書き込みの倍率と `last.model` の読み出しの倍率）。待ちを明けた応答の書き込み・出力は書かない（設計の決定 15） |
| やめた・一時的に起こさなかった | 同上 | `kind: stop_waking`・`wait_id`・`reason`・`scope`（`session`・`wait`・`once`）・`k`・`n_max` |

どの行も `at`・`launch`・`session_id` を持つ。ラッパーの `log.jsonl` には書かない（書き手を 1 つにする）。

### 既存の部品の再利用

| 新設するもの | 見つけた既存の部品 | 見つけ方 | 使う・使わない理由 |
| --- | --- | --- | --- |
| 会話の記録の末尾の読み取りと最後の応答 | #4089 の `adapters/transcript.read_tail`・`last_context` | #4089 の詳細設計の「#4068 との受け渡し」 | そのまま使う。末尾は `read_tail` を `hook.transcript-tail` の宣言の大きさで 1 回だけ呼び、`last_turn`・`wake_reply`・`opening` は同じ文字列を受ける。固定長の `agents.tail` は使わない（利用者が直した読み取りの上限が効かなくなる）。読めないときの扱いも `read_tail` の空の文字列に従う |
| 背景の作業の分け方 | #4049 の `relay_busy.task_kind`・`work_count` | #4049 の詳細設計 | そのまま使う |
| 中継のマーカー | `markers.mark` | `grep -n "def mark" src/ndf/domains/relay/markers.py` | マーカーの形のときに使う |
| 排他・起動の照らし | `signals.held`・`STOP_LOCK`・`current_launch` | #4049 の詳細設計 | そのまま使う |
| 版の確かめ | `adapters/cli_floor.require` | `grep -n "cli_floor" src/ndf/domains/relay/wrapper.py` | 確かめに使った版を返すよう変えて、`_start` が打つ結果の版を使う（「ラッパー」の節） |
| 対象の会話の限定 | #4049 の `signals.relay_session`・`SESSION_ENV` | #4049 の詳細設計 | そのまま使う。会話の ID の形は `SESSION_ID_RE`（`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`）を `signals` に足し、`keep-<会話>.json` の名前を作る前に照らす |

### 単体テストの観点

| 関数 | 観点 |
| --- | --- |
| `run` | `NDF_RELAY_REWAKE` の無い環境（登録の無い起動から入口を直接呼んだ）では、中継の保存先・起動・会話の ID がそろっていても、眠りの関数 0 回・状態と記録の読み取り 0 回で 0。中継の保存先の変数なしで眠りの関数 0 回・記録の読み取り 0 回で 0。`session_id` が `NDF_RELAY_SESSION` と違う入力・UUID の形でない会話の ID では、状態を読まず書かずに 0。状態も待ちも無い Stop で読み取り 0 回。待つ子で 3,000 秒後に 2、標準エラーに起こす文。眠る間に新しい Stop・UserPromptSubmit で世代が替わると 0。古い応答の時刻を持つ Stop の hook が guard の後に動くと、世代を戻さず何も書かずに 0。質問のファイルが在ると 0。2 つの hook を同じ時刻まで眠らせても 2 は 1 つ。完全な前の応答だけが残り `last_assistant_message` が違う入力では、眠りの関数 0 回・`stopped` 無しで 0。起こす時点が過去になる見本でも 0。作業 1 件の Stop の hook の `started` の後に、作業 0 件の Stop の同じ文の応答が記録された見本では、古い hook が世代も状態も書かずに 0 で、新しい hook が作業 0 件で決める（その応答は起床の文で始まったので、待ちを明けず眠らない）。古い hook の `started` をその応答の時刻より後にずらした見本（動き始めが遅れた）では、古い hook と新しい hook のどちらを先に流しても世代が `contested` になり、2 は 0 個。末尾の読み取りは `read_tail` を 1 回だけ呼び、大きさは差し替えた `hook.transcript-tail` の値 |
| `_settle` | 起こす文から始まった応答の最初の呼び出しの読み出しが前の `S` の 0.89 倍で `stopped`、0.9 倍で `hit`。最初の呼び出しの書き込みが両方 0 でも `wake_result` が 1 行で、`hit` は読み出しで決まる。Tool を使った引継ぎの起床で `wake_result` の `cache_read`・`cache_write_1h`・`cache_write_5m`・`output` が全呼び出しの合計で、`w_1h`・`w_5m` を持つ。寿命別の項目の欠けた呼び出しを含む起床で `priced` 偽・2 つの合計が無い。`wake_reply` が `truncated` の見本で `hit` null・`priced` 偽の `wake_result` が 1 行、印が消え、`stopped` も `wait_stop` も無い。印の直後の入力が通常の入力なら判じない。形を決められなければ判じず `wait_stop` を立て、同じ Stop で `wait_end` も `wait_start` も書かず、`k`・`n_max`・`wait_stop` が残る。その Stop の作業も監視も 0 件（待つ子でない）でも同じで、`wait_end` を書かない。続く Stop で `opening` が `wake`・`unknown` の間も、背景の件数に依らず残り、`normal` の Stop で初めて `wait_end`。通常の入力で始まった待ちの後の Stop で `wait_end` が 1 行で、`tokens`・`ttl`・`model`・`w`・`r` が更新前の `last`（1 時間・Opus 5.5・100,000 の `last` と、5 分へ書いた別のモデルの今回の応答の見本で 100,000・`1h`・Opus 5.5・2.0・0.05）、`cache_read` と `hit` が待ちを明けた最初の呼び出し（最初が 20,000・後の Tool の呼び出しが 101,000 の見本で 20,000・偽）、`opened_at` が入力の行の時刻（Stop の時刻でない）。`wait_end` の後に `last` が今回の応答になる。`wait_end` の後の待つ子の Stop で `k` 0・`spent` 0・`n_max` を決め直した `wait_start`。寿命: `turn.ttl` が None で状態の `ttl` が `1h` なら `1h` で待ちを始め、状態にも無ければ `wait_start` も `n_max` も無く、`stop_waking`（`once`）が会話で 1 行、状態の `ttl` は書き換わらない。`turn.ttl` が `1h` なら状態の `ttl` が `5m` でも `1h`。排他の外の読み取りの後に別の hook が状態の `ttl` を `1h` にした順で、`_settle` は `1h` で補う。同じ `message.id` の世代を別の hook が持つ状態で打つと、世代の `by` が `contested` になり、ほかは何も変わらない。新しい世代がある状態で打つと、`pending`・`wait_stop`・起こす記録のどれも変えない。`current_launch` が別の起動の状態で打つと、世代も含めて何も変えない（世代が新しい時刻でも） |
| `_finish` | 起こすたびに `spent` が判定の時の `S` の `r × S + d` だけ増える。`end_wait` の後の Stop で、次の待ちでも起こさない。世代が `contested` の状態で眠りから戻ると、何も書かずに 0。上限の `stop` は `wait_stop` だけを書き、`stopped` を書かない。新しい世代がある状態で眠りから戻ると、上限でも `wait_stop`・`stop_waking` を書かない。マーカーが書けない順で状態も記録も変わらず 0、状態が書けない順でマーカーが消えて 0 |

## hook の登録（`registration`・`runtimes.json`・`entry.py`）

起こす hook はプラグインの登録（`hooks/claude.json`）に足さず、ラッパーが中継の子の起動にだけ `--settings` で渡す。中継の外の会話の Stop では、この hook のプロセスは起動しない（設計の決定 1）。

- `runtimes.json` の claude の `Stop` に `"async_rewake": {"budget": "stop-rewake", "timeout_max": 3300}` を足す。`timeout_max` は背景の登録だけの上限で、事象の `timeout_max`（同期の Stop の登録の上限。ランタイムの 600）は変えない。この行は `render` が読まず、`rewake_entry` と予算の検査だけが読む
- 方針のファイル `contracts/limits/hook.budget.stop-rewake.json` を今の `hook.budget.*` の形（`p95_ms`・`deadline`・`margin`・`timeout`・`inject_chars`）で置く。値は次のとおりである。3,300 秒を受け付けない版では、設計の決定 7 のとおり版の範囲に入れない

  | 項目 | 値 |
  | --- | --- |
  | `timeout` | 3,300 秒（起こす時点の最大 3,000 秒 + 確かめる間隔 60 秒 + 余り 240 秒） |
  | `deadline` | 3,300 |
  | `margin` | 240 |
  | `p95_ms` | 500（待つ子でない Stop で終わるまで） |
  | `inject_chars` | 1,000（起こす文の長さの上限） |

- `budget` に `rewake_budget(runtime, event, limits=None) -> Optional[Budget]` を足す。事象の行（`tables.event_spec(runtime, event)` の `spec`）の `async_rewake.budget` から、`tables.policy(f"hook.budget.{spec['async_rewake']['budget']}")` で引く（式の綴りは「読み手の突き合わせ」の動的な読み口の `expr` と同じにする）。`async_rewake` の無い事象は None
- `registration.rewake_entry(runtime: str, event: str, plugin_root: Path = tables.PLUGIN_ROOT) -> dict[str, Any]` を足す。返す値は次の形である
  - `{"type": "command", "command": <command>, "asyncRewake": true, "timeout": <rewake_budget の timeout>}`
  - `<command>` は今の command と同じ文で、プラグインの根を環境変数でなく plugin_root の絶対パス（shlex.quote）にし、末尾に rewake を足したもの

  `--settings` の hook にはプラグインの根の環境変数が渡らないので、絶対パスで書く。timeout は宣言（`contracts/limits/hook.budget.stop-rewake.json`）から読み、3,300 を埋め込まない。事象の行に `async_rewake` が無い・`rewake_budget` が None なら `tables.TableError`
- `registration.rewake_settings(plugin_root: Path) -> dict[str, Any]` を足す。返す値は `{"hooks": {"Stop": [{"hooks": [rewake_entry("claude", "Stop", plugin_root)]}]}}` である。`render` は変えない（プラグインの登録に `asyncRewake` は現れない）
- 検査: `budget_registrations` は、`async_rewake` を持つ事象に背景の行を 1 つ足す（登録のファイルからは読まない。`timeout` は `rewake_entry` が使うのと同じ `rewake_budget` の `timeout`）。`_file_registrations` は登録のファイルの登録ごとに `async_rewake`（登録の `asyncRewake` が真か）を行に足す。`_registration_errors` は、背景の行を `rewake_budget` の締め切りと `async_rewake.timeout_max` で照らす。ほかの行は今のとおり事象の予算と `timeout_max` で照らす。登録のファイルに `asyncRewake` が真の登録があれば、事象によらず破れとする（プラグインを入れた全ての会話で起動させない）。背景の行には、`rewake_reach` の診断も破れとして足す
- `budget.rewake_reach(budget: Budget) -> Optional[str]` を足す。`cache.wake` の `seconds`（余白）と `interval`（確かめる間隔）を `tables.policy_value` で読み、次のどれかなら理由の文（値と超えた秒を含む）を、どれでもなければ None を返す。`interval` が 0 以下。`seconds` が 0 未満か `TTL_SECONDS["1h"]` 以上。`TTL_SECONDS["1h"] − seconds + interval + budget.margin` が `budget.timeout` を越える（既定の 600・60・240 で 3,000 + 60 + 240 = 3,300 秒ちょうど。余白を 120 秒にすると 3,780 秒で越える）。`registration.rewake_unreachable()` は `rewake_budget("claude", "Stop")` を渡してこの値を返す。`cache.wake` の `read_by` に `ndf.domains.hooks.budget` を足す
- `entry.py` は `argv[3] == "rewake"` なら `call("ndf.domains.hooks.rewake", "run", argv)` の値を返す（ほかは今のとおり 0）

| 観点 | テスト |
| --- | --- |
| 中継の外で起動しない | `render("claude")` と生成した `hooks/claude.json` のどの事象にも `asyncRewake` の登録が無く、Stop の登録は 1 つ。`hooks/claude.json` の Stop に `asyncRewake: true` の登録を足すと `check_budget` が破れとする |
| 背景の登録 | `rewake_settings(<一時の根>)` の Stop の登録が 1 つで、`asyncRewake: true`・`timeout`（`hook.budget.stop-rewake` の値）を持ち、command が `<一時の根>/hooks/entry.py` の絶対パスと `rewake` を含み、`${CLAUDE_PLUGIN_ROOT}` を含まない。空白を含む根でも引数が 1 つに保たれる。`contracts/limits/hook.budget.stop-rewake.json` の値を差し替えると `timeout` が変わる。codex の登録は変わらない |
| 予算の検査 | 生成した登録で `check_budget` が空。同期の Stop の登録の timeout を 601 にすると破れ（上限 600 のまま）。背景の予算の timeout を 3,301 にすると破れ。背景の予算の timeout を同期の予算の締め切り未満にしても、背景の予算の締め切り以上なら破れない。`async_rewake` の行を消すと `rewake_entry` が `TableError`。`cache.wake` の `seconds` を 120 にすると `rewake_reach` が理由の文を返し、`check_budget` が破れとする。`interval` を 0 にしても同じ。既定の値では None |
| 入口 | `rewake` の引数で `run` の 2 がそのまま返り、`run` を読めなければ 0。`NDF_RELAY_REWAKE` の無い環境で `rewake` の引数を渡すと、眠りの関数 0 回・状態と記録の読み取り 0 回で 0（登録の無い起動から直接呼んだとき） |

## UserPromptSubmit の guard（`turn`）

- 中継の下の UserPromptSubmit で、子を止める排他の中で次の 2 つを行う。中継の下とは、中継の保存先と起動の ID（`NDF_RELAY_LAUNCH`）があり、入力の `session_id` が `NDF_RELAY_SESSION` と同じで、会話の ID が `SESSION_ID_RE` の形のときである
  1. `signals.rewake_on(<保存先>, <自分の起動の ID>)` が偽なら何も書かない。偽になるのは次の 2 つのときである
     - 今の起動（`launch.json`）が自分の起動でない（起動し直した後の旧い起動の guard が、新しい起動の予約を失効させない）
     - 今の起動に起こす hook の登録を渡していない（版の範囲の外の子で状態を作らない）
  2. `bump_generation(Generation(at=clock(), by="turn"))` を打つ（眠る hook の予約を失効させる）。状態のファイルが無い会話では、世代だけを持つ状態を作る。最初の待ちで背景の hook より先に入力が届いても、後から動く hook は入力より古い応答の時刻しか持たず、世代を取れない
  3. 状態に待ち（`wait_id`）が在り、起こした印（`pending`）が無ければ、同じ区間で `opened` にその `clock()` を書く（既に在れば書き換えない）。起こす文の入力は、必ず `_finish` が `pending` を書いた後に届くので、`pending` の無い待ちへの入力は通常の入力である。`pending` が在る間の入力は起床の文か利用者の割り込みかを決められないので書かず、`wake_reply`・`opening` の照らしに任せる。`opened` は、通常の入力の後の Tool の結果が大きく、次の Stop の `opening` が入力の行を末尾の窓の外へ押し出されて `unknown` を返しても、待ちを旧い `k`・`wait_stop` から切り離すために使う
- この guard はプラグインの登録の今の UserPromptSubmit の上で動くので、プロセスは増えない。状態のファイルを作るのは、起こす hook の登録を渡した中継の子だけである
- 起こす文の見分け: 設計の決定 10 の実測で、入力の `prompt` に起こす文が載ると分かれば、頭が `WAKE_HEAD` の入力を中継の入力として数え直さない。載らなければ今の `markers.take` のままにする

| 観点 | テスト |
| --- | --- |
| 失効 | 眠る hook の世代より新しい入力で世代が替わり、眠る hook が 0 で終わる |
| 最初の待ちの順 | 状態のファイルが無い会話で guard が先に動くと、世代（`by` `turn`）だけの状態ができる。その後に入力より前の応答の時刻を持つ Stop の hook が動くと、世代を取らず、状態も起こす記録も書かずに 0 |
| 起動の照らし | `launch.json` が別の起動の ID の状態で guard を打つと、世代は変わらず、状態が無ければ作らない。新しい起動の眠る hook は起こす。`launch.json` の `rewake` が偽の起動では、状態を作らず世代も書かない |
| 対象の限定 | `session_id` が `NDF_RELAY_SESSION` と違う入力（入れ子の claude）では世代を替えず、眠る hook は起こす |
| 見分け | 起きた応答の入力で数え手が数え直さない |
| 待ちを明けた入力 | 待ちが在り `pending` の無い状態で guard を打つと `opened` が書かれ、`pending` が在る状態では書かれない。上限で `wait_stop` が立った待ちの後に guard が `opened` を書き、次の Stop の末尾が `hook.transcript-tail` を越える Tool の結果で入力の行を含まない（`opening` が `unknown`）見本で、`_settle` が `hit`・`cache_read` が null の `wait_end` を 1 行書き、`k`・`n_max`・`spent`・`wait_stop`・`opened` が消え、待つ子なら同じ Stop で新しい `wait_start` を書く |

## ラッパー（`wrapper._start`）

- `adapters/cli_floor.require(name, argv0, env, cwd) -> Optional[str]` に変える。下限の確かめ（古ければ `TooOld`）は今のまま保ち、確かめに使った版（`_version` が同じ実パス・更新時刻・大きさで持つ値。表に無い名前・見つからない・読めないなら None）を返す。`refuse_old` は返り値を捨てるだけで変えない。`--version` の起動は増えない
- `_start` は `cli_floor.require("claude", CLAUDE, env, self.s.cwd)` の返り値を版として使う。次の 2 つがそろえば、`_launch_target` へ渡す起動の引数（`[CLAUDE, *head, *self.s.args, *once]`）の `head` の後に `--settings` と `json.dumps(registration.rewake_settings(<根>))` を足す。起動先を決めた後の `Target.argv` には足さない。API キーの従量の子では、`account_choice._source` がこの 1 つの `--settings` を元の設定として読み、`_compose` が `apiKeyHelper` と `ConfigChange` の hook を合わせて 1 つの `--settings` に作り直すので、`hooks.Stop` の起こす登録はそのまま残る（`_compose` は元の `hooks` の `ConfigChange` 以外のキーを保つ）。キーでない子は足した引数をそのまま受ける
  - 版の文字列が `limits.texts("cache.rewake", "versions")`（`contracts/limits/cache.rewake.json` を読む）のどれかと完全に一致する（`semver` の大小では比べない）
  - 受け継ぐ引数（`claude_args.Parsed.carried`）に `--settings` の語も `--settings=` で始まる語も無い
  - `registration.rewake_unreachable()` が None（起こす時点と確かめる間隔が背景の `timeout` の内に届く）。理由の文が返れば登録も `NDF_RELAY_REWAKE` も足さず、ラッパーの `log.jsonl` に `{"kind": "rewake_off", "reason": <理由の文>}` を起動ごとに 1 行書く（`ndf check` も同じ理由で破れとする）

  `<根>` は `self.launched_root` があればそれ、無ければ `tables.PLUGIN_ROOT`。一覧に無い・None・一覧が空・利用者の `--settings` があるなら足さない。利用者の `--settings` があるときは、受け継ぐ引数を並びも中身も変えずに渡す（`--settings` は最後の 1 つだけが効くので、重ねると利用者の設定が効かなくなる。#1543 の実測）。登録を足すときは、子の環境に `NDF_RELAY_REWAKE=1` も足す。足さないときは、親の環境に同じ名前があっても子の環境から外す（登録の無い子の hook が眠らないように）。`--resume` で起動し直すときも同じ判定で毎回足す（`--settings` は起動ごとの引数で、会話に残らない）。足したかは `signals.set_launch(self.dir, launch, rewake=<足したか>)` で今の起動の記録に書く。UserPromptSubmit の guard が `signals.rewake_on` で読む
- 見回りでは版を読まない

| 観点 | テスト |
| --- | --- |
| 版の門 | `versions` を `["2.1.330", "2.1.332"]` にした値で、版 2.1.330 と 2.1.332 は足す・2.1.331（両端の間の測っていない版）・2.1.329・2.1.333 は足さない。読めない・一覧が空でも足さない。足した引数の JSON が `rewake_settings` と同じ。足した起動の子の環境に `NDF_RELAY_REWAKE=1` があり、足さない起動（一覧に無い版・利用者の `--settings`）では、親の環境に `NDF_RELAY_REWAKE=1` があっても子の環境に無い |
| 届かない宣言 | 版が一覧に在り、`cache.wake` の `seconds` を 120 にした値で、初回の起動と `--resume` の再開のどちらも `--settings` の登録も `NDF_RELAY_REWAKE` も足さず、`log.jsonl` に `rewake_off` の行があり、今の起動の記録の `rewake` が偽 |
| 利用者の `--settings` | 版が一覧に在り、受け継ぐ引数に `--settings=x.json` があるときと `--settings x.json` があるときのどちらも、初回の起動と `--resume` の再開の引数に `--settings` が 1 つだけで、利用者の値がそのまま並び、`rewake_settings` の JSON が無い。今の起動の記録の `rewake` が偽 |
| 従量キーの子 | 版が一覧に在り、利用者の `--settings` が無く、起動先が API キーの従量の接続の子のとき、初回の起動と `--resume` の再開のどちらでも、子の起動の引数に `--settings` が 1 つだけで、その JSON が `apiKeyHelper`・`hooks.ConfigChange` の `config-gate` の hook・`hooks.Stop` の `rewake_settings` の登録の 3 つをすべて持つ。起動先の理由が `key_unresolved`・`key_overridden` にならない |
| 版の取得 | `require` が下限の確かめに読んだ版を返し、下限より古ければ今のとおり `TooOld`。`_start` から通した 1 回の起動で、差し替えた `--version` の実行が 1 回だけ |

## 集計（`ndf relay cache report`）

| 関数 | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- |
| `report` | 新設 | `report(rows: Iterable[Mapping[str, Any]], since: Optional[float] = None) -> dict[str, Any]` | `wait_id`（と `session_id`）で待ちにまとめ、`wake` と `wake_result` を `wake_id` で結ぶ。出す値と分母は次の表のとおり。倍率は各行の `w`・`r`・`out` を使う。`since` があれば `wait_start` の `at` がそれ以後の待ちだけを集める。`metrics` に `partial_waits`（`wait_start` が `since` より前か無い待ち。どの集計にも入れない）・`unmatched_wakes`（`wake_result` の無い `wake`）・`orphan_results`（`wake` の無い `wake_result`）・`unpriced_waits`（`wake_result` の無い `wake`、`priced` が偽か `hit` が null の `wake_result` を持つ待ち）を出す |
| `run` | 新設 | `run(args: argparse.Namespace) -> int` | 中継の保存先の全ラッパーの `cache-log.jsonl` を読み、`--since` の日時を `report` に渡し、統一の JSON レポート（`{"tool": "relay", "status", "summary", "items", "metrics"}`）で返す。読めない行は数えて `metrics.skipped` に出す。費用は行の倍率で出すので上限の設定を読まない |

| 値 | 何から出すか |
| --- | --- |
| 待ちの数・上限で止めた待ちの数 | `wait_start`・`stop_waking`（`scope` `wait`） |
| 起こした回数 | `wake` の行の数（結べない `wake` も数える） |
| `age` の幅（30・45・50・55・60 分）ごとの当たりの率 | 結べた組のうち `hit` が null でないものだけ。`age` は `wake`、`hit` は `wake_result`、分母はその組の数 |
| `d` の中央値 | 結べた組のうち `text` が `wake` で `priced` が真のものの `cache_write_1h × w_1h + cache_write_5m × w_5m + output × out`（起床に属する全呼び出しの合計。引継ぎの起床・`priced` が偽の起床は入れない） |
| 起床 1 回の実費の中央値（`text` の別） | 結べた組のうち `priced` が真のものの `cache_read × r + cache_write_1h × w_1h + cache_write_5m × w_5m + output × out`（全呼び出しの合計。読み出しを含む） |
| 待ちの長さの中央・75・90 パーセンタイル | `wait_id`（待ちが始まった応答の時刻）から `wait_end` の `opened_at`（待ちを明けた入力の時刻）まで。`wait_end` の行の `at`（Stop の時刻）は使わない |
| 避けた書き直し（1 つの待ちで最大 1 回） | 待ちの長さ（上の行）が `wait_start` の `ttl` の寿命を越え、その待ちに `hit` が真の `wake_result`（結べた組）が 1 つ以上あり、`wait_end` の `hit`（待ちを明けた最初の呼び出しの読み出し）が真の待ちだけ。`unpriced_waits` の待ち（`wake_result` の無い `wake`・`priced` が偽か `hit` が null の `wake_result` を持つ待ち）は入れない。token は `wait_end` の `tokens`、費用の差は `(w − r) × tokens` − その待ちの全ての `wake_result`（`wake` の無いものも含む）の実費の合計。`wake` の無いまま寿命を越えて自然に当たった待ちは数えない |

## 上限の設定の方針のファイル（`contracts/limits/<方針の名前>.json`）

今の上限の設定は、`contracts/limits/` に方針 1 つにつき 1 ファイルで置き、`contracts/limits.py` の `policy(name)` がそのファイルだけを読む（1 つの `limits.json` は無い。#3687）。足す方針は次の 9 ファイルで、どれも今の方針のファイルと同じく `summary` と `read_by`（方針を引くモジュール。規則 `limit-readers` が実際の読み手と突き合わせる）を持つ。今の `hook.transcript-tail.json` は、`read_by` に `ndf.domains.hooks.rewake` を足す（起こす hook が末尾の大きさを読む）。

| ファイル | 形（`summary`・`read_by` を除く） | `read_by` |
| --- | --- | --- |
| `cache.wake.json` | `{"seconds": 600, "interval": 60}` | `ndf.domains.relay.keep_cache`（`ratios` の中で方針の名前を文字列リテラルで書いて `get` を呼ぶ。下の「読み手の突き合わせ」）・`ndf.domains.hooks.budget`（`rewake_reach` が `tables.policy_value` で読む） |
| `cache.write-ratio.json` | `{"ratios": {"1h": 2.0, "5m": 1.25}}` | 同上 |
| `cache.read-ratio.json` | `{"default": 0.1, "ratios": {"claude-opus-5-5": 0.05, "claude-sonnet-5-5": 0.05, "claude-fable-5-1": 0.025, "claude-fable-5-2": 0.025, "claude-mythos-5-1": 0.025, "claude-mythos-5-2": 0.025}}` | 同上 |
| `cache.output-ratio.json` | `{"default": 5}` | 同上 |
| `cache.wake-cost.json` | `{"count": 2000}` | 同上 |
| `cache.hit.json` | `{"ratio": 0.9}` | 同上 |
| `cache.handoff.json` | `{"calls": 4, "count": 25000, "start": 60000}` | 同上 |
| `cache.rewake.json` | `{"versions": []}`（実装の最初の実測の後に、通った版を一覧に書く。新しい版は測り直して通ってから一覧に足す） | `ndf.domains.relay.wrapper` |
| `hook.budget.stop-rewake.json` | 今の `hook.budget.*` の形で `timeout` 3,300 | `ndf.domains.hooks.budget`（`rewake_budget` の動的な読み口。`registration` は `rewake_budget` を呼ぶだけで方針を引かない） |

`limits.schema.json` の方針の形と、`contracts/limits.py` の `Policy` に、次の項目を足す。

| 項目 | `limits.schema.json` の形 | `Policy` の型 |
| --- | --- | --- |
| `ratios` | 文字列から数への表 | `Optional[dict[str, float]]` |
| `default` | 数 | `Optional[float]` |
| `ratio` | 0 より大きく 1 以下 | `Optional[float]` |
| `versions` | `数.数.数` の文字列の配列。重複なし。空も可 | `Optional[tuple[str, ...]]` |
| `calls` | 1 以上の整数 | `Optional[int]` |
| `start` | 0 以上の整数 | `Optional[int]` |

読み手は入口ごとに次のとおり決める。どの入口も、方針の名前から `contracts/limits/<方針の名前>.json` を読む今の仕組み（`limits.policy`・`tables.policy`）を通り、ファイルの場所を自分で組み立てない。

| 入口 | 取得の API | 返る型 |
| --- | --- | --- |
| 起こす hook（`rewake`・`keep_cache.ratios` へ渡す） | `hooks/tables.policy_value(name, key)`（今のまま。方針の 1 つの値を JSON のまま返す） | `ratios` は `dict[str, float]`、ほかは数。`cache.rewake` の `versions` は読まない（版の一覧を読むのはラッパーの `limits.texts` だけで、`cache.rewake` の `read_by` は `ndf.domains.relay.wrapper` だけ） |
| 集計（`cli_cache`） | 読まない。起こす hook が `tables.policy_value` で読んで行に書いた倍率（`w_1h`・`w_5m`・`w`・`r`・`out`）を使う | — |
| ラッパー（`wrapper._start`） | 新設の `contracts/limits.texts(name, key) -> tuple[str, ...]`（版の一覧を返す API はこの名前だけ。`text` は作らない） | `tuple[str, ...]`（`versions`） |

### 読み手の突き合わせ（規則 `limit-readers`）

`ndf check` の規則 `limit-readers`（`src/ndf/checks/limits_readers.py`）は、方針の `read_by` と、方針を引く読み口（`src/ndf/checks/rules.json` の `limits`）の呼び手を突き合わせる。新しい読み口と読み手は、次のとおり `rules.json` と `read_by` に足す。

| 引く箇所 | `rules.json` の `limits` に足す行 | 読み手に数えるモジュール（`read_by`） |
| --- | --- | --- |
| ラッパーの版の一覧（`limits.texts("cache.rewake", "versions")`） | `readers` に `"ndf.contracts.limits.texts": {"arg": "name", "index": 0}`（`value`・`seconds` と同じ形） | `ndf.domains.relay.wrapper`（`cache.rewake`） |
| `keep_cache.ratios` の中の `get("cache.write-ratio", "ratios")` など | `readers` に `"ndf.domains.relay.keep_cache.ratios.get": {"arg": "name", "index": 0}`（引数で受けた読み口。前例は `ndf.domains.relay.context_thresholds.thresholds.value`） | `ndf.domains.relay.keep_cache`（方針の名前を文字列リテラルで書く `ratios` のモジュール。`cache.wake`・`cache.write-ratio`・`cache.read-ratio`・`cache.output-ratio`・`cache.wake-cost`・`cache.hit`・`cache.handoff`）。`rewake` は `tables.policy_value` を関数のまま渡すだけで、方針の名前を書かないので読み手に数えない |
| 末尾の大きさ（`rewake` の `tables.policy_value("hook.transcript-tail", "count")`） | 足さない（`tables.policy_value` は今の読み口） | `ndf.domains.hooks.rewake` を `hook.transcript-tail` に足す |
| 背景の予算（`budget.rewake_budget` の `tables.policy(f"hook.budget.{spec['async_rewake']['budget']}")`） | `dynamic` に `{"at": "ndf.domains.hooks.budget.rewake_budget", "expr": "f\"hook.budget.{spec['async_rewake']['budget']}\"", "template": "hook.budget.{}", "vocabulary": "hooks/runtimes.json#/runtimes/claude/events/Stop/async_rewake/budget", "reason": "背景の登録の予算を、ランタイムのコントラクト表の async_rewake の予算のキーから引く"}`。語彙は `*` を使わずに Stop の行を指す（`async_rewake` を持たない事象で語彙を引けずに検査が止まらないように）。今の `_budget_policy` の行の語彙（`events/*/budget`）は変えない | `ndf.domains.hooks.budget`（`hook.budget.stop-rewake`） |

`keep_cache.ratios` と `rewake_budget` の中で方針の名前を引く式は、上の表の `expr` と同じ綴りで書く（綴りが違えば規則が読み口を解けない）。実装の完了の条件に、方針のファイルを差し替えるテスト（下）に加えて、`ndf check` の規則 `limit-readers` が破れ 0 件で通ること（PR の CI の検査）を入れる。

`limits.value` は、値が数でない（`ratios`・`versions`）ときに `LimitsError` を上げる。テスト（設計のテスト設計の 13）は、差し替えた `contracts/limits/` の方針のファイル（`cache.write-ratio.json`・`cache.read-ratio.json`・`cache.handoff.json`・`cache.rewake.json`）を入口にして、次の 4 つが差し替えた値どおりになることを確かめる。

- hook の `ratios` の結果（辞書の `ratios` と `default`、`cache.handoff` の 3 つ）
- hook が書いた行の倍率
- その行から集計が出す費用
- ラッパーの版の門（`limits.texts` が返す文字列の配列の `versions`）
