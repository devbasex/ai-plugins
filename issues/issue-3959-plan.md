# #3959 #4055: MVV 判定を進め方によらず打ち、レビューの保留・根拠の行の確かめ・プランの外のエントリポイント・工程の入力の形の確かめを足す

## 関連リンク

- 要求仕様: [issue-3959-requirements.md](./issue-3959-requirements.md)（正は #3959 の本文）。受け入れ条件は番号で指す。条件 6 は #4104 へ移したので無い
- 設計書: [issue-3959-4055-design.md](./issue-3959-4055-design.md)、詳細設計書: [issue-3959-4055-design-detail.md](./issue-3959-4055-design-detail.md)
- 前提にする設計: #4076 の設計（[issue-4076-design.md](./issue-4076-design.md)）と、その直しの #4090・#4099 の設計（[issue-4090-4099-design.md](./issue-4090-4099-design.md)）。単位 2〜4 は #4076 の実装が main へ入った後に始める（要求仕様の前提 1、設計の決定 7）
- モード: `standard`（公開インタフェースの `ndf records mvv gate` の引数と `ndf records mvv cite` を足し、手順書とコントラクトを変える）

## 修正対象

詳細設計書の「地図」と、構成要素ごとの関数の仕様からコピーした。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/contracts/workflow/stage-io.schema.json` | `$defs.stage-in` を文脈のスキーマへの参照（平らな object）にする |
| `plugins/ndf/contracts/workflow/context.schema.json` | `$defs.mvv_gate` に任意のキー `holds`・`noted` |
| `plugins/ndf/src/ndf/progress/definition.py` | `validate_stage_input` が入力の object そのものを確かめる・`stage_input_errors`・`stage_io`（新設） |
| `plugins/ndf/src/ndf/progress/invoke.py` | `resolve_inputs` が `validate_stage_input` で確かめる |
| `plugins/ndf/src/ndf/progress/command.py` | `stage_inputs_of`（新設） |
| `plugins/ndf/src/ndf/progress/model.py` | `MVV_SKIP_GATES`（新設）・`mvv_skip` |
| `plugins/ndf/src/ndf/progress/gate/asking.py` | `mvv_lines`（新設）・`material`（プランの承認資料に「MVV 判定」の節） |
| `plugins/ndf/src/ndf/progress/gate/checks.py` | `MvvFacts.review_holds`・`check_review_holds`・`refusals`・`judgeable`・`may_skip`・`note`・`CHECKS_BY_GATE` |
| `plugins/ndf/src/ndf/domains/records/cli.py` | `--machine`・`_mvv_inputs`・`_env_inputs`・`_mvv_cite` |
| `plugins/ndf/src/ndf/domains/records/mvv_gate.py` | `gate`・`_judged`・`pr_number`・`boundary_paths`・`_recorded`・`snapshot_facts`・`review_prs`・`GATE_STEPS`・`GATE_STAGES`。`base_facts` は消す |
| `plugins/ndf/src/ndf/domains/records/mvv.py` | `validation_rows`・`review_rows`・`mvv_validate_sprint`・`ReviewWrite`・`record_review`・`cite`・`compare_files` |
| `plugins/ndf/src/ndf/domains/records/mvv_decide.py` | `review_holds`（新設） |
| `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | `gate_outcome` |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | `Outcome`・`_summary` |
| `plugins/ndf/src/ndf/domains/crossreview/finish_decide.py` | `finish(record_failed=…)` |
| `plugins/ndf/src/ndf/domains/crossreview/cli.py` | `cmd_run` |
| `plugins/ndf/docs/contracts/commands.md` | `ndf records mvv gate` の `--machine` と入力の形、`ndf records mvv cite` |
| `plugins/ndf/skills/development-workflow/references/pace.md`・`approval-request.md`・`agent-layers.md`・`project-mvv.md` | 詳細設計書の「手順書」の表のとおり |
| `docs/glossary/glossary.json`・`docs/glossary.md` | 語「レビューの保留」の追加と「MVV 判定」の意味の変更（`ndf records glossary render` で作り直す） |

テストは各タスクの対象ファイルに挙げる。

## タスク分解

### Task 1: 今の判定の振る舞いを縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/domains/records/test_mvv_gate.py`、`plugins/ndf/tests/progress/test_invoke.py`
- **変更内容:** 変えない振る舞いを先にテストで縛る。プランが組む平らな入力（`resolve_inputs` の結果）で `ndf records mvv gate` が今の形の `mvv_gate` を返すこと。承認済みの MVV が無いリポジトリで `verdict: null` を返すこと（前提 2）。`fast` で検査を通り「従う」の設計承認ゲートが `skip: true` になること
- **満たす受け入れ条件:** 前提 2（今の振る舞いを壊さない）
- **進め方:** 今のコードで通るテストを書く（機能追加の最初のタスク）

### Task 2: 工程の入力のコントラクトを 1 つにし、判定のエントリポイントで確かめる

- **対象ファイル:** `plugins/ndf/contracts/workflow/stage-io.schema.json`、`plugins/ndf/src/ndf/progress/definition.py`、`plugins/ndf/src/ndf/progress/invoke.py`、`plugins/ndf/src/ndf/domains/records/cli.py`（`_env_inputs`）、`plugins/ndf/src/ndf/domains/records/mvv_gate.py`（`GATE_STEPS`・`GATE_STAGES`）、`plugins/ndf/tests/progress/test_definition.py`、`plugins/ndf/tests/progress/test_invoke.py`、`plugins/ndf/tests/domains/records/test_mvv_gate.py`
- **変更内容:** 詳細設計書の「工程の入力のコントラクト」と、「プランの外のエントリポイント」の `_env_inputs` の手順（`stage_io` で `mode` を使わずに StageDef を作り、`stage_input_errors` のスキーマの誤りを先に `invalid`、キーの不足を `input_missing` にする）。`{"context": {...}}` の入力は `invalid`、形が合い `mode` だけが無い入力は `input_missing` で止め、`gate` を呼ばない
- **満たす受け入れ条件:** 9
- **進め方:** 失敗するテスト（`{"context": {...}}` で `invalid` にならない）→ 通す最小実装 → 整理

### Task 3: ステートマシンの ID から判定の入力を組む

- **対象ファイル:** `plugins/ndf/src/ndf/progress/command.py`（`stage_inputs_of`）、`plugins/ndf/src/ndf/domains/records/cli.py`（`--machine`・`_mvv_inputs`）、`plugins/ndf/docs/contracts/commands.md`、`plugins/ndf/tests/progress/test_command_mvv_inputs.py`（新設）、`plugins/ndf/tests/domains/records/test_mvv_gate.py`
- **変更内容:** 詳細設計書の「プランの外のエントリポイント（`--machine`）」。両方のエントリポイントが同じ入力・同じ PR を選ぶ。`stage_inputs_of` はステートマシンの `repo` を起動元の `repo_id` と照合し、別リポジトリの ID を断る
- **満たす受け入れ条件:** 8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: 判定を打つ条件と省く条件を分け、リリース承認ゲートを省かない

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv_gate.py`、`plugins/ndf/src/ndf/domains/records/mvv_material_decide.py`、`plugins/ndf/src/ndf/domains/records/mvv.py`（`validation_rows`・`mvv_validate_sprint`・`compare_files`）、`plugins/ndf/src/ndf/progress/gate/checks.py`（`judgeable`・`refusals`・`may_skip`・`note`・`CHECKS_BY_GATE`）、`plugins/ndf/src/ndf/progress/gate/asking.py`（`mvv_lines`・`material`）、`plugins/ndf/src/ndf/progress/model.py`、`plugins/ndf/contracts/workflow/context.schema.json`、`plugins/ndf/tests/domains/records/test_mvv_gate.py`、`plugins/ndf/tests/domains/records/test_mvv.py`、`plugins/ndf/tests/progress/gate/test_gate_checks.py`、`plugins/ndf/tests/progress/gate/test_gate_present.py`
- **変更内容:** 詳細設計書の「判定を打つ条件と省く条件」と「リリース承認ゲートの省略の止め」と「手順書」の「プランの経路の承認資料の生成」。MVV とレッドラインのパスをベースから 1 度だけ読み（`fetch_branch_or_raise` で取得し、#4076 の `current` と `Snapshot.commit` を使う）、差分は `snap.commit` と head の組から読む。プランの承認資料（`asking.material`）に今回の判定・`why`・保留の欄を載せる
- **この単位で作る `_judged` の手順 (5) の範囲（保留なしの経路）:** `validation_rows` で記録を 1 回読み、手順 (7) の再利用にだけ渡す（読めなければ再利用を省いて LLM で判定する）。保留は読まず、出力の `holds` は常に `null`（承認資料は「レビューの保留: 未確認」）。`review_rows`・`review_holds`・`review_prs`・`MvvFacts.review_holds`・`check_review_holds` は参照しない（Task 5 が足す）。詳細設計書の手順のうち保留に関わる部分（(5) の保留の算出と「読めない」保留、`holds` の配列、保留による `skip` 偽）は Task 5 で足す
- **満たす受け入れ条件:** 1・2・3（判定と PR のコメント）
- **進め方:** 失敗するテスト（`normal` で LLM のスタブが呼ばれない）→ 通す最小実装 → 整理

### Task 5: レビューの保留を読み、記録の欠落で cross-review を止める

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv.py`（`review_rows`・`ReviewWrite`・`record_review`）、`plugins/ndf/src/ndf/domains/records/mvv_decide.py`、`plugins/ndf/src/ndf/progress/gate/checks.py`（`check_review_holds`・`MvvFacts`）、`plugins/ndf/src/ndf/domains/records/mvv_gate.py`（保留の配線）、`plugins/ndf/src/ndf/domains/crossreview/drive.py`、`plugins/ndf/src/ndf/domains/crossreview/finish_decide.py`、`plugins/ndf/src/ndf/domains/crossreview/cli.py`、`plugins/ndf/tests/domains/records/test_mvv_review_holds.py`（新設）、`plugins/ndf/tests/domains/records/test_mvv_record_review.py`（#4076 の実装が作る。無ければ新設）、`plugins/ndf/tests/domains/crossreview/test_crossreview_finish.py`、`plugins/ndf/tests/progress/gate/test_gate_checks.py`
- **変更内容:** 詳細設計書の「レビューの保留」。`review_holds` は伏せた行（`withheld: true`）の 3 つ目の規則を含む（設計の決定 11）。`record_review` の重ねない判定は詳細設計書に書いた全項目（head・終わり方・版・項目・理由・本文・`withheld`）と伏せた行の 2 つの規則のまま。Task 4 の保留なしの経路に、`_judged` の手順 (5) の `review_prs` → `review_rows` → `review_holds` と記録を読めないときの保留 1 件、`MvvFacts.review_holds`・`check_review_holds` を足し、`holds` を読んだ結果の配列（無ければ `[]`）にする
- **満たす受け入れ条件:** 4・5
- **進め方:** 失敗するテスト（`fast` で保留があっても `skip: true`）→ 通す最小実装 → 整理

### Task 6: 承認資料の根拠の行を確かめるコマンド

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv.py`（`cite`）、`plugins/ndf/src/ndf/domains/records/cli.py`（`_mvv_cite`）、`plugins/ndf/docs/contracts/commands.md`、`plugins/ndf/tests/domains/records/test_mvv_cite.py`（新設）
- **変更内容:** 詳細設計書の「根拠の行の確かめ（`cite`）」。`decision_lines` で 4 要素を作り、`mismatch` の分岐は持たない。結果の `items`・`summary` を組む前に自由記述（`path`・`cited`・`unknown`）を伏せる。`ok` の結果の `summary` と `metrics` を資料に載せる形にする
- **満たす受け入れ条件:** 7（承認資料の側。`decisions.md` の例は #4076 の実装が直す）
- **進め方:** 失敗するテスト（バージョン 1 を引いた行で `refused` にならない）→ 通す最小実装 → 整理

### Task 7: 手順書と用語集

- **対象ファイル:** `plugins/ndf/skills/development-workflow/references/pace.md`、`approval-request.md`、`agent-layers.md`、`project-mvv.md`、`docs/glossary/glossary.json`、`docs/glossary.md`
- **変更内容:** 詳細設計書の「手順書」（supervisor の経路の承認資料の載せ方・「目指す姿の根拠」を決定の見出しの下に写す形・`cite` の `ok`・`refused`・`precondition` の分岐）と、設計書の「用語」の表
- **満たす受け入れ条件:** 1・3・4・7・8（手順の側）
- **進め方:** テスト駆動を適用しない（`.md` の文言を突き合わせるテストは書かない決まり）。`ndf records glossary render --check` と `ndf records glossary meanings` が `ok` になることで確かめる

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 1 工程の入力とプランの外のエントリポイント | Task 1・2・3 | 3959:8・3959:9 | 約 400 | （実装の後に書き足す） |
| 2 判定を打つ条件と省く条件 | Task 4 | 3959:1・3959:2・3959:3 | 約 600 | |
| 3 レビューの保留と記録の欠落 | Task 5 | 3959:4・3959:5 | 約 650 | |
| 4 根拠の行の確かめ | Task 6 | 3959:7 | 約 250 | |
| 5 手順書と用語集 | Task 7 | 3959:1・3959:3・3959:4・3959:7・3959:8 | 約 200 | |

- 行数は追加と削除の和で、`git diff --shortstat <ベース>...HEAD` で数える。数えないパスは無い（ロックファイルと再生成のファイルを触らない。`docs/glossary.md` は再生成だが、数えても 1,000 行を超えない）
- どの単位も `.ndf/` のファイルを触らない
- 実行の順: 単位 1 を最初に流す（#4076 の部品を使わないので、#4076 の実装の前でも始められる）。単位 2〜4 は #4076 の実装が main へ入った後に始める。単位 3 は単位 2 の後（`_judged` の配線と `checks.py` を単位 2 が変える。単位 2 は保留を読まない経路で単体で動き、単位 3 が保留の関数とフィールドを足して配線する。上の Task 4・Task 5 の範囲）。単位 4 は単位 1 の後（`records/cli.py` を単位 1 が変える）で、単位 2・3 と並列にしてよい（`mvv.py` は関数ごとに分かれ、衝突は取り込みで解ける）。単位 5 は単位 1〜4 の後
- バージョンを上げるのは単位 1〜5 と #4076 の実装がすべて main に入った後（設計の決定 7）

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る主なファイルは `mvv_gate.py` 168 行・`mvv.py` 171 行・`checks.py` 202 行・`records/cli.py` 308 行で小さい。大きいのは `crossreview/drive.py` 998 行・`progress/command.py` 932 行・`progress/invoke.py` 816 行だが、どれも関数 1〜2 つを足すか変えるだけで、7 タスクのうちこの 3 ファイルを触るのは 3 つ（Task 2・3・5） |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `records/cli.py` の `_env_inputs` と `progress/command.py` の `stage_inputs_of` の、工程の定義を引く処理の重なり（単位 1）と、`mvv_gate._judged` と `cite` がどちらも `current` を呼ぶ前の取得の手順（単位 2・4） | 単位をまたいで同じ形の処理が 2 か所にできうる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #4076 の実装が #4090・#4099 の直しと違う形で入る | 単位 2 を始める前に、#4076 の部品のシグネチャを詳細設計書の前提と突き合わせ、違えば設計の該当の行を直してから進める |
| 計画 p9 のレーン lz1-contract（#3684）が `plugins/ndf/contracts/`・`plugins/ndf/src/` を同時に触る | 単位 1 を始める前に、そのレーンの触る場所と取り込みの衝突を確かめ、base-sync で解く |
| 単位 2 の後・単位 3 の前の main では、`normal` でも判定を打つがレビューの保留を読まない | バージョンを上げるのは単位 1〜5 の後なので、利用者には届かない（決定 7） |

## 切り戻し手順

- データの移行は無い。単位ごとの PR を revert すれば戻る。単位 3 を戻すときは単位 5 の手順書の保留の行も戻す
- 更新の前にリリース承認ゲートまで進んでいたスプリントは、`ndf progress cancel` してから作り直す（設計の決定 3。実装の PR の `## 移行の手順` に書く）

## 完了の定義

- [ ] 受け入れ条件 1〜5・7〜9 をすべて満たし、条件ごとに検証手段と結果が対応している（1・3 の判定の中身は、リリースの後の最初の `normal` のスプリントの設計承認ゲートで見る）
- [ ] 各単位の PR の CI が通り、`cross-review` が収束している（`.ndf/project.json` の `test.ci.only` が真なので手元でテストを打たない）
