# #4566 の詳細設計: CI テストの設定が無いリポジトリでもリファクタリングを通す

[設計文書](./issue-4566-design.md)の「構成要素」を、ファイルと関数の単位まで下ろした文書である。受け入れ条件の番号は[要求仕様](./issue-4566-requirements.md)を指す。パスは、特に書かなければ `plugins/ndf/` からの相対である。

`--work` は `ndf refactor run` の今の引数で、対象のリポジトリの中の位置（呼び出し元の作業ツリー。既定は `.`）を渡す。担当の AI が書く作業場所（`rf<PR>/work`）とは別のものである。

**この文書は、Issue #4566（ https://github.com/devbasex/ai-plugins/issues/4566 ）の実装が満たす仕様を定める。** 既存のコードを変える所は、表の「種類」の列の「変更」と、各セクションの説明で示す。

## 地図

`*` は新設である。

```text
plugins/ndf/
  src/ndf/
    testing/means.py *
    progress/{command.py, refactor_values.py, invoke.py}
    core/route/__init__.py
    core/converge/worktree.py
    testing/weight.py
    domains/crossrefactor/{cli.py, drive.py, workspace.py, final_gate.py, final_gate_decide.py,
                           local_gate.py *, protected_decide.py *, prompt_decide.py}
  contracts/{workflow/, templates/, schemas/, limits/, version.json}
  docs/contracts/commands.md
  skills/{development-workflow, cross-refactoring}/
docs/specifications/
```

依存は一方向である。`progress`・`domains/crossrefactor` が `testing` を読み、`testing/means.py` は `testing` の中の純粋な関数だけを読む。`core/route` は手段の判定を読まず、起動の引数に `--local-test` があるかだけを見る。`protected_decide.py` はファイルもプロセスも触らず、git を読むのは `local_gate.py` である。

| パス | 種類 | 役割 | 依存する先 | クラスまたはモジュール | 新設（＋）と変更（△）の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/testing/means.py` | 新設 | テストを確かめる手段を設定から決める | `testing.weight`・`testing.scoped` | `means` | ＋`decide`・`total_minutes` |
| `contracts/workflow/stages/refactor.json` | 変更 | 条件の語を `tests_verifiable` にする | — | — | — |
| `contracts/workflow/ndf.schema.json` | 変更 | 条件の語の一覧に `tests_verifiable` を足す | — | — | — |
| `src/ndf/progress/command.py` | 変更 | 条件の語の値を求める | `testing.means` | `command` | △`facts_of` |
| `src/ndf/progress/refactor_values.py` | 変更 | 既定の起動を手段ごとに組む | `testing.means`・`core.route` | `refactor_values` | △`_default_launch` |
| `src/ndf/core/route/__init__.py` | 変更 | 起動の引数と、経路ごとのプランの形 | — | `route` | △`refactor_launch`・`render`、＋`_by_refactor_gate`・`RouteUnsupported` |
| `src/ndf/progress/invoke.py` | 変更 | 対象外の組み合わせを `precondition` にする | `core.route` | `invoke` | △`plan_failure` |
| `contracts/templates/cross-refactoring.json`・`contracts/templates/parts/steps.json` | 変更 | `follow` のステップを足す | — | — | — |
| `src/ndf/domains/crossrefactor/cli.py` | 変更 | 引数と手段の組み合わせを確かめる | `testing.means` | `cli` | △`run_config`・`_drive_parsers`・`_release`・`main`、＋`_check_gate` |
| `src/ndf/domains/crossrefactor/drive.py` | 変更 | 経路の分岐、排他、再利用、SHA の書き出し | `local_gate`・`workspace` | `drive` | △`RunConfig`・`drive`・`_reused`・`_init`・`_final`・`_summary`、＋`_write_pushed` |
| `src/ndf/domains/crossrefactor/workspace.py` | 変更 | PR ごとの排他ロック、権限を絞ったログの書き込みと期限での削除 | `ndf.slot_pool`・`ndf.contracts.limits` | `workspace` | ＋`hold`・`write_private_log`・`sweep_gate_logs` |
| `src/ndf/domains/crossrefactor/local_gate.py` | 新設 | 手元の最終ゲート | `final_gate`・`protected_decide`・`testing.slots`・`core.converge.worktree` | `local_gate` | ＋`settings_at`・`protection_at`・`run_local_gate`・`run_suites`・`redact_output`・`suite_label`・`fix_excerpt`・`verify_tree`・`sweep_verify_trees` |
| `src/ndf/core/converge/worktree.py` | 変更 | 取得済みの先頭を受け取る同期を分け、`sync_worktree` もそれを呼ぶ | — | `worktree` | △`sync_worktree`、＋`sync_fetched`・`refuse_dirty` |
| `src/ndf/testing/weight.py` | 変更 | リダイレクト先も含む語の並びを返す関数を足す | — | `weight` | ＋`all_words` |
| `src/ndf/domains/crossrefactor/protected_decide.py` | 新設 | テストの定義のファイルの判定 | `testing.scoped`・`testing.weight` | `protected_decide` | ＋`protected_of`・`narrow`・`inside_work` |
| `src/ndf/domains/crossrefactor/final_gate.py` | 変更 | 直しが対象範囲の外を変えていないかを調べる関数を、2 つの経路から呼べるようにする。CI の記録にチェックの名前を載せる | `final_gate_decide` | `final_gate` | △`check_fix`（旧 `_check_fix`）・`fix_or_undo`（旧 `_fix_or_undo`）・`run_gate` |
| `src/ndf/domains/crossrefactor/final_gate_decide.py` | 変更 | 直しの範囲の判定に、除くファイルを足す | `testing.scoped` | `final_gate_decide` | △`verify_final_fix` |
| `src/ndf/domains/crossrefactor/prompt_decide.py` | 変更 | 手元で落ちたときの指示文 | — | `prompt_decide` | △`final_fix` |
| `contracts/limits/test-run.test.json` | 変更 | `read_by` に `local_gate` を足す | — | — | — |
| `contracts/limits/cross-refactoring.gate-log.keep.json` | 新設 | 手元の最終ゲートのログを残す秒（2592000 秒 = 30 日） | — | — | — |
| `contracts/schemas/ndf-settings/project.schema.json`・`skills/development-workflow/schemas/project.schema.json` | 変更 | 設定のキー `test.protected_files` と `test.local_total_minutes` を足す。2 つ目のファイルは、1 つ目と 1 バイトも違わないコピーにする | — | — | — |
| `contracts/schemas/io/refactor.args.schema.json`・`contracts/version.json`・`docs/contracts/commands.md` | 変更 | コマンドのコントラクト（生成して書き直す） | — | — | — |
| `skills/development-workflow/SKILL.md`・`skills/cross-refactoring/SKILL.md` | 変更 | 手順書 | — | — | — |
| `docs/specifications/` の 3 本（`ndf-ci-only-tests.md`・`ndf-review-units-and-refactoring-timing.md`・`ndf-stage-plan-work-prompts.md`） | 変更 | 確定仕様 | — | — | — |
| `tests/testing/test_means.py`・`tests/domains/crossrefactor/{test_local_gate.py, test_protected.py}` | 新設 | 新しい部品のテスト | — | — | — |
| `tests/progress/{test_model.py, test_run.py, test_invoke_refactor.py, test_sprint_templates.py}`・`tests/route/test_templates.py`・`tests/domains/crossrefactor/{test_drive.py, test_refactor_cli.py, test_prompt_decide.py}` | 変更 | 今あるテスト | — | — | — |

## テストを確かめる手段の判定

満たす決定: 決定 1・2・3、受け入れ条件 3・4・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `decide` | `src/ndf/testing/means.py` | 新設 | `decide(project: Mapping[str, Any]) -> Means` | 設定の中身から手段と、手元で打つ内容を返す |
| `total_minutes` | 同上 | 新設 | `total_minutes(project: Mapping[str, Any]) -> float \| None` | `test.local_total_minutes` が正の数なら返す |

- `decide`
  - 事後条件: 判定の順は、設計文書の「テストを確かめる手段」の表のとおり。副作用は無く、例外を上げない。形の違う値は「無い」と読む

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Kind` | 型（`required_checks`・`local_test`・`none`） | `means.py` | — |
| `Means` | 型。`kind`、`required`（チェックの名前）、`suites`（名前とコマンドの組）、`declarations`（テストの suite の宣言そのもの）、`commands`（保護の判定に使う `command` と `scope_command`）、`digest`（テストの設定の識別値）、`protected`（`test.protected_files`） | `means.py` | — |

`digest` は、テストの suite ごとの `name`・`command`・`scope_command`・`paths` を設定の順に並べた JSON の SHA-256 である。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `decide` | `weight.is_test_suite`・`scoped.ci_only`・`declared.test_section` | `testing/` の 3 ファイルを読んだ | 前の 2 つを使う。`test_section` はファイルを読むので呼ばない |
| `decide` | `invoke._test_command`（コマンドを ` && ` でつなぐ） | `progress/invoke.py` を読んだ | 使わない。suite ごとに打って上限を掛けるので、つないだ 1 本では足りない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `decide` | 手段の表の 4 行 | `kind` を省いた suite だけ、`command` の無い suite だけ、`required_checks` が空の並び | プロジェクトの解析が値を決められず `test` に `{"unknown": "<理由>"}` を書いた設定、`suites` が並びでない |
| `total_minutes` | 正の数 | キーが無い | 0・負・文字列は None |

## 工程の定義と条件の語

満たす決定: 決定 1・7、受け入れ条件 1・2・5・7・8・9・10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `facts_of` | `src/ndf/progress/command.py` | 変更 | 今のまま | 語 `tests_verifiable` の値を `means.decide(…).kind != none` で返す。`ci_declared` はそのまま残す |

`refactor.json` の `when` は `["code_in_diff", "tests_verifiable"]` にする。順は今と同じで、文書だけの差分は先の語で飛ぶ。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `facts_of` | 手段の表の 4 行すべてで、値が真なら起動の引数が組め、偽なら組めない | 古い定義の語 `ci_declared` が今と同じ値 | 設定を読めないときは今と同じ失敗 |

## 起動の組み立て

満たす決定: 決定 1・3、受け入れ条件 1・2・3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_default_launch` | `src/ndf/progress/refactor_values.py` | 変更 | 今のまま | 手段が必須のチェックなら `["--required", *名前]`、手元のテストなら `["--local-test"]` を渡す。無しなら None |
| `refactor_launch` | `src/ndf/core/route/__init__.py` | 変更 | `refactor_launch(branch: str, base_ref: str, gate: Sequence[str]) -> list[str]` | 第 3 引数を、必須のチェックの名前の並びから、合否の確かめ先の引数の並びに変える |

これまで `_default_launch` は `ci.required_checks` を直接読んでいた。この PR により `means.decide` だけを読み、条件の語と同じ答えになる（不変条件 I1）。CI テストの設定があり必須のチェックが無ければ None を返す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_default_launch` | 2 つの手段の起動の引数 | `workflow.launch.refactor` があれば手段を見ない | 手段が無ければ `_default_launch` は None を返し、起動の引数が無いものとして扱われる |

## `ndf refactor` の引数

満たす決定: 決定 3、受け入れ条件 3・4・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_drive_parsers` | `src/ndf/domains/crossrefactor/cli.py` | 変更 | 今のまま | `--required` の必須を外し（`default=[]`）、`--local-test`（値なし）と `--pushed-out` を足す |
| `_check_gate` | 同上 | 新設 | `_check_gate(a: argparse.Namespace) -> None` | 引数と手段の組み合わせを確かめ、合わなければ `ValueError` |
| `run_config` | 同上 | 変更 | 今のまま | 先頭で `_check_gate` を呼び、`local_test` と `pushed_out` を渡す |
| `_release` | 同上 | 変更 | 今のまま | 排他ロックを取り、記録に残る検証用の作業場所も片付け、`sweep_gate_logs` を呼ぶ |
| `main` | 同上 | 変更 | 今のまま | `final_gate.GateStopped` の扱いは今のまま。ロックを取れないときの停止を足す |

argparse の「どちらか 1 つ必須」は使わない。失敗が結果 JSON を出さずに終わるためである。`_check_gate` の `ValueError` は、今の表（`STOP_KINDS`）で `invalid` になる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_check_gate` | `--required` と `--local-test` が両方無いか両方ある、`--local-test` と `--ci-test` の併用、設定の手段が手元のテストでないのに `--local-test` | 呼び方の誤り | 担当の起動と取得の前 | 止める | `invalid` の結果 JSON | 何も作らない。引数を直して打ち直す |
| `_check_gate` | `--local-test` で設定を読めない | 設定の誤り | 同上 | 止める | `precondition` | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_check_gate` | `--required` は設定の手段に関わらず通る。手段が手元のテストなら `--local-test` が通る | `--pushed-out` を CI の経路で渡しても無視する | 異常系の行ごと。担当を起動しないことも見る |

## 手元の最終ゲート

満たす決定: 決定 4・5・6・9・10、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `protected_of` | `protected_decide.py` | 新設 | `protected_of(tree: Mapping[str, str \| None], commands: Sequence[str], declared: Sequence[str], search: Sequence[str] = ()) -> Protected` | ツリー（パス → リンク先）から、保護するファイルの判定を組む。`declared` には設定 `test.protected_files` の指定を渡す |
| `inside_work` | `protected_decide.py` | 新設 | `inside_work(commands: Sequence[str], roots: Sequence[str]) -> tuple[str, ...]` | `roots` の配下を指す絶対パスの語を返す |
| `narrow` | `protected_decide.py` | 新設 | `narrow(scope: Sequence[str], tracked: Sequence[str], protected: Protected) -> tuple[str, ...]` | 保護しないファイルを 1 つも含まない指定を対象範囲から外す |
| `verify_final_fix` | `final_gate_decide.py` | 変更 | `verify_final_fix(changed_by_commit, target_scope, excluded: Callable[[str], bool] \| None = None)` | `excluded` が真のファイルは、範囲の中でも範囲の外として扱う |
| `check_fix` | `final_gate.py` | 変更 | `check_fix(g, before: str, failed: Sequence[str], scope: Sequence[str], excluded=None) -> None` | 直しが範囲の外を変えていれば `before` へ戻して `refused`。2 つの経路で使う |
| `fix_or_undo` | `final_gate.py` | 変更 | 今のまま（名前の `_` を外す） | 担当が結果を出さなければ戻す |
| `run_gate` | `final_gate.py` | 変更 | 今のまま | 合格の記録に、手段と通ったチェックの名前を足す |
| `settings_at` | `local_gate.py` | 新設 | `settings_at(g: git.Git, commit: str, repo_work: Path) -> dict[str, Any]` | そのコミットのルートの `.ndf/project.json` を読む。コミットに無ければ `--work` の設定 |
| `protection_at` | `local_gate.py` | 新設 | `protection_at(g: git.Git, commit: str, project: Mapping[str, Any], repo_work: Path) -> Protected` | コマンドが `--work` の配下を絶対パスで指せば停止する（`inside_work`）。`git ls-tree -r -z` と、リンクの中身を読んで `protected_of` を呼ぶ |
| `verify_tree` | `local_gate.py` | 新設 | `verify_tree(repo, root: Path, pr: int, commit: str, state, save)`（コンテキストマネージャ。`Path` を渡す） | 検証用の作業場所を作り、打ち終えたら取り除く |
| `sweep_verify_trees` | `local_gate.py` | 新設 | `sweep_verify_trees(repo, state, save) -> list[str]` | 中断で残った作業場所を取り除き、残した理由を返す |
| `run_suites` | `local_gate.py` | 新設 | `run_suites(tree: Path, means: Means, budget: TestBudget, logs: Path, label: str) -> tuple[Failed, ...]` | テストの suite の `command` を順に打ち、落ちた suite を終了コードとともに返す。プロセスを起動できなければ停止する |
| `run_local_gate` | `local_gate.py` | 新設 | `run_local_gate(g, cfg: LocalGateConfig, state, save, fix: FixLauncher) -> dict[str, Any]` | 取り込み → テスト → push か直し、を周の上限まで回し、最終ゲートの記録を返す |
| `sync_fetched` | `core/converge/worktree.py` | 新設 | `sync_fetched(g: git.Git, tip: str, base: str = "") -> SyncResult` | 取得済みの先頭 `tip` へ合わせ、送っていないコミットを載せ直す。取得はしない。今の `sync_worktree` の取得より後の処理を移したもの |
| `refuse_dirty` | 同上 | 新設 | `refuse_dirty(g: git.Git) -> None` | 追跡ファイルに commit していない変更があれば `SyncRefused` |
| `sync_worktree` | 同上 | 変更 | 今のまま | `refuse_dirty` → `fetch_tips` → `sync_fetched`。呼び出し元（cross-review と CI の経路）の振る舞いは変えない |
| `write_private_log` | `workspace.py` | 新設 | `write_private_log(logs: Path, name: str, text: str) -> Path` | 実行ユーザーだけが読めるログを書く |
| `sweep_gate_logs` | `workspace.py` | 新設 | `sweep_gate_logs(base: Path, repo_id: str, own: Path, keep: float, now: float) -> int` | 同じリポジトリの NDF が書いたと記録にある、期限を過ぎた手元の最終ゲートのログを消し、件数を返す |
| `redact_output` | `local_gate.py` | 新設 | `redact_output(text: str) -> str` | コマンド、出力、suite の名前の認証情報を伏せる |
| `suite_label` | `local_gate.py` | 新設 | `suite_label(index: int, name: str) -> str` | NDF の外や記録へ出す suite の呼び名を作る |
| `fix_excerpt` | `local_gate.py` | 新設 | `fix_excerpt(failed: Sequence[Failed]) -> list[str]` | 指示文に載せる行を、伏せ字にして上限で切って作る |
| `hold` | `workspace.py` | 新設 | `hold(root: Path) -> FileLock \| None` | `rf<PR>/run.lock` を待たずに取る。取れなければ None |
| `RunConfig` | `drive.py` | 変更 | `local_test: bool = False`・`pushed_out: Path \| None = None` を足す | `args()` は、手元の経路では `--required …`・`--ci-test …` の代わりに `--local-test` を並べる。`pushed_out` は並べない |
| `drive` | `drive.py` | 変更 | 今のまま | 始めに `hold`、終わりに解く。ロックを取った後に `sweep_gate_logs` を呼ぶ。手元の経路で対象範囲が空なら、次を行わずに最終ゲートへ進む: 担当の AI を起動できるかの確認、プロジェクトの判断基準の文書（MVV）の読み込み、改善の提案、項目ごとの実装と検証 |
| `_reused` | `drive.py` | 変更 | 今のまま | 手元の経路でテストを打って完了した実行は、今の PR の先頭の設定から求めた識別値と今の `PATH` の SHA-256 が、どちらも記録の値と同じときだけ、前の結果を再利用する。合格の記録を引くときと同じ組で判定する |
| `_init` | `drive.py` | 変更 | 今のまま | 手元の経路では、保護するファイルを、対象範囲と、実装が変えた関数とファイルの一覧（状態ファイルの `touched`。提案はこの中から出る）の両方から除く |
| `_final` | `drive.py` | 変更 | 今のまま | 経路で `run_gate` か `run_local_gate` を呼ぶ |
| `_summary` | `drive.py` | 変更 | 今のまま | 「通ったチェックの名前」「手元のテスト全体が通った」「変更が無いのでテストは打っていない」を書き分ける |
| `_write_pushed` | `drive.py` | 新設 | `_write_pushed(cfg: RunConfig, head: str) -> None` | `--pushed-out` があれば SHA を 1 行で書く。再利用した結果でも書く。既存のファイルとリンクの扱いは下の箇条のとおり |

- `_write_pushed`
  - 事前条件: 指定のパスが無いか、通常のファイルで中身が SHA 1 行（40 か 64 桁の 16 進と改行だけ）である。親のディレクトリが在る
  - 事後条件: 指定のパスがシンボリックリンク、ディレクトリ、ほかの中身の通常のファイルなら、書かずに `precondition` で停止する。リンク先も既存のファイルの中身も変えない
  - 手順: 同じディレクトリに `tempfile.mkstemp` で一時ファイルを作って SHA を書き、`os.replace` で指定の名前へ置き換える。置き換えるのは名前だけなので、同じファイルを指すハードリンクの中身は変わらない
  - 理由: `ndf refactor run` は人も直接打てるので、出力先に利用者のファイルを誤って指定されても、その中身を SHA で置き換えない。工程のプランが渡す `{state_dir}/pushed-sha` は、この条件を常に満たす
- `settings_at`
  - 副作用: git の読み取りだけ。ツリーに在るかは `git ls-tree` で見る。無いファイルでは終了コード 0 で何も出さないことを 2026-11-26 に確かめた
- `verify_tree`
  - 事後条件: 作る前にパスを状態の `verify_trees` へ保存し、取り除いた後に消す。名前は `rf<PR>/verify-<ランダム>` で、実行のたびに違う
  - 副作用: `git worktree add --detach`、`worktree.lock_review_worktree`、取り除きは `git worktree unlock` と `git worktree remove --force`
- `write_private_log`
  - 事後条件: `logs/` は 0700、ログファイルは 0600。umask に頼らない。ディレクトリは `mkdir(mode=0o700, exist_ok=True)` の後に `chmod` する
  - 事後条件: 前からあるファイルは開かず、書き換えない。同じ名前がハードリンクやシンボリックリンクでも、リンク先の中身と権限は変わらない
  - 手順: `tempfile.mkstemp(dir=logs, prefix="local-gate-", suffix=".tmp")` で新しいファイルを作り、`fchmod` で 0600 にしてから内容を書き、`os.replace` でログの名前へ置き換える。失敗したら一時ファイルを消す
  - 異常: `logs/` がシンボリックリンクか、所有者が実行ユーザーでない、または権限を直せなければ、書かずに停止する
  - `run_suites` は、suite の出力をこの関数でだけ書く。渡すのは `redact_output` を掛けた文字列だけである。伏せる前のコマンドと出力はメモリの中だけで持ち、ファイルに書かない。担当の出力のログも同じ `logs/` に入るので、`workspace` の作成と再開で `logs/` を 0700 にする
- `sweep_gate_logs`
  - シグネチャ: `sweep_gate_logs(base: Path, repo_id: str, own: Path, keep: float, now: float) -> int`。`repo_id` は自分のリポジトリの識別値（`git.Git.common_dir()` の実パス）、`own` は自分の `rf<PR>/` である
  - 事前条件: `own` の排他ロックを持っている
  - 事後条件: 消すのは、次をすべて満たすファイルだけである。`base` の直下の `rf<PR>/` の状態ファイルの `repo` が `repo_id` と一致する。ファイル名がその状態ファイルの `gate_logs` に記録されている。`rf<PR>/logs/` の直下にある、名前が `local-gate-` で始まる通常のファイルである。更新時刻が `keep` 秒より古い。消したファイル名は `gate_logs` から外して状態ファイルを書き直す
  - 事後条件: `own` 以外の `rf<PR>/` は、その PR のロック（`hold`）を待たずに取れたときだけ扱い、扱い終えたら解く。取れなければ飛ばす。状態ファイルが無い・読めない・`repo` が無いか違う `rf<PR>/` には触れない。`rf<PR>/` か `logs/` がシンボリックリンクなら入らない。消せないファイルは残して続ける
  - 呼ぶ所: `drive` と `_release` が、自分の排他ロックを取った後に呼ぶ。`base` は作業場所の親ディレクトリ（`location.default_base`）で、`NDF_WORKTREE_BASE` を通じてほかのリポジトリと共有されうる。`keep` は `contracts/limits/cross-refactoring.gate-log.keep.json` の `seconds` である
  - 記録: `write_private_log` を呼ぶ側（`run_suites`）は、ログを書いた後にファイル名を状態ファイルの `gate_logs` に足す。`drive` は状態ファイルを作るときと再開のたびに `repo` を書く
- `redact_output`
  - 事後条件: 次の順に掛ける。秘密鍵のブロックを最初に原文で探すのは、手順 3 の関数が `-----BEGIN` を含む行を丸ごと置き換え、その後では開始の行を見つけられないためである
    1. 原文で、`-----BEGIN` と `PRIVATE KEY` を含む行から、`-----END` と `PRIVATE KEY` を含む行まで（無ければ末尾まで）を、まとめて伏せる。開始の行に終わりもあれば、その行だけを伏せる
    2. 全文に `ndf.adapters.run_log.redact` を掛ける
    3. 行ごとに `ndf.domains.project.secret.redact` を掛ける
- `suite_label`
  - 事後条件: `#<設定の順番> <名前>` を返す。名前に `redact_output` を掛けて何かが変わる場合は、名前を載せず `#<設定の順番>` だけを返す。名前は先頭の 100 文字（定数 `NAME_CHARS`）までにする
  - 結果 JSON、状態ファイル、PR のコメント、標準出力と標準エラー、停止の理由、指示文に suite を書くときは、この呼び名だけを使う
- `fix_excerpt`
  - 事後条件: 落ちた suite 1 件につき文字列 1 つを返す。中身は、`suite_label` の呼び名、終了コードか時間切れの別、コマンド、出力の末尾である。コマンドと出力の全文に `redact_output` を掛けてから、上限で切る。コマンドは先頭の 500 文字、出力は末尾の 100 行かつ末尾の 8,000 文字までにする。上限は定数 `COMMAND_CHARS`・`OUTPUT_LINES`・`OUTPUT_CHARS`（`local_gate.py`）に置く
- `sweep_verify_trees`
  - 事前条件: 排他ロックを持っている
  - 事後条件: 状態の `verify_trees` に記録があるパスだけを対象にする。git worktree lock の状態で扱いを分ける。lock が無いもの（作成の後、lock を掛ける前に中断した）と、理由の文字列が NDF の付けたものは取り除く。理由の文字列がほかのもの（利用者が掛けた lock）は残す。`worktree.lock_of` は lock が無ければ None を返す。git の作業場所として登録の無いディレクトリも残す
- `run_suites`
  - 事後条件: テストの後、作業場所の `HEAD` が対象のコミットのままで、追跡ファイルに変更が無い
  - 副作用: 設定のコマンドの起動（`slots.run_command`）。引数 `suites` には、対象の SHA の判定に使った設定の宣言（`means.declarations`）を渡す。重い処理かの判定が、検証用の作業場所の置き場所に左右されないようにする

### 処理の手順

`run_local_gate` の 1 周は次の順である。流れの図は設計文書の「処理の流れ」にある。

| 順 | 条件 | 結果 |
| --- | --- | --- |
| 1 | リモートの先頭を `worktree.fetch_tips` で取得する。取得した先頭が `HEAD` の祖先（同じ SHA を含む）なら載せ直さず、`HEAD` の SHA を保つ。祖先でなければ、取得した先頭を `worktree.sync_fetched` へ渡して載せ直す。以後の順は、ここで取得した先頭を「取り込んだ先頭」として使う。直前の周が順 7 で取得していれば、その先頭を使い、取得し直さない | 載せ直しが衝突したら `base_conflict` |
| 2 | 取り込んだ先頭の設定の手段が手元のテストでない | `precondition` |
| 3 | `HEAD` が取り込んだ先頭と同じ（送るコミットが無い） | テストを打たずに合格（`tests: skipped`） |
| 4 | 取り込んだ先頭から `HEAD` までの変更（`git diff --name-only --no-renames -z`）が保護するファイルに当たる | `refused` |
| 5 | テスト全体が要るかを調べる | `HEAD`・識別値・`PATH` の組が合格の記録に無ければテスト全体を打つ。記録にあれば打たずに順 6 へ進む |
| 6 | 合格 | `git push <リモート> <SHA>:refs/heads/<ブランチ>`。受け付ければ終了 |
| 7 | push の拒否で、取り直したリモートの先頭が変わっていて、周が残る | 次の周 |
| 8 | push の拒否で、ほか | `push_rejected` |
| 9 | 不合格の終了コードに 126・127 があり、取り込んだ先頭が合格の記録にも基準側の不合格の記録にも無い。周が残っていなくても、この行を適用する | 取り込んだ先頭（この実行のコミットを載せない状態）だけでテスト全体を打ち、結果を記録する。比べ方は順 9 の下の箇条のとおり。直しへ進む場合は順 10 へ |
| 10 | 不合格で、周が残らない | `test_failed` |
| 11 | 不合格（126・127 を含まない）で、周が残り、取り込んだ先頭が合格の記録にも基準側の不合格の記録にも無い | 順 9 と同じく、取り込んだ先頭だけでテスト全体を打って記録し、同じ比べ方をする。直しへ進む場合は順 12 へ |
| 12 | ほか | `fix_or_undo` で直し、`check_fix`（保護するファイルは範囲の外）で確かめて次の周 |

- 繰り返しの終わる条件: 周は `max_fix + 1` 回まで
- 順 9・11 の比べ方: 基準側（取り込んだ先頭）で落ちた suite の順番の集合を B、変更後（対象の SHA）で落ちた suite の順番の集合を H とする。記録があれば打たずにその記録を使う
  - H のうち 126・127 を返した suite が、B でも 126・127 を返す: `precondition`
  - H から B を引いた集合が空でない: 直しへ進む。`fix_excerpt` に渡すのはその差の suite だけである
  - H から B を引いた集合が空: `test_failed`（直さない）。理由に B と H の両方の呼び名と終了コードを載せ、「suite の単位では原因を見分けていない」と書く
- 基準側の結果は、合格なら `local_passed` に、不合格なら `local_base_failed` に組とともに残す

`protected_of` の判定は、パスが次のどれかに当たれば真である。保護するファイルは、設計文書の「テストの定義のファイル」にあるファイルと同じで、一覧はそのセクションにある。

- どこかの階層が `.ndf`
- 名前が、決まった名前の一覧に当たる（`fnmatch`）
- テストのコマンドを分けた単語をパスとして正規化し、そのパスか、その親のパス（ディレクトリがファイルやリンクに置き換わった場合）に当たる。ツリーに無いパスも当たる。パスがシンボリックリンクなら、リンクとリンク先の両方が当たる
- `search`（`PATH` の相対の要素。空と `.` はルート）のどれかの下の、`/` を含まない単語と同じ名前に当たる。`search` は `protection_at` が、テストを打つ環境の `PATH` から作る
- `test.protected_files` の指定の中（`scoped.in_scope`）

コマンドの語は `weight.split_commands` で分け、`=` を含む単語は、`=` より後ろの部分もパスとして照らす。正規化は `posixpath.normpath` で、ルートの外を指す結果（`..` で始まる）は捨てる。リンクはコミットのツリーの中だけでたどり、ツリーの外を指せばリンク自体だけを保護する。

テストのコマンドは書かれたとおりに打つので、`--work` のリポジトリのルート配下を指す絶対パスは、検証用の作業場所ではなく `--work` のコードを指す。`inside_work` はそのような語を返し、`protection_at` は 1 つでもあれば `precondition` で停止する。ルートは、`--work` に渡されたパスと、シンボリックリンクを解いたパスの両方で照らす。リポジトリの中のファイルは相対パスで指す。

`inside_work` が調べる語は `weight.all_words` で作る。`weight.split_commands` はリダイレクト先の語を捨てるので、`> <ルート>/report.txt` を見落とすためである。`all_words` は、同じ字句の分け方で、リダイレクト先を含むすべての語を返す。

この検査で分かるのは、コマンドの文字列にそのまま書かれた絶対パスだけである。次のものは検出しない。

- 変数、コマンド置換、`~` を展開して決まるパス
- 引用符の中に入れた別のシェルのコマンド（`sh -c "... > /path"`）
- テストのスクリプトやプログラムが内部で読み書きするパス

NDF はテストの実行を隔離しないので、これらによる検証用の作業場所の外への書き込みは防げない。テストの後の追跡ファイルの確認も、検証用の作業場所の中しか見ない。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `PROTECTED_NAMES` | 定数（決まった名前の一覧） | `protected_decide.py` | 設計文書の表のとおり |
| `Protected` | 型（`covers(path) -> bool`） | `protected_decide.py` | — |
| `Failed` | 型（落ちた suite の呼び名 `label`、終了コードか時間切れの別、コマンド、出力） | `local_gate.py` | コマンドと出力は伏せる前の値で、メモリの中だけで持つ |
| `LocalGateConfig` | 型（`pr`・`branch`・`scope`・`max_fix`・`repo_work`・`root`・`total_seconds`） | `local_gate.py` | `total_seconds` は `test.local_total_minutes` を秒に直した値。未設定なら安全網の上限（`budget_decide.SafetyNet` の `limit_minutes`）を秒に直した値 |
| `TestBudget` | 型（累計の上限、使った秒、suite 1 つの上限） | `local_gate.py` | suite の上限は、上限の設定ファイル `contracts/limits/test-run.test.json` の `seconds` |
| `local_passed` | 状態ファイルのキー（`{sha, digest, path_digest}` の並び。`path_digest` は `PATH` の値の SHA-256） | `state.json` | 空 |
| `verify_trees` | 状態ファイルのキー（パスの並び） | `state.json` | 空 |
| `local_base_failed` | 状態ファイルのキー（`{sha, digest, path_digest, failed}` の並び。`failed` は基準側で落ちた suite の順番と終了コード） | `state.json` | 空 |
| `repo` | 状態ファイルのキー（リポジトリの識別値。git の共通ディレクトリの実パス） | `state.json` | 無し |
| `gate_logs` | 状態ファイルのキー（NDF が書いた手元の最終ゲートのログのファイル名の並び） | `state.json` | 空 |
| `final` | 状態ファイルのキー。`status`・`rounds`・`head` に、`means` と、CI なら `checks`、手元なら `tests`（`passed`・`skipped`）と `digest`、落ちた場合は `base_failed` と `head_failed`（それぞれ suite の呼び名と終了コードの並び） | `state.json` | — |
| `done.digest`・`done.path_digest` | 状態ファイルのキー（テストを打って完了したときだけ。合格に使った識別値と `PATH` の SHA-256） | `state.json` | 無し |

`state.json` のキーの表は `store.py` の説明にあるので、同じ変更で書き足す。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ PR の `run` 同士、`run` と `release` | `rf<PR>/` の状態ファイルと作業場所 | `slot_pool.try_lock`（`filelock`）を待たずに取る | プロセスが終われば解ける。打ち直しは合格の記録と項目を引き継ぐ |
| 手元のテストと、同じマシンで並行して動くほかの作業の重いテスト | マシン | `slots.run_command` のテストのスロット | 待った秒は累計に足さない（`slots.waited_since`） |
| 手元のテストの間の、他者の push | PR のブランチ | 排他しない。push の拒否で気づく | 次の周で取り込み直す |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `drive` | ロックを取れない | 同じ PR の実行が動いている | 状態を読む前 | 止める | `precondition` | 何も変えない。終わってから打ち直す |
| `protection_at` | テストのコマンドが `--work` の配下を絶対パスで指す | 検証用の作業場所ではないコードをテストする指定 | 提案の前と、取り込んだ先頭の設定を読むとき | 止める | `precondition`。停止の理由の文に、`suite_label` の呼び名、当たった設定のキー（`command` か `scope_command`）、「リポジトリの中のファイルは相対パスで指す」という直し方の案内を載せる。当たった語そのものは載せない | 送らない。設定を直して打ち直す |
| `settings_at` | 設定が JSON でない | 設定の誤り | 読み取り | 止める | `precondition` | 直して打ち直す |
| `verify_tree` | 作成か lock の失敗 | git の失敗 | `local_gate` | 呼び出し元へ返す | `precondition` | 記録に残り、次の実行が片付ける |
| `verify_tree` | 取り除けない | ファイルの権限など | 終了時 | 理由を標準エラーへ出して続ける | 結果は変えない | 記録に残し、次の実行か `release` が片付ける |
| `run_suites` | プロセスを起動できない | シェルや作業場所が使えない | 起動の `OSError` | 残りの suite を打たずに止める | `precondition`。`suite_label` の呼び名とエラーの種別 | 送らない。環境を直して打ち直す |
| `run_local_gate` | 取り込んだ先頭だけのテストが 126・127 を返す | テストランナーが無い、実行の権限が無い | 「処理の手順」の順 9・11 | 止める（直さない） | `precondition`。`suite_label` の呼び名と終了コード | 送らない。コマンドか環境を直して打ち直す |
| `run_suites` | suite が落ちる（非ゼロ。126・127 を含む）、1 本の上限を超える | テストの失敗 | 終了コード、`TimeoutExpired` | 呼び出し元へ返す | 直しの後も落ちれば `test_failed` | コミットは担当が書く作業場所（`rf<PR>/work`）に残る |
| `run_suites` | 次のどちらか。(a) 累計の時間が累計の上限に達した。(b) 累計の残りが suite 1 つの上限より短いので、残りを制限時間にして打ち、その時間を超えた | テストが重い | 起動の前と `TimeoutExpired` | 止める | `fix_limit`。停止の理由の文に、使った累計、上限の値、キー名 `test.local_total_minutes` | 打ち直すと累計は 0 から |
| `write_private_log` | `logs/` の権限を直せない、所有者が違う、シンボリックリンク | 保存先の権限や置き換え | ログを書く前 | 止める | `precondition`。保存先のパス | 出力を書かず、送らない。保存先を直して打ち直す |
| `run_suites` | 追跡ファイルか `HEAD` が変わる | テストが書き換える | テストの後 | 止める | `precondition`。書き換わったファイル | 送らない |
| `run_suites` | テストの実行枠が空くのを待ちきれない、空きメモリが足りずにテストを止める | マシンの混雑 | `slots.Stopped` | 呼び出し元へ返す | 今の停止理由のまま | 打ち直せる |
| `run_local_gate` | 設計文書の「停止する場面と停止理由」の表の場面（`test_failed`・`refused`・`push_rejected` など） | 同じ表の「場面」の列のとおり | 上の「処理の手順」の表の順 | 止める（`final_gate.GateStopped`） | 同じ表の「停止理由」の列の値を持つ結果 JSON | 状態ファイルを残し、打ち直せる |
| `_write_pushed` | ファイルに書けない。指定のパスがリンク・ディレクトリ・SHA 1 行でない既存のファイル | 保存先の誤り | 書き込みの前 | 止める | `precondition`。指定のパスと、書かなかった理由 | 既存のファイルとリンク先は変えない。出力先を直して打ち直すと、再利用した結果で書く |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `run_suites` | suite ごとに、`suite_label` の呼び名と、伏せ字にしたコマンドと出力の全文 | `rf<PR>/logs/local-gate-<周>-<対象>-<順番>.log`（`write_private_log` で書く）。`<対象>` は、対象の SHA のテストが `head`、取り込んだ先頭だけのテストが `base`。`<順番>` は設定の順番で、suite の名前はファイル名に使わない | 伏せる前のコマンドと出力は、どこにも書かない。結果 JSON、状態ファイル、PR のコメント、標準出力と標準エラーには、コマンドも出力も載せない |
| `run_local_gate` | 落ちた suite の `suite_label` の呼び名と終了コード（起動できなければエラーの種別）、使った秒と上限 | 結果 JSON の `summary`、最終ゲートの記録 | 環境変数 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `run_local_gate` | `final_gate.send`・`final_gate.run_gate` | `final_gate.py` を読んだ | `send` は取り込み直して `HEAD` を送るので使わない。`GateStopped`・直しの確かめは使う |
| `run_local_gate` | `worktree.fetch_tips`・`worktree.sync_worktree` の同期の処理 | `core/converge/worktree.py` を読んだ | `sync_worktree` は呼ばない。内部で `fetch_tips` をもう一度打ち、リモートの先頭が同じでも `reset --hard` と `cherry-pick` を打つためである。`fetch_tips` の結果を、先頭が `HEAD` に含まれていないときだけ `sync_fetched` へ渡す。どの周も、始めに `refuse_dirty` を打つ |
| `run_suites` | `verify.failing`・`slots.run_command` | `verify.py`・`slots.py` を読んだ | `verify.failing` は変えたファイルに対応するテストだけを打つので使わない。起動は `run_command` で、`suites=means.declarations` を渡す。省くと実行ディレクトリから設定を読み直し、`--work` の設定で補ったリポジトリでは、検証用の作業場所から同じ宣言が見つかるとは限らない |
| `verify_tree`・`sweep_verify_trees` | `worktree.lock_review_worktree`・`lock_of`・`release`・`sweep_orphans` | 同上 | 前の 2 つを使う。`worktree.release` は lock の無い作業場所を `--force` で消さず、`worktree.sweep_orphans` はディレクトリが残っている作業場所の lock を外さない。中断で残ったものを回収できないので、片付けは新しく書く |
| `redact_output` | `run_log.redact`・`project.secret.redact` | `run_log.py`・`secret.py` を読んだ | 両方使う。`run_log.redact` は一部の形のキーを伏せられず、`secret.redact` は環境変数の値を伏せられないためである。どちらも複数行の秘密鍵の本体を伏せないので、先にブロックを伏せる処理を足す |
| `write_private_log` | `hooks/outbox.py` の保存先の用意（0700 に直す） | `grep -rn "0o600" src/ndf` | 同じ形にする。`outbox` の関数は送信の保存先に固有なので呼ばない |
| `hold` | `slot_pool.try_lock` | `grep -rn "FileLock" src/ndf` | 使う |
| `protected_of` | `weight.split_commands`・`scoped.in_scope` | `testing/` を読んだ | 使う |
| `inside_work` | `weight.split_commands` | `weight.py` を読んだ | リダイレクト先を捨てるので使わず、同じ字句の分け方の `all_words` を足す |
| `settings_at` | `declared.read_ndf_settings` | `declared.py` を読んだ | `--work` の側はこれを使う。コミットの側はファイルでないので git で読む |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `protected_of` | 決まった名前（どの階層の `conftest.py` も含む）、コマンドが名指すスクリプト、宣言のディレクトリ | `./` や `..` を含む表記、`--config=` の形、リンク、ツリーに無いパスの新設、親がリンクに置き換わる、`PATH` の `.` と相対の要素 | ツリーの外を指すリンク |
| `inside_work` | 相対パスと、ルートの外の絶対パスは返さない | `--config=<ルート>/x` の形、ルートそのもの、リダイレクト先（`>`・`>>`・`<`・`2>`） | `scripts/../` を含む絶対パス |
| `narrow` | 保護するファイルの指定が消える | 保護するファイルとほかを含むディレクトリは残る | すべて保護なら空 |
| `run_local_gate` | 合格して指定の SHA を送る。変更が無ければ打たない | 最後の周でテストが落ちる場合、PR のブランチが進んだために push が拒まれる場合 | 処理の手順の表の行ごと |
| `run_suites` | 設定の順にすべて打つ | 累計の残りが suite の上限より小さい | 異常系の行ごと。時計と起動を差し替える |
| `write_private_log` | ディレクトリ 0700・ファイル 0600 | umask 022、前からある 0755 の `logs/` と 0644 のログ | `logs/` がシンボリックリンク。同じ名前が別のファイルへのハードリンクかシンボリックリンク（リンク先の中身と権限が変わらない） |
| `sweep_gate_logs` | 期限を過ぎた、記録のあるログを消す。ほかの PR の `rf<PR>/` も同じリポジトリなら消す | 期限の内のログは残す。ほかの PR のロックが取られていれば飛ばす | 名前が `local-gate-` でないファイル、`gate_logs` に記録の無い同じ接頭辞のファイル、状態ファイルの `repo` が違うか無い `rf<PR>/`、シンボリックリンクの `logs/` に触れない |
| `redact_output` | トークンの形と秘密の名前の環境変数の値を伏せる | 複数行の秘密鍵（終わりの行の有無、開始と終わりが同じ行） | 回帰テスト: `-----BEGIN … PRIVATE KEY-----` の行を行単位の伏せ字が置き換える場合でも、鍵の本体の行が結果に残らない |
| `suite_label` | 順番と名前 | 100 文字を超える名前、`/` を含む名前 | 認証情報の形を含む名前は順番だけになる |
| `run_suites`（ログ） | 伏せ字にしたログを書く | 同じ名前の suite が 2 つ、`/` を含む名前（別々のログになる） | 伏せる前の値がファイルに残らない |
| `fix_excerpt` | 伏せ字の後に切る | 100 行と 8,000 文字の境、境をまたぐトークンと秘密鍵、短い出力は全体 | 出力が空 |
| `sweep_verify_trees` | 記録のあるパスだけ消す | lock が無いもの、NDF の lock、利用者の lock の 3 つで扱いが分かれる | 記録の無いディレクトリに触れない |
| `drive`・`_reused` | 同じ SHA・設定・`PATH` なら再利用し、`--pushed-out` を書く。`PATH` だけが違えば再利用しない | 対象範囲が空 | ロック中は何も変えない |
| `_write_pushed` | 無いパスに書く。前の実行が書いた SHA 1 行のファイルを置き換える | 同じファイルへのハードリンクがあっても、リンクの側の中身は変わらない | シンボリックリンク、ディレクトリ、ほかの中身の既存のファイルは `precondition` で、中身もリンク先も変わらない |

実の git の作業場所と bare のリモートは、今ある `tests/domains/crossrefactor/_drive_world.py` を使う。

## 工程のプランの組み立て

満たす決定: 決定 6、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_by_refactor_gate` | `core/route/__init__.py` | 新設 | `_by_refactor_gate(name: str, plan: Mapping[str, Any], values: Mapping[str, Value]) -> dict[str, Any]` | 起動の引数に `--local-test` があるかで、プランのステップを組み替える |
| `render` | 同上 | 変更 | 今のまま | `expanded` と `_fill` の間で `_by_refactor_gate` を通す |

| テンプレート | `--local-test` | 結果 |
| --- | --- | --- |
| `cross-refactoring` | 無し | `follow` を外す（`without_steps`） |
| `cross-refactoring` | 有り | `push`・`fix-push` を外し、`refactor` の `run` に `--pushed-out {state_dir}/pushed-sha` を足して `on_cause.incomplete` を消す |
| `check` | 有り | `RouteUnsupported`（`RouteError` の派生） |
| ほか | — | 変えない |

`follow` のステップは `parts/steps.json` に足す。引数は順にブランチ、リモート、SHA のファイルである。失敗はどれも終了コード 1 にそろえ、`exit_cause` を `precondition` にする。

```bash
s=$(cat "$2") && [ -n "$s" ] && git fetch -q -- "$1" "refs/heads/$0" && git merge -q --ff-only --no-overwrite-ignore "$s" && [ "$(git rev-parse HEAD)" = "$s" ] || exit 1
```

2026-11-26 に一時リポジトリで打ち、終了コードは次のとおりだった。呼び出し元の worktree の `HEAD` が指定の SHA より古い場合と、一致する場合は 0 である。指定の SHA より先に進んでいる場合と、ファイルが無い場合は 1 である。

`--no-overwrite-ignore` を付けるのは、利用者のファイルを守るためである。既定の `git merge --ff-only` は、git の無視の対象（`.gitignore` など）のファイルが更新先で追跡されるパスにあると、黙って上書きする。2026-11-26 に git 2.53.0 で打つと、既定では終了コード 0 で中身が置き換わり、このオプションを付けると終了コード 1 でファイルが残った。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_by_refactor_gate` | `check` を手元の経路で組む | 対象外の組み合わせ | プランを組むとき | 止める（`RouteUnsupported`） | `precondition` | プランを書かない |

これまで `plan_failure` は `RouteError` をすべて `invalid` にしていた。この PR により、派生の `RouteUnsupported` は `RouteError` より先に判定して `precondition` にする。`ndf route` から直接組む経路も同じ停止理由にする。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_by_refactor_gate` | 2 つの経路のステップの並びと遷移 | `--candidates` が後ろに足された起動 | `check` と `--local-test` |

## 最終ゲート修正の指示文

満たす決定: 決定 6、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `final_fix` | `prompt_decide.py` | 変更 | `final_fix(pr: int, scope: Sequence[str], failed: Sequence[str], *, local: bool = False) -> str` | `local` なら「手元で次のテストが落ちた」と書く。`local` の `failed` は `fix_excerpt` の結果で、載せる項目と上限は設計書の「テストのコマンドと出力の扱い」にある。`local` では、指示文全体を UTF-8 で 60,000 バイト（定数 `PROMPT_BYTES`、`prompt_decide.py`）以下にする。`failed` は先頭から順に 1 件ずつ丸ごと載せる。1 件でも収まらなければ、その件と以降の件は載せず、「ほかに N 件が失敗した」と件数だけを書く。60,000 バイトは、Linux が引数 1 つに許す 131,072 バイトから、起動の側が指示文の後ろに足す文の分の余裕を引いた値である。件数の文のほかに、`final_fix` は渡されたもの以外の情報を足さない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `final_fix` | `local` の真偽で「CI で落ちた」と「手元で落ちた」が切り替わり、`failed` の内容が指示文に載る | 出力の末尾が空。多バイトの長い `failed` が多数でも `PROMPT_BYTES` 以下で、載せなかった件数が合う | — |

文言そのものは照合しない。

## 手順書と確定仕様

満たす決定: 決定 1・3・4・6・10、受け入れ条件 11・12

| 文書 | 書くこと |
| --- | --- |
| `skills/development-workflow/SKILL.md` | 工程の表の「リファクタリング」の行を、行を増やさずに書き換える。実行の条件、`workflow.stages` で止める書き方（キー・`reason` が必須・新しく作るスプリントから効く）、`test.protected_files`・`test.local_total_minutes` への参照 |
| `skills/cross-refactoring/SKILL.md` | 引数の表に `--local-test`・`--pushed-out`、手元の最終ゲートの流れと停止理由、必須のチェックの経路が確かめる範囲 |
| `docs/specifications/` の 3 本 | 「CI テストの設定が無いリポジトリでは通さない」を今の振る舞いとして書いている所を直す |

`.md` の文言を照合するテストは書かない。
