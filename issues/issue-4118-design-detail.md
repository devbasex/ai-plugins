# #4118 の詳細設計: 旧い版の受付箱の閉鎖と旧版の照合

設計文書は [issue-4118-design.md](./issue-4118-design.md) にある。状態と事象の表（C1〜C10）と不変条件（V1〜V7）が振る舞いの正本で、この文書の関数はどれもその 1 行を実装する。パスは `plugins/ndf/` からの相対で書く。#4103 の詳細設計（[issue-4103-design-detail.md](./issue-4103-design-detail.md)）の `drain`・`listing`・`_review_launches`・`write_launch`・`Tmux.sessions` の上に乗る。

## 地図

```text
plugins/ndf/
├── contracts/relay-protocol.json            # inbox に inboxes（版ごとの受付箱の名前の表）を足す
├── docs/contracts/commands.md               # relay ls の legacy・legacy_reason、後の版が閉じたときの断り
├── src/ndf/identity.py                      # state_dir_of*（環境の表から保存先を解く。_state_home もこれを使う）
├── src/ndf/domains/relay/
│   ├── inbox_closure.py                     # state*・settle*・superseded_since*・refuse_if_superseded*・_close_one*・_answer_old*・_reconcile*・_owner*・_clients*・own_inbox*・retired_inboxes*
│   ├── tmux.py                              # panes*（名前とペインの pid を 1 回で読む）・sessions（panes から引く）
│   ├── hub.py                               # submit・drain・listing・REQUESTS_DIR
│   └── cli.py                               # _hub の最初で後の版の閉鎖を断る・ls の出力に legacy・legacy_reason
└── src/ndf/progress/lane/launch.py          # wrappers が legacy を見る
```

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/inbox_closure.py` | 新設 | 旧い版の受付箱を閉じ、旧版の跡を照らし、閉鎖の状態を決める | `ndf.contracts.facts`・`ndf.identity`・`core/launch_ticket`・`domains/relay/signals`・`domains/relay/tmux` | `Closure`・`Mark`（新設） | `own_inbox`・`retired_inboxes`・`state`・`settle`・`superseded_since`・`refuse_if_superseded`・`_close_one`・`_answer_old`・`_reconcile`・`_owner`・`_clients`（新設） |
| `src/ndf/identity.py` | 変更 | 環境の表から状態の保存先を解く規則を 1 か所にする | — | — | `state_dir_of`（新設）、`_state_home`（`state_dir_of` を使う） |
| `src/ndf/domains/relay/tmux.py` | 変更 | セッションの名前とペインの pid を 1 回の `list-sessions` で読む | — | `Tmux` | `panes`（新設）、`sessions`（`panes` の鍵を返す） |
| `src/ndf/domains/relay/hub.py` | 変更 | `drain` と一覧が閉鎖を通し、済むまで起動を保留する。頼む側は後の版の閉鎖で断る | `inbox_closure` | — | `submit`・`drain`・`listing`・`_write_request`（変更）、`REQUESTS_DIR`（表から引く） |
| `src/ndf/domains/relay/cli.py` | 変更 | `spawn`・`restart`・`stop`（`--all` を含む）の副作用の前に後の版の閉鎖を断る。`ls` の結果の最上位に `legacy`・`legacy_reason` を載せる | `hub`・`inbox_closure` | — | `_hub`（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | `legacy` が真の応答を None にする | — | — | `wrappers`（変更） |
| `contracts/relay-protocol.json` | 変更 | 版ごとの受付箱の名前の表を持つ | — | — | — |

## 受付箱の閉鎖（`inbox_closure`）

### 引数の `state_dir`

この節の関数の第 1 引数はどれも `state_dir`（`hub.Context.state_dir`、`launch_ticket` の関数が受ける値と同じ状態の保存先）である。リレーハブの置き場（`<state_dir>/relay/hub`。`facts.value("locations", "relay-hub", "path")`）は関数の中で `hub.hub_dir` と同じ式で引き、受付箱・脇の `.closing`・`closure.json`・`results/`・`hub-log.jsonl` はその下に置く。`write_launch`・`read_launch` には `state_dir` をそのまま渡す。リレーハブの置き場を引数で受ける関数は作らない（`<state_dir>/relay/hub/relay/hub` に印を書く誤りを型の上で起こさない）。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `own_inbox` | `domains/relay/inbox_closure.py` | 新設 | `() -> str` | `relay-protocol.json` の `inboxes[str(inbox)]` を返す（`hub.REQUESTS_DIR` の出所） |
| `retired_inboxes` | 同上 | 新設 | `() -> tuple[str, ...]` | `inboxes` のうち番号が `inbox` より小さい名前を番号の順に返す（今は `requests`・`requests-v2`） |
| `state` | 同上 | 新設 | `(state_dir: Path) -> Closure` | 閉鎖の状態を決める（V5）。読む順は、自分の受付箱の `lstat` → 旧い受付箱の `lstat` → `closure.json` の順で、`closure.json` を最後に読む（V7）。判定の順は `superseded` → `open` → `pending` / `settled` |
| `superseded_since` | 同上 | 新設 | `(state_dir: Path, seen: Closure) -> bool` | `closure.json` だけを読み直し、`by` が自分の `inbox` より大きければ真（`seen` が既に `superseded` なら真）。一覧が行を読み終えた後に打つ（V7） |
| `refuse_if_superseded` | 同上 | 新設 | `(state_dir: Path) -> Optional[str]` | `state` が `superseded` なら断りの文、ほかは None。呼び手（CLI の `_hub` が副作用の前、`hub.submit` が券を取る前）が `precondition` の `Outcome` にする（`inbox_closure` は `hub` に依らない） |
| `settle` | 同上 | 新設 | `(state_dir: Path, tmux: Tmux, wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic, panes: Optional[Callable[[], Optional[dict[str, int]]]] = None) -> Closure` | `hub.lock` の持ち主だけが呼ぶ。脇の `.closing` が残っていれば状態に依らず断りを続け（C10）、`open` なら閉じ（C1〜C3）、`pending` で試してよい時刻を過ぎていれば照合し（C5）、呼んだ後の状態を返す。時刻は引数で受けず、書く直前に `wall()`・`mono()` を読む。`panes` は `_review_launches` と同じ回の tmux の一覧を共有するときに渡す |
| `_close_one` | 同上 | 新設 | `(state_dir: Path, name: str) -> bool` | 旧い受付箱 1 つを閉じる（C2）。ディレクトリなら脇の `<名前>.closing` へ移して `_answer_old`、通常のファイルを一時の名前から `os.rename` で置く。置けたら真 |
| `_answer_old` | 同上 | 新設 | `(state_dir: Path, aside: Path) -> bool` | 脇の `.closing` の `*.json` ごとに断りの結果を `results/<名前>` へ書き、要求を消し、記録に 1 行書く。最後にディレクトリごと消す（C3）。消しきれたら真 |
| `_clients` | 同上 | 新設 | `(state_dir: Path) -> Optional[list[int]]` | 生きている tmux のクライアントのうち、引数が `-L ndf-lanes` と `new-session` を含み、この保存先のもの（`_owner` の規則でクライアントの環境を解く）の pid を返す。Linux は `/proc/<pid>/cmdline`・`environ` を読む。`/proc` が無ければ `ps -A -o pid=,args=` を 1 回打ち、所属を確かめずに全部を返す（数え過ぎの側）。読めなければ None |
| `_owner` | 同上 | 新設 | `(state_dir: Path, pid: int) -> Optional[bool]` | `pid` の環境（Linux の `/proc/<pid>/environ`）を `identity.state_dir_of` で解き、`state_dir` と同じ実体のパスなら真、違えば偽。`/proc` が無い環境では真（数え過ぎの側）。`/proc` があって読めない（プロセスが終わった・権限）なら None |
| `_reconcile` | 同上 | 新設 | `(state_dir: Path, panes: dict[str, int], now: float) -> Optional[list[str]]` | 名前の形が合い（`launch_ticket.check_name`）、起動の印が無く、`_owner` が真の名前に `write_launch(state_dir, 名前, "legacy", now)` を書き、書いた名前を返す。`_owner` が偽の名前は飛ばす。None の名前が 1 つでもあれば何も書かずに None を返す（C5） |
| `state_dir_of` | `src/ndf/identity.py` | 新設 | `(environ: Mapping[str, str]) -> Path` | `NDF_STATE_HOME`・`XDG_STATE_HOME`・`HOME` の `.local/state` の順で根を引き、プラグイン名を足す。`_state_home` は `os.environ` でこれを使う（11.0.0〜11.0.15 の `_state_home` と同じ規則） |
| `panes` | `src/ndf/domains/relay/tmux.py` | 新設 | `() -> Optional[dict[str, int]]` | `list-sessions -F '#{session_name}\t#{pane_pid}'` を 1 回打つ。終了コードと空・None の読み方は #4103 の `sessions` と同じ |
| `sessions` | 同上 | 変更 | 変えない | `panes()` の鍵の集合を返す（tmux を別に打たない） |

`Closure` は `NamedTuple("Closure", kind: str, closed_at: Optional[float])`。`kind` は `open`・`pending`・`settled`・`superseded`。`legacy` は `kind != "settled"`。

`closure.json` の形は `{"by": <閉じた側の inbox の番号>, "reconciled": <真偽>, "closed_at": <エポック秒か null>, "not_before": <time.monotonic() の秒か null>, "boot": <起動の識別子か null>}`。`boot` は Linux の `/proc/sys/kernel/random/boot_id`、読めなければ null。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `state` | 自分の受付箱のパスがあり、ディレクトリでない。または `closure.json` の `by` が自分の `inbox` より大きい | `superseded` |
| `state` | 旧い受付箱のどれかが通常のファイルでない（`lstat` で `S_ISREG` が偽・無い） | `open` |
| `state` | 全部が通常のファイルで、`closure.json` が無い・読めない・`reconciled` が真でない | `pending`（`closed_at` は読めた値か None） |
| `state` | 全部が通常のファイルで、`reconciled` が真 | `settled` |
| `settle` | `superseded` | 何もしない（脇の `.closing` も後の版が扱う） |
| `settle` | `superseded` でなく、脇の `<旧い受付箱>.closing` がある（どの状態でも） | 先に `_answer_old` を打つ（C10）。消しきれなくても状態の判定へ進む |
| `settle` | `settled` | 何もしない |
| `settle` | `open` | 1. 照合が要るかを決める。次のどれかなら要る（`need`）<br>・旧い受付箱のどれかがディレクトリ<br>・脇の `.closing` がある（C10 の前に見る）<br>・`closure.json` があり、`reconciled` が真でない（壊れている・`by` が自分より小さい版の書いたものを含む。前の閉鎖の中断と、前の版の照合待ちを引き継ぐ）<br>2. 何かを移す前に、`closure.json` を `{"by": <自分の inbox>, "reconciled": false, "closed_at": null, "not_before": null}` で書く（閉鎖の始まりの印。`need` に依らない）<br>3. 旧い受付箱ごとに `_close_one`。どれかが偽なら `open` を返す<br>4. 全部を置いた後に時刻を読み、`need` なら `{"by", "reconciled": false, "closed_at": wall(), "not_before": mono() + 猶予, "boot"}` を書いて `pending`、`need` でなければ `{"by", "reconciled": true, "closed_at": wall()}` を書いて `settled` を返す |
| `settle` | `pending` で `not_before` が無い・`boot` が今の起動と違う形で読めない | `not_before = mono() + 猶予` と `boot`・`closed_at` を書いて `pending`（C1 の 2 と 4 の間の中断の後。閉鎖が済んだ時点から猶予を取り直す） |
| `settle` | `pending` で `boot` が今と同じで `mono() < not_before` | 何もしない（tmux も `/proc` も読まない。C4） |
| `settle` | `pending` で試してよい（`mono() >= not_before`、または `boot` が今と違う＝再起動の後） | 次の順に照らす。どこかで止まったら `not_before = mono() + 猶予` を書いて `pending`<br>1. `_clients` が None か空でない（旧版の作成が終わったと確かめられない。r1-3）<br>2. `panes()` が None（C5 の不明）<br>3. `_reconcile` が None（所属を確かめられない名前がある）・`write_launch` が失敗<br>全部を通れば、記録に `legacy_reconciled` → `{"by", "reconciled": true, "closed_at"}` を書いて `settled` |

猶予は `limits.seconds("relay.stop-wait")`（契約の 30 秒）を直に読む。`hub.Context.stop_wait`（`NDF_RELAY_STOP_WAIT` で呼び出し元ごとに変えられる停止の待ち）は使わない。時刻の比較は `time.monotonic()`（Linux では起動からの単調な時計で、プロセスの間で共有される）だけで行い、壁時計を前へ進めても短くならない。`boot` が違えば前の起動の tmux のサーバーとクライアントは残らないので、待たずに試してよい。

`_clients` が 1 つでも返したら、次に試すのは見えた時点から猶予の後である。クライアントが消えた直後にサーバーが残りの要求を処理する間も、猶予がそれを覆う。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_close_one` | 通常のファイルを置けない | 脇へ移した後に旧版が作り直した（`IsADirectoryError`） | `os.rename` | 一時のファイルを消して偽を返す | 無し（一覧の `legacy_reason: open`） | 次の `hub.lock` の持ち主が作り直したディレクトリを脇へ移してやり直す |
| `_close_one` | 脇へ移せない | 脇の `.closing` が残る（`_answer_old` が消しきれなかった）・権限 | `os.rename` | 偽を返す | 同上 | 次の持ち主の C10 が脇を消した後にやり直す |
| `_answer_old` | 断りを書けない・消せない | 権限・容量 | `signals.replace_json`・`unlink` | 残りを残して偽を返す。`_close_one` は脇を残したまま通常のファイルを置く | 断りの届かない旧版の頼む側は待ち切れで終わる | 状態に依らず、次の `hub.lock` の持ち主の `settle` が C10 で続きから断る。同じ要求に同じ断りを書く |
| `state` | `closure.json` が壊れている | 途中で落ちた | `json.loads` | `pending`（`closed_at` は None） | `legacy_reason: pending` | `settle` が書き直す |
| `settle` | 旧版の tmux のクライアントが残る | 旧版のハブが強制終了し、`new-session` のクライアントが生き残った | `_clients` | 照合を確定しない（`not_before` を進める） | `legacy_reason: pending`。待ち切れの文に「旧版の tmux のクライアント（pid <pid>）が残っている」 | クライアントが終わってから猶予の後に照合する。止まったままなら人が pid を止める |
| `settle` | 所属を確かめられない | ペインのプロセスが終わった直後・権限 | `_owner` が None | 照合を確定しない | `legacy_reason: pending` | 猶予の後に読み直す |
| `settle` | `write_launch` が失敗 | 権限・容量 | `_reconcile` | `reconciled` を書かずに `pending` のまま返す | 同上 | 猶予の後の持ち主が照合し直す。印のある名前は飛ばす |
| `refuse_if_superseded` | 後の版が閉じた | 受付箱 4 の版の閉鎖 | `state` | 断りの文を返す（呼び手が `precondition` にする） | 「リレーの受付箱は後の版（<by>）が閉じた。新しい版の ndf で打ち直す」、終了コード 3 | 後の版の `ndf` で打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `CLOSURE_FILE` | 定数 | `inbox_closure.py` | `"closure.json"` |
| `ASIDE_SUFFIX` | 定数 | 同上 | `".closing"` |
| `LEGACY_REASON` | 定数 | 同上 | `"legacy"`（起動の印の理由） |
| `REFUSAL` | 定数 | 同上 | 「リレーの受付箱は新しい版（受付箱 <inbox>）へ移った。新しい版の ndf で打ち直す」 |
| 猶予 | 設定値 | `contracts/limits.json` の `relay.stop-wait` の `seconds` | 30 秒。`limits.seconds` で読み、環境変数で上書きしない |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 新しい版の `settle` と旧版のハブの処理 | 旧い受付箱・tmux | どちらも `hub.lock` の中 | 重ならない（V1） |
| 新しい版の `settle` と、強制終了した旧版のハブの生き残った tmux のクライアント | tmux のセッション | 錠を持たない。`_clients` が見える間は照合を確定せず、見えなくなってから猶予の後に一覧を読む | 何度試しても同じ。確定するまで起動は保留される |
| 新しい版の `settle` と旧版の頼む側の書き込み | 旧い受付箱のパス | 頼む側は排他を取らない。移す前に書かれた要求は脇で断られ、移した後は `mkdir` が `FileExistsError`、移した直後に作り直されたら `_close_one` が偽を返し次の回に閉じ直す | 何度打っても同じ |
| `settle` と錠を持たない一覧の `state` | `closure.json`・旧い受付箱 | `closure.json` は一時の名前から置き換える。閉鎖は何かを移す前に `reconciled: false` を書く | 一覧が「全部閉じた・`reconciled` が真」を読むのは、照合が要らないか照合が済んだ後だけ（V2） |
| この版の錠を持たない一覧と、後の版の `settle` の閉鎖 | `closure.json`・`requests-v3` | 後の版は何かを移す前に `closure.json` に自分の `by` を書く。一覧は `closure.json` を `state` の最後に読み、行を読み終えた後に `superseded_since` で読み直す | 一覧が `requests-v3` をディレクトリと読んだ直後に後の版が移しても、`closure.json` の読み直しで `superseded` に変わる。読み直しで変わらなければ、行を読み終えた時点で後の版はまだ閉鎖を始めておらず、後の版のセッションも無い（V7） |
| `_reconcile` と起動の途中の旧版のラッパー | ラッパーの錠 | 錠を試さない | ラッパーは止められない。印は `running` に負けるだけ |
| `_reconcile` と別の `NDF_STATE_HOME` のリレー | tmux の `ndf-lanes` のソケット・同じ名前 | 錠を共有しない（`hub.lock` も印も保存先ごと）。`_owner` がペインの環境で所属を確かめ、別の保存先の名前に印を書かない | 別の保存先のセッションは、この保存先の上限にも迷子の停止にも入らない |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_close_one` | `{"event": "inbox_closed", "inbox", "pending"}` | `hub-log.jsonl` | 要求の中身 |
| `_answer_old` | 要求 ID・`ok: false`・`summary`・`cause: precondition`・`inbox` | 同上 | 要求の中身（引数・作業ディレクトリ） |
| `settle` | `{"event": "legacy_reconciled", "marked", "skipped_foreign"}` | 同上 | ペインの環境の値 |
| `settle` | `{"event": "legacy_deferred", "reason": "clients" \| "tmux" \| "owner" \| "write", "pids"}`（`not_before` を進めたとき） | 同上 | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_answer_old` | `signals.replace_json`・`hub._log` | `hub.py` を読んだ | 使う |
| `_reconcile` | `launch_ticket.write_launch`・`read_launch`・`check_name`（#4103） | #4103 の詳細設計 | 使う。どれも `state_dir` を受ける |
| `settle` の tmux の一覧 | `Tmux.sessions`（#4103） | 同上 | `panes` を足し、`sessions` は `panes` から引く。`drain` と一覧は `_review_launches` と同じ回の結果を共有する |
| `_owner` | `identity._state_home` | `identity.py` と 11.0.0・11.0.11・11.0.15 のタグを読んだ | 規則を `state_dir_of` へ移して両方で使う |
| `state` | `identity.lock_held` | 読んだ | 使わない（錠の持ち主を推し量らない。決定 4） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `state` | 4 つの状態を返す | 旧い受付箱がシンボリックリンク → `open`。`closure.json` だけ `reconciled: true` で旧い受付箱がディレクトリ → `open`。`closure.json` の `by` が 4 → `superseded` | `closure.json` が壊れている → `pending` |
| `settle` | 旧い受付箱が無い保存先 → 1 回で `settled`・tmux も `/proc` も読まない。ディレクトリがある → `pending` → 猶予の後に `settled` | 猶予の 1 秒前は tmux を打たない。`NDF_RELAY_STOP_WAIT=1` の `Context` から打っても猶予は 30 秒のまま。`wall` を 1 時間進めても照合しない。`_answer_old` を遅くしても `not_before` は最後の通常のファイルを置いた後の `mono()` から数える | 一覧が None → `pending` のまま。`write_launch` が失敗 → `pending` のまま |
| `settle` の中断 | — | 次の各点で止めて打ち直す。どれも照合を経ずに `settled` にならない<br>・`requests/` を閉じ、`requests-v2`（元から無い）を置く前（r1-1 の例）<br>・2 の `closure.json` を書いた直後<br>・4 を書く前<br>・この版の `pending`（`by: 3`）の保存先で、受付箱 4 の版が `requests-v3`（作られていない）を閉じる | — |
| `settle` の試し直し | 一覧が None のとき、待ちの間に `drain` を 240 回打っても `list-sessions` は猶予ごとに 1 回。その間も `stop` は処理される | `_clients` が pid を返す間は `list-sessions` を打たない。消えた直後の回でも照合せず、猶予の後に照合する | 強制終了したハブの替え玉（`new-session` の生き残ったクライアントを模したプロセス）が残る間は `settled` にならない |
| `settle` の C10 | `settled` の保存先に脇の `.closing` が残る → 次の `drain` で断りが書かれ、脇が消える | `pending` の保存先でも同じ | 書けない → 状態は変わらず、次の回にまた試す |
| `_close_one` | ディレクトリ → 通常のファイル | 脇へ移した直後に作り直す → 偽、次の回で閉じる | 脇の `.closing` が残る → C10 が先に断ってから閉じる |
| `_answer_old` | 要求ごとに断りが書かれ、要求とディレクトリが消える | 形の違う要求・一時のファイルも断るか消す | 書けない → 偽、残りが次の回に断られる |
| `_reconcile` | 印の無い自分の保存先の名前にだけ書く | 名前の形が違う名前は飛ばす。印のある名前は書き直さない。別の `NDF_STATE_HOME` の環境で動くペインの名前は飛ばす | ペインの pid が消えている → None・何も書かない |
| `_reconcile` の置き場 | 照合の後、`launch_ticket.launch_names(state_dir)`・`hub.listing`・`hub._over_cap` が同じ名前を数える | `<state_dir>/relay/hub/relay/hub` が作られない | — |

## ハブ・一覧・頼む側（`hub`・`cli`・`launch.wrappers`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drain` | `domains/relay/hub.py` | 変更 | 変えない | `hub.lock` を取れたら、`inbox_closure.settle(ctx.state_dir, ctx.tmux, panes=…)` → `_review_launches` → `sweep` → 受付箱の処理の順に進める。照合と印の見直しが同じ回に tmux を要するときは `Tmux.panes` を 1 回だけ打つ。`settle` の結果が `settled` でなければ、`stop` だけを処理し、`spawn`・`replace`・`restart` は処理の錠を取らずに残す（C7） |
| `listing` | 同上 | 変更 | `(ctx: Context) -> Listing`（行の一覧と `Closure`） | `hub.lock` を待たずに試し、取れたら `settle` → `_review_launches` を打ってから放す。取れなければ `inbox_closure.state` を読む。その後は #4103 の順（券 → 印 → 錠）で行を読み、最後に `superseded_since` を打ち、真なら `Closure` を `superseded` にする（V7） |
| `submit` | 同上 | 変更 | 変えない | 券を取る前に `refuse_if_superseded` を打つ。`_write_request` の `FileExistsError`・`NotADirectoryError` も同じ断りにする（C9）。待ち切れのときは `state` の種類を文に足す |
| `_hub` | `domains/relay/cli.py` | 変更 | 変えない | `spawn`・`restart`・`stop`（`--all` を含む）では、名前の確かめの直後、`adopt`・`_admitting`・`_stop_all` の停止シグナルファイル・`submit` のどれよりも前に `refuse_if_superseded(ctx.state_dir)` を打ち、断りなら何も書かずに返す（受け入れ条件 6）。`ls` は結果の最上位に `legacy`（`kind != "settled"`）と `legacy_reason`（済みなら null）を載せる |
| `wrappers` | `progress/lane/launch.py` | 変更 | 変えない | 応答の `relay_protocol` が 3 以上で、`legacy` が偽のときだけ表を返す。`legacy` が無い応答は `relay_protocol` の条件で決まる（#4103 の前の版は既に None） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `drain` | 済みでない | 旧版の照合待ち・閉じきれない | `settle` の戻り | 起動を残し、停止だけ処理する | 頼む側は待つ。待ち切れれば `wait_timeout`（文に閉鎖の状態） | 済んだ後の `drain` が処理する |
| `_hub`・`submit` | 後の版が閉じた | 新しい版が `requests-v3` を閉じた | `refuse_if_superseded`・`_write_request` | 券を受けない・取らない・放す。停止シグナルファイルを置かない | `precondition`、終了コード 3 | 後の版の `ndf` で打ち直す |
| `listing` | `settle` が例外 | 保存先の権限 | `listing` | `state` を読んで続ける（`legacy: true`） | 一覧の `legacy_reason` | 次の一覧で再び試す |

`_hub` の確かめの後、副作用の前に後の版が閉じる窓は残る。そのとき `stop --all` が置いた停止シグナルファイルは本数を増やさず、`submit` が同じ断りを返す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `drain` | 済みの保存先で、記録と結果のファイルを開かない（読み取りを数える替え玉で 0 回） | 照合待ちで 4 種の要求 → `stop` だけ処理 | `settle` が `open` を返す → 同じく保留 |
| `listing` | `legacy`・`legacy_reason` が状態と一致する | `hub.lock` を別のプロセスが持つ間は `state` だけで決まる。`hub.lock` を取れなかった一覧が `requests-v3` を読んだ直後に、受付箱 4 の版の替え玉が `closure.json` に `by: 4` を書いて `requests-v3` を脇へ移し、旧い受付箱を閉じて `reconciled: true` を書く → 一覧は `superseded`（`legacy: true`）。替え玉が行を読んだ後に閉鎖を始めても同じ | — |
| `submit` | `superseded` で券も要求も作らない | 確かめた後に閉じられる → 同じ断り | — |
| `_hub` | `superseded` の保存先で、`stop --all` が停止シグナルファイルを 1 つも置かない（tmux の外のラッパーも含む）。`--ticket`・`--ticket-fd` を添えた `spawn` が `adopt` を打たず、券の `.req` の錠と中身が変わらない。`_admitting` も打たない | — | — |
| `wrappers` | `legacy: false` → 表 | `legacy` の欄が無い・`relay_protocol` が 3 → 表 | `legacy: true` → None |
