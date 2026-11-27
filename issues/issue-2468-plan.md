# #2468: 役に立たない繰り返しを止め、費用と時間に上限を持つ

## 関連リンク
- 要求仕様: [issue-2468-requirements.md](./issue-2468-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/2468 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-2468-design.md](./issue-2468-design.md)、詳細設計書: [issue-2468-design-detail.md](./issue-2468-design-detail.md)
- 実装の順: 3 課題のうち最後である。[#4363 の実装計画](./issue-4363-plan.md) と [#3780 の実装計画](./issue-3780-plan.md) の単位がすべてマージされた後に始める
- モード: `standard`（上限の数え方・設定・キューの振る舞いが変わる機能追加）

## 修正対象

詳細設計書の「地図」から引き写した。パスは `plugins/ndf/` からの相対である。

| ファイル | 変える中身 |
| --- | --- |
| `src/ndf/domains/crossreview/budget_decide.py`・`spend.py`（新設） | 使った量の判断と記録、中断の回収 |
| `src/ndf/domains/crossreview/verify_decide.py`・`verify.py` | `same_as`・`skip_broken` |
| `src/ndf/domains/crossreview/journal.py`・`fixer.py` | 仮の指摘と行の確定、見送った時点の head と比較基点 |
| `src/ndf/domains/crossreview/drive.py`・`fixgate_decide.py` | ラウンドの合計、上限の確認、中断と再開 |
| `src/ndf/domains/crossreview/scope_decide.py`・`round_scope.py`・`reviewed_pr.py` | 読んだ証跡に使う記録、全員が読めた head |
| `src/ndf/domains/crossreview/finish_decide.py`・`report_decide.py`・`leftover_decide.py`・`leftover.py`・`cli.py` | 終わり方 `limit`、残し方、`--budget-state` |
| `src/ndf/domains/crossreview/settings.py`・`criteria_decide.py` | `limits` |
| `src/ndf/core/plan/queue_decide.py`・`queue_run.py`・`cli.py` | 停止の回数と `--retry` |
| `src/ndf/core/route/__init__.py`・`contracts/templates/impl.json` | 単位のレビューの包みと `unreviewed` の経路 |
| `contracts/data/review-criteria.json`・`schemas/ndf-settings/review.schema.json`・`schemas/io/cross-review.args.schema.json`・`domains/ndf-files.json` | 指示の文・設定の形・引数 |
| `contracts/limits/cross-review.{max-seconds,max-usd,heartbeat-seconds}.json`・`plan.queue.same-step-stops.json`（新設）、`contracts/causes/repeated_stop.json`（新設） | 既定の値と停止の種類 |
| `docs/contracts/{commands,exit-codes,ndf-settings}.md` | `ndf check --write-spec` の生成物 |
| `skills/cross-review/SKILL.md`・`docs/01-state-and-review.md`・`docs/02-fix-and-rotation.md` | 数え方・上限・終わり方 |
| `tests/domains/crossreview/test_crossreview_{limits（新設）,leftover,journal,verify,run}.py`、`tests/core/plan/test_queue.py`・`test_queue_run.py`、`tests/route/test_templates.py`・`test_flows.py` | 新しい振る舞いのテスト |

詳細設計で設計の触るファイルに足したのは、`spend.py`、`verify.py`、`fixer.py`、`fixgate_decide.py`、`round_scope.py`、`plan.queue.same-step-stops.json`、`docs/contracts/` の 3 つ、`test_crossreview_verify.py`、`test_crossreview_run.py` である（理由は詳細設計の地図）。

## タスク分解

テストは先に書き、合否は Pull Request の CI で読む（`.ndf/project.json` の `test.ci.only`）。`plugins/ndf/bin/ndf check` は手元で打つ。時間の上限と経過の記録は時計と待ちを差し替えて確かめ、実時間を待たない。

### Task 1: 今の振る舞いを縛る退行テスト
- **対象ファイル:** `test_crossreview_run.py`、`test_crossreview_limits.py`（新設）
- **変更内容:** 指摘が無いラウンドは相手役の判定の文字によらず `converged`、`limits` の無い `version: 1` の `.ndf/review.json` が読める、収束した後の打ち直しは回る
- **満たす受け入れ条件:** 2468:2・6
- **進め方:** 今のコードで通るテストを足す（退行テスト）

### Task 2: 上限の設定
- **対象ファイル:** `criteria_decide.py`、`settings.py`、`review.schema.json`、`ndf-files.json`、秒と費用の上限の設定、`docs/contracts/ndf-settings.md`
- **変更内容:** `limits_of`・`ProjectLimits`・`ReviewSettings.limits`。まだ止める処理には使わない
- **満たす受け入れ条件:** 2468:6
- **進め方:** 失敗するテスト（形の違う `limits` は `invalid`）→ 最小実装 → 整理

### Task 3: 同じステップで 2 回止まったプランの流し直しを止める
- **対象ファイル:** `queue_decide.py`、`queue_run.py`、`core/plan/cli.py`、`repeated_stop.json`、`plan.queue.same-step-stops.json`、`docs/contracts/commands.md`・`exit-codes.md`、`test_queue.py`、`test_queue_run.py`
- **変更内容:** `next_stop`・`refused_repeat`・`_clear_stops`・`--retry`
- **満たす受け入れ条件:** 2468:5
- **進め方:** 失敗するテスト（子のプロセスは今の偽の Popen）→ 最小実装 → 整理

### Task 4: 判定役の回答に `same_as` と `skip_broken` を足す
- **対象ファイル:** `verify_decide.py`、`verify.py`、`review-criteria.json`、`test_crossreview_verify.py`
- **変更内容:** `select` の `recheck`、`instruction` の一覧、`read`・`ask`。駆動はまだ一覧を渡さない
- **満たす受け入れ条件:** 2468:1
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 5: 仮の指摘と行の確定
- **対象ファイル:** `journal.py`、`drive.py`、`test_crossreview_journal.py`
- **変更内容:** `propose`・`view`・`settle`、`enter` を 2 つの呼び出しの組にする、`carry` の本文の一致、振動の判定を行の番号で比べる。先頭の一致で重ねる今の期待は、critical・major について直す
- **満たす受け入れ条件:** 2468:1・2
- **進め方:** 失敗するテスト（詳細設計の `propose`・`view`・`settle` の観点）→ 最小実装 → 整理

### Task 6: 見送りの出直し・理由の確認・振動の候補を組み込む
- **対象ファイル:** `journal.py`、`fixer.py`、`drive.py`、`test_crossreview_journal.py`、`test_crossreview_run.py`
- **変更内容:** `to_verify`・`known_for_verifier`・`oscillating`・`apply_fix(at=...)`・`_stale_skips`
- **満たす受け入れ条件:** 2468:1
- **進め方:** 失敗するテスト（言い換えた出直しでラウンドが増えない・保護の対象で差分が変わると理由を確かめる・必ず人の承認が要る操作の指摘は人の判断を待つまま）→ 最小実装 → 整理

### Task 7: 使った量の判断と記録
- **対象ファイル:** `budget_decide.py`・`spend.py`（新設）、`heartbeat-seconds.json`、`test_crossreview_limits.py`
- **変更内容:** `counted`・`hit`・`recovered`・`same_target`・`advice`・`Meter`・`recover`・`other_records`。まだ駆動からは呼ばない。会話を続けた修正担当の `total_cost_usd` が起動分だけか累計かを、`claude -p` を `--resume` で 2 回打って確かめ、累計なら差を足す（結果は Pull Request の本文に書く）
- **満たす受け入れ条件:** 2468:3・4
- **進め方:** 失敗するテスト（時計と待ちを差し替える）→ 最小実装 → 整理

### Task 8: ラウンドを合計で数え、上限で止め、中断から続ける
- **対象ファイル:** `drive.py`、`scope_decide.py`、`reviewed_pr.py`、`fixgate_decide.py`、`settings.py`、`cli.py`（`--budget-state`）、`finish_decide.py`（終わり方 `limit` を足すだけ）、`report_decide.py`、`cross-review.args.schema.json`、`docs/contracts/commands.md`、`test_crossreview_limits.py`、`test_crossreview_run.py`
- **変更内容:** `_over`・`_resume`・`_collect`・`_decide`・`_fix`、`finished`、記録を相手役の前に保存する順。`cmd_fix_gate` も `stopped_limit` を渡す
- **満たす受け入れ条件:** 2468:2・3・4
- **進め方:** 失敗するテスト（収束していない打ち直しの合計が `--max-rounds` を越えない・上限で相手役を起動しない・判定の前の中断から続け、修正が要れば修正担当まで進む・修正担当の実行中の中断の使った量を 1 回だけ数える）→ 最小実装 → 整理

### Task 9: 上限で終わった起動の結果と残し方
- **対象ファイル:** `finish_decide.py`、`round_scope.py`、`reviewed_pr.py`、`leftover_decide.py`、`leftover.py`、`report_decide.py`、`cli.py`、`test_crossreview_limits.py`、`test_crossreview_leftover.py`
- **変更内容:** `Limited`・`_limit_verdict`・`read_all`・`of_record`・`filed_number`・出直しと上限の起票。**出直した見送りと上限で残った指摘の起票でも、全文の秘密を伏せてから題を切り出すことを、境目をまたぐ偽の秘密で確かめる。** 上限で残った指摘の行に `parked` を付けないことと、必ず人の承認が要る操作の指摘が `reraised` や上限の起票の後も人の判断を待つことも確かめる
- **満たす受け入れ条件:** 2468:1・4
- **進め方:** 失敗するテスト（詳細設計の上限の結果の表の 5 行）→ 最小実装 → 整理

### Task 10: 実装のプランの経路と単位のレビューの包み
- **対象ファイル:** `impl.json`、`core/route/__init__.py`、`test_templates.py`、`test_flows.py`
- **変更内容:** `unreviewed` の経路、`--budget-state`、`UNIT_REVIEW`。終了コード 4 で PR が一致する場合にファイルを消すことを確かめている今のテストは、残す期待に直す
- **満たす受け入れ条件:** 2468:3・4
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 11: 手順書
- **対象ファイル:** `skills/cross-review/` の 3 つの文書
- **変更内容:** 詳細設計の「手順書」
- **満たす受け入れ条件:** 2468:3・4・5
- **進め方:** テスト駆動は適用しない。読み手の立場で読み直す

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 上限の設定 | Task 1・2 | 2468:2・6 | 約 250 | |
| キューの停止の回数 | Task 3 | 2468:5 | 約 350 | |
| 判定役の同じ指摘の回答 | Task 4 | 2468:1 | 約 250 | |
| 仮の指摘と行の確定 | Task 5 | 2468:1・2 | 約 600 | |
| 見送りの出直し | Task 6 | 2468:1 | 約 400 | |
| 使った量 | Task 7 | 2468:3・4 | 約 450 | |
| 合計と中断の再開 | Task 8 | 2468:2・3・4 | 約 600 | |
| 上限の結果 | Task 9 | 2468:1・4 | 約 550 | |
| プランの経路と手順書 | Task 10・11 | 2468:3・4・5 | 約 350 | |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（プレリファクタリング） | しない | 2026-11-27 の `main` で `drive.py` 986 行・`cli.py` 848 行・`journal.py` 488 行・`core/route/__init__.py` 947 行。#4363 と #3780 のプレリファクタリングで `drive.py` と `cli.py` が減った後に始め、新しい処理は `budget_decide.py` と `spend.py` へ置く。`drive.py` を変えるのは 11 タスク中 3 つ（Task 5・6・8）。テストは cross-review 417・プラン 157・route 136 関数（#2468 を始める前の値） |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `drive.py`・`journal.py` と、同じ指摘を決める処理（`journal`・`leftover_decide.normalized`・`oscillation.finding_key`） | 3 課題の追加で 2 つのファイルが伸びる。本文の一致の決め方が 3 か所に分かれる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `drive.py` が 1,000 行を超える（3 課題の後の見込みは 950 行前後。実測ではない） | Task 8 を始めるときに行数を測り、超える見込みならラウンドの再開の処理を `spend.py` と同じ層の別のモジュールへ出す |
| 収束しないまま上限に達した Pull Request は打ち直してもラウンドを始めない | 結果に到達した上限と変える値を出す。手順書の「ラウンドを足す」を書き換える |
| 仮の指摘にする順の変更で、今の指摘の重ね方の期待が変わる | minor・nit の重ね方は変えないことを退行テストで縛る |
| 利用上限で 2 回止まったプランも `--retry` が要る | 案内の文に `--retry` を出す |

## 切り戻し手順
- 単位ごとの Pull Request を後ろから順に revert する。`.ndf/review.json` の `limits` と `queue.json` の `stop` は、前のバージョンが読み飛ばす
- 前のバージョンは `verdict: interrupted` のラウンドを読んだ証跡に数える。切り戻した後は、中断したラウンドが残る状態の保存先を使い回さない

## 完了の定義
- [ ] 受け入れ条件 2468:1〜6 をすべて満たし、条件ごとに CI のテストと対応している
- [ ] Pull Request の CI と `plugins/ndf/bin/ndf check` が通る
