# #4091 の詳細設計: 席の段階・返却・取り直し

設計文書 [issue-4091-design.md](./issue-4091-design.md) の「状態と事象の表」の遷移を、ファイルと関数の粒度へ下ろした文書である。関数はどれも表の遷移（T1〜T8）のどれかを実装し、表に無い遷移を書かない。パスは `plugins/ndf/` からの相対で書く。#4050 の実装（`row_held`・`row_implementing`・`row_answered.gate`・続きの手 `released`・`answered`）の上に乗る。

## 地図

**`quiet`・`probe`・`kept_alive` と、起動の途中・手動の再起動・期限を越えて生きる手の扱いは #4103 へ分けた（設計文書の決定 8）。** この文書の該当の行と手順は #4103 の設計の材料として残し、#4091 の実装では安全側（取り直しの起動の結果が「起動しなかった」と確定しない限り取り直し中のまま数える）に倒す。

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
│   ├── liveness.py                         # judge（段階の分岐）・needs_table*・wrapper_running（生存の判定の共通）
│   └── launch.py                           # quiet*（ハブの排他の中で一覧を読み、返却済みを書く）
├── src/ndf/domains/relay/hub.py            # quiet*（ハブの排他 hub.lock を取る文脈・溜まった要求の処理）・_drain_held*
├── src/ndf/domains/relay/tmux.py           # probe*（セッションの有無を「ある・無い・不明」で返す）
├── src/ndf/domains/relay/cli.py            # restart の頼む側が admit.lock の中で held を読み、結果まで持つ
└── src/ndf/progress/plan/
    ├── run.py                              # snapshot_of・gate_now*・vacate_lane*・settle_seat*・launch_lane（返却済みの分岐と T7）
    └── run_decide.py                       # Snapshot.seats/vacates/settles・Vacate*・Settle*・_launches（段階で手を選ぶ）・_perform の分岐
```

依存の向き: `lane` は `plan` を import しない。`gate_now` はスプリントのステートマシン（`gate.asking`）を読むので `plan/run.py` に置き、結果の `GateNow` だけを `lane.rules.seat_wait` と `lane.move_seat` に渡す（`lane` は `gate` を import しない。#4050 の `record_resumed` の `consumed` と同じ形）。`lane/launch.py` の `quiet` だけが `domains/relay/hub.py` の `quiet` を import する（錠のパスと取り方をリレーの 1 か所に置く）。

**生存の判定は 1 つ:** ラッパーの一覧（`launch.wrappers()`）は停止済みの登録行（`registered: true, running: false`）も返すので、名前が載っているかでは決めない。「そのレーンのラッパーが動いている」は、観測（`snapshot_of` の `vacates`・`settles`）・T2・T3・T7・取り直しの起動の後の確かめ・続きの手の期限（`_expire_launched`）のすべてで、今の `liveness.wrapper_running(table.get(<ラッパーの名前>))` だけで読む。以下の「載っている」「動いている」はこの値が真、「無い」は一覧を読めてこの値が偽のことをいう。返却済みへ移す前（T2・T6・T7）の「不在」は、ハブの排他の中で「無い」に加えて、`launch.quiet` が渡す「起動の途中」（そのラッパーの名前の未処理の起動の要求か、錠を持たない tmux のセッション）にも当たらないことをいう。起動の途中に当たれば返却済みを書かない（数え続ける）。

**返却済みはハブの排他の中でだけ書く:** ラッパーの起動・置き換え・手動の再起動（`ndf relay spawn`・`spawn --replace`・`restart`）は、どれもハブへの要求（`hub.submit`）になり、ハブがその排他（`<状態の保存先>/hub/hub.lock`）を持ったまま起動の確かめ（`_confirm`）まで処理する。返却済みへ移す書き込み（T2・T6・T7 の `vacated`）は、`launch.quiet` でこの排他を取り、その中で不在を確かめてから `move_seat` を打つ。排他を持つ間は起動の要求が処理されないので、不在を確かめた後・書く前にラッパーが起きることがなく、処理の途中の要求（応答待ちが切れた起動を含む）は処理が終わってから読まれる。ただし排他を取れたことは、起動の要求がすべて処理済みであることを意味しない。`hub.submit` は要求のファイルを書いてから `drain` で排他を取りにいくので、その間に頼んだ耐久ステップが中断すると、未処理の要求のファイルが残り、後の `drain` が起動する。`_confirm` が期限で失敗した起動も、作った tmux のセッションが残り、後からラッパーが錠を取りうる。そのため排他を取ったら、まず要求の置き場（`hub_dir / REQUESTS_DIR`）に溜まった要求を `drain` と同じ処理で古い順に処理し（中断で頼む側を失った要求も、ほかのリレーの操作を待たずにここで処理される。応答待ちの頼む側がいれば、`drain` と同じく結果のファイルを読む）、その後で、そのラッパーの名前の起動の要求（種類 `spawn`・置き換え・`restart`）が残っていないこと（処理の後に書かれた要求）と、そのラッパーの名前の tmux のセッションが錠を持たずに残っていないことを確かめる（名前を持たない `spawn` はハブが `t<番号>` を付けるので、レーンのラッパーの名前には当たらない）。tmux の照会は、セッションが無いと tmux が答えたときだけ「無い」とし、tmux を打てない・時間切れ・ほかの誤りは「不明」として起動の途中と同じに扱う（席を保持する）。排他を取れなければ書かずに `stale`（数え続ける）。返却済みを書いた後に手動の再起動で起きたラッパーは、起動の許可（`count` の、記録と動いているラッパーの和集合）とハブ（動いているラッパーと要求の `held` の和集合を数える。手動の `restart` も #4044 の変更で `ndf lanes seats` の名前を `held` に載せる）がどちらも数えるので上限を超えず、その行の T4 の置き換えで 1 本になる。手動の `restart` の `held` は頼む側がハブの排他の前に読むので、その読み取りから要求の処理までの間に別のレーンが T4・T5 で席を取ると古い `held` で判定される。そのため `ndf relay restart` の頼む側は、`held` を読む前から結果を受け取るまで起動の排他（`admit.lock`）を持つ（下の「手動の再起動と起動の許可の直列化」）。錠の順は `admit.lock` → `hub.lock` → `lock_plan` の 1 方向で（`admit.lock` を取るのは取り直しの手順の 5 と手動の `restart` の頼む側だけ）、ハブの処理は `lock_plan`・`admit.lock` を取らないので循環しない。

| パス | 種類 | 役割 1 文 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- |
| `contracts/workflow/progress.json` | 変更 | 事象 `row_seat` を足す | — | — |
| `src/ndf/progress/lane/types.py` | 変更 | 席の段階を持ち、数えるかを決める | `Seat`（新設）・`LaneRow` | `LaneRow.seat`（欄）・`LaneRow.counted`（新設） |
| `src/ndf/progress/lane/fold.py` | 変更 | `row_seat` を畳み込む | — | レーンの畳み込み |
| `src/ndf/progress/lane/rules.py` | 変更 | 席を返す理由を 1 か所で決める | `GateNow`（新設） | `seat_wait`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | 表の遷移を照らして書く。実装の開始で段階を読む | — | `move_seat`（新設）・`begin_implementation`（変更） |
| `src/ndf/progress/lane/machine.py` | 変更 | 数える行を `counted` にする | — | `lanes_of`・`held_seats` |
| `src/ndf/progress/lane/liveness.py` | 変更 | 段階で生死を読み、一覧が要るかを決める | — | `judge`（変更）・`needs_table`（新設）・`wrapper_running`（変えない。生存の判定の共通） |
| `src/ndf/progress/lane/launch.py` | 変更 | ハブの排他の中で一覧を読み、渡された書き込みを打つ | — | `quiet`（新設） |
| `src/ndf/domains/relay/hub.py` | 変更 | ハブの排他を外から取れるようにし、その中で溜まった要求を処理してから起動の途中の名前を返す | — | `quiet`・`_drain_held`（新設）・`drain`（中身を `_drain_held` へ移す） |
| `src/ndf/domains/relay/tmux.py` | 変更 | セッションの有無を不在と不明に分けて返す | `Tmux` | `probe`（新設）・`has`（`probe` を読む形にする） |
| `src/ndf/domains/relay/cli.py` | 変更 | 手動の `restart` の `held` の読み取りから結果までを起動の排他の中で行う | — | `restart` の要求の組み立て（#4044 が `held` を載せた箇所） |
| `src/ndf/progress/plan/run.py` | 変更 | 写しに段階と手を載せ、返却・確かめ・取り直しの耐久ステップを持つ | — | `snapshot_of`・`launch_lane`（変更）、`gate_now`・`vacate_lane`・`settle_seat`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 段階で手を選び、期限に達する続きの手を観測へ渡す | `Snapshot`・`Observe`・`Vacate`（新設）・`Settle`（新設）・`Launch` | `_launches`・`_observe`・`_expire_launched`・`_perform` の分岐（変更） |

## 席の段階（`lane/types`・`fold`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Seat` | `lane/types.py` | 新設（型） | `phase: str`・`seq: int`・`since: int`・`wait: Optional[str]`・`request: Optional[str]`・`launch: Optional[str]`・`cause: Optional[str]` | 設計文書の「集約」の 7 つの値 |
| `LaneRow.seat` | `lane/types.py` | 新設（欄） | `Seat` | 畳み込みが入れる。`row_seat` の無い行は `Seat(SEATED, <行の開始の番号>, <同じ>, None, None, None, None)` |
| `LaneRow.counted` | `lane/types.py` | 新設 | `@property -> bool` | `state == RUNNING and seat.phase != VACATED`。数える規則はここだけ |
| `LaneRow.seated_at_seq` | `lane/types.py` | 変更（#4050 の新設） | `@property -> int` | `seat.since` を返す（#4050 の (a)・(c') と I2 がそのまま着席の始まりを読む） |
| `row_seat` の畳み込み | `lane/fold.py` | 変更 | — | 行の `seat` を、事象の中身で `phase`・`seq`（事象の番号）・`wait`・`request`・`launch`・`cause` に置き換える。`cause` はその事象の値だけで、持ち越さない（最後の `row_seat` の `cause`。「取り直しの手順」の 7 が読む）。`phase == reseating` なら `since` もその番号にする。`wait` と `request` は `vacating` を書いた事象の値を `vacated`・`reseating`・`seated` へ移る間も持ち越す（`row_seat` の中身に無いときは前の値）。`launch` は `reseating` を書いた事象の値を、次の `reseating` まで（T5・T6・T7 の後も）持ち越す。T4 の起動キーと `since` の組が、同じ手の回復を見分ける材料になる |

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
| `needs_table` | `lane/liveness.py` | 新設 | `(lanes: Mapping[str, list[LaneRow]], expiring: Sequence[str]) -> bool` | 実行中の行に `VACATING` か `RESEATING` があるか、`expiring`（下の `Observe.expiring` の名前）が空でなければ真。観測は `judge_lanes` が一覧を要るときか、これが真のときだけ `probes.wrappers()` を 1 回読み、すべてで同じ一覧を使う |
| `quiet`（リレー）（#4103。設計文書の決定 8） | `domains/relay/hub.py` | 新設 | `(ctx: Context, names: Sequence[str], wait: float) -> ContextManager[Optional[frozenset[str]]]` | `hub_dir(ctx.state_dir) / LOCK_FILE` を `signals.held(…, wait)` で取る。取れなければ None を渡す。取れたら、まず溜まった要求を `drain` と同じ処理（`drain` の排他の中の繰り返しを `_drain_held(ctx, base)` に切り出し、`drain` と `quiet` の両方が打つ）で処理する。耐久ステップが要求のファイルを書いた後・`drain` の前に中断して残った要求は、ここで処理されるか（起動が通ればラッパーが動き、次の確かめで席あり）、処理が失敗すればラッパーは起きない。その後、`names` のうち起動の途中の名前を渡す: 要求の置き場（`REQUESTS_DIR`）に、`name` がその名前で種類が `spawn`・`REPLACE`・`restart` の要求が残る名前（処理の後に書かれた要求。その頼む側が自分の `drain` で処理する）と、tmux のセッションの照会（`ctx.tmux.probe(name)`）が「ある」か「不明」でラッパーの錠（`registry.wrapper_dir(…) / registry.LOCK_FILE`）を `identity.lock_held` で持たない名前（`_confirm` が期限で失敗した後に残るセッションか、残っているかを確かめられないセッション）。`drain` と同じ錠で、ハブの処理と同時には持たれない。読めない要求のファイルは `drain` が `invalid` にするので数えない |
| `probe`（tmux）（#4103。設計文書の決定 8） | `domains/relay/tmux.py` | 新設 | `(name: str) -> Optional[bool]` | `has-session` を打ち、終了コード 0 なら True、tmux がセッションかサーバーが無いと答えた（終了コード 1 で、出力が `can't find session`・`no server running`・`error connecting to`（ソケットが無い）のどれか）なら False、tmux を打てない（`OSError`）・時間切れ・ほかの誤りなら None（不明）。`has` は今のまま（`probe(name) is True`）で、ほかの呼び手を変えない |
| `quiet`（#4103。設計文書の決定 8） | `lane/launch.py` | 新設 | `(names: Sequence[str], act: Callable[[Optional[dict[str, dict[str, Any]]], frozenset[str]], T], run: Optional[Any] = None) -> Optional[T]` | リレーの文脈（`relay.cli.hub_context(os.environ)`）でリレーの `quiet` を起動の排他と同じ秒（`machine.LOCK_POLICY`）まで取りにいき、取れたら中で `wrappers(run)` を 1 回読んで `act(table, starting)` を返す（`starting` はリレーの `quiet` が渡した起動の途中の名前）。取れなければ None。`relay ls` はハブの排他を取らないので、中で読んでも待ち合わない。呼び手は、`table` が None でなく、ラッパーが `wrapper_running` で偽で、名前が `starting` に無いときだけ「不在」として返却済みを書く |

## 観測と手（`plan/run`・`plan/run_decide`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `gate_now` | `plan/run.py` | 新設 | `(conn, sprint_id: str, view: PlanView) -> Optional[GateNow]` | スプリントのステートマシンを DB だけで読み、今のステップが承認ゲート（ステップの定義が `gate` を持つ）で質問の id（`gate.asking.waiting`）があれば、計画の開いたリクエストのうち、設計承認なら種類 `lane_question` で `metadata` の `source == ndf-gate:1`・`gate == 質問の id`・`sprint == sprint_id` のもの、リリース承認なら `id == lane-release-<質問の id>` で `sprint_state == sprint_id` のもののキーを添えて返す。承認ゲートで待っていなければ None |
| `Observe.expiring` | `plan/run_decide.py` | 新設（欄） | `tuple[str, ...]`（既定は空） | この観測の後の `_expire_launched` で期限に達する続きの手のレーンの名前。`_observe` が `RunState.launched` と `waits` から `_tick_waits` と同じ式（`(waits の回数 + 1) * poll >= up`）で決める。止める手と行を始める手のレーンは載せない |
| `observe` | `plan/run.py` | 変更 | `(spec: dict, spent: Sequence[str] = (), expiring: Sequence[str] = ()) -> dict` | `_perform` が `observe(spec, list(act.spent), list(act.expiring))` で打つ。`expiring` を `snapshot_of` へ渡す |
| `snapshot_of` | `plan/run.py` | 変更 | `(view, rows, found=None, moved=None, spent=(), expiring: Sequence[str] = ()) -> dict` | `needs_table(rows, expiring)` で一覧の要否を決める。写しの `alive` に、`expiring` の各レーンのラッパーの生存を `"running"`（`wrapper_running` が真）・`"absent"`（一覧を読めて偽）・`"unknown"`（一覧を読めない）で載せる（`Snapshot.alive: tuple[tuple[str, str], ...]`）。写しに `seats`・`vacates`・`settles` を載せる。`vacates` は、席ありの行のうち `seat_wait` が値を返す行（承認ゲートは計画にそのレーンの開いた `lane_question` か `lane-release` があるときだけ `gate_now` を呼ぶ）と、`wrapper_running` が真の返却中の行の（レーン, 行, スプリント, `seq`, 待ちのキー, リクエストのキー）。`settles` は、一覧を読めて `wrapper_running` が偽の返却中の行（停止済みの登録行が載る行を含む）と、取り直し中の行の（レーン, 行, スプリント, `seq`）。一覧を読めない観測では、返却中・取り直し中の行に手を出さない。`answered`・`released`（#4050）と `relaunch` には、席ありか返却済みの行だけを載せ、返却中・取り直し中の行を載せない（S5） |
| `Vacate` | `plan/run_decide.py` | 新設（手） | `lane`・`row`・`sprint`・`seq`・`wait`・`request` | キーは `vacate:<レーン>/<行>:<待ちのキー>`。待ちが同じなら段階が席ありから返却中へ進んでも同じキーなので、止める失敗を 1 つの手として数える |
| `Settle` | `plan/run_decide.py` | 新設（手） | `lane`・`row`・`sprint`・`seq` | キーは `settle:<レーン>/<行>:<seq>` |
| `_launches` | `plan/run_decide.py` | 変更 | 変えない | 今の並びの先頭（止める手の位置）に `Vacate` と `Settle` を置く。続きの手（#4050 の `answered`・`released` と中止）は写しのとおりで、返却済みの行の手は `Launch.seat` に `seq` を載せる（席ありの行は 0）。1 行に手は 1 つ（`Vacate`・`Settle` が当たる行の続きの手は写しに載らない） |
| `Launch.seat` | `plan/run_decide.py` | 新設（欄） | `int`（既定 0） | 返却済みの行の続きの手が観測した席の段階の番号。キーには入れない（決定 3） |
| `_perform` | `plan/run.py` | 変更 | 変えない | `Vacate` は `vacate_lane`、`Settle` は `settle_seat` を打つ |
| 失敗の数え方 | `plan/run_decide.py` | 変えない | — | `Vacate` は今の止める手（`halt`）と同じ扱い: 成功を返したら `launched` に置き、ラッパーが動いたまま候補に残って期限を越えたら失敗の 1 回、尽きたら `halt_failed`（`final`）。ラッパーが一覧から消えた行は `vacates` に載らず `settles` に載るので、`Vacate` の期限を待たずに `Settle` が返却済みを書く。`Vacate` と `Settle` の `stale` は失敗に数えず、`tried` に置くだけにする（次の観測で求め直す）。`Vacate`（手順 4）と `Settle` が起動の途中（未処理の起動の要求か錠を持たない tmux のセッション）に当たったときは、書かずに `{"ok": true}` を返して `launched` に置く。要求はハブが処理し、残ったセッションはラッパーが錠を取るか終わるかで消えるので、次の観測の同じキーの手が決める。`Vacate` は、行が同じ段階のまま期限を越えれば失敗の 1 回に数え、尽きたら今の止める手と同じく `final` で知らせる（数え続けるので上限は超えない）。**`Settle` は `final` にも `failed` にもしない:** `Settle` のキー（`settle:<レーン>/<行>:<seq>`）は確かめが書くまで変わらないので、打ち切ると、期限の後にラッパーが錠を取った・セッションが消えた行の T7・T2 を打つ手が無くなる。`Settle` の期限切れは `_fail` を通さず、`launched` と `waits` から外して同じ観測の候補に戻し（次の観測でまた打つ）、期限切れの回数だけを `attempts` に数える。回数が `Policy.attempts` に達したら、行の段階で今の知らせ（返却中は `halt_failed`、取り直し中は `launch_failed`）を 1 回だけ `unreported` に置き、キーを `noticed` に積んで 2 回目を知らせない。以後も観測ごとに同じキーの `Settle` を打ち、生存か不在が確定した観測で T7（または T2）を書く。返却済みの行の続きの手は今の続きの手と同じ（`waiting` は数えない）。**続きの手（行を始めた起動の席を使う手。取り直しの後の手を含む）の期限切れは、失敗に数える前にそのレーンの生存を見る:** `_expire_launched` は期限を越えた続きの手について、同じ観測の写しの `alive`（`Observe.expiring` で頼んだレーンの生存）を読み、`"running"` なら失敗に数えず、置き換えもせず、`launched` に残して数え直さない（起動した conductor が準備や作業で `next` まで時間を要しても、生きている間は同じ手で置き換えない。止まっているかは #4036 の席ありの行の生死の判定が決める）。このとき、その手のキーを `RunState.kept_alive`（新設の欄。生存を理由に期限の後も `launched` に残したキー。キーが `launched` から外れたら同時に外す）に積む。`_notice_stalls` は、`kept_alive` のキーを持つレーンを `relaunching`（`/goal` の入れ直しを待つので止まりを知らせないレーン）から外す。`launched` に残したキーは `_candidates` が打たないので、`_relaunchable` が真でも入れ直しは起きず、外さないと止まりの知らせも出ないまま止まる。外すので、ラッパーが生きたまま写しの `stalls` に載る行は、今の止まりと同じく `noticed` のキーで 1 回だけ知らせる（置き換えはしない。人が止めればラッパーが消え、次の期限切れの判定が `"absent"` で打ち直す）。`"absent"` なら今のとおり失敗の 1 回として数え、同じキーで打ち直す。`"unknown"` か `alive` に無い（`expiring` に載らなかった）ときは期限切れを決めず、`launched` と `waits` の回数を据え置いて次の観測へ持ち越す（数えない。次の `_observe` がまた `expiring` に載せる）。行を始める手と止める手（`Vacate` を含む）の期限は変えない |
| `RunState.kept_alive`（#4103。設計文書の決定 8） | `plan/run_decide.py` | 新設（欄） | `tuple[str, ...]`（既定は空） | 期限を越えたがラッパーが動いているので `launched` に残した続きの手のキー。`_notice_stalls` がそのレーンの止まりの知らせを抑えないために読む（上の「失敗の数え方」） |
| `_notice_stalls`（#4103。設計文書の決定 8） | `plan/run_decide.py` | 変更 | 変えない | `relaunching` から `kept_alive` のキーを持つレーンを外す |
| `vacate_lane` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, act: dict) -> dict` | 下の「返却の手順」 |
| `settle_seat` | `plan/run.py` | 新設（耐久ステップ） | `(spec: dict, act: dict) -> dict` | 下の「確かめの手順」 |
| `launch_lane` | `plan/run.py` | 変更 | 変えない | 続きの手で、読み直した行の段階が `VACATED` なら下の「取り直しの手順」、`RESEATING` なら「取り直しの手順」の 6（同じ手の回復）か「確かめの手順」だけを行って起動しない（S6）、`VACATING` なら `stale`、`SEATED` なら、同じ手の回復（「取り直しの手順」の 7）に当たれば結果だけを返し、当たらなければ今のとおり置き換え（`seated=True`）。行を始める手と止める手は変えない |
| 同じ手の回復の判定 | `plan/run.py` | 新設（`launch_lane` の内部） | `(row: LaneRow, act: Launch) -> bool` | `act.seat > 0`（返却済みの行から出た手）・`row.seat.launch == act.key`（T4 が保存した起動キー）・`row.seat.since > act.seat`（その T4 が手の観測した返却済みの後にある）のすべてで真。真なら、行の今の段階はこの手の試行が T4 で始めたものである。席ありの行から出た後続の手（`act.seat == 0`。期限の後の打ち直しなど）は当たらない |

返却の手順（`vacate_lane`。T1・T3・T2）:

1. 行を読み直す。段階が `SEATED` で `seq` が手と同じなら、`move_seat(frm=SEATED, to=VACATING, cause=<held か gate>, wait=手の待ちのキー, request=手のリクエスト, recheck=…)` を打つ。None なら `{"ok": false, "stale": true}` を返す。段階が `VACATING` で `wait` が手と同じなら書かずに 2 へ進む（打ち直しと、観測が出した続きの手のどちらもここを通る）。それ以外は `stale`
2. `launch.wrappers()` を 1 回読む。None なら `stale`（書かない。返却中のまま数える）
3. そのレーンのラッパーが動いていれば（`wrapper_running`）`launch.stop` を打つ。失敗なら止める手の失敗の形（`ok: false` と `detail`）を返す。成功なら 4 へ進む
4. `launch.quiet` の中で一覧を読み、不在なら `move_seat(frm=VACATING, seq=<今の seq>, to=VACATED, cause=<stopped か absent>)` を打ち、`{"ok": true}` を返す。まだ動いているか起動の途中なら書かずに `{"ok": true}` を返す（次の観測が同じキーの手を出し、期限を越えれば失敗に数える）。排他を取れない・一覧を読めなければ `stale`

確かめの手順（`settle_seat` と、`launch_lane` が取り直し中の行を見たとき。返却中の T2 と取り直し中の T7）:

1. 行を読み直し、段階が `VACATING` か `RESEATING` で `seq` が手と同じでなければ `stale`（`launch_lane` から来たときの照らし方は「取り直しの手順」の 6）
2. `launch.quiet` の中で一覧を読む。排他を取れない・一覧が None なら `stale`
3. 返却中なら、不在なら `move_seat(to=VACATED, cause=absent)`、動いていれば書かずに `stale`（止めるのは `Vacate`）。取り直し中なら、動いていれば `move_seat(to=SEATED, cause=present)`、不在なら `move_seat(to=VACATED, cause=absent)`。どちらの段階でも、動いていないが起動の途中（そのラッパーの名前の未処理の起動の要求か、錠を持たない tmux のセッション）なら書かずに `{"ok": true}` を返す（「失敗の数え方」。中断で残った起動の要求は 2 の `quiet` が先に処理するので、ここで残るのは処理の後に書かれた要求か、錠を取る前のセッションか、照会が不明のセッションだけで、どれも次の観測の同じキーの `Settle` が決める。返却済みの行にラッパーが起きない）。どの書き込みも 2 の排他の中で打つ。ラッパーを起動も停止もしない

取り直しの手順（`launch_lane` の返却済みの分岐。T4・T5・T6）:

1. 起動の入力（`first_input`）と計画を読み、`machine.admitted(own, wrapper, launch.wrappers, go, where)` を打つ。`own` は `lanes_of` なので、返却済みのこの行は数えない
2. `go(held)` の中（起動の排他の中）で、`move_seat(frm=VACATED, seq=手の seat, to=RESEATING, cause=admitted, launch=手のキー)` を打つ。None なら起動せずに `stale` を返す
3. `launch.replace(..., Seat(<席>, seated=False, held))` で起動する
4. 成功なら `move_seat(frm=RESEATING, seq=2 の番号, to=SEATED, cause=launched)`。ハブの上限の断り（`_cap_refusal`。`read_spawn` が `waiting` にした結果）は、ハブが置き換えの対象を止める前に上限を判定した結果で、対象のラッパーが無いことを保証しない（T4 の後に手動の `relay restart` で起きていることがある）。`launch.quiet` の中で一覧を読み、不在なら `move_seat(to=VACATED, cause=refused)` を打って `waiting` の結果を返す。動いている・起動の途中・排他を取れない・一覧を読めないときは書かずに `waiting` の結果を返す（失敗に数えない。行は取り直し中のまま数え続け、次の観測の `Settle` が T7 で決める。動いていれば席ありになり、その後の観測が席ありの行の続きの手で置き換える）
5. ほかの失敗は、起動しなかったと確定しない（tmux を作った後の起動の確かめ（`_confirm`）の失敗も、ハブの応答待ちの切れ（`wait_timeout`。ハブは要求を処理し続けうる）も失敗になる）。返却済みへすぐには戻さず、「確かめの手順」の 2・3 を取り直し中の行へ打つ: `launch.quiet` の排他の中で一覧を読み、動いていれば `move_seat(to=SEATED, cause=present)` を打って成功の結果を、不在なら `move_seat(to=VACATED, cause=launch_failed)` を打って失敗の結果を返す（失敗は手のキーで数える）。起動の途中（応答待ちが切れて要求が未処理・`_confirm` の後にセッションが残る）・排他を取れない・一覧を読めなければ書かずに取り直し中のまま失敗の結果を返し（失敗に数える）、次の観測の `Settle` が同じ確かめで決める（2 本目は起動しない）
6. 同じ手の回復（取り直し中）: 2 の後・4 の前に中断すると、耐久ステップの回復が同じ手を打ち直す。行の段階は `RESEATING` で、行の `seq` は T4 の番号、手の `seat` は返却済みの番号なので、番号では照らさない。上の「同じ手の回復の判定」が真なら、行の今の `seq`（T4 の番号）を使って 5 の確かめ（排他の中で一覧を読み、T5 か T6）を打つ。偽なら「確かめの手順」へ手の `seat` のまま渡す（別の手なので `stale`）。どちらも 2 本目を起動しない
7. 同じ手の回復（確定の後）: T5 か 5・6 の書き込みをコミットした後、耐久ステップの結果が保存される前に中断すると、打ち直しは `SEATED`（または `VACATED`）の行を読む。「同じ手の回復の判定」が真なら起動も書き込みもせず、行の最後の `row_seat` の `cause`（`launch_lane` が `first_input` と同じトランザクションの `lane.read_view` で読んだ行の `seat.cause`。畳み込みが最後の事象の値を入れる）から、下の「確定の結果の作り直し」の表で結果を作り直して返す。表に無い `cause`（`admitted` は取り直し中なので 6 に当たる。`held`・`gate`・`stopped` は返却の側で、判定が真にならない）は作り直さず `stale` を返す。`SEATED` の行に `launch.replace` をもう一度打たないので、起動済みの conductor を置き換えない。偽（席ありの行から出た後続の手・別の T4 の後の手）なら今のとおり進む（`SEATED` は置き換え、`VACATED` は 1 から）

確定の結果の作り直し（「取り直しの手順」の 7。結果の形は `launch.replace` の今の結果と同じで、`_after_launch` が読む）:

| 行の段階 | 最後の `cause` | どの遷移が書いたか | 返す結果 | 判断の扱い |
| --- | --- | --- | --- | --- |
| `SEATED` | `launched` | 手順 4 の T5 | `{"ok": true, "detail": "前の試行が起動した（席あり）"}` | 起動の成功（`launched` に置く） |
| `SEATED` | `present` | 手順 5・6 か `Settle` の T7 | `{"ok": true, "detail": "前の試行の起動の後にラッパーが動いていた（席あり）"}` | 同上 |
| `VACATED` | `launch_failed` | 手順 5・6 の T6 | `{"ok": false, "detail": "前の試行の起動の後にラッパーが無かった（返却済み）"}` | 失敗の 1 回（手のキーで数える） |
| `VACATED` | `absent` | `Settle` の T7（手の中断の後に別の観測が確かめた） | `{"ok": false, "detail": "前の試行の中断の後にラッパーが無かった（返却済み）"}` | 同上 |
| `VACATED` | `refused` | 手順 4 の T6（ハブの断り） | `{"ok": false, "waiting": {"reason": "machine-lanes", "machine_lanes": null, "cap": null, "unreadable": []}, "detail": "前の試行がハブの上限で断られた（返却済み）"}` | 失敗に数えない（待ち） |

手動の再起動と起動の許可の直列化（`domains/relay/cli.py`。S1 の前提）:

1. `ndf relay restart` の頼む側は、#4044 の `seats.read`（`ndf lanes seats`）を打つ前に起動の排他（`machine.admit_lock(limits.seconds(machine.LOCK_POLICY))`）を取り、`hub.submit` が結果を返すまで持つ。取れなければ要求を書かずに `refused`（「起動の排他を取れない。少し待って打ち直す」）を返す
2. 取り直しの T4 と行を始める起動は、同じ `admit.lock` の中で数えてから起動の要求を出すので、手動の `restart` の `held` の読み取りと要求の処理は、別のレーンの T4・T5 の前か後のどちらかに並ぶ。前なら、その別のレーンの `admitted` が再起動したラッパーを動いているラッパーとして数える。後なら、`held` に T4・T5 の後の行（取り直し中か席あり。ラッパーが落ちても `counted`）が載り、ハブが上限で断る
3. ハブの処理は `admit.lock` を取らないので、錠の順（`admit.lock` → `hub.lock`）は変わらない。`spawn` の通常の起動は今の #4044 の形のままにする（行を持たない名前は T4 の対象にならない）

`next` との競合: `row_seat` と `row_implementing` はどちらも `lock_plan` の中で行を読み直してから書くので、先に書いた方だけが通る。T1 が先なら、元の conductor の `next` は `row_implementing` を書かずに今の待ちを返す。`next` が先なら、T1 の `recheck` で `seat_wait` が外れて（実装の待ちが解けた・新しい記録が無い）書かない。取り直し中の行では、起動した conductor の `next` が T5 より先に来ても実装を始める（T5 は `RESEATING → SEATED` だけを見るので、間に書かれた `row_implementing` で外れない）。

返却中の行に回答が載った（`row_answered`）ときは、続きの手は写しに載らず（S5）、T2 の後の観測で返却済みの行の手として T4 へ進む。元の conductor は回答を受けない（要求仕様の前提 2）ので、続けるのは取り直した conductor 1 本だけである。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 返却・取り直し・確かめと、conductor の `next`・オーガナイザーの `answered`・`lanes set` | レーンのステートマシン | どの書き込みも `lock_plan` の中で行を読み直す。席の遷移は `phase` と `seq` を照らす | 古い手は書かない。打ち直しは段階から続く（「返却の手順」1、「取り直しの手順」6・7） |
| 取り直しと、別の計画の行を始める手・取り直し | マシン全体の席 | `admit.lock` の中で数え、その中で T4 を書いてから起動する | 取り直し中の行は数えるので、`admit.lock` を出た後の別の計画の数え上げに入る |
| 返却済みの書き込み（T2・T6・T7）と、ハブが処理するラッパーの起動・置き換え・手動の `ndf relay restart` | そのレーンのラッパーと席 | 返却済みは `hub.lock` の中で一覧と、未処理の起動の要求・錠を持たない tmux のセッションを読んでから書く。起動の要求はハブが `hub.lock` の中で処理する | 不在の確かめと書き込みの間に起動が割り込まず、中断で残った未処理の要求や `_confirm` の後に残ったセッションがあれば書かない。書いた後の手動の再起動は、#4044 の `held`（席を持つレーン。ラッパーの落ちた席ありの行を含む）と動いているラッパーの和集合でハブが上限を判定し（頼む側が `held` の読み取りから結果まで `admit.lock` を持つので、`held` が古くならない）、通れば動いているラッパーとして起動の許可とハブが数え、T4 が置き換える |
| 続きの手の期限と、取り直した conductor の準備 | そのレーンのラッパー | 期限切れを数える前に同じ観測の一覧で生存を読む | 動いている間は置き換えず数えない。消えた後は同じキーで打ち直す |
| 観測と手 | 同じ計画 | 計画ごとに耐久ワークフローは 1 本で、手を 1 つずつ打つ | 中断した手は回復で打ち直される。新しい試行の耐久ワークフローは、取り直し中の行を `Settle` で確かめる |

### 単体テストの観点

- `seat_wait`: 席ありで `held_seq > since` かつ待つ先が残る行は `held`、取り直した後で `held_seq <= since` の行は None。待つ先が無い（#4050 の続きの手が出る）行は None。承認ゲートは、リクエストが無い・閉じた・`gate` が同じ `row_answered` がある行は None、前の質問 Q1 の回答だけがあり今の Q2 のリクエストが開く行は `gate:Q2`。回答の後の next を待つ行・開発ループの行・席あり以外の行は None
- `move_seat`: `MOVES` に無い遷移・`seq` の違う手・`running` でない行には書かない。T1 は `recheck` が偽なら書かない
- `counted`・`lanes_of`: 返却済みだけを数えず、返却中・取り直し中はラッパーが一覧に無くても数える。上限 1 で、計画 P の行が取り直し中（ラッパーがまだ無い）のとき、計画 Q の行を始める手が上限で待つ
- `judge`: 返却中・返却済みは待ちの種類、取り直し中は `RESUMING` で、`STALL_KINDS` に入らず、ラッパーとスプリントの読み取りが 0 回
- `begin_implementation`: 返却中・返却済みの行で `row_implementing` を書かず、取り直し中の行では書く
- 返却の手順: 手順 1 の後で止めて打ち直す・手順 3 の停止の後で止めて打ち直す・一覧を読めない（None）の 3 通りで、それぞれ返却中のまま残るか T2 に進み、どの時点でも返却済みの行にラッパーが動いていない
- 生存の判定: 一覧に `registered: true, running: false` の行だけが載るレーンで、返却中の行は `settles` に載って T2 で返却済みへ進み（名前の有無で読むと `vacates` に残って落ちる）、取り直し中の行は T7 で返却済みへ戻る（名前の有無で読むと席ありになって落ちる）。同じ一覧で `_expire_launched` は期限を越えた続きの手を失敗に数える
- 取り直しの手順: 手順 2 の後で止めて同じ手を打ち直すと、手の `seat`（返却済みの番号）と行の `seq`（T4 の番号）が違っても、保存した起動キーで同じ手と判定して T4 の番号で確かめ、ラッパーが無ければ返却済みに戻り、あれば席ありになり、`launch.replace` は合わせて 1 回しか呼ばれない（番号で照らすと `stale` で取り直し中のまま残って落ちる）。T5 のコミットの後・結果の保存の前で止めて打ち直すと、`launch.replace` を呼ばずに成功の結果を返す（置き換えると落ちる）。同じキーで席ありの行から出た後続の手（`seat == 0`。期限の後の打ち直し）は置き換えで起動する（回復と取り違えて起動しないと落ちる）。起動の失敗（設計承認・リリース承認の回答の後、リクエストは閉じている）で、ハブの排他の中の一覧にラッパーが無ければ返却済みに戻り、次の観測で同じキーの手が出る。起動の確かめの失敗・応答待ちの切れで一覧にラッパーが動いていれば席ありになり、排他を取れなければ取り直し中のまま数え続ける（失敗をすぐ返却済みにすると落ちる）。ハブの断りでは失敗に数えない。ハブの断りの後、排他の中の一覧でラッパーが無ければ返却済みへ戻り、T4 の後に手動の `relay restart` で対象を起こしてから断らせると、書かずに取り直し中のまま `waiting` を返し、次の `Settle` で席ありになる（断りだけで返却済みにすると落ちる）
- 確定の結果の作り直し: T5（`launched`）・手順 5 の T5（`present`）・手順 5 の T6（`launch_failed`）・手順 4 の T6（`refused`）・`Settle` の T7（`present`・`absent`）の各コミットの直後で止めて同じ手を打ち直し、`launch.replace` を呼ばずに表の結果（成功・失敗・`waiting`）を返し、`_after_launch` が成功は `launched` へ、失敗は手のキーの 1 回、`waiting` は数えずに扱う（`Seat.cause` を畳み込まないと落ちる）
- 起動の途中: (a) `hub.submit` が要求のファイルを書いた後・`drain` の前に止めた（要求のファイルが残る）状態で `Settle` と手順 5 を打つと、排他を取れても返却済みを書かず、その後の `drain` がラッパーを起こしても返却済みの行にラッパーが動かない。(b) `_confirm` が期限で失敗し、tmux のセッションが錠を持たずに残る状態で同じく書かず、後でラッパーが錠を取ると次の `Settle` が席ありにする（一覧だけで不在と読むと落ちる）。(c) 上限 1 で唯一のレーンが取り直し中のとき、`hub.submit` が要求のファイルを書いた後・`drain` の前に耐久ステップを止め、ほかのリレーの操作（`spawn`・`restart`・`stop`）を 1 回も打たずに観測だけを回すと、次の `Settle` の `quiet` が残った要求を処理して、起動が通れば席あり、起動が失敗すれば返却済みになる（残った要求を見て書かないだけで処理しないと、取り直し中のまま止まって落ちる）。(d) tmux の照会が時間切れ・`OSError`・知らない誤りを返し、`relay ls` は読めてラッパーが動いていないとき、返却済みを書かない。セッションが無いと tmux が答えたときだけ書く（`has` の偽を不在と読むと落ちる）
- `Settle` の打ち切りなし: `Settle` が `Policy.attempts` 回続けて期限を越えると知らせが 1 回だけ出て、`failed`・`final` に入らない。(a) その後にラッパーが錠を取ると次の観測の `Settle` が席ありにし、(b) セッションが消えると返却済みにする。どちらも同じキーの手で、2 回目の知らせは出ない（尽きた `Settle` を止める手と同じく打ち切ると、取り直し中のまま数え続けて落ちる）
- 生存を理由に残した手と止まり: 続きの手（`/goal` の入れ直しを含む）が期限を越え、ラッパーが動いたまま写しの `stalls` にその行が載るとき、`kept_alive` にキーが載り、止まりの知らせが 1 回出て、置き換えは打たれない。人がラッパーを止めた後の観測で `"absent"` になり、同じキーで打ち直され、`kept_alive` から外れる（`_relaunchable` が真のままなので知らせを抑えると、再起動も知らせも出ずに落ちる）
- 手動の再起動の直列化: 上限 1 で、A の `relay restart` の頼む側が `held` を読んだ直後に、別のスレッドで B の T4・T5 を走らせ、その後 B のラッパーだけを落としてから A の要求を処理させると、B の T4 は A の頼む側が `admit.lock` を離すまで待ち、A の再起動の後に B の `admitted` が A を数えて上限で待つ。逆の順（B の T5 の後に A が `held` を読む）では A が上限で断られる。どちらの順でも B の続きの手（`--seated`）の後に動くラッパーは 1 本である（頼む側が `admit.lock` を持たずに `held` を読むと 2 本動いて落ちる）
- ハブの排他: `launch.quiet` の排他を別のスレッドが持つ間、T2・T6・T7 の返却済みは書かれない。返却中の行で不在を確かめた後に `relay restart` の要求を差し込む試験で、要求の処理が返却済みの書き込みの前か後のどちらかに並び、前ならラッパーが動いていて書かれず、後なら返却済みの後に起きたラッパーを起動の許可が数えて上限 1 の別の計画を待たせる
- 手動の再起動と落ちた席: 上限 1 で、別レーン B の行が席ありのままラッパーだけが落ち、レーン A の行が返却済みのとき、A の `relay restart` は `held`（B を含む）と動いているラッパーの和集合で数えて上限で断られ、B の続きの手（`--seated`）の後も動くラッパーは 1 本である（`held` を載せずに動いているラッパーだけで数えると 2 本動いて落ちる。#4044 の実装を前提に置く）
- `_launches`: 返却中・取り直し中の行に続きの手と `/goal` の入れ直しを出さない。取り直して席ありになった直後の観測で、同じ回答の手が別のキーで出ない（`RunState.launched` に同じキーが残る）
- 計画の実行中の行が取り直した 1 行だけで、conductor が `next` の前に落ちたとき（一覧にラッパーが無い）、期限の後に同じキーの手が置き換えで打たれる（`seated=True`）。conductor が生きたまま `next` を打たずに期限を越えても、失敗に数えず置き換えない（期限だけで数えると落ちる）。一覧を読めない観測では期限切れを決めない
- 期限の入力: `_observe` が期限に達する続きの手のレーンだけを `Observe.expiring` に載せ（止める手・行を始める手・期限前の手は載せない）、`observe` がそれを `snapshot_of` へ渡し、写しの `alive` に `running`・`absent`・`unknown` が載る。`judge_lanes` が一覧を要らない観測で、返却中・取り直し中の行が無く `expiring` が空なら一覧を読まず（`relay ls` の起動 0 回）、`expiring` が載れば 1 回だけ読む。`_expire_launched` は `running` で数えず、`absent` で 1 回数え、`unknown` で回数を据え置く
- 状態と事象の表のすべての遷移（T1〜T7）を通す試験を 1 本置き、表に無い遷移（例 返却済み → 席あり）が起きないことを `MOVES` で縛る

## 手順書と契約

| 文書 | 書くこと |
| --- | --- |
| `docs/specifications/ndf-parallel-machine-plan-limit.md` の「稼働中の数の数え方」 | 数える行の規則（返却済みを数えない）と、席の段階の表（設計文書の「状態と事象の表」の要点） |
| `references/parallel-checks.md` の「マシン全体の稼働中の数」 | 承認ゲートの回答を待つ行と実装の待ちの行は、返却済みになった後は数えない |
| `references/parallel.md` | 返却中・返却済みの行の読み方（`ndf lanes status` の `seat`）と、止められないとき（`halt_failed`）にオーガナイザーがラッパーを止める手 |
| `docs/contracts/commands.md` | `ndf lanes status` の行の `seat`。`ndf relay restart` の項に、並列の計画の待つ行のラッパーを起こすと起動の許可とハブが動いているラッパーとして数えること、その再起動の上限の判定は席を持つレーン（`held`）と動いているラッパーの和集合で数えること（#4044） |
| 用語集 | 「席の段階」「席を返す」「席を取り直す」（設計文書の「用語」） |
| #4050 の文書 | 設計文書の「#4050 の設計の置き換え」の表のとおり |
