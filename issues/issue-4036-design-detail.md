# #4036 の詳細設計: 何もしていない conductor を止まりとして知らせる

設計文書 [issue-4036-design.md](./issue-4036-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/limits.json                            parallel-run.idle の閾値
├── src/ndf/
│   ├── domains/hooks/guards/relay_mark.py           Stop のたびに活動を書く
│   ├── domains/relay/signals.py                     activity.json の読み書き
│   ├── domains/relay/observe.py                     サブエージェントの会話の記録のパス・生きている背景のシェル
│   ├── domains/relay/hub.py                         ndf relay ls の行に session_id・cwd・pid
│   ├── progress/lane/liveness.py                    Activity・Probes.activity・idle_conductor
│   └── progress/plan/
│       ├── run.py                                   活動を読む実装と、止まりの材料
│       └── run_decide.py                            lane_stalled のリクエストに moved・background
└── tests/
    ├── domains/hooks/test_relay_mark.py *
    ├── domains/relay/test_relay_observe.py
    ├── progress/test_lane_liveness.py
    └── progress/test_plan_run_stall.py *
```

依存の向き: 書く側は `hooks/guards/relay_mark.py` → `domains/relay/signals.py` だけである。保存先は環境変数 `NDF_RELAY_DIR` から得るので `registry.py` を読まない（`registry.py` は `facts` 経由で `jsonschema` を読み込み、hook の許可 import の表にも無い）。読む側は `progress/plan/run.py` → `domains/relay/registry.py`（保存先の解決に `wrapper_dir`）・`domains/relay/signals.py`（活動の読み取りに `read_activity`）・`domains/relay/observe.py` である。そこから `progress/lane/liveness.py`（判定）→ `progress/plan/run_decide.py`（知らせ）へ続き、いずれも一方向である。`liveness.py` は `relay` を読まず、材料は今までどおり `Probes` の呼び出しが集める。`registry.py` は `hooks` を読まない。循環は無い。要素どうしの順序は設計文書の「処理の流れ」にある。

**実装で置き場を変えたこと（4036-u2-judge）:** 活動を読む処理（下の「活動を読む」の手順 1〜6）は、`progress/plan/run.py` ではなく `domains/relay/observe.py` の `conductor_activity` に置いた。`domains/relay/hub.py` の `listing` が動いている行ごとにこれを呼び、`ndf relay ls` の行に `turn_done`・`moved`・`background` を載せる（結果のスキーマは `relay.result.schema.json`）。`progress/plan/run.py` の `lane_activity` は、`launch.wrappers` が読んだ一覧の行の 3 つの値を `liveness.Activity` へ写すだけである。理由は 2 つある。層の規則（`checks/rules.json` の `layers`。`contracts → adapters → core → progress → domains`）では `progress` から `domains` を読めない。また子の表（`checks/children.json`）で P3（`ndf.progress.plan`）の先に要るものに M3b（`ndf.domains.relay`）が無い（規則 child-deps の IG1）。`progress` は今もラッパーの一覧を `ndf relay ls` の出力で読んでいるので、同じ経路に載せた。活動は `ndf relay ls --activity`（`hub.listing(ctx, activity=True)`）のときだけ読む。`--activity` を付けるのは生死の判定が使う `run.probes` の一覧（`launch.wrappers(activity=True)`）だけで、起動の許可（`machine.admitted`）など稼働の印しか使わない一覧は活動を読まない。/proc の親の一覧（`process_parents`）は、シェルの生死を確かめる行（応答が終わり、今の応答の Stop の記録がある行）に届いたときに初めて読み、その 1 回の一覧の中で全行が共有する（`functools.cache`）。会話の記録と背景のシェルの出力の置き場は、ラッパーが子に渡した環境（起動先の `Target.env`。従量接続・登録済みキーでは共有の `CLAUDE_CONFIG_DIR`）で組み、相対のパスは子の作業ディレクトリを基準に絶対化して `wrapper.json` に書いた値（`transcript`・`shell_outputs`）を使い、一覧を呼ぶ側の環境（`CLAUDE_CONFIG_DIR`・`CLAUDE_CODE_TMPDIR`）に依らない。判定の条件・閾値・知らせの中身は変えていない。

設計文書の触るファイルのうち `plugins/ndf/src/ndf/progress/plan/cli.py` は、変える行が無い。活動を読む項目を `run.probes` に足すだけで `ndf lanes status` にも当たるためで、`cli.py:103` が `run.probes(where.repo, a.plan)` を渡す今の形のままにする。この経路の確かめは最後の「判定の値が増えることの当たり先」にある。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/contracts/limits.json` | 変更 | 何もしていないとみなすまでの秒を持つ | 無し（データ） | `policies` | 無し |
| `plugins/ndf/src/ndf/domains/relay/signals.py` | 変更 | ラッパーの保存先の `activity.json` を読み書きする（Stop の hook の import の表にあるモジュールに置き、hook の閉包に `jsonschema` を入れない） | 無し（標準ライブラリだけ） | モジュール `ndf.domains.relay.signals` | `write_activity`（新設）・`read_activity`（新設） |
| `plugins/ndf/src/ndf/domains/relay/observe.py` | 変更 | 背景のサブエージェントの会話の記録のパスを組み、conductor のプロセスの下で生きている背景のシェルを数える | `ndf.domains.relay.signals`（今までどおり）・`os`・`pathlib` | モジュール `ndf.domains.relay.observe` | `subagent_transcript_path`（新設）・`process_parents`（新設）・`live_shells`（新設）・`shell_output_path`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | Stop のたびに背景の作業の一覧（`id`・`type`。観測できなければ `null`）とセッションを書く | `ndf.domains.relay.signals`（保存先は `NDF_RELAY_DIR`。`registry` は読まない） | モジュール `ndf.domains.hooks.guards.relay_mark` | `check`（変更） |
| `plugins/ndf/src/ndf/domains/relay/hub.py` | 変更 | ラッパーの一覧の行に会話の記録を組む材料とラッパーの `pid` を載せる | `ndf.domains.relay.registry` | モジュール `ndf.domains.relay.hub` | `listing`（変更） |
| `plugins/ndf/src/ndf/progress/lane/liveness.py` | 変更 | 活動の型と新しい判定を足す | `ndf.contracts.limits` | モジュール `ndf.progress.lane.liveness` | `Activity`（新設）・`idle_after`（新設）・`judge`（変更）・`judge_lanes`（変更）・`Probes`（変更） |
| `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | 活動を読む実装（終わったサブエージェントを除いて数える）と、止まりの材料に背景の作業の数を足す | `ndf.domains.relay.registry`（保存先の解決）・`signals`（活動の読み取り）・`observe`・`ndf.identity` | モジュール `ndf.progress.plan.run` | `lane_activity`（新設）・`probes`（変更）・`_judge`（変更）。`run.py` の `Judged`（変更） |
| `plugins/ndf/src/ndf/progress/plan/run_decide.py` | 変更 | 止まりの知らせに、ISO 8601 へ直した最後に動いた時刻と背景の作業の数を載せる | 無し（標準ライブラリの `datetime` だけ） | モジュール `ndf.progress.plan.run_decide` | `Stall`（変更）・`_stall_report`（変更）・`with_stalls`（変更） |
| `plugins/ndf/tests/domains/hooks/test_relay_mark.py` | 新設 | Stop の hook が活動を書く | `ndf.domains.hooks` | モジュール | テスト関数 |
| `plugins/ndf/tests/domains/relay/test_relay_observe.py` | 変更 | サブエージェントの会話の記録のパスを組む | `ndf.domains.relay.observe` | モジュール | テスト関数 |
| `plugins/ndf/tests/progress/test_lane_liveness.py` | 変更 | 新しい判定の条件と境目 | `ndf.progress.lane.liveness` | モジュール | テスト関数 |
| `plugins/ndf/tests/progress/test_plan_run_stall.py` | 新設 | 止まりの知らせの中身 | `ndf.progress.plan` | モジュール | テスト関数 |

## 活動の記録（`activity.json`）

満たす決定: 決定 2、受け入れ条件 2、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_activity` | `plugins/ndf/src/ndf/domains/relay/signals.py` | 新設 | `write_activity(directory: Path, tasks: Any, session_id: Optional[str], reply: Optional[str] = None) -> bool` | ラッパーの保存先の `activity.json` に、書いた時刻・セッション・終えた応答の `uuid`（`reply`）・背景の作業の一覧（要素ごとの `id`・`type`。観測できなければ `null`）を置く。書けなければ偽 |
| `read_activity` | `plugins/ndf/src/ndf/domains/relay/signals.py` | 新設 | `read_activity(directory: Path) -> Optional[dict[str, Any]]` | `activity.json` を読む。無い・壊れていれば `None` |

- `write_activity`
  - 事前条件: `directory` はラッパーの保存先（`wrapper_dir` が返すディレクトリ）。`tasks` は Stop の hook の入力の `background_tasks` の生の値（`ctx.payload.get("background_tasks")`。欠けていれば `None`）で、`ctx.background_tasks`（リストでなければ空のリストへ正規化する。`domains/hooks/context.py:609-611`）は使わない。`session_id` は hook の入力の `session_id`。`reply` は hook の `ctx.last_reply_id`（会話の記録の末尾 `hook.transcript-tail` から選んだ最後の `assistant` の記録の `uuid`。読めなければ `None`）
  - 事後条件: `activity.json` が設計文書の「データ構造」の形（`at`・`session_id`・`reply`・`tasks`）で存在する。`tasks` がリストでない（欠けている・`null`・別の型）ときは `tasks` を `null` にして書く（背景の作業を観測できなかった印。空のリストとは別）。リストのときは、要素は入力の要素と同じ数・同じ順で、`{"id": <文字列>, "type": <文字列>}` だけを持つ（`status`・`description` は写さない）。入力の要素が object でない・`id` か `type` が文字列でないときは、その値を空の文字列にして要素の数を保つ（読む側で「走っている」とみなす）。最後に書いた中身だけが残る
  - 副作用: ファイルの書き込み 1 つ（`signals.replace_json`。`write_state` と同じ置き換えの書き方）
- `read_activity`
  - 事前条件: 無し
  - 事後条件: 返る辞書は `at`（ISO 8601 の文字列）・`session_id`（文字列か `None`）・`reply`（文字列か `None`。文字列でも `null` でもなければ形が違うとみなす）・`tasks`（要素が `id`・`type` の文字列を持つ object のリストか `None`）を持つ。キーが欠ける・形が違えば `None`。`tasks` が `null` の記録は読めた記録として返し、`tasks` を `None` のまま渡す
  - 副作用: 無し（読み取りだけ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_activity` | 書けない | 保存先の権限・容量（`OSError`） | `signals.replace_json` | 既定値で続ける（偽を返し、例外を外へ出さない） | 出力なし（conductor の応答は止めない。決定 2 の代償） | 次の Stop で書き直す。判定は古い記録か `None` のまま |
| `read_activity` | 読めない・形が違う | ファイルが無い・JSON が壊れている | 読み取りの直後 | 既定値で続ける（`None`） | 判定は今までどおり `alive`（決定 1 の「読めないとき」） | 後始末は無い |

`write_state` が壊れた状態を `RegistryError` にするのと違い、活動の記録は診断の値なので例外にしない（`append_log` と同じ方針。`src/ndf/domains/relay/registry.py:66-72`）。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `ACTIVITY_FILE` | 定数 | `plugins/ndf/src/ndf/domains/relay/signals.py` | `"activity.json"` |

ラッパーの保存先に置くファイルの一覧は `registry.py` の冒頭の表が唯一の保存先なので、そこに 1 行足す。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| conductor の Stop の hook（書く）と、耐久ワークフローの読み直し・`ndf lanes status`（読む） | `<ラッパーの保存先>/activity.json` | 取らない。書きは `signals.replace_json`（一時ファイルを作って置き換える）で、読む側は中途半端な中身を読まない | 同じ Stop で 2 回呼ばれても最後の中身になる。書けなかった回は次の Stop が書き直す |
| 1 つのラッパーの中の複数のセッション | 同上 | ラッパー 1 つにつき conductor は 1 つなので、書き込み元は 1 つ | 前のセグメントの記録は上書きされる（最後の応答だけを見る） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `write_activity` | `registry.write_state`（`src/ndf/domains/relay/registry.py:62`）と `signals.replace_json`（`signals.py:145`） | `grep -n "def write_state" -A 3 plugins/ndf/src/ndf/domains/relay/registry.py` | 使う。`replace_json` をそのまま呼び、`write_state` と同じ置き換えの書き方にする |
| `write_activity` | `signals.write_json`（`signals.py:156`。排他を待って書く） | `grep -n "def write_json" -A 6 plugins/ndf/src/ndf/domains/relay/signals.py` | 使えない。排他の待ちを持つのは書き込み元が複数あるシグナルファイル用で、書き込み元が 1 つの活動の記録では待ちの時間だけが増える |
| `read_activity` | `registry.read_state`（`registry.py:49`） | `grep -n "def read_state" -A 12 plugins/ndf/src/ndf/domains/relay/registry.py` | 使えない（壊れた中身を `RegistryError` にする）。形の検査だけ `None` に倒して踏襲する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write_activity` | `subagent` と `shell` の 2 件を渡すと、`activity.json` が `at`・`session_id`・`reply` と、`id`・`type` だけを持つ 2 要素の `tasks` を持つ | 背景の作業が 0 件の明示の空のリストでも書く（`tasks` が空のリスト）。`id` の無い要素も空の文字列の `id` で 1 要素として書く。`tasks` に `None`・文字列・object を渡すと `tasks` が `null` で書かれ、空のリストにならない | 書けないディレクトリで偽を返し、例外を上げない |
| `read_activity` | 書いた中身をそのまま読める | `tasks` が空のリストの記録と `null` の記録を読み分ける（前者は空のリスト、後者は `None`） | ファイルが無い・JSON が壊れている・`tasks` がリストでも `null` でもない・要素の `id` が文字列でない・`at` が無い・`reply` が文字列でも `null` でもないとき `None` |

## Stop の hook が活動を書く（`relay-marker` guard）

満たす決定: 決定 2、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `plugins/ndf/src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | `check(ctx, svc) -> Verdict \| None`（変わらない） | 中継の下なら、まず活動（背景の作業の一覧）を書く。次のセグメントのマーカーの判定は今までどおり |
| `last_reply_id` | `plugins/ndf/src/ndf/domains/hooks/context.py`（`HookContext`。`last_reply` の直後） | 新設 | `@cached_property last_reply_id -> Optional[str]` | `transcript_tail` から末尾の側で最初に見つかる `assistant` の記録の `uuid`（`observe.reply_id` と同じ選び方）。読めない・`uuid` が文字列でなければ `None` |

- `check`
  - 事前条件: Stop のイベントで呼ばれる（guard の表の行は変えない）
  - 事後条件: 中継の下（`signals.relay_dir` がディレクトリを返す）なら、戻り値によらず `activity.json` が書かれている
  - 副作用: 今までの副作用（次のセグメントのマーカーの書き込み）に、活動の記録の書き込みが 1 つ増える

書く位置は `signals.relay_dir(ctx.env)` の判定の直後である。`ctx.last_reply` が空のときと次のセグメントのブロックが無いときの早い戻りより前に置き、Stop のたびに書く。背景の作業が残るときの変更要求（`pushback`）の前でもあるので、背景の作業が 1 件以上の記録も残る。渡すのは入力の生の値 `ctx.payload.get("background_tasks")` と `ctx.payload.get("session_id")`、終えた応答の `uuid`（`ctx.last_reply_id`）である。`ctx.last_reply_id` は `ctx.transcript_tail`（`hook.transcript-tail` の大きさの末尾）から最後の `assistant` の記録を選んでその `uuid` を返す。hook の import の表（`contracts/hooks/imports.json`）は `ndf.adapters.claude` を禁じ、それを読む `observe` を hook から読めないので、選ぶ規則（末尾から最初に見つかる `assistant` の記録）だけを `observe.reply_id` とそろえて `domains/hooks/context.py` に置く。`ctx.background_tasks`（`domains/hooks/context.py:609-611`）はフィールドの欠けや型の違いを空のリストへ正規化するので、そのまま書くと「背景の作業が空だと確かめた記録」と「観測できなかった記録」が同じになる。明示の空のリストだけを背景の作業 0 件の証拠にするため、正規化の前の値を渡す（既存の `pushback` の判定は今までどおり `ctx.background_tasks` を使う）。

中継の下でラッパーの保存先を指すのは環境変数 `NDF_RELAY_DIR` である。`wrapper.py:740` が `signals.launch_env(self.dir, launch)` で渡し、`self.dir` は `registry.wrapper_dir(...)`（`wrapper.py:278`）である。設計文書の「未確認のまま残ること」の 3 番目（guard がラッパーの保存先を知っているか）は、この 2 行で足りる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 活動を書けない | 保存先の権限・容量 | `signals.write_activity` の返り（偽） | 既定値で続ける（判定の続きへ進む。conductor の進行を止めない） | 出力なし | 次の Stop で書き直す |
| `check` | 中継の下でない | `NDF_RELAY_DIR` が無い・ディレクトリでない | `signals.relay_dir` の返り | 今までどおり `None`（通す） | 出力なし | 判定は今までどおり `alive`（決定 2 の代償） |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 中継の下でない | 何も書かずに `None` |
| `check` | 中継の下・背景の作業が 1 件以上・次のセグメントのブロックがある | 活動（その一覧）を書き、今までどおり `pushback` |
| `check` | 中継の下・背景の作業が 0 件・次のセグメントのブロックがある | 活動（空の一覧）を書き、今までどおり次のセグメントのマーカーを書いて `passing` |
| `check` | 中継の下・次のセグメントのブロックが無い | 活動を書いて `None` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 背景の作業が 0 件の Stop（入力の `background_tasks` が `[]`）で `activity.json` の `tasks` が空のリストになり、`session_id` が入力の値になる（決定 2）。`reply` が会話の記録の最後の `assistant` の記録の `uuid` になる | 背景の作業が `subagent` 1 件・`shell` 1 件の Stop でも書かれ、`tasks` が 2 件の `id`・`type` になる。次のセグメントのブロックが無い Stop・`last_reply` が空の Stop でも書かれる | 書けない保存先でも例外を上げず、今までの判定をそのまま返す。入力に `background_tasks` が無い Stop・文字列の Stop で、`tasks` が `null` で書かれる |

## ラッパーの一覧の行（`ndf relay ls`）

満たす決定: 決定 1、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `listing` | `plugins/ndf/src/ndf/domains/relay/hub.py` | 変更 | `listing(ctx: Context) -> list[dict[str, Any]]`（変わらない） | 各行に `session_id`・`cwd`・`pid`（ラッパーの状態ファイルの値。`pid` はラッパーのプロセス）を足す。既存のキーは変えない |

- `listing`
  - 事前条件: 変わらない
  - 事後条件: 行に `session_id`（文字列か `None`）・`cwd`（文字列か `None`）・`pid`（整数か `None`）が増える。状態のファイルが無い行ではどれも `None`
  - 副作用: 変わらない（`registry.read_state` の読み取りを 1 行で使い回す）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `listing` | 状態のファイルが無い | 起動の途中のラッパー | `registry.read_state` の返り（`None`） | 既定値で続ける（`session_id`・`cwd` を `None`） | 一覧の行に `null` が載る | 判定は活動を読めず `alive` のまま |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `listing` | 動いているラッパーの行に `session_id` と `cwd` が載る | 登録簿に無くロックだけを持つラッパーの行でも載る | 状態のファイルが壊れているときは今までどおり `RegistryError`（この変更で増やさない） |

## 活動を読む（`Probes.activity`）

満たす決定: 決定 1、決定 2、受け入れ条件 1

**置き場の変更（4036-u2-judge）:** この節の `lane_activity` の手順 1〜6 は `observe.conductor_activity(row, directory, look)` が行い、`hub.listing` が `ndf relay ls` の行へ載せる。`row` はラッパーの状態の `session_id`・`transcript`・`shell_outputs`・`pid` を持ち、手順 2 の会話の記録のパスと手順 5 の出力のファイルの置き場は、`transcript_path`・`shell_output_path` を一覧の側で組まずにこの値を使う。`look`（`observe.Look`）は読む時刻・会話の記録の末尾の大きさ（`hook.transcript-tail`）・親 PID の一覧を返す口・`/proc` の場所を持ち、1 回の一覧で全行が共有する。`run.lane_activity(row)` は行の `turn_done` が真偽値のときだけ `Activity(turn_done, moved, background)` を返し、ほかは `None` を返す。応答の途中（`turn_done` が偽）と最後の応答の時刻を読めないときは、背景の作業を数えない（`background` は `None`）。どちらも判定は `alive` で変わらない。手順 3 で活動の記録が今の応答のものかは、時刻でなく応答の `uuid` で確かめる。Stop の hook は会話の記録の末尾から選んだ最後の `assistant` の記録の `uuid` を `activity.json` の `reply` に書き（`ctx.last_reply_id`）、`conductor_activity` は手順 2 で読んだ同じ会話の記録から同じ規則で選んだ `uuid`（`observe.reply_id` と同じ値。手順 2 と同じ 1 回の読み取りで得る）と比べる。`at` は秒で書かれるので、時刻で比べると前の Stop と最新の応答が同じ秒に入ったときに前の Stop の記録も通ってしまう。hook が走った時点で最後の応答がまだ記録に書かれていなければ、hook は前の応答の `uuid` を書くので照合は通らず、背景の作業は数えない（`alive` の側へ倒れる）。置き場の理由は地図の「実装で置き場を変えたこと」にある。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Activity` | `plugins/ndf/src/ndf/progress/lane/liveness.py` | 新設 | `@dataclass(frozen=True) class Activity: turn_done: bool; moved: Optional[float]; background: Optional[int]` | 判定が使う活動の材料（設計文書の「データ構造」の 3 つ）。`background` は終わった背景の作業を除いた後の数、`moved` は conductor の最後の応答と除いた背景の作業の終わった時刻のうち最も遅い時刻で、`judge` は数え直さない |
| `Probes` | `plugins/ndf/src/ndf/progress/lane/liveness.py` | 変更 | `activity: Callable[[str], Optional[Activity]] = lambda _: None` を足す | レーンの名前から活動を読む項目。既定は `None`（活動を読まない呼び手では今までどおりの判定になる） |
| `subagent_transcript_path` | `plugins/ndf/src/ndf/domains/relay/observe.py`（`transcript_path` の直後） | 新設 | `subagent_transcript_path(transcript: Path, agent_id: str) -> Optional[Path]` | セッションの会話の記録のパスと背景の作業の `id` から、サブエージェントの会話の記録のパス（`<記録から .jsonl を除いたもの>/subagents/agent-<id>.jsonl`）を組む。`id` がファイル名に使えない形なら `None` |
| `process_parents` | `plugins/ndf/src/ndf/domains/relay/observe.py`（`live_shells` の直前） | 新設 | `process_parents(proc: Path = Path("/proc")) -> Optional[dict[int, int]]` | `/proc/<pid>/stat` からプロセスごとの親 PID の一覧を組む。判定の読み直しごとに 1 度だけ組み、各レーンの `live_shells` で共有する。`/proc` を読めなければ `None` |
| `live_shells` | `plugins/ndf/src/ndf/domains/relay/observe.py` | 新設 | `live_shells(wrapper_pid: int, parents: Optional[Mapping[int, int]], proc: Path = Path("/proc")) -> Optional[int]` | `parents` で、ラッパーのプロセスの子の `claude` の、そのまた子を求め、そのうち Bash の Tool のシェル（`cmdline` が `bash` で、設定ディレクトリ（既定の `~/.claude` か、登録済みアカウントの `CLAUDE_CONFIG_DIR`）の `/shell-snapshots/snapshot-` を含む。目印は設定ディレクトリの名前に依らない `/shell-snapshots/snapshot-`）の数を返す。`parents` が `None`・ラッパーの子に `claude` が無い・`cmdline` を読めないときは `None` |
| `shell_output_path` | `plugins/ndf/src/ndf/domains/relay/observe.py`（`subagent_transcript_path` の直後） | 新設 | `shell_output_path(environ: Mapping[str, str], cwd: Path, session_id: str, task_id: str) -> Optional[Path]` | 背景のシェルの出力のファイル（`<一時の保存先>/claude-<uid>/<作業ディレクトリの鍵>/<session_id>/tasks/<id>.output`）のパスを組む。`id` がファイル名に使えない形なら `None` |
| `conductor_activity` | `plugins/ndf/src/ndf/domains/relay/observe.py` | 新設 | `conductor_activity(row: Mapping[str, Any], directory: Path, look: Look) -> dict[str, Any]` | ラッパーの状態の値（`session_id`・`transcript`・`shell_outputs`・`pid`）とラッパーの保存先から、最後の応答が終わったか（`turn_done`）・最後に動いた時刻（`moved`）・まだ走っているとみなす背景の作業の数（`background`）を読む。下の手順 1〜6 を行う |
| `reply_id` | `plugins/ndf/src/ndf/domains/relay/observe.py`（`turn_done` の直後） | 新設 | `reply_id(path: Path, size: int) -> Optional[str]` | 会話の記録の末尾 `size` バイトから最後の `assistant` の記録を選び、その `uuid` を返す（`turn_done` と同じ読み方。読めない・`uuid` が文字列でなければ `None`）。`conductor_activity` は `turn_done` の 2 つと同じ 1 回の読み取りでこの値を得て、`activity.json` の `reply` と比べる |
| `listing` | `plugins/ndf/src/ndf/domains/relay/hub.py` | 変更 | `listing(ctx: Context, …)`（`--activity` のときだけ `Look` を組む） | 動いているラッパーの行ごとに `conductor_activity` を呼び、返りの 3 つを `ndf relay ls` の行へ載せる。`Look` の時刻・末尾の大きさ・親 PID の一覧（1 回の一覧で 1 度だけ組む）は全行で共有する |
| `lane_activity` | `plugins/ndf/src/ndf/progress/plan/run.py` | 新設 | `lane_activity(row: Optional[Mapping[str, Any]]) -> Optional[liveness.Activity]` | 一覧の行に載った `turn_done`・`moved`・`background` を `Activity` へ写す。ファイル・環境変数・`/proc` を読まない（層の規則で `progress` は `domains` を読まない） |
| `probes` | `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | `probes(repo: str, plan: str) -> liveness.Probes`（変わらない） | `activity` の項目に `lane_activity` を結び付け、活動付きのラッパーの一覧（`launch.wrappers(activity=True)`）の行を渡す。一覧は今までどおり 1 回の読み直しで 1 度だけ読み（`functools.cache`）、レーンの間で共有する |

- `subagent_transcript_path`
  - 事前条件: `transcript` は `transcript_path` が返すセッションの会話の記録のパス
  - 事後条件: `agent_id` が空でなく英数字・`-`・`_` だけなら `transcript.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"` を返す。それ以外（空・`/` や `.` を含む）は `None` を返し、記録の保存先の外を指すパスを組まない
  - 副作用: 無し（ファイルを読まない）
- `lane_activity`
  - 事前条件: `row` は `ndf relay ls --activity` の 1 行（`None` なら一覧に無い）
  - 事後条件: 行の `turn_done` が真偽値なら `Activity(turn_done, moved, background)` を、ほか（行が無い・活動を読めなかった行）は `None` を返す
  - 副作用: 無し（行の値を写すだけで、ファイルも環境変数も読まない）
- `conductor_activity`
  - 事前条件: `row` はラッパーの状態の値で、`transcript`・`shell_outputs` はラッパーが子の `claude` に渡した環境（`CLAUDE_CONFIG_DIR` を含む）で組んで `wrapper.json` に書いた絶対パス。`directory` はラッパーの保存先。会話の記録と出力の保存先を、一覧を呼ぶ側の `os.environ`・`cwd` から組まない（耐久ワークフローと起動先の `CLAUDE_CONFIG_DIR` が違うレーンでも起動先の記録を読むため）
  - 事後条件: 返す `turn_done` は conductor の会話の記録の最後が終わった応答か。`activity.json` は、`session_id` が行の `session_id` と同じで、`reply` が conductor の会話の記録の最後の `assistant` の記録の `uuid` と同じときだけ使う（前の Stop の記録と今の応答を組み合わせない。時刻では比べない。`at` は秒で書かれるので、前の Stop と最新の応答が同じ秒に入ると区別できない）。`reply` と会話の記録の `uuid` のどちらかが無いときは確かめられないので使わない。使えないとき・`tasks` が `None` のときは `background` を `None` にする。`background` は次の和である
    - `type` が `subagent` の要素のうち、終わっていないもの。終わったサブエージェントとは、`subagent_transcript_path` が返すパスの記録を `observe.turn_done` で読んで `(True, t)` が返るものをいう。記録を読めないもの（パスを組めない・ファイルが無い・壊れている・応答の途中）は走っているとみなす
    - シェル: 行の `pid`（`row.get("pid")`）が整数なら `live_shells(pid, parents)` で数え、数が返ればその数（Stop の一覧に載っていないシェルも数える）。`pid` が無い・整数でない（`None` を含む）ときは `live_shells` を呼ばず、`live_shells` が `None` を返したときと同じに扱う。どちらも一覧の `shell` の数によらず `background` 全体を `None` にする（生死を確かめられないので止まりの根拠にしない。一覧が空でも、一覧に載っていないシェルが走っていることがある）
    - `subagent`・`shell` 以外の `type`（`monitor` など）の要素の数（走っているとみなす）
  - `moved` は、conductor の最後の応答の時刻と、除いた背景の作業の終わった時刻のうち最も遅いものである。終わったサブエージェントはその記録の最後の応答の時刻 `t`、終わったシェル（一覧に載り、`live_shells` が数を返し、生きているシェルより一覧のシェルが多いとき）は一覧のシェルごとの `observe.shell_output_in(Path(shell_outputs), id)`（ラッパーが `wrapper.json` に書いた絶対パスの下。`shell_output_path` で `environ`・`cwd` から組み直さない）の更新時刻の最大を使う。終わったシェルの出力のファイルを 1 つも読めないときは、終わった時刻を確かめられないので、同じセッション・同じ Stop の記録（`activity.json` の `at`）・同じ終わったシェルの数について終わりを最初に確かめた時刻を使う。初めて確かめた読み直しでは `now`（読み直しの時刻）をラッパーの保存先の `shells-ended.json` に書いてそれを使い、次からはその時刻を読む（読み直すたびに `now` にすると無活動の時間が毎回 0 に戻り、止まりにならない）。書けなければ `now` を使う。シェルの生死を確かめられず `background` が `None` になるときも、`moved` には手順 4 で集めた終わったサブエージェントの時刻を重ねる（`activity.json` を使えないときは除いた作業が無いので、conductor の最後の応答の時刻になる）。conductor の会話の記録を読めなければ `turn_done`・`moved`・`background` をどれも `None` にして返す
  - 副作用: ファイルの読み取り（`activity.json`・conductor の会話の記録の末尾・`type` が `subagent` の要素ごとにその会話の記録の末尾・`/proc` の conductor の子の `cmdline`・終わったシェルの出力のファイルの更新時刻）。会話の記録はどれも末尾だけを読む（`limits.json` の `hook.transcript-tail` の `count` バイト。決定 1 の代償）
- `live_shells`
  - 事前条件: `wrapper_pid` はラッパーの状態ファイルの `pid`（`wrapper.py:961`）。`claude` はラッパーが擬似端末で起動した直接の子である（`wrapper.py:236-253`）
  - 事前条件（続き）: `parents` は同じ読み直しの `process_parents` の返り。`process_parents` は走査の間に終わったプロセス（`FileNotFoundError`・`ProcessLookupError`）と `stat` を読めないプロセス（`hidepid=1` の他のユーザーのもの。`PermissionError`）を載せずに続ける
  - 事後条件: `parents` からラッパーの子の `claude` を、そのまた子から `cmdline` が Bash の Tool のシェルの形のものを数える（`task/*/children` は読む間に子が終わると生きている子も欠け得るので使わない）。`cmdline` を読む前か読む間に子が終わった（`FileNotFoundError`・`ESRCH` の `ProcessLookupError`）ときはその子を数えず、他の読み取りの失敗（`OSError`）は `None` にして呼び手へ例外を上げない。応答が終わった後（`turn_done` が真）に生きている Bash の Tool のシェルは背景のシェルだけなので、判定は `turn_done` が真のときだけこの数を使う
  - 副作用: `/proc` の読み取りだけ。シグナルを送らない

### 異常系の処理

活動を観測するのは `observe.conductor_activity` で、どの失敗でも例外を上げずに `{"turn_done": …, "moved": …, "background": …}` の辞書を返す（`hub.listing` がこの辞書を一覧の行へ展開する）。`run.lane_activity` は一覧の行の値を写すだけで、ファイルを読まない。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `lane_activity` | ラッパーが一覧に無い | レーンが動いていない | `row` が `None` | 既定値で続ける（`None` を返す） | 判定は順 7・順 8（`working`・`ended`）へ進む | 後始末は無い |
| `lane_activity` | 一覧の行に活動が載っていない | 止まっている行・`conductor_activity` が 3 つとも `None` の辞書（`NO_ACTIVITY`）を返した行 | 行の `turn_done` が真偽値でない | 既定値で続ける（`None` を返す） | 判定は `alive`（決定 1 の「読めないとき」） | 次の読み直しで読める |
| `conductor_activity` | `session_id`・`transcript`・`shell_outputs` が無い | 起動の途中・旧いラッパー | `row` の値の型（文字列でない） | 既定値で続ける（`NO_ACTIVITY` を返す） | `ndf relay ls --activity` の行の 3 つが `null`。判定は `alive` | 次の読み直しで読める |
| `conductor_activity` | 会話の記録を読めない・壊れている | ファイルが無い・途中で切れている | 手順 2 の読み取りの返り（`observe.turn_done` と同じく `None`） | 既定値で続ける（`NO_ACTIVITY` を返す） | 行の 3 つが `null`。判定は `alive` | 次の読み直しで読める |
| `conductor_activity` | 活動の記録が無い | hook が呼ばれない実行（中継を使わない conductor） | `signals.read_activity` の返り（`None`） | 既定値で続ける（`background` が `None` の辞書を返す） | 行の `background` が `null`。判定は `alive`（決定 2 の代償） | 後始末は無い |
| `conductor_activity` | 活動の記録が今の応答のものと確かめられない | 別のセッションの記録（切り替えの後に書けなかった）・`reply` が最後の `assistant` の記録の `uuid` と違う（今の Stop で書けなかった・hook が走った時点で最後の応答が記録に無かった）・どちらかの `uuid` が無い | `session_id` と `reply` の照合 | 既定値で続ける（`background` が `None` の辞書を返す） | 行の `background` が `null`。判定は `alive`。前の Stop の空の一覧を根拠に止まりとしない（同じ秒に書かれた前の Stop の記録も含む） | 次の Stop が書き直せば読める |
| `conductor_activity` | 背景の作業を観測できなかった記録 | Stop の入力に `background_tasks` が無い・型が違う（`tasks` が `null`） | `read_activity` の返りの `tasks` | 既定値で続ける（`background` が `None` の辞書を返す） | 行の `background` が `null`。判定は `alive` | 後始末は無い |
| `conductor_activity` | シェルの生死を確かめられない | `/proc` が無い・行に `pid` が無い・ラッパーの子に `claude` が無い・`cmdline` を読めない | `live_shells` の返り（`None`） | 既定値で続ける（一覧の `shell` の数によらず `background` が `None` の辞書を返す。一覧が空でも 0 にしない） | 行の `background` が `null`。判定は `alive`（今までの代償へ戻る。Stop の一覧に載っていないシェルが走っていても止まりとしない） | 後始末は無い |
| `conductor_activity` | 終わったシェルの終わった時刻が分からない | 出力のファイルが無い・読めない | `observe.shell_output_in` の返り・`stat` の失敗 | 既定値で続ける（終わりを最初に確かめた時刻を `shells-ended.json` に残し、`moved` にする。初めての読み直しでは `now`） | 最初に確かめた読み直しでは止まりにせず、その時刻から閾値が過ぎた後の読み直しで止まりになる。`shells-ended.json` を書けなければ毎回 `now` で、止まりにならない | `shells-ended.json` は次に違うキー（セッション・Stop の記録・終わったシェルの数）で書くときに置き換える |
| `conductor_activity` | サブエージェントの会話の記録を読めない | `id` がファイル名に使えない形・記録のファイルが無い・壊れている・`id` と記録の名前が一致しない（設計文書の「未確認のまま残ること」） | `subagent_transcript_path` の返り（`None`）・`observe.turn_done` の返り（`None`） | 既定値で続ける（その要素を「走っている」として数える） | 判定は `alive`（止まりと知らせる側へ倒さない） | 次の読み直しで読めれば除かれる |
| `conductor_activity` | サブエージェントが応答の途中 | 背景のサブエージェントがまだ動いている | `observe.turn_done` の返り（`(False, None)`） | その要素を「走っている」として数える（正常） | 判定は `alive` | 後始末は無い |
| `listing` | ラッパーの名前の形が違う | 一覧の行の名前が壊れている（`RegistryError`） | `registry.wrapper_dir`（状態を読む前） | 今までどおり `RegistryError` を上げる（この変更で増やさない。`conductor_activity` は呼ばれない） | 今までどおり | 後始末は無い |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `conductor_activity` | `session_id` と `transcript`・`shell_outputs` がある・会話の記録を読める・最後の応答が終わっている・今の応答の活動の記録がある | `{"turn_done": True, "moved": …, "background": <数>}`。`background` は次の 3 行で数える |
| `conductor_activity` | `tasks` の要素の `type` が `subagent`・その会話の記録の最後の応答が終わっている | その要素を数えない |
| `conductor_activity` | `tasks` の要素の `type` が `subagent`・その会話の記録を読めない・応答の途中 | その要素を数える |
| `conductor_activity` | シェル・`live_shells` が数を返す | 一覧によらずその数を数える。一覧のシェルより少なければ、終わったシェルの出力のファイルの更新時刻を `moved` に重ねる |
| `conductor_activity` | 行に `pid` が無い（`live_shells` を呼ばない）・`live_shells` が `None`（どちらも一覧の `shell` の数によらない） | `{"turn_done": True, "moved": …, "background": None}`。`moved` は手順 2 の時刻と手順 4 で集めた時刻の最大 |
| `conductor_activity` | `tasks` の要素の `type` が `subagent` でも `shell` でもない（`monitor` など） | その要素を数える |
| `conductor_activity` | `session_id`・`transcript`・`shell_outputs` のどれかが無い・conductor の会話の記録を読めない | `NO_ACTIVITY`（3 つとも `None`） |
| `conductor_activity` | 応答の途中・最後の応答の時刻が無い | `{"turn_done": <その値>, "moved": <手順 2 の時刻>, "background": None}`（活動の記録を読まない） |
| `conductor_activity` | 会話の記録は読めるが活動の記録が無い・別のセッション・`reply` が最後の応答の `uuid` と違う（どちらかが無い）・`tasks` が `null` | `{"turn_done": True, "moved": <手順 2 の時刻>, "background": None}` |
| `lane_activity` | 行が `None`・行の `turn_done` が真偽値でない | `None` |
| `lane_activity` | 行の `turn_done` が真偽値 | `Activity(row["turn_done"], row.get("moved"), row.get("background"))` |

手順は次の順である。

戻り値はどの手順でも `listing` が行へ展開する辞書 `{"turn_done": …, "moved": …, "background": …}` である（`None` も `Activity` も返さない。`Activity` へ写すのは `run.lane_activity`）。

1. `row` が `None`、または `session_id`・`transcript`・`shell_outputs`（ラッパーが `wrapper.json` に書いた絶対パス）が文字列でなければ、3 つとも `None` の辞書（`NO_ACTIVITY`）を返す
2. `Path(transcript)` を `observe.turn_done` と同じ読み方で読み、同じ 1 回の読み取りで最後の `assistant` の記録の `uuid`（`observe.reply_id` と同じ値）も得る（一覧の側の `os.environ`・`cwd` からパスを組まない）。`None` なら 3 つとも `None` の辞書（`NO_ACTIVITY`）を返す。`turn_done` が偽（応答の途中）か時刻が `None` なら、活動の記録を読まずに `background` が `None` の辞書で返す（`turn_done` はその値、`moved` は手順 2 の時刻）。手順 3 へ進むのは `turn_done` が真で時刻があるときだけで、時刻の無い比べは起きない
3. `registry.wrapper_dir` でラッパーの保存先を求め、`signals.read_activity` で活動の記録を読む。無い・`session_id` が行と違う・`reply` が手順 2 で読んだ最後の `assistant` の記録の `uuid` と違う（どちらかが無いときも違うとみなす）・`tasks` が `None` なら `{"turn_done": turn_done, "moved": moved, "background": None}` を返す
4. `tasks` の要素ごとに、`type` が `subagent` なら手順 2 のパスと要素の `id` から `subagent_transcript_path` でパスを組み、`observe.turn_done` が `(True, t)` を返す要素だけを除いて `t` を集める
5. 行の `pid`（`row.get("pid")`。`row["pid"]` で引かない）が整数なら `live_shells(pid, parents)` でシェルを数える。数が返り、一覧の `shell` の数より少なければ、一覧の `shell` ごとに `observe.shell_output_in(Path(shell_outputs), id)` の更新時刻を集める（1 つも読めなければ、終わりを最初に確かめた時刻を集める。初めてなら `now` を `shells-ended.json` に残して集める）。`pid` が無い・整数でないとき（`live_shells` を呼ばない）と `live_shells` が `None` を返したときは、一覧の `shell` の数によらず（空でも）シェルの生死を確かめられない印を立てて手順 6 へ進む（ここで return しない）
6. 手順 2 の時刻と手順 4・5 で集めた時刻の最大を `moved` にする。手順 5 で確かめられない印が立っていれば `{"turn_done": turn_done, "moved": moved, "background": None}` を、そうでなければ残りの数の和を `background` に入れた同じ形の辞書を返す

読むファイルはレーンごとに 2 つと、`type` が `subagent` の要素の数と、`/proc` の conductor の子の数（`cmdline`）と、終わったシェルの出力のファイルの数である。繰り返しはレーンの数（並列の上限は `relay.max-named` の 6）と各レーンの `tasks` の要素の数で終わる。会話の記録は末尾の決まった大きさだけを読むので、記録が伸びても読む量は増えない。`/proc/<pid>/stat` は見えているプロセスの数だけ、シェルの生死を確かめる行がある読み直しで 1 度だけ読む（レーンの数を掛けない）。活動を求めない `ndf relay ls`（`--activity` なし）はどれも読まない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `conductor_activity` | `observe.turn_done`・`observe.transcript_path`（`src/ndf/domains/relay/observe.py:196,55`） | `grep -rn "turn_done\|transcript_path" plugins/ndf/src/ndf/`（`wrapper_stop.py:265`・`wrapper_guard.py:104-105` が同じ組で使っている） | 使う。最後の応答が終わったかとその時刻を返す関数はこれだけで、ラッパーが同じ組み合わせで呼んでいる |
| `conductor_activity` | `observe.tool_pending`（`observe.py:185`） | 同上 | 使えない。Tool の実行の途中かを返すだけで、背景の作業が残っているかとは別（決定 2 の採らなかった案） |
| `subagent_transcript_path` | `context_report._subagents_dir`（`src/ndf/domains/relay/context_report.py:158-160`。会話の記録から `subagents` の保存先を組む） | `grep -rn "subagents" plugins/ndf/src/ndf/` | 使えない（名前が `_` で始まる `context_report.py` の中だけの関数で、`run.py` から読むと、生死の判定の材料が文脈の量の報告のモジュール（`records.usage` などを読む）に依存する）。同じ組み方（`.jsonl` を除いて `subagents` を足す）にそろえ、`observe` に置く。`context_report` をこの関数へ寄せるかは、この課題の範囲の外である |
| `subagent_transcript_path` | `observe.transcript_path`（`observe.py:55-57`） | 同上 | 使う（引数の `transcript` はこの関数の返り）。サブエージェントの記録はセッションの記録の隣にあるので、`environ`・`cwd` から組み直さない |
| `conductor_activity` | `observe.turn_done` をサブエージェントの会話の記録に使う | 手元の `~/.claude/projects/<鍵>/<session_id>/subagents/agent-<id>.jsonl` の形を見た（2026-11-23） | 使う。`turn_done` は `user`・`assistant` の記録だけを読むので、サブエージェントの記録にもそのまま当たる |
| `conductor_activity` | `signals.read_activity`（この設計で新設。`signals.py:331`）・`registry.wrapper_dir`（`registry.py:43`） | `grep -n "def wrapper_dir" -A 4 plugins/ndf/src/ndf/domains/relay/registry.py` | 使う |
| `lane_activity` | `launch.wrappers`（`src/ndf/progress/lane/launch.py:352`） | `grep -n "def wrappers" -A 6 plugins/ndf/src/ndf/progress/lane/launch.py` | 使う（`probes` が渡す一覧を使い回す）。1 回の読み直しで `ndf relay ls` を 2 度打たない |
| `Activity` | `liveness.Liveness`・`lane.LaneRow` | `grep -n "@dataclass" -A 3 plugins/ndf/src/ndf/progress/lane/liveness.py` | 使えない（持つ値が違う）。凍結した `dataclass` にする形だけそろえる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `conductor_activity` | 記録のそろったレーンで、返りの辞書の `turn_done` が真・`moved` が記録の時刻。`tasks` が `subagent` 1 件で、その会話の記録の最後の応答が終わっていれば `background` が 0 で、`moved` が conductor の最後の応答とサブエージェントの最後の応答のうち遅い方（lracct の現象の形。決定 2）。conductor とサブエージェントのどちらの記録も、`observe.turn_done` を差し替えずに実関数で読み、`hub.listing` からの呼び出しで `size` に `limits.json` の `hook.transcript-tail` の `count` が渡る | `tasks` が空で `background` が 0。`subagent` 1 件でその記録が応答の途中なら 1。`shell` 1 件で、差し替えた `/proc`（一時ディレクトリ）に生きているシェルが 1 つなら 1、0 なら 0 で `moved` が出力のファイルの更新時刻。`/proc` を読めなければ、一覧の `shell` の数によらず（空でも）`background` が `None`。一覧が空でも生きているシェルが 1 つなら 1。今の応答と同じ秒に書かれた前の Stop の記録（`reply` が違う）では `background` が `None` で、同じ秒でも `reply` が同じなら 0 | `session_id`・`transcript`・`shell_outputs` のどれかが無い・会話の記録が無いときは、例外を上げずに 3 つとも `None` の辞書。活動の記録が無いときは `background` が `None` の辞書。`subagent` の記録が無い・`id` が `../x` のときは、例外を上げずにその要素を数えて `background` が 1。`activity.json` の `session_id` が行と違う・`reply` が最後の応答の `uuid` と違う・`reply` が無い・`tasks` が `null` のとき、`background` が `None` |
| `lane_activity` | 一覧の行の `turn_done`・`moved`・`background` を `Activity` へ写す（`background` が `None` の行も写す） | — | 行が `None`・`turn_done` が `None` の行（活動を読めなかった行）で `None` |
| `reply_id` | 最後の `assistant` の記録の `uuid`（後ろに `tool_result` が続いても同じ） | — | `uuid` の無い記録・ファイルが無いとき `None` |
| `subagent_transcript_path` | `<d>/<sid>.jsonl` と `a1b2` から `<d>/<sid>/subagents/agent-a1b2.jsonl` | 英数字・`-`・`_` だけの `id` | 空の `id`・`/` か `.` を含む `id` で `None` |
| `live_shells` | 一時ディレクトリに組んだ `/proc` の形（ラッパー → `claude` → `bash -c source …/.claude/shell-snapshots/snapshot-…`）で 1。登録済みアカウントの `CLAUDE_CONFIG_DIR`（`…/.ndf/accounts/<id>/shell-snapshots/snapshot-…`）のシェルも数える | MCP のサーバーなどシェルでない子は数えない。子が無ければ 0。`cmdline` を読む間に終わった子（`ProcessLookupError`）は数えない | ラッパーの `pid` が一覧に無い・子に `claude` が無い・`parents` が `None`・`cmdline` を読めない（`PermissionError`）とき `None` |
| `process_parents` | 一時ディレクトリに組んだ `/proc` の `stat` から親 PID。comm に括弧や空白があっても読む | 数字でない項目は読まない。`stat` を読めない PID（`PermissionError`）は外して続ける | `/proc` が無ければ `None` |
| `shell_output_path` | `cwd`・`session_id`・`id` から `tasks/<id>.output` のパス | — | `id` が `../x` で `None` |

## 何もしていない conductor の判定（`idle_conductor`）

満たす決定: 決定 1、決定 3、決定 4、受け入れ条件 1、受け入れ条件 2、受け入れ条件 3、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `idle_after` | `plugins/ndf/src/ndf/progress/lane/liveness.py` | 新設 | `idle_after() -> float` | 何もしていないとみなすまでの秒（`contracts/limits.json` の `parallel-run.idle`） |
| `judge` | `plugins/ndf/src/ndf/progress/lane/liveness.py` | 変更 | `judge(row: Optional[LaneRow], sprint: str, wrapper: Optional[Mapping[str, Any]], listed: bool, activity: Optional[Activity] = None, now: Optional[float] = None) -> Liveness` | 順 5 と順 6 の間に新しい条件を置き、当たれば `idle_conductor` を返す |
| `judge_lanes` | `plugins/ndf/src/ndf/progress/lane/liveness.py` | 変更 | `judge_lanes(lanes: Mapping[str, list[LaneRow]], probes: Probes, now: Optional[float] = None) -> dict[str, Liveness]` | 判定に要るレーンだけ `probes.activity` を呼び、`judge` へ渡す |
| `Liveness` | `plugins/ndf/src/ndf/progress/lane/liveness.py` | 変更 | `moved_at: Optional[float] = None`・`background: Optional[int] = None` を足す | 判定が見た最後の応答の時刻とまだ走っているとみなす背景の作業の数を、知らせを組む側へ渡す |

- `idle_after`
  - 事前条件: 無し
  - 事後条件: `contracts/limits.json` の `parallel-run.idle` の `seconds` を返す。コードに秒の値を書かない（受け入れ条件 5）
  - 副作用: コントラクトの表を 1 度読む（`limits` を読む側が持つ）
- `judge`
  - 事前条件: `now` を省くと今の時刻（`time.time()`）を使う。テストは時刻を渡す（時間で区切った試験を行わない）
  - 事後条件: 返る `Liveness` の `kind` は 9 つのどれか。`idle_conductor` のときだけ `moved_at` と `background` が入る
  - 副作用: 無し（活動の材料は引数で受け取る。この関数は今までどおり DB もプロセスも読まない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `idle_after` | 閾値を読めない | `limits.json` が壊れている | `limits.seconds` | 呼び出し元へ返す（`contracts.ContractError`） | 呼び手のサブコマンドが `stopped`（`config_broken`） | 設定を直せば打ち直せる |
| `judge` | 活動を読めない | `activity` が `None`・`turn_done` が偽・`moved` が `None`・`background` が `None` | 新しい条件の判定 | 既定値で続ける（新しい条件に当たらず、今までどおり `alive`） | 止まりとして知らせない（決定 1・決定 2） | 後始末は無い |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `judge` | 順 1〜順 5 のどれかに当たる | 今までどおり（`idle`・`resuming`・`waiting_answer`・`unknown`・`stalled`） |
| `judge` | ラッパーが動いている・活動が読める・`turn_done` が真・`background` が 0・`now - moved >= idle_after()` | `idle_conductor`（止まり）。`background` が 0 には、`tasks` が空の場合と、背景の作業がすべて終わった場合の両方が入る。`moved` は除いた背景の作業の終わった時刻も含めた最も遅い時刻なので、長い背景の作業が終わった直後（完了の通知が conductor を起こす前）は閾値に届かない |
| `judge` | ラッパーが動いている・`background` が 1 以上 | `alive`（受け入れ条件 2）。`shell` など、終わったかを確かめられない背景の作業が残る行もここに入る |
| `judge` | ラッパーが動いている・`turn_done` が偽（応答の途中） | `alive` |
| `judge` | ラッパーが動いている・`now - moved < idle_after()` | `alive` |
| `judge` | ラッパーが動いていない | 今までどおり（`working`・`ended`） |

新しい条件を順 2（回答の後の `next` 待ち）と順 3（スプリントの待ちが `gate`）より後ろに置くので、承認ゲートと質問の回答を待つ行は今までどおり止まりにならない（受け入れ条件 3）。繰り返しはレーンの数で終わる。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `IDLE_CONDUCTOR` | 定数 | `plugins/ndf/src/ndf/progress/lane/liveness.py` | `"idle_conductor"`（判定の種類の並びに 9 つ目として足す） |
| `STALL_KINDS` | 定数 | `plugins/ndf/src/ndf/progress/lane/liveness.py` | `(STALLED, ENDED, IDLE_CONDUCTOR)` |
| `IDLE_POLICY` | 定数 | `plugins/ndf/src/ndf/progress/lane/liveness.py` | `"parallel-run.idle"`（`limits.json` の方針の名前） |
| `parallel-run.idle` | 設定値 | `plugins/ndf/contracts/limits.json` | `{"summary": "conductor が応答を終えて背景の作業を持たないまま、何もしていないとみなすまでの秒", "seconds": 600}` |

秒の唯一の保存先は `limits.json` で、`liveness.py` は方針の名前だけを持つ（受け入れ条件 5）。理由の文は「conductor が応答を終えてから \<分\> 分、背景の作業が無いまま動いていない」で、分は `now - moved` から組む。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `idle_after` | `ndf.contracts.limits.seconds`（`src/ndf/contracts/limits.py:85`） | `grep -n "^def seconds\|^def value" plugins/ndf/src/ndf/contracts/limits.py` | 使う。`progress/plan/run.py:100-107` が `parallel-run.poll`・`parallel-run.launch` を読むのと同じ関数 |
| `idle_after` | `run_decide.Policy`（閾値を引数で渡す形） | `grep -n "class Policy" -A 8 plugins/ndf/src/ndf/progress/plan/run_decide.py` | 使えない。計画の実行の方針を持つ型で、判定の閾値を持たない。設計文書の「解き方の全体像」が `limits.json` から判定へ直に渡す形を示しているので、判定の側で読む |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge` | 応答が終わり・背景 0 件・閾値を過ぎた行で `idle_conductor` になり、`stall` が真（受け入れ条件 1） | 閾値ちょうど（`now - moved == idle_after()`）で止まり、1 秒手前では `alive`。時刻はテストが渡す（時間で区切った試験を行わない） | 活動が `None`・`turn_done` が偽・`background` が 1 以上・`background` が `None` の 4 通りで `alive`（受け入れ条件 2・決定 1） |
| `judge` | スプリントの待ちが `gate` の行と、回答の後の `next` 待ちの行は、閾値を過ぎても止まりにならない（受け入れ条件 3） | ラッパーが `stalled` の行は今までどおり `stalled`（決定 4） | — |
| `judge`（`lane_activity` と組む） | `activity.json` の `tasks` が `subagent` 1 件で、その会話の記録の最後の応答が終わっている行は、サブエージェントの最後の応答から閾値を過ぎれば `idle_conductor`（決定 2。lracct の現象の形）。会話の記録は一時ディレクトリに置き、`now` はテストが渡す。`tasks` が `shell` 1 件で、差し替えた `/proc` に生きているシェルが無く、出力のファイルの更新時刻から閾値を過ぎた行も `idle_conductor`（受け入れ条件 1 のシェルの取りこぼし） | conductor の最後の応答が 30 分前・サブエージェントの最後の応答が 1 分前の行は、閾値が 10 分でも `alive`（長い背景の作業が終わった直後の誤りを防ぐ）。生きているシェルが 1 つある行は閾値を過ぎても `alive` | `subagent` 1 件でその会話の記録が無い行は `alive`（読めないサブエージェントを走っているとみなす）。前の Stop の空の一覧（`reply` が今の応答の `uuid` と違う。`at` が同じ秒でも）だけの行は閾値を過ぎても `alive` |
| `idle_after` | `limits.json` の値を返す | 設定を変えると判定の境目が変わる（受け入れ条件 5） | — |

## 止まりの知らせの中身（`lane_stalled`）

満たす決定: 決定 4、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_judge` | `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | `_judge(rows, state, found, moved, spent=()) -> Judged`（変わらない） | 止まりの材料に背景の作業の数を足し、`idle_conductor` では最後に動いた時刻を `Liveness.moved_at`（conductor の最後の応答と終わった背景の作業の時刻のうち最も遅いもの。UNIX 時刻のまま）にする |
| `Judged` | `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | `stalls: list[tuple[str, str, str, str \| float \| None, str, int \| None]]` | 止まりの組の型を 6 つに広げる |
| `Stall` | `plugins/ndf/src/ndf/progress/plan/run_decide.py` | 変更 | `moved: Optional[str \| float]` に広げ、`background: Optional[int] = None` を足す | スナップショットの止まり 1 つ。既定の `None` は、変更の前に書かれた 5 つの組のスナップショットを読み直しの途中で包むためである |
| `_stall_report` | `plugins/ndf/src/ndf/progress/plan/run_decide.py` | 変更 | `_stall_report(state: RunState, lane: str, key: str, reason: str, moved: Optional[str \| float], row: str, background: Optional[int] = None) -> Report` | `moved` が数（UNIX 時刻）なら ISO 8601 の文字列へ直し、リクエストの中身に `background` を足す |
| `with_stalls` | `plugins/ndf/src/ndf/progress/plan/run_decide.py` | 変更 | シグネチャは変わらない（`stalls: Sequence[Sequence[Any]]`） | 受ける止まりの組が 6 つになることを説明の文に書く |

- `_judge`
  - 事前条件: 変わらない
  - 事後条件: `Judged.stalls` の各要素が（レーン, 知らせのキー, 理由, 最後に動いた時刻, 行, 背景の作業の数）の 6 つになる。`idle_conductor` の行では、最後に動いた時刻が `Liveness.moved_at`（UNIX 時刻の数のまま。スナップショットは JSON なので数として残る）、背景の作業の数が 0 になる。ほかの判定では今までどおりステートマシンの記録の時刻（`last_moved` の ISO 8601 の文字列）と `None`
  - 副作用: 無し
- `_stall_report`
  - 事後条件: `moved` が数なら `datetime.fromtimestamp(moved, timezone.utc).isoformat()` で ISO 8601 の文字列へ直す（`last_moved` が返す記録の時刻と同じ UTC の形）。文字列と `None` はそのまま使う。本文の「最後に動いた時刻」と `Report.facts` の `("moved", …)` はどちらも直した後の文字列で、`("background", <数か None>)` が増える。`write_report`（`src/ndf/progress/plan/run.py:283-295`）が `facts` をそのままリクエストの中身へ入れるので、`lane_stalled` のリクエストに `background` が載る
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_judge` | 最後に動いた時刻が無い | 活動も記録も読めない | 組を作るところ | 既定値で続ける（`None`。本文は今までどおり「最後に動いた時刻 不明」） | リクエストの `moved` が `null` | 後始末は無い |
| `_stall_report` | 背景の作業の数が無い | `idle_conductor` 以外の止まり（活動を見ずに決まる判定） | 組の 6 つ目 | 既定値で続ける（`None` をそのまま載せる） | リクエストの `background` が `null` | 後始末は無い |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_stall_report` | `lane_stalled` のリクエストの中身に `moved`（最後に動いた時刻。ISO 8601 の文字列）・`launches`・`last_launch` と `background`（まだ走っているとみなす背景の作業の数） | 計画のステートマシンのリクエスト（`write_report`） | 会話の記録の中身。活動の記録のパス |
| `judge` | 判定の理由の文（`Liveness.reason`）と、`ndf lanes status` の行の `liveness`（`as_dict` に `moved_at`・`background` が増える） | 結果 JSON の `items` と `warnings` | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| （新設する関数は無し。変える関数と型だけ） | `Report.facts`（`src/ndf/progress/plan/run_decide.py:129-137`） | `grep -n "class Report" -A 10 plugins/ndf/src/ndf/progress/plan/run_decide.py` | 使う。リクエストの中身へ項目を足す仕組みが既にあり、`write_report` が `facts` をそのまま入れる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_judge` | `idle_conductor` の行の組に、`Liveness.moved_at`（UNIX 時刻）と `0` が入る | ほかの止まり（`stalled`・`ended`）の行では今までどおりの ISO 8601 の時刻と `None` | 時刻が読めない行で `None` |
| `_stall_report` | `moved` に UNIX 時刻 `0.0` を渡すと、`lane_stalled` のリクエストの `moved` が `"1970-01-01T00:00:00+00:00"`・`background` が `0` になる（受け入れ条件 4） | `moved` が ISO 8601 の文字列ならそのまま載る。`background` が `None` の止まりでも書ける | 5 つの組のスナップショットを `Stall` で包んでも例外を上げず、`background` が `None` |

## 判定の値が増えることの当たり先

満たす決定: 決定 4

判定の値を 8 から 9 に増やすため、値ごとに分岐する既存の規則を 1 つずつ見る（設計文書の「危険」）。検索で集めた当たり先は次のとおりで、当てはまらない規則は実装で変える。

| 当たり先 | 検索の語とコマンド | 見た結果 |
| --- | --- | --- |
| `run.py:165` の `/goal` の入れ直し（`v.kind == liveness.STALLED`） | `grep -rn "liveness\.\(STALLED\|ENDED\|ALIVE\|UNKNOWN\)" plugins/ndf/src/ndf/` | `stalled` だけを見る条件で、`idle_conductor` は再起動の対象にしない（ラッパーは動いているため）。変えない |
| `run.py:168` の `unknown` の集計 | 同上 | `unknown` だけを見る。変えない |
| `liveness.STALL_KINDS` を読む側（`Liveness.stall`・`run.py` の `_judge`・`plan/__init__.py` の `_stall_warnings`） | `grep -rn "STALL_KINDS\|\.stall\b" plugins/ndf/src/ndf/progress/` | 値の名前を書かずに `STALL_KINDS` で判定するので、定数に足すだけで当たる |
| 知らせのキー（`liveness.notice_key`） | `grep -n "def notice_key" -A 3 plugins/ndf/src/ndf/progress/lane/liveness.py` | キーに判定の種類を含むので、`stalled` の知らせと `idle_conductor` の知らせが別のキーになる。変えない |
| リクエストを書く側（`run.write_report`・`NOTICE_FIELDS`） | `grep -n "NOTICE_FIELDS\|def write_report" plugins/ndf/src/ndf/progress/plan/run.py` | リクエストの種類は `lane_stalled` のままで、判定の種類は理由の文に入る。変えない |
| `ndf lanes status`（`plan/__init__.py:462` の `judge_lanes`） | `grep -rn "judge_lanes" plugins/ndf/src/ndf/` | 判定の値で分岐せず `as_dict()` を行に載せるだけ。変えない |
| `ndf lanes status` のサブコマンド（`plan/cli.py:103` の `_cmd_status`） | `grep -rn "probes(" plugins/ndf/src/ndf/progress/plan/cli.py` | `run.probes(where.repo, a.plan)` を渡すだけなので、活動を読む項目を `run.probes` に足せば当たる。`judge_lanes` の `now` は省いたときに今の時刻にするので、ここも変えない。実装では、新しい判定が出たレーンで `ndf lanes status` の行に `liveness.kind` が `idle_conductor` と出ることを 1 回確かめる |
