# #2944 の詳細設計: モデルと effort の実験

設計文書 [issue-2944-design.md](./issue-2944-design.md) と [issue-2944-design-allocation.md](./issue-2944-design-allocation.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは断りが無ければ `plugins/ndf/` からの相対で書く。#1959 を実装した後のコード（[issue-1959-design-detail.md](./issue-1959-design-detail.md)）を前提にする。語の意味は設計書の「解き方の全体像」と「用語」にある。この文書の `layer`（層）は起動の種類で、コードの層（adapters など）とは別である。

## 地図

パッケージ・モジュール構成（触るファイルのうちコード。`*` は新設）:

```text
src/ndf/
  cli.py                              models・delegate-args の行
  adapters/
    model_choice.py *                 値の取得・割り付け・引き上げ・実験の記録の読み書き
    launch_settings.py                experiments のキー
    claude.py                         --effort・拒否の判定・効果指標のインターフェース
    llm.py                            判定の単位
    run_log.py                        子の環境の上書きと除外
    agents.py                         Codex の使用量と所要時間
  core/
    worker.py                         worker の起動の値と行
    converge/participants.py          相手役の引数と回答の読み取り
    plan/runner.py・steps.py・records.py・transition_decide.py  単位・打ち直しの理由・修正の効果指標・models の記録
  progress/invoke.py・gate/entry.py   Skill の起動の値、worker へのモードの引き継ぎ
  domains/
    records/usage.py                  行の欄・Codex の費用・効果指標の受け口
    records/experiments.py *          効果指標の記録と出現率の更新
    records/allocation_decide.py *    出現率の計算（純粋な関数）
    records/models_cli.py *           ndf models
    settings/delegate_args_cli.py *   ndf delegate-args
    crossreview/seats.py・fixer.py・journal.py・drive.py  シートの単位・修正担当・指摘の担当・効果指標
```

依存の向き: 層は下から contracts・adapters・core・progress・domains の順で、下の層は上の層を読み込まない。値を選ぶ処理は全部の層から呼ぶので adapters に置き、割り付けに要る実験の記録と出現率のファイルの読み書きもそこに置く。`domains/records/experiments.py` は、adapters のこの読み書きを使って読む。adapters と core から出現率の計算（domains）を呼ぶ所は、下の層が上の層を読み込めないため、#1959 の起動記録のインターフェースに効果指標の関数を足して、差し込みで呼ぶ。

| パス | 種類 | 役割 | 依存する先 | クラスまたはモジュール | 新設（N）と変更（C） |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/adapters/model_choice.py` | 新設 | 値と出所を返す | launch_settings・launch_approval | `Choice` | N: `table`・`stage_of`・`pick`・`raised`・`fallback`・`candidates`・`compatibility_key`・`read_rows`・`append_row` |
| `src/ndf/adapters/launch_settings.py` | 変更 | `experiments` を返す | — | `Settings` | C: `load` |
| `src/ndf/adapters/claude.py` | 変更 | 引数と拒否の判定 | — | `ClaudeCall`・`LaunchRecorder` | C: `claude_launch`・`_error_reply`。N: `report_outcome` |
| `src/ndf/adapters/llm.py` | 変更 | 判定の単位 | model_choice | モジュール | C: `ask` |
| `src/ndf/adapters/run_log.py` | 変更 | 子の環境 | — | モジュール | C: `child_process_env`。N: `overlay_env` |
| `src/ndf/adapters/agents.py` | 変更 | 監視の結果 | — | `AgentStatus` | C: `_start_and_watch`。N: `codex_usage` |
| `src/ndf/core/worker.py` | 変更 | worker の起動 | model_choice | `Handoff` | C: `_add_run`・`delegate`・`_resume_until_written`・`_cmd_run` |
| `src/ndf/core/converge/participants.py` | 変更 | 相手役の引数 | model_choice | モジュール | C: `review_launch`・`read_only_launch`・`reading_launch`・`answer_text` |
| `src/ndf/core/plan/transition_decide.py` | 変更 | 打ち直しの理由を数える | — | `RunStep`・`StepState` | N: `retry_reason`。C: `_run` |
| `src/ndf/core/plan/runner.py` | 変更 | 単位の ID と効果指標 | model_choice・claude | モジュール | C: `run_step`・`final_result`。N: `_step_env`・`_after_unit` |
| `src/ndf/core/plan/steps.py` | 変更 | 子の結果の起動の値を拾う | — | モジュール | C: `outcome_of` |
| `src/ndf/core/plan/records.py` | 変更 | 回をまたぐ記録 | — | モジュール | N: `_add_models`・`recorded_models`・`read_units`・`write_units` |
| `src/ndf/progress/invoke.py` | 変更 | Skill の起動 | model_choice | モジュール | C: `_call_skill` |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf worker run` の引数に `--mode` を足す | — | モジュール | 呼ぶ引数を足すだけ |
| `src/ndf/domains/records/usage.py` | 変更 | 行と指標の受け口 | experiments | モジュール | N: `codex_cost`・`record_outcome` |
| `src/ndf/domains/records/experiments.py` | 新設 | 効果指標と出現率 | model_choice・allocation_decide | `UnitValue` | N: `record_outcome`・`unit_values`・`refresh` |
| `src/ndf/domains/records/allocation_decide.py` | 新設 | 計算 | — | `Entry`・`Judgement` | N: `weighted`・`eliminated`・`probabilities`・`judgement` |
| `src/ndf/domains/records/models_cli.py` | 新設 | `ndf models` | model_choice・experiments・usage | モジュール | N: `main`・`_pick`・`_report`・`report_rows` |
| `src/ndf/domains/settings/delegate_args_cli.py` | 新設 | 委譲の引数 | model_choice・launch_settings | モジュール | N: `main` |
| `src/ndf/domains/crossreview/seats.py` | 変更 | シートの単位 | model_choice・usage | `SeatRun` | C: `launch_reviewers`・`_seat_launch`・`_seat_result` |
| `src/ndf/domains/crossreview/fixer.py` | 変更 | 修正担当の値 | model_choice・usage | `FixRun` | C: `_fixer_launch`・`_launch_fixer`・`_keep_fixer` |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 挙げた担当を全員持つ | — | `FindingJournal` | C: `enter`。N: `experiment_values` |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 効果指標を渡す | experiments | `_Run` | C: `_round`・`_summary`。N: `_record_experiment` |
| `src/ndf/cli.py` | 変更 | サブコマンドの登録 | — | `COMMANDS` | C: 行の追加 |

表と手順書:

| パス | 変える中身 |
| --- | --- |
| `contracts/domains/launch-experiments.json`（新設） | 設計書の「初期の選択肢と効果指標」「層と作業の名前から工程を引く対応」の表、effort とモデルの順位、`allocation` の既定、指標のバージョン |
| `contracts/data/codex-prices.json`（新設） | 単価、`default_model`、取得日、出典 |
| `contracts/domains/claude-failures.json` | `model_refused` の行（どの呼び出し元の停止理由も `precondition`） |
| `contracts/domains/runtimes.json` | codex の `usage_args: ["--json", "-o", "{answer}"]`、モデルと effort の引数の形 |
| `contracts/domains/env.json` | `NDF_UNIT_ID`・`NDF_CHARGE_TO`・`NDF_RETRY_CAUSE`・`NDF_LAUNCH_MODEL`・`NDF_LAUNCH_EFFORT`・`NDF_LAUNCH_CODEX_MODEL`・`NDF_LAUNCH_CODEX_EFFORT`・`CLAUDE_CODE_EFFORT_LEVEL` |
| `contracts/domains/ndf-files.json` | `launch-experiments.jsonl`・`launch-weights.json`（保存先は使用量ログと同じ） |
| `contracts/schemas/ndf-settings/launch.schema.json` | `experiments` のキー |
| `contracts/schemas/internal/experiment.schema.json`（新設）・`usage.schema.json` | 実験の記録の行の形、使用量ログの足す欄 |
| `contracts/schemas/io/models.*`・`delegate-args.*`・`docs/contracts/*.md` | 引数のスキーマと規約のページは生成し、結果のスキーマは手で書く |
| `skills/external-ai/SKILL.md`・`references/cli-codex.md`・`skills/qa-security-scan/03-report-template.md`・`agents/corder.md` | 固定の `medium` と `safety-args` を `ndf delegate-args` にする |
| `skills/launch-config/SKILL.md` | `experiments` の書き方。承認の対象ではないこと |

## 値を選ぶ処理

満たす決定: 決定 1・2・3・6・8、受け入れ条件 1・2・7・10・12・14・15・19

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `table` | `adapters/model_choice.py` | 新設 | `table(root: Path) -> Table` | 同梱とリポジトリの `experiments` を重ね、値の制約を検証する |
| `stage_of` | 同 | 新設 | `stage_of(layer: str, name: str) -> Optional[str]` | 層と作業の名前から工程を引く |
| `pick` | 同 | 新設 | `pick(layer: str, name: str, runtime: str = "claude", *, cwd: Path, unit: Optional[str] = None, retry: Optional[str] = None, mode: Optional[str] = None, argument: tuple[Optional[str], Optional[str]] = (None, None), environ: Optional[Mapping] = None) -> Choice` | 優先の順で値を決め、要れば割り付けて引き上げる |
| `raised` | 同 | 新設 | `raised(runtime: str, model: Optional[str], effort: Optional[str]) -> tuple[Optional[str], Optional[str]]` | 設計書の「引き上げる起動」の上げ方の表を当てる |
| `fallback` | 同 | 新設 | `fallback(choice: Choice, reason: str) -> Optional[Choice]` | 切り替える条件に当たれば `launch_error` の行を書き、基準の組の値を返す。当たらなければ None |
| `candidates` | 同 | 新設 | `candidates(arms: Sequence, rows: Sequence, entry: Optional[Mapping], now: datetime) -> list[int]` | 打ち切った組と起動できない組を除いた添字 |
| `compatibility_key` | 同 | 新設 | `compatibility_key(arms: Sequence, metric: str) -> str` | 選択肢の並びと指標のハッシュ |
| `read_rows`・`append_row` | 同 | 新設 | `read_rows(path: Optional[Path] = None) -> list[dict]`・`append_row(row: Mapping) -> bool` | 実験の記録を読む・形を確かめて足す |

- `Choice` は `layer`・`name`・`runtime`・`stage`・`mode`・`model`・`effort`・`model_source`・`effort_source`・`unit`・`assign_id`・`raised`・`arm`・`base_arm` を持つ。返すものは、`cli_args() -> list[str]`（claude は `--model`・`--effort`、codex は `-c model_reasoning_effort=…`・`-m`）、`env_changes() -> dict[str, Optional[str]]`（`CLAUDE_CODE_EFFORT_LEVEL` を外すか引き継ぐか。None は外す）、`row_fields() -> dict`（使用量ログの欄）を返す
- `pick`
  - 事前条件: `layer` は `worker`・`skill`・`judge`・`seat`
  - 事後条件: 引数か環境変数で、モデルと effort の片方でも指定があれば割り付けず、無い側は基準の組の値。`unit` が無い場合、`enabled` が偽の場合、選択肢が 1 つの場合は、基準の組の値（出所は `base`、組が値を持たなければ `default`）で、割り付けの行を書かない。停止（`enabled` が偽）か固定（選択肢が 1 つ）で基準を使う単位に割り付けがあれば `halted` の行を足す。工程が無い層と作業の名前の起動には、引数を足さない（環境変数と起動の引数の指定は効く）
  - 副作用: 実験の記録のロックの中で、読み取り、割り付けの判定、1 行の追記をする
- `append_row` は、呼び出し元がロックを取らない呼び出し（拒否・停止・`unpriced` の行）でも `pick` と同じロックを使い、記録が改行で終わらなければ改行を先に足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `table` | 設定の誤り | 形、順位に無い effort、無い工程・層・ランタイム、値の制約 | 読み込みの直後 | 呼び出し元へ返す（`InternalError("precondition")`） | `stopped`・`precondition`。ファイルとキーを示す | 起動しない |
| `pick` | 値の誤り | 環境変数か引数の effort が順位に無い、モデル名に改行、表に無い `layer`・`retry` | 引数の確認 | 呼び出し元へ返す | 同上 | 起動しない |
| `pick` | 記録を書けない | 権限・容量・ロックの待ち | 追記 | 既定値（指定なし、割り付け ID なし）で続ける | 標準エラーに警告 | 単位は実験に数えない |
| `read_rows` | 壊れた行 | 書きかけ | 解析 | 飛ばして続ける | 出力なし | — |
| `fallback` | 切り替えの記録を書けない | 同上 | 追記 | 基準の値を返して続ける | 警告に起動できなかった組と基準で続けたこと | — |

### 処理の手順

`pick` は次の順で見る（[allocation の「割り付けの流れ」](./issue-2944-design-allocation.md#割り付けの流れ)）。

1. 実験の停止（`enabled` が偽）と固定（選択肢が 1 つ）
2. この単位の、基準への切り替えか停止の記録
3. この単位の既にある割り付け
4. 候補の抽選

`retry` があれば、決まった組に `raised` を 1 回だけ当てる。引数と環境変数の値は上げない。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 工程・選択肢・順位・指標・割り付けの値 | 設定値 | `contracts/domains/launch-experiments.json` | 設計書の表 |
| 起動できない組の条件 | 定数 | 同上 | 直近 7 日に、モデルを理由にした拒否が 2 件 |
| 実験の記録の行 | 型 | `contracts/schemas/internal/experiment.schema.json` | `assign`・`outcome`・`launch_error`・`halted`・`unpriced` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並列の起動の割り付け | `launch-experiments.jsonl` | ファイルのロックの中で読んで足す。ロックの中で出現率を計算しない | `window_days` の期間の中の同じ単位には同じ組を返すので、打ち直しても行は増えない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `read_rows`・`append_row` | `domains/records/jsonl.py` | 層の順序（adapters は domains を読めない） | 使えない。`filelock` と `contracts` の形の確認を使う |
| `table` | `launch_settings.load` | #1959 の詳細設計 | 使う |
| `compatibility_key` | `launch_approval.digest` の形 | 同上 | 同じ「キーの順にそろえた JSON の SHA-256」にする |
| `pick` のリポジトリのキー | `launch_approval.repo_key` | 同上 | 使う |
| `stage_of` | `contracts/worker/kinds.json` | `ls contracts/worker` | 作業の名前は同じ。工程の対応は持たないので表に置く |
| `raised`・`candidates`・`fallback` | — | `grep -rn "effort" src/ndf`（該当なし） | 新設する |
| `report_outcome`・`record_outcome` | `report_launch`・`record_cli`（#1959） | #1959 の詳細設計 | 同じ差し込みに関数を足す |
| `overlay_env` | `worker._worker_env` | `core/worker.py` | 一時的な環境の上書きを下の層へ移して共有する |
| `codex_usage`・`codex_cost` | `ud.usage_of` | `domains/records/usage_decide.py` | 4 つの値の形に合わせて使う |
| `unit_values` | `launch_costs`（#1959） | 同上 | 起動 1 回の費用に使う |
| `weighted`・`eliminated`・`probabilities`・`judgement` | 標準ライブラリの `random` | 設計書の決定 5 | 使う |
| `pick` | `participants.assign`（相手役の割り当て） | `core/converge/participants.py` | 役割の割り当てで、値は選ばない。新設する |
| `record_outcome`（2 つ）・`refresh` | `jsonfile`・`jsonl` | `domains/records/` | 読み書きに使う |
| `_step_env`・`_after_unit`・`_add_models`・`recorded_models`・`read_units`・`write_units` | `_add_outputs`・`read_json`・`write_json` | `core/plan/records.py`・`files.py` | 同じ読み書きを使う |
| `_record_experiment` | `drive._summary`・`criteria_decide.blocking` | `domains/crossreview/` | 重大の判定に使う |
| `main`・`_pick`・`_report` | `contracts.result`・`ndf.cli` のサブコマンドの形 | `src/ndf/cli.py` | 結果 JSON の組み立てを使う |
| `report_rows`・`experiment_values`・`retry_reason` | — | `grep -rn "def report" src/ndf/domains/records` | 使用量の合計の `usage.report` は別の集計。新設する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `table` | 工程とランタイムごとの置き換え | 選択肢 1 つ、`{}` の組 | 空の配列、同じ組、`floor` × 組の数 > 1、`min_units` 1、順位に無い effort |
| `stage_of` | 表の各行 | 工程が「無し」の組 | 表に無い組 |
| `pick` | 同じ単位は同じ組。並列でも 1 行 | `unit` なし、`enabled` 偽、片側の指定 | 壊れた設定。記録を書けない場合 |
| `raised` | 表の各行 | `opus` の `xhigh` は変えない | 順位に無いモデル名 |
| `fallback` | 基準へ切り替え、行が 1 つ | 基準の組の拒否は None | 2 件で候補から外れ、7 日で戻る |
| `candidates` | 打ち切りの期限の後に戻る | 全部の非基準が外れると基準だけ | — |

## claude の起動と拒否の判定

満たす決定: 決定 8、受け入れ条件 2・14・17

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `claude_launch` | `adapters/claude.py` | 変更 | 変えない | `ClaudeCall.effort` があれば `--effort` を足す |
| `_error_reply` | 同 | 変更 | 変えない | 4 つの条件（`is_error` が真、`api_error_status` が 404、4 つのトークン数が欄として在って 0、`modelUsage` が空）がそろう誤りを `Failure.MODEL_REFUSED` にする |
| `report_outcome` | 同 | 新設 | `report_outcome(assign_id: str, quality: Optional[float], detail: Mapping) -> None` | 差し込まれた処理の `record_outcome` を呼ぶ |
| `child_process_env` | `adapters/run_log.py` | 変更 | `child_process_env(extra: Optional[Mapping[str, Optional[str]]] = None)` | 値が None の名前を子の環境から外す |
| `overlay_env` | 同 | 新設 | `overlay_env(changes: Mapping[str, Optional[str]]) -> ContextManager[None]` | このプロセスの環境を一時的に上書きし、戻す |

- 拒否の条件は [allocation の「起動できない組」](./issue-2944-design-allocation.md#起動できない組)の表のとおりで、欄が無いことを 0 とみなさない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `report_outcome` | 書けない | 処理の例外・差し込みが無い | 呼び出し | 既定値（書かない）で続ける | 標準エラーに警告 | 単位は数えない |
| `overlay_env` | 中の処理の例外 | — | `finally` | 呼び出し元へ返す | — | 環境を戻す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_error_reply` | 404 と 4 つのトークン数の 0 と空の `modelUsage` | トークンの欄の欠け | 利用上限、通信の誤り、基準でも同じ判定 |
| `child_process_env` | None の名前が子に無い | 元から無い名前 | — |

## 判定の起動

満たす決定: 決定 3・6、受け入れ条件 9・12・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `ask` | `adapters/llm.py` | 変更 | 変えない | `request_id` を単位にして値を取得し、起動ごとに行を渡し、効果指標を書く |

- 2 回目は `retry="invalid_answer"`。`MODEL_REFUSED` で `fallback` が値を返せば、判定の打ち直しの回数に数えずに 1 回だけ打ち直し、`request_try` を 1 増やす。期限は残りの時間
- `outcome` は `valid`・`invalid`・`unknown`。効果指標は 1 回目が `valid` なら 1、`invalid` なら 0（2 回目で合っても 0）、`unknown` と起動できない場合は null
- 値を取得する作業場所は `Path.cwd()`。stop-wait の判定（`judge_child`）は変えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask` | 設定の誤り | `pick` | 起動の前 | 呼び出し元へ返す | `stopped`・`precondition` | 起動しない |
| `ask` | 回答が 2 回とも合わない | モデルの回答 | 形の確認 | 今までと同じ（`needs_human`） | 変えない | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `ask` | 使用量ログの行（`Choice.row_fields`・`request_id`・`request_try`・`outcome`）と効果指標 | 使用量ログ・実験の記録 | 質問と回答の本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `ask` | 起動の引数に組がある。行と指標が 1 つ | 2 回目で合うと指標は 0 | 拒否で基準へ 1 回、`request_try` が増える。`unknown` は null |

## worker の起動と Skill を回す起動

満たす決定: 決定 3・6・7、受け入れ条件 2・4・10・11・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_add_run` | `core/worker.py` | 変更 | 変えない | `--unit`・`--mode` を足す |
| `_cmd_run` | 同 | 変更 | 変えない | `--unit` が無ければ `NDF_UNIT_ID`、`pick` の `retry` には `NDF_RETRY_CAUSE` の値を渡し、結果の `metrics.launch` に値・割り付け ID・実際のモデルを載せる |
| `delegate` | 同 | 変更 | `delegate(handoff, system, *, wd, root, call, session)`（`Handoff` に `unit`・`mode`・`retry`・`charge_to`） | 会話の最初に値を決め、引数と環境を再開でも同じにする。拒否は 1 回だけ新しい会話で打ち直す |
| `_resume_until_written` | 同 | 変更 | `_resume_until_written(…, choice: Choice)` | 子ごとに値と `outcome` を行にする |
| `_call_skill` | `progress/invoke.py` | 変更 | 変えない | `pick("skill", run.name, unit=NDF_UNIT_ID)` の値で起動し、行にする |

- 再開は、最初の起動の `--model`・`--effort`・割り付け ID・`CLAUDE_CODE_EFFORT_LEVEL` の扱いを保持して使う（設計書の「同じ会話を続ける起動」）。最初の起動の子にこの変数が無かった場合は、再開の子から外す
- `charge_to`（費用を手戻りとして負う割り付け ID）は、作業が `fix`・`ci-fix` のときの `NDF_CHARGE_TO` の値である。ほかの作業は `charge_to` を書かず、子の環境から `NDF_CHARGE_TO` を外す
- `outcome` は、結果ファイルがあればその `status`、無ければ `no_result`、起動の失敗の回答は `unknown`。基準へ切り替える打ち直しは `RESUMES` に数えない
- `--mode` は、`ndf answer work` が作業の値に `mode` を持つ場合に渡す（`progress/gate/entry.py`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `delegate` | 設定の誤り | `pick` | 起動の前 | 呼び出し元へ返す | `stopped`・`precondition` | 起動しない |
| `delegate` | 基準の組の拒否 | 使えないモデル | `MODEL_REFUSED` と `fallback` が None | 止める | `stopped`・`precondition`。理由と `.ndf/launch.json` を示す | 設定を直して再実行 |
| `delegate` | 残りの期限が無い | 拒否に時間がかかった | 打ち直しの前 | 止める | `stopped`・`interrupted` | 再実行は基準の組 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `delegate` | 引数に組があり、再開も同じ | effort を指定せずに始めた会話の再開は変数を渡さない | 拒否で基準へ 1 回だけ。2 回目の拒否は止まる |
| `_cmd_run` | `metrics.launch` とモード | 単位なしは割り付けない | 引数の effort の誤り |
| `_call_skill` | 値と行 | 単位なし | 設定の誤りで呼び出し 0 回 |

## プランの実行

満たす決定: 決定 3・6・7、受け入れ条件 8・9・10・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `retry_reason` | `core/plan/transition_decide.py` | 新設 | `retry_reason(work: str, kind: Optional[str]) -> Optional[str]` | 修正の作業と停止理由から、`--retry` の値を返す |
| `_run` | 同 | 変更 | 変えない | 修正ステップへ入った回数を理由ごとに数え、2 回目以降の実行の指示に理由を持たせる |
| `_step_env` | `core/plan/runner.py` | 新設 | `_step_env(spec, step, act) -> dict[str, Optional[str]]` | `NDF_UNIT_ID`・`NDF_CHARGE_TO`・`NDF_RETRY_CAUSE` を付けるか外す |
| `_after_unit` | 同 | 新設 | `_after_unit(state_dir: Path, step, out: dict) -> None` | 割り付け ID を保持し、修正と作業場所の用意の効果指標を書く |
| `run_step` | 同 | 変更 | `run_step(spec, step_id, attempt, nth=1, retry=None)` | 上の 2 つを呼ぶ |
| `outcome_of` | `core/plan/steps.py` | 変更 | 変えない | 結果の `metrics.launch` を拾う |
| `_add_models`・`recorded_models` | `core/plan/records.py` | 新設 | `_add_models(state_dir, step_id, launch)`・`recorded_models(state_dir) -> dict` | ステップごとの指定した値と実際のモデルを残し、結果の `metrics.models` に載せる |
| `read_units`・`write_units` | 同 | 新設 | `read_units(state_dir) -> dict`・`write_units(state_dir, value)` | 工程の割り付け ID と、結果待ちの修正を持つ |

- 作業の名前は、ステップの引数が `ndf answer work` で始まる場合の最後の語。単位は、`design`・`impl` が `<run_id>:<工程>`、`fix`・`mechanical` が `<run_id>:<ステップ>`
- `retry_reason`: `fix-push` は `push_failed`、`fix-base` は `base_conflict`、`fix`・`ci-fix` は停止理由が `test_failed`・`check_failed`・`base_conflict` のときその値、ほかは None
- `NDF_CHARGE_TO` を付けるのは次の 2 つで、ほかは外す
  - worker でないステップ。この実行に `impl`（無ければ `design`）の割り付けがある場合
  - `fix`・`ci-fix` の worker。この実行に `impl` の割り付けがある場合
- 修正の効果指標は、修正ステップが 1 回目に完了した直後のステップが `ok` なら 1、止まれば 0。修正ステップ自体が止まれば 0、承認ゲートは書かない。`worktree` は完了が 1、停止が 0

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_after_unit` | 記録を書けない | 状態の保存先・実験の記録 | 書き込み | 続ける | 標準エラーに警告。ステップの結果は変えない | 単位は数えない |
| `outcome_of` | `metrics.launch` の形が違う | 古い worker | 読み取り | 既定値（無し）で続ける | — | — |
| `read_units` | 壊れたファイル | 書きかけ | 解析 | 空で続ける | 警告 | その実行の指標は書かない |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 耐久ステップの回復 | `units.json`・`models.json` | 1 つのプランは 1 本ずつステップを打つ | 回数は遷移の状態が持つので回復で同じ値になる。同じ割り付け ID の効果指標は、先に書いた行だけが有効 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `retry_reason`・`_run` | テストの失敗の集計ステップ `test-repeat` と CI の失敗の集計ステップ `repeat-check` のどちらを通っても同じ理由 | 1 回目は理由なし | 表に無い停止理由 |
| `_step_env` | 単位の ID と `charge_to` | `impl` の無いプラン | 親から継いだ値が外れる |
| `_after_unit` | 次の検査の成否が指標 | 承認ゲート | 書けなくても結果が同じ |

## cross-review と相手役

満たす決定: 決定 3・4・7、受け入れ条件 3・4・13・15・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `review_launch`・`read_only_launch`・`reading_launch` | `core/converge/participants.py` | 変更 | 末尾に `choice: Optional[Choice] = None` | 組の引数と環境を足し、Codex は `usage_args` を足す |
| `answer_text` | 同 | 変更 | 変えない | `<出力>.answer` があれば、その本文を読む |
| `_start_and_watch` | `adapters/agents.py` | 変更 | 変えない | `AgentStatus.seconds` に起動から終了までの秒を入れる |
| `codex_usage` | 同 | 新設 | `codex_usage(out: Path) -> Optional[dict]` | `turn.completed` の行の `usage` を足し合わせる |
| `codex_cost` | `domains/records/usage.py` | 新設 | `codex_cost(usage: Mapping, model: Optional[str]) -> tuple[str, Optional[float]]` | 単価表から USD を返す。無いモデルは None |
| `_seat_launch`・`launch_reviewers`・`_seat_result` | `domains/crossreview/seats.py` | 変更 | `_seat_launch(run, seat, prompt, stem, choice)` | シートの単位で値を取得し、起動ごとに行を書き、Codex の回答を `answer_text` で読む |
| `_fixer_launch`・`_launch_fixer`・`_keep_fixer` | `domains/crossreview/fixer.py` | 変更 | 変えない | 修正担当の値と `charge_to`・`session_seq` を状態ファイルに残して再開に使う |
| `enter` | `domains/crossreview/journal.py` | 変更 | 変えない | 同じ指摘を挙げた担当を `raisers` にすべて残す |
| `experiment_values` | 同 | 新設 | `experiment_values(rows: Sequence, first: Collection[str], seats: Sequence[str]) -> tuple[int, dict[str, Optional[float]]]` | 1 ラウンド目の採用された重大な指摘の数と、シートごとの挙げた割合を返す |
| `_record_experiment` | `domains/crossreview/drive.py` | 新設 | `_record_experiment(run: _Run, final: str) -> None` | シートと、`NDF_CHARGE_TO` の単位の効果指標を書く |

- シートの単位は `<cross-review の実行の ID>:<シート>`。実行の ID は状態ファイルに 1 回だけ作り、打ち直しでも同じ値。1 ラウンド目で出た指摘の id も状態ファイルに残す（出し直しで行の `round` が変わるため）
- 採用された指摘は見送りで終わらなかった指摘、重大は critical か major。review の指標は、同じレビューに 2 つ以上のシートがあり、採用が 1 件以上の場合だけ値を持つ
- `_record_experiment` は、終わり方が収束かラウンドの上限のときに書く。結果を読めないシートがあって終わった場合と、誤りで終わった場合は書かない。design と impl の指標は `1 / (1 + 1 ラウンド目の採用された重大な指摘の数)`
- 修正担当は `pick("seat", "fix")`（工程が無いので実験の引数を足さない）。環境変数と引数の指定は効く
- シートの子の環境から `NDF_CHARGE_TO` を外す
- `codex exec --json` で、利用上限とサンドボックスの失敗が今の検出（`agents.usage_limit`・`sandbox_failed`。行頭の文字列を読む）に掛かるかは未確認である。実装で失敗する条件を 1 回起動し、掛からなければ検出を `--json` の出力の形に合わせる
- Codex の行は `session_seq` 0、費用は `codex_cost` の値を `session_cost_usd` に書く。単価が無ければ欄を置かず、`unpriced` の行を足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `answer_text` | 回答のファイルが無い | 途中で終わった | 読み取り | 呼び出し元へ返す（回答なし） | 今の「読めなかった」と同じ | 起動の前に前の回答のファイルを消す |
| `codex_usage` | 使用量の行が無い | 失敗した起動 | 解析 | None | 行の `usage` が `unknown` | — |
| `codex_cost` | 単価が無い | 新しいモデル | 単価表 | None | `report` に不明の件数 | 単価表を足すと次から計算する |
| `launch_reviewers` | 設定の誤り | `pick` | 起動の前 | 呼び出し元へ返す | `stopped`・`precondition` | どのシートも起動しない |
| `_record_experiment` | 書けない | 実験の記録 | 追記 | 続ける | レビューの結果は変えない | 単位は数えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_launch` | `-c`・`-m`・`--ephemeral`・`--json`・`-o` | 指定なしの組 | cross-refactoring の起動は変わらない |
| `codex_usage`・`codex_cost` | 実際の出力の見本 | モデルの指定なしは `default_model` | 単価の無いモデル |
| `experiment_values` | 割合と件数 | 採用 0 件は null、シート 1 つは null | 見送りと後のラウンドを数えない |
| `_record_experiment` | 収束で 1 回 | `NDF_CHARGE_TO` なし | 打ち直しで 2 行目が効かない |
| `_launch_fixer` | 再開の値と `charge_to` が初回と同じ | 新しい会話は取り直す | — |

## 効果指標と出現率

満たす決定: 決定 4・5・7、受け入れ条件 6・15・16・17・18・20

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_outcome` | `domains/records/experiments.py` | 新設 | `record_outcome(assign_id: str, quality: Optional[float], detail: Mapping) -> bool` | 割り付けを引いて指標の行を足し、`refresh` を呼ぶ |
| `unit_values` | 同 | 新設 | `unit_values(rows, usage_rows, *, repo: str, stage: str, runtime: str, key: str, now: datetime) -> list[UnitValue]` | 割り付け ID ごとに効果・費用・確率・組をまとめ、使えない単位を除く |
| `refresh` | 同 | 新設 | `refresh(assign: Mapping) -> bool` | 計算し直す条件に当たれば、出現率の項目を置き換える |
| `record_outcome` | `domains/records/usage.py` | 新設 | 同じ引数 | 差し込みの受け口。`experiments.record_outcome` を呼ぶ |
| `weighted` | `domains/records/allocation_decide.py` | 新設 | `weighted(units) -> Stats` | 重み付きの平均と有効な件数 |
| `eliminated` | 同 | 新設 | `eliminated(units, arms, margin, window_days, seed) -> dict[int, datetime]` | 打ち切る組と期限 |
| `probabilities` | 同 | 新設 | `probabilities(units, candidates, allocation, seed) -> Optional[Entry]` | 段階と確率。件数が足りなければ `burn_in` |
| `judgement` | 同 | 新設 | `judgement(units, candidates, seed) -> Judgement` | 「必要な件数」の表の 4 つの表示 |

- 計算の式、順番、乱数の種、単位の除き方は [allocation](./issue-2944-design-allocation.md#出現率の計算) のとおり。`allocation_decide` はファイルも時刻も読まない
- `record_outcome`
  - 事後条件: 同じ割り付け ID の指標の行があれば足さない。指標の名前とバージョンは、割り付けの工程の表から引く
  - 副作用: 実験の記録への追記と、出現率のファイルの置き換え

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_outcome` | 割り付けが無い | 別のマシンの ID、消された記録 | 検索 | 偽を返す | 警告 | — |
| `refresh` | 計算か書き込みの失敗 | ログの読み取り・権限 | 例外 | 前の出現率のまま続ける | 警告 | 次の指標で計算し直す |
| `refresh` | 出現率のファイルが壊れている | 書きかけ | 解析 | 項目が無いとして作り直す | 出力なし | 冪等 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 複数の `refresh` | `launch-weights.json` | 計算はロックの外。書くときだけロックの中で読み直し、自分の項目を置き換える（一時ファイルから `os.replace`） | 後で書いた側が残る。同じ単位の集まりからは同じ値 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `record_outcome` | 行が 1 つ、計算が呼ばれる | 前の計算から 1 時間以内 | 2 回目は足さない |
| `unit_values` | 手戻りの費用を含む | 費用 0 と不明は除く、期間を過ぎた割り付け直し | 別のリポジトリ、モデルが変わった単位 |
| `probabilities` | 合計 1、下限以上 | `min_units` の前後 | 効果の下限を外れる安い組 |
| `eliminated` | 0.99 を超える組 | 30 件の前後、期限の後に戻る | 基準は打ち切らない |
| `judgement` | 4 つの表示 | 30 件ちょうど | そろって候補だった単位が無い |

## `ndf models` と `ndf delegate-args`

満たす決定: 決定 1・8、受け入れ条件 3・4・6・8・9・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `domains/records/models_cli.py` | 新設 | `main(argv: list[str]) -> int` | `pick`・`report` を振り分ける |
| `_pick` | 同 | 新設 | `_pick(a: Namespace) -> int` | `json` は結果 JSON、`args` は 1 行 1 引数 |
| `_report` | 同 | 新設 | `_report(a: Namespace) -> int` | 使用量ログと実験の記録を読んで結果を出す |
| `report_rows` | 同 | 新設 | `report_rows(usage_rows, experiment_rows, since, until, stage) -> list[dict]` | 設計書の「集計」の順と区分で値を組む |
| `main` | `domains/settings/delegate_args_cli.py` | 新設 | `main(argv: list[str]) -> int` | `pick("seat", "delegate", runtime)` と `safety_args(runtime, "delegate", cwd)` の引数を続けて出す |

- 引数を出す 2 つ（`_pick` の `args` の形と、`delegate-args` の `main`）は、`InternalError` を自分で受けて標準エラーへ書き、標準出力に何も出さずに 0 以外で終える。`delegate-args` は、モデルと effort の引数と安全装置の引数の両方がそろってから、1 回で書き出す
- `report` は呼ばれた時点で計算し直し、出現率のファイルを書き換えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_pick` | 設定の誤り | `pick` | 呼び出し | 止める | `json` は `stopped`、`args` は標準エラーだけ | 再実行できる |
| `_report` | 記録が無い | 初回 | 読み取り | 続ける | 「記録なし」 | — |
| `main`（delegate-args） | 片方の失敗 | 設定・承認 | 呼び出し | 止める | 標準出力は空、終了コードは `precondition` | 再実行できる |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_report` | 設計書の `ndf models report` の出力 | 結果 JSON | 作業の本文、指摘の本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `report_rows` | 依頼・ステップ・単位にまとめてから区分へ入れる | 期間の境をまたぐ依頼 | すべて拒否された依頼、重なった `launch_id` |
| `main`（delegate-args） | モデルと安全装置の引数が続く | 引数 0 個 | 安全装置の停止で標準出力が空 |
