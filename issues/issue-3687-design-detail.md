# #3687 の詳細設計: 分割した表の読み手・4 つの検査の規則・見本の突き合わせ

設計文書 [issue-3687-design.md](./issue-3687-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。各セクションの「満たす決定」が指す決定は [issue-3687-design-decisions.md](./issue-3687-design-decisions.md) にある。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── bin/ndf                                   （変更: exec の前に uv sync を 1 回、exec は uv run --no-sync）
├── contracts/
│   ├── limits/                              * （limits.json を 223 項目へ分割 + _meta.json）
│   ├── causes/                              * （causes.json を 35 項目へ分割 + _meta.json）
│   ├── live-samples/                        * （見本の宣言。1 見本 1 ファイル）
│   ├── replaceable/                         * （置き換えの規則。1 規則 1 ファイル）
│   ├── data/toolchain.json                  * （NDF が動く Python と uv）
│   ├── domains/split-tables.json            * （分ける表の一覧）
│   ├── domains/locations.json                （変更: 実験版の置き場の行を足す）
│   ├── hooks/imports.json                    （変更: boot.python を消す）
│   └── schemas/
│       ├── toolchain.schema.json            *
│       ├── live-sample.schema.json          *
│       ├── replaceable.schema.json          *
│       ├── limits.schema.json                （変更: 1 項目の形へ）
│       ├── causes.schema.json                （変更: 1 項目の形へ）
│       ├── ndf-settings/project.schema.json  （変更: toolchain・budget のキー）
│       ├── io/check.result.schema.json       （変更: metrics.budget の 4 件数）
│       └── io/samples.{args,result}.schema.json *
├── src/ndf/
│   ├── adapters/http.py                      （変更: fetch_body に本文の上限と転送の禁止の引数を足す）
│   ├── contracts/__init__.py                 （変更: load_split を足す）
│   ├── contracts/limits.py                   （変更: 分割した表を読む）
│   ├── contracts/causes.py                   （変更: 同上）
│   ├── contracts/toolchain.py               * （配る構成の宣言の読み手）
│   ├── checks/model.py                       （変更: 設定とリポジトリの根を渡す）
│   ├── checks/run.py                         （変更: 規則 4 つを並びへ）
│   ├── checks/rules.json                     （変更: 規則のしきい値と要約）
│   ├── checks/contracts_split.py            *
│   ├── checks/toolchain.py                  *
│   ├── checks/budget.py                     *
│   ├── checks/experimental.py               *
│   ├── domains/samples/verify.py            *
│   ├── domains/samples/cli.py               *
│   ├── domains/hooks/boot.py                 （変更: 下限の読み先を toolchain.json へ）
│   ├── domains/hooks/tables.py               （変更: 分割した表を標準ライブラリで読む）
│   ├── domains/hooks/budget.py               （変更: 予算の 1 項目だけを読む）
│   └── route/cli.py                          （変更: samples のサブコマンドを足す）
└── tests/
    ├── adapters/test_notify.py               （変更: fetch_body の本文の上限と 3xx のテストを足す）
    ├── samples/github/rest/issue.json       * （見本の実体）
    ├── contracts/test_split.py              *
    ├── contracts/test_toolchain.py          *
    ├── checks/test_{contracts_split,toolchain,budget,experimental}.py *
    └── domains/samples/test_{verify,cli}.py *
```

依存の向き: `contracts` → `checks` → `route`、`contracts` → `domains/samples` → `route` の一方向で、循環は無い。`checks` の 4 つの規則は `contracts` と `checks/model` だけに依存し、互いを参照しない。`domains/hooks` は `contracts` を import しない（標準ライブラリだけで動く縛り）。`domains/hooks/tables.py` が自分で JSON を読む向きを保つ。型の関係は設計文書の構成要素図を指し、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/contracts/__init__.py` | 変更 | 分割した表をディレクトリから読む | `json`・`pathlib`・`jsonschema` | モジュール | `load_split`（新設）・`split_tables`（新設） |
| `src/ndf/contracts/toolchain.py` | 新設 | 配る構成の宣言（Python と uv）の読み手 | `ndf.contracts` | モジュール | `table`・`python_floor`・`hook_floor`・`uv_version`・`uv_installer_sha256` |
| `src/ndf/contracts/limits.py` | 変更 | 上限の設定を分割した表から読む | `ndf.contracts` | モジュール | `table`（変更） |
| `src/ndf/contracts/causes.py` | 変更 | 停止理由を分割した表から読む | `ndf.contracts` | モジュール | `table`（変更） |
| `src/ndf/checks/contracts_split.py` | 新設 | 分割した表の形を確かめる規則 | `ndf.contracts`・`checks.model` | モジュール | `check` |
| `src/ndf/checks/toolchain.py` | 新設 | 配る構成の宣言と置き場の一致を確かめる規則 | `ndf.contracts.toolchain`・`checks.model`・`ruamel.yaml`・`tomllib` | モジュール | `check`・`workflow_tools`・`pyproject_floor` |
| `src/ndf/checks/budget.py` | 新設 | 件数の上限を確かめる規則 | `checks.model`・`checks.imports`・`ruamel.yaml`・`tomllib` | モジュール | `check`・`measure`・`count_tests`・`count_jobs`・`unused_deps`・`replaceable_hits` |
| `src/ndf/checks/experimental.py` | 新設 | 実験版への参照を確かめる規則 | `ndf.contracts`・`checks.model` | モジュール | `check` |
| `src/ndf/checks/model.py` | 変更 | 見る範囲にリポジトリの設定を持たせる | 既存 | `Scope` | `settings`（新設の属性）・`repo_files`（新設） |
| `src/ndf/checks/run.py` | 変更 | 規則 4 つを並びへ足し、`budget.measure` の件数を `metrics.budget` へ出す | 上の 4 モジュール | モジュール | `check`（変更）・`collect`（変更） |
| `src/ndf/adapters/http.py` | 変更 | 本文の取得に本文の上限と転送の禁止を指定できるようにする | 既存 | モジュール | `fetch_body`（変更。`max_bytes`・`follow` の引数） |
| `src/ndf/domains/samples/verify.py` | 新設 | 見本を本物の記述と突き合わせる | `ndf.contracts`・`ndf.adapters.http`・`jsonschema` | モジュール | `verify`・`fetch_document`・`definition_schema`・`compare`・`unknown_keys`・`describe` |
| `src/ndf/domains/samples/cli.py` | 新設 | `ndf samples verify` のエントリポイント | `verify`・`ndf.contracts.result` | モジュール | `main` |
| `src/ndf/domains/hooks/tables.py` | 変更 | 分割した表を標準ライブラリで読む | `json`・`pathlib` | モジュール | `load`（変更）・`load_split`（新設）・`policies`（変更）・`policy_value`（変更） |
| `src/ndf/domains/hooks/budget.py` | 変更 | hook の起動ごとの予算の引き方を 1 項目の読みへ替える | `tables` | モジュール | `policy_name`・`budget_for`（変更。`limits` を渡さないときの中身） |
| `src/ndf/domains/hooks/boot.py` | 変更 | システム python の下限の読み先を替える | `tables` | モジュール | （既存の下限の判定の行だけ） |
| `bin/ndf` | 変更 | 依存を同期してから uv へ渡す | — | シェルスクリプト | （`stop_deps_missing` の呼び出しを 1 か所足し、`exec` の `--frozen` を `--no-sync` へ替える） |

## 分割した表の読み手

満たす決定: 決定 1、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load_split` | `src/ndf/contracts/__init__.py` | 新設 | `load_split(rel: str) -> dict[str, Any]` | `contracts/<rel>/` の `_` で始まらないファイルを読み、ファイル名（拡張子 `.json` を除く）をキーにした辞書を返す |
| `split_tables` | `src/ndf/contracts/__init__.py` | 新設 | `split_tables() -> dict[str, dict[str, str]]` | 分ける表の一覧（`domains/split-tables.json`）を読み、表の名前 → 項目の単位とキーの出所を返す |
| `table`（`limits`） | `src/ndf/contracts/limits.py` | 変更 | `table() -> dict[str, Policy]` | 読み先を `limits.json` から `limits/` へ替える。返りの形は変えない |
| `table`（`causes`） | `src/ndf/contracts/causes.py` | 変更 | `table() -> dict[str, CauseRow]` | 読み先を `causes.json` から `causes/` へ替え、各ファイルの中身へ `kind`（ファイル名）を足してから確かめる |
| `load_split`（hook 側） | `src/ndf/domains/hooks/tables.py` | 新設 | `load_split(rel: str) -> dict[str, Any]` | 同じ規則を標準ライブラリだけで行う（`jsonschema` を使わない） |
| `policy_value`（hook 側） | `src/ndf/domains/hooks/tables.py` | 変更 | `policy_value(name: str, key: str) -> Any` | `contracts/limits/<name>.json` の 1 ファイルだけを読む（表の全体を組み立てない） |
| `policies`（hook 側） | `src/ndf/domains/hooks/tables.py` | 変更 | `policies(prefix: str = "") -> dict[str, dict[str, Any]]` | `contracts/limits/` のうち名前が `prefix` で始まる項目だけを読む。hook の経路で呼ぶのは SessionStart の予算の検査（`guards/session.py` の `check_budget`。`prefix="hook."` の 23 項目）だけで、`ndf hooks check-budget`（`cli.py`）も同じ `prefix` で呼ぶ |
| `has_policy`（hook 側） | `src/ndf/domains/hooks/tables.py` | 新設 | `has_policy(name: str) -> bool` | `contracts/limits/<name>.json` が在るか（読まずに `Path.is_file()` だけ） |
| `policy_name`・`budget_for`（hook 側） | `src/ndf/domains/hooks/budget.py` | 変更 | 変えない（`limits` を渡さない呼び出しの中身だけを替える） | `limits` を渡さないときは `policies()` で全件を組み立てず、`has_policy` で `hook.budget.<予算>.<ランタイム>` の有無を見て、使う 1 項目だけを `policy_value` の経路（1 項目 1 ファイル・1 プロセス 1 回）で読む。`main.py` が hook の起動のたびに `budget_for` を呼ぶため、ここで全件を読むと 1 起動ごとに 223 ファイルを読むことになる |

- `load_split`
  - 事前条件: `contracts/<rel>/` がディレクトリとして在る
  - 事後条件: 返りのキーは `_` で始まらないファイル名の集合と一致し、同じキーは 1 つだけである。並びはファイル名の昇順
  - ファイル名の規則（CLI 側と hook 側で同じ）: 項目のファイルは拡張子が小文字の `.json` のものだけである。`_` で始まらないのに拡張子が `.json` でないファイル（`a.JSON`・`a.txt` など）があれば、黙って飛ばさず誤りにする。キーは大小文字を区別して返すが、`str.casefold()` で同じになるキーが 2 つあれば重なりとして断る（大小文字を区別しないファイルシステムで 1 つに潰れるため）
- `policy_value`（hook 側）
  - 事後条件: 読むファイルは `contracts/limits/<name>.json` の 1 つだけ。1 プロセスで同じ名前を 2 回引いても読みは 1 回（`load` と同じ 1 プロセス 1 回の控え）。分割の前の `limits.json` を 1 回読んでいたのと同じく、上限 1 件の参照でファイルの読みは 1 回で済む
  - 副作用: ディレクトリの読み取りのみ。`cached_reader` を通して 1 プロセス 1 回に抑えるのは呼ぶ側（`limits.table` など）

- hook の 1 起動あたりの読み込み（2026-11-23 に測った。`limits.json` から 223 項目を一時ディレクトリへ分け、同じ機械で 1 回あたりの平均を取った）
  - 分割の前: どの hook も `limits.json`（39,107 バイト）を 1 回読む。1 回 188 マイクロ秒
  - 分割の後の 1 ファイル: 1 回 13 マイクロ秒。ファイルの数に比例し、2 ファイルで 26・5 ファイルで 67 マイクロ秒。`hook.` で始まる 23 項目をディレクトリの一覧から引くと 769、全 223 項目で 3,035 マイクロ秒
  - 代表の hook の事象と、引く方針（どの事象も `main.py` の `hook.transcript-tail` と `budget_for` の予算の 1 項目を引く）:

  | 事象 | 引く方針 | 分割の後の読み | 許す上限 |
  | --- | --- | --- | --- |
  | PreToolUse（Read）で `read_repeat` に届く | `hook.transcript-tail`・予算の 1 項目・`hook.read-repeat`。断るときは `hook.deny-repeat`・`hook.counter-lock` を足す | 3〜5 ファイル（40〜67 マイクロ秒） | 6 ファイル・分割の前の 188 マイクロ秒を超えない |
  | PreToolUse（Bash）で `sleep` に届く | `hook.transcript-tail`・予算の 1 項目・`hook.sleep` | 3 ファイル | 同上 |
  | Stop で `unattended` に届く | `hook.transcript-tail`・予算の 1 項目・`hook.unattended-mark`・`hook.pushback`・`hook.counter-lock` | 5 ファイル | 同上 |
  | SessionStart（予算の検査） | 上に加えて `policies(prefix="hook.")` の 23 項目 | 25 ファイル程度（約 800 マイクロ秒） | 1 セッションに 1 回だけなので、分割の前より 1 ミリ秒までの増えを許す |

  - 読み込みの回数は 1 起動につき 1 回から 3〜5 回へ増えるが、1 回あたりが小さくなるため、SessionStart を除く事象の所要は分割の前より短い。読む回数を増やさないための同じ名前の控え（`cached_reader`）は、同じ方針を 2 回引くときにだけ効く。違う方針を引く回数は上の表のとおりに抑える
  - 縛り方: 単体テストで、上の 4 つの事象それぞれの guard をプロセスの中で呼び（子のプロセスを起動しない）、`tables` のファイルの読みを数える関数に差し替えて、読んだファイルの数が「許す上限」の列以下であることを確かめる。全件（223 ファイル）を読む経路を hook の事象から呼ぶと落ちる。所要は Task 4 の測定の手順で同じ形に測り、単位の Pull Request の本文に書く（時間で合否を決めるテストは書かない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_split` | ディレクトリが無い | 分割の前の状態、または保存先の差し替えの誤り | `Path.is_dir()` | 呼び出し元へ返す（`ContractError`） | 読み手の例外（`LimitsError` など）に包まれ、`config_broken` で 2 | 再実行で直らない。ファイルを置いてから打ち直す |
| `load_split` | 項目のファイルが JSON でない | 手で書いた誤り | `json.loads` | 呼び出し元へ返す | 同上。`summary` にファイルのパス | 同上 |
| `load_split` | 項目でないファイルがある（`a.JSON`・`a.txt`） | 拡張子の誤り | ファイル名の拡張子の判定 | 呼び出し元へ返す | 同上。`summary` にファイルのパス | 同上 |
| `load_split` | 大小文字だけが違うキーが 2 つ（`a.json` と `A.json`） | 大文字と小文字だけが違う名前 | `casefold` したキーの重なりの判定（既存の `duplicates`） | 呼び出し元へ返す | 同上。`summary` に重なったキー | 同上 |
| `policy_value`（hook 側） | 項目のファイルが無い | 方針の名前の誤り | `Path.is_file()` | 呼び出し元へ返す（`TableError`） | 今の「方針 <名前> に <キー> が無い」と同じ | 同上 |
| `load_split` | 項目のファイルが辞書でない | 配列や文字列を書いた | 読んだ値の型の判定 | 呼び出し元へ返す | 同上 | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `META_PREFIX` | 定数 | `src/ndf/contracts/__init__.py` | `"_"`（この文字で始まるファイルは項目として読まない） |
| `SPLIT_TABLES` | 定数 | `src/ndf/contracts/__init__.py` | `"domains/split-tables.json"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load_split` | `contracts.load_json`（67 行）・`contracts.duplicates`（56 行）・`contracts._refuse_duplicate_keys`（60 行） | `grep -n 'def ' plugins/ndf/src/ndf/contracts/__init__.py` | 1 ファイルの読みと重なりの判定はそのまま使い、ディレクトリの走査だけを足す |
| `load_split`（hook 側） | `domains/hooks/tables.py` の `load` | `sed -n '1,30p' plugins/ndf/src/ndf/domains/hooks/tables.py` | 同じファイルへ並べて置く（`contracts` を import できないため、重なりの判定は書き写す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load_split` | 3 項目のディレクトリを読むと 3 キーの辞書になる。`_meta.json` はキーに入らない | 項目 0 件のディレクトリは空の辞書 | 上の異常系の 5 行それぞれ。CLI 側と hook 側の `load_split` へ同じ入力のディレクトリを渡し、同じ辞書か同じ誤りになる（1 つのテストで両方を引数化する） |
| `policy_value`（hook 側） | 1 項目のファイルから値を返す | 同じ名前を 2 回引いてもファイルの読みは 1 回 | 項目のファイルが無い |
| hook の事象ごとの読み | 上の表の 4 つの事象で、読んだファイルの数が許す上限以下 | `hook.budget.<予算>.<ランタイム>` の無い事象は、`has_policy` の確かめだけで共通の予算の 1 項目を読む | `budget_for` が `policies()` の全件を組み立てると落ちる |
| `limits.table` | 分割した表から読んだ結果が、分割の前の `limits.json` と同じ辞書になる | — | ディレクトリが無ければ `LimitsError` |
| `causes.table` | ファイル名が `kind` として入る | — | 中身に `kind` が書いてあってファイル名と違えば `CausesError` |

## 配る構成の宣言と検査

満たす決定: 決定 3、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `table` | `src/ndf/contracts/toolchain.py` | 新設 | `table() -> Toolchain` | `data/toolchain.json` を 1 プロセス 1 回読み、スキーマで確かめた値を返す |
| `python_floor` | `src/ndf/contracts/toolchain.py` | 新設 | `python_floor() -> tuple[int, int]` | NDF が動く Python の下限（`(3, 11)`） |
| `hook_floor` | `src/ndf/contracts/toolchain.py` | 新設 | `hook_floor() -> tuple[int, int]` | hook のシステム python の下限（`(3, 9)`） |
| `uv_version` | `src/ndf/contracts/toolchain.py` | 新設 | `uv_version() -> str` | uv のバージョン |
| `uv_installer_sha256` | `src/ndf/contracts/toolchain.py` | 新設 | `uv_installer_sha256() -> str` | uv のインストーラの照合値 |
| `check` | `src/ndf/checks/toolchain.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 宣言と置き場の食い違いを 1 か所 1 件の `Finding` にする |
| `workflow_tools` | `src/ndf/checks/toolchain.py` | 新設 | `workflow_tools(path: Path) -> list[ToolUse]` | 1 本のワークフローから `setup-uv`・`setup-python` のステップを拾い、行番号と `with` の値を返す |
| `pyproject_floor` | `src/ndf/checks/toolchain.py` | 新設 | `pyproject_floor(path: Path) -> tuple[tuple[int, int] \| None, int]` | `requires-python` の下限と、その行番号 |

- `check`
  - 事前条件: `scope.settings` にリポジトリの設定が入っている。設定の `toolchain` が無ければ何も照らさない（違反 0 件）。`workflows`・`plugin_pyprojects`・`launchers` はキーごとに独立で、在るキーの対象だけを照らす（設計文書の「配る構成の宣言と、リポジトリの設定」）
  - 事後条件: 返りの `Finding` は、`path` がリポジトリの根からの相対で、`line` が食い違った行
  - 副作用: ファイルの読み取りのみ（ネットワークは使わない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 宣言が読めない | `data/toolchain.json` が無い・形が違う | `toolchain.table()` | 止める（`CheckError`） | `config_broken` で 2 | 宣言を直して打ち直す |
| `workflow_tools` | ワークフローが YAML として読めない | 手で書いた誤り | `ruamel.yaml` の読み | 呼び出し元へ返す（`CheckError`） | 同上。`summary` にファイルのパス | 同上 |
| `pyproject_floor` | `requires-python` が無い・読めない | 宣言の欠け | 読んだ値の判定 | 違反にする（`Finding`） | 違反 1 件で 1 | 宣言を書いて打ち直す |
| `check` | `setup-uv` に `version` が無い | 固定を書き忘れた | `with` のキーの有無 | 違反にする | 同上 | 同上 |
| `check` | `setup-uv` 以外の経路で uv を入れる | `pip install uv` などを戻した | `run` の本文の照合（`UV_INSTALL_PATTERNS`） | 違反にする | 同上 | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 設定に `toolchain` が無い | 何も照らさない（違反 0 件。利用先のリポジトリは新しいキーを置くまで今までどおり） |
| `check` | 設定に `toolchain` は在るが、`workflows`・`plugin_pyprojects`・`launchers` の一部だけが在る | 在るキーの対象だけを照らす（無いキーの対象は見ない） |
| `check` | 設定に `toolchain.workflows` が在り、当たるファイルが 0 件 | 違反 0 件（グロブの誤りは検査しない。設定の責任） |
| `check` | `setup-python` の `python-version` が `toolchain.python_ci` に無く、`toolchain.python_compat` の `workflows` に当たるファイルで `versions` にも無い | その行を違反にする |
| `check` | `python-version` が `toolchain.python_compat` の `versions` に在るが、ファイルが `python_compat` の `workflows` に当たらない | その行を違反にする（下限の版は下限を確かめる手順にだけ書く） |
| `check` | `python-version` が `toolchain.python_compat` の `versions` に在り、ファイルが `workflows` に当たる | 違反 0 件 |
| `check` | `setup-uv` の `version` が宣言と違う、または無い | その行を違反にする |
| `check` | `launchers` のファイルの `UV_VERSION=` か `UV_INSTALLER_SHA256=` が宣言と違う | その行を違反にする |
| `check` | `workflows` に当たるファイルの `run` の本文が `setup-uv` 以外の uv の導入（`UV_INSTALL_PATTERNS` のどれか）に当たる | その行を違反にする（uv を入れてよい経路は、`version` を宣言と同じ値で持つ `setup-uv` だけ） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `UV_INSTALL_PATTERNS` | 定数 | `src/ndf/checks/toolchain.py` | `run` の本文で uv の導入とみなす形 3 つ（`pip3? install` の引数に `uv` がある・`pipx install uv`・`astral.sh/uv` の取得） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 宣言と 3 つの置き場がそろっていれば 0 件。`script-structure.yml` の `3.10` は `python_compat` で 0 件 | 設定に `toolchain` が無いとき 0 件で何も読まない。`plugin_pyprojects` だけが在るとき、ワークフローとランチャーを見ない | 上の異常系の 5 行それぞれ。`setup-uv` を持つワークフローに `pip install --upgrade uv` の手順を足すと 1 件。`python_compat` の `workflows` の外（`pytest.yml`）に `3.10` を書くと 1 件 |
| `workflow_tools` | `uses` が `astral-sh/setup-uv@<sha> # v10.3.3` の形でも Action の名前で拾える | `with` が無いステップ | YAML が壊れている |
| `pyproject_floor` | `>=3.11` から `(3, 11)` | `>=3.11,<4` のような複合の指定 | `requires-python` が無い |

## 件数の上限の検査

満たす決定: 決定 4、受け入れ条件 6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/checks/budget.py` | 新設 | `check(scope: Scope, rules: dict[str, Any], counts: Counts \| None = None) -> list[Finding]` | `measure` の件数（渡されなければ自分で数える）を設定の上限と比べ、超えた項目を違反にする |
| `measure` | `src/ndf/checks/budget.py` | 新設 | `measure(scope: Scope) -> Counts` | 4 つの件数と、使われない依存・置き換えられる自作の中身を返す。`run.py` が 1 回だけ呼び、`check` へ渡し、件数を結果 JSON の `metrics.budget`（`{"tests": .., "ci_jobs": .., "unused_deps": .., "replaceable": ..}`）へ出す。違反の有無にかかわらず出す |
| `count_tests` | `src/ndf/checks/budget.py` | 新設 | `count_tests(scope: Scope) -> int` | テストのファイルの `def test_`・`async def test_` の定義の数 |
| `count_jobs` | `src/ndf/checks/budget.py` | 新設 | `count_jobs(paths: list[Path]) -> int` | ワークフローの `jobs` のキーの数の合計 |
| `unused_deps` | `src/ndf/checks/budget.py` | 新設 | `unused_deps(scope: Scope) -> list[str]` | `toolchain.plugin_pyprojects` の各 `pyproject.toml` の `[project].dependencies` のうち、同じディレクトリの `src/` に import が無く、`budget.dep_exempt` にも無いものの名前 |
| `replaceable_hits` | `src/ndf/checks/budget.py` | 新設 | `replaceable_hits(scope: Scope) -> list[Finding]` | 置き換えの規則に当たった箇所 |

- `count_tests`
  - 事前条件: `scope.sources` の構文木が読めている（`unparsable` が先に走っている）
  - 事後条件: テストのファイルの見分けは既存の宣言（`contracts/domains/test-files.json`）に従い、数えるのは最上位とクラスの中の関数の定義だけ（入れ子の関数は数えない）
- `unused_deps`
  - 事前条件: `toolchain.plugin_pyprojects` の各 `pyproject.toml` と、同じディレクトリの `uv.lock` が読める。`plugin_pyprojects` が無ければ数えない（0 件）
  - 数える対象: `[project].dependencies`（直接の実行時の依存）だけ。`[dependency-groups]` と `[project.optional-dependencies]`（開発用）と、lock にだけある推移的な依存は数えない。宣言にあって同じディレクトリの `uv.lock` に無い依存も数えない（lock の鮮度は `uv lock --check` が別に見る）
  - 対にするソース: `pyproject.toml` と同じディレクトリの `src/` の下の `.py`（`scope` の構文木を使う）
  - import の名前: `budget.dep_modules` に在ればその値、無ければ正規化した名前の `-` と `.` を `_` へ替えたもの。import の最上位の名前と照らす
  - 事後条件: 返りは宣言の名前（PEP 503 で正規化した小文字）の昇順。`budget.dep_exempt` に挙げた依存は含まない
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 設定に `budget` が無い | 利用先のリポジトリ | 設定のキーの有無 | 既定値で続ける（違反 0 件） | 無し。0 | — |
| `check` | 上限が整数でない | 設定の誤り | 設定のスキーマ | 止める（`CheckError`） | `config_broken` で 2 | 設定を直して打ち直す |
| `count_jobs` | ワークフローに `jobs` が無い | 手で書いた誤り | 読んだ値の判定 | 既定値で続ける（0 件として数える） | 無し | — |
| `replaceable_hits` | 置き換えの規則が読めない | 宣言の欠け・形の誤り | `contracts.load_split` | 止める（`CheckError`） | `config_broken` で 2 | 宣言を直して打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 数えた件数 <= 上限 | 違反 0 件 |
| `check` | 数えた件数 > 上限 | 項目ごとに 1 件の `Finding`（`path` は設定のファイル、`detail` に「<数えた値> 件（上限 <上限>）」と超えた中身の先頭 5 件） |
| `unused_deps` | 依存が関数の中で import されている | 使っているとみなす（構文木の全体を見るため、行頭でなくても拾う） |
| `unused_deps` | 依存が子プロセスとして起動される（`ruff`） | `budget.dep_exempt` に挙げたものだけ、使っているとみなす（依存名の例外。`replaceable` の規則の `exempt` は保存先のパスの例外で、別のもの） |
| `unused_deps` | 依存が開発用（`pytest-xdist`）か推移的 | 数えない |
| `replaceable_hits` | 当たった箇所が規則の `exempt` の保存先にある | 数えない |

- 計算量の注意: 4 つの数えはいずれも構文木を 1 回走査するだけで、ファイルを読み直さない（構文木は `checks/model.py` の `Source` が 1 ファイル 1 回だけ作る）

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `DETAIL_HEAD` | 定数 | `src/ndf/checks/budget.py` | `5`（`detail` に並べる中身の件数） |
| `Counts` | 型 | `src/ndf/checks/budget.py` | 4 つの件数と、`unused_deps` の名前・`replaceable_hits` の箇所 |
| `ReplaceableRule` | 型 | `src/ndf/checks/budget.py` | 置き換えの規則 1 件（`find`・`replace_with`・`exempt`） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 4 項目とも上限の内なら 0 件 | 数えた値が上限と等しいとき 0 件、上限 + 1 で 1 件 | 上の異常系の 4 行それぞれ |
| `measure` と `run.py` | 違反 0 件の入力でも、`ndf check` の結果 JSON の `metrics.budget` に 4 つの件数が出て、その結果 JSON が `contracts/schemas/io/check.result.schema.json` に通る（`metrics` は `additionalProperties: false` で閉じているため、スキーマへ `budget` を足さないと落ちる） | 設定に `budget` が無いときも件数は出る（上限と比べないだけ） | — |
| `count_tests` | 最上位とクラスの中の `def test_` を数える | 入れ子の関数の `def test_` は数えない | 構文の誤りのファイルは `unparsable` が先に外す |
| `count_jobs` | 2 本のワークフローの `jobs` の合計 | matrix を持つジョブは 1 件として数える | `jobs` が無い |
| `unused_deps` | 行頭の import も関数の中の import も使っているとみなす | 名前の正規化（`mdit-py-plugins` と `mdit_py_plugins`）。`dep_modules` の別名（`python-dotenv` → `dotenv`）。`dep_exempt` の依存と開発用の依存は数えない | 依存の宣言が読めない |
| `replaceable_hits` | 規則に当たる呼び出しを数える | `exempt` の保存先は数えない | 規則の `find` が知らない種類 |

## 実験版の参照の検査

満たす決定: 決定 6、受け入れ条件 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/checks/experimental.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 既定で動くものから実験版の置き場への参照を違反にする |

- `check`
  - 事前条件: 保存先の表（`contracts/domains/locations.json`）に実験版の行が在る。無ければ違反 0 件で返す
  - 事後条件: 参照は、ファイルの本文に実験版の保存先のパス（`experimental/ndf/`）が現れることで見る。実験版は配布元の外にあり `ndf` のパッケージに入らないため、import の形は見ない
  - 見る範囲: プラグインの根（`plugins/ndf/`）の下で配るファイルすべて（`Scope.repo_files` で引き、`.py` 以外も本文を文字列として照らす）。起動の経路の `bin/`、プラグイン直下の設定（`.claude-plugin/`・`.codex-plugin/`・`pyproject.toml`）も含む
  - 除外の一覧（`EXEMPT`）: 次の 3 つは保存先のパスを持つのが役目なので数えない。(1) 保存先の表 `contracts/domains/locations.json` の、実験版の行の保存先の列（同じファイルのほかの行・ほかの列は数える）、(2) この規則の実装 `src/ndf/checks/experimental.py` と、`rules.json` の `experimental` のキーの値、(3) この規則のテスト `tests/checks/test_experimental.py`
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 保存先の表に実験版の行が無い | 宣言の前の状態 | 表の行の探索 | 既定値で続ける（違反 0 件） | 無し。0 | — |
| `check` | 保存先の表が読めない | 形の誤り | `contracts.facts` の読み | 止める（`CheckError`） | `config_broken` で 2 | 宣言を直して打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 参照するファイルが `bin/`・`src/`・`hooks/`・`skills/`・`agents/`・`contracts/`・`manifests/`・`.claude-plugin/`・`.codex-plugin/` の下か `pyproject.toml` | 違反 1 件 |
| `check` | 参照が除外の一覧（`EXEMPT`）に当たる | 違反 0 件（宣言そのものと規則の実装・テスト） |
| `check` | 参照するファイルが `README.md`・`CHANGELOG.md`・`docs/` の下 | 違反 0 件（利用者向けの説明として許す） |
| `check` | 参照するファイル自身が実験版の置き場の下 | 違反 0 件（実験版どうしの参照は許す） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `EXEMPT` | 定数 | `src/ndf/checks/experimental.py` | 上の除外の一覧の 3 つ（パスと、`locations.json` は行と列の指定） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | `src/` のファイルが実験版のパスを書くと 1 件。`bin/ndf` が書いても 1 件 | `README.md` からの参照は 0 件。`locations.json` の実験版の行の保存先の列は 0 件で、同じファイルの別の行の `summary` に書くと 1 件。今のリポジトリの全体に当てて 0 件（規則を入れた Pull Request が自分の宣言で落ちない） | 保存先の表が読めない |

## 見本の突き合わせ

満たす決定: 決定 2・7、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `verify` | `src/ndf/domains/samples/verify.py` | 新設 | `verify(peer: str \| None = None, fetch: Callable[[str], Any] = fetch_document) -> VerifyResult` | 宣言の見本を本物の記述と突き合わせ、見本ごとの結果（食い違い・取れない）と相手ごとの要求の数を返す |
| `fetch_document` | `src/ndf/domains/samples/verify.py` | 新設 | `fetch_document(url: str) -> dict[str, Any]` | 公開の記述（OpenAPI 3.1 の bundled 版）の文書の全体を 1 回の要求で取る。打ち直さない。`http.fetch_body(url, seconds, max_bytes=<samples.fetch の max_bytes>, follow=False)` で取り、本文を `json.loads` する |
| `fetch_body` | `src/ndf/adapters/http.py` | 変更（引数を足す） | `fetch_body(url: str, timeout: float, transport: httpx.BaseTransport \| None = None, *, max_bytes: int \| None = None, follow: bool = True) -> bytes` | 既定の値（上限なし・転送を追う）では今までと同じ。`max_bytes` を渡すと、受け取った本文の合計がそれを超えた時点で読むのをやめて `FetchError`（「本文が <max_bytes> バイトを超えた」）。`follow=False` なら転送を追わず、3xx の応答は本文を読まずに `FetchError`（「HTTP <状態>（転送は追わない）」）で、要求は 1 回だけ送る |
| `definition_schema` | `src/ndf/domains/samples/verify.py` | 新設 | `definition_schema(document: dict[str, Any], pointer: str) -> dict[str, Any]` | 文書の全体を根に持ったまま、ポインタの定義を指す検証用のスキーマ（`{"$schema": <2020-12>, "components": document["components"], "$ref": pointer}`。OpenAPI だけの語（`example`・`discriminator`）は 2020-12 の検証器が知らない語として無視する）を返す。切り出さないので、定義の中の `#/components/schemas/...` の参照は同じ文書の中で解ける |
| `compare` | `src/ndf/domains/samples/verify.py` | 新設 | `compare(sample: Any, schema: dict[str, Any]) -> list[str]` | `Draft202012Validator(schema)` の `iter_errors` の誤りを `describe` で値を含まない説明にし、`unknown_keys` の結果と合わせて昇順で返す（`contracts.schema_errors` の整形は `ValidationError.message` を使い、見本の値を含みうるので使わない） |
| `unknown_keys` | `src/ndf/domains/samples/verify.py` | 新設 | `unknown_keys(sample: Any, schema: dict[str, Any], document: dict[str, Any]) -> list[str]` | 見本の辞書のキーのうち、定義の `properties` に無いものを `<パス>: 知らないキー <キー>` の形で返す（JSON Schema の検証とは別に定める判定。下の「未知のキーの判定」） |
| `describe` | `src/ndf/domains/samples/verify.py` | 新設 | `describe(error: ValidationError) -> str` | 誤り 1 件を `<パス>: <検証の規則>`（`/`-区切りの `absolute_path`（最上位は `(最上位)`）と `error.validator`（`type`・`required`・`enum` など））で返す。`required` だけは足りないキーの名前を添える（キーの名前はスキーマの側の語で、応答の値ではない）。`error.message`・`error.instance` は使わない |
| `main` | `src/ndf/domains/samples/cli.py` | 新設 | `main(argv: list[str]) -> int` | 引数を読み、`verify` を呼び、結果 JSON を出して終了コードを返す |

- `verify`
  - 事前条件: 見本の宣言（`contracts/live-samples/`）と上限（`contracts/limits/samples.fetch.json` の `seconds`・`retries`・`peers`）が読める
  - 呼び出しの数え方: 相手ごとに、当てる見本の `source.schema` の異なる URL の数を求める。最悪の要求の数（異なる URL の数 ×（1 + `retries`））が `peers` のその相手の上限を超える宣言では、1 回も呼ばずに `precondition` で返す。`peers` に無い相手の見本も呼ばずに `precondition` で返す。取った文書は URL ごとに 1 回だけ取り、同じ URL を指す見本で使い回す。実際に送った要求（取得の失敗も 1 回と数える）を相手ごとに数えて返す
  - 事後条件: 見本ごとの結果の並びは見本のパスの昇順。1 つの URL が取れなくても、ほかの URL の見本は当て続ける
  - 副作用: ネットワーク（記述の取得。`adapters/http.py` の `fetch_body` を、転送を追わず（`follow=False`）・本文の上限（`max_bytes`）を付けて使う。打ち直しの繰り返しは `verify` も `fetch_body` も持たない）
  - 取得の経路の選び方: `request_json` は本文の上限が 1 MiB で、GitHub の記述（2026-11-23 の実測で 14,395,662 バイト）を受け取れないため使わない。`fetch_body` の既定は転送を追うため、転送 1 回ごとに要求が 1 回増えて `peers` の上限の数え方（1 URL 1 要求）と合わない。そこで `follow=False` で 1 URL 1 要求に固定し、宣言の URL が転送を返したら `unreachable` にする（宣言の URL を転送の先へ直すのは人の仕事）
- 未知のキーの判定（`unknown_keys`）
  - なぜ別に要るか: GitHub の記述の `issue` の定義は `additionalProperties` を書かない（2026-11-23 の記述で確認）。JSON Schema では書かないキーは何でも受けるため、検証だけでは見本にキーが 1 つ増えても通る
  - 判定: 見本の辞書ごとに、その位置の定義（`$ref` は文書の中で解いた先）の `properties` のキーの集合を求め、見本のキーのうち集合に無いものを未知のキーとする。`allOf` は各枝の `properties` の和を、`anyOf`・`oneOf` は見本がその枝の検証に通る枝の `properties` の和を使う（どの枝にも通らなければ検証の誤りが先に出るので、未知のキーは数えない）。定義が `additionalProperties` か `patternProperties` を明示していればそれに従い（`false` なら JSON Schema の検証が既に落とすので数えない、スキーマか `true` なら未知とみなさない）、`properties` を持たない定義（自由な辞書）は数えない。配列は `items` の定義で要素ごとに同じ判定を行う
  - 出力: パスとキーの名前だけ（値は出さない）
- `definition_schema`
  - 事前条件: 文書の `openapi` が `3.1.` で始まる（3.0 の文書は `nullable` を持ち、2020-12 の検証器では null を受けられないため断る）
  - 事後条件: 返りのスキーマを `Draft202012Validator.check_schema` に通せる。参照は文書の中（`#/components/...`）だけで解き、外の URL を取りに行かない（`referencing` の既定の取得は使わない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `fetch_document` | 記述を取れない | ネットワーク・相手の障害 | `httpx` の例外と状態コード | その URL の見本を `unreachable` にして続ける | ほかに食い違いが無ければ `infra_wait` で 75。`items` に `kind: unreachable` の見本 | 再実行で直りうる（毎日のジョブは次の日に再び打つ） |
| `fetch_document` | 転送（3xx）が返った | 相手が記述の置き場を移した | `fetch_body(follow=False)` の `FetchError` | 転送の先を取りに行かず、その URL の見本を `unreachable` にして続ける（要求は 1 回と数える） | 同上。`detail` に状態コード | 宣言の URL を転送の先へ直して打ち直す |
| `fetch_document` | 本文が上限を超えた | 記述が大きくなった、または別の物を返した | `fetch_body(max_bytes=...)` の `FetchError` | 読むのをやめ、その URL の見本を `unreachable` にして続ける | 同上。`detail` に上限のバイト数 | `samples.fetch` の `max_bytes` を見直して打ち直す |
| `definition_schema` | 文書が 3.1 でない | 宣言の URL の誤り（3.0 の `descriptions/` を指した） | `openapi` の値 | 止める（呼び出しの後だが見本を当てない） | `config_broken` で 2 | 宣言の URL を直して打ち直す |
| `definition_schema` | ポインタの先が無い | 相手の記述の形が変わった | 引いた値の有無 | 違反にする（食い違い 1 件） | `samples_mismatch` で 1。`items` に見本のパスとポインタ | 宣言を直して打ち直す |
| `compare` | 文書の中の参照が解けない | 相手の記述の形が変わった（参照先の定義が消えた） | `referencing` の `Unresolvable` | 違反にする（食い違い 1 件。例外で落とさない） | `samples_mismatch` で 1。`items` に解けなかった参照 | 見本と宣言を直して打ち直す |
| `compare` | 見本が記述に合わない | 相手の API の形が変わった、または見本が古い | `jsonschema` の検証 | 違反にする | `samples_mismatch` で 1。`items` の `detail` に食い違ったパスと検証の規則（`describe`。値は出さない） | 見本を録り直して打ち直す |
| `unknown_keys` | 見本に定義に無いキーがある | 相手がキーを消した・改名した、または見本が古い | `unknown_keys` の判定 | 違反にする | `samples_mismatch` で 1。`items` の `detail` にパスとキーの名前 | 見本を録り直して打ち直す |
| `verify` | 最悪の要求の数が上限を超える・上限の無い相手 | 宣言を増やしすぎた | 宣言を読んだ後の数え | 止める（呼ばない） | `precondition` で 3。`summary` に相手ごとの数と上限 | 宣言を減らすか上限を上げる |
| `verify` | 見本のファイルが無い | 宣言とファイルの食い違い | `Path.is_file()` | 違反にする | `samples_mismatch` で 1 | 見本を置いて打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `verify` | `peer` を渡した | その相手の宣言だけを当てる |
| `verify` | 宣言が 0 件 | 食い違い 0 件で `ok` |
| `verify` | 食い違い 0 件・取れない 0 件 | `ok`（0） |
| `verify` | 食い違い 1 件以上（取れない見本があってもよい） | `samples_mismatch`（1）。`items` に食い違いと取れない見本の両方を載せる |
| `verify` | 食い違い 0 件・取れない 1 件以上 | `infra_wait`（75） |
| `compare` | 見本が定義に合う | 食い違い 0 件 |
| `compare` | 定義に `additionalProperties` が無く、見本に定義に無いキーが 1 つある | 食い違い 1 件（`unknown_keys`）。JSON Schema の検証だけなら通る形である |

- 繰り返しの終わる条件: 異なる URL の数だけ取り、見本の件数だけ比べて終わる（打ち直しの繰り返しは持たない。失敗は次の日のジョブに任せる）
- 報告側との契約: 毎日のジョブの報告（`scripts/runtime-smoke-latest.py report`）は、終了コードではなく `items` の `kind: mismatch` を相手ごとに数え、1 件以上の相手ごとに課題を 1 件作る。`kind: unreachable` だけの相手には作らない。報告のジョブは `live-samples` が失敗で終わっても動き（`if: ${{ !cancelled() && contains(needs.*.result, 'failure') }}`）、結果 JSON は `live-samples` の `if: always()` の手順が成果物へ上げる。成果物が無ければ（検証の前の失敗）報告は何もせずに終える

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VerifyResult` | 型 | `src/ndf/domains/samples/verify.py` | 見本ごとの結果の並び・当てた数・相手ごとの要求の数 |
| `Outcome` | 型 | `src/ndf/domains/samples/verify.py` | 見本のパス・相手・`kind`（`mismatch`・`unreachable`）・説明 |
| 記述の取得の上限 | 設定値 | `contracts/limits/samples.fetch.json` | 秒 60・打ち直し 0 回・本文の上限 `max_bytes: 67108864`（64 MiB。2026-11-23 の実測 14,395,662 バイトの約 4.7 倍）・相手ごとの要求の上限 `peers: {"github": 2}`（分割した `limits` の 1 項目として足す。`limits.schema.json` の 1 項目の形へ `retries`・`max_bytes` と `peers`（相手 → 整数）を足す） |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `main` | 結果 JSON（`items` に見本ごとの `sample`・`peer`・`kind`・`detail`、`metrics` に `samples`・`calls`） | 標準出力の最後の 1 行 | 取得した記述の本文、相手の応答の値（見本のパスと食い違ったキーだけを出す） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `fetch_document` | `src/ndf/adapters/http.py` の `request_json`（転送を追わない・本文 1 MiB まで）と `fetch_body`（転送を追う・本文の上限なし・総経過時間の期限） | `sed -n '236,262p;485,560p' plugins/ndf/src/ndf/adapters/http.py` | どちらもそのままは使えない。`request_json` は 1 MiB の上限で GitHub の記述（14,395,662 バイト）を落とし、`fetch_body` は転送を追うので 1 URL 1 要求にならない。`fetch_body` へ `max_bytes` と `follow` の引数を足して使う（期限の仕組み（`_Fetch`・`_Watchdog`）はそのまま再利用し、既定の値の呼び出し元の振る舞いは変えない）。打ち直しはどちらも持たないので、`retries`＝0 は `verify` の要求の数の見積もりにだけ使う |
| `compare` | `src/ndf/contracts/__init__.py` の `schema_errors`（116 行）・`_schema_validator`（101 行） | 同ファイルの読み | 使わない。`schema_errors` はコントラクトの下のファイルのパスを取り、`$defs` だけを持たせた写しを作るため、取得した文書の `components` を解けない。誤りの整形も `ValidationError.message` を使い、型の誤りなどで見本の値を含むため、見本の検証の出力（値を出さない契約）に使えない。検証器は `definition_schema` の返りから作り（`_schema_validator` の「根を残して `$ref` で指す」作り方と同じ形）、整形は `describe` が `absolute_path` と `validator` だけから組み立てる |
| `main` | `src/ndf/contracts/result.py` の `emit` | `git grep -n 'def emit' -- plugins/ndf/src` | 使う（結果 JSON の形とテストの差し替え（`conftest.py` の `_checked_emit`）に乗る） |

### 単体テストの観点

偽の記述は本物の `issue` と同じ参照の構造にする（`components.schemas.issue` が `#/components/schemas/simple-user`・`#/components/schemas/label` を `$ref` で指し、null を取る列を `"type": ["string", "null"]` で書き、本物と同じく `additionalProperties` を書かない。`openapi` は `3.1.0`）。テストは `fetch` の引数へ偽の関数を渡し、ネットワークに出ない。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `verify` | 偽の取得で、見本が定義に合えば食い違い 0 件 | 宣言 0 件で `ok`。`peer` の絞り込みで 1 件だけ当てる。同じ URL の見本 2 件で要求は 1 回 | 上の異常系の 7 行それぞれ。1 つの URL が取れず別の URL の見本が食い違う入力で `samples_mismatch` と両方の `items` |
| `definition_schema` | 参照を含む定義でも、参照先の列の型の誤りを見つける（参照先を検証せずに通さない） | null を取る列に null の見本が通る | 3.0 の文書（`nullable` を持つ）を断る。ポインタの先が無い |
| `compare` | 見本が定義に合えば 0 件 | 空の辞書の見本 | キーの型が違う見本で、そのキーのパスと `type` が説明に出て、見本の値（目印の文字列 `SECRET-VALUE-3687` を入れておく）は説明に出ない。`enum` の誤りでも値が出ない。参照先の定義を消した文書で例外にならず食い違い 1 件 |
| `unknown_keys` | 本物と同じく `additionalProperties` を書かない `issue` の定義で、定義どおりの見本は 0 件 | 入れ子（`user` の `simple-user`）と配列（`labels` の `label`）の中のキーも見る。`anyOf` の枝の `properties`。`additionalProperties` を明示した定義では数えない | 最上位にキーを 1 つ足した見本で 1 件（`additionalProperties` の無い定義でも落ちる）。`user` の中にキーを足しても 1 件 |
| `main` | 結果 JSON の形がサブコマンドのスキーマに合う | 食い違い 0 件のときの `metrics` | `infra_wait` のとき 75 |
| `fetch_body`（`httpx.MockTransport` を渡す。ネットワークに出ない） | `max_bytes` と `follow` を渡さない呼び出しは今までどおり転送を追う | 本物と同じ 14,395,662 バイトの本文を `max_bytes` 64 MiB で受け取れる（`request_json` の 1 MiB を超える大きさ）。本文が `max_bytes` + 1 バイトで `FetchError` | `follow=False` で 301・302・307 を返すと `FetchError` で、転送の先へ要求が送られない（転送の先を返す偽の相手への要求が 0 回）。`verify` へ通すと、その URL の要求は 1 回と数えられ `unreachable` になる |

## 依存の用意

満たす決定: 決定 5、受け入れ条件 5

### 関数の仕様

`plugins/ndf/bin/ndf` はシェルスクリプトで、関数は既にある `stop_deps_missing`（48-58 行）だけを使う。足すのは 98 行の `exec` の前の 3 行である。

| 箇所 | 種類 | 変える中身 |
| --- | --- | --- |
| `bin/ndf` の 98 行の直前 | 変更 | `"$uv_bin" sync --frozen --no-dev --quiet --project "$root"` を打ち、0 以外なら `stop_deps_missing "依存を用意できない（ネットワークを確かめる）: <ログの最終行>"` を呼ぶ |
| `bin/ndf` の 98 行 | 変更 | `exec "$uv_bin" run --frozen --no-dev ...` の `--frozen` を `--no-sync` へ替える。直前の同期で用意した同じ環境（`UV_PROJECT_ENVIRONMENT`）をそのまま使い、`exec` の後に uv が環境を同期し直さない |

- 事前条件: `uv_bin` が決まっている（uv を探すか入れる処理の後）
- 事後条件: `exec` へ進むのは同期が成功したときだけ。同期の経路を通るのは 1 起動に 1 回（`uv run --no-sync`）。成功したときのシグナルの伝わり方は今までと変わらない（`exec` のまま）
- lock の扱い: 同期は今の `uv run` と同じ `--frozen`（lock を更新せず、宣言と lock の鮮度は確かめない）。宣言と lock の食い違いは、配る前に CI の `uv lock --check`（`script-structure.yml` の lock の検査）が落とす
- 副作用: `.venv` の作成・更新と、ログのファイル（既にある `$log` を使う）

### 異常系の処理

| 箇所 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| 同期 | 依存を取れない | ネットワークが無い・相手の障害 | 終了コード | 止める（`stop_deps_missing`） | 結果 JSON 1 行と `deps_missing`（69） | 再実行で直りうる |

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `start.emit_deps_missing`（単体テスト。子のプロセスを起動しない） | 同期の失敗のメッセージ（ログの最終行を含む）を渡すと、結果 JSON 1 行の `summary` と `cause.detail` にそのメッセージが入る | 空のログの最終行 | 終了コードが `deps_missing`（69） |
| `bin/ndf`（リリース後テストの実機の確認。単体テストに足さない） | 同期が成功すれば今までどおりサブコマンドが動く | 同期済みの状態で 2 回続けて打っても同じ結果（冪等） | `UV_OFFLINE=1` で venv の無い状態から打つと、結果 JSON 1 行と 69 |

## 検査の規則を足す手順

`ndf check` の登録は `run.py` の `check()` の呼び出しの並びである（設計文書の「今の姿」）。4 つの規則はこの並びへ次の順で足す。順序は指摘の並び（`_order`）に影響しない。

```python
counts = budget.measure(scope)  # metrics.budget へも出す（違反の有無に依らない）
found += contracts_split.check(scope, rules) + toolchain.check(scope, rules)
found += budget.check(scope, rules, counts) + experimental.check(scope, rules)
```

`rules.json` へ足すキーは `contracts_split`・`toolchain`・`budget`・`experimental` の 4 つで、それぞれ `summary` を持つ。`model.py` の `load_rules` が見る必須のキーの一覧（130 行）へ、この 4 つを足す。

`Scope` にはリポジトリの設定（`.ndf/project.json`）を読んだ値（`settings`）と、プラグインの外のファイルを引く `repo_files(glob)` を足す。4 つの規則のうち `toolchain`・`budget`・`experimental` がこれを使う（`experimental` はプラグインの根の下の `.py` 以外のファイルを引くため）。`contracts-split` とほかの既存の規則は今までどおりプラグインの下の構文木だけを見る。
