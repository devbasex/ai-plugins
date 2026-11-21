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
│       ├── context.schema.json                       # 文脈のキー rebuild_findings・design_head・plan_omitted を足す
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
│   │   ├── cli.py                                    # ndf plan units check / render
│   │   └── inputs_decide.py                          # design_gaps の単位の数の規則を消し、設計の結果の表の読み手（扱い・統合先を含む）を置く
│   ├── core/route/__init__.py                        # 既定のレビューの起動で design-review も --purpose design にする
│   ├── domains/materials/collect.py                  # 設計の結果の表の読み手と設計文書の番号の並びを inputs_decide のものへ寄せる
│   ├── ci_cli.py / adapters/github.py                # ndf ci merge に --pr・--base を足し、マージ済み・キューの待ちを判定する
│   └── progress/
│       ├── stages.py / definition.py                 # per_unit を足す（per_issue は残す）
│       ├── invoke.py                                 # per_unit の工程は単位ごとの作業場所・プラン・キュー・マージで流す。ndf.plan にも工程の入力を渡す
│       └── command.py                                # ndf progress units plans / run（supervisor が手で流す入口。工程と同じ処理）
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
| `src/ndf/core/plan/inputs_decide.py` | 変更 | `design_gaps` から「PR の単位が 2 つ以上」の理由を消す（決定 2）。設計の結果の表の行の読み手 `design_results`（課題・扱い・統合先・触るファイル）と設計文書の名前の番号の並び `DESIGN_DOC` を置く（下の「設計の結果の表の読み手」） | 無し |
| `src/ndf/domains/materials/collect.py` | 変更 | `design_rows`・`DESIGN_FILE` を消し、`inputs_decide.design_results`・`DESIGN_DOC` に替える（読み手を 1 つにする） | `inputs_decide` |
| `src/ndf/ci_cli.py`・`src/ndf/adapters/github.py` | 変更 | `ndf ci merge` に `--pr <番号>`・`--base <宛先>` を足す（下の「マージの対象の照合と再開」） | 無し |
| `src/ndf/core/route/__init__.py` | 変更 | `review_launch` の `--purpose design` を、テンプレート `design` と `design-review` の両方で選ぶ | 無し |
| `contracts/workflow/ndf.schema.json` | 変更 | 工程のメタデータに `per_unit`（`const: true`）を足す。`per_issue` は残す | 無し |
| `contracts/workflow/context.schema.json` | 変更 | 文脈のキー `rebuild_findings`（再構成の指摘のファイル）・`design_head`（承認に使う設計 PR の HEAD）・`plan_omitted`（実装計画を省いた課題の番号の配列）を足す | 無し |
| `contracts/workflow/stages/plan.json` | 変更 | `phase: design` と `outputs: ["plan_omitted"]`（確かめのステップは足さない） | 無し |
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
| `src/ndf/progress/invoke.py` | 変更 | `per_unit` の工程の `ndf.plan` のステップを、単位ごとの作業場所・プラン・`ndf plan queue`・マージで流す。`ndf.plan` の起動にも `NDF_STAGE_INPUT` を渡す | `units_files` |
| `src/ndf/progress/command.py` | 変更 | `ndf progress units plans`・`ndf progress units run`（工程の外から同じ処理を打つ入口） | `invoke` |
| `tests/adapters/test_ci_cli.py` | 変更 | `--pr`・`--base` の照合、マージ済みの成功、キューの待ち | 無し |
| `tests/core/plan/test_units_decide.py` | 新設 | 判定の単体テスト（受け入れ条件 2・5・7・8、U1〜U6・W1〜W2） | fixture |
| `tests/core/plan/test_units_cli.py` | 新設 | コマンドのテスト（受け入れ条件 3・4・6、`render --check`、ファイルの集め方） | fixture |
| `tests/core/plan/fixtures/units/issue-3672.units.json` | 新設 | #3672 の詳細設計の地図から作った 1 単位のファイル（受け入れ条件 8） | 無し |
| `tests/core/plan/test_inputs.py` | 変更 | `design_gaps` の単位の数のテストを消し、`design_rows` のテストを `design_results` へ移す（扱い・統合先・統合文書の #11 の行） | 無し |
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
| `judge(units, limits, designed: Optional[Mapping[int, DesignResult]] = None) -> Judgement` | U1〜U6 の違反、W1〜W2 の警告、行数の規則に当たった単位ごとの分け方の案 | 投げない。違反があっても `views` は実行順の取れる範囲で返す |
| `split(unit: Unit, units: Units, limits: Limits) -> Proposal` | 決定 6 の案（下の「分け方の案の作り方」） | 1 つのタスクで `max` を超えるときは、そのタスクだけの単位を置き、`note` に「T<n> を書き手が対象ファイルごとに分け直す」と書く。案を作れないとき（下の 4・6）は `units` を空にし、`resplit` と `note` で書き手に返す |
| `apply(units: Units, proposal: Proposal) -> Units` | 元の単位を案の単位に置き換え、`rewired` の単位（元の単位を指していた後続）を差し替えたグラフ | 投げない |
| `single(issue: int, designed: Optional[DesignResult]) -> Units` | 単位のファイルを持たない課題を、課題 1 つを 1 単位（タスク 1 つ、`lines` は None、`files` は設計の結果の触るファイル、`criteria` は空、`whole` は真＝その課題のすべての条件）にする（受け入れ条件 4） | 設計の結果の無い課題は `files` を空にする（プランに `touches` を書かず、キューはすべてと重なるとみなして 1 本ずつ流す） |

規則の判定:

| 規則 | 当たる条件 |
| --- | --- |
| U1 | タスクの `id`・単位の `name` が重なる。タスクの `after` か単位の `after` が無いものを指す。タスクのグラフか単位のグラフが輪になる（同じ単位の中のタスクの輪も含む） |
| U2 | タスクがどの単位にも無い、または 2 つ以上の単位にある。単位の `tasks` が無いタスクを指す。タスクの `issue` がファイルの `issues` の外にある。`criteria` の課題がそのタスクの `issue` と違う。単位が受け入れ条件を 1 つも持たず、`whole` のタスクも持たない（条件を持たない準備のタスクは、条件を持つ単位へ束ねる。`single` の単位は `whole` で課題のすべての条件を持つので当たらない） |
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

   足すたびに `orders` を取り直し、U4 の組が無くなるまで繰り返す（足す依存は組 1 つに 1 本で、組の数は有限なので止まる）。終わった後のグラフで `judge` を通し、違反（U6 の `note` 付きの単位を除く）が残らないことをテストで縛る

### 単位のファイルの読み書き（`core/plan/units_files.py`）

満たす決定: 2・5・7。満たす受け入れ条件: 3・4・6。

| 関数 | 入力 → 返り | 異常系 |
| --- | --- | --- |
| `collect(repo: Path, issues: Sequence[int], omitted: Sequence[int]) -> Collected` | 下の「単位のファイルの集め方」。`omitted` は工程「計画」が実装計画を省くと決めた課題（文脈の `plan_omitted`）。返りは読んだ単位のファイル（パスの一意な並び）・ファイルを持たない課題とその扱い（`single`・`skip`） | 下の表 |
| `load(path: Path) -> Units` | スキーマ（`review-units.schema.json`）で確かめて `parse` | 読めない・形が違うと `UnitsError`（理由にパスとスキーマの誤り） |
| `designed(repo: Path, issues: Sequence[int]) -> dict[int, DesignResult]` | 名前の番号の並び（`inputs_decide.DESIGN_DOC`）に課題の番号を含む設計文書（`issues/issue-10-11-design.md` は #10 と #11 の両方）の「設計の結果」の表を `inputs_decide.design_results` で読み、課題ごとに行を 1 つにする（`materials.collect` と同じ読み手）。表に行の無い課題は返りに載せない | 読めない文書は飛ばす（今の `design_files` と同じ）。同じ課題の行が 2 つの文書で扱いか統合先が食い違えば `UnitsError` |
| `limits(repo: Path) -> Limits` | `.ndf/project.json` の `workflow.review_units` を先に、無いキーは `contracts.limits.value("review-units.target-lines", "count")` と `max-lines` | `target >= max` は `UnitsError` |
| `render(units, judgement, doc: str) -> str` | `units`・`judgement` は**その文書と対の単位のファイル 1 つだけ**を `merge` せずに `judge` したもの（タスクの `id`・単位の名前に接頭辞が無く、実行順・マージの順はそのファイルの中で数える）。実装計画の文書の 2 つの区間（`<!-- ndf:units:begin -->`〜`<!-- ndf:units:end -->` と `<!-- ndf:order:begin -->`〜`<!-- ndf:order:end -->`）を作り直した全文。前者は「レビューの単位」の表（単位・実行順・マージの順・含むタスク・先に要る単位・満たす受け入れ条件・見込みの行数・実際の行数＝`actual_lines`）、後者は「並列と直列」の表（実行順ごとに並列の単位と理由「同じ実行順で依存が無く、触るファイルが重ならない」。単位が 1 つの実行順は理由を「—」。依存ごとに直列の 2 単位と依存の理由） | 区間の印が無いか片方だけなら `UnitsError` |
| `parallel_cap(environ: Mapping[str, str], lane_plans: Optional[int]) -> int` | 同じ実行順で同時に流す本数の上限。`min(lane_plans, limits.from_env(environ, "NDF_RELAY_MAX_NAMED", "relay.max-named", "count"))`（`lane_plans` が None ならマシンの上限だけ）。マシンの上限は `progress/lane/machine.cap` と同じ読み方 | 投げない |
| `unit_plans(repo, given: UnitGiven, out: Path) -> list[UnitPlan]` | 下の「単位のプランの組み立て」 | 違反があれば何も書かずに `UnitsError`（理由に違反の行） |

#### 単位のファイルの集め方（`collect`）

実装計画の Markdown と単位のファイルには、同じ名前の規則を当てる。**計画の名前**は `issues/issue-<番号の並び>-plan` で、番号の並びは `-` で区切った 1 つ以上の番号（`inputs_decide.PLAN_DOC = ^issues/issue-(\d+(?:-\d+)*)-plan(\.units\.json|\.md)$`）である。Markdown（`.md`）と単位のファイル（`.units.json`）は、計画の名前が同じものを対にする。

1. `issues/` の下で `PLAN_DOC` に当たるファイルのうち、番号の並びのどれかがスプリントの課題の番号と一致するものを、計画の名前ごとに集める（統合の計画 `issue-10-11-plan.*` は #10 と #11 のどちらからも当たるが 1 回だけ読む）
2. 計画の名前ごとに次を確かめる
   - 単位のファイルがあれば、その `issues` がスプリントの課題の中にあり、名前の番号の並びと一致する
   - Markdown に `<!-- ndf:units:begin -->` の印があるのに、同じ名前の単位のファイルが無ければ止める（`UnitsError`「実装計画 `<パス>` があるのに単位のファイル `<名前>.units.json` が無い」）。`issue-10-11-plan.md` に印があり `issue-10-11-plan.units.json` が無いときも、#10・#11 のどちらも省いた扱いに落とさない
   - 課題が 2 つの計画の名前に現れれば止める
3. どの計画の名前にも現れない課題は次の順で扱いを決める。**実装計画を省いてよいのは、工程「計画」が省くと決めて `plan_omitted` に載せた課題だけ**で、ファイルが無いことから省いたとは推さない

| 課題の状態 | 扱い |
| --- | --- |
| 設計の結果の表で扱いが「実装する」でない（統合する・閉じる） | `skip`（単位を作らない。統合先の課題の単位が持つ）。統合先がスプリントの外なら止める |
| 計画の名前に現れ、Markdown に印が無く単位のファイルも無い（この変更より前に書いた実装計画） | `single`（今の振る舞いのまま）。`check` は `warnings` に「旧い形の実装計画で行数を確かめていない」を載せる |
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

- 行の型: 表の列は今の「課題・扱い・統合先・触るファイル」の順で、`row[0]` の `#<番号>` を `issue`、`row[1]` を `handling`（前後の空白を除いた値。「実装する」「統合する」「閉じる」）、`row[2]` の `#<番号>` を `into`（「—」や空なら None）、`row[3]` を `files_cell` で `files` と `delegated` にする。列が 4 つに満たない行は飛ばす（今の `design_rows` と同じ）
- 統合文書の探索: `design_numbers` が番号の並び全体を返すので、`issue-10-11-design.md` は #11 からも見つかり、その文書の #11 の行を読める
- 既存の材料収集: `materials.collect.design_files(root, number, files)` は `DESIGN_FILE.match` を `number in design_numbers(rel)` に、`design_rows(text, number)` を `[r.files for r in design_results(text) if r.issue == number]` の和に替える。返りの形（文書ごとの触るファイル）は変えない

#### 単位のプランの組み立て（`unit_plans`）

`UnitGiven` は工程が渡す値（スプリントの課題 `issues`・省いた課題 `omitted`・スプリントのブランチ `sprint`・リモート・状態の保存先・テンプレートの差し込みの組み立て `values_of(unit) -> 値`）である。

1. `collect(repo, given.issues, given.omitted)` → 各ファイルを `load` → ファイルを持たない `single` の課題を足して `merge` → `designed` → `judge`。違反があれば何も書かない。ファイルをまたぐ U4 もここで当たる
2. 単位に、スプリント全体で一意な連番 `seq` を、実行順 → マージの順（ファイルのパスの順 → `units[]` の並び → `single` の課題の番号の順）で振る。プランの名前・ブランチ・作業場所はどれも `<sprint>-u<seq>`、出力は `out/<sprint>-u<seq>.json`（別のファイルどうしで同じ名前にならない）
3. 単位ごとに指示のファイル `out/<sprint>-u<seq>.unit.json` を書く: 単位の名前・代表の課題・単位の課題・担当のタスク（`id`・`title`・`files`・`criteria`）・実装計画の文書のパス・「このファイルの tasks だけを実装し、ほかのタスクに手を付けない」の一文。`single` の単位は担当のタスクを「課題の全体」とする
4. 差し込み: `issue` は代表の課題、`acceptance` は単位の課題のうち条件を持つ課題ごとに `--issue <課題>` と、その課題の条件ごとに `--criteria <課題>:<条件>`（条件を持たない課題には `--issue` を付けない。全部の条件を照らさないため）。`single` の単位は `--issue <課題>` だけ（課題のすべての条件）。`unit_file` は 3 の指示のファイル。残りの差し込み（題・本文・`review`・`test_command` など）は `given.values_of(unit)` が組む（下の工程の流し方）
5. プランの最上位へ `touches`（単位の触るファイル。空なら書かない）を足す。`needs` は**同じ実行順のプランだけ**を指す（同じ実行順には依存が無いので、実際には書かない）。前の実行順への依存は、工程が前の実行順のマージを確かめてから次のキューを打つことで守る（今の `ndf plan queue` は起動ごとに済んだプランを空から数え、別の起動で済んだ `needs` を解けないため）。単位の依存は `items[].after` と指示のファイルに残す
6. 返りは実行順・マージの順に並べた `UnitPlan(path, unit_file, name, order, merge, branch)`

### コマンド（`core/plan/cli.py`）

満たす受け入れ条件: 3・5・6。結果 JSON の `tool` は既存の `TOOL`（`plan`）。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `units check [<ファイル>...] [--issue N]... [--omitted N]... [--from-stage] [--docs]` | ファイル・`--issue`・`--from-stage`（環境変数 `NDF_STAGE_INPUT` の `issues` と `plan_omitted`）のどれかで単位のファイルを `collect` の規則で集め（手で打つときの省いた課題は `--omitted`）、`designed` を読んで（ファイルを直に渡したときも、その課題の設計の文書があれば）**`merge` した後のグラフ全体**で `judge` を打つ（ファイルをまたぐ U4 もここで当たる）。`--docs` は加えて、印のある実装計画の文書ごとに、**対の単位のファイル 1 つだけ**のグラフで `render` を作り、今の文書と照らす（`render <そのファイル> --doc <その文書> --check` と同じ判定を同じ起動の中で行う。表は書き手が `render` で作った形と同じになる） | 違反が無ければ `ok`（`items` に `unit` と `split`、`warnings` に W1・W2 と旧い形の文、`metrics` に `units`・`orders`・`target`・`max`・`omitted`・`head`（確かめた HEAD））。違反・表の食い違いがあれば `stopped`（`invalid`、`items` に `violation` と `split`。案を作れない単位の `split` は `units` が空で `resplit` と `note` を持ち、書き手にタスクの切り直しを求める）。読めない・宣言の誤り・書き忘れは `stopped`（`invalid`） |
| `units render <ファイル> --doc <実装計画> [--check]` | `render` で全文を作り、`--check` が無ければ書く（worker が手元で使う。単位のファイルが複数あるスプリントでも、ファイルごとに打つ） | `ok`。`--check` で食い違えば `stopped`（`invalid`、理由「表が単位のファイルと食い違う」） |

単位のプランを組んで流す手動の入口は `ndf plan` でなく `ndf progress units`（`progress/command.py`）に置く。題・本文・既定のレビューの起動は単位ごとに違い、それを組む `template_values` が `progress` にあるためである。

| サブコマンド | 振る舞い | 返り |
| --- | --- | --- |
| `ndf progress units plans --out <ディレクトリ> --sprint <ブランチ> [--remote R] [--issue N]... [--omitted N]... [--from-stage] [--set 名前=値]... [--lane-plans N]` | 工程と同じ `values_of`（下の工程の流し方の 1）で `unit_plans` を打つ。単位ごとの差し込み（`issue`・`acceptance`・`unit_file`・`title`・`body`・`review`・`branch`・`worktree`・`base`）は単位ごとに組み、`--set` で与えると `stopped`（`invalid`、「単位ごとに組む差し込み <名前> は --set で与えない」）。`--set` は**それ以外の差し込みにだけ全単位に共通に**当たる（`test_command` など） | `ok`（`items` に `{"kind": "plan", "path", "unit_file", "unit", "order", "merge", "branch", "after"}`、`metrics.max` に `parallel_cap` の値）。違反は `stopped`（`invalid`） |
| `ndf progress units run --out <ディレクトリ> --sprint <ブランチ> [--remote R]` | `plans` が書いたプランを、下の工程の流し方の 2〜7（実行順ごとの作業場所・キュー・マージ・記録・再開・スプリントの worktree の早送り）と同じ関数で流す。打ち直すと記録から続ける | 工程の `_call_units` と同じ（`ok`・`ci_pending`・`stopped`） |

### 工程の流し方（`progress/invoke.py`）

満たす決定: 1・3・7。満たす受け入れ条件: 3・4。

`per_unit` の工程（`implement-units`）の `ndf.plan` のステップは、`_call_plan` の中で `_call_units(run, cwd, work_dir, runner)` に分ける。`per_unit` でない工程は今のまま。

1. **組み立て**: `units_files.unit_plans` に `values_of(unit)` を渡す。`values_of` は、単位の値で写した入力（`issues` を代表の課題 1 つ、`worktree` を単位の作業場所、`branch` を単位のブランチ、`base` をスプリントのブランチ）の `StageRun` を作り、それで `template_values` を打つ。したがって題・本文は代表の課題について既存の `_issue_values` の信頼の確かめとキャッシュ（`ISSUE_BODY_FILE`・`ISSUE_TITLE_FILE`）を通り、既定の `review` は単位のブランチで組まれ、設定の `workflow.launch.review`・`test.ci.only` もそのまま効く。スプリントの課題が複数でも、単位ごとに課題 1 つの入力になるので題・本文が欠けない
2. **作業場所**: 実行順 k を始めるときに `git fetch <remote> <sprint>` し、その実行順の各単位について `ndf worktree create <sprint>-u<seq> --from <remote>/<sprint>` で、前の実行順のマージをすべて含む HEAD から単位専用のブランチと作業場所を用意する（既にあれば作り直さない）。単位の PR の宛先はスプリントのブランチ（差し込み `base`）
3. **キュー**: `ndf plan queue <その実行順のプランのパス>... --max <parallel_cap>` を打つ。`lane_plans` はスプリントがレーンで動くとき、そのレーンの `max_plans`（並列の計画の `max_plans`、省けば既定）。supervisor が手で流すときは `ndf progress units run` が同じ値を使う
4. **マージの前の記録**: キューが `ok` で終われば、その実行順の単位ごとに、**最初のマージの要求より前に** `work_dir/units/<seq>.json` へ PR の番号（`gh pr view <単位のブランチ> --json number,baseRefName` で 1 回だけ読む）・宛先（スプリントのブランチ）・レビュー済みの HEAD（そのプランの状態ディレクトリの値）・状態 `queued` を書く。記録を書けなければマージを要求しない
5. **マージ**: 記録のある単位をマージの順に、**単位の作業場所を cwd にして** `ndf ci merge --branch <単位のブランチ> --pr <記録の番号> --base <スプリントのブランチ> --reviewed <記録の HEAD>` を打つ。返りで記録の状態を書き換える（`ok` は `merged`、マージキューへ入れた・入っている `ci_pending` は `queued` のまま）。判定は下の「マージの対象の照合と再開」が `ci merge` の中で持つので、工程の側で `gh pr view` を打ち直さない
6. **再開**: 打ち直したときは、記録が `merged` の単位を飛ばし（API を呼ばない）、`queued` の単位には 5 と同じ `ci merge` を打つ。`ci merge` はマージ済みなら `ok`、キューの中なら `ci_pending`、キューから外れていればマージを要求し直し、HEAD がレビュー済みの HEAD と違えば止める（`refused`。工程は `precondition` で止め、プランを打ち直してレビューから）。記録の無い単位（4 の前に止まった）は 3 のキューの結果から始める。どれかが `ci_pending` なら次の実行順を始めずに `ci_pending` で返す
7. **次の実行順と最後**: その実行順のすべての記録が `merged` になってから 2 へ戻る。最後の実行順まで済めば、スプリントの worktree（工程の cwd）で `git fetch <remote> <sprint>` と `git merge --ff-only <remote>/<sprint>` を打ち、ローカルの HEAD をマージ済みの先端まで進めてから工程の残りのステップ（`gathered`。`ndf progress gathered` は `<remote>/<sprint>` がローカルの HEAD の祖先であることを求める）へ進む。早送りできない（スプリントの worktree に push していないコミットがある）なら止める（`precondition`、理由「スプリントの worktree が <remote>/<sprint> から分かれている」）

| 関数 | 変える前 → 後 | 異常系 |
| --- | --- | --- |
| `_call_plan(run, cwd, work_dir, answer, runner)`（責務の変更） | 前: `plan_path` で 1 本書き `ndf plan next` を打つ。後: 工程が `per_unit` なら `_call_units` へ渡す | — |
| `_call_units(run, cwd, work_dir, runner)`（新設） | 上の 1〜7（2〜7 は `run_units(plans, sprint, remote, cwd, work_dir, runner)` に分け、`ndf progress units run` も同じ関数を呼ぶ） | `UnitsError`・`TemplateValuesMissing` は工程の失敗（`cause.kind: invalid`、理由をそのまま）。queue とマージの失敗は今の `_from_process` のまま返し、次の実行順を始めない |
| `plan_queue_argv(paths, cap, cwd) -> list[str]`（新設） | `ndf_command` と同じ形で `plan queue … --max <cap>` の argv | 投げない |
| `unit_merge_argv(branch, number, base, reviewed) -> list[str]`（新設） | `ndf_command` と同じ形で `ci merge --branch <branch> --pr <number> --base <base> --reviewed <reviewed>` の argv。打つ cwd は呼び手が単位の作業場所にする | 投げない |
| `_call_plan` の環境（責務の変更） | 前: `ndf.plan` の `ndf plan next` に工程の入力を渡さない（`NDF_STAGE_INPUT` は `ndf.script` だけ）。後: `ndf.script` と同じく `run.env({INPUT_ENV: json.dumps(run.inputs)})` を渡す。プランのステップの子（`ndf plan units check --from-stage` など）は環境を引き継ぐ | — |

`stages.py` の工程の型と `definition.py:321` の読み手に `per_unit` を足す（既定は偽）。`per_issue` は改名せずに残す（`ndf.schema.json` の既存のキーと、保存済みのスナップショットの `per_issue` を今のまま読めるようにするため。`_stage_from` はキーをそのまま `StageDef` に渡すので、`per_unit` を持たない古いスナップショットは既定の偽で読める）。

### マージの対象の照合と再開（`ndf ci merge --pr --base`）

満たす決定: 1・7。設計 PR（`design-land`）と単位の PR（工程「実装」）の両方が使う。今の `ci merge --branch` はブランチの開いた PR を引き直してその最初の PR をマージし、マージ済みなら開いた PR が無いので止まる。承認・レビューした PR と違う PR をマージしない、マージキューの後に打ち直せる、の 2 つのため `--pr <番号>`（承認・記録した PR）と `--base <宛先>` を足す。`--pr` を渡したときは次の順で判定する（PR を 1 回読む。`--pr` を省いた今の呼び出しは変えない）

| PR の状態 | 返り |
| --- | --- |
| 番号の PR の head のブランチが `--branch` と違う・宛先が `--base` と違う | `refused`（「#<番号> は承認した対象（<ブランチ> → <宛先>）と違う」）。開き直した・宛先を変えた PR はマージしない |
| `MERGED` で、`headRefOid` が `--reviewed` と同じ | `ok`（「既にマージ済み」。マージキューが済ませた後の打ち直し） |
| `MERGED` で、`headRefOid` が `--reviewed` と違う／`CLOSED` | `refused` |
| `OPEN` で、`headRefOid` が `--reviewed` と違う（作業場所の HEAD の確かめは今のまま先に行う） | `refused` |
| `OPEN` で、`GitHub.in_merge_queue(番号)` が真 | `ci_pending`（マージの要求を打ち直さない） |
| `OPEN` で、キューに無い（入っていない・外された） | 今のマージの要求（キューがあれば登録して `ci_pending`、無ければマージして `ok`） |

API の呼び出しは、マージ済みの打ち直しで PR の読み 1 回、キューの待ちで読みとキューの確かめの 2 回に収まる。

### 設計 PR の承認とマージ（`design-review`・`design-land`）

満たす決定: 1。満たす受け入れ条件: 1。工程「ドキュメントレビュー」（`design-approval`）のステップの中身である。

| ステップ | 中身 | 失敗したとき |
| --- | --- | --- |
| `docs-review`（テンプレート `design-review`） | ① `{{review}} --findings {{rebuild_findings}}`（既定の起動は `--purpose design`。`rebuild_findings` は工程「ドキュメント再構成」の出力）→ ② 直しの push → ③ `ndf plan units check --from-stage --docs`（**最終の HEAD の単位のファイルと表を 1 回だけ確かめる**。工程の入力 `issues`・`plan_omitted` は `_call_plan` が渡す `NDF_STAGE_INPUT` から読む）→ ④ Draft を外す（`gh pr view <design_pr> --json isDraft` が真のときだけ `gh pr ready <design_pr>`。Ready 済みの PR での打ち直しでは打たずに進む）→ ⑤ conflict-check → ⑥ ci（既存の部品）→ ⑦ HEAD の照合（下）を通った SHA を `design_head` として報告に載せる | ③ が止まれば修正の worker が単位のファイルか文書を直して ② へ戻る。⑥ の失敗は ci-fix → push → ① へ戻る（HEAD が変わったのでレビューと ③ をやり直し、④ は Ready 済みなので飛ばす）。マージ先の取り込み（base-sync）も ① へ戻る。⑦ の照合が食い違えば ① へ戻る |
| `design-mvv` | 今のまま（`ndf records mvv gate --gate design`） | 今のまま |
| `design-gate` | 承認を 1 回だけ求める。承認資料に設計 PR の URL・`design_head`・`units check` の結果を載せる。承認の記録は `design_pr`・宛先（スプリントのブランチ）・`design_head` の組で持つ | 差し戻しは今のまま。承認で課題を外したときは下の「承認で課題を外したとき」 |
| `design-merge`（テンプレート `design-land`） | conflict-check → 設計の作業場所で `ndf ci merge --branch <branch> --pr <design_pr> --base <スプリントのブランチ> --reviewed <design_head>`（判定は「マージの対象の照合と再開」）。承認のステップ（`merge-gate`）を持たず、プランの中で承認を聞き直さない。マージキューの待ち（`ci_pending`）で返った工程の打ち直しは、承認の記録（`design_pr`・宛先・`design_head`）がある限り `docs-review` からやり直さず `design-merge` から続けて同じ argv を打ち、マージ済み（PR が `MERGED` で HEAD が `design_head`）なら成功として工程を終える | 衝突・HEAD の食い違い・番号か宛先の食い違いで承認した対象のままマージできなければ、push せずに止まる（`precondition`、理由「承認した PR と HEAD のままマージできない」）。工程を打ち直すと `docs-review` から始まり、`design_head` と違う HEAD の承認の記録は使わない（レビュー・MVV 判定・承認をやり直す） |

**HEAD の照合（⑦）**: 次の 4 つの SHA が同じときだけ `design_head` を書く。③ の `metrics.head`（単位を確かめた HEAD）、⑦ の時点のローカルの HEAD、`gh pr view <design_pr> --json headRefOid`（PR の HEAD）、⑥ の `ndf ci watch` の結果の `instance`（CI を判定した PR の HEAD）。どれかが違えば（⑥ の後に push があった、PR を別の HEAD で更新した）`design_head` を書かずに ① へ戻る。承認の記録はこの `design_head` と組なので、承認した PR の HEAD・CI が通った HEAD・単位を確かめた HEAD は 1 つに結び付き、`design-land` の `--reviewed` と `--pr` がマージ要求の時点で同じ組を照らす。

**承認で課題を外したとき**: 既存の設計承認ゲートは承認の回答で課題を外せる。外した課題を含む計画（たとえば `issue-10-11-plan.units.json` から #11 を外す）は、そのままでは実装の `collect` が「#11 がスプリントの外」で止まり、残った課題の単位にも外した課題の条件・依存が残る。そこで、課題を外した回答は `design_head` の承認として扱わず、工程を「計画」へ戻す（`design-merge` を打たない）。「計画」は残った課題で実装計画と単位のファイルを書き直し（外した課題のタスク・条件・依存を除く。統合の計画なら名前の番号の並びも書き直す）、「ドキュメント再構成」「ドキュメントレビュー」を通って、新しい `design_head` で承認を 1 回求め直す。

変更の無い経路（レビューが収束し CI が通り、衝突が無い）では、承認は `design-gate` の 1 回だけである。

### 工程の定義（`contracts/workflow/`）

満たす決定: 1。満たす受け入れ条件: 1・4。

| 定義 | 変えた後 |
| --- | --- |
| `modes/standard.json` | 要求 → 作業場所 → 設計（`design-draft`）→ 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ …（行の数 18 は変わらない） |
| `modes/legacy-refactor.json` | 作業場所 → 設計（`design-draft`）→ 詳細設計 → **計画** → ドキュメント再構成 → ドキュメントレビュー（`design-approval`）→ 実装（`implement-units`）→ … |
| `modes/light.json`・`operation.json`・`documentation.json` | 変えない（`design.json`・`document-review.json`・`implement.json` を指したまま。`documentation` の「計画」はドキュメントレビューの後のまま） |
| `stages/plan.json` | `phase: design`。ステップ `plan`（`implementation-plan`）だけ。`outputs: ["plan_omitted"]`（実装計画を省いた課題。省かなければ空の配列）。実装計画を書くのはこの工程だけで、工程「設計」は書かない（設計書を省いた課題も同じ） |
| `stages/design-draft.json` | ステップ `design`（`design-draft`）だけ。`outputs: ["design_pr"]` |
| `stages/design-approval.json` | `inputs`: `issues`・`worktree`・`branch`・`mode`・`pace`・`mvv_ref`・`design_pr`・`rebuild_findings`・`plan_omitted`。`docs-review` → `design-mvv` → `design-gate`（modes は `legacy-refactor`・`standard`）→ `design-merge`。`outputs: ["mvv_gate", "design_head"]` |
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
| 承認した設計 PR を閉じて開き直した・宛先を変えた | `ci merge --pr --base` が `refused`。マージせずに止まり、レビューから打ち直す |
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
| 規則 | U2〜U6・W1・W2 のそれぞれの最小の入力で `rule` が出る。当たらない入力では出ない。`files` にディレクトリ（末尾 `/`）を書くとスキーマが断る |
| 上限の境 | 400 は何も出ない、401 は W1、1,000 は W1、1,001 は U6。宣言 `target_lines: 200` で 201 が W1 |
| 分け方の案 | 同じ深さで同じファイルを触る 600 行のタスク 2 つの単位で、案が 2 単位を直列にし、`apply` した後の `judge` に違反が無い。元の単位を指す後続が案の最後の層を指す。元の単位と同じファイルを触る別の単位が実行順 2 にあると、案の後半との U4 を依存の追加で直し、グラフ全体の `judge` に違反が無い。その別の単位が別のファイルにあれば `resplit` を返す。条件なしの準備 500 行と、それに依存する条件ありの実装 600 行は `resplit: ("T1", "T2")` で案を作らない |
| #3698・#3247 | 16,757 行・24,008 行の 1 単位で U6、案のどの単位も 1,000 以下 |
| #3672 | fixture を 1 単位にすると U6、案の単位の実行順が 契約 → 読む部品と解析 → 付け替え → 削除とテスト の順に沿う |
| 集め方 | 統合の計画（`issue-10-11-plan.units.json`）を 1 回だけ読む。2 つのファイルの同じ実行順の単位が同じファイルを触ると U4。新しい形の計画に単位のファイルが無ければ止まる（`issue-10-11-plan.md` に印があり JSON が無いときも）。旧い形と `plan_omitted` の課題は `single` で、`check` と `plans` の両方が U2 に当たらず通る。計画も `plan_omitted` も無い課題は止まる |
| 設計の結果の表 | `design_results` が扱い・統合先を読み、`issue-10-11-design.md` の #11 の行を #11 から読める。`materials.collect.design_files` の返りが今と同じ |
| 表の照合 | 2 つの計画のファイルを `render` で 1 つずつ書いた後、まとめて `check --docs` が `ok`（表はファイルごとのグラフ、U4 は併せたグラフ） |
| マージの照合 | `ci merge --pr --base` が、番号か宛先の違いで `refused`、マージ済みで HEAD が同じなら `ok`、キューの中なら要求を打たずに `ci_pending`、キューから外れていれば要求し直す |
| プラン | 2 つのファイルからのプランの名前と出力のパスが重ならない。同じ課題の 2 単位で指示のファイルの担当のタスクと `acceptance` が分かれる。条件を持たない課題に `--issue` が付かない。`single` の `files` が空なら `touches` を書かない |
| 実際のキュー | A → B の 2 単位を、別々の `ndf plan queue` の起動（A の後にマージの記録を置いてから B）で流すと B が流れる。依存の無い 2 単位は同じ起動で同じ組に入る |
| 工程の流し方 | 単位ごとに作業場所とブランチが分かれ、`review` の argv が単位のブランチを持ち、`ci merge` を単位の作業場所で `--pr <記録の番号>` 付きで打つ。PR の番号とレビュー済みの HEAD の記録がマージの要求より前に書かれる。A をマージ済みにして B で止めた後の打ち直しで A に `ci merge` を打たない。`ci_pending` では次の実行順を始めない。`--max` がレーンの `max_plans` とマシンの上限の小さい方。最後のマージの後にスプリントの worktree を早送りしてから `gathered` が `ok` になる。`ndf progress units plans` で 2 課題の単位の題・本文・`review` が単位ごとに違い、単位ごとの差し込みを `--set` で与えると止まる。`ndf progress units run` が実行順 1 のマージの後に実行順 2 を流す |
| 承認とマージ | `design-review` の既定のレビューの argv が `--purpose design --findings <再構成の指摘>` を持つ。Draft を外すステップが CI の前にあり、Ready 済みの PR では `gh pr ready` を打たない。CI を落として直した後、レビューと `units check` をやり直して承認まで進む。4 つの HEAD のどれかが違えば `design_head` を書かない。変更の無い経路で承認が 1 回。マージの argv が `--pr <design_pr> --base <スプリントのブランチ> --reviewed <design_head>`。HEAD を変えるとマージせずに止まる。マージキューの待ちの後の打ち直しでマージ済みを成功にする。承認で課題を外すと「計画」へ戻る。本物の `ndf` で `design-review` のプランを流し、`units check --from-stage` が工程の `issues` を受け取る |
| 工程 | 5 つのモードを `test_sprint_templates.py` の偽の流しで最後まで流す。`standard`・`legacy-refactor` で「計画」の位置と phase、設計承認ゲートのステップが「ドキュメントレビュー」に 1 つ、`mvv_gate` が「ドキュメントレビュー」の出力。`light`・`operation`・`documentation` の並びとステップが変わらない。`legacy-refactor` が `design_pr` と `rebuild_findings` を出して `design-approval` と `implement-units` を最後まで流す。`per_issue` だけを持つ保存済みのスナップショットを読める |
