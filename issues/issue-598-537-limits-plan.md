# #598 + #537: 監視の上限・CLI の上限・無進捗の許容の順序を 1 か所で決める（P2 の実装計画）

## 関連リンク

- 要求と受け入れ条件: [issue-662-598-537-619-584-583-requirements.md](issue-662-598-537-619-584-583-requirements.md)（P2 は AC30〜AC42 と AC70〜AC72）
- 設計文書: [issue-662-598-537-619-584-583-design.md](issue-662-598-537-619-584-583-design.md)（P2 は決定 10〜13。決定 10 の cross-refactoring の例外を含む）
- 契約の文書: [issue-662-598-537-619-584-583-design-contracts.md](issue-662-598-537-619-584-583-design-contracts.md)（「上限の表（P2）」「`limits.py`（P2）」「`launch-cli.sh` の第 7 引数（P2）」「`bg-wait.sh`（P2）」）
- 境界: [issue-647-592-553-design.md](issue-647-592-553-design.md) の決定 13 と「他の設計との契約」の D-A（P2）の行
- 前段: P1（#662、PR #677、develop の d11c473）
- 課題: #598 / #537（マイルストーン 21）

## モード

`standard`（判定済み）。共通層に上限の表を新設し、2 つの Skill の起動・監視・骨組みを表へつなぐため。

## 目的と非目的

達成したい状態:

- 監視の上限・無進捗の許容・CLI の上限の既定値を `lib/limits.py` の 1 つの表だけが持ち、全 28 組で「無進捗の許容 < 監視の上限 < CLI の上限」がテストで固定される
- agy の CLI の上限が、利用者が上書きした監視の上限からも + 120 秒で導かれ、CLI が監視より先に打ち切らない
- 600 秒を超える監視を、Claude Code の Bash の 1 回の上限（600 秒）に収まる待ちで待てる

やらないこと:

- `usage_limit` / `cli_timeout` の検知、プロセスグループでの停止、`read-result` の理由、起動し直しの経路（P3）
- cross-refactoring の `apply` / `fix` / `final-fix` の監視へ `--stall-timeout "$IMPL_STALL_TIMEOUT"` を足すこと（D-C の P6、#665）。P2 は `--phase` を渡し `--timeout` を渡さない形まで
- cross-refactoring の待ちを `bg-wait.sh` にすること（#656）

## 受け入れ条件

**要求の文書の AC30〜AC42 と AC70〜AC72 をそのまま使う。** ここでは写さない。タスクごとに満たす番号を指す。

## 修正対象

```text
plugins/ndf/scripts/lib/limits.py                 新設（上限の表と CLI）
plugins/ndf/scripts/lib/monitor.py                --phase、上限の表からの解決、許容 ≥ 上限の警告、記録の phase
plugins/ndf/scripts/lib/launch-cli.sh             第 7 引数に工程名
plugins/ndf/scripts/lib/README.md                 置いてあるものの表に limits.py
plugins/ndf/skills/cross-review/scripts/bg-wait.sh        新設
plugins/ndf/skills/cross-review/scripts/launch-reviewer.sh 工程名 review
plugins/ndf/skills/cross-review/scripts/critique.sh       工程名 critique、NDF_CRITIQUE_PRINT_TIMEOUT の引き上げ
plugins/ndf/skills/cross-review/scripts/critique-round.sh  --phase critique
plugins/ndf/skills/cross-review/scripts/wait-review.sh    冒頭の既定値の説明
plugins/ndf/skills/cross-review/SKILL.md                  骨組みの待ち（bg-wait.sh）と監視のコメント
plugins/ndf/skills/cross-review/docs/01-state-and-review.md  hard timeout の既定・区切った待ちの説明・スクリプト表
plugins/ndf/skills/cross-review/docs/03-review-output.md     「タイムアウトなしで wait」の値
plugins/ndf/skills/cross-review/docs/04-contracts.md         監視の記録の phase
plugins/ndf/skills/cross-refactoring/scripts/launch-cli.sh   PRINT_TIMEOUT を工程名に
plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/measure.py  記録の phase を優先
plugins/ndf/skills/cross-refactoring/SKILL.md                monitor.py の 4 か所を --phase に
plugins/ndf/skills/cross-refactoring/docs/01-state-and-propose.md / 02-apply-and-review.md / 04-fix-and-report.md  同 4 か所
テスト（cross-review/tests と cross-refactoring/tests）
dev.kiro / dev.agy の配布物（bash scripts/build-runtime-plugins.sh）
```

## タスク分解

### Task 1: 上限の表 `limits.py`

- **対象ファイル:** `plugins/ndf/scripts/lib/limits.py`、`cross-review/tests/test_limits.py`
- **変更内容:** `PHASE_TIMEOUT`（7 工程）・`AGENT_STALL`（4 担当）・`CLI_MARGIN = 120`・`DEFAULT_STALL = 180`。`monitor_timeout(phase, agent, explicit)`（`--timeout` → `MONITOR_TIMEOUT_<担当>` → `MONITOR_TIMEOUT` → 表）、`stall_timeout(agent, explicit)`、`cli_timeout(phase, agent)`、`check()`。CLI は `cli-timeout` / `monitor-timeout` / `check`。表に無い工程は終了コード 1
- **満たす受け入れ条件:** AC30、AC31
- **進め方:** 表の値と 28 組の順序・解決順・CLI の終了コードのテストを先に書く → 実装

### Task 2: 監視が工程で上限を引く

- **対象ファイル:** `lib/monitor.py`、`cross-review/tests/test_monitor_phase.py`、既存の `test_monitor_import_safety.py` / `test_monitor_outcome_file.py`
- **変更内容:** `--phase`（省略時は `review` の値、記録の `phase` は `null`）。担当ごとに上限と許容を解決し、許容 ≥ 上限なら担当名と 2 つの値を標準エラーへ 1 行。監視の開始時に解決した値を標準エラーへ 1 行。結果ファイルと記録に `phase`。`DEFAULT_TIMEOUT` などの定数は表を指す別名にする
- **満たす受け入れ条件:** AC30（表だけが既定値を持つ）、AC32、AC33
- **進め方:** 環境変数と引数の組み合わせで標準エラーの値と警告を見るテストを先に書く → 実装

### Task 3: 起動が工程名から CLI の上限を導く

- **対象ファイル:** `lib/launch-cli.sh`、`cross-review/scripts/launch-reviewer.sh`、`critique.sh`、`cross-refactoring/scripts/launch-cli.sh`、`cross-review/tests/test_launch_agy.py`（工程名の場合を足す）、`cross-review/tests/test_launch_print_timeout.py`（新設）、`cross-refactoring/tests/test_launch_agy_phases.py`
- **変更内容:** 第 7 引数が空なら `apply`、数字ならその秒数、工程名なら `limits.py cli-timeout`。`launch-reviewer.sh` は `review`、`critique.sh` は `critique`（`NDF_CRITIQUE_PRINT_TIMEOUT` が導出値未満なら導出値へ引き上げて 1 行出す）。cross-refactoring は `propose-tests` を `propose` に正規化して渡す
- **満たす受け入れ条件:** AC34、AC35、AC36
- **進め方:** 引数を書き出す偽の agy で `--print-timeout` の値を見るテストを先に書く → 実装

### Task 4: cross-refactoring の監視の呼び出しを `--phase` に

- **対象ファイル:** `cross-refactoring/SKILL.md`、`docs/01-state-and-propose.md`、`docs/02-apply-and-review.md`、`docs/04-fix-and-report.md`、`refactor_lib/measure.py`、`cross-refactoring/tests/test_monitor_phase_calls.py`（新設）、`test_run_metrics_summary.py`
- **変更内容:** 8 か所の `monitor.py` に `--phase` を渡し `--timeout` を消す。工程の所要の判定で記録の `phase` を優先する（P1 の申し送り）
- **満たす受け入れ条件:** AC37、AC38
- **進め方:** AC38 の grep と `--timeout` の不在をテストに書く → 文書を直す

### Task 5: 区切った待ち `bg-wait.sh` と cross-review の骨組み

- **対象ファイル:** `cross-review/scripts/bg-wait.sh`、`SKILL.md`、`scripts/critique-round.sh`、`scripts/wait-review.sh`、`docs/01-state-and-review.md`、`docs/03-review-output.md`、`cross-review/tests/test_bg_wait.py`、`test_skill_bg_wait.py`（新設）
- **変更内容:** `run` / `wait`。骨組みのレビューの監視（起動し直しを含む）と `critique-round.sh` を `bg-wait.sh run` で起動し、`wait` を 124 のあいだ繰り返す。`critique-round.sh` の監視へ `--phase critique`。3 か所の上限の説明を表の値へ
- **満たす受け入れ条件:** AC39、AC40、AC41、AC42
- **進め方:** `sleep` のコマンドで 124・終了コード・541 の丸めを見るテスト、骨組みの行の形のテストを先に書く → 実装

### Task 6: 配布物の同期と検証

- **対象ファイル:** `dev.kiro` / `dev.agy` の生成物
- **満たす受け入れ条件:** AC70、AC71、AC72
- **進め方:** `bash scripts/build-runtime-plugins.sh` → 検証手段の表のコマンド

## リスクと対処

| リスク | 対処 |
| --- | --- |
| cross-review の `SKILL.md` が行数の上限（`test_skill_layout.py` の 420 行、現在 417 行）に近い | 待ちの行を 1 行に収め、説明は `docs/01-state-and-review.md` へ置く。上限の値は変えない |
| `monitor.py` は触る範囲が広く、既存テストが多い | タスクごとに `cross-review/tests` の監視のテストを通す |
| テストの環境に `MONITOR_*` が export されていると値が変わる（#678） | 起動と監視を呼ぶテストは `MONITOR_` で始まる環境変数を除いた環境で動かす |

## 切り戻し手順

- Pull Request を revert すれば、上限は変更前（監視 420 秒、agy の CLI の上限は呼び出し側の固定値）へ戻る。状態ファイルの形は変えないため、データの移行は無い

## 実装上の決定

設計が「実装で決める」とした点と、設計が書いていない細部の決定を残す。

| # | 決めたこと | 理由 |
| --- | --- | --- |
| 1 | `limits.py` を bash から `python3` で呼ぶ（設計の未確認 9） | 標準ライブラリだけで書け、`uv run --script` の起動の費用と依存の解決を避けられる。起動の経路は既に `python3` を使っている（cross-refactoring の `launch-cli.sh` の雛形の展開） |
| 2 | 監視の結果ファイルと記録の両方に `phase` を足す | 契約の文書は記録の辞書に `phase` を足すとし、記録は結果ファイルと同じ辞書を追記する。2 つの形を分けない |
| 3 | `--phase` に表に無い名前を渡すと、`argparse` の `choices`（終了コード 2）ではなく自前の検査で終了コード 1 にする | 契約の文書が USAGE の 1 を約束している |
| 4 | 監視は担当ごとに解決した上限と許容を、開始時に標準エラーへ 1 行出す | AC32 を標準エラーで確かめるため（契約のテスト設計）。実行中の cross-review でどの上限が効いたかを利用者が読める |
| 5 | `bg-wait.sh wait` は、待ち切ったとき（124）にログの最後の 1 行を標準エラーへ、終わったときにログの全体を標準出力へ出す | 背景へ回すと監視の進捗と標準出力の JSON が進行側から見えなくなるため |
| 6 | `bg-wait.sh run` は背景の pid を `<rc>.pid` に残し、`wait` は rc ファイルが無く pid も生きていないとき（またはそもそも起動していないとき）終了コード 1 で戻る | 背景が強制終了されて rc ファイルが書かれないと、124 が永遠に返り待ちが終わらない |
| 7 | `lib/launch-cli.sh` は工程名の解決を全ランタイムで行う（値を使うのは agy だけ） | 表に無い工程名をランタイムに関わらず同じ終了コード 1 で拒む |

## 完了の定義

- [ ] AC30〜AC42 をすべて満たし、条件ごとにテストかコマンドが対応している
- [ ] AC70〜AC72 のコマンドが終了コード 0
- [ ] cross-refactoring と cross-review を収束まで回し、CI の必須チェックが最新の head で pass
