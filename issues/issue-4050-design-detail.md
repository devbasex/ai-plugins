# #4050 の詳細設計: 実装の待ち・空いた時間にやることの一覧・割り当て

設計文書 `issues/issue-4050-design.md` の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の detailed-design.md にある。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── workflow/progress.json              # 事象 row_held・row_implementing・row_drift_reviewed・row_parked・row_seated・row_unparked・lane_held・lane_row_ended・idle_listed・idle_adjusted、lane_added の書き手
│   └── schemas/io/lanes.args.schema.json   # ndf lanes idle の引数
├── docs/contracts/commands.md              # ndf lanes idle・status・wait、ndf progress next の結果
├── src/ndf/domains/relay/signals.py        # 質問のファイルに AskUserQuestion の metadata を写す
├── src/ndf/progress/
│   ├── model.py                            # design_issues*（設計文書と設計ブランチの番号）
│   ├── run.py                              # Env.hold・Env.peek・_start・wait と next の手の組み立て・--drift-reviewed
│   ├── gate/answering.py                   # lane_env
│   ├── lane/
│   │   ├── types.py                        # LaneRow の held_seq・held・implementing_seq・gated・drift・review・drift_reviewed・parked_seq・park_seat・park_stop・seated_seq・seat_launch・unparked_seq・seat_fail・holding・design_phase・parked・seat_progressed
│   │   ├── fold.py                         # row_held・row_implementing・row_drift_reviewed・row_parked・row_seated・row_unparked の畳み込み、idle_* の畳み込み
│   │   ├── rules.py                        # next_row・implementation_wait*・park_reason*・_order_graph・_after_sources（設計中の行へだけ起点の末尾から、ほかの行へは終わった行からだけ引く）
│   │   ├── __init__.py                     # start_row（gated）・begin_implementation*・review_drift*・changed_paths*・drift*・park*・seat*・unpark*・end_row（lane_row_ended）
│   │   ├── machine.py                      # lanes_of・held_seats（実装の待ちと席を返した行を数えない）
│   │   ├── liveness.py                     # HELD・席を返した行の判定・wrapper_table*（観測で 1 回だけ読む一覧）・stopping*（停止の済んでいない席を返した行）
│   │   └── launch.py                       # 起動し直しの入力（スプリントの行は next → row resumed --answered の順、開発ループの行は今のとおり）
│   └── plan/
│       ├── __init__.py                     # _row_item・_lane_items・_plan_metrics、add_lane の書き手、ask_request（質問の metadata）、complete_plan（排他の中で一覧と作り直しを確かめる）
│       ├── idle.py*                        # refresh*・adjust*・current*・show*
│       ├── run.py                          # _halts・_parks*・snapshot_of・launch_lane（席の取り直し）・assign_idle*（耐久ステップ）
│       ├── run_decide.py                   # Snapshot の released・parks・parked・idle・free、Launch の answered・hold・fail_key、RunState.assign_failed、_launches・_after_observe・_fail・Assign*・ParkAct*
│       ├── wait_decide.py                  # NOTICE_KINDS
│       └── cli.py                          # ndf lanes idle・caller_actor*・answered の --gate・row resumed の --answered
└── tests/progress/                         # test_lane・test_lane_machine・test_plan・test_plan_run・test_plan_wait・test_run_hold*・test_plan_idle*
```

依存の向き: `run.py`（スプリントの進行）は `Env` の口だけを持ち、`lane` を import しない。`gate/answering.py` が `lane` と `run` をつなぐ（今の `ended` と同じ）。`plan/idle.py` は `lane`（計画の読み書き）・`lane.machine`（空きの数）・`ndf.adapters.github`（作り直しの読み取り）に依存し、`plan/run.py` は `plan/idle.py` の `current` を読み、`plan/__init__.add_lane` で書く。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/progress.json` | 変更 | 事象の定義に 10 足し、`lane_added` の書き手に `workflow` を足す | — | — | — |
| `src/ndf/progress/model.py` | 変更 | 設計文書の名前と設計ブランチの head から課題の番号を取り出す | — | — | `design_issues`（新設） |
| `src/ndf/progress/run.py` | 変更 | 最初の `implement` の工程の手前で `Env.hold` を呼び、`work`・`wait`・`next` のどれでも待ちと確認を返す | `stages` | `Env` | `Env.hold`・`Env.peek`（新設の欄）、`_start`・`_drive`・`wait`・`report`・`_cmd_wait`（変更）、`_hold_report`・`_drift_report`（新設） |
| `src/ndf/progress/gate/answering.py` | 変更 | `Env.hold`・`Env.peek` を取得・実装の開始の判定と記録につなぐ | `lane`・`run`・`ndf.adapters.git` | — | `lane_env`（変更） |
| `src/ndf/progress/lane/types.py` | 変更 | 行の実装の待ちと実装の開始を持つ | — | `LaneRow` | `held_seq`・`held_at`・`held`・`hold_base`・`implementing_seq`・`gated`・`drift`・`review`・`drift_reviewed`・`parked_seq`・`park_seat`・`park_stop`・`seated_seq`・`seat_launch`・`unparked_seq`・`seat_fail`（欄）、`holding`・`implementing`・`design_phase`・`parked`・`seat_progressed`（新設） |
| `src/ndf/progress/lane/fold.py` | 変更 | `row_held`・`row_implementing`・`row_drift_reviewed`・`row_parked`・`row_seated`・`row_unparked` と `idle_*` を畳み込む | `types` | — | レーンの畳み込み（変更）、`fold_idle`（新設） |
| `src/ndf/progress/lane/rules.py` | 変更 | スプリントの行は `after` を見ずに始め、実装の手前で `after` と未確定の `touches` を照らす。I13 のグラフの辺を実装の開始で切り替え、実装を始めた行と始まった行へは起点の終わった行からだけ辺を引く。席を返す理由を 1 か所で決める | `types` | — | `next_row`・`_order_graph`・`_after_sources`（変更）、`implementation_wait`・`park_reason`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | 実装の開始を判定して待ちか開始を書き、変わったファイルを求め、行の終わりを計画へ知らせる。回答の記録に質問の id を載せ、再開の印は回答の消費を確かめたときだけ書く | `rules`・`model`・`ndf.adapters.git` | — | `begin_implementation`・`review_drift`・`changed_paths`・`drift`・`park`・`seat`・`unpark`（新設）、`end_row`・`record_answer`・`record_resumed`（変更） |
| `src/ndf/progress/lane/machine.py` | 変更 | 実装の待ちの行と席を返した行のレーンを、記録だけで数えない | `types` | — | `lanes_of`・`held_seats`（変更） |
| `src/ndf/progress/lane/liveness.py` | 変更 | 実装の待ちの行を `held`、承認ゲートで席を返した行を `waiting_answer` と記録から判定し、席を返す前の承認ゲートの待ちを見分ける | `types`・`stages` | — | `judge`・`judge_lanes`（変更）、`HELD`（定数）、`gate_wait`・`wrapper_table`・`stopping`（新設） |
| `src/ndf/progress/lane/launch.py` | 変更 | スプリントの行（`kind: sprint`）の回答の後の next と待ちの起動し直しの入力を `ndf progress next` → `ndf lanes row resumed` の順にし、回答の後の next では対象の回答の番号（`--answered <Launch.answered>`。起動のキーの番号と別に運ぶ）を渡す。開発ループの行（`kind: loop`）は今の `resume_commands` のまま（`row resumed` → `ndf loop get <ループ>`） | — | — | `resume_commands`（行の種類で分ける。変更） |
| `src/ndf/progress/plan/idle.py` | 新設 | 一覧の作り直し・調整・読み | `lane`・`machine`・`github` | — | `refresh`・`adjust`・`current`・`show`・`order` |
| `src/ndf/progress/plan/run.py` | 変更 | 写しに待ち・席を返す先・一覧を載せ、席の返却と取り直し・割り当ての耐久ステップを持つ | `idle`・`plan` | — | `_halts`・`snapshot_of`・`launch_lane`（変更）、`_parks`・`park_lane`・`assign_idle`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 待ちの起動し直し・席を返す手・割り当ての手を足し、1 行に続きの手を 1 つに絞り、一覧が残る間は完了にしない | — | `Snapshot`・`RunState`・`Launch`・`Assign`（新設）・`ParkAct`（新設） | `_launches`・`_after_observe`・`decide`・`_fail`（変更）、`_assignable`・`Launch.fail_key`（新設） |
| `src/ndf/progress/plan/wait_decide.py` | 変更 | `lane_held`・`lane_row_ended` を知らせにする | — | — | `NOTICE_KINDS`（変更） |
| `src/ndf/progress/plan/__init__.py` | 変更 | 表示に `touches`・`held`・一覧を載せ、`add_lane` に書き手を渡せるようにし、質問のリクエストに `metadata` を載せる | `lane`・`idle` | — | `_row_item`・`_lane_items`・`_plan_metrics`・`add_lane`・`ask_request`・`complete_plan`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 質問のファイル（`question.json`）に AskUserQuestion の `metadata` を写す | — | — | 質問を書く関数（変更） |
| `src/ndf/progress/plan/cli.py` | 変更 | `ndf lanes idle refresh / set / show` と呼び出し元の役割 | `idle` | — | `_cmd_idle`・`caller_actor`（新設） |

## 実装の待ちの判定と記録（`rules`・`lane`）

満たす決定: 決定 2・決定 3・決定 6・決定 8・決定 13・決定 14、受け入れ条件 1・2・4・5・6・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `implementation_wait` | `lane/rules.py` | 新設 | `(view: PlanView, lanes: Lanes, lane: str) -> list[str]` | レーンの `after` のうち `after_met` が偽の値を、計画の並びの順で返す。レーンが `idle` を持ち `touches` が空なら、末尾に印 `TOUCHES_UNSET` を足す |
| `next_row` | `lane/rules.py` | 変更 | 変えない | 行の入力が `kind: sprint` なら `waiting_after` を常に空にする。`loop` は今のとおり |
| `_order_graph` | `lane/rules.py` | 変更 | 変えない | スプリントの行のレーンでは、レーン全体を指す `after` の辺を行ごとに引く。起点の末尾から（`_after_sources(ref, lanes, ended=False)`）引くのは、`LaneRow.design_phase` が真の行（未着手の行と、実装の待ちを持つ版で始めてまだ実装の開始を記録していない実行中の行）だけである。それ以外の行、つまり実装の開始を記録した行・終わった（完了か中止の）行・実装の待ちを持たない版で始めた実行中の行（旧形式。実装を始めたかを記録から読めない）へは、起点の終わった行からだけ（`ended=True`）引く。終わった行へ実行中の行から辺を引かないので、終わった行を経由して実行中の 2 行に後付けの前後ができない（実装の前に中止した C の `after` に実行中の A を足しても A→C を引かず、実装中の B の `after` に C を足した C→B と合わせても A と B の重なりを I13 が断る）。旧形式の行を実装中と読むのは安全側で、辺が減るぶん I13 は断る側へ倒れる。先頭の行と最初の未着手の行だけで決めない。開発ループの行のレーンは今のとおり行の状態で読み、最初の未着手の行へだけ起点の末尾から、始まった行と終わった行へは起点の終わった行から（`ended=True`）引く（決定 14） |
| `_after_sources` | `lane/rules.py` | 変更 | `(ref: str, lanes: Lanes, ended: bool) -> list[tuple[str, str]]` | `after` の 1 つが指す起点の行。`ended` が真なら、起点のうち終わった（完了か中止の）行だけを採る。レーンを指せばその終わった行の末尾、行を指せばその行が終わっていればその行で、どちらも無ければ空。偽なら今のとおりレーンの末尾の行か指した行。今の `started`（未着手でない行を採る）をやめる。実行中の行を起点に採ると、実行中の 2 行に後から足した `after` が前後の辺になり、I13 が重なりを見逃すため（決定 14） |
| `park_reason` | `lane/rules.py` | 新設 | `(row: LaneRow, gate_key: Optional[str], alive: Optional[bool]) -> Optional[str]` | 席を返す理由を 1 か所で決める。`parked` でない実行中の行で、(1) `holding` で `held_seq > seated_at_seq` なら `held` (2) `gate_key`（`gate_wait` が返したリクエストのキー）があれば `gate`（実装を始めた行も含む。リリース承認を待つ行は実装を始めた後にある） (3) `holding` か `awaiting_next` で `alive` が偽なら `vacant`。`alive` が None（ラッパーの一覧を読めない）なら (3) を決めない。どれでもなければ None。耐久ワークフローの観測（`_parks`）と書く前の再検証（`park`）が同じこの関数を読み、2 つの条件を食い違わせない。`gate_key` は、観測では `gate_wait` が、再検証では `lane.regate` が作る（どちらも「回答が今の着席の後に載っていない・今の質問の id のリクエストが開いている」を同じ条件で見る）。再検証では排他の中でラッパーを読み直さず、`alive` には手が運ぶ観測の値を渡す。手の理由が `vacant` なら `False`（観測でラッパーが無かった）、`held`・`gate` なら None である。`vacant` の手は、着席の番号が手の `seat` と同じ（観測の後に席を取り直していない）ことを共通の条件で確かめるので、観測の後に同じ着席でラッパーが現れることは無い（ラッパーを起動するのは `launch_lane` だけで、起動の前に `row_seated` で着席を進める）。`None` を渡すと (3) を決めず、`vacant` の手が毎回 `stale` になる（決定 13） |
| `begin_implementation` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, stage: str, base: Callable[[], BaseRef], git: Callable[[], Git]) -> Begin` | 下の「手順」で、待ちか実装の開始のどちらかを、計画の排他の中で 1 回で決めて書く。`base`・`git` は Git が要ると決まったときだけ呼ぶ（`Git.remote()` も `git worktree list` もそれまで打たない） |
| `review_drift` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, reason: str) -> None` | 実装の開始を記録し `review` が真の行に `row_drift_reviewed` を書く（確認待ちの判定は `review && !drift_reviewed` の 1 つで、`drift` の値を見ない）。記録が無い・`review` が偽・既に書いた行は書かない |
| `changed_paths` | `lane/__init__.py` | 新設 | `(git: Git, base_sha: str, head_sha: str) -> Optional[list[str]]` | 計画の排他の外で `git diff --name-only <base_sha>..<head_sha>` を 1 回打ち、変わったパスを返す。読めなければ None |
| `drift` | `lane/__init__.py` | 新設 | `(paths: Optional[Sequence[str]], touches: Sequence[str]) -> Optional[list[str]]` | 計画の排他の中で、`changed_paths` の結果のうち最新の `touches` のどれかと `touch_hit` で当たるものを昇順で返す。`paths` が None なら None。Git を読まない |
| `regate` | `lane/__init__.py` | 新設 | `(view: PlanView, row: LaneRow, act: ParkAct, current_gate: Callable[[], Optional[str]]) -> Optional[str]` | 再検証の `gate_key` を DB だけで作る。次のすべてを満たせば `act.request`、どれかが欠ければ None: (1) 回答が今の着席の後に載っていない（`answered_seq <= seated_at_seq`。`gate_wait` の (2) と同じ条件） (2) 計画のリクエスト `act.request` が開いている (3) `current_gate()` が `act.gate`（観測した質問の id）と同じ (4) そのリクエストの中身が `act.gate` と行のスプリントに合う（`lane_question` は `metadata.gate`・`metadata.sprint`、`lane-release` は `id` の `lane-release-<質問の id>`・`sprint_state`）。`park` と `park_lane` の手順 3 が呼ぶ |
| `park` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, act: ParkAct, current_gate: Callable[[], Optional[str]]) -> Optional[int]` | `lock_plan` の中で行を読み直し、`park_lane` の手順 1 の再検証を通った実行中の行にだけ `row_parked`（書き手 workflow。中身に `reason`・`stopped`・`request`・`gate`）を書く。外れれば書かずに None。`current_gate` はスプリントが今待つ承認ゲートの質問の id を DB だけで読む関数で、呼び手（`plan/run.py`）が `gate.asking.waiting` から作って渡す（`lane` は `gate` を import しない。`record_resumed` の `consumed` と同じ形） |
| `seat` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, launch_key: str, fail_key: str) -> Optional[int]` | `lock_plan` の中で行を読み直し、席を返した行に `row_seated`（書き手 workflow。中身に `launch`・`fail`）を書く。席を返していない行は書かない。`launch_lane` はラッパーを起動する前に呼ぶ |
| `unpark` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, seat: int, reason: Literal["stop_failed", "gate_changed"]) -> Optional[int]` | `lock_plan` の中で行を読み直し、席を返した行で `seated_at_seq` が `seat` と同じときだけ `row_unparked`（書き手 workflow）を書く。`row_seated` と違い今の着席を新しくしない（`seated_at_seq` を進めない）ので、`held_seq > seated_at_seq` も `gate_wait` も元のまま成り立ち、次の読み直しで同じキーの席を返す手になる。`park_lane` がラッパーを止められなかったときと、止める直前に質問の id が変わったときに呼ぶ（決定 13） |
| `end_row` | `lane/__init__.py` | 変更 | 変えない | `row_ended` と同じトランザクションで、計画に `lane_row_ended`（`lane`・`row`・`outcome`）を書く |
| `LaneRow.holding` | `lane/types.py` | 新設 | `@property -> bool` | `state == RUNNING and held_seq > 0 and implementing_seq == 0` |
| `LaneRow.implementing` | `lane/types.py` | 新設 | `@property -> bool` | `implementing_seq > 0` |
| `LaneRow.gated` | `lane/types.py` | 新設（欄） | `bool` | 行の `row_started` の中身の `gated`（実装の待ちを持つ版の `start_row` が真で書く）。無い行（更新の前に始めた行）は偽 |
| `LaneRow.design_phase` | `lane/types.py` | 新設 | `@property -> bool` | `state == PENDING`、または `state == RUNNING and gated and not implementing`。I13 のグラフで起点の末尾から辺を引く行はこれだけ（`_order_graph`） |
| `start_row` | `lane/__init__.py` | 変更 | 変えない | `row_started` の中身に `gated: true` を足す（更新の前に始めた実行中の行と見分ける） |
| `LaneRow.awaiting_review` | `lane/types.py` | 新設 | `@property -> bool` | `implementing and review and not drift_reviewed`。確認待ちの判定はこの 1 か所に置き、`begin_implementation` の手順 1・`lane_env` の `hold`・`peek` はどれもここを読む（`drift` の空・null で分けない） |
| `LaneRow.parked` | `lane/types.py` | 新設 | `@property -> bool` | `state == RUNNING and parked_seq > max(seated_seq, unparked_seq)`（席を返した行。回答が載っても `row_seated` まで真。`row_unparked` で偽に戻る） |
| `LaneRow.unparked_seq` | `lane/types.py` | 新設（欄） | `int` | 最後の `row_unparked` の番号。`seated_at_seq` には入れない |
| `LaneRow.park_seat`・`LaneRow.park_stop`・`LaneRow.park_reason` | `lane/types.py` | 新設（欄） | `int`・`bool`・`Optional[str]` | 最後の `row_parked` の `seat`（席を返した着席の番号）・`stopped`（ラッパーを止める予定だったか）・`reason`。同じ着席の停止を再開する手がこれを読む |
| `LaneRow.seat_launch` | `lane/types.py` | 新設（欄） | `Optional[str]` | 最後の `row_seated` の `launch`（今の着席を開いた続きの手の起動のキー） |
| `LaneRow.seat_fail` | `lane/types.py` | 新設（欄） | `Optional[str]` | 最後の `row_seated` の `fail`（席を取り直した続きの手の失敗のキー。`Launch.fail_key`） |
| `LaneRow.seat_progressed` | `lane/types.py` | 新設 | `@property -> bool` | 今の着席の間（`seated_at_seq` より後）に、`row_held`・`row_implementing`・回答の番号を持つ `row_resumed`（`--answered` で書いたもの）のどれかがある。回答の無い `row_resumed`（起動の試行の印）は数えない。起動し直した conductor が何も進めずに終わったかを読む |
| `LaneRow.hold_base` | `lane/types.py` | 新設（欄） | `Optional[tuple[str, str]]` | 行の最初の `row_held` の（`ref`・`base_sha`）。後の `row_held` で上書きしない。差分の基準はこれだけで、着席ごとの待ちの記録（`held`・`held_seq`）と分ける |
| `LaneRow.seated_at_seq` | `lane/types.py` | 新設 | `@property -> int` | 今の着席の始まりの番号（レーンのステートマシンの事象の番号）。`seated_seq` があればそれ、無ければ行の開始の事象の番号 |

`BaseRef` は（`remote`・`branch`・`ref`（`<remote>/<branch>`））で、呼び手（`lane_env`）が `Git.remote()` とブランチ戦略の設定から組む関数として渡し、`begin_implementation` が Git を読むと決めたときだけ呼ぶ。`origin` を決め打ちしない。

`begin_implementation` の手順:

1. 計画の排他の外で、行とレーンを DB から読む（`sprint_row`）。属さなければ `Begin(outside=True)`（計画の外のスプリント。何も書かない）。行が既に `implementing` なら、書かずに今の記録を返す（`review` が真で `drift_reviewed` が偽なら確認待ち。`drift` が null でも同じ）
2. 同じ読み取りで `implementation_wait` を求め（DB だけ）、次の 5 つに分ける。Git を読むのは (c)・(d) だけである
   - (a) 今の着席の待ちがあり（`holding` で `held_seq > seated_at_seq`）、待つ先が残る: Git を読まずに今の待ちを返す（書かない）
   - (b) 待ったことが無く（`holding` でない）、待つ先も無い: Git を読まずに 3 へ進む（`after` がすべて満たされた行も、初回の `next` で `fetch` しない）
   - (c) 待つ先があり、待ったことが無い（`held_seq == 0`）: `base()`・`git()` を呼び、`Git.fetch_branch(<remote>, <branch>)`（宛先を明示した refspec `+refs/heads/<branch>:refs/remotes/<remote>/<branch>` の取得。clone の fetch refspec が対象のブランチを追跡しなくても `<ref>` が更新される。`git fetch <remote> <branch>` だけでは `FETCH_HEAD` しか更新されないことがある） を 1 回打ってから `git rev-parse <ref>` で `base_sha` を読む
   - (c') 待つ先があり、前の着席で待ったが今の着席の待ちが無い（起動し直した conductor がまだ待つ先を見た）: Git を読まない。新しい `row_held` の `base_sha`・`ref` には、行の最初の待ちの値（`LaneRow.hold_base`）を写す
   - (d) `holding` で待つ先が無い（待ちが解けた）: `base()`・`git()` を呼び、(c) と同じ `Git.fetch_branch` と `git rev-parse <ref>` で今のコミットを読み、`changed_paths(git, hold_base.base_sha, 今のコミット)` で変わったパスを読む。基準は最後の `row_held` でなく行の最初の待ちのコミットなので、最初の待ちから再着席までに入った変更も漏れない
3. トランザクションを始め、`lock_plan` を取り、最新の計画とレーンを読み直して `implementation_wait` を求める。排他の中では Git を読まない
4. 空でなければ: 今の着席の待ちがあれば書かずに今の待ちを返す。そうでなく (c) なら 2 の値、(c') なら `hold_base` の値を `ref`・`base_sha` にして、`row_held` と `lane_held` を `checked_write` で書いて新しい待ちを返す。(d) の行（前に待った行で、2 と 3 の間に `lanes set` が `after` を足した）も、Git を読み直さずに `hold_base` の値で同じく書く。(b) の行（待ったことが無く `base_sha` を読んでいない）は、書かずにトランザクションを抜けて 1 から 1 回だけやり直す。2 回目も同じなら `stopped`・`precondition` で止める
5. 空でも、3 で読み直した行が `parked`（`park_lane` が先に `row_parked` を書いた）なら、`row_implementing` を書かずに今の待ちを返す（席を取り直した後の `next` で始める）。空なら: (c)・(c') の行（2 と 3 の間に待つ先が無くなった）は、書かずにトランザクションを抜けて 1 から 1 回だけやり直す（(b) か (d) として読み直す）。(d) の行は `drift(2 のパス, 最新の touches)` を求め、`row_implementing`（`stage`・`drift`・`base_sha`・`review`（`drift` が空でないか null なら真））を書く。(b) の行は `row_implementing`（`stage`、`review` は偽）を書く。どれも `checked_write` で書き、`Begin(started=True, drift=…, review=…)` を返す。`checked_write` は I13 のグラフを、書いた後の「実装の開始」で組み直して確かめる

- 事前条件: 呼び手は `store.run_lock`（スプリントごと）を持っている
- 事後条件: 待ちを返したとき、行の `holding` が真で、今の着席の間の `row_held` は 1 件だけある（I2）。開始を返したとき、`row_implementing` は 1 件だけあり、書いた時点の最新の計画で `implementation_wait` が空だった（I9）
- 副作用: 計画とレーンのステートマシンへの書き込み（同じトランザクション）。Git は 2 の (c)・(d) だけで、`Git.fetch_branch` と `git rev-parse` を高々 1 回ずつ、(d) で `git diff` を高々 1 回（すべて排他の外）。(a)・(b)・(c') の行は `Git.remote()` も含めて Git を 1 回も打たない（やり直しの 1 回を除く）

計画の排他を取るほかの書き込み（`lanes set`・`lane add`・`row add`・割り当て）は、どれも今の `checked_write` か `lock_plan` を通る。手順 3〜5 が 1 つのトランザクションなので、これらは `row_implementing` の前に入って待ちを生むか、後に入って実装を始めた行を含むグラフで I13 の検査を受けるかのどちらかになる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `begin_implementation` | 計画の決まりで断られる | 待ちか開始の書き込みが I13 などに当たる | `checked_write` | 呼び出し元へ返す | `next` が `stopped`・`precondition` と断った理由 | 書かない。直して `next` を打ち直せる |
| `begin_implementation` | 取得か参照の読み取りに失敗する | ネットワーク・認証・基のブランチが無い・リモート名の設定の誤り（`RemoteError`） | 手順 2 の `Git.fetch_branch`・`git rev-parse` の終了コード | 待ちも開始も書かずに止める（古い参照で `base_sha` を書かない） | `next` が `stopped`・`work_stopped`、`summary` に打ったコマンドと理由 | 打ち直せる |
| `begin_implementation` | 排他の間に依存が増え、Git を読んでいない | 手順 2 と 3 の間の `lanes set` | 手順 4 | 1 回だけやり直す | 2 回目も同じなら `stopped`・`precondition` | 打ち直せる |
| `changed_paths` | 差分を読めない | `base_sha` が取得した履歴に無い（強制 push など） | `git diff` の終了コード | 既定値で続ける | `drift` を null・`review` を真で記録し、`warnings` に理由。null は空と区別し、確認の結果を返す（判断の記録を求める） | 工程は判断の記録の後に始める。打ち直しても起動し直しても、記録の `review` から確認の結果を返す |

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
| `begin_implementation` と席を返す手（`park_lane`） | レーンの行 | どちらも `lock_plan` の中で行を読み直してから書く。`row_parked` の後の `begin_implementation` は実装を始めず、`row_implementing` の後の `park_lane` は再検証で外れる | 先に書いたほうだけが通る。止めた後は起動し直しが席を取り直して続ける |
| `begin_implementation` と耐久ワークフローの読み直し | 計画とレーンのステートマシン | 今の `lock_plan` | 読み直しは書いた後の状態を次の周で読む |

### 単体テストの観点

- `implementation_wait`: 行を指す `after`・レーンを指す `after`・行の無いレーンを指す `after`（満たさない）の 3 通り。`idle` を持ち `touches` が空のレーンは `after` を満たしても印を返す
- `next_row`: スプリントの行は `after` があっても `waiting_after` が空、開発ループの行は今のとおり
- `_order_graph`・`_after_sources`（決定 14）: A と B が別の場所を触り、両方の行が実装を始めた後（A の行は `running`）、`lanes set` で B の `touches` を A と重なる値にして `after: [A]` を足すと I13 で断られる（実行中の行を起点に採ると通って落ちる）。行を指す `after: [A/r1]`（r1 が `running`）でも同じ。A の行が終わった後なら通る。開発ループの始まった行でも同じ。
- `_order_graph`（終わった行を経由する経路）: A・B が同じ場所を触り、A は実装を始めた実行中、B は実装を始めた実行中、C は実装の前に中止した行のとき、`lanes set` で C の `after` に A を足し、続けて B の `after` に C を足すと、B の `touches` を A と重ねる書き込みが I13 で断られる（終わった C へ A の末尾から辺を引くと A→C→B の経路ができて通り、落ちる）
- `_order_graph`（更新をまたぐ行）: `gated` を持たない `row_started`（更新の前の版で始めた行）の実行中の B と実行中の A について、後から B の `after` に A を足して `touches` を重ねると I13 で断られる（`implementing` の有無だけで設計中と読むと A の末尾から辺を引いて通り、落ちる）。同じ組で、`gated` を持つ B（設計中）なら今の規則どおり通る
- `_order_graph`: A と B が同じ場所を触り `B.after=[A]`、両方が未着手のとき、B の `row start` も、続く A の `row start` も I13 で断られない。B が実装を始めた後に A へ行を足しても、始めた時点で終わっていた行からだけ辺を引く（今のテストが変えずに通る）。B の先頭の行が実装を始めた後に A へ未着手の行を足し、その A を待つ B の 2 行目を `row start` で設計へ進めても断られず、2 行目が `running` の間も A の末尾から 2 行目への辺が残る
- `begin_implementation`: 1 回目は待ちを書き、2 回目は書かない。`row_seated` の後にまだ待つ先があれば、Git を打たずに、最初の待ちの `base_sha` を写した新しい待ちを 1 件書く。依存が解けた後は `row_implementing` を 1 回だけ書き、`row_resumed` だけでは待ちのまま。手順 2 と 3 の間に `lanes set` で満たしていない `after` を足すと開始を書かない。`after` が空の行と、`after` がすべて満たされた待ったことの無い行では、Git の呼び出し（`Git.remote()`・`git worktree list` を含む）が 0 回で、取得が失敗する作業場所でも実装を始める。今の待ちがあり依存が未解決の行で打ち直しても Git の呼び出しが 0 回。取得の失敗で何も書かない。計画の外のスプリントは `outside`
- `drift` の算出: 最初の待ちの後、席を返す前に `touches` に当たるファイルを基のブランチへ入れ、再着席で依存が増えて待ち直し、その後に待ちが解けると、そのファイルが `drift` に載る（差分の基準を最後の `row_held` にすると落ちる）。手順 2 と 3 の間に `lanes set` で `touches` を広げると、広げた先に当たる変更も `drift` に載る。差分を読めず null で記録した後に conductor を起動し直しても、`--drift-reviewed` まで工程を起動しない
- `changed_paths`・`drift`: `touches` に当たるファイルだけを返す。fetch refspec が基のブランチを追跡しない clone（`remote.<名前>.fetch` を別のブランチだけにした作業場所）でも、取得の後の `<ref>` が取得したコミットを指し、待ちの `base_sha` と一覧の `ls-tree` が同じ参照を読む（`git fetch <remote> <branch>` にすると古い追跡参照を読むか参照が無くて落ちる）。`base_sha` が無いと null と warnings。待ち始める前に追跡参照が古かった作業場所でも、取得した後の参照が `base_sha` になる。リモート名が `upstream` の作業場所で同じ結果
- `end_row`: `row_ended` と `lane_row_ended` が同じトランザクションで 1 件ずつ

## 工程を始める口（`run`・`answering`）

満たす決定: 決定 2・決定 6、受け入れ条件 2・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Env.hold` | `run.py` | 新設の欄 | `Callable[[Any, str, stages.StageDef, Path], Optional[HoldResult]]`（既定は常に None） | 工程を始める前に呼ぶ口。待ちなら `HoldResult(held=…)`、開始を記録して確認が要れば `HoldResult(review=True, drift=…)`、開始を記録して続けてよければ `HoldResult(drift=…)`（待たなかった行は `drift=None`） |
| `Env.peek` | `run.py` | 新設の欄 | `Callable[[Any, str], Optional[HoldResult]]`（既定は常に None） | 書かずに今の待ちか確認待ちを読む口。`wait` と `next` の手の組み立てが使う |
| `wait` | `run.py` | 変更 | `(st, machine_id, timeout, sleep=…, *, clock=…, env: Optional[Env] = None)` | 作業が止まった後の手を `report(v, decide(v), env)` で組む。`env` が None なら今と同じ |
| `_cmd_wait` | `run.py` | 変更 | 変えない | `wait(st, a.machine_id, a.timeout, env=a.env)` で、CLI の入口（`gate/entry.py` の `run.with_env(a, lane_env(…))`）が入れた `Env` を渡す。今は `Env` を捨てて `report(v, decide(v))` を呼ぶので、渡さないと `peek` に届かない |
| `_start` | `run.py` | 変更 | 変えない | 工程のフェーズが `implement` で、これより前の `implement` の工程が 1 つも始まっていなければ `Env.hold` を呼ぶ。`held` なら工程を始めずに `_hold_report` を、`review` なら工程を始めずに `_drift_report` を返す。どちらでもなければ工程を始め、`drift` を結果の `metrics.drift` に載せる |
| `_drive` | `run.py` | 変更 | 変えない | `inline`（`ndf progress work`）で工程をまたいで流すとき、`_start` が `_hold_report`・`_drift_report` を返したら流しを止め、その結果を返す（`work.log` に残すだけにせず、スプリントの作業の結果として記録する） |
| `report` | `run.py` | 変更 | `(v: View, m: Move, env: Optional[Env] = None) -> dict` | 次の手が最初の `implement` の工程の開始で、`env.peek` が待ちか確認待ちを返せば、通常の次手（工程の起動）の代わりに `_hold_report`・`_drift_report` を返す。`next` を打ち直す手を作らない |
| `_cmd_next` | `run.py` | 変更 | `--drift-reviewed <理由>` を足す | 引数があれば、`Env` の口から `lane.review_drift` を呼んでから、今のとおり工程を始める |
| `_hold_report` | `run.py` | 新設 | `(v: View, s: StageDef, held: Hold) -> dict` | 入出力のコントラクトの形の結果（`next` を持たない） |
| `_drift_report` | `run.py` | 新設 | `(v: View, s: StageDef, drift: Optional[list[str]]) -> dict` | 入出力のコントラクトの確認の形の結果（`next` は `--drift-reviewed` 付きの `next`） |
| `lane_env` | `gate/answering.py` | 変更 | 変えない | `hold` に、`BaseRef`（`Git.remote()` とブランチ戦略の設定）を組む関数と `Git` を作る関数を、呼ばずに `lane.begin_implementation` へ渡す関数を入れる。`peek` に、行を読むだけの関数を入れる。`--drift-reviewed` の口に `lane.review_drift` をつなぐ |

- `_start` の「これより前の `implement` の工程が始まっていない」は、スナップショットの工程の並びで、この工程より前のフェーズ `implement` の行がどれも `pending` か `skipped` であることで決める（`light` で最初の工程が `implement` のときも同じ規則で当たる）
- `Env.peek` は DB だけを読み、Git も GitHub も読まない。`wait` の回数あたりの読み取りは、レーンの行の 1 回の読み取りだけ増える（計画の外のスプリントでは増えない）

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `lane_env` の `hold` | 計画の外のスプリント | None（今と同じ） |
| 同上 | `implementation_wait` が空・待ったことが無い | `row_implementing` を書き、`HoldResult(drift=None)` |
| 同上 | `implementation_wait` が空・待ちの行（`holding`）・変わったファイルが空 | `row_implementing` を書き、`HoldResult(drift=[])` |
| 同上 | `implementation_wait` が空・待ちの行・変わったファイルが空でないか読めない | `row_implementing` を書き、`HoldResult(review=True, drift=…)`。工程は始めない |
| 同上 | 開始を記録済み・確認待ち（`review` が真で `drift_reviewed` が偽。`drift` が null の行も含む） | 書かずに `HoldResult(review=True, drift=…)` |
| 同上 | 開始を記録済み・確認待ちでない（`review` が偽か `drift_reviewed` が真） | 書かずに `HoldResult(drift=記録の値)` |
| 同上 | `implementation_wait` が空でない | `HoldResult(held=…)`（待ちの行なら書かずに今の待ち） |

待ちが解けたことは `row_implementing` だけで読むので、起動し直しの `row resumed` → `next`、ラッパーの conductor がそのまま打つ `next`、オーガナイザーが打つ `next`、`work` のどの経路でも同じ表を通り、`drift` が届く。

### 単体テストの観点

- `standard` のスプリントで「計画」の手前で待ち、「要求と受け入れ条件」〜「ドキュメントレビュー」は待たない
- `light` のスプリントで最初の工程の手前で待つ。`drift` が空でないときは、工程の作業を起動しない（差し替えた作業の起動の呼び出しが 0 回）まま確認の結果を返し、`--drift-reviewed` の後に起動する
- 設計の工程で `ndf progress work` を起動した worker が「計画」の手前まで進んで待ちを記録した後、CLI の `ndf progress wait`（`gate/entry.py` の入口から `lane_env` の `Env` で打つ経路）が `held` を `next` 無しで返す（`work.log` を読まずに分かる）。`Env` を渡さない `wait` の呼び出しに戻すと、工程を起動する `next` を返して落ちる
- `row resumed` の後、`next` の前に止めたスプリントで、次の `next` が `drift` を返す。ラッパーの conductor が起動し直しより先に `next` を打っても `drift` が返る
- `drift` が null で記録された行で `next` を打ち直しても、起動し直した conductor が `next` を打っても、確認の結果を返して工程の作業を起動しない。`--drift-reviewed` の後にだけ起動する
- `Env.hold` の既定（計画の外）で工程の順が今と同じ

## マシンの数と生死の判定（`machine`・`liveness`）

満たす決定: 決定 4・決定 11、受け入れ条件 3、I3・I8

席を返したかは、レーンのステートマシンの `row_parked`・`row_seated` だけで読む（`LaneRow.parked`）。数え方と生死の判定はこの記録を読み、スプリントを読む CLI（`progress wait --timeout 0`）を数えるたびに打たない。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `lanes_of` | `lane/machine.py` | 変更 | 変えない | レーンの「実行中の行を持つ」を、実行中の行のうち `parked` でなく、今の着席の待ちを持たない（`holding` で `held_seq > seated_at_seq` でない）行があるかで決める。数えないのは、席を返した行と、今の着席の間に待ちを記録した行（ラッパーが動いていれば `count` が一覧の側で数え、止めた後は `parked`）だけである。席を取り直した行（`row_seated` の後、`next` が実装の開始を記録するまで `holding` が真のまま）は、ラッパーが一覧にまだ現れなくても記録だけで予約として数える。`count` はレーンの名前とラッパーの名前の和集合で数えるので、ラッパーが現れても 2 重に数えない。同じ計画（`admitted` の `own`）も他の計画（`read_lanes` が読む計画）も、この関数の同じ規則で数える |
| `held_seats` | `lane/machine.py` | 変更 | 変えない | ハブへ渡す席を、`lanes_of` と同じ規則で決める（席を返した行のレーンを渡さない） |
| `judge` | `lane/liveness.py` | 変更 | 変えない | 実行中の行が `parked` なら、回答が席を返した後に載っていれば今の `RESUMING`、`holding` なら `HELD`、どちらでもなければ（承認の回答を待つ）`WAITING_ANSWER` を返す（最後の `row_parked` の理由が `launch_failed` でも同じ規則で読む）。席を返していない行が `holding` なら `HELD`（`stall: false`）を返す。どちらもスプリントもラッパーも読まない |
| `wrapper_table` | `lane/liveness.py` | 新設 | `(lanes: Mapping[str, list[LaneRow]], probes: Probes) -> Optional[Mapping[str, Mapping[str, Any]]]` | 観測で 1 回だけラッパーの一覧（`probes.wrappers()`）を読む。読むのは、`judge_lanes` が判定に要る行（今の `needs`）か、`_parks` が要る行（`parked` でない実行中の行のうち `holding` か `awaiting_next` のもの、と `stopping` の行）が 1 つでもあるときだけで、どちらも無ければ空の一覧を返して読まない。読めなければ None |
| `judge_lanes` | `lane/liveness.py` | 変更 | `(lanes, probes, table: Optional[Mapping[str, Mapping[str, Any]]]) -> dict[str, Liveness]` | ラッパーの一覧を自分で読まず、`wrapper_table` の値を受け取る。`parked`・`holding` の行はスプリントの待ちを読む対象から外す（スプリントを読む行の数は今以下になる）。同じ観測で `_parks` も同じ `table` を受け取るので、`holding` の行だけが残る計画でもラッパーの生死を `_parks` が読め、一覧の読み取りは観測あたり高々 1 回のまま |
| `stopping` | `lane/liveness.py` | 新設 | `(row: LaneRow, name: str, table: Optional[Mapping[str, Mapping[str, Any]]]) -> bool` | 席を返した行の停止が済んでいないか。`parked` で `park_stop` が真で、一覧が None（読めない）か一覧にそのレーンのラッパー `name` が動いて載るとき真。停止の確定はラッパーが一覧から消えたことで読み、事象を足さない |
| `gate_wait` | `lane/liveness.py` | 新設 | `(row: LaneRow, sprint_wait: Mapping[str, Any], requests: Sequence[Request], defs: StageDefs) -> Optional[str]` | 席を返す前の行が今の承認ゲートの回答を待ち、その質問をオーガナイザーが計画のリクエストとして受けて回答できるかを決め、できればそのリクエストのキーを返す。条件は (1) スプリントの待ちの結果が `status: gate` で、`metrics` の工程とステップの定義が `gate`（設計承認・リリース承認）を持ち、`metrics.gate.id`（今のゲートの質問の id）がある (2) 回答が載っていない（`answered_seq` が今の着席より古い） (3) 計画の開いた（閉じていない）リクエストのうち、ゲートの種類に応じた次のものが 1 つある。設計承認ゲートは、種類 `lane_question`・このレーン・中身の `metadata` が `source: ndf-gate:1`・`gate` が今の質問の id・`sprint` が行のスプリントの ID のもの。リリース承認ゲートは、種類 `lane-release`・`id` が `asking.request_id` の値（`lane-release-<今の質問の id>`）・`sprint_state` が行のスプリントの ID のもの（`lanes release` が回答と同じトランザクションで閉じるので、開いていれば未回答）。工程名だけ・レーンだけで照らさないので、変更要求の後に流し直した質問（id が変わる）や、前の行・ゲートでない質問のリクエストでは止めない。どれかが欠ければ None（止めない）。`judge_lanes` が今読むスプリントの待ちの結果を使い、新しい読み取りを足さない |

### 単体テストの観点

- 待ちの行のレーンは `count` に入らず、ラッパーが動いていれば一覧の側で数える（止めるまでは数える）
- 上限 1 のマシンで、計画 P の待ちの行が `row_seated` を書いた直後（ラッパーがまだ一覧に無い）に、別の計画 Q の行を始める手が `admitted` を通ると上限で待つ（`holding` の行を一律に外すと Q が起動して 2 本になり落ちる）。ラッパーが一覧に現れた後も数は 1
- 席を返した行のレーンは、回答が載った後（`resuming`）も `row_seated` まで `count` に入らない。他の計画の行も同じ規則で数え、数えるときに差し替えたスプリントの読み取りの呼び出しが 0 回
- `gate_wait`: `status: gate` でも、承認ゲートでないステップ（質問の待ち）・リクエストがまだ計画に無い・閉じたリクエストしか無い行は None。同じレーンに開いた `lane_question` があっても、`metadata` の無い質問・`gate` が今の質問の id と違う質問（前のゲートや流し直す前のもの）・`sprint` が前の行のスプリントの質問では None。リリース承認で、同じ工程の流し直す前の質問の `lane-release` が開いていても、今の質問の id のリクエストが無ければ None
- 席を返した承認の待ちの行は、ラッパーが無くても `WAITING_ANSWER` で止まりにならない。`HELD` と `WAITING_ANSWER` は `STALL_KINDS` に入らない
- 計画の実行中の行が、席を取り直した後の実装の待ちの行（`holding`・`parked` が偽）1 本だけで、そのラッパーが落ちているとき、観測が `probes.wrappers()` を 1 回だけ呼び、`_parks` が `vacant` を出す（`judge_lanes` の対象だけで一覧を読むかを決めると一覧を読まずに落ちる）。`judge_lanes` の対象の行もある計画でも、一覧の読み取りは 1 回

## 起動の手と割り当て（`plan/run`・`plan/run_decide`）

満たす決定: 決定 4・決定 8・決定 11・決定 13、受け入れ条件 3・4・12・16、I7・I8・I11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_halts` | `plan/run.py` | 変えない | 変えない | 最後の行が終わったレーンだけを止める先にする（今のまま）。待ちの行は `_parks` で扱う |
| `_parks` | `plan/run.py` | 新設 | `(view, rows, table: Optional[Mapping[str, Mapping[str, Any]]], verdicts) -> list[tuple[str, str, str, ParkAct]]` | 席を返す先の（レーン, 行, スプリント, 手の中身）。理由は `rules.park_reason`（`gate_key` は `gate_wait` の値、`alive` は観測で `liveness.wrapper_table` が 1 回だけ読み、`judge_lanes` と共有した一覧。一覧が None なら None）だけで決め、ここで条件を書き足さない。開発ループの行（`kind: loop`）は対象にしない（今のとおり動く）。(1) `held`: 今の着席の間に待ちを記録した行 (2) `gate`: 今の承認ゲートのリクエストが開いた行（実装を始めた行も含む） (3) `vacant`: 待ちの行（実装の待ちか回答の後の next 待ち）でラッパーが動いていない行。起動し直した conductor のラッパーは、`next` が新しい待ちを記録するまで (1) に当たらないので止めない。ラッパーの一覧を読めなければ (3) を決めない。`ParkAct` には観測した着席の番号・`gate` なら質問の id（`metrics.gate.id`）を載せる。理由が `vacant` で、行が席を取り直した後（`seated_seq > 0`）に何も進めていなければ（`seat_progressed` が偽）、`charge` に `seat_fail`（その着席を開いた続きの手の失敗のキー）を載せる。加えて (4) 停止の続き: 席を返した行で `liveness.stopping` が真の行に、最後の `row_parked` を写した手を出す（`park_lane` の手順 1 の再開の経路を通る） |
| `park_lane` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, lane: str, row: str, sid: str, act: ParkAct) -> dict` | 下の「席を返す手順」で、先に再検証と `row_parked` を 1 つのトランザクションで書き、その後にラッパーを止める。再検証で外れれば何もせず `{"ok": false, "stale": true}`（失敗に数えない）。止める直前に質問の id が変わっていれば `row_unparked`（`gate_changed`）で戻して `stale` を返す。止められなければ `row_unparked`（`stop_failed`）で席を返したことを取り消し（`row_seated` は書かない）、`halt_failed` と同じ形で失敗を返す |
| `snapshot_of` | `plan/run.py` | 変更 | 変えない | 写しに `parks`（`_parks`）・`parked`（席を返した行のスプリントの ID）・`released`（`parked` で停止が済み（`liveness.stopping` が偽）、`holding` で `implementation_wait` が空で、`awaiting_next` でない行の（レーン, 行, スプリント, `parked_seq`, `held_seq`））と `idle`（`idle.current` の項目の番号の並びと `listed_seq`）と `free`（空きの数）を載せる。`released` は `row_implementing` が書かれるまで残る（`row_resumed` を見ない）。`answered` の項目は（レーン, 行, スプリント, 起動のキーの番号, 回答の番号）で、2 つの番号を別々に運ぶ。`stopping` が真の行は `answered` に載せない。起動のキーの番号は、席を返したかによらず `max(answered_seq, parked_seq)`（回答の後に最後に席を返した番号。一度も席を返していない行では今のとおり `answered_seq`）。席を取り直して `parked` が偽になっても番号が変わらないので、取り直した直後の読み直しで同じ回答の手が新しいキーで出ない（`RunState.launched` の照合が効く）。席を返し直すと `parked_seq` が進んで新しいキーになる。回答の番号は `answered_seq`（最後の `row_answered` の番号）である |
| `launch_lane` | `plan/run.py` | 変更 | 変えない | 続きの手（`continues`）のうち、スプリントが写しの `parked` にあるもの（回答の後の next・待ちの起動し直し・中止）は `seated=True` にせず、行を始める手と同じく `machine.admitted` を通す。通ったら、同じ `admit.lock` の中で、ラッパーを起動する前に `lane.seat`（`launch` は手のキー、`fail` は `Launch.fail_key`）で `row_seated` を書いてコミットし（席を先に確定する）、その後に起動の入力を渡してラッパーを起動する。起動に失敗したら `lane.park` で席を返す。手は理由 `launch_failed`・`seat` は書いたばかりの着席の番号・`stopped` は偽・`request` と `gate` は前の `row_parked` の値で、`charge` を付けない（ここで数えるので、次の観測の `vacant` で数え直さない）。理由 `launch_failed` の再検証は理由ごとの条件を持たず、共通の外れ条件（`running` でない・既に `parked`・着席の番号が違う）だけを見る（起動の前に `row_seated` を書いたので、`held` や `gate` の条件で確かめ直すと必ず外れる）。今の起動の失敗と同じく打ち直しの回数を数える。回数は `Launch.fail_key` で数えるので、席を返すたびに起動のキーが変わっても同じ待ちの失敗として積もり、`policy.attempts` で打ち切って `launch_failed` を 1 回知らせる（決定 13）。席を返していない行の続きの手は今のとおり `seated=True` |
| `_launches` | `plan/run_decide.py` | 変更 | 変えない | `parks` から席を返す手（`Launch(park=…)`）を作り、終わったレーンを止める手と同じ位置に並べる。`released` から起動し直しの手（`Launch(resume=sid, seq=parked_seq, hold=held_seq)`）を作り、回答の後の next の手（`Launch(resume=sid, seq=<起動のキーの番号>, answered=<回答の番号>)`）と同じ位置に並べる。失敗のキー（`Launch.fail_key`）が `RunState.failed` にある手は出さない（`_candidates` の `_skip` の照合のうち `failed` だけを `fail_key` で照らし、`launched`・`tried` は今のとおり `key` で照らす）。同じスプリントの回答の後の next があれば起動し直しの手を作らない（I10。回答の後の `next` も実装の境では `Env.hold` を通る）。中止の手も含め、続きの手は 1 行に 1 つ。席を取り直した行（`parked` が偽で `seated_seq > 0`）では、キーが今の着席を開いた手のキー（`LaneRow.seat_launch`）と同じ続きの手を作らない。席を取り直した後の回答の後の next は、起動のキーの番号（`max(answered_seq, parked_seq)`）が変わらないので、作ると `awaiting_next` が残る間ずっと「起動したまま待つ手」になり、既存の `_expire_launched` が期限（`Policy.up`）で起動の失敗と数え、同じキーで打ち直して動いている conductor を置き換える。作らないので、その着席のマーカーは次の `_expire_launched` で待ちの候補から外れて失敗に数えずに消える。その着席が何も進めずに終わったかは、ラッパーの消失を観測した `vacant` の手の `charge` で 1 回だけ数える（`seat_launch` と同じ着席の `seat_fail`）。conductor が動いていて `next` が遅いだけなら、ラッパーが一覧にある間は `vacant` にならず、何も数えない |
| `_assignable` | `plan/run_decide.py` | 新設 | `(snap: Snapshot, acts: list[Launch], failed: Mapping[int, int]) -> Optional[Assign]` | 手が 1 つも無く、計画が `running`、`free >= 1` なら、一覧のうち `failed` に無い最初の課題の割り当ての手を返す。手の `skipped` に、今の写しの `listed_seq` で飛ばした（`failed` にある）課題を一覧の順で載せる |
| `Launch.fail_key` | `plan/run_decide.py` | 新設 | `@property -> str` | 打ち直しの回数と打ち切りを数えるキー。席を返した行の続きの手では、席の世代（`parked_seq`）に依らず、手が応える記録の番号で決める。回答の後の next は、席を返したかによらず `next:<スプリント>:a<回答の番号>`、待ちの起動し直しは `resume:<スプリント>:h<held_seq>`、席を返す手は `key`（`park:<レーン>/<行>:<着席の番号>`。止められずに取り消しても着席は変わらないので同じ値）。ほかの手は `key` と同じ（今のまま）。同じ着席の 2 回の起動を防ぐ `RunState.launched` は今のとおり `key` で持つ |
| `_count` | `plan/run_decide.py` | 新設 | `(state: RunState, lane: str, key: str, detail: str, policy: Policy, *, final: bool, kind: str) -> RunState` | 今の `_fail` の本体を、数えるキーと知らせの種類を引数で受ける形にしたもの。回数（`attempts`）・`failed`・`final`・知らせの閉じた後の戻し（`_reopen_closed` の照合）を `key` で数える |
| `_fail` | `plan/run_decide.py` | 変更 | 変えない | `_count(state, act.lane, act.fail_key, …)` を呼ぶ。席を返す手（`act.park`）は止める手（`act.halt`）と同じく `final`（尽きたら打ち直さない。閉じても数え直さない）で、種類は `halt_failed`。ほかの手は今のとおり `launch_failed` |
| `_charge` | `plan/run_decide.py` | 新設 | `(state: RunState, lane: str, fail_key: str, detail: str, policy: Policy) -> RunState` | 席を返す手の `charge` を数える。`_count(…, final=False, kind=LAUNCH_FAILED)`。打ち切ったら `launch_failed` で 1 回知らせ、知らせのリクエストが閉じたら数え直す（続きの手の失敗と同じ扱い） |
| `_after_observe` | `plan/run_decide.py` | 変更 | 変えない | 全行が終わったとき（`snap.complete`）も、一覧に `failed` に無い課題が残っていれば `Complete` を打たない。`_assignable` が手を返せば割り当てを、返さなければ（`free` が 0）`Sleep` を打つ。残っていなければ `Complete`（`skipped` に `RunState.assign_failed` の課題を載せる）へ進み、写しの判断は打つかの目安にとどめ、閉じてよいかは `complete_plan` が排他の中で決める |
| `complete_plan` | `plan/__init__.py` | 変更 | `(conn, pid: str, skipped: Sequence[int] = ()) -> bool` | `lock_plan` を取って最新の計画とレーンを読み直し、今の条件（全行の終わり・回答していないリクエストが無い）に加えて次の 2 つを満たすときだけ `plan_completed` を書く。(1) `idle.current` から `skipped` を除いた一覧が空 (2) 計画が一度でも `idle_listed` を持てば、最後の `idle_listed` の番号が最後の `lane_row_ended` の番号より新しい（行の終わりを受けたオーガナイザーの作り直しが済んだ）。満たさなければ書かずに偽を返し、耐久ワークフローは読み直して割り当てか `Sleep` へ戻る。`idle_listed` を持たない計画は (2) を見ず、今のとおり閉じる |
| `refresh` の書き込み | `plan/idle.py` | — | — | `idle_listed` は `lock_plan` の中で、計画が `running` か `paused` のときだけ書く。閉じた計画には書かず `stopped`・`precondition` を返す。`complete_plan` と同じ排他なので、作り直しが先なら完了が (1) で外れ、完了が先なら作り直しが断られる。完了が先になるのは (2) により、最後の行の終わりの後の作り直しが済んで一覧が空だったときだけである |
| `assign_idle` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, issue: int, listed_seq: int, skipped: list[int]) -> dict` | 下の手順で割り当てで足すレーンを書く。結果は `{"ok": true, "lane": …}`・`{"ok": false, "stale": true}`・`{"ok": false, "detail": …}` のどれか |
| `decide` | `plan/run_decide.py` | 変更 | 変えない | `_candidates` が空のとき `_assignable` を見る。`stale` は失敗に数えず `Observe` へ戻る。断られたら、その課題を `RunState.assign_failed` に（課題, `listed_seq`）で入れ、同じ課題で 1 回だけ `launch_failed` の形の知らせを作る。席を返す手が通り、手が `charge` を持てば、`_charge` でその失敗のキーを 1 回数える（起動し直した conductor が待ちも実装の開始も回答の消費も書かずに終わった。決定 13） |

席を返す手順（`park_lane`）:

1. トランザクションを始め、`lock_plan` を取り、行を読み直す。行が既に `parked` で、`park_seat` と `park_reason` が手の `seat`・`reason` と同じなら、同じ着席の停止の再開として扱い、何も書かずにコミットして手順 3 から続ける（`park_stop` が偽なら止めるものが無いので `{"ok": true}`）。耐久ステップが `row_parked` のコミットの後・停止の前に中断され、同じ引数で打ち直されたときと、`_parks` が出した停止の続きの手のどちらもこの経路を通る。それ以外は、次の表のどれかに当たれば書かずに `stale` を返す

| 当たる手 | `stale` にする条件 |
| --- | --- |
| どの理由でも | 行が `running` でない・既に `parked`・今の着席の番号（`seated_at_seq`）が手の `seat` と違う |
| `held`・`gate` | `rules.park_reason(row, gate_key, None)` が手の理由と違う値を返す。`gate_key` は `lane.regate` が作る |
| `vacant` | `rules.park_reason(row, gate_key, False)` が `vacant` でない。`alive` の `False` は観測の値で、排他の中でラッパーを読み直さない |
| `launch_failed` | 理由ごとの条件を持たない（上の行だけ） |

   `park_reason` に当てると、`held` は `holding` で `held_seq > seated_at_seq` のときだけ通る（観測の後に `next` が実装を始めた行は `holding` が偽になって外れる）。`gate` は `regate` の 4 条件をすべて満たすときだけ通り、`implementing` では外さない（リリース承認を待つ行は実装を始めた後にあり、外すと毎回 `stale` になってリリース待ちの行が枠を返せない）。`vacant` の手がコミットまで進むのは、次の 3 つがそろうときだけである。(i) 着席の世代: 今の着席の番号が手の `seat` と同じ（共通の行。観測の後に `launch_lane` が席を取り直していない） (ii) 今の待ち: `holding` か `awaiting_next` が真で、今の着席の間に待ちを記録しておらず（`held_seq <= seated_at_seq`。記録していれば `held` が先に当たり、次の観測で `held` の手になる）、`regate` が None（承認の待ちなら `gate` が先に当たる） (iii) ラッパーが無い: 手が運ぶ観測の値（`alive=False`）。起動し直した conductor が `next` の前に落ちた行は (i)〜(iii) をすべて満たすので、`stale` にならずに席を返し、次の観測で新しいキーの続きの手になる
2. 同じトランザクションで `row_parked`（`seat` は手の着席の番号・`stopped` は 3 で止める予定か・`gate` は手の質問の id）を書いてコミットする。以後、行は `parked` で、`begin_implementation` は手順 3 の排他の中で `parked` の行に `row_implementing` を書かない（今の待ちを返す。下の「`next` との競合」）
3. コミットの後でラッパーが動いていれば、止める直前に理由が `gate` なら `regate` を読み直す（行は `parked` になったが、着席と回答とリクエストの条件は同じに読める）。None なら（質問の id が手の `gate` と違う・リクエストが閉じた・回答が載った）、止めずに `lane.unpark`（`gate_changed`）で戻して `stale` を返す（変更要求の後に流し直した質問がまだ計画へ届いていないうちに、古いリクエストを根拠にラッパーを止めない）。同じなら `launch.stop` で止める
4. 止められなければ、`lane.unpark`（`stop_failed`）で席を返したことを取り消し、`halt_failed` の形で失敗を返す。`row_seated` は書かない（着席を新しくすると `held_seq > seated_at_seq` が崩れ、動いたままのラッパーが `vacant` にも当たらず、待つレーンが枠を占め続ける）。着席の番号が変わらないので、次の読み直しで `park_reason` が同じ理由を返し、同じキーの席を返す手になる。`_fail` が手のキーで回数を数え、`policy.attempts` 回続けて止められなければ打ち直さずに `halt_failed` を 1 回知らせる（決定 13）

`next` との競合: `row_parked` と `row_implementing` はどちらも計画の排他の中で行を読み直してから書くので、先に書いたほうだけが通る。`park_lane` が先なら、止める前のラッパーが `next` を打っても実装を始めず（`parked` の行は `begin_implementation` の (a) と同じく今の待ちを返す）、止めた後の起動し直しの手（`released`）が席を取り直してから実装を始める。`next` が先なら、手順 1 の `implementing` の再検証で外れ、実装に進んだラッパーを止めない。承認の待ち（`gate`）の行で `park_lane` の後・停止の前に回答が載り、止める前の conductor が回答を消費しても、状態はスプリントのステートマシンにあり、席を返した行は `awaiting_next`（回答の後の next）として上限の判定を通して起動し直す（回答の消費の確かめは下の「起動し直しの入力」）。

`assign_idle` の手順（1 つのトランザクション）:

1. `lock_plan` を取り、最新の計画とレーンを読む
2. 計画が `running` でない、`idle.current` から手の `skipped`（観測した一覧の世代で割り当てに失敗した課題。耐久ステップの引数で運ぶので、再開しても同じ値を読む）を除いた先頭が `issue` でない、最後の `idle_listed`・`idle_adjusted` の番号が写しより新しい、のどれかなら書かずに `stale` を返す（観測の後の `idle set --pin`・`plan pause` を拾う）
3. 1 で読んだ計画から、名前（`i<番号>`、重なれば `i<番号>-<n>`）と `after`（同じリポジトリの終わっていないレーンのすべて）を組み、`add_lane(..., actor="workflow", idle=issue)` で書く。`add_lane` は同じトランザクションの中で呼び、排他を取り直さない
4. 計画の決まりで断られたら `{"ok": false, "detail": …}`

- 席を返した行の続きの手（起動し直し・回答の後の next・中止）は、行を始める手と同じくマシン全体の上限の判定（`machine.admitted`）を通す。`admitted` が数える他のレーンのうち、席を返した行のレーンは `lanes_of` が数えないので、上限 1 で席を返した 2 レーンに回答が載ると、先に通った 1 本が起動して `row_seated` を書き、もう 1 本はその動いているラッパーを数えて上限で待つ（互いを数えて両方が止まることはない）
- 入力は行の種類で分ける。開発ループの行（`kind: loop`）は今の `resume_commands` のまま `ndf lanes row resumed <スプリント>` → `ndf loop get <ループ>` で、`progress next` を打たない（ループのスプリントへ `next` を打つと行が終わる。`_going_on`）。開発ループの行には席を返す手を作らない（`_parks` が `kind: loop` の行を外す。開発ループの行は今のとおり動く）ので、`--answered` も要らない。スプリントの行（`kind: sprint`）は `ndf progress next` → `ndf lanes row resumed <スプリント> --answered <Launch.answered>` の順にする（今の `launch.py` は逆で、番号も渡さない）。`--answered` に渡すのは回答の番号（最後の `row_answered` の番号）で、起動のキーの番号（`Launch.seq`。席を返した行では `parked_seq`）ではない。`seq` を渡すと (1) の照合が外れ、回答を消費しても `row_resumed` が書かれず `awaiting_next` が残る。`row resumed` は無条件に書かない。`record_resumed` は計画の排他の中で行を読み直し、(1) 渡した番号が行の最後の `row_answered` の番号と同じで (2) その回答が消費された（`row_answered` が `gate` を持てば、スプリントがその質問の id の承認ゲートでもう待っておらず（`asking.waiting` が None か別の id）、`run.decide` が回答の消費（CONSUME）を返さない。`gate` を持たない回答は (1) だけ）ときだけ `row_resumed` を書く。(2) の確かめはスプリントを読むので、`plan/cli.py` の `row resumed` が `gate.asking.waiting`・`run.decide` で求める関数を作って `record_resumed(conn, sprint_id, actor, answered, consumed)` に渡す（`lane` は `run`・`gate` を import しない）。消費を確かめられなければ書かずに `stopped`・`precondition` を返し、`next` に `ndf progress next <スプリント>` → 同じ `row resumed` を載せる（`/goal` の下の conductor が打ち直す）。誰が消費したかは問わない（止める前の conductor が先に消費した行でも、起動し直した `next` の後に (2) が真になる）。`next` が起動済みの作業との排他などで回答を消費せずに戻っても `awaiting_next` は消えず、conductor が応答を終えてラッパーが無くなれば `_parks` の (3) で席を返し、新しいキーで起動し直す。待ちの起動し直し（回答の無い `released`）の入力は `--answered` を渡さず、`row resumed` は今のとおり起動の試行の印として書く。`next` の前に conductor が落ちても `row_resumed` が無いので、回答の後の next 待ち（`awaiting_next`）のまま残り、ラッパーも動いていないので次の読み直しで席を返す手（止めずに `row_parked`）を受け、その後の読み直しで新しいキーの続きの手になる。`next` を 2 回打っても `store.run_lock` の中で同じ手を返す。`row resumed` は起動の試行の印で、待ちを解かない
- 続きの手のキーは今のとおり `next:<スプリント>:<seq>` で、席を返した行の `seq` は `parked_seq` である。席を返すたびに番号が進むので、同じ着席の待ちを 2 回起動せず（`RunState.launched`）、起動し直した conductor が落ちて席を返し直した後は新しいキーで起動できる（期限切れを待たない）。打ち直しの回数はキーでなく `Launch.fail_key` で数える（キーは席を返すたびに変わるので、キーで数えると同じ待ちの起動の失敗が上限に届かない）
- 席を返す手（`park_lane`）は、席を返していない行と、停止の済んでいない席を返した行（`liveness.stopping` が真）にだけ作る。後者は停止の続きの手で、中身は最後の `row_parked` の `reason`・`seat`・`request`・`gate` を写し、キーも失敗のキーも元の手と同じ（`park:<レーン>/<行>:<着席の番号>`）なので、止められない回数は元の手と合わせて数える。停止の済んだ行（ラッパーが一覧から消えた行）には打ち直さない
- 停止の済んでいない席を返した行（`stopping` が真）は、`released`・`answered`・中止の続きの手の候補に入れない。元のラッパーが動いたまま新しいラッパーを起動しない（同じレーンの名前を置き換えて動いている conductor を失わない）ためで、ラッパーが一覧から消えた次の観測で候補に入る。一覧を読めない観測でも候補に入れない（安全側）
- 割り当ての手の後の次の読み直しで、足したレーンは `wakeable` に入り、行を始める手（上限の判定を通す）になる。割り当てそのものは上限の判定を通さない（席を使わない）
- `RunState.assign_failed` は観測ごとに空にしない（`tried` と違う）。今の写しの `idle` の `listed_seq` が失敗を記録したときより新しい（一覧を作り直したか、その課題の調整が書かれた）項目は飛ばすのをやめる。飛ばす課題は割り当ての手の `skipped` で `assign_idle` へ渡し、耐久ステップの確かめ直しも同じ課題を飛ばす。知らせ済みの課題は `RunState.reported` で 2 回知らせない

席を返した行の回答と再開（承認の種類ごと。どれもラッパーへの打ち込みを使わない）:

| 承認の種類 | 回答の記録 | レーンへの再開の通知 | 起動し直した後の取り込み |
| --- | --- | --- | --- |
| 設計承認ゲート（`lane_question`） | `ndf answer <スプリント> --gate <質問の id> --result … --by organizer --key …`（スプリントの回答の事象） | `ndf lanes answered <スプリント> --answer <回答> --gate <質問の id>`（`row_answered`。`record_answer` が `gate` を中身に載せる）。続けて `ndf lanes request close` | 起動の入力の `ndf progress next` が回答を消費する（`run.decide` の CONSUME） |
| リリース承認ゲート（`lane-release`） | `ndf lanes release <計画> <リクエスト>... --result … --version … --release …`（計画のリクエストの回答。同じトランザクションでリクエストを閉じる） | リクエストごとに `ndf lanes answered <sprint_state> --answer <released か rejected> --gate <質問の id>` | 起動の入力の `ndf progress next` の入口（`gate/entry.py` の `_took` → `release.take_reply`）が記録を承認ゲートの回答（`by: organizer`）として書き、`version`・`release_pr` を文脈へ写してから流す |

`_launches` の回答の後の next の契機は今のとおり `row_answered`（`answered_seq > resumed_seq`）だけで、`ndf answer` や `lanes release` だけでは起動し直さない。`--gate` は任意で、承認ゲートでない質問の回答には付けない（席を返すのは承認ゲートの行だけなので、席を返した行の回答には必ず付く）。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Assign` | 型（`issue`・`key`・`listed_seq`・`skipped`） | `plan/run_decide.py` | — |
| `ParkAct` | 型（`reason`（`held`・`gate`・`vacant`・`launch_failed`）・`request`・`gate`（質問の id）・`seat`・`stopped`・`charge`（数える失敗のキー）） | `lane/types.py`（`lane.park` の引数なので `lane` に置き、`plan/run_decide.py` が import する。`lane` から `plan` への import を作らない） | — |
| `Launch.answered`・`Launch.hold` | 欄（回答の番号・待ちの番号。`seq` と別に運ぶ） | `plan/run_decide.py` | 0・0 |
| `Snapshot.parks`・`Snapshot.parked`・`Snapshot.released`・`Snapshot.idle`・`Snapshot.free` | 欄 | `plan/run_decide.py` | 空・空・空・None・None |
| `Snapshot.answered`・`Snapshot.released` の項目 | 形（`answered` は（レーン, 行, スプリント, 起動のキーの番号, 回答の番号）、`released` は（レーン, 行, スプリント, `parked_seq`, `held_seq`）） | `plan/run_decide.py` | — |
| `Launch.park` | 欄（`ParkAct`）。キーは `park:<レーン>/<行>:<着席の番号>` | `plan/run_decide.py` | None |
| `ROW_PARKED`・`ROW_SEATED`・`ROW_UNPARKED` | 定数（事象の種類） | `lane/types.py` | `"row_parked"`・`"row_seated"`・`"row_unparked"` |
| `RunState.assign_failed` | 欄（（課題, `listed_seq`）の並び） | `plan/run_decide.py` | 空 |
| 割り当てで足すレーンの名前 | 規則 | `plan/run.py` | `i<課題の番号>`、重なれば `i<番号>-2`… |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 割り当てとオーガナイザーの `lane add`・`idle set`・`plan pause` | 計画のステートマシン | `assign_idle` の手順 1〜3 を 1 つのトランザクションにし、`lock_plan` の中で計画の状態と一覧の先頭を確かめ直す | 割り当ては `lane_added` 1 件で、課題が計画の行に入るので、再開しても同じ課題を 2 回足さない（手順 2 の確かめで外れる） |
| 割り当てとほかの計画の起動 | マシンの枠 | 割り当ては席を使わない。行の開始は今の `admit.lock` | 次の読み直しで数え直す |

### 単体テストの観点

- 待ちの行と承認の待ちの行の席を返す手が、行を始める手より前に出る。席を返した後の読み直しでは打たない
- 観測の後、`park_lane` の前に同じ行の `next` が `row_implementing` を書くと、`park_lane` は `stale` でラッパーを止めず `row_parked` も書かない。逆に `row_parked` の後、停止の前に止める前のラッパーが `next` を打つと、実装を始めず待ちを返し、起動し直しの後に始める（排他の待ち合わせをテストで作る）
- 実装を始めた行がリリース承認（`lane-release`）を待ち、今の質問の id のリクエストが開いていると、`park_lane` は `stale` にならず `row_parked` を書いてラッパーを止める。リリース待ちの行だけで全枠が埋まった計画でも、次の読み直しで一覧の先頭が割り当てられる（`implementing` で外すと毎回 `stale` になって落ちる）。`held` の手だけが、観測の後に実装を始めた行で `stale` になる
- 観測の後・`park_lane` の前に、同じ着席の間に承認の対象が変わって質問の id が更新され、前の質問のリクエストがまだ開いている写しで、`park_lane` は `stale` で書かず止めない。`row_parked` のコミットの後・停止の前に `current_gate()` が別の id を返すと、`row_unparked` で戻して止めない（差し替えた停止の呼び出しが 0 回）
- `row_parked`（`stopped: true`）のコミットの後・停止の前に耐久ステップを中断させ、同じ引数で `park_lane` を打ち直すと、`stale` にならずにラッパーを止める（「既に `parked`」で外すと動いたままのラッパーが残って落ちる）。耐久ワークフローを起動し直した新しい実行でも、観測が `stopping` の行に停止の続きの手を出して止める。止まるまでの観測で、その行に回答が載っていても `answered` の続きの手を出さない
- ラッパーを止められないと `row_unparked` が書かれ、`row_seated` は書かれず、`seated_at_seq` が変わらない。動いたままのラッパーの行は次の観測で同じキーの席を返す手になり、止められれば席を返す。`policy.attempts` 回続けて止められなければ `halt_failed` を 1 回知らせ、打ち直さない（`row_seated` で戻すと次の観測で手が出ずに落ちる）
- 起動し直した conductor のラッパーが `next` を打つ前の読み直しで、席を返す手を打たない（`released` にも `parks` にも入らない）。その conductor が `next` の前に落ちると、席を返す手（止めない）を経て新しいキーで起動し直す
- `implementation_wait` が空になった待ちの行の起動し直しが 1 回だけで、上限なら待ちに置かれる。`after` を満たしても `touches` が未確定の割り当てたレーンは起動し直さず、`lanes set` で `touches` だけを合わせた後の読み直しで起動し直す。回答の後の next も、席を返した行なら上限なら待ちに置かれる
- 上限 1 で席を返した 2 レーンに回答が載ると、1 本が `row_seated` を書いてから起動し、もう 1 本は上限で待ち、1 本目の行が終わった後に起動する
- 席を返した承認の待ちの行の回答の後の next で、`parked_seq` と回答の番号が違う（回答が席を返した後に載った）とき、起動の入力が `--answered <回答の番号>` を渡し、回答を消費した後の `row resumed` が `row_resumed` を書いて `awaiting_next` が消える。設計承認とリリース承認の両方で確かめる（`Launch.seq` を渡すと書かれずに落ちる）
- 起動の失敗 1 回で、失敗のキーの回数が 1 だけ増える（`launch_failed` の手の `row_parked` が再検証で外れずに書かれ、次の観測の `vacant` で数え直さない）
- 回答の後の next で席を取り直した後、起動した conductor のラッパーが一覧に載ったまま `next` を打たずに `Policy.up` を超える回数の観測を続けても、起動の失敗に数えず、同じ回答の手でラッパーを置き換えない（`_expire_launched` の期限に任せると、期限の後に失敗を数えて置き換えて落ちる）。その後ラッパーが消えると `vacant` で席を返し、`charge` が失敗のキーを 1 回だけ数える
- 回答の後の next で席を取り直した直後の読み直しで、同じ回答の手が出ない（`parked` が偽になって起動のキーの番号が変わると、`launch.replace` が動き始めた conductor を置き換えて落ちる）
- 待ちの起動し直しの起動が続けて失敗すると、席を返すたびに起動のキーが変わっても `Launch.fail_key` で数え、`policy.attempts` 回で打ち切って `launch_failed` を 1 回知らせ、それ以上ラッパーを起動しない（キーで数えると上限に届かず落ちる）。起動し直した conductor が待ちも実装の開始も回答の消費も書かずに終わり、`vacant` で席を返し直すことが続いても同じキーで数えて打ち切る。conductor が待ちを書いた着席の後の `vacant` は数えない。知らせのリクエストを閉じると再び打つ
- 起動し直しで `row_seated` を書くのはラッパーの起動より前で、起動の入力の `next` は必ず新しい着席の後に走る。`row_seated` の前に別の `next`（オーガナイザーが打ったものなど）が走って依存が増えていても、新しい conductor の `next` が新しい着席の待ちを書き、`_parks` の (1) で席を返す（旧着席の待ちが対象外のまま残らない）。ラッパーの起動に失敗すると `row_parked` で席が戻り、稼働中の数に残らず、次の起動の手は新しいキーになる（失敗のキーは同じ）
- 回答の後の next と待ちの起動し直しが同じ行で当たる写しで、手が 1 つ（回答の後の next）
- 開発ループの行（`kind: loop`）に回答が載ると、起動の入力が今のとおり `ndf lanes row resumed <スプリント>` → `ndf loop get <ループ>` で、`ndf progress next` を含まない（一律に `progress next` にすると開発ループが再開せずに落ちる）。開発ループの行が承認の待ちでも `_parks` は手を作らない
- 起動し直した conductor の `next` が回答を消費せずに戻る（差し替えた排他で進めない）と、`row resumed --answered` は `row_resumed` を書かず、行は `awaiting_next` のまま残る。回答を消費した後なら書く。止める前の conductor が先に消費していた行でも、起動し直した後の `row resumed` が書く。渡した番号より新しい回答が載っていれば書かない
- 席を返した行がある計画を中止すると、中止の手が上限の判定を通る（空いた枠に別の設計が入った後は上限で待つ）。席を返していない行の中止は今のとおり `seated`
- 割り当ての条件（手が無い・`running`・`free >= 1`・一覧に失敗していない課題がある）のどれかが欠けると打たない。1 回の判断で 1 本
- 最後の既存の行が終わって空きができた読み直しで、一覧が残っていれば `Complete` でなく割り当てを打つ。一覧が空なら `Complete`
- 一覧を使う計画で最後の行が終わった直後、作り直しの前の空の一覧の写しで `Complete` を打っても、`complete_plan` は `plan_completed` を書かない（最後の `idle_listed` が `lane_row_ended` より古い）。作り直しが候補を足した後の読み直しで割り当てを打つ。作り直しが空の一覧を書いた後なら閉じる。`Complete` の観測と `complete_plan` の間に作り直しが候補を足すと、`complete_plan` が排他の中の読み直しで外れる（写しの判断で閉じると候補を残したまま閉じて落ちる）。閉じた後の `refresh` は書かない。`idle_listed` を持たない計画は今のとおり閉じる
- 観測の後、`assign_idle` の前に `idle set --pin` か `plan pause` を書くと、`stale` で書かずに読み直す（失敗の知らせを出さない）
- 割り当てが断られたとき、3 回の観測を通して知らせが 1 回で、同じ課題を打ち直さず、次の読み直しで一覧の次の課題を割り当てる。その割り当ての耐久ステップは、渡された `skipped` で先頭の失敗した課題を飛ばして確かめ直し、`stale` にならずに書く（`skipped` を渡さないと 2 番目の課題が毎回 `stale` になって落ちる）。一覧を作り直すと、その課題をまた割り当ての候補にする

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

- 基のブランチの参照は `BaseRef`（`Git.remote()` とブランチ戦略の設定）で、`origin` を決め打ちしない。読む前に `Git.fetch_branch(<remote>, <branch>)`（宛先を明示した refspec `+refs/heads/<branch>:refs/remotes/<remote>/<branch>` の取得。clone の fetch refspec が対象のブランチを追跡しなくても `<ref>` が更新される。`git fetch <remote> <branch>` だけでは `FETCH_HEAD` しか更新されないことがある） を 1 回打ち、`git ls-tree` は取得で更新した `<ref>` を読む。取得に失敗したら GitHub を読めないときと同じく止める（古い参照で設計の有無を決めない）
- 設計の有無は、`<ref>` の `issues/` の名前の一覧（`git ls-tree --name-only <ref> issues/` を 1 回）の各名前に `design_issues("doc", 名前)` を当てた番号の和と、開いた Pull Request の一覧の head のうち設計ブランチの接頭辞（`model.branch_prefix("design")`）で始まるものに `design_issues("branch", head)` を当てた番号の和から求める。どちらかに入る課題を除く。課題ごとに GitHub を読まない
- マイルストーンの Issue の一覧から、Pull Request の項目（REST の `pull_request` を持つ）を除く
- 読み取りは次の 3 段で、どれも束ねて読む（前提 5）。(1) 計画の行の課題の番号を重複を除いて集め、所属のマイルストーンを `hub.nodes(issues, "issue", …)`（`closing_prs` と同じ一括の読み取り）で引く。行ごとに GitHub を呼ばない (2) 得たマイルストーンと `--milestone` の和の重複を除き、マイルストーンごとに開いた Issue の一覧をページ送りで読む (3) 開いた Pull Request の一覧をページ送りで 1 系列読む。1 回の `refresh` の呼び出しの回数は「(1) の束の数 + (2) のページ数の和 + (3) のページ数」で、計画の行の数にもタイムアウトの回数にも比例しない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `refresh` | GitHub を読めない | 認証・ネットワーク・上限 | `ndf.adapters.github` の例外 | 止める（前の一覧を残す） | `stopped`・`work_stopped`、終了コード 1 | 書かない。打ち直せる |
| `refresh` | 計画が閉じている | 耐久ワークフローが先に計画を完了にした | `idle_listed` を書く前の `lock_plan` の中の読み直し | 書かない | `stopped`・`precondition`、終了コード 1 | 書かない。閉じた計画には要らない |
| `refresh` | 読むマイルストーンが 1 つも無い | 計画の行の課題がマイルストーンを持たず、`--milestone` も渡されていない（計画から得たマイルストーンと明示の指定の和が空） | 読み取りの (2) の前 | 止める | `stopped`・`precondition`、`--milestone` を渡すよう `summary` に書く | `--milestone` を付けて打ち直せる（計画の課題がマイルストーンを持たなくても、明示の指定だけで一覧を作る） |
| `refresh` | 基のブランチを取得できない | ネットワーク・認証・リモート名の誤り | `Git.fetch_branch` の終了コード | 止める（前の一覧を残す） | `stopped`・`work_stopped`、終了コード 1 | 書かない。打ち直せる |
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
- `refresh`: 計画の行の課題・設計文書のある課題・設計 PR の開いた課題を除く。まとめた設計文書 `issue-4049-4050-design.md` は両方を除き、`issue-4050-design-detail.md` だけでは除かない。head が `design/issue-2489`・`design/4050-parallel-design-ahead`・`design/4049-4050-x` の開いた PR を一括の一覧で返し、#2489・#4049・#4050 を除く。`design/feature-x` は除かない。マイルストーンの一覧に混じった PR を項目に入れない。差し替えた GitHub の書き込みの呼び出しが 0 回。読み取りの呼び出しが、同じ課題を 3 行に持つ 12 行の計画・2 ページのマイルストーン・2 ページの PR の一覧で、束 1 + ページ 2 + ページ 2 の 5 回。読めなければ・取得できなければ `idle_listed` を書かない。リモート名が `upstream` の作業場所で `upstream/<基>` を読む。計画の行の課題がどれもマイルストーンを持たないとき、`--milestone` 無しなら `precondition` で止まり、`--milestone <番号>` を付けるとその指定だけで一覧を作る（計画の課題のマイルストーンの有無だけで止めると落ちる）
- `design_issues`: 上の名前のそれぞれと、数字の無い名前（空を返す）
- `current`: `pin` が先頭、`exclude` が作り直しの後も外れたまま、割り当てで計画に入った課題が消える
- 書き手の検査: conductor と workflow の `idle_listed`・`idle_adjusted` は断られる（事象を直接作る）。加えて、`NDF_RELAY_PLAN` を持つ環境で CLI の `ndf lanes idle set`・`refresh` を打つと `refused` になり、持たない環境では通る（CLI の経路）

## 表示と知らせ（`plan/__init__`・`wait_decide`）

満たす決定: 決定 3、受け入れ条件 8・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_lane_items` | `plan/__init__.py` | 変更 | 変えない | レーンの項目に `touches` を足す |
| `_row_item` | `plan/__init__.py` | 変更 | 変えない | `holding` の行に `held`（`waiting`（読むときの `implementation_wait`）・`stage`・`since`）を足す。`row_resumed` では消さない |
| `_plan_metrics` | `plan/__init__.py` | 変更 | 変えない | `idle`（件数と先頭の課題）を足す |
| `NOTICE_KINDS` | `plan/wait_decide.py` | 変更 | `("request_opened", "plan_completed", "lane_held", "lane_row_ended")` | `lane_held` と `lane_row_ended` を知らせにする。どちらも計画の事象なので、今の `--after` の番号で続きを読め、読み取りもポーリングも増えない |

### 単体テストの観点

- `status` の出力で、待ちの行だけが `held` を持つ。`row_resumed` の後も（実装を始める前に conductor が落ちた行も待ちとして）`held` を持ち続け、`row_implementing` の後に消える。`held.waiting` は読むときの最新の計画で求め直した待つ先で、`lanes set` で `touches` を合わせると印が消える
- `wait` が `lane_held` を 1 回返し、`--after` を進めた後は返さない
- 行が終わると `wait` が `lane_row_ended` を返す（耐久ワークフローの `row end`・conductor の `row end` のどちらでも）

## 手順書と契約（`parallel.md` ほか）

満たす決定: 決定 1・決定 7・決定 8・決定 10、受け入れ条件 13・14

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/parallel.md` | 知らせの表に `lane_held`（一覧を作り直し、待つ先を読む）と `lane_row_ended`（一覧を作り直す）を足す。節「空いた時間にやること」を足し、一覧を作り直す 4 つの時点・`pin`・`exclude` の基準（計画の目的に沿わない課題を外す・依存の先になる課題を先に出す）・割り当ては耐久ワークフローが行うこと・設計承認の回答を届ける前に `ndf lanes set <計画> <レーン> --touches <設計の結果の触るファイル>... --after <重なるレーン>... --reason <理由>` を打つこと（重なるレーンが無ければ `--after` を値無しで渡して空にする。合わせずに回答を届けても壊れないが、起動し直した行が `touches` の待ちで止まり、合わせた後にもう 1 回起動し直す）を書く（決定 8）。「耐久ワークフローがすること」の表に、実装の待ちと承認の待ちの行のラッパーを止める・待ちの解けで起動し直す・割り当て（一覧が残る間は計画を完了にしない）の行を足す。設計を直す手（確認の結果の `drift` を読み、続けるなら `next --drift-reviewed`、続けないなら行を中止して同じ課題の行を足す）を書く。「質問に回答する」「まとめたリリース」の `ndf lanes answered` に `--gate <質問の id>` を足し、席を返した行でも同じ経路（設計承認: `ndf answer` → `lanes answered` → `request close`、リリース承認: `lanes release` → `lanes answered`）で答えれば起動し直しが起きること、`ndf answer` だけでは起動し直しが起きないことを書く |
| `skills/development-workflow/references/asking.md` | 「並列の計画のレーン」に、承認ゲートの `AskUserQuestion` の `metadata` へ `source`（`ndf-gate:<番号>`）に加えて `gate`（`progress wait` の `metrics.gate.id`）と `sprint`（スプリントのステートマシンの ID）を付けることを書く。付けない質問は耐久ワークフローが席を返す対象にしない（止めずに待つだけで、壊れない） |
| `skills/development-workflow/references/parallel-checks.md` | 「マシン全体の稼働中の数」に、実装の待ちの行を数えないことを足す |
| `skills/issue-plan-strategy/references/parallel-plan.md` | `after` の行を「スプリントの行では実装のフェーズを始める条件、開発ループの行では行を始める条件」に直す |
| `docs/contracts/commands.md` | `ndf lanes answered` の `--gate`（任意。承認ゲートの回答なら質問の id）、`ndf lanes row resumed` の `--answered`、`ndf lanes idle`、`status`・`wait` の結果の足したキー、`ndf progress next` の `held`・`drift` |
| `contracts/schemas/io/lanes.args.schema.json` | `idle` の 3 つのサブコマンドの引数 |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 設計文書の「用語」の 5 語を足し、`ndf records glossary render` で作り直す |
