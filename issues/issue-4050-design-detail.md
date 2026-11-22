# #4050 の詳細設計: 実装の待ち・空いた時間にやることの一覧・割り当て

設計文書 `issues/issue-4050-design.md` の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の detailed-design.md にある。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── workflow/progress.json              # 事象 row_held・lane_held・idle_listed・idle_adjusted、lane_added の書き手
│   └── schemas/io/lanes.args.schema.json   # ndf lanes idle の引数
├── docs/contracts/commands.md              # ndf lanes idle・status・wait、ndf progress next の結果
├── src/ndf/progress/
│   ├── run.py                              # Env.hold・_start
│   ├── gate/answering.py                   # lane_env
│   ├── lane/
│   │   ├── types.py                        # LaneRow の held_seq・held・holding
│   │   ├── fold.py                         # row_held の畳み込み、idle_* の畳み込み
│   │   ├── rules.py                        # next_row・implementation_wait*
│   │   ├── __init__.py                     # hold_row*・drift*
│   │   ├── machine.py                      # count（待ちの行を数えない）
│   │   ├── liveness.py                     # HELD
│   │   └── launch.py                       # 起動し直しの入力（待ちの解け）
│   └── plan/
│       ├── __init__.py                     # _row_item・_lane_items・_plan_metrics、add_lane の書き手
│       ├── idle.py*                        # refresh*・adjust*・current*・show*
│       ├── run.py                          # _halts・read_snapshot・assign_idle*（耐久ステップ）
│       ├── run_decide.py                   # Snapshot の held・idle、_launches・Assign*
│       ├── wait_decide.py                  # NOTICE_KINDS
│       └── cli.py                          # ndf lanes idle
└── tests/progress/                         # test_lane・test_lane_machine・test_plan・test_plan_run・test_plan_wait・test_run_hold*・test_plan_idle*
```

依存の向き: `run.py`（スプリントの進行）は `Env` の口だけを持ち、`lane` を import しない。`gate/answering.py` が `lane` と `run` をつなぐ（今の `ended` と同じ）。`plan/idle.py` は `lane`（計画の読み書き）・`lane.machine`（空きの数）・`ndf.adapters.github`（作り直しの読み取り）に依存し、`plan/run.py` は `plan/idle.py` の `current` を読み、`plan/__init__.add_lane` で書く。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/progress.json` | 変更 | 事象の定義に 4 つ足し、`lane_added` の書き手に `workflow` を足す | — | — | — |
| `src/ndf/progress/run.py` | 変更 | 最初の `implement` の工程の手前で `Env.hold` を呼ぶ | `stages` | `Env` | `Env.hold`（新設の欄）、`_start`（変更）、`_hold_report`（新設） |
| `src/ndf/progress/gate/answering.py` | 変更 | `Env.hold` を実装の待ちの判定と記録につなぐ | `lane`・`run` | — | `lane_env`（変更） |
| `src/ndf/progress/lane/types.py` | 変更 | 行の実装の待ちを持つ | — | `LaneRow` | `held_seq`・`held_at`・`held`（欄）、`holding`（新設） |
| `src/ndf/progress/lane/fold.py` | 変更 | `row_held` と `idle_*` を畳み込む | `types` | — | レーンの畳み込み（変更）、`fold_idle`（新設） |
| `src/ndf/progress/lane/rules.py` | 変更 | スプリントの行は `after` を見ずに始め、実装の手前で `after` を照らす | `types` | — | `next_row`（変更）、`implementation_wait`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | 実装の待ちを書き、変わったファイルを求める | `rules`・`model`・`ndf.adapters.git` | — | `hold_row`（新設）、`drift`（新設） |
| `src/ndf/progress/lane/machine.py` | 変更 | 実装の待ちの行のレーンを数えない | `types` | — | `count` に渡す `PlanLanes.lanes` の作り方（変更） |
| `src/ndf/progress/lane/liveness.py` | 変更 | 実装の待ちの行を `held` と判定する | `types` | — | 判定（変更）、`HELD`（定数） |
| `src/ndf/progress/plan/idle.py` | 新設 | 一覧の作り直し・調整・読み | `lane`・`machine`・`github` | — | `refresh`・`adjust`・`current`・`show`・`order` |
| `src/ndf/progress/plan/run.py` | 変更 | 写しに待ちと一覧を載せ、割り当ての耐久ステップを持つ | `idle`・`plan` | — | `_halts`（変更）、`snapshot_of`（変更）、`assign_idle`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 待ちの起動し直しと割り当ての手を足す | — | `Snapshot`・`Assign`（新設） | `_launches`（変更）、`_assignable`（新設）、`decide`（変更） |
| `src/ndf/progress/plan/wait_decide.py` | 変更 | `lane_held` を知らせにする | — | — | `NOTICE_KINDS`（変更） |
| `src/ndf/progress/plan/__init__.py` | 変更 | 表示に `touches`・`held`・一覧を載せ、`add_lane` に書き手を渡せるようにする | `lane`・`idle` | — | `_row_item`・`_lane_items`・`_plan_metrics`・`add_lane`（変更） |
| `src/ndf/progress/plan/cli.py` | 変更 | `ndf lanes idle refresh / set / show` | `idle` | — | `_cmd_idle`（新設） |

## 実装の待ちの判定と記録（`rules`・`lane`）

満たす決定: 決定 2・決定 3・決定 6、受け入れ条件 2・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `implementation_wait` | `lane/rules.py` | 新設 | `(view: PlanView, lanes: Lanes, lane: str) -> list[str]` | レーンの `after` のうち `after_met` が偽の値を、計画の並びの順で返す |
| `next_row` | `lane/rules.py` | 変更 | 変えない | 行の入力が `kind: sprint` なら `waiting_after` を常に空にする。`loop` は今のとおり |
| `hold_row` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, stage: str, base: str, base_sha: str) -> Optional[Hold]` | スプリントの行を引き、`implementation_wait` が空なら None。空でなければ、行が待ちの中（`holding`）なら今の待ちを、そうでなければ `row_held`（レーン）と `lane_held`（計画）を `checked_write` で書いて新しい待ちを返す |
| `drift` | `lane/__init__.py` | 新設 | `(git: Git, base: str, base_sha: str, touches: Sequence[str]) -> list[str]` | `git diff --name-only <base_sha>..origin/<base>` の各パスのうち、`touches` のどれかと `touch_hit` で当たるものを昇順で返す |
| `LaneRow.holding` | `lane/types.py` | 新設 | `@property -> bool` | `state == RUNNING and held_seq > resumed_seq` |

- `hold_row`
  - 事前条件: `sprint_id` のスプリントが計画のレーンの行に属する（`sprint_row` で引ける）。属さなければ None（計画の外のスプリント）
  - 事後条件: 待ちを返したとき、行の `holding` が真で、`row_held` は今の待ちの中で 1 件だけある（I2）
  - 副作用: 計画とレーンのステートマシンへの書き込み（同じトランザクション）
- `drift`
  - 副作用: `git fetch origin <base>` を 1 回と `git diff` を 1 回打つ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `hold_row` | 計画の決まりで断られる | 待ちの書き込みが I13 などに当たる（起こらない想定。待ちは鎖を変えない） | `checked_write` | 呼び出し元へ返す | `next` が `stopped`・`precondition` と断った理由 | 書かない。直して `next` を打ち直せる |
| `hold_row` | `origin/<base>` を引けない | 取得の失敗・基のブランチが無い | 呼び出し元（`lane_env`）の `git rev-parse` | 待ちを書かずに止める | `next` が `stopped`・`work_stopped` | 打ち直せる |
| `drift` | 差分を読めない | `base_sha` が取得した履歴に無い（強制 push など） | `git diff` の終了コード | 既定値で続ける | `drift` を null にし、`warnings` に理由 | 工程は始める |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Hold` | 型（`waiting`・`stage`・`base_sha`・`seq`・`new: bool`） | `lane/types.py` | — |
| `ROW_HELD`・`LANE_HELD` | 定数（事象の種類） | `lane/types.py` | `"row_held"`・`"lane_held"` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じスプリントの `next` を 2 本 | スプリントのステートマシン・レーンの行 | 今の `store.run_lock`（スプリントごと）の中で `hold_row` を呼ぶ | `holding` が真なら書かずに今の待ちを返す |
| `hold_row` と耐久ワークフローの読み直し | 計画とレーンのステートマシン | 今の `lock_plan`（`checked_write` の中） | 読み直しは書いた後の状態を次の周で読む |

### 単体テストの観点

- `implementation_wait`: 行を指す `after`・レーンを指す `after`・行の無いレーンを指す `after`（満たさない）の 3 通り
- `next_row`: スプリントの行は `after` があっても `waiting_after` が空、開発ループの行は今のとおり
- `hold_row`: 1 回目は書き、2 回目は書かない。`row_resumed` の後の待ちは新しく書く。計画の外のスプリントは None
- `drift`: `touches` に当たるファイルだけを返す。`base_sha` が無いと null と warnings

## 工程を始める口（`run`・`answering`）

満たす決定: 決定 2、受け入れ条件 2・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Env.hold` | `run.py` | 新設の欄 | `Callable[[Any, str, stages.StageDef, Path], Optional[HoldResult]]`（既定は常に None） | 工程を始める前に呼ぶ口。待ちなら `HoldResult(held=…)`、待ちが解けた直後なら `HoldResult(drift=…)` |
| `_start` | `run.py` | 変更 | 変えない | 工程のフェーズが `implement` で、これより前の `implement` の工程が 1 つも始まっていなければ `Env.hold` を呼ぶ。`held` なら工程を始めずに `_hold_report` を返す。`drift` なら始めた後の結果の `metrics.drift` に載せる |
| `_hold_report` | `run.py` | 新設 | `(v: View, s: StageDef, held: Hold) -> dict` | 入出力のコントラクトの形の結果（`next` を持たない） |
| `lane_env` | `gate/answering.py` | 変更 | 変えない | `hold` に、基のブランチ（ブランチ戦略の設定）と `origin/<base>` を引いて `lane.hold_row` を呼び、待ちが無く直前の待ちがあった行には `lane.drift` を呼ぶ関数を入れる |

- `_start` の「これより前の `implement` の工程が始まっていない」は、スナップショットの工程の並びで、この工程より前のフェーズ `implement` の行がどれも `pending` か `skipped` であることで決める（`light` で最初の工程が `implement` のときも同じ規則で当たる）

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `lane_env` の `hold` | 計画の外のスプリント | None（今と同じ） |
| 同上 | `implementation_wait` が空・直前の待ちが無い | None |
| 同上 | `implementation_wait` が空・直前の待ちがある（`held_seq > 0` で `holding` が偽） | `HoldResult(drift=lane.drift(...))` |
| 同上 | `implementation_wait` が空でない | `HoldResult(held=lane.hold_row(...))` |

### 単体テストの観点

- `standard` のスプリントで「計画」の手前で待ち、「要求と受け入れ条件」〜「ドキュメントレビュー」は待たない
- `light` のスプリントで最初の工程の手前で待つ
- `Env.hold` の既定（計画の外）で工程の順が今と同じ

## マシンの数と生死の判定（`machine`・`liveness`）

満たす決定: 決定 4、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PlanLanes.lanes` を作る関数 | `lane/machine.py` | 変更 | 変えない | レーンの実行中の行が `holding` なら、そのレーンを「実行中の行を持つ」に数えない |
| 生死の判定 | `lane/liveness.py` | 変更 | 変えない | 実行中の行が `holding` なら `HELD`（`stall: false`）を返し、スプリントもラッパーも読まない |

### 単体テストの観点

- 待ちの行のレーンは `count` に入らず、ラッパーが動いていれば一覧の側で数える（止めるまでは数える）
- `HELD` は `STALL_KINDS` に入らない

## 起動の手と割り当て（`plan/run`・`plan/run_decide`）

満たす決定: 決定 4・決定 8、受け入れ条件 3・4・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_halts` | `plan/run.py` | 変更 | 変えない | 最後の行が終わったレーンに加え、実行中の行が `holding` でラッパーが動いているレーンを止める先に入れる |
| `snapshot_of` | `plan/run.py` | 変更 | 変えない | 写しに `released`（`holding` で `implementation_wait` が空の行の（レーン, 行, スプリント, `held_seq`））と `idle`（`idle.current` の先頭の課題と件数）と `free`（空きの数）を載せる |
| `_launches` | `plan/run_decide.py` | 変更 | 変えない | `released` から起動し直しの手（`Launch(resume=sid, seq=held_seq, admit=True)`）を作り、回答の後の next の手と同じ位置に並べる |
| `_assignable` | `plan/run_decide.py` | 新設 | `(snap: Snapshot, acts: list[Launch]) -> Optional[Assign]` | 手が 1 つも無く、計画が `running`、`free >= 1`、一覧が空でなければ、先頭の課題の割り当ての手を返す |
| `assign_idle` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, issue: int) -> dict` | 割り当てで足すレーン（入出力のコントラクトの形）を `plan.add_lane(..., actor="workflow", idle=issue)` で書く。断られたら `{"ok": false, "detail": …}` |
| `decide` | `plan/run_decide.py` | 変更 | 変えない | `_candidates` が空のとき `_assignable` を見る。割り当ての結果が失敗なら、同じ課題で 1 回だけ `launch_failed` の形の知らせを作り、その課題を `RunState.tried` に入れて同じ実行では打ち直さない |

- 起動し直しの手は、行を始める手と同じくマシン全体の上限の判定（`machine.admitted`）を通す（今の回答の後の next は通さない。待ちの行はラッパーを止めて席を返しているため）。入力は `launch.py` の回答の後の next と同じ並び（`ndf lanes row resumed` → `ndf progress next`）
- 割り当ての手の後の次の読み直しで、足したレーンは `wakeable` に入り、行を始める手（上限の判定を通す）になる。割り当てそのものは上限の判定を通さない（席を使わない）

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Assign` | 型（`issue`・`key`） | `plan/run_decide.py` | — |
| `Snapshot.released`・`Snapshot.idle`・`Snapshot.free` | 欄 | `plan/run_decide.py` | 空・None・None |
| 割り当てで足すレーンの名前 | 規則 | `plan/run.py` | `i<課題の番号>`、重なれば `i<番号>-2`… |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 割り当てとオーガナイザーの `lane add`・`idle set` | 計画のステートマシン | 今の `lock_plan` | 割り当ては `lane_added` 1 件で、課題が計画の行に入るので、再開しても同じ課題を 2 回足さない（足す前に一覧を読み直す） |
| 割り当てとほかの計画の起動 | マシンの枠 | 割り当ては席を使わない。行の開始は今の `admit.lock` | 次の読み直しで数え直す |

### 単体テストの観点

- 待ちの行のラッパーを止める手が、行を始める手より前に出る
- `after` を満たした待ちの行の起動し直しが 1 回だけで、上限なら待ちに置かれる
- 割り当ての条件 4 つ（手が無い・`running`・`free >= 1`・一覧が空でない）のどれかが欠けると打たない。1 回の判断で 1 本
- 割り当てが断られたとき、知らせが 1 回で、同じ課題を打ち直さない

## 空いた時間にやることの一覧（`plan/idle`・`cli`）

満たす決定: 決定 7、受け入れ条件 9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `order` | `plan/idle.py` | 新設 | `(items: Iterable[IdleItem]) -> list[IdleItem]` | マイルストーンの連番（名前の先頭の `^\d{2} `）の昇順、連番の無いものは後ろで名前の昇順、同じマイルストーンの中は課題の番号の昇順に並べる |
| `refresh` | `plan/idle.py` | 新設 | `(conn, pid: str, gh: GitHub, git: Git, base: str, extra_milestones: Sequence[int]) -> dict` | 計画の行の課題のマイルストーンと `extra_milestones` の開いた Issue を読み、計画の行の課題と設計のあるものを除き、`order` で並べて `idle_listed`（書き手 organizer）を書く |
| `adjust` | `plan/idle.py` | 新設 | `(conn, pid: str, issue: int, how: Literal["pin", "exclude", "clear"], reason: str) -> None` | `idle_adjusted` を書く |
| `current` | `plan/idle.py` | 新設 | `(view: PlanView, lanes: Lanes) -> list[IdleItem]` | 最後の `idle_listed` の項目から、計画のどの行の課題と `exclude` を除き、`pin` を書いた順で先頭へ出す。GitHub を読まない |
| `show` | `plan/idle.py` | 新設 | `(conn, pid: str) -> dict` | `current` と `listed_at`・空き（`machine` の数え方）・`excluded` を結果の形で返す |
| `_cmd_idle` | `plan/cli.py` | 新設 | `(a: argparse.Namespace, cwd: Path) -> dict` | `refresh`・`set`・`show` を振り分ける |
| `add_lane` | `plan/__init__.py` | 変更 | `(conn, pid, decl, request=None, actor="organizer", idle=None) -> str` | 書き手と `idle` を `lane_added` に載せる。ほかは今のとおり |

- 設計の有無は、`origin/<base>` の `issues/` の名前の一覧（`git ls-tree --name-only origin/<base> issues/` を 1 回）と、開いた Pull Request の head の一覧（`head` が `design/` で始まるものを 1 回の一覧で読む）から求める。課題ごとに GitHub を読まない
- 読み取りの回数は、マイルストーンごとの Issue の一覧（ページ）と Pull Request の一覧 1 回に収める（前提 5）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `refresh` | GitHub を読めない | 認証・ネットワーク・上限 | `ndf.adapters.github` の例外 | 止める（前の一覧を残す） | `stopped`・`work_stopped`、終了コード 1 | 書かない。打ち直せる |
| `refresh` | 計画の行の課題にマイルストーンが 1 つも無い | 計画の課題がマイルストーンを持たない | 読んだ Issue | 止める | `stopped`・`precondition`、`--milestone` を渡すよう `summary` に書く | 打ち直せる |
| `adjust` | 書き手が organizer でない | conductor・workflow が打った | 事象の定義（`actors`） | 断る | `refused`・終了コード 1 | 書かない |
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
- `refresh`: 計画の行の課題・設計文書のある課題・設計 PR の開いた課題を除く。差し替えた GitHub の書き込みの呼び出しが 0 回。読めなければ `idle_listed` を書かない
- `current`: `pin` が先頭、`exclude` が作り直しの後も外れたまま、割り当てで計画に入った課題が消える
- 書き手の検査: conductor と workflow の `idle_listed`・`idle_adjusted` は断られる

## 表示と知らせ（`plan/__init__`・`wait_decide`）

満たす決定: 決定 3、受け入れ条件 8・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_lane_items` | `plan/__init__.py` | 変更 | 変えない | レーンの項目に `touches` を足す |
| `_row_item` | `plan/__init__.py` | 変更 | 変えない | `holding` の行に `held`（`waiting`・`stage`・`since`）を足す |
| `_plan_metrics` | `plan/__init__.py` | 変更 | 変えない | `idle`（件数と先頭の課題）を足す |
| `NOTICE_KINDS` | `plan/wait_decide.py` | 変更 | `("request_opened", "plan_completed", "lane_held")` | `lane_held` を知らせにする |

### 単体テストの観点

- `status` の出力で、待ちの行だけが `held` を持ち、起動し直した後（`row_resumed` の後）の行は持たない
- `wait` が `lane_held` を 1 回返し、`--after` を進めた後は返さない

## 手順書と契約（`parallel.md` ほか）

満たす決定: 決定 1・決定 7・決定 8・決定 10、受け入れ条件 13・14

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/parallel.md` | 知らせの表に `lane_held`（一覧を作り直し、待つ先を読む）を足す。節「空いた時間にやること」を足し、一覧を作り直す 4 つの時点・`pin`・`exclude` の基準（計画の目的に沿わない課題を外す・依存の先になる課題を先に出す）・割り当ては耐久ワークフローが行うこと・設計承認の回答を届けた後に `ndf lanes set <レーン> --touches <設計の結果の触るファイル> --after <重なるレーン>` を打つことを書く。「耐久ワークフローがすること」の表に、待ちの行のラッパーを止める・待ちの解けで起動し直す・割り当ての 3 行を足す。設計を直す手（`drift` を読み、行を中止して同じ課題の行を足す）を書く |
| `skills/development-workflow/references/parallel-checks.md` | 「マシン全体の稼働中の数」に、実装の待ちの行を数えないことを足す |
| `skills/issue-plan-strategy/references/parallel-plan.md` | `after` の行を「スプリントの行では実装のフェーズを始める条件、開発ループの行では行を始める条件」に直す |
| `docs/contracts/commands.md` | `ndf lanes idle`、`status`・`wait` の結果の足したキー、`ndf progress next` の `held`・`drift` |
| `contracts/schemas/io/lanes.args.schema.json` | `idle` の 3 つのサブコマンドの引数 |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 設計文書の「用語」の 5 語を足し、`ndf records glossary render` で作り直す |
