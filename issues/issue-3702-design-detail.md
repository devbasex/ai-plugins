# #3702 の詳細設計: 単位のファイル・単位の判定・単位のプランの組み立てと、設計のフェーズの工程の並び

設計文書 [issue-3702-design.md](issue-3702-design.md) の構成要素と決定を、ファイルと関数の粒度へ下ろした文書である。決定の番号・規則（U1〜U6・W1〜W2）は設計文書を指す。
パスは `plugins/ndf/` からの相対で書く（リポジトリの根からのパスは `/` で始めずにそのまま書く）。

## 地図

```text
plugins/ndf/
├── contracts/
│   ├── limits.json                                   # review-units.target-lines / max-lines を足す
│   ├── schemas/project-workflow.schema.json          # workflow.review_units を足す
│   ├── schemas/ndf-settings/project.schema.json      # 同じキーを workflow に足す（preflight の知らないキーに数えない）
│   ├── templates/
│   │   ├── design-draft.json                         *（設計書を書いて Draft の PR を開くまで）
│   │   ├── design-review.json                        *（設計 PR のレビュー → 単位の確かめ → Ready → CI → 承認に使う HEAD）
│   │   ├── design-land.json                          *（承認した HEAD だけをマージする。承認を聞き直さない）
│   │   ├── implementation-plan.json                  # worker の指示に単位のファイルと render を足す（確かめのステップは足さない）
│   │   ├── impl.json                                 # worker の前に impl のプロンプトの全文を書く prompt（unit_file。既定は空）と、worker へ NDF_WORK_PROMPT
│   │   └── params.json                               # unit_file の既定
│   └── workflow/
│       ├── ndf.schema.json                           # 工程のメタデータに per_unit・restart_on・worktree_input を足す
│       ├── context.schema.json                       # 文脈のキー rebuild_findings・design_head・plan_omitted・design_worktree・design_branch・design_base・excluded を足す
│       ├── units-run.schema.json                     *（流し方の記録 units.json の形）
│       ├── review-units.schema.json                  *
│       ├── modes/{standard,legacy-refactor}.json     # 新しい 6 つの工程を指し、計画を詳細設計の直後へ
│       └── stages/
│           ├── design-draft.json                     *（工程「設計」。standard・legacy-refactor だけ）
│           ├── design-approval.json                  *（工程「ドキュメントレビュー」。standard・legacy-refactor だけ）
│           ├── implement-units.json                  *（工程「実装」。per_unit。standard・legacy-refactor だけ）
│           ├── design-detail-pr.json                 *（新しい経路の「詳細設計」。worktree_input: design_worktree）
│           ├── plan-pr.json                          *（新しい経路の「計画」。phase: design・design-drop → plan・worktree_input）
│           ├── document-rebuild-pr.json              *（新しい経路の「ドキュメント再構成」。rebuild_findings・worktree_input）
│           └── design-detail.json・plan.json・document-rebuild.json  # 変えない（light・operation・documentation が指す共有の定義）
├── src/ndf/
│   ├── core/plan/
│   │   ├── units_decide.py                           *（純粋な判定）
│   │   ├── units_files.py                            *（ファイル・宣言・文書・プランの読み書き）
│   │   ├── cli.py                                    # ndf plan units check / render / prompt
│   │   └── inputs_decide.py                          # design_gaps の単位の数の規則を消し、設計の結果の表の読み手（扱い・統合先を含む）を置く
│   ├── core/route/__init__.py                        # 既定のレビューの起動で design-review も --purpose design にする。宣言 merge.commit を先に読むマージの方式 declared_merge_method
│   ├── domains/materials/collect.py                  # 設計の結果の表の読み手と設計文書の番号の並びを inputs_decide のものへ寄せる
│   ├── ci_cli.py / adapters/github.py                # ndf ci merge に照合の経路（--pr・--base・--method・--since・--queue-approved と --target --answer）を足す。ci watch に --conflict と成功の instance
│   ├── domains/worktree/{create,cli}.py              # ndf worktree create に --no-fetch を足す（実行順ごとに固定した SHA から作る）
│   └── progress/
│       ├── run.py                                    # 工程の行を開き直す事象 reopened（世代を進める）・課題セットの事象 issues_set と、ステップの失敗で工程を開き直す restart_on
│       ├── gate/{asking,answering}.py                # 資料を作る時点で記録と検査結果を照らし、回答を design-answer.json に結ぶ。課題を外した回答で「計画」から開き直す
│       ├── stages.py / definition.py                 # per_unit・restart_on を足す（per_issue は残す）
│       ├── invoke.py                                 # per_unit の工程は単位ごとの作業場所・プラン・キュー・マージで流す。ndf.plan にも工程の入力を渡す
│       └── command.py                                # ndf progress units plans / run と ndf progress design target / ask / answer / drop / sync（supervisor が手で流す入口。工程と同じ処理）
└── tests/
    ├── adapters/test_ci_cli.py
    ├── core/plan/{test_units_decide.py*, test_units_cli.py*, test_inputs.py}
    ├── core/plan/fixtures/units/issue-3672.units.json *
    └── progress/{test_definition.py, test_command.py, test_sprint_templates.py, test_invoke.py}
```

依存の向きは一方向である。`units_decide` は標準ライブラリ（`graphlib`）と `inputs_decide.DesignResult` だけを読む。`units_files` は `units_decide`・`inputs_decide`（設計の結果の表の読み手）・`ndf.contracts`（スキーマと上限）・`ndf.core.route`（impl のテンプレートの描画）を読む。`cli.py` と `progress/invoke.py`・`progress/command.py` は `units_files` を読み、`units_files` はどれも読まない。単位ごとの差し込み（題・本文・レビューの起動）は `progress` の `template_values` が組むので、それを使う手動の入口は `core/plan/cli.py` でなく `progress/command.py` に置く。`core` から `domains` は読まない（設計の結果の表の読み手を `inputs_decide` に置き、`materials.collect` がそれを使う）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `contracts/workflow/review-units.schema.json` | 新設 | 単位のファイルの形（`version`・`issues`・`tasks`・`units`） | 無し |
| `contracts/limits.json` | 変更 | `review-units.target-lines`（`count: 400`）・`review-units.max-lines`（`count: 1000`） | 無し |
| `contracts/schemas/project-workflow.schema.json` | 変更 | `workflow.review_units`（`target_lines`・`max_lines`。整数 1 以上） | 無し |
| `contracts/schemas/ndf-settings/project.schema.json` | 変更 | `workflow` の `properties` に同じ `review_units` を足す | 無し |
| `src/ndf/core/plan/units_decide.py` | 新設 | 実行順・導く値・違反・警告・分け方の案 | `graphlib`・`inputs_decide` |
| `src/ndf/core/plan/units_files.py` | 新設 | 単位のファイルを集める・読む、上限を読む、文書の表を作る、単位のプランと指示のファイルを書く | `units_decide`・`inputs_decide`・`ndf.contracts`・`ndf.core.route` |
| `src/ndf/core/plan/cli.py` | 変更 | サブコマンド `units`（`check`・`render`・`prompt`） | `units_files` |
| `src/ndf/core/plan/inputs_decide.py` | 変更 | `design_gaps` から「PR の単位が 2 つ以上」の理由を消す（決定 2）。設計の結果の表の行の読み手 `design_results`（課題・扱い・統合先・触るファイル）、扱いの許容値 `HANDLINGS` と行の検査 `design_result_errors`、設計文書の名前の番号の並び `DESIGN_DOC` を置く（下の「設計の結果の表の読み手」） | 無し |
| `src/ndf/domains/materials/collect.py` | 変更 | `design_rows`・`DESIGN_FILE` を消し、`inputs_decide.design_results`・`DESIGN_DOC` に替える（読み手を 1 つにする） | `inputs_decide` |
| `src/ndf/ci_cli.py`・`src/ndf/adapters/github.py` | 変更 | `ndf ci merge` に照合の経路（`--pr`・`--base`・`--method`・`--since`・`--queue-approved` と、それらを承認の記録から読み回答と照らす `--target --answer`）を足す（下の「マージの対象の照合と再開」）。`ndf ci watch` に `--conflict`（同じ PR の読み取りで衝突を判定）を足し、成功の結果にも判定した PR の HEAD を `instance` に載せる（今は失敗のときだけ）。`GitHub.merge_queue(branch)`（キューの有無と方式。異なる宛先ごとに 1 回）を足す | 無し |
| `src/ndf/domains/worktree/create.py`・`cli.py` | 変更 | `ndf worktree create` に `--no-fetch` を足す（`--from` に渡した SHA から取得せずに作る。下の工程の流し方の 2） | 無し |
| `src/ndf/progress/run.py` | 変更 | 工程の行を開き直す事象 `reopened`（行を `pending` へ戻して世代を進め、その工程のステップの進みと待ちの回答を捨てる。保存先は世代ごと）・工程に属さない課題セットの事象 `issues_set` と、工程の定義の `restart_on`（ステップの失敗の種類で工程を最初のステップから開き直す）を読む（下の「承認を無効にする遷移」） | 無し |
| `src/ndf/progress/gate/asking.py`・`answering.py` | 変更 | 設計承認ゲートの資料を、記録（`design-target.json`）と検査の結果を照らしてから読んだバイト列で作り（`design-material.json`）、回答を `design-answer.json` と回答の事象にその組とハッシュで結ぶ。課題を外した回答では `issues_set` と「計画」から「ドキュメントレビュー」までの `reopened` を書く（戻した回答は「設計」から） | `run` |
| `src/ndf/core/route/__init__.py` | 変更 | `review_launch` の `--purpose design` を、テンプレート `design` と `design-review` の両方で選ぶ。`declared_merge_method`（宣言 `branch_strategy.merge.commit` を先に読む。下の「マージの方式の決め方」）を足す | 無し |
| `contracts/workflow/ndf.schema.json` | 変更 | 工程のメタデータに `per_unit`（`const: true`）・`restart_on`（ステップの id → 失敗の種類の配列）・`worktree_input`（テンプレートの作業場所を組む文脈のキー。省けば `worktree`）を足す。`per_issue` は残す | 無し |
| `contracts/workflow/units-run.schema.json` | 新設 | 流し方の記録 `units.json` の形（下の「流し方の記録」） | 無し |
| `contracts/workflow/stages/design-detail-pr.json` | 新設 | 新しい経路の工程「詳細設計」。`design-detail.json` と同じステップに `worktree_input: "design_worktree"`（設計の作業場所で書く）。`inputs` に `design_worktree`・`design_branch` を足す | 無し |
| `contracts/workflow/context.schema.json` | 変更 | 文脈のキー `rebuild_findings`（再構成の指摘のファイル）・`design_head`（承認した設計 PR の HEAD）・`plan_omitted`（実装計画を省いた課題の番号の配列）・`design_worktree`・`design_branch`・`design_base`（設計 PR の作業場所・head・宛先）・`excluded`（承認で外した課題。`issues_set` から取る）・`drop_file`（工程「計画」の `design-drop` の結果のファイル）・`units_queue`（単位のキューの承認。工程「ドキュメントレビュー」の `design-sync` が出す）を足す | 無し |
| `contracts/workflow/stages/plan-pr.json` | 新設 | 新しい経路の工程「計画」。`phase: design`・`worktree_input: "design_worktree"`・`inputs` に `issues`・`worktree`・`excluded`・`design_worktree`・`design_branch`・ステップ `design-drop` → `plan`（`implementation-plan`。`with` の `values` に `drop_file`）・`outputs: ["plan_omitted", "drop_file"]`（確かめのステップは足さない） | 無し |
| `contracts/workflow/stages/document-rebuild-pr.json` | 新設 | 新しい経路の工程「ドキュメント再構成」。`document-rebuild.json` と同じステップに `outputs: ["rebuild_findings"]`・`worktree_input: "design_worktree"`・`inputs` に `design_worktree`・`design_branch` を足す | 無し |
| `contracts/workflow/stages/design-draft.json` | 新設 | 工程「設計」（standard・legacy-refactor）。ステップ `design`（`design-draft`）、`outputs: ["design_pr", "design_worktree", "design_branch", "design_base"]` | 無し |
| `contracts/workflow/stages/design-approval.json` | 新設 | 工程「ドキュメントレビュー」（standard・legacy-refactor）。`docs-review`（`design-review`）→ `design-mvv` → `design-gate` → `design-merge`（`design-land`）→ `design-sync`。`restart_on: {"design-gate": ["stale_approval"], "design-merge": ["stale_approval"]}` | 無し |
| `contracts/workflow/stages/implement-units.json` | 新設 | 工程「実装」（standard・legacy-refactor）。`implement.json` と同じステップに `per_unit: true`。`inputs` に `plan_omitted`・`units_queue` を足す | 無し |
| `contracts/workflow/modes/standard.json`・`legacy-refactor.json` | 変更 | 新しい 6 つの工程（`design-draft`・`design-detail-pr`・`plan-pr`・`document-rebuild-pr`・`design-approval`・`implement-units`）を指し、「計画」を「詳細設計」の直後へ移す | 無し |
| `contracts/templates/design-draft.json` | 新設 | 設計の作業場所を作る（`<branch>-design`）→ 設計の worker → push → PR を宛先 `pr_target("doc")` へ Draft で開く | 無し |
| `contracts/templates/design-review.json` | 新設 | 下の「設計 PR の承認とマージ」の並び | 無し |
| `contracts/templates/design-land.json` | 新設 | 承認の記録を `ndf ci merge --target` でマージする（承認のステップを持たない） | 無し |
| `contracts/templates/implementation-plan.json` | 変更 | worker の指示に「単位のファイルを書き、`ndf plan units render` で表を作る」を足す | 無し |
| `contracts/templates/impl.json`・`params.json` | 変更 | 差し込み `unit_file`（既定は空の文字列）と、worker の前のステップ `prompt`（impl のプロンプトの全文を書く）を足し、worker のステップに `NDF_WORK_PROMPT` を渡す。`worker` の説明に契約を書く（下の「worker への単位の指示」） | 無し |
| `src/ndf/progress/stages.py`・`definition.py` | 変更 | 工程の型に `per_unit: bool = False` と `restart_on: Mapping[str, tuple[str, ...]]`（ステップの id → 開き直す失敗の種類）を足す（`per_issue` はそのまま） | 無し |
| `src/ndf/progress/invoke.py` | 変更 | `per_unit` の工程の `ndf.plan` のステップを、単位ごとの作業場所・プラン・`ndf plan queue`・マージで流す。`ndf.plan` の起動にも `NDF_STAGE_INPUT` を渡す | `units_files` |
| `src/ndf/progress/command.py` | 変更 | `ndf progress units plans`・`ndf progress units run`・`ndf progress design target`・`ask`・`answer`・`drop`・`sync`（工程の外から同じ処理を打つ入口） | `invoke` |
| `tests/adapters/test_ci_cli.py` | 変更 | `--pr`・`--base` の照合、マージ済みの成功、キューの待ち、残リスクと方式、再オープン・宛先の変更の事象、API の回数、`ci watch` の成功の `instance` | 無し |
| `tests/core/plan/test_units_decide.py` | 新設 | 判定の単体テスト（受け入れ条件 2・5・7・8、U1〜U6・W1〜W2） | fixture |
| `tests/core/plan/test_units_cli.py` | 新設 | コマンドのテスト（受け入れ条件 3・4・6、`render --check`、ファイルの集め方） | fixture |
| `tests/core/plan/fixtures/units/issue-3672.units.json` | 新設 | #3672 の詳細設計の地図から作った 1 単位のファイル（受け入れ条件 8） | 無し |
| `tests/core/plan/test_inputs.py` | 変更 | `design_gaps` の単位の数のテストを消し、`design_rows` のテストを `design_results` へ移す（扱い・統合先・統合文書の #11 の行）。設計テンプレートどおりの「マージする」の行が `design_result_errors` を通り、空欄・許容値の外・統合先の欠けが誤りになる | 無し |
| `tests/progress/test_definition.py`・`test_command.py`・`test_sprint_templates.py`・`test_invoke.py` | 変更 | 5 つのモードの工程の並び・ゲートの位置・出力のキー・`per_unit` の流し方・承認とマージ | 無し |

`light`・`operation`・`documentation` は今の `design.json`・`document-review.json`・`implement.json` を指したまま変えない（設計書の決定 1）。手順書（`skills/` の 8 ファイル）の変更は実装計画のタスクにあり、関数を持たないのでこの文書に下ろさない。

## 構成要素ごとの仕様

### 単位のファイルの形（`review-units.schema.json`）

満たす決定: 2・3。満たす受け入れ条件: 2・3。

| キー | 形 | 規則 |
| --- | --- | --- |
| `version` | `1` | 定数 |
| `issues` | 整数の配列（1 以上） | 単位のファイルが扱う課題。ファイル名の番号の並びと一致する |
| `tasks[]` | `id`（`^T[0-9]+$`）・`title`・`issue`（整数。`issues` が 2 つ以上なら必須、1 つなら省けてその課題）・`files`（文字列の配列、1 以上。各値はリポジトリの根からの**正規形**のファイルのパス。スキーマの `pattern` は `^(?!/)(?!.*/$)(?!.*//)(?!(?:.*/)?\.{1,2}(?:/|$))[^*?\[\]\\]+$`＝先頭と末尾に `/` が無く、`//`・`.` と `..` の区切り・glob・`\\` を含まない。下の「パスの検査」も通す）・`lines`（整数 0 以上）・`criteria`（`^[0-9]+:[0-9]+$` の配列）・`after`（タスクの `id` の配列） | `additionalProperties: false` |
| `units[]` | `name`（1 文字以上）・`tasks`（タスクの `id` の配列、1 以上）・`after`（`{unit, reason}` の配列。`reason` は空白でない文字を含む）・任意の `actual_lines`（整数 0 以上） | `additionalProperties: false` |

**パスの検査**（`units_files.load`。スキーマで縛れない分）: 各値が `posixpath.normpath(値) == 値` であること、`repo / 値` が既存のディレクトリでないこと、`(repo / 値).resolve()` がリポジトリの根の中にあること（リポジトリの外を指すシンボリックリンクを断る）を確かめ、当たれば `UnitsError`（パスとタスクの `id`）。まだ無いファイル（新設）は、親のディレクトリが無くても通す。正規形だけを通すので、U4 は文字列の一致で同じファイルを見分けられる。

タスクの所属の課題の正本は `tasks[].issue` である。単位の課題はその単位のタスクの `issue` の集まり（現れた順）、**代表の課題**は単位の最初のタスクの `issue` とする。スキーマで縛れない規則（重複・参照の実在・輪・覆い・重なり・行数・所属）は `units_decide.judge` が持つ。

### 単位の判定（`core/plan/units_decide.py`）

満たす決定: 3・4・5・6。満たす受け入れ条件: 2・5・7・8。ファイル・git・環境変数を読まない。

```python
class Task(NamedTuple): id: str; title: str; issue: int; files: tuple[str, ...]; lines: Optional[int]; criteria: tuple[str, ...]; after: tuple[str, ...]; whole: bool = False
class Edge(NamedTuple): unit: str; reason: str
class Unit(NamedTuple): name: str; tasks: tuple[str, ...]; after: tuple[Edge, ...]; actual_lines: Optional[int] = None
class Units(NamedTuple): issues: tuple[int, ...]; tasks: tuple[Task, ...]; units: tuple[Unit, ...]
class Limits(NamedTuple): target: int; max: int
class View(NamedTuple): name: str; order: int; merge: int; lines: Optional[int]; touches: tuple[str, ...]; issues: tuple[int, ...]; criteria: tuple[str, ...]; after: tuple[Edge, ...]
class Finding(NamedTuple): rule: str; unit: str; reason: str
class Proposal(NamedTuple): unit: str; units: tuple[Unit, ...]; rewired: tuple[Unit, ...]; note: Optional[str]; resplit: tuple[str, ...] = ()
class Judgement(NamedTuple): views: tuple[View, ...]; violations: tuple[Finding, ...]; warnings: tuple[Finding, ...]; proposals: tuple[Proposal, ...]
```

`Task.whole` は「課題のすべての受け入れ条件を持つ」の印で、単位のファイルからは作らない（スキーマにキーが無い）。`single` だけが真にする。`Proposal.resplit` は書き手に切り直しを求めるタスクの `id` で、空でなければ `units` も空にし、`note` に理由を書く（案を当てられない）。

| 関数 | 入力 → 返り | 異常系 |
| --- | --- | --- |
| `parse(doc: Mapping[str, Any]) -> Units` | スキーマを通った辞書 → 型（`issue` を省いたタスクはファイルの唯一の課題） | 投げない（形はスキーマが先に断る） |
| `merge(parts: Sequence[tuple[str, Units]]) -> Units` | 複数の単位のファイル（`(ファイルの名前, Units)`）を 1 つのグラフにする。タスクの `id` と単位の `name` を `<ファイルの名前>/<元の値>` にし、`after` も同じく付け替える（ファイルが 1 つなら付け替えない） | 投げない |
| `orders(units: Units) -> tuple[dict[str, int], list[Finding]]` | 単位ごとの実行順（前提 1）。先にタスクのグラフ（`tasks[].after`）を `graphlib.TopologicalSorter` で並べて参照と輪を確かめ、次に単位のグラフを並べ、並べた順に「先に要る単位の実行順の最大 + 1」を取る | タスク・単位の重複、先に要るタスク・単位が無い、どちらかのグラフの輪は `Finding("U1", …)`。そのときは実行順を返さない（空の辞書） |
| `views(units, order) -> tuple[View, ...]` | 単位ごとの導く値。行数はタスクの `lines` の和（どれかが None なら None）、触るファイル・課題・受け入れ条件はタスクの和（現れた順）、`merge` は実行順、同じ実行順は `units[]` の並びで振った 1 からの番号 | 投げない |
| `judge(units, limits, designed: Optional[Mapping[int, DesignResult]] = None, known: Optional[Mapping[int, tuple[int, ...]]] = None) -> Judgement` | U1〜U6 の違反、W1〜W2 の警告、行数の規則に当たった単位ごとの分け方の案（`proposals` は `check` の結果に `split` を足したもの）。`known` は課題ごとの受け入れ条件の番号（U2 の番号と覆いの検査に使う） | 投げない。違反があっても `views` は実行順の取れる範囲で返す |
| `check(units, limits, designed=None, known=None) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]` | 違反と警告だけ（案を作らない）。`judge` はこれを呼んでから案を作り、`split` は案の検証にこれだけを使う（`judge` を呼ばないので案の生成が循環しない） | 投げない |
| `split(unit: Unit, units: Units, limits: Limits) -> Proposal` | 決定 6 の案（下の「分け方の案の作り方」） | 1 つのタスクで `max` を超えるときは、そのタスクだけの単位を置き、`note` に「T<n> を書き手が対象ファイルごとに分け直す」と書く。案を作れないとき（下の 4・6）は `units` を空にし、`resplit` と `note` で書き手に返す |
| `apply(units: Units, proposal: Proposal) -> Units` | 元の単位を案の単位に置き換え、`rewired` の単位（元の単位を指していた後続）を差し替えたグラフ | 投げない |
| `single(issue: int, designed: Optional[DesignResult]) -> Units` | 単位のファイルを持たない課題を、課題 1 つを 1 単位（タスク 1 つ、`lines` は None、`files` は設計の結果の触るファイル、`criteria` は空、`whole` は真＝その課題のすべての条件）にする（受け入れ条件 4） | 設計の結果の無い課題は `files` を空にする（プランに `touches` を書かず、キューはすべてと重なるとみなして 1 本ずつ流す） |

規則の判定:

| 規則 | 当たる条件 |
| --- | --- |
| U1 | タスクの `id`・単位の `name` が重なる。タスクの `after` か単位の `after` が無いものを指す。タスクのグラフか単位のグラフが輪になる（同じ単位の中のタスクの輪も含む） |
| U2 | タスクがどの単位にも無い、または 2 つ以上の単位にある。単位の `tasks` が無いタスクを指す。タスクの `issue` がファイルの `issues` の外にある。`criteria` の課題がそのタスクの `issue` でも、設計の結果で `issue` へ「マージする」課題でもない。`criteria` の条件の番号が `known` にその課題の条件として無い（`known` を渡したとき）。**条件の覆い**: `known` を渡したとき、単位を持つ課題（`whole` でない）と、その課題へ「マージする」課題のそれぞれについて、`known` の条件の番号のすべてがグラフ全体のどれかのタスクの `criteria` にある（無い条件ごとに `Finding("U2", <課題の最初の単位>, "#<課題>:<番号> がどの単位にも割り当たっていない")`）。単位が受け入れ条件を 1 つも持たず、`whole` のタスクも持たない（条件を持たない準備のタスクは、条件を持つ単位へ束ねる）。`single` の単位は `whole` で課題のすべての条件を持つので、覆いも単位の条件も当たらない |
| U3 | タスク B の `after` にあるタスク A の単位が、B の単位でも B の単位の依存の閉包の中でもない |
| U4 | 実行順の同じ 2 つの単位の触るファイルに、同じパスがある（パスは具体的なファイルだけなので、文字列の一致で決まる。ファイルをまたいで `merge` した後のグラフで確かめる） |
| U5 | 単位の依存の `reason` が空白だけ（スキーマが通しても、前後の空白を除いて空なら当てる） |
| U6 | `lines` が None でなく `limits.max` を超える |
| W1 | `lines` が None でなく `limits.target` を超え、`limits.max` 以下 |
| W2 | `designed` が渡され、単位の課題が設計の結果の表に無い、または単位の触るファイルが単位の課題の触るファイルの和の外にある |

#### 分け方の案の作り方（`split`）

1. 単位のタスクを、単位の中のタスクの依存の深さ（先に要るタスクの深さの最大 + 1。単位の外のタスクは数えない）で層に分ける
2. 同じ層の中で `files` の重なるタスクを連結成分に束ねる。成分の行数が `target` 以下なら 1 つの単位にする。超えるなら成分のタスクを 1 つずつの単位にし、同じファイルを触るタスクの単位どうしを `tasks[]` の並びで直列につなぐ（理由「同じファイルを触る」）。直列にした単位は案の中で実行順が分かれる
3. 重ならない成分は、`target` を超える手前まで同じ単位へ足し、超えたら次の単位を始める（`<元の名前> <番号>`）
4. 受け入れ条件を持たない案の単位（準備のタスクだけの単位）は、そのタスクを先に要る案の単位のうち `tasks[]` の並びで最初のものへ併せる（U2 を避ける）。併せた単位の行数が `max` を超えるか、先に要る案の単位が無いときは案を作らない: `resplit` にその準備のタスクと併せる先のタスク、`note` に「条件を持たない T<n>（<行数> 行）を、条件を持つタスクへ割って入れるか、条件を割り当ててタスクを切り直す」を返す。例: 条件なしの準備 T1（500 行）と、T1 に依存する条件ありの T2（600 行）は、併せると 1,100 行で `max` を超えるので `resplit: ("T1", "T2")`
5. 各単位に、その単位のタスクが先に要るタスクを含む案の単位への依存を理由「タスクの依存の深さ」で付ける
6. 元の単位の `after` は、先に要る案の単位を持たない案の単位（最初の層）のすべてが引き継ぐ。元の単位を `after` で指していた後続の単位は、指す先を案の単位のうち後続を持たないもの（最後の層）のすべてに付け替え、`rewired` に返す
7. **グラフ全体で確かめ直す**: `apply` で元のグラフに当て、`orders` と U4 を**グラフ全体**で判定する。層の中を直列にすると案の単位の実行順が後ろへずれ、元の単位の外の単位（たとえば元の単位と同じファイルを触り、もともと実行順 2 にいた単位）と同じ実行順で重なることがある。当たった組（案の単位 P と外の単位 Q）ごとに次のどちらかにする。同じ実行順の 2 単位はどちらも相手を閉包に持たないので、依存を足しても輪にならない
   - **依存を直す**: Q が元の単位と同じ単位のファイルにあれば、Q を P の後に置く（Q の `after` に P、理由「同じファイルを触る」。Q を `rewired` に足す）。案はそのファイルの中で閉じるので書き手はそのまま写せる
   - **書き手に返す**: Q が別の単位のファイルにある（`merge` で接頭辞の違う単位）なら、単位の `after` はファイルをまたげないので直せない。案の単位を空にし、`resplit` に P と Q のタスク、`note` に「<P のファイル> の T<n> と <Q のファイル> の T<m> が同じファイルを触る。どちらかのファイルでタスクの触るファイルか実行順を直す」を返す

   足すたびに `orders` を取り直し、U4 の組が無くなるまで繰り返す（足す依存は組 1 つに 1 本で、組の数は有限なので止まる）。依存を足したことで、案の外の単位どうし（Q と、Q が後ろへずれて同じ実行順になった R）が新たに U4 に当たったときも同じ規則で直すか返す。R が Q と別の単位のファイルにあれば `resplit` に Q と R のタスクを載せて返す
8. **案を生成しない判定で検証する**: 終わった後のグラフを `check`（案を作らない）に通す。U1（4 の併せや 7 の依存で輪ができた）か、U6 の `note` 付きの単位以外の違反が残れば、案を当てずに `units` を空にし、`resplit` に違反の単位のタスク、`note` に違反の行を返す。W1 は残ってよい（目安を超える 1 タスクの単位は分けようがない）。`split` は `judge` を呼ばず、`judge` は `split` の返りを再び `split` しないので、たとえば目安 400 行で条件を持つ 600 行の単独タスクの単位は、W1 のままで案を作らずに終わる（`judge` が案を作るのは U6 の単位と、W1 でタスクが 2 つ以上の単位だけ）。テストは、案を当てたグラフの `check` に違反が無いこと、併せで輪ができる入力と、依存を足して案の外の 2 単位が重なる入力で `resplit` が返ることを縛る

### 単位のファイルの読み書き（`core/plan/units_files.py`）

満たす決定: 2・5・7。満たす受け入れ条件: 3・4・6。

| 関数 | 入力 → 返り | 異常系 |
| --- | --- | --- |
| `collect(repo: Path, issues: Sequence[int], omitted: Sequence[int]) -> Collected` | 下の「単位のファイルの集め方」。`omitted` は工程「計画」が実装計画を省くと決めた課題（文脈の `plan_omitted`）。返りは読んだ単位のファイル（パスの一意な並び）・ファイルを持たない課題とその扱い（`single`・`skip`） | 下の表 |
| `load(path: Path) -> Units` | スキーマ（`review-units.schema.json`）で確かめて `parse` | 読めない・形が違うと `UnitsError`（理由にパスとスキーマの誤り） |
| `designed(repo: Path, issues: Sequence[int]) -> dict[int, DesignResult]` | 名前の番号の並び（`inputs_decide.DESIGN_DOC`）に課題の番号を含む設計文書（`issues/issue-10-11-design.md` は #10 と #11 の両方）の「設計の結果」の表を `inputs_decide.design_results` で読み、課題ごとに行を 1 つにする（`materials.collect` と同じ読み手）。表に行の無い課題は返りに載せない | 読めない文書は飛ばす（今の `design_files` と同じ）。行の扱いか統合先が `design_result_errors` に当たれば `UnitsError`（文書のパスと課題の番号）。同じ課題の行が 2 つの文書で扱いか統合先が食い違えば `UnitsError` |
| `criteria_of(repo: Path, issues: Sequence[int]) -> dict[int, tuple[int, ...]]` | 課題ごとの受け入れ条件の番号（`judge` の `known`）。要求仕様 `issues/issue-<番号>-requirements.md` の受け入れ条件の番号を読み、無ければ `ndf loop acceptance` と同じ読み手で課題の本文（工程が書いた `ISSUE_BODY_FILE` のキャッシュがあればそれ、無ければ `gh issue view` を課題ごとに 1 回）から読む | 条件を読めない課題は `UnitsError`（「#<番号> の受け入れ条件を読めない」）。条件を確かめずに通さない |
| `limits(repo: Path) -> Limits` | `.ndf/project.json` の `workflow.review_units` を先に、無いキーは `contracts.limits.value("review-units.target-lines", "count")` と `max-lines` | `target >= max` は `UnitsError` |
| `render(units, judgement, doc: str) -> str` | `units`・`judgement` は**その文書と対の単位のファイル 1 つだけ**を `merge` せずに `judge` したもの（タスクの `id`・単位の名前に接頭辞が無く、実行順・マージの順はそのファイルの中で数える）。実装計画の文書の 2 つの区間（`<!-- ndf:units:begin -->`〜`<!-- ndf:units:end -->` と `<!-- ndf:order:begin -->`〜`<!-- ndf:order:end -->`）を作り直した全文。前者は「レビューの単位」の表（単位・実行順・マージの順・含むタスク・先に要る単位・満たす受け入れ条件・見込みの行数・実際の行数＝`actual_lines`）、後者は「並列と直列」の表（実行順ごとに並列の単位と理由「同じ実行順で依存が無く、触るファイルが重ならない」。単位が 1 つの実行順は理由を「—」。依存ごとに直列の 2 単位と依存の理由） | 区間の印が無いか片方だけなら `UnitsError` |
| `parallel_cap(environ: Mapping[str, str], lane_plans: Optional[int]) -> int` | 同じ実行順で同時に流す本数の上限。`min(lane_plans, limits.from_env(environ, "NDF_RELAY_MAX_NAMED", "relay.max-named", "count"))`（`lane_plans` が None ならマシンの上限だけ）。マシンの上限は `progress/lane/machine.cap` と同じ読み方 | 投げない |
| `unit_plans(repo, given: UnitGiven, out: Path) -> list[UnitPlan]` | 下の「単位のプランの組み立て」 | 違反があれば何も書かずに `UnitsError`（理由に違反の行） |

#### 単位のファイルの集め方（`collect`）

実装計画の Markdown と単位のファイルには、同じ名前の規則を当てる。**計画の名前**は `issues/issue-<番号の並び>-plan` で、番号の並びは `-` で区切った 1 つ以上の番号（`inputs_decide.PLAN_DOC = ^issues/issue-(\d+(?:-\d+)*)-plan(\.units\.json|\.md)$`）である。Markdown（`.md`）と単位のファイル（`.units.json`）は、計画の名前が同じものを対にする。

1. **扱いを先に決める**: `designed` で課題ごとの設計の結果の扱いを読み、計画のファイルを見る前に確定する。扱いが「マージする」「閉じる」の課題（以下、**単位を作らない課題**）は、計画の有無にかかわらず `skip` とする
2. `issues/` の下で `PLAN_DOC` に当たるファイルのうち、番号の並びのどれかがスプリントの課題の番号と一致するものを、計画の名前ごとに集める（統合の計画 `issue-10-11-plan.*` は #10 と #11 のどちらからも当たるが 1 回だけ読む）
3. 計画の名前ごとに、Markdown と単位のファイルを**双方向に**確かめる
   - 単位のファイルがあれば、同じ名前の Markdown が無ければ止める（`UnitsError`「単位のファイル `<パス>` に対の実装計画 `<名前>.md` が無い」）。Markdown に 2 つの区間の印（開始と終了の両方）がそろっていなければ止める（「`<名前>.md` に区間 <印> が無い」）。単位のファイルの `issues` がスプリントの課題の中にあり、名前の番号の並びと一致する
   - Markdown に `<!-- ndf:units:begin -->` の印があるのに、同じ名前の単位のファイルが無ければ止める（`UnitsError`「実装計画 `<パス>` があるのに単位のファイル `<名前>.units.json` が無い」）。`issue-10-11-plan.md` に印があり `issue-10-11-plan.units.json` が無いときも、#10・#11 のどちらも省いた扱いに落とさない
   - 印の無い Markdown だけの計画は、**スプリントの起点に既にあった**ときだけ旧い形とする: `git cat-file -e <remote>/<スプリントの PR の宛先>:<パス>`（宛先は `branch_strategy.pr_target("sprint")`。取得はしない）が通れば旧い形、通らなければ止める（「`<パス>` はこのスプリントで書いた実装計画なのに区間の印と単位のファイルが無い」）。新しく書いた計画の JSON と印の書き忘れを旧い形として通さない
   - 課題が 2 つの計画の名前に現れれば止める
   - **単位を作らない課題との矛盾**: 単位を作らない課題が計画の名前・単位のファイルの `issues`・`tasks[].issue` のどれかに現れれば止める（`UnitsError`「#<番号> は設計の結果で <扱い>（<設計文書のパス>）なのに、計画 `<パス>` にある」）。統合する課題（「マージする」）のタスクは統合先の課題（`issue` を統合先）として統合先の計画に書き、条件は `criteria` に `<統合する課題>:<番号>` で載せる（U2 がこれを許す）。統合する課題の旧い計画が残っていれば、それも止める（書き手が工程「計画」で消す）。止めるので、承認した扱いと矛盾する単位の PR は作られない
4. 単位を作らない課題を除き、どの計画の名前にも現れない課題は次の順で扱いを決める。**実装計画を省いてよいのは、工程「計画」が省くと決めて `plan_omitted` に載せた課題だけ**で、ファイルが無いことから省いたとは推さない

| 課題の状態 | 扱い |
| --- | --- |
| 設計の結果の表で扱いが「マージする」 | `skip`（単位を作らない。統合先の課題の単位が持つ）。統合先がスプリントの外か、統合先の扱いが「実装する」でなければ止める（`UnitsError`「#<番号> の統合先 #<統合先> がスプリントで実装されない（<文書のパス>）」）。統合する課題の条件は統合先の単位が覆う（U2） |
| 設計の結果の表で扱いが「閉じる」 | `skip`（単位を作らない。実装の PR を持たずにスプリント PR で閉じる） |
| 設計の結果の表で扱いが「実装する」か、表に行が無い | 下の 3 行（計画の名前・`omitted`）で決める |
| 計画の名前に現れ、Markdown に印が無く単位のファイルも無く、スプリントの起点に既にあった（この変更より前に書いた実装計画） | `single`（今の振る舞いのまま）。`check` は `warnings` に「旧い形の実装計画で行数を確かめていない」を載せる |
| 計画の名前に現れず、`omitted` にある（工程「計画」が文書の役割の表の「省いてよい文書と条件」で省いた） | `single`。`check` の `metrics.omitted` に課題を載せ、承認資料に「実装計画を省いた課題（行数は確かめていない）」として出す |
| 計画の名前に現れず、`omitted` にも無い | 止める（`UnitsError`「#<番号> の実装計画が無く、省く判断も無い」）。作成必須の課題で Markdown と単位のファイルの両方を書き忘れても、行数を確かめずに進めない |

`plan_omitted` は工程「計画」の出力で、`implementation-plan` の worker が省くと決めた課題を報告に載せ、工程がそれを文脈へ書く（省かなかったスプリントでは空の配列）。新しく書く実装計画は worker が `render` で必ず印を置くので、印の有無で新旧を分けられる。

#### 設計の結果の表の読み手（`inputs_decide.design_results`）

`materials.collect.design_rows` は触るファイルしか返さず、`DESIGN_FILE` は名前の最初の番号しか拾わない。`collect` が扱い・統合先を読み、統合文書の後ろの番号の行も読めるよう、読み手を次の形で `inputs_decide` に置き直す（Task 6）。

```python
class DesignResult(NamedTuple): issue: int; files: tuple[str, ...]; units: tuple[str, ...] = (); delegated: tuple[str, ...] = (); handling: str = "実装する"; into: Optional[int] = None
DESIGN_DOC = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-design[^/]*\.md$")
def design_numbers(path: str) -> tuple[int, ...]  # DESIGN_DOC の番号の並び（当たらなければ空）
def design_results(text: str) -> list[DesignResult]  # 「設計の結果」の表の行ごと
```

- 行の型: 表の列は今の「課題・扱い・統合先・触るファイル」の順で、`row[0]` の `#<番号>` を `issue`、`row[1]` を `handling`（前後の空白を除いた値をそのまま持つ。値の検査はしない）、`row[2]` の `#<番号>` を `into`（「—」や空なら None）、`row[3]` を `files_cell` で `files` と `delegated` にする。列が 4 つに満たない行は飛ばす（今の `design_rows` と同じ）
- 扱いの値: 設計テンプレート（`skills/design/references/design-template.md` の「設計の結果の書き方」）と設計書の決定 2 のとおり `HANDLINGS = ("実装する", "マージする", "閉じる")` を `inputs_decide` に置く。`design_result_errors(result: DesignResult) -> list[str]` が 1 行の誤りを返す: 扱いが `HANDLINGS` に無い（空欄を含む）、「マージする」で `into` が None、「実装する」「閉じる」で `into` が None でない、`into` が自分の課題。`units_files.designed` は読んだ行ごとにこれを打ち、誤りがあれば `UnitsError`「<文書のパス> の #<番号> の行: <誤り>」で止める。`designed` は `units check --from-stage`（工程「ドキュメントレビュー」の ③）と `unit_plans` の両方が打つので、不正な行は設計承認の前に止まる。`materials.collect` は `design_results` の `files` だけを使い、検査を打たない（返りを今と変えない）
- 統合文書の探索: `design_numbers` が番号の並び全体を返すので、`issue-10-11-design.md` は #11 からも見つかり、その文書の #11 の行を読める
- 既存の材料収集: `materials.collect.design_files(root, number, files)` は `DESIGN_FILE.match` を `number in design_numbers(rel)` に、`design_rows(text, number)` を `[r.files for r in design_results(text) if r.issue == number]` の和に替える。返りの形（文書ごとの触るファイル）は変えない

#### 単位のプランの組み立て（`unit_plans`）

`UnitGiven` は工程が渡す値（スプリントの課題 `issues`・省いた課題 `omitted`・スプリントのブランチ `sprint`・リモート・状態の保存先・単位のキューの承認 `queue`（下の「単位のキューの承認の受け渡し」の `units_queue`）・テンプレートの差し込みの組み立て `values_of(unit) -> 値`）である。

1. `collect(repo, given.issues, given.omitted)` → 各ファイルを `load` → ファイルを持たない `single` の課題を足して `merge` → `designed` → `judge`。違反があれば何も書かない。ファイルをまたぐ U4 もここで当たる
2. 単位に、スプリント全体で一意な連番 `seq` を、実行順 → マージの順（ファイルのパスの順 → `units[]` の並び → `single` の課題の番号の順）で振る（`numbering(units, order, files)`。`units check` の `unit` の項目も同じ関数で振る）。プランの名前・ブランチ・作業場所はどれも `<sprint>-u<seq>`、出力は `out/<sprint>-u<seq>.json`（別のファイルどうしで同じ名前にならない）
3. 単位ごとに指示のファイル `out/<sprint>-u<seq>.unit.json` を書く: 単位の名前・代表の課題・単位の課題・担当のタスク（`id`・`title`・`files`・`criteria`）・実装計画の文書のパス。`single` の単位には書かない（担当のタスクを持たないため。下の「worker への単位の指示」）
4. 差し込み: `issue` は代表の課題、`acceptance` は単位の課題のうち条件を持つ課題ごとに `--issue <課題>` と、その課題の条件ごとに `--criteria <課題>:<条件>`（条件を持たない課題には `--issue` を付けない。全部の条件を照らさないため）。`single` の単位は `--issue <課題>` だけ（課題のすべての条件）。`unit_file` は 3 の指示のファイル（`single` は空の文字列）。残りの差し込み（題・本文・`review`・`test_command` など）は `given.values_of(unit)` が組む（下の工程の流し方）
5. プランの最上位へ `touches`（単位の触るファイル。空なら書かない）を足す。`needs` は**同じ実行順のプランだけ**を指す（同じ実行順には依存が無いので、実際には書かない）。前の実行順への依存は、工程が前の実行順のマージを確かめてから次のキューを打つことで守る（今の `ndf plan queue` は起動ごとに済んだプランを空から数え、別の起動で済んだ `needs` を解けないため）。単位の依存は `items[].after` と指示のファイルに残す
6. 返りは実行順・マージの順に並べた `UnitPlan(path, unit_file, name, order, merge, branch)`

### コマンド（`core/plan/cli.py`）

満たす受け入れ条件: 3・5・6。結果 JSON の `tool` は既存の `TOOL`（`plan`）。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `units check [<ファイル>...] [--issue N]... [--omitted N]... [--from-stage] [--docs] [--lane-plans N] [--out <ファイル>]` | ファイル・`--issue`・`--from-stage`（環境変数 `NDF_STAGE_INPUT` の `issues`・`plan_omitted`・`excluded`。`excluded` の残りの検査は上の「外した課題の文書を取り除く」の 3）のどれかで単位のファイルを `collect` の規則で集め（手で打つときの省いた課題は `--omitted`）、`designed` を読んで（ファイルを直に渡したときも、その課題の設計の文書があれば）**`merge` した後のグラフ全体**で `judge` を打つ（ファイルをまたぐ U4 もここで当たる）。`--docs` は加えて、印のある実装計画の文書ごとに、**対の単位のファイル 1 つだけ**のグラフで `render` を作り、今の文書と照らす（`render <そのファイル> --doc <その文書> --check` と同じ判定を同じ起動の中で行う。表は書き手が `render` で作った形と同じになる） | 違反が無ければ `ok`（`items` に `unit` と `split`、`warnings` に W1・W2 と旧い形の文、`metrics` に `units`・`orders`・`target`・`max`・`omitted`・`head`（確かめた HEAD）・`parallel`（`parallel_cap` の値。`--lane-plans` か `--from-stage` の入力のレーンの `max_plans` を渡す）・`docs`（`--docs` を付けたか）・`files`（読んだ計画・単位のファイル・設計文書のパスと、HEAD のコミットでの blob の SHA。読んだファイルが HEAD と違えば `head` を書かずに `stopped`（`invalid`）。下の「HEAD の照合」の 3））。`unit` の項目は**スプリント全体の `merge` 後のグラフ**で、実装が使う値と同じ番号を持つ: `{"kind": "unit", "file", "name", "seq", "order", "merge", "parallel_with"（同じ実行順のほかの単位の `seq` と名前）, "after", "issues", "criteria", "lines"}`。`seq`・`merge` は `unit_plans` の 2 と同じ関数（`numbering(units, order, files)`）で振るので、承認資料の表と実装のマージの順が食い違わない。`--out` を渡せば同じ結果 JSON をそのファイルにも書く（設計の承認の記録が読む。下の「設計の承認の記録」）。違反・表の食い違いがあれば `stopped`（`invalid`、`items` に `violation` と `split`。案を作れない単位の `split` は `units` が空で `resplit` と `note` を持ち、書き手にタスクの切り直しを求める）。読めない・宣言の誤り・書き忘れは `stopped`（`invalid`） |
| `units render <ファイル> --doc <実装計画> [--check]` | `render` で全文を作り、`--check` が無ければ書く（worker が手元で使う。単位のファイルが複数あるスプリントでも、ファイルごとに打つ） | `ok`。`--check` で食い違えば `stopped`（`invalid`、理由「表が単位のファイルと食い違う」） |

単位のプランを組んで流す手動の入口は `ndf plan` でなく `ndf progress units`（`progress/command.py`）に置く。題・本文・既定のレビューの起動は単位ごとに違い、それを組む `template_values` が `progress` にあるためである。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `ndf progress units plans --out <ディレクトリ> --sprint <ブランチ> [--remote R] [--issue N]... [--omitted N]... [--from-stage] [--target <design-target.json> --answer <design-answer.json>] [--set 名前=値]... [--lane-plans N]` | `--target`・`--answer`（`--from-stage` では文脈の `units_queue`）から下の「単位のキューの承認の受け渡し」の `units_queue` を決め、工程と同じ `values_of`（下の工程の流し方の 1）で `unit_plans` を打つ。単位ごとの差し込み（`issue`・`acceptance`・`unit_file`・`title`・`body`・`review`・`branch`・`worktree`・`base`）は単位ごとに組み、`--set` で与えると `stopped`（`invalid`、「単位ごとに組む差し込み <名前> は --set で与えない」）。`--set` は**それ以外の差し込みにだけ全単位に共通に**当たる（`test_command` など） | `ok`（`items` に `{"kind": "plan", "path", "unit_file", "unit", "order", "merge", "branch", "after"}`、`metrics.max` に `parallel_cap` の値）。同じ値を**流し方の記録** `out/units.json` にも書く（下の「流し方の記録」）。違反は `stopped`（`invalid`） |
| `ndf progress units run --out <ディレクトリ>` | `out/units.json` を読み（スプリント・リモート・上限・単位の並びはそこから取り、引数で与えない）、`plans` が書いたプランを、下の工程の流し方の 2〜7（実行順ごとの作業場所・キュー・マージ・記録・再開・スプリントの worktree の早送り）と同じ関数で流す。打ち直すと記録から続ける | 工程の `_call_units` と同じ（`ok`・`ci_pending`・`stopped`） |

#### 流し方の記録（`units.json`）

`unit_plans` は、プランと指示のファイルに加えて `out/units.json` を書く。工程（`_call_units`）と手動（`ndf progress units run`）はどちらもこのファイルだけから流し方を復元し、組み立ての後に起動が分かれても同じ計画で流す。

**計画と状態は 1 組**: `units.json`・プラン・指示のファイル（計画）と、`orders.<k>.base_sha`・`work_dir/units/<seq>.json`（状態）は、同じ組み立てから生まれた 1 組としてだけ使う。

- **初回だけ組み立てる**: `_call_units` は `out/units.json` が無いときだけ `unit_plans` を打つ。`unit_plans` はプランと指示のファイルを書いた後、最後に `units.json` を一時ファイルからの `os.replace` で書く（`units.json` があることが組み立てを終えた印。途中で止まった組み立ては、次の起動で `out` を消してからやり直す）
- **再開は保存した組を読む**: `units.json` があれば `unit_plans` を打たず、`units.json` とそこから指すプラン・指示のファイルを読み、`orders` と `<seq>.json` をそのまま続きに使う（`base_sha` を初期化しない）。`units.json` には組み立てに使った入力の指紋 `source`（`issues`・`omitted` と、読んだ計画・単位のファイル・設計文書のパスごとの、スプリントの HEAD での blob の SHA）を書き、再開のたびに今のスプリントの作業場所で同じ指紋を取り直して照らす
- **再計画で状態を引き継ぐ条件**: 指紋が違う（承認をやり直して計画が変わった）とき、状態が 1 つも無い（どの `orders.<k>.base_sha` も null で、`work_dir/units/` に記録が無い）ならそのまま `out` を消して組み立て直す。状態が 1 つでもあれば引き継がずに `precondition`（「単位の計画が変わったが、実行の記録 <パス> がある」）で止め、人に返す。`<seq>.json` は再採番した別の単位に当てない（`seq` は計画の組の中でだけ意味を持つ）

```json
{"version": 1, "sprint": "sprint-3702", "remote": "origin", "max": 3, "merge_method": "merge",
 "queue": {"approved": false, "sprint": "sprint-3702", "target_sha256": "<記録の SHA-256>", "answer_sha256": "<回答の SHA-256>"},
 "source": {"issues": [3702], "omitted": [], "queue": {"approved": false, "sprint": "sprint-3702", "target_sha256": "<記録の SHA-256>", "answer_sha256": "<回答の SHA-256>"}, "files": {"issues/issue-3702-plan.units.json": "<blob の SHA>"}},
 "units": [{"seq": 1, "name": "契約と退行テスト", "file": "issues/issue-3702-plan.units.json", "order": 1, "merge": 1,
            "plan": "sprint-3702-u1.json", "unit_file": "sprint-3702-u1.unit.json", "branch": "sprint-3702-u1", "after": []}],
 "orders": {"1": {"base_sha": null}}}
```

- `max` は組み立ての時点の `parallel_cap`、`merge_method` は宛先（スプリントのブランチ）について `route.declared_merge_method` で決めた方式（下の「マージの方式の決め方」。単位のマージに `--method` で渡す）。`queue` は照合済みの承認から決めた単位のキューの承認（下の「単位のキューの承認の受け渡し」。単位のマージに `--queue-approved` を付けるかの唯一の根拠）。`orders.<k>.base_sha` は実行順 k を始めたときに固定した SHA（下の 2。始める前は null）
- 単位ごとの PR・HEAD・状態は `work_dir/units/<seq>.json`（下の 4）に分けて置き、`units.json` は組み立てと実行順の開始のときだけ書く
- 形は `contracts/workflow/units-run.schema.json`（新設。Task 8）で確かめ、読めなければ `stopped`（`invalid`）

### 工程の流し方（`progress/invoke.py`）

満たす決定: 1・3・7。満たす受け入れ条件: 3・4。

`per_unit` の工程（`implement-units`）の `ndf.plan` のステップは、`_call_plan` の中で `_call_units(run, cwd, work_dir, runner)` に分ける。`per_unit` でない工程は今のまま。

1. **組み立て**: `units_files.unit_plans` に `values_of(unit)` を渡す。`values_of` は、単位の値で写した入力（`issues` を代表の課題 1 つ、`worktree` を単位の作業場所、`branch` を単位のブランチ、`base` をスプリントのブランチ）の `StageRun` を作り、それで `template_values` を打つ。したがって題・本文は代表の課題について既存の `_issue_values` の信頼の確かめとキャッシュ（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE`）を通り、既定の `review` は単位のブランチで組まれ、設定の `workflow.launch.review`・`test.ci.only` もそのまま効く。スプリントの課題が複数でも、単位ごとに課題 1 つの入力になるので題・本文が欠けない
2. **作業場所**: 実行順 k を始めるときに `git fetch <remote> <sprint>` を**1 回だけ**打ち、`git rev-parse <remote>/<sprint>` の SHA を `units.json` の `orders.<k>.base_sha` に書いて固定する（既に書いてあれば取得も書き換えもしない。再開で起点が動かない）。その実行順の各単位について `ndf worktree create <sprint>-u<seq> --from <base_sha> --no-fetch` で、前の実行順のマージをすべて含む同じコミットから単位専用のブランチと作業場所を用意する（既にあれば作り直さない）。今の `ndf worktree create` は `--from` があっても既定のリモートを取得するので、`--no-fetch`（取得を飛ばし、`--from` の SHA が手元に無ければ `precondition` で止める）を足す。取得は実行順ごとに 1 回で、単位の本数に比例して増えない。単位の PR の宛先はスプリントのブランチ（差し込み `base`）
3. **キュー**: `ndf plan queue <その実行順のプランのパス>... --max <units.json の max> --resume` を打つ（`--resume` は常に付ける。初回はキューの記録が無いので全部流し、打ち直しでは完了したプランを流さずに続ける。今の `ndf plan queue` は `--resume` が無いと完了済みの記録を使わない）。`lane_plans` はスプリントがレーンで動くとき、そのレーンの `max_plans`（並列の計画の `max_plans`、省けば既定）。supervisor が手で流すときは `ndf progress units run` が同じ値を使う
4. **マージの前の記録**: キューが `ok` で終われば、その実行順の単位ごとに、**最初のマージの要求より前に** `work_dir/units/<seq>.json` へ PR の番号（`gh pr view <単位のブランチ> --json number,baseRefName` で 1 回だけ読む）・宛先（スプリントのブランチ）・レビュー済みの HEAD（そのプランの状態ディレクトリの値）・書いた時刻 `recorded_at`（ISO 8601。`ci merge --since` に渡し、これより後の再オープン・宛先の変更を見つける）・状態 `queued` を書く。記録を書けなければマージを要求しない
5. **マージ**: 記録のある単位をマージの順に、**単位の作業場所を cwd にして** `ndf ci merge --branch <単位のブランチ> --pr <記録の番号> --base <スプリントのブランチ> --reviewed <記録の HEAD> --method <units.json の merge_method> --since <記録の recorded_at>`（`units.json` の `queue.approved` が真で `queue.sprint` が `sprint` と同じときだけ `--queue-approved` を足す）を打つ。返りで記録の状態を書き換える（`ok` は `merged`、マージキューへ入れた・入っている `ci_pending` は `queued` のまま）。判定は下の「マージの対象の照合と再開」が `ci merge` の中で持つので、工程の側で `gh pr view` を打ち直さない
6. **再開と再レビュー**: 記録の状態は `queued`（マージ待ち）・`merged`・`stale`（レビューからやり直す）・`blocked`（人に返す）の 4 つで、遷移は次のとおり。打ち直しは常にこの表から続ける。`stale` の記録は理由 `reason`（`refused` か `base_conflict`）・開始点 `start`（`review` か `conflict-check`）・再レビューの回数 `restarts` を持ち、起動をまたいで残る

   | 今の状態と `ci merge` の返り | 次の状態と工程の振る舞い |
   | --- | --- |
   | 記録なし（4 の前に止まった） | 3 のキュー（`--resume`）の結果から始める |
   | `merged` | API を呼ばずに飛ばす |
   | `queued` → `ok` | `merged` |
   | `queued` → `ci_pending` | `queued` のまま。次の実行順を始めずに `ci_pending` で返す |
   | `queued` → `refused`（HEAD がレビュー済みと違う・承認後の再オープン）か `base_conflict` | `restarts` が 0 なら `stale`（`reason`・`start`・`ci merge` の返りの `instance` を足し、`restarts` を 1 にする）を**先に書いてから**、同じ起動で下の `stale` の行を行う。`restarts` が 1 以上なら `blocked`（再レビューの後もまた断られた） |
   | `stale`（同じ起動で書いた直後か、前の起動で再レビューが中断・失敗した） | その単位のプランだけを `ndf plan queue <そのプラン> --resume --from <プラン>=<記録の start>` で打つ。開始点は `refused` なら `review`（今の HEAD をレビューし直す）、`base_conflict` なら `conflict-check`（マージ先の取り込みからレビューまで）。`--from` を付けるので、キューの記録に残る完了済みの古いレビューを使わない（3 の通常のキューの `--resume` では `stale` の単位のプランを渡さない）。キューが `ok` で終われば、4 と同じく PR を読み直し、プランの状態ディレクトリの新しいレビュー済みの HEAD と新しい `recorded_at` で記録を `queued` に**書き換えて**から 5 を打つ（`restarts` は残す）。キューが失敗・中断すれば記録は `stale` のままで、次の起動もこの行から同じ開始点で続ける。古い HEAD のまま同じ `ci merge` を繰り返さない |
   | `queued` → `refused`（番号・ブランチ・宛先の違い、承認外の宛先へのマージ） | `blocked`。工程は `precondition` で止め、次の実行順へ進まず人に返す（マージ済みでも成功にしない） |
   | `blocked` | 人が記録を消すか直した後の打ち直しまで `precondition` を返し続ける（`ndf progress units run` は理由と記録のパスを出す） |

7. **次の実行順と最後**: その実行順のすべての記録が `merged` になってから 2 へ戻る。最後の実行順まで済めば、スプリントの worktree（工程の cwd）で `git fetch <remote> <sprint>` と `git merge --ff-only <remote>/<sprint>` を打ち、ローカルの HEAD をマージ済みの先端まで進めてから工程の残りのステップ（`gathered`。`ndf progress gathered` は `<remote>/<sprint>` がローカルの HEAD の祖先であることを求める）へ進む。早送りできない（スプリントの worktree に push していないコミットがある）なら止める（`precondition`、理由「スプリントの worktree が <remote>/<sprint> から分かれている」）

| 関数 | 変える前 → 後 | 異常系 |
| --- | --- | --- |
| `_call_plan(run, cwd, work_dir, answer, runner)`（責務の変更） | 前: `plan_path` で 1 本書き `ndf plan next` を打つ。後: 工程が `per_unit` なら `_call_units` へ渡す | — |
| `_call_units(run, cwd, work_dir, runner)`（新設） | 上の 1〜7（2〜7 は `run_units(plans, sprint, remote, cwd, work_dir, runner)` に分け、`ndf progress units run` も同じ関数を呼ぶ） | `UnitsError`・`TemplateValuesMissing` は工程の失敗（`cause.kind: invalid`、理由をそのまま）。queue とマージの失敗は今の `_from_process` のまま返し、次の実行順を始めない |
| `plan_queue_argv(paths, cap, cwd, start: Optional[tuple[str, str]] = None) -> list[str]`（新設） | `ndf_command` と同じ形で `plan queue … --max <cap> --resume`（`start` があれば `--from <プラン>=<ステップ>` も）の argv | 投げない |
| `unit_merge_argv(branch, number, base, reviewed, method, since, queue_approved: bool) -> list[str]`（新設） | `ndf_command` と同じ形で `ci merge --branch <branch> --pr <number> --base <base> --reviewed <reviewed> --method <method> --since <since>`（`queue_approved` が真なら `--queue-approved`）の argv。打つ cwd は呼び手が単位の作業場所にする | 投げない |
| `_call_plan` の環境（責務の変更） | 前: `ndf.plan` の `ndf plan next` に工程の入力を渡さない（`NDF_STAGE_INPUT` は `ndf.script` だけ）。後: `ndf.script` と同じく `run.env({INPUT_ENV: json.dumps(run.inputs)})` を渡す。プランのステップの子（`ndf plan units check --from-stage` など）は環境を引き継ぐ | — |

#### worker への単位の指示（`impl.json`）

今の `worker` は設定で与える任意の argv（`params.json` の `worker`。既定なし）で、テンプレートは末尾に作業名 `impl` を足して打つだけである。`ndf answer work` は `<state_dir>/impl-prompt.md` を読むが、そのファイルを書く共通の処理は無い。そこで、プロンプトの生成元と、任意の worker へ担当範囲を渡す契約を次のとおり定める。

- **生成元**: `ndf plan units prompt --out <プロンプト> [--unit <指示のファイル>] --issue <課題> --body <本文のファイル> --title <題> [--acceptance …]`（`core/plan/cli.py`。Task 8）が `impl` のプロンプトの全文を書く（追記でなく上書き。打ち直しでも同じ全文になる）。全文は「課題」（題・本文）と「担当の範囲」の 2 節からなる。「担当の範囲」は次の 2 通りで、文を分ける
  - `--unit` あり（単位）: 指示のファイルの担当のタスク（`id`・`title`・`files`・`criteria`）・実装計画の文書のパス・「上のタスクだけを実装し、ほかのタスクに手を付けない」
  - `--unit` なし（`single` の単位と、`unit_file` を渡さない従来の工程）: 「この課題の受け入れ条件のすべてを実装する」と、`acceptance` の課題と条件の一覧。タスクの一覧を書かず、「ファイル内のタスクだけ」の文も書かない
- **`single` の単位**: `unit_plans` の 3 は `single` の単位に指示のファイルを書かず、差し込み `unit_file` を空にする（指示のファイルにタスクの無い単位を作らない）。範囲は上の `--unit` なしの文になる
- **ステップ**: `params.json` に `unit_file`（「単位の指示のファイル。空なら課題の全体を実装する」、`"default": ""`）を足す。`impl.json` の `worker` の前にステップ `prompt` を足し、空かどうかで引数を分けて上のコマンドを打つ。argv は `["sh", "-c", "out=$0 unit=$1 issue=$2 title=$3 body=$4; shift 4; exec ndf plan units prompt --out \"$out\" ${unit:+--unit \"$unit\"} --issue \"$issue\" --title \"$title\" --body \"$body\" \"$@\"", "{state_dir}/impl-prompt.md", "{{unit_file}}", "{{issue}}", "{{title}}", "{{body_file}}", "{{acceptance}}"]` とする。固定の 4 引数（`unit_file`・課題・題・本文のファイル）を変数へ取り出してから `shift 4` し、残った `"$@"` には `{{acceptance}}`（`list: true` なので `--issue N --criteria N:M …` の要素に展開される）だけが残る。`unit_file` が空でも固定の 4 つは位置で数えるので、空の文字列は `shift 4` で落ち、`--unit` も付かない（`shift` を省くと、固定の値と空の文字列が `"$@"` に重なって渡る）。引数の位置は実装で決めてよいが、`unit_file` が空のときに `--unit` を渡さないことと、`"$@"` に受け入れ条件の引数だけを残すことは守る。従来の工程（`light`・`operation`・`documentation` の `implement.json`）と手動の `ndf route render impl` も同じプロンプトを得る
- **worker への契約**: `worker` のステップを `["sh", "-c", "NDF_WORK_PROMPT=\"$0\" exec \"$@\"", "{state_dir}/impl-prompt.md", "{{worker}}", "impl"]` にし、環境変数 `NDF_WORK_PROMPT` にプロンプトのパスを渡す（argv の末尾は今のまま作業名だけなので、既存の worker の引数の受け方を変えない）。`params.json` の `worker` の説明に「作業名 `impl` の worker は `NDF_WORK_PROMPT` のファイルを作業の指示として読み、その『担当の範囲』の外を実装しない」を契約として書く。`ndf answer work` は今のまま `<state_dir>/impl-prompt.md`（同じファイル）を読むので変えない。契約に従わない worker を設定したプロジェクトでは範囲を守れないが、それは設定の誤りとして手順書（Task 9 の `agent-layers.md`）に書く

`stages.py` の工程の型と `definition.py:321` の読み手に `per_unit` を足す（既定は偽）。`per_issue` は改名せずに残す（`ndf.schema.json` の既存のキーと、保存済みのスナップショットの `per_issue` を今のまま読めるようにするため。`_stage_from` はキーをそのまま `StageDef` に渡すので、`per_unit` を持たない古いスナップショットは既定の偽で読める）。

### マージの対象の照合と再開（`ndf ci merge --pr --base`）

満たす決定: 1・7。設計 PR（`design-land`）と単位の PR（工程「実装」）の両方が使う。今の `ci merge --branch` はブランチの開いた PR を引き直してその最初の PR をマージし、マージ済みなら開いた PR が無いので止まる。承認・レビューした PR と違う PR をマージしない、マージキューの後に打ち直せる、の 2 つのため `--pr <番号>`（承認・記録した PR）と `--base <宛先>` を足す。

**引数の組み合わせ**: 経路は 2 つだけで、どちらにも当たらない呼び出しは PR を読まずに `stopped`（`invalid`、「--pr・--base・--reviewed・--method・--since はそろえて渡す」）で止める。

| 経路 | 受け付ける引数 | 振る舞い |
| --- | --- | --- |
| 照合の経路 | `--branch`・`--pr`・`--base`・`--reviewed`・`--method`・`--since` のすべて（任意で `--queue-approved`）。または `--target <ファイル> --answer <回答の記録>` だけ（設計の承認の記録と、それに対する回答。同じ値と `queue.design` をそのファイルから読む。下の「回答との照合」） | 下の表で判定する |
| 従来の経路 | `--branch`（`--reviewed`・`--method` は任意）。`--pr`・`--base`・`--since`・`--target` を渡さない | 今のまま（ブランチの開いた PR を引いてマージする） |
| 止める | 照合の経路の値が 1 つでも欠ける、`--target` と `--answer` の片方だけを渡す、または `--target` とほかの照合の値を併せて渡す | `invalid`。宛先・方式を照らさずにマージする呼び出しを作らない |

`--method` は下の「マージの方式の決め方」で決めた方式である（単位は `units.json` の `merge_method`、設計 PR は承認の記録の `method`）。

**マージの方式の決め方**（`core/route/__init__.py` の `declared_merge_method(repo, base, remote) -> str`。新設。単位と設計 PR の両経路がこれだけを使う）: 今の `route.default_merge_method` は宛先で許された方式の先頭を選び、プロジェクトの宣言を読まないので、照合の経路では使わない。

宣言の `branch_strategy.merge` には 2 つの値があり、役割が違う。**方式**は `merge.commit`（`merge`・`squash`・`rebase`。`BranchStrategy.merge_commit`。`allowed_merge_methods`・`pick_merge_method` が扱う値と同じ）で、`declared_merge_method` はこれだけを読む。**経路**は `merge.method`（`merge-queue`・`strict`・`direct`。`BranchStrategy.merge_method`）で、本番の PR の照合（`domains/release/land.py` の `check_production_pr`）が使う値である。`merge.method` を `pick_merge_method` に渡さない（本リポジトリの `merge-queue` は方式の値ではないので、渡すと設計の承認の記録も単位の計画も作れない）。設計 PR と単位の PR の宛先（スプリントか、`doc_prs: base` のベース）が本番のブランチと同じとは限らないので、この 2 つの経路はキューを使うかを `merge.method` から推さず、宛先が実際にキューを持つか（`GitHub.merge_queue(branch)`。下の「要求の時点の宛先」）で決める。

| 場合 | 決める方式 |
| --- | --- |
| 宣言 `branch_strategy.merge.commit` があり、宛先（`allowed_merge_methods(base)`。ruleset とリポジトリの設定）で許される | 宣言の方式（`pick_merge_method(宣言, allowed)`） |
| 宣言があり、宛先で許されない | 決めずに `RouteError`（「宣言の方式 <方式> は <宛先> で許されない」）。単位は `unit_plans` が `UnitsError` で止め、設計 PR は `design target` が `stopped`（`invalid`）を返して記録を書かない。許された方式へ黙って替えない |
| 宣言が無い | 宛先で許された方式の先頭（今の `default_merge_method` と同じ） |
| 宛先の許可を読めない（git・GitHub の失敗） | 決めずに `RouteError`（`precondition`）。None のまま記録に書かない |

宛先がマージキューを持つときも同じ値を記録に書く。キューへの登録では方式をキューの設定（`mergeQueue(branch).configuration.mergeMethod`）が決めるので、**キューの実際の方式を記録の方式と照らす**: `design target` は記録を書く前に、キューを持つ宛先ごとにその方式（`MERGE`・`SQUASH`・`REBASE` を小文字にした値）が `declared_merge_method` の値と同じかを確かめ、違えば記録を書かずに `stopped`（`invalid`、「<宛先> のキューの方式 <方式> が宣言 merge.commit の <方式> と違う」）で止める。承認資料に出す方式と実際にマージされる方式が食い違う記録は作らない。`ci merge` も登録の直前に、同じ読み（下の「キューと事象の読み」。今の `_MERGE_QUEUE_STATE` が既に `mergeMethod` を読む）のキューの方式を `--method` と照らし、違えば登録を送らずに `refused`（承認の後にキューの設定を変えた）。既存のマージの部品（`parts/steps.json` の `merge`）と差し込み `merge_method` の既定は変えない（この変更の外）。

**判定**（照合の経路。作業場所の HEAD と `--reviewed` の確かめは今のまま先に行う）: 読む順は **① PR を `GitHub.pull(番号)` で 1 回読む → ② `OPEN` か `MERGED` なら「キューと事象の読み」を 1 回 → ③ キューを持たない宛先へ直接マージするときだけ、方式の読み 2 → PR の読み直し 1 → 要求** に固定する。判定は上の行から順に当て、最初に当たった行で返す。マージ済みかどうかより先に head のブランチ・HEAD を照らす（今の `_preflight` の「マージ済みなら成功」を照合の経路では使わない）。

**キューから外す（`GitHub.dequeue`）のは、この実行が登録したと確かめられる PR だけ**とする: head のブランチが `--branch`、`headRefOid` が `--reviewed` と同じ（承認・レビューした PR そのもの）で、キューの登録（`mergeQueueEntry`）の `enqueuer` が今の認証の利用者（`viewer`）、`enqueuedAt` が `--since` より後のとき。番号の渡し間違い（ブランチか HEAD が違う）では何も外さず、ほかの人が登録した PR も外さない（`refused` の理由に「#<番号> はキューにいるが、この実行の登録ではないので外していない」を足し、人に返す）。

| PR の状態 | 返り | 読む API |
| --- | --- | --- |
| ① head のブランチが `--branch` と違う | `refused`（「#<番号> は承認した対象（<ブランチ> → <宛先>）と違う」）。`MERGED` でも同じ。キューから外さない（番号の渡し間違いで別の作業の PR を外さない） | ① だけ |
| ① `headRefOid` が `--reviewed` と違う（`MERGED`・`OPEN` のどちらも） | `refused`。外さない（HEAD が変わった PR は GitHub がキューから外す） | ① だけ |
| ① `CLOSED`（マージされていない） | `refused` | ① だけ |
| ① `OPEN` で、本文に決めていない「マージの前に決める」残リスクの行（`- [ ]`）が残る | `refused`（今の `_preflight` と同じ判定と理由。残った行を出す）。要求を送らない | ① だけ |
| ① `OPEN` で `mergeStateStatus` が `dirty` | `stopped`（`base_conflict`、要求を送らない）。衝突の判定もこの読み取りで行う（下の `design-land`） | ① だけ |
| ② 宛先（`baseRefName`）が `--base` と違う、または `--since` より後に再オープン・宛先の変更の事象がある（`OPEN`・`MERGED` のどちらも） | `refused`（「#<番号> は承認の後に開き直された／宛先を変えられた」、`MERGED` なら「#<番号> が承認外の宛先 <宛先> へマージされた、または承認の後に宛先を変えてマージされた」）。`OPEN` でキューにいれば、上の条件で外してから返す | ①②（外すときは要求 1） |
| ② `MERGED` で、宛先がキューを持ち、`--queue-approved` が無い（`--target` では記録の `queue.design` が偽） | `refused`（「承認していないキューの経路でマージされた」）。人に返す | ①② |
| ② `MERGED` | `ok`（「既にマージ済み」。マージキューが済ませた後の打ち直し）。宛先の変更の事象を読んだうえでの成功なので、宛先を変えてマージした後に宛先の名前が戻って見えても成功にしない | ①② |
| ② `OPEN` で、宛先がキューを持ち、`--queue-approved` が無い | `refused`（下の「要求の時点の宛先」。登録を送らない）。**既にキューにいても待たずに**ここで返す（例外の承認の無い登録を `ci_pending` で待ってマージ済みを成功にしない）。この実行の登録なら外す | ①②（外すときは要求 1） |
| ② `OPEN` で、宛先がキューを持ち、キューの方式（`mergeMethod`）が `--method` と違う | `refused`（「<宛先> のキューの方式 <方式> が承認した <方式> と違う」）。この実行の登録なら外す | ①②（外すときは要求 1） |
| ② `OPEN` で、キューに入っている | `ci_pending`（マージの要求を打ち直さない） | ①② |
| ② `OPEN` で、宛先がキューを持ち、入っていない（入っていない・外された） | ① の `pull` の HEAD で `enqueuePullRequest`（`expectedHeadOid`）を送り `ci_pending`。方式はキューが決め、上の行で `--method` と同じことを確かめ済み | ①②・登録の要求 1 |
| ② `OPEN` で、宛先がキューを持たない | ③ 方式を `allowed_merge_methods(--base)` で読み、`pick_merge_method(--method, allowed)` が None なら要求を送らずに `refused`（今の `_method_refusal`。宣言と違う方式でマージしない）。合えば PR を読み直し（`pull2`）、`pull2` を ① の行と宛先の行（ブランチ・HEAD・状態・残リスク・衝突・`baseRefName`）で照らし直して、当たればその行の返り（このときは外さない。キューを持たない）。通れば `pull2` の HEAD で直接マージを要求し、成功したら PR を 1 回読み直して宛先を照らす（下の「要求の時点の宛先」） | ①②・方式の読み 2・読み直し 1・要求 1・要求の後の読み直し 1 |

**キューと事象の読み**: 今の `_MERGE_QUEUE_STATE` の GraphQL（`isMergeQueueEnabled`・`isInMergeQueue`・`mergeQueue(branch).configuration.mergeMethod`）に、`timelineItems(since: --since, itemTypes: [REOPENED_EVENT, BASE_REF_CHANGED_EVENT])` の件数・`mergeQueueEntry { enqueuedAt enqueuer { login } }`・`viewer { login }` を足した 1 本の問い合わせ（`GitHub.merge_state(pull, since) -> MergeState`）にする。再オープン・宛先の変更・キューの状態と方式・登録した人を 1 回で読み、照合の経路の `OPEN` と `MERGED` の行はどれもこの読みを 1 回だけ使う。閉じて開き直した PR は、番号・ブランチ・宛先・HEAD が同じでも `REOPENED_EVENT` で止まる（承認の後の状態の変更を照らす情報はこの事象で、承認の記録の時刻 `--since` より後のものだけを数える）。マージ済みの PR も宛先の変更の事象を数えるので、承認の後に宛先を変えてマージされたものを、今見える `baseRefName` が `--base` と同じでも成功にしない。

**同じ読み取りを使う責務**（`adapters/github.py`）: `GitHub.merge_approved(pull: Pull, state: MergeState, base: str, reviewed: str, method: str) -> MergeOutcome` を足す。`ci_cli._merge` は照合の経路で ① を読んで上の表の ① の行を判定し、`OPEN` か `MERGED` のときだけ ② を 1 回読んで ② の行を判定し、要求するときに ① の `pull` と `state` を `merge_approved` へ渡す。`merge_approved` は、キューがあれば ① の `pull` で登録し、無ければ ③（方式の読み → `pull2` の読み直しと照らし直し → `_merge_pull(pull2, expect_head, method, route)`）を打つ。`_merge_pull` は `_merge_with` の本体を `pull` を受け取る形に切り出したもの（`_merge_with` は `self.pull(number)` の後に `_merge_pull` を呼ぶ形にして、従来の経路の振る舞いを変えない）。方式の読みはキューを持たない宛先へ直接マージするときだけで、マージ済み・待機中・衝突・照合の失敗の早期の返りでは読まない。**`pull2` の読みと要求の間に別の API 呼び出しを挟まない**（方式の読みと ② は `pull2` より前に済ませる）。要求が断られて PR を読み直すとき（今の `after = self.pull(number)`）は、読み直した PR を上の表の ① の行と宛先で照らし直してから成否を決める: 宛先か head のブランチか HEAD が違えば `refused`、`MERGED` で 3 つが同じときだけ `ok`、ほかは今の `refusal_kind` の種類

**要求の時点の宛先**: GitHub の直接マージ（REST の `PUT /pulls/{n}/merge` の `sha`）とキュー登録（`enqueuePullRequest` の `expectedHeadOid`）は HEAD を条件にできるが、宛先を条件にする引数を持たない。GitHub の API で宛先を要求の時点まで完全に固定する手段は無い。宛先の変更は PR の書き手か書き込み権限を持つ人だけができ、NDF の工程は記録済みの PR の宛先を変える操作を持たない（`gh pr edit --base` を打つステップを置かない。`test_sprint_templates.py` で全テンプレートの argv に無いことを縛る）。したがって宛先を変えられるのは、書き込み権限を持つ人のうっかりの操作だけである。そこで**自動の要求を送ってよい条件**を経路ごとに次のとおり決め、条件を満たさない要求は送らない

| 経路 | 自動の要求を送ってよい条件 | 条件を満たさないときと、要求の後に見つけた違い |
| --- | --- | --- |
| 直接マージ（宛先にキューが無い） | **仕様で許容する例外**（承認は要らない）。宛先を照らす実際の順は、① の読みで照らす → ② で宛先の変更の事象を照らす → 方式の読み 2 → `pull2` の読みで照らし直す → 要求、である。最後に宛先を照らす `pull2` の読みと要求の間には API 呼び出しを挟まないので、変えられる窓は `pull2` の読みから要求までの 1 往復（秒の単位）の中だけになる（① から要求までの間の変更は `pull2` で見つける）。この窓の中のうっかりの変更は脅威の想定の外として許容する。自動の要求は、`pull2` が ① の行と宛先の行をすべて通ったときだけ送る | 要求の成功の後に PR を 1 回読み直し、`baseRefName` が `--base` と違えば `refused`（「#<番号> が承認外の宛先 <宛先> へマージされた」）。後の打ち直しでは、マージ済みの PR を ② の宛先の変更の事象でも照らす（上の表）。マージは戻せないので、工程は `precondition` で止め（単位なら記録を `blocked`）、次の実行順・スプリントの worktree の早送り・`gathered` へ進まず、戻すかどうかを人に返す（自動で revert しない） |
| マージキュー | **承認した例外のときだけ**: 登録からキューがマージするまでの間（分の単位）は宛先の変更を見張る手段が再開の照合しかないので、承認者がこの例外を見て承認したことを要求の前に確かめる。`design target` は記録を書くときに設計 PR の宛先とスプリントのブランチ（単位の宛先）がキューを持つかとその方式を読み（`GitHub.merge_queue(branch) -> Optional[QueueConfig]`。今の `_MERGE_QUEUE_STATE` の `mergeQueue(branch)` の部分だけの問い合わせ。**異なる宛先ごとに 1 回**で、`doc_prs: sprint` のように設計 PR の宛先とスプリントのブランチが同じなら 1 回の読みを両方に使う）、記録に `queue: {"design": 真偽, "units": 真偽}` と単位の宛先 `sprint`（スプリントのブランチ）を書く。承認資料は真の宛先ごとに「宛先がキューを持つので、登録の後の宛先の変更は再開の照合で見つける」を載せ、回答の事象は記録の SHA-256 と組なので、この例外の承認も同じ照合で確かめられる。`ci merge` の照合の経路は `--queue-approved` が無ければ登録しない。`--target` は記録の `queue.design` が真のときだけ登録を許し、単位は下の「単位のキューの承認の受け渡し」で照合済みの根拠から `--queue-approved` を決める | 宛先がキューを持つのに例外の承認が無い（承認の後にキューを有効にした、を含む）なら、登録を送らずに `refused`（「宛先 <宛先> がキューを持つが、承認でその例外を認めていない」）。既にキューにいても待たずに同じく返す（上の表）。設計 PR は `stale_approval` で承認を取り直し、単位は `blocked` で人に返す。登録の後は、`ci_pending` で返った工程の打ち直しのたびに ① と ② の行で照らし、違えばこの実行の登録なら `dequeue` して `refused`。キューがマージした後に見つけた違いも `refused` で人に返す。承認者が直接マージで流したいときは宛先のキューを外す判断を人に委ねる |

**API の回数**（照合の経路。PR の読み 1 を含む）: ブランチ・HEAD・閉じた・残リスク・衝突の失敗 1、マージ済みの打ち直し 2（PR・キューと事象。宛先の変更の事象を読むため）、宛先・事象・例外の承認・キューの方式の失敗 2（この実行の登録を外すときは 3）、キューの待ち 2（PR・キューと事象）、キューへの登録 3（PR・キューと事象・登録）、直接マージ 7（PR・キューと事象・方式 2・要求の直前の読み直し・要求・要求の後の読み直し）。今の従来の経路の直接マージ 4（PR・方式 2・要求）より 3 回多い（キューと事象の読み、要求の直前の読み直し、要求の後の読み直し）。要求の直前の読み直しは、宛先を最後に照らす読みと要求の間を 1 往復にするための 1 回で、方式の読みを ① より前へ移して省くと、マージ済み・待機中・照合の失敗でも方式を読む（2 回増える）ので採らない。偽の GitHub で呼び出しを数え、返りごとにこの回数をテストで縛る（マージ済み・待機中で方式を読まないこと、`pull2` と要求の間に呼び出しが無いことを含む）。

**単位のキューの承認の受け渡し**（工程と手動の両方）: 単位のマージに `--queue-approved` を付けてよいかは、照合済みの承認の回答と単位の宛先（スプリントのブランチ）を結び付けた 1 つの値 `units_queue` から決める。承認の回答の事象には `queue` を載せず、`design-answer.json` の場所（世代ごとの `stage_dir`）を工程「実装」が探すこともしない。

1. **作る**: `progress/gate` に `units_queue(target: Path, answer: Path, sprint: str) -> UnitsQueue` を置く。中で `ci merge --target --answer` と同じ「回答との照合」の関数を打ち（通らなければ `precondition`。設計 PR のマージの後に打つので開き直さない）、記録の `sprint` が引数の `sprint` と同じかを確かめ（違えば `invalid`）、`{"approved": 記録の queue.units, "sprint", "target_sha256", "answer_sha256"}` を返す
2. **工程**: `design-sync`（`ndf progress design sync --target … --answer …`）が取り込みと照合の後にこれを打ち、出力 `units_queue` を出す。工程「ドキュメントレビュー」の `outputs` に `units_queue`、`context.schema.json` に同じキー、工程「実装」（`implement-units.json`）の `inputs` に `units_queue` を足す。`_call_units` は `units_queue` を `unit_plans` へ渡す（`UnitGiven.queue`）
3. **手動**: `ndf progress units plans` に `--target <design-target.json> --answer <design-answer.json>`（2 つそろえて渡す。片方だけは `invalid`）を足し、同じ `units_queue` を打つ。`--from-stage` では文脈の `units_queue` を使う。どちらも無ければ `{"approved": false, "sprint": <--sprint>}` とし、キューを持つスプリントでは単位が `refused` → `blocked` になる（例外を承認していないのと同じ扱い。黙って付けない）
4. **保存と再開**: `unit_plans` は `units_queue` を `units.json` の `queue` に書く（`queue.sprint` が `units.json` の `sprint` と違えば何も書かずに `UnitsError`）。`queue` は指紋 `source` にも入れ、承認を取り直して値が変わったら上の「再計画で状態を引き継ぐ条件」に従う。`run_units`（工程の `_call_units` と手動の `units run` の共通の関数）は、`units.json` の `queue.approved` が真で `queue.sprint` が `units.json` の `sprint` と同じときだけ、単位の `ci merge` に `--queue-approved` を付ける。再開はいつも `units.json` から読むので、工程でも手動でも同じ根拠から同じ値になる

### 設計 PR の承認とマージ（`design-review`・`design-land`）

満たす決定: 1。満たす受け入れ条件: 1。工程「ドキュメントレビュー」（`design-approval`）のステップの中身である。

#### ブランチと作業場所

今の工程「作業場所の用意」が出すのは `worktree` と `branch` の 1 組（スプリントのブランチとその作業場所）で、後の工程もその作業場所で書く。設計 PR をスプリントへ入れるには、head と宛先を別のブランチにする必要があるので、次の 2 組に分ける。

| 組 | ブランチ | 作業場所 | 作る工程 | 使う工程 |
| --- | --- | --- | --- | --- |
| スプリント | `branch`（今のまま） | `worktree`（今のまま） | 作業場所の用意 | 要求・実装（単位の作業場所の起点と、最後の早送り）・その後の工程。設計の承認の後にここで承認済みのファイルを読む |
| 設計 | `design_branch` = `<branch>-design` | `design_worktree` = `.worktrees/<branch>-design` | 設計（`design-draft` の最初のステップが `ndf worktree create <branch>-design --from <remote>/<design_base>` で作る。既にあれば作り直さない） | 設計・詳細設計・計画・ドキュメント再構成・ドキュメントレビュー。設計 PR の head |

- 設計 PR の宛先 `design_base` は**宣言から決める**: `branch_strategy.pr_target("doc", sprint_branch=branch)`。`branch_strategy.sprint.doc_prs` が `sprint` ならスプリントのブランチ、`base` ならベースブランチ。`design-draft` はこの値を `base` として明示して PR を開き（`route.derived` の既定の `pr_target("sprint")` に任せない）、`design_base` を出力する。`design-land` の `--base` も同じ `design_base` を使うので、PR を開いた宛先と照らす宛先がずれない
- **設計ブランチの起点は宛先**: 設計ブランチは `<remote>/<design_base>` から切る（`doc_prs: sprint` ならスプリントの先端で今と同じ、`base` ならベースブランチの先端）。ベースに未マージのスプリント側のコミットは設計 PR の差分に入らず、設計承認でベースへ公開されない。`doc_prs: base` のとき、スプリント側の変更のうち設計 PR に入れるのは、工程「要求と受け入れ条件」が書いたスプリントの課題の要求仕様 `issues/issue-<番号>-requirements.md`（設計の worker が読み、承認者が設計と並べて見る）だけとする。`design-draft` は作業場所を作った直後に `git checkout <remote>/<branch> -- <そのパス>...`（スプリントのブランチにあるものだけ）で取り込み、1 つのコミット（「要求仕様をスプリントから取り込む」）にする。ほかのスプリント側の変更（コード・設定・ほかの文書）は入れない。`doc_prs: sprint` では起点がスプリントの先端なので取り込みは打たない
- 工程「詳細設計」「計画」「ドキュメント再構成」「ドキュメントレビュー」は、設計の作業場所で書く**新しい経路の工程の定義**（下の「工程の定義」の `design-detail-pr.json`・`plan-pr.json`・`document-rebuild-pr.json` と `design-approval.json`）だけが `worktree_input: "design_worktree"`（`ndf.schema.json` に足す。省けば今のまま `worktree`）を持ち、テンプレートの `worktree`・`branch` を設計の組から組む。`light`・`operation`・`documentation` が指す共有の `design-detail.json`・`plan.json`・`document-rebuild.json` は変えないので、`design_worktree` を出力しない従来の経路は今の `worktree` と出力契約のまま通る。`worktree_input` の文脈のキーが文脈に無ければ、工程は `worktree` へ黙って落とさず `invalid`（「工程 <名前> の作業場所 <キー> が文脈に無い」）で止める
- **マージの後に承認済みのファイルをスプリント側へ揃える**（ステップ `design-sync`。`ndf progress design sync`）: スプリントの作業場所で次の順に打つ。工程「実装」はスプリントの作業場所の `collect` でこのファイルを読む
  1. 取り込みの前の HEAD を控え、`git fetch <remote> <design_base>` を打つ
  2. 取り込む: `doc_prs` が `sprint` なら `git merge --ff-only <remote>/<branch>`、`base` なら `git merge --no-edit <remote>/<design_base>`（今の部品 `base-sync` がスプリントの PR に打つのと同じ取り込み。衝突すれば `git merge --abort` で戻して `precondition`）
  3. **照合する**: `git diff --quiet <design_head> HEAD -- <承認の記録の files>`（計画・単位のファイル・設計文書）で、取り込んだ結果（スプリント側の既存の変更との自動の合成を含む）の承認したファイルが承認した HEAD と同じかを確かめる。違えば push せずに `git reset --keep <控えた HEAD>` で取り込みを手元から外し、`precondition`（「承認した <パス> がスプリントで取り込みの後に変わる」）で止める
  4. 一致したときだけ、`base` なら `git push <remote> HEAD:refs/heads/<branch>`（`sprint` はマージでリモートのスプリントが既に進んでいるので push しない）。承認と違う計画をスプリントへ公開しない
- `doc_prs: base` を選んだプロジェクトも宣言を変えずに使える。マージ先は宣言のまま（ベースブランチ）で、スプリントへは `design-sync` の取り込みで届く。移行の手順は要らない

#### ステップ

| ステップ | 中身 | 失敗したとき |
| --- | --- | --- |
| `docs-review`（テンプレート `design-review`） | ① `{{review}} --findings {{rebuild_findings}}`（既定の起動は `--purpose design`。`rebuild_findings` は工程「ドキュメント再構成」の出力）→ ② 直しの push → ③ `ndf plan units check --from-stage --docs --out {{stage_dir}}/units-check.json`（**最終の HEAD の単位のファイルと表を 1 回だけ確かめる**。工程の入力 `issues`・`plan_omitted` は `_call_plan` が渡す `NDF_STAGE_INPUT` から読む）→ ④ Draft を外す（`gh pr view <design_pr> --json isDraft` が真のときだけ `gh pr ready <design_pr>`。Ready 済みの PR での打ち直しでは打たずに進む）→ ⑤ `design-ci`: `ndf ci watch --pr {{design_pr}} --conflict --budget {{ci_budget}} --required={{required}} --test={{tests}} \| tee {state_dir}/ci.json`（下の「番号に固定した CI と衝突の判定」。結果は `{state_dir}/ci.json`）→ ⑥ `ndf progress design target`（下の「HEAD の照合」。通れば承認の記録を書く） | ③ が止まれば修正の worker が単位のファイルか文書を直して ② へ戻る。⑤ のテストの失敗は ci-fix → push → ① へ戻る（HEAD が変わったのでレビューと ③ をやり直し、④ は Ready 済みなので飛ばす）。⑤ の `base_conflict` はマージ先の取り込み（base-sync）→ ① へ戻る。⑥ の照合が食い違えば ① へ戻る |
| `design-mvv` | 今のまま（`ndf records mvv gate --gate design`） | 今のまま |
| `design-gate` | 承認を 1 回だけ求める。承認資料は承認の記録（`design-target.json`）と、そこから指す `units-check.json` だけから作る（下の「承認資料」）。資料を作るときに読んだ 2 つのファイルのバイト列の SHA-256 を照らしてから表を作り、回答の事象に記録の `pr`・`base`・`head` と、資料を作ったときの 2 つの SHA-256（`target_sha256`・`units_check_sha256`）を写す | 差し戻しは今のまま。課題を外した回答は下の「承認を無効にする遷移」。照合が食い違えば資料を作らずに `stale_approval` で失敗し、`restart_on` で `docs-review` から開き直す。回答の時点の照合が食い違えば回答を記録せずに同じく開き直す |
| `design-merge`（テンプレート `design-land`） | 設計の作業場所で `ndf ci merge --target {{stage_dir}}/design-target.json --answer {{stage_dir}}/design-answer.json` の 1 ステップだけ（判定は「マージの対象の照合と再開」。`--pr`・`--branch`・`--base`・`--reviewed`・`--method`・`--since` は記録から読む）。要求の前に `ci merge` が下の「回答との照合」を打ち、違えば要求を送らずに `refused`（`stale_approval` に写す）。部品の `conflict-check`（ブランチ名で `gh pr view` を打ち、`DIRTY` なら `base-sync` へ）は置かない: 記録済みの `design_pr` を照らす前にブランチ名で別の PR を引く CLI の起動と API の呼び出しが再開のたびに増え、`DIRTY` の判定が照合より先に走るためである。衝突は `ci merge` が照合と同じ PR の読み取りの `mergeStateStatus` で判定し、`base_conflict` を返す。承認のステップ（`merge-gate`）を持たず、プランの中で承認を聞き直さない。マージキューの待ち（`ci_pending`）で返った工程の打ち直しは `design-merge` から続けて同じ argv を打ち、マージ済み（PR が `MERGED` で HEAD が記録の `head`）なら成功として次へ進む | `refused`（番号・ブランチ・宛先・HEAD・再オープン・残リスク・方式）と `base_conflict` は `stale_approval` に写して失敗する（下の「承認を無効にする遷移」）。ただし「承認外の宛先へマージされた」は `precondition` で止め、人に返す（戻せないため開き直さない） |
| `design-sync` | 上の「マージの後に承認済みのファイルをスプリント側へ揃える」。揃えた後に `units_queue(design-target.json, design-answer.json, branch)` を打ち、出力 `units_queue` を出す（上の「単位のキューの承認の受け渡し」） | `precondition` で止まる（実装へ進まない）。回答との照合に落ちたときも、マージの後なので開き直さずに `precondition` で人に返す |

**番号に固定した CI と衝突の判定**（⑤）: 既存の部品 `conflict-check` と `ci` はどちらもブランチ名で `gh pr view` を打って番号を引き直すが、`docs-review` では番号 `design_pr` が工程「設計」の出力で確定している。そこで `design-review` はこの 2 つの部品を使わず、記録済みの番号を `ndf ci watch --pr` に直に渡す 1 ステップにする（ブランチ名で引く CLI の起動 2 回と API の呼び出し 2 回を省く）。衝突の判定も同じ番号に固定し、`ci watch` に `--conflict` を足す（`ci_cli.py`。Task 7）: `ci watch` が最初に読む同じ PR の読み取り（`GitHub.pull(番号)`）の `mergeStateStatus` が `dirty` なら、チェックを待たずに `stopped`（`base_conflict`）を返す。`conflict-check` と CI がそれぞれ PR を読む今の形より読みが 1 回少なく、衝突と CI の判定が同じ PR の同じ読み取りに結び付く。`--conflict` を付けない従来の呼び出しの振る舞いは変えない

#### 設計の承認の記録（`design-target.json`）

工程のステップの入力は工程を始めたときの文脈から組まれ、ステップの出力が文脈へ入るのは工程の終わりである（`progress/run.py` の `_run_step` と `_finish_stage`）。またプランの結果の `metrics`・`items` は工程へ渡る `outputs` に含まれない。そこで同じ工程の中で `docs-review` の結果を `design-gate` と `design-merge` へ渡すのは文脈でなく、工程ごとに 1 つのディレクトリ `{{stage_dir}}`（`invoke` が `work_root/<機械>/<工程の id>/shared-g<世代>` を差し込む。世代は下の「承認を無効にする遷移」。ステップの作業ディレクトリ `step_location` と別で、工程の中のすべてのステップが同じ場所を指す）に置いたファイルとする。

| ファイル | 書く | 読む | 中身 |
| --- | --- | --- | --- |
| `units-check.json` | ③ | ⑥・承認資料 | `units check` の結果 JSON（`metrics`・`items`・`warnings` をそのまま。`metrics.files` に読んだファイルのパスと blob の SHA） |
| `design-target.json` | ⑥（下の検証がすべて通ったときだけ。通らなければ書かず、前の記録も消す） | 承認資料・`design-merge`・`design-sync` | `{"pr", "branch": design_branch, "base": design_base, "head", "method", "since"（書いた時刻）, "files"（計画・単位のファイル・設計文書のパス。`units-check.json` の `metrics.files` と同じ）, "units_check_sha256", "queue": {"design", "units"}, "sprint"（単位の宛先＝スプリントのブランチ。`queue.units` を読んだ宛先）}` |
| `design-material.json` | 承認資料を作ったとき（`asking.py`。手動は `ndf progress design ask`） | 回答 | `{"target_sha256", "units_check_sha256"}`（資料を作るときに読んだ 2 つのファイルのバイト列の SHA-256） |
| `design-answer.json` | 回答を記録したとき（`answering.py`。手動は `ndf progress design answer`） | `design-merge`（`ci merge --target --answer`） | `{"answer": "approved", "target_sha256", "units_check_sha256", "pr", "base", "head", "queue", "answered_at"}`（`design-material.json` の値を写す） |

- `design_head` は `design-target.json` の `head` を工程の出力として文脈に書く（工程の終わりで入る。後の工程の参照用で、マージはファイルの値で打つ）
- **資料を作る時点の照合**: `asking.py` は `design-target.json` と `units-check.json` をそれぞれ 1 回だけバイト列で読み、`units-check.json` の SHA-256 が記録の `units_check_sha256` と同じかを確かめてから、**その読んだバイト列から**表を作る（読み直さない）。違えば資料を作らずに `stale_approval`（記録の後に検査の結果が入れ替わった）。表を作ったバイト列の 2 つの SHA-256 を `design-material.json` に書き、回答（`answering.py`）はそれを `design-answer.json` と回答の事象に写す。回答は資料を作った中身と同じ記録に結び付き、後で別の整合した記録へ差し替えても回答と一致しない
- **回答との照合**（`ndf ci merge --target <記録> --answer <回答>`。工程と手動の両方が打つ唯一の入口）: マージの要求の前に、PR を読む前に、①回答の `answer` が `approved`、②記録のファイルの SHA-256 が回答の `target_sha256`、③記録の指す `units-check.json` の SHA-256 が記録の `units_check_sha256` と回答の `units_check_sha256` の両方、④記録の `pr`・`base`・`head`・`queue` が回答の値、のすべてと同じかを確かめ、どれかが違えば `refused`（「承認した記録と違う」。`design-merge` では `stale_approval`）。`invoke` の側で同じ照合を別に持たない

**HEAD の照合（⑥。`ndf progress design target --pr <design_pr> --branch <design_branch> --base <design_base> --check {{stage_dir}}/units-check.json --ci {state_dir}/ci.json --out {{stage_dir}}/design-target.json`）**: 次の検証がすべて通ったときだけ記録を書く。どれかが通らなければ記録を書かずに `stopped`（`stale`。結果の失敗・用途の違いは `invalid`）を返し、① へ戻る。

1. **結果の成功と用途**: `ci.json` が `tool: ci` の `ci watch` の結果で `status: ok`、`metrics.pr` が `--pr` と同じ（別の PR の CI・失敗・待ちの結果を通さない）。`units-check.json` が `tool: plan` の `units check` の結果で `status: ok`、`metrics.docs` が真（`--docs` 付きで表まで照らした）
2. **4 つの SHA**: `units-check.json` の `metrics.head`（単位を確かめた HEAD）、ローカルの HEAD、`gh pr view <design_pr> --json headRefOid`（PR の HEAD）、`ci.json` の `instance`（CI を判定した PR の HEAD）が同じ。今の `ci_cli._report` は失敗のときだけ `instance` に SHA を入れるので、**成功の結果にも判定に使った `pull.head_sha` を `instance` に載せる**（Task 7。`ci watch` の成功を読むほかの呼び手は `instance` を読まないので振る舞いは変わらない）。違えば（⑤ の後に push があった、PR を別の HEAD で更新した）`stale`
3. **検査がそのコミットの内容を読んだこと**: `units-check.json` の `metrics.files` のすべてのパスの blob の SHA を、`git ls-tree -z <head> -- <パス>...` の **1 回の起動**で取り（ファイルの数によらず CLI の起動は 1 回。引数の長さが OS の上限を超えるときだけ、上限に収まる束に分けて打つ）、各パスの SHA が記録の値と同じかを照らす（出力に無いパス＝コミットに無いのに読んだファイルがあれば違いとする）。ファイルごとに `git rev-parse` を打たない。`units check` の側も、読んだファイルが HEAD と違う（読んだファイルのすべてを渡した `git status --porcelain -z --untracked-files=all -- <読んだファイル>...` の 1 回の起動で、変更か未追跡の行が出る）なら `metrics.head` を書かずに `stopped`（`invalid`、「読んだ <パス> が HEAD のコミットと違う」）を返す。手元の変更を残したまま打っても、別の計画を承認資料に載せられない

`method` は `route.declared_merge_method(repo, design_base)`（上の「マージの方式の決め方」）、`queue` は上の「要求の時点の宛先」（`design_base` とスプリントのブランチが同じなら `merge_queue` の読みを 1 回だけ打ち、両方に使う）、`since` は書いた時刻。`design target` の外部の起動は、ローカルの HEAD 1・PR の HEAD 1・blob の一括の読み 1・キューの読み（異なる宛先ごとに 1）・方式の読み 2 に固定し、計画・単位のファイル・設計文書の数に比例して増えない（テストで起動の回数を縛る）。承認の記録はこの `head` と組なので、承認した PR の HEAD・CI が通った HEAD・単位を確かめた HEAD とその内容は 1 つに結び付き、`design-land` が同じ組を照らす。

**承認資料**: `asking.py` は設計承認ゲートの資料に、設計 PR の URL・`head`・宛先・宛先がマージキューを持つか（`queue` の真の宛先ごとに例外の文。上の「要求の時点の宛先」）と、`units-check.json` の**スプリント全体の**単位の表を載せる。表は `unit` の項目の `seq`・名前・ファイル・実行順・マージの順（実装が使う番号）・並列の相手（`parallel_with`）・先に要る単位・条件・行数と、同時に流す本数の上限（`metrics.parallel`）である。実装計画の文書の表はファイルの中で番号を振るので、承認者が実装の順を見るのはこの資料の表とする（2 ファイルに独立な単位が 1 つずつあれば、文書ではどちらも「マージの順 1」だが、資料では `seq` 1・2 の並列の相手どうしとして出る）。

**手で流す経路（#3908 でテンプレートが動かない間）**: supervisor が工程の外で同じ検査・停止・照合を打つ。手順は Task 9 で `references/approval-request.md` に書き、どの手順も工程と同じコマンドなので結果は同じになる

1. 設計の作業場所で最終の HEAD を push した後に `ndf plan units check --issue <課題>... [--omitted <課題>]... --docs --out <dir>/units-check.json`。`ok` でなければ承認を求めず、書き手に直させてからやり直す（手元に変更を残したまま打つと、上の「HEAD の照合」の 3 のとおり `units check` が止まる）
2. `ndf ci watch --pr <design_pr> --conflict ... | tee <dir>/ci.json` が `ok` の後に `ndf progress design target ...`（上と同じ引数）。`stopped` なら 1 からやり直す
3. 承認資料は `ndf progress design ask --target <dir>/design-target.json --out <dir>/design-material.md` で作る（`progress/command.py`。工程の `asking.py` と同じ関数で、同じ照合をしてから資料を作り、`<dir>/design-material.json` に 2 つの SHA-256 を書く）。今の `ndf answer ask` はゲート待ちを返すだけで資料を読まず回答も保存しないので、この経路では使わない
4. 人が回答した後に、supervisor がその回答を `ndf progress design answer --material <dir>/design-material.json --approved --out <dir>/design-answer.json` で記録する（工程の `answering.py` と同じ関数。打つ時点で記録と検査の結果の SHA-256 が資料と同じかを確かめ、違えば書かずに `stopped`（`stale`）で 1 からやり直す）。人の回答が無いときは打たない
5. `ndf ci merge --target <dir>/design-target.json --answer <dir>/design-answer.json`（上の「回答との照合」で、提示した記録に対する回答との一致をマージの要求の前に機械で確かめる）。`refused`・`base_conflict` なら 1 から（承認を取り直す）
6. `ndf progress design sync --target <dir>/design-target.json --answer <dir>/design-answer.json` でスプリント側へ揃え、`<dir>/units-queue.json` に `units_queue`（上の「単位のキューの承認の受け渡し」）を書く。単位は `ndf progress units plans … --target <dir>/design-target.json --answer <dir>/design-answer.json` で組む

#### 承認を無効にする遷移

今の進行管理は、完了した工程を再実行せず（`decide` は最初の未完了の行を選ぶ）、失敗した工程は失敗したステップから再開する。また `gate/answering.py` は課題を外した回答を承認として記録し、文脈の `issues` だけを書き換える。古い承認と工程の出力を使わずに戻すため、行を開き直す事象を足す。

- **事象 `reopened`**（`progress/run.py`）: 工程の行を `pending` に戻し、その工程の**世代**を 1 つ進める。世代は新しく作らず、今の `StepProgress.generation`（承認ゲートの変更要求で 1 つ進み、ステップを初めから流し直す値）をそのまま使う。`_SEES` に `reopened` の読み手を足し、変更要求の `_answered` と同じく `generation` を 1 つ進めて `done`・`waiting`・`answer` を捨て、数え始め `anchor` を `reopened` の番号へ移す（世代は、その行の変更要求と `reopened` の数の和）。ステップの進み・待ち・回答（古い承認）は捨てられる。文脈（`View.context`）は、開き直した工程が出した出力をその `reopened` より後に書き直されるまで重ねない（`design_head` など古い出力を後の工程が読まない）
- **世代ごとの保存先**: ステップの作業ディレクトリは今の `step_location` のまま `work_root/<機械>/<工程の id>/<ステップ>-g<世代>` とする（今の形が既に世代を含む）。`{{stage_dir}}` は同じ規則で `work_root/<機械>/<工程の id>/shared-g<世代>` にする（新設なので前の場所は無い）。保存先の形を変えないので、**進行中のスプリントの互換の規則は要らない**: この変更の前に保存された工程は `reopened` を持たず、世代は変更要求の数だけで今と同じ値になり、事象が参照する従来の実行の記録を同じ場所から読む（途中まで済んだプランを最初から起動しない、待ちの記録を見失わない）。`reopened` の後は世代が進むので新しい場所に切り替わる。テストは、`reopened` を持たない保存済みの事象の列で `step_location` が今と同じ文字列を返すこと、`reopened` の後に `-g<世代+1>` になることを縛る。ステップの id `shared` は `{{stage_dir}}` と重なるので `ndf.schema.json` で断る（今の定義に `shared` という id のステップは無い）。今の `plan_argv` は同じ保存先・同じ HEAD の完了したプランの結果を再利用するが、開き直した後は保存先が新しく空なので、宛先の変更など HEAD が動かない理由で開き直しても `docs-review` を打ち直し、承認の記録・資料・回答も作り直す。古い世代のファイルは消さずに残す（読む経路が無い。後片付けは今の工程の作業ディレクトリと同じ）
- **`restart_on`**（工程の定義のメタデータ。`design-approval.json` に `{"design-gate": ["stale_approval"], "design-merge": ["stale_approval"]}`）: ステップがこの種類で失敗したら、`run.py` はその工程に `reopened` を書く。次の起動は工程の最初のステップ（`docs-review`）から流れ、レビュー・MVV 判定・承認をやり直す。`design-gate` は承認資料を作るときの照合の不一致（下の表）を `stale_approval` で失敗するので、今の「失敗したステップから再開する」規則で同じ資料作りを打ち直し続けず、`docs-review` から検査をやり直す
- **回答の時点の照合の不一致**: `answering.py` は回答を記録する前に、`design-material.json` の 2 つの SHA-256 が今の `design-target.json`・`units-check.json` のバイト列の SHA-256 と同じかを確かめる。違えば回答を承認として記録せず（`design-answer.json` も書かない）、回答の事象の代わりに「ドキュメントレビュー」の行へ `reopened` を書く（`restart_on` と同じ世代の更新。理由「資料を作った後に承認の記録か検査の結果が変わった」）。次の起動は `docs-review` から流れ、資料を作り直して承認を求め直す
- **課題セットは工程の出力と独立**: 今の `answering.py` は課題を外した結果の `issues` を承認ゲートの工程の出力として書くので、その工程を `reopened` にすると除外が一緒に捨てられ得る。そこで課題セットの変更を工程に属さない事象 `issues_set`（`{"issues": [...], "excluded": [...], "reason": "excluded" | "restored"}`）として書き、`View.context` は `issues` と `excluded` を `reopened` に関係なく最後の `issues_set` から取る（無ければ工程「要求と受け入れ条件」までの `issues` と空の `excluded`）。`reopened` が捨てるのは工程の出力だけで、`issues_set` は捨てない。書く順は `issues_set` → `reopened`（同じ書き込みの中）

| 起きること | 遷移 | 捨てるもの |
| --- | --- | --- |
| 承認で課題を外した（`answering.py`） | 同じ書き込みで `issues_set`（`issues` = 外す前の課題 − 外した課題、`excluded` = 外した課題）と、「計画」「ドキュメント再構成」「ドキュメントレビュー」の 3 行に `reopened`。回答は承認として消費しない（`design-merge` を打たない）。次の「計画」は `issues` に**残った課題だけ**、`excluded` に外した課題を受け取り、最初のステップ `design-drop`（下）で外した課題の文書と設計の結果を設計 PR から取り除いてから、残った課題で実装計画と単位のファイルを書き直す（外した課題のタスク・条件・依存を除く。統合の計画なら名前の番号の並びも書き直す） | 3 つの工程の出力（`plan_omitted`・`rebuild_findings`・`design_head`）と、ドキュメントレビューの承認の記録・資料・回答（世代が進むので読まれない） |
| `design-gate` が資料を作るときに記録と検査の結果が食い違った（`stale_approval`） | `restart_on` で「ドキュメントレビュー」に `reopened`。次は `docs-review` から（同じ資料作りを打ち直さない） | 承認の記録・資料 |
| 回答を記録するときに資料の SHA-256 と今の記録・検査の結果が食い違った（`answering.py`） | 回答を記録せず、同じ書き込みで「ドキュメントレビュー」に `reopened`。次は `docs-review` から | 承認の記録・資料（回答は記録されない） |
| `design-merge` が照合で断られた・衝突した（`stale_approval`） | `restart_on` で「ドキュメントレビュー」に `reopened`。次は `docs-review` から | 承認の記録・資料・回答 |
| 承認外の宛先へマージされた | `precondition` で止める（開き直さない）。人が戻すかを決め、決めた後に conductor が `reopened` を書くか工程を進める | なし |
| 回答し直しで外した課題を戻した（今の `_restored`） | 同じ書き込みで `issues_set`（`issues` = 外す前の課題に戻す、`excluded` = 空、`reason: restored`）と、「設計」から「ドキュメントレビュー」までの 5 行に `reopened`。次の「設計」は**外す前の課題のすべて**を受け取り、`design-drop` で取り除いた戻した課題の設計を書き直す（既にある設計の作業場所と設計 PR をそのまま使う。`design-draft` は作業場所・PR があれば作り直さない） | 5 つの工程の出力と、承認の記録・資料・回答 |

**外した課題の文書を取り除く**（ステップ `design-drop`。`ndf progress design drop`。新しい経路の「計画」`plan-pr.json` の最初のステップ。`excluded` が空なら何もせずに通り、空の指示のファイルを書く）: 設計の作業場所で次を行い、1 つのコミット（「外した課題 #<番号> の設計を取り除く」）にして push する。**変えてよいのは、このスプリントが設計 PR で追加・変更した差分だけ**とする。起点は設計 PR の宛先との分岐点 `git merge-base <remote>/<design_base> HEAD`（取得はしない。工程「設計」が作業場所を作るときに取得済み）で、起点に既にあった内容（前のスプリントまでの要求仕様・設計文書）は消さない。

1. **先に全部を決める（書き換えない）**: 作業場所が clean（`git status --porcelain` が空）でなければ `precondition` で止める。起点からの差分 `git diff --name-status <起点> HEAD -- issues/` を 1 回だけ読み、外した課題に関わるファイルごとに次の扱いを決める。残った課題の行が「マージする」で外した課題を統合先に持つかの検査（当たれば `precondition`、「#<残った課題> の統合先 #<外した課題> を外した」。人が扱いを決め直す）もここで行い、当たれば何も書き換えずに止める
   - 名前の番号の並び（`DESIGN_DOC`・`PLAN_DOC` と要求仕様・詳細設計の名前）が**外した課題だけ**からなるファイルで、起点に無い（差分で `A`）: `git rm`
   - 同じく外した課題だけのファイルで、起点にあり、このスプリントで変えた（`M`）: `git checkout <起点> -- <パス>` で起点の内容へ戻す（消さない）
   - 同じく外した課題だけのファイルで、このスプリントで変えていない（差分に無い）: 触らない
   - 名前に残った課題と外した課題の両方を含む統合の文書: 「設計の結果」の表のうち、外した課題の行で**起点のその文書に無い行**だけを機械で消す（起点にあった行は残す）。本文の外した課題の記述は機械で消さず、下の 3 の指示のファイルに載せる
2. **書き換えて 1 つのコミットにする**: 1 の扱いを当て、コミットして push する。書き換え・コミット・push の途中で失敗したら、`git reset --hard <始めの HEAD>` で作業場所を始めの HEAD に戻してから失敗を返す（始めに clean を確かめたので、捨てるのはこの手順の編集だけ。push の前に戻すので、途中の編集が設計 PR に残らない）
3. **次のステップへの受け渡し**: 結果を `{{stage_dir}}/drop.json`（`{"excluded": [...], "base": <起点の SHA>, "removed": [パス], "restored": [パス], "rows": [{"path", "issue"}], "rewrite": [{"path", "issues": [外した課題]}]}`）に書き、同じ `items` を結果にも載せる。`implementation-plan` のテンプレートに差し込み `drop_file`（`params.json`。既定は空の文字列）を足し、空でなければ worker の指示に「`<drop_file>` の `rewrite` の文書の本文から、`issues` の課題の記述を取り除く。外した課題の番号を計画・単位のファイル・統合の文書の本文に残さない」を加える。今の `_run_step` は前のステップの `items` を入力に入れないので、`plan-pr.json` の `plan` ステップの `with` に `"values": {"drop_file": "{{stage_dir}}/drop.json"}` を書き、`invoke` が `{{stage_dir}}` を同じ工程・同じ世代の保存先に置き換えて渡す（ファイルが無ければ `invalid`。`design-drop` が終わっていない）。`light`・`operation`・`documentation` の `plan.json` は `drop_file` を渡さないので空のまま今と同じ
4. **残りの検査**: `docs-review` の再レビューは 2 の差分を含む HEAD を読む。`units check --from-stage` は文脈の `excluded` と、`drop.json` を受け取り（`design-drop` が出力 `drop_file` に `drop.json` のパスを出し、工程「計画」の `outputs` に `drop_file`、`context.schema.json` に同じキー、工程「ドキュメントレビュー」の `inputs` に `drop_file` を足す。`NDF_STAGE_INPUT` の `drop_file` から読む。`excluded` が空なら空の `drop.json` を指す）、次のどれかが残れば止める（`stopped`（`invalid`）、「外した #<番号> が <パス> に残る」）: 外した課題を名前の番号に含む計画・単位のファイル（起点に無いもの）、外した課題の `tasks[].issue`・`criteria`、外した課題の「設計の結果」の行で起点に無いもの、**本文**: `rewrite` の文書と、スプリントの計画の文書（`PLAN_DOC` の `.md`）の起点から足した行（`git diff <起点> HEAD` の `+` の行）に、外した課題の番号の参照 `#<番号>`（後ろが数字でないもの）があるもの。起点にあった記述は数えないので、前のスプリントの文書が外した課題に触れていても止まらない。worker が統合の文書の本文を消し残しても、承認の前に機械で止まる

変更の無い経路（レビューが収束し CI が通り、衝突が無い）では、承認は `design-gate` の 1 回だけである。

### 工程の定義（`contracts/workflow/`）

満たす決定: 1。満たす受け入れ条件: 1・4。

| 定義 | 変えた後 |
| --- | --- |
| `modes/standard.json` | 要求 → 作業場所 → 設計（`design-draft`）→ 詳細設計（`design-detail-pr`）→ **計画**（`plan-pr`）→ ドキュメント再構成（`document-rebuild-pr`）→ ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ …（行の数 18 は変わらない） |
| `modes/legacy-refactor.json` | 作業場所 → 設計（`design-draft`）→ 詳細設計（`design-detail-pr`）→ **計画**（`plan-pr`）→ ドキュメント再構成（`document-rebuild-pr`）→ ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ … |
| `modes/light.json`・`operation.json`・`documentation.json` | 変えない（`design.json`・`document-review.json`・`implement.json` を指したまま。`documentation` の「計画」はドキュメントレビューの後のまま） |
| `stages/plan-pr.json`（新設） | `phase: design`。ステップ `design-drop`（`excluded` が空なら何もしない。上の「外した課題の文書を取り除く」）→ `plan`（`implementation-plan`）。`inputs` に `issues`・`worktree`・`excluded`・`design_worktree`・`design_branch`。`outputs: ["plan_omitted", "drop_file"]`（実装計画を省いた課題。省かなければ空の配列。`drop_file` は上の「外した課題の文書を取り除く」の 3）。実装計画を書くのはこの工程だけで、工程「設計」は書かない（設計書を省いた課題も同じ） |
| `stages/design-draft.json` | ステップ `design`（`design-draft`）だけ。`outputs: ["design_pr", "design_worktree", "design_branch", "design_base"]` |
| `stages/design-detail-pr.json`・`plan-pr.json`・`document-rebuild-pr.json`（新設） | `worktree_input: "design_worktree"`（設計の作業場所で書く。上の「ブランチと作業場所」）。3 つとも `inputs` に共有の定義のキー（`issues`・`worktree`。`plan-pr` は加えて `excluded`）と `design_worktree`・`design_branch` を書く（今の `resolve_inputs` は `inputs`・`optional_inputs` に書いたキーだけを文脈から渡すので、書かなければテンプレートの `worktree`・`branch` を設計の組から組めない）。`definition.py` は、`worktree_input` を持つ工程の `inputs` にそのキーと、対になるブランチのキー（`design_worktree` なら `design_branch`）が無ければ定義の誤りとして読み込みで断る。`standard`・`legacy-refactor` だけが指す |
| `stages/design-detail.json`・`plan.json`・`document-rebuild.json` | 変えない（`light`・`operation`・`documentation` が今の `worktree`・phase・出力のまま使う） |
| `stages/design-approval.json` | `inputs`: `issues`・`worktree`・`branch`・`design_worktree`・`design_branch`・`design_base`・`mode`・`pace`・`mvv_ref`・`design_pr`・`rebuild_findings`・`plan_omitted`・`excluded`・`drop_file`。`worktree_input: "design_worktree"`（`design-sync` だけはスプリントの `worktree` で打つ）。`docs-review` → `design-mvv` → `design-gate`（modes は `legacy-refactor`・`standard`）→ `design-merge` → `design-sync`。`restart_on: {"design-gate": ["stale_approval"], "design-merge": ["stale_approval"]}`。`outputs: ["mvv_gate", "design_head", "units_queue"]` |
| `stages/implement-units.json` | `implement.json` と同じステップに `per_unit: true`。`inputs` に `plan_omitted`・`units_queue` を足す |
| `stages/document-rebuild-pr.json` | `outputs: ["rebuild_findings"]`（指摘が無くても空の指摘のファイルを書く） |

**`legacy-refactor` の設計 PR**: 今の手順書は「`legacy-refactor` は設計 PR を分けたときだけ再構成とドキュメントレビューを通る」とするが、今の工程の定義（`design.json`）は条件を持たず、工程「設計」は常に `design_pr` を出す。新しい経路では、単位のファイルを工程「実装」より前にスプリントのブランチへ入れる必要があるので、**`standard` と `legacy-refactor` は常に設計 PR を分ける**（`design-draft` が必ず Draft の PR を開き `design_pr` を出し、「ドキュメント再構成」が必ず `rebuild_findings` を出す）。設計 PR を分けない経路は残さないので、`design-approval` が無い PR や出力を待つ状態は作られない。`per_unit` の工程「実装」は `standard` と `legacy-refactor` で常に選ぶ。手順書の「分けたときだけ」の記述（`development-workflow/SKILL.md`・`references/stage-notes.md`・`references/agent-layers.md`）は Task 9 で「常に分ける」に直す |

## 異常系の処理

| 起きること | 扱い |
| --- | --- |
| 単位のファイルが壊れている（JSON・スキーマ） | `check`・`plans` は `stopped`（`invalid`）。工程「ドキュメントレビュー」の `units check` で止まり、修正の worker が直して push する |
| 単位の違反（U1〜U6）・表の食い違い | `check` は案を添えて `stopped`。`plans` は何も書かない。工程「実装」は失敗し、実装を始めない |
| 新しい形の実装計画に単位のファイルが無い | `stopped`（`invalid`、「実装計画があるのに単位のファイルが無い」）。設計承認の前に止まる |
| 宣言の誤り（`target_lines >= max_lines`・形の違い） | `stopped`（`invalid`、理由に `.ndf/project.json` の `workflow.review_units`）。`invoke._project` は今のまま `WORKFLOW_SCHEMA` で断る |
| 単位のファイルが無い課題（実装計画を省いた・旧い形） | 課題 1 つを 1 単位（`single`）。行数を知らないので U6・W1 を当てず、`check` が省いた・旧い形であることを報告する |
| 承認の後に設計 PR の HEAD が変わる | マージせずに止まり、レビューから打ち直す（承認の記録は使わない） |
| 承認した設計 PR を閉じて開き直した・宛先を変えた（番号・ブランチ・宛先・HEAD が同じままの再オープンを含む） | `ci merge` が承認の記録の `since` より後の `REOPENED_EVENT`・`BASE_REF_CHANGED_EVENT` か宛先の違いで `refused`。`stale_approval` で「ドキュメントレビュー」を開き直し、レビューから打ち直す |
| 照合の後、要求までの間に宛先が変わった | 直接マージは要求の後の読み直しで、キューは再開の照合で見つけて `refused`。工程は `precondition` で止まり、次の実行順へ進まない（マージ済みなら戻す判断を人に返す）。キューの経路は宛先を固定できない経路として承認資料に載る |
| 本文に決めていない「マージの前に決める」残リスクがある・宣言の方式が許されない | 照合の経路も要求を送らずに `refused`（今の `_preflight`・`_method_refusal` と同じ） |
| 単位の PR の HEAD がレビュー済みと違う・衝突した | 記録を `stale` にし、その単位のプランだけを `--resume --from` でレビュー（か取り込み）から打ち直して記録の HEAD を書き換える。2 回目は `blocked` で人に返す |
| JSON だけの計画・区間の印の欠けた計画・このスプリントで書いた印の無い計画 | `stopped`（`invalid`）。設計承認の前に止まる |
| 設計の結果で「マージする」「閉じる」の課題が計画やタスクにある | `stopped`（`invalid`）。設計承認の前に止まる |
| 受け入れ条件の番号が課題に無い・どの単位にも割り当たらない条件がある | U2 で `stopped`。設計承認の前に止まる |
| 正規形でない・ディレクトリ・リポジトリの外を指すパス | `stopped`（`invalid`、パスとタスク）。設計承認の前に止まる |
| 設計の結果の表の扱いが空欄・許容値の外、「マージする」に統合先が無い | `check`・`plans` が `stopped`（`invalid`、文書のパスと課題の番号）。設計承認の前に止まり、単位を作らない |
| 実装計画も省く判断も無い課題 | `stopped`（`invalid`、「#<番号> の実装計画が無く、省く判断も無い」）。設計承認の前に止まる |
| 分け方の案を機械で作れない（条件の無い準備のタスクが大きい・別のファイルの単位と同じファイルを触る） | `check` が `split` の `resplit` と `note` を返して `stopped`。書き手がタスクを切り直す |
| 承認で課題を外した | 承認として扱わず工程「計画」へ戻り、`design-drop` で外した課題の文書と設計の結果を設計 PR から取り除き、残った課題で計画を書き直してから承認を求め直す。外した課題は工程の出力の開き直しで復元されない（`issues_set`） |
| 承認資料を作るときに検査の結果が記録と違う・回答と違う記録でマージを求めた | 資料を作らずに `stale_approval`／`ci merge` が要求を送らずに `refused`。工程は「ドキュメントレビュー」を開き直す |
| 宛先がマージキューを持つのに承認でその例外を認めていない | 登録を送らずに `refused`。設計 PR は承認を取り直し、単位は `blocked` |
| 宣言のマージの方式が宛先で許されない・許可を読めない | 記録を書かずに止める（`invalid`／`precondition`）。別の方式へ替えない |
| `design-sync` の取り込みで承認したファイルが変わる | push せずに取り込みを手元から外して `precondition` |
| 工程「実装」の再開で単位の計画が変わった | 実行の記録が無ければ組み立て直し、あれば `precondition` で人に返す（記録を別の単位に当てない） |
| 実装の途中で止まる（キュー・マージ・マージキューの待ち） | 打ち直すと、マージ済みの単位を飛ばし、記録の状態から続ける。キューから外れた PR はマージを要求し直す |
| 最後のマージの後にスプリントの worktree を早送りできない | `precondition` で止まる（`gathered` へ進まない） |

## 単体テストの観点

| 観点 | 縛る振る舞い |
| --- | --- |
| 実行順 | 依存の無い単位が 1、A → B → C が 1・2・3、B と C が A の後なら両方 2。単位の輪・同じ単位の中のタスクの輪・タスクと単位の重複は U1 で実行順を返さない |
| 導く値 | 行数・触るファイル・課題・受け入れ条件がタスクの和、マージの順が実行順と並びで決まる |
| 規則 | U2〜U6・W1・W2 のそれぞれの最小の入力で `rule` が出る。当たらない入力では出ない。`files` に `src/./a.py`・`src//a.py`・`../a.py`・末尾 `/` を書くとスキーマが断り、末尾 `/` の無い既存のディレクトリ・リポジトリの外を指すシンボリックリンクは `load` が断る。まだ無いファイルは通る。課題の条件が 1・2 で単位が 1 だけを宣言すると U2（`known` を渡したとき）、条件の番号 3 が無いと U2、統合先のタスクが統合する課題の条件を持てば通る |
| 上限の境 | 400 は何も出ない、401 は W1、1,000 は W1、1,001 は U6。宣言 `target_lines: 200` で 201 が W1 |
| 分け方の案 | 同じ深さで同じファイルを触る 600 行のタスク 2 つの単位で、案が 2 単位を直列にし、`apply` した後の `judge` に違反が無い。元の単位を指す後続が案の最後の層を指す。元の単位と同じファイルを触る別の単位が実行順 2 にあると、案の後半との U4 を依存の追加で直し、グラフ全体の `judge` に違反が無い。その別の単位が別のファイルにあれば `resplit` を返す。条件なしの準備 500 行と、それに依存する条件ありの実装 600 行は `resplit: ("T1", "T2")` で案を作らない。目安 400 行で条件を持つ 600 行の単独タスクの単位は W1 で案を作らず、`judge` が再帰しない。併せで輪ができる入力と、依存を足して案の外の 2 単位が重なる入力は `resplit`。案の検証は `check` だけで行う |
| #3698・#3247 | 16,757 行・24,008 行の 1 単位で U6、案のどの単位も 1,000 以下 |
| #3672 | fixture を 1 単位にすると U6、案の単位の実行順が 契約 → 読む部品と解析 → 付け替え → 削除とテスト の順に沿う |
| 集め方 | 統合の計画（`issue-10-11-plan.units.json`）を 1 回だけ読む。2 つのファイルの同じ実行順の単位が同じファイルを触ると U4。新しい形の計画に単位のファイルが無ければ止まる（`issue-10-11-plan.md` に印があり JSON が無いときも）。JSON だけ・終了の印の欠け・スプリントの起点に無い印の無い Markdown は止まり、起点にあった印の無い Markdown だけが旧い形。設計の結果で「閉じる」の課題のタスクが統合計画にある・「マージする」課題の旧い計画が残ると止まる。旧い形と `plan_omitted` の課題は `single` で、`check` と `plans` の両方が U2 に当たらず通る。計画も `plan_omitted` も無い課題は止まる |
| 設計の結果の表 | `design_results` が扱い・統合先を読み、`issue-10-11-design.md` の #11 の行を #11 から読める。`materials.collect.design_files` の返りが今と同じ。設計テンプレートの書き方どおりの行（`\| #11 \| マージする \| #10 \| \|`）が誤りなく通り、`collect` が #11 を `skip` にして #10 の単位を作る（実装のプランを #11 に作らない）。扱いが空欄・「統合する」など `HANDLINGS` の外・「マージする」で統合先が「—」の行は、`check --from-stage` が文書のパスと課題の番号を理由に `stopped`（`invalid`）になり、単位を作らない |
| 表の照合 | 2 つの計画のファイルを `render` で 1 つずつ書いた後、まとめて `check --docs` が `ok`（表はファイルごとのグラフ、U4 は併せたグラフ） |
| マージの照合 | 同じ番号・ブランチ・宛先・HEAD で `--since` の後に再オープンした PR は `refused`。本文に未決の「マージの前に決める」が残ると要求を送らずに `refused`。`--method` が許されない方式なら要求を送らずに `refused`。API の回数が照合の失敗 1・マージ済み 2・待ち 2・登録 3・直接 7 で、マージ済みと待ちで方式を読まず、直接マージの要求の直前の読み直しと要求の間に呼び出しが無い。ブランチか HEAD の違う番号を渡すと、その PR がキューにいても `dequeue` を打たない。宛先が変わった承認済みの PR は、登録した人が今の利用者で `enqueuedAt` が `--since` より後のときだけ外し、ほかの人の登録なら外さずに `refused`。例外の承認の無い PR が既にキューにいると `ci_pending` でなく `refused`。マージ済みで今の `baseRefName` が `--base` と同じでも、`--since` の後に宛先の変更の事象があれば `refused`。キューの方式が `--method` と違えば登録を送らずに `refused`。`declared_merge_method` が `merge.commit` を読み、宣言 `merge.method: merge-queue`・`merge.commit: merge` の本リポジトリの値で `merge` を返す（`merge.method` を方式として渡さない）。`design target` がキューの方式と宣言の方式の違いで記録を書かず、設計 PR とスプリントの宛先が同じならキューの読みが 1 回で、ファイルが 1 個でも 10 個でも blob の読みの起動が 1 回。`ci watch` の成功の結果の `instance` が判定した HEAD。`ci merge` が、番号か宛先の違いで `refused`、マージ済みで HEAD が同じなら `ok`、キューの中なら要求を打たずに `ci_pending`、キューから外れていれば要求し直す。`--base` だけ・`--pr` だけ・`--reviewed` の無い照合の呼び出しは PR を読まずに `invalid`。別の HEAD か別の宛先で `MERGED` の PR は `refused`（`ok` にしない）。要求で PR を 1 回も読み直さない（偽の GitHub で `pull` の読みが照合の 1 回だけ）。要求が断られた後の読み直しで別の HEAD のマージ済みを見ると `refused`。直接マージの後の読み直しで宛先が違えば `refused`、キューの中で宛先が変われば `dequeue` して `refused`。`dirty` の PR は要求を送らずに `base_conflict`。宛先がキューを持ち `--queue-approved`（`--target` では `queue.design`）が無ければ登録を送らずに `refused`。`--target` の記録か検査の結果が `--answer` と違えば PR を読まずに `refused`。`declared_merge_method` が宣言を先に使い、許されない宣言と許可の読みの失敗で止まる。`ci watch --conflict` が同じ PR の読み取りで `base_conflict` を返す |
| プラン | 2 つのファイルからのプランの名前と出力のパスが重ならない。同じ課題の 2 単位で指示のファイルの担当のタスクと `acceptance` が分かれる。条件を持たない課題に `--issue` が付かない。`single` の `files` が空なら `touches` を書かない |
| 流し方の記録 | `units plans` の後の別の起動の `units run --out` だけで、実行順・マージの順・上限・方式を `units.json` から復元して流す。実行順ごとの `git fetch` が 1 回で、同じ実行順の作業場所が `base_sha` の同じコミットから `--no-fetch` で作られる。HEAD の違いで `refused` の単位が `stale` になり、そのプランだけを `--from <プラン>=review` で打ち直して記録の HEAD を書き換えてからマージし、古い HEAD で `ci merge` を繰り返さない。`--resume` で完了したプランを流さない。`units.json` がある再開で `unit_plans` を打たず `base_sha` が変わらない。指紋が違い実行の記録があれば `precondition`、記録が無ければ組み立て直す。`stale` を書いた後に再レビューが中断した次の起動で、記録の `start` から `--from` で打ち直してから `queued` に戻す。再レビューの後にまた断られると `blocked` |
| worker の指示 | `prompt` のステップの argv を本物の `sh` で、単位あり（`unit_file` に指示のファイル）と従来の経路（`unit_file` が空）の両方について打ち、`ndf plan units prompt` を偽の実行ファイルに差し替えて受け取った argv を記録する。どちらも `--out`・`--issue`・`--title`・`--body` が 1 回ずつで、その後ろが `acceptance` の要素だけ（固定の値と空の文字列が重ならない）。単位ありでは `--unit <ファイル>` が付き、従来の経路では `--unit` も空の引数も無い。`unit_file` を渡さない `ndf route render impl` が通り、`impl-prompt.md` が課題の全体を実装する文で書かれ、タスクの一覧と「タスクだけ」の文を持たない。渡すと担当のタスクの節を持つ全文になり、打ち直しても同じ全文（二重にならない）。`single` の単位は指示のファイルを持たず全体の文になる。worker のステップの環境に `NDF_WORK_PROMPT` がプロンプトのパスで入り、argv の末尾は作業名 `impl` のまま |
| 実際のキュー | A → B の 2 単位を、別々の `ndf plan queue` の起動（A の後にマージの記録を置いてから B）で流すと B が流れる。依存の無い 2 単位は同じ起動で同じ組に入る |
| 工程の流し方 | 単位ごとに作業場所とブランチが分かれ、`review` の argv が単位のブランチを持ち、`ci merge` を単位の作業場所で `--pr <記録の番号>` 付きで打つ。PR の番号とレビュー済みの HEAD の記録がマージの要求より前に書かれる。A をマージ済みにして B で止めた後の打ち直しで A に `ci merge` を打たない。`ci_pending` では次の実行順を始めない。`--max` がレーンの `max_plans` とマシンの上限の小さい方。最後のマージの後にスプリントの worktree を早送りしてから `gathered` が `ok` になる。`ndf progress units plans` で 2 課題の単位の題・本文・`review` が単位ごとに違い、単位ごとの差し込みを `--set` で与えると止まる。`ndf progress units run` が実行順 1 のマージの後に実行順 2 を流す |
| 承認とマージ | `design-draft` が宛先 `pr_target("doc")`（`doc_prs: sprint` でスプリント・`base` でベース）で設計の作業場所から PR を開き、`design-land` の `--base` が同じ値。`doc_prs: base` でマージの後に `design-sync` がスプリントへ取り込み、承認したファイルが同じことを確かめる。`ci watch` が成功した後に `design target` が 4 つの SHA をそろえて記録を書き、承認まで進む。承認資料の表がスプリント全体の `seq`・並列の相手・上限を出す。記録を書き換えると `design-merge` が `stale_approval` で「ドキュメントレビュー」を開き直す。資料を作る前か回答の前に検査の結果を入れ替えると、`design-gate` の打ち直しでなく `docs-review` から流れ直す。`reopened` を持たない保存済みの工程が今と同じ保存先を読み、`reopened` の後に新しい世代の保存先を使う。`units_queue` が工程では `design-sync` の出力から、手動では `units plans --target --answer` から `units.json` の `queue` に入り、`units run` の再開でも同じ `--queue-approved` を付ける。課題を外した回答で「計画」「ドキュメント再構成」「ドキュメントレビュー」が `reopened` になり、古い `design_head` と回答を使わない。`design-review` の既定のレビューの argv が `--purpose design --findings <再構成の指摘>` を持つ。Draft を外すステップが CI の前にあり、Ready 済みの PR では `gh pr ready` を打たない。CI を落として直した後、レビューと `units check` をやり直して承認まで進む。4 つの HEAD のどれかが違えば `design_head` を書かない。変更の無い経路で承認が 1 回。マージの argv が `--target <stage_dir>/design-target.json`。HEAD を変えるとマージせずに止まる。マージキューの待ちの後の打ち直しでマージ済みを成功にする。`design-land` のプランが `conflict-check` を持たず、再開で打つ外部の起動が `ci merge` の 1 回だけ。`design-review` のプランがブランチ名の `gh pr view` を打たない。`design target` が失敗・待ちの `ci.json`・別の PR の `ci.json`・`--docs` の無い検査・HEAD と違う内容を読んだ検査で記録を書かない。資料を作る前に検査の結果を入れ替えると資料を作らない。手動の `design ask` → `design answer` → `ci merge --target --answer` が工程と同じ照合を通り、回答の後に別の整合した記録へ差し替えると `refused`。`doc_prs: base` の設計ブランチがベースから切られ、スプリント側のコミットのうち要求仕様だけを持つ。`design-sync` が照合に落ちると push しない。宛先の変更で開き直した後（HEAD が同じ）に `docs-review` が打ち直される（世代の保存先）。課題を外した後の「計画」が残った課題だけを受け取り、`design-drop` が外した課題だけの文書のうちこのスプリントで足したものを消し、起点にあって変えたものは起点の内容へ戻し、起点のまま変えていないものと起点にあった表の行は残す。統合先の検査に落ちると何も書き換えず、コミットの途中で落ちると作業場所が始めの HEAD に戻る。`drop.json` の `rewrite` が次の `implementation-plan` の指示に入り、統合の文書の本文に外した課題の `#<番号>` を足したまま残すと `units check --from-stage` が止まる。`plan-pr`・`design-detail-pr`・`document-rebuild-pr` が `design_worktree`・`design_branch` を入力に持ち、`worktree_input` のキーを `inputs` に持たない定義は読み込みで断る。戻した後は「設計」が外す前の課題のすべてを受け取る。`light`・`operation`・`documentation` の工程が `design_worktree` 無しで通る。承認で課題を外すと「計画」から流れ直す。本物の `ndf` で `design-review` のプランを流し、`units check --from-stage` が工程の `issues` を受け取る |
| 工程 | 5 つのモードを `test_sprint_templates.py` の偽の流しで最後まで流す。`standard`・`legacy-refactor` で「計画」の位置と phase、設計承認ゲートのステップが「ドキュメントレビュー」に 1 つ、`mvv_gate` が「ドキュメントレビュー」の出力。`light`・`operation`・`documentation` の並びとステップが変わらない。`legacy-refactor` が `design_pr` と `rebuild_findings` を出して `design-approval` と `implement-units` を最後まで流す。`per_issue` だけを持つ保存済みのスナップショットを読める |
