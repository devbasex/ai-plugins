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
├── adapters/{git, github}.py
├── ci_cli.py
└── domains/{crossreview/{cli,drive,settings}.py, records/{cli,mvv_gate}.py}
contracts/
├── workflow/review-units.schema.json*
├── workflow/stages/{design, document-review, plan, plan-design*, implement}.json
├── workflow/{modes/{standard,legacy-refactor}.json, context.schema.json}
├── templates/{design-pr-review*, docs-ci*, design-merge-approved*, impl, implementation-plan, implementation-plan-convert*, params}.json
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
| `adapters/github.py` | 1,000 | #4369 がファイルを分ける。この変更は #4369 の実装の後に始めるので、分けた後のファイルへ足す |

### ファイルごとの役割

| パス | 種類 | 役割 | 依存する先 | 最上位の関数 |
| --- | --- | --- | --- | --- |
| `src/ndf/core/plan/units_decide.py` | 新設 | 実行順・導く値・違反・警告・分け方の案を返す | `graphlib` | `parse`・`judge`・`split`・`single` |
| `src/ndf/core/plan/units_files.py` | 新設 | 単位のファイル・実装計画・上限を読み、表を作る | 上・`inputs_decide`・`git`・`contracts` | `find`・`load`・`limits_of`・`collect`・`check`・`render_tables`・`differs` |
| `src/ndf/core/plan/cli.py` | 変更 | `ndf plan units render`・`check` | `units_files` | `_cmd_units` |
| `src/ndf/core/plan/inputs_decide.py` | 変更 | 「設計の結果」の表の「扱い」と「統合先」の列を読む。`design_gaps` を消す | — | `design_handlings`（新設） |
| `src/ndf/core/plan/queue_decide.py` | 変更 | `RESUME_ALWAYS` に `guard` を足す | — | — |
| `src/ndf/core/plan/review_units_decide.py` | 変更 | #4369 の `Unit` に並列で使う欄を足す | — | — |
| `src/ndf/progress/design_record.py` | 新設 | 承認の記録を組む・照らす・読む | `units_files`・`folding` | `build`・`stale`・`reset_drafts`・`approved_of`・`required`・`mismatches`・`without`・`converted`・`reapproved_of` |
| `src/ndf/progress/units_invoke.py` | 変更 | 単位の読み元を単位のファイルにし、承認と照らし、並列に流す | 下の 3 つ・`units_files` | `prepare`・`run_stage`（変更）・`advance`（新設） |
| `src/ndf/progress/units_queue.py` | 新設 | マージキューで待つ単位の状態を読む | `github` | `read`・`action` |
| `src/ndf/progress/units_convert.py` | 新設 | この変更より前の表を単位のファイルへ変換する | `review_units_decide`・`units_files`・`design_record`・`units_sync` | `convert`・`compare` |
| `src/ndf/progress/units_sync.py` | 新設 | 送り先を確かめ、承認した文書をスプリントブランチへ取り込む | `git`・`github`・`design_record` | `destination_errors`・`outgoing_errors`・`sync` |
| `src/ndf/progress/units_guard.py` | 新設 | 実装のプランを通すかを決める | `store`・`design_record` | `record_launch`・`guard` |
| `src/ndf/progress/units_cli.py` | 新設 | `ndf progress units run`・`sync`・`guard` | 上の 5 つ | `add_parsers`・`dispatch` |
| `src/ndf/progress/invoke.py` | 変更 | 承認の記録の値と保存先を差し込み、単位の経路を呼ぶ | `design_record` | `template_values`・`_approved_target`（変更） |
| `src/ndf/progress/run.py`・`folding.py` | 変更 | MVV 判定で人の承認を省いた回答に記録を付け、記録が古ければステップを未了に戻す。`run.py` は `units run` が使う 2 つの関数を公開名にする | `design_record` | `_consume`・`_step_draft`・`approved_seen`（変更）・`play_move`・`launching`（改名） |
| `src/ndf/progress/definition.py` | 変更 | そのモードに判定のステップが無い工程の出力から `mvv_gate` を外す | — | `_stage_def`（変更） |
| `src/ndf/progress/store.py` | 変更 | リポジトリのスプリントの一覧を読む | — | `read_machines`（新設） |
| `src/ndf/progress/gate/entry.py`・`answering.py` | 変更 | 資料を出す前と回答の確定の前に記録と照らす | `design_record` | `present`・`answer`・`recheck`（変更） |
| `src/ndf/ci_cli.py` | 変更 | `ndf ci merge --verify-merged`・`ndf ci watch --head` | — | `_merge_pull`・`_watch`（変更） |
| `src/ndf/adapters/git.py` | 変更 | push の URL を読み、解析し、git がもう一度書き換えるかを返す | — | `github_target`・`Git.push_urls`・`Git.rewrites_again` |
| `src/ndf/adapters/github.py` | 変更 | `pulls` に REST への読み替えを止める指定 | — | `pulls`（変更） |
| `src/ndf/domains/crossreview/{cli,drive,settings}.py` | 変更 | `--reviewed-base-out` | — | `_sync`・`_reusable`・`cmd_run`（変更） |
| `src/ndf/domains/records/{cli,mvv_gate}.py` | 変更 | 設計の判定の入力を、2 つの工程のどちらの形でも受ける | — | `_env_inputs`（変更） |

## 単位の判定（`core/plan/units_decide.py`）

満たす決定: 決定 4・5・6・8、受け入れ条件 2・5・7・8、I1〜I4。ファイル・git・環境変数を読まない。

### 型

| 型 | 欄 |
| --- | --- |
| `Task` | `id`（`T<n>`）・`title`・`issue`・`files: tuple[str, ...]`・`lines: Optional[int]`（無いのは `single` が作る合成のタスクだけ）・`criteria: tuple[str, ...]`・`after: tuple[str, ...]` |
| `Edge` | `unit`・`reason`・`issue: Optional[int]`（別のファイルの単位を指すとき） |
| `UnitDef` | `name`・`tasks: tuple[str, ...]`・`after: tuple[Edge, ...]`・`actual_lines: Optional[int]` |
| `UnitFile` | `path`・`issues: tuple[int, ...]`・`tasks: tuple[Task, ...]`・`units: tuple[UnitDef, ...]`・`whole: bool`（実装計画を省いた課題を 1 単位にしたかのフラグ） |
| `Limits` | `target`・`max` |
| `View` | `file`・`name`・`order`・`merge`・`after`（解決済みの `(file, name, reason)`）・`issues`・`tasks`・`criteria`・`lines`・`actual_lines`・`touches` |
| `Finding` | `rule`・`unit`・`reason` |
| `Proposal` | `unit`・`units: tuple[UnitDef, ...]`・`resplit: tuple[str, ...]`（切り直すタスク）・`remaining: tuple[Finding, ...]` |
| `Judgement` | `views`・`violations`・`warnings`・`proposals` |

`UnitDef` は #4369 の `review_units_decide.Unit`（流すときの単位）と別の型である。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `parse` | `units_decide.py` | 新設 | `(doc: Mapping, path: str) -> UnitFile` | スキーマを通った辞書を型にする。`issue` を省いたタスクはファイルの唯一の課題 |
| `judge` | 同上 | 新設 | `(files: Sequence[UnitFile], limits: Limits, criteria: Mapping[int, int]) -> Judgement` | 全ファイルを 1 つのグラフにし、違反・警告・導く値・案を返す。`criteria` のキーは、受け入れ条件を指してよい課題のすべてである |
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
| U2 | タスクが 0 個か 2 個以上の単位にある。タスクの `issue` がファイルの `issues` に無い。`criteria` が指す課題が引数 `criteria` のキーに無い（対象外の参照）。番号が 1 未満か、`criteria[課題]`（本文の受け入れ条件の数）より大きい |
| U3 | タスク B の `after` のタスク A の単位が、B の単位でも B の単位の閉包の中でもない |
| U4 | 互いに相手を閉包に持たない 2 単位の `touches` が重なる。`touches` はタスクの `files` の和で、文字列の一致で比べる |
| U5 | `Edge.reason` が前後の空白を除くと空 |
| U6・W1 | `lines`（タスクの `lines` の和）が `max` を超える（U6）。`target` を超え `max` 以下（W1）。`lines` が無い単位（`whole` が真）にはどちらも当てない |

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
| `check` | 同上 | 新設 | `(tree, issues, omitted, limits, criteria, docs: bool) -> Report` | `judge` の結果に、単位のファイルが無い課題の確かめ（欠落の確かめ）を足す。`docs` が真なら、実装計画のタスクの番号が重ならないことと、実装計画と単位のファイルのタスクの集合の一致と、実装計画の 2 つの表の一致も足す |
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

`review-units.schema.json` は設計書の「データ構造」の例の形で、`additionalProperties: false`、`version` は定数 1 にする。`tasks[].files` の各値は、先頭と末尾の `/`、`//`、`.` と `..` の区切り、`*?[]` を断る `pattern` を持つ。`title` と `lines`（0 以上の整数）は必須である。行数の無い通常のタスクは `load` が `invalid` で止める。宣言 `workflow.review_units` は `{"target_lines", "max_lines"}`（1 以上の整数。どちらも任意）で、`project-workflow.schema.json` と `ndf-settings/project.schema.json` の両方に足す。

### 処理の手順

- 扱いの値の「マージする」は他の課題へ統合すること、「閉じる」は実装せずに閉じることである
- 扱いの決め方: `collect` は、番号の並びに課題を含む設計書（`issues/issue-<番号の並び>-design.md`。`-design-detail.md` は含めない）の「設計の結果」の表を読む。設計書の無い課題（設計書を省いた課題）の扱いは「実装する」である
- `render_tables`: 「含むタスク」は `Task 1・2` の形で書く（#4369 の表の読み手が読める形）。「先に要る単位」は `名前（理由）`、別のファイルの単位は `名前（#<課題>。理由）`。「並列と直列」は実行順の昇順に 1 行ずつ。「実際の行数」の列は `View.actual_lines`（ファイルと単位の名前の組で引いた JSON の値）だけから作り、今の表の値は読まない。セクションが無ければ「タスク分解」の後に足す
- 受け入れ条件の数は、`check` の呼び手が課題の本文を 1 回の一括読み取り（`GitHub.nodes`）で読み、`inputs_decide.criteria` で数える。実装の開始で承認の記録があるときは、読まずに記録の `criteria` を使う
- 受け入れ条件を読む課題は、場合で決まる。判定器へ渡す `criteria` のキーもこの集合にする
  - `--issue` があるとき: その課題（扱いが「マージする」の統合元を含む）
  - `--issue` が無く、ファイルを引数で直に指したとき: 読んだファイルの `issues` と、タスクの `criteria` が指すすべての課題
- 取得の失敗は GitHub の失敗として止め、違反にしない。集合に無い課題を指すタスクは、判定器が U2（対象外の参照）にする

### 異常系の処理

| 関数 | 失敗 | 扱い（停止理由の種類） | 出すもの |
| --- | --- | --- | --- |
| `load` | JSON でない・スキーマに合わない・パスがディレクトリかシンボリックリンク | `invalid` | ファイル名と、行かタスクの `id` |
| `limits_of` | `target_lines` が `max_lines` 以上 | `invalid` | 2 つの値と宣言のキー |
| `check` | 欠落・実装計画のタスクの番号の重なり・タスクの集合の違い・表の食い違い・違反 | `invalid`（`kind: violation`・`split` を `items` に） | 課題・単位・規則の番号 |
| `check` | 課題の本文を読めない | GitHub の失敗の分類のまま | 課題の番号 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `find`・`load` | 番号の並び `7-8` | `78` と `7` を取り違えない | 同じ課題が 2 ファイル、パスの各形 |
| `limits_of` | 宣言なし・片方だけ | `target == max` | 型の違い |
| `check` | 省いた課題・「マージする」「閉じる」は欠落にしない | 全課題が省略 | 1 件の欠落、片側だけのタスク、番号が重なる見出し、手で直した表 |
| `render_tables` | 2 回打って同じ | JSON の `actual_lines` が表に出る。表だけに手で書いた値は残らない | セクションが無い |

## コマンド `ndf plan units`

満たす決定: 決定 7、受け入れ条件 3・5。

| サブコマンド | 引数 | 動き |
| --- | --- | --- |
| `check` | `[--issue N]... [--omitted N]... [<ファイル>...] [--docs]` | `check` の結果 JSON。`--issue` が無く、環境変数 `NDF_STAGE_INPUT` があれば、入力の `issues`・`plan_omitted`・`worktree` を使う（工程の `script` のステップは引数を組めないため） |
| `render` | `[--issue N]... [--omitted N]...` | 表を書き直してから、同じプロセスで `check --docs` を返す |

環境変数 `NDF_DESIGN_RECORD_DIR` があるとき（工程「ドキュメントレビュー」の `units-check`）は、次のように動く。

- 読み元は作業ツリーではなく、`NDF_DESIGN_RECORD_DIR` が指す保存先（下の「承認の記録」で説明する工程「ドキュメントレビュー」の保存先）にある `reviewed-head` が示すコミットにする。作業場所の HEAD がそのコミットと違えば `stopped`（`approval_stale`）
- 通ったら、保存先の `checked.json` に `{"head", "branch", "issues", "handling", "omitted", "limits", "criteria", "units"}` を書く。`branch` は入力の `branch`（スプリントブランチ）で、無ければ `stopped`（`precondition`）にする。`units` は結果の `items` と同じで、`criteria` は課題ごとの受け入れ条件の数である
- 入力に `design_pr` があれば、保存先の `reviewed-pr` の番号と比べ、違えば `stopped`（`refused`）
- 結果の `outputs.design_pr` に `reviewed-pr` の番号を返す。設計承認ゲートの質問は、文脈に `design_pr` が無くてもこの値を読む
- `reviewed-pr` などのファイルが無く、`workflow.launch.review` が指定されていれば、理由にこのキーと直し方（設計書の「影響と危険」）を書く

単体テストの観点は次のとおりである。

- 結果 JSON の `items` の形（設計書の「コマンド」）
- `render` の後の `check --docs` が通る
- レビューの HEAD と違えば `checked.json` を書かない

## 承認の記録（`progress/design_record.py`）

満たす決定: 決定 1・7・11、I5・I7、受け入れ条件 1。

### 内部のデータと定数

工程「ドキュメントレビュー」の保存先は `<work_root>/document-review/record-g<世代>/` である。変更要求で世代が上がると新しい保存先になり、前の世代の値は混ざらない。

- `invoke` は保存先を、環境変数 `NDF_DESIGN_RECORD_DIR` と差し込み `record_dir` で渡す。環境変数は `contracts/domains/env.json` に `set_by: ndf` で足す
- `script` のステップは引数を定義に固定して持つので、実行時の値を引数で渡せない。そのため `run` は、保存先を渡す `script` のステップ（`units-check` など）の `StageRun.env_extra` に、保存先と一緒に `NDF_MACHINE_ID`（ステートマシンの ID）も入れる。`env.json` には同じ形で足す
- `invoke._call_script` も `env_extra` を子へ渡す

| ファイル | 書く側 | 中身 |
| --- | --- | --- |
| `reviewed-pr`・`reviewed-head`・`reviewed-base` | `ndf cross-review run`（テンプレート `design-pr-review`） | `<所有者>/<リポジトリ>#<番号>`、SHA、ブランチ名 |
| `checked.json` | `ndf plan units check` | 上の「コマンド」 |
| `design-target.json` | `build` | `{"version": 1, "review": {"repo","pr","head","base"}, "sprint_branch": "<ブランチ>", "targets": {"issues","handling","omitted"}, "limits": {"target","max"}, "criteria": {"<課題>": 数}, "units": [...]}` |

承認の回答のイベント（`answered`）の payload に、`design-target.json` の中身をキー `design_target` で持つ。回答の SSOT はイベントで、ファイルは資料を作るための作業用である。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `required` | `design_record.py` | 新設 | `(snap: stages.Snapshot) -> bool` | 工程「ドキュメントレビュー」がそのモードで設計承認ゲートのステップを持つか |
| `build` | 同上 | 新設 | `(record_dir: Path) -> dict` | 3 つのファイルと `checked.json` から記録を組み、書く |
| `stale` | 同上 | 新設 | `(record: Mapping, repo, pr, head, base) -> list[str]` | 今の PR と記録の違い |
| `reset_drafts` | 同上 | 新設 | `(v, stage: stages.StageDef, why: str) -> list[EventDraft]` | 工程の先頭から設計承認ゲートまでを未了に戻すイベント |
| `approved_of` | 同上 | 新設 | `(v) -> Optional[dict]` | 工程「ドキュメントレビュー」の最新の回答が承認なら、その `design_target`。後の変更要求があれば None |
| `mismatches` | 同上 | 新設 | `(record, tree, context) -> list[str]` | 決定 11 の表の 4 つの比較。スプリントの課題は全体を比べ、ほかの比較は記録の `targets.issues` に絞る |
| `without` | 同上 | 新設 | `(record, drop, files) -> dict` | 課題を外す回答の記録の絞り込み。外せない形なら `Refused` |
| `converted` | 同上 | 新設 | `(repo, head, branch, context, limits, report) -> dict` | 変換の再承認の記録。`design-target.json` と同じ形で、`sprint_branch` は `branch`、`review.pr` は null、`head` は単位のファイルを含むコミット |
| `reapproved_of` | 同上 | 新設 | `(v) -> Optional[dict]` | 工程「実装」の最新の回答が承認なら、その `design_target` |

### 承認ゲートとのつなぎ

| 場所 | 変える中身 |
| --- | --- |
| `gate/entry.present` | 設計承認ゲートで `required` なら、`build` して `stale` を見る。違いがあれば資料を出さず、`reset_drafts` を書いて `ndf progress next` を案内する。合えば承認資料に単位の表・警告・省いた課題を足す |
| `gate/answering.answer` | 回答の確定の前に `stale` を見る。違いがあれば回答を書かず、`reset_drafts` を書く。承認なら payload に `design_target` を足す。工程「実装」の再承認（決定 10 の変換で求める承認。下の「この変更より前のスプリントの変換」）の回答では、その承認の待ちを返した `gate` の結果の `report.design_target` を payload に足す。設計 PR の照合ではないので `stale` は見ない |
| `gate/answering.recheck` | 回答の後に head かマージ先が変わっていれば、待ちへ戻すかわりに `reset_drafts` を書く |
| `run._consume` | MVV 判定が人の承認を省いて作る回答にも `design_target` を足す。判定の結果が持つ承認の対象（出力 `mvv_gate.target` の `repo`・`pr`・`base`・`head`。判定が読んだ PR の値）を、記録の `review` の 4 つと比べる。1 つでも違うか欠ければ、人の承認を省く回答を作らず、`reset_drafts` を書いてレビューへ戻す |
| `run._step_draft` | `units-check`・`docs-ci`・`design-mvv`・`design-merge-approved` のどれかが `approval_stale` で止まれば、失敗のイベントに `reset_drafts` を続ける |
| `run.approved_seen` | 返す辞書に `design_target` を含める |
| `folding.StepProgress._recorded` | 未了に戻すイベントの対象が待ちのステップなら、`waiting` を消す |

**課題を外す回答**（`--drop`）: `without` が記録を残る課題へ絞り、承認の回答の `design_target` にする。

| 記録の欄 | 絞り方 |
| --- | --- |
| `targets.issues`・`handling`・`omitted`・`criteria` | 外した課題の要素とキーを除く |
| `units` | 残る課題の単位のファイルと、残る省いた課題の単位だけを残し、`judge` で実行順とマージの順を求め直す |

次のどれかなら、回答を `Refused` にする。理由には当たった条件と「変更を求めて実装計画を分け直す」を書く。

- 1 つのファイルが、外す課題と残す課題の両方を持つ
- 扱いが「マージする」の課題と統合先の、片方だけを外す
- 絞った後の `judge` に違反がある（残る単位が外した単位を先に要る、など）

`mismatches` は、最初にスプリントの課題を比べる。文脈の今の `issues`（`--drop` があれば外した後の値）の全体が、記録の `targets.issues` と同じでなければ違いにする。承認の後に課題を追加しても、見落とさないためである。

扱い・統合先と省いた課題の比較は、記録の `targets.issues` の課題に絞る。今の `_dropped` は文脈の `issues` だけを変えるので、文脈の `plan_omitted` と設計書の「設計の結果」の表には外した課題が残るためである。`collect` と `lineup_from_files` も、記録の課題だけを読む。

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
| `mismatches` | 記録と同じ | `--drop` の後は違いなし | 承認の後にスプリントへ課題を追加した |
| ゲートとのつなぎ | 違えば未了へ戻る、承認の payload に記録、MVV の判定の対象が head は同じで base だけ違えば未了へ戻る | 省いた課題・統合元と統合先の両方を外し、残りが照合を通る | 混ざったファイルの課題を外す、統合元か統合先の片方を外す |

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
| `document-review`（`standard`・`legacy-refactor`） | `issues`・`worktree`・`branch`・`mode` | `pace`・`mvv_ref`・`mvv_gate`・`design_pr`・`plan_omitted` | `mvv_gate` |
| `document-review`（`documentation`） | `worktree`・`branch`（今のまま） | 同上 | 無し |
| `plan-design`（`phase: design`） | `issues`・`worktree`・`mode` | — | `plan_omitted` |
| `plan`（今のまま） | — | — | `plan_omitted` を足す |
| `implement` | 今のまま | `branch`（#4369）・`plan_omitted` | 今のまま |

- `context.schema.json` に `plan_omitted`（1 以上の整数の配列）を足す
- **モードごとの入力**: 工程 `document-review` の定義ファイルの `inputs` には 4 つを書く。`definition._stage_def` は、工程 `document-review` の定義を組み立てるときに限り、選んだモードで `design-pr-review` が実行されなければ、`inputs` から `issues` と `mode` を外す。ほかの工程の `inputs` は変えない。`documentation` の文書のレビューが、新しい必須の入力で止まらないようにするためである
- **出力の `mvv_gate`**: 工程の終わりの確かめは、定義の `outputs` のキーがすべてあることを求める。`standard` の工程「設計」には判定のステップが無い。そこで `definition._stage_def` は、そのモードで残るステップに `*-mvv` が無ければ `outputs` から `mvv_gate` を外す
- **判定の入力**: `mvv_gate.GATE_STAGES` の設計の値を `("design", "document-review")` にする。`records/cli._env_inputs` は、どちらかの工程の入力の規則に合えば受ける
- **判定する PR**: 工程「ドキュメントレビュー」の MVV 判定は、作業場所のブランチから PR を探さず、レビューした PR を判定する。`docs-ci` と違う PR を判定すると、`run._consume` がレビューへ戻し、打ち直すたびに同じ不一致が起きるためである
  - `ndf records mvv gate` は、環境変数 `NDF_DESIGN_RECORD_DIR` があれば、保存先の `reviewed-pr` のリポジトリと番号を使う。`--machine` で打つ場合は、そのステートマシンの工程「ドキュメントレビュー」の今の世代の保存先を読む
  - 取得と読み取りには、URL がそのリポジトリを指すリモートを使う。そのリモートが無ければ、判定を打てないとして人の承認へ回し、レビューへは戻さない
  - 判定の直前に読んだ PR の head か base が、保存先の `reviewed-head`・`reviewed-base` と違えば、判定もコメントもせず `stopped`（`approval_stale`）
  - 単体テストでは、作業場所の既定のリモートがレビューしたリポジトリと違っても、レビューした PR を判定することを確かめる

### テンプレート

| テンプレート | 差し込み | ステップ |
| --- | --- | --- |
| `design-pr-review`（新設） | `repo`・`worktree`・`branch`・`remote`・`review`・`record_dir` | `review`（`{{review}} --reviewed-out <record_dir>/reviewed-head --reviewed-pr-out <record_dir>/reviewed-pr --reviewed-base-out <record_dir>/reviewed-base --findings <record_dir>/findings.json`。`incomplete` は `push` へ）・`push` |
| `docs-ci`（新設） | `repo`・`reviewed_repo`・`reviewed_pr`・`reviewed_head`・`required`・`tests`・`ci_budget` | `ci`（`ndf ci watch --repo {{reviewed_repo}} --pr {{reviewed_pr}} --head {{reviewed_head}} --required={{required}} --test={{tests}} --budget {{ci_budget}}`）。CI の失敗は直さずに工程を止める |
| `design-merge-approved`（新設） | `repo`・`approved_repo`・`approved_pr`・`approved_head`・`approved_base`・`merge_method`・`production_gate` | `merge`（`ndf ci merge --repo --pr --approved-head --approved-base --method --production-gate --verify-merged`）。`probe` を置かない |
| `impl`（変更） | 変えない | 先頭に `guard`（`ndf progress units guard --issue {{issue}} --base {{base}} --branch {{branch}} --unit-file={{unit_file}}`）を足す |
| `implementation-plan`（変更） | 変えない | 変えない（指示は `kinds.json` が持つ） |
| `implementation-plan-convert`（新設） | `implementation-plan` の差し込みと `convert` | `work`（`implementation-plan` の作業）だけ。push のステップを持たない |

- `reviewed_repo`・`reviewed_pr`・`reviewed_head` は、`invoke.template_values` が保存先の `reviewed-pr`・`reviewed-head` から入れる。`reviewed-pr` の `<所有者>/<リポジトリ>#<番号>` は、`#` の前を `reviewed_repo`、後ろの番号を `reviewed_pr` にする。作業場所の既定のリモートがレビューしたリポジトリと違っても、同じ PR の CI を待つためである。文脈の `design_pr` は使わない
- 設定で差し替えたレビューの起動（`workflow.launch.review`）にも、`design-pr-review` の 3 つの出力の引数と `--findings` を後ろへ足す。受けない起動はレビューのステップで止まる。この契約と直し方は、`params.json` の `review` の説明と変更履歴に書く。旧い形の起動を受ける互換の処理は持たない
- `invoke.template_values` は、`<record_dir>/findings.json` に `{"items": [...]}` を書く。変更要求の指摘（`StageRun.notes`）があればその本文を 1 件入れ、無ければ空にする。cross-review は渡された項目を「足すべき内容」として扱う。修正担当が全項目に「直した」か「見送り（理由つき）」の結果を付けるまで、収束にしない
- 変更要求の項目の本文には「利用者の変更要求である。反映して直したと記録する。反映できないときは、区分 `needs_user` の見送りにして理由を書く」を添える。`needs_user` の見送りがあるレビューは承認を自動で省かず、承認資料に反映していない要求と理由が載る。ほかの補足と同じ扱いで見送って、直していない設計を承認へ出さないためである
- `invoke.PRODUCTION_GATE` に `design-merge-approved` を足し、値は `design-merge` と同じ `STALE` にする。この値は `ndf ci merge --production-gate` に渡る。マージ先が本番へ自動で反映され、承認の記録が無いときに、`approval_stale` で止める指定である
- `invoke._approved_target` は、`approved["design_target"]["review"]` があれば、承認の値を記録の値で埋める。無ければ空文字にし、`ndf ci merge` が断る。承認の値は `approved_repo`・`approved_pr`・`approved_head`・`approved_base` である
- `kinds.json` の `implementation-plan` は、`outputs` に `plan_omitted` を足し、指示に次の 3 つを足す
  - 単位のファイルを書いて `ndf plan units render` を打ち、通るまで直してコミットする
  - 設計書を省いた課題は、設計 PR の実装計画の本文を変えずに、単位のファイルと 2 つの表だけを足す
  - 実装計画を省く課題の番号を `plan_omitted` に返す（無ければ空の配列）
- 変換（決定 10）のときだけの指示を、4 つ目として分けて足す。作業の入力 `convert`（新しいテンプレート `implementation-plan-convert` が渡す。既定は空）が空でないときは、コミットまでで止めて push しない。上の 3 つと通常の「実装計画を書く」指示より優先して、既存の実装計画の本文を変えずに単位のファイルと 2 つの表だけを足す。`convert` が空なら、この指示は当たらない。設計を変えて計画し直すときは、今のとおり本文を書き直す
- テンプレート `impl` の `guard` は、打ち直しのたびに先に実行する（`queue_decide.RESUME_ALWAYS` に足す）。`guard` が通った後は、通常の再開位置から流す。通常の再開位置は #4369 の `plan_start` が決める。`plan_start` は、PR の番号を記録したファイル（`pr-number`）が無いのに再開位置が PR を作るステップ（`pr`）より後なら、`pr` から流し直す。この判定では、`guard` を `pr` より前のステップとして扱う。`guard` が失敗した回は `guard` から流し直し、飛ばさない

### 異常系の処理

| 場所 | 失敗 | 扱い |
| --- | --- | --- |
| `design-pr-review` | 3 つのファイルを書かない起動 | `record_dir` に無いので、次の `units-check` が `precondition` で止まる |
| `docs-ci` | PR の HEAD が `reviewed_head` と違う（開始と成功の確定の時点） | `approval_stale`。ステップを未了に戻し、レビューから流し直す |
| `docs-ci` | 必須のチェックの失敗 | 工程を止める。直して push すると head が変わるので、打ち直しは記録の照合でレビューからになる |
| `design-merge-approved` | head か base が承認と違う（未マージ） | `approval_stale`。ステップを未了に戻し、レビューから流し直す |
| 同 | マージ済みで head か base が承認と違う | `refused`。流し直さない（[#4420](https://github.com/devbasex/ai-plugins/issues/4420) の条件 2。人が確かめる） |

### 単体テストの観点

- `tests/progress/test_definition.py` で、モードごとの流れるステップの並びを固定する
  - `standard`・`legacy-refactor` は、設計承認ゲートが「ドキュメントレビュー」に 1 つだけある
  - 順は「詳細設計」→「計画」→「ドキュメント再構成」で、マージは承認の後にある
  - ほかのモードの並びは変わらない
  - `documentation` の「ドキュメントレビュー」は、必須の入力が `worktree`・`branch` だけで、出力に `mvv_gate` が無い
- `tests/progress/test_sprint_templates.py` で、3 つの新しいテンプレートの argv を組む

## cross-review の `--reviewed-base-out` と `ndf ci merge --verify-merged`

満たす決定: 決定 1、I7。

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_sync` | `domains/crossreview/drive.py` | 変更 | 変えない | `RunConfig.follow_base` が真なら、`--base` でなく PR の今のマージ先を読んで取得し、比較元にする |
| `_reusable` | 同上 | 変更 | 変えない | `follow_base` で、前のラウンドの記録の `base_branch` が今と違えば偽 |
| `cmd_run` | `domains/crossreview/cli.py` | 変更 | 変えない | `--reviewed-base-out` を受け、始めに消し、`--reviewed-out` を書くときに最後のラウンドの `base_branch` を書く |
| `_merge_pull` | `ci_cli.py` | 変更 | 変えない | `--verify-merged` があり、マージ済みの PR の head か base が承認の値と違えば `refused` |
| `_watch` | 同上 | 変更 | 変えない | `--head <SHA>` があれば、読んだ PR の HEAD が違う時点（開始と成功の確定）で `approval_stale` |
| `pulls` | `adapters/github.py` | 変更 | `(numbers, spare=(), *, rest_fallback: bool = True)` | 偽なら REST へ読み替えない |

- `RunConfig`（`settings.py`）に `follow_base: bool = False` を足し、`--reviewed-base-out` を渡した起動だけ真にする。ラウンドの記録に `base_branch` を足す
- マージ先が前のラウンドと違うラウンドは、前の head からの差分を使わず、新しいマージ先との全差分を渡す。作り方は `--base` からの全差分と同じである
- `--verify-merged` は `--approved-head`・`--approved-base` と組でだけ受ける（無ければ `invalid`）

| 失敗 | 扱い |
| --- | --- |
| PR のマージ先を読めない | 今の `_merge_target` と同じく `precondition` |
| `--reviewed-base-out` を書けない | `precondition` |

単体テストの観点: 起動の間にマージ先が変われば全差分を読み直して新しい名前を書く。`--verify-merged` なしのマージ済みは今のまま `ok`。`ndf ci watch --head` は、待つ間に HEAD が変われば成功にしない。`rest_fallback=False` は利用枠の失敗をそのまま上げる。

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
| `advance` | `units_invoke.py` | 新設 | `(ctx, merge_sha) -> None` | スプリントブランチを取得し、作業場所を ff で進め、HEAD がマージのコミットを含むことを確かめる |

`State` は、状態の種類と、マージ済みならマージのコミット（`Pull.merge_sha`）を持つ。

再送の記録は、単位ごとの保存先の `queue.json`（`{"pr": <番号>, "resent": <真偽>, "queued_at": <時刻>}`）に持つ。`queued_at` は、その PR が最初にキューの待ちになった時刻で、書いた後は打ち直しでも再送でも変えない。`resend` では、`ndf ci merge` を打つ前に `resent` を真にして書く。打つ途中で中断しても回数が戻らない。起動のたびにこのファイルを読み、PR の番号が同じなら値を引き継ぐ。

**代表の課題**は、単位のタスクの所属の課題のうち番号が最も小さいものにする。`ndf plan units` の結果の `issues` の最小値で、課題全体を 1 単位にしたものはその課題である。実装のフェーズの手順書の決まり（`issue` には単位の課題のうち番号の最も小さいもの）と、#4369 の決定 9（番号の最小）に合わせるためである。実装計画のファイル名の番号から選ぶと、単位にタスクの無い課題の題が単位の PR に付くことがある。ブランチ名、PR の題と本文、`guard` の照合がこの値を使う。

### 始める前

`prepare` は次の順に見る。上で止まれば下は見ない。

1. `required(snap)` が真なら、`approved_of`。無ければ始めない
2. #4369 の開始前の確かめを呼ぶ（取得・配布先として宣言したブランチでないこと・ff・push）。単位ごとに流すか、#4369 のこれまでの経路（`impl` を 1 回）で流すかは、実装計画の「レビューの単位」の見出しか単位のファイルがあるかで決める。どちらも、手元と取得した先端の両方で調べる。承認が要るスプリントは、常に単位の経路にする。ここで決まった HEAD を `start_head` とし、3 より後の照合・読み込み・起動の判定は、すべてこのコミットを読む
3. `required` が真なら、承認した文書が届いていることを確かめる。次のどちらかのコミットが、`start_head` と同じか `start_head` の祖先なら通る（`git merge-base --is-ancestor <コミット> <start_head>`）。どちらでもなければ始めない。SHA の一致は条件にしない。単位をマージするたびにスプリントブランチが進むためである
   - 記録の head
   - 取り込みのコミット（`design-sync.json` の、`record_head` が記録の head と同じもの）
4. 記録があれば、`mismatches`。1 件でもあれば始めない。起動に使う課題・扱い・省いた課題・上限・受け入れ条件の数は記録の値にする
5. 古い定義で単位の欄があれば、単位のファイルの有無にかかわらず `units_convert.convert` を呼ぶ。変換が完了して承認も要らなければ、`convert` は None を返す。変換がコミットを足したら `start_head` を取り直す。この変更より後に作ったスプリントで、欄があるのに単位のファイルが無ければ始めない
6. `required` が真なら、記録の `targets.omitted` の課題を、課題ごとの単位（`units_decide.single`）にする。全部の課題が省いた課題で欄もファイルも無くても、単位の経路を続ける。`required` が偽で、欄もファイルも無ければ、#4369 の「これまでの経路」（単位に分けず `impl` を 1 回）
7. 単位のファイルを `units_files.check`（`docs` は偽）に通す。違反があれば始めない

「この変更より後に作ったスプリント」は、保存した定義の工程「実装」の `optional_inputs` に `plan_omitted` があることで見分ける。無いものが「古い定義」である。この変更が足すキーだからである。

スプリントは、保存した工程の定義の並びのまま流れる。`per_unit` は、単位ごとに流すことを表す工程の指定で、#4369 が足す。

**工程の定義に `per_unit` が無いスプリント**（#4369 より前に作ったもの）: 実装計画に単位の欄があれば、`invoke` は単位を流す処理へ渡し、上の 5 の変換を通す。#4369 の決定 4（定義に `per_unit` が無ければ `impl` を 1 回）は、単位の欄が無いスプリントにだけ残る。設計書の決定 10 が、分けたはずの変更を 1 本にしないことを求めるためである。

### 処理の手順

`run_stage` は、起動・マージ・GitHub の読み取りを主のスレッドで 1 つずつ行い、`impl` のプランだけを `ThreadPoolExecutor` で流す。

1. 開始時の一覧でマージ済みの単位を除いて、`graphlib.TopologicalSorter` のグラフを組む。マージ済みの単位を指す依存も除く（`done` は `get_ready` が返す前の単位に呼べないためである）
2. `get_ready()` が返した単位を、すべて起動待ちのキューに入れる（`get_ready` は同じ単位を二度返さない）。空いた本数だけ、キューから `merge` の順に取り出して起動する。本数の上限は `named_cap.read(os.environ, <状態の保存先>).value`
3. `concurrent.futures.wait(FIRST_COMPLETED)` で待つ。待ちの PR があるときは、次に問い合わせる時刻を持ち、その時刻までを上限にして待つ。`units_queue.read` を打つのは、その時刻に達したときだけで、待ちの PR をまとめて読む。プランの完了で先に戻った場合は読まず、時刻も変えない。間隔は `test-triage.check-wait` である
   - 次の問い合わせの時刻を決めるたびに、PR ごとに、`queue.json` の `queued_at` からの経過を `ci_budget` と比べる。ほかの単位が動いている間も、打ち直した後も、同じ時刻から数える
   - 超えた PR は、読むのをやめて `ci_pending` の失敗にする。GitHub の利用枠が戻る時刻がこの上限より後なら `infra_wait` の失敗にする。PR は GitHub のキューに残っているとみなし、「キューに残る PR」として覚える。補った単位を起動するかの判定に使う
4. 終わったプランが同時に複数あれば、`merge` の順に `merge_unit` を打つ。`ci_pending` で PR がキューにあれば、待ちの一覧へ入れて次へ進む
5. `merge_unit` が `ok`（直接のマージ）か、`units_queue.read` が `merged`（キューでのマージ）になった単位は、先に `advance` でスプリントの作業場所をマージのコミットを含む先端へ進める。進められた単位だけを `done` にして、2 へ戻る。`advance` の失敗は止める（`precondition`）
6. 失敗した単位は `done` にしない（後に続く単位は `get_ready` に出ない）。`cause.kind` が `usage_limit` なら、以後は起動せず、実行中を待って結果を保存し、成功した単位はマージする
7. 起動できる単位も実行中も無くなったら、待ちがある間は手順 3 の時刻と上限で読み続ける
8. 全単位がマージ済みなら #4369 の `sprint_pr`。残りがあれば `stopped` で、残りの単位と理由を `cause.detail` に書く。残りの原因が待ちの上限だけなら、種類は `ci_pending` か `infra_wait` にする

打ち直しでは、#4369 の `resume_of` が `merge` を返す単位でも、先に `units_queue.read` を打つ。キューにあれば待ちの一覧へ入れ、`ndf ci merge` を打たない。`queued_at` から `ci_budget` を超えていれば、この 1 回の読み取りの後は問い合わせを続けず、上の上限の扱いにする。

単位の欄が無い課題と実装計画を省いた課題は、課題全体を 1 つの単位にして補う。この単位は `touches` が分からない。そのため、次のどちらもそろった後に、課題の番号の順に 1 つずつ起動する。

- 単位のファイルの単位に、起動待ち・実行中・キューの待ちが無く、キューに残る PR も無い
- 単位のファイルの単位が、`done`・失敗・先に要る単位の失敗で始められない、のどれかになっている

単位のファイルの単位が失敗しても、この単位は始める。依存の指定が無いためである。ただしキューに残る PR がある間は始めない。その PR が後からマージされると、同じファイルを触っていた場合に競合するためである。この場合は手順 8 の `ci_pending` か `infra_wait` で終え、呼び手は別の作業へ進める。このように、キューに残る PR がある間は補った単位の起動を止めておく（次の表の「保留」）。打ち直した実行では、`ndf ci merge` の再送まで終えた後の PR の状態で、保留を解くかを次の表で決める。読み取った状態がキュー外でも、同じ実行で再送したなら保留を続ける（表の最後の行）。

| その起動の最後に分かった状態 | 保留 |
| --- | --- |
| マージ済み、閉じている | 解く |
| 開いたままキューに無く、再送は済んでいる（この起動では打たない） | 解く（その単位は失敗） |
| キューにある（再送でキューに入り直した場合を含む） | 続ける |
| 状態を取得できない | 続ける |
| この起動で再送し、マージできたと確認できない | 続ける |

補った単位が失敗しても、次の補った単位を始める。利用上限（`usage_limit`）の後は、手順 6 のとおり何も起動しない。

### 異常系の処理

| 失敗 | 扱い（停止理由の種類） | 利用者に見える理由 |
| --- | --- | --- |
| 承認の記録が無い | 始めない（`precondition`） | `ndf progress restore` で状態を戻すか、利用者に判断を求める。工程「ドキュメントレビュー」を流し直す案内はしない（設計 PR はもうマージ済みのため） |
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
| `ndf progress units run` と `ndf progress next` が起動した作業 | 同じスプリント | `run_lock` の中で起動マーカー（`run.launching`）も確かめる | 工程のイベント |

### 単体テストの観点

`tests/progress/test_units_invoke.py`（#4369 が作る。1,000 行を超えるなら `test_units_parallel.py` に分ける）。時計・待ち・`Ports` を差し替え、本物の時間を待たない。観点は設計書のテスト設計の受け入れ条件 3・4、I5・I6、利用上限の行である。次も確かめる。

- 承認が要るスプリントで全部の課題が実装計画を省いたときは、課題ごとの単位を流し、`impl` を 1 回にまとめない
- 依存の無い単位が上限より多いとき、初回に枠へ入らなかった単位も、枠が空けば起動する
- 単位のファイルの単位が失敗しても、実行中と待ちが無くなれば、補った単位を起動する
- 依存のある複数の単位をマージした後の打ち直しは、例外を出さず、残りだけを流す
- 問い合わせの間隔の中にプランの完了が何件返っても、キューの読み取りは増えない
- ほかの単位が実行中でも、上限を超えた PR は読むのをやめる
- 上限を超えた PR がある間は、補った単位を起動せず、`ci_pending` か `infra_wait` で終える
- 期限を超えた PR の打ち直しで、再送がキューに入り直すか状態を取得できなければ、補った単位を起動しない
- 打ち直しをまたいでも、上限は最初にキューの待ちになった時刻から数える
- キューでマージした先行の単位は、作業場所が進んだ後にだけ後の単位を始める
- 打ち直しをまたいでも、キューから外れた PR の再送は 1 回だけである
- 取得で作業場所が進んだときは、進んだ後のコミットと承認の記録を比べる
- 設計 PR の head がスプリントブランチ自身の経路（`design-sync.json` が無い）では、最初の単位をマージしてスプリントブランチが記録の head より先へ進んだ後の打ち直しも、手順 3 を通って残りの単位を流す

## この変更より前のスプリントの変換（`progress/units_convert.py`）

満たす決定: 決定 10。

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `convert` | `units_convert.py` | 新設 | `(ctx, ports, answer: Optional[str]) -> Optional[StageResult]` | 設計書の決定 10 の「再開点」の表の順に進め、照合か再承認を確かめてから反映して push する。実装へ進めるなら None。単位のファイルがある打ち直しでも毎回通る |
| `compare` | 同上 | 新設 | `(old: Sequence[Unit], new: Sequence[View]) -> list[str]` | 単位の名前・含むタスク・旧い先に要る単位の違い |

- 変換の担当は、新しいテンプレート `implementation-plan-convert` を `ports.call_plan` で流す。今のテンプレート `implementation-plan` は push のステップを持つので使わない。差し込み `convert` を渡し、担当への指示は上の「テンプレート」の変換のときの指示になる。旧い表の「実際の行数」の値は、担当が `units[].actual_lines` へ移す
- 旧い承認の表は、実装計画を `git show` で読んで `review_units_decide.lineup` に通す。読むコミットは `records.approved_head(v, "design")` の head である
- 担当の作業場所は、`units_sync.push_url` を通った URL から読んだスプリントブランチの先端から作る一時の作業場所（detached HEAD）である。通らなければ、取得も担当の起動もせずに `stopped`（`refused`）にする。期待するリポジトリは、ステートマシンの `repo` を解析した値である。手元のスプリントブランチからは作らない。手元が送り先より古いと、変換し直しても同じ古いコミットが起点になり、cherry-pick の競合を繰り返すためである。コミットは `refs/ndf/convert/<ステートマシンの ID>` で持ち、作業場所は消す。反映の前の照合と `mismatches` は、このコミットを読む
- ref は、反映して push した後か、却下の回答を読んだ時点で消す
- 照合が違えば、先に `design_record.reapproved_of` の記録を読む。`mismatches` が空なら有効で、下の「反映」へ進む
- 有効な記録が無ければ、下の「再承認の資料」の内容を持つ `reapproval.md` を作業の保存先に書き、`design_record.converted` の記録を `report.design_target` に持つ `gate` の結果（選択肢は承認と却下）を返す
- 回答は `ndf answer` が工程「実装」のイベントに書き、payload の `design_target` が記録になる。別のファイルは持たない
- `ndf progress units run` は回答を引数で受けない。`convert` が `gate` を返したら、`next` と同じく回答の待ちをイベントに書いて返す

### 再承認の資料

`reapproval.md` は、反映の手順 4 で再び問う場合も含め、問うたびに書き直す。承認者が今回何を判断するかを、資料だけで分かるようにするためである。次を載せる。

- 再承認が必要になった理由（旧い表との違いか、前回の承認の後に変わった項目）
- 旧い表と新しい分け方を並べた表
- 前回承認したコミットから、問う対象のコミットまでの実装計画の差分。前回の承認は、再承認の記録があればその `head`、無ければ旧い設計承認の head である
- 実行対象の違い（再承認の記録がある場合の `mismatches`）

### 反映

照合が一致するか、有効な再承認の記録があれば、次の順に進める。スプリントの作業場所は、push が通るまで変えない。作業場所を先に進めると、push が拒否されてリモートが先へ進んでいた場合に手元とリモートが分かれ、打ち直しが開始前の確かめで止まるためである。

持っておいたコミットが無い打ち直し（反映済み）では、push するものが無いので、次の順を通らない。

1. `units_sync.push_url` で URL を照合し、その URL からスプリントブランチの先端を読む（「送るコミットの確かめ」と同じ読み方）。以後の手順は同じ URL を使い、読み直さない
2. 送るコミットを一時の作業場所で作る。持っておいたコミットの親がその先端なら、そのまま使う。違えば、先端へ cherry-pick する。競合すれば、持っておいたコミットを捨て、手順 1 の先端から一時の作業場所を作り直して変換し直す
3. 送るコミットを読み元にして、変換が変えた範囲を確かめる。違反があれば、捨てて変換し直す
   - 実装計画で変えてよいのは、「レビューの単位」と「並列と直列」の 2 つのセクションだけである。この 2 つを除いた本文が、親のコミットと同じでなければ違反である
   - `units_files.check`（`docs` は真）
4. 同じコミットで、承認した内容を確かめる。次のどちらかに当たれば push せず、ref を送るコミットに置き換えて、再承認の `gate` を返す
   - 再承認の記録がある場合: 記録との `mismatches` に違いがあるか、実装計画の中身が記録の `head` と違う。`compare` の結果にはよらず確かめる。実装計画の全体を比べるのは、この push の前だけである
   - 再承認の記録が無い場合: 旧い表との照合 `compare` が違う
5. `units_sync.destination_errors` と `units_sync.outgoing_errors` を通す。`destination_errors` には手順 1 の URL を渡す。渡す記録は `design_record.converted` の値で、変えてよいファイルは、スプリントの課題の単位のファイルと実装計画である。`outgoing_errors` には手順 1 で読んだ先端を渡し、読み直さない。違えば push せず `stopped`（`refused`）
6. `git push --no-follow-tags --recurse-submodules=no -- <手順 1 の URL> <送るコミットの SHA>:refs/heads/<スプリントブランチ>` を打つ。強制はしない。送るコミットの親は手順 1 の先端なので、その後に送り先が進めば、git が拒否する
7. push が通ったら、スプリントの作業場所を `git merge --ff-only` でそのコミットへ進める

| 失敗 | 扱い |
| --- | --- |
| 担当の失敗・push の拒否 | `stopped`。打ち直しは「再開点」の表の続きから |
| 旧い承認の HEAD を読めない | 始めない（`precondition`） |
| 却下 | 始めない（`refused`） |

単体テストの観点は次のとおりである。

- 再開点の 4 行
- 依存を足しただけは違いにしない
- 旧い表にあった単位の依存（先に要る単位）が変換で消える場合は、承認の回答があるまで実装を流さない
- 承認の後にファイルを直すか、実行対象が変わると再び問う
- 単位の分け方が同じで実装計画の本文だけが変わった再承認でも、`reapproval.md` に理由と差分が載る
- 再承認の回答の前と却下の後は、手元とリモートのスプリントブランチが変わらない
- 承認を待つ間に先端で実装計画か扱いが変わると、単位の分け方が旧い表と一致していても、push せずに再び問う
- 実装計画の 2 つのセクションの外を変えた変換は push しない
- 手元より送り先が進んで cherry-pick が競合すると、送り先の先端から変換し直す
- 反映の 1 回の試行で、送り先の URL への `git fetch` は 1 回である
- push が拒否された後は、手元のスプリントブランチが変わらず、打ち直しは新しい先端へ載せ直して反映する
- push の URL が「送り先の確かめ」の拒否の例に当たれば、変換の担当の起動の前の取得も push も起きない
- 手元だけのコミットは送らない。送り先の違いと、対象外のファイルを含む変換のコミットは push しない
- ファイルを作った直後の中断（未コミット・未 push・未再承認）は、打ち直しで続きから進み、実装を始めない

## 承認した文書の取り込み（`progress/units_sync.py`）

満たす決定: 決定 1・11、I8。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `github_target` | `adapters/git.py` | 新設 | `(url: str) -> Optional[GitHubTarget]` | URL の全体が 3 つの形に合えば `repo`（`<所有者>/<リポジトリ>`）と `secret`（認証情報を含むか）。合わなければ None |
| `Git.push_urls` | 同上 | 新設 | `(remote: str) -> list[str]` | `git remote get-url --push --all` の行 |
| `Git.rewrites_again` | 同上 | 新設 | `(url: str) -> bool` | 引数に渡した URL を git が別の値に読み替えうるか |
| `push_url` | `units_sync.py` | 新設 | `(git, repo: str, url: Optional[str] = None) -> tuple[Optional[str], list[str]]` | 下の表の「送り先の URL」の条件。通れば URL を返す。`url` を渡せば読み直さずに同じ値を確かめる |
| `destination_errors` | 同上 | 新設 | `(git, record, context, machine_repo: str, url: Optional[str] = None) -> tuple[Optional[str], list[str]]` | 照合した URL と、送り先のリポジトリとブランチがゲートの記録とスプリントの状態に合わない点 |
| `outgoing_errors` | 同上 | 新設 | `(git, tip: str, head: str, allowed: Sequence[str]) -> list[str]` | 下の「送るコミットの確かめ」に合わない点。変換の反映も使う |
| `sync` | 同上 | 新設 | `(st, machine_id, cwd, hub) -> dict` | 確かめてから取り込み、照合して push する |

### 送り先の確かめ

`destination_errors` は次の 4 つを見る。リポジトリの比較は大文字小文字を区別しない。1 つでも違えば、取り込みもコミットも push もしない。

| 確かめる値 | 取り方 | 合う条件 |
| --- | --- | --- |
| 送り先の URL | `push_url`（下の手順） | 手順のすべてを通り、リポジトリが記録の `review.repo` と同じ |
| スプリントのリポジトリ | ステートマシンの `repo`（作成の時点のリモートの URL）を `github_target` に通す。送らない値なので `secret` は問わない | 解析でき、記録の `review.repo` と同じ |
| 送り先のブランチ | 記録の `sprint_branch`。突き合わせる相手は、文脈の `branch`（工程「作業場所の用意」が書く）と、文脈の `worktree` の今のブランチ | 3 つがすべて同じ。記録に `sprint_branch` が無ければ合わない |
| 配布先として宣言したブランチでないこと | #4369 の `released_branches`（`.ndf/project.json` の `branch_strategy` の `branches.base`・`branches.production`・`channels[].ref`・`release.deliveries[].branch`） | 含まれない |

- push は `git push --no-follow-tags --recurse-submodules=no -- <照合した URL> <照合した HEAD の SHA>:refs/heads/<記録の sprint_branch>` で打つ。upstream の設定を使わない。2 つの指定は、手元の設定がタグやサブモジュールを一緒に送るのを止める
- リモート名は `Git.remote()` である。使うのは URL を読む時だけで、`git fetch`・`git push` には渡さない
- URL は認証の値を含みうるので、理由には `<所有者>/<リポジトリ>` と当たった条件だけを出す
- push 先は取得先と別の URL を設定できるので、取得先の URL では確かめない

`push_url` は次の順に確かめる。1 つでも当たれば、URL を使う通信をせずに返す。認証情報の確かめは、URL をコマンドの引数に渡すより前にある。

1. `git remote get-url --push --all -- <remote>` が 1 行である（`url` を渡されたときは読まない）
2. `github_target` が None でない。次の 3 つの正規表現の全体一致（`fullmatch`）で、方式とホスト名は大文字小文字を区別しない。所有者とリポジトリは `[A-Za-z0-9_.-]+`（`.`・`..` を除く）で、末尾の `.git` と `/` は 1 つずつ許す。クエリ・`#`・空白・`%`・余分なパスの要素があれば合わない
   - `https://[<利用者の情報>@]github.com[:443]/<所有者>/<リポジトリ>`
   - `ssh://git[:<パスワード>]@github.com[:22]/<所有者>/<リポジトリ>`
   - `git@github.com:<所有者>/<リポジトリ>`
3. `secret` が偽である。理由に、直し方（`git remote set-url --push` と credential helper）を書く
4. `repo` が期待するリポジトリと同じである
5. `rewrites_again` が偽である。`git config --null --get-regexp '^url\..*\.(insteadof|pushinsteadof)$'` の値のどれかが URL の先頭に一致するか（終了コード 1 は設定が無い）、`git remote` の一覧に URL と同じ名前があれば真

git 2.53.0 で実行して確かめたことは次のとおりである。

- `insteadOf` は連鎖せず 1 回だけ当たる。`get-url` の返す値を引数に渡すと、もう一度当たる
- 引数の URL への push には、`pushurl` の有無によらず `pushInsteadOf` が当たる。取得には当たらないが、同じ URL で送るので両方の規則を見る
- `remote.<URL>.url` があると、引数の URL はそのリモートとして読まれる

### 送るコミットの確かめ

push は、手元の HEAD までのコミットをすべて公開する。そのため、送り先から読んだ先端と比べて、新しく送るコミットを限る。

- 送り先の先端は、照合した URL に `git fetch --no-tags -- <URL> refs/heads/<記録の sprint_branch>` を打ち、`FETCH_HEAD` から読む。取得先の URL と remote-tracking ref は使わない。読めなければ `stopped`（`precondition`）
- `git rev-list <先端>..<HEAD>` が空か 1 コミットである
- 1 コミットある場合、そのコミットはマージのコミットでなく、変えたファイル（`git diff-tree`）がすべて呼び手の渡す対象のファイルである
- `sync` の対象は取り込むファイルで、中身が記録の head と同じことも確かめる

合わなければ `stopped`（`refused`）にし、コミットとファイル名を示す。

`outgoing_errors` は先端を読まず、呼び手が読んだ値を受け取る。呼び手は 1 回の実行で push の前に 1 度だけ読み、同じ値を使う。確かめのたびに読み直さないのは、読み直しても push までの間に送り先が進むことは防げず、進んだ場合は強制しない push が拒否されるためである。読み直しは、通信の回数が増えるだけになる。送り先が巻き戻された場合はこの拒否が働かないが、そのための `--force-with-lease` は足さない。この push には強制の指定を持たせないためである。

### 処理の手順

`sync` の手順は次のとおりである。取り込むファイルは、単位のファイルと実装計画と、番号の並びが一致する `-requirements.md`・`-design.md`・`-design-detail.md` である。

1. `approved_of`。無ければ `stopped`
2. `destination_errors`。1 件でもあれば `stopped`（`refused`）。返った URL を、手順 3 以降の取得と push のすべてに使う
3. `<work_root>/design-sync.json`（`{"record_head", "commit"}`）の `record_head` が記録の head と同じで、手元に `commit` があれば、送り先の先端を読む（上の「送るコミットの確かめ」の読み方）。ここで読んだ値を、手順 6 と 8 でも使う
   - 先端が `commit` を含めば `ok`
   - 手元の HEAD だけが含めば（push の失敗の後）、手順 8 へ進む
4. 手元のスプリントブランチが記録の head を祖先に持てば、取り込むものは無いので、PR を読まずに `ok`。`ndf progress next` の経路は、設計 PR の head がスプリントブランチ自身なので、ここで終わる
5. 記録の PR を読み、マージ済み・head が記録の head・base が記録の base を確かめる。base の正しい宛先は、通常は `pr_target("doc", <branch>)`、設計 PR の head がスプリントブランチ自身なら `pr_target("sprint")` である
6. 作業場所を検査する。取り込むファイルに未コミットの変更があるか（`git status --porcelain --untracked-files=all -- <ファイル>` が空でない）、マージの途中（`MERGE_HEAD` がある）なら `stopped`（`precondition`）。ファイルを書き換えない。次に `merge-base` から両側の変更を調べる。スプリント側も変えていて中身が違うファイルがあれば `stopped`。送り先の先端を読み（手順 3 で読んでいればその値を使い）、手元の HEAD で「送るコミットの確かめ」を通す。手元だけの別のコミットがあれば、取り込みのコミットを作らずに止まる
7. `git checkout <記録の head> -- <ファイル>` し、差があれば `git commit -- <ファイル>` でそのファイルだけをコミットする（ほかのステージ済みの変更は含めない）。差が無ければ（コミットの直後に中断した打ち直し）コミットしない。HEAD の SHA を `design-sync.json` に書く
8. 照合する。HEAD の各ファイルの blob が記録の head の同じファイルと同じでなければ、push せずに `stopped`。手順 6 で使った先端で「送るコミットの確かめ」を通し、通らなければ push せずに `stopped`
9. 照合した HEAD の SHA を push する。同じ URL から先端を読み直し、その SHA を含めば `ok`。push が拒否されれば `precondition`。理由には、取り込みのコミットを送り先の先端の上へ載せ直して（`git rebase`）打ち直すと書く。マージのコミットを作ると「送るコミットの確かめ」を通らないためである

### 異常系の処理

| 失敗 | 扱い（停止理由の種類） |
| --- | --- |
| 承認の記録が無い | `precondition` |
| 送り先の違い | `refused`。項目ごとの記録の値と今の値 |
| 未マージ・head か base の違い | `precondition`（未マージ）・`refused` |
| 取り込むファイルの未コミットの変更・マージの途中 | `precondition`。ファイル名 |
| 競合 | `base_conflict`。ファイル名 |
| コミットの中身が承認と違う | `refused`。push しない |
| push の拒否 | `precondition`。強制しない |

### 単体テストの観点

次のどれでもコミットと push が起きないことを、リモートの ref で確かめる。

- 別のリポジトリの push 先
- 別ホストで所有者名とリポジトリ名が同じ push 先（`https://notgithub.com/o/r`・`https://github.com.example.com/o/r`・`https://example.com/github.com/o/r`・`git@notgithub.com:o/r`）
- 解析できない形（`file://`・ローカルのパス・`http://`・ssh の別名）
- 認証情報を含む URL（`https://<token>@github.com/o/r`）。理由の文にも git の引数にも token が現れないことも確かめる
- push の URL が 2 つ以上（どちらも同じリポジトリでも拒否する）
- `insteadOf` か `pushInsteadOf` がもう一度当たる設定と、URL と同じ名前のリモート
- 別のブランチに切り替えた作業場所
- 文脈の `branch` と作業場所がそろって、記録の `sprint_branch` と別のブランチ
- 文脈に `branch` が無いか、記録に `sprint_branch` が無い
- 取り込むファイルに未コミットの編集がある（編集が残ることも確かめる）
- 手元のスプリントブランチに、送り先に無い別のコミットがある
- コミットの中身が承認した head と違う（commit のフックが書き換える形）

次も確かめる。

- 取り込みと関係ないステージ済みの変更は、コミットに入らない
- push が拒否された後の打ち直しは、競合にせず送り直し、リモートに届くまで `ok` にしない
- 取得先と push 先の URL が違うリモートでは、push 先に届けば `ok` にする
- `git fetch` と `git push` の引数は照合した URL で、リモート名でない

テストのリモートの URL は `git@github.com:o/r.git` にし、環境変数 `GIT_SSH_COMMAND` の偽の ssh が手元の bare リポジトリで受ける。`insteadOf` で手元へ向けると、上の拒否に当たるためである。この形で push と取得ができることは、git 2.53.0 で実行して確かめた。URL の 3 つの形と拒否の例は、`github_target` の単体テスト（`tests/adapters/test_git.py`）でも縛る

## 実装のプランの確かめ（`progress/units_guard.py`）

満たす決定: 決定 12、I5。

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_launch` | `units_guard.py` | 新設 | `(work_root, branch, record, issue, unit_file: Optional[Path]) -> None` | `units/guard/<ブランチの sha256 の先頭 16 字>.json` に `{"branch", "record_head", "issue", "unit_sha256"}` を書く |
| `guard` | 同上 | 新設 | `(st, repo, issue, base, branch, unit_file) -> list[str]` | 設計書の決定 12 の表の判定。空なら通す |
| `read_machines` | `store.py` | 新設 | `(conn, repo) -> list[dict]` | そのリポジトリのスプリントの一覧 |

- 対象のスプリントは、終わっていないもののうち、文脈の `issues` に `--issue` があるか、文脈の `branch` が `--base` のものである
- 「宛先がスプリントブランチ」は、`--base` が `model.branch_prefix("sprint")`（`sprint/`）で始まることである
- 指示のファイル（`--unit-file` に渡すファイル。#4369 の `unit_file` で、`unit_run` が担当タスクの本文を足す `unit.md`。JSON の単位のファイルとは別のファイル）は、中身の sha256 を比べる。指示の無い単位は空文字である

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
| `ndf progress units run` | `<ステートマシンの ID>`・`--cwd` | 下の「実装を流す手順」。工程「実装」だけを、`ndf progress next` と同じ処理で流す |
| `ndf progress units sync` | `[<ID>]`・`--cwd`。`<ID>` を省けば環境変数 `NDF_MACHINE_ID`（工程のステップ `design-sync`）。取り込みは文脈の `worktree` で行う | `units_sync.sync` |
| `ndf progress units guard` | `--issue`・`--base`・`--branch`・`--unit-file` | `units_guard.guard` |

**実装を流す手順**: `units run` は、工程「実装」を `ndf progress next` と同じ処理で流し、同じ工程のイベントを書く。別の流し方を持つと、失敗や回答の待ちが記録に残らず、開始前の検査も抜けるためである。違いは、別のプロセスを起動せずその場で流すことと、工程「実装」の外へ進まないことの 2 つである。

1. ステートマシンの `run_lock` を取る
2. 同じロックの中で `run.launching` を見る。`next` が起動マーカーを書き、起動した作業がまだロックを取っていなければ、何も流さず、`next` と同じ「作業が動いている」結果（`ok`。次は `ndf progress wait <ID>`）を返す
3. `run.migrate_definition` を呼び、`ndf progress next` と同じ `Env` で `run.Drive`（`inline=True`）を組む
4. `run.decide` が工程「実装」の `Action.START` か `Action.RESUME` を返す間、`run.play_move` を打つ。`play_move` が結果を返したら、その結果で終わる
5. 繰り返しを抜けたら、下の表の結果を返す

| 抜けた時点の `run.decide` | 結果 |
| --- | --- |
| 工程「実装」の `Action.FAILED` | `run.report`。`next` と同じ失敗の結果で、再試行などの経路を持つ。再試行を選ぶまで、打ち直しても流さない |
| 工程「実装」の `Action.GATE` | `run.report`。`next` と同じ回答の待ちの結果。回答は `ndf answer` で返す |
| 別の工程・`DONE`・`COMPLETE` で、この実行で工程「実装」を流した | `ok`。次は `ndf progress next <ID>` |
| 同じで、1 手も流していない | `stopped`（`precondition`）。次の 1 手の工程とアクションを理由に書く |

- `START` は `play_move` が `_start_once` を通る。工程の `when`・入力・プランの差し込みの検査は `next` と同じで、`when` が偽なら工程を飛ばすイベントを書き、実装は起動しない。この場合の結果は `ok` で、飛ばした条件を書く
- 実装のステップの失敗と再承認の待ちは、`_run_step` が `next` と同じイベントに書く。そのため、次の `units run` と `next` は同じ `FAILED`・`GATE` を読む
- `run.py` では、今の `_step` を `play_move`、`_launching` を `launching` に改名して公開する（行は増えない）。繰り返しは `units_cli.py` に置く
- supervisor は工程「実装」に `ndf progress finish` を打たない。ステップ `gathered` と工程の終わりまで `units run` が流す

`test_units_invoke.py` では、次を確かめる。本物の別プロセスは起動せず、起動マーカーと作業の生死は差し替える。

- 上の表の行ごとに、実装のプランが起動するかしないか
- 起動マーカーがあり作業がロックを取る前は、実装を起動しない
- `units run` で実装が失敗した後に同じコマンドを打つと、実装を起動せず、失敗の結果を返す
- `units run` で再承認が要ると、回答の待ちが記録され、`ndf progress next` が同じ質問を返す
- 工程の `when` が偽なら、実装を起動しない

承認した HEAD、承認の要否、送り先、再承認の回答を渡す引数は作らない。承認の回答だけがあって設計 PR のマージや `design-sync` が残るときは、次の工程が「ドキュメントレビュー」なので始まらない。このことも同じテストで確かめる。単位の経路でないスプリント（分け方の承認が要らず、欄もファイルも無い）では、`next` と同じく `impl` を 1 回流す。`ndf check --write-spec` で `docs/contracts/commands.md`・`env.md`・`contracts/schemas/io/` を生成し直す。

## 手順書

満たす決定: 決定 1・10・11・12。

| ファイル | 直す所 |
| --- | --- |
| `skills/implementation-plan/SKILL.md` | 単位のファイルの書き方と `render`。実装の途中の分け直しは、実際の行数の追記だけにする |
| `skills/design/SKILL.md` | 手順 7 の設計 PR の流れ（承認とマージは「ドキュメントレビュー」の終わり） |
| `skills/requirements-design/references/document-roles.md` | 実装計画の順序と、設計 PR に載ること |
| `skills/development-workflow/SKILL.md` | 工程表の順序と承認ゲートの位置 |
| `skills/development-workflow/references/agent-layers.md` | 単位ごとにプランを組む手順を `ndf progress units run` に置き換え（工程「実装」に `ndf progress finish` は打たない）、設定が `doc_prs: base`（承認した HEAD のマージ先がベースブランチ）のときの `sync` を書く |
| `skills/development-workflow/references/{approval-request,stage-notes}.md` | 承認資料の単位の表、設計 PR の流れ |
| `skills/issue-plan-strategy/references/execution-plan.md` | 実行計画の実装の行は `check` の `items` から置く |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `judge` | `graphlib.TopologicalSorter`、`queue_decide.waves` | 標準ライブラリを実行した（2026-11-26）。`queue_decide.py` を読んだ | `graphlib` を使う。`waves` は理由と行数を持たない |
| `find`・`design_handlings` | `issue_docs.design_issues`、`inputs_decide.split_row`・`section_lines`、`collect.design_rows` | 3 つのファイルを読んだ | 使う。`design_rows` は触るファイルだけを読むので変えない |
| `build`・`stale` | `gate.Target`・`merge_approval.MergeTarget` | `gate/asking.py` を読んだ | `Target` の値と比べる。単位と扱いは持たないので記録を足す |
| `reset_drafts` | `run.review_again_drafts`・`stages.RESET` | `run.py` を読んだ | 同じイベントの形を使う。戻す範囲が違う |
| `destination_errors`・`push_url`・`github_target` | `git.repo_of_url`・`Git.remote`・`Git.remote_url`、#4369 の `released_branches` | `adapters/git.py` を読み、git の URL の書き換えを実行した | `Git.remote` を使う。`repo_of_url` は部分一致なので使わず、全体を解析する読み手を足す。ほかの呼び手の `repo_of_url` は変えない |
| `units_queue.read` | `GitHub.pulls` | `adapters/github.py` を読んだ | 読み替えを止める指定を足して使う |
| `read_machines` | `store.read_machine` | `store.py` を読んだ | 1 件ずつの読み取りだけなので足す |
