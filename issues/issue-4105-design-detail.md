# #4105 の詳細設計: リリース承認ゲートの保留を読む範囲（組み立ての時点・判定する PR・含む PR・印）

設計文書 [issue-4105-design.md](./issue-4105-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。前提は #3959 の実装が main へ入った後の形である（[issue-3959-4055-design-detail.md](./issue-3959-4055-design-detail.md) の `validation_rows`・`review_rows`・`review_holds`・`review_prs`・`MvvFacts.review_holds`・`check_review_holds`・`MVV_SKIP_GATES`・`Snapshot.commit`、`gate_outcome` の `holds`）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/workflows/
├── release-cd.yml                             … workflow_dispatch の入力 sprint → release-steps assemble --sprint
└── ndf-release-reusable.yml                   … workflow_call の入力 sprint（任意）→ 空でなければ --sprint
plugins/ndf/
├── skills/release/assets/workflows/ndf-release.yml … 利用先の雛形。入力 sprint を宣言して with: sprint へ渡す
├── contracts/
│   ├── workflow/stages/deploy.json            … 先頭に release-prepare（ステップの when）
│   ├── workflow/ndf.schema.json               … $defs.step に when
│   ├── workflow/context.schema.json           … $defs.mvv_gate に pr・scope・note
│   └── templates/release-prepare.json *       … 組み立ての worker のプラン
├── src/ndf/adapters/github.py                 … compare（merge base とコミット）
├── src/ndf/domains/records/
│   ├── cli.py                                 … --machine の入力に lane.release_inputs を足す
│   ├── mvv_gate.py                            … release_target・review_prs（release）・_judged
│   └── mvv_material_decide.py                 … gate_outcome に pr・scope・note
├── src/ndf/progress/gate/checks.py           … note が読んだ PR・範囲を確定できない理由を載せる
├── src/ndf/domains/release/
│   ├── record_decide.py                       … included_line・assembled_commits・sprint_of・assembled_message（_approved_message を置き換える）
│   ├── record.py                              … pull_commits（_release_commits を一般化）・assembled_included・_included
│   ├── assemble.py                            … open_release_numbers・assembles・owned・prepared・assemble（sprint の印）
│   ├── land.py                                … Lander.land（マージの前に記録を書ける形かを確かめる）
│   ├── cli.py                                 … prepared・assemble の --sprint
│   └── trail.py                               … mvv_counts
├── src/ndf/progress/
│   ├── lane/__init__.py                       … delegates_release・release_inputs
│   ├── definition.py                          … ステップの when を許す
│   ├── command.py                             … 条件の語 assembles_release
│   ├── invoke.py                              … 判定の入力 release_delegated・machine_id、worker の環境変数 NDF_MACHINE_ID
│   ├── model.py                               … MVV_SKIP_GATES・mvv_marked・mvv_skip・mvv_answer_void
│   ├── run.py                                 … ステップの when・own_release・StepProgress._answered
│   └── gate/
│       ├── records.py                         … answers
│       ├── asking.py                          … question
│       ├── answering.py                       … _docs_only_after
│       └── release.py                         … _request_fields（質問の PR のまま）
├── skills/release/SKILL.md                     … 組み立てを release-prepare へ、提示は判定のコメントの承認資料
├── skills/release/references/release-workflow.md … workflow の入力 sprint
├── skills/development-workflow/references/    … pace.md・approval-request.md・agent-layers.md・production-merge.md
└── tests/
    ├── domains/records/test_mvv_gate.py
    ├── domains/release/test_release_record.py
    ├── domains/release/test_release_assemble.py
    ├── domains/release/test_release_approval.py
    ├── domains/records/test_cli.py
    ├── progress/test_invoke.py
    ├── progress/test_lane.py
    ├── progress/test_model.py
    ├── progress/test_run.py
    ├── progress/test_definition.py
    └── progress/gate/test_gate_answer.py
```

依存の向き: `records.mvv_gate` が `domains.release.record`・`domains.release.assemble`（`open_release_numbers`・`assembles`・`owned`）・`domains.release.trail`（`mvv_counts`）・`core.branch_strategy` を読む。`release` は `records` と `progress` を読まない。`progress` は `records` と `release` を読まない（`model.mvv_marked` は `mvv_gate` の値の形だけを見る。ステップの条件 `assembles_release` は `command.facts_of` が今の `prod_branch` と同じくブランチ戦略の設定から読む）。リリースを委ねるかは `progress.lane` が決め、両方の入口（`invoke` と `records/cli.py` の `--machine`）が `lane.release_inputs` で判定の入力 `release_delegated`・`machine_id` に入れる（判定の本体の `records.mvv_gate` は `progress` を読まない。`records/cli.py` は #3959 の `--machine` で既にステートマシンを読む入口である）。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/stages/deploy.json` | 変更 | デプロイの工程の定義 | — | — | ステップ `release-prepare`（`ndf.plan`、プラン `release-prepare`、`when: ["assembles_release", "own_release"]`）を `release-mvv` の前に足す |
| `contracts/workflow/ndf.schema.json` | 変更 | 工程の定義の型 | — | — | `$defs.step` に `when`（工程と同じ語の配列）。語の列挙に `assembles_release`・`own_release` |
| `contracts/templates/release-prepare.json` | 新設 | 組み立ての worker のプラン | — | — | `release.json` と同じ形。worker の作業 `release-prepare` を 1 つ流す |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | — | `$defs.mvv_gate` に任意のキー `pr`（整数）・`scope`（`base`・`head`・`pulls`）・`note`（整数） |
| `src/ndf/adapters/github.py` | 変更 | GitHub の読み書き | — | `GitHub` | `compare(base, head) -> Compared`（新設。`merge_base_commit.sha` とコミット）。`compare_commits` は `compare(...).commits` を返す |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの前の判定 | `release.record`・`release.assemble`・`release.trail`・`branch_strategy` | モジュール | `release_target`（新設）・`review_prs`（`release` の分を変更）・`_judged`（変更）・`gate`（選べないときの止まり方） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（引数 `pr`・`scope` を足す） |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査と記録 | — | モジュール | `note`（読んだ PR・範囲を確定できない理由を足す） |
| `src/ndf/domains/release/record_decide.py` | 変更 | リリース記録の純粋な判定 | `included_decide` | モジュール | `included_line`・`assembled_message`（新設。`_approved_message` を消す）・`approved_included`・`approved_units`（失敗の扱いを変える） |
| `src/ndf/domains/release/record.py` | 変更 | リリース記録 | `record_decide`・`included_decide` | モジュール | `pull_commits`（新設。`_release_commits` を置き換える）・`assembled_included`（新設）・`_included`（`assembled_included` を呼ぶ） |
| `src/ndf/domains/release/assemble.py` | 変更 | 組み立て | `record_decide` | モジュール | `open_release_numbers`・`assembles`・`owned`・`prepared`（新設）・`open_releases`・`assemble`（引数 `sprint`） |
| `src/ndf/domains/release/land.py` | 変更 | マージ | `record` | `Lander` | `land`（記録を書く設定でマージの前に `assembled_included(require=False)` を当てる） |
| `src/ndf/domains/release/cli.py` | 変更 | リリースの CLI | `assemble` | モジュール | `prepared`（新設のサブコマンド）・`assemble` の `--sprint` |
| `src/ndf/domains/records/cli.py` | 変更 | 判定の CLI | `progress.lane` | モジュール | `--machine` の入力に `lane.release_inputs` を足す（#3959 の `_mvv_inputs` の後） |
| `.github/workflows/release-cd.yml`・`ndf-release-reusable.yml`・`skills/release/assets/workflows/ndf-release.yml` | 変更 | リリースの workflow | — | — | 入力 `sprint`（任意・既定は空）と `--sprint` への受け渡し |
| `src/ndf/domains/release/trail.py` | 変更 | 承認の証跡 | — | モジュール | `mvv_counts`（新設） |
| `src/ndf/progress/lane/__init__.py` | 変更 | レーン | — | モジュール | `delegates_release`・`release_inputs`（新設） |
| `src/ndf/progress/definition.py`・`run.py`・`command.py`・`invoke.py` | 変更 | 工程の定義と駆動 | `lane` | モジュール | ステップの `when` の評価（`run._step_when_off`）・語 `assembles_release`（`command.facts_of`）・`own_release`（`run.TABLE_FACTS`）・判定の入力（`invoke` が `lane.release_inputs` を足す）・worker の環境変数 `NDF_MACHINE_ID`（`StageRun.env`） |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | `MVV_SKIP_GATES`（値）・`mvv_marked`（新設）・`mvv_skip`（変更）・`mvv_answer_void`（新設） |
| `src/ndf/progress/run.py` | 変更 | 駆動 | `model` | `StepProgress` | `_answered`（変更） |
| `src/ndf/progress/gate/records.py` | 変更 | 回答の読み口 | `model` | モジュール | `answers`（変更） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認ゲートの質問 | — | モジュール | `question`（変更） |
| `src/ndf/progress/gate/answering.py` | 変更 | 回答の検証 | `model` | モジュール | `_docs_only_after`（MVV の回答を外す）・`recheck`（回答した者を渡す） |

## 組み立ての時点（`release-prepare`）

満たす決定: 決定 1・7・9、受け入れ条件 1

- `deploy.json` の `do` を `release-prepare` → `release-mvv` → `release-gate` → `release` にする。`release-prepare` の `metadata.ndf` は `kind: work`・`range: sprint_diff`（`release` と同じ）・`when: ["assembles_release", "own_release"]`、出力のスキーマは `stage-io.schema.json#/$defs/report`
- **ステップの条件**: `ndf.schema.json` の `$defs.step` に `when` を足す（工程の `when` と同じ語の列挙。すべて真のときだけ流す）。`run.py` はステップを始める前に `_step_when_off`（工程の `_when_off` と同じ求め方。外から求める語は `d.env.facts`、表から求める語は `TABLE_FACTS`）を評価し、偽の語があれば worker を起動せず、そのステップの記録を `finished`・`report: {"skipped": "<語>"}` で書く。求められなければ工程の `when` と同じく `precondition` で止まる
- **語 `assembles_release`**（`command.facts_of`）: 作業場所のブランチ戦略で `assemble.assembles(strategy)` が真
- **語 `own_release`**（`run.TABLE_FACTS`）: `lane.delegates_release(conn, machine_id, env)` が偽。`delegates_release` はスプリントが計画のレーンの行を持つ（`lane.sprint_row` が None でない）か、`external.organizer_dir(env)` が None でないときに真。`request_release` が出し先を決める条件と同じ
- `release-prepare.json` は `release.json` と同じ引数（`repo`・`worker`）で、`steps` は `{"id": "work", "run": ["{{worker}}", "release-prepare"]}`。出力の `outputs` は空でよい（判定は GitHub から読む。決定 2）。ステートマシンの ID はテンプレートの差し込みでなく、`invoke` が `StageRun.env` で足す環境変数 `NDF_MACHINE_ID`（定数 `invoke.MACHINE_ENV`。`NOTES_ENV` と同じ経路で、プランの子プロセスと worker へ継がれる）で渡す。工程から組むプランだけが持ち、単発の `ndf route` では無い
- **workflow の入力 `sprint`**（決定 9 の移行の順）: `ndf-release-reusable.yml` の `workflow_call.inputs` に `sprint`（`type: string`・`required: false`・`default: ""`）を足し、`assemble` のジョブは `SPRINT` が空でなければ `release-steps assemble` の引数に `--sprint "$SPRINT"` を足す。`release-cd.yml` の `workflow_dispatch.inputs` に `sprint`（任意・既定は空）を足し、`assemble` のジョブで同じく渡す。雛形 `ndf-release.yml` は `workflow_dispatch.inputs.sprint` と `with: sprint: ${{ inputs.sprint || '' }}` を足す。この 3 つと CLI の `--sprint` を同じ版で出す
- `skills/release/SKILL.md` の「公開する」の手順 1（組み立て）と CHANGELOG の本文を書く手順を、`release-prepare` の worker が打つ手順として分ける。worker の手順:
  1. `NDF_MACHINE_ID` が無ければ `precondition` で止まる
  2. `ndf release prepared --sprint "$NDF_MACHINE_ID"` を打つ。`precondition`（workflow が入力 `sprint` を持たない）ならその理由のまま止まる
  3. `outputs.pr` があり `outputs.broken` が空なら、組み立てを打たずに使い直す。変更要求の指摘（`NDF_STAGE_NOTES`）は直しのコミットか本文の編集で当てる
  4. `outputs.broken` がある（範囲が壊れた自分の PR）か、指摘が範囲・版・単位を変えるなら、組み立てを打たずに `precondition` で止まる。理由に `outputs.broken`（または範囲を変える指摘）と「#<outputs.pr> を閉じてから流し直す」を載せる（今の `ReleaseOpen` と同じく人が閉じる。worker は PR を閉じない。自分で閉じて組み直す手順は `#4157`）
  5. 開いた自分の PR が無いときだけ、`step=assemble` に `-f sprint="$NDF_MACHINE_ID"` を渡して組み立てる
  6. 組み立てたリリースブランチを取り出し、CHANGELOG の本文を書いて push する（今の「公開する」の手順のまま）
- `release` の worker は手順 2〜4（提示・回答・マージ・公開）だけを打つ。提示の承認資料は、組み立てる戦略ならリリースの PR の本文、組み立てない戦略なら今どおり `ndf release materials --pr <mvv_gate.pr>` で集めたもの（決定 8）。リリースの PR の本文は判定の後に編集しない（承認資料の「MVV 判定」の欄は今回の判定の出力が出所。決定 8）

### 関数の仕様（組み立て）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `delegates_release` | `progress/lane/__init__.py` | 新設 | `delegates_release(conn: sa.Connection, machine_id: str, env: Mapping[str, str]) -> bool` | スプリントがリリースをオーガナイザーに委ねるか（上の定義）。`request_release` の出し先の分岐もこの関数を通す |
| `release_inputs` | `progress/lane/__init__.py` | 新設 | `release_inputs(conn: sa.Connection, machine_id: str, env: Mapping[str, str]) -> dict[str, Any]` | 判定の入力に足す `{"release_delegated": delegates_release(…), "machine_id": machine_id}`。`invoke`（`NDF_STAGE_INPUT` を組むとき、`release-mvv` のステップだけ）と `records/cli.py` の `--machine`（#3959 の `_mvv_inputs` の後）の両方がこれを呼ぶ |
| `assembles` | `release/assemble.py` | 新設 | `assembles(strategy: Strategy) -> bool` | ブランチ戦略が版上げの PR を組み立てるか（範囲の取り方が `pr` でなく、版を持つ）。`assemble` の `NoReleasePr` の条件をこの関数で決める（判定・ステップの条件・組み立てで条件を 1 つにする） |
| `assemble` | `release/assemble.py` | 変更 | 引数 `sprint: Optional[str] = None` を足す | `sprint` があれば組み立てのコミットに行 `Ndf-Sprint: <sprint>` を書く。ほかは今のまま（同じ名前のリリースブランチが既にあれば今どおり `AssembleError`） |
| `prepared` | `release/assemble.py` | 新設 | `prepared(hub: GitHub, target: Target, formats: NameFormats, sprint: str, workflow: str) -> Optional[Prepared]`（`Prepared(pr: int, head: str, broken: Optional[str])`） | 先に `workflow`（設定の `cd.workflow`）のファイルを既定ブランチから読み（contents API 1 回）、`yaml.safe_load` で読んだ `on.workflow_dispatch.inputs` に `sprint` が無ければ `WorkflowOutdated`（`precondition`。理由に雛形の該当行と `NDF_TAG` の更新を名指す）。`on` は文字列キー `"on"` と真偽値キー `True` の両方を見る（PyYAML の `safe_load` は YAML 1.1 の規則で `on:` を `True` のキーとして読む）。次に本番宛ての開いたリリースの PR（`open_release_numbers`）が 1 つなら、その PR を `hub.pull` で 1 回読み、`hub.compare(本番の先端, pull.head_sha)` の 1 回の応答のコミットで `owned`（下）と `broken`（`assembled_message` の `ValueError` の文。無ければ None）を決める（同じ head の同じ応答で持ち主と範囲を決める）。持ち主なら番号・head・`broken`。印が違うか無い・2 つ以上なら `ReleaseOpen`（今の組み立てと同じ止まり方）。開いた PR が無ければ None |
| `sprint_of` | `release/record_decide.py` | 新設 | `sprint_of(message: str) -> Optional[str]` | 組み立てのコミットのメッセージの行 `Ndf-Sprint:` の値（無いか 2 つ以上なら None） |

### 異常系の処理

| 失敗 | 振る舞い |
| --- | --- |
| 組み立てが止まる（`ReleaseOpen`・`AssembleError`） | `release-prepare` が `stopped`。工程は判定へ進まない（今の `release` の止まり方と同じ） |
| 組み立てが済まないまま `release-prepare` が `ok` を返す | 判定が開いたリリースの PR を見つけられず、判定のステップが `stopped`（下の `release_target`）。承認ゲートへ進まない |
| 変更要求の後・準備の成功の後の中断から流し直す | `prepared` が同じスプリントの PR を返し、組み立てを打たない |
| 利用先の `ndf-release.yml` が入力 `sprint` を持たない（NDF だけを上げた） | `prepared` が `WorkflowOutdated` で `precondition`。何も作らずに `release-prepare` が `stopped`、理由に workflow の更新の手順 |
| 自分の PR の範囲が壊れている（組み立ての後に別のマージ） | `prepared` が `broken` 付きで返し、`release-prepare` が組み立てを打たずに `stopped`（`precondition`）。理由に壊れた理由と閉じる PR の番号。人が閉じて流し直すと組み立てから進む（自動の組み直しは `#4157`） |
| push の後・PR の作成の前に止まった | 再実行で `prepared` は None、組み立てが「ブランチが既にある」（`AssembleError`）で止まる。人がブランチを消して流し直す（自動の回復は `#4157`） |

### 単体テストの観点（組み立て）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| ステップの `when` | 両方真 → worker を起動する | `production_pr`・計画のレーン・`NDF_ORGANIZER_DIR` → 起動せず `finished`（`skipped`） | 語を求められない → `precondition` |
| `prepared` | 同じ印の開いた PR → その番号・head・`broken` None。`on:` のまま `sprint` を宣言した workflow（`safe_load` が `on` を `True` のキーで返す）を読む → 止まらない | 開いた PR が 0 → None。同じ印の PR の上に別のスプリントのマージ → 番号と `broken` に理由（`ReleaseOpen` にしない） | 印が違う・印が無い・2 つ → `ReleaseOpen`。workflow が `sprint` を宣言しない → `WorkflowOutdated` |
| `assemble`（印） | `sprint` あり → 組み立てのコミットに行 `Ndf-Sprint: <sprint>` | `sprint` が無い → 行を書かない（今のまま） | — |
| worker の止まり方（`release-prepare` の通しのテスト） | 範囲の壊れていない自分の PR → 組み立てを打たずに使い直す | 範囲・版を変える指摘 → `precondition` で止まる | `prepared` が `broken` を返す → 組み立てを打たず `precondition`、理由に閉じる PR の番号 |
| workflow | `release-cd.yml`・雛形が `sprint` を宣言し、再利用 workflow が空でなければ `--sprint` を渡す（YAML を読む検査） | 再利用 workflow を `sprint` 無しで呼ぶ → `--sprint` を足さない | — |
| 変更要求（`decide`） | リリース承認ゲートで変更要求 → `release-prepare` が流れ直し、`prepared` で使い直して `ReleaseOpen` で止まらない | 準備の後に中断 → 同じ | — |
| `invoke` | 工程から組むプランの子プロセスの環境に `NDF_MACHINE_ID` が入る | — | — |

## 判定する PR と含む PR

満たす決定: 決定 2・3・4・6・7、受け入れ条件 1・2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `compare` | `adapters/github.py` | 新設 | `compare(base: str, head: str, first: Optional[dict] = None) -> Compared`（`Compared(merge_base: str, commits: list[dict])`） | `compare_commits` と同じページ送り（`per_page`・`page`）で、最初のページの `merge_base_commit.sha` を一緒に返す。`base`・`head` は SHA を渡す（ブランチ名を渡さない）。`first` に同じ `base...head` の 1 ページ目の応答（同じ `per_page`）が渡れば、それを使い 2 ページ目から読む |
| `open_release_numbers` | `release/assemble.py` | 新設 | `open_release_numbers(hub: GitHub, production: str, base: str, formats: names_decide.NameFormats) -> list[int]` | 今の `open_releases` の本体（本番宛ての開いた PR を 1 ページ読み、head がリリースブランチの書式に合うか `base`（`base != production` のとき）の番号を小さい順に返す。読み切れなければ `AssembleError`）。`open_releases(hub, target, formats)` は `target.production`・`target.base` を渡してこれを呼ぶ |
| `release_target` | `records/mvv_gate.py` | 新設 | `release_target(hub: GitHub, probe: Strategy, inputs: Mapping[str, Any]) -> tuple[int, bool]` | リリースで判定する PR の番号と、持ち主の確かめが要るか（自分で組み立てる戦略で真）。`probe` は作業場所のブランチ戦略（探索用。`branch_strategy.load(root)`）。`inputs` に `release_delegated`（真偽値）と `machine_id`（文字列）が無ければ `TargetError`（両方の入口が `lane.release_inputs` で必ず足すので、欠けは入口の誤り）。`release_delegated` が真か `assemble.assembles(probe)` が偽なら文脈の `pr`。それ以外は `open_release_numbers` が 1 つならそれ（印の照合はこの関数でせず、`_judged` が判定する head を固定した後に、範囲と同じ compare の応答で `assemble.owned` を当てる。下の手順 4）。0・2 つ以上・文脈の `pr` が無い（委ねた・組み立てない戦略で）なら `TargetError`（「判定するリリースの PR を 1 つに決められない: <理由と開いた PR の番号>」） |
| `pull_commits` | `release/record.py` | 新設（`_release_commits` を置き換える） | `pull_commits(hub: GitHub, pull: Pull, base_sha: Optional[str], first: Optional[dict] = None) -> tuple[list[dict], str]` | PR のコミットと分岐点（compare の `merge_base_commit.sha`）。開いていれば `hub.compare(base_sha, pull.head_sha)` のコミットと merge base。マージ済み（`pull.merge_sha` あり）は今の `_release_commits` と同じく、マージのコミットの第 1 親から head まで（`hub.compare(第 1 親, pull.head_sha)`）。複数コミットの rebase merge では第 1 親が PR の中のコミットになり範囲が狭くなるが、マージ済みの PR を判定するのは組み立てない戦略の文脈の `pr` だけで、`scope` を付けないので省略には効かない（保留の表示の範囲だけに効く）。開いていて `base_sha` が None・マージのコミットが読めないなら `RecordError`（判定は `scope` を付けない） |
| `included_line` | `release/record_decide.py` | 新設 | `included_line(message: str) -> Union[Missing, Broken, list[int]]` | 「含む PR」の行の 3 つの状態: 行が無い（`MISSING`）・形が違う（`BROKEN`。`#<番号>` の並びでも `none` でもない、行が 2 つ以上）・番号（`none` は空の配列）。`included_from_message` は `included_line` が配列のときだけその値、ほかは None（今の呼び手のため） |
| `assembled_commits` | `release/record_decide.py` | 新設 | `assembled_commits(commits: Sequence[Mapping[str, Any]], head: str) -> list[Mapping[str, Any]]` | `head` から first-parent で辿った PR のコミット（`_first_parent_messages`）のうち、件名が `included_decide` の `RELEASE_TITLE` の形のもの（組み立てのコミット）を新しい順に返す。範囲も行の形も検証しない（持ち主の確かめ `prepared`・`owned` が使う） |
| `assembled_message` | `release/record_decide.py` | 新設（`_approved_message` を消す） | `assembled_message(commits: Sequence[Mapping[str, Any]], head: str, upstream: Callable[[str], bool]) -> Optional[str]` | `assembled_commits` が 1 つで、その `included_line` が配列ならそのメッセージ。`upstream(sha)` は SHA が本番の先端の祖先か（呼び手が compare の merge base で決める）。次は `ValueError`（どのコミットの件名と行かを文に入れる）: 組み立てのコミットが 2 つ以上・組み立てのコミットの行が `MISSING` か `BROKEN`・組み立てのコミットが無く件名の違うコミットだけが行を持つ・組み立てのコミットより上の first-parent のコミットに、第 2 親以降のどれかで `upstream` が偽のマージか、件名から PR の番号（`included_decide.number_of`）が出て行に無いものがある（組み立ての後にほかの変更が入った）。第 2 親以降がすべて `upstream` で真のマージ（GitHub のブランチの更新で本番を取り込んだ）は許し、その件名の番号も読まない。組み立てのコミットも行を持つコミットも無ければ None。件名の違う直しのコミットの行は読まない |
| `approved_included`・`approved_units` | `release/record_decide.py` | 変更（失敗の扱いと引数） | 引数 `upstream: Callable[[str], bool]` を足す | `assembled_message` を通す。`ValueError` は呼び出し元へ返す |
| `owned` | `release/assemble.py` | 新設 | `owned(commits: Sequence[Mapping[str, Any]], head: str, sprint: str) -> bool` | 呼び手が固定した head の compare の応答のコミットの `assembled_commits` が 1 つ以上で、すべての `sprint_of` が `sprint` か（純粋。GitHub を読まない）。`prepared`・`_judged` が、範囲の検証と同じ応答に当てる |
| `assembled_included` | `release/record.py` | 新設 | `assembled_included(hub: GitHub, commits: Sequence[dict], pull: Pull, production: str, production_sha: str, formats: NameFormats, *, require: bool) -> list[int]` | `upstream` を「`hub.compare(sha, production_sha)` の merge base が `sha`」で組んで `assembled_message` に渡す（親が 2 つのコミットがあるときだけ compare を打つ）。`production_sha` は判定では `base_sha`、リリース記録ではマージのコミットの第 1 親、マージの前の検査では本番の先端。ほかは今の `_included` の本体（組み立てのコミットがあれば行の番号、無ければ first-parent の件名から `included_decide.numbers`）。`require` が真（判定の組み立てる戦略）なら、組み立てのコミットが無い（`assembled_message` が None）とき件名へ戻さず `RecordError`。`ValueError` は `RecordError`（「組み立てのコミットの『含む PR』を 1 つに決められない: <原因>」）にする |
| `_included` | `release/record.py` | 変更 | シグネチャは同じ | `assembled_included(…, require=False)` を呼ぶ（リリース記録と判定で規則を 1 つにする。リリース記録は組み立てない戦略の件名の扱いを今のまま持つ） |
| `Lander.land` | `release/land.py` | 変更 | シグネチャは同じ | 記録を書く設定（`route.release_pr and route.bump`。`cmd_merge` の `records` と同じ条件）のとき、`_gate` の後・マージキューへ入れる前に、`_gate` が固定した本番の先端 `prod.tip` と `pull.head_sha` のコミットへ `assembled_included(…, require=assemble.assembles(strategy))` を当て、`RecordError` なら `precondition` で止まる（理由に「リリース記録を書けない形。PR を閉じて組み直す（`#4157` まで人が閉じる）」）。人の承認の有無によらない。組み立てる設定では組み立てのコミットが無い PR（手で開いた版上げの PR）も止める（件名へ戻さない。普及の前なので互換の経路は置かない）。compare は `_bump_facts` が読んだ同じ `prod.tip...head` の 1 ページ目の応答（`_bump_facts` を `per_page`・`page=1` 付きで読むようにし、`Gate.compared` に残す）を `hub.compare(…, first=)` に渡し、2 ページ目からだけを読む。`landing.method` が `direct`（`_bump_facts` を読まない）なら本番の先端を 1 回読んで全ページを読む |
| `mvv_counts` | `release/trail.py` | 新設 | `mvv_counts(strategy: Strategy) -> Optional[str]` | MVV の回答（`--by mvv` の証跡）を公開側が承認に数えない理由。`approval.via` が `review` なら理由（「approval.via: review は GitHub のレビューを求める」）、`merge.admin` が真なら理由（「管理者の迂回は利用者の証跡だけを数える」）、数えるなら None。呼び手は `is None` で読む（真偽値として読まない）。`trail_writer`・`_counts` と同じ設定を読む |
| `review_prs` | `records/mvv_gate.py` | 変更（#3959 の新設を変える） | `review_prs(gate: str, number: int, inputs: Mapping[str, Any], hub: GitHub, pull: Pull, strategy: Strategy, base_sha: str) -> tuple[tuple[int, ...], tuple[str, ...]]` | 保留を読む PR と、範囲を確定できない理由の並び（確定できれば空）。`design` は `((number,), ())`。`release` は下の手順の 6 |
| `_judged` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `gate_outcome` | `records/mvv_material_decide.py` | 変更 | 引数 `pr: Optional[int] = None`・`scope: Optional[Mapping[str, Any]] = None` を足す | `pr`・`scope` が None でなければ出力に入れる |
| `_recorded` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | コメントを書けたら、その ID を出力の `note` に入れる。書けなければ #3959 の `noted: false` のまま、`note` を入れない |
| `note` | `progress/gate/checks.py` | 変更 | シグネチャは同じ（`outcome` から読む） | `scope` があれば「保留を読んだ PR: #a #b …」、無ければ範囲を確定できない理由の行を足す。承認の依頼の「MVV 判定」の欄はこのコメントでなく今回の出力 `mvv_gate` を写す（#3959 の「承認資料の出所」） |

### 処理の手順（`_judged` の `release`）

#3959 の `_judged` の手順のうち、判定する PR を決める手順・`base_sha` の取り方・保留を読む手順を変える。`base_sha` は #3959 と同じく「判定する PR の宛先を fetch した先端」で、判定する PR を文脈の `pr` から `release_target` の PR へ差し替える。

1. `probe = branch_strategy.load(root)`（作業場所の今の宣言。判定する PR を探すためだけに使う）
2. `number, check_owner = release_target(hub, probe, inputs)`（`design` は今どおり `pr_number`）。`TargetError`・`AssembleError`・`GitHubError` は判定のステップを止める（下の異常系）
3. `pull = hub.pull(number)`（#3959 が判定する head を先に読む手順と同じ 1 回）。以降の持ち主の確かめ・範囲・保留はすべてこの `pull.head_sha` を使い、PR を読み直さない
4. `base_sha` = `pull.base` を fetch した先端（#3959 の `Snapshot.commit` の取り方。`release_branch`・`trunk` の `release_pr` では本番ブランチ、組み立てない戦略では文脈の `pr` の宛先）。MVV と `strategy = branch_strategy.load_at_ref(root, base_sha)` をこの SHA で読む。範囲を確定できない理由の並び `unsettled: list[str]` を空で始め、`strategy` の本番ブランチ・ベースブランチ・書式が `probe` と違う・`assemble.assembles(strategy) != assemble.assembles(probe)`（探索した時と固定した SHA で組み立てるかが違う）・組み立てる戦略で `pull.base` が本番ブランチでない、のそれぞれで理由を足す。続けて `commits, fork = pull_commits(hub, pull, base_sha)` を 1 回だけ読む（`hub.compare(base_sha, pull.head_sha)`。固定した SHA どうしの 1 つのスナップショット）。`check_owner` が真なら、この `commits` に `assemble.owned(commits, pull.head_sha, inputs["machine_id"])` を当て、偽なら `TargetError` で判定のステップを止める（手順 2 の後に PR が別の組み立てに差し替わっても、読んだ head の組み立てのコミットで持ち主を決める）。ここでの compare の `GitHubError` は、`check_owner` が真なら判定のステップを止め（持ち主を決められない）、偽なら下の手順 6 と同じく `scope` を付けない
5. 差分・機械の検査・LLM の判定は #3959 のまま、`number` の PR に当てる。判定の記録のコメントも `number` の PR へ書く
6. `prs, found = review_prs(gate, number, inputs, hub, pull, strategy, base_sha)` の後、`unsettled.extend(found)`（手順 4 の理由を上書きしない）:
   - 手順 4 の `commits, fork` を使う（compare を打ち直さない）。開いた PR で `fork != base_sha` なら理由「リリースの PR がベースの先端（`base_sha`）を含まない」
   - 委ねたスプリント（`inputs["release_delegated"]`）: 理由「リリースをオーガナイザーに委ねたので含む PR を確定できない」。番号は件名から（`assembled_included(…, require=False)`）で、保留の表示に使う
   - 組み立てる戦略: `assembled_included(hub, commits, pull, strategy の本番ブランチ, base_sha, formats, require=True)` の番号
   - 組み立てない戦略: 番号は first-parent の件名から（`assembled_included(…, require=False)`）で、保留の表示に使う。理由「組み立てない戦略は承認資料の集合の記録が無い」を足す（`scope` を付けない）
   - 番号に `number` と文脈の `pr`（整数なら）を足した和（小さい順・重複なし）
7. 読めた行で `review_holds(rows, broken, repo, prs, mvv)`。`unsettled` が空なら `scope = {"base": base_sha, "head": pull.head_sha, "pulls": list(prs)}`、空でなければ `scope` は None で、`unsettled` のすべてを `why` に足す（`skip` 偽）
8. `why_not = trail.mvv_counts(strategy)`。`why_not is not None` なら `why` に足して `skip` 偽（`scope` は付けたまま）。`if why_not:` のような真偽値の読み方をしない
9. `gate_outcome(gate, verdict, skip, why, pull.head_sha, holds=…, pr=number, scope=scope)`。#3959 の `release` の暫定の理由（「#4105 まで省かない」）は消す。`_recorded` がコメントの ID を `note` に入れる

### 異常系の処理

| 失敗 | 振る舞い |
| --- | --- |
| `release_target` の `TargetError`（自分で組み立てる戦略で開いたリリースの PR が 0 か 2 つ以上・印が今のスプリントと違うか無い・文脈の `pr` が無い・入力に `release_delegated` か `machine_id` が無い） | 判定のステップが `stopped`（`precondition`）。出力の `mvv_gate` を書かず、コメントを書かず、承認ゲートへ進まない。理由に開いた PR の番号を載せる |
| `open_release_numbers` の `AssembleError`（1 ページで読み切れない）・`GitHubError`（PR の一覧・判定する PR を読めない） | 同上（`AssembleError` は `precondition`、GitHub の不達と 5xx は `transient`） |
| `load_at_ref` の `StrategyError`・MVV を読めない（PR は選べた） | `verdict: null`・`skip` 偽・`pr` は判定した PR。`scope` なし。コメントは判定した PR へ書く |
| `pull_commits` の `GitHubError`・`RecordError`、`assembled_included` の `RecordError` | 判定の後の `may_skip` の前に、`why` に理由を入れて `skip` 偽。判定の記録は判定した PR のコメントへ書く。`pr` は判定した PR、`scope` なし |
| 記録（`mvv-validation.jsonl`）を読めない | #3959 のまま（保留を「読めない」の 1 件にする）。範囲は確定しているので `scope` は書く（保留があるので省かれない） |
| 判定の記録のコメントを書けない（`noted: false`） | `note` を出力に入れない。`scope` は残す（組み立てる戦略は資料が PR の本文にある。組み立てない戦略はもともと `scope` を持たない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `included_line` | `#1 #2` → `[1, 2]` | `none` → `[]`・行が無い → `MISSING` | `#x`・空・行が 2 つ → `BROKEN` |
| `assembled_commits` | 組み立ての件名のコミット 1 つ → それ | 上に別のスプリントのマージがある → 組み立てのコミットを返す（範囲を検証しない） | — |
| `assembled_message` | 組み立ての件名と形どおりの行を持つコミット 1 つ → そのメッセージ | 行の無い直しのコミット・`Ndf-Included-Pulls: none` を持つ直しのコミットが上に積まれている → どちらも組み立てのコミットのメッセージ。組み立てのコミットも行も無い → None。上に第 2 親が `upstream` で真のマージ（本番の取り込み）→ 組み立てのコミットのメッセージ | 組み立ての件名で行を持つコミットが 2 → `ValueError`。組み立てのコミットの行が無い・形が違う → `ValueError`。件名が `Release: …` でないコミットだけが行を持つ → `ValueError`。組み立てのコミットの上に第 2 親が `upstream` で偽のマージ・行に無い PR の件名のコミット → `ValueError` |
| `pull_commits` | 開いた PR → `compare` に `base_sha` と head の SHA（ブランチ名を渡さない）、merge base を返す | マージ済みの PR → マージのコミットの第 1 親から head（`_release_commits` と同じ） | 開いていて `base_sha` が None・マージのコミットが読めない → `RecordError` |
| `assembled_included`・`_included` | 行の番号（PR のコミットの外の番号を含む） | 行が無い → `require=False` は first-parent の件名から。本番の取り込みのマージ → 行の番号のまま、リリース記録も同じ番号 | `require=True` で組み立てのコミットが無い → `RecordError`。`ValueError` → `RecordError`。リリース記録の `record` も同じ入力で止まる |
| `Lander.land` | 記録を書ける形のリリースの PR → 今どおりマージキューへ。`merge-queue` で compare の 1 ページ目を `_bump_facts` と共有し、検査が 1 ページ目を読み直さない（スタブの呼び出しの数） | 記録を書かない設定（組み立てない戦略）→ 検査しない | 組み立ての後に本番以外からマージした PR・組み立てる設定で組み立てのコミットが無い PR → マージキューへ入れず `precondition`（承認の証跡があっても） |
| `release_target` と持ち主の確かめ（`_judged`） | 開いたリリースの PR が 1 で今のスプリントの印 → その番号。`owned` と範囲が同じ compare の応答から決まる（compare が 1 回） | `production_pr` → 文脈の `pr`・`release_delegated` → 文脈の `pr`（持ち主を確かめない） | 0 で `release_pr`・2 つ → `TargetError`。唯一開いた PR の印が違う（別のスプリント）・印が無い（ベースブランチからの PR）→ `TargetError`。入力に `release_delegated` か `machine_id` が無い → `TargetError`。PR の一覧を読んだ後にリリースブランチが別のスプリントの組み立てへ差し替わる（スタブが `hub.pull` で新しい head を返す）→ 新しい head の印で `TargetError`、古い head の印で新しい head に `scope` を付けない |
| `release_inputs`（両入口） | 同じステートマシンで、`invoke` が組む `NDF_STAGE_INPUT` と `--machine` の入力が同じ `release_delegated`・`machine_id` を持つ | 計画のレーンのスプリントで `--machine` から判定 → `release_delegated` 真、`mvv_gate.pr` が文脈の `pr`（開いた別のリリースの PR があっても選ばない） | — |
| `mvv_counts` | `approval.via: gate`・`merge.admin` が偽 → None | — | `approval.via: review` → 理由・`merge.admin` が真 → 理由 |
| `review_prs`（`release`） | 組み立てる戦略で merge base が `base_sha` → 理由の並びが空 | `merge_base_branch` で本番からベースへの同期のマージ（本番の先端が第 2 親）の後の組み立て → merge base が `base_sha` で空 | merge base が `base_sha` でない → 理由「ベースの先端を含まない」。委ねたスプリント → 理由。組み立てない戦略 → 番号は件名から・理由「承認資料の集合の記録が無い」 |
| `_judged`（`release`） | `trunk`・`release_pr` で、行に版上げの PR の差分に無い単位の PR、その PR に `redline` → `fast` で「従う」でも `skip` 偽・`holds` にその PR・`scope.pulls` にその番号・`pr` がリリースの PR | `release_branch` の 2 スプリントの行 → `scope.pulls` が組み立ての `facts.pulls` と同じ。保留が無い → `skip` 真・`scope` あり。`base_sha` がリリースの PR の宛先（本番）の先端で、スプリントの PR の宛先でない。組み立てない戦略 → `skip` 偽・`scope` なし・`holds` は載る | 組み立てのコミットが 2・行の欠け・ベースより遅れ → `skip` 偽・`scope` なし・`pr` あり・`why` とコメントに理由。`base_sha` の戦略が探索の戦略と違う（組み立てるかが違う形を含む）うえにコミットの範囲は読める → `scope` なし・`why` に設定の不一致（手順 6 で消えない）。`approval.via: review` → `skip` 偽。`release_target` の失敗 → ステップが `stopped`。組み立てる戦略で `assemble._facts`・`_span`・`_since_by_unit` を呼ばない（スタブが呼ばれない） |
| 承認の受け方ごと（`_judged` → `mvv_skip` → `decide`） | `approval.via: gate`・`merge.admin` が偽・保留なし → 承認ゲートが `service:mvv` の回答で `CONSUME` | — | `approval.via: review`・`merge.admin` が真 → 同じ入力で承認ゲートが `GATE`（人を待つ） |

## 省略の条件と回答の読み口

満たす決定: 決定 2・5・6、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MVV_SKIP_GATES` | `progress/model.py` | 変更（値） | `("design", "release")` | MVV 判定で省いてよい承認ゲートの区分 |
| `mvv_marked` | `progress/model.py` | 新設 | `mvv_marked(held: Mapping[str, Any]) -> bool` | 判定の出力が、区分に要る印を持つか。`release` は `pr` が整数で、`scope` が辞書で `base`・`head` が 40 桁の 16 進・`pulls` が整数の配列（空でない）のとき真。`design` は常に真 |
| `mvv_skip` | `progress/model.py` | 変更 | シグネチャは同じ | 今の条件（#3959 の `gate in MVV_SKIP_GATES` を含む）に `mvv_marked(held)` を足す |
| `mvv_answer_void` | `progress/model.py` | 新設 | `mvv_answer_void(payload: Mapping[str, Any]) -> Optional[str]` | 回答した者が `MVV_SERVICE` の回答で、`payload[MVV_KEY]` が辞書でないか `mvv_marked` が偽なら理由（「MVV 判定の回答が保留を読んだ範囲を持たない（旧版の判定）」）。ほかは None。#4106 はこの関数に判定の対象との照合を足す |
| `StepProgress._answered` | `progress/run.py` | 変更 | シグネチャは同じ | 変更要求でない回答のうち `mvv_answer_void(p)` が理由を返すものは、`answer` に残さない（承認ゲートは人を待つ）。事象は消さない |
| `answers` | `progress/gate/records.py` | 変更 | シグネチャは同じ | `mvv_answer_void` が理由を返す回答を一覧から外す（`approved_head` がその回答の head を返さない） |
| `question` | `progress/gate/asking.py` | 変更 | シグネチャは同じ | リリース承認ゲートでは、待ちの文脈（`_waiting_context`。直前の判定の出力が重なる）の `mvv_gate.pr` が整数ならそれを質問の PR にする（`scope` の有無によらない）。無い（MVV の判定を流さない旧版の出力）ときだけ今どおり `pr`。`request_release` のリクエストの `prs`・`recheck` の head・`approved_head` が返す head はすべてこの質問の PR から決まる |
| `_docs_only_after` | `progress/gate/answering.py` | 変更 | 引数 `by: Optional[str]` を足す | `by` が `model.MVV_SERVICE` なら偽（MVV の回答の後に加わった範囲は、文書だけでも出し直す）。`recheck` は回答の payload の `by` を渡す |

### 異常系の処理

| 失敗 | 振る舞い |
| --- | --- |
| 印の無い `release` の判定の `skip: true`（旧版の出力） | `mvv_skip` が None。`_consume` は `service:mvv` の回答を書かず、承認ゲートは人を待つ |
| 印の無い判定を添えた未消費の `service:mvv` の回答（旧版が書いた事象） | `_answered` が読まず、`decide` は `GATE`。`ndf answer head` は None か人の回答の head |
| `scope` の形が違う（`pulls` が空・`base` が短い）・`pr` が無い | 印が無いのと同じ |
| `service:mvv` の回答の後に head が進む（文書だけでも） | `recheck` が承認ゲートのステップを待ちへ戻す（戻し先はこの 1 か所。設計の決定 5 と同じ）。`release-mvv`・`release-prepare` は流し直さない。待ちの間は `run.py` の `prog.waiting is None` の条件で `mvv_skip` が当たらないので、同じ判定で再び省かずに人を待つ（待ちへ戻した後の回答だけが読まれる）。承認の依頼の「MVV 判定」の欄は、`scope.head` と今の head が違うことを添える |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `mvv_skip` | `release`・`skip: true`・`pr` と `scope` あり → 省く | `design` は `scope` が無くても今どおり省く | `release`・`skip: true`・`scope` なし（#3959 より前の版の形）→ None |
| `mvv_answer_void` | 印のある回答 → None | 人の回答 → None | 印の無い `service:mvv` の `release` の回答 → 理由 |
| `decide`（受け入れ条件 4） | 印のある判定から省いた回答 → `CONSUME` | #3959 の版で人の承認待ち（`waiting`）を挟んだ後に、印の無い判定の出力の上で更新 → 人を待つ | #3959 より前の版が書いた未消費の `service:mvv` の回答から始める → `GATE`、`approved_head` が None |
| `question` | リリース承認ゲートの質問の PR が `mvv_gate.pr` | `scope` が無い → `mvv_gate.pr`。`mvv_gate` が `pr` を持たない → 文脈の `pr` | 判定の後にリリースの PR の head が進む → `recheck` が待ちへ戻す。リクエストの `prs` が質問の PR |
| `recheck`（文書だけ） | 人の回答の後に文書だけが加わる → `_accept`（今どおり） | — | `service:mvv` の回答の後に文書だけが加わる → `_accept` せず待ちへ戻し、次の `decide` が `GATE`（`release-mvv` の結果を空にせず、`service:mvv` の回答を新たに書かない） |

## 手順書

| 手順書 | 変えること |
| --- | --- |
| `skills/release/SKILL.md` | 「公開する」の組み立てと CHANGELOG の本文を `release-prepare` の worker の手順にし（上の「組み立ての時点」の手順 1〜6: `NDF_MACHINE_ID`・`prepared`・使い直し・範囲が壊れたときの止まり方・`-f sprint` 付きの組み立て）、`release` の worker の手順から外す。組み立てない戦略の提示は今どおり `materials` で集める。`release-prepare` と `release-steps merge` が範囲の壊れた PR で止まったときに、人が PR を閉じて流し直す手順。リリースの PR の本文は判定の後に編集しない |
| `skills/release/references/release-workflow.md` | workflow の `step=assemble` の入力 `sprint` を足す。既存の利用先の移行の順（NDF を上げるときに `ndf-release.yml` へ入力 `sprint` の宣言と `with: sprint` を足し、`NDF_TAG` を同じ版へ上げる。足さないと `ndf release prepared` が止まる） |
| `skills/development-workflow/references/pace.md` | 「具体例」の 4 と「MVV 判定」の表の順 4 を、判定する PR（`mvv_gate.pr`。自分で組み立てるなら本番宛ての開いたリリースの PR、組み立てない戦略と委ねたスプリントは文脈の `pr`）・保留を判定した PR・文脈の `pr`・含む PR から読むこと・省く条件（`scope`・`approval.via: gate`・`merge.admin` が偽）に書き換える。#3959 が書いた「リリースの保留は #4105 まで読まない」を消す |
| `skills/development-workflow/references/approval-request.md` | リリース承認ゲートの「MVV 判定」の欄の出所を、#3959 の設計承認ゲートと同じく今回の判定の出力 `outputs.mvv_gate` にし（PR の番号でコメントを探さない）、`scope.pulls`（保留を読んだ PR）か範囲を確定できない理由・`pr`・`scope.head` と今の head の違いを写す。#4105 の断りを消す |
| `skills/development-workflow/references/agent-layers.md` | #3959 が足した「手動の経路でリリース承認ゲートを返す前に `--gate release --machine` で打つ」の前に、組み立てを済ませることを足す |
| `skills/development-workflow/references/production-merge.md` | 「選んで集めるリリース方式のリリース」の順（組み立て → 承認資料 → 承認）が、どの戦略のデプロイの工程でも同じになったこと、レーンでは組み立てをオーガナイザーが持つことを 1 行で足す |
