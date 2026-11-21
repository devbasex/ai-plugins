# #3370 #3691: 依存の更新の仕組み（Renovate・7 日の規則・脆弱性の知らせ・Actions の SHA・外部 CLI の下限）（実装計画）

## 関連リンク

- 要求仕様: [issue-3370-requirements.md](./issue-3370-requirements.md)（正は #3370 の本文）、[issue-3691-requirements.md](./issue-3691-requirements.md)（正は #3691 の本文）。受け入れ条件は `<課題>:<条件>` の番号で指す
- 設計書: [issue-3370-3691-design.md](./issue-3370-3691-design.md)、詳細設計書: [issue-3370-3691-design-detail.md](./issue-3370-3691-design-detail.md)、決定の記録: [issue-3370-3691-design-decisions.md](./issue-3370-3691-design-decisions.md)
- #3380（設計レビュー P）: 設計書の「設計の結果」で「閉じる」。行 3380-1・2・3・4・6・8・9 は #3691 の受け入れ条件が持つので、この計画にタスクを持たない（下の「完了の定義」で閉じる）
- モード: `standard`（設計書の「影響と危険」の判定。外部 CLI の下限で公開インタフェースの終了コードと理由の文が増える機能追加で、課題 2・触るファイル 40 を超える）

## 修正対象

詳細設計書の「地図」と、構成要素ごとの関数の仕様から引き写す。lock は上げる先が実装の時点で決まる（決定 7）。上げたバージョンで落ちるコードとテストの直し（3370:11）は触るファイルが実装の時点で決まり、Task 3 の PR 本文に書く。

| ファイル | 変える中身 |
| --- | --- |
| `.github/renovate.json`（新設） | 詳細設計書の「Renovate の設定」の定数と規則 1〜12、`customManagers` 2 つ |
| `.github/renovate-version`（新設） | 実装の時点で公開から 7 日たった 44 系の版の 1 行 |
| `.github/dependabot.yml` | `github-actions` の項目に `cooldown: { default-days: 7 }`、頭の注記を Renovate との分担へ |
| `.github/workflows/renovate.yml`（新設） | 詳細設計書の「Renovate を動かすワークフロー」の表（起動・検証の実行の前提・排他・token・版の検証・全体の設定・検証用の全体の設定・PR の無い失敗の知らせ・有効にする時機） |
| `.github/workflows/renovate-gate.yml`（新設） | 詳細設計書の「依存の PR の関門」の表（`pull_request_target`・`status`・`merge_group`、commit status `ndf/renovate-gate`） |
| `.github/workflows/deps-weekly.yml`（新設） | ジョブ `supply-chain-report`（`report`）と `renovate-config`（版の検証・`age --renovate-version`・`renovate-config-validator --strict`） |
| `.github/workflows/deps-affected-comment.yml`（新設） | `workflow_run` で artifact `deps-affected` を既定ブランチのコードの `post-affected` で投稿する |
| `.github/workflows/script-structure.yml` | `uv lock --check` をプロジェクトごとの出力（`lock_root`・`lock_ndf`・`lock_playwright`・`lock_serena`）で起動。`age`・`action-pins`・`affected` のステップを足す |
| `.github/workflows/runtime-plugin-smoke-latest.yml` | `main` の 3 ファイルの sparse の checkout、入力 `floors_ref`、`Check CLI --help against the floors table`・`Check gh --help against the floors table` の 2 ステップ |
| `.github/workflows/` のほかの 10 本（`glossary.yml`・`lint.yml`・`ndf-release-reusable.yml`・`pr-base-guard.yml`・`pytest.yml`・`release-cd.yml`・`runtime-plugin-authenticated-smoke.yml`・`runtime-plugin-smoke.yml`・`runtime-plugin-validate.yml` と上の `script-structure.yml`・`runtime-plugin-smoke-latest.yml`） | 外部の Action の `uses:` を `@<40 桁の SHA> # v<具体的な版>` へ |
| `scripts/plugin-deps.py`（新設） | `main`・`targets`・`read_holds`・`report`・`lock_diff`・`affected`・`post_affected`・`age`・`action_pins`・`renovate_gate`・`renovate_log`・`cli_help`・`uv_pin` |
| `scripts/tests/test_plugin_deps.py`（新設） | 設定の検査（I3・I5・I6・I7・I9・I10）と各サブコマンドの単体テスト（詳細設計書の「単体テストの観点」） |
| `scripts/required-checks-allow.json` | `not_required` に `renovate-config`・一覧の投稿のジョブ、`statuses` に `renovate/artifacts` と `ndf/renovate-gate`（出元 `renovate-gate.yml`）、`awaiting` に `ndf/renovate-gate` |
| `.ndf/project.json` | `test.ci_exempt` に新しいワークフロー 4 本の行 |
| `pyproject.toml`・`uv.lock` | `[tool.uv] exclude-newer = "7 days"`、直接の依存を上げる先へ、lock の作り直し |
| `plugins/ndf/pyproject.toml`・`plugins/ndf/uv.lock` | 同上（`dbos`・`httpx`・`sqlalchemy` の行は変えない）、頭の注記を版の選び方と上げる操作の 3 行へ |
| `plugins/playwright-kit/skills/playwright-kit-ops/pyproject.toml`・`uv.lock` | 同上 |
| `plugins/mcp/mcp-serena/pyproject.toml`・`uv.lock` | 同上 |
| `plugins/ndf/skills/release/assets/slides/.npmrc`（新設）・`package.json`・`package-lock.json` | `min-release-age=7`、上げる先へ、lock の作り直し |
| `plugins/ndf/bin/ndf`・`scripts/lib/ndf_wrappers.py` | `UV_VERSION` と `UV_INSTALLER_SHA256` を `uv-pin` で 2 か所そろえる（値を変えるのは Renovate の PR。この計画では今の値のまま組の一致を縛る） |
| `plugins/ndf/contracts/domains/cli-floors.json`（新設） | `gh`・`claude`・`codex` の `executable`・`version_args`・`version_pattern`・`min_version`・`help` |
| `plugins/ndf/src/ndf/adapters/cli_floor.py`（新設） | `require`・`TooOld` |
| `plugins/ndf/src/ndf/adapters/agents.py`・`claude.py`・`github.py`・`cli.py`、`plugins/ndf/src/ndf/domains/project/measure_ci.py`、`plugins/ndf/src/ndf/domains/relay/wrapper.py`・`cli.py`・`account_login.py`・`cli_account.py` | `_guarded_run`・`call_claude`・`claude_plugin`・`token`・`raw`・`Wrapper` の起動・`_exec_claude`・`_claude`・`_auth`・`launch_args`・`_acc_remove` が起動の前に `require` を呼ぶ（シグネチャは変えない。変える前と後は詳細設計書の「外部 CLI の下限」の表） |
| `plugins/ndf/tests/adapters/test_cli_floor.py`（新設） | 比べの境界と、起動の箇所がすべて比べを持つこと |

## タスク分解

機能追加なので、最初のタスクは今の振る舞いを縛る退行テストにする（Task 1。外部 CLI の側は Task 14 の頭）。テストは CI で走らせる（`.ndf/project.json` の `test.ci.only: true`。合否は単位の PR の `gh pr checks` で読む）。

### Task 1: 今の lock の一致の検査を縛る退行テスト

- **対象ファイル:** `scripts/tests/test_plugin_deps.py`（新設）
- **変更内容:** `script-structure.yml` が `uv lock --check --project plugins/ndf` を持ち、merge queue と push で打つことを縛るテストを先に置く。Task 2 でプロジェクトごとに広げる土台にする
- **満たす受け入れ条件:** 3370:10（今の振る舞いの保持）
- **進め方:** 今の `script-structure.yml` で通るテスト → 検査を 1 行外すと落ちることを確かめる（退行テストなので失敗から始めない）

### Task 2: 4 つの uv のプロジェクトに 7 日の規則とプロジェクトごとの一致の検査

- **対象ファイル:** 4 つの `pyproject.toml`・`uv.lock`、`.github/workflows/script-structure.yml`、`scripts/tests/test_plugin_deps.py`
- **変更内容:** `[tool.uv] exclude-newer = "7 days"` を足して lock に `exclude-newer-span = "P7D"` を入れる。`scope` のステップにプロジェクトごとの出力を足し、各 `uv lock --check` をその出力で起動する（playwright-kit の入力が変わったら根も。merge queue と push では 4 つとも）
- **満たす受け入れ条件:** 3370:5・3370:10、3691:5（7 日の設定と `uv lock --check`）
- **進め方:** 失敗するテスト（4 つのどれかの検査か `exclude-newer` が無いと落ちる）→ 設定 → 整理。単位の PR で、4 つの定義のそれぞれだけを書き換えた検証のコミットで対応する検査が落ちることを確かめ、そのコミットは戻す

### Task 3: 最初のバージョンアップと上げたバージョンでの直し

- **対象ファイル:** 4 つの `pyproject.toml`・`uv.lock`、スライドの `.npmrc`（新設）・`package.json`・`package-lock.json`、上げたバージョンで落ちるコードとテスト（実装の時点で決まる）
- **変更内容:** 詳細設計書の「依存の定義と lock と一致の検査」の手順 1〜5 のとおり、`==` の宣言を公開から 7 日たった上げる先へ書き換え、`uv lock --upgrade` で作り直す（playwright-kit の後に根）。`dbos==3.4.0`・`httpx==0.28.1`・`sqlalchemy` は変えない。スライドは `.npmrc` に `min-release-age=7` を置いて npm の lock を作り直す。バージョンが変わった依存の一覧と、戻した依存・直した箇所と理由を PR 本文に書く
- **満たす受け入れ条件:** 3370:4・3370:11、3691:5（上げる先にそろう）
- **進め方:** 宣言と lock の書き換え → 単位の PR の CI で落ちたテストを失敗するテストとして直す → 整理。上げる先の版そのものはテストで縛らない（決定 7。版は実装の時点で決まる）

### Task 4: `plugins/ndf/pyproject.toml` の頭の注記

- **対象ファイル:** `plugins/ndf/pyproject.toml`、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 作り直しの間の注記（`ndf-rebuild`・`11.0.0-dev.N`）を、版の選び方（決定 106 と 7 日）と上げる操作（依存の一覧の Issue と `.github/renovate.json`）の 3 行へ直す
- **満たす受け入れ条件:** 3370:12
- **進め方:** 失敗するテスト（注記の指すファイルが無いと落ちる。文言は照らさない）→ 注記 → 整理。`.github/renovate.json` は Task 5 で入るので、テストは Task 5 と同じ単位の中で通す

### Task 5: Renovate の設定と固定の欄・7 日の一致

- **対象ファイル:** `.github/renovate.json`（新設）、`.github/renovate-version`（新設）、`.github/dependabot.yml`（`cooldown` と頭の注記）、`scripts/plugin-deps.py`（新設。`main`・`targets`・`read_holds`）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 詳細設計書の「Renovate の設定」の定数と規則 1〜9・11・12 を書く。`targets` は追跡している lock から `ignorePaths` を除いた種類とディレクトリの組を返し、`read_holds` は固定と対象の外の規則の 5 つの欄を読む。`dependencyDashboardHeader` の固定の一覧が規則と同じことを縛る
- **満たす受け入れ条件:** 3370:6・3370:8、3691:1（対象の lock の一覧）、3691:6 のうち固定の欄（知らせは Task 10）
- **進め方:** 失敗するテスト（I3・I5 の設定の部分・I6・I7・I9、`targets` と `read_holds` の単体）→ 設定とスクリプト → 整理

### Task 6: `UV_VERSION` の 2 か所を拾う規則と `uv-pin`

- **対象ファイル:** `.github/renovate.json`（規則 10・1 つ目の `customManagers`）、`scripts/plugin-deps.py`（`uv_pin`）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** `astral-sh/uv` を `github-releases` で拾い、更新の後に `uv-pin` がインストーラの sha256 を 2 か所へ書き直す。2 か所の版とハッシュの組の一致を縛る
- **満たす受け入れ条件:** 3691:1（コードの中の固定の行）
- **進め方:** 失敗するテスト（記録したインストーラの本文・取得 1 回・取得の失敗で書き換えない・片方だけ変えると落ちる）→ 実装 → 整理

### Task 7: 公開から 7 日の検査（`age`）と Renovate の設定の検証

- **対象ファイル:** `scripts/plugin-deps.py`（`age`）、`.github/workflows/script-structure.yml`（`age` のステップ）、`.github/workflows/deps-weekly.yml`（新設。`renovate-config` のジョブ）、`scripts/required-checks-allow.json`（`not_required` に `renovate-config`）、`.ndf/project.json`（`deps-weekly.yml` の `ci_exempt`）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 差分で入った lock の版・`UV_VERSION`・Renovate の版の公開日を読み、7 日に満たないか公開日が読めないものを拒む（規則 12 の組だけ通す）。`renovate-config` は宛先の先頭の検証器で版を確かめてから `renovate-config-validator --strict` を打つ
- **満たす受け入れ条件:** 3370:6（Dashboard のチェックで待つ日数を飛ばした版を拒む側）
- **進め方:** 失敗するテスト（6 日と 7 日の境・公開日の無い版・例外の組と違う版）→ 実装 → 整理

### Task 8: Renovate を動かすワークフローと PR の無い失敗の知らせ

- **対象ファイル:** `.github/workflows/renovate.yml`（新設）、`scripts/plugin-deps.py`（`renovate_log`）、`.ndf/project.json`（`renovate.yml` の `ci_exempt`）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 詳細設計書の「Renovate を動かすワークフロー」の表のとおり。`if: vars.RENOVATE_APP_CLIENT_ID != ''` で App を置くまで飛ばす。`renovate_log` は記録から PR もブランチも作られなかった失敗を要約に並べて失敗で終える
- **満たす受け入れ条件:** 3370:7
- **進め方:** 失敗するテスト（起動が `schedule` と `workflow_dispatch` だけ・`verify/` の入力の検証・`renovate_log` の記録の入力）→ 実装 → 整理。実走の確かめ（詳細設計書の「受け入れ条件 9 と 7 の確かめ」の 5〜6）は App を置いた後（下の「リスクと対処」）

### Task 9: 依存の PR の関門（`renovate-gate`）

- **対象ファイル:** `.github/workflows/renovate-gate.yml`（新設）、`scripts/plugin-deps.py`（`renovate_gate`）、`scripts/required-checks-allow.json`（`statuses` に `renovate/artifacts`・`ndf/renovate-gate`、`awaiting` に `ndf/renovate-gate`）、`.ndf/project.json`（`renovate-gate.yml` の `ci_exempt`）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 既定ブランチの定義と検証器で、今の head の最も新しい `renovate/artifacts` が App の bot の書いた `success` のときだけ `ndf/renovate-gate` を `success` にする。対象の外の PR は `gh api` で `success` を付けるだけ
- **満たす受け入れ条件:** 3370:9
- **進め方:** 失敗するテスト（赤・未報告・保留・古い head の成功・別の主体の成功を拒み、今の head の bot の成功だけ通す。`statusCheckWhen.artifactError` が `"always"`）→ 実装 → 整理。実走の確かめ（同じ節の 1〜4・7）は App を置いた後

### Task 10: 脆弱性と解いてよい固定の知らせ（`report`）

- **対象ファイル:** `scripts/plugin-deps.py`（`report`）、`.github/workflows/deps-weekly.yml`（`supply-chain-report` のジョブ）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 対象の lock の `uv audit`・`npm audit`、固定の相手の `requires_dist`、プレリリースを選べる依存を集め、目印と作成者で引いた 1 件の Issue を作る・書き直す・閉じる。取得が 1 つでも失敗したら Issue に触れない
- **満たす受け入れ条件:** 3691:6
- **進め方:** 失敗するテスト（詳細設計書の「処理の手順」の `report` の行と、単体テストの観点の `report` の行）→ 実装 → 整理

### Task 11: lock の差分から確かめるテストの一覧のデータを作る（`lock_diff`・`affected`）

- **対象ファイル:** `scripts/plugin-deps.py`（`lock_diff`・`affected`・配布名の対応表）、`.github/workflows/script-structure.yml`（`affected` のステップと artifact）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** `uv.lock` と `package-lock.json` の差分から、変わったパッケージ・使うモジュール・そのテスト（無ければ「テストが無い」）・推移的な依存の親を作る。4 つの `pyproject.toml` の直接の依存が対応表で引けることを縛る
- **満たす受け入れ条件:** 3691:2（一覧のデータ）
- **進め方:** 失敗するテスト（記録した lock の差分・推移的な依存だけ・読めない lock）→ 実装 → 整理。単位の PR で `python-dotenv` と `pptxgenjs` の差分を 1 つずつ流して一覧を見る

### Task 12: 一覧の投稿（`post_affected`）

- **対象ファイル:** `scripts/plugin-deps.py`（`post_affected`）、`.github/workflows/deps-affected-comment.yml`（新設）、`scripts/required-checks-allow.json`（`not_required` に一覧の投稿のジョブ）、`.ndf/project.json`（`deps-affected-comment.yml` の `ci_exempt`）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 既定ブランチのコードで artifact の PR 番号と head の SHA を確かめ、目印のコメントを作る・書き直す・消す
- **満たす受け入れ条件:** 3691:2（1 件のコメント・lock を変えない PR には出ない）
- **進め方:** 失敗するテスト（作る・書き直す・差分が無くなった PR で消す・今の head と違う SHA で書かない）→ 実装 → 整理

### Task 13: Actions を SHA で固定する

- **対象ファイル:** `.github/workflows/` の 11 本の `uses:`、`scripts/plugin-deps.py`（`action_pins`）、`.github/workflows/script-structure.yml`（`action-pins` のステップ）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 詳細設計書の「Dependabot の設定とワークフローの参照」のとおり、今のメジャーの中で公開から 7 日たった最新のリリースのタグが指すコミットへ書き換え、注記をその版にする。`action_pins` は差分で変わった行の注記のタグと SHA を比べる（Dependabot の `cooldown` は Task 5 で入る）
- **満たす受け入れ条件:** 3691:4
- **進め方:** 失敗するテスト（I10 の形・注記と違う SHA・注記つきのタグを辿る）→ 書き換えとスクリプト → 整理

### Task 14: 外部 CLI の下限の表と比べ（`cli_floor`）

- **対象ファイル:** `plugins/ndf/contracts/domains/cli-floors.json`（新設）、`plugins/ndf/src/ndf/adapters/cli_floor.py`（新設）、`plugins/ndf/tests/adapters/test_cli_floor.py`（新設）
- **変更内容:** 頭で、今の起動の箇所のテスト（`agents`・`claude`・`github`・`relay` を import するテスト）を変えずに通し続けることを退行の条件にする。`require` は起動と同じ `PATH` と `cwd` で実パスを解き、`--version` を 5 秒の期限で打って下限と比べ、実パスと `st_mtime_ns`・`st_size` で結果を再利用する。`gh`・`codex` の `min_version` は各 CLI の変更履歴で決め、決められなければ実装の時点の CI の版を置いて PR 本文に書く
- **満たす受け入れ条件:** 3691:3（下限の定義と比べ）
- **進め方:** 失敗するテスト（詳細設計書の「単体テストの観点」の `require` の行）→ 実装 → 整理

### Task 15: 起動の箇所に比べをつなぐ

- **対象ファイル:** `plugins/ndf/src/ndf/adapters/agents.py`・`claude.py`・`github.py`・`cli.py`、`plugins/ndf/src/ndf/domains/project/measure_ci.py`、`plugins/ndf/src/ndf/domains/relay/wrapper.py`・`cli.py`・`account_login.py`・`cli_account.py`、`plugins/ndf/tests/adapters/test_cli_floor.py`
- **変更内容:** 詳細設計書の「起動の箇所の変える前と後」の 9 行のとおり。`TooOld` はその箇所の今の「起動できない」の形で返す。`_acc_remove` は登録も symlink も残して止まる
- **満たす受け入れ条件:** 3691:3（古い CLI で理由を出して 0 以外で終わる・下限と同じ版で止まらない）
- **進め方:** 失敗するテスト（古い版を返す偽の CLI を `PATH` の先に置いた各エントリポイント・学習の設定で拒む経路で `--version` を打たない・比べを通らない起動の箇所を足すと落ちる）→ 実装 → 整理。単位の PR で `ndf launch-args` と cross-review の 1 周の `--version` の回数を数える

### Task 16: `--help` の照らし合わせ（`cli_help`）

- **対象ファイル:** `scripts/plugin-deps.py`（`cli_help`）、`.github/workflows/runtime-plugin-smoke-latest.yml`、`plugins/ndf/contracts/domains/cli-floors.json`（`help` の見本）、`scripts/tests/test_plugin_deps.py`
- **変更内容:** 詳細設計書の「`--help` の照らし合わせの置き場」のとおり。`main` の sparse の checkout の検証器で、スモークのイメージの中の CLI を 1 回の `docker run` で照らし、`gh` はホストで照らす。`src/ndf` が渡すサブコマンドとフラグがすべて見本にあることを縛る
- **満たす受け入れ条件:** 3691:3（`--help` の照らし合わせ）
- **進め方:** 失敗するテスト（詳細設計書の「単体テストの観点」の `cli_help` の行）→ 実装 → 整理

## レビューの単位

依存の向き（詳細設計書の「地図」の「依存の向き」）で分ける。単位 2 が `scripts/plugin-deps.py` の `main` と `targets` を作るので、単位 3〜7 と 9 は単位 2 のマージの後に始める。単位 8 は NDF の側だけで、単位 1 と並べて始めてよい。行数は `git diff --shortstat <ベース>...HEAD -- . ':(exclude)uv.lock' ':(exclude)**/uv.lock' ':(exclude)**/package-lock.json'` で数える。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 1. 7 日の規則と最初のバージョンアップ | Task 1・2・3 | 3370:4・3370:5・3370:10・3370:11、3691:5 | 約 250（数えない: 4 つの `uv.lock`・`package-lock.json`。直す箇所は実装の時点で決まる） | （実装の後に書き足す） |
| 2. Renovate の設定 | Task 4・5・6 | 3370:6・3370:8・3370:12、3691:1 | 約 600 | （実装の後に書き足す） |
| 3. 公開から 7 日の検査 | Task 7 | 3370:6 | 約 350 | （実装の後に書き足す） |
| 4. Renovate の起動と関門 | Task 8・9 | 3370:7・3370:9 | 約 650 | （実装の後に書き足す） |
| 5. 脆弱性の知らせ | Task 10 | 3691:6 | 約 550 | （実装の後に書き足す） |
| 6. 確かめるテストの一覧 | Task 11・12 | 3691:2 | 約 600 | （実装の後に書き足す） |
| 7. Actions の SHA の固定 | Task 13 | 3691:4 | 約 350 | （実装の後に書き足す） |
| 8. 外部 CLI の下限 | Task 14・15 | 3691:3 | 約 600 | （実装の後に書き足す） |
| 9. `--help` の照らし合わせ | Task 16 | 3691:3 | 約 300 | （実装の後に書き足す） |

単位 4 は単位 3 の後に始める（`renovate.yml` が `age --renovate-version` を打つ）。単位 9 は単位 2 と単位 8 の後に始める（`cli-floors.json` と `scripts/plugin-deps.py` の両方に積む）。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る既存のファイルで最も大きい `plugins/ndf/src/ndf/adapters/github.py` は 1000 行だが、触るのは 16 タスク中 1 タスク（`token` の 1 か所）で、import するテストが 23 ファイルある。同じファイルを触るタスクが多いのは新設の `scripts/plugin-deps.py`（16 タスク中 10）と `script-structure.yml`（123 行、16 タスク中 4）で、前者は構造を新しく作り、後者は小さい |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `scripts/plugin-deps.py` の外部の差し替え（`Fetch`・`Gh`・`Run`）と結果 JSON の組み立て、`script-structure.yml` の差分の 2 点（`HEAD^1`・`HEAD`）を比べるステップ | 単位 2〜7 と 9 が同じスクリプトへ別々にサブコマンドを足し、単位 1・3・6・7 が同じワークフローへ同じ形のステップを足すので、単位をまたぐ重複が出やすい |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 3370:7・3370:9 と 3691:1 の Issue の行は、Renovate が `main` で App の token を持って走るまで実走で確かめられない（I8） | 単位 4 の PR ではワークフローと検証器を記録した入力のテストで縛る。実走の確かめ（詳細設計書の「受け入れ条件 9 と 7 の確かめ」の 1〜7）は、リリースの後に利用者が App と秘密と変数を置いてから打ち、結果を #3370 に書く。PR 本文に、App と秘密の導入をマージの後に行う順序を書く |
| `scripts/plugin-deps.py`・`scripts/tests/test_plugin_deps.py`・`script-structure.yml`・`scripts/required-checks-allow.json`・`.ndf/project.json` を複数の単位が触り、並べると衝突する | 単位 2 を先にマージし、残りはスプリントブランチの上で切る。衝突したら後に入る単位が取り込み直す |
| 上げたバージョンで落ちるテストの数が実装の時点まで分からない（決定 7） | 単位 1 の見込みに直しを含めない。直しで 1,000 行を超えそうなら、直しを依存の向きで別の単位へ分け直す |
| 上げたバージョンで解決できない組がある | 詳細設計書の手順 4 で 1 つ前の候補へ戻し、戻した依存と理由を PR 本文に書く |
| Z2 #3687（lz2-ci）が `.github/workflows/` と `plugins/ndf/contracts/` を触る | 計画 p9 では lz2-ci はこのレーンの後に始まる。SHA の固定（単位 7）と下限の表（単位 8）が先に入る |
| `gh`・`codex` の下限を高く置きすぎて利用者が止まる | Task 14 の決め方で、NDF が使う引数が初めて入った版にする。決められない CLI は CI の版を置いて PR 本文と「未確認のまま残ること」の行に書く |
| `main` の ruleset に `ndf/renovate-gate` を足す前は、関門が必須にならない | 承認ゲート 2 で利用者が足す（`scripts/required-checks-allow.json` の `awaiting`）。足す前に開いている PR は workflow の再実行で状態を得る |

## 切り戻し手順

- 単位ごとに、スプリントブランチへのマージのコミットを `git revert` で戻せる。データの移行を含まない
- 単位 1 を戻すと lock が前の版に戻る。後に入った単位は lock を触らないので、単位 1 だけを戻してよい
- 動き出した Renovate を止めるときは、Actions の変数 `RENOVATE_APP_CLIENT_ID` を消す（`renovate.yml` の定時の実行が飛ぶ）。開いた更新の PR は閉じる
- 単位 8 を戻すと、外部 CLI の下限の比べが無くなり、今の起動の振る舞いに戻る

## 完了の定義

- [ ] 3370:4〜12 と 3691:1〜6 をすべて満たし、条件ごとに検証手段と結果が対応している（3370:13 は当たらない。3370:1〜3 は設計 PR #3522 で済み）
- [ ] すべての単位の PR で `cross-review` が収束し、スプリントブランチへマージされている
- [ ] スプリントの PR 本文に、バージョンが変わった依存の一覧、戻した依存と理由、上げたバージョンで直した箇所と理由、App と秘密の導入をマージの後に行う順序がある
- [ ] リリースの後に、詳細設計書の「受け入れ条件 9 と 7 の確かめ」を打った結果が #3370 にある
- [ ] #3380 の行 3380-1・2・3・4・6・8・9 が #3691 の受け入れ条件で満たされたことを #3380 に書き、残る行（3380-5 の DBOS、3380-7 の CI の uv と Python の版）の行き先が開いた課題にあることを確かめてから閉じる
- [ ] `quality-gates` の `standard` の段階（完了判定の差分のファイルの数を設計書の「設計の結果」と照らす）
