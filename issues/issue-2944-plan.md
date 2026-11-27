# #2944: モデルと effort を実験で選ぶ

## 関連リンク

- 要求仕様: [issue-2944-requirements.md](./issue-2944-requirements.md)（受け入れ条件は番号で指す）
- 設計書: [issue-2944-design.md](./issue-2944-design.md)・[issue-2944-design-allocation.md](./issue-2944-design-allocation.md)・詳細設計書: [issue-2944-design-detail.md](./issue-2944-design-detail.md)
- 先行する計画: [issue-1959-plan.md](./issue-1959-plan.md)。**#1959 の全部の単位がスプリントブランチに入ってから始める**（起動の設定・使用量ログの行・起動記録のインターフェースを使う）
- モード: `standard`（公開インタフェースに追加があり、起動の既定の値が変わる）

## 修正対象

[詳細設計書の「地図」](./issue-2944-design-detail.md#地図)の 2 つの表のとおり。触るファイルの一覧は設計書の「設計の結果」が持つ。

## タスク分解

### Task 1: 今の起動の引数を縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/adapters/test_llm.py`・`tests/core/test_worker_launch.py`・`tests/progress/test_invoke.py`
- **変更内容:** 指定の無い起動が `--model`・`--effort` を持たないこと、判定の 2 回の打ち直し、cross-refactoring の起動の引数を固定する
- **満たす受け入れ条件:** 7
- **進め方:** 今のコードで通るテストを先に置く

### Task 2: 選択肢から値を選び、割り付ける

- **対象ファイル:** `contracts/domains/launch-experiments.json`・`adapters/model_choice.py`・`contracts/schemas/internal/experiment.schema.json`・`usage.schema.json`（足す欄。Task 3 と 4 が使う）・`domains/records/models_cli.py`（`pick`）・`tests/adapters/test_model_choice.py`（新設）、`adapters/launch_settings.py`・`launch.schema.json`、`cli.py`、`env.json`・`ndf-files.json`、`contracts/schemas/io/models.*`、`skills/launch-config/SKILL.md`
- **変更内容:** 詳細設計の「値を選ぶ処理」と `ndf models pick`
- **満たす受け入れ条件:** 1・7・10・12・14・15・19
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 効果指標を記録し、出現率を計算し、`report` で読む

- **対象ファイル:** `domains/records/experiments.py`・`allocation_decide.py`（新設）・`models_cli.py`（`report`）・`usage.py`（`record_outcome`）、`tests/domains/records/test_experiments.py`・`test_allocation.py`・`test_models_report.py`（新設）
- **変更内容:** 詳細設計の「効果指標と出現率」と `report`
- **満たす受け入れ条件:** 6・9・11・17・18・20
- **設計承認で持ち越した項目:** 実験の記録の保持を 13 か月の固定から「選択の計算に使う期間の設定値（`window_days`。既定 60 日、最長 365 日）＋約 30 日」へ変え、確認なしに自動で削除する（利用者の訂正、2026-11-27）。設計書の条件 17・`issue-2944-design-allocation.md` の「保持する期間」・詳細設計の `prune`・要求仕様の値も同じ単位で直す
- **進め方:** 計算は純粋な関数のテストを先に書く。記録の行は `experiment.schema.json` の形の見本で作る

### Task 4: worker・Skill・判定の起動に値を当て、行に残す

- **対象ファイル:** `adapters/claude.py`・`llm.py`・`run_log.py`、`core/worker.py`、`progress/invoke.py`・`gate/entry.py`、`domains/records/usage.py`、`claude-failures.json`
- **変更内容:** `--effort`、拒否の判定と基準への切り替え、使用量ログの欄、再開の値の復元。`claude -p` を実際に 1 回起動し、指定した値が受け付けられることと、会話の途中の切り替えの cache を stream-json で確かめる
- **満たす受け入れ条件:** 2・4・5・10・13・14
- **進め方:** 失敗するテスト → 実装 → 手で起動して確かめる

### Task 5: プランの実行が単位・引き上げの理由・修正の指標を扱う

- **対象ファイル:** `core/plan/transition_decide.py`・`runner.py`・`steps.py`・`records.py`・`model.py`、`contracts/templates/parts/steps.json`、`tests/core/plan/`
- **変更内容:** 詳細設計の「プランの実行」
- **満たす受け入れ条件:** 8・9・10・15・16
- **設計承認で持ち越した項目:** プランの外の Skill の起動は実験に入れず基準の組で動くことと、I8 は `outcome` の表の呼び出し元に限ることを限界に書く
- **設計承認で持ち越した項目（#1959 から移した）:** プランの実行は工程のモードを子へ環境変数で渡す。#1959 の A4（[issue-1959-plan.md](./issue-1959-plan.md) の Task 5）は、子へ渡す環境変数のうち `NDF_PLAN_RUN_ID`・`NDF_STEP_ID`・`NDF_STEP_ATTEMPT` の 3 つだけを入れた。モードの値を使うのはこの課題の `pick` なので、渡す所もこのタスクで入れる（conductor の決定、2026-11-27）。名前は `contracts/domains/env.json` に行を足して引く
- **進め方:** 遷移の判断は純粋な関数のテストを先に書く。`impl` と `check` のプランのテンプレートで遷移を通す

### Task 6: 相手役のシートを単位にし、レビューから効果指標を書く

- **対象ファイル:** `core/converge/participants.py`・`adapters/agents.py`、`domains/crossreview/seats.py`・`fixer.py`・`journal.py`・`drive.py`、`domains/records/usage.py`（`codex_cost`）、`contracts/data/codex-prices.json`・`runtimes.json`
- **変更内容:** 詳細設計の「cross-review と相手役」。`codex exec --json` を実際に打ち、使用量の欄の内訳（`cached_input_tokens` が `input_tokens` の内数か）、利用上限とサンドボックスの失敗の検出を確かめる
- **満たす受け入れ条件:** 3・4・13・15・16
- **進め方:** 実際の出力を見本にしてテストを先に書く

### Task 7: 委譲の起動にモデルと effort の選択を足す

- **対象ファイル:** `domains/settings/delegate_run_cli.py`、`skills/external-ai/SKILL.md`・`references/cli-codex.md`・`references/cli-agy.md`・`references/cli-kiro.md`・`skills/qa-security-scan/03-report-template.md`・`agents/corder.md`
- **変更内容:** 詳細設計の「`ndf models` と `ndf delegate-run`」（`--runtime` は codex・kiro・agy。kiro と agy は `pick` を呼ばない）と手順書の固定の `medium` の削除
- **満たす受け入れ条件:** 3
- **進め方:** CLI はテストを先に書く。手順書の起動の例は打って確かめる

## レビューの単位

行数は `git diff --shortstat <ベース>...HEAD -- . ':(exclude)uv.lock' ':(exclude)plugins/ndf/docs/contracts' ':(exclude)plugins/ndf/contracts/schemas/io/*.args.schema.json'` で数える。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 先に要る単位 | 実際の行数 |
| --- | --- | --- | --- | --- | --- |
| B1 値を選ぶ処理 | Task 1・2 | 2944:1・2944:7・2944:10・2944:12・2944:14・2944:15・2944:19 | 約 900 | #1959 の A1〜A5 | |
| B2 出現率の計算と report | Task 3 | 2944:6・2944:9・2944:11・2944:17・2944:18・2944:20 | 約 900 | B1 | |
| B3 claude の起動への適用 | Task 4 | 2944:2・2944:4・2944:5・2944:10・2944:13・2944:14 | 約 700 | B1 | |
| B4 プランの実行 | Task 5 | 2944:8・2944:9・2944:10・2944:15・2944:16 | 約 500 | B2・B3 | |
| B5 cross-review と相手役 | Task 6 | 2944:3・2944:4・2944:13・2944:15・2944:16 | 約 700 | B2・B3 | |
| B6 委譲の引数 | Task 7 | 2944:3 | 約 250 | B1 | |

- 順序: B1 → （B2・B3・B6 は並列）→ （B4・B5 は並列）
- B3 は起動の差し込みの `record_outcome` を呼ぶ側で、受け口は B2 が `usage.py` に足す。2 つとも `usage.py` を触るので、後にマージする側が取り込む
- B1 だけが入った状態では、どの起動も値を取得しないので振る舞いは変わらない。B3 以降で起動の値が変わるので、`main` へは B2〜B5 をそろえて出す（値を変えるのに指標を記録しない状態を作らない）
- B1・B2 が 900 行になる見込みの大半はテストである。1,000 行を超えそうなら、B1 は同梱の表と優先の順・割り付けで、B2 は計算と `report` で分ける

条件 6 の「2 スプリント以上の後の report」は、リリース後の確認で行う（要求仕様の「リリース後の確認の計画」）。条件 20 は設計レビューで確かめる。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `worker.py` 998 行（7 タスク中 2）、`drive.py` 960 行（1）、`invoke.py` 930 行（2）、`claude.py` 783 行（1）、`runner.py` 731 行（1）。テストは `test_invoke.py` 68 件、`test_crossreview_run.py` 46 件、`test_runner.py` 29 件がある。計算と値の取得は新しいモジュールに置く |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | worker・Skill・判定・シートの「値の取得 → 起動 → 拒否で切り替え → 行」の同じ形、`usage.py`（2 課題の単位が触る） | 4 つの呼び出し元が同じ手順を持つ |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `codex exec --json` で、利用上限とサンドボックスの失敗の検出が変わる | Task 6 の最初に実際の出力を取り、検出の形を決める |
| 使えないモデルの利用者で起動が止まる | 拒否の判定を実際の出力の見本で縛り、基準へ切り替える |
| このスプリント自身の工程の値が変わる | 実験を止める場合は `.ndf/launch.json` の `experiments.enabled` を偽にする |

## 切り戻し手順

- 利用先は `"experiments": {"enabled": false}` で基準の組に戻せる
- コードは単位の PR を revert する。実験の記録と出現率のファイルは残っても読まれない

## 完了の定義

- [ ] 受け入れ条件 1〜5・7〜20 を満たし、条件ごとに検証手段と結果が対応している。条件 6 はリリース後の確認の計画に残した
- [ ] CI のテスト・静的解析・`claude plugin validate .`・`ndf records glossary render --check` が通る
- [ ] 手元の `claude` と `codex` を実際に 1 回起動した結果を PR に書いた
