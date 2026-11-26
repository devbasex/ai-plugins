# #4295 #2037 の詳細設計: 触るファイルと関数での重なりの判定と、変えたファイルの知らせ

設計文書 [issue-4295-2037-design.md](./issue-4295-2037-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。データの形・知らせの組の規則・設計の読み方の表は設計文書が持ち、ここでは指すだけにする。

- 不変条件の番号 `I1`〜`I9` と「決定」は、設計文書の番号である
- 受け入れ条件は `#4295-1` のように「課題-番号」で書く
- パスは、断りが無ければ `plugins/ndf/` からの相対である。地図の表だけはリポジトリの根からの相対で書く

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/
│   ├── ci_cli.py
│   ├── core/            design_results.py*  durable.py  plan/queue_decide.py  route/__init__.py
│   ├── contracts/       limits.py
│   ├── domains/         materials/collect.py
│   └── progress/        run.py  definition.py  stages.py  invoke.py
│       ├── lane/        types.py  fold.py  rules.py  __init__.py  machine.py  launch.py
│       └── plan/        __init__.py  run.py  run_decide.py  touches.py*  touches_decide.py*
├── contracts/
│   ├── workflow/        progress.json  ndf.schema.json  parallel-plan.schema.json
│   │   └── stages/      document-review.json  implement.json
│   ├── schemas/         limits.schema.json  internal/stage-snapshot.schema.json
│   ├── limits/          parallel-run.touches.json*
│   └── templates/       impl.json  merge.json  params.json
├── skills/              design/ development-workflow/ issue-plan-strategy/ の references
└── tests/
```

依存の向き: `core/design_results.py` と `core/plan/queue_decide.py` は `progress` と `domains` を読まない。`progress` と `domains/materials` がこの 2 つを読む。`progress/lane` は `progress/plan` を読まず、`progress/plan/touches.py` が `lane`・`touches_decide`・`design_results` を読む。`touches_decide.py` は `lane` の型と `rules` だけを読み、DB とプロセスを読まない。どの向きにも循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/core/design_results.py` | 新設 | 版の中の設計の文書から、課題の並びの触る範囲を求める | `core/issue_docs`、`core/plan/inputs_decide`、`core/plan/queue_decide`、`adapters/git`、markdown-it | `Entry`・`Source`・`Scope`・`Docs`・`GitDocs`・`DocsUnreadable` | 新設: `section_tables`・`result_entry`・`design_rows`・`design_files`・`map_paths`・`scope_of`・`merge_commit` |
| `plugins/ndf/src/ndf/domains/materials/collect.py` | 変更 | 設計の結果の読み取りを `design_results` へ任せる | `core/design_results` | モジュール | 削除: `design_rows`・`design_files`・`DESIGN_FILE`。変更: `gather` の呼び先 |
| `plugins/ndf/src/ndf/core/plan/queue_decide.py` | 変更 | `<パス>::<名前>` を分けて重なりを決める | — | モジュール | 新設: `split_symbol`。変更: `contains` |
| `plugins/ndf/src/ndf/core/route/__init__.py` | 変更 | 実装のプランの `touches` を設計の結果から補う | `core/design_results` | モジュール | 新設: `design_touches`。変更: `launch_values`・`needed`・`render`・`_one_template_errors` |
| `plugins/ndf/contracts/templates/impl.json` | 変更 | プランに `touches` を差し込む | `params.json` | テンプレート | — |
| `plugins/ndf/contracts/templates/params.json` | 変更 | 差し込み `touches`・`design_pr` を定義する | — | 設定 | — |
| `plugins/ndf/contracts/templates/merge.json` | 変更 | 打ち直しの `probe` を、head が同じマージ済みの PR に絞る | `parts/steps.json` | テンプレート | — |
| `plugins/ndf/src/ndf/ci_cli.py` | 変更 | `merge` の成功の結果に `outputs.pr` を載せる | — | モジュール | 変更: `_merge` |
| `plugins/ndf/src/ndf/progress/invoke.py` | 変更 | 差し込み `design_pr` の出所の説明を足す | `core/route` | モジュール | 変更なし（モジュールの説明だけ） |
| `plugins/ndf/contracts/workflow/stages/implement.json` | 変更 | 工程「実装」が文脈の `design_pr` を受ける | — | 定義 | — |
| `plugins/ndf/contracts/workflow/stages/document-review.json` | 変更 | 出力 `design_pr` を宣言し、`docs-merge` の出力の名前を付け替える | — | 定義 | — |
| `plugins/ndf/contracts/workflow/ndf.schema.json` | 変更 | ステップの `metadata.ndf` に `outputs_as` を足す | — | スキーマ | — |
| `plugins/ndf/contracts/schemas/internal/stage-snapshot.schema.json` | 変更 | 定義のスナップショットのステップに `outputs_as` を足す | — | スキーマ | — |
| `plugins/ndf/src/ndf/progress/stages.py` | 変更 | ステップの定義が `outputs_as` を持つ | — | `StepDef` | — |
| `plugins/ndf/src/ndf/progress/definition.py` | 変更 | `outputs_as` を読み、付け替え先を確かめる | `stages` | モジュール | 新設: `_outputs_as_errors`・`modes_with_stage`。変更: `_step_def`・`check_contracts` |
| `plugins/ndf/src/ndf/progress/run.py` | 変更 | 工程の出力の名前を付け替えてから残す | `stages` | モジュール | 変更: `_stage_output` |
| `plugins/ndf/contracts/workflow/progress.json` | 変更 | 事象 `touches_found`・`touches_compared`・`touches_retry` を足す | — | 設定 | — |
| `plugins/ndf/src/ndf/progress/lane/types.py` | 変更 | 行の触る場所と比較の状態の型を持つ | — | `LaneSpec`・`PlanView`・`LaneRow`・`RowTouches`・`ScopeChange`・`DEFAULT_MODE` | 新設: `mode_of` |
| `plugins/ndf/src/ndf/progress/lane/fold.py` | 変更 | 新しい事象を畳み込む | `types` | モジュール | 新設: `_fold_touches`。変更: `_spec`・`_fold_plan_event`・`fold_plan`・`_row_from` |
| `plugins/ndf/src/ndf/progress/lane/rules.py` | 変更 | I13 を行の単位で判定し、重なりの組と待ちを求める | `types`、`core/plan/queue_decide` | モジュール | 新設: `designing`・`row_scope`・`overlap_pairs`・`ordered_since`・`touches_wait`。変更: `_touch_hits`・`_touch_errors`・`plan_complete` |
| `plugins/ndf/src/ndf/progress/lane/__init__.py` | 変更 | 工程 `document-review` を持つモードの名前を `PlanView` に載せる | `progress/definition` | モジュール | 変更: `read_plan` |
| `plugins/ndf/src/ndf/progress/lane/machine.py` | 変更 | マシンのほかの計画の畳み込みを返す | `lane` | モジュール | 新設: `other_views` |
| `plugins/ndf/src/ndf/progress/lane/launch.py` | 変更 | `DEFAULT_MODE` を `types` から読む | `types` | モジュール | — |
| `plugins/ndf/src/ndf/progress/plan/__init__.py` | 変更 | レーンの印を記録し、観測の事象を書く | `lane` | モジュール | 新設: `record_touches`。変更: `lane_payload`・`complete_plan` |
| `plugins/ndf/src/ndf/progress/plan/touches.py` | 新設 | GitHub とステートマシンを読み、観測の 1 周を行う | `lane`、`touches_decide`、`core/design_results` | `Memory`・`ApiDocs`・`FetchError` | 新設: `observe_cycle`・`gh_api`・`record_designs`・`record_diffs`・`compare` |
| `plugins/ndf/src/ndf/progress/plan/touches_decide.py` | 新設 | 観測の判断（振り分け・取得の順・最後の観測・知らせ）を持つ | `lane` の型と `rules` | `Pull`・`Pending`・`Notices` | 新設: `assign`・`pick`・`is_final`・`counted`・`notices` |
| `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | 観測の 1 周を耐久ステップで呼ぶ | `plan/touches` | モジュール | 新設: `observe_touches`。変更: `policy`・`spec_of`・`snapshot_of`・`observe`・`complete`・`_perform`・`lanes_workflow` |
| `plugins/ndf/src/ndf/progress/plan/run_decide.py` | 変更 | 読み直しの何回かに 1 回、観測の手を返す | — | `Touches`・`Policy`・`RunState` | 変更: `decide` |
| `plugins/ndf/src/ndf/core/durable.py` | 変更 | `FORMAT_SOURCE_HASH` を書き直す | — | 定数 | — |
| `plugins/ndf/contracts/limits/parallel-run.touches.json` | 新設 | 観測の間隔・取得の上限・失敗の上限を持つ | — | 設定 | — |
| `plugins/ndf/contracts/schemas/limits.schema.json` | 変更 | 上限の項目 `fetches` を足す | — | スキーマ | — |
| `plugins/ndf/src/ndf/contracts/limits.py` | 変更 | `Policy` に `fetches` を足す | — | `Policy` | — |
| `plugins/ndf/contracts/workflow/parallel-plan.schema.json` | 変更 | レーンの任意の真偽値 `touches_from_design` を足す | — | スキーマ | — |
| `plugins/ndf/skills/design/references/design-template.md` | 変更 | 触るファイルのセルの `<パス>::<名前>` を書く | — | 手順書 | — |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | 単発の経路の `--add design_pr=` と `touches` の補いを書く | — | 手順書 | — |
| `plugins/ndf/skills/development-workflow/references/parallel.md` | 変更 | `touches_overlap` の扱いを知らせの表に足す | — | 手順書 | — |
| `plugins/ndf/skills/development-workflow/references/parallel-checks.md` | 変更 | I13 が行の触る場所を比べることを書く | — | 手順書 | — |
| `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md` | 変更 | `touches_from_design` と `after` の書き方を書く | — | 手順書 | — |
| `plugins/ndf/tests/core/test_design_results.py` | 新設 | 設計の読み方を縛る | — | テスト | — |
| `plugins/ndf/tests/core/plan/test_queue.py` | 変更 | I5 を縛る | — | テスト | — |
| `plugins/ndf/tests/adapters/test_ci_cli.py` | 変更 | `merge` の成功の結果の `outputs.pr` を縛る | — | テスト | — |
| `plugins/ndf/tests/route/test_templates.py` | 変更 | `touches` の補いと省略、`merge` の出力を縛る | — | テスト | — |
| `plugins/ndf/tests/progress/test_invoke_template_values.py` | 変更 | 工程の経路の `touches` を縛る | — | テスト | — |
| `plugins/ndf/tests/domains/materials/test_materials_cli.py` | 変更 | 読み手を移した後も材料が同じことを縛る | — | テスト | — |
| `plugins/ndf/tests/progress/test_lane.py` | 変更 | I1・I2・I13・I8 を縛る | — | テスト | — |
| `plugins/ndf/tests/progress/test_lane_machine.py` | 変更 | ほかの計画の読み出しを縛る | — | テスト | — |
| `plugins/ndf/tests/progress/test_plan_run.py` | 変更 | 観測の手を返す回数と完了の待ちを縛る | — | テスト | — |
| `plugins/ndf/tests/progress/test_plan_touches.py` | 新設 | 観測の 1 周（I3・I4・I6・I7・I9）を縛る | — | テスト | — |
| `plugins/ndf/tests/progress/test_run.py` | 変更 | 出力の付け替えを縛る | — | テスト | — |
| `plugins/ndf/tests/progress/test_definition.py` | 変更 | `outputs_as` の検査を縛る | — | テスト | — |

## 設計の結果の読み手

満たす決定: 決定 1・2・9、受け入れ条件 #4295-1・#4295-4・#2037-5

読み方の手順と、課題の記載の扱いの表は、設計文書の「設計の結果の読み方」にある。`scope_of` はその手順 2〜5 を行う。手順 1（設計 PR と版）は `merge_commit` が行う。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `section_tables` | `core/design_results.py` | 新設 | `(text: str, title: str) -> list[Table]` | 見出し `title` のセクションの表を返す（`collect` の `_section_tables` を移す） |
| `result_entry` | 同上 | 新設 | `(text: str, number: int) -> Optional[Entry]` | 「設計の結果」の表の課題の記載を返す。記載が無ければ `None` |
| `design_rows` | 同上 | 新設 | `(text: str, number: int) -> list[str]` | 記載の触るファイルのパス（`::` より前。重なりを除く） |
| `design_files` | 同上 | 新設 | `(root: Path, number: int, files: Iterable[str]) -> list[tuple[str, list[str]]]` | 手元の設計の文書ごとの触るファイル（材料の収集用） |
| `map_paths` | 同上 | 新設 | `(text: str) -> Optional[list[str]]` | 詳細設計の地図の表の「パス」の列の値。読めなければ `None` |
| `scope_of` | 同上 | 新設 | `(issues: Sequence[int], docs: Docs) -> Scope` | 課題の並びの触る範囲を決める |
| `merge_commit` | 同上 | 新設 | `(cwd: Path, number: int, repo: Optional[str] = None) -> Optional[str]` | 設計 PR のマージのコミット。マージ済みでなければ `None` |

- `result_entry`
  - 同じ課題の記載が 2 行以上あれば、扱いが同じなら触るファイルの和を取る。扱いが違えば `None` ではなく扱いを空文字にして返し、`scope_of` が読めなかったとする
  - 触るファイルのセルは `files_cell`（`core/plan/inputs_decide`）で読み、`<パス>::<名前>` のまま返す
- `design_files`
  - 名前の番号は `issue_docs.design_issues` で突き合わせる。`issue-4295-2037-design.md` はどちらの番号でも拾う
- `map_paths`
  - `## 地図` の中で、見出しの行に「パス」を持つ最初の表を読む
  - 表が無い、列が無い、バッククォートの値が 1 つも無い行がある、のどれかで `None`。一部だけを返すと、地図だけに載ったファイルが落ちるためである
- `scope_of`
  - 地図のパスは、同じ文書の設計の結果の表のどの記載の触るファイル（`::` より前）にも無いものだけを足す。表の記載は `section_tables` で、単位の課題でない記載も含めて読む
  - 事後条件: `files` が `None` なら `reason` が空でない。すべての課題が `閉じる` なら `files` は `()`（`None` ではない）
  - `sources` は読んだ文書ごとに `path`・`role`（`design`・`detail`）・`issues`。`repo` と `commit` は呼び手が足す
  - 副作用: 無し（読み出しは `docs` が行う）
- `merge_commit`
  - 副作用: `gh pr view <番号> [--repo <repo>] --json state,mergeCommit` を `cwd` で打つ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `merge_commit` | GitHub を読めない | `gh` の非 0 終了、出力が JSON でない | `merge_commit` | `DocsUnreadable` を投げる | 呼び手が扱う | 再実行できる |
| `scope_of` | 一覧か本文を取得できない | `docs` の失敗 | `docs` | `DocsUnreadable` をそのまま通す | 呼び手が扱う | 再実行できる |
| `scope_of` | 必要な記載が無い | 候補が無い・2 つ以上、記載が無い、空のセル、地図を読めない | `scope_of` | 呼び出し元へ返す（`files=None` と理由） | 理由は `touches_found` の `reason` か、プランを組むときの警告 | 同じ版からは同じ結果 |
| `design_files` | 手元の文書を読めない | `OSError`・`UnicodeDecodeError` | `design_files` | その文書を飛ばす（今と同じ） | 無し | 再実行できる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Entry(action, merged_into, files)` | 型 | `core/design_results.py` | — |
| `Source(path, role, issues)` | 型 | 同上 | — |
| `Scope(files, sources, reason)` | 型。`files` は `Optional[tuple[str, ...]]` | 同上 | — |
| `Docs` | Protocol。`names() -> list[str]`（`issues/` の下のパス）と `text(path) -> str` | 同上 | — |
| `GitDocs(location, commit, remote)` | `Docs` の実装。`git ls-tree --name-only <commit> issues/` と `git show <commit>:<パス>`。コミットが手元に無ければ `git fetch <remote> <commit>` を 1 回打つ | 同上 | — |
| 扱いの語（`実装する`・`マージする`・`閉じる`） | 定数 | 同上 | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `section_tables`・`design_rows`・`design_files` | `domains/materials/collect.py` の同じ働きの関数 | `grep -rn "design_rows\|design_files" src tests` | 移して使う（呼び手は `collect.gather` とテスト 1 件） |
| `result_entry` | `core/plan/inputs_decide.files_cell` | 定義を読んだ | セルの読み取りに使う |
| `map_paths` | `core/plan/inputs_decide.files_cell`、`adapters/markdown` | 両モジュールの関数の一覧を読んだ | `files_cell` を使う。`adapters/markdown` は表の中身を返さないので使えない |
| `scope_of` | `core/issue_docs.design_issues` | `grep -rn issue_docs src` | 名前の番号の突き合わせに使う |
| `merge_commit` | `adapters/github.GitHub.pull` | `Pull` の定義を読んだ | 使わない。設計文書が `gh pr view` で読むと定め、作業場所のリポジトリを `gh` が解く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `result_entry` | `::` 付きと無しのセル | `#78` は 7 と 8 に当たらない | 記載が無ければ `None` |
| `design_files` | 複数の番号の名前を拾う | 戻り値に `::` が残らない | 読めない文書を飛ばす |
| `map_paths` | 「（新設）」付きの行 | 「パス」の列が先頭でない表 | 表が無い、値の無い行で `None` |
| `scope_of` | 詳細設計ありは和、無しは結果の表だけ。地図にパスがあっても、表の `a.py::f` はそのまま残り `a.py` は足されない。表に無い地図のパスは足される。別の課題の記載にだけあるパスは足されない | すべて `閉じる` で `()`。`マージする` は統合先 | 候補 0 と 2、空のセル、地図を読めない、`DocsUnreadable` |
| `merge_commit` | マージ済みでコミット | 開いた PR で `None` | `gh` の失敗で `DocsUnreadable` |

## 触る範囲の判定

満たす決定: 決定 8・9、受け入れ条件 #2037-2〜#2037-4・#2037-6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `split_symbol` | `core/plan/queue_decide.py` | 新設 | `(value: str) -> tuple[str, Optional[str]]` | 値を最初の `::` でパスと名前に分ける。名前が無いか空なら `None` |
| `contains` | 同上 | 変更 | `(a: str, b: str) -> bool` | 不変条件 I5 で 2 つの値が重なるかを返す |

- `contains` の変える前は、値全体の正規化した文字列の一致だった。変えた後は、パスの部分がディレクトリなら偽、パスが違えば偽、どちらかに名前が無ければ真、両方に名前があれば、等しいか一方が `<名前>.` で始まるときだけ真にする
- `shared_hits`・`overlaps`・`waves` は変えない

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `split_symbol` | `str.partition` | — | 標準の関数を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `split_symbol` | `a.py::C.m` | `a.py::` は名前なし | — |
| `contains` | `a.py::f` と `a.py::g` は偽 | `a.py::C` と `a.py::C.m` は真、`a.py::C` と `a.py::Cx` は偽 | `tests/` はどれとも偽 |

## 実装のプランの補い

満たす決定: 決定 10、受け入れ条件 #2037-1・#2037-5

差し込み `touches` は「値があるときだけプランのキーになる」。値が無ければ `render` がプランから `touches` のキーを外すので、プランはすべてと重なる。

- `impl.json` のプランに `"touches": ["{{touches}}"]` を足し、`params` に `touches`・`design_pr` を足す
- `params.json` に `touches`（`list: true`、既定なし）と `design_pr`（文字列、既定なし）を足す
- 工程の経路は、`implement.json` の `metadata.ndf.optional_inputs` に `design_pr` を足して文脈から受ける。`template_values` は、入力のうちテンプレートの `params` にある値を写すので、追加の読み出しは要らない。`design_pr` は無くてもよい値なので、足りない値の出所の表（`FILL_SOURCES`）には足さず、モジュールの説明に出所だけを書く
- 単発の経路は `--add design_pr=<番号>` で渡す。`--add` の値は並びになるので、`design_touches` は文字列と要素 1 つの並びの両方を受ける

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `design_touches` | `core/route/__init__.py` | 新設 | `(values: Mapping[str, Value], issues: Sequence[int]) -> Optional[list[str]]` | 設計 PR のマージのコミットの文書から、単位の課題の触る範囲を返す。読めなければ `None` |
| `launch_values` | 同上 | 変更 | 変えない | テンプレートが `touches` を持ち値が無ければ、`design_touches` で補う |
| `needed` | 同上 | 変更 | 変えない | 値があるときだけの差し込み（`touches`）と、その元（`design_pr`）を、値が無ければ求めない |
| `render` | 同上 | 変更 | 変えない | `touches` の値が無ければ、プランのキー `touches` を外してから埋める |
| `_one_template_errors` | 同上 | 変更 | 変えない | `design_pr` を `touches` の元として「使う差し込み」に数える |

- `design_touches`
  - 事前条件: `values` の `repo` は `derived` か工程の経路が埋めている
  - 単位の課題は、`acceptance` の `--issue` の値の並び。無ければ `issue`
  - `design_pr`・課題・`repo` のどれかが無ければ、何も読まずに `None`
  - 版は `merge_commit(repo, design_pr)`、文書は `GitDocs(repo, commit, remote)` で読む
  - 副作用: `gh pr view`、`git ls-tree`・`git show`（必要なら `git fetch`）。作業場所のファイルと HEAD は読まない
- `launch_values`: `--add touches=` で値があるときは補わない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `design_touches` | 設計を読めない | `DocsUnreadable`、`GitError`、`merge_commit` が `None`、`Scope.files` が `None` | `design_touches` | 既定値で続ける（`touches` を書かない） | 標準エラーに 1 行「touches を書かない: <理由>」。終了コードは変えない | プランを組み直せば読み直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `design_touches` | `launch_values` の `acceptance` の組み方、`design_results.scope_of` | 定義を読んだ | `scope_of` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `design_touches` | 2 つの課題の和。`::` を残す | `design_pr` が `["12"]` | 1 つの課題だけ読めなくても `None` |
| `render` | `touches` があればキーを持つ | 無ければキーが無い。空の並びはキーを持つ | — |
| `launch_values` | 工程の経路と単発の経路が同じ値 | `--add touches=` が勝つ | 手元の設計の文書を書き換えても値が同じ |

## 設計 PR の番号の記録と、工程の出力の付け替え

満たす決定: 決定 3・10、受け入れ条件 #4295-1

- `document-review.json`: 次の 2 つを書く。`metadata.ndf.outputs` には `design_pr` を書く。`docs-merge` のステップの `metadata.ndf` には `"outputs_as": {"pr": "design_pr"}` を書く。設計文書の `metadata.ndf.outputs_as` はこの位置を指す
- `ndf.schema.json` の `$defs/step` に `outputs_as`（文字列から文字列への対応）を足す
- `stages.StepDef` に `outputs_as: Mapping[str, str] = field(default_factory=dict)` を足す。`stage-snapshot.schema.json` の `$defs/step` にも足す。前に作ったステートマシンのスナップショットはこの項目を持たないので、既定の空で読む
- マージした PR の番号は、`ndf ci merge`（`ci_cli._merge`）が返す。成功の結果は今も `items` にマージした番号を持つので、同じ番号を `outputs: {"pr": <番号>}` にも載せる。共有のステップ `merge` の `run` は変えず、CLI の起動も API の呼び出しも増えない
- `merge.json` は、共有のステップ `merge` の `probe` だけをこのテンプレートの中で上書きし、ステップの id と並びは変えない
  - `gh pr list --head <branch> --state merged --json number,headRefOid` を 1 回打ち、`headRefOid` が `git rev-parse HEAD` と一致する PR を選ぶ
  - 一致する PR があれば、`items` と `outputs.pr` にその番号を持つ結果 JSON を出して 0 で終える。無ければ、何も出さずに 1 で終える（まだ済んでいない）
- `run` と `probe` は、同じ「head が作業場所の HEAD である PR」を指す。`ndf ci merge` は開いた PR を `expect_head`（作業場所の HEAD）を添えてマージする。`probe` は同じ HEAD でマージ済みの PR を探す。同じブランチで前にマージした PR は head が違うので、設計 PR を同じブランチで出し直しても、前の PR の番号は記録されない
- この `outputs.pr` は工程「Pull Request」の `merge` からも出る。同じ PR の番号なので値は変わらない

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_stage_output` | `progress/run.py` | 変更 | 変えない | ステップごとの出力の名前を、そのステップの `outputs_as` で付け替えてから重ねる |
| `_step_def` | `progress/definition.py` | 変更 | 変えない | `outputs_as` を `StepDef` へ写す |
| `_outputs_as_errors` | 同上 | 新設 | `(doc: Mapping[str, Any]) -> list[str]` | 付け替え先が工程の `outputs` に無ければ誤りにする |
| `check_contracts` | 同上 | 変更 | 変えない | `_outputs_as_errors` の誤りを足す |

- 工程の出力に `design_pr` を宣言するので、`ndf progress check`・`finish` は工程「ドキュメントレビュー」の出力にも `design_pr` を求める。手で終えるときは、マージした設計 PR の番号を渡す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_stage_output` | `design_pr` が出ない | `docs-merge` が番号を返さない | `validate_stage_output`（今の検査） | 止める（工程を `bad_report` で失敗にする） | 「文脈のキー design_pr が無い」 | 工程を打ち直すと `probe` が番号を返す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_outputs_as_errors` | `definition._context_key_errors` | 定義を読んだ | 別の検査（キーの対応）なので使えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_stage_output` | `docs-merge` の `pr` が `design_pr` になる | `docs-pr` の `pr` は残らない | 文脈に `pr` が書かれない |
| `_outputs_as_errors` | 宣言した付け替え先は通る | `outputs_as` が無いステップ | 宣言に無い付け替え先は誤り |
| `ci_cli._merge` | 成功の結果の `outputs.pr` が `items` の番号と同じ | — | 止まった結果は `outputs` を持たない |
| `merge.json` の `probe` | head が同じマージ済みの PR の番号を返す | 同じブランチに head の違うマージ済みの PR があり、まだ済んでいないと返す | `gh` の失敗で結果を出さない |

## 事象のコントラクトと行の触る場所の畳み込み

満たす決定: 決定 4・7、受け入れ条件 #4295-1・#4295-5

`progress.json` の `events` に次の 3 件を足す（`level` は `plan`、`actors` は `["workflow"]`、`from`・`state` は持たない）。中身の形は設計文書の「データ構造」にある。

| `kind` | `scope` | 行のキー |
| --- | --- | --- |
| `touches_found` | `row` | レーンの名前 |
| `touches_retry` | `row` | レーンの名前 |
| `touches_compared` | `table` | 無し |

`touches_compared` の `seen` のキーは、ステートマシンの ID である。事象の番号はステートマシンごとに振られ、行の追加と開始（`row_added`・`row_started`）はレーンのステートマシンの事象である。そのため、計画の ID に加えて、比べたレーンのステートマシンの ID も同じ形で並べる。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `LaneSpec.touches_from_design` | 型の項目 | `progress/lane/types.py` | `False` |
| `RowTouches(design_seq, design, changed, read, pulls, final_seq)` | 型。`design` は `Optional[tuple]`（`files: null` は `None`）、`read` は読んだ（リポジトリ, PR, マージ先, head）の集合。`pulls` は行へ振り分けた（リポジトリ, PR）の集合 | 同上 | 空 |
| `ScopeChange(seq, lane, row, files, after)` | 型。触る場所か `after` を変えた事象 1 件。`row` が `None` ならレーンの設定の変更。変えていない項目は `None` | 同上 | — |
| `PlanView.touches` | （レーン, 行）→ `RowTouches` | 同上 | 空 |
| `PlanView.scope_log` | `ScopeChange` の並び（事象の順） | 同上 | 空 |
| `PlanView.retries` | （レーン, 行, `source`）→ 失敗の回数 | 同上 | 空 |
| `PlanView.seen`・`PlanView.design_overlaps`・`PlanView.places` | 最後の `touches_compared` の値。`places` はほかの計画の所在（`plan_id`・`cwd`・`plan`） | 同上 | 空 |
| `PlanView.design_modes` | 工程 `document-review` を持つモードの名前の集まり | 同上 | 空 |
| `LaneRow.added_seq`・`started_seq`・`started_at`・`ended_at` | 型の項目。レーンのステートマシンの事象の番号と時刻 | 同上 | 0 か `None` |
| `DEFAULT_MODE` | 定数。`launch.py` から移す | 同上 | `standard` |

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `mode_of` | `progress/lane/types.py` | 新設 | `(row: LaneRow) -> str` | 行の `args` の `--mode`（`--mode=` の形を含む）。無ければ `DEFAULT_MODE` |
| `_fold_touches` | `progress/lane/fold.py` | 新設 | `(view: PlanView, ev: model.Event) -> PlanView` | 新しい 3 つの事象を畳み込む |
| `_fold_plan_event` | 同上 | 変更 | `(view: PlanView, ev: model.Event) -> PlanView` | 下書きだけでなく番号を受け、`lane_added`・`lane_changed` の触る場所と `after` を `scope_log` に積む |
| `fold_plan`・`_spec`・`_row_from` | 同上 | 変更 | 変えない | 上の新しい項目を埋める |
| `read_plan` | `progress/lane/__init__.py` | 変更 | 変えない | `design_modes` に `definition.modes_with_stage("document-review")` を載せる |
| `modes_with_stage` | `progress/definition.py` | 新設 | `(stage: str) -> frozenset[str]` | 工程の文書 `stage` を呼ぶモードの名前の集まり |
| `lane_payload` | `progress/plan/__init__.py` | 変更 | 変えない | 印があれば `lane_added` の値に `touches_from_design` を載せる |

- `_fold_touches`: `source: design` は行の `design`・`design_seq` を置き換え、`scope_log` に積む。`source: diff` は `files` の和を `changed` に、`from` を `read` に、`pulls` を `RowTouches.pulls` に足し、`final: true` の記録（`files: null` を含む）で `final_seq` を入れる。`files: null` の `diff` は `changed` を消さない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_fold_touches` | 計画に無いレーンの事象 | 起きない（書き手がレーンを読んでから書く） | — | そのまま持つ（読み手は引かない） | 無し | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `mode_of` | `launch.py` の `--mode` の扱い | `grep -n "DEFAULT_MODE\|--mode" src/ndf/progress/lane/launch.py` | 有無を見るだけで値を取り出さないので新設する |
| `_fold_touches` | `fold._fold_plan_event` | 定義を読んだ | 同じ畳み込みから呼ぶ |
| `modes_with_stage` | `definition.modes`・`load_definition` | 定義を読んだ | この 2 つを使って組む |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_fold_touches` | 差分の事象 2 件の和 | `files: []` の最後の観測で `changed` が消えない | `files: null` の設計の記録は `design` が `None` で `design_seq` あり |
| `mode_of` | `--mode light` | `--mode=light` | `--mode` が無ければ `standard` |

## 計画の決まり

満たす決定: 決定 3・4・5・7、受け入れ条件 #4295-2・#4295-3・#4295-6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `designing` | `progress/lane/rules.py` | 新設 | `(view: PlanView, lane: str, row: LaneRow) -> bool` | 設計中の行（I1 の 4 条件）か |
| `row_scope` | 同上 | 新設 | `(view: PlanView, lane: str, row: LaneRow) -> tuple[str, ...]` | I1 の表で行の触る場所を返す |
| `overlap_pairs` | 同上 | 新設 | `(own: PlanSide, others: Sequence[PlanSide], dirty: Collection[RowRef]) -> list[Overlap]` | 「重なりの知らせの組の規則」で、比べ直す組の重なりを求める |
| `ordered_since` | 同上 | 新設 | `(view: PlanView, lanes: Lanes, a: RowRef, b: RowRef, seq: int) -> bool` | 事象 `seq` より後の `after` の変更を順に当て、途中で 2 つの行の間に前後の鎖があったかを返す |
| `touches_wait` | 同上 | 新設 | `(view: PlanView, lanes: Lanes, lane: str, key: str) -> list[str]` | 行が実装の入口で待つ先を返す（I8・決定 3） |
| `_touch_hits` | 同上 | 変更 | `(a: Sequence[str], b: Sequence[str]) -> list[str]` | レーンの型でなく、触る場所の並びを受ける |
| `_touch_errors` | 同上 | 変更 | 変えない | 同時に動き得る行の組ごとに `row_scope` を比べ、設計中の行を外す。誤りの文の形は今のまま |
| `plan_complete` | 同上 | 変更 | `(view: PlanView, lanes: Lanes, require_final: bool = False) -> bool` | `require_final` なら、始まったスプリントの行すべてに `final_seq` があることを足す（I6） |

- `row_scope`: 設計の記録が一覧（空を含む）で、かつ「終わっていない行で、記録より後にレーンの触る場所の設定を変えた」に当たらなければ記録、ほかは設定
- `overlap_pairs`
  - 相手の行の範囲（同じ計画は前後の鎖の無い終わっていない行、別の計画は実行中の行、終わった行を残す条件と比べる期間）は設計文書の「重なりの知らせの組の規則」のとおり。終わった行の前後は事象の時刻（`started_at`・`ended_at`）で比べる
  - 同じ計画の組は名前の順で先の行を自分の行にして 1 件にする
  - リポジトリは `PlanSide.repos` の名前が等しいかで比べる。クローンのパスは比べない
  - 重なりは `touch_hit` で見る。`files` に載せる名前は、当たった 2 つの値のうち長い方（同じ長さなら辞書順で後）にし、向きを入れ替えても同じにする
  - `Overlap.scope_files` は触る場所どうしの比べ方で当たった分。設計中の行は触る場所どうしの比べ方から外し、変えたファイルとの比べ方では設定の値を使う
- `ordered_since` の鎖は `_order_graph` で作る。行の状態は今の値を使い、`after` だけを時点ごとの値にする。別の計画の組は偽
- `touches_wait` の戻り値は次の 3 つを持つ。空なら待たない
  - `touches_design`: 設計中の行（記録が無い）
  - `touches_compare`: 設計中の行だった行で、`view.seen[view.machine_id]` が行の `design_seq` より小さい
  - `touches_overlap:<リクエストのキー>`: `design_files` が空でない開いた知らせの、`lane`・`row` か `with` に載る
- 実装の待ちの判定 `rules.implementation_wait` は #4050 が作る。#4050 の実装の後に、その戻り値へ `touches_wait` の戻り値を足す 1 行を加える。この文書の他の関数は #4050 の形に依らない

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `designing` | 印あり・`kind: sprint`・`mode_of(row)` が `design_modes` にある・`design_seq` が無い | 真 |
| `designing` | 上の 4 つのどれかが欠ける | 偽 |

- 計算量の注意: 行の組の総当たりである。行は 1 計画で数十までなので、そのまま比べる

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `RowRef(plan, lane, row)` | 型 | `progress/lane/rules.py` | — |
| `PlanSide(view, lanes, repos)` | 型。`repos` はレーンから `<所有者>/<リポジトリ>` への対応。読み手の `compare` が埋める | 同上 | 空 |
| `Overlap(own, other, files, scope_files)` | 型 | 同上 | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `designing`・`row_scope` | 無し | `rules.py` の全関数を読んだ | 新設する |
| `overlap_pairs` | `_order_graph`・`_concurrent`・`touch_hit`・`_same_repo` | 同上 | 前の 3 つを使う。`_same_repo` はクローンのパスを比べるので使わず、`PlanSide.repos` の名前を比べる |
| `touches_wait` | `PlanView.open_requests` | `types.py` を読んだ | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `designing` | 印ありの既定のモード | `--mode light`、開発ループの行は偽 | 印なしは偽 |
| `row_scope` | 記録の一覧 | 空の一覧は設定へ落とさない。記録の後の `set --touches` は設定 | `files: null` は設定 |
| `overlap_pairs` | 3 つの比べ方 | 終わった行を相手に残す 2 条件。リポジトリ違いは比べない。クローンのパスが違っても名前が同じなら比べる | 後で始まった行とは比べない |
| `touches_wait` | 待つ 3 つの印 | `with` にだけ載る行 | `source: diff` の知らせだけなら空 |
| `_touch_errors` | 記録のファイルが別なら通る | 設計中の行を外す | 印なしは `test_touch_overlap_errors_as_they_are_now` のまま |
| `ordered_since` | `after` を足して外した組は真 | `seq` より前に外した順序は偽。間のレーンを通した鎖も真 | 別の計画の組は偽 |
| `plan_complete` | `final_seq` があれば真 | 始まらずに中止した行は求めない | 無ければ偽 |

## 触る場所の観測

満たす決定: 決定 3・6・7、受け入れ条件 #4295-1・#4295-4・#4295-6

GitHub の読み出しと DB の書き込みは `plan/touches.py`、判断は `plan/touches_decide.py` に置く。`run.py` は耐久ステップ 1 つで呼ぶだけにする。

### 耐久ワークフローへの組み込み

- `Policy` に `touches_every: int = 0` を足す。値は `ceil(interval / poll)`（1 以上）で、`spec_of` が耐久ワークフローの入力へ書く
- `decide` は、`Observe` の結果を受けたとき、次の 3 つがそろえば `Touches` の手を返す
  - `touches_every` が 0 でない
  - スナップショットの計画の状態が、生死を判定する状態（`WATCHED_STATES`）である
  - 読み直しの回数が `touches_every` の倍数（最初の回を含む）である
- `Touches` の結果を受けたら、今の `_after_observe` へ進む
- 前の版が始めた耐久ワークフローの入力は `touches_every` を持たない。0 になり、記録した呼び出しの順は変わらない。その計画の完了は I6 を求めない（`require_final` は入力の `touches_every` が 0 でないときだけ真）。観測は、計画を区切りで止めて再開した次の耐久ワークフローから働く
- `lanes_workflow` の本体が変わるので `durable.FORMAT_SOURCE_HASH` を書き直す。呼び出しの順は上のとおり変わらないので `FORMAT` は上げない

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `policy`・`spec_of`・`lanes_workflow` | `progress/plan/run.py` | 変更 | 変えない | `touches_every` を渡す |
| `snapshot_of` | 同上 | 変更 | `(view, rows, found=None, moved=None, spent=(), require_final=False) -> dict` | 完了の判定へ `require_final` を渡す |
| `observe`・`complete` | 同上 | 変更 | 変えない | 入力から `require_final` を決める |
| `observe_touches` | 同上 | 新設 | `(spec: dict[str, Any]) -> dict[str, Any]` | 耐久ステップ。`touches.observe_cycle` を呼び、件数の要約を返す |
| `_perform` | 同上 | 変更 | 変えない | `Touches` を `observe_touches` へ振る |
| `decide` | `progress/plan/run_decide.py` | 変更 | 変えない | 上の組み込み |
| `complete_plan` | `progress/plan/__init__.py` | 変更 | `(conn, pid: str, require_final: bool = False) -> bool` | 完了の判定へ渡す |
| `record_touches` | 同上 | 新設 | `(conn, pid: str, drafts: list[model.EventDraft]) -> None` | 計画の排他を取り、観測の事象を追記する |
| `other_views` | `progress/lane/machine.py` | 新設 | `(own_id: str, places: Iterable[tuple[str, str]]) -> list[PlanSide]` | ほかの計画の畳み込みを返す。読めない計画は飛ばす |
| `observe_cycle` | `progress/plan/touches.py` | 新設 | `(spec: dict[str, Any], memory: Memory) -> dict[str, Any]` | 観測の 1 周（設計の記録 → 差分の記録 → 比較） |
| `record_designs` | 同上 | 新設 | `(ctx: Cycle) -> None` | 不変条件 I1 の 1〜3 を満たし、ドキュメントレビューが済んで記録の無い行へ、設計の触る場所を記録する |
| `record_diffs` | 同上 | 新設 | `(ctx: Cycle) -> None` | PR の一覧の差分を読み、読み残しの組の変えたファイルを記録し、最後の観測を書く |
| `compare` | 同上 | 新設 | `(ctx: Cycle) -> None` | 比べ直す組の知らせと比較済みの事象番号を 1 つのトランザクションで書く（I7） |
| `gh_api` | 同上 | 新設 | `(cwd: Path, path: str, *flags: str) -> str` | `gh api` を打ち、標準出力を返す |
| `assign` | `progress/plan/touches_decide.py` | 新設 | `(pulls: Sequence[Pull], rows: Mapping[RowKey, RowTarget]) -> dict[RowKey, list[Pull]]` | base か（同じリポジトリの）head のブランチか、行へ振り分け済みの番号（`RowTarget` が持つ `pulls`）で、PR を行へ振り分ける |
| `pick` | 同上 | 新設 | `(pending: Sequence[Pending], limit: int) -> list[Pending]` | 終わった行を先に、残りは更新の古い順に、上限まで選ぶ |
| `is_final` | 同上 | 新設 | `(listed: bool, since: Optional[datetime], ended_at: datetime, remaining: int) -> bool` | I6 の 3 条件。`since` はこの周で進める前の値 |
| `counted` | 同上 | 新設 | `(failures: int, failed: bool, attempts: int) -> tuple[Optional[int], bool]` | 書く失敗の回数（書かなければ `None`）と、上限に届いたか（I9） |
| `notices` | 同上 | 新設 | `(view: PlanView, overlaps: Sequence[Overlap], dirty: Collection[RowRef], reorderable: Mapping[RowRef, bool], reordered: Collection[tuple[RowRef, RowRef]]) -> Notices` | I4 で閉じる知らせ・開く知らせの中身・`design_overlaps` を決める |

- `observe_cycle`
  - 事前条件: 計画が動いている
  - 観測の対象は、`kind: sprint` でスプリントを持つ行のうち、実行中か「終わって `final_seq` が無い」もの
  - 行のスプリントの定義と事象を読み、工程「ドキュメントレビュー」が終了か、文脈の `branch`・`design_pr`、段階 `implement` の工程に入ったか（未着手と無効以外の行があるか）を得る
  - 行のリポジトリは、レーンの `repo`（クローンの絶対パス）か、無ければ耐久ワークフローの作業ディレクトリ。`<所有者>/<リポジトリ>` は `gh repo view --json nameWithOwner` でクローンのパスごとに 1 回だけ読み、`Memory` に持つ。`compare` はこの値をほかの計画のレーンのクローンにも使う。ほかの計画のレーンで `lane_repo` が無ければ、その計画の所在の `cwd` をクローンとする
  - 副作用: `gh`、計画のステートマシンへの追記
- `record_designs`: 対象は、レーンに印があり、`kind: sprint` で、`mode_of(row)` が `design_modes` にある行（`designing` の条件のうち記録の有無を除いた 3 つ）。ほかの行は設計の資料を読まず、記録も書かないので、`row_scope` は設定の値を返す
- `record_diffs`: `assign` の結果のうち `PlanView` の行の `pulls` に無い PR は、ファイルを読めたかに依らず、その周の `touches_found` の `pulls` に載せる。読み終えた組が無ければ `files: []`・`from: []` で書く
- `record_diffs`: PR の一覧の読み方、読み残し、ファイルの読み方と head・マージ先の読み直し、一覧の読み取り済み時刻は、設計文書の決定 6 のとおり。走査の開始の時刻は `gh api -i` の応答の `Date` ヘッダから取る。1 周の 1 リポジトリの追記は、行ごとに 1 件の `touches_found` にする
- `compare`: ほかの計画は、マシンの索引と中継の登録簿の所在に、記録した所在（`PlanView.places`）を足して読む。計画が完了すると索引から外れるため、終わった行の最後の観測を、比べる前に見失わないようにする。再起動で消えないように、所在は `touches_compared` の `places` に書く
  - `places` に無い計画を読んだ周は、重なりが無くても `touches_compared` を書く
  - 完了した計画は、その最後の事象まで比べ終えた周の `places` から外す。読めない所在は残して次の周で読み直す
- `notices`
  - 「新しく重なり始めたファイル」は、`overlaps` の `scope_files` のうち、次のどちらかに当たるもの
    - `view.design_overlaps` の同じ組に無い
    - `view.scope_log` の比較済みより後の変更を行ごとに順に当てると、途中のどこかで行の触る場所がそのファイルを含まなかった
    - 比較済みより後に、組の間に前後の鎖があった時点がある（呼び手の `compare` が `ordered_since` で求め、`reordered` の組の集まりとして渡す）
  - `reorderable` は、組の両端が同じ計画のときだけ、未着手か、実行中で段階 `implement` に入っていない行のレーンを載せる。別の計画の組は空にする
  - 比べ直さなかった組の `design_overlaps` は引き継ぎ、重なりが無くなった組と、前後の鎖ができて相手から外れた組は `files: []` にする。`after` を変えた事象が比較済みより後にあれば、`compare` は自分の計画の行をすべて `dirty` にする

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `gh_api` | GitHub を読めない | `gh` の非 0 終了、出力が形に合わない | `gh_api` | `FetchError` を投げる | `run.log` に 1 行 | 次の周で読み直す |
| `record_designs` | 取得の失敗 | `DocsUnreadable`・`FetchError` | `record_designs` | 再試行（`touches_retry`。上限で `files: null`） | 同上。`files: null` の理由は事象に残る | 回数は DB にあり、再起動をまたぐ |
| `record_designs` | 必要な記載が無い、`design_pr` が無い、未マージ | `Scope.files` が `None` など | 同上 | `files: null` を 1 度だけ書く | 事象の `reason` | 同じ行は読み直さない |
| `record_diffs` | 一覧・ファイル・head とマージ先の読み直しの失敗、head かマージ先の変化 | `FetchError`、head かマージ先の不一致 | `record_diffs` | 読み残しに残して再試行。終わった行は I6 の表の区分で失敗に数える | `run.log` に 1 行 | 次の周で読み直す |
| `compare` | クローンのリポジトリの名前を読めない | `gh repo view` の失敗（`FetchError`） | `compare` | 自分の計画のクローンなら `touches_compared` を書かない。ほかの計画のクローンならその計画を飛ばし、`seen` を進めない | `run.log` に 1 行 | 次の周で読み直す |
| `compare` | ほかの計画を読めない | DB が無い・形が違う | `other_views` | その計画を飛ばして続ける | `run.log` に 1 行 | 次の周で読み直す。比較済みの事象番号を持たないので、読めた周に比べる |
| `observe_cycle` | 書き込みの衝突・断り | `store.Conflict`・`store.StoreBusy`・`model.Refused`（計画が止まった） | `observe_cycle` | この周を打ち切る | `run.log` に 1 行 | 比較済みの事象番号が進まないので次の周で同じ比較をする |

どの失敗でも耐久ワークフローは止めず、レーンの起動と生死の判定は続ける。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `parallel-run.touches` | 設定 | `contracts/limits/parallel-run.touches.json` | `interval: 300`、`fetches: 20`、`attempts: 3`。`read_by` は `ndf.progress.plan.run`・`ndf.progress.plan.touches` |
| `Memory` | 型。リポジトリごとの一覧の読み取り済み時刻、行ごとの読み残し、クローンのパスごとのリポジトリの名前 | `progress/plan/touches.py`（モジュールの中の、計画の ID ごとの辞書） | 空。プロセスを起動し直すと消え、最初の周が決定 6 のとおり作り直す |
| `ApiDocs(cwd, repo, commit)` | `Docs` の実装。`git/trees` で `issues` の一覧、`contents/<パス>?ref=<commit>` を `Accept: application/vnd.github.raw+json` で本文 | 同上 | — |
| `Touches` | 手の型 | `progress/plan/run_decide.py` | — |
| `RunState.observed` | 読み直しの回数 | 同上 | 0 |
| `touches_overlap` | リクエストの `kind` の定数 | `progress/lane/rules.py`（`touches_wait` と `notices` が読む） | — |

`attempts: 3` は `parallel-run.launch` の打ち直しの回数と同じ値である。`gh api --paginate` は複数のページの配列を 1 つの配列にして返す（`gh` 2.105.0 で確かめた）。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 観測と、オーガナイザーの `ndf lanes set`・`request close` | 計画のステートマシン | 追記は `lane.lock_plan` と事象の番号の確かめ（`append_events`）。`compare` は同じトランザクションで読み直してから書く | 読んだ後に変わっていれば衝突になり、次の周でやり直す |
| 観測と、レーンの conductor の行の開始・終了 | レーンのステートマシン | 観測は読むだけ | 比較の後に始まった行は、`seen` に無い番号なので次の周で比べる |
| 観測の途中の中断 | 事象の追記 | — | `from` に載った（リポジトリ, PR, マージ先, head）は読み直さない。知らせは I4 の突き合わせで重ならない |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `observe_cycle` | 読めなかった対象（行・PR の番号・理由の 1 行）、飛ばした計画 | 標準エラー（切り離した実行では `run.log`） | 認証の値、本文の中身 |
| `observe_touches` | 記録した件数・知らせの件数 | 耐久の記録（ステップの戻り値） | ファイルの一覧 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `observe_touches` | `run.py` の `observe`・`report` | 定義を読んだ | 同じ耐久ステップの形にする |
| `record_touches` | `plan/__init__.py` の `_append`・`lane.lock_plan` | 同上 | 2 つを使う |
| `other_views` | `machine.discover`・`indexed`・`read_lanes` | 定義を読んだ | 所在の解き方を使う。`read_lanes` はレーンの名前だけを返すので、畳み込みを返す関数を足す |
| `observe_cycle`・`record_designs` | `design_results.scope_of`、`command.read_current`、`model.fold` | 定義を読んだ | 使う |
| `record_diffs`・`gh_api` | `adapters/github.GitHub.rest` | `adapters/github.py` の関数の一覧を読んだ | 使わない。設計文書が `gh api` で読むと定め、`Date` ヘッダを要る |
| `compare` | `table.open_request`・`close_request` | 定義を読んだ | 使う |
| `assign`・`pick`・`is_final`・`counted`・`notices` | `run_decide.py`、`rules.py` | 全関数の一覧を読んだ | 当たる部品は無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `decide` | 5 回に 1 回 `Touches` | `touches_every` が 0 なら返さない | 止めた計画では返さない |
| `snapshot_of`・`complete_plan` | `final` があれば完了 | 入力に `touches_every` が無ければ求めない | 無ければ完了しない |
| `observe_cycle` | `gh` を差し替えて 1 周の記録と知らせ | 打ち直しで知らせが増えない | 追記の衝突で打ち切り、次の周で比べる |
| `record_designs` | 設定を置き換える。印の無い行は、設計 PR がマージ済みでも読まず、比較は設定の値になる | 上限の 1 回前の失敗と再起動 | 記載が無ければ 1 度だけ `files: null` |
| `record_diffs` | ファイルと名前を変えた元のパス | 101 件の一覧、取得の上限 | head の変化、読む間のマージ先の変化、記録の後にマージ先だけが変わった PR は読み直す、読み残しの間にマージ先を変えて `Memory` を空にしても同じ行で読む、ページの失敗 |
| `compare` | 知らせと `seen` が同じトランザクション | 相手を 1 回読んだ後に `Memory` を空にし（再起動）、相手が完了して索引から消えても、記録の所在から最後の観測を読んで知らせを開く | 読めない計画を飛ばす |
| `gh_api` | 標準出力を返す | `-i` のヘッダと本文の分離 | 非 0 で `FetchError` |
| `record_touches` | 追記する | — | 番号が違えば `Conflict` |
| `other_views` | 自分の計画を除く | 同じ計画の 2 つの所在は 1 回 | DB の無い所在を飛ばす |
| `assign` | base と head の振り分け。振り分け済みの PR は、マージ先を変えても同じ行 | `doc_prs: base` の設計 PR | フォークの同名ブランチは振り分けない |
| `pick` | 終わった行が先 | 上限ちょうど | — |
| `is_final` | 3 条件で真 | `since` が `ended_at` と同じなら偽 | 一覧を読めない周は偽 |
| `counted` | 失敗で 1 増える | 成功は、前に失敗があるときだけ 0 を書く | 上限で真 |
| `notices` | 新規・追加・開き直し。別の計画の組は `reorderable` が空 | 観測を挟まない再発、回答済みの継続 | 同じ重なりでは何も書かない |

## 計画の書き方・規約と、オーガナイザーの手順

満たす決定: 決定 3・7・9、受け入れ条件 #4295-2・#4295-5

| ファイル | 変える中身 |
| --- | --- |
| `contracts/workflow/parallel-plan.schema.json` | `$defs/lane` に任意の `touches_from_design`（真偽値）を足す |
| `skills/issue-plan-strategy/references/parallel-plan.md` | キーの表に `touches_from_design` を足す。ドキュメントレビューの工程を持つモードのスプリントのレーンに付けること、`touches` には予想する範囲を書くこと、粗い範囲の重なりを避ける `after` を付けないことを書く |
| `skills/development-workflow/references/parallel-checks.md` | I13 の行を、行の触る場所（設計の記録かレーンの設定）を比べ、設計中の行を外す、に直す |
| `skills/development-workflow/references/parallel.md` | 知らせの表に `request_opened`・`touches_overlap` を足す。`reorderable` のレーンへ `after` を足すか、そのまま進めて base-sync で直すかを決めて閉じる。`reorderable` が空の組は base-sync で直す |
| `skills/design/references/design-template.md` | 触るファイルの列の決まりに `` `<パス>::<名前>` `` の書き方（1 つの名前を 1 つのバッククォート）を足す |
| `skills/development-workflow/references/agent-layers.md` | レビューの単位のプランを組むとき `--add design_pr=<番号>` を渡すと `touches` が補われ、渡さないと直列になる、を足す |
