# 全体テストの所要: 確かめる振る舞いを弱めずに縮める手段と、テストを消したときの照合

## 概要

このリポジトリの全体テスト（`.ndf/project.json` の `test.suites` の全部）は、手元で 180 秒以内に終わる大きさを目標にする。
所要は、確かめる振る舞いを弱めない手段で縮め、守る振る舞いを別のテストや suite が確かめているテストは消す。
消したテストは 1 件ごとに、当たった基準と、その振る舞いを今後どこが守るかを残し、計測のスクリプトで着手前後の
テストの集合と照らす。

**削る基準と速くする手段の正本は [docs/plugin-development-guide.md](../plugin-development-guide.md) の「テストを削る基準」である。**
この文書は、その手段をこのリポジトリへ当てた形と、届いた値と、当てなかった手段の理由を持つ。

## 用語

語の定義は用語集（ [docs/glossary.md](../glossary.md) ）が正である。この文書で使う主な語は次のとおり。

| 用語 | この文書での使い方 |
| --- | --- |
| 全体テスト | `.ndf/project.json` の `test.suites` の全部（pytest と、構造・リンク・秘密などの検査の suite） |
| 消したテスト | 着手前に走っていて、着手後の結果（走った・skip・deselect）のどれにも無く、テストの台帳に載るテスト |
| テストの台帳 | 消した・まとめた・置き換えたテストと、遅いテストの処置を 1 件ずつ記録した文書 |

## 背景

| 決めたこと | 理由 |
| --- | --- |
| 手段ごとの見込みの秒を先に出し、見込みの大きい順に入れ、尽きたら届いた値と残りの差を示して止める | 届かない目標へ手段を足し続けないため。残りの時間は、本物のスクリプトと子プロセスを起こして実時間で待つ結合テストにあり、書き直せるかはファイルごとに読むまで分からない |
| 並列の数（`-n 4`）と宣言の pytest のコマンドは変えない | 着手前後を同じ条件で比べるため。並列を上げる・テストを既定の実行から外すことは目標の達成として数えない |
| 偽の HTTP サーバーのポーリングの間隔をテストヘルパーの側で縮める | 標準ライブラリの既定の間隔 0.5 秒のため、後始末の `shutdown()` が 1 件ごとに 0.5 秒待つ。テストの対象のコードを変えずに縮まる |
| 永続ワークフローの問い合わせの間隔は、プロセスの中で走るテストでだけ、テストの側で差し替える。上限の宣言は変えない | 上限の宣言を読むモジュールは上書きの環境変数を持たない。テストのために環境変数を足すと、利用者が触れる設定が増える |
| テストの対象のスクリプトの起動を、利用者から見える振る舞いを変えずに縮めることだけは、テストの対象のコードの変更として許す | 縮めた秒が、テストの子プロセスの起動だけでなく、工程が打つ 1 回ごとの起動にも効くため |
| 計測のスクリプトは実験版に置く | 呼んだときだけ働く補助であり、既定の振る舞いから参照しない |

## 仕様

### 当てた速くする手段

| 手段 | 当てた形 |
| --- | --- |
| テストヘルパーの固定の待ちを縮める | 偽の HTTP サーバーを `serve_forever(poll_interval=0.01)` で回す（`plugins/ndf/scripts/tests/account_fake.py`・`test_wait_notify.py`・`test_training_optout.py`・`test_claude_api_keys.py`・`test_lib_notify.py`・`plugins/playwright-kit/skills/playwright-authoring/tests/test_app_ready.py`） |
| プロセスの中で走るテストの待ちの間隔を差し替える | `plugins/ndf/scripts/tests/conftest.py` の自動のフィクスチャ `_short_durable_polls` が、読み込み済みの `durable` の `POLL_SECONDS` と `wait`・`wait_from` の既定の間隔を宣言の 1/10（0.02 秒・0.1 秒）にする。`contracts/limits.json` は書き換えない。子プロセスで起こした supervise には効かない |
| テストの対象のスクリプトの起動を縮める | `plugins/ndf/scripts/lib/md.py`（`markdown_it`）・`mdtable.py`（`tabulate`）・`locks.py`（`filelock`）は、サードパーティのライブラリを使う関数の中で import する。引数・標準出力・結果 JSON・終了コード・書くファイルは変わらない |

### 起動を縮めないスクリプト

| スクリプト | 起動の大半 | 縮めない理由 |
| --- | --- | --- |
| `parallel.py` | 永続ワークフローのライブラリ（`dbos`）。`lib/parallel_route.py` が import の時に手順を登録する | 登録を遅らせると、ワークフローの起動と再開の前に手順が登録済みである前提が崩れ、再開の振る舞いが変わりうる |
| `release-steps.py`・`test-run.py`（と `supervise.py`・`sprint-state.py` の残り） | `pydantic`。`lib/schema.py` の `BaseModel` を宣言の形のクラスが import の時に継ぐ | クラスの定義が import の時に要る。遅らせるにはモジュールの分け方を変える必要がある |

### 消したテスト

基準 6（suite・CI のジョブが既に走らせる検査を、テストの中でもう一度走らせる）に当たる 7 件を消した。

| 消したテスト | 同じ検査を流すもの |
| --- | --- |
| `scripts/tests/test_check_ndf_contracts.py::test_this_repository_matches_its_contracts` | suite `ndf-contracts` と CI の `script-structure.yml` |
| `scripts/tests/test_doc_line_limit.py::test_the_repository_satisfies_the_limit` | suite `doc-line-limit-check` |
| `scripts/tests/test_check_repo_secrets.py::test_this_repository_passes` | suite `repo-secrets-check` |
| `scripts/tests/test_check_skill_repo_assumptions.py::test_real_repository_passes`・`test_default_scan_covers_the_real_repository` | suite `skill-repo-assumptions-check` |
| `scripts/tests/test_cross_skill_refs.py::test_the_check_passes_on_the_repository` | suite `runtime-plugin-validate`（`check-cross-skill-refs.py --root`） |
| `scripts/tests/test_doc_staleness.py::test_real_repository_passes` | suite `runtime-plugin-validate`（`check-doc-staleness.py --root`） |

リポジトリ全体が検査を通ることは suite が確かめる。各検査のスクリプトの判定の分岐は、残るテストが一時の木で確かめる
（[ndf-entrypoint-contracts.md](ndf-entrypoint-contracts.md)・[ndf-repo-assumptions-check.md](ndf-repo-assumptions-check.md) のテスト観点）。

### 常に成り立つ条件

| 条件 | 破れたときの扱い |
| --- | --- |
| 着手前に既定の実行で走っていたテストは、着手後も走るか、台帳に処置・基準の番号・守るテスト（または守らなくてよい理由）つきで載る | 計測のスクリプトの `diff` が終了コード 1 で、載っていないテストを出す |
| 着手前に走っていたテストが、着手後に skip・deselect に変わらない。消したテストは、着手後の木に定義が残っていない | 同上 |
| 判定に使う計測は、開始時の 1 分の load average が CPU の数（8）以下である | その計測は参考の値として残し、判定に数えない |
| 起動を縮めたスクリプトは、同じ引数に対して同じ標準出力・結果 JSON・終了コードを返し、同じファイルを書く | 既存のテストが落ちる。落ちたらそのスクリプトの短縮を戻す |
| 台帳の基準の番号と使った手段は、すべて開発ガイドの表と一覧にある | 表に無い番号・手段は、開発ガイドへ足すか、使うのをやめる |

### 計測のスクリプト（実験版）

計測は実験版の `plugins/ndf/scripts/experimental/whole-test-timing.py` で行う（台帳は [docs/ndf-experiments.md](../ndf-experiments.md) ）。

| 副命令 | 中身 | 終了コード |
| --- | --- | --- |
| `measure` | 宣言のコマンドで全体テストを流し、件数・各テストの所要の合計・壁時計・遅いテストの上位 30 件・ファイルの上位 10 本と、開始時の load average を記録する。環境変数 `PYTEST_PLUGINS` で読み込ませた pytest のプラグインが、集められたテストと実行に残ったテストの差を deselect として記録する。プラグインはテストを流す前に自分の環境変数を外し、suite の作業ディレクトリと一致するセッションだけを記録する（テストの中で起動する入れ子の pytest を記録に混ぜない） | 全 suite の結果 |
| `diff` | 着手前後の記録を台帳と照らす。台帳に無い消えたテスト（`missing`）・skip に変わったテスト（`now_skipped`）・deselect に変わったテスト（`now_deselected`。台帳に載っていても出す）・定義が残る消したテスト（`still_defined`。着手後の木のファイルを AST で読む）を出す | 1: どれかが 1 件以上 |
| `startup --root <dir> [--runs N] <スクリプト>... [-- <引数>...]` | スクリプトの最小の起動（既定は `--help`）を N 回（既定 10）流し、中央値・最小・最大のミリ秒と終了コードを出す | 0: すべて起動できた / 1: 終了コードが回ごとに違った / 2: スクリプトが無い |

`collect_ignore` などの収集の段で外す書き方は deselect の照合で見つからないため、`still_defined` の照合で見つける。

## 運用

### 届いた値

2026-11-10 にこの開発コンテナ（CPU 8 個・メモリ 30 GB、pytest の `-n 4`）で測った。同じマシンでほかのテストが
並んで動き、途中の負荷は 10〜13 まで上がったため、値には負荷の差が入っている。

| 計測 | 開始時の load average | 壁時計 | うち pytest | 件数 | 各テストの所要の合計 |
| --- | --- | --- | --- | --- | --- |
| 着手前 | 4.01 | 1129.0 秒 | 1064.8 秒 | 12,994 | 4,048.6 秒 |
| 着手後（判定に使った 3 回） | 2.25・3.16・7.46 | 806.4・918.8・1014.8 秒 | 760.2・859.8・965.1 秒 | 12,917 | 2,809.7・3,215.5・3,677.0 秒 |

180 秒には届いていない。判定に使った 3 回の壁時計と 180 秒の差は 626〜835 秒である。子プロセスを起こすテストを
プロセスの中の呼び出しへ置き換える手段は当てていない。残りの時間は、supervise の queue と再開・中継・cross-review と
cross-refactoring の駆動・worktree のテスト環境の結合テストにある。

起動の秒（`--help` を 10 回流した中央値。開始時の load average 9.73 と 10.7 で、参考の値）は、`supervise.py` 409.7 → 328.6 ms・
`sprint-state.py` 302.9 → 228.8 ms・`glossary.py` 162.2 → 114.2 ms だった。

計測の記録と台帳は [issues/old/issue-2309-test-ledger.md](../../issues/old/issue-2309-test-ledger.md) と
`issues/old/issue-2309-timing/` にある。

### テストを書く・消すとき

- 遅いテストは、開発ガイドの 6 つの手段のどれかで、確かめる振る舞いを弱めずに速くする
- 消す・まとめるときは、1 件ごとに基準の番号と守るテストを Pull Request から追える形で残す。落ちるから消すのは基準に当たらない
- 消した前後は `whole-test-timing.py diff` で照らし、終了コード 0 を確かめる

## テスト観点

- 偽の HTTP サーバーを使うテストの後始末が 0.1 秒以内で終わること（間隔を既定へ戻すと 0.5 秒になる）
- 待ちの間隔を差し替えたテストが、差し替える前と同じ結果を確かめて通り、上限の宣言のファイルに差分が出ないこと
- 起動を縮めたスクリプトの既存のテストが、変更なしで通ること
- `diff` が、台帳に無い消えたテスト・skip や deselect に変わったテスト・定義が残る消したテストで終了コード 1 を返すこと
- `startup` が N 回の中央値・最小・最大と終了コードを返し、終了コードが回ごとに違えば 1 を返すこと
- 並列の数と宣言の pytest のコマンド（`.ndf/project.json`）が変わらないこと

## 関連リンク

- 要求: https://github.com/devbasex/ai-plugins/issues/2309 （意味の薄いテストを削る基準は https://github.com/devbasex/ai-plugins/issues/2204 ）
- 削る基準と速くする手段: [docs/plugin-development-guide.md](../plugin-development-guide.md) の「テストを削る基準」
- 全体テストの実測から範囲テストの上限を決める仕様: [ndf-supervise-test-limits-from-whole-runs.md](ndf-supervise-test-limits-from-whole-runs.md)
