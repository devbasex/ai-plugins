# #4110 の詳細設計: 停止の確かめ（`relay_hub.hold`・`_confirm_seat`）

設計文書 [issue-4110-design.md](./issue-4110-design.md) の決定 1・2 を、ファイルと関数の粒度へ下ろした文書である。#4091 の詳細設計（[issue-4091-design-detail.md](./issue-4091-design-detail.md)）の `quiet`・`probe` の行と、返却の手順の 4・確かめの手順の 2・3・取り直しの手順の 4・5 を置き換える。それ以外の #4091 の手順（段階の照らし方・同じ手の回復・確定の結果の作り直し）は変えない。パスは `plugins/ndf/` からの相対で書く。実装は #4091 の実装の PR で行う。

## 地図

```text
plugins/ndf/
├── src/ndf/core/relay_hub.py               # hub_dir・LOCK_FILE・INBOXES・START_KINDS・REPLACE・hold*（新設のモジュール）
├── src/ndf/domains/relay/hub.py            # hub_dir・LOCK_FILE・REQUESTS_DIR・REPLACE を relay_hub から読む（定義を移すだけ）
└── src/ndf/progress/plan/run.py            # _confirm_seat*・vacate_lane・settle_seat・launch_lane（停止の確かめを打つ所）
```

`*` は新設。#4091 の詳細設計の地図のうち、`lane/launch.py` の `quiet`、`domains/relay/hub.py` の `quiet`・`_drain_held`、`domains/relay/tmux.py` の `probe`、`domains/relay/cli.py` の `restart` の頼む側は #4091 では作らない（`probe` と `cli.py` は #4103）。

依存の向き: `progress/plan/run.py` → `core/relay_hub.py`（`progress` から `core` へ）、`domains/relay/hub.py` → `core/relay_hub.py`（`domains` から `core` へ）。どちらも層の規則（`src/ndf/checks/rules.json` の `structure.layers`）の下向きで、`progress` は `domains` を読まない。`relay_hub` が読むのは `ndf.contracts.facts` と `ndf.core.durable`・`ndf.core.durable_locks` だけである。

| パス | 種類 | 役割 1 文 | 依存 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/relay_hub.py` | 新設 | ハブの置き場・錠・受付箱の名前を 1 か所に持ち、ハブの排他を持つ文脈を出す | `ndf.contracts.facts`・`ndf.core.durable`・`ndf.core.durable_locks` | — | `hub_dir`（`hub.py` から移す）・`hold`（新設） |
| `src/ndf/domains/relay/hub.py` | 変更 | 定数と `hub_dir` を `relay_hub` から読む | `relay_hub` | — | 振る舞いは変えない |
| `src/ndf/progress/plan/run.py` | 変更 | 返却済みを書く遷移と取り直し中の席ありを 1 か所で確かめる | `relay_hub`・`lane.launch`・`lane.liveness`・`lane.machine` | — | `_confirm_seat`（新設）・`vacate_lane`・`settle_seat`・`launch_lane`（#4091 の新設と変更） |

## ハブの置き場と排他（`core/relay_hub`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `hub_dir` | `core/relay_hub.py` | 移す（`hub.py` から） | `(state_dir: Path) -> Path` | 今のとおり `state_dir / facts.value("locations", "relay-hub", "path")` |
| `hold` | 同上 | 新設 | `(state_dir: Path, name: str, seconds: float) -> ContextManager[Optional[bool]]` | `hub_dir(state_dir) / LOCK_FILE` を `durable.open_lock_file` で開き、`durable_locks.try_flock(handle, LOCK_EX)` を `seconds` の間（`machine.admit_lock` と同じ間隔で）取りにいく。取れなければ None を渡す。取れたら、`INBOXES` のそれぞれの `*.json` を読み、`kind` が `START_KINDS` のどれかで `name` が引数と同じ要求が 1 つでもあれば True、無ければ False を渡す。文脈を出るときに錠を放して閉じる。要求を処理も削除もしない |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 値 |
| --- | --- | --- | --- |
| `LOCK_FILE` | 定数（移す） | `core/relay_hub.py` | `"hub.lock"` |
| `REQUESTS_DIR` | 定数（移す） | 同上 | `"requests-v2"`（今の版の受付箱） |
| `INBOXES` | 定数（新設） | 同上 | `(REQUESTS_DIR, "requests")`。`"requests"` は導入を読まない旧版の受付箱で、旧版の頼む側の `drain` も同じ `hub.lock` を取って処理する |
| `REPLACE` | 定数（移す） | 同上 | `"replace"` |
| `START_KINDS` | 定数（新設） | 同上 | `("spawn", REPLACE, "restart")`。ラッパーを起こしうる要求の種類（`hub.handle` が `spawn`・`restart` に回す種類） |

### 異常系の処理

| 起きること | 扱い |
| --- | --- |
| 錠のファイルやハブの置き場が無い | `durable.open_lock_file` が作る（ハブの `drain` と同じく、無ければ作ってよい） |
| 受付箱のディレクトリが無い | その受付箱は空として読む |
| 要求のファイルが読む間に消えた（頼む側が期限切れで消した・処理された） | 数えない（`FileNotFoundError`） |
| 要求のファイルが JSON として読めない・形が違う | 数えない。ハブの `drain` も `invalid` にして起動しない（要求は `signals.replace_json` で一時ファイルから置き換えて書かれるので、書きかけは見えない） |
| 名前の無い `spawn` | 数えない。ハブが `t<番号>` を付けるので、レーンのラッパーの名前に当たらない |

## 停止の確かめ（`plan/run._confirm_seat`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_confirm_seat` | `progress/plan/run.py` | 新設 | `(spec: dict, lane: str, row: str, sprint: str, frm: str, seq: int, cause_absent: str, table: Optional[dict[str, dict[str, Any]]]) -> tuple[str, str]` | 下の「処理の手順」で、返却中か取り直し中の行を確かめ、（書いた遷移か書かなかった理由, 文）を返す。理由は `present`・`vacated`・`running`・`starting`・`busy`・`unknown`・`stop_failed`・`stale` のどれか。文は手の結果の `detail` に載せる（`stop_failed` なら `launch.stop` の結果の `detail`）。`frm` は手が照らす段階（`VACATING` か `RESEATING`）、`seq` は照らす番号、`table` は呼び手が直前に読んだ一覧 |

### 処理の手順

1. `table` が None なら `unknown` を返す（書かない）
2. 一覧でそのレーンのラッパーが動いていれば（`liveness.wrapper_running(table.get(<ラッパーの名前>))`）、`frm` が `RESEATING` なら `move_seat(frm=RESEATING, seq, to=SEATED, cause=present)` を打ち、番号が返れば `present`、None なら `stale`。`frm` が `VACATING` なら書かずに `running`（止めるのは `Vacate`）
3. `launch.stop(repo, plan, lane)` を打つ。`relay stop` はハブを通り、ハブの `drain` は受付箱の要求を古い順に処理してから最後にこの `stop` を処理する。ok でなければ書かずに `stop_failed`
4. `relay_hub.hold(identity.load().state_dir, <ラッパーの名前>, limits.seconds(machine.LOCK_POLICY))` に入る。None なら `busy`
5. 錠を持ったまま `launch.wrappers()` を 1 回読む。None なら `unknown`。動いていれば 2 と同じ（`RESEATING` は `present`、`VACATING` は `running`）。`hold` が True（その名前の起動の要求が受付箱にある）なら `starting`
6. どれでもなければ、錠を持ったまま `move_seat(frm, seq, to=VACATED, cause=cause_absent)` を打ち、番号が返れば `vacated`、None なら `stale`。錠は `move_seat` のコミットの後に放す

状態の保存先は、リレーの CLI（`relay.cli.hub_context`）と同じ `identity.load().state_dir` を読む。

### 呼ぶ所（停止の確かめを打つ所）

#4091 の詳細設計の手順の番号で書く。手の結果の形と失敗の数え方は #4091 のとおりで、下の表は `_confirm_seat` の返り値をどの結果にするかだけを決める。

| 呼ぶ所（#4091 の手順） | 引数 | `present` | `vacated` | `running` | `starting` | `busy`・`unknown`・`stale` | `stop_failed` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 返却の手順の 4（`vacate_lane`。手順 3 で止めた後） | `frm=VACATING`、`cause_absent` は手順 3 で止めたら `stopped`、止めるものが無ければ `absent`、`table` は手順 3 の後に読んだ一覧 | — | `{"ok": true}` | `{"ok": true}`（次の観測の同じキーの手が止める。期限を越えれば失敗の 1 回） | `{"ok": true}` | `stale` | 止める手の失敗の形（`ok: false` と `detail`） |
| 確かめの手順の 2・3（`settle_seat`。返却中・取り直し中） | `frm` は読み直した行の段階、`cause_absent=absent`、`table` は手の中で 1 回読んだ一覧 | `{"ok": true}` | `{"ok": true}` | `stale`（止めるのは `Vacate`） | `{"ok": true}` | `stale` | `{"ok": false, "detail": …}` |
| 取り直しの手順の 4（ハブの上限の断りの後） | 一覧で動いていれば呼ばずに書かず `waiting` を返す（#4091 のとおり次の観測の T7 に任せる）。動いていなければ `frm=RESEATING`、`seq` は手順 2 の番号、`cause_absent=refused` | — | `waiting` の結果 | — | `waiting` の結果 | `waiting` の結果（取り直し中のまま） | `waiting` の結果（取り直し中のまま） |
| 取り直しの手順の 5（ほかの失敗の後）と 6（同じ手の回復で取り直し中を見たとき） | `frm=RESEATING`、`seq` は手順 2 の番号（6 は行の今の `seq`）、`cause_absent=launch_failed` | 成功の結果 | 失敗の結果（手のキーで 1 回数える） | — | 失敗の結果（取り直し中のまま） | 失敗の結果（取り直し中のまま） | 失敗の結果（取り直し中のまま） |

取り直しの手順の 7（確定の後の同じ手の回復）と「確定の結果の作り直し」の表は #4091 のまま変えない（`cause` の値 `present`・`absent`・`launch_failed`・`refused` は `_confirm_seat` が書く値と同じ）。返却の手順の 4 の `stopped` は返却の側なので、作り直しの対象にならない。

### 異常系の処理

| 起きること | 扱い |
| --- | --- |
| 一覧を読めない（手の前・錠の中） | 書かない（`unknown`）。行は数え続け、次の観測で読み直す |
| `relay stop` が失敗する（錠が `relay.stop-wait` の秒で空かない・ハブの応答待ちが切れる） | 書かない（`stop_failed`）。止める手の失敗として数える（#4091 の `halt_failed` の経路） |
| `hub.lock` を `parallel-machine.admit-lock` の秒の間に取れない | 書かない（`busy`）。次の観測で読み直す |
| 受付箱にその名前の起動の要求が残る | 書かない（`starting`）。次の観測の同じキーの手の `relay stop` の `drain` がその要求を処理してから止める |
| `move_seat` が照らして外れる | 書かない（`stale`。#4091 のとおり失敗に数えない） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 停止の確かめと、ハブの起動・置き換え・手動の `relay restart`（どの版の頼む側も） | そのレーンのラッパーと席 | 確かめ（一覧の読み直しと受付箱の読み）と返却済みの書き込みを `hub.lock` の中で行う。ハブは `hub.lock` の中でだけ要求を処理する | 確かめから書き込みまでにラッパーは起きない。錠を持つ間に書かれた要求は書き込みの後に処理され、そのラッパーが錠を取った後は起動の許可とハブが動いているラッパーとして数える。錠を取るまでの窓は設計文書の決定 3 の代償 |
| 停止の確かめの 3 の `relay stop` と、受付箱に溜まった起動の要求 | ハブの受付箱 | `drain` がファイル名の時刻の順に処理する | 古い起動の要求が先に処理され、その後の `stop` が止める。中断しても、次の観測の同じキーの手が同じ順で打ち直す |
| 停止の確かめと、取り直しの起動（同じ行） | そのレーンのラッパー | 計画ごとに耐久ワークフローは 1 本で、手を 1 つずつ打つ。中断した手の子のプロセスが後から書く要求は、設計文書の決定 3 の代償 | 打ち直しは #4091 の同じ手の回復のとおり |
| 錠の順 | `admit.lock`・`hub.lock`・`lock_plan` | 取り直しの手順の 4・5 は `admit.lock` の中で `_confirm_seat` を打つので、`admit.lock` → `hub.lock` → `lock_plan`。`relay stop` は `hold` の前に終わる（`hub.lock` を持ったまま `relay stop` を打たない） | ハブの `drain` は `admit.lock`・`lock_plan` を取らないので循環しない |

### 単体テストの観点

- `hold`: 別のプロセスがハブの `signals.held` で `hub.lock` を持つ間は None を渡し、放した後は取れる（同じファイルの `flock` で排他になることを確かめる）。`requests-v2/` と `requests/` のどちらかに、同じ名前の `spawn`・`replace`・`restart` があれば True、`stop`・別の名前・名前の無い `spawn`・読めないファイルだけなら False。受付箱が無くても False。要求のファイルを消さない
- `_confirm_seat`: 一覧が None なら書かない。取り直し中で動いていれば止めずに席あり。返却中で動いていれば書かずに `running`。動いていなければ `relay stop` の後に錠の中で読み直し、要求が無ければ返却済み。錠の中の一覧で動いていれば返却済みを書かない（`relay stop` の後・錠の前に手動の再起動を差し込む）
- 錠を持たない tmux のセッション（`_confirm` の期限切れの後を作る）が残る返却中の行で、`relay stop` がセッションを止め、その後に返却済みになる
- `relay stop` の前に、そのレーンの `replace` の要求を受付箱に置いた（頼む側を落とした）状態で確かめると、`drain` がその要求を処理してから `stop` が止め、返却済みになる。`relay stop` の後・`hold` の前に要求を置くと書かずに `starting` になり、次の確かめで返却済みになる
- `hold` を持つ間に別のプロセスが `relay spawn --replace` を打つと、その処理は返却済みの書き込みの後になる。上限 1 で、その後に起きたラッパーを、別の計画の行の起動の許可が数えて待つ
- 取り直しの手順の 4: ハブの断りの後に一覧で動いていれば書かずに `waiting`、動いていなければ止めて返却済みにし `waiting`。手順の 5: 起動の確かめの失敗・応答待ちの切れの後、動いていれば席ありで成功、動いていなければ止めて返却済みで失敗の 1 回
- `hub.py` の振る舞いは変わらない（今のハブのテストがそのまま通る）。`src/ndf/progress` の下に `ndf.domains` の import が無く、構造の検査が通る
