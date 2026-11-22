# #4050 の詳細設計: 実装の待ち・空いた時間にやることの一覧・割り当て

設計文書 `issues/issue-4050-design.md` の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の detailed-design.md にある。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── workflow/progress.json              # 事象 row_held・row_implementing・row_drift_reviewed・lane_held・lane_row_ended・idle_listed・idle_adjusted、lane_added の書き手
│   └── schemas/io/lanes.args.schema.json   # ndf lanes idle の引数
├── docs/contracts/commands.md              # ndf lanes idle・status・wait、ndf progress next の結果
├── src/ndf/progress/
│   ├── model.py                            # design_issues*（設計文書と設計ブランチの番号）
│   ├── run.py                              # Env.hold・Env.peek・_start・wait と next の手の組み立て・--drift-reviewed
│   ├── gate/answering.py                   # lane_env
│   ├── lane/
│   │   ├── types.py                        # LaneRow の held_seq・held・implementing_seq・drift・drift_reviewed・holding
│   │   ├── fold.py                         # row_held・row_implementing・row_drift_reviewed の畳み込み、idle_* の畳み込み
│   │   ├── rules.py                        # next_row・implementation_wait*・_order_graph（実装の開始で辺を切り替える）
│   │   ├── __init__.py                     # begin_implementation*・review_drift*・drift*・end_row（lane_row_ended）
│   │   ├── machine.py                      # count（実装の待ちと承認の待ちの行を数えない）
│   │   ├── liveness.py                     # HELD
│   │   └── launch.py                       # 起動し直しの入力（待ちの解け）
│   └── plan/
│       ├── __init__.py                     # _row_item・_lane_items・_plan_metrics、add_lane の書き手
│       ├── idle.py*                        # refresh*・adjust*・current*・show*
│       ├── run.py                          # _halts・snapshot_of・assign_idle*（耐久ステップ）
│       ├── run_decide.py                   # Snapshot の released・awaiting・idle・free、RunState.assign_failed、_launches・_after_observe・Assign*
│       ├── wait_decide.py                  # NOTICE_KINDS
│       └── cli.py                          # ndf lanes idle・caller_actor*
└── tests/progress/                         # test_lane・test_lane_machine・test_plan・test_plan_run・test_plan_wait・test_run_hold*・test_plan_idle*
```

依存の向き: `run.py`（スプリントの進行）は `Env` の口だけを持ち、`lane` を import しない。`gate/answering.py` が `lane` と `run` をつなぐ（今の `ended` と同じ）。`plan/idle.py` は `lane`（計画の読み書き）・`lane.machine`（空きの数）・`ndf.adapters.github`（作り直しの読み取り）に依存し、`plan/run.py` は `plan/idle.py` の `current` を読み、`plan/__init__.add_lane` で書く。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/progress.json` | 変更 | 事象の定義に 7 つ足し、`lane_added` の書き手に `workflow` を足す | — | — | — |
| `src/ndf/progress/model.py` | 変更 | 設計文書の名前と設計ブランチの head から課題の番号を取り出す | — | — | `design_issues`（新設） |
| `src/ndf/progress/run.py` | 変更 | 最初の `implement` の工程の手前で `Env.hold` を呼び、`work`・`wait`・`next` のどれでも待ちと確認を返す | `stages` | `Env` | `Env.hold`・`Env.peek`（新設の欄）、`_start`・`_drive`・`wait`・`report`（変更）、`_hold_report`・`_drift_report`（新設） |
| `src/ndf/progress/gate/answering.py` | 変更 | `Env.hold`・`Env.peek` を取得・実装の開始の判定と記録につなぐ | `lane`・`run`・`ndf.adapters.git` | — | `lane_env`（変更） |
| `src/ndf/progress/lane/types.py` | 変更 | 行の実装の待ちと実装の開始を持つ | — | `LaneRow` | `held_seq`・`held_at`・`held`・`implementing_seq`・`drift`・`drift_reviewed`（欄）、`holding`・`implementing`（新設） |
| `src/ndf/progress/lane/fold.py` | 変更 | `row_held`・`row_implementing`・`row_drift_reviewed` と `idle_*` を畳み込む | `types` | — | レーンの畳み込み（変更）、`fold_idle`（新設） |
| `src/ndf/progress/lane/rules.py` | 変更 | スプリントの行は `after` を見ずに始め、実装の手前で `after` と未確定の `touches` を照らす。I13 のグラフの辺を実装の開始で切り替える | `types` | — | `next_row`・`_order_graph`（変更）、`implementation_wait`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | 実装の開始を判定して待ちか開始を書き、変わったファイルを求め、行の終わりを計画へ知らせる | `rules`・`model`・`ndf.adapters.git` | — | `begin_implementation`・`review_drift`・`drift`（新設）、`end_row`（変更） |
| `src/ndf/progress/lane/machine.py` | 変更 | 実装の待ちの行と承認の待ちの行のレーンを数えない | `types` | — | `count` に渡す `PlanLanes.lanes` の作り方（変更） |
| `src/ndf/progress/lane/liveness.py` | 変更 | 実装の待ちの行を `held` と判定する。承認の待ちは今の `waiting_answer` のうちスプリントの待ちが `gate` のものを使う | `types` | — | 判定（変更）、`HELD`（定数）、`gate_wait`（新設） |
| `src/ndf/progress/plan/idle.py` | 新設 | 一覧の作り直し・調整・読み | `lane`・`machine`・`github` | — | `refresh`・`adjust`・`current`・`show`・`order` |
| `src/ndf/progress/plan/run.py` | 変更 | 写しに待ちと一覧を載せ、割り当ての耐久ステップを持つ | `idle`・`plan` | — | `_halts`（変更）、`snapshot_of`（変更）、`assign_idle`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 待ちの起動し直し・承認の待ちのラッパーを止める手・割り当ての手を足し、一覧が残る間は完了にしない | — | `Snapshot`・`RunState`・`Assign`（新設） | `_launches`・`_after_observe`・`decide`（変更）、`_assignable`（新設） |
| `src/ndf/progress/plan/wait_decide.py` | 変更 | `lane_held`・`lane_row_ended` を知らせにする | — | — | `NOTICE_KINDS`（変更） |
| `src/ndf/progress/plan/__init__.py` | 変更 | 表示に `touches`・`held`・一覧を載せ、`add_lane` に書き手を渡せるようにする | `lane`・`idle` | — | `_row_item`・`_lane_items`・`_plan_metrics`・`add_lane`（変更） |
| `src/ndf/progress/plan/cli.py` | 変更 | `ndf lanes idle refresh / set / show` と呼び出し元の役割 | `idle` | — | `_cmd_idle`・`caller_actor`（新設） |

## 実装の待ちの判定と記録（`rules`・`lane`）

満たす決定: 決定 2・決定 3・決定 6・決定 8、受け入れ条件 1・2・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `implementation_wait` | `lane/rules.py` | 新設 | `(view: PlanView, lanes: Lanes, lane: str) -> list[str]` | レーンの `after` のうち `after_met` が偽の値を、計画の並びの順で返す。レーンが `idle` を持ち `touches` が空なら、末尾に印 `TOUCHES_UNSET` を足す |
| `next_row` | `lane/rules.py` | 変更 | 変えない | 行の入力が `kind: sprint` なら `waiting_after` を常に空にする。`loop` は今のとおり |
| `_order_graph` | `lane/rules.py` | 変更 | 変えない | レーン全体を指す `after` の辺を切り替える「後続が始まった」を、スプリントの行のレーンでは「先頭の行が実装の開始を記録した（`implementing`）」で読む。開発ループの行のレーンは今のとおり行の状態で読む |
| `begin_implementation` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, stage: str, base: BaseRef) -> Begin` | 下の「手順」で、待ちか実装の開始のどちらかを、計画の排他の中で 1 回で決めて書く |
| `review_drift` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, reason: str) -> None` | 実装の開始を記録し `drift` が空でない行に `row_drift_reviewed` を書く。記録が無い・`drift` が空・既に書いた行は書かない |
| `drift` | `lane/__init__.py` | 新設 | `(git: Git, ref: str, base_sha: str, touches: Sequence[str]) -> Optional[list[str]]` | `git diff --name-only <base_sha>..<ref>` の各パスのうち、`touches` のどれかと `touch_hit` で当たるものを昇順で返す。読めなければ None |
| `end_row` | `lane/__init__.py` | 変更 | 変えない | `row_ended` と同じトランザクションで、計画に `lane_row_ended`（`lane`・`row`・`outcome`）を書く |
| `LaneRow.holding` | `lane/types.py` | 新設 | `@property -> bool` | `state == RUNNING and held_seq > 0 and implementing_seq == 0` |
| `LaneRow.implementing` | `lane/types.py` | 新設 | `@property -> bool` | `implementing_seq > 0` |

`BaseRef` は（`remote`・`branch`・`ref`（`<remote>/<branch>`））で、呼び手（`lane_env`）が `Git.remote()` とブランチ戦略の設定から組む。`origin` を決め打ちしない。

`begin_implementation` の手順:

1. 計画の排他の外で、行とレーンを読む（`sprint_row`）。属さなければ `Begin(outside=True)`（計画の外のスプリント。何も書かない）。行が既に `implementing` なら、書かずに今の記録を返す（`drift` が空でなく `drift_reviewed` が偽なら確認待ち）
2. 計画の排他の外で、Git を読むかを決める。レーンの `after` が空でないか、`touches` が未確定か、行が `holding` なら読む。読むときは `git fetch <remote> <branch>` を 1 回打ってから `git rev-parse <ref>` を打つ（読み取りの前に取得し、古い追跡参照を使わない）。行が `holding` なら、続けて `drift(git, ref, held.base_sha, touches)` を求める。読まない行（`after` が空・`touches` が確定・待ったことが無い）は Git を 1 回も打たない
3. トランザクションを始め、`lock_plan` を取り、最新の計画とレーンを読み直して `implementation_wait` を求める。排他の中では Git を読まない
4. 空でなければ: 行が `holding` なら書かずに今の待ちを返す。そうでなく 2 で Git を読んでいれば、`row_held`（`ref`・`base_sha` は 2 の値）と `lane_held` を `checked_write` で書いて新しい待ちを返す。2 で Git を読んでいなければ（2 と 3 の間に `lanes set` が `after` を足した）、書かずにトランザクションを抜けて 1 から 1 回だけやり直す。2 回目も同じなら `stopped`・`precondition` で止める
5. 空なら: `row_implementing`（`stage`。行が `holding` なら `drift` と `base_sha` も）を `checked_write` で書き、`Begin(started=True, drift=…)` を返す。`checked_write` は I13 のグラフを、書いた後の「実装の開始」で組み直して確かめる

- 事前条件: 呼び手は `store.run_lock`（スプリントごと）を持っている
- 事後条件: 待ちを返したとき、行の `holding` が真で、`row_held` は 1 件だけある（I2）。開始を返したとき、`row_implementing` は 1 件だけあり、書いた時点の最新の計画で `implementation_wait` が空だった（I9）
- 副作用: 計画とレーンのステートマシンへの書き込み（同じトランザクション）。`git fetch` と `git rev-parse` を高々 1 回ずつ、`git diff` を高々 1 回（すべて排他の外）

計画の排他を取るほかの書き込み（`lanes set`・`lane add`・`row add`・割り当て）は、どれも今の `checked_write` か `lock_plan` を通る。手順 3〜5 が 1 つのトランザクションなので、これらは `row_implementing` の前に入って待ちを生むか、後に入って実装を始めた行を含むグラフで I13 の検査を受けるかのどちらかになる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `begin_implementation` | 計画の決まりで断られる | 待ちか開始の書き込みが I13 などに当たる | `checked_write` | 呼び出し元へ返す | `next` が `stopped`・`precondition` と断った理由 | 書かない。直して `next` を打ち直せる |
| `begin_implementation` | 取得か参照の読み取りに失敗する | ネットワーク・認証・基のブランチが無い・リモート名の設定の誤り（`RemoteError`） | 手順 2 の `git fetch`・`git rev-parse` の終了コード | 待ちも開始も書かずに止める（古い参照で `base_sha` を書かない） | `next` が `stopped`・`work_stopped`、`summary` に打ったコマンドと理由 | 打ち直せる |
| `begin_implementation` | 排他の間に依存が増え、Git を読んでいない | 手順 2 と 3 の間の `lanes set` | 手順 4 | 1 回だけやり直す | 2 回目も同じなら `stopped`・`precondition` | 打ち直せる |
| `drift` | 差分を読めない | `base_sha` が取得した履歴に無い（強制 push など） | `git diff` の終了コード | 既定値で続ける | `drift` を null にし、`warnings` に理由。null は空と区別し、確認の結果を返す（判断の記録を求める） | 工程は判断の記録の後に始める |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Hold` | 型（`waiting`・`stage`・`ref`・`base_sha`・`seq`・`new: bool`） | `lane/types.py` | — |
| `Begin` | 型（`outside`・`held: Optional[Hold]`・`started`・`drift: Optional[list[str]]`・`review: bool`） | `lane/types.py` | — |
| `BaseRef` | 型（`remote`・`branch`・`ref`） | `lane/types.py` | — |
| `ROW_HELD`・`ROW_IMPLEMENTING`・`ROW_DRIFT_REVIEWED`・`LANE_HELD`・`LANE_ROW_ENDED` | 定数（事象の種類） | `lane/types.py` | `"row_held"`・`"row_implementing"`・`"row_drift_reviewed"`・`"lane_held"`・`"lane_row_ended"` |
| `TOUCHES_UNSET` | 定数（待つ先の印） | `lane/rules.py` | `"touches"`（レーン名と重ならないよう、レーン名の規則が許さない形にする。実装で `:touches` など規則の外の字を使う） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じスプリントの `next`・`work` を 2 本 | スプリントのステートマシン・レーンの行 | 今の `store.run_lock`（スプリントごと）の中で `begin_implementation` を呼ぶ | 待ちなら書かずに今の待ちを、開始済みなら今の記録を返す |
| `begin_implementation` と `lanes set`・`lane add`・`row add`・割り当て | 計画とレーンのステートマシン | 手順 3〜5 を 1 つのトランザクションにし、`lock_plan` の中で依存の確かめと書き込みを行う。Git の読み取りは排他の外（手順 2）だけ | 開始の前に落ちれば何も書かれず、待ちの行のまま起動し直しの候補に残る |
| `begin_implementation` と耐久ワークフローの読み直し | 計画とレーンのステートマシン | 今の `lock_plan` | 読み直しは書いた後の状態を次の周で読む |

### 単体テストの観点

- `implementation_wait`: 行を指す `after`・レーンを指す `after`・行の無いレーンを指す `after`（満たさない）の 3 通り。`idle` を持ち `touches` が空のレーンは `after` を満たしても印を返す
- `next_row`: スプリントの行は `after` があっても `waiting_after` が空、開発ループの行は今のとおり
- `_order_graph`: A と B が同じ場所を触り `B.after=[A]`、両方が未着手のとき、B の `row start` も、続く A の `row start` も I13 で断られない。B が実装を始めた後に A へ行を足しても、始めた時点で終わっていた行からだけ辺を引く（今のテストが変えずに通る）
- `begin_implementation`: 1 回目は待ちを書き、2 回目は書かない。依存が解けた後は `row_implementing` を 1 回だけ書き、`row_resumed` だけでは待ちのまま。手順 2 と 3 の間に `lanes set` で満たしていない `after` を足すと開始を書かない。`after` が空の行では Git の呼び出しが 0 回。取得の失敗で何も書かない。計画の外のスプリントは `outside`
- `drift`: `touches` に当たるファイルだけを返す。`base_sha` が無いと null と warnings。待ち始める前に追跡参照が古かった作業場所でも、取得した後の参照が `base_sha` になる。リモート名が `upstream` の作業場所で同じ結果
- `end_row`: `row_ended` と `lane_row_ended` が同じトランザクションで 1 件ずつ

## 工程を始める口（`run`・`answering`）

満たす決定: 決定 2・決定 6、受け入れ条件 2・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Env.hold` | `run.py` | 新設の欄 | `Callable[[Any, str, stages.StageDef, Path], Optional[HoldResult]]`（既定は常に None） | 工程を始める前に呼ぶ口。待ちなら `HoldResult(held=…)`、開始を記録して確認が要れば `HoldResult(review=True, drift=…)`、開始を記録して続けてよければ `HoldResult(drift=…)`（待たなかった行は `drift=None`） |
| `Env.peek` | `run.py` | 新設の欄 | `Callable[[Any, str], Optional[HoldResult]]`（既定は常に None） | 書かずに今の待ちか確認待ちを読む口。`wait` と `next` の手の組み立てが使う |
| `_start` | `run.py` | 変更 | 変えない | 工程のフェーズが `implement` で、これより前の `implement` の工程が 1 つも始まっていなければ `Env.hold` を呼ぶ。`held` なら工程を始めずに `_hold_report` を、`review` なら工程を始めずに `_drift_report` を返す。どちらでもなければ工程を始め、`drift` を結果の `metrics.drift` に載せる |
| `_drive` | `run.py` | 変更 | 変えない | `inline`（`ndf progress work`）で工程をまたいで流すとき、`_start` が `_hold_report`・`_drift_report` を返したら流しを止め、その結果を返す（`work.log` に残すだけにせず、スプリントの作業の結果として記録する） |
| `report`・`wait` | `run.py` | 変更 | 変えない | 次の手が最初の `implement` の工程の開始で、`Env.peek` が待ちか確認待ちを返せば、通常の次手（工程の起動）の代わりに `_hold_report`・`_drift_report` を返す。`next` を打ち直す手を作らない |
| `_cmd_next` | `run.py` | 変更 | `--drift-reviewed <理由>` を足す | 引数があれば、`Env` の口から `lane.review_drift` を呼んでから、今のとおり工程を始める |
| `_hold_report` | `run.py` | 新設 | `(v: View, s: StageDef, held: Hold) -> dict` | 入出力のコントラクトの形の結果（`next` を持たない） |
| `_drift_report` | `run.py` | 新設 | `(v: View, s: StageDef, drift: Optional[list[str]]) -> dict` | 入出力のコントラクトの確認の形の結果（`next` は `--drift-reviewed` 付きの `next`） |
| `lane_env` | `gate/answering.py` | 変更 | 変えない | `hold` に、`BaseRef`（`Git.remote()` とブランチ戦略の設定）を組んで `lane.begin_implementation` を呼ぶ関数を入れる。`peek` に、行を読むだけの関数を入れる。`--drift-reviewed` の口に `lane.review_drift` をつなぐ |

- `_start` の「これより前の `implement` の工程が始まっていない」は、スナップショットの工程の並びで、この工程より前のフェーズ `implement` の行がどれも `pending` か `skipped` であることで決める（`light` で最初の工程が `implement` のときも同じ規則で当たる）
- `Env.peek` は DB だけを読み、Git も GitHub も読まない。`wait` の回数あたりの読み取りは、レーンの行の 1 回の読み取りだけ増える（計画の外のスプリントでは増えない）

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `lane_env` の `hold` | 計画の外のスプリント | None（今と同じ） |
| 同上 | `implementation_wait` が空・待ったことが無い | `row_implementing` を書き、`HoldResult(drift=None)` |
| 同上 | `implementation_wait` が空・待ちの行（`holding`）・変わったファイルが空 | `row_implementing` を書き、`HoldResult(drift=[])` |
| 同上 | `implementation_wait` が空・待ちの行・変わったファイルが空でないか読めない | `row_implementing` を書き、`HoldResult(review=True, drift=…)`。工程は始めない |
| 同上 | 開始を記録済み・確認待ち（`drift` が空でなく `drift_reviewed` が偽） | 書かずに `HoldResult(review=True, drift=…)` |
| 同上 | 開始を記録済み・確認済みか確認が要らない | 書かずに `HoldResult(drift=記録の値)` |
| 同上 | `implementation_wait` が空でない | `HoldResult(held=…)`（待ちの行なら書かずに今の待ち） |

待ちが解けたことは `row_implementing` だけで読むので、起動し直しの `row resumed` → `next`、ラッパーの conductor がそのまま打つ `next`、オーガナイザーが打つ `next`、`work` のどの経路でも同じ表を通り、`drift` が届く。

### 単体テストの観点

- `standard` のスプリントで「計画」の手前で待ち、「要求と受け入れ条件」〜「ドキュメントレビュー」は待たない
- `light` のスプリントで最初の工程の手前で待つ。`drift` が空でないときは、工程の作業を起動しない（差し替えた作業の起動の呼び出しが 0 回）まま確認の結果を返し、`--drift-reviewed` の後に起動する
- 設計の工程で `ndf progress work` を起動した worker が「計画」の手前まで進むと止まり、`ndf progress wait` が `held` を `next` 無しで返す（`work.log` を読まずに分かる）
- `row resumed` の後、`next` の前に止めたスプリントで、次の `next` が `drift` を返す。ラッパーの conductor が起動し直しより先に `next` を打っても `drift` が返る
- `Env.hold` の既定（計画の外）で工程の順が今と同じ

## マシンの数と生死の判定（`machine`・`liveness`）

満たす決定: 決定 4・決定 11、受け入れ条件 3、I3・I8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PlanLanes.lanes` を作る関数 | `lane/machine.py` | 変更 | 変えない | レーンの実行中の行が `holding` か、承認の待ち（`gate_wait`）でラッパーを止めた行なら、そのレーンを「実行中の行を持つ」に数えない |
| 生死の判定 | `lane/liveness.py` | 変更 | 変えない | 実行中の行が `holding` なら `HELD`（`stall: false`）を返し、スプリントもラッパーも読まない。規則 3 の `waiting_answer` は今のまま |
| `gate_wait` | `lane/liveness.py` | 新設 | `(row: LaneRow, sprint_wait: Mapping[str, Any]) -> bool` | 判定が `waiting_answer` で、スプリントの待ちが承認ゲート（`gate`）であり、回答が載っていない（`resuming` でない）か |

### 単体テストの観点

- 待ちの行のレーンは `count` に入らず、ラッパーが動いていれば一覧の側で数える（止めるまでは数える）
- 承認ゲートの待ちの行のレーンも、ラッパーを止めた後は `count` に入らない。承認ゲートでない質問の待ちは今のとおり数える
- `HELD` は `STALL_KINDS` に入らない

## 起動の手と割り当て（`plan/run`・`plan/run_decide`）

満たす決定: 決定 4・決定 8・決定 11、受け入れ条件 3・4・12、I7・I8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_halts` | `plan/run.py` | 変更 | 変えない | 最後の行が終わったレーンに加え、実行中の行が `holding` か `gate_wait` でラッパーが動いているレーンを止める先に入れる |
| `snapshot_of` | `plan/run.py` | 変更 | 変えない | 写しに `released`（`holding` で `implementation_wait` が空の行の（レーン, 行, スプリント, `held_seq`））と `idle`（`idle.current` の項目の番号の並びと `listed_seq`）と `free`（空きの数）を載せる。`released` は `row_implementing` が書かれるまで残る（`row_resumed` を見ない） |
| `_launches` | `plan/run_decide.py` | 変更 | 変えない | `released` から起動し直しの手（`Launch(resume=sid, seq=held_seq, admit=True)`）を作り、回答の後の next の手と同じ位置に並べる。回答の後の next の手にも `admit=True` を付ける（承認の待ちの行はラッパーを止めて席を返しているため） |
| `_assignable` | `plan/run_decide.py` | 新設 | `(snap: Snapshot, acts: list[Launch], failed: Mapping[int, int]) -> Optional[Assign]` | 手が 1 つも無く、計画が `running`、`free >= 1` なら、一覧のうち `failed` に無い最初の課題の割り当ての手を返す |
| `_after_observe` | `plan/run_decide.py` | 変更 | 変えない | 全行が終わったとき（`snap.complete`）も、一覧に `failed` に無い課題が残っていれば `Complete` を打たない。`_assignable` が手を返せば割り当てを、返さなければ（`free` が 0）`Sleep` を打つ。残っていなければ今のとおり `Complete` へ進む |
| `assign_idle` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, issue: int, listed_seq: int) -> dict` | 下の手順で割り当てで足すレーンを書く。結果は `{"ok": true, "lane": …}`・`{"ok": false, "stale": true}`・`{"ok": false, "detail": …}` のどれか |
| `decide` | `plan/run_decide.py` | 変更 | 変えない | `_candidates` が空のとき `_assignable` を見る。`stale` は失敗に数えず `Observe` へ戻る。断られたら、その課題を `RunState.assign_failed` に（課題, `listed_seq`）で入れ、同じ課題で 1 回だけ `launch_failed` の形の知らせを作る |

`assign_idle` の手順（1 つのトランザクション）:

1. `lock_plan` を取り、最新の計画とレーンを読む
2. 計画が `running` でない、`idle.current` の先頭（失敗した課題を飛ばした後の先頭）が `issue` でない、最後の `idle_listed`・`idle_adjusted` の番号が写しより新しい、のどれかなら書かずに `stale` を返す（観測の後の `idle set --pin`・`plan pause` を拾う）
3. 1 で読んだ計画から、名前（`i<番号>`、重なれば `i<番号>-<n>`）と `after`（同じリポジトリの終わっていないレーンのすべて）を組み、`add_lane(..., actor="workflow", idle=issue)` で書く。`add_lane` は同じトランザクションの中で呼び、排他を取り直さない
4. 計画の決まりで断られたら `{"ok": false, "detail": …}`

- 起動し直しの手は、行を始める手と同じくマシン全体の上限の判定（`machine.admitted`）を通す。入力は `launch.py` の回答の後の next と同じ並び（`ndf lanes row resumed` → `ndf progress next`）。`row resumed` は起動の試行の印で、待ちを解かない。同じ待ちを 2 回起動しないのは、今の起動のキー（`held_seq` を含む）と `RunState.launched` の期限による
- 割り当ての手の後の次の読み直しで、足したレーンは `wakeable` に入り、行を始める手（上限の判定を通す）になる。割り当てそのものは上限の判定を通さない（席を使わない）
- `RunState.assign_failed` は観測ごとに空にしない（`tried` と違う）。今の写しの `idle` の `listed_seq` が失敗を記録したときより新しい（一覧を作り直したか、その課題の調整が書かれた）項目は飛ばすのをやめる。知らせ済みの課題は `RunState.reported` で 2 回知らせない

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Assign` | 型（`issue`・`key`・`listed_seq`） | `plan/run_decide.py` | — |
| `Snapshot.released`・`Snapshot.idle`・`Snapshot.free` | 欄 | `plan/run_decide.py` | 空・None・None |
| `RunState.assign_failed` | 欄（（課題, `listed_seq`）の並び） | `plan/run_decide.py` | 空 |
| 割り当てで足すレーンの名前 | 規則 | `plan/run.py` | `i<課題の番号>`、重なれば `i<番号>-2`… |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 割り当てとオーガナイザーの `lane add`・`idle set`・`plan pause` | 計画のステートマシン | `assign_idle` の手順 1〜3 を 1 つのトランザクションにし、`lock_plan` の中で計画の状態と一覧の先頭を確かめ直す | 割り当ては `lane_added` 1 件で、課題が計画の行に入るので、再開しても同じ課題を 2 回足さない（手順 2 の確かめで外れる） |
| 割り当てとほかの計画の起動 | マシンの枠 | 割り当ては席を使わない。行の開始は今の `admit.lock` | 次の読み直しで数え直す |

### 単体テストの観点

- 待ちの行と承認の待ちの行のラッパーを止める手が、行を始める手より前に出る
- `after` を満たした待ちの行の起動し直しが 1 回だけで、上限なら待ちに置かれる。回答の後の next も上限なら待ちに置かれる
- 割り当ての条件（手が無い・`running`・`free >= 1`・一覧に失敗していない課題がある）のどれかが欠けると打たない。1 回の判断で 1 本
- 最後の既存の行が終わって空きができた読み直しで、一覧が残っていれば `Complete` でなく割り当てを打つ。一覧が空なら `Complete`
- 観測の後、`assign_idle` の前に `idle set --pin` か `plan pause` を書くと、`stale` で書かずに読み直す（失敗の知らせを出さない）
- 割り当てが断られたとき、3 回の観測を通して知らせが 1 回で、同じ課題を打ち直さず、次の読み直しで一覧の次の課題を割り当てる。一覧を作り直すと、その課題をまた割り当ての候補にする

## 空いた時間にやることの一覧（`plan/idle`・`cli`）

満たす決定: 決定 7、受け入れ条件 9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `order` | `plan/idle.py` | 新設 | `(items: Iterable[IdleItem]) -> list[IdleItem]` | マイルストーンの連番（名前の先頭の `^\d{2} `）の昇順、連番の無いものは後ろで名前の昇順、同じマイルストーンの中は課題の番号の昇順に並べる |
| `refresh` | `plan/idle.py` | 新設 | `(conn, pid: str, gh: GitHub, git: Git, base: BaseRef, extra_milestones: Sequence[int], actor: str) -> dict` | 計画の行の課題のマイルストーンと `extra_milestones` の開いた Issue を読み、計画の行の課題と設計のあるものを除き、`order` で並べて `idle_listed`（書き手 `actor`）を書く |
| `adjust` | `plan/idle.py` | 新設 | `(conn, pid: str, issue: int, how: Literal["pin", "exclude", "clear"], reason: str, actor: str) -> None` | `idle_adjusted`（書き手 `actor`）を書く |
| `design_issues` | `progress/model.py` | 新設 | `(kind: Literal["doc", "branch"], name: str) -> list[int]` | 設計文書の名前か設計ブランチの head から課題の番号を取り出す（規則は設計文書の「設計文書の番号」「設計ブランチの番号」）。当たらなければ空 |
| `caller_actor` | `plan/cli.py` | 新設 | `(environ: Mapping[str, str]) -> str` | 中継が子へ渡す `NDF_RELAY_PLAN`（計画のレーンのラッパーの子だけが持つ。`relay/hub.py` の `spawn_env`）があれば `conductor`、無ければ `organizer` を返す。脅威はうっかりの書き込みだけで、本人のなりすましは扱わない |
| `current` | `plan/idle.py` | 新設 | `(view: PlanView, lanes: Lanes) -> list[IdleItem]` | 最後の `idle_listed` の項目から、計画のどの行の課題と `exclude` を除き、`pin` を書いた順で先頭へ出す。GitHub を読まない |
| `show` | `plan/idle.py` | 新設 | `(conn, pid: str) -> dict` | `current` と `listed_at`・空き（`machine` の数え方）・`excluded` を結果の形で返す |
| `_cmd_idle` | `plan/cli.py` | 新設 | `(a: argparse.Namespace, cwd: Path) -> dict` | `refresh`・`set`・`show` を振り分ける。`refresh`・`set` の書き手は `caller_actor(os.environ)` で決め、`organizer` に決め打ちしない。事象の `actors` が conductor などを断る |
| `add_lane` | `plan/__init__.py` | 変更 | `(conn, pid, decl, request=None, actor="organizer", idle=None) -> str` | 書き手と `idle` を `lane_added` に載せる。ほかは今のとおり |

- 基のブランチの参照は `BaseRef`（`Git.remote()` とブランチ戦略の設定）で、`origin` を決め打ちしない。読む前に `git fetch <remote> <branch>` を 1 回打つ。取得に失敗したら GitHub を読めないときと同じく止める（古い参照で設計の有無を決めない）
- 設計の有無は、`<ref>` の `issues/` の名前の一覧（`git ls-tree --name-only <ref> issues/` を 1 回）の各名前に `design_issues("doc", 名前)` を当てた番号の和と、開いた Pull Request の一覧の head のうち設計ブランチの接頭辞（`model.branch_prefix("design")`）で始まるものに `design_issues("branch", head)` を当てた番号の和から求める。どちらかに入る課題を除く。課題ごとに GitHub を読まない
- マイルストーンの Issue の一覧から、Pull Request の項目（REST の `pull_request` を持つ）を除く
- 読み取りは次の 3 段で、どれも束ねて読む（前提 5）。(1) 計画の行の課題の番号を重複を除いて集め、所属のマイルストーンを `hub.nodes(issues, "issue", …)`（`closing_prs` と同じ一括の読み取り）で引く。行ごとに GitHub を呼ばない (2) 得たマイルストーンと `--milestone` の和の重複を除き、マイルストーンごとに開いた Issue の一覧をページ送りで読む (3) 開いた Pull Request の一覧をページ送りで 1 系列読む。1 回の `refresh` の呼び出しの回数は「(1) の束の数 + (2) のページ数の和 + (3) のページ数」で、計画の行の数にもタイムアウトの回数にも比例しない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `refresh` | GitHub を読めない | 認証・ネットワーク・上限 | `ndf.adapters.github` の例外 | 止める（前の一覧を残す） | `stopped`・`work_stopped`、終了コード 1 | 書かない。打ち直せる |
| `refresh` | 計画の行の課題にマイルストーンが 1 つも無い | 計画の課題がマイルストーンを持たない | 読んだ Issue | 止める | `stopped`・`precondition`、`--milestone` を渡すよう `summary` に書く | 打ち直せる |
| `refresh` | 基のブランチを取得できない | ネットワーク・認証・リモート名の誤り | `git fetch` の終了コード | 止める（前の一覧を残す） | `stopped`・`work_stopped`、終了コード 1 | 書かない。打ち直せる |
| `adjust`・`refresh` | 書き手が organizer でない | レーンの conductor（中継の子）・workflow が打った | `caller_actor` で決めた書き手を事象の定義（`actors`）で照らす | 断る | `refused`・終了コード 1 | 書かない |
| `show` | 上限を読めない | マシンの設定が壊れている | `named_cap.read` | 既定値で続ける | `free` を null、`warnings` に理由 | — |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `IdleItem` | 型（`issue`・`title`・`milestone`・`milestone_order: Optional[int]`） | `plan/idle.py` | — |
| `MILESTONE_ORDER` | 定数（正規表現） | `plan/idle.py` | `^(\d{2}) ` |
| `IDLE_LISTED`・`IDLE_ADJUSTED` | 定数（事象の種類） | `lane/types.py` | `"idle_listed"`・`"idle_adjusted"` |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `refresh` | 読んだマイルストーン・項目の数・除いた数（計画の行・設計あり） | 結果 JSON の `metrics` | GitHub の token |
| `assign_idle` | 割り当てた課題とレーンの名前 | 計画の `run.log` と `lane_added` の中身 | — |

### 単体テストの観点

- `order`: 連番 `01`・`63`・連番無しの 3 つのマイルストーンと、同じマイルストーンの番号の前後
- `refresh`: 計画の行の課題・設計文書のある課題・設計 PR の開いた課題を除く。まとめた設計文書 `issue-4049-4050-design.md` は両方を除き、`issue-4050-design-detail.md` だけでは除かない。head が `design/issue-2489`・`design/4050-parallel-design-ahead`・`design/4049-4050-x` の開いた PR を一括の一覧で返し、#2489・#4049・#4050 を除く。`design/feature-x` は除かない。マイルストーンの一覧に混じった PR を項目に入れない。差し替えた GitHub の書き込みの呼び出しが 0 回。読み取りの呼び出しが、同じ課題を 3 行に持つ 12 行の計画・2 ページのマイルストーン・2 ページの PR の一覧で、束 1 + ページ 2 + ページ 2 の 5 回。読めなければ・取得できなければ `idle_listed` を書かない。リモート名が `upstream` の作業場所で `upstream/<基>` を読む
- `design_issues`: 上の名前のそれぞれと、数字の無い名前（空を返す）
- `current`: `pin` が先頭、`exclude` が作り直しの後も外れたまま、割り当てで計画に入った課題が消える
- 書き手の検査: conductor と workflow の `idle_listed`・`idle_adjusted` は断られる（事象を直接作る）。加えて、`NDF_RELAY_PLAN` を持つ環境で CLI の `ndf lanes idle set`・`refresh` を打つと `refused` になり、持たない環境では通る（CLI の経路）

## 表示と知らせ（`plan/__init__`・`wait_decide`）

満たす決定: 決定 3、受け入れ条件 8・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_lane_items` | `plan/__init__.py` | 変更 | 変えない | レーンの項目に `touches` を足す |
| `_row_item` | `plan/__init__.py` | 変更 | 変えない | `holding` の行に `held`（`waiting`・`stage`・`since`）を足す |
| `_plan_metrics` | `plan/__init__.py` | 変更 | 変えない | `idle`（件数と先頭の課題）を足す |
| `NOTICE_KINDS` | `plan/wait_decide.py` | 変更 | `("request_opened", "plan_completed", "lane_held", "lane_row_ended")` | `lane_held` と `lane_row_ended` を知らせにする。どちらも計画の事象なので、今の `--after` の番号で続きを読め、読み取りもポーリングも増えない |

### 単体テストの観点

- `status` の出力で、待ちの行だけが `held` を持ち、起動し直した後（`row_resumed` の後）の行は持たない
- `wait` が `lane_held` を 1 回返し、`--after` を進めた後は返さない
- 行が終わると `wait` が `lane_row_ended` を返す（耐久ワークフローの `row end`・conductor の `row end` のどちらでも）

## 手順書と契約（`parallel.md` ほか）

満たす決定: 決定 1・決定 7・決定 8・決定 10、受け入れ条件 13・14

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/parallel.md` | 知らせの表に `lane_held`（一覧を作り直し、待つ先を読む）と `lane_row_ended`（一覧を作り直す）を足す。節「空いた時間にやること」を足し、一覧を作り直す 4 つの時点・`pin`・`exclude` の基準（計画の目的に沿わない課題を外す・依存の先になる課題を先に出す）・割り当ては耐久ワークフローが行うこと・設計承認の回答を届ける前に `ndf lanes set <レーン> --touches <設計の結果の触るファイル> --after <重なるレーン>` を打つこと（合わせるまで実装は `touches` の待ちに入る）を書く。「耐久ワークフローがすること」の表に、実装の待ちと承認の待ちの行のラッパーを止める・待ちの解けで起動し直す・割り当て（一覧が残る間は計画を完了にしない）の行を足す。設計を直す手（確認の結果の `drift` を読み、続けるなら `next --drift-reviewed`、続けないなら行を中止して同じ課題の行を足す）を書く |
| `skills/development-workflow/references/parallel-checks.md` | 「マシン全体の稼働中の数」に、実装の待ちの行を数えないことを足す |
| `skills/issue-plan-strategy/references/parallel-plan.md` | `after` の行を「スプリントの行では実装のフェーズを始める条件、開発ループの行では行を始める条件」に直す |
| `docs/contracts/commands.md` | `ndf lanes idle`、`status`・`wait` の結果の足したキー、`ndf progress next` の `held`・`drift` |
| `contracts/schemas/io/lanes.args.schema.json` | `idle` の 3 つのサブコマンドの引数 |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 設計文書の「用語」の 5 語を足し、`ndf records glossary render` で作り直す |
