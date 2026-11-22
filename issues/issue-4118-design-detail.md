# #4118 の詳細設計: 旧い版の受付箱の閉鎖と旧版の照合

設計文書は [issue-4118-design.md](./issue-4118-design.md) にある。状態と事象の表（C1〜C9）と不変条件（V1〜V6）が振る舞いの正本で、この文書の関数はどれもその 1 行を実装する。パスは `plugins/ndf/` からの相対で書く。#4103 の詳細設計（[issue-4103-design-detail.md](./issue-4103-design-detail.md)）の `drain`・`listing`・`_review_launches`・`write_launch`・`Tmux.sessions` の上に乗る。

## 地図

```text
plugins/ndf/
├── contracts/relay-protocol.json            # inbox に inboxes（版ごとの受付箱の名前の表）を足す
├── docs/contracts/commands.md               # relay ls の legacy・legacy_reason、後の版が閉じたときの断り
├── src/ndf/domains/relay/
│   ├── inbox_closure.py                     # state*・settle*・_close_one*・_answer_old*・_reconcile*・own_inbox*・retired_inboxes*
│   ├── hub.py                               # submit・drain・listing・REQUESTS_DIR
│   └── cli.py                               # ls の出力に legacy・legacy_reason
└── src/ndf/progress/lane/launch.py          # wrappers が legacy を見る
```

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/inbox_closure.py` | 新設 | 旧い版の受付箱を閉じ、旧版の跡を照らし、閉鎖の状態を決める | `ndf.contracts.facts`・`core/launch_ticket`・`domains/relay/signals`・`domains/relay/tmux` | `Closure`（新設） | `own_inbox`・`retired_inboxes`・`state`・`settle`・`_close_one`・`_answer_old`・`_reconcile`（新設） |
| `src/ndf/domains/relay/hub.py` | 変更 | `drain` と一覧が閉鎖を通し、済むまで起動を保留する。頼む側は後の版の閉鎖で断る | `inbox_closure` | — | `submit`・`drain`・`listing`・`_write_request`（変更）、`REQUESTS_DIR`（表から引く） |
| `src/ndf/domains/relay/cli.py` | 変更 | `ls` の結果の最上位に `legacy`・`legacy_reason` を載せる | `hub` | — | `_hub`（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | `legacy` が真の応答を None にする | — | — | `wrappers`（変更） |
| `contracts/relay-protocol.json` | 変更 | 版ごとの受付箱の名前の表を持つ | — | — | — |

## 受付箱の閉鎖（`inbox_closure`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `own_inbox` | `domains/relay/inbox_closure.py` | 新設 | `() -> str` | `relay-protocol.json` の `inboxes[str(inbox)]` を返す（`hub.REQUESTS_DIR` の出所） |
| `retired_inboxes` | 同上 | 新設 | `() -> tuple[str, ...]` | `inboxes` のうち番号が `inbox` より小さい名前を番号の順に返す（今は `requests`・`requests-v2`） |
| `state` | 同上 | 新設 | `(base: Path) -> Closure` | 閉鎖の状態を決める（V5）。読むのは自分の受付箱・旧い受付箱の `lstat` と `closure.json` だけ。順は `superseded` → `open` → `pending` / `settled` |
| `settle` | 同上 | 新設 | `(base: Path, tmux: Tmux, now: float, grace: float, sessions: Optional[Callable[[], Optional[set[str]]]] = None) -> Closure` | `hub.lock` の持ち主だけが呼ぶ。`open` なら閉じ（C1〜C3）、`pending` で猶予を過ぎていれば照合し（C5）、呼んだ後の状態を返す。`sessions` は `_review_launches` と同じ回の tmux の一覧を共有するときに渡す |
| `_close_one` | 同上 | 新設 | `(base: Path, name: str) -> bool` | 旧い受付箱 1 つを閉じる（C2）。脇の `.closing` があれば先に `_answer_old` を打ち、ディレクトリなら脇へ移して `_answer_old`、無ければ通常のファイルを一時の名前から `os.rename` で置く。置けたら真 |
| `_answer_old` | 同上 | 新設 | `(base: Path, aside: Path) -> None` | 脇の `.closing` の `*.json` ごとに断りの結果を `results/<名前>` へ書き、要求を消し、記録に 1 行書く。最後にディレクトリごと消す（C3） |
| `_reconcile` | 同上 | 新設 | `(base: Path, names: set[str], now: float) -> list[str]` | 名前の形が合い（`launch_ticket.check_name`）、起動の印の無い名前に `write_launch(…, "legacy", now)` を書き、書いた名前を返す（C5） |

`Closure` は `NamedTuple("Closure", kind: str, closed_at: Optional[float])`。`kind` は `open`・`pending`・`settled`・`superseded`。`legacy` は `kind != "settled"`。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `state` | 自分の受付箱のパスがあり、ディレクトリでない | `superseded` |
| `state` | 旧い受付箱のどれかが通常のファイルでない（`lstat` で `S_ISREG` が偽・無い） | `open` |
| `state` | 全部が通常のファイルで、`closure.json` が無い・読めない・`reconciled` が真でない | `pending`（`closed_at` は読めた値か None） |
| `state` | 全部が通常のファイルで、`reconciled` が真 | `settled` |
| `settle` | `superseded` か `settled` | 何もしない |
| `settle` | `open` | 1. 旧い受付箱のどれかがディレクトリか、脇の `.closing` があれば、`closure.json` を `{"closed_at": now, "reconciled": false}` で書く（閉じる前）<br>2. 旧い受付箱ごとに `_close_one`。どれかが偽なら `open` を返す（ファイルを置けなかった）<br>3. 1 で書かなかった（照合が要らない）なら `{"closed_at": now, "reconciled": true}` を書いて `settled`、書いたなら `pending` を返す（照合は次の回以降） |
| `settle` | `pending` で `closed_at` が None | `{"closed_at": now, "reconciled": false}` を書いて `pending`（C1 の中断の後。安全側） |
| `settle` | `pending` で `now < closed_at + grace` | 何もしない（tmux を打たない。C4） |
| `settle` | `pending` で猶予を過ぎ、一覧が None | 何もしない（C5 の不明） |
| `settle` | `pending` で猶予を過ぎ、一覧が名前の集合 | `_reconcile` → 記録に `legacy_reconciled` → `reconciled: true` を書いて `settled` |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_close_one` | 通常のファイルを置けない | 脇へ移した後に旧版が作り直した（`IsADirectoryError`） | `os.rename` | 一時のファイルを消して偽を返す | 無し（一覧の `legacy_reason: open`） | 次の `hub.lock` の持ち主が作り直したディレクトリを脇へ移してやり直す |
| `_close_one` | 脇へ移せない | 脇の `.closing` が残る（先に `_answer_old` が消しきれなかった）・権限 | `os.rename` | 偽を返す | 同上 | 同上 |
| `_answer_old` | 断りを書けない・消せない | 権限・容量 | `signals.replace_json`・`unlink` | 残りを残して返す（`_close_one` は脇を残したまま続ける） | 同上 | 次の持ち主が続きから断る。同じ要求に同じ断りを書く |
| `state` | `closure.json` が壊れている | 途中で落ちた | `json.loads` | `pending`（`closed_at` は None） | `legacy_reason: pending` | `settle` が書き直す |
| `settle` | `write_launch` が失敗 | 権限・容量 | `_reconcile` | `reconciled` を書かずに `pending` のまま返す | 同上 | 次の持ち主が照合し直す。印のある名前は飛ばす |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `CLOSURE_FILE` | 定数 | `inbox_closure.py` | `"closure.json"` |
| `ASIDE_SUFFIX` | 定数 | 同上 | `".closing"` |
| `LEGACY_REASON` | 定数 | 同上 | `"legacy"`（起動の印の理由） |
| `REFUSAL` | 定数 | 同上 | 「リレーの受付箱は新しい版（受付箱 <inbox>）へ移った。新しい版の ndf で打ち直す」 |
| 猶予 | 設定値 | `contracts/limits.json` の `relay.stop-wait` | 30 秒（`hub.Context.stop_wait.seconds`） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 新しい版の `settle` と旧版のハブの処理 | 旧い受付箱・tmux | どちらも `hub.lock` の中 | 重ならない（V1） |
| 新しい版の `settle` と旧版の頼む側の書き込み | 旧い受付箱のパス | 頼む側は排他を取らない。移す前に書かれた要求は脇で断られ、移した後は `mkdir` が `FileExistsError`、移した直後に作り直されたら `_close_one` が偽を返し次の回に閉じ直す | 何度打っても同じ |
| `settle` と錠を持たない一覧の `state` | `closure.json`・旧い受付箱 | `closure.json` は一時の名前から置き換える。照合を要するときは閉じる前に偽を書く | 一覧が「全部閉じた・`reconciled` が真」を読むのは、照合が要らないか照合が済んだ後だけ（V2） |
| `_reconcile` と起動の途中の旧版のラッパー | ラッパーの錠 | 錠を試さない | ラッパーは止められない。印は `running` に負けるだけ |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_close_one` | `{"event": "inbox_closed", "inbox", "pending"}` | `hub-log.jsonl` | 要求の中身 |
| `_answer_old` | 要求 ID・`ok: false`・`summary`・`cause: precondition`・`inbox` | 同上 | 要求の中身（引数・作業ディレクトリ） |
| `settle` | `{"event": "legacy_reconciled", "marked"}` | 同上 | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_answer_old` | `signals.replace_json`・`hub._log` | `hub.py` を読んだ | 使う |
| `_reconcile` | `launch_ticket.write_launch`・`read_launch`・`check_name`（#4103） | #4103 の詳細設計 | 使う |
| `settle` の tmux の一覧 | `Tmux.sessions`（#4103） | 同上 | 使う。`drain` と一覧は `_review_launches` と同じ回の結果を共有する |
| `state` | `identity.lock_held` | 読んだ | 使わない（錠の持ち主を推し量らない。決定 4） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `state` | 4 つの状態を返す | 旧い受付箱がシンボリックリンク → `open`。`closure.json` だけ `reconciled: true` で旧い受付箱がディレクトリ → `open` | `closure.json` が壊れている → `pending` |
| `settle` | 旧い受付箱が無い保存先 → 1 回で `settled`・tmux を打たない。ディレクトリがある → `pending` → 猶予の後に `settled` | 猶予の 1 秒前は tmux を打たない。`closure.json` を書く前に止めると `pending` | 一覧が None → `pending` のまま。`write_launch` が失敗 → `pending` のまま |
| `_close_one` | ディレクトリ → 通常のファイル | 脇へ移した直後に作り直す → 偽、次の回で閉じる | 脇の `.closing` が残る → 先に断ってから閉じる |
| `_answer_old` | 要求ごとに断りが書かれ、要求とディレクトリが消える | 形の違う要求・一時のファイルも断るか消す | 書けない → 残りが次の回に断られる |
| `_reconcile` | 印の無い名前にだけ書く | 名前の形が違う名前は飛ばす。印のある名前は書き直さない | — |

## ハブ・一覧・頼む側（`hub`・`cli`・`launch.wrappers`）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drain` | `domains/relay/hub.py` | 変更 | 変えない | `hub.lock` を取れたら、`inbox_closure.settle` → `_review_launches` → `sweep` → 受付箱の処理の順に進める。照合と印の見直しが同じ回に tmux を要するときは `Tmux.sessions` を 1 回だけ打つ。`settle` の結果が `settled` でなければ、`stop` だけを処理し、`spawn`・`replace`・`restart` は処理の錠を取らずに残す（C7） |
| `listing` | 同上 | 変更 | `(ctx: Context) -> Listing`（行の一覧と `Closure`） | `hub.lock` を待たずに試し、取れたら `settle` → `_review_launches` を打ってから放す。取れなければ `inbox_closure.state` を読む。その後は #4103 の順（券 → 印 → 錠）で行を読む |
| `submit` | 同上 | 変更 | 変えない | 券を取る前に `state` を読み、`superseded` なら `precondition` で断る。`_write_request` の `FileExistsError`・`NotADirectoryError` も同じ断りにする（C9）。待ち切れのときは `state` の種類を文に足す |
| `_hub`（`ls`） | `domains/relay/cli.py` | 変更 | 変えない | 結果の最上位に `legacy`（`kind != "settled"`）と `legacy_reason`（済みなら null）を載せる |
| `wrappers` | `progress/lane/launch.py` | 変更 | 変えない | 応答の `relay_protocol` が 3 以上で、`legacy` が偽のときだけ表を返す。`legacy` が無い応答は `relay_protocol` の条件で決まる（#4103 の前の版は既に None） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `drain` | 済みでない | 旧版の照合待ち・閉じきれない | `settle` の戻り | 起動を残し、停止だけ処理する | 頼む側は待つ。待ち切れれば `wait_timeout`（文に閉鎖の状態） | 済んだ後の `drain` が処理する |
| `submit` | 後の版が閉じた | 新しい版が `requests-v3` を閉じた | `state`・`_write_request` | 券を取らない・放す | `precondition`、終了コード 3 | 後の版の `ndf` で打ち直す |
| `listing` | `settle` が例外 | 保存先の権限 | `listing` | `state` を読んで続ける（`legacy: true`） | 一覧の `legacy_reason` | 次の一覧で再び試す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `drain` | 済みの保存先で、記録と結果のファイルを開かない（読み取りを数える替え玉で 0 回） | 照合待ちで 4 種の要求 → `stop` だけ処理 | `settle` が `open` を返す → 同じく保留 |
| `listing` | `legacy`・`legacy_reason` が状態と一致する | `hub.lock` を別のプロセスが持つ間は `state` だけで決まる | — |
| `submit` | `superseded` で券も要求も作らない | 確かめた後に閉じられる → 同じ断り | — |
| `wrappers` | `legacy: false` → 表 | `legacy` の欄が無い・`relay_protocol` が 3 → 表 | `legacy: true` → None |
