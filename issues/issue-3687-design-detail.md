# #3687 の詳細設計: 分割した表の読み手・4 つの検査の規則・見本の突き合わせ

設計文書 [issue-3687-design.md](./issue-3687-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。各セクションの「満たす決定」が指す決定は [issue-3687-design-decisions.md](./issue-3687-design-decisions.md) にある。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── bin/ndf                                   （変更: exec の前に uv sync を 1 回）
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
│       └── io/samples.{args,result}.schema.json *
├── src/ndf/
│   ├── contracts/__init__.py                 （変更: load_split を足す）
│   ├── contracts/limits.py                   （変更: 分割した表を読む）
│   ├── contracts/causes.py                   （変更: 同上）
│   ├── contracts/toolchain.py               * （配る構成の宣言の読み手）
│   ├── checks/model.py                       （変更: 設定と репо の根を渡す）
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
│   └── route/cli.py                          （変更: samples のサブコマンドを足す）
└── tests/
    ├── samples/github/rest/issue.json       * （見本の実体）
    ├── contracts/test_split.py              *
    ├── contracts/test_toolchain.py          *
    ├── checks/test_{contracts_split,toolchain,budget,experimental}.py *
    └── domains/samples/test_{verify,cli}.py *
```

依存の向き: `contracts` → `checks` → `route`、`contracts` → `domains/samples` → `route` の一方向で、循環は無い。`checks` の 4 つの規則は `contracts` と `checks/model` だけに依存し、互いを参照しない。`domains/hooks` は `contracts` を import しない（標準ライブラリだけで動く縛り）。`domains/hooks/tables.py` が自分で JSON を読む向きを保つ。型の関係は設計文書の構成要素図を指し、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/contracts/__init__.py` | 変更 | 分割した表をディレクトリから読む | `json`・`pathlib` | モジュール | `load_split`（新設）・`split_tables`（新設） |
| `src/ndf/contracts/toolchain.py` | 新設 | 配る構成の宣言（Python と uv）の読み手 | `ndf.contracts` | モジュール | `table`・`python_floor`・`hook_floor`・`uv_version`・`uv_installer_sha256` |
| `src/ndf/contracts/limits.py` | 変更 | 上限の設定を分割した表から読む | `ndf.contracts` | モジュール | `table`（変更） |
| `src/ndf/contracts/causes.py` | 変更 | 停止理由を分割した表から読む | `ndf.contracts` | モジュール | `table`（変更） |
| `src/ndf/checks/contracts_split.py` | 新設 | 分割した表の形を確かめる規則 | `ndf.contracts`・`checks.model` | モジュール | `check` |
| `src/ndf/checks/toolchain.py` | 新設 | 配る構成の宣言と置き場の一致を確かめる規則 | `ndf.contracts.toolchain`・`checks.model`・`ruamel.yaml`・`tomllib` | モジュール | `check`・`workflow_tools`・`pyproject_floor` |
| `src/ndf/checks/budget.py` | 新設 | 件数の上限を確かめる規則 | `checks.model`・`checks.imports`・`ruamel.yaml`・`tomllib` | モジュール | `check`・`count_tests`・`count_jobs`・`unused_deps`・`replaceable_hits` |
| `src/ndf/checks/experimental.py` | 新設 | 実験版への参照を確かめる規則 | `ndf.contracts`・`checks.model` | モジュール | `check` |
| `src/ndf/checks/model.py` | 変更 | 見る範囲にリポジトリの設定を持たせる | 既存 | `Scope` | `settings`（新設の属性）・`repo_files`（新設） |
| `src/ndf/checks/run.py` | 変更 | 規則 4 つを並びへ足す | 上の 4 モジュール | モジュール | `check`（変更）・`collect`（変更） |
| `src/ndf/domains/samples/verify.py` | 新設 | 見本を本物の記述・出力と突き合わせる | `ndf.contracts`・`httpx`・`jsonschema` | モジュール | `verify`・`fetch_schema`・`fetch_command`・`compare` |
| `src/ndf/domains/samples/cli.py` | 新設 | `ndf samples verify` のエントリポイント | `verify`・`ndf.contracts.result` | モジュール | `main` |
| `src/ndf/domains/hooks/tables.py` | 変更 | 分割した表を標準ライブラリで読む | `json`・`pathlib` | モジュール | `load`（変更）・`load_split`（新設） |
| `src/ndf/domains/hooks/boot.py` | 変更 | システム python の下限の読み先を替える | `tables` | モジュール | （既存の下限の判定の行だけ） |
| `bin/ndf` | 変更 | 依存を同期してから uv へ渡す | — | シェルスクリプト | （`stop_deps_missing` の呼び出しを 1 か所足す） |

## 分割した表の読み手

満たす決定: 決定 1、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load_split` | `src/ndf/contracts/__init__.py` | 新設 | `load_split(rel: str) -> dict[str, Any]` | `contracts/<rel>/` の `_` で始まらない `.json` を読み、ファイル名（拡張子を除く）をキーにした辞書を返す |
| `split_tables` | `src/ndf/contracts/__init__.py` | 新設 | `split_tables() -> dict[str, dict[str, str]]` | 分ける表の一覧（`domains/split-tables.json`）を読み、表の名前 → 項目の単位とキーの出所を返す |
| `table`（`limits`） | `src/ndf/contracts/limits.py` | 変更 | `table() -> dict[str, Policy]` | 読み先を `limits.json` から `limits/` へ替える。返りの形は変えない |
| `table`（`causes`） | `src/ndf/contracts/causes.py` | 変更 | `table() -> dict[str, CauseRow]` | 読み先を `causes.json` から `causes/` へ替え、各ファイルの中身へ `kind`（ファイル名）を足してから確かめる |
| `load_split`（hook 側） | `src/ndf/domains/hooks/tables.py` | 新設 | `load_split(rel: str) -> dict[str, Any]` | 同じ規則を標準ライブラリだけで行う（`jsonschema` を使わない） |

- `load_split`
  - 事前条件: `contracts/<rel>/` がディレクトリとして在る
  - 事後条件: 返りのキーは `_` で始まらないファイル名の集合と一致し、同じキーは 1 つだけである。並びはファイル名の昇順
  - 副作用: ディレクトリの読み取りのみ。`cached_reader` を通して 1 プロセス 1 回に抑えるのは呼ぶ側（`limits.table` など）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_split` | ディレクトリが無い | 分割の前の状態、または保存先の差し替えの誤り | `Path.is_dir()` | 呼び出し元へ返す（`ContractError`） | 読み手の例外（`LimitsError` など）に包まれ、`config_broken` で 2 | 再実行で直らない。ファイルを置いてから打ち直す |
| `load_split` | 項目のファイルが JSON でない | 手で書いた誤り | `json.loads` | 呼び出し元へ返す | 同上。`summary` にファイルのパス | 同上 |
| `load_split` | 同じキーのファイルが 2 つ（`a.json` と `a.JSON`） | 大文字と小文字だけが違う名前 | キーを集めた後の重なりの判定（既存の `duplicates`） | 呼び出し元へ返す | 同上。`summary` に重なったキー | 同上 |
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
| `load_split` | 3 項目のディレクトリを読むと 3 キーの辞書になる。`_meta.json` はキーに入らない | 項目 0 件のディレクトリは空の辞書 | 上の異常系の 4 行それぞれ |
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
| `pyproject_floor` | `src/ndf/checks/toolchain.py` | 新設 | `pyproject_floor(path: Path) -> tuple[tuple[int, int] | None, int]` | `requires-python` の下限と、その行番号 |

- `check`
  - 事前条件: `scope.settings` にリポジトリの設定が入っている（無ければ `toolchain` のキーを見ずにプラグインの中だけを見る）
  - 事後条件: 返りの `Finding` は、`path` がリポジトリの根からの相対で、`line` が食い違った行
  - 副作用: ファイルの読み取りのみ（ネットワークは使わない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 宣言が読めない | `data/toolchain.json` が無い・形が違う | `toolchain.table()` | 止める（`CheckError`） | `config_broken` で 2 | 宣言を直して打ち直す |
| `workflow_tools` | ワークフローが YAML として読めない | 手で書いた誤り | `ruamel.yaml` の読み | 呼び出し元へ返す（`CheckError`） | 同上。`summary` にファイルのパス | 同上 |
| `pyproject_floor` | `requires-python` が無い・読めない | 宣言の欠け | 読んだ値の判定 | 違反にする（`Finding`） | 違反 1 件で 1 | 宣言を書いて打ち直す |
| `check` | `setup-uv` に `version` が無い | 固定を書き忘れた | `with` のキーの有無 | 違反にする | 同上 | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 設定に `toolchain` が無い | ワークフローを見ず、`pyproject` と `bin/ndf` の 2 つだけを宣言と照らす |
| `check` | 設定に `toolchain.workflows` が在り、当たるファイルが 0 件 | 違反 0 件（グロブの誤りは検査しない。設定の責任） |
| `check` | `setup-python` の `python-version` が `toolchain.python_ci` に無い | その行を違反にする |
| `check` | `setup-uv` の `version` が宣言と違う、または無い | その行を違反にする |
| `check` | `bin/ndf` の `UV_VERSION=` か `UV_INSTALLER_SHA256=` が宣言と違う | その行を違反にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 宣言と 3 つの置き場がそろっていれば 0 件 | 設定に `toolchain` が無いとき、ワークフローを見ない | 上の異常系の 4 行それぞれ |
| `workflow_tools` | `uses` が `astral-sh/setup-uv@<sha> # v10.3.3` の形でも Action の名前で拾える | `with` が無いステップ | YAML が壊れている |
| `pyproject_floor` | `>=3.11` から `(3, 11)` | `>=3.11,<4` のような複合の指定 | `requires-python` が無い |

## 件数の上限の検査

満たす決定: 決定 4、受け入れ条件 6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/checks/budget.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 4 つの件数を数え、設定の上限を超えた項目を違反にする |
| `count_tests` | `src/ndf/checks/budget.py` | 新設 | `count_tests(scope: Scope) -> int` | テストのファイルの `def test_`・`async def test_` の定義の数 |
| `count_jobs` | `src/ndf/checks/budget.py` | 新設 | `count_jobs(paths: list[Path]) -> int` | ワークフローの `jobs` のキーの数の合計 |
| `unused_deps` | `src/ndf/checks/budget.py` | 新設 | `unused_deps(scope: Scope) -> list[str]` | 宣言した依存のうち、`src/` に import も子プロセスの起動も無いものの名前 |
| `replaceable_hits` | `src/ndf/checks/budget.py` | 新設 | `replaceable_hits(scope: Scope) -> list[Finding]` | 置き換えの規則に当たった箇所 |

- `count_tests`
  - 事前条件: `scope.sources` の構文木が読めている（`unparsable` が先に走っている）
  - 事後条件: テストのファイルの見分けは既存の宣言（`contracts/domains/test-files.json`）に従い、数えるのは最上位とクラスの中の関数の定義だけ（入れ子の関数は数えない）
- `unused_deps`
  - 事前条件: 依存の宣言のファイル（`pyproject.toml`）が読める
  - 事後条件: 返りは宣言の名前（正規化した小文字）の昇順。`exempt` に挙げた依存は含まない
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
| `unused_deps` | 依存が子プロセスとして起動される（`ruff`） | `exempt` の宣言に挙げたものだけ、使っているとみなす |
| `replaceable_hits` | 当たった箇所が規則の `exempt` の保存先にある | 数えない |

- 計算量の注意: 4 つの数えはいずれも構文木を 1 回走査するだけで、ファイルを読み直さない（構文木は `checks/model.py` の `Source` が 1 ファイル 1 回だけ作る）

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `DETAIL_HEAD` | 定数 | `src/ndf/checks/budget.py` | `5`（`detail` に並べる中身の件数） |
| `ReplaceableRule` | 型 | `src/ndf/checks/budget.py` | 置き換えの規則 1 件（`find`・`replace_with`・`exempt`） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 4 項目とも上限の内なら 0 件 | 数えた値が上限と等しいとき 0 件、上限 + 1 で 1 件 | 上の異常系の 4 行それぞれ |
| `count_tests` | 最上位とクラスの中の `def test_` を数える | 入れ子の関数の `def test_` は数えない | 構文の誤りのファイルは `unparsable` が先に外す |
| `count_jobs` | 2 本のワークフローの `jobs` の合計 | matrix を持つジョブは 1 件として数える | `jobs` が無い |
| `unused_deps` | 行頭の import も関数の中の import も使っているとみなす | 名前の正規化（`mdit-py-plugins` と `mdit_py_plugins`） | 依存の宣言が読めない |
| `replaceable_hits` | 規則に当たる呼び出しを数える | `exempt` の保存先は数えない | 規則の `find` が知らない種類 |

## 実験版の参照の検査

満たす決定: 決定 6、受け入れ条件 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/checks/experimental.py` | 新設 | `check(scope: Scope, rules: dict[str, Any]) -> list[Finding]` | 既定で動くものから実験版の置き場への参照を違反にする |

- `check`
  - 事前条件: 保存先の表（`contracts/domains/locations.json`）に実験版の行が在る。無ければ違反 0 件で返す
  - 事後条件: 参照は、ファイルの本文に実験版の保存先のパスが現れることと、`ndf.experimental` で始まる import の 2 つで見る
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 保存先の表に実験版の行が無い | 宣言の前の状態 | 表の行の探索 | 既定値で続ける（違反 0 件） | 無し。0 | — |
| `check` | 保存先の表が読めない | 形の誤り | `contracts.facts` の読み | 止める（`CheckError`） | `config_broken` で 2 | 宣言を直して打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `check` | 参照するファイルが `src/`・`hooks/`・`skills/`・`agents/`・`contracts/`・`manifests/` の下 | 違反 1 件 |
| `check` | 参照するファイルが `README.md`・`CHANGELOG.md`・`docs/` の下 | 違反 0 件（利用者向けの説明として許す） |
| `check` | 参照するファイル自身が実験版の置き場の下 | 違反 0 件（実験版どうしの参照は許す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | `src/` のファイルが実験版のパスを書くと 1 件 | `README.md` からの参照は 0 件。実験版の中からの参照も 0 件 | 保存先の表が読めない |

## 見本の突き合わせ

満たす決定: 決定 2・7、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `verify` | `src/ndf/domains/samples/verify.py` | 新設 | `verify(peer: str \| None = None) -> VerifyResult` | 宣言の見本を本物と突き合わせ、食い違いと呼び出しの数を返す |
| `fetch_schema` | `src/ndf/domains/samples/verify.py` | 新設 | `fetch_schema(url: str, pointer: str) -> dict[str, Any]` | 公開の記述を取り、JSON ポインタで 1 つの定義を引く |
| `fetch_command` | `src/ndf/domains/samples/verify.py` | 新設 | `fetch_command(argv: list[str]) -> str` | コマンドを引数の配列のまま実行し、標準出力を返す（シェルを通さない） |
| `compare` | `src/ndf/domains/samples/verify.py` | 新設 | `compare(sample: Any, got: Any, how: str) -> list[str]` | 比べ方（`schema`・`keys`・`exact`）ごとに食い違いの説明を返す |
| `main` | `src/ndf/domains/samples/cli.py` | 新設 | `main(argv: list[str]) -> int` | 引数を読み、`verify` を呼び、結果 JSON を出して終了コードを返す |

- `verify`
  - 事前条件: 見本の宣言（`contracts/live-samples/`）が読める
  - 事後条件: 相手ごとの呼び出しの合計が上限を超える宣言では、1 回も呼ばずに `precondition` で返す。食い違いの並びは見本のパスの昇順
  - 副作用: ネットワーク（記述の取得）と子プロセスの起動（`command`）
- `fetch_command`
  - 事前条件: `argv` は 1 要素以上
  - 副作用: 子プロセスを 1 つ起動する。シェルを通さないため、宣言の文字列が展開されることは無い

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `fetch_schema` | 記述を取れない | ネットワーク・相手の障害 | `httpx` の例外と状態コード | 呼び出し元へ返す | `infra_wait` で 75 | 再実行で直りうる（毎日のジョブは次の日に再び打つ） |
| `fetch_schema` | ポインタの先が無い | 相手の記述の形が変わった | 引いた値の有無 | 違反にする（食い違い 1 件） | `samples_mismatch` で 1。`items` に見本のパスとポインタ | 宣言を直して打ち直す |
| `fetch_command` | コマンドが無い・0 以外で終わる | 相手の CLI が入っていない | 終了コード | 呼び出し元へ返す | `precondition` で 3 | CLI を入れてから打ち直す |
| `compare` | 見本が記述に合わない | 相手の API の形が変わった、または見本が古い | `jsonschema` の検証 | 違反にする | `samples_mismatch` で 1。`items` に食い違ったキーと型 | 見本を録り直して打ち直す |
| `verify` | 呼び出しの合計が上限を超える | 宣言を増やしすぎた | 宣言を読んだ後の合計 | 止める（呼ばない） | `precondition` で 3。`summary` に相手ごとの合計と上限 | 宣言を減らすか上限を上げる |
| `verify` | 見本のファイルが無い | 宣言とファイルの食い違い | `Path.is_file()` | 違反にする | `samples_mismatch` で 1 | 見本を置いて打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `verify` | `peer` を渡した | その相手の宣言だけを当てる |
| `verify` | 宣言が 0 件 | 食い違い 0 件で `ok` |
| `verify` | 1 件が `infra_wait` で落ちた | 残りの見本は当て続け、最後に `infra_wait` を返す（1 件の障害で全部を止めない） |
| `compare`（`keys`） | キーの集合が同じで型も同じ | 食い違い 0 件（値は見ない） |
| `compare`（`exact`） | 値まで同じ | 食い違い 0 件 |

- 繰り返しの終わる条件: 宣言の件数だけ回して終わる（打ち直しの繰り返しは持たない。失敗は次の日のジョブに任せる）

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VerifyResult` | 型 | `src/ndf/domains/samples/verify.py` | 食い違いの並び・当てた数・相手ごとの呼び出しの数 |
| `Mismatch` | 型 | `src/ndf/domains/samples/verify.py` | 見本のパス・相手・食い違いの説明 |
| 記述の取得の上限 | 設定値 | `contracts/limits/samples.fetch.json` | 秒 60・打ち直し 2 回（分割した `limits` の 1 項目として足す） |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `main` | 結果 JSON（`items` に食い違い、`metrics` に `samples`・`calls`） | 標準出力の最後の 1 行 | 取得した記述の本文、相手の応答の値（見本のパスと食い違ったキーだけを出す） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `fetch_schema` | `src/ndf/adapters/http.py`（`httpx` の呼び出しと打ち直し） | `git grep -n 'httpx' -- plugins/ndf/src` | 使う（打ち直しの扱いを二重に持たない） |
| `compare`（`schema`） | `src/ndf/contracts/__init__.py` の `schema_errors`（116 行） | 同ファイルの読み | 使う（`jsonschema` の検証器の作り方と誤りの整形を再利用する） |
| `main` | `src/ndf/contracts/result.py` の `emit` | `git grep -n 'def emit' -- plugins/ndf/src` | 使う（結果 JSON の形とテストの差し替え（`conftest.py` の `_checked_emit`）に乗る） |
| `fetch_command` | `scripts/plugin-deps.py` の `_help_outputs`（1639 行） | `grep -n '_help_outputs' scripts/plugin-deps.py` | 使えない。`scripts/` は配る対象の外で、11 系のコードから import しない。コマンドの実行は `subprocess.run` を直に呼ぶ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `verify` | 偽の取得で、見本と同じ形なら食い違い 0 件 | 宣言 0 件で `ok`。`peer` の絞り込みで 1 件だけ当てる | 上の異常系の 6 行それぞれ |
| `compare` | `keys` で値が違ってもキーと型が同じなら 0 件 | 空の辞書どうし | キーが 1 つ増えた応答で、そのキーが `items` に出る |
| `fetch_command` | 標準出力を返す | 空の出力 | コマンドが無い・0 以外で終わる |
| `main` | 結果 JSON の形がサブコマンドのスキーマに合う | 食い違い 0 件のときの `metrics` | `infra_wait` のとき 75 |

## 依存の用意

満たす決定: 決定 5、受け入れ条件 5

### 関数の仕様

`plugins/ndf/bin/ndf` はシェルスクリプトで、関数は既にある `stop_deps_missing`（48-58 行）だけを使う。足すのは 98 行の `exec` の前の 3 行である。

| 箇所 | 種類 | 変える中身 |
| --- | --- | --- |
| `bin/ndf` の 98 行の直前 | 変更 | `"$uv_bin" sync --frozen --no-dev --quiet --project "$root"` を打ち、0 以外なら `stop_deps_missing "依存を用意できない（ネットワークを確かめる）: <ログの最終行>"` を呼ぶ |

- 事前条件: `uv_bin` が決まっている（uv を探すか入れる処理の後）
- 事後条件: `exec` へ進むのは同期が成功したときだけ。成功したときのシグナルの伝わり方は今までと変わらない（`exec` のまま）
- 副作用: `.venv` の作成・更新と、ログのファイル（既にある `$log` を使う）

### 異常系の処理

| 箇所 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| 同期 | 依存を取れない | ネットワークが無い・相手の障害 | 終了コード | 止める（`stop_deps_missing`） | 結果 JSON 1 行と `deps_missing`（69） | 再実行で直りうる |
| 同期 | lock と宣言が食い違う | lock の更新漏れ | 終了コード | 同上。`cause.detail` にログの最終行 | 同上 | `uv lock` を打ってから再実行 |

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `bin/ndf` | 同期が成功すれば今までどおりサブコマンドが動く | 同期済みの状態で 2 回続けて打っても同じ結果（冪等） | 偽の `uv`（`sync` で 1 を返す）を `PATH` の先頭に置くと、結果 JSON 1 行と 69 |

## 検査の規則を足す手順

`ndf check` の登録は `run.py` の `check()` の呼び出しの並びである（設計文書の「今の姿」）。4 つの規則はこの並びへ次の順で足す。順序は指摘の並び（`_order`）に影響しない。

```python
found += contracts_split.check(scope, rules) + toolchain.check(scope, rules)
found += budget.check(scope, rules) + experimental.check(scope, rules)
```

`rules.json` へ足すキーは `contracts_split`・`toolchain`・`budget`・`experimental` の 4 つで、それぞれ `summary` を持つ。`model.py` の `load_rules` が見る必須のキーの一覧（130 行）へ、この 4 つを足す。

`Scope` にはリポジトリの設定（`.ndf/project.json`）を読んだ値（`settings`）と、プラグインの外のファイルを引く `repo_files(glob)` を足す。4 つの規則のうち `toolchain` と `budget` だけがこれを使い、ほかの規則は今までどおりプラグインの下だけを見る。
