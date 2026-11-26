# 4369: 工程「実装」を実装計画のレビューの単位ごとに流す

## 関連リンク

- 要求仕様: [issue-4369-requirements.md](./issue-4369-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4369 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4369-design.md](./issue-4369-design.md)・詳細設計書 [issue-4369-design-detail.md](./issue-4369-design-detail.md)・決定の記録 [issue-4369-design-decisions.md](./issue-4369-design-decisions.md)
- モード: `standard`（公開インタフェースが変わり、関数を新設する）

## 修正対象

パスは `plugins/ndf/` からの相対である。詳細設計書の「地図」を引き写し、下の「設計の後に main が変えた所」の分を直した。関数のシグネチャと失敗時の扱いは詳細設計書が持つ。

### 設計の後に main が変えた所

詳細設計書は main の `25c97daed` を読んで書いた。2026-11-26 の `origin/main`（`696cdc439`）は同じファイルを変えているので、実装を始める前にスプリントブランチへ `origin/main` を取り込む。食い違う所は次のとおり扱う。

| 詳細設計の記述 | main の今 | この計画での扱い |
| --- | --- | --- |
| `progress/issue_copies.py` を新設し、題と本文のコピーを移す | 同じ移動が `progress/issue_values.py` として済んでいる。`clear_values` もそこにある | 新設しない。`issue_values.py` を変える |
| `issue_values(run, work_dir, location, *, number, repo)` | `issue_values(inputs, work_dir, location)` | 第 1 引数は `inputs` のまま、`number`・`repo` を足す |
| `ndf ci merge --pr` は無い | `--pr` があり、`--branch` とは同時に渡せない | `--pr` と `--branch` を同時に受けられるようにする。`--pr` だけの呼び出しは今のまま（マージ済みなら `ok`、閉じていれば `precondition`） |
| `GitHub.merge` に `expect_base` を足す | `MergeExpect` が、送る直前とキューへ入れた後にマージ先を照らす | `expect_base` は足さない。`ci_cli` が読んだ PR の宛先を `--base` と照らせば、条件 4 を満たす |
| `github.py` は 964 行 | 1,000 行（上限） | プレリファクタリングで `Pull` を移し、`merged_pulls` は新しいファイルの関数にする |
| `invoke.py` は 985 行 | 930 行 | `plan_runs.py` への移動は変えない（`units_invoke.py` が `invoke.py` を import しないために要る） |

### ファイル

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/progress/plan_runs.py`（新設） | `StageRun`・`StageResult` と、プランの実行の記録の読み取り（`_previous`・`_from_of`・`read_report`）を `invoke.py` から移す。`UnitsStopped` を置く |
| `src/ndf/adapters/github_pulls.py`（新設） | `Pull`・`Authored`・`PULL_FIELDS` を `github.py` から移す（`github.py` は同じ名前で読み直す）。`merged_pulls` を足す。詳細設計の表に無いのは、`github.py` が行数の上限にあるため |
| `src/ndf/adapters/github.py` | 上の移動と、受け入れ条件の確かめから移す `BODY_FIELDS` |
| `src/ndf/core/plan/review_units_decide.py`（新設） | 単位の表の読み手（`has_units`・`lineup`・`acceptance_args`） |
| `src/ndf/core/issue_docs.py` | `plan_issues` |
| `src/ndf/progress/refactor_values.py` | `TIMING_HEADING` を読み手から読む |
| `src/ndf/ci_cli.py` | `--base`、`--branch` と組む `--pr`、`_target` |
| `src/ndf/domains/crossreview/{cli,finish_decide,journal}.py` | `--repo`・`--stop-on-hold`・`--reviewed-pr-out` |
| `src/ndf/domains/crossreview/{settings,drive,fixer,criteria_decide,cli}.py`・`contracts/data/review-criteria.json` | 単位の範囲の指示（`NDF_UNIT_FILE`・`unit_block`） |
| `src/ndf/contracts/worker_kinds.py`・`contracts/worker/kinds.json` | `optional_inputs` の `unit_file` |
| `contracts/domains/env.json`・`docs/contracts/env.md` | `NDF_UNIT_FILE`（文書は生成し直す） |
| `src/ndf/domains/loop/acceptance.py` | `--no-criteria` |
| `src/ndf/core/route/__init__.py` | `unit_template`・`unit_review_launch`・`review_launch` |
| `contracts/templates/{impl,pr,params}.json`・`contracts/templates/parts/steps.json` | PR の番号の保存、フォークの除外、`unit_file` |
| `src/ndf/progress/invoke.py` | `StageRun` の欄の利用、`restore_pr_number`（PR の番号の書き戻し）、単位を流す処理への分かれ |
| `src/ndf/progress/issue_values.py` | 課題とリポジトリを指定できるコピー、単位の `values.json` の掃除 |
| `src/ndf/progress/{stages,definition}.py` | `per_unit` |
| `contracts/workflow/ndf.schema.json`・`contracts/schemas/{internal/stage-snapshot,project-workflow}.schema.json`・`contracts/schemas/io/` | `per_unit`・`unit_required_checks`（`io/` は生成し直す） |
| `contracts/workflow/stages/implement.json` | `per_unit`・任意の入力 `branch`（有効にするのは Task 10） |
| `src/ndf/progress/units_invoke.py`（新設） | 開始前の確かめ、単位ごとの実行とマージ、打ち直し、スプリントの PR |
| `docs/contracts/commands.md`・`skills/development-workflow/references/agent-layers.md` | 生成し直しと手順の追記 |

## タスク分解

テストは `tests/` の下の次のファイルに置く。行数の上限（1,000 行）に近いファイルへは足さず、新しいファイルを作る。観点は詳細設計書の「単体テストの観点」と、設計書の「テスト設計」にある。

### Task 1: プレリファクタリング（型と PR の読み取りを別のファイルへ移す）
- **対象ファイル:** `src/ndf/progress/plan_runs.py`（新設）、`src/ndf/progress/invoke.py`、`src/ndf/adapters/github_pulls.py`（新設）、`src/ndf/adapters/github.py`
- **変更内容:** 振る舞いを変えずに、修正対象の表の型と関数を移す。呼ぶ側の import だけを直す
- **満たす受け入れ条件:** 無し
- **進め方:** 今の `tests/progress/`・`tests/adapters/` が通ることを確かめてから移し、移した後にもう一度通す

### Task 2: 単位の見出しが無いスプリントの今の振る舞いを縛る
- **対象ファイル:** `tests/progress/test_invoke_per_unit.py`（新設。`test_invoke.py` は 999 行ある）
- **変更内容:** 実装計画に単位の見出しが無い複数課題のスプリントで、`impl` の呼び出しが 1 回で、`values.json` と `--set` の `branch` が作業場所の今のブランチになることを確かめる退行テストを足す
- **満たす受け入れ条件:** 2
- **進め方:** 今のコードで通るテストを書く（失敗するテストから始めない）

### Task 3: 実装計画の単位の表を読む
- **対象ファイル:** `src/ndf/core/plan/review_units_decide.py`（新設）、`src/ndf/core/issue_docs.py`、`src/ndf/progress/refactor_values.py`、`tests/core/plan/test_review_units.py`（新設）、`tests/core/test_issue_docs.py`
- **変更内容:** 詳細設計書の「単位の表の読み手」のとおり。読み方は設計書の「単位の表の読み方」
- **満たす受け入れ条件:** 1・7
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: マージ済みの単位の PR を読み、マージの対象と宛先を確かめる
- **対象ファイル:** `src/ndf/adapters/github_pulls.py`、`src/ndf/ci_cli.py`、`tests/adapters/test_github_merged_pulls.py`（新設）、`tests/adapters/test_ci_cli.py`
- **変更内容:** `merged_pulls`、`Pull.from_rest` の `cross_repository`、`ndf ci merge` の `--base` と `--branch`+`--pr`（`_target`）。head・宛先・リポジトリの違いと、マージされず閉じた PR は `refused` で、要求を送らない。main の承認の確かめ（本番チャネル）の経路は変えない
- **満たす受け入れ条件:** 4・6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: 人の判断を待つ指摘で止め、読んだ PR を書き出す
- **対象ファイル:** `src/ndf/domains/crossreview/{cli,finish_decide,journal}.py`、`tests/domains/crossreview/test_crossreview_finish.py`
- **変更内容:** `--repo`・`--stop-on-hold`・`--reviewed-pr-out`（詳細設計書の「レビューの終わり方」）
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 6: 単位の範囲の指示と、条件の無い単位の確かめ
- **対象ファイル:** `src/ndf/domains/crossreview/{settings,drive,fixer,criteria_decide,cli}.py`、`contracts/data/review-criteria.json`、`src/ndf/contracts/worker_kinds.py`、`contracts/worker/kinds.json`、`contracts/domains/env.json`、`docs/contracts/env.md`、`src/ndf/domains/loop/acceptance.py`、`src/ndf/adapters/github.py`（`BODY_FIELDS`）、`tests/domains/crossreview/test_crossreview_criteria.py`、`tests/contracts/test_worker_kinds_shape.py`、`tests/domains/loop/test_loop_acceptance.py`
- **変更内容:** `NDF_UNIT_FILE`・`unit_file`・`unit_block` と `--no-criteria`
- **満たす受け入れ条件:** 1・4
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 7: `impl` のテンプレートとレビューの起動
- **対象ファイル:** `contracts/templates/{impl,pr,params}.json`、`contracts/templates/parts/steps.json`、`src/ndf/core/route/__init__.py`、`tests/route/test_templates.py`（詳細設計の表に無いが、テンプレートの組み立てのテストの置き場）
- **変更内容:** 詳細設計書の「テンプレートとレビューの起動」のとおり。単位の経路でない `impl` の `done-check` とステップの並びは変えない
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 8: 工程の定義の `per_unit` と、工程から作業を呼ぶ層の準備
- **対象ファイル:** `src/ndf/progress/{plan_runs,invoke,issue_values,stages,definition}.py`、`contracts/workflow/ndf.schema.json`、`contracts/schemas/internal/stage-snapshot.schema.json`、`contracts/schemas/project-workflow.schema.json`、`contracts/schemas/io/`、`tests/progress/test_invoke_per_unit.py`、`tests/progress/test_invoke_issue_copies.py`、`tests/progress/test_definition.py`
- **変更内容:** `StageRun` の追加の欄と `plain()`、上書きのテンプレートと値、`restore_pr_number`、`issue_values` の課題とリポジトリの指定、`clear_values` の単位の掃除、`StageDef.per_unit`。`implement.json` はまだ変えない（既定の振る舞いは `pr-number` の書き戻しのほかは変わらない）
- **満たす受け入れ条件:** 1・2・6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 9: 開始前の確かめと打ち直しの判定
- **対象ファイル:** `src/ndf/progress/units_invoke.py`（新設）、`tests/progress/test_units_invoke.py`（新設）
- **変更内容:** `prepare`・`released_branches`・`unit_branch`・`round_state`・`resume_of` と、型と定数。まだ `invoke.py` から呼ばない
- **満たす受け入れ条件:** 6・7
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 10: 単位を順に流し、`per_unit` を有効にする
- **対象ファイル:** `src/ndf/progress/units_invoke.py`、`src/ndf/progress/invoke.py`、`contracts/workflow/stages/implement.json`、`docs/contracts/commands.md`、`skills/development-workflow/references/agent-layers.md`、`tests/progress/test_units_invoke.py`、`tests/progress/test_invoke_per_unit.py`
- **変更内容:** `unit_worktree`・`include_sprint`・`unit_run`・`merge_unit`・`sprint_pr`・`run_stage` を足し、`prepare_plan`・`invoke` を単位を流す処理へつなぎ、工程「実装」に `per_unit` と任意の入力 `branch` を置く。`ndf check --write-spec` で生成し直す
- **満たす受け入れ条件:** 1・2・3・4・5・6・7
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

## レビューの単位

表の順に流す。受け入れ条件はどれも、`per_unit` を有効にする最後の単位で初めて利用者の操作から確かめられるので、確認の記録はその単位にまとめる（途中の単位が寄与する条件は、タスクの欄にある）。見込みは追加と削除の和で、テストと生成する文書を含み、数えないパスは無い。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 型の移動と退行テスト | Task 1・2 | 無し | 約 500（移動は追加と削除の両方に数える） | |
| 単位の表の読み手 | Task 3 | 無し | 約 450 | |
| PR の読み取りとマージ | Task 4 | 無し | 約 350 | |
| レビューの終わり方と単位の範囲 | Task 5・6 | 無し | 約 450 | |
| テンプレートとレビューの起動 | Task 7 | 無し | 約 300 | |
| 工程の定義と呼び出し層 | Task 8 | 無し | 約 350 | |
| 開始前の確かめと打ち直し | Task 9 | 無し | 約 600 | |
| 単位の実行と有効化 | Task 10 | 1〜7 | 約 800 | |

目安の 400 行を超える単位は、次の理由で分けない。どれも 1,000 行は超えない。最後の単位が 1,000 行を超えそうなら、`unit_worktree`・`include_sprint`・`merge_unit` とそのテストを先の単位に分ける。

- 型の移動: 追加と削除の両方に数えるためで、中身は変わらない
- 単位の表の読み手・レビュー: 実装とテストを分けると単独で読めない
- 開始前の確かめ・単位の実行: 分岐が多く、テストが行数の半分を占める

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | `progress/invoke.py` の型と実行の記録の読み取りを `plan_runs.py` へ、`adapters/github.py` の `Pull`・`Authored` を `github_pulls.py` へ移す（Task 1） | `github.py` は 1,000 行で上限にあり、足せない。`invoke.py` は 930 行で、`units_invoke.py` と型を共有するには移さないと import が循環する。`tests/progress/test_invoke.py` はテスト 68 件 |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `core/route/__init__.py` と `impl.json` の `sh -c` のラッパー、`ci_cli.py` の `_target` と `_merge_pull`、`open_pulls_of_head` と `merged_pulls` のページ送り、`invoke.py` と `units_invoke.py` の打ち直しの判定 | どれも別の単位が同じ形の処理を足す |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| main の変更と詳細設計が食い違う所が、上の表の外にもある | 取り込んだ後、各タスクの始めに対象の関数を読み直す。設計の決定を変える必要があれば止めて判断を仰ぐ |
| #3702 の実装が `progress/`・`contracts/` を先に変える | 後に入る側が取り込む |
| このスプリント自身は、自動の経路がまだ単位を読まない | supervisor で単位ごとに回す（課題の本文の工程の一覧のとおり） |

## 切り戻し手順

- 単位の PR を新しい順に打ち消す。工程「実装」の振る舞いは、最後の単位（`implement.json` の `per_unit`）を打ち消すだけで今の経路へ戻る
- データの移行は無い。`per_unit` を持つ定義を保存済みのスプリントは、保存した定義のまま流れるので、打ち消すときは該当のスプリントを確かめる

## 完了の定義

- [ ] 受け入れ条件（1〜7）をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `plugins/ndf/bin/ndf check` が通り、PR の CI のテストが通る
- [ ] 単位が 2 つ以上ある実際の課題を `ndf progress next` で流す確認を、リリース後テストへ送っている
