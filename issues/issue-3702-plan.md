# #3702: 実装計画を設計 PR に載せ、単位の実行順と大きさを機械で確かめる

## 関連リンク

- 要求仕様: [issue-3702-requirements.md](./issue-3702-requirements.md)（正は #3702 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3702-design.md](./issue-3702-design.md)・詳細設計書: [issue-3702-design-detail.md](./issue-3702-design-detail.md)
- 単位の正本: [issue-3702-plan.units.json](./issue-3702-plan.units.json)（この計画の 2 つの表は、実装の後は `ndf plan units render` が作る。今はコマンドが無いので手で写し、同じ規則で確かめた）
- モード: `standard`（公開インタフェース（コマンド・宣言・工程の並び）が変わり、触るファイルが 4 を超える）

## 修正対象

詳細設計書の「地図」の表のとおり。手順書の 8 ファイル（タスク 8）は地図の注記にある。

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
- **変更内容:** `split`。#3672 の fixture は詳細設計の地図の行をタスクにし、「依存する先」を `after`、#3698 のファイルごとの追加と削除の和を見込みにして作る
- **満たす受け入れ条件:** 5・7・8
- **進め方:** 3 つの入力で落ちるテスト → 実装

### Task 5: 単位のファイルの読み書きと `ndf plan units`

- **対象ファイル:** `plugins/ndf/src/ndf/core/plan/units_files.py`（新設）、`plugins/ndf/src/ndf/core/plan/cli.py`、`plugins/ndf/tests/core/plan/test_units_cli.py`（新設）
- **変更内容:** `find`・`load`・`limits`・`render`・`unit_plans` と、サブコマンド `units check`・`render`・`plans`
- **満たす受け入れ条件:** 3・4・6
- **進め方:** コマンドの結果 JSON で落ちるテスト → 実装。書いたプランを `queue_decide.waves` に渡して組を確かめる

### Task 6: 設計の結果の単位の数の規則を消す

- **対象ファイル:** `plugins/ndf/src/ndf/core/plan/inputs_decide.py`、`plugins/ndf/tests/core/plan/test_inputs.py`
- **変更内容:** `design_gaps` の「PR の単位が 2 つ以上」の理由と、そのテストを消す（設計書の決定 2）
- **満たす受け入れ条件:** 3
- **進め方:** テストを先に消し、残りのテストが通ることを確かめる

### Task 7: 工程の並びとゲートの位置、単位ごとのプランで流す工程「実装」

- **対象ファイル:** 詳細設計書の地図の `contracts/workflow/stages/`・`modes/`・`contracts/templates/{design,implementation-plan}.json`・`src/ndf/progress/{stages,definition,invoke}.py` と `tests/progress/` の 4 ファイル
- **変更内容:** 詳細設計書の「工程の定義」と「工程の流し方」
- **満たす受け入れ条件:** 1・3・4
- **進め方:** 工程の並び・ゲートの位置・`per_unit` の流し方で落ちるテスト → 定義と実装

### Task 8: 手順書を単位のファイルと新しい工程の並びに合わせる

- **対象ファイル:** `implementation-plan/SKILL.md`（単位のファイルと 2 つの表・並列と直列の欄）、`design/SKILL.md` と `design/references/design-template.md`（工程の並び・設計の結果と単位の関係）、`requirements-design/references/document-roles.md`（実装計画のセクションの順に「並列と直列」）、`development-workflow/SKILL.md`・`references/agent-layers.md`（フェーズの表・実装のフェーズの単位を `ndf plan units plans` に）・`references/approval-request.md`（承認資料に単位の表と `check` の結果）、`issue-plan-strategy/references/execution-plan.md`（実装の行を 1 回で置く）
- **変更内容:** 設計書の決定 1・2・7 の手順
- **満たす受け入れ条件:** 1・2・4
- **進め方:** テスト駆動を適用しない（`.md` の文言を照らすテストは書かない決まり）。書いたコマンドは書く前に打って確かめる

## レビューの単位

<!-- ndf:units:begin -->
| 単位 | 実行順 | マージの順 | 含むタスク | 先に要る単位 | 満たす受け入れ条件 | 見込みの行数 | 実際の行数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 契約と退行テスト | 1 | 1 | Task 1・2・6 | — | 3702:1・3702:3・3702:6 | 170 | |
| 判定 | 1 | 2 | Task 3 | — | 3702:2・3702:5・3702:7 | 380 | |
| 分け方の案 | 2 | 3 | Task 4 | 判定 | 3702:5・3702:7・3702:8 | 350 | |
| コマンド | 2 | 4 | Task 5 | 契約と退行テスト・判定 | 3702:3・3702:4・3702:6 | 380 | |
| 工程 | 3 | 5 | Task 7 | コマンド | 3702:1・3702:3・3702:4 | 390 | |
| 手順書 | 3 | 6 | Task 8 | コマンド | 3702:1・3702:2・3702:4 | 400 | |
<!-- ndf:units:end -->

行数は追加と削除の和の見込みで、再生成するファイルと lock は数えない（このスプリントには無い）。合わせて約 2,070 行を 6 本の PR に分け、どれも目安の 400 行以下である。

## 並列と直列

<!-- ndf:order:begin -->
| 実行順 | 並列に流す単位 | 理由 |
| --- | --- | --- |
| 1 | 契約と退行テスト・判定 | 同じ実行順で依存が無く、触るファイルが重ならない |
| 2 | 分け方の案・コマンド | 同じ実行順で依存が無く、触るファイルが重ならない |
| 3 | 工程・手順書 | 同じ実行順で依存が無く、触るファイルが重ならない |

| 先の単位 | 後の単位 | 理由 |
| --- | --- | --- |
| 判定 | 分け方の案 | T4 が T3 の判定の型と関数を使い、同じファイルを触る |
| 契約と退行テスト | コマンド | T5 が T2 のスキーマと上限の既定を読む |
| 判定 | コマンド | T5 が T3 の judge を呼ぶ |
| コマンド | 工程 | 工程「計画」と「実装」が T5 の ndf plan units と unit_plans を呼ぶ |
| コマンド | 手順書 | 手順書が T5 のコマンドの名前と返りを書く |
<!-- ndf:order:end -->

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る既存のファイルは `cli.py`（約 250 行）・`invoke.py`・`inputs_decide.py` で、足すのは新しいモジュールが中心。同じ既存のファイルを触るタスクは Task 7 の `invoke.py` だけ |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `units_files.unit_plans` と `progress/invoke.py` の差し込みの組み立て | 2 つの単位（コマンド・工程）が impl のプランの値を組む。重なれば 1 か所へ寄せる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 工程の定義を触る別の変更（#3908 の直し・#3092 の後続）と `contracts/workflow/stages/` が重なる | 後からマージする側が取り込む。工程の単位を実行順 3 に置き、先に入った変更の上で直す |
| #3908 でテンプレートが動かず、工程から組む経路を本物で流せない | 定義の検査と `test_sprint_templates.py` の偽の流しで縛り、本物はリリース後テストで流す |
| レーン lsplit の触る場所の外の 3 ファイル（Task 2） | 設計承認ゲートで触る場所に足すかを見てもらう |

## 完了の定義

- [ ] 受け入れ条件 1〜8 をすべて満たし、条件ごとに検証手段（PR の CI のテスト・`ndf check`）と結果が対応している
- [ ] 6 つの単位の PR がそれぞれ cross-review を収束させてスプリントブランチへ入り、実際の行数を表に書き足した
