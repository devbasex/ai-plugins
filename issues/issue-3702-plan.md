# #3702: 実装計画を設計 PR に載せ、単位の実行順と大きさを機械で確かめる

## 関連リンク

- 要求仕様: [issue-3702-requirements.md](./issue-3702-requirements.md)（正は #3702 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3702-design.md](./issue-3702-design.md)・詳細設計書: [issue-3702-design-detail.md](./issue-3702-design-detail.md)
- 単位の正本: [issue-3702-plan.units.json](./issue-3702-plan.units.json)（この計画の 2 つの表は、実装の後は `ndf plan units render` が作る。今はコマンドが無いので手で写し、同じ規則で確かめた）
- モード: `standard`（公開インタフェース（コマンド・宣言・工程の並び）が変わり、触るファイルが 4 を超える）

## 修正対象

詳細設計書の「地図」の表のとおり。手順書の 8 ファイル（タスク 9）は地図の注記にある。

## タスク分解

最初のタスクは機能追加の決まりどおり、今の振る舞いを縛る退行テストである。

### Task 1: 今の工程表とキューの組を縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/progress/test_definition.py`
- **変更内容:** 工程表の行の数（18）と、設計承認ゲートのステップが 1 つだけあることを、並びを変える前に縛る。並びを変えた後もこの 2 つは保つ
- **満たす受け入れ条件:** 1
- **進め方:** 今のまま通るテストを足す（退行の網）

### Task 2: 単位のファイルの形と上限の宣言・既定を置く

- **対象ファイル:** `plugins/ndf/contracts/workflow/review-units.schema.json`（新設）、`plugins/ndf/contracts/limits.json`、`plugins/ndf/contracts/schemas/project-workflow.schema.json`、`plugins/ndf/contracts/schemas/ndf-settings/project.schema.json`
- **変更内容:** 詳細設計書の「単位のファイルの形」と宣言 `workflow.review_units`、上限の既定 400 / 1,000
- **満たす受け入れ条件:** 3・6
- **進め方:** スキーマを通る例と断る例を Task 5 のテストで縛る（ここでは形だけ）

### Task 3: 実行順・導く値・規則の判定

- **対象ファイル:** `plugins/ndf/src/ndf/core/plan/units_decide.py`（新設）、`plugins/ndf/tests/core/plan/test_units_decide.py`（新設）
- **変更内容:** `parse`・`orders`・`views`・`judge`（U1〜U6・W1〜W2）
- **満たす受け入れ条件:** 2・5・7
- **進め方:** 規則ごとに落ちるテスト → 最小の実装 → 整理

### Task 4: 分け方の案と #3698・#3247・#3672 の入力

- **対象ファイル:** `plugins/ndf/src/ndf/core/plan/units_decide.py`、`plugins/ndf/tests/core/plan/test_units_decide.py`、`plugins/ndf/tests/core/plan/fixtures/units/issue-3672.units.json`（新設）
- **変更内容:** `split`・`apply`（同じ深さで同じファイルを触る大きなタスクの直列・後続の依存の付け替え・条件の無い準備のタスクの併せ・案を当てた後のグラフ全体の U4 の直しと書き手へ返す `resplit`・案を当てた後の `judge`）。#3672 の fixture は詳細設計の地図の行をタスクにし、「依存する先」を `after`、#3698 のファイルごとの追加と削除の和を見込みにして作る
- **満たす受け入れ条件:** 5・7・8
- **進め方:** 3 つの入力と、同じ深さで同じファイルを触る 600 行のタスク 2 つ、それに元の単位と同じファイルを触る実行順 2 の別の単位（同じファイル・別のファイル）を足した入力、条件なしの準備 500 行と条件ありの実装 600 行の入力で落ちるテスト → 実装

### Task 5: 単位のファイルの集め方・読み書きと `ndf plan units`

- **対象ファイル:** `plugins/ndf/src/ndf/core/plan/units_files.py`（新設）、`plugins/ndf/src/ndf/core/plan/cli.py`、`plugins/ndf/tests/core/plan/test_units_cli.py`（新設）
- **変更内容:** `collect`（設計の結果の扱いを先に決め、計画の名前の規則を Markdown と単位のファイルに双方向に当て、省く判断 `omitted` の無い欠落・スプリントの起点に無い印の無い計画・単位を作らない課題の計画とタスクで止める）・`load`（パスの正規形と既存のディレクトリ・リポジトリの外の検査）・`designed`・`criteria_of`・`limits`・`render`・`parallel_cap`・`numbering`・`unit_plans`（流し方の記録 `units.json` も書く）と、サブコマンド `units check`（`--docs`・`--omitted`・`--lane-plans`・`--out`。`unit` の項目はスプリント全体の `seq`・マージの順・並列の相手）・`render`。`check` は上限で止めるときに Task 4 の案を返す。手で流す入口（`ndf progress units plans`・`run`）は単位ごとの差し込みを `progress` が組むので Task 8 に置く
- **満たす受け入れ条件:** 3・4・6
- **進め方:** コマンドの結果 JSON で落ちるテスト → 実装。書いたプランを `queue_decide.waves` に渡して組を確かめ、依存のある 2 単位を別々の本物の `ndf plan queue` の起動で流す。`issue-10-11-plan.md` に印があり JSON が無い入力、計画も `omitted` も無い課題、旧い形と省いた課題の `single` が `check` と `unit_plans` の両方で通る入力、2 つの計画を `render` で 1 つずつ書いた後にまとめて `check --docs` する入力、JSON だけ・印の欠け・このスプリントで書いた印の無い計画、「閉じる」課題のタスクを持つ統合計画、条件 2 を落とした単位、`src/./a.py` と既存のディレクトリのパスを足す

### Task 6: 設計の結果の単位の数の規則を消し、表の読み手を 1 つにする

- **対象ファイル:** `plugins/ndf/src/ndf/core/plan/inputs_decide.py`、`plugins/ndf/tests/core/plan/test_inputs.py`、`plugins/ndf/src/ndf/domains/materials/collect.py`
- **変更内容:** `design_gaps` の「PR の単位が 2 つ以上」の理由と、そのテストを消す（設計書の決定 2）。設計の結果の表の読み手を詳細設計書の「設計の結果の表の読み手」の形で `inputs_decide` に置く: 行の型は `DesignResult` に `handling`（扱い）と `into`（統合先）を足したもの、統合文書は名前の番号の並び（`DESIGN_DOC`・`design_numbers`）で探して `issue-10-11-design.md` の #11 の行も読む。`materials.collect` は `design_rows`・`DESIGN_FILE` を消し、`design_files` の中でパスを `design_results` の `files` から取り出す（返りの形は変えない）。受け入れ条件の中身の指紋 `criteria_digest` を `inputs_decide` に置く（Task 5 の `criteria_of` と Task 8 の `loop acceptance check` が使う）
- **満たす受け入れ条件:** 3
- **進め方:** 単位の数のテストを消し、`design_rows` のテストを `design_results` へ移してから、扱い・統合先・統合文書の #11 の行で落ちるテストを足す → 実装。`materials` の既存のテストが変えずに通ることを確かめる

### Task 7: 設計のフェーズの工程の並びと、設計 PR の承認・マージ

- **対象ファイル:** `contracts/workflow/stages/{plan-pr,design-detail-pr,design-draft,design-approval,document-rebuild-pr}.json`（新設。共有の `plan`・`design-detail`・`document-rebuild` は変えない）・`modes/{standard,legacy-refactor}.json`・`context.schema.json`・`ndf.schema.json`（`restart_on`・`worktree_input`）・`contracts/templates/{design-draft,design-review,design-land,implementation-plan}.json`・`src/ndf/core/route/__init__.py`・`src/ndf/progress/invoke.py`（`ndf.plan` にも `NDF_STAGE_INPUT` を渡す・`{{stage_dir}}`・`worktree_input`）・`src/ndf/progress/{run,stages,definition,command}.py`（`reopened`・`restart_on`・`ndf progress design target / sync`）・`src/ndf/progress/gate/{asking,answering}.py`・`src/ndf/ci_cli.py`・`src/ndf/adapters/github.py`（`ci merge` の照合の経路・`ci watch` の成功の `instance`）と `tests/progress/` の `test_definition.py`・`test_command.py`・`test_sprint_templates.py`・`test_invoke.py`・`test_run.py`・`gate/test_gate_answer.py`、`tests/adapters/test_ci_cli.py`（いずれも `plugins/ndf/` の下）
- **変更内容:** 詳細設計書の「設計 PR の承認とマージ」「マージの対象の照合と再開」と「工程の定義」のうち設計のフェーズ。工程の入力 `issues`・`plan_omitted` を `design-approval` に足し、`_call_plan` が `ndf.plan` の起動にも工程の入力を渡して `units check --from-stage` まで届ける。Draft の解除は Draft のときだけ打つ。設計の作業場所 `<branch>-design` を作り、設計 PR を宛先 `pr_target("doc")` で開く（`doc_prs` の宣言を尊重する）。`ci_cli._report` の成功の結果にも判定した HEAD を `instance` に載せ、`ndf ci watch --pr <design_pr> --conflict` で記録済みの番号に固定して CI と衝突を同じ読み取りで判定し、`ndf progress design target` が両結果の成功と用途・4 つの HEAD（単位を確かめた・ローカル・PR・CI を判定した）・検査が HEAD のコミットの内容を読んだことを確かめたときだけ承認の記録 `{{stage_dir}}/design-target.json` を書く。方式は宣言を先に読む `declared_merge_methods` で宛先ごとに決め（リポジトリの設定の読みは共有する）、`design target` はスプリントのブランチを `--sprint` で受け取る。承認資料は記録と検査の結果を照らしてから読んだバイト列で作り、回答を `design-answer.json` に結ぶ（手動は `ndf progress design ask`・`answer`）。`design-land` は `ci merge --target --answer` で回答との一致を確かめてから記録の組（番号・ブランチ・宛先・HEAD・方式・時刻）を照らしてマージし（残リスク・方式・再オープン・宛先の変更も照らす。マージキューの経路は承認した例外のときだけ登録する）、マージキューの後の打ち直しでマージ済みを成功にする。照合で断られたら `restart_on` で「ドキュメントレビュー」を開き直す（マージ済みの PR を成功にしないとき・本文に未決の残リスクがあるとき・キューにいる PR を断るときは `precondition` で人に返し、開き直さずに同じ argv の打ち直しで続ける。照合の経路はキューから外さず、キューの中で見つけた違反を受け取りに残す。マージ済みの PR は、承認対象と方式に結び付いた受け取りと、登録からマージまでのキューの事象で照らす）。設計ブランチは宛先 `design_base` から切る。マージの後は `design-sync` で取り込み、承認したファイルと照らしてから push する。`reopened` は工程の世代を進めて保存先を分ける。承認で課題を外したら `answering.py` が工程に属さない `issues_set` と「計画」から「ドキュメントレビュー」までの `reopened` を書き、「計画」の `design-drop` が外した課題の文書と設計の結果を取り除く。新しい経路の「詳細設計」「計画」「ドキュメント再構成」は新設の工程の定義に分け、共有の定義は変えない。`standard`・`legacy-refactor` は常に設計 PR を分ける。`light`・`operation`・`documentation` は変えない
- **満たす受け入れ条件:** 1・4
- **進め方:** 5 つのモードの偽の流し（`legacy-refactor` が `design_pr` と `rebuild_findings` を出して最後まで流れる）・ゲートの位置・Draft の解除（Ready 済みで打たない）・CI を落として直した後にレビューと単位の検査をやり直して承認まで進む流し・`ci watch` の成功から承認まで通る流し・HEAD の食い違い・承認 1 回・`--target`・番号か宛先の違い・再オープン・残リスク・方式の違いで止まる・API の回数・マージ済みの打ち直し・課題を外した回答で 3 つの工程が開き直る・照合の失敗で「ドキュメントレビュー」が開き直る・`doc_prs: base` の `design-sync`・既定のレビューの argv で落ちるテスト → 定義と実装。本物の `ndf` の CLI で `design-review` のプランを流し、`units check --from-stage` が工程の `issues` を受け取ることを確かめる

### Task 8: 単位ごとの作業場所・プラン・キュー・マージで流す工程「実装」

- **対象ファイル:** `contracts/workflow/stages/implement-units.json`（新設）・`units-run.schema.json`（新設）・`ndf.schema.json`・`modes/{standard,legacy-refactor}.json`・`contracts/templates/{impl,params}.json`・`src/ndf/progress/{stages,definition,invoke,command}.py`・`src/ndf/core/plan/cli.py`（`units prompt`）・`src/ndf/domains/worktree/{create,cli}.py`（`--no-fetch`）・`src/ndf/domains/loop/acceptance.py`（`loop acceptance check --criteria-digest`）と `tests/progress/` の `test_definition.py`・`test_sprint_templates.py`・`test_invoke.py`・`test_command.py`、`tests/core/plan/test_units_cli.py`、`tests/domains/worktree/test_worktree_cli.py`、`tests/domains/loop/test_loop_acceptance.py`（いずれも `plugins/ndf/` の下）
- **変更内容:** 詳細設計書の「工程の流し方」「流し方の記録」「worker への単位の指示」と `per_unit`（`per_issue` は残す）。実行順ごとに 1 回だけ取得した SHA を `units.json` に固定し、`ndf worktree create --from <SHA> --no-fetch` で単位の作業場所を作る。キューは常に `--resume` で打つ。PR の番号・宛先・レビュー済みの HEAD・時刻をマージの最初の要求より前に記録し、`ci merge` の照合の経路（`--method`・`--since` 付き）でマージ済み・キューの中・キューから外れたを判定する。HEAD の違い・衝突は記録を `stale` にしてその単位のプランだけを `--from` でレビューから打ち直し、記録の HEAD を書き換える。`unit_file` は既定を空にし、ステップ `prompt`（`ndf plan units prompt`）が impl のプロンプトの全文（単位は担当のタスク、`single` と従来の工程は課題の全体）を書き、worker のステップへ `NDF_WORK_PROMPT` で渡す。再開は保存した `units.json` と状態を 1 組で読み、`stale` の記録は保存した開始点から打ち直す。最後のマージの後にスプリントの worktree を `git merge --ff-only <remote>/<sprint>` で早送りしてから `gathered` へ進む。手で流す入口 `ndf progress units plans`（単位ごとの差し込みは工程と同じ `values_of`、`--set` は共通の差し込みだけ）・`ndf progress units run`（工程と同じ `run_units`）を置く。単位の `acceptance` に承認した条件の指紋 `--criteria-digest` を付け、`ndf loop acceptance check` は本文の条件の指紋が承認と違えば記録を照らさずに止める（条件の出典は課題の本文だけ。詳細設計書の「受け入れ条件の出典」）
- **満たす受け入れ条件:** 3・4
- **進め方:** 単位ごとの作業場所とブランチ・単位の値で組んだ `review`・マージの前の記録・単位の作業場所での `ci merge --pr`・マージ済みの単位を飛ばす打ち直し・キューから外れた PR の要求し直し・`ci_pending` での停止・最後のマージの後の早送りと `gathered` の `ok`・`--max` の値・2 課題の `units plans` の単位ごとの題と `review`・単位ごとの差し込みの `--set` で止まる・`units run` が実行順 1 のマージの後に実行順 2 を流す・別の起動の `units run` が `units.json` から上限と順を復元する・実行順ごとの取得が 1 回・`stale` の単位の再レビューと記録の書き換え・`unit_file` を渡さない `ndf route render impl` が通る・`per_issue` だけのスナップショットの読み込みで落ちるテスト → 実装

### Task 9: 手順書を単位のファイルと新しい工程の並びに合わせる

- **対象ファイル:** `implementation-plan/SKILL.md`（単位のファイルと 2 つの表・並列と直列の欄・`tasks[].issue`）、`design/SKILL.md` と `design/references/design-template.md`（工程の並び・設計の結果と単位の関係）、`requirements-design/references/document-roles.md`（実装計画のセクションの順に「並列と直列」）、`development-workflow/SKILL.md`・`references/agent-layers.md`（フェーズの表・実装のフェーズの単位を、手で流すときの手順「`ndf progress units plans` で単位のプランを書き、`ndf progress units run` を打つ。`run` は実行順ごとに作業場所を作り、`ndf plan queue --max <metrics.max>` で同じ実行順だけを流し、その実行順の単位をすべてマージしたと確かめてから次の実行順へ進む。止まったら同じ `run` を打ち直すと記録から続く」に置き換える。生成したプランを一度のキューにまとめて渡さない）・`references/stage-notes.md`（`legacy-refactor` は常に設計 PR を分ける）・`references/approval-request.md`（承認資料に単位の表と `check` の結果・省いた課題。#3908 でテンプレートが動かない間に supervisor が手で打つ承認前の検査・停止・照合の手順＝詳細設計書の「手で流す経路」の 1〜5: 最終の HEAD で `ndf plan units check --docs --out`、`ok` でなければ承認を求めない、`ndf ci watch` の後に `ndf progress design target` で 4 つの SHA をそろえて記録を書く、資料は記録から作る、承認の後は `ndf ci merge --target` と `ndf progress design sync`。どれかが止まれば最初から）、`issue-plan-strategy/references/execution-plan.md`（実装の行を 1 回で置く）
- **変更内容:** 設計書の決定 1・2・7 の手順
- **満たす受け入れ条件:** 1・2・4
- **進め方:** テスト駆動を適用しない（`.md` の文言を照らすテストは書かない決まり）。書いたコマンドは書く前に打って確かめる。工程（Task 7・8）の後にマージし、手順が工程より先に入らないようにする

## レビューの単位

<!-- ndf:units:begin -->
| 単位 | 実行順 | マージの順 | 含むタスク | 先に要る単位 | 満たす受け入れ条件 | 見込みの行数 | 実際の行数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 契約と退行テスト | 1 | 1 | Task 1・2・6 | — | 3702:1・3702:3・3702:6 | 280 | |
| 判定 | 1 | 2 | Task 3 | — | 3702:2・3702:5・3702:7 | 380 | |
| 分け方の案 | 2 | 3 | Task 4 | 判定 | 3702:5・3702:7・3702:8 | 430 | |
| コマンド | 3 | 4 | Task 5 | 契約と退行テスト・判定・分け方の案 | 3702:3・3702:4・3702:6 | 480 | |
| 工程（設計のフェーズ） | 4 | 5 | Task 7 | コマンド | 3702:1・3702:4 | 760 | |
| 工程（実装） | 5 | 6 | Task 8 | コマンド・工程（設計のフェーズ） | 3702:3・3702:4 | 700 | |
| 手順書 | 6 | 7 | Task 9 | 工程（実装） | 3702:1・3702:2・3702:4 | 400 | |
<!-- ndf:units:end -->

行数は追加と削除の和の見込みで、再生成するファイルと lock は数えない（このスプリントには無い）。合わせて約 3,430 行を 7 本の PR に分ける。分け方の案・コマンド・工程の 4 単位は目安の 400 行を超える（W1 の警告）が、どれも上限の 1,000 行以下で、触るファイルが工程の定義と CLI の対でまとまるため分けない。

## 並列と直列

<!-- ndf:order:begin -->
| 実行順 | 並列に流す単位 | 理由 |
| --- | --- | --- |
| 1 | 契約と退行テスト・判定 | 同じ実行順で依存が無く、触るファイルが重ならない |
| 2 | 分け方の案 | — |
| 3 | コマンド | — |
| 4 | 工程（設計のフェーズ） | — |
| 5 | 工程（実装） | — |
| 6 | 手順書 | — |

| 先の単位 | 後の単位 | 理由 |
| --- | --- | --- |
| 判定 | 分け方の案 | T4 が T3 の判定の型と関数を使い、同じファイルを触る |
| 契約と退行テスト | コマンド | T5 が T2 のスキーマと上限の既定、T6 の設計の結果の表の読み手を読む |
| 判定 | コマンド | T5 が T3 の judge を呼ぶ |
| 分け方の案 | コマンド | T5 の check が上限で止めるときに T4 の分け方の案を返す |
| コマンド | 工程（設計のフェーズ） | 工程「ドキュメントレビュー」が T5 の ndf plan units check --docs を打つ |
| コマンド | 工程（実装） | 工程「実装」が T5 の unit_plans と parallel_cap を呼ぶ |
| 工程（設計のフェーズ） | 工程（実装） | T8 が T7 と同じモードの定義とテストのファイル・`invoke.py`・`command.py` を触り、T7 の `ci merge` の照合の経路を使う |
| 工程（実装） | 手順書 | 手順書が T7・T8 の工程の並び・出力と T5 のコマンドの返りを書く。工程より先に手順だけが入らないよう直列にする |
<!-- ndf:order:end -->

判定（公開する前の部品）と分け方の案を先に入れ、利用者が打つコマンドは両方がそろってから入れる（上限で止まる経路に案が無い版を作らない）。工程と手順書は直列で、どちらもスプリントブランチの上で揃ってから 1 回のリリースで利用者に届く。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る既存のファイルは `cli.py`（約 250 行）・`invoke.py`・`inputs_decide.py` で、足すのは新しいモジュールが中心。既存のファイルを大きく変えるのは Task 8 の `invoke.py` だけ |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `units_files.unit_plans` と `progress/invoke.py` の差し込みの組み立て | 2 つの単位（コマンド・工程（実装））が impl のプランの値を組む。重なれば 1 か所へ寄せる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 工程の定義を触る別の変更（#3908 の直し・#3092 の後続）と `contracts/workflow/stages/` が重なる | 後からマージする側が取り込む。工程の単位を実行順 4・5 に置き、先に入った変更の上で直す |
| #3908 でテンプレートが動かず、工程から組む経路を本物で流せない | 定義の検査と `test_sprint_templates.py` の偽の流しで縛り、本物はリリース後テストで流す |
| レーン lsplit の触る場所の外の 13 ファイル: 上限の宣言と既定の正本 3（`contracts/limits.json`・`contracts/schemas/project-workflow.schema.json`・`contracts/schemas/ndf-settings/project.schema.json`。Task 2）、`src/ndf/domains/materials/collect.py`（Task 6）、`src/ndf/core/route/__init__.py`（Task 7）、`src/ndf/ci_cli.py`・`src/ndf/adapters/github.py`・`tests/adapters/test_ci_cli.py`（Task 7）、`src/ndf/domains/worktree/{create,cli}.py`・`tests/domains/worktree/test_worktree_cli.py`・`src/ndf/domains/loop/acceptance.py`・`tests/domains/loop/test_loop_acceptance.py`（Task 8）。cross-review の 1 周目の直しで足した `src/ndf/progress/run.py`・`src/ndf/progress/gate/{asking,answering}.py`・`tests/progress/test_run.py`・`tests/progress/gate/test_gate_answer.py`（Task 7）は、レーンの触る場所（`plugins/ndf/src/ndf/progress/`・`plugins/ndf/tests/progress/`）の中にあり、数に入らない | 設計承認ゲートで、13 ファイルとそれを触るタスクを示し、触る場所に足すかを見てもらう（実装の途中でレーンの外の変更として止めない） |

## 完了の定義

- [ ] 受け入れ条件 1〜8 をすべて満たし、条件ごとに検証手段（PR の CI のテスト・`ndf check`）と結果が対応している
- [ ] 7 つの単位の PR がそれぞれ cross-review を収束させてスプリントブランチへ入った（単位の PR は `issues/issue-3702-plan.units.json` と表を触らない）
- [ ] 最後の実行順（手順書）のマージの後に、supervisor が 1 回だけ、各単位の PR のファイルごとの追加と削除（`gh pr view <番号> --json files` の `path`・`additions`・`deletions`）から、見込みと同じ規則で数えないファイル（再生成のファイルと lock。上の「レビューの単位」の行数の注記の規則。このスプリントには無い）を除いた和を `issues/issue-3702-plan.units.json` の `units[].actual_lines` に書き、`ndf plan units render issues/issue-3702-plan.units.json --doc issues/issue-3702-plan.md` で表を作り直して、1 コミットでスプリントブランチへ入れた（並列の単位が互いの値を消さないよう、書くのはこの 1 回だけ）
