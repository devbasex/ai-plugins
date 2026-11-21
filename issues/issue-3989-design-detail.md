# #3989 の詳細設計: 同時実行の制御を稼働中のレーンの数とテストのスロットの 2 層にする

設計文書 [issue-3989-design.md](./issue-3989-design.md) の決定 1〜6 を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く（`docs/ndf-parallel-model.md` だけはリポジトリの根から）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/
│   ├── core/named_cap.py *          上限の値の読み書き（マシンの設定 → 環境変数 → limits.json）
│   ├── progress/lane/
│   │   ├── types.py                 PlanView の max_lanes・max_plans、LaneSpec の plans を外す
│   │   ├── fold.py                  記録の 3 つの値を読まない
│   │   ├── rules.py                 I20・I2 を消す
│   │   ├── machine.py               稼働中のレーンとほかのラッパーを 1 本ずつ数える・上限を named_cap で読む・排他の確かめ
│   │   └── __init__.py              説明から I20 を外す
│   ├── progress/plan/
│   │   ├── __init__.py              計画の行とレーンの行から 3 つの値を外す・待ちの理由と metrics.machine
│   │   ├── cli.py                   ndf lanes limits と ndf lanes set --plans を消す
│   │   └── run.py                   起動の許可へ plans を渡さない
│   └── domains/relay/
│       ├── cli.py                   ndf relay cap・hub_context の上限
│       └── hub.py                   判定のたびに上限を読む・一覧に錠だけのラッパーを載せる
├── contracts/
│   ├── workflow/parallel-plan.schema.json
│   └── schemas/io/{lanes,relay}.{args,result}.schema.json
├── docs/contracts/{commands,env}.md
├── skills/…/parallel-plan.md・parallel.md・parallel-checks.md・parallel-cross-repo.md・parallel-work.md
└── tests/
    ├── core/test_named_cap.py *
    ├── progress/test_lane.py・test_plan.py・test_lane_machine.py・test_run_lanes.py
    ├── support/plan_decls.py
    └── domains/relay/test_relay_cli.py
docs/ndf-parallel-model.md
```

依存の向き: `core/named_cap.py` は `ndf.contracts.limits` だけに依存し、`progress/lane/machine.py`・`progress/plan/__init__.py`（`machine` 経由）・`domains/relay/hub.py`・`domains/relay/cli.py` がそれを呼ぶ。`progress` から `domains` へは依存しない（ラッパーの一覧は今のまま `ndf relay ls` の結果で読む）。状態の保存先のパスは呼ぶ側が渡し、`named_cap` は `identity` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/named_cap.py` | 新設 | 同時に動かす数の上限を 1 つの順で読み、マシンの設定を書く | `ndf.contracts.limits` | モジュール・`Cap` | `path`・`read`・`write`（新設） |
| `src/ndf/progress/lane/types.py` | 変更 | 計画の状態の型 | — | `PlanView`・`LaneSpec` | `PlanView.max_lanes`・`max_plans`、`LaneSpec.plans` を消す |
| `src/ndf/progress/lane/fold.py` | 変更 | 計画の事象の畳み込み | `types` | モジュール | `_spec`・`_changed_spec`・`_plan_base`・`_plan_limits`（変更） |
| `src/ndf/progress/lane/rules.py` | 変更 | 計画の決まり | `types` | モジュール | `_limit_errors` を消し、`plan_errors` から外す。説明の I2・I20 を外す |
| `src/ndf/progress/lane/machine.py` | 変更 | マシン全体の稼働中の数と起動の許可 | `named_cap`・`launch`・`liveness` | `PlanLanes`・`Count`・モジュール | `PlanLanes`・`lanes_of`・`count`・`admit`・`waiting`・`cap`・`admitted`（変更）・`lock_free`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | レーンのステートマシンの読み書き | — | モジュール | 説明の I20 を外す |
| `src/ndf/progress/plan/__init__.py` | 変更 | 計画のステートマシンの関数と状態の表示 | `machine` | モジュール | レーンの行を組む関数（75 行）・`plan_payload`・`machine_waits`・`_plan_metrics`・`status`（変更）。`LIMIT_POLICY`・`_default_limit` を消す |
| `src/ndf/progress/plan/cli.py` | 変更 | `ndf lanes` のサブコマンド | `plan` | モジュール | `_cmd_set`（変更）・`_cmd_limits` と `limits` の引数を消す |
| `src/ndf/progress/plan/run.py` | 変更 | 耐久ワークフローの手 | `machine` | モジュール | `launch_lane`（変更） |
| `src/ndf/domains/relay/hub.py` | 変更 | リレーハブの判定と一覧 | `named_cap`・`registry` | `Context` | `Context`（`max_named` を消す）・`spawn`・`restart`・`listing`（変更）・`_max_named`（新設） |
| `src/ndf/domains/relay/cli.py` | 変更 | `ndf relay` のサブコマンド | `named_cap`・`hub` | モジュール | `hub_context`（変更）・`_cap`（新設）・`cap` の引数 |
| `contracts/workflow/parallel-plan.schema.json` | 変更 | 計画の設定の形 | — | — | `max_lanes`・`max_plans`・レーンの `plans`（`required` からも）を消す |
| `contracts/schemas/io/lanes.args.schema.json`・`lanes.result.schema.json` | 変更 | `ndf lanes` の引数と結果の形 | — | — | `limits` のサブコマンド・`set --plans`・`max_lanes`・`max_plans` を消し、`machine` と `machine_wait` の新しい項目を足す |
| `contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` | 変更 | `ndf relay` の引数と結果の形 | — | — | `cap` の引数（`--named`・`--clear`）と結果（`value`・`source`）、`ls` の行の `registered` を足す |
| `docs/contracts/commands.md`・`env.md` | 変更 | コマンドと環境変数の一覧 | — | — | `ndf lanes limits` の行を消し、`ndf lanes set`・`ndf relay cap` の行と `NDF_RELAY_MAX_NAMED` の読む順を直す |
| `skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | 計画の書き方 | — | — | 例とレーンの項目の表から `plans` を、計画の項目から 2 つの上限を、規則の表から I2・I20 を消し、同時実行は 2 つの層で絞ると書く |
| `skills/development-workflow/references/parallel.md` | 変更 | オーガナイザーの手順 | — | — | `ndf lanes set` の `--plans` を消し、「上限を変える」の行を `ndf relay cap` へ |
| `skills/development-workflow/references/parallel-checks.md` | 変更 | マシンの上限の説明 | — | — | 2 つの層・数える単位（レーン 1 本）・読む順・ハブとの一致・待ちの理由 3 つ・`metrics.machine` |
| `skills/development-workflow/references/parallel-cross-repo.md` | 変更 | 別のリポジトリのレーンの手順 | — | — | 例の `plans`、「上限を超える」と「I20 で断られた」の行を消す |
| `skills/development-workflow/references/parallel-work.md` | 変更 | 並行の決まり | — | — | 並行の本数の行の `ndf lanes limits` を 2 つの層へ |
| `docs/ndf-parallel-model.md` | 変更 | 並行開発の役割と用語 | — | — | 141 行の `plans` と上限の説明を 2 つの層へ |
| `tests/core/test_named_cap.py` | 新設 | 読む順と書き方 | `named_cap` | — | — |
| `tests/progress/test_lane.py`・`test_plan.py`・`test_lane_machine.py`・`test_run_lanes.py`・`tests/support/plan_decls.py` | 変更 | I20・I2・`plans` のテストを消し、受け入れ条件 1〜10 のテストを足す | — | — | — |
| `tests/domains/relay/test_relay_cli.py` | 変更 | `ndf relay cap`・ハブの判定が書いた値を読むこと・一覧の錠だけのラッパー | — | — | — |

## 上限の値（named_cap）

満たす決定: 決定 4、受け入れ条件 7・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `path` | `src/ndf/core/named_cap.py` | 新設 | `path(state_dir: Path) -> Path` | マシンの設定のパス（`<state_dir>/relay/hub/cap.json`。ハブの保存先 `hub.hub_dir` と同じ親） |
| `read` | 同上 | 新設 | `read(environ: Mapping[str, str], state_dir: Path) -> Cap` | マシンの設定 → `NDF_RELAY_MAX_NAMED` → `limits.json` の `relay.max-named` の順で、最初にある値と出所を返す |
| `write` | 同上 | 新設 | `write(state_dir: Path, value: Optional[int]) -> None` | 値を書く。`None` ならファイルを消す |

- `read`
  - 事後条件: `Cap.value` は 1 以上の整数で、`Cap.source` は `machine`・`env`・`limits` のどれか。呼ぶたびにファイルを読み直し、値を持ち越さない
  - 副作用: ファイルを読むだけ
- `write`
  - 事後条件: 値を書いたら `{"version": 1, "max_named": <値>}` のファイルがある。置き換えは一時ファイルからの改名で行い、読む側は半端な中身を読まない
  - 副作用: ファイルを書く・消す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read` | マシンの設定が JSON でない・`version` が 1 でない・`max_named` が 1 以上の整数でない | 手で書き換えた | `read` | 止める（`NamedCapError`、`ContractError` の子） | 呼んだサブコマンドが原因 `invalid` で止まる（終了コード 2）。文にファイルのパスと誤り | `ndf relay cap --named`・`--clear` で書き直してから打ち直せる |
| `read` | `NDF_RELAY_MAX_NAMED` が数でない・0 以下 | 環境変数の誤り | `limits.from_env` | 今のまま `EnvError` | 今のまま | 今のまま |
| `write` | 値が 1 未満 | 引数の誤り | `write` の呼び手（`_cap`） | 止める | 原因 `invalid`（終了コード 2） | 書かない。打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Cap` | 型（`NamedTuple`: `value: int`・`source: str`） | `src/ndf/core/named_cap.py` | — |
| `FILE` | 定数 | 同上 | `relay/hub/cap.json`（状態の保存先からの相対） |
| `ENV` | 定数 | 同上 | `NDF_RELAY_MAX_NAMED` |
| `POLICY` | 定数 | 同上 | `("relay.max-named", "count")`。`machine.CAP_POLICY` はこれを指す（同じ事実を 2 か所に持たない）。`plan.LIMIT_POLICY` は消す |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `ndf relay cap` と、起動の許可・ハブ・`status` の読み | マシンの設定のファイル | 取らない。書く側は改名で置き換え、読む側は書く前か後のどちらかの中身を丸ごと読む | 同じ値を何度書いても同じ。読む側は次の判定で読み直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `read` | `limits.from_env`（環境変数 → `limits.json`） | `git grep -n "def from_env" plugins/ndf/src` | 使う。マシンの設定が無いときに呼ぶ |
| `write` | `relay.signals.replace_json`（一時ファイルからの改名） | `git grep -n "def replace_json" plugins/ndf/src` | 使えない。`domains` にあり、`core` から呼ぶと依存が逆向きになる。同じ形（一時ファイル + `os.replace`）を `core` に書く。共有の場所へ移すかは実装の `refactoring` で見る |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `read` | ファイルがあれば環境変数より先に読み、`source` が `machine`。無ければ環境変数、それも無ければ `limits.json` | 書いた直後の同じプロセスの 2 回目の `read` が新しい値を返す | 壊れたファイルで `NamedCapError` |
| `write` | 書いた値を `read` が返す。`None` の後は環境変数に戻る | 1 を書ける | 0 を渡すと断る（呼び手） |

## 起動の許可（machine・run）

満たす決定: 決定 3・4・6、受け入れ条件 5・6・7・8・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `PlanLanes` | `src/ndf/progress/lane/machine.py` | 変更 | `lanes: tuple[tuple[str, bool], ...]`（ラッパーの名前, 実行中の行を持つか） | 重み（`plans`）を持たない |
| `lanes_of` | 同上 | 変更 | 変えない | `(ラッパーの名前, 実行中の行を持つか)` を組む |
| `count` | 同上 | 変更 | 変えない（`count(plans, listed, skip) -> Count`） | 稼働中のレーンのラッパーの名前と、一覧で動いているラッパーの名前（役に依らない）の和集合から `skip` を除いた数を数える |
| `admit` | 同上 | 変更 | `admit(total: int, cap: int) -> bool` | `total + 1 <= cap` |
| `waiting` | 同上 | 変更 | `waiting(reason: str, got: Optional[Count], cap: int) -> dict[str, Any]` | 待ちの結果（`reason`・`machine_lanes`・`cap`・`unreadable`） |
| `cap` | 同上 | 変更 | `cap(environ: Optional[Mapping[str, str]] = None, state_dir: Optional[Path] = None) -> int` | `named_cap.read` の値。`state_dir` を省けば `identity.load().state_dir` |
| `admitted` | 同上 | 変更 | `admitted(own: PlanLanes, lane_wrapper: str, listed: Callable[[], Optional[Mapping[str, Mapping[str, Any]]]], go: Callable[[], dict[str, Any]]) -> dict[str, Any]` | 排他の中で `cap` を読み、数えて、収まれば `go` |
| `lock_free` | 同上 | 新設 | `lock_free() -> bool` | マシンの起動の排他を待たずに 1 回だけ取り、取れたらすぐ放して真を返す |
| `launch_lane` | `src/ndf/progress/plan/run.py` | 変更 | 変えない | `admitted` へ `plans` を渡さない |

- `count`
  - 事後条件: 数えるのは次の 3 つで、同じ名前は 1 回、`skip` は数えない。(1) 読めた計画の実行中の行を持つレーンのラッパー。(2) 一覧で `running` のラッパー（計画のレーンか否か・役に依らない）。(3) 読めない計画の動いているラッパー（`unreadable` に計画を載せる。今のまま）
  - ハブとの関係: (2) は `ndf relay ls` が載せる錠を持つラッパーのすべてで、ハブの `_own_count` が数えるラッパーを含む
- `lock_free`
  - 事後条件: 排他を持ったまま返らない
  - 副作用: 排他のファイルを開く（無ければ作る。`durable.open_lock_file` と同じ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `admitted` | マシンの設定が壊れている | 手で書き換えた | `cap` → `named_cap.read` | 待ちにせず起動の手の失敗 `final` で返す（打ち直しても同じ失敗） | `launch_failed` の知らせの詳細にファイルと誤り | `ndf relay cap` で直し、リクエストを閉じれば起動の候補へ戻る |
| `admitted` | 排他を取れない | 別の計画が数えている | `admit_lock` | 待ち（`lock`。今のまま） | 無し（`status` の待ち） | 次の周で打ち直す |
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
| 同じ計画の同じ周の 2 つの `launch_lane` | 同上 | 判断が 1 手ずつ打つので重ならない。2 手目は 1 手目のラッパーを数える | 同上 |
| `ndf lanes status` の `lock_free` と `launch_lane` | `admit.lock` | `status` は取れるかを 1 回試してすぐ放す。待たない | `status` は何も書かない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `count` | オーガナイザーの役の動いているラッパーと、登録簿に無く錠だけのラッパーを 1 本ずつ数える。`plans` に依らず 1 | 起動するレーン自身のラッパーは数えない。実行中の行を持つのにラッパーが止まったレーンは数える | — |
| `admitted` | 別の計画 2 つ（ステートマシンの ID が違う）の同時の判定で、起動の後の稼働中の数が上限以内 | 稼働中の数 = 上限 - 1 なら起動し、= 上限なら待つ | マシンの設定が壊れていると `final` の失敗 |
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
| `machine_waits` | `src/ndf/progress/plan/__init__.py` | 変更 | `machine_waits(view, rows, listed: Any, free: bool) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]` | 起動できるレーンごとの待ち（理由と数）と、マシンの集計（`lanes`・`cap`・`unreadable`）を返す |
| `_plan_metrics` | 同上 | 変更 | 変えない | `max_lanes`・`max_plans` を外し、`machine`（`machine_waits` の 2 つ目）を足す |
| `status` | 同上 | 変更 | 変えない | `probes` があれば `machine.lock_free()` を 1 回呼んで `machine_waits` へ渡す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `machine_waits` | `free` が偽 | 起動できるレーンのすべてに `{"reason": "lock"}` の待ち。集計は一覧を読めれば数える |
| `machine_waits` | 一覧を読めない（`listed` が None） | 自分の計画の実行中の行を持つレーンだけで数え、収まらないレーンに `reason` が `observe` の待ち。集計の `lanes` はその数 |
| `machine_waits` | 一覧を読めた・収まらない | `reason` が `machine-lanes` の待ち |
| `machine_waits` | 収まる | 待ちを載せない |

- 集計の `lanes` は、`skip` を空にした `count` の合計（今動いている分）である。レーンごとの判定は、そのレーンのラッパーを `skip` にして数える（起動の許可と同じ）
- 計算量の注意: ほかの計画の読み込み（`machine_plans`）はレーンの数に依らず 1 回だけ（今のまま）

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `machine_waits` | 上限で待つレーンに `machine-lanes`、`metrics.machine` に稼働中の数と上限 | 起動できるレーンが無ければ待ちは空で、集計は出る | 一覧を読めないと `observe`、排他が取られていると `lock` |

## リレーハブと `ndf relay cap`（hub・relay cli）

満たす決定: 決定 3・4、受け入れ条件 7・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Context` | `src/ndf/domains/relay/hub.py` | 変更 | フィールド `max_named: int` を消す | 上限の値を文脈に持ち越さない |
| `_max_named` | 同上 | 新設 | `_max_named(ctx: Context) -> int` | `named_cap.read(ctx.environ, ctx.state_dir).value` |
| `spawn`・`restart` | 同上 | 変更 | 変えない | 判定のたびに `_max_named` を読んで `hub_decide.Lanes` を組む |
| `listing` | 同上 | 変更 | 変えない | 登録簿の行に加え、登録簿に無く錠を持つラッパー（`registry.running` の名前）を `{"name", "running": true, "state", "registered": false}` の行で載せる。登録簿の行は `registered: true` |
| `hub_context` | `src/ndf/domains/relay/cli.py` | 変更 | 変えない | `max_named` を読まない |
| `_cap` | 同上 | 新設 | `_cap(args: argparse.Namespace) -> int` | `ndf relay cap [--named <数> \| --clear]`。書く・消す・引数なしなら今の値と出所を結果 JSON に出す |

- `_cap`
  - 事後条件: 結果の `metrics` に `value`（今の上限）と `source`（`machine`・`env`・`limits`）。書いた後は書いた値と `machine`
  - 副作用: マシンの設定のファイルを書く・消す
- `listing`
  - 事後条件: `running: true` の行の名前の集まりが、`registry.running(state_dir)` の名前の集まりを含む
  - 呼び手への影響: 耐久ワークフローの生死の判定（`liveness`）と計画の発見（`machine.discover`）は、`role`・`plan`・`cwd` の無い行を conductor の行と読まない（今の `_conductor` の条件のまま）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_cap` | `--named` と `--clear` を両方渡した・`--named` が 1 未満 | 引数の誤り | `argparse` の排他のグループと `_cap` | 止める | 原因 `invalid`（終了コード 2） | 書かない。打ち直せる |
| `spawn`・`restart` | マシンの設定が壊れている | 手で書き換えた | `named_cap.read` | 止める（起動しない） | 原因 `invalid` と文にファイル | `ndf relay cap` で直して打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_cap` | 書いた値を引数なしの `cap` と、その後の `spawn` の判定が使う | `--clear` の後は環境変数の値に戻る | 両方の引数で断る |
| `spawn` | 上限を下げた後は新しい起動を断り、動いているラッパーは止めない | 上限ちょうどの本数で断る | — |
| `listing` | 登録簿に無く錠を持つラッパーが `registered: false` の行で載る | 登録簿の行は今の項目のまま | — |
