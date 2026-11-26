# #4513: デプロイの工程をバージョン番号を持たないリポジトリでも終える

## 関連リンク

- 要求仕様: [issue-4513-requirements.md](./issue-4513-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4513 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4513-design.md](./issue-4513-design.md)。詳細設計書: [issue-4513-design-detail.md](./issue-4513-design-detail.md)
- モード: `standard`（公開インタフェースの結果 JSON の形と工程の振る舞いを変える不具合の修正で、触るファイルが 4 を超える）
- 外した範囲: 配布物に含まれることの確かめ・手動の配布の記録・その復旧は #4586 で扱う

## 修正対象

詳細設計書の「地図」の表から引き写す。関数ごとの変える前と後は、詳細設計書の構成要素ごとの「関数の仕様」にある。

| ファイル | 変える中身 |
| --- | --- |
| `contracts/schemas/result.schema.json` | `product_version` を空白だけでない文字列にし、バージョン番号の形を `$defs/version_number` へ移す |
| `contracts/schemas/release-verification.schema.json` | `version` を空白だけでない文字列にする |
| `contracts/worker/kinds.json`・`contracts/workflow/context.schema.json` | `product_version` の説明 |
| `src/ndf/contracts/worker_kinds.py` | `write_released`・`released_of`（新設） |
| `src/ndf/progress/definition.py` | `required_outputs`（新設） |
| `src/ndf/progress/run.py` | `released_answer`（新設）、`_run_step`・`_step_draft`・`_finish_stage`（変更） |
| `src/ndf/progress/invoke.py` | `StageRun.released`・`answered_gap_detail`（新設）、`plan_path`・`_no_route`（変更） |
| `src/ndf/progress/gate/entry.py` | `released_outcome`・`_version_kind`（新設）、`_cmd_work`・`work_argv`・`_outputs_of`（変更） |
| `src/ndf/domains/release/forms_decide.py` | `reaches`（新設）、`deliveries_of`（変更） |
| `src/ndf/core/worker.py`・`core/worker_outcome.py` | 引数 `--require-version-number`、`Checked`（新設）、`require_outputs`・`_output_gap`・`outcome_of`（変更） |
| `src/ndf/core/plan/steps.py`・`transition_decide.py`・`runner.py`・`records.py` | `missing_outputs` と要約の引き継ぎ、`outputs.json` の要素の並び |
| `skills/` の 5 ファイル | `product_version` の意味と、出力の足りない承認の読み方 |

テストは詳細設計書の地図の `tests/` の 13 ファイルに足す。

## タスク分解

変更の種類は不具合なので、各単位の最初のタスクは、今は落ちる再現のテストにする。

### Task 1: 短いハッシュの値が結果 JSON の形の誤りになることを再現する

- **対象ファイル:** `tests/contracts/test_result.py`・`tests/core/test_worker_outcome.py`
- **変更内容:** `product_version: "main abc1234"` の結果 JSON が今は形の誤りになることを、受け入れ条件 3 の期待（通る）で書く。空文字・空白だけが誤りになる期待（条件 5）も並べる
- **満たす受け入れ条件:** 3・5（再現）
- **進め方:** 失敗するテストを先に書くだけ。実装は Task 2

### Task 2: 形の確かめを結果の定義から `ndf worker run` へ移す

- **対象ファイル:** `result.schema.json`・`release-verification.schema.json`・`kinds.json`・`context.schema.json`・`core/worker.py`・`core/worker_outcome.py`・`progress/gate/entry.py`（`_version_kind`・`work_argv`・`_outputs_of`）・`skills/release-verification/SKILL.md`
- **変更内容:** 詳細設計書の「結果 JSON とリリース後テストの記録の定義」と「worker の結果の確かめ」の `ok` の分。`gate` の引き継ぎは Task 5 に回す
- **満たす受け入れ条件:** 3・4・5・8
- **進め方:** Task 1 のテストを通す → `none` 以外と設定を読めないときに `ndf v11.0.0` が欠けになるテスト（条件 8）を足して通す → 整理

### Task 3: オーガナイザーの承認の後にデプロイの工程が止まることを再現する

- **対象ファイル:** `tests/progress/test_sprint_templates.py`・`tests/progress/gate/test_gate_work.py`
- **変更内容:** `none` の設定で、オーガナイザーが `main abc1234` を記録して承認した後のデプロイの工程が、判断の求めで止まらず完了し、worker を起動しないことを期待で書く（今は落ちる）
- **満たす受け入れ条件:** 1・2（再現）
- **進め方:** 失敗するテストを先に書くだけ。実装は Task 4

### Task 4: 記録済みの値を `values.json` で渡し、worker を起動せずに終える

- **対象ファイル:** `contracts/worker_kinds.py`・`progress/run.py`（`released_answer`・`_run_step`）・`progress/invoke.py`（`StageRun.released`・`plan_path`）・`progress/gate/entry.py`（`released_outcome`・`_cmd_work`）・`domains/release/forms_decide.py`・`skills/release/SKILL.md`・`references/release-steps.md`・`skills/development-workflow/references/parallel.md`
- **変更内容:** 詳細設計書の「値のファイル」「工程の進行: 記録済みの値の読み方」「worker の起動」の記録済みの値の分
- **満たす受け入れ条件:** 1・2
- **進め方:**
  1. Task 3 のテストを通す
  2. 設計書のテスト設計の決定 2・3 と条件 1 の行を足して通す。届け先の選び方、利用者の承認の回で worker を起動すること、前の `values.json` が残った retry、ハイフンと波括弧を含む値である
  3. 整理

### Task 5: 値をそろえた判断の求めへの承認で終わることを再現し、`gate` の出力を引き継ぐ

- **対象ファイル:** `core/worker_outcome.py`・`core/plan/records.py`・`core/plan/runner.py`（`run_step`）・`tests/core/test_worker_outcome.py`・`tests/core/plan/test_runner.py`・`tests/progress/test_invoke.py`
- **変更内容:** 再現のテスト（`gate` で `delivery`・`product_version` を持つ結果ファイルの後の `approved` で工程が完了する。今は落ちる）を先に書く。続けて、詳細設計書の「worker の結果の確かめ」の `gate` の分と「プランの出力の記録」を実装する
- **満たす受け入れ条件:** 9、8（`gate` の値の形）
- **進め方:** 失敗するテスト → `require_outputs` の `gate` → `outputs.json` の要素の並び → 設計書のテスト設計の決定 6 の行と、却下して retry した後の前の回の値の行を足して通す → 整理

### Task 6: 出力の足りない承認を、名前と理由を示して失敗にする

- **対象ファイル:** `progress/definition.py`・`progress/invoke.py`（`answered_gap_detail`・`_no_route`）・`progress/run.py`（`_step_draft`・`_finish_stage`）・`core/plan/steps.py`・`transition_decide.py`・`runner.py`（`_perform`・`final_result`）・`records.py`（`_keep_step_missing`・`recorded_step_missing`）・`skills/progress-tracking/SKILL.md`
- **変更内容:** 詳細設計書の「工程の定義の読み手」「プランの遷移と結果の要約」「失敗の理由」
- **満たす受け入れ条件:** 6・7
- **進め方:**
  1. 失敗するテスト: 出力の無い判断の求めに `approved` と回答すると、理由に `product_version` と worker の要約が入る（今は落ちる）
  2. 実装して通す
  3. テストを足して通す: 失敗の後に `ndf progress next` を打っても worker が起動されない（条件 7）。先のステップが同じキーを返すプランでも欠けが隠れない
  4. 整理

## レビューの単位

1 つの課題の見込みが 1,000 行を超えるので、タスクの依存の向きで 4 つに分ける。どの単位も単独でテストが通る。行数は `git diff --shortstat <ベース>...HEAD -- plugins/ndf` で数える。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| U1 形の確かめの移し替え | Task 1・2 | 4513:3・4513:4・4513:5・4513:8 | 約 300 | （実装の後に書き足す） |
| U2 記録済みの値で終える | Task 3・4 | 4513:1・4513:2 | 約 400 | （実装の後に書き足す） |
| U3 判断の求めの出力の引き継ぎ | Task 5 | 4513:9 | 約 350 | （実装の後に書き足す） |
| U4 出力の足りない承認の理由 | Task 6 | 4513:6・4513:7 | 約 400 | （実装の後に書き足す） |

- 順序は U1 → U2 → U3 → U4 にする。U2 は U1 の `_version_kind` と緩めた結果の定義を使う。U4 は U3 の `metrics.missing_outputs` を使う
- U2 を 2 番目に置くのは、他のプロジェクトの進行を止めている事象（オーガナイザーの承認の後に工程が終わらない）を直す単位だからである
- U3 と U4 を分けるのは、合わせると約 750 行で 400 行の目安を超え、U3 だけで受け入れ条件 9 が単独で通るためである

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | しない | 測った値: 触るファイルのうち 700 行を超えるのは `invoke.py` 930・`run.py` 965・`core/worker.py` 998・`runner.py` 731 の 4 つ。同じファイルを触るタスクの割合は、最も多い `invoke.py`・`run.py`・`entry.py` で 6 タスク中 2〜3。どのファイルも既存のテストが厚い（`test_invoke.py` 68・`test_run.py` 62・`test_gate_work.py` 37・`test_runner.py` 29）。足すのは関数の単位で、既存の関数の分け方を変えない。`core/worker.py` は引数を 1 つ足すだけである |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `records.py` の `_keep_step_gaps` と `_keep_step_missing`（同じ形のステップごとの記録が 2 つ並ぶ）、`run.py` の `_step_draft` と `_finish_stage` の失敗の理由の組み立て | U3 と U4 が同じファイルに同じ形の処理を足す。1 つにまとめるかは、2 つが並んだ後に見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 結果 JSON の定義を緩め、`product_version` を書く経路が worker の確かめを通らないと、バージョン番号の形が守られない | U1 で `none` 以外と設定を読めないときの `ndf v11.0.0` のテスト（条件 8）を置く。NDF の中のほかの書き手（バージョンアップの組み立て・`release-steps`）は保存先から値を作るので変えない |
| `outputs.json` の形を変え、直す前の形を読む実行がある | 直す前の形をステップの分からない要素 1 つとして読むテストを U3 に置く |
| 進行中のスプリントで、直す前の NDF が書いた `values.json` が残る | `plan_path` が毎回 `released_version` を書き直す。U2 の retry のテストで確かめる |

## 切り戻し手順

- 単位ごとの PR を revert すれば戻る。データの移行は無い
- U3 を戻すと `outputs.json` は直す前の形に戻るが、U3 の後に書いた要素の並びの形は直す前の NDF が読めない。U3 を戻すときは、進行中のプランの保存先の `outputs.json` を消してから打ち直す

## 完了の定義

- [ ] 受け入れ条件 1〜9 をすべて満たし、条件ごとに設計書の「テスト設計」の行のテストと結果が対応している
- [ ] 単位ごとの PR の CI が通る。このリポジトリはテストを CI に寄せる設定（`.ndf/project.json` の `test.ci.only: true`）なので、テストは手元で走らせず、CI の結果で合否を決める
- [ ] レビューの単位の「実際の行数」を書き足した
