# #4331: テストの所要を CI に残し、main の中央値が上限を超えたら知らせる

## 関連リンク
- 要求仕様: [issue-4331-requirements.md](./issue-4331-requirements.md)（正は [#4331](https://github.com/devbasex/ai-plugins/issues/4331) の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4331-design.md](./issue-4331-design.md)、詳細設計書: [issue-4331-design-detail.md](./issue-4331-design-detail.md)
- モード: `standard`。実装計画を省く条件のうち「公開インタフェースに触れない」を満たさない（要求仕様の「影響」の公開インタフェースの行が「変わらない」でない）ので書く。触るファイルも 11 で、上限の 4 を超える

## 修正対象

詳細設計書の「地図」から引き写す。関数ごとの仕様は詳細設計書の各セクションにある。

| ファイル | 変える中身 |
| --- | --- |
| `.github/workflows/pytest.yml` | ジョブ `pytest-shard`: 試行の番号のファイルを書く手順を足す。根のテストの手順に名前 `根のテスト` を付ける。「NDF のテストのファイルを決める」の手順を消す。NDF のテストを `-p pytest_shard --junitxml` で流す。成果物 `pytest-ndf-junit-<番号>` を足す。3 つの成果物に `overwrite: true` を付ける。`ndf-ok-tools-<番号>` の保存期間を 30 日にする。中央値のジョブ `pytest-duration`（名前 `pytest (所要)`）を新設する |
| `scripts/pytest_shard.py` | 新設: `shard_env`・`_test_files`・`_stash_keys`・`pytest_configure`・`pytest_ignore_collect`・`_XdistHooks.pytest_configure_node`。変更: `_file_of`（引数 `base` を足す）・`_seconds`（NDF の XML も読む）。docstring を書き換える |
| `scripts/pytest-shard-seconds.json` | 実装の Pull Request の CI の成果物から作り直し、`plugins/ndf/tests/...` のキーを加える |
| `scripts/ci_test_durations.py`（新設） | `run` と `trend` のサブコマンド。`Gh`・`Part`・`Match`・`Search` と、詳細設計書の「所要の読み取り」の関数 |
| `scripts/ci-test-duration.json`（新設） | `runs`（10）・`limit_seconds`（実測から決める）・`top`（20） |
| `scripts/tests/test_pytest_shard.py`（新設） | 割り当てのプラグインと `seconds` と `files` の範囲テスト |
| `scripts/tests/test_ci_test_durations.py`（新設） | `run` と `trend` の範囲テスト（`gh` の応答を差し替える） |
| `plugins/ndf/tests/conftest.py` | `pytest_sessionfinish`: `NDF_OK_TOOLS_OUT` を渡された実行は記録を書くだけにする。docstring の 1 文を書き換える |
| `plugins/ndf/tests/test_ok_tools_record.py`（新設） | ok の出力の記録の範囲テスト |
| `.ndf/project.json` | `budget` から `tests` のキーを外す（決定 5） |
| `docs/specifications/ndf-split-tables-toolchain-and-budget.md` | このリポジトリはテストの量を実行時間で見ること、件数の上限は NDF の機能として残ることを書く（決定 5） |

## タスク分解

変更の種類は機能追加なので、最初のタスクは今の振る舞いを縛る退行テストにする。

### Task 1: 今の割り当てと ok の出力の確かめを退行テストで縛る
- **対象ファイル:** `scripts/tests/test_pytest_shard.py`（新設）、`plugins/ndf/tests/test_ok_tools_record.py`（新設）
- **変更内容:** `files --total 2 --index <番号>` の出力（`tests/a.py` に 2 件、`tests/b.py`・`tests/c.py` に 1 件ずつで、ジョブ 0 が `tests/a.py`、ジョブ 1 が `tests/b.py` と `tests/c.py`）を縛る。`NDF_OK_TOOLS_OUT` を渡さない全体の実行で、検証済みでないサブコマンドがあれば `exitstatus` が 1 になることを縛る。どちらも今のコードで通る
- **満たす受け入れ条件:** 4331:10、4331:11（退行しないことの土台）
- **進め方:** 今のコードで通るテストを先に置く（退行テストなので、落ちるテストからは始めない）

### Task 2: ok の出力の記録を、`NDF_OK_TOOLS_OUT` を渡された実行では書くだけにする
- **対象ファイル:** `plugins/ndf/tests/conftest.py`、`plugins/ndf/tests/test_ok_tools_record.py`
- **変更内容:** 詳細設計書の「ok の出力の記録」のとおり。`NDF_OK_TOOLS_OUT` が空でない全体の実行では、検証済みでないサブコマンドがあっても `exitstatus` を変えない。空の文字列は渡さないときと同じに扱う。docstring の 1 文を書き換える
- **満たす受け入れ条件:** 4331:11
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: NDF のテストを収集の前に実測秒でジョブへ分ける pytest のプラグイン
- **対象ファイル:** `scripts/pytest_shard.py`、`scripts/tests/test_pytest_shard.py`
- **変更内容:** `shard_env`・`_test_files`・`_stash_keys`・`pytest_configure`・`_XdistHooks.pytest_configure_node`・`pytest_ignore_collect` を足す。pytest はフックの中で import し、pytest の無い `python3` でも `files`・`seconds` が打てるようにする。単体テストの観点は詳細設計書の「割り当て」の表のとおり（6 つの持ち分の和が一覧と一致し重ならない・重いファイルが空いたジョブへ入る・worker は計算せず `workerinput` の対応を使う・対応の無い worker で `UsageError`）
- **満たす受け入れ条件:** 4331:8、4331:10
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: `seconds` が根と NDF の両方の XML から実測秒の表を作り直す
- **対象ファイル:** `scripts/pytest_shard.py`、`scripts/tests/test_pytest_shard.py`
- **変更内容:** `_file_of` に引数 `base` を足す。`_seconds` は XML のパスに `pytest-ndf-junit-` を含むものを `plugins/ndf` を起点に戻し、リポジトリのルートから見たパスをキーにする。壊れた XML ではパスを標準エラーへ書いて 1 を返し、表を書き直さない。`summary` の文と docstring を書き換える
- **満たす受け入れ条件:** 4331:8（実測秒の表の作り直し）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: テストのジョブで NDF の JUnit XML を残し、プラグインで分ける
- **対象ファイル:** `.github/workflows/pytest.yml`、`scripts/pytest-shard-seconds.json`
- **変更内容:** 詳細設計書の「テストのジョブ」の処理の手順の表のとおりにジョブ `pytest-shard` を並べ直す。Pull Request の CI が通ったら、`gh run download <run の ID> -p 'pytest-*junit-*' -D <成果物の保存先>` でその回の成果物を取得する。`python3 scripts/pytest_shard.py seconds <成果物の保存先>` で実測秒の表を作り直し、更新した表をコミットする。使う回は、6 つのジョブがすべて成功し、根と NDF の XML がすべてそろった回に限る。作り直した後の CI で、次の 3 つを測って Pull Request に書く。1 つ目は受け入れ条件 9 の前と後の秒（変更前は run 45280461372 で、6 ジョブの合計と最も長いジョブ。収集の手順の秒は、変更前の「NDF のテストのファイルを決める」と「NDF のテスト」の和と、変更後の「NDF のテスト」を比べる）。2 つ目は、ルートの `conftest.py` の外し方が NDF のテストで働かないことを、NDF のテストの件数で確かめた結果である。3 つ目は、`ndf project measure` の JUnit の ZIP の取得の増分の秒である（設計書の「影響と危険」の「マージの前の判定」で、6 個と 12 個を 3 回ずつ測る）
- **満たす受け入れ条件:** 4331:1、4331:8、4331:9、4331:10、4331:11
- **進め方:** テスト駆動を適用しない。ワークフローの定義は手元で流せず、実装の Pull Request の CI の実行と成果物の一覧で確かめる（設計書のテスト設計の条件 1・9 の行）

### Task 6: 1 回の実行の所要を出すサブコマンド `run`
- **対象ファイル:** `scripts/ci_test_durations.py`（新設）、`scripts/ci-test-duration.json`（新設）、`scripts/tests/test_ci_test_durations.py`（新設）
- **変更内容:** `Gh`（締め切りと `Gh.raw` の受信の上限を含む）・`read_config`・`shard_jobs`・`parse_junit`・`read_parts`・`match_attempts`・`fetch_run`・`render_run`・`cmd_run`・`main` を作る。`fetch_run` は詳細設計書の「展開の前の検証」の表のとおりに、成果物の名前・大きさ・エントリの名前・壊れた ZIP・書き出す先を検証する。設定ファイルの `limit_seconds` は、Task 5 の後の Pull Request の CI の実行を `run` で読んだ合計の 1.25 倍を 10 秒単位で切り上げた値にする。読む回は、部分集計とも失敗したジョブを含むとも出なかった回に限る
- **満たす受け入れ条件:** 4331:2、4331:3
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 7: この回の集計を書き、直近の中央値を上限と比べるサブコマンド `trend`
- **対象ファイル:** `scripts/ci_test_durations.py`、`scripts/tests/test_ci_test_durations.py`
- **変更内容:** `current_total`・`past_totals`・`verdict`・`render_trend`・`cmd_trend` を作る。時間の予算（`CI_TEST_DURATION_STARTED` から求める締め切り）、知らせの出し分け、読めないものの扱い、探索の上限は、設計書の「処理の流れ」と詳細設計書の「処理の手順」の表のとおり。終了コードは引数と設定の誤りのときだけ 2 で、超えたときも 0
- **満たす受け入れ条件:** 4331:4、4331:5、4331:6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 8: `main` への push で `trend` を打つ中央値のジョブ
- **対象ファイル:** `.github/workflows/pytest.yml`
- **変更内容:** 詳細設計書の「中央値のジョブ」の処理の手順の表のとおりにジョブ `pytest-duration` を足す。`continue-on-error: true` を付け、集約のジョブ `pytest` の `needs` に入れない。マージの前に `main` を取り込み、ワークフローのジョブの数が 40 で上限 `budget.ci_jobs` に収まることを数え直す
- **満たす受け入れ条件:** 4331:4、4331:6
- **進め方:** テスト駆動を適用しない。ワークフローの定義は手元で流せない。Pull Request の実行でジョブが飛ばされることを CI の画面で確かめ、`main` での動きはリリース後の確認の計画で見る

### Task 9: このリポジトリの設定から `budget.tests` を外し、確定仕様をそろえる
- **対象ファイル:** `.ndf/project.json`、`docs/specifications/ndf-split-tables-toolchain-and-budget.md`
- **変更内容:** 決定 5 のとおり。規則 `budget` のコードと設定のスキーマは変えない。`ndf check` を打ち、規則 `budget` がテストの件数で落とさないことを確かめる
- **満たす受け入れ条件:** 4331:7
- **進め方:** テスト駆動を適用しない。規則の側は既存の `plugins/ndf/tests/checks/test_budget.py`（キーが無い項目は比べない）が縛っている。設定と確定仕様の一致は cross-review で見る

## レビューの単位

見込みの差分は全体で約 2,300 行になり 1,000 行を超えるので、タスクの依存の向きで 3 つに分ける。所要を読む側（単位 2・3）は、テストのジョブが残す成果物の形（単位 1）に頼る。どの単位も単独でテストが通り、終えた後も CI が動く。行数は `git diff --shortstat <ベース>...HEAD -- . ':(exclude)scripts/pytest-shard-seconds.json' ':(exclude)issues/issue-*'` で数える（実測秒の表は再生成で作るファイルなので数えない）。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 単位 1: NDF の JUnit XML を残し、実測秒でジョブへ分ける | Task 1・2・3・4・5 | 4331:1・4331:8・4331:9・4331:10・4331:11 | 約 600 | （実装の後に書き足す） |
| 単位 2: 1 回の所要を読む `run` | Task 6 | 4331:2・4331:3 | 約 900 | （実装の後に書き足す） |
| 単位 3: 中央値を上限と比べる `trend` と中央値のジョブ、`budget.tests` を外す | Task 7・8・9 | 4331:4・4331:5・4331:6・4331:7 | 約 800 | （実装の後に書き足す） |

単位 2 の `limit_seconds` と、単位 1 の実測秒の表は、単位 1 の Pull Request の CI が残す NDF の JUnit XML から決める。そのため単位は 1 → 2 → 3 の順に流す。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る既存のファイルはどれも小さい（`scripts/pytest_shard.py` 101 行、`.github/workflows/pytest.yml` 256 行、`plugins/ndf/tests/conftest.py` 152 行）。同じファイルを触るタスクは、`pytest_shard.py` が 9 タスク中 2（Task 3・4）、`pytest.yml` が 2（Task 5・8）、`ci_test_durations.py` が 2（Task 6・7）で、1 つのファイルに集まらない。`pytest_shard.py` の既存のテストは `scripts/tests/test_root_conftest.py` から呼ぶものだけなので、Task 1 の退行テストを先に置いて縛る |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 単位 2 と単位 3 が同じ `scripts/ci_test_durations.py` を触る。`run` と `trend` が共有する試行の照合（`match_attempts`）と XML の読み取り（`read_parts`）を両方の呼び出し側から見直す。成果物の名前の頭・試行の番号のファイルの名前・手順の名前を、`scripts/ci_test_durations.py` と `scripts/pytest_shard.py` が別々の定数で持つことも候補にする | 2 つの単位にまたがる同じ形の処理と、2 か所に置いた定数で、単位ごとのレビューでは片方しか見えないため |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `ndf project measure` の JUnit の ZIP の取得の増分が 10 秒を超える | 単位 1 の中で設計書の「影響と危険」の「まとめる形」へ変える（根と NDF の XML を 1 つの成果物 `pytest-junit-<番号>` にまとめる）。変えたときは、単位 2 の `fetch_run` と `read_parts` を、まとめる形の見分け方（中のファイルの名前で見分ける）で作る |
| 実測秒の表に NDF のファイルが無い間は、NDF のテストがファイルの数で分けられる | Task 5 で表を作り直してから、受け入れ条件 8・9 の秒を測る |
| xdist の worker へ割り当てが渡らない | `pytest_configure` が worker で `UsageError` を上げて止める（詳細設計書の異常系の表）。CI で 6 ジョブの NDF のテストの件数の和が全体の件数と一致することを確かめる |
| `.github/workflows/pytest.yml` と `.ndf/project.json` を別の変更も触る | 各単位のマージの前に `main` を取り込み、ジョブの数を数え直す |

## 切り戻し手順
- 単位ごとに revert できる。データの移行は無い
- 単位 1 を戻すと、NDF のテストは項目数で分ける今の形に戻る。単位 2・3 のスクリプトは NDF の XML が無い回を「記録が無い」として扱う（`run` は 1、`trend` は無効な集計）
- 単位 3 を戻すと、中央値のジョブが無くなり、`.ndf/project.json` に `budget.tests` が戻る

## 完了の定義
- [ ] 受け入れ条件 4331:1〜4331:11 をすべて満たし、条件ごとに検証手段と結果が対応している（設計書の「テスト設計」の表）
- [ ] 範囲テスト（`scripts/tests/test_pytest_shard.py`・`scripts/tests/test_ci_test_durations.py`・`plugins/ndf/tests/test_ok_tools_record.py`）が通り、各単位の Pull Request の CI が通っている
- [ ] 受け入れ条件 9 の前と後の秒、`ndf project measure` の増分の秒、`limit_seconds` を決めた実行の ID が Pull Request に書いてある
