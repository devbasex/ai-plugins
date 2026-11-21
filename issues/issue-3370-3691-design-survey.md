# 依存の更新の外部の調査（#3370 #3691）

[issue-3370-3691-design.md](./issue-3370-3691-design.md) の「外部の調査」の表と 3 案の比較である。LIB1〜LIB8 と M1〜M4 は前の設計（設計 PR #3522、2026-11-14）の調べで、`plugins/ndf-next` は今の `plugins/ndf` に当たる。LIB9〜LIB22 と M5〜M15 は 2026-11-21 に調べた（Renovate の文書とソースは本体のタグ 44.257.5 の時点）。

## ライブラリとサービス

| # | 候補 | バージョン | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | Renovate（本体） | 44.239.1 | AGPL-3.0 | 2026-11-13 | 無し | 満たす（下の比較の表）。決定 106 の「古すぎる安定版はプレリリースへ」は、今のバージョンの年齢を基準にした近い形で表す（決定 4） | https://docs.renovatebot.com/configuration-options/ ・ https://docs.renovatebot.com/key-concepts/minimum-release-age/ ・ https://docs.renovatebot.com/modules/manager/pep621/ ・ https://docs.renovatebot.com/key-concepts/dashboard/ ・ `gh api repos/renovatebot/renovate`・`gh api repos/renovatebot/renovate/releases/latest`・`gh api repos/renovatebot/renovate/contents/docs/usage/configuration-options.md`（2026-11-14） |
| LIB2 | Renovate を GitHub Actions で動かすアクション（`renovatebot/github-action`） | v46.3.13 | AGPL-3.0（本体と同じ組織。アクションのライセンスは未確認） | 2026-11-09 | 無し | 満たす。token は PAT・GitHub App・`GITHUB_TOKEN` のどれか。`GITHUB_TOKEN` では Renovate の PR の上のワークフローが手で承認するまで走らない | https://github.com/renovatebot/github-action ・ `gh api repos/renovatebot/github-action/releases/latest`（2026-11-14） |
| LIB3 | Dependabot（version updates・security updates・alerts） | GitHub に組み込み | —（GitHub のサービス） | — | 無し | 一部を満たす。待つ日数・依存ごとの止めは持つが、止めた理由を書く項目・Issue 1 件での知らせ・プレリリースへ上げる規則を持たない | https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference ・ https://docs.github.com/en/code-security/dependabot/ecosystems-supported-by-dependabot/supported-ecosystems-and-repositories ・ https://docs.github.com/en/code-security/dependabot/dependabot-security-updates/about-dependabot-security-updates ・ https://docs.github.com/en/code-security/dependabot/dependabot-alerts/about-dependabot-alerts （2026-11-14） |
| LIB4 | 自作（PyPI の JSON API・packaging・uv の上の `scripts/plugin-deps.py`。#3347 の決定 107） | — | —（このリポジトリの MIT） | — | — | 満たす（決定 106 をそのまま表せる）。脆弱性・推移的な依存・Actions と CLI のバージョンは持たない | `issues/issue-3347-design.md` の決定 107・LIB20・LIB22（2026-11-14） |
| LIB5 | uv の `exclude-newer` | 0.13.7 | MIT / Apache-2.0 | — | 無し | 満たす。相対の期間（`"7 days"` など）を書け、比べる日時はパッケージのファイル（wheel・sdist）ごとのアップロードの日時である。依存ごとの例外（`exclude-newer-package`）もある | https://docs.astral.sh/uv/reference/settings/ ・ https://docs.astral.sh/uv/concepts/resolution/ （2026-11-14）・M1・M2 |
| LIB6 | このリポジトリの依存グラフと Dependabot alerts | — | — | — | — | 今は Python の依存を見ていない。Dependabot alerts は有効だが、依存グラフのマニフェストは 0 件で、開いている知らせも 0 件 | M3 |
| LIB7 | `plugins/ndf-next/pyproject.toml` の今のバージョン | — | — | — | — | githubkit は 0.17.1、DBOS は 3.4.0 に上がっている（コミット `2a7b00260`・`1200b0488`）。#3347 の「14 本の最新のバージョン」の表で上げる先が今と違うのは 11 本（DBOS と githubkit と httpx を除く） | `git log -- plugins/ndf-next/pyproject.toml`・`issues/issue-3347-design.md` の表（2026-11-14） |
| LIB8 | Renovate の依存の一覧の Issue を閉じる設定と、lock の作り直しの失敗の知らせ | 44.239.1 の文書 | AGPL-3.0 | — | — | 一部を満たす。`dependencyDashboard.autoclose` を `true` にすると「残る PR が無くなったとき」に Issue を閉じる。承認を待つ更新が残るときに閉じるかは文書に無い。パッケージマネージャが lock の作り直しを拒むと「artifact error」になり、PR に警告のコメントと `renovate/artifacts` のチェックが付く。`statusCheckWhen.artifactError` を `"always"` にすると成功で緑・失敗で赤を出し、必須のチェックにできる | `gh api repos/renovatebot/renovate/contents/docs/usage/configuration-options.md`（blob `06410a943f7f`、2026-11-14）の `dependencyDashboard.autoclose`・`minimumReleaseAgeBuffer`・`platformAutomerge`・`statusCheckWhen` の節 |
| LIB9 | Renovate（本体） | 44.257.5 | AGPL-3.0 | 2026-11-20 | 無し | 最新は 44.257.5（2026-11-20 公開）。LIB1 の 44.239.1 より新しい | `gh api repos/renovatebot/renovate/releases/latest`（2026-11-21） |
| LIB10 | `renovatebot/github-action` | v46.3.14 | AGPL-3.0（`license` のファイル。12.0.0 より前の部分だけ MIT の表示を残す） | 2026-11-16 | 無し | `renovate-version` を渡さないときは既定の `'44'` を Docker のタグに使い、実行の時点の 44 系の最新を動かす（7 日を待たない）。決定 2 が既定に任せない理由 | `gh api repos/renovatebot/github-action/contents/action.yml?ref=v46.3.14`・`contents/src/docker.ts?ref=v46.3.14`・`contents/license?ref=v46.3.14`（2026-11-21） |
| LIB11 | Renovate の `description` と設定の検証 | 44.257.5 | AGPL-3.0 | 2026-11-20 | 無し | `description` は文字列の配列（文字列 1 つも受ける）で、`packageRules` の中にも書ける。未知のキーは `Invalid configuration option` で落ちる。検証は `npx --yes --package renovate@<版> -- renovate-config-validator --strict --no-global <ファイル>`（ファイルを渡すと全体の設定として読むため `--no-global` が要る） | Renovate 44.257.5 の `lib/config/options/index.ts`・`docs/usage/configuration-options.md`（`gh api repos/renovatebot/renovate/contents/<パス>?ref=44.257.5`、2026-11-21）・M5〜M7 |
| LIB12 | Renovate の lock の作り直し（`lockFileMaintenance`）と uv | 44.257.5 | AGPL-3.0 | 2026-11-20 | 無し | pep621 の uv の処理が対応し、作り直しでは `uv lock --upgrade` を打つ。既定は無効。`dependencyDashboardApproval` を受ける。作り直しで上がるパッケージには日時が無く、`minimumReleaseAge` を置くと `renovate/stability-days` が待ちのまま残る | Renovate 44.257.5 の `lib/modules/manager/pep621/processors/uv.ts`・`docs/usage/key-concepts/minimum-release-age.md`（2026-11-21） |
| LIB13 | Renovate の `customManagers` と `postUpgradeTasks` | 44.257.5 | AGPL-3.0 | 2026-11-20 | 無し | `UV_VERSION="0.13.7"` を正規表現で拾い `github-releases` の `astral-sh/uv` で上げる設定は検証を通った（M5）。`postUpgradeTasks` はコミットの前にコマンドを打ち、打てるのは全体の設定にだけ書ける `allowedCommands`（旧名 `allowedPostUpgradeCommands`、既定は空）に一致したものだけ | Renovate 44.257.5 の `docs/usage/configuration-options.md`・`docs/usage/self-hosted-configuration.md`（2026-11-21）・M5 |
| LIB14 | Renovate の PyPI のリリースの日時 | 44.257.5 | AGPL-3.0 | 2026-11-20 | 無し | そのバージョンのファイルの `upload_time` の最も早いもの（最初のアップロード）を使う。前の設計で確かめられなかった点 | Renovate 44.257.5 の `lib/modules/datasource/pypi/index.ts`（2026-11-21） |
| LIB15 | Renovate の脆弱性の PR（`vulnerabilityAlerts`・`osvVulnerabilityAlerts`） | 44.257.5 | AGPL-3.0 | 2026-11-20 | 無し | どちらも `schedule`・`dependencyDashboardApproval`・`minimumReleaseAge` を飛ばす（`force` で強く当てる）。lock を作り直す側が待つ日数で拒めば artifact error。`osvVulnerabilityAlerts` は実験的で、対象は直接の依存（uv の pep621 だけが例外） | Renovate 44.257.5 の `lib/config/options/index.ts`・`lib/workers/repository/init/vulnerability.ts`・`docs/usage/configuration-options.md`（2026-11-21） |
| LIB16 | Dependabot の `cooldown` | GitHub に組み込み | — | — | 無し | キーは `default-days`（既定 3）・`semver-*-days`・`include`・`exclude`。github-actions は `default-days` を受け、`semver-*-days` は受けない。version updates だけに効く | https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference （取得の要約、2026-11-21） |
| LIB17 | `actions/create-github-app-token` | v3.4.0 | MIT | 2026-11-18 | 無し | 入力は `client-id`（`app-id` は非推奨）と `private-key`、権限ごとの `permission-<名前>` | `gh api repos/actions/create-github-app-token/contents/action.yml?ref=v3.4.0`（2026-11-21） |
| LIB18 | uv の `exclude-newer-package` | 0.13.7（最新 0.13.9） | MIT / Apache-2.0 | 2026-11-17 | 無し | `[tool.uv] exclude-newer-package = { <名前> = <値> }`。値は `false`・日時・日付・期間を受ける。`true` は警告だけで `[tool.uv]` 全体を読まず、終了コード 0 のまま（M14） | `uv lock --help`（2026-11-21）・M13・M14 |
| LIB19 | `uv audit` | 0.13.7 | MIT / Apache-2.0 | 2026-11-17 | 無し | 実験的な機能（打つたびに警告）。JSON の出力も実験的で `schema.version` は `"preview"`。質問合わせ先は OSV。終了コードは 0 件で 0、1 件以上で 1、質問合わせの失敗で 2 | `uv audit --help`（2026-11-21）・M8〜M11 |
| LIB20 | grimp（import のグラフ） | 3.20 | BSD-2-Clause | 2026-11-13 | 無し | import できるパッケージの中の import のグラフを作り、外のパッケージも節にできる。`scripts/` とテストのようにパッケージでないファイルは見ない。決定 10 で使わない | https://pypi.org/pypi/grimp/json （2026-11-21） |
| LIB21 | pytest-testmon | 2.3.1 | MIT | 2026-11-19 | 無し | 変わったファイルとメソッドから打つテストを選ぶ。依存のバージョンの変化からは選ばない。決定 10 で使わない | https://pypi.org/pypi/pytest-testmon/json （2026-11-21） |
| LIB22 | semver（NDF の既存の依存） | 3.1.0（定義）・3.1.2（最新） | BSD-3-Clause | 2026-11-15 | 無し | バージョンの比べに使う。下限の比べ（決定 11）が新しい依存を足さずに使える | `plugins/ndf/pyproject.toml:34`・https://pypi.org/pypi/semver/json （2026-11-21） |

## 実測

（一時ディレクトリに `plugins/ndf-next` の `pyproject.toml` と `uv.lock` をコピーして打った。uv 0.13.7、2026-11-14）:

| # | 打ったこと | 結果 |
| --- | --- | --- |
| M1 | `[tool.uv]` に `exclude-newer = "7 days"` を足して `uv lock`、続けて引数なしの `uv lock --check` | lock の `[options]` に `exclude-newer-span = "P7D"` が入り、`uv lock --check` は終了コード 0 |
| M2 | M1 の後、定義の semver を 3.1.1（2026-11-09 公開）にして `uv lock --check` | 解決を拒んだ: `semver was filtered by exclude-newer to only include packages uploaded before 2026-11-07T23:18:14Z`。定義の python-dotenv を 1.2.4（2026-10-01 公開）にしたときは `The lockfile at uv.lock needs to be updated, but --check was provided.` で終了コード 1 |
| M3 | `gh api repos/devbasex/ai-plugins/vulnerability-alerts`・`gh api repos/devbasex/ai-plugins/dependabot/alerts`・GraphQL の `dependencyGraphManifests`・`gh api repos/devbasex/ai-plugins/dependency-graph/sbom` | 204（alerts は有効）・開いている知らせ 0 件・マニフェスト 0 件・SBOM は 404 |
| M4 | M1 の lock を作った後、日がたって比べる日時が進んだ状態を、定義を `exclude-newer = "1 day"`、lock の `exclude-newer-span` を `"P1D"` に替えて（lock の他の行は変えずに）作り、引数なしの `uv lock --check` | 終了コード 0（`Resolved 58 packages`）。lock は比べる日時を絶対の日時で持たない（`exclude-newer = "0001-01-01T00:00:00Z"` は効かない値と注記される）。uv の文書も「時刻が変わっても lock を更新しない。`--upgrade` か `--refresh` で解決し直したときに日時を進める」とする（ https://docs.astral.sh/uv/concepts/resolution/ 、2026-11-14） |
| M5 | 決定 5・6 の形の設定（`description` の配列、`lockFileMaintenance` の中の `dependencyDashboardApproval` と `minimumReleaseAge: null`、正規表現の `customManagers` と `github-releases`）に `renovate-config-validator --strict`（Renovate 44.257.5） | `Config validated successfully`、終了コード 0 |
| M6 | `packageRules` の中に `reason`、最上位に未知のキーを置いて同じコマンド | `Invalid configuration option: packageRules[0].reason` ほか、終了コード 1 |
| M7 | 旧名の `stabilityDays` だけの設定を `--strict` ありとなしで | `Config migration necessary`。`--strict` ありは 1、なしは 0 |
| M8 | `uv audit --frozen --project plugins/ndf --output-format json` | 終了コード 1。最上位のキーは `schema`・`summary`（`audited_packages: 57`・`vulnerabilities: 14`）・`vulnerabilities`・`adverse_statuses`。1 件のキーは `dependency`（`name`・`version`）・`id`・`aliases`・`fix_versions` など。14 件すべて filelock 4.0.3（`fix_versions: ["4.2.1"]`） |
| M9 | M8 を既定の出力の形で | 終了コード 1 |
| M10 | 届かない proxy を置いて M8 | 終了コード 2。標準出力は空。標準エラーに `Request failed after 3 retries`（`api.osv.dev`） |
| M11 | `uv audit --frozen --project plugins/mcp/mcp-serena --output-format json` | 終了コード 0、`vulnerabilities: 0` |
| M12 | `plugins/ndf/skills/release/assets/slides` で `npm audit --package-lock-only --json` と `npm outdated --json` | `npm audit` は終了コード 1・high 2 件（推移的な image-size を通した pptxgenjs 4.0.1。直しは 4.0.0 への major の戻し）。`npm outdated` は 0 件（追跡していない `node_modules` を読む） |
| M13 | 一時のプロジェクトで `exclude-newer-package = { httpx = <値> }` を `false`・日時・日付・期間で `uv lock --offline` | 4 つとも終了コード 0。日付は翌日の 0 時として lock に入る |
| M14 | M13 の値を `true` に | 警告を出して終了コード 0 のまま、lock に `exclude-newer` が入らない |
| M15 | `claude --version`・`codex --version`・`gh --version` | `2.1.330 (Claude Code)`・`codex-cli 0.167.0`・`gh version 2.103.3 (2026-10-26)` |

## 3 案の比較

各欄の出所は上の表の番号で示す。2026-11-14 の比較に、2026-11-21 の調べで分かったことを「追記」として足した。

各欄の出所は上の表の番号で示す。文書の範囲で確かめられなかった欄は「未確認」とし、選んだ理由に使わない（要求の前提 4）。

| 観点 | Renovate（LIB1・LIB2） | Dependabot（LIB3） | 自作（LIB4） |
| --- | --- | --- | --- |
| 1. 7 日待つ規則と `exclude-newer = "7 days"` の両立 | 両立する。`minimumReleaseAge: "7 days"` でバージョンごとのリリースの日時から待ち、満たないバージョンは PR を作らず「待っている」として依存の一覧の Issue に出す（既定の `internalChecksFilter: "strict"`）。Renovate は uv の設定を読まず、文書は「Renovate とパッケージマネージャの両方に置く」ことを勧める。lock を作り直すのは uv なので、`exclude-newer` も効く（M2）。PyPI の日時を最初のアップロードで数えるかは未確認 | 両立する。`cooldown` の `default-days: 7`（設定しなくても 3 日）。数える日時は「release」とだけあり、アップロードの日時かは未確認。security updates には効かない | 両立する。最初のアップロードの日時から 7 日（`COOLDOWN_DAYS`）を数え、`bump` は uv で lock を作る |
| 決定 106 の上げる先の規則（参考） | 近い形で表せる。`ignoreUnstable`（既定は安定版だけ）を `matchCurrentAge: "> 365 days"` の規則で `false` にすると、今のバージョンが 365 日より古い依存だけプレリリースを候補にする。基準は「最新の安定版の年齢」ではなく「今のバージョンの年齢」である | 表せない。プレリリースを候補にする規則の記述は文書に無い | そのまま表せる |
| 2. 既知の脆弱性の知らせ | 持つ。`vulnerabilityAlerts` は GitHub の依存グラフと Dependabot alerts を読み、待ちと予定を飛ばして直しの PR を作る。`osvVulnerabilityAlerts` は OSV のデータベースを手元で引き、uv のプロジェクト（pep621）では直接の依存の外も対象にする | 持つ。alerts と security updates。ただしこのリポジトリの依存グラフは今マニフェストを 0 件しか持たず、Python の依存を見ていない（M3）。uv.lock を依存グラフが読むかは未確認 | 持たない |
| 3. すべての lock と推移的な依存 | すべての `pyproject.toml` と `uv.lock` を見つける（`includePaths` で絞れる）。推移的な依存は、脆弱性の直しのときと、lock を作り直す定期の更新（`lockFileMaintenance`、既定で無効）で上げる | 対象のディレクトリを `directory` か `directories` で並べる（`directories` の詳しい記述は未確認）。推移的な依存は `allow` の `indirect` に uv が含まれる | `--project` で 1 つずつ。推移的な依存は見ない |
| 4. GitHub Actions のバージョンと CLI のバージョン | 両方を扱える。Actions は `github-actions` の仕組み（`with:` のバージョンも含む）、ワークフローの中の CLI のバージョンは正規表現で拾う独自の仕組み（`customManagers`）。Actions を書き換えるには token に `workflow` の権限が要る | Actions は扱える（今このリポジトリで使っている）。ワークフローの中の CLI のバージョンを上げる機能は文書に無い | 扱わない |
| 5. httpx のような固定と理由の置き場 | 依存ごとの規則（`packageRules`）に、範囲（`allowedVersions`）か止め（`enabled: false`）と、理由の文（`description`）を書ける。止めた依存が依存の一覧の Issue にどう出るかは未確認 | 止めは `ignore` で書ける。理由を書く項目は文書に無い（YAML のコメントだけ） | `[tool.ndf.deps.hold]` に理由を書き、検査の結果に「固定している」と出す |
| 知らせの形（受け入れ条件 7） | 1 件の Issue（Dependency Dashboard）にまとめる。`dependencyDashboardApproval` で、チェックを入れるまで PR を作らない。脆弱性の直しの PR は承認を待たない。閉じるのは `dependencyDashboard.autoclose` で「残る PR が無くなったとき」（承認を待つ更新が残るときの扱いは未確認。LIB8）。PyPI の取得が失敗したときに Issue を書き換えずに失敗で終わるかは未確認 | 更新ごとか、まとめた単位ごとの PR。Issue 1 件で知らせる設定は文書に無い | 印つきの Issue を 1 件だけ作るか書き直す |
| 要る権限と秘密 | アクションで動かすなら token が要る。PAT（人に結びつく）・GitHub App（アプリの作成と秘密鍵）・`GITHUB_TOKEN`（PR の上のワークフローが手で承認するまで走らず、リポジトリの設定の変更が要る）のどれか | 要らない（GitHub に組み込み） | `GITHUB_TOKEN` の Issue の書き込みだけ |
| 自分で保守するコード | 設定の JSON とワークフローの 1 本 | 設定の YAML | スクリプト・単体テスト・ワークフロー |

追記（2026-11-21）:

- 観点 1: Renovate は PyPI の日時を最初のアップロードで数える（LIB14）。Renovate のバージョンを action の既定に任せると 7 日を待たない（LIB10）。Dependabot の `cooldown` は github-actions でも `default-days` を受ける（LIB16）
- 観点 2: Renovate の脆弱性の PR は承認・待つ日数・予定を飛ばす（LIB15）。`uv audit` は OSV を引き、すべての lock のパッケージを見るが実験的である（LIB19）
- 観点 3: Renovate の lock の作り直しは uv で `uv lock --upgrade` を打ち、推移的な依存を上げる（LIB12）
- 観点 4: 正規表現の仕組みでコードの中の `UV_VERSION` を拾える（LIB13・M5）
- 観点 5: `description` は文字列の配列で、欄を行で持てる。未知のキーは検証で落ちる（LIB11・M6）

## 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| Renovate の依存の一覧の Issue に、止めた依存と取得の失敗がどう出るか・遅れが無くなったときに Issue を閉じるか | https://docs.renovatebot.com/key-concepts/dashboard/ ・ https://docs.renovatebot.com/configuration-options/ ・ 設定の文書の原文（LIB8） | 閉じる設定は LIB8 で見つけた。閉じる条件は「残る PR が無くなったとき」で、承認を待つ更新との関係と、止めた依存と取得の失敗の出方は記述を見つけられなかった。「未確認のまま残ること」へ移す |
| Mend の Renovate アプリの導入の手順と、リポジトリの中身の扱い | 試していない | 取得の試みが上限の 30 回に近づいたため止めた。決定 2 はアプリを採らない理由に、ここを使わない |
| Dependabot が uv.lock を依存グラフに載せるか・`directories` の詳しい形・`ignore` の `versions` の形 | https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference ・ https://docs.github.com/en/code-security/dependabot/ecosystems-supported-by-dependabot/supported-ecosystems-and-repositories | ページが長く、中盤を要約でしか読めなかった |
| Dependabot が SHA で固定した Action を上げ、バージョンの注記も書き換えるか | dependabot-options-reference（先頭の範囲）・keeping-your-actions-up-to-date-with-dependabot・このリポジトリの Dependabot の履歴 | 読めた範囲に記述が無く、このリポジトリに SHA で固定した参照は 0 件だった。取得の上限に当たった |
| uv の settings の文書の `exclude-newer-package` の原文 | `gh api repos/astral-sh/uv/contents/docs/reference/settings.md?ref=0.13.7` | 404。答えは実測（M13・M14）と `uv lock --help` で埋めた |
| Renovate の `minimumReleaseAgePackageManagerSync` が uv の `exclude-newer` を読むか | Renovate 44.257.5 の `minimum-release-age.md` と `uv.ts` | 名が挙がるのは npm・pnpm・Yarn・Poetry・Cargo だけで、`uv.ts` に `exclude-newer` の語は無かった |
| `renovatebot/github-action` で `allowedCommands` を渡す形 | `lib/config/options/index.ts` の定義 | 環境変数の名前と action からの渡し方は確かめていない |

調べた量: 2026-11-14 は候補 8 件・取得の試み 32 回。2026-11-21 は候補 8 件（Renovate 本体・`renovatebot/github-action`・Dependabot・`actions/create-github-app-token`・uv・grimp・pytest-testmon・semver）・取得の試み 33 回。
