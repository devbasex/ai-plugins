# #4516 の詳細設計: 解析の記録を追跡しないファイルへ分ける

設計文書 [issue-4516-design.md](./issue-4516-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。新設する関数は 4 つ（`measured.split`・`measured.strip_legacy`・`measured.load`・`measured.carry_walls`）、シグネチャを変えるものは 3 つ（`write.merge_project`・`write.Inputs`・`write.File`）ある。そのため別ファイルにした。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.ndf/project.json            このリポジトリの追跡している設定（スキーマの変更と同じコミットで移す）
plugins/ndf/
├── src/ndf/domains/project/
│   ├── measured.py *        解析の記録の形・計測値の取り分け・旧い形のキーの取り除き・読み込み・実行時間の引き継ぎ
│   ├── measure_ci.py        ワークフローの行の jobs を定義の数だけにし、実行の数を run_jobs で返す
│   ├── write.py             設定の値を決め、解析の記録を組み、書くファイルの計画を作る
│   ├── check.py             新しさの判定・設定の読み込み
│   └── cli.py               書き出しの入力に前の解析の記録を足す・無視の確認・記録の要約の表示
├── contracts/
│   ├── domains/locations.json      場所 measured を .ndf/measured へ
│   ├── domains/ndf-files.json      行 project-measured のキー・行 project-measured-ignore を足す
│   └── schemas/
│       ├── ndf-settings/project.schema.json      analysis・languages・ci・test_duration を直す
│       └── internal/project-measured.schema.json *
├── skills/development-workflow/
│   ├── schemas/project.schema.json               契約のスキーマのコピー
│   └── references/project-analysis.md            書き出しの規則・.ndf/ の追跡
├── docs/contracts/ndf-settings.md・files.md       生成物（ndf.checks.spec_render）
└── tests/domains/project/
    ├── test_project_cli.py
    ├── test_project_measure.py
    └── test_project_measured.py *
```

依存の向き: `cli` → `write` → `check` → `measured`、`write` → `measured`、`measured` → (`ndf.contracts`・`ndf.domains.records`・`items`)。`measured` は `write` も `check` も読まない（`check` の定数 `ANALYZER`・`SCHEMA` を使わず、スキーマのパスは `ndf.contracts.schema_path` から自分で引く）。そのため依存に輪は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/project/measured.py` | 新設 | 解析の記録の形を持ち、設定の値から計測値を取り分ける | `ndf.contracts`・`ndf.domains.records.files`・`ndf.domains.records.jsonfile`・`items` | `measured` | `split`・`strip_legacy`・`load`・`carry_walls`（新設）。定数 `VERSION`・`IGNORE_TEXT`・`SCHEMA` |
| `plugins/ndf/src/ndf/domains/project/measure_ci.py` | 変更 | ワークフローの行のジョブ数を、定義の数（`jobs`）と実行の数（`run_jobs`）に分ける | 変えない | `measure_ci` | `_workflow_entry`（変更） |
| `plugins/ndf/src/ndf/domains/project/write.py` | 変更 | 設定の値を決め、解析の記録を組み、書くファイルの計画を作る | `measured`・`check` | `write` | `merge_item`・`merge_project`・`_replace_ours`・`_declare_ci_tests`・`plan`・`Inputs`・`File`（変更）。`_carry_walls`・`_same_but_time`（削除） |
| `plugins/ndf/src/ndf/domains/project/check.py` | 変更 | 新しさを解析の記録から判定する | `measured` | `check` | `load`・`judge`（変更） |
| `plugins/ndf/src/ndf/domains/project/cli.py` | 変更 | 書き出しの入力に前の解析の記録を足す。記録のパスが git に無視されることを確かめる。記録を要約で表示する | `measured`・`write`・`check` | `cli` | `_inputs`・`_write`・`_print_write`（変更） |
| `.ndf/project.json` | 変更 | このリポジトリの追跡している設定を新しい形式へ移す（設計文書の決定 4） | 無し | — | — |
| `plugins/ndf/contracts/schemas/internal/project-measured.schema.json` | 新設 | 解析の記録の形 | 無し | — | — |
| `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` | 変更 | 設定の形から計測値と解析の記録を外す | 無し | — | — |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | 上のスキーマのバイト単位で同一のコピー | 無し | — | — |
| `plugins/ndf/contracts/domains/locations.json` | 変更 | 場所 `measured` を `{"root": "repo", "path": ".ndf/measured", "tracked": false}` へ | 無し | — | — |
| `plugins/ndf/contracts/domains/ndf-files.json` | 変更 | 行 `project-measured` の `path` を `project.json`・`keys` を `analyzer`・`at`・`inputs`・`branches`・`file_counts`・`wall_seconds`・`run_jobs`・`pending` へ。行 `project-measured-ignore`（`path` は `.gitignore`・`kind` は `measured`・`ends` は `kept`）を足す。summary の「状態の保存先へ書く」を「追跡しない保存先へ書く」へ | 無し | — | — |
| `plugins/ndf/docs/contracts/ndf-settings.md`・`files.md` | 変更 | 生成し直す | `spec_render` | — | — |
| `plugins/ndf/skills/development-workflow/references/project-analysis.md` | 変更 | 「書き出しの規則」の指紋と実行時間の行、「`.ndf/` の追跡」の表に解析の記録の行を足す | 無し | — | — |
| `plugins/ndf/tests/domains/project/test_project_measured.py` | 新設 | `measured` の単体テスト | `measured` | — | — |
| `plugins/ndf/tests/domains/project/test_project_measure.py` | 変更 | `_workflow_entry` が定義の数を `jobs`、実行の数を `run_jobs` に書くことを確かめる | `measure_ci` | — | — |
| `plugins/ndf/tests/domains/project/test_project_cli.py` | 変更 | 書き出しと判定のテストの期待を、解析の記録が `.ndf/measured/project.json` にある形へ直す | `cli`・`write`・`check` | — | `test_merge_item_carries_ci_walls` は消し、同じ振る舞いを `test_project_measured.py` の `carry_walls` のテストで確かめる |

## 解析の記録（measured.py）

満たす決定: 決定 1・3・4・6、受け入れ条件 2・3・4・11・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `split` | `measured.py` | 新設 | `split(key: str, value: Any) -> tuple[Any, dict[str, Any]]` | 設定の項目の値から計測値を取り除いた値と、取り除いた計測値（`{"file_counts": …}` か `{"wall_seconds": …, "run_jobs": …}` か `{}`）を返す |
| `strip_legacy` | `measured.py` | 新設 | `strip_legacy(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]` | 設定から旧い形のキー（`analysis` の `written` 以外のすべて・`test_duration`・`split` が取り除く計測値）を取り除いたコピーと、見つけたキーの名前の並びを返す |
| `load` | `measured.py` | 新設 | `load(main: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]` | メインディレクトリの解析の記録を読み、(記録, None) か (None, 読めない理由) を返す |
| `carry_walls` | `measured.py` | 新設 | `carry_walls(walls: dict[str, float], paths: Optional[list[str]], previous: Optional[dict[str, Any]], legacy: dict[str, float]) -> tuple[dict[str, float], list[dict[str, Any]]]` | 新しい測定で実行時間の無いワークフローに、前の実行時間を引き継ぐ。引き継ぎ元は、前の記録の `wall_seconds`、そこに無ければ移行前の設定の実行時間 `legacy` の順に見る |

- `split`
  - 事前条件: `value` は項目の新しい値か前の値（不明 `{"unknown": …}` を含む）
  - 事後条件: `key` が `languages` なら、配列の各行から `files` を除き、`{"file_counts": {名前: 数}}` を返す。`ci` なら `workflows` の各行から `wall_seconds` と `run_jobs` を除き、`{"wall_seconds": {パス: 秒}, "run_jobs": {パス: 数}}` を返す。ほかのキー・不明・形の違う値は `(value, {})` をそのまま返す（取り除く物が無い）。入力を書き換えない
  - 数でない計測値（旧スキーマが認める `wall_seconds: null` など）は、キーを値から取り除くが、返す表には入れない（未計測として扱う）。そのため、返す表は新しい記録の形（値は数だけ）にそのまま合う
  - 副作用: 無し
- `carry_walls`
  - `paths` は、新しい測定の結果の `ci.workflows[].path` の並びである（設定の `ci` ではない。設定の `ci` を人が直していても、測定の結果のワークフローで引き継ぐ）。新しい測定の `ci` が不明（`{"unknown": …}`。GitHub の API の障害・時間切れ）なら `None` を渡す。測定に成功してワークフローが 0 本なら空の並びを渡す。この 2 つを区別する
  - `legacy` は、移行前の設定の `ci` の値を `split("ci", …)` に通して得た `wall_seconds` の表である（`null` は `split` が除くので入らない）。移行前の設定でなければ空の表になる
- `strip_legacy`
  - 事後条件: 返したコピーには、`analysis` の `written` 以外のすべてのキー・`test_duration`・`languages[].files`・`ci.workflows[].wall_seconds` が無い。見つけたキーの名前は、取り除いたキーごとに 1 つ並ぶ。`analysis` のキーは 1 つずつ（例 `analysis.analyzer`・`analysis.at`・`analysis.inputs`・`analysis.branches`）、`test_duration` は 1 つ、`languages[].files` と `ci.workflows[].wall_seconds` は行がいくつあっても 1 つずつである。並びは設定のキーの順である。入力を書き換えない
  - 副作用: 無し
- `load`
  - 事後条件: 記録が在り、UTF-8 の JSON の object で、`SCHEMA`（`internal/project-measured`）に合えば (記録, None)。途中の印（`pending: true`）の付いた記録もそのまま返す（判定は `check.judge` が行い、前の実行時間は書き出しが引き継ぐ）
  - 副作用: ファイルを 1 つ読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load` | 記録が無い | 新しく clone した・消した | `Path.is_file` | 呼び出し元へ返す（`None, "解析の記録が無い"`） | 判定の理由「解析の記録が無い」、`check` の終了コード 3 | 書き出しが作る。再実行してよい |
| `load` | 読めない | JSON でない・object でない | `jsonfile.read_object` の `JsonFileError` | 呼び出し元へ返す（`None, "解析の記録が読めない: <誤り>"`） | 同上 | 書き出しが置き換える |
| `load` | 読めない | 不正な UTF-8 を含む | `jsonfile.read_object` が変換せずに送る `UnicodeDecodeError`（`jsonfile.read` は `OSError` と `JSONDecodeError` だけを `JsonFileError` に変える） | `load` が捕まえて呼び出し元へ返す（`None, "解析の記録が読めない: UTF-8 として読めない（位置 <n>）"`） | 同上 | 同上 |
| `load` | 形が違う | 人が書き換えた・旧い NDF が書いた | `contracts.schema_errors` | 呼び出し元へ返す（`None, "解析の記録が形に合わない: <誤り 3 件まで>"`） | 同上 | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `carry_walls` | `paths` が `None`（新しい測定の `ci` が不明） | 前の記録に `wall_seconds` があれば、その表をすべて残す。前の記録が無いか `wall_seconds` を持たなければ、`legacy` の表をすべて残す（パスごとには混ぜない）。行 `{"kind": "unknown", "key": "ci", "reason": "新しい解析に CI が無い", "kept_previous": true}` を 1 つ返す（今の `_replace_ours` が不明の `ci` で前の値を残すのと同じ扱い） |
| `carry_walls` | 新しい測定にそのパスの実行時間がある | 新しい値を使う |
| `carry_walls` | 無く、前の記録の `wall_seconds` にそのパスがある | 前の記録の値を使い、行 `{"kind": "unknown", "key": "ci#<パス>", "reason": "新しい解析に壁時計が無い", "kept_previous": true}` を返す |
| `carry_walls` | 前の記録にも無く、`legacy` にそのパスがある（移行の 1 回目） | 移行前の設定の値を使い、同じ行を返す |
| `carry_walls` | どれにも無い | 値を持たない（行も返さない） |
| `carry_walls` | 前の記録か `legacy` にあって `paths` に無いパス（`paths` が `None` でないとき） | 捨てる（測定に成功し、ワークフローが消えたと確かめられた） |

`carry_walls` は設定の `ci` の持ち主（人の値か解析の値か）を見ない。今の `_carry_walls` は解析の値の `ci` のときだけ引き継ぐが、実行時間はもう設定の値ではないため、持ち主に依らず引き継ぐ。`run_jobs` は引き継がない（新しい測定で実行を選べなかったワークフローは、記録に `run_jobs` を持たない）。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VERSION` | 定数 | `measured.py` | `1`（解析の記録の形のバージョン） |
| `IGNORE_TEXT` | 定数 | `measured.py` | `"# NDF の解析が書く記録。コミットしない\n*\n"` |
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
| `split` | `languages` の `files` と `ci` の `wall_seconds`・`run_jobs` を取り分ける | `files` の無い行・`wall_seconds` の無い行・`wall_seconds: null` の行（キーは消え、表に入らない）・空の配列・不明の値・ほかのキー | 配列でない `languages`（そのまま返す） |
| `strip_legacy` | 旧い形の設定から 4 種類のキーを取り除き名前を返す | 新しい形の設定（空の並びを返し、コピーが入力と等しい） | `analysis` が object でない（そのまま残す。形の検査が拾う） |
| `load` | 形に合う記録・途中の印の付いた記録を返す | — | 無い・JSON でない・不正な UTF-8・形が違う（理由を返す） |
| `carry_walls` | 処理の手順の 6 行 | `previous` が None で `legacy` から引き継ぐ・`paths` が None と空の並びで結果が違う | — |

## 書き出し（write.py）

満たす決定: 決定 2・3・4、受け入れ条件 1・2・3・4・5・9・11・12・15・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Inputs` | `write.py` | 変更 | 前: `Inputs(root, main, existing, measure, answers)`。後: `Inputs(root, main, existing, measure, answers, record: Optional[dict[str, Any]])` | 書き出しの入力に、前の解析の記録（読めなければ None）を足す |
| `merge_item` | `write.py` | 変更（責務） | 変えない | 持ち主の見分けは渡された前の値（取り除く前）で行い、前の値を残すとき・新しい値と比べるときは `measured.split` で計測値を取り除いた前の値を使う |
| `merge_project` | `write.py` | 変更 | 前: `merge_project(existing, measure, answers, analysis, written_before) -> tuple[dict, list]`。後: `merge_project(existing, measure, answers, written_before) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]` | 引数 `analysis`（解析の記録）を外し、返りに取り分けた計測値を足す。設定の `analysis` は `{"written": …}` だけにし、`test_duration` を取り除く |
| `_replace_ours` | `write.py` | 変更（責務） | 変えない | 前の値のままの項目を置き換える。新しい値が不明なら前の値（計測値を取り除いた値）を残す。`ci` の実行時間の引き継ぎ（今の `_carry_walls` の呼び出し）を外す |
| `_declare_ci_tests` | `write.py` | 変更（責務） | 変えない | 指紋を `doc["analysis"]["written"]` に書く（`analysis` が `written` だけになっても同じ場所） |
| `File` | `write.py` | 変更 | 前: `File(path, name, old, new)`。後: `File(path, name, old, new, summary: Optional[str] = None)` | `summary` があれば、表示は差分の代わりにこの 1 行を出す |
| `plan` | `write.py` | 変更（責務） | 変えない | 解析の記録を組み、書くファイルを `.ndf/measured/.gitignore`（無いときだけ）→ 途中の印を付けた前の記録 → `.ndf/project.json`（今の中身と違うときだけ）→ 解析の記録の順で返す。解析の記録は、時刻を除いて前と違うとき、`.ndf/project.json` を書くとき、前の記録に途中の印が残っているときのどれかで入る |
| `_workflow_entry` | `measure_ci.py` | 変更（責務） | 変えない | `jobs` に定義から数えたジョブ数（`static_jobs`）だけを書く。実行を選べたときは、その実行のジョブ数を `run_jobs` に書く（今は両者の大きい方を `jobs` に書く） |
| `_write` | `cli.py` | 変更（責務） | 変えない | `--dry-run` なら今と同じく何も書かず、`git check-ignore` も打たずに予定を表示する。実際に書くときで、`Plan.files` に `.ndf/measured/` の下のファイル（`.gitignore`・途中の印・記録のどれか。`.gitignore` だけでもよい）があるときだけ、次を行う。(1) `Plan.files` に `.gitignore` があれば、書く前にメインディレクトリで `git ls-files -- .ndf/measured` を 1 回打ち、出力が空でなければ何も書かずに止める。空なら `.gitignore` を書く。(2) `git check-ignore -q <記録のパス>` を 1 回打ち、0 以外なら残りを書かずに止める。(3) `Plan.files` の残りを順番に書く。`Plan.files` が空なら git を起動せず、終了コード 0 で終わる |
| `_print_write` | `cli.py` | 変更（責務） | 変えない | 動詞は今と同じく `dry_run` で選ぶ（`書く（--dry-run）` か `書いた`）。`File.summary` があるファイルは `<動詞>: <パス>（<summary>）` の 1 行だけを出し、差分を出さない。`.ndf/project.json` は今と同じく差分を出す |

- `merge_project`
  - 事前条件と順序: 項目ごとに、`resolve_item` が決めた新しい値を先に `measured.split` で計測値と設定の値に分け、設定の値だけを `merge_item` へ渡す。取り分けた計測値は返りの計測値に集める。そのため `merge_item` が比べる新しい値に計測値は残らず、人が直した項目で `mismatch` が毎回出ることは無い
  - 事後条件: 返す設定に旧い形のキーが無い（I1）。`written` の指紋は計測値を取り除いた値から作る（I2）。返す計測値は、`languages` と `ci` の新しい値を `measured.split` で取り分けたもの（人が直した項目か解析が書いた項目かに依らない。人が直した項目でも測った値の計測値を記録へ書く）
- `plan`
  - 事後条件: 返す `Plan.files` のうち追跡するファイルは `.ndf/project.json` だけ（受け入れ条件 5）。解析の記録は `write.plan` が組み、形は `{"version", "analyzer", "at", "inputs", "branches", "file_counts"?, "wall_seconds"?, "run_jobs"?}` で、`wall_seconds` は `measured.carry_walls` を通した後の値。`carry_walls` の `paths` には、新しい測定の `ci` が不明なら `None`、そうでなければ測定の結果の `ci.workflows[].path` の並びを渡す。`legacy` には、`Inputs.existing` の `ci` を `measured.split` に通した `wall_seconds` を渡す。`.gitignore` の `File` は `files.path(main, "project-measured-ignore")` が無いときだけ入り、中身は `measured.IGNORE_TEXT`
  - 最後の記録: 次のどれかに当たるとき、印の無い記録の `File` を最後に入れる。(1) 記録の中身が時刻を除いて前の記録と違う。(2) `.ndf/project.json` を書く（このとき途中の印も入るので、印を消すために必ず書く）。(3) `Inputs.record` に `pending: true` が残っている。どれにも当たらなければ記録を書かない
  - 途中の印: `Inputs.record` があり、`.ndf/project.json` か記録のどちらかを書くときだけ、`{**Inputs.record, "pending": true}` の `File` を `.ndf/project.json` の前に入れる。印を入れた計画には、必ず最後の記録が入る（上の (2) と (1)・(3) のどれかが当たるため）。最後の記録の `File` は `pending` を持たない
  - 表示の要約: `.gitignore`・途中の印・記録の `File` は `summary` を持つ。記録は `解析の記録。入力 <N> 件・言語 <M> 件・ワークフロー <K> 件`、途中の印は `書き出しの途中の印`、`.gitignore` は `git から外す設定` とする。`.ndf/project.json` の `File` は `summary` を持たない
  - 副作用: 無し（書くのは `cli._write`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan` | 書き出す設定が形に合わない | 解析器の誤り | `contracts.schema_errors(doc, check.SCHEMA)` | 止める（`WriteError`） | `書き出す設定が形に合わない（解析器の誤り）`、終了コード 1（今と同じ） | 何も書かない |
| `plan` | 書き出す解析の記録が形に合わない | 解析器の誤り | `contracts.schema_errors(record, measured.SCHEMA)` | 止める（`WriteError`） | `書き出す解析の記録が形に合わない（解析器の誤り）`、終了コード 1 | 何も書かない |
| `cli._write` | `.ndf/measured/` の下に追跡されたファイルがある（`.gitignore` を作る前） | 利用者が追跡していた `.gitignore` を作業ツリーで消した・記録を追跡した（I4） | `git ls-files -- .ndf/measured` の出力が空でない | 止める。`.gitignore`・途中の印・`.ndf/project.json`・記録は書かない | `.ndf/measured/ の下のファイルが git に追跡されている: <パス>`、終了コード 1 | 利用者が追跡をやめるか、消した `.gitignore` を戻してから打ち直す |
| `cli._write` | 記録のパスが git に無視されない | 既存の `.ndf/measured/.gitignore` が空・独自のパターン・記録が追跡済み（I4） | `git check-ignore -q` の終了コードが 0 以外 | 止める。途中の印・`.ndf/project.json`・記録は書かない。既にあった `.gitignore` は変えない | `.ndf/measured/project.json が git に無視されない。.ndf/measured/.gitignore の中身か、記録が追跡されていないかを確かめる`、終了コード 1 | 利用者が除外の設定を直してから打ち直す |
| `cli._write` | 途中のファイルを書けない（`.ndf/measured/.gitignore` を書けないとき（I4）を含む） | 権限・ディスク | `signals.replace_text` の `OSError` | 止める。`.gitignore` か途中の印を書けなければ、後の `.ndf/project.json` と解析の記録は書かない | `設定を書けない: <誤り>`、終了コード 1（今と同じ） | 書けた分は残る。途中の印の後で止まれば印が残り、前の記録が無ければ記録が無いので、次の判定が「古い」と読む（設計文書の「処理の流れ」の表、I7） |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `merge_item` | 前の値（計測値を取り除く前）のハッシュが、前の `written` の指紋と一致する | 解析の値。新しい値（計測値を取り除いた値）で置き換える |
| `merge_item` | 一致しない、かつ新しい値が不明か、取り除いた前の値と同じ | 人の値。取り除いた前の値を残し、行 `kept` |
| `merge_item` | 一致しない、かつ違う | 人の値。取り除いた前の値を残し、行 `mismatch`（`declared` は取り除いた前の値） |

## 判定と読み込み（check.py・cli.py）

満たす決定: 決定 4・5、受け入れ条件 7・8・10・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load` | `check.py` | 変更（責務） | 変えない | 設定を読み、`measured.strip_legacy` で取り除いたコピーでスキーマを確かめ、取り除く前の値を返す（旧い形を「壊れている」にしない） |
| `judge` | `check.py` | 変更（責務） | 変えない | 旧い形のキーがあれば「古い」。次に `measured.load` の記録が無ければ「古い」。記録に途中の印（`pending: true`）があれば「古い」。どれでもなければ記録の `analyzer`・`inputs`・`branches` で今と同じ判定をする |
| `_inputs` | `cli.py` | 変更（責務） | 変えない | `measured.load(main)` の記録を `Inputs.record` に渡す（読めなければ None） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `judge` | 旧い形のキーがある | 移行の前の設定 | `measured.strip_legacy` の並びが空でない | 「古い」 | 理由 `旧い形の解析の記録が設定にある: <キー>`、終了コード 3 | 手順 0 が解析を打ち直す |
| `judge` | 解析の記録が無い・読めない | 決定 5 | `measured.load` | 「古い」 | 理由に `load` の理由、終了コード 3 | 同上 |
| `judge` | 前の書き出しが途中で止まった | 書き出しの途中の失敗（I7） | 記録の `pending` | 「古い」 | 理由 `前の書き出しが終わっていない`、終了コード 3 | 同上 |
| `load` | 取り除いた後も形に合わない | 人が設定を壊した | `contracts.schema_errors` | 呼び出し元へ返す（`JsonFileError`。今と同じ） | `check` は終了コード 2、`write` は 2（今と同じ） | 利用者が直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge` | 記録の入力のファイルのハッシュと今の HEAD が同じなら「新しい」、入力かブランチが変われば「古い」 | worktree から打つとメインディレクトリの記録を読む | 記録が無い・壊れている・不正な UTF-8・途中の印・旧い形の設定（どれも終了コード 3） |
| `load` | 新しい形の設定を返す | 旧い形の設定を、取り除かずに返す | 取り除いても形に合わない（終了コード 2） |
| `plan`（`cli.main(["write", …])` から） | 2 回目の書き出しで `.ndf/project.json` を書かない。記録を差分でなく要約の 1 行で表示する | 移行の 1 回目（`wall_seconds: null` を含む・前の記録が無く移行前の実行時間を引き継ぐ）・記録の無い clone・`ci` が不明 | 前の記録の入力が今と同じで、`.ndf/project.json` か記録の書き込みを失敗させる（次の判定が「古い」）・記録のパスが無視されない（終了コード 1 で既存のファイルが前のまま） |
| `plan`・`_write`（`cli.main(["write", …])` から） | 同じ測定の結果で回答だけを変えた書き出しの後、記録に途中の印が無い。印の残った記録から同じ内容で打ち直すと、印が消える | 記録の無い clone で `--dry-run` を打つと、`.ndf/measured/` の下に何も作られず、表示の動詞が `書く（--dry-run）` になる。書くものが無い書き出しでは git を呼ばず終了コード 0（記録が追跡済みでも）。書くファイルが `.gitignore` だけで記録が追跡されていないとき、`git ls-files` と `git check-ignore` を 1 回ずつ呼ぶ（呼び出しを差し替えて回数を数える）。記録が追跡済みなら `git ls-files` だけを呼ぶ | 追跡していた `.gitignore` を作業ツリーで消した状態で、`.gitignore` を作らずに終了コード 1 |
