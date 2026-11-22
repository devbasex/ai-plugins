# #4049 の詳細設計: 背景の作業の記録とラッパーの待ち

設計文書 [issue-4049-design.md](./issue-4049-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。#3995 の実装の後に、その上で実装する（運用上限は `context_thresholds.thresholds` が返す値で、`SectionState.context_limit` に入る）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/
│   ├── hooks/
│   │   ├── dispatch.py・main.py
│   │   └── guards/
│   │       ├── relay_busy.py *
│   │       └── relay_mark.py
│   └── relay/
│       ├── signals.py
│       ├── switch_decide.py
│       └── wrapper.py
├── contracts/
│   ├── hooks/guards.json・runtimes.json
│   └── domains/ndf-files.json・env.json
├── docs/contracts/hooks.md・files.md・env.md（再生成）
├── skills/development-workflow/references/relay.md
└── tests/domains/
    ├── hooks/test_guards.py
    └── relay/test_relay_parts.py・test_relay_decide.py・test_relay_wrapper.py
```

依存の向き: guard `relay_busy` と `relay_mark`（M3a の hook）は `relay.signals`（M3b）と hook の中の部品（`hooks.tables`・`hooks.verdict`）だけを読み、`wrapper` は `signals` と `switch_decide` を読む。`switch_decide` は時計と端末とファイルを読まない純粋な関数のまま、`signals` を import しない。`signals` は標準ライブラリだけを使う。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/hooks/guards/relay_busy.py` | 新設 | 中継の下の子の会話の SessionStart で 0 件を書き、背景の作業の開始を記録し、記録できない開始を止める | `relay.signals`・`hooks.verdict` | モジュール `relay_busy` | `check`・`on_deadline`・`_target`・`_starts_background`（新設） |
| `src/ndf/domains/hooks/dispatch.py` | 変更 | 確定した拒否の理由を後の guard と締め切りの経路へ渡す | `hooks.verdict` | `GuardServices`・モジュール `dispatch` | `run`（変更）、`GuardServices.denied`（項目を足す） |
| `src/ndf/domains/hooks/main.py` | 変更 | 締め切りの出力に、dispatch が確定した拒否の理由を残す | `hooks.dispatch` | `_Run`・モジュール `main` | `_entry`・`_deadline_out`（変更）、`_Run.denied`（項目を足す） |
| `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | Stop で子の会話の背景の作業の件数を書いてから（`relay-busy` を止めた環境では書かない）、今のマーカーの扱いへ進む | `relay.signals`・`hooks.tables` | モジュール `relay_mark` | `check`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 背景の作業の記録の読み書きと、子の会話の ID の環境変数を足す | 標準ライブラリ | モジュール `signals` | `write_busy`・`record_start`・`read_busy`・`drop_other_busy`・`relay_session`（新設）、`launch_env`（変更）、定数 `SESSION_ENV`・`BUSY_PREFIX`・`LAUNCH_ID_RE`・`STOP_LOCK` |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | 背景の作業の件数で引継ぎと止まりの判定を待つ | `account_decide` | `SectionState`・`Background`・モジュール `switch_decide` | `needs_background`・`_over`・`_stalled`（新設）、`_alive`（変更）、`SectionState.background`（項目を足す）、定数 `BACKGROUND_WAIT`・`BACKGROUND_LEAST`・`BACKGROUND_UNKNOWN`・`ACTIVE` |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 起動の前にほかの起動の記録を消し、要るときだけ記録を読み、子を止める直前に排他の中で読み直し、待ちと動きを状態に書く | `signals`・`switch_decide` | `Wrapper`・モジュール `wrapper` | `_on_active`・`_busy_zero`・`_busy_not_running`（新設）、`_start`・`_state`・`_stop_if_clear`・`_on_handover`・`_on_resume`（変更）、`_ACTIONS`（行を足す） |
| `contracts/hooks/guards.json` | 変更 | guard `relay-busy` の行を足し、`relay-marker` の `summary` を直す | — | — | — |
| `contracts/hooks/runtimes.json` | 変更 | Codex の `skipped` に `relay-busy` を足す | — | — | — |
| `contracts/domains/ndf-files.json` | 変更 | `relay-busy`（`<名前>/busy-<起動>.json`）の行を足す | — | — | — |
| `contracts/domains/env.json` | 変更 | `NDF_RELAY_SESSION` の行を足す | — | — | — |
| `docs/contracts/hooks.md`・`files.md`・`env.md` | 変更（再生成） | `ndf check --write-spec` で作り直す | — | — | — |
| `skills/development-workflow/references/relay.md` | 変更 | 判定の表の順 5・6、hook の一覧（`relay-busy`・`relay-marker`）、記録の行の種類の表（`wait_background`・`active`）を直す | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更（`glossary.md` は再生成） | 設計の「用語」の 3 語を反映する | — | — | — |
| `tests/domains/hooks/test_guards.py`・`tests/domains/hooks/test_dispatch.py`・`tests/domains/relay/test_relay_parts.py`・`test_relay_decide.py`・`test_relay_wrapper.py` | 変更 | 下の単体テストの観点 | — | — | — |

## 背景の作業の記録（`signals`）

満たす決定: 決定 2・決定 3、受け入れ条件 4・5・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_busy` | `src/ndf/domains/relay/signals.py` | 新設 | `write_busy(directory: Path, launch: str, session_id: str, count: int, source: str) -> bool` | `busy-<launch>.json` を `{"launch", "session_id", "count", "source", "at"}` で置き換え、書けたら真 |
| `record_start` | 同上 | 新設 | `record_start(directory: Path, launch: str, session_id: str, wait: float) -> bool` | 子を止める排他 `STOP_LOCK` を `wait` 秒まで待って取り、その中で今の起動が `launch` のときだけ `write_busy(…, 1, "start")` を打ち、書けたら真 |
| `read_busy` | 同上 | 新設 | `read_busy(directory: Path, launch: str, session_id: str) -> Optional[tuple[int, str, str]]` | `busy-<launch>.json` が今の起動・今の会話の記録で形が合えば `(count, source, at)`、ほかは None（不明） |
| `drop_other_busy` | 同上 | 新設 | `drop_other_busy(directory: Path, launch: str) -> None` | `busy-*.json` のうち `launch` のもの以外を消す |
| `relay_session` | 同上 | 新設 | `relay_session(env: Mapping[str, str]) -> Optional[str]` | 環境の `NDF_RELAY_SESSION`。無い・空なら None |
| `launch_env` | 同上 | 変更 | `launch_env(directory: Path, launch: str, session_id: str) -> dict[str, str]` | 今の 2 つに `NDF_RELAY_SESSION` を足して返す |

- `write_busy`
  - 事前条件: `source` は `session`・`start`・`stop` のどれか。`count` は 0 以上の整数
  - 事後条件: 真を返したとき、`busy-<launch>.json` は引数の値と書いた時刻 `at`（`now_iso`）を持つ。偽を返したとき、ファイルは呼ぶ前のまま
  - 副作用: `launch` が `LAUNCH_ID_RE` に合わなければファイルの名前を作らずに偽。合えば `replace_json` で一時の名前に書いて名前を変える。`OSError` は偽にして外へ出さない。排他は取らない（開始の記録は `record_start` が排他を取って呼ぶ）
- `record_start`
  - 事後条件: 真を返したとき、子を止める排他の中で今の起動（`launch.json`）が `launch` で、`busy-<launch>.json` は件数 1・`start`。排他を `wait` 秒で取れない・今の起動が `launch` でない（ラッパーが子を止めて起動を外した後）・`write_busy` が偽なら偽で、ファイルは呼ぶ前のまま
  - 副作用: `STOP_LOCK` を `held` で取る（`drop_question` と同じ取り方）。`OSError` は偽にして外へ出さない
- `read_busy`
  - 副作用: 無し（読むだけ）。`launch` が `LAUNCH_ID_RE` に合わない・ファイルが無い・`OSError`・`UnicodeDecodeError`・JSON が壊れた・JSON の `launch` が引数の `launch` と違うか無い・`session_id` が違う・`count` が `bool` か負か整数でない・`source` が 3 つのどれでもない・`at` が文字列でないか `datetime.fromisoformat` で読めないかタイムゾーンを持たない（`now_iso` の形でない）ときは None。例外を外へ出さない（見回りの `SignalError` の止まり（`_halt_broken`）へ流さない）
- `drop_other_busy`
  - 副作用: 中継の保存先の `busy-*.json` を `glob` で拾い、名前が `busy-<launch>.json` でないものを消す。消せないもの（`OSError`）は残して続ける

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_busy` | 起動の ID の形が違う | 手で渡した環境変数・空の値 | `LAUNCH_ID_RE` の照らし | 書かずに偽を返す | 無し（呼び手が扱う） | 無し |
| `write_busy` | 書けない | ディスク・権限（`OSError`） | `replace_json` | 書かずに偽を返す | 無し（呼び手が扱う） | 次の書き込みで書き直す |
| `record_start` | 排他を取れない・今の起動でない | ラッパーが子を止める最中か止めた後 | `held`・`current_launch` | 書かずに偽を返す | 無し（呼び手の `relay-busy` が拒否する） | 子が生きていればモデルが呼び直せる |
| `read_busy` | 記録が不明 | 上の副作用の箇条 | 読む場所 | None を返す | 無し（ラッパーの状態の理由に出る） | 次の書き込みで直る |
| `drop_other_busy` | 消せない | 権限 | `unlink` | 残して続ける | 無し | 次の起動で消し直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SESSION_ENV` | 定数 | `src/ndf/domains/relay/signals.py` | `NDF_RELAY_SESSION` |
| `BUSY_PREFIX` | 定数 | 同上 | `busy-`（ファイルは `busy-<起動の ID>.json`） |
| `LAUNCH_ID_RE` | 定数 | 同上 | `^[0-9a-f]{32}$`（ラッパーが作る `uuid.uuid4().hex` の形） |
| `STOP_LOCK` | 定数 | 同上 | `question.json.lock`（今の `wrapper.QUESTION_LOCK` を `signals` へ移し、`wrapper` はこれを使う。子を止める排他で、質問の転送と背景の作業の開始の記録が共有する） |
| `Background` | 型（`NamedTuple`） | `src/ndf/domains/relay/switch_decide.py` | `count: int`・`source: str`・`at: str`。`switch_decide` が型を持ち、`signals` は `switch_decide` を import しないので、`read_busy` は 3 つ組を返し、`wrapper._state` が `Background` に詰める |

`signals.py` の冒頭のファイルの表に `busy-<起動>.json` の行（書き手: 子の会話の hook `relay-busy`（SessionStart・PreToolUse）・`relay-marker`（Stop）。ラッパーは書かず、ほかの起動のものを消すだけ。中身: その起動の背景の作業の件数）を足す。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| ラッパーが 0 件を読んでから子を止めるまでと、子の開始の記録（端末の入力・背景の作業の知らせ・予定の起床で子が動いた応答） | `busy-<起動>.json`・`launch.json` | 子を止める排他 `STOP_LOCK` で直列にする。`record_start` は排他の中で今の起動を照らして書き、ラッパーの `_stop_if_clear` は排他の中で記録を読み直し、0 件（止まりの起動し直しは 0 件か不明）でなければ子に何もしない。ラッパーが先に排他を取れば、子を止めて起動を外した後に `record_start` が今の起動の違いで偽になり、開始は拒否される（止めた子の開始は始まらない）。`record_start` が先なら、ラッパーは読み直しで 1 件以上を見て止めない | 排他を取れなかったラッパーは何もせず次の見回りで決め直す |
| 同じ応答の並ぶ PreToolUse（Agent を 2 つ同時に起動） | `busy-<起動>.json` | `STOP_LOCK` を順に取る。どちらも件数 1・`start` を置き換えで書く | 同じ値を何度書いても同じ |
| PreToolUse と Stop | 同上 | Stop は排他を取らない。Claude Code は応答の Tool の呼び出しを終えてから Stop を発火し、Stop の hook を終えてから次の応答へ進む | Stop が後に書いて正しい件数に置き換わる |
| 止めた子の遅れた hook と新しい起動 | 起動ごとのファイル | 取らない。遅れた hook は旧い起動のファイルにしか書けない | 旧い起動のファイルは次の起動の `drop_other_busy` が消す |
| 入れ子の claude の hook と子の hook | 同上 | 取らない。入れ子の claude は `session_id` が違うので書かない | — |
| ラッパーの見回りの読み取りと子の書き込み | 同上 | 取らない。名前の変更で書くので、読み手は前か後の中身のどちらかを読む。子を止める判断は上の行の読み直しで確かめる | 読み直しは次の見回り |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `write_busy` | `signals.write_json`（排他つき）・`replace_json` | `grep -n "def write_json\|def replace_json" src/ndf/domains/relay/signals.py` | `replace_json` を使う。`write_json` はファイルごとの排他（`<path>.lock`）を取るが、要るのは子を止める排他との直列なので `record_start` が `STOP_LOCK` を取る |
| `record_start` | `signals.drop_question`（`question.json.lock` を取り、中で `current_launch` を照らす） | `grep -n "def drop_question\|current_launch" src/ndf/domains/relay/signals.py` | 同じ取り方と照らし方で書く。排他は `held`、起動の照らしは `current_launch` を使う |
| `read_busy` | `signals._read`・`read_next` | `grep -n "def _read\|def read_next" src/ndf/domains/relay/signals.py` | `_read` を使い、`OSError`・`UnicodeDecodeError`・`SignalError` を None にする。`read_next` はマーカーの形に固有 |
| `drop_other_busy` | `signals.drop` | `grep -n "def drop" src/ndf/domains/relay/signals.py` | 1 つずつの削除に `drop` を使う |
| `relay_session` | `signals.relay_launch` | 同上 | 同じ形で足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write_busy`・`read_busy` | 書いた件数 2・`stop` を同じ起動と会話で読み返せる。0・`session` も読み返せる | `count` が 0 と 1 | `replace_json` を失敗させると偽で前の中身が残る。起動の ID が `../x`・空・大文字なら偽でファイルを作らない。別の会話の記録・壊れた JSON・`count` が文字列か負か `true`・`source` が別の値・ファイルが無い・権限で読めない・不正な UTF-8 のときは `read_busy` が None で例外を上げない。`busy-<A>.json` の名前で中身の `launch` が B か `launch` が無い 0 件、`at` が無い・数値・`"yesterday"`・タイムゾーンの無い `2026-11-22T03:19:34` の 0 件も None |
| `record_start` | 今の起動が A のとき A を渡すと件数 1・`start` を書いて真 | 別のスレッドが `STOP_LOCK` を持つ間は `wait` の後に偽で書かない。持ち手が放した後の呼び出しは真 | 今の起動が None（ラッパーが外した）か B なら偽で書かない。`write_busy` を失敗させると偽 |
| `drop_other_busy` | 起動 A・B のファイルがあるとき B を渡すと A だけが消える | ファイルが無くても例外を上げない | 消せないファイルがあっても残りを消す |
| `launch_env`・`relay_session` | `launch_env` の返りに 3 つの変数があり、`relay_session` が会話の ID を返す | 空の `NDF_RELAY_SESSION` は None | — |

## guard `relay-busy`

満たす決定: 決定 2・決定 3、受け入れ条件 3・4・5・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/relay_busy.py` | 新設 | `check(ctx, svc) -> Verdict \| None` | SessionStart で子の会話の 0 件を書き、PreToolUse で子の会話の背景の作業の開始を記録し、記録できなければその呼び出しを拒否する |
| `on_deadline` | 同上 | 新設 | `on_deadline(env: Mapping[str, str], payload: dict) -> Optional[str]` | 締め切りを過ぎたとき、記録し終えていない子の会話の背景の作業の開始なら拒否の理由を返す |
| `_target` | 同上 | 新設 | `_target(env: Mapping[str, str], session_id: Any) -> bool` | 中継の対象の子の会話か。`env` の `NDF_RELAY_DIR` が空でない文字列で、`relay_session(env)` が None でなく、`session_id` がそれと等しい。保存先がディレクトリとして見えるかは見ない |
| `_starts_background` | 同上 | 新設 | `_starts_background(tool: Any, tool_input: Any, tools: Sequence[str]) -> bool` | Tool の名前が `tools`（表の `params.background_tools`）にあるか、Bash で `tool_input` が辞書かつ `tool_input.get("run_in_background") is True` か。型が違う入力は偽 |

- `check`
  - 事前条件: 表の行の `events` は `["SessionStart", "PreToolUse"]`、`tools` は `[]`（SessionStart は Tool の名前を持たず、`GuardSpec.applies` は `tools` が空のときだけ当てるので、Tool の絞り込みは `_starts_background` が行う）
  - 事後条件（先に行う判定）: 次のどれかに当たれば、ファイルに触れずに None。(a) SessionStart で `source` が `startup`・`resume` でない（`compact`・`clear` は背景の作業が残りうるので 0 件を書かない）。(b) PreToolUse で `_starts_background` が偽。(c) `_target(os.environ, ctx.session_id)` が偽（中継の外・旧いラッパーの下で `NDF_RELAY_SESSION` が無い・入れ子の claude）。これらは環境変数と入力の値の比べだけで、例外を上げる経路（ファイル・JSON・正規表現の照らし）に入らない。`tools` が空の `fail_closed` の行なので、ここで例外が出ると中継の内外を問わずすべての PreToolUse が `GUARD_FAILED` で拒否される。この並びはそれを避けるための約束である
  - 事後条件（先行の拒否）: PreToolUse で先に行う判定に当たらず、`svc.denied`（下の「先行の拒否の受け渡し」）が空でなければ、ファイルに触れず `record_start` も呼ばずに、この実行の payload を `_recorded` に残して None。この呼び出しは先の guard の拒否で始まらないので、「1 件以上」を書くと次の Stop まで実在しない作業を待たせる。記録は前の中身のまま残る。`_recorded` に残すのは、締め切りの経路で `BUSY_START_FAILED` を重ねないためで、拒否は `_deadline_out` が先行の理由で保つ
  - 事後条件（中継の対象の子の会話）: `directory = relay_dir(os.environ)`・`launch = relay_launch(os.environ)` を求める。SessionStart なら、どちらかが None のときは書かずに None（記録が無いので、ラッパーは不明として読む）、ほかは `write_busy(directory, launch, session, 0, "session")` を書いて結果を問わず None。PreToolUse なら、どちらかが None（子の保存先が消えた・起動の ID が無い）か `record_start(directory, launch, session, wait)` が偽のときは `deny(BUSY_START_FAILED)`、真ならこの実行の payload をモジュールの `_recorded` に残して None。`wait` は `hook.counter-lock` の秒（`relay_mark` と同じ取り方）
  - 副作用: `busy-<起動>.json` を書く。PreToolUse では `STOP_LOCK` を取る
- `on_deadline`
  - 事後条件: 事象が PreToolUse で、`check` の先に行う判定に当たらず（子の会話の背景の作業の開始）、`_recorded is not payload`（この実行の `check` が書き終えておらず、先行の拒否で書かずに返してもいない）ときだけ `BUSY_START_FAILED` を返す。ほかは None（通す。SessionStart の締め切りは書かずに通し、ラッパーは不明として読む）。`ask.on_deadline` と同じ型。先に行う判定と同じく例外を上げる経路に入らない
  - 副作用: 無し

### 処理の順序（#3995 の `context-limit` との関係）

`order` は 65 で、PreToolUse の `strict` でない行のうち最後に呼ばれる（`context-limit` 20・`heredoc` 25〜`read-repeat` 50・`ask-forward` 60 の後）。#3995 の詳細設計が縛る「PreToolUse の Bash・Read で `dispatch.select` が返す並びの先頭は `context-limit`」は保たれ、`relay-busy` の排他の待ちと書き込みがほかの guard の予算（`budget.usable`）を使うことは無い。`dispatch.run` は予算を使い切った後も `fail_closed` の行を飛ばさないので、前の guard が予算を使い切っても `relay-busy` は呼ばれ、記録を書けなければ拒否する。プロセス全体の締め切り（`SIGALRM`）を過ぎたときは、`_deadline_out` が `on_deadline` を呼び、記録し終えていない開始を拒否する。

`order` で決まるのは呼ぶ順と予算の選別までで、先の guard の拒否を `relay-busy` と締め切りの経路へ渡すのは次の「先行の拒否の受け渡し」である。

### 先行の拒否の受け渡し（`dispatch.run`・`main._deadline_out`）

`dispatch.run` は判定を最後に `combine` で合成し、`_deadline_out` は `fail_closed` の行の `on_deadline` だけを集める。このままでは (1) `context-limit` が拒否した背景の Bash でも `relay-busy` が「1 件以上」を書き、(2) `relay-busy` が書き終えた後から出力が決まるまでに `SIGALRM` が入ると、先の拒否が捨てられ `relay-busy` の `on_deadline` も None を返して、拒否した呼び出しが通る。これを次の契約で塞ぐ。

| 部品 | 保存先 | 種類 | 変更 |
| --- | --- | --- | --- |
| `GuardServices.denied` | `src/ndf/domains/hooks/dispatch.py` | 項目を足す | `tuple[str, ...]`（既定 `()`）。この実行でその guard より前に確定した拒否の理由の並び |
| `dispatch.run` | 同上 | 変更 | 引数 `held: Optional[list[str]] = None` を足す。各 guard の判定を確定した直後（`fail_closed` でない行の `deny` を `advise` へ下げるかを決めた後）、`got.kind == "deny"` なら `got.text` を内部の並びと `held` に足してから次の guard を呼ぶ。次の guard の `GuardServices` には、それまでの並びを `denied` に渡す。`advise` へ下げた拒否は確定でないので足さない。`combine` は deny を最も強い種類として選ぶので、ここで足した理由は最後の判定でも拒否になる |
| `_Run.denied` | `src/ndf/domains/hooks/main.py` | 項目を足す | `list[str]`（既定は空）。`_entry` は `dispatch.run(..., held=run.denied)` と同じ並びを渡す |
| `_deadline_out` | 同上 | 変更 | 理由の並びを `list(run.denied)` から始め、その後に今のとおり `fail_closed` の行の `on_deadline` の理由を足す。`run.denied` が空でなければ、`on_deadline` が全部 None でも拒否を出す |

- 事後条件: `held` へ足すのは `relay-busy` を呼ぶ前なので、`relay-busy` が先行の拒否を見て書かずに返した後のどの時点で `SIGALRM` が入っても、`_deadline_out` は先の理由で拒否する。`relay-busy` を呼ぶ前（先の guard の判定の中か、確定から `held` へ足すまで）に入ったときは、`relay-busy` の `on_deadline` が記録の無い背景の作業の開始を `BUSY_START_FAILED` で拒否する
- 範囲: 背景の作業の開始でない呼び出しで、先の `fail_closed` でない guard の拒否が `held` に入る前に締め切りが来たときは、今のとおり通す（`fail_closed` でない guard の締め切りは通す側へ倒れる、設計の前提）
- 副作用: 無し（並びは 1 回の hook の実行の中だけに持つ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 開始を書けない（`record_start` が偽） | ディスク・権限・起動の ID の形・排他を取れない・ラッパーが子を止めて起動を外した | `record_start` の返り | 止める（`deny`。`deny_key` は `none` なので数えず、案内へ下げない） | PreToolUse の拒否の理由 `BUSY_START_FAILED`（hook の終了コードは 0） | モデルが呼び直せる |
| `check` | 子の保存先が使えない（`relay_dir` か `relay_launch` が None） | 中継の対象の子の会話で、保存先が消えた・ディレクトリでない・起動の ID が無い | `relay_dir`・`relay_launch` の返り | PreToolUse の背景の作業の開始だけを止める。背景でない呼び出しと中継の対象でない会話は先の判定で通っている | 拒否の理由 `BUSY_START_FAILED` | 保存先が戻れば呼び直せる |
| `check` | SessionStart で 0 件を書けない | ディスク・権限・保存先が使えない | `write_busy` の返り | 書かずに通す | 無し | ラッパーは不明として読み、最初の Stop が件数を書く |
| `check` | 例外 | 書き込みの経路で想定しない失敗（先に行う判定は例外を上げない） | `dispatch.run`（`fail_closed`） | 止める（`GUARD_FAILED` の文）。この行の `tools` は空なので、その呼び出しの Tool を問わず拒否する | 拒否の理由（SessionStart は拒否の出力の形を持たないので空） | 同上 |
| `on_deadline` | 締め切りを過ぎた | hook の予算の使い切り・割り込み・排他の待ち | `main._deadline_out` | 記録し終えていなければ止め、書き終えていれば通す | 拒否の理由 `BUSY_START_FAILED` | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `BUSY_START_FAILED` | 定数 | `src/ndf/domains/hooks/guards/relay_busy.py` | `背景の作業の開始を中継へ記録できなかったので、始めない。もう一度呼ぶ（中継のラッパーは記録の無い背景の作業を守れない）` |
| `_recorded` | モジュールの変数 | 同上 | None（記録し終えた実行の payload。`ask._recorded` と同じ扱い） |

表の行（`contracts/hooks/guards.json`）:

| 項目 | 値 |
| --- | --- |
| `name` | `relay-busy` |
| `summary` | 中継の下の子の会話で、SessionStart（`startup`・`resume`）に起動ごとの記録へ 0 件を、背景の作業を始める呼び出し（Agent・Monitor・`run_in_background` の Bash）の前に子を止める排他の中で「1 件以上」を書く。書けない開始は締め切りの経路でも止める（ラッパーはこの guard が書いた記録で 0 件と確かめた子だけを引継ぎで止める。#4049） |
| `module` | `ndf.domains.hooks.guards.relay_busy` |
| `events` | `["SessionStart", "PreToolUse"]` |
| `tools` | `[]`（Tool の絞り込みは `check` の先の判定で行う） |
| `order` | 65（PreToolUse の通常の行の後。上の「処理の順序」） |
| `strict` | 偽 |
| `suggests` | `[]`（`GuardSpec.from_row` が `row["suggests"]` を読むので省かない） |
| `fail_closed` | 真 |
| `deny_key` | `none` |
| `limits` | `["hook.counter-lock"]` |
| `params` | `{"background_tools": ["Agent", "Monitor"]}` |

`contracts/hooks/runtimes.json` の Codex の `skipped` に `"relay-busy": "中継のラッパーは Claude Code だけを包む"` を足す。`contracts/domains/ndf-files.json` に `{"id": "relay-busy", "location": "relay-wrappers", "path": "<名前>/busy-<起動>.json", "kind": "exchange", "tracked": false, "writer": "子の会話の SessionStart・PreToolUse・Stop の hook（M3a）。中継のラッパーはほかの起動のものを消す", "ends": "run"}` を足す。`contracts/domains/env.json` に `{"id": "NDF_RELAY_SESSION", "area": "relay", "set_by": "ndf", "type": "string", "secret": false, "meaning": "中継のラッパーが子へ渡す子の会話の ID。hook は入力の session_id がこれと同じ会話を中継の対象として背景の作業の記録を書き、書けない開始を止める（子のシェルから起動した claude を外す）"}` を足す。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `check`・`on_deadline` | `ask.check`・`ask.on_deadline`（`fail_closed` で記録し終えたかを `_recorded` で見る型） | `grep -n "_recorded\|def on_deadline" src/ndf/domains/hooks/guards/ask.py` | 同じ型で書く。質問の転送は持たないので関数は共有しない |
| `_target` | `signals.relay_dir`（保存先がディレクトリでなければ None） | `grep -n "def relay_dir" src/ndf/domains/relay/signals.py` | 対象の判定には使わない（保存先が消えた子を中継の外と取り違える）。対象と決めた後の保存先の取得に使う |
| `_starts_background` | 背景の作業の開始を見分ける既存の関数 | `grep -rn "run_in_background" src/ndf/domains/hooks` | 無い。新設する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 中継の下の子の会話で Agent・Monitor・`run_in_background: true` の Bash の PreToolUse が「1 件以上」を書いて None。`agent_id` のある Agent（サブエージェントの中）でも書く。SessionStart の `startup`・`resume` で 0 件・`session` を書く | `run_in_background` が偽か無い Bash は触れずに None。`run_in_background` が文字列 `"true"` の Bash・`tool_input` が辞書でない Bash も触れない。SessionStart の `compact`・`clear` は書かない | 中継の外・`NDF_RELAY_SESSION` が無い・入力の `session_id` が違う（入れ子の claude）ときは触れずに None。`NDF_RELAY_DIR` を消えたディレクトリにした子の会話では、背景の作業の開始は `deny`、背景でない Bash・Read は None。書き込みを失敗させると `deny`。ラッパーが `STOP_LOCK` を持つ間と、起動を外した後は `deny`。背景でない・中継の外・入れ子の claude の入力では、`record_start`・`relay_dir` を例外を上げる差し替えにしても呼ばれず None（先の判定が例外の経路に入らない）。書き込みの経路の `record_start` を例外にした背景の Agent は、入口から流すと `GUARD_FAILED` で拒否。続けて 3 回失敗させても 3 回とも `deny` |
| 表の順序 | 実際の `guards.json` で PreToolUse の Bash・Read の入力に `select` が返す並びの先頭が `context-limit` のまま、`relay-busy` は最後 | 差し替えた時計で `context-limit` の判定の中に `budget.usable` を超えて進めても、500,000 の背景の Bash は `context-limit` の `deny` が残り、`relay-busy` は呼ばれても `record_start` を呼ばず（例外を上げる差し替えで確かめる）、前から置いた 0 件・`stop` の記録がそのまま残る。`context-limit` の拒否を `advise` へ下げた（`counter.allow_deny` が偽）ときは確定の拒否でないので記録を書く。書き込みを失敗させた背景の Agent は同じ時計でも `relay-busy` の `deny` | — |
| 先行の拒否の受け渡し | `dispatch.run` が拒否を確定した guard の後の guard へ `svc.denied` でその理由を渡し、`held` にも同じ理由が入る | `advise` へ下げた拒否は `denied`・`held` に入らない | 入口から 500,000 の背景の Bash を流し、`dispatch.render` を `main._Deadline` を上げる差し替えにして `relay-busy` の判定の後へ割り込ませると、出力は `context-limit` の理由の拒否で、記録は前の中身のまま。同じ割り込みで先行の拒否の無い背景の Agent は、記録を書き終えているので空の出力 |
| `on_deadline` | 記録し終えていない子の会話の Agent の payload で `BUSY_START_FAILED` | 書き終えた同じ payload と、先行の拒否で書かずに返した同じ payload では None。SessionStart の payload では None | 背景でない Bash・Read・中継の外・入れ子の claude では None。予算を使い切った後と締め切りの後の両方で、入口から流した Agent は拒否、Read と背景でない Bash は空の出力 |

## 中継のマーカーの guard（`relay_mark`）

満たす決定: 決定 1・決定 2・決定 3、受け入れ条件 1・6・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | `check(ctx, svc) -> Verdict \| None`（変えない） | 中継の下で、最後の応答とブロックの有無を見る前に、子の会話の Stop の件数を書く。その後は今のまま |

- `check`
  - 変える前: 中継の外か最後の応答が空なら None。ブロックが無ければ None。背景の作業が残れば変更要求、無ければ次のセグメントのマーカーを書く
  - 変えた後: 最初に、中継の下で `relay_launch`・`relay_session` があり、入力の `session_id` が `relay_session` と同じで、`ctx.payload.get("background_tasks")` がリストで、`relay-busy` を止めていない（`tables.is_off("relay-busy", tables.off_names(os.environ.get("NDF_HOOK_OFF")))` が偽）なら `write_busy(directory, launch, session, len(<リスト>), "stop")` を書く（`ctx.background_tasks` は使わない。項目が無い・リストでないときは書かない）。`relay-busy` を止めた環境では開始が記録されないので、Stop の 0 件を書くとラッパーが記録の無い背景の作業を抱えた子を止めうる。書かずに記録を無いまま（不明）にする。書けたかを計測の `notes` の `relay_busy` に残す。その後は今のまま（`fail_closed` は偽のまま）
  - 副作用: `busy-<起動>.json` を書く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 件数を書けない | ディスク・権限 | `write_busy` の返り | 書かずに前の記録を残し、今のマーカーの扱いへ進む | 無し | 次の Stop で書き直す |
| `check` | 列が無い・リストでない | タスクの登録簿に届かない | 入力の `background_tasks` の型 | 書かずに前の記録を残す | 無し | 同上 |

表の行の `summary` を「中継の下で、子の会話の Stop の背景の作業の件数を起動ごとの記録へ書く（列が無い・`relay-busy` を止めた環境では書かない）。最後の応答が次のセグメントのブロックを持てば、背景の作業が残る間は変更要求、無ければ中継のマーカーを書く（#1877・#2438・#4049）」に直す。`events`・`fail_closed` は変えない。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 中継の下の子の会話の Stop で `background_tasks` が 2 件なら 2、空なら 0 を書く。ブロックの無い応答・最後の応答が空でも書く | 項目が無い・`null`・文字列の `background_tasks` では書かず、前の 1 件以上が残る | 入れ子の claude（`session_id` が違う）・中継の外・`NDF_HOOK_OFF` が `relay-busy` か `all` の環境では書かない。書き込みを失敗させても変更要求を返さず、ブロックがあれば今のとおりマーカーを扱う |

## ラッパーの判定（`switch_decide`）

満たす決定: 決定 4・決定 5、受け入れ条件 1・2・5・6・9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `SectionState.background` | `src/ndf/domains/relay/switch_decide.py` | 変更（項目を足す） | `background: Optional[Background] = None` | 今の起動の背景の作業の記録。None は不明か読んでいない |
| `needs_background` | 同上 | 新設 | `needs_background(state: SectionState) -> bool` | 見回りが背景の作業の記録を読むか。`_alive` と同じ 2 つの条件（`_over(state) and quiet and not handover_asked`、または `_stalled(state)`）のどちらかが真か |
| `_over` | 同上 | 新設 | `_over(state: SectionState) -> bool` | 文脈の量が運用上限以上か（今の `_alive` の `over` を出したもの） |
| `_stalled` | 同上 | 新設 | `_stalled(state: SectionState) -> bool` | `stall_seconds` が正で `idle_seconds ≥ stall_seconds` か（今の `_alive` の止まりの式を出したもの） |
| `_alive` | 同上 | 変更 | `_alive(state: SectionState, quiet: bool) -> Step`（変えない） | 下の判定表で手を決める |

`_over` と `_stalled` は `_alive` と `needs_background` が共有する。`quiet` は `decide` が今のとおり `idle_seconds ≥ quiet_seconds` で求め、`needs_background` も同じ式を使う。

### 処理の手順

`_alive` の判定表（上から最初に当たった行。「待つ条件」は「運用上限以上・静か・未要求」、「止まり」は `_stalled`、件数は `background.count`）:

| 待つ条件 | 止まり | `background` | 結果 |
| --- | --- | --- | --- |
| 真 | — | 件数 0 | `handover`（今の理由と `HANDOVER_PROMPT`） |
| — | — | 件数 1 以上で、待つ条件か止まりが真 | `wait_background`（`BACKGROUND_WAIT`。`source` が `start` なら `BACKGROUND_LEAST`） |
| — | 真 | 件数 0・None | `_restart_or_stop`（今のとおり） |
| 真 | 偽 | None | `wait_background`（`BACKGROUND_UNKNOWN`） |
| 偽 | 偽 | — | `active`（`ACTIVE`） |

`decide` の上の段（質問・利用上限と認証・次のセグメントのマーカー・子の終わり）は変えず、`_alive` の前に今のとおり判定する。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `BACKGROUND_WAIT` | 定数 | `src/ndf/domains/relay/switch_decide.py` | `背景の作業 {count} 件の終わりを待つ（記録 {at}）。子を止めず、引継ぎも起動し直しもしない` |
| `BACKGROUND_LEAST` | 定数 | 同上 | `背景の作業 1 件以上の終わりを待つ（開始の記録 {at}）。子を止めず、引継ぎも起動し直しもしない` |
| `BACKGROUND_UNKNOWN` | 定数 | 同上 | `背景の作業を確かめられないので引継ぎを待つ（止まりは子の出力と端末の入力で見る）` |
| `ACTIVE` | 定数 | 同上 | `子が動いている`（今の `_alive` の文） |

`Step` の `kind` の説明に `wait_background`・`active` を足す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `decide` | 運用上限で静かで件数 0 なら `handover`、件数 1・`stop` なら `wait_background`（理由に 1 件と時刻）、件数 1・`start` なら理由が「1 件以上」。止まりの秒で件数 2 なら `wait_background`、0 なら `restart`、今日の上限なら `stop`。待つ条件と止まりが同時に真のとき、0 は `handover`・None は `restart`・1 以上は `wait_background` | 運用上限 − 1 で静かなら件数に依らず `handover` を返さない。引継ぎを求め済みなら件数 0 でも返さない | `background=None` で運用上限・静かなら `wait_background`（`BACKGROUND_UNKNOWN`）、止まりの秒なら `restart`。何も当たらなければ `active` |
| `needs_background` | 運用上限で静かで未要求なら真、止まりの秒なら真 | 運用上限 − 1 で静か・運用上限で静かでない・求め済み・止まりの秒の 0.1 秒前は偽 | `stall_seconds` が 0 なら止まりで真にならない |

## ラッパーの起動と見回り（`wrapper`）

満たす決定: 決定 2・決定 3・決定 5、受け入れ条件 4・9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._start` | `src/ndf/domains/relay/wrapper.py` | 変更 | `_start(self, req: Launch) -> Optional[Ended]`（変えない） | `signals.set_launch(self.dir, launch)` の直後、`spawn` の前に `drop_other_busy(self.dir, launch)` を打ち、子の環境を `launch_env(self.dir, launch, req.session_id)` で組む。今の起動の記録は書かない（0 件は子の会話の SessionStart の `relay-busy` が書く） |
| `Wrapper._state` | 同上 | 変更 | `_state(self) -> switch_decide.SectionState`（変えない） | 今のとおり状態を組み、`needs_background(state)` が真のときだけ `read_busy(self.dir, sec.launch, sec.session_id)` を読み、`Background` に詰めて `dataclasses.replace` で足す |
| `Wrapper._stop_if_clear` | 同上 | 変更 | `_stop_if_clear(self, sec: Section, allow: Optional[Callable[[Optional[tuple[int, str, str]]], bool]] = None) -> str` | 今のとおり `STOP_LOCK` を待たずに取り、質問のファイルが無いことを確かめた後、`allow` があれば同じ排他の中で `read_busy(self.dir, sec.launch, sec.session_id)` を読み直し、`allow` が偽なら子に何もせず `"held"` を返す |
| `Wrapper._on_handover` | 同上 | 変更 | 変えない | `_stop_if_clear(sec, allow=_busy_zero)` で呼ぶ（読み直しが 0 件のときだけ止める） |
| `Wrapper._on_resume` | 同上 | 変更 | 変えない | `step.kind` が `restart` なら `_stop_if_clear(sec, allow=_busy_not_running)` で呼ぶ（読み直しが 0 件か不明のときだけ止める）。`account` は今のとおり `allow` なし（要求の範囲の外） |
| `Wrapper._on_active` | 同上 | 新設 | `_on_active(self, step: Step, sec: Section) -> Optional[Ended]` | 状態のファイルを `running` と `step.reason` で書いて None を返す（同じ状態と理由は `_write_state` が書き直さない） |
| `Wrapper._ACTIONS` | 同上 | 変更（行を足す） | `"wait_background": _on_wait_like`・`"active": _on_active` | 待ちは `waiting`、動きは `running` を状態に書く |

`_busy_zero(b)` は `b is not None and b[0] == 0`、`_busy_not_running(b)` は `b is None or b[0] == 0` を返すモジュールの関数（`wrapper.py` に新設。判定表の「0 件なら引継ぎ」「0 件・不明なら起動し直し」と同じ条件）。`_on_switch`（次のセグメントのマーカー）と `_halt_broken` は `allow` なしで今のとおり呼ぶ。

- `Wrapper._start`
  - 事後条件: 子を起こす前に、ほかの起動の記録が無い（消せなかったものは残る）。今の起動の記録は無く、子の SessionStart の hook が 0 件を書くまで、読む側は不明として扱う
  - 副作用: ほかの起動の記録を消す
- `Wrapper._stop_if_clear`
  - 事後条件: `"stopped"` を返したとき、子を止める排他の中で、読み直した記録が `allow` を満たし、その後に子を止めて起動を外している。排他を持つ間は `record_start` が書けないので、読み直しの後に開始が記録されて背景の作業が始まることは無い
  - 副作用: 今のとおり。`allow` があるときは記録を 1 回読む（子を止める見回りだけで、受け入れ条件 10 の回数に入らない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Wrapper._start` | ほかの起動の記録を消せない | 権限 | `drop_other_busy` | 残して子を起動する（今の起動の記録だけを読むので判定に入らない） | 無し | 次の起動で消し直す |
| `Wrapper._state` | 記録が不明 | `read_busy` の表・子の SessionStart の hook が書いていない（旧い hook・`relay-busy` を止めた・書けなかった） | `read_busy` の返り | `background=None` で判定する（引継ぎを待ち、止まりは今のとおり） | 状態の理由（`BACKGROUND_UNKNOWN`。待つ条件がそろったとき） | 次の見回りで読み直す |
| `Wrapper._stop_if_clear` | 読み直しで記録が変わった | 0 件を読んだ後に子が背景の作業を始めた | `allow` の返り | 子に何もせず `"held"`（次の見回りで決め直し、`wait_background` になる） | 無し | 同上 |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `Wrapper._note`（変えない） | 理由が前の見回りと変わったときの `wait_background`・`active` の行 | 中継の記録 `log.jsonl` | 背景の作業の説明とコマンドの文（件数と時刻だけを出す） |
| `_on_wait_like`・`_on_active` | 状態と理由 | 状態のファイル（`ndf relay ls` が読む） | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_start` | 起動すると、差し替えた `spawn` が呼ばれる時点で前の起動の記録が無く、今の起動の記録も無く、子の環境に `NDF_RELAY_SESSION` がある。子の環境で SessionStart（`startup`）だけを流し Stop を流さない起動でも、運用上限で静かになれば `handover` | 起動し直した後、前の起動の 1 件を読まない | SessionStart を流さない起動（旧い hook の代わり）と、`NDF_HOOK_OFF=relay-busy` の子の環境で SessionStart と Stop（0 件）を流した起動では、運用上限で静かでも `handover` を返さず `wait_background`（`BACKGROUND_UNKNOWN`）、止まりの秒なら `restart` |
| `_state` | 子の環境で Stop を流して書いた件数が `background` に入る | 運用上限 − 1 で静か・運用上限で静かでない・求め済み・止まりの秒の手前の見回りでは `read_busy` の呼び出しが 0 回、条件がそろった見回りでは 1 回（差し替えて数える） | 運用上限以上で静かな 1 件の子は、差し替えた時計で 0.5 秒ずつ 7,200 回（止まりの秒の 2 倍）回しても、全見回りで `wait_background` で `restart`・`handover` が 0 回。運用上限 − 1 で 1 件の子は、止まりの秒の手前の見回りまで `active`、止まりの秒に達した見回りから `wait_background` に変わり、同じ時計で 7,200 回目まで `restart` が 0 回 |
| `_stop_if_clear` と開始の記録の直列 | 0 件を読んで `handover` を決めた後、`_stop_if_clear` を呼ぶ前に子の環境の Agent の PreToolUse を入口から流すと、`_stop_if_clear` は `"held"` で子を止めず、次の見回りは `wait_background` | ラッパーが `STOP_LOCK` を持つ間（`_stop_child` を差し替えて止めておく）に流した Agent の PreToolUse は拒否され、放した後の子は止まって起動が外れ、同じ PreToolUse を流し直しても拒否される。止まりの `restart` は読み直しが不明なら止め、1 件以上なら止めない | 質問のファイルがあるときは今のとおり読み直す前に `"held"` |
| `_on_wait_like`・`_on_active` | 1 件で待つ見回りで状態のファイルが `waiting` と件数・時刻の理由を持つ。待ちの後に子が出力を出した見回りで `running` と「子が動いている」に戻る | 同じ理由の見回りを続けても状態のファイルを書き直さない | — |
| 旧い起動の遅れた hook | 起動 A を止めて B を起動した後、A の環境の Stop（0 件）と PreToolUse を流しても B の記録は変わらない（A の PreToolUse は今の起動が違うので拒否） | A の環境の hook が A を照らした後に止まり、B の記録の後に書いても、B の記録は変わらない（A のファイルに書く） | — |
| 背景の作業の終わりの後 | B が Agent の PreToolUse で 1 件以上、Stop で 1 件を書いて静かになると `wait_background`。次の Stop で 0 件を書いた後の静かな見回りで `handover` を 1 度だけ返す | Stop の 0 件の書き込みを失敗させると `wait_background` のまま、次の成功した Stop で解ける | — |
