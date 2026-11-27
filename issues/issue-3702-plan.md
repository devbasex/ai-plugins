# #3702: 実装計画を設計 PR に載せ、単位の実行順と大きさを機械で確かめる

## 関連リンク

- 要求仕様: [issue-3702-requirements.md](./issue-3702-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/3702 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3702-design.md](./issue-3702-design.md)・詳細設計書: [issue-3702-design-detail.md](./issue-3702-design-detail.md)
- 単位の SSOT: [issue-3702-plan.units.json](./issue-3702-plan.units.json)
  - 下の「レビューの単位」と「並列と直列」の表は、この変更の実装の後は `ndf plan units render` が作る
  - 今はコマンドが無いので、同じ規則（U1・U3・U4、実行順、行数）を確かめるスクリプトで作った
- モード: `standard`（公開インタフェース（コマンド・宣言・工程の並び）が変わり、関数を新設する）

## 着手の前提

https://github.com/devbasex/ai-plugins/issues/4369 の実装が `main` に入ってから始める（設計の決定 2）。2026-11-27 の時点では入っていない（詳細設計の PR https://github.com/devbasex/ai-plugins/pull/4567 がレビュー中）。始めるときに `origin/main` を取り込み、詳細設計の「#4369 の」関数の名前と引数を実物に合わせる。

## 修正対象

詳細設計書の「地図」の表のとおり。テストと手順書は下の各タスクの対象ファイルに挙げる。パスはリポジトリの根からの相対である。

## タスク分解

依存の順に並べる。最初のタスクは、今の振る舞いを縛る退行テストを含む。

### Task 1: 今の工程の並びを縛る退行テストと、単位のファイルの形・上限・設計の結果の読み手
- **対象ファイル:**
  - `plugins/ndf/contracts/workflow/review-units.schema.json`
  - `plugins/ndf/contracts/schemas/project-workflow.schema.json`
  - `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json`
  - `plugins/ndf/src/ndf/core/plan/inputs_decide.py`
  - `plugins/ndf/tests/core/plan/test_inputs.py`
  - `plugins/ndf/tests/progress/test_definition.py`
- **変更内容:**
  - 先に、今の工程の並び（モードごとの流れるステップと設計承認ゲートの位置）を縛る退行テストを足す
  - そのあとで、次の 3 つを足す。単位のファイルのスキーマ、宣言 `workflow.review_units`、「設計の結果」の表の扱いと統合先の読み手 `design_handlings`
  - 上限の 2 つの設定（`contracts/limits/review-units.*.json`）は Task 6 で読み手 `limits_of` と一緒に足す。設定だけを先に足すと、読み手の無い上限の設定として `ndf check` の規則 `limit-readers` が落ちるためである。この単位のブランチに先に足した 2 つのファイルは消す
  - 呼び出し元の無い `design_gaps` とそのテストを消す
- **満たす受け入れ条件:** 無し（条件 6 の上限の設定は Task 6 で満たす）
- **進め方:** 退行テストは今のまま通る形で足す。ほかは失敗するテスト → 通す最小実装 → 整理

### Task 2: 実行順と確かめの規則
- **対象ファイル:**
  - `plugins/ndf/src/ndf/core/plan/units_decide.py`
  - `plugins/ndf/tests/core/plan/test_units_decide.py`
- **変更内容:** 詳細設計の「単位の判定」の型と `parse`・`judge`（U1〜U6・W1、実行順、導く値、マージの順）・`single`。400・401・1,000・1,001 行と、16,757 行・24,008 行の単位を入力にする
- **満たす受け入れ条件:** 2・3・5・7
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 分け方の案と #3698・#3247・#3672 の入力
- **対象ファイル:**
  - `plugins/ndf/src/ndf/core/plan/units_decide.py`
  - `plugins/ndf/tests/core/plan/test_units_decide.py`
  - `plugins/ndf/tests/core/plan/fixtures/units/issue-3672.units.json`
- **変更内容:** `split` と `judge` の `proposals`。#3672 の詳細設計の「ファイル配置」の表からタスクを作った 1 単位の fixture を、設計書の「テスト設計」の表の下の作り方（群の分け方・依存・行数の根拠）で作り、案が上限に収まる複数の単位になり、依存の向きの順になることを縛る（先に要る: Task 2）
- **満たす受け入れ条件:** 5・7・8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: cross-review が比較元をマージ先に合わせて書き出す
- **対象ファイル:**
  - `plugins/ndf/src/ndf/domains/crossreview/cli.py`
  - `plugins/ndf/src/ndf/domains/crossreview/drive.py`
  - `plugins/ndf/src/ndf/domains/crossreview/settings.py`
  - `plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`
  - `plugins/ndf/docs/contracts/commands.md`
- **変更内容:** `--reviewed-base-out` と `RunConfig.follow_base`。ラウンドの記録の `base_branch`、マージ先が変わったときの全差分。コマンドの一覧を生成し直す
- **満たす受け入れ条件:** 無し（条件 1 の部品。条件 1 は Task 18 の単位で確かめる）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: マージ済みの PR も承認と照らす指定と、REST へ読み替えない読み取り
- **対象ファイル:**
  - `plugins/ndf/src/ndf/ci_cli.py`
  - `plugins/ndf/src/ndf/adapters/github.py`
  - `plugins/ndf/tests/adapters/test_ci_cli.py`
  - `plugins/ndf/tests/adapters/test_github.py`
  - `plugins/ndf/docs/contracts/commands.md`
- **変更内容:** `ndf ci merge --verify-merged`・`ndf ci watch --head` と `GitHub.pulls` の `rest_fallback`。指定の無い呼び出しの振る舞いは変えない。コマンドの一覧を生成し直す
- **満たす受け入れ条件:** 無し（条件 1 の部品。条件 1 は Task 18 の単位で確かめる）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 6: 単位のファイルの読み書きと欠落の確かめ
- **対象ファイル:**
  - `plugins/ndf/src/ndf/core/plan/units_files.py`
  - `plugins/ndf/tests/core/plan/test_units_files.py`
  - `plugins/ndf/contracts/limits/review-units.target-lines.json`
  - `plugins/ndf/contracts/limits/review-units.max-lines.json`
- **変更内容:** 詳細設計の「単位のファイルの読み書き」の `find`・`load`・`limits_of`・`collect`・`check`・`render_tables`・`differs`。上限の 2 つの設定（既定 400 行・1,000 行）を読み手 `limits_of` と一緒に足す（先に要る: Task 1・2）
- **満たす受け入れ条件:** 3・4・6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 7: ndf plan units render・check
- **対象ファイル:**
  - `plugins/ndf/src/ndf/core/plan/cli.py`
  - `plugins/ndf/src/ndf/core/plan/units_files.py`
  - `plugins/ndf/tests/core/plan/test_units_files.py`
  - `plugins/ndf/contracts/domains/env.json`
  - `plugins/ndf/docs/contracts/env.md`
  - `plugins/ndf/docs/contracts/commands.md`
- **変更内容:** `ndf plan units render`・`check`。`NDF_STAGE_INPUT` と `NDF_DESIGN_RECORD_DIR` を受ける形と `checked.json`。環境変数の一覧に `NDF_DESIGN_RECORD_DIR`・`NDF_MACHINE_ID` を足し、2 つの文書を生成し直す（先に要る: Task 3・6）
- **満たす受け入れ条件:** 2・3・5・6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 8: 承認の記録を組み、比べる
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/design_record.py`
  - `plugins/ndf/tests/progress/test_design_record.py`
- **変更内容:** `progress/design_record.py` の `required`・`build`・`stale`・`reset_drafts`・`approved_of`・`mismatches`・`without`・`converted`・`reapproved_of`。まだどこからも呼ばない（先に要る: Task 6）
- **満たす受け入れ条件:** 無し（条件 1 の部品。条件 1 は Task 18 の単位で確かめる）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 9: 承認ゲートと承認の記録をつなぐ
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/gate/entry.py`
  - `plugins/ndf/src/ndf/progress/gate/answering.py`
  - `plugins/ndf/src/ndf/progress/run.py`
  - `plugins/ndf/src/ndf/progress/folding.py`
  - `plugins/ndf/src/ndf/progress/invoke.py`
  - `plugins/ndf/tests/progress/gate/test_gate_design_record.py`
- **変更内容:** 詳細設計の「承認ゲートとのつなぎ」の表の 7 か所（変換の再承認の記録を回答に添える分を含む）と、課題を外す回答の扱い。`required` が偽の定義では今の振る舞いのまま（先に要る: Task 8）
- **満たす受け入れ条件:** 無し（条件 1 の部品。条件 1 は Task 18 の単位で確かめる）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 10: 単位のファイルから流し、承認と照らす
- **対象ファイル:**
  - `plugins/ndf/src/ndf/core/plan/review_units_decide.py`
  - `plugins/ndf/src/ndf/progress/units_invoke.py`
  - `plugins/ndf/tests/progress/test_units_invoke.py`
- **変更内容:** `Unit` の欄、`lineup_from_files`、`prepare` の「始める前」の 1〜5 と、6 の実装計画を省いた課題を課題ごとの単位にする分岐、`unit.md` の担当タスクの本文、代表の課題。分け方の承認が要らず単位のファイルの無いスプリントは、#4369 の表の経路のままにする（Task 16 が変換に置き換える）（先に要る: Task 6・8）
- **満たす受け入れ条件:** 3・4
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 11: 実装を流すコマンド（ndf progress units run）
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/units_cli.py`
  - `plugins/ndf/src/ndf/progress/command.py`
  - `plugins/ndf/src/ndf/progress/run.py`
  - `plugins/ndf/tests/progress/test_units_invoke.py`
  - `plugins/ndf/docs/contracts/commands.md`
- **変更内容:** `ndf progress units run` を `units_cli.py` に置き、`command.py` に登録する。詳細設計の「実装を流す手順」のとおり、工程「実装」だけを `next` と同じ処理で流し、同じイベントを書く。`run.py` の 2 つの関数を公開名にする。失敗の後の打ち直し・再承認の待ち・起動直後の作業・偽の開始条件で実装が起動しないことをテストする。コマンドの一覧を生成し直す（先に要る: Task 9・10）
- **満たす受け入れ条件:** 3
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 12: 依存の順に並列で流す
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/units_invoke.py`
  - `plugins/ndf/tests/progress/test_units_parallel.py`
- **変更内容:** `run_stage` を `graphlib` と `ThreadPoolExecutor` の形にする。本数の上限、マージを 1 つずつ、依存する単位だけを止める失敗、利用上限（先に要る: Task 10）
- **満たす受け入れ条件:** 3
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。時計と待ちは差し替える

### Task 13: マージキューの待ちの状態を読み、後の単位を始める
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/units_queue.py`
  - `plugins/ndf/src/ndf/progress/units_invoke.py`
  - `plugins/ndf/tests/progress/test_units_parallel.py`
- **変更内容:** `units_queue.read`・`action` と、`run_stage` の待ちの一覧、打ち直しの前の状態の読み取り。マージ済みでも作業場所へ取り込めていない単位の「取り込み待ち」の一覧（`units/ingest.json`）と、取り込み待ちのマージのコミットを 1 回でまとめて渡す `advance`。取り込み待ちの期限が最初に分かった時刻から数えられ、打ち直しても数え直さないことをテストで縛る（先に要る: Task 5・12）
- **満たす受け入れ条件:** 3
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 14: 実装のプランの最初の確かめ（guard）
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/units_guard.py`
  - `plugins/ndf/src/ndf/progress/units_cli.py`
  - `plugins/ndf/src/ndf/progress/units_invoke.py`
  - `plugins/ndf/src/ndf/progress/store.py`
  - `plugins/ndf/src/ndf/progress/invoke.py`
  - `plugins/ndf/src/ndf/core/plan/queue_decide.py`
  - `plugins/ndf/contracts/templates/impl.json`
  - `plugins/ndf/tests/progress/test_units_guard.py`
  - `plugins/ndf/docs/contracts/commands.md`
- **変更内容:**（先に要る: Task 11・13）
  - `units_guard`・`store.read_machines`・`ndf progress units guard`
  - テンプレート `impl` の `guard`
  - `RESUME_ALWAYS` と `plan_start`
  - 起動の記録
  - 承認が要らないスプリントと単発の実装は通る
  - `guard` はこの単位のマージの時点から働くので、今の経路（単発の実装・保存した状態にあるスプリント宛ての手で組んだプラン）が通ることをテストで縛る
- **満たす受け入れ条件:** 3
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 20: 送り先の URL の読み取り
Task 15 が使うので、番号の順でなくここに置く。

- **対象ファイル:**
  - `plugins/ndf/src/ndf/adapters/git.py`
  - `plugins/ndf/tests/adapters/test_git.py`
- **変更内容:** `github_target`・`Git.push_urls`・`Git.rewrites_again`。別ホストで所有者名とリポジトリ名が同じ URL、解析できない形、認証情報を含む URL、もう一度書き換わる設定をテストで縛る。`repo_of_url` とその呼び手は変えない
- **満たす受け入れ条件:** 無し（条件 1 の部品。条件 1 は Task 18 の単位で確かめる）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 15: 送り先を確かめて承認した文書を取り込む（sync）
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/units_sync.py`
  - `plugins/ndf/src/ndf/progress/units_cli.py`
  - `plugins/ndf/tests/progress/test_units_sync.py`
  - `plugins/ndf/docs/contracts/commands.md`
- **変更内容:** `units_sync.push_url`・`destination_errors`・`outgoing_errors`・`sync` と `ndf progress units sync`。取得と push は照合した URL を相手にする。送り先が違うときと URL を拒否するときにコミットも push も起きないことを、偽の ssh で受ける手元のリモートの ref で縛る（先に要る: Task 8・11・20）
- **満たす受け入れ条件:** 無し（条件 1 の部品。条件 1 は Task 18 の単位で確かめる）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 16: この変更より前のスプリントの変換
- **対象ファイル:**
  - `plugins/ndf/src/ndf/progress/units_convert.py`
  - `plugins/ndf/src/ndf/progress/units_invoke.py`
  - `plugins/ndf/src/ndf/progress/invoke.py`
  - `plugins/ndf/contracts/worker/kinds.json`
  - `plugins/ndf/contracts/templates/implementation-plan.json`
  - `plugins/ndf/contracts/templates/implementation-plan-convert.json`
  - `plugins/ndf/tests/progress/test_units_convert.py`
- **変更内容:**（先に要る: Task 10・15）
  - `units_convert`
  - `prepare` の 3
  - 反映（送るコミットの作成と照合。送り先と送るコミットの確かめは Task 15 の関数を使う）。担当の起動の前の取得も push も `push_url` を通った URL を相手にし、拒否する URL では通信しないことをテストで縛る
  - `per_unit` の無い定義で単位の欄があるときの分岐
  - push しないテンプレート `implementation-plan-convert`（差し込み `convert`）と、作業の定義の変換のときだけの指示
  - 1 回の変換で担当を起動するのは 2 回まで（競合によるやり直しも数える）。2 回目でも違反か競合があれば、違反の一覧を `convert-violations.json` に書いて止まることをテストで縛る
  - 変換はこの単位のマージの時点から、単位の欄を持つ今のスプリントに働く。単位の欄が無いスプリントが今のまま流れることと、変換の再開の各段階をテストで縛る
- **満たす受け入れ条件:** 3・4
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 17: 工程の出力と判定の入力の受け方と、新しい 3 つのテンプレート
- **対象ファイル:**
  - `plugins/ndf/contracts/templates/design-pr-review.json`
  - `plugins/ndf/contracts/templates/docs-ci.json`
  - `plugins/ndf/contracts/templates/design-merge-approved.json`
  - `plugins/ndf/contracts/templates/params.json`
  - `plugins/ndf/contracts/domains/env.json`
  - `plugins/ndf/src/ndf/progress/definition.py`
  - `plugins/ndf/src/ndf/progress/invoke.py`
  - `plugins/ndf/src/ndf/domains/records/cli.py`
  - `plugins/ndf/src/ndf/domains/records/mvv_gate.py`
  - `plugins/ndf/tests/progress/test_sprint_templates.py`
  - `plugins/ndf/docs/contracts/env.md`
- **変更内容:**（先に要る: Task 4・5・9）
  - `definition._stage_def` の `mvv_gate` の出力と、モードごとの必須の入力
  - `_env_inputs`
  - MVV 判定がレビューした PR を対象にし、判定の直前に head と base を照合する
  - テンプレート `design-pr-review`・`docs-ci`・`design-merge-approved`
  - `invoke` の記録の保存先・指摘のファイル・承認の値・`PRODUCTION_GATE`
  - 工程の定義はまだ呼ばない
- **満たす受け入れ条件:** 無し（条件 1 の部品。条件 1 は Task 18 の単位で確かめる）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 18: 設計のフェーズの工程の並びを切り替える
- **対象ファイル:**
  - `plugins/ndf/contracts/workflow/stages/design.json`
  - `plugins/ndf/contracts/workflow/stages/document-review.json`
  - `plugins/ndf/contracts/workflow/stages/plan.json`
  - `plugins/ndf/contracts/workflow/stages/plan-design.json`
  - `plugins/ndf/contracts/workflow/stages/implement.json`
  - `plugins/ndf/contracts/workflow/modes/standard.json`
  - `plugins/ndf/contracts/workflow/modes/legacy-refactor.json`
  - `plugins/ndf/contracts/workflow/context.schema.json`
  - `plugins/ndf/contracts/worker/kinds.json`
  - `plugins/ndf/contracts/schemas/io/plan.args.schema.json`
  - `plugins/ndf/contracts/schemas/io/plan.result.schema.json`
  - `plugins/ndf/contracts/schemas/io/progress.args.schema.json`
  - `plugins/ndf/contracts/schemas/io/progress.result.schema.json`
  - `plugins/ndf/contracts/schemas/io/ci.args.schema.json`
  - `plugins/ndf/contracts/schemas/io/cross-review.args.schema.json`
  - `plugins/ndf/tests/progress/test_definition.py`
- **変更内容:** 工程の定義 4 つと新設の `plan-design`、2 つのモード、文脈の `plan_omitted`、作業の定義の単位のファイルと `plan_omitted`。入出力の規約を生成し直す。#4420 の条件 1・2 のテストを足す（先に要る: Task 7・14・15・16・17）
- **満たす受け入れ条件:** 1・4
- **進め方:** `standard`・`legacy-refactor` の新しい並びの失敗するテスト → 定義の変更。Task 1 の退行テストのうちほかのモードの分はそのまま通る

### Task 19: 手順書を新しい流れに合わせる
- **対象ファイル:**
  - `plugins/ndf/skills/implementation-plan/SKILL.md`
  - `plugins/ndf/skills/design/SKILL.md`
  - `plugins/ndf/skills/requirements-design/references/document-roles.md`
  - `plugins/ndf/skills/development-workflow/SKILL.md`
  - `plugins/ndf/skills/development-workflow/references/agent-layers.md`
  - `plugins/ndf/skills/development-workflow/references/approval-request.md`
  - `plugins/ndf/skills/development-workflow/references/stage-notes.md`
  - `plugins/ndf/skills/issue-plan-strategy/references/execution-plan.md`
- **変更内容:** 詳細設計の「手順書」の表の 8 ファイル（先に要る: Task 18）
- **満たす受け入れ条件:** 1・2・4
- **進め方:** テスト駆動を適用しない（`.md` の文言を照合するテストは書かない）。`ndf check` で確かめる

## レビューの単位

見込みは追加と削除の和で、テストと生成する文書を含む。合計は約 6,500 行である。数えないパスは無い。どの単位も 1,000 行以下で、400 行を超えるのは「単位のファイルの読み書き」（410 行。上限の設定を読み手と一緒に足すため）だけである。どの単位も、終えた時点で今の流れが動く。工程の並び（設計承認ゲートの位置）が新しくなるのは「工程の並びの切り替え」からである。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実行順 | 先に要る単位 | マージの順 | 実際の行数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 単位の形と上限 | Task 1 | 無し | 300 | 1 | 無し | 1 | |
| 実行順と規則 | Task 2 | 3702:2・3702:3・3702:5・3702:7 | 390 | 1 | 無し | 2 | |
| 分け方の案 | Task 3 | 3702:5・3702:7・3702:8 | 330 | 2 | 実行順と規則（案は実行順と規則の判定を使う） | 5 | |
| レビューとマージの引数 | Task 4・5 | 無し（条件 1 は「工程の並びの切り替え」で満たす） | 400 | 1 | 無し | 3 | |
| 単位のファイルの読み書き | Task 6 | 3702:3・3702:4・3702:6 | 410 | 2 | 単位の形と上限（読み手が形・上限・設計の結果の読み手を使う）、実行順と規則（読んだファイルを判定へ渡す） | 6 | |
| 単位のコマンド | Task 7 | 3702:2・3702:3・3702:5・3702:6 | 340 | 3 | 分け方の案（check の結果に分け方の案を載せる）、単位のファイルの読み書き（コマンドが読み書きを呼ぶ）、レビューとマージの引数（コマンドの一覧の同じファイルを生成し直す） | 7 | |
| 承認の記録 | Task 8 | 無し（条件 1 は「工程の並びの切り替え」で満たす） | 380 | 3 | 単位のファイルの読み書き（記録の照合が単位のファイルの比較を使う） | 8 | |
| ゲートと記録のつなぎ | Task 9 | 無し（条件 1 は「工程の並びの切り替え」で満たす） | 400 | 4 | 承認の記録（ゲートが記録を組み、比べる） | 9 | |
| 単位のファイルから流す | Task 10 | 3702:3・3702:4 | 340 | 4 | 単位のファイルの読み書き（単位の並びを単位のファイルから読む）、承認の記録（実装の開始が承認の記録と照らす） | 10 | |
| 実装を流すコマンド | Task 11 | 3702:3 | 250 | 5 | ゲートと記録のつなぎ（呼び出し層の同じファイルを変える）、単位のファイルから流す（コマンドが同じ関数を呼ぶ）、単位のコマンド（コマンドの一覧の同じファイルを生成し直す） | 11 | |
| 並列の実行 | Task 12 | 3702:3 | 390 | 5 | 単位のファイルから流す（先に要る単位の情報を持つ並びを並列に流す） | 12 | |
| キューの待ち | Task 13 | 3702:3 | 280 | 6 | 並列の実行（待ちの間も後の単位を並列に始める）、レビューとマージの引数（REST へ読み替えない読み取りを使う） | 13 | |
| 実装のプランの確かめ | Task 14 | 3702:3 | 380 | 7 | 実装を流すコマンド（実装を流すコマンドと同じファイルに guard を足す）、キューの待ち（単位を流す処理に起動の記録を足す）、ゲートと記録のつなぎ（呼び出し層の同じファイルを変える） | 14 | |
| 承認した文書の取り込み | Task 15 | 無し（条件 1 は「工程の並びの切り替え」で満たす） | 390 | 8 | 実装のプランの確かめ（guard と同じコマンドのファイルに足す）、承認の記録（承認した対象を記録から読む）、送り先の URL の読み取り（送り先の確かめが URL の読み手を使う） | 15 | |
| 前のスプリントの変換 | Task 16 | 3702:3・3702:4 | 400 | 9 | 実装のプランの確かめ（単位を流す処理と呼び出し層の同じファイルを変える）、承認した文書の取り込み（反映が送り先と送るコミットの確かめを使う） | 16 | |
| テンプレートと判定の入力 | Task 17 | 無し（条件 1 は「工程の並びの切り替え」で満たす） | 390 | 10 | 前のスプリントの変換（呼び出し層の同じファイルを変える）、単位のコマンド（環境変数の一覧の同じファイルを変える）、ゲートと記録のつなぎ（テンプレートが記録の保存先へ書く） | 17 | |
| 工程の並びの切り替え | Task 18 | 3702:1・3702:4 | 320 | 11 | テンプレートと判定の入力（新しい工程が新しいテンプレートを呼ぶ）、承認した文書の取り込み（新しい工程のステップが sync を打つ） | 18 | |
| 手順書 | Task 19 | 3702:1・3702:2・3702:4 | 300 | 12 | 工程の並びの切り替え（書く流れは切り替えた後の工程である） | 19 | |
| 送り先の URL の読み取り | Task 20 | 無し（条件 1 は「工程の並びの切り替え」で満たす） | 130 | 1 | 無し | 4 | |

ただし次の 3 つは、切り替えより前のマージの時点から今のスプリントに働く。それぞれのタスクで、今の経路が通ることをテストする。

| 単位 | 先に働くもの | 当たる条件 |
| --- | --- | --- |
| 単位のファイルから流す | 単位のファイルがあれば、単位の並びをそこから読む | 実装計画に単位のファイルがあるスプリント |
| 実装のプランの確かめ | テンプレート `impl` の `guard` | すべての実装のプラン。保存した状態を読めないときと、保存した状態に無いスプリントブランチ宛てのときは止まる |
| 前のスプリントの変換 | 単位のファイルへの変換と、必要なら再承認 | 実装計画に単位の欄を持つ、切り替えより前のスプリント |

## 並列と直列

直列に残す理由のうち「同じファイル」は、依存の無い 2 つの単位が同じファイルを触ると衝突するためである（規則 U4）。

| 実行順 | この実行順の単位 | 依存で前後が決まらず、並列に流れる単位 | 直列に残す依存と理由 |
| --- | --- | --- | --- |
| 1 | 単位の形と上限、実行順と規則、レビューとマージの引数、送り先の URL の読み取り | 単位の形と上限: 実行順と規則、分け方の案、レビューとマージの引数、送り先の URL の読み取り<br>実行順と規則: 単位の形と上限、レビューとマージの引数、送り先の URL の読み取り<br>レビューとマージの引数: 単位の形と上限、実行順と規則、分け方の案、単位のファイルの読み書き、承認の記録、ゲートと記録のつなぎ、単位のファイルから流す、並列の実行、送り先の URL の読み取り<br>送り先の URL の読み取り: 単位の形と上限、実行順と規則、分け方の案、レビューとマージの引数、単位のファイルの読み書き、単位のコマンド、承認の記録、ゲートと記録のつなぎ、単位のファイルから流す、実装を流すコマンド、並列の実行、キューの待ち、実装のプランの確かめ | 無し |
| 2 | 分け方の案、単位のファイルの読み書き | 分け方の案: 単位の形と上限、レビューとマージの引数、単位のファイルの読み書き、承認の記録、ゲートと記録のつなぎ、単位のファイルから流す、並列の実行、キューの待ち、送り先の URL の読み取り<br>単位のファイルの読み書き: 分け方の案、レビューとマージの引数、送り先の URL の読み取り | 実行順と規則 → 分け方の案: 案は実行順と規則の判定を使う<br>単位の形と上限 → 単位のファイルの読み書き: 読み手が形・上限・設計の結果の読み手を使う<br>実行順と規則 → 単位のファイルの読み書き: 読んだファイルを判定へ渡す |
| 3 | 単位のコマンド、承認の記録 | 単位のコマンド: 承認の記録、ゲートと記録のつなぎ、単位のファイルから流す、並列の実行、キューの待ち、送り先の URL の読み取り<br>承認の記録: 分け方の案、レビューとマージの引数、単位のコマンド、送り先の URL の読み取り | 分け方の案 → 単位のコマンド: check の結果に分け方の案を載せる<br>単位のファイルの読み書き → 単位のコマンド: コマンドが読み書きを呼ぶ<br>レビューとマージの引数 → 単位のコマンド: コマンドの一覧の同じファイルを生成し直す<br>単位のファイルの読み書き → 承認の記録: 記録の照合が単位のファイルの比較を使う |
| 4 | ゲートと記録のつなぎ、単位のファイルから流す | ゲートと記録のつなぎ: 分け方の案、レビューとマージの引数、単位のコマンド、単位のファイルから流す、並列の実行、キューの待ち、送り先の URL の読み取り<br>単位のファイルから流す: 分け方の案、レビューとマージの引数、単位のコマンド、ゲートと記録のつなぎ、送り先の URL の読み取り | 承認の記録 → ゲートと記録のつなぎ: ゲートが記録を組み、比べる<br>単位のファイルの読み書き → 単位のファイルから流す: 単位の並びを単位のファイルから読む<br>承認の記録 → 単位のファイルから流す: 実装の開始が承認の記録と照らす |
| 5 | 実装を流すコマンド、並列の実行 | 実装を流すコマンド: 並列の実行、キューの待ち、送り先の URL の読み取り<br>並列の実行: 分け方の案、レビューとマージの引数、単位のコマンド、ゲートと記録のつなぎ、実装を流すコマンド、送り先の URL の読み取り | ゲートと記録のつなぎ → 実装を流すコマンド: 呼び出し層の同じファイルを変える<br>単位のファイルから流す → 実装を流すコマンド: コマンドが同じ関数を呼ぶ<br>単位のコマンド → 実装を流すコマンド: コマンドの一覧の同じファイルを生成し直す<br>単位のファイルから流す → 並列の実行: 先に要る単位の情報を持つ並びを並列に流す |
| 6 | キューの待ち | キューの待ち: 分け方の案、単位のコマンド、ゲートと記録のつなぎ、実装を流すコマンド、送り先の URL の読み取り | 並列の実行 → キューの待ち: 待ちの間も後の単位を並列に始める<br>レビューとマージの引数 → キューの待ち: REST へ読み替えない読み取りを使う |
| 7 | 実装のプランの確かめ | 実装のプランの確かめ: 送り先の URL の読み取り | 実装を流すコマンド → 実装のプランの確かめ: 実装を流すコマンドと同じファイルに guard を足す<br>キューの待ち → 実装のプランの確かめ: 単位を流す処理に起動の記録を足す<br>ゲートと記録のつなぎ → 実装のプランの確かめ: 呼び出し層の同じファイルを変える |
| 8 | 承認した文書の取り込み | 承認した文書の取り込み: 無し | 実装のプランの確かめ → 承認した文書の取り込み: guard と同じコマンドのファイルに足す<br>承認の記録 → 承認した文書の取り込み: 承認した対象を記録から読む<br>送り先の URL の読み取り → 承認した文書の取り込み: 送り先の確かめが URL の読み手を使う |
| 9 | 前のスプリントの変換 | 前のスプリントの変換: 無し | 実装のプランの確かめ → 前のスプリントの変換: 単位を流す処理と呼び出し層の同じファイルを変える<br>承認した文書の取り込み → 前のスプリントの変換: 反映が送り先と送るコミットの確かめを使う |
| 10 | テンプレートと判定の入力 | テンプレートと判定の入力: 無し | 前のスプリントの変換 → テンプレートと判定の入力: 呼び出し層の同じファイルを変える<br>単位のコマンド → テンプレートと判定の入力: 環境変数の一覧の同じファイルを変える<br>ゲートと記録のつなぎ → テンプレートと判定の入力: テンプレートが記録の保存先へ書く |
| 11 | 工程の並びの切り替え | 工程の並びの切り替え: 無し | テンプレートと判定の入力 → 工程の並びの切り替え: 新しい工程が新しいテンプレートを呼ぶ<br>承認した文書の取り込み → 工程の並びの切り替え: 新しい工程のステップが sync を打つ |
| 12 | 手順書 | 手順書: 無し | 工程の並びの切り替え → 手順書: 書く流れは切り替えた後の工程である |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | しない | 行数の上限に近いのは `progress/run.py`（965 行。足すのは 25 行まで）と `progress/command.py`（987 行。足すのは 10 行まで）で、新しい処理は別のファイルに置く。`adapters/github.py`（1,000 行）は #4369 の実装が分ける。分かれていなければ、Task 5 の初めに分ける |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `progress/units_invoke.py`・`units_queue.py`・`units_convert.py`・`units_guard.py` の状態の読み書き、`design_record` と `gate` の対象の比較、`inputs_decide.design_handlings` と `domains/materials/collect.design_rows` の表の読み取り | 複数の単位が同じ形の処理を足す |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #4369 の実装が詳細設計の名前と違う | 着手の前に取り込んで合わせる。単位の分け方が変わるなら、承認を取り直す |
| 承認ゲートの位置の移動で、進行中のスプリントが止まる | 保存した定義の並びのまま流れる（決定 10）。「前のスプリントの変換」のテストで縛る |
| このスプリント自身は新しい流れを使えない | 今の supervisor の手順（単位ごとのプラン）で回す |
| `tests/progress/test_definition.py` などの大きいテストのファイルが 1,000 行を超える | 超える前にファイルを分ける |

## 切り戻し手順

- 単位の PR を新しい順に打ち消す。新しい流れは「工程の並びの切り替え」を打ち消すと、以後に作るスプリントが今の並びに戻る
- 切り替えの後に作ったスプリントは、保存した定義が新しい並びを持つ。打ち消す前に終えるか中止する
- データの移行は無い

## 完了の定義

- [ ] 受け入れ条件 1〜8 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `plugins/ndf/bin/ndf check` が通り、PR の CI のテストが通る
- [ ] 要求の「リリース後の確認の計画」の 4 項目を、リリース後テストへ送っている
