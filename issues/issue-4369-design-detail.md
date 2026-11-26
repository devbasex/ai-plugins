# #4369 の詳細設計: 工程「実装」をレビューの単位ごとに流す

[設計書](./issue-4369-design.md)の構成要素を、ファイルと関数の粒度へ下ろした文書である。決定の番号は[決定の記録](./issue-4369-design-decisions.md)、条件の番号は[要求仕様](./issue-4369-requirements.md)を指す。引数の意味、単位の表の読み方、打ち直しの表、テンプレートのステップの並びは設計書が持つので、写さずにセクション名で指す。パスは `plugins/ndf/` からの相対である。

## 地図

```text
src/ndf/
├── core/issue_docs.py
├── core/plan/review_units_decide.py   *
├── core/route/__init__.py
├── progress/plan_runs.py              *
├── progress/issue_copies.py           *
├── progress/units_invoke.py           *
├── progress/invoke.py
├── progress/stages.py・definition.py・refactor_values.py
├── adapters/github.py
├── ci_cli.py
├── contracts/worker_kinds.py
├── domains/loop/acceptance.py
└── domains/crossreview/{cli,settings,drive,fixer,criteria_decide,finish_decide,journal}.py
contracts/
├── workflow/stages/implement.json・workflow/ndf.schema.json
├── schemas/{internal/stage-snapshot,project-workflow}.schema.json・schemas/io/
├── templates/{impl,pr,params}.json・templates/parts/steps.json
├── worker/kinds.json
├── data/review-criteria.json
└── domains/env.json
```

依存の向きは一方向である。`invoke.py` → `units_invoke.py` → `plan_runs.py`・`issue_copies.py` → `core/` → `adapters/` の順に読み、逆向きの import は置かない。`units_invoke.py` がプランやコマンドを打つときは、`invoke.py` が渡す関数（`Ports`）を呼ぶ。`progress/` は `domains/` を import できない（`src/ndf/checks/rules.json` の層の並び）。そのため作業場所の用意は `ndf worktree create`、マージは `ndf ci merge` を子プロセスで打つ。

同じファイルの規則は、1 ファイル 1,000 行以下も求める。`invoke.py` は 985 行あるので、型とプランの実行の記録の読み取りを `plan_runs.py` へ、課題の題と本文のコピーを `issue_copies.py` へ先に移す（振る舞いを変えない移動）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/plan/review_units_decide.py` | 新設 | 実装計画の本文から単位の並びか読めない理由を返す | `inputs_decide` | `Unit`・`PlanDoc`・`UnitsUnreadable` | `has_units`・`lineup`・`acceptance_args`（新設） |
| `src/ndf/core/issue_docs.py` | 変更 | 実装計画のパスから課題の番号を読む | — | モジュール | `plan_issues`（新設） |
| `src/ndf/progress/plan_runs.py` | 新設 | 工程の呼び出しと結果の型、プランの実行の記録の読み取りを持つ | `core.plan`・`core.route` | `StageRun`・`StageResult`・`UnitsStopped` | `previous`・`from_of`・`read_report`（移動） |
| `src/ndf/progress/issue_copies.py` | 新設 | 課題の題と本文を保存先へコピーする | `adapters.github`・`worker_paths` | モジュール | `issue_text`・`issue_values`（移動して変更） |
| `src/ndf/progress/units_invoke.py` | 新設 | 開始前の確かめ、単位ごとの実行とマージ、スプリントの PR を流す | 上の 3 つ・`git`・`github`・`branch_strategy` | `Ports`・`Lineup`・`RoundState`・`Resume` | `prepare`・`run_stage`・`resume_of` ほか（下の表） |
| `src/ndf/progress/invoke.py` | 変更 | `per_unit` の工程を単位を流す処理へ渡す | `units_invoke` | モジュール | `stage_run`・`prepare_plan`・`plan_path`・`plan_argv`・`_call_plan`・`invoke`・`template_values`・`clear_values`（変更） |
| `src/ndf/progress/stages.py`・`definition.py` | 変更 | 工程の型に `per_unit` を足して読む | — | `StageDef` | `_stage_def`（変更） |
| `src/ndf/progress/refactor_values.py` | 変更 | 見出しの定数を単位の表の読み手から読む | `review_units_decide` | モジュール | — |
| `src/ndf/core/route/__init__.py` | 変更 | `impl` のレビューの起動と、単位の経路のテンプレートを組む | — | モジュール | `unit_template`・`unit_review_launch`（新設）、`review_launch`（変更） |
| `src/ndf/adapters/github.py` | 変更 | マージ済みの単位の PR を読み、マージの宛先を確かめる | — | `Pull`・`GitHub` | `merged_pulls`（新設）、`merge`・`_merge_with`・`_preflight`・`Pull.from_rest`（変更） |
| `src/ndf/ci_cli.py` | 変更 | `--base`・`--pr` でマージの対象を決める | `github` | モジュール | `_target`（新設）、`_merge`（変更） |
| `src/ndf/contracts/worker_kinds.py` | 変更 | 作業の定義の空でよい入力を渡す | — | モジュール | `kind_values`（変更） |
| `src/ndf/domains/crossreview/cli.py` ほか | 変更 | 新しい引数と単位の範囲の指示を扱う | `repo`・`mvv_decide` | `RunConfig`・`Finding` | `finish`・`unit_block`・`_unit_text`・`_write_reviewed_pr`（下の表） |
| `src/ndf/domains/loop/acceptance.py` | 変更 | `--no-criteria` を受ける | `github` | モジュール | `_check`・`add_parsers`（変更） |

`contracts/` のファイルと文書は、下の各セクションの本文で扱う。

## 単位の表の読み手

満たす決定: 決定 2・7・9、受け入れ条件 1・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `plan_issues` | `core/issue_docs.py` | 新設 | `(path: str) -> frozenset[int]` | `issues/issue-<番号の並び>-plan.md` の番号を返す。形が違えば空 |
| `has_units` | `core/plan/review_units_decide.py` | 新設 | `(text: str) -> bool` | コードブロックの外に単位の見出しがあるか |
| `lineup` | 同上 | 新設 | `(plans: Sequence[PlanDoc], criteria: Mapping[int, int], issues: Sequence[int]) -> tuple[Unit, ...]` | 全実装計画の表を読み、補った単位を足して流す順に並べる |
| `acceptance_args` | 同上 | 新設 | `(unit: Unit) -> list[str]` | 設計書の「単位の代表の課題と `acceptance` の組み方」の引数の並びを返す |

- `lineup`
  - 事前条件: `plans` はスプリントの課題の実装計画で、少なくとも 1 つに単位の見出しがある。`criteria` はスプリントの課題ごとの受け入れ条件の数（`inputs_decide.criteria` の長さ）
  - 事後条件: 返す並びはそのまま流す順である。「タスク分解」の各タスクは 1 つの単位にだけ入る

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 中身 |
| --- | --- | --- | --- |
| `PlanDoc` | 型 | `review_units_decide.py` | `path`・`issues: frozenset[int]`・`text` |
| `Unit` | 型 | 同上 | `name`・`issue`（代表の課題）・`plan`（パス。補った単位は空）・`tasks: tuple[int, ...]`・`criteria: tuple[tuple[int, int], ...]`（課題と条件の番号）・`supplemented: bool` |
| `UnitsUnreadable` | 例外 | 同上 | `reasons: tuple[str, ...]`（`<パス>: <理由>`） |
| 見出しの語・列の名前 | 定数 | 同上 | `レビューの単位`・`タスク分解`・`リファクタリングの時機`・`単位`・`含むタスク`・`満たす受け入れ条件`・`実行順`。`TIMING_HEADING` は `progress/refactor_values.py` にもあるので、定数はこのファイルへ移して読ませる |

### 異常系の処理

どの失敗も、全実装計画を読み終えてから理由をまとめて `UnitsUnreadable` にする。呼び元は工程を始めずに `invalid` で止め、実装計画を直せば打ち直せる。

| 関数 | 失敗 | 検出する場所 | 扱い |
| --- | --- | --- | --- |
| `lineup` | 設計書の「単位の表の読み方」の「読めない」の各形 | 表と各行の読み取り | 呼び出し元へ返す |
| `lineup` | 2 つの実装計画の単位が、代表の課題も名前も同じ（ブランチ名が重なる） | 並べた後 | 呼び出し元へ返す |

### 処理の手順

| 条件 | 結果 |
| --- | --- |
| 単位の見出しがある実装計画 | 表の行を単位にする。並びの鍵はファイル名の番号の最小 |
| どの単位の見出しの実装計画の番号にも入らないスプリントの課題 | 課題 1 つを補った単位にする。名前は `issue-<番号>`、並びの鍵はその番号 |
| 「実行順」の列がある | 計画の中を値の順（同じ値は表の順） |
| 列が無く、時機が「実装の前」で始まる行の対象が「しない」で始まらない | 「タスク分解」の最初の `### Task` を含む単位を先頭にする |
| 上のどれでもない | 表の順 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `has_units`・`lineup` | `inputs_decide.outside_fences`・`split_row`・`criteria` | `inputs_decide.py` を読んだ | 使う |
| `has_units`・`lineup` | `adapters/markdown.py` の `atx_headings` | `rules.json` の `decide_forbidden_imports` | 使えない（判断のモジュールは `ndf.adapters` を import できない） |
| `acceptance_args` | `route.launch_values` | `core/route/__init__.py` を読んだ | 使えない（条件の指定と `--no-criteria` を組まない） |
| `plan_issues` | `issue_docs.design_issues` | `issue_docs.py` を読んだ | 同じ形の正規表現を実装計画の側に足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `plan_issues` | `issue-7-8-plan.md` | 番号の一部（`78` と `7`） | 設計書のパスは空 |
| `has_units` | `##`・`###` の見出し | コードブロックの中だけの見出しは偽 | — |
| `lineup` | 列あり・列なし・プレリファクタリングの有無・補った単位の混在 | 範囲 `Task 1〜4`・`T1〜T4`、接頭辞の省略、`無し`、課題 1 つの番号だけのセル | 「読めない」の各形、`implementation-plan` の手順書の例の行は名前を埋めれば読める |
| `acceptance_args` | 条件あり（2 課題） | 条件なし・補った単位 | — |

## 単位を流す処理

満たす決定: 決定 4〜6・8・10・13〜16、受け入れ条件 1〜6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `prepare` | `progress/units_invoke.py` | 新設 | `(run: StageRun, work_dir: Path, cwd: Path) -> Optional[Lineup]` | 設計書の「開始前の確かめ」を行い、単位の経路なら並びを `units.json` に書く。これまでの経路なら None |
| `run_stage` | 同上 | 新設 | `(run: StageRun, work_dir: Path, cwd: Path, ports: Ports) -> Optional[StageResult]` | 全単位を順に流し、スプリントの PR を開く。これまでの経路なら None |
| `released_branches` | 同上 | 新設 | `(section: Mapping) -> frozenset[str]` | ブランチ戦略の設定から配布先のブランチを集める（I4） |
| `unit_branch` | 同上 | 新設 | `(sprint: str, unit: Unit) -> str` | `<sprint>-u<issue>-<名前の sha256 の先頭 8 字>` |
| `unit_worktree` | 同上 | 新設 | `(ctx: Context, branch: str, ports: Ports) -> Path` | 取得し直し、設計書の「単位のブランチと保存先」の表で作業場所を用意する |
| `include_sprint` | 同上 | 新設 | `(ctx: Context, worktree: Path) -> None` | スプリントブランチの先端を含まなければ `git merge --no-edit` で取り込む |
| `round_state` | 同上 | 新設 | `(unit_dir: Path) -> RoundState` | `run.json` を持つ最新の回の、結果・マージの結果・`reviewed-head`・`stopped-head` を読む |
| `resume_of` | 同上 | 新設 | `(state: RoundState, head: str, local_tests: bool) -> Resume` | 打ち直しの扱いを決める（純粋な関数） |
| `unit_run` | 同上 | 新設 | `(ctx: Context, unit: Unit, worktree: Path, round_dir: Path, start: Optional[str]) -> StageRun` | 単位の指示のファイルを書き、`impl` の呼び出し（上書きの値・テンプレート・環境変数）を組む |
| `merge_unit` | 同上 | 新設 | `(ctx: Context, branch: str, worktree: Path, round_dir: Path, ports: Ports) -> dict` | `ndf ci merge` を打ち、結果の種類を `merge.json` に残し、成功ならスプリントの作業場所を ff で進める |
| `sprint_pr` | 同上 | 新設 | `(ctx: Context, ports: Ports) -> StageResult` | 先端を確かめ、テンプレート `pr` を push のステップなしで流す |

- `prepare`
  - 事前条件: `run.per_unit` が真
  - 事後条件: 単位の経路なら、スプリントの作業場所の HEAD が選んだリモートのスプリントブランチと同じになり、`work_dir/units.json` に並びがある。これまでの経路なら `units.json` に経路だけを書き、作業場所は変えない
  - 副作用: `git fetch`・`ls-remote`・`merge --ff-only`・`push`、課題の本文の一括読み取り（`GitHub.nodes`）
- `run_stage`
  - 事前条件: `units.json` が無ければ `prepare` を先に呼ぶ
  - 事後条件: `ok` なら全単位がマージ済みの PR を持ち（I5）、スプリントの PR が開いている
  - 副作用: 作業場所の作成、プランの実行、PR のマージ、保存先のファイル
- `unit_worktree`・`include_sprint`・`merge_unit`・`sprint_pr`
  - 副作用: git と GitHub の読み書き。`unit_worktree` は `ndf worktree create <ブランチ> --from <スプリントブランチ>` を打つ

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 中身・既定値 |
| --- | --- | --- | --- |
| `Ports` | 型 | `units_invoke.py` | `call_plan(run, work_dir) -> StageResult`、`command(argv, cwd, env) -> CompletedProcess` |
| `Context` | 型 | 同上 | 工程の呼び出し・保存先・スプリントの作業場所とブランチ・リモート・`<所有者>/<リポジトリ>`・設定・待つチェックとそのキー |
| `Lineup` | 型 | 同上 | `units: tuple[Unit, ...]`。`units.json` は `{"route": "units" か "plain", "units": [...]}` |
| `RoundState` | 型 | 同上 | `round_dir`・`done`（結果のバックアップ。無ければ None）・`merge`・`reviewed`・`stopped_head` |
| `Resume` | 型 | 同上 | `action`（`run`・`merge`）・`new_round: bool`・`start: Optional[str]` |
| `REVIEW_STEPS` | 定数 | 同上 | `review`・`review-delta`・`resolved` |
| `BEFORE_TEST` | 定数 | 同上 | `work`・`acceptance`・`test`・`test-repeat`・`fix` |
| `MERGE_ONLY` | 定数 | 同上 | `ci_pending`・`infra_wait` |

保存先のファイルは次のとおりである。

| ファイル | 書く側 |
| --- | --- |
| `<work_dir>/units.json` | `prepare` |
| `<work_dir>/units/<単位のブランチ>/<回>/unit.md` | `unit_run`（流すたびに書き直す） |
| 同じディレクトリの `pr-number`・`reviewed-head`・`reviewed-pr` | `impl` のプラン |
| 同じディレクトリの `stopped-head` | `run_stage`（`impl` が返った直後の HEAD） |
| 同じディレクトリの `merge.json` | `merge_unit` |
| `<work_dir>/sprint-pr/` | テンプレート `pr` のプラン |

`unit.md` には、単位の名前・含むタスク・満たす条件（番号だけ。課題の本文は写さない）・実装計画のパス・単位の外の扱い・例外（決定 8）を書く。補った単位は書かず、`unit_file` と `NDF_UNIT_FILE` を渡さない。

### 処理の手順

単位 1 つは、次の順で扱う。

1. マージ済みの一覧（開始時に `merged_pulls` で 1 回読む）にあれば、`already_merged` にして次へ進む
2. `unit_worktree` で作業場所を用意し、`round_state` を読む
3. 取り込みの前の HEAD で `resume_of` が `merge` を返せば、手順 6 へ進む（HEAD を変えるとレビューした HEAD と合わなくなる）
4. `include_sprint` の後の HEAD で `resume_of` を読み直し、`unit_run` を `ports.call_plan` で流す
5. `stopped-head` を書く。`ok` でなければ止まる
6. `merge_unit` を打つ。`ok` でなければ止まる

`resume_of` の判定表（上の行を先に当てる）:

| 回の状態 | HEAD | 結果 |
| --- | --- | --- |
| 回が無い | — | `run`。1 回目の保存先、最初から |
| 結果が `ok`、`merge` が `MERGE_ONLY` | `reviewed` と同じ | `merge` |
| 結果が `ok`、または止まったステップが `REVIEW_STEPS` | `stopped_head` と違うか、無い | `run`。次の回、`test` から（手元のテストを外す設定は `push`） |
| 同上 | 同じ | `run`。次の回、`push` から |
| 止まったステップが `BEFORE_TEST` の外 | 違うか、無い | `run`。次の回、`test` から（外す設定は `push`） |
| 上のどれでもない（結果が無い中断を含む） | — | `run`。同じ回で、プランの打ち直しに任せる |

止まったステップは、結果の `items` の先頭（`kind` が `plan`）の `step` から読む。

### 異常系の処理

「止める」は `stopped` を返して後の単位を始めないことをいう。終了コードは停止理由の種類で `contracts/facts/exit-codes.json` が決める。どれも原因を直して `ndf progress next` を打ち直せる。

| 関数 | 失敗 | 扱い（停止理由の種類） | 利用者に見える理由に書くこと | 後始末 |
| --- | --- | --- | --- | --- |
| `prepare` | 取得の失敗（不存在でない） | 始めない（`precondition`） | ブランチと git の理由 | 作業場所は変えない |
| `prepare` | 未コミットの実装計画 | 始めない（`precondition`） | パスと、コミットか取り消し | 同上 |
| `prepare` | `branch` が文脈に無い・今のブランチと違う・配布先 | 始めない（`precondition`） | 当たった条件と直し方 | 同上 |
| `prepare` | ff できない・push の拒否 | 始めない（`precondition`） | ブランチと git の理由 | 強制しない |
| `prepare` | 表が読めない | 始めない（`invalid`） | `UnitsUnreadable.reasons` | — |
| `prepare` | 課題の本文が読めない | 始めない（`github.GitHubError` を `plan_failure` が分ける） | GitHub の理由 | — |
| `unit_worktree` | 手元とリモートが分かれる・`ndf worktree create` の失敗 | 止める（`precondition`） | 単位の名前と理由 | — |
| `include_sprint` | 取り込みの衝突 | 止める（`base_conflict`） | 単位の名前とブランチ | `git merge --abort` |
| `unit_run` | 代表の課題の作成者に書き込み権限が無い | 止める（`precondition`） | 単位の名前と課題 | プランを流さない |
| `run_stage` | `impl` が `ok` でない | 止める（`impl` の種類のまま。`retry_after` も引き継ぐ） | 単位の名前・課題・PR の番号・`impl` の理由。ステップ `ci` なら待つチェックのキー | `stopped-head` を書く |
| `merge_unit` | `pr-number` か `reviewed-head` が無い | 止める（`precondition`） | 単位の名前 | マージを打たない |
| `merge_unit` | `ndf ci merge` が `ok` でない・結果 JSON が読めない | 止める（コマンドの種類。読めなければ `bad_report`） | 単位の名前・PR の番号・理由 | `merge.json` に種類を残す |
| `merge_unit`・`sprint_pr` | 取得か ff の失敗 | 止める（`precondition`） | git の理由 | — |
| `sprint_pr` | リモートの先端が HEAD と違う | 止める（`precondition`） | 両方の SHA、片方にだけあるコミットの範囲、利用者が確認してから打ち直すこと | 取り込みも push もしない |
| `sprint_pr` | テンプレート `pr` が `ok` でない | 止める（返った種類と `gaps`） | 理由 | 単位はやり直さない |
| `run_stage` | git・GitHub の例外 | 呼び出し元へ返す（`invoke.plan_failure`） | 今の分類の文 | — |

`released_branches` はブランチ戦略の設定を読めなければ `StrategyError` を上げ、`prepare` が `precondition` にする。

**止まった結果の残り方**: 工程のステートマシンは、止まったステップの `cause` だけを残し、`items` と `metrics` は残さない（`progress/run.py` の `_step_draft`）。そのため、受け入れ条件 5 が求める単位の名前と理由は、必ず `cause.detail` の文に書く。`items` と `metrics.unit_ci` はステップの結果 JSON に持ち、成功した試行ではステートマシンに残る。

### 並行と排他

工程を流す作業は、ステートマシンの `run_lock` で 1 つに絞られている。単位は 1 つずつ流すので、新しい排他は足さない。打ち直しは、マージ済みの PR の一覧と回ごとの保存先から再開位置を決める。

### ログと記録の出力

| 関数 | 出すもの | 書き先 |
| --- | --- | --- |
| `prepare` | 単位の PR の宛先・待つチェックの名前・読んだキー | 標準エラーの 1 行、`metrics.unit_ci`（`base`・`required`・`key`） |
| `run_stage` | 単位ごとの `{"kind": "unit", "name", "issue", "pr", "status"}`（`merged`・`already_merged`・`stopped`。止まった行は `reason`） | 結果の `items` |

課題の本文と git のリモートの URL は出さない。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `prepare` | `Git.fetch_branch`・`merge_ff`・`status_paths`・`blobs`、`branch_strategy.load` | `adapters/git.py`・`core/branch_strategy.py` を読んだ | 使う。`ls-remote --exit-code` は不存在で終了コード 2（git 2.53.0 で実行して確かめた） |
| `released_branches` | `Strategy` の属性 | `core/branch_strategy.py` | `section` から読む（`channels`・`deliveries` の属性が無い） |
| `unit_branch` | — | `git grep` で `-u<番号>` を組む処理を探した | 無し |
| `unit_worktree` | `domains/worktree/create.create` | `rules.json` の層の並び | import できないので `ndf worktree create` を打つ |
| `unit_worktree` | `Git.fetch`（`--prune` と `refs/heads/<sprint>-u*`） | 一時リポジトリで実行（git 2.53.0）。一致する ref が無くても終了コード 0、消えた ref は掃除される | 使う |
| `include_sprint` | `Git.is_ancestor` | `adapters/git.py` | 使う |
| `round_state`・`resume_of` | `invoke` の `_previous`・`_from_of`・`plan_start` | `progress/invoke.py` | 前の 2 つを `plan_runs.py` へ移して使う。回をまたぐ判断は無い |
| `unit_run` | `invoke.template_values`・`worker_paths.write_file` | 同上 | 上書きの値を渡して使う |
| `merge_unit` | `ndf ci merge`・`result.last_line` | `ci_cli.py` | 使う |
| `sprint_pr` | テンプレート `pr`・`route.without_steps` | `contracts/templates/pr.json` | 使う |
| `run_stage` | `GitHub.pulls_by_head` | `adapters/github.py` | 使えない（宛先で絞らず直近 5 件）。`merged_pulls` を足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `prepare` | 単位の経路・これまでの経路 | リモートにブランチが無い、手元が遅れてリモートに単位がある | 異常系の表の `prepare` の各行 |
| `run_stage` | 順に流して `ok`、飛ばした単位は `already_merged` | 単位 1 つ | 途中の失敗で後を呼ばない |
| `released_branches` | 4 つのキーから集める | `channels`・`deliveries` が無い | — |
| `unit_branch` | 同じ入力は同じ名前 | 日本語の単位の名前 | — |
| `unit_worktree`・`include_sprint` | 設計書の表の 6 行 | 作業場所が既にある | 分かれている・衝突 |
| `round_state`・`resume_of` | 判定表の各行 | `run.json` の無い空の回は数えない、`stopped_head` が無い | — |
| `unit_run` | 上書きの値と環境変数 | 補った単位は `unit_file` が空 | 作成者を信用しない |
| `merge_unit` | `--repo`・`--pr`・`--base`・`--reviewed` を渡す | マージキューの待ち | ファイルの欠け |
| `sprint_pr` | `push`・`fix-push` の無いプラン、`GH_REPO` | 開いた PR がある | 先端の違い・`Refs` の欠け |

## 工程から作業を呼ぶ層

満たす決定: 決定 4・6・9・13、受け入れ条件 1・2

`StageRun` に次の欄を足す。既定値のままの呼び出しは、これまでと同じに動く。

| 欄 | 型・既定 | 使う所 |
| --- | --- | --- |
| `per_unit` | `bool = False` | `stage_run` が工程の定義から写す。単位を流す処理へ渡すかを決める |
| `overrides` | `Mapping[str, route.Value]`（空） | `template_values` が出所の表より先に置く |
| `template` | `Optional[route.Template] = None` | `prepare_plan`・`plan_path` が `templates()` の代わりに使う |
| `env_extra` | `Mapping[str, str]`（空） | `_call_plan` が子の環境へ足す（`GH_REPO`・`NDF_UNIT_FILE`） |
| `start` | `Optional[str] = None` | 回の記録が無いときの `--from` |

`StageRun.plain()` は、`per_unit` を偽にし、入力から `branch` を除いた呼び出しを返す。工程の入力に `branch` を足すと、`template_values` がその値を差し込み `branch` にするためである。これまでの経路は、今のとおり作業場所の今のブランチを使う。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `stage_run` | `progress/invoke.py` | 変更 | 変えない | `StageRun.per_unit` を `stage.per_unit` にする |
| `prepare_plan` | 同上 | 変更 | `(run, work_dir, cwd) -> Optional[PreparedPlan]` | `per_unit` なら `units_invoke.prepare` を呼び、単位の経路なら None。ほかは `run.plain()` で今のまま |
| `invoke` | 同上 | 変更 | 変えない | `per_unit` なら `units_invoke.run_stage` を呼び、None なら `run.plain()` で今のまま |
| `template_values` | 同上 | 変更 | 変えない | `overrides` の名前は出所の表から求めない |
| `plan_path` | 同上 | 変更 | 変えない | `run.template` があればそれを描画する |
| `plan_argv` | 同上 | 変更 | 変えない | `plan_start` が None なら `run.start` を使う |
| `_call_plan` | 同上 | 変更 | 変えない | `run.env(run.env_extra)` で子を打つ。`plan_failure` は `UnitsStopped` を分ける |
| `clear_values` | 同上 | 変更 | 変えない | `units.json` と、`units/` の下のすべての `values.json` も消す。回の記録と `merge.json` は残す |
| `issue_text` | `progress/issue_copies.py` | 変更 | `(location: Path, number: int, repo: Optional[str] = None) -> Optional[tuple[str, str]]` | `repo` があればそのリポジトリから読み、権限もそこで確かめる |
| `issue_values` | 同上 | 変更 | `(run, work_dir, location, *, number: Optional[int] = None, repo: Optional[str] = None) -> Optional[dict]` | 課題とリポジトリの組のコピーを使い回す。省けば今のとおり番号の最小の課題 |

- `issue_values`
  - 事後条件: 題のコピーは `{"title", "repo"}`。`repo` が違うコピーは使わない
  - 副作用: 読み直すときは、題のコピーを消す → 本文を書く → 題のコピーを書く、の順にする

`unit_required_checks` の読み取りは `units_invoke` が行い、`overrides["required"]` で渡す。`invoke.py` の `PROJECT_KEYS` は変えない。

### 異常系の処理

| 関数 | 失敗 | 扱い |
| --- | --- | --- |
| `prepare_plan`・`invoke` | `UnitsStopped`（`kind`・`detail`・`items`） | `plan_failure` が `stopped` にする。工程は始まらないか失敗する |
| `clear_values` | 消せない | `OSError` を上げる（今のとおり `precondition`） |
| `issue_values` | 書き込み権限の無い作成者 | None を返す。呼び元が止める |
| `issue_values` | 私有でないコピー | `worker_paths.EntryUnsafe`（今のとおり） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `prepare_plan`・`invoke` | 経路の分かれ。これまでの経路の `values.json` と `--set` に `branch` が足されない | `per_unit` の無い定義 | `UnitsStopped` の結果 |
| `plan_argv` | 記録が無ければ `--from <start>` | 止まった回は止まったステップから | — |
| `clear_values` | 単位の `values.json` を消す | `units/` が無い | — |
| `issue_values` | 指定した課題とリポジトリ | 別のリポジトリの同じ番号のコピー、題のコピーが無い中断 | 権限なし |

## 工程の定義と設定のスキーマ

満たす決定: 決定 4・13、受け入れ条件 1・2

関数のシグネチャは変えない。`StageDef` に `per_unit: bool = False` を足し、`definition._stage_def` が `metadata.ndf.per_unit` から読む（`per_issue` と同じ形）。`_stage_dict` と `_stage_from` は欄をそのまま写すので変えない。欄の無い保存済みのスナップショットは偽になる。

| ファイル | 足すもの |
| --- | --- |
| `contracts/workflow/stages/implement.json` | `"per_unit": true`、`"optional_inputs": ["branch"]` |
| `contracts/workflow/ndf.schema.json` | 工程の `per_unit`（`{"const": true}`） |
| `contracts/schemas/internal/stage-snapshot.schema.json` | `stage` の `per_unit`（`boolean`。必須にしない） |
| `contracts/schemas/project-workflow.schema.json` | `unit_required_checks`（空でない文字列の配列。空の配列も可） |

## テンプレートとレビューの起動

満たす決定: 決定 6・11・13・14、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `unit_template` | `core/route/__init__.py` | 新設 | `(t: Template, repo: str, drop: Collection[str]) -> Template` | `use` を開き、`drop` のステップを外し、残るステップ `ci` の引数の末尾に `--repo <repo>` を足したテンプレートを返す |
| `unit_review_launch` | 同上 | 新設 | `(repo: str, command: Sequence[str]) -> list[str]` | レビューの起動を、照合する包み（`UNIT_REVIEW`）で包む |
| `review_launch` | 同上 | 変更 | 変えない | `impl` では、PR の番号をブランチから引かず `{state_dir}/pr-number` から読む包みにする |

`drop` は `done-check` で、待つチェックが空の並びなら `ci` も入れる。単位の経路の `{{review}}` は `unit_review_launch(repo, 設定の起動か、--pr を持たない既定の ndf cross-review run)` である。

`UNIT_REVIEW` は `sh -c` で打つ。`$0` はリポジトリ、`$1` は `{state_dir}`、残りがレビューの起動と、ステップが後ろに足す引数である。

1. `pr-number` を読む。無いか空なら終了コード 2
2. `reviewed-pr` を消す
3. 起動に `--repo`・`--pr`・`--stop-on-hold`・`--reviewed-pr-out` を足して打つ
4. 終了コードが 0 か 6（`incomplete`）なら `reviewed-pr` を `<repo>#<番号>` と照合する。違うか無ければ、`--reviewed-out` のファイルを消し、`precondition` の結果 JSON を出して終了コード 3
5. ほかの終了コードなら `--reviewed-out` のファイルを消し、起動の終了コードのまま終える（起動の理由を残す）

テンプレートの変更は次のとおりである。ステップの並びは変えない。

| ファイル・ステップ | 変える中身 |
| --- | --- |
| `parts/steps.json` の `pr` | `run` は番号を `gh pr create` の出力の URL の末尾から取り、`{state_dir}/pr-number` へ書く。`probe` は head と `{{base}}` で絞り、`isCrossRepository` の PR を除き、見つけた番号を同じファイルへ書く |
| `impl.json` の `conflict-check`・`ci` | 共有のステップの `run` を上書きし、番号を `{state_dir}/pr-number` から読む。読めなければ `conflict-check` は終了コード 2、`ci` は 1 |
| `impl.json` のプランの最上位 | `"unit_file": "{{unit_file}}"` のキーを足す（`core/plan/model.parse` は知らないキーを読まない。差し込みを使わないとテンプレートの検査が落ちる） |
| `params.json` | `unit_file`（既定は空文字列） |
| `pr.json` の `pr` の `probe` | `isCrossRepository` の PR を除く |

### 異常系の処理

| 関数 | 失敗 | 扱い |
| --- | --- | --- |
| `unit_template` | 外すステップがテンプレートに無い | 何もしない（`without_steps` の今の振る舞い） |
| `unit_review_launch` の包み | 番号が読めない・照合が違う | 上の手順 1・4 |
| `review_launch` の包み（`impl`） | `pr-number` が無い | 終了コード 1。`pr` のステップを通る回から打ち直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `unit_template` | `expanded`・`without_steps` | `core/route/__init__.py` | 使う |
| `unit_review_launch` | `REVIEW_WITH_PR`・`REVIEW_STATE` | 同上 | 同じ「`sh -c` の包み」の形にする。照合は持たないので足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `unit_template` | `done-check` が無く、`ci` に `--repo` | `ci` を外すと、`ci` を指すルートが残らず `ci.required_checks` なしで組める | — |
| `unit_review_launch` | 照合が合えば起動の終了コード | 後ろに足した `--base`・`--state-dir` が通る | 番号かリポジトリの違い・ファイルなし |
| `review_launch` | `impl` は `pr-number` を使う | ほかのテンプレートは今のまま | — |
| `pr` のステップ | 作成と検索で同じファイル | 別の宛先とフォークの PR を拾わない | URL の末尾が番号でない |

## worker の作業の定義とレビューの指示

満たす決定: 決定 8、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `kind_values` | `contracts/worker_kinds.py` | 変更 | 変えない | 作業の定義の `optional_inputs` のうち、空でない値も返す（欠けの検査には数えない） |
| `unit_block` | `domains/crossreview/criteria_decide.py` | 新設 | `(role: str, unit: str) -> list[str]` | 単位の範囲と例外の指示の行。`unit` が空なら空 |
| `_unit_text` | `domains/crossreview/cli.py` | 新設 | `() -> str` | `NDF_UNIT_FILE` のファイルの中身。変数が無ければ空 |

`RunConfig`（`settings.py`）に `unit: str = ""` を足し、`_run_config` が `_unit_text()` を入れる。`drive._review_prompt` と `fixer._fix_prompt` は `unit_block` の行を足す。`unit_block` の相手役向けの文には、後の単位の条件を未達にしないことと、秘密・認証認可・利用者のデータ・戻せない操作の指摘は必ず出すことを書く。修正担当向けの文には、後の単位を理由に見送るときは区分 `not_applicable` と担当の単位を書くことと、同じ例外は見送らないことを書く。

`contracts/worker/kinds.json` は、`impl`・`fix`・`ci-fix`・`fix-push`・`fix-base` に `"optional_inputs": ["unit_file"]` を足し、`summary` に「入力 `unit_file` があれば、そのファイルを読んで単位の範囲に従う」を足す。起動指示は入力の値をデータとして扱うので、ファイルに従うことは作業の定義の側に書く必要がある。

`contracts/domains/env.json` に `NDF_UNIT_FILE`（`set_by` は `ndf`）の行を足し、`docs/contracts/env.md` を生成し直す。

### 異常系の処理

| 関数 | 失敗 | 扱い |
| --- | --- | --- |
| `_unit_text` | 変数のファイルが読めない | `ValueError` を上げ、`_or_stop` が `invalid` にする（範囲なしでレビューしない） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `unit_block` | `reviewer_block`・`fixer_block`・`document_block` | `criteria_decide.py` | `document_block` と同じ形で足す。文は `contracts/data/review-criteria.json` に置く |
| `_unit_text` | `settings.read_settings` | `settings.py` | 使えない（`.ndf/review.json` 専用） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `kind_values` | `unit_file` を返す | 空文字列と未指定は返さず、欠けにしない | — |
| `unit_block` | 役ごとの行に例外が入る | 空なら行を足さない | — |
| `_unit_text` | ファイルの中身 | 変数なし | 読めないパス |

## レビューの終わり方

満たす決定: 決定 14、受け入れ条件 4

引数の意味は設計書の「引数と工程の結果」にある。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `finish` | `domains/crossreview/finish_decide.py` | 変更 | `(review, handed=(), record_failed=None, *, stop_on_hold: bool = False) -> Verdict` | 真なら、人の判断を待つ指摘を `held_finding` の欠けにする |
| `_write_reviewed_pr` | `domains/crossreview/cli.py` | 新設 | `(path: Path, repo: str, pr: int) -> None` | `<repo>#<pr>` を書く |

- `Finding` に `defer: Optional[str] = None` を最後の欄として足す。`journal.findings` は行の `REVIEW_DEFER` を渡し、`as_findings` は `defer` を読む
- `cmd_run` は `--repo` があれば、`HUB` の第 1 引数に渡し、`GH_REPO` を環境に置く（相手役と修正担当の `gh` も同じリポジトリになる）
- `--reviewed-pr-out` のファイルは始めに消し、`--reviewed-out` を書くときだけ書く。番号は状態ファイルの `current_pr` である

### 処理の手順

人の判断を待つ指摘は、次のどちらかである。区分の判定は `mvv_decide.defer_holds` を使う。

| 指摘 | 条件 |
| --- | --- |
| レッドライン（`redline` が真） | `outcome` が `fixed` でない |
| ほか | `outcome` が `deferred` で、`defer_holds(defer)` が真 |

| `stop_on_hold` を除いた判定 | 当てはまる指摘 | 結果 |
| --- | --- | --- |
| `ok` | あり | `incomplete`。欠けは指摘ごとに 1 件 |
| `incomplete` | あり | 同じ名前の欠けが無い指摘を足す |
| ほかの停止 | — | 変えない |
| どれでも | なし | 変えない |

`record_failed` による置き換えは、この後に当てる。

### 異常系の処理

| 関数 | 失敗 | 扱い |
| --- | --- | --- |
| `_write_reviewed_pr` | 書けない | `precondition` で止める |
| `cmd_run` | `--repo` の形が違う | `repo.resolve_target_repo` の `RepoUnresolved` を `invalid` にする |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_write_reviewed_pr` | `--reviewed-out` の書き込み | `cli.cmd_run` | 同じ所で書く。`cmd_run` を 60 行以下に保つため関数に分ける |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `finish` | 区分 `filed`・`not_applicable` だけなら `ok` | 区分が無い・知らない値、直したレッドライン | 引数なしは今のまま |
| `_write_reviewed_pr`・`cmd_run` | 保存済みの状態より `--repo` を優先する | 前の起動のファイルを消す | 形の違う `--repo` |

## 受け入れ条件の確かめ

満たす決定: 決定 9、受け入れ条件 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_check` | `domains/loop/acceptance.py` | 変更 | 変えない | `--no-criteria` なら本文を読まずに `ok` |
| `add_parsers` | 同上 | 変更 | 変えない | `--criteria` と `--no-criteria` を排他にする |

本文の一括読み取りの選択（`BODY_FIELDS`）は、`units_invoke` も使うので `adapters/github.py` へ移す。

### 異常系の処理

| 関数 | 失敗 | 扱い |
| --- | --- | --- |
| `add_parsers` | 両方を渡す | argparse の誤りで止まる |

### 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `_check` | 記録の無い条件があっても `ok` | `--criteria` との同時指定 |

## PR の読み取りとマージ

満たす決定: 決定 6・11、受け入れ条件 4・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `merged_pulls` | `adapters/github.py` | 新設 | `(self, heads: Iterable[str], base: str) -> dict[str, int]` | head ごとの、宛先が `base` でフォークでないマージ済み PR の番号。無い head はキーを持たない |
| `merge` | 同上 | 変更 | `(..., jump: bool = False, expect_base: Optional[str] = None) -> MergeOutcome` | 読み直した PR の宛先が違えば要求を送らない |
| `_merge_with`・`_preflight` | 同上 | 変更 | `expect_base` を足す | 宛先の確かめは、マージ済みの確かめより先にする |
| `Pull.from_rest` | 同上 | 変更 | 変えない | head と base のリポジトリが違うか、head のリポジトリが null なら `cross_repository` を真にする。キーが無ければ偽 |
| `_target` | `ci_cli.py` | 新設 | `(hub: GitHub, args: Namespace) -> Pull \| dict` | `--pr`・`--base` からマージの対象を決める。決められなければ停止の結果を返す |
| `_merge` | 同上 | 変更 | 変えない | `_target` の PR を、`expect_base=args.base` でマージする |

`github.py` は 964 行ある。足した後に 1,000 行を超える場合は、PR の読み取りを別のファイルへ分ける。

### 処理の手順

| `--pr` | `--base` | 対象 |
| --- | --- | --- |
| 無し | 無し | 今のまま（`pulls_by_head` の開いた PR の先頭） |
| 無し | あり | `open_pulls_of_head` のうち宛先が `--base` の PR |
| あり | 任意 | その番号の PR。head・宛先（`--base` があれば）・リポジトリ・状態を確かめる |

### 異常系の処理

| 関数 | 失敗 | 扱い（停止理由の種類） |
| --- | --- | --- |
| `_target` | 対象の開いた PR が無い | `precondition` |
| `_target` | head・宛先の違い、フォーク、マージされず閉じた | `refused`。要求を送らない |
| `merge` | 読み直した宛先が違う | `refused`。直接のマージもキューへの投入も送らない |
| `merged_pulls` | GitHub の失敗 | `GitHubError` を呼び出し元へ返す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `merged_pulls` | `pulls_by_head`・`open_pulls_of_head`・`aliased` | `adapters/github.py` | `aliased` と `Pull.from_graphql` を使い、`baseRefName`・`states: MERGED` で絞る |
| `_target` | `open_pulls_of_head`・`hub.pull` | 同上 | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `merged_pulls` | 宛先の合う PR | head が 0 件 | 別の宛先・フォークを除く |
| `merge` | 宛先が合えば今のまま | `expect_base` なし | 読み直しで変わった宛先（直接・キュー） |
| `Pull.from_rest` | 同じリポジトリ | head のリポジトリが null | — |
| `_target`・`_merge` | 番号の PR へ要求する | 引数なし | 異常系の表の各行 |

## コマンドの一覧と手順書

満たす決定: 決定 4・7、受け入れ条件 1

- `ndf check --write-spec` で `docs/contracts/commands.md`・`docs/contracts/env.md`・`contracts/schemas/io/` を生成し直す
- `skills/development-workflow/references/agent-layers.md` の「実装のフェーズの単位」に、`ndf progress next` の自動の経路も単位を読むことと、単位の表の読み方を書く
