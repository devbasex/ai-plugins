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
│   ├── templates/design.json                         # review・ci を外し、PR を開くまでにする
│   ├── templates/implementation-plan.json            # worker の指示に単位のファイルと render を足し、check を足す
│   └── workflow/
│       ├── review-units.schema.json                  *
│       ├── modes/{standard,legacy-refactor}.json     # 計画を詳細設計の直後へ
│       └── stages/{plan,design,document-review,implement}.json
├── src/ndf/
│   ├── core/plan/
│   │   ├── units_decide.py                           *（純粋な判定）
│   │   ├── units_files.py                            *（ファイル・宣言・文書・プランの読み書き）
│   │   ├── cli.py                                    # ndf plan units check / render / plans
│   │   └── inputs_decide.py                          # design_gaps の単位の数の規則を消す
│   └── progress/
│       ├── stages.py / definition.py                 # per_issue を per_unit へ
│       └── invoke.py                                 # per_unit の工程は単位ごとのプランを queue で流す
└── tests/
    ├── core/plan/{test_units_decide.py*, test_units_cli.py*, test_inputs.py}
    ├── core/plan/fixtures/units/issue-3672.units.json *
    └── progress/{test_definition.py, test_command.py, test_sprint_templates.py, test_invoke.py}
```

依存の向きは一方向である。`units_decide` は標準ライブラリ（`graphlib`）と `inputs_decide.DesignResult` だけを読む。`units_files` は `units_decide`・`ndf.contracts`（スキーマと上限）・`ndf.core.route`（impl のテンプレートの描画）を読む。`cli.py` と `progress/invoke.py` は `units_files` を読み、`units_files` はどちらも読まない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `contracts/workflow/review-units.schema.json` | 新設 | 単位のファイルの形（`version`・`issues`・`tasks`・`units`） | 無し |
| `contracts/limits.json` | 変更 | `review-units.target-lines`（`count: 400`）・`review-units.max-lines`（`count: 1000`） | 無し |
| `contracts/schemas/project-workflow.schema.json` | 変更 | `workflow.review_units`（`target_lines`・`max_lines`。整数 1 以上） | 無し |
| `contracts/schemas/ndf-settings/project.schema.json` | 変更 | `workflow` の `properties` に同じ `review_units` を足す | 無し |
| `src/ndf/core/plan/units_decide.py` | 新設 | 実行順・導く値・違反・警告・分け方の案 | `graphlib`・`inputs_decide` |
| `src/ndf/core/plan/units_files.py` | 新設 | 単位のファイルを探す・読む、上限を読む、文書の表を作る、単位のプランを書く | `units_decide`・`ndf.contracts`・`ndf.core.route` |
| `src/ndf/core/plan/cli.py` | 変更 | サブコマンド `units`（`check`・`render`・`plans`） | `units_files` |
| `src/ndf/core/plan/inputs_decide.py` | 変更 | `design_gaps` から「PR の単位が 2 つ以上」の理由を消す（決定 2） | 無し |
| `contracts/workflow/stages/plan.json` | 変更 | `phase: design`。`plan` の後に `units-check`（`ndf plan units check --from-stage`） | 無し |
| `contracts/workflow/stages/design.json` | 変更 | ステップを `design` だけにし、`outputs` を `design_pr` だけにする | 無し |
| `contracts/workflow/stages/document-review.json` | 変更 | ステップを `docs-review`（設計 PR）→ `design-mvv` → `design-gate` → `design-merge` にし、`inputs` に `mode`・`pace`・`mvv_ref`・`design_pr`、`outputs` に `mvv_gate` | 無し |
| `contracts/workflow/stages/implement.json` | 変更 | `per_issue` を `per_unit` にする | 無し |
| `contracts/workflow/modes/standard.json`・`legacy-refactor.json` | 変更 | 「計画」を「詳細設計」の直後へ移す | 無し |
| `contracts/templates/design.json` | 変更 | `review`・その後の push・conflict-check・ci を外し、PR を Draft で開くまでにする（レビューは工程「ドキュメントレビュー」） | 無し |
| `contracts/templates/implementation-plan.json` | 変更 | worker の指示に「単位のファイルを書き、`ndf plan units render` で表を作る」を足し、push の前に `ndf plan units check` と `render --check` のステップを置く | 無し |
| `src/ndf/progress/stages.py`・`definition.py` | 変更 | 工程の型の `per_issue` を `per_unit` へ改名する | 無し |
| `src/ndf/progress/invoke.py` | 変更 | `per_unit` の工程の `ndf.plan` のステップを、単位ごとのプランと `ndf plan queue` で流す | `units_files` |
| `tests/core/plan/test_units_decide.py` | 新設 | 判定の単体テスト（受け入れ条件 2・5・7・8、U1〜U6・W1〜W2） | fixture |
| `tests/core/plan/test_units_cli.py` | 新設 | コマンドのテスト（受け入れ条件 3・4・6、`render --check`） | fixture |
| `tests/core/plan/fixtures/units/issue-3672.units.json` | 新設 | #3672 の詳細設計の地図から作った 1 単位のファイル（受け入れ条件 8） | 無し |
| `tests/core/plan/test_inputs.py` | 変更 | `design_gaps` の単位の数のテストを消す | 無し |
| `tests/progress/test_definition.py`・`test_command.py`・`test_sprint_templates.py`・`test_invoke.py` | 変更 | 工程の並び・ゲートの位置・出力のキーの移動・`per_unit` の流し方 | 無し |

手順書（`skills/` の 8 ファイル）の変更は実装計画のタスクにあり、関数を持たないのでこの文書に下ろさない。

## 構成要素ごとの仕様

### 単位のファイルの形（`review-units.schema.json`）

満たす決定: 2・3。満たす受け入れ条件: 2・3。

| キー | 形 | 規則 |
| --- | --- | --- |
| `version` | `1` | 定数 |
| `issues` | 整数の配列（1 以上） | 単位のファイルが扱う課題。ファイル名の番号の並びと一致する |
| `tasks[]` | `id`（`^T[0-9]+$`）・`title`・`files`（文字列の配列、1 以上）・`lines`（整数 0 以上）・`criteria`（`^[0-9]+:[0-9]+$` の配列）・`after`（タスクの `id` の配列） | `additionalProperties: false` |
| `units[]` | `name`（1 文字以上）・`tasks`（タスクの `id` の配列、1 以上）・`after`（`{unit, reason}` の配列。`reason` は空白でない文字を含む）・任意の `actual_lines`（整数 0 以上） | `additionalProperties: false` |

スキーマで縛れない規則（参照の実在・輪・覆い・重なり・行数）は `units_decide.judge` が持つ。

### 単位の判定（`core/plan/units_decide.py`）

満たす決定: 3・4・5・6。満たす受け入れ条件: 2・5・7・8。ファイル・git・環境変数を読まない。

```python
class Task(NamedTuple): id: str; title: str; files: tuple[str, ...]; lines: Optional[int]; criteria: tuple[str, ...]; after: tuple[str, ...]
class Edge(NamedTuple): unit: str; reason: str
class Unit(NamedTuple): name: str; tasks: tuple[str, ...]; after: tuple[Edge, ...]; actual_lines: Optional[int] = None
class Units(NamedTuple): issues: tuple[int, ...]; tasks: tuple[Task, ...]; units: tuple[Unit, ...]
class Limits(NamedTuple): target: int; max: int
class View(NamedTuple): name: str; order: int; merge: int; lines: Optional[int]; touches: tuple[str, ...]; criteria: tuple[str, ...]; after: tuple[Edge, ...]
class Finding(NamedTuple): rule: str; unit: str; reason: str
class Proposal(NamedTuple): unit: str; units: tuple[Unit, ...]; note: Optional[str]
class Judgement(NamedTuple): views: tuple[View, ...]; violations: tuple[Finding, ...]; warnings: tuple[Finding, ...]; proposals: tuple[Proposal, ...]
```

| 関数 | 入力 → 返り | 異常系 |
| --- | --- | --- |
| `parse(doc: Mapping[str, Any]) -> Units` | スキーマを通った辞書 → 型 | 投げない（形はスキーマが先に断る） |
| `orders(units: Units) -> tuple[dict[str, int], list[Finding]]` | 単位ごとの実行順（前提 1）。`graphlib.TopologicalSorter` で並べ、並べた順に「先に要る単位の実行順の最大 + 1」を取る | 先に要る単位が無い・輪は `Finding("U1", …)`。輪のときは実行順を返さない（空の辞書） |
| `views(units, order) -> tuple[View, ...]` | 単位ごとの導く値。行数はタスクの `lines` の和（どれかが None なら None）、触るファイル・受け入れ条件はタスクの和（現れた順）、`merge` は実行順、同じ実行順は `units[]` の並びで振った 1 からの番号 | 投げない |
| `judge(units, limits, designed: Optional[Mapping[int, DesignResult]] = None) -> Judgement` | U1〜U6 の違反、W1〜W2 の警告、行数の規則に当たった単位ごとの分け方の案 | 投げない。違反があっても `views` は実行順の取れる範囲で返す |
| `split(unit: Unit, units: Units, limits: Limits) -> Proposal` | 決定 6 の案。タスクを依存の深さの順に並べ、同じ深さの中は `files` の重なるものを同じ単位に、重ならないものを別の単位に置き、`target` を超える手前で区切る。区切った単位の名前は `<元の名前> <番号>`、依存は前の深さの単位を理由「タスクの依存の深さ」で指す。元の単位の `after` は最初の深さの単位が引き継ぐ | 1 つのタスクで `max` を超えるときは、そのタスクだけの単位を置き、`note` に「T<n> を対象ファイルのディレクトリで分ける」と書く |
| `single(issues: Sequence[int], designed: Mapping[int, DesignResult]) -> Units` | 単位のファイルが無い課題を、課題 1 つを 1 単位（タスク 1 つ、`lines` は None、`files` は設計の結果の触るファイル）にする（受け入れ条件 4） | 設計の結果の無い課題は `files` を空にする（`touches` が無いプランとして、すべてと重なり 1 本ずつ流れる） |

規則の判定:

| 規則 | 当たる条件 |
| --- | --- |
| U2 | タスクがどの単位にも無い、または 2 つ以上の単位にある。単位の `tasks` が無いタスクを指す |
| U3 | タスク B の `after` にあるタスク A の単位が、B の単位でも B の単位の依存の閉包の中でもない |
| U4 | 実行順の同じ 2 つの単位の触るファイルに、同じパスがある（`/` で終わる値はディレクトリとして重なりに数えない。プランの `queue_decide.contains` と同じ規則） |
| U5 | 単位の依存の `reason` が空白だけ（スキーマが通しても、前後の空白を除いて空なら当てる） |
| U6 | `lines` が None でなく `limits.max` を超える |
| W1 | `lines` が None でなく `limits.target` を超え、`limits.max` 以下 |
| W2 | `designed` が渡され、単位のファイルの課題が設計の結果の表に無い、または単位の触るファイルが課題の触るファイルの和の外にある |

### 単位のファイルの読み書き（`core/plan/units_files.py`）

満たす決定: 2・5・7。満たす受け入れ条件: 3・4・6。

| 関数 | 入力 → 返り | 異常系 |
| --- | --- | --- |
| `find(repo: Path, issues: Sequence[int]) -> dict[int, Path]` | `issues/issue-*-plan.units.json` のうち、名前の `-` で区切った番号のどれかが課題の番号と一致するもの（番号の一部とは合わせない） | 1 つの課題に 2 つ当たれば `UnitsError` |
| `load(path: Path) -> Units` | スキーマ（`review-units.schema.json`）で確かめて `parse` | 読めない・形が違うと `UnitsError`（理由にパスとスキーマの誤り） |
| `limits(repo: Path) -> Limits` | `.ndf/project.json` の `workflow.review_units` を先に、無いキーは `contracts.limits.value("review-units.target-lines", "count")` と `max-lines` | `target >= max` は `UnitsError` |
| `render(units, judgement, doc: str) -> str` | 実装計画の文書の 2 つの区間（`<!-- ndf:units:begin -->`〜`<!-- ndf:units:end -->` と `<!-- ndf:order:begin -->`〜`<!-- ndf:order:end -->`）を作り直した全文。前者は「レビューの単位」の表（単位・実行順・マージの順・含むタスク・先に要る単位・満たす受け入れ条件・見込みの行数・実際の行数）、後者は「並列と直列」の表（実行順ごとに並列の単位と理由「同じ実行順で依存が無く、触るファイルが重ならない」、依存ごとに直列の 2 単位と依存の理由） | 区間の印が無いか片方だけなら `UnitsError` |
| `unit_plans(repo, given: Mapping[str, Value], issues: Sequence[int], out: Path) -> list[Path]` | 課題ごとに `find` → `load`（無ければ `single`）→ `judge`。違反が無ければ、単位ごとに `route.render(route.choose("implement"), …)` を描き、`branch` を `<given の branch>-<merge>`、`issue` を単位の課題の最小、`acceptance` を `--issue <課題>`（単位の課題ごと）と `--criteria <課題>:<条件>`（単位の条件ごと）にし、プランの最上位へ `touches`（単位の触るファイル）と `needs`（先に要る単位のプランの名前）を足して `out/<プランの名前>.json` に書く。返りは実行順・マージの順に並べたパス | 違反があれば何も書かずに `UnitsError`（理由に違反の行）。単位の課題が `issues` の外なら `UnitsError` |

### コマンド（`core/plan/cli.py`）

満たす受け入れ条件: 3・5・6。結果 JSON の `tool` は既存の `TOOL`（`plan`）。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `units check [<ファイル>...] [--issue N]... [--from-stage]` | ファイル・`--issue`・`--from-stage`（環境変数 `NDF_STAGE_INPUT` の `issues`）のどれかで単位のファイルを集め、`judge` を打つ。`--issue` で見つからない課題は課題 1 つを 1 単位として扱い、`items` に載せる | 違反が無ければ `ok`（`items` に `unit` と `split`、`warnings` に W1・W2 の文、`metrics` に `units`・`orders`・`target`・`max`）。違反があれば `stopped`（`invalid`、`items` に `violation` と `split`）。読めない・宣言の誤りは `stopped`（`invalid`） |
| `units render <ファイル> --doc <実装計画> [--check]` | `render` で全文を作り、`--check` が無ければ書く | `ok`。`--check` で食い違えば `stopped`（`invalid`、理由「表が単位のファイルと食い違う」） |
| `units plans --out <ディレクトリ> [--issue N]... [--from-stage] [--set 名前=値]...` | `unit_plans` を打つ | `ok`（`items` に `{"kind": "plan", "path", "unit", "order"}`）。違反は `stopped`（`invalid`） |

### 工程の流し方（`progress/invoke.py`）

満たす決定: 1・7。満たす受け入れ条件: 3・4。

| 関数 | 変える前 → 後 | 異常系 |
| --- | --- | --- |
| `_call_plan(run, cwd, runner)`（責務の変更） | 前: `plan_path` で 1 本書き `ndf plan next` を打つ。後: 工程が `per_unit` のときは、`template_values` で埋めた値を `given` にして `units_files.unit_plans` で単位ごとのプランを書き、実行順ごとに `ndf plan queue <その実行順のパス>... --max <上限の設定 relay.max-named の count>` を打つ。キューが `ok` で終われば、その実行順の単位の PR をマージの順に `ndf ci merge --branch <単位のブランチ> --reviewed <reviewed-head>` でマージしてから次の実行順へ進む（設計書の決定 3）。`per_unit` でない工程は今のまま | `UnitsError` は工程の失敗（`cause.kind: invalid`、理由をそのまま）。queue とマージの失敗は今の `_from_process` のまま返し、次の実行順を始めない |
| `plan_queue_argv(paths, cwd) -> list[str]`（新設） | `ndf_command` と同じ形で `plan queue` の argv | 投げない |
| `unit_merge_argv(branch, reviewed, cwd) -> list[str]`（新設） | `ndf_command` と同じ形で `ci merge --branch <branch> --reviewed <reviewed>` の argv。`reviewed` はプランの状態ディレクトリの `reviewed-head` | 投げない |

`stages.py` の工程の型と `definition.py:321` の読み手は、キー `per_issue` を `per_unit` へ改名する（今の読み手は値を写すだけで、使う箇所が無い）。

### 工程の定義（`contracts/workflow/`）

満たす決定: 1。満たす受け入れ条件: 1・4。

| 定義 | 変えた後 |
| --- | --- |
| `modes/standard.json` | 要求 → 作業場所 → 設計 → 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー → 実装 → …（行の数 18 は変わらない） |
| `modes/legacy-refactor.json` | 作業場所 → 設計 → 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー → 実装 → … |
| `stages/plan.json` | `phase: design`。ステップ `plan`（`implementation-plan`）→ `units-check`（`ndf.script`、`["ndf", "plan", "units", "check", "--from-stage"]`、kind `record`）。設計書を省いた課題（実装計画は工程「設計」が書く）も同じ確かめを通す |
| `stages/design.json` | ステップ `design` だけ。`outputs: ["design_pr"]` |
| `stages/document-review.json` | `docs-review`（`cross-review`、範囲 `sprint_diff`、subject `design`）→ `design-mvv` → `design-gate`（modes は今と同じ 3 つ）→ `design-merge`。`outputs: ["mvv_gate"]` |
| `stages/implement.json` | `per_unit: true` |

## 異常系の処理

| 起きること | 扱い |
| --- | --- |
| 単位のファイルが壊れている（JSON・スキーマ） | `check`・`plans` は `stopped`（`invalid`）。工程「計画」は失敗し、worker が直して打ち直す |
| 単位の違反（U1〜U6） | `check` は案を添えて `stopped`。`plans` は何も書かない。工程「実装」は失敗し、実装を始めない |
| 宣言の誤り（`target_lines >= max_lines`・形の違い） | `stopped`（`invalid`、理由に `.ndf/project.json` の `workflow.review_units`）。`invoke._project` は今のまま `WORKFLOW_SCHEMA` で断る |
| 実装計画の文書の表が正本と食い違う | `render --check` が `stopped`。工程「計画」のプランの push の前で止まる |
| 単位のファイルが無い課題 | 課題 1 つを 1 単位（`single`）。行数を知らないので U6・W1 を当てない |

## 単体テストの観点

| 観点 | 縛る振る舞い |
| --- | --- |
| 実行順 | 依存の無い単位が 1、A → B → C が 1・2・3、B と C が A の後なら両方 2。輪は U1 で実行順を返さない |
| 導く値 | 行数・触るファイル・受け入れ条件がタスクの和、マージの順が実行順と並びで決まる |
| 規則 | U2〜U6・W1・W2 のそれぞれの最小の入力で `rule` が出る。当たらない入力では出ない |
| 上限の境 | 400 は何も出ない、401 は W1、1,000 は W1、1,001 は U6。宣言 `target_lines: 200` で 201 が W1 |
| #3698・#3247 | 16,757 行・24,008 行の 1 単位で U6、案のどの単位も 1,000 以下 |
| #3672 | fixture を 1 単位にすると U6、案の単位の実行順が 契約 → 読む部品と解析 → 付け替え → 削除とテスト の順に沿う |
| プラン | 書いたプランの `needs`・`touches`、`queue_decide.waves` で同じ実行順が同じ組。単位のファイルが無い課題は 1 本 |
| 文書 | `render` の後に `render --check` が通り、表の 1 セルを書き換えると落ちる |
| 工程 | `standard`・`legacy-refactor` で「計画」の位置と phase、設計承認ゲートのステップが「ドキュメントレビュー」に 1 つ、`mvv_gate` が「ドキュメントレビュー」の出力 |
