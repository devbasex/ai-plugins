# #3959 #4055: MVV 判定を進め方によらず打ち、レビューの保留・根拠の行の確かめ・プランの外のエントリポイント・工程の入力の形の確かめを足す

## 関連リンク

- 要求仕様: [issue-3959-requirements.md](./issue-3959-requirements.md)（正は #3959 の本文）。受け入れ条件は番号で指す。条件 6 は #4104 へ移したので無い
- 設計書: [issue-3959-4055-design.md](./issue-3959-4055-design.md)、詳細設計書: [issue-3959-4055-design-detail.md](./issue-3959-4055-design-detail.md)
- 前提にする設計: #4076 の設計（[issue-4076-design.md](./issue-4076-design.md)）と、その直しの #4090・#4099 の設計（[issue-4090-4099-design.md](./issue-4090-4099-design.md)）。単位 2〜4 は #4076 の実装が main へ入った後に始める（要求仕様の前提 1、設計の決定 7）
- 前提にする実装計画: [issue-4076-plan.md](./issue-4076-plan.md)。この計画の単位が使う #4076 の部品と、それを作る #4076 の単位は「レビューの単位」の表の後にある
- モード: `standard`（公開インタフェースの `ndf records mvv gate` の引数と `ndf records mvv cite` を足し、手順書とコントラクトを変える）

## 修正対象

詳細設計書の「地図」と、構成要素ごとの関数の仕様からコピーした。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/contracts/workflow/stage-io.schema.json` | `$defs.stage-in` を文脈のスキーマへの参照（平らな object）にする |
| `plugins/ndf/contracts/workflow/context.schema.json` | `$defs.mvv_gate` に任意のキー `holds`・`noted` |
| `plugins/ndf/src/ndf/progress/definition.py` | `validate_stage_input` が入力の object そのものを確かめる・`stage_input_errors`・`stage_io`（新設） |
| `plugins/ndf/src/ndf/progress/invoke.py` | `resolve_inputs` が `validate_stage_input` で確かめる |
| `plugins/ndf/src/ndf/progress/stage_inputs.py`（新設） | `stage_inputs_of`（新設）。詳細設計書の保存先は `progress/command.py` だが、`command.py` は計画 p9 の lsplit のスプリントブランチで 971 行あり、足すと `ndf check` の構造の規則（1,000 行）を超えるので新しいモジュールに置く |
| `plugins/ndf/src/ndf/progress/model.py` | `MVV_SKIP_GATES`・`MVV_GATE_STEPS`（新設）・`mvv_skip` |
| `plugins/ndf/src/ndf/progress/gate/asking.py` | `mvv_lines`（新設）・`material`（プランの承認資料に「MVV 判定」の節） |
| `plugins/ndf/src/ndf/progress/gate/checks.py` | `MvvFacts.review_holds`・`check_review_holds`・`refusals`・`judgeable`・`may_skip`・`note`・`CHECKS_BY_GATE` |
| `plugins/ndf/src/ndf/domains/records/cli.py` | `--machine`・`_mvv_inputs`・`_env_inputs`・`_mvv_cite` |
| `plugins/ndf/src/ndf/domains/records/mvv_gate.py` | `gate`・`_judged`・`pr_number`・`boundary_paths`・`_recorded`・`snapshot_facts`・`review_prs`・`GATE_STEPS`・`GATE_STAGES`。`base_facts` は消す |
| `plugins/ndf/src/ndf/domains/records/mvv.py` | `validation_rows`・`review_rows`・`mvv_validate_sprint`・`ReviewWrite`・`record_review`・`cite`・`compare_files`。`cite` が呼ぶ `decision_lines`・`fenced_lines` は、#4076 の計画の U7 がこのモジュールに置く（判断のモジュールは `ndf.adapters` を読めないため。#4076 の計画の「修正対象」） |
| `plugins/ndf/src/ndf/domains/records/mvv_decide.py` | `review_holds`（新設） |
| `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | `gate_outcome`・`diff_rows`・`DiffError` |
| `plugins/ndf/src/ndf/adapters/publish.py` | `Publisher._private_refs`（秘密を含む参照を照会しない） |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | `Outcome`・`_summary` |
| `plugins/ndf/src/ndf/domains/crossreview/finish_decide.py` | `finish(record_failed=…)` |
| `plugins/ndf/src/ndf/domains/crossreview/journal.py` | `apply_fix`（見送りの区分 `defer` を持つ） |
| `plugins/ndf/contracts/data/review-criteria.json` | 見送りの区分の選び方 |
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

- **対象ファイル:** `plugins/ndf/contracts/workflow/stage-io.schema.json`、`plugins/ndf/src/ndf/progress/definition.py`、`plugins/ndf/src/ndf/progress/invoke.py`、`plugins/ndf/src/ndf/domains/records/cli.py`（`_env_inputs`）、`plugins/ndf/src/ndf/domains/records/mvv_gate.py`（`GATE_STEPS`・`GATE_STAGES`）、`plugins/ndf/src/ndf/progress/model.py`（`MVV_GATE_STEPS`。`GATE_STEPS` と `asking.material` が読む 1 か所の表）、`plugins/ndf/tests/progress/test_definition.py`、`plugins/ndf/tests/progress/test_invoke.py`、`plugins/ndf/tests/domains/records/test_mvv_gate.py`
- **変更内容:** 詳細設計書の「工程の入力のコントラクト」と、「プランの外のエントリポイント」の `_env_inputs` の手順（`stage_io` で `mode` を使わずに StageDef を作り、`stage_input_errors` のスキーマの誤りを先に `invalid`、キーの不足を `input_missing` にする）。`{"context": {...}}` の入力は `invalid`、形が合い `mode` だけが無い入力は `input_missing` で止め、`gate` を呼ばない
- **満たす受け入れ条件:** 9
- **進め方:** 失敗するテスト（`{"context": {...}}` で `invalid` にならない）→ 通す最小実装 → 整理

### Task 3: ステートマシンの ID から判定の入力を組む

- **対象ファイル:** `plugins/ndf/src/ndf/progress/stage_inputs.py`（新設。`stage_inputs_of`。保存先を変えた理由は「修正対象」）、`plugins/ndf/src/ndf/domains/records/cli.py`（`--machine`・`_mvv_inputs`）、`plugins/ndf/docs/contracts/commands.md`、`plugins/ndf/tests/progress/test_command_mvv_inputs.py`（新設）、`plugins/ndf/tests/domains/records/test_mvv_gate.py`
- **変更内容:** 詳細設計書の「プランの外のエントリポイント（`--machine`）」。両方のエントリポイントが同じ入力・同じ PR を選ぶ。そのため `mvv_gate.pr_number` をこのタスクで変え、`design` は文脈の `design_pr` を読まずに `worktree` のブランチの開いた PR を選ぶ（#4076 の部品を使わない変更なので単位 1 に入る）。`stage_inputs_of` はステートマシンの `repo` を起動元の `repo_id` と照合し、別リポジトリの ID を断る
- **満たす受け入れ条件:** 8
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: 判定を打つ条件と省く条件を分け、リリース承認ゲートを省かない

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv_gate.py`、`plugins/ndf/src/ndf/domains/records/mvv_material_decide.py`、`plugins/ndf/src/ndf/domains/records/mvv.py`（`validation_rows`・`mvv_validate_sprint`・`compare_files`）、`plugins/ndf/src/ndf/progress/gate/checks.py`（`judgeable`・`refusals`・`may_skip`・`note`・`CHECKS_BY_GATE`）、`plugins/ndf/src/ndf/progress/model.py`、`plugins/ndf/contracts/workflow/context.schema.json`、`plugins/ndf/tests/domains/records/test_mvv_gate.py`、`plugins/ndf/tests/domains/records/test_mvv.py`、`plugins/ndf/tests/progress/gate/test_gate_checks.py`
- **変更内容:** 詳細設計書の「判定を打つ条件と省く条件」と「リリース承認ゲートの省略の止め」。MVV とレッドラインのパスをベースから 1 度だけ読み（`fetch_branch_or_raise` で取得し、#4076 の `current` と `Snapshot.commit` を使う）、差分は `snap.commit` と head の組から読む（`current`・`Snapshot.commit` は #4076 の計画の U3 が作る）（比較の `files` が 300 件に届いたときだけ、同じ組を手元の git で読み直す）。プランの承認資料に載せる部分は Task 8 が持つ
- **この単位で作る `_judged` の手順 (5) の範囲（保留なしの経路）:** `validation_rows` で記録を 1 回読み、手順 (7) の再利用にだけ渡す（読めなければ再利用を省いて LLM で判定する）。保留は読まず、出力の `holds` は常に `null`（承認資料は「レビューの保留: 未確認」。判定を打てた（`verdict` がある）ときの理由は「この判定は保留を読んでいない」で、途中で止まった扱いにしない。`fast` で検査がすべて通ると `why` は空なので、Task 8 の `mvv_lines` は空の `why` でも資料を組む。詳細設計書の「手順書」の「載せ方」）。`review_rows`・`review_holds`・`review_prs`・`MvvFacts.review_holds`・`check_review_holds` は参照しない（Task 5 が足す）。詳細設計書の手順のうち保留に関わる部分（(5) の保留の算出と「読めない」保留、`holds` の配列、保留による `skip` 偽）は Task 5 で足す
- **満たす受け入れ条件:** 1・2・3（判定と PR のコメント）
- **進め方:** 失敗するテスト（`normal` で LLM のスタブが呼ばれない）→ 通す最小実装 → 整理

### Task 5: レビューの保留を読み、記録の欠落で cross-review を止める

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv.py`（`review_rows`・`ReviewWrite`・`record_review`）、`plugins/ndf/src/ndf/domains/records/mvv_decide.py`、`plugins/ndf/src/ndf/progress/gate/checks.py`（`check_review_holds`・`MvvFacts`）、`plugins/ndf/src/ndf/domains/records/mvv_gate.py`（保留の配線）、`plugins/ndf/src/ndf/domains/crossreview/drive.py`、`plugins/ndf/src/ndf/domains/crossreview/finish_decide.py`、`plugins/ndf/src/ndf/domains/crossreview/journal.py`、`plugins/ndf/contracts/data/review-criteria.json`、`plugins/ndf/tests/domains/crossreview/test_crossreview_journal.py`、`plugins/ndf/src/ndf/domains/crossreview/cli.py`、`plugins/ndf/tests/domains/records/test_mvv_review_holds.py`（新設）、`plugins/ndf/tests/domains/records/test_mvv_record_review.py`（#4076 の計画の U12 が作る。この単位は書き足す）、`plugins/ndf/tests/domains/crossreview/test_crossreview_finish.py`、`plugins/ndf/tests/progress/gate/test_gate_checks.py`
- **変更内容:** 詳細設計書の「レビューの保留」。`review_holds` は、直っていないレッドラインと、見送りの区分が `needs_user`（または区分の無い）見送りだけを保留にし、伏せた記録（`withheld: true`。#4076 の計画の U12 が書く）の 3 つ目の規則を含む（設計の決定 2・11）。修正担当が見送るときに区分を書き（`drive.py` の結果の形・`review-criteria.json`）、ジャーナル（`journal.py`）が持ち、`record_review` が記録に書く。`record_review` の重ねない判定は詳細設計書に書いた全項目（head・終わり方・バージョン・項目・理由・本文・`withheld`）と伏せた記録の 2 つの規則のまま。Task 4 の保留なしの経路に、`_judged` の手順 (5) の `review_prs` → `review_rows` → `review_holds` と記録を読めないときの保留 1 件、`MvvFacts.review_holds`・`check_review_holds` を足し、`holds` を読んだ結果の配列（無ければ `[]`）にする
- **満たす受け入れ条件:** 4・5
- **進め方:** 失敗するテスト（`fast` で保留があっても `skip: true`）→ 通す最小実装 → 整理

### Task 6: 承認資料の根拠の行を確かめるコマンド

- **対象ファイル:** `plugins/ndf/src/ndf/domains/records/mvv.py`（`cite`）、`plugins/ndf/src/ndf/domains/records/cli.py`（`_mvv_cite`）、`plugins/ndf/docs/contracts/commands.md`、`plugins/ndf/tests/domains/records/test_mvv_cite.py`（新設）、`plugins/ndf/src/ndf/adapters/publish.py`（`Publisher._private_refs`）、`plugins/ndf/tests/adapters/test_publish.py`
- **変更内容:** 詳細設計書の「根拠の行の確かめ（`cite`）」。`mvv.decision_lines`（#4076 の計画の U7 が `records/mvv.py` に置く）で 4 要素を作り、`mismatch` の分岐は持たない。結果の `items`・`summary` を組む前に自由記述（`path`・`cited`・`unknown`）を伏せる。`ok` の結果の `summary` と `metrics` を資料に載せる形にする
- **満たす受け入れ条件:** 7（承認資料の側。`decisions.md` の例は #4076 の実装が直す）
- **進め方:** 失敗するテスト（バージョン 1 を引いた行で `refused` にならない）→ 通す最小実装 → 整理

### Task 7: 手順書と用語集

- **対象ファイル:** `plugins/ndf/skills/development-workflow/references/pace.md`、`approval-request.md`、`agent-layers.md`、`project-mvv.md`、`docs/glossary/glossary.json`、`docs/glossary.md`
- **変更内容:** 詳細設計書の「手順書」（supervisor の経路の承認資料の載せ方・「目指す姿の根拠」を決定の見出しの下に載せる形・`cite` の `ok`・`refused`・`precondition` の分岐）と、設計書の「用語」の表
- **満たす受け入れ条件:** 1・3・4・7・8（手順の側）
- **進め方:** テスト駆動を適用しない（`.md` の文言を突き合わせるテストは書かない決まり）。`ndf records glossary render --check` と `ndf records glossary meanings` が `ok` になることで確かめる

### Task 8: プランの承認資料に MVV 判定の節を載せる

- **対象ファイル:** `plugins/ndf/src/ndf/progress/gate/asking.py`（`mvv_lines`・`material`）、`plugins/ndf/tests/progress/gate/test_gate_present.py`
- **変更内容:** 詳細設計書の「手順書」の「プランの経路の承認資料の生成」。プランの承認資料（`asking.material`）に今回の判定・`why`・保留の欄を載せる。判定は文脈でなく、待っている工程の判定のステップの `step_recorded` の `outputs.mvv_gate` から読む。`holds` が `null`（Task 4 の保留なしの経路）でも、配列（Task 5 の後）でも資料を組む
- **満たす受け入れ条件:** 3（プランの経路の承認資料）
- **進め方:** 失敗するテスト（承認資料に「MVV 判定」の節が無い）→ 通す最小実装 → 整理

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 1 工程の入力とプランの外のエントリポイント | Task 1・2・3 | 3959:8・3959:9 | 約 400 | （実装の後に書き足す） |
| 2 判定を打つ条件と省く条件 | Task 4 | 3959:1・3959:2・3959:3 | 約 850（比較の API の 300 件の経路と差分の固定を含む） | |
| 3 レビューの保留と記録の欠落 | Task 5 | 3959:4・3959:5 | 約 750（見送りの区分を含む） | |
| 4 根拠の行の確かめ | Task 6 | 3959:7 | 約 350（伏せる処理の `publish.py` を含む） | |
| 6 プランの承認資料 | Task 8 | 3959:3 | 約 250 | |
| 5 手順書と用語集 | Task 7 | 3959:1・3959:3・3959:4・3959:7・3959:8 | 約 200 | |

- 行数は追加と削除の和で、`git diff --shortstat <ベース>...HEAD` で数える。数えないパスは無い（ロックファイルと再生成のファイルを触らない。`docs/glossary.md` は再生成だが、数えても 1,000 行を超えない）
- どの単位も `.ndf/` のファイルを触らない
- 実行の順: 単位 1 を最初に流す（#4076 の部品を使わないので、#4076 の実装の前でも始められる。#4076 の計画の U3・U4 と `records/mvv_gate.py`・`records/cli.py`・`docs/contracts/commands.md` を共有するが、触る関数と項目は分かれ、後に入る PR が取り込みで解く）。単位 2〜4 は #4076 の実装が main へ入った後に始める。単位 3 は単位 2 の後（`_judged` の配線と `checks.py` を単位 2 が変える。単位 2 は保留を読まない経路で単体で動き、単位 3 が保留の関数とフィールドを足して配線する。上の Task 4・Task 5 の範囲）。単位 4 は単位 1 の後（`records/cli.py` を単位 1 が変える）で、単位 2・3 と並列にしてよい（`mvv.py` は関数ごとに分かれ、衝突は取り込みで解ける）。単位 6 は単位 2 の後で、単位 3 と並列にしてよい（`asking.py` はほかの単位が触らない）。単位 5 は単位 1〜4 と 6 の後
- この計画の単位が使う #4076 の部品と、それを作る #4076 の計画の単位:

  | この計画の単位 | 使う #4076 の部品 | 作る #4076 の単位 |
  | --- | --- | --- |
  | 2 判定を打つ条件と省く条件 | `current`・`Snapshot.commit`・`Snapshot.items` | U3 MVV のスナップショット |
  | 3 レビューの保留と記録の欠落 | `record_review`（`clean`・`withheld: true`）・`mvv-review` の記録・ジャーナルの `judged_mvv`・`drive._summary` の順序・`finish_decide` の `mvv_unresolved` | U9・U10・U12 |
  | 4 根拠の行の確かめ | `parse_citation`・`citation_errors`・`mvv.decision_lines`・`mvv.fenced_lines` | U7 根拠の行の判定 |
  | 5 手順書と用語集 | `project-mvv.md` の「工程での読み方」・`agent-layers.md` の MVV の欄・用語集の 3 語 | U13 手順書と用語集 |

  どの単位も #4076 の計画の U1〜U13 がすべて main へ入った後に始める（設計の決定 7）。表は、#4076 の部品のシグネチャを突き合わせる先を示す
- バージョンを上げるのは単位 1〜6 と #4076 の実装がすべて main に入った後（設計の決定 7）

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る主なファイルは `mvv_gate.py` 168 行・`mvv.py` 171 行・`checks.py` 202 行・`records/cli.py` 308 行で小さい（2026-11-23 の main）。1,000 行の規則に近いのは 3 つで、`crossreview/drive.py` 998 行は #4076 の計画の U2 が先に約 800 行へ分ける。`progress/command.py` は計画 p9 の lsplit のスプリントブランチで 971 行なので、Task 3 の `stage_inputs_of` を新しい `progress/stage_inputs.py` に置いて足さない。`progress/invoke.py` は同じブランチで 965 行（開いている #4307 で 970 行）で、Task 2 は `resolve_inputs` の数行を置き換えるだけ。8 タスクのうちこの 3 ファイルを触るのは 2 つ（Task 2・5） |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `records/cli.py` の `_env_inputs` と `progress/stage_inputs.py` の `stage_inputs_of` の、工程の定義を引く処理の重なり（単位 1）と、`mvv_gate._judged` と `cite` がどちらも `current` を呼ぶ前の取得の手順（単位 2・4） | 単位をまたいで同じ形の処理が 2 か所にできうる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #4076 の実装が #4090・#4099 の直しと違う形で入る | 単位 2 を始める前に、#4076 の部品のシグネチャを詳細設計書の前提と突き合わせ、違えば設計の該当の箇所を直してから進める |
| 計画 p9 のレーン lz1-contract（#3684 #3667 #2339）が同じファイルを触る。U3 が `progress/model.py`（`MVV_PACES`）と `context.schema.json`（`pace` の分け方）、U7 が `stage-io.schema.json`・`context.schema.json`・`progress/command.py`・`progress/invoke.py`・`progress/gate/asking.py`、U5 が `records/mvv.py`（記録の読み書きに種類を渡す） | 単位 1・2・6 を始める前に、そのレーンの単位が main へ入ったかを確かめ、入っていれば取り込んでから始める。入っていなければ後に入る PR が base-sync で解く。重なりの一覧はこの計画を出した PR の本文にある |
| lz1-contract の U5 が `mvv-validation.jsonl` の記録を種類ごとのスキーマ（`contracts/schemas/internal/mvv-validation.schema.json`）で確かめるようにする。単位 3 が足す見送りの区分（`defer`）と、#4076 の U12 が書く `withheld: true` の記録がスキーマに無いと書けずに落ちる | 単位 3 を始める前に lz1-contract の U5 が main へ入ったかを確かめ、入っていればスキーマに欄を足す（触るファイルが 1 つ増える） |
| `progress/invoke.py` が lsplit と lz1-contract の U7 の変更の後に 1,000 行を超える | 単位 1 を始める前に `wc -l` で測り、Task 2 の変更で超えるなら `resolve_inputs` の入力の確かめを `progress/stage_inputs.py` へ移す |
| 単位 2 の後・単位 3 の前の main では、`normal` でも判定を打つがレビューの保留を読まない | バージョンを上げるのは単位 1〜6 の後なので、利用者の手元には出ない（決定 7） |

## 切り戻し手順

- データの移行は無い。単位ごとの PR を revert すれば戻る。単位 3 を戻すときは単位 5 の手順書の保留の記述も戻す
- 更新の前にリリース承認ゲートまで進んでいたスプリントは、`ndf progress cancel` してから作り直す（設計の決定 3。実装の PR の `## 移行の手順` に書く）

## 完了の定義

- [ ] 受け入れ条件 1〜5・7〜9 をすべて満たし、条件ごとに検証手段と結果が対応している（1・3 の判定の中身は、リリースの後の最初の `normal` のスプリントの設計承認ゲートで見る）
- [ ] 各単位の PR の CI が通り、`cross-review` が収束している（`.ndf/project.json` の `test.ci.only` が真なので手元でテストを打たない）
