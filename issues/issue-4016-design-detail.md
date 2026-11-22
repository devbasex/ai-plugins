# #4016 の詳細設計: GitHub への書き込みをまとめ、マシン全体で間を空ける

設計文書 [issue-4016-design.md](./issue-4016-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/limits.json
├── src/ndf/
│   ├── adapters/
│   │   ├── github.py
│   │   └── write_gate.py *
│   └── domains/crossreview/
│       ├── cli.py
│       ├── journal.py
│       ├── prior_decide.py
│       ├── threads.py
│       └── throttle_decide.py
└── tests/
    ├── adapters/
    │   ├── test_github.py
    │   └── test_write_gate.py *
    └── domains/crossreview/
        ├── test_crossreview_report.py
        └── test_crossreview_throttle.py
docs/glossary/glossary.json, docs/glossary.md
```

依存の向き: `crossreview/cli.py` → `adapters/write_gate.py`・`adapters/github.py`、`adapters/github.py` → `adapters/write_gate.py` → `slot_pool`（ロックの作り）・`filelock`・`githubkit.throttling`。`threads.py` と `throttle_decide.py` は adapters を import せず、待ちの終わりを知らせる関数（`block`）を引数で受ける。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/adapters/write_gate.py` | 新設 | 書き込みの要求にマシン全体の順番を取らせる githubkit の throttler | `slot_pool`・`filelock`・`githubkit.throttling`・`contracts.limits` | `WriteGate` | `kind_of`・`machine_gate`・`WriteGate.acquire`・`WriteGate.async_acquire`・`WriteGate.reserve`・`Reservation`・`WriteGate.withdraw`・`WriteGate.within`・`WriteGate.sent`・`WriteGate.block`・`Busy`・`pr_lock`（新設） |
| `src/ndf/adapters/github.py` | 変更 | `Client` に `WriteGate` を渡し、二次レート制限を `GitHubError` の属性で示す | `write_gate` | `GitHubError`・`RetryWithin`・`GitHub`・`Limit` | `client`・`RetryWithin.__init__`・`RetryWithin.__call__`・`GitHubError.__init__`・`_translated`・`_translating`・`GitHub.graphql`（変更）、`limit_of`・`Limit`・`reasons_of`（新設） |
| `src/ndf/domains/crossreview/threads.py` | 変更 | 行に付ける指摘をコミットごとのレビューで書き、閉じた理由を最初のコメントへ入れる | `throttle_decide`・`store`・`report_decide` | `Port`・`GitHubPort` | `sync`・`opening`・`patient`・`GitHubPort.__init__`・`GitHubPort.open_thread`（変更）、`finding_of`・`Mark`・`restored`・`replying`・`position_refused`・`GitHubPort.open_review`・`Placed`（新設）、`GitHubPort.changed`（`_changed` を公開）、`_sync_one`・`GitHubPort._post` の間隔・`Pace.clock`（削除） |
| `src/ndf/domains/crossreview/journal.py`・`prior_decide.py` | 変更 | 指摘に発生の値を書き、スレッドの返信を持つ | — | `Journal`・`Thread` | `Journal._raised_again`・新しい指摘の記録・`Thread`（変更） |
| `src/ndf/domains/crossreview/throttle_decide.py` | 変更 | 打ち直しの待ちを指数的に延ばす | `contracts.limits`・`contracts.routing` | モジュール | `wait_before_retry`（変更）、`secondary_limited`・`spacing_wait`・`SPACING`（削除） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | マシンの `WriteGate` を作り、クライアントと打ち直しへ渡す。同じ PR の公開を排他する | `write_gate` | `_github_hub`・`_post_report` | `_github_hub`・`_post_report`（変更） |
| `contracts/limits.json` | 変更 | 間隔と打ち直しの値 | — | — | `github.write-spacing`・`github.content-spacing`・`github.write-send-lock`・`github.pr-publish-lock`（追加）、`cross-review.secondary-rate-limit`（変更）、`cross-review.thread-spacing`（削除） |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 変更 | 用語を足す | — | — | 「書き込みの順番の記録」「内容を作る書き込み」「二次レート制限」（追加） |

## WriteGate（書き込みの順番）

満たす決定: 決定 1・2・3・7・10、受け入れ条件 3・4、不変条件 I1・I2・I3・I5・I6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `kind_of` | `adapters/write_gate.py` | 新設 | `kind_of(request: httpx.Request) -> Optional[str]` | 要求が読み取り（None）・書き込み（`WRITE`）・内容を作る書き込み（`CONTENT`）のどれかを決める |
| `machine_gate` | 同上 | 新設 | `machine_gate() -> WriteGate` | OS のユーザーに 1 つの記録（`gate_path()`）を使う `WriteGate` を作る |
| `gate_path` | 同上 | 新設 | `gate_path() -> Path` | `pwd.getpwuid(os.getuid()).pw_dir / ".local" / "state" / "ndf-github" / "write-gate.json"`。環境変数（`NDF_STATE_HOME`・`XDG_STATE_HOME`・`HOME`）を読まない |
| `WriteGate.__init__` | 同上 | 新設 | `__init__(self, path: Path, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None` | 記録のパスと時計を持つ |
| `WriteGate.acquire` | 同上 | 新設 | `acquire(self, request: httpx.Request) -> Generator[None, Any, Any]`（`@contextmanager`） | 書き込みなら `reserve` の秒だけ眠り、送信のロックを取って待ちの終わりを読み直してから通す。文脈の出口（送り終えた後）で `sent` を呼んで送信のロックを放す。読み取りはすぐ通す |
| `WriteGate.sent` | 同上 | 新設 | `sent(self, kind: str) -> None` | 送り終えた時刻から、記録の `next`（`CONTENT` なら `next_content` も）を延ばす |
| `WriteGate.async_acquire` | 同上 | 新設 | `async_acquire(self, request: httpx.Request) -> AsyncGenerator[None, Any]`（`@asynccontextmanager`） | `acquire` と同じ予約で、`anyio.sleep` で待つ（githubkit の抽象メソッドを満たす。NDF は同期の口だけを使う） |
| `WriteGate.reserve` | 同上 | 新設 | `reserve(self, kind: str, remaining: Optional[Callable[[], float]] = None) -> Reservation` | ロックの中で予約の時刻を決めて記録へ書き、今から予約までの秒と、取り消しに使う値を返す。`remaining` があれば、ロックの待ちを `min(LOCK_SECONDS, remaining())` に収め、ロックを取った時点で待ちが `remaining()` を越えるなら記録を書かずに `Busy` を出す |
| `Reservation` | 同上 | 新設（型） | `NamedTuple(wait: float, at: float, kind: str, before: tuple[float, float])` | 予約までの秒・予約の時刻・種類・書く前の `next` と `next_content` |
| `WriteGate.withdraw` | 同上 | 新設 | `withdraw(self, reservation: Reservation) -> None` | 予算の切れで送らなかった予約を取り消す。記録のロックを待たずに試し（取れなければ何もしない）、`next`（`CONTENT` なら `next_content` も）が自分の書いた値のままなら `before` へ戻す。後ろに別の予約があれば戻さない |
| `WriteGate.within` | 同上 | 新設 | `within(self, remaining: Callable[[], float]) -> BaseThrottler` | 同じ記録を使い、どのロックの待ちも眠りも `remaining()` に収める throttler を返す（決定 10） |
| `Busy` | 同上 | 新設（例外） | `Busy(seconds: float)` | 順番の待ち（眠り・記録のロック・送信のロック）が呼び手の予算を越えた。`seconds` は越えた待ちの秒（ロックの時間切れなら待った秒） |
| `WriteGate.block` | 同上 | 新設 | `block(self, seconds: float) -> None` | 待ちの終わりを `max(今の待ちの終わり, 今 + seconds)` へ延ばす |

- `kind_of`
  - 事後条件: メソッドが `GET`・`HEAD` なら None。`POST` で URL の path が `/graphql` なら、本文の JSON の `query` を先頭の空白とコメントを除いて読む。`mutation` で始まらなければ None、mutation の最初のフィールドの名前が `create` か `add` で始まれば `CONTENT`、ほかは `WRITE`。`/graphql` 以外の `POST` は `CONTENT`。`PATCH`・`PUT`・`DELETE` は `WRITE`
  - 副作用: 無い（純粋な関数）
- `WriteGate.reserve`
  - 事前条件: `kind` は `WRITE` か `CONTENT`
  - 事後条件: 記録の `next` は `予約 + github.write-spacing の秒`、`CONTENT` なら `next_content` も `予約 + github.content-spacing の秒`。予約は `max(今, next, blocked_until)`、`CONTENT` ならさらに `next_content` との大きいほう
  - 副作用: 記録のファイルとロックのファイル（`write-gate.json.lock`）を書く。ディレクトリが無ければ作る
  - 時計の戻り: 読んだ記録の `written_at` が今より `CLOCK_SLACK` 秒を越えて先なら、差の秒を `next`・`next_content`・`blocked_until` から引いてから計算する。書くたびに `written_at` を今にする。予約や `blocked_until` が今からどれだけ先かでは記録を捨てない
- `WriteGate.acquire`（書き込み）
  - 手順: (1) `reserve` の秒を眠る。(2) 送信のロック（`write-gate.send.lock`）を `github.write-send-lock` の秒まで待って取る。(3) 記録のロックの中で `blocked_until` を読み、今より後なら送信のロックを放して (1) へ戻る。(4) 通す。(5) 出口で `sent` を呼び、送信のロックを放す（例外でも放す）
  - 事後条件: 送る時刻は `blocked_until` 以後で、送信のロックを持つほかの書き込みと重ならない
- `WriteGate.within` の `acquire`（予算を持つ書き込み。決定 10）
  - 手順: 上の手順のすべてのロックの待ちを、取りに行く時点の `remaining()` で縛る。(1) `reserve(kind, remaining)`（記録のロックの待ちは `min(LOCK_SECONDS, remaining())`。取った時点で待ちが `remaining()` を越えるなら記録を書かずに `Busy`）。(2) 眠る。(3) 送信のロックの待ちを `min(github.write-send-lock, remaining())` にする。(4) 記録のロックの待ちを `min(LOCK_SECONDS, remaining())` にして `blocked_until` を読み直す。(5) 送信を許可する直前に `remaining()` を読み、0 以下なら送らない
  - 予算による打ち切り: 待ちの上限に `remaining()` を使ったロックの時間切れと、(5) の予算の切れは `Busy` にする。予約を書いた後（(3)〜(5)）なら、送信のロックを放し、`withdraw` で予約を取り消してから出す。I5 の「ロックを持たずに送る」経路へは落とさない（I5 へ落ちるのは、待ちの上限が `LOCK_SECONDS`・`github.write-send-lock` のときの時間切れだけ）
  - 事後条件: 送る時刻は、`acquire` を呼んだ時点から `remaining()` の秒より前。`Busy` を出したときは送っておらず、後ろに別の予約が無ければ記録は呼ぶ前と同じ
  - `sent` の記録のロックは送った後なので縛らない（送った分の間隔を記録から落とさない。I6）
- `WriteGate.sent`
  - 事後条件: `next` は `max(next, 今 + github.write-spacing の秒)`、`CONTENT` なら `next_content` も `max(next_content, 今 + github.content-spacing の秒)`
- `WriteGate.block`
  - 事前条件: `seconds` は今からの秒数（0 以上）。絶対時刻を渡さない
  - 事後条件: 記録の `blocked_until` が `max(今の blocked_until, 今 + seconds)` になる（縮めない）
  - 副作用: 記録のファイルを書く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `reserve` | ロックを 10 秒取れない | ほかのプロセスが固まっている | `FileLock.acquire(timeout)` の `Timeout` | 既定値で続ける（プロセスの中の前の書き込みから 2 つの方針の秒だけ待つ。I5） | 標準エラーへ `書き込みの順番の記録のロックを取れない: <パス>` の 1 行。終了コードは変えない | 次の書き込みでまたロックを試す |
| `reserve`・`block` | 記録を読めない | 壊れた JSON・形の違う値・権限が無い | `json.loads` の `ValueError`・`OSError`・値の型の確かめ | 壊れた JSON と形の違う値は空の記録として読み、書き直す。`OSError` はロックを取れないときと同じに扱う | 標準エラーへ `書き込みの順番の記録を読めない: <パス>: <理由>` の 1 行 | 書き直した後は通常どおり |
| `reserve`・`block`・`sent` | 壁時計が戻った | NTP の補正・手で時計を変えた | 記録の `written_at` − 今 > `CLOCK_SLACK` | 戻った秒の分だけ記録の時刻を手前へずらす（待ちの終わりの残りは保つ） | 無し | 無し |
| `acquire` | 送信のロックを `github.write-send-lock` の秒まで取れない | ほかのプロセスの送信が固まっている | `FileLock.acquire(timeout)` の `Timeout` | 送信のロックを持たずに送る（I5） | 標準エラーへ `書き込みの送信のロックを取れない: <パス>` の 1 行 | 次の書き込みでまた試す |
| `acquire`（`within`） | 待ちが `remaining()` を越える | ほかのレーンの書き込み・待ちの終わり | 記録のロックを取った時点で予約までの秒と `remaining()` を比べる | 予約を書かずに `Busy` を出す。`_translating` が `GitHubError(retry_after=待ちの秒)` へ写す | 呼び手の予算の切れの出力（今のとおり） | 呼び手の次の周回で打ち直せる |
| `acquire`（`within`） | 記録のロック・送信のロックを `remaining()` の秒までに取れない、送る直前に `remaining()` が 0 以下 | ほかのレーンの応答が遅い・ロックを持つプロセスが固まっている | 上限を `remaining()` にした `FileLock.acquire(timeout)` の `Timeout`・送る直前の `remaining()` | 送らずに `Busy` を出す。予約を書いた後なら送信のロックを放して `withdraw` で取り消す。I5 の経路（ロックを持たずに送る）へ落とさない | 同上 | 同上 |
| `acquire` | 眠りの途中で中断された | `KeyboardInterrupt` など | 呼び出し元 | 呼び出し元へ返す | 呼び出し元の出力 | 予約は残り、ほかの書き込みが最大 7.2 秒待つ（決定 2 の代償） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 記録の形 | 型（JSON） | `gate_path()`（`<OS のユーザーのホーム>/.local/state/ndf-github/write-gate.json`） | `{"version": 1, "next": 0.0, "next_content": 0.0, "blocked_until": 0.0, "written_at": 0.0}`（秒は UNIX 時刻。`written_at` は最後に書いたときの壁時計） |
| 送信のロック | ファイル | 記録と同じディレクトリの `write-gate.send.lock` | — |
| `github.write-send-lock` | 設定値 | `contracts/limits.json` | `interval` 60（送信のロックを待つ上限の秒） |
| `CLOCK_SLACK` | 定数 | `adapters/write_gate.py` | 1.0（壁時計が戻ったとみなす差の秒） |
| `WRITE`・`CONTENT` | 定数 | `adapters/write_gate.py` | `"write"`・`"content"` |
| `github.write-spacing`・`github.content-spacing` | 設定値 | `contracts/limits.json` | どちらも `interval`。1 秒と 7.2 秒（下の「打ち直しの待ちと設定」の表） |
| `LOCK_SECONDS` | 定数 | `adapters/write_gate.py` | 10.0 |

`limits.Policy` に欄を足さないため、2 つの間隔を別の方針に分ける。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じマシンの NDF のプロセス（レーン・cross-review・起票・リリース） | 記録のファイル・送信のロック | `filelock.FileLock`（`slot_pool.lock` で作る。カーネルのロックで、持ち主が消えると解ける）。ロックの中では読み・計算・書きだけを行い、眠らない | 予約は書いた時点で効く。落ちたプロセスの予約は時刻が過ぎれば効かなくなる |
| 同じプロセスのスレッド | 同上 | 取るたびに `slot_pool.lock(path)` で別の `FileLock` を作り、`WriteGate` の属性に持たない。`thread_local=False` の 1 つの `FileLock` をスレッドで共有すると、別のスレッドの取得が再入として通り、同時に同じ記録を書き換える。別のインスタンスはファイルを別に開くので、同じプロセスの中でもカーネルのロックが排他する | 同上 |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `reserve`・`block` | ロックを取れない・記録を読めないときの 1 行 | 標準エラー（警告） | 要求の本文・認証の値 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `WriteGate` | githubkit の `BaseThrottler`・`LocalThrottler` | `githubkit/throttling.py`・`core.py` を読んだ | 継ぐ。`LocalThrottler` は同時の本数だけで間を空けない |
| `WriteGate.reserve` | `slot_pool.lock`・`slot_pool.try_lock` | `grep -rn "FileLock" plugins/ndf/src` | ロックの作り方を `slot_pool.lock` に揃える（関数を import して使う） |
| `machine_gate`・`gate_path` | `identity.load().state_dir` | `identity.py` の `_state_home`（`NDF_STATE_HOME` → `XDG_STATE_HOME` → `~/.local/state`） | 使わない。環境変数で保存先が変わり、同じユーザーの NDF の間で共有されない |
| `kind_of` | 無し | `grep -rn "mutation" plugins/ndf/src/ndf/adapters` | 要求の分け方を持つ関数は無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `kind_of` | GET → None、POST `/repos/o/r/issues` → `CONTENT`、PATCH → `WRITE`、GraphQL の query → None、`resolveReviewThread` → `WRITE`、`addComment` → `CONTENT` | 先頭に空白・改行・`#` のコメントのある mutation | 本文が JSON でない GraphQL の POST は `WRITE` |
| `reserve` | 続けて 2 回 `WRITE` を取ると 2 回目の待ちが `github.write-spacing` の秒。`CONTENT` の後の `CONTENT` は `github.content-spacing` の秒 | 予約の時刻がちょうど今。`blocked_until` が今から 3,600 秒先（長い `retry-after`）でも捨てずに待つ。6 つの `CONTENT` が並んだ後ろの予約も捨てない | ロックが取れない（I5）・壊れた JSON・壁時計が 1 時間戻った記録（時刻がずれ、待ちの終わりの残りが保たれる） |
| `reserve`（8 つのスレッド） | 同じ `WriteGate` を 8 つのスレッドから同時に `reserve` すると、8 つの予約がすべて違い、隣どうしの差が `github.write-spacing` の秒以上 | — | — |
| `reserve`（2 つのプロセス） | 2 つのプロセスが同じ記録で同時に取ると、予約の差が `github.write-spacing` の秒以上（受け入れ条件 4） | — | — |
| `block` | `block(30)` の後の `reserve` の予約が今 + 30 秒以後（I3） | 短い `block` が長い待ちの終わりを縮めない | 記録を読めない |
| `gate_path` | `NDF_STATE_HOME`・`XDG_STATE_HOME`・`HOME` を変えた 2 つの子プロセスが同じパスを返し、同時に取った予約の差が `github.write-spacing` の秒以上 | — | — |
| `acquire` | 読み取りは眠らない。書き込みは `reserve` の秒だけ眠る | 待ち 0 秒 | — |
| `within` | `remaining` 3 秒で待ち 60 秒 → `Busy(60)`・記録が変わらない・送らない | 待ちがちょうど `remaining()` なら待って送る | 残り 1 秒でほかのプロセスが記録のロックを 2 秒持つ → 1 秒で `Busy`・記録が変わらない・送らない（I5 の 1 行を出さない）。残り 2 秒でほかのプロセスが送信のロックを持って応答を 60 秒待つ → 約 2 秒で `Busy`・送らない・予約が取り消されて記録が呼ぶ前と同じ。眠りの後に `remaining()` が 0 になる偽の時計 → 送らずに `Busy` |
| `acquire`（送る時刻） | 偽の時計と偽の送信で、2 つの書き込みの送信の開始の差が `github.write-spacing` の秒以上。先の予約の眠りを遅らせても、後の書き込みは先の送り終えた時刻から数える（I6） | 応答に 5 秒かかる送信の間、ほかのスレッドの送信が始まらない | 眠りの間にほかのプロセスが `block` すると、送らずに予約し直し、送信の時刻が待ちの終わり以後（I3）。送信のロックが取れないと送って 1 行を出す（I5） |

## GitHub のアダプター（`adapters/github.py`）

満たす決定: 決定 1・7・8、受け入れ条件 3・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `client` | `adapters/github.py` | 変更 | `client(auth: Optional[str] = None, transport: Optional[httpx.BaseTransport] = None, remaining: Optional[Callable[[], float]] = None, gate: Optional[WriteGate] = None) -> Client` | `gate` を `Client(throttler=...)` と `RetryWithin` へ渡す。`remaining` を渡されたら throttler は `gate.within(remaining)` にする（決定 10）。`gate` が None で `transport` も None なら `machine_gate()` を使い、`transport` を渡したとき（テスト）は付けない |
| `RetryWithin.__init__` | 同上 | 変更 | `__init__(self, remaining: Callable[[], float], gate: Optional[WriteGate] = None) -> None` | 待ちの終わりを知らせる先を持つ |
| `Limit` | 同上 | 新設（型） | `NamedTuple(seconds: float, hinted: bool)` | 二次レート制限の待ちの秒と、GitHub が待ちの秒を示したか |
| `limit_of` | 同上 | 新設 | `limit_of(status: int, headers: Mapping[str, str], message: str, now: float) -> Optional[Limit]` | 二次レート制限なら待ちの秒と `hinted`、ほかは None（下の事後条件）。REST の応答も GraphQL の誤りもここで見分ける |
| `RetryWithin.__call__` | 同上 | 変更 | 変わらない | githubkit の失敗（`SecondaryRateLimitExceeded` も通常の `RequestFailed` も）を `limit_of` で見て、二次レート制限なら打ち直すかに依らず（最後の試行でも）`gate.block(limit.seconds)` を呼ぶ。`hinted` が偽の秒は `routing.backoff(cross-review.secondary-rate-limit, retry_count)` |
| `GitHubError.__init__` | 同上 | 変更 | 今の引数に `secondary: bool = False, hinted: bool = False, reasons: tuple[str, ...] = ()` を足す | 二次レート制限か・GitHub が待ちの秒を返したか・拒否の理由の全文（切り詰めない）を持つ |
| `reasons_of` | 同上 | 新設 | `reasons_of(text: str) -> tuple[str, ...]` | 応答の本文の全体を JSON として読み、`message` と `errors[]` のそれぞれ（文字列ならそのまま、辞書なら `message`・`code`・`field`・`resource` を空白でつなぐ）を順に返す。JSON でなければ本文の全体を 1 つ返す。切り詰めない |
| `_translated` | 同上 | 変更 | 変わらない | `limit_of` の結果で `secondary`・`hinted`・`retry_after`（`hinted` が偽なら `routing.backoff` の 1 回目の秒）を書く。HTTP の拒否では、表示の文の先頭 300 字への切り詰めより前に `reasons_of(exc.response.text)` を `reasons` に書く（表示の文は今のまま） |
| `_translating` | 同上 | 変更 | `_translating(label: str, detail_body: bool = False, gate: Optional[WriteGate] = None)` | 写した `GitHubError` が `secondary` なら `gate.block(retry_after)` を呼んでから投げ直す（githubkit の打ち直しを通らない失敗も共有する）。`write_gate.Busy` も `GitHubError(retry_after=秒)` へ写す（決定 10） |
| `GitHub.graphql` | 同上 | 変更 | 変わらない | `GraphQLFailed`（HTTP 200 の誤り）と GraphQL の `PrimaryRateLimitExceeded` の誤りの `message` を `limit_of` に渡し、二次レート制限なら `secondary` の `GitHubError` にする（`_translating` が `block` する）。`GitHub` は `gate: Optional[WriteGate]` の欄を持ち、`client()` と同じ `WriteGate` を受ける |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `RetryWithin.__call__` | `block` が記録を書けない | `WriteGate` の異常 | `WriteGate.block` | 既定値で続ける（`WriteGate` が標準エラーへ出す） | `WriteGate` の 1 行 | 無し |
| `_translated` | 応答にヘッダーが無い | GitHub が返さない | `exc.response.headers` | `hinted` を偽にする | 無し | 無し |
| `limit_of` | `retry-after`・`x-ratelimit-reset` が数でない | 壊れたヘッダー | `int()` の `ValueError` | そのヘッダーが無いとして扱う（`hinted` 偽） | 無し | 無し |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `client` | 偽の transport で `Client` の throttler が `WriteGate` でない。`gate` を渡すとそれが付く | — | — |
| `limit_of` | `retry-after: 30` の 403 → 30 秒・`hinted` 真。`x-ratelimit-remaining: 0` と reset のある本文 `secondary rate limit` の 403 → reset までの秒・`hinted` 真。GraphQL の誤りの `message` に `secondary rate limit` → `hinted` 偽 | `x-ratelimit-reset` だけが付き `x-ratelimit-remaining` が正の 403 → `hinted` 偽（r1-14）。429 | 本文に語の無い 403 → None。数でない `retry-after` → `hinted` 偽 |
| `reasons_of` | `{"message": "Validation Failed", "errors": ["pull_request_review_thread.line must be part of the diff"]}` → 2 つの理由 | 位置の理由が本文の 300 字より後ろにある `errors[]`（前に長い理由が並ぶ）→ その理由が切れずに入る。辞書の `errors[]` | JSON でない本文 → 全文が 1 つ |
| `_translated` | `retry-after` のある 403 の二次レート制限 → `secondary` 真・`hinted` 真。一次の残量が正でヘッダー `retry-after` の無い本文 `secondary rate limit` の 403（githubkit の通常の `RequestFailed`）→ `secondary` 真・`hinted` 偽・`retry_after` 60 | 429 | 本文に語の無い 403 → `secondary` 偽 |
| `GitHub.graphql` | HTTP 200 で誤りの `message` が `secondary rate limit` → `secondary` 真の `GitHubError` で、`gate.block` が呼ばれる | — | ほかの GraphQL の誤りは今の `GraphQL の誤り: …` のまま |
| `RetryWithin.__call__` | 二次レート制限（`SecondaryRateLimitExceeded` と、本文で見分けた通常の 403）で `gate.block` が呼ばれる | 打ち直さない判断（待ちが残りを越える・最後の試行）でも呼ばれる | 一次の上限では呼ばない |

## 指摘のスレッド（`threads.py`・`journal.py`・`prior_decide.py`・`cli.py`）

満たす決定: 決定 4・5・6、受け入れ条件 1・2・6、不変条件 I4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Port`（Protocol） | `domains/crossreview/threads.py` | 変更 | `open_review(commit: str, comments: Sequence[Placed]) -> int`・`open_threads() -> list[prior_decide.Thread]`・`changed() -> frozenset[str]` を足す | レビューを作る・PR のスレッドを読む（`GitHubPort` は `Reader` として既に持つ）・PR の変更ファイルを返す |
| `GitHubPort.changed` | 同上 | 変更（今の `_changed` を公開の名前にする） | `changed(self) -> frozenset[str]` | PR の変更ファイル（1 回だけ読み、`open_thread` と共有する） |
| `sync` | 同上 | 変更 | 変わらない | 設計文書の「処理の流れ」の 1〜7 の順で書き、印を残す。差分の判定は `sync` が `port.changed()` で 1 度だけ行う |
| `opening` | 同上 | 変更 | `opening(finding: Mapping[str, Any], closed: Optional[str] = None) -> str` | `MARKER` の次の行に `<!-- ndf-occurrence: <finding["occurrence"]> -->`（`occurrence` が無ければ省く）を置き、`closed`（`closing` の文）があれば本文の末尾に `CLOSED_HEAD` の行と並べて足す |
| `replying` | 同上 | 新設 | `replying(finding: Mapping[str, Any], closed: str) -> str` | 返信の本文（`closed` と `occurrence` の行） |
| `finding_of` | 同上 | 新設 | `finding_of(body: str) -> Optional[Mark]` | 最初のコメントの本文（`opening` の形）から指摘の番号・`occurrence`・閉じた理由を本文に持つかを読む。`MARKER` で始まらなければ None |
| `Mark` | 同上 | 新設（型） | `NamedTuple(fid: str, occurrence: Optional[str], closed: bool)` | 本文から読んだ指摘の印 |
| `restored` | 同上 | 新設 | `restored(finding: Mapping[str, Any], thread: prior_decide.Thread) -> Optional[dict[str, Any]]` | `thread.trusted` が真で、`finding_of(thread.body)` の番号と `occurrence` が指摘と同じ（指摘の `occurrence` が None なら照合しない）なら、`{"comment": thread.comment, "replied": 本文に閉じた理由がある か thread.replies に同じ occurrence の自分の返信がある, "resolved": thread.resolved}` を返す。ほかは None |
| `position_refused` | 同上 | 新設 | `position_refused(status: int, reasons: Sequence[str]) -> bool` | 422 で、`reasons`（`GitHubError.reasons`。表示用に切り詰める前の検証エラーのすべて）のどれかが `POSITION_PHRASES` を含み、どれも `REFUSAL_PHRASES`（スパム・二次レート制限）を含まない（行の位置の誤りで、1 件ずつなら救える）か。表示の文（`str(exc)`）は見ない |
| `GitHubPort.open_thread` | 同上 | 変更 | 変わらない | 行に付ける投稿が断られたとき、ファイルへ付け直すのは `position_refused(exc.status, exc.reasons)` が真のときだけにする（今は状態の 422 だけで付け直す）。スパム・分類不能の 422 はその個別投稿を打ち切って例外を出す（付け直しの書き込みを足さない）。`_place` のパスの解決の判定（`path_unresolved`）も `exc.reasons` を見る |
| `patient` | 同上 | 変更 | `patient(call: Callable[[], T], errors: type[Exception], sleep: Callable[[float], None] = time.sleep, block: Optional[Callable[[float], None]] = None) -> T` | `exc.secondary` で見分け、`wait_before_retry` の秒を `block` へ知らせてから眠る |
| `GitHubPort.__init__` | 同上 | 変更 | `pace` を `sleep: Callable[[float], None] = time.sleep` に、`block: Optional[Callable[[float], None]] = None` を足す | 間隔は `WriteGate` が持つので、時計を持たない |
| `GitHubPort.open_review` | 同上 | 新設 | `open_review(self, commit: str, comments: Sequence[Placed]) -> int` | すべての `Placed.body` を 1 回の `self.publisher.guarded(*bodies)` に通し（今の `_safe` と同じ検査。資格情報・非公開のリポジトリの参照を除く）、その結果で `POST /pulls/{n}/reviews`（`commit_id`・`event: COMMENT`・`comments[]` の `path`・`line`・`side: RIGHT`・`body`）を書き、レビューの id を返す。差分の判定はしない（`sync` が済ませて渡す） |
| `Placed` | 同上 | 新設（型） | `NamedTuple(fid: str, path: str, line: int, body: str)` | レビューに載せる 1 件（`body` は検査の前の本文） |
| `THREADS_QUERY` | 同上 | 変更 | — | スレッドの `comments(first:1)` に加えて `replies: comments(last:20){nodes{databaseId body author{login}}}` を読む（要求の数は変わらない） |
| `prior_decide.Thread` | `domains/crossreview/prior_decide.py` | 変更 | `replies: tuple[str, ...] = ()` を足す | 今の認証の login が書いた返信の本文（`GitHubPort.open_threads` が `_trusted` で選ぶ） |
| `Journal._raised_again`・新しい指摘の記録 | `domains/crossreview/journal.py` | 変更 | 変わらない | 指摘を初めて記録するときと、直した指摘が再発したとき（`revived` に入れるとき）に `occurrence` を `uuid.uuid4().hex[:12]` で書き直す |
| `_post_report` | `domains/crossreview/cli.py` | 変更 | 変わらない | `with write_gate.pr_lock(repo, pr):` の中で状態ファイルを読み、`sync`・印の保存・記録のコメントを行う |
| `write_gate.pr_lock` | `adapters/write_gate.py` | 新設 | `pr_lock(repo: str, pr: int) -> FileLock` | 書き込みの順番の記録と同じディレクトリの `pr/<所有者>__<名前>__<番号>.lock` を `slot_pool.lock` で作る（取るたびに別のインスタンス）。待つ上限は `github.pr-publish-lock` の秒 |

- `sync`
  - 事前条件: `data` は cross-review の状態ファイルの中身で、呼び手が同じ PR の公開のロックを持つ
  - 事後条件: 書けた指摘と、PR から復元した指摘の印に `comment` がある。この実行で閉じた指摘で最初のコメントに閉じた理由を入れたもの（レビューでも 1 件ずつの作成でも）は `replied` が真。復元した指摘の `replied`・`resolved` は PR の状態と同じ
  - 副作用: Port を通した GitHub への書き込みと、読み取り（変更ファイルを 1 回、スレッドを書く前に 1 回、スレッドを 1 つでも作ったら後に 1 回）
- `patient`
  - 事後条件: `exc.secondary` が偽か、`wait_before_retry` が None なら例外をそのまま出す
- 自分の判定: 「自分が書いた」は `prior_decide.Thread.trusted`（`GitHubPort._trusted`。書き手の login が、GraphQL の `viewer` で 1 回だけ読んだ今の認証の login と同じ）で決める。`GET /user` は足さない。Actions の bot と利用者のように実行ごとに token が違うと、前の実行のスレッドは `trusted` が偽で復元しない（他人の同じ書式のコメントを拾わない側に倒す）。そのときは保存の前に落ちた分だけ二重に書きうる（落ちた上に token が替わったときだけ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `sync` | レビューの作成が行の位置の 422 | 組の中のどれかの行が差分に無い | `open_review` の例外を `position_refused` で見る | 組の指摘を 1 件ずつ `open_thread` で書く（決定 5） | 無し（1 件ずつの失敗は今のとおり注意へ） | 無し |
| `sync` | レビューの作成がほかの 422 | スパムの判定・原因の分からない拒否 | `position_refused` が偽 | 展開せず、組の指摘ごとに注意へ残し、ほかの組を続ける | 記録のコメントの注意に `指摘 <番号> のスレッドを書けなかった: …` | 打ち直しで書き直す |
| `sync` | レビューの作成がほかの理由で失敗 | 二次レート制限の打ち直しの上限・不達 | `open_review` の例外 | 組の指摘ごとに注意へ残し、ほかの組を続ける | 同上 | 公開できた状態を残さないので、打ち直しで書き直す（今のとおり） |
| `sync` | 書く前にスレッド・変更ファイルを読めない | 不達など | `open_threads`・`changed` の例外 | 何も書かずに注意へ残して返す（読めないまま書くと、保存の前に落ちた打ち直しで二重に作る） | 同上 | 打ち直せる（I4） |
| `sync` | 作った後のスレッドを読めない | 不達など | `open_threads` の例外 | レビューの指摘の印を埋めずに注意へ残し、resolve を打たない | 同上 | 打ち直しは書く前の読み取りで印を復元する（I4） |
| `sync` | 読んだコメントに指摘の番号が無い | 本文が書き換えられた | `finding_of` が None | その指摘を注意へ残す | 同上 | 打ち直しても同じ注意が残る |
| `_post_report` | 同じ PR の公開のロックを `github.pr-publish-lock` の秒まで取れない | 同じ PR の別の公開が続いている | `FileLock.acquire(timeout)` の `Timeout` | 書かずに `ReportFailed` | `PR #<番号> へ cross-review の記録を書けなかった: …`（今の公開の失敗と同じ） | 打ち直せる |
| `patient` | 二次レート制限が上限の回数まで続く | GitHub のブロック | `wait_before_retry` が None | 呼び出し元へ返す | 呼び出し元が注意へ残す | 次の起動で打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `sync` | 差分に無いファイルの指摘 | 書かない（記録のコメントの表に載る。今のとおり） |
| `sync` | 印に `comment` がある・この実行で閉じた・`replied` が偽 | `replying` の本文で返信する（前の実行のスレッド） |
| `sync` | 印に `comment` がある・閉じた・`resolved` が偽 | resolve する |
| `sync` | 印が無い・`restored` が印を返す | 書かずにその印を使う（`replied`・`resolved` が真なら返信・resolve もしない） |
| `sync` | 印が無い・行が分かる・差分にあるファイル・ラウンドの head がある | コミットの組に入れる |
| `sync` | 印が無い・行が分からない（ファイルに付ける）・組が行の位置の 422 | 1 件ずつ `open_thread`。本文は `opening(finding, closed)`。作れたら `comment` と（閉じていれば）`replied` を埋める |
| `sync` | この実行でスレッドを 1 つでも作った | スレッドを 1 回読み直し、レビューの指摘の `comment` を埋めてから resolve へ進む（resolve はこの読み取りの手元の id を使う） |
| `sync` | 収束の妨げにならない重さ・巻き直しの前に閉じた | 書かない（今のとおり） |

- 繰り返しの終わる条件: 指摘の一覧を 1 度だけ回し、組ごとにレビューを 1 回送る
- 計算量の注意: スレッドの読み取りは 1 回の実行で最大 2 回（スレッドが 100 件を超えるとページの数だけ）。変更ファイルの読み取りは 1 回

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `occurrence` | 指摘の欄 | 状態ファイルの `findings[]` | `uuid4` の 16 進の先頭 12 字。初めての記録と再発で書く |
| `OCCURRENCE_LINE` | 定数 | `threads.py` | `<!-- ndf-occurrence: {value} -->` |
| `CLOSED_HEAD` | 定数 | `threads.py` | `**この実行で閉じた**`（`finding_of` が閉じた理由の有無を読む目印） |
| `POSITION_PHRASES` | 定数 | `threads.py` | `must be part of the diff`・`could not be resolved`・`diff_hunk`・`line must be`（今の `_PATH_UNRESOLVED` を含める） |
| `REFUSAL_PHRASES` | 定数 | `threads.py` | `submitted too quickly`・`secondary rate limit`・`spam`（どれかを含む理由があれば、位置の理由があっても展開・付け直しをしない） |
| `github.pr-publish-lock` | 設定値 | `contracts/limits.json` | `interval` 900（同じ PR の公開のロックを待つ上限の秒） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `sync` | 1 つのコミットの N 件 → `open_review` 1 回・`open_thread` 0 回（受け入れ条件 1）。2 つのコミット → 2 回。この実行で閉じた指摘は返信 0 回で本文に閉じた理由（受け入れ条件 2）。前の実行の印を持つ閉じた指摘は返信 1 回 | 指摘 0 件で何も書かない。行に付ける指摘 1 件。差分に無いファイルの指摘を含む組 → レビュー 1 回で 422 にならず、外の指摘は書かれず記録のコメントの表に残る・`changed` 1 回 | 行の位置の 422 の組が 1 件ずつに落ちる（決定 5）。スパムの判定の 422 は `open_thread` 0 回で注意。印を保存せずに打ち直すと、PR に自分のスレッドがある指摘で `open_review` が増えない。ほかの書き手のスレッドは拾わない（受け入れ条件 6） |
| `sync`（中断の後の再実行） | 偽の Port で、レビューの作成・1 件ずつの作成・返信・resolve のそれぞれの直後に例外で止め、印を保存せずに打ち直すと、どの書き込みも 2 回目が 0 回。閉じた理由を入れたレビューの直後に止めると、打ち直しは返信 0 回・resolve 1 回 | 返信の後・resolve の前に止めると、打ち直しは返信 0 回・resolve 1 回 | — |
| `sync`（発生の照合） | 再発した指摘（新しい `occurrence`）は、同じ番号の resolve 済みの古いスレッドに結び付かず新しく書かれる | `occurrence` の無い前の版の指摘は復元せず書く | 別の状態ファイルの同じ番号（`occurrence` が違う）のスレッドを拾わない |
| `sync`（resolve の id） | ファイルに付けた閉じた指摘と 422 の後の 1 件ずつの閉じた指摘で、作成の後のスレッドの読み取りが 1 回、resolve のたびに読み直さない | — | — |
| `_post_report`（同時の公開） | 同じ PR への 2 つの `_post_report` を 2 つのスレッドで同時に走らせると、どの指摘のスレッドの作成も 1 回 | — | ロックを取れないと書かずに `ReportFailed` |
| `GitHubPort.open_review` | 偽の transport が受けた `comments[].body` のすべて（閉じた理由を含む）が `guarded` の結果で、資格情報の形の文字列と非公開のリポジトリの参照が残らない。`guarded` の呼び出しは 1 回 | 1 件の組 | — |
| `finding_of` | `opening` が作った本文から番号と `occurrence` を読む | 閉じた理由を足した本文 → `closed` 真 | `MARKER` の無い本文は None |
| `position_refused` | `must be part of the diff` の 422 → 真 | `could not be resolved` の 422 → 真。位置の理由が `errors[]` の 3 つ目で、本文の 300 字より後ろにある 422 → 真（組が 1 件ずつに落ちる） | `was submitted too quickly` の 422・語の無い 422・403 → 偽。位置の理由とスパムの理由が並ぶ 422 → 偽 |
| `GitHubPort.open_thread` | 行の投稿が位置の 422 → ファイルへ付け直して 2 回目の投稿 | — | 行の投稿がスパムの 422（`was submitted too quickly`）・語の無い 422 → 付け直さず投稿 1 回で例外 |
| `patient` | `hinted` 偽の二次レート制限が 3 回続くと 60・120・240 秒を眠り、4 回目で例外（受け入れ条件 5）。`block` が同じ秒で呼ばれる | `hinted` 真なら `retry_after` を眠る | `secondary` 偽の失敗は眠らずに出す |

## 打ち直しの待ち（`throttle_decide.py`）と設定

満たす決定: 決定 8、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `wait_before_retry` | `domains/crossreview/throttle_decide.py` | 変更 | `wait_before_retry(limited: bool, attempt: int, retry_after: Optional[float], hinted: bool) -> Optional[float]` | `attempt` が `attempts` 以上か `limited` が偽なら None。`hinted` なら `retry_after`、無ければ `routing.backoff(policy, attempt - 1, None, None)` |
| `_github_hub` | `domains/crossreview/cli.py` | 変更 | 変わらない | `gate = write_gate.machine_gate()` を作り、`gh.client(gate=gate)`・`GitHub(..., gate=gate)`・`GitHubPort(..., block=gate.block)`・記録のコメントの `patient(..., block=gate.block)` へ渡す |

`contracts/limits.json` の変更:

| 方針 | 前 | 後 |
| --- | --- | --- |
| `cross-review.secondary-rate-limit` | `attempts` 3・`interval` 60 | `attempts` 4・`interval` 60・`factor` 2・`max_interval` 600 |
| `cross-review.thread-spacing` | `interval` 1 | 消す（`github.write-spacing` が持つ） |
| `github.write-spacing` | — | `interval` 1（どの書き込みも前の書き込みから空ける秒） |
| `github.content-spacing` | — | `interval` 7.2（内容を作る書き込みを前の内容を作る書き込みから空ける秒。3,600 秒 ÷ 500 件） |
| `github.write-send-lock` | — | `interval` 60（送信のロックを待つ上限の秒。githubkit の既定の読み取りの時間切れより長く取る） |
| `github.pr-publish-lock` | — | `interval` 900（同じ PR の公開のロックを待つ上限の秒。指摘 100 件の公開が 7.2 秒ずつ並んでも収まる長さ） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `wait_before_retry` | `hinted` 偽で attempt 1・2・3 → 60・120・240 | attempt 4 → None。`max_interval` で頭打ち | `limited` 偽 → None |
