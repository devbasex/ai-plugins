# #4068 の詳細設計: 起こす hook と起こす記録

設計文書 [issue-4068-4199-design.md](./issue-4068-4199-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

**この機能の目的。** 中継の下の会話が裏の作業を待つ間に、1 時間のプロンプトのキャッシュが切れると、次の応答で文脈の全体を書き直す費用がかかる。そこで、寿命が切れる手前で hook が会話を短く応答させ（「起こす」）、キャッシュを読ませて寿命を延ばす。起こす費用の合計が書き直し 1 回分に届いたら、起こすのをやめる。

**この文書の中心の語**（ほかの語は設計文書の「用語」にある）:

| 語 | 意味 |
| --- | --- |
| 中継・ラッパー | `ndf relay` が Claude Code を子プロセスとして包んで起動し、出力と記録を見回るプロセス。引継ぎ・アカウントの切り替え・起動し直しを行う |
| 子 | 中継が包んで動かす Claude Code の会話 |
| 起こす・起床 | 起こす hook が終了コード 2 で終わり、Claude Code が入力を待つ子に短い応答をさせること。その応答を「起こした応答」と呼ぶ |
| 待つ子 | Stop の時点で、裏に作業（#4049 の分け方）が 1 件以上あり、回答待ちの質問が無い子。作業と一緒に監視があってもよい。裏に監視だけが残る子は待つ子でない（設計の決定 16） |
| 世代 | 眠っている起こす hook のうち、どれが起こしてよいかを会話ごとに 1 つに決める印。最新の出来事の時刻・書いた者の ID・取った応答の ID の組（`Generation`）で、新しい応答や入力が来ると書き換わり、古い hook の予定（予約）を取り消す |
| `S` | 判定に使う応答の文脈の量。その応答の `usage` の入力・読み出し・書き込みの合計（`LastTurn.tokens`） |
| 従量の接続 | API キーなどの従量課金で接続する起動先（`metered`）。その設定は子の環境の `PATH` を上書きでき、別の `claude` を起動し得る |
| 見回り | ラッパーが子を動かしている間に一定の間隔で子の状態を確かめる処理（`Wrapper._loop`） |

**前に実装されているもの。** 次の 3 つの機能の上に実装する。どれも main にある。

- 判断開始の値の知らせの数え手（#3995）。利用者の入力ごとに数え直すカウンターで、guard `turn` は起こす文の入力では数え直さない（設計の決定 10）。判断開始の値そのものは、起こす hook は読まない（監視だけの子の引継ぎを外したため。設計の決定 16）
- 利用上限に当たったアカウントから別のアカウントへ子を切り替える仕組み（#3604・#3864）
- バックグラウンドの作業と監視の分け方（#4049）

**成果物の分け方。** この詳細設計の実装は、無効のまま入れる「実装の PR」である。`cache.rewake` の `versions` は空、`is_wake_input` は常に None のままマージし、どの子にも起こす hook を登録しない。設計の決定 7 の 11 の実測は、マージした実装を使って続きの「有効化の PR」で行い、そこで `is_wake_input` と `versions` を直す（設計の決定 7 の「一覧に載せる手順」）。

**#4089（会話の記録の末尾を読む部品）との順序。** #4089 は未実装で、この実装の前でも後でもよい。

- この実装が先のとき: 下の表の #4089 の部品（`src/ndf/adapters/transcript.py`）を、#4089 の詳細設計の「記録の読み手」のとおりにこの実装の中で作る。`read_tail` は `domains/hooks/context.py` から移して import に替える。呼び手（guard・ラッパー・`observe`）の置き換えは #4089 の実装に残す
- #4089 が先のとき: 作らずに使う

使うものは次のとおりである。

| 使うもの | 取り方 |
| --- | --- |
| バックグラウンドの作業の分け方 | #4049 の `relay_busy.task_kind`・`work_count` |
| 会話の記録の末尾と最後の応答 | #4089 の `adapters/transcript.read_tail`・`last_context`・`timestamp_seconds`（#4089 の詳細設計の「#4068 との受け渡し」。未実装。上の段落） |

#4089 の `last_context(tail, Reply(<文>))` が返す状態の要点（正本は #4089 の詳細設計の「#4068 との受け渡し」）:

| 状態 | 意味 | この実装での扱い |
| --- | --- | --- |
| `current` | 末尾の最後の応答が、渡した文（Stop の `last_assistant_message`）と同じで、量・時刻・`message.id` を読めた | 今回の応答として使う |
| `behind` | 末尾の最後の応答が渡した文と違う（今回の応答がまだ記録に無い） | `unrecorded`（一時的） |
| `compacted`・`partial` | 圧縮の境の後でまだ応答が無い・同じ応答の行が書きかけ | `unrecorded`（一時的） |
| `empty`（#4089 で「応答が無い」（`none`）に分類される状態の 1 つ） | 末尾が空か、応答の行が無い（`read_tail` が読めなかったときを含む） | `unrecorded`（一時的） |
| `form` | 突き合わせた応答の採用行の `usage`・`message.id`・`timestamp` の形が違う。採用行は、最後の応答の行のうち最も新しい物理の行（同じ `message.id` の行が複数あれば最後の行）で、量・ID・時刻はこの行だけから読む（#4089 の設計の「読む単位の決まり」） | `form`（記録の形が違う。会話で止める） |

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
│       └── wrapper.py・wrapper_launch.py・account_choice.py
├── src/ndf/checks/rules.json（`limits` の `readers`・`dynamic` に行を足す。「読み手の突き合わせ」）
├── src/ndf/adapters/transcript.py *・src/ndf/domains/hooks/context.py（#4089 より先に実装するときだけ。冒頭の段落）
├── contracts/
│   ├── limits/cache.wake.json・cache.write-ratio.json・cache.read-ratio.json・cache.output-ratio.json・cache.wake-cost.json・cache.hit.json・cache.rewake.json・hook.budget.stop-rewake.json *（`hook.transcript-tail.json` は `read_by` だけ変更）
│   ├── schemas/limits.schema.json
│   ├── hooks/runtimes.json
│   ├── domains/ndf-files.json・env.json
│   └── schemas/io/relay.args.schema.json・relay.result.schema.json
├── hooks/claude.json（再生成）
├── docs/contracts/hooks.md・files.md・commands.md（再生成）
├── skills/development-workflow/references/relay.md
├── src/ndf/contracts/limits.py
└── tests/
    ├── domains/hooks/test_rewake.py *・test_registration.py・test_budget.py・test_guards.py
    ├── domains/relay/test_keep_cache.py *・test_relay_wrapper.py・test_relay_cli_parse.py
    ├── contracts/test_limits.py（`limits.texts`・`value` の `LimitsError`）
    └── adapters/test_transcript.py *（#4089 より先に実装するときだけ）
```

依存の向き:

| 依存 | 向きと決まり |
| --- | --- |
| `hooks/rewake.py` → `relay/keep_cache.py`・`relay/signals.py`・`adapters/transcript.py`（`read_tail`） | hook → 中継の向きで、今の `hooks/guards/turn.py` → `relay/markers.py` と同じ。`rewake` は `markers` を読まない |
| `keep_cache.py` → `adapters/transcript.py`（`last_context`・`timestamp_seconds`） | 判定を純粋な関数に閉じる。末尾の文字列は引数で受け、ファイルを開かない |
| `cli_cache.py` → `keep_cache.py`・`signals` | 費用は行に書かれた倍率で出す |
| `relay/wrapper.py` → `hooks/registration.py`（`rewake_settings`） | 中継 → hook の向きの唯一の依存。`registration` は `relay` を読まないので輪にならない |

| ファイル | 種別 | 責務（1 文） | 依存 | 公開シンボル | 関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/keep_cache.py` | 新設 | 起こす時点・上限・次の手を決め、渡された会話の記録の末尾から最後の応答・起床の呼び出し・待ちを終えた入力を読む | 標準ライブラリ・`adapters.transcript`（`last_context`・`timestamp_seconds`） | `LastTurn`・`TurnRead`・`Usage`・`Reply`・`Opening`・`Ratios`・`Step`・モジュール `keep_cache` | `last_turn`・`wake_reply`・`opening`・`is_wake_input`・`ratios`・`wake_at`・`n_max`・`next_step`・`hit`（新設） |
| `src/ndf/domains/hooks/rewake.py` | 新設 | Stop のバックグラウンドで起動し、今回の応答を確かめて世代を書き、眠って確かめ直し、起こすなら終了コード 2 で終わる | `keep_cache`・`signals`・`hooks.tables`・`relay_busy`・`adapters.transcript`（`read_tail`） | モジュール `rewake` | `run`・`_target`・`_settle`・`_reconcile`・`_wait`・`_finish`（新設） |
| `src/ndf/domains/hooks/guards/turn.py` | 変更 | 中継の下の UserPromptSubmit で、今の起動を突き合わせてから起こす状態の世代を入力の時刻に書き換え（状態が無ければ世代だけの状態を作る）、起こす文の入力を `prompt` の頭（`WAKE_HEAD`）で中継の入力と見分ける（決定 10） | `signals`・`keep_cache` | — | `check`（変更） |
| `hooks/entry.py` | 変更 | 引数が 4 つ以上で `argv[3]` が `rewake` なら `rewake.run` の終了コードを返す。ほか（既存の登録の 3 つの引数の形を含む）は今のとおり `run_entry` に委ねる | — | — | `main`（変更） |
| `src/ndf/domains/hooks/registration.py` | 変更 | 中継の子へ `--settings` で渡す起こす hook の登録（Stop の `asyncRewake: true` 1 つ・`timeout` はバックグラウンドの予算から・command は `hooks/entry.py` の絶対パス）を作る。プラグインの登録（`render`）には足さない | `hooks.tables`・`hooks.budget` | — | `rewake_entry`・`rewake_settings`・`rewake_unreachable`（新設。`budget.rewake_reach` をそのまま返す）。`render` は変えない |
| `src/ndf/domains/hooks/budget.py` | 変更 | バックグラウンドの登録（`rewake_entry` と同じく `rewake_budget` の `timeout` を持つ行）を `async_rewake` の予算と上限で検査し、`cache.wake` の起こす時点と確かめる間隔がバックグラウンドの `timeout` の内に届くか（`rewake_reach`）を確かめ、プラグインの登録のファイルに `asyncRewake` の登録があれば破れとする。同期の登録は今の予算と上限で検査する | `hooks.tables`・`relay.keep_cache`（定数の `TTL_SECONDS`・`MAX_CHECKS` だけ。関数は読まない）（`registration` は読まない。`registration` が `budget` を読むので輪を作らない） | — | `rewake_budget`・`rewake_reach`・`rewake_registrations`（新設）・`registrations_of`・`budget_registrations`・`_file_registrations`・`_registration_errors`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 起こす状態（会話ごと）と起こす記録の読み書き。世代は新しい時刻でだけ書き換え、同じ応答の取り合いを `contested` にして、その応答で始まった起こしていない待ちを取り消す（`wait_void`）。今の起動の記録（`launch.json`）に起こす hook の登録を渡したか（`rewake`）を持つ | 標準ライブラリ | — | `read_keep`・`bump_generation`・`update_keep`・`append_cache_log`・`rewake_on`（新設）・`set_launch`（`rewake` の引数を足す）、定数 `KEEP_PREFIX`・`CACHE_LOG`・`SESSION_ID_RE` |
| `src/ndf/domains/relay/wrapper.py`・`wrapper_launch.py`・`account_choice.py` | 変更 | 利用者の `--settings` が無く、`registration.rewake_unreachable()` が None なら登録つきの引数を作り、候補ごとに従量の接続の設定を 1 回だけ読み（`account_choice.metered_of`）、その同じ値で `account_choice.version_env`（新設）が求めた最終の環境で解いた `claude` の実パスの版（`cli_floor.version_at`。読む時点は「ラッパー」の節の 3 の表）が `cache.rewake` の `versions` のどれかと完全に一致する候補だけ、起動の引数に `--settings <rewake_settings の JSON>` を、子の環境に `NDF_RELAY_REWAKE=1` を、起動の記録に `rewake` 真を入れる。起動先の用意（`target_env`・`key_target`）にも同じ設定の値を渡す（「ラッパー」の節） | `signals`・`adapters.cli_floor`（`resolve`・`version_at`）・`hooks.registration`（`rewake_settings`） | `Wrapper` | `_start`・`_try`・`_fallback`（変更）・`account_choice.metered_of`・`version_env`（新設）・`target_env`・`key_target`・`key_child`（`decl` の引数を足す） |
| `src/ndf/domains/relay/cli_cache.py` | 新設 | `ndf relay cache report` | `keep_cache`・`signals` | モジュール `cli_cache` | `report`・`run`（新設） |
| `src/ndf/domains/relay/cli.py` | 変更 | サブコマンド `cache report` を足す | `cli_cache` | — | `build_parser`（変更） |
| `contracts/limits/<方針の名前>.json`・`contracts/schemas/limits.schema.json`・`src/ndf/contracts/limits.py` | 新設（方針のファイル）・変更 | 方針のファイル `cache.wake`・`cache.write-ratio`・`cache.read-ratio`・`cache.output-ratio`・`cache.wake-cost`・`cache.hit`・`cache.rewake`・`hook.budget.stop-rewake`（どれも `contracts/limits/` に 1 方針 1 ファイル）を足し、スキーマと `Policy` に `ratios`・`default`・`ratio`・`versions` の項目を足す | — | `Policy` | `texts`（新設）・`value`（数でない値で `LimitsError`） |
| `src/ndf/checks/rules.json` | 変更 | 規則 `limit-readers` の読み口の表（`limits`）に、`readers` の `ndf.contracts.limits.texts`・`ndf.domains.relay.keep_cache.ratios.get` と、`dynamic` の `ndf.domains.hooks.budget.rewake_budget` の行を足す（「読み手の突き合わせ」） | — | — | — |
| `src/ndf/adapters/transcript.py`・`src/ndf/domains/hooks/context.py` | 新設・変更（#4089 より先に実装するときだけ） | #4089 の詳細設計の「記録の読み手」のとおり。`context.py` は `read_tail` を移して import に替えるだけで、呼び手は替えない | 標準ライブラリ | `ToolUse`・`Reply`・`Tail`・`ContextRead` | `last_context`・`timestamp_seconds`・`read_tail` |
| `contracts/hooks/runtimes.json` | 変更 | claude の Stop に `async_rewake`（予算 `stop-rewake`・バックグラウンドの登録だけの `timeout_max` 3,300）を足す。プラグインの登録の生成には使わず、`rewake_entry` と予算の検査が読む | — | — | — |
| `contracts/domains/ndf-files.json`・`env.json` | 変更 | `ndf-files.json` に `relay-keep`（`<名前>/keep-<会話>.json`）・`relay-cache-log`（`<名前>/cache-log.jsonl`）の行。どちらもラッパーの保存先と同じ掃除の対象。`env.json` に `NDF_RELAY_REWAKE`（ラッパーが起こす hook の登録を渡した子にだけ `1` で渡す。起こす hook が読む）の行を足す | — | — | — |
| `contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` | 変更 | `cache report` の引数と結果 | — | — | — |
| `hooks/claude.json`・`docs/contracts/*.md` | 変更（再生成） | `ndf hooks render`・`ndf check --write-spec` で作り直す | — | — | — |
| `skills/development-workflow/references/relay.md` | 変更 | hook の一覧に起こす hook、記録の種類に起こす記録、見直しの手順（20 件ごと）を足す | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更（`glossary.md` は再生成） | 要求の用語のうち新しい 7 語（キャッシュの寿命・キャッシュを起こす・起こす hook・起こす記録・起こす間隔・起こす回数の上限・書き直し）と、設計の「用語」の新設の 4 語を足す | — | — | — |

## 判定（`keep_cache`）

純粋な関数で、時刻・値・記録の文字を引数で受ける。

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `last_turn` | `src/ndf/domains/relay/keep_cache.py` | 新設 | `last_turn(tail: str, last_message: Optional[str], started: float) -> TurnRead` | 今回の Stop の応答を末尾から読む。返す値は下の「`last_turn` の返し方」の表。ロックの外で動くので、状態を読まず、状態の寿命も受けない |
| `wake_reply` | 同上 | 新設 | `wake_reply(tail: str, after_id: str) -> Reply` | `tail` は `last_turn` と同じ `read_tail` の文字列（読み直さない）。`after_id` の応答の直後にある最初の入力の行だけを見る（Tool の結果の行は入力に数えない）。起床の文の形（設計の決定 7 の実測 8 で決めた形を `is_wake_input` に閉じる）なら、その次から次の入力の手前（無ければ末尾）までの `assistant` の `message.id` をその起床の呼び出しとし、`Reply("wake", <最初の呼び出しの読み出し>, <全呼び出しの Usage>)`。最初の呼び出しの書き込みが両方 0 でも `wake` で返す。利用者の入力かバックグラウンドの作業の知らせの形なら、その先を探さずに `Reply("preempted", None, None)`。どちらとも決められないなら `Reply("unknown", None, None)`。`after_id` の応答が `tail` に無い（起床の後の大きな Tool の結果で、読み取りの上限の外へ押し出された）なら `Reply("truncated", None, None)`。`after_id` が `tail` に在れば、その後の行は末尾まで連続して `tail` に在るので、途中の呼び出しだけが欠けることは無い |
| `opening` | 同上 | 新設 | `opening(tail: str, message_id: str) -> Opening` | `message_id` の応答を始めた入力（さかのぼって最初の、Tool の結果でない入力の行）と、その入力の直後の最初のモデル呼び出しを返す。`kind` はその入力に `is_wake_input` を当てた結果から `wake`（起床の文）・`normal`（通常の入力）・`unknown`（決められない・入力の行が `tail` に無い）。`normal` のときだけ `at`（入力の行の `timestamp` の UNIX 秒）と `first_read`（最初の呼び出しの読み出し）を持つ。`_settle` が待ちを終えるかと、`wait_end` の当たり・時刻を決めるのに使う。今回の応答の最後の呼び出しや Stop の時刻は使わない |
| `is_wake_input` | 同上 | 新設 | `is_wake_input(line: Mapping[str, Any]) -> Optional[bool]` | 入力の行 1 つが起床の文なら真、通常の入力なら偽、決められなければ None。起床の文が会話の記録の入力の行にどう載るかは、まだ測っていない（設計の決定 7 の実測 8）。この関数はその形だけを知る。実装の PR では常に None を返し、版の一覧（`cache.rewake` の `versions`）も空のままマージする。そのため起こす hook はどの子にも登録されず、機能は働かない（今の振る舞いのまま）。この関数と版の一覧は、有効化の PR で一緒に直す（実測 8 を先に測り、その形でこの関数を直してから残りを測る。設計の決定 7 の「一覧に載せる手順」） |
| `ratios` | 同上 | 新設 | `ratios(get: Callable[[str, str], Any], model: str, ttl: str) -> Ratios` | `get(name, key)` で上限の設定の値を読む（hook は `tables.policy_value` を渡す。集計は読まない）。`cache.write-ratio` の `ratios`（辞書）から引数の `ttl` の `w` と、`1h`・`5m` の `w_1h`・`w_5m`、`cache.read-ratio` の `ratios`（辞書。表に無いモデルは `default`）から `r`、`d`・出力の倍率・余白・確かめる間隔・当たりの境。辞書でない・数でない値は `tables.TableError`。費用の式の分母（`n_max` の `r × S + d`）が正になるよう、書き込み・読み出し・出力の倍率（`ratios` の各値と `default`）が 0 以下か、`d`（`cache.wake-cost.count`）が 1 未満（スキーマの `count` と同じ下限）でも `tables.TableError`（hook は `tables.policy_value` で JSON のまま読み、スキーマの検査を通らない宣言も届き得るので、実行時にも拒む）。確かめる間隔（`cache.wake.interval`）が 0 以下か、1 回の眠りの確かめの回数 `ceil((TTL_SECONDS["1h"] − seconds) / interval)` が `MAX_CHECKS` を越えるか、余白（`cache.wake.seconds`）が 0 未満か 1 時間の寿命（`TTL_SECONDS["1h"]`）以上でも `tables.TableError`（スキーマの `interval`・`seconds` はほかの方針と共通で 0 を許すので、ここで拒む。`_wait` が休まずに世代を読み続けない） |
| `wake_at` | 同上 | 新設 | `wake_at(at: float, ttl: str, wake_margin: float) -> Optional[float]` | `wake_margin` は起こす時点の余白（`cache.wake.seconds`）である。`ttl` が `1h` でなければ、余白によらず None（5 分の寿命は常に起こさない。設計の決定 2）。`1h` なら `at + TTL_SECONDS["1h"] − wake_margin`。`ttl` は `_settle` が補った寿命（未定のまま呼ばない）。余白の範囲（0 以上 1 時間未満）は `ratios` が確かめる。到達条件（`budget.rewake_reach`）は `3600 − 余白 + 確かめる間隔 + budget.margin ≤ timeout` である（`budget.margin` はバックグラウンドの hook の予算の余りの秒で、余白とは別の値）。既定の間隔 60・`budget.margin` 240・`timeout` 3,300 では、余白は 600 秒以上に限られる。それでも、5 分を除くのはこの関数の規則で、到達条件には頼らない |
| `n_max` | 同上 | 新設 | `n_max(tokens: int, q: Ratios) -> int` | `floor((w − r) × S / (r × S + d))`。`S` は引数の `tokens`（待ちの始まりの応答の文脈の量）、`w`・`r`・`d` は `q` の値。0 未満にしない |
| `hit` | 同上 | 新設 | `hit(read: int, before: int, ratio: float) -> bool` | `read >= before × ratio`。`read` は起床か待ちを終えた入力の最初の呼び出しの読み出し |
| `next_step` | 同上 | 新設 | `next_step(k: int, limit: int, spent: float, tokens: int, q: Ratios) -> Step` | `k + 1 > limit` → `stop`。`spent + r × tokens + d > (w − r) × tokens` なら `stop`（費用の上限。回数の上限と同じく `wait_stop` を「上限」にする）。ほかは `wake`。`limit` は待ちの始まりの `S` で決めた状態の `n_max`、`spent` は状態の累計、`tokens` は判定の時の（今回の応答の）`S`、`q` は判定の時の応答の寿命とモデルの倍率。費用の照合で、累計は寿命・モデル・`S` が変わっても最後の起床の時の `(w − r) × tokens` に収まる。倍率が変わらず `S` が減らなければ、回数の上限より先に効かない（設計の決定 3 の「費用の約束の範囲」）。監視だけの子はここへ来ない（待つ子でないので眠らない。設計の決定 16）ので、作業と監視の件数も判断開始の値も受けない |


### `last_turn` の返し方

`tail` は `read_tail` の文字列で、`started` は hook の動き始めの時刻である。中で `last_context(tail, Reply(last_message))` を 1 回だけ呼び、その状態から、次の表の上から順に最初に当たった行の値を返す。項目の取り出し元は #4089 の詳細設計の「#4068 との受け渡し」の表に従う。`ref` は、突き合わせた最後の `assistant` の行の `message.id` と `timestamp` の UNIX 秒の組 `(message_id, at)` である。`last_context` が `form` でも返す `ContextRead.message_id`・`timestamp` から作る。

| 順 | `last_context` の状態と条件 | 返す値 | 意味 |
| --- | --- | --- | --- |
| 1 | `current` か `form` で、`ref` が在り、`ref.at > started` | `TurnRead(None, "newer")` | 遅れて動いた古い hook が、自分の Stop の後に書かれた次の応答を読んだ。形の違いより先に確かめる |
| 2 | `form` | `TurnRead(None, "form", ref)`。`message.id` か時刻が読めなければ `ref` は None | 突き合わせた応答の形が違う。前の行へさかのぼらない |
| 3 | `behind`・`compacted`・`partial`・`empty`（`read_tail` が読めずに空の文字列を返したときを含む） | `TurnRead(None, "unrecorded")` | 今回の応答がまだ記録に無い・読めない（一時的） |
| 4 | `current` | `TurnRead(LastTurn, None)` | 今回の応答 |

寿命は、応答の寿命別の書き込みだけで決める。1 時間が正なら `1h`、1 時間が 0 で 5 分が正なら `5m`、両方 0 なら `ttl` を None（未定）で返す。状態の寿命は受けない（ロックの外で読んだ状態を補いに使わない。設計の決定 13）。補うのは `_settle` だけである。

この実装は応答の ID で突き合わせない。全ての Stop を `last_message` の文（`Reply`）で突き合わせ、シグネチャに ID を足さない。`Target` も ID を持たない。ID による突き合わせは、実測 9 で Stop の入力に応答の ID が載る版が見つかった後に、別の設計で足す。その設計が、取る入力の項目・#4089 の手がかりの型・ID が合わないときの結果を決める（設計の決定 4 の「応答の ID が取れたとき」）。

### 内部のデータと定数

| 名前 | 種別 | 保存先 | 値・形 |
| --- | --- | --- | --- |
| `LastTurn` | 型（`NamedTuple`） | `keep_cache.py` | `message_id`・`at`（`timestamp` の UNIX 秒）・`tokens`（入力・読み出し・書き込みの合計）・`ttl`（`1h`・`5m`・None（未定。`_settle` が補う））・`model`・`cache_read`・`cache_write`・`output` |
| `TurnRead` | 型（`NamedTuple`） | 同上 | `turn`（`LastTurn` か None）・`reason`（`unrecorded`・`form`・`newer` か None）・`ref`（`form` のときだけ。突き合わせた最後の `assistant` の行の `message.id` と `timestamp` の UNIX 秒の組 `(message_id, at)`。どちらかが読めなければ None。ほかの理由では None） |
| `Reply` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`preempted`・`unknown`・`truncated`）・`first_read`（起床の最初の呼び出しの読み出し。当たりの判定に使う。`wake` のときだけ）・`usage`（`Usage`。起床に属する全呼び出しの合計。`wake` のときだけ） |
| `Usage` | 型（`NamedTuple`） | 同上 | `calls`（呼び出しの数）・`cache_read`・`output`（どれも合計）・`cache_write_1h`・`cache_write_5m`（寿命別の書き込みの合計。`priced` が偽なら None）・`priced`（真偽。全呼び出しで `cache_creation` の `ephemeral_1h_input_tokens`・`ephemeral_5m_input_tokens` が 0 以上の整数で在り、2 つの和が `cache_creation_input_tokens` と同じとき真。1 つでも欠ける・整数でない・負・和が違えば偽で、推し量らない） |
| `Opening` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`normal`・`unknown`）・`at`（待ちを終えた入力の行の時刻）・`first_read`（その入力の直後の最初の呼び出しの読み出し） |
| `Ratios` | 型（`NamedTuple`） | 同上 | `w`（`ratios` に渡した寿命の書き込みの倍率。どの応答の寿命を渡すかは下の「寿命とモデルの取り方」）・`w_1h`・`w_5m`・`r`・`d`・`out`・`wake_margin`（起こす時点の余白。`cache.wake.seconds`）・`interval`・`hit` |
| `Step` | 型（`NamedTuple`） | 同上 | `kind`（`wake`・`stop`）・`reason` |
| `TTL_SECONDS` | 定数 | 同上 | `{"1h": 3600, "5m": 300}`（公式の寿命。値段ではないので上限の設定に置かない） |
| `MAX_CHECKS` | 定数 | 同上 | 300（眠る hook 1 つが 1 回の眠りで世代を確かめる回数の上限。待ちが続けば起床の Stop ごとに新しい hook が眠り直すので、待ち全体の回数ではない。回数は `ceil((TTL_SECONDS["1h"] − seconds) / interval)` で、余白と間隔の組で決まる。既定の 600・60 で 50 回。宣言の値の守りなので上限の設定に置かず、`ratios` と `budget.rewake_reach` がこの定数を読む） |
| `WAKE_HEAD` | 定数 | 同上 | `【ndf-relay】`（起こす文の頭。起きた応答の見分けに使う） |
| `WAKE_TEXT` | 定数 | 同上 | 設計の「処理の流れ」の末尾の「起こす文（既定）」 |

### 異常系の処理

| 関数 | 失敗の種類 | 原因 | 検出する場所 | 検出したときの処理 | 利用者への通知 | 回復の手段 |
| --- | --- | --- | --- | --- | --- | --- |
| `last_turn` | 最後の応答が載っていない・記録が読めない | Stop の時点で未記録（公式の hook の文書が「Stop の時点で最後の応答を記録に含まないことがある」と書くバージョン。設計の「外部の調査」の PRD3）・`read_tail` が空の文字列を返した（権限・ファイルが無い） | `last_context` の状態（`behind`・`compacted`・`partial`・`empty`。冒頭の状態の表と「`last_turn` の返し方」の 3 行目と同じ 4 つ） | `reason` `unrecorded` | 無し（呼び手が `stop_waking`（`scope` `once`）を会話で 1 度書く。止めない） | 次の Stop で読み直す |
| `last_turn` | 形が違う | Claude Code の記録の形の変化 | `last_context` の `form`・#4089 の表の項目の型 | `reason` `form` | 無し（呼び手が `stopped` と `stop_waking`（`scope` `session`）を 1 度書く） | 読み方を直す |
| `last_turn` | 次の応答を読んだ | 遅れて動いた古い hook | 応答の時刻と `started` の比べ | `reason` `newer` | 無し（呼び手は何も書かずに 0） | — |
| `wake_reply` | 起床の呼び出しが末尾の範囲に無い | 起床の後の大きな Tool の結果が `hook.transcript-tail` の大きさを越えた | `after_id` の応答が `tail` に無い | `kind` `truncated` | 無し（呼び手が `hit` null・`priced` 偽の `wake_result` を書いてフラグを消す。止めない） | — |
| `wake_reply` | 寿命別の書き込みが読めない | 記録の形の変化・和の食い違い | 各呼び出しの `cache_creation` の突き合わせ | `Usage.priced` 偽 | 無し（集計が費用から外して `unpriced_waits` に数える） | 読み方を直す |
| `ratios` | 行が無い・値が数でない・倍率が 0 以下・`d` が 1 未満・間隔が 0 以下・確かめの回数が上限を越える・余白が範囲の外 | 上限の設定の壊れ・利用者の宣言 | 型と範囲の突き合わせ | `tables.TableError` を投げる | 無し（呼び手が起こさない） | 上限の設定を直す（`ndf check` が落とす） |

### 単体テストの観点

| 関数 | 観点 |
| --- | --- |
| `last_turn` | 実測の形の見本を読める。最後の `assistant` の行に `usage` が無い見本では、前の行が完全でも `form` で、`ref` が採用行の `message.id` と時刻。同じ見本で採用行の時刻が `started` の 1 秒後なら `form` でなく `newer`。採用行の `timestamp` が無い見本では `form` で `ref` が None。`output_tokens` だけが欠けた見本（`last_context` は `current`）でも `form` と `ref`。完全な前の応答だけが残り `last_message` が違う見本では `unrecorded`。`last_message` が None でも `unrecorded`。同じ文で時刻が `started` の 1 秒後なら `newer`、`started` と同じなら読める。空の文字列（`read_tail` が読めなかった）で `unrecorded`。寿命: 1 時間が正で `1h`、5 分だけ正で `5m`、両方 0 で None（引数に状態の寿命を取らない）、`cache_creation` が無い・負で `form` |
| `wake_reply` | 起床の文の後の応答を返す。Tool を 2 回使った起床の見本では `first_read` が最初の呼び出しの読み出し、`usage.calls` が 3 で合計が 3 つの和、Tool の結果の行で区切らない。1 回目が 1 時間へ 300・2 回目が 5 分へ 1,000 書いた見本で `cache_write_1h` 300・`cache_write_5m` 1,000・`priced` 真。最初の呼び出しの書き込みが両方 0 の見本でも `wake` で `first_read` を返す。どれかの呼び出しの寿命別の項目が欠ける・整数でない・負・和が合計と違う見本で `priced` 偽・2 つの合計が None。起床の後に利用者の入力が続く見本では、その入力の後の応答を含めない。フラグの直後の最初の入力が利用者の入力の見本では、その後に起床の文の応答があっても `preempted`。最初の入力の形を決められない見本では `unknown`。起こす文に従わず Tool を使った起床で 2 回目の Tool の結果が `tail` の大きさを越え、`after_id` の応答が `tail` の外へ押し出された見本では `truncated`。同じ大きな結果でも `after_id` が `tail` に残る大きさでは `wake` で 3 つの和 |
| `opening` | 利用者の入力で始まり Tool を使った応答で `normal`（Tool の結果の行を飛ばす）、`at` が入力の行の時刻、`first_read` が入力の直後の最初の呼び出しの読み出し（最初の呼び出しの読み出し 20,000・後の Tool の呼び出しの読み出し 101,000 の見本で 20,000）。起床の文で始まった応答で `wake`。形を決められない入力・入力の行が `tail` に無い見本で `unknown` |
| `ratios` | `cache.wake` の `interval` を 0・-1 にした見本、`seconds` 600 で `interval` を 0.01・9 にした見本（300,000 回・334 回）、`seconds` 550・`interval` 10 の見本（305 回）、`seconds` を -1・3,600 にした見本、`cache.read-ratio` の `default` か表の値を 0・-0.05 にした見本、読み出しの倍率 0 と `d` 0 を合わせた見本、`cache.write-ratio` の `1h` を 0 にした見本、`cache.wake-cost` の `count` を 0・-1 にした見本で `tables.TableError`（hook は眠らずに 0。`n_max` に分母 0 が渡らない）。`ndf check` も同じ値の方針のファイルをスキーマで破れとする。`seconds` 600 で `interval` 10（300 回）・60 で通る |
| `n_max` | Opus 5.5・`1h`・`d` 2,000 で `S` 400,000 → 35、500,000 → 36、20,000 → 13。読み出し 0.1 で 400,000 → 18 |
| `wake_at` | `1h`・余白 600 で `at + 3000`、`5m` で None。余白 240（5 分の寿命より短い）でも `5m` は None、`1h` は `at + 3360` |
| `next_step` | Opus 5.5・`1h`・`d` 2,000・`limit` 33・`tokens` 250,000 のまま、`k` 32 → `wake`、`k` 33 → `stop`。`limit` 0 → `stop`（純粋な関数の境界。`limit` を式から作る組の試験は設計のテスト設計の 4）。`limit` 36・`tokens` が 500,000 から起床ごとに 300 増える列では、`k` 35（`spent` 953,925・`tokens` 510,500）でも `wake`、`k` 36 で `stop`（費用の照合は早く止めない。`k` 36 の時点の累計 981,450 は `(w − r) × 510,500` = 995,475 以下）。読み出し 0.1・`tokens` 500,000・`limit` 36 で、`k` 17・`spent` 859,000 → `wake`、`k` 18・`spent` 911,000 → `stop`（911,000 + 52,000 = 963,000 が 950,000 を越える）。`S` が減った見本（Opus 5.5・`limit` 33・`k` 20・`tokens` 150,000。`(w − r) × tokens` = 292,500）で、`spent` 280,000 → `wake`（280,000 + 9,500 = 289,500）、`spent` 290,000 → `stop`（299,500 が 292,500 を越える） |

## 起こす hook（`hooks/rewake.py`）

### 関数の仕様

| 関数 | ファイル | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- | --- |
| `run` | `src/ndf/domains/hooks/rewake.py` | 新設 | `run(argv: list[str], stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr, environ: Mapping[str, str] = os.environ, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> int` | 終了コード。起こすなら標準エラーへ文を書いて 2、ほかは 0。例外はすべて 0（Stop を壊さない）。最初に `clock()` を呼び、その値を動き始めの時刻（`started`）として `Target` に持たせる。`started` は Claude Code が hook を起動した時刻より遅れ得る（インタプリタの立ち上がり）ので、今回の応答の突き合わせは `started` だけに頼らず、`_settle` の取り合いの突き合わせを重ねる |
| `_target` | 同上 | 新設 | `_target(payload: Mapping[str, Any], environ: Mapping[str, str]) -> Optional[Target]` | 最初に `environ` の `NDF_RELAY_REWAKE` が `1` かを確かめ、違えば None（ラッパーが登録を渡した起動でだけ渡す変数。設計の決定 7。登録の無い起動からエントリポイントを直接呼んでも眠らない）。続いて、中継の保存先（`NDF_RELAY_DIR`）・起動の ID・会話の ID・`transcript_path` がそろい、入力の `session_id` が #4049 の `signals.relay_session(environ)`（`NDF_RELAY_SESSION`）と同じで、会話の ID が UUID の形（`SESSION_ID_RE`）のときだけ `Target`（`background_tasks` を #4049 の `task_kind` で分けた作業と監視の件数、`last_assistant_message`、起動のたびに作る `hook_id`（`uuid4().hex`）・`started` を持つ）。ほかは None（入れ子の claude は `--settings` の登録を受け継がないのでこの hook が起動しない。起動しても `session_id` が違うので通らない） |
| `_settle` | 同上 | 新設 | `_settle(t: Target, turn: LastTurn, tail: str, q_for: Callable[[str, str], Ratios], clock: Callable[[], float]) -> Settled` | 子を止めるロックを取り、その中で 1 つの区間として下の「`_settle` の手順」の (1)〜(7) をこの順に行う。新しい状態・寿命を補った `turn`・起こさない理由を返す。`q_for(model, ttl)` はモデルと寿命から倍率を返す関数で、`run` が `lambda model, ttl: keep_cache.ratios(tables.policy_value, model, ttl)` を渡す（寿命が決まる前には倍率を引けないので、値でなく関数で受ける） |
| `_reconcile` | 同上 | 新設 | `_reconcile(t: Target, keep: dict, tail: str, q_for) -> dict` | ロックの中で呼ぶ。状態の `pending` を `_settle` の (4) の規則で突き合わせ、`Reply.kind` に応じた `outcome` の `wake_result` を 1 行書いてフラグを消した状態を返す（フラグを消すのに `wake_result` を書かない経路は無い）。今回の応答（`last_turn`）の読み取りの結果を使わず、`tail` とフラグだけで決めるので、`last_turn` が `form` を返した Stop でも呼べる。起床に属する最後の呼び出しの寿命別の項目が欠けても、`wake_reply` は `priced` 偽の `Reply` を返すので、`wake_result` は `hit`（最初の呼び出しの読み出しで決める）と `priced` 偽で書かれる |
| `_wait` | 同上 | 新設 | `_wait(t: Target, gen: Generation, until: float, interval: float, clock, sleep) -> bool` | `until` まで `interval` ごとに世代だけを読み、自分のままなら真。会話の記録は読まない |
| `_finish` | 同上 | 新設 | `_finish(t: Target, gen: Generation, turn: LastTurn, q: Ratios, stderr: TextIO, clock: Callable[[], float]) -> int` | `q` は `run` が眠る前に 1 度だけ求めて渡す（「処理の手順」の 5）。子を止めるロックを取り、その中で 世代（`by` が自分か。`contested` なら外れ）・起動・質問のファイル を突き合わせ、外れたら何も書かずに 0。そろえば状態を読み直し、`stopped`・`wait_stop` が在れば何も書かずに 0（眠る間に別の hook が書いた）。無ければ `next_step(k, n_max, spent, turn.tokens, q)` で手を決める。`turn` は `_settle` が寿命を補った今回の応答（`Settled.turn`）で、`q` は `ratios(tables.policy_value, turn.model, turn.ttl)`（下の「寿命とモデルの取り方」）。`stop` なら `wait_stop` を「上限」にして `stop_waking`（`scope` `wait`）を書いて 0。`wake` なら設計の決定 6 の順で (1) 起こしたフラグ（`wake_id`・フラグの `S`・起こした時刻・起こす前の最後の応答の ID・`turn.model`・`turn.ttl`。どれも必須）・`k + 1`・`spent + r × turn.tokens + d`・世代（起こした時刻） (2) `wake` の行 を書き、`WAKE_TEXT` を出して 2 |


### `_settle` の手順

`q_for` は次の 3 か所で呼ぶ。(3) で寿命が決まった後の `wait_start` の `n_max`、(4) のフラグの `model`・`ttl` での値付け、(5) の `last` の `model`・`ttl` での値付けである。

1. **起動を突き合わせる。** `signals.current_launch` が `t.launch` でなければ、世代も含めて何も書かずに `Settled(stale=True)` を返す
2. **世代を取る。** `bump_generation(Generation(at=turn.at, by=t.hook_id, message_id=turn.message_id))` を呼ぶ。`bump_generation` は次の順で判定する
   1. (2a) 取り合い: 世代の `message_id` が `turn.message_id` と同じで、書き込み元が自分でなければ、時刻を比べずに世代の `by` を `contested` に替える（既に `contested` ならそのまま）。続けて、先に世代を取った hook がこの応答で始めた待ちを取り消す。状態の `wait_id` が世代の `at` と同じで、`k` が 0 で `pending` が無ければ、待ちの項目（`wait_id`・`k`・`n_max`・`spent`・`wait_stop`・`opened`）を消し、`wait_void` の行を 1 行書く。ほかの項目（世代の `at`・`message_id`、状態の `ttl`・`last`・`stopped`・`noted`・`pending`）は書かずに `stale` を返す。どちらの hook も起こさない（設計の決定 4）

   取り消すのが待ちだけなのは、(3)〜(7) のうち Stop ごとに違う入力（作業の件数 `works`）を使うのが (6) の「待つ子か」だけだからである。(3) の寿命・(4) のフラグの突き合わせ・(5) の待ちの終わり・(7) の `last` は、取り合った応答と `tail` だけで決まり、どちらの hook が先に書いても同じ値になる。先に取った hook が古い Stop（作業 1 件）で、正しい Stop が作業 0 件なら、取り消さないと存在しない待ちの `wait_start` が残り、集計に混ざる。逆に先に取った hook が正しい Stop でも取り消す（どちらが正しいかを決められないので、どちらも起こさない決定 4 の代償に含める）。次の待つ子の Stop が新しい待ちを始める。`k` が 1 以上か `pending` が在る待ち（起こした後に取り合いが届いた）は、起こした記録を持つので取り消さない。(6) が書く `stop_waking`（`scope` `once`・寿命がまだ分からない）は一度きりの知らせで集計に入らないので、取り消さない
   2. (2b) 古い: そうでなく、世代の時刻が `turn.at` より新しいか同じなら、何も書かずに `stale` を返す
   3. (2c) ほかは世代を書く

   (2a) を (2b) より先に見るのは、同じ応答を取り合う 2 つの hook が同じ `timestamp` を持ち、(2b) を先にすると取り合いに届かないためである
3. **寿命を決める。** 状態を読み直す。`turn.ttl` が在ればそれ、None なら状態の `ttl` を寿命にする。それも無ければ寿命は未定で、(5) と (6) の待ちの始まりを書かない。決まった寿命は状態の `ttl` に書き、未定なら `ttl` を書き換えない。以下の `turn` は寿命を補った値で、`Settled.turn` に入れて返す
4. **起こしたフラグを突き合わせる。** フラグがあれば `_reconcile` を呼び、`wake_reply(tail, <フラグの応答の ID>)` の結果で、次の表のとおり `wake_result` を 1 行書いてフラグを消す。どの結果でも、`model`・`ttl`・倍率・`tokens` はフラグから書く。倍率はフラグの `model`・`ttl`（起こした時の判定に使った応答のもの）で引く

   | `Reply.kind` | `wake_result` の `outcome` と項目 | 状態に書くもの |
   | --- | --- | --- |
   | `wake` | `woke`。`hit` は `hit(Reply.first_read, <フラグの S>, q.hit)`。`first_read`・`calls`・`cache_read`・`cache_write_1h`・`cache_write_5m`・`output`・`priced` は `Reply` から。`w_1h`・`w_5m`・`r`・`out` はフラグの `model`・`ttl` の倍率 | 当たらなければ `stopped` |
   | `preempted` | `preempted`。判定しない。`hit` null・`calls` 0・`cache_read`・`cache_write_1h`・`cache_write_5m`・`output` 0・`priced` 真（起床の呼び出しが無かった） | 無し |
   | `unknown` | `unknown`。判定しない。`hit` null・`priced` 偽 | `wait_stop` を「起床を突き合わせられない」にする |
   | `truncated` | `truncated`。`hit` null・`priced` 偽 | 無し（`stopped` も `wait_stop` も書かない） |
5. **待ちを終えるか決める。** 待ちがあれば `opening(tail, turn.message_id)` を呼び、次の表のとおり扱う

   | `Opening.kind` と状態 | 扱い |
   | --- | --- |
   | `normal` | 待ちを終える。`wait_end` の `tokens`・`ttl`・`model` は更新前の状態の `last`、`w`・`r` はその寿命とモデルの倍率、`hit` は `hit(Opening.first_read, last.tokens, q.hit)`、`cache_read` は `Opening.first_read`、`opened_at` は `Opening.at`、`age` は `Opening.at − last.at` で書く |
   | `unknown` で、状態の `opened`（guard が待ちの間の通常の入力で書いた時刻）が `last.at` より後 | 待ちを終える（#4068 の受け入れ条件 4 の「ただし」。記録の入力の行は見分けられないが、guard の証拠がある）。`wait_end` の `opened_at` は `opened`、`age` は `opened − last.at`、`hit` と `cache_read` は null（費用と当たりの証拠が無いので推し量らない）。ほかの項目は `normal` と同じ |
   | `wake`、または `opened` の無い `unknown` | 待ちを終えない。今の Stop が待つ子でなくても（バックグラウンドの作業が 0 件でも）、`k`・`n_max`・`spent`・`wait_stop`（(4) で立てた「起床を突き合わせられない」を含む）を保つ。バックグラウンドの件数より、今回の応答を始めた入力の突き合わせを先にする |

   待ちを終えたら、`wait_id`・`k`・`n_max`・`spent`・`wait_stop`・`opened` を消す
6. **待ちを始めるか決める。** 待つ子で待ちが無く、(3) で寿命が決まったときだけ、`wait_id` を世代の時刻、`k` と `spent` を 0、`n_max` を式の値にして `wait_start` を書く。寿命が未定なら、`noted` に「寿命がまだ分からない」が無いときだけ `stop_waking`（`scope` `once`）を書いて `noted` に足し、待ちを始めない。(5) で待ちを終えなかった Stop では、待ちが残るので書かない
7. **`last` を更新する。** 最後に `last` を `turn` にする（`wait_end` を書いた後。寿命が未定なら `last.ttl` は None で、待ちは無い）

### 内部の型

内部の型（どれも `NamedTuple`）:

| 型 | 項目 |
| --- | --- |
| `Target` | `relay_dir`・`launch`・`session_id`・`transcript`・`works`・`monitors`・`last_message`・`hook_id`・`started` |
| `Generation` | `at`・`by`（`contested` は取り合いのフラグ）・`message_id`（起こす hook の Stop が取った応答。guard と起床は None） |
| `Settled` | `keep`・`turn`（寿命を補った `LastTurn`）・`stale`・`reason` |

### 状態のファイル（`keep-<会話>.json`）

中継の保存先の `<名前>/keep-<会話>.json` に、会話（`session_id`）ごとに 1 つ置く。読み書きは `signals` の `read_keep`・`bump_generation`・`update_keep` で行う。どれも子を止めるロックの中で書く（眠る hook の世代の読み取りだけはロックを取らない）。ファイルはラッパーの保存先と同じ掃除の対象である（`ndf-files.json` の `relay-keep`）。項目の意味と理由は設計文書の「データ構造」の起こす状態の表にある。

| 項目 | 型 | 誰が書くか | いつ消すか |
| --- | --- | --- | --- |
| `generation` | object: `at`（UNIX 秒）・`by`（書き込み元の ID。guard は `turn`、取り合いは `contested`）・`message_id`（起こす hook の Stop が取った応答の ID。guard と起床は null） | `_settle` の (2)・「処理の手順」の 3 の `form` の分岐（`bump_generation`）、`_finish`（起こした時刻）、UserPromptSubmit の guard | 消さない。新しい時刻でだけ書き換える |
| `wait_id` | 数（待ちが始まった Stop の世代の `at`） | `_settle` の (6) | `_settle` の (5) で待ちを終えたとき。`bump_generation` の取り合い（`_settle` の (2a)）でその待ちを取り消したとき |
| `k` | 整数（今の待ちで起こした回数） | `_settle` の (6)（0）、`_finish`（`k + 1`） | 同上 |
| `n_max` | 整数（今の待ちの上限） | `_settle` の (6) | 同上 |
| `spent` | 数（今の待ちで起こした費用の見積りの累計） | `_settle` の (6)（0）、`_finish`（`r × S + d` を足す） | 同上 |
| `wait_stop` | 文字列（理由: 上限・起床を突き合わせられない） | `_finish`（上限）、`_settle` の (4)（`unknown`） | 同上 |
| `opened` | 数（待ちの間に guard が受けた通常の入力の時刻） | 「UserPromptSubmit の guard」の手順 3 | 同上 |
| `ttl` | 文字列 `1h`・`5m`（最後に分かった寿命） | `_settle` の (3)（分かった寿命でだけ） | 消さない。未定の応答では書き換えない |
| `last` | object: `message_id`・`tokens`・`model`・`ttl`・`at`（突き合わせ終えた最後の応答） | `_settle` の (7) | 消さない。Stop ごとに置き換える |
| `pending` | object（起こしたフラグ）: `wake_id`・起こす前の `S`・起こした時刻・起こす前の最後の応答の ID・その応答の `model`・`ttl`。どれも必須 | `_finish` の (1) | 次の Stop の `_reconcile`（`wake_result` を必ず 1 行書いてから） |
| `stopped` | 文字列（会話で止めた理由: 当たらない・記録の形が違う） | `_settle` の (4)（当たらない）、「処理の手順」の 3 の `form` の分岐 | 消さない。在ればその会話では二度と起こさない |
| `noted` | 文字列の配列（`stop_waking` を書き終えた一時的な理由の集合） | 「処理の手順」の 3（未記録）・「処理の手順」の 5（起こす時点が過去）・`_settle` の (6)（寿命がまだ分からない） | 消さない。同じ理由の行を会話で 1 度だけにする |

寿命とモデルの取り方（同じ待ちの途中で寿命かモデルが変わっても、値ごとに 1 つに決める。設計の決定 13）:

| 値 | 使う応答 | 理由 |
| --- | --- | --- |
| 回数の上限 `n_max`（`wait_start`） | 待ちが始まった応答（その Stop の `Settled.turn`）の `S`・寿命・モデル。待ちの間は変えない | #4068 の受け入れ条件 4。待ちの単位の上限 |
| 今回の起こす時点（`wake_at`） | 今回の応答（`Settled.turn`）の寿命 | キャッシュの寿命を決めるのは最後の書き込み |
| 起床の費用の足し込み（`spent += r × S + d`）・通常の起床の前の費用の照合（`next_step` の `w`・`r`） | 今回の応答（`Settled.turn`）の寿命とモデル。`S` も今回の応答 | 照合で比べる書き直しは、今の寿命とモデルでの今の `S` の書き直し |
| 起きた応答の値付け（`wake_result` の `model`・`ttl`・`r` と、`w_1h`・`w_5m`・`out`） | フラグに書いた `model`・`ttl`（起こした時の判定に使った応答のもの）。`w_1h`・`w_5m` は寿命ごとの定数 | フラグは今回の応答の形が違う（`form`）・範囲から欠けた（`truncated`）Stop でも読める |
| 避けた書き直し（`wait_end`） | 待ちを終えた応答の 1 つ前（状態の `last`） | 設計の決定 15 |

例: Opus 5.5・1 時間・`S` 250,000 で待ちが始まり（`n_max` 33）、`k` 20 の判定の時の応答も 1 時間とする。このとき費用の照合は `w` 2.0・`r` 0.05 で行い、フラグに `claude-opus-5-5`・`1h` を書く。起きた応答が 5 分へだけ書けば、次の Stop の `wake_result` は `ttl` `1h`・`r` 0.05（フラグの値）で、書き込みは `cache_write_5m × w_5m` で数える。同じ Stop の `Settled.turn.ttl` は `5m` になり、状態の `ttl` も `5m` に書き換わる。`wake_at` が None なので起こさず、`k` 21・`n_max` 33・`spent` は残る（待ちは通常の入力で終わるまで続き、`wait_end` の `w` は `last` の `5m` の 1.25）。続く Stop の応答が 1 時間へ書けば、同じ待ちの `k` 21 から、その応答の寿命とモデルで判定を続ける

### 処理の手順

1. `_target` が None なら 0（#4068 の受け入れ条件 2・3）
2. 状態を読む（ロックを取らない読み取り）。状態に待ちも起こしたフラグも無く、`works` が 0 なら（監視だけか、何も無い）、会話の記録を読まずに 0（#4068 の受け入れ条件 9。監視だけの子は設計の決定 16）。眠る hook も無いので世代を書く要が無い。待ちかフラグが残っていれば、`works` が 0 でも 3 へ進み、フラグの突き合わせと待ちの終わりを `_settle` で行う（待ちは 4 で「待つ子でない」として始めず、眠らない）
3. 会話の記録の末尾を `transcript.read_tail(t.transcript, tables.policy_value("hook.transcript-tail", "count"))` で 1 回だけ読み、`last_turn(tail, t.last_message, t.started)` を呼ぶ（読み取りの上限は利用者が直した宣言の値がそのまま効く。この `tail` を `_settle` の `wake_reply`・`opening` にも渡し、読み直さない）。`newer` なら何も書かずに 0（遅れて動いた古い hook。#4068 の受け入れ条件 2）。`unrecorded`（記録が読めず空の文字列が返ったときも含む。`read_tail` は `OSError` を投げない）なら、起こさず止めずに 0。その理由を `noted` に持たなければ、ロックの中で突き合わせてから `stop_waking`（`scope` `once`）と `noted` を書く（#4068 の受け入れ条件 7）。`form` なら、どの場合も 0 で終わり、この節の 4 へ進まない（形の違う応答では次の予約を作らない）。書くものは次の表で決める。突き合わせはロックの中で、`_settle` の (1)・(2) と同じく `Generation(at=ref.at, by=t.hook_id, message_id=ref.message_id)` で行う（起動が今の起動か、`bump_generation` で世代を取れたか）。`ref` が None のときだけは世代を突き合わせられないので、起動だけを突き合わせる

   | 場合 | 書くもの |
   | --- | --- |
   | `ref` が None（応答の ID か時刻が読めず、Stop と応答を結べない） | 起動が今の起動なら、書いていなければ `stopped`（記録の形）と `stop_waking`（`scope` `session`）を書く（#4068 の受け入れ条件 7 の「記録の形が違う」。時刻の欠けもこれに当たる）。世代・`pending` は変えない（世代の時刻が無く、フラグの突き合わせを Stop と結べないため。フラグは次の Stop の `_settle` の (4) が突き合わせる）。遅れて動いた古い hook がこの行に入っても書くものは変わらない。`last_context` は末尾の最後の応答だけを見るので、古い hook が読んだ形の違う応答は、今の Stop の hook も読む同じ応答で、その hook も同じ `stopped` を書くからである |
   | 起動が今の起動でない・世代を取れない（新しい世代・同じ時刻の先の書き込み元） | 何も書かない（遅れて動いた hook はフラグも `stopped` も変えない） |
   | 同じ `message_id` の取り合い（`bump_generation` の (2a)。時刻より先に見る） | 世代の `by` を `contested` に替え、先に取った hook がこの応答で始めた起こしていない待ちを (2a) のとおり取り消す（`wait_void`）。フラグ・`stopped` は変えない（`_settle` の (2) と同じ） |
   | 突き合わせを通り、起こしたフラグが無い | 書いていなければ `stopped`（記録の形）と `stop_waking`（`scope` `session`） |
   | 突き合わせを通り、起こしたフラグが在る | `_reconcile` で `wake_result` を書いてフラグを消し（起床の結果の照合は今回の応答の形の検証と分ける。#4199 の受け入れ条件 8）、続けて書いていなければ `stopped` と `stop_waking`（`scope` `session`） |

   突き合わせを通ったなら、`ref.at` は世代の時刻より新しい。`_finish` は起こした時刻を世代に書くので、フラグの起こした時刻は必ず `ref.at` より前である。フラグより前の応答を持つ hook は世代を取れず、上の表の「起動が今の起動でない・世代を取れない」の行に入る。そのためフラグの時刻と `ref.at` を別に比べない
4. `_settle` を呼ぶ。`stale` なら 0（#4068 の受け入れ条件 2。取り合いも含む）。`stopped` か `wait_stop` があれば 0（#4068 の受け入れ条件 4・8）。待つ子でなければ 0（`works` が 0。監視だけの子を含む）。寿命が未定（`Settled.turn.ttl` が None）なら 0（`_settle` が `stop_waking` を 1 度書いている。#4199 の受け入れ条件 4）
5. 倍率を 1 度だけ求める。`q = keep_cache.ratios(tables.policy_value, Settled.turn.model, Settled.turn.ttl)`。`TableError` などで読めなければ 0。`wake_at(Settled.turn.at, Settled.turn.ttl, q.wake_margin)` が None なら 0（5 分の寿命。起こす対象の外なので `stop_waking` は書かない。#4068 の受け入れ条件 11 の「やめた」は、起こす対象の待ちで起こすのをやめたときを指し、5 分の待ちは `wait_start` の `ttl` が `5m` であることで集計から見分ける）。`wake_at` が今の時刻以前なら、起こさず止めずに 0（この節の 3 と同じく `once` を 1 度）。`_wait` が偽なら 0（#4068 の受け入れ条件 1・2）
6. `_finish(t, gen, Settled.turn, q, stderr, clock)` を呼ぶ（#4068 の受け入れ条件 4・10・11）

### 異常系の処理

| 関数 | 失敗の種類 | 原因 | 検出する場所 | 検出したときの処理 | 利用者への通知 | 回復の手段 |
| --- | --- | --- | --- | --- | --- | --- |
| `run` | 例外 | 想定外の入力・読み書きの失敗 | 一番外の `except` | 0 を返す | 無し | 次の Stop で起動し直る |
| `bump_generation` | 書けない・ロックを取れない | 権限・ディスク・ラッパーが子を止める最中 | `signals.held`・`replace_json` | 0 を返す（世代の無い hook は古い hook と見分けられないので眠らない） | 無し | 次の Stop |
| `_finish` | ロックを取れない | ラッパーが子を止める最中 | `signals.held` | 起こさずに 0 | 無し | 次の起動で始め直る |
| `_finish` | 状態を書けない | 権限・ディスク | `replace_json` | 何も書かずに 0（`k`・`pending` は書かれていない） | 無し | 次の Stop |
| `_finish` | `wake` の行を書けない | 権限・ディスク | `append_cache_log` | 2 のまま（状態は起こしたことを持つ。集計は `wake` の無い `wake_result` を起こした回数に数え（`wake_id` の重複を除いた数）、当たりの率と `d` から外す） | 無し | — |
| `_settle`・`_finish` | 突き合わせが外れた | 新しい世代・起動の替わり | ロックの中の突き合わせ | 状態も起こす記録も書かずに 0 | 無し | — |

### 並行とロック

- 世代・状態・起こしたフラグ・起こす記録の読み書きは、すべて子を止めるロック（`signals.STOP_LOCK`。#4049 の実装で `wrapper.QUESTION_LOCK` から移し、main の `signals.py` にある）の中で行う。ラッパーが子を止める処理と、UserPromptSubmit の guard の世代の書き換えと、同時には動かない。書き込みのある区間（`_settle`・`_finish`・「処理の手順」の 3 の `stop_waking`・guard の世代の書き換え）は、どれも区間の初めに、まず今の起動を、次に世代を突き合わせる。起動が外れたら世代も書かない。ロックを出てから書く経路は無い
- 世代は時刻・書き込み元の ID・取った応答の `message_id` の組で、時刻が状態のものより新しいときだけ書き換える（`bump_generation`）。同じ時刻なら先に書いた書き込み元を残す。起こす hook の Stop は、時刻を比べる前に取り合いを見る（`_settle` の (2a)〜(2c) の順）。世代の `message_id` と同じ応答を別の書き込み元として取りに来たら、時刻に依らず `by` を `contested` に替える。同じ応答を 2 つの hook が取り合ったので、どちらも起こさない。眠る hook は `_wait` で替わったのを見て終わる。時刻の比べは、どれも同じ機械のシステムの時計どうしで行う（記録の `timestamp` は `timestamp_seconds` で UTC の UNIX 秒に直す。設計の決定 6）。時刻の取得元は次の 3 つである（設計の決定 6）。起こす hook が動き始めた時刻（`started`）は世代の時刻には使わない（`newer` の判定にだけ使う）
  - 起こす hook の Stop: 今回の応答と確かめた行の `timestamp`
  - UserPromptSubmit の guard: guard の `clock()`
  - 起床: ロックの中の `clock()`
- 待つ子かは Stop の入力の `background_tasks` を自分で分けて決める。同じ Stop で動く #4049 の同期の guard（`relay-marker`）が書く記録のファイルは読まないので、2 つの hook の実行の順に依らない
- 眠る hook は状態のファイル（`keep-<会話>.json`）の世代を `interval` ごとに読むだけで、ロックを取らない（読み違えても、起こす直前にロックの中で突き合わせ直す）

### ログと記録の出力

| 出来事 | 出力先 | 項目 |
| --- | --- | --- |
| 待ちの始まり | `cache-log.jsonl` | `kind: wait_start`・`wait_id`・`tokens`・`ttl`・`model`・`n_max` |
| 起こした | 同上 | `kind: wake`・`wait_id`・`wake_id`（`uuid4().hex`。状態の `pending` にも置く）・`tokens`・`age`・`k`・`n_max`・`spent`（この起床を足した後） |
| 起きた応答 | 同上 | `kind: wake_result`・`wait_id`・`wake_id`・`outcome`（`woke`・`preempted`（割り込みで起床の呼び出しが無かった。呼び出し 0・費用 0・`priced` 真）・`truncated`・`unknown`（どちらも `priced` 偽））・`hit`（最初の呼び出しの読み出しで決める。`woke` のほかは null）・`tokens`（フラグの `S`）・`first_read`・`calls`・`cache_read`・`cache_write_1h`・`cache_write_5m`・`output`（起床に属する全呼び出しの合計。`priced` が偽なら `cache_write_1h`・`cache_write_5m` を書かない）・`priced`・`model`・`ttl`（どちらもフラグの値。起こした時の判定に使った応答のもの）・`w_1h`・`w_5m`・`r`・`out` |
| 待ちの終わり | 同上 | `kind: wait_end`・`wait_id`・`opened_at`（待ちを終えた入力の行の時刻）・`age`（`opened_at − last.at`）・`k`・`hit`（待ちを終えた最初の呼び出しの読み出しが `tokens` の `cache.hit.ratio` 以上か）・`cache_read`（その読み出し）・`tokens`・`ttl`・`model`（どれも更新前の状態の `last`。待ちの終わりの直前の応答）・`w`・`r`（`last.ttl` の書き込みの倍率と `last.model` の読み出しの倍率）。待ちを終えた応答の書き込み・出力は書かない（設計の決定 15） |
| やめた・一時的に起こさなかった | 同上 | `kind: stop_waking`・`wait_id`・`reason`・`scope`（`session`・`wait`・`once`）・`k`・`n_max` |
| 待ちを取り消した | 同上 | `kind: wait_void`・`wait_id`・`reason`（`contested`）。取り合った応答で始めた、起こしていない待ち（`_settle` の (2a)） |

どの行も `at`・`launch`・`session_id` を持つ。ラッパーの `log.jsonl` には書かない（書き込み元を 1 つにする）。

### 既存の部品の再利用

| 新設するもの | 見つけた既存の部品 | 見つけ方 | 使う・使わない理由 |
| --- | --- | --- | --- |
| 会話の記録の末尾の読み取りと最後の応答 | #4089 の `adapters/transcript.read_tail`・`last_context` | #4089 の詳細設計の「#4068 との受け渡し」 | そのまま使う。末尾は `read_tail` を `hook.transcript-tail` の宣言の大きさで 1 回だけ呼び、`last_turn`・`wake_reply`・`opening` は同じ文字列を受ける。固定長の `agents.tail` は使わない（利用者が直した読み取りの上限が効かなくなる）。読めないときの扱いも `read_tail` の空の文字列に従う |
| バックグラウンドの作業の分け方 | #4049 の `relay_busy.task_kind`・`work_count` | #4049 の詳細設計 | そのまま使う |
| 中継のマーカー | `markers.mark` | `grep -n "def mark" src/ndf/domains/relay/markers.py` | 使わない。`markers.mark` のマーカーは、最初に届いた入力が消費する。そのため、起こした後に利用者の入力が先に届くと、利用者の入力が起こす文の印を消費し、起こす文の入力と利用者の入力を区別できない。代わりに `prompt` の頭で見分ける（設計の決定 10） |
| ロック・起動の突き合わせ | `signals.held`・`STOP_LOCK`・`current_launch` | #4049 の詳細設計 | そのまま使う |
| バージョンの確かめ | `adapters/cli_floor.resolve`・`version_at`（`account_choice.claude_version` が使う、実パスごとの版の記録） | `grep -n "version_at\|cli_floor" src/ndf/domains/relay/*.py` | そのまま使う。候補ごとの最終の環境で実パスを解き、記録から版を読む。`require` は起動先を選ぶ前の環境で解くので門に使わず、変えない（「ラッパー」の節） |
| 対象の会話の限定 | #4049 の `signals.relay_session`・`SESSION_ENV` | #4049 の詳細設計 | そのまま使う。会話の ID の形は `SESSION_ID_RE`（`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`）を `signals` に足し、`keep-<会話>.json` の名前を作る前に突き合わせる |

### 単体テストの観点

| 関数 | 観点 |
| --- | --- |
| `run` | `NDF_RELAY_REWAKE` の無い環境（登録の無い起動からエントリポイントを直接呼んだ）では、中継の保存先・起動・会話の ID がそろっていても、眠りの関数 0 回・状態と記録の読み取り 0 回で 0。中継の保存先の変数なしで眠りの関数 0 回・記録の読み取り 0 回で 0。`session_id` が `NDF_RELAY_SESSION` と違う入力・UUID の形でない会話の ID では、状態を読まず書かずに 0。状態も待ちも無い Stop で読み取り 0 回。状態も待ちも無く、裏が監視だけ（`ndf lanes wait` 1 件・`ndf progress wait` 1 件）の Stop でも、会話の記録の読み取り 0 回・眠りの関数 0 回・状態の書き込み 0 回で 0（設計の決定 16）。作業 1 件と監視 1 件が並ぶ Stop は待つ子として眠る。待ちが残る状態で、作業の知らせで始まった監視だけの Stop は、`wait_end` を書いて待ちを始めず、眠りの関数 0 回で 0。待つ子で 3,000 秒後に 2、標準エラーに起こす文。眠る間に新しい Stop・UserPromptSubmit で世代が替わると 0。古い応答の時刻を持つ Stop の hook が guard の後に動くと、世代を戻さず何も書かずに 0。質問のファイルが在ると 0。2 つの hook を同じ時刻まで眠らせても 2 は 1 つ。完全な前の応答だけが残り `last_assistant_message` が違う入力では、眠りの関数 0 回・`stopped` 無しで 0。起こす時点が過去になる見本でも 0。作業 1 件の Stop の hook の `started` の後に、作業 0 件の Stop の同じ文の応答が記録された見本では、古い hook が世代も状態も書かずに 0 で、新しい hook が作業 0 件で決める（その応答は起床の文で始まったので、待ちを終えず眠らない）。古い hook の `started` をその応答の時刻より後にずらした見本（動き始めが遅れた）では、古い hook と新しい hook のどちらを先に流しても世代が `contested` になり、2 は 0 個。古い hook（作業 1 件）が先にロックを取る順では、古い hook が書いた `wait_start` の後に `wait_void` が 1 行書かれ、状態に待ちが残らず、その記録の集計で待ちの数が 0。新しい hook（作業 0 件）が先の順では、`wait_start` も `wait_void` も書かれない。末尾の読み取りは `read_tail` を 1 回だけ呼び、大きさは差し替えた `hook.transcript-tail` の値 |
| `_settle` | 起こす文から始まった応答の最初の呼び出しの読み出しが前の `S` の 0.89 倍で `stopped`、0.9 倍で `hit`。最初の呼び出しの書き込みが両方 0 でも `wake_result` が 1 行で、`hit` は読み出しで決まる。起こす文に従わず Tool を使った起床で `wake_result` の `cache_read`・`cache_write_1h`・`cache_write_5m`・`output` が全呼び出しの合計で、`w_1h`・`w_5m` を持つ。寿命別の項目の欠けた呼び出しを含む起床で `priced` 偽・2 つの合計が無い。`wake_reply` が `truncated` の見本で `hit` null・`priced` 偽の `wake_result` が 1 行、フラグが消え、`stopped` も `wait_stop` も無い。フラグの直後の入力が通常の入力なら判定せず、`outcome` `preempted`・`hit` null・`calls` 0・費用の項目 0・`priced` 真の `wake_result` が 1 行でフラグが消える。形を決められなければ判定せず、`outcome` `unknown`・`hit` null・`priced` 偽の `wake_result` を 1 行書き、`wait_stop` を立て、同じ Stop で `wait_end` も `wait_start` も書かず、`k`・`n_max`・`wait_stop` が残る。その Stop の作業も監視も 0 件（待つ子でない）でも同じで、`wait_end` を書かない。続く Stop で `opening` が `wake`・`unknown` の間も、バックグラウンドの件数に依らず残り、`normal` の Stop で初めて `wait_end`。通常の入力で始まった待ちの後の Stop で `wait_end` が 1 行で、`tokens`・`ttl`・`model`・`w`・`r` が更新前の `last`（1 時間・Opus 5.5・100,000 の `last` と、5 分へ書いた別のモデルの今回の応答の見本で 100,000・`1h`・Opus 5.5・2.0・0.05）、`cache_read` と `hit` が待ちを終えた最初の呼び出し（最初が 20,000・後の Tool の呼び出しが 101,000 の見本で 20,000・偽）、`opened_at` が入力の行の時刻（Stop の時刻でない）。`wait_end` の後に `last` が今回の応答になる。`wait_end` の後の待つ子の Stop で `k` 0・`spent` 0・`n_max` を決め直した `wait_start`。寿命: `turn.ttl` が None で状態の `ttl` が `1h` なら `1h` で待ちを始め、状態にも無ければ `wait_start` も `n_max` も無く、`stop_waking`（`once`）が会話で 1 行、状態の `ttl` は書き換わらない。`turn.ttl` が `1h` なら状態の `ttl` が `5m` でも `1h`。ロックの外の読み取りの後に別の hook が状態の `ttl` を `1h` にした順で、`_settle` は `1h` で補う。同じ `message.id` の世代を別の hook が持つ状態で呼ぶと、世代の `by` が `contested` になる。状態の `wait_id` がその世代の `at` で `k` 0・`pending` 無しなら、待ちの項目が消えて `wait_void` が 1 行書かれる。`wait_id` が別の時刻・`k` 1・`pending` 在りのどれかなら、待ちも記録も変わらない。どの場合も `ttl`・`last`・`stopped`・`noted` は変わらない。新しい世代がある状態で呼ぶと、`pending`・`wait_stop`・起こす記録のどれも変えない。`current_launch` が別の起動の状態で呼ぶと、世代も含めて何も変えない（世代が新しい時刻でも） |
| `_reconcile` | 起床の 2 回目の呼び出し（今回の応答）だけ `cache_creation` の寿命別の項目が欠けた見本で、`last_turn` は `form` を返し、`run` が `priced` 偽・`hit` が最初の呼び出しの読み出しで決まった `wake_result` を 1 行書いてフラグを消し、その後に `stopped`（形）と `stop_waking`（`session`）を書く。`form` の分岐の表（「処理の手順」の 3）の 5 行をそれぞれ確かめる。フラグの無い状態の `form` で突き合わせを通ると、`wake_result` を書かず、`stopped`（形）と `stop_waking`（`session`）を 1 行ずつ書いて 0。同じ見本をもう一度流しても行は増えない。`form` の応答の `message.id` か `timestamp` が読めない見本では、世代とフラグは変わらず、`stopped`（形）と `stop_waking`（`session`）を 1 行ずつ書いて 0。同じ見本をもう一度流しても行は増えない。`current_launch` が別の起動の状態で同じ見本を流すと、何も書かずに 0。同じ起動で、新しい待ちの `pending` が立った後に、その前の応答の時刻を `ref` に持つ遅れた hook が `form` で動く順では、世代を取れずにフラグも `stopped` も記録も変えず 0。どの場合も眠りの関数の呼び出しは 0 回 |
| `_finish` | 起こすたびに `spent` が判定の時の `S` の `r × S + d` だけ増える。世代が `contested` の状態で眠りから戻ると、何も書かずに 0。上限の `stop` は `wait_stop` だけを書き、`stopped` を書かない。新しい世代がある状態で眠りから戻ると、上限でも `wait_stop`・`stop_waking` を書かない。状態が書けない順で記録も変わらず 0 で、中継のマーカーを書かない（`markers.mark` の呼び出し 0 回）。`append_cache_log` が `wake` の行で失敗する順で 2 を返し、状態の `pending` が残り、次の Stop の `wake_result` の `tokens`・`model`・`ttl` がフラグと同じ |

## hook の登録（`registration`・`runtimes.json`・`entry.py`）

起こす hook はプラグインの登録（`hooks/claude.json`）に足さず、ラッパーが中継の子の起動にだけ `--settings` で渡す。中継の外の会話の Stop では、この hook のプロセスは起動しない（設計の決定 1）。登録を渡した子では、バックグラウンドの作業も予約も無い応答を含めて Stop ごとに 1 回起動する。Stop の登録は `matcher` を取らず、登録の条件で対象外の Stop を除けないためである（「処理の手順」の 2 の早期終了は起動の後に効く）。この追加の費用は、設計の決定 7 の実測 11 で測り、通ったバージョンだけを `cache.rewake` の `versions` に入れる。実測 11 は、短い応答を 50 回続ける中継で、hook の起動から終了までの 95 パーセンタイルと応答 1 回の所要時間を、登録ありとなしで比べる。開始時に分かる条件（中継の子・版・利用者の `--settings`・`cache.wake` の届き）は既に登録の条件で、待つ子かは Stop ごとにしか決まらないので、登録はこれ以上絞れない。そのため実測 11 は、対象外の Stop も含めた 1 日あたりの追加の起動の数（キーでない従量の接続の `--version` の追加の起動を含む）と、経路ごとに測って回数で重みを付けた追加の CPU 時間を、既存の同期の Stop の hook の量と別々に比べ、どちらかを越えれば通さない（設計の決定 7 の 11 の「登録を絞れるかと、起動の総数」）。

- `runtimes.json` の claude の `Stop` に `"async_rewake": {"budget": "stop-rewake", "timeout_max": 3300}` を足す。`timeout_max` はバックグラウンドの登録だけの上限で、事象の `timeout_max`（同期の Stop の登録の上限。ランタイムの 600）は変えない。この行は `render` が読まず、`rewake_entry` と予算の検査だけが読む
- 方針のファイル `contracts/limits/hook.budget.stop-rewake.json` を今の `hook.budget.*` の形（`p95_ms`・`deadline`・`margin`・`timeout`・`inject_chars`）で置く。値は次のとおりである。3,300 秒を受け付けないバージョンでは、設計の決定 7 のとおりバージョンの一覧に入れない

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
- 検査: `budget.rewake_registrations(runtime: Optional[str] = None) -> list[dict]` を足し、`async_rewake` を持つ事象ごとにバックグラウンドの行を 1 つ作る（登録のファイルからは読まない。`timeout` は `rewake_entry` が使うのと同じ `rewake_budget` の `timeout`、`async_rewake` は真）。`budget_registrations` は今の行にこのバックグラウンドの行を足す。`registrations_of` は、登録のファイルが在るランタイムでだけ `_file_registrations` の行にバックグラウンドの行を足す。配布物の `hooks/claude.json` にバックグラウンドの登録は載らないので、ファイルの行だけではバックグラウンドの予算と `cache.wake` を検査できない。ファイルの無いランタイムでは `budget_registrations` が足した行をそのまま使い、重ねて足さない。どちらの経路でもバックグラウンドの行は事象ごとに 1 つである。`_file_registrations` は登録のファイルの登録ごとに `async_rewake`（登録の `asyncRewake` が真か）を行に足す。`_registration_errors` は、バックグラウンドの行を `rewake_budget` の締め切りと `async_rewake.timeout_max` で突き合わせる。ほかの行は今のとおり事象の予算と `timeout_max` で突き合わせる。登録のファイルに `asyncRewake` が真の登録があれば、事象によらず破れとする（プラグインを入れた全ての会話で起動させない）。バックグラウンドの行には、`rewake_reach` の診断も破れとして足す
- `budget.rewake_reach(budget: Budget) -> Optional[str]` を足す。`cache.wake` の `seconds`（余白）と `interval`（確かめる間隔）を `tables.policy_value` で読み、次のどれかなら理由の文（値と超えた秒を含む）を、どれでもなければ None を返す。`interval` が 0 以下か、`ceil((TTL_SECONDS["1h"] − seconds) / interval)` が `keep_cache.MAX_CHECKS`（300 回）を越える（余白 550・間隔 10 は 305 回で越える）。`seconds` が 0 未満か `TTL_SECONDS["1h"]` 以上。`TTL_SECONDS["1h"] − seconds + interval + budget.margin` が `budget.timeout` を越える（既定の 600・60・240 で 3,000 + 60 + 240 = 3,300 秒ちょうど。余白を 120 秒にすると 3,780 秒で越える）。`registration.rewake_unreachable()` は `rewake_budget("claude", "Stop")` を渡してこの値を返す。`cache.wake` の `read_by` に `ndf.domains.hooks.budget` を足す
- `entry.py` は `len(argv) > 3 and argv[3] == "rewake"` なら `call("ndf.domains.hooks.rewake", "run", argv)` の値を返す。ほかは今のとおり `call("ndf.domains.hooks.main", "run_entry", argv)` の値を返す。長さを先に確かめるのは、既存の登録が実行ファイル名・ランタイム・事象の 3 つ（`entry.py claude PreToolUse`）で呼ぶためである。`argv[3]` を先に読むと `IndexError` で落ち、同期の guard が動かない

| 観点 | テスト |
| --- | --- |
| 中継の外で起動しない | `render("claude")` と生成した `hooks/claude.json` のどの事象にも `asyncRewake` の登録が無く、Stop の登録は 1 つ。`hooks/claude.json` の Stop に `asyncRewake: true` の登録を足すと `check_budget` が破れとする |
| バックグラウンドの登録 | `rewake_settings(<一時の根>)` の Stop の登録が 1 つで、`asyncRewake: true`・`timeout`（`hook.budget.stop-rewake` の値）を持ち、command が `<一時の根>/hooks/entry.py` の絶対パスと `rewake` を含み、`${CLAUDE_PLUGIN_ROOT}` を含まない。空白を含む根でも引数が 1 つに保たれる。`contracts/limits/hook.budget.stop-rewake.json` の値を差し替えると `timeout` が変わる。codex の登録は変わらない |
| 予算の検査 | 生成した登録で `check_budget` が空。同期の Stop の登録の timeout を 601 にすると破れ（上限 600 のまま）。バックグラウンドの予算の timeout を 3,301 にすると破れ。バックグラウンドの予算の timeout を同期の予算の締め切り未満にしても、バックグラウンドの予算の締め切り以上なら破れない。`async_rewake` の行を消すと `rewake_entry` が `TableError`。`cache.wake` の `seconds` を 120 にすると `rewake_reach` が理由の文を返し、`check_budget` が破れとする。`interval` を 0 にしても、`seconds` 600 で 9 にしても、`seconds` 550・`interval` 10 にしても同じ。既定の値では None。生成した `hooks/claude.json` が在る根でも無い根でも、`registrations_of` の結果にバックグラウンドの行がちょうど 1 つ含まれ（無い根で `seconds` を 120 にしても診断は 1 件）、その根で `seconds` を 120 にすると `check_budget` が破れとする（配布物を使う検査の経路） |
| エントリポイント | 既存の登録と同じ 3 つの引数（`["entry.py", "claude", "PreToolUse"]`・`["entry.py", "claude", "UserPromptSubmit"]`）で、差し替えた `run_entry` が同じ引数で 1 回呼ばれ、その値が返る（`IndexError` にならない）。`rewake` の引数で `run` の 2 がそのまま返り、`run` を読めなければ 0。`NDF_RELAY_REWAKE` の無い環境で `rewake` の引数を渡すと、眠りの関数 0 回・状態と記録の読み取り 0 回で 0（登録の無い起動から直接呼んだとき） |

## UserPromptSubmit の guard（`turn`）

- 中継の下の UserPromptSubmit で、子を止めるロックの中で次の 3 つを行う。中継の下とは、次の 3 つがそろうときである。中継の保存先と起動の ID（`NDF_RELAY_LAUNCH`）がある。入力の `session_id` が `NDF_RELAY_SESSION` と同じである。会話の ID が `SESSION_ID_RE` の形である
  1. `signals.rewake_on(<保存先>, <自分の起動の ID>)` が偽なら何も書かない。偽になるのは次の 2 つのときである
     - 今の起動（`launch.json`）が自分の起動でない（起動し直した後の旧い起動の guard が、新しい起動の予約を失効させない）
     - 今の起動に起こす hook の登録を渡していない（バージョンの一覧に無い子で状態を作らない）
  2. `bump_generation(Generation(at=clock(), by="turn"))` を呼ぶ（眠る hook の予約を失効させる）。状態のファイルが無い会話では、世代だけを持つ状態を作る。最初の待ちでバックグラウンドの hook より先に入力が届いても、後から動く hook は入力より古い応答の時刻しか持たず、世代を取れない
  3. 状態に待ち（`wait_id`）が在り、起こしたフラグ（`pending`）が無ければ、同じ区間で `opened` にその `clock()` を書く（既に在れば書き換えない）。起こす文の入力は、必ず `_finish` が `pending` を書いた後に届くので、`pending` の無い待ちへの入力は通常の入力である。`pending` が在る間の入力は、入力の `prompt` で見分ける。頭が `WAKE_HEAD` なら起床の文で書かず、ほかの入力（利用者の割り込み・バックグラウンドの作業の知らせ）なら `opened` を書く。起床の文が `prompt` に載ることは設計の決定 7 の実測 5 で確かめる。載らないバージョンはバージョンの門の一覧に入れない（設計の決定 10）。フラグの直後の入力の行が末尾の範囲の外へ押し出され、`wake_reply` が `truncated`、`opening` が `unknown` を返すことがある。そのときも `opened` があれば `_settle` の (5) で待ちを終える。`opened` は、待ちを旧い `k`・`wait_stop` から切り離すために使う。通常の入力の後の Tool の結果が大きく、次の Stop の `opening` が入力の行を末尾の範囲の外へ押し出されて `unknown` を返すときに効く
- この guard はプラグインの登録の今の UserPromptSubmit の上で動くので、プロセスは増えない。状態のファイルを作るのは、起こす hook の登録を渡した中継の子だけである
- 起こす文の見分け: guard（`turn.check`）は今、#3995 の数え手の `svc.counter.reset(source=...)` を入力ごとに呼ぶ。数え手は、判断開始の値の知らせを利用者の入力ごとに数え直すカウンターである。中継のマーカーを消せた入力は `source="relay"` で呼び、数え手は数え直さない。ほかは `"human"` で呼び、数え手は数え直す（利用者の入力の番号を 1 つ進める）。頭が `WAKE_HEAD` の `prompt` の入力も `"relay"` で呼び、利用者の入力（`"human"`）として数え直さない。`"human"` で数え直すと、起こすたびに判断開始の値の知らせが出直し、文脈の大きい子に知らせが増える（設計の決定 10）。ラッパーが初回のプロンプトに置く今の中継のマーカー（`markers.take`）の扱いは変えない

| 観点 | テスト |
| --- | --- |
| 失効 | 眠る hook の世代より新しい入力で世代が替わり、眠る hook が 0 で終わる |
| 最初の待ちの順 | 状態のファイルが無い会話で guard が先に動くと、世代（`by` `turn`）だけの状態ができる。その後に入力より前の応答の時刻を持つ Stop の hook が動くと、世代を取らず、状態も起こす記録も書かずに 0 |
| 起動の突き合わせ | `launch.json` が別の起動の ID の状態で guard を呼ぶと、世代は変わらず、状態が無ければ作らない。新しい起動の眠る hook は起こす。`launch.json` の `rewake` が偽の起動では、状態を作らず世代も書かない |
| 対象の限定 | `session_id` が `NDF_RELAY_SESSION` と違う入力（入れ子の claude）では世代を替えず、眠る hook は起こす |
| 見分け | 頭が `WAKE_HEAD` の `prompt` の入力で guard を呼ぶと、`svc.counter.reset` が `source="relay"` で呼ばれ、`"human"` では呼ばれない |
| 待ちを終えた入力 | 待ちが在り `pending` の無い状態で guard を呼ぶと `opened` が書かれる。`pending` が在る状態では、`prompt` の頭が `WAKE_HEAD` なら書かれず、利用者の入力の `prompt` なら書かれる。`pending` が在る間に利用者の入力が先に届き、その応答の大きな Tool の結果でフラグの応答も入力の行も末尾の外へ出た見本（`wake_reply` が `truncated`・`opening` が `unknown`）で、`hit` null の `wake_result` と、`hit`・`cache_read` が null の `wait_end` が 1 行ずつ書かれ、`k`・`spent` が消える。上限で `wait_stop` が立った待ちの後に guard が `opened` を書き、次の Stop の末尾が `hook.transcript-tail` を越える Tool の結果で入力の行を含まない（`opening` が `unknown`）見本で、`_settle` が `hit`・`cache_read` が null の `wait_end` を 1 行書き、`k`・`n_max`・`spent`・`wait_stop`・`opened` が消え、待つ子なら同じ Stop で新しい `wait_start` を書く |

## ラッパー（`wrapper._start`）

- バージョンの門は、起動先（候補）ごとに、実際に起動する実行ファイルで決める。起動先を選ぶ前の `_start` の `cli_floor.require`（下限の確かめ）の版は門に使わない。従量の接続の設定は子の環境の `PATH` を上書きできる（`account_choice.fallbacks` が最終の環境で `claude` を解き直すのと同じ理由）。そのため、選ぶ前の環境で解いた `claude` と、子として起動する `claude` が別の実行ファイルになり得る。`cli_floor.require` は変えない
- 起動の手順（`_start`・`wrapper_launch._try`・`_fallback`）:
  1. `_start` は、次の 3 つがそろうときだけ、`_launch_target` へ渡す起動の引数（`[CLAUDE, *head, *self.s.args, *once]`）とは別に、`head` の後に `--settings` と `json.dumps(registration.rewake_settings(<根>))` を足した引数（以下「登録つきの引数」）を作る。そろわなければ作らず、どの候補にも登録を渡さない
     - 受け継ぐ引数（`claude_args.Parsed.carried`）に `--settings` の語も `--settings=` で始まる語も無い
     - `registration.rewake_unreachable()` が None（起こす時点と確かめる間隔がバックグラウンドの `timeout` の内に届く）。理由の文が返れば、ラッパーの `log.jsonl` に `{"kind": "rewake_off", "reason": <理由の文>}` を起動ごとに 1 行書く（`ndf check` も同じ理由で破れとする）
     - `limits.texts("cache.rewake", "versions")`（`contracts/limits/cache.rewake.json` を読む）が空でない
  2. 1 で登録つきの引数を作ったときだけ、2〜4 を行う（作らなかった起動では版を読まず、5 で登録の無い引数を選ぶ。`--version` は増えない）
     - やること: 候補が `metered`（従量の接続）なら、`_try` は従量の接続の設定を候補ごとに 1 回だけ読む（新設の `account_choice.metered_of(store, env) -> Union[Decl, str]`。今の `_decl_of` を公開した名前）。読めなければ（理由の符号）、版を読まず、用意もせずに、今のとおりその理由で飛ばす。読めた値 `decl` を、版を読む環境（下の `version_env`）と起動先の用意の両方へ同じ値で渡す
     - 渡し方: `target_env`・`key_target` に引数 `decl: Optional[Decl] = None` を足し、在れば `_decl_of` を読み直さない。`target_env` から `key_target`、`key_target` から `_launch_made` へも同じ値を渡す。`key_child` も `decl` を受ける
     - 版を読む環境: 新設の `account_choice.version_env(env: Mapping[str, str], decl: Optional[Decl]) -> dict[str, str]` が返す。`decl` があれば `session_env.metered_env` に当てた環境を返す。子と同じく親の認証の変数・秘密の変数を外し、設定の `PATH` を重ねた環境である。None（既定のログイン・登録済みアカウント・`key:<名前>`）なら `env` をそのまま返す。設定のファイルを読まず、ファイルを書かず、届けも検証も `--version` もしない
     - 理由: 間に `ndf relay accounts` などが `metered.json` や環境の設定を書き換えることがある。設定を 1 回だけ読めば、版を判定した実行ファイルと、用意して起動する子の `PATH` が同じ 1 回の読み取りから決まる。子の環境の `PATH` を変えるのは従量の接続の設定だけで（`account_env`・`key_env` は `PATH` を触らない）、`metered_env` は用意のときと同じ関数に同じ `decl` を当てる。そのため `version_env` が返す環境の `PATH` は、用意した `Target.env` の `PATH` と一致する
  3. その環境（以下 `venv`）と作業ディレクトリで `exe = cli_floor.resolve(CLAUDE, venv, cwd)` を呼ぶ。`spawn_pty` が起動する実行ファイル（`cli_floor.locate(argv[0], Target.env, cwd)`）と同じものを指す。版（`cli_floor.version_at("claude", exe, venv, cwd)`）を読む時点は、`--version` の起動が増えるかどうかで次の 3 つに分ける。`version_at` は `cli_floor` の既存の記録（実パス・更新時刻・大きさの組）を使う。同じ記録を、キーの子の版の下限の確かめ（`account_choice._gate_off` の `claude_version`）も読む。`--version` の起動は `venv`（親の API キー・認証ヘッダーを外した従量の子と同じ環境）で行い、親の環境は渡さない

     | 候補 | 版を読む時点 | `--version` の起動 |
     | --- | --- | --- |
     | `exe` が `_start` の `require` と同じ実行ファイル（`metered` 以外の候補はいつもこれ） | 用意の前 | 記録を読むだけで打たない |
     | 別の実行ファイルを指す従量の接続（API キーの設定のキーの子も、それ以外も） | 用意の後 | 先に登録の無い引数で用意し（`target_env`・`key_target` の `_build` と `verify`）、実効の設定の確かめ（`metered_overridden`・`key_overridden`）と固定できるか（`key_pin`）を通った候補でだけ版を読む。キーの子では、`verify` の中の版の下限の確かめ（`_gate_off`）がこの 2 つを通った後に同じ実行ファイルの版を今も読むので、その 1 回が `cli_floor` の記録に残り、用意の後の `version_at` は記録を読むだけで打たない（版の下限の確かめと起こす登録の判定が同じ 1 回の版を使う。候補あたりの起動は今と同じ 1 回）。キーでない子は通った候補でだけ `--version` を 1 回起動する。`cli_floor` の記録はラッパーのプロセスの中に実パス・更新時刻・大きさの組ごとに残るので、同じ実行ファイルの候補を `--resume` の再開や切り替えで何度確かめても、打つのはラッパーのプロセス 1 つにつき最初の 1 回だけである。この追加の起動は、有効化の PR の実測 11 の起動の総数に入れて判定する（設計の決定 7 の 11）。どちらも、確かめや固定で落ちる候補では打たない。版が一覧に在れば登録を足す。キーでない子は、用意した `Target.argv` の `head` の直後に `--settings` と `rewake_settings` の JSON を差し込む（`_build` は `--model` をオプションの最後へ移すだけで、`head` と `--settings` に触らない）。キーの子は、`_compose` が作った 1 つの `--settings` の JSON（登録つきの引数を作れる起動では利用者の `--settings` が無いので、ファイルでなく引数の JSON の文字列で、一時ファイルも作らない）の `hooks.Stop` に `rewake_entry` を足して書き直し、`--settings` を 2 つにしない。足す設定は `hooks.Stop` だけで、`verify` が見る接続先・認証・helper を変えず、固定（`Resolved.pins`）は設定のファイルにだけ掛かって引数の JSON には掛からないので、確かめ直さず固定も書き直さない |
  4. 用意の前に版を読む候補（3 の表の 1 行目）だけ、ここで引数を選ぶ。登録つきの引数があり、`version` が一覧のどれかと完全に一致すれば（`semver` の大小では比べない）登録つきの引数を、ほか（一覧に無い・None・登録つきの引数が無い）は登録の無い引数を使う。用意の後に版を読む候補（3 の表の 2 行目）は、ここで引数を選ばない。登録の無い引数で用意した `Target.argv` に、版が一覧と完全に一致したときだけ 3 の表のとおり登録を足し、その `Target.argv` をそのまま起動に使う。`_start` が作った登録つきの引数で置き換えない（置き換えると、キーの子の合成した `--settings` の `apiKeyHelper` と `ConfigChange` の hook が落ちる）
  5. 用意の前に版を読む候補は、選んだ引数で起動先を 1 度だけ用意する（`target_env`・`key_target`。既定のログインは引数をそのまま使う）。用意の後に版を読む候補は 3 の表のとおり登録を足すだけで、用意し直さない。どちらでも、使用の届け・一時ファイル・実効の設定の確かめ（`verify` の `effective_env`）は候補ごとに今と同じ 1 回である。API キーの従量の子では、`account_choice._source` が選んだ引数の 1 つの `--settings` を元の設定として読む。`_compose` が、それに `apiKeyHelper` と `ConfigChange` の hook を合わせて 1 つの `--settings` に作り直す。そのため `hooks.Stop` の起こす登録はそのまま残る（`_compose` は元の `hooks` の `ConfigChange` 以外のキーを保つ）。登録つきの起動先だけ、`Target.env` に `NDF_RELAY_REWAKE=1` を足す
  6. その候補を起動する直前（`_spawn_target` の前）に、`signals.set_launch(self.dir, launch, rewake=<登録つきの起動先か>)` で今の起動の記録を書き直す（`_start` が候補を試す前に書く記録は `rewake` 偽。子の最初の UserPromptSubmit より前に真偽が決まる）。UserPromptSubmit の guard が `signals.rewake_on` で読む。起動の行（`account`）に `rewake`（真偽）・`claude_version`（3 で読んだ `version`）・`claude_path`（3 で解いた `exe`）・`version_probed`（3 の `version_at` が `--version` を実際に打ったか。`cli_floor` に新設する `seen(path) -> bool`（実パス・更新時刻・大きさの組が記録に在るか。`--version` を打たない）を `version_at` の前に読み、在れば偽）を足す。後の 2 つは、有効化の後に版の確かめの追加の起動を実際の回数で見直すための項目である（設計の決定 7 の 11）。登録つきの引数を作らなかった起動（3 を通らない）では、`claude_path` と `version_probed` を書かない

  `<根>` は `self.launched_root` があればそれ、無ければ `tables.PLUGIN_ROOT`。利用者の `--settings` があるときは、受け継ぐ引数を並びも中身も変えずに渡す（`--settings` は最後の 1 つだけが効くので、重ねると利用者の設定が効かなくなる。#1543 の実測）。登録の無い起動先では、親の環境に `NDF_RELAY_REWAKE` があっても子の環境から外す（登録の無い子の hook が眠らないように）。こうして候補ごとに、登録・`NDF_RELAY_REWAKE`・起動の記録の `rewake` が、その候補の実行ファイルの版の 1 つの判定でそろう。`--resume` で起動し直すときも同じ判定で毎回行う（`--settings` は起動ごとの引数で、会話に残らない）
- 子を動かしている間の見回り（`Wrapper._loop`）ではバージョンを読まない

| 観点 | テスト |
| --- | --- |
| バージョンの門 | `versions` を `["2.1.330", "2.1.332"]` にした値で、バージョン 2.1.330 と 2.1.332 は足す・2.1.331（両端の間の測っていないバージョン）・2.1.329・2.1.333 は足さない。読めない・一覧が空でも足さない。足した引数の JSON が `rewake_settings` と同じ。足した起動の子の環境に `NDF_RELAY_REWAKE=1` があり、足さない起動（一覧に無いバージョン・利用者の `--settings`）では、親の環境に `NDF_RELAY_REWAKE=1` があっても子の環境に無い |
| 起動先ごとの実行ファイル | 親の `PATH` の `claude` が 2.1.330（一覧に在る）で、従量の接続の設定が `PATH` を上書きして 2.1.333 の `claude` を指す見本で、その子の起動の引数の `--settings` に `hooks.Stop` の起こす登録が無く（`apiKeyHelper`・`ConfigChange` は在る）、子の環境に `NDF_RELAY_REWAKE` が無く、今の起動の記録の `rewake` が偽。向きを逆にした見本（親が 2.1.333・設定の `PATH` が 2.1.330）でも、`_start` の時点で一覧が空でなければ、その子には登録・変数・`rewake` 真がそろう。同じ実行ファイルの候補では `--version` の実行が `_start` の 1 回だけ。別の実行ファイルを指す API キーの従量の接続では、版の下限の確かめと起こす登録の判定を合わせて `--version` の実行がその実行ファイルで 1 回（用意の後の版の取得は打たない）。`verify` が `metered_overridden`・`key_overridden`・`key_pin` を返す見本では 0 回。別の実行ファイルを指すキーでない従量の接続では、差し替えた `verify` が通れば 1 回、`metered_overridden` を返せば 0 回、設定が読めない（`metered_unavailable`）なら 0 回。版が一覧に在る見本では、キーでない子の起動の引数が登録つきの引数で用意したときと同じ並び（`head` の直後に `--settings`、`--model` は最後）、キーの子の起動の引数の `--settings` が 1 つで `apiKeyHelper`・`ConfigChange`・`hooks.Stop` の起こす登録の 3 つを持ち、どちらも `verify` の呼び出しは 1 回で固定のファイルは書き直さない。版が一覧に無い候補でも、従量の接続の候補では差し替えた `verify` の呼び出しが 1 回、登録済みアカウントの候補では `account_dir.prepare`（使用の届け）が 1 回で、その子を終えた後に届けが残らない。`version_env` が返す環境の `PATH` が、既定のログイン・登録済みアカウント・`key:<名前>`・`PATH` を持つ従量の接続・持たない従量の接続のどれでも、用意した `Target.env` の `PATH` と同じ。差し替えた `metered_decl` が 1 回目と 2 回目で別の `PATH`（2.1.330 と 2.1.333）を返す見本で、1 つの候補の中の呼び出しが 1 回だけで、版を判定した実行ファイルと起動する子の `PATH` がそろう。親の環境に `ANTHROPIC_API_KEY`・`ANTHROPIC_CUSTOM_HEADERS` があり、従量の接続の設定が別の `claude` を指す見本で、差し替えた `--version` の実行が受けた環境にその 2 つが無い。`cache.rewake` の `versions` が空・利用者の `--settings` がある・`cache.wake` が届かない起動（登録つきの引数を作らない起動）では、起こす登録の判定による `--version` の実行が 0 回。キーでない従量の接続の設定が別の `claude` を指す見本では、`--version` の実行が `_start` の 1 回だけ。API キーの従量の接続の設定が別の `claude` を指す見本では、版の下限の確かめ（`verify` の `_gate_off`）が `config-gate` の下限を確かめるために、その実行ファイルの版を今のとおり 1 回読み（`_start` と合わせて 2 回）、起こす登録の判定は版を読まないので増えない。版の下限の確かめが版を読めない（差し替えた `--version` が失敗する）見本では、今のとおり `key_gate` でその候補を飛ばす |
| 届かない宣言 | バージョンが一覧に在り、`cache.wake` の `seconds` を 120 にした値で、初回の起動と `--resume` の再開のどちらも `--settings` の登録も `NDF_RELAY_REWAKE` も足さず、`log.jsonl` に `rewake_off` の行があり、今の起動の記録の `rewake` が偽 |
| 利用者の `--settings` | バージョンが一覧に在り、受け継ぐ引数に `--settings=x.json` があるときと `--settings x.json` があるときのどちらも、初回の起動と `--resume` の再開の引数に `--settings` が 1 つだけで、利用者の値がそのまま並び、`rewake_settings` の JSON が無い。今の起動の記録の `rewake` が偽 |
| 従量キーの子 | バージョンが一覧に在り、利用者の `--settings` が無く、起動先が API キーの従量の接続の子のとき、初回の起動と `--resume` の再開のどちらでも、子の起動の引数に `--settings` が 1 つだけで、その JSON が `apiKeyHelper`・`hooks.ConfigChange` の `config-gate` の hook・`hooks.Stop` の `rewake_settings` の登録の 3 つをすべて持つ。起動先の理由が `key_unresolved`・`key_overridden` にならない |
| バージョンの取得 | `require` は変えず、下限より古ければ今のとおり `TooOld`。既定のログインと、`PATH` を上書きしない候補の起動で、差し替えた `--version` の実行が `_start` の 1 回だけ（門は `version_at` が同じ記録を読む）。起動の行の `claude_path` が解いた実パスで、同じ実行ファイルの候補では `version_probed` が偽、キーでない従量の接続が別の `claude` を指す見本では最初の起動で真・同じラッパーの `--resume` の再開で偽。登録つきの引数を作らない起動（一覧が空）では 2 つとも無い |

## 集計（`ndf relay cache report`）

| 関数 | 新設/変更 | シグネチャ | 返り値・副作用 |
| --- | --- | --- | --- |
| `report` | 新設 | `report(rows: Iterable[Mapping[str, Any]], since: Optional[float] = None) -> dict[str, Any]` | `wait_id`（と `session_id`）で待ちにまとめ、`wake` と `wake_result` を `wake_id` で結ぶ。出す値と分母は次の表のとおり。倍率は各行の `w`・`r`・`out` を使う。`since` があれば `wait_start` の `at` がそれ以後の待ちだけを集める。`wait_void` の行を持つ待ちは、どの集計にも入れず `metrics` の `voided_waits` に数える。`metrics` に `partial_waits`（`wait_start` が `since` より前か無い待ち。どの集計にも入れない）・`unmatched_wakes`（`wake_result` の無い `wake`。記録の欠け）・`preempted_wakes`（`outcome` が `preempted` の `wake_result`。割り込みで起床の呼び出しが無かった）・`orphan_results`（`wake` の無い `wake_result`）・`unpriced_waits`（`wake_result` の無い `wake` か、`priced` が偽の `wake_result`（`truncated`・`unknown` と寿命別の項目の欠けた `woke`）を持つ待ち。`preempted` だけの待ちは入れない）を出す |
| `run` | 新設 | `run(args: argparse.Namespace) -> int` | 中継の保存先の全ラッパーの `cache-log.jsonl` を読み、`--since` の日時を `report` に渡し、統一の JSON レポート（`{"tool": "relay", "status", "summary", "items", "metrics"}`）で返す。読めない行は数えて `metrics.skipped` に出す。費用は行の倍率で出すので上限の設定を読まない |

| 値 | 何から出すか |
| --- | --- |
| 待ちの数・上限で止めた待ちの数・突き合わせられずに止めた待ちの数 | `wait_start`・`stop_waking`（`scope` `wait`・`reason` が上限）・`wake_result`（`outcome` `unknown`。この止め方では `stop_waking` を書かない） |
| 起こした回数 | `wake` の行と `wake_result` の行に出る `wake_id` の、重複を除いた数。`wake_result` の無い `wake`（記録の欠け）も、`wake` の無い `wake_result`（`_finish` が終了コード 2 を返したが `wake` の行を書けなかった。下の見本）も 1 回に数え、両方そろった起床も 1 回である |
| `age` の幅（30・45・50・55・60 分）ごとの当たりの率 | 結べた組のうち `outcome` が `woke` で `hit` が null でないものだけ。`age` は `wake`、`hit` は `wake_result`、分母はその組の数 |
| `d` の中央値 | 結べた組のうち `outcome` が `woke` で `priced` が真のものの `cache_write_1h × w_1h + cache_write_5m × w_5m + output × out`（起床に属する全呼び出しの合計。`priced` が偽の起床は入れない） |
| 起床 1 回の実費の中央値 | 結べた組のうち `outcome` が `woke` で `priced` が真のものの `cache_read × r + cache_write_1h × w_1h + cache_write_5m × w_5m + output × out`（全呼び出しの合計。読み出しを含む） |
| 待ちの長さの中央・75・90 パーセンタイル | `wait_id`（待ちが始まった応答の時刻）から `wait_end` の `opened_at`（待ちを終えた入力の時刻）まで。`wait_end` の行の `at`（Stop の時刻）は使わない |
| 避けた書き直しの推定（1 つの待ちで最大 1 回。項目の名前と `summary` に「推定」を付ける） | 待ちの長さ（上の行）が `wait_start` の `ttl` の寿命を越え、その待ちに `hit` が真の `wake_result`（結べた組）が 1 つ以上あり、最後に当たった起床（その組の `wake` の行の `at`）から `wait_end` の `opened_at` までが `wait_end` の `ttl` の寿命以内で、`wait_end` の `hit`（待ちを終えた最初の呼び出しの読み出し）が真の待ちだけ。最後に当たった起床から寿命を越えて当たった待ちは、当たりを起床に帰せないので数えない。反実仮想の推定で、前提は設計の決定 9 にある。`unpriced_waits` の待ちは入れない。`preempted` の `wake_result` を持つだけでは外さない（費用 0）。token は `wait_end` の `tokens`、費用の差は `(w − r) × tokens` − その待ちの全ての `wake_result`（`wake` の無いものも含む。`preempted` は 0）の実費の合計。`wake` の無いまま寿命を越えて自然に当たった待ちは数えない |

起こした回数の見本: 1 つの待ちに、`wake_id` `a` の `wake` と `wake_result`、`wake_id` `b` の `wake` だけ（起きた応答の前に会話が終わった）、`wake_id` `c` の `wake_result` だけ（`wake` の行を書けなかった）がある記録では、起こした回数は 3（`a`・`b`・`c`）、`unmatched_wakes` 1・`orphan_results` 1 である。`wake` の行の数（2）で数えると落ちる。

## 上限の設定の方針のファイル（`contracts/limits/<方針の名前>.json`）

今の上限の設定は、`contracts/limits/` に方針 1 つにつき 1 ファイルで置く。`contracts/limits.py` の `policy(name)` がそのファイルだけを読む（1 つの `limits.json` は無い。#3687）。足す方針は次の 9 ファイルである。どれも今の方針のファイルと同じく `summary` と `read_by`（方針を引くモジュール。規則 `limit-readers` が実際の読み手と突き合わせる）を持つ。今の `hook.transcript-tail.json` は、`read_by` に `ndf.domains.hooks.rewake` を足す（起こす hook が末尾の大きさを読む）。

| ファイル | 形（`summary`・`read_by` を除く） | `read_by` |
| --- | --- | --- |
| `cache.wake.json` | `{"seconds": 600, "interval": 60}` | `ndf.domains.relay.keep_cache`（`ratios` の中で方針の名前を文字列リテラルで書いて `get` を呼ぶ。下の「読み手の突き合わせ」）・`ndf.domains.hooks.budget`（`rewake_reach` が `tables.policy_value` で読む） |
| `cache.write-ratio.json` | `{"ratios": {"1h": 2.0, "5m": 1.25}}` | 同上 |
| `cache.read-ratio.json` | `{"default": 0.1, "ratios": {"claude-opus-5-5": 0.05, "claude-sonnet-5-5": 0.05, "claude-fable-5-1": 0.025, "claude-fable-5-2": 0.025, "claude-mythos-5-1": 0.025, "claude-mythos-5-2": 0.025}}` | 同上 |
| `cache.output-ratio.json` | `{"default": 5}` | 同上 |
| `cache.wake-cost.json` | `{"count": 2000}` | 同上 |
| `cache.hit.json` | `{"ratio": 0.9}` | 同上 |
| `cache.rewake.json` | `{"versions": []}`（実装の PR では空のまま。有効化の PR で、マージした実装で測って通ったバージョンを一覧に書く。新しいバージョンは測り直して通ってから一覧に足す。設計の決定 7 の「一覧に載せる手順」） | `ndf.domains.relay.wrapper` |
| `hook.budget.stop-rewake.json` | 今の `hook.budget.*` の形で `timeout` 3,300 | `ndf.domains.hooks.budget`（`rewake_budget` の動的な読み口。`registration` は `rewake_budget` を呼ぶだけで方針を引かない） |

`limits.schema.json` の方針の形と、`contracts/limits.py` の `Policy` に、次の項目を足す。

| 項目 | `limits.schema.json` の形 | `Policy` の型 |
| --- | --- | --- |
| `ratios` | 文字列から 0 より大きい数への表 | `Optional[dict[str, float]]` |
| `default` | 0 より大きい数 | `Optional[float]` |
| `ratio` | 0 より大きく 1 以下 | `Optional[float]` |
| `versions` | `数.数.数` の文字列の配列。重複なし。空も可 | `Optional[tuple[str, ...]]` |
| `calls` | 1 以上の整数 | `Optional[int]` |
| `start` | 0 以上の整数 | `Optional[int]` |

読み手はエントリポイントごとに次のとおり決める。どのエントリポイントも、方針の名前から `contracts/limits/<方針の名前>.json` を読む今の仕組み（`limits.policy`・`tables.policy`）を通る。ファイルの場所は自分で組み立てない。

| エントリポイント | 取得の API | 返る型 |
| --- | --- | --- |
| 起こす hook（`rewake`・`keep_cache.ratios` へ渡す） | `hooks/tables.policy_value(name, key)`（今のまま。方針の 1 つの値を JSON のまま返す） | `ratios` は `dict[str, float]`、ほかは数。`cache.rewake` の `versions` は読まない（バージョンの一覧を読むのはラッパーの `limits.texts` だけで、`cache.rewake` の `read_by` は `ndf.domains.relay.wrapper` だけ） |
| 集計（`cli_cache`） | 読まない。起こす hook が `tables.policy_value` で読んで行に書いた倍率（`w_1h`・`w_5m`・`w`・`r`・`out`）を使う | — |
| ラッパー（`wrapper._start`） | 新設の `contracts/limits.texts(name, key) -> tuple[str, ...]`（バージョンの一覧を返す API はこの名前だけ。`text` は作らない） | `tuple[str, ...]`（`versions`） |

### 読み手の突き合わせ（規則 `limit-readers`）

`ndf check` の規則 `limit-readers`（`src/ndf/checks/limits_readers.py`）は、2 つを突き合わせる。方針の `read_by` と、方針を引く読み口（`src/ndf/checks/rules.json` の `limits`）の呼び手である。新しい読み口と読み手は、次のとおり `rules.json` と `read_by` に足す。

| 引く箇所 | `rules.json` の `limits` に足す行 | 読み手に数えるモジュール（`read_by`） |
| --- | --- | --- |
| ラッパーのバージョンの一覧（`limits.texts("cache.rewake", "versions")`） | `readers` に `"ndf.contracts.limits.texts": {"arg": "name", "index": 0}`（`value`・`seconds` と同じ形） | `ndf.domains.relay.wrapper`（`cache.rewake`） |
| `keep_cache.ratios` の中の `get("cache.write-ratio", "ratios")` など | `readers` に `"ndf.domains.relay.keep_cache.ratios.get": {"arg": "name", "index": 0}`（引数で受けた読み口。前例は `ndf.domains.relay.context_thresholds.thresholds.value`） | `ndf.domains.relay.keep_cache`（方針の名前を文字列リテラルで書く `ratios` のモジュール。`cache.wake`・`cache.write-ratio`・`cache.read-ratio`・`cache.output-ratio`・`cache.wake-cost`・`cache.hit`）。`rewake` は `tables.policy_value` を関数のまま渡すだけで、方針の名前を書かないので読み手に数えない |
| 末尾の大きさ（`rewake` の `tables.policy_value("hook.transcript-tail", "count")`） | 足さない（`tables.policy_value` は今の読み口） | `ndf.domains.hooks.rewake` を `hook.transcript-tail` に足す |
| バックグラウンドの予算（`budget.rewake_budget` の `tables.policy(f"hook.budget.{spec['async_rewake']['budget']}")`） | `dynamic` に `{"at": "ndf.domains.hooks.budget.rewake_budget", "expr": "f\"hook.budget.{spec['async_rewake']['budget']}\"", "template": "hook.budget.{}", "vocabulary": "hooks/runtimes.json#/runtimes/claude/events/Stop/async_rewake/budget", "reason": "バックグラウンドの登録の予算を、ランタイムのコントラクト表の async_rewake の予算のキーから引く"}`。語彙は `*` を使わずに Stop の行を指す（`async_rewake` を持たない事象で語彙を引けずに検査が止まらないように）。今の `_budget_policy` の行の語彙（`events/*/budget`）は変えない | `ndf.domains.hooks.budget`（`hook.budget.stop-rewake`） |

`keep_cache.ratios` と `rewake_budget` の中で方針の名前を引く式は、上の表の `expr` と同じ綴りで書く（綴りが違えば規則が読み口を解けない）。実装の完了の条件に、方針のファイルを差し替えるテスト（下）に加えて、`ndf check` の規則 `limit-readers` が破れ 0 件で通ること（PR の CI の検査）を入れる。

`limits.value` は、値が数でない（`ratios`・`versions`）ときに `LimitsError` を上げる。テスト（設計のテスト設計の 13）は、`contracts/limits/` の方針のファイル（`cache.write-ratio.json`・`cache.read-ratio.json`・`cache.rewake.json`）を差し替える。そのうえでエントリポイントから、次の 4 つが差し替えた値どおりになることを確かめる。

- hook の `ratios` の結果（辞書の `ratios` と `default`）
- hook が書いた行の倍率
- その行から集計が出す費用
- ラッパーのバージョンの門（`limits.texts` が返す文字列の配列の `versions`）
