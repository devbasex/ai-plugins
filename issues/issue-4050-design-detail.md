# #4050 の詳細設計: 7 つのフェーズの順序と排他・フェーズの待ち・空いた時間にやることの一覧・割り当て

設計文書 `issues/issue-4050-design.md` の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の detailed-design.md にある。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── workflow/phases.json*               # 7 つのフェーズの exclusion・requires
│   ├── workflow/ndf.schema.json            # phase の enum を phases.json の id に。required から外す
│   ├── workflow/stages/*.json              # phase の値。クリーンアップは phase 無し。確定仕様化の入力から worktree・branch を外す
│   ├── workflow/modes/*.json               # クリーンアップを Pull Request の直後、確定仕様化をデプロイの後ろへ
│   ├── templates/plan-to-spec.json         # 確定仕様化の worktree を基のブランチから作る
│   ├── workflow/progress.json              # 事象 row_held・row_entered・row_idle・row_active・row_left・row_drift_reviewed・lane_held・lane_row_ended・idle_listed・idle_adjusted・batch_claimed・batch_closed・delegate_answered・gate_presented、lane_added と row_active の書き手、row_started の gated、request_opened の lane-verify・lane-backlog
│   └── schemas/io/lanes.args.schema.json   # ndf lanes idle の引数
├── docs/contracts/commands.md              # ndf lanes idle・status・wait、ndf progress next の結果
├── src/ndf/domains/relay/signals.py        # 質問のファイルに AskUserQuestion の metadata を写す
├── src/ndf/progress/
│   ├── model.py                            # design_issues*（設計文書と設計ブランチの番号）
│   ├── definition.py                       # phases.json の読み込みと phase・requires の検査
│   ├── stages.py                           # StageDef.phase を Optional に、PhaseDef*
│   ├── run.py                              # decide（順序と動いている工程）・Env.hold（すべてのフェーズの境）・Env.hold_all・Env.idle・Env.busy・Env.leave・Env.delegate・Env.recheck・--spec-paths・Env.peek・_start・answer_draft・awaiting*・wait と next の手の組み立て・--drift-reviewed
│   ├── gate/answering.py                   # lane_env・recheck（消費の直前に呼ぶ形）
│   ├── gate/asking.py                      # pending*・waiting（decide を呼ばない）
│   ├── gate/entry.py                       # present（gate_presented と続ける next）・_progress（入口の recheck をやめ Env.recheck を渡す）
│   ├── lane/
│   │   ├── types.py                        # LaneRow の held_seq・held・implementing_seq・gated・drift・review・drift_reviewed・parked_seq・park_seat・park_stop・seated_seq・seat_launch・unparked_seq・seat_fail・stopped_seq・holding・design_phase・parked・stop_pending・seat_progressed
│   │   ├── fold.py                         # row_held・row_entered・row_drift_reviewed・row_parked・row_seated・row_unparked・row_stopped の畳み込み、idle_* の畳み込み
│   │   ├── rules.py                        # phase_wait*・phase_of*・occupants*・spec_overlaps*・next_row・implementation_wait*・park_reason*・_order_graph・_after_sources（設計中の行へだけ起点の末尾から、ほかの行へは終わった行からだけ引く）
│   │   ├── __init__.py                     # start_row（gated）・enter_phase*・mark_idle*・begin_implementation*（enter_phase の implement の枝）・review_drift*・changed_paths*・drift*・park*・seat*・unpark*・mark_stopped*・end_row（lane_row_ended）
│   │   ├── machine.py                      # lanes_of・held_seats（実装の待ちと席を返した行を数えない）
│   │   ├── liveness.py                     # HELD・席を返した行の判定・wrapper_table*（観測で 1 回だけ読む一覧）・stopping*（停止を確かめていない席を返した行）
│   │   └── launch.py                       # 起動し直しの入力（スプリントの行は next --launch <キー> → row resumed --answered の順、開発ループの行は今のとおり）
│   └── plan/
│       ├── __init__.py                     # _row_item・_lane_items・_plan_metrics、add_lane の書き手、ask_request（質問の metadata）、complete_plan（排他の中で一覧・失敗の世代・作り直しを確かめる）
│       ├── idle.py*                        # refresh*・adjust*・current*・show*・relist_pending*
│       ├── run.py                          # _halts・_parks*・snapshot_of・launch_lane（席の取り直し）・assign_idle*（耐久ステップ）
│       ├── run_decide.py                   # Snapshot の released・parks・parked・idle・free、Snapshot の relist、Launch の answered・hold・fail_key、Complete の skipped、RunState.assign_failed、_launches・_after_observe・_fail・still_skipped*・Assign*・ParkAct*
│       ├── wait_decide.py                  # NOTICE_KINDS
│       └── cli.py                          # ndf lanes idle・caller_actor*・answered の --gate・row resumed の --answered
└── tests/progress/                         # test_lane・test_lane_machine・test_plan・test_plan_run・test_plan_wait・test_run_hold*・test_plan_idle*
```

依存の向き: `run.py`（スプリントの進行）は `Env` の口だけを持ち、`lane` を import しない。`gate/answering.py` が `lane` と `run` をつなぐ（今の `ended` と同じ）。`plan/idle.py` は `lane`（計画の読み書き）・`lane.machine`（空きの数）・`ndf.adapters.github`（作り直しの読み取り）に依存し、`plan/run.py` は `plan/idle.py` の `current` を読み、`plan/__init__.add_lane` で書く。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/progress.json` | 変更 | 事象の定義に 14 足し（レーンの `row_held`・`row_entered`・`row_idle`・`row_active`・`row_left`・`row_drift_reviewed`、計画の `lane_held`・`lane_row_ended`・`idle_listed`・`idle_adjusted`・`batch_claimed`・`batch_closed`、スプリントの `delegate_answered`・`gate_presented`）、`lane_added` と `row_active` の書き手に `workflow` を、`row_started` の中身に `gated` を、`request_opened` の種類に `lane-verify`・`lane-backlog` を足す。#4091 の `row_parked`・`row_seated`・`row_unparked`・`row_stopped` は足さない | — | — | — |
| `contracts/workflow/phases.json` | 新設 | 7 つのフェーズの排他の規則と順序 | — | — | — |
| `contracts/workflow/ndf.schema.json`・`stages/*.json`・`modes/*.json`・`templates/plan-to-spec.json` | 変更 | `phase` の値と工程の並びを 7 つのフェーズに合わせる | `phases.json` | — | — |
| `src/ndf/progress/stages.py` | 変更 | フェーズの表の行と、`phase` を持たない工程を表す | — | `PhaseDef`（新設）・`StageDef` | — |
| `src/ndf/progress/definition.py` | 変更 | フェーズの表を読み、工程の定義の `phase` を照らす | `stages` | — | `load_phases`（新設）・`_stage_def`（変更） |
| `src/ndf/progress/model.py` | 変更 | 設計文書の名前と設計ブランチの head から課題の番号を取り出す | — | — | `design_issues`（新設） |
| `src/ndf/progress/run.py` | 変更 | フェーズの最初の工程の手前で `Env.hold` を呼び（実装・確定仕様化・ほかのフェーズで同じ口）、`work`・`wait`・`next` のどれでも待ち・宣言・確認・委ねを返す。回答を消費する直前に `Env.recheck` を呼ぶ | `stages` | `Env` | `Env.hold`・`Env.hold_all`・`Env.idle`・`Env.busy`・`Env.leave`・`Env.delegate`・`Env.recheck`・`Env.peek`（新設の欄）、`decide`・`_start`・`_drive`・`wait`・`report`・`answer_draft`・`_cmd_wait`（変更）、`awaiting`・`_phase_done`・`_consume_delegated`・`_hold_report`・`_drift_report`・`_declare_report`（新設） |
| `src/ndf/progress/gate/answering.py` | 変更 | `Env` の口をレーンの関数につなぎ、承認対象の再検証を回答の消費の直前に呼べる形にする | `lane`・`run`・`ndf.adapters.git` | — | `lane_env`・`recheck`（変更） |
| `src/ndf/progress/gate/asking.py`・`gate/entry.py` | 変更 | 回答を待つ質問を `decide` から切り離して探し、質問を届けた記録と続ける `next` を返す | `run` | — | `pending`（新設）・`waiting`・`present`・`_progress`（変更） |
| `src/ndf/progress/lane/types.py` | 変更 | 行の実装の待ちと実装の開始を持つ | — | `LaneRow` | `held_seq`・`held_at`・`held`・`hold_base`・`implementing_seq`・`gated`・`drift`・`review`・`drift_reviewed`・`parked_seq`・`park_seat`・`park_stop`・`seated_seq`・`seat_launch`・`unparked_seq`・`seat_fail`・`stopped_seq`（欄）、`holding`・`implementing`・`design_phase`・`parked`・`stop_pending`・`seat_progressed`（新設） |
| `src/ndf/progress/lane/fold.py` | 変更 | `row_held`・`row_entered`（`implement`）・`row_drift_reviewed`・`row_parked`・`row_seated`・`row_unparked`・`row_stopped` と `idle_*` を畳み込む | `types` | — | レーンの畳み込み（変更）、`fold_idle`（新設） |
| `src/ndf/progress/lane/rules.py` | 変更 | スプリントの行は `after` を見ずに始め、実装の手前で `after` と未確定の `touches` を照らす。I13 のグラフの辺を実装の開始で切り替え、実装を始めた行と始まった行へは起点の終わった行からだけ辺を引く。席を返す理由を 1 か所で決める | `types` | — | `next_row`・`_order_graph`・`_after_sources`（変更）、`implementation_wait`・`park_reason`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | 実装の開始を判定して待ちか開始を書き、変わったファイルを求め、行の終わりを計画へ知らせる。回答の記録に質問の id を載せ、再開の印は回答の消費を確かめたときだけ書く | `rules`・`model`・`ndf.adapters.git` | — | `begin_implementation`・`review_drift`・`changed_paths`・`drift`・`park`・`seat`・`unpark`・`mark_stopped`（新設）、`end_row`・`record_answer`・`record_resumed`（変更） |
| `src/ndf/progress/lane/machine.py` | 変更 | 実装の待ちの行と席を返した行のレーンを、記録だけで数えない | `types` | — | `lanes_of`・`held_seats`（変更） |
| `src/ndf/progress/lane/liveness.py` | 変更 | 実装の待ちの行を `held`、承認ゲートで席を返した行を `waiting_answer` と記録から判定し、席を返す前の承認ゲートの待ちを見分ける | `types`・`stages` | — | `judge`・`judge_lanes`（変更）、`HELD`（定数）、`gate_wait`・`wrapper_table`・`stopping`（新設） |
| `src/ndf/progress/lane/launch.py` | 変更 | スプリントの行（`kind: sprint`）の回答の後の next と待ちの起動し直しの入力を `ndf progress next --launch <手のキー>` → `ndf lanes row resumed` の順にし（`--launch` は `claim_relaunch` が書いた取りかかりと照らす）、回答の後の next では対象の回答の番号（`--answered <Launch.answered>`。起動のキーの番号と別に運ぶ）を渡す。開発ループの行（`kind: loop`）は今の `resume_commands` のまま（`row resumed` → `ndf loop get <ループ>`） | — | — | `resume_commands`（行の種類で分ける。変更） |
| `src/ndf/progress/plan/idle.py` | 新設 | 一覧の作り直し・調整・読み | `lane`・`machine`・`github` | — | `refresh`・`adjust`・`current`・`show`・`order`・`relist_pending` |
| `src/ndf/progress/plan/run.py` | 変更 | 写しに待ち・席を返す先・一覧を載せ、席の返却と取り直し・割り当ての耐久ステップを持つ | `idle`・`plan` | — | `_halts`・`snapshot_of`・`launch_lane`（変更）、`_parks`・`park_lane`・`assign_idle`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 待ちの起動し直し・席を返す手・割り当ての手を足し、1 行に続きの手を 1 つに絞り、一覧が残る間は完了にしない | — | `Snapshot`・`RunState`・`Launch`・`Assign`（新設）・`ParkAct`（新設） | `_launches`・`_after_observe`・`decide`・`_fail`（変更）、`_assignable`・`Launch.fail_key`（新設） |
| `src/ndf/progress/plan/wait_decide.py` | 変更 | `lane_held`・`lane_row_ended` を知らせにする | — | — | `NOTICE_KINDS`（変更） |
| `src/ndf/progress/plan/__init__.py` | 変更 | 表示に `touches`・`held`・一覧を載せ、`add_lane` に書き手を渡せるようにし、質問のリクエストに `metadata` を載せる | `lane`・`idle` | — | `_row_item`・`_lane_items`・`_plan_metrics`・`add_lane`・`ask_request`・`complete_plan`（変更） |
| `src/ndf/domains/relay/signals.py` | 変更 | 質問のファイル（`question.json`）に AskUserQuestion の `metadata` を写す | — | — | 質問を書く関数（変更） |
| `src/ndf/progress/plan/cli.py` | 変更 | `ndf lanes idle refresh / set / show`・`ndf lanes batch` と呼び出し元の役割 | `idle`・`plan` | — | `_cmd_idle`・`_cmd_batch`・`caller_actor`（新設） |

## #4091 へ分けたもの（範囲外）

**待つ行のラッパーを止めて席を返し、待ちが解けたら上限の判定を通して席を取り直す仕組みは、#4091 へ分けた（設計文書の決定 15）。** この文書のうち、名前に「範囲外（#4091）」の印を付けた関数・欄・型と、節「席を返す手順（`park_lane`）」「`next` との競合」「席を返した行の回答と再開」は #4091 の設計の材料として残すもので、#4050 では作らない。#4050 で作るのは次の形である。

| 部品 | #4050 での形 |
| --- | --- |
| 実装の待ちの行のラッパー | 止めない。conductor は `held` を受けて応答を終え、ラッパーは今の承認待ちと同じく動いたまま枠を使う |
| マシン全体の数（`lanes_of`・`held_seats`） | 変えない。実装の待ちの行・承認待ちの行も実行中として数える |
| 生死の判定（`judge`） | 実装の待ちの行（`holding`）を `HELD`（`stall: false`）と読み、止まりとして知らせない |
| 待ちの起動し直し | `phase_wait`（実装の待ちなら `implementation_wait`）が空になった `holding` の行へ、今の回答の後の next と同じ続きの手（`seated=True`）で起動し直す。キーは `next:<スプリント>:<held_seq>`、入力は `ndf progress next` → `ndf lanes row resumed <スプリント>`（`--answered` 無し） |
| 回答の後の next | 今の形のまま。入力の順を `next` → `row resumed --answered <回答の番号>` にし、回答の消費を確かめて `row_resumed` を書く（決定 3・#4081 の 2 の直し）は #4050 に残す |
| 起動の失敗 | 今のとおりキーで数える（席を返さないのでキーは変わらない） |
| 着席（`seated_at_seq`） | 行の開始の番号だけ（`row_seated` を書かない）。I2 は「1 行の同じフェーズ（の並び）と同じ宣言に `row_held` は高々 1 件」と読む |
| 割り当て | 空き（上限 − 稼働中の数）が 1 以上の読み直しで打つ。待つ行が枠を返さないので、空きは終わった行と上限の余りから出る |

## フェーズの表・順序・排他（`phases.json`・`definition`・`run`・`rules`・`lane`）

満たす決定: 決定 1・決定 16・決定 17・決定 18・決定 19・決定 20、提案の受け入れ条件 17〜21（承認資料）、不変条件 I2・I14〜I17

### 契約の形

`contracts/workflow/phases.json`（新設）:

```json
{"phases": [
  {"id": "design", "name": "設計", "shared": "その課題の文書と設計 Pull Request", "exclusion": "none", "requires": [], "context": "none", "carrier": "lane"},
  {"id": "implement", "name": "実装", "shared": "コード（touches）・テストのスロット", "exclusion": "after_touches", "requires": ["design"], "context": "strong", "carrier": "lane"},
  {"id": "release", "name": "リリース", "shared": "リポジトリの版", "exclusion": "organizer_batch", "requires": ["implement"], "context": "weak", "carrier": "organizer"},
  {"id": "spec", "name": "確定仕様化", "shared": "docs/specifications/ の該当ファイル", "exclusion": "declared_paths", "requires": ["implement"], "context": "medium", "carrier": "lane"},
  {"id": "verify", "name": "検証", "shared": "利用者の環境に入った版・本物のアカウント", "exclusion": "organizer_batch", "requires": ["release"], "context": "weak", "carrier": "organizer"},
  {"id": "retrospective", "name": "振り返り", "shared": "Issue・Pull Request のコメントと新しい起票", "exclusion": "none", "requires": ["verify"], "context": "strong", "carrier": "lane"},
  {"id": "backlog", "name": "棚卸し", "shared": "Issue の本文・マイルストーン・着手の順位", "exclusion": "organizer_batch", "requires": ["retrospective", "spec"], "context": "weak", "carrier": "organizer"}
]}
```

工程の定義の `phase` の付け替え（`contracts/workflow/stages/*.json`）:

| 工程の定義 | 今 | 直した後 |
| --- | --- | --- |
| `requirements`・`worktree`・`design`・`design-detail`・`document-sources`・`document-rebuild`・`document-review` | `design` | `design`（変えない） |
| `plan`・`implement`・`refactor`・`code-review`・`quality-gates`・`pull-request` | `implement` | `implement`（変えない） |
| `cleanup` | `release` | 無し（キーを外す） |
| `deploy` | `release` | `release`（変えない） |
| `plan-to-spec` | `close` | `spec`。`inputs` から `worktree`・`branch` を外す |
| `layout-review`・`release-verification` | `close` | `verify` |
| `retrospective` | `close` | `retrospective` |
| `backlog` | `close` | `backlog` |

`contracts/workflow/ndf.schema.json` の `phase` は `enum` を 7 つの `id` にし、`required` から外す。`description` は「工程が属するフェーズ（`phases.json` の `id`）。無い工程はフェーズの境にならない」にする。`contracts/templates/plan-to-spec.json` は、`worktree`・`branch` を引数で受けたまま、conductor が基のブランチから作った確定仕様化用の worktree（ブランチ `spec/<課題の番号>-<主題>`）を渡す形にし、`plan-to-spec` の Skill の手順に作業場所の用意（`ndf:worktree`）を足す。工程のクリーンアップは確定仕様化より前に流れるので、確定仕様化の作業場所は確定仕様化の工程が自分で片付ける。テンプレートの最後の段（今はマージの後に終わる）に後片付けを足し、次の 3 つの終わり方のどれでも worktree とローカルのブランチを消してから工程を終える: (1) 確定仕様の Pull Request をマージした（リモートのブランチはマージで消える）(2) 書くことが無かった（`--spec-paths none` か差分が空。Pull Request を作らずに消す）(3) 行が中止された（今のクリーンアップと同じく、`ndf lanes` の中止の後片付けで消す）。再試行（`ndf progress retry` か落ちた後の打ち直し）は、同じ名前のブランチと worktree が残り、そのブランチがマージされていなければ再利用し、マージ済みなら消して基のブランチから作り直す。消すのは `git worktree remove` と `git branch -d`（マージ済みかを Git が確かめる）で、`-D`・`--force` が要るときは止めて理由を返す

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PhaseDef` | `stages.py` | 新設 | `@dataclass(frozen=True) (id: str, name: str, exclusion: str, requires: tuple[str, ...], context: str, carrier: str)` | フェーズの表の 1 行 |
| `StageDef.phase` | `stages.py` | 変更 | `Optional[str]` | クリーンアップは None |
| `load_phases` | `definition.py` | 新設 | `() -> dict[str, PhaseDef]` | `phases.json` を読み、`requires` が表の `id` だけを指し輪を作らない（`graphlib.TopologicalSorter`）ことを確かめる。違えば定義の読み込みの誤りとして断る |
| `_stage_def` | `definition.py` | 変更 | 変えない | `phase=meta.get("phase")`。`ndf progress new` が新しいスナップショットを作るとき、値が表に無ければ定義の誤り（I16）。保存済みの旧形式のスナップショットを読むときは照らさない（「旧形式のスプリント」） |
| `Snapshot.phases` | `stages.py` | 新設（欄） | `Optional[dict[str, PhaseDef]]` | `ndf progress new` が作るスナップショットに、そのときの `phases.json` の写しを入れる（`snapshot_dict`・`snapshot_from` が読み書きする）。無いスナップショットは旧形式で、下の「旧形式のスプリント」で流す |
| `decide` | `run.py` | 変更 | `(v: View, phases: Optional[Mapping[str, PhaseDef]]) -> Move` | 下の「`decide` の手順」。`Move` に `candidates`（START の候補の工程の並び）を足す。`phases` はスナップショットの `phases` で、None（旧形式）なら今の `decide`（定義の並びで最初の終わっていない工程）をそのまま使う |
| `_phase_done` | `run.py` | 新設 | `(v: View, phase: str) -> bool` | そのフェーズの工程がすべて終わった（完了か無効）か。モードに工程の無いフェーズは真 |
| `_entered` | `run.py` | 新設 | `(v: View, s: StageDef) -> bool` | 行がそのフェーズの工程を 1 つでも始めたか（スプリントの記録で読む。`row_entered` を書くかの判定に使い、計画の記録を読まない） |
| `_start` | `run.py` | 変更 | 変えない | 候補を順に見て、`phase` を持ちまだ入っていないフェーズの工程なら `Env.hold(stage, phase, paths)` を呼ぶ（`paths` は `--spec-paths` の値。無ければ None）。`hold` は調べるだけで待ちを書かない（入るときだけ `row_entered` を書く）。`enter` なら `Env.busy` を呼んで工程を始め、`held` なら次の候補を見る。`ended` なら工程を始めずに止める。`replaced` なら工程を始めずに `stopped`・`precondition`（「起動し直しで置き換わる」）を返す（ほかの候補へ進まず、`Env.hold_all` も `Env.idle` も呼ばない）。`review` なら工程を始めずに `_drift_report` を返す（実装の境の確認待ち。ほかの候補へ進まない）。全候補が `held` なら、越えてよい待ち（承認ゲート・委ね）の有無によらず、得た待ちの並び（`HoldItem` の並び）で必ず `Env.hold_all` を呼んで待ちを書く（承認の回答とフェーズの占有のどちらが先に解けても、耐久ワークフローが起動し直せるように）。`hold_all` が `retry`（排他の中で確かめ直すと待つ先が空いた工程）を返したら、その工程について `Env.hold` を 1 回だけ呼び直す。`held` を返したら、応答の形は 1 つの優先順で決める: 越えてよい待ちがあれば定義の並びで最初のものの待ち（`GATE` の質問か `DELEGATED`）の結果を返し、その `metrics.held` に書いた待ちを添え、`Env.idle` の理由はその待ち（`gate`・`delegated`）。無ければ `Env.idle("held", …)` を呼んで `_hold_report` を返す。どちらの形でも行は `holding` と `idle` が真になり、回答が載れば回答の後の next が、待つ先が空けば待ちの起動し直し（`released`）が打たれる（同じ読み直しで両方当たれば回答の後の next だけ。I10）。`declare` を返した候補が 1 つでもあれば、待ちを書かずに全候補の後で `declare` を返す（宣言の無い確定仕様化を待ちとして書かない）。どの経路でも `run.py` は `lane` を import せず、`Env` の口だけを呼ぶ |
| `Env.hold` | `run.py` | 変更 | `Callable[[Any, str, StageDef, str, Optional[list[str]]], Optional[HoldResult]]`（接続・スプリント・工程・フェーズ・宣言。既定は常に None） | フェーズの最初の工程を始める手前で呼ぶ唯一の口（実装のフェーズも同じ口）。None は計画の外で、工程をそのまま始める。`HoldResult` は `enter`（`row_entered` を書いた。実装なら `drift`）・`held`（待つ先。書いていない `HoldItem`）・`declare`・`review`（`drift`）・`ended`（行の状態）・`replaced`（`conductor_check` が断った起動のキー。何も書いていない）のどれか 1 つを持つ |
| `Env.hold_all` | `run.py` | 新設 | `Callable[[Any, str, list[HoldItem]], HoldResult]` | 候補の全部が待つときだけ呼ぶ。レーンでは `lane.hold_phases`（下）、計画の外は呼ばれない（`hold` が None を返すので待ちが起きない） |
| `Env.idle` | `run.py` | 新設 | `Callable[[str, str], None]`（理由・工程） | `next` が待ち（承認の回答待ち・委ねた回答待ち・フェーズの待ち）を返して応答を終える直前に呼ぶ。レーンでは `lane.mark_idle`、計画の外は何もしない |
| `Env.busy` | `run.py` | 新設 | `Callable[[str, str], bool]`（理由・工程。`next` の `--launch` の値は `lane_env` が閉包で持つ） | `next`・`work` が conductor に続けさせる結果（工程の開始・回答の消費・`declare`・`review`）を返す前に呼ぶ。工程の開始と回答の消費では、スプリントへ書く前に呼ぶ。偽なら（耐久ワークフローの起動の取りかかりの後で、置き換えられる前の conductor）何も書かずに `stopped`・`precondition` を返す。レーンでは `lane.mark_active`、計画の外は常に真。フェーズの最初の工程かによらず、クリーンアップや同じフェーズの次の工程でも呼ぶ |
| `Env.leave` | `run.py` | 新設 | `Callable[[str], None]`（フェーズ） | 工程の完了か無効の記録で、そのフェーズが `_phase_done` になったとき、同じ `next` の中で 1 回呼ぶ。レーンでは `lane.leave_phase`（`row_left` を書く）、計画の外は何もしない |
| `Env.recheck` | `run.py` | 新設 | `Callable[[View, Move], Optional[str]]`（読んだスプリント・消費の手。既定は常に None） | `next`・`work`（`_drive` の各周）・`wait` の手の組み立てが、承認ゲートの回答を消費する手（`CONSUME`）を実際に書く直前に、同じ `store.run_lock` の中で 1 回呼ぶ唯一の再検証の口。理由を返したら（回答の後に承認対象が変わった）その消費を書かず、`decide` を読み直して次の手を決める。CLI の入口（`gate/entry.py` の `_progress`）が `gate.answering.recheck` を `observe`・`docs_only` と組んで入れ、計画の内外で同じ。入口で `recheck` を先に 1 回呼ぶ今の形はやめる（確定仕様化の実行中は消費が後の周に来るので、入口の 1 回では消費の直前を確かめられない）。`gate.answering.recheck` は `decide` を呼んで `CONSUME` かを確かめる今の形をやめ、渡された消費の手（`Move`）の工程・ステップ・回答で照らす（`RESUME` が優先される間に呼ばれて素通りすることが無い） |
| `lane_env` | `gate/answering.py` | 変更 | 変えない | `hold` に `lane.enter_phase`、`hold_all` に `lane.hold_phases`、`idle` に `lane.mark_idle`、`busy` に `lane.mark_active`、`leave` に `lane.leave_phase`、`delegate` に `lane.delegate`、`peek` に行を DB だけで読む関数をつなぐ。実装のフェーズの Git の部品（`BaseRef` を組む関数と `Git` を作る関数）は、呼ばずに `enter_phase` へ渡す（`enter_phase` が実装の枝で `begin_implementation` に渡す）。`--drift-reviewed` の口に `lane.review_drift` をつなぐ |
| `phase_of` | `lane/rules.py` | 新設 | `(row: LaneRow) -> Optional[str]` | 行が占めるフェーズ。実行中で、最後の `row_entered` が最後の `row_held` より新しく、そのフェーズの `row_left` が最後の `row_entered` より後に無ければその `phase`、そうでなければ None（I14）。確定仕様化の工程を終えた（完了か無効）行は、次のフェーズに入る前や承認の回答を待つ間も占めない |
| `occupants` | `lane/rules.py` | 新設 | `(view, lanes, lane: str, phase: str) -> list[str]` | 同じリポジトリ（`_same_repo`）の別のレーンの実行中の行のうち `phase_of` が `phase` の行を `<レーン>/<行>` で返す。開発ループの行（`kind: loop`）は数えない。使うのは `spec_overlaps` だけ |
| `spec_overlaps` | `lane/rules.py` | 新設 | `(view, lanes, lane: str, paths: list[str]) -> list[str]` | `occupants(…, "spec")` のうち、その行の `spec_paths` と `touch_hit` で重なる行。`paths` が空なら空 |
| `phase_wait` | `lane/rules.py` | 新設 | `(view, lanes, lane: str, row: LaneRow, phase: str, paths: Optional[list[str]]) -> list[str]` | 規則 → 判定の表（`{"none": 空, "organizer_batch": 空, "after_touches": implementation_wait, "declared_paths": spec_overlaps}`）を引いて待つ先を返す。起動し直しの判定（`snapshot_of` の `released`）・表示（`status` の `held.waiting`）・生死の判定（`judge` の `HELD`）は、どれも `LaneRow.holding` が真の行について、`held_phase` の各フェーズの `phase_wait` を、確定仕様化の宣言は保存済みの `held_paths` で求める。どれか 1 つのフェーズの待つ先が空なら解けたと読む |
| `enter_phase` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, stage: str, phase: str, paths: Optional[list[str]], base, git, launch: Optional[str]) -> HoldResult` | 下の「`enter_phase` の手順」。入るときだけ書き、待つときは書かずに `HoldItem` を返す |
| `hold_phases` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, items: list[HoldItem], launch: Optional[str]) -> HoldResult` | 下の「`hold_phases` の手順」。候補の全部が待つときに、計画の排他の中で確かめ直してから待ちを 1 件だけ書く |
| `HoldItem` | `lane/types.py` | 新設（型） | `(stage, phase, waiting: list[str], paths: Optional[list[str]], ref, base_sha)` | 待ちを書く前の 1 候補。`ref`・`base_sha` は実装の枝で `begin_implementation` の手順 2 が排他の外で読んだ値（ほかのフェーズは None） |
| `mark_idle` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, reason: str, stage: str) -> Optional[int]` | 最後の `row_idle` が最後の `row_active`・`row_answered` より新しければ書かない（同じ待ちの打ち直し）。そうでなければ `row_idle` を `checked_write` で書く |
| `mark_active` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, reason: str, stage: str, launch: Optional[str]) -> Active` | `lock_plan` の中で行を読み直す。先に `conductor_check`（下）を通し、断られれば書かずに `Active(ok=False, replaced=<キー>)` を返す。(1) 最後の `row_idle` が最後の `row_active` より新しければ `row_active`（`reason`・`stage`）を書いて `Active(ok=True)`（応答を終えた後に conductor が仕事を再び始めた切り替わりで 1 件）。(2) 最後の `row_active` が耐久ワークフローの `relaunch`（起動の取りかかり）で、その後に conductor の `row_active` が無ければ、引数の `launch`（起動の入力の `next` が `--launch <キー>` で渡す）がその `launch` と同じときだけ `row_active` を書いて通し、違えば書かずに `Active(ok=False, replaced=<キー>)` を返す（置き換えられる前の conductor が取りかかりの後に仕事を始めない）。`_start` は `ok` が偽なら工程を始めず、`stopped`・`precondition`（「起動し直しで置き換わる」）を返す。(3) どちらでもなければ書かずに `Active(ok=True)`。`row_idle` を持たない行は書かない |
| `conductor_check` | `lane/__init__.py` | 新設 | `(row: LaneRow, launch: Optional[str]) -> Optional[str]` | conductor が行かスプリントの占有・待ち・作業を書く前に、計画の排他（`lock_plan`）の中で読み直した行に当てる唯一の照合。`relaunching` で、引数の `launch` が最後の `relaunch` の `launch` と違えば（置き換えられる前か、前の取りかかりで起動した conductor）そのキーを返し、そうでなければ None。`mark_active`・`enter_phase` の手順 2・`hold_phases` の手順 1・`begin_implementation` の手順 3 がどれも書く前に同じ排他の中で呼び、キーが返れば何も書かずに `HoldResult(replaced=<キー>)`（`begin_implementation` は `Begin(replaced=…)`）を返す。`_start` は `replaced` を受けたら工程を始めず `stopped`・`precondition`（「起動し直しで置き換わる」）を返す。照合と書き込みが同じ排他の中にあるので、`claim_relaunch` の後に元の conductor が `row_entered`・`row_held` を書いて確定仕様化の宣言や占有を残すことは無い。`launch` は `lane_env` が閉包で持つ `next --launch` の値で、`Env.hold`・`Env.hold_all`・`Env.busy` のどれにも同じ値を渡す |
| `claim_relaunch` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, idle_seq: int, launch: str) -> Claim` | 耐久ワークフローが続きの手（回答の後の next・待ちの起動し直し）のラッパーを置き換える直前に、`lock_plan` の中で行を読み直し、`running` で `LaneRow.relaunchable` が真で、最後の `row_idle` の番号（無ければ 0）が観測した `idle_seq` と同じときだけ通す。観測（`snapshot_of`）と同じ `relaunchable` を読むので、観測で候補になった行が起動の直前に条件の違いで外れることは無い。通ったとき、`idle_seq > 0` の行には `row_active`（書き手 workflow・`reason: relaunch`・`launch`）を書いて `claimed` を返す（起動の入力の `next` に `--launch <キー>` を渡す）。`idle_seq == 0` の行（`row_idle` を一度も持たない行。旧形式のスプリントと開発ループの行）は書かずに `untracked` を返し、今のとおり `--launch` を渡さずに置き換える（conductor が `row_active` を書かないので、取りかかりを書くと照らす相手が無い）。外れれば書かずに `stale`（失敗に数えない）。conductor の `mark_active` と同じ排他で書くので、先に書いたほうだけが通る。**取りかかりの取り直し:** `relaunching`（最後の `row_active` が workflow の `relaunch` で、その後に conductor の `row_active` が無い）の行は、`idle_seq > active_seq` が偽でも `relaunchable` が真なので、同じ `idle_seq` の手が取り直せる。起動の失敗（`launch.replace` か起動が失敗した）・起動した conductor が最初の `next` の前に落ちた（`_expire_launched` が期限 `Policy.up` で起動の失敗と数える）のどちらでも、今の決まりどおり同じキーで打ち直した手が取り直して起動し、`policy.attempts` 回で打ち切って `launch_failed` を 1 回知らせる。取り直しは新しい `row_active`（`relaunch`・その手のキー）を書くので、前の取りかかりで起動した conductor が後から `next --launch <前のキー>` を打っても `mark_active` が断る |
| `LaneRow.relaunching` | `lane/types.py` | 新設 | `@property -> bool` | 最後の `row_active` が書き手 workflow の `relaunch` で、その後に conductor の `row_active` が無い（起動の取りかかりの後、起動した conductor がまだ仕事を始めていない） |
| `LaneRow.relaunchable` | `lane/types.py` | 新設 | `@property -> bool` | 続きの手で起動してよい行の唯一の条件。`idle_seq == 0`（`row_idle` を持たない行。今のとおり）か、`idle_seq > active_seq`（応答を終えたまま）か、`relaunching`。`awaiting_next`・`snapshot_of` の `released`・`claim_relaunch` がどれもここを読む |
| `Claim` | `lane/types.py` | 新設（型） | `Literal["claimed", "untracked", "stale"]` | `claim_relaunch` の結果 |
| `leave_phase` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, phase: str) -> Optional[int]` | そのフェーズの `row_entered` があり、その後に同じフェーズの `row_left` が無いときだけ `row_left`（`phase`）を書く |
| `Env.delegate` | `run.py` | 新設 | `Callable[[StageDef, str], Optional[DelegateResult]]`（工程・フェーズ） | フェーズの表の `carrier` が `organizer` のフェーズ（検証・棚卸し）の最初の工程を始める手前で呼ぶ。計画の外は None（今のとおり行の conductor が流す）。リリースは今のリリースのリクエストのままで呼ばない |
| `delegate` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, phase: str, stages: list[str], version: Optional[str]) -> DelegateResult` | 計画の排他の中で、同じ行の同じフェーズの開いた委ねのリクエストが無ければ `request_opened`（`lane-verify` か `lane-backlog`）を書く。中身に、委ねる工程の並び `stages`（そのモードのそのフェーズの未完の工程すべて。`documentation` の検証なら体裁レビューとリリース後テスト）・版・検証の環境（`environment`。下の「検証の環境」）を載せる。開いたものがあれば書かずにそのキーを返す。失敗で閉じたものは数えない（打ち直しで新しく書く） |
| `_start`（委ねの枝） | `run.py` | 変更 | 変えない | `Env.delegate` がキーを返したら、そのフェーズの工程を始めずに「委ねた」として記録する。検証は、委ねた工程の並びの先頭をステップの待ち（`waiting`、待つものは `delegated:<キー>`）にし、残りは未着手のまま置く。ほかの候補が無ければ `Env.idle("delegated", 工程)` を呼んで待ちを返す。棚卸しは委ねた工程をすべて `completed`（中身に委ねたリクエストのキー）にして次へ進む |
| `_consume_delegated` | `run.py` | 新設 | `(v: View, s: StageDef, answer: DelegateAnswer) -> list[EventDraft]` | 委ねた回答（スプリントの事象 `delegate_answered`）を 1 回で消費する。`done` なら、回答のリクエストの `stages` の工程をすべて `completed` にし、リポートを文脈の `verification` へ写す。`failed` なら、同じ工程の並びの先頭を `failed`（`cause` に `delegate_failed` とリポートのパス）にし、残りは未着手のまま置く。どちらも同じトランザクションで書き、2 回目の消費は書き足さない |
| `batch` | `plan/__init__.py` | 新設 | `(conn, plan_id: str, keys: list[str], phase: str, result: str, report: str, reason: str) -> dict` | 下の「まとめた検証と棚卸し」。CLI は `plan/cli.py` の `_cmd_batch`（書き手はオーガナイザーだけ） |
| `LaneRow` の欄 | `lane/types.py` | 変更 | `phase: Optional[str]`・`entered: dict[str, int]`・`entered_seq: int`・`left: dict[str, int]`・`spec_paths: tuple[str, ...]`・`idle_seq: int`・`active_seq: int`・`held_phase: tuple[str, ...]`・`held_paths: Optional[tuple[str, ...]]` | `implementing_seq` は `entered.get("implement", 0)` と同じ値を持つ。`held_phase`・`held_paths` は最後の `row_held` の `phase`（並びにそろえる）と `paths` |
| `LaneRow.holding` | `lane/types.py` | 新設 | `@property -> bool` | 待ちが解けていない行。`state == RUNNING and held_seq > 0` で、`held_phase` のどのフェーズも `entered.get(フェーズ, 0) < held_seq`（待った後に入っていない）。行全体で使う唯一の定義で、`snapshot_of`・`status`・`judge`・`hold_phases` の重複防止がどれもこれを読む。実装の後に確定仕様化を待つ行も、実装の待ちと同じく真になる |
| `LaneRow.awaiting_next` | `lane/types.py` | 変更 | `@property -> bool` | 今の条件（`answered_seq > resumed_seq`）に `relaunchable` を足す（応答を終えた後に仕事を再び始めていないか、取りかかりの後に起動した conductor がまだ仕事を始めていない）。`row_idle` を一度も持たない行（旧形式のスプリントと開発ループの行）は今のとおり |
| `LaneRow.idle` | `lane/types.py` | 新設 | `@property -> bool` | `idle_seq > active_seq`。conductor が待ちを返して応答を終え、その後に仕事を始めていない行。`mark_active` の (1) と `mark_idle` が読む。起動の判定は `relaunchable` を読み、これを直接読まない |

### `decide` の手順

**待機中の工程**を、回答の有無によらず次の 2 つと定める: (w1) 質問を届けた承認ゲートの工程（ゲートのステップが待ちで、今の質問の id の `gate_presented` がある）(w2) 委ねた工程（`waiting` が `delegated:<キー>`）。待機中の工程は、回答が載った後も消費されるまで「動いている工程」に数えない。

1. 行全体が終わっていれば `DONE`
2. 失敗した工程があればそれ（`FAILED`）
3. 動いている工程（`running` で待機中でない工程）があればそれ（I17）。まだ質問を届けていない承認ゲートの待ち（ゲートのステップが待ちで、今の質問の id の `gate_presented` が無い）もここに入り、今のとおり `GATE` を返す（`present` が質問を届けて `gate_presented` を書く）。作業の待ち（今の `_running_move` の待ち）もここで返す
4. 待機中の工程のうち回答が載ったものがあれば、定義の並びで最初のものを消費する手（承認ゲートは今の `CONSUME`、委ねた工程は `_consume_delegated`）を返す。実際の作業（3）が無いときにだけ消費し、新しい候補（5）より先に消費する
5. 候補: 定義の並びを先頭から歩き、未着手の工程のうち、その `phase` の `requires` がすべて `_phase_done` で、同じフェーズの定義の並びで前の工程が終わっている工程を候補に足す。歩みは、終わっていない（完了でも無効でもない）工程に着いたところで、次の 3 つのどれかなら越えて続け、どれでもなければその工程で止める（後ろの工程を候補にしない）
   - (a) 越えてよい待ち: 回答の載っていない待機中の工程（w1・w2）
   - (b) いま候補に足した未着手の工程（`_start` が前から順に照らすので、後ろの候補は前の候補が待つときだけ始まる）
   - (c) `requires` を満たさないので候補にならない未着手の工程（順序の上で後ろのフェーズ。例: 検証の前の振り返り）
   `phase` の無い工程（クリーンアップ）は (a)〜(c) のどれにも当てず、未着手なら候補に足してそこで止め、動いているか待つ（承認・作業の待ち）間は 3 で返す。クリーンアップが終わる（完了か無効）まで、後ろのデプロイ・確定仕様化は候補に入らない
6. 候補があれば `START`（`candidates` に並べる）。`_start` が全候補で待ちを得たら、待ちを `Env.hold_all` で書いた後に 7 へ
7. 越えてよい待ちの工程があれば、定義の並びで最初のものの待ち（承認ゲートなら `GATE`。質問は書き足さず `gate_presented` も書き足さない。委ねた工程なら `DELEGATED`）。6 で書いたフェーズの待ちがあれば結果の `metrics.held` に添える。越えてよい待ちが無ければ 6 の待ち（`HELD`）
8. 全部の工程が終わっていれば `COMPLETE`

**質問を届けた後に続ける入口（計画の内外）。** 承認ゲートに着いた最初の `next`（`work`・`wait` の手の組み立ても同じ）は 3 で `GATE` を返し、CLI の入口の `gate/entry.py` の `present` が質問・承認資料（計画の行ならリリースのリクエスト）を作って、同じ呼び出しの中で `gate_presented`（質問の id）をスプリントへ書く。書いた後に `decide` を読み直し、`START` が返る（ほかに進める工程がある）なら、ゲートの結果に `next`（`ndf progress next <スプリント>`）と `metrics.continue: true` を足し、`summary` に「回答を待ちながら次の工程を進める」と書く。進める工程が無ければ今のゲートの結果のまま（`row_idle` は 7 の待ちとして書く）。計画の行の conductor は、質問がリクエストとしてオーガナイザーへ届くので、`next` を打って続ける。計画の外の conductor は、今のとおり回答ごとのルートで人に問い（`AskUserQuestion`）、人が答える前に続ける場合だけ `next` を打つ。答えが先に届けば 4 が消費を先に返すので、計画の外の並びは今と同じになる。`gate_presented` は同じ質問の id に 1 件で、変更要求の後に流し直した質問（id が変わる）は新しく書く。

**確定仕様化が候補に入るときのデプロイの状態。** `standard`・`documentation`・`operation` の並び（クリーンアップ → デプロイ → 確定仕様化）では、確定仕様化が候補に入るのは、クリーンアップが完了か無効で、デプロイが未着手（このときデプロイが先の候補で、`organizer_batch` なので待たずに始まり、確定仕様化は始まらない）・リリース承認ゲートの質問を届けた後で回答が無い（越えてよい待ち）・完了か無効のどれかのときである。デプロイが動いている（ゲートの前の作業中・作業の待ち・質問を届ける前のゲート）間は 3 がデプロイを返し、回答が載った後は 4 がデプロイの消費を返すので、確定仕様化は始まらない。確定仕様化が動いている間に回答が載っても 3 が確定仕様化を返し、終えた後の `next` が 4 で消費する。

### 回答を待つ質問の探し方（`gate/asking.py`）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `pending` | `gate/asking.py` | 新設 | `(v: run.View, **kw) -> Optional[Question]` | `decide` を呼ばずに、回答を待つ承認ゲートの質問を探す。工程の定義の並びで、`gate` を持つステップが質問を書いた後で、回答（`answered`）が載っていない工程の最初のものを `question(v, …)` で返す。無ければ None。`decide` が `START`・`RESUME`（確定仕様化を始める・流している間）・`HELD`・`DELEGATED` を返していても同じ質問を返す |
| `waiting` | `gate/asking.py` | 変更 | 変えない | `pending` を呼ぶだけにする（`decide` が `GATE` のときだけ返す今の条件をやめる）。回答の口（`gate/answering.py` の `waiting(v)`・`gate/release.py` の `waiting(run.read(…))`）と `report` の質問の表示はどれもここを読む |
| `awaiting` | `run.py` | 新設 | `(v: View, stage: str, gate_id: Optional[str]) -> Optional[tuple[StageDef, Step, StepProgress]]` | 工程 `stage` のゲートのステップが待ちで回答が載っておらず、`gate_id` があればそれが今の質問の id と同じときだけ、その工程・ステップ・進みを返す。`decide` を呼ばない。`asking.pending` と `answer_draft` が同じこの関数を読む |
| `answer_draft` | `run.py` | 変更 | 変えない | 回答を書く同じトランザクション（`append` の `build`）の中で、`decide` が対象の工程の `GATE` を返すことを求める今の検査をやめ、`awaiting(v, stage, extra.get("gate_id"))` で対象のゲートと質問の id に結び付けて確かめる。外れれば今のとおり `Refused`。`decide` が `START`・`RESUME`・`HELD`・`DELEGATED` を返す間も、`ndf answer`（`gate/answering.py` の `answer`）とまとめたリリースの `take_reply` の両経路が回答を書ける。選択肢の検査（`choices`）は `awaiting` が返したステップで行う |
| `present` | `gate/entry.py` | 変更 | 変えない | 上の「質問を届けた後に続ける入口」。`gate_presented` を書き、`decide` を読み直して `START` なら `next` を足す |
| `_progress` | `gate/entry.py` | 変更 | 変えない | 入口で `recheck` を 1 回呼ぶ今の形をやめ、`Env.recheck` に `recheck` を組んで渡す（消費の直前に呼ばれる）。`_took`（`take_reply`）は今のとおり入口で呼ぶ |

### 旧形式のスプリント

スプリントの定義は作成時のスナップショットに残り、再開はそれを読む。#4050 の前に作ったスナップショットは `phases` を持たず、工程の `phase` に `close` が残り、確定仕様化がクリーンアップより前で実装の worktree を入力に取る。旧形式は移行せず、そのまま流す。

- `decide` は `phases` が None なら今の `decide`（定義の並びで最初の終わっていない工程）を返し、`requires` を引かない。`_start` は `Env.hold`・`Env.delegate`・`Env.busy`・`Env.leave` を呼ばない（旧形式の行は計画の外と同じくフェーズの排他・委ね・`row_idle` を持たず、今のとおり動く）。`Env.idle` も呼ばない
- 工程の並びと入力は書き換えない。旧形式の確定仕様化はクリーンアップより前にあり、実装の worktree が残っている間に流れる
- 旧形式の行は `row_entered` を持たないので確定仕様化を占めない（決定 16 の代償と同じ）。実装の待ちも持たない（#4050 の前の振る舞いのまま）
- `phase` の値が表に無いこと（`close`）は、旧形式のスナップショットでは定義の誤りにしない（I16 の検査は `ndf progress new` が新しいスナップショットを作るときだけ行う）

### `enter_phase` の手順

1. `phase` が `implement` なら、計画の排他を取らずに `begin_implementation` へ委ね、その結果を `HoldResult` に写して返す。Git の取得・差分（`begin_implementation` の手順 2）は排他の外で走り、行の再検証と開始の記録は `begin_implementation` の手順 3〜5 が自分で `lock_plan` を取って行う（外側で排他を持ったまま委ねない。リモートの取得の間に計画のほかの書き込みを止めない）。書く事象の名前は `row_entered`（`phase: implement`）。ただし `begin_implementation` の手順 4 は待ちを書かず、手順 2 で読んだ `ref`・`base_sha`（(c') なら `hold_base` の値）を持つ `HoldItem` を `HoldResult(held=…)` で返す（書くのは `hold_phases`）
2. ほかのフェーズは、計画の排他（`lock_plan`）を取り、行が `running` かを確かめる（違えば書かずに `HoldResult(ended=…)`）。続けて `conductor_check(row, launch)` を通し、キーが返れば書かずに `HoldResult(replaced=<キー>)` を返す。以下の 3〜6 はこの排他の中で行う
3. `phase` が `spec` なら、宣言を次の順で決める: (i) 引数の `paths`（`--spec-paths`） (ii) 行が `holding` で `held_phase` に `spec` を含めば、保存済みの `held_paths`。どちらも無ければ書かずに `HoldResult(declare=True)`。待ちから起動し直した `next`（`--spec-paths` を持たない）は (ii) で最初の宣言を引き継ぎ、宣言し直さない。打ち直しで `--spec-paths` が保存済みの宣言と違えば、引数を採る（新しい宣言で待つ先を求め直す）
4. `phase_wait` が空なら `row_entered`（`phase`・`stage`・`spec` なら 3 で決めた `paths`）を書いて `HoldResult(enter=True)`。行が `idle` か `relaunching` なら、同じトランザクションで `row_active`（書き手 conductor・`reason: enter`・`stage`）も書く（占有の記録と仕事の再開を分けて書くと、その間に `claim_relaunch` が通り、入ったまま置き換えられる）。続く `Env.busy` は `mark_active` の (3) で何も書かない
5. 空でなければ、何も書かずに `HoldResult(held=HoldItem(…))` を返す。待ちの記録と知らせは、候補の全部が待つと決まったときに `hold_phases` だけが書く（確定仕様化が重なっても次の検証に進める行に、`row_held`・`lane_held` を残さない）
6. `none`・`organizer_batch` のフェーズは 4 だけを通る（排他は取るが待たない。`row_entered` は表示と占有の終わりのために書く）

### `hold_phases` の手順

1. トランザクションを始め、`lock_plan` を取り、行が `running` かを確かめる（違えば書かずに `HoldResult(ended=…)`）。続けて `conductor_check(row, launch)` を通し、キーが返れば書かずに `HoldResult(replaced=<キー>)` を返す
2. 受けた `HoldItem` のそれぞれについて、最新の計画で `phase_wait` を求め直す（宣言は `HoldItem.paths`）。どれかが空なら書かずに `HoldResult(retry=<その工程>)` を返す（`_start` がその工程の `Env.hold` を 1 回だけ呼び直す。2 回目も待ちに戻れば、その `next` は書かずに待ちを返し、次の `next` で照らし直す）
3. 行が `holding` で、`held_phase` が受けたフェーズの並びと同じで、確定仕様化を含むなら `held_paths` が受けた宣言（`HoldItem.paths`）とも同じなら、書かずに今の待ちを返す（I2）。宣言が違えば（待つ間に `--spec-paths` で宣言を直した）4 で新しい `row_held` を書き、以後の起動し直しと `phase_wait` は新しい `held_paths` を使う
4. そうでなければ `row_held`（`phase`（フェーズの並び）・`waiting`（フェーズごとの待つ先）・`stage`（工程の並び）・`paths`（確定仕様化を含むときの宣言）・実装を含むときの `base`・`ref`・`base_sha`）と計画の `lane_held` を同じトランザクションで書いて `HoldResult(held=…)`。フェーズの並びが前の待ちと違えば、前の待ちは解けたと読む（`holding` は最後の `row_held` だけを見る）

`HoldResult` に `retry` を足す。`hold_phases` は `Env.hold` と同じく `store.run_lock`（スプリントごと）の中で呼ぶ。

### まとめた検証と棚卸し（`batch`・検証の環境）

**検証の環境。** 委ねのリクエスト `lane-verify` は、検証する版（`version`。デプロイの工程の出力の版数）と環境（`environment`）を持つ。環境は、行のリポジトリのプロジェクトの宣言（`.ndf/` の工程の設定。`contracts/schemas/project-workflow.schema.json` に足すキー `verify.environment`。値は環境の名前の文字列）から `delegate` が読み、宣言が無ければリポジトリの名前（`<owner>/<name>`）にする。同じ利用者の環境やアカウントを使う別のリポジトリは、宣言で同じ名前を指す。環境を読むのは `delegate` の 1 か所だけで、ほかは記録の `environment` を読む。

| 範囲 | 決まり |
| --- | --- |
| 1 件の結果で閉じてよい範囲 | 同じ `environment` で、リポジトリごとに版が 1 つ（同じリポジトリの違う版を混ぜない）の開いた `lane-verify`。棚卸し（`lane-backlog`）は同じリポジトリ |
| 同時実行を防ぐ範囲 | 同じ `environment` の検証は、マシンのどの計画（別の保存先を持つ計画を含む）でも 1 本だけ。棚卸しは同じリポジトリで 1 本だけ。計画ごとの `lock_plan` や `read_lanes` の読み取りでは別の計画の取りかかりと不可分に確かめられないので、マシン全体の取りかかりの記録（下）で確かめる |
| 検証する版の維持 | 取りかかり中のまとめの環境に入るリポジトリ（そのまとめに載るリポジトリ）の新しい版を、`ndf lanes release` は断る（`stopped`・`precondition`。まとめの id を返す）。検証の間に同じ環境の版が入れ替わらない。取りかかりの前に新しい版が入っていたかは、`batch close` の `--observed-version`（検証の担い手が環境で読んだ版）で照らす（下の表） |

**マシン全体の取りかかりの記録。** マシンの状態の保存先（`<状態の保存先>/parallel/`。起動の排他 `admit.lock` と計画の索引と同じ場所）に、取りかかりの記録 `batches/<種類>-<キー>.json`（種類は `verify` か `backlog`、キーは環境の名前かリポジトリの名前を規則の外の字で写したもの。中身は計画の ID・計画の状態の保存先・まとめの id・リクエストのキー・リポジトリごとの版）と、その排他 `batches.lock`（flock）を置く。`batch claim` は `batches.lock` を取った中で記録を読み、記録があり、その計画の状態を読んで `batch_closed` の無い `batch_claimed` が残っていれば断る。記録が無いか、残っていなければ（閉じた後に記録を消す前に落ちた・計画を読めず消えた）記録を書き、同じ排他の中で自分の計画に `batch_claimed` を書く。`batch close` は自分の計画に `batch_closed` を書いた後、`batches.lock` の中で自分のまとめの id の記録だけを消す。確認と確保が同じ排他の中にあるので、別の計画の同じ環境の `claim` が同時に空きを見て両方通ることは無い |

**`ndf lanes batch` の 2 つの手。**

1. `ndf lanes batch claim <計画> <リクエスト>... --phase <verify か backlog>`: 挙げたリクエストが上の「閉じてよい範囲」に収まる開いた委ねのリクエストかを確かめ、マシン全体の取りかかりの記録で同時実行の範囲に取りかかり中のまとめが無ければ、記録と `batch_claimed`（まとめの id・リクエストのキー・フェーズ・環境・リポジトリごとの版）を書いて id を返す。オーガナイザーは id を得てから単発の担い手に検証か棚卸しを任せる
2. `ndf lanes batch close <計画> <まとめの id> --result <done か failed> --report <ファイル> [--observed-version <リポジトリ>=<版>...] --reason <理由>`: 1 つのトランザクションで、結果とリポートのパスと検証で読んだ版を `batch_closed` に書き、次の表のとおりにリクエストを扱い、検証なら各行への回答（レーンの `row_answered`（中身に `request`）とスプリントの `delegate_answered`（キー・結果・リポートのパス・検証した版））も書く。オーガナイザーが行ごとに `ndf lanes answered` を打つ手は無い。途中で落ちてもリクエストの閉じと回答は片方だけ残らず、打ち直しは閉じたまとめの id を見て書かずに前の結果を返す（冪等）。コミットの後にマシン全体の取りかかりの記録を消す（消す前に落ちた記録は、次の `claim` が `batch_closed` を読んで空きとみなす）。検証の `done` は、`--observed-version` がまとめのすべてのリポジトリについてリクエストの版と同じときだけ受ける。違うか渡されなければ断り（`stopped`・`precondition`）、旧い版への成功を記録しない。`failed`（`cause` に `version_mismatch` を添える）でだけ閉じられる

| フェーズ | `done` | `failed` |
| --- | --- | --- |
| 検証 | リクエストを閉じ、同じトランザクションで各行へ `done` の回答を書く。行の `next` が `_consume_delegated` で委ねた工程の並びをすべて完了にし、振り返りへ進む | リクエストを閉じ、同じトランザクションで各行へ `failed` の回答を書く。行の `next` が委ねた工程の並びの先頭を `failed` にし、`decide` が `FAILED` を返す（振り返りへ進まない）。行の結果と `ndf lanes status` の行の項目に失敗（`delegate_failed`・リポートのパス）を出す。再実行は、直した後に `ndf progress retry <スプリント> <工程>` を打つ。工程が未着手に戻り、次の `next` が新しい `lane-verify` を書く（閉じた失敗のリクエストは数えない） |
| 棚卸し | リクエストを閉じる。行は書いた時点で工程を終えているので、行へは何も打たない | リクエストを閉じない（結果とリポートだけを残し、取りかかりを外す）。開いたリクエストが残るので計画は完了しない（今の決まり）。`ndf lanes status` の `metrics` に失敗した棚卸しの件数とリポートを出す。再実行は、同じリクエストで `batch claim` からやり直す。棚卸しをしないと決めたら、オーガナイザーが `request close` で理由を付けて閉じる |

委ねた回答を書くのは `batch close` だけで、`ndf lanes answered` は `--request` を受けない（委ねのリクエストへの回答を行ごとに打つ口を作らないので、対象のスプリント・委ねのキー・確定した結果の照合を別に持たない）。`batch close` が書く `row_answered` は今の回答の後の next の契機（`answered_seq > resumed_seq`）になり、応答を終えた行（`idle`）を耐久ワークフローが起動し直す。スプリントは `delegate_answered` で委ねた回答を読み、計画を読まない。

### 委ねた検証の回答と `decide`

| 委ねた工程の状態 | `decide` | 結果の記録 |
| --- | --- | --- |
| 回答が無い | 越えてよい待ち。確定仕様化など後ろに無い候補があれば `START`、無ければ `DELEGATED`（`Env.idle("delegated", …)`）。委ねた工程を候補に選び直さない | 何も書かない |
| `done` の回答 | 4 の消費の手（`_consume_delegated`）。確定仕様化が動いている間は確定仕様化（`RESUME`）が先で、終えた後の `next` が消費する | 委ねた工程の並びすべてを `completed`、文脈の `verification` にリポート |
| `failed` の回答 | 4 の消費の手。消費の後は `FAILED` | 並びの先頭を `failed`（`cause` に `delegate_failed`）、残りは未着手 |

`documentation` の検証は、体裁レビューとリリース後テストを 1 件の `lane-verify`（`stages` に 2 つ）で委ね、1 件の回答で 2 つとも消費する。どちらかを委ねずに残すことは無い。

### 異常系の処理

| 起きたこと | 扱い |
| --- | --- |
| `phases.json` の `requires` が輪を作るか、表に無い `id` を指す | 定義の読み込みで断る（`ndf progress new` が始まらない） |
| 工程の定義の `phase` が表に無い | 同上（新しいスナップショットだけ。旧形式は「旧形式のスプリント」） |
| `--spec-paths` が確定仕様化の境でない `next` に付いた | 引数の誤り（`precondition`）で止める |
| 宣言した行の確定仕様化が途中で落ちた | 行は `spec` を占めたまま（`row_left` が無い）。打ち直しの `next` は同じ工程を続け、宣言し直さない。行が中止されれば占有は消える |
| 同じ行で、`row_idle` を書いた後に回答の後の起動し直しの前に conductor が別の工程を始めた | `Env.busy` が書いた `row_active` が `row_idle` より新しくなり、回答の後の手も待ちの起動し直しも打たれない。同じフェーズの次の工程やクリーンアップのように `row_entered` を書かない開始でも同じ。conductor は工程を終えた後の `next` で回答を読む |
| `batch claim` が同じ環境の取りかかり中のまとめに当たる（別の計画のものを含む） | `stopped`・`precondition` で断り、取りかかり中のまとめの計画とまとめの id を返す。閉じた後に打ち直す |
| `batch close` の途中で落ちた | トランザクションが戻り、リクエストは開いたまま・回答は無いまま。打ち直せる。コミットの後に記録を消す前に落ちたら、次の `claim` が閉じたまとめとして読み飛ばす |
| 検証で環境から読んだ版がリクエストの版と違う | `done` で閉じられない。`failed`（`version_mismatch`）で閉じ、行の失敗に出す。新しい版の行の検証が通った後に、旧い版の行を `ndf progress retry` で検証し直すか行を中止するかはオーガナイザーが決める |
| 取りかかり中の環境へ新しい版を出そうとした | `ndf lanes release` が `stopped`・`precondition` で断り、まとめの id を返す。`batch close` の後に打ち直す |
| `batch claim` に違う環境・同じリポジトリの違う版・別のフェーズのリクエストが混ざる | `stopped`・`precondition` で断る |

### 単体テストの観点

- `load_phases`: 7 つの `id` と `requires` が本文の図と、`context`・`carrier` が本文の「コンテキストの依存と担い手」の表と同じ。輪を作る表・表に無い `id` を指す表で断る
- `decide`: `standard` の行で、デプロイが承認ゲートに着いた最初の `next` は `GATE` を返し、`present` が `gate_presented` を書いて結果に `next` を足す。その後の `next` で `START`（確定仕様化）が返る（`gate_presented` の無いゲートは越えない）。確定仕様化を始める前に回答が載っていれば、確定仕様化を始めずにデプロイの `CONSUME` が返る（回答が載ったゲートを動いている工程と読んで `CONSUME` を先頭で返すか、候補を先に返すと落ちる）。委ねた検証に回答が載った行でも、確定仕様化が動いている間は `RESUME`、終えた後に `_consume_delegated` が返る。回答が載った後でも、確定仕様化が動いている間は確定仕様化（`RESUME`）が返る。確定仕様化を終えた後に回答があればデプロイの `CONSUME`。検証が委ねた回答を待つ行は確定仕様化を候補に残す。振り返りは検証の前に候補に入らない。棚卸しは振り返りと確定仕様化の両方が終わるまで候補に入らない。`light` の棚卸しは確定仕様化が無くても候補に入る。クリーンアップは Pull Request の直後に候補に入る。クリーンアップが承認か作業の待ちを返す間は、デプロイも確定仕様化も候補に入らず、クリーンアップの待ちが返る。デプロイが動いている（ゲートの前）間は確定仕様化を始めない
- 旧形式のスプリント: `phases` を持たず `close` の工程を持つスナップショットの進行中のスプリント（確定仕様化 → クリーンアップの並び）を再開すると、今の並びのまま最後まで進み、`Env.hold` が 0 回呼ばれ、確定仕様化が実装の worktree を消される前に流れる。`requires` を引いて落ちない
- `asking.pending`: デプロイがリリース承認の質問を書いた後、`decide` が `START`（確定仕様化）を返す行と、確定仕様化が動いている（`RESUME`）行と、確定仕様化が宣言の重なりで待つ（`HELD`）行のどれでも、`ndf answer` と `ndf lanes release` が質問を見つけて回答を書ける（`decide` が `GATE` のときだけ探すと落ちる）。公開の CLI で確かめる: 確定仕様化が `START`・`RESUME`・`HELD` の各状態で、`ndf answer <スプリント> --gate <質問の id> …` が回答を書け（`answer_draft` が `decide` の `GATE` を求めると断られて落ちる）、`ndf lanes release` の回答の後の `ndf progress next` の入口（`take_reply`）も回答を書ける。質問の id が今のものと違う回答は断られる
- `Env.recheck`: リリース承認の回答が載った後、確定仕様化の実行中に承認対象（PR の head）を変え、`ndf progress work` の駆動で確定仕様化を終えると、同じ駆動の次の周がデプロイの回答を消費する前に `Env.recheck` が呼ばれ、回答を消費せずにゲートの待ちへ戻る（入口でだけ `recheck` を呼ぶと、入口では `RESUME` なので素通りし、消費して落ちる）。計画の外のスプリントでも同じ
- `phase_of`・`occupants`: `row_held` を後に持つ行は占めない。確定仕様化を終えて `row_left` を書いた後、リリース承認の回答を待って `row_idle` で止まった行は確定仕様化を占めず、宣言の重なる別の行が入れる。別のリポジトリのレーンと開発ループの行を数えない
- `delegate`・`_start` の委ねの枝: 検証に入った計画の行の `next` が `lane-verify` を 1 回だけ書いて待ちを返し、打ち直しで増えない。`done` の回答の後の `next` がリポートを `verification` へ写して委ねた工程の並びをすべて終える（`documentation` では体裁レビューとリリース後テストの 2 つが 1 件の回答で終わる）。`failed` の回答の後は `FAILED` になり振り返りへ進まず、`ndf progress retry` の後の `next` が新しい `lane-verify` を書く。棚卸しは `lane-backlog` を書いた `next` で工程が終わる。計画の外のスプリントは委ねない
- `batch`: 別の環境・同じリポジトリの違う版・別のフェーズのリクエストを混ぜた `claim` を断る。同じ環境を宣言した 2 つのリポジトリの `claim` は、片方が取りかかり中なら断る。別の保存先を持つ 2 つの計画が同じ環境の `claim` を同時に打つ（差し替えた排他の待ち合わせで並べる）と、片方だけが通る（計画ごとの `lock_plan` で確かめると両方通って落ちる）。棚卸しは別の計画の同じリポジトリでも同じ。オーガナイザー以外の書き手で断る。棚卸しの `failed` はリクエストを開いたまま残し、計画が完了しない。検証の `batch close` は、リクエストの閉じ・`batch_closed`・各行の `row_answered` と `delegate_answered` を 1 つのトランザクションで書き、3 行のうち 1 行目を書いた後に落とす（差し替えた書き込みで例外を出す）と何も残らず、打ち直すと 3 行すべてに回答が載る。閉じた後の打ち直しは何も書き足さない。`--observed-version` がリクエストの版と違う `done` は断られ、`failed` では閉じられる。取りかかり中の環境に入るリポジトリの `ndf lanes release` は断られ、`batch close` の後は通る。マシン全体の取りかかりの記録を消す前に落ちても、次の `claim` が通る
- `spec_overlaps`: 別のファイルの宣言は重ならず、glob（`docs/specifications/*.md`）と具体のパスは重なる。空の宣言（`none`）は重ならない
- `enter_phase`・`hold_phases`: `declare` は記録を書かない。候補を調べる `enter_phase` は待ちを書かず、確定仕様化が重なって検証に入れる行は `row_held`・`lane_held` を 1 件も書かない。全候補が待つときだけ 1 件書き、打ち直しても増えない。`hold_phases` の確かめ直しで待つ先が空いた工程は書かずに入り直す。`--spec-paths` で宣言して待った行を、`--spec-paths` 無しの `next` で起動し直すと、保存済みの宣言で入り、`declare` を返さない。待つ間に `--spec-paths` で宣言を `a.md` から `b.md` に直すと新しい `row_held`（`paths: [b.md]`）が書かれ、`a.md` を占めていた行が出ても起動し直さず、`b.md` を占めていた行が出たときに起動し直して `b.md` で入る（フェーズの並びだけで重複を判定すると古い宣言で入って落ちる）。実装の枝の `enter_phase` は外側で `lock_plan` を取らない（`Git.fetch_branch` を差し替えて止めた間に、別の接続の `lanes set` が待たずに通る）
- 承認と占有の待ちの重なり: リリース承認の回答を待ち、確定仕様化が宣言の重なりで待つ行は、`row_held`（`spec`）を書いたうえで `GATE` の結果（`metrics.held` 付き）を返し、`row_idle` は 1 件。回答が先に載ると回答の後の next が打たれてデプロイを消費し、その後の `next` が確定仕様化の待ちを照らす。重なりが先に解けると待ちの起動し直し（`released`）が打たれて確定仕様化に入る（`GATE` を返す段で待ちを書かないと、重なりだけが解けたときに起動し直されず落ちる）
- `holding`: 実装を始めた後に確定仕様化を待つ行が `holding` で、重なりが解けると `snapshot_of` の `released` に載り、`status` が `held` を出し、`judge` が `HELD` を返す。確定仕様化に入ると 3 つとも外れる
- `awaiting_next`・`idle`: `row_idle` → `row_answered` の行は真。`row_idle` → `row_active` → `row_answered` の行は偽（同じフェーズの次の工程やクリーンアップを始めた行、`declare` を返した行を含む）。`row_idle` を持たない行は今のとおり。待ちの起動し直し（`released`）も `idle` が偽の行には打たない
- 起動の取りかかり（`claim_relaunch`・`mark_active`）: 観測で `idle` だった行で、観測の後・置き換えの前に conductor が `next` で工程を始める（`row_active`）と、`claim_relaunch` が `stale` を返して `launch.replace` を呼ばない。逆に `claim_relaunch` が `relaunch` を書いた後に元の conductor が `next` を打つと、`mark_active` が断って工程を始めず `stopped` を返し、`--launch <キー>` を持つ起動し直しの `next` だけが工程を始める（差し替えた排他の待ち合わせで両方の順を作る）
- 起動の取りかかりの取り直し: `claim_relaunch` が `relaunch` を書いた後に差し替えた `launch.replace` が失敗すると、次の観測が同じキーの回答の後の next（待ちの起動し直しでも同じ）を出し、打ち直しの `claim_relaunch` が `claimed` を返して起動する（`idle_seq > active_seq` だけで照らすと `stale` で外れ、失敗にも数えられずに止まって落ちる）。起動した conductor が最初の `next` の前に落ちた行も、`Policy.up` の期限の後に同じキーで取り直して起動する。続けて失敗すると `policy.attempts` 回で `launch_failed` を 1 回知らせる。取り直した後に前の取りかかりのキーで `next --launch` を打つと断られる
- `row_idle` を持たない行の起動: 旧形式のスプリントの行と開発ループの行に回答が載ると、観測が回答の後の next を出し、`claim_relaunch` が書かずに `untracked` を返し、置き換えた後の入力（スプリントは `next` → `row resumed --answered`、開発ループは `row resumed` → `loop get`）で `row_resumed` が書かれるまで通る（起動の直前だけ `idle_seq > active_seq` を求めると `stale` で止まって落ちる）
- 置き換えられた conductor の占有: `claim_relaunch` が `relaunch` を書いた後、元の conductor の `next` が確定仕様化の境で `--spec-paths` を付けて `Env.hold` を呼ぶと、`row_entered`・`row_held` を 1 件も書かずに `stopped` を返し、別の行の確定仕様化を待たせない（`Env.busy` の前に `hold` が書くと落ちる）。実装の境（`begin_implementation`）と全候補の待ち（`hold_phases`）でも同じ。`enter_phase` が入った行は `row_entered` と `row_active` を 1 つのトランザクションで書き、その間に `claim_relaunch` が通らない
- `wakeable_lanes`: 上限 1・計画の並び B、A・`B.after=[A]` で、最初の読み直しが A を始め、B は始めない。A の行が実行中になった後の読み直しで空きが無ければ B を待たせ、A の行が終わった後に B を始める。上限 2 なら B も設計から始まる（席を残さずに B を先に始めると、B が設計の後に実装の待ちで席を持ち続け、A が始まらずに落ちる）
- 確定仕様化の作業場所: 確定仕様の Pull Request をマージした後・書くことが無かった後のどちらでも、`spec/<番号>-<主題>` の worktree とローカルのブランチが残らない。落ちた後の打ち直しは残ったマージ前のブランチを再利用し、マージ済みのブランチは作り直す

## 実装の待ちの判定と記録（`rules`・`lane`）

満たす決定: 決定 2・決定 3・決定 6・決定 8・決定 13・決定 14、受け入れ条件 1・2・4・5・6・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `implementation_wait` | `lane/rules.py` | 新設 | `(view: PlanView, lanes: Lanes, lane: str) -> list[str]` | レーンの `after` のうち `after_met` が偽の値を、計画の並びの順で返す。レーンが `idle` を持ち `touches` が空なら、末尾に印 `TOUCHES_UNSET` を足す |
| `next_row` | `lane/rules.py` | 変更 | 変えない | 行の入力が `kind: sprint` なら `waiting_after` を常に空にする。`loop` は今のとおり |
| `wakeable_lanes` | `lane/rules.py` | 変更 | `(view, lanes, free: Optional[int]) -> list[str]` | 始めてよいレーンを、`after` の起点が先に来る順（`after` の辺のトポロジカル順。同じ順位は計画の並び）で返す。#4050 では待つ行が席を返さないので、`after` の起点に未着手の行が残るスプリントの行（始めると設計の後に実装の待ちで席を持ち続ける行）は、`free` が 2 以上のときだけ返す（起点が始める席を 1 つ残す）。`free` が None（数えない呼び手）なら今のとおり。例: 上限 1・計画の並び B、A・`B.after=[A]` では A を先に始め、B は A の行が始まった後か空きが 2 になった読み直しで始める（B が先に席を取って A を締め出さない）。席を返す #4091 が入った後はこの残し方を外す |
| `_order_graph` | `lane/rules.py` | 変更 | 変えない | スプリントの行のレーンでは、レーン全体を指す `after` の辺を行ごとに引く。起点の末尾から（`_after_sources(ref, lanes, ended=False)`）引くのは、`LaneRow.design_phase` が真の行（未着手の行と、実装の待ちを持つ版で始めてまだ実装の開始を記録していない実行中の行）だけである。それ以外の行、つまり実装の開始を記録した行・終わった（完了か中止の）行・実装の待ちを持たない版で始めた実行中の行（旧形式。実装を始めたかを記録から読めない）へは、起点の終わった行からだけ（`ended=True`）引く。終わった行へ実行中の行から辺を引かないので、終わった行を経由して実行中の 2 行に後付けの前後ができない（実装の前に中止した C の `after` に実行中の A を足しても A→C を引かず、実装中の B の `after` に C を足した C→B と合わせても A と B の重なりを I13 が断る）。旧形式の行を実装中と読むのは安全側で、辺が減るぶん I13 は断る側へ倒れる。先頭の行と最初の未着手の行だけで決めない。開発ループの行のレーンは今のとおり行の状態で読み、最初の未着手の行へだけ起点の末尾から、始まった行と終わった行へは起点の終わった行から（`ended=True`）引く（決定 14） |
| `_after_sources` | `lane/rules.py` | 変更 | `(ref: str, lanes: Lanes, ended: bool) -> list[tuple[str, str]]` | `after` の 1 つが指す起点の行。`ended` が真なら、起点のうち終わった（完了か中止の）行だけを採る。レーンを指せばその終わった行の末尾、行を指せばその行が終わっていればその行で、どちらも無ければ空。偽なら今のとおりレーンの末尾の行か指した行。今の `started`（未着手でない行を採る）をやめる。実行中の行を起点に採ると、実行中の 2 行に後から足した `after` が前後の辺になり、I13 が重なりを見逃すため（決定 14） |
| `park_reason`（範囲外（#4091）。決定 15） | `lane/rules.py` | 新設 | `(row: LaneRow, gate_key: Optional[str], alive: Optional[bool]) -> Optional[str]` | 席を返す理由を 1 か所で決める。`parked` でない実行中の行で、(1) `holding` で `held_seq > seated_at_seq` なら `held` (2) `gate_key`（`gate_wait` が返したリクエストのキー）があれば `gate`（実装を始めた行も含む。リリース承認を待つ行は実装を始めた後にある） (3) `holding` か `awaiting_next` で `alive` が偽なら `vacant`。`alive` が None（ラッパーの一覧を読めない）なら (3) を決めない。どれでもなければ None。耐久ワークフローの観測（`_parks`）と書く前の再検証（`park`）が同じこの関数を読み、2 つの条件を食い違わせない。`gate_key` は、観測では `gate_wait` が、再検証では `lane.regate` が作る（どちらも「回答が今の着席の後に載っていない・今の質問の id のリクエストが開いている」を同じ条件で見る）。再検証の `alive` は、手の理由が `held`・`gate` なら None である。`vacant` なら、`park_lane` がマシンの起動の排他（`admit.lock`。`launch_lane` が `row_seated` の書き込みからラッパーの起動の戻りまで持つ）を取った中で、そのレーンのラッパーの有無を `launch.wrappers()` で 1 回だけ読み直した値を渡す（読めなければ None で、`vacant` の手は `stale`）。着席の番号が同じでも観測だけでは足りない。`launch_lane` は起動の前に `row_seated` を書くので、`row_seated` のコミットの後・起動の戻りの前の観測は、席を取り直したばかりの着席でラッパーが無いと見る。その観測の `vacant` の手を観測の値だけで通すと、起動途上の着席に席を返し（`stopped` は偽）、その後に現れたラッパーの行を次の観測が `released` で起動し直して、動いている conductor を置き換える。起動の排他の中で読み直せば、起動途上なら排他が空くまで待ってからラッパーが載った一覧を読むので外れ、起動が失敗したなら `launch_lane` が先に `launch_failed` で席を返していて共通の条件（既に `parked`）で外れる。読み直しは `vacant` の手のときだけで、観測あたりの一覧の読み取り（高々 1 回）は変えない（決定 13） |
| `begin_implementation` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, stage: str, base: Callable[[], BaseRef], git: Callable[[], Git], launch: Optional[str]) -> Begin` | 下の「手順」で、待ちか実装の開始のどちらかを、計画の排他の中で 1 回で決めて書く。`base`・`git` は Git が要ると決まったときだけ呼ぶ（`Git.remote()` も `git worktree list` もそれまで打たない） |
| `review_drift` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, reason: str) -> None` | 実装の開始を記録し `review` が真の行に `row_drift_reviewed` を書く（確認待ちの判定は `review && !drift_reviewed` の 1 つで、`drift` の値を見ない）。記録が無い・`review` が偽・既に書いた行は書かない |
| `changed_paths` | `lane/__init__.py` | 新設 | `(git: Git, base_sha: str, head_sha: str) -> Optional[list[str]]` | 計画の排他の外で `git diff --name-only <base_sha>..<head_sha>` を 1 回打ち、変わったパスを返す。読めなければ None |
| `drift` | `lane/__init__.py` | 新設 | `(paths: Optional[Sequence[str]], touches: Sequence[str]) -> Optional[list[str]]` | 計画の排他の中で、`changed_paths` の結果のうち最新の `touches` のどれかと `touch_hit` で当たるものを昇順で返す。`paths` が None なら None。Git を読まない |
| `regate`（範囲外（#4091）。決定 15） | `lane/__init__.py` | 新設 | `(view: PlanView, row: LaneRow, act: ParkAct, current_gate: Callable[[], Optional[str]]) -> Optional[str]` | 再検証の `gate_key` を DB だけで作る。次のすべてを満たせば `act.request`、どれかが欠ければ None: (1) 回答が今の着席の後に載っていない（`answered_seq <= seated_at_seq`。`gate_wait` の (2) と同じ条件） (2) 計画のリクエスト `act.request` が開いている (3) `current_gate()` が `act.gate`（観測した質問の id）と同じ (4) そのリクエストの中身が `act.gate` と行のスプリントに合う（`lane_question` は `metadata.gate`・`metadata.sprint`、`lane-release` は `id` の `lane-release-<質問の id>`・`sprint_state`）。`park` と `park_lane` の手順 3 が呼ぶ |
| `park`（範囲外（#4091）。決定 15） | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, act: ParkAct, current_gate: Callable[[], Optional[str]]) -> Optional[int]` | `lock_plan` の中で行を読み直し、`park_lane` の手順 1 の再検証を通った実行中の行にだけ `row_parked`（書き手 workflow。中身に `reason`・`stopped`（停止の予定）・`request`・`gate`）を書く。外れれば書かずに None。`current_gate` はスプリントが今待つ承認ゲートの質問の id を DB だけで読む関数で、呼び手（`plan/run.py`）が `gate.asking.waiting` から作って渡す（`lane` は `gate` を import しない。`record_resumed` の `consumed` と同じ形） |
| `seat`（範囲外（#4091）。決定 15） | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, launch_key: str, fail_key: str) -> Optional[int]` | `lock_plan` の中で行を読み直し、席を返した行に `row_seated`（書き手 workflow。中身に `launch`・`fail`）を書く。席を返していない行は書かない。`launch_lane` はラッパーを起動する前に呼ぶ |
| `unpark`（範囲外（#4091）。決定 15） | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, seat: int, reason: Literal["stop_failed", "gate_changed"]) -> Optional[int]` | `lock_plan` の中で行を読み直し、席を返した行で `seated_at_seq` が `seat` と同じときだけ `row_unparked`（書き手 workflow）を書く。`row_seated` と違い今の着席を新しくしない（`seated_at_seq` を進めない）ので、`held_seq > seated_at_seq` も `gate_wait` も元のまま成り立ち、次の読み直しで同じキーの席を返す手になる。`park_lane` がラッパーを止められなかったときと、止める直前に質問の id が変わったときに呼ぶ（決定 13） |
| `mark_stopped`（範囲外（#4091）。決定 15） | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, seat: int) -> Optional[int]` | `lock_plan` の中で行を読み直し、`stop_pending` が真で `park_seat` が `seat` と同じ行にだけ `row_stopped`（書き手 workflow。中身に `seat`）を書く。外れれば書かずに None。`park_lane` が `launch.stop` の成功の後と、停止の再開でラッパーが既に動いていないと読んだときに呼ぶ |
| `end_row` | `lane/__init__.py` | 変更 | 変えない | `row_ended` と同じトランザクションで、計画に `lane_row_ended`（`lane`・`row`・`outcome`）を書く |
| `LaneRow.holding` | `lane/types.py` | 新設 | `@property -> bool` | 節「フェーズの表・順序・排他」の同じ名前の行の定義（行全体で 1 つ）。実装の待ちの行は `held_phase` が `implement` の `holding` の行 |
| `LaneRow.implementing` | `lane/types.py` | 新設 | `@property -> bool` | `implementing_seq > 0` |
| `LaneRow.gated` | `lane/types.py` | 新設（欄） | `bool` | 行の `row_started` の中身の `gated`（実装の待ちを持つ版の `start_row` が真で書く）。無い行（更新の前に始めた行）は偽 |
| `LaneRow.design_phase` | `lane/types.py` | 新設 | `@property -> bool` | `state == PENDING`、または `state == RUNNING and gated and not implementing`。I13 のグラフで起点の末尾から辺を引く行はこれだけ（`_order_graph`） |
| `start_row` | `lane/__init__.py` | 変更 | 変えない | `row_started` の中身に `gated: true` を足す（更新の前に始めた実行中の行と見分ける） |
| `LaneRow.awaiting_review` | `lane/types.py` | 新設 | `@property -> bool` | `implementing and review and not drift_reviewed`。確認待ちの判定はこの 1 か所に置き、`begin_implementation` の手順 1・`lane_env` の `hold`・`peek` はどれもここを読む（`drift` の空・null で分けない） |
| `LaneRow.parked`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設 | `@property -> bool` | `state == RUNNING and parked_seq > max(seated_seq, unparked_seq)`（席を返した行。回答が載っても `row_seated` まで真。`row_unparked` で偽に戻る） |
| `LaneRow.unparked_seq`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設（欄） | `int` | 最後の `row_unparked` の番号。`seated_at_seq` には入れない |
| `LaneRow.stopped_seq`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設（欄） | `int` | 最後の `row_stopped`（席を返した行のラッパーの停止を確かめた）の番号 |
| `LaneRow.stop_pending`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設 | `@property -> bool` | `parked and park_stop and stopped_seq < parked_seq`。止める予定で席を返し、その `row_parked` の後に停止を確かめていない行。記録だけで決まり、`wrapper_table` が一覧を読むかの条件と `stopping` の前提に使う |
| `LaneRow.park_seat`・`LaneRow.park_stop`・`LaneRow.park_reason`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設（欄） | `int`・`bool`・`Optional[str]` | 最後の `row_parked` の `seat`（席を返した着席の番号）・`stopped`（ラッパーを止める予定だったか）・`reason`。同じ着席の停止を再開する手がこれを読む |
| `LaneRow.seat_launch`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設（欄） | `Optional[str]` | 最後の `row_seated` の `launch`（今の着席を開いた続きの手の起動のキー） |
| `LaneRow.seat_fail`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設（欄） | `Optional[str]` | 最後の `row_seated` の `fail`（席を取り直した続きの手の失敗のキー。`Launch.fail_key`） |
| `LaneRow.seat_progressed`（範囲外（#4091）。決定 15） | `lane/types.py` | 新設 | `@property -> bool` | 今の着席の間（`seated_at_seq` より後）に、`row_held`・`row_entered`（`implement`）・回答の番号を持つ `row_resumed`（`--answered` で書いたもの）のどれかがある。回答の無い `row_resumed`（起動の試行の印）は数えない。起動し直した conductor が何も進めずに終わったかを読む |
| `LaneRow.hold_base` | `lane/types.py` | 新設（欄） | `Optional[tuple[str, str]]` | 行の最初の `row_held` の（`ref`・`base_sha`）。後の `row_held` で上書きしない。差分の基準はこれだけで、着席ごとの待ちの記録（`held`・`held_seq`）と分ける |
| `LaneRow.seated_at_seq` | `lane/types.py` | 新設 | `@property -> int` | 今の着席の始まりの番号（レーンのステートマシンの事象の番号）。`seated_seq` があればそれ、無ければ行の開始の事象の番号 |

`BaseRef` は（`remote`・`branch`・`ref`（`<remote>/<branch>`））で、呼び手（`lane_env`）が `Git.remote()` とブランチ戦略の設定から組む関数として渡し、`begin_implementation` が Git を読むと決めたときだけ呼ぶ。`origin` を決め打ちしない。

`begin_implementation` の手順:

1. 計画の排他の外で、行とレーンを DB から読む（`sprint_row`）。属さなければ `Begin(outside=True)`（計画の外のスプリント。何も書かない）。行が `running` でなければ、Git を読まずに `Begin(ended=<行の状態>)` を返す（3 と同じ）。行が既に `implementing` なら、書かずに今の記録を返す（`review` が真で `drift_reviewed` が偽なら確認待ち。`drift` が null でも同じ）
2. 同じ読み取りで `implementation_wait` を求め（DB だけ）、次の 5 つに分ける。Git を読むのは (c)・(d) だけである
   - (a) 今の着席の待ちがあり（`holding` で `held_seq > seated_at_seq`）、待つ先が残る: Git を読まずに今の待ちを返す（書かない）
   - (b) 待ったことが無く（`holding` でない）、待つ先も無い: Git を読まずに 3 へ進む（`after` がすべて満たされた行も、初回の `next` で `fetch` しない）
   - (c) 待つ先があり、待ったことが無い（`held_seq == 0`）: `base()`・`git()` を呼び、`Git.fetch_branch(<remote>, <branch>)`（宛先を明示した refspec `+refs/heads/<branch>:refs/remotes/<remote>/<branch>` の取得。clone の fetch refspec が対象のブランチを追跡しなくても `<ref>` が更新される。`git fetch <remote> <branch>` だけでは `FETCH_HEAD` しか更新されないことがある） を 1 回打ってから `git rev-parse <ref>` で `base_sha` を読む
   - (c') 待つ先があり、前の着席で待ったが今の着席の待ちが無い（起動し直した conductor がまだ待つ先を見た）: Git を読まない。新しい `row_held` の `base_sha`・`ref` には、行の最初の待ちの値（`LaneRow.hold_base`）を写す
   - (d) `holding` で待つ先が無い（待ちが解けた）: `base()`・`git()` を呼び、(c) と同じ `Git.fetch_branch` と `git rev-parse <ref>` で今のコミットを読み、`changed_paths(git, hold_base.base_sha, 今のコミット)` で変わったパスを読む。基準は最後の `row_held` でなく行の最初の待ちのコミットなので、最初の待ちから再着席までに入った変更も漏れない
3. トランザクションを始め、`lock_plan` を取り、最新の計画とレーンを読み直す。先に行の状態を確かめ、行が `running` でなければ（2 の取得の間にオーガナイザーが中止した・行が終わった）、何も書かずにトランザクションを抜け、`Begin(ended=<行の状態>)`（`completed` か `cancelled`）を返す（終わった行に `row_held`・`row_entered`（`implement`） を書かない）。続けて `conductor_check(row, launch)` を通し、キーが返れば何も書かずに `Begin(replaced=<キー>)` を返す。`running` なら `implementation_wait` を求める。5 で `row_entered`（`implement`）を書くときは、`enter_phase` の手順 4 と同じく、行が `idle` か `relaunching` なら同じトランザクションで `row_active`（`reason: enter`）も書く。排他の中では Git を読まない
4. 空でなければ: 今の着席の待ちがあれば書かずに今の待ちを返す。そうでなく (c) なら 2 の値、(c') なら `hold_base` の値を `ref`・`base_sha` にして、`row_held` と `lane_held` を `checked_write` で書いて新しい待ちを返す。(d) の行（前に待った行で、2 と 3 の間に `lanes set` が `after` を足した）も、Git を読み直さずに `hold_base` の値で同じく書く。(b) の行（待ったことが無く `base_sha` を読んでいない）は、書かずにトランザクションを抜けて 1 から 1 回だけやり直す。2 回目も同じなら `stopped`・`precondition` で止める
5. 空でも、3 で読み直した行が `parked`（`park_lane` が先に `row_parked` を書いた）なら、`row_entered`（`implement`） を書かずに今の待ちを返す（席を取り直した後の `next` で始める）。空なら: (c)・(c') の行（2 と 3 の間に待つ先が無くなった）は、書かずにトランザクションを抜けて 1 から 1 回だけやり直す（(b) か (d) として読み直す）。(d) の行は `drift(2 のパス, 最新の touches)` を求め、`row_entered`（`implement`）（`stage`・`drift`・`base_sha`・`review`（`drift` が空でないか null なら真））を書く。(b) の行は `row_entered`（`implement`）（`stage`、`review` は偽）を書く。どれも `checked_write` で書き、`Begin(started=True, drift=…, review=…)` を返す。`checked_write` は I13 のグラフを、書いた後の「実装の開始」で組み直して確かめる

- 事前条件: 呼び手は `store.run_lock`（スプリントごと）を持っている
- 事後条件: 待ちを返したとき、行の `holding` が真で、今の着席の間の `row_held` は 1 件だけある（I2）。開始を返したとき、`row_entered`（`implement`） は 1 件だけあり、書いた時点の最新の計画で `implementation_wait` が空だった（I9）
- 副作用: 計画とレーンのステートマシンへの書き込み（同じトランザクション）。Git は 2 の (c)・(d) だけで、`Git.fetch_branch` と `git rev-parse` を高々 1 回ずつ、(d) で `git diff` を高々 1 回（すべて排他の外）。(a)・(b)・(c') の行は `Git.remote()` も含めて Git を 1 回も打たない（やり直しの 1 回を除く）

計画の排他を取るほかの書き込み（`lanes set`・`lane add`・`row add`・割り当て）は、どれも今の `checked_write` か `lock_plan` を通る。手順 3〜5 が 1 つのトランザクションなので、これらは `row_entered`（`implement`） の前に入って待ちを生むか、後に入って実装を始めた行を含むグラフで I13 の検査を受けるかのどちらかになる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `begin_implementation` | 計画の決まりで断られる | 待ちか開始の書き込みが I13 などに当たる | `checked_write` | 呼び出し元へ返す | `next` が `stopped`・`precondition` と断った理由 | 書かない。直して `next` を打ち直せる |
| `begin_implementation` | 取得か参照の読み取りに失敗する | ネットワーク・認証・基のブランチが無い・リモート名の設定の誤り（`RemoteError`） | 手順 2 の `Git.fetch_branch`・`git rev-parse` の終了コード | 待ちも開始も書かずに止める（古い参照で `base_sha` を書かない） | `next` が `stopped`・`work_stopped`、`summary` に打ったコマンドと理由 | 打ち直せる |
| `begin_implementation` | 行が終わっている | 手順 2 の取得の間にオーガナイザーが行を中止した（`lanes row cancel` など） | 手順 3 の排他の中の読み直し（と手順 1） | 書かずに `Begin(ended=…)` を返す | `next` が `stopped`・`precondition`、`summary` に行の終了の状態 | 書かない。終わった行は打ち直しても同じ結果 |
| `begin_implementation` | 排他の間に依存が増え、Git を読んでいない | 手順 2 と 3 の間の `lanes set` | 手順 4 | 1 回だけやり直す | 2 回目も同じなら `stopped`・`precondition` | 打ち直せる |
| `changed_paths` | 差分を読めない | `base_sha` が取得した履歴に無い（強制 push など） | `git diff` の終了コード | 既定値で続ける | `drift` を null・`review` を真で記録し、`warnings` に理由。null は空と区別し、確認の結果を返す（判断の記録を求める） | 工程は判断の記録の後に始める。打ち直しても起動し直しても、記録の `review` から確認の結果を返す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Hold` | 型（`waiting`・`stage`・`ref`・`base_sha`・`seq`・`new: bool`） | `lane/types.py` | — |
| `Begin` | 型（`outside`・`ended: Optional[str]`（行が終わっていたときの状態 `completed`・`cancelled`）・`held: Optional[Hold]`・`started`・`drift: Optional[list[str]]`・`review: bool`・`replaced: Optional[str]`（`conductor_check` が断った起動のキー）） | `lane/types.py` | — |
| `BaseRef` | 型（`remote`・`branch`・`ref`） | `lane/types.py` | — |
| `ROW_HELD`・`ROW_IMPLEMENTING`・`ROW_DRIFT_REVIEWED`・`LANE_HELD`・`LANE_ROW_ENDED` | 定数（事象の種類） | `lane/types.py` | `"row_held"`・`"row_entered"`・`"row_drift_reviewed"`・`"lane_held"`・`"lane_row_ended"` |
| `TOUCHES_UNSET` | 定数（待つ先の印） | `lane/rules.py` | `"touches"`（レーン名と重ならないよう、レーン名の規則が許さない形にする。実装で `:touches` など規則の外の字を使う） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じスプリントの `next`・`work` を 2 本 | スプリントのステートマシン・レーンの行 | 今の `store.run_lock`（スプリントごと）の中で `begin_implementation` を呼ぶ | 待ちなら書かずに今の待ちを、開始済みなら今の記録を返す |
| `begin_implementation` と `lanes set`・`lane add`・`row add`・割り当て | 計画とレーンのステートマシン | 手順 3〜5 を 1 つのトランザクションにし、`lock_plan` の中で依存の確かめと書き込みを行う。Git の読み取りは排他の外（手順 2）だけ | 開始の前に落ちれば何も書かれず、待ちの行のまま起動し直しの候補に残る |
| `begin_implementation` と席を返す手（`park_lane`） | レーンの行 | どちらも `lock_plan` の中で行を読み直してから書く。`row_parked` の後の `begin_implementation` は実装を始めず、`row_entered`（`implement`） の後の `park_lane` は再検証で外れる | 先に書いたほうだけが通る。止めた後は起動し直しが席を取り直して続ける |
| `begin_implementation` と耐久ワークフローの読み直し | 計画とレーンのステートマシン | 今の `lock_plan` | 読み直しは書いた後の状態を次の周で読む |

### 単体テストの観点

- `implementation_wait`: 行を指す `after`・レーンを指す `after`・行の無いレーンを指す `after`（満たさない）の 3 通り。`idle` を持ち `touches` が空のレーンは `after` を満たしても印を返す
- `next_row`: スプリントの行は `after` があっても `waiting_after` が空、開発ループの行は今のとおり
- `_order_graph`・`_after_sources`（決定 14）: A と B が別の場所を触り、両方の行が実装を始めた後（A の行は `running`）、`lanes set` で B の `touches` を A と重なる値にして `after: [A]` を足すと I13 で断られる（実行中の行を起点に採ると通って落ちる）。行を指す `after: [A/r1]`（r1 が `running`）でも同じ。A の行が終わった後なら通る。開発ループの始まった行でも同じ。
- `_order_graph`（終わった行を経由する経路）: A・B が同じ場所を触り、A は実装を始めた実行中、B は実装を始めた実行中、C は実装の前に中止した行のとき、`lanes set` で C の `after` に A を足し、続けて B の `after` に C を足すと、B の `touches` を A と重ねる書き込みが I13 で断られる（終わった C へ A の末尾から辺を引くと A→C→B の経路ができて通り、落ちる）
- `_order_graph`（更新をまたぐ行）: `gated` を持たない `row_started`（更新の前の版で始めた行）の実行中の B と実行中の A について、後から B の `after` に A を足して `touches` を重ねると I13 で断られる（`implementing` の有無だけで設計中と読むと A の末尾から辺を引いて通り、落ちる）。同じ組で、`gated` を持つ B（設計中）なら今の規則どおり通る
- `_order_graph`: A と B が同じ場所を触り `B.after=[A]`、両方が未着手のとき、B の `row start` も、続く A の `row start` も I13 で断られない。B が実装を始めた後に A へ行を足しても、始めた時点で終わっていた行からだけ辺を引く（今のテストが変えずに通る）。B の先頭の行が実装を始めた後に A へ未着手の行を足し、その A を待つ B の 2 行目を `row start` で設計へ進めても断られず、2 行目が `running` の間も A の末尾から 2 行目への辺が残る
- `begin_implementation`: 1 回目は待ちを書き、2 回目は書かない。`row_seated` の後にまだ待つ先があれば、Git を打たずに、最初の待ちの `base_sha` を写した新しい待ちを 1 件書く。依存が解けた後は `row_entered`（`implement`） を 1 回だけ書き、`row_resumed` だけでは待ちのまま。手順 2 と 3 の間に `lanes set` で満たしていない `after` を足すと開始を書かない。`after` が空の行と、`after` がすべて満たされた待ったことの無い行では、Git の呼び出し（`Git.remote()`・`git worktree list` を含む）が 0 回で、取得が失敗する作業場所でも実装を始める。今の待ちがあり依存が未解決の行で打ち直しても Git の呼び出しが 0 回。取得の失敗で何も書かない。計画の外のスプリントは `outside`。手順 2 の `Git.fetch_branch`（差し替えて止める）の間に行を中止すると、`row_held`・`row_entered`（`implement`） を書かずに `Begin(ended="cancelled")` を返し、`next` が工程を起動しない（排他の中で `running` を確かめないと終わった行に書いて落ちる）。(c)・(c')・(d) のどの経路でも同じ
- `drift` の算出: 最初の待ちの後、席を返す前に `touches` に当たるファイルを基のブランチへ入れ、再着席で依存が増えて待ち直し、その後に待ちが解けると、そのファイルが `drift` に載る（差分の基準を最後の `row_held` にすると落ちる）。手順 2 と 3 の間に `lanes set` で `touches` を広げると、広げた先に当たる変更も `drift` に載る。差分を読めず null で記録した後に conductor を起動し直しても、`--drift-reviewed` まで工程を起動しない
- `changed_paths`・`drift`: `touches` に当たるファイルだけを返す。fetch refspec が基のブランチを追跡しない clone（`remote.<名前>.fetch` を別のブランチだけにした作業場所）でも、取得の後の `<ref>` が取得したコミットを指し、待ちの `base_sha` と一覧の `ls-tree` が同じ参照を読む（`git fetch <remote> <branch>` にすると古い追跡参照を読むか参照が無くて落ちる）。`base_sha` が無いと null と warnings。待ち始める前に追跡参照が古かった作業場所でも、取得した後の参照が `base_sha` になる。リモート名が `upstream` の作業場所で同じ結果
- `end_row`: `row_ended` と `lane_row_ended` が同じトランザクションで 1 件ずつ

## 工程を始める口（`run`・`answering`）

満たす決定: 決定 2・決定 6・決定 16・決定 17、受け入れ条件 2・5・6

`Env.hold` の契約は節「フェーズの表・順序・排他」の関数表の 1 つだけで、実装のフェーズも確定仕様化もほかのフェーズも同じ口を通る（`Env.hold(conn, sprint, stage, phase, paths) -> Optional[HoldResult]`）。この節は、その口を `next`・`work`・`wait`・`status` がどう使うかと、実装の枝の結果の読み方だけを書く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Env.peek` | `run.py` | 新設の欄 | `Callable[[Any, str], Optional[HoldResult]]`（既定は常に None） | 書かずに今のフェーズの待ち（`holding` の行の `held_phase` ごとの `phase_wait`。宣言は `held_paths`）か、実装の確認待ち（`awaiting_review`）を読む口。`wait` と `next` の手の組み立てが使う |
| `wait` | `run.py` | 変更 | `(st, machine_id, timeout, sleep=…, *, clock=…, env: Optional[Env] = None)` | 作業が止まった後の手を `report(v, decide(v), env)` で組む。`env` が None なら今と同じ |
| `_cmd_wait` | `run.py` | 変更 | 変えない | `wait(st, a.machine_id, a.timeout, env=a.env)` で、CLI の入口（`gate/entry.py` の `run.with_env(a, lane_env(…))`）が入れた `Env` を渡す。今は `Env` を捨てて `report(v, decide(v))` を呼ぶので、渡さないと `peek` に届かない |
| `_start` | `run.py` | 変更 | 変えない | 節「フェーズの表・順序・排他」の `_start`（候補を順に見て、まだ入っていないフェーズの工程なら `Env.hold`、委ねるフェーズなら `Env.delegate`、全候補が待てば `Env.hold_all`）。結果の分岐は下の「処理の手順」の表の 1 つで、フェーズに依らない |
| `_drive` | `run.py` | 変更 | 変えない | `inline`（`ndf progress work`）で工程をまたいで流すとき、`_start` が工程を始めない結果（`_hold_report`・`_declare_report`・`_drift_report`・待機中の待ち・`ended`）を返したら流しを止め、その結果を返す（`work.log` に残すだけにせず、スプリントの作業の結果として記録する）。各周で回答を消費する手（`CONSUME`）を書く直前に `Env.recheck` を呼ぶ |
| `report` | `run.py` | 変更 | `(v: View, m: Move, env: Optional[Env] = None) -> dict` | 次の手が `START` で、その先頭の候補がまだ入っていないフェーズの工程なら `env.peek` を読み、待ちか確認待ちを返せば、通常の次手（工程の起動）の代わりに `_hold_report`・`_drift_report` を返す。`next` を打ち直す手を作らない |
| `_cmd_next` | `run.py` | 変更 | `--spec-paths <パス>...`・`--drift-reviewed <理由>`・`--launch <キー>` を足す | `--drift-reviewed` があれば `Env` の口から `lane.review_drift` を呼んでから工程を選ぶ。`--spec-paths` は `_start` が `Env.hold` の `paths` に渡す。`--launch` は `lane_env` が `Env.busy`（`mark_active`）に渡す |
| `_hold_report` | `run.py` | 新設 | `(v: View, held: HoldResult) -> dict` | 入出力のコントラクトの待ちの形の結果（`held.phase`・`waiting`・`stage`、確定仕様化なら `paths`。`next` を持たない） |
| `_declare_report` | `run.py` | 新設 | `(v: View, s: StageDef) -> dict` | 入出力のコントラクトの宣言の形の結果（`next` は `--spec-paths` 付きの `next`） |
| `_drift_report` | `run.py` | 新設 | `(v: View, s: StageDef, drift: Optional[list[str]]) -> dict` | 入出力のコントラクトの確認の形の結果（`next` は `--drift-reviewed` 付きの `next`） |
| `lane_env` | `gate/answering.py` | 変更 | 変えない | 節「フェーズの表・順序・排他」の `lane_env` の行のとおり。実装の枝の Git の部品（`BaseRef`（`Git.remote()` とブランチ戦略の設定）を組む関数と `Git` を作る関数）は、呼ばずに `enter_phase` へ渡す |
| `_row_item`（`status`） | `plan/__init__.py` | 変更 | 変えない | 下の節「表示と知らせ」。`holding` の行の待つ先は `phase_wait` で求める（`implementation_wait` に決め打ちしない） |

- `_start` が `Env.hold` を呼ぶのは、候補の工程の `phase` が無いフェーズでなく、`_entered(v, s)` が偽（その行がそのフェーズの工程をまだ 1 つも始めていない）のときだけである。同じフェーズの 2 つ目以降の工程とクリーンアップは `Env.hold` を呼ばず、`Env.busy` だけを呼んで始める（`light` で最初の工程が `implement` のときも同じ規則で当たる）
- `Env.peek` は DB だけを読み、Git も GitHub も読まない。`wait` の回数あたりの読み取りは、レーンの行の 1 回の読み取りだけ増える（計画の外のスプリントでは増えない）
- `ndf lanes status`（`_row_item`）・生死の判定（`judge`）・起動し直し（`snapshot_of` の `released`）は、`next` と同じく `LaneRow.holding` と `phase_wait`（宣言は `held_paths`）を読む。4 つの経路が同じ待ちを同じ規則で見る

### 処理の手順

`_start` が `Env.hold`（と `Env.hold_all`）の結果で決める分岐。フェーズに依らず 1 つの表で、`next`・`work`（`_drive`）・`wait`（`report` は `peek` で同じ結果を書かずに読む）が同じ結果を返す。

| `HoldResult` | 意味 | `_start` の扱い | 結果 |
| --- | --- | --- | --- |
| None | 計画の外のスプリント | 工程をそのまま始める | 今と同じ |
| `ended` | 行が終わっている（取得の間に中止された行を含む） | 工程を始めずに止める | `stopped`・`precondition`。`summary` に行の終了の状態。`next` を持たない |
| `enter` | `row_entered` を書いた（実装なら `drift` を持つ） | `Env.busy` の後に工程を始める。実装で待った行なら `drift` を結果の `metrics.drift` に載せる（空も載せる。待たなかった行は載せない） | 工程を起動した結果 |
| `review` | 実装の開始を記録し、変わったファイルが空でないか読めない（記録済みの確認待ちを含む） | 工程を始めず、ほかの候補へ進まない | `_drift_report` |
| `declare` | 確定仕様化の宣言が無い | 待ちを書かずに次の候補を見て、全候補の後で返す | `_declare_report` |
| `held` | 待つ先がある（書いていない `HoldItem`） | 次の候補を見る。全候補が待てば `Env.hold_all` | 節「フェーズの表・順序・排他」の `_start` の優先順（待機中の待ちがあればその結果に `metrics.held` を添え、無ければ `_hold_report`） |
| `retry`（`hold_all` だけ） | 確かめ直すと待つ先が空いた | その工程の `Env.hold` を 1 回だけ呼び直す | 上のどれか |

実装のフェーズの `enter_phase`（`begin_implementation`）の結果の内訳:

| 条件 | 書くもの | `HoldResult` |
| --- | --- | --- |
| `implementation_wait` が空・待ったことが無い | `row_entered`（`implement`） | `enter`（`drift=None`） |
| `implementation_wait` が空・待ちの行（`holding`）・変わったファイルが空 | `row_entered`（`implement`） | `enter`（`drift=[]`） |
| `implementation_wait` が空・待ちの行・変わったファイルが空でないか読めない | `row_entered`（`implement`）（`review: true`） | `review`（工程は始めない） |
| 開始を記録済み・確認待ち（`review` が真で `drift_reviewed` が偽。`drift` が null の行も含む） | 書かない | `review` |
| 開始を記録済み・確認待ちでない | 書かない | `enter`（`drift=記録の値`） |
| `implementation_wait` が空でない | 書かない（待ちは `hold_phases` が書く） | `held` |

待ちが解けたことは `row_entered` だけで読むので、起動し直しの `next` → `row resumed`、ラッパーの conductor がそのまま打つ `next`、オーガナイザーが打つ `next`、`work` のどの経路でも同じ表を通り、`drift` と保存済みの宣言が届く。

### 単体テストの観点

- `standard` のスプリントで「計画」の手前と確定仕様化の手前でだけ `Env.hold` が呼ばれ、「要求と受け入れ条件」〜「ドキュメントレビュー」の 2 つ目以降の工程・同じフェーズの 2 つ目以降の工程・クリーンアップでは呼ばれない（クリーンアップと同じフェーズの次の工程では `Env.busy` だけが呼ばれる）
- `light` のスプリントで最初の工程の手前で待つ。`drift` が空でないときは、工程の作業を起動しない（差し替えた作業の起動の呼び出しが 0 回）まま確認の結果を返し、`--drift-reviewed` の後に起動する
- 設計の工程で `ndf progress work` を起動した worker が「計画」の手前まで進んで待ちを記録した後、CLI の `ndf progress wait`（`gate/entry.py` の入口から `lane_env` の `Env` で打つ経路）が `held` を `next` 無しで返す。確定仕様化の宣言の重なりで待った行でも同じ形（`held.phase` が `spec`）を返す。`Env` を渡さない `wait` の呼び出しに戻すと、工程を起動する `next` を返して落ちる
- `row resumed` の後、`next` の前に止めたスプリントで、次の `next` が `drift` を返す。ラッパーの conductor が起動し直しより先に `next` を打っても `drift` が返る
- `drift` が null で記録された行で `next` を打ち直しても、起動し直した conductor が `next` を打っても、確認の結果を返して工程の作業を起動しない。`--drift-reviewed` の後にだけ起動する
- 実装の後に確定仕様化を待つ行で、`next`・`wait`・`status`・`judge` が同じ待つ先（`phase_wait` の `spec`）を返す（`status` が `implementation_wait` を読むと空を出して落ちる）
- `Env.hold` の既定（計画の外）で工程の順が今と同じ

## マシンの数と生死の判定（`machine`・`liveness`）

満たす決定: 決定 4・決定 11、受け入れ条件 3、I3・I8

席を返したかは、レーンのステートマシンの `row_parked`・`row_seated` だけで読む（`LaneRow.parked`）。数え方と生死の判定はこの記録を読み、スプリントを読む CLI（`progress wait --timeout 0`）を数えるたびに打たない。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `lanes_of` | `lane/machine.py` | 変更 | 変えない | **#4050 では変えない（実装の待ちの行も今の承認待ちと同じく実行中として数える）。下の記述は #4091 の材料。** レーンの「実行中の行を持つ」を、実行中の行のうち `parked` でなく、今の着席の待ちを持たない（`holding` で `held_seq > seated_at_seq` でない）行があるかで決める。数えないのは、席を返した行と、今の着席の間に待ちを記録した行（ラッパーが動いていれば `count` が一覧の側で数え、止めた後は `parked`）だけである。席を取り直した行（`row_seated` の後、`next` が実装の開始を記録するまで `holding` が真のまま）は、ラッパーが一覧にまだ現れなくても記録だけで予約として数える。`count` はレーンの名前とラッパーの名前の和集合で数えるので、ラッパーが現れても 2 重に数えない。同じ計画（`admitted` の `own`）も他の計画（`read_lanes` が読む計画）も、この関数の同じ規則で数える |
| `held_seats` | `lane/machine.py` | 変更 | 変えない | **#4050 では変えない。下の記述は #4091 の材料。** ハブへ渡す席を、`lanes_of` と同じ規則で決める（席を返した行のレーンを渡さない） |
| `judge` | `lane/liveness.py` | 変更 | 変えない | **#4050 では、席を返していない実行中の行が `holding` なら `HELD`（`stall: false`）を返すだけにする。`parked` の行の読み方は #4091。** 実行中の行が `parked` なら、回答が席を返した後に載っていれば今の `RESUMING`、`holding` なら `HELD`、どちらでもなければ（承認の回答を待つ）`WAITING_ANSWER` を返す（最後の `row_parked` の理由が `launch_failed` でも同じ規則で読む）。席を返していない行が `holding` なら `HELD`（`stall: false`）を返す。どちらもスプリントもラッパーも読まない |
| `wrapper_table`（範囲外（#4091）。決定 15） | `lane/liveness.py` | 新設 | `(lanes: Mapping[str, list[LaneRow]], probes: Probes) -> Optional[Mapping[str, Mapping[str, Any]]]` | 観測で 1 回だけラッパーの一覧（`probes.wrappers()`）を読む。読むのは、`judge_lanes` が判定に要る行（今の `needs`）か、`_parks` が要る行（`parked` でない実行中の行のうち `holding` か `awaiting_next` のもの、と `LaneRow.stop_pending` が真の行）が 1 つでもあるときだけで、どちらも無ければ空の一覧を返して読まない。読むかどうかは記録だけで決め、一覧の値で決まる `stopping` を条件に使わない。停止を確かめた席を返した行（`row_stopped` を持つ行）と、止めなかった席を返した行（`park_stop` が偽）は読む理由にならないので、承認の回答を待つ行だけが残る計画では一覧を読まない。読めなければ None |
| `judge_lanes` | `lane/liveness.py` | 変更 | `(lanes, probes, table: Optional[Mapping[str, Mapping[str, Any]]]) -> dict[str, Liveness]` | **#4050 では今のとおりラッパーの一覧を自分で読み、`holding` の行をスプリントの待ちを読む対象から外すだけにする。`wrapper_table` を受け取る形は #4091。** ラッパーの一覧を自分で読まず、`wrapper_table` の値を受け取る。`parked`・`holding` の行はスプリントの待ちを読む対象から外す（スプリントを読む行の数は今以下になる）。同じ観測で `_parks` も同じ `table` を受け取るので、`holding` の行だけが残る計画でもラッパーの生死を `_parks` が読め、一覧の読み取りは観測あたり高々 1 回のまま |
| `stopping`（範囲外（#4091）。決定 15） | `lane/liveness.py` | 新設 | `(row: LaneRow, name: str, table: Optional[Mapping[str, Mapping[str, Any]]]) -> bool` | 席を返した行の停止が済んでいないか。`row.stop_pending` が真で、一覧が None（読めない）か一覧にそのレーンのラッパー `name` が動いて載るとき真。`stop_pending` が偽の行は一覧を見ずに偽（`wrapper_table` はその行のために一覧を読まない）。停止を確かめた印は `park_lane` が止めた後に書く `row_stopped` で、ラッパーが一覧から消えたことを観測しても印の代わりにしない（印が無いままだと毎回の観測で一覧を読む） |
| `gate_wait`（範囲外（#4091）。決定 15） | `lane/liveness.py` | 新設 | `(row: LaneRow, sprint_wait: Mapping[str, Any], requests: Sequence[Request], defs: StageDefs) -> Optional[str]` | 席を返す前の行が今の承認ゲートの回答を待ち、その質問をオーガナイザーが計画のリクエストとして受けて回答できるかを決め、できればそのリクエストのキーを返す。条件は (1) スプリントの待ちの結果が `status: gate` で、`metrics` の工程とステップの定義が `gate`（設計承認・リリース承認）を持ち、`metrics.gate.id`（今のゲートの質問の id）がある (2) 回答が載っていない（`answered_seq` が今の着席より古い） (3) 計画の開いた（閉じていない）リクエストのうち、ゲートの種類に応じた次のものが 1 つある。設計承認ゲートは、種類 `lane_question`・このレーン・中身の `metadata` が `source: ndf-gate:1`・`gate` が今の質問の id・`sprint` が行のスプリントの ID のもの。リリース承認ゲートは、種類 `lane-release`・`id` が `asking.request_id` の値（`lane-release-<今の質問の id>`）・`sprint_state` が行のスプリントの ID のもの（`lanes release` が回答と同じトランザクションで閉じるので、開いていれば未回答）。工程名だけ・レーンだけで照らさないので、変更要求の後に流し直した質問（id が変わる）や、前の行・ゲートでない質問のリクエストでは止めない。どれかが欠ければ None（止めない）。`judge_lanes` が今読むスプリントの待ちの結果を使い、新しい読み取りを足さない |

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
| `_parks`（範囲外（#4091）。決定 15） | `plan/run.py` | 新設 | `(view, rows, table: Optional[Mapping[str, Mapping[str, Any]]], verdicts) -> list[tuple[str, str, str, ParkAct]]` | 席を返す先の（レーン, 行, スプリント, 手の中身）。理由は `rules.park_reason`（`gate_key` は `gate_wait` の値、`alive` は観測で `liveness.wrapper_table` が 1 回だけ読み、`judge_lanes` と共有した一覧。一覧が None なら None）だけで決め、ここで条件を書き足さない。開発ループの行（`kind: loop`）は対象にしない（今のとおり動く）。(1) `held`: 今の着席の間に待ちを記録した行 (2) `gate`: 今の承認ゲートのリクエストが開いた行（実装を始めた行も含む） (3) `vacant`: 待ちの行（実装の待ちか回答の後の next 待ち）でラッパーが動いていない行。起動し直した conductor のラッパーは、`next` が新しい待ちを記録するまで (1) に当たらないので止めない。ラッパーの一覧を読めなければ (3) を決めない。`ParkAct` には観測した着席の番号・`gate` なら質問の id（`metrics.gate.id`）を載せる。理由が `vacant` で、行が席を取り直した後（`seated_seq > 0`）に何も進めていなければ（`seat_progressed` が偽）、`charge` に `seat_fail`（その着席を開いた続きの手の失敗のキー）を載せる。加えて (4) 停止の続き: `stop_pending` が真の席を返した行に、最後の `row_parked` を写した手を出す（`park_lane` の手順 1 の再開の経路を通る）。手を出すのは一覧を読めた観測だけで、ラッパーが一覧に動いて載れば止め直し、一覧を読めてそのレーンのラッパーが載っていなければ止めずに `row_stopped` だけを書く。一覧が None（読めない）の観測では (4) の手を出さず、`row_stopped` も書かない（載っていないとみなさない。`stopping` が真のまま、続きの手の候補にも入らず、次の観測で読み直す）。どちらも `row_stopped` を書いた後は `stop_pending` が偽になり、その行のために一覧を読まない |
| `park_lane`（範囲外（#4091）。決定 15） | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, lane: str, row: str, sid: str, act: ParkAct) -> dict` | 下の「席を返す手順」で、先に再検証と `row_parked` を 1 つのトランザクションで書き、その後にラッパーを止める。止めた後（止めるものが無いと一覧で確かめたときを含む。`stopped` が偽の手は書かない）に `lane.mark_stopped` で `row_stopped` を書く。一覧を読めないときは書かない（手順 3）。再検証で外れれば何もせず `{"ok": false, "stale": true}`（失敗に数えない）。止める直前に質問の id が変わっていれば `row_unparked`（`gate_changed`）で戻して `stale` を返す。止められなければ `row_unparked`（`stop_failed`）で席を返したことを取り消し（`row_seated` は書かない）、`halt_failed` と同じ形で失敗を返す |
| `snapshot_of` | `plan/run.py` | 変更 | 変えない | **#4050 では、写しに `released`（`holding` で、`held_phase` のどれかのフェーズの `phase_wait`（宣言は `held_paths`）が空で、`LaneRow.relaunchable` が真（応答を終えたまま、か取りかかりの後に起動した conductor がまだ仕事を始めていない）で、`awaiting_next` でない行の（レーン, 行, スプリント, `held_seq`, `idle_seq`））と、`answered` の各項目に `idle_seq` を足したものと、`idle`・`relist`・`free` を載せる。`released` と `answered` の起動の判定は、`claim_relaunch` と同じ `relaunchable` だけで読む（`row_idle` を持たない旧形式のスプリントと開発ループの行は `idle_seq` が 0 で真。今のとおり）。`parks`・`parked`・`stopping` と、起動のキーの番号を `max(answered_seq, parked_seq)` にする規則は #4091 で、`answered` の起動のキーの番号は今のとおり `answered_seq`（回答の番号と同じ値だが、項目は 2 つの番号を別々に運ぶ）。** 写しに `parks`（`_parks`）・`parked`（席を返した行のスプリントの ID）・`released`（`parked` で停止が済み（`liveness.stopping` が偽。`stop_pending` が偽か、真でもラッパーが一覧に無い）、`holding` で `implementation_wait` が空で、`awaiting_next` でない行の（レーン, 行, スプリント, `parked_seq`, `held_seq`））と `idle`（`idle.current` の項目の（課題, 項目の世代）の並びと `listed_seq`）と `relist`（`idle.relist_pending` の値と最後の `lane_row_ended` の番号・時刻から求めた猶予の越え）と `free`（空きの数）を載せる。`released` は `row_entered`（`implement`） が書かれるまで残る（`row_resumed` を見ない）。`answered` の項目は（レーン, 行, スプリント, 起動のキーの番号, 回答の番号）で、2 つの番号を別々に運ぶ。`stopping` が真の行は `answered` に載せない。起動のキーの番号は、席を返したかによらず `max(answered_seq, parked_seq)`（回答の後に最後に席を返した番号。一度も席を返していない行では今のとおり `answered_seq`）。席を取り直して `parked` が偽になっても番号が変わらないので、取り直した直後の読み直しで同じ回答の手が新しいキーで出ない（`RunState.launched` の照合が効く）。席を返し直すと `parked_seq` が進んで新しいキーになる。回答の番号は `answered_seq`（最後の `row_answered` の番号）である |
| `launch_lane` | `plan/run.py` | 変更 | 変えない | **#4050 では、続きの手（回答の後の next・待ちの起動し直し）が動いているラッパーを `launch.replace` で置き換える直前に、`lane.claim_relaunch(conn, sid, 手の idle_seq, 手のキー)` を呼ぶことだけを足す（続きの手は今のとおり `seated=True`）。`stale` なら置き換えずに `{"ok": false, "stale": true}`（失敗に数えない）を返す。`claimed` なら起動の入力の `next` に `--launch <手のキー>` を渡し、`untracked`（`row_idle` を持たない行）なら今のとおり渡さない。取りかかりの後に `launch.replace` か起動が失敗したときは、取りかかりを取り消さずに今のとおり失敗を返す。行は `relaunching` のまま `relaunchable` が真で残るので、次の観測が同じキーの手を出し、打ち直しの `claim_relaunch` が取り直して起動する（`_fail` が同じキーで数え、`policy.attempts` で打ち切る）。観測の後に conductor が仕事を再び始めていれば `row_active` が先にあって外れ、取りかかりの後に元の conductor が仕事を始めようとすると `mark_active` が断るので、置き換えと作業の開始はどちらか一方だけが通る。下の記述は #4091 の材料。** 続きの手（`continues`）のうち、スプリントが写しの `parked` にあるもの（回答の後の next・待ちの起動し直し・中止）は `seated=True` にせず、行を始める手と同じく `machine.admitted` を通す。通ったら、同じ `admit.lock` の中で、ラッパーを起動する前に `lane.seat`（`launch` は手のキー、`fail` は `Launch.fail_key`）で `row_seated` を書いてコミットし（席を先に確定する）、その後に起動の入力を渡してラッパーを起動する。起動に失敗したら `lane.park` で席を返す。手は理由 `launch_failed`・`seat` は書いたばかりの着席の番号・`stopped` は偽・`request` と `gate` は前の `row_parked` の値で、`charge` を付けない（ここで数えるので、次の観測の `vacant` で数え直さない）。理由 `launch_failed` の再検証は理由ごとの条件を持たず、共通の外れ条件（`running` でない・既に `parked`・着席の番号が違う）だけを見る（起動の前に `row_seated` を書いたので、`held` や `gate` の条件で確かめ直すと必ず外れる）。今の起動の失敗と同じく打ち直しの回数を数える。回数は `Launch.fail_key` で数えるので、席を返すたびに起動のキーが変わっても同じ待ちの失敗として積もり、`policy.attempts` で打ち切って `launch_failed` を 1 回知らせる（決定 13）。席を返していない行の続きの手は今のとおり `seated=True` |
| `_launches` | `plan/run_decide.py` | 変更 | 変えない | **#4050 では、`released` から待ちの起動し直しの手（`Launch(resume=sid, seq=held_seq)`。キーは `next:<スプリント>:<held_seq>` で、席を返さないので変わらず、打ち直しの回数も今のとおりキーで数える）を作り、回答の後の next の手と同じ位置に並べ、同じ行で両方当たれば回答の後の next だけを打つ（I10）。`parks` の手・`fail_key`・席を取り直した行の扱いは #4091。** `parks` から席を返す手（`Launch(park=…)`）を作り、終わったレーンを止める手と同じ位置に並べる。`released` から起動し直しの手（`Launch(resume=sid, seq=parked_seq, hold=held_seq)`）を作り、回答の後の next の手（`Launch(resume=sid, seq=<起動のキーの番号>, answered=<回答の番号>)`）と同じ位置に並べる。失敗のキー（`Launch.fail_key`）が `RunState.failed` にある手は出さない（`_candidates` の `_skip` の照合のうち `failed` だけを `fail_key` で照らし、`launched`・`tried` は今のとおり `key` で照らす）。同じスプリントの回答の後の next があれば起動し直しの手を作らない（I10。回答の後の `next` も実装の境では `Env.hold` を通る）。中止の手も含め、続きの手は 1 行に 1 つ。席を取り直した行（`parked` が偽で `seated_seq > 0`）では、キーが今の着席を開いた手のキー（`LaneRow.seat_launch`）と同じ続きの手を作らない。席を取り直した後の回答の後の next は、起動のキーの番号（`max(answered_seq, parked_seq)`）が変わらないので、作ると `awaiting_next` が残る間ずっと「起動したまま待つ手」になり、既存の `_expire_launched` が期限（`Policy.up`）で起動の失敗と数え、同じキーで打ち直して動いている conductor を置き換える。作らないので、その着席のマーカーは次の `_expire_launched` で待ちの候補から外れて失敗に数えずに消える。その着席が何も進めずに終わったかは、ラッパーの消失を観測した `vacant` の手の `charge` で 1 回だけ数える（`seat_launch` と同じ着席の `seat_fail`）。conductor が動いていて `next` が遅いだけなら、ラッパーが一覧にある間は `vacant` にならず、何も数えない |
| `_assignable` | `plan/run_decide.py` | 新設 | `(snap: Snapshot, acts: list[Launch], failed: Sequence[tuple[int, int]]) -> Optional[Assign]` | 手が 1 つも無く、計画が `running`、`free >= 1` なら、一覧のうち `still_skipped(写しの項目, failed)` に無い最初の課題の割り当ての手を返す。手は、写しの `listed_seq` と別に、選んだ項目の世代（`item_seq`。写しの項目の（課題, 項目の世代）の値）を運ぶ。手の `skipped` に、`failed` のうち飛ばした課題の（課題, 失敗を記録した項目の世代）を一覧の順で載せる |
| `Launch.fail_key`（範囲外（#4091）。決定 15） | `plan/run_decide.py` | 新設 | `@property -> str` | 打ち直しの回数と打ち切りを数えるキー。席を返した行の続きの手では、席の世代（`parked_seq`）に依らず、手が応える記録の番号で決める。回答の後の next は、席を返したかによらず `next:<スプリント>:a<回答の番号>`、待ちの起動し直しは `resume:<スプリント>:h<held_seq>`、席を返す手は `key`（`park:<レーン>/<行>:<着席の番号>`。止められずに取り消しても着席は変わらないので同じ値）。ほかの手は `key` と同じ（今のまま）。同じ着席の 2 回の起動を防ぐ `RunState.launched` は今のとおり `key` で持つ |
| `_count`（範囲外（#4091）。決定 15） | `plan/run_decide.py` | 新設 | `(state: RunState, lane: str, key: str, detail: str, policy: Policy, *, final: bool, kind: str) -> RunState` | 今の `_fail` の本体を、数えるキーと知らせの種類を引数で受ける形にしたもの。回数（`attempts`）・`failed`・`final`・知らせの閉じた後の戻し（`_reopen_closed` の照合）を `key` で数える |
| `_fail` | `plan/run_decide.py` | 変更 | 変えない | **#4050 では変えない。下の記述は #4091 の材料。** `_count(state, act.lane, act.fail_key, …)` を呼ぶ。席を返す手（`act.park`）は止める手（`act.halt`）と同じく `final`（尽きたら打ち直さない。閉じても数え直さない）で、種類は `halt_failed`。ほかの手は今のとおり `launch_failed` |
| `_charge`（範囲外（#4091）。決定 15） | `plan/run_decide.py` | 新設 | `(state: RunState, lane: str, fail_key: str, detail: str, policy: Policy) -> RunState` | 席を返す手の `charge` を数える。`_count(…, final=False, kind=LAUNCH_FAILED)`。打ち切ったら `launch_failed` で 1 回知らせ、知らせのリクエストが閉じたら数え直す（続きの手の失敗と同じ扱い） |
| `_after_observe` | `plan/run_decide.py` | 変更 | 変えない | 全行が終わったとき（`snap.complete`）は、次の順で手を決める。(0) 今の `_before_end`（終わったレーンを止める手・尽きた止める手の知らせ `halt_failed`・止め直しの待ちの `Sleep`）を、割り当て・作り直しの待ち・完了のどれよりも先に見て、手を返せばそれを打つ。上限 1 で最後の行を終えたラッパーがまだ動いているとき、止める手より先に (1) へ進むと、ラッパーが枠を占めて `free` が 0 のまま `Sleep` を繰り返し、停止も割り当ても起きない。止めた後の読み直しで枠が空き、(1) が割り当てる (0') 写しの `relist` が真のまま、最後の `lane_row_ended` の時刻から `RELIST_GRACE_SECONDS` を越えていれば、知らせ `relist_pending` を 1 回だけ打つ（`RunState.reported` と、開いた知らせの写し `reported` で 2 回打たない）。この期限の判定は一覧の残件数と割り当ての可否によらず、(1) の前に置く（最後の行の後に `refresh` が失敗して古い一覧に課題が残り、空きが無い写しでも、猶予を越えれば知らせが開く）。知らせのリクエストは `launch_failed` と同じ形で、中身の `kind` は `relist_pending`、知らせのキーの項目 `relist` に `relist:<最後の lane_row_ended の番号>` を載せる（`NOTICE_FIELDS` に `relist_pending: "relist"` を足す） (1) 一覧に、`still_skipped` が飛ばさない課題が残っていれば `Complete` を打たない。`_assignable` が手を返せば割り当てを、返さなければ（`free` が 0）`Sleep(policy.poll)` を打つ (2) 一覧が空でも、写しの `relist`（`idle.relist_pending` の値）が真なら、作り直しを待って `Sleep(policy.poll)` を打ち、`Complete` を打たない（`Complete(false)` から待ち時間なしで `Observe` に戻る繰り返しを作らない） (3) どれでもなければ `Complete(skipped=RunState.assign_failed)` へ進む。写しの判断は打つかの目安にとどめ、閉じてよいかは `complete_plan` が排他の中で決める。`Complete` が偽を返したら、次の観測までの間に `Sleep(policy.poll)` を挟む（排他の中で外れた理由は次の写しの (1)・(2) に現れる） |
| `complete_plan` | `plan/__init__.py` | 変更 | `(conn, pid: str, skipped: Sequence[tuple[int, int]] = ()) -> bool` | `skipped` は割り当てに失敗した（課題, 失敗を記録した項目の世代）の並びで、`RunState.assign_failed` をそのまま渡す（課題の番号だけを渡さない）。`lock_plan` を取って最新の計画とレーンを読み直し、今の条件（全行の終わり・回答していないリクエストが無い）に加えて次の 2 つを満たすときだけ `plan_completed` を書く。(1) `idle.current` の項目のうち、`still_skipped(項目, skipped)` が飛ばさないものが無い。除外は排他の中の最新の一覧の世代で決めるので、観測の後に `idle set --pin`・`clear` や作り直しで項目の世代が進んだ課題は除外が切れて (1) で外れる (2) `idle.relist_pending(view)` が偽。満たさなければ書かずに偽を返し、耐久ワークフローは `Sleep` を挟んで読み直し、割り当てか作り直しの待ちへ戻る。`idle_listed` を持たない計画は (2) が常に偽で、今のとおり閉じる |
| `still_skipped` | `plan/run_decide.py` | 新設 | `(items: Sequence[tuple[int, int]], failed: Sequence[tuple[int, int]]) -> set[int]` | 一覧の項目の（課題, 項目の世代）と、失敗の（課題, 失敗を記録した項目の世代）から、まだ飛ばす課題を返す。項目の世代は、最後の `idle_listed` の番号と、その課題の最後の `idle_adjusted` の番号の大きいほう（`idle.current` が項目ごとに返す）。失敗の側も同じ項目の世代（割り当ての手の `Assign.item_seq`）で記録し、一覧の世代（`listed_seq`）で記録しない。失敗の世代が項目の世代以上の課題だけを飛ばす。一覧の世代で記録すると、一覧の作成の後に `idle set --pin X` を書いた X（項目の世代が一覧の世代より大きい）の失敗が記録した直後から飛ばされず、次の観測でも同じ X を選び続けて後続の割り当てと完了に進めない。DB を読まない純粋な関数で、`_assignable`（写しの項目）・`assign_idle` の手順 2・`complete_plan` の (1)（排他の中の項目）が同じこの関数を読み、除外の規則を 3 か所で食い違わせない |
| `relist_pending` | `plan/idle.py` | 新設 | `(view: PlanView) -> bool` | 一覧を使う計画で、最後の行の終わりの後の作り直しが済んでいないか。計画が `idle_listed` を持ち、最後の `lane_row_ended` の番号が最後の `idle_listed` の中身の `ended_seq` より大きく、かつ知らせ `relist:<その lane_row_ended の番号>` が閉じていないとき真。知らせを閉じたかは、リクエストのキー（`table.open_request` が別に採番する）で引かない。計画のリクエストのうち中身の `kind` が `relist_pending` で、知らせのキーの項目 `relist` が `relist:<その番号>` のものを探し、1 つでも閉じていれば（`closed` を持てば）閉じたとみなす（今の `reported` が `NOTICE_FIELDS` の項目で知らせのキーを読むのと同じ照合）。`view.requests` を `relist:<番号>` で引くと、オーガナイザーが作り直さずに知らせを `request close` で閉じても待ちが解けない。`idle_listed` を書いた番号でなく、その作り直しが取得を始めたときに読んだ `ended_seq` で比べるので、行の終わりの前に取得を始めた作り直しが行の終わりの後に書いても済んだことにならない。DB だけを読み、`snapshot_of`（写しの `relist`）と `complete_plan` の (2) が同じこの関数を読む |
| `refresh` の書き込み | `plan/idle.py` | — | — | 取得（`Git.fetch_branch`）と GitHub の読み取りを始める前に、計画の最後の `lane_row_ended` の番号と、計画の最後の事象の番号と、今の時刻を DB と時計から読み（排他の外。無ければ 0）、`idle_listed` の中身の `ended_seq`・`fetch_seq`・`fetched_at` に載せる。（`fetch_seq`, `fetched_at`）を取得の世代と呼ぶ。`idle_listed` は、項目が前回と同じでも必ず書く（書かないと、最後の行が終わった後に `relist_pending` が偽にならず計画が閉じない）。書くのは `lock_plan` の中で、計画が `running` か `paused` のときだけで、閉じた計画には書かず `stopped`・`precondition` を返す。同じ排他の中で最後の `idle_listed` の取得の世代を読み、自分の世代がそれより古ければ（辞書順で小さければ）書かずに `status: ok`・`metrics.superseded: true` を返す（打ち直さなくてよい。新しい取得が既に書いた）。最後の行の終わりの前に始めた取得 A が遅れ、行の終わりの後に始めた取得 B が先に書いたとき、A が B の一覧と `ended_seq` を置き換えると、B が見つけた課題が割り当てられず、済んでいた作り直しも再び待ちになる。比べるのは書いた順でなく取得を始めた順で、`fetch_seq` が同じ（間に計画の事象が無い）ときだけ `fetched_at` で決める。`complete_plan` と同じ排他なので、作り直しが先なら完了が (1) か (2) で外れ、完了が先なら作り直しが断られる。完了が先になるのは、最後の行の終わりの後に取得を始めた作り直しが済んで一覧が空だったか、オーガナイザーが知らせ `relist_pending` を閉じて作り直しを待たないと決めたときだけである |
| `assign_idle` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, issue: int, listed_seq: int, item_seq: int, skipped: list[tuple[int, int]]) -> dict` | 下の手順で割り当てで足すレーンを書く。`listed_seq` は手順 2 の写しの新しさの確かめに、`item_seq` は失敗の記録に使う。結果は `{"ok": true, "lane": …}`・`{"ok": false, "stale": true}`・`{"ok": false, "detail": …}` のどれか |
| `decide` | `plan/run_decide.py` | 変更 | 変えない | **#4050 では、割り当ての手の扱いだけを足す。`charge` を数える部分は #4091。** `_candidates` が空のとき `_assignable` を見る。`stale` は失敗に数えず `Observe` へ戻る。断られたら、その課題を `RunState.assign_failed` に（課題, 手の `item_seq`）で入れ、同じ課題で 1 回だけ `launch_failed` の形の知らせを作る。席を返す手が通り、手が `charge` を持てば、`_charge` でその失敗のキーを 1 回数える（起動し直した conductor が待ちも実装の開始も回答の消費も書かずに終わった。決定 13） |

席を返す手順（`park_lane`）（範囲外（#4091）。決定 15）:

1. 手の理由が `vacant` なら、先に起動の排他（`admit.lock`）を取り、そのレーンのラッパーの有無を `launch.wrappers()` で 1 回読む（`alive`）。排他は手順 2 のコミットまで持つ。続けてトランザクションを始め、`lock_plan` を取り、行を読み直す。行が既に `parked` で、`park_seat` と `park_reason` が手の `seat`・`reason` と同じなら、同じ着席の停止の再開として扱い、何も書かずにコミットして手順 3 から続ける（`park_stop` が偽なら止めるものが無いので `{"ok": true}`）。耐久ステップが `row_parked` のコミットの後・停止の前に中断され、同じ引数で打ち直されたときと、`_parks` が出した停止の続きの手のどちらもこの経路を通る。それ以外は、次の表のどれかに当たれば書かずに `stale` を返す

| 当たる手 | `stale` にする条件 |
| --- | --- |
| どの理由でも | 行が `running` でない・既に `parked`・今の着席の番号（`seated_at_seq`）が手の `seat` と違う |
| `held`・`gate` | `rules.park_reason(row, gate_key, None)` が手の理由と違う値を返す。`gate_key` は `lane.regate` が作る |
| `vacant` | `rules.park_reason(row, gate_key, alive)` が `vacant` でない。`alive` は、トランザクションの前に取った起動の排他（`admit.lock`）の中で、そのレーンのラッパーの有無を 1 回だけ読み直した値（読めなければ None で外れる） |
| `launch_failed` | 理由ごとの条件を持たない（上の行だけ） |

   `park_reason` に当てると、`held` は `holding` で `held_seq > seated_at_seq` のときだけ通る（観測の後に `next` が実装を始めた行は `holding` が偽になって外れる）。`gate` は `regate` の 4 条件をすべて満たすときだけ通り、`implementing` では外さない（リリース承認を待つ行は実装を始めた後にあり、外すと毎回 `stale` になってリリース待ちの行が枠を返せない）。`vacant` の手がコミットまで進むのは、次の 3 つがそろうときだけである。(i) 着席の世代: 今の着席の番号が手の `seat` と同じ（共通の行。観測の後に `launch_lane` が席を取り直していない） (ii) 今の待ち: `holding` か `awaiting_next` が真で、今の着席の間に待ちを記録しておらず（`held_seq <= seated_at_seq`。記録していれば `held` が先に当たり、次の観測で `held` の手になる）、`regate` が None（承認の待ちなら `gate` が先に当たる） (iii) ラッパーが無い: 起動の排他の中で読み直した値（`alive=False`）。起動途上の着席（`row_seated` のコミットの後・ラッパーの起動の戻りの前）は、`launch_lane` が排他を持つので読み直しが起動の戻りまで待ち、載ったラッパーを見て外れる。起動し直した conductor が `next` の前に落ちた行は (i)〜(iii) をすべて満たすので、`stale` にならずに席を返し、次の観測で新しいキーの続きの手になる。排他の順は、`launch_lane` と同じく起動の排他 → 計画の排他（`lock_plan`）で、`vacant` の手だけが起動の排他を取る（`held`・`gate`・`launch_failed` の手は取らない）
2. 同じトランザクションで `row_parked`（`seat` は手の着席の番号・`stopped` は 3 で止める予定か・`gate` は手の質問の id）を書いてコミットする。以後、行は `parked` で、`begin_implementation` は手順 3 の排他の中で `parked` の行に `row_entered`（`implement`） を書かない（今の待ちを返す。下の「`next` との競合」）
3. コミットの後、`stopped` が真の手だけ、そのレーンのラッパーの有無を `launch.wrappers()` で 1 回読む（`stopped` が偽の手は読まない）。一覧が None（読めない）なら、止めず `row_stopped` も書かずに `stale`（失敗に数えない）を返す。`stop_pending` が真のまま残るので `stopping` も真のままで、`released`・`answered`・中止の続きの手の候補に入らず、一覧を読めた次の観測の (4) の手がこの手順 3 をやり直す（読めないのを「載っていない」とみなして `row_stopped` を書くと、`stop_pending` が偽になり、動いたままの元のラッパーを `launch_lane` が同じレーン名で置き換えて conductor を落とす。印は永続なので戻せない）。一覧を読めてラッパーが動いていれば、止める直前に理由が `gate` なら `regate` を読み直す（行は `parked` になったが、着席と回答とリクエストの条件は同じに読める）。None なら（質問の id が手の `gate` と違う・リクエストが閉じた・回答が載った）、止めずに `lane.unpark`（`gate_changed`）で戻して `stale` を返す（変更要求の後に流し直した質問がまだ計画へ届いていないうちに、古いリクエストを根拠にラッパーを止めない）。同じなら `launch.stop` で止め、成功したら `lane.mark_stopped` で `row_stopped` を書いて `{"ok": true}` を返す。一覧を読めて、そのレーンのラッパーが載っていない（止める必要が無いと確かめた）ときだけ（停止の再開で、前の打ち直しが止めた後に中断した・ラッパーが自分で終わった）、止めずに `row_stopped` だけを書く。`row_stopped` を書く前に中断されても、`stop_pending` が真のまま残るので、次の観測の (4) の手がこの手順 3 をやり直す
4. 止められなければ、`lane.unpark`（`stop_failed`）で席を返したことを取り消し、`halt_failed` の形で失敗を返す。`row_seated` は書かない（着席を新しくすると `held_seq > seated_at_seq` が崩れ、動いたままのラッパーが `vacant` にも当たらず、待つレーンが枠を占め続ける）。着席の番号が変わらないので、次の読み直しで `park_reason` が同じ理由を返し、同じキーの席を返す手になる。`_fail` が手のキーで回数を数え、`policy.attempts` 回続けて止められなければ打ち直さずに `halt_failed` を 1 回知らせる（決定 13）

`next` との競合（範囲外（#4091）。決定 15）: `row_parked` と `row_entered`（`implement`） はどちらも計画の排他の中で行を読み直してから書くので、先に書いたほうだけが通る。`park_lane` が先なら、止める前のラッパーが `next` を打っても実装を始めず（`parked` の行は `begin_implementation` の (a) と同じく今の待ちを返す）、止めた後の起動し直しの手（`released`）が席を取り直してから実装を始める。`next` が先なら、手順 1 の `implementing` の再検証で外れ、実装に進んだラッパーを止めない。承認の待ち（`gate`）の行で `row_parked` のコミットの後・停止の前に回答が載ったときは、どこで載ったかで 2 通りに分け、どちらも 1 つの conductor だけが回答の後を続ける。(A) 手順 3 の `regate` の読み直しより前に載った: `regate` の (1)（回答が今の着席の後に載っていない）が外れて None になるので、止めずに `lane.unpark`（`gate_changed`）で席を戻し、元のラッパーを残して `stale` を返す。行は席を返していない行に戻る（`seated_at_seq` は変わらない）ので、次の観測の回答の後の next は今のとおり席を返していない行の手（`seated=True`。上限の判定を通さない）で、`launch.replace` が元のラッパーを置き換えて起動の入力の `next` → `row resumed --answered` を打つ。席を返していない行で回答が載ったときの今の経路と同じで、回答を消費して続けるのは置き換えた後の conductor である。置き換える前に元の conductor が先に消費していても、`record_resumed` は誰が消費したかを問わない（下の「起動し直しの入力」）。(B) `regate` の読み直しの後・`launch.stop` の前に載った: 止めて席を返したままにし、次の観測の回答の後の next は席を返した行の手（`seated=False`。上限の判定を通し、`row_seated` の後に起動する）になる。回答を消費して続けるのは起動し直した conductor である。どちらでも、席を返したまま上限を通さずに起動することはなく、元のラッパーを残したまま席を返した行の手で 2 本目を起動することもない（席を返した行の続きの手は `stopping` の間は候補に入らない）

`assign_idle` の手順（1 つのトランザクション）:

1. `lock_plan` を取り、最新の計画とレーンを読む
2. 計画が `running` でない、`idle.current` から `still_skipped(項目, 手の skipped)` が飛ばす課題（手の `skipped` は割り当てに失敗した（課題, 失敗を記録した項目の世代）。耐久ステップの引数で運ぶので、再開しても同じ値を読む）を除いた先頭が `issue` でない、最後の `idle_listed`・`idle_adjusted` の番号が写しより新しい、のどれかなら書かずに `stale` を返す（観測の後の `idle set --pin`・`plan pause` を拾う）
3. 1 で読んだ計画から、名前（`i<番号>`、重なれば `i<番号>-<n>`）と `after`（同じリポジトリの終わっていないレーンのすべて）を組み、`add_lane(..., actor="workflow", idle=issue)` で書く。`add_lane` は同じトランザクションの中で呼び、排他を取り直さない
4. 計画の決まりで断られたら `{"ok": false, "detail": …}`

- 席を返した行の続きの手（起動し直し・回答の後の next・中止）は、行を始める手と同じくマシン全体の上限の判定（`machine.admitted`）を通す。`admitted` が数える他のレーンのうち、席を返した行のレーンは `lanes_of` が数えないので、上限 1 で席を返した 2 レーンに回答が載ると、先に通った 1 本が起動して `row_seated` を書き、もう 1 本はその動いているラッパーを数えて上限で待つ（互いを数えて両方が止まることはない）
- 入力は行の種類で分ける。開発ループの行（`kind: loop`）は今の `resume_commands` のまま `ndf lanes row resumed <スプリント>` → `ndf loop get <ループ>` で、`progress next` を打たない（ループのスプリントへ `next` を打つと行が終わる。`_going_on`）。開発ループの行には席を返す手を作らない（`_parks` が `kind: loop` の行を外す。開発ループの行は今のとおり動く）ので、`--answered` も要らない。スプリントの行（`kind: sprint`）は `ndf progress next` → `ndf lanes row resumed <スプリント> --answered <Launch.answered>` の順にする（今の `launch.py` は逆で、番号も渡さない）。`--answered` に渡すのは回答の番号（最後の `row_answered` の番号）で、起動のキーの番号（`Launch.seq`。回答の後の next では `max(answered_seq, parked_seq)`）ではない。`seq` を渡すと (1) の照合が外れ、回答を消費しても `row_resumed` が書かれず `awaiting_next` が残る。`row resumed` は無条件に書かない。`record_resumed` は計画の排他の中で行を読み直し、(1) 渡した番号が行の最後の `row_answered` の番号と同じで (2) その回答が消費された（`row_answered` が `gate` を持てば、スプリントがその質問の id の承認ゲートでもう待っておらず（`asking.waiting` が None か別の id）、`run.decide` が回答の消費（CONSUME）を返さない。`gate` を持たない回答は (1) だけ）ときだけ `row_resumed` を書く。(2) の確かめはスプリントを読むので、`plan/cli.py` の `row resumed` が `gate.asking.waiting`・`run.decide` で求める関数を作って `record_resumed(conn, sprint_id, actor, answered, consumed)` に渡す（`lane` は `run`・`gate` を import しない）。消費を確かめられなければ書かずに `stopped`・`precondition` を返し、`next` に `ndf progress next <スプリント>` → 同じ `row resumed` を載せる（`/goal` の下の conductor が打ち直す）。誰が消費したかは問わない（止める前の conductor が先に消費した行でも、起動し直した `next` の後に (2) が真になる）。`next` が起動済みの作業との排他などで回答を消費せずに戻っても `awaiting_next` は消えず、conductor が応答を終えてラッパーが無くなれば `_parks` の (3) で席を返し、新しいキーで起動し直す。待ちの起動し直し（回答の無い `released`）の入力は `--answered` を渡さず、`row resumed` は今のとおり起動の試行の印として書く。`next` の前に conductor が落ちても `row_resumed` が無いので、回答の後の next 待ち（`awaiting_next`）のまま残り、ラッパーも動いていないので次の読み直しで席を返す手（止めずに `row_parked`）を受け、その後の読み直しで新しいキーの続きの手になる。`next` を 2 回打っても `store.run_lock` の中で同じ手を返す。`row resumed` は起動の試行の印で、待ちを解かない
- 続きの手のキーは今のとおり `next:<スプリント>:<seq>` で、`seq`（起動のキーの番号）は手の種類ごとに次の 1 つの式で決め、写し（`snapshot_of`）・手（`_launches`）・`row_seated` の `launch`（`seat`）・`seat_launch` の照合のどこでも同じ式を使う。回答の後の next は、席を返したかによらず `max(answered_seq, parked_seq)`（一度も席を返していない行では `answered_seq`）。回答の無い待ちの起動し直し（`released`）は `parked_seq`。回答の後の next で席を返した行に一律 `parked_seq` を使わない（回答が席の返却より後に載った行で、着席の前は `parked_seq`、着席の後は `answered_seq` と式が変わると、同じ回答のキーが変わって `seat_launch` の照合をすり抜け、起動済みの conductor を置き換える）。席を返すたびに番号が進むので、同じ着席の待ちを 2 回起動せず（`RunState.launched`）、起動し直した conductor が落ちて席を返し直した後は新しいキーで起動できる（期限切れを待たない）。打ち直しの回数はキーでなく `Launch.fail_key` で数える（キーは席を返すたびに変わるので、キーで数えると同じ待ちの起動の失敗が上限に届かない）
- 席を返す手（`park_lane`）は、席を返していない行と、停止の済んでいない席を返した行（`liveness.stopping` が真）にだけ作る。後者は停止の続きの手で、中身は最後の `row_parked` の `reason`・`seat`・`request`・`gate` を写し、キーも失敗のキーも元の手と同じ（`park:<レーン>/<行>:<着席の番号>`）なので、止められない回数は元の手と合わせて数える。停止を確かめた行（`row_stopped` を持つ行）には打ち直さない。`stop_pending` が真で、一覧を読めてラッパーが載っていない行には、止めずに `row_stopped` だけを書く手を 1 回出す。一覧を読めない観測では手を出さない
- 停止の済んでいない席を返した行（`stopping` が真）は、`released`・`answered`・中止の続きの手の候補に入れない。元のラッパーが動いたまま新しいラッパーを起動しない（同じレーンの名前を置き換えて動いている conductor を失わない）ためで、ラッパーが一覧から消えた次の観測で候補に入る。一覧を読めない観測でも候補に入れない（安全側）
- 割り当ての手の後の次の読み直しで、足したレーンは `wakeable` に入り、行を始める手（上限の判定を通す）になる。割り当てそのものは上限の判定を通さない（席を使わない）
- `RunState.assign_failed` は観測ごとに空にしない（`tried` と違う）。今の写しの `idle` の項目の世代が失敗を記録したときの項目の世代（`Assign.item_seq`）より新しい（一覧を作り直したか、その課題の調整が書かれた）項目は飛ばすのをやめる。判定は `still_skipped` の 1 つで、割り当て・`assign_idle`・`complete_plan` が同じ規則で読む。飛ばす課題は割り当ての手の `skipped` で `assign_idle` へ渡し、耐久ステップの確かめ直しも同じ課題を飛ばす。知らせ済みの課題は `RunState.reported` で 2 回知らせない

席を返した行の回答と再開（承認の種類ごと。どれもラッパーへの打ち込みを使わない）（範囲外（#4091）。決定 15）:

| 承認の種類 | 回答の記録 | レーンへの再開の通知 | 起動し直した後の取り込み |
| --- | --- | --- | --- |
| 設計承認ゲート（`lane_question`） | `ndf answer <スプリント> --gate <質問の id> --result … --by organizer --key …`（スプリントの回答の事象） | `ndf lanes answered <スプリント> --answer <回答> --gate <質問の id>`（`row_answered`。`record_answer` が `gate` を中身に載せる）。続けて `ndf lanes request close` | 起動の入力の `ndf progress next` が回答を消費する（`run.decide` の CONSUME） |
| リリース承認ゲート（`lane-release`） | `ndf lanes release <計画> <リクエスト>... --result … --version … --release …`（計画のリクエストの回答。同じトランザクションでリクエストを閉じる） | リクエストごとに `ndf lanes answered <sprint_state> --answer <released か rejected> --gate <質問の id>` | 起動の入力の `ndf progress next` の入口（`gate/entry.py` の `_took` → `release.take_reply`）が記録を承認ゲートの回答（`by: organizer`）として書き、`version`・`release_pr` を文脈へ写してから流す |

`_launches` の回答の後の next の契機は今のとおり `row_answered`（`answered_seq > resumed_seq`）だけで、`ndf answer` や `lanes release` だけでは起動し直さない。`--gate` は任意で、承認ゲートでない質問の回答には付けない（席を返すのは承認ゲートの行だけなので、席を返した行の回答には必ず付く）。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Assign` | 型（`issue`・`key`・`listed_seq`（写しの一覧の世代）・`item_seq`（選んだ項目の世代）・`skipped`（（課題, 失敗を記録した項目の世代）の並び）） | `plan/run_decide.py` | — |
| `Complete.skipped` | 欄（（課題, 失敗を記録した項目の世代）の並び。`RunState.assign_failed` の写し。耐久の記録に引数として残るので、再開しても同じ値で `complete_plan` を打つ） | `plan/run_decide.py` | 空 |
| `Snapshot.relist` | 欄（`idle.relist_pending` の値・最後の `lane_row_ended` の番号・その時刻から `RELIST_GRACE_SECONDS` を越えたか） | `plan/run_decide.py` | None（一覧を使わない計画） |
| `RELIST_GRACE_SECONDS` | 定数（最後の行の終わりから、作り直しが済まないまま知らせ `relist_pending` を打つまでの秒） | `plan/run_decide.py` | 600（回答の後の next の猶予 `ANSWER_NEXT_GRACE_SECONDS` と同じ値。オーガナイザーの手が遅れていると読む猶予の 1 つ） |
| `RELIST_PENDING` | 定数（知らせの種類） | `plan/run_decide.py` | `"relist_pending"`。中身は `launch_failed` と同じ形で、知らせのキーは項目 `relist`（`NOTICE_FIELDS` の値）に `relist:<番号>` で載せ、`detail` に最後に終わった行と、打つコマンド `ndf lanes idle refresh <計画>` を載せる |
| `ParkAct`（範囲外（#4091）。決定 15） | 型（`reason`（`held`・`gate`・`vacant`・`launch_failed`）・`request`・`gate`（質問の id）・`seat`・`stopped`（停止の予定。実績は `row_stopped`）・`charge`（数える失敗のキー）） | `lane/types.py`（`lane.park` の引数なので `lane` に置き、`plan/run_decide.py` が import する。`lane` から `plan` への import を作らない） | — |
| `Launch.answered`・`Launch.hold` | 欄（回答の番号・待ちの番号。`seq` と別に運ぶ） | `plan/run_decide.py` | 0・0 |
| `Snapshot.parks`・`Snapshot.parked`・`Snapshot.released`・`Snapshot.idle`・`Snapshot.free` | 欄 | `plan/run_decide.py` | 空・空・空・None・None |
| `Snapshot.answered`・`Snapshot.released` の項目 | 形（`answered` は（レーン, 行, スプリント, 起動のキーの番号, 回答の番号）、`released` は（レーン, 行, スプリント, `parked_seq`, `held_seq`）） | `plan/run_decide.py` | — |
| 旧版の写しの `answered` | 回復の規則。耐久ステップの回復は旧版の `observe` が記録した写しをそのまま返し、耐久アプリのバージョンは NDF の版上げで変わらないので、動いていた計画を新しい版で再開すると 4 要素の項目（レーン, 行, スプリント, 回答の番号）が届く。`Snapshot.of` が `answered` の各項目を読み、4 要素なら 4 番目を起動のキーの番号と回答の番号の両方に補って 5 要素にする（旧版は席を返さないので `parked_seq` は 0 で、`max(answered_seq, 0)` と同じ値。旧版が `RunState.launched` に残した `next:<スプリント>:<回答の番号>` とキーが一致し、同じ回答を 2 回起動しない）。5 要素はそのまま読み、それ以外の長さは判断の誤りとして `TypeError`。`_launches` は補った後の 5 要素だけを展開する。ほかの新しい欄（`parks`・`parked`・`released`・`idle`・`relist`・`free`）は今の「無いキーは既定値」の規則で補う | `plan/run_decide.py` | — |
| `Launch.park` | 欄（`ParkAct`）。キーは `park:<レーン>/<行>:<着席の番号>` | `plan/run_decide.py` | None |
| `ROW_PARKED`・`ROW_SEATED`・`ROW_UNPARKED` | 定数（事象の種類） | `lane/types.py` | `"row_parked"`・`"row_seated"`・`"row_unparked"` |
| `RunState.assign_failed` | 欄（（課題, 失敗を記録した項目の世代 `Assign.item_seq`）の並び） | `plan/run_decide.py` | 空 |
| 割り当てで足すレーンの名前 | 規則 | `plan/run.py` | `i<課題の番号>`、重なれば `i<番号>-2`… |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 割り当てとオーガナイザーの `lane add`・`idle set`・`plan pause` | 計画のステートマシン | `assign_idle` の手順 1〜3 を 1 つのトランザクションにし、`lock_plan` の中で計画の状態と一覧の先頭を確かめ直す | 割り当ては `lane_added` 1 件で、課題が計画の行に入るので、再開しても同じ課題を 2 回足さない（手順 2 の確かめで外れる） |
| 割り当てとほかの計画の起動 | マシンの枠 | 割り当ては席を使わない。行の開始は今の `admit.lock` | 次の読み直しで数え直す |

### 単体テストの観点

- 待ちの行と承認の待ちの行の席を返す手が、行を始める手より前に出る。席を返した後の読み直しでは打たない
- 観測の後、`park_lane` の前に同じ行の `next` が `row_entered`（`implement`） を書くと、`park_lane` は `stale` でラッパーを止めず `row_parked` も書かない。逆に `row_parked` の後、停止の前に止める前のラッパーが `next` を打つと、実装を始めず待ちを返し、起動し直しの後に始める（排他の待ち合わせをテストで作る）
- 実装を始めた行がリリース承認（`lane-release`）を待ち、今の質問の id のリクエストが開いていると、`park_lane` は `stale` にならず `row_parked` を書いてラッパーを止める。リリース待ちの行だけで全枠が埋まった計画でも、次の読み直しで一覧の先頭が割り当てられる（`implementing` で外すと毎回 `stale` になって落ちる）。`held` の手だけが、観測の後に実装を始めた行で `stale` になる
- 観測の後・`park_lane` の前に、同じ着席の間に承認の対象が変わって質問の id が更新され、前の質問のリクエストがまだ開いている写しで、`park_lane` は `stale` で書かず止めない。`row_parked` のコミットの後・停止の前に `current_gate()` が別の id を返すと、`row_unparked` で戻して止めない（差し替えた停止の呼び出しが 0 回）
- `row_parked`（`stopped: true`）のコミットの後・停止の前に耐久ステップを中断させると、記録は `row_parked` の `stopped` が真で `row_stopped` が無く、`stop_pending` が真になる（`stopped` を止めた実績として停止の後に真で書くと、ここで偽のまま `stop_pending` が立たず落ちる）。同じ引数で `park_lane` を打ち直すと、`stale` にならずにラッパーを止める（「既に `parked`」で外すと動いたままのラッパーが残って落ちる）。耐久ワークフローを起動し直した新しい実行でも、観測が `stopping` の行に停止の続きの手を出して止める。止まるまでの観測で、その行に回答が載っていても `answered` の続きの手を出さない
- ラッパーを止められないと `row_unparked` が書かれ、`row_seated` は書かれず、`seated_at_seq` が変わらない。動いたままのラッパーの行は次の観測で同じキーの席を返す手になり、止められれば席を返す。`policy.attempts` 回続けて止められなければ `halt_failed` を 1 回知らせ、打ち直さない（`row_seated` で戻すと次の観測で手が出ずに落ちる）
- 起動し直した conductor のラッパーが `next` を打つ前の読み直しで、席を返す手を打たない（`released` にも `parks` にも入らない）。その conductor が `next` の前に落ちると、席を返す手（止めない）を経て新しいキーで起動し直す
- `phase_wait` が空になった待ちの行（実装の待ちでは `implementation_wait` が空）の起動し直しが 1 回だけで、上限なら待ちに置かれる。`after` を満たしても `touches` が未確定の割り当てたレーンは起動し直さず、`lanes set` で `touches` だけを合わせた後の読み直しで起動し直す。回答の後の next も、席を返した行なら上限なら待ちに置かれる
- 上限 1 で席を返した 2 レーンに回答が載ると、1 本が `row_seated` を書いてから起動し、もう 1 本は上限で待ち、1 本目の行が終わった後に起動する
- 席を返した承認の待ちの行の回答の後の next で、`parked_seq` と回答の番号が違う（回答が席を返した後に載った）とき、起動の入力が `--answered <回答の番号>` を渡し、回答を消費した後の `row resumed` が `row_resumed` を書いて `awaiting_next` が消える。設計承認とリリース承認の両方で確かめる（`Launch.seq` を渡すと書かれずに落ちる）
- 起動の失敗 1 回で、失敗のキーの回数が 1 だけ増える（`launch_failed` の手の `row_parked` が再検証で外れずに書かれ、次の観測の `vacant` で数え直さない）
- 回答の後の next で席を取り直した後、起動した conductor のラッパーが一覧に載ったまま `next` を打たずに `Policy.up` を超える回数の観測を続けても、起動の失敗に数えず、同じ回答の手でラッパーを置き換えない（`_expire_launched` の期限に任せると、期限の後に失敗を数えて置き換えて落ちる）。その後ラッパーが消えると `vacant` で席を返し、`charge` が失敗のキーを 1 回だけ数える
- 回答の後の next で席を取り直した直後の読み直しで、同じ回答の手が出ない（`parked` が偽になって起動のキーの番号が変わると、`launch.replace` が動き始めた conductor を置き換えて落ちる）。回答が席の返却より後に載った行（`answered_seq > parked_seq`）と前に載った行の両方で、着席の前の手のキー・`row_seated` の `launch`・着席の後の写しのキーが同じ `max(answered_seq, parked_seq)` になる（着席の前に `parked_seq` を使うと前者で落ちる）
- 待ちの起動し直しの起動が続けて失敗すると、席を返すたびに起動のキーが変わっても `Launch.fail_key` で数え、`policy.attempts` 回で打ち切って `launch_failed` を 1 回知らせ、それ以上ラッパーを起動しない（キーで数えると上限に届かず落ちる）。起動し直した conductor が待ちも実装の開始も回答の消費も書かずに終わり、`vacant` で席を返し直すことが続いても同じキーで数えて打ち切る。conductor が待ちを書いた着席の後の `vacant` は数えない。知らせのリクエストを閉じると再び打つ
- 起動し直しで `row_seated` を書くのはラッパーの起動より前で、起動の入力の `next` は必ず新しい着席の後に走る。`row_seated` の前に別の `next`（オーガナイザーが打ったものなど）が走って依存が増えていても、新しい conductor の `next` が新しい着席の待ちを書き、`_parks` の (1) で席を返す（旧着席の待ちが対象外のまま残らない）。ラッパーの起動に失敗すると `row_parked` で席が戻り、稼働中の数に残らず、次の起動の手は新しいキーになる（失敗のキーは同じ）
- 回答の後の next と待ちの起動し直しが同じ行で当たる写しで、手が 1 つ（回答の後の next）
- 旧版の耐久記録の回復: 旧版の `snapshot` で作った、4 要素の `answered` を持ち `parks`・`released` などの欄が無い写しの辞書と、その起動を `launched` に持つ `RunState` から `_after_observe` を回すと、例外にならず、手のキーが旧版と同じ `next:<スプリント>:<回答の番号>`・`Launch.answered` が同じ回答の番号になり、起動済みの手を打ち直さない（補わずに 5 要素で展開すると回復で落ちる）。旧版が記録した耐久ワークフローの途中から新しい版で再開する回帰テストを 1 本足す
- 開発ループの行（`kind: loop`）に回答が載ると、起動の入力が今のとおり `ndf lanes row resumed <スプリント>` → `ndf loop get <ループ>` で、`ndf progress next` を含まない（一律に `progress next` にすると開発ループが再開せずに落ちる）。開発ループの行が承認の待ちでも `_parks` は手を作らない
- 起動し直した conductor の `next` が回答を消費せずに戻る（差し替えた排他で進めない）と、`row resumed --answered` は `row_resumed` を書かず、行は `awaiting_next` のまま残る。回答を消費した後なら書く。止める前の conductor が先に消費していた行でも、起動し直した後の `row resumed` が書く。渡した番号より新しい回答が載っていれば書かない
- 席を返した行がある計画を中止すると、中止の手が上限の判定を通る（空いた枠に別の設計が入った後は上限で待つ）。席を返していない行の中止は今のとおり `seated`
- 割り当ての条件（手が無い・`running`・`free >= 1`・一覧に失敗していない課題がある）のどれかが欠けると打たない。1 回の判断で 1 本
- 最後の既存の行が終わって空きができた読み直しで、一覧が残っていれば `Complete` でなく割り当てを打つ。一覧が空なら `Complete`
- 全行の終わりと止める手: 上限 1 で最後の行を終えたラッパーが一覧に動いて載り、一覧に次の課題がある写しで、`_after_observe` は先に終わったレーンを止める手を打ち、止めた後の読み直しで割り当てを打つ（割り当ての分岐を先に見ると `free` が 0 で `Sleep` を繰り返して落ちる）。止める手が尽きたら `halt_failed` を割り当てより先に知らせる
- 作り直しの知らせと一覧の残り: 最後の行の後に `refresh` が失敗し、古い一覧に課題が残り `free` が 0 の写しでも、`RELIST_GRACE_SECONDS` を越えると知らせ `relist_pending` が 1 回だけ開く（期限の判定を割り当ての分岐の後に置くと毎回 `Sleep` で落ちる）
- 一覧を使う計画で最後の行が終わった直後、作り直しの前の空の一覧の写しで `Complete` を打っても、`complete_plan` は `plan_completed` を書かない（最後の `idle_listed` が `lane_row_ended` より古い）。作り直しが候補を足した後の読み直しで割り当てを打つ。作り直しが空の一覧を書いた後なら閉じる。`Complete` の観測と `complete_plan` の間に作り直しが候補を足すと、`complete_plan` が排他の中の読み直しで外れる（写しの判断で閉じると候補を残したまま閉じて落ちる）。閉じた後の `refresh` は書かない。`idle_listed` を持たない計画は今のとおり閉じる
- 完了の除外の世代: 一覧の課題 X の割り当てが断られて `assign_failed` に（X, 世代）が入った後、`Complete` の観測と `complete_plan` の間に `idle set --pin X` を書くと、`complete_plan` が排他の中で X の世代が進んだのを読んで外れ、次の観測で X を割り当てる（課題の番号だけを渡すと X を除いたまま閉じて落ちる）。調整の無い X は除いたまま閉じる
- 作り直しの取得の始まり: `refresh` が取得を始めた後（差し替えた GitHub の読み取りの中）に最後の行が終わり、その `refresh` が空の一覧を行の終わりより後の番号で書いても、`ended_seq` が行の終わりより古いので `complete_plan` は閉じない。行の終わりの後に取得を始めた `refresh` が書いた後に閉じる（書いた番号で比べると前者で閉じて落ちる）
- 作り直しの取得の世代: 取得 A を始めた後（差し替えた GitHub の読み取りの中で止める）に最後の行が終わり、その後に始めた取得 B が候補 X を含む一覧を書き、続けて A が終わると、A は書かずに `superseded` を返し、今の一覧に X が残り、`relist_pending` が偽のまま、次の観測で X を割り当てる（書いた順で採ると A が B を置き換えて X を失い、作り直しの待ちに戻って落ちる）。間に計画の事象が無い 2 本の取得が逆順に終わっても、後に始めたほうの一覧が残る
- 作り直しの待ち: 最後の行が終わり一覧が空で作り直しが済んでいない写しでは、`_after_observe` が `Complete` を打たずに `Sleep(policy.poll)` を打つ（`Complete` と `Observe` が待ち時間なしで繰り返すと落ちる）。前回と同じ項目の `refresh` も `idle_listed` を書き、その後に閉じる。`refresh` が GitHub を読めずに `work_stopped` で止まり、`RELIST_GRACE_SECONDS` を越えると知らせ `relist_pending` が 1 回だけ開き、3 回の観測を通して 2 回開かない。知らせが開いている間は閉じず、作り直さずに知らせを `ndf lanes request close <計画> <リクエストのキー>`（採番されたキー）で閉じると、`relist_pending` が偽になり前の一覧で閉じる（知らせのキー `relist:<番号>` でリクエストを引くと待ちが解けずに落ちる）。`Complete` が偽を返した後の手は `Sleep`
- 起動途上の着席: `launch_lane` が `row_seated` をコミットした後・ラッパーの起動が戻る前（差し替えた起動を止めて待ち合わせる）に観測が `vacant` の手を出しても、`park_lane` は起動の排他を待ち、起動の戻りの後にラッパーを読み直して `stale` を返し、`row_parked` を書かない。続く観測で `released` の手が出ず、起動したラッパーを置き換えない（観測の値だけで通すと、起動途上の着席に席を返し、次の観測で動いている conductor を置き換えて落ちる）。起動が失敗した着席では `launch_failed` の `row_parked` が先に書かれ、`vacant` の手は「既に `parked`」で外れる。`vacant` の手のときだけラッパーの一覧の読み取りが 1 回増え、ほかの手では増えない
- 停止の前の回答: 承認の待ちの行で `row_parked`（`stopped: true`）のコミットの後、手順 3 の `regate` の前に `row_answered` を書くと、停止の呼び出しが 0 回で `row_unparked`（`gate_changed`）が書かれ、次の観測の回答の後の next が 1 つだけ出て `seated=True` で元のラッパーを置き換える（席を返した行の手として上限の判定を通すと落ちる）。`regate` の後・`launch.stop` の前に書くと、止めて席を返したまま、回答の後の next が上限の判定を通して `row_seated` の後に起動する。どちらでも同じ観測で 2 本を起動しない
- 停止を確かめた印: 承認の回答を待つ席を返した行（`row_parked` の後に `row_stopped` を書いた行）だけが残る計画で、10 回の観測を通してラッパーの一覧を読む呼び出しが 0 回（`parked` で `park_stop` が真の行を読む条件にすると毎回読んで落ちる）。`row_parked` の後・`row_stopped` の前に耐久ステップを中断すると、次の観測が一覧を 1 回読み、ラッパーが動いていれば止め直し、消えていれば止めずに `row_stopped` だけを書き、その後の観測では読まない
- 一覧を読めないときの停止の印: `stop_pending` が真の行がある計画で、差し替えた `probes.wrappers()` が None を返す観測では、`_parks` が (4) の手を出さず、`row_stopped` が書かれず、回答が載っていても `released`・`answered` の続きの手が出ない。`park_lane` の手順 3 で `launch.wrappers()` が None を返すと、停止の呼び出しが 0 回・`row_stopped` が 0 件で `stale` を返す。一覧を読めた次の観測で、ラッパーが載っていれば止め直し、載っていなければ `row_stopped` だけを書く（None を載っていないとみなすと `row_stopped` が書かれ、次の観測の続きの手が動いている conductor を置き換えて落ちる）
- 調整の後の失敗の世代: 一覧の作成（`idle_listed` の番号 10）の後に `idle set --pin X`（番号 12）を書き、その X の割り当てが断られると、`assign_failed` に（X, 12）が入り、次の観測で X を飛ばして次の課題を割り当て、X が残っても `complete_plan` が閉じる（一覧の世代 10 で記録すると次の観測でも X を選び続けて落ちる）。失敗の後に X へ `idle set --clear` か `--pin` を書き直す（番号 15）と、次の観測で X を再び割り当ての候補にする
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
| `refresh` | GitHub を読めない | 認証・ネットワーク・上限 | `ndf.adapters.github` の例外 | 止める（前の一覧を残す） | `stopped`・`work_stopped`、終了コード 1 | 書かない。オーガナイザーは作り直す時点のうち次に来るもの（`wait` が知らせ無しで返ったとき・知らせ `relist_pending`）で打ち直す。最後の行が終わった後に打ち直しても済まないまま `RELIST_GRACE_SECONDS` を越えると、耐久ワークフローが知らせ `relist_pending` を 1 回出す。作り直しが済めば知らせを閉じ、読めないまま作り直しを待たずに閉じてよいと決めたら、作り直さずに知らせを閉じる（`relist_pending` が偽になり、前の一覧で完了か割り当てへ進む） |
| `refresh` | 計画が閉じている | 耐久ワークフローが先に計画を完了にした | `idle_listed` を書く前の `lock_plan` の中の読み直し | 書かない | `stopped`・`precondition`、終了コード 1 | 書かない。閉じた計画には要らない |
| `refresh` | 読むマイルストーンが 1 つも無い | 計画の行の課題がマイルストーンを持たず、`--milestone` も渡されていない（計画から得たマイルストーンと明示の指定の和が空） | 読み取りの (2) の前 | 止める | `stopped`・`precondition`、`--milestone` を渡すよう `summary` に書く | `--milestone` を付けて打ち直せる（計画の課題がマイルストーンを持たなくても、明示の指定だけで一覧を作る） |
| `refresh` | 基のブランチを取得できない | ネットワーク・認証・リモート名の誤り | `Git.fetch_branch` の終了コード | 止める（前の一覧を残す） | `stopped`・`work_stopped`、終了コード 1 | 書かない。打ち直しと知らせは GitHub を読めないときと同じ |
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
| `_row_item` | `plan/__init__.py` | 変更 | 変えない | 行に今のフェーズ（`phase_of`）を、`holding` の行に `held`（`phase`（`held_phase`）・`waiting`（フェーズごとに、読むときの最新の計画で `phase_wait` を求め直した値。確定仕様化の宣言は `held_paths`）・`stage`・`since`）を足す。`row_resumed` では消さない |
| `_plan_metrics` | `plan/__init__.py` | 変更 | 変えない | `idle`（件数と先頭の課題）を足す |
| `NOTICE_KINDS` | `plan/wait_decide.py` | 変更 | `("request_opened", "plan_completed", "lane_held", "lane_row_ended")` | `lane_held` と `lane_row_ended` を知らせにする。どちらも計画の事象なので、今の `--after` の番号で続きを読め、読み取りもポーリングも増えない |

### 単体テストの観点

- `status` の出力で、待ちの行だけが `held` を持つ。`row_resumed` の後も（実装を始める前に conductor が落ちた行も待ちとして）`held` を持ち続け、`row_entered`（`implement`） の後に消える。`held.waiting` は読むときの最新の計画で求め直した待つ先で、`lanes set` で `touches` を合わせると印が消える
- `wait` が `lane_held` を 1 回返し、`--after` を進めた後は返さない
- 行が終わると `wait` が `lane_row_ended` を返す（耐久ワークフローの `row end`・conductor の `row end` のどちらでも）

## 手順書と契約（`parallel.md` ほか）

満たす決定: 決定 1・決定 7・決定 8・決定 10・決定 16〜19、受け入れ条件 13・14

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/parallel.md` | 知らせの表に `lane_held`（一覧を作り直し、待つ先を読む）と `lane_row_ended`（一覧を作り直す）を足す。節「空いた時間にやること」を足し、一覧を作り直す 4 つの時点・`pin`・`exclude` の基準（計画の目的に沿わない課題を外す・依存の先になる課題を先に出す）・割り当ては耐久ワークフローが行うこと・設計承認の回答を届ける前に `ndf lanes set <計画> <レーン> --touches <設計の結果の触るファイル>... --after <重なるレーン>... --reason <理由>` を打つこと（重なるレーンが無ければ `--after` を値無しで渡して空にする。合わせずに回答を届けても壊れないが、起動し直した行が `touches` の待ちで止まり、合わせた後にもう 1 回起動し直す）を書く（決定 8）。「耐久ワークフローがすること」の表に、待ちの解けで起動し直す（ラッパーは止めず、応答を終えた行（`row_idle`）にだけ打つ。ラッパーを止めて席を返すのは #4171 へ回した #4091）・割り当て（一覧が残る間と、最後の行の終わりの後の作り直しが済むまでは計画を完了にしない）の行を足す。知らせの表に `relist_pending`（`ndf lanes idle refresh` を打ち、済めば知らせを閉じる。GitHub を読めないまま作り直しを待たないと決めたら、作り直さずに閉じる）を足し、`refresh` が `work_stopped` で止まったら次の作り直しの時点で打ち直すことを書く。設計を直す手（確認の結果の `drift` を読み、続けるなら `next --drift-reviewed`、続けないなら行を中止して同じ課題の行を足す）を書く。「質問に回答する」「まとめたリリース」の `ndf lanes answered` に `--gate <質問の id>` を足し、席を返した行でも同じ経路（設計承認: `ndf answer` → `lanes answered` → `request close`、リリース承認: `lanes release` → `lanes answered`）で答えれば起動し直しが起きること、`ndf answer` だけでは起動し直しが起きないことを書く |
| `skills/development-workflow/references/asking.md` | 「並列の計画のレーン」に、承認ゲートの `AskUserQuestion` の `metadata` へ `source`（`ndf-gate:<番号>`）に加えて `gate`（`progress wait` の `metrics.gate.id`）と `sprint`（スプリントのステートマシンの ID）を付けることを書く。付けない質問は耐久ワークフローが席を返す対象にしない（止めずに待つだけで、壊れない） |
| `skills/development-workflow/references/parallel-checks.md` | 「マシン全体の稼働中の数」に、#4050 では待ちの行（実装・確定仕様化の待ち）と承認・委ねの回答待ちの行もラッパーが動いたまま稼働中に数えること（数え方を変えないこと）を書く。数えないのは席を返す仕組み（#4091。#4171 へ回した）が入った後である |
| `skills/issue-plan-strategy/references/parallel-plan.md` | `after` の行を「スプリントの行では実装のフェーズを始める条件、開発ループの行では行を始める条件」に直す |
| `docs/contracts/commands.md` | `ndf lanes answered` の `--gate`（任意。承認ゲートの回答なら質問の id）、`ndf lanes row resumed` の `--answered`、`ndf lanes idle`、`status`・`wait` の結果の足したキー、`ndf progress next` の `held`・`drift` |
| `contracts/schemas/io/lanes.args.schema.json` | `idle` の 3 つのサブコマンドと `batch` の引数 |
| `contracts/schemas/io/progress.args.schema.json` | `next` の `--spec-paths`（1 つ以上のパスか `none`） |
| `skills/development-workflow/references/parallel.md`（7 つのフェーズ） | 節「7 つのフェーズ」を足し、フェーズと排他の表・順序の図・`declare` を受けたときの手（設計文書から書く確定仕様のパスを決めて `--spec-paths` で宣言する）・検証と棚卸しの待ちの読み方を書く。「まとめたリリース」の後ろに節「まとめた検証と棚卸し」を足す（委ねのリクエストを同じ環境（棚卸しは同じリポジトリ）でまとめ、`ndf lanes batch claim` で取りかかり、単発の担い手（`ndf:supervisor`）に任せ、検証の担い手が環境で読んだ版を `--observed-version` に渡して `ndf lanes batch close` で閉じる。行ごとの `answered` は打たない。取りかかり中の環境へ新しい版を出せないこと、版が違えば `failed` でだけ閉じられることを書く）。文脈の上限に近づいた conductor の引継ぎ文書に、依存の強い境目（設計 → 実装・実装 → 振り返り）で決めたことの理由と捨てた案を残すことを書く |
| `skills/development-workflow/SKILL.md`・`references/agent-layers.md`・`references/context-window.md` | 工程の並び（Pull Request → クリーンアップ → デプロイ → 確定仕様化 → 検証 → 振り返り → 棚卸し）と、承認の回答を待つ間に確定仕様化を流すことに直す |
| `skills/plan-to-spec/SKILL.md` | 確定仕様化が実装の worktree を使わず、基のブランチから自分の worktree を作ること。書く確定仕様のパスを最初に決めること（計画の行では `--spec-paths` の宣言に使う）。マージ・変更なし・再試行のそれぞれで自分の worktree とブランチを片付ける時点と再利用の条件（節「フェーズの表・順序・排他」の `plan-to-spec.json` の段落） |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 設計文書の「用語」の 11 語を足し、`ndf records glossary render` で作り直す |
