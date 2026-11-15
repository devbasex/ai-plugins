# #3616 の詳細設計: 落ちたテストを JUnit の成果物とランナーごとのログのパーサーで読み、収集の確かめの終了コードを suite の設定から読む

設計文書 [issue-3616-design.md](issue-3616-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。パスはすべて `plugins/ndf-next/` からの相対で書く（表の「パス」の列だけはリポジトリの根からのパス）。

用語は設計文書の「用語」と同じである。この文書で足す語は次の 2 つだけである。

| 語 | 意味 |
| --- | --- |
| 読み取りの記録 | 落ちたテストのチェック 1 件について、どこから読んだか（`junit`・`log`・`none`）・何件読めたか・読めなかった理由を持つ 1 行（`FailedTestRead`）。CI の結果 JSON の `metrics.failed_test_reads` の 1 要素になる |
| 取得の枠 | 1 回の組み立てで取得してよい成果物の ZIP の本数。`limits.json` の `github.failed-logs` の `count`（今は 5）で、読むチェックの数の上限と同じ値を使う |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/schemas/
│   ├── declarations/project.schema.json
│   └── io/ci.result.schema.json
├── docs/contracts/
│   ├── commands.md            （生成物）
│   └── declarations.md        （生成物）
├── src/ndf/
│   ├── testing/
│   │   ├── junit.py           *
│   │   └── ci_logs.py         *
│   ├── adapters/
│   │   ├── ci.py
│   │   └── github.py
│   ├── ci_cli.py
│   ├── core/route/local_tests.py
│   ├── domains/crossrefactor/
│   │   ├── collect.py
│   │   └── final_gate.py
│   └── checks/children.json
└── tests/
    ├── testing/
    │   ├── test_junit.py      *
    │   └── test_ci_logs.py    *
    ├── adapters/{test_ci.py, test_ci_cli.py, test_github.py}
    ├── route/test_commands.py
    └── domains/
        ├── crossrefactor/{test_added_tests_collected.py, test_gate_and_probe.py}
        └── project/test_project_cli.py
```

依存の向きは一方向である。`ndf.testing.junit` と `ndf.testing.ci_logs` は標準ライブラリだけを読み、`ndf.adapters` も `ndf.domains` も読まない。`ndf.adapters.ci` は `ndf.adapters.github`・`ndf.adapters.git`・`ndf.testing.junit`・`ndf.testing.ci_logs`・`ndf.testing.declared` を読む。`ndf.ci_cli` と `ndf.domains.crossrefactor.final_gate` は `ndf.adapters.ci` を読み、逆向きの import は無い。`ndf.core.route.local_tests` は結果 JSON のファイルだけを読み、`ndf.adapters.ci` を読まない（設計の決定 6）。見本の XML と出所を書いたファイルは `tests/testing/` の下へ置き、置き場の名前は実装で決める（設計文書の「設計の結果」）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/testing/junit.py` | 新設 | JUnit の XML と成果物の ZIP から、落ちたテストとファイルを決められない testcase の理由を返す | 無し（標準ライブラリだけ） |
| `plugins/ndf-next/src/ndf/testing/ci_logs.py` | 新設 | `runner` の名前からログのパーサーを引く対応表と、pytest のパーサーを持つ | 無し（標準ライブラリだけ） |
| `plugins/ndf-next/src/ndf/adapters/ci.py` | 変更 | 落ちたテストのチェックごとに成果物を先に、ログを後に読み、読み取りの記録を組む | `ndf.adapters.github`・`ndf.adapters.git`・`ndf.testing.junit`・`ndf.testing.ci_logs`・`ndf.testing.declared` |
| `plugins/ndf-next/src/ndf/adapters/github.py` | 変更 | ジョブ・ワークフローの実行・実行の成果物の一覧・成果物の ZIP の 4 つの読み取りを足す | githubkit（今のまま） |
| `plugins/ndf-next/src/ndf/ci_cli.py` | 変更 | `ndf ci watch` が `test_failed` で止まったとき、組み立ての結果を結果 JSON の `metrics` へ載せる | `ndf.adapters.ci` |
| `plugins/ndf-next/src/ndf/core/route/local_tests.py` | 変更 | 落ちたテストが空のとき、読み取りの記録から止まった理由の文を組む | `ndf.contracts`（今のまま） |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/final_gate.py` | 変更 | 最後の確かめが、作業場所の設定を組み立てへ渡す | `ndf.adapters.ci` |
| `plugins/ndf-next/src/ndf/domains/crossrefactor/collect.py` | 変更 | 収集の確かめの成功の終了コードを suite の `collect_exit_codes` から読む | `ndf.testing`（今のまま） |
| `plugins/ndf-next/src/ndf/checks/children.json` | 変更 | 子の行 `AD4` を足す | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/project.schema.json` | 変更 | `Suite` に `collect_command`・`collect_exit_codes` を足し、`TestCi.junit_artifacts` の説明を直す | 無し |
| `plugins/ndf-next/contracts/schemas/io/ci.result.schema.json` | 変更 | `metrics` に `junit_artifacts`・`failed_test_reads` を足し、`test_failed` の結果で `failed_test_reads` を必須にする | 無し |
| `plugins/ndf-next/docs/contracts/commands.md` | 変更 | スキーマから `ndf check` が作り直す生成物 | CI の結果 JSON のスキーマ |
| `plugins/ndf-next/docs/contracts/declarations.md` | 変更 | スキーマから `ndf check` が作り直す生成物 | 設定ファイルのスキーマ |
| `plugins/ndf-next/tests/testing/test_junit.py` | 新設 | JUnit の読み取りの単体テスト | `ndf.testing.junit` |
| `plugins/ndf-next/tests/testing/test_ci_logs.py` | 新設 | ログのパーサーの一覧の単体テスト（受け入れ条件 7 のテストを `test_ci.py` から移す） | `ndf.testing.ci_logs` |
| `plugins/ndf-next/tests/adapters/test_ci.py` | 変更 | 組み立ての単体テスト（偽の GitHub） | `ndf.adapters.ci` |
| `plugins/ndf-next/tests/adapters/test_ci_cli.py` | 変更 | 結果 JSON の `metrics` の形のテスト | `ndf.ci_cli` |
| `plugins/ndf-next/tests/adapters/test_github.py` | 変更 | 4 つの読み取りのテスト（偽の転送） | `ndf.adapters.github` |
| `plugins/ndf-next/tests/route/test_commands.py` | 変更 | 止まった理由の文のテスト | `ndf.core.route` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_added_tests_collected.py` | 変更 | 収集の確かめの終了コードのテスト | `ndf.domains.crossrefactor.collect` |
| `plugins/ndf-next/tests/domains/crossrefactor/test_gate_and_probe.py` | 変更 | 最後の確かめが設定を渡すテスト | `ndf.domains.crossrefactor.final_gate` |
| `plugins/ndf-next/tests/domains/project/test_project_cli.py` | 変更 | 設定ファイルのスキーマの検査のテスト | `ndf.domains.project` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/testing/junit.py` | モジュール | 新設: `JunitFailures`・`JunitError`・`junit_failures`・`resolve_file`・`xml_members`・`_normalized`・`_failed_cases` |
| `src/ndf/testing/ci_logs.py` | モジュール | 新設: `LogParser`・`PARSERS`・`parser_of`・`pytest_failures`（`adapters/ci.py` の `failed_test_nodes` を名前を変えて移す） |
| `src/ndf/adapters/github.py` | `GitHub` | 新設: `job`・`workflow_run`・`run_artifacts`・`artifact_zip` |
| `src/ndf/adapters/ci.py` | モジュール | 新設: `ReadSource`・`FailedTestSource`（`from_work`）・`FailedTestRead`（`as_metric`）・`FailedTests`・`failed_tests_of`・`runner_of`・`_RunJunit`・`_read_run_junit`・`_workflow_file`。削除: `failed_test_nodes`・`failed_test_nodes_of`・`_PYTEST_FAILED`。変えない: `files_of` |
| `src/ndf/ci_cli.py` | モジュール | 変更: `_failed_nodes` を `_failed_metrics` に置き換える・`_report`（呼ぶ関数を替えるだけ） |
| `src/ndf/core/route/local_tests.py` | モジュール | 新設: `unread_detail`。変更: `_failed_nodes`（結果 JSON も返す）・`_scoped_plan` |
| `src/ndf/domains/crossrefactor/final_gate.py` | モジュール・`CiPort` | 変更: `_failed_tests`・`CiPort.failed_tests` の型・`run_gate`（組み立ての入力を作って渡す） |
| `src/ndf/domains/crossrefactor/collect.py` | モジュール | 新設: `exit_codes_of`。変更: `_collect`・`collected`（引数を 1 つ渡すだけ）。削除: `COLLECT_OK` |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `junit_failures` | `src/ndf/testing/junit.py` | 新設 | `(xml: bytes, tracked: frozenset[str]) -> JunitFailures` | XML 1 本の落ちた testcase を `<ファイル>::<name>` と、ファイルを決められない testcase の理由へ分ける | `tracked` は作業ディレクトリの根からの相対パスの集合 | `nodes` は重ならず昇順で、各要素のファイルは `tracked` に入る（I1・I2）。`<skipped>` だけの testcase と通った testcase はどちらにも入らない（I3） | 無し |
| `resolve_file` | `src/ndf/testing/junit.py` | 新設 | `(candidates: Sequence[str], tracked: frozenset[str]) -> Optional[str]` | 属性の候補を順に試し、追跡するファイルの 1 つに決める | 候補は試す順に並ぶ（空文字を含んでよい） | 返す値は `tracked` の要素か None。設計の「JUnit の読み取りのファイルの決め方」の規則に従う | 無し |
| `xml_members` | `src/ndf/testing/junit.py` | 新設 | `(archive: bytes) -> list[bytes]` | 成果物の ZIP の中の `.xml`（大文字小文字を問わない）の中身を、ZIP の中の名前の順に返す | 無し | 返す要素は ZIP の `.xml` のメンバーだけ | 無し |
| `parser_of` | `src/ndf/testing/ci_logs.py` | 新設 | `(runner: Optional[str]) -> Optional[LogParser]` | `runner` の名前に登録されたログのパーサーを返す | 無し | `PARSERS` に無い名前と None には None を返す（I4） | 無し |
| `pytest_failures` | `src/ndf/testing/ci_logs.py` | 新設 | `(log_text: str) -> list[str]` | pytest のログの `FAILED`・`ERROR` の行から node id を並べる | 無し | 今の `failed_test_nodes` と同じ入力に同じ並びを返す（重ならず昇順。I8・受け入れ条件 7） | 無し |
| `GitHub.job` | `src/ndf/adapters/github.py` | 新設 | `(job_id: int) -> dict` | `GET /repos/{repo}/actions/jobs/{job_id}` の本文を返す | 無し | 返す辞書は応答の JSON のまま | ネットワーク（GitHub の REST） |
| `GitHub.workflow_run` | `src/ndf/adapters/github.py` | 新設 | `(run_id: int) -> dict` | `GET /repos/{repo}/actions/runs/{run_id}` の本文を返す | 無し | 返す辞書は応答の JSON のまま | ネットワーク（GitHub の REST） |
| `GitHub.run_artifacts` | `src/ndf/adapters/github.py` | 新設 | `(run_id: int) -> list[dict]` | 実行の成果物の一覧を `total_count` に届くまで読む | 無し | 返す並びは応答の `artifacts` を順につないだもの（`github.list-pages` ページまで） | ネットワーク（GitHub の REST） |
| `GitHub.artifact_zip` | `src/ndf/adapters/github.py` | 新設 | `(artifact_id: int) -> bytes` | 成果物の ZIP の中身をバイト列で返す | 呼ぶ側が大きさ（一覧の `size_in_bytes`）を上限と比べ済み | 返すのは転送先の応答の本文のバイト列（`rest` のように文字列へ直さない） | ネットワーク（GitHub の REST と、302 の転送先） |
| `FailedTestSource.from_work` | `src/ndf/adapters/ci.py` | 新設 | `(cwd: Path) -> FailedTestSource` | 作業場所から根へ探した設定ファイルの glob と suite、作業ディレクトリが追跡するファイルを読む | 無し | `junit_artifacts` は文字列か None、`suites` はテストの suite（`declared.load_suites`）、`tracked` は根からの相対パスの集合 | ファイル（設定ファイルを読む）・外部コマンド（`git ls-files -z --full-name`） |
| `FailedTestRead.as_metric` | `src/ndf/adapters/ci.py` | 新設 | `() -> dict` | 読み取りの記録 1 行を結果 JSON の要素の辞書にする | 無し | キーは `check`・`run_id`・`runner`・`source`・`found`・`reason` の 6 つ（スキーマと同じ） | 無し |
| `failed_tests_of` | `src/ndf/adapters/ci.py` | 新設 | `(github: GitHub, checks: Iterable[Check], source: FailedTestSource) -> FailedTests` | 落ちたテストのチェックごとに成果物を先に、ログを後に読み、落ちたテストと読み取りの記録を組む | `checks` は落ちたテストのチェック（`Reading.failed_tests`） | `nodes` は重ならず昇順（I2）。`reads` は `checks` の 1 件ごとに 1 行で入力の順。取得した ZIP の本数は取得の枠以下（I5）。`GitHubError` を外へ投げない | ネットワーク（GitHub の REST） |
| `runner_of` | `src/ndf/adapters/ci.py` | 新設 | `(workflow_file: str, suites: Sequence[Mapping[str, Any]]) -> tuple[Optional[str], str]` | ワークフローのファイルに `ci_jobs` が当たる suite の `runner` を 1 つに決める | `workflow_file` は `@` 以降を外したパス | 当たる suite の `runner` が 1 種類ならそれと空の理由、当たらないか 2 種類以上なら None と理由 | 無し |
| `_read_run_junit` | `src/ndf/adapters/ci.py` | 新設 | `(github: GitHub, run_id: int, glob: str, tracked: frozenset[str], room: int) -> _RunJunit` | 1 つのワークフローの実行の成果物から落ちたテストを読み、その実行を「成果物から読めた」とするかを決める | `room` は残りの取得の枠（0 以上） | `ok` が真なら `nodes` が 1 件以上・決められない testcase が 0 件・枠で打ち切っていない。`fetched` は取得した ZIP の本数で `room` 以下 | ネットワーク（GitHub の REST） |
| `_failed_metrics` | `src/ndf/ci_cli.py` | 新設 | `(hub: GitHub, reading: Reading, cwd: Path) -> dict` | 組み立ての結果を結果 JSON の `metrics` の 5 つのキーへ写す | `test_failed` で止まったときだけ呼ぶ | `failed_tests`・`test_files`・`failed_test_reads` を必ず置き、`junit_artifacts` は設定にあるときだけ、`log_error` はジョブを特定できないチェックがあるときだけ置く | ファイル・外部コマンド・ネットワーク（`from_work` と `failed_tests_of` を通して） |
| `unread_detail` | `src/ndf/core/route/local_tests.py` | 新設 | `(found: Mapping[str, Any], path: Path) -> str` | 落ちたテストが空の CI の結果から、止まった理由の文を組む | `found` の落ちたテストのファイルが空 | 止まった理由が `test_failed` でなければ今の文、`test_failed` なら設計の「`ndf route test --failed-from`」の 2 つの文のどちらか | 無し |
| `_failed_nodes` | `src/ndf/core/route/local_tests.py` | 変更 | `(path: Path) -> tuple[list[str], dict[str, Any]]` | CI の結果 JSON の落ちたテストと、結果 JSON そのものを返す | 無し | 返す結果 JSON は `read_result` の値（None でない） | ファイル（CI の結果を読む） |
| `_scoped_plan` | `src/ndf/core/route/local_tests.py` | 変更 | `(ci_result: Path, cwd: Path) -> tuple[list[str], list[scoped.ScopedRun]]` | 落ちたテストが空のときの止まった理由の文を `unread_detail` に任せる | 無し | ファイルが空なら必ず `_NotRunnable` か `LocalTestError` を投げ、テストのコマンドを返さない（I6） | ファイル（CI の結果と設定ファイルを読む） |
| `_failed_tests` | `src/ndf/domains/crossrefactor/final_gate.py` | 変更 | `(hub: github.GitHub, watched: ci.WatchResult, source: ci.FailedTestSource) -> list[str]` | 最後の確かめの落ちたテストを、`ndf ci watch` と同じ組み立てで得る | 無し | 返すのは `failed_tests_of(...).nodes`。空なら呼ぶ側が落ちたチェックの名前で直しを頼む（今のまま） | ネットワーク（GitHub の REST） |
| `run_gate` | `src/ndf/domains/crossrefactor/final_gate.py` | 変更 | 変えない | `test_failed` の周で `FailedTestSource.from_work` を作り、`CiPort.failed_tests` へ渡す | 無し | 渡す入力は `ndf ci watch` と同じ関数で作ったもの | ファイル・外部コマンド・ネットワーク（今のものに `from_work` が加わる） |
| `exit_codes_of` | `src/ndf/domains/crossrefactor/collect.py` | 新設 | `(suite: Mapping[str, Any]) -> frozenset[int]` | suite の `collect_exit_codes` を、成功と読む終了コードの集合にする | 無し | キーが無いか並びでなければ `{0}`（I10）。真偽値の要素は数えない | 無し |
| `_collect` | `src/ndf/domains/crossrefactor/collect.py` | 変更 | `(work: Path, name: str, command: str, timeout: float, ok: frozenset[int]) -> frozenset[str]` | 収集のコマンドの終了コードが `ok` に入るときだけ出力を読む | `ok` は空でない | `ok` に入らない終了コードでは値を返さない（I7） | 外部コマンド（収集のコマンド） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `junit_failures` | XML として読めない | 壊れた成果物・JUnit でない XML | `ET.fromstring` の `ET.ParseError` | `JunitError` にして呼び出し元へ返す | 無し（組み立てが読み取りの記録の `reason` に書く） | 後始末なし。同じ入力で再実行しても同じ結果 |
| `xml_members` | ZIP として読めない | 壊れた成果物 | `zipfile.BadZipFile` | `JunitError` にして呼び出し元へ返す | 無し（同上） | 同上 |
| `GitHub.job` | 応答が誤り・上限・ネットワーク | 権限・存在しないジョブ・rate limit | `_translating` | `GitHubError` で呼び出し元へ返す | 無し（組み立てが記録する） | 後始末なし。次の `ndf ci watch` で再実行できる |
| `GitHub.workflow_run` | 同上 | 同上 | `_translating` | `GitHubError` で呼び出し元へ返す | 無し（同上） | 同上 |
| `GitHub.run_artifacts` | 同上・ページの上限を超えた | 同上・成果物が `github.list-pages` × 100 件を超える | `_translating`・ページの数え上げ | `GitHubError` で呼び出し元へ返す（上限は `_too_many_pages`） | 無し（同上） | 同上 |
| `GitHub.artifact_zip` | 期限切れ（410）・応答の誤り・ネットワーク | 保存期間を過ぎた成果物・権限 | `_translating`（`GitHubError.status` が 410） | `GitHubError` で呼び出し元へ返す | 無し（同上） | 同上 |
| `FailedTestSource.from_work` | 設定ファイルを読めない | JSON の誤り・開けない | `declared.test_section`・`declared.load_suites`（読めなければ空を返す） | 既定値で続ける（glob も suite も無いとして読む。設計の入出力の契約） | 無し。結果 JSON に `junit_artifacts` が置かれない | 後始末なし |
| `FailedTestSource.from_work` | 追跡するファイルを読めない | git の作業ディレクトリでない・git を起動できない | `git.GitError` | 既定値で続ける（`tracked` を空にする。JUnit のどの testcase もファイルが決まらず、ログへ移る） | 無し。読み取りの記録の `reason` にファイルを決められない testcase が出る | 後始末なし |
| `failed_tests_of` | ジョブかワークフローの実行を読めない（E2 の失敗） | `GitHub.job`・`GitHub.workflow_run` の `GitHubError` | 組み立ての中 | そのチェックだけ読み元 `none`・`found` 0・`reason` に例外の文を残し、`FailedTests.job_errors` へ足して続ける | 結果 JSON の `log_error` に理由（2 件以上は `; ` でつなぐ）。止まった理由は `test_failed` のまま、終了コードも今のまま | 後始末なし。次の周で読み直す |
| `failed_tests_of` | 成果物の一覧・ZIP・XML を読めない | `GitHubError`・`JunitError` | `_read_run_junit` の結果の `ok` が偽 | その実行のチェックをログへ移す | 読み取りの記録の `reason` に成果物の理由が残る | 後始末なし |
| `failed_tests_of` | ログを読めない | `GitHub.job_log` の `GitHubError` | 組み立ての中 | そのチェックを読み元 `none` にし、成果物の理由とログの理由の両方を `reason` に残して続ける | 読み取りの記録の `reason` | 後始末なし |
| `failed_tests_of` | 読むチェックが上限を超えた | 落ちたテストのチェックが `github.failed-logs` の件数より多い | チェックの数え上げ | 超えた分のチェックを読まず、読み元 `none`・`reason` に上限を残す | 読み取りの記録の `reason` | 後始末なし |
| `_read_run_junit` | 取得の枠を使い切った | 当たる成果物が枠より多い | 取得の前の本数の比較 | 残りを取得せず、`ok` を偽・`reason` に上限で止めたことを入れて返す | 読み取りの記録の `reason`（呼び出し元が写す） | 後始末なし |
| `_read_run_junit` | 成果物が大きすぎる | 一覧の `size_in_bytes` が `MAX_ARTIFACT_BYTES` を超える | 取得の前の大きさの比較 | 取得せず、`ok` を偽・`reason` に大きさを入れて返す | 同上 | 後始末なし |
| `_read_run_junit` | 成果物の一覧・ZIP・XML を読めない | `GitHubError`・`JunitError` | 例外を捕まえる | `ok` を偽・`reason` に例外の文を入れて返す（ほかの例外は捕まえない） | 同上 | 後始末なし |
| `_failed_metrics` | 組み立てが想定外の例外を投げる | 実装の誤り | 捕まえない | 止める（`main` の外へ出る） | Python の例外の出力と終了コード 1 | 後始末なし。誤りを直して再実行する |
| `unread_detail` | 止まった理由が `test_failed` なのに読み取りの記録が無い | `ndf ci watch` の結果でないファイル・この変更の前の版が書いた結果 | `metrics.failed_test_reads` が並びでない | `LocalTestError` にして呼び出し元へ返す | `invalid` の結果 JSON（`_invalid`）と、その終了コード | 後始末なし。`ndf ci watch` を打ち直してから再実行する |
| `_failed_nodes` | CI の結果を読めない・結果 JSON でない | ファイルが無い・壊れている | `read_result` | `LocalTestError` で呼び出し元へ返す（今のまま） | `invalid` の結果 JSON | 同上 |
| `_scoped_plan` | 落ちたテストが空 | 読み取りの記録のとおり | ファイルの一覧が空 | `_NotRunnable(unread_detail(...))` で止める（I6） | `precondition` の結果 JSON（止まった理由の文は `unread_detail`） | 後始末なし。設定を足して CI を回し直せば再実行できる |
| `_failed_tests` | 組み立てが落ちたテストを返さない | 全チェックが読み元 `none` | 空の `nodes` | 呼び出し元へ空を返す | 無し（`run_gate` が落ちたチェックの名前で直しを頼む。今のまま） | 後始末なし |
| `run_gate` | 組み立ての入力を作れない | `from_work` の失敗は既定値で続けるため起きない | — | 呼び出し元へ返す（今の `GateStopped` の経路を変えない） | 今のまま | 今のまま |
| `_collect` | 終了コードが `ok` に入らない | 収集のコマンドの失敗・宣言に無い終了コード（pytest の 5 を宣言していない suite） | `proc.returncode not in ok` | `CollectError` で止める（今の文のまま） | 今のまま（`ndf refactor collected` の結果と、改修の項目の戻し） | 後始末なし。宣言を足すかコマンドを直して再実行する |

## 処理の手順

`failed_tests_of` の 1 チェックの読み元（チェックを入力の順に回す。`n` は `github.failed-logs` の件数）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `failed_tests_of` | チェックが先頭から `n` 件より後 | 読み元 `none`・`run_id` null・`runner` null・`reason` に上限 |
| `failed_tests_of` | ジョブかワークフローの実行を読めない | 読み元 `none`・`run_id` は読めた値か null・`reason` と `job_errors` に例外の文 |
| `failed_tests_of` | glob が設定にあり、実行の `_RunJunit.ok` が真 | 読み元 `junit`・`found` は実行の件数（同じ実行の行は同じ値）・`runner` は `runner_of` の値 |
| `failed_tests_of` | glob が無いか `ok` が偽、`runner_of` が `runner` を返し、`parser_of` がパーサーを返す、ログを読めた | 読み元 `log`・`found` はそのログから読めた件数（0 もありうる）・`reason` は成果物の理由（glob が無ければ空） |
| `failed_tests_of` | 同上でログを読めない | 読み元 `none`・`reason` に成果物の理由とログの理由 |
| `failed_tests_of` | glob が無いか `ok` が偽、`runner_of` が None | 読み元 `none`・`reason` に成果物の理由と `runner_of` の理由 |
| `failed_tests_of` | glob が無いか `ok` が偽、`runner` にパーサーが無い | 読み元 `none`・`runner` は名前・`reason` に「`runner` のログのパーサーが無い」と成果物の理由 |
| `_read_run_junit` | glob に当たり `expired` が偽の成果物が 0 本 | `ok` 偽・`reason` に「glob に当たる成果物が無い」 |
| `_read_run_junit` | 当たる本数が `room` を超える | `room` 本まで取得して読み、`ok` 偽・`reason` に上限 |
| `_read_run_junit` | 全本を取得でき、決められない testcase が 1 件以上 | `ok` 偽・`reason` に決められない testcase の理由（先頭の 3 件と残りの件数） |
| `_read_run_junit` | 全本を取得でき、落ちた testcase が 0 件 | `ok` 偽・`reason` に「成果物に落ちたテストが無い」 |
| `_read_run_junit` | 全本を取得でき、落ちが 1 件以上・決められない testcase が 0 件 | `ok` 真 |
| `resolve_file` | 正規化した候補が `tracked` の要素と一致 | その要素 |
| `resolve_file` | `tracked` の要素のうち、正規化した候補が `/<要素>` で終わるものがある（絶対パス） | 当たる要素のうち最も長いもの |
| `resolve_file` | `tracked` の要素のうち `/<候補>` で終わるものがちょうど 1 つ | その要素 |
| `resolve_file` | どれにも当たらない・末尾の照合で 2 つ以上が当たる | 次の候補へ。候補が尽きたら None |
| `runner_of` | `ci_jobs` の `#` より前が `workflow_file` と一致する suite の `runner` が 1 種類 | その `runner` |
| `runner_of` | 当たる suite が無い | None・`reason` に「ワークフロー <ファイル> に当たる suite が無い（ci_jobs）」 |
| `runner_of` | 当たる suite の `runner` が 2 種類以上 | None・`reason` に当たった `runner` の並び |
| `unread_detail` | 止まった理由が `test_failed` でない | 今の文（「落ちたテストが無い（全体テストは打たずに止まる）」） |
| `unread_detail` | `test_failed`・`failed_test_reads` が無い | `LocalTestError` |
| `unread_detail` | `test_failed`・`metrics.junit_artifacts` が無い | `test.ci.junit_artifacts` を書くよう促す文と、記録の `runner`（null なら「当たる suite が無い」）とチェックの名前 |
| `unread_detail` | `test_failed`・`metrics.junit_artifacts` がある | glob と、チェックごとの `reason` |

- 繰り返しの終わる条件: `failed_tests_of` はチェックの並びを 1 回だけ回す。実行ごとの成果物の読み取りは、`run_id` をキーにした辞書に結果を置いて 2 回目から使い回すため、同じ実行で 2 回呼ばない。`run_artifacts` のページは `total_count` に届くか空のページか `github.list-pages` で終わる
- 計算量の注意: `resolve_file` は testcase 1 件ごとに `tracked` を末尾の照合で走査する（`O(testcase の数 × 追跡するファイルの数)`）。落ちた testcase だけを照らし、一致の照合は集合の引きで先に済ませる。ZIP は取得の枠と `MAX_ARTIFACT_BYTES` で切った分だけメモリに載せる

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `JunitFailures` | 型（`NamedTuple`: `nodes: tuple[str, ...]`・`unresolved: tuple[str, ...]`） | `src/ndf/testing/junit.py` | — |
| `JunitError` | 型（例外） | `src/ndf/testing/junit.py` | — |
| `FAILURE_TAGS` | 定数 | `src/ndf/testing/junit.py` | `("failure", "error")` |
| `CANDIDATE_ATTRS` | 定数（候補の順。testcase か testsuite のどちらの属性か） | `src/ndf/testing/junit.py` | testcase の `file`・testsuite の `file`・testcase の `classname`・testsuite の `name` |
| `MAX_ARTIFACT_BYTES` | 定数（成果物 1 本の上限） | `src/ndf/testing/junit.py` | `50 * 1024 * 1024` |
| `LogParser` | 型（`Callable[[str], list[str]]`） | `src/ndf/testing/ci_logs.py` | — |
| `PARSERS` | 定数（`runner` → パーサーの対応表。唯一の置き場） | `src/ndf/testing/ci_logs.py` | `{"pytest": pytest_failures}` |
| `_PYTEST_FAILED` | 定数（正規表現） | `src/ndf/testing/ci_logs.py`（`adapters/ci.py` から移す） | 今の正規表現のまま |
| `ReadSource` | 型（`StrEnum`: `JUNIT`・`LOG`・`NONE`） | `src/ndf/adapters/ci.py` | — |
| `FailedTestSource` | 型（凍った dataclass: `junit_artifacts: Optional[str]`・`suites: tuple[Mapping, ...]`・`tracked: frozenset[str]`） | `src/ndf/adapters/ci.py` | — |
| `FailedTestRead` | 型（`NamedTuple`: `check`・`run_id`・`runner`・`source`・`found`・`reason`） | `src/ndf/adapters/ci.py` | — |
| `FailedTests` | 型（`NamedTuple`: `nodes: list[str]`・`reads: list[FailedTestRead]`・`job_errors: list[str]`） | `src/ndf/adapters/ci.py` | — |
| `_RunJunit` | 型（`NamedTuple`: `nodes`・`ok`・`reason`・`fetched`） | `src/ndf/adapters/ci.py` | — |
| 取得の枠・読むチェックの上限 | 設定値 | `contracts/limits.json` の `github.failed-logs` の `count`（今の値を使い、増やさない） | 5 |
| `EXIT_CODES_KEY` | 定数 | `src/ndf/domains/crossrefactor/collect.py` | `"collect_exit_codes"` |
| `DEFAULT_EXIT_CODES` | 定数 | `src/ndf/domains/crossrefactor/collect.py` | `frozenset({0})` |
| 子の行 `AD4` | 設定値 | `src/ndf/checks/children.json` | `packages: []`・`needs: ["AD1", "C3", "T1"]`・順番 6・レーン `lci-junit`（設計の決定 8） |

`MAX_ARTIFACT_BYTES` は、所要時間の測定（`domains/project/measure_ci.py` の `ARTIFACT_BYTES`）と同じ値だが、指す事実が違う。測定のものは 1 回の実行で読む成果物の合計の上限で、こちらは 1 本の上限である。`ndf.testing` は `ndf.domains` を読めないため、測定の定数を参照しない。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 複数のレーンの `ndf ci watch` と、cross-refactoring の最後の確かめ | GitHub の API の枠（rate limit） | 取らない。読むだけで、上限の扱いは今の `client(remaining=...)` が持つ | 読むだけで書き込まない。中断しても次の呼び出しが最初から読み直す |
| 同上 | 成果物の ZIP | 取らない。ZIP はメモリで読み、一時ファイルを作らない | 同上 |
| `ndf ci watch` と `ndf route test --failed-from` | CI の結果 JSON のファイル | 取らない。書き手は呼ぶ側のプラン（結果のファイルの置き場は今のまま）で、手元の確かめはその後のステップで読むだけ | 手元の確かめは同じファイルから同じ文を組む |
| `ndf ci watch` と、同じ作業ディレクトリでの git の操作 | 作業ディレクトリの索引 | 取らない。`git ls-files` は読むだけ | 同上 |
| 収集の確かめ | 収集のコマンドが触るもの | 今のまま（`slots.run_command`） | 今のまま |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_failed_metrics` | `failed_tests`・`test_files`・`failed_test_reads`・`junit_artifacts`・`log_error` | 結果 JSON の `metrics`（標準出力の最後の行） | 成果物の転送先の URL（署名を含む）・トークン・ZIP と XML の中身・ログの本文 |
| `failed_tests_of` | 読み取りの記録の `reason`（例外の文・決められない testcase の `classname` と `name`・試した属性） | 結果 JSON の `metrics.failed_test_reads`（`_failed_metrics` を通して） | `GitHubError` の文は要求のメソッドとパス（`GET /repos/...`）だけを持ち、転送先の URL を持たない。`failure` の本文（メッセージとスタックトレース）は写さない |
| `_scoped_plan` | `unread_detail` の文 | 結果 JSON の `cause.detail`（`precondition`） | 同上 |
| `_collect` | 今のまま（終了コードと標準エラーの末尾 400 字） | `CollectError` の文 | 今のまま |

待つ間の 1 行（`_progress` の標準エラーの出力）は変えない。成果物の読み取りは標準エラーへ何も出さない。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `junit_failures` | `domains/project/measure_ci.py` の `junit_seconds`、junitparser | `grep -rn "ElementTree" src`。junitparser は設計文書の LIB2 | 使えない。`junit_seconds` は根と testsuite の `time` だけを読み、testcase と落ちを読まない。読み方（`ET.fromstring` と `noqa: S314` の理由）は同じ形にそろえる。junitparser は設計の決定 3 |
| `resolve_file` | `testing/scoped.py` の `in_scope`・`files_of` | `grep -n "def " src/ndf/testing/scoped.py` | 使えない。`in_scope` は接頭辞と glob の照合で、末尾の照合を持たない |
| `xml_members` | `domains/project/measure_ci.py` の `_xmls` | `grep -rn "zipfile" src` | 使えない。`ndf.testing` から `ndf.domains` を読めない（設計の決定 8）。`_xmls` は壊れた ZIP を空として扱い、理由を返さない。`_xmls` をこの関数へ寄せる変更は、触るファイルに `measure_ci.py` を含まないため、この課題では行わない |
| `parser_of` | `adapters/ci.py` の `failed_test_nodes` | 読んだ（`src/ndf/adapters/ci.py:265`） | 使う。パーサーの中身として移し、対応表から引く |
| `pytest_failures` | `adapters/ci.py` の `failed_test_nodes` と `_PYTEST_FAILED` | 同上 | 使う（名前を変えて移す。正規表現と戻り値は変えない） |
| `GitHub.job` | `GitHub.rest` | `grep -n "def " src/ndf/adapters/github.py` | 使う（`rest("GET", ...)` を呼ぶだけ） |
| `GitHub.workflow_run` | `GitHub.rest` | 同上 | 使う |
| `GitHub.run_artifacts` | `GitHub._pages`・`GitHub.check_runs` のページの読み方 | `src/ndf/adapters/github.py:620`・`:689` を読んだ | `_pages` は使えない（応答の本文が並びで来る前提で、成果物の一覧は `{"total_count", "artifacts"}` の物で来る）。`check_runs` と同じ `total_count` までの読み方にそろえ、ページの上限は `github.list-pages` を使う |
| `GitHub.artifact_zip` | `GitHub.rest`・`GitHub.job_log` | `src/ndf/adapters/github.py:372`（`_response_body`）・`:718` を読んだ | `rest` は使えない（JSON でない本文を `resp.text` の文字列にし、ZIP を壊す）。`_translating` と `self.api.request` は使う。302 の転送は、同じく 302 で返す `job_log` が今のクライアントで読めていることから、クライアントが辿る |
| `FailedTestSource.from_work` | `testing/declared.py` の `test_section`・`load_suites`、`adapters/git.py` の `Git.run` | `grep -n "def " src/ndf/testing/declared.py`・`grep -rn "ls-files" src` | 使う（どちらも読めないときに空を返す扱いが入出力の契約と一致する） |
| `FailedTestRead.as_metric` | `ci_cli.py` の `_item` | 読んだ | 使えない。チェックの項目（`items`）の形で、`metrics` の要素の形と違う |
| `failed_tests_of` | `adapters/ci.py` の `failed_test_nodes_of` | 読んだ | 置き換える（上限の読み方 `limits.value("github.failed-logs", "count")` と `noqa: NDF201` の理由はそのまま使う） |
| `runner_of` | `adapters/ci.py` の `bare_name`・`is_test_check`、`domains/project/write.py` の `ci_jobs` のファイルを読む関数（295 行目。`#` より前を取る） | `grep -rn "ci_jobs" src` | 使えない。前の 2 つはチェックの名前の照合で、ワークフローのファイルの照合を持たない。`write.py` の関数は `ndf.domains` にあり、`ndf.adapters` から読めない（設計の決定 8）。`#` より前を取る切り方は同じ形にそろえる |
| `_read_run_junit` | `domains/project/measure_ci.py` の `_junit_of_run` | 読んだ | 使えない。`ndf.adapters` から `ndf.domains` を読めない。名前の推測（`JUNIT_NAME`）で選ぶ形は設計の決定 1 で採らない |
| `_failed_metrics` | `ci_cli.py` の `_failed_nodes` | 読んだ | 置き換える |
| `unread_detail` | `contracts/causes.py` の `kind_of` | `grep -n "def kind_of" src/ndf/contracts/causes.py` | 使う（結果 JSON の `cause` から止まった理由の種類を読む） |
| `exit_codes_of` | `testing/scoped.py` の `Suite` | 読んだ | 使えない。`Suite` は範囲のテストのキーだけを持ち、`collect_command` の読み手は `collect.py` の辞書の読み方である。同じ読み方にそろえる |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `junit_failures` | 5 つのランナーの見本の XML（設計のテスト設計の受け入れ条件 1 の形）から、落ちたテストだけが `<ファイル>::<name>` で返る | `<skipped>` だけの testcase・`<error>` だけの testcase・`testsuites` の無い根の `testsuite`・入れ子の `testsuite` | 壊れた XML で `JunitError` |
| `resolve_file` | `file` が相対パスで一致・`classname` がパスで一致 | 絶対パス（PHPUnit）で長い方のファイルを選ぶ・バックスラッシュと `./` の正規化・末尾の照合で 2 つ当たる候補は次へ | 全候補が当たらず None（jest の既定・pytest の `xunit2`） |
| `xml_members` | `.xml` と `.XML` だけを返す | `.xml` の無い ZIP で空 | 壊れた ZIP で `JunitError` |
| `parser_of` | `pytest` でパーサー | 試験用のパーサーを足した対応表でその `runner` が引ける（受け入れ条件 9） | 登録の無い名前・None で None |
| `pytest_failures` | 今の `test_failed_test_nodes_and_files_from_log` の入力と期待（受け入れ条件 7） | 同じ node の重なり・`ERROR <パス>` | `.py` を含まない行は拾わない |
| `GitHub.job` | 偽の転送の応答の JSON を返す | — | 404 で `GitHubError` |
| `GitHub.workflow_run` | 同上 | — | 404 で `GitHubError` |
| `GitHub.run_artifacts` | 2 ページを `total_count` までつなぐ | 空の一覧 | ページの上限を超えて `GitHubError` |
| `GitHub.artifact_zip` | 302 の後のバイト列が `bytes` のまま返る | — | 410 で `status` が 410 の `GitHubError` |
| `FailedTestSource.from_work` | 設定ファイルの glob とテストの suite と追跡するファイル（根からの相対） | 下位のディレクトリから呼んでも根からの相対パス・`kind: lint` の suite を含まない | 壊れた設定ファイルで glob と suite が空・git でない場所で `tracked` が空 |
| `FailedTestRead.as_metric` | 6 つのキーを持ち、CI の結果 JSON のスキーマに通る | `run_id`・`runner` が null | — |
| `failed_tests_of` | 成果物から読めた実行のチェックが読み元 `junit`（受け入れ条件 1・5） | 同じ実行の 2 チェックが同じ `run_id`・`found` を持つ・上限 + 1 本の成果物で取得が枠で止まる（I5）・上限より多いチェック | ジョブを読めないチェックだけ `none` で続ける（E2 の失敗）・成果物が無い・期限切れ・410 でログへ移る（受け入れ条件 6）・決められない testcase のある実行は全チェックがログへ移る・パーサーの無い `runner` は読まない（受け入れ条件 8）・ログを読めない |
| `runner_of` | `ci_jobs` の `.github/workflows/pytest.yml` に当たる | `#<ジョブ>` 付きの `ci_jobs` もファイルだけで照らす | 当たらない・`runner` が 2 種類 |
| `_read_run_junit` | `ok` 真 | `room` 0 で取得しない・大きさの上限ちょうど | 壊れた ZIP・一覧の `GitHubError`・決められない testcase・0 件 |
| `_failed_metrics` | 5 つのキーが置かれ、結果 JSON がスキーマに通る | glob の無い設定で `junit_artifacts` を置かない・`log_error` はジョブを読めないときだけ | 想定外の例外は捕まえない |
| `unread_detail` | glob が無い文に `test.ci.junit_artifacts` と `runner`（受け入れ条件 10）・glob がある文に glob と理由（受け入れ条件 11） | `runner` が null の記録・`test_failed` でない結果 | 記録の無い `test_failed` で `LocalTestError` |
| `_failed_nodes` | 落ちたテストと結果 JSON | 落ちたテストの無い結果 | 読めないファイルで `LocalTestError` |
| `_scoped_plan` | 落ちたテストのファイルを suite の範囲で打つ計画（今のまま） | — | 空の一覧でテストのコマンドを打たずに `precondition`（I6） |
| `_failed_tests` | 組み立てに作業場所の設定が渡り、JUnit の成果物から読む偽の GitHub で一覧が出る | — | 全チェックが `none` で空を返す |
| `run_gate` | `test_failed` の周で `CiPort.failed_tests` に入力が渡る | 通った周では入力を作らない | 今の止まる経路を変えない |
| `exit_codes_of` | `[0, 5]` で `{0, 5}` | キーが無いと `{0}`・`[true]` を数えない | 並びでない値で `{0}` |
| `_collect` | `[0, 5]` の suite で 5 が成功（受け入れ条件 12） | — | `[0]` で 1 が `CollectError`・宣言の無い suite で 5 が `CollectError`（受け入れ条件 13） |
