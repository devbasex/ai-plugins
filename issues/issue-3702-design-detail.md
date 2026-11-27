# #3702 の詳細設計: 単位のファイル・承認の記録・単位の並列・設計のフェーズの工程の並び

[設計書](./issue-3702-design.md)の構成要素を、ファイルと関数の粒度へ下ろした文書である。決定の番号、不変条件の番号（I1〜I8）、規則の番号（U1〜U6・W1）は設計書を指す。コマンドの引数の意味と結果、処理の流れ、単位のファイルの形は設計書が持つので、写さずにセクション名で指す。パスは `plugins/ndf/` からの相対である。

「supervisor」は、工程を回す AI である。この文書の「プラン」は、手順をステップの列にした実行の定義で、実装計画とは別である。「単位の欄」は、実装計画の「レビューの単位」の欄を指す。「MVV 判定」は、プロジェクトの方針に沿うかの AI の判定である。

#4369 は、`ndf progress next` の工程「実装」が、単位の表を読んで単位を 1 つずつ流すようにする課題である。単位は、レビューの単位（1 本の PR として実装・レビュー・マージするタスクのまとまり）を指す。**前提にする #4369 の実装は、2026-11-27 の時点で `main` に無い。** この文書が「#4369 の」と書く関数とファイルは、[#4369 の確定仕様](../docs/specifications/ndf-implement-stage-review-units.md)と、レビュー中の[詳細設計の PR](https://github.com/devbasex/ai-plugins/pull/4567) の名前である。実装を始める時点で `main` の形が違えば、名前と引数を `main` に合わせる。

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
| `Proposal` | `file`（超えた単位のファイルのパス）・`unit`・`units: tuple[UnitDef, ...]`・`resplit: tuple[str, ...]`（切り直すタスク）・`remaining: tuple[Finding, ...]`・`repoint: tuple[tuple[str, str, Optional[int]], ...]`（付け替える依存。中身は表の下） |
| `Judgement` | `views`・`violations`・`warnings`・`proposals` |

分け方の案（`Proposal`）を適用する処理は、まず `units` が空かどうかを見る。

空の案は、単位を区切れなかったことを表す。適用するものは無いので、元の単位と、その単位への依存をそのまま残す。空の `units` で置き換えると元の単位が消え、そのタスクがどの単位にも属さなくなる。1 つで上限を超えるタスクがある場合は、切り直すタスクが `resplit` に入る。

`units` に単位がある場合は、次の 2 つを行って適用する。

- `file` の単位 `unit` を、`units` に置き換える
- `repoint` が指す依存の先を、`units` の最後の単位に変える

`repoint` の 1 件は、依存を持つ単位のファイルのパス、その単位の名前、依存の `Edge.issue` の 3 つ組である。その単位の依存のうち、先の名前が `unit` で `issue` が一致するものだけを変える。`issue` まで比べるのは、1 つの単位が別々のファイルの同じ名前の単位に依存できるためである。名前だけで選ぶと、分けていない単位への依存まで書き換わる。

`UnitDef` は #4369 の `review_units_decide.Unit`（流すときの単位）と別の型である。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `parse` | `units_decide.py` | 新設 | `(doc: Mapping, path: str) -> UnitFile` | スキーマを通った辞書を型にする。`issue` を省いたタスクはファイルの唯一の課題 |
| `judge` | 同上 | 新設 | `(files: Sequence[UnitFile], limits: Limits, criteria: Mapping[int, int]) -> Judgement` | 全ファイルを 1 つのグラフにし、違反・警告・導く値・案を返す。`criteria` のキーは、受け入れ条件を指してよい課題のすべてである |
| `split` | 同上 | 新設 | `(files, key: tuple[str, str], limits, criteria=None) -> Proposal` | 超えた単位を、依存の順の直列の単位に区切る |
| `single` | 同上 | 新設 | `(issue: int) -> UnitFile` | 課題全体を 1 つの単位にする。名前は `issue-<番号>`、`lines` は無し、`touches` は空、`whole` は真 |

### 処理の手順

`judge` は次の順に進む。参照の解決に失敗した時点（U1）でも、残りの規則は評価できる範囲で続ける。

1. 単位を `(ファイルのパス, 単位の名前)` の組にする。`Edge.issue` は、その課題を `issues` に持つファイルを探すためだけに使う
2. タスクのグラフ（ファイルごと）と単位のグラフ（全ファイル）を `graphlib.TopologicalSorter` に入れる。`CycleError` は U1 にする
3. 単位のグラフを並べた順に、実行順を「先に要る単位の実行順の最大 + 1」（無ければ 1）で決める
4. 同じ順に、各単位の閉包（間接を含む先に要る単位の集合）を求める。依存の順に並べられる単位では、先に要る単位の閉包が先に求まっているので、直接の依存とその閉包の和にする。こうすると依存の辺を 1 回ずつ見るだけで済む
   - 輪の上の単位と、輪に依存する単位（輪の下流）は依存の順に並ばず、先に要る単位の閉包が求まらない。これらは実行順を 0 にし、マージの順も 0 のままにする（手順 6 で振らない）。閉包は、依存をたどって届く単位をすべて集めて求める。閉包を空にはしないので、U3・U4 の判定はこれらの単位にも同じように当てる
5. 下の表の規則を当てる
6. `merge` は、実行順が 1 以上の単位にだけ振る。並びは実行順の順、同じ実行順の中は `(単位の最小の課題の番号, ファイルの中の並び)` の順で、1 から振る。実行順が 0 の単位（輪の上と下流）の `merge` は 0 のままにする
7. U6 の単位すべてと、W1 のうちタスクが 2 つ以上の単位に `split` を呼ぶ

| 規則 | 判定の式 |
| --- | --- |
| U1 | ファイルの中でタスクの `id` か単位の名前が重なる。`judge` が受け取った `UnitFile` で、1 つの単位の `tasks` に同じタスクが重なる。所属の数と行数は 1 回と数える。1 つの課題を 2 つのファイルの `issues` が持つ。`after`・`units[].tasks` の指す先が無い。グラフに輪がある |
| U2 | ファイルにタスクか単位が無い。単位のタスクが 0 個。タスクが 0 個か 2 個以上の単位にある。タスクの `issue` がファイルの `issues` に無い。`criteria` が指す課題が引数 `criteria` のキーに無い（対象外の参照）。番号が 1 未満か、`criteria[課題]`（本文の受け入れ条件の数）より大きい |
| U3 | タスク B の `after` のタスク A の単位が、B の単位でも B の単位の閉包の中でもない |
| U4 | 互いに相手を閉包に持たない 2 単位の `touches` が重なる。`touches` はタスクの `files` の和で、文字列の一致で比べる。ファイルから触る単位を引く索引を作り、各単位について、入力順で後ろにあり、同じファイルを触る単位だけを重複なく集める。こうすると 1 つの組を 1 回だけ調べ、ファイルを共有しない組は調べない。ただし、多くの単位が同じファイルを共有していると、索引では同じ組を何度もたどる。この切り替えの目安には単位の数の 2 乗を使う（実際に比べる組の数ではない）。ファイルごとの触る単位の数の 2 乗の和がこの値を超えたら、索引を使わず、入力順で後ろにあるすべての単位を候補にする。どちらの場合も、依存で前後の決まる相手は、候補を並べる前と重なるファイルを求める前に外す。1 つの組の違反は、重なるファイルをまとめて 1 件にする |
| U5 | `Edge.reason` が前後の空白を除くと空 |
| U6・W1 | `lines`（タスクの `lines` の和）が `max` を超える（U6）。`target` を超え `max` 以下（W1）。`lines` が無い単位（`whole` が真）にはどちらも当てない |

`split` の手順（決定 8）:

1. 単位のタスクを、タスクの依存の順（同じ順はタスクの番号の小さい順）に並べる
2. 1 つで `max` を超えるタスクがあれば、`units` を空、`resplit` をそのタスクにして返す
3. 前から足して `target` を超える手前で区切る。1 つで `target` を超えるタスクは、それだけで 1 区切りにする
4. 区切りが 1 つなら `units` を空にする（分けようがない）
5. 名前は `<元の名前> <番号>`、後の区切りは直前の区切りを先に要る単位にする（理由「分け方の案: 依存の順」）
6. 元の `after` は最初の区切りが引き継ぎ、元の単位を指していた依存は最後の区切りへ付け替える。付け替えた依存は `repoint` に入れる
7. 当てた後のグラフを同じ規則で確かめ（案は作らない）、残る違反を `remaining` に入れる。`judge` は受け取った `criteria` を `split` に渡し、受け入れ条件の参照の違反も残す

### 異常系の処理

例外は投げない。判定できないことは `Finding` で返す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge` | 依存の深さの実行順、別のファイルの依存、`whole` の単位 | 400・401・1,000・1,001 行、#3698 と #3247 の大きさの 16,757 行と 24,008 行（受け入れ条件 7） | U1〜U6 の各形、別のファイルの同じ `T1` は通る、同じ単位の中のタスクの依存は通る |
| `split` | #3672 の fixture（`tests/core/plan/fixtures/units/issue-3672.units.json`。Task 3 で新設する単位のファイルで、`issues/issue-3672-design-detail.md` の「ファイル配置」の表から、設計書の「テスト設計」の表の下の作り方で全タスクを 1 単位にまとめる）が上限に収まる複数の単位になり、依存の向きの順になる（受け入れ条件 8） | 区切りが 1 つ | 1 タスクで上限超え、当てた後に残る U4 |

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
| `differs` | 同上 | 新設 | `(a: Mapping, b: Mapping) -> bool` | 検査を通した JSON を、`units[].actual_lines` だけを除いて、並びと中身で比べる。型にしないので、`issue` の明示と省略は違いになる。JSON は `documents(tree, paths, listing)` がまとめて読み、パスの一致は呼び手が確かめる |
| `design_handlings` | `inputs_decide.py` | 新設 | `(text: str) -> dict[int, tuple[str, Optional[int]]]` | 「設計の結果」の表の（扱い, 統合先） |

### 内部のデータと定数

| 名前 | 定義するファイル | 中身 |
| --- | --- | --- |
| `Tree` | `units_files.py` | 読み元。作業ツリー（`Path`）かコミット（`git ls-tree`・`git show`） |
| `Collected` | 同上 | `files`・`omitted`・`excluded`（扱いが「マージする」「閉じる」）・`missing` |
| `Report` | 同上 | `judgement`・`missing`・`task_gaps`・`stale_tables` |
| `review-units.target-lines.json`・`max-lines.json` | `contracts/limits/` | `{"summary", "count": 400 / 1000, "read_by": ["ndf.core.plan.units_files"]}` |

`review-units.schema.json` は設計書の「データ構造」の例の形で、`additionalProperties: false`、`version` は定数 1 にする。`tasks[].files` の各値は、先頭と末尾の `/`、`//`、`.` と `..` の区切り、`*?[]` を断る `pattern` を持つ。`title` と `lines`（0 以上の整数）は必須である。`tasks`・`units`・各単位の `tasks` は 1 つ以上（`minItems: 1`）にする。各単位の `tasks` は `uniqueItems: true` にする。同じタスクを 2 回書いたファイルは、`load` が `invalid` で止め、`judge` まで進めない。`parse` はスキーマを検証しない。そのため `judge` は、受け取った `UnitFile` がスキーマを通ったかにかかわらず、単位の中の重複を U1 として返す。スキーマを通さずに辞書を `parse` して渡す呼び出し（テストや別の読み込み口）でも、重複を見落とさないためである。計画を省く課題は `single` で課題全体を 1 単位にするので、空の計画はファイルで表さない。行数の無い通常のタスクは `load` が `invalid` で止める。宣言 `workflow.review_units` は `{"target_lines", "max_lines"}`（1 以上の整数。どちらも任意）で、`project-workflow.schema.json` と `ndf-settings/project.schema.json` の両方に足す。

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
| `render` | `--issue N [--issue N]... [--omitted N]...` | 表を書き直してから、同じプロセスで `check --docs` を返す。置き換える直前に実装計画を読み直し、読んだ後に変わっていればどれも書き換えずに `stopped`（`precondition`）にする |

環境変数 `NDF_DESIGN_RECORD_DIR` があるとき（工程「ドキュメントレビュー」の `units-check`）は、次のように動く。

- 読み元は作業ツリーではなく、`NDF_DESIGN_RECORD_DIR` が指す保存先（下の「承認の記録」で説明する工程「ドキュメントレビュー」の保存先）にある `reviewed-head` が示すコミットにする。作業場所の HEAD がそのコミットと違えば `stopped`（`approval_stale`）
- `--docs` を指定しなくても、実装計画と突き合わせる。タスクか表が食い違えば `stopped`（`invalid`）にし、`checked.json` を書かない。突き合わせていない内容を承認へ進めないためである
- 確かめを通ったら、保存先の `checked.json` を書く。書くキーは次のとおりである

  | キー | 値 |
  | --- | --- |
  | `ndf_record` | NDF の記録の種別（`design-units-check`） |
  | `head` | 確かめで読んだコミット（`reviewed-head` の値）の ID。文字列 |
  | `issues` | 確かめた課題の番号の配列。扱いが「マージする」「閉じる」の課題も含む |
  | `handling` | 課題の扱い。`issues` の全課題について、課題の番号の文字列をキーにし、値を `{"handling": "<扱い>", "into": <番号か null>}` にするオブジェクト。たとえば `{"7": {"handling": "実装する", "into": null}}` |
  | `handling` の値の `handling` | 設計書の「設計の結果」の表の「扱い」の文字列。表に行の無い課題は「実装する」 |
  | `handling` の値の `into` | 扱いが「マージする」で、表の「統合先」のセルが `#<番号>` だけなら、その番号を整数で入れる。説明が付いたセルを含め、ほかの場合は `null` |
  | `omitted` | 実装計画を省き、課題全体を 1 単位にした課題の番号の配列。扱いが「マージする」「閉じる」の課題は含まない |
  | `limits` | 単位の見込みの行数の基準。`{"target": <目安>, "max": <上限>}`。この 2 つの値を超えたときの扱いは、設計書の「決定 5」の表の W1 と U6 に従う |
  | `branch` | 入力の `branch`（スプリントブランチ）。無ければ `stopped`（`precondition`） |
  | `criteria` | 課題ごとの受け入れ条件の数 |
  | `units` | 結果の `items` のうち `kind: unit` の項目 |

- 前の `checked.json` は、記録の種別が `design-units-check` のものだけを始めに消す。ほかのファイルは変えずに `stopped`（`refused`）にする
- 書くときは、一意の名前の一時ファイルに書き終えてから、`checked.json` の名前が空いているときだけ公開する。埋まっていれば `stopped`（`refused`）にする
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
| `mismatches` | 同上 | 新設 | `(record, tree, context) -> list[str]` | 設計書の決定 11 の表の 4 つの比較（単位のファイル・スプリントの課題・課題ごとの扱いと統合先・実装計画を省いた課題）。スプリントの課題は全体を比べ、ほかの比較は記録の `targets.issues` に絞る |
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
| `_reusable` | 同上 | 変更 | 変えない | 起動の引数によらず、前のラウンドに記録した比較元のブランチ（`mvv.mvv_branch`）が今回と違えば偽 |
| `cmd_run` | `domains/crossreview/cli.py` | 変更 | 変えない | `--reviewed-base-out` を受け、始めに片付け、`--reviewed-out` を書くときに最後のラウンドの比較元のブランチを書く。`--reviewed-out`・`--reviewed-pr-out` と同じ保存先か、入力の `--findings` と同じファイルなら、片付ける前に `invalid` で止まる。起動時に、3 つの出力先と隣の記録を照合する。NDF が書いたと確かめられた出力だけを空に戻し、記録のない中身入りの出力は残して `precondition` を返す（下の「出力先の既存のファイル」）。同じ出力先を使う起動の重なりは CLI では防がないので、呼び出し側が起動ごとに別の出力先を渡す。`--repo` の形はこれらを片付ける前に確かめる |
| `_merge_pull` | `ci_cli.py` | 変更 | 変えない | `--verify-merged` があり、マージ済みの PR の head か base が承認の値と違えば `refused` |
| `_watch` | 同上 | 変更 | 変えない | `--head <SHA>` があれば、待ちの開始時と必須チェックが通った時点に PR の HEAD を読み、指定した SHA と違えば `approval_stale` |
| `pulls` | `adapters/github.py` | 変更 | `(numbers, spare=(), *, rest_fallback: bool = True)` | 偽なら REST へ読み替えない |

- `RunConfig`（`settings.py`）に `follow_base: bool = False` を足し、`--reviewed-base-out` を渡した起動だけ真にする
- 出力先の既存のファイルは、NDF が書いたと確かめられたものだけを空に戻し、それ以外は変えずに止まる（`outputs.check`・`outputs.clear_previous`・`outputs.write_recorded`）。利用者のデータを消さずに、前の起動の値を今回の結果と取り違えないためである
  - この節は、PR 4638（ https://github.com/devbasex/ai-plugins/pull/4638 ）で利用者が選んだ最小の方式を書く。レビューの指摘に応えるうちに、出力 1 行を書く処理が約 480 行と新しい引数にまで膨らんだ。そこで利用者は、ロック・追記式の記録・前のバージョンの出力の退避を外し、同時の起動・偽装・前のバージョンの出力への指摘は見送ると決めた。扱わない場合と、そのときの再開の手順は、この節の最後の表にまとめる
  - 出力を書くとき（`write_recorded`）は、出力が空か、記録と出力全体が一致していれば、同じ引数で打ち直して再開できる順に書く。出力か記録の一部だけが残った場合は、変えずに `precondition` で止まる
    - まず出力を開く。無ければ作り、あれば同じ inode のまま中身を空にする
    - 次に、書く中身と出力のデバイス番号・inode を、記録（`.<名前>.ndf-output.json`。権限 0600）へ 1 回の書き込みで書く
    - 最後に、出力へ中身を 1 回の書き込みで書く。中身は 1 行（数十バイト）、記録は数百バイトである
    - 記録を出力より先に書くのは、出力を先に書くと、記録を書く前に終了したときに記録の無い出力が残り、同じコマンドを打ち直しても止まり続けるためである
    - 一時のファイルから置き換える書き方は採らない。一時のファイルの名前は記録の名前より長く、長い出力の名前では名前の上限を超えて、レビューの後に書けなくなるためである。強制終了すると一時のファイルが残り、次の起動も回収できないためでもある
  - 中断した状態ごとの扱いは、次の表のとおりである

    | 中断した状態 | 次の起動の扱い |
    | --- | --- |
    | 出力を空にした後、記録を作る前に終了した | 空の出力として、そのまま使う |
    | 記録を作った直後、中身を書く前に終了した（空の記録が残る） | 空の記録は失う中身が無いので、記録が無いのと同じに扱い、片付けで消す |
    | 記録を書いた後、出力の中身を書く前に終了した | 空の出力として使い、記録を消す |
    | 出力の中身を書き終えた後に終了した | 記録と出力全体が一致するので、空に戻して続ける |
    | 書き込みの失敗か、書き切れなかったことに起動自身が気づいた（ディスクの容量の不足など） | その起動が出力を空に戻し記録を消してから `precondition` を返す。原因を取り除けば、同じ引数で打ち直して続く |
    | 1 回の書き込みの途中で電源断などにより止まり、出力か記録の一部だけが残った | 利用者が編集したファイルと区別できないので、変えずに `precondition` で止まる（下の「扱わない場合」の表） |
  - 記録には、NDF の記録であることを示す種別（`ndf_record`）を書く。種別の無いファイルや JSON と読めないファイルは、利用者が同じ名前で作ったファイルかもしれないので、消さずに止まる
  - 記録は、umask によらず権限 0600 で作る。記録には出力と同じ値（レビューした SHA・PR・ブランチ名）が入る。umask 022 のまま作ると 0644 になり、呼び手が 0600 で用意した出力の値が、同じマシンの他の利用者に読めるようになるためである
  - 記録を状態の保存先でなく出力の隣に置くのは、通常のレビューと差分のレビューが、違う状態の保存先から同じ `reviewed-head` を書くためである
  - 起動の始めに、`cmd_run` は 3 つの出力先をすべて `check` で確かめてから、`clear_previous` で片付ける。1 つでも止まる出力先があれば、どの出力先も変えずに、相手役を起動する前に `precondition` で止まる。後の出力先の不備で止まったときに先の出力だけが空になっていると、呼び手は止まった起動の後に前の値を読めると考えて、空の値を読むためである
  - 出力先の状態ごとの扱いは、次の表のとおりである

    | 出力先の状態 | 扱い |
    | --- | --- |
    | ファイルが無い | 作れるかを、作って消して確かめる |
    | 空のファイル | そのまま使い、同じファイルへ書く。呼び手が先に作っておく使い方で止まらない |
    | 記録と同じファイル（デバイス番号と inode が同じ）で、中身のバイト列全体が記録の中身と一致する | 同じファイルのまま空に戻し、記録を消す |
    | 記録の無い中身入りのファイル | 変えずに `precondition` で止まる |
    | 記録と別のファイル（中身が同じでも）、記録と同じファイルでも中身が一部でも違う（短く編集した・途中まで書かれた） | 変えずに `precondition` で止まる |
    | シンボリックリンク、通常のファイルでないもの、隣に NDF の記録と読めないファイルがある | 変えずに `precondition` で止まる |

  - 中身が同じでも別のファイルは片付けない。利用者が同じパスへ同じ中身で作り直したファイルと、NDF が書いたファイルを区別するためである（MVV の Value 6: NDF が自動で片付けるのは NDF が作ったものだけ）
  - 中身は、全体が記録と一致するときだけ片付ける。先頭の部分の一致を書きかけとして片付けると、利用者が同じファイルを短く編集した場合と区別できないためである。中身は UTF-8 として読まずにバイト列で比べるので、文字の途中で切れた中身も、一致しない中身として止まる
  - 片付けでは、ファイルを消さずに空に戻す。消して作り直すと、新しいファイルの権限は umask で決まり（umask が 022 なら 0644）、0600 で用意した出力が他の利用者に読めるようになるためである
    - 出力を読む側は、空のファイルを無いファイルと同じに扱う。テンプレートの照合（`review-head`・`head-reviewed`）は空の値で `exit 2`、単位の回の `reviewed-head` を読む `units_invoke` は空の文字列を「レビューした HEAD が無い」と扱い、単位のレビューの起動スクリプト（`UNIT_REVIEW`）の `reviewed-pr` の照合は不一致になる。そのため、空に戻しても後の工程の判定は変わらない
  - 書けるかは、相手役を起動する前に `check` で確かめる。書けないと分かるのがレビューの後では、レビューの結果を受け取れないためである。次の場合に `precondition` で止まる
    - 出力先の親のディレクトリが無い
    - 既存の出力に書けない。無い出力を作れない
    - 記録を作れない。無い記録は作って消して確かめる。既存の記録は、消して作り直せるよう、親のディレクトリに書き込みと実行の権限があるかを確かめる。既存の空の出力にだけ書ける連携は、ここで止まる
  - 3 つの出力先が同じファイルか、ある出力先が別の出力の記録と同じパスかは、相手役を起動する前に `invalid` で止める。記録と重なると、後に書く方が先の出力か記録を上書きするためである。同じファイルかは、リンクと相対パスを解決したパスに加え、既にあるファイルならデバイス番号と inode でも比べる。同じ空のファイルへのハードリンクは、パスを解決しても別の名前のままなので、パスだけでは重なりを見落とす。見落とすと、後に書く比較元か PR が先に書いた HEAD を置き換え、成功の結果を返しても承認に要る HEAD を読めない。入力の `--findings` と出力先の重なりも同じ方法で比べる
  - これらの停止は、3 つの引数の説明（`--help`・スキーマ・`commands.md`）に書く
  - 実装のテンプレートの差分のレビュー（`review-delta`）は、通常のレビューが書いた `reviewed-head` を起点の控え（`delta-base`）へ写した後、レビューを起動する前に `reviewed-head` を同じファイルのまま空にする。消さずに空にするのは、呼び手が決めた権限（例えば 0600）と inode を保つためである。シンボリックリンクなら空にせず、起動した `run` の照合に任せる
    - `reviewed-head` はテンプレートが状態の保存先に置くファイルで、中身は `delta-base` に残る。空にするのは、前のバージョンが書いた記録の無い `reviewed-head` が残っていても、差分のレビューを止めずに続けるためである（MVV の Value 1・Value 3）。例えば、前のバージョンで通常のレビューを終えた実装の工程を、同じ状態の保存先のまま新しいバージョンで再開した場合に当たる。空にしないと、起動した `run` が記録の無い中身入りの出力として `precondition` で止まり、利用者に状態のファイルを調べて消す操作を求めることになる
  - 次の場合は扱わない。CLI は利用者のファイルを消さずに止まるか、呼び出し側が防ぐ

    | 場合 | 起きること | 防ぎ方・再開の手順 |
    | --- | --- | --- |
    | 同じ出力先を使う起動が同時に動く | CLI は起動を直列にしない。後の起動の片付けが、先の起動の書いた出力を空にすることがある | 呼び出し側が排他する。NDF のテンプレートは、単位の回ごとに別の状態の保存先（出力先）を渡す |
    | 記録を残さない前のバージョンが書いた出力を、CLI を直接やスクリプトから呼び出す連携が `--reviewed-out` などに渡した | 記録の無い中身入りの出力として、変えずに `precondition` で止まる。実装のテンプレートの工程は、起動の前に `reviewed-head` を空にするので止まらない（上の `review-delta` の項） | 出力先の指定を誤っていないかを確かめ、別のパスを渡すか、前のバージョンの NDF が書いた出力だと確かめてから消して打ち直す |
    | 出力か記録を書く 1 回の書き込みの途中で電源断などにより止まり、一部だけが残った。数百バイトまでを 1 回で書くので、まず起きない | 記録と一致しない出力か、NDF の記録と読めない記録として、変えずに `precondition` で止まる | 片付けてよいかは、記録を読めるかで分かれる。記録（`.<名前>.ndf-output.json`）を読める場合は、出力の中身が記録の `written` の先頭の部分と一致することを確かめてから、出力を空にし記録を消して打ち直す。記録が途中で切れて `written` を読めない場合と、一致しない場合は、出力と記録の両方を残し、別の出力先を指定して打ち直す |
    | 利用者が記録を書き換えた | NDF の記録と読めなければ、変えずに `precondition` で止まる | 記録と出力を確かめてから消し、打ち直す |
- `--reviewed-base-out` を渡した起動も、`--reviewed-pr-out` と同じく読んだ PR を確かめる（`RunConfig.check_pr` を真にする）。比較元は、レビューした HEAD がその PR の HEAD と確かめられたときだけ書く。確かめずに書くと、別の PR のマージ先を、レビューした HEAD と組にして承認へ渡してしまう
  - この確かめで、git の起動が 1 回増える場合がある。`--reviewed-pr-out` を併用せず、同じ起動で push した修正のコミットを最後にレビューした場合である。push 先が PR の head のリポジトリかを確かめるため、`git remote get-url --push --all` を 1 回打つ。`--reviewed-pr-out` を渡した起動と同じ回数である
  - push の時の結果は使えない。push は送り先のリポジトリを確かめずに送るので、使い回せる確かめの結果が無いためである
  - 代わりに、`check_base_source` が取得元を読む起動（`git remote get-url`）で push 先もまとめて読む案がある。採らなかった。増えるのは上の経路だけで 1 回の起動に 1 回であり、取得元と push 先を読む関数を 1 つにまとめる変更は、この単位の目的（比較元の出力）の外になるためである
- `--reviewed-base-out` を渡した起動は、PR のマージ先を取得するリモートの取得元が、PR のベースリポジトリと同じかを確かめる（`check_base_source`）。PR のマージ先の名前は GitHub から読むが、ブランチは作業場所のリモートから取得するので、`--repo` やフォークで両者が別のリポジトリだと、同じ名前の別のブランチとの差分をレビューし、その比較元を承認へ渡してしまうためである。この照合は省かない
  - 対象のリポジトリを作業場所の同じリモートの URL から決めた起動は、そのとき読んだ取得元（`Reader.fetched`）を使い回し、git を起動しない
  - `--repo` か保存済みの状態から対象のリポジトリを決めた起動は、取得元をまだ読んでいないので、`git remote get-url` を 1 回打つ。修正を push しない起動でも、1 回の起動につき 1 回増える。push 先の確かめ（上）とは別の回数で、両方に当たる起動は 2 回増える
  - 代わりに、明示されたリポジトリを取得元とみなして照合を省く案がある。採らなかった。照合を省くと、取得元が違うリモートで取得したブランチとの差分を、明示したリポジトリの比較元として承認へ渡すためである。増えるのは手元の git の起動 1 回で、ネットワークも相手役の起動も使わない
- ラウンドの比較元のブランチは、ラウンドの記録の `mvv.mvv_branch` で照らす。別のキーは足さない。`_sync` は、MVV を読むブランチを決めた時点でこの値を書く
  - 比較元がブランチなら、その名前を `mvv.mvv_branch` に記録する。`follow_base` の起動では、PR の今のマージ先がこのブランチになる
  - `follow_base` が偽で `--base` がコミットなら、比較元のブランチは無い。この場合は、MVV を読んだ PR のマージ先のブランチを、ラウンドの `mvv.mvv_branch` と、詳細レビューを通ったセクションの `reviewed_base_branch` の両方に記録する。空にもコミットの SHA にもしない
  - このときも、差分の起点は PR のマージ先へ置き換えない。指定したコミットと HEAD の merge-base を求め、そこから HEAD までを全差分とする。指定したコミットのツリーと HEAD を直接は比べない。直接比べると、指定したコミットが HEAD の祖先でない場合に、PR に取り込まれていない比較元の側の変更までレビューの対象に入る
  - ラウンドの `base` には、その merge-base の SHA を記録する。前のラウンドと差分の起点が同じかは、この値で比べる。違うコミットを指定しても merge-base が同じなら、差分は同じなので同じ起点とみなす
  - キーを分けると、このキーの無い前のバージョンのラウンドをすべて不一致とみなし、バージョンを上げた後に収束済みの PR を同じ条件で打ち直しても、全差分を読み直す
- 比較元のブランチが前のラウンドと違う起動は、前の head からの差分も前回の収束結果も使わず、新しい比較元との全差分を渡す。作り方は `--base` からの全差分と同じである
  - 比較元のブランチが変わるのは、`follow_base` の起動で PR のマージ先が変わったときと、`--base` を別のブランチへ替えたときである
  - merge-base が同じでも比べる。`main` から 1 コミット進めた `sprint/y` のように、merge-base が同じまま比較元だけが変わることがある。HEAD・merge-base・MVV だけで比べると、新しい比較元で詳細レビューをせずに、前回の収束結果を返してしまう
- 詳細レビューを通ったセクションは、比較元のブランチが今と同じ記録だけを引き継ぐ。MVV が同じでも、差分の相手が変われば、前の詳細レビューは新しい差分を確かめていないためである
  - 記録には、そのときの比較元のブランチ（`reviewed_base_branch`）を残す。値はラウンドの `mvv.mvv_branch` と同じで、`--base` がコミットなら PR のマージ先のブランチである
  - 比較元のブランチだけでは、差分の起点が変わったことが分からない。`--base` がコミットなら、別のコミットを渡しても、記録するブランチは同じ PR のマージ先になるためである。そこで、記録には差分の起点（`--base` と HEAD の merge-base の SHA。`reviewed_base_commit`）も残す。`--base` がブランチでもコミットでも、merge-base の SHA を記録する
  - 範囲を決める時点で、記録の起点と今の起点を比べ、今の差分で中身が変わらないと確かめられたセクションだけ記録を引き継ぐ。`--base` がブランチかコミットかは問わない
    - 起点が同じなら、同じ HEAD に対する差分は前と同じなので、すべてのセクションの記録を引き継ぐ。`--base main` から、同じ merge-base になる SHA へ替えた起動が当たる
    - 起点が違えば、2 つの起点でセクションの本文（見出しの行から次の見出しの前まで）と親の見出しの並びを比べ、両方が同じセクションの記録を引き継ぐ。起点の本文が同じなら、同じ HEAD に対するそのセクションの差分も同じだからである。本文か親の見出しが違うセクションと、片方の起点にしか無いセクションの記録は外す。両方の起点に無いセクション（PR で足したセクション）の記録は引き継ぐ。2 つの起点に違いが無いので、同じ HEAD に対するそのセクションの差分も変わらないためである
    - 親の見出しも比べるのは、本文が同じでも、セクションを別の親の見出しの下へ移すと適用先が変わるためである。例えば、「本番」の下にあった「実行手順」を「検証」の下へ移すと、手順を実行する環境が変わる。本文だけを比べると、この移動を読まないまま、セクションが詳細レビューを通ったと記録される
    - セクションの分け方（見出しの判定とコードブロックの扱い）は、詳細レビューの対象のセクションを決めるとき（`sections_of`）と同じ関数で行う。片方だけを直して、詳細レビューの対象と記録を引き継ぐ対象がずれないためである
    - 例えば、設計 PR に `main` の更新をマージして merge-base が進んでも、その更新が PR で変えた文書に触れていなければ、すべての記録を引き継ぐ。更新が文書の 1 つのセクションに触れていれば、そのセクションだけを詳細レビューに戻す
    - 記録の起点が今の起点の祖先かでは決めない。コミットの祖先関係が分かっても、差分の中身が前の差分に含まれるとは限らないためである。例えば、あるセクションの値が起点 A で 0、途中のコミット B で 1、HEAD で 0 だとする。A を起点にした差分にこの値の変更は現れないが、B を起点にした差分には 1 から 0 への変更が現れる。A と B でこのセクションの本文が違うので、`--base B` に替えると、このセクションの記録は外れる
    - 起点が前へ戻った場合（PR の中のコミット B を `--base` に渡してレビューした後に `--base main` へ戻した場合）も、同じ規則で比べる。A から B までの変更に触れたセクションの記録が外れる
    - 本文は手元のオブジェクトだけから読み、部分クローンで欠けた本文をリモートから取得しない（`Git.offline`。`review-scope` と同じ）。変わり方（追加・削除・変更）からあるはずの本文が手元に無ければ、本文を読めない場合として扱う
    - 起点の本文を読めない場合（記録の起点のコミットが手元に無いなど）と、起点を記録しない前のバージョンの記録を読む場合は、すべての記録を外す
  - 本文を比べるために git を起動するのは、詳細レビューを求める起動（設計 PR。`every_section`）で、記録の起点が今の起点と違い、引き継ぐ候補の記録があるときだけである。ほかの PR も全差分のラウンドの後にセクションの記録を残すが、範囲の判定に使わないので、起点が違えば本文を比べずに記録を外す。起動は 1 回の判定につき次の 2 回までである。`run` では、全差分のラウンドを終えて記録の起点を今の起点に書き直すまでの判定（ふつうは最初のラウンド）だけで起きる。`review-scope` では、条件に当たる呼び出しのたびに起きる
    1. 2 つの起点の間で変わったファイルの名前と変わり方を読む（`git diff --name-status`。1 回）
    2. そのうち、記録のあるセクションを含む文書があれば、両方の起点の本文をまとめて読む（`git cat-file --batch`。1 回）。該当する文書が無ければ起動しない
  - 起点が違うときの扱いは、次の案から選んだ。比べたのは、比較元の更新を取り込んだ後に増える処理の回数と、詳細レビューに戻るセクションの数である。所要時間は測っていない（未計測）

    | 案 | 起点が変わったときの追加の処理 | 採否と理由 |
    | --- | --- | --- |
    | 記録をすべて外す | 無し | 採らない。PR で変えた文書に触れない更新でも、すべてのセクションを相手役ごとに詳細レビューし直す。相手役が読み直す分のトークンと、相手役のレビューを待つ時間がかかる |
    | 記録の起点が今の起点の祖先なら引き継ぐ | git の起動 1 回 | 採らない。上の A・B の例のように、差分に現れた変更を読まないまま、詳細レビューを通ったと記録する |
    | 2 つの起点でセクションの本文を比べる | git の起動 2 回まで（ネットワークを使わず、手元のオブジェクトを読むだけ） | 採る。詳細レビューに戻るのは、差分の中身か親の見出しが変わったセクションだけである。増える処理は git の起動 2 回までで、相手役の起動は増えない |

  - 前のラウンドの head からの差分を渡すかは、詳細レビューの記録とは別に決める。前のラウンドの記録が今回のレビューの条件に合い、その head が今の HEAD の祖先なら、前の head からの差分を渡す。条件に合う前の head が無ければ、比較元との全差分を渡す。PR のコミットを積み直すと、merge-base が同じでも前の head が今の HEAD の祖先でなくなるので、全差分を渡す
  - 詳細レビュー済みセクションの照合規則は共通である。`run` と `review-scope` は、記録を引き継げるかを同じ関数（`round_scope.reviewed`。判定の中身は `scope_decide.reviewed_for`）で判定する。`--reviewed-base-out` を指定した `run` は、差分の起点と収束結果の再利用に対象 PR の一致も求める。`review-scope` は対象 PR を照合しないため、事前に示した差分の範囲と `run` が渡す範囲は異なることがある。例えば、PR を読んだ記録の無い前のラウンドしか無い場合、`review-scope` は前の head からの差分を示すが、`run` は全差分を渡す
  - 前回の収束を再利用するか（`_reusable`）は、この判定とは別に、最後のラウンドの記録を照らして決める。照らすのは HEAD・相手役・重点・MVV・比較元のブランチ・merge-base などで、記録の起点と今の起点が違えば一致しない。そのため、上の規則で起点が違うために記録を外す起動では、`run` も前回の収束を返さない
  - ただし、`review-scope` が全差分を返しても、`run` が前回の収束を返すことがある。次の 2 つの場合で、`review-scope` の範囲は `run` がレビューを回すことの保証にならない
    - 前回のラウンドで、欠けた相手役の代わりに別の相手役が席を埋めていた場合。`run` は席を埋めてよい起動なら、埋めた席を欠けた相手役の代わりにも数えて収束を再利用する（`stand_in`）。`review-scope` は埋めた席を数えないので、相手役が全員そのラウンドを読んだとみなさず、全差分を返す
    - 前のバージョンが書いた記録に、差分の起点（`reviewed_base_commit`）が無い場合。記録を外した結果、詳細レビューが必要なセクションが残るなら、`review-scope` は全差分を返す。一方 `run` は、HEAD や相手役など、前回の収束を再利用する条件をすべて満たせば、前回の結果を返す
  - 比べるのは、ラウンドの範囲を決める時点である。`follow_base` の起動か、`--base` を変えた起動かによらない。比較元が違う記録は引き継がない。設計 PR の場合は、新しい全差分に含まれる文書の全セクションを詳細レビューの対象にする。詳細レビューをセクションごとに求めるのは設計 PR だけで（`review_scope` の `every_section`）、ほかの PR は Markdown を変えても詳細レビューの指示を出さない
  - 範囲を決める時点で比べないと、前の比較元で通った同じ名前のセクションが詳細レビューの指示から外れる。さらにラウンドの後、そのセクションが今の比較元で通ったと記録される
  - `review-scope` は、指定された `--base` を比較元とする。`review-scope` はネットワークから取得せず、手元のリモート追跡参照を読む。PR のマージ先に合わせる `run` の範囲を事前に確認する場合は、次の順に進める
    1. PR の現在のマージ先を調べる
    2. 対象のリモートからそのブランチを取得し、手元のリモート追跡参照を更新する
    3. そのブランチ名を `review-scope --base` に渡す
  - 名前だけを調べて取得しないと、追跡参照が古い作業場所では前の比較元で範囲を決めてしまい、実際の `run` と違う範囲を返す
- `--verify-merged` は `--pr`・`--approved-head`・`--approved-base` と組でだけ受ける（どれかが無ければ `invalid`）。番号を渡さない経路は開いている PR しか探さず、マージ済みの PR を承認の値と照らせないためである

`ndf ci watch --head <SHA>` が返すチェック結果は、指定した SHA のものである。待ちの開始時と、必須チェックが通った時点に PR の HEAD を読み、指定した SHA と違えば `approval_stale` を返す。取得したチェック結果がどの SHA のものかを確かめるだけでは足りない。結果は指定した SHA に固定して読んでいるので、PR の HEAD が動いても一致し、成功を返してしまうためである。

必須チェックが通った時点で、PR を 1 回読み直す（`GitHub.pull()` 1 回）。読み直しで HEAD の変更が分かり、`approval_stale` を返す場合も、この取得は発生する。REST の一次の上限に当たって GraphQL で読み替える場合は、API の要求がさらに 1 回増える。必須チェックが失敗した起動と、待ちの予算を使い切った起動は読み直さない。読み直しの回数はコマンドの契約（`--head` の説明）にも書く。

| 案 | 追加の PR の取得 | 採否と理由 |
| --- | --- | --- |
| 待ちの開始時だけ HEAD を確かめる | 無し | 採らない。チェックの取得先は承認した SHA のままなので、PR の今の HEAD が未承認の SHA に変わっても、古い SHA のチェック結果で成功を返す。取得先を固定するだけでは、待つ間の HEAD の変更を防げない |
| 必須チェックが通った時点にだけ読み直す | 必須チェックが通った起動 1 回につき 1 回 | 採る。待つ間の変更を検出でき、増えるのは必須チェックが通った経路の 1 回だけである |
| 待つ間にチェックを読むたびに PR も読む | チェックを読むたびに 1 回 | 採らない。変更は早く分かるが、回数が待つ時間に比例して増える。成功を返さない限り、遅く分かっても誤りにならない |

| 失敗 | 扱い |
| --- | --- |
| PR のマージ先を読めない | 今の `_merge_target` と同じく `precondition` |
| `--reviewed-base-out` を書けない | `precondition`。出力先に書けるかは相手役を起動する前に確かめ、書けなければレビューを始めずに止まる |

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
| `advance` | `units_invoke.py` | 新設 | `(ctx, merge_shas: Sequence[str]) -> frozenset[str]` | スプリントブランチを 1 回取得して作業場所を ff で進め、渡したマージのコミットのうち、進めた後の HEAD が含むものを返す。取得か ff に失敗したら例外を出す |

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

1. 取り込み済みの単位を除いて、`graphlib.TopologicalSorter` のグラフを組む。取り込み済みの単位とは、開始時の一覧で PR がマージ済みで、かつ `start_head` がそのマージのコミットを含む単位である。PR がマージ済みかだけで除くと、取得先でマージのコミットがまだ見えない間に、後続の単位を古い HEAD から始めてしまう
   - 取り込み済みの単位を指す依存も除く。`done` は、`get_ready` が返す前の単位には呼べないためである
   - PR はマージ済みだが `start_head` がマージのコミットを含まない単位は、グラフに残したまま起動せず、手順 5 の取り込み待ちに入れる。期限は `units/ingest.json` の最初に分かった時刻から数える（記録が無ければ今の時刻）
2. `get_ready()` が返した単位を、すべて起動待ちのキューに入れる（`get_ready` は同じ単位を二度返さない）。空いた本数だけ、キューから `merge` の順に取り出して起動する。本数の上限は `named_cap.read(os.environ, <状態の保存先>).value`
3. `concurrent.futures.wait(FIRST_COMPLETED)` で待つ。待ちの PR があるときは、次に問い合わせる時刻を持ち、その時刻までを上限にして待つ。`units_queue.read` を打つのは、その時刻に達したときだけで、待ちの PR をまとめて読む。プランの完了で先に戻った場合は読まず、時刻も変えない。間隔は `test-triage.check-wait` である
   - 次の問い合わせの時刻を決めるたびに、PR ごとに、`queue.json` の `queued_at` からの経過を `ci_budget` と比べる。ほかの単位が動いている間も、打ち直した後も、同じ時刻から数える
   - 超えた PR は、読むのをやめて `ci_pending` の失敗にする。GitHub の利用枠が戻る時刻がこの上限より後なら `infra_wait` の失敗にする。PR は GitHub のキューに残っているとみなし、「キューに残る PR」として覚える。補った単位を起動するかの判定に使う
4. 終わったプランが同時に複数あれば、`merge` の順に `merge_unit` を打つ。`ci_pending` で PR がキューにあれば、待ちの一覧へ入れて次へ進む
5. マージ済みの単位は、スプリントの作業場所がそのマージのコミットを含むまで進めてから `done` にする。対象は、`merge_unit` が `ok` を返した単位（直接のマージ）と、`units_queue.read` が `merged` を返した単位（キューでのマージ）である
   - 同じ手順 4 の回か、同じ `units_queue.read` の応答で分かった単位は、まとめて `advance` に渡し、1 回だけ打つ。単位ごとに打つと、N 件のマージで Git の起動とリモートへの問い合わせが N−1 回ずつ増えるためである
   - `advance` が返したコミットの単位だけを `done` にして、2 へ戻る
   - 返らなかった単位は、マージのコミットの SHA とともに「取り込み待ち」の一覧に入れる。直接のマージの単位も、キューの待ちの一覧ではなくこの一覧に入る。一覧は、作業の保存先の `units/ingest.json`（単位の名前ごとに `{"sha", "merged_at"}`。`merged_at` はマージが最初に分かった時刻）にも書き、`queue.json` と同じく打ち直しをまたいで持つ。すでに記録のある単位は `merged_at` を書き換えない。取り込めた単位は記録から消す。取り込み待ちがある間は、手順 3 の次の問い合わせの時刻を持つ。その時刻になったら、取り込み待ちの SHA をすべて `advance` に 1 回で渡し直す。GitHub への問い合わせは要らない
   - 取り込み待ちの単位は、キューの待ちと同じく、`queued_at` の代わりに `units/ingest.json` の `merged_at` から `ci_budget` を数える。打ち直した後も同じ時刻から数える。超えたら `ci_pending` の失敗にし、「取り込めていないマージ」として覚える。補った単位を起動するかの判定に使う
   - `advance` の例外は止める（`precondition`）
6. 失敗した単位は `done` にしない（後に続く単位は `get_ready` に出ない）。`cause.kind` が `usage_limit` なら、以後は起動せず、実行中を待って結果を保存し、成功した単位はマージする
7. 起動できる単位も実行中も無くなったら、キューの待ちか取り込み待ちがある間は手順 3 の時刻と上限で読み続ける。このとき `concurrent.futures.wait` は使わない。待つ Future が空だと、指定した時間を待たずにすぐ返り、空の待機を繰り返して CPU を使い続けるためである。代わりに、テストが差し替えられる `sleep`（`progress/run.py` の `Env.sleep` と同じく既定は `time.sleep`）で、次の問い合わせの時刻まで眠る
8. 全単位が取り込み済み（PR がマージ済みで、作業場所の HEAD がマージのコミットを含む）なら #4369 の `sprint_pr`。残りがあれば `stopped` で、残りの単位と理由を `cause.detail` に書く。残りの原因が待ちの上限だけなら、種類は `ci_pending` か `infra_wait` にする

打ち直しでは、#4369 の `resume_of` が `merge` を返す単位でも、先に `units_queue.read` を打つ。キューにあれば待ちの一覧へ入れ、`ndf ci merge` を打たない。`queued_at` から `ci_budget` を超えていれば、この 1 回の読み取りの後は問い合わせを続けず、上の上限の扱いにする。

単位の欄が無い課題と実装計画を省いた課題は、課題全体を 1 つの単位にして補う。この単位は `touches` が分からない。そのため、次のどちらもそろった後に、課題の番号の順に 1 つずつ起動する。

- 単位のファイルの単位に、起動待ち・実行中・キューの待ち・取り込み待ちが無く、キューに残る PR も取り込めていないマージも無い
- 単位のファイルの単位が、`done`・失敗・先に要る単位の失敗で始められない、のどれかになっている

単位のファイルの単位が失敗しても、この単位は始める。依存の指定が無いためである。ただしキューに残る PR がある間は始めない。その PR が後からマージされると、同じファイルを触っていた場合に競合するためである。この場合は手順 8 の `ci_pending` か `infra_wait` で終え、呼び手は別の作業へ進める。取り込み待ちが期限を超えた単位（取り込めていないマージ）がある間も、同じく始めない。作業場所がそのマージのコミットを含まないまま始めると、直前にマージされた変更を含まない作業場所で実装とテストが進むためである。取り込めていないマージは、打ち直しで作業場所がそのコミットを含んだときに解く。このように、キューに残る PR か取り込めていないマージがある間は補った単位の起動を止めておく（次の表の「保留」）。打ち直した実行では、`ndf ci merge` の再送まで終えた後の PR の状態で、保留を解くかを次の表で決める。読み取った状態がキュー外でも、同じ実行で再送したなら保留を続ける（表の最後の行）。

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
- 実行中の単位が無く待ちの PR だけが残るときは、差し替えた時計で次の問い合わせの時刻まで 1 回だけ眠り、その間に待機を繰り返さない
- 1 回のキューの読み取りで複数の単位のマージが分かったとき、スプリントブランチの取得は 1 回である
- 直接のマージの直後の取得でマージのコミットがまだ見えない単位は、次の問い合わせの時刻に取得し直し、見えた後に後続の単位を始める
- 取り込み待ちの途中で打ち直すと、`units/ingest.json` の `merged_at` から期限を数え続け、最初から数え直さない
- 打ち直しの開始時に PR はマージ済みでも `start_head` がマージのコミットを含まない単位は、グラフから除かず、後続の単位を始めない
- 全単位の PR がマージ済みでも、作業場所がマージのコミットを含まない単位があれば `sprint_pr` へ進まない
- 取り込み待ちが期限を超えた単位があると、補った単位を起動しない
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
| `compare` | 同上 | 新設 | `(old: Sequence[Unit], new: Sequence[View]) -> list[str]` | 旧い表と比べた違いを返す。単位の名前と含むタスクの違いと、根拠の無い待ちである。根拠の無い待ちは、新しい依存を間接も含めてたどれる（待つ側, 待たれる側）の組のうち、次の 2 種類の辺をつないだグラフでたどれない組である。1 つは旧い表の先に要る単位の辺。もう 1 つは、触るファイルが重なる 2 単位の間にある、新しい依存と同じ向きの辺 |

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

持っておいたコミットが無い打ち直し（反映済み）では、push するものが無いので、次の 1〜7 の順を通らず、次の表で進める。どの行でも、`unit_run` が担当の指示（`unit.md`）に足すタスクの本文は、`convert.json` の `commit` の実装計画から `git show` で読む。

| 打ち直しの時点の状態 | 扱い |
| --- | --- |
表は上から順に判定し、最初に当たった行で進める。

| 順 | 判定 | 扱い |
| --- | --- | --- |
| 1 | `convert.json` があり、その `commit` が `start_head` の祖先で、単位のファイルが `units[].actual_lines` を除いてそのコミットと同じ | 実装へ進む |
| 2 | 1 に当たらない（`convert.json` が無い場合を含む）。`design_record.reapproved_of` の記録があり、その `head` が `start_head` の祖先で、単位のファイル（`units[].actual_lines` を除く）と実装計画の本文が記録の `head` と同じで、`mismatches` が空 | `convert.json` を `{"commit": <記録の head>, "approval": {"kind": "reapproval", "head": <記録の head>}}` に書き直し、実装へ進む。同じ承認を打ち直しのたびに問い直さないためである |
| 3 | 1 にも 2 にも当たらない（再承認の記録が無いか、記録の `head` の後に単位のファイルか実装計画の本文が変わった） | `start_head` を対象にした `design_record.converted` の記録で、再承認の `gate` を返す |

1. `units_sync.push_url` で URL を照合し、その URL からスプリントブランチの先端を読む（「送るコミットの確かめ」と同じ読み方）。以後の手順は同じ URL を使い、読み直さない
2. 送るコミットを一時の作業場所で作る。持っておいたコミットの親がその先端なら、そのまま使う。違えば、先端へ cherry-pick する。競合すれば、持っておいたコミットを捨て、手順 1 の先端から一時の作業場所を作り直して変換し直す
3. 送るコミットを読み元にして、変換が変えた範囲を確かめる。違反があれば、捨てて変換し直す
   - 実装計画で変えてよいのは、「レビューの単位」と「並列と直列」の 2 つのセクションだけである。この 2 つを除いた本文が、親のコミットと同じでなければ違反である
   - `units_files.check`（`docs` は真）
   - 1 回の `convert` で担当を起動するのは 2 回まで（最初の変換と、やり直し 1 回）とする。手順 2 の競合によるやり直しも数える。2 回目でも違反か競合があれば、最後の候補のコミットを `refs/ndf/convert/<ステートマシンの ID>` に残し、違反の一覧を `<work_root>/convert-violations.json` に書いて `stopped`（`invalid`）にする。理由には違反の一覧を出す。同じ変換の誤りが続くとき、AI の起動とトークンの費用を際限なく払わないためである。打ち直しは利用者が打つときだけで、そのときは回数を 0 から数え直す
4. 同じコミットで、承認した内容を確かめる。次のどちらかに当たれば push せず、ref を送るコミットに置き換えて、再承認の `gate` を返す
   - 再承認の記録がある場合: 記録との `mismatches` に違いがあるか、実装計画の中身が記録の `head` と違う。`compare` の結果にはよらず確かめる。実装計画の全体を比べるのは、この push の前だけである
   - 再承認の記録が無く、旧い設計承認の head に単位の表がある場合: 旧い表との照合 `compare` が違うか、実装計画の本文が旧い設計承認の head と違う。本文は、変換が書き換える「レビューの単位」と「並列と直列」の 2 つのセクションを除いて比べる。手順 3 が比べる親のコミットは今の先端なので、設計承認の後から変換までに書き換わったタスクの本文は、手順 3 では見つからないためである
   - 設計承認の回答が無いか、その head に実装計画か単位の表が無い場合（実装計画を承認の後に書いた場合）: 人が承認した分け方も本文も無いので、`compare` も本文の比較もせず、再承認を求めない（設計書の決定 10 の末尾）
5. `units_sync.destination_errors` と `units_sync.outgoing_errors` を通す。`destination_errors` には、引数 `url` に手順 1 の URL を、引数 `record` に `design_record.converted` の値を渡す。`outgoing_errors` には、引数 `tip` に手順 1 で読んだ先端を渡して読み直さず、引数 `allowed`（変えてよいファイル）にスプリントの課題の単位のファイルと実装計画を渡す。違えば push せず `stopped`（`refused`）
6. `<work_root>/convert.json` に `{"commit": <送るコミットの SHA>, "approval": {"kind": "design" か "reapproval", "head": <照合に使った承認の head>}}` を書く。push の後に中断しても、再開で何を承認して送ったかを読むためである。続けて `git push --no-follow-tags --recurse-submodules=no -- <手順 1 の URL> <送るコミットの SHA>:refs/heads/<スプリントブランチ>` を打つ。強制はしない。送るコミットの親は手順 1 の先端なので、その後に送り先が進めば、git が拒否する
7. push が通ったら、スプリントの作業場所を `git merge --ff-only` でそのコミットへ進める

| 失敗 | 扱い |
| --- | --- |
| 担当の失敗・push の拒否 | `stopped`。打ち直しは「再開点」の表の続きから |
| 旧い承認の HEAD を読めない | 始めない（`precondition`） |
| 却下 | 始めない（`refused`） |

単体テストの観点は次のとおりである。

- 再開点の 4 行
- 触るファイルが重なる 2 つの単位の間に依存を足しただけなら、違いにしない
- 触るファイルが重ならない 2 つの単位の間に依存を足すと、違いにして再承認を求める
- 触るファイルが重なる単位の間の依存で、間接に生じる待ちが旧い順序と重なる辺でたどれるなら、違いにしない
- 触るファイルが重ならない辺を足したときは、その辺を通って間接に生じる待ちの組も違いとして返す。ただし返すのは、旧い表の先に要る単位の辺と触るファイルが重なる辺だけではたどれない組に限る。たとえば旧い表で C がすでに A を待つとき、根拠の無い A→B と、触るファイルが重なる B→C を足しても、A→C は返さず、A→B だけを返す
- 旧い表にあった単位の依存（先に要る単位）が変換で消える場合は、承認の回答があるまで実装を流さない
- 承認の後にファイルを直すか、実行対象が変わると再び問う
- 単位の分け方が同じで実装計画の本文だけが変わった再承認でも、`reapproval.md` に理由と差分が載る
- 設計承認の後に、スプリントブランチで実装計画のタスクの本文を書き換えると、単位の分け方が旧い表と一致していても push せずに再承認を求める
- 設計承認の回答が無いスプリントと、承認の head に実装計画が無いスプリント（承認の後に初めて実装計画を書いた）は、本文を比べず、止まらず、再承認も求めずに反映する
- 再承認の回答の前と却下の後は、手元とリモートのスプリントブランチが変わらない
- 承認を待つ間に先端で実装計画か扱いが変わると、単位の分け方が旧い表と一致していても、push せずに再び問う
- 実装計画の 2 つのセクションの外を変えた変換は push しない
- 2 回続けて違反した変換は、担当を 3 回目に起動せず、候補の ref と違反の一覧を残して `invalid` で止まる
- 手元より送り先が進んで cherry-pick が競合すると、送り先の先端から変換し直す
- 反映の 1 回の試行で、送り先の URL への `git fetch` は 1 回である
- push が拒否された後は、手元のスプリントブランチが変わらず、打ち直しは新しい先端へ載せ直して反映する
- push の URL が「送り先の確かめ」の拒否の例に当たれば、変換の担当の起動の前の取得も push も起きない
- 手元だけのコミットは送らない。送り先の違いと、対象外のファイルを含む変換のコミットは push しない
- ファイルを作った直後の中断（未コミット・未 push・未再承認）は、打ち直しで続きから進み、実装を始めない
- 反映の後にスプリントブランチの実装計画のタスクの本文を書き換えても、担当の指示は `convert.json` のコミットの本文で組まれる
- 反映の後に単位のファイルを書き換えた打ち直しは、実装を始めずに再承認を求める
- その再承認を受けた後の打ち直しは、`convert.json` を書き直して実装へ進み、同じ承認を問い直さない
- `convert.json` が無い状態で再承認を受けた後の打ち直しも、`convert.json` を書いて実装へ進み、同じ承認を問い直さない

## 承認した文書の取り込み（`progress/units_sync.py`）

満たす決定: 決定 1・11、I8。

### 関数の仕様

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `github_target` | `adapters/git.py` | 新設 | `(url: str) -> Optional[GitHubTarget]` | URL の全体が 3 つの形に合えば `repo`（`<所有者>/<リポジトリ>`）と `secret`（認証情報を含むか）。合わなければ None |
| `Git.push_urls` | 同上 | 新設 | `(remote: str) -> list[str]` | `git remote get-url --push --all` の出力を LF だけで区切った値（末尾の CR は URL に残す） |
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
2. `github_target` が None でない。次の 3 つの正規表現の全体一致（`fullmatch`）で、方式は小文字の `https`・`ssh` だけを受け、ホスト名だけ大文字小文字を区別しない（git は大文字の方式を別の remote helper として読み、失敗する）。所有者とリポジトリは `[A-Za-z0-9_.-]+`（`.`・`..` を除く）で、末尾の `.git` と `/` は 1 つずつ許す。クエリ・`#`・空白・`%`・余分なパスの要素があれば合わない
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
   - どちらも含まなければ（取り込みのコミットを手元で消した後など）、`design-sync.json` を使わずに手順 4 から進める。記録が今の手元を表さないためである。手順 6 と 8 の確かめは同じに通るので、手元だけの別のコミットは送らない
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
- `design-sync.json` の `commit` を送り先の先端も手元の HEAD も含まない打ち直しは、手順 4 から進め、取り込みのコミットを作り直す
- 取得先と push 先の URL が違うリモートでは、push 先に届けば `ok` にする
- `git fetch` と `git push` の引数は照合した URL で、リモート名でない

テストのリモートの URL は `git@github.com:o/r.git` にし、環境変数 `GIT_SSH_COMMAND` の偽の ssh が手元の bare リポジトリで受ける。`insteadOf` で手元へ向けると、上の拒否に当たるためである。この形で push と取得ができることは、git 2.53.0 で実行して確かめた。URL の 3 つの形と拒否の例は、`github_target` の単体テスト（`tests/adapters/test_git.py`）でも縛る

## 実装のプランの確かめ（`progress/units_guard.py`）

満たす決定: 決定 12、I5。

| 関数 | 定義するファイル | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_launch` | `units_guard.py` | 新設 | `(work_root, branch, base, record, issue, unit_file: Optional[Path]) -> None` | `units/guard/<ブランチの sha256 の先頭 16 字>.json` に `{"branch", "base", "record_head", "issue", "unit_sha256"}` を書く。`base` は単位の PR の宛先で、スプリントブランチである |
| `guard` | 同上 | 新設 | `(st, repo, issue, base, branch, unit_file) -> list[str]` | 設計書の決定 12 の表の判定。空なら通す |
| `read_machines` | `store.py` | 新設 | `(conn, repo) -> list[dict]` | そのリポジトリのスプリントの一覧 |

- 対象のスプリントは、終わっていないもののうち、文脈の `issues` に `--issue` があるか、文脈の `branch` が `--base` のものである
- 対象のスプリントを見つけた後の照合でも、`--base` を記録の `base` と比べる。課題・作業ブランチ・指示が起動の記録と同じでも、宛先が違うプランは通さない。承認した単位を、承認していない宛先へマージさせないためである
- 「宛先がスプリントブランチ」は、`--base` が `model.branch_prefix("sprint")`（`sprint/`）で始まることである
- 指示のファイル（`--unit-file` に渡すファイル。#4369 の `unit_file` で、`unit_run` が担当タスクの本文を足す `unit.md`。JSON の単位のファイルとは別のファイル）は、中身の sha256 を比べる。指示の無い単位は空文字である

| 失敗 | 扱い |
| --- | --- |
| 状態の保存先を読めない | 止める（`precondition`） |
| 指示のファイルを読めない | 止める（`precondition`） |
| 違い | 止める（`refused`）。`ndf progress units run <ID>` を打つと書く |

単体テストの観点: 起動した単位は通る。課題か指示を変えた手組みのプランは止まる。課題・作業ブランチ・指示が同じで宛先だけを変えたプランも止まる。単発の `feat/` 宛ては通る。登録の無い `sprint/` 宛ては止まる。

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
