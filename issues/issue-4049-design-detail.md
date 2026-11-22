# #4049 の詳細設計: 背景の作業の記録とラッパーの待ち

設計文書 [issue-4049-design.md](./issue-4049-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。#3995 の実装の後に、その上で実装する（運用上限は `context_thresholds.thresholds` が返す値で、`SectionState.context_limit` に入る）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/
│   ├── hooks/guards/
│   │   ├── relay_busy.py *
│   │   └── relay_mark.py
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

依存の向き: guard `relay_busy` と `relay_mark`（M3a の hook）は `relay.signals`（M3b）だけを読み、`wrapper` は `signals` と `switch_decide` を読む。`switch_decide` は時計と端末とファイルを読まない純粋な関数のまま、`signals` を import しない。`signals` は標準ライブラリだけを使う。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/hooks/guards/relay_busy.py` | 新設 | 中継の下で子の会話の背景の作業の開始を記録し、記録できない開始を止める | `relay.signals`・`hooks.verdict` | モジュール `relay_busy` | `check`・`on_deadline`・`_starts_background`（新設） |
| `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | Stop で子の会話の背景の作業の件数を書いてから、今のマーカーの扱いへ進む | `relay.signals` | モジュール `relay_mark` | `check`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 背景の作業の記録の読み書きと、子の会話の ID の環境変数を足す | 標準ライブラリ | モジュール `signals` | `write_busy`・`read_busy`・`drop_other_busy`・`relay_session`（新設）、`launch_env`（変更）、定数 `SESSION_ENV`・`BUSY_PREFIX`・`LAUNCH_ID_RE` |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | 背景の作業の件数で引継ぎと止まりの判定を待つ | `account_decide` | `SectionState`・`Background`・モジュール `switch_decide` | `needs_background`・`_over`・`_stalled`（新設）、`_alive`（変更）、`SectionState.background`（項目を足す）、定数 `BACKGROUND_WAIT`・`BACKGROUND_LEAST`・`BACKGROUND_UNKNOWN`・`ACTIVE` |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 起動の前に 0 件を書き、要るときだけ記録を読み、待ちと動きを状態に書く | `signals`・`switch_decide` | `Wrapper` | `_on_active`（新設）、`_start`・`_state`（変更）、`_ACTIONS`（行を足す） |
| `contracts/hooks/guards.json` | 変更 | guard `relay-busy` の行を足し、`relay-marker` の `summary` を直す | — | — | — |
| `contracts/hooks/runtimes.json` | 変更 | Codex の `skipped` に `relay-busy` を足す | — | — | — |
| `contracts/domains/ndf-files.json` | 変更 | `relay-busy`（`<名前>/busy-<起動>.json`）の行を足す | — | — | — |
| `contracts/domains/env.json` | 変更 | `NDF_RELAY_SESSION` の行を足す | — | — | — |
| `docs/contracts/hooks.md`・`files.md`・`env.md` | 変更（再生成） | `ndf check --write-spec` で作り直す | — | — | — |
| `skills/development-workflow/references/relay.md` | 変更 | 判定の表の順 5・6、hook の一覧（`relay-busy`・`relay-marker`）、記録の行の種類の表（`wait_background`・`active`）を直す | — | — | — |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更（`glossary.md` は再生成） | 設計の「用語」の 3 語を反映する | — | — | — |
| `tests/domains/hooks/test_guards.py`・`tests/domains/relay/test_relay_parts.py`・`test_relay_decide.py`・`test_relay_wrapper.py` | 変更 | 下の単体テストの観点 | — | — | — |

## 背景の作業の記録（`signals`）

満たす決定: 決定 2・決定 3、受け入れ条件 4・5・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_busy` | `src/ndf/domains/relay/signals.py` | 新設 | `write_busy(directory: Path, launch: str, session_id: str, count: int, source: str) -> bool` | `busy-<launch>.json` を `{"launch", "session_id", "count", "source", "at"}` で置き換え、書けたら真 |
| `read_busy` | 同上 | 新設 | `read_busy(directory: Path, launch: str, session_id: str) -> Optional[tuple[int, str, str]]` | `busy-<launch>.json` が今の会話の記録で形が合えば `(count, source, at)`、ほかは None（不明） |
| `drop_other_busy` | 同上 | 新設 | `drop_other_busy(directory: Path, launch: str) -> None` | `busy-*.json` のうち `launch` のもの以外を消す |
| `relay_session` | 同上 | 新設 | `relay_session(env: Mapping[str, str]) -> Optional[str]` | 環境の `NDF_RELAY_SESSION`。無い・空なら None |
| `launch_env` | 同上 | 変更 | `launch_env(directory: Path, launch: str, session_id: str) -> dict[str, str]` | 今の 2 つに `NDF_RELAY_SESSION` を足して返す |

- `write_busy`
  - 事前条件: `source` は `launch`・`start`・`stop` のどれか。`count` は 0 以上の整数
  - 事後条件: 真を返したとき、`busy-<launch>.json` は引数の値と書いた時刻 `at`（`now_iso`）を持つ。偽を返したとき、ファイルは呼ぶ前のまま
  - 副作用: `launch` が `LAUNCH_ID_RE` に合わなければファイルの名前を作らずに偽。合えば `replace_json` で一時の名前に書いて名前を変える。`OSError` は偽にして外へ出さない。排他は取らない
- `read_busy`
  - 副作用: 無し（読むだけ）。`launch` が `LAUNCH_ID_RE` に合わない・ファイルが無い・`OSError`・`UnicodeDecodeError`・JSON が壊れた・`session_id` が違う・`count` が `bool` か負か整数でない・`source` が 3 つのどれでもないときは None。例外を外へ出さない（見回りの `SignalError` の止まり（`_halt_broken`）へ流さない）
- `drop_other_busy`
  - 副作用: 中継の保存先の `busy-*.json` を `glob` で拾い、名前が `busy-<launch>.json` でないものを消す。消せないもの（`OSError`）は残して続ける

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_busy` | 起動の ID の形が違う | 手で渡した環境変数・空の値 | `LAUNCH_ID_RE` の照らし | 書かずに偽を返す | 無し（呼び手が扱う） | 無し |
| `write_busy` | 書けない | ディスク・権限（`OSError`） | `replace_json` | 書かずに偽を返す | 無し（呼び手が扱う） | 次の書き込みで書き直す |
| `read_busy` | 記録が不明 | 上の副作用の箇条 | 読む場所 | None を返す | 無し（ラッパーの状態の理由に出る） | 次の書き込みで直る |
| `drop_other_busy` | 消せない | 権限 | `unlink` | 残して続ける | 無し | 次の起動で消し直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SESSION_ENV` | 定数 | `src/ndf/domains/relay/signals.py` | `NDF_RELAY_SESSION` |
| `BUSY_PREFIX` | 定数 | 同上 | `busy-`（ファイルは `busy-<起動の ID>.json`） |
| `LAUNCH_ID_RE` | 定数 | 同上 | `^[0-9a-f]{32}$`（ラッパーが作る `uuid.uuid4().hex` の形） |
| `Background` | 型（`NamedTuple`） | `src/ndf/domains/relay/switch_decide.py` | `count: int`・`source: str`・`at: str`。`switch_decide` が型を持ち、`signals` は `switch_decide` を import しないので、`read_busy` は 3 つ組を返し、`wrapper._state` が `Background` に詰める |

`signals.py` の冒頭のファイルの表に `busy-<起動>.json` の行（書き手: 中継のラッパー（起動の前）と子の会話の hook `relay-busy`（PreToolUse）・`relay-marker`（Stop）、中身: その起動の背景の作業の件数）を足す。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ応答の並ぶ PreToolUse（Agent を 2 つ同時に起動） | `busy-<起動>.json` | 取らない。どちらも件数 1・`start` を置き換えで書く | 同じ値を何度書いても同じ |
| PreToolUse と Stop | 同上 | 取らない。Claude Code は応答の Tool の呼び出しを終えてから Stop を発火し、Stop の hook を終えてから次の応答へ進む | Stop が後に書いて正しい件数に置き換わる |
| 止めた子の遅れた hook と新しい起動 | 起動ごとのファイル | 取らない。遅れた hook は旧い起動のファイルにしか書けない | 旧い起動のファイルは次の起動の `drop_other_busy` が消す |
| 入れ子の claude の hook と子の hook | 同上 | 取らない。入れ子の claude は `session_id` が違うので書かない | — |
| ラッパーの読み取りと子の書き込み | 同上 | 取らない。名前の変更で書くので、読み手は前か後の中身のどちらかを読む | 読み直しは次の見回り |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `write_busy` | `signals.write_json`（排他つき）・`replace_json` | `grep -n "def write_json\|def replace_json" src/ndf/domains/relay/signals.py` | `replace_json` を使う。`write_json` は排他を取り、この形は排他を要らない |
| `read_busy` | `signals._read`・`read_next` | `grep -n "def _read\|def read_next" src/ndf/domains/relay/signals.py` | `_read` を使い、`OSError`・`UnicodeDecodeError`・`SignalError` を None にする。`read_next` はマーカーの形に固有 |
| `drop_other_busy` | `signals.drop` | `grep -n "def drop" src/ndf/domains/relay/signals.py` | 1 つずつの削除に `drop` を使う |
| `relay_session` | `signals.relay_launch` | 同上 | 同じ形で足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write_busy`・`read_busy` | 書いた件数 2・`stop` を同じ起動と会話で読み返せる。0・`launch` も読み返せる | `count` が 0 と 1 | `replace_json` を失敗させると偽で前の中身が残る。起動の ID が `../x`・空・大文字なら偽でファイルを作らない。別の会話の記録・壊れた JSON・`count` が文字列か負か `true`・`source` が別の値・ファイルが無い・権限で読めない・不正な UTF-8 のときは `read_busy` が None で例外を上げない |
| `drop_other_busy` | 起動 A・B のファイルがあるとき B を渡すと A だけが消える | ファイルが無くても例外を上げない | 消せないファイルがあっても残りを消す |
| `launch_env`・`relay_session` | `launch_env` の返りに 3 つの変数があり、`relay_session` が会話の ID を返す | 空の `NDF_RELAY_SESSION` は None | — |

## guard `relay-busy`

満たす決定: 決定 2・決定 3、受け入れ条件 3・5・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/relay_busy.py` | 新設 | `check(ctx, svc) -> Verdict \| None` | 子の会話の背景の作業の開始を記録し、記録できなければその呼び出しを拒否する |
| `on_deadline` | 同上 | 新設 | `on_deadline(env: Mapping[str, str], payload: dict) -> Optional[str]` | 締め切りを過ぎたとき、記録し終えていない子の会話の背景の作業の開始なら拒否の理由を返す |
| `_starts_background` | 同上 | 新設 | `_starts_background(tool: str, tool_input: Mapping[str, Any], tools: Sequence[str]) -> bool` | Tool の名前が `tools`（表の `params.background_tools`）にあるか、Bash で `tool_input["run_in_background"] is True` か |

- `check`
  - 事前条件: 表の行の `events` は `["PreToolUse"]`、`tools` は `["Agent", "Monitor", "Bash"]`
  - 事後条件: 背景の作業の開始でない・中継の外（`relay_dir` が None）・`relay_launch` か `relay_session` が None・入力の `session_id` が `relay_session` と違う（入れ子の claude）ときは、ファイルに触れずに None。これらの判定は例外を上げない形で最初に行う（`fail_closed` の例外の拒否に、ほかの Bash・Agent・Monitor を巻き込まない）。子の会話の開始なら `write_busy(directory, launch, session, 1, "start")` を書き、真ならこの実行の payload をモジュールの `_recorded` に残して None、偽なら `deny(BUSY_START_FAILED)`
  - 副作用: `busy-<起動>.json` を書く
- `on_deadline`
  - 事後条件: `check` と同じ条件で子の会話の背景の作業の開始であり、`_recorded is not payload`（この実行の `check` が書き終えていない）ときだけ `BUSY_START_FAILED` を返す。ほかは None（通す）。`ask.on_deadline` と同じ型
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 書けない（`write_busy` が偽） | ディスク・権限・起動の ID の形 | `write_busy` の返り | 止める（`deny`。`deny_key` は `none` なので数えず、案内へ下げない） | PreToolUse の拒否の理由 `BUSY_START_FAILED`（hook の終了コードは 0） | モデルが呼び直せる |
| `check` | 例外 | 想定しない入力 | `dispatch.run`（`fail_closed`） | 止める（`GUARD_FAILED` の文） | 拒否の理由 | 同上 |
| `on_deadline` | 締め切りを過ぎた | hook の予算の使い切り・割り込み | `main._deadline_out` | 記録し終えていなければ止め、書き終えていれば通す | 拒否の理由 `BUSY_START_FAILED` | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `BUSY_START_FAILED` | 定数 | `src/ndf/domains/hooks/guards/relay_busy.py` | `背景の作業の開始を中継へ記録できなかったので、始めない。もう一度呼ぶ（中継のラッパーは記録の無い背景の作業を守れない）` |
| `_recorded` | モジュールの変数 | 同上 | None（記録し終えた実行の payload。`ask._recorded` と同じ扱い） |

表の行（`contracts/hooks/guards.json`）:

| 項目 | 値 |
| --- | --- |
| `name` | `relay-busy` |
| `summary` | 中継の下で、子の会話が背景の作業を始める呼び出し（Agent・Monitor・`run_in_background` の Bash）の前に、起動ごとの記録へ「1 件以上」を書く。書けない開始は締め切りの経路でも止める（ラッパーは 0 件と確かめた子だけを引継ぎで止める。#4049） |
| `module` | `ndf.domains.hooks.guards.relay_busy` |
| `events` | `["PreToolUse"]` |
| `tools` | `["Agent", "Monitor", "Bash"]` |
| `order` | 15（`context-limit` の 20 より前） |
| `strict` | 偽 |
| `fail_closed` | 真 |
| `deny_key` | `none` |
| `limits` | `[]` |
| `params` | `{"background_tools": ["Agent", "Monitor"]}` |

`contracts/hooks/runtimes.json` の Codex の `skipped` に `"relay-busy": "中継のラッパーは Claude Code だけを包む"` を足す。`contracts/domains/ndf-files.json` に `{"id": "relay-busy", "location": "relay-wrappers", "path": "<名前>/busy-<起動>.json", "kind": "exchange", "tracked": false, "writer": "中継のラッパーと PreToolUse・Stop の hook（M3a）", "ends": "run"}` を足す。`contracts/domains/env.json` に `{"id": "NDF_RELAY_SESSION", "area": "relay", "set_by": "ndf", "type": "string", "secret": false, "meaning": "中継のラッパーが子へ渡す子の会話の ID。hook は入力の session_id がこれと同じ会話のときだけ背景の作業の記録を書く（子のシェルから起動した claude を外す）"}` を足す。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `check`・`on_deadline` | `ask.check`・`ask.on_deadline`（`fail_closed` で記録し終えたかを `_recorded` で見る型） | `grep -n "_recorded\|def on_deadline" src/ndf/domains/hooks/guards/ask.py` | 同じ型で書く。質問の転送は持たないので関数は共有しない |
| `_starts_background` | 背景の作業の開始を見分ける既存の関数 | `grep -rn "run_in_background" src/ndf/domains/hooks` | 無い。新設する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 中継の下の子の会話で Agent・Monitor・`run_in_background: true` の Bash の PreToolUse が「1 件以上」を書いて None。`agent_id` のある Agent（サブエージェントの中）でも書く | `run_in_background` が偽か無い Bash は触れずに None。`run_in_background` が文字列 `"true"` の Bash も触れない | 中継の外・`NDF_RELAY_SESSION` が無い・入力の `session_id` が違う（入れ子の claude）ときは触れずに None。書き込みを失敗させると `deny`。`check` を例外にする差し替えでも、背景でない Bash は通る（入口から流して確かめる）。続けて 3 回失敗させても 3 回とも `deny` |
| `on_deadline` | 記録し終えていない子の会話の Agent の payload で `BUSY_START_FAILED` | 書き終えた同じ payload では None | 背景でない Bash・Read・中継の外・入れ子の claude では None。予算を使い切った後と締め切りの後の両方で、入口から流した Agent は拒否、Read と背景でない Bash は空の出力 |

## 中継のマーカーの guard（`relay_mark`）

満たす決定: 決定 1・決定 2・決定 3、受け入れ条件 1・6・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | `check(ctx, svc) -> Verdict \| None`（変えない） | 中継の下で、最後の応答とブロックの有無を見る前に、子の会話の Stop の件数を書く。その後は今のまま |

- `check`
  - 変える前: 中継の外か最後の応答が空なら None。ブロックが無ければ None。背景の作業が残れば変更要求、無ければ次のセグメントのマーカーを書く
  - 変えた後: 最初に、中継の下で `relay_launch`・`relay_session` があり、入力の `session_id` が `relay_session` と同じで、`ctx.payload.get("background_tasks")` がリストなら `write_busy(directory, launch, session, len(<リスト>), "stop")` を書く（`ctx.background_tasks` は使わない。項目が無い・リストでないときは書かない）。書けたかを計測の `notes` の `relay_busy` に残す。その後は今のまま（`fail_closed` は偽のまま）
  - 副作用: `busy-<起動>.json` を書く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 件数を書けない | ディスク・権限 | `write_busy` の返り | 書かずに前の記録を残し、今のマーカーの扱いへ進む | 無し | 次の Stop で書き直す |
| `check` | 列が無い・リストでない | タスクの登録簿に届かない | 入力の `background_tasks` の型 | 書かずに前の記録を残す | 無し | 同上 |

表の行の `summary` を「中継の下で、子の会話の Stop の背景の作業の件数を起動ごとの記録へ書く（列が無ければ書かない）。最後の応答が次のセグメントのブロックを持てば、背景の作業が残る間は変更要求、無ければ中継のマーカーを書く（#1877・#2438・#4049）」に直す。`events`・`fail_closed` は変えない。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 中継の下の子の会話の Stop で `background_tasks` が 2 件なら 2、空なら 0 を書く。ブロックの無い応答・最後の応答が空でも書く | 項目が無い・`null`・文字列の `background_tasks` では書かず、前の 1 件以上が残る | 入れ子の claude（`session_id` が違う）・中継の外では書かない。書き込みを失敗させても変更要求を返さず、ブロックがあれば今のとおりマーカーを扱う |

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
| `Wrapper._start` | `src/ndf/domains/relay/wrapper.py` | 変更 | `_start(self, req: Launch) -> Optional[Ended]`（変えない） | `signals.set_launch(self.dir, launch)` の直後、`spawn` の前に `write_busy(self.dir, launch, req.session_id, 0, "launch")` と `drop_other_busy(self.dir, launch)` を打ち、子の環境を `launch_env(self.dir, launch, req.session_id)` で組む |
| `Wrapper._state` | 同上 | 変更 | `_state(self) -> switch_decide.SectionState`（変えない） | 今のとおり状態を組み、`needs_background(state)` が真のときだけ `read_busy(self.dir, sec.launch, sec.session_id)` を読み、`Background` に詰めて `dataclasses.replace` で足す |
| `Wrapper._on_active` | 同上 | 新設 | `_on_active(self, step: Step, sec: Section) -> Optional[Ended]` | 状態のファイルを `running` と `step.reason` で書いて None を返す（同じ状態と理由は `_write_state` が書き直さない） |
| `Wrapper._ACTIONS` | 同上 | 変更（行を足す） | `"wait_background": _on_wait_like`・`"active": _on_active` | 待ちは `waiting`、動きは `running` を状態に書く |

- `Wrapper._start`
  - 事後条件: 子を起こす前に、今の起動の記録が 0 件で、ほかの起動の記録が無い（消せなかったものは残る）。0 件を書けなければ記録が無く、読む側は不明として扱う
  - 副作用: 記録を書き、消す。`spawn` が失敗して `set_launch(None)` で起動を外したときは、書いた 0 件を消さない（次の起動の `drop_other_busy` が消す）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Wrapper._start` | 0 件を書けない | ディスク | `write_busy` の返り | 子を起動して続ける（引継ぎは不明として待ち、止まりは今のとおり） | 状態の理由（`BACKGROUND_UNKNOWN`。待つ条件がそろったとき） | 子の最初の開始か Stop で書かれる |
| `Wrapper._state` | 記録が不明 | `read_busy` の表 | `read_busy` の返り | `background=None` で判定する | 同上 | 次の見回りで読み直す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `Wrapper._note`（変えない） | 理由が前の見回りと変わったときの `wait_background`・`active` の行 | 中継の記録 `log.jsonl` | 背景の作業の説明とコマンドの文（件数と時刻だけを出す） |
| `_on_wait_like`・`_on_active` | 状態と理由 | 状態のファイル（`ndf relay ls` が読む） | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_start` | 起動すると、差し替えた `spawn` が呼ばれる時点で今の起動の記録が 0 件で、前の起動の記録が無く、子の環境に `NDF_RELAY_SESSION` がある。Stop を流さない起動でも、運用上限で静かになれば `handover` | 起動し直した後、前の起動の 1 件を読まない | `write_busy` を失敗させても子は起動し、運用上限で静かでも `handover` を返さない |
| `_state` | 子の環境で Stop を流して書いた件数が `background` に入る | 運用上限 − 1 で静か・運用上限で静かでない・求め済み・止まりの秒の手前の見回りでは `read_busy` の呼び出しが 0 回、条件がそろった見回りでは 1 回（差し替えて数える） | 1 件で出力が止まった見回りを差し替えた時計で 0.5 秒ずつ 7,200 回回しても、判定はずっと `wait_background` で `restart` が 0 回 |
| `_on_wait_like`・`_on_active` | 1 件で待つ見回りで状態のファイルが `waiting` と件数・時刻の理由を持つ。待ちの後に子が出力を出した見回りで `running` と「子が動いている」に戻る | 同じ理由の見回りを続けても状態のファイルを書き直さない | — |
| 旧い起動の遅れた hook | 起動 A を止めて B を起動した後、A の環境の Stop（0 件）と PreToolUse を流しても B の記録は変わらない | A の環境の hook が A を照らした後に止まり、B の記録の後に書いても、B の記録は変わらない（A のファイルに書く） | — |
| 背景の作業の終わりの後 | B が Agent の PreToolUse で 1 件以上、Stop で 1 件を書いて静かになると `wait_background`。次の Stop で 0 件を書いた後の静かな見回りで `handover` を 1 度だけ返す | Stop の 0 件の書き込みを失敗させると `wait_background` のまま、次の成功した Stop で解ける | — |
