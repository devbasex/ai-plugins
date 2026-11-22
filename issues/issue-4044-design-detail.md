# リレーハブの上限の詳細設計（#4044）

設計文書 [issue-4044-design.md](./issue-4044-design.md) の解き方を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 5 つ（`machine.machine_seats`・計画の CLI の `_cmd_seats`・`launch.by_name`・`seats.read`・`hub._counted`）、シグネチャを変える関数が 3 つ（`machine.machine_plans`・`subcommand.call`・`hub.restart`）あるので別ファイルにした。パスは `plugins/ndf/` からの相対で書く。

## 地図

```text
plugins/ndf/
├── src/ndf/progress/lane/machine.py    machine_plans の own を省けるようにし、machine_seats を足す
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
| 通常の `spawn`（`--replace` なし）・`restart` | 頼む側 1 本 | 頼む側 1 本 ＋ `ndf lanes seats` 1 本（中で `ndf relay ls` を打たない） |
| 置き換えの起動（`--replace`）・続きの起動（`--seated`）・`stop`・`ls` | 今のまま | 今のまま（`ndf lanes seats` を打たない） |

増えるのは、通常の起動と再起動の 1 回ごとに CLI の起動 1 本だけである。テストで `runner` の呼ばれた回数（1 回）を数えて縛る（下の「テスト」）。

## 構成要素

| ファイル | 種別 | 責務 | 依存 | 公開の単位 | 足す・変える関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/progress/lane/machine.py` | 変更 | マシンの数え方と起動の許可 | `launch`・`store`・`model` | モジュール | `machine_plans`（変更）・`machine_seats`（新設） |
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
| `machine_plans` | `machine.py` | 変更 | `machine_plans(own: Optional[PlanLanes], listed, places=None) -> list[PlanLanes]`（今は `own: PlanLanes`） | `own` が None なら自分の計画を持たずに、登録簿の conductor の行と索引の計画だけを 1 計画 1 回読む。None でなければ今のまま |
| `machine_seats` | `machine.py` | 新設 | `machine_seats(listed: Mapping[str, Mapping[str, Any]]) -> tuple[tuple[str, ...], tuple[str, ...]]` | `machine_plans(None, listed)` を読み、`held_seats(plans, "")`（実行中の行を持つレーンのラッパーの名前）と、読めない計画の鍵（`readable` が偽の `key`）を返す |
| `_cmd_seats` | `progress/plan/cli.py` | 新設 | `_cmd_seats(a: argparse.Namespace, stdin: TextIO = sys.stdin) -> dict[str, Any]` | 標準入力の登録簿の一覧（`ndf relay ls` の `items` と同じ形の JSON 配列）を `launch.by_name` で名前で引ける形にし、`machine_seats` を通して結果 JSON を作る。`launch.wrappers()` は呼ばない（プロセスを起こさない）。一覧を読めなければ `stopped`（原因 `invalid`） |
| `by_name` | `launch.py` | 新設 | `by_name(items: Any) -> Optional[dict[str, dict[str, Any]]]` | `items` が配列なら、`name` を持つ行を名前で引ける形にして返す。配列でなければ None。`wrappers` はこれを使う（中身の切り出しで、振る舞いは変えない） |
| `call` | `subcommand.py` | 変更 | `call(plugin_root, args, timeout, runner=subprocess.run, cwd=None, stdin: Optional[str] = None) -> Called` | `stdin` があれば `runner` へ `input=stdin` で渡す。ほかは今のまま（`ndf plan pending` などの呼び手は変えない） |
| `read` | `domains/relay/seats.py` | 新設 | `read(plugin_root: Path, timeout: float, listed: list[dict[str, Any]], runner: Any = subprocess.run) -> Seats` | `subcommand.call(plugin_root, ["lanes", "seats"], timeout, runner, stdin=json.dumps(listed))` を 1 回打ち、`items` の文字列だけを名前として返す。読めなければ名前を None にして理由を返す |
| `_counted` | `hub.py` | 新設 | `_counted(running: list[hub_decide.Running], held: tuple[str, ...], target: Optional[str]) -> tuple[int, bool]` | `own`（動いているこの NDF のラッパー、つまり `version` を持つものの名前の集合）と `held` から `target` を除いた名前の和集合の数 `len(own \| (set(held) - {target}))` と、`target` が `own` にあるか（`own_target`）を返す。**`target` は `own` からは除かない**（今の `_over_cap` の式と同じ）。動いている対象を除くのは、`hub_decide.replace`・`hub_decide.restart` が `others_running`（`target_running` が真なら 1 を引く）で 1 回だけ行う。`Lanes.running` は「動いている対象を含む数」と定義し、`hub_decide` の減算は変えない |
| `restart` | `hub.py` | 変更 | `restart(ctx: Context, req: Mapping[str, Any]) -> Outcome`（今は `name: str`） | `name` は `str(req["name"])`、`held` は `_held(req)`。本数を `_counted(running, held, name)` で数えて `hub_decide.restart` へ渡す。ほかは今のまま |

### `machine_seats`・`_cmd_seats`

- `ndf lanes seats` は引数を取らず、標準入力から登録簿の一覧を読む（頼む側が同じプロセスで読んだ `hub.listing` の結果）。標準入力が端末なら読まずに `stopped`（原因 `invalid`。「登録簿の一覧を標準入力に渡す」）を返し、待ち続けない。`_dispatch` は `a.action == "seats"` のとき、`_where`（チェックアウトの保存先を求める）より前に `_cmd_seats(a)` を返す。git の外の作業ディレクトリ（リレーの頼む側の作業ディレクトリ）から打てるようにするためである
- 同じ登録簿の一覧を頼む側とサブコマンドで 2 回読まない（サブコマンドの中で `ndf relay ls` を打つと、通常の起動と再起動のたびに CLI の起動が 2 本増える）
- 結果: `{"tool": "lanes", "status": "ok", "summary": "実行中の行を持つレーンのラッパー: <数> 本", "items": [<名前>…], "metrics": {"unreadable": [<計画の鍵>…]}}`。`items` は名前の文字列の昇順の配列である
- 事後条件: 同じ登録簿・索引・計画の状態について、`items` は起動の許可（`admitted`）が `go` に渡す `held_seats` から自分の計画の分を除いたものと同じ規則で作られる（自分の計画を持たないので、`skip` は空）

### `seats.read`

```python
class Seats(NamedTuple):
    """`names` は実行中の行を持つレーンのラッパーの名前（読めなければ None）、`note` は読めなかった理由。"""
    names: Optional[tuple[str, ...]]
    note: str
```

- 結果の `items` が配列でなければ、読めなかったとする（`note` に「サブコマンド lanes seats の items が配列でない」）。配列の要素のうち空でない文字列だけを名前にする

### 頼む側（`cli._hub`）

- `args.action == "spawn"` で `--replace` が無いとき、または `args.action == "restart"` のとき、要求を書く前に `seats.read(ident.root, limits.seconds("relay.seats"), hub.listing(ctx))` を打つ（登録簿の一覧は同じプロセスで読み、`ndf relay ls` を打たない。`hub.listing` が `RegistryError` を出したら、名前を読めなかったときと同じ扱いにする）
  - 名前が読めたら、要求の `held` に名前の配列を載せる（0 件なら空の配列）
  - 読めなければ `held` を載せず、`note` を結果の `warnings` に 1 行で載せる
- 行を始める起動（`--replace`）の要求は今のとおり引数の `--held` を載せ、`seats.read` を打たない。`stop`・`ls` も打たない
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
| `_cmd_seats` | 計画のステートマシンを読めない | チェックアウトが消えた・DB の誤り | `read_lanes` が `readable` の偽の行 | 名前に含めず `metrics.unreadable` に載せる（`status` は `ok`） | 無し（そのラッパーは動いていればハブが数える） | 読めるようになった次の起動から数える |
| `restart` | 要求に `name` が無い | 壊れた要求 | `KeyError` | 今のとおり `_handle_file` が `invalid` にする | `invalid` の結果 | — |

## 確定仕様の書き直し

| 節 | 書き直す中身 |
| --- | --- |
| 構成要素と責務 | `machine.py` の行に `machine_seats` を、`launch.py` の次に `progress/plan/cli.py` の `ndf lanes seats` を、`hub.py` の行に `_counted` で数えることを、`domains/relay/seats.py` の行を足す |
| 常に成り立つ条件 | 「起動の許可が数える数は、リレーハブが数える数以上」の行を「`ndf lanes seats` と登録簿を読めたとき、通常の起動・置き換えの起動・再起動でハブが数える数は、起動の許可が数える数と同じ規則で作られる」に直す。上限を下げない限り、続きの起動の後も動くラッパーが上限を超えない行を足す |
| リレーハブの起動の判定 | 表の `spawn`（`--replace` なし）と `restart` の本数の判定を「動いているラッパーと要求の `held`（頼む側が `ndf lanes seats` で読む）の和集合（`restart` は対象を 1 回だけ除く）」に直す。通常の起動の名前が `held` にあれば上限によらず断る行を足す。最後の段落を「上限を下げた直後だけ、続きの起動の後に一時的に上限を超え得る」に直す |
| エラー処理 | `ndf lanes seats` を読めないときの行（`held` を載せずに今の数え方で判定し、`warnings` に理由）を足す |
| 既知の限界 | 「ハブより早く待つ」の行を消す（ハブも同じ数え方になる）。「頼む側が読んでからハブが判定するまでに行を始めてすぐラッパーが落ちたレーンは数え落とす」と「`ndf lanes seats` を読めない間は今の数え方」と「旧い版のハブが処理する間（プラグインの更新の直後）は今の数え方」の 3 行を足す |
| テスト観点 | 下の「テスト」の 4 行を足す |
| 外部連携 | `### ndf lanes seats` の小見出しを足し、結果の形を書く |

用語集へ足す語は無い（「稼働中のレーン」「起動の許可」「リレーハブ」は用語集の語のまま使う）。

## テスト

| 縛る振る舞い | テストのファイル |
| --- | --- |
| 上限 N で、動いているラッパー N−1 本と `held` の止まったレーン 1 本の和が N のとき、通常の起動の要求が `refused`（`max-named`、`running: N`）になり起動しない。その後の止まったレーンの `--seated` の続きの起動が通り、動く本数が N である | `tests/domains/relay/test_relay_hub.py` |
| 同じ状態で、止まっている別の名前の `restart` が上限で断られ、対象が `held` にあれば対象を除いて数えて通る。日次の回数の断りは今のまま | 同上 |
| 動いている対象を含む境界: 上限 N で、対象を含めて N 本が動き `held` にも対象がある `restart` と置き換えの起動が通る（対象を 2 回引かない）。対象を含めて N 本が動き、`held` に止まった別のレーン 1 本があると断る（`running: N`）。対象を含めて N+1 本が動くと断る | 同上 |
| 上限に空きがある状態で、`held` にある名前（登録簿の席を持つ止まったレーン）の通常の起動が `refused` になり、登録簿のその行の `seat` が変わらない。その後の同じ席の `--seated` の続きの起動が通る。`held` に無い名前の通常の起動は今のとおり通る | 同上 |
| 頼む側が、通常の `spawn` と `restart` の要求に `ndf lanes seats` の名前を `held` で載せ、`--replace` の要求では打たない。1 回の通常の起動・再起動で `runner` が 1 回だけ呼ばれ、その `input` に登録簿の一覧が載る。サブコマンドが読めないとき・登録簿を読めないとき、`held` を載せずに結果の `warnings` に理由が 1 行載る（`runner` を差し替える） | `tests/domains/relay/test_relay_cli.py` |
| `ndf lanes seats` が、標準入力の一覧から、索引だけに載る計画と登録簿の conductor の行の計画の実行中の行を持つレーンの名前を返し、読めない計画を `metrics.unreadable` に載せ、git の外の作業ディレクトリから打てる。`ndf relay ls` を打たない（`launch` の `run` を差し替えて呼ばれないことを確かめる）。標準入力が配列でなければ `stopped`（`invalid`）。`machine_plans(None, …)` が同じ計画を 1 回だけ読む | `tests/progress/test_lane_machine.py`・`tests/progress/test_plan.py` |

どのテストも、事象の数・回数・状態で判定し、時間で区切らない。
