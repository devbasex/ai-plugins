# #2468 の詳細設計: 役に立たない繰り返しを止め、費用と時間に上限を持つ

[設計文書](./issue-2468-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。決定の理由・不変条件（I1〜I4）・出直しの扱いの表・消費量の数え方は設計文書を、受け入れ条件は [要求仕様](./issue-2468-requirements.md) を指し、ここでは写さない。

[#4363 の詳細設計](./issue-4363-design-detail.md) と [#3780 の詳細設計](./issue-3780-design-detail.md) の後に実装する。使うのは `verify_decide`（`select`・`instruction`・`read`・`Verdict`・`Answers`）、`verify.ask`、`journal.apply_checks`・`sort`・`filed`、`drive._confirm`、`scope_decide.origin_record`、`round_scope.origin`、`changes.Range`・`new_in_pr`、`leftover_decide`（`normalized`・`proceeds`・`kinds_of`・`files_issue`・`result_fields`）、`leftover.file_issues` である。

| 語 | 意味 |
| --- | --- |
| 相手役 | cross-review で差分を読んで指摘を書く AI |
| 判定役 | 相手役が書いた重い指摘が実際に起きるかを確かめる読むだけの claude（#4363） |
| 席 | 相手役 1 人分の枠。結果を返さない相手役の席には、読むだけの claude が代わりに入る |
| 線引き | 2 ラウンド目以降の指摘を、修正へ進めるものと直さずに残すものに分けること（#3780） |
| 欠け | 結果の `gaps`。直したとも見送ったとも決まらない重い指摘などで、残ると結果が `ok` にならない |
| プロジェクトの上限 | `.ndf/review.json` の `limits.rounds`・`limits.seconds`・`limits.usd`（省いた秒と費用の既定を含む）。コマンドの `--max-rounds` は含まない |
| 差分のレビュー | 実装のプランが、レビューの後に足したコミットだけを、別の状態の保存先で読むレビュー |
| 単位のレビューの包み | 単位の実装プランがレビューの起動を包むシェルスクリプト（`core/route` の `UNIT_REVIEW`）。対象の PR を読んだかを確かめる |
| 仮の指摘 | 相手役が返し、まだ行（状態ファイルの指摘の記録）にしていない指摘（`candidates`） |
| 出直し | 見送った指摘か直さずに残した指摘と同じ指摘が、もう一度出ること |
| 使った量 | ラウンドごとの秒と claude の費用（記録の `spent`） |
| 振動の候補 | 前々ラウンドにあって前ラウンドで出ず、直した指摘の行 |

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/
│   ├── domains/crossreview/
│   │   ├── budget_decide.py *   使った量と上限の判断
│   │   ├── spend.py *           使った量の記録と中断の回収
│   │   ├── verify_decide.py, verify.py, journal.py
│   │   ├── leftover_decide.py, leftover.py
│   │   ├── drive.py, fixer.py, fixgate_decide.py
│   │   ├── scope_decide.py, round_scope.py, reviewed_pr.py
│   │   ├── finish_decide.py, report_decide.py
│   │   └── settings.py, criteria_decide.py, cli.py
│   └── core/
│       ├── plan/ queue_decide.py, queue_run.py, cli.py
│       └── route/__init__.py
├── contracts/
│   ├── data/review-criteria.json
│   ├── schemas/ ndf-settings/review.schema.json, io/cross-review.args.schema.json
│   ├── domains/ndf-files.json
│   ├── limits/ cross-review.max-seconds.json *, cross-review.max-usd.json *,
│   │            cross-review.heartbeat-seconds.json *, plan.queue.same-step-stops.json *
│   ├── causes/repeated_stop.json *
│   └── templates/impl.json
├── docs/contracts/ commands.md, exit-codes.md, ndf-settings.md
├── skills/cross-review/
└── tests/ domains/crossreview/, core/plan/, route/
```

依存の向き: `cli` が `drive` を読み、`drive` が `spend`・`journal`・`verify`・`budget_decide`・`finish_decide`・`round_scope`・`changes` を読み、`spend` が `budget_decide` を読む。`budget_decide`・`finish_decide`・`verify_decide`・`leftover_decide` は外部との接続を読まない。`core.plan` と `core.route` は cross-review のモジュールを読まない（今のとおりコマンドの文字列でつなぐ）。輪は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/crossreview/budget_decide.py` | 新設 | 使った量を足し、当たった上限を返す | 無し | `Limits`・`Used`・`Recovered` | 新設: `counted`・`hit`・`recovered`・`same_target`・`advice` |
| `plugins/ndf/src/ndf/domains/crossreview/spend.py` | 新設 | 起動を包んで使った量を記録し、中断した起動の分を回収し、別の保存先の記録を読む（`drive.py` の行数の上限のため分ける。設計の触るファイルに足した） | `budget_decide`・`store`・`run_log` | `Meter` | 新設: `recover`・`other_records` |
| `plugins/ndf/src/ndf/domains/crossreview/verify_decide.py` | 変更 | 見送りの一覧を指示に足し、`same_as`・`skip_broken` を読む | `criteria_decide` | `Verdict` | 変更: `select`・`instruction`・`read` |
| `plugins/ndf/src/ndf/domains/crossreview/verify.py` | 変更 | 一覧を指示へ渡す（`instruction` の引数が増えるため。設計の触るファイルに足した） | `verify_decide` | モジュール | 変更: `ask` |
| `plugins/ndf/src/ndf/domains/crossreview/journal.py` | 変更 | 仮の指摘をまとめ、行を確定し、出直しを扱う | `leftover_decide`・`verify_decide` | `FindingJournal`・`Settled`・`Context` | 新設: `propose`・`view`・`to_verify`・`settle`・`known_for_verifier`・`oscillating` / 変更: `enter`・`carry`・`apply_fix` |
| `plugins/ndf/src/ndf/domains/crossreview/leftover_decide.py` | 変更 | 出直した見送りと上限で残った指摘の残し方 | `criteria_decide` | モジュール | 新設: `filed_number` / 変更: `result_fields`・`summary_line` |
| `plugins/ndf/src/ndf/domains/crossreview/leftover.py` | 変更 | 出直した見送りと上限で残った指摘を起票する | `leftover_decide` | モジュール | 変更: `file_issues` |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | 変更 | ラウンドを合計で数え、上限を確かめ、中断から続ける | `spend`・`journal`・`verify`・`budget_decide`・`finish_decide`・`round_scope`・`changes` | `_Run`・`Outcome` | 新設: `_over`・`_resume`・`_stale_skips`・`_collect`・`_decide`・`_fix` / 変更: `_round`・`_drive_rounds`・`_summary` |
| `plugins/ndf/src/ndf/domains/crossreview/fixer.py` | 変更 | 見送りを決めた時点の head と比較基点を渡す（設計の「データ構造」の `skipped_head`・`skipped_base`。設計の触るファイルに足した） | `journal` | モジュール | 変更: `take_fix`・`_fix_record` |
| `plugins/ndf/src/ndf/domains/crossreview/fixgate_decide.py` | 変更 | 上限で修正を起動しなかったラウンドを記録の不足にしない（設計の触るファイルに足した） | 無し | モジュール | 変更: `require_fix_for_changes` |
| `plugins/ndf/src/ndf/domains/crossreview/scope_decide.py` | 変更 | 中断したラウンドを読んだ証跡にしない | `store` | モジュール | 新設: `finished` / 変更: `read_by_all_head`・`origin_record` |
| `plugins/ndf/src/ndf/domains/crossreview/round_scope.py` | 変更 | 上限で終わるときの「全員が読めた head」を返す（#3780 で変えるファイル。#2468 の設計の触るファイルに足した） | `scope_decide`・`changes` | モジュール | 新設: `read_all` |
| `plugins/ndf/src/ndf/domains/crossreview/reviewed_pr.py` | 変更 | 上限で終わった起動の読んだ PR を返す | `store` | モジュール | 新設: `of_record` / 変更: `read_by_all_this_run` |
| `plugins/ndf/src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方 `limit` と上限の結果の表 | `leftover_decide` | `Review`・`Limited` | 新設: `_limit_verdict` / 変更: `_finish`・`summary_of`・`as_review` |
| `plugins/ndf/src/ndf/domains/crossreview/report_decide.py` | 変更 | 出直し・上限・読めない費用を表と注意に出す | `leftover_decide` | モジュール | 変更: `final_of`・`_finding_rows`・`_round_rows` |
| `plugins/ndf/src/ndf/domains/crossreview/settings.py` | 変更 | `limits` と `--budget-state` を持つ | `criteria_decide`・`limits` | `RunConfig`・`ReviewSettings` | 変更: `read_settings`・`outside_checkout` |
| `plugins/ndf/src/ndf/domains/crossreview/criteria_decide.py` | 変更 | `.ndf/review.json` の `limits` の形を確かめる | `contracts` | `ProjectLimits` | 新設: `limits_of` |
| `plugins/ndf/src/ndf/domains/crossreview/cli.py` | 変更 | `--budget-state` を受け、結果と `--reviewed-out` に上限を反映する | `drive`・`finish_decide` | モジュール | 変更: `_run_parser`・`_run_config`・`_write_reviewed`・`_report` |
| `plugins/ndf/src/ndf/core/plan/queue_decide.py` | 変更 | 停止の回数を数え、流さない判断をする | 無し | `Outcome` | 新設: `next_stop`・`refused_repeat` |
| `plugins/ndf/src/ndf/core/plan/queue_run.py` | 変更 | 回数を `queue.json` に持ち、2 回なら起動しない | `queue_decide`・`limits` | `QueueOptions`・`_QueueExecution` | 新設: `_clear_stops` / 変更: `_record`・`_launch` |
| `plugins/ndf/src/ndf/core/plan/cli.py` | 変更 | `--retry <プラン>` を受ける | `queue_run` | モジュール | 変更: `_parser`・`_cmd_queue` |
| `plugins/ndf/src/ndf/core/route/__init__.py` | 変更 | 単位のレビューの包みが読んだ head のファイルを残す | 無し | 定数 `UNIT_REVIEW` | — |
| `plugins/ndf/contracts/data/review-criteria.json` | 変更 | 判定役の指示に `same_as`・`skip_broken` を足す | 無し | データ | — |
| `plugins/ndf/contracts/schemas/ndf-settings/review.schema.json` | 変更 | `limits` を足す | 無し | スキーマ | — |
| `plugins/ndf/contracts/domains/ndf-files.json` | 変更 | `review` のキーに `limits` | 無し | データ | — |
| `plugins/ndf/contracts/limits/cross-review.max-seconds.json` | 新設 | 秒の既定 3600（`seconds`） | 無し | データ | — |
| `plugins/ndf/contracts/limits/cross-review.max-usd.json` | 新設 | 費用の既定 20 ドル（`count`） | 無し | データ | — |
| `plugins/ndf/contracts/limits/cross-review.heartbeat-seconds.json` | 新設 | 経過の秒を書く間隔 10 秒（`interval`） | 無し | データ | — |
| `plugins/ndf/contracts/limits/plan.queue.same-step-stops.json` | 新設 | 同じステップの停止の上限 2（`same_cause`）。上限の値は設定だけが持つ決まりのため（設計の触るファイルに足した） | 無し | データ | — |
| `plugins/ndf/contracts/causes/repeated_stop.json` | 新設 | 同じステップで 2 回停止した停止の種類 | 無し | データ | — |
| `plugins/ndf/contracts/templates/impl.json` | 変更 | `unreviewed` の経路と `--budget-state` | 無し | データ | — |
| `plugins/ndf/contracts/schemas/io/cross-review.args.schema.json` | 変更 | `--budget-state`（生成物） | 無し | 生成物 | — |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | `--budget-state`・`--retry`（生成物。設計の触るファイルに足した） | 無し | 生成物 | — |
| `plugins/ndf/docs/contracts/exit-codes.md` | 変更 | `repeated_stop`（生成物。設計の触るファイルに足した） | 無し | 生成物 | — |
| `plugins/ndf/docs/contracts/ndf-settings.md` | 変更 | `limits`（生成物。設計の触るファイルに足した） | 無し | 生成物 | — |
| `plugins/ndf/skills/cross-review/SKILL.md` | 変更 | 数え方・上限・終わり方 | 無し | 文書 | — |
| `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` | 変更 | 同上 | 無し | 文書 | — |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` | 変更 | 同上 | 無し | 文書 | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_limits.py` | 新設 | 上限・使った量・中断と再開 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_leftover.py` | 変更 | 出直しと上限の残し方 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_journal.py` | 変更 | 仮の指摘と行の確定 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_verify.py` | 変更 | `same_as`・`skip_broken`（設計の触るファイルに足した） | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_run.py` | 変更 | `--max-rounds` が合計になる変化と通しの確かめ（設計の触るファイルに足した） | — | テスト | — |
| `plugins/ndf/tests/core/plan/test_queue.py` | 変更 | 停止の回数の判断 | — | テスト | — |
| `plugins/ndf/tests/core/plan/test_queue_run.py` | 変更 | `--resume`・`--retry` | — | テスト | — |
| `plugins/ndf/tests/route/test_templates.py` | 変更 | 包みとテンプレート | — | テスト | — |
| `plugins/ndf/tests/route/test_flows.py` | 変更 | `unreviewed` の経路 | — | テスト | — |

## 上限の設定（criteria_decide・settings・review.schema.json）

満たす決定: 決定 3・4、受け入れ条件 4・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `limits_of` | `criteria_decide.py` | 新設 | `limits_of(raw: Mapping[str, Any], where: str) -> ProjectLimits` | `version: 1` の `limits` を読み、形の違いは `ReviewDeclInvalid` |
| `read_settings` | `settings.py` | 変更 | シグネチャは変えない | `ReviewSettings.limits` に読んだ値を入れる |
| `outside_checkout` | `settings.py` | 変更 | シグネチャは変えない | `budget_states` も worktree の外にあることを確かめ、実体のパスに置き換える |

`outside_checkout` を `--budget-state` にも当てるのは、worktree の中の状態ファイルは PR の作成者が書き換えられ、上限の合計を増減できるためである。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `limits_of` | 形が違う | 負の数・文字列・知らないキー・真偽値 | 値の検査 | 止める | `invalid`（今の `focus` の誤りと同じ） | 設定を直して打ち直す |
| `outside_checkout` | 保存先が worktree の中 | `--budget-state` の指定 | 実体のパスの比較 | 止める | `precondition` | 外の保存先を渡す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `ProjectLimits(rounds: Optional[int], seconds: Optional[float], usd: Optional[float])` | 型 | `criteria_decide.py` | すべて None |
| `cross-review.max-seconds` | 設定値 | `contracts/limits/` | 3600 秒 |
| `cross-review.max-usd` | 設定値 | `contracts/limits/` | 20 ドル |
| `RunConfig.budget_states: tuple[Path, ...]` | 設定値 | `settings.py` | 空 |

秒と費用の既定は `settings` が引き、`limits.rounds` には既定を置かない（設計の「`.ndf/review.json`」の表）。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `limits_of` | `criteria_decide.focus_of`・`fixer_sessions_of`、`review.schema.json` | `grep -rn "def .*limits_of\|def focus_of\|def fixer_sessions_of" --include=*.py src/ndf` | 同じ読み方（`version: 1` の後に呼び、`ReviewDeclInvalid`）にそろえる。`checks.ratchet.limits_of` は別の物（ラチェットの基準） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `limits_of` | 3 キーを読む | `limits` の無い `version: 1` が読める（受け入れ条件 6）。1 つだけ | `rounds` が 0・小数・真偽値、`seconds` が 0・負、知らないキー |
| `outside_checkout` | 外の保存先はそのまま | 同じ保存先を 2 回 | worktree の中のリンクは `precondition` |

## 使った量と上限の判断（budget_decide）

満たす決定: 決定 2・3・4・5、受け入れ条件 3・4（不変条件 I2・I3）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `counted` | `budget_decide.py` | 新設 | `counted(own: Sequence[Mapping[str, Any]], others: Sequence[Sequence[Mapping[str, Any]]]) -> Used` | 自分と渡した保存先のラウンドの記録から、最後の収束より後の量を数える |
| `hit` | `budget_decide.py` | 新設 | `hit(used: Used, limits: Limits, *, before_round: bool) -> tuple[str, ...]` | 当たった上限の名前を返す |
| `recovered` | `budget_decide.py` | 新設 | `recovered(running: Mapping[str, Any], mtimes: Sequence[float]) -> Recovered` | 中断した起動の秒、`spent.unread` に足す数（起動していた claude の数）、終わりの時刻を返す |
| `same_target` | `budget_decide.py` | 新設 | `same_target(own: Mapping[str, Any], other: Mapping[str, Any]) -> bool` | 別の保存先の状態が、今回と同じリポジトリと PR のものか |
| `advice` | `budget_decide.py` | 新設 | `advice(hits: Sequence[str], used: Used, limits: Limits, state_dir: str) -> list[str]` | 当たった上限ごとの、使った量と変える値の文 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `counted` | `--max-rounds` の回数 | 自分の記録のうち、自分の最後の `converged` より後の件数 |
| `counted` | `limits.rounds`・秒・費用 | 自分と渡した記録を `spent.ended_at` の順に並べ、最後の `converged` より後を足す。`spent` の無い記録（前のバージョンが書いたもの）は 0 として先に並べる。この版が書く記録は、作った時点から `spent.ended_at` を持つ |
| `counted` | `spent` の無い記録 | ラウンドには数え、秒と費用は足さない |
| `hit` | `before_round` が真 | `max_rounds`・`limits.rounds`・`limits.seconds`・`limits.usd` を確かめる |
| `hit` | `before_round` が偽（判定役・修正担当の起動の前） | 秒と費用だけを確かめる。`used` は進行中のラウンドの分を含めて渡す |
| `hit` | 合計がちょうど上限 | 当たったとみなす |
| `recovered` | `elapsed` と、起動の開始より後の出力の最も遅い更新時刻までの秒 | 大きい方 |

`same_target` は、`target_repo` が同じで、相手の `pr`・`current_pr` のどちらかが、自分の `pr`・`current_pr` のどちらかと同じ場合に真である。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Limits(max_rounds, rounds, seconds, usd)` | 型 | `budget_decide.py` | 無し |
| `Used(rounds_here, rounds_all, seconds, usd, unread)` | 型 | `budget_decide.py` | 0 |
| `Recovered(seconds, unread, ended_at)` | 型 | `budget_decide.py` | 無し |
| 上限の名前 | 文字列 | `budget_decide.py` | `max_rounds`・`limits.rounds`・`limits.seconds`・`limits.usd` |

記録の `spent` の形は設計文書の「データ構造」にある。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `counted`・`hit`・`recovered`・`same_target`・`advice` | `crossrefactor.budget_decide`・`hooks.budget` | `grep -rn "def .*budget\|class .*Budget\|class .*Meter" --include=*.py src/ndf` | どちらも別の量（改修の分と hook の秒）で、cross-review のラウンドの記録を数えないので使えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `counted` | 収束の無い打ち直しをまたいで合計する | 収束の後は 0 から。`ended_at` の順で保存先を混ぜる。`spent` の無い記録 | 無し |
| `hit` | 4 つの上限をそれぞれ返す | ちょうど上限。ラウンド数の上限ちょうどの最後のラウンドは、判定役と修正担当の前では当たらない | `--max-rounds` だけに当たり `limits.rounds` が残る場合と、その逆。2 つの上限の小さい方を 1 つの回数と比べる誤った実装は、このテストで失敗する |
| `recovered` | 経過の秒を使う | 出力の更新時刻のほうが長い・起動の前の更新時刻は使わない | 出力が無くても経過の秒 |
| `same_target` | 元の番号と今の番号 | 巻き直した後 | 別のリポジトリ・別の PR |
| `advice` | 当たった上限をすべて並べ、引数か `.ndf/review.json` のキーかが分かる | 差分のレビューの保存先の名前を含む | 無し |

## 使った量の記録と中断の回収（spend）

満たす決定: 決定 2・5、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Meter` | `spend.py` | 新設 | `Meter(record: dict[str, Any], save: Callable[[], None], clock: Callable[[], float] = time.time, wait: Optional[Callable[[float], bool]] = None)`、`wrap(launch: Launcher) -> Launcher`、`totals() -> tuple[float, float]`、`close() -> None` | 1 ラウンドの起動を包み、`spent` と `spent.running` を書く |
| `recover` | `spend.py` | 新設 | `recover(record: dict[str, Any], mtimes_of: Callable[[Sequence[str]], Sequence[float]]) -> bool` | `spent.running` の残った記録に、中断した起動の秒を `spent.seconds` へ、起動していた claude の数を `spent.unread` へ 1 回だけ足して `spent.running` を消す（`spent.usd` は費用が読めないので足さない） |
| `other_records` | `spend.py` | 新設 | `other_records(dirs: Sequence[Path], ref: store.StateRef, data: Mapping[str, Any], own: Path) -> list[list[Mapping[str, Any]]]` | 渡した保存先の、同じ PR のラウンドの記録（中断の分は回収した値）を返す。書き換えない |

- `Meter`
  - 事前条件: `record` は状態ファイルに保存済みのラウンドの記録
  - 事後条件: 起動が 1 つでも走っている間は `spent.running` がある。起動が 1 つ終わるたびに、その起動の費用を `spent.usd` に足し（読めなかった claude の起動は `spent.unread` に数える）、同じ保存の中でその起動を `spent.running` から外す（claude の起動なら claude の数を 1 減らし、出力ファイルを除く）。この保存では `spent.running.elapsed` もその時点の秒にする（除いた出力ファイルの更新時刻は回収で使えないため）。最後の起動が終わる保存で `spent.running` を消し、`spent.seconds` を、`Meter` を作った時点で記録にあった秒に、作ってからの秒を足した値に置き換える（判定役から再開したラウンドでも、前の分が残る）。`spent.running` がある間は `spent.seconds` を書き換えない（走っている間の秒は `spent.running.elapsed` が持ち、回収で 2 回足さないため）。`spent.ended_at` は書いた時刻に置き換える
  - 副作用: `save` で状態ファイルを書く。起動中は別のスレッドが `cross-review.heartbeat-seconds` の間隔で `spent.running.elapsed` を書き直す
- `other_records`
  - 事前条件: `dirs` は `outside_checkout` が実体のパスにした値
  - 事後条件: 重複と自分の保存先（`own`）を除く。状態の無い保存先は空

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Meter` | 費用を読めない | 結果の行が無い・`total_cost_usd` が無い | `run_log.reported_cost` が None | 既定値で続ける（0 を足し `unread` に数える） | 注意に数 | 無し |
| `Meter` | 状態ファイルを書けない | 空き容量・権限 | `OSError` | 呼び出し元へ返す（今の保存の失敗と同じ） | 今のとおり | 打ち直すと中断の回収から続く |
| `Meter` | 起動が例外で終わる | 起動の失敗 | `wrap` の `finally` | 止める。`running` は残さず分を足す | 呼び出し元の扱い | 打ち直せる |
| `recover` | `running` の形が違う | 手で書き換えた状態 | 値の検査 | 既定値で続ける（`spent.seconds` にも `spent.unread` にも足さずに `spent.running` を消す） | 無し | 無し |
| `other_records` | 渡した状態が壊れている | 書きかけ・手の編集 | `store.StateBroken` | 止める | `invalid` | 保存先を直すか外す |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並べて起動する相手役と経過を書くスレッド | ラウンドの記録と状態ファイル | `Meter` の中の 1 つのロック | `spent` はラウンドの記録に 1 つだけ持つ。起動の費用を足すのと、その起動を `spent.running` から外すのを 1 回の保存で行うので、終わった起動の費用は後の中断で失われず、回収が `unread` に足すのは走っていた claude だけになる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `cross-review.heartbeat-seconds` | 設定値 | `contracts/limits/` | 10 秒 |

claude の起動は `spec.agent` が claude かで見分ける（席の claude・代わりに入る claude・判定役・修正担当のすべて）。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `Meter` | `run_log.reported_cost`・`record_cost_file`、`agents.run_agent` | `grep -rn "def reported_cost\|total_cost_usd" --include=*.py src/ndf`、`grep -rn "heartbeat\|elapsed" --include=*.py src/ndf` | 費用の読み取りは `reported_cost` を使う。起動中の経過を書く部品は無い（`record_cost_file` は statusline の合算で別の書き先） |
| `recover`・`other_records` | `store.state_pr`・`read_object`、`budget_decide.recovered` | `store.py` を読んだ | 状態の読み取りは `store` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Meter` | 起動の前に `running` が保存され、終わると消えて秒と費用が足される | 並べた 3 つの起動で `spent.running` の claude の数が 3 になる。1 つ終わった時点の保存で、その費用が足され、claude の数が 2 になる（残りが終わる前に回収しても、終わった起動の費用が残り、`unread` は 2 だけ増える）。時計と `wait` を差し替えて経過の記録を確かめる（実時間は待たない） | 費用を読めない起動は `unread`。例外でも `running` が残らない |
| `recover` | 中断の分を 1 回だけ足す | 2 回呼んでも増えない | `running` の無い記録は何もしない |
| `other_records` | 同じ PR の記録を返し、書き換えない | 同じ保存先 2 回・自分の保存先 | 別の PR は除く。壊れた状態は `StateBroken` |

## 判定役の回答（verify_decide・verify）

満たす決定: 決定 1、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `select` | `verify_decide.py` | 変更 | `select(rows, use_tracks: bool = False, recheck: Collection[str] = ()) -> list[Mapping[str, Any]]` | 先に `redline` を持つ指摘と NDF の指摘を除く（人の判断を待つ指摘などは `recheck` でも渡さない）。残りのうち、見送りの理由を確かめる指摘（`recheck`）は、ほかの条件によらず渡す |
| `instruction` | `verify_decide.py` | 変更 | `instruction(rows, diff, worktree, criteria, known: Sequence[Mapping[str, Any]] = (), recheck: Collection[str] = ()) -> str` | 見送り済み・残した指摘・振動の候補の一覧（番号・本文・理由）と、理由を確かめる指摘を足す |
| `read` | `verify_decide.py` | 変更 | `read(found, ids, known: Collection[str] = ()) -> Answers` | `same_as`（一覧の番号か空）と `skip_broken`（真・偽・null）を読む |
| `ask` | `verify.py` | 変更 | `ask(run, round_no, rows, diff, launch, known: Sequence[Mapping[str, Any]] = (), recheck: Collection[str] = ()) -> Answers` | 一覧を指示へ渡す |

`Verdict` に `same_as: str = ""` と `skip_broken: Optional[bool] = None` を足す。`same_as` が一覧に無い番号なら空にして注意する。`skip_broken` が真なのに 3 つの文（条件・経路・結果）が無い回答は、`skip_broken` を null にする。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask` | 回答が得られない | #4363 の異常系と上限で起動しない場合 | 同上 | 既定値で続ける。`same_as` は無し（新しい指摘）、見送りの理由は確かめられなかった扱い | 注意 | 次の起動で確かめる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `select` | `recheck` の指摘は `protect` を持っても渡る | 一覧があるラウンドは `use_tracks` で渡る | `redline` は `recheck` でも渡らない |
| `read` | `same_as` と `skip_broken` を読む | `same_as` が空・`skip_broken` が null | 一覧に無い番号、3 つの文の無い真 |

## ジャーナル（仮の指摘と行の確定）

満たす決定: 決定 1、受け入れ条件 1（不変条件 I1）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `propose` | `journal.py` | 新設 | `propose(self, round_no: int, raised: Sequence[dict[str, Any]]) -> list[dict[str, Any]]` | 相手役の報告を、本文の全体が一致するものごとに仮の指摘（番号と報告の並び）にまとめる。行は変えない |
| `view` | `journal.py` | 新設 | `view(candidate: Mapping[str, Any], items: Sequence[str], touched: Optional[Touched]) -> dict[str, Any]` | 仮の指摘の重要度・保護の種類・MVV の項目・根拠の報告の場所と 1 文を、すべての報告から決める |
| `to_verify` | `journal.py` | 新設 | `to_verify(self, views: Sequence[Mapping[str, Any]], mvv: Optional[str], stale: Collection[str]) -> tuple[list[Mapping[str, Any]], frozenset[str]]` | 判定役へ渡す仮の指摘と、見送りの理由を確かめるものの番号を返す |
| `known_for_verifier` | `journal.py` | 新設 | `known_for_verifier(self, mvv: Optional[str], oscillating: Collection[str]) -> list[dict[str, Any]]` | 同じ MVV の見送りの行、残した行、振動の候補の行の番号・本文・理由 |
| `oscillating` | `journal.py` | 新設 | `oscillating(self, history: Sequence[Collection[str]]) -> frozenset[str]` | 前々ラウンドにあって前ラウンドで出ず、直した行の番号 |
| `settle` | `journal.py` | 新設 | `settle(self, round_no: int, candidates: Sequence[Mapping[str, Any]], answers: verify_decide.Answers, ctx: Context) -> Settled` | 設計の「出直しの形」の表で行を確定し、確認の結果を適用する |
| `enter` | `journal.py` | 変更 | 引数と返りの型は変えない（返りの中身が指摘のキーから行の番号に変わる） | `propose` と回答の無い `settle` を続けて呼び、収束を止める行の番号を返す |
| `carry` | `journal.py` | 変更 | シグネチャは変えない | 本文の重なりを、critical・major は `normalized` の一致で見る |
| `apply_fix` | `journal.py` | 変更 | `apply_fix(self, items, handed, round_no, *, mvv: Optional[str] = None, at: Optional[changes.Range] = None)` | 見送りの行に `skipped_head`・`skipped_base` を書く |

- `settle`
  - 事前条件: `candidates` は `propose` の返りか、状態ファイルの `candidates` を読んだ値
  - 事後条件: 仮の指摘はどれも 1 つの行になる。返りの `Settled.history`（`frozenset[str]`）は収束を止める行の番号である。`FindingJournal` の属性 `fresh`（`list[str]`。新しく出たか戻った行の番号）・`raised_ids`（`list[str]`。このラウンドで出た行の番号）・`returned`（`set[str]`。直した後に戻った行の番号）は、今回の分になる。出直しは行の `reraised` にラウンドの番号を持つ

### 処理の手順

行を確定する順は、設計文書の「出直しの形」の表を上の行から当てる。表の扱いと関数の対応は次のとおりである。

| 設計の表の扱い | `settle` の中の呼び出し |
| --- | --- |
| 承認済みの MVV が違う・初めて承認の要る操作 | 本文の全体が一致すれば `_revive`、無ければ新しい行 |
| 本文の全体が一致・`same_as` で見送りか残した行 | 行へ反映（今の `_raised_again`）した後、見送りの行は設計の「同じ行へ反映した後」の表、残した行は `sort`（`leftover_decide.proceeds`）で決める |
| `same_as` で今回の保護の種類が行に無い | 新しい行 |
| `same_as` で振動の候補 | `_revive` の後、本文と場所を今回の値にする |
| 本文の全体が一致する未解決・直した行 | 行へ反映（直した行は `_revive`） |
| ほか | 新しい行 |

- minor・nit は今のとおり先頭の一致（`oscillation.finding_key`）でまとめる
- 振動の判定（`oscillation.returned`）に渡すのは行の番号である
- `to_verify` が外すのは、本文の全体が未解決の行と一致する指摘、見送りの行か残した行と一致して理由の確認が要らない指摘、重要度が重くない指摘である

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 仮の指摘 `{"id": "c<ラウンド>-<n>", "reports": [報告]}` | 型 | 記録の `candidates` | 報告は重ねる前の値（`by`・`text`・`severity`・`impact`・`path`・`line`・`section`・`protect`・`mvv`） |
| `Context(items, mvv, touched, stale, oscillating, severities)` | 型 | `journal.py` | `stale` は理由の確認が要る見送りの行の番号 |
| `Settled(history: frozenset[str], checked: Checked)` | 型 | `journal.py` | 無し |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `propose`・`view`・`settle` | `journal.enter`・`_raised`・`_classify`・`criteria_decide.effective_severity`・`heavier` | `journal.py` を読んだ | 重要度と MVV の決め方は既存の関数を報告ごとに当てる。重ねる前の値を持つ仕組みは無い |
| `to_verify`・`known_for_verifier` | `journal.skipped_texts`・`pending` | 同上 | `skipped_texts` は本文だけを返すので、番号と理由を持つ一覧を別に返す |
| `oscillating` | `oscillation.returned` | `grep -rn "def returned" src/ndf` | `returned` は戻った後を判定する。戻る前の候補は新しく書く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `propose`・`view` | 同じ本文 2 件が 1 件になり、後の報告だけの `redline` と `protect` が残る | 1 文の無い critical と 1 文のある major は major。未変更行の critical と変えた行の major は、重要度 critical・場所と 1 文は変えた行。届く順を入れ替えても同じ | 本文が空の報告は捨てる |
| `settle` | 見送った指摘が、同じ文でも言い換え（`same_as`）でも、出直しで未解決にならない | 本文の全体が一致し、保護の種類が増えるか重要度が上がる出直しは、見送りの行が未解決へ戻る。言い換えで保護の種類が増える場合は新しい行になる。直さずに残した行の出直しは、critical か変えた行の major なら `parked` が外れる | 本文の先頭だけが同じ別の指摘は別の行になる。判定役が失敗したときの言い換えも新しい行になる |
| `settle`（理由の確認） | `skip_broken` が偽なら見送りのままにする | 出直しの重要度が見送りの行以下で、保護の種類がすべて行の `protect` にあり、行か出直しが保護の対象で、行が `stale`（`_stale_skips` の返り）の場合は、`skip_broken` が null でも行を未解決へ戻して警告する | `redline` の行は `reraised` が付いても人の判断を待つ |
| `oscillating` | 直した行が 1 ラウンド消えれば候補 | 3 ラウンド未満は空 | 見送った行は候補にしない |
| `apply_fix` | 見送りに head と比較基点 | `at` が None なら書かない | 無し |

## 駆動（drive・fixer・fixgate_decide）

満たす決定: 決定 2・5・7、受け入れ条件 1・2・3・4（不変条件 I2・I3）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_over` | `drive.py` | 新設 | `_over(run: _Run, *, before_round: bool, meter: Optional[spend.Meter] = None) -> tuple[str, ...]` | 使った量を数え、当たった上限を返す |
| `_resume` | `drive.py` | 新設 | `_resume(run: _Run, launch: Launcher) -> Optional[str]` | 中断した起動の使った量を回収する。判定の前に中断したラウンドは判定役から、上限で修正担当を起動しなかったラウンドは修正担当から、続けられれば続けて終わり方を返す |
| `_stale_skips` | `drive.py` | 新設 | `_stale_skips(run: _Run, current: changes.Range) -> frozenset[str]` | 見送りを決めた後に根拠の差分が加わりうる行の番号 |
| `_collect` | `drive.py` | 新設 | `_collect(run: _Run, round_no: int, prep: Prepared, record: dict[str, Any], launch: Launcher) -> list[dict[str, Any]]` | 相手役を起動し、仮の指摘と読めなかった相手役を保存する |
| `_decide` | `drive.py` | 新設 | `_decide(run: _Run, record: dict[str, Any], prep: Prepared, candidates: Sequence[Mapping[str, Any]], launch: Launcher, meter: spend.Meter) -> Optional[str]` | 判定役、行の確定、線引き、判定までを行う |
| `_fix` | `drive.py` | 新設 | `_fix(run: _Run, record: dict[str, Any], prep: Prepared, launch: Launcher, meter: spend.Meter) -> Optional[str]` | 上限を確かめて修正担当を起動し、結果を取り込んで送る。`_round` と `_resume` が呼ぶ |
| `_round` | `drive.py` | 変更 | シグネチャは変えない | 相手役の前に、`spent` の初期値（秒・費用・`unread` は 0、`ended_at` は作った時刻）を持たせた記録を保存し、`_collect`・`_decide`・`_fix` の順に進め、各起動の前に上限を確かめる |
| `require_fix_for_changes` | `fixgate_decide.py` | 変更 | `require_fix_for_changes(round_no, verdict, fix, pending, stopped_limit: Optional[str] = None)` | `stopped_limit` のあるラウンドは記録の不足にしない。判断の順は、`fix` があれば `OK`、`pending` があれば `INGEST`（`stopped_limit` があっても取り込む）、`stopped_limit` があれば `OK`、ほかは `STOP` |

`fixer.take_fix` と `_fix_record` は、修正担当が終わった時点の head と比較基点を `apply_fix(at=...)` へ渡す。`_fix` と `_ingest_pending` は、`stopped_limit` のあるラウンドに `fix` を書く保存で `stopped_limit` を消す。先に消すと、修正の結果を書く前の中断の後、次の起動が記録の不足で止まるためである。`ndf cross-review fix-gate` も `stopped_limit` を渡す。`Outcome` に `limited: Optional[fin.Limited]` を足す。

- `_over`
  - 副作用: `spend.other_records` で渡した保存先の状態を読む（書き換えない）
- `_resume`
  - 事後条件: ラウンドの記録は 1 件も削除しない。戻る時点で、最後のラウンドの記録には `verdict` があり、`spent.running` のキーは無い
  - 副作用: 判定役と修正担当を起動しうる。状態ファイルを書く
- `_stale_skips`
  - 副作用: `git` を読む（`changes.new_in_pr`）
- `_collect`
  - 事後条件: 記録が `candidates`・`unread_reviewers`・`spent` を持つ。全員が読めなければ `verdict: incomplete_reviewers` の記録を保存してから今の `precondition` を投げる
  - 副作用: 相手役の起動、状態ファイルの保存
- `_decide`
  - 事後条件: `candidates` が消え、行が確定している
  - 副作用: 判定役の起動、状態ファイルの保存

### 処理の手順

ラウンドの繰り返し（`_drive_rounds`）は次の順にする。

| 順 | すること |
| --- | --- |
| 1 | 今の `_reusable`（前回の収束を返す）。収束していれば上限を数えない |
| 2 | `_resume` |
| 3 | `_over(before_round=True)` が空でなければ終える。秒か費用に当たっていれば `limit`、ほかは `max_rounds` |
| 4 | `_round`。終わり方が返れば終える。ほかは 3 へ |

- 秒か費用とラウンド数の両方に当たった場合は `limit` にする（秒か費用に当たると、次の起動も回せないため。設計の表はこの重なりを定めていないので、[設計文書の「未確認のまま残ること」](./issue-2468-design.md#未確認のまま残ること)を設計の承認で確かめる）
- 繰り返しの終わる条件: ラウンドを 1 つ回すごとに数えるラウンドが 1 増え、順 3 で `--max-rounds` に達するので、必ず終わる

上限で終わる起動は、終える場所によらず、状態ファイルの `limit_end` に終わり方（`limit` か `max_rounds`）、当たった上限の名前、`advice` の文を保存する。後の `ndf cross-review report` が、実行した時と同じ終わり方と上限を出せるようにするためである。

| 終える場所 | 状態ファイルに足すもの |
| --- | --- |
| 修正担当を起動する前に秒か費用に当たった | そのラウンドの記録に `stopped_limit`（修正担当を起動しなかった印）と、`limit_end` |
| 順 3（次のラウンドを始める前。直前のラウンドは修正まで終えている） | `limit_end` |

- `limit_end` は、`_drive_rounds` が `_resume` を呼ぶ前に消す。前の起動の終わり方を残さないためである
- `report_decide.final_of` は `limit_end` の終わり方を返す。`ndf cross-review report` は、`limit_end` の `advice` の文を記録のコメントの注意に足す

`_resume` は設計の「消費量の保存と、中断したラウンドの再開」の表に従い、次の順に当てる。

| 順 | 最後のラウンドの状態 | `_resume` |
| --- | --- | --- |
| 1 | `spent.running` が残っている（`verdict` の有無によらない。修正担当の実行中に中断したラウンドは `verdict` を持つ） | `spend.recover` で中断した起動の分を足して保存し、順 2 へ進む |
| 2 | `verdict` が `changes_required` で `stopped_limit` があり `fix` が無く、順 4 に挙げる値が記録と同じ（head の比べ方だけは表の下） | `_fix` で同じ番号のラウンドの修正担当を起動する。相手役と判定役は起動しない。秒か費用の上限に当たったままなら `_fix` は起動せず、`stopped_limit` を残して `limit`。起動の時点では `stopped_limit` を残し、`fix` か `verdict: error` を書く保存で消す。返りは `_round` の修正の後と同じ（`error` か None） |
| 2a | ほかで `verdict` がある（`stopped_limit` があっても順 4 に挙げる値が違う場合を含む） | ここで終える。修正の結果の取り込みは今の `_ingest_pending` がする。未解決の行は次のラウンドで修正担当へ渡る |
| 3 | `candidates` が無い | `verdict: interrupted` にして None |
| 4 | `candidates` があり、head・比較基点・相手役・重点・単位の指示・PR・MVV が記録と同じ | `_decide` から同じ番号のラウンドを続け、修正が要れば `_round` と同じく `_fix` で修正担当まで進める。`unread_reviewers` があれば `incomplete_reviewers` で終える |
| 5 | `candidates` があり、順 4 のどれかが違う | `verdict: interrupted` にして `candidates` を消し None |

順 2 の head は、worktree の `HEAD` でなく、起動の始めの同期で読んだ origin の PR ブランチの先頭（`SyncResult.tip`）を記録の head と比べる。修正担当がコミットした後、結果ファイルを書く前に中断すると、そのコミットは送られないまま worktree に残り、`HEAD` は記録の head と違うためである。

| 起きたこと | origin の先頭 | 順 2 |
| --- | --- | --- |
| 中断した修正担当の未送信のコミットが worktree にある | 記録の head と同じ | 同じラウンドの修正担当を起動する |
| ほかの人がコミットを送った | 記録の head と違う | 順 2a |

順 2 の `_fix` は、記録の head をレビューした head として `send_fix` へ渡す。起動し直した修正担当が新しいコミットを作らなくても、残っていたコミットが送られる。

`_stale_skips` は、見送りの行のうち次のどちらかに当たる行の番号を返す。ここで `current` は、このラウンドの比較基点と head の組（引数の `changes.Range`）である。

- (a) 行の `skipped_head` が今の head と違う（`skipped_head` が無い行も含む）
- (b) 行の `skipped_base` が今の比較基点と違い、かつ `changes.new_in_pr(g, Range(skipped_base, skipped_head), current)` が空でないか、比べられない（None）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_over` | 渡した状態が壊れている | `other_records` の `StateBroken` | `spend` | 止める | `invalid` | 保存先を直す |
| `_resume` | 判定役の起動に失敗 | 上限・失敗 | `verify.ask` | 既定値で続ける（行を確定する） | 注意 | 無し |
| `_stale_skips` | 比べられない | コミットが無い | `new_in_pr` が None | 既定値で続ける（確かめが要る側に入れる） | 注意 | 無し |
| `_collect` | 全員が読めない | 今と同じ | `Reviewed.silent` | 止める。記録は残す | `precondition` | 打ち直すと上限に数える |
| `_decide` | 秒か費用に当たった | `_over` | 判定役を起動する前 | 判定役を起動せずに行を確定して保存し、判定へ進む。修正が要れば修正担当も起動せず、`stopped_limit` を書いて `limit` | 上限の結果 | 上限を上げて打ち直すと続く |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_over` | 当たった上限と使った量 | 要約・`warnings`・過程 | 指摘の本文 |
| `_collect`・`_decide` | `candidates`・`unread_reviewers`・`stopped_limit`・`interrupted`・`limit_end` | 状態ファイル | 指摘の本文は状態ファイルの外へ出さない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_over` | `budget_decide.counted`・`hit`、`spend.other_records` | `drive.py` を読んだ | 3 つを呼ぶだけにする |
| `_resume` | `drive._ingest_pending`・`_resend`（中断の回収） | 同上 | 修正の中断はこの 2 つが扱う。判定の前の中断を扱う部品は無い |
| `_stale_skips` | `changes.new_in_pr`（#3780） | #3780 の詳細設計 | 比較基点の変化の扱いはこれを使う |
| `_collect`・`_decide`・`_fix` | `drive._round`・`fixer.run_fixer`・`send_fix` | `drive.py` を読んだ | 60 行の上限（`NDF701`）を超えないように分ける。修正の起動と送りは今の `run_fixer`・`send_fix` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_over`（`run` を通す） | 収束せずに終えた後の打ち直しは残りの回数だけ回る。収束の後は 0 から | 起動の時点で使い切ると相手役を起動しない。その後の `report` が同じ終わり方と当たった上限を出す。上限を上げた打ち直しは `limit_end` を消す。上限ちょうどの最後のラウンドは修正まで行う。秒か費用が上限ちょうどなら判定役も修正担当も起動しない | 打ち直すたびに 0 から数え直す誤った実装は、このテストで失敗する |
| `_resume` | 判定役の実行中の中断は相手役を起動せずに続き、相手役の費用を 1 回だけ数える | 一部が読めなかったラウンドは `incomplete_reviewers`。保存済みで上限なら判定役を起動しない。修正担当の実行中の中断（`verdict` がある）でも、中断した起動の秒を `spent.seconds` へ、claude の数を `spent.unread` へ 1 回だけ足す | head が変われば `interrupted` で読み直す。相手役の実行中の中断も 1 ラウンドに数える。ラウンド数の上限ちょうどのラウンドが秒の上限で修正を起動せずに終えた後、秒の上限だけを上げて打ち直すと、相手役を起動せずに修正担当だけを起動する。その修正担当が結果を書く前に中断しても、次の打ち直しは記録の不足で止まらず、修正担当を起動する。コミットした後の中断でも同じラウンドの修正担当を起動し、残ったコミットを送る。origin の先頭が進んでいれば起動しない |
| `_collect` | 記録と `spent` が残る | 全員が読めないラウンドも数え、読んだ head にしない | 無し |
| `_decide`・`_round` | 指摘 0 件は `converged`（受け入れ条件 2） | 修正を起動せずに終えた記録に `stopped_limit` が付き `verdict` は変わらず、次の起動は記録の不足で止まらない | 比較基点を変えて起動すると見送りの理由を確かめる |
| `require_fix_for_changes` | `stopped_limit` があり結果ファイルが無ければ `OK` | `stopped_limit` と結果ファイルの両方があれば `INGEST` | `stopped_limit`・修正の記録・結果ファイルのどれも無ければ `STOP` |

## 終わり方と残し方（finish_decide・scope_decide・round_scope・reviewed_pr・leftover・report_decide・cli）

満たす決定: 決定 4・7、受け入れ条件 1・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `finished` | `scope_decide.py` | 新設 | `finished(record: Mapping[str, Any]) -> bool` | 読んだ証跡に使える記録か（`interrupted` と、`spent` か `candidates` を持ち `verdict` の無い記録は偽） |
| `read_all` | `round_scope.py` | 新設 | `read_all(g: git.Git, state: Mapping[str, Any], basis: scope_decide.Basis, current: changes.Range) -> Optional[Mapping[str, Any]]` | 設計の「全員が読めた head」の条件を満たすラウンドの記録 |
| `of_record` | `reviewed_pr.py` | 新設 | `of_record(run: Run, record: Mapping[str, Any]) -> Optional[str]` | その記録の読んだ PR が今回読んだ PR と同じなら返す |
| `_limit_verdict` | `finish_decide.py` | 新設 | `_limit_verdict(review: Review, gaps: Sequence[Mapping[str, str]], decisions, counts: str) -> Verdict` | 設計の上限の結果の表 |
| `filed_number` | `leftover_decide.py` | 新設 | `filed_number(reason: Optional[str]) -> Optional[int]` | 区分 `filed` の理由の最初の `#<番号>` |
| `result_fields` | `leftover_decide.py` | 変更 | `result_fields(row, comment_url, *, limit_left: bool = False)` | 出直した見送りと上限で残った行にも `disposition` と `url` を出す |
| `file_issues` | `leftover.py` | 変更 | `file_issues(data, ref, port, save, primary, *, manual: bool = False, limit_left: bool = False)` | 候補に、出直した見送りと上限で残った行を足す |
| `read_by_all_this_run` | `reviewed_pr.py` | 変更 | シグネチャは変えない | `finished` でない記録を飛ばす |

`Review` に `limited: Optional[Limited] = None` を足し、`summary_of`・`as_review` が通す。`read_by_all_head`・`origin_record`（#3780）は `finished` でない記録を飛ばす。`report_decide.final_of` は状態の `limit_end` の終わり方を読み、`FINAL_LABEL` に `limit` を足し、`_finding_rows` は `reraised` を、`_round_rows` は `interrupted` を表に出す。`spent.unread` が 1 以上なら注意に数を載せる。

`cli` は `--budget-state <保存先>`（繰り返せる）を足し、上限で終わった起動は `read_all` の head を `--reviewed-out` に、`of_record` の PR を `--reviewed-pr-out` に書く。

- `read_all`
  - 副作用: `git` を読むだけである
- `file_issues`
  - 事後条件: 区分 `filed` の行は起票せず、理由の番号の Issue の URL を `issue` に持つ。`not_applicable` の行は起票しない。それ以外は #3780 の `files_issue` で決める。起票の伏せ字の順（全文を伏せてから題を切り出す）は #3780 の起票と同じ経路を通る

### 処理の手順

`_limit_verdict` は `mvv_unresolved`・`mvv_record_failed` を先に判定した後に当てる。

| `head_read` | 欠け | 全員が読めた head と当たった上限 | 結果 |
| --- | --- | --- | --- |
| 真 | 無し | — | `ok`。到達した上限と続け方を警告に出す |
| 真 | 有り | — | `incomplete` |
| 偽 | 有り | — | `incomplete`。欠けに読んでいない head（`unread_head`）を足す |
| 偽 | 無し | 全員が読めた head があり、当たったのが `--max-rounds`（`max_rounds`）だけ | `unreviewed` |
| 偽 | 無し | 全員が読めた head が無い。または、`limits.rounds`・`limits.seconds`・`limits.usd` のどれかに当たった（`--max-rounds` にも当たっていてもこの行） | `precondition` |

`read_all` は、次をすべて満たすラウンドの記録を返す。記録は `scope_decide.origin_record` で探す。

- 今回の相手役の席の全員が、そのラウンドを読んでいる。そのラウンドで代わりに入った claude の席は、欠けた相手役が読んだと数える（`Basis` の `stand_in` を真にする）
- 記録の読んだ PR（`pr`）が、今回読んだ PR と同じである。今回の値は今の `drive._basis` が持つもので、読んだ PR の確認を求める起動（`--reviewed-pr-out`）でだけ値を持つ
- 記録の head が今の head かその祖先である
- 比較基点が違う場合は、`changes.new_in_pr` が空である

起票の候補は次のとおりである。

| 行 | 残し方 |
| --- | --- |
| 区分 `filed` の出直した見送り | 起票し直さず、理由の番号の Issue の URL |
| 区分 `not_applicable` の出直した見送り | 記録のコメント |
| 区分 `needs_user` か区分の無い出直した見送り | `files_issue` なら起票、ほかは記録 |
| 上限で残った未解決の重い指摘（`limit_left`） | `files_issue` なら起票、ほかは記録。`parked` を付けない |
| `issue_error` のある行 | 打ち直しで起票し直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `LIMIT = "limit"` | 終わり方 | `finish_decide.py` | `FINALS` に足す |
| `Limited(hits: tuple[str, ...], head_read: bool, delta: bool, notes: tuple[str, ...])` | 型 | `finish_decide.py` | `delta` は、全員が読めた head があり、`hits` が `max_rounds`（`--max-rounds`）だけのとき真 |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_all` | 比べられない | コミットが無い | `new_in_pr` が None | 呼び出し元へ返す（None。読んでいない扱い） | `precondition` | 打ち直せる |
| `of_record` | 記録に PR が無い・違う | `check_pr` でない実行 | 値の比較 | 呼び出し元へ返す（None。`--reviewed-pr-out` を書かない） | 無し | 無し |
| `_limit_verdict` | 上限の情報が無い要約 | `finish` に古い要約を渡した | `limited` が None | 既定値で続ける（今の判定） | 今のとおり | 無し |
| `file_issues` | 起票の失敗 | #3780 の異常系 | 同じ | 同じ（`issue_error`） | 注意 | 次の起動か `report` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `finished` | `read_by_all_head` の `INCOMPLETE_REVIEWERS` の除外 | `grep -rn "INCOMPLETE_REVIEWERS" src/ndf` | 同じ除外の場所に足す条件を 1 つにまとめる |
| `read_all` | `scope_decide.origin_record`・`round_scope.origin`・`changes.new_in_pr`（#3780） | #3780 の詳細設計 | 条件を組み合わせるだけにする |
| `of_record` | `reviewed_pr.confirmed` | `reviewed_pr.py` を読んだ | `confirmed` は今回の起動で読んだ head だけを扱うので、記録の PR を照らす関数を足す |
| `_limit_verdict` | `_finish`・`_limit_decision` | `finish_decide.py` を読んだ | 上限の表は今の `_limit_decision`（欠けが無ければ ok）と違うので分ける |
| `filed_number` | `leftover_decide`（#3780）・`publish._BARE_REF` | `grep -rn "_BARE_REF" src/ndf` | `_BARE_REF` は文全体が参照の場合だけに一致するので使えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_limit_verdict` | 表の 5 行 | 相手役を足した打ち直しは同じ head でも `ok` にならない。修正を送って上限に当たった起動と、その打ち直しが同じ結果になる | 修正で送ったコミット（`fix.pushed`）を読んだ head とみなす誤った実装は、このテストで失敗する |
| `read_all` | 比較基点が同じなら返る | 競合の無いベースの取り込みは返る。比較先を変えて差分から消えただけなら返る | 比較先を変えて新しい変更が現れると None |
| `finished` | `interrupted` と未完の記録は偽 | `verdict` の無い古い記録（`spent` も無い）は真 | 無し |
| `file_issues`・`result_fields` | 出直した見送りと上限で残った指摘の URL が `items` に載る。上限で残った未解決の critical は、起票してよい種類を持てば起票する | 区分 `filed` は起票し直さない。保護の対象の指摘は、上限で残って起票しても未解決のままである。上限を上げて同じ保存先で打ち直すと修正担当へ渡り、別の保存先で始めた実行（差分のレビューなど）にも未解決の指摘として引き継がれて修正担当へ渡る | **出直した見送りと上限で残った指摘の起票でも、題の境目をまたぐ偽の秘密の断片が題にも本文にも残らない** |

## プランの経路と単位のレビューの包み（impl.json・route）

満たす決定: 決定 4・7、受け入れ条件 3・4

`contracts/templates/impl.json` を次のとおり変える。

| ステップ | 変えること |
| --- | --- |
| `review` | 起動の引数に `--budget-state {state_dir}/cross-review-delta` を足し、`on_cause` に `"unreviewed": "push-reviewed"` |
| `review-delta` | 起動の引数に `--budget-state {state_dir}/cross-review` を足す |

`precondition` の経路は足さない。設計・点検・cross-review のテンプレートは変えない。

`core/route` の `UNIT_REVIEW` は、`--reviewed-pr-out` の中身が対象の PR（`<リポジトリ>#<番号>`）と一致すれば、終了コードによらず `--reviewed-out` のファイルを残す。一致しなければ消し、終了コードが 0 か 6 なら今のとおり `precondition` にする。今のテスト（`test_unit_review_keeps_the_exit_code_of_a_failed_launch_and_removes_the_reviewed_head`）は終了コード 4 で PR が一致する場合に消すことを確かめているので、この変更で期待を直す。

テンプレートの単体テストの観点は、`review` の `unreviewed` が `review-delta` へ進むこと、`--budget-state` が互いの保存先であること、包みが `unreviewed`（終了コード 3）でファイルを残すこと、PR が一致しなければ消すことである。

## キュー（queue_decide・queue_run・core/plan/cli）

満たす決定: 決定 6、受け入れ条件 5（不変条件 I4）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `next_stop` | `queue_decide.py` | 新設 | `next_stop(previous: Optional[Mapping[str, Any]], status: Optional[str], kind: Optional[str], step: Optional[str]) -> Optional[dict[str, Any]]` | プランの結果から次の `stop`（`step`・`count`）を決める |
| `refused_repeat` | `queue_decide.py` | 新設 | `refused_repeat(row: Mapping[str, Any], limit: int) -> Optional[str]` | 回数が上限以上なら止まったステップを返す |
| `_clear_stops` | `queue_run.py` | 新設 | `_clear_stops(qdir: Path, names: Collection[str]) -> None` | `queue.json` の指定したプランの `stop` を消して保存する |
| `_record` | `queue_run.py` | 変更 | シグネチャは変えない | 行の `stop` を `next_stop` で保つ |
| `_launch` | `queue_run.py` | 変更 | シグネチャは変えない | `--resume` で上限のプランは起動せずに `repeated_stop` の結果にする |

`QueueOptions` に `retry: tuple[str, ...] = ()` を足す。`core/plan/cli` は `--retry <プラン>`（繰り返せる）を足し、`--resume` が無いか、キューに無い名前なら `invalid`。

- `_clear_stops`
  - 副作用: `queue.json` を書く

### 処理の手順

| 結果 | `next_stop` |
| --- | --- |
| `ok` | 消す |
| 停止（`repeated_stop` と前提の未完了の `precondition` を除く）で止まったステップが前と同じ | 回数に 1 を足す |
| 停止でステップが違う | そのステップで 1 |
| 承認の待ち・起動せずに返した結果・ステップの分からない停止 | 変えない |

起動の始め、子のプランを起動する前に、保存した停止の回数（`queue.json` の `stop`）を次のとおり消して保存する。

| 打ち方 | 消す回数 |
| --- | --- |
| `--resume` なし | 全プランの分 |
| `--resume` だけ | 消さない |
| `--resume` と `--retry <プラン>` | 指定したプランの分だけ |

回数の確かめは `--from` の指定に関係なく行う。起動せずに返す結果は、`step` に保存した `stop.step` を入れ、`next` に `--from <プラン>=<ステップ>` が付くようにする。`--retry` は `next` に入れない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `refused_repeat` | 回数が上限 | 同じステップの停止 | `_launch` | 呼び出し元へ返す | `status: stopped`・`cause.kind: repeated_stop`・終了コードは失敗の値、設計の案内の文 | 原因を直し `--retry` を足して打つ |
| `_clear_stops` | `queue.json` を書けない | 権限・空き容量 | `OSError` | 呼び出し元へ返す（今の `_record` と同じ） | 今のとおり | 打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `plan.queue.same-step-stops` | 設定値 | `contracts/limits/` | 2 |
| `repeated_stop` | 停止の種類 | `contracts/causes/repeated_stop.json` | `std_type: runtime`、`exit: failed`、`route: human`、`retry: null` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `next_stop`・`refused_repeat` | `core.route.streak`・`repeated`（同じ原因の続き） | `grep -rn "def .*repeated\|def streak\|def stop_" --include=*.py src/ndf/core` | 数える単位が違う（原因のキーの列でなく、プランごとのステップと回数）ので使えない |
| `_clear_stops` | `queue_run._recorded`・`files.write_json` | `queue_run.py` を読んだ | 読み書きはこの 2 つを使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `next_stop` | 同じステップで 1、2 と増える | 違うステップは 1。完了で消える | 承認の待ちと `repeated_stop` で変わらない |
| `refused_repeat`・`_launch` | 2 回止まったプランの `--resume` は起動せず `repeated_stop`。`next` の `--from` は止まったステップ | `--retry` で起動。`--from` で別のステップを指定しても起動しない | 前提にするプランは流さない |
| `_clear_stops` | `--resume` なしの起動は全部消して起動し、起動しなかった後段のプランの回数も消える | `--retry` は指定したプランだけ | `--retry` を `--resume` なしで渡すと `invalid` |

## 手順書

満たす決定: 決定 1〜7、受け入れ条件 3・4・5

`skills/cross-review/` の「ラウンドを足す」を、合計で数える数え方・上限の設定・終わり方 `limit`・上限の結果の表に書き換え、見送りの出直しの扱いを足す。文言を照合するテストは書かない。
