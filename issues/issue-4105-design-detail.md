# #4105 の詳細設計: リリース承認ゲートの保留を読む範囲（組み立ての時点・判定する PR・含む PR・印）

設計文書 [issue-4105-design.md](./issue-4105-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。前提は #3959 の実装が main へ入った後の形である（[issue-3959-4055-design-detail.md](./issue-3959-4055-design-detail.md) の `validation_rows`・`review_rows`・`review_holds`・`review_prs`・`MvvFacts.review_holds`・`check_review_holds`・`MVV_SKIP_GATES`・`Snapshot.commit`、`gate_outcome` の `holds`）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/workflows/
├── release-cd.yml                             … workflow_dispatch の入力 sprint（任意）→ 空でなければ --sprint（再利用 workflow と同じ配列の形）
└── ndf-release-reusable.yml                   … workflow_call の入力 sprint（任意）→ 空でなければ --sprint
plugins/ndf/
├── skills/release/assets/workflows/ndf-release.yml … 利用先の雛形。入力 sprint を宣言して with: sprint へ渡す
├── contracts/
│   ├── workflow/stages/deploy.json            … 先頭に release-prepare（ステップの when）
│   ├── workflow/ndf.schema.json               … $defs.step に when
│   ├── workflow/context.schema.json           … $defs.mvv_gate に pr・scope・note
│   └── templates/release-prepare.json *       … 組み立ての worker のプラン
├── src/ndf/adapters/github.py                 … compare（merge base とコミット）・merge_base（merge base だけ）・Pull.head_repo
├── src/ndf/domains/records/
│   ├── cli.py                                 … --machine の入力に lane.release_inputs を足す
│   ├── mvv_gate.py                            … release_target・review_prs（release）・_judged
│   └── mvv_material_decide.py                 … gate_outcome に pr・scope・note
├── src/ndf/progress/gate/checks.py           … note が読んだ PR・範囲を確定できない理由を載せる
├── src/ndf/domains/release/
│   ├── record_decide.py                       … included_line・assembled_commits・sprint_of・changelog_done・assembled_message（_approved_message を置き換える）
│   ├── record.py                              … pull_commits（_release_commits を一般化）・upstream・assembled_included・_included
│   ├── assemble.py                            … open_release_numbers・assembles・owned・release_pull_off・rebuild_inputs・recovery・workflow_passes_sprint・prepared・assemble（sprint の印）
│   ├── land.py                                … Lander.land（マージの前に記録を書ける形かを確かめる）
│   ├── cli.py                                 … prepared（--sprint・--pr・--head）・assemble の --sprint
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

依存の向き: `records.mvv_gate` が `domains.release.record`・`domains.release.assemble`（`open_release_numbers`・`assembles`・`owned`）・`domains.release.trail`（`mvv_counts`）・`core.branch_strategy` を読む。`release` は `records` と `progress` を読まない（`release.assemble` は `release.record` の `upstream` を読む。`record` は `assemble` を読まない）。`progress` は `records` と `release` を読まない（`model.mvv_marked` は `mvv_gate` の値の形だけを見る。ステップの条件 `assembles_release` は `command.facts_of` が今の `prod_branch` と同じくブランチ戦略の設定から読む）。リリースを委ねるかは `progress.lane` が決め、両方の入口（`invoke` と `records/cli.py` の `--machine`）が `lane.release_inputs` で判定の入力 `release_delegated`・`machine_id` に入れる（判定の本体の `records.mvv_gate` は `progress` を読まない。`records/cli.py` は #3959 の `--machine` で既にステートマシンを読む入口である）。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/workflow/stages/deploy.json` | 変更 | デプロイの工程の定義 | — | — | ステップ `release-prepare`（`ndf.plan`、プラン `release-prepare`、`when: ["assembles_release", "own_release"]`）を `release-mvv` の前に足す |
| `contracts/workflow/ndf.schema.json` | 変更 | 工程の定義の型 | — | — | `$defs.step` に `when`（工程と同じ語の配列）。語の列挙に `assembles_release`・`own_release` |
| `contracts/templates/release-prepare.json` | 新設 | 組み立ての worker のプラン | — | — | `release.json` と同じ形。worker の作業 `release-prepare` を 1 つ流す |
| `contracts/workflow/context.schema.json` | 変更 | 文脈の型 | — | — | `$defs.mvv_gate` に任意のキー `pr`（整数）・`scope`（`base`・`head`・`pulls`）・`note`（整数） |
| `src/ndf/adapters/github.py` | 変更 | GitHub の読み書き | — | `GitHub` | `compare(base, head) -> Compared`（新設。`merge_base_commit.sha` とコミット）・`merge_base(base, head) -> str`（新設。1 ページ目だけ）。`compare_commits` は `compare(...).commits` を返す。`Pull` に `head_repo: str = ""`（head のリポジトリの `<所有者>/<名前>`。REST は `head.repo.full_name`、GraphQL は `headRepository.nameWithOwner`。head のリポジトリが消えていれば空）を足す |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの前の判定 | `release.record`・`release.assemble`・`release.trail`・`branch_strategy` | モジュール | `release_target`（新設）・`review_prs`（`release` の分を変更）・`_judged`（変更）・`gate`（選べないときの止まり方） |
| `src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定の材料と出力 | — | モジュール | `gate_outcome`（引数 `pr`・`scope` を足す） |
| `src/ndf/progress/gate/checks.py` | 変更 | 省かない理由の検査と記録 | — | モジュール | `note`（読んだ PR・範囲を確定できない理由を足す） |
| `src/ndf/domains/release/record_decide.py` | 変更 | リリース記録の純粋な判定 | `included_decide` | モジュール | `included_line`・`changelog_done`・`assembled_message`（新設。`_approved_message` を消す）・`approved_included`・`approved_units`（失敗の扱いを変える） |
| `src/ndf/domains/release/record.py` | 変更 | リリース記録 | `record_decide`・`included_decide` | モジュール | `pull_commits`（新設。`_release_commits` を置き換える）・`upstream`（新設）・`assembled_included`（新設）・`_included`（`assembled_included` を呼ぶ） |
| `src/ndf/domains/release/assemble.py` | 変更 | 組み立て | `record_decide`・`record`（`upstream`） | モジュール | `open_release_numbers`・`assembles`・`owned`・`release_pull_off`・`rebuild_inputs`・`recovery`・`workflow_passes_sprint`・`prepared`（新設）・`open_releases`・`assemble`（引数 `sprint`） |
| `src/ndf/domains/release/land.py` | 変更 | マージ | `record` | `Lander` | `land`（記録を書く設定でマージの前に `assembled_included(require=False)` を当てる） |
| `src/ndf/domains/release/cli.py` | 変更 | リリースの CLI | `assemble` | モジュール | `prepared`（新設のサブコマンド。`--sprint` 必須、`--pr`・`--head` 任意）・`assemble` の `--sprint` |
| `src/ndf/domains/records/cli.py` | 変更 | 判定の CLI | `progress.lane` | モジュール | `--machine` の入力に `lane.release_inputs` を足す（#3959 の `_mvv_inputs` の後） |
| `.github/workflows/release-cd.yml`・`ndf-release-reusable.yml`・`skills/release/assets/workflows/ndf-release.yml` | 変更 | リリースの workflow | — | — | 入力 `sprint`（任意・既定は空）と、空でなければ `--sprint` を足す受け渡し（2 つの実行 workflow で同じ配列の形）。ほかの入力は足さない（組み直しの入力は今ある `level`・`units`。下の「組み直しの入力」） |
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
- **workflow の入力 `sprint`**（決定 9 の移行の順）: `ndf-release-reusable.yml` の `workflow_call.inputs` に `sprint`（`type: string`・`required: false`・`default: ""`）を足す。`release-cd.yml` の `workflow_dispatch.inputs` に `sprint`（任意・既定は空）を足す。2 つの実行 workflow の組み立てのステップは同じ形で渡す: ステップの `env` に `SPRINT: ${{ inputs.sprint }}` を置き、`run` の中で今の `--units` と同じく配列を組む（`extra=()`、`if [ -n "$SPRINT" ]; then extra+=(--sprint "$SPRINT"); fi`）。`release-steps assemble` の呼び出しは `"${extra[@]}"` を展開する（空なら引数を足さない）。雛形 `ndf-release.yml` は `workflow_dispatch.inputs.sprint` と `with: sprint: ${{ inputs.sprint || '' }}` を足す。この 3 つと CLI の `--sprint` を同じ版で出す
- **組み直しの入力**（範囲・版・単位を変える指摘を組み立てに当てる入力）: workflow に新しい入力を足さず、今ある入力だけを使う。戦略ごとに次のとおりで、`rebuild_inputs(strategy)`（下）が返す

  | 戦略（組み立てる戦略だけ） | 変えられる入力 | dispatch の入力 → CLI |
  | --- | --- | --- |
  | `trunk` の `release_pr`・版を持つ `merge_base_branch` | `level` | `-f level=` → `inputs.level` → `release-steps assemble --level`（`release-cd.yml` は `LEVEL`、再利用 workflow は `LEVEL` で渡す。今のまま） |
  | `release_branch` | `level`・`units` | 加えて `-f units=` → `inputs.units` → `--units`（再利用 workflow の今の配列の形。雛形は宣言済み） |

  `pr` は組み立ての入力でない（`release-cd.yml` と雛形の `pr` は merge の PR の番号、`release-steps assemble --pr` は組み立てない `production_pr` の本番の PR）。`release-cd.yml` は `trunk` なので `units` を宣言しない（足さない）。入力に無い変更（含む PR の 1 つだけを外す・版の番号を直に指定する・`trunk` で単位を選ぶなど）は組み直しで当てられない。`prepared` はこの組を dispatch の workflow の `on.workflow_dispatch.inputs` で宣言されたものに絞って `outputs.inputs` に返す（宣言されていない入力を `-f` で渡して dispatch を 422 で落とさない）
- `skills/release/SKILL.md` の「公開する」の手順 1（組み立て）と CHANGELOG の本文を書く手順を、`release-prepare` の worker が打つ手順として分ける。worker の手順:
  1. `NDF_MACHINE_ID` が無ければ `precondition` で止まる
  2. `ndf release prepared --sprint "$NDF_MACHINE_ID"` を打つ。`precondition`（workflow が入力 `sprint` を組み立てまで渡さない）ならその理由のまま止まる
  3. `outputs.pr` があり `outputs.broken` が空なら、組み立てを打たない（PR を作り直さない。使い直す）。準備が済んだかはこの分岐で決めず、手順 6 で決める
  4. 指摘が範囲・版・単位を変えるなら、まずその指摘を `outputs.inputs`（上の「組み直しの入力」を workflow の宣言で絞ったもの）の値に当てられるかを決める。当てられなければ、`outputs.pr` の有無によらず、組み立ても復旧の案内もせずに `precondition` で止まる（理由「組み直しの入力で当てられない指摘: <指摘>（変えられる入力: <outputs.inputs>）」。PR を消して組み直しても当たらないので、PR とブランチを消させない）。次に、`outputs.pr` があり、`outputs.broken` がある（範囲が壊れた自分の PR）か、当てられる範囲・版・単位の指摘があるなら、組み立てを打たずに `precondition` で止まる。理由に `outputs.broken`（または指摘と当てる入力の値）と `outputs.recovery`（`recovery`（下）が組む復旧の順。準備とマージの前の検査で同じ文）を載せる（`assemble` は同じ名前のリリースブランチが残っていると `AssembleError` で止まるので、PR を閉じるだけでは組み直せない。worker は PR を閉じずブランチも消さない。自分で閉じて組み直す手順は `#4157`）。`outputs.pr` が無い（開いた自分の PR が無い。人が片付けて流し直した後を含む）なら止まらず、当てた入力の値（`-f level=`、`release_branch` では `-f units=` も）を持って手順 5 へ進む
  5. 開いた自分の PR が無いときだけ、`step=assemble` に `-f sprint="$NDF_MACHINE_ID"`（と手順 4 で当てた入力）を渡して組み立てる（組み立てた直後は仕上げの印が無いので、`prepared` を打ち直さずに手順 6 へ進む）
  6. 手順 5 で組み立てたか `outputs.changelog` が偽なら（組み立てた直後と、PR の作成の後・CHANGELOG の push の前に中断した再実行の両方）、リリースブランチを取り出し、CHANGELOG の本文を書き（今の「公開する」の手順のまま）、行 `Ndf-Changelog: <NDF_MACHINE_ID>` を持つコミットにして push する（本文に差分が無ければ空のコミットにする。件名に PR の番号 `(#n)` を書かない。`assembled_message` が組み立ての後に入った PR と読まないため）。真なら書き直さない
  7. 変更要求の指摘（`NDF_STAGE_NOTES`）のうち手順 4 で入力に当てたもの以外があれば、直しのコミットか PR の本文の編集で当てる（手順 6 の後。CHANGELOG の仕上げで指摘の直しを上書きしない）。最後に push したコミットの SHA を控える
  8. 分岐は「手順 5〜7 でコミットを push したか」の 1 つだけで決める（PR の本文の編集は push に数えない。本文は `prepared` の判定に効かない）。push していない（手順 2 の `outputs.changelog` が真・`outputs.broken` が空で、指摘が無いか本文の編集だけ）なら、`prepared` を打ち直さず手順 2 の結果で `ok` を返す。push したときだけ、手順 2 と同じスプリント ID で `ndf release prepared --sprint "$NDF_MACHINE_ID" --head <手順 7 で控えた SHA>` を打ち直す（手順 5 で組み立てたときは手順 6 で必ず push するのでこちら）。PR の番号が分かっていれば（手順 2 の `outputs.pr`）`--pr <番号>` も渡す。`--head` があれば workflow の検査を省き（手順 2 で済んでいる）、`--pr` があれば開いた PR の探索を省く（どちらでも PR の状態・宛先・head のリポジトリと ref は `hub.pull` の応答で確かめる）。`outputs.changelog` が真で `outputs.broken` が空のときだけ `ok` を返す。違えば `precondition` で止まる（仕上げの push が届いていない）
- `release` の worker は手順 2〜4（提示・回答・マージ・公開）だけを打つ。提示の承認資料は、組み立てる戦略ならリリースの PR の本文、組み立てない戦略なら今どおり `ndf release materials --pr <mvv_gate.pr>` で集めたもの（決定 8）。リリースの PR の本文は判定の後に編集しない（承認資料の「MVV 判定」の欄は今回の判定の出力が出所。決定 8）

### 関数の仕様（組み立て）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `delegates_release` | `progress/lane/__init__.py` | 新設 | `delegates_release(conn: sa.Connection, machine_id: str, env: Mapping[str, str]) -> bool` | スプリントがリリースをオーガナイザーに委ねるか（上の定義）。`request_release` の出し先の分岐もこの関数を通す |
| `release_inputs` | `progress/lane/__init__.py` | 新設 | `release_inputs(conn: sa.Connection, machine_id: str, env: Mapping[str, str]) -> dict[str, Any]` | 判定の入力に足す `{"release_delegated": delegates_release(…), "machine_id": machine_id}`。`invoke`（`NDF_STAGE_INPUT` を組むとき、`release-mvv` のステップだけ）と `records/cli.py` の `--machine`（#3959 の `_mvv_inputs` の後）の両方がこれを呼ぶ |
| `assembles` | `release/assemble.py` | 新設 | `assembles(strategy: Strategy) -> bool` | ブランチ戦略が版上げの PR を組み立てるか（範囲の取り方が `pr` でなく、版を持つ）。`assemble` の `NoReleasePr` の条件をこの関数で決める（判定・ステップの条件・組み立てで条件を 1 つにする） |
| `assemble` | `release/assemble.py` | 変更 | 引数 `sprint: Optional[str] = None` を足す | `sprint` があれば組み立てのコミットに行 `Ndf-Sprint: <sprint>` を書く。ほかは今のまま（同じ名前のリリースブランチが既にあれば今どおり `AssembleError`） |
| `prepared` | `release/assemble.py` | 新設 | `prepared(hub: GitHub, target: Target, strategy: Strategy, sprint: str, workflow: str, *, pr: Optional[int] = None, head: Optional[str] = None) -> Preparation`（`Preparation(inputs: Optional[tuple[str, ...]], pull: Optional[Prepared])`、`Prepared(repo: str, pr: int, head: str, branch: str, broken: Optional[str], changelog: bool, recovery: str)`） | `head` が None なら先に `workflow`（設定の `cd.workflow`）のファイルを既定ブランチから読み（contents API 1 回。`uses` が再利用 workflow を指すジョブがあれば、その固定参照のファイルをもう 1 回）、`workflow_passes_sprint`（下）が理由を返せば `WorkflowOutdated`（`precondition`。理由に欠けた箇所・雛形の該当行・`NDF_TAG` の更新を名指す）。通れば `inputs` は `rebuild_inputs(strategy)` のうち読んだ workflow の `on.workflow_dispatch.inputs` が宣言するもの。`head` があれば workflow を読まず（手順 2 で検査済み）、`inputs` は None。次に PR を決める: `pr` があればそれ（PR の一覧を読まない）、無ければ本番宛ての開いたリリースの PR（`open_release_numbers`）が 1 つならそれ。その PR を `hub.pull` で 1 回読み、同じ応答に `release_pull_off`（下）を当てる。理由が返れば `precondition`（`pr` を渡した経路では「#<pr> が開いた本番宛てのリリースの PR でなくなった: <理由>」。一覧を経由した経路では一覧の後に閉じられたか宛先・head が変わったので、同じ文で止まる。どちらも `ReleaseOpen` にも None にもしない）。`head` があり `pull.head_sha` と違えば `precondition`「push した head が PR に届いていない」。本番の先端を 1 回読んで SHA に固定し（`production_sha`）、`hub.compare(production_sha, pull.head_sha)` の 1 回の応答のコミットで `owned`（下）・`broken`・`changelog`（`changelog_done`）を決める（同じ head の同じ応答で持ち主・範囲・仕上げを決める）。`broken` は `assembled_message(commits, pull.head_sha, record.upstream(hub, production_sha))` の `ValueError` の文（無ければ None）。`upstream` の `GitHubError`（祖先を確かめる通信の失敗）は `broken` にせず、そのまま `transient` で止まる（範囲の破損と区別し、正常な PR に削除を案内しない）。持ち主ならリポジトリ（`target.repo`）・番号・head・head のブランチ名（`pull.head_ref`）・`broken`・`changelog`・`recovery(target.repo, number, pull.head_ref, pull.head_sha, server)`（`server` は `hub` の GitHub のホスト）。印が違うか無い・2 つ以上なら `ReleaseOpen`（今の組み立てと同じ止まり方）。開いた PR が無ければ `pull` は None（`inputs` は返す）。CLI は `inputs` を `outputs.inputs`、`pull` の欄を `outputs.repo`・`outputs.pr`・`outputs.head`・`outputs.branch`・`outputs.broken`・`outputs.changelog`・`outputs.recovery` に出す |
| `release_pull_off` | `release/assemble.py` | 新設 | `release_pull_off(pull: Pull, target: Target, formats: NameFormats) -> Optional[str]` | `hub.pull` の 1 回の応答が、開いた本番宛ての対象のリリースの PR かを確かめる（純粋）。`pull.state` が `open` でない・`pull.base_ref` が `target.production` でない・`pull.head_repo` が `target.repo` でない（フォークや消えたリポジトリの PR）・`pull.head_ref` がリリースブランチの書式に合わない、のどれかなら理由の文、すべて満たせば None。`open_release_numbers` の一覧の条件と同じ規則を、PR を読んだ時点の応答に当て直す |
| `rebuild_inputs` | `release/assemble.py` | 新設 | `rebuild_inputs(strategy: Strategy) -> tuple[str, ...]` | 組み直しで変えられる dispatch の入力（上の「組み直しの入力」の表。純粋）。`release_branch` は `("level", "units")`、ほかの組み立てる戦略は `("level",)`。`pr` を含めない |
| `recovery` | `release/assemble.py` | 新設 | `recovery(repo: str, number: int, branch: str, head: str, server: str) -> str` | 範囲の壊れた PR を人が片付ける順の文（純粋）。`prepared` の `Prepared.recovery` と `Lander.land` の止まる理由が同じ関数を使う（`server` は `hub` の GitHub のホスト）。順: (1) 残したい手直しを退避する（`git fetch <server>/<repo>.git <head>` で取り出せる） → (2) PR を閉じる前に `gh pr view <number> --repo <repo> --json state,baseRefName,headRepository,headRepositoryOwner,headRefName,headRefOid` で、開いている・head のリポジトリが `<repo>`・ref が `<branch>`・SHA が `<head>` かを確かめる。どれかが違えば閉じずに止まり、流し直して今の状態で判定させる（検査の後の push を含む PR を閉じない） → (3) `gh pr close <number> --repo <repo>` → (4) `git push --force-with-lease=refs/heads/<branch>:<head> <server>/<repo>.git --delete <branch>`（対象のリポジトリの URL を直に名指す。作業場所のリモート名 `origin` に頼らない。lease が外れれば閉じた後の push があるので消さずに確かめ直す） → (5) 流し直す |
| `workflow_passes_sprint` | `release/assemble.py` | 新設 | `workflow_passes_sprint(text: str, called: Callable[[str], Optional[str]]) -> Optional[str]` | dispatch の workflow が入力 `sprint` を組み立てまで渡し、組み立てを打つ CLI が `--sprint` を持つ版か（`called` を除き純粋）。YAML は NDF の宣言済みの依存 `ruamel-yaml` の `YAML(typ="safe", pure=True).load` で読む（`manifests.front_matter` と同じ。PyYAML を使わない。ruamel は YAML 1.2 の規則で `on:` を文字列キー `"on"` として読む）。次のどれかなら欠けた箇所の文を返し、すべて満たせば None: `on.workflow_dispatch.inputs` に `sprint` が無い・組み立てを打つジョブが 1 つも無い・組み立てを打つジョブのどれかが `inputs.sprint` を渡さない。組み立てを打つジョブは 2 種類。(a) `uses` が `ndf-release-reusable.yml` を指すジョブ: `with.sprint` の値が `inputs.sprint` を含み、`called(uses の値)`（`prepared` が `uses` の `<所有者>/<リポジトリ>/<パス>@<参照>` をその参照で contents API から読む。`./` で始まる同じリポジトリのパスは既定ブランチから読む。読めなければ None）が返す呼ばれる側の本文が `on.workflow_call.inputs` に `sprint` を宣言してその中で (b) の規則を満たし、かつ実行する CLI の参照 `with.ndf_ref` が検査した参照と一致すること。`ndf_ref` は式でない文字列で、`uses` が `@<参照>` を持てばそれと等しいこと（再利用 workflow は `inputs.ndf_ref` で CLI を checkout するので、`uses` だけを上げて `ndf_ref` を旧版に残すと、dispatch の後に旧版の CLI が `--sprint` を拒む）。`./` の `uses` は参照を持たないので、`called("devbasex/ai-plugins/.github/workflows/ndf-release-reusable.yml@<ndf_ref>")` が `workflow_call.inputs.sprint` を宣言すること（3 つの workflow と CLI は同じ版で出すので、その版の CLI が `--sprint` を持つ）。`uses` のタグが旧版のまま・呼ばれる側が `sprint` を宣言しない・読めない・`ndf_ref` が式か `uses` の参照と違う、はどれも理由。(b) ステップの `run` が `release-steps assemble` を含むジョブ: そのステップかジョブの `env` で値が `inputs.sprint` を含む変数を `X` として、`release-steps assemble` の呼び出しの引数に次のどちらかがあること。(b1) `--sprint` の直後の実引数が `inputs.sprint` を含む式か `"$X"`・`"${X}"`。(b2) 同じ `run` の中で配列 `A` に `--sprint` とその直後の `"$X"`・`"${X}"` を足す代入（`A=(…)`・`A+=(…)`。`if` の中でもよい）があり、呼び出しの引数に `"${A[@]}"` があること。配布する 2 つの実行 workflow は (b2) の形にそろえる（上の「workflow の入力 `sprint`」）。`--sprint` があるだけ・`inputs.sprint` を持つ変数が `--sprint` と別の引数に渡る・配列を組むが呼び出しで展開しない、はどれも理由 |
| `upstream` | `release/record.py` | 新設 | `upstream(hub: GitHub, production_sha: str) -> Callable[[str], bool]` | 固定した本番の SHA に対する祖先の判定を組む（`prepared`・判定の `assembled_included`・マージの前の検査の `assembled_included` が共有する）。返す関数は「`sha == production_sha` なら通信せずに真、違えば `hub.merge_base(sha, production_sha) == sha`」で、同じ SHA は覚えて打ち直さない。通信の失敗は `GitHubError` のまま投げる（`ValueError` にも `RecordError` にもしない。呼び手は範囲の破損と区別して `transient` で扱う） |
| `changelog_done` | `release/record_decide.py` | 新設 | `changelog_done(commits: Sequence[Mapping[str, Any]], head: str, sprint: str) -> bool` | `head` から first-parent で辿り、組み立てのコミットより上に行 `Ndf-Changelog: <sprint>` を持つコミットがあるか（純粋）。組み立てのコミットが無ければ偽 |
| `sprint_of` | `release/record_decide.py` | 新設 | `sprint_of(message: str) -> Optional[str]` | 組み立てのコミットのメッセージの行 `Ndf-Sprint:` の値（無いか 2 つ以上なら None） |

### 異常系の処理

| 失敗 | 振る舞い |
| --- | --- |
| 組み立てが止まる（`ReleaseOpen`・`AssembleError`） | `release-prepare` が `stopped`。工程は判定へ進まない（今の `release` の止まり方と同じ） |
| 組み立てが済まないまま `release-prepare` が `ok` を返す | 判定が開いたリリースの PR を見つけられず、判定のステップが `stopped`（下の `release_target`）。承認ゲートへ進まない |
| 変更要求の後・準備の成功の後の中断から流し直す | `prepared` が同じスプリントの PR を返し、組み立てを打たない |
| PR の作成の後・CHANGELOG の本文の push の前に中断した | 再実行で `prepared` が同じスプリントの PR を `changelog` 偽で返す。組み立てを打たずに手順 6 で CHANGELOG を仕上げてから `ok` を返す（使い直しの分岐でも仕上げを飛ばさない） |
| 利用先の `ndf-release.yml` が入力 `sprint` を持たないか、宣言だけで `with: sprint` を渡さないか、宣言と引き渡しを足して `uses` のタグが旧版のまま（呼ばれる側が `sprint` を宣言しない）か、`uses` だけを上げて `with.ndf_ref` が旧版のまま（NDF だけを上げた・移行の途中） | `prepared` が `WorkflowOutdated` で `precondition`。dispatch も組み立ても打たずに `release-prepare` が `stopped`、理由に欠けた箇所と workflow の更新の手順 |
| `prepared` の祖先の確かめ（`upstream` の `merge_base`）が GitHub に届かない | `broken` にせず `transient` で止まる（復旧の削除を案内しない） |
| 自分の PR の範囲が壊れている（組み立ての後に別のマージ） | `prepared` が `broken` 付きで返し、`release-prepare` が組み立てを打たずに `stopped`（`precondition`）。理由に壊れた理由と `recovery` の順（手直しの退避 → 閉じる前に PR の状態・head のリポジトリ・ref・SHA を確かめ直す → PR を閉じる → 対象のリポジトリの URL に lease 付きでブランチを消す → 流し直す）。人がその順で片付けて流し直すと組み立てから進む（自動の組み直しは `#4157`） |
| 範囲・版・単位を変える指摘が組み直しの入力（`outputs.inputs`）で当てられない | 開いた自分の PR の有無によらず、組み立ても復旧の案内もせずに `stopped`（`precondition`）。理由に指摘と変えられる入力。PR とブランチは残す |
| `prepared` が一覧を読んだ後か `--pr` で渡された PR が、閉じられた・宛先が本番でなくなった・head のリポジトリか ref が変わった | `hub.pull` の応答の `release_pull_off` が理由を返し `precondition`。head が同じでも `ok` にしない |
| 範囲を変える変更要求の後、人が PR とブランチを片付けて流し直した（同じ変更要求の指摘が渡る） | `prepared` の `pull` が None なので手順 4 で止まらず、指摘を組み直しの入力（`outputs.inputs`。`level`、`release_branch` では `units` も）に当てて手順 5 で組み立てる |
| push の後・PR の作成の前に止まった | 再実行で `prepared` は None、組み立てが「ブランチが既にある」（`AssembleError`）で止まる。人がブランチを消して流し直す（自動の回復は `#4157`） |

### 単体テストの観点（組み立て）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| ステップの `when` | 両方真 → worker を起動する | `production_pr`・計画のレーン・`NDF_ORGANIZER_DIR` → 起動せず `finished`（`skipped`） | 語を求められない → `precondition` |
| `prepared` | 同じ印の開いた PR → リポジトリ・その番号・head・ブランチ名・`broken` None。組み立ての上に `Ndf-Changelog` の行のコミット → `changelog` 真。組み立ての上に GitHub のブランチの更新で本番を取り込んだマージ（第 2 親が `production_sha` の祖先）→ `broken` None（`assembled_included` と同じ `upstream`） | 開いた PR が 0 → None。同じ印の PR の上に別のスプリントのマージ → 番号・ブランチ名・head と `broken` に理由（`ReleaseOpen` にしない）。組み立てのコミットだけの PR（PR の作成の後に中断）→ `changelog` 偽。`pr`・`head` を渡す → workflow と PR の一覧を読まない（スタブの呼び出しの数） | 印が違う・印が無い・2 つ → `ReleaseOpen`。workflow が渡さない → `WorkflowOutdated`（GitHub の PR の一覧を読まない）。`upstream` の `merge_base` が `GitHubError` → `broken` にせず `GitHubError`。`head` が `pull.head_sha` と違う → `precondition`。`pr` を渡し、その PR が閉じている・宛先が本番でない・head のリポジトリがフォーク・ref がリリースブランチの書式でない（head は同じ）→ `precondition`（`ok` の値を返さない）。一覧を読んだ後にスタブの `hub.pull` が閉じた PR か宛先の変わった PR を返す → 同じく `precondition` |
| `release_pull_off` | 開いた本番宛ての対象のリポジトリのリリースブランチの PR → None | — | `state: closed`・`base_ref` が本番でない・`head_repo` が別か空・`head_ref` が書式外 → それぞれ理由 |
| `rebuild_inputs` | `release_branch` → `level`・`units` | `trunk` の `release_pr`・版を持つ `merge_base_branch` → `level` だけ | どの戦略でも `pr` を含まない |
| `recovery` | 文に `gh pr view` の確かめ（開いている・head のリポジトリ・ref・SHA）が `gh pr close` より前にあり、削除が `--force-with-lease=refs/heads/<branch>:<head>` と `<server>/<repo>.git` を持つ（`origin` を使わない） | — | — |
| `workflow_passes_sprint` | 雛形 `ndf-release.yml`（`on:` のまま）と、`called` が返す今の版の `ndf-release-reusable.yml`・`release-cd.yml` を ruamel で読む → None（`on` を文字列キーで読み、宣言と引き渡しを見つける） | — | `sprint` の宣言が無い → 理由。宣言だけ足して `with: sprint` が無い雛形（移行の途中）→ 理由（`with: sprint` の欠け）。宣言と `with: sprint` を足し `uses` のタグが旧版（`called` が `workflow_call.inputs.sprint` の無い本文を返す）・`called` が None → 理由。`uses` を新版にして `with.ndf_ref` を旧版のまま残した雛形・`ndf_ref` が式 → 理由（片方だけ上げた移行）。`release-steps assemble` の `run` に `--sprint` が無い → 理由。`--sprint` の実引数が `inputs.sprint` と無関係で、別の `env` の変数だけが `inputs.sprint` を持つ → 理由。配列に `--sprint "$SPRINT"` を足すが呼び出しで `"${extra[@]}"` を展開しない → 理由。(b1) の形（`--sprint` の直後に `"$SPRINT"`）と (b2) の形（別の行で配列を組んで展開）はどちらも None |
| `assemble`（印） | `sprint` あり → 組み立てのコミットに行 `Ndf-Sprint: <sprint>` | `sprint` が無い → 行を書かない（今のまま） | — |
| worker の止まり方（`release-prepare` の通しのテスト） | 範囲の壊れていない仕上げ済みの自分の PR で指摘なし → 組み立ても push もせず、`prepared` を 1 回だけ打って `ok`。同じ PR で指摘が本文の編集だけ → 本文を編集し、`prepared` を打ち直さず（1 回だけ）`ok` | 開いた自分の PR があって `level` で当てられる版の指摘 → `precondition` で止まり、理由に当てる入力の値と `recovery`。開いた PR が無くて版を変える指摘 → 止まらず `-f level` に当てて組み立てる（`release_branch` で単位を変える指摘は `-f units`）。`trunk` で含む PR の 1 つだけを外す指摘（入力に無い）→ PR の有無によらず組み立ても復旧の案内もせず `precondition`。どの経路でも `-f pr=` を組み立てに渡さない。`changelog` 偽の自分の PR（PR の作成の後に中断）→ 組み立てを打たずに CHANGELOG を仕上げ、`Ndf-Changelog` の行のコミットを push し、`prepared --sprint <同じ ID> --pr <n> --head <push した SHA>` で確かめてから `ok` | `prepared` が `broken` を返す → 組み立てを打たず `precondition`、理由に `outputs.recovery`（閉じる前の確かめ・lease 付きの削除） |
| workflow | `release-cd.yml`・雛形が `sprint` を宣言する。実ファイルの `release-cd.yml` と `ndf-release-reusable.yml` の組み立てのステップの `run` を取り出し、`NDF` を引数を書き出すスタブにして bash で流す: `SPRINT=m-1` → 引数に `--sprint m-1` がある。実ファイルを `workflow_passes_sprint` に通すと None | 同じ `run` を `SPRINT` が空で流す → 引数に `--sprint` が無い（2 つの workflow で同じ） | — |
| 変更要求（`decide`） | リリース承認ゲートで変更要求 → `release-prepare` が流れ直し、`prepared` で使い直して `ReleaseOpen` で止まらない | 準備の後に中断 → 同じ | — |
| `invoke` | 工程から組むプランの子プロセスの環境に `NDF_MACHINE_ID` が入る | — | — |

## 判定する PR と含む PR

満たす決定: 決定 2・3・4・6・7、受け入れ条件 1・2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `compare` | `adapters/github.py` | 新設 | `compare(base: str, head: str, first: Optional[dict] = None) -> Compared`（`Compared(merge_base: str, commits: list[dict])`） | `compare_commits` と同じページ送り（`per_page`・`page`）で、最初のページの `merge_base_commit.sha` を一緒に返す。`base`・`head` は SHA を渡す（ブランチ名を渡さない）。`first` に同じ `base...head` の 1 ページ目の応答（同じ `per_page`）が渡れば、それを使い 2 ページ目から読む |
| `merge_base` | `adapters/github.py` | 新設 | `merge_base(base: str, head: str) -> str` | `compare` の `base...head` を `per_page=1`・`page=1` で 1 回だけ読み、`merge_base_commit.sha` を返す（コミットのページを送らない）。祖先の確かめ（`assembled_included` の `upstream`）だけが使う |
| `open_release_numbers` | `release/assemble.py` | 新設 | `open_release_numbers(hub: GitHub, production: str, base: str, formats: names_decide.NameFormats) -> list[int]` | 今の `open_releases` の本体（本番宛ての開いた PR を 1 ページ読み、head がリリースブランチの書式に合うか `base`（`base != production` のとき）の番号を小さい順に返す。読み切れなければ `AssembleError`）。`open_releases(hub, target, formats)` は `target.production`・`target.base` を渡してこれを呼ぶ |
| `release_target` | `records/mvv_gate.py` | 新設 | `release_target(hub: GitHub, probe: Strategy, inputs: Mapping[str, Any]) -> tuple[int, bool]` | リリースで判定する PR の番号と、持ち主の確かめが要るか（自分で組み立てる戦略で真）。`probe` は作業場所のブランチ戦略（探索用。`branch_strategy.load(root)`）。`inputs` に `release_delegated`（真偽値）と `machine_id`（文字列）が無ければ `TargetError`（両方の入口が `lane.release_inputs` で必ず足すので、欠けは入口の誤り）。`release_delegated` が真か `assemble.assembles(probe)` が偽なら文脈の `pr`。それ以外は `open_release_numbers` が 1 つならそれ（印の照合はこの関数でせず、`_judged` が判定する head を固定した後に、範囲と同じ compare の応答で `assemble.owned` を当てる。下の手順 4）。0・2 つ以上・文脈の `pr` が無い（委ねた・組み立てない戦略で）なら `TargetError`（「判定するリリースの PR を 1 つに決められない: <理由と開いた PR の番号>」） |
| `pull_commits` | `release/record.py` | 新設（`_release_commits` を置き換える） | `pull_commits(hub: GitHub, pull: Pull, base_sha: Optional[str], first: Optional[dict] = None) -> tuple[list[dict], str]` | PR のコミットと分岐点（compare の `merge_base_commit.sha`）。開いていれば `hub.compare(base_sha, pull.head_sha)` のコミットと merge base。マージ済み（`pull.merge_sha` あり）は今の `_release_commits` と同じく、マージのコミットの第 1 親から head まで（`hub.compare(第 1 親, pull.head_sha)`）。複数コミットの rebase merge では第 1 親が PR の中のコミットになり範囲が狭くなるが、マージ済みの PR を判定するのは組み立てない戦略の文脈の `pr` だけで、`scope` を付けないので省略には効かない（保留の表示の範囲だけに効く）。開いていて `base_sha` が None・マージのコミットが読めないなら `RecordError`（判定は `scope` を付けない） |
| `included_line` | `release/record_decide.py` | 新設 | `included_line(message: str) -> Union[Missing, Broken, list[int]]` | 「含む PR」の行の 3 つの状態: 行が無い（`MISSING`）・形が違う（`BROKEN`。`#<番号>` の並びでも `none` でもない、行が 2 つ以上）・番号（`none` は空の配列）。`included_from_message` は `included_line` が配列のときだけその値、ほかは None（今の呼び手のため） |
| `assembled_commits` | `release/record_decide.py` | 新設 | `assembled_commits(commits: Sequence[Mapping[str, Any]], head: str) -> list[Mapping[str, Any]]` | `head` から first-parent で辿った PR のコミット（`_first_parent_messages`）のうち、件名が `included_decide` の `RELEASE_TITLE` の形のもの（組み立てのコミット）を新しい順に返す。範囲も行の形も検証しない（持ち主の確かめ `prepared`・`owned` が使う） |
| `assembled_message` | `release/record_decide.py` | 新設（`_approved_message` を消す） | `assembled_message(commits: Sequence[Mapping[str, Any]], head: str, upstream: Callable[[str], bool]) -> Optional[str]` | `assembled_commits` が 1 つで、その `included_line` が配列ならそのメッセージ。`upstream(sha)` は SHA が本番の先端の祖先か（呼び手が compare の merge base で決める）。次は `ValueError`（どのコミットの件名と行かを文に入れる）: 組み立てのコミットが 2 つ以上・組み立てのコミットの行が `MISSING` か `BROKEN`・組み立てのコミットが無く件名の違うコミットだけが行を持つ・組み立てのコミットより上の first-parent のコミットに、第 2 親以降のどれかで `upstream` が偽のマージか、件名から PR の番号（`included_decide.number_of`）が出て行に無いものがある（組み立ての後にほかの変更が入った）。第 2 親以降がすべて `upstream` で真のマージ（GitHub のブランチの更新で本番を取り込んだ）は許し、その件名の番号も読まない。組み立てのコミットも行を持つコミットも無ければ None。件名の違う直しのコミットの行は読まない |
| `approved_included`・`approved_units` | `release/record_decide.py` | 変更（失敗の扱いと引数） | 引数 `upstream: Callable[[str], bool]` を足す | `assembled_message` を通す。`ValueError` は呼び出し元へ返す |
| `owned` | `release/assemble.py` | 新設 | `owned(commits: Sequence[Mapping[str, Any]], head: str, sprint: str) -> bool` | 呼び手が固定した head の compare の応答のコミットの `assembled_commits` が 1 つ以上で、すべての `sprint_of` が `sprint` か（純粋。GitHub を読まない）。`prepared`・`_judged` が、範囲の検証と同じ応答に当てる |
| `assembled_included` | `release/record.py` | 新設 | `assembled_included(hub: GitHub, commits: Sequence[dict], pull: Pull, production: str, production_sha: str, formats: NameFormats, *, require: bool) -> list[int]` | `upstream(hub, production_sha)`（上。`prepared` と同じ関数）を `assembled_message` に渡す（親が 2 つのコミットの第 2 親以降を確かめるときだけ呼ばれ、同じ SHA は 1 回の実行の中で覚えて打ち直さない。全ページを読む `hub.compare` を祖先の確かめに使わない。本番が先に進んでも、使わないコミットのページを読まない。祖先の確かめの `GitHubError` は `RecordError` にせずそのまま投げる）。`production_sha` は判定では `base_sha`、リリース記録ではマージのコミットの第 1 親、マージの前の検査では本番の先端。ほかは今の `_included` の本体（組み立てのコミットがあれば行の番号、無ければ first-parent の件名から `included_decide.numbers`）。`require` が真（判定の組み立てる戦略）なら、組み立てのコミットが無い（`assembled_message` が None）とき件名へ戻さず `RecordError`。`ValueError` は `RecordError`（「組み立てのコミットの『含む PR』を 1 つに決められない: <原因>」）にする |
| `_included` | `release/record.py` | 変更 | シグネチャは同じ | `assembled_included(…, require=False)` を呼ぶ（リリース記録と判定で規則を 1 つにする。リリース記録は組み立てない戦略の件名の扱いを今のまま持つ） |
| `Lander.land` | `release/land.py` | 変更 | シグネチャは同じ | 記録を書く設定（`route.release_pr and route.bump`。`cmd_merge` の `records` と同じ条件）のとき、`_gate` の後・マージキューへ入れる前に、`_gate` が固定した本番の先端 `prod.tip` と `pull.head_sha` のコミットへ `assembled_included(…, require=assemble.assembles(strategy))` を当て、`RecordError` なら `precondition` で止まる（理由に「リリース記録を書けない形」と、準備と同じ `recovery(target.repo, pull.number, pull.head_ref, pull.head_sha, server)` の順（閉じる前に PR の状態・head のリポジトリ・ref・SHA を確かめ直し、対象のリポジトリの URL に lease 付きで消す。`#4157` まで人が片付ける））。祖先の確かめの `GitHubError` は `transient` で止まり、削除を案内しない。人の承認の有無によらない。組み立てる設定では組み立てのコミットが無い PR（手で開いた版上げの PR）も止める（件名へ戻さない。普及の前なので互換の経路は置かない）。compare は `_bump_facts` が読んだ同じ `prod.tip...head` の 1 ページ目の応答（`_bump_facts` を `per_page`・`page=1` 付きで読むようにし、`Gate.compared` に残す）を `hub.compare(…, first=)` に渡し、2 ページ目からだけを読む。`landing.method` が `direct`（`_bump_facts` を読まない）なら本番の先端を 1 回読んで全ページを読む |
| `mvv_counts` | `release/trail.py` | 新設 | `mvv_counts(strategy: Strategy) -> Optional[str]` | MVV の回答（`--by mvv` の証跡）を公開側が承認に数えない理由。`approval.via` が `review` なら理由（「approval.via: review は GitHub のレビューを求める」）、`merge.admin` が真なら理由（「管理者の迂回は利用者の証跡だけを数える」）、数えるなら None。呼び手は `is None` で読む（真偽値として読まない）。`trail_writer`・`_counts` と同じ設定を読む |
| `Span` | `records/mvv_gate.py` | 新設 | `Span(commits: Optional[list[dict]], fork: Optional[str], failure: Optional[str])` | 手順 4 で読んだ PR のコミットと merge base。読めなければ `commits`・`fork` が None で `failure` に理由（`GitHubError`・`RecordError` の文）。手順 4 が 1 回だけ作り、持ち主の確かめと `review_prs` の両方へ同じ値を渡す |
| `review_prs` | `records/mvv_gate.py` | 変更（#3959 の新設を変える） | `review_prs(gate: str, number: int, inputs: Mapping[str, Any], hub: GitHub, pull: Pull, strategy: Strategy, base_sha: str, span: Optional[Span]) -> tuple[tuple[int, ...], tuple[str, ...]]` | 保留を読む PR と、範囲を確定できない理由の並び（確定できれば空）。`design` は `((number,), ())`（`span` は None）。`release` は下の手順の 6。`span` からコミットと merge base を読み、`pull_commits`・`hub.compare` を呼ばない（`hub` は祖先の確かめの `merge_base` だけに使う）。`span.failure` があればそれを理由にする |
| `_judged` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | 下の手順 |
| `gate_outcome` | `records/mvv_material_decide.py` | 変更 | 引数 `pr: Optional[int] = None`・`scope: Optional[Mapping[str, Any]] = None` を足す | `pr`・`scope` が None でなければ出力に入れる |
| `_recorded` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | コメントを書けたら、その ID を出力の `note` に入れる。書けなければ #3959 の `noted: false` のまま、`note` を入れない |
| `note` | `progress/gate/checks.py` | 変更 | シグネチャは同じ（`outcome` から読む） | `scope` があれば「保留を読んだ PR: #a #b …」、無ければ範囲を確定できない理由の行を足す。承認の依頼の「MVV 判定」の欄はこのコメントでなく今回の出力 `mvv_gate` を写す（#3959 の「承認資料の出所」） |

### 処理の手順（`_judged` の `release`）

#3959 の `_judged` の手順のうち、判定する PR を決める手順・`base_sha` の取り方・保留を読む手順を変える。`base_sha` は #3959 と同じく「判定する PR の宛先を fetch した先端」で、判定する PR を文脈の `pr` から `release_target` の PR へ差し替える。

1. `probe = branch_strategy.load(root)`（作業場所の今の宣言。判定する PR を探すためだけに使う）
2. `number, check_owner = release_target(hub, probe, inputs)`（`design` は今どおり `pr_number`）。`TargetError`・`AssembleError`・`GitHubError` は判定のステップを止める（下の異常系）
3. `pull = hub.pull(number)`（#3959 が判定する head を先に読む手順と同じ 1 回）。以降の持ち主の確かめ・範囲・保留はすべてこの `pull.head_sha` を使い、PR を読み直さない
4. `base_sha` = `pull.base` を fetch した先端（#3959 の `Snapshot.commit` の取り方。`release_branch`・`trunk` の `release_pr` では本番ブランチ、組み立てない戦略では文脈の `pr` の宛先）。MVV と `strategy = branch_strategy.load_at_ref(root, base_sha)` をこの SHA で読む。範囲を確定できない理由の並び `unsettled: list[str]` を空で始め、`strategy` の本番ブランチ・ベースブランチ・書式が `probe` と違う・`assemble.assembles(strategy) != assemble.assembles(probe)`（探索した時と固定した SHA で組み立てるかが違う）・組み立てる戦略で `pull.base` が本番ブランチでない、のそれぞれで理由を足す。続けて `commits, fork = pull_commits(hub, pull, base_sha)` を 1 回だけ読み（`hub.compare(base_sha, pull.head_sha)`。固定した SHA どうしの 1 つのスナップショット）、`span = Span(commits, fork, None)` にする。`GitHubError`・`RecordError` なら `span = Span(None, None, <理由>)`。`check_owner` が真なら、この `span.commits` に `assemble.owned(span.commits, pull.head_sha, inputs["machine_id"])` を当て、偽なら `TargetError` で判定のステップを止める（手順 2 の後に PR が別の組み立てに差し替わっても、読んだ head の組み立てのコミットで持ち主を決める）。`span.failure` があるとき、`check_owner` が真なら判定のステップを止め（持ち主を決められない）、偽なら `span` をそのまま手順 6 へ渡し、手順 6 が `scope` を付けない
5. 差分・機械の検査・LLM の判定は #3959 のまま、`number` の PR に当てる。判定の記録のコメントも `number` の PR へ書く
6. `prs, found = review_prs(gate, number, inputs, hub, pull, strategy, base_sha, span)` の後、`unsettled.extend(found)`（手順 4 の理由を上書きしない）:
   - 引数の `span`（手順 4 の 1 回の compare の応答）を使う（`pull_commits`・`hub.compare` を打ち直さない）。`span.failure` があれば、番号は判定した PR と文脈の `pr` だけにし、理由にその文を足す。開いた PR で `span.fork != base_sha` なら理由「リリースの PR がベースの先端（`base_sha`）を含まない」
   - 委ねたスプリント（`inputs["release_delegated"]`）: 理由「リリースをオーガナイザーに委ねたので含む PR を確定できない」。番号は件名から（`assembled_included(…, require=False)`）で、保留の表示に使う
   - 組み立てる戦略: `assembled_included(hub, span.commits, pull, strategy の本番ブランチ, base_sha, formats, require=True)` の番号
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
| `pull_commits` の `GitHubError`・`RecordError`（手順 4 の compare の失敗）で `check_owner` が真 | 持ち主を確かめられないので判定のステップが `stopped`（`GitHubError` の不達と 5xx は `transient`、ほかは `precondition`）。出力の `mvv_gate` を書かず、コメントを書かず、承認ゲートへ進まない |
| `pull_commits` の `GitHubError`・`RecordError`（手順 4 が `Span.failure` にして `review_prs` へ渡す）で `check_owner` が偽、`assembled_included` の `RecordError`・祖先の確かめの `GitHubError` | 判定の後の `may_skip` の前に、`why` に理由を入れて `skip` 偽。判定の記録は判定した PR のコメントへ書く。`pr` は判定した PR、`scope` なし |
| 記録（`mvv-validation.jsonl`）を読めない | #3959 のまま（保留を「読めない」の 1 件にする）。範囲は確定しているので `scope` は書く（保留があるので省かれない） |
| 判定の記録のコメントを書けない（`noted: false`） | `note` を出力に入れない。`scope` は残す（組み立てる戦略は資料が PR の本文にある。組み立てない戦略はもともと `scope` を持たない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `included_line` | `#1 #2` → `[1, 2]` | `none` → `[]`・行が無い → `MISSING` | `#x`・空・行が 2 つ → `BROKEN` |
| `assembled_commits` | 組み立ての件名のコミット 1 つ → それ | 上に別のスプリントのマージがある → 組み立てのコミットを返す（範囲を検証しない） | — |
| `assembled_message` | 組み立ての件名と形どおりの行を持つコミット 1 つ → そのメッセージ | 行の無い直しのコミット・`Ndf-Included-Pulls: none` を持つ直しのコミットが上に積まれている → どちらも組み立てのコミットのメッセージ。組み立てのコミットも行も無い → None。上に第 2 親が `upstream` で真のマージ（本番の取り込み）→ 組み立てのコミットのメッセージ | 組み立ての件名で行を持つコミットが 2 → `ValueError`。組み立てのコミットの行が無い・形が違う → `ValueError`。件名が `Release: …` でないコミットだけが行を持つ → `ValueError`。組み立てのコミットの上に第 2 親が `upstream` で偽のマージ・行に無い PR の件名のコミット → `ValueError` |
| `pull_commits` | 開いた PR → `compare` に `base_sha` と head の SHA（ブランチ名を渡さない）、merge base を返す | マージ済みの PR → マージのコミットの第 1 親から head（`_release_commits` と同じ） | 開いていて `base_sha` が None・マージのコミットが読めない → `RecordError` |
| `assembled_included`・`_included` | 行の番号（PR のコミットの外の番号を含む） | 行が無い → `require=False` は first-parent の件名から。本番の取り込みのマージ → 行の番号のまま、リリース記録も同じ番号。第 2 親が `production_sha` と同じ → GitHub を呼ばない。第 2 親が違う SHA で本番が数ページ先へ進んでいる → `merge_base` を 1 回だけ呼び、`compare` のページを読まない（スタブの呼び出しの数） | `require=True` で組み立てのコミットが無い → `RecordError`。`ValueError` → `RecordError`。リリース記録の `record` も同じ入力で止まる |
| `Lander.land` | 記録を書ける形のリリースの PR → 今どおりマージキューへ。`merge-queue` で compare の 1 ページ目を `_bump_facts` と共有し、検査が 1 ページ目を読み直さない（スタブの呼び出しの数） | 記録を書かない設定（組み立てない戦略）→ 検査しない | 組み立ての後に本番以外からマージした PR・組み立てる設定で組み立てのコミットが無い PR → マージキューへ入れず `precondition`（承認の証跡があっても） |
| `release_target` と持ち主の確かめ（`_judged`） | 開いたリリースの PR が 1 で今のスプリントの印 → その番号。`owned` と範囲が同じ compare の応答から決まる（compare が 1 回） | `production_pr` → 文脈の `pr`・`release_delegated` → 文脈の `pr`（持ち主を確かめない） | 0 で `release_pr`・2 つ → `TargetError`。唯一開いた PR の印が違う（別のスプリント）・印が無い（ベースブランチからの PR）→ `TargetError`。入力に `release_delegated` か `machine_id` が無い → `TargetError`。PR の一覧を読んだ後にリリースブランチが別のスプリントの組み立てへ差し替わる（スタブが `hub.pull` で新しい head を返す）→ 新しい head の印で `TargetError`、古い head の印で新しい head に `scope` を付けない。`check_owner` が真で手順 4 の compare が `GitHubError`・`RecordError` → 判定のステップが `stopped`、`mvv_gate` もコメントも書かない（`review_prs` を呼ばない） |
| `release_inputs`（両入口） | 同じステートマシンで、`invoke` が組む `NDF_STAGE_INPUT` と `--machine` の入力が同じ `release_delegated`・`machine_id` を持つ | 計画のレーンのスプリントで `--machine` から判定 → `release_delegated` 真、`mvv_gate.pr` が文脈の `pr`（開いた別のリリースの PR があっても選ばない） | — |
| `mvv_counts` | `approval.via: gate`・`merge.admin` が偽 → None | — | `approval.via: review` → 理由・`merge.admin` が真 → 理由 |
| `review_prs`（`release`） | 組み立てる戦略で merge base が `base_sha` → 理由の並びが空。`_judged` を通すと `hub.compare`（`pull_commits`）が 1 回だけ呼ばれ、`owned` と `review_prs` が同じ `Span` を受け取る（スタブが 2 回目の compare で別のコミットを返すようにしても結果が変わらない） | `merge_base_branch` で本番からベースへの同期のマージ（本番の先端が第 2 親）の後の組み立て → merge base が `base_sha` で空 | merge base が `base_sha` でない → 理由「ベースの先端を含まない」。委ねたスプリント → 理由。組み立てない戦略 → 番号は件名から・理由「承認資料の集合の記録が無い」。`Span.failure` あり（手順 4 の compare の失敗、`check_owner` 偽）→ compare を打ち直さず、その理由で `scope` なし |
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
| `skills/release/SKILL.md` | 「公開する」の組み立てと CHANGELOG の本文を `release-prepare` の worker の手順にし（上の「組み立ての時点」の手順 1〜8: `NDF_MACHINE_ID`・`prepared`・使い直し・範囲が壊れたときの止まり方・`-f sprint` 付きの組み立て・CHANGELOG の仕上げと印・指摘の当て方・準備完了の確かめ）、`release` の worker の手順から外す。組み立てない戦略の提示は今どおり `materials` で集める。`release-prepare` と `release-steps merge` が範囲の壊れた PR で止まったときに、止まった理由に載る `recovery` の順（1 つの関数が組む同じ文）に従って片付けて流し直す手順（手直しの退避 → 閉じる前に PR が開いていて head のリポジトリ・ref・SHA が理由のとおりかを確かめ、違えば閉じない → PR を閉じる → 対象のリポジトリの URL を名指して lease 付きでリリースブランチを消す。ブランチが残ると組み立てが `AssembleError` で止まる。lease が外れれば消さずに確かめ直す）。範囲・版・単位を変える指摘は組み直しの入力（`level`、`release_branch` では `units`）でだけ当て、当てられない指摘では PR を消させずに止まること。CHANGELOG の仕上げのコミットに行 `Ndf-Changelog: <ID>` を書き、使い直しでも `changelog` が偽なら仕上げる手順（手順 6）。リリースの PR の本文は判定の後に編集しない |
| `skills/release/references/release-workflow.md` | workflow の `step=assemble` の入力 `sprint` を足す。既存の利用先の移行の順（NDF を上げるときに `ndf-release.yml` へ入力 `sprint` の宣言と `with: sprint` を足し、`NDF_TAG` を `uses:` と `ndf_ref` の 2 か所とも同じ版へ上げる。足さないか片方だけ足す・片方だけ上げると `ndf release prepared` が組み立ての前に止まる）。組み直しで変えられる入力（`level`、`release_branch` では `units`）と、`pr` が組み立ての入力でないこと |
| `skills/development-workflow/references/pace.md` | 「具体例」の 4 と「MVV 判定」の表の順 4 を、判定する PR（`mvv_gate.pr`。自分で組み立てるなら本番宛ての開いたリリースの PR、組み立てない戦略と委ねたスプリントは文脈の `pr`）・保留を判定した PR・文脈の `pr`・含む PR から読むこと・省く条件（`scope`・`approval.via: gate`・`merge.admin` が偽）に書き換える。#3959 が書いた「リリースの保留は #4105 まで読まない」を消す |
| `skills/development-workflow/references/approval-request.md` | リリース承認ゲートの「MVV 判定」の欄の出所を、#3959 の設計承認ゲートと同じく今回の判定の出力 `outputs.mvv_gate` にし（PR の番号でコメントを探さない）、`scope.pulls`（保留を読んだ PR）か範囲を確定できない理由・`pr`・`scope.head` と今の head の違いを写す。#4105 の断りを消す |
| `skills/development-workflow/references/agent-layers.md` | #3959 が足した「手動の経路でリリース承認ゲートを返す前に `--gate release --machine` で打つ」の前に、組み立てを済ませることを足す |
| `skills/development-workflow/references/production-merge.md` | 「選んで集めるリリース方式のリリース」の順（組み立て → 承認資料 → 承認）が、どの戦略のデプロイの工程でも同じになったこと、レーンでは組み立てをオーガナイザーが持つことを 1 行で足す |
