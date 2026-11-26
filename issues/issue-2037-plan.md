# #2037: 同じファイルでも触る関数が交わらない実装のプランを同時に流す

## 関連リンク
- 要求仕様: [issue-2037-requirements.md](./issue-2037-requirements.md)（正は [#2037](https://github.com/devbasex/ai-plugins/issues/2037) の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4295-2037-design.md](./issue-4295-2037-design.md)、詳細設計書: [issue-4295-2037-design-detail.md](./issue-4295-2037-design-detail.md)（#4295 と共通。不変条件と決定の番号はこの設計書の番号）
- 同じスプリントの実装計画: [issue-4295-plan.md](./issue-4295-plan.md)
- モード: `standard`。スプリントの課題が 2 つで、公開インタフェースも変わるので、実装計画を省かない

## 修正対象

詳細設計書の「地図」から引き写す。関数ごとの仕様は詳細設計書の各セクションにある。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/core/design_results.py`（新設） | 版の中の設計の文書から、課題の並びの触る範囲を求める |
| `plugins/ndf/src/ndf/domains/materials/collect.py` | 設計の結果の読み取りを `design_results` へ任せる |
| `plugins/ndf/src/ndf/core/plan/queue_decide.py` | `<パス>::<名前>` を分けて重なりを決める |
| `plugins/ndf/skills/design/references/design-template.md` | 触るファイルのセルの `<パス>::<名前>` の書き方 |
| `plugins/ndf/src/ndf/core/route/__init__.py` | 実装のプランの `touches` を設計の結果から補う |
| `plugins/ndf/contracts/templates/impl.json` | プランに `touches` を差し込む |
| `plugins/ndf/contracts/templates/params.json` | 差し込み `touches`・`design_pr` を定義する |
| `plugins/ndf/contracts/templates/merge.json` | 打ち直しの `probe` を、head が同じマージ済みの PR に絞る |
| `plugins/ndf/src/ndf/ci_cli.py` | `merge` の成功の結果に `outputs.pr` を載せる |
| `plugins/ndf/contracts/workflow/stages/implement.json` | 工程「実装」が文脈の `design_pr` を受ける |
| `plugins/ndf/src/ndf/progress/invoke.py` | モジュールの説明に差し込み `design_pr` の出所を足す |
| `plugins/ndf/contracts/workflow/stages/document-review.json` | 出力 `design_pr` を宣言し、`docs-merge` の出力の名前を付け替える |
| `plugins/ndf/contracts/workflow/ndf.schema.json` | ステップの `metadata.ndf` に `outputs_as` を足す |
| `plugins/ndf/contracts/schemas/internal/stage-snapshot.schema.json` | 定義のスナップショットのステップに `outputs_as` を足す |
| `plugins/ndf/src/ndf/progress/stages.py` | `StepDef` が `outputs_as` を持つ |
| `plugins/ndf/src/ndf/progress/definition.py` | `outputs_as` を読み、付け替え先を確かめる |
| `plugins/ndf/src/ndf/progress/run.py` | 工程の出力の名前を付け替えてから残す |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 単発の経路の `--add design_pr=` と `touches` の補い |
| `plugins/ndf/tests/core/test_design_results.py`（新設）、`plugins/ndf/tests/core/plan/test_queue.py`、`plugins/ndf/tests/domains/materials/test_materials_cli.py`、`plugins/ndf/tests/route/test_templates.py`、`plugins/ndf/tests/progress/test_invoke_template_values.py`、`plugins/ndf/tests/adapters/test_ci_cli.py`、`plugins/ndf/tests/progress/test_run.py`、`plugins/ndf/tests/progress/test_definition.py` | 詳細設計書の「単体テストの観点」と、設計書の「テスト設計」 |

設計 PR の番号の記録に使うファイル（`stages.py`・`definition.py`・`progress/run.py`・`document-review.json`・2 つのスキーマと、そのテスト）は、設計の結果の表では #4295 の記載にある。#2037 の受け入れ条件 1 は、工程の経路で `design_pr` が文脈に無いと満たせないので、この計画の Task 4 で作る。#4295 の実装はこの記録を使うだけにする。

## タスク分解

変更の種類は機能追加なので、最初のタスクは今の振る舞いを縛る退行テストにする。テストは `uv run --frozen --project . --all-extras pytest <対象> -q` で、対象のファイルかディレクトリに絞って流す。

### Task 1: 今の設計の結果の読み方と重なりの判定を退行テストで縛る
- **対象ファイル:** `plugins/ndf/tests/domains/materials/test_materials_cli.py`、`plugins/ndf/tests/core/plan/test_queue.py`
- **変更内容:** 材料の収集が設計の結果から得るファイルの一覧を、今のコードの出力で縛る（足りない場合だけ足す）。`test_a_plan_without_touches_overlaps_every_plan` と `test_a_test_directory_does_not_serialize_plans_touching_other_files` が通ることを確かめる
- **満たす受け入れ条件:** 2037:5、2037:6
- **進め方:** 退行テストなので、今のコードで通るテストを先に置く

### Task 2: 同じファイルでも名前が交わらなければ重ならないと判定する
- **対象ファイル:** `plugins/ndf/src/ndf/core/plan/queue_decide.py`、`plugins/ndf/tests/core/plan/test_queue.py`、`plugins/ndf/skills/design/references/design-template.md`
- **変更内容:** 詳細設計書の「触る範囲の判定」のとおり、`split_symbol` を足し、`contains` を不変条件 I5 にする。テンプレートに `` `<パス>::<名前>` `` の書き方（1 つの名前を 1 つのバッククォート）を足す
- **満たす受け入れ条件:** 2037:2、2037:3、2037:4、2037:6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 設計の結果の読み手を `core` に置く
- **対象ファイル:** `plugins/ndf/src/ndf/core/design_results.py`、`plugins/ndf/src/ndf/domains/materials/collect.py`、`plugins/ndf/tests/core/test_design_results.py`、`plugins/ndf/tests/domains/materials/test_materials_cli.py`
- **変更内容:** 詳細設計書の「設計の結果の読み手」のとおり。`design_rows`・`design_files` を移し、名前の番号は `issue_docs.design_issues` で突き合わせる。`scope_of`・`map_paths`・`result_entry`・`merge_commit`・`GitDocs` を足す。テストの入力は、設計テンプレートが定める表の形（複数のセル、`::` 付き、扱い 3 種類）から作る
- **満たす受け入れ条件:** 2037:1、2037:5（読み手の部分。条件を満たし切るのは Task 5）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。移す 2 関数は Task 1 のテストで守る

### Task 4: マージした設計 PR の番号を、工程の出力 `design_pr` に記録する
- **対象ファイル:** `plugins/ndf/src/ndf/ci_cli.py`、`plugins/ndf/contracts/templates/merge.json`、`plugins/ndf/contracts/workflow/stages/document-review.json`、`plugins/ndf/contracts/workflow/ndf.schema.json`、`plugins/ndf/contracts/schemas/internal/stage-snapshot.schema.json`、`plugins/ndf/src/ndf/progress/stages.py`、`plugins/ndf/src/ndf/progress/definition.py`、`plugins/ndf/src/ndf/progress/run.py`、`plugins/ndf/tests/adapters/test_ci_cli.py`、`plugins/ndf/tests/route/test_templates.py`、`plugins/ndf/tests/progress/test_run.py`、`plugins/ndf/tests/progress/test_definition.py`
- **変更内容:** 詳細設計書の「設計 PR の番号の記録と、工程の出力の付け替え」のとおり。`merge.json` の `probe` に書く `gh pr list --head <branch> --state merged --json number,headRefOid` は、書く前に手元で打って、一致するものが無いときと失敗したときの出力と終了コードを確かめる
- **満たす受け入れ条件:** 2037:1（工程の経路の土台）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。設計書のテスト設計の「ドキュメントレビューの出力」の 2 行を縛る

### Task 5: 実装のプランの `touches` を設計の結果から補う
- **対象ファイル:** `plugins/ndf/src/ndf/core/route/__init__.py`、`plugins/ndf/contracts/templates/impl.json`、`plugins/ndf/contracts/templates/params.json`、`plugins/ndf/contracts/workflow/stages/implement.json`、`plugins/ndf/src/ndf/progress/invoke.py`、`plugins/ndf/skills/development-workflow/references/agent-layers.md`、`plugins/ndf/tests/route/test_templates.py`、`plugins/ndf/tests/progress/test_invoke_template_values.py`
- **変更内容:** 詳細設計書の「実装のプランの補い」のとおり。`design_touches` を足し、`launch_values`・`needed`・`render`・`_one_template_errors` を直す。手順書のコマンドは、書く前に打って確かめる
- **満たす受け入れ条件:** 2037:1、2037:5
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。設計書のテスト設計の「決定 10」「#2037 の 1」「手順 5」の行を縛る

## レビューの単位

#2037 の見込みの差分が 1,000 行を超えるので、依存の向きで 2 つに分ける。2037-U1 は単独で動き（手で書いた `touches` の関数の指定が効く）、2037-U2 は 2037-U1 の読み手を使う。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 2037-U1 | Task 1・2・3 | 2037:2・2037:3・2037:4・2037:6 | 約 650 | |
| 2037-U2 | Task 4・5 | 2037:1・2037:5 | 約 450 | |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `core/route/__init__.py` は 849 行だが触るのは 5 タスク中 Task 5 だけで、`test_templates.py` に 21 件のテストがある。`progress/run.py` は 963 行だが触るのは Task 4 の 1 関数で、`test_run.py` に 60 件ある。`queue_decide.py` は 161 行で `test_queue.py` に 8 件ある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `core/plan/queue_decide.py` の `contains` と `progress/lane/rules.py` の `touch_hit` | 触る範囲の重なりの判定が 2 か所にある。レーンはファイル単位、プランは関数単位という違い（決定 4）を保ったまま、値の分け方と正規化をそろえられるかを見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `agent-layers.md` を、開いている PR [#4502](https://github.com/devbasex/ai-plugins/pull/4502)（確定仕様化の自動のプランの直し）と [#4488](https://github.com/devbasex/ai-plugins/pull/4488)（承認ゲートの手前の MVV 判定の直し）も変えている（2026-11-25 の確認） | 「スプリントの中の並列」だけを変え、後にマージする側が base-sync で直す |
| 工程の出力に `design_pr` を足すと、前の版が始めたスプリントのスナップショットは `outputs_as` を持たない | 既定の空で読む。Task 4 のテストで、項目の無いスナップショットを読めることを縛る |

## 切り戻し手順
- どちらの単位も Pull Request の revert で戻せる。データの移行は無い。戻した後のプランは `touches` を持たず、今と同じく直列に流れる

## 完了の定義
- [ ] 受け入れ条件 1〜6 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `standard` の検証の段階を通る（`ndf loop design-tests --issue 2037` を含む）
- [ ] `ndf check` と CI の検査が通る
