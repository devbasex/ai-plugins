# 3908: 工程のプランが作業のプロンプトを組んで worker を起動し、工程が手を借りずに流れるようにする

## 関連リンク

- 要求仕様: [issue-3908-requirements.md](./issue-3908-requirements.md)。受け入れ条件は番号（1〜5）、起票者の条件は S1・S2 で指す
- 設計書: [issue-3908-design.md](./issue-3908-design.md)・詳細設計書: [issue-3908-design-detail.md](./issue-3908-design-detail.md)
- モード: `standard`（不具合の直しで、工程のプランの差し込みの既定・`ndf progress next` と `ndf progress check` の返り・結果 JSON の受け渡しを変える安定版の変更。設計承認ゲートを通した設計文書を持つ）

## 修正対象

詳細設計書の「地図」から引いた。表に無い `plugins/ndf/src/ndf/progress/gate/__init__.py` は Task 3 で、`plugins/ndf/tests/progress/test_definition.py`・`test_command_check.py` は Task 9 で足す（理由は各タスクにある）。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/contracts/templates/params.json` | 差し込み `worker` の既定 |
| `plugins/ndf/contracts/worker/kinds.json`（新設） | 作業の名前 18 件ごとの定義（唯一の基準） |
| `plugins/ndf/contracts/worker/prompt.md`（新設） | 起動指示の 7 項目と規則 7 個の共通のテンプレート |
| `plugins/ndf/contracts/workflow/context.schema.json` | 文脈のキーごとの `x-source` |
| `plugins/ndf/src/ndf/progress/worker_kinds.py`（新設） | `work_kinds`・`kind_values`・`kind_gaps`・`work_dir_of` |
| `plugins/ndf/src/ndf/progress/invoke.py` | `values.json` の読み書き・`prepare_plan`・`plan_failure`・`plan_path`・`_call_plan`・`_fill_refactor`・代表の課題 |
| `plugins/ndf/src/ndf/progress/run.py` | `step_dir`・`_start`・`_run_step` |
| `plugins/ndf/src/ndf/progress/command.py` | `_cmd_check`・`base_ref`・`_sprint_range` |
| `plugins/ndf/src/ndf/progress/definition.py` | `context_source`・`_missing_keys` |
| `plugins/ndf/src/ndf/progress/gate/entry.py` | `work_prompt`・`_write_prompt`・`work_argv`・`work_parser`・`_cmd_work` |
| `plugins/ndf/src/ndf/core/worker.py` | `outcome_of`・`_add_run`・`_cmd_run`（`outputs` の受け渡しと `--require-output`） |
| `plugins/ndf/src/ndf/core/plan/steps.py`・`transition_decide.py`・`runner.py` | ステップの `gaps` をプランの停止結果へ渡す |
| `plugins/ndf/src/ndf/core/branch_strategy.py` | `of_project` |
| `plugins/ndf/src/ndf/core/route/__init__.py` | `REFACTOR_WITH_PR`・`refactor_launch`・`acceptance` の補い |
| `plugins/ndf/docs/contracts/commands.md` | `ndf answer work` の節（生成物） |
| `plugins/ndf/tests/progress/test_invoke.py`・`test_invoke_refactor.py`・`test_run.py`・`test_sprint_templates.py`・`gate/test_gate_work.py` | 差し込みの既定・`values.json`・1 回だけ組むこと・決められない工程・リファクタリングの既定・プロンプト |
| `plugins/ndf/tests/core/test_worker_outcome.py`（新設）・`tests/core/plan/test_steps.py`・`test_runner.py` | `outputs` の受け渡しと `gaps` |

## タスク分解

不具合の再現のテスト（工程「要求と受け入れ条件」を起動すると差し込み `worker` で止まる）は Task 10 の最初のステップで書く。`worker` の既定を入れると止まっていた 17 工程が一斉に走り出す（設計書の「危険」）ため、既定を入れるのはプロンプトと出力の受け渡しがそろった後の最後の単位にする。再現のテストを先の単位に置くと、その単位が単独でテストを通らない。

### Task 1: 実測 — `ndf answer work` の今の振る舞いと、組み直しの起動の引数

- **対象ファイル:** 無し（結果を `3908-u1-kinds` の Pull Request に残す）
- **変更内容:** 一時ディレクトリを保存先にして、プロンプトのファイルが無い状態で `ndf answer work --state-dir <一時ディレクトリ> --cwd <一時ディレクトリ> requirements` を打ち、終了コードと結果 JSON の形を残す（設計書の「未確認のまま残ること」の 2 行目）。差し込み `rebuild`（`params.json` の「後ろに --out と結果のファイルを足して打つ」）を使うテンプレートと、組み直しの起動が受ける引数を `--help` で確かめ、`refactor` と同じ既定の起動を持たせられるかの材料を残す（同 3 行目。既定を足すかは設計の範囲の外なので、ここでは決めずに結果だけを残す）
- **満たす受け入れ条件:** 無し（Task 5・Task 10 の前提）
- **進め方:** テスト駆動を適用しない（今の振る舞いの記録）

### Task 2: 退行テスト — 工程「クリーンアップ」の引数

- **対象ファイル:** `plugins/ndf/tests/progress/test_sprint_templates.py`
- **変更内容:** `merged.json` と `fix.json` のテンプレートを組んだ argv が今と同じであることを縛る
- **満たす受け入れ条件:** 5
- **進め方:** 今の振る舞いで通るテストを書く（退行テスト）。範囲のテストは `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/progress/test_sprint_templates.py -q` で打つ

### Task 3: 作業ごとの定義（`kinds.json` と `worker_kinds.py`）

- **対象ファイル:** `plugins/ndf/contracts/worker/kinds.json`（新設）、`plugins/ndf/src/ndf/progress/worker_kinds.py`（新設）、`plugins/ndf/src/ndf/progress/gate/entry.py`（`WORK_KINDS` を消す）、`plugins/ndf/src/ndf/progress/gate/__init__.py`、`plugins/ndf/tests/progress/gate/test_gate_work.py`
- **変更内容:** 18 件の定義（`work`・`summary`・`skills`・`excerpt`・`inputs`・`cwd`・`outputs`）と 4 つの関数。`gate/__init__.py` は地図に無いが、詳細設計の「作業ごとの定義」が `WORK_KINDS` の再公開（73・84 行）を `work_kinds` へ置き換えると定めるので足す
- **満たす受け入れ条件:** 1・2（決定 3）
- **進め方:** 失敗するテスト（キーの集合がテンプレートの `{{worker}}` の後ろの名前の集合と一致する・`cwd` が `worktree` の作業を使うテンプレートは `worktree` を持つ・`kind_gaps` の 3 通り）→ 通す最小実装 → 整理

### Task 4: 差し込みを 1 回だけ組み、決められなければ `started` の前に返す

- **対象ファイル:** `plugins/ndf/src/ndf/progress/invoke.py`（`PreparedPlan`・`KindInputsMissing`・`prepare_plan`・`plan_failure`・`write_values`・`read_values`・`clear_values`・`plan_path`・`_call_plan`）、`plugins/ndf/src/ndf/progress/run.py`（`step_dir`・`_start`・`_run_step`）、`plugins/ndf/tests/progress/test_invoke.py`・`test_run.py`
- **変更内容:** 詳細設計の「差し込みの値の受け渡し」と「差し込みを 1 回だけ組み、決められなければ起動の前に返す」の 2 節。試行の始まりに前の試行の `values.json` を消す
- **満たす受け入れ条件:** 3（起票者の条件 S1）
- **進め方:** 失敗するテスト（決められない工程で `started` も `failed` も増えない・`items` に名前と出所・`template_values` の呼び出しが 1 回・打ち直しで設定を取り直す・一時障害で `transient`）→ 通す最小実装 → 整理。`plan_failure` は `_call_plan` の分類を移すだけなので、移す前に今の分類を縛るテスト（6 つの例外の型）を先に置く

### Task 5: プロンプトを組んで worker を起動する（`ndf answer work`）

- **対象ファイル:** `plugins/ndf/contracts/worker/prompt.md`（新設）、`plugins/ndf/src/ndf/progress/gate/entry.py`、`plugins/ndf/tests/progress/gate/test_gate_work.py`、`plugins/ndf/docs/contracts/commands.md`（生成物）
- **変更内容:** `work_prompt`・`_write_prompt`・`work_argv`・`work_parser`・`_cmd_work`。パスは `ndf.contracts.worker_paths.report_paths`、書き込みは `open_workdir`・`write_file`（どちらも #3877 の `3877-u1-paths`）
- **満たす受け入れ条件:** 1（決定 1・2・6）
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の 3 行。置き換えは差し替えた `exec_` で確かめる。`repo` と `worktree` が違う値で `--cwd` を確かめる）→ 通す最小実装 → 整理。`ndf check --write-spec` で生成物を作り直す

### Task 6: 作業が作った出力をプランの結果へ渡す

- **対象ファイル:** `plugins/ndf/src/ndf/core/worker.py`（`outcome_of`・`_add_run`・`_cmd_run`）、`plugins/ndf/src/ndf/core/plan/steps.py`・`transition_decide.py`・`runner.py`、`plugins/ndf/tests/core/test_worker_outcome.py`（新設）、`plugins/ndf/tests/core/plan/test_steps.py`・`test_runner.py`、`plugins/ndf/tests/progress/test_sprint_templates.py`
- **変更内容:** 結果ファイルの `outputs` を検査してから `ndf worker run` の結果 JSON へコピーする。`--require-output` の欠けを `incomplete` の `gaps` にし、プランの停止結果まで渡す。`outcome_of` の引数は `3877-u3-run` が足した `report` の後ろに `required` を足す
- **満たす受け入れ条件:** 1（決定 1 の出力の受け渡し）
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の 3 行）→ 通す最小実装 → 整理

### Task 7: 課題が 2 件以上の工程の差し込み（代表の課題と `acceptance`）

- **対象ファイル:** `plugins/ndf/src/ndf/progress/invoke.py`（`_issue_values`）、`plugins/ndf/src/ndf/core/route/__init__.py`（`launch_values`）、`plugins/ndf/tests/progress/test_invoke.py`
- **変更内容:** 設計書の決定 7 のとおり、課題が 2 件以上なら番号の最も小さい課題から `title`・`body_file`・`issue` を取り、`acceptance` を課題ごとの `--issue` の並びにする。詳細設計書にはこの 2 つの関数の行が無いので、関数は設計書の決定 7 と今のコード（`invoke.py:387`・`route/__init__.py:486`）から決める
- **満たす受け入れ条件:** 2
- **進め方:** 失敗するテスト（課題 1 件で今までどおり・2 件で代表の課題と `--issue` 2 つ・`check_acceptance` が通る）→ 通す最小実装 → 整理

### Task 8: リファクタリングの既定の起動（`refactor`）

- **対象ファイル:** `plugins/ndf/src/ndf/core/route/__init__.py`（`REFACTOR_WITH_PR`・`refactor_launch`）、`plugins/ndf/src/ndf/core/branch_strategy.py`（`of_project`）、`plugins/ndf/src/ndf/progress/command.py`（`base_ref`・`_sprint_range`）、`plugins/ndf/src/ndf/progress/invoke.py`（`_fill_refactor`）、`plugins/ndf/tests/progress/test_invoke_refactor.py`
- **変更内容:** 詳細設計の「リファクタリングの既定の起動」の節
- **満たす受け入れ条件:** 2
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の 4 行）→ 通す最小実装 → 整理。`REFACTOR_WITH_PR` の本文は子のプロセスのテストを足さず、Task 11 で工程「リファクタリング」を 1 回流して確かめる

### Task 9: 出力の値の取り場所（`x-source`）

- **対象ファイル:** `plugins/ndf/contracts/workflow/context.schema.json`、`plugins/ndf/src/ndf/progress/definition.py`（`context_source`・`_missing_keys`）、`plugins/ndf/src/ndf/progress/command.py`（`_cmd_check`）、`plugins/ndf/tests/progress/test_definition.py`・`test_command_check.py`
- **変更内容:** キーごとの `x-source` と、欠けたキーの行への取り場所の添え書き。2 つのテストのファイルは地図に無いが、`definition.py` と `ndf progress check` を覆う既存のテストがここにあるので足す
- **満たす受け入れ条件:** 4（起票者の条件 S2）
- **進め方:** 失敗するテスト（`delivery`・`mvv_gate` で取り場所が返る・`items` に載る）→ 通す最小実装 → 整理

### Task 10: 差し込み `worker` の既定を入れる

- **対象ファイル:** `plugins/ndf/contracts/templates/params.json`、`plugins/ndf/tests/progress/test_sprint_templates.py`
- **変更内容:** `"default": ["ndf", "answer", "work", "--state-dir", "{state_dir}"]` と `summary` の書き換え
- **満たす受け入れ条件:** 1・5
- **進め方:** 最初に再現のテスト（`standard` のステートマシンで工程「要求と受け入れ条件」を起動すると、今は `worker` の差し込みで止まる）→ 既定を足す → Task 2 の退行テストが通り続けることを確かめる

### Task 11: 工程を 1 つずつ通して確かめる

- **対象ファイル:** `plugins/ndf/contracts/worker/kinds.json`・`plugins/ndf/contracts/worker/prompt.md`（足りなかった項目を足すときだけ）
- **変更内容:** 設計書の「危険」と「未確認のまま残ること」の 1 行目のとおり、使い捨てのステートマシンで工程を 1 つずつ（まず要求と受け入れ条件）`ndf progress next` で起動し、worker が組んだプロンプトだけで作業を終えるかを見る。足りなかった項目は `kinds.json` の行とテンプレートの文で足す。工程「リファクタリング」は名前に空白と日本語を含むファイルを差分に入れて 1 回流し、`--scope` に 1 ファイル 1 引数で渡ったことを残す。工程ごとの結果（起動の成否・足した項目）を Pull Request に表で残す
- **満たす受け入れ条件:** 1・2
- **進め方:** テスト駆動を適用しない（実際の worker を起動する確認で、プロンプトの中身の過不足は流してみるまで分からない）

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| `3908-u1-kinds` | Task 1・2・3 | 3908:5 | 約 450 | （実装の後に書き足す） |
| `3908-u2-values` | Task 4 | 3908:3 | 約 470 | （実装の後に書き足す） |
| `3908-u3-prompt` | Task 5 | 3908:1 | 約 320 | （実装の後に書き足す） |
| `3908-u4-outputs` | Task 6 | 3908:1 | 約 280 | （実装の後に書き足す） |
| `3908-u5-issues` | Task 7・8 | 3908:2 | 約 320 | （実装の後に書き足す） |
| `3908-u6-source` | Task 9 | 3908:4 | 約 130 | （実装の後に書き足す） |
| `3908-u7-default` | Task 10・11 | 3908:1・3908:2・3908:5 | 約 120 | （実装の後に書き足す） |

行数は追加と削除の和（テストを含む）で、`plugins/ndf/docs/contracts/commands.md` と `uv.lock` は数えない。課題の見込みの和が約 2,090 行で 1,000 行を超えるため、依存の向き（作業の定義 → 差し込みの受け渡し → プロンプト → 出力の受け渡し → 既定）で分けた。`3908-u1-kinds` の受け入れ条件 3908:5 は退行テストで、`3908-u7-default` で既定を入れた後も同じテストで確かめる。

単位の順序: `3908-u1-kinds` と `3908-u2-values` は最初の波で並列に流してよい。`3908-u3-prompt` は `3877-u1-paths`・`3908-u1-kinds`（`entry.py`）・`3908-u2-values` の後。`3908-u4-outputs` は `3877-u3-run` の後（`core/worker.py`）。`3908-u5-issues` は `3908-u2-values` の後（`invoke.py`）、`3908-u6-source` は `3908-u5-issues` の後（`command.py`）。`3908-u7-default` はほかの 6 単位の後に最後に流す。全体の順序は [issue-3877-plan.md](./issue-3877-plan.md) の「レビューの単位」の表の下にある。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `progress/run.py` は 989 行で触るのは 11 タスク中 1 タスク（Task 4）、`progress/invoke.py` は 816 行で 3 タスク（Task 4・7・8）、`progress/command.py` は 932 行で 2 タスク（Task 8・9）。テストは `test_invoke.py` に 65 件、`test_run.py` に 54 件ある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `progress/invoke.py`（`3908-u2-values` と `3908-u5-issues`）・`progress/command.py`（`3908-u5-issues` と `3908-u6-source`）・`core/worker.py` の `outcome_of`（`3877-u3-run` と `3908-u4-outputs`）。`KindInputsMissing` と `TemplateValuesMissing` の同じ形の `items` | 複数の単位が同じファイルと同じ関数を順に変える。欠けの名前と出所を返す同じ形の例外が 2 つ並ぶ |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `worker` の既定で 17 工程が一斉に走り出し、プロンプトの情報が足りない worker が意図しない作業をする | 既定は最後の単位（`3908-u7-default`）で入れ、Task 11 で工程を 1 つずつ通す。リリースの後に起きたら、conductor がその工程を `ndf progress` で止めて手で閉じる経路が残る |
| `invoke.py` と `core/plan/runner.py` を並行するスプリント（#3905・#4229）も触る | 単位ごとに `main` を取り込んでから Pull Request を出し、衝突はその単位の中で直す |
| Task 1 で `ndf answer work` の今の終了コードが設計の想定と違う | 結果を Pull Request に残し、`_cmd_work` の止まり方（詳細設計の異常系の処理）と食い違えば `結果: 判断が要る` で supervisor へ返す |

## 切り戻し手順

- 全体を今の振る舞い（起動の前に止まる）へ戻すときは、`params.json` の `worker` の既定を外す 1 行の revert で足りる（設計書の「危険」）
- 単位ごとの Pull Request の revert でも戻る。`values.json` は保存先の一時ファイルで、データ移行は無い

## 完了の定義

- [ ] 受け入れ条件 1〜5 を満たし、条件ごとに検証手段（テスト関数の名前）と CI の結果（`gh pr checks <PR>`）が対応している
- [ ] Task 1 の実測の結果と、Task 11 の工程ごとの結果を Pull Request に残している
- [ ] リリース後テストで、このスプリントのステートマシン（`sprint:2cea9b46d998:p9-lsplit-r2-55c601e023a9`）の工程がプランで流れることを確かめる（要求仕様の検証手段）
- [ ] `standard` のモードの検証（単位ごとの cross-review の収束・CI の必須のチェック）を通している（quality-gates）
