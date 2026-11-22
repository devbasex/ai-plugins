# #4092 の詳細設計: 索引の後始末を解決済みの所在から読み取りだけで開く

設計文書 [issue-4092-design.md](./issue-4092-design.md) の解き方を、ファイルと関数の粒度へ下ろした文書である。#4051 の詳細設計 [issue-4051-design-detail.md](./issue-4051-design-detail.md) の「索引を消す条件」「耐久ワークフローの終わり」を置き換える。新設する関数が 1 つ（`machine.read_plan_at`）と #4051 の実装が足す `store.open_reader`（仕様は #4044 の詳細設計）、シグネチャを変える関数が 1 つ（`run._indexed_until_done`。#4051 の設計の形からも変わる）あるので別ファイルにした。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。#4051 の地図に足す分を含む）:

```text
plugins/ndf/
├── src/ndf/progress/store.py           open_reader を足す（仕様は #4044 の詳細設計）
├── src/ndf/progress/lane/machine.py    read_plan_at を足し、unindex_idle をそれで読む
├── src/ndf/progress/plan/run.py        _indexed_until_done と advance の呼び方・写しに unstarted と gone を載せる
├── src/ndf/progress/plan/run_decide.py RunState に awaiting を足し、as_result に pending を足す
└── tests/progress/
    ├── test_store_sqlite.py            open_reader の開き方（#4044 の「開き方」の行）
    ├── test_lane_machine.py            unindex_idle・read_plan_at
    └── test_plan_run.py                後始末の子プロセスの数・起動の途中で残す・区切り停止から再開する経路・結果の pending
```

依存の向き: `run.py` → `machine.py` → `store.py` と `run.py` → `run_decide.py` の一方向で、今と変わらない。`unindex_idle` は `machine.py` の中の `read_plan_at`・`unindex_plan` だけを呼び、`read_lanes`・`command` の Git の関数を呼ばない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/progress/store.py` | 変更 | ステートマシンの DB の保存 | `durable` | モジュール | `open_reader`（新設。仕様は #4044 の詳細設計の「関数」の表のまま） |
| `src/ndf/progress/lane/machine.py` | 変更 | マシンの数え方と起動の許可・計画の索引 | `store`・`model` | モジュール | `read_plan_at`（新設）・`unindex_idle`（#4051 の新設のシグネチャと中身を変える） |
| `src/ndf/progress/plan/run.py` | 変更 | 計画を進める耐久ワークフローの起動と終わり | `machine`・`durable`・`run_decide` | モジュール | `_indexed_until_done`（変更）・`advance`（呼び方）・`snapshot_of`・`read_snapshot`（写しに `unstarted`・`gone` を載せる） |
| `src/ndf/progress/plan/run_decide.py` | 変更 | 計画を進める耐久ワークフローの判断 | — | `RunState`・`Snapshot`・`Observe` | `RunState.awaiting`（足す）・`as_result`（`pending` を足す）・`Snapshot.unstarted`・`Snapshot.gone`（足す）・`Observe.awaiting`（足す）・`_after_launch`・`decide`（`awaiting` の足し外し） |

## 開いて読む（`machine.py`・`store.py`）

満たす決定: 決定 1・決定 2・決定 3、受け入れ条件 1・3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `open_reader` | `src/ndf/progress/store.py` | 新設 | `open_reader(url: str, root: Path) -> Store` | #4044 の詳細設計の「関数」の表の `open_reader` の行のとおり。この課題は仕様を変えない |
| `read_plan_at` | `src/ndf/progress/lane/machine.py` | 新設 | `read_plan_at(plan_id: str, url: str, root: Path, key: str) -> PlanLanes` | `store.open_reader(url, root)` で開き、`store.transaction` の中で `read_view(conn, plan_id)` を読んで `lanes_of` を返す。`read_lanes` と同じ `_UNREADABLE` の例外（`model.Refused`・`durable.StoreStopped` とその子の `StoreBusy`・`StoreInUse`・`sa.exc.SQLAlchemyError`・`contracts.ContractError`・`OSError` など。集合は変えない）は `PlanLanes(key, readable=False)` にする |
| `unindex_idle` | `src/ndf/progress/lane/machine.py` | 変更（#4051 の新設） | `unindex_idle(plan_id: str, url: str, root: Path, settled: bool) -> bool`（#4051 の設計は `unindex_idle(where: PlanPlace)`） 先に `is_indexed(plan_id)`（#4051 の新設。ファイルの有無を見るだけ）を見て、索引が無ければ計画を読まずに偽を返す（決定 5）。`settled` が偽なら計画を読まずに偽を返す（決定 4）。どちらでもなければ `read_plan_at(plan_id, url, root, plan_id)` で読み、読めて数える行が無いときだけ `unindex_plan(plan_id)` で索引から消し、消したら真を返す |

- `read_plan_at`
  - 事前条件: `url` と `root` は同じ計画のチェックアウトの解決済みの接続先と保存先。`key` は読めないときに返す行の鍵で、呼び手が決める（`read_indexed` は `f"{place.cwd}:{place.plan}"`。`unindex_idle` は `readable` しか見ないので `plan_id` を渡す）
  - 事後条件: 読めれば `lanes_of(*read_view(conn, plan_id))`、読めなければ `readable` が偽の行。DB も表も作らず、検査の目印・バックアップを変えない
  - 副作用: DB を読み取りだけで 1 回開く。子プロセスを起こさない
- `unindex_idle`
  - 事前条件: 呼ぶのはその計画の耐久ワークフロー（実行のキーのロックを持つ）だけである（#4051 の決定 2 のまま）。`settled` は、耐久ワークフローが結果を返し、その結果の `pending` が空の配列であること（起動済みで行の開始を待つ起動が無い）
  - 事後条件: `settled` が真で、読めて、`lanes` に実行中の行を持つものが無いとき、索引のファイルが無い。それ以外は索引のファイルを変えない
  - 副作用: 索引のファイルの有無を 1 回見る。索引があり `settled` が真のときだけ、計画のステートマシンを読み取りだけで 1 回読む。索引のファイルを消すことがある。子プロセスを起こさない
- #4044 の `read_indexed` は、接続先を解いた後の `open_reader` から `lanes_of` までを `read_plan_at(place.plan_id, url, Path(place.store_root), f"{place.cwd}:{place.plan}")` の呼び出しにする（#4044 の実装の PR が行う。契約は変えない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `unindex_idle` | 起動の途中か、終わり方が確かめられない | 耐久ワークフローが例外で終わった・結果の `pending` が空でない・結果に `pending` が無い（足す前の版で記録した結果） | `_indexed_until_done` が `settled` を偽にする | 読まずに消さず偽を返す（決定 4） | 無し | 次に耐久ワークフローが終わるときか、完了済みの計画の打ち直しで確かめ直す |
| `unindex_idle` | 計画を読めない | DB が無い・表のバージョンが違う・バックアップからの戻し（`restore`）の最中・WAL の回復中・その DB に計画が無い | `read_plan_at` が `readable` の偽を返す | 消さずに偽を返す（#4051 の決定 3） | 無し | 次に耐久ワークフローが終わるときか、完了済みの計画の打ち直しで確かめ直す |
| `unindex_idle` | 索引のファイルを消せない | 権限・ファイルシステムの誤り（`OSError`） | `unindex_plan` | 呼び出し元へ返す | `_indexed_until_done` の扱い（#4051 のまま） | 残った索引は数え方が正しく数える |

#4051 の異常系の表の「読んだ計画の ID が索引と違う」の行は消す。計画の ID で直に開くので、読んだ計画と索引の ID は食い違わない。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `unindex_idle` | 索引が無い（`is_indexed` が偽） | 読まずに偽 |
| `unindex_idle` | `settled` が偽 | 読まずに残して偽 |
| `unindex_idle` | `settled` が真・読めた・数える行が無い | 消して真 |
| `unindex_idle` | `settled` が真・読めた・数える行がある | 残して偽 |
| `unindex_idle` | `settled` が真・読めない | 残して偽 |

## 耐久ワークフローの終わり（`run.py`）

満たす決定: 決定 1・決定 4、受け入れ条件 1・2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `RunState.awaiting` | `src/ndf/progress/plan/run_decide.py` | 新設（項目） | `awaiting: tuple[tuple[str, str, str], ...] = ()`（起動のキー・レーン・行） | 行の開始を確かめていない起動の記録。`launched`（起動の候補に残る間の印）とは別に持つ。既定の空で、この項目の無い前の版の状態からも読める |
| `_after_launch` | 同上 | 変更 | 変えない | 起動できた起動（`value["ok"]` が真で、止める手でないもの）を、`launched`・`started` に足すのと同時に `awaiting` へ `(act.key, act.lane, act.row)` で足す（同じキーが既にあれば足さない） |
| `decide`（`Observe` の結果を受ける分岐） | 同上 | 変更 | 変えない | 写しを受けたら、`_expire_launched` とは別に `awaiting` を絞る。`(lane, row)` が写しの `unstarted` に無い（開始を確定したか終わった）組と、`lane` が写しの `gone` にある組を外し、ほかは残す。`_expire_launched` が `launched` から外したこと（区切りで止めて起動の候補が空になった・期限で起動の失敗に数えた）では外さない |
| `Snapshot.unstarted`・`Snapshot.gone` | 同上 | 新設（項目） | どちらも `list[Any]`、既定の空 | `unstarted` は未着手の行の `[レーン, 行]`（計画の状態によらず、`live` で `active` でない行）。`gone` はラッパーの一覧を読めて、そのレーンのラッパーが動いていないと確かめたレーン（`Observe.awaiting` のレーンだけを確かめる）。読めなければ空（確かめていないので外さない） |
| `Observe.awaiting` | 同上 | 新設（項目） | `awaiting: tuple[str, ...] = ()` | 読み直しの手に、`awaiting` に残るレーンを載せる。`_observe` が `RunState.awaiting` から作る |
| `snapshot_of`・`read_snapshot` | `src/ndf/progress/plan/run.py` | 変更 | `awaiting: Sequence[str] = ()` を足す | `unstarted` を行から作り、`awaiting` が空でなく `found` があれば、`found.wrappers()`（生死の判定と同じ読み直しの中の 1 回の読みを共有する）で `awaiting` の各レーンのラッパーが動いていないものを `gone` にする。`awaiting` が空ならラッパーの一覧を読まない |
| `RunState.as_result` | `src/ndf/progress/plan/run_decide.py` | 変更 | 変えない | 結果に `"pending": [<キー>…]` を足す。中身は `awaiting` のキーを足した順で並べたもの（止める手は入らない） |
| `_indexed_until_done` | `src/ndf/progress/plan/run.py` | 変更 | `_indexed_until_done(plan_id: str, url: str, root: Path) -> Iterator[Callable[[Mapping[str, Any]], None]]`（今は `plan_id: str` で `Iterator[None]`、#4051 の設計は `where: machine.PlanPlace`） | 結果を受ける関数を `yield` する。本体がそれに渡した結果の `pending` が空の配列のときだけ `settled` を真にし、終わったら（終わり方を問わず）`machine.unindex_idle(plan_id, url, root, settled)` を打つ。結果を受けずに抜けた（例外）・`pending` が無いか配列でない・空でないときは `settled` が偽。例外の扱い（本体の例外を優先する）は #4051 の詳細設計のまま |
| `advance` | `src/ndf/progress/plan/run.py` | 変更 | 変えない | 耐久ワークフローを流す経路と、計画が `completed` で索引が残るときの早期 return の経路（#4051 の詳細設計）の両方で、`_indexed_until_done(plan_id, where.url, where.root)` に入り、返す結果（`durable.start`・`durable.result` の値。早期 return では `RunState(outcome=completed).as_result()` で `pending` は空）を return の前に受ける関数へ渡す。`machine.PlanPlace` を後始末のために作らない |

- `pending` のキーは、読み直しが行の開始の確定（行が `unstarted` に無い）か、ラッパーが動いていないこと（レーンが `gone` にある）を確かめるまで `awaiting` に残る。区切りで止めた計画では `wakeable_lanes` が空を返して `_expire_launched` が `launched` から外すが、`awaiting` は外れない。conductor はラッパーの中で動くので、conductor の `row start` が確定前か書き込み中なら、そのキーは結果の `pending` に必ず残る（設計文書の決定 4）

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
| `settled` が偽なら `unindex_idle` が計画を開かずに偽を返す（`read_plan_at` を差し替えて呼ばれたら落とす） | `tests/progress/test_lane_machine.py` |
| 索引を書かずに終わった実行（起動の許可を通らずに完了・中止・区切りで終わる）では、`unindex_idle` が `read_plan_at` を呼ばず（差し替えて呼ばれたら落とす）、`_indexed_until_done` を抜ける間に `subprocess.run`・`subprocess.Popen` が 1 回も呼ばれない（#4092 の受け入れ条件 6） | `tests/progress/test_plan_run.py` |
| 起動の許可を通して conductor を起動した後、`row start` の前に耐久ワークフローが例外で終わると索引が残る。その後 `row start` を確定し、ラッパーと登録簿の行が無い状態で、別の計画の `machine_plans` がその行を実行中として数える。結果の `pending` が空でない終わり・`pending` の無い結果でも残る | `tests/progress/test_plan_run.py` |
| `as_result` の `pending` が、起動できて行の開始を確かめていない起動のキーだけを持ち、止める手・行が実行中になった起動・ラッパーが動いていないと確かめた起動を持たない。期限で失敗に数えた起動・区切りで止めて起動の候補から外れた起動は、行が未着手でラッパーが動いていれば持つ | `tests/progress/test_plan_run.py` |
| 区切り停止から再開する経路: 起動の許可を通して conductor を起動し、`row start` の前に計画を区切りで止める。次の読み直しで `launched` は空になるが、耐久ワークフローは `pending` にそのキーを持って `paused` で終わり、索引が残る。計画を再開し、次の起動の許可より先に旧い conductor の `row start` を確定し、ラッパーと登録簿の行を消すと、別の計画の `machine_plans` がその行を実行中として数える。止めている間にラッパーが動いていないと確かめた読み直しの後の終わりでは `pending` が空で索引が消える | `tests/progress/test_plan_run.py` |
| `snapshot_of` が、`awaiting` が空ならラッパーの一覧を読まず、空でなければ生死の判定と同じ 1 回の読みを使う。一覧を読めないときは `gone` が空 | `tests/progress/test_plan_run.py` |
