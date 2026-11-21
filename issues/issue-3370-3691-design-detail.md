# #3370 #3691 の詳細設計: Renovate と Dependabot の設定・2 本のワークフロー・依存の検査のスクリプト・外部 CLI の下限

設計文書 [issue-3370-3691-design.md](./issue-3370-3691-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ以上あるため別ファイルにした（新設 10・シグネチャを変える関数 0。責務を変える関数 8 は「外部 CLI の下限」の表に変える前と後を書く）。決定の番号は [issue-3370-3691-design-decisions.md](./issue-3370-3691-design-decisions.md) を指す。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/
├── renovate.json *
├── dependabot.yml
└── workflows/
    ├── renovate.yml *
    ├── deps-weekly.yml *
    ├── script-structure.yml          （uv lock --check を 4 つのプロジェクトへ）
    └── <ほかの 10 本>.yml            （uses: を SHA と版の注記へ）
scripts/
├── plugin-deps.py *
├── required-checks-allow.json
└── tests/test_plugin_deps.py *
pyproject.toml・uv.lock
plugins/ndf/pyproject.toml・uv.lock
plugins/playwright-kit/skills/playwright-kit-ops/pyproject.toml・uv.lock
plugins/mcp/mcp-serena/pyproject.toml・uv.lock
plugins/ndf/
├── contracts/domains/cli-floors.json *
├── src/ndf/adapters/cli_floor.py *
├── src/ndf/adapters/{claude,agents,github}.py
├── src/ndf/domains/project/measure_ci.py
├── src/ndf/domains/relay/{wrapper,cli,account_login}.py
└── tests/adapters/test_cli_floor.py *
.ndf/project.json                     （test.ci_exempt に 2 行）
```

依存の向き: ワークフロー → `scripts/plugin-deps.py` → 設定のファイル（`renovate.json`・各 `pyproject.toml`・`cli-floors.json`）の一方向で、スクリプトは NDF のパッケージを import しない（根の環境で動く）。NDF の側は 起動の箇所 → `cli_floor` → `ndf.contracts.facts`（表の読み取り）の一方向である。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `.github/renovate.json` | 新設 | Renovate の対象・7 日・承認・まとめ方・固定と理由 | — | — | — |
| `.github/workflows/renovate.yml` | 新設 | 週 1 回と手動で Renovate を動かす | `renovate.json`・GitHub App | — | — |
| `.github/workflows/deps-weekly.yml` | 新設 | 週 1 回と手動の `report`・`cli-help`、lock を変えた PR の `affected` | `scripts/plugin-deps.py` | — | — |
| `scripts/plugin-deps.py` | 新設 | 依存の検査の 4 つのサブコマンド | 標準ライブラリ・`packaging`（根の環境） | モジュール | `main`・`targets`・`read_holds`・`report`・`lock_diff`・`affected`・`cli_help`・`uv_pin`（新設） |
| `plugins/ndf/src/ndf/adapters/cli_floor.py` | 新設 | 外部 CLI のバージョンを下限と比べる | `ndf.contracts.facts`・`semver` | モジュール | `require`・`TooOld`（新設） |
| `plugins/ndf/contracts/domains/cli-floors.json` | 新設 | `gh`・`claude`・`codex` の下限の表 | — | — | — |
| 起動の箇所 7 ファイル | 変更 | 起動の前に `cli_floor.require` を呼ぶ | `cli_floor` | — | `run_agent`・`call_claude`・`claude_plugin`（`claude.py` の `plugin` の起動）・`token`・`raw`・`Wrapper` の起動・`_exec_claude`・`login`（変更。シグネチャは変えない） |
| `scripts/tests/test_plugin_deps.py` | 新設 | 設定の検査とスクリプトの単体テスト | `scripts/plugin-deps.py` | — | — |
| `plugins/ndf/tests/adapters/test_cli_floor.py` | 新設 | 下限の比べと、起動の箇所がすべて比べを持つこと | `cli_floor` | — | — |

## Renovate の設定

満たす決定: 決定 1〜8・12、受け入れ条件 #3370 の 5〜9・12、#3691 の 1・5・6

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 待つ日数（正） | 設定値 | `plugins/ndf/pyproject.toml` の `[tool.uv] exclude-newer` | `"7 days"` |
| 待つ日数（写し） | 設定値 | 根・playwright-kit・mcp-serena の `[tool.uv] exclude-newer`、`renovate.json` の `minimumReleaseAge`、`dependabot.yml` の `cooldown.default-days` | `"7 days"`・`"7 days"`・`7`（I6） |
| 日付の無いバージョン | 設定値 | `minimumReleaseAgeBehaviour` | `"timestamp-required"` |
| マージ先のブランチ | 設定値 | `baseBranchPatterns` | `["main"]` |
| 対象の仕組み | 設定値 | `enabledManagers` | `["pep621", "npm", "custom.regex"]` |
| 外す場所 | 設定値 | `ignorePaths` | `["docs/presentations/**"]`（I9 の正。スクリプトもこれを読む） |
| 依存の一覧の Issue | 設定値 | `dependencyDashboard`・`dependencyDashboardTitle`・`dependencyDashboardApproval`・`dependencyDashboardAutoclose` | `true`・`"依存の一覧"`・`true`・`true`。キーの形は使う Renovate のバージョンの定義に合わせる（前の設計の注記と同じ） |
| lock の作り直し | 設定値 | `lockFileMaintenance` | 全体は既定（無効）のまま、`matchManagers: ["pep621"]` の規則で `{"enabled": true, "minimumReleaseAge": null}`（予定は既定の月曜の朝。承認は全体の `dependencyDashboardApproval` で求める。待つ日数を置かない理由は決定 6） |
| lock の作り直しのチェック | 設定値 | `statusCheckWhen.artifactError` | `"always"` |
| 脆弱性の PR | 設定値 | `vulnerabilityAlerts.enabled`・`osvVulnerabilityAlerts` | `false`・`false`（決定 9） |
| ラベル・コミットの接頭辞 | 設定値 | `labels`・`commitMessagePrefix` | `["dependencies"]`・`"Update:"` |
| 許すコマンド（全体の設定） | 設定値 | `renovate.yml` が渡す全体の設定の `allowedCommands` | `["^python3 scripts/plugin-deps\\.py uv-pin$"]`（全体の設定にだけ書ける。渡し方は「未確認のまま残ること」。LIB13） |
| Renovate のバージョン | 設定値 | `.github/renovate-version` | 実装の時点で公開から 7 日たった 44 系（2026-11-21 の最新は 44.257.5。LIB9） |

規則（`packageRules`）は次の並びで置く。固定と対象の外の規則の `description` は、決定 5 の 5 行を持つ。

| # | 当てる依存 | 中身 |
| --- | --- | --- |
| 1〜5 | lock ごと（`matchFileNames` に各プロジェクトの定義） | `groupName` を `依存（<ディレクトリ>）` にし、lock の作り直しも同じまとまりに入れる（決定 3） |
| 6 | `matchCurrentAge: "> 365 days"` | `ignoreUnstable: false`（決定 4） |
| 7 | `httpx` | `allowedVersions: "==0.28.1"`。種類 `相手の制約`・相手 `githubkit 0.17.1`・制約 `httpx<1.0.0,>=0.23.0`・見直す日（実装の日から 90 日） |
| 8 | `dbos` | `enabled: false`。種類 `対象の外`・相手 `C1+ の続き（#3285 の追補）`・制約 `3.4.0 のまま`・見直す日（実装の日から 90 日） |
| 9 | `ruamel-yaml` | `plugins/ndf` と `plugins/mcp/mcp-serena` を同じバージョンにそろえる。`groupName` を `ruamel-yaml（plugins/ndf と mcp-serena）` にし、規則 1〜5 より後に置いて上書きする（mcp-serena の定義の頭の注記の約束） |
| 10 | `UV_VERSION` の 2 か所（`customManagers` が拾う `astral-sh/uv`） | `postUpgradeTasks` に `python3 scripts/plugin-deps.py uv-pin`、書き換えてよいファイルに `plugins/ndf/bin/ndf` と `scripts/lib/ndf_wrappers.py`（決定 12） |

`customManagers` は 2 つである。1 つ目は `managerFilePatterns` に `plugins/ndf/bin/ndf` と `scripts/lib/ndf_wrappers.py`、`matchStrings` に `UV_VERSION *= *"(?<currentValue>[0-9.]+)"`、`depNameTemplate` に `astral-sh/uv`、`datasourceTemplate` に `github-releases` を置く（M5 で検証を通った形）。2 つ目は `.github/renovate-version` の 1 行を `renovatebot/renovate` の `github-releases` として拾う。

固定の `description` の形（行の順は固定。`見直す日` は ISO の日付）:

```json
"description": [
  "種類: 相手の制約",
  "相手: githubkit 0.17.1",
  "制約: httpx<1.0.0,>=0.23.0",
  "見直す日: 2027-02-19",
  "理由: githubkit 0.17.1 が httpx<1.0.0,>=0.23.0 を求めるため 0.28.1 に固定する（#3370 の受け入れ条件 8）"
]
```

`description` は文字列の配列を受け、`packageRules` の中にも書ける（LIB11）。未知のキー（`reason` など）は検証で落ちる（M6）ので、欄はこの配列の行で持つ。

## Renovate を動かすワークフロー

満たす決定: 決定 2・12、受け入れ条件 #3370 の 7・I5・I8

| 項目 | 値 |
| --- | --- |
| 起動 | `schedule: cron "0 0 * * 1"`（月曜 09:00 JST）と `workflow_dispatch` だけ |
| token | `actions/create-github-app-token`（v3.4.0。入力は `client-id`。`app-id` は非推奨。LIB17）に Actions の変数 `RENOVATE_APP_CLIENT_ID` と秘密 `RENOVATE_APP_PRIVATE_KEY` を渡す。権限は contents・pull-requests・issues・statuses を `write`、checks を `read` |
| Renovate | `renovatebot/github-action`（SHA で固定）。`renovate-version` に `.github/renovate-version` の 1 行を読んで渡す（決定 2） |
| 全体の設定 | `RENOVATE_REPOSITORIES: ${{ github.repository }}`・`RENOVATE_ONBOARDING: "false"`・`RENOVATE_REQUIRE_CONFIG: required`・`RENOVATE_BINARY_SOURCE: install`・許すコマンド |
| 有効にする時機 | 実装の PR が `main` に入り、App と秘密を置いた後（I8）。ジョブに `if: vars.RENOVATE_APP_CLIENT_ID != ''` を置き、変数を置くまでの定時の実行は飛ばす（失敗を出し続けない） |

## サプライチェーンの検査のワークフロー

満たす決定: 決定 9・10・11、受け入れ条件 #3691 の 2・3・6

| ジョブ | 起動 | 打つこと | 権限 |
| --- | --- | --- | --- |
| `supply-chain-report` | `schedule: cron "0 0 * * 1"`・`workflow_dispatch` | `uv run --frozen --project . python scripts/plugin-deps.py report` | `issues: write`・`contents: read` |
| `cli-help` | 同上 | 最新の `@anthropic-ai/claude-code` と `@openai/codex` を npm で入れ、`python3 scripts/plugin-deps.py cli-help` | `contents: read` |
| `affected` | `pull_request` の `paths`（`**/uv.lock`・`**/package-lock.json`） | `python3 scripts/plugin-deps.py affected --base origin/${{ github.base_ref }}` | `pull-requests: write`・`contents: read` |
| `renovate-config` | `pull_request` の `paths`（`.github/renovate.json`・`.github/renovate-version`） | `npx --yes --package renovate@<.github/renovate-version の値> -- renovate-config-validator --strict --no-global .github/renovate.json`（`--no-global` でリポジトリの設定として読む。LIB11） | `contents: read` |

`affected` と `renovate-config` のジョブの名前は `scripts/required-checks-allow.json` の `not_required` に置く。理由は「lock か Renovate の設定を変えた PR にだけ走る」で、`affected` は合否でなく一覧を出す。`renovate/artifacts` も同じ定義の `statuses` に置く（決定 8）。

## 依存の検査のスクリプト

満たす決定: 決定 5・9・10・11・12、受け入れ条件 #3691 の 1・2・3・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `scripts/plugin-deps.py` | 新設 | `main(argv: list[str] \| None = None) -> int` | サブコマンドを振り分け、結果 JSON を標準出力へ 1 行で出す |
| `targets` | 同上 | 新設 | `targets(root: Path) -> list[Target]` | 追跡している lock から `renovate.json` の `ignorePaths` を除き、種類（uv・npm）とディレクトリの組を返す |
| `read_holds` | 同上 | 新設 | `read_holds(config: dict) -> list[Hold]` | `packageRules` の固定と対象の外の規則から 5 つの欄を読む。欄が欠けたら `ValueError` |
| `report` | 同上 | 新設 | `report(root: Path, fetch: Fetch, gh: Gh, today: date) -> Result` | すべての対象の脆弱性と解いてよい固定を集め、脆弱性の知らせを作る・書き直す・閉じる |
| `lock_diff` | 同上 | 新設 | `lock_diff(old: str, new: str) -> list[Change]` | 2 つの `uv.lock`（か `package-lock.json`）の本文から、変わったパッケージの名前・前と後のバージョンを返す |
| `affected` | 同上 | 新設 | `affected(root: Path, base: str, gh: Gh) -> Result` | 変わったパッケージを import するモジュールとテストを並べ、PR に 1 件のコメントで出す |
| `cli_help` | 同上 | 新設 | `cli_help(root: Path, run: Run) -> Result` | `cli-floors.json` の見本の引数が、各 CLI の `--help` の出力にあるかを照らす |
| `uv_pin` | 同上 | 新設 | `uv_pin(root: Path, fetch: Fetch) -> Result` | `UV_VERSION` のインストーラを取得して sha256 を計算し、`UV_INSTALLER_SHA256` を書き直す |

`Fetch`・`Gh`・`Run` は外部（HTTP・`gh`・子プロセス）を差し替える引数で、単体テストは記録した応答を渡す。

- `report`
  - 事前条件: 各対象のディレクトリで `uv audit` か `npm audit` が打てる
  - 事後条件: 取得がすべて成り立てば、ラベル `supply-chain` の開いた Issue がちょうど 1 件で、本文は「脆弱性」と「解いてよい固定」の 2 つの表を持つ。どちらも 0 行なら Issue は閉じている。取得が 1 つでも失敗したら、Issue は実行の前のまま
  - 副作用: `uv audit`・`npm audit`・PyPI の JSON API（固定の相手の最新の上げる先の `requires_dist`）・`gh issue list/create/edit/close`
- `affected`
  - 事後条件: lock が変わった PR に、目印の行 `<!-- ndf:deps-affected -->` を持つコメントがちょうど 1 件ある。lock が変わらなければコメントを書かない
  - 副作用: `git show <base>:<lock>`・`uv tree --invert`・`gh pr comment`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `report` | 脆弱性の取得の失敗 | ネットワーク・OSV の失敗・JSON の形の変化 | `uv audit` の終了コード（0 は 0 件・1 は 1 件以上・2 は質問合わせの失敗。M8〜M11）と JSON の `schema.version` と読み取り | 止める | `status: stopped`・`cause: transient` と対象のディレクトリ。終了コード 1 | Issue に触れない（I11）。再実行してよい |
| `report` | PyPI の取得の失敗 | ネットワーク・404 | `fetch` | 止める | 同上 | 同上 |
| `read_holds` | 欄の欠け | 規則の書き損じ | 欄の読み取り | 止める | `status: stopped`・`cause: config_broken` と規則の番号。終了コード 1 | 規則を直して再実行 |
| `affected` | 読めない lock | lock の衝突のマーカー・形の違い | `lock_diff` | 止める | `cause: invalid`。コメントは書かない。終了コード 1 | 再実行してよい |
| `cli_help` | 見本の引数が無い | CLI の引数の変化 | `--help` の出力の照らし合わせ | 止める | `status: stopped`・`cause: check_failed` と CLI と引数。終了コード 1 | 見本か NDF を直す |
| `uv_pin` | インストーラの取得の失敗 | ネットワーク・バージョンが無い | `fetch` | 止める | 終了コード 1。Renovate の `renovate/artifacts` が赤 | 定義を書き換えない。再実行してよい |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `report` の固定の判定 | 種類 `相手の制約` で、相手の最新の上げる先が固定した依存に `制約:` と違う指定を持つ | 「解いてよい固定」に出す |
| 同上 | `見直す日:` が今日より前 | 「解いてよい固定」に出す（種類によらない） |
| 同上 | `exclude-newer-package` の例外で、注記の外す日が今日より前 | 「解いてよい固定」に出す（決定 9） |
| `affected` | 変わったパッケージを import するモジュールが無い | 推移的な依存として、それを求める直接の依存を添えて出す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `targets` | 追跡している 6 つの lock から `docs/presentations/` を除いた 5 つ | `ignorePaths` が空なら 6 つ | `renovate.json` が無ければ止まる |
| `read_holds` | httpx と dbos の 5 つの欄 | 行の順が違うと欠けとして止まる | 欄を 1 つ消すと `config_broken` |
| `report` | 記録した `uv audit` の JSON（脆弱性あり）で表を作る。0 件で閉じる | 見直す日が今日ちょうどは出さず、前日は出す | 取得の失敗を 1 つ混ぜると Issue に触れない |
| `lock_diff` | 記録した 2 つの lock の差分 | 足しただけ・消しただけのパッケージ | 衝突のマーカーを持つ lock で止まる |
| `affected` | 変わったパッケージを import するモジュールとテスト | 推移的な依存だけが変わった差分 | lock が変わらない差分でコメントを書かない |
| `cli_help` | 見本の引数がすべてある `--help` | 見本に無い引数が `--help` に増えても通る | 見本の引数を 1 つ消した `--help` で止まる |
| `uv_pin` | 記録したインストーラの本文の sha256 を書く | `UV_VERSION` が変わらなければ書かない | 取得の失敗で書き換えない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `lock_diff` | 標準ライブラリの `tomllib` | uv.lock は TOML | 使う |
| `affected` | grimp（import のグラフ）・pytest-testmon（変わったファイルのテストの選び） | LIB20・LIB21 | 使わない（grimp は import できるパッケージだけを見て `scripts/` とテストを見ない。testmon は依存のバージョンの変化をテストの選びに使わない）。標準ライブラリの `ast` で読む |
| `targets` | `scripts/lib/ci_workflows.py` | `grep -rn 'ls-files' scripts/lib` | 使えない（ワークフローだけを読む）。`git ls-files` を打つ |

## 外部 CLI の下限

満たす決定: 決定 11、受け入れ条件 #3691 の 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `require` | `plugins/ndf/src/ndf/adapters/cli_floor.py` | 新設 | `require(name: str, executable: str \| None = None) -> None` | 表の行 `name` の下限と、実行ファイルの `--version` の出力を比べ、古ければ `TooOld` を上げる。プロセスにつき CLI ごとに 1 回だけ読む |
| `TooOld` | 同上 | 新設 | `class TooOld(Exception)`（`name`・`found`・`floor` を持つ） | 理由の文「<名前> <見つけたバージョン> は下限 <下限> より古い。<下限> 以上へ上げる」を持つ |

起動の箇所の変える前と後（シグネチャは変えない）:

| 関数 | 変える前 | 変えた後 |
| --- | --- | --- |
| `agents.run_agent` | 学習の設定と重い起動のスロットを確かめて起動する | 先に `require`。`TooOld` なら `AgentState.REFUSED` と理由で起動しない |
| `claude.call_claude` と `plugin` の起動 | 同上 | 先に `require("claude")`。`TooOld` なら `Failure.REFUSED` と理由 |
| `github.token` | `gh auth token` を打つ | 打つ前に `require("gh")`。`TooOld` なら `GitHubError(理由)` |
| `measure_ci` の `raw` | `gh api` を打つ | 打つ前に `require("gh")`。`TooOld` なら `GhUnavailable(理由)` |
| `relay.wrapper` の起動・`relay.cli` の `execvp` | `claude` を起動する | 先に `require("claude")`。`TooOld` なら今の起動の失敗と同じ経路で理由を出して 0 以外で終わる |
| `relay.account_login` | `claude` が PATH に無ければ `LoginError("precondition")` | 見つけた後に `require("claude")`。`TooOld` なら `LoginError("precondition", 理由)` |

### 内部のデータと定数

`cli-floors.json` は領域の表の形（`summary`・`owner`・`columns`・`rows`）で持つ。

| 列 | 型 | 中身 |
| --- | --- | --- |
| `id` | 文字列 | `gh`・`claude`・`codex` |
| `executable` | 文字列 | 実行ファイルの名前。`claude`・`codex` は `domains/runtimes.json` の `executable` と同じ値（表の参照の検査で縛る） |
| `version_args` | 文字列の配列 | `["--version"]` |
| `version_pattern` | 文字列 | 出力からバージョンを取り出す正規表現（`gh version (\d+\.\d+\.\d+)`・`(\d+\.\d+\.\d+) \(Claude Code\)`・`codex-cli (\d+\.\d+\.\d+)`。M15 の出力の形） |
| `min_version` | 文字列 | 下限（「未確認のまま残ること」の決め方） |
| `help` | 配列 | 照らす見本。`{"args": [...], "flags": [...]}` の並び。`claude` は `{"table": "data/claude-cli.json"}` で今の表を指す |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `require` | 下限より古い | 利用者の CLI が古い | 比べ | 呼び出し元へ返す（`TooOld`） | 起動の箇所の形で理由を出し、0 以外で終わる | CLI を上げて再実行 |
| `require` | バージョンを読めない | 出力の形の変化・起動の失敗 | 正規表現 | 既定値で続ける（比べずに起動を通し、警告を標準エラーへ 1 行） | 警告の 1 行 | 起動の失敗は今の箇所の扱いに任せる |

バージョンを読めないときに止めないのは、CLI が無い・壊れているときの扱いを今の起動の箇所が既に持つためである。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `require` | 下限より新しいバージョンで何も上げない | 下限と同じバージョンで上げない。1 つ古いバージョンで上げる | 読めない出力で警告だけ出して通す。2 回目の呼び出しで `--version` を打たない |
| 起動の箇所 | 古いバージョンを返す偽の CLI を PATH の先に置くと、各エントリポイントが起動せずに理由を返す | — | `src/ndf` の中で表の `executable` を `subprocess` か `exec` に渡す関数が、`require` を呼ばずに足されるとテストが落ちる |

## Dependabot の設定とワークフローの参照

満たす決定: 決定 4・13、受け入れ条件 #3691 の 4

- `dependabot.yml` の `github-actions` の項目に `cooldown: { default-days: 7 }` を足す。頭の注記を「Python と npm の依存は Renovate（`.github/renovate.json`）、Actions はここ」に書き直す
- 外部の Action の参照を `uses: <所有者>/<名前>@<40 桁の SHA> # v<タグ>` にする。SHA は実装の時点で `gh api repos/<所有者>/<名前>/git/ref/tags/<タグ>` が指すコミットにする（注記つきのタグは指す先のコミットまで辿る）
- 設定の検査のテストは、`.github/workflows/*.yml` の `uses:` のうち `./` で始まらないものすべてが `@[0-9a-f]{40} # v` の形であることを縛る

## 依存の定義と lock と一致の検査

満たす決定: 決定 4・6・7、受け入れ条件 #3370 の 4・5・10・12、#3691 の 5

- 4 つの `pyproject.toml` の `[tool.uv]` に `exclude-newer = "7 days"` を足し、各ディレクトリで `uv lock --upgrade` を打つ（DBOS と httpx は今のバージョンのまま。`--upgrade-package` で除くか、定義の `==` で止める）
- `plugins/ndf/pyproject.toml` の頭の注記を、作り直しの間の注記（プロジェクト名 `ndf-rebuild`・`11.0.0-dev.N`）から、バージョンの選び方（決定 106 と 7 日）と上げる操作（依存の一覧の Issue と `.github/renovate.json`）の 3 行へ直す
- `script-structure.yml` の 97 行の `uv lock --check --project plugins/ndf` を、4 つのプロジェクトを並べたステップにする
- 脆弱性の直しの例外の形（決定 9）: `exclude-newer-package = { "<パッケージ>" = "<日時>" }  # <勧告の ID> 外す日 <YYYY-MM-DD>`

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `scripts/plugin-deps.py` の各サブコマンド | 統一の結果 JSON の 1 行（`tool: "plugin-deps"`・`status`・`summary`・`items`） | 標準出力 | GitHub の token・App の秘密鍵 |
| `cli_floor.require` | バージョンを読めなかったときの 1 行 | 標準エラー | — |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `report` の定時の実行と手動の実行 | 脆弱性の知らせの Issue | ワークフローの `concurrency: supply-chain-report` | 同じ入力なら同じ本文。途中で止まっても次の実行が書き直す |
| `affected` の同じ PR への push の連続 | PR のコメント | `concurrency: deps-affected-<PR 番号>` と `cancel-in-progress` | 目印の行で 1 件を引いて書き直す |
| Renovate の更新の PR と DBOS の PR | `uv.lock` | 先に `main` に入った PR の lock を正とし、後の PR が lock を作り直す（I8） | Renovate は PR 本文のチェックで作り直す |
