# #4103 の詳細設計: 起動の券・迷子のセッションの印・一覧の起動の途中・期限を越えた続きの手

設計文書 [issue-4103-design.md](./issue-4103-design.md) の構成要素と「状態と事象の表」（R1〜R8・T2・T5'・T6・T7）を、ファイルと関数の粒度へ下ろした文書である。関数はどれも表の 1 行を実装し、表に無い遷移を書かない。パスは `plugins/ndf/` からの相対で書く。#4044 の実装（頼む側の `seats.read`・要求の `held`・ハブの和集合の数え方）と #4091 の実装（`Seat`・`move_seat`・`vacate_lane`・`settle_seat`・`launch_lane` の返却済みの分岐・`Vacate`・`Settle`）の上に乗る。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/causes.json                    # abandoned
├── docs/contracts/commands.md               # relay ls の starting・relay の --ticket・restart/spawn の起動の排他
├── src/ndf/core/launch_ticket.py*           # 券のパス・取得・判定・掃除
├── src/ndf/domains/relay/
│   ├── hub.py                               # submit・drain・_handle_file・_confirm・stop・spawn・restart・_over_cap・listing・strays*
│   ├── tmux.py                              # probe*・has
│   └── cli.py                               # --ticket・restart/spawn の頼む側の admit.lock
├── src/ndf/progress/lane/
│   ├── launch.py                            # _relay が券を取って子へ渡す・Presence*
│   └── liveness.py                          # wrapper_presence*
└── src/ndf/progress/plan/
    ├── run.py                               # settle_seat・vacate_lane・launch_lane の確かめ・observe・snapshot_of
    └── run_decide.py                        # Observe.expiring*・Snapshot.alive*・RunState.kept_alive*・settling*・_expire_launched・_notice_stalls・Settle の扱い
```

依存の向き: `core/launch_ticket` は `ndf.identity`・`ndf.contracts.facts` だけを読み、`progress` と `domains` の両方から読まれる。`progress/lane/launch.py` は今のとおりリレーを子のプロセス（`bin/ndf relay …`）で呼び、`domains/relay` を import しない。`domains/relay/cli.py` は `progress/lane/machine.admit_lock` を読む（上の層が下の層を読む。`src/ndf/checks/rules.json` の `layers`）。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/launch_ticket.py` | 新設 | 起動の券のパス・取得・判定・掃除を 1 か所に置く | `ndf.identity`・`ndf.contracts.facts` | `Ticket`（新設） | `new`・`take`・`adopt`・`directory`・`path`・`busy`・`held_names`・`sweep`（新設） |
| `src/ndf/domains/relay/hub.py` | 変更 | 券のある要求だけを処理し、確かめの失敗を片付け、一覧に起動の途中を載せる | `launch_ticket`・`tmux`・`registry` | — | `submit`・`drain`・`_handle_file`・`_confirm`・`stop`・`spawn`・`restart`・`_over_cap`・`listing`（変更）、`_strays`・`_mark_stray`・`_review_strays`・`_starting`（新設） |
| `src/ndf/domains/relay/tmux.py` | 変更 | セッションの有無を 3 値で返す | — | `Tmux` | `probe`（新設）・`has`（変更） |
| `src/ndf/domains/relay/cli.py` | 変更 | `--ticket` を受け、手動の `restart` と通常の `spawn` を起動の排他の中で頼む | `hub`・`seats`・`progress.lane.machine` | — | `_hub`（変更）・`_admitting`（新設） |
| `src/ndf/progress/lane/launch.py` | 変更 | 子のリレーを打つ前に券を取り、記述子と ID を渡す | `launch_ticket` | `Presence`（新設の定数の組） | `_relay`・`_run`（変更） |
| `src/ndf/progress/lane/liveness.py` | 変更 | 一覧の行から生存を 1 か所で決める | — | — | `wrapper_presence`（新設） |
| `src/ndf/progress/plan/run.py` | 変更 | 席の手と期限の生存を一覧の 3 つの生存で読む | `liveness`・`launch` | — | `vacate_lane`・`settle_seat`・`launch_lane`・`observe`・`snapshot_of`（変更）、`_confirm_seat`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 期限を越えた続きの手を生存で扱い、`Settle` を打ち切らない | — | `Observe`・`Snapshot`・`RunState` | `_observe`・`_expire_launched`・`_notice_stalls`・`_after_launch`（変更）、`_expiring`・`_tick_settle`（新設） |

## 起動の券（`core/launch_ticket`）

満たす決定: 決定 1、受け入れ条件 1・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Ticket` | `core/launch_ticket.py` | 新設（型） | `name: str`・`id: str`・`fd: int` | 持っている券 1 枚（ラッパーの名前・券の ID・共有の錠を持つ記述子） |
| `directory` | 同上 | 新設 | `(state_dir: Path, name: Optional[str]) -> Path` | `<状態の保存先>/<relay-hub の保存先>/tickets/<名前>`。名前が無ければ `-` |
| `path` | 同上 | 新設 | `(state_dir: Path, name: Optional[str], ticket_id: str) -> Path` | `directory(...) / f"{ticket_id}.lock"` |
| `take` | 同上 | 新設（文脈） | `(state_dir: Path, name: Optional[str]) -> ContextManager[Ticket]` | 新しい ID（`uuid4().hex`）でファイルを `O_CREAT | O_EXCL`・0o600 で作り、`LOCK_SH` を取って渡す。抜けるときに錠を解き、記述子を閉じ、ファイルを消す |
| `adopt` | 同上 | 新設 | `(state_dir: Path, name: Optional[str], ticket_id: str) -> Ticket` | 呼び手から渡された券（`--ticket`）を受ける。ファイルが無ければ `TicketError`。錠は取らない（記述子を受け継いでいる前提）。`fd` は -1 |
| `busy` | 同上 | 新設 | `(state_dir: Path, name: Optional[str], ticket_id: str) -> bool` | その券の錠を誰かが持っていれば真（`identity.lock_held`）。ファイルが無ければ偽 |
| `held_names` | 同上 | 新設 | `(state_dir: Path) -> frozenset[str]` | `tickets/` の下で、持たれた券を 1 つ以上持つ名前（`-` を除く） |
| `sweep` | 同上 | 新設 | `(state_dir: Path, older: float, now: Callable[[], float]) -> int` | 錠が空き、更新の時刻が `older` 秒より古い券のファイルを消し、空のディレクトリを消して数を返す |

- `take`
  - 事前条件: 要求のファイルを書く前に呼ぶ
  - 事後条件: `with` の中では `busy(...)` が真。抜けた後、この券を受け継いだ子が生きていれば真のまま、全員が閉じれば偽
  - 副作用: 券のファイルの作成と削除

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `take` | 作れない・錠を取れない | 保存先の権限・容量 | `os.open`・`flock` の `OSError` | 呼び出し元へ返す（要求を書かない） | 頼む側の結果 `ok: false`・`cause: precondition`、「起動の券を作れない: <理由>」 | 作れたファイルは消す。打ち直せる |
| `adopt` | 券のファイルが無い | 呼び手が券を取らずに `--ticket` を渡した・掃除された | `path.is_file()` | 呼び出し元へ返す（要求を書かない） | `ok: false`・`cause: invalid`、「渡された起動の券 <ID> が無い」 | 打ち直しは呼び手が新しい券で行う |
| `sweep` | 消せない | 権限 | `OSError` | 既定値で続ける（数えない） | 無し | 次の `drain` で再び試す |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 頼む側の `take` と `sweep` | 券のファイル | `sweep` は錠が空き、`relay.hub-wait` の秒の 2 倍より古いものだけを消す。`take` は作った直後に錠を取るので、作ってから錠を取るまでに古くならない | 万一作った直後に消されても、ハブは券のファイルが無い要求を持たれていないとして捨てる（安全側。頼む側は応答待ちが切れて打ち直す） |
| 頼む側・ハブの `LOCK_SH` と一覧の `busy` | 券の錠 | `busy` は `LOCK_EX | LOCK_NB` を一瞬だけ取る（`identity.lock_held`）。`take` とハブの `LOCK_SH` は待って取る（一瞬の `busy` を待つだけ） | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `take`・`busy` | `with` の中で真、抜けた後に偽でファイルが無い | 記述子を `pass_fds` で子へ渡し、親が抜けた後も子が生きている間は真、子が終わると偽 | 保存先に書けない |
| `held_names` | 持たれた券の名前だけ | 空いた券だけの名前・`-` を含まない | `tickets/` が無い |
| `sweep` | 空いた古い券だけを消す | 閾値ちょうど・持たれた古い券を消さない | 消せない |

## 頼む側（`hub.submit`・`cli`）

満たす決定: 決定 1・4・6、受け入れ条件 1・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `submit` | `domains/relay/hub.py` | 変更 | `(ctx: Context, request: Mapping[str, Any], ticket: Optional[str] = None) -> Outcome` | `ticket` があれば `adopt`、無ければ `take` で券を持ち、要求に `ticket` を載せて `requests-v3/<20 桁の時刻>-<券の ID>.json` に書き、今のとおり結果を待つ。結果を読むか諦めるまで券を持つ（R1・R2・R8）。旧版のハブの断りでの書き直し（`_unknown_to_older_hub`）は同じ券で新しいファイル名にする |
| `_hub` | `domains/relay/cli.py` | 変更 | 変えない | `--ticket` を受けて `submit` へ渡す。種類が `restart` か、`--replace` も `--seated` も無い `spawn` のときは `_admitting` の中で、#4044 の `seats.read` から `submit` の結果までを行う |
| `_admitting` | `domains/relay/cli.py` | 新設（文脈） | `() -> ContextManager[Optional[str]]` | `machine.admit_lock(limits.seconds(machine.LOCK_POLICY))` を取る。取れれば None を、`LockBusy` なら断りの文を渡す（呼び手は要求を書かずに `refused` を返す） |

- `submit`
  - 事前条件: `ticket` を渡すときは、呼び手がその券の錠を持ち、記述子をこのプロセスへ渡している
  - 事後条件: 戻ったときにこのプロセスは券の錠を持たない（受け継いだ記述子は閉じる）
  - 副作用: 券のファイル・要求のファイル・結果のファイル

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `submit` | 応答待ちが切れた | ハブの処理が `relay.hub-wait` を越えた | `_wait_until` | 呼び出し元へ返す | 今のとおり `wait_timeout` | 要求のファイルを消し、券を放す。処理中なら処理は続き、その間はハブが券を持つので一覧は `starting` を返す |
| `_hub`（`_admitting`） | 起動の排他を取れない | 耐久ワークフローの起動の許可か、別の手動の再起動が持っている | `LockBusy` | 呼び出し元へ返す（要求を書かない） | `refused`、「マシンの起動の排他を <秒> 秒の間に取れない。少し待って打ち直す」、終了コード 1 | 何も残らない。打ち直せる |
| `_hub` | `--ticket` が無い券 | 呼び手の誤り | `adopt` | 呼び出し元へ返す | `invalid` | 打ち直しは呼び手が新しい券で |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 手動の `restart`・通常の `spawn` と、起動の許可（行の開始・T4） | マシンの席 | 頼む側が `held` を読む前から結果まで `admit.lock` を持つ。錠の順は `admit.lock` → `hub.lock`（ハブの処理は `admit.lock` を取らない） | `held` を読んでから処理されるまでに数える席は増えない。頼む側が落ちれば `admit.lock` と券が同時に空き、要求は捨てられる |
| `--replace` の子と、親の `admitted` | `admit.lock` | 子は取らない（親が持つ） | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `submit` | 券を取り、`requests-v3` に `ticket` つきで書き、結果の後に券が空く | `ticket` を渡すと自分では取らず、子として打たれた形で親の券が残る | 応答待ちが切れても、処理中の要求は券が持たれたまま |
| `_hub` | `restart` と通常の `spawn` が `admit.lock` の中で `seats.read` と `submit` を打つ（呼ばれた順を縛る） | `--replace`・`--seated` は `admit.lock` を取らない（取ると親と取り合って止まる試験で落ちる） | 取れなければ要求のファイルが書かれない |
| 直列化 | 設計文書のテスト設計の受け入れ条件 3 の 2 つの順 | — | — |

## ハブの処理（`hub.drain`・`_confirm`・迷子の印・上限の判定・一覧）

満たす決定: 決定 1・2・3・6、受け入れ条件 1・2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drain` | `domains/relay/hub.py` | 変更 | 変えない | `hub.lock` を取れたら、`_review_strays` を打ち、`launch_ticket.sweep` を打ってから、`requests-v3` と `requests-v2` の要求をファイル名（先頭が時刻）の順に合わせて処理する |
| `_handle_file` | 同上 | 変更 | 変えない | `requests-v3` の要求は、読んだ `ticket` と `name` で `launch_ticket.busy` を見る。偽なら処理せず `Outcome(False, "頼む側がいない要求を処理しない", "abandoned")` を返す（R4。結果のファイルは書かず、要求を消し、記録に残す）。真なら券のファイルを開いて `LOCK_SH` を待って取り、処理の後（結果を書いた後）に放す（R3・R5・R6）。`requests-v2` の要求は今のとおり |
| `_confirm` | 同上 | 変更 | 変えない | 今の判定が通らなければ、`stop(ctx, name)` でセッションを止めて錠が空くのを待ち、その後の `ctx.tmux.probe(name)` が False でなければ `_mark_stray(ctx, name, <理由>)` を打ってから、失敗の結果を返す（R6）。結果の文に「セッションを止めた」か「迷子として数える」を足す |
| `stop` | 同上 | 変更 | 変えない | 「動いていない」の判定を `not lock_held and probe(name) is False` にする（不明は「ある」として `kill` へ進む）。`kill` の後、錠が空くのを待ち、`probe` が False なら印を消して ok、そうでなければ印を書いて ok（錠は空いた）を返す。錠が空かなければ今のとおり `wait_timeout` |
| `spawn` | 同上 | 変更 | 変えない | 対象の有無を `name in names or ctx.tmux.probe(name) is not False` で読む（不明は「ある」） |
| `_strays` | 同上 | 新設 | `(ctx: Context) -> frozenset[str]` | `strays/` の下のファイルの名前 |
| `_mark_stray` | 同上 | 新設 | `(ctx: Context, name: str, reason: str) -> None` | `strays/<名前>` に `{"at", "reason"}` を書き、記録（`hub-log.jsonl`）に 1 行残す |
| `_review_strays` | 同上 | 新設 | `(ctx: Context) -> None` | 印の名前ごとに、ラッパーの錠が持たれていれば印を消す（動いているラッパーとして数える）。`probe` が False なら消す。True なら `stop` を打つ（印は `stop` が決める）。None なら残す（R7） |
| `_over_cap` | 同上 | 変更 | 変えない | 数える名前を「動いているラッパー ∪ `_strays(ctx)` ∪ `held`」にする（対象の名前は除く）（U4） |
| `restart` | 同上 | 変更 | 変えない | `#4044` の和集合の数え方に `_strays(ctx)` を足す（対象の名前は除く） |
| `_starting` | 同上 | 新設 | `(ctx: Context) -> frozenset[str]` | 起動の途中の名前を、①`launch_ticket.held_names` ②`_strays` ③`requests-v2` の種類 `spawn`・`replace`・`restart` の要求の `name` の順に読んで合わせる |
| `listing` | 同上 | 変更 | 変えない | 先に `_starting(ctx)` を読み、その後で `registry.busy_names` を読む（U2。順を逆にしない）。行に `starting` と `stray`（`_strays` に名前がある）を足し、登録簿にも錠にも無く `starting` だけの名前も `registered: false` で載せる |

- `_handle_file`（券のある要求）
  - 事前条件: `hub.lock` を持っている
  - 事後条件: 処理した要求の結果と、要るなら迷子の印が、券を放す前に書かれている
  - 副作用: 券の錠（共有）・要求と結果のファイル・印・記録

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_handle_file` | 券が持たれていない | 頼む側が落ちた・諦めた | `launch_ticket.busy` | 処理しない | 記録の `abandoned`。頼む側はいないので結果は書かない | 要求のファイルを消す。二度と処理されない |
| `_handle_file` | 券のファイルを開けない | 掃除と重なった・権限 | `OSError` | 持たれていないと同じに扱う | 同上 | 同上（安全側） |
| `_confirm` | 止めた後も `probe` が False でない | tmux の不調・セッションが止まらない | `probe` | 迷子の印を書いて失敗を返す | 失敗の結果の文に「迷子として数える」 | 次の `drain` か `stop` で見直す |
| `_review_strays` | 照会が不明のまま | tmux の不調 | `probe` が None | 印を残す（数え続ける） | 無し（一覧の `starting`） | 次の `drain` で再び見る |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `stop` | 錠が空き・`probe` が False | 「動いていない」。印があれば消す |
| `stop` | 錠が空き・`probe` が True か None | `kill` → 錠が空くのを待つ → `probe` が False なら印を消して ok、そうでなければ印を書いて ok |
| `stop` | 錠が持たれている | `kill` → 錠が空くのを待つ（空かなければ `wait_timeout`）→ 上と同じく `probe` で印を決める |
| `_review_strays` | 錠が持たれている | 印を消す（以後は動いているラッパーとして数える） |
| `_review_strays` | 錠が空き・`probe` が False / True / None | 消す / `stop` / 残す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `REQUESTS_V3` | 定数 | `hub.py` | `"requests-v3"` |
| `STRAYS_DIR` | 定数 | `hub.py` | `"strays"` |
| 券の掃除の閾値 | 設定値（今の値から作る） | `hub.py` | `relay.hub-wait` の秒の 2 倍 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| ハブの処理と一覧（`relay ls`） | 券・印・ラッパーの錠 | 一覧は排他を取らず、券 → 印 → 錠の順に読む。ハブは印を書いてから券を放し、起動の確かめは錠が取られるまで待つ | 券が空いたと読めた後に読む印と錠には、その処理の結果が出ている（U2） |
| ハブの処理と頼む側の落ち | 券 | ハブは処理の番で券を見て、処理の間は共有で持つ | 番の時点で持たれていなかった要求は二度と処理されない。見た直後に頼む側が落ちても、ハブが `hub.lock` と券を持ったまま処理を終えるので、一覧は処理の終わりまで `starting` を返す |
| 迷子の印とハブの処理 | 印 | 書くのも消すのも `hub.lock` の中 | 何度見直しても同じ |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_handle_file` | `abandoned` の要求の ID と名前 | `hub-log.jsonl` | 要求の `args`（最初の入力） |
| `_mark_stray`・`_review_strays` | 印を書いた・消した名前と理由 | `hub-log.jsonl` | 環境の値 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_strays`・`_mark_stray` | `registry` の登録簿・`hub.json` | `hub.py`・`registry.py` を読んだ | 使えない（登録簿は登録の行で、起動していない名前を持たせると一覧と `restart` の意味が変わる。印は別のファイルにする） |
| `launch_ticket.busy` | `identity.lock_held` | `grep -n "def lock_held" src/ndf/identity.py` | 使う |
| `launch_ticket.take` | `signals.held`（排他の錠） | `grep -n "def held" src/ndf/domains/relay/signals.py` | 使えない（排他で取る形で、`domains` の下にあり `progress` から読めない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_handle_file` | 券が持たれた要求を処理し、処理の間は `busy` が真 | 番の直前に券を放すと `abandoned` で捨て、ラッパーを起こさない。`requests-v2` の要求は券を見ずに処理する | 券のファイルが無い |
| `_confirm` | 確かめの失敗でセッションを止め、`probe` が False なら印を書かない | 確かめの 15 秒の後に錠を取るラッパーが、止められて動いていない | 止めた後の `probe` が None・True で印が書かれる |
| `_over_cap`・`restart` | 印の名前を 1 本と数えて上限で断る | 印の名前と対象が同じなら数えない | — |
| `listing` | 持たれた券・印・`requests-v2` の要求の名前に `starting: true` | 券を読んだ後に処理を終わらせても（読む順の試験）、その処理が起こしたラッパーが `running` で読める | 錠を先に読むと落ちる |
| `_review_strays` | 処理の手順の表の 4 行 | — | — |

## tmux の照会（`tmux.probe`）

満たす決定: 決定 3、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `probe` | `domains/relay/tmux.py` | 新設 | `(name: str) -> Optional[bool]` | `has-session -t =<名前>` を打つ。終了コード 0 なら True。終了コード 1 で出力が `can't find session`・`no server running` を含むか、`error connecting to` と `No such file or directory` を含めば False。`OSError`・時間切れ・それ以外は None |
| `has` | 同上 | 変更 | 変えない | `probe(name) is True`。ハブの中で有無を読む所は `probe` を読み、`has` は今のほかの呼び手のためだけに残す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `probe` | 終了コード 0 で True、`can't find session: x` で False、`error connecting to /tmp/tmux-1000/s (No such file or directory)` で False | `error connecting to … (Permission denied)` で None | `OSError`・`TimeoutExpired`・終了コード 1 で知らない出力は None |

## 起動の手（`progress/lane/launch`）

満たす決定: 決定 1、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_relay` | `progress/lane/launch.py` | 変更 | `(argv: list[str], run: Optional[Any], failure: str, name: str) -> dict[str, Any]` | `launch_ticket.take(state_dir, name)` の中で、`argv` に `--ticket <ID>` を足し、`_run(argv, run, pass_fds=(ticket.fd,))` で子を打つ。子が戻ったら券を放す（`spawn`・`replace`・`stop` が呼ぶ） |
| `_run` | 同上 | 変更 | `(argv: list[str], run: Optional[Any], pass_fds: tuple[int, ...] = ()) -> CompletedProcess[str]` | `pass_fds` を `subprocess.run` へ渡す |
| `Presence` | 同上 | 新設（定数） | `RUNNING = "running"`・`STARTING = "starting"`・`ABSENT = "absent"`・`UNKNOWN = "unknown"` | 生存の値の名前 |

- `_relay`
  - 事前条件: 状態の保存先が `identity.load()` で読める
  - 事後条件: 戻ったとき、親は券を持たない。子が生きていれば券は子が持つ
  - 副作用: 券のファイル・子のプロセス

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_relay` | 券を作れない | 保存先 | `OSError` | 呼び出し元へ返す | `{"ok": false, "detail": "<failure>: 起動の券を作れない: <理由>"}` | 何も残らない。手の失敗として数える |
| `_relay` | 子が `--ticket` を知らない | 子が古い導入 | 子の終了コード（引数の誤り） | 呼び出し元へ返す | 今の `read_spawn` の失敗 | 要求は書かれていない（不在） |

## 生存の判定と席の手（`liveness`・`plan/run`）

満たす決定: 決定 2・3、受け入れ条件 1・2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `wrapper_presence` | `progress/lane/liveness.py` | 新設 | `(table: Optional[Mapping[str, Mapping[str, Any]]], name: str) -> str` | `table` が None なら `UNKNOWN`、行の `wrapper_running` が真なら `RUNNING`、行の `starting` が真なら `STARTING`、それ以外（行が無いを含む）は `ABSENT`。席の手・観測・期限の判定はこの関数だけを読む |
| `_confirm_seat` | `progress/plan/run.py` | 新設 | `(conn, row: LaneRow, sprint: str, seq: int, cause_absent: str) -> dict[str, Any]` | `launch.wrappers()` を 1 回読み、`wrapper_presence` で分ける。取り直し中: `RUNNING` → `move_seat(to=SEATED, cause=present)`、`ABSENT` → `move_seat(to=VACATED, cause=cause_absent)`。返却中: `ABSENT` → `move_seat(to=VACATED, cause=absent)`、`RUNNING` → 書かない。`STARTING`・`UNKNOWN` はどちらも書かない。返すのは書いた遷移か、書かなかった理由（`present`・`vacated`・`starting`・`unknown`・`stale`） |
| `settle_seat` | 同上 | 変更（#4091 の新設） | 変えない | 確かめの手順の 2・3 を `_confirm_seat(..., cause_absent="absent")` にする。`starting` で、その名前の行が迷子の印だけ（券も旧い要求も無い。一覧の行に `stray: true` を足して見分ける）なら `launch.stop` を 1 回打つ（ハブが印を見直す）。結果は下の「`Settle` の結果」の表 |
| `vacate_lane` | 同上 | 変更（#4091 の新設） | 変えない | 返却の手順の 4 を、`launch.stop` の後の `_confirm_seat(..., cause_absent="stopped")` にする（`hub.lock` を取らない） |
| `launch_lane` | 同上 | 変更（#4091） | 変えない | 取り直しの手順の 4・5 を下の「起動の後の確かめ」の表にする。手順 6・7（同じ手の回復）は #4091 のまま、確かめの部分だけ `_confirm_seat` を読む |

起動の後の確かめ（取り直しの手順の 4・5。`launch.replace` が戻り、券を放した後）:

| 起動の結果 | 一覧の生存 | 書くもの | 返す結果 | 判断の扱い |
| --- | --- | --- | --- | --- |
| 成功 | — | T5'（`launched`） | 成功 | `launched` に置く |
| ハブの断り（`waiting`） | `RUNNING` | 席あり（`present`） | `waiting` | 数えない。次の観測で席ありの行の続きの手が置き換える |
| ハブの断り | `ABSENT` | T6（`refused`） | `waiting` | 数えない |
| 断り以外の失敗 | `RUNNING` | 席あり（`present`） | 成功 | 起動できたとして扱う（応答待ちの切れの後にハブが起こした） |
| 断り以外の失敗 | `ABSENT` | T6（`launch_failed`） | 失敗 | 手のキーで 1 回数える |
| どちらでも | `STARTING`・`UNKNOWN` | 書かない（取り直し中のまま） | 断りなら `waiting`、それ以外は失敗 | 失敗は 1 回数える。続きは `Settle` |

`Settle` の結果:

| 書いたもの・理由 | 返す結果 | 判断の扱い |
| --- | --- | --- |
| 書いた（`present`・`vacated`） | `{"ok": true}` | 終わり（次の観測で写しから消える） |
| `starting`・`unknown` | `{"ok": true, "pending": true}` | `_tick_settle` で回数を数える |
| `stale` | `{"ok": false, "stale": true}` | 失敗に数えない（#4091 のまま） |

### 処理の手順

- 繰り返しの終わる条件: `Settle` は券が空き印が消えた時点で必ず `RUNNING` か `ABSENT` を読む。券は頼む側が結果を受け取るか `relay.hub-wait` で諦めるか落ちると空き、印は tmux が答えると消える

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 席の手（T2・T6・T7）と、ハブが処理する起動 | そのレーンのラッパーと席 | 席の手は `lock_plan` だけを取る。一覧を読んだ後・書く前にその名前で新しく券を取れるのは、同じ計画の耐久ワークフロー（席の手と順に打つ）と `admit.lock` を持つ手動の `restart`・通常の `spawn` だけ（U3） | 書いた後に手動の再起動で起きたラッパーは、起動の許可とハブが動いているラッパーとして数える（#4091 の S1）。席の手は打ち直しても同じ一覧で同じ結果を書く |
| 取り直しの手の打ち直しと、生き残った子 | 券 | 親が落ちても子が券を持つ | 打ち直しは `RESEATING` の行で起動せず `_confirm_seat` だけを打つ（#4091 の S6）。子が生きている間は `STARTING` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `wrapper_presence` | 4 つの値 | `running` と `starting` が両方真の行は `RUNNING`、行が無いと `ABSENT` | 一覧が None で `UNKNOWN` |
| `_confirm_seat` | 段階と生存の組み合わせの表どおりに書く | 一覧に `registered: true, running: false` の行だけが載るレーンは `ABSENT` | `STARTING`・`UNKNOWN` で書かない |
| `launch_lane` | 起動の後の確かめの表の 6 行 | 応答待ちが切れた後にハブが起こした（一覧 `RUNNING`）を成功にする | 断りを数えると落ちる |
| `settle_seat` | 親と子が落ちた要求の後で `ABSENT` → 返却済み | 親だけが落ちて子が生きている間は `pending` | 迷子の印だけのとき `stop` を 1 回打つ |

## 期限を越えた続きの手と `Settle`（`plan/run_decide`）

満たす決定: 決定 5、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Observe.expiring` | `plan/run_decide.py` | 新設（欄） | `tuple[str, ...]`（既定は空） | この観測の後の `_expire_launched` で期限に達する続きの手（`Launch.continues` が真）のレーン |
| `_expiring` | 同上 | 新設 | `(state: RunState, policy: Policy) -> tuple[str, ...]` | `launched` の続きの手のうち、`waits` の回数 + 1 で `_tick_waits` と同じ式（`n * poll >= up`）を満たすもののレーン。止める手・行を始める手は載せない |
| `_observe` | 同上 | 変更 | 変えない | `Observe(spent, expiring=_expiring(state, policy))` |
| `observe` | `plan/run.py` | 変更 | `(spec: dict, spent: Sequence[str] = (), expiring: Sequence[str] = ()) -> dict` | `expiring` を `snapshot_of` へ渡す。`_perform` が `observe(spec, list(act.spent), list(act.expiring))` で打つ |
| `snapshot_of` | `plan/run.py` | 変更 | `(view, rows, found=None, moved=None, spent=(), expiring: Sequence[str] = ()) -> dict` | `expiring` が空でなければ一覧を読む（`judge_lanes`・#4091 の `needs_table` と同じ 1 回を共有する）。写しの `alive` に、`expiring` の各レーンの `wrapper_presence` を載せる |
| `Snapshot.alive` | `plan/run_decide.py` | 新設（欄） | `list[Any]`（`[レーン, 生存]` の並び。既定は空） | 期限に達する続きの手のレーンの生存 |
| `RunState.kept_alive` | 同上 | 新設（欄） | `tuple[str, ...]` | 期限を越えたが `RUNNING` なので `launched` に残した続きの手のキー。キーが `launched` から外れたら同時に外す |
| `RunState.settling` | 同上 | 新設（欄） | `tuple[tuple[str, int], ...]` | `Settle` のキーと、`pending` が続いた観測の回数 |
| `_expire_launched` | 同上 | 変更 | 変えない | 期限切れに分かれた続きの手を、写しの `alive` のそのレーンで分ける。`RUNNING`: 数えず置き換えず、`launched` と `waits`（回数は据え置き）に残し、`kept_alive` に積む。`ABSENT`: 今のとおり `_fail` で 1 回数え、`kept_alive` から外す（候補に戻り同じキーで打ち直す）。`STARTING`・`UNKNOWN`・載っていない: `launched` と `waits` を据え置く。止める手・行を始める手は今のとおり |
| `_notice_stalls` | 同上 | 変更 | 変えない | `relaunching`（`/goal` の入れ直しを待つレーン）から、`kept_alive` のキーを持つレーンを外す |
| `_after_launch` | 同上 | 変更 | 変えない | `Settle` の結果は `launched`・`started`・`history`・`_fail` に入れない。`pending` なら `_tick_settle`、書いたら `settling` からキーを外す。`stale` は `tried` に置くだけ |
| `_tick_settle` | 同上 | 新設 | `(state: RunState, act: Settle, policy: Policy) -> RunState` | `settling` のキーの回数を 1 つ進め、`n * poll >= up` に初めて達したら、行の段階で今の知らせ（取り直し中は `LAUNCH_FAILED`、返却中は `HALT_FAILED`）を `unreported` に置き、キーを `noticed` に積む（2 回目を出さない）。`failed`・`final` には置かない |

- `_expire_launched`
  - 事後条件: `kept_alive` ⊆ `launched`。`RUNNING` の続きの手は失敗に数えられず、候補（`_candidates`）にも出ない
- `Settle` の候補: `Settle` は `launched`・`failed` に入らないので、写しの `settles` に載る限り観測ごとに `_candidates` に出る（`tried` は観測ごとに空く）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `snapshot_of` | 一覧を読めない | `relay ls` の失敗 | `launch.wrappers()` が None | 既定値で続ける（`alive` を `UNKNOWN`） | 無し | 次の観測で読み直す。期限切れを決めない |
| `_tick_settle` | 長く起動の途中 | 子が待ち続ける・tmux の不調 | 回数 | 知らせを 1 回出して打ち続ける | 計画のリクエスト（`launch_failed`・`halt_failed`） | 券が空き印が消えた観測で書く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_expiring`・`observe`・`snapshot_of` | 期限に達する続きの手のレーンだけが `expiring` に載り、写しの `alive` に生存が載る | 止める手・行を始める手・期限前の手は載らない。`judge_lanes` が一覧を要らず、返却中・取り直し中の行も `expiring` も無い観測では `relay ls` を打たない。載れば 1 回だけ | 一覧が None で `UNKNOWN` |
| `_expire_launched` | `RUNNING` で数えず `kept_alive` に載る、`ABSENT` で 1 回数えて打ち直す | `STARTING`・`UNKNOWN` で回数が据え置き | 期限だけで数えると落ちる |
| `_notice_stalls` | `kept_alive` のレーンが写しの `stalls` にあると、止まりの知らせが 1 回出る | 2 回目は出ない | `relaunching` から外さないと、`/goal` の入れ直しも知らせも出ずに落ちる |
| `_tick_settle` | `pending` が `up` の秒を越えると知らせが 1 回、その後も `Settle` が出て、券が空けば書く | ちょうど閾値 | `Settle` を `failed`・`final` に置くと落ちる |
