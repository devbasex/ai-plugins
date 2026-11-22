# #4110 の詳細設計: 停止の確かめ（`relay_hub.hold`・`_confirm_seat`）

設計文書 [issue-4110-design.md](./issue-4110-design.md) の決定 1・2 を、ファイルと関数の粒度へ下ろした文書である。#4091 の詳細設計（[issue-4091-design-detail.md](./issue-4091-design-detail.md)）の `quiet`・`probe` の行と、返却の手順の 4・確かめの手順の 2・3・取り直しの手順の 4・5 を置き換える。それ以外の #4091 の手順（段階の照らし方・同じ手の回復・確定の結果の作り直し）は変えない。パスは `plugins/ndf/` からの相対で書く。実装は #4091 の実装の PR で行う。

## 地図

```text
plugins/ndf/
├── src/ndf/core/relay_hub.py               # hub_dir・LOCK_FILE・INBOXES・START_KINDS・REPLACE・SOCKET・start_name*・starting*・session*・hold*（新設のモジュール）
├── src/ndf/domains/relay/hub.py            # 定数を relay_hub から読む・drain（stop より前に並ぶ同じ名前の起動の要求を取り消す・期限を過ぎたら次の要求を取らない）・submit（期限の判定）・_cancel_superseded*・stop_named*
├── src/ndf/domains/relay/tmux.py           # SOCKET を relay_hub から読む（定義を移すだけ）
└── src/ndf/progress/plan/run.py            # _confirm_seat*・vacate_lane・settle_seat・launch_lane（停止の確かめを打つ所）
```

`*` は新設。#4091 の詳細設計の地図のうち、`lane/launch.py` の `quiet`、`domains/relay/hub.py` の `quiet`・`_drain_held`、`domains/relay/tmux.py` の `probe`、`domains/relay/cli.py` の `restart` の頼む側は #4091 では作らない（`probe` と `restart` の頼む側は #4103）。`domains/relay/cli.py` は変えない（単名の `relay stop` は今もハブを必ず通す。「名前つきの停止」）。

依存の向き: `progress/plan/run.py` → `core/relay_hub.py`（`progress` から `core` へ）、`domains/relay/hub.py`・`tmux.py` → `core/relay_hub.py`（`domains` から `core` へ）。どれも層の規則（`src/ndf/checks/rules.json` の `structure.layers`）の下向きで、`progress` は `domains` を読まない。`relay_hub` が読むのは `ndf.contracts.facts` と `ndf.core.durable`・`ndf.core.durable_locks` と標準の `subprocess` だけである。

| パス | 種類 | 役割 1 文 | 依存 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/relay_hub.py` | 新設 | ハブの置き場・錠・受付箱・tmux のソケットの名前を 1 か所に持ち、起動の要求の見分けと受付箱と tmux のセッションの有無を読み、ハブの排他を持つ文脈を出す | `ndf.contracts.facts`・`ndf.core.durable`・`ndf.core.durable_locks` | — | `hub_dir`（`hub.py` から移す）・`start_name`・`starting`・`session`・`hold`（新設） |
| `src/ndf/domains/relay/hub.py` | 変更 | 定数と `hub_dir` を `relay_hub` から読む。`drain` は `stop` より前に並ぶ同じ名前の起動の要求を取り消し、`stop` の要求は錠の中でセッションの消滅とラッパーの錠の解放を確かめて成功を返す。`submit` の期限を、誰が処理したかによらず結果を読んだ時刻で判定する | `relay_hub` | — | `drain`・`submit`（変更）・`_cancel_superseded`・`stop_named`（新設）・`handle`（`stop` を `stop_named` へ渡す） |
| `src/ndf/domains/relay/tmux.py` | 変更 | `SOCKET` を `relay_hub` から読む | `relay_hub` | `Tmux` | 振る舞いは変えない |
| `src/ndf/progress/plan/run.py` | 変更 | 返却済みを書く遷移と取り直し中の席ありを 1 か所で確かめる | `relay_hub`・`lane.launch`・`lane.liveness`・`lane.machine` | — | `_confirm_seat`（新設）・`vacate_lane`・`settle_seat`・`launch_lane`（#4091 の新設と変更） |

## ハブの置き場と排他（`core/relay_hub`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `hub_dir` | `core/relay_hub.py` | 移す（`hub.py` から） | `(state_dir: Path) -> Path` | 今のとおり `state_dir / facts.value("locations", "relay-hub", "path")` |
| `start_name` | 同上 | 新設 | `(req: Mapping[str, Any]) -> Optional[str]` | 要求の `kind` が `START_KINDS` のどれかで `name` が空でない文字列ならその名前、それ以外は None。`starting` とハブの `_cancel_superseded` の両方がこれで起動の要求を見分ける（見分けを 2 か所に持たない） |
| `starting` | 同上 | 新設 | `(state_dir: Path, name: str) -> bool` | `hub_dir(state_dir)` の下の `INBOXES` のそれぞれの `*.json` を読み、`start_name` が引数の `name` を返す要求が 1 つでもあれば True、無ければ False。要求を処理も削除もしない。錠は取らない（`hold` が錠の中で呼ぶ） |
| `session` | 同上 | 新設 | `(environ: Mapping[str, str], name: str, timeout: float, runner: Any = subprocess.run) -> Optional[bool]` | そのラッパーの名前の tmux のセッションの有無を 3 値で返す。`[environ の NDF_RELAY_TMUX か "tmux", "-L", SOCKET, "has-session", "-t", "=<名前>"]` を `timeout` の秒で打ち、終了コード 0 なら True。終了コード 1 で出力が `can't find session`・`no server running` を含むか、`error connecting to` と `No such file or directory` を含めば False。`OSError`・時間切れ・それ以外は None（#4103 の詳細設計の `probe` と同じ規則。#4103 の実装の `probe` はこの関数を読み、規則を 2 か所に持たない） |
| `hold` | 同上 | 新設 | `(state_dir: Path, name: str, seconds: float) -> ContextManager[Optional[bool]]` | `hub_dir(state_dir) / LOCK_FILE` を `durable.open_lock_file` で開き、`durable_locks.try_flock(handle, LOCK_EX)` を `seconds` の間（`machine.admit_lock` と同じ間隔で）取りにいく。取れなければ None を渡す。取れたら `starting(state_dir, name)` を渡す。文脈を出るときに錠を放して閉じる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 値 |
| --- | --- | --- | --- |
| `LOCK_FILE` | 定数（移す） | `core/relay_hub.py` | `"hub.lock"` |
| `REQUESTS_DIR` | 定数（移す） | 同上 | `"requests-v2"`（今の版の受付箱） |
| `INBOXES` | 定数（新設） | 同上 | `(REQUESTS_DIR, "requests")`。`"requests"` は導入を読まない旧版の受付箱で、旧版の頼む側の `drain` も同じ `hub.lock` を取って処理する |
| `REPLACE` | 定数（移す） | 同上 | `"replace"` |
| `START_KINDS` | 定数（新設） | 同上 | `("spawn", REPLACE, "restart")`。ラッパーを起こしうる要求の種類（`hub.handle` が `spawn`・`restart` に回す種類） |
| `SOCKET` | 定数（`tmux.py` から移す） | 同上 | `"ndf-lanes"` |

### 異常系の処理

| 起きること | 扱い |
| --- | --- |
| 錠のファイルやハブの置き場が無い | `durable.open_lock_file` が作る（ハブの `drain` と同じく、無ければ作ってよい） |
| 受付箱のディレクトリが無い | その受付箱は空として読む |
| 要求のファイルが読む間に消えた（頼む側が期限切れで消した・処理された） | 数えない（`FileNotFoundError`） |
| 要求のファイルが JSON として読めない・形が違う | 数えない。ハブの `drain` も `invalid` にして起動しない（要求は `signals.replace_json` で一時ファイルから置き換えて書かれるので、書きかけは見えない） |
| 名前の無い `spawn` | 数えない。ハブが `t<番号>` を付けるので、レーンのラッパーの名前に当たらない |
| tmux を打てない・時間切れ・知らない出力（`session`） | None（不明）。呼び手は「ある」と同じく席を返さない |

## 名前つきの停止（ハブの `drain`・`stop_named`・`submit`）

停止の経路は 2 つあり、この設計が変えるのはハブの側だけである。`relay.cli` は変えない。

| 経路 | 今の振る舞い | この設計の後 |
| --- | --- | --- |
| 単名の `relay stop <名前>`（`HANDLERS["stop"]` → `_hub`）。停止の確かめの 3 の `launch.stop` はこの経路 | 停止シグナルを置かず、錠の外で何も読まずに、いつも `hub.submit(ctx, {"kind": "stop", "name": ...})` を打つ。判定はハブの `hub.lock` の中 | `cli.py` は変えない。ハブの側で、取り消し（`_cancel_superseded`）・止まったことの確かめ（`stop_named`）・期限（`submit`・`drain`）が効く |
| 一括の `relay stop --all`（`_stop_all` → 名前ごとの `_stop_one`） | 名前ごとに停止シグナルを置き、錠の外の `ctx.tmux.has` が真のときだけ `submit` する。tmux の外のラッパーはシグナルだけで成功 | `cli.py` は変えない（今のまま）。`submit` した要求には、ハブの側の取り消し・確かめ・期限が同じく効く。停止の確かめは一括の経路を使わないので、錠の外で `has` を読む窓は席の返却に関わらない |

一括の経路を無条件に `submit` する形にしないのは、tmux の外のラッパーまで 1 本ずつ要求を書いて応答を待つことになり、ハブの競合時に待ちが本数分たまり、シグナルを置けた名前まで失敗に数えるからである。

今の単名の経路に残る問題は次の 3 つである。

- (a) 残った起動の要求を処理してから止めると、停止の操作が起動を起こす。中断で起動の要求だけが受付箱に残った行では、`relay stop` の `drain` がその要求を処理してラッパーを起こし、その後に止める。`replace` は作業ディレクトリ・引数・席番号を登録簿へ保存し、`restart` は再起動の回数を使い、後の `stop` はどちらも戻さない
- (b) 今のハブの `stop` は `tmux kill-session` の成否もセッションが消えたことも確かめず、ラッパーの錠が空いていれば成功を返す。錠を取らずに残ったセッション（`_confirm` の期限切れの後）では、kill が失敗しても `relay stop` が成功し、セッションが残る。入口の `has` も tmux の失敗を「無い」と読む
- (c) `submit` は応答待ちの中で自分の `drain` を最後まで回してから結果を読み、期限は `drain` の後にしか見ない。ほかの名前の起動が溜まっていると、`relay.hub-wait` の秒を越えても結果があれば成功を返す。別のプロセスの `drain` を待つときは期限で `wait_timeout` になるので、誰が処理したかで期限の効き方が違う

そこで、`drain` で停止より先に受け付けた同じ名前の起動を実行せずに取り消し、`stop` の要求は錠の中で止まったことを確かめてから成功を返し、`submit` の期限を誰が処理したかによらず同じ規則で判定する。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_cancel_superseded` | `domains/relay/hub.py` | 新設 | `(ctx: Context, base: Path, paths: list[Path]) -> list[Path]` | `drain` が錠の中で読んだ要求の並び（古い順）を受け、各 `stop` の要求より前に並ぶ、同じ名前の起動の要求（`relay_hub.start_name` がその名前を返すもの）を取り消し、取り消さなかったパスの並びを返す。取り消しは、結果のファイルへ `Outcome(False, "後の停止の要求（<要求 ID>）が取り消した", "cancelled")` を書き、`_log` に残し、要求のファイルを消す（処理した要求と同じ後始末）。取り消した要求は `handle` へ渡さない |
| `drain` | 同上 | 変更 | `(ctx: Context, deadline: Optional[float] = None) -> bool` | 錠を取った後、今の版の受付箱の `*.json` を 1 回読んで古い順に並べ、`_cancel_superseded` を通してから残りを今のとおり処理する。`deadline`（`ctx.monotonic()` の値）があれば、各要求を `handle` へ渡す前に `ctx.monotonic() >= deadline` を見て、過ぎていればそれ以降の要求を処理せずに錠を放す（残りは次の `drain` が処理する。処理中の 1 件は打ち切らない）。処理の途中に書かれた要求は始めに読んだ並びに入らないので、次の `drain` が処理する |
| `submit` | 同上 | 変更 | 今のとおり | 期限 `deadline = ctx.monotonic() + ctx.hub_wait.seconds` を 1 回決め、応答待ちの各回で `drain(ctx, deadline)` を打つ。結果のファイルを読んだら、その時点で `ctx.monotonic() < deadline` のときだけその結果を返す。期限を過ぎて読んだ結果は消して捨て、期限で切れたときと同じく要求のファイルを消して `wait_timeout` を返す。自分の `drain` が処理した結果か、別のプロセスの `drain` が書いた結果かで分けない |
| `handle` | 同上 | 変更 | 今のとおり | `kind` が `stop` の要求を `stop_named` へ渡す（ほかの種類は今のとおり） |
| `stop_named` | 同上 | 新設 | `(ctx: Context, name: str) -> Outcome` | `hub.lock` の中で、ラッパーの錠（`identity.lock_held`）と `relay_hub.session(ctx.environ, name, ctx.tmux.timeout)` を読む。錠が空いていて照会が False なら「動いていない」で成功。照会が True か None なら `ctx.tmux.kill(name)` を 1 回打つ（錠だけが持たれていて照会が False なら打たない）。その後 `ctx.stop_wait` の秒まで、「錠が空いていて照会が False」を待ち、満たせば「止めた」で成功、満たせなければ `Outcome(False, "<名前> が <秒> 秒で止まったと確かめられない（錠: 持たれている／空き、tmux: ある／不明／無い、kill: <detail>）", "wait_timeout")`。kill の成否は文に載せるだけで、成功はセッションの消滅（照会が False）と錠の解放の両方を確かめたときに限る |

`spawn`・`restart` の中の `_await_exit` が打つ今の `stop` は変えない（取り消しも `stop_named` も通らない）。

### 守ること

- ハブは `hub.lock` の中でだけ起動の要求を処理する。`stop_named` もその中で走るので、セッションと錠を読んでから成功を返すまでに、同じ名前の起動は完了しない。`stop_named` より前に完了した起動は、セッションか錠として見えて止まる
- `stop_named` の成功は、錠の中で「ラッパーの錠が空いている」と「tmux の照会が無いと答えた」の両方を見たときだけである（(b)）。kill が失敗してセッションが残る・照会が不明のままなら `wait_timeout` の失敗で、`launch.stop` は ok を返さず、停止の確かめは `stop_failed` になる
- 単名の経路は停止シグナルを置かないので、tmux の外のラッパー（錠だけが持たれてセッションが無い）には、今の `stop` と同じく錠が空くまで待ち、`relay.stop-wait` の秒で空かなければ `wait_timeout` の失敗を返す。「シグナルを置いた」で成功を返す分岐は `stop_named` に持たない（それは一括の経路の `_stop_one` が錠の外で返す、今のままの成功である）
- 停止より前に並ぶ同じ名前の起動の要求は `handle` へ渡らないので、ラッパーは起きず、登録簿（作業ディレクトリ・引数・席番号）も再起動の回数も変わらない（(a)）。待っている頼む側の `submit` は取り消しの結果を読み、`ok: false`・`cause: cancelled` を返す。`launch.replace` が受けたら、#4091 の取り直しの手順の 5（ほかの失敗）として扱う
- `stop` より後ろに並ぶ同じ名前の起動の要求（停止の後に受け付けたもの）は取り消さず、`stop` の後で処理する。停止の後の頼みは新しい意図として扱う（設計文書の決定 3 の代償の窓と同じ）
- ほかの名前の要求は、取り消さずに今のとおり古い順に処理する
- 取り消すのは今の版の受付箱の要求だけである。旧版の受付箱（`requests/`）の要求は今の版の `drain` が処理しないので取り消さない（「停止の確かめ」の異常系の旧版の受付箱の行）。旧版のハブが今の版の受付箱を処理する間も取り消さない（#4118 の版の混在の範囲）
- `submit` の期限（(c)）: 結果を `relay.hub-wait` の秒の内に読めたときだけ成功する。自分で `drain` するときは期限を過ぎた後に新しい要求を取らないので、`submit` が返るまでの時間は `relay.hub-wait` の秒に処理中の 1 件の分（`relay.started` か `relay.stop-wait` の秒）を足した長さで抑えられる。期限で処理されなかった自分の要求は消すので、後から実行されない。期限の前に `_cancel_superseded` が取り消した要求は取り消されたままになる（停止の意図に沿う側）。処理を終えたのに期限を過ぎて読んだ結果を捨てるのは、今の別のプロセスの `drain` を待つ場合（期限で要求を消し、処理中のハブはその結果を書かない）と同じ扱いで、起動の要求でも同じである

手動の `relay stop <名前>` も同じ経路を通る。止めたい名前の残った起動は取り消され、後から起きない。

## 停止の確かめ（`plan/run._confirm_seat`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_confirm_seat` | `progress/plan/run.py` | 新設 | `(spec: dict, lane: str, row: str, sprint: str, frm: str, seq: int, cause_absent: str, table: Optional[dict[str, dict[str, Any]]]) -> tuple[str, str]` | 下の「処理の手順」で、返却中か取り直し中の行を確かめ、（書いた遷移か書かなかった理由, 文）を返す。理由は `present`・`vacated`・`running`・`starting`・`busy`・`unknown`・`stop_failed`・`stale` のどれか。文は手の結果の `detail` に載せる（`stop_failed` なら `launch.stop` の結果の `detail`）。`frm` は手が照らす段階（`VACATING` か `RESEATING`）、`seq` は照らす番号、`table` は呼び手が直前に読んだ一覧 |

### 処理の手順

1. `table` が None なら `unknown` を返す（書かない）
2. 一覧でそのレーンのラッパーが動いていれば（`liveness.wrapper_running(table.get(<ラッパーの名前>))`）、`frm` が `RESEATING` なら `move_seat(frm=RESEATING, seq, to=SEATED, cause=present)` を打ち、番号が返れば `present`、None なら `stale`。`frm` が `VACATING` なら書かずに `running`（止めるのは `Vacate`）
3. `launch.stop(repo, plan, lane)` を打つ。どの錠も持たずに打つ（`admit.lock`・`hub.lock`・`lock_plan` のどれも持たない）。`launch.stop` が打つのは単名の `ndf relay stop <ラッパーの名前>`（`_hub` の経路。一括の `--all` ではない）で、停止シグナルを置かずにいつもハブを通る（「名前つきの停止」）。ハブの `drain` は、その名前の先に受け付けた起動の要求を実行せずに取り消し、ほかの要求を古い順に処理してから、最後にこの `stop` を錠の中の `stop_named` で処理する。ok でなければ（`stop_named` がセッションの消滅と錠の解放を確かめられない・`relay.hub-wait` の秒の内に結果を読めない・取り消し以外の失敗）書かずに `stop_failed`
4. `relay_hub.hold(identity.load().state_dir, <ラッパーの名前>, limits.seconds(machine.LOCK_POLICY))` に入る。None なら `busy`
5. 錠を持ったまま `launch.wrappers()` を 1 回読む。None なら `unknown`。動いていれば 2 と同じ（`RESEATING` は `present`、`VACATING` は `running`）。`hold` が True（その名前の起動の要求が受付箱にある）なら `starting`
6. 錠を持ったまま `relay_hub.session(os.environ, <ラッパーの名前>, limits.seconds("relay.tmux"))` を 1 回打つ。True（3 の後・錠の前に起きた起動が、ラッパーの錠を取らないセッションを残した。`_confirm` の期限切れの後を含む）なら `starting`、None なら `unknown`。どちらも書かない（次の観測の同じキーの手の 3 がそのセッションを止める）
7. どれでもなければ、錠を持ったまま `move_seat(frm, seq, to=VACATED, cause=cause_absent)` を打ち、番号が返れば `vacated`、None なら `stale`。錠は `move_seat` のコミットの後に放す

錠を持つ間に打つのは、`relay ls` 1 回（今の `hub.listing` は `hub.lock` を取らない）・受付箱の読み・tmux の照会 1 回（`relay.tmux` の秒まで）・`move_seat` 1 回だけで、ハブの処理を待つものは無い。

状態の保存先は、リレーの CLI（`relay.cli.hub_context`）と同じ `identity.load().state_dir` を読む。

### 呼ぶ所（停止の確かめを打つ所）

#4091 の詳細設計の手順の番号で書く。手の結果の形と失敗の数え方は #4091 のとおりで、下の表は `_confirm_seat` の返り値をどの結果にするかだけを決める。

取り直しの手順の 4・5 は、起動の排他（`admit.lock`）を放した後に打つ。`machine.admitted` に渡す `go` は取り直しの手順の 2・3（T4 の書き込みと `launch.replace`）だけを行い、T4 の番号を閉包の変数へ残して `launch.replace` の結果を返す。`admitted` が返った後、その変数に番号があるとき（`go` が走って T4 を書いた）だけ、手順 4・5 を `admit.lock` の外で打つ。T5（取り直し中 → 席あり）も T6（取り直し中 → 返却済み）も数える席を増やさない（取り直し中の行は既に数えている）ので、`admit.lock` は要らない。`admitted` が返ってから 4・5 を打つまでに中断すると、#4091 の取り直しの手順の 6（同じ手の回復）が打ち直す。

| 呼ぶ所（#4091 の手順） | 引数 | `present` | `vacated` | `running` | `starting` | `busy`・`unknown`・`stale` | `stop_failed` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 返却の手順の 4（`vacate_lane`。手順 3 で止めた後） | `frm=VACATING`、`cause_absent` は手順 3 で止めたら `stopped`、止めるものが無ければ `absent`、`table` は手順 3 の後に読んだ一覧 | — | `{"ok": true}` | `{"ok": true}`（次の観測の同じキーの手が止める。期限を越えれば失敗の 1 回） | `{"ok": true}` | `stale` | 止める手の失敗の形（`ok: false` と `detail`） |
| 確かめの手順の 2・3（`settle_seat`。返却中・取り直し中） | `frm` は読み直した行の段階、`cause_absent=absent`、`table` は手の中で 1 回読んだ一覧 | `{"ok": true}` | `{"ok": true}` | `stale`（止めるのは `Vacate`） | `{"ok": true}` | `stale` | `{"ok": false, "detail": …}` |
| 取り直しの手順の 4（ハブの上限の断りの後） | `admit.lock` を出た後に `launch.wrappers()` を 1 回読み、動いていれば呼ばずに書かず `waiting` を返す（#4091 のとおり次の観測の T7 に任せる）。動いていなければ `frm=RESEATING`、`seq` は手順 2 の番号、`cause_absent=refused`、`table` はその一覧 | 成功の結果（錠の中の読み直しで動いていて T5 を書いた。手順 5 の `present` と同じ結果で、取り直しの手順の 7 の作り直しの表の `present` の行とも同じ） | `waiting` の結果 | — | `waiting` の結果 | `waiting` の結果（取り直し中のまま） | `waiting` の結果（取り直し中のまま） |
| 取り直しの手順の 5（ほかの失敗の後）と 6（同じ手の回復で取り直し中を見たとき） | `frm=RESEATING`、`seq` は手順 2 の番号（6 は行の今の `seq`）、`cause_absent=launch_failed`、`table` は `admit.lock` を出た後に読んだ一覧 | 成功の結果 | 失敗の結果（手のキーで 1 回数える） | — | 失敗の結果（取り直し中のまま） | 失敗の結果（取り直し中のまま） | 失敗の結果（取り直し中のまま） |

取り直しの手順の 7（確定の後の同じ手の回復）と「確定の結果の作り直し」の表は #4091 のまま変えない（`cause` の値 `present`・`absent`・`launch_failed`・`refused` は `_confirm_seat` が書く値と同じ）。返却の手順の 4 の `stopped` は返却の側なので、作り直しの対象にならない。

### 異常系の処理

| 起きること | 扱い |
| --- | --- |
| 一覧を読めない（手の前・錠の中） | 書かない（`unknown`）。行は数え続け、次の観測で読み直す |
| `relay stop` が失敗する（`stop_named` が `relay.stop-wait` の秒の内にセッションの消滅と錠の解放を確かめられない（kill の失敗・照会の不明を含む）・ハブの応答待ちが `relay.hub-wait` の秒で切れる） | 書かない（`stop_failed`）。止める手の失敗として数える（#4091 の `halt_failed` の経路） |
| `hub.lock` を `parallel-machine.admit-lock` の秒の間に取れない | 書かない（`busy`）。次の観測で読み直す |
| 受付箱にその名前の起動の要求が残る | 書かない（`starting`）。今の版の受付箱なら、次の観測の同じキーの手の `relay stop` がハブへ `stop` を出し、`drain` がその要求を実行せずに取り消す（ラッパーを起こさない。ほかのリレーの操作を待たない） |
| 旧版の受付箱（`requests/`）にその名前の起動の要求が残る | 書かない（`starting`）。今の版の `drain` は旧版の受付箱を処理しないので、旧版の頼む側の `drain` が処理するまで返却中のまま数え続ける（上限を超えない側）。旧版の頼む側は応答待ちの間 `drain` を打ち直し、期限切れでは要求を消すので、残るのは旧版の頼む側が応答を待たずに落ちたときだけである。#4118 が版の混在の検出で扱う |
| 錠の中の tmux の照会が「ある」・不明 | 書かない（`starting`・`unknown`）。次の観測の同じキーの手の `relay stop` がセッションを止めてから確かめ直す |
| `move_seat` が照らして外れる | 書かない（`stale`。#4091 のとおり失敗に数えない） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 停止の確かめと、ハブの起動・置き換え・手動の `relay restart`（どの版の頼む側も） | そのレーンのラッパーと席 | 確かめ（一覧の読み直し・受付箱の読み・tmux のセッションの照会）と返却済みの書き込みを `hub.lock` の中で行う。ハブは `hub.lock` の中でだけ要求を処理する | 確かめから書き込みまでにラッパーは起きない。3 の `relay stop` の後・錠の前に処理された起動は、錠の中で一覧（動いている）か tmux の照会（錠を取らないセッションが残る）に出るので書かない。錠を持つ間に書かれた要求は書き込みの後に処理され、そのラッパーが錠を取った後は起動の許可とハブが動いているラッパーとして数える。書き込みの後に書かれる要求の窓は設計文書の決定 3 の代償 |
| 停止の確かめの 3 の `relay stop` と、受付箱に溜まった起動の要求 | ハブの受付箱 | `drain` が錠の中で要求の並びを読み、処理の前に `stop` より前に並ぶ同じ名前の起動の要求を取り消し、残りをファイル名の時刻の順に処理する | 同じ名前の古い起動の要求は起きずに取り消され、待つ頼む側は `cancelled` を受け取る。ほかの名前の要求は `stop` より先に処理される。中断しても、次の観測の同じキーの手が同じ順で打ち直す |
| 単名の停止と、別の `drain` が処理する同じ名前の起動 | そのラッパーのセッションと受付箱 | セッションと錠の判定を `stop_named` で `hub.lock` の中で行う。単名の経路（`_hub`）は錠の外で何も読まない（今のとおり） | 判定から成功を返すまでに起動は完了しない。判定より前に完了した起動はセッションか錠として見えて止まる。成功はセッションの消滅と錠の解放を錠の中で見たときだけ |
| 一括の停止（`relay stop --all`）と、ハブの競合 | 名前ごとの停止シグナルとハブ | 今のまま。名前ごとにシグナルを置き、錠の外の `has` が真の名前だけ `submit` する（tmux の外のラッパーはハブを待たない） | 待ちが累積するのは tmux のセッションを持つ名前の数だけで、今と変わらない。失敗に数えるのは、シグナルを置けない名前と `submit` が失敗した名前だけ（今のとおり）。停止の確かめは一括の経路を使わない |
| `submit` の応答待ちと、溜まったほかの名前の要求 | ハブの受付箱と `relay.hub-wait` の期限 | 自分で `drain` するときも、別のプロセスの `drain` を待つときも、結果を期限の内に読めたときだけ成功。自分の `drain` は期限を過ぎたら次の要求を取らない | 期限で処理されなかった自分の要求は消し、残ったほかの要求は次の `drain` が処理する。停止の確かめは `stop_failed` を受けて書かず、次の観測で打ち直す |
| 停止の確かめと、取り直しの起動（同じ行） | そのレーンのラッパー | 計画ごとに耐久ワークフローは 1 本で、手を 1 つずつ打つ。中断した手の子のプロセスが後から書く要求は、設計文書の決定 3 の代償 | 打ち直しは #4091 の同じ手の回復のとおり |
| 錠の順 | `admit.lock`・`hub.lock`・`lock_plan` | `_confirm_seat` はどの呼ぶ所でも `admit.lock` を持たずに打つ（取り直しの手順の 4・5 は `admitted` が返った後）。中の順は `hub.lock` → `lock_plan` だけ。`relay stop` はどの錠も持たずに打ち、`hold` の前に終わる（`hub.lock` を持ったまま `relay stop` を打たない） | ハブの `drain` は `admit.lock`・`lock_plan` を取らないので循環しない |
| 停止の確かめの 3 の `relay stop` と、ほかのレーン・計画の起動の許可 | 同じマシンの `admit.lock` | `relay stop` を `admit.lock` の外で打つ | `relay stop` の `drain` がほかのレーンの溜まった `spawn`・`replace`（1 件ごとに `_confirm` の `relay.started` の秒まで）を処理する間も、ほかの計画のレーンは `admit.lock` を取って起動の許可を進められる。長い `drain` で待つのはこの手だけで、自分の `drain` でも別のプロセスの `drain` でも、`submit` が `relay.hub-wait` の秒の内に結果を読めなければ `stop_failed`（止める手の失敗の 1 回）になる。自分の `drain` は期限を過ぎたら次の要求を取らないので、待つ長さは `relay.hub-wait` の秒に処理中の 1 件の分を足した長さまでである |

### 単体テストの観点

- `start_name`: `spawn`・`replace`・`restart` で名前があればその名前、`stop`・名前の無い `spawn`・名前が文字列でない要求は None
- `starting`: `requests-v2/` と `requests/` のどちらかに、同じ名前の `spawn`・`replace`・`restart` があれば True、`stop`・別の名前・名前の無い `spawn`・読めないファイルだけなら False。受付箱が無くても False。要求のファイルを消さない
- `session`: 終了コード 0 で True、`can't find session`・`no server running`・`error connecting to … (No such file or directory)` で False、`Permission denied`・知らない出力・`OSError`・時間切れで None。引数に `-L ndf-lanes` と `=<名前>` が載る
- `hold`: 別のプロセスがハブの `signals.held` で `hub.lock` を持つ間は None を渡し、放した後は取れる（同じファイルの `flock` で排他になることを確かめる）。取れたら `starting` の値を渡す
- 名前つきの停止のテストは、単名の経路（`relay.cli.main(["stop", <名前>])` か `launch.stop`）で打つ。`--all` の経路で代えない
- 照会と完了の競合（`relay stop` を成功させてラッパーを残さない）: 別のプロセスが `hub.lock` を持ったまま同じ名前の `replace` を処理してセッションを作り、要求のファイルを消してから錠を放す。その処理の間に単名の `relay stop` を打つと、`stop_named` が錠の中でそのセッションを見て止め、ラッパーは残らない（錠の外で判定して `submit` を打たずに成功を返す形にすると落ちる）
- `stop_named` の確かめ: ラッパーの錠を取らないセッションが残る名前で、`kill-session` が失敗してセッションが残る tmux（偽の runner）に単名の `relay stop` を打つと、`relay.stop-wait` の秒の後に `wait_timeout` の失敗を返す（錠が空いていることだけで成功を返すと落ちる）。照会が不明（時間切れ）のままでも失敗。kill が成功してセッションが消え、錠も空けば成功
- `submit` の期限: 今の版の受付箱に別の名前の `spawn` を 2 件置き（偽の `_confirm` で 1 件ごとに時計を進める）、`relay.hub-wait` をその 1 件分より長く 2 件分より短くして単名の `relay stop` を打つ。自分の `drain` は 1 件目を処理し、期限を過ぎたので 2 件目と `stop` を処理せず、`submit` は `wait_timeout` を返し、`stop` の要求のファイルは消え、2 件目は受付箱に残る（自分の `drain` を最後まで回して結果があれば成功を返すと落ちる）。自分の要求を処理し終えたのが期限の後なら、結果を捨てて `wait_timeout`。別のプロセスが `hub.lock` を期限を越えて持つ場合も `wait_timeout`。どの場合も停止の確かめは `stop_failed` で書かない
- 取り消し: 今の版の受付箱に同じ名前の `replace`（作業ディレクトリ・引数・席番号つき）と `restart` を置き、その後に `relay stop` を打つと、ラッパーは起きず、登録簿の記録も再起動の回数も変わらず、`replace`・`restart` を待つ頼む側は `ok: false`・`cause: cancelled` を受け取る（取り消さずに処理してから止めると、登録簿か回数が変わって落ちる）。`stop` の後ろに並ぶ同じ名前の起動の要求と、`stop` の前に並ぶ別の名前の起動の要求は今のとおり処理する。旧版の受付箱の要求は取り消さない
- `stop_named`: セッションがあれば止め、セッションの消滅と錠の解放の両方を見て成功。セッションが無く錠だけが持たれていれば止めずに錠が空くまで待ち、`relay.stop-wait` の秒で空かなければ `wait_timeout`（シグナルを置いたとして成功を返すと落ちる）。どちらも無ければ「動いていない」で成功
- `_confirm_seat`: 一覧が None なら書かない。取り直し中で動いていれば止めずに席あり。返却中で動いていれば書かずに `running`。動いていなければ `relay stop` の後に錠の中で読み直し、要求もセッションも無ければ返却済み。錠の中の一覧で動いていれば返却済みを書かない（`relay stop` の後・錠の前に手動の再起動を差し込む）
- 錠を持たない tmux のセッション（`_confirm` の期限切れの後を作る）が残る返却中の行で、`relay stop` がセッションを止め、その後に返却済みになる
- `relay stop` の後・`hold` の前に、ラッパーの錠を取らない tmux のセッションを作る（停止の後に再起動が入り `_confirm` が期限で切れた形）と、錠の中の一覧も受付箱も空でも、tmux の照会が True で書かずに `starting` になる。次の確かめの `relay stop` がセッションを止め、返却済みになる。照会が None なら書かずに `unknown`（照会を打たずに書くと落ちる）
- 中断で、そのレーンの `replace` の要求だけが今の版の受付箱に残り、tmux のセッションが無い返却中の行で、ほかのリレーの操作を挟まずに 1 回確かめると、`relay stop` の `drain` がその要求を実行せずに取り消し（ラッパーは起きない）、その 1 回で返却済みになる（残った要求を処理してから止めると、ラッパーが起き、登録簿の記録が変わって落ちる）。`relay stop` の後・`hold` の前に要求を置くと書かずに `starting` になり、次の確かめで返却済みになる
- `hold` を持つ間に別のプロセスが `relay spawn --replace` を打つと、その処理は返却済みの書き込みの後になる。上限 1 で、その後に起きたラッパーを、別の計画の行の起動の許可が数えて待つ
- 取り直しの手順の 4: ハブの断りの後に一覧で動いていれば書かずに `waiting`、動いていなければ止めて返却済みにし `waiting`。最初の一覧で動いておらず、`relay stop` の後・錠の前に手動の `relay restart` で起こしたラッパーが錠の中の一覧で動いていれば、T5 を書いて成功の結果を返す（`waiting` を返すと、席ありの行を取り直し中と扱って落ちる）。手順の 5: 起動の確かめの失敗・応答待ちの切れの後、動いていれば席ありで成功、動いていなければ止めて返却済みで失敗の 1 回
- 取り直しの手順の 4・5 の `_confirm_seat` の間（`relay stop` の `drain` がほかのレーンの要求を処理している間を含む）、別のスレッドが `machine.admit_lock` をすぐに取れる（`go` の中で手順 4・5 を打つと落ちる）
- `hub.py` の振る舞いは、`stop` の要求の扱い（取り消しと `stop_named`）と `submit` の期限（期限を過ぎて読んだ結果を捨てる・自分の `drain` が期限の後に次の要求を取らない）のほかは変わらない（今のハブのテストのうち、`stop` の要求にも期限をまたぐ待ちにも関わらないものはそのまま通る）。`relay.cli` の `_stop_one`・`_stop_all` の今のテストはそのまま通る。`src/ndf/progress` の下に `ndf.domains` の import が無く、構造の検査が通る
