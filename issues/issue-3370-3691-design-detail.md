# #3370 #3691 の詳細設計: Renovate と Dependabot の設定・2 本のワークフロー・依存の検査のスクリプト・外部 CLI の下限

設計文書 [issue-3370-3691-design.md](./issue-3370-3691-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ以上あるため別ファイルにした（新設 15・シグネチャを変える関数 0。責務を変える関数 11 は「外部 CLI の下限」の表に変える前と後を書く）。決定の番号は [issue-3370-3691-design-decisions.md](./issue-3370-3691-design-decisions.md) を指す。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/
├── renovate.json *
├── dependabot.yml
└── workflows/
    ├── renovate.yml *
    ├── deps-weekly.yml *
    ├── deps-affected-comment.yml *   （信頼済みのコードで確かめるテストの一覧を投稿する）
    ├── script-structure.yml          （uv lock --check をプロジェクトごとの入力で・依存の PR の関門・affected）
    ├── runtime-plugin-smoke-latest.yml （最新の CLI の --help の照らし合わせを足す）
    └── <ほかの 10 本>.yml            （uses: を SHA と版の注記へ）
plugins/ndf/skills/release/assets/slides/.npmrc *（min-release-age）
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
├── src/ndf/adapters/cli.py            （launch-args が ndf-claude の起動の前に比べる）
├── src/ndf/domains/relay/{wrapper,cli,account_login,cli_account}.py
└── tests/adapters/test_cli_floor.py *
.ndf/project.json                     （test.ci_exempt に 2 行）
```

依存の向き: ワークフロー → `scripts/plugin-deps.py` → 設定のファイル（`renovate.json`・各 `pyproject.toml`・`cli-floors.json`）の一方向で、スクリプトは NDF のパッケージを import しない（根の環境で動く）。NDF の側は 起動の箇所 → `cli_floor` → `ndf.contracts.facts`（表の読み取り）の一方向である。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `.github/renovate.json` | 新設 | Renovate の対象・7 日・承認・まとめ方・固定と理由 | — | — | — |
| `.github/workflows/renovate.yml` | 新設 | 週 1 回と手動で Renovate を動かす。手動では検証用のブランチを指定できる | `renovate.json`・GitHub App・`scripts/plugin-deps.py` | — | — |
| `.github/workflows/deps-weekly.yml` | 新設 | 週 1 回と手動の `report`、Renovate の設定を変えた PR の `renovate-config` | `scripts/plugin-deps.py` | — | — |
| `.github/workflows/deps-affected-comment.yml` | 新設 | `script-structure.yml` の実行が残した一覧のデータを、既定ブランチのコードで PR に投稿する | `scripts/plugin-deps.py`（既定ブランチの版） | — | — |
| `scripts/plugin-deps.py` | 新設 | 依存の検査のサブコマンド | 標準ライブラリ・`packaging`（根の環境） | モジュール | `main`・`targets`・`read_holds`・`report`・`lock_diff`・`affected`・`post_affected`・`age`・`action_pins`・`renovate_gate`・`renovate_log`・`cli_help`・`uv_pin`（新設） |
| `plugins/ndf/src/ndf/adapters/cli_floor.py` | 新設 | 外部 CLI のバージョンを下限と比べる | `ndf.contracts.facts`・`semver` | モジュール | `require`・`TooOld`（新設） |
| `plugins/ndf/contracts/domains/cli-floors.json` | 新設 | `gh`・`claude`・`codex` の下限の表 | — | — | — |
| 起動の箇所 9 ファイル | 変更 | 起動の前に `cli_floor.require` を呼ぶ | `cli_floor` | — | `_guarded_run`・`call_claude`・`claude_plugin`（`claude.py` の `plugin` の起動）・`token`・`raw`・`Wrapper` の起動・`_exec_claude`・`_claude`・`_auth`・`launch_args`・`_acc_remove`（変更。シグネチャは変えない） |
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
| 依存の一覧の Issue | 設定値 | `dependencyDashboard`（オブジェクト）・`dependencyDashboardApproval` | `{"enabled": true, "title": "依存の一覧", "autoclose": true}`・`true`。44.257.5 では真偽値の `dependencyDashboard` とフラットな `dependencyDashboardTitle`・`dependencyDashboardAutoclose` が設定の移行の対象で、`--strict` の検証が落ちるため、オブジェクトの形だけを書く。設定全体は実行に使う `.github/renovate-version` の版の `renovate-config-validator --strict` で検証する（`renovate-config` のジョブ） |
| 固定の一覧（依存の一覧の Issue の頭） | 設定値 | `dependencyDashboardHeader` | 固定と対象の外の規則ごとに「固定: <依存> <版>（<種類>・相手 <相手>・見直す日 <日付>）」の 1 行。`read_holds` が読む規則と同じ中身であることを設定の検査のテストが縛る（#3370 の受け入れ条件 8。httpx が遅れではなく固定として出る） |
| lock の作り直し | 設定値 | 仕組みごとの設定 `pep621.lockFileMaintenance`・`npm.lockFileMaintenance` | どちらも `{"enabled": true, "minimumReleaseAge": null}`。`packageRules` の中で有効にしても、44.257.5 は規則を当てる前に作り直しの候補を作るかを決める（`updates/flatten.ts`）ため、仕組みの設定に置く。全体の `lockFileMaintenance` は既定（無効）のまま。承認は全体の `dependencyDashboardApproval` で求める。待つ日数を置かない理由は決定 6 |
| 同じ lock の並行の PR を防ぐ | 設定値 | `branchConcurrentLimit`・`prConcurrentLimit` | `1`・`1`。Renovate のブランチは同時に 1 本だけで、同じ lock を触る PR が 2 本開かない（決定 3） |
| 出す順 | 設定値 | 規則の `prPriority` | lock ごとの通常の更新 `2` → ruamel-yaml の横断の更新 `1` → lock の作り直し `0`（`matchUpdateTypes: ["lockFileMaintenance"]`）。チェックの入った候補が複数あれば、値の大きいものから 1 本ずつ作る |
| major を分けない | 設定値 | 規則 1〜5 の `separateMajorMinor`・`separateMultipleMajor` | `false`・`false`（既定では `groupName` だけでは major のブランチが分かれ、同じ lock に 2 本目ができる） |
| 取得の失敗で止める | 設定値 | `hostRules` | `pypi.org`・`files.pythonhosted.org`・`registry.npmjs.org`・`api.github.com` に `{"abortOnError": true}`。個別のパッケージの取得が 1 つでも失敗したら、依存の一覧の Issue を書く前に実行を止める（#3370 の受け入れ条件 7。実走での確かめは「依存の一覧の Issue を書き換えないことの確かめ」） |
| npm の解決の版 | 設定値 | `constraints.npm` | `min-release-age` を持つ npm の最小の版以上（実装の時点に npm の文書で版を確かめて書く）。`binarySource: install` でこの npm を入れて lock を作り直す |
| lock の作り直しのチェック | 設定値 | `statusCheckWhen.artifactError` | `"always"` |
| 脆弱性の PR | 設定値 | `vulnerabilityAlerts.enabled`・`osvVulnerabilityAlerts` | `false`・`false`（決定 9） |
| ラベル・コミットの接頭辞 | 設定値 | `labels`・`commitMessagePrefix` | `["dependencies"]`・`"Update:"` |
| 許すコマンド（全体の設定） | 設定値 | `renovate.yml` が渡す全体の設定の `allowedCommands` | `["^python3 scripts/plugin-deps\\.py uv-pin$", "^uv lock --project \\.$"]`（全体の設定にだけ書ける。渡し方は「未確認のまま残ること」。LIB13） |
| Renovate のバージョン | 設定値 | `.github/renovate-version` | 実装の時点で公開から 7 日たった 44 系（2026-11-21 の最新は 44.257.5。LIB9）。中身は `^[0-9]+\.[0-9]+\.[0-9]+$` の 1 行だけ |

規則（`packageRules`）は次の並びで置く。固定と対象の外の規則の `description` は、決定 5 の 5 行を持つ。

| # | 当てる依存 | 中身 |
| --- | --- | --- |
| 1〜5 | lock ごと（`matchFileNames` に各プロジェクトの定義）の直接の依存の更新（`matchUpdateTypes` に `lockFileMaintenance` を含めない） | `groupName` を `依存（<ディレクトリ>）`、`prPriority: 2`、`separateMajorMinor: false`・`separateMultipleMajor: false`。lock の作り直しは同じまとまりに入れない（44.257.5 の `updates/generate.ts` は通常の更新と作り直しの同じまとまりを扱わない。決定 3） |
| 4 の追加 | playwright-kit の定義 | `postUpgradeTasks` に `uv lock --project .`、`fileFilters` に `uv.lock`、`executionMode: "branch"`。根は playwright-kit を編集可能なパスの依存として取り込み、根の `uv.lock` もその制約を持つため、同じ PR で根の lock も作り直す。根の固定と両立せず解決できなければ `renovate/artifacts` が赤になり、関門（「依存の PR の関門」）がマージを止める。根の `==` を同じ PR で人が上げるか、行を外す |
| 6 | `matchPackageNames` に、`report` が「プレリリースを選べる依存」に出した依存を人が書いた一覧（初めは空） | `ignoreUnstable: false`（決定 4）。種類 `プレリリースを選ぶ`・相手 `決定 106`・制約 `7 日たった最新の安定版が 365 日より古い`・見直す日 |
| 7 | `httpx` | `allowedVersions: "==0.28.1"`。種類 `相手の制約`・相手 `githubkit 0.17.1`・制約 `httpx<1.0.0,>=0.23.0`・見直す日（実装の日から 90 日） |
| 8 | `dbos` | `enabled: false`。種類 `対象の外`・相手 `C1+ の続き（#3285 の追補）`・制約 `3.4.0 のまま`・見直す日（実装の日から 90 日） |
| 9 | `ruamel-yaml` | `plugins/ndf` と `plugins/mcp/mcp-serena` を同じバージョンにそろえる。`groupName` を `ruamel-yaml（plugins/ndf と mcp-serena）`、`prPriority: 1` にし、規則 1〜5 より後に置いて上書きする（mcp-serena の定義の頭の注記の約束）。ruamel-yaml は規則 1〜5 のまとまりから外れ、両方の lock を 1 本の PR で上げる。プロジェクトごとの PR と同時には開かない（`branchConcurrentLimit: 1`） |
| 10 | `UV_VERSION` の 2 か所（`customManagers` が拾う `astral-sh/uv`） | `groupName` を `uv のインストーラ`、`postUpgradeTasks` に `python3 scripts/plugin-deps.py uv-pin`、`executionMode: "branch"`（既定の `update` では 2 か所の更新ごとに打たれる）、`fileFilters` に `plugins/ndf/bin/ndf` と `scripts/lib/ndf_wrappers.py`（決定 12） |
| 11 | `matchUpdateTypes: ["lockFileMaintenance"]` | `prPriority: 0`。lock の作り直しは lock ごとに 1 本で、通常の更新と別の承認の行になる |
| 12 | 脆弱性の例外（決定 9） | 種類 `脆弱性の例外`・相手 `<勧告の ID>`・制約 `<パッケージ>==<版>`・見直す日（外す日）。`enabled: false` で Renovate はこの依存を上げない。`age` がこの組だけを 7 日の外として通す |

3 種類の候補（lock ごとの更新・ruamel-yaml の横断・lock の作り直し）に同時にチェックが入ったときは、`prPriority` の順に 1 本ずつ作る。前の PR がマージされて閉じると、次の定時か手動の実行が次の 1 本を作る。実装のレーンは、`plugins/ndf` の 3 種類に同時にチェックを入れ、ブランチが 1 本ずつしかできず、後のブランチが前のマージの後の `main` から作られることを、実際の依存の一覧の Issue と生成されたブランチで確かめる。

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
| 起動 | `schedule: cron "0 0 * * 1"`（月曜 09:00 JST）と `workflow_dispatch` だけ。`workflow_dispatch` の入力 `base_branch`（既定 `main`）は `main` か `verify/` で始まる名前だけを受ける |
| 排他 | `concurrency: { group: renovate, cancel-in-progress: false }`。定時と手動の実行が重なっても 1 つずつ走り、同じ依存の一覧の Issue とブランチへ 2 つの実行が書かない |
| チェックの効く時機 | 依存の一覧の Issue に入れたチェックは、次の定時（月曜 09:00 JST）か手動の実行で読まれて PR になる。すぐに PR がほしいときは `workflow_dispatch` を打つ。この手順は依存の一覧の Issue の頭（`dependencyDashboardHeader`）にも書く |
| token | `actions/create-github-app-token`（v3.4.0。入力は `client-id`。`app-id` は非推奨。LIB17）に Actions の変数 `RENOVATE_APP_CLIENT_ID` と秘密 `RENOVATE_APP_PRIVATE_KEY` を渡す。App の slug は変数 `RENOVATE_APP_SLUG` に置き、`renovate-gate` が状態を書いた主体の確かめに使う。権限は contents・pull-requests・issues・statuses を `write`、checks を `read` |
| Renovate のバージョンの検証 | token を作る前に、`.github/renovate-version` を環境変数として読み（`${{ }}` でシェルへ展開しない）、`^[0-9]+\.[0-9]+\.[0-9]+$` に合わなければ止める。続いて `python3 scripts/plugin-deps.py age --renovate-version` が npm の `renovate` のその版の公開日を読み、7 日に満たないか公開日が読めなければ止める（Dependency Dashboard のチェックは Renovate の待つ日数を飛ばせるため、ここで拒む） |
| Renovate | `renovatebot/github-action`（SHA で固定）。`renovate-version` に検証した 1 行を渡す（決定 2）。`LOG_FORMAT=json`・`LOG_FILE` で記録を残す |
| 全体の設定 | `RENOVATE_REPOSITORIES: ${{ github.repository }}`・`RENOVATE_ONBOARDING: "false"`・`RENOVATE_REQUIRE_CONFIG: required`・`RENOVATE_BINARY_SOURCE: install`・`RENOVATE_EXIT_CODE_FOR_ERRORS: "true"`・許すコマンド。`exitCodeForErrors` を有効にしないと、44.257.5 は外部のホストの取得の失敗で中断してもプロセスを 0 で終える（`workers/global/index.ts`）。この設定で `abortOnError` の中断がジョブの失敗になり、定時の実行の失敗が GitHub Actions の失敗の知らせで届く。`base_branch` が `main` でないときだけ `RENOVATE_BASE_BRANCH_PATTERNS` をその 1 本に、`RENOVATE_USE_BASE_BRANCH_CONFIG: merge` にして検証用のブランチの `renovate.json` を読ませ、さらに下の「検証用の全体の設定」を `RENOVATE_FORCE` で渡す |
| 検証用の全体の設定 | `base_branch` が `main` でないときだけ、`renovate.yml` に書いた固定の JSON を `RENOVATE_FORCE` に渡す（ブランチの側からは変えられない）。中身は `branchPrefix: "renovate-verify/"`・`dependencyDashboard: {"enabled": true, "title": "依存の一覧（検証）", "autoclose": true}`・`includePaths: ["plugins/ndf/**"]`・`packageRules` の末尾に足す 2 行（`{"matchPackageNames": ["*", "!httpx"], "enabled": false}` と `{"matchPackageNames": ["httpx"], "dependencyDashboardApproval": false}`）・`hostRules: [{"matchHost": "renovate-verify.invalid", "abortOnError": true}]`。理由は 3 つ。1 つ目に、44.257.5 はベースブランチを 1 本だけにするとブランチ名にベースの名前を含めず、同じ `branchPrefix` のブランチを整理の対象にする（`workers/repository/finalize/prune.ts`）。また依存の一覧の Issue は題で引くため、接頭辞と題を分けないと検証の実行が本番の承認の一覧と `renovate/` のブランチを書き換え・再利用・削除する。2 つ目に、`dependencyDashboardApproval: true` のままでは検証の実行が httpx の PR を作らないため、検証の対象を `plugins/ndf` の httpx に絞り、その更新だけ承認を免除する。3 つ目に、`hostRules` はリポジトリの初期化のときに登録され（`workers/repository/init/merge.ts` の `applyHostRules`）、ベースブランチの設定は後からマージされる（`workers/repository/process/index.ts`）ため、障害用のホストの規則はベースブランチの `renovate.json` に書いても HTTP のクライアントへ届かない。`force` は初期化の設定に上から重なり、同じ時点で登録される |
| PR の無い失敗の知らせ | Renovate の後のステップ `python3 scripts/plugin-deps.py renovate-log` が記録を読み、lock の作り直し・`postUpgradeTasks`・解決の失敗で PR もブランチも作られなかった更新を、ジョブの要約に並べて実行を失敗で終える。定時の実行の失敗は GitHub Actions の失敗の知らせで届く |
| 有効にする時機 | 実装の PR が `main` に入り、App と秘密を置いた後（I8）。ジョブに `if: vars.RENOVATE_APP_CLIENT_ID != ''` を置き、変数を置くまでの定時の実行は飛ばす（失敗を出し続けない） |

### 受け入れ条件 9 の確かめ（解決できない上げ方）

設定だけを変えた既定のブランチでは Renovate が既定のブランチの設定を読むため、検証用のブランチ `verify/3370-httpx` を切り、その `renovate.json` の規則 7 を `allowedVersions: "==1.0.dev6"`・`ignoreUnstable: false` に書き換える。`workflow_dispatch` を `base_branch: verify/3370-httpx` で打つと、検証用の全体の設定（上の表）が対象を `plugins/ndf` の httpx だけに絞り、その更新の承認を免除するので、Renovate は検証用の依存の一覧の Issue（題 `依存の一覧（検証）`）に行を書き、`renovate-verify/` のブランチから `verify/3370-httpx` を宛先に httpx を 1.0.dev6 へ上げる PR を作る。githubkit の制約で `uv lock` が解決できず `renovate/artifacts` が赤になる。

マージの拒否まで実際に見るため、打つ前に検証用の ruleset（対象 `refs/heads/verify/**`、必須のチェックに `main` の ruleset と同じ `script-structure-check`）を置く。`main` の ruleset は `verify/` 宛ての PR に効かないため、これが無いと拒否を観測できない。`renovate-gate` は head のブランチが `renovate/` か `renovate-verify/` で始まるときに打つ（下の「依存の PR の関門」）。PR の差分に `uv.lock` の変更が無いこと、`script-structure-check` が `renovate-gate` で落ち、PR の画面と `gh pr merge` がマージを拒むことを見る。

後始末では、消す PR の head が `renovate-verify/` で始まり base が `verify/3370-httpx` であること、消すブランチが `renovate-verify/` か `verify/` で始まること、閉じる Issue の題が `依存の一覧（検証）` であることを 1 件ずつ確かめてから、PR・ブランチ・検証用の依存の一覧の Issue・検証用の ruleset を消す。本番の依存の一覧の Issue の `updated_at` と `renovate/` のブランチの一覧が検証の前と同じであることも見る。

### 依存の一覧の Issue を書き換えないことの確かめ（受け入れ条件 7）

同じく検証用のブランチで、httpx の規則に `registryUrls: ["https://renovate-verify.invalid/simple/"]` を足して手動で打つ。障害用のホスト `renovate-verify.invalid` の `abortOnError` は検証用の全体の設定（`RENOVATE_FORCE`）が初期化のときに登録する。打つ前に検証用の依存の一覧の Issue を 1 度作っておき（上の確かめの実行で作られたもの）、その `updated_at` と本文を控える。実行の後に、Renovate の JSON の記録に `renovate-verify.invalid` への取得の失敗の行があること（対象のホストへの取得が実際に失敗したこと）、ジョブが失敗で終わったこと（`exitCodeForErrors`）、検証用の依存の一覧の Issue の `updated_at` と本文が実行の前と同じであることを見る。記録に失敗の行が無ければ、障害が起きていないので確かめは不成立として設定を見直す。書き換えたなら、受け入れ条件 7 の「書き換えずに」を「取得できなかった依存を一覧の Issue の取得の失敗の欄に出し、ほかの行を書き直す」へ変える案を、実装の PR の承認資料の判断が要る点に挙げる。

### 依存の PR の関門

`renovate/artifacts` は必須のチェックに入れない（決定 8）。代わりに、すべての PR で走る必須のチェック `script-structure-check` に、`python3 scripts/plugin-deps.py renovate-gate` のステップを足す。PR の head のブランチが `renovate/` か `renovate-verify/`（検証の実行）で始まるときだけ、PR の現在の head の SHA の `renovate/artifacts` の状態を読み、`success` でなければ（赤・未報告・保留）失敗で終わる。Renovate が push の直後に状態を書くまでの間は、未報告を 10 秒おきに 60 秒まで読み直してから失敗にする。ほかの PR では何もせず通る。必須のチェックが落ちるので、merge queue にも NDF の `ndf ci merge` にも入らない。

状態は `gh api repos/<リポジトリ>/commits/<SHA>/statuses` で読む。既存の `script-structure.yml` の権限は `contents: read` だけなので、`script-structure-check` のジョブの権限に `statuses: read` を足し、このステップに `GH_TOKEN: ${{ github.token }}` を渡す。同じ SHA と context には書き込みの権限を持つ別の主体も状態を書けるため、`statuses` の一覧（新しい順）から context が `renovate/artifacts` の行のうち最も新しい 1 件を選び、その `creator.login` が Renovate の App の bot（`<App の slug>[bot]`。slug は Actions の変数 `RENOVATE_APP_SLUG` から渡し、変数が無ければ失敗）であるときだけ、その `state` を見る。最も新しい行が別の主体のものなら、`success` でも `cause: check_failed`・理由「Renovate の App 以外が書いた状態」で失敗にする。単体テストは、赤・未報告・保留・古い head の SHA の成功・別の主体が後から書いた成功を拒み、今の head で App の bot が書いた最新の成功だけを通すことを縛る。

## サプライチェーンの検査のワークフロー

満たす決定: 決定 9・10・11、受け入れ条件 #3691 の 2・3・6

| ジョブ | 起動 | 打つこと | 権限 |
| --- | --- | --- | --- |
| `supply-chain-report` | `schedule: cron "0 0 * * 1"`・`workflow_dispatch` | `uv run --frozen --project . python scripts/plugin-deps.py report` | `issues: write`・`contents: read` |
| `renovate-config` | `pull_request` の `paths`（`.github/renovate.json`・`.github/renovate-version`）。同じ PR への連続の push は `concurrency: renovate-config-<PR 番号>` と `cancel-in-progress` | 1. PR の `.github/renovate-version` を環境変数で読み、`^[0-9]+\.[0-9]+\.[0-9]+$` に合わなければ止める。2. 宛先の先頭（`HEAD^1`）の `scripts/plugin-deps.py` を `git show` で取り出し（PR のコードでない検証器）、`age --renovate-version <版>` で公開から 7 日と公開日の有無を確かめる。3. 通ったときだけ `npx --yes --package renovate@<版> -- renovate-config-validator --strict --no-global .github/renovate.json`（`--no-global` でリポジトリの設定として読む。LIB11。設定全体を実行と同じ版で検証する） | `contents: read` |

`script-structure.yml`（すべての PR で走る必須のチェック）に足すステップ:

| ステップ | 条件 | 打つこと |
| --- | --- | --- |
| lock の一致の検査 | プロジェクトごとの入力（下の「依存の定義と lock と一致の検査」）が変わったとき。merge queue と push では常に | `uv lock --check --project <ディレクトリ>` |
| 依存の PR の関門 | `pull_request` で head のブランチが `renovate/` か `renovate-verify/` で始まるとき | `GH_TOKEN: ${{ github.token }}`・`RENOVATE_APP_SLUG: ${{ vars.RENOVATE_APP_SLUG }}` を渡して `python3 scripts/plugin-deps.py renovate-gate --pr <番号> --head <PR の head の SHA> --bot "<slug>[bot]"`（ジョブの権限に `statuses: read` を足す） |
| 公開から 7 日の検査 | `HEAD^1` と `HEAD` の間で lock・`UV_VERSION` の 2 か所・`.github/renovate-version` のどれかが変わったとき | `python3 scripts/plugin-deps.py age --base HEAD^1 --head HEAD` |
| Action の参照の検査 | `HEAD^1` と `HEAD` の間で `.github/workflows/` の `uses:` の行が変わったとき | `python3 scripts/plugin-deps.py action-pins --base HEAD^1 --head HEAD` |
| 確かめるテストの一覧 | `pull_request` のとき（lock の差分が無ければ数秒で終わる） | `python3 scripts/plugin-deps.py affected --base HEAD^1 --head HEAD --out tmp/deps-affected.json`。結果を artifact `deps-affected` に置く。lock の差分が無く、読み取りの token で引いた目印のコメントも無ければ artifact を置かない |

比べる 2 点は、`actions/checkout` の `fetch-depth: 2` で取った PR のマージのコミット（`HEAD`）とその第 1 親（`HEAD^1`）である。どちらも同じイベントで取ったコミットで、実行の間に宛先のブランチが進んでも PR の外の変更を拾わない（既存の NDF の検査の差分と同じ取り方）。

`deps-affected-comment.yml` は `workflow_run`（`Script structure` の `completed`）で起動し、`pull-requests: write`・`actions: read` を持つ。既定のブランチのコードだけを checkout し、PR のコードは動かさない。artifact `deps-affected` が無ければ最初のステップで終わる。あれば JSON をデータとしてだけ読み、`post_affected` が次を確かめてから投稿する。

- artifact の `pr` と `head_sha` が、`workflow_run` の `pull_requests[0].number` と `head_sha` に一致する
- `gh api repos/<リポジトリ>/pulls/<番号>` の今の `head.sha` が `head_sha` と同じ（古い実行の結果で新しい HEAD のコメントを書き直さない）
- 本文は既定のブランチのコードが artifact の値から組み立て、artifact の文字列はコードのブロックに入れて書く

fork からの PR では `pull_request` の token が読み取りだけになるため、書き込みはこの分けた実行だけが行う。

`renovate-config` のジョブの名前は `scripts/required-checks-allow.json` の `not_required` に置く。理由は「Renovate の設定を変えた PR にだけ走る」である。`deps-affected-comment.yml` のジョブも合否でなく一覧の投稿なので同じ所に置く。`renovate/artifacts` も同じ定義の `statuses` に置く（必須にしない。関門は `script-structure-check` の中にある。決定 8）。

### `--help` の照らし合わせの置き場

最新の Claude Code と Codex は、既存の `runtime-plugin-smoke-latest.yml`（毎日 06:00 JST と手動。`runtime` の行列 `claude`・`codex`）が既に解決して入れている。ただし最新の CLI が入るのはジョブのホストでなく、`scripts/runtime-smoke-test.sh` が作る Docker イメージ `ai-plugins-runtime-smoke-<ランタイム>` の中である（ホストの `claude`・`codex` は最新ではない）。スクリプトはコンテナを消すがイメージは残すので、照らし合わせはそのイメージで `--help` を打つ。新しいワークフローも CLI の 2 回目の取得も足さない（CLI の取得は 1 日に各 1 回のまま）。

照らし合わせの材料（`scripts/plugin-deps.py`・`plugins/ndf/contracts/domains/cli-floors.json`・表が参照する `plugins/ndf/contracts/data/claude-cli.json`）は、配布元のコミットの同じ 1 つから取る。既存の checkout は `ref` が `develop` で `fetch-depth: 1` のため、作業ツリーには新設の `scripts/plugin-deps.py` が無いことがあり、`origin/main` も取得されていない。そこで 2 つ目の `actions/checkout`（`ref: ${{ inputs.floors_ref || 'main' }}`・`path: tmp/deps-floors`・`fetch-depth: 1`）を足し、3 つのファイルをすべてその作業ツリーから読む。`workflow_dispatch` に入力 `floors_ref`（既定 `main`。照らす表の ref）を足し、手動で別の ref を試すときもスクリプトと表と `claude-cli.json` が同じコミットから来る。スモークを試す `ref`（既存の入力）とは別にする。

足すステップは 2 つで、どちらも既存の `Upload smoke artifacts` の後・`Report the failure as an issue` の前に置き、`if: ${{ !cancelled() }}` で前のスモークの成否によらず打つ（失敗はジョブの失敗になり、報告のステップの `failure()` に届く）。

1. `Check CLI --help against the floors table`: `python3 tmp/deps-floors/scripts/plugin-deps.py cli-help --root tmp/deps-floors --runtime "$RUNTIME" --image "ai-plugins-runtime-smoke-$RUNTIME"`。`--image` を渡すと `Run` は `docker run --rm --entrypoint <executable> <image> <args>` で打つ。イメージが無い（ビルドで落ちた）ときは `cause: check_failed`・理由「最新の CLI のイメージが無い」で失敗にする
2. `Check gh --help against the floors table`（`if: ${{ !cancelled() && matrix.runtime == 'claude' }}`）: `--image` を渡さずに `--runtime gh` で、ホストの `ubuntu-latest` の `gh` を照らす

食い違いはこのジョブの失敗になり、既存の `report` のステップがランタイムごとの 1 件の課題に残す。#3691 の受け入れ条件 3 の「週 1 回の検査」は、この毎日の実行で満たす（実行の回数は既存のまま増えない）。

## 依存の検査のスクリプト

満たす決定: 決定 5・9・10・11・12、受け入れ条件 #3691 の 1・2・3・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `scripts/plugin-deps.py` | 新設 | `main(argv: list[str] \| None = None) -> int` | サブコマンドを振り分け、結果 JSON を標準出力へ 1 行で出す |
| `targets` | 同上 | 新設 | `targets(root: Path) -> list[Target]` | 追跡している lock から `renovate.json` の `ignorePaths` を除き、種類（uv・npm）とディレクトリの組を返す |
| `read_holds` | 同上 | 新設 | `read_holds(config: dict) -> list[Hold]` | `packageRules` の固定と対象の外の規則から 5 つの欄を読む。欄が欠けたら `ValueError` |
| `report` | 同上 | 新設 | `report(root: Path, fetch: Fetch, gh: Gh, today: date) -> Result` | すべての対象の脆弱性・解いてよい固定・プレリリースを選べる依存を集め、脆弱性の知らせを作る・書き直す・閉じる |
| `lock_diff` | 同上 | 新設 | `lock_diff(old: str, new: str) -> list[Change]` | 2 つの `uv.lock`（か `package-lock.json`）の本文から、変わったパッケージの名前・前と後のバージョンと、lock の中の依存の関係から引いた親を返す |
| `affected` | 同上 | 新設 | `affected(root: Path, base: str, head: str) -> Result` | `base` と `head` の lock の差分から、変わったパッケージを使うモジュールとテストを並べた一覧のデータ（PR 番号・head の SHA・本文の材料）を作る。GitHub へは書かない |
| `post_affected` | 同上 | 新設 | `post_affected(data: Path, run: WorkflowRun, gh: Gh) -> Result` | 既定のブランチのコードで、一覧のデータの PR 番号と head の SHA を確かめてから、目印のコメントを作るか書き直すか消す |
| `age` | 同上 | 新設 | `age(root: Path, base: str \| None, head: str \| None, fetch: Fetch, today: datetime, renovate_version: str \| None = None) -> Result` | 差分で新しく入ったバージョン（lock の各パッケージ・`UV_VERSION`・Renovate の版）の公開日を取得し、7 日に満たないか公開日が読めないものを拒む。脆弱性の例外の規則（規則 12）の組だけを通す |
| `action_pins` | 同上 | 新設 | `action_pins(root: Path, base: str, head: str, gh: Gh) -> Result` | 差分で変わった `uses:` の各行で、注記のタグが指すコミット（注記つきのタグは指す先まで辿る）が行の SHA と同じかを確かめる |
| `renovate_gate` | 同上 | 新設 | `renovate_gate(pr: int, head: str, bot: str, gh: Gh) -> Result` | Renovate の PR の今の head で、最も新しい `renovate/artifacts` の状態が `bot` の書いた `success` かを確かめる |
| `renovate_log` | 同上 | 新設 | `renovate_log(log: Path) -> Result` | Renovate の JSON の記録から、PR もブランチも作られなかった失敗の更新を並べる |
| `cli_help` | 同上 | 新設 | `cli_help(root: Path, run: Run, runtime: str, image: str \| None = None) -> Result` | `root` の `cli-floors.json` の見本の引数が、各 CLI の対応する `--help` の出力にあるかを照らす。`image` があればそのイメージの中の CLI を、無ければホストの CLI を打つ |
| `uv_pin` | 同上 | 新設 | `uv_pin(root: Path, fetch: Fetch) -> Result` | `UV_VERSION` のインストーラを `https://astral.sh/uv/<版>/install.sh` から 1 回だけ取得して sha256 を計算し、`plugins/ndf/bin/ndf` と `scripts/lib/ndf_wrappers.py` の 2 か所の版とハッシュを同じ値に書き直す |

`Fetch`・`Gh`・`Run` は外部（HTTP・`gh`・子プロセス）を差し替える引数で、単体テストは記録した応答を渡す。

- `report`
  - 事前条件: 各対象のディレクトリで `uv audit` か `npm audit` が打てる
  - 管理する Issue の見分け方: 本文の 1 行目が目印 `<!-- ndf:supply-chain-report -->` で、作成者が `github-actions[bot]` の Issue だけを管理の対象にする。ラベル `supply-chain` は一覧の絞り込みにだけ使い、ラベルだけで書き換え・閉じる先を選ばない。対象が 0 件なら作り、1 件なら書き直すか閉じる。2 件以上、または目印を持つのに作成者が違う Issue があれば、どれにも触れずに `cause: config_broken` で止まる（人が報告した脆弱性の Issue に同じラベルを付けても上書きされない）
  - 事後条件: 取得がすべて成り立てば、管理の対象の開いた Issue がちょうど 1 件で、本文は「脆弱性」「解いてよい固定」「プレリリースを選べる依存」の 3 つの表を持つ。どれも 0 行なら Issue は閉じている。取得が 1 つでも失敗したら、Issue は実行の前のまま
  - 副作用: `uv audit`・`npm audit`・PyPI の JSON API（固定の相手の採用できる版の `requires_dist`・直接の依存の版と公開日）・`gh issue list/create/edit/close`
- `affected` と `post_affected`
  - 事後条件: lock が変わった PR に、目印の行 `<!-- ndf:deps-affected -->` と対象の head の SHA を持つコメントがちょうど 1 件ある。一覧を作れなかったときも、同じ目印のコメントを対象の head の SHA と理由で書き直す（#3691 の E6。CI の合否は変えない）。lock の差分が無い PR では、目印のコメントが既にあれば消し、無ければ書かない（lock の変更を取り消した後に前の一覧を残さない）
  - 副作用: `git show <base>:<lock>`・`git show <head>:<lock>`（lock は読むだけで、`uv` を打たない）・artifact の作成。投稿は `post_affected` の `gh api` だけ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `report` | 脆弱性の取得の失敗 | ネットワーク・OSV の失敗・JSON の形の変化 | `uv audit` の終了コード（0 は 0 件・1 は 1 件以上・2 は質問合わせの失敗。M8〜M11）と JSON の `schema.version` と読み取り | 止める | `status: stopped`・`cause: transient` と対象のディレクトリ。終了コード 1 | Issue に触れない（I11）。再実行してよい |
| `report` | PyPI の取得の失敗 | ネットワーク・404 | `fetch` | 止める | 同上 | 同上 |
| `report` | 管理の対象の Issue を 1 件に決められない | 目印つきの Issue が 2 件以上・目印を持つ別の作成者の Issue | `gh issue list` の結果の目印と作成者 | 止める | `cause: config_broken` と Issue の番号。終了コード 1 | どの Issue にも触れない。人が余分な Issue の目印を外して再実行 |
| `read_holds` | 欄の欠け | 規則の書き損じ | 欄の読み取り | 止める | `status: stopped`・`cause: config_broken` と規則の番号。終了コード 1 | 規則を直して再実行 |
| `affected` | 読めない lock | lock の衝突のマーカー・形の違い | `lock_diff` | 一覧の代わりに理由を返す | `cause: invalid`。artifact に理由と head の SHA を置き、`post_affected` が目印のコメントを「一覧を作れない: <理由>（<head の SHA>）」に書き直す。ステップは 0 で終わり CI の合否を変えない（E6） | 直した lock を push すれば次の実行が一覧に戻す |
| `post_affected` | PR 番号か head の SHA が合わない | 古い実行・別の PR の artifact | PR の今の `head.sha` との比べ | 書かずに終える | `status: skipped`・理由。終了コード 0 | 新しい head の実行が書く |
| `age` | 7 日に満たない・公開日が読めない | Dashboard のチェックで待つ日数を飛ばした・Renovate の `--before` を外した再試行・取得の失敗 | 公開日の比べ | 止める | `cause: check_failed` とパッケージと版と公開日。終了コード 1 | 7 日たってから作り直すか、脆弱性の例外（決定 9）を足す |
| `action_pins` | 注記のタグと SHA が合わない | 注記の書き損じ・Dependabot が注記を書き換えない | タグの指すコミットとの比べ | 止める | `cause: check_failed` と行。終了コード 1 | 注記か SHA を直す |
| `renovate_gate` | `renovate/artifacts` が `success` でない | 解決できない上げ方・`uv-pin` の失敗 | 今の head の状態 | 止める | `cause: check_failed` と状態。終了コード 1 | Renovate が直したブランチを push し直すか、PR を閉じる |
| `cli_help` | 見本の引数が無い | CLI の引数の変化 | `--help` の出力の照らし合わせ | 止める | `status: stopped`・`cause: check_failed` と CLI と引数。終了コード 1 | 見本か NDF を直す |
| `uv_pin` | インストーラの取得の失敗 | ネットワーク・バージョンが無い | `fetch` | 止める | 終了コード 1。Renovate の `renovate/artifacts` が赤 | 2 つのファイルのどちらも書き換えない。再実行してよい |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `report` の固定の判定 | 種類 `相手の制約` で、相手の採用できる最新の版（公開から 7 日たった安定版）の `requires_dist` に、固定した依存の行が無い。マーカーはそのプロジェクトの環境（`requires-python` の下限の Python・`sys_platform == "linux"`・要る extra）で評価し、偽になる行は無いものとして扱う | 「解いてよい固定」に「制約の消失」として出す |
| 同上 | 同じ相手の行があり、固定した依存の公開から 7 日たった版のうち固定の版より新しいものの中に、新しい制約は許し `制約:` は許さない版が 1 つ以上ある | 「解いてよい固定」に「緩和」とその版を添えて出す |
| 同上 | 新しい制約と `制約:` が許す版の集まりが同じ（並びの違い・同じ意味の書き換え）か、新しい制約の方が狭い（強化） | 出さない（強化は結果 JSON の `items` に `tightened` として残すだけ） |
| 同上 | `見直す日:` が今日より前 | 「解いてよい固定」に出す（種類によらない） |
| 同上 | 種類 `脆弱性の例外` で、見直す日（外す日）が今日より前 | 「解いてよい固定」に出す（決定 9） |
| `report` のプレリリースの判定 | 直接の依存で、公開から 7 日たった最新の安定版が 365 日より古く、それより新しいプレリリースが 7 日たって公開されている | 「プレリリースを選べる依存」に出す。規則 6 の一覧に書けば、Renovate がプレリリースを候補に入れる |
| 同上 | 規則 6 の一覧にある依存で、7 日たった最新の安定版が 365 日以内になった | 「プレリリースを選べる依存」に「一覧から外してよい」として出す |
| 同上 | 7 日たった最新の安定版が 365 日以内（プレリリースがより新しくても） | 出さない。Renovate は既定（`ignoreUnstable: true`）でその安定版を選ぶ |
| `affected` | 変わったパッケージを使うモジュールが無い | 推移的な依存として、lock の依存の関係から引いた親（直接の依存まで）を添えて出す。消えたパッケージは `base` の lock の関係から、入ったパッケージは `head` の lock の関係から引く |
| `affected` | 使うモジュールはあるが、そのモジュールを使うテストが無い | モジュールの行に「テストが無い」と出す（行を落とさない） |

`affected` の lock の種類ごとの扱い:

| lock | 配布名から使う名前へ | 使う箇所の読み方 | 推移的な依存の親 |
| --- | --- | --- | --- |
| `uv.lock` | 既定は配布名の `-` を `_` にした名前。違うものは `scripts/plugin-deps.py` の対応表に持つ（`python-dotenv` → `dotenv`・`ruamel-yaml` → `ruamel.yaml`・`pyyaml` → `yaml` など）。CLI としてだけ使う依存（`ruff`・`pytest` など）は対応表に「import しない」と書く。4 つの `pyproject.toml` の直接の依存がすべて、使う名前が 1 つ以上見つかるか「import しない」に載ることを設定の検査のテストが縛る | そのプロジェクトのディレクトリの `*.py` を標準ライブラリの `ast` で読む `import` と `from … import` | `uv.lock` の `[[package]]` の `dependencies` を `tomllib` で読んだ逆向きの関係（`uv tree` を打たない。lock を書き換えうるため） |
| `package-lock.json` | 配布名のまま | そのディレクトリの `*.js`（`node_modules` を除く）の `require("<名前>")` と `import … from "<名前>"`（正規表現。`lib/deck-frame.js` の `require("pptxgenjs")` を拾う） | `packages` の各項目の `dependencies` の逆向きの関係 |

実装のレーンは、`plugins/ndf/uv.lock` で `python-dotenv` を、スライドの `package-lock.json` で `pptxgenjs` とその推移的な依存を変えた差分を 1 つずつ流し、両方の形で一覧が出ることを確かめる。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `targets` | 追跡している 6 つの lock から `docs/presentations/` を除いた 5 つ | `ignorePaths` が空なら 6 つ | `renovate.json` が無ければ止まる |
| `read_holds` | httpx と dbos の 5 つの欄 | 行の順が違うと欠けとして止まる | 欄を 1 つ消すと `config_broken` |
| `report` | 記録した `uv audit` の JSON（脆弱性あり）で表を作る。0 件で閉じる。固定の判定で、相手が行を消した・緩めたを出す | 見直す日が今日ちょうどは出さず、前日は出す。制約の並びだけを変えた・同じ意味に書き換えた・強めたは出さない。安定版が 365 日ちょうどと 366 日のプレリリースの判定 | 取得の失敗を 1 つ混ぜると Issue に触れない。同じラベルで目印の無い Issue・目印つきの 2 件目があると触れずに止まる |
| `lock_diff` | 記録した 2 つの lock の差分（uv と npm） | 足しただけ・消しただけのパッケージ | 衝突のマーカーを持つ lock で理由を返す |
| `affected` | 変わったパッケージを使うモジュールとテスト（`python-dotenv` → `dotenv`、npm の `pptxgenjs`） | 推移的な依存だけが変わった差分。テストの無いモジュール | lock が変わらず目印のコメントも無い差分で artifact を置かない。読めない lock で理由の artifact を置く |
| `post_affected` | 一覧のコメントを作る・書き直す | lock の差分が無くなった PR の目印のコメントを消す | 今の head と違う SHA の artifact で書かない |
| `age` | 公開から 7 日の版を通す | 6 日と 7 日の境 | 公開日の無い版・7 日に満たない版で止まる。脆弱性の例外の組は通し、例外と違う版は止める |
| `action_pins` | 注記のタグが指す SHA と同じ行を通す | 注記つきのタグを辿る | 注記と違う SHA で止まる |
| `renovate_gate` | 今の head で App の bot が書いた `success` を通す | Renovate 以外の PR で何もしない | 赤・未報告・保留・古い head の成功・別の主体が後から書いた成功で止まる |
| `cli_help` | 見本の引数がすべてある `--help`（トップレベルとサブコマンド） | 見本に無い引数が `--help` に増えても通る | 見本の引数を 1 つ消した `--help` で止まる。`image` を渡してイメージが無いと止まる |
| `uv_pin` | 記録したインストーラの本文の sha256 を 2 つのファイルに書く。取得は 1 回 | `UV_VERSION` が変わらなければ書かない | 取得の失敗でどちらも書き換えない |
| 設定の検査 | 2 つのファイルの `UV_VERSION` と `UV_INSTALLER_SHA256` の組が同じ | — | 片方の版かハッシュだけを変えると落ちる |

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
| `require` | `plugins/ndf/src/ndf/adapters/cli_floor.py` | 新設 | `require(name: str, argv0: str, env: Mapping[str, str] \| None = None, cwd: Path \| None = None) -> None` | 実際に起動する実行ファイルを、起動と同じ `PATH`（`env` の `PATH`、無ければ今のプロセスの `PATH`）と `cwd` で `shutil.which(argv0)` で解き、実パス（`os.path.realpath`）にする。その実行ファイルの `--version` を `env`・`cwd` を渡して 5 秒の期限で打ち、表の行 `name` の下限と比べ、古ければ `TooOld` を上げる。結果は実パスと `st_mtime_ns`・`st_size` の組をキーにプロセスの中で再利用する。表に無い `name` は何もしない |
| `TooOld` | 同上 | 新設 | `class TooOld(Exception)`（`name`・`path`・`found`・`floor` を持つ） | 理由の文「<名前> <見つけたバージョン>（<実パス>）は下限 <下限> より古い。<下限> 以上へ上げる」を持つ |

表に載るのは `gh`・`claude`・`codex` の 3 行で、Kiro と agy（`kiro-cli`・`agy`）は下限を持たない。`run_agent` は相手役のどのランタイムでも `require(spec.agent, spec.argv[0], …)` を呼ぶが、表に無い名前では何もしない（#3691 の受け入れ条件 3 の対象は 3 つの CLI だけ）。

`--version` の追加の起動は、1 つのプロセスで実行ファイル（実パス）ごとに 1 回で、期限は 5 秒である。期限を過ぎたら「バージョンを読めない」として起動を通す。短命のコマンド（`ndf launch-args`・`gh` を 1 回打つコマンド）では起動 1 回につき 1 回増える。複数の参加者を起動する経路（cross-review の `claude`・`codex`・`kiro-cli`）は、表にある実行ファイルごとに 1 回で、同じ実行ファイルを何度起動しても 2 回目からは打たない。実装のレーンは、`ndf launch-args` と cross-review の 1 周で `--version` の起動の回数を数え、この回数になることを確かめる。

起動の箇所の変える前と後（シグネチャは変えない）:

| 関数 | 変える前 | 変えた後 |
| --- | --- | --- |
| `agents._guarded_run`（`run_agent` の中） | `run_agent` が学習の設定と重い起動のスロットを確かめた後に起動する | 既存の前提（学習の設定・スロット）を通った後、`_start_and_watch` の前で `require(spec.agent, spec.argv[0], child_process_env(spec.env), spec.cwd)`。`TooOld` なら `AgentState.REFUSED` と理由で起動しない。学習の設定で拒む経路では `--version` も打たない |
| `claude.call_claude` と `plugin` の起動 | 同上 | 先に `require("claude", <起動の argv[0]>, <子の環境>, <作業ディレクトリ>)`。`TooOld` なら `Failure.REFUSED` と理由 |
| `github.token` | `gh auth token` を打つ | 打つ前に `require("gh", …)`。`TooOld` なら `GitHubError(理由)` |
| `measure_ci` の `raw` | `gh api` を打つ | 打つ前に `require("gh", …)`。`TooOld` なら `GhUnavailable(理由)` |
| `relay.wrapper` の起動・`relay.cli` の `execvp` | `claude` を起動する | 先に `require("claude", …)`。`TooOld` なら今の起動の失敗と同じ経路で理由を出して 0 以外で終わる |
| `relay.account_login._claude`（ログインの `_start` が使う） | `claude` が PATH に無ければ `LoginError("precondition")` | 見つけた後に `require("claude", path)`。`TooOld` なら `LoginError("precondition", 理由)` |
| `relay.account_login._auth`（`auth status --json` の認証状態の確かめと `logout` が共有する） | `claude auth <引数>` を打つ | 打つ前に `require("claude", claude, _env(environ, config_dir))`。`TooOld` なら状態の確かめでは `LoginError("precondition", 理由)`、`logout` では呼び出し元の `_acc_remove` が先に止めているので届かない |
| `relay.cli_account._acc_remove` | `detach` → `logout` → `unregister` の順で、`logout` が通らなくても登録を外す | `detach` の前に `require("claude", …)`。`TooOld` なら `status: stopped`・理由で 0 以外で終わり、登録も symlink も今のまま残す（古い CLI でログアウトできないまま登録だけを外さない） |
| `adapters/cli.py` の `launch_args`（`bin/ndf-claude` が `exec claude` の前に打つ） | 引数を items で出す | 先に `require("claude", "claude")`。`TooOld` なら `status: stopped`・理由で 0 以外で終わり、`ndf-claude` は既存の分岐でその回答を出して同じ終了コードで終わる（`exec claude` に進まない） |

### 内部のデータと定数

`cli-floors.json` は領域の表の形（`summary`・`owner`・`columns`・`rows`）で持つ。

| 列 | 型 | 中身 |
| --- | --- | --- |
| `id` | 文字列 | `gh`・`claude`・`codex` |
| `executable` | 文字列 | 実行ファイルの名前。`claude`・`codex` は `domains/runtimes.json` の `executable` と同じ値（表の参照の検査で縛る） |
| `version_args` | 文字列の配列 | `["--version"]` |
| `version_pattern` | 文字列 | 出力からバージョンを接尾辞まで取り出す正規表現（`gh version (\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)`・`(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?) \(Claude Code\)`・`codex-cli (\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)`。M15 の出力の形）。比べは `semver` で行い、下限と同じ数字のプレリリース（`0.50.0-alpha.1` と下限 `0.50.0`）は下限に満たないとして止める |
| `min_version` | 文字列 | 下限（「未確認のまま残ること」の決め方） |
| `help` | 配列 | 照らす見本。`{"args": [...], "flags": [...]}` の並びで、`args` はその `--help` を打つ引数（`["--help"]`・`["auth", "status", "--help"]`・`["plugin", "install", "--help"]` など）、`flags` はその出力にあるべき引数。`claude` はトップレベルの `--help` に `{"args": ["--help"], "table": "data/claude-cli.json"}` で今の表を使い、NDF が打つサブコマンド（`auth login`・`auth status --json`・`auth logout`・`plugin` の各サブコマンド）は、サブコマンドの行を別に持つ。`src/ndf` の中で `claude`・`codex`・`gh` に渡すサブコマンドとフラグの組がすべて見本にあることを、起動の箇所を探すテストが縛る |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `require` | 下限より古い | 利用者の CLI が古い | 比べ | 呼び出し元へ返す（`TooOld`） | 起動の箇所の形で理由を出し、0 以外で終わる | CLI を上げて再実行 |
| `require` | バージョンを読めない | 出力の形の変化・起動の失敗 | 正規表現 | 既定値で続ける（比べずに起動を通し、警告を標準エラーへ 1 行） | 警告の 1 行 | 起動の失敗は今の箇所の扱いに任せる |

バージョンを読めないときに止めないのは、CLI が無い・壊れているときの扱いを今の起動の箇所が既に持つためである。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `require` | 下限より新しいバージョンで何も上げない | 下限と同じバージョンで上げない。1 つ古いバージョンで上げる。下限と同じ数字のプレリリースで上げる | 読めない出力・5 秒の期限切れで警告だけ出して通す。同じ実行ファイルの 2 回目の呼び出しで `--version` を打たない。`env` の `PATH` で別の実行ファイルに解けるときは、その実行ファイルを改めて比べる。表に無い `kiro`・`agy` で何も打たない |
| 起動の箇所 | 古いバージョンを返す偽の CLI を PATH の先に置くと、各エントリポイント（`bin/ndf-claude`・`ndf` の各コマンド・アカウントのログイン・状態の確かめ・登録の解除）が起動せずに理由を返す。登録の解除では登録が残る | 学習の設定で拒む経路で `--version` を打たない | `src/ndf` の中で表の `executable` を `subprocess` か `exec` に渡す関数と、`bin/` の中で表の `executable` を `exec` するシェルが、`require`（シェルでは `ndf launch-args`）を通らずに足されるとテストが落ちる |

## Dependabot の設定とワークフローの参照

満たす決定: 決定 4・13、受け入れ条件 #3691 の 4

- `dependabot.yml` の `github-actions` の項目に `cooldown: { default-days: 7 }` を足す。頭の注記を「Python と npm の依存は Renovate（`.github/renovate.json`）、Actions はここ」に書き直す
- 外部の Action の参照を `uses: <所有者>/<名前>@<40 桁の SHA> # v<具体的な版>` にする。初回も 7 日を守るため、動くメジャーのタグ（`v7` など）を今の指す先へ解かない。各 Action の `gh api repos/<所有者>/<名前>/releases` から、今のメジャーの中で `published_at` が実装の日の 7 日より前の最新のリリース（下書きとプレリリースを除く）を選び、そのタグ（`v7.0.3` など）が指すコミットを `gh api repos/<所有者>/<名前>/git/ref/tags/<タグ>` で引く（注記つきのタグは指す先のコミットまで辿る）。注記はその具体的な版にする
- 設定の検査のテストは、`.github/workflows/*.yml` の `uses:` のうち `./` で始まらないものすべてが `@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+` の形であることを縛る（形だけ）。SHA と注記の対応は、`uses:` の行を変えた PR で `script-structure.yml` の `action-pins` のステップがタグの指すコミットと比べて縛る（初回の PR と Dependabot の PR の両方に効く）

## 依存の定義と lock と一致の検査

満たす決定: 決定 4・6・7、受け入れ条件 #3370 の 4・5・10・12、#3691 の 5

最初のバージョンアップ（決定 7）は次の順で行う。`uv lock --upgrade` だけでは `==` で固定した直接の依存と dev グループの宣言を越えられない（uv は宣言の制約の中でしか上げない）ため、宣言を先に書き換える。

1. 4 つの `pyproject.toml` の `[tool.uv]` に `exclude-newer = "7 days"` を足す
2. 各プロジェクトの `==` で固定した直接の依存（`[project] dependencies`・`[dependency-groups]` の dev を含む）ごとに、PyPI の JSON API の `releases` の各ファイルの `upload_time` を読み、最初のアップロードが実装の時点の 7 日より前のバージョンのうち、決定 106 の上げる先（最新の安定版。それが 365 日より古く、より新しいプレリリースがあればそのプレリリース）を選ぶ。そのバージョンが今の `==` の範囲の外なら（major を含む）、`==` をそのバージョンに書き換える。`dbos`・`httpx` の行と、`sqlalchemy`（DBOS が同じバージョンを使う）は書き換えない
3. 各ディレクトリで `uv lock --upgrade` を打ち、宣言から lock を作り直す（推移的な依存も 7 日の範囲で上がる。`==` の行はその版に止まる）。根は playwright-kit をパスの依存で持つので、playwright-kit の後に根を作り直す
4. 解決できなければ、解決を拒んだ依存の `==` を 1 つ前の候補に戻して 3 をやり直し、戻した依存と理由を PR 本文に書く
5. `git diff` の lock から、バージョンが変わった依存（直接と推移的）の一覧を PR 本文に書く。`dbos==3.4.0`・`httpx==0.28.1` が変わっていないことを見る

- `plugins/ndf/pyproject.toml` の頭の注記を、作り直しの間の注記（プロジェクト名 `ndf-rebuild`・`11.0.0-dev.N`）から、バージョンの選び方（決定 106 と 7 日）と上げる操作（依存の一覧の Issue と `.github/renovate.json`）の 3 行へ直す
- スライドの npm の lock のディレクトリに `.npmrc` を置き、`min-release-age=7` を書く（npm の解決でも 7 日を守る。決定 6）
- `script-structure.yml` の 97 行の `uv lock --check --project plugins/ndf` を、プロジェクトごとのステップにする。今の `if: steps.scope.outputs.ndf == 'true'` は `NDF_PATHS`（根・playwright-kit・mcp-serena の定義と lock を含まない）で決まり、そのまま残すと NDF 以外の定義だけを変えた PR が検査を抜ける。そこで `scope` のステップに、プロジェクトごとの入力 `<ディレクトリ>/pyproject.toml` と `<ディレクトリ>/uv.lock` で決まる出力（`lock_root`・`lock_ndf`・`lock_playwright`・`lock_serena`）を足し、各 `uv lock --check --project <ディレクトリ>` をその出力で起動する。playwright-kit の入力が変わったときは根も検査する（根が playwright-kit をパスの依存で持つ）。merge queue と push では 4 つとも常に打つ。実装のレーンは、4 つの定義のそれぞれだけを書き換えた PR で対応する検査が落ちること、merge queue で 4 つとも打たれることを確かめる
- 脆弱性の直しの例外の形（決定 9）: uv では `exclude-newer-package = { "<パッケージ>" = "<日時>" }  # <勧告の ID> 外す日 <YYYY-MM-DD> 版 <版>`、npm では `package.json` の `overrides` に `"<パッケージ>": "<版>"`。どちらも同じ勧告の ID・版・外す日を持つ規則 12 を `renovate.json` に置き、組が揃わないと設定の検査のテストが落ちる

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `scripts/plugin-deps.py` の各サブコマンド | 統一の結果 JSON の 1 行（`tool: "plugin-deps"`・`status`・`summary`・`items`） | 標準出力 | GitHub の token・App の秘密鍵 |
| `cli_floor.require` | バージョンを読めなかったときの 1 行 | 標準エラー | — |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `report` の定時の実行と手動の実行 | 脆弱性の知らせの Issue | ワークフローの `concurrency: supply-chain-report` | 同じ入力なら同じ本文。途中で止まっても次の実行が書き直す |
| Renovate の定時の実行と手動の実行 | 依存の一覧の Issue・更新のブランチ | `renovate.yml` の `concurrency: renovate`（`cancel-in-progress: false`）でリポジトリに 1 つずつ | 後の実行が前の実行の書いた承認の状態を読み直す。チェックが効くのは次の定時か手動の実行 |
| 同じ lock を触る Renovate の更新（lock ごと・ruamel-yaml の横断・lock の作り直し） | `uv.lock`・`package-lock.json` | `branchConcurrentLimit: 1` で同時に 1 本。順は `prPriority` | 前の PR がマージされた後の実行が、次の 1 本を `main` の上で作る |
| `post_affected` の同じ PR への連続の実行 | PR のコメント | `deps-affected-comment.yml` の `concurrency: deps-affected-<PR 番号>`（`cancel-in-progress: false`）と、今の head の SHA との比べ | 目印の行で 1 件を引いて書き直す。古い head の結果は書かない |
| Renovate の更新の PR と DBOS の PR | `uv.lock` | 先に `main` に入った PR の lock を正とし、後の PR が lock を作り直す（I8） | Renovate は PR 本文のチェックで作り直す |
