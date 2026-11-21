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
│   │   ├── impl.json                                 # worker の前に単位の指示をプロンプトへ足す unit-prompt（unit_file。既定は空）
│   │   └── params.json                               # unit_file の既定
│   └── workflow/
│       ├── ndf.schema.json                           # 工程のメタデータに per_unit・restart_on・worktree_input を足す
│       ├── context.schema.json                       # 文脈のキー rebuild_findings・design_head・plan_omitted・design_worktree・design_branch・design_base を足す
│       ├── units-run.schema.json                     *（流し方の記録 units.json の形）
│       ├── review-units.schema.json                  *
│       ├── modes/{standard,legacy-refactor}.json     # 新しい 3 つの工程を指し、計画を詳細設計の直後へ
│       └── stages/
│           ├── design-draft.json                     *（工程「設計」。standard・legacy-refactor だけ）
│           ├── design-approval.json                  *（工程「ドキュメントレビュー」。standard・legacy-refactor だけ）
│           ├── implement-units.json                  *（工程「実装」。per_unit。standard・legacy-refactor だけ）
│           ├── design-detail.json                    # worktree_input: design_worktree
│           ├── plan.json                             # phase: design・worktree_input: design_worktree
│           └── document-rebuild.json                 # 出力に rebuild_findings を足す・worktree_input: design_worktree
├── src/ndf/
│   ├── core/plan/
│   │   ├── units_decide.py                           *（純粋な判定）
│   │   ├── units_files.py                            *（ファイル・宣言・文書・プランの読み書き）
│   │   ├── cli.py                                    # ndf plan units check / render
│   │   └── inputs_decide.py                          # design_gaps の単位の数の規則を消し、設計の結果の表の読み手（扱い・統合先を含む）を置く
│   ├── core/route/__init__.py                        # 既定のレビューの起動で design-review も --purpose design にする
│   ├── domains/materials/collect.py                  # 設計の結果の表の読み手と設計文書の番号の並びを inputs_decide のものへ寄せる
│   ├── ci_cli.py / adapters/github.py                # ndf ci merge に照合の経路（--pr・--base・--method・--since と --target）を足す。ci watch の成功にも instance
│   ├── domains/worktree/{create,cli}.py              # ndf worktree create に --no-fetch を足す（実行順ごとに固定した SHA から作る）
│   └── progress/
│       ├── run.py                                    # 工程の行を開き直す事象 reopened と、ステップの失敗で工程を開き直す restart_on
│       ├── gate/{asking,answering}.py                # 承認の記録に design-target の組を写す。課題を外した回答で「計画」から開き直す
│       ├── stages.py / definition.py                 # per_unit・restart_on を足す（per_issue は残す）
│       ├── invoke.py                                 # per_unit の工程は単位ごとの作業場所・プラン・キュー・マージで流す。ndf.plan にも工程の入力を渡す
│       └── command.py                                # ndf progress units plans / run と ndf progress design target（supervisor が手で流す入口。工程と同じ処理）
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
| `src/ndf/core/plan/cli.py` | 変更 | サブコマンド `units`（`check`・`render`） | `units_files` |
| `src/ndf/core/plan/inputs_decide.py` | 変更 | `design_gaps` から「PR の単位が 2 つ以上」の理由を消す（決定 2）。設計の結果の表の行の読み手 `design_results`（課題・扱い・統合先・触るファイル）、扱いの許容値 `HANDLINGS` と行の検査 `design_result_errors`、設計文書の名前の番号の並び `DESIGN_DOC` を置く（下の「設計の結果の表の読み手」） | 無し |
| `src/ndf/domains/materials/collect.py` | 変更 | `design_rows`・`DESIGN_FILE` を消し、`inputs_decide.design_results`・`DESIGN_DOC` に替える（読み手を 1 つにする） | `inputs_decide` |
| `src/ndf/ci_cli.py`・`src/ndf/adapters/github.py` | 変更 | `ndf ci merge` に照合の経路（`--pr`・`--base`・`--method`・`--since` と、それらを 1 つのファイルから読む `--target`）を足す（下の「マージの対象の照合と再開」）。`ndf ci watch` の成功の結果にも判定した PR の HEAD を `instance` に載せる（今は失敗のときだけ） | 無し |
| `src/ndf/domains/worktree/create.py`・`cli.py` | 変更 | `ndf worktree create` に `--no-fetch` を足す（`--from` に渡した SHA から取得せずに作る。下の工程の流し方の 2） | 無し |
| `src/ndf/progress/run.py` | 変更 | 工程の行を開き直す事象 `reopened`（行を `pending` へ戻し、その工程のステップの進みと待ちの回答を捨てる）と、工程の定義の `restart_on`（ステップの失敗の種類で工程を最初のステップから開き直す）を読む（下の「承認を無効にする遷移」） | 無し |
| `src/ndf/progress/gate/asking.py`・`answering.py` | 変更 | 設計承認ゲートの資料を設計の承認の記録（`design-target.json`）から作り、回答の事象にその組とハッシュを写す。課題を外した回答では「計画」から「ドキュメントレビュー」までを `reopened` にする | `run` |
| `src/ndf/core/route/__init__.py` | 変更 | `review_launch` の `--purpose design` を、テンプレート `design` と `design-review` の両方で選ぶ | 無し |
| `contracts/workflow/ndf.schema.json` | 変更 | 工程のメタデータに `per_unit`（`const: true`）・`restart_on`（ステップの id → 失敗の種類の配列）・`worktree_input`（テンプレートの作業場所を組む文脈のキー。省けば `worktree`）を足す。`per_issue` は残す | 無し |
| `contracts/workflow/units-run.schema.json` | 新設 | 流し方の記録 `units.json` の形（下の「流し方の記録」） | 無し |
| `contracts/workflow/stages/design-detail.json` | 変更 | `worktree_input: "design_worktree"`（設計の作業場所で書く） | 無し |
| `contracts/workflow/context.schema.json` | 変更 | 文脈のキー `rebuild_findings`（再構成の指摘のファイル）・`design_head`（承認した設計 PR の HEAD）・`plan_omitted`（実装計画を省いた課題の番号の配列）・`design_worktree`・`design_branch`・`design_base`（設計 PR の作業場所・head・宛先）を足す | 無し |
| `contracts/workflow/stages/plan.json` | 変更 | `phase: design`・`worktree_input: "design_worktree"` と `outputs: ["plan_omitted"]`（確かめのステップは足さない） | 無し |
| `contracts/workflow/stages/document-rebuild.json` | 変更 | `outputs` に `rebuild_findings` を足す・`worktree_input: "design_worktree"` | 無し |
| `contracts/workflow/stages/design-draft.json` | 新設 | 工程「設計」（standard・legacy-refactor）。ステップ `design`（`design-draft`）、`outputs: ["design_pr", "design_worktree", "design_branch", "design_base"]` | 無し |
| `contracts/workflow/stages/design-approval.json` | 新設 | 工程「ドキュメントレビュー」（standard・legacy-refactor）。`docs-review`（`design-review`）→ `design-mvv` → `design-gate` → `design-merge`（`design-land`）→ `design-sync`。`restart_on: {"design-merge": ["stale_approval"]}` | 無し |
| `contracts/workflow/stages/implement-units.json` | 新設 | 工程「実装」（standard・legacy-refactor）。`implement.json` と同じステップに `per_unit: true` | 無し |
| `contracts/workflow/modes/standard.json`・`legacy-refactor.json` | 変更 | 新しい 3 つの工程を指し、「計画」を「詳細設計」の直後へ移す | 無し |
| `contracts/templates/design-draft.json` | 新設 | 設計の作業場所を作る（`<branch>-design`）→ 設計の worker → push → PR を宛先 `pr_target("doc")` へ Draft で開く | 無し |
| `contracts/templates/design-review.json` | 新設 | 下の「設計 PR の承認とマージ」の並び | 無し |
| `contracts/templates/design-land.json` | 新設 | 承認の記録を `ndf ci merge --target` でマージする（承認のステップを持たない） | 無し |
| `contracts/templates/implementation-plan.json` | 変更 | worker の指示に「単位のファイルを書き、`ndf plan units render` で表を作る」を足す | 無し |
| `contracts/templates/impl.json`・`params.json` | 変更 | 差し込み `unit_file`（既定は空の文字列）と、worker の前のステップ `unit-prompt`（空なら何もしない）を足す（下の「worker への単位の指示」） | 無し |
| `src/ndf/progress/stages.py`・`definition.py` | 変更 | 工程の型に `per_unit: bool = False` と `restart_on: Mapping[str, tuple[str, ...]]`（ステップの id → 開き直す失敗の種類）を足す（`per_issue` はそのまま） | 無し |
| `src/ndf/progress/invoke.py` | 変更 | `per_unit` の工程の `ndf.plan` のステップを、単位ごとの作業場所・プラン・`ndf plan queue`・マージで流す。`ndf.plan` の起動にも `NDF_STAGE_INPUT` を渡す | `units_files` |
| `src/ndf/progress/command.py` | 変更 | `ndf progress units plans`・`ndf progress units run`・`ndf progress design target`（工程の外から同じ処理を打つ入口） | `invoke` |
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

`UnitGiven` は工程が渡す値（スプリントの課題 `issues`・省いた課題 `omitted`・スプリントのブランチ `sprint`・リモート・状態の保存先・テンプレートの差し込みの組み立て `values_of(unit) -> 値`）である。

1. `collect(repo, given.issues, given.omitted)` → 各ファイルを `load` → ファイルを持たない `single` の課題を足して `merge` → `designed` → `judge`。違反があれば何も書かない。ファイルをまたぐ U4 もここで当たる
2. 単位に、スプリント全体で一意な連番 `seq` を、実行順 → マージの順（ファイルのパスの順 → `units[]` の並び → `single` の課題の番号の順）で振る（`numbering(units, order, files)`。`units check` の `unit` の項目も同じ関数で振る）。プランの名前・ブランチ・作業場所はどれも `<sprint>-u<seq>`、出力は `out/<sprint>-u<seq>.json`（別のファイルどうしで同じ名前にならない）
3. 単位ごとに指示のファイル `out/<sprint>-u<seq>.unit.json` を書く: 単位の名前・代表の課題・単位の課題・担当のタスク（`id`・`title`・`files`・`criteria`）・実装計画の文書のパス・「このファイルの tasks だけを実装し、ほかのタスクに手を付けない」の一文。`single` の単位は担当のタスクを「課題の全体」とする
4. 差し込み: `issue` は代表の課題、`acceptance` は単位の課題のうち条件を持つ課題ごとに `--issue <課題>` と、その課題の条件ごとに `--criteria <課題>:<条件>`（条件を持たない課題には `--issue` を付けない。全部の条件を照らさないため）。`single` の単位は `--issue <課題>` だけ（課題のすべての条件）。`unit_file` は 3 の指示のファイル。残りの差し込み（題・本文・`review`・`test_command` など）は `given.values_of(unit)` が組む（下の工程の流し方）
5. プランの最上位へ `touches`（単位の触るファイル。空なら書かない）を足す。`needs` は**同じ実行順のプランだけ**を指す（同じ実行順には依存が無いので、実際には書かない）。前の実行順への依存は、工程が前の実行順のマージを確かめてから次のキューを打つことで守る（今の `ndf plan queue` は起動ごとに済んだプランを空から数え、別の起動で済んだ `needs` を解けないため）。単位の依存は `items[].after` と指示のファイルに残す
6. 返りは実行順・マージの順に並べた `UnitPlan(path, unit_file, name, order, merge, branch)`

### コマンド（`core/plan/cli.py`）

満たす受け入れ条件: 3・5・6。結果 JSON の `tool` は既存の `TOOL`（`plan`）。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `units check [<ファイル>...] [--issue N]... [--omitted N]... [--from-stage] [--docs] [--lane-plans N] [--out <ファイル>]` | ファイル・`--issue`・`--from-stage`（環境変数 `NDF_STAGE_INPUT` の `issues` と `plan_omitted`）のどれかで単位のファイルを `collect` の規則で集め（手で打つときの省いた課題は `--omitted`）、`designed` を読んで（ファイルを直に渡したときも、その課題の設計の文書があれば）**`merge` した後のグラフ全体**で `judge` を打つ（ファイルをまたぐ U4 もここで当たる）。`--docs` は加えて、印のある実装計画の文書ごとに、**対の単位のファイル 1 つだけ**のグラフで `render` を作り、今の文書と照らす（`render <そのファイル> --doc <その文書> --check` と同じ判定を同じ起動の中で行う。表は書き手が `render` で作った形と同じになる） | 違反が無ければ `ok`（`items` に `unit` と `split`、`warnings` に W1・W2 と旧い形の文、`metrics` に `units`・`orders`・`target`・`max`・`omitted`・`head`（確かめた HEAD）・`parallel`（`parallel_cap` の値。`--lane-plans` か `--from-stage` の入力のレーンの `max_plans` を渡す））。`unit` の項目は**スプリント全体の `merge` 後のグラフ**で、実装が使う値と同じ番号を持つ: `{"kind": "unit", "file", "name", "seq", "order", "merge", "parallel_with"（同じ実行順のほかの単位の `seq` と名前）, "after", "issues", "criteria", "lines"}`。`seq`・`merge` は `unit_plans` の 2 と同じ関数（`numbering(units, order, files)`）で振るので、承認資料の表と実装のマージの順が食い違わない。`--out` を渡せば同じ結果 JSON をそのファイルにも書く（設計の承認の記録が読む。下の「設計の承認の記録」）。違反・表の食い違いがあれば `stopped`（`invalid`、`items` に `violation` と `split`。案を作れない単位の `split` は `units` が空で `resplit` と `note` を持ち、書き手にタスクの切り直しを求める）。読めない・宣言の誤り・書き忘れは `stopped`（`invalid`） |
| `units render <ファイル> --doc <実装計画> [--check]` | `render` で全文を作り、`--check` が無ければ書く（worker が手元で使う。単位のファイルが複数あるスプリントでも、ファイルごとに打つ） | `ok`。`--check` で食い違えば `stopped`（`invalid`、理由「表が単位のファイルと食い違う」） |

単位のプランを組んで流す手動の入口は `ndf plan` でなく `ndf progress units`（`progress/command.py`）に置く。題・本文・既定のレビューの起動は単位ごとに違い、それを組む `template_values` が `progress` にあるためである。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `ndf progress units plans --out <ディレクトリ> --sprint <ブランチ> [--remote R] [--issue N]... [--omitted N]... [--from-stage] [--set 名前=値]... [--lane-plans N]` | 工程と同じ `values_of`（下の工程の流し方の 1）で `unit_plans` を打つ。単位ごとの差し込み（`issue`・`acceptance`・`unit_file`・`title`・`body`・`review`・`branch`・`worktree`・`base`）は単位ごとに組み、`--set` で与えると `stopped`（`invalid`、「単位ごとに組む差し込み <名前> は --set で与えない」）。`--set` は**それ以外の差し込みにだけ全単位に共通に**当たる（`test_command` など） | `ok`（`items` に `{"kind": "plan", "path", "unit_file", "unit", "order", "merge", "branch", "after"}`、`metrics.max` に `parallel_cap` の値）。同じ値を**流し方の記録** `out/units.json` にも書く（下の「流し方の記録」）。違反は `stopped`（`invalid`） |
| `ndf progress units run --out <ディレクトリ>` | `out/units.json` を読み（スプリント・リモート・上限・単位の並びはそこから取り、引数で与えない）、`plans` が書いたプランを、下の工程の流し方の 2〜7（実行順ごとの作業場所・キュー・マージ・記録・再開・スプリントの worktree の早送り）と同じ関数で流す。打ち直すと記録から続ける | 工程の `_call_units` と同じ（`ok`・`ci_pending`・`stopped`） |

#### 流し方の記録（`units.json`）

`unit_plans` は、プランと指示のファイルに加えて `out/units.json` を書く。工程（`_call_units`）と手動（`ndf progress units run`）はどちらもこのファイルだけから流し方を復元し、組み立ての後に起動が分かれても同じ計画で流す。

```json
{"version": 1, "sprint": "sprint-3702", "remote": "origin", "max": 3, "merge_method": "squash",
 "units": [{"seq": 1, "name": "契約と退行テスト", "file": "issues/issue-3702-plan.units.json", "order": 1, "merge": 1,
            "plan": "sprint-3702-u1.json", "unit_file": "sprint-3702-u1.unit.json", "branch": "sprint-3702-u1", "after": []}],
 "orders": {"1": {"base_sha": null}}}
```

- `max` は組み立ての時点の `parallel_cap`、`merge_method` は宛先（スプリントのブランチ）の宣言から `route.default_merge_method` で決めた方式（単位のマージに `--method` で渡す）。`orders.<k>.base_sha` は実行順 k を始めたときに固定した SHA（下の 2。始める前は null）
- 単位ごとの PR・HEAD・状態は `work_dir/units/<seq>.json`（下の 4）に分けて置き、`units.json` は組み立てと実行順の開始のときだけ書く
- 形は `contracts/workflow/units-run.schema.json`（新設。Task 8）で確かめ、読めなければ `stopped`（`invalid`）

### 工程の流し方（`progress/invoke.py`）

満たす決定: 1・3・7。満たす受け入れ条件: 3・4。

`per_unit` の工程（`implement-units`）の `ndf.plan` のステップは、`_call_plan` の中で `_call_units(run, cwd, work_dir, runner)` に分ける。`per_unit` でない工程は今のまま。

1. **組み立て**: `units_files.unit_plans` に `values_of(unit)` を渡す。`values_of` は、単位の値で写した入力（`issues` を代表の課題 1 つ、`worktree` を単位の作業場所、`branch` を単位のブランチ、`base` をスプリントのブランチ）の `StageRun` を作り、それで `template_values` を打つ。したがって題・本文は代表の課題について既存の `_issue_values` の信頼の確かめとキャッシュ（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE`）を通り、既定の `review` は単位のブランチで組まれ、設定の `workflow.launch.review`・`test.ci.only` もそのまま効く。スプリントの課題が複数でも、単位ごとに課題 1 つの入力になるので題・本文が欠けない
2. **作業場所**: 実行順 k を始めるときに `git fetch <remote> <sprint>` を**1 回だけ**打ち、`git rev-parse <remote>/<sprint>` の SHA を `units.json` の `orders.<k>.base_sha` に書いて固定する（既に書いてあれば取得も書き換えもしない。再開で起点が動かない）。その実行順の各単位について `ndf worktree create <sprint>-u<seq> --from <base_sha> --no-fetch` で、前の実行順のマージをすべて含む同じコミットから単位専用のブランチと作業場所を用意する（既にあれば作り直さない）。今の `ndf worktree create` は `--from` があっても既定のリモートを取得するので、`--no-fetch`（取得を飛ばし、`--from` の SHA が手元に無ければ `precondition` で止める）を足す。取得は実行順ごとに 1 回で、単位の本数に比例して増えない。単位の PR の宛先はスプリントのブランチ（差し込み `base`）
3. **キュー**: `ndf plan queue <その実行順のプランのパス>... --max <units.json の max> --resume` を打つ（`--resume` は常に付ける。初回はキューの記録が無いので全部流し、打ち直しでは完了したプランを流さずに続ける。今の `ndf plan queue` は `--resume` が無いと完了済みの記録を使わない）。`lane_plans` はスプリントがレーンで動くとき、そのレーンの `max_plans`（並列の計画の `max_plans`、省けば既定）。supervisor が手で流すときは `ndf progress units run` が同じ値を使う
4. **マージの前の記録**: キューが `ok` で終われば、その実行順の単位ごとに、**最初のマージの要求より前に** `work_dir/units/<seq>.json` へ PR の番号（`gh pr view <単位のブランチ> --json number,baseRefName` で 1 回だけ読む）・宛先（スプリントのブランチ）・レビュー済みの HEAD（そのプランの状態ディレクトリの値）・書いた時刻 `recorded_at`（ISO 8601。`ci merge --since` に渡し、これより後の再オープン・宛先の変更を見つける）・状態 `queued` を書く。記録を書けなければマージを要求しない
5. **マージ**: 記録のある単位をマージの順に、**単位の作業場所を cwd にして** `ndf ci merge --branch <単位のブランチ> --pr <記録の番号> --base <スプリントのブランチ> --reviewed <記録の HEAD> --method <units.json の merge_method> --since <記録の recorded_at>` を打つ。返りで記録の状態を書き換える（`ok` は `merged`、マージキューへ入れた・入っている `ci_pending` は `queued` のまま）。判定は下の「マージの対象の照合と再開」が `ci merge` の中で持つので、工程の側で `gh pr view` を打ち直さない
6. **再開と再レビュー**: 記録の状態は `queued`（マージ待ち）・`merged`・`stale`（レビューからやり直す）・`blocked`（人に返す）の 4 つで、遷移は次のとおり。打ち直しは常にこの表から続ける

   | 今の状態と `ci merge` の返り | 次の状態と工程の振る舞い |
   | --- | --- |
   | 記録なし（4 の前に止まった） | 3 のキュー（`--resume`）の結果から始める |
   | `merged` | API を呼ばずに飛ばす |
   | `queued` → `ok` | `merged` |
   | `queued` → `ci_pending` | `queued` のまま。次の実行順を始めずに `ci_pending` で返す |
   | `queued` → `refused`（HEAD がレビュー済みと違う・承認後の再オープン）か `base_conflict` | `stale`（理由と `ci merge` の返りの `instance` を記録に足す）。工程は `precondition` で止まらず、同じ起動でその単位のプランだけを `ndf plan queue <そのプラン> --resume --from <プラン>=<開始点>` で打ち直す。開始点は `refused` なら `review`（今の HEAD をレビューし直す）、`base_conflict` なら `conflict-check`（マージ先の取り込みからレビューまで）。キューが `ok` で終われば、4 と同じく PR を読み直し、プランの状態ディレクトリの新しいレビュー済みの HEAD で記録を `queued` に**書き換えて**から 5 を打つ。古い HEAD のまま同じ `ci merge` を繰り返さない。同じ単位の `stale` は 1 回の起動で 1 回までで、2 回目は `blocked` |
   | `queued` → `refused`（番号・ブランチ・宛先の違い、承認外の宛先へのマージ） | `blocked`。工程は `precondition` で止め、次の実行順へ進まず人に返す（マージ済みでも成功にしない） |
   | `blocked` | 人が記録を消すか直した後の打ち直しまで `precondition` を返し続ける（`ndf progress units run` は理由と記録のパスを出す） |

7. **次の実行順と最後**: その実行順のすべての記録が `merged` になってから 2 へ戻る。最後の実行順まで済めば、スプリントの worktree（工程の cwd）で `git fetch <remote> <sprint>` と `git merge --ff-only <remote>/<sprint>` を打ち、ローカルの HEAD をマージ済みの先端まで進めてから工程の残りのステップ（`gathered`。`ndf progress gathered` は `<remote>/<sprint>` がローカルの HEAD の祖先であることを求める）へ進む。早送りできない（スプリントの worktree に push していないコミットがある）なら止める（`precondition`、理由「スプリントの worktree が <remote>/<sprint> から分かれている」）

| 関数 | 変える前 → 後 | 異常系 |
| --- | --- | --- |
| `_call_plan(run, cwd, work_dir, answer, runner)`（責務の変更） | 前: `plan_path` で 1 本書き `ndf plan next` を打つ。後: 工程が `per_unit` なら `_call_units` へ渡す | — |
| `_call_units(run, cwd, work_dir, runner)`（新設） | 上の 1〜7（2〜7 は `run_units(plans, sprint, remote, cwd, work_dir, runner)` に分け、`ndf progress units run` も同じ関数を呼ぶ） | `UnitsError`・`TemplateValuesMissing` は工程の失敗（`cause.kind: invalid`、理由をそのまま）。queue とマージの失敗は今の `_from_process` のまま返し、次の実行順を始めない |
| `plan_queue_argv(paths, cap, cwd, start: Optional[tuple[str, str]] = None) -> list[str]`（新設） | `ndf_command` と同じ形で `plan queue … --max <cap> --resume`（`start` があれば `--from <プラン>=<ステップ>` も）の argv | 投げない |
| `unit_merge_argv(branch, number, base, reviewed, method, since) -> list[str]`（新設） | `ndf_command` と同じ形で `ci merge --branch <branch> --pr <number> --base <base> --reviewed <reviewed> --method <method> --since <since>` の argv。打つ cwd は呼び手が単位の作業場所にする | 投げない |
| `_call_plan` の環境（責務の変更） | 前: `ndf.plan` の `ndf plan next` に工程の入力を渡さない（`NDF_STAGE_INPUT` は `ndf.script` だけ）。後: `ndf.script` と同じく `run.env({INPUT_ENV: json.dumps(run.inputs)})` を渡す。プランのステップの子（`ndf plan units check --from-stage` など）は環境を引き継ぐ | — |

#### worker への単位の指示（`impl.json`）

- `params.json` に `unit_file`（「単位の指示のファイル。空なら課題の全体を実装する」、`"default": ""`）を足す。既定を持つので、`unit_file` を渡さない従来の工程（`light`・`operation`・`documentation` の `implement.json`）と手動の `ndf route render impl` は値不足で止まらない
- `impl.json` の `worker` の前にステップ `unit-prompt` を足す: `["sh", "-c", "test -z \"$0\" || exec ndf plan units prompt \"$0\" --append \"$1\"", "{{unit_file}}", "{state_dir}/impl-prompt.md"]`。`unit_file` が空なら何もせずに通る
- `ndf plan units prompt <指示のファイル> --append <プロンプト>`（`core/plan/cli.py`。Task 8）は、指示のファイルの担当のタスク（`id`・`title`・`files`・`criteria`）・実装計画の文書のパス・「このファイルの tasks だけを実装し、ほかのタスクに手を付けない」の一文を、worker が読むプロンプト（`ndf answer work` が `--prompt-file` で渡す `<state_dir>/impl-prompt.md`）の末尾へ節「担当のタスク」として足す。worker は今の作業名 `impl` のまま、そのプロンプトから担当のタスクを読む（作業名・`ndf worker run` の引数は変えない）。打ち直しで同じ節を二重に足さない（節の見出しがあれば置き換える）

`stages.py` の工程の型と `definition.py:321` の読み手に `per_unit` を足す（既定は偽）。`per_issue` は改名せずに残す（`ndf.schema.json` の既存のキーと、保存済みのスナップショットの `per_issue` を今のまま読めるようにするため。`_stage_from` はキーをそのまま `StageDef` に渡すので、`per_unit` を持たない古いスナップショットは既定の偽で読める）。

### マージの対象の照合と再開（`ndf ci merge --pr --base`）

満たす決定: 1・7。設計 PR（`design-land`）と単位の PR（工程「実装」）の両方が使う。今の `ci merge --branch` はブランチの開いた PR を引き直してその最初の PR をマージし、マージ済みなら開いた PR が無いので止まる。承認・レビューした PR と違う PR をマージしない、マージキューの後に打ち直せる、の 2 つのため `--pr <番号>`（承認・記録した PR）と `--base <宛先>` を足す。

**引数の組み合わせ**: 経路は 2 つだけで、どちらにも当たらない呼び出しは PR を読まずに `stopped`（`invalid`、「--pr・--base・--reviewed・--method・--since はそろえて渡す」）で止める。

| 経路 | 受け付ける引数 | 振る舞い |
| --- | --- | --- |
| 照合の経路 | `--branch`・`--pr`・`--base`・`--reviewed`・`--method`・`--since` のすべて。または `--target <ファイル>` だけ（設計の承認の記録。同じ 6 つの値をそのファイルから読む） | 下の表で判定する |
| 従来の経路 | `--branch`（`--reviewed`・`--method` は任意）。`--pr`・`--base`・`--since`・`--target` を渡さない | 今のまま（ブランチの開いた PR を引いてマージする） |
| 止める | 照合の経路の値が 1 つでも欠ける、または `--target` とほかの照合の値を併せて渡す | `invalid`。宛先・方式を照らさずにマージする呼び出しを作らない |

`--method` は既存のマージの部品（`parts/steps.json` の `merge`）と同じく、宛先の宣言から `route.default_merge_method` で決めた方式である（単位は `units.json` の `merge_method`、設計 PR は承認の記録の `method`）。

**判定**（照合の経路。作業場所の HEAD と `--reviewed` の確かめは今のまま先に行う）: PR を `GitHub.pull(番号)` で 1 回読み、その読み取り結果 `pull` を判定と要求の両方に使う。判定は上の行から順に当て、最初に当たった行で返す。マージ済みかどうかより先に head のブランチ・宛先・HEAD を照らす（今の `_preflight` の「マージ済みなら成功」を照合の経路では使わない）

| PR の状態（読んだ `pull`） | 返り | 読む API |
| --- | --- | --- |
| head のブランチが `--branch` と違う・宛先（`baseRefName`）が `--base` と違う | `refused`（「#<番号> は承認した対象（<ブランチ> → <宛先>）と違う」）。`MERGED` でも同じ（承認外の宛先へ入ったものを成功にしない）。`OPEN` なら `GitHub.dequeue(番号)` で外してから返す | `dequeue` の読み 1（入っていれば外す要求 1） |
| `headRefOid` が `--reviewed` と違う（`MERGED`・`OPEN` のどちらも） | `refused` | なし |
| `CLOSED`（マージされていない） | `refused` | なし |
| `MERGED` | `ok`（「既にマージ済み」。マージキューが済ませた後の打ち直し） | なし |
| `OPEN` で、本文に決めていない「マージの前に決める」残リスクの行（`- [ ]`）が残る | `refused`（今の `_preflight` と同じ判定と理由。残った行を出す）。要求を送らない | なし |
| `OPEN` で `mergeStateStatus` が `dirty` | `stopped`（`base_conflict`、要求を送らない）。衝突の判定もこの読み取りで行う（下の `design-land`） | なし |
| `OPEN` で、`--since` より後に再オープン・宛先の変更の事象がある | `refused`（「#<番号> は承認の後に開き直された／宛先を変えられた」）。キューに入っていれば外してから返す | 下の「キューと事象の読み」1（外すときは要求 1） |
| `OPEN` で、キューに入っている | `ci_pending`（マージの要求を打ち直さない） | 同じ読みの結果を使う |
| `OPEN` で、宛先がキューを持ち、入っていない（入っていない・外された） | 同じ `pull` の HEAD で `enqueuePullRequest`（`expectedHeadOid`）を送り `ci_pending`。方式はキューが決める（今の `require_method` が偽の部品と同じ） | 登録の要求 1 |
| `OPEN` で、宛先がキューを持たない | 方式を `allowed_merge_methods(--base)` で読み、`pick_merge_method(--method, allowed)` が None なら要求を送らずに `refused`（今の `_method_refusal`。宣言と違う方式でマージしない）。合えば同じ `pull` で直接マージを要求し、成功したら PR を 1 回読み直して宛先を照らす（下の「要求の時点の宛先」） | 方式の読み 2（ruleset・リポジトリの設定）・要求 1・読み直し 1 |

**キューと事象の読み**: 今の `_MERGE_QUEUE_STATE` の GraphQL（`isMergeQueueEnabled`・`isInMergeQueue`）に、`timelineItems(since: --since, itemTypes: [REOPENED_EVENT, BASE_REF_CHANGED_EVENT])` の件数を足した 1 本の問い合わせ（`GitHub.merge_state(pull, since) -> MergeState`）にする。再オープン・宛先の変更・キューの状態を 1 回で読み、照合の経路の `OPEN` の行はどれもこの読みを 1 回だけ使う。閉じて開き直した PR は、番号・ブランチ・宛先・HEAD が同じでも `REOPENED_EVENT` で止まる（承認の後の状態の変更を照らす情報はこの事象で、承認の記録の時刻 `--since` より後のものだけを数える）。

**同じ読み取りを使う責務**（`adapters/github.py`）: `GitHub.merge_approved(pull: Pull, state: MergeState, base: str, reviewed: str, method: str) -> MergeOutcome` を足す。`ci_cli._merge` は照合の経路で `pull` を 1 回読んで上の表の前半（宛先・HEAD・状態・残リスク・衝突）を判定し、`OPEN` のときだけ `merge_state` を 1 回読み、要求するときに同じ `pull` と `state` を `merge_approved` へ渡す。`merge_approved` は、キューがあれば登録、無ければ方式を読んで `_merge_with` の本体を `pull` を受け取る形（`_merge_pull(pull, expect_head, method, route)`）に切り出したものを呼び、PR を読み直さない（`_merge_with` は `self.pull(number)` の後に `_merge_pull` を呼ぶ形にして、従来の経路の振る舞いを変えない）。方式の読みはキューを持たない宛先へ直接マージするときだけで、マージ済み・待機中・衝突・照合の失敗の早期の返りでは読まない。方式の読みと要求の間に別の API 呼び出しを挟まない。要求が断られて PR を読み直すとき（今の `after = self.pull(number)`）は、読み直した PR を上の表の前半で照らし直してから成否を決める: 宛先か head のブランチか HEAD が違えば `refused`、`MERGED` で 3 つが同じときだけ `ok`、ほかは今の `refusal_kind` の種類

**要求の時点の宛先**: GitHub の直接マージ（REST の `PUT /pulls/{n}/merge` の `sha`）とキュー登録（`enqueuePullRequest` の `expectedHeadOid`）は HEAD を条件にできるが、宛先を条件にする引数を持たない。GitHub の API で宛先を要求の時点まで固定する手段は無いので、**自動マージを許す条件**を「照合した宛先が、要求が処理されるまで NDF の外から変えられない」とし、次のとおり経路ごとに満たせるかを決める。宛先の変更は PR の書き手か書き込み権限を持つ人だけができ、NDF の工程は記録済みの PR の宛先を変える操作を持たない（`gh pr edit --base` を打つステップを置かない。`test_sprint_templates.py` で全テンプレートの argv に無いことを縛る）。したがって条件を破れるのは、書き込み権限を持つ人のうっかりの操作だけである

| 経路 | 条件を満たせるか | 満たせない分の扱い |
| --- | --- | --- |
| 直接マージ（宛先にキューが無い） | 満たせる範囲: 照合と要求を同じ `pull` から打ち、間に API 呼び出しを挟まないので、変えられる窓は要求 1 往復の中だけになる | 窓の中の変更は防げない。要求の成功の後に PR を 1 回読み直し、`baseRefName` が `--base` と違えば `refused`（「#<番号> が承認外の宛先 <宛先> へマージされた」）。マージは戻せないので、工程は `precondition` で止め（単位なら記録を `blocked`）、次の実行順・スプリントの worktree の早送り・`gathered` へ進まず、戻すかどうかを人に返す（自動で revert しない） |
| マージキュー | **満たせない**: 登録からキューがマージするまでの間（分の単位）、宛先の変更を見張る手段が再開の照合しかない | 登録の後は、`ci_pending` で返った工程の打ち直しのたびに 1 行目と事象の行で照らし、違えば `dequeue` して `refused`。キューがマージした後に見つけた違いも `refused` で人に返す。この経路は「満たせない経路」として承認資料に明記し（設計 PR・単位の宛先がキューを持つとき「宛先がキューを持つので、登録の後の宛先の変更は再開の照合で見つける」と載せる）、承認者が直接マージで流したいときは宛先のキューを外す判断を人に委ねる |

**API の回数**（照合の経路。PR の読み 1 を含む）: マージ済みの打ち直し 1、照合の失敗・残リスク・衝突 1、キューの待ち 2（PR・キューと事象）、キューへの登録 3（PR・キューと事象・登録）、直接マージ 6（PR・キューと事象・方式 2・要求・読み直し）。今の従来の経路の直接マージ 4（PR・方式 2・要求）より、キューと事象の読みと読み直しの 2 回多い。偽の GitHub で呼び出しを数え、返りごとにこの回数をテストで縛る（マージ済み・待機中で方式を読まないことを含む）。

### 設計 PR の承認とマージ（`design-review`・`design-land`）

満たす決定: 1。満たす受け入れ条件: 1。工程「ドキュメントレビュー」（`design-approval`）のステップの中身である。

#### ブランチと作業場所

今の工程「作業場所の用意」が出すのは `worktree` と `branch` の 1 組（スプリントのブランチとその作業場所）で、後の工程もその作業場所で書く。設計 PR をスプリントへ入れるには、head と宛先を別のブランチにする必要があるので、次の 2 組に分ける。

| 組 | ブランチ | 作業場所 | 作る工程 | 使う工程 |
| --- | --- | --- | --- | --- |
| スプリント | `branch`（今のまま） | `worktree`（今のまま） | 作業場所の用意 | 要求・実装（単位の作業場所の起点と、最後の早送り）・その後の工程。設計の承認の後にここで承認済みのファイルを読む |
| 設計 | `design_branch` = `<branch>-design` | `design_worktree` = `.worktrees/<branch>-design` | 設計（`design-draft` の最初のステップが `ndf worktree create <branch>-design --from <remote>/<branch>` で作る。既にあれば作り直さない） | 設計・詳細設計・計画・ドキュメント再構成・ドキュメントレビュー。設計 PR の head |

- 設計 PR の宛先 `design_base` は**宣言から決める**: `branch_strategy.pr_target("doc", sprint_branch=branch)`。`branch_strategy.sprint.doc_prs` が `sprint` ならスプリントのブランチ、`base` ならベースブランチ。`design-draft` はこの値を `base` として明示して PR を開き（`route.derived` の既定の `pr_target("sprint")` に任せない）、`design_base` を出力する。`design-land` の `--base` も同じ `design_base` を使うので、PR を開いた宛先と照らす宛先がずれない
- 工程「詳細設計」「計画」「ドキュメント再構成」「ドキュメントレビュー」は、工程の定義の `worktree_input: "design_worktree"`（`ndf.schema.json` に足す。省けば今のまま `worktree`）で、テンプレートの `worktree`・`branch` を設計の組から組む
- **マージの後に承認済みのファイルをスプリント側へ揃える**（ステップ `design-sync`。`ndf progress design sync`）: スプリントの作業場所で `git fetch <remote> <design_base>` を打ち、`doc_prs` が `sprint` なら `git merge --ff-only <remote>/<branch>`、`base` なら `git merge --no-edit <remote>/<design_base>` の後に `git push <remote> <branch>`（今の部品 `base-sync` がスプリントの PR に打つのと同じ取り込み）。どちらも最後に `git diff --quiet <design_head> HEAD -- <承認の記録の files>`（計画・単位のファイル・設計文書）で、承認したファイルがスプリントの HEAD で同じかを確かめ、違えば `precondition`（「承認した <パス> がスプリントで変わっている」）で止める。工程「実装」はスプリントの作業場所の `collect` でこのファイルを読む
- `doc_prs: base` を選んだプロジェクトも宣言を変えずに使える。マージ先は宣言のまま（ベースブランチ）で、スプリントへは `design-sync` の取り込みで届く。移行の手順は要らない

#### ステップ

| ステップ | 中身 | 失敗したとき |
| --- | --- | --- |
| `docs-review`（テンプレート `design-review`） | ① `{{review}} --findings {{rebuild_findings}}`（既定の起動は `--purpose design`。`rebuild_findings` は工程「ドキュメント再構成」の出力）→ ② 直しの push → ③ `ndf plan units check --from-stage --docs --out {{stage_dir}}/units-check.json`（**最終の HEAD の単位のファイルと表を 1 回だけ確かめる**。工程の入力 `issues`・`plan_omitted` は `_call_plan` が渡す `NDF_STAGE_INPUT` から読む）→ ④ Draft を外す（`gh pr view <design_pr> --json isDraft` が真のときだけ `gh pr ready <design_pr>`。Ready 済みの PR での打ち直しでは打たずに進む）→ ⑤ conflict-check → ⑥ ci（既存の部品。結果は `{state_dir}/ci.json`）→ ⑦ `ndf progress design target`（下の「HEAD の照合」。通れば承認の記録を書く） | ③ が止まれば修正の worker が単位のファイルか文書を直して ② へ戻る。⑥ の失敗は ci-fix → push → ① へ戻る（HEAD が変わったのでレビューと ③ をやり直し、④ は Ready 済みなので飛ばす）。マージ先の取り込み（base-sync）も ① へ戻る。⑦ の照合が食い違えば ① へ戻る |
| `design-mvv` | 今のまま（`ndf records mvv gate --gate design`） | 今のまま |
| `design-gate` | 承認を 1 回だけ求める。承認資料は承認の記録（`design-target.json`）と、そこから指す `units-check.json` だけから作る（下の「承認資料」）。回答の事象に記録の `pr`・`base`・`head` と記録のファイルの SHA-256 を写す | 差し戻しは今のまま。課題を外した回答は下の「承認を無効にする遷移」 |
| `design-merge`（テンプレート `design-land`） | 設計の作業場所で `ndf ci merge --target {{stage_dir}}/design-target.json` の 1 ステップだけ（判定は「マージの対象の照合と再開」。`--pr`・`--branch`・`--base`・`--reviewed`・`--method`・`--since` は記録から読む）。打つ前に `invoke` が、記録のファイルの SHA-256 が承認の回答の事象に写した値と同じかを確かめ、違えば打たずに `stale_approval` で失敗する。部品の `conflict-check`（ブランチ名で `gh pr view` を打ち、`DIRTY` なら `base-sync` へ）は置かない: 記録済みの `design_pr` を照らす前にブランチ名で別の PR を引く CLI の起動と API の呼び出しが再開のたびに増え、`DIRTY` の判定が照合より先に走るためである。衝突は `ci merge` が照合と同じ PR の読み取りの `mergeStateStatus` で判定し、`base_conflict` を返す。承認のステップ（`merge-gate`）を持たず、プランの中で承認を聞き直さない。マージキューの待ち（`ci_pending`）で返った工程の打ち直しは `design-merge` から続けて同じ argv を打ち、マージ済み（PR が `MERGED` で HEAD が記録の `head`）なら成功として次へ進む | `refused`（番号・ブランチ・宛先・HEAD・再オープン・残リスク・方式）と `base_conflict` は `stale_approval` に写して失敗する（下の「承認を無効にする遷移」）。ただし「承認外の宛先へマージされた」は `precondition` で止め、人に返す（戻せないため開き直さない） |
| `design-sync` | 上の「マージの後に承認済みのファイルをスプリント側へ揃える」 | `precondition` で止まる（実装へ進まない） |

#### 設計の承認の記録（`design-target.json`）

工程のステップの入力は工程を始めたときの文脈から組まれ、ステップの出力が文脈へ入るのは工程の終わりである（`progress/run.py` の `_run_step` と `_finish_stage`）。またプランの結果の `metrics`・`items` は工程へ渡る `outputs` に含まれない。そこで同じ工程の中で `docs-review` の結果を `design-gate` と `design-merge` へ渡すのは文脈でなく、工程ごとに 1 つのディレクトリ `{{stage_dir}}`（`invoke` が `work_root/<機械>/<工程の id>/shared` を差し込む。ステップの作業ディレクトリ `step_location` と別で、工程の中のすべてのステップが同じ場所を指す）に置いたファイルとする。

| ファイル | 書く | 読む | 中身 |
| --- | --- | --- | --- |
| `units-check.json` | ③ | ⑦・承認資料 | `units check` の結果 JSON（`metrics`・`items`・`warnings` をそのまま） |
| `design-target.json` | ⑦（4 つの SHA が同じときだけ。違えば書かず、前の記録も消す） | 承認資料・`design-merge`・`design-sync` | `{"pr", "branch": design_branch, "base": design_base, "head", "method", "since"（書いた時刻）, "files"（計画・単位のファイル・設計文書のパス）, "units_check_sha256"}` |

- 承認資料と `design-merge` は同じ `design-target.json` を読み、回答の事象に写した SHA-256 で同じ中身であることを確かめる。`design_head` は `design-target.json` の `head` を工程の出力として文脈に書く（工程の終わりで入る。後の工程の参照用で、マージはファイルの値で打つ）
- `ndf ci merge --target` は `units_check_sha256` を `units-check.json` で照らし、違えば `refused`（承認の後に確かめの結果が書き換わった）

**HEAD の照合（⑦。`ndf progress design target --pr <design_pr> --branch <design_branch> --base <design_base> --check {{stage_dir}}/units-check.json --ci {state_dir}/ci.json --out {{stage_dir}}/design-target.json`）**: 次の 4 つの SHA が同じときだけ記録を書く。`units-check.json` の `metrics.head`（単位を確かめた HEAD）、ローカルの HEAD、`gh pr view <design_pr> --json headRefOid`（PR の HEAD）、`ci.json` の `instance`（CI を判定した PR の HEAD）。今の `ci_cli._report` は失敗のときだけ `instance` に SHA を入れるので、**成功の結果にも判定に使った `pull.head_sha` を `instance` に載せる**（Task 7。`ci watch` の成功を読むほかの呼び手は `instance` を読まないので振る舞いは変わらない）。どれかが違えば（⑥ の後に push があった、PR を別の HEAD で更新した）記録を書かずに `stopped`（`stale`）を返し、① へ戻る。`method` は `route.default_merge_method(repo, design_base)`、`since` は書いた時刻。承認の記録はこの `head` と組なので、承認した PR の HEAD・CI が通った HEAD・単位を確かめた HEAD は 1 つに結び付き、`design-land` が同じ組を照らす。

**承認資料**: `asking.py` は設計承認ゲートの資料に、設計 PR の URL・`head`・宛先・宛先がマージキューを持つか（持てば「満たせない経路」の文）と、`units-check.json` の**スプリント全体の**単位の表を載せる。表は `unit` の項目の `seq`・名前・ファイル・実行順・マージの順（実装が使う番号）・並列の相手（`parallel_with`）・先に要る単位・条件・行数と、同時に流す本数の上限（`metrics.parallel`）である。実装計画の文書の表はファイルの中で番号を振るので、承認者が実装の順を見るのはこの資料の表とする（2 ファイルに独立な単位が 1 つずつあれば、文書ではどちらも「マージの順 1」だが、資料では `seq` 1・2 の並列の相手どうしとして出る）。

**手で流す経路（#3908 でテンプレートが動かない間）**: supervisor が工程の外で同じ検査・停止・照合を打つ。手順は Task 9 で `references/approval-request.md` に書き、どの手順も工程と同じコマンドなので結果は同じになる

1. 設計の作業場所で最終の HEAD を push した後に `ndf plan units check --issue <課題>... [--omitted <課題>]... --docs --out <dir>/units-check.json`。`ok` でなければ承認を求めず、書き手に直させてからやり直す
2. `ndf ci watch --pr <design_pr> ... | tee <dir>/ci.json` が `ok` の後に `ndf progress design target ...`（上と同じ引数）。`stopped` なら 1 からやり直す
3. 承認資料は `<dir>/design-target.json` と `units-check.json` から `ndf answer ask` で作る（資料に記録の SHA-256 が載る）
4. 承認の後に `ndf ci merge --target <dir>/design-target.json`。`refused`・`base_conflict` なら 1 から（承認を取り直す）
5. `ndf progress design sync --target <dir>/design-target.json` でスプリント側へ揃える

#### 承認を無効にする遷移

今の進行管理は、完了した工程を再実行せず（`decide` は最初の未完了の行を選ぶ）、失敗した工程は失敗したステップから再開する。また `gate/answering.py` は課題を外した回答を承認として記録し、文脈の `issues` だけを書き換える。古い承認と工程の出力を使わずに戻すため、行を開き直す事象を足す。

- **事象 `reopened`**（`progress/run.py`）: 工程の行を `pending` に戻す。`step_progress` はその行の最後の `reopened` より後の事象だけを数えるので、ステップの進み・待ち・回答（古い承認）は捨てられる。文脈（`View.context`）は、開き直した工程が出した出力をその `reopened` より後に書き直されるまで重ねない（`design_head` など古い出力を後の工程が読まない）。開き直した工程の `{{stage_dir}}` の `design-target.json` と `units-check.json` は `reopened` を書くときに消す
- **`restart_on`**（工程の定義のメタデータ。`design-approval.json` に `{"design-merge": ["stale_approval"]}`）: ステップがこの種類で失敗したら、`run.py` はその工程に `reopened` を書く。次の起動は工程の最初のステップ（`docs-review`）から流れ、レビュー・MVV 判定・承認をやり直す

| 起きること | 遷移 | 捨てるもの |
| --- | --- | --- |
| 承認で課題を外した（`answering.py` が外した課題を `issues` から除く事象を書いた直後） | 同じ書き込みで「計画」「ドキュメント再構成」「ドキュメントレビュー」の 3 行に `reopened`。回答は承認として消費しない（`design-merge` を打たない）。次は「計画」から、残った課題で実装計画と単位のファイルを書き直す（外した課題のタスク・条件・依存を除く。統合の計画なら名前の番号の並びも書き直す） | 3 つの工程の出力（`plan_omitted`・`rebuild_findings`・`design_head`）と、ドキュメントレビューの承認の記録・回答 |
| `design-merge` が照合で断られた・衝突した（`stale_approval`） | `restart_on` で「ドキュメントレビュー」に `reopened`。次は `docs-review` から | 承認の記録・回答 |
| 承認外の宛先へマージされた | `precondition` で止める（開き直さない）。人が戻すかを決め、決めた後に conductor が `reopened` を書くか工程を進める | なし |
| 回答し直しで外した課題を戻した（今の `_restored`） | 今のまま `issues` を戻し、上と同じ 3 行に `reopened` | 同上 |

変更の無い経路（レビューが収束し CI が通り、衝突が無い）では、承認は `design-gate` の 1 回だけである。

### 工程の定義（`contracts/workflow/`）

満たす決定: 1。満たす受け入れ条件: 1・4。

| 定義 | 変えた後 |
| --- | --- |
| `modes/standard.json` | 要求 → 作業場所 → 設計（`design-draft`）→ 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ …（行の数 18 は変わらない） |
| `modes/legacy-refactor.json` | 作業場所 → 設計（`design-draft`）→ 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ … |
| `modes/light.json`・`operation.json`・`documentation.json` | 変えない（`design.json`・`document-review.json`・`implement.json` を指したまま。`documentation` の「計画」はドキュメントレビューの後のまま） |
| `stages/plan.json` | `phase: design`。ステップ `plan`（`implementation-plan`）だけ。`outputs: ["plan_omitted"]`（実装計画を省いた課題。省かなければ空の配列）。実装計画を書くのはこの工程だけで、工程「設計」は書かない（設計書を省いた課題も同じ） |
| `stages/design-draft.json` | ステップ `design`（`design-draft`）だけ。`outputs: ["design_pr", "design_worktree", "design_branch", "design_base"]` |
| `stages/design-detail.json`・`plan.json`・`document-rebuild.json` | `worktree_input: "design_worktree"`（設計の作業場所で書く。上の「ブランチと作業場所」） |
| `stages/design-approval.json` | `inputs`: `issues`・`worktree`・`branch`・`design_worktree`・`design_branch`・`design_base`・`mode`・`pace`・`mvv_ref`・`design_pr`・`rebuild_findings`・`plan_omitted`。`worktree_input: "design_worktree"`（`design-sync` だけはスプリントの `worktree` で打つ）。`docs-review` → `design-mvv` → `design-gate`（modes は `legacy-refactor`・`standard`）→ `design-merge` → `design-sync`。`restart_on: {"design-merge": ["stale_approval"]}`。`outputs: ["mvv_gate", "design_head"]` |
| `stages/implement-units.json` | `implement.json` と同じステップに `per_unit: true`。`inputs` に `plan_omitted` を足す |
| `stages/document-rebuild.json` | `outputs` に `rebuild_findings` を足す（指摘が無くても空の指摘のファイルを書く。`documentation` で読まれなくても害が無い） |

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
| 承認で課題を外した | 承認として扱わず工程「計画」へ戻り、残った課題で計画を書き直してから承認を求め直す |
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
| マージの照合 | 同じ番号・ブランチ・宛先・HEAD で `--since` の後に再オープンした PR は `refused`。本文に未決の「マージの前に決める」が残ると要求を送らずに `refused`。`--method` が許されない方式なら要求を送らずに `refused`。API の回数がマージ済み 1・待ち 2・登録 3・直接 6 で、マージ済みと待ちで方式を読まない。`ci watch` の成功の結果の `instance` が判定した HEAD。`ci merge` が、番号か宛先の違いで `refused`、マージ済みで HEAD が同じなら `ok`、キューの中なら要求を打たずに `ci_pending`、キューから外れていれば要求し直す。`--base` だけ・`--pr` だけ・`--reviewed` の無い照合の呼び出しは PR を読まずに `invalid`。別の HEAD か別の宛先で `MERGED` の PR は `refused`（`ok` にしない）。要求で PR を 1 回も読み直さない（偽の GitHub で `pull` の読みが照合の 1 回だけ）。要求が断られた後の読み直しで別の HEAD のマージ済みを見ると `refused`。直接マージの後の読み直しで宛先が違えば `refused`、キューの中で宛先が変われば `dequeue` して `refused`。`dirty` の PR は要求を送らずに `base_conflict` |
| プラン | 2 つのファイルからのプランの名前と出力のパスが重ならない。同じ課題の 2 単位で指示のファイルの担当のタスクと `acceptance` が分かれる。条件を持たない課題に `--issue` が付かない。`single` の `files` が空なら `touches` を書かない |
| 流し方の記録 | `units plans` の後の別の起動の `units run --out` だけで、実行順・マージの順・上限・方式を `units.json` から復元して流す。実行順ごとの `git fetch` が 1 回で、同じ実行順の作業場所が `base_sha` の同じコミットから `--no-fetch` で作られる。HEAD の違いで `refused` の単位が `stale` になり、そのプランだけを `--from <プラン>=review` で打ち直して記録の HEAD を書き換えてからマージし、古い HEAD で `ci merge` を繰り返さない。`--resume` で完了したプランを流さない |
| worker の指示 | `unit_file` を渡さない `ndf route render impl` が通り、`unit-prompt` が何もしない。渡すと `impl-prompt.md` に担当のタスクの節が 1 回だけ入る |
| 実際のキュー | A → B の 2 単位を、別々の `ndf plan queue` の起動（A の後にマージの記録を置いてから B）で流すと B が流れる。依存の無い 2 単位は同じ起動で同じ組に入る |
| 工程の流し方 | 単位ごとに作業場所とブランチが分かれ、`review` の argv が単位のブランチを持ち、`ci merge` を単位の作業場所で `--pr <記録の番号>` 付きで打つ。PR の番号とレビュー済みの HEAD の記録がマージの要求より前に書かれる。A をマージ済みにして B で止めた後の打ち直しで A に `ci merge` を打たない。`ci_pending` では次の実行順を始めない。`--max` がレーンの `max_plans` とマシンの上限の小さい方。最後のマージの後にスプリントの worktree を早送りしてから `gathered` が `ok` になる。`ndf progress units plans` で 2 課題の単位の題・本文・`review` が単位ごとに違い、単位ごとの差し込みを `--set` で与えると止まる。`ndf progress units run` が実行順 1 のマージの後に実行順 2 を流す |
| 承認とマージ | `design-draft` が宛先 `pr_target("doc")`（`doc_prs: sprint` でスプリント・`base` でベース）で設計の作業場所から PR を開き、`design-land` の `--base` が同じ値。`doc_prs: base` でマージの後に `design-sync` がスプリントへ取り込み、承認したファイルが同じことを確かめる。`ci watch` が成功した後に `design target` が 4 つの SHA をそろえて記録を書き、承認まで進む。承認資料の表がスプリント全体の `seq`・並列の相手・上限を出す。記録を書き換えると `design-merge` が `stale_approval` で「ドキュメントレビュー」を開き直す。課題を外した回答で「計画」「ドキュメント再構成」「ドキュメントレビュー」が `reopened` になり、古い `design_head` と回答を使わない。`design-review` の既定のレビューの argv が `--purpose design --findings <再構成の指摘>` を持つ。Draft を外すステップが CI の前にあり、Ready 済みの PR では `gh pr ready` を打たない。CI を落として直した後、レビューと `units check` をやり直して承認まで進む。4 つの HEAD のどれかが違えば `design_head` を書かない。変更の無い経路で承認が 1 回。マージの argv が `--target <stage_dir>/design-target.json`。HEAD を変えるとマージせずに止まる。マージキューの待ちの後の打ち直しでマージ済みを成功にする。`design-land` のプランが `conflict-check` を持たず、再開で打つ外部の起動が `ci merge` の 1 回だけ。承認で課題を外すと「計画」から流れ直す。本物の `ndf` で `design-review` のプランを流し、`units check --from-stage` が工程の `issues` を受け取る |
| 工程 | 5 つのモードを `test_sprint_templates.py` の偽の流しで最後まで流す。`standard`・`legacy-refactor` で「計画」の位置と phase、設計承認ゲートのステップが「ドキュメントレビュー」に 1 つ、`mvv_gate` が「ドキュメントレビュー」の出力。`light`・`operation`・`documentation` の並びとステップが変わらない。`legacy-refactor` が `design_pr` と `rebuild_findings` を出して `design-approval` と `implement-units` を最後まで流す。`per_issue` だけを持つ保存済みのスナップショットを読める |
