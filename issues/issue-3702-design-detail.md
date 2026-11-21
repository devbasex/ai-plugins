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
│   │   └── impl.json                                 # worker に単位の指示のファイル（unit_file）を渡す
│   └── workflow/
│       ├── ndf.schema.json                           # 工程のメタデータに per_unit を足す
│       ├── context.schema.json                       # 文脈のキー rebuild_findings・design_head を足す
│       ├── review-units.schema.json                  *
│       ├── modes/{standard,legacy-refactor}.json     # 新しい 3 つの工程を指し、計画を詳細設計の直後へ
│       └── stages/
│           ├── design-draft.json                     *（工程「設計」。standard・legacy-refactor だけ）
│           ├── design-approval.json                  *（工程「ドキュメントレビュー」。standard・legacy-refactor だけ）
│           ├── implement-units.json                  *（工程「実装」。per_unit。standard・legacy-refactor だけ）
│           ├── plan.json                             # phase: design
│           └── document-rebuild.json                 # 出力に rebuild_findings を足す
├── src/ndf/
│   ├── core/plan/
│   │   ├── units_decide.py                           *（純粋な判定）
│   │   ├── units_files.py                            *（ファイル・宣言・文書・プランの読み書き）
│   │   ├── cli.py                                    # ndf plan units check / render / plans
│   │   └── inputs_decide.py                          # design_gaps の単位の数の規則を消し、設計の結果の表の読み手を置く
│   ├── core/route/__init__.py                        # 既定のレビューの起動で design-review も --purpose design にする
│   ├── domains/materials/collect.py                  # 設計の結果の表の読み手を inputs_decide のものへ寄せる
│   └── progress/
│       ├── stages.py / definition.py                 # per_unit を足す（per_issue は残す）
│       └── invoke.py                                 # per_unit の工程は単位ごとの作業場所・プラン・キュー・マージで流す
└── tests/
    ├── core/plan/{test_units_decide.py*, test_units_cli.py*, test_inputs.py}
    ├── core/plan/fixtures/units/issue-3672.units.json *
    └── progress/{test_definition.py, test_command.py, test_sprint_templates.py, test_invoke.py}
```

依存の向きは一方向である。`units_decide` は標準ライブラリ（`graphlib`）と `inputs_decide.DesignResult` だけを読む。`units_files` は `units_decide`・`inputs_decide`（設計の結果の表の読み手）・`ndf.contracts`（スキーマと上限）・`ndf.core.route`（impl のテンプレートの描画）を読む。`cli.py` と `progress/invoke.py` は `units_files` を読み、`units_files` はどちらも読まない。`core` から `domains` は読まない（設計の結果の表の読み手を `inputs_decide` に置き、`materials.collect` がそれを使う）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `contracts/workflow/review-units.schema.json` | 新設 | 単位のファイルの形（`version`・`issues`・`tasks`・`units`） | 無し |
| `contracts/limits.json` | 変更 | `review-units.target-lines`（`count: 400`）・`review-units.max-lines`（`count: 1000`） | 無し |
| `contracts/schemas/project-workflow.schema.json` | 変更 | `workflow.review_units`（`target_lines`・`max_lines`。整数 1 以上） | 無し |
| `contracts/schemas/ndf-settings/project.schema.json` | 変更 | `workflow` の `properties` に同じ `review_units` を足す | 無し |
| `src/ndf/core/plan/units_decide.py` | 新設 | 実行順・導く値・違反・警告・分け方の案 | `graphlib`・`inputs_decide` |
| `src/ndf/core/plan/units_files.py` | 新設 | 単位のファイルを集める・読む、上限を読む、文書の表を作る、単位のプランと指示のファイルを書く | `units_decide`・`inputs_decide`・`ndf.contracts`・`ndf.core.route` |
| `src/ndf/core/plan/cli.py` | 変更 | サブコマンド `units`（`check`・`render`・`plans`） | `units_files` |
| `src/ndf/core/plan/inputs_decide.py` | 変更 | `design_gaps` から「PR の単位が 2 つ以上」の理由を消す（決定 2）。`design_rows`（設計の結果の表から課題の触るファイル）を `materials.collect` から移す | 無し |
| `src/ndf/domains/materials/collect.py` | 変更 | `design_rows` を `inputs_decide` のものに替える（読み手を 1 つにする） | `inputs_decide` |
| `src/ndf/core/route/__init__.py` | 変更 | `review_launch` の `--purpose design` を、テンプレート `design` と `design-review` の両方で選ぶ | 無し |
| `contracts/workflow/ndf.schema.json` | 変更 | 工程のメタデータに `per_unit`（`const: true`）を足す。`per_issue` は残す | 無し |
| `contracts/workflow/context.schema.json` | 変更 | 文脈のキー `rebuild_findings`（再構成の指摘のファイル）・`design_head`（承認に使う設計 PR の HEAD）を足す | 無し |
| `contracts/workflow/stages/plan.json` | 変更 | `phase: design` だけを変える（確かめのステップは足さない） | 無し |
| `contracts/workflow/stages/document-rebuild.json` | 変更 | `outputs` に `rebuild_findings` を足す | 無し |
| `contracts/workflow/stages/design-draft.json` | 新設 | 工程「設計」（standard・legacy-refactor）。ステップ `design`（`design-draft`）、`outputs: ["design_pr"]` | 無し |
| `contracts/workflow/stages/design-approval.json` | 新設 | 工程「ドキュメントレビュー」（standard・legacy-refactor）。`docs-review`（`design-review`）→ `design-mvv` → `design-gate` → `design-merge`（`design-land`） | 無し |
| `contracts/workflow/stages/implement-units.json` | 新設 | 工程「実装」（standard・legacy-refactor）。`implement.json` と同じステップに `per_unit: true` | 無し |
| `contracts/workflow/modes/standard.json`・`legacy-refactor.json` | 変更 | 新しい 3 つの工程を指し、「計画」を「詳細設計」の直後へ移す | 無し |
| `contracts/templates/design-draft.json` | 新設 | 設計の worker → push → PR を Draft で開く | 無し |
| `contracts/templates/design-review.json` | 新設 | 下の「設計 PR の承認とマージ」の並び | 無し |
| `contracts/templates/design-land.json` | 新設 | 承認した HEAD を `ndf ci merge --reviewed` でマージする（承認のステップを持たない） | 無し |
| `contracts/templates/implementation-plan.json` | 変更 | worker の指示に「単位のファイルを書き、`ndf plan units render` で表を作る」を足す | 無し |
| `contracts/templates/impl.json` | 変更 | 差し込み `unit_file` を足し、worker の起動に渡す | 無し |
| `src/ndf/progress/stages.py`・`definition.py` | 変更 | 工程の型に `per_unit: bool = False` を足す（`per_issue` はそのまま） | 無し |
| `src/ndf/progress/invoke.py` | 変更 | `per_unit` の工程の `ndf.plan` のステップを、単位ごとの作業場所・プラン・`ndf plan queue`・マージで流す | `units_files` |
| `tests/core/plan/test_units_decide.py` | 新設 | 判定の単体テスト（受け入れ条件 2・5・7・8、U1〜U6・W1〜W2） | fixture |
| `tests/core/plan/test_units_cli.py` | 新設 | コマンドのテスト（受け入れ条件 3・4・6、`render --check`、ファイルの集め方） | fixture |
| `tests/core/plan/fixtures/units/issue-3672.units.json` | 新設 | #3672 の詳細設計の地図から作った 1 単位のファイル（受け入れ条件 8） | 無し |
| `tests/core/plan/test_inputs.py` | 変更 | `design_gaps` の単位の数のテストを消し、`design_rows` のテストを移す | 無し |
| `tests/progress/test_definition.py`・`test_command.py`・`test_sprint_templates.py`・`test_invoke.py` | 変更 | 5 つのモードの工程の並び・ゲートの位置・出力のキー・`per_unit` の流し方・承認とマージ | 無し |

`light`・`operation`・`documentation` は今の `design.json`・`document-review.json`・`implement.json` を指したまま変えない（設計書の決定 1）。手順書（`skills/` の 8 ファイル）の変更は実装計画のタスクにあり、関数を持たないのでこの文書に下ろさない。

## 構成要素ごとの仕様

### 単位のファイルの形（`review-units.schema.json`）

満たす決定: 2・3。満たす受け入れ条件: 2・3。

| キー | 形 | 規則 |
| --- | --- | --- |
| `version` | `1` | 定数 |
| `issues` | 整数の配列（1 以上） | 単位のファイルが扱う課題。ファイル名の番号の並びと一致する |
| `tasks[]` | `id`（`^T[0-9]+$`）・`title`・`issue`（整数。`issues` が 2 つ以上なら必須、1 つなら省けてその課題）・`files`（文字列の配列、1 以上。各値は `^(?!/)(?!.*/$)[^*?\[\]]+$`＝リポジトリの根からの具体的なファイルで、ディレクトリ・glob を書かない）・`lines`（整数 0 以上）・`criteria`（`^[0-9]+:[0-9]+$` の配列）・`after`（タスクの `id` の配列） | `additionalProperties: false` |
| `units[]` | `name`（1 文字以上）・`tasks`（タスクの `id` の配列、1 以上）・`after`（`{unit, reason}` の配列。`reason` は空白でない文字を含む）・任意の `actual_lines`（整数 0 以上） | `additionalProperties: false` |

タスクの所属の課題の正本は `tasks[].issue` である。単位の課題はその単位のタスクの `issue` の集まり（現れた順）、**代表の課題**は単位の最初のタスクの `issue` とする。スキーマで縛れない規則（重複・参照の実在・輪・覆い・重なり・行数・所属）は `units_decide.judge` が持つ。

### 単位の判定（`core/plan/units_decide.py`）

満たす決定: 3・4・5・6。満たす受け入れ条件: 2・5・7・8。ファイル・git・環境変数を読まない。

```python
class Task(NamedTuple): id: str; title: str; issue: int; files: tuple[str, ...]; lines: Optional[int]; criteria: tuple[str, ...]; after: tuple[str, ...]
class Edge(NamedTuple): unit: str; reason: str
class Unit(NamedTuple): name: str; tasks: tuple[str, ...]; after: tuple[Edge, ...]; actual_lines: Optional[int] = None
class Units(NamedTuple): issues: tuple[int, ...]; tasks: tuple[Task, ...]; units: tuple[Unit, ...]
class Limits(NamedTuple): target: int; max: int
class View(NamedTuple): name: str; order: int; merge: int; lines: Optional[int]; touches: tuple[str, ...]; issues: tuple[int, ...]; criteria: tuple[str, ...]; after: tuple[Edge, ...]
class Finding(NamedTuple): rule: str; unit: str; reason: str
class Proposal(NamedTuple): unit: str; units: tuple[Unit, ...]; rewired: tuple[Unit, ...]; note: Optional[str]
class Judgement(NamedTuple): views: tuple[View, ...]; violations: tuple[Finding, ...]; warnings: tuple[Finding, ...]; proposals: tuple[Proposal, ...]
```

| 関数 | 入力 → 返り | 異常系 |
| --- | --- | --- |
| `parse(doc: Mapping[str, Any]) -> Units` | スキーマを通った辞書 → 型（`issue` を省いたタスクはファイルの唯一の課題） | 投げない（形はスキーマが先に断る） |
| `merge(parts: Sequence[tuple[str, Units]]) -> Units` | 複数の単位のファイル（`(ファイルの名前, Units)`）を 1 つのグラフにする。タスクの `id` と単位の `name` を `<ファイルの名前>/<元の値>` にし、`after` も同じく付け替える（ファイルが 1 つなら付け替えない） | 投げない |
| `orders(units: Units) -> tuple[dict[str, int], list[Finding]]` | 単位ごとの実行順（前提 1）。先にタスクのグラフ（`tasks[].after`）を `graphlib.TopologicalSorter` で並べて参照と輪を確かめ、次に単位のグラフを並べ、並べた順に「先に要る単位の実行順の最大 + 1」を取る | タスク・単位の重複、先に要るタスク・単位が無い、どちらかのグラフの輪は `Finding("U1", …)`。そのときは実行順を返さない（空の辞書） |
| `views(units, order) -> tuple[View, ...]` | 単位ごとの導く値。行数はタスクの `lines` の和（どれかが None なら None）、触るファイル・課題・受け入れ条件はタスクの和（現れた順）、`merge` は実行順、同じ実行順は `units[]` の並びで振った 1 からの番号 | 投げない |
| `judge(units, limits, designed: Optional[Mapping[int, DesignResult]] = None) -> Judgement` | U1〜U6 の違反、W1〜W2 の警告、行数の規則に当たった単位ごとの分け方の案 | 投げない。違反があっても `views` は実行順の取れる範囲で返す |
| `split(unit: Unit, units: Units, limits: Limits) -> Proposal` | 決定 6 の案（下の「分け方の案の作り方」） | 1 つのタスクで `max` を超えるときは、そのタスクだけの単位を置き、`note` に「T<n> を書き手が対象ファイルごとに分け直す」と書く |
| `apply(units: Units, proposal: Proposal) -> Units` | 元の単位を案の単位に置き換え、`rewired` の単位（元の単位を指していた後続）を差し替えたグラフ | 投げない |
| `single(issue: int, designed: Optional[DesignResult]) -> Units` | 単位のファイルを持たない課題を、課題 1 つを 1 単位（タスク 1 つ、`lines` は None、`files` は設計の結果の触るファイル、`criteria` は空＝その課題のすべての条件）にする（受け入れ条件 4） | 設計の結果の無い課題は `files` を空にする（プランに `touches` を書かず、キューはすべてと重なるとみなして 1 本ずつ流す） |

規則の判定:

| 規則 | 当たる条件 |
| --- | --- |
| U1 | タスクの `id`・単位の `name` が重なる。タスクの `after` か単位の `after` が無いものを指す。タスクのグラフか単位のグラフが輪になる（同じ単位の中のタスクの輪も含む） |
| U2 | タスクがどの単位にも無い、または 2 つ以上の単位にある。単位の `tasks` が無いタスクを指す。タスクの `issue` がファイルの `issues` の外にある。`criteria` の課題がそのタスクの `issue` と違う。単位が受け入れ条件を 1 つも持たない（条件を持たない準備のタスクは、条件を持つ単位へ束ねる） |
| U3 | タスク B の `after` にあるタスク A の単位が、B の単位でも B の単位の依存の閉包の中でもない |
| U4 | 実行順の同じ 2 つの単位の触るファイルに、同じパスがある（パスは具体的なファイルだけなので、文字列の一致で決まる。ファイルをまたいで `merge` した後のグラフで確かめる） |
| U5 | 単位の依存の `reason` が空白だけ（スキーマが通しても、前後の空白を除いて空なら当てる） |
| U6 | `lines` が None でなく `limits.max` を超える |
| W1 | `lines` が None でなく `limits.target` を超え、`limits.max` 以下 |
| W2 | `designed` が渡され、単位の課題が設計の結果の表に無い、または単位の触るファイルが単位の課題の触るファイルの和の外にある |

#### 分け方の案の作り方（`split`）

1. 単位のタスクを、単位の中のタスクの依存の深さ（先に要るタスクの深さの最大 + 1。単位の外のタスクは数えない）で層に分ける
2. 同じ層の中で `files` の重なるタスクを連結成分に束ねる。成分の行数が `target` 以下なら 1 つの単位にする。超えるなら成分のタスクを 1 つずつの単位にし、同じファイルを触るタスクの単位どうしを `tasks[]` の並びで直列につなぐ（理由「同じファイルを触る」）。直列にした単位は実行順が分かれるので U4 に当たらない
3. 重ならない成分は、`target` を超える手前まで同じ単位へ足し、超えたら次の単位を始める（`<元の名前> <番号>`）
4. 各単位に、その単位のタスクが先に要るタスクを含む案の単位への依存を理由「タスクの依存の深さ」で付ける
5. 元の単位の `after` は、先に要る案の単位を持たない案の単位（最初の層）のすべてが引き継ぐ。元の単位を `after` で指していた後続の単位は、指す先を案の単位のうち後続を持たないもの（最後の層）のすべてに付け替え、`rewired` に返す
6. 案は `apply` で元のグラフに当てて `judge` を通し、違反（U6 の `note` 付きの単位を除く）が残らないことをテストで縛る

### 単位のファイルの読み書き（`core/plan/units_files.py`）

満たす決定: 2・5・7。満たす受け入れ条件: 3・4・6。

| 関数 | 入力 → 返り | 異常系 |
| --- | --- | --- |
| `collect(repo: Path, issues: Sequence[int]) -> Collected` | 下の「単位のファイルの集め方」。返りは読んだ単位のファイル（パスの一意な並び）・ファイルを持たない課題とその扱い（`single`・`skip`） | 下の表 |
| `load(path: Path) -> Units` | スキーマ（`review-units.schema.json`）で確かめて `parse` | 読めない・形が違うと `UnitsError`（理由にパスとスキーマの誤り） |
| `designed(repo: Path, issues: Sequence[int]) -> dict[int, DesignResult]` | 課題ごとに `issues/issue-<番号>-design*.md` の「設計の結果」の表を `inputs_decide.design_rows` で読む（`materials.collect` と同じ読み手）。表に行の無い課題は返りに載せない | 読めない文書は飛ばす（今の `design_files` と同じ） |
| `limits(repo: Path) -> Limits` | `.ndf/project.json` の `workflow.review_units` を先に、無いキーは `contracts.limits.value("review-units.target-lines", "count")` と `max-lines` | `target >= max` は `UnitsError` |
| `render(units, judgement, doc: str) -> str` | 実装計画の文書の 2 つの区間（`<!-- ndf:units:begin -->`〜`<!-- ndf:units:end -->` と `<!-- ndf:order:begin -->`〜`<!-- ndf:order:end -->`）を作り直した全文。前者は「レビューの単位」の表（単位・実行順・マージの順・含むタスク・先に要る単位・満たす受け入れ条件・見込みの行数・実際の行数＝`actual_lines`）、後者は「並列と直列」の表（実行順ごとに並列の単位と理由「同じ実行順で依存が無く、触るファイルが重ならない」。単位が 1 つの実行順は理由を「—」。依存ごとに直列の 2 単位と依存の理由） | 区間の印が無いか片方だけなら `UnitsError` |
| `parallel_cap(environ: Mapping[str, str], lane_plans: Optional[int]) -> int` | 同じ実行順で同時に流す本数の上限。`min(lane_plans, limits.from_env(environ, "NDF_RELAY_MAX_NAMED", "relay.max-named", "count"))`（`lane_plans` が None ならマシンの上限だけ）。マシンの上限は `progress/lane/machine.cap` と同じ読み方 | 投げない |
| `unit_plans(repo, given: UnitGiven, out: Path) -> list[UnitPlan]` | 下の「単位のプランの組み立て」 | 違反があれば何も書かずに `UnitsError`（理由に違反の行） |

#### 単位のファイルの集め方（`collect`）

1. `issues/issue-*-plan.units.json` のうち、名前の `-` で区切った番号のどれかがスプリントの課題の番号と一致するものを、パスで一意に集める（統合の計画が複数の課題に当たっても 1 回だけ読む）
2. 各ファイルの `issues` がスプリントの課題の中にあり、ファイル名の番号の並びと一致することを確かめる。課題が 2 つのファイルに現れれば止める
3. ファイルを持たない課題は次の順で扱いを決める

| 課題の状態 | 扱い |
| --- | --- |
| 設計の結果の表で扱いが「実装する」でない（統合する・閉じる） | `skip`（単位を作らない。統合先の課題の単位が持つ） |
| `issues/issue-<番号>-plan.md` に `<!-- ndf:units:begin -->` の印がある（新しい形の実装計画） | 止める（`UnitsError`「実装計画があるのに単位のファイルが無い」）。書き忘れを `single` に落とさない |
| `issues/issue-<番号>-plan.md` があり、印が無い（この変更より前に書いた実装計画） | `single`（今の振る舞いのまま）。`check` は `warnings` に「旧い形の実装計画で行数を確かめていない」を載せる |
| `issues/issue-<番号>-plan.md` が無い（実装計画を省いた。文書の役割の表の「省いてよい文書と条件」） | `single`。`check` の `metrics.omitted` に課題を載せ、承認資料に「実装計画を省いた課題（行数は確かめていない）」として出す |

新しく書く実装計画は `implementation-plan` の worker が `render` で必ず印を置くので、印の有無で新旧を分けられる。

#### 単位のプランの組み立て（`unit_plans`）

`UnitGiven` は工程が渡す値（スプリントのブランチ `sprint`・リモート・状態の保存先・テンプレートの差し込みの組み立て `values_of(unit) -> 値`）である。

1. `collect` → 各ファイルを `load` → ファイルを持たない `single` の課題を足して `merge` → `designed` → `judge`。違反があれば何も書かない。ファイルをまたぐ U4 もここで当たる
2. 単位に、スプリント全体で一意な連番 `seq` を、実行順 → マージの順（ファイルのパスの順 → `units[]` の並び → `single` の課題の番号の順）で振る。プランの名前・ブランチ・作業場所はどれも `<sprint>-u<seq>`、出力は `out/<sprint>-u<seq>.json`（別のファイルどうしで同じ名前にならない）
3. 単位ごとに指示のファイル `out/<sprint>-u<seq>.unit.json` を書く: 単位の名前・代表の課題・単位の課題・担当のタスク（`id`・`title`・`files`・`criteria`）・実装計画の文書のパス・「このファイルの tasks だけを実装し、ほかのタスクに手を付けない」の一文。`single` の単位は担当のタスクを「課題の全体」とする
4. 差し込み: `issue` は代表の課題、`acceptance` は単位の課題のうち条件を持つ課題ごとに `--issue <課題>` と、その課題の条件ごとに `--criteria <課題>:<条件>`（条件を持たない課題には `--issue` を付けない。全部の条件を照らさないため）。`single` の単位は `--issue <課題>` だけ（課題のすべての条件）。`unit_file` は 3 の指示のファイル。残りの差し込み（題・本文・`review`・`test_command` など）は `given.values_of(unit)` が組む（下の工程の流し方）
5. プランの最上位へ `touches`（単位の触るファイル。空なら書かない）を足す。`needs` は**同じ実行順のプランだけ**を指す（同じ実行順には依存が無いので、実際には書かない）。前の実行順への依存は、工程が前の実行順のマージを確かめてから次のキューを打つことで守る（今の `ndf plan queue` は起動ごとに済んだプランを空から数え、別の起動で済んだ `needs` を解けないため）。単位の依存は `items[].after` と指示のファイルに残す
6. 返りは実行順・マージの順に並べた `UnitPlan(path, unit_file, name, order, merge, branch)`

### コマンド（`core/plan/cli.py`）

満たす受け入れ条件: 3・5・6。結果 JSON の `tool` は既存の `TOOL`（`plan`）。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `units check [<ファイル>...] [--issue N]... [--from-stage] [--docs]` | ファイル・`--issue`・`--from-stage`（環境変数 `NDF_STAGE_INPUT` の `issues`）のどれかで単位のファイルを `collect` の規則で集め、`designed` を読んで（ファイルを直に渡したときも、その課題の設計の文書があれば）`judge` を打つ。`--docs` は加えて、印のある実装計画の文書ごとに `render` の結果と今の文書を照らす（`render --check` と同じ判定を同じ起動の中で行う） | 違反が無ければ `ok`（`items` に `unit` と `split`、`warnings` に W1・W2 と旧い形の文、`metrics` に `units`・`orders`・`target`・`max`・`omitted`・`head`（確かめた HEAD））。違反・表の食い違いがあれば `stopped`（`invalid`、`items` に `violation` と `split`）。読めない・宣言の誤り・書き忘れは `stopped`（`invalid`） |
| `units render <ファイル> --doc <実装計画> [--check]` | `render` で全文を作り、`--check` が無ければ書く（worker が手元で使う） | `ok`。`--check` で食い違えば `stopped`（`invalid`、理由「表が単位のファイルと食い違う」） |
| `units plans --out <ディレクトリ> [--issue N]... [--from-stage] [--set 名前=値]... [--lane-plans N]` | `unit_plans` を打つ（supervisor が手で流すときの入口。差し込みは `--set` の値を全単位に共通に使う） | `ok`（`items` に `{"kind": "plan", "path", "unit_file", "unit", "order", "merge", "branch", "after"}`、`metrics.max` に `parallel_cap` の値）。違反は `stopped`（`invalid`） |

### 工程の流し方（`progress/invoke.py`）

満たす決定: 1・3・7。満たす受け入れ条件: 3・4。

`per_unit` の工程（`implement-units`）の `ndf.plan` のステップは、`_call_plan` の中で `_call_units(run, cwd, work_dir, runner)` に分ける。`per_unit` でない工程は今のまま。

1. **組み立て**: `units_files.unit_plans` に `values_of(unit)` を渡す。`values_of` は、単位の値で写した入力（`issues` を代表の課題 1 つ、`worktree` を単位の作業場所、`branch` を単位のブランチ、`base` をスプリントのブランチ）の `StageRun` を作り、それで `template_values` を打つ。したがって題・本文は代表の課題について既存の `_issue_values` の信頼の確かめとキャッシュ（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE`）を通り、既定の `review` は単位のブランチで組まれ、設定の `workflow.launch.review`・`test.ci.only` もそのまま効く。スプリントの課題が複数でも、単位ごとに課題 1 つの入力になるので題・本文が欠けない
2. **作業場所**: 実行順 k を始めるときに `git fetch <remote> <sprint>` し、その実行順の各単位について `ndf worktree create <sprint>-u<seq> --from <remote>/<sprint>` で、前の実行順のマージをすべて含む HEAD から単位専用のブランチと作業場所を用意する（既にあれば作り直さない）。単位の PR の宛先はスプリントのブランチ（差し込み `base`）
3. **キュー**: `ndf plan queue <その実行順のプランのパス>... --max <parallel_cap>` を打つ。`lane_plans` はスプリントがレーンで動くとき、そのレーンの `max_plans`（並列の計画の `max_plans`、省けば既定）。supervisor の手順も `ndf plan units plans` の `metrics.max` を同じ `--max` に渡す
4. **マージ**: キューが `ok` で終われば、その実行順の単位をマージの順に、**単位の作業場所を cwd にして** `ndf ci merge --branch <単位のブランチ> --reviewed <reviewed-head>`（`reviewed-head` はそのプランの状態ディレクトリの値）を打つ
5. **完了の記録と再開**: 単位ごとに `work_dir/units/<seq>.json` へ PR の番号・レビュー済みの HEAD・状態（`queued`・`merged`）を書く。打ち直したときは、記録のある単位を `gh pr view <番号> --json state,headRefOid` で確かめ、`MERGED` なら `ci merge` を打たずに飛ばし、開いていてマージキューに入っていれば待ちとして `ci_pending` で返す（打ち直しで同じ確かめから続く）。開いていて HEAD がレビュー済みの HEAD と違えば止める（`precondition`、プランを打ち直してレビューから）。`ci merge` がマージキューへの登録で `ci_pending` を返したときも完了とせず同じく返す
6. **次の実行順**: その実行順のすべての単位の PR が `MERGED` になってから 2 へ戻る。実行順がすべて済めば、工程の残りのステップ（`gathered`）へ進む

| 関数 | 変える前 → 後 | 異常系 |
| --- | --- | --- |
| `_call_plan(run, cwd, work_dir, answer, runner)`（責務の変更） | 前: `plan_path` で 1 本書き `ndf plan next` を打つ。後: 工程が `per_unit` なら `_call_units` へ渡す | — |
| `_call_units(run, cwd, work_dir, runner)`（新設） | 上の 1〜6 | `UnitsError`・`TemplateValuesMissing` は工程の失敗（`cause.kind: invalid`、理由をそのまま）。queue とマージの失敗は今の `_from_process` のまま返し、次の実行順を始めない |
| `plan_queue_argv(paths, cap, cwd) -> list[str]`（新設） | `ndf_command` と同じ形で `plan queue … --max <cap>` の argv | 投げない |
| `unit_merge_argv(branch, reviewed) -> list[str]`（新設） | `ndf_command` と同じ形で `ci merge --branch <branch> --reviewed <reviewed>` の argv。打つ cwd は呼び手が単位の作業場所にする | 投げない |

`stages.py` の工程の型と `definition.py:321` の読み手に `per_unit` を足す（既定は偽）。`per_issue` は改名せずに残す（`ndf.schema.json` の既存のキーと、保存済みのスナップショットの `per_issue` を今のまま読めるようにするため。`_stage_from` はキーをそのまま `StageDef` に渡すので、`per_unit` を持たない古いスナップショットは既定の偽で読める）。

### 設計 PR の承認とマージ（`design-review`・`design-land`）

満たす決定: 1。満たす受け入れ条件: 1。工程「ドキュメントレビュー」（`design-approval`）のステップの中身である。

| ステップ | 中身 | 失敗したとき |
| --- | --- | --- |
| `docs-review`（テンプレート `design-review`） | ① `{{review}} --findings {{rebuild_findings}}`（既定の起動は `--purpose design`。`rebuild_findings` は工程「ドキュメント再構成」の出力）→ ② 直しの push → ③ `ndf plan units check --from-stage --docs`（**最終の HEAD の単位のファイルと表を 1 回だけ確かめる**）→ ④ `gh pr ready <branch>`（Draft を外す。レビューが収束し単位を確かめた後）→ ⑤ conflict-check → ⑥ ci（既存の部品）→ ⑦ 今の HEAD を `design_head` として報告に載せる | ③ が止まれば修正の worker が単位のファイルか文書を直して ② へ戻る。⑥ の失敗は ci-fix → push → ① へ戻る（HEAD が変わったのでレビューと ③ をやり直す）。マージ先の取り込み（base-sync）も ① へ戻る |
| `design-mvv` | 今のまま（`ndf records mvv gate --gate design`） | 今のまま |
| `design-gate` | 承認を 1 回だけ求める。承認資料に設計 PR の URL・`design_head`・`units check` の結果を載せる。承認の記録は `design_head` と組で持つ | 差し戻しは今のまま |
| `design-merge`（テンプレート `design-land`） | conflict-check → 設計の作業場所で `ndf ci merge --branch <branch> --reviewed <design_head>`。承認のステップ（`merge-gate`）を持たず、プランの中で承認を聞き直さない | 衝突や HEAD の食い違いで承認した HEAD のままマージできなければ、push せずに止まる（`precondition`、理由「承認した HEAD からマージできない」）。工程を打ち直すと `docs-review` から始まり、HEAD が `design_head` と違う承認の記録は使わない（レビュー・MVV 判定・承認をやり直す） |

変更の無い経路（レビューが収束し CI が通り、衝突が無い）では、承認は `design-gate` の 1 回だけである。

### 工程の定義（`contracts/workflow/`）

満たす決定: 1。満たす受け入れ条件: 1・4。

| 定義 | 変えた後 |
| --- | --- |
| `modes/standard.json` | 要求 → 作業場所 → 設計（`design-draft`）→ 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ …（行の数 18 は変わらない） |
| `modes/legacy-refactor.json` | 作業場所 → 設計（`design-draft`）→ 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ … |
| `modes/light.json`・`operation.json`・`documentation.json` | 変えない（`design.json`・`document-review.json`・`implement.json` を指したまま。`documentation` の「計画」はドキュメントレビューの後のまま） |
| `stages/plan.json` | `phase: design`。ステップ `plan`（`implementation-plan`）だけ。実装計画を書くのはこの工程だけで、工程「設計」は書かない（設計書を省いた課題も同じ） |
| `stages/design-draft.json` | ステップ `design`（`design-draft`）だけ。`outputs: ["design_pr"]` |
| `stages/design-approval.json` | `inputs`: `worktree`・`branch`・`mode`・`pace`・`mvv_ref`・`design_pr`・`rebuild_findings`。`docs-review` → `design-mvv` → `design-gate`（modes は `legacy-refactor`・`standard`）→ `design-merge`。`outputs: ["mvv_gate", "design_head"]` |
| `stages/implement-units.json` | `implement.json` と同じステップに `per_unit: true` |
| `stages/document-rebuild.json` | `outputs` に `rebuild_findings` を足す（`documentation` で読まれなくても害が無い） |

## 異常系の処理

| 起きること | 扱い |
| --- | --- |
| 単位のファイルが壊れている（JSON・スキーマ） | `check`・`plans` は `stopped`（`invalid`）。工程「ドキュメントレビュー」の `units check` で止まり、修正の worker が直して push する |
| 単位の違反（U1〜U6）・表の食い違い | `check` は案を添えて `stopped`。`plans` は何も書かない。工程「実装」は失敗し、実装を始めない |
| 新しい形の実装計画に単位のファイルが無い | `stopped`（`invalid`、「実装計画があるのに単位のファイルが無い」）。設計承認の前に止まる |
| 宣言の誤り（`target_lines >= max_lines`・形の違い） | `stopped`（`invalid`、理由に `.ndf/project.json` の `workflow.review_units`）。`invoke._project` は今のまま `WORKFLOW_SCHEMA` で断る |
| 単位のファイルが無い課題（実装計画を省いた・旧い形） | 課題 1 つを 1 単位（`single`）。行数を知らないので U6・W1 を当てず、`check` が省いた・旧い形であることを報告する |
| 承認の後に設計 PR の HEAD が変わる | マージせずに止まり、レビューから打ち直す（承認の記録は使わない） |
| 実装の途中で止まる（キュー・マージ・マージキューの待ち） | 打ち直すと、マージ済みの単位を飛ばし、記録の状態から続ける |

## 単体テストの観点

| 観点 | 縛る振る舞い |
| --- | --- |
| 実行順 | 依存の無い単位が 1、A → B → C が 1・2・3、B と C が A の後なら両方 2。単位の輪・同じ単位の中のタスクの輪・タスクと単位の重複は U1 で実行順を返さない |
| 導く値 | 行数・触るファイル・課題・受け入れ条件がタスクの和、マージの順が実行順と並びで決まる |
| 規則 | U2〜U6・W1・W2 のそれぞれの最小の入力で `rule` が出る。当たらない入力では出ない。`files` にディレクトリ（末尾 `/`）を書くとスキーマが断る |
| 上限の境 | 400 は何も出ない、401 は W1、1,000 は W1、1,001 は U6。宣言 `target_lines: 200` で 201 が W1 |
| 分け方の案 | 同じ深さで同じファイルを触る 600 行のタスク 2 つの単位で、案が 2 単位を直列にし、`apply` した後の `judge` に違反が無い。元の単位を指す後続が案の最後の層を指す |
| #3698・#3247 | 16,757 行・24,008 行の 1 単位で U6、案のどの単位も 1,000 以下 |
| #3672 | fixture を 1 単位にすると U6、案の単位の実行順が 契約 → 読む部品と解析 → 付け替え → 削除とテスト の順に沿う |
| 集め方 | 統合の計画（`issue-10-11-plan.units.json`）を 1 回だけ読む。2 つのファイルの同じ実行順の単位が同じファイルを触ると U4。新しい形の計画に単位のファイルが無ければ止まり、旧い形・省いた課題は `single` |
| プラン | 2 つのファイルからのプランの名前と出力のパスが重ならない。同じ課題の 2 単位で指示のファイルの担当のタスクと `acceptance` が分かれる。条件を持たない課題に `--issue` が付かない。`single` の `files` が空なら `touches` を書かない |
| 実際のキュー | A → B の 2 単位を、別々の `ndf plan queue` の起動（A の後にマージの記録を置いてから B）で流すと B が流れる。依存の無い 2 単位は同じ起動で同じ組に入る |
| 工程の流し方 | 単位ごとに作業場所とブランチが分かれ、`review` の argv が単位のブランチを持ち、`ci merge` を単位の作業場所で打つ。A をマージ済みにして B で止めた後の打ち直しで A に `ci merge` を打たない。`ci_pending` では次の実行順を始めない。`--max` がレーンの `max_plans` とマシンの上限の小さい方 |
| 承認とマージ | `design-review` の既定のレビューの argv が `--purpose design --findings <再構成の指摘>` を持つ。Draft を外すステップが CI の前にある。変更の無い経路で承認が 1 回。マージの argv が `--reviewed <design_head>`。HEAD を変えるとマージせずに止まる |
| 工程 | 5 つのモードを `test_sprint_templates.py` の偽の流しで最後まで流す。`standard`・`legacy-refactor` で「計画」の位置と phase、設計承認ゲートのステップが「ドキュメントレビュー」に 1 つ、`mvv_gate` が「ドキュメントレビュー」の出力。`light`・`operation`・`documentation` の並びとステップが変わらない。`per_issue` だけを持つ保存済みのスナップショットを読める |
