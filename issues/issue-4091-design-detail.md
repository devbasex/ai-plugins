# #4091 の詳細設計: 席の段階・返却・取り直し

設計文書 [issue-4091-design.md](./issue-4091-design.md) の「状態と事象の表」の遷移を、ファイルと関数の粒度へ下ろした文書である。関数はどれも表の遷移（T1〜T8）のどれかを実装し、表に無い遷移を書かない。パスは `plugins/ndf/` からの相対で書く。#4050 の実装（`row_held`・`row_implementing`・`row_answered.gate`・続きの手 `released`・`answered`）の上に乗る。

## 地図

```text
plugins/ndf/
├── contracts/workflow/progress.json        # 事象 row_seat（書き手 workflow）
├── docs/contracts/commands.md              # ndf lanes status の行の seat
├── src/ndf/progress/lane/
│   ├── types.py                            # Seat*・SEATED/VACATING/VACATED/RESEATING*・ROW_SEAT*・LaneRow.seat・LaneRow.counted*
│   ├── fold.py                             # row_seat の畳み込み
│   ├── rules.py                            # seat_wait*・GateNow*
│   ├── __init__.py                         # move_seat*・begin_implementation（段階を読む・since）
│   ├── machine.py                          # lanes_of・held_seats（counted）
│   └── liveness.py                         # judge（段階の分岐）・needs_table*
└── src/ndf/progress/plan/
    ├── run.py                              # snapshot_of・gate_now*・vacate_lane*・settle_seat*・launch_lane（返却済みの分岐と T7）
    └── run_decide.py                       # Snapshot.seats/vacates/settles・Vacate*・Settle*・_launches（段階で手を選ぶ）・_perform の分岐
```

依存の向き: `lane` は `plan` を import しない。`gate_now` はスプリントのステートマシン（`gate.asking`）を読むので `plan/run.py` に置き、結果の `GateNow` だけを `lane.rules.seat_wait` と `lane.move_seat` に渡す（`lane` は `gate` を import しない。#4050 の `record_resumed` の `consumed` と同じ形）。

| パス | 種類 | 役割 1 文 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- |
| `contracts/workflow/progress.json` | 変更 | 事象 `row_seat` を足す | — | — |
| `src/ndf/progress/lane/types.py` | 変更 | 席の段階を持ち、数えるかを決める | `Seat`（新設）・`LaneRow` | `LaneRow.seat`（欄）・`LaneRow.counted`（新設） |
| `src/ndf/progress/lane/fold.py` | 変更 | `row_seat` を畳み込む | — | レーンの畳み込み |
| `src/ndf/progress/lane/rules.py` | 変更 | 席を返す理由を 1 か所で決める | `GateNow`（新設） | `seat_wait`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | 表の遷移を照らして書く。実装の開始で段階を読む | — | `move_seat`（新設）・`begin_implementation`（変更） |
| `src/ndf/progress/lane/machine.py` | 変更 | 数える行を `counted` にする | — | `lanes_of`・`held_seats` |
| `src/ndf/progress/lane/liveness.py` | 変更 | 段階で生死を読み、一覧が要るかを決める | — | `judge`（変更）・`needs_table`（新設） |
| `src/ndf/progress/plan/run.py` | 変更 | 写しに段階と手を載せ、返却・確かめ・取り直しの耐久ステップを持つ | — | `snapshot_of`・`launch_lane`（変更）、`gate_now`・`vacate_lane`・`settle_seat`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 段階で手を選ぶ | `Snapshot`・`Vacate`（新設）・`Settle`（新設）・`Launch` | `_launches`・`_perform` の分岐（変更） |

## 席の段階（`lane/types`・`fold`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Seat` | `lane/types.py` | 新設（型） | `phase: str`・`seq: int`・`since: int`・`wait: Optional[str]`・`request: Optional[str]`・`launch: Optional[str]` | 設計文書の「集約」の 6 つの値 |
| `LaneRow.seat` | `lane/types.py` | 新設（欄） | `Seat` | 畳み込みが入れる。`row_seat` の無い行は `Seat(SEATED, <行の開始の番号>, <同じ>, None, None, None)` |
| `LaneRow.counted` | `lane/types.py` | 新設 | `@property -> bool` | `state == RUNNING and seat.phase != VACATED`。数える規則はここだけ |
| `LaneRow.seated_at_seq` | `lane/types.py` | 変更（#4050 の新設） | `@property -> int` | `seat.since` を返す（#4050 の (a)・(c') と I2 がそのまま着席の始まりを読む） |
| `row_seat` の畳み込み | `lane/fold.py` | 変更 | — | 行の `seat` を、事象の中身で `phase`・`seq`（事象の番号）・`wait`・`request`・`launch` に置き換える。`phase == reseating` なら `since` もその番号にする。`wait` と `request` は `vacating` を書いた事象の値を `vacated`・`reseating`・`seated` へ移る間も持ち越す（`row_seat` の中身に無いときは前の値） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 値 |
| --- | --- | --- | --- |
| `SEATED`・`VACATING`・`VACATED`・`RESEATING` | 定数 | `lane/types.py` | `"seated"`・`"vacating"`・`"vacated"`・`"reseating"` |
| `ROW_SEAT` | 定数（事象の種類） | `lane/types.py` | `"row_seat"` |
| `MOVES` | 定数（表の遷移。前の段階 → 許す後の段階） | `lane/types.py` | `seated → {vacating}`・`vacating → {vacated}`・`vacated → {reseating}`・`reseating → {seated, vacated}` |

## 返す理由と遷移の書き込み（`lane/rules`・`lane/__init__`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `GateNow` | `lane/rules.py` | 新設（型） | `question: str`・`request: Optional[str]` | スプリントが今待つ承認ゲートの質問の id と、その質問に合う開いた計画のリクエストのキー（無ければ None） |
| `seat_wait` | `lane/rules.py` | 新設 | `(row: LaneRow, waits: Sequence[str], gate: Optional[GateNow]) -> Optional[tuple[str, Optional[str]]]` | T1 の条件を 1 か所で決め、（待ちのキー, リクエストのキー）か None を返す。純粋な関数。共通の条件: `state == RUNNING`・`kind == sprint`・`seat.phase == SEATED`・回答の後の next を待っていない（`answered_seq <= resumed_seq`）。(1) `holding` で `held_seq > seat.since` で `waits`（`implementation_wait` の値）が空でない → `("held:<held_seq>", None)` (2) `gate` があり、`gate.request` が None でなく、`gate == gate.question` の `row_answered` が行に無い → `("gate:<question>", gate.request)`。(1) を先に見る。どれでもなければ None。観測（`snapshot_of`）と書く前の読み直し（`move_seat`）がこの関数だけを読む |
| `move_seat` | `lane/__init__.py` | 新設 | `(conn, sprint_id: str, frm: str, seq: int, to: str, cause: str, *, wait: Optional[str] = None, request: Optional[str] = None, launch: Optional[str] = None, recheck: Optional[Callable[[PlanView, LaneRow], bool]] = None) -> Optional[int]` | トランザクションを始め、`lock_plan` を取り、行を読み直す。行が `running` で、`seat.phase == frm`・`seat.seq == seq`・`to in MOVES[frm]`、`recheck` があればその値が真のときだけ `row_seat`（書き手 workflow）を `checked_write` で書いて番号を返す。外れれば書かずに None。T1 は `recheck` に「`seat_wait` を DB だけで組んだ入力で読み直し、待ちのキーが `wait` と同じ」を渡す。ほかの遷移は `recheck` を渡さない（決定 4。照らすのは段階と番号だけ） |
| `begin_implementation` | `lane/__init__.py` | 変更（#4050 の新設） | 変えない | 手順 3 の排他の中で読み直した行の `seat.phase` が `VACATING` か `VACATED` なら、`row_implementing` を書かずに今の待ちを返す（#4050 の手順 5 の `parked` の判定を置き換える）。`RESEATING` と `SEATED` は今のとおり進む（S4） |

T1 の `recheck` が使う入力は、`move_seat` の呼び手（`vacate_lane`）が組む。`waits` は排他の中で読んだ計画から `rules.implementation_wait`（#4050。DB だけ）で、`gate` は `gate_now(conn, sprint_id, view)` で作る。

## 生死の判定と数え方（`machine`・`liveness`）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `lanes_of` | `lane/machine.py` | 変更 | 変えない | レーンの「数える行を持つ」を `any(r.counted for r in rows[name])` で決める。起動の許可・`held_seats`・#4044 の `ndf lanes seats`・#4051 の索引の後始末が同じ値を読む |
| `held_seats` | `lane/machine.py` | 変えない | 変えない | `lanes_of` の値を読むので、返却済みの行のレーンを渡さなくなる |
| `judge` | `lane/liveness.py` | 変更 | 変えない | 先頭で `seat.phase` を見る。`VACATING`・`VACATED` は `seat.wait` が `held:` で始まれば `HELD`、`gate:` なら `WAITING_ANSWER`（どちらも `stall: false`）、`RESEATING` は `RESUMING` を返し、ラッパーもスプリントも読まない。`SEATED` は今の判定（#4050・#4036 の形）へ通す |
| `needs_table` | `lane/liveness.py` | 新設 | `(lanes: Mapping[str, list[LaneRow]]) -> bool` | 実行中の行に `VACATING` か `RESEATING` があれば真。観測は `judge_lanes` が一覧を要るときか、これが真のときだけ `probes.wrappers()` を 1 回読み、両方で同じ一覧を使う |

## 観測と手（`plan/run`・`plan/run_decide`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `gate_now` | `plan/run.py` | 新設 | `(conn, sprint_id: str, view: PlanView) -> Optional[GateNow]` | スプリントのステートマシンを DB だけで読み、今のステップが承認ゲート（ステップの定義が `gate` を持つ）で質問の id（`gate.asking.waiting`）があれば、計画の開いたリクエストのうち、設計承認なら種類 `lane_question` で `metadata` の `source == ndf-gate:1`・`gate == 質問の id`・`sprint == sprint_id` のもの、リリース承認なら `id == lane-release-<質問の id>` で `sprint_state == sprint_id` のもののキーを添えて返す。承認ゲートで待っていなければ None |
| `snapshot_of` | `plan/run.py` | 変更 | 変えない | 写しに `seats`・`vacates`・`settles` を載せる。`vacates` は、席ありの行のうち `seat_wait` が値を返す行（承認ゲートは計画にそのレーンの開いた `lane_question` か `lane-release` があるときだけ `gate_now` を呼ぶ）と、一覧にラッパーが動いて載る返却中の行の（レーン, 行, スプリント, `seq`, 待ちのキー, リクエストのキー）。`settles` は、一覧を読めて、ラッパーが載らない返却中の行と、取り直し中の行の（レーン, 行, スプリント, `seq`）。一覧を読めない観測では、返却中・取り直し中の行に手を出さない。`answered`・`released`（#4050）と `relaunch` には、席ありか返却済みの行だけを載せ、返却中・取り直し中の行を載せない（S5） |
| `Vacate` | `plan/run_decide.py` | 新設（手） | `lane`・`row`・`sprint`・`seq`・`wait`・`request` | キーは `vacate:<レーン>/<行>:<待ちのキー>`。待ちが同じなら段階が席ありから返却中へ進んでも同じキーなので、止める失敗を 1 つの手として数える |
| `Settle` | `plan/run_decide.py` | 新設（手） | `lane`・`row`・`sprint`・`seq` | キーは `settle:<レーン>/<行>:<seq>` |
| `_launches` | `plan/run_decide.py` | 変更 | 変えない | 今の並びの先頭（止める手の位置）に `Vacate` と `Settle` を置く。続きの手（#4050 の `answered`・`released` と中止）は写しのとおりで、返却済みの行の手は `Launch.seat` に `seq` を載せる（席ありの行は 0）。1 行に手は 1 つ（`Vacate`・`Settle` が当たる行の続きの手は写しに載らない） |
| `Launch.seat` | `plan/run_decide.py` | 新設（欄） | `int`（既定 0） | 返却済みの行の続きの手が観測した席の段階の番号。キーには入れない（決定 3） |
| `_perform` | `plan/run.py` | 変更 | 変えない | `Vacate` は `vacate_lane`、`Settle` は `settle_seat` を打つ |
| 失敗の数え方 | `plan/run_decide.py` | 変えない | — | `Vacate` は今の止める手（`halt`）と同じ扱い: 成功を返したら `launched` に置き、ラッパーが動いたまま候補に残って期限を越えたら失敗の 1 回、尽きたら `halt_failed`（`final`）。ラッパーが一覧から消えた行は `vacates` に載らず `settles` に載るので、`Vacate` の期限を待たずに `Settle` が返却済みを書く。`Vacate` と `Settle` の `stale` は失敗に数えず、`tried` に置くだけにする（次の観測で求め直す）。返却済みの行の続きの手は今の続きの手と同じ（`waiting` は数えない） |
| `vacate_lane` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, act: dict) -> dict` | 下の「返却の手順」 |
| `settle_seat` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, act: dict) -> dict` | 下の「確かめの手順」 |
| `launch_lane` | `plan/run.py` | 変更 | 変えない | 続きの手で、読み直した行の段階が `VACATED` なら下の「取り直しの手順」、`RESEATING` なら「確かめの手順」だけを行って起動しない（S6）、`VACATING` なら `stale`、`SEATED` なら今のとおり置き換え（`seated=True`）。行を始める手と止める手は変えない |

返却の手順（`vacate_lane`。T1・T3・T2）:

1. 行を読み直す。段階が `SEATED` で `seq` が手と同じなら、`move_seat(frm=SEATED, to=VACATING, cause=<held か gate>, wait=手の待ちのキー, request=手のリクエスト, recheck=…)` を打つ。None なら `{"ok": false, "stale": true}` を返す。段階が `VACATING` で `wait` が手と同じなら書かずに 2 へ進む（打ち直しと、観測が出した続きの手のどちらもここを通る）。それ以外は `stale`
2. `launch.wrappers()` を 1 回読む。None なら `stale`（書かない。返却中のまま数える）
3. そのレーンのラッパーが動いて載っていれば `launch.stop` を打つ。失敗なら止める手の失敗の形（`ok: false` と `detail`）を返す。成功なら 2 をもう 1 回だけ読み、まだ載っていれば `{"ok": true}` を返す（次の観測が同じキーの手を出し、期限を越えれば失敗に数える）
4. 載っていなければ `move_seat(frm=VACATING, seq=<今の seq>, to=VACATED, cause=<stopped か absent>)` を打ち、`{"ok": true}` を返す

確かめの手順（`settle_seat` と、`launch_lane` が取り直し中の行を見たとき。返却中の T2 と取り直し中の T7）:

1. 行を読み直し、段階が `VACATING` か `RESEATING` で `seq` が手と同じでなければ `stale`
2. `launch.wrappers()` を 1 回読む。None なら `stale`
3. 返却中なら、ラッパーが載っていなければ `move_seat(to=VACATED, cause=absent)`、載っていれば書かずに `stale`（止めるのは `Vacate`）。取り直し中なら、載っていれば `move_seat(to=SEATED, cause=present)`、載っていなければ `move_seat(to=VACATED, cause=absent)`。ラッパーを起動も停止もしない

取り直しの手順（`launch_lane` の返却済みの分岐。T4・T5・T6）:

1. 起動の入力（`first_input`）と計画を読み、`machine.admitted(own, wrapper, launch.wrappers, go, where)` を打つ。`own` は `lanes_of` なので、返却済みのこの行は数えない
2. `go(held)` の中（起動の排他の中）で、`move_seat(frm=VACATED, seq=手の seat, to=RESEATING, cause=admitted, launch=手のキー)` を打つ。None なら起動せずに `stale` を返す
3. `launch.replace(..., Seat(<席>, seated=False, held))` で起動する
4. 成功なら `move_seat(frm=RESEATING, seq=2 の番号, to=SEATED, cause=launched)`。ハブの上限の断り（`_cap_refusal`）なら `move_seat(to=VACATED, cause=refused)` を打って `waiting` の結果を返す。ほかの失敗なら `move_seat(to=VACATED, cause=launch_failed)` を打って失敗の結果を返す
5. 2 の後・4 の前に中断したら、耐久ステップの回復が同じ手を打ち直し、1 で読み直した段階が `RESEATING` なので「確かめの手順」だけを行う（2 本目を起動しない）

`next` との競合: `row_seat` と `row_implementing` はどちらも `lock_plan` の中で行を読み直してから書くので、先に書いた方だけが通る。T1 が先なら、元の conductor の `next` は `row_implementing` を書かずに今の待ちを返す。`next` が先なら、T1 の `recheck` で `seat_wait` が外れて（実装の待ちが解けた・新しい記録が無い）書かない。取り直し中の行では、起動した conductor の `next` が T5 より先に来ても実装を始める（T5 は `RESEATING → SEATED` だけを見るので、間に書かれた `row_implementing` で外れない）。

返却中の行に回答が載った（`row_answered`）ときは、続きの手は写しに載らず（S5）、T2 の後の観測で返却済みの行の手として T4 へ進む。元の conductor は回答を受けない（要求仕様の前提 2）ので、続けるのは取り直した conductor 1 本だけである。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 返却・取り直し・確かめと、conductor の `next`・オーガナイザーの `answered`・`lanes set` | レーンのステートマシン | どの書き込みも `lock_plan` の中で行を読み直す。席の遷移は `phase` と `seq` を照らす | 古い手は書かない。打ち直しは段階から続く（「返却の手順」1、「取り直しの手順」5） |
| 取り直しと、別の計画の行を始める手・取り直し | マシン全体の席 | `admit.lock` の中で数え、その中で T4 を書いてから起動する | 取り直し中の行は数えるので、`admit.lock` を出た後の別の計画の数え上げに入る |
| 観測と手 | 同じ計画 | 計画ごとに耐久ワークフローは 1 本で、手を 1 つずつ打つ | 中断した手は回復で打ち直される。新しい試行の耐久ワークフローは、取り直し中の行を `Settle` で確かめる |

### 単体テストの観点

- `seat_wait`: 席ありで `held_seq > since` かつ待つ先が残る行は `held`、取り直した後で `held_seq <= since` の行は None。待つ先が無い（#4050 の続きの手が出る）行は None。承認ゲートは、リクエストが無い・閉じた・`gate` が同じ `row_answered` がある行は None、前の質問 Q1 の回答だけがあり今の Q2 のリクエストが開く行は `gate:Q2`。回答の後の next を待つ行・開発ループの行・席あり以外の行は None
- `move_seat`: `MOVES` に無い遷移・`seq` の違う手・`running` でない行には書かない。T1 は `recheck` が偽なら書かない
- `counted`・`lanes_of`: 返却済みだけを数えず、返却中・取り直し中はラッパーが一覧に無くても数える。上限 1 で、計画 P の行が取り直し中（ラッパーがまだ無い）のとき、計画 Q の行を始める手が上限で待つ
- `judge`: 返却中・返却済みは待ちの種類、取り直し中は `RESUMING` で、`STALL_KINDS` に入らず、ラッパーとスプリントの読み取りが 0 回
- `begin_implementation`: 返却中・返却済みの行で `row_implementing` を書かず、取り直し中の行では書く
- 返却の手順: 手順 1 の後で止めて打ち直す・手順 3 の停止の後で止めて打ち直す・一覧を読めない（None）の 3 通りで、それぞれ返却中のまま残るか T2 に進み、どの時点でも返却済みの行にラッパーが動いていない
- 取り直しの手順: 手順 2 の後で止めて打ち直すと、ラッパーが無ければ返却済みに戻り、あれば席ありになり、`launch.replace` は合わせて 1 回しか呼ばれない。起動の失敗（設計承認・リリース承認の回答の後、リクエストは閉じている）で返却済みに戻り、次の観測で同じキーの手が出る。ハブの断りでは失敗に数えない
- `_launches`: 返却中・取り直し中の行に続きの手と `/goal` の入れ直しを出さない。取り直して席ありになった直後の観測で、同じ回答の手が別のキーで出ない（`RunState.launched` に同じキーが残る）
- 計画の実行中の行が取り直した 1 行だけで、conductor が `next` の前に落ちたとき、期限の後に同じキーの手が置き換えで打たれる（`seated=True`）
- 状態と事象の表のすべての遷移（T1〜T7）を通す試験を 1 本置き、表に無い遷移（例 返却済み → 席あり）が起きないことを `MOVES` で縛る

## 手順書と契約

| 文書 | 書くこと |
| --- | --- |
| `docs/specifications/ndf-parallel-machine-plan-limit.md` の「稼働中の数の数え方」 | 数える行の規則（返却済みを数えない）と、席の段階の表（設計文書の「状態と事象の表」の要点） |
| `references/parallel-checks.md` の「マシン全体の稼働中の数」 | 承認ゲートの回答を待つ行と実装の待ちの行は、返却済みになった後は数えない |
| `references/parallel.md` | 返却中・返却済みの行の読み方（`ndf lanes status` の `seat`）と、止められないとき（`halt_failed`）にオーガナイザーがラッパーを止める手 |
| `docs/contracts/commands.md` | `ndf lanes status` の行の `seat` |
| 用語集 | 「席の段階」「席を返す」「席を取り直す」（設計文書の「用語」） |
| #4050 の文書 | 設計文書の「#4050 の設計の置き換え」の表のとおり |
