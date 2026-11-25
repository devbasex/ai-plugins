# #4516 の詳細設計: 解析の記録を追跡しないファイルへ分ける

設計文書 [issue-4516-design.md](./issue-4516-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。新設する関数 4（`measured.split`・`measured.strip_legacy`・`measured.load`・`measured.carry_walls`）とシグネチャを変える関数 3（`write.merge_project`・`write.Inputs`・`write._replace_ours`）があるため、別ファイルにした。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/project/
│   ├── measured.py *        解析の記録の形・取り分け・読み込み・実行時間の引き継ぎ
│   ├── write.py             設定の値を決め、書くファイルの計画を作る
│   ├── check.py             新しさの判定・設定の読み込み
│   └── cli.py               書き出しの入力に前の解析の記録を足す
├── contracts/
│   ├── domains/locations.json      場所 measured を .ndf/measured へ
│   ├── domains/ndf-files.json      行 project-measured のキー・行 project-measured-ignore を足す
│   └── schemas/
│       ├── ndf-settings/project.schema.json      analysis・languages・ci・test_duration を直す
│       └── internal/project-measured.schema.json *
├── skills/development-workflow/
│   ├── schemas/project.schema.json               契約のスキーマの写し
│   └── references/project-analysis.md            書き出しの規則・.ndf/ の追跡
├── docs/contracts/ndf-settings.md・files.md       生成物（ndf.checks.spec_render）
└── tests/domains/project/
    ├── test_project_cli.py
    └── test_project_measured.py *
```

依存の向き: `cli` → `write` → `measured` → (`check`・`ndf.contracts`・`ndf.domains.records`)、`cli` → `check` → `measured`。`measured` は `write` を読まない（`write.File` を返さず、パスと中身を返す）。`check` は `measured.load` と `measured.strip_legacy` を読み、`measured` は `check` の定数（`ANALYZER`・`SCHEMA`）を読まない。設定のスキーマのパスは `ndf.contracts.schema_path` から各モジュールが引くので、輪は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/project/measured.py` | 新設 | 解析の記録の形を持ち、設定の値から計測値を取り分ける | `ndf.contracts`・`ndf.domains.records.files`・`ndf.domains.records.jsonfile`・`items` | `measured` | `split`・`strip_legacy`・`load`・`carry_walls`（新設）。定数 `VERSION`・`IGNORE_TEXT`・`LEGACY_ANALYSIS_KEYS`・`SCHEMA` |
| `plugins/ndf/src/ndf/domains/project/write.py` | 変更 | 設定の値を決め、書くファイルの計画を作る | `measured`・`check` | `write` | `merge_item`・`merge_project`・`_replace_ours`・`_declare_ci_tests`・`plan`・`Inputs`（変更）。`_carry_walls`・`_same_but_time`（削除） |
| `plugins/ndf/src/ndf/domains/project/check.py` | 変更 | 新しさを解析の記録から判定する | `measured` | `check` | `load`・`judge`（変更） |
| `plugins/ndf/src/ndf/domains/project/cli.py` | 変更 | 書き出しの入力に前の解析の記録を足す | `measured`・`write`・`check` | `cli` | `_inputs`（変更） |
| `plugins/ndf/contracts/schemas/internal/project-measured.schema.json` | 新設 | 解析の記録の形 | 無し | — | — |
| `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` | 変更 | 設定の形から計測値と解析の記録を外す | 無し | — | — |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | 上の写し（1 バイトも違わない） | 無し | — | — |
| `plugins/ndf/contracts/domains/locations.json` | 変更 | 場所 `measured` を `{"root": "repo", "path": ".ndf/measured", "tracked": false}` へ | 無し | — | — |
| `plugins/ndf/contracts/domains/ndf-files.json` | 変更 | 行 `project-measured` の `path` を `project.json`・`keys` を `analyzer`・`at`・`inputs`・`branches`・`file_counts`・`wall_seconds` へ。行 `project-measured-ignore`（`path` は `.gitignore`・`kind` は `measured`・`ends` は `kept`）を足す。summary の「状態の保存先へ書く」を「追跡しない保存先へ書く」へ | 無し | — | — |
| `plugins/ndf/docs/contracts/ndf-settings.md`・`files.md` | 変更 | 生成し直す | `spec_render` | — | — |
| `plugins/ndf/skills/development-workflow/references/project-analysis.md` | 変更 | 「書き出しの規則」の指紋と実行時間の行、「`.ndf/` の追跡」の表に解析の記録の行を足す | 無し | — | — |
| `plugins/ndf/tests/domains/project/test_project_measured.py` | 新設 | `measured` の単体テスト | `measured` | — | — |
| `plugins/ndf/tests/domains/project/test_project_cli.py` | 変更 | 書き出しと判定の振る舞いのテストを新しい置き場へ | `cli`・`write`・`check` | — | `test_merge_item_carries_ci_walls` を `test_project_measured.py` の引き継ぎのテストへ移す |

## 解析の記録（measured.py）

満たす決定: 決定 1・3・4・6、受け入れ条件 2・3・8・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `split` | `measured.py` | 新設 | `split(key: str, value: Any) -> tuple[Any, dict[str, Any]]` | 設定の項目の値から計測値を取り除いた値と、取り除いた計測値（`{"file_counts": …}` か `{"wall_seconds": …}` か `{}`）を返す |
| `strip_legacy` | `measured.py` | 新設 | `strip_legacy(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]` | 設定から旧い形のキー（`analysis` の `written` 以外・`test_duration`・`split` が取り除く計測値）を取り除いた写しと、見つけたキーの名前の並びを返す |
| `load` | `measured.py` | 新設 | `load(main: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]` | メインディレクトリの解析の記録を読み、(記録, None) か (None, 読めない理由) を返す |
| `carry_walls` | `measured.py` | 新設 | `carry_walls(walls: dict[str, float], paths: list[str], previous: Optional[dict[str, Any]]) -> tuple[dict[str, float], list[dict[str, Any]]]` | 新しい測定で実行時間の無いワークフローに、前の記録の同じパスの実行時間を引き継ぐ |

- `split`
  - 事前条件: `value` は項目の新しい値か前の値（不明 `{"unknown": …}` を含む）
  - 事後条件: `key` が `languages` なら、配列の各行から `files` を除き、`{"file_counts": {名前: 数}}` を返す。`ci` なら `workflows` の各行から `wall_seconds` を除き、`{"wall_seconds": {パス: 秒}}` を返す。ほかのキー・不明・形の違う値は `(value, {})` をそのまま返す（取り除く物が無い）。入力を書き換えない
  - 副作用: 無し
- `strip_legacy`
  - 事後条件: 返した写しには `analysis` の `written` 以外のキー・`test_duration`・`languages[].files`・`ci.workflows[].wall_seconds` が無い。見つけたキーの名前は `analysis.at`・`test_duration`・`languages[].files`・`ci.workflows[].wall_seconds` の形で、見つけた順に並ぶ。入力を書き換えない
  - 副作用: 無し
- `load`
  - 事後条件: 記録が在り、JSON の object で、`SCHEMA`（`internal/project-measured`）に合えば (記録, None)
  - 副作用: ファイルを 1 つ読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load` | 記録が無い | 新しく clone した・消した | `Path.is_file` | 呼び出し元へ返す（`None, "解析の記録が無い"`） | 判定の理由「解析の記録が無い」、`check` の終了コード 3 | 書き出しが作る。再実行してよい |
| `load` | 読めない | JSON でない・object でない | `jsonfile.read_object` の `JsonFileError` | 呼び出し元へ返す（`None, "解析の記録が読めない: <誤り>"`） | 同上 | 書き出しが置き換える |
| `load` | 形が違う | 人が書き換えた・旧い NDF が書いた | `contracts.schema_errors` | 呼び出し元へ返す（`None, "解析の記録が形に合わない: <誤り 3 件まで>"`） | 同上 | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `carry_walls` | 新しい測定にそのパスの実行時間がある | 新しい値を使う |
| `carry_walls` | 無く、前の記録の `wall_seconds` にそのパスがある | 前の値を使い、行 `{"kind": "unknown", "key": "ci#<パス>", "reason": "新しい解析に壁時計が無い", "kept_previous": true}` を返す |
| `carry_walls` | どちらにも無い | 値を持たない（行も返さない） |
| `carry_walls` | 前の記録にあって `paths` に無いパス | 捨てる（消えたワークフロー） |

`carry_walls` は設定の `ci` の持ち主（人の値か解析の値か）を見ない。今の `_carry_walls` は解析の値の `ci` のときだけ引き継ぐが、実行時間はもう設定の値ではないため、持ち主に依らず引き継ぐ。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VERSION` | 定数 | `measured.py` | `1`（解析の記録の形のバージョン） |
| `IGNORE_TEXT` | 定数 | `measured.py` | `"# NDF の解析が書く記録。コミットしない\n*\n"` |
| `LEGACY_ANALYSIS_KEYS` | 定数 | `measured.py` | 旧い `analysis` のキーで、取り除くもの: `("analyzer", "at", "inputs", "branches")` |
| `SCHEMA` | 定数 | `measured.py` | `contracts.schema_path("internal/project-measured")` |

保存先のパスは定数に持たず、`files.path(main, "project-measured")`・`files.path(main, "project-measured-ignore")` で表から引く（`files.relpath` が場所 `measured` の `.ndf/measured` と行の `path` をつなぐ）。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load` | `ndf.domains.records.jsonfile.read_object`・`ndf.contracts.schema_errors` | `grep -n "def read_object\|def schema_errors" -r plugins/ndf/src/ndf` | 使う |
| `carry_walls` | `write._carry_walls` | 読んだ | 置き換える（設定の `ci` の行でなく、パス → 秒の表で引き継ぐ形に変わる） |
| `split`・`strip_legacy` | `items.obj`・`items.seq` | 読んだ | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `split` | `languages` の `files` と `ci` の `wall_seconds` を取り分ける | `files` の無い行・`wall_seconds` の無い行・空の配列・不明の値・ほかのキー | 配列でない `languages`（そのまま返す） |
| `strip_legacy` | 旧い形の設定から 4 種類のキーを取り除き名前を返す | 新しい形の設定（空の並びを返し、写しが入力と等しい） | `analysis` が object でない（そのまま残す。形の検査が拾う） |
| `load` | 形に合う記録を返す | — | 無い・JSON でない・形が違う（理由を返す） |
| `carry_walls` | 処理の手順の 4 行 | `previous` が None | — |

## 書き出し（write.py）

満たす決定: 決定 2・3・4、受け入れ条件 1・2・4・7・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Inputs` | `write.py` | 変更 | `Inputs(root, main, existing, measure, answers, record: Optional[dict[str, Any]])` | 書き出しの入力に、前の解析の記録（読めなければ None）を足す |
| `merge_item` | `write.py` | 変更（責務） | 変えない | 持ち主の見分けは渡された前の値（取り除く前）で行い、前の値を残すとき・新しい値と比べるときは `measured.split` で計測値を取り除いた前の値を使う |
| `merge_project` | `write.py` | 変更 | `merge_project(existing, measure, answers, written_before) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]` | 新しい設定・出力の行・取り分けた計測値を返す。設定の `analysis` は `{"written": …}` だけにし、`test_duration` を取り除く |
| `_replace_ours` | `write.py` | 変更 | `_replace_ours(key, cur, new, origin) -> tuple[Any, list[dict[str, Any]]]`（引数は変えず、`ci` の引き継ぎを外す） | 前の値のままの項目を置き換える。新しい値が不明なら前の値（計測値を取り除いた値）を残す |
| `_declare_ci_tests` | `write.py` | 変更（責務） | 変えない | 指紋を `doc["analysis"]["written"]` に書く（`analysis` が `written` だけになっても同じ場所） |
| `plan` | `write.py` | 変更（責務） | 変えない | 書くファイルを、登録（無いときだけ）→ `.ndf/project.json`（今の中身と違うときだけ）→ 解析の記録（時刻を除いて前と違うときだけ）の順で返す |

- `merge_project`
  - 事後条件: 返す設定に旧い形のキーが無い（I1）。`written` の指紋は計測値を取り除いた値から作る（I2）。返す計測値は、`languages` と `ci` の新しい値を `measured.split` で取り分けたもの（項目の持ち主に依らない。人の値の項目でも測った値の計測値を記録へ書く）
- `plan`
  - 事後条件: 返す `Plan.files` のうち追跡するファイルは `.ndf/project.json` だけ（受け入れ条件 4）。解析の記録は `{"version", "analyzer", "at", "inputs", "branches", "file_counts"?, "wall_seconds"?}` で、`wall_seconds` は `measured.carry_walls` を通した後の値。登録の `File` は `files.path(main, "project-measured-ignore")` が無いときだけ入る
  - 副作用: 無し（書くのは `cli._write`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan` | 書き出す設定が形に合わない | 解析器の誤り | `contracts.schema_errors(doc, check.SCHEMA)` | 止める（`WriteError`） | `書き出す設定が形に合わない（解析器の誤り）`、終了コード 1（今と同じ） | 何も書かない |
| `plan` | 書き出す解析の記録が形に合わない | 解析器の誤り | `contracts.schema_errors(record, measured.SCHEMA)` | 止める（`WriteError`） | `書き出す解析の記録が形に合わない（解析器の誤り）`、終了コード 1 | 何も書かない |
| `cli._write` | 途中のファイルを書けない | 権限・ディスク | `signals.replace_text` の `OSError` | 止める | `設定を書けない: <誤り>`、終了コード 1（今と同じ） | 書けた分は残る。次の判定が「古い」と読む（設計文書の「処理の流れ」の表） |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `merge_item` | 前の値の指紋が前の `written` と一致（取り除く前の値で照らす） | 解析の値。新しい値（計測値を取り除いた値）で置き換える |
| `merge_item` | 一致しない、かつ新しい値が不明か、取り除いた前の値と同じ | 人の値。取り除いた前の値を残し、行 `kept` |
| `merge_item` | 一致しない、かつ違う | 人の値。取り除いた前の値を残し、行 `mismatch`（`declared` は取り除いた前の値） |

## 判定と読み込み（check.py・cli.py）

満たす決定: 決定 4・5、受け入れ条件 5・6・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load` | `check.py` | 変更（責務） | 変えない | 設定を読み、`measured.strip_legacy` で取り除いた写しでスキーマを確かめ、取り除く前の値を返す（旧い形を「壊れている」にしない） |
| `judge` | `check.py` | 変更（責務） | 変えない | 旧い形のキーがあれば「古い」。次に `measured.load` の記録が無ければ「古い」。あれば記録の `analyzer`・`inputs`・`branches` で今と同じ判定をする |
| `_inputs` | `cli.py` | 変更（責務） | 変えない | `measured.load(main)` の記録を `Inputs.record` に渡す（読めなければ None） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `judge` | 旧い形のキーがある | 移行の前の設定 | `measured.strip_legacy` の並びが空でない | 「古い」 | 理由 `旧い形の解析の記録が設定にある: <キー>`、終了コード 3 | 手順 0 が解析を打ち直す |
| `judge` | 解析の記録が無い・読めない | 決定 5 | `measured.load` | 「古い」 | 理由に `load` の理由、終了コード 3 | 同上 |
| `load` | 取り除いた後も形に合わない | 人が設定を壊した | `contracts.schema_errors` | 呼び出し元へ返す（`JsonFileError`。今と同じ） | `check` は終了コード 2、`write` は 2（今と同じ） | 利用者が直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge` | 記録の入力と今の木が同じなら「新しい」、入力かブランチが変われば「古い」 | worktree から打つとメインディレクトリの記録を読む | 記録が無い・壊れている・旧い形の設定（どれも終了コード 3） |
| `load` | 新しい形の設定を返す | 旧い形の設定を、取り除かずに返す | 取り除いても形に合わない（終了コード 2） |
| `plan`（`cli.main(["write", …])` から） | 2 回目の書き出しで `.ndf/project.json` を書かない | 移行の 1 回目・記録の無い clone | — |
