# #4103 の詳細設計: 起動の券・起動の印・一覧の起動の途中・期限を越えた続きの手

設計文書 [issue-4103-design.md](./issue-4103-design.md) の構成要素と「状態と事象の表」（R1〜R13・T2・T5'・T6・T7）を、ファイルと関数の粒度へ下ろした文書である。関数はどれも表の 1 行を実装し、表に無い遷移を書かない。パスは `plugins/ndf/` からの相対で書く。この文書は次の 2 つの実装の上に乗る。

- #4044 の実装: 頼む側の `seats.read`・要求の `held`・ハブの和集合の数え方
- #4091 の実装: `Seat`・`move_seat`・`vacate_lane`・`settle_seat`・`launch_lane` の返却済みの分岐・`Vacate`・`Settle`

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/causes.json                    # abandoned・stray
├── contracts/relay-protocol.json*           # {"inbox": 3}（子の版の対応の宣言）
├── contracts/domains/env.json               # NDF_RELAY_LAUNCH_GEN（secret: false。new-session -e で渡す）
├── docs/contracts/commands.md               # relay ls の starting と名前の無い起動の予約の行、relay の --ticket・--ticket-fd・--launch-gen・--stray、stop の成功の条件
├── src/ndf/core/launch_ticket.py*           # 名前と券の ID の検証・券の取り方と試し方・起動の印・掃除・対応の宣言
├── src/ndf/domains/relay/
│   ├── hub.py                               # submit・drain・_handle_file・_launch・_confirm・_await_exit・stop・spawn・restart・_over_cap・listing・_review_launches*・_starting*
│   ├── tmux.py                              # probe*・sessions*・has
│   ├── cli.py                               # --ticket・--ticket-fd・--launch-gen・OWN_OPTIONS・restart/spawn の頼む側の admit.lock
│   ├── wrapper.py                           # run の始めで印の世代を照らし、子の終わりを確かめた終わりで自分の世代の印を消す
│   ├── signals.py                           # NAME_RE は launch_ticket を参照
│   └── registry.py                          # wrapper_dir は launch_ticket.check_name を使う
├── src/ndf/progress/lane/
│   ├── launch.py                            # _relay が子の版を確かめ、券を取って子へ渡す・Presence*
│   ├── machine.py                           # count が starting の名前を数える
│   └── liveness.py                          # Seen*・wrapper_seen*・wrapper_presence*
└── src/ndf/progress/plan/
    ├── run.py                               # settle_seat・vacate_lane・launch_lane の確かめ・observe・snapshot_of
    └── run_decide.py                        # Observe.expiring*・Snapshot.alive*・RunState.kept_alive*・settling*・_expire_launched・_candidates・_notice_stalls・Settle の扱い
```

依存の向き:

- `core/launch_ticket` は `ndf.identity`・`ndf.contracts.facts` だけを読み、`progress` と `domains` の両方から読まれる
- `progress/lane/launch.py` は今のとおり、リレーを子のプロセス（`bin/ndf relay …`）で呼び、`domains/relay` を import しない
- `domains/relay/cli.py` は `progress/lane/machine.admit_lock` を読む（上の層が下の層を読む。`src/ndf/checks/rules.json` の `layers`）
- 循環は無い

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/launch_ticket.py` | 新設 | 名前と券の ID の検証、券と起動の印のパス・取り方・試し方・掃除、対応の宣言を 1 か所に置く | `ndf.identity`・`ndf.contracts.facts` | `Ticket`・`Launch`・`TicketError`（新設） | `check_name`・`check_id`・`req_path`・`hub_path`・`take`・`adopt`・`release`・`held`・`processing`・`ticket_names`・`unnamed_ids`・`launch_path`・`read_launch`・`write_launch`・`checked_launch`・`drop_launch`・`launch_names`・`sweep`・`supports`（新設） |
| `src/ndf/domains/relay/hub.py` | 変更 | 生存の錠を照らした要求だけを処理し、起動の前に印を書き、不在を確かめて印を消し、一覧に起動の途中を載せる | `launch_ticket`・`tmux`・`registry` | — | `submit`・`drain`・`_handle_file`・`_launch`・`_confirm`・`_await_exit`・`stop`・`spawn`・`restart`・`_over_cap`・`listing`・`spawn_env`（変更）、`_review_launches`・`_starting`（新設） |
| `src/ndf/domains/relay/tmux.py` | 変更 | セッションの有無を 3 値で返し、印の見直しのために名前の一覧を 1 回で返す | — | `Tmux` | `probe`・`sessions`（新設）・`has`（変更） |
| `contracts/domains/env.json` | 変更 | `NDF_RELAY_LAUNCH_GEN` を秘密でない名前として宣言し、`new_session_args` が `-e` に載せる | — | — | 行を 1 つ足す |
| `src/ndf/domains/relay/cli.py` | 変更 | `--ticket`・`--ticket-fd`・`--launch-gen`・`--stray` を受け、手動の `restart` と通常の `spawn` を起動の排他の中で頼む | `hub`・`seats`・`launch_ticket`・`progress.lane.machine` | — | `_hub`・`_parser`・`OWN_OPTIONS`（変更）・`_admitting`（新設） |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 錠を取った後に印の世代を照らし、子の終わりを確かめて終わるとき、錠を持ったまま自分の世代の起動の印を消す | `launch_ticket` | `Wrapper` | `run`（変更） |
| `src/ndf/domains/relay/signals.py`・`registry.py` | 変更 | 名前の形を `launch_ticket` の 1 か所から読む | `launch_ticket` | — | `NAME_RE`・`wrapper_dir`（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | 子の版を確かめ、子のリレーを打つ前に券を取り、記述子と ID を渡す。計画の停止に印の世代を添える | `launch_ticket` | `Presence`（新設の定数の組） | `_relay`・`_run`・`_spawn_argv`・`stop`（変更） |
| `src/ndf/progress/lane/machine.py` | 変更 | 起動の許可が、一覧の `starting` の名前を未確定の起動として数える | `liveness` | — | `count`（変更） |
| `src/ndf/progress/lane/liveness.py` | 変更 | 一覧から生存・`starting` の出所・印の世代を 1 か所で決める | — | `Seen`（新設の型） | `wrapper_seen`・`wrapper_presence`・`seen_of`（新設） |
| `src/ndf/progress/plan/run.py` | 変更 | 席の手と期限の生存を一覧の生存で読み、`Settle` は同じ観測の写しの生存を使う | `liveness`・`launch` | — | `vacate_lane`・`settle_seat`・`launch_lane`・`observe`・`snapshot_of`（変更）、`_confirm_seat`（新設） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 期限を越えた続きの手を生存で扱ってレーン単位で抑え、`Settle` を打ち切らない | — | `Observe`・`Snapshot`・`RunState` | `_observe`・`_expire_launched`・`_candidates`・`_notice_stalls`・`_after_launch`（変更）、`_expiring`・`_held_lanes`・`_tick_settle`（新設） |

## 起動の券と起動の印（`core/launch_ticket`）

満たす決定: 決定 1・3・6、受け入れ条件 1・3・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `NAME_RE`・`ID_RE` | `core/launch_ticket.py` | 新設（定数） | `NAME_RE = ^(?!\.{1,2}$)[A-Za-z0-9._-]{1,64}$`・`ID_RE = ^[0-9a-f]{32}$` | 名前と券の ID の形の正本。`signals.NAME_RE` はこれを参照する |
| `check_name` | 同上 | 新設 | `(name: Optional[str]) -> str` | None なら `UNNAMED`（`"@unnamed"`）。形が `NAME_RE` に合わなければ `TicketError`。パスを作る前にどの入口も呼ぶ |
| `check_id` | 同上 | 新設 | `(ticket_id: str) -> str` | `ID_RE` に合わなければ `TicketError` |
| `req_path`・`hub_path` | 同上 | 新設 | `(state_dir: Path, name: Optional[str], ticket_id: str) -> Path` | `check_name`・`check_id` を通した後の `<relay-hub>/tickets/<名前>/<ID>.req`・`.hub` |
| `Ticket` | 同上 | 新設（型） | `name: str`・`id: str`・`fd: int` | このプロセスが持つ生存の錠の記述子 1 つ |
| `take` | 同上 | 新設 | `(state_dir: Path, name: Optional[str]) -> Ticket` | 新しい ID で `.req` を `O_CREAT|O_EXCL`・0o600 で作り、`LOCK_EX` を待って取る（R1）。取った後に `fstat` の装置と inode がパスの `stat` と違えば（掃除と重なった）、閉じて新しい ID で 1 回だけ取り直す |
| `adopt` | 同上 | 新設 | `(state_dir: Path, name: Optional[str], ticket_id: str, fd: int) -> Ticket` | 受け継いだ記述子を受ける。次の 2 つを確かめ、違えば `TicketError`。錠は取り直さない<br>・`fstat(fd)` の装置と inode が `req_path` の `stat` と同じ<br>・`held(req_path)` が真 |
| `release` | 同上 | 新設 | `(ticket: Ticket) -> None` | 記述子を閉じる（R11）。`LOCK_UN` は打たず、ファイルも消さない |
| `held` | 同上 | 新設 | `(path: Path) -> bool` | 観測者の試し。ファイルを読み取りで開き、`LOCK_SH|LOCK_NB` が `EWOULDBLOCK` なら真。取れたらすぐ閉じて偽。ファイルが無ければ偽。排他を取らない |
| `processing` | 同上 | 新設（文脈） | `(state_dir: Path, name: Optional[str], ticket_id: str) -> ContextManager[bool]` | ハブだけが呼ぶ（R3・R4）。`.hub` を開き（無ければ作り）、`LOCK_EX` を待って取る。続けて `held(req_path)` を試し、その真偽を渡す。抜けるときは、錠を持ったまま `.hub` を消してから閉じる |
| `ticket_names` | 同上 | 新設 | `(state_dir: Path) -> frozenset[str]` | `tickets/` の下の名前ごとに、券ごとに `held(.req)`、続けて `held(.hub)` を試し、どちらかが真の券を持つ名前。`check_name` に通らないディレクトリ・`check_id` に通らないファイルは読まない。`@unnamed` は `unnamed_ids` が読む |
| `unnamed_ids` | 同上 | 新設 | `(state_dir: Path) -> frozenset[str]` | `tickets/@unnamed/` の下で、`held(.req)` か `held(.hub)` が真の券の ID（`check_id` に通るものだけ）。一覧が名前の無い起動の予約の行を作る |
| `Launch` | 同上 | 新設（型） | `gen: str`・`ticket: Optional[str]`・`at: float`・`reason: str`・`checked: float` | 起動の印の中身。`ticket` は印を書いた処理の要求の券の ID |
| `launch_path` | 同上 | 新設 | `(state_dir: Path, name: str) -> Path` | `check_name` を通した後の `<relay-hub>/launches/<名前>` |
| `read_launch` | 同上 | 新設 | `(state_dir: Path, name: str) -> Optional[Launch]` | 印を読む。無ければ None。壊れていれば、世代を空にした `Launch` を返す（印はあるとして数える） |
| `write_launch` | 同上 | 新設 | `(state_dir: Path, name: str, reason: str, now: float, ticket: Optional[str] = None) -> Launch` | 新しい世代（`uuid4().hex`）で印を一時ファイルと `os.replace` で書く（R5）。処理中の要求の券の ID を `ticket` に載せる。ハブだけが `hub.lock` の中で呼ぶ |
| `checked_launch` | 同上 | 新設 | `(state_dir: Path, name: str, now: float) -> None` | 世代を変えずに `checked` だけを書き直す（R9 の照会の時刻）。印が無ければ何もしない。`hub.lock` の持ち主だけが呼ぶ |
| `drop_launch` | 同上 | 新設 | `(state_dir: Path, name: str, gen: Optional[str]) -> bool` | `gen` が None ならそのまま消す（`hub.lock` の持ち主。照会 → 錠の順に確かめた後だけ呼ぶ）。`gen` があれば、今の世代が同じときだけ消す（ラッパー。R10）。消したら真 |
| `launch_names` | 同上 | 新設 | `(state_dir: Path) -> frozenset[str]` | `launches/` の下で `check_name` に通る名前 |
| `sweep` | 同上 | 新設 | `(state_dir: Path, older: float, now: Callable[[], float]) -> int` | 更新の時刻が `older` 秒より古い `.req`・`.hub` ごとに、`LOCK_EX|LOCK_NB` を取れたものだけを錠を持ったまま消し、閉じる（R12）。空のディレクトリを消して数を返す |
| `supports` | 同上 | 新設 | `(root: Path) -> bool` | `<root>/contracts/relay-protocol.json` の `inbox` が 3 以上なら真。無い・読めない・形が違えば偽 |

- `take`・`adopt`・`release`
  - 事前条件: 要求のファイルを書く前に `take` か `adopt` を呼ぶ
  - 事後条件: 券を持つ間、`held(req_path)` は真。`release` の後も、この記述子を受け継いだ親か子が生きていれば真のまま。全員が閉じれば偽で、ファイルは残る（消すのは `sweep` だけ）
  - 副作用: `take` は券のファイルを作る
- `processing`
  - 事前条件: `hub.lock` を持っている
  - 事後条件: `with` の中では `held(hub_path)` が真。抜けた後、`.hub` は無い。抜ける前に、呼び手は結果と印を書き終えている

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check_name`・`check_id` | 形が違う | 絶対パス・`..`・区切り・誤った ID | 正規表現 | `TicketError` を呼び出し元へ返す（ファイルに触れない） | `ok: false`・`cause: invalid`、「名前（券の ID）の形が違う: <値>」 | 何も残らない |
| `take` | 作れない・錠を取れない | 保存先の権限・容量 | `os.open`・`flock` の `OSError` | 呼び出し元へ返す（要求を書かない） | `ok: false`・`cause: precondition`、「起動の券を作れない: <理由>」 | 作れたファイルは残る（`sweep` が消す）。打ち直せる |
| `adopt` | 券のファイルが無い・記述子が別のファイルを指す・錠が持たれていない | 呼び手の誤り・掃除された | `stat`・`fstat`・`held` | 呼び出し元へ返す（要求を書かない） | `ok: false`・`cause: invalid`、「渡された起動の券 <ID> を受けられない: <理由>」 | 打ち直しは呼び手が新しい券で行う |
| `processing` | `.hub` を作れない | 権限・容量 | `OSError` | 生存の錠を試さず、真偽を偽として渡す | 呼び手（ハブ）が `abandoned` にする | 安全側。頼む側は応答待ちが切れて打ち直す |
| `write_launch` | 書けない | 権限・容量 | `OSError` | 呼び出し元へ返す | ハブがセッションを作らずに `precondition` を返す | 打ち直せる |
| `sweep` | 消せない | 権限 | `OSError` | 既定値で続ける（数えない） | 無し | 次の `drain` で再び試す |
| `supports` | 読めない | 古い導入・壊れた導入 | `OSError`・JSON の誤り | 偽を返す | 呼び手（起動の手）が起動の失敗にする | 子を打たないので何も残らない |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 頼む側の `take` と `sweep` | `.req` | `sweep` は、古く、かつ `LOCK_EX|LOCK_NB` を取れたものだけを消す。`take` は作った直後に錠を待って取る | 作った直後・錠の前に消されても、`take` が inode の照合で気付いて取り直す |
| 頼む側の親と子 | `.req` の open file description | 親が排他で取り、`pass_fds` で子へ渡す。どちらも `LOCK_UN` を打たず閉じるだけ | どちらかが生きていれば錠が残る |
| 観測者（一覧・ハブの試し・`adopt`）と、持ち主 | `.req`・`.hub` | 観測者は `LOCK_SH|LOCK_NB` を一瞬取るだけ。排他は持ち主だけが取る | 観測者どうしは互いを「持たれている」と読まない。持ち主の `LOCK_EX` の待ちは、観測者の一瞬の共有の錠を待つだけ |
| ハブの `processing` と頼む側の落ち | `.hub`・`.req` | ハブは処理の錠を取ってから生存の錠を試す | 試しの前に落ちれば捨てる。試しの後に落ちれば処理を終える。どちらの間も一覧は処理の錠で `starting` を読む |
| ハブの `drop_launch` とラッパーの `drop_launch` | 印 | ハブ（と一覧）は `hub.lock` の中、ラッパーは自分の錠の中で世代を照らす | ハブが新しい世代を書くのは、その名前の `stop` が成功した後（`_await_exit` は錠の有無を問わず `stop` を打つ）なので、古いラッパーが新しい印を消さない |
| `hub.lock` の持ち主の `drop_launch` と、遅れて起きたラッパーの `read_launch` | 印・ラッパーの錠 | 消す側は tmux の不在 → ラッパーの錠の順に読む。ラッパーは錠を取った後に印を読む | 消す前に印を読んだラッパーは錠を持つので、消す側は錠で気付いて消さないか、消しても `running` で数えられる。消した後に印を読んだラッパーは動かない（U10） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check_name`・`check_id` | 形の合う値を返す | `.`・`..`・64 字・65 字・`@unnamed` を名前として渡す | 絶対パス・`a/b`・`../x`・31 桁の ID で `TicketError` |
| `take`・`release`・`held` | `take` の後に真、`release` の後に偽でファイルは残る | 次の 2 つを縛る<br>・記述子を `pass_fds` で子へ渡すと、親が `release` した後も子が生きている間は真、子が終わると偽<br>・親が先に `release` しても子の錠は解けない（`LOCK_UN` を打つと落ちる） | 保存先に書けない |
| `adopt` | 受け継いだ記述子で受ける | 別の券の `.req` を開いた記述子を渡すと `TicketError` | ファイルが無い・錠が空いている |
| `held` | 排他の持ち主がいれば真 | 2 つの観測者が同時に試しても、どちらも偽を返す（持ち主がいないとき） | ファイルが無い |
| `processing` | 生存の錠が持たれていれば真を渡し、抜けた後に `.hub` が無い | 処理の錠を取った後・試しの前に頼む側を止めると偽を渡す | `.hub` を作れない |
| `ticket_names`・`unnamed_ids` | 持たれた券の名前・名前の無い券の ID だけ | 生存の錠が空いて処理の錠だけ持たれた券を含む。`ticket_names` は `@unnamed` と形の違うディレクトリを含まない | `tickets/` が無い |
| `write_launch`・`checked_launch`・`drop_launch` | 書くたびに世代が変わり、`ticket` が載る。`checked_launch` は世代を変えない | 世代の違う `drop_launch` は消さない | 書けない |
| `sweep` | 空いた古い券だけを消す | 閾値ちょうど・持たれた古い券を消さない・子だけが持つ古い券を消さない | 消せない |
| `supports` | `inbox` が 3 で真 | 2 で偽 | ファイルが無い・壊れている |

## 頼む側（`hub.submit`・`cli`）

満たす決定: 決定 1・4・6、受け入れ条件 1・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `submit` | `domains/relay/hub.py` | 変更 | `(ctx: Context, request: Mapping[str, Any], ticket: Optional[Ticket] = None) -> Outcome` | 次の順に進める（R1・R2・R11）。<br>・要求の `name` を `check_name` で確かめる<br>・`ticket` が無ければ `take` で券を取る<br>・要求に `ticket` を載せ、`requests-v3/<20 桁の時刻>-<券の ID>.json` に書いて、今のとおり結果を待つ<br>・結果を読むか諦めたら `release` する（渡された券も同じ）<br>旧版のハブの断りでの書き直し（`_unknown_to_older_hub`）は、同じ券で新しいファイル名にする |
| `_hub` | `domains/relay/cli.py` | 変更 | 変えない | 次の 3 つを行う。<br>・`--name`・`--ticket` を `check_name`・`check_id` で確かめてから、`--ticket` と `--ticket-fd` があれば `adopt` して `submit` へ渡す（片方だけなら `invalid`）<br>・種類が `restart` か、`--replace` も `--seated` も無い `spawn` のときは、`_admitting` の中で #4044 の `seats.read` から `submit` の結果までを行う<br>・`stop` の `--launch-gen`（世代か `none`）は要求の `launch_gen` に、`--stray` は `stray: true` に載せる |
| `OWN_OPTIONS`・`_parser` | 同上 | 変更 | — | 次の 2 つを足す。<br>・`spawn` に `--ticket`・`--ticket-fd` を足し、`_split_claude_args` が Claude の引数として拾わないようにする<br>・`restart`・`stop` に `--ticket`・`--ticket-fd`、`stop` に `--launch-gen`・`--stray` を足す（`--stray` は `--launch-gen` と一緒にだけ受ける） |
| `_admitting` | 同上 | 新設（文脈） | `() -> ContextManager[Optional[str]]` | `machine.admit_lock(limits.seconds(machine.LOCK_POLICY))` を取る。取れれば None を、`LockBusy` なら断りの文を渡す（呼び手は要求を書かずに `refused` を返す） |

- `submit`
  - 事前条件: `ticket` を渡すときは、`adopt` が通っている
  - 事後条件: 戻ったとき、このプロセスは券の記述子を持たない。券のファイルは残る
  - 副作用: 券のファイル（`take` のとき）・要求のファイル・結果のファイル

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `submit` | 応答待ちが切れた | ハブの処理が `relay.hub-wait` を越えた | `_wait_until` | 呼び出し元へ返す | 今のとおり `wait_timeout` | 要求のファイルを消し、券を `release` する。処理中なら処理は続く。その間はハブが処理の錠を持つので、一覧は `starting` を返し、起動の許可も数える |
| `_hub`（`_admitting`） | 起動の排他を取れない | 耐久ワークフローの起動の許可か、別の手動の再起動が持っている | `LockBusy` | 呼び出し元へ返す（要求を書かない） | `refused`、「マシンの起動の排他を <秒> 秒の間に取れない。少し待って打ち直す」、終了コード 1 | 何も残らない。打ち直せる |
| `_hub` | 名前・券の ID の形が違う・`--ticket` を受けられない | 呼び手の誤り | `check_name`・`check_id`・`adopt` | 呼び出し元へ返す（ファイルに触れない） | `invalid` | 打ち直しは呼び手が新しい券で |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 手動の `restart`・通常の `spawn` と、起動の許可（行の開始・T4） | マシンの席 | 次の 2 つで守る。<br>・頼む側が `held` を読む前から `submit` が戻るまで `admit.lock` を持つ。錠の順は `admit.lock` → `hub.lock`（ハブの `drain` は `hub.lock` を待たずに試すだけで、`admit.lock` を取らない）<br>・頼む側が先に戻った後の処理は、起動の許可が一覧の `starting` で数える | `held` を読んでから処理されるまでに数える席は増えない。頼む側が落ちれば `admit.lock` と生存の錠が同時に空く。ハブがまだ処理の錠を取っていなければ要求は捨てられ、取っていれば処理の錠と印で数え続ける |
| `--replace` の子と、親の `admitted` | `admit.lock` | 子は取らない（親が持つ） | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `submit` | 券を取り、`requests-v3` に `ticket` つきで書き、結果の後に記述子を閉じる | `ticket` を渡すと自分では取らず、子として打たれた形で親の錠が残る | 応答待ちが切れても、処理中の要求は処理の錠で `starting` に見える |
| `_hub` | `restart` と通常の `spawn` が `admit.lock` の中で `seats.read` と `submit` を打つ（呼ばれた順を縛る） | `--replace`・`--seated` は `admit.lock` を取らない（取ると親と取り合って止まる試験で落ちる） | 取れなければ要求のファイルが書かれない。名前・券の ID の形が違えば `tickets/` の下にも外にもファイルが作られない |
| `_parse` | `spawn --name n --ticket <ID> --ticket-fd 5 … <プロンプト>` で `ticket` と `ticket_fd` が読め、`rest` に入らない | `--ticket` をプロンプトの後に置くと、`rest` に入る（耐久ステップは前に置く） | `--ticket` だけ・`--ticket-fd` だけで `invalid` |
| 直列化 | 設計文書のテスト設計の受け入れ条件 3 の 2 つの順と、頼む側が処理の途中で待ち切れになる順 | — | — |

## ハブの処理（`hub.drain`・`_launch`・`_confirm`・`stop`・起動の印・上限の判定・一覧）

満たす決定: 決定 1・2・3、受け入れ条件 1・2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drain` | `domains/relay/hub.py` | 変更 | 変えない | `hub.lock` を試し、取れたら次の順に進める<br>・`_review_launches(ctx)`<br>・`launch_ticket.sweep`<br>・`requests-v3` の要求を、ファイル名（先頭が時刻）の順に処理する。`requests-v2` の扱いは #4118 が決め、この課題では今の処理を変えない |
| `_handle_file` | 同上 | 変更 | 変えない | `requests-v3` の要求は、`name`・`ticket` を `check_name`・`check_id` で確かめる（違えば試さずに捨てる）。`launch_ticket.processing` の中で処理する<br>・渡された真偽が偽: 処理せず `Outcome(False, "頼む側がいない要求を処理しない", "abandoned")` を返す（R4。結果のファイルは書かず、要求を消し、記録に残す）<br>・真: 今のとおり処理して結果を書いてから抜ける（R3・R6・R7）。処理中の券の ID を `ctx` に載せ、`_launch` が印の `ticket` に書く |
| `_launch` | 同上 | 変更 | 変えない | `_preflight` の後、`write_launch(name, "launch", ticket=<処理中の券の ID>)` で印を書いてから（書けなければ `precondition` を返す）、`tmux.start` を打つ。`spawn_env` が環境に `NDF_RELAY_LAUNCH_GEN=<世代>` を足し、`new_session_args` が環境変数の表の宣言（`secret: false`）で `-e` に載せる。`start` が失敗（時間切れを含む）を返しても、すぐには返さず、`stop(ctx, name)` で片付けてから失敗を返す（R7） |
| `spawn_env` | 同上 | 変更 | `(environ, state_dir, name, entry, gen: Optional[str] = None) -> dict[str, str]` | `gen` があれば `NDF_RELAY_LAUNCH_GEN` を足す |
| `_confirm` | 同上 | 変更 | 変えない | 今の判定（錠を `relay.started` まで待つ・`probe(name) is True`・錠が持たれている）が通れば成功を返し、印は残す（R6）。通らなければ `stop(ctx, name)` を打ち、その結果を照らす（R7）<br>・`stop` が成功（印を消した）: 「セッションを止めた」を足した失敗を返す<br>・`stop` が失敗: 「迷子として数える」を足した失敗を返す（`probe=False` の 1 回だけでは「止めた」と言わない） |
| `stop` | 同上 | 変更 | `(ctx: Context, name: str, gen: Optional[str] = None, stray: bool = False) -> Outcome` | `gen` は世代か `"none"`。手順は下の「処理の手順」の表。成功を返すのは、`probe` が False で、その後に読んだ錠が空いているときだけ（U8）で、そのとき印を消す |
| `_await_exit` | 同上 | 変更 | `(ctx: Context, name: str) -> Optional[Outcome]` | 対象が動いているかを問わず `stop(ctx, name)` を打つ（⑪。錠の空いた対象も印と残ったセッションを確かめる）。`stop` が成功のときだけ None を返して置き換え・再起動へ進み、`stray`・`wait_timeout` ならその結果を返す。`spawn` の置き換えと `restart` はどちらもこれを通してから `_launch` で新しい世代を書く |
| `spawn` | 同上 | 変更 | 変えない | 対象の有無を `name in names or ctx.tmux.probe(name) is not False` で読む（不明は「ある」）。名前の検証は `check_name` で、`_next_name` の後・tmux より前に行う |
| `_review_launches` | 同上 | 新設 | `(ctx: Context) -> None` | `hub.lock` の中で全部の印を見直す（R9。`drain` と一覧が呼ぶ）。tmux を打つのは高々 1 回<br>・錠の空いた印のうち、`checked` が `relay.stop-wait` の秒より古いものが無ければ、何もしない（tmux を打たない）<br>・あれば `ctx.tmux.sessions()` を 1 回打つ。None（不明）なら、その印すべての `checked` を進めて残す<br>・名前の集合が返れば、集合に無い印ごとに、その後でラッパーの錠を読み、空いていれば `drop_launch(name, None)`。集合にある印と、錠が持たれた印は `checked` を進めて残す<br>止めるのは `stop` だけ。1 つの名前の見直しは `stop` が `probe` で行う |
| `_over_cap` | 同上 | 変更 | 変えない | 数える名前を「動いているラッパー ∪ `launch_names` ∪ `held`」にする（対象の名前は除く）（U4） |
| `restart` | 同上 | 変更 | 変えない | `#4044` の和集合の数え方に `launch_names` を足す（対象の名前は除く） |
| `_starting` | 同上 | 新設 | `(ctx: Context) -> dict[str, list[str]]` | 名前ごとに、起動の途中の出所を読む順に集める<br>① `ticket_names`（`ticket`）と `unnamed_ids`（名前 `@unnamed:<券の ID>`・`ticket`）<br>② `launch_names`（`launch`）。印の `ticket` が①の名前の無い券の ID と同じなら、その予約の名前を集めから外す（印の名前が同じ 1 本を数える） |
| `listing` | 同上 | 変更 | 変えない | 次の順に進める（U2。順を逆にしない）<br>・`hub.lock` を待たずに試し、取れたら `_review_launches(ctx)` を打ってから放す（取れなければ見直さない）<br>・`_starting(ctx)` を読む（その中で券 → 印の順）<br>・その後で `registry.busy_names` を読む<br>行には `starting`・`starting_by`・`launch_gen`（`read_launch` の世代）を足す。`running` の欄の名前と意味は変えない（`liveness.wrapper_running` が読む）。登録簿にも錠にも無く `starting` だけの名前と、名前の無い起動の予約も `registered: false` で載せる |

- `_handle_file`（券のある要求）
  - 事前条件: `hub.lock` を持っている
  - 事後条件: 処理した要求の結果と、要るなら印が、処理の錠を消す前に書かれている
  - 副作用: 処理の錠・要求と結果のファイル・印・記録
- `_review_launches`
  - 事前条件: `hub.lock` を持っている（`drain` の中か、一覧が待たずに取れたとき）
  - 事後条件: 消した印の名前は、tmux の一覧に無く、その後に読んだラッパーの錠が空いていた。tmux を打った回数は 0 か 1
  - 所要の上限: tmux の時間切れ（`relay.tmux`）1 回分と、印の数だけのファイルの読み書き

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_handle_file` | 生存の錠が持たれていない・名前か券の ID の形が違う | 頼む側が落ちた・諦めた・書き手の誤り | `processing`・`check_name`・`check_id` | 処理しない | 記録の `abandoned`。頼む側はいないので結果は書かない | 要求のファイルを消す。二度と処理されない |
| `_launch` | `start` が失敗・時間切れ | tmux の不調 | `started.ok` | `stop` で片付けてから失敗を返す | `precondition`。片付けられなければ「迷子として数える」を足す | 印が残れば `stop` か次の `drain`・一覧で見直す。印を消した後に作成の要求が遅れて実行されても、そのラッパーは世代の照合で動かない |
| `_confirm` | 止めた後も不在を確かめられない | tmux の不調・セッションが止まらない | `stop` の結果 | 印を残して失敗を返す | 失敗の結果の文に「迷子として数える」 | 次の `drain`・一覧か `stop` で見直す |
| `_await_exit` | 対象の `stop` が失敗 | 印の残る名前のセッションが止まらない・照会が不明 | `stop` の結果 | 新しい世代を書かずに、その結果を返す | `stray`・`wait_timeout` | 印は前の世代のまま残る。打ち直せる |
| `stop` | 錠が空かない | ラッパーが止まらない | `_wait_until` | 印を残す | 今のとおり `wait_timeout` | 打ち直せる |
| `stop` | 錠は空いたが `probe` が True・None | tmux の不調 | `probe` | 印を残す | `stray`、「<名前> のセッションが残る・有無が分からない。上限で 1 本と数え続ける」、終了コード 1 | 打ち直せる。置き換え・再起動は止まる |
| `stop`（世代つき） | 印の世代が添えた値と違う・ほかの券が持たれている・`--stray` で錠が持たれている | 一覧を読んだ後に起動が進んだ | 照らす条件 | 止めない | `stale`、「<名前> は一覧を読んだ後に変わった。止めない」 | 次の観測で読み直す |
| `_review_launches` | 照会が不明のまま | tmux の不調 | `sessions` が None | 印を残す（数え続ける）。`checked` を進める | 無し（一覧の `starting`） | `relay.stop-wait` の秒の後の `drain`・一覧で再び見る |
| `listing` | `hub.lock` を取れない | ハブが処理中 | 待たない試し | 見直さずに読む | 無し | 処理中のハブの `drain` が見直す。次の一覧でも試す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `stop`（世代つき） | `hub.lock` の中で、次のどれか。印の世代が添えた値と違う（`none` なら印がある）・その名前の券が持たれている（自分の券を除く）・`stray` でラッパーの錠が持たれている | 止めずに `stale` |
| `stop` | 錠が空き・`probe` が False で、その後に読んだ錠も空き | 「動いていない」。印があれば消して ok |
| `stop` | 錠が空き・`probe` が True か None | `kill` → 錠が空くのを待つ → `probe` が False で、その後に読んだ錠も空きなら印を消して ok、そうでなければ印を残して（無ければ `write_launch(name, "stop")` で書いて）`stray` |
| `stop` | 錠が持たれている | `kill` → 錠が空くのを待つ（空かなければ印を残して `wait_timeout`）→ 上と同じく `probe` と、その後に読んだ錠で決める |
| `_review_launches` | 錠の空いた印のうち、照会から `relay.stop-wait` の秒を過ぎたものが無い | tmux を打たない |
| `_review_launches` | `sessions` が名前の集合 | 集合に無く、その後に読んだ錠が空いた印を消す。集合にある・錠が持たれた印は残す（`checked` を更新） |
| `_review_launches` | `sessions` が None | 全部を残す（`checked` を更新） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `REQUESTS_V3` | 定数 | `hub.py` | `"requests-v3"` |
| `GEN_NONE` | 定数 | `hub.py` | `"none"`（世代つきの停止で「印が無かった」を表す） |
| 券の掃除の閾値 | 設定値（今の値から作る） | `hub.py` | `relay.hub-wait` の秒の 2 倍 |
| 印の照会の間隔 | 設定値（今の値） | `hub.py` | `relay.stop-wait` の秒 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| ハブの処理と一覧（`relay ls`） | 券・印・ラッパーの錠 | 一覧は `hub.lock` を待たずに試し、取れたときだけ印を見直してから放す。読むときは排他を取らず、生存の錠 → 処理の錠 → 印 → ラッパーの錠の順に読む。ハブは処理の錠 → 生存の錠の試し → 印 → セッション → 結果 → 処理の錠を消す、の順に進む | 券の空きを読めた後に読む印と錠には、その処理の結果が出ている（U2）。一覧が `hub.lock` を持つ一瞬、頼む側の `drain` は取れずに次の周回で取り直す（`submit` は `relay.hub-wait` の間 `drain` を打ち続ける） |
| ハブの処理と頼む側の落ち | 券 | ハブは処理の錠を取ってから生存の錠を試す | 試しの前に落ちれば捨てる。試しの後に落ちても、ハブが処理の錠を持ったまま処理を終えるので、一覧は処理の終わりまで `starting` を返す |
| 名前の無い起動の処理と一覧 | `tickets/@unnamed/`・印 | ハブは名前を決めた後、印に券の ID を載せてから処理の錠を消す。一覧は券 → 印の順に読む | 券を読んだ時点で印が無ければ予約の行が、印があれば印の名前の行が 1 本と数える。どちらも無い時点は無い |
| ハブの処理と、ハブのプロセスの落ち | 処理の錠・印・tmux のクライアント | OS が処理の錠を解く | 印を書いた後なら印が残り、`starting` が続く。`.hub` のファイルは `sweep` が消す。生き残ったクライアントが後でセッションを作っても、印を消した後ならラッパーは動かない |
| 印とハブの処理 | 印 | 書くのは `hub.lock` の中だけ。消すのは `hub.lock` の中（ハブか一覧）か、錠を持つ自分の世代のラッパー | 何度見直しても同じ |
| 置き換え・再起動と、印だけが残る名前 | 印・セッション | `_await_exit` が錠の有無を問わず `stop` を打ち、成功の後にだけ `_launch` が新しい世代を書く | 前の世代のセッションが止まらなければ、新しい世代を書かない |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_handle_file` | `abandoned` の要求の ID と名前 | `hub-log.jsonl` | 要求の `args`（最初の入力） |
| `write_launch` を呼ぶ所・`_review_launches`・`stop` | 印を書いた・消した名前・世代・理由 | 同上 | 環境の値 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| 起動の印 | `registry` の登録簿・`hub.json` | `hub.py`・`registry.py` を読んだ | 使えない（登録簿は登録の行で、起動していない名前を持たせると一覧と `restart` の意味が変わる。印は別のファイルにする） |
| `launch_ticket.held` | `identity.lock_held` | `grep -n "def lock_held" src/ndf/identity.py` | 使えない（`LOCK_EX|LOCK_NB` で試すので、観測者どうしと、ハブの試しと一覧の試しを取り違える。共有の待たない試しを新設する） |
| `launch_ticket.take` | `signals.held`（排他の錠） | `grep -n "def held" src/ndf/domains/relay/signals.py` | 使えない（`domains` の下にあり `progress` から読めない。抜けるときに錠を解く形で、子と共有する錠を解く） |
| 名前の形 | `signals.NAME_RE` | `grep -n "NAME_RE" src/ndf/domains/relay/signals.py` | `core` へ移して正本にする（`progress` からも読む必要があり、`.`・`..` を断る形に直す）。`signals.NAME_RE` は参照に変える |
| 世代をラッパーへ渡す経路 | `tmux.new_session_args`・`contracts/domains/env.json` | `tmux.py` を読んだ（`-e` に載せるのは `facts.ids("env", secret=False)` の名前だけ） | 使う。表に `NDF_RELAY_LAUNCH_GEN` を足すだけで、既に動いているサーバーにも届く |
| 一覧の印の見直し | `drain` の `hub.lock`（`signals.held(…, 0)`） | `hub.py` の `drain` を読んだ | 使う。同じ錠を待たずに取り、取れなければ見直さない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_handle_file` | 生存の錠が持たれた要求を処理し、処理の間は `held(.hub)` が真 | 次の 3 つを縛る<br>・番の直前に券を放すと `abandoned` で捨て、ラッパーを起こさない<br>・処理の錠を取った後・試しの前に頼む側を止めると捨て、その間の一覧は `starting`<br>・一覧が `.req` を試している最中にハブが試しても、頼む側のいない要求を捨てる | 券のファイルが無い・名前が `../x` |
| `_launch`・`_confirm` | 確かめの成功で印が残り、`running` で読める。印に処理中の券の ID が載る | 次の 4 つを縛る<br>・確かめの 15 秒の後に錠を取るラッパーが止められ、印が消える<br>・`start` が時間切れを返したがセッションは作られていたときに、`stop` で片付ける<br>・`new-session` の直後にハブを止めると、印が残る<br>・`new_session_args` の引数に `-e NDF_RELAY_LAUNCH_GEN=<世代>` が入る | 止めた後の `probe` が None・True で印が残り、`stray` の文が付く |
| `_await_exit` | 錠が持たれていない対象にも `stop` を打つ | 印だけが残り、錠の空いたセッションが残る名前で、`stop` が成功してから新しい世代が書かれる | `stop` が `stray` なら新しい世代を書かず、印は前の世代のまま |
| `stop` | `probe` が False で、その後に読んだ錠も空けば ok で印を消す | 世代が違う・`none` で印がある・ほかの券が持たれている・`--stray` で錠が持たれていれば `stale` で止めない | 錠が空いても `probe` が None で `stray`。そのとき `_await_exit` が置き換えを止める |
| `_over_cap`・`restart` | 印の名前を 1 本と数えて上限で断る | 印の名前と対象が同じなら数えない | — |
| `listing` | 持たれた券・印の名前に `starting: true` と出所。名前の無い券に予約の行 | 次の 3 つを縛る<br>・券を読んだ後に処理を終わらせても（読む順の試験）、その処理が起こしたラッパーが `running` で、印が `starting` で読める<br>・ラッパーが自分の世代の印を消してから錠を放す順で、`starting` も `running` も偽の時点に新しいラッパーが起きない<br>・名前の無い起動で、印が券の ID を持てば予約の行が消え、1 本だけ数えられる | 次のどれかで落ちる<br>・錠を先に読む<br>・処理の錠を生存の錠より先に読む<br>・名前の無い券を読まない |
| `listing` の見直し | `hub.lock` が空いていれば、異常終了したラッパーの印を消してから読む | `hub.lock` が持たれていれば見直さず、tmux を打たない | tmux の一覧が不明なら印を残す |
| `_review_launches` | 処理の手順の表の 3 行。印が 5 つでも tmux は 1 回 | 錠を見た直後にラッパーを落とすと、印が残り `starting` を返す。tmux の一覧を読んだ後・錠を読む前にラッパーが錠を取ると、消さない | 照会が不明で印を残し、`relay.stop-wait` の秒の間は照会し直さない。tmux が時間切れでも 1 回分で戻る |

## tmux の照会（`tmux.probe`・`sessions`）

満たす決定: 決定 3、受け入れ条件 2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `probe` | `domains/relay/tmux.py` | 新設 | `(name: str) -> Optional[bool]` | `has-session -t =<名前>` を打つ<br>・True: 終了コード 0<br>・False: 終了コード 1 で、出力が `can't find session`・`no server running` を含むか、`error connecting to` と `No such file or directory` を含む<br>・None: `OSError`・時間切れ・それ以外 |
| `sessions` | 同上 | 新設 | `() -> Optional[frozenset[str]]` | `list-sessions -F '#{session_name}'` を 1 回打つ（`_review_launches` が呼ぶ）。終了コード 0 なら名前の集合。`no server running` か `error connecting to … (No such file or directory)` なら空。それ以外（`OSError`・時間切れを含む）は None |
| `has` | 同上 | 変更 | 変えない | `probe(name) is True`。ハブの中で有無を読む所は `probe` を読み、`has` は今のほかの呼び手（`cli._stop_one`）のためだけに残す |
| `new_session_args` | 同上 | 変えない | 変えない | 環境変数の表に `NDF_RELAY_LAUNCH_GEN`（`secret: false`）が足されるので、`-e` に世代が載る。コードは変えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `probe` | 終了コード 0 で True、`can't find session: x` で False、`error connecting to /tmp/tmux-1000/s (No such file or directory)` で False | `error connecting to … (Permission denied)` で None | `OSError`・`TimeoutExpired`・終了コード 1 で知らない出力は None |
| `sessions` | 名前の集合 | サーバーが無いと空 | `OSError`・時間切れで None |
| `new_session_args` | 環境に `NDF_RELAY_LAUNCH_GEN` があれば `-e NDF_RELAY_LAUNCH_GEN=<世代>` が入る | 秘密の名前は `-e` に入らない（今のまま） | — |

## ラッパーの始まりと終わり（`wrapper.run`）

満たす決定: 決定 3、受け入れ条件 5

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `run` | `domains/relay/wrapper.py` | 変更 | 変えない | 次の 2 つを足す。環境の `NDF_RELAY_LAUNCH_GEN` が無い（人が直接打った `relay run`）ときは、どちらも行わない<br>・**始め（R13）**: 錠（`relay.lock`）を取った直後、版の混ざりの確かめの前に `launch_ticket.read_launch(name)` を読む。印が無い・世代が自分の世代と違う（壊れた印の空の世代を含む）なら、`_loop` に入らず `Ended("refused", "<名前> の起動の印が別の世代を指す。起動しない")` を返す。印は消さない<br>・**終わり（R10）**: 錠を持つ `with` の末尾で、`_loop` が `Ended` を返し、かつ今のセクションが無いか、その子の `isalive()` が偽のときだけ、`launch_ticket.drop_launch(name, gen)` を打ってから錠を放す。例外で抜けるとき、子を止められずに終わるとき（`_child_alive` の `Ended("refused", …)`。子は生きている）、始めの照合で断ったときは消さない |

単体テストの観点:

| 観点 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| 始めの照合 | 印の世代が自分の世代と同じなら `_loop` に入る | 印が無い・別の世代・壊れた印なら Claude を起動せずに `refused` で終わり、印は残る。世代の環境が無い `relay run` は照合しない | 印を読めない |
| 終わりの消し方 | 子が終わって `Ended` を返すと、自分の世代の印が消える | 世代が違う印（後の起動のもの）は消さない。`_child_alive` の `refused` で終わると印が残る（子が生きている） | 例外で抜けると印が残る |

## 起動の手と起動の許可（`progress/lane/launch`・`machine`）

満たす決定: 決定 1・4・6、受け入れ条件 1・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_relay` | `progress/lane/launch.py` | 変更 | `(argv: list[str], run: Optional[Any], failure: str, name: str) -> dict[str, Any]` | 次の順に進める（`spawn`・`replace`・`stop` が呼ぶ）。<br>・`argv[0]` の `bin/ndf` の導入の根（`_root_of`）を `launch_ticket.supports` で読み、偽なら券も子も作らずに `{"ok": false, "detail": "<failure>: 起動の券に対応しない導入 <根>"}` を返す<br>・真なら `take(state_dir, name)` で券を取り、`argv` の `relay <動詞>` の直後（`--name` があればその直後）に `--ticket <ID> --ticket-fd <番号>` を挿む<br>・`_run(argv, run, pass_fds=(ticket.fd,))` で子を打ち、子が戻ったら `release` する |
| `_spawn_argv` | 同上 | 変更 | 変えない | 券の語を挿む位置が、プロンプト（`first`）と `--` より前になる並びを保つ |
| `_run` | 同上 | 変更 | `(argv: list[str], run: Optional[Any], pass_fds: tuple[int, ...] = ()) -> CompletedProcess[str]` | `pass_fds` を `subprocess.run` へ渡す |
| `stop` | 同上 | 変更 | `(repo: str, plan: str, lane: str, run: Optional[Any] = None, launch_gen: Optional[str] = None, stray: bool = False) -> dict[str, Any]` | 計画の停止（`vacate_lane`・`settle_seat`）は `launch_gen` に一覧で読んだ印の世代（無ければ `"none"`）を渡し、`argv` に `--launch-gen <値>`（`stray` なら続けて `--stray`）を足す。None は世代を添えない（今の呼び手のため） |
| `Presence` | 同上 | 新設（定数） | `RUNNING = "running"`・`STARTING = "starting"`・`ABSENT = "absent"`・`UNKNOWN = "unknown"` | 生存の値の名前 |
| `count` | `progress/lane/machine.py` | 変更 | 変えない | `running` を `{name for name in listed if wrapper_presence(listed, name) != ABSENT}` にする（動いている名前・起動の途中の名前・名前の無い起動の予約の行。決定 4）。予約の行の名前（`@unnamed:<券の ID>`）は計画のレーンの名前と重ならないので、`skip` と `live` の和で二重に数えない |

- `_relay`
  - 事前条件: 状態の保存先が `identity.load()` で読める
  - 事後条件: 戻ったとき、親は券の記述子を持たない。子が生きていれば券は子が持つ
  - 副作用: 券のファイル・子のプロセス

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_relay` | 子の導入が券に対応しない | 子が古い導入 | `supports` | 呼び出し元へ返す（券も子も作らない） | `{"ok": false, "detail": "…起動の券に対応しない導入…"}` | 要求は書かれていない（不在と確定）。手の失敗として数える |
| `_relay` | 券を作れない | 保存先 | `OSError`・`TicketError` | 呼び出し元へ返す | `{"ok": false, "detail": "<failure>: 起動の券を作れない: <理由>"}` | 何も残らない。手の失敗として数える |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_relay` | 子へ `--ticket`・`--ticket-fd` が `--name` の直後に渡り、子が戻った後に親の記述子が閉じる | 親が先に戻っても、子が生きている間は `held` が真 | `relay-protocol.json` の無い根では子を打たない（`run` が呼ばれない） |
| `count` | `starting` の行の名前と名前の無い起動の予約の行を数える | `running` と `starting` が両方真の名前を 1 本と数える | 一覧が None なら今のとおり `OBSERVE` |
| `stop` | 世代を渡すと `--launch-gen <世代>`、`none` を渡すと `--launch-gen none`、`stray` で `--stray` が付く | 世代を渡さない呼び手の `argv` は今のまま | — |

## 生存の判定と席の手（`liveness`・`plan/run`）

満たす決定: 決定 2・3・5、受け入れ条件 1・2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Seen` | `progress/lane/liveness.py` | 新設（型） | `presence: str`・`starting_by: tuple[str, ...]`・`launch_gen: Optional[str]` | 1 つの名前について、同じ観測で読んだ生存と、`starting` の出所・印の世代 |
| `wrapper_seen` | 同上 | 新設 | `(table: Optional[Mapping[str, Mapping[str, Any]]], name: str) -> Seen` | 次の順に決める。席の手・観測・期限の判定・起動の許可はこの関数（と `wrapper_presence`）だけを読む<br>・`table` が None（一覧を読めない）: `UNKNOWN`<br>・行の `running` が真（今の `wrapper_running(row)`。`relay ls` の行の欄の名前は `running`）: `RUNNING`<br>・行の `starting` が真: `STARTING`<br>・それ以外（行が無いを含む）: `ABSENT`<br>`starting_by`・`launch_gen` は行の値（行が無ければ空と None） |
| `wrapper_presence` | 同上 | 新設 | `(table: Optional[Mapping[str, Mapping[str, Any]]], name: str) -> str` | `wrapper_seen(table, name).presence` |
| `seen_of` | 同上 | 新設 | `(item: Optional[Mapping[str, Any]]) -> Seen` | 写しの `settles` の行から `Seen` を作る。`presence` が 4 つの値のどれでもない・欄が無い・行が None なら `UNKNOWN` |
| `_confirm_seat` | `progress/plan/run.py` | 新設 | `(conn, row: LaneRow, sprint: str, seq: int, cause_absent: str, seen: Seen) -> dict[str, Any]` | 渡された生存（`seen`）で分ける。一覧を自分では読まない（呼び手が `wrapper_seen` か `seen_of` で作る）<br>・取り直し中: `RUNNING` → `move_seat(to=SEATED, cause=present)`、`ABSENT` → `move_seat(to=VACATED, cause=cause_absent)`<br>・返却中: `ABSENT` → `move_seat(to=VACATED, cause=absent)`、`RUNNING` → 書かない<br>・`STARTING`・`UNKNOWN`: どちらも書かない<br>返すのは、書いた遷移か書かなかった理由（`present`・`vacated`・`starting`・`unknown`・`stale`）と、`STARTING` のときの `starting_by`・`launch_gen` |
| `settle_seat` | 同上 | 変更（#4091 の新設） | 変えない（手の `seen` に、写しの `settles` の行をそのまま載せて受ける） | 確かめの手順の 2・3 を `_confirm_seat(..., cause_absent="absent", seen=seen_of(<手の seen>))` にする。`relay ls` を打たない。写しの行が `unknown` なら書かずに `pending` を返す。`STARTING` で `starting_by == ("launch",)`（出所が印だけ）なら、次の条件で `launch.stop(…, launch_gen=<世代>, stray=True)` を 1 回打つ（ハブが世代と錠を照らして見直す）。結果は下の「`Settle` の結果」の表<br>・条件: その (レーン, 世代) へ最後に頼んでから `relay.stop-wait` の秒が過ぎている（`RunState.settling` の記録を手に載せて受ける） |
| `vacate_lane` | 同上 | 変更（#4091 の新設） | 変えない | 返却の手順の 3 の `launch.stop` に、手順の 2 で読んだ一覧の `wrapper_seen(table, name).launch_gen`（無ければ `"none"`）を `launch_gen` として渡す。`stale` が返れば書かずに `stale` を返す（次の観測で読み直す）。手順の 4 を、`launch.stop` の後に `launch.wrappers()` を 1 回読んだ `_confirm_seat(..., cause_absent="stopped", seen=wrapper_seen(<読んだ一覧>, name))` にする（`hub.lock` を取らない。`stop` の後なので一覧を読み直す） |
| `launch_lane` | 同上 | 変更（#4091） | 変えない | 取り直しの手順の 4・5 を下の「起動の後の確かめ」の表にする（券を閉じた後に `launch.wrappers()` を 1 回読み、`wrapper_seen` で `_confirm_seat` へ渡す）。手順 6・7（同じ手の回復）は #4091 のまま、確かめの部分だけ `_confirm_seat` を読む |

起動の後の確かめ（取り直しの手順の 4・5。`launch.replace` が戻り、券を閉じた後）:

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
| `starting`・`unknown` | `{"ok": true, "pending": true, "stopped_gen": <頼んだ世代か null>}` | `_tick_settle` で回数を数え、頼んだ世代を記録する |
| `stale` | `{"ok": false, "stale": true}` | 失敗に数えない（#4091 のまま） |

### 処理の手順

- 繰り返しの終わる条件: `Settle` は、券が空き印が消えた時点で必ず `RUNNING` か `ABSENT` を読む
  - 券が空くのは、頼む側が結果を受け取るか、`relay.hub-wait` で諦めるか、落ちて、かつハブが処理を終えたとき
  - 印が消えるのは、tmux が「無い」と答えた後に読んだ錠が空いていたときか、ラッパーが子の終わりを確かめて終わったとき。異常終了したラッパーの印は、観測が打つ一覧の見直し（`hub.lock` が空いているとき）が消すので、`relay spawn` が打たれなくても消える
- 一覧を再利用する条件: 同じ観測の写しに載った生存を使う。写しから `Settle` までの間に、同じ計画がそのレーンに起動の手を打たない（S5）ことが条件で、書く前の `seq` の照合は `lock_plan` の中で変えずに行う。観測が一覧を読めなかったときも、写しの行は `unknown` を持ち、`Settle` は読み直さない。`vacate_lane` と `launch_lane` は、自分で `stop`・起動を打った後なので読み直す（それぞれ 1 回）

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 席の手（T2・T6・T7）と、ハブが処理する起動 | そのレーンのラッパーと席 | 席の手は `lock_plan` だけを取る。一覧を読んだ後・書く前に、その名前で新しく券を取れるのは次の 2 つだけ（U3）<br>・同じ計画の耐久ワークフロー（席の手と順に打つ）<br>・`admit.lock` を持つ手動の `restart`・通常の `spawn` | 書いた後に手動の再起動で起きたラッパーは、起動の許可とハブが動いているラッパーとして数える（#4091 の S1）。席の手は打ち直しても同じ一覧で同じ結果を書く |
| `Settle`・`Vacate` の世代つきの `stop` と、手動の再起動 | 印・セッション | ハブが `hub.lock` の中で世代と券を照らす（`Settle` は錠も） | 一覧を読んだ後に再起動が進めば、世代が変わるので `stale` で止めない |
| 取り直しの手の打ち直しと、生き残った子 | 券 | 親が落ちても子が券を持つ | 打ち直しは `RESEATING` の行で起動せず `_confirm_seat` だけを打つ（#4091 の S6）。子が生きている間は `STARTING` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `wrapper_seen`・`wrapper_presence` | 4 つの値。`relay ls` が実際に返す行（`running` の欄）で `RUNNING` を読む | `running` と `starting` が両方真の行は `RUNNING`、行が無いと `ABSENT` | 一覧が None で `UNKNOWN`。`wrapper_running` の欄の名前で作った行では `RUNNING` にならない（欄の名前の試験） |
| `seen_of` | 写しの行の 4 つの値 | `presence` の欄が無い・値が違う行は `UNKNOWN` | 行が None で `UNKNOWN` |
| `_confirm_seat` | 段階と生存の組み合わせの表どおりに書く | 一覧に `registered: true, running: false` の行だけが載るレーンは `ABSENT`。`relay ls` を打たない | `STARTING`・`UNKNOWN` で書かない |
| `vacate_lane` | 一覧の世代を添えて止め、止めた後の一覧で返却済みにする | 一覧を読んだ後に同じ名前の手動の再起動が成功すると `stale` で止めない。印の無い行は `none` を添える | — |
| `launch_lane` | 起動の後の確かめの表の 6 行 | 応答待ちが切れた後にハブが起こした（一覧 `RUNNING`）を成功にする | 断りを数えると落ちる |
| `settle_seat` | 親と子が落ちた要求の後で `ABSENT` → 返却済み | 親だけが落ちて子が生きている間は `pending` | 次の 3 つを縛る<br>・出所が印だけのとき、世代つきの `stop` を 1 回打つ<br>・券が併存すれば打たない<br>・同じ世代へ `relay.stop-wait` の秒の間に 2 回打たない<br>・写しの行が `unknown` なら `relay ls` も `stop` も打たない |

## 期限を越えた続きの手と `Settle`（`plan/run_decide`）

満たす決定: 決定 5、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Observe.expiring` | `plan/run_decide.py` | 新設（欄） | `tuple[str, ...]`（既定は空） | この観測の後の `_expire_launched` で期限に達する続きの手（`Launch.continues` が真）のレーン |
| `_expiring` | 同上 | 新設 | `(state: RunState, policy: Policy) -> tuple[str, ...]` | `launched` の続きの手のうち、`waits` の回数 + 1 で `_tick_waits` と同じ式（`n * poll >= up`）を満たすもののレーン。止める手・行を始める手は載せない。既に期限を越えて残っている手（`kept_alive` と据え置いた手）のレーンも載せる（生存を観測ごとに読み直す） |
| `_observe` | 同上 | 変更 | 変えない | `Observe(spent, expiring=_expiring(state, policy))` |
| `observe` | `plan/run.py` | 変更 | `(spec: dict, spent: Sequence[str] = (), expiring: Sequence[str] = ()) -> dict` | `expiring` を `snapshot_of` へ渡す。`_perform` が `observe(spec, list(act.spent), list(act.expiring))` で打つ |
| `snapshot_of` | `plan/run.py` | 変更 | `(view, rows, found=None, moved=None, spent=(), expiring: Sequence[str] = ()) -> dict` | 次のどれかがあれば一覧を 1 回読む（`judge_lanes`・#4091 の `needs_table` と同じ 1 回を共有する）<br>・`expiring` が空でない<br>・`settles` がある<br>写しには次を載せる<br>・`alive`: `expiring` の各レーンの `wrapper_presence`<br>・`settles` の各行: `wrapper_seen` の `presence`・`starting_by`・`launch_gen`<br>一覧を読めなかった（`launch.wrappers()` が None）観測でも、`alive` と `settles` の行を作り、生存を `unknown` と明示して載せる（取得済みの `unknown`。`Settle` はこれを読み直しの合図にしない） |
| `Snapshot.alive` | `plan/run_decide.py` | 新設（欄） | `list[Any]`（`[レーン, 生存]` の並び。既定は空） | 期限に達する続きの手のレーンの生存 |
| `RunState.kept_alive` | 同上 | 新設（欄） | `tuple[str, ...]` | 期限を越えたが `RUNNING` なので `launched` に残した続きの手のキー。キーが `launched` から外れたら同時に外す |
| `RunState.settling` | 同上 | 新設（欄） | `tuple[tuple[str, int, Optional[str], int], ...]` | `Settle` のキー・`pending` が続いた観測の回数・最後に `stop` を頼んだ世代・そのときの回数 |
| `_expire_launched` | 同上 | 変更 | 変えない | 期限切れに分かれた続きの手を、写しの `alive` のそのレーンで分ける。止める手・行を始める手は今のとおり<br>・`RUNNING`: 数えず置き換えず、`launched` に残し、`waits` にはこの観測で期限に達した回数 `n`（`_tick_waits` が数えた値。`n * poll >= up` を満たす）を保存し、`kept_alive` に積む<br>・`ABSENT`: 今のとおり `_fail` で 1 回数え、`kept_alive` から外す（候補に戻り、同じキーで打ち直す）<br>・`STARTING`・`UNKNOWN`・載っていない: `launched` に残し、`waits` には同じく期限に達した回数 `n` を保存する（1 つ前の値に戻さない）<br>そのため `_tick_waits` は、期限切れに分けた手の `n` も呼び手へ返す（返す組に回数を足す） |
| `_held_lanes` | 同上 | 新設 | `(state: RunState, policy: Policy) -> frozenset[str]` | `launched` に残る続きの手のうち、保存された `waits` の回数が `n * poll >= up` に達したもの（`kept_alive` と据え置いた手）のレーン。`_expire_launched` が期限に達した回数を保存するので、期限に達した観測の直後から抑えに入る |
| `_tick_waits` | 同上 | 変更 | `(state, waiting, policy) -> tuple[list[str], list[tuple[str, int]], list[tuple[Launch, int]]]` | 期限切れの手を、期限に達した回数と組にして返す。数え方（`n * poll >= up`）は変えない |
| `_candidates` | 同上 | 変更 | 変えない | 今の絞り込みに、`a.lane in _held_lanes(state, policy)` で `a.halt` でも `a.cancel` でもない手を除く条件を足す。続きの手（`next`・`/goal` の入れ直し）も行を始める手も、別のキーでも出さない。止める手と中止させる手は通す |
| `_notice_stalls` | 同上 | 変更 | 変えない | `relaunching`（`/goal` の入れ直しを待つレーン）から、`kept_alive` のキーを持つレーンを外す |
| `_after_launch` | 同上 | 変更 | 変えない | `Settle` の結果は `launched`・`started`・`history`・`_fail` に入れない。`pending` なら `_tick_settle`、書いたら `settling` からキーを外す。`stale` は `tried` に置くだけ |
| `_tick_settle` | 同上 | 新設 | `(state: RunState, act: Settle, got: Mapping[str, Any], policy: Policy) -> RunState` | `settling` のキーの回数を 1 つ進め、`got.stopped_gen` があれば頼んだ世代と回数を記録する。`n * poll >= up` に初めて達したら、行の段階で今の知らせ（取り直し中は `LAUNCH_FAILED`、返却中は `HALT_FAILED`）を `unreported` に置き、キーを `noticed` に積む（2 回目を出さない）。`failed`・`final` には置かない |

- `_expire_launched`・`_candidates`
  - 事後条件: `kept_alive` ⊆ `launched`。`RUNNING` の続きの手は失敗に数えられない。`_held_lanes` のレーンには、止める手と中止させる手しか候補に出ない
- `Settle` の候補: `Settle` は `launched`・`failed` に入らないので、写しの `settles` に載る限り観測ごとに `_candidates` に出る（`tried` は観測ごとに空く）。手には `settling` の頼んだ世代と回数を載せ、`settle_seat` が `stop` の間隔を決める

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `snapshot_of` | 一覧を読めない | `relay ls` の失敗 | `launch.wrappers()` が None | 既定値で続ける（`alive` と `settles` の生存を `unknown` と明示して載せる） | 無し | 次の観測で読み直す（`relay ls` は観測あたり 1 回のまま）。期限切れを決めず、席も書かない |
| `_tick_settle` | 長く起動の途中 | 子が待ち続ける・tmux の不調 | 回数 | 知らせを 1 回出して打ち続ける | 計画のリクエスト（`launch_failed`・`halt_failed`） | 券が空き印が消えた観測で書く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_expiring`・`observe`・`snapshot_of` | 期限に達する続きの手のレーンだけが `expiring` に載り、写しの `alive` に生存が載る | 次の 3 つを縛る<br>・止める手・行を始める手・期限前の手は載らない<br>・`judge_lanes` が一覧を要らず、`settles` も `expiring` も無い観測では `relay ls` を打たない<br>・保留の `Settle` が 3 本・`expiring` が 2 本ある観測でも 1 回だけ。一覧を読めない観測でも、`settles` の 3 本の行が `unknown` を持ち、続く `Settle` が `relay ls` を打たない | 一覧が None で `unknown` を明示して載せる |
| `_expire_launched` | `RUNNING` で数えず `kept_alive` に載る、`ABSENT` で 1 回数えて打ち直す | `STARTING`・`UNKNOWN` で `launched` に残り、保存した回数が期限に達した値（例: 3 回目で達したら 3）になる | 期限だけで数えると落ちる。1 つ前の回数を保存すると、`_held_lanes` の試験で落ちる |
| `_candidates`・`_held_lanes` | `kept_alive` のレーンに、別のキーの `/goal` の入れ直しが写しに載っても候補に出ない | 止める手と中止させる手は出る。`ABSENT` で数えた次の観測では出る。`STARTING` で期限に達した直後の観測でも、別のキーの `/goal` の入れ直しは出ない | キーだけで除くと落ちる |
| `_notice_stalls` | `kept_alive` のレーンが写しの `stalls` にあると、止まりの知らせが 1 回出る | 2 回目は出ない | `relaunching` から外さないと、`/goal` の入れ直しも知らせも出ずに落ちる |
| `_tick_settle` | `pending` が `up` の秒を越えると知らせが 1 回、その後も `Settle` が出て、券が空けば書く | ちょうど閾値。頼んだ世代が記録され、次の `stop` は `relay.stop-wait` の秒の後 | `Settle` を `failed`・`final` に置くと落ちる |
