# リレーハブの上限の詳細設計（#4044）

設計文書 [issue-4044-design.md](./issue-4044-design.md) の解き方を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 9 つ（`machine.read_indexed`・`machine.machine_seats`・計画の CLI の `_cmd_seats`・`launch.by_name`・`seats.read`・`hub._counted`・`store.open_reader`・`store.index_url`・`store.indexed_url`）、シグネチャを変える関数が 2 つ（`subcommand.call`・`hub.restart`）、項目を足す型が 1 つ（`machine.PlanPlace` の省略できる `store_root`・`db`）あるので別ファイルにした。パスは `plugins/ndf/` からの相対で書く。

## 地図

```text
plugins/ndf/
├── src/ndf/progress/lane/machine.py    索引に保存先（store_root）と接続先（db）を書き、Git を起動せずに索引の計画を読む read_indexed と machine_seats を足す
├── src/ndf/progress/store.py           読み取りだけで開く open_reader と、索引に書く接続先を作る index_url・解く indexed_url を足す
├── src/ndf/progress/plan/run.py        起動の許可へ渡す PlanPlace に保存先（spec["root"]）と接続先（spec["url"] から作る）を載せる
├── src/ndf/progress/plan/cli.py        サブコマンド seats を足す（チェックアウトの保存先を求めずに打てる）
├── src/ndf/progress/lane/launch.py     wrappers の一覧を名前で引く形にする部分を by_name に切り出す（_cmd_seats も使う）
├── src/ndf/domains/relay/subcommand.py call に標準入力（stdin）を渡せるようにする
├── src/ndf/domains/relay/seats.py      新設。登録簿の一覧を標準入力に載せて ndf lanes seats を打ち、名前を読む（pending.py と同じ形）
├── src/ndf/domains/relay/cli.py        通常の spawn と restart の要求に held を載せ、読めなければ warnings を出す
├── src/ndf/domains/relay/hub.py        _counted を足し、_over_cap と restart がそれで数える。通常の起動が held の名前を取るのを断る。handle が restart へ要求を渡す
├── contracts/limits.json               relay.seats（30 秒）を足す
├── skills/development-workflow/references/relay.md   同時に動かす数の上限の段落に、実行中の行を持つレーンを数えることを足す
└── tests/…                             下の「テスト」
docs/specifications/ndf-parallel-machine-plan-limit.md   下の「確定仕様の書き直し」
```

依存の向き: `domains/relay/cli.py` → `domains/relay/seats.py` → `domains/relay/subcommand.py`（プロセスで `ndf lanes seats` を打つ）。`domains/relay` から `ndf.progress` を読み込まない（子の依存の表の M3b は P3 に依存しない）。逆の向き（P3 から M3b）も子の依存の表に無いので、`progress/plan/cli.py` は登録簿を同じプロセスで読めない。そのため登録簿は頼む側（`domains/relay/cli.py`。同じプロセスで `hub.listing` を読める）が読み、`ndf lanes seats` の標準入力に載せて渡す。`progress/plan/cli.py` → `progress/lane/machine.py` は今のとおりで、`_cmd_seats` は `launch.wrappers()`（`ndf relay ls` をもう 1 本起こす）を呼ばない。

### プロセスの起動の回数

| 要求 | 今 | 直した後 |
| --- | --- | --- |
| 通常の `spawn`（`--replace` も `--seated` も無い）・`restart` | 頼む側 1 本 | 頼む側 1 本 ＋ `ndf lanes seats` 1 本（中で `ndf relay ls` も Git も起こさない） |
| 置き換えの起動（`--replace`）・続きの起動（`--seated`）・`stop`・`ls` | 今のまま | 今のまま（`ndf lanes seats` を打たない） |

増えるのは、通常の起動と再起動の 1 回ごとに CLI の起動 1 本だけである。`ndf lanes seats` の中では Git を含む子プロセスを 1 本も起こさない（計画の数によらず 0 本）。今の `read_lanes` は計画ごとに保存先と ID を解くため `git rev-parse`・`git worktree list`・`git remote get-url` を起こし、計画の数に比例して増えるので使わない。代わりに、計画の索引に保存先（`store_root`）・接続先（`db`）と計画のステートマシンの ID（`plan_id`）を書いておき、`read_indexed` がそれだけで DB を読み取りだけで開く（`store.open_reader` は Git を起こさない）。

| 数える回数 | 上限 | 縛るテスト |
| --- | --- | --- |
| 頼む側が起こす CLI（`runner` の呼び出し） | 通常の起動・再起動の 1 回につき 1 本。置き換えの起動・続きの起動・`stop`・`ls` は 0 本 | `tests/domains/relay/test_relay_cli.py` |
| `ndf lanes seats` の中で起こす子プロセス（Git を含む） | 0 本（索引の計画が何件でも） | `tests/progress/test_plan.py`（索引の計画 3 件で `subprocess.run`・`subprocess.Popen` を数える差し替えを入れ、0 回） |

頼む側の `runner` の回数では子 CLI の中の起動を数えられないので、`_cmd_seats` を同じプロセスで打ち、子プロセスを起こす口（`subprocess.run`・`subprocess.Popen`）を数えて縛る。子 CLI は `_cmd_seats` を通すだけなので、これで子 CLI の中の起動を数えたことになる。

## 構成要素

| ファイル | 種別 | 責務 | 依存 | 公開の単位 | 足す・変える関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/progress/lane/machine.py` | 変更 | マシンの数え方と起動の許可・計画の索引 | `launch`・`store`・`model` | モジュール | `PlanPlace`（省略できる項目 `store_root`・`db` を足す）・`index_plan`・`indexed`（中身だけ）・`read_indexed`・`machine_seats`（新設）。`_read_index`・`machine_plans`・`read_lanes` は変えない |
| `src/ndf/progress/store.py` | 変更 | ステートマシンの DB の保存 | `durable` | モジュール | `open_reader`・`index_url`・`indexed_url`（新設）。`open_store`・`transaction` は変えない |
| `src/ndf/progress/plan/run.py` | 変更 | レーンの起動 | `machine` | — | `PlanPlace` を作るところ（中身だけ） |
| `src/ndf/progress/plan/cli.py` | 変更 | `ndf lanes` のサブコマンド | `machine`・`launch` | サブコマンド `seats` | `_cmd_seats`（新設）、`parser`・`_dispatch`（足すだけ） |
| `src/ndf/progress/lane/launch.py` | 変更 | ラッパーの一覧を読む | — | モジュール | `by_name`（新設。`wrappers` の中身を切り出す） |
| `src/ndf/domains/relay/subcommand.py` | 変更 | 中継が NDF のサブコマンドを打つ | `identity` | モジュール | `call`（引数 `stdin` を足す） |
| `src/ndf/domains/relay/seats.py` | 新設 | 頼む側が実行中の行を持つレーンの名前を読む | `subcommand` | モジュール | `read`（新設）、`Seats`（型） |
| `src/ndf/domains/relay/cli.py` | 変更 | `ndf relay` の頼む側 | `seats`・`hub` | — | `_hub`・`_outcome`（中身だけ） |
| `src/ndf/domains/relay/hub.py` | 変更 | リレーハブの判定 | `hub_decide`・`registry` | モジュール | `_counted`（新設）・`restart`（変更）、`spawn`・`_over_cap`・`handle`（中身だけ） |
| `contracts/limits.json` | 変更 | 上限の設定 | — | — | `relay.seats` |

## 関数

| 関数 | 場所 | 新設・変更 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PlanPlace` | `machine.py` | 変更（型） | `PlanPlace(plan_id, cwd, plan, store_root: Optional[str] = None, db: Optional[str] = None)` | 計画のチェックアウトの保存先（`command.store_root_of` の値）と、索引に書く接続先（`store.index_url(spec["url"])` の値）を持つ。`index_plan` は索引の本文に `store_root` と `db`（`{"url": …, "exact": …}`）を書き、`INDEX_VERSION` を 2 にする。**`_read_index` は今のとおり `plan_id`・`cwd`・`plan` だけを検証し、版 1 の本文も読む**。`indexed` は `store_root`・`db` が正しい形のときだけ載せ、無ければ None にする。起動の許可（`machine_plans`）は `plan_id`・`cwd`・`plan` だけを使い、今のとおり `read_lanes` で所在を解くので、版 1 の索引の計画も数える。版 1 の本文は版 2 と違うので、その計画の次の起動の許可で `index_plan` が書き直す。`run.py` は `spec["root"]` と `store.index_url(spec["url"])` を渡す |
| `read_indexed` | `machine.py` | 新設 | `read_indexed(place: PlanPlace, environ: Mapping[str, str] = os.environ) -> PlanLanes` | `place.store_root` か `place.db` が None（版 1 の索引）なら開かずに `readable` が偽の行を返す。そうでなければ `store.indexed_url(place.db, environ)` で接続先を解き（None なら開かずに `readable` が偽の行）、`store.open_reader(url, Path(place.store_root))` で開いて、`place.plan_id` の計画を `read_view` で読んで `lanes_of` を返す。頼む側の環境の `store.default_url` は評価しない。Git を起こさない（保存先も ID も解かない）。DB が無い・読めなければ `readable` が偽の行（鍵は `f"{place.cwd}:{place.plan}"`）。DB も表も作らない |
| `index_url` | `store.py` | 新設 | `index_url(url: str) -> dict[str, Any]` | 索引に書く接続先。`sa.engine.make_url(url)` から、パスワードと、認証情報のクエリ（キー名の小文字に `password`・`secret`・`token` のどれかを含むもの。定数 `store.SECRET_QUERY_KEYS` の部分文字列で判定する）だけを除き、ほかのクエリ（`host`・`hostaddr`・`port`・`dbname`・`service`・`sslmode` など、接続先を変えうるもの）はキーの昇順に並べて残した文字列を `url` に、除いたものが無かったか（パスワードが None で認証情報のクエリが無い）を `exact` に入れて返す。認証情報を返さない |
| `indexed_url` | `store.py` | 新設 | `indexed_url(db: Mapping[str, Any], environ: Mapping[str, str]) -> Optional[str]` | 索引の接続先から開く URL を解く。`exact` が真なら `db["url"]`。偽なら `environ` の `NDF_PROGRESS_DB` を `index_url` に通した `url` が `db["url"]` と文字列で一致するときだけその値、それ以外（変数が無い・別の DB）は None。比べる `url` は認証情報の外のクエリを含むので、ホスト・ポート・DB 名がクエリで指定されていても、どれか 1 つでも違えば一致しない。一致して違ってよいのは、パスワードと認証情報のクエリだけである（同じ接続先の別の認証情報） |
| `open_reader` | `store.py` | 新設 | `open_reader(url: str, root: Path) -> Store` | 読み取りだけで開く。表を作らない・表のバージョンを書かない・検査の目印を見ない（`quick_check`・バックアップ・目印の更新をしない）。SQLite: 実ファイルが無ければ `StoreStopped`（作らない）。`db_lock` を共有で待たずに試し（取れなければ `StoreInUse`。戻しの最中）、`file:<実ファイル>?mode=ro` で開き（`durable.check_file` と同じ形）、トランザクションを `BEGIN`（DEFERRED）で始め、`busy_timeout` は 0 にする（WAL の読み手は書き手を待たないので、待つのは WAL の回復の間だけで、そのときは `StoreBusy` として読めない計画にする）。PostgreSQL: 接続して `SET TRANSACTION READ ONLY` で読む。どちらも表のバージョンが `SCHEMA_VERSION` と違えば（表が無いを含む）`StoreStopped`。`transaction` はこの `Store` でも使える |
| `machine_seats` | `machine.py` | 新設 | `machine_seats(listed: Mapping[str, Mapping[str, Any]]) -> tuple[tuple[str, ...], tuple[str, ...]]` | 索引の計画（`indexed()`。版 1 も含む）を `plan_id` ごとに 1 回 `read_indexed` で読み、`held_seats(plans, "")`（実行中の行を持つレーンのラッパーの名前）と、読めない計画の鍵（`readable` が偽の `key`）を返す。登録簿の conductor の行の計画のうち索引に無いもの（`(cwd, plan)` が索引に無い）は、ID を解くのに Git が要るので読まずに読めない計画の鍵に載せる。`read_lanes`・`machine_plans` は呼ばない（`machine_plans`・`read_lanes` は変えない） |
| `_cmd_seats` | `progress/plan/cli.py` | 新設 | `_cmd_seats(a: argparse.Namespace, stdin: TextIO = sys.stdin) -> dict[str, Any]` | 標準入力の登録簿の一覧（`ndf relay ls` の `items` と同じ形の JSON 配列）を `launch.by_name` で名前で引ける形にし、`machine_seats` を通して結果 JSON を作る。`launch.wrappers()` は呼ばない（プロセスを起こさない）。一覧を読めなければ `stopped`（原因 `invalid`） |
| `by_name` | `launch.py` | 新設 | `by_name(items: Any) -> Optional[dict[str, dict[str, Any]]]` | `items` が配列なら、`name` を持つ行を名前で引ける形にして返す。配列でなければ None。`wrappers` はこれを使う（中身の切り出しで、振る舞いは変えない） |
| `call` | `subcommand.py` | 変更 | `call(plugin_root, args, timeout, runner=subprocess.run, cwd=None, stdin: Optional[str] = None) -> Called` | `stdin` があれば `runner` へ `input=stdin` で渡す。ほかは今のまま（`ndf plan pending` などの呼び手は変えない） |
| `read` | `domains/relay/seats.py` | 新設 | `read(plugin_root: Path, timeout: float, listed: list[dict[str, Any]], runner: Any = subprocess.run) -> Seats` | `subcommand.call(plugin_root, ["lanes", "seats"], timeout, runner, stdin=json.dumps(listed))` を 1 回打ち、`items` の文字列だけを名前として返す。読めなければ名前を None にして理由を返す |
| `_counted` | `hub.py` | 新設 | `_counted(running: list[hub_decide.Running], held: tuple[str, ...], target: Optional[str]) -> tuple[int, bool]` | `own`（動いているこの NDF のラッパー、つまり `version` を持つものの名前の集合）と `held` から `target` を除いた名前の和集合の数 `len(own \| (set(held) - {target}))` と、`target` が `own` にあるか（`own_target`）を返す。**`target` は `own` からは除かない**（今の `_over_cap` の式と同じ）。動いている対象を除くのは、`hub_decide.replace`・`hub_decide.restart` が `others_running`（`target_running` が真なら 1 を引く）で 1 回だけ行う。`Lanes.running` は「動いている対象を含む数」と定義し、`hub_decide` の減算は変えない |
| `restart` | `hub.py` | 変更 | `restart(ctx: Context, req: Mapping[str, Any]) -> Outcome`（今は `name: str`） | `name` は `str(req["name"])`、`held` は `_held(req)`。本数を `_counted(running, held, name)` で数えて `hub_decide.restart` へ渡す。ほかは今のまま |

### `machine_seats`・`_cmd_seats`

- `ndf lanes seats` は引数を取らず、標準入力から登録簿の一覧を読む（頼む側が同じプロセスで読んだ `hub.listing` の結果）。標準入力が端末なら読まずに `stopped`（原因 `invalid`。「登録簿の一覧を標準入力に渡す」）を返し、待ち続けない。`_dispatch` は `a.action == "seats"` のとき、`_where`（チェックアウトの保存先を求める）より前に `_cmd_seats(a)` を返す。git の外の作業ディレクトリ（リレーの頼む側の作業ディレクトリ）から打てるようにするためである
- 同じ登録簿の一覧を頼む側とサブコマンドで 2 回読まない（サブコマンドの中で `ndf relay ls` を打つと、通常の起動と再起動のたびに CLI の起動が 2 本増える）
- 結果: `{"tool": "lanes", "status": "ok", "summary": "実行中の行を持つレーンのラッパー: <数> 本", "items": [<名前>…], "metrics": {"unreadable": [<計画の鍵>…], "legacy": <数>}}`。`items` は名前の文字列の昇順の配列である
- 事後条件: 同じ登録簿・索引・計画の状態について、索引の版 2 に載り開ける計画の分の `items` は、起動の許可（`admitted`）が `go` に渡す `held_seats` から自分の計画の分を除いたものと同じ規則（`lanes_of`・`held_seats`）で作られる（自分の計画を持たないので、`skip` は空）。起動の許可は行を始める起動の前に計画を索引に書く（`admitted` の `index_plan`）ので、実行中の行を持つ計画は耐久ワークフローが動く間は索引に載る
- `metrics` に `legacy`（`store_root` か `db` の無い版 1 の索引の計画の数）を足す。頼む側は `legacy` が 1 以上なら結果の `warnings` に「版 1 の索引の計画 <数> 件を数えていない（その計画の次の起動の許可で書き直される）」を 1 行載せる
- 索引に無く登録簿の conductor の行にだけ載る計画は読まない（`metrics.unreadable` に鍵を載せ、`items` に名前を含めない）。起動の許可はこの計画を `read_lanes` で読んで名前に含めるので、両者の名前を比べるのは索引の版 2 に載る計画に限る。そのラッパーが動いていればハブが数える。耐久ワークフローが終わって索引から外れた後も実行中の行が残る計画は、ラッパーが止まると数え落とす（既知の限界。#4051 の実装の後は、索引が数える行の無くなるまで残るので起きにくくなる）

### `seats.read`

```python
class Seats(NamedTuple):
    """`names` は実行中の行を持つレーンのラッパーの名前（読めなければ None）、`note` は読めなかった理由。"""
    names: Optional[tuple[str, ...]]
    note: str
```

- 結果の `items` が配列でなければ、読めなかったとする（`note` に「サブコマンド lanes seats の items が配列でない」）。配列の要素のうち空でない文字列だけを名前にする

### 頼む側（`cli._hub`）

- `args.action == "spawn"` で `--replace` も `--seated` も無いとき、または `args.action == "restart"` のとき、要求を書く前に `seats.read(ident.root, limits.seconds("relay.seats"), hub.listing(ctx))` を打つ（登録簿の一覧は同じプロセスで読み、`ndf relay ls` を打たない。`hub.listing` が `RegistryError` を出したら、名前を読めなかったときと同じ扱いにする）
  - 名前が読めたら、要求の `held` に名前の配列を載せる（0 件なら空の配列）
  - 読めなければ `held` を載せず、`note` を結果の `warnings` に 1 行で載せる
- 行を始める起動（`--replace`）の要求は今のとおり引数の `--held` を載せ、`seats.read` を打たない。続きの起動（`--seated`）・`stop`・`ls` も打たず、`held` を載せない（続きの起動は席で通り、ハブは続きの起動で自分の名前を `held` から除かないので、載せると自分を 2 回数えうる）
- `_outcome(got, warnings)` に `warnings` を足し、`result.result` と `result.stopped` へ渡す（空なら渡さない）
- 引数の `--held` が `--replace` を要する規則（`_validate`）は変えない。通常の起動の `held` は頼む側だけが作る

### ハブ（`hub.py`）

- `spawn` は `_over_cap(ctx, running, name if replacing else None, _held(req))` と、行を始める起動かどうかに依らず要求の `held` を渡す
- `_over_cap` は数えるところを `_counted(running, held, target)` に置き換える。判定（`hub_decide.replace` か `hub_decide.spawn`）と断りの項目は今のまま
- `handle` は `restart(requester(ctx, req), req)` を打つ
- `restart` は `lanes = hub_decide.Lanes(counted, limit, own_target, starts.count, ctx.max_starts)`（`counted, own_target = _counted(running, _held(req), name)`）で判定する。`counted` は動いている対象を含む数で、`hub_decide.restart` が `own_target` のとき 1 を引く（対象を引くのはこの 1 回だけ）。今の `target`（対象が動いているか。バージョンを問わない）は止める手（`_await_exit`）にだけ使い続ける
- **通常の起動（`--replace` も `--seated` も無い）は、名前が要求の `held` にあれば、上限の空きによらず断る**（`refused`、「<名前> は実行中の行を持つレーンのラッパー。--seated の続きの起動で起こす」。登録簿に書かない）。判定は `_over_cap` より前、`target and not replacing` の判定の次に置く。通常の起動は登録簿の行を `seat: null` で書くので、空きがあるときに通すと、そのレーンの正規の `--seated` の続きの起動が席の違いで断られるためである。`held` の無い要求（名前を読めなかった・旧い頼む側）は今のとおり断らない（既知の限界）
- 数えるときも、通常の起動は自分の名前を `held` から除かない（`target` は None）

## 異常系

| 関数 | 失敗 | 原因 | 検出 | 扱い | 利用者に見えるもの | 回復 |
| --- | --- | --- | --- | --- | --- | --- |
| `seats.read` | サブコマンドが 0 以外で終わる・時間内に返らない・結果が読めない（導入が壊れてサブコマンドが無いときを含む） | 計画の DB の誤り・ファイルシステムの誤り・壊れた導入 | `subcommand.call` が `result` を None で返す | `held` を載せずに要求を書く | 結果の `warnings` に理由 1 行（例「サブコマンド lanes seats の結果を読めない（終了コード 1）: …」） | 原因を直せば次の起動から数える |
| `cli._hub` | 登録簿を読めない | 状態の保存先の誤り | `hub.listing` が `RegistryError` | `seats.read` を打たず、`held` を載せずに要求を書く | 結果の `warnings` に理由 1 行 | 同上 |
| `_cmd_seats` | 標準入力の一覧を読めない（端末・JSON でない・配列でない） | 頼む側の誤り・手で打った | `json.loads` の失敗か `launch.by_name` が None | `stopped`（原因 `invalid`） | 頼む側は読めなかったとして上の行の扱い | 同上 |
| `spawn` | 通常の起動の名前が `held` にある | 実行中の行を持つレーンの名前を、`--seated` を付けずに起こした | 名前が `_held(req)` にある | 断る（登録簿に書かない） | `refused` の結果と理由 | `--seated` の続きの起動で起こす |
| `_cmd_seats` | 計画のステートマシンを読めない・索引に無い | チェックアウトが消えた・DB の誤り・表のバージョンが違う・戻しの最中・登録簿にだけ載る計画 | `read_indexed` が `readable` の偽の行、または索引に無い conductor の行 | 名前に含めず `metrics.unreadable` に載せる（`status` は `ok`） | 無し（そのラッパーは動いていればハブが数える） | 読めるようになった次の起動から数える |
| `_cmd_seats` | 版 1 の索引 | プラグインの更新の直後で、計画の次の起動の許可がまだ | `PlanPlace` の `store_root` か `db` が None | 開かずに `metrics.unreadable` に載せ、`metrics.legacy` に数える | 結果の `warnings` に版 1 の索引の行 1 行 | その計画の次の起動の許可が版 2 に書き直す（起動の許可はその間も数える） |
| `read_indexed` | 接続先を解けない | パスワードつきの接続先で、頼む側に同じ `NDF_PROGRESS_DB` が無い・別の DB を指す | `store.indexed_url` が None | 開かずに `metrics.unreadable` に載せる | 無し | 頼む側に計画と同じ `NDF_PROGRESS_DB` を渡す |
| `restart` | 要求に `name` が無い | 壊れた要求 | `KeyError` | 今のとおり `_handle_file` が `invalid` にする | `invalid` の結果 | — |

## 確定仕様の書き直し

| 節 | 書き直す中身 |
| --- | --- |
| 構成要素と責務 | `machine.py` の行に `read_indexed`・`machine_seats` を、`launch.py` の次に `progress/plan/cli.py` の `ndf lanes seats` を、`hub.py` の行に `_counted` で数えることを、`domains/relay/seats.py` の行を足す |
| 常に成り立つ条件 | 「起動の許可が数える数は、リレーハブが数える数以上」の行を「`ndf lanes seats` と登録簿を読めたとき、通常の起動・置き換えの起動・再起動でハブが数える数は、起動の許可が数える数と同じ規則で作られる」に直す。上限を下げない限り、続きの起動の後も動くラッパーが上限を超えない行を足す |
| リレーハブの起動の判定 | 表の `spawn`（`--replace` なし）と `restart` の本数の判定を「動いているラッパーと要求の `held`（頼む側が `ndf lanes seats` で読む）の和集合（`restart` は対象を 1 回だけ除く）」に直す。通常の起動の名前が `held` にあれば上限によらず断る行を足す。最後の段落を「上限を下げた直後だけ、続きの起動の後に一時的に上限を超え得る」に直す |
| エラー処理 | `ndf lanes seats` を読めないときの行（`held` を載せずに今の数え方で判定し、`warnings` に理由）を足す |
| 既知の限界 | 「ハブより早く待つ」の行を消す（ハブも同じ数え方になる）。「頼む側が読んでからハブが判定するまでに行を始めてすぐラッパーが落ちたレーンは数え落とす」と「`ndf lanes seats` を読めない間は今の数え方」と「旧い版のハブが処理する間（プラグインの更新の直後）は今の数え方」と「`ndf lanes seats` は索引に載る計画だけを読む（登録簿にだけ載る計画の止まったラッパーは数え落とす）」と「版 1 の索引の計画は次の起動の許可で書き直されるまで `ndf lanes seats` が数えない」と「パスワードつきの接続先の計画は、頼む側に同じ `NDF_PROGRESS_DB` が無いと数えない」の 6 行を足す |
| マシンの計画の索引 | ファイルの形（版と項目）はこの課題が持つ。#4051 は同じ節の消す時点と条件（索引の寿命）だけを直し、形の文に触らない（[issue-4051-design-detail.md](./issue-4051-design-detail.md) の「確定仕様」）。#4051 とこの課題の実装の順によらず、両方を適用した後の節は次の形になる。書く形は版 2 の `{"version": 2, "plan_id": <計画のステートマシンの ID>, "cwd": <作業ディレクトリ>, "plan": <計画の名前>, "store_root": <チェックアウトの保存先>, "db": {"url": <パスワードと認証情報のクエリを除き、ほかのクエリを残した接続先>, "exact": <除いたものが無いか>}}`。旧版は版 1 の `{"version": 1, "plan_id", "cwd", "plan"}` で、`_read_index` は今のとおり `plan_id`・`cwd`・`plan` だけを検証して読み、起動の許可は版によらず数え、#4051 の `unindex_idle` も版によらず消す条件を確かめる。`ndf lanes seats` は版 2 の `store_root`・`db`・`plan_id` だけで DB を読み取りだけで開き、Git も表の用意も検査も起こさず、版 1 は開かずに `unreadable` と `legacy` に数える。版 1 の索引は、その計画の次の起動の許可の `index_plan` が版 2 へ書き直す。今の節の `{"version": 1, "cwd", "plan"}`（`plan_id` が無い）も、この形へ直す |
| テスト観点 | 下の「テスト」の行を足す（Git を起こさない行・続きの起動で打たない行・接続先の行・開き方の行・版 1 の索引の行を含む） |
| 外部連携 | `### ndf lanes seats` の小見出しを足し、結果の形を書く |

用語集へ足す語は無い（「稼働中のレーン」「起動の許可」「リレーハブ」は用語集の語のまま使う）。

## テスト

| 縛る振る舞い | テストのファイル |
| --- | --- |
| 上限 N で、動いているラッパー N−1 本と `held` の止まったレーン 1 本の和が N のとき、通常の起動の要求が `refused`（`max-named`、`running: N`）になり起動しない。その後の止まったレーンの `--seated` の続きの起動が通り、動く本数が N である | `tests/domains/relay/test_relay_hub.py` |
| 同じ状態で、止まっている別の名前の `restart` が上限で断られ、対象が `held` にあれば対象を除いて数えて通る。日次の回数の断りは今のまま | 同上 |
| 動いている対象を含む境界: 上限 N で、対象を含めて N 本が動き `held` にも対象がある `restart` と置き換えの起動が通る（対象を 2 回引かない）。対象を含めて N 本が動き、`held` に止まった別のレーン 1 本があると断る（`running: N`）。対象を含めて N+1 本が動くと断る | 同上 |
| 上限に空きがある状態で、`held` にある名前（登録簿の席を持つ止まったレーン）の通常の起動が `refused` になり、登録簿のその行の `seat` が変わらない。その後の同じ席の `--seated` の続きの起動が通る。`held` に無い名前の通常の起動は今のとおり通る | 同上 |
| 頼む側が、通常の `spawn` と `restart` の要求に `ndf lanes seats` の名前を `held` で載せ、`--replace` の要求と `--seated` の要求では打たない（`runner` が 0 回で、`--seated` の要求に `held` が無い）。1 回の通常の起動・再起動で `runner` が 1 回だけ呼ばれ、その `input` に登録簿の一覧が載る。サブコマンドが読めないとき・登録簿を読めないとき、`held` を載せずに結果の `warnings` に理由が 1 行載る（`runner` を差し替える） | `tests/domains/relay/test_relay_cli.py` |
| `ndf lanes seats` が、標準入力の一覧から、索引（版 2）に載る計画の実行中の行を持つレーンの名前を返し、git の外の作業ディレクトリから打てる。名前を起動の許可の `held_seats` と比べるのは、索引の版 2 に載り両方の経路が読める計画だけで、同じ名前になる。索引に無く登録簿の conductor の行にだけ載る計画は、`items` に名前が無く `metrics.unreadable` に鍵が載る（同じ状態の起動の許可はその名前を含む）。`ndf relay ls` を打たない（`launch` の `run` を差し替えて呼ばれないことを確かめる）。索引の計画 3 件（別々のチェックアウト）を読む間に、子プロセスを起こす口（`subprocess.run`・`subprocess.Popen`）が 0 回しか呼ばれない（Git を起こさない）。標準入力が配列でなければ `stopped`（`invalid`）。同じ `plan_id` を 1 回だけ読む | `tests/progress/test_lane_machine.py`・`tests/progress/test_plan.py` |
| 接続先: 計画が保存先の外の SQLite（`NDF_PROGRESS_DB`）で起動の許可を通り、頼む側の環境に `NDF_PROGRESS_DB` が無いとき・別の SQLite を指すとき、`read_indexed` が索引の `db` の DB の名前を返す。パスワードつきの URL（`index_url` の `exact` が偽）は、頼む側の `NDF_PROGRESS_DB` が無いと `unreadable`、同じ URL なら開く URL がその値になる（`indexed_url` を直に縛る）。頼む側の `NDF_PROGRESS_DB` が同じ `plan_id` を持つ別の SQLite を指しても、その DB の行を名前に含めない。クエリだけが違う別の DB（計画が `postgresql://u:p@/ndf?host=db-a`、頼む側が `postgresql://u:p@/ndf?host=db-b`。どちらも同じ `plan_id` の行を持つ）では `indexed_url` が None を返し、`read_indexed` が頼む側の DB を開かずに `unreadable` を返す（`open_reader` を差し替えて呼ばれないことを確かめる）。パスワードだけが違う同じ接続先では開く。索引のファイルに、パスワードと認証情報のクエリ（`?sslpassword=…` を付けた URL で確かめる）の文字列が無い | `tests/progress/test_lane_machine.py`・`tests/progress/test_store_sqlite.py` |
| 開き方: 別の接続が `BEGIN IMMEDIATE` で書き込みを開いたまま（未確定の行あり）のとき、`open_reader` と `transaction` で確定済みの行を読み、`StoreBusy` にならない（`busy_timeout` は 0 なので、待つ形なら即座に落ちる）。目印の古い DB を開いても、目印の時刻・バックアップの一覧・表が変わらない。表の無い DB・実ファイルの無い保存先では `StoreStopped` で、表もファイルも作らない | `tests/progress/test_store_sqlite.py` |
| 版 1 の索引だけに載る計画（登録簿に conductor の行が無く、ラッパーは止まり、実行中の行を持つレーンが 1 本）で、更新の直後に、起動の許可（`machine_plans` → `count`・`held_seats`）がそのレーンを数えて上限 1 で待たせ、`held_seats` に名前を含める。同じ状態の `ndf lanes seats` は名前を返さず `metrics.unreadable` に鍵、`metrics.legacy` に 1 を載せ、頼む側の結果の `warnings` に版 1 の索引の行が 1 行載る。その計画の起動の許可を 1 回通した後、索引が版 2 になり、`ndf lanes seats` が名前を返す | `tests/progress/test_lane_machine.py`・`tests/progress/test_plan.py`・`tests/domains/relay/test_relay_cli.py` |

どのテストも、事象の数・回数・状態で判定し、時間で区切らない。
