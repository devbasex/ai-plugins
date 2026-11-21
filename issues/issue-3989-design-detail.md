# #3989 の詳細設計: 同時実行の制御を稼働中のレーンの数とテストのスロットの 2 層にする

設計文書 [issue-3989-design.md](./issue-3989-design.md) の決定 1〜7 を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く（`docs/ndf-parallel-model.md` だけはリポジトリの根から）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/
│   ├── contracts/named_cap.py *     上限の値の読み書き（マシンの設定 → 環境変数 → limits.json）
│   ├── progress/lane/
│   │   ├── types.py                 PlanView の max_lanes・max_plans、LaneSpec の plans を外す
│   │   ├── fold.py                  記録の 3 つの値を読まない
│   │   ├── rules.py                 I20・I2 を消す
│   │   ├── machine.py               稼働中のレーンとほかのラッパーを 1 本ずつ数える・上限を named_cap で読む・排他の確かめ・計画の索引
│   │   ├── launch.py                spawn --replace --seat（続きは --seated も）で置き換え、ハブの上限の断りを待ちに変える
│   │   └── __init__.py              説明から I20 を外す
│   ├── progress/plan/
│   │   ├── __init__.py              計画の行とレーンの行から 3 つの値を外す・待ちの理由と metrics.machine
│   │   ├── cli.py                   ndf lanes limits と ndf lanes set --plans を消す
│   │   └── run.py                   起動の許可へ plans を渡さない・止めてから spawn する手順を spawn --replace へ・終わるときに索引を消す
│   └── domains/relay/
│       ├── cli.py                   ndf relay cap・hub_context の上限
│       ├── hub.py                   判定のたびに上限を読む（無ければ要求の cap）・置き換えの起動（席の確かめ・停止前の確かめ）・NamedCapError を結果にする・一覧に錠だけのラッパーを載せる
│       └── hub_decide.py            置き換えの起動の判定（本数だけ。続きの起動はハブが呼ぶ前に分ける）
├── contracts/
│   ├── domains/env.json             NDF_RELAY_MAX_NAMED の説明（読む順の正本）
│   ├── limits.json                  relay.max-named の説明
│   ├── workflow/parallel-plan.schema.json
│   └── schemas/io/{lanes,relay}.{args,result}.schema.json
├── docs/contracts/{commands,env}.md   env.md は env.json から再生成
├── skills/…/parallel-plan.md・parallel.md・parallel-checks.md・parallel-cross-repo.md・parallel-work.md
└── tests/
    ├── contracts/test_named_cap.py *
    ├── progress/test_lane.py・test_plan.py・test_lane_machine.py・test_run_lanes.py・test_lane_launch.py
    ├── support/plan_decls.py
    └── domains/relay/test_relay_cli.py・test_relay_hub.py
docs/ndf-parallel-model.md
```

依存の向き: `contracts/named_cap.py` は同じパッケージの `ndf.contracts`（`limits`・`facts`・`ContractError`）だけに依存し、`progress/lane/machine.py`・`progress/plan/__init__.py`（`machine` 経由）・`domains/relay/hub.py`・`domains/relay/cli.py` がそれを呼ぶ。`progress` から `domains` へは依存しない（ラッパーの一覧は今のまま `ndf relay ls` の結果で読む）。状態の保存先のパスは呼ぶ側が渡し、`named_cap` は `identity` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/contracts/named_cap.py` | 新設 | 同時に動かす数の上限を 1 つの順で読み、マシンの設定を書く | `ndf.contracts` | モジュール・`Cap`・`NamedCapError` | `path`・`read`・`fallback`・`write`・`parse`（新設） |
| `src/ndf/progress/lane/types.py` | 変更 | 計画の状態の型 | — | `PlanView`・`LaneSpec` | `PlanView.max_lanes`・`max_plans`、`LaneSpec.plans` を消す |
| `src/ndf/progress/lane/fold.py` | 変更 | 計画の事象の畳み込み | `types` | モジュール | `_spec`・`_changed_spec`・`_plan_base`・`_plan_limits`（変更） |
| `src/ndf/progress/lane/rules.py` | 変更 | 計画の決まり | `types` | モジュール | `_limit_errors` を消し、`plan_errors` から外す。説明の I2・I20 を外す |
| `src/ndf/progress/lane/machine.py` | 変更 | マシン全体の稼働中の数と起動の許可 | `named_cap`・`launch`・`liveness` | `PlanLanes`・`Count`・モジュール | `PlanLanes`・`lanes_of`・`count`・`admit`・`waiting`・`cap`・`admitted`・`machine_plans`（変更）・`lock_free`・`index_plan`・`unindex_plan`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | レーンのステートマシンの読み書き | — | モジュール | 説明の I20 を外す |
| `src/ndf/progress/plan/__init__.py` | 変更 | 計画のステートマシンの関数と状態の表示 | `machine` | モジュール | レーンの行を組む関数（75 行）・`plan_payload`・`machine_waits`・`_plan_metrics`・`status`（変更）。`LIMIT_POLICY`・`_default_limit` を消す |
| `src/ndf/progress/plan/cli.py` | 変更 | `ndf lanes` のサブコマンド | `plan` | モジュール | `_cmd_set`（変更）・`_cmd_limits` と `limits` の引数を消す |
| `src/ndf/progress/plan/run.py` | 変更 | 耐久ワークフローの手 | `machine`・`launch` | モジュール | `launch_lane`・耐久ワークフローの本体（終わるときに `unindex_plan`）（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | レーンのラッパーの起動と停止 | `relay` のサブコマンド | モジュール | `spawn`（`replace`・`seat`・`seated` を足す）・`read_spawn`（ハブの上限の断りを `waiting` に変える）（変更） |
| `src/ndf/domains/relay/hub.py` | 変更 | リレーハブの判定と一覧 | `named_cap`・`registry` | `Context` | `Context`（`max_named` を消す）・`submit`・`handle`・`requester`・`spawn`・`restart`・`listing`（変更）・`_max_named`・`_seated_ok`・`_preflight`（新設） |
| `src/ndf/domains/relay/hub_decide.py` | 変更 | リレーハブの起動の判断（純粋な関数） | — | `Lanes` | `replace`（新設）。`spawn`・`restart`・`mixed` は変えない |
| `src/ndf/domains/relay/cli.py` | 変更 | `ndf relay` のサブコマンド | `named_cap`・`hub` | モジュール | `hub_context`・`_hub`・`main`（変更）・`_cap`（新設）・`cap` の引数・`spawn --replace`・`--seat`・`--seated` |
| `contracts/workflow/parallel-plan.schema.json` | 変更 | 計画の設定の形 | — | — | `max_lanes`・`max_plans`・レーンの `plans`（`required` からも）を消す |
| `contracts/schemas/io/lanes.args.schema.json`・`lanes.result.schema.json` | 変更 | `ndf lanes` の引数と結果の形 | — | — | `limits` のサブコマンド・`set --plans`・`max_lanes`・`max_plans` を消し、`machine` と `machine_wait` の新しい項目を足す |
| `contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` | 変更 | `ndf relay` の引数と結果の形 | — | — | `cap` の引数（`--named`・`--clear`）と結果（`value`・`source`）、`spawn` の `--replace`・`--seat`・`--seated`、要求の `cap`（値か解けなかった文）、上限の断りの `items`（`refusal`・`running`・`cap`）、`ls` の行の `registered` を足す |
| `contracts/domains/env.json`・`contracts/limits.json` | 変更 | 環境変数と上限の設定の正本 | — | — | `NDF_RELAY_MAX_NAMED` の `meaning` を「名前つきのラッパーとレーンの同時の数の上限。マシンの設定（`ndf relay cap`）が無いときに読み、無ければ上限の設定 `relay.max-named`。1 以上の整数」に直す（今の「無ければマシンの設定」は順が逆）。`relay.max-named` の `summary` も同じ順に直す |
| `docs/contracts/commands.md`・`env.md` | 変更 | コマンドと環境変数の一覧（生成物） | `env.json`・引数のスキーマ | — | 手で直さず、正本を直してから規約の描画（`ndf.checks.spec_render`）で再生成する。`ndf lanes limits` の行が消え、`ndf lanes set`・`ndf relay cap`・`ndf relay spawn --replace`・`--seat`・`--seated` の行と `NDF_RELAY_MAX_NAMED` の読む順が正本に従う |
| `skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | 計画の書き方 | — | — | 例とレーンの項目の表から `plans` を、計画の項目から 2 つの上限を、規則の表から I2・I20 を消し、同時実行は 2 つの層で絞ると書く |
| `skills/development-workflow/references/parallel.md` | 変更 | オーガナイザーの手順 | — | — | `ndf lanes set` の `--plans` を消し、「上限を変える」の行を `ndf relay cap` へ |
| `skills/development-workflow/references/parallel-checks.md` | 変更 | マシンの上限の説明 | — | — | 2 つの層・数える単位（レーン 1 本）・読む順・ハブとの一致・待ちの理由 3 つ・`metrics.machine` |
| `skills/development-workflow/references/parallel-cross-repo.md` | 変更 | 別のリポジトリのレーンの手順 | — | — | 例の `plans`、「上限を超える」と「I20 で断られた」の行を消す |
| `skills/development-workflow/references/parallel-work.md` | 変更 | 並行の決まり | — | — | 並行の本数の行の `ndf lanes limits` を 2 つの層へ |
| `docs/ndf-parallel-model.md` | 変更 | 並行開発の役割と用語 | — | — | 141 行の `plans` と上限の説明を 2 つの層へ |
| `tests/contracts/test_named_cap.py` | 新設 | 読む順・整数の契約・書き方 | `named_cap` | — | — |
| `tests/progress/test_lane.py`・`test_plan.py`・`test_lane_machine.py`・`test_run_lanes.py`・`test_lane_launch.py`・`tests/support/plan_decls.py` | 変更 | I20・I2・`plans` のテストを消し、受け入れ条件 1〜10 のテストを足す | — | — | — |
| `tests/domains/relay/test_relay_cli.py`・`test_relay_hub.py` | 変更 | `ndf relay cap`・ハブの判定が書いた値と要求の値を読むこと・置き換えの起動（席・混在・停止前の確かめ）・壊れた設定の後ろの要求・一覧の錠だけのラッパー | — | — | — |

## 上限の値（named_cap）

満たす決定: 決定 4、受け入れ条件 7・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `path` | `src/ndf/contracts/named_cap.py` | 新設 | `path(state_dir: Path) -> Path` | マシンの設定のパス（`<state_dir>/relay/hub/cap.json`。ハブの保存先 `hub.hub_dir` と同じ親） |
| `read` | 同上 | 新設 | `read(environ: Mapping[str, str], state_dir: Path, fallback: Optional[Union[Cap, NamedCapError]] = None) -> Cap` | マシンの設定 → `NDF_RELAY_MAX_NAMED` → `limits.json` の `relay.max-named` の順で、最初にある値と出所を返す。マシンの設定があればその値を返し、環境変数と上限の設定を読まない（壊れていても例外を出さない）。マシンの設定が無ければ、`fallback` に `Cap` を渡したときはそれを、`NamedCapError` を渡したときはそれを送出し、渡さないときは `fallback(environ)` を返す（ハブが要求に載った頼む側の値か解析の誤りで判定する） |
| `fallback` | 同上 | 新設 | `fallback(environ: Mapping[str, str]) -> Cap` | マシンの設定を読まずに、`NDF_RELAY_MAX_NAMED` → `limits.json` の `relay.max-named` の順で値と出所（`env`・`limits`）を返す。ハブへの要求に載せる `cap` はこの値で、マシンの設定から独立する |
| `parse` | 同上 | 新設 | `parse(raw: object, where: str) -> int` | 1 以上の整数だけを返す。文字列は `^[1-9][0-9]*$` に合うものだけ、数は `bool` でない `int` だけを受け、ほかは `where`（ファイル・環境変数・上限の設定の名前）を添えた `NamedCapError` |
| `write` | 同上 | 新設 | `write(state_dir: Path, value: Optional[int]) -> None` | 値を書く。`None` ならファイルを消す |

- `read`
  - 事後条件: `Cap.value` は 1 以上の整数で、`Cap.source` は `machine`・`env`・`limits` のどれか。呼ぶたびにファイルを読み直し、値を持ち越さない。3 つの出所の値はどれも `parse` を通す（`limits.from_env` は正の浮動小数点数を返し、非有限値も通すので使わない。`limits.value` で上限の設定の生の値を読み、`parse` へ渡す）
  - 副作用: ファイルを読むだけ
- `write`
  - 事後条件: 値を書いたら `{"version": 1, "max_named": <値>}` のファイルがある。置き換えは一時ファイルからの改名で行い、読む側は半端な中身を読まない
  - 副作用: ファイルを書く・消す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read` | マシンの設定が JSON でない・`version` が 1 でない・`max_named` が 1 以上の整数でない | 手で書き換えた | `read`・`parse` | 止める（`NamedCapError`、`ContractError` の子） | 呼び手ごとの扱いは下の表。文にファイルのパスと誤り | `ndf relay cap --named`・`--clear` で書き直してから打ち直せる |
| `read` | `NDF_RELAY_MAX_NAMED` が 1 以上の整数でない（`0`・`-1`・`0.5`・`1.5`・`1e1`・`inf`・`nan`・数でない） | 環境変数の誤り | `parse` | 止める（`NamedCapError`。切り捨てない） | 同上。文に環境変数の名前と値 | 環境変数を直して打ち直せる |
| `read` | 上限の設定 `relay.max-named` が 1 以上の整数でない | プラグインの設定の誤り | `parse` | 止める（`NamedCapError`） | 同上 | 設定を直したバージョンを入れる |

`NamedCapError` を捕まえる箇所（どこでも例外のまま呼び手の外へ出さない）:

| 呼び手 | 捕まえる箇所 | 結果 |
| --- | --- | --- |
| ハブの要求（`spawn`・`restart`） | `hub.handle` の `except` に `named_cap.NamedCapError` を足す（`registry.RegistryError` と並べる） | `Outcome(False, 文, "invalid")`。`drain` はその要求に結果を書いてファイルを消し、後ろの要求を処理する |
| 頼む側の `spawn`・`restart`（要求に載せる `cap` を解く `submit`） | `submit` が `named_cap.fallback` を `try` の中で呼んで捕まえる | 止めない。要求の `cap` に `{"error": 文}` を載せて書く。ハブはマシンの設定が無いときだけその文で `invalid` にする（マシンの設定を優先する規約。設定があれば頼む側の環境変数の誤りでは止めない） |
| `ndf relay cap`（引数なし） | 同上 | 同上 |
| 起動の許可（`admitted`） | `admitted` が `cap()` を `try` の中で呼び、`NamedCapError` を捕まえる | 起動の手の失敗 `{"ok": False, "final": True, "detail": 文}` |
| `ndf lanes status` | `machine_waits` が `cap()` を呼ぶ箇所で捕まえる | 止めない。`metrics.machine` の `cap` を null・`error` に文、待ちは載せない。終了コードは今のまま |
| `write` | 値が 1 未満 | 引数の誤り | `write` の呼び手（`_cap`） | 止める | 原因 `invalid`（終了コード 2） | 書かない。打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Cap` | 型（`NamedTuple`: `value: int`・`source: str`） | `src/ndf/contracts/named_cap.py` | — |
| `FILE` | 定数 | 同上 | `relay/hub/cap.json`（状態の保存先からの相対） |
| `ENV` | 定数 | 同上 | `NDF_RELAY_MAX_NAMED` |
| `POLICY` | 定数 | 同上 | `("relay.max-named", "count")`。`machine.CAP_POLICY` はこれを指す（同じ事実を 2 か所に持たない）。`plan.LIMIT_POLICY` は消す |
| `NamedCapError` | 例外（`contracts.ContractError` の子） | 同上 | — |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `ndf relay cap` と、起動の許可・ハブ・`status` の読み | マシンの設定のファイル | 取らない。書く側は改名で置き換え、読む側は書く前か後のどちらかの中身を丸ごと読む | 同じ値を何度書いても同じ。読む側は次の判定で読み直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `read` | `limits.from_env`（環境変数 → `limits.json`） | `git grep -n "def from_env" plugins/ndf/src` | 使わない。正の浮動小数点数を返し、`0.5`・`inf` を通す。1 以上の整数の契約は `parse` が持ち、上限の設定は `limits.value` で読む |
| `write` | `relay.signals.replace_json`（一時ファイルからの改名） | `git grep -n "def replace_json" plugins/ndf/src` | 使えない。`domains` にあり、`core` から呼ぶと依存が逆向きになる。同じ形（一時ファイル + `os.replace`）を `core` に書く。共有の場所へ移すかは実装の `refactoring` で見る |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `read` | ファイルがあれば環境変数より先に読み、`source` が `machine`。無ければ環境変数、それも無ければ `limits.json`。`fallback` を渡すと、ファイルが無ければ環境変数を読まずにそれを返し、ファイルがあればファイルの値。ファイルが 6・環境変数が `1.5` なら `Cap(6, "machine")`（例外を出さない）。`fallback` に `NamedCapError` を渡すと、ファイルが無ければそれを送出し、ファイルがあればファイルの値 | 書いた直後の同じプロセスの 2 回目の `read` が新しい値を返す | 壊れたファイルで `NamedCapError` |
| `fallback` | ファイルが 6・環境変数が 2 なら `Cap(2, "env")`。環境変数が無ければ `limits` | ファイルがあっても読まない（壊れたファイルでも例外を出さない） | 壊れた環境変数で `NamedCapError` |
| `parse` | `1`・`"1"`・`"8"` を通す | `"1"` は通り `"0"` は断る | `0`・`-1`・`True`・`1.5`・`"0.5"`・`"1.5"`・`"1e1"`・`"inf"`・`"nan"`・`"many"` で `NamedCapError`（環境変数・ファイル・上限の設定のそれぞれで 1 つずつ） |
| `write` | 書いた値を `read` が返す。`None` の後は環境変数に戻る | 1 を書ける | 0 を渡すと断る（呼び手） |

## 起動の許可（machine・run）

満たす決定: 決定 3・4・6・7、受け入れ条件 5・6・7・8・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PlanLanes` | `src/ndf/progress/lane/machine.py` | 変更 | `lanes: tuple[tuple[str, bool], ...]`（ラッパーの名前, 実行中の行を持つか） | 重み（`plans`）を持たない |
| `lanes_of` | 同上 | 変更 | 変えない | `(ラッパーの名前, 実行中の行を持つか)` を組む |
| `count` | 同上 | 変更 | 変えない（`count(plans, listed, skip) -> Count`） | 稼働中のレーンのラッパーの名前と、一覧で動いているラッパーの名前（役に依らない）の和集合から `skip` を除いた数を数える |
| `admit` | 同上 | 変更 | `admit(total: int, cap: int) -> bool` | `total + 1 <= cap` |
| `waiting` | 同上 | 変更 | `waiting(reason: str, got: Optional[Count], cap: Optional[int]) -> dict[str, Any]` | 待ちの結果（`reason`・`machine_lanes`・`cap`・`unreadable`）。排他の待ち（`lock`）は `got` と `cap` が None で、`machine_lanes`・`cap` を null にする（上限を読む前に排他を待つので、数も上限も無い） |
| `cap` | 同上 | 変更 | `cap(environ: Optional[Mapping[str, str]] = None, state_dir: Optional[Path] = None) -> int` | `named_cap.read` の値。`state_dir` を省けば `identity.load().state_dir` |
| `admitted` | 同上 | 変更 | `admitted(own: PlanLanes, lane_wrapper: str, listed: Callable[[], Optional[Mapping[str, Mapping[str, Any]]]], go: Callable[[tuple[str, ...]], dict[str, Any]], where: PlanPlace) -> dict[str, Any]` | 排他の中で `cap` を読み、数えて、収まれば `index_plan(where)` を書いてから `go` を打つ。`go` には、数えた計画のうち実行中の行を持つレーンのラッパーの名前を、ラッパーの生死にかかわらず渡し（自分のレーンを除く。一覧を読めなければ自分の計画の分。`held_seats`）、ハブはそれと自分が見る動いているラッパーの和集合を数える（許可の後にラッパーだけが止まった稼働中のレーンを数え落とさない） |
| `machine_plans` | 同上 | 変更 | `machine_plans(own: PlanLanes, listed: Mapping[str, Mapping[str, Any]], indexed: Sequence[PlanPlace]) -> list[PlanLanes]` | 自分の計画・一覧の conductor の行の計画・索引の計画の和を 1 計画 1 回読む。同じ計画が別の所在（別の worktree）に載っても、計画のステートマシンの ID を解いた段階で読んだ計画を飛ばす。索引の行は ID を持つので、読んだ計画なら所在も解かない |
| `index_plan` | 同上 | 新設 | `index_plan(where: PlanPlace) -> None` | マシンの計画の索引（`<状態の保存先>/parallel/plans/<計画のステートマシンの ID>.json`、`{"version": 1, "cwd", "plan"}`）を一時ファイルからの改名で書く。同じ中身なら書かない |
| `unindex_plan` | 同上 | 新設 | `unindex_plan(plan_id: str) -> None` | 索引のファイルを消す（無くてもよい） |
| `indexed` | 同上 | 新設 | `indexed() -> list[PlanPlace]` | 索引の計画の所在。読めないファイルは飛ばす |
| `lock_free` | 同上 | 新設 | `lock_free() -> bool` | マシンの起動の排他を待たずに 1 回だけ取り、取れたらすぐ放して真を返す |
| `launch_lane` | `src/ndf/progress/plan/run.py` | 変更 | 変えない | `admitted` へ `plans` を渡さず、計画の所在を渡す。`go` は `launch.stop` を打たず、`launch.spawn` の 1 回にする（止めるのはハブが判定した後）。席（`seat`）は `<レーンのステートマシンの ID>:<行のキー>`（`act.row`）。行を始める起動は `launch.replace` に `Seat(席, held=<admitted が渡した名前>)`、続きの起動（`act.continues`。中止・回答の後の next・`/goal` の入れ直し）は同じ行の席で `Seat(席, seated=True)` を打ち、今のまま `admitted` を通らない |
| 耐久ワークフローの本体 | 同上 | 変更 | 変えない | 終わるとき（`paused`・`stopped`・`done` など、どの終わり方でも）に `unindex_plan` を打つ |
| `replace`・`Seat` | `src/ndf/progress/lane/launch.py` | 新設 | `replace(repo, plan, lane, cwd, first, seat: Seat) -> dict[str, Any]`・`Seat(seat: str, seated: bool = False, held: tuple[str, ...] = ())` | `ndf relay spawn --replace --seat <席>` に、`seated` なら `--seated`、`held` の名前ごとに `--held <名前>` を足して打つ（`--name` は今のまま `wrapper_name`）。`spawn` は今のまま |
| `read_spawn` | 同上 | 変更 | 変えない | 結果が上限の断り（`items` に `refusal: "max-named"`）なら `{"ok": False, "waiting": {"reason": "machine-lanes", "machine_lanes": running, "cap": cap, "unreadable": []}, "detail": 文}` を返す |

- `count`
  - 事後条件: 数えるのは次の 3 つで、同じ名前は 1 回、`skip` は数えない。(1) 読めた計画の実行中の行を持つレーンのラッパー。(2) 一覧で `running` のラッパー（計画のレーンか否か・役に依らない）。(3) 読めない計画の動いているラッパー（`unreadable` に計画を載せる。今のまま）
  - ハブとの関係: (2) は `ndf relay ls` が載せる錠を持つラッパーのすべてで、ハブの `_own_count` が数えるラッパーを含む
- `admitted`
  - 手順: 排他を取る → `cap()` → 一覧 → 数える → 収まらなければ `waiting`、収まれば `index_plan` → `go`。`go` の結果がハブの上限の断り（`waiting` を持つ）なら、そのまま返す
- 索引
  - 書くのは起動の許可が通ったときだけで、`admit.lock` の中で書く。これで、実行中の行を持つ計画は、その行を始めた起動の時点から索引に載る
  - 消すのは耐久ワークフローが終わるときだけ。異常終了で残ったファイルは、読む側が実行中の行の無い計画として 0 本と数える。`ndf lanes start` で同じ計画を打ち直せば、次の起動の許可が同じファイルを書く
  - `PlanPlace` は `(plan_id, cwd, plan)` の `NamedTuple`
- `lock_free`
  - 事後条件: 排他を持ったまま返らない
  - 副作用: 排他のファイルを開く（無ければ作る。`durable.open_lock_file` と同じ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `admitted` | マシンの設定・環境変数・上限の設定の値が 1 以上の整数でない | 手で書き換えた | `cap` → `named_cap.read` | `admitted` が `cap()` を排他の `try` の中で呼び、`NamedCapError` を捕まえて起動の手の失敗 `{"ok": False, "final": True, "detail": 文}` で返す（打ち直しても同じ失敗）。例外のまま耐久ステップの外へ出さない | `launch_failed` の知らせの詳細にファイルか環境変数の名前と誤り | `ndf relay cap` で直し、リクエストを閉じれば起動の候補へ戻る |
| `admitted` | 排他を取れない | 別の計画が数えている | `admit_lock` | 待ち（`lock`。`machine_lanes`・`cap` は null） | 無し（`status` の待ち） | 次の周で打ち直す |
| `launch_lane` | 行を始める起動をハブが上限で断った（許可の後にほかの起動が枠を使った・上限が下がった） | 排他の隙間・上限の変更 | `read_spawn` | 待ち（`machine-lanes`）。元のラッパーはハブが止めていない。続きの起動（`seated`）はハブが本数で断らないので、この行に当たらない | 無し（`status` の待ち。レーンは未着手の行を持つままなので `wakeable_lanes` に載り、`status` が数え直して出す） | 次の周で打ち直す |
| `index_plan` | 索引を書けない | 権限・保存先が無い | `index_plan` | 起動の手の失敗（`final` でない）。`go` を打たない | `launch_failed` の知らせ（回数が尽きたら） | 次の周で打ち直す |
| `lock_free` | 排他のファイルを開けない | 権限・保存先が無い | `lock_free` | 偽を返す（待ちの理由を `lock` にする） | `status` の待ちが `lock` | 次の `status` で読み直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `count` | 読めた計画のレーン・実行中の行がある | 1 |
| `count` | 読めた計画のレーン・実行中の行が無い・一覧で動いている | 1（起動の直後・終わったのにまだ止めていないラッパー） |
| `count` | 計画のレーンでない・一覧で動いている（役に依らない。登録簿に無く錠だけのものを含む） | 1 |
| `count` | 読めない計画の動いているラッパー | 1、計画を `unreadable` へ |
| `count` | `skip`（起動しようとしているレーン） | 0 |
| `admitted` | 一覧を読めない | 自分の計画の実行中の行を持つレーンだけで数え、待ちの理由は `observe`（今のまま） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つの計画の `launch_lane` | ラッパーの一覧と稼働中の数 | 今のまま `admit_lock` の中で数えてから spawn する。spawn の後に排他を放すので、次の判定は起動したラッパーを一覧で数える | 待ちは失敗に数えず、次の周で数え直す |
| `launch_lane` と、`admit.lock` を取らない `ndf relay spawn`・`ndf relay cap --named` | ハブの空きと上限 | 2 つの排他を 1 つにはしない（耐久ワークフローは `admit.lock` を持ったまま子のハブを待つので、ハブが同じ排他を取ると詰まる）。代わりに、ハブが `hub.lock` の中で元のラッパーを止める前に判定し、上限の断りを起動の手が待ちに戻す（決定 7） | 断られても元のラッパーは動いたまま。次の周で数え直す |
| `admitted` の `index_plan` と、ほかの計画の `machine_plans` | 索引のファイル | `index_plan` は `admit.lock` の中で書き、読む側（起動の許可）も同じ排他の中で読む。`status` は排他の外で読み、書く前か後のどちらかを丸ごと読む | 同じ中身を何度書いても同じ |
| 同じ計画の同じ周の 2 つの `launch_lane` | 同上 | 判断が 1 手ずつ打つので重ならない。2 手目は 1 手目のラッパーを数える | 同上 |
| `ndf lanes status` の `lock_free` と `launch_lane` | `admit.lock` | `status` は取れるかを 1 回試してすぐ放す。待たない | `status` は何も書かない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `count` | オーガナイザーの役の動いているラッパーと、登録簿に無く錠だけのラッパーを 1 本ずつ数える。`plans` に依らず 1 | 起動するレーン自身のラッパーは数えない。実行中の行を持つのにラッパーが止まったレーンは数える | — |
| `admitted` | 別の計画 2 つ（ステートマシンの ID が違う）の同時の判定で、起動の後の稼働中の数が上限以内。許可した起動は索引に計画を書く | 稼働中の数 = 上限 - 1 なら起動し、= 上限なら待つ | マシンの設定・環境変数が壊れていると `final` の失敗（例外が出ない）。排他を取れないと `waiting` の `machine_lanes`・`cap` が null |
| `machine_plans` | 上限 1 で計画 A が回答待ちの行を持ち、A のラッパーが止まって登録簿に A の行が無くても、索引から A を読んで 1 本と数え、計画 B の起動を待たせる | 索引に残った終わった計画は 0 本 | 読めない索引のファイルは飛ばす |
| `launch_lane`・`read_spawn`（`test_lane_launch.py`・`test_run_lanes.py`） | 許可の後にハブが上限で断った結果（`refusal: "max-named"`）を `waiting`（`machine-lanes`）にし、判断は失敗にも `launch_failed` にもしない。`go` は `launch.stop` を打たない。行を始める起動は `--replace --seat <席>`、続きの起動は同じ席で `--replace --seat <席> --seated` を打つ | 3 本が動く間に上限を 2 へ下げて、中止・回答の後の next・`/goal` の入れ直しのそれぞれを打つと待たずに通り、中止した計画は中止の処理を終える | ほかの断り（バージョンの混在など）は今のまま失敗 |
| `lock_free` | 誰も持たなければ真 | 別のスレッドが持つ間は偽 | — |
| 判断との組み（`test_run_lanes.py`） | 上限 1・稼働中 1・待ち 2 で、稼働中が終わった後の周に並びの先の 1 本だけ起動する | 上限を上げた後の周に待ちのレーンが起動する | 待ちを失敗に数えない・`launch_failed` を書かない |

## 計画とレーンの値を消す（types・fold・rules・plan・cli）

満たす決定: 決定 1・2、受け入れ条件 1・2・3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_spec`・`_changed_spec` | `src/ndf/progress/lane/fold.py` | 変更 | 変えない | レーンの追加・変更の事象の `plans` を読まない。`plans` だけの変更の事象はレーンを変えない |
| `_plan_base`・`_plan_limits` | 同上 | 変更 | 変えない | 計画の作成・上限の変更の事象の `max_lanes`・`max_plans` を読まない。上限の変更の事象は計画の状態を変えない（`_plan_limits` は事象を読み捨てる関数になる。消すかは実装で決め、消すなら計画の行の `lane_changed` を読み捨てる分岐を残す） |
| `plan_errors` | `src/ndf/progress/lane/rules.py` | 変更 | 変えない | `_limit_errors` を外す（I2・I20 が無くなる） |
| レーンの行を組む関数・`plan_payload` | `src/ndf/progress/plan/__init__.py` | 変更 | 変えない | レーンの行に `plans` を、計画の行に `max_lanes`・`max_plans` を書かない |
| `_cmd_set` | `src/ndf/progress/plan/cli.py` | 変更 | 変えない | `--touches`・`--after` だけを受ける |

`_cmd_limits` と `ndf lanes limits` の引数は消す。計画の行への `lane_changed` を書く経路は無くなる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_doc`（`ndf lanes init`・`start`・`lane add`） | 計画の設定に `max_lanes`・`max_plans` か、レーンに `plans` がある | 11.0.13 までの形で書いた | 形の検査（`additionalProperties: false`） | 止める（今の `PlanInputError`） | 原因 `invalid`（終了コード 2）。文にキーの名前が出る（実装で文を確かめ、出なければ誤りの文を組む側で名指す） | ファイルからキーを消して打ち直せる |
| `ndf lanes limits`・`ndf lanes set --plans` | 消したサブコマンドと引数を打った | 古い手順 | `argparse` | 止める | 引数の誤り（終了コード 2） | `ndf relay cap` で打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_spec`・`_changed_spec`・`_plan_base`・`_plan_limits` | 3 つの値を持つ記録（作成・追加・変更・上限の変更の 4 つの事象）を畳み込んで計画の状態を読める | 値だけの事象の後も計画とレーンの状態が変わらない | — |
| `plan_errors` | `after` で直列の未着手 3 本で、計画の作成が通る | レーン 1 本の計画も通る | — |

## 待ちの表示（plan.status）

満たす決定: 決定 5、受け入れ条件 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `machine_waits` | `src/ndf/progress/plan/__init__.py` | 変更 | `machine_waits(view, rows, listed: Any, free: bool) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]` | 起動できるレーンごとの待ち（理由と数）と、マシンの集計（`lanes`・`cap`・`unreadable`・`others`・`error`）を返す。ほかの計画は起動できるレーンがあるときだけ `machine_plans` で 1 回読み、集計とレーンごとの判定がその結果を共有する |
| `_plan_metrics` | 同上 | 変更 | 変えない | `max_lanes`・`max_plans` を外し、`machine`（`machine_waits` の 2 つ目）を足す |
| `status` | 同上 | 変更 | 変えない | `probes` があれば `machine.lock_free()` を 1 回呼んで `machine_waits` へ渡す。ラッパーの一覧は今のまま `probes.wrappers()` を 1 回呼んで渡し、集計とレーンごとの判定のために `ndf relay ls` を打ち足さない |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `machine_waits` | 上限を読めない（`NamedCapError`） | 待ちを載せない。集計の `cap` は null、`error` に文、`lanes` は下の規則で数える。`status` は止めず、終了コードは今のまま |
| `machine_waits` | `free` が偽 | 起動できるレーンのすべてに `{"reason": "lock", "machine_lanes": null, "cap": null, "unreadable": []}` の待ち（耐久ワークフローの `waiting` と同じ形）。集計は一覧を読めれば数える |
| `machine_waits` | 起動できるレーンが無い | 待ちは空。ほかの計画のステートマシンを読まない（`machine_plans` を呼ばず、`read_lanes` の Git の所在の確かめも打たない）。集計の `lanes` は自分の計画と一覧の動いているラッパーで数え、`others` は偽 |
| `machine_waits` | 一覧を読めない（`listed` が None） | 自分の計画の実行中の行を持つレーンだけで数え、収まらないレーンに `reason` が `observe` の待ち。集計の `lanes` はその数 |
| `machine_waits` | 一覧を読めた・収まらない | `reason` が `machine-lanes` の待ち |
| `machine_waits` | 収まる | 待ちを載せない |

- 集計の `lanes` は、`skip` を空にした `count` の合計（今動いている分）である。レーンごとの判定は、そのレーンのラッパーを `skip` にして数える（起動の許可と同じ）
- 計算量の注意: ほかの計画の読み込み（`machine_plans`。計画ごとにステートマシンを開き、所在の確かめで Git を打つ）は、起動できるレーンがあるときだけ 1 回の `status` で 1 計画 1 回（レーンの数に依らない）。集計はその結果を使い回し、別に読まない。起動できるレーンが無い `status` では 0 回で、今の `machine_waits` の回数を超えない

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `machine_waits` | 上限で待つレーンに `machine-lanes`、`metrics.machine` に稼働中の数と上限 | 起動できるレーンが無ければ待ちは空で、集計は出て、`read_lanes`（を差し替えた数え手）の呼び出しが 0 回。起動できるレーンが 3 本・ほかの計画が 2 つなら呼び出しが 2 回 | 一覧を読めないと `observe`、排他が取られていると `lock`（`machine_lanes`・`cap` が null）、壊れた `cap.json` で `error` が載り例外が出ない |

## リレーハブと `ndf relay cap`（hub・relay cli）

満たす決定: 決定 3・4・7、受け入れ条件 7・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Context` | `src/ndf/domains/relay/hub.py` | 変更 | フィールド `max_named: int` を消し、`fallback_cap: Optional[Union[Cap, NamedCapError]] = None` を足す | 上限の値を文脈に持ち越さない。`fallback_cap` は要求ごとに `requester` が載せる |
| `submit` | 同上 | 変更 | 変えない | 置き換えの要求（種別 `replace`）を、その種別を知らない旧版のハブが「知らないリクエスト: replace」（原因 `invalid`）で断ったら、その回答を捨てて同じ中身を新しい要求 ID で書き直し（待つ結果のパスと期限切れで消す要求のパスも新しい ID へ切り替える。旧版のハブは結果を書いてから要求を消すので、同じパスへ書き直すとその削除が再送分を消す）、対応版のハブ（自分の `drain` を含む）が処理するまで待つ（旧版が通常の起動として席を書かずに処理しない）。`spawn`・`replace`（`seated` でないもの）・`restart` の要求に、頼む側が解いた `cap`（`{"value", "source"}`。`named_cap.fallback(ctx.environ)`。マシンの設定を読まず、`source` は `env` か `limits`）を載せる。マシンの設定の値は載せないので、要求を書いた後に `--clear` しても、ハブは消した値で判定しない。解けなければ（`NamedCapError`）止めずに `{"error": 文}` を載せて要求を書く。`seated` の要求は上限を使わないので `cap` を解かない |
| `requester` | 同上 | 変更 | `requester(ctx: Context, req: Mapping[str, Any]) -> Context` | 導入とバージョンに加え、要求の `cap` を文脈のフォールバックの上限（`Context.fallback_cap`）に載せる。`{"value", "source"}` なら `Cap`、`{"error"}` ならその文の `NamedCapError`、無い・形が違えば None |
| `_max_named` | 同上 | 新設 | `_max_named(ctx: Context) -> int` | `named_cap.read(ctx.environ, ctx.state_dir, ctx.fallback_cap).value`。マシンの設定があればその値（要求の解析の誤りは無視する）、無ければ要求の値か、要求の解析の誤り（`NamedCapError`）。要求に `cap` が無いときだけハブ自身の環境と `limits.json` を読む |
| `handle` | 同上 | 変更 | 変えない | 種別 `replace` を `spawn` へ回す（置き換えの起動）。`except` に `named_cap.NamedCapError` を足し、`Outcome(False, 文, "invalid")` にする |
| `spawn`・`restart` | 同上 | 変更 | 変えない | 本数を判定するときだけ `_max_named` を読んで `hub_decide.Lanes` を組む（`seated` の続きの起動は読まない）。`restart` は今のまま `hub_decide.restart`（本数と日次の再起動の回数）。`spawn` の判定は下の表 |
| `_seated_ok` | 同上 | 新設 | `_seated_ok(entries: Mapping[str, Mapping[str, Any]], name: str, seat: str, req: Mapping[str, Any]) -> Optional[Outcome]` | 続きの起動の席を確かめる。登録簿に `name` の行があり、その `seat` が要求の `seat` と同じなら None、違えば `Outcome(False, 文, "refused")`。席を書く前のバージョンが書いた行（`seat` のキーが無い）は、役（`role`）と計画（`plan`）が要求と同じなら None にし、起動の後に席を書く（更新の前から実行中の行の続きを断らない。頼む側は実行中の行を読んでから続きの起動を打つ）。今のバージョンは席を持たない起動でも `seat: null` を書くので、キーの無い行は旧いバージョンの行だけである。ラッパーが動いているかは見ない |
| `_preflight` | 同上 | 新設 | `_preflight(ctx: Context, entry: Mapping[str, Any]) -> Optional[Outcome]` | 止める前に確かめられる起動の条件を確かめる。導入の入口（`identity.entrypoint(ctx.plugin_root)`）がファイルで、作業ディレクトリ（`entry["cwd"]`、無ければ `ctx.state_dir`）がディレクトリなら None、違えば `Outcome(False, 文, "precondition")`。`_launch` も先頭でこれを呼ぶ（入口の確かめを 1 か所にする） |
| `replace` | `src/ndf/domains/relay/hub_decide.py` | 新設 | `replace(lanes: Lanes) -> Decision` | 行を始める置き換えの起動の判定。対象を除いた本数 + 1 が `max_named` を超えれば `REFUSE`（対象は止めない）。`starts_today`・`max_starts` を見ない。`seated` の続きの起動はハブが上限を読む前に分けるので、この関数を呼ばない |
| `listing` | 同上 | 変更 | 変えない | 登録簿の行に加え、登録簿に無く錠を持つラッパー（`registry.busy_names`。状態のファイルを読まない）を `{"name", "running": true, "state", "registered": false}` の行で載せる。登録簿の行は `registered: true`。ラッパーの状態のファイルは行ごとに 1 回だけ読む |
| `hub_context` | `src/ndf/domains/relay/cli.py` | 変更 | 変えない | `max_named` を読まない |
| `_hub` | 同上 | 変更 | 変えない | `--replace` なら要求の種別を `replace` にし（旧版のハブが通常の起動として処理できない種別）、`seat`（`--seat <席>`）・`seated`（`--seated`）・`held`（`--held <名前>` の並び）を載せる。`--held` は `--replace` を要する。`--seat` は `--replace` を、`--seated` は `--replace`・`--seat`・`--name` をすべて要し、欠ければ引数の誤り（名前の採番で新しい名前の続きの起動を作らせない） |
| `main` | 同上 | 変更 | 変えない | `except` の組に `named_cap.NamedCapError` を足し、原因 `invalid` の結果 JSON にする |
| `_cap` | 同上 | 新設 | `_cap(args: argparse.Namespace) -> int` | `ndf relay cap [--named <数> \| --clear]`。書く・消す・引数なしなら今の値と出所を結果 JSON に出す |

- `_cap`
  - 事後条件: 結果の `metrics` に `value`（今の上限）と `source`（`machine`・`env`・`limits`）。書いた後は書いた値と `machine`
  - 副作用: マシンの設定のファイルを書く・消す
- `spawn` の判定の順
  1. バージョンの混在の判定 `hub_decide.mixed`。`replace` が偽なら今のまま動いているラッパーのすべてで、真なら `restart` と同じく対象（同じ名前）を除いた動いているラッパーで判定する。対象だけが旧版なら通り、別名の旧版が動いていれば対象を止める前に `precondition` で断る
  2. `replace` が偽で同じ名前が動いていれば断る（今のまま）
  3. `seated` なら `_seated_ok`。`seated` でなければ `_max_named` を読んで本数を判定する（`seated` は上限を読まないので、壊れた `cap.json` でも続きの起動が通る）。置き換えの本数は、この NDF の動いているラッパーの名前と要求の `held` の名前の和集合から対象を除いて数える（起動の許可が数えた、実行中の行を持つレーンをラッパーの生死にかかわらずハブが数える。許可の後・ハブの判定の前にラッパーだけが止まり、許可を通らない起動が空きに入っても、置き換えは対象を止めずに断り、待ちに戻る）
  4. `replace` なら `_preflight`（対象を止める前）
  5. `replace` で対象が動いていれば `_await_exit` で止め、`_launch` で起動する

  | 要求 | 同じ名前のラッパー | 本数の判定 | 日次の再起動の回数 | 通ったとき | 登録簿の `starts` |
  | --- | --- | --- | --- | --- | --- |
  | `replace` が偽 | 動いている | — | 見ない | 断る（今のまま「既に動いている」） | — |
  | `replace` が偽 | 動いていない | `hub_decide.spawn`（動いている本数 + 1 ≤ 上限） | 見ない | 起動する | その日の 0 回で書く（今のまま） |
  | `replace` が真・`seated` が偽（行を始める起動） | 動いている・動いていない | `hub_decide.replace`（対象を除いた本数 + 1 ≤ 上限） | 見ない | `_preflight` が通れば、対象が動いていれば `_await_exit` で止めてから起動する | その日の 0 回で書き直し、`seat` に要求の席を書く（今の `stop` → `spawn` と同じ。元の行の回数を引き継がず、増やさない） |
  | `replace` が真・`seated` が真（続きの起動） | 動いている・動いていない（止まっていても登録簿に席があれば通る） | しない（上限を読まない）。代わりに `_seated_ok`（登録簿の行の `seat` が要求と同じ） | 見ない | 同上 | 同上（`seat` は同じ値） |

  - 席: 行を始める起動が上限の判定を通ったときだけ、登録簿のその名前の行に `seat`（`<レーンのステートマシンの ID>:<行のキー>`）を書く（席を持たない起動は `seat: null`）。`seated` の続きの起動は、名前を明示し、登録簿の同じ名前の行の `seat` と同じ席を渡したときだけ本数を免除される。名前ごとに席は 1 つなので、続きの起動は席を持つ名前を置き換えるだけで、新しい名前のラッパーを増やせない。`stop` は席を消さない（中止のために止まった対象も正規の続きで起動できる）。席は次に同じ名前で行を始める起動が書き直す
  - 動く本数が上限を超えるのは、上限を下げた直後か、許可を通らない起動が席を使った後だけで、その後の行を始める起動と通常の `spawn` は断られる
- `spawn`・`restart` の上限の断り
  - 結果の `cause` は今のまま `refused`、`items` に `{"refusal": "max-named", "running": <対象を除いて数えた本数>, "cap": <上限>}` を載せる。バージョンの混在・回数の上限の断りには載せない。耐久ワークフローの `launch.read_spawn` はこの項目だけを待ちとして読む
  - 上限で断るとき、対象のラッパーは止めていない（判定は `_await_exit` の前）
- `listing`
  - 事後条件: `running: true` の行の名前の集まりが、`registry.running(state_dir)` の名前の集まりを含む
  - 呼び手への影響: 耐久ワークフローの生死の判定（`liveness`）と計画の発見（`machine.discover`）は、`role`・`plan`・`cwd` の無い行を conductor の行と読まない（今の `_conductor` の条件のまま）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_cap` | `--named` と `--clear` を両方渡した・`--named` が 1 未満 | 引数の誤り | `argparse` の排他のグループと `_cap` | 止める | 原因 `invalid`（終了コード 2） | 書かない。打ち直せる |
| `spawn`・`restart`（ハブ） | マシンの設定が壊れている | 手で書き換えた | `_max_named` → `named_cap.read` | `handle` が `NamedCapError` を捕まえて `Outcome(False, 文, "invalid")` にする（起動しない・対象を止めない）。`_handle_file` の `except` の組は `ContractError` を捕まえないので、`handle` で捕まえる | その要求の頼む側に原因 `invalid` と文にファイル。`drain` はその要求に結果を書いてファイルを消し、後ろの要求（`stop` を含む）を続けて処理する | `ndf relay cap` で直して打ち直せる |
| `spawn`・`restart`（頼む側の CLI） | 頼む側の環境変数・上限の設定が壊れている | 環境変数の誤り | `submit` → `named_cap.fallback` | `submit` が捕まえ、要求の `cap` に `{"error": 文}` を載せて書く。ハブはマシンの設定があればその値で判定し、無ければ `invalid` | マシンの設定があれば起動の結果のまま。無ければ原因 `invalid` と文に環境変数の名前 | 環境変数を直すか `ndf relay cap --named` で打ち直せる |
| `cap`（頼む側の CLI） | マシンの設定・環境変数が壊れている | 同上 | `_cap` → `named_cap.read` | `relay.cli.main` が捕まえる | 原因 `invalid`（終了コード 2）の結果 JSON | 同上 |
| `spawn`（ハブ、`seated`） | 登録簿に名前の行が無い・`seat` が違う（席を書く前のバージョンの行で、役か計画が違うときを含む） | 席の無い新規起動・古い手順 | `_seated_ok` | 断る（起動しない・対象を止めない） | 原因 `refused`、文に名前と席 | `--seated` を外し、上限の判定を受けて打ち直せる |
| `spawn`（ハブ、`replace`） | 導入の入口が無い・作業ディレクトリが無い | 導入の削除・誤った `--cwd` | `_preflight` | 断る（対象を止めない） | 原因 `precondition`、文に入口か作業ディレクトリのパス | 正しい `--cwd` で打ち直せる。元のセッションは動いたまま |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_cap` | 書いた値を引数なしの `cap` と、その後の `spawn` の判定が使う | `--clear` の後は環境変数の値に戻る | 両方の引数で断る |
| `spawn` | 上限を下げた後は新しい起動を断り、動いているラッパーは止めない。`cap --named 6` の後に頼む側の環境が `NDF_RELAY_MAX_NAMED=1.5` で `spawn` を打つと、要求が書かれ、6 で判定して起動する（マシンの設定が無ければ同じ要求が `invalid`）。行を始める起動が登録簿に席を書き、対象のラッパーを止めた後でも同じ名前・同じ席の `seated` が通る。マシンの設定が無く、要求の `cap` が 8・ハブの文脈の環境に変数が無い（既定 6）とき、6 本が動く間の要求を通す。マシンの設定が 6・頼む側の環境が 2 で要求を書き、`drain` の前に `cap --clear` すると、3 本が動く間の要求を 2 で断る | 上限ちょうどの本数で断り、`items` に `refusal: "max-named"`。`replace` で 3 本が動き上限 2 なら、対象を止めずに断る（`_await_exit` も `stop` も打たない）。上限 3 なら対象を止めてから起動する。`replace` と `seated` で 3 本が動き上限 2 なら、対象を止めてから起動し、3 本のまま。日次の再起動の回数を使い切った登録簿の行でも `replace` は通り、`starts` はその日の 0 回。メジャーの違う旧版で動くのが対象だけなら `replace` は通り、別名の旧版が動いていれば対象を止めずに `precondition` で断る | 壊れた `cap.json` で `replace`（`seated` でない）と通常の `spawn` は `invalid` の結果になり、例外が出ない。壊れた `cap.json` でも `seated` の続きの起動は通り、中止の処理を終える（`test_run_lanes.py` でも中止の手順で確かめる）。登録簿に行の無い名前・席の違う `seated` は `refused` で起動せず、本数が増えない。存在しない `--cwd` の `replace` は `precondition` で、`tmux.kill` を打たず元のセッションが動いたまま。`--seated` で `--replace`・`--seat`・`--name` のどれかが無ければ引数の誤り |
| `drain` | — | — | 壊れた `cap.json` の `spawn` の要求の後ろに `stop` の要求を置くと、1 回の `drain` で両方に結果が書かれ、要求のファイルが残らない |
| `main` | — | — | 壊れた `cap.json` で引数なしの `ndf relay cap` と `ndf relay spawn` が原因 `invalid`（終了コード 2）の結果 JSON を出す |
| `listing` | 登録簿に無く錠を持つラッパーが `registered: false` の行で載る | 登録簿の行は今の項目のまま | — |
