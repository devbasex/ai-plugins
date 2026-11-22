# #4092 の詳細設計: 索引の後始末を解決済みの所在から読み取りだけで開く

設計文書 [issue-4092-design.md](./issue-4092-design.md) の解き方を、ファイルと関数の粒度へ下ろした文書である。#4051 の詳細設計 [issue-4051-design-detail.md](./issue-4051-design-detail.md) の「索引を消す条件」「耐久ワークフローの終わり」を置き換える。新設する関数が 1 つ（`machine.read_plan_at`）と #4051 の実装が足す `store.open_reader`（仕様は #4044 の詳細設計）、シグネチャを変える関数が 1 つ（`run._indexed_until_done`。#4051 の設計の形からも変わる）あるので別ファイルにした。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。#4051 の地図に足す分を含む）:

```text
plugins/ndf/
├── src/ndf/progress/store.py           open_reader を足す（仕様は #4044 の詳細設計）
├── src/ndf/progress/lane/machine.py    read_plan_at を足し、unindex_idle をそれで読む
├── src/ndf/progress/plan/run.py        _indexed_until_done と advance の呼び方
└── tests/progress/
    ├── test_store_sqlite.py            open_reader の開き方（#4044 の「開き方」の行）
    ├── test_lane_machine.py            unindex_idle・read_plan_at
    └── test_plan_run.py                後始末の子プロセスの数
```

依存の向き: `run.py` → `machine.py` → `store.py` の一方向で、今と変わらない。`unindex_idle` は `machine.py` の中の `read_plan_at`・`unindex_plan` だけを呼び、`read_lanes`・`command` の Git の関数を呼ばない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/progress/store.py` | 変更 | ステートマシンの DB の保存 | `durable` | モジュール | `open_reader`（新設。仕様は #4044 の詳細設計の「関数」の表のまま） |
| `src/ndf/progress/lane/machine.py` | 変更 | マシンの数え方と起動の許可・計画の索引 | `store`・`model` | モジュール | `read_plan_at`（新設）・`unindex_idle`（#4051 の新設のシグネチャと中身を変える） |
| `src/ndf/progress/plan/run.py` | 変更 | 計画を進める耐久ワークフローの起動と終わり | `machine`・`durable` | モジュール | `_indexed_until_done`（変更）・`advance`（呼び方） |

## 開いて読む（`machine.py`・`store.py`）

満たす決定: 決定 1・決定 2・決定 3、受け入れ条件 1・3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `open_reader` | `src/ndf/progress/store.py` | 新設 | `open_reader(url: str, root: Path) -> Store` | #4044 の詳細設計の「関数」の表の `open_reader` の行のとおり。この課題は仕様を変えない |
| `read_plan_at` | `src/ndf/progress/lane/machine.py` | 新設 | `read_plan_at(plan_id: str, url: str, root: Path, key: str) -> PlanLanes` | `store.open_reader(url, root)` で開き、`store.transaction` の中で `read_view(conn, plan_id)` を読んで `lanes_of` を返す。`read_lanes` と同じ `_UNREADABLE` の例外（`model.Refused`・`durable.StoreStopped` とその子の `StoreBusy`・`StoreInUse`・`sa.exc.SQLAlchemyError`・`contracts.ContractError`・`OSError` など。集合は変えない）は `PlanLanes(key, readable=False)` にする |
| `unindex_idle` | `src/ndf/progress/lane/machine.py` | 変更（#4051 の新設） | `unindex_idle(plan_id: str, url: str, root: Path) -> bool`（#4051 の設計は `unindex_idle(where: PlanPlace)`） | `read_plan_at(plan_id, url, root, plan_id)` で読み、読めて数える行が無いときだけ `unindex_plan(plan_id)` で索引から消し、消したら真を返す |

- `read_plan_at`
  - 事前条件: `url` と `root` は同じ計画のチェックアウトの解決済みの接続先と保存先。`key` は読めないときに返す行の鍵で、呼び手が決める（`read_indexed` は `f"{place.cwd}:{place.plan}"`。`unindex_idle` は `readable` しか見ないので `plan_id` を渡す）
  - 事後条件: 読めれば `lanes_of(*read_view(conn, plan_id))`、読めなければ `readable` が偽の行。DB も表も作らず、検査の目印・バックアップを変えない
  - 副作用: DB を読み取りだけで 1 回開く。子プロセスを起こさない
- `unindex_idle`
  - 事前条件: 呼ぶのはその計画の耐久ワークフロー（実行のキーのロックを持つ）だけである（#4051 の決定 2 のまま）
  - 事後条件: 読めて、`lanes` に実行中の行を持つものが無いとき、索引のファイルが無い。それ以外は索引のファイルを変えない
  - 副作用: 計画のステートマシンを読み取りだけで 1 回読む。索引のファイルを消すことがある。子プロセスを起こさない
- #4044 の `read_indexed` は、接続先を解いた後の `open_reader` から `lanes_of` までを `read_plan_at(place.plan_id, url, Path(place.store_root), f"{place.cwd}:{place.plan}")` の呼び出しにする（#4044 の実装の PR が行う。契約は変えない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `unindex_idle` | 計画を読めない | DB が無い・表のバージョンが違う・バックアップからの戻し（`restore`）の最中・WAL の回復中・その DB に計画が無い | `read_plan_at` が `readable` の偽を返す | 消さずに偽を返す（#4051 の決定 3） | 無し | 次に耐久ワークフローが終わるときか、完了済みの計画の打ち直しで確かめ直す |
| `unindex_idle` | 索引のファイルを消せない | 権限・ファイルシステムの誤り（`OSError`） | `unindex_plan` | 呼び出し元へ返す | `_indexed_until_done` の扱い（#4051 のまま） | 残った索引は数え方が正しく数える |

#4051 の異常系の表の「読んだ計画の ID が索引と違う」の行は消す。計画の ID で直に開くので、読んだ計画と索引の ID は食い違わない。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `unindex_idle` | 読めた・数える行が無い | 消して真 |
| `unindex_idle` | 読めた・数える行がある | 残して偽 |
| `unindex_idle` | 読めない | 残して偽 |

## 耐久ワークフローの終わり（`run.py`）

満たす決定: 決定 1、受け入れ条件 1・2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_indexed_until_done` | `src/ndf/progress/plan/run.py` | 変更 | `_indexed_until_done(plan_id: str, url: str, root: Path) -> Iterator[None]`（今は `plan_id: str`、#4051 の設計は `where: machine.PlanPlace`） | 耐久ワークフローが終わったら（終わり方を問わず）、`machine.unindex_idle(plan_id, url, root)` を打つ。例外の扱い（本体の例外を優先する）は #4051 の詳細設計のまま |
| `advance` | `src/ndf/progress/plan/run.py` | 変更 | 変えない | 耐久ワークフローを流す経路と、計画が `completed` で索引が残るときの早期 return の経路（#4051 の詳細設計）の両方で、`_indexed_until_done(plan_id, where.url, where.root)` を渡す。`machine.PlanPlace` を後始末のために作らない |

- `advance` が `where.url` を渡すのは同じプロセスの中だけで、索引・結果・ログに書かない
- #4044 の実装は、`advance` が起動の許可へ渡す `PlanPlace`（`store_root`・`db` を持つ）を作る。後始末はその `PlanPlace` を読まない

## 単体テストの観点

#4051 の詳細設計の「単体テストの観点」のうち「読んだ計画の ID が索引の `plan_id` と違うとき」の行を消し、次の行を足す。ほかの行は変えない。

| 観点 | 置き場 |
| --- | --- |
| `unindex_idle` が `read_lanes`・`store.open_store` を呼ばない（差し替えて呼ばれたら落とす）。実行中の行を持つ計画では偽を返し索引が残り、行を終えると真を返し消える | `tests/progress/test_lane_machine.py` |
| 計画の無い DB・DB の無い保存先・バックアップからの戻しの最中（`db_lock` を排他で持つ）で、`unindex_idle` が偽を返し索引が残る。DB も表も作られない | 同上 |
| 別の接続が `BEGIN IMMEDIATE` で書き込みを開いたまま（未確定の行あり。確定済みの行には実行中の行が無い）でも、`unindex_idle` が待たずに真を返し索引を消す | 同上（SQLite の計画） |
| `advance` が完了で終わるとき、`_indexed_until_done` を抜ける間に `subprocess.run`・`subprocess.Popen` が 1 回も呼ばれない（後始末の間だけ差し替えて数える） | `tests/progress/test_plan_run.py` |
| `open_reader` の開き方（#4044 の詳細設計の「単体テストの観点」の「開き方」の行） | `tests/progress/test_store_sqlite.py` |
