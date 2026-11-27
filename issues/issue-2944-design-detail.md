# #2944 の詳細設計: モデルと effort の実験

設計文書 [issue-2944-design.md](./issue-2944-design.md) と [issue-2944-design-allocation.md](./issue-2944-design-allocation.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは断りが無ければ `plugins/ndf/` からの相対で書く。#1959 を実装した後のコード（[issue-1959-design-detail.md](./issue-1959-design-detail.md)）を前提にする。語の意味は設計書の「解き方の全体像」と「用語」にある。この文書の `layer`（層）は起動の種類で、コードの層（adapters など）とは別である。

## 地図

パッケージ・モジュール構成（触るファイルのうちコード。`*` は新設）:

```text
src/ndf/
  cli.py                              models の行
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
    settings/delegate_run_cli.py     ndf delegate-run
    crossreview/seats.py・fixer.py・journal.py・drive.py  シートの単位・修正担当・指摘の担当・効果指標
```

依存の向き: 層は下から contracts・adapters・core・progress・domains の順で、下の層は上の層を読み込まない。値を選ぶ処理は全部の層から呼ぶので adapters に置き、割り付けに要る実験の記録と出現率のファイルの読み書きもそこに置く。`domains/records/experiments.py` は、adapters のこの読み書きを使って読む。adapters と core から出現率の計算（domains）を呼ぶ所は、下の層が上の層を読み込めないため、#1959 の起動記録のインターフェースに効果指標の関数を足して、差し込みで呼ぶ。

Claude の起動処理・判定・worker・Skill の起動処理は、`model_choice` を直接 import しない。モジュールの依存関係の検査（`checks/children.json`）がこの import を禁止しているためである。代わりに `adapters/claude_choice.py` が、値を選ぶ処理を受け取る口（`set_launch_chooser`）と、選んだ値で起動する部品（`ChoiceRun`・`report_outcome`）を持つ。`ndf.__main__` は、`claude_choice.set_launch_chooser` を通じて選択処理（`adapters/model_chooser.py`）を登録する。使用量ログの書き込み処理（`claude.set_launch_recorder`）と同じ形である。エントリポイントを通らない呼び出しとテストは選択処理を登録しないので、値を選ばずに起動する（2026-11-27、B3 の実装で決定）。

| パス | 種類 | 役割 | 依存する先 | クラスまたはモジュール | 新設（N）と変更（C） |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/adapters/model_choice.py` | 新設 | 値と出所を返す | launch_settings・launch_approval | `Choice` | N: `table`・`bundled_errors`・`stage_of`・`launch_choice`・`raised`・`base_fallback`・`candidates`・`compatibility_key`・`read_experiments`・`ledger`・`append_row`・`read_weights`・`update_weights`・`weights_entry`・`probs_fit`・`entry_fits`・`model_group`・`assigns`・`records_rule`・`utc_now`（B2 で出現率の計算が使う読み書きと時計を公開した）・`refused_until`・`unpriced_models`・`oldest_kept`（B2b） |
| `src/ndf/adapters/experiment_rows.py` | 新設（B2b） | 実験の記録の行を読むだけの判定 | — | `Where` | N: `records_rule`・`assigns`・`candidates`・`refused_until`・`observed_models`・`observed_latest`・`unpriced_models`・`model_group`・`probs_fit`・`entry_fits`。`model_choice` が同じ名前で公開し直す。`model_choice` が行数の安全網（1,000 行）を超えるので、ファイルの読み書きと時計を使わない判定をここへ分けた |
| `src/ndf/adapters/launch_settings.py` | 変更 | `experiments` を返す | — | `Settings` | C: `load` |
| `src/ndf/adapters/claude.py` | 変更 | 引数と拒否の判定 | — | `ClaudeCall`・`LaunchRecorder`・`LaunchLog` | C: `claude_launch`・`_error_reply`。N: `model_refused` |
| `src/ndf/adapters/claude_choice.py` | 新設 | 値を選ぶ処理の差し込みと、選んだ値で起動する部品 | claude | `LaunchChooser`・`ChoiceRun` | N: `set_launch_chooser`・`pick_values`・`report_outcome` |
| `src/ndf/adapters/model_chooser.py` | 新設 | 差し込む値を選ぶ処理（`model_choice` の値の選択と切り替えに、実際のモデルの行を足す） | model_choice | モジュール | N: `observed` |
| `src/ndf/__main__.py` | 変更 | 値を選ぶ処理の差し込み | claude_choice・model_chooser | モジュール | 差し込みの 1 行 |
| `src/ndf/adapters/llm.py` | 変更 | 判定の単位 | claude_choice | モジュール | C: `ask` |
| `src/ndf/adapters/run_log.py` | 変更 | 子の環境 | — | モジュール | C: `child_process_env`。N: `overlay_env` |
| `src/ndf/adapters/agents.py` | 変更 | 監視の結果 | — | `AgentStatus` | C: `_start_and_watch`。N: `codex_usage` |
| `src/ndf/core/worker.py` | 変更 | worker の起動 | claude_choice | `Handoff` | C: `_add_run`・`delegate`・`_resume_until_written`・`_cmd_run` |
| `src/ndf/core/converge/participants.py` | 変更 | 相手役の引数 | model_choice | モジュール | C: `review_launch`・`read_only_launch`・`reading_launch`・`answer_text` |
| `src/ndf/core/plan/transition_decide.py` | 変更 | 打ち直しの理由を数える | — | `RunStep`・`StepState` | N: `retry_reason`。C: `_run` |
| `src/ndf/core/plan/runner.py` | 変更 | 単位の ID と効果指標 | model_choice・claude | モジュール | C: `run_step`・`final_result`。N: `_step_env`・`_after_unit` |
| `src/ndf/core/plan/steps.py` | 変更 | 子の結果の起動の値を拾う | — | モジュール | C: `outcome_of` |
| `src/ndf/core/plan/model.py` | 変更 | ステップの定義の読み取り | — | `Step` | C: `verifies` を読む |
| `src/ndf/core/plan/records.py` | 変更 | 回をまたぐ記録 | — | モジュール | N: `_add_models`・`recorded_models`・`read_units`・`write_units` |
| `src/ndf/progress/invoke.py` | 変更 | Skill の起動 | claude_choice | モジュール | C: `_call_skill` |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf worker run` の引数に `--mode` を足す | — | モジュール | 呼ぶ引数を足すだけ |
| `src/ndf/domains/records/usage.py` | 変更 | 行と指標の受け口 | experiments | モジュール | N: `codex_cost`・`record_outcome`（`experiments.write_outcome` を呼ぶ） |
| `src/ndf/domains/records/experiments.py` | 新設 | 効果指標と出現率 | model_choice・allocation_decide | `UnitValue`・`UsageIndex` | N: `write_outcome`・`unit_values`・`unit_pool`・`pick_units`・`usage_index`・`seed_of`・`refresh`・`records_since`・`rows_for_units`（B2b） |
| `src/ndf/domains/records/allocation_decide.py` | 新設 | 計算 | — | `Entry`・`Judgement`・`Stats`・`Verdict`（単位は型 `Unit`、乱数は型 `Sampler` で受ける） | N: `weighted`・`comparable`（`report` も比べる単位の絞り込みに使う）・`eliminated`・`probabilities`・`judgement` |
| `src/ndf/domains/records/usage_decide.py` | 変更 | `launch_id` ごとの行と費用 | — | モジュール | N: `launch_rows` |
| `src/ndf/domains/records/models_cli.py` | 新設 | `ndf models` | model_choice・experiments・allocation_decide・usage・usage_decide | モジュール | N: `main`・`_pick_command`・`_report`・`report_rows` |
| `src/ndf/domains/settings/delegate_run_cli.py` | 変更 | 委譲の起動にモデルと effort を足す | model_choice・launch_settings | モジュール | C: `main` |
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
| `contracts/domains/ndf-files.json` | `launch-experiments-<YYYY-MM>.jsonl`・`launch-weights.json`・`launch-pruned`（保存先は使用量ログと同じ） |
| `contracts/schemas/ndf-settings/launch.schema.json` | `experiments` のキー |
| `contracts/schemas/internal/experiment.schema.json`（新設）・`experiment-weights.schema.json`（新設。B2）・`usage.schema.json` | 実験の記録の行の形、出現率のファイルの形（内部データの種類 `experiment-weights`）、使用量ログの足す欄（`outcome` は名前の対応表 `contracts/data/internal-names.json` の行「起動の終わり方」） |
| `contracts/schemas/io/models.*`・`docs/contracts/*.md` | 引数のスキーマと規約のページは生成し、結果のスキーマは手で書く |
| `skills/external-ai/SKILL.md`・`references/cli-codex.md`・`cli-agy.md`・`cli-kiro.md`・`skills/qa-security-scan/03-report-template.md`・`agents/corder.md` | 固定の `medium` を消す。起動の例の `ndf delegate-run`（#1959）は変えない |
| `contracts/templates/parts/steps.json` | 検査のステップに `verifies`（そのステップが確かめる修正の作業の名前）を足す |
| `skills/launch-config/SKILL.md` | `experiments` の書き方。承認の対象ではないこと |

## 値を選ぶ処理

満たす決定: 決定 1・2・3・6・8、受け入れ条件 1・2・7・10・12・14・15・19

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `table` | `adapters/model_choice.py` | 新設 | `table(root: Path, settings: Optional[Settings] = None) -> Table` | 同梱とリポジトリの `experiments` を重ね、値の制約を検証する。`settings` を渡されれば `.ndf/launch.json` を読み直さずに使う。同梱の値はスキーマに照らさない（配布の後に変わらないので、CI のテストが `bundled_errors` で確かめる） |
| `bundled_errors` | 同 | 新設 | `bundled_errors() -> list[str]` | 同梱の値が起動の設定のスキーマに合わない箇所を返す。起動の経路では呼ばず、CI のテストが呼ぶ |
| `stage_of` | 同 | 新設 | `stage_of(layer: str, name: str) -> Optional[str]` | 層と作業の名前から工程を引く |
| `launch_choice` | 同 | 新設 | `launch_choice(layer: str, name: str, runtime: str = "claude", *, cwd: Path, environ: Optional[Mapping[str, str]] = None, settings: Optional[Settings] = None, **request: Any) -> Choice` | 優先の順で値を決め、要れば割り付けて引き上げる。`settings` は `table` へ渡す。`request` は `Request` の欄を名前で受ける |
| `Request` | 同 | 新設 | `unit: Optional[str] = None`、`charge_to: Optional[str] = None`、`retry: Optional[str] = None`、`mode: Optional[str] = None`、`argument: tuple[Optional[str], Optional[str]] = (None, None)`、`argument_at: tuple[str, str] = ("引数 --model", "引数 --effort")` | `launch_choice` の `request` が受け付ける欄。ほかの名前を渡すと `TypeError` になる。`argument` はモデルと effort の引数の指定、`argument_at` は誤りの文でその引数を指す言い方 |
| `raised` | 同 | 新設 | `raised(runtime: str, model: Optional[str], effort: Optional[str]) -> tuple[Optional[str], Optional[str]]` | 設計書の「引き上げる起動」の上げ方の表を当てる |
| `base_fallback` | 同 | 新設 | `base_fallback(choice: Choice, reason: str) -> Optional[Choice]` | 切り替える条件（`Choice.switchable`。割り付けた、基準でない組）に当たれば `launch_error` の行を書き、基準の組の値を返す。当たらなければ None。行は `append_row(row, choice.store)` で、割り付けたときと同じ保存先に書く。呼び出しで環境変数を差し替えて保存先が既定と違っても、次の起動が同じ保存先から切り替えを読めるようにするためである |
| `candidates` | 同 | 新設 | `candidates(arms: Sequence, rows: Sequence, entry: Optional[Mapping], now: datetime, where: Where, *, refused: Optional[Mapping[int, datetime]] = None) -> list[int]` | 打ち切った組と起動できない組を除いた添字。`refused` は呼び手が求めた `refused_until` の結果で、渡されれば記録を走査し直さない。`launch_error` は割り付け ID で割り付けの行を引き、`where`（`repo`・`stage`・`runtime`・`key` を持つ組）が同じものだけを数える。引数の多い関数の検査（PLR0913）に合わせ、4 つのキーを 1 つの値にまとめた |
| `refused_until` | 同（本体は `adapters/experiment_rows.py`） | 新設（B2b） | `refused_until(arms, rows, now: datetime, where: Where) -> dict[int, datetime]` | 起動できない組 → 候補へ戻る日時。戻る日時は、新しいほうから `refusals` 件目の `launch_error` の行の日時に `refusal_days` を足した日時。`candidates` と `report` の `dropped_until` が同じ規則を使う |
| `unpriced_models` | 同（本体は `adapters/experiment_rows.py`） | 新設（B2b） | `unpriced_models(rows, where: Where, start: datetime) -> list[str]` | `start` 以降に割り付けた同じキーの単位の、費用を計算できないモデル。割り付けの `uniform` の判定と `report` の表示が同じ条件を使う |
| `oldest_kept` | 同 | 新設（B2b） | `oldest_kept(root: Optional[Path] = None) -> Optional[datetime]` | 保存先に残っている最も古い月のファイルの月初め（UTC）。月の掃除は保存先を共有するリポジトリの最長の `window_days` で消すので、今の設定からは決まらず、実際のファイルから求める |
| `compatibility_key` | 同 | 新設 | `compatibility_key(arms: Sequence, metric: str) -> str` | 選択肢の並びと指標のハッシュ |
| `read_experiments` | 同 | 新設 | `read_experiments(since: datetime, root: Optional[Path] = None) -> list[dict]` | `since` の月から今月までの実験の記録を読む（ロックを取らない） |
| `ledger` | 同 | 新設 | `ledger(since: datetime, root: Optional[Path] = None, older: Optional[Callable[[Sequence[Mapping]], Optional[datetime]]] = None) -> ContextManager[Ledger]` | 設計書の「読み書き」の順で、読んだ行（`rows`）と追記（`append`）を、実験の記録のロックの中で渡す。`older` は任意で、`since` の月から読んだ行を受け取り、さらに古い月から読む日時か None を返す。日時を返せば、その月から `since` の前の月までを、ロックを取る前に読み足す（古い月には追記されないため）。`Ledger.since` は読み始めた日時で、読み足したときはその日時になる |
| `append_row` | 同 | 新設 | `append_row(row: Mapping, root: Optional[Path] = None) -> bool` | `ledger` を開いて 1 行足す。`root` は記録の保存先で、省くと既定の保存先（`records_dir()`）に書く。書けなければ警告を出して偽を返す |
| `prune` | 同 | 新設 | `prune(now: datetime, window_days: int, root: Optional[Path] = None) -> None` | [allocation の「保持する期間」](./issue-2944-design-allocation.md#実験の記録と出現率)の月の掃除。`window_days` は今の設定の値である。残す日数は、この値と、記録に残る割り付けの行の `window_days` のうち最も大きい値に 30 日を足した日数で、`now` からその日数を引いた日時より前に終わった月のファイルを消す。`launch-pruned` の月が今月なら何もしない。違えば、古い月のファイルと出現率の項目を消し（出現率のファイルはロックの中で読み直してから変える）、全部成功したら今月を書く。`launch_choice` が呼び、失敗しても起動は続ける |

- `Choice` は `layer`・`name`・`runtime`・`stage`・`mode`・`model`・`effort`・`model_source`・`effort_source`・`unit`・`assign_id`・`raised`・`arm`・`base_arm`・`store`（割り付けに使った記録の保存先。決められなければ None）を持つ。返すものは、`cli_args() -> list[str]`（claude は `--model`・`--effort`、codex は `-c model_reasoning_effort=…`・`-m`）、`env_changes() -> dict[str, Optional[str]]`（`CLAUDE_CODE_EFFORT_LEVEL` を外すか引き継ぐか。None は外す）、`row_fields() -> dict`（使用量ログの欄。`unit` は割り付けを受けた起動、つまり `assign_id` がある起動だけに置く）を返す
- `launch_choice`
  - 事前条件: `layer` は `worker`・`skill`・`judge`・`seat`
  - 事後条件: 引数か環境変数で、モデルと effort の片方でも指定があれば割り付けず、無い側は基準の組の値。`unit` が無い場合、`enabled` が偽の場合、選択肢が 1 つの場合は、基準の組の値（出所は `base`、組が値を持たなければ `default`）で、割り付けの行を書かない。停止（`enabled` が偽）・固定（選択肢が 1 つ）・互換性のキーの変更で基準を使う単位に割り付けがあれば `halted` の行を足す。引数か環境変数の指定がある起動は、`unit` があれば割り付けの有無に関わらず理由 `manual` の `halted` の行を足し、`charge_to` があればその割り付けの単位にも足す。同じ単位の同じ理由の行は重ねて足さない。工程が無い層と作業の名前の起動には、引数を足さない（環境変数と起動の引数の指定は効く）
  - 副作用: `ledger` の中で、割り付けの判定と行の追記をする
- ロックを取るのは `ledger` だけである。`Ledger.append` はロックを取らないので、`launch_choice` と `experiments.write_outcome` は開いた `ledger` の `append` を呼び、`append_row` を中から呼ばない（同じロックを取り直さない）。拒否・実際のモデル・`unpriced` の行のように、`ledger` の外から 1 行を足す呼び出しは `append_row` を使う
- `Ledger.append` は、行の形を確かめ、記録が改行で終わらなければ書きかけの行を閉じるバイト列（`launch_records.TORN_END`）を先に足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `table` | 設定の誤り | 形、順位に無い effort、無い工程・層・ランタイム、値の制約 | 読み込みの直後 | 呼び出し元へ返す（`InternalError("precondition")`） | `stopped`・`precondition`。ファイルとキーを示す | 起動しない |
| `launch_choice` | 値の誤り | 環境変数か引数の effort が順位に無い、モデル名に改行、表に無い `layer`・`retry` | 引数の確認 | 呼び出し元へ返す | 同上 | 起動しない |
| `launch_choice` | 記録を書けない | 権限・容量・ロックの待ち | 追記 | 既定値（指定なし、割り付け ID なし）で続ける | 標準エラーに警告 | 単位は実験に数えない |
| `read_experiments` | 壊れた行 | 書きかけ | 解析 | 飛ばして続ける | 出力なし | — |
| `base_fallback` | 切り替えの記録を書けない | 同上 | 追記 | 基準の値を返して続ける | 警告に起動できなかった組と基準で続けたこと | — |

### 処理の手順

`launch_choice` は次の順で見る（[allocation の「割り付けの流れ」](./issue-2944-design-allocation.md#割り付けの流れ)）。

1. 引数か環境変数の指定
2. 実験の停止（`enabled` が偽）と固定（選択肢が 1 つ）
3. この単位の、基準への切り替えか停止の記録
4. この単位の既にある割り付け（互換性のキーが今と違えば、停止の行を足して基準の組）
5. 候補の抽選

`retry` があれば、決まった組に `raised` を 1 回だけ当てる。引数と環境変数の値は上げない。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 工程・選択肢・順位・指標・割り付けの値 | 設定値 | `contracts/domains/launch-experiments.json` | 設計書の表 |
| 出現率を計算し直す間隔 | 設定値 | 同上の records の行の `refresh_minutes` | 60 分 |
| 起動できない組の条件 | 定数 | 同上 | 条件の本文は[割り付けの設計の「起動できない組」](./issue-2944-design-allocation.md#起動できない組)の「外す条件」と「受け入れる限界」にある |
| 実験の記録の行 | 型 | `contracts/schemas/internal/experiment.schema.json` | `assign`・`outcome`・`launch_error`・`halted`・`observed`・`unpriced` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並列の起動の割り付け | `launch-experiments-<YYYY-MM>.jsonl` | 全部の月で 1 つのロック。ロックの外で読み、中では追記分だけを読んで足す。ロックの中で出現率を計算しない | `window_days` の期間の中の同じ単位には同じ組を返すので、打ち直しても行は増えない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `read_experiments`・`ledger`・`append_row` | `domains/records/jsonl.py` | 層の順序（adapters は domains を読めない） | 使えない。`filelock` と `contracts` の形の確認を使う |
| `table` | `launch_settings.load` | #1959 の詳細設計 | 使う |
| `compatibility_key` | `launch_approval.digest` の形 | 同上 | 同じ「キーの順にそろえた JSON の SHA-256」にする |
| `launch_choice` のリポジトリのキー | `launch_approval.repo_key` | #1959 の詳細設計 | 使う。値の選択（単位のある起動）と起動の承認の確認（指定のある起動）の両方がキーを使い、それぞれがその時点で解く。承認の確認が、割り付けの記録を待つ前に解いたキーを使わないためである（[設計書の「受け入れる限界」](./issue-2944-design.md#受け入れる限界)）。同じ値をファイルから求める関数を別に持つと、`.git` と `commondir` を読む処理がほかの 3 か所と重なる（重複の検査） |
| `stage_of` | `contracts/worker/kinds.json` | `ls contracts/worker` | 作業の名前は同じ。工程の対応は持たないので表に置く |
| `raised`・`candidates`・`base_fallback` | — | `grep -rn "effort" src/ndf`（該当なし） | 新設する |
| `report_outcome`・`record_outcome` | `report_launch`・`record_cli`（#1959） | #1959 の詳細設計 | 同じ差し込みに関数を足す |
| `overlay_env` | `worker._worker_env` | `core/worker.py` | 一時的な環境の上書きを下の層へ移して共有する |
| `codex_usage`・`codex_cost` | `ud.usage_of` | `domains/records/usage_decide.py` | 4 つの値の形に合わせて使う |
| `unit_values` | `launch_costs`（#1959） | 同上 | 起動 1 回の費用に使う |
| `weighted`・`eliminated`・`probabilities`・`judgement` | 標準ライブラリの `random` | 設計書の決定 5 | 使う。判断のモジュールは `random` を読めない（規則 decide-pure）ので、`experiments` が種から `random.Random` を作って渡す |
| `launch_choice` | `participants.assign`（相手役の割り当て） | `core/converge/participants.py` | 役割の割り当てで、値は選ばない。新設する |
| `write_outcome`・`record_outcome`・`refresh` | `jsonfile`・`jsonl` | `domains/records/` | 使用量ログの読み取りに `jsonl` を使う。実験の記録と出現率のファイルは `model_choice` の `ledger`・`read_experiments`・`weights_entry`・`update_weights` を使う |
| `_step_env`・`_after_unit`・`_add_models`・`recorded_models`・`read_units`・`write_units` | `_add_outputs`・`read_json`・`write_json` | `core/plan/records.py`・`files.py` | 同じ読み書きを使う |
| `_record_experiment` | `drive._summary`・`criteria_decide.blocking` | `domains/crossreview/` | 重大の判定に使う |
| `main`・`_pick_command`・`_report` | `contracts.result`・`ndf.cli` のサブコマンドの形 | `src/ndf/cli.py` | 結果 JSON の組み立てを使う |
| `report_rows`・`experiment_values`・`retry_reason` | — | `grep -rn "def report" src/ndf/domains/records` | 使用量の合計の `usage.report` は別の集計。新設する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `prune` | 古い月のファイルと項目が消え、`launch-pruned` が今月になる | `window_days` ＋ 30 日の境（境にかかる月は残る）、年をまたぐ。今月のファイルが先にあっても掃除する。完了の記録が今月なら一覧を調べない | 削除に失敗すると `launch-pruned` を書かず、次の呼び出しがやり直す。起動は続く |
| `table` | 工程とランタイムごとの置き換え | 選択肢 1 つ、`{}` の組 | 空の配列、同じ組、`floor` × 組の数 > 1、`min_units` 1、順位に無い effort |
| `stage_of` | 表の各行 | 工程が「無し」の組 | 表に無い組 |
| `launch_choice` | 同じ単位は同じ組。並列でも 1 行 | `unit` なし、`enabled` 偽、片側の指定、割り付けの後の手動指定と選択肢の削除、月をまたぐ割り付け | 壊れた設定。記録を書けない場合 |
| `raised` | 表の各行 | `opus` の `xhigh` は変えない | 順位に無いモデル名 |
| `base_fallback` | 基準へ切り替え、行が 1 つ | 基準の組の拒否は None | 2 件で候補から外れ、7 日で戻る。別のリポジトリと別のキーの行は数えない |
| `candidates` | 打ち切りの期限の後に戻る | 全部の非基準が外れると基準だけ | — |

## claude の起動と拒否の判定

満たす決定: 決定 8、受け入れ条件 2・14・17

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `claude_launch` | `adapters/claude.py` | 変更 | 変えない | `ClaudeCall.effort` があれば `--effort` を足す |
| `_error_reply` | 同 | 変更 | 変えない | 4 つの条件（`is_error` が真、`api_error_status` が 404、4 つのトークン数が欄として在って 0、`modelUsage` が空）がそろう誤りを `Failure.MODEL_REFUSED` にする |
| `report_outcome` | `adapters/claude_choice.py` | 新設 | `report_outcome(assign_id: str, quality: Optional[float], detail: dict) -> None` | 差し込まれた処理の `record_outcome` を呼ぶ |
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

- 2 回目は `retry="invalid_answer"`。`MODEL_REFUSED` で `base_fallback` が値を返せば、判定の打ち直しの回数に数えずに 1 回だけ打ち直し、`request_try` を 1 増やす。期限は残りの時間
- 切り替えの前に、単調時計で残りの期限を測り、残っていれば `before_retry` で材料が古くなっていないかを確かめ、もう一度期限を測る。どちらかで止まれば `base_fallback` を呼ばず、実験の記録に拒否の行も書かず、基準の組を起動しない（使用量ログの起動の行の `model_refused` は残る）。止まった拒否は、割り付けの候補から外す件数に数えない（[allocation の「起動できない組」の「受け入れる限界」](./issue-2944-design-allocation.md#起動できない組)）
- 確かめを通れば、`base_fallback` が切り替えの行（`launch_error` の `switched` が真）を書いて基準の組の値を返す。記録を読み書きする間（錠の待ちを含む）も期限は進むので、書いた後にもう一度残りの期限を測る。残っていれば基準の組で起動し直し、残っていなければ起動しない
- 回答のスキーマの検査は起動 1 回につき 1 回にし、その結果から使用量ログの `outcome` と打ち直しの判断の両方を作る
- `outcome` は `valid`・`invalid`・`timeout`・`unknown`。`timeout` は、子プロセスを起動した後の期限切れ（`Failure.TIMEOUT`）である。効果指標は 1 回目が `valid` なら 1、`invalid` か `timeout` なら 0（2 回目で合っても、2 回目が期限切れでも 0 は変わらない）、`unknown` と起動できない場合は null
- 値を取得する作業場所は `Path.cwd()`。stop-wait の判定（`judge_child`）は変えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask` | 設定の誤り | `launch_choice` | 起動の前 | 呼び出し元へ返す | `stopped`・`precondition` | 起動しない |
| `ask` | 回答が 2 回とも合わない | モデルの回答 | 形の確認 | 今までと同じ（`needs_human`） | 変えない | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `ask` | 使用量ログの行（`Choice.row_fields`・`request_id`・`request_try`・`outcome`）と効果指標 | 使用量ログ・実験の記録 | 質問と回答の本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `ask` | 起動の引数に組がある。行と指標が 1 つ | 2 回目で合うと指標は 0 | 拒否で基準へ 1 回、`request_try` が増える。起動の後の期限切れは 0、`unknown` と起動できない場合は null |

## worker の起動と Skill を回す起動

満たす決定: 決定 3・6・7、受け入れ条件 2・4・10・11・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_add_run` | `core/worker.py` | 変更 | 変えない | `--unit`・`--mode` を足す |
| `_cmd_run` | 同 | 変更 | 変えない | `--unit` が無ければ `NDF_UNIT_ID`、`launch_choice` の `retry` には `NDF_RETRY_CAUSE` の値を渡し、結果の `metrics.launch` に値・割り付け ID・実際のモデルを載せる |
| `delegate` | 同 | 変更 | `delegate(handoff, system, *, wd, root, call, session)`（`Handoff` に `unit`・`mode`・`retry`・`charge_to`） | 会話の最初に値を決め、引数と環境を再開でも同じにする。拒否は 1 回だけ新しい会話で打ち直す |
| `_resume_until_written` | 同 | 変更 | `_resume_until_written(…, choice: Choice)` | 子ごとに値と `outcome` を行にする |
| `_call_skill` | `progress/invoke.py` | 変更 | 変えない | `launch_choice("skill", run.name, unit=NDF_UNIT_ID)` の値で起動し、行にする |

- `claude -p` の呼び出し元（worker・Skill・判定・Claude のシート）は、割り付けた単位の最初に実際のモデルが返った起動（引き上げと基準への切り替えを除く）で、`observed` の行を `append_row` で足す
- 再開は、最初の起動の `--model`・`--effort`・割り付け ID・`CLAUDE_CODE_EFFORT_LEVEL` の扱いを保持して使う（設計書の「同じ会話を続ける起動」）。最初の起動の子にこの変数が無かった場合は、再開の子から外す
- `charge_to`（費用を手戻りとして負う割り付け ID）は、作業が `fix`・`ci-fix` のときの `NDF_CHARGE_TO` の値である。ほかの作業は `charge_to` を書かず、子の環境から `NDF_CHARGE_TO` を外す
- `outcome` は、結果ファイルがあればその `status`、無ければ `no_result`、起動の失敗の回答は `unknown`。基準へ切り替える打ち直しは `RESUMES` に数えない
- worker・Skill・判定は、拒否を受けた起動が切り替える対象（`Choice.switchable`）かを最初に確かめる。割り付けの無い起動と基準の組の拒否は、期限が切れていても拒否のまま止まる（`precondition`）。次の確認と期限切れの扱いは、対象の起動だけに当てる
- worker と Skill は、拒否の行を書く前に、残りの期限と起動の承認の対象（`Grant.verify`）を確かめる。承認の対象が変わったか承認が取り消されていれば、行を書かずに `precondition` で止まる
- 基準の組で起動し直す起動は、最初の起動の前に決めた期限を超えない。`LaunchOptions.left` には、単調時計で管理する期限までの残り秒数を呼び出すたびに計算する関数を渡す。起動の部品はこの関数を次のように使う
  - 重い起動のスロットを待つ上限は、待ち始める時点（学習に使わない設定の確認の後）の元の上限と残り秒数の小さい方にする。残りが無ければスロットを取り始めない。残りが無くなってスロットを取れなければ `interrupted` で止まる
  - スロットを取った直後に残りが無ければ、承認の再確認と CLI の下限の確認をせずに `interrupted` で止まる
  - 子を起動する直前と、中継の起動で子の実行を許す直前に残りを確かめ、無ければ起動せず `interrupted` で止まる
  - 回答を待つ上限は、子を起動した後、待ち始める時点の残り秒数にする。中継の起動で子の起動の知らせを待つ上限も、残り秒数を超えない
- 残りが無くて起動し直せないときは、worker も Skill も、元のモデルの拒否ではなく期限切れの `interrupted` を返す
- `--mode` は、`ndf answer work` が作業の値に `mode` を持つ場合に渡す（`progress/gate/entry.py`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `delegate` | 設定の誤り | `launch_choice` | 起動の前 | 呼び出し元へ返す | `stopped`・`precondition` | 起動しない |
| `delegate` | 基準の組の拒否 | 使えないモデル | `MODEL_REFUSED` と `base_fallback` が None | 止める | `stopped`・`precondition`。理由と `.ndf/launch.json` を示す | 設定を直して再実行 |
| `delegate` | 切り替える対象の起動で残りの期限が無い | 拒否に時間がかかった | 打ち直しの前 | 止める。拒否の行も切り替えの行も書かない | `stopped`・`interrupted` | 再実行は、同じ単位の期間の中の割り付けを引き継ぎ、元の組で起動する。拒否の件数で候補から外す規則は新しく割り付けるときの抽選だけに効き、既にある割り付けには効かない（[allocation の「割り付けの流れ」](./issue-2944-design-allocation.md#割り付けの流れ)）。元の組がまた拒否されれば、判定と同じ手順で基準の組へ切り替える |

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
| `_add_models`・`recorded_models` | `core/plan/records.py` | 新設 | `_add_models(state_dir, run_id, step_id, launch)`・`recorded_models(state_dir, run_id) -> dict` | ステップごとの指定した値と実際のモデルを、実行 ID（`run_id`）と一緒に残す。結果の `metrics.models` には、結果を返す実行で起動したステップだけを載せる |
| `read_units`・`write_units` | 同 | 新設 | `read_units(state_dir) -> dict`・`write_units(state_dir, value)` | 工程の割り付け ID と、結果待ちの修正を持つ |

- 作業の名前は、ステップの引数が `ndf answer work` で始まる場合の最後の語。単位は、`design`・`impl` が `<run_id>:<工程>`、`fix`・`mechanical` が `<run_id>:<ステップ>`
- `retry_reason`: `fix-push` は `push_failed`、`fix-base` は `base_conflict`、`fix`・`ci-fix` は停止理由が `test_failed`・`check_failed`・`base_conflict` のときその値、ほかは None
- `NDF_CHARGE_TO` を付けるのは次の 2 つで、ほかは外す
  - worker でないステップ。この実行に `impl`（無ければ `design`）の割り付けがある場合
  - `fix`・`ci-fix` の worker。この実行に `impl` の割り付けがある場合
- 修正の効果指標は、修正ステップの 1 回目の実行を対象に、その修正を確かめる検査の結果で書く。修正の後に、定義の `verifies` にその修正の作業の名前を持つステップ（検査のステップ）の結果を実行順に見て、効果指標を決められる最初の結果を使う。前提の不足や期限切れで決められなければ、結果待ちの修正を残し、同じ実行で次に該当する検査を待つ。同じ検査の打ち直しも対象にする

| `verifies` を足す共通のステップ | 値 |
| --- | --- |
| `test`・`ci-local`（`ndf route test`）、`ci`（`ndf ci watch`） | `fix`・`ci-fix`・`fix-base` |
| `push` | `fix-push` |

- 検査が `ok` なら 1。停止理由が `test_failed`・`check_failed`・`push_rejected`・`base_conflict` なら 0、ほかの理由（前提の不足、期限切れなど）なら書かない
- 工程のモードは、工程の入力の `mode` を `NDF_WORKFLOW_MODE` でステップの子へ渡し、無ければ子の環境から外す。`launch_choice` は引数の `mode` が無ければこの値を行の `mode` にする
- プランの実行は `model_choice` を読み込めない（`checks/children.json`）ので、作業の工程は `claude_choice.pick_stage`（差し込まれた `stage_of`）で引く（実装で決定）
- 検査の前にある push・待ち・準備・承認ゲートのステップの結果は、指標に使わない。承認ゲートの承認や却下からは、1 も 0 も書かない。検査に届かずに実行が終われば欠測とし、行を書かない
- 修正ステップ自体が止まれば 0。`worktree` は完了が 1、停止が 0

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_after_unit` | 記録を書けない | 状態の保存先・実験の記録 | 書き込み | 続ける | 標準エラーに警告。ステップの結果は変えない | 単位は数えない |
| `outcome_of` | `metrics.launch` の形が違う | 古い worker | 読み取り | 既定値（無し）で続ける | — | — |
| `read_units` | 壊れたファイル | 書きかけ | 解析 | 空で続ける | 警告 | その実行の指標は書かない |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 耐久ステップの回復 | `units.json`・`models.json` | 1 つのプランは 1 本ずつステップを打つ | 回数は遷移の状態が持つので回復で同じ値になる。結果待ちの修正はステップごとに 1 件で持ち、同じ実行 ID・ステップ・実行回数を流し直しても増えない。同じ割り付け ID の効果指標は、先に書いた行だけが有効 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `retry_reason`・`_run` | テストの失敗の集計ステップ `test-repeat` と CI の失敗の集計ステップ `repeat-check` のどちらを通っても同じ理由 | 1 回目は理由なし | 表に無い停止理由 |
| `_step_env` | 単位の ID と `charge_to` | `impl` の無いプラン | 親から継いだ値が外れる |
| `_after_unit` | `verifies` を持つ検査の成否が指標 | 検査の前に push や承認ゲートがある。検査に届かず終わる | 書けなくても結果が同じ |

## cross-review と相手役

満たす決定: 決定 3・4・7、受け入れ条件 3・4・13・15・16

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `with_choice` | `core/converge/participants.py` | 新設 | `with_choice(spec: agents.Launch, choice: Optional[Any]) -> agents.Launch` | `review_launch`・`read_only_launch`・`reading_launch` が組み立てた起動に組の引数と環境を足し、Codex は `usage_args` を足す |
| `answer_text` | 同 | 変更 | 変えない | codex の `--json` の出力で `<出力>.answer` があれば、その本文を読む |
| `_start_and_watch` | `adapters/agents.py` | 変更 | 変えない | `AgentStatus.seconds` に起動から終了までの秒を入れる |
| `codex_usage` | 同 | 新設 | `codex_usage(out: Path) -> Optional[CodexUsage]` | `turn.completed` の行の `usage` を足し合わせる |
| `codex_cost` | `domains/records/usage.py` | 新設 | `codex_cost(used: Any, model: Optional[str]) -> tuple[str, Optional[float]]`（`used` は `agents.CodexUsage`） | 換算に使ったモデルの名前と、単価表から求めた USD を返す。単価の無いモデルの USD は None |
| `record_codex` | 同 | 新設 | `record_codex(used: Any, meta: Mapping[str, Any], choice: Any) -> None` | `caller` が `codex-exec` の行を使用量ログに書く。費用の欄と `unpriced` を記録する条件は、表の下の「Codex の費用と `unpriced` の記録」に従う |
| `_seat_launch`・`launch_reviewers`・`_seat_result` | `domains/crossreview/seats.py` | 変更 | `_seat_launch(run, seat, prompt, stem, choice)` | シートの単位で値を取得し、起動ごとに行を書き、Codex の回答を `answer_text` で読む |
| `_fixer_launch`・`_launch_fixer`・`_keep_fixer` | `domains/crossreview/fixer.py` | 変更 | 変えない | 修正担当の値と `charge_to`・`session_seq` を状態ファイルに残して再開に使う |
| `enter` | `domains/crossreview/journal.py` | 変更 | 変えない | 同じ指摘を挙げた担当を `raisers` にすべて残す |
| `experiment_values` | 同 | 新設 | `experiment_values(rows: Sequence, first: Collection[str], seats: Sequence[str]) -> tuple[Optional[int], dict[str, Optional[float]]]` | 1 ラウンド目の採用された重大な指摘の数（裁定していない重大な指摘があれば None）と、シートごとの挙げた割合を返す |
| `_record_experiment` | `domains/crossreview/drive.py` | 新設 | `_record_experiment(run: _Run, final: str) -> None` | シートと、`NDF_CHARGE_TO` の単位の効果指標を書く |

- シートの単位は `<cross-review の実行の ID>:<シート>`。実行の ID は状態ファイルに 1 回だけ作り、打ち直しでも同じ値。1 ラウンド目で出た指摘の id も状態ファイルに残す。後のラウンドで担当が同じ指摘をもう一度挙げると、ジャーナルの行の `round` がそのラウンドに書き換わり、1 ラウンド目の指摘と見分けられなくなるためである
- 採用された指摘は修正担当が直した（`fixed`）指摘で、見送りと、ラウンドの上限で終わって裁定していない指摘は含めない。重大は critical か major。review の指標は、同じレビューに 2 つ以上のシートがあり、採用が 1 件以上の場合だけ値を持つ
- `_record_experiment` は、終わり方が収束かラウンドの上限のときに書く。結果を読めないシートがあって終わった場合と、誤りで終わった場合は書かない。design と impl の指標は `1 / (1 + 1 ラウンド目の採用された重大な指摘の数)` で、数が None なら null
- 修正担当は `launch_choice("seat", "fix")`（工程が無いので実験の引数を足さない）。環境変数と引数の指定は効く
- シートの子の環境から `NDF_CHARGE_TO` を外す
- `codex exec --json` は失敗の理由を標準エラーに出さないので、利用上限とサンドボックスの失敗は標準出力のイベントからも検出する
  - 監視中の利用上限の検出は、`error`・`turn.failed` の行の文を `usage_limit` で調べる。`CodexTail` で追記された完全な行だけを読み、起動の終了時には改行のない最後の行も読む
  - サンドボックスの失敗の検出（`sandbox_failed`）は、`codex_events` で標準出力全体を読み、`type` が `item.completed` のイベントから `item.type` が `command_execution` のものを抽出する。その `item` の `exit_code` が 0 以外で、かつ `aggregated_output` が `bwrap:` で始まる場合に、サンドボックスの失敗と判定する
  - どちらも、標準出力の最初の行が `thread.started` イベントの場合だけ、後続の行を Codex のイベントとして扱う。最初の行がそうでなければ、後の行に `thread.started` があっても扱わない
  - コマンドの出力で見つけた失敗は、出力の行でなく固定の文を返す。返す値は PR のコメントに載り、出力の行は手元のパスやコマンドが出したデータを含み得るためである
- 回答のファイル（`<出力>.out.answer`）を回答先に指定した起動は、起動の前にそのファイルを消す（`_start`）。codex は失敗すると前の中身を残すためである。指定しない起動は消さない。`answer_text` が回答のファイルを読むのは codex の `--json` の出力だけなので、残った古い回答は読まれない
- 実装で設計の形を変えた理由（B5）。振る舞いは変えていない
  - 値を起動の組み立ての関数の引数にしないのは、引数を足すと上限（PLR0913。6）を超え、cross-refactoring が呼ぶ関数の形も変わるためである
  - 使用量を辞書で持たないのは、欄の名前の文字列を複数のファイルに置くと重複の検査が落とすためである
- Codex の使用量ログの行は `session_seq` が 0 である
- Codex の費用と `unpriced` の記録
  - 費用は `codex_cost` の値を、使用量ログの `session_cost_usd` に書く
  - 使用量が取れない起動と、単価の無いモデルの起動では、`session_cost_usd` を省く
  - 単価の無いモデルの起動に実験への割り付け ID がある場合は、その ID を付けて実験の記録に `unpriced` を記録する。割り付け ID が無い起動では記録しない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `answer_text` | 回答のファイルが無い | 途中で終わった | 読み取り | 呼び出し元へ返す（回答なし） | 今の「読めなかった」と同じ | 起動の前に前の回答のファイルを消す |
| `codex_usage` | 使用量の行が無い | 失敗した起動 | 解析 | None | 行の `usage` が `unknown` | — |
| `codex_cost` | 単価が無い | 新しいモデル | 単価表 | None | `report` に不明の件数 | 単価表を足すと次から計算する |
| `launch_reviewers` | 設定の誤り | `launch_choice` | 起動の前 | 呼び出し元へ返す | `stopped`・`precondition` | どのシートも起動しない |
| `_record_experiment` | 書けない | 実験の記録 | 追記 | 続ける | レビューの結果は変えない | 単位は数えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_launch` | `-c`・`-m`・`--ephemeral`・`--json`・`-o` | 指定なしの組 | cross-refactoring の起動は変わらない |
| `codex_usage`・`codex_cost` | 実際の出力の見本 | モデルの指定なしは `default_model` | 単価の無いモデル |
| `experiment_values` | 割合と件数 | 採用 0 件は null、シート 1 つは null | 見送り・裁定していない指摘・後のラウンドを数えない。1 ラウンド目に裁定していない重大な指摘があれば件数は None |
| `_record_experiment` | 収束で 1 回 | `NDF_CHARGE_TO` なし | 打ち直しで 2 行目が効かない |
| `_launch_fixer` | 再開の値と `charge_to` が初回と同じ | 新しい会話は取り直す | — |

## 効果指標と出現率

満たす決定: 決定 4・5・7、受け入れ条件 6・15・16・17・18・20

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write_outcome` | `domains/records/experiments.py` | 新設 | `write_outcome(assign_id: str, quality: Optional[float], detail: Mapping) -> bool` | 割り付けを引いて指標の行を足し、出現率を計算し直すかを判定する |
| `unit_values` | 同 | 新設 | `unit_values(rows, usage: UsageIndex, *, where: Where, now: datetime, window_days: int, observed: Optional[Mapping[str, str]] = None) -> list[UnitValue]` | 割り付け ID ごとに効果・費用・確率・組・実際のモデルをまとめ、使えない単位を除く |
| `usage_index` | 同 | 新設（B2b） | `usage_index(usage_rows) -> UsageIndex` | 使用量ログの全部の行から、割り付け ID ごとの単位の費用と最初の起動の実際のモデルを求める。どちらも全部の行を走査するので、実験のキーをいくつも集計する `report` は 1 回だけ求めて各キーへ渡す |
| `seed_of` | 同 | 新設 | `seed_of(where: Where, allocation: Mapping, units) -> int` | 乱数の種（工程・ランタイム・互換性のキー・割り付けの値・計算に使う単位の SHA-256） |
| `refresh` | 同 | 新設 | `refresh(assign: Mapping) -> bool` | 計算し直す条件に当たれば、出現率の項目を置き換える。書き換えるときに、期間を過ぎた項目も消す |
| `record_outcome` | `domains/records/usage.py` | 新設 | `record_outcome(assign_id: str, quality: Optional[float], detail: Mapping) -> bool` | 差し込みの受け口。`experiments.write_outcome` を呼ぶ |
| `weighted` | `domains/records/allocation_decide.py` | 新設 | `weighted(units) -> Stats` | 重み付きの平均と有効な件数 |
| `eliminated` | 同 | 新設 | `eliminated(units, arms, margin, window_days, rng) -> dict[int, datetime]` | 打ち切る組と期限 |
| `probabilities` | 同 | 新設 | `probabilities(units, candidates, allocation, rng) -> Entry` | 段階と確率。件数が足りなければ `burn_in` |
| `judgement` | 同 | 新設 | `judgement(units, candidates, allocation, rng, best: Optional[Sequence[float]] = None) -> Judgement` | 「必要な件数」の表の 4 つの表示。最良である確率は `probabilities` と同じ効果の下限（`allocation` の `margin`）を当てて出す。`best` は、同じ単位・候補・割り付けの値・乱数の種で `probabilities` が求めた最良である確率で、渡されれば標本を引き直さない。件数のしきい値はそれぞれが当てる |

- 計算の式、順番、乱数の種、単位の除き方は [allocation](./issue-2944-design-allocation.md#出現率の計算) のとおり。`allocation_decide` はファイルも時刻も読まない
- 実装で設計の名前と引数を変えた所（B2）。決定は変えていない
  - `experiments` の関数は `write_outcome`（設計では `record_outcome`）。同じ名前の関数が 2 つのファイルにあると重複の検査（copy-dup）が落とすため。`usage` の受け口は `record_outcome` のまま
  - `unit_values` はキーを 4 つの引数でなく `Where` で受け、期間 `window_days` を足した。引数の多い関数の検査（PLR0913。上限 6）に合わせ、`candidates` と同じ形にした
  - `allocation_decide` の 3 つの関数は、種の代わりに種から作った乱数（`random.Random`）を受ける。判断のモジュールは `random` を読めない（規則 decide-pure）ので、種を作る `seed_of` と乱数の生成は `experiments` が持つ
  - `probabilities` は件数が足りないときも `burn_in` の `Entry` を返す（None を返す場面が無い）
- 出現率のファイルの形は `contracts/schemas/internal/experiment-weights.schema.json` で、書く側（`update_weights`）と読む側（`read_weights`）の両方がこの形を確かめる
  - 添字と確率が選択肢に合うかは、選択肢を知る読み手（割り付けの `entry_fits`）と書き手（`refresh`）が `probs_fit` で確かめる。条件は [allocation の「割り付けの流れ」](./issue-2944-design-allocation.md#割り付けの流れ)にある「項目を使える条件」の添字と確率の 2 つで、添字はどの段階でも確かめ、確率は `adaptive` のときだけ確かめる
  - 合わない項目は、割り付けが使わず、候補から均等に抽選する（PR #4661 のレビューの r16-3: https://github.com/devbasex/ai-plugins/pull/4661 ）
- `write_outcome`
  - 事後条件: 同じ割り付け ID の指標の行があれば足さない。この確認と追記は、1 つの `ledger` の中で行う。指標の名前とバージョンは、割り付けの工程の表から引く。出現率を計算し直すかの判定は `ledger` を抜けてから行い、行を足さなかったときも行う。指標を書いた直後に中断した作業を再開したときに、その指標を出現率へ反映するためである
  - 読む範囲: 割り付けの行は、まず出現率の計算に要る期間の月から探す。作業ディレクトリの設定にある `window_days` と、同梱の記録規則（`records_rule()`）にある `refusal_days` を比べ、長いほうの日数がかかる月から割り付けの行を探す。見つからなければ、記録を残す最長の期間（365 日＋`kept_extra_days`）の始まりの月から、読んだ範囲の前の月までだけを読み足す。読み足しは、関数の表にある `ledger` の引数 `older` に、割り付けの行が無ければ最長の期間の始まりを返す関数を渡して指定する。読み足したかは `Ledger.since`（読み始めた日時）で分かる。読み足しは、最初の月と同じく実験の記録のロックを取る前に行う。古い月には追記されないので、ロックの中で読み直す必要は無い。ロックの中では、今月の追記分を読み足してから、割り付けと同じ割り付け ID の効果指標の行を確かめ、無ければ追記する。読んだ月を読み直さず、ロックも 1 回だけ取る。設定を読めなければ、最初から最長の期間で読む。効果指標は割り付けの後に書くので、割り付けが範囲にあれば同じ割り付け ID の指標の行も範囲にある
  - 計算への受け渡し: `ledger` で読んだ行を計算に渡し、月別のファイルを読み直さない。最長の期間まで読み足した場合だけ、計算に要る期間の月の行に絞って渡す（`experiments._read_range`）。読み足していなければ、読んだ行がそのまま計算に要る範囲なので絞らない。割り付けが読むのと同じ範囲にそろえ、期間の外の割り付けに付いた拒否や `observed` の行を候補とモデルの判定に数えないためである
- `refresh`
  - 作業ディレクトリのリポジトリが割り付けのリポジトリと同じかは、計算し直す条件に当たったときだけ `launch_approval.repo_key` で確かめる（`git rev-parse` を 1 回起動する）。条件は [allocation の「出現率の計算」](./issue-2944-design-allocation.md#出現率の計算) のとおりで、前の計算から間隔の内でも、項目が無い・候補か割り付けの値が違う・実際のモデルが今のモデルと違うときは当たる。違うリポジトリなら計算しない。条件に当たらない呼び出しは git を起動しない。項目は割り付けの行のリポジトリのキーで引く
  - 今のモデルの観測（`model_choice.observed_models`）は 1 回だけ求め、項目を使えるかの照合（`entry_fits` は観測の結果を引数に受ける）と単位の絞り込みの両方に使う
  - 単位を絞る今のモデルと、項目に書く実際のモデル（`models`）は、割り付けの `entry_fits` と同じ `observed` の行（`model_choice.observed_models`）から決める（[allocation の「実際のモデルが変わった単位は混ぜない」](./issue-2944-design-allocation.md#実験の記録と出現率)）
  - 副作用: 実験の記録への追記と、出現率のファイルの置き換え

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_outcome` | 割り付けが無い | 別のマシンの ID、消された記録 | 検索 | 偽を返す | 警告 | — |
| `refresh` | 計算か書き込みの失敗 | ログの読み取り・権限 | 例外 | 前の出現率のまま続ける | 警告 | 次の指標で計算し直す |
| `refresh` | 出現率のファイルが壊れている | 書きかけ | 解析 | 項目が無いとして作り直す | 出力なし | 冪等 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 再開の処理が重なった `write_outcome` | 実験の記録 | 確認と追記を同じロックの中で行う | 指標の行は割り付け ID ごとに 1 つ。壊れて 2 行ある記録は、どの集計も先に書いた行だけを使う |
| 複数の `refresh` と `prune` | `launch-weights.json` | 計算はロックの外。書き換えるときは同じロックを取り、ロックの中で最新の内容を読み直してから変える（一時ファイルから `os.replace`） | 同じ項目の計算は後で書いた側が残る。掃除は期限を過ぎた項目だけを消すので、新しい項目は失われない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write_outcome` | 行が 1 つ、計算の判定が呼ばれる。計算に要る期間で割り付けが見つかれば記録を読み直さず、項目が新しければ git を起動しない | 前の計算から 1 時間以内。実際のモデルが項目と違えば 1 時間以内でも計算し、新しいモデルの評価が無ければ `burn_in` を新しいモデルで書く | 2 回目は足さないが判定は行う。ロックを待つ間に先行の呼び出しが書いた行を見て足さない |
| `unit_values` | 手戻りの費用を含む | 費用 0 と不明は費用の平均から除き、効果は残す、期間を過ぎた割り付け直し | 別のリポジトリ、モデルが変わった単位、モデルが切り替わって元に戻った場合の期限切れの境界 |
| `probabilities` | 合計 1、下限以上 | `min_units` の前後、費用の分かる単位が 2 件未満 | 効果の下限を外れる安い組 |
| `eliminated` | 0.99 を超える組 | 30 件の前後、期限の後に戻る | 基準は打ち切らない |
| `judgement` | 4 つの表示 | 30 件ちょうど | そろって候補だった単位が無い |

## `ndf models` と `ndf delegate-run`

満たす決定: 決定 1・8、受け入れ条件 3・4・6・8・9・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `domains/records/models_cli.py` | 新設 | `main(argv: list[str]) -> int` | `pick`・`report` を振り分ける |
| `_pick_command` | 同 | 新設 | `_pick_command(a: Namespace) -> int` | `--args` が無ければ結果 JSON、あれば 1 行 1 引数 |
| `_report` | 同 | 新設 | `_report(a: Namespace) -> int` | 使用量ログと実験の記録を読んで結果を出す |
| `report_rows` | 同 | 新設 | `report_rows(usage_rows, experiment_rows, scope: Scope) -> list[dict]` | 設計書の「集計」の順と区分で値を組む。`Scope` は期間（`since` を含み `end` を含まない）・工程・割り付けの値（`allocation`）・リポジトリのキー（`repo`）を持つ。`_report` は、作業ディレクトリのリポジトリの今の設定（`model_choice.table`）の割り付けの値と、そのリポジトリのキーを渡す |
| `launch_rows` | `domains/records/usage_decide.py` | 新設 | `launch_rows(rows) -> list[tuple[Mapping, Optional[float]]]` | `launch_id` ごとに数える 1 行と起動 1 回の費用の組（`launch_costs` と同じ行の選び方） |
| `main` | `domains/settings/delegate_run_cli.py` | 変更 | 引数は #1959 のまま | codex は `settings = launch_settings.load(launch_settings.launch_root(cwd))` で起動の設定を読み、同じ `cwd` と `settings` を渡した `launch_choice("seat", "delegate", "codex", cwd=cwd, settings=settings, argument=(<コマンドが明示したモデル>, <コマンドが明示した effort>))` の引数と `safety_args("codex", a.use, cwd, settings=settings)`（用途は `--use` で受けた値。省けば `delegate`）の引数の並び、作業ディレクトリの引数（`-C <今のディレクトリ>`。ランタイムの表の `workdir_options` の先頭の名前）を、この順に続けて `{args}` に入れる。コマンドが `-m`・`--model`（ランタイムの表の `model_options`）でモデルを、`-c`・`--config`（同じく `effort_options`）の `model_reasoning_effort=<値>` で effort を明示すれば、その値を起動の引数として渡し（設計書の「値の優先」の順 1。下位の環境変数は確かめない）、明示した側の引数は足さない。kiro と agy は `launch_choice` を呼ばず、`safety_args` の引数だけを入れる |
| `safety_args` | `adapters/launch_settings.py` | 変更 | `safety_args(runtime: str, use: str, cwd: Path, settings: Optional[Settings] = None) -> SafetyArgs` | #1959 の判定表のまま。サンドボックスなしで起動する経路で、`settings` を渡されれば `.ndf/launch.json` を読み直さずに使う。起動の直前の照らし直し（`verify`）は、これまでどおりファイルを読み直して承認と照らす |

- `delegate-run` の codex の委譲では、`.ndf/launch.json` の読み取りと全体のスキーマ検証を、起動の引数を組み立てる段階で 1 回にまとめる。`launch_choice` と `safety_args` がそれぞれ読むと、サンドボックスなしの経路で読み取りと検証が 1 回ずつ増えるためである
  - サンドボックスなしで起動する経路では、起動の直前の承認の確認（`verify`）で、もう 1 回読み取りと検証を行う。`verify` は最初に読んだ `Settings` を使い回さず、ファイルを読み直す。使い回すと、引数を組み立てた後に `permissions.unsandboxed` から codex を外しても、古い設定のまま安全装置を外して起動するためである。読み取りと検証は合わせて 2 回になる
  - `verify` が見つけるのは、承認のハッシュの対象（`permissions` と、そこから参照する MCP の定義ファイル。工程の指定に `mcp: "inherit"` があればリポジトリ直下の `.mcp.json` も入り、無いこと自体も対象になる）の変更と、承認の取り消しだけである。読み直しで起動の設定か対象の定義ファイルを読めない・JSON でない・スキーマや定義の形に合わないときも、理由を返して起動を止める（`launch_settings._target_files`・`_mcp_definition`・`Grant.verify`）。`experiments` だけの変更は、スキーマに合う限り見つけず、委譲を止めない。モデルと effort は、引数を組み立てる段階で最初に読んだ設定から選ぶ
- `_pick_command` の `--args` の形は、`InternalError` を自分で受けて標準エラーへ書き、標準出力に何も出さずに 0 以外で終える。`delegate-run` の失敗の扱いは #1959 の詳細設計のとおりで、`launch_choice` の失敗も CLI を起動せずに同じ形で終える
- `report` は呼ばれた時点で計算し直し、出現率のファイルを書き換えない
- `report` の結果の `repo` は、パスを含まない識別子（`launch_approval.repo_digest`。リポジトリのキーのハッシュの先頭 16 桁）である。使用量ログは複数のリポジトリで共有し、結果は人に渡すことがあるので、キー（絶対パス）を出さない。打った作業ディレクトリのリポジトリの識別子は `metrics` の `repo` で返す
- `report` の結果の items は 2 種類である。`launch_group` は工程 × 行の指定値（リポジトリ・ランタイム・モード・モデル・effort）ごとの起動の値・成功率・やり直し率で、モードが区分に入るのでモードごとの内訳になる。`arm` は実験のキーの組ごとの単位の数・効果の平均と幅・費用の平均・ROI と基準の組との比・最良である確率・出現率（`adaptive` のときだけ）・段階と優劣の表示で、モードごとの単位の数を持つ。単位の無い組の表示は「記録なし」、件数を集める間は「件数を集めている」
- `report` の期間は、`--since` の日の 0 時（JST）を含み、`--until` の翌日の 0 時（JST）を含まない。使用量ログと実験の記録は日時をマイクロ秒まで持つので、最後の日の 23:59:59 で切ると、その 1 秒の間の行が連続する日付のどちらの集計にも入らないためである
- 実験の組（`arm`）は、作業ディレクトリのリポジトリの割り付けだけを集計する。打ち切り・段階・出現率の計算には、作業ディレクトリのリポジトリにある `.ndf/launch.json` の `experiments.allocation` を使い、この値はリポジトリごとに違う。記録の保存先は複数のリポジトリで共有するので、別のリポジトリの割り付けに作業ディレクトリの設定を当てると、実行する場所によって結果が変わる。出現率の計算（`experiments.refresh`）も、作業ディレクトリと別のリポジトリの割り付けは計算しない
- `arm` の値は 2 つの単位の集まりから求める。表示する効果・費用・ROI・優劣は、期間に割り付けた単位から求める。打ち切り・候補・段階・出現率は、期間の終わりの時点で割り付けが使う値を示すため、期間の終わりまでの直近 `window_days` 日に割り付けた単位から求める。期間を `window_days` より長く取っても、期限の切れた打ち切りを表示しないためである。
  - 実際のモデルは、集まりごとに、その範囲の観測（`observed`）と単位だけから決める。開始日を早めた期間の古い観測で直近の単位を除かず、期間の終わりより後に割り付けた別のモデルの評価で期間の中の単位を除かないためである。そのため、割り付けの日時で範囲に絞ってから、まとまりの今のモデルを決める
  - 単位の候補（`experiments.unit_pool`）と観測（`observed_latest`。日時も返す）は、2 つの範囲を覆う広さで実験のキーごとに 1 回だけ集める。範囲ごとの処理は、集めた結果を割り付けの日時と観測の日時で絞ってモデルを決めること（`experiments.pick_units`）だけである。範囲ごとに集め直すと、実験の記録の全行の走査が範囲の数だけ増えるためである
  - 表示する最良である確率（`best`）は、期間の単位から求めた値だけを出す。期間の件数が足りず求められなければ出さない
  - 2 つの集まりが同じなら、単位は 1 回だけ求め、最良である確率は `probabilities` の結果を `judgement` に渡して標本を引き直さない
- 期間の終わりより後の実験の記録は、用途で扱いを分ける。効果指標と停止は、期間の後に書かれた行も読む。評価や費用が後日届いた単位も、期間の単位として数えるためである。実際のモデル（`observed`）・起動の拒否（`launch_error`）・費用を計算できないモデル（`unpriced`）は、期間の終わりまでの行だけを読む。起動の拒否は、候補の判定にも、基準へ切り替えた単位を除く判定にも、期間の終わりまでの行だけを使う。期間の後のモデルの更新や起動の拒否で、当時の単位を除いたり候補を打ち切ったりしないためである
- 候補から外した組は、理由（`dropped`）と候補へ戻る日時（`dropped_until`）を返す。打ち切りは期限、起動できない組は `refused_until` の日時である。両方に当たる組は、候補へ戻るのが遅いほうの理由と日時を返す。`refused_until` は 1 回だけ求め、`candidates` に渡して候補の判定と共有する
- 費用を計算できないモデル（`unpriced_models`）は、割り付けの `uniform` の判定と同じ関数 `unpriced_models` で、期間の終わりまでの直近 `window_days` 日に割り付けた単位から求める。単価表に後から載ったモデルを除く判定は、単価表（`codex-prices.json`）を入れる単位で `unpriced_models` に足し、割り付けと `report` の両方に効かせる
- 拒否された行は、使用量ログの `model_refused` の欄を持つ行である。Claude の起動がモデルを理由に拒否されたら、基準の組へ切り替えたかにかかわらず、使用量ログの行に `model_refused: true` を書く。書くのは行を作る処理（`claude.LaunchLog`）である。切り替え前の確認で止まったときに書かないのは、候補から外す判定に使う実験の記録の `launch_error` の行である。実験の記録ではなく使用量ログに持つのは、実験の記録が使用量ログより先に月の掃除で消え、消えた期間を集計し直すと、基準の組へ切り替えて成功した依頼が拒否された組の成功になるためである。割り付け ID では見分けない。同じ単位の起動はすべて同じ割り付け ID を持つので、判定の 1 回目で回答の形が合わず、打ち直しの起動だけが拒否された場合に、1 回目の行まで拒否として除いてしまうためである
- 出現率の式（`floor + (1 − floor × 候補の数) × 最良である確率`）は、`floor × 候補の数` が 1 を超えると負になる。過去の実験のキーは今の設定より組が多いことがあるので、`adaptive` の段階でこれに当たるキーは出現率（`probability`）を省き、`note` に理由を添える。割り付けの行は当時の `floor` を持たないので、当時の値では計算しない
- `--since` が `--until` より後なら、記録を読まずに `invalid` で止める
- 重なった `launch_id` を除く走査と起動の費用（`usage_decide.launch_rows`）は 1 回だけ求め、起動の区分の集計と単位の費用（`usage_index`）で共有する
- 実験の記録の行は、読むときに形（`experiment.schema.json`）を確かめる。形に合わない行は除き、その数を `metrics` の `broken_experiment_rows` で返す。必須の欄の無い行が 1 行あっても、ほかの行の集計を止めないためである
- `metrics` の `kept_since` は、保存先に残っている最も古い月のファイルの月初め（UTC）で、`--since` がそれより前のときだけ置く。月の掃除は、保存先を共有するリポジトリの割り付けのうち最長の `window_days` を使って月ごとに消すので、今の設定からは残っている範囲が決まらないためである。月の掃除で消えた月と、もともと記録の無かった月は区別しない
- 集計の条件は、表 `launch-experiments` の `records` 行で定義する。`success_outcomes` は成功として数える終了結果、`uncounted_outcomes` は成功率の分母から外す終了結果、`step_layers` はステップのやり直し率の対象にする起動の層である。実装はこれらの定義を参照し、値をコードに書き写さない（検査 const-fact が書き写しを見つける）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_pick_command` | 設定の誤り | `launch_choice` | 呼び出し | 止める | 結果 JSON は `stopped`、`--args` は標準エラーだけ | 再実行できる |
| `_report` | 記録が無い | 初回 | 読み取り | 続ける | 「記録なし」 | — |
| `main`（delegate-run） | `launch_choice` の失敗 | 設定 | 呼び出し | CLI を起動せずに止める | 標準出力は空、終了コードは `precondition` | 再実行できる |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_report` | 設計書の `ndf models report` の出力 | 結果 JSON | 作業の本文、指摘の本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `report_rows` | 依頼・ステップ・単位にまとめてから区分へ入れる | 期間の境をまたぐ依頼 | すべて拒否された依頼、重なった `launch_id` |
| `main`（delegate-run） | codex はモデルと安全装置の引数が `{args}` に続けて入る。kiro と agy は安全装置の引数だけで、`launch_choice` を呼ばない | 引数 0 個。`launch_choice` が止まる `experiments` の誤りがあっても kiro と agy は起動する | `launch_choice` の失敗と安全装置の停止で `exec` を呼ばない |
