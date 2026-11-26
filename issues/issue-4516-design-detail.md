# #4516 の詳細設計: 読み手の無い値の測定をやめ、解析の記録を追跡しないファイルへ分ける

設計文書 [issue-4516-design.md](./issue-4516-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。新設する関数は 5 つ（`analysis_record.strip_item`・`analysis_record.strip_legacy`・`analysis_record.load`・`analysis_record.written_digest`・`strategy_write.lock_file`）、置き場を移す関数は 1 つ（`write.value_digest` → `items.value_digest`）、シグネチャを変えるものは 5 つ（`write.merge_project`・`write.Inputs`・`write.File`・`check.repo_dirs`・`cli._inputs`）ある。そのため別ファイルにした。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除）:

```text
.ndf/project.json                  このリポジトリの追跡している設定（スキーマの変更と同じコミットで移す）
docs/specifications/
├── project-decl-ci-representative-run.md -   消した振る舞い（CI の実行時間を測る実行の選び方）だけを書いた確定仕様
└── README.md                                  上の行を消す
plugins/ndf/
├── src/ndf/domains/project/
│   ├── analysis_record.py *   解析の記録の形・旧い形のキーの取り除き・読み込み
│   ├── measure_ci.py          ci を必須のチェックだけにし、実行・ジョブ・成果物・テストの所要時間の取得を消す
│   ├── measure_repo.py        言語の行からファイル数を外し、名前の順に並べる
│   ├── write.py               設定の値を決め、解析の記録を組み、書くファイルの計画を作る
│   ├── check.py               新しさの判定・設定の読み込み・作業場所と git の共通ディレクトリの解決
│   ├── items.py               書いた値の指紋（value_digest）を write から移す
│   ├── strategy_write.py      解決済みの共通ディレクトリからロックのパスを作る関数を足す
│   └── cli.py                 測定の結果からテストの所要時間を外す・前の記録を入力に足す・ロック・無視の確認・記録の要約の表示
├── contracts/
│   ├── domains/locations.json      場所 measured を消し、analysis を足す
│   ├── domains/ndf-files.json      行 project-measured を消し、project-analysis・project-analysis-ignore を足す
│   ├── limits/branch-strategy.write-lock.json   解析の書き出しも同じロックを使うことを書く
│   └── schemas/
│       ├── ndf-settings/project.schema.json      analysis・languages・ci・test_duration を直す
│       └── internal/project-analysis.schema.json *
├── skills/development-workflow/
│   ├── schemas/project.schema.json               契約のスキーマのコピー
│   └── references/project-analysis.md            測るもの・書き出しの規則・.ndf/ の追跡
├── docs/contracts/ndf-settings.md・files.md       生成物（ndf.checks.spec_render）
└── tests/domains/project/
    ├── test_project_cli.py
    ├── test_project_measure.py
    └── test_analysis_record.py *
```

依存の向き: `cli` → `write` → `check` → `analysis_record`、`write` → `analysis_record`、`cli` → `strategy_write`（ロックのパスだけ）、`analysis_record` → (`ndf.contracts`・`ndf.domains.records`・`items`)、`write` → `items`。`items` はこのパッケージのどのモジュールも読まない。`analysis_record` は `write` も `check` も読まない（`check` の定数 `ANALYZER`・`SCHEMA` を使わず、スキーマのパスは `ndf.contracts.schema_path` から自分で引く）。`measure_ci` と `measure_repo` は今のまま `cli` から呼ばれ、`analysis_record` を読まない。そのため依存に輪は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/project/analysis_record.py` | 新設 | 解析の記録の形を持ち、設定から旧い形のキーを取り除く | `ndf.contracts`・`ndf.domains.records.files`・`ndf.domains.records.jsonfile`・`items` | `analysis_record` | `strip_item`・`strip_legacy`・`load`・`written_digest`（新設）。定数 `VERSION`・`IGNORE_TEXT`・`SCHEMA` |
| `plugins/ndf/src/ndf/domains/project/measure_ci.py` | 変更 | `ci` を必須のチェックだけにする | 変えない（`allocation`・`top_level_block` の取り込みを外す） | `measure_ci` | `measure`（変更）。`_time`・`span`・`_strip_comment`・`job_ids`・`workflow_jobs`・`junit_seconds`・`_xmls`・`_junit_of_run`・`_steps_of`・`_record_value`・`ndf_record`・`_test_duration`・`_has_skipped_job`・`_representative`・`_workflow_entry`・`_Runs`・`_measure_runs` と定数 `TEST_STEP`・`CANDIDATE_RUNS`・`RECENT`・`WORKFLOW_PATH`・`JUNIT_NAME`・`ARTIFACT_BYTES`（削除） |
| `plugins/ndf/src/ndf/domains/project/measure_repo.py` | 変更 | 言語の行からファイル数を外し、名前の順に並べる | 変えない | `measure_repo` | `measure_languages`（変更） |
| `plugins/ndf/src/ndf/domains/project/write.py` | 変更 | 設定の値を決め、解析の記録を組み、書くファイルの計画を作る | `analysis_record`・`check` | `write` | `merge_item`・`merge_project`・`_replace_ours`・`_declare_ci_tests`・`plan`・`Inputs`・`File`・`p_number`（変更）。`_carry_walls`・`_same_but_time`（削除）。`value_digest`（`items` へ移す。呼び出しは `items.value_digest` に直す） |
| `plugins/ndf/src/ndf/domains/project/items.py` | 変更 | `write` から移した `value_digest` を置き、`write` と `analysis_record` が共用する | 変えない（標準ライブラリの `hashlib`・`json` だけを足す） | `items` | `value_digest`（`write` から移す。中身は変えない） |
| `plugins/ndf/src/ndf/domains/project/strategy_write.py` | 変更 | 解決済みの git の共通ディレクトリからロックのパスを作る関数を足す | 変えない | `strategy_write` | `lock_file`（新設）。`lock_path`（責務を変えない。中で `lock_file` を使う） |
| `plugins/ndf/src/ndf/domains/project/strategy_cli.py` | 変更 | `check.repo_dirs` の戻り値を名前で受ける | 変えない | `strategy_cli` | `check.repo_dirs` を呼ぶ箇所（137 行目）だけ |
| `plugins/ndf/src/ndf/domains/project/check.py` | 変更 | 新しさを解析の記録から判定する | `analysis_record` | `check` | `load`・`judge`・`repo_dirs`（変更）。`RepoDirs`（新設の型）。`ANALYZER` を 3 から 4 へ |
| `plugins/ndf/src/ndf/domains/project/cli.py` | 変更 | 測定の結果からテストの所要時間を外す。書き出しの入力に前の解析の記録を足す。記録のパスが git に無視されることを確かめる。記録を要約で表示する | `analysis_record`・`write`・`check`・`strategy_write` | `cli` | `_ci_items`・`_inputs`・`_write`・`_print_write`（変更） |
| `.ndf/project.json` | 変更 | このリポジトリの追跡している設定を新しい形式へ移す（設計文書の決定 4） | 無し | — | — |
| `plugins/ndf/contracts/schemas/internal/project-analysis.schema.json` | 新設 | 解析の記録の形（`version`・`analyzer`・`inputs`・`branches`・`written_digest` が必須、`pending` が任意、ほかのキーを許さない） | 無し | — | — |
| `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` | 変更 | `Analysis` を `{written}` だけにする。`written` は今と同じく省略でき（既定は `{}`）、`required` を持たない。`Language` から `files` を外し、`required` を `name` だけにする。`Ci` を `{required_checks}` だけにする。`required_checks` は今と同じく省略でき（既定は `[]`）、`required` を持たない。旧い形のキーを取り除いた後の `analysis: {}`・`ci: {}` も形に合う。`Workflow` などのそれだけが使う定義を消す。`test_duration` と `TestDuration` を消す | 無し | — | — |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | 上のスキーマのバイト単位で同一のコピー | 無し | — | — |
| `plugins/ndf/contracts/domains/locations.json` | 変更 | 項目 `measured` を消し、`{"id": "analysis", "root": "repo", "path": ".ndf/analysis", "tracked": false}` を足す | 無し | — | — |
| `plugins/ndf/contracts/limits/branch-strategy.write-lock.json` | 変更 | `read_by` に `ndf.domains.project.cli` を足し、summary を「`.ndf/project.json` を書くロックの待ちの上限。ブランチ戦略の書き込みと解析の書き出しが共有する」へ直す。`seconds` の 30 は変えない | 無し | — | — |
| `plugins/ndf/contracts/domains/ndf-files.json` | 変更 | 行 `project-measured` を消す。行 `project-analysis`（`location` は `analysis`・`path` は `project.json`・`kind` は `record`・`tracked` は偽・`ends` は `replaced`・`keys` は `analyzer`・`inputs`・`branches`・`written_digest`・`pending`）と、行 `project-analysis-ignore`（`path` は `.gitignore`・`kind` は `record`・`ends` は `kept`）を足す。summary の「実行で変わる計測値は追跡する設定へ書き戻さず、状態の保存先へ書く」を「読み手の無い計測値は保存しない。解析の記録は追跡しない保存先へ書く」へ | 無し | — | — |
| `plugins/ndf/docs/contracts/ndf-settings.md`・`files.md` | 変更 | 生成し直す | `spec_render` | — | — |
| `plugins/ndf/skills/development-workflow/references/project-analysis.md` | 変更 | 「3 手」（measure・回答・write の手順の節）の、実行時間と、それを測る実行の選び方の段落を消す。「書き出しの規則」の実行時間の行を消し、指紋が設定に残ることを書く。「`.ndf/` の追跡」の表に解析の記録の行を足す。移行の 1 回目に出る差分をコミットする手順を書く | 無し | — | — |
| `docs/specifications/project-decl-ci-representative-run.md` | 削除 | 消した振る舞いだけを書いた確定仕様 | 無し | — | — |
| `docs/specifications/README.md` | 変更 | 上の行を消す | 無し | — | — |
| `plugins/ndf/tests/domains/project/test_analysis_record.py` | 新設 | `analysis_record` の単体テスト | `analysis_record` | — | — |
| `plugins/ndf/tests/domains/project/test_project_measure.py` | 変更 | 消した関数のテストを消し、`gh` の呼び出しが 3 か所だけであること・言語の行にファイル数が無く名前の順であることを確かめる | `measure_ci`・`measure_repo` | — | — |
| `plugins/ndf/tests/domains/project/test_project_cli.py` | 変更 | 書き出しと判定のテストの期待を、解析の記録が `.ndf/analysis/project.json` にある形へ直す。`test_merge_item_carries_ci_walls` と、`test_duration` を扱う `_ci_items` のテストを消す。`write.value_digest` の呼び出しを `items.value_digest` に直す | `cli`・`write`・`check` | — | — |

## 解析の記録（analysis_record.py）

満たす決定: 決定 1・3・4、受け入れ条件 2・3・5・8・9・11・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `strip_item` | `analysis_record.py` | 新設 | `strip_item(key: str, value: Any) -> tuple[Any, list[str]]` | 設定の 1 項目の値から旧い形のキーを取り除いた値と、取り除いたキーの名前を返す |
| `strip_legacy` | `analysis_record.py` | 新設 | `strip_legacy(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]` | 設定から旧い形のキーを取り除いたコピーと、見つけたキーの名前の並びを返す |
| `load` | `analysis_record.py` | 新設 | `load(main: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]` | メインディレクトリの解析の記録を読み、(記録, None) か (None, 読めない理由) を返す |
| `written_digest` | `analysis_record.py` | 新設 | `written_digest(doc: Optional[dict[str, Any]]) -> str` | 設定の書いた値の指紋（`analysis.written`）から、記録に対応する設定の指紋を作る。ハッシュは `items.value_digest` で取る |

- `strip_item`
  - 事後条件: `key` が `languages` で値が配列なら、各行から `files` を除き、取り除いたら `["languages[].files"]` を返す。`ci` で値が object なら、`workflows` と `provider` を除き、取り除いたキーの名前（`ci.workflows`・`ci.provider`）を返す。`required_checks` の無い `ci` からは `{}` を返す（`ci` を消さない）。ほかのキー・不明（`{"unknown": …}`）・形の違う値は `(value, [])` をそのまま返す。入力を書き換えない
  - 副作用: 無し
- `strip_legacy`
  - 事後条件: 返したコピーには、`analysis` の `written` 以外のすべてのキー・`test_duration`・`strip_item` が取り除くキーが無い。`written` の無い `analysis` からは `{}` を残す（`analysis` を消さない）。見つけたキーの名前は、取り除いたキーごとに 1 つ並ぶ。`analysis` のキーは 1 つずつ（例 `analysis.analyzer`・`analysis.at`・`analysis.inputs`・`analysis.branches`）、`test_duration` は 1 つ、`languages[].files` は行がいくつあっても 1 つである。並びは設定のキーの順である。入力を書き換えない
  - 副作用: 無し
- `load`
  - 事後条件: 記録が在り、UTF-8 の JSON の object で、`SCHEMA`（`internal/project-analysis`）に合えば (記録, None)。途中の印（`pending: true`）の付いた記録もそのまま返す（判定は `check.judge` が行う）。旧い形の記録（`at` などの知らないキーを持つ）は形が違うとして返す
  - 副作用: ファイルを 1 つ読む
- `written_digest`
  - 事後条件: `items.value_digest(doc["analysis"]["written"])` を返す。正規化した JSON の SHA-256 を自分で書かず、書いた値の指紋と同じ関数を使う。`doc` が None・`analysis` か `written` が無い・object でないときは `{}` から作る。`analysis.written` の外のキー（人が直した値・`branch_strategy`）は結果に影響しない
  - 副作用: 無し

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load` | 記録が無い | 新しく clone した・消した | `Path.is_file` | 呼び出し元へ返す（`None, "解析の記録が無い"`） | 判定の理由「解析の記録が無い」、`check` の終了コード 3 | 書き出しが作る。再実行してよい |
| `load` | 読めない | JSON でない・object でない | `jsonfile.read_object` の `JsonFileError` | 呼び出し元へ返す（`None, "解析の記録が読めない: <誤り>"`） | 同上 | 書き出しが置き換える |
| `load` | 読めない | 不正な UTF-8 を含む | `jsonfile.read_object` が変換せずに送る `UnicodeDecodeError`（`jsonfile.read` は `OSError` と `JSONDecodeError` だけを `JsonFileError` に変える） | `load` が捕まえて呼び出し元へ返す（`None, "解析の記録が読めない: UTF-8 として読めない（位置 <n>）"`） | 同上 | 同上 |
| `load` | 形が違う | 人が書き換えた・旧い NDF が書いた | `contracts.schema_errors` | 呼び出し元へ返す（`None, "解析の記録が形に合わない: <誤り 3 件まで>"`） | 同上 | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `VERSION` | 定数 | `analysis_record.py` | `1`（解析の記録の形のバージョン） |
| `IGNORE_TEXT` | 定数 | `analysis_record.py` | `"# NDF の解析が書く記録。コミットしない\n*\n"` |
| `SCHEMA` | 定数 | `analysis_record.py` | `contracts.schema_path("internal/project-analysis")` |

保存先のパスは定数に持たず、`files.path(main, "project-analysis")`・`files.path(main, "project-analysis-ignore")` で表から引く（`files.relpath` が場所 `analysis` の `.ndf/analysis` と行の `path` をつなぐ）。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load` | `ndf.domains.records.jsonfile.read_object`・`ndf.contracts.schema_errors` | `grep -n "def read_object\|def schema_errors" -r plugins/ndf/src/ndf` | 使う |
| `strip_item`・`strip_legacy` | `items.obj`・`items.seq` | 読んだ | 使う |
| `written_digest` | `write.value_digest`（正規化した JSON の SHA-256） | `grep -rn "def value_digest" plugins/ndf/src` | 使う。`analysis_record` は `write` を読まない（`write` が `analysis_record` を読むので、読むと依存が輪になる）ため、`value_digest` を両方が既に読む `items` へ移して共用する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `strip_item` | `languages` の `files`、`ci` の `workflows`・`provider` を取り除き、名前を返す | `files` の無い行・空の配列・不明の値・ほかのキー・`required_checks` だけの `ci`（何も取り除かない）・`required_checks` の無い旧い `ci`（`{}` を返す） | 配列でない `languages`・object でない `ci`（そのまま返す） |
| `strip_legacy` | 旧い形の設定から 4 種類のキーを取り除き名前を返す | 新しい形の設定（空の並びを返し、コピーが入力と等しい）・`written` と `required_checks` を両方省いた旧い形（`analysis: {}`・`ci: {}` を残し、設定のスキーマに合う） | `analysis` が object でない（そのまま残す。形の検査が拾う） |
| `load` | 形に合う記録・途中の印の付いた記録を返す | — | 無い・JSON でない・不正な UTF-8・形が違う・`at` を持つ旧い形・`written_digest` の無い記録（理由を返す） |
| `written_digest` | 同じ `written` なら、キーの並びが違っても同じ値 | 設定が None・`analysis` の無い設定（`{}` の値と同じ）・`written` の外だけが違う 2 つの設定（同じ値） | `written` の値が 1 つ違う（違う値） |

## 測定（measure_ci.py・measure_repo.py・cli.py の `_ci_items`）

満たす決定: 決定 3、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `measure` | `measure_ci.py` | 変更（責務） | 変えない | 返すのを `{"ci", "merges", "github_issues", "hosts", "notes"}` にする（`test_duration` を外す）。`ci` は `measured({"required_checks": _required_checks(gh, head)})` で、実行の一覧・ジョブ・成果物を取りに行かない。`repo` が無いときは今と同じく `ci` を不明にする |
| `measure_languages` | `measure_repo.py` | 変更（責務） | 変えない | 言語の行を `{name, frameworks, version?}` にし、`files` を書かない。並びを言語の名前の順にする。言語を見つけるための拡張子の数え上げは今のまま行う |
| `_ci_items` | `cli.py` | 変更（責務） | 変えない | `items["ci"]` だけを入れ、`items["test_duration"]` を入れない。時間切れで `ci_part` が無いときの `test_duration` の補いを消す |
| `p_number` | `write.py` | 変更（責務） | 変えない | 表から `test_duration`（P3）の行を消す |

- `measure`
  - 事後条件: `gh` の呼び出しは、`rules/branches/<head>`（`head` があるとき）・`pulls?state=closed&per_page=100`・`issues?state=all&per_page=100` の 3 つだけである。`_required_checks` が時間切れ（`GhUnavailable(TIME_UP)`）を受けたら、今の `_measure_runs` と同じく `ci` を不明にし、注記 `CI を測れない: 時間切れ` を出す。時間切れ以外の失敗は、今と同じく必須のチェックを空の並びにする
  - 副作用: `gh` を最大 3 回起動する。ファイルを読まない（今の `ndf_record` が読んでいた NDF の実行の記録を読まない）

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `measure` | `Gh` の起動を差し替えると、呼ばれるパスは 3 つだけで `actions/` で始まるものが無い。`ci` は `{required_checks: [...]}` | `head` が無い（`rules/branches` を呼ばず空の並び）・`repo` が無い（`ci` が不明で `gh` を呼ばない） | `rules/branches` が時間切れ（`ci` が不明・注記）・それ以外の失敗（空の並び） |
| `measure_languages` | 行に `files` が無く、名前の順に並ぶ | ファイル数の順位が入れ替わる 2 つの木で、並びが同じ | — |

## 書き出し（write.py・cli.py）

満たす決定: 決定 2・3・4、受け入れ条件 1・2・3・5・6・10・12・13・15・16・17

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Inputs` | `write.py` | 変更 | 前: `Inputs(root, main, existing, measure, answers)`。後: `Inputs(root, main, existing, measure, answers, record: Optional[dict[str, Any]])` | 書き出しの入力に、前の解析の記録（読めなければ None）を足す |
| `merge_item` | `write.py` | 変更（責務） | 変えない | 持ち主の見分けは渡された前の値（取り除く前）で行い、前の値を残すとき・新しい値と比べるときは `analysis_record.strip_item` で旧い形のキーを取り除いた前の値を使う |
| `merge_project` | `write.py` | 変更 | 前: `merge_project(existing, measure, answers, analysis, written_before) -> tuple[dict, list]`。後: `merge_project(existing, measure, answers, written_before) -> tuple[dict[str, Any], list[dict[str, Any]]]` | 引数 `analysis`（解析の記録）を外す。設定の `analysis` は、指紋が 1 つも無くても必ず `{"written": …}` の形にし、`test_duration` を取り除く |
| `_replace_ours` | `write.py` | 変更（責務） | 変えない | 前の値のままの項目を置き換える。新しい値が不明なら前の値（旧い形のキーを取り除いた値）を残す。`ci` の実行時間の引き継ぎ（今の `_carry_walls` の呼び出し）を外す |
| `_declare_ci_tests` | `write.py` | 変更（責務） | 変えない | 指紋を `doc["analysis"]["written"]` に書く（`analysis` が `written` だけになっても同じ場所） |
| `File` | `write.py` | 変更 | 前: `File(path, name, old, new)`。後: `File(path, name, old, new, summary: Optional[str] = None)` | `summary` があれば、表示は差分の代わりにこの 1 行を出す |
| `plan` | `write.py` | 変更（責務） | 変えない | 解析の記録を組み、書くファイルを `.ndf/analysis/.gitignore`（無いときだけ）→ 途中の印を付けた前の記録 → `.ndf/project.json`（今の中身と違うときだけ）→ 解析の記録の順で返す |
| `_write` | `cli.py` | 変更（責務） | 変えない | 作業場所を `check.repo_dirs` で 1 回解決する（`--dry-run` でも行う）。`--dry-run` でなければ、既存の設定と記録を読む前にロックを取り、最後のファイルを書くまで持つ。書く前に、記録が git に無視されることを確かめてからファイルを書く（手順は表の下） |
| `_print_write` | `cli.py` | 変更（責務） | 変えない | 動詞は今と同じく `dry_run` で選ぶ（`書く（--dry-run）` か `書いた`）。`File.summary` があるファイルは `<動詞>: <パス>（<summary>）` の 1 行だけを出し、差分を出さない。`.ndf/project.json` は今と同じく差分を出す |

- `merge_project`
  - 事前条件と順序: 新しい値は測定がもう旧い形のキーを持たないので、そのまま `merge_item` へ渡す。前の値は取り除く前の値のまま渡し、`merge_item` が見分けた後で取り除く
  - 事後条件: 返す設定に旧い形のキーが無い（I1）。`written` の指紋は、旧い形のキーを取り除いた後の値から作る
- `plan`
  - 解析の記録: `{"version": analysis_record.VERSION, "analyzer": check.ANALYZER, "inputs": <測定の結果の inputs>, "branches": <check.branch_state>, "written_digest": analysis_record.written_digest(<書き出しの後の設定>)}`。書き出しの後の設定は、`.ndf/project.json` を書くときはその新しい中身、書かないときは今の中身である。解析した時刻を持たない。組んだ記録を `analysis_record.SCHEMA` で確かめる
  - 事後条件: 返す `Plan.files` のうち追跡するファイルは `.ndf/project.json` だけ（受け入れ条件 6）。`.gitignore` の `File` は `files.path(main, "project-analysis-ignore")` が無いときだけ入り、中身は `analysis_record.IGNORE_TEXT`
  - 最後の記録: 次のどれかに当たるとき、印の無い記録の `File` を最後に入れる。(1) 記録の中身が前の記録と違う。(2) `.ndf/project.json` を書く（このとき途中の印も入るので、印を消すために必ず書く）。(3) `Inputs.record` に `pending: true` が残っている。どれにも当たらなければ記録を書かない
  - 途中の印: `Inputs.record` があり、`.ndf/project.json` か記録のどちらかを書くときだけ、`{**Inputs.record, "pending": true}` の `File` を `.ndf/project.json` の前に入れる。印を入れた計画には、必ず最後の記録が入る。設定を書く回は (2) が、記録だけを書く回は (1) か (3) が当たるためである。最後の記録の `File` は `pending` を持たない
  - 表示の要約: `.gitignore`・途中の印・記録の `File` は `summary` を持つ。記録は `解析の記録。入力 <N> 件`、途中の印は `書き出しの途中の印`、`.gitignore` は `git から外す設定` とする。`.ndf/project.json` の `File` は `summary` を持たない
  - 副作用: 無し（書くのは `cli._write`）
- `_write` の手順
  - 最初に `check.repo_dirs` を 1 回だけ呼び、作業場所・メインディレクトリ・git の共通ディレクトリを `RepoDirs` として求める。これは `--dry-run` でも行う。worktree から打ったときに、メインディレクトリの設定と記録から予定を作るためである。ロックのパスと `_inputs` は、この解決済みの値を使い、git の共通ディレクトリを求め直さない。今の書き出しも `check.repo_dirs` を 1 回呼んでいるので、git の起動の回数は今と同じである
  - `--dry-run` のとき: 上の解決の後、ロックを取らずに `_inputs(args, dirs)` と `write.plan` で予定を作り、表示して終える。省くのは、ロック・`git ls-files`・`git check-ignore`・ファイルの書き込みの 4 つである
  - `--dry-run` でないとき: 設定と記録を読む前に `FileLock(strategy_write.lock_file(dirs.common), timeout=limits.seconds("branch-strategy.write-lock"))` を取る。`_inputs(args, dirs)`（既存の設定と記録の読み込み）・`write.plan`・下の書き込みは、すべてこのロックの中で行う。ロックは最後のファイルを書いた後か、途中で止めたときに放す
  - `Plan.files` が空のとき: `git ls-files` と `git check-ignore` を打たず、終了コード 0 で終わる（`check.repo_dirs` の git とロックは、計画を作る前なので、この回も通る）
  - `Plan.files` に `.ndf/analysis/` の下のファイル（`.gitignore`・途中の印・記録のどれか。`.gitignore` だけでもよい）があるとき:
    1. `Plan.files` に `.gitignore` があれば、書く前にメインディレクトリで `git ls-files -- .ndf/analysis` を 1 回打つ。終了コードが 0 で、かつ標準出力が空のときだけ `.gitignore` を書く。終了コード 0 で出力が空でなければ「追跡されている」で止める。終了コードが 0 でなければ、出力によらず「確かめられない」で止める。どちらも何も書かない
    2. `git check-ignore -q <記録のパス>` を 1 回打つ。0 なら続ける。1 なら「無視されない」で止める。それ以外なら「確かめられない」で止める。どちらも残りを書かない。無視の出所（`.ndf/analysis/.gitignore`・ルートの `.gitignore`・`.git/info/exclude`・`core.excludesFile`）は問わない
    3. `Plan.files` の残りを順番に書く
  - それ以外（`.ndf/project.json` だけを書くとき）: 今と同じく順番に書く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `plan` | 書き出す設定が形に合わない | 解析器の誤り | `contracts.schema_errors(doc, check.SCHEMA)` | 止める（`WriteError`） | `書き出す設定が形に合わない（解析器の誤り）`、終了コード 1（今と同じ） | 何も書かない |
| `plan` | 書き出す解析の記録が形に合わない | 解析器の誤り | `contracts.schema_errors(record, analysis_record.SCHEMA)` | 止める（`WriteError`） | `書き出す解析の記録が形に合わない（解析器の誤り）`、終了コード 1 | 何も書かない |
| `cli._write` | `.ndf/analysis/` の下に追跡されたファイルがある（`.gitignore` を作る前） | 利用者が追跡していた `.gitignore` を作業ツリーで消した・記録を追跡した（I4） | `git ls-files -- .ndf/analysis` が終了コード 0 で、出力が空でない | 止める。`.gitignore`・途中の印・`.ndf/project.json`・記録は書かない | `.ndf/analysis/ の下のファイルが git に追跡されている: <パス>`、終了コード 1 | 利用者が追跡をやめるか、消した `.gitignore` を戻してから打ち直す |
| `cli._write` | ロックを取れない | 別の worktree の書き出しか `ndf project branch-strategy` が `.ndf/project.json` を書いている（I10） | `filelock.Timeout`（上限 `branch-strategy.write-lock` の 30 秒） | 止める。設定と記録を読まず、何も書かない | `別の実行が設定を書いています。終わってから打ち直してください`、終了コード 1 | ロックを持っていた側の結果が残る。打ち直してよい |
| `cli._write` | git が追跡と無視を確かめられない | 索引が壊れている・読めないなど | `git ls-files` の終了コードが 0 以外、または `git check-ignore` の終了コードが 0 でも 1 でもない | 止める。`.gitignore`（`git ls-files` で止めたとき）・途中の印・`.ndf/project.json`・記録は書かない | `git が .ndf/analysis/ の追跡と無視を確かめられない: <git の標準エラー出力>`、終了コード 1 | 利用者が git の状態を直してから打ち直す |
| `cli._write` | 記録のパスが git に無視されない | 無視する除外の設定がどこにも無い（既存の `.ndf/analysis/.gitignore` が空・独自のパターンで、ルートの `.gitignore`・`.git/info/exclude`・`core.excludesFile` も無視しない）・記録が追跡済み（I4） | `git check-ignore -q` の終了コードが 1 | 止める。途中の印・`.ndf/project.json`・記録は書かない。既にあった `.gitignore` は変えない | `.ndf/analysis/project.json が git に無視されない。.ndf/analysis/.gitignore の中身か、記録が追跡されていないかを確かめる`、終了コード 1 | 利用者が除外の設定を直してから打ち直す |
| `cli._write` | 途中のファイルを書けない（`.ndf/analysis/.gitignore` を書けないとき（I4）を含む） | 権限・ディスク | `signals.replace_text` の `OSError` | 止める。`.gitignore` か途中の印を書けなければ、後の `.ndf/project.json` と解析の記録は書かない | `設定を書けない: <誤り>`、終了コード 1（今と同じ） | 書けた分は残る。途中の印の後で止まれば印が残り、前の記録が無ければ記録が無いので、次の判定が「古い」と読む（設計文書の「処理の流れ」の表、I7） |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `merge_item` | 前の値（旧い形のキーを取り除く前）のハッシュが、前の `written` の指紋と一致する | 解析の値。新しい値で置き換える |
| `merge_item` | 一致しない、かつ新しい値が不明か、取り除いた前の値と同じ | 人の値。取り除いた前の値を残し、行 `kept` |
| `merge_item` | 一致しない、かつ違う | 人の値。取り除いた前の値を残し、行 `mismatch`（`declared` は取り除いた前の値） |

## 判定と読み込み（check.py・cli.py）

満たす決定: 決定 4・5、受け入れ条件 8・9・11・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load` | `check.py` | 変更（責務） | 変えない | 設定を読み、`analysis_record.strip_legacy` で取り除いたコピーでスキーマを確かめ、取り除く前の値を返す（旧い形を「壊れている」にしない） |
| `judge` | `check.py` | 変更（責務） | 変えない | 旧い形のキーがあれば「古い」。次に `analysis_record.load` の記録が無ければ「古い」。記録に途中の印（`pending: true`）があれば「古い」。記録の `written_digest` が `analysis_record.written_digest(<今の設定>)` と違えば「古い」。どれでもなければ記録の `analyzer`・`inputs`・`branches` で今と同じ判定をする |
| `repo_dirs` | `check.py` | 変更 | 前: `repo_dirs(root) -> tuple[git.Git, Path]`。後: `repo_dirs(root) -> RepoDirs`（`NamedTuple`。`top: git.Git`・`main: Path`・`common: Path`） | 作業場所・メインディレクトリ・git の共通ディレクトリを返す。共通ディレクトリは、今もメインディレクトリを求めるために取っている `top.common_dir()` の値をそのまま返し、git を余分に起動しない。呼び出し元（`check.py`・`cli.py` の 2 か所・`strategy_cli.py`）は名前で受ける |
| `_inputs` | `cli.py` | 変更 | 前: `_inputs(args)`。後: `_inputs(args, dirs: check.RepoDirs)` | 渡された `dirs` の `main` から設定と記録を読み、`check.repo_dirs` を呼ばない。`analysis_record.load(main)` の記録を `Inputs.record` に渡す（読めなければ None） |
| `lock_file` | `strategy_write.py` | 新設 | `lock_file(common: Path) -> Path` | 解決済みの git の共通ディレクトリからロックのパス（`common / LOCK_NAME`）を返す。git を起動しない。`lock_path(root)` は共通ディレクトリを求めてからこの関数を呼ぶ |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `judge` | 旧い形のキーがある | 移行の前の設定 | `analysis_record.strip_legacy` の並びが空でない | 「古い」 | 理由 `旧い形の値が設定にある: <キー>`、終了コード 3 | 手順 0 が解析を打ち直す |
| `judge` | 解析の記録が無い・読めない | 決定 5 | `analysis_record.load` | 「古い」 | 理由に `load` の理由、終了コード 3 | 同上 |
| `judge` | 前の書き出しが途中で止まった | 書き出しの途中の失敗（I7） | 記録の `pending` | 「古い」 | 理由 `前の書き出しが終わっていない`、終了コード 3 | 同上 |
| `judge` | 記録が今の設定に対応しない | 書き出しの後に利用者が `.ndf/project.json` を `git restore` などで以前の状態へ戻した・別のブランチの設定に切り替えた（I9） | `written_digest` の不一致 | 「古い」 | 理由 `設定が解析の記録と対応しない`、終了コード 3 | 同上 |
| `load` | 取り除いた後も形に合わない | 人が設定を壊した | `contracts.schema_errors` | 呼び出し元へ返す（`JsonFileError`。今と同じ） | `check` は終了コード 2、`write` は 2（今と同じ） | 利用者が直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge` | 記録の入力のファイルのハッシュと今の HEAD が同じなら「新しい」、入力かブランチが変わるか、記録の解析器のバージョンが古ければ「古い」 | worktree から打つとメインディレクトリの記録を読む。解析の書いていない項目や `branch_strategy` だけを変えた設定は「新しい」のまま | 記録が無い・壊れている・不正な UTF-8・途中の印・旧い形の設定・`analysis.written` だけを以前の値へ戻した設定（どれも終了コード 3） |
| `load` | 新しい形の設定を返す | 旧い形の設定を、取り除かずに返す。`analysis.written` と `ci.required_checks` を両方省いた旧い形の設定も返す（取り除いた後の `analysis: {}`・`ci: {}` が形に合う） | 取り除いても形に合わない（終了コード 2） |
| `plan`（`cli.main(["write", …])` から） | 2 回目の書き出しで `.ndf/project.json` も記録も書かない。記録を差分でなく要約の 1 行で表示する。記録の `written_digest` が書いた設定の値と合う | 移行の 1 回目（旧い形のキーを持つ設定。`analysis.written` と `ci.required_checks` を両方省いたものを含む）・記録の無い clone・`ci` が不明 | 前の記録の入力が今と同じで、`.ndf/project.json` か記録の書き込みを失敗させる（次の判定が「古い」）・記録のパスが無視されない（終了コード 1 で既存のファイルが前のまま） |
| `plan`・`_write`（`cli.main(["write", …])` から） | 同じ測定の結果で回答だけを変えた書き出しの後、記録に途中の印が無い。印の残った記録から同じ内容で打ち直すと、印が消える。`.ndf/analysis/.gitignore` が空でも `.git/info/exclude` が記録のパスを無視していれば書く（テストでは `GIT_CONFIG_GLOBAL` を空のファイルにし、`GIT_CONFIG_NOSYSTEM=1` にする） | 記録の無い clone で `--dry-run` を打つと、`.ndf/analysis/` の下に何も作られず、表示の動詞が `書く（--dry-run）` になる。書くものが無い書き出しでは `git ls-files` と `git check-ignore` を呼ばず終了コード 0（記録が追跡済みでも）。書くファイルが `.gitignore` だけで記録が追跡されていないとき、`git ls-files` と `git check-ignore` を 1 回ずつ呼ぶ（呼び出しを差し替えて回数を数える）。記録が追跡済みなら `git ls-files` だけを呼ぶ | 追跡していた `.gitignore` を作業ツリーで消した状態で、`.gitignore` を作らずに終了コード 1。`git ls-files` が終了コード 128 で出力が空なら `.gitignore` を作らずに終了コード 1。`git check-ignore` が 128 なら設定と記録を書かずに終了コード 1。ロックを持った状態（上限を 0 秒に差し替える）で書き出すと、設定と記録を読まずに終了コード 1 |
