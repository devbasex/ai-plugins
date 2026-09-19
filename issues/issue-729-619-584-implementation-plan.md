# cross-review: 利用上限で止まった担当が「結果ファイル無し」と報告されて空振りの起動し直しで待たされ、止めた担当が後から結果を書く → 上限を理由に報告して同じラウンドで起動し直さず、止めた後は書かせない（実装計画 / #729 #619 #584）

## 関連リンク

- 親 issue #729、子 issue #619 #584
- 要求と受け入れ条件: [issue-729-619-584-requirements.md](issue-729-619-584-requirements.md)（AC1〜AC24）
- 設計: [issue-729-619-584-design.md](issue-729-619-584-design.md)（決定 12 件。識別子は冒頭の「用語の対応表」で引く）
- 設計 Pull Request: #781（`develop` へマージ済み）

## モード

`standard`。本番の振る舞い（結果なしの理由と起動し直しの判断）を変え、共通層と cross-review の複数モジュールにまたがる。

## 目的と非目的

達成したい状態:

- 担当の CLI が利用上限で落ちたとき、進行側と利用者に理由「利用上限」が届き、同じラウンドで同じ担当を起動し直さない
- 結果なしの判断と起動し直しの可否を、結末の共通層の 1 つの関数が持つ。cross-review はその値を読むだけになる
- 監視が止めた担当の子プロセスが、止めた後に結果ファイルを書かない

やらないこと:

- cross-refactoring の結果の読み取りと 3 つの取り込みの実装（G4 #728 が、この計画が作る結末を読む関数を使って行う）
- 投稿の後に打ち切られた担当の記録と重ねての投稿（G5 #730）
- 利用上限の担当を外して残りの担当で回す判断（#478）
- 監視の終了コードと標準出力の変更、cross-review の骨組み（`SKILL.md`）の変更

## 前提

- 前提 1: 設計文書の決定 12 件を変えない。実装で決めるのは「未確認のまま残ること」の 4 件（claude の 429 の出る先・bash 3.2 の `set -m`・文言の並ぶ順序・stem の突き合わせ）で、決めた結果は設計文書の同じ節へ書く
- 前提 2: 監視の結果ファイルと監視の記録（P1）、上限の表と工程（P2）は `develop` にある。この変更はその上に載せる
- 前提 3: 手元の bash は 5.3.9 である。macOS の bash 3.2 は実機が無いため、`set -m` の互換は bash の変更履歴の確認までとし、成り立たない場合の逃げ道（グループの先頭でなければ pid だけを止める）を実装で保証する

## 受け入れ条件

要求文書の AC1〜AC24 をそのまま使う。検証手段は設計文書の「テスト設計」の表にある。この計画では、各タスクが満たす番号を「タスク分解」で示す。

## ドメイン用語

設計文書の「用語の対応表」を使う。この計画で新たに使う語は次の 2 つである。

| 用語 | 意味 |
| --- | --- |
| 共通層のタスク | 結末の語彙・監視・起動の手順を変えるタスク（Task 1〜4）。cross-review を触らない |
| 読む側のタスク | cross-review の状態の操作を変えるタスク（Task 5〜6）。共通層のタスクの後に行う |

## 不変条件

- 監視の終了コード 0〜6 と標準出力の 13 個のキーは変わらない
- 結果の取り込みの終了コード（使える結果 0 / 読めない結果 3 / それ以外 1）と、判定の 0 / 2 / 7 / 8 の意味は変わらない
- 結果ファイルが JSON オブジェクトとして読めるなら、監視の結末が何であっても使える結果として扱う
- 理由の語彙と起動し直しの可否の表を持つのは結末の共通層だけである

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 監視の標準出力・終了コード | 変えない | 変えない |
| 監視の結果ファイル・監視の記録の `reason` | `usage_limit` / `cli_timeout` の 2 値が増える | 追加のみ。読む側は値を集計するだけで一覧を持たない |
| 状態ファイルの `rounds[-1].<担当>` | `monitor_detail` の鍵が増える（結果なしのときだけ）。`no_result_reason` の値が 10 種類になる | 追加のみ。既存の状態ファイルはそのまま読める |
| 結末の共通層の関数 | `read_launch_outcome` / `relaunch_same_agent` / `LaunchOutcome` / `NO_RELAUNCH_REASONS` を新設 | 追加のみ。既存の `read_outcome` / `reason_for` / `REASONS` の呼び出し側は変わらない |
| 起動の手順の引数・pid ファイル | 変えない | 変えない |

## 修正対象

```text
plugins/ndf/scripts/lib/monitor_outcome.py
plugins/ndf/scripts/lib/monitor.py
plugins/ndf/scripts/lib/launch-cli.sh
plugins/ndf/scripts/lib/README.md
plugins/ndf/scripts/tests/test_monitor_outcome_unit.py
plugins/ndf/skills/cross-review/scripts/state.py
plugins/ndf/skills/cross-review/docs/01-state-and-review.md
plugins/ndf/skills/cross-review/docs/03-review-output.md
plugins/ndf/skills/cross-review/docs/04-contracts.md
plugins/ndf/skills/cross-review/tests/test_monitor_usage_limit.py        新設
plugins/ndf/skills/cross-review/tests/test_launch_cli_process_group.py   新設
plugins/ndf/skills/cross-review/tests/test_read_result_reason.py         新設
plugins/ndf/skills/cross-review/tests/test_judge_no_result_reason.py     新設
issues/issue-729-619-584-design.md                                       「未確認のまま残ること」の更新
plugins/ndf/dev.kiro/ / plugins/ndf/dev.agy/                             配布物の同期（生成物）
```

## タスク分解

共通層のタスク（1〜4）→ 読む側のタスク（5〜6）→ 文書と退行の確認（7〜8）の順に進める。各タスクは失敗するテスト → 通す最小実装 → 整理の順で行う。

### Task 1: 結末を 1 つの値として読む関数を共通層に置く

- **対象ファイル:** `plugins/ndf/scripts/lib/monitor_outcome.py`、`plugins/ndf/scripts/tests/test_monitor_outcome_unit.py`
- **変更内容:** 理由の語彙を 9 語にする（`REASONS` に `usage_limit` / `cli_timeout` / `unparsable`）。起動し直せない理由の集合 `NO_RELAUNCH_REASONS = frozenset({"usage_limit"})` と `relaunch_same_agent(reason)` を置く。`LaunchOutcome`（`payload` / `reason` / `detail` / `monitor` / `relaunch_same_agent`）と `read_launch_outcome(tmp_dir, stem, result_path=None)` を新設する。理由の決め方は設計文書の「結末を読む関数」の表のとおり。例外・`SystemExit`・標準出力/標準エラーへの出力を出さない。モジュールの冒頭の説明に読み取りの責務を足す
- **満たす受け入れ条件:** AC1、AC8〜AC11
- **進め方:** 監視の結果ファイル（各理由・壊れた JSON・無し）× 結果ファイル（オブジェクト・配列・壊れた JSON・空・無し）の組み合わせを一時ディレクトリに置く単体テストを先に書く。`capsys` で出力が空であることも見る

### Task 2: 監視が利用上限の文言を検知し、理由「利用上限」を結末に添える

- **対象ファイル:** `plugins/ndf/scripts/lib/monitor.py`、`plugins/ndf/skills/cross-review/tests/test_monitor_usage_limit.py`（新設）
- **変更内容:** `MonitorOutcome` に `reason: Optional[str] = None` を足し、`create(status, detail, reason=None)` にする。`USAGE_LIMIT_FATAL`（kiro の文言・claude の 429 の JSON・既存の `quota exceeded` / `rate limit exceeded`・`HTTP/x 429`）と `CLAUDE_STDOUT_USAGE_LIMIT` を新設し、`EARLY_ERROR_FATAL` から 429 と quota / rate limit の一致を外す（401 / 403 は残す）。致命の照合の前に利用上限の照合を置く（err.log は全担当、stdout.log は claude だけ JSON 向けの照合）。`_early_error_outcome` は利用上限に一致したとき `reason="usage_limit"` を添える。`_record_outcome` は `outcome.reason or reason_for(status)` で理由を書く。結末を作る既存の呼び出し 8 か所は変えない
- **満たす受け入れ条件:** AC2〜AC4、AC6、AC7
- **進め方:** 設計文書の「実測」の 10 行を入力にした照合の単体テストと、文言を 1 行書いた err.log / stdout.log と終わったプロセスの pid ファイルで `monitor_agent` を呼ぶテストを先に書く。既存の `test_monitor_outcome_file.py` と `test_monitor_early_error.py` は変えずに通す。**照合の順序（利用上限 → 致命 → 警告の見た目の致命）をテストで固定する**（設計文書の未確認 5）

### Task 3: CLI 自身の上限で結果を書かずに終わった担当を、理由「CLI の上限」にする

- **対象ファイル:** `plugins/ndf/scripts/lib/monitor.py`、`plugins/ndf/skills/cross-review/tests/test_monitor_usage_limit.py`
- **変更内容:** `CLI_TIMEOUT_AFTER_EXIT`（`print timeout after \S+ with turn in progress`）を新設する。`_process_exit_outcome` で、終了して結果ファイルが無いときだけ err.log を照合し、一致すれば `reason="cli_timeout"` を添える。結果ファイルがあれば従来どおり `OK`
- **満たす受け入れ条件:** AC5
- **進め方:** 結果ファイルあり・なしの 2 通りのテストを先に書く

### Task 4: CLI を独立したプロセスグループで起動し、止めるときはグループへ送る

- **対象ファイル:** `plugins/ndf/scripts/lib/launch-cli.sh`、`plugins/ndf/scripts/lib/monitor.py`、`plugins/ndf/skills/cross-review/tests/test_launch_cli_process_group.py`（新設）
- **変更内容:** 起動の手順の `launch_runtime` を呼ぶ前に `set -m` を有効にし、背景起動した CLI の pid がプロセスグループの番号になるようにする（起動の直後に `set +m` で戻す）。`_kill_pid` は `os.getpgid(pid) == pid` かつ `!= os.getpgrp()` のとき `os.killpg` で SIGTERM → 3 秒 → SIGKILL を送り、それ以外は従来どおり pid だけへ送る。生存の確認はグループの先頭の pid で見る
- **満たす受け入れ条件:** AC19〜AC21
- **進め方:** 3 秒後に子プロセスが結果ファイルを書く偽の CLI（`bash -c`）を PATH に置いて起動の手順から起動し、`os.getpgid(pid) == pid` を確かめるテスト、監視の上限 2 秒で止めた後 4 秒待っても結果ファイルが無いテスト、先頭でない pid で `os.killpg` が呼ばれないことを `mock` で見るテストを先に書く。**bash 3.2 の互換は、`set -m` が bash 2 系から存在する組み込みであることを `man bash` / 変更履歴で確かめ、設計文書の未確認 3 へ書く**（実機での確認は未確認のまま残す）

### Task 5: cross-review の結果の取り込みが共通の値を読み、理由と監視の詳細を残す

- **対象ファイル:** `plugins/ndf/skills/cross-review/scripts/state.py`、`plugins/ndf/skills/cross-review/tests/test_read_result_reason.py`（新設）
- **変更内容:** 共通層の `monitor_outcome` を読み込む。`_read_review_result_file` は結果ファイルを自前で開かず `read_launch_outcome(tmp_dir, f"{agent}-review-pr{pr}", rfile)` を呼び、`payload` が無ければ `reason` を `no_result_reason` として記録して従来の終了コード（`unparsable` は 3、それ以外は 1）で止める。`_record_no_result` に `monitor_detail`（監視の結果ファイルがあるときだけ鍵を書く。空文字は書かない）を足す。`no_verdict` / `not_posted` の上書きは従来どおり
- **満たす受け入れ条件:** AC13、AC12（`plugins/ndf/skills/` に `usage_limit` の条件分岐を書かない）
- **進め方:** 結果ファイル無し + 監視の結果ファイル（各理由）/ 無しで `read-result` を呼び、状態ファイルの `no_result_reason` と `monitor_detail` と終了コードを見るテストを先に書く。既存の `test_state_read_result.py` / `test_state_no_result.py` は変えずに通す。**stem の突き合わせ**（設計文書の未確認 6）は、`launch-reviewer.sh` の `STEM` と監視の `DEFAULT_STEM_TEMPLATE` と取り込みの stem が同じ形であることをテストで固定する

### Task 6: 判定が理由を出し、起動し直せない理由があれば止める。報告の表に理由を出す

- **対象ファイル:** `plugins/ndf/skills/cross-review/scripts/state.py`、`plugins/ndf/skills/cross-review/tests/test_judge_no_result_reason.py`（新設）
- **変更内容:** `_handle_no_result_round` は結果なしの担当の理由を集めて標準出力に `NO_RESULT_REASONS='<担当>=<理由> ...'` を出す。理由に `relaunch_same_agent` が偽のものがあれば、起動し直さずに `final=error` として終了コード 1 で止め、標準エラーに担当・理由・`monitor_detail` を出す。すべて起動し直してよい理由なら従来どおり 7 / 2 度目は 1。`_print_round_summary` は結果なしの担当を `<担当>=NO_RESULT(<理由>)` の形で出す
- **満たす受け入れ条件:** AC14〜AC17、AC24
- **進め方:** 状態ファイルを作って `judge` と `report` を呼ぶ。`usage_limit` を含む / 含まない / 2 度目の 3 通りを先に書く。`SKILL.md` の骨組みの行（`bg-wait.sh run` から `case $JUDGE_RC` まで）は `git diff` で差が無いことを確かめる

### Task 7: 文書を更新する

- **対象ファイル:** `plugins/ndf/skills/cross-review/docs/01-state-and-review.md`、`docs/03-review-output.md`、`docs/04-contracts.md`、`plugins/ndf/scripts/lib/README.md`、`issues/issue-729-619-584-design.md`
- **変更内容:** 理由の表を 10 語にする（`missing` / `unparsable` / `no_verdict` / `not_posted` / `timeout` / `stalled` / `early_error` / `usage_limit` / `cli_timeout` / `pidfile_bad`）。「monitor.py が誤って kill する場合の手順」に、上限に当たった場合の見分け方（`reason` と `monitor-outcomes.jsonl` の読み方）を足す。契約の文書に `monitor_detail` の鍵と `reason` の 2 値を足す。共通層の一覧の `monitor_outcome.py` の行に読み取りの責務を足す。設計文書の「未確認のまま残ること」の 1・3・5・6 を、実装で決めた結果へ更新する
- **満たす受け入れ条件:** AC18
- **進め方:** テスト駆動を適用しない（文書）。`grep` で 10 語と「見分け方」の節を確かめる。`python3 scripts/check-doc-line-limit.py` を通す

### Task 8: 退行の確認と配布物の同期

- **対象ファイル:** `plugins/ndf/dev.kiro/` `plugins/ndf/dev.agy/` の生成物
- **変更内容:** `bash scripts/build-runtime-plugins.sh` で生成物を揃える。全テスト・定義の検査を通す。#619 と #584 の再現を手動で確かめる（要求文書の「検証手段」）
- **満たす受け入れ条件:** AC22、AC23
- **進め方:** テスト駆動を適用しない（検証）。コマンドの終了コードを記録し、Pull Request 本文へ載せる

## 影響範囲

- 監視を使う 2 つの Skill（cross-review / cross-refactoring）。cross-refactoring は監視の結果ファイルの `reason` に 2 値が増えるだけで、終了コードの分岐は変わらない
- 実行の要約（`run_metrics.py`）は `reason` の値を集計するだけで、語彙の一覧を持たないため変更なし
- 担当の CLI のプロセスが独立したプロセスグループで動く。起動の手順を呼ぶ側（`launch-reviewer.sh`、cross-refactoring の `launch-*.sh`）の引数は変わらない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `state.py` は 4470 行の 1 ファイルで、G1（#727）も同じファイルの別の節を触る | タスクごとにテストを通す。触る関数を `_read_review_result_file` / `_record_no_result` / `_handle_no_result_round` / `_print_round_summary` の 4 つに限る。競合は後からマージする側が解く |
| `docs/01-state-and-review.md` は 497 行で、行数の上限（500）に近い。理由の表に 7 行足すと超える | 同じ文書の既存の記述を詰め、501 行以上にしない。詰められなければ理由の表を契約の文書（`04-contracts.md`）へ移し、元の場所からリンクする |
| 利用上限の文言の照合を致命の照合の前に置くため、既存の 429 / quota の一致の `reason` が変わる | 既存テストは `_scan_early_fatal` の返り値だけを見ており、利用上限の照合を先に置いても `_scan_early_fatal` 単体の挙動は変えない。理由の変化は新しいテストで固定する |
| `set -m` を有効にすると、bash がジョブの終了を標準エラーへ出す・起動元がジョブ制御の影響を受ける | `launch_runtime` の直前で有効にし、直後に `set +m` で戻す。既存の `test_lib_launch_cli_runtimes.py` / `test_launch_cli_guards.py` で標準エラーの形が変わらないことを見る |
| 監視の環境変数（`MONITOR_*`）を export したシェルでは既存テストが落ちる（#678、G6） | export していないシェルでテストを実行する |

## 切り戻し手順

- すべてコードと文書の変更で、データ移行は無い。Pull Request の revert で戻せる
- 状態ファイルの `monitor_detail` は追加の鍵で、戻した後の読み手は無視する。監視の結果ファイルの `usage_limit` / `cli_timeout` は戻した後の `reason_for` の一覧に無いが、読む側は値を集計するだけで一覧を照合しない

## 完了の定義

- [ ] AC1〜AC24 をすべて満たし、条件ごとに検証手段と結果が対応している（Pull Request 本文の表）
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る（export していないシェル）
- [ ] `bash scripts/build-runtime-plugins.sh --check`、`python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .` が終了コード 0
- [ ] `git grep -n 'usage_limit' -- plugins/ndf/skills` の一致行が文書・テスト・テンプレートの文言だけである（AC12）
- [ ] 設計文書の「未確認のまま残ること」の 1・3・5・6 が、実装で決めた結果へ更新されている
