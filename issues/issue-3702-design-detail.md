# #3702 の詳細設計: 単位のファイル・承認の記録・単位の並列・設計のフェーズの工程の並び

[設計書](./issue-3702-design.md)の構成要素を、ファイルと関数の粒度へ下ろした文書である。決定の番号、不変条件の番号（I1〜I8）、規則の番号（U1〜U6・W1）は設計書を指す。コマンドの引数の意味と結果、処理の流れ、単位のファイルの形は設計書が持つので、写さずにセクション名で指す。パスは `plugins/ndf/` からの相対である。

「supervisor」は、工程を回す AI である。この文書の「プラン」は、手順をステップの列にした実行の定義で、実装計画とは別である。「単位の欄」は、実装計画の「レビューの単位」の欄を指す。「MVV 判定」は、プロジェクトの方針に沿うかの AI の判定である。

#4369 は、`ndf progress next` の工程「実装」が、単位の表を読んで単位を 1 つずつ流すようにする課題である。単位は、レビューの単位（1 本の PR として実装・レビュー・マージするタスクのまとまり）を指す。**前提にする #4369 の実装は、2026-11-27 の時点で `main` に無い。** この文書が「#4369 の」と書く関数とファイルは、[#4369 の設計](./issue-4369-design.md)と、レビュー中の[詳細設計の PR](https://github.com/devbasex/ai-plugins/pull/4567) の名前である。実装を始める時点で `main` の形が違えば、名前と引数を `main` に合わせる。

## 地図

### ディレクトリと依存の向き

```text
src/ndf/
├── core/plan/{units_decide*, units_files*, cli, inputs_decide, queue_decide, review_units_decide}.py
├── progress/{design_record*, units_invoke, units_queue*, units_convert*, units_sync*, units_guard*, units_cli*}.py
├── progress/{invoke, run, folding, definition, command, store}.py
├── progress/gate/{entry, answering}.py
├── adapters/github.py
├── ci_cli.py
└── domains/{crossreview/{cli,drive,settings}.py, records/{cli,mvv_gate}.py}
contracts/
├── workflow/review-units.schema.json*
├── workflow/stages/{design, document-review, plan, plan-design*, implement}.json
├── workflow/{modes/{standard,legacy-refactor}.json, context.schema.json}
├── templates/{design-pr-review*, docs-ci*, design-merge-approved*, impl, implementation-plan, params}.json
├── limits/review-units.{target-lines*, max-lines*}.json
├── schemas/{project-workflow.schema.json, ndf-settings/project.schema.json, io/}
├── worker/kinds.json
└── domains/env.json
```

`*` は新設するファイルである。

依存の向きは、層の決まり（`contracts` → `adapters` → `core` → `progress` → `domains`。後ろが前を読む）のとおりで、循環は無い。

- `units_decide` は標準ライブラリだけを読む
- `units_files` は `units_decide`・`inputs_decide`・`adapters` を読む
- `progress/design_record` は `core` と `progress/model`・`folding` だけを読む。読む側は `run`・`gate`・`units_invoke` である
- `run` が `gate` を読むと循環するので、承認の記録は `gate/` の下に置かない

1 ファイル 1,000 行以下の決まりがある。上限に近いファイルは次の 3 つである（行数は 2026-11-27 の `main`）。

| ファイル | 今の行数 | この変更での扱い |
| --- | ---: | --- |
| `progress/run.py` | 965 | 呼び出しだけを足す（25 行まで） |
| `progress/command.py` | 987 | サブコマンドの登録だけを足す（10 行まで） |
| `adapters/github.py` | 1,000 | #4369 が分ける |

### ファイルごとの役割

| パス | 種類 | 役割 | 依存する先 | 最上位の関数 |
| --- | --- | --- | --- | --- |
| `src/ndf/core/plan/units_decide.py` | 新設 | 実行順・導く値・違反・警告・分け方の案を返す | `graphlib` | `parse`・`judge`・`split`・`single` |
| `src/ndf/core/plan/units_files.py` | 新設 | 単位のファイル・実装計画・上限を読み、表を作る | 上・`inputs_decide`・`git`・`contracts` | `find`・`load`・`limits_of`・`collect`・`check`・`render_tables`・`differs` |
| `src/ndf/core/plan/cli.py` | 変更 | `ndf plan units render`・`check` | `units_files` | `_cmd_units` |
| `src/ndf/core/plan/inputs_decide.py` | 変更 | 「設計の結果」の表の「扱い」と「統合先」の列を読む。`design_gaps` を消す | — | `design_handlings`（新設） |
| `src/ndf/core/plan/queue_decide.py` | 変更 | `RESUME_ALWAYS` に `guard` を足す | — | — |
| `src/ndf/core/plan/review_units_decide.py` | 変更 | #4369 の `Unit` に並列で使う欄を足す | — | — |
| `src/ndf/progress/design_record.py` | 新設 | 承認の記録を組む・照らす・読む | `units_files`・`folding` | `build`・`stale`・`reset_drafts`・`approved_of`・`required`・`mismatches` |
| `src/ndf/progress/units_invoke.py` | 変更 | 単位の読み元を単位のファイルにし、承認と照らし、並列に流す | 下の 3 つ・`units_files` | `prepare`・`run_stage`（変更） |
| `src/ndf/progress/units_queue.py` | 新設 | マージキューで待つ単位の状態を読む | `github` | `read`・`action` |
| `src/ndf/progress/units_convert.py` | 新設 | この変更より前の表を単位のファイルへ変換する | `review_units_decide`・`units_files` | `convert`・`compare` |
| `src/ndf/progress/units_sync.py` | 新設 | 送り先を確かめ、承認した文書をスプリントブランチへ取り込む | `git`・`github`・`design_record` | `destination_errors`・`sync` |
| `src/ndf/progress/units_guard.py` | 新設 | 実装のプランを通すかを決める | `store`・`design_record` | `record_launch`・`guard` |
| `src/ndf/progress/units_cli.py` | 新設 | `ndf progress units run`・`sync`・`guard` | 上の 5 つ | `add_parsers`・`dispatch` |
| `src/ndf/progress/invoke.py` | 変更 | 承認の記録の値と保存先を差し込み、単位の経路を呼ぶ | `design_record` | `template_values`・`_approved_target`（変更） |
| `src/ndf/progress/run.py`・`folding.py` | 変更 | MVV 判定で人の承認を省いた回答に記録を付け、記録が古ければステップを未了に戻す | `design_record` | `_consume`・`_step_draft`・`approved_seen`（変更） |
| `src/ndf/progress/definition.py` | 変更 | そのモードに判定のステップが無い工程の出力から `mvv_gate` を外す | — | `_stage_def`（変更） |
| `src/ndf/progress/store.py` | 変更 | リポジトリのスプリントの一覧を読む | — | `read_machines`（新設） |
| `src/ndf/progress/gate/entry.py`・`answering.py` | 変更 | 資料を出す前と回答の確定の前に記録と照らす | `design_record` | `present`・`answer`・`recheck`（変更） |
| `src/ndf/ci_cli.py` | 変更 | `ndf ci merge --verify-merged` | — | `_merge_pull`（変更） |
| `src/ndf/adapters/github.py` | 変更 | `pulls` に REST への読み替えを止める指定 | — | `pulls`（変更） |
| `src/ndf/domains/crossreview/{cli,drive,settings}.py` | 変更 | `--reviewed-base-out` | — | `_sync`・`_reusable`・`cmd_run`（変更） |
| `src/ndf/domains/records/{cli,mvv_gate}.py` | 変更 | 設計の判定の入力を、2 つの工程のどちらの形でも受ける | — | `_env_inputs`（変更） |

## 単位の判定（`core/plan/units_decide.py`）

満たす決定: 決定 4・5・6・8、受け入れ条件 2・5・7・8、I1〜I4。ファイル・git・環境変数を読まない。

### 型

| 型 | 欄 |
| --- | --- |
| `Task` | `id`（`T<n>`）・`title`・`issue`・`files: tuple[str, ...]`・`lines: Optional[int]`・`criteria: tuple[str, ...]`・`after: tuple[str, ...]` |
| `Edge` | `unit`・`reason`・`issue: Optional[int]`（別のファイルの単位を指すとき） |
| `UnitDef` | `name`・`tasks: tuple[str, ...]`・`after: tuple[Edge, ...]`・`actual_lines: Optional[int]` |
| `UnitFile` | `path`・`issues: tuple[int, ...]`・`tasks: tuple[Task, ...]`・`units: tuple[UnitDef, ...]`・`whole: bool`（実装計画を省いた課題を 1 単位にしたかのフラグ） |
| `Limits` | `target`・`max` |
| `View` | `file`・`name`・`order`・`merge`・`after`（解決済みの `(file, name, reason)`）・`issues`・`tasks`・`criteria`・`lines`・`touches` |
| `Finding` | `rule`・`unit`・`reason` |
| `Proposal` | `unit`・`units: tuple[UnitDef, ...]`・`resplit: tuple[str, ...]`（切り直すタスク）・`remaining: tuple[Finding, ...]` |
| `Judgement` | `views`・`violations`・`warnings`・`proposals` |

`UnitDef` は #4369 の `review_units_decide.Unit`（流すときの単位）と別の型である。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `parse` | `units_decide.py` | 新設 | `(doc: Mapping, path: str) -> UnitFile` | スキーマを通った辞書を型にする。`issue` を省いたタスクはファイルの唯一の課題 |
| `judge` | 同上 | 新設 | `(files: Sequence[UnitFile], limits: Limits, criteria: Mapping[int, int]) -> Judgement` | 全ファイルを 1 つのグラフにし、違反・警告・導く値・案を返す |
| `split` | 同上 | 新設 | `(files, key: tuple[str, str], limits) -> Proposal` | 超えた単位を、依存の順の直列の単位に区切る |
| `single` | 同上 | 新設 | `(issue: int) -> UnitFile` | 課題全体を 1 つの単位にする。名前は `issue-<番号>`、`lines` は無し、`touches` は空、`whole` は真 |

### 処理の手順

`judge` は次の順に進む。参照の解決に失敗した時点（U1）でも、残りの規則は評価できる範囲で続ける。

1. 単位を `(ファイルのパス, 単位の名前)` の組にする。`Edge.issue` は、その課題を `issues` に持つファイルを探すためだけに使う
2. タスクのグラフ（ファイルごと）と単位のグラフ（全ファイル）を `graphlib.TopologicalSorter` に入れる。`CycleError` は U1 にする
3. 単位のグラフを並べた順に、実行順を「先に要る単位の実行順の最大 + 1」（無ければ 1）で決める
4. 同じ順に、各単位の閉包（間接を含む先に要る単位の集合）を求める
5. 下の表の規則を当てる
6. `merge` は、実行順、同じ実行順の中は `(単位の最小の課題の番号, ファイルの中の並び)` の順に 1 から振る
7. U6 の単位すべてと、W1 のうちタスクが 2 つ以上の単位に `split` を呼ぶ

| 規則 | 判定の式 |
| --- | --- |
| U1 | ファイルの中でタスクの `id` か単位の名前が重なる。1 つの課題を 2 つのファイルの `issues` が持つ。`after`・`units[].tasks` の指す先が無い。グラフに輪がある |
| U2 | タスクが 0 個か 2 個以上の単位にある。タスクの `issue` がファイルの `issues` に無い。`criteria` の番号が 1 未満か、`criteria[課題]`（本文の受け入れ条件の数）より大きい |
| U3 | タスク B の `after` のタスク A の単位が、B の単位でも B の単位の閉包の中でもない |
| U4 | 互いに相手を閉包に持たない 2 単位の `touches` が重なる。`touches` はタスクの `files` の和で、文字列の一致で比べる |
| U5 | `Edge.reason` が前後の空白を除くと空 |
| U6・W1 | `lines`（タスクの `lines` の和）が `max` を超える（U6）。`target` を超え `max` 以下（W1）。`lines` が無ければどちらも当てない |

`split` の手順（決定 8）:

1. 単位のタスクを、タスクの依存の順（同じ順はタスクの番号の小さい順）に並べる
2. 1 つで `max` を超えるタスクがあれば、`units` を空、`resplit` をそのタスクにして返す
3. 前から足して `target` を超える手前で区切る。1 つで `target` を超えるタスクは、それだけで 1 区切りにする
4. 区切りが 1 つなら `units` を空にする（分けようがない）
5. 名前は `<元の名前> <番号>`、後の区切りは直前の区切りを先に要る単位にする（理由「分け方の案: 依存の順」）
6. 元の `after` は最初の区切りが引き継ぎ、元の単位を指していた依存は最後の区切りへ付け替える
7. 当てた後のグラフを同じ規則で確かめ（案は作らない）、残る違反を `remaining` に入れる

### 異常系の処理

例外は投げない。判定できないことは `Finding` で返す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge` | 依存の深さの実行順、別のファイルの依存、`whole` の単位 | 400・401・1,000・1,001 行、#3698 と #3247 の大きさの 16,757 行と 24,008 行（受け入れ条件 7） | U1〜U6 の各形、別のファイルの同じ `T1` は通る、同じ単位の中のタスクの依存は通る |
| `split` | #3672 の fixture が上限に収まる複数の単位になり、依存の向きの順になる（受け入れ条件 8） | 区切りが 1 つ | 1 タスクで上限超え、当てた後に残る U4 |

## 単位のファイルの読み書き（`core/plan/units_files.py`）

満たす決定: 決定 3・7・11、受け入れ条件 2・3・6。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `find` | `units_files.py` | 新設 | `(tree: Tree, issues: Sequence[int]) -> dict[int, str]` | `issues/issue-<番号の並び>-plan.units.json` を課題の番号で引く |
| `load` | 同上 | 新設 | `(tree: Tree, path: str) -> UnitFile` | スキーマとパスを検査して `parse` する |
| `limits_of` | 同上 | 新設 | `(project: Mapping) -> Limits` | `workflow.review_units` を既定より先に当てる |
| `collect` | 同上 | 新設 | `(tree, issues, omitted) -> Collected` | 課題ごとの扱いを当て、読むファイルと欠落を返す |
| `check` | 同上 | 新設 | `(tree, issues, omitted, limits, criteria, docs: bool) -> Report` | `judge` の結果に、単位のファイルが無い課題の確かめ（欠落の確かめ）を足す。`docs` が真なら、実装計画と単位のファイルのタスクの集合の一致と、実装計画の 2 つの表の一致も足す |
| `render_tables` | 同上 | 新設 | `(plan: str, views: Sequence[View]) -> str` | 実装計画の「レビューの単位」と「並列と直列」のセクションを書き直した全文 |
| `differs` | 同上 | 新設 | `(a: UnitFile, b: UnitFile) -> bool` | `actual_lines` を除いて、並びと中身を比べる |
| `design_handlings` | `inputs_decide.py` | 新設 | `(text: str) -> dict[int, tuple[str, Optional[int]]]` | 「設計の結果」の表の（扱い, 統合先） |

### 内部のデータと定数

| 名前 | 定義するファイル | 中身 |
| --- | --- | --- |
| `Tree` | `units_files.py` | 読み元。作業ツリー（`Path`）かコミット（`git ls-tree`・`git show`） |
| `Collected` | 同上 | `files`・`omitted`・`excluded`（扱いが「マージする」「閉じる」）・`missing` |
| `Report` | 同上 | `judgement`・`missing`・`task_gaps`・`stale_tables` |
| `review-units.target-lines.json`・`max-lines.json` | `contracts/limits/` | `{"summary", "count": 400 / 1000, "read_by": ["ndf.core.plan.units_files"]}` |

`review-units.schema.json` は設計書の「データ構造」の例の形で、`additionalProperties: false`、`version` は定数 1 にする。`tasks[].files` の各値は、先頭と末尾の `/`、`//`、`.` と `..` の区切り、`*?[]` を断る `pattern` を持つ。`title` は必須である。宣言 `workflow.review_units` は `{"target_lines", "max_lines"}`（1 以上の整数。どちらも任意）で、`project-workflow.schema.json` と `ndf-settings/project.schema.json` の両方に足す。

### 処理の手順

- 扱いの値の「マージする」は他の課題へ統合すること、「閉じる」は実装せずに閉じることである
- 扱いの決め方: `collect` は、番号の並びに課題を含む設計書（`issues/issue-<番号の並び>-design.md`。`-design-detail.md` は含めない）の「設計の結果」の表を読む。設計書の無い課題（設計書を省いた課題）の扱いは「実装する」である
- `render_tables`: 「含むタスク」は `Task 1・2` の形で書く（#4369 の表の読み手が読める形）。「先に要る単位」は `名前（理由）`、別のファイルの単位は `名前（#<課題>。理由）`。「並列と直列」は実行順の昇順に 1 行ずつ。「実際の行数」の列は、今の表の同じ名前の行の値を残す。セクションが無ければ「タスク分解」の後に足す
- 受け入れ条件の数は、`check` の呼び手が課題の本文を 1 回の一括読み取り（`GitHub.nodes`）で読み、`inputs_decide.criteria` で数える

### 異常系の処理

| 関数 | 失敗 | 扱い（停止理由の種類） | 出すもの |
| --- | --- | --- | --- |
| `load` | JSON でない・スキーマに合わない・パスがディレクトリかシンボリックリンク | `invalid` | ファイル名と、行かタスクの `id` |
| `limits_of` | `target_lines` が `max_lines` 以上 | `invalid` | 2 つの値と宣言のキー |
| `check` | 欠落・タスクの集合の違い・表の食い違い・違反 | `invalid`（`kind: violation`・`split` を `items` に） | 課題・単位・規則の番号 |
| `check` | 課題の本文を読めない | GitHub の失敗の分類のまま | 課題の番号 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `find`・`load` | 番号の並び `7-8` | `78` と `7` を取り違えない | 同じ課題が 2 ファイル、パスの各形 |
| `limits_of` | 宣言なし・片方だけ | `target == max` | 型の違い |
| `check` | 省いた課題・「マージする」「閉じる」は欠落にしない | 全課題が省略 | 1 件の欠落、片側だけのタスク、手で直した表 |
| `render_tables` | 2 回打って同じ | 実際の行数を残す | セクションが無い |

## コマンド `ndf plan units`

満たす決定: 決定 7、受け入れ条件 3・5。

| サブコマンド | 引数 | 動き |
| --- | --- | --- |
| `check` | `[--issue N]... [--omitted N]... [<ファイル>...] [--docs]` | `check` の結果 JSON。`--issue` が無く、環境変数 `NDF_STAGE_INPUT` があれば、入力の `issues`・`plan_omitted`・`worktree` を使う（工程の `script` のステップは引数を組めないため） |
| `render` | `[--issue N]... [--omitted N]...` | 表を書き直してから、同じプロセスで `check --docs` を返す |

環境変数 `NDF_DESIGN_RECORD_DIR` があるとき（工程「ドキュメントレビュー」の `units-check`）は、次のように動く。

- 読み元は作業ツリーではなく、保存先の `reviewed-head` のコミットにする。作業場所の HEAD がそのコミットと違えば `stopped`（`approval_stale`）
- 通ったら、保存先の `checked.json` に `{"head", "issues", "handling", "omitted", "limits", "units"}` を書く。`units` は結果の `items` と同じである

単体テストの観点は次のとおりである。

- 結果 JSON の `items` の形（設計書の「コマンド」）
- `render` の後の `check --docs` が通る
- レビューの HEAD と違えば `checked.json` を書かない

## 承認の記録（`progress/design_record.py`）

満たす決定: 決定 1・7・11、I5・I7、受け入れ条件 1。

### 内部のデータと定数

工程「ドキュメントレビュー」の保存先は `<work_root>/document-review/record-g<世代>/` である。変更要求で世代が上がると新しい保存先になり、前の世代の値は混ざらない。

- `invoke` は保存先を、環境変数 `NDF_DESIGN_RECORD_DIR` と差し込み `record_dir` で渡す。環境変数は `contracts/domains/env.json` に `set_by: ndf` で足す
- `script` のステップは引数を定義に固定して持つ。そのため `run` は、同じステップの `StageRun.env_extra` に `NDF_MACHINE_ID`（ステートマシンの ID）も入れる。`env.json` には同じ形で足す
- `invoke._call_script` も `env_extra` を子へ渡す

| ファイル | 書く側 | 中身 |
| --- | --- | --- |
| `reviewed-pr`・`reviewed-head`・`reviewed-base` | `ndf cross-review run`（テンプレート `design-pr-review`） | `<所有者>/<リポジトリ>#<番号>`、SHA、ブランチ名 |
| `checked.json` | `ndf plan units check` | 上の「コマンド」 |
| `design-target.json` | `build` | `{"version": 1, "review": {"repo","pr","head","base"}, "targets": {"issues","handling","omitted"}, "limits": {"target","max"}, "units": [...]}` |

承認の回答のイベント（`answered`）の payload に、`design-target.json` の中身をキー `design_target` で持つ。回答の SSOT はイベントで、ファイルは資料を作るための作業用である。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `required` | `design_record.py` | 新設 | `(snap: stages.Snapshot) -> bool` | 工程「ドキュメントレビュー」がそのモードで設計承認ゲートのステップを持つか |
| `build` | 同上 | 新設 | `(record_dir: Path) -> dict` | 3 つのファイルと `checked.json` から記録を組み、書く |
| `stale` | 同上 | 新設 | `(record: Mapping, repo, pr, head, base) -> list[str]` | 今の PR と記録の違い |
| `reset_drafts` | 同上 | 新設 | `(v, stage: stages.StageDef, why: str) -> list[EventDraft]` | 工程の先頭から設計承認ゲートまでを未了に戻すイベント |
| `approved_of` | 同上 | 新設 | `(v) -> Optional[dict]` | 工程「ドキュメントレビュー」の最新の回答が承認なら、その `design_target`。後の変更要求があれば None |
| `mismatches` | 同上 | 新設 | `(record, tree, context) -> list[str]` | 決定 11 の表の 4 つの比較 |

### 承認ゲートとのつなぎ

| 場所 | 変える中身 |
| --- | --- |
| `gate/entry.present` | 設計承認ゲートで `required` なら、`build` して `stale` を見る。違いがあれば資料を出さず、`reset_drafts` を書いて `ndf progress next` を案内する。合えば承認資料に単位の表・警告・省いた課題を足す |
| `gate/answering.answer` | 回答の確定の前に `stale` を見る。違いがあれば回答を書かず、`reset_drafts` を書く。承認なら payload に `design_target` を足す |
| `gate/answering.recheck` | 回答の後に head かマージ先が変わっていれば、待ちへ戻すかわりに `reset_drafts` を書く |
| `run._consume` | MVV 判定が人の承認を省いて作る回答にも `design_target` を足す。判定の head が記録の head と違えば、人の承認を省かない |
| `run._step_draft` | `design-merge-approved` が `approval_stale` で止まれば、失敗のイベントに `reset_drafts` を続ける |
| `run.approved_seen` | 返す辞書に `design_target` を含める |
| `folding.StepProgress._recorded` | 未了に戻すイベントの対象が待ちのステップなら、`waiting` を消す |

**課題を外す回答**（`--drop`）: 記録の `targets.issues` から外した課題を除く。除いた後の単位のファイルを `judge` に通し、違反があるか、1 つのファイルが外す課題と残す課題の両方を持てば、回答を `Refused` にする。理由には「変更を求めて実装計画を分け直す」と書く。つまり、残る課題の分け方が承認した表のまま成り立つときだけ、外して承認できる。

### 異常系の処理

| 関数 | 失敗 | 扱い |
| --- | --- | --- |
| `build` | 4 つのファイルのどれかが無い・読めない | 資料を出さず、`reset_drafts`。理由にファイル名 |
| `stale` | PR を読めない | `model.Refused`（待ちのまま。今の `github_observe` と同じ） |
| `approved_of` | 承認の回答に `design_target` が無い | None（記録を取れない。決定 11 の表） |
| `mismatches` | 記録の HEAD が手元に無い | 取得を 1 回試し、無ければ理由に載せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `required` | `standard`・`legacy-refactor` が真 | 古い保存した定義は偽 | — |
| `build`・`stale` | 記録の形 | head だけ違う、base だけ違う | ファイルの欠け |
| `approved_of` | 承認 | 承認の後の変更要求、MVV の回答 | payload に記録が無い |
| ゲートとのつなぎ | 違えば未了へ戻る、承認の payload に記録 | 課題を外せる形 | 混ざったファイルの課題を外す |

## 工程の定義とテンプレート

満たす決定: 決定 1、受け入れ条件 1・4。

### 工程の定義

| ファイル | ステップ（順） | `modes` |
| --- | --- | --- |
| `stages/design.json` | `design`・`design-review` | 付けない（今のまま） |
| 同 | `design-mvv` | `light`・`operation`・`documentation` |
| 同 | `design-reading`・`design-gate` | `documentation` |
| 同 | `design-merge` | `light`・`operation`・`documentation` |
| `stages/document-review.json` | `docs-pr`・`docs-review`・`docs-merge` | `documentation` |
| 同（この順に並べる） | `design-pr-review`（`review`）・`units-check`（`record`）・`docs-ci`（`work`）・`design-mvv`（`record`）・`design-reading`（`reading`）・`design-gate`（`gate`）・`design-merge-approved`（`merge`）・`design-sync`（`record`） | `standard`・`legacy-refactor` |
| `stages/plan-design.json`（新設） | `plan`（`ndf.plan` の `implementation-plan`） | — |
| `modes/{standard,legacy-refactor}.json` | 「計画」の行を「詳細設計」の直後へ移し、`plan-design` を呼ぶ | — |

`design-mvv` の id は変えない（`model.MVV_GATE_STEPS` と `ndf records mvv gate --machine` がこの id で探す）。

| 工程 | `inputs` | `optional_inputs` | `outputs` |
| --- | --- | --- | --- |
| `document-review` | `issues`・`worktree`・`branch`・`mode` | `pace`・`mvv_ref`・`mvv_gate`・`design_pr`・`plan_omitted` | `mvv_gate` |
| `plan-design`（`phase: design`） | `issues`・`worktree`・`mode` | — | `plan_omitted` |
| `plan`（今のまま） | — | — | `plan_omitted` を足す |
| `implement` | 今のまま | `branch`（#4369）・`plan_omitted` | 今のまま |

- `context.schema.json` に `plan_omitted`（1 以上の整数の配列）を足す
- **出力の `mvv_gate`**: 工程の終わりの確かめは、定義の `outputs` のキーがすべてあることを求める。`standard` の工程「設計」には判定のステップが無い。そこで `definition._stage_def` は、そのモードで残るステップに `*-mvv` が無ければ `outputs` から `mvv_gate` を外す
- **判定の入力**: `mvv_gate.GATE_STAGES` の設計の値を `("design", "document-review")` にする。`records/cli._env_inputs` は、どちらかの工程の入力の規則に合えば受ける

### テンプレート

| テンプレート | 差し込み | ステップ |
| --- | --- | --- |
| `design-pr-review`（新設） | `repo`・`worktree`・`branch`・`remote`・`review`・`record_dir` | `review`（`{{review}} --reviewed-out <record_dir>/reviewed-head --reviewed-pr-out <record_dir>/reviewed-pr --reviewed-base-out <record_dir>/reviewed-base --findings <record_dir>/findings.json`。`incomplete` は `push` へ）・`push` |
| `docs-ci`（新設） | `repo`・`design_pr`・`required`・`tests`・`ci_budget` | `ci`（`ndf ci watch --pr {{design_pr}} --required={{required}} --test={{tests}} --budget {{ci_budget}}`）。CI の失敗は直さずに工程を止める |
| `design-merge-approved`（新設） | `repo`・`approved_repo`・`approved_pr`・`approved_head`・`approved_base`・`merge_method`・`production_gate` | `merge`（`ndf ci merge --repo --pr --approved-head --approved-base --method --production-gate --verify-merged`）。`probe` を置かない |
| `impl`（変更） | 変えない | 先頭に `guard`（`ndf progress units guard --issue {{issue}} --base {{base}} --branch {{branch}} --unit-file={{unit_file}}`）を足す |
| `implementation-plan`（変更） | 変えない | 変えない（指示は `kinds.json` が持つ） |

- 設定で差し替えたレビューの起動（`workflow.launch.review`）にも、`design-pr-review` の 3 つの出力の引数を後ろへ足す。受けない起動はレビューのステップで止まる
- `invoke.template_values` は、`<record_dir>/findings.json` に `{"items": [...]}` を書く。変更要求の指摘（`StageRun.notes`）があればその本文を 1 件入れ、無ければ空にする。cross-review は渡された項目を「足すべき内容」として扱い、決め終えるまで収束にしない
- `invoke.PRODUCTION_GATE` に `design-merge-approved`（`stale`）を足す
- `invoke._approved_target` は、`approved["design_target"]["review"]` があれば、承認の値を記録の値で埋める。無ければ空文字にし、`ndf ci merge` が断る。承認の値は `approved_repo`・`approved_pr`・`approved_head`・`approved_base` である
- `kinds.json` の `implementation-plan` は、`outputs` に `plan_omitted` を足し、指示に次の 3 つを足す
  - 単位のファイルを書いて `ndf plan units render` を打ち、通るまで直してコミットする
  - 設計書を省いた課題は、設計 PR の実装計画の本文を変えずに、単位のファイルと 2 つの表だけを足す
  - 実装計画を省く課題の番号を `plan_omitted` に返す（無ければ空の配列）
- テンプレート `impl` の `guard` は、#4369 の `plan_start` が持つ「`pr` を通るので開始点を変えないステップ」の一覧にも足す。`queue_decide.RESUME_ALWAYS` にも足す

### 異常系の処理

| 場所 | 失敗 | 扱い |
| --- | --- | --- |
| `design-pr-review` | 3 つのファイルを書かない起動 | `record_dir` に無いので、次の `units-check` が `precondition` で止まる |
| `docs-ci` | 必須のチェックの失敗 | 工程を止める。直して push すると head が変わるので、打ち直しは記録の照合でレビューからになる |
| `design-merge-approved` | head か base が承認と違う（未マージ） | `approval_stale`。ステップを未了に戻し、レビューから流し直す |
| 同 | マージ済みで head か base が承認と違う | `refused`。流し直さない（[#4420](https://github.com/devbasex/ai-plugins/issues/4420) の条件 2。人が確かめる） |

### 単体テストの観点

- `tests/progress/test_definition.py` で、モードごとの流れるステップの並びを固定する
  - `standard`・`legacy-refactor` は、設計承認ゲートが「ドキュメントレビュー」に 1 つだけある
  - 順は「詳細設計」→「計画」→「ドキュメント再構成」で、マージは承認の後にある
  - ほかのモードの並びは変わらない
- `tests/progress/test_sprint_templates.py` で、3 つの新しいテンプレートの argv を組む

## cross-review の `--reviewed-base-out` と `ndf ci merge --verify-merged`

満たす決定: 決定 1、I7。

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_sync` | `domains/crossreview/drive.py` | 変更 | 変えない | `RunConfig.follow_base` が真なら、`--base` でなく PR の今のマージ先を読んで取得し、比較元にする |
| `_reusable` | 同上 | 変更 | 変えない | `follow_base` で、前のラウンドの記録の `base_branch` が今と違えば偽 |
| `cmd_run` | `domains/crossreview/cli.py` | 変更 | 変えない | `--reviewed-base-out` を受け、始めに消し、`--reviewed-out` を書くときに最後のラウンドの `base_branch` を書く |
| `_merge_pull` | `ci_cli.py` | 変更 | 変えない | `--verify-merged` があり、マージ済みの PR の head か base が承認の値と違えば `refused` |
| `pulls` | `adapters/github.py` | 変更 | `(numbers, spare=(), *, rest_fallback: bool = True)` | 偽なら REST へ読み替えない |

- `RunConfig`（`settings.py`）に `follow_base: bool = False` を足し、`--reviewed-base-out` を渡した起動だけ真にする。ラウンドの記録に `base_branch` を足す
- マージ先が前のラウンドと違うラウンドは、前の head からの差分を使わず、新しいマージ先との全差分を渡す。作り方は `--base` からの全差分と同じである
- `--verify-merged` は `--approved-head`・`--approved-base` と組でだけ受ける（無ければ `invalid`）

| 失敗 | 扱い |
| --- | --- |
| PR のマージ先を読めない | 今の `_merge_target` と同じく `precondition` |
| `--reviewed-base-out` を書けない | `precondition` |

単体テストの観点: 起動の間にマージ先が変われば全差分を読み直して新しい名前を書く。`--verify-merged` なしのマージ済みは今のまま `ok`。`rest_fallback=False` は利用枠の失敗をそのまま上げる。

## 単位を流す処理（`progress/units_invoke.py`・`units_queue.py`）

満たす決定: 決定 2・9・11・12、受け入れ条件 3、I5・I6。単位 1 つ分の作業場所・プラン・マージ・打ち直し・スプリントの PR は #4369 の関数を使う。

### 変える中身

| 関数・型 | 定義するファイル | 種類 | シグネチャ・欄 | 責務 |
| --- | --- | --- | --- | --- |
| `Unit` | `review_units_decide.py` | 変更 | `after: tuple[str, ...] = ()`・`merge: int = 0`・`task_ids: tuple[tuple[str, int], ...] = ()` | 先に要る単位の鍵、マージの順、担当タスクと所属の課題 |
| `lineup_from_files` | `units_invoke.py` | 新設 | `(views, files) -> tuple[Unit, ...]` | `View` を #4369 の `Unit` にする |
| `prepare` | 同上 | 変更 | 変えない | 下の「始める前」の順で、読み元を決める |
| `run_stage` | 同上 | 変更 | `(run, work_dir, cwd, ports, answer=None)` | 単位を並列に流し、終わった順にマージする |
| `unit_run` | 同上 | 変更 | 変えない | `unit.md` に担当タスクの本文を足し、`units_guard.record_launch` を打ってからプランを返す |
| `read` | `units_queue.py` | 新設 | `(hub, numbers) -> dict[int, State]` | `pulls(rest_fallback=False)` で `merged`・`queued`・`open`・`closed`・`unknown` にする |
| `action` | 同上 | 新設 | `(state: State, resent: bool) -> str` | 設計書の決定 9 の表（`done`・`wait`・`resend`・`fail`） |

**代表の課題**は、単位のタスクの所属の課題のうち番号が最も小さいものにする。`ndf plan units` の結果の `issues` の最小値で、課題全体を 1 単位にしたものはその課題である。実装のフェーズの手順書の決まり（`issue` には単位の課題のうち番号の最も小さいもの）と、#4369 の決定 9（番号の最小）に合わせるためである。実装計画のファイル名の番号から選ぶと、単位にタスクの無い課題の題が単位の PR に付くことがある。ブランチ名、PR の題と本文、`guard` の照合がこの値を使う。

### 始める前

`prepare` は次の順に見る。上で止まれば下は見ない。

1. `required(snap)` が真なら、`approved_of`。無ければ始めない
2. 記録があれば、`mismatches`。1 件でもあれば始めない。起動に使う課題・扱い・省いた課題・上限は記録の値にする
3. `required` が偽で、単位の欄があるのに単位のファイルが無ければ、`units_convert.convert`（古い定義）か、始めない（この変更より後に作ったスプリント）
4. 欄もファイルも無ければ、#4369 の「これまでの経路」（単位に分けず `impl` を 1 回）
5. 単位のファイルを `units_files.check`（`docs` は偽）に通す。違反があれば始めない
6. #4369 の開始前の確かめ（取得・配布先として宣言したブランチでないこと・push）

「この変更より後に作ったスプリント」は、保存した定義の工程「実装」の `optional_inputs` に `plan_omitted` があることで見分ける。この変更が足すキーだからである。

スプリントは、保存した工程の定義の並びのまま流れる。`per_unit` は、単位ごとに流すことを表す工程の指定で、#4369 が足す。

**工程の定義に `per_unit` が無いスプリント**（#4369 より前に作ったもの）: 実装計画に単位の欄があれば、`invoke` は単位を流す処理へ渡し、上の 3 の変換を通す。#4369 の決定 4（定義に `per_unit` が無ければ `impl` を 1 回）は、単位の欄が無いスプリントにだけ残る。設計書の決定 10 が、分けたはずの変更を 1 本にしないことを求めるためである。

### 処理の手順

`run_stage` は、起動・マージ・GitHub の読み取りを主のスレッドで 1 つずつ行い、`impl` のプランだけを `ThreadPoolExecutor` で流す。

1. 開始時の一覧でマージ済みの単位を `graphlib.TopologicalSorter` の `done` にする
2. `get_ready()` の単位を、`merge` の順に、空いた本数だけ起動する。本数の上限は `named_cap.read(os.environ, <状態の保存先>).value`
3. `concurrent.futures.wait(FIRST_COMPLETED)` で待つ。待ちの PR があれば、間隔（`test-triage.check-wait`）を上限にして待ち、`units_queue.read` を打つ
4. 終わったプランが同時に複数あれば、`merge` の順に `merge_unit` を打つ。`ci_pending` で PR がキューにあれば、待ちの一覧へ入れて次へ進む
5. マージできた単位を `done` にし、2 へ戻る
6. 失敗した単位は `done` にしない（後に続く単位は `get_ready` に出ない）。`cause.kind` が `usage_limit` なら、以後は起動せず、実行中を待って結果を保存し、成功した単位はマージする
7. 起動できる単位も実行中も無くなったら、待ちがあれば `ci_budget` まで読み続ける。待ちが `ci_budget` を超えれば `ci_pending` で止まる。GitHub の利用枠が戻る時刻がこの上限より後なら `infra_wait` で止まる
8. 全単位がマージ済みなら #4369 の `sprint_pr`。残りがあれば `stopped` で、残りの単位と理由を `cause.detail` に書く

打ち直しでは、#4369 の `resume_of` が `merge` を返す単位でも、先に `units_queue.read` を打つ。キューにあれば待ちの一覧へ入れ、`ndf ci merge` を打たない。

単位の欄が無い課題は、課題全体を 1 つの単位にして補う。この単位は `touches` が分からない。そのため、単位のファイルの単位がすべて `done` になった後に、課題の番号の順に 1 つずつ起動する。

### 異常系の処理

| 失敗 | 扱い（停止理由の種類） | 利用者に見える理由 |
| --- | --- | --- |
| 承認の記録が無い | 始めない（`precondition`） | `ndf progress restore` で状態を戻すか、利用者に判断を求める。設計 PR のマージの後に「ドキュメントレビュー」を流し直さない |
| `mismatches` がある | 始めない（`refused`） | 違う項目ごとの記録の値と今の値 |
| 単位の違反 | 始めない（`invalid`） | 規則の番号と単位 |
| 上限の値が壊れている（`NamedCapError`） | 始めない（`invalid`） | 出所と値 |
| プランのスレッドの例外 | その単位の失敗（`invoke.plan_failure` の分類） | 単位の名前 |
| キューの状態を読めない | 待ちのまま（打ち直さない） | 次に読む時刻 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 中断からの再開 |
| --- | --- | --- | --- |
| 単位の `impl` どうし | 重い起動のスロット（メモリを多く使う起動の同時数の上限） | 今の `ndf.slot_pool` | 回ごとの保存先（#4369） |
| 作業場所の作成・マージ | git の共有ディレクトリ・マージ先 | 主のスレッドだけが打つ | マージ済みの PR の一覧 |
| 2 つの `ndf progress units run` | 同じスプリント | ステートマシンの `run_lock` | — |

### 単体テストの観点

`tests/progress/test_units_invoke.py`（#4369 が作る。1,000 行を超えるなら `test_units_parallel.py` に分ける）。時計・待ち・`Ports` を差し替え、本物の時間を待たない。観点は設計書のテスト設計の受け入れ条件 3・4、I5・I6、利用上限の行である。

## この変更より前のスプリントの変換（`progress/units_convert.py`）

満たす決定: 決定 10。

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `convert` | `units_convert.py` | 新設 | `(ctx, ports, answer: Optional[str]) -> Optional[StageResult]` | 設計書の決定 10 の「再開点」の表の順に進め、実装へ進めるなら None |
| `compare` | 同上 | 新設 | `(old: Sequence[Unit], new: Sequence[View]) -> list[str]` | 単位の名前・含むタスク・旧い先に要る単位の違い |

- 変換の担当は、テンプレート `implementation-plan` を `ports.call_plan` で流す。担当への指示は、`kinds.json` の作業 `implementation-plan` の指示の文に足す「実装計画があれば、本文を変えずに単位のファイルを足す」の一文である
- 旧い承認の表は、実装計画を `git show` で読んで `review_units_decide.lineup` に通す。読むコミットは `records.approved_head(v, "design")` の head である
- 照合が違えば、旧い表と新しい分け方を並べた `reapproval.md` を作業の保存先に書き、`gate` の結果（選択肢は承認と却下）を返す。回答は `{"head", "files", "answer"}` を `reapproval.json` に残し、`files` の中身が今の単位のファイルと同じときだけ有効にする

| 失敗 | 扱い |
| --- | --- |
| 担当の失敗・push の拒否 | `stopped`。打ち直しは「再開点」の表の続きから |
| 旧い承認の HEAD を読めない | 始めない（`precondition`） |
| 却下 | 始めない（`refused`） |

単体テストの観点は次のとおりである。

- 再開点の 4 行
- 依存を足しただけは違いにしない
- 旧い表にあった単位の依存（先に要る単位）が変換で消える場合は、承認の回答があるまで実装を流さない
- 承認の後にファイルを直すと再び問う

## 承認した文書の取り込み（`progress/units_sync.py`）

満たす決定: 決定 1・11、I8。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `destination_errors` | `units_sync.py` | 新設 | `(git, record, context, machine_repo: str) -> list[str]` | 送り先のリポジトリとブランチが、スプリントの記録と違う点 |
| `sync` | 同上 | 新設 | `(st, machine_id, cwd, hub) -> dict` | 確かめてから取り込み、push する |

### 送り先の確かめ

`destination_errors` は次の 4 つを見る。1 つでも違えば、取り込みもコミットも push もしない。

| 確かめる値 | 取り方 | 合う条件 |
| --- | --- | --- |
| 送り先のリポジトリ | `git remote get-url --push --all <remote>` の全行を `git.repo_of_url` に通す | 全行が、記録の `review.repo` と大文字小文字を区別せずに同じ |
| スプリントのリポジトリ | ステートマシンの `repo`（作成の時点のリモートの URL）を `repo_of_url` に通す | 記録の `review.repo` と同じ |
| 送り先のブランチ | 文脈の `branch`（工程「作業場所の用意」が書く） | 値があり、文脈の `worktree` の今のブランチと同じ |
| 配布先として宣言したブランチでないこと | #4369 の `released_branches` | 含まれない |

- push は `git push -- <remote> HEAD:refs/heads/<文脈の branch>` で打ち、upstream の設定を使わない
- リモート名は `Git.remote()` である
- URL は認証の値を含みうるので、理由には `<所有者>/<リポジトリ>` だけを出す
- push 先は取得先と別の URL を設定できるので、取得先の URL では確かめない。`git remote get-url --push --all` は、`pushurl` の全部を 1 行ずつ返す（git 2.53.0 で実行して確かめた）

### 処理の手順

`sync` の手順は次のとおりである。

1. `approved_of`。無ければ `stopped`
2. `destination_errors`。1 件でもあれば `stopped`（`refused`）
3. 記録の PR を読み、マージ済み・head が記録の head・base が記録の base を確かめる。base の正しい宛先は次のとおりである
   - 通常は `pr_target("doc", <branch>)`
   - 設計 PR の head がスプリントブランチ自身のとき（`ndf progress next` の経路）は、スプリントの PR の宛先（`pr_target("sprint")`）
4. スプリントブランチが記録の head を祖先に持てば、何もせず `ok`
5. 取り込むファイルごとに、`merge-base` から両側の変更を調べる。スプリント側も変えていて中身が違うファイルがあれば `stopped`。取り込むファイルは次のとおりである
   - 単位のファイルと実装計画
   - 番号の並びが一致する `-requirements.md`・`-design.md`・`-design-detail.md`
6. `git checkout <記録の head> -- <ファイル>` してコミットし、push する。コミットの SHA を `<work_root>/design-sync.json` に残し、打ち直しでは、スプリントブランチがそのコミットを含めば `ok`
7. 取り込んだ内容が記録の head と同じことを確かめる

`ndf progress next` の経路は、手順 4 で `ok` を返して終わる。設計 PR の head がスプリントブランチ自身だからである。

### 異常系の処理

| 失敗 | 扱い（停止理由の種類） |
| --- | --- |
| 承認の記録が無い | `precondition` |
| 送り先の違い | `refused`。項目ごとの記録の値と今の値 |
| 未マージ・head か base の違い | `precondition`（未マージ）・`refused` |
| 競合 | `base_conflict`。ファイル名 |
| push の拒否 | `precondition`。強制しない |

### 単体テストの観点

次のどれでもコミットと push が起きないことを、リモートの ref で確かめる。

- 別のリポジトリの push 先（`pushurl` が 2 つのうち 1 つだけ違う場合を含む）
- 別のブランチに切り替えた作業場所
- 文脈に `branch` が無い

## 実装のプランの確かめ（`progress/units_guard.py`）

満たす決定: 決定 12、I5。

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_launch` | `units_guard.py` | 新設 | `(work_root, branch, record, issue, unit_file: Optional[Path]) -> None` | `units/guard/<ブランチの sha256 の先頭 16 字>.json` に `{"branch", "record_head", "issue", "unit_sha256"}` を書く |
| `guard` | 同上 | 新設 | `(st, repo, issue, base, branch, unit_file) -> list[str]` | 設計書の決定 12 の表の判定。空なら通す |
| `read_machines` | `store.py` | 新設 | `(conn, repo) -> list[dict]` | そのリポジトリのスプリントの一覧 |

- 対象のスプリントは、終わっていないもののうち、文脈の `issues` に `--issue` があるか、文脈の `branch` が `--base` のものである
- 「宛先がスプリントブランチ」は、`--base` が `model.branch_prefix("sprint")`（`sprint/`）で始まることである
- 指示のファイル（`--unit-file` に渡すファイル。単位のファイルとは別）は、中身の sha256 を比べる。指示の無い単位は空文字である

| 失敗 | 扱い |
| --- | --- |
| 状態の保存先を読めない | 止める（`precondition`） |
| 指示のファイルを読めない | 止める（`precondition`） |
| 違い | 止める（`refused`）。`ndf progress units run <ID>` を打つと書く |

単体テストの観点: 起動した単位は通る。課題か指示を変えた手組みのプランは止まる。単発の `feat/` 宛ては通る。登録の無い `sprint/` 宛ては止まる。

## コマンド `ndf progress units`（`progress/units_cli.py`）

満たす決定: 決定 12。

| サブコマンド | 引数 | 呼ぶ関数 |
| --- | --- | --- |
| `ndf progress units run` | `<ステートマシンの ID>`・`--cwd` | 保存した定義の工程「実装」の `StageRun` を組み、`units_invoke.run_stage`。工程のイベントは書かない（supervisor は今のとおり `ndf progress finish` を打つ） |
| `ndf progress units sync` | `[<ID>]`・`--cwd`。`<ID>` を省けば環境変数 `NDF_MACHINE_ID`（工程のステップ `design-sync`）。取り込みは文脈の `worktree` で行う | `units_sync.sync` |
| `ndf progress units guard` | `--issue`・`--base`・`--branch`・`--unit-file` | `units_guard.guard` |

承認した HEAD、承認の要否、送り先を渡す引数は作らない。`units run` は、単位の経路でないスプリント（欄もファイルも無い）では `precondition` で止まる。`ndf check --write-spec` で `docs/contracts/commands.md`・`env.md`・`contracts/schemas/io/` を生成し直す。

## 手順書

満たす決定: 決定 1・10・11・12。

| ファイル | 直す所 |
| --- | --- |
| `skills/implementation-plan/SKILL.md` | 単位のファイルの書き方と `render`。実装の途中の分け直しは、実際の行数の追記だけにする |
| `skills/design/SKILL.md` | 手順 7 の設計 PR の流れ（承認とマージは「ドキュメントレビュー」の終わり） |
| `skills/requirements-design/references/document-roles.md` | 実装計画の順序と、設計 PR に載ること |
| `skills/development-workflow/SKILL.md` | 工程表の順序と承認ゲートの位置 |
| `skills/development-workflow/references/agent-layers.md` | 単位ごとにプランを組む手順を `ndf progress units run` に置き換え、設定が `doc_prs: base`（承認した HEAD のマージ先がベースブランチ）のときの `sync` を書く |
| `skills/development-workflow/references/{approval-request,stage-notes}.md` | 承認資料の単位の表、設計 PR の流れ |
| `skills/issue-plan-strategy/references/execution-plan.md` | 実行計画の実装の行は `check` の `items` から置く |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `judge` | `graphlib.TopologicalSorter`、`queue_decide.waves` | 標準ライブラリを実行した（2026-11-26）。`queue_decide.py` を読んだ | `graphlib` を使う。`waves` は理由と行数を持たない |
| `find`・`design_handlings` | `issue_docs.design_issues`、`inputs_decide.split_row`・`section_lines`、`collect.design_rows` | 3 つのファイルを読んだ | 使う。`design_rows` は触るファイルだけを読むので変えない |
| `build`・`stale` | `gate.Target`・`merge_approval.MergeTarget` | `gate/asking.py` を読んだ | `Target` の値と比べる。単位と扱いは持たないので記録を足す |
| `reset_drafts` | `run.review_again_drafts`・`stages.RESET` | `run.py` を読んだ | 同じイベントの形を使う。戻す範囲が違う |
| `destination_errors` | `git.repo_of_url`・`Git.remote`、#4369 の `released_branches` | `adapters/git.py` を読み、`git remote get-url --push --all` を実行した | 使う。`remote_repo` は取得先の URL を読むので使えない |
| `units_queue.read` | `GitHub.pulls` | `adapters/github.py` を読んだ | 読み替えを止める指定を足して使う |
| `read_machines` | `store.read_machine` | `store.py` を読んだ | 1 件ずつの読み取りだけなので足す |
