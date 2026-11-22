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

依存の向き: `crossreview/cli.py` → `adapters/write_gate.py`・`adapters/github.py`、`adapters/github.py` → `adapters/write_gate.py` → `filelock`・`githubkit.throttling`。`threads.py` と `throttle_decide.py` は adapters を import せず、待ちの終わりを知らせる関数（`block`）を引数で受ける。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/adapters/write_gate.py` | 新設 | 書き込みの要求にマシン全体の順番を取らせる githubkit の throttler | `filelock`・`githubkit.throttling`・`contracts.limits` | `WriteGate` | `kind_of`・`machine_gate`・`WriteGate.acquire`・`WriteGate.async_acquire`・`WriteGate.reserve`・`WriteGate.within`・`WriteGate.sent`・`WriteGate.block`・`Busy`（新設） |
| `src/ndf/adapters/github.py` | 変更 | `Client` に `WriteGate` を渡し、二次レート制限を `GitHubError` の属性で示す | `write_gate` | `GitHubError`・`RetryWithin`・`GitHub`・`Limit` | `client`・`RetryWithin.__init__`・`RetryWithin.__call__`・`GitHubError.__init__`・`_translated`・`_translating`・`GitHub.graphql`（変更）、`limit_of`・`Limit`（新設） |
| `src/ndf/domains/crossreview/threads.py` | 変更 | 行に付ける指摘をコミットごとのレビューで書き、閉じた理由を最初のコメントへ入れる | `throttle_decide`・`store`・`report_decide` | `Port`・`GitHubPort` | `sync`・`opening`・`patient`・`GitHubPort.__init__`（変更）、`finding_of`・`GitHubPort.open_review`・`Placed`（新設）、`_sync_one`・`GitHubPort._post` の間隔・`Pace.clock`（削除） |
| `src/ndf/domains/crossreview/throttle_decide.py` | 変更 | 打ち直しの待ちを指数的に延ばす | `contracts.limits`・`contracts.routing` | モジュール | `wait_before_retry`（変更）、`secondary_limited`・`spacing_wait`・`SPACING`（削除） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | マシンの `WriteGate` を作り、クライアントと打ち直しへ渡す | `write_gate` | `_github_hub` | `_github_hub`（変更） |
| `contracts/limits.json` | 変更 | 間隔と打ち直しの値 | — | — | `github.write-spacing`・`github.content-spacing`・`github.write-send-lock`（追加）、`cross-review.secondary-rate-limit`（変更）、`cross-review.thread-spacing`（削除） |
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
| `WriteGate.reserve` | 同上 | 新設 | `reserve(self, kind: str, limit: Optional[float] = None) -> float` | ロックの中で予約の時刻を決めて記録へ書き、今から予約までの秒を返す。`limit` を越える待ちなら記録を書かずに `Busy` を出す |
| `WriteGate.within` | 同上 | 新設 | `within(self, remaining: Callable[[], float]) -> BaseThrottler` | 同じ記録を使い、待ちを `remaining()` に収める throttler を返す（決定 10） |
| `Busy` | 同上 | 新設（例外） | `Busy(seconds: float)` | 順番の待ちが呼び手の予算を越えた |
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
| `acquire`（`within`） | 待ちが `remaining()` を越える | ほかのレーンの書き込み・待ちの終わり | 眠る前と送信のロックを待つ前に比べる | 予約を書かずに `Busy` を出す。`_translating` が `GitHubError(retry_after=待ちの秒)` へ写す | 呼び手の予算の切れの出力（今のとおり） | 呼び手の次の周回で打ち直せる |
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
| `within` | `remaining` 3 秒で待ち 60 秒 → `Busy(60)`・記録が変わらない・送らない | 待ちがちょうど `remaining()` なら待って送る | — |
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
| `GitHubError.__init__` | 同上 | 変更 | 今の引数に `secondary: bool = False, hinted: bool = False` を足す | 二次レート制限か・GitHub が待ちの秒を返したかを持つ |
| `_translated` | 同上 | 変更 | 変わらない | `limit_of` の結果で `secondary`・`hinted`・`retry_after`（`hinted` が偽なら `routing.backoff` の 1 回目の秒）を書く |
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
| `_translated` | `retry-after` のある 403 の二次レート制限 → `secondary` 真・`hinted` 真。一次の残量が正でヘッダー `retry-after` の無い本文 `secondary rate limit` の 403（githubkit の通常の `RequestFailed`）→ `secondary` 真・`hinted` 偽・`retry_after` 60 | 429 | 本文に語の無い 403 → `secondary` 偽 |
| `GitHub.graphql` | HTTP 200 で誤りの `message` が `secondary rate limit` → `secondary` 真の `GitHubError` で、`gate.block` が呼ばれる | — | ほかの GraphQL の誤りは今の `GraphQL の誤り: …` のまま |
| `RetryWithin.__call__` | 二次レート制限（`SecondaryRateLimitExceeded` と、本文で見分けた通常の 403）で `gate.block` が呼ばれる | 打ち直さない判断（待ちが残りを越える・最後の試行）でも呼ばれる | 一次の上限では呼ばない |

## 指摘のスレッド（`threads.py`）

満たす決定: 決定 4・5・6、受け入れ条件 1・2・6、不変条件 I4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Port`（Protocol） | `domains/crossreview/threads.py` | 変更 | `open_review(commit: str, comments: Sequence[Placed]) -> int` と `open_threads() -> list[prior_decide.Thread]` を足す | レビューを作るメソッドと、PR のスレッドを読むメソッド（`GitHubPort` は `Reader` として既に持つ） |
| `sync` | 同上 | 変更 | 変わらない | 設計文書の「処理の流れ」の 1〜6 の順で書き、印を残す |
| `opening` | 同上 | 変更 | `opening(finding: Mapping[str, Any], closed: Optional[str] = None) -> str` | `closed`（`closing` の文）があれば本文の末尾に足す |
| `finding_of` | 同上 | 新設 | `finding_of(body: str) -> Optional[str]` | 最初のコメントの本文（`opening` の形）から指摘の番号を読む。`MARKER` で始まらなければ None |
| `patient` | 同上 | 変更 | `patient(call: Callable[[], T], errors: type[Exception], sleep: Callable[[float], None] = time.sleep, block: Optional[Callable[[float], None]] = None) -> T` | `exc.secondary` で見分け、`wait_before_retry` の秒を `block` へ知らせてから眠る |
| `GitHubPort.__init__` | 同上 | 変更 | `pace` を `sleep: Callable[[float], None] = time.sleep` に、`block: Optional[Callable[[float], None]] = None` を足す | 間隔は `WriteGate` が持つので、時計を持たない |
| `GitHubPort.open_review` | 同上 | 新設 | `open_review(self, commit: str, comments: Sequence[Placed]) -> int` | `POST /pulls/{n}/reviews`（`commit_id`・`event: COMMENT`・`comments[]` の `path`・`line`・`side: RIGHT`・`body`）を書き、レビューの id を返す。PR の差分に無いファイルの指摘は呼び手が除いてから渡す |
| `Placed` | 同上 | 新設（型） | `NamedTuple(fid: str, path: str, line: int, body: str)` | レビューに載せる 1 件 |

- `sync`
  - 事前条件: `data` は cross-review の状態ファイルの中身
  - 事後条件: 書けた指摘と、PR に自分のスレッドがあった指摘の印に `comment` がある。この実行で閉じた指摘で最初のコメントに閉じた理由を入れたものは `replied` が真
  - 副作用: Port を通した GitHub への書き込みと、スレッドの読み取り（書く前に 1 回、レビューを送れたら後に 1 回）
- `patient`
  - 事後条件: `exc.secondary` が偽か、`wait_before_retry` が None なら例外をそのまま出す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `sync` | レビューの作成が 422 | 組の中のどれかの行が差分に無い | `open_review` の例外の `status` | 組の指摘を 1 件ずつ `open_thread` で書く（決定 5） | 無し（1 件ずつの失敗は今のとおり注意へ） | 無し |
| `sync` | レビューの作成がほかの理由で失敗 | 二次レート制限の打ち直しの上限・不達 | `open_review` の例外 | 組の指摘ごとに注意へ残し、ほかの組を続ける | 記録のコメントの注意に `指摘 <番号> のスレッドを書けなかった: …` | 公開できた状態を残さないので、打ち直しで書き直す（今のとおり） |
| `sync` | 書く前のスレッドを読めない | 不達など | `open_threads` の例外 | 何も書かずに注意へ残して返す（読めないまま書くと、保存の前に落ちた打ち直しで二重に作る） | 同上 | 打ち直せる（I4） |
| `sync` | 送った後のスレッドを読めない | 不達など | `open_threads` の例外 | 印を埋めずに注意へ残し、resolve を打たない | 同上 | 打ち直しは書く前の読み取りで印を埋める（I4） |
| `sync` | 読んだコメントに指摘の番号が無い | 本文が書き換えられた | `finding_of` が None | その指摘を注意へ残す | 同上 | 打ち直しても同じ注意が残る |
| `patient` | 二次レート制限が上限の回数まで続く | GitHub のブロック | `wait_before_retry` が None | 呼び出し元へ返す | 呼び出し元が注意へ残す | 次の起動で打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `sync` | 印に `comment` がある・この実行で閉じた・`replied` が偽 | 返信する（前の実行のスレッド） |
| `sync` | 印に `comment` がある・閉じた・`resolved` が偽 | resolve する |
| `sync` | 印が無い・PR に自分が書いたスレッドがあり、最初のコメントに番号がある | 書かずに印を埋める |
| `sync` | 印が無い・行が分かる・差分にあるファイル・ラウンドの head がある | コミットの組に入れる |
| `sync` | 印が無い・行が分からない（ファイルに付ける） | 1 件ずつ `open_thread` |
| `sync` | 収束の妨げにならない重さ・巻き直しの前に閉じた | 書かない（今のとおり） |

- 繰り返しの終わる条件: 指摘の一覧を 1 度だけ回し、組ごとにレビューを 1 回送る
- 計算量の注意: スレッドの読み取りは 1 回の実行で最大 2 回（スレッドが 100 件を超えるとページの数だけ）

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `sync` | 1 つのコミットの N 件 → `open_review` 1 回・`open_thread` 0 回（受け入れ条件 1）。2 つのコミット → 2 回。この実行で閉じた指摘は返信 0 回で本文に閉じた理由（受け入れ条件 2）。前の実行の印を持つ閉じた指摘は返信 1 回 | 指摘 0 件で何も書かない。行に付ける指摘 1 件 | 422 の組が 1 件ずつに落ちる（決定 5）。印を保存せずに打ち直すと、PR に自分のスレッドがある指摘で `open_review` が増えない。ほかの書き手のスレッドは拾わない（受け入れ条件 6） |
| `finding_of` | `opening` が作った本文から番号を読む | 閉じた理由を足した本文 | `MARKER` の無い本文は None |
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

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `wait_before_retry` | `hinted` 偽で attempt 1・2・3 → 60・120・240 | attempt 4 → None。`max_interval` で頭打ち | `limited` 偽 → None |
