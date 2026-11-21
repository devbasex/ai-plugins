# 依存の更新: すべての lock・Actions・外部 CLI を公開から 7 日たった最新の安定版に保ち、遅れと脆弱性を週 1 回知らせる

このリポジトリの依存は、Renovate（依存の更新の PR を作るツール）が対象のすべての lock の遅れを 1 件の Issue（依存の一覧の Issue）に知らせ、
チェックを入れた更新を lock ごとに 1 本の PR で上げる。GitHub Actions は 40 桁の SHA で固定し、Dependabot が公開から 7 日待って上げる。
既知の脆弱性と解いてよくなった固定は、週 1 回のサプライチェーンの検査が別の 1 件の Issue（脆弱性の知らせ）に知らせる。
NDF は下限より古い外部 CLI（`gh`・`claude`・`codex`）を起動しない。依存のバージョンの決まりは「公開から 7 日たった最新の安定版。httpx は
0.28.1 に固定し、DBOS は対象の外」である（#3347 の決定 106、利用者の決定 2026-11-14）。

例: 月曜 09:00 JST の定時の実行で、`plugins/ndf/uv.lock` の semver に 7 日たった新しいバージョンがあった。

| 順 | 起きること |
| ---: | --- |
| 1 | `renovate.yml` が GitHub App の token で Renovate を動かす。Renovate は 7 日に満たないバージョンを外し、依存の一覧の Issue に「依存（plugins/ndf）」の行を書く。頭には固定の一覧（httpx 0.28.1・dbos）が出る |
| 2 | 開発者がその行にチェックを入れ、`renovate.yml` を手動で打つ（次の定時でもよい） |
| 3 | Renovate が `uv lock` で lock を作り直し、`plugins/ndf` の更新を 1 本にまとめた PR を作る。作り直しの成否を `renovate/artifacts` に付ける |
| 4 | PR の CI で `script-structure.yml` が `uv lock --check --project plugins/ndf` と、新しく入ったバージョンの公開日の検査（`age`）を打ち、変わったパッケージを import するテストの一覧のデータを作る。`deps-affected-comment.yml` がそれを PR の 1 件のコメントに書く |
| 5 | `renovate-gate.yml` が既定ブランチの検証器で、今の head の `renovate/artifacts` が Renovate の App の書いた `success` かを確かめ、commit status `ndf/renovate-gate` を付ける。`success` でなければ merge queue に入らない |

| 何を読むか | 正本 |
| --- | --- |
| Renovate の規則・7 日・固定の理由の値 | [.github/renovate.json](../../.github/renovate.json) |
| 外部 CLI の下限と `--help` の見本 | [plugins/ndf/contracts/domains/cli-floors.json](../../plugins/ndf/contracts/domains/cli-floors.json) |
| 用語の定義 | 用語集 [docs/glossary.md](../glossary.md) |
| 必須にしないジョブと commit status の一覧 | [scripts/required-checks-allow.json](../../scripts/required-checks-allow.json) |

## 概要

### 構成要素と責務

| 要素 | 置き場 | 責務 |
| --- | --- | --- |
| Renovate の設定 | `.github/renovate.json` | 対象の lock と `UV_VERSION`・Renovate 自身のバージョンを見て 7 日待ち、チェックを入れるまで PR を作らず、lock ごとに 1 本にまとめる。lock の作り直し（推移的な依存）を候補に出す。固定と対象の外を欄つきの理由で持つ |
| Renovate のバージョン | `.github/renovate-version` | 動かす Renovate のバージョンを `^[0-9]+\.[0-9]+\.[0-9]+$` の 1 行で持つ。Renovate 自身が 7 日待って上げる |
| Renovate を動かすワークフロー | `.github/workflows/renovate.yml` | 週 1 回の定時（月曜 09:00 JST）と手動で Renovate を動かす。手動では検証用のブランチ（`verify/`）を宛先にできる |
| 依存の PR の関門 | `.github/workflows/renovate-gate.yml` | 既定ブランチの定義と検証器で Renovate の PR の `renovate/artifacts` を確かめ、PR の先端（merge queue ではキューのコミット）へ commit status `ndf/renovate-gate` を付ける |
| サプライチェーンの検査 | `.github/workflows/deps-weekly.yml` | ジョブ `supply-chain-report`（週 1 回と手動の脆弱性の知らせ）と `renovate-config`（Renovate の設定を変えた PR で設定を検証する） |
| 確かめるテストの一覧の投稿 | `.github/workflows/deps-affected-comment.yml` | `script-structure.yml` が残した一覧のデータを、既定ブランチのコードで PR 番号と head の SHA を確かめてから 1 件のコメントに書く |
| 定義と lock の一致・公開日・Action の参照の検査 | `.github/workflows/script-structure.yml` | `uv lock --check` を 4 つの uv のプロジェクトごとに打つ。`age`・`action-pins`・`affected` を打つ |
| `--help` の照らし合わせ | `.github/workflows/runtime-plugin-smoke-latest.yml` | 毎日の最新の CLI のスモークの中で、使う引数を見本と照らす |
| Dependabot の設定 | `.github/dependabot.yml` | Actions の参照を週 1 回上げ、`cooldown` の 7 日を持つ。Python と npm は扱わない |
| 依存の検査のスクリプト | `scripts/plugin-deps.py` | サブコマンド `targets`・`holds`・`uv-pin`・`affected`・`post-affected`・`report`・`action-pins`・`age`・`cli-help`・`renovate-gate`・`renovate-log`。結果は統一の結果 JSON の 1 行（`tool: "plugin-deps"`）を標準出力へ出し、`stopped` なら終了コード 1。NDF のパッケージを import せず根の環境で動く |
| 依存の定義と lock | 4 つの uv のプロジェクト（根・`plugins/ndf`・`plugins/playwright-kit/skills/playwright-kit-ops`・`plugins/mcp/mcp-serena`）の `pyproject.toml`・`uv.lock`、スライドのテンプレート（`plugins/ndf/skills/release/assets/slides/`）の `package.json`・`package-lock.json`・`.npmrc` | `[tool.uv] exclude-newer = "7 days"`・`.npmrc` の `min-release-age=7` と、上げる先のバージョン |
| 外部 CLI の下限の表 | `plugins/ndf/contracts/domains/cli-floors.json` | `gh`・`claude`・`codex` の実行ファイル・バージョンを出す引数・読み方・下限・`--help` の見本 |
| 下限の比べ | `plugins/ndf/src/ndf/adapters/cli_floor.py` | `require`・`refuse_old`・`TooOld`。起動の前にバージョンを下限と比べる |
| 設定の検査のテスト | `scripts/tests/test_plugin_deps.py`・`plugins/ndf/tests/adapters/test_cli_floor.py` | 下の「常に成り立つ条件」と各サブコマンドを、記録した入力で縛る |

依存の向きは、ワークフロー → `scripts/plugin-deps.py` → 設定のファイル（`renovate.json`・各 `pyproject.toml`・`cli-floors.json`）の
一方向である。NDF の側は 起動の箇所 → `cli_floor` → `ndf.contracts.facts`（表の読み取り）の一方向である。

### コンテキスト

依存のバージョンの語（安定版・上げる先・遅れ・固定する理由・依存の更新の仕組み・依存の一覧の Issue・推移的な依存・脆弱性の知らせ）は
コンテキスト `ndf-code-structure` に、外部 CLI の下限はコンテキスト `ndf-agent-cli` に属する。2 つは上下流の関係で、コードの構成が
外部 CLI の下限を依存の 1 つとして読む（下限の表は NDF のコントラクトが持ち、週 1 回の検査は読むだけ）。Renovate・Dependabot・uv の語
（`minimumReleaseAge`・`cooldown`・`exclude-newer`）は設定の値としてだけ使う。

## 用語

語の定義は用語集 [docs/glossary.md](../glossary.md) のコンテキスト `ndf-code-structure`
（[docs/glossary/ndf-code-structure.md](../glossary/ndf-code-structure.md)）と `ndf-agent-cli`
（[docs/glossary/ndf-agent-cli.md](../glossary/ndf-agent-cli.md)）にある。

## 背景

2026-11-14 の #1142（NDF の作り直し）の設計レビューで、NDF の依存 24 本のうち 14 本が最新の安定版より古く、遅れを知らせる検査も
上げる決まった操作も無いと分かった。推移的な依存・ほかの lock・コードの中の固定・外部 CLI・Actions は誰も見ておらず、根の lock は
86 本のうち 47 本が遅れ、playwright-kit の lock には既知の脆弱性が 7 件あった（2026-11-21 の実測）。

- **仕組みは Renovate を GitHub Actions の上で GitHub App の token で動かす。** 7 日待つ・固定と理由・1 件の Issue での知らせ・承認して
  から PR を作る形を設定だけで満たし、ほかの lock と推移的な依存も同じ設定で扱えるためである。#3347 の決定 107 の自作
  （`scripts/plugin-deps.py` に遅れの検査と上げる操作を持つ）を置き換えた。Dependabot は固定の理由を書く項目と 1 件の Issue での知らせを持たない。
  Mend の運用するアプリでは起動の時刻と条件をこのリポジトリで決められず、`GITHUB_TOKEN` では Renovate の PR の CI が手の承認まで走らない
- **Renovate のバージョンは `.github/renovate-version` に置く。** action の既定は動くタグ `44` で 7 日を待たず、ワークフローに書くと App に
  Workflows の権限が要るためである
- **7 日は Renovate・Dependabot・uv（と npm）に同じ値で置く。** Renovate は uv の設定を読まず、lock を作り直すのは uv と npm なので、
  1 か所の取りこぼしで破らないためである。依存の一覧の Issue のチェックは Renovate の待つ日数を飛ばせるため、PR の上で公開日を確かめる
  `age` が最後の守りになる
- **脆弱性は Renovate の脆弱性の PR でなく、週 1 回の `uv audit` と `npm audit` で知らせる。** Renovate の脆弱性の PR は承認・待つ日数・
  予定を飛ばし、7 日に満たない直しでは lock の作り直しが失敗する。このリポジトリの依存グラフは `uv.lock` を読まず、Dependabot alerts も使えない
- **Actions は Renovate でなく Dependabot が上げる。** Renovate にワークフローを書き換えさせると App に Workflows の権限が要る
- **外部 CLI の下限は NDF のコントラクトの表に置き、その CLI を起動する箇所の前で比べる。** SessionStart の hook で止めると CLI を使わない
  コマンドまで止まり、下限はプロジェクトごとに違う値でないため利用者の設定には置かない

## 対象範囲

| 含む | 含まない |
| --- | --- |
| 追跡している lock（`git ls-files` の `uv.lock`・`package-lock.json`）から `ignorePaths` の `docs/presentations/**` を除いた 5 つと、その推移的な依存 | `docs/presentations/` の lock（発表日時点の記録） |
| コードの中の uv のバージョン `UV_VERSION`（`plugins/ndf/bin/ndf`・`scripts/lib/ndf_wrappers.py`）と Renovate 自身のバージョン | CI の uv と Python のバージョンの固定（Z2 #3687） |
| `.github/workflows/` の外部の Action の参照 | DBOS のバージョン（C1+ の続き。#3285 の追補） |
| `gh`・`claude`・`codex` の下限と `--help` の照らし合わせ | Kiro と agy の下限・依存のライセンスの検査 |

## 仕様

### 集約

| 集約 | オーナー（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| 依存の定義（uv のプロジェクトごと） | Renovate の更新の PR | 各 `pyproject.toml` | 依存の行 | バージョン・7 日の設定・`uv.lock`（定義から `uv lock` で作るコピー） |
| 依存の更新の規則 | 規則を変える開発者の PR | `.github/renovate.json` | 依存ごとの規則（まとめ方・固定と理由・対象の外と理由） | 7 日・承認の要否・対象のパス |
| 遅れの知らせ | Renovate | 依存の一覧の Issue | 更新の候補の行 | 承認のチェック |
| 脆弱性の知らせ | サプライチェーンの検査 | 脆弱性の知らせの Issue | 脆弱性の行・解いてよい固定の行・プレリリースを選べる依存の行 | 勧告の ID・直ったバージョン |
| Actions の参照 | Dependabot の PR | `.github/workflows/*.yml` の `uses:` | 参照の行 | SHA・バージョンの注記 |
| 外部 CLI の下限 | 下限を変える開発者の PR | `plugins/ndf/contracts/domains/cli-floors.json` | CLI の行 | 下限・バージョンの読み方・`--help` の見本 |

依存の定義のオーナーの例外は、DBOS の行を上げる C1+ の続きの PR だけである（DBOS は Renovate の対象の外）。

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| IG5 | 各 `uv.lock` が定義と一致する | `script-structure.yml` の `uv lock --check --project <ディレクトリ>` が PR を落とす。lock は比べる日時を絶対の日時で持たないので、日がたっても落ちない |
| I1 | 上げる先は、最初のアップロードから 7 日たったバージョンに限る（脆弱性の例外の規則に載る組を除く） | Renovate が 7 日に満たないバージョンを出しても、`uv lock` と npm の `min-release-age` が解決を拒む。lock・`UV_VERSION`・Renovate のバージョンに入った 7 日に満たないバージョンと公開日の無いバージョンは、`age` が落とす |
| I2 | 知らせは依存の一覧の Issue と脆弱性の知らせの 2 件だけである | 脆弱性の知らせは本文の目印と作成者で 1 件を引き、無ければ作る。目印つきが 2 件以上なら触れずに止まる |
| I3 | 固定する依存と対象の外の依存は、規則の `description` に種類・相手・制約・見直す日・理由の 5 行をこの順で持つ。脆弱性の例外（uv の `exclude-newer-package`・npm の `overrides`）は、同じ勧告の ID・バージョン・外す日を持つ規則と組になる | 欄の欠けた規則と規則の無い例外は、設定の検査のテストが落とす。規則だけが先に入る状態は、例外を 2 つの PR で入れる手順の先行の PR として通す |
| I4 | 解決できない上げ方では、更新の PR のチェック `renovate/artifacts` が赤になる | 今の head の `renovate/artifacts` が Renovate の App の書いた `success` でなければ、`ndf/renovate-gate` が `success` にならず merge queue に入らない |
| I5 | 定時の検査（`renovate`・`supply-chain-report`）は週 1 回の定時と手動でだけ走り、PR では走らない | ワークフローの起動の条件を設定の検査のテストが確かめる |
| I6 | Renovate の `minimumReleaseAge`・Dependabot の `cooldown.default-days`・4 つの `exclude-newer` は同じ 7 日で、正は `plugins/ndf/pyproject.toml` の値。lock の作り直しだけは Renovate の待つ日数を `null` にし、7 日を uv と npm が守る | 食い違うか、`null` を lock の作り直しの外に置くと、設定の検査のテストが落ちる |
| I7 | 固定した依存は、固定したバージョンの外を上げる先にしない（httpx は `==0.28.1` だけ） | 固定の範囲を外すとテストが落ちる |
| I8 | 依存の定義の各行の書き手は同じ時点に 1 つだけである | Renovate は App と秘密を置くまで動かない（`if: vars.RENOVATE_APP_CLIENT_ID != ''`）。`uv.lock` が食い違ったら、後に入る PR が `main` の上で lock を作り直す |
| I9 | 対象の lock は、追跡している lock から `ignorePaths` を除いたものすべてである | lock を足して Renovate の対象から漏れると、設定の検査のテストが落ちる |
| I10 | 外部の Action の参照はすべて `@<40 桁の SHA> # v<具体的なバージョン>` の形である | タグで参照する行を足すと、設定の検査のテストが落ちる。SHA と注記の対応は `action-pins` が確かめる |
| I11 | 取得（脆弱性・PyPI・npm）が 1 つでも失敗した実行は、知らせを書き換えずに失敗で終わる | `report` は終了コード 1 で Issue に触れない。Renovate は `hostRules` の `abortOnError` で、取得が 1 つでも失敗したら依存の一覧の Issue を書く前に止まり、`exitCodeForErrors` でジョブが失敗になる |
| I12 | NDF は下限より古い外部 CLI を起動しない | 起動の前の比べで、その箇所の今の「起動できない」の形で止まる。比べを持たない起動の箇所を足すと、起動の箇所を探すテストが落とす |

### ドメインイベント

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | 依存の新しいバージョンが公開された | PyPI・npm・GitHub の releases | Renovate・Dependabot の次の実行 |
| E2 | 公開から 7 日たった | 時間 | Renovate（`minimumReleaseAge`）・Dependabot（`cooldown`）・uv（`exclude-newer`）・npm（`min-release-age`） |
| E3 | 週 1 回の検査が遅れ・脆弱性・解いてよい固定を、毎日のスモークが `--help` の食い違いを見つけた | `renovate.yml`・`deps-weekly.yml`・`runtime-plugin-smoke-latest.yml` | 依存の一覧の Issue・脆弱性の知らせ・ワークフローの失敗 |
| E4 | 遅れの知らせと脆弱性の知らせを書き直した・閉じた | Renovate・`report` | 開発者 |
| E5 | 更新の PR が lock を書き換えた | 依存の一覧の Issue のチェック → Renovate | CI（`uv lock --check`・`age`・テスト）と関門 |
| E6 | 更新の PR に、変わった依存と確かめるテストの一覧が出た | `affected` → `post-affected` | 更新の PR を見る開発者。一覧を作れなければ理由で書き直し、CI の合否は変えない |
| E7 | NDF のコマンドが外部 CLI のバージョンを下限と比べた | `cli_floor.require` | 利用者（止まったときの理由） |

### Renovate の設定

| 項目 | 値 |
| --- | --- |
| 対象 | `baseBranchPatterns: ["main"]`・`enabledManagers: ["pep621", "npm", "custom.regex"]`・`ignorePaths: ["docs/presentations/**"]` |
| 7 日 | `minimumReleaseAge: "7 days"`・`minimumReleaseAgeBehaviour: "timestamp-required"`（日付の無いバージョンを上げない） |
| 依存の一覧の Issue | `dependencyDashboard` をオブジェクトの形（`enabled`・`title: "依存の一覧"`・`autoclose: true`・`header`）で書き、`dependencyDashboardApproval: true`。`header` は、チェックが効く時機（次の定時か手動の実行）と固定の一覧「固定: <依存> <バージョン>（<種類>・相手 <相手>・見直す日 <日付>）」を持つ。固定の一覧が規則と同じ中身であることはテストが縛り、httpx は遅れでなく固定として見える |
| lock の作り直し | `pep621.lockFileMaintenance`・`npm.lockFileMaintenance` を `{"enabled": true, "minimumReleaseAge": null}`。uv では `uv lock --upgrade`、npm では lock を作り直す。作り直しで上がるパッケージには Renovate が見る日時が無く、待つ日数を置くと待ちのチェックが残り続けるため `null` にする |
| 並行の PR | `branchConcurrentLimit: 1`・`prConcurrentLimit: 1`・`separateMajorMinor: false`・`separateMultipleMajor: false`。同じ lock を 2 本の PR が触らない |
| 取得の失敗 | `hostRules` の `pypi.org`・`files.pythonhosted.org`・`registry.npmjs.org`・`api.github.com` に `abortOnError: true` |
| npm | `constraints.npm: ">=11.10.0"`（`min-release-age` を持つバージョン） |
| 失敗の返し方 | `statusCheckWhen.artifactError: "always"`。`renovate/artifacts` を常に出す |
| 脆弱性の PR | `vulnerabilityAlerts.enabled: false`・`osvVulnerabilityAlerts: false` |
| ラベル・コミット | `labels: ["dependencies"]`・`commitMessagePrefix: "Update:"` |

`customManagers` は 2 つで、`UV_VERSION *= *"(?<currentValue>[0-9.]+)"` を `astral-sh/uv` の `github-releases` として、
`.github/renovate-version` の 1 行を `renovatebot/renovate` の `github-releases` として拾う。

規則（`packageRules`）は次の並びで置く。後の規則が前の規則を上書きする。

| # | 当てる依存 | 中身 |
| --- | --- | --- |
| 1〜5 | lock ごとの定義（根・`plugins/ndf`・mcp-serena・playwright-kit・スライドの `package.json`）の通常の更新 | `groupName: "依存（<ディレクトリ>）"`・`prPriority: 2`。lock の作り直しは同じまとまりに入れない（Renovate が通常の更新と作り直しの同じまとまりを扱わない） |
| 4 の追加 | playwright-kit | `postUpgradeTasks` に `uv lock --project .`（`fileFilters: ["uv.lock"]`・`executionMode: "branch"`）。根は playwright-kit を編集可能なパスの依存で取り込むので、根の lock も同じ PR で作り直す |
| 6 | 人が書いたプレリリースを選ぶ依存の一覧（初めは空） | `ignoreUnstable: false`。種類 `プレリリースを選ぶ`・相手 `決定 106`・制約 `7 日たった最新の安定版が 365 日より古い` |
| 7 | `httpx` | `allowedVersions: "==0.28.1"`。種類 `相手の制約`・相手 `githubkit 0.17.1`・制約 `httpx<1.0.0,>=0.23.0` |
| 8 | `dbos` | `enabled: false`。種類 `対象の外`・相手 `C1+ の続き（#3285 の追補）`・制約 `plugins/ndf は 3.4.0・根は 3.1.0 のまま` |
| 9 | `ruamel-yaml`（`plugins/ndf` と mcp-serena） | 両方の lock を同じバージョンにそろえて 1 本の PR で上げる（`prPriority: 1`） |
| 10 | `astral-sh/uv`（`UV_VERSION` の 2 か所） | `groupName: "uv のインストーラ"`。`postUpgradeTasks` に `python3 scripts/plugin-deps.py uv-pin`（`executionMode: "branch"`）を置き、インストーラの sha256 を 2 か所へ書き直す |
| 11 | `matchUpdateTypes: ["lockFileMaintenance"]` | `prPriority: 0`。lock ごとに 1 本で、通常の更新と別の承認の行 |
| 12（足すときだけ） | 脆弱性の例外 | 種類 `脆弱性の例外`・相手 `<勧告の ID>`・制約 `<パッケージ>==<バージョン>`・見直す日（外す日）、`enabled: false`。`age` がこの組だけを 7 日の外として通す |

固定と対象の外の規則の `description` は次の 5 行の配列である（未知のキーは Renovate の検証で落ちるため、欄は行で持つ）。
`見直す日` は ISO の日付で、`scripts/plugin-deps.py` の `read_holds` が読む。行の順が違うか欄が欠ければ `config_broken` で止まる。

```json
"description": [
  "種類: 相手の制約",
  "相手: githubkit 0.17.1",
  "制約: httpx<1.0.0,>=0.23.0",
  "見直す日: 2027-02-19",
  "理由: githubkit 0.17.1 が httpx<1.0.0,>=0.23.0 を求めるため 0.28.1 に固定する（#3370 の受け入れ条件 8）"
]
```

種類は `相手の制約`・`自分の非互換`・`対象の外`・`プレリリースを選ぶ`・`脆弱性の例外` の 5 つである。チェックの入った候補が
複数あれば、`prPriority` の大きいもの（lock ごとの更新 → ruamel-yaml の横断 → lock の作り直し）から 1 本ずつ作る。前の PR が
マージされて閉じると、次の定時か手動の実行が次の 1 本を `main` の上で作る。

### Renovate を動かすワークフロー

| 項目 | 値 |
| --- | --- |
| 起動 | `schedule: cron "0 0 * * 1"` と `workflow_dispatch` だけ。入力 `base_branch`（既定 `main`）は `main` か `verify/<名前>` だけを受け、`verify/` では `verify_scenario`（`unresolvable`・`fetch-failure`）も要る |
| 有効にする時機 | ジョブの `if: vars.RENOVATE_APP_CLIENT_ID != ''`。変数を置くまでの定時の実行は飛ぶ |
| 排他 | `concurrency: { group: renovate, cancel-in-progress: false }` |
| バージョンの検証 | token を作る前に `.github/renovate-version` を読み、形が合わなければ止める。続いて `plugin-deps.py age --renovate-version <バージョン>` が npm の `renovate` のそのバージョンの公開日を読み、7 日に満たないか読めなければ止める |
| token | `actions/create-github-app-token` に変数 `RENOVATE_APP_CLIENT_ID` と秘密 `RENOVATE_APP_PRIVATE_KEY` を渡す。権限は contents・pull-requests・issues・statuses を `write`、checks を `read` |
| Renovate | `renovatebot/github-action` に検証したバージョンを渡す。全体の設定は `RENOVATE_ONBOARDING: "false"`・`RENOVATE_REQUIRE_CONFIG: required`・`RENOVATE_BINARY_SOURCE: install`・`RENOVATE_EXIT_CODE_FOR_ERRORS: "true"`（`abortOnError` の中断をジョブの失敗にする）・JSON の記録・`RENOVATE_ALLOWED_COMMANDS`（`^python3 scripts/plugin-deps\.py uv-pin$` と `^uv lock --project \.$`） |
| PR の無い失敗 | Renovate の後の `plugin-deps.py renovate-log` が記録を読み、PR もブランチも作られなかった失敗の更新をジョブの要約に並べて失敗で終える |

**検証の実行（`base_branch` が `verify/`）。** token を作る前にそのブランチの先端の SHA を読み、`compare/main...<SHA>` の `status` が
`identical` か `behind`（`main` の祖先）でなければ止める。Renovate が読む設定と lock を作り直す作業ツリーを、`main` に入った信頼済みの
コミットに限るためである。`RENOVATE_BASE_BRANCH_PATTERNS` をそのブランチ 1 本に、`RENOVATE_ALLOWED_COMMANDS` を `[]` にし、
`renovate.yml` に固定した JSON を `verify_scenario` ごとに `RENOVATE_FORCE` で渡す。中身は `branchPrefix: "renovate-verify/"`・題
`依存の一覧（検証）`・対象を `plugins/ndf` の pep621 の httpx だけにし、httpx に `==1.0.dev6` を承認なしで許す規則（`fetch-failure` では
取得先を届かない `renovate-verify.invalid` にする）である。`force` の `packageRules` は既存の規則を配列ごと置き換えるため、完全な形で書く。
接頭辞と題を分けるのは、本番の依存の一覧の Issue と `renovate/` のブランチを書き換え・再利用・削除しないためである。Renovate の後に先端の
SHA を読み直し、変わっていれば実行を失敗で終える。

### 依存の PR の関門

`renovate/artifacts` は必須のチェックに入れない（Renovate の PR にしか付かず、必須にすると付かない PR が止まる）。代わりに
`renovate-gate.yml` が commit status `ndf/renovate-gate` を付け、`main` の ruleset の必須にこの status を足す（出元を GitHub Actions のアプリ
15368 に限る。`ndf/release-approval` と同じ形）。`pull_request` で動く `script-structure-check` の中に置かないのは、PR の中で関門の条件も
検証器も書き換えられるためである。

| 項目 | 値 |
| --- | --- |
| 起動 | `pull_request_target`（`opened`・`synchronize`・`reopened`）・`status`（context が `renovate/artifacts` のときだけ）・`merge_group`。どれも既定ブランチの workflow の定義で動く |
| 判定の単位 | SHA。その SHA を先端に持つブランチのうち `renovate/` か `renovate-verify/` で始まるものから、同じリポジトリの開いた Renovate の PR をすべて引いて判定する。候補を取りきれなければ落ちて `success` を付けない |
| 対象の外の PR | その SHA に Renovate の PR が無く、`pull_request_target` で fork か Renovate の接頭辞でない PR なら、checkout も Python も打たずに `success`（説明「Renovate の PR でない」）を付ける |
| 検証器 | 既定ブランチの `scripts/plugin-deps.py renovate-gate --pr <番号>... --head <SHA> --bot "<slug>[bot]" --post`。PR のコードは取り出さない |
| 判定 | PR ごとに今の head を確かめ、その SHA の `statuses` から context が `renovate/artifacts` の最も新しい 1 件を選ぶ。`creator.login` が Renovate の App の bot（変数 `RENOVATE_APP_SLUG` から組み立てる）の `success` なら `success`、未報告か保留なら `pending`、それ以外（別の主体が後から書いた `success` を含む）は `failure`。どの PR の今の head も `--head` と違えば付けずに `skipped` |
| merge queue | PR の時点の status を引き継がず、キューのコミットの第 2 親（キューに入ったバージョン）で判定し直してキューのコミットに付ける。候補の Renovate の PR が無ければシェルで `success`。候補があれば `actions/checkout` の sparse で検証器だけを取って `--queue-sha` つきで打つ。既定ブランチに検証器が無い・一時ブランチの形でない・第 2 親が無い・`gh api` が落ちたときは `failure`。`posted` が無いまま落ちたら、フォールバックのステップが `failure` を付ける |
| 直列化と権限 | `concurrency: renovate-gate-<SHA>`（`cancel-in-progress: false`）。権限は `statuses: write`・`pull-requests: read`・`contents: read` |

### サプライチェーンの検査

| ジョブ | 起動 | 打つこと | 権限 |
| --- | --- | --- | --- |
| `supply-chain-report` | `schedule: cron "0 0 * * 1"`・`workflow_dispatch` | `uv run --frozen --project . python scripts/plugin-deps.py report` | `issues: write`・`contents: read` |
| `renovate-config` | `pull_request` の `paths`（`.github/renovate.json`・`.github/renovate-version`）。同じ PR の連続の push は `cancel-in-progress` | バージョンの形を確かめ、宛先の先頭（`HEAD^1`）の `scripts/plugin-deps.py` を `git show` で取り出して `age --config-ref HEAD^1 --renovate-version <バージョン>` を打つ（宛先に検証器が無ければ npm の公開日をワークフローの定義で確かめる）。通ったときだけ `npx --yes --package renovate@<バージョン> -- renovate-config-validator --strict --no-global .github/renovate.json` | `contents: read` |

`report` は、uv のプロジェクトでは `uv audit --frozen --output-format json`、npm の lock では `npm audit --package-lock-only --json` を打ち、
本文の 1 行目が目印 `<!-- ndf:supply-chain-report -->` で作成者が `github-actions[bot]` の Issue を 1 件だけ管理する（ラベル
`supply-chain` は絞り込みにだけ使う）。本文は「脆弱性」「解いてよい固定」「プレリリースを選べる依存」の 3 つの表を持ち、どれも 0 行なら閉じる。

| 判定 | 条件 | 結果 |
| --- | --- | --- |
| 固定 | 種類 `相手の制約` で、相手の採用できる最新のバージョン（公開から 7 日たった安定版）の `requires_dist` に固定した依存の行が無い（マーカーはそのプロジェクトの `requires-python` の下限・`sys_platform == "linux"`・要る extra で評価する） | 「解いてよい固定」に「制約の消失」 |
| 同上 | 新しい制約が、固定のバージョンより新しい 7 日たったバージョンのうち `制約:` の許さないバージョンを 1 つ以上許す | 「解いてよい固定」に「緩和」とそのバージョン |
| 同上 | 許すバージョンの集まりが同じ（並び・同じ意味の書き換え）か、新しい制約の方が狭い | 出さない（強化は結果 JSON の `items` に `tightened` として残す） |
| 同上 | `見直す日:` が今日より前（種類によらない。脆弱性の例外では外す日） | 「解いてよい固定」 |
| プレリリース | 直接の依存で、7 日たった最新の安定版が 365 日より古く、それより新しいプレリリースが 7 日たっている | 「プレリリースを選べる依存」。人が規則 6 に書けば Renovate が候補に入れる |
| 同上 | 規則 6 にある依存で、7 日たった最新の安定版が 365 日以内になった | 「プレリリースを選べる依存」に「一覧から外してよい」 |

### 確かめるテストの一覧

`script-structure.yml` は、`pull_request` で head のリポジトリが宛先と同じときだけ `plugin-deps.py affected --base HEAD^1 --head HEAD` を
打ち、データを artifact `deps-affected` に置く。比べる 2 点は同じイベントで取った PR のマージのコミットとその第 1 親で、宛先が進んでも
PR の外の変更を拾わない。`affected` は lock を読むだけで `uv` を打たない（`uv tree` は lock を書き換えうる）。

| lock | パッケージ名から使う名前へ | 使う箇所の読み方 | 推移的な依存の親 |
| --- | --- | --- | --- |
| `uv.lock` | 既定は `-` を `_` にした名前。違うものは `IMPORT_NAMES`（`python-dotenv` → `dotenv` など）、CLI としてだけ使う依存は `NO_IMPORT` に持つ。4 つの定義の直接の依存がすべてどちらかで引けることをテストが縛る | そのプロジェクトの `*.py` の `import` を標準ライブラリの `ast` で読む | `[[package]]` の `dependencies` を `tomllib` で読んだ逆向きの関係 |
| `package-lock.json` | パッケージ名のまま | そのディレクトリの `*.js`（`node_modules` を除く）の `require` と `import` | `packages` の `dependencies` の逆向きの関係 |

`deps-affected-comment.yml` は `workflow_run`（`Script structure` の `completed`）で起動し、`pull-requests: write`・`actions: read` を持つ。
既定ブランチのコードだけを checkout し、artifact の JSON をデータとしてだけ読む。`post-affected` は artifact の PR 番号と head の SHA が
実行に結び付いた値と、PR の今の `head.sha` に一致するときだけ、目印 `<!-- ndf:deps-affected -->` のコメントを作るか書き直す。lock の差分が
無くなった PR では目印のコメントを消す。fork の PR では `workflow_run.pull_requests` が空になり投稿先を区別できないため、一覧を作らない。
一覧の生成に `pull_request_target` を使わないのは、書き込みの token に PR のコードが届くためである。

### 公開から 7 日の検査と Action の参照

`script-structure.yml` は条件を付けずに `plugin-deps.py age --base HEAD^1 --head HEAD` を打つ（`actions/setup-python` の 3.12 の実行ファイル。
`uv.lock` を `tomllib` で読むため）。差分で lock・`UV_VERSION`・`.github/renovate-version` が変わっていなければ取得せずに `skipped` で
終わる。新しく入ったバージョンの公開日を PyPI・npm・GitHub の releases から読み、7 日に満たないか読めなければ `check_failed` で落とす。
脆弱性の例外は宛先（`HEAD^1`）の設定の規則 12 だけから読み、同じ PR で足した例外ではその PR のバージョンを通さない。

`plugin-deps.py action-pins --base HEAD^1 --head HEAD` は、差分で変わった `uses:` の各行で、注記のタグが指すコミット（注記つきのタグは
指す先まで辿る）が行の SHA と同じかを確かめる。初めの SHA は、各 Action の今のメジャーの中で `published_at` が 7 日より前の最新の
リリースのタグが指すコミットにした（動くメジャーのタグを解くと 7 日に満たないコミットを採りうる）。

### 外部 CLI の下限

| 行 | `executable` | `min_version` | `version_pattern` の対象 |
| --- | --- | --- | --- |
| `gh` | `gh` | 2.17.0 | `gh version <バージョン>` |
| `claude` | `claude` | 2.1.268 | `<バージョン> (Claude Code)` |
| `codex` | `codex` | 0.122.0 | `codex-cli <バージョン>` |

下限は NDF が使う引数とサブコマンドがすべて入った最初のバージョンである。`help` の列は照らす見本 `{"args": [...], "flags": [...]}` の
並びで、`claude` のトップレベルの `--help` は `{"table": "data/claude-cli.json"}` で今の引数の表を使う。`claude`・`codex` の `executable`
は `domains/runtimes.json` と同じ値である。

`require(name, argv0, env=None, cwd=None)` は、起動と同じ `PATH`（`env` の `PATH`、無ければ今のプロセスの値）と `cwd` で実行ファイルを
解いて実パスにし、`version_args`（`--version`）を 5 秒の期限で打って `semver` で下限と比べる。古ければ `TooOld`（`name`・`path`・
`found`・`floor` を持ち、文は「<名前> <見つけたバージョン>（<実パス>）は下限 <下限> より古い。<下限> 以上へ上げる」）を上げる。
下限と同じ数字のプレリリースは下限に満たない。結果は実パス・`st_mtime_ns`・`st_size` の組ごとにプロセスの中で再利用し、同じ実行ファイルに
2 回目の `--version` を打たない。表に無い名前（`kiro-cli`・`agy`）は何もしない。バージョンを読めない・期限切れ・見つからないときは、
比べずに起動を通し、読めなかったときは標準エラーへ 1 行を出す（CLI が無い・壊れているときの扱いは今の起動の箇所が持つ）。
`refuse_old` は `require` を打ち、`TooOld` をその箇所の例外へ直す。

| 起動の箇所 | 古いときの形 |
| --- | --- |
| `adapters/agents.py` の `_guarded_run`（学習の設定・重い起動のスロットを通った後、起動の前） | `AgentState.REFUSED` と理由。学習の設定で拒む経路では `--version` も打たない |
| `adapters/claude.py` の `call_claude`・`plugin` の起動 | `Failure.REFUSED` と理由 |
| `adapters/github.py` の `token`（`gh auth token`） | `GitHubError(理由)` |
| `domains/project/measure_ci.py` の `gh api` | `GhUnavailable(理由)` |
| `domains/relay/wrapper.py` の子の起動・`domains/relay/cli.py` のパススルー | 子の起動は停止理由 `precondition`、パススルーは `ndf-relay: claude を起動できない（<理由>）` で終了コード 127 |
| `domains/relay/account_login.py` のログイン・`auth status`・`logout` | `LoginError("precondition", 理由)`。比べの `--version` は認証のコマンドと同じ環境（親の認証の変数を外し `CLAUDE_CONFIG_DIR` を置いた環境）で打つ |
| `domains/relay/cli_account.py` の登録の解除 | `detach` の前に比べ、古ければ `status: stopped` で 0 以外で終わり、登録も symlink も残す（古い CLI でログアウトできないまま登録だけを外さない） |
| `adapters/cli.py` の `launch-args`（`bin/ndf-claude` が `exec claude` の前に打つ） | `status: stopped` で 0 以外。`ndf-claude` は同じ終了コードで終わり `exec claude` に進まない |

### `--help` の照らし合わせ

`runtime-plugin-smoke-latest.yml`（毎日 06:00 JST と手動。行列 `claude`・`codex`）の中で、スモークの後に前の段の成否によらず打つ。
最新の CLI はスモークが作る Docker イメージ `ai-plugins-runtime-smoke-<ランタイム>` の中にあるため、照らし合わせはそのイメージで
`--help` を打つ。新しいワークフローも CLI の 2 回目の取得も足さない。

- 検証器は `main` の先端から `scripts/plugin-deps.py`・`cli-floors.json`・`claude-cli.json` の 3 つだけを sparse の checkout で
  `tmp/deps-tool` に取る。このジョブは `issues: write` を持つため、入力の ref のコードを動かさない
- 手の起動の入力 `floors_ref` が空でも `main` でもないときだけ、その ref の 2 つの JSON を `tmp/deps-floors` に取る。打つ実行ファイルと
  引数は `main` の表（`--root tmp/deps-tool`）で決め、`floors_ref` の表（`--floors`）からは照らす名前だけを読む。`main` の表に無い引数の
  見本は打たずに `untested` として残し、失敗にしない（マージの後の定時の実行で照らす）
- `cli-help --runtime <ランタイム> --image <イメージ>` は 1 回の `docker run --rm --entrypoint sh` で全部の見本の `--help` を打つ。
  見本ごとの出力は実行ごとの 32 桁の乱数の境界の行で分け、区切りが欠ければ `check_failed`。`claude` の行だけ、`--image` 無しの
  `--runtime gh` でホストの `gh` を照らす
- 見本の引数が `--help` に無ければ失敗で終わり、既存の報告のステップがランタイムごとの 1 件の課題に残す。`--help` の側に見本に無い
  引数が増えても通る。表の形が検証器と合わなければ `config_broken`

## データ・設定

| 設定 | 保存先 | 値 |
| --- | --- | --- |
| 待つ日数（正） | `plugins/ndf/pyproject.toml` の `[tool.uv] exclude-newer` | `"7 days"`（lock の `[options]` に `exclude-newer-span = "P7D"`） |
| 待つ日数（写し） | 根・playwright-kit・mcp-serena の `exclude-newer`、`renovate.json` の `minimumReleaseAge`、`dependabot.yml` の `cooldown.default-days`、スライドの `.npmrc` の `min-release-age` | `"7 days"`・`"7 days"`・`7`・`7` |
| Renovate のバージョン | `.github/renovate-version` | 公開から 7 日たった 44 系の 1 行 |
| `UV_VERSION` と `UV_INSTALLER_SHA256` | `plugins/ndf/bin/ndf`・`scripts/lib/ndf_wrappers.py` | 2 か所で同じ組（テストが縛る）。`uv-pin` は `https://astral.sh/uv/<バージョン>/install.sh` を 1 回だけ取得して sha256 を計算し、2 か所を同じ値に書き直す |
| 脆弱性の例外 | uv: `exclude-newer-package = { "<パッケージ>" = "<日時>" }  # <勧告の ID> 外す日 <YYYY-MM-DD> 版 <バージョン>`、npm: `package.json` の `overrides` | `renovate.json` の規則 12 と組 |
| 定時の検査の免除 | `.ndf/project.json` の `test.ci_exempt` | `renovate.yml`・`renovate-gate.yml`・`deps-weekly.yml`・`deps-affected-comment.yml` の 4 行と理由 |
| 必須にしないもの | `scripts/required-checks-allow.json` | `not_required` に `supply-chain-report`・`renovate-config`・`renovate-gate`・`renovate-gate-merge-queue`、`statuses` に `ndf/renovate-gate`（出元 `renovate-gate.yml`）と `renovate/artifacts`、`awaiting` に `ndf/renovate-gate` |

`plugins/ndf/pyproject.toml` の頭の注記は、バージョンの選び方（決定 106 と 7 日）・上げる操作（依存の一覧の Issue と `.github/renovate.json`）・
固定を解く条件（規則とこの設定を同じ PR で変える）の 3 行を持つ。

## 外部連携

| 相手 | 使う所 | 中身 |
| --- | --- | --- |
| GitHub App（Renovate 用） | `renovate.yml` | 実行ごとに短い期限の token を作る。秘密鍵は Actions の秘密、クライアント ID と slug は Actions の変数 |
| PyPI の JSON API | `report`・`age` | バージョンごとの最初のアップロードの日時（`upload_time`）・`requires_dist` |
| npm のレジストリ | `age`・`renovate-config` | バージョンごとの公開日（`time`） |
| GitHub の API | `action-pins`・`age`・`renovate-gate`・`post-affected`・`report` | タグの指すコミット・releases の公開日・commit status・PR のコメント・Issue |
| OSV（`uv audit`）・npm audit | `report` | 既知の脆弱性。`uv audit` はプレビューの機能で、終了コードは 0 件で 0・1 件以上で 1・問い合わせの失敗で 2 |

## エラー処理

| 箇所 | 失敗 | 扱い |
| --- | --- | --- |
| `report` | 脆弱性・PyPI の取得の失敗、`uv audit` の JSON の形が読めない | `status: stopped`・`cause: transient` と対象、終了コード 1。Issue に触れない。再実行してよい |
| `report` | 管理の対象の Issue を 1 件に決められない（目印つきが 2 件以上・目印を持つ別の作成者の Issue） | `cause: config_broken` と Issue の番号。どれにも触れず、人が余分な目印を外して再実行する |
| `read_holds` | 欄の欠け・行の順の違い | `cause: config_broken` と規則の番号 |
| `affected` | 読めない lock（衝突のマーカー・形の違い） | `cause: invalid`。目印のコメントを「一覧を作れない: <理由>（<head の SHA>）」に書き直し、ステップは 0 で終わる（CI の合否を変えない） |
| `post-affected` | PR 番号か head の SHA が合わない・投稿先が空 | 書かずに `status: skipped`、終了コード 0 |
| `age` | 7 日に満たない・公開日が読めない | `cause: check_failed` とパッケージ・バージョン・公開日、終了コード 1。7 日たってから作り直すか、脆弱性の例外を入れる |
| `action-pins` | 注記のタグと SHA が合わない | `cause: check_failed` と行。注記か SHA を直す |
| `renovate-gate` | `renovate/artifacts` が `success` でない | `ndf/renovate-gate` を `failure`（未報告・保留では `pending`）。Renovate が直したブランチを push し直すか、PR を閉じる |
| `cli-help` | 見本の引数が無い・イメージが無い・出力を見本ごとに分けられない | `cause: check_failed`、終了コード 1 |
| `uv-pin` | インストーラの取得の失敗 | 2 つのファイルのどちらも書き換えず終了コード 1。`renovate/artifacts` が赤になる |
| `require` | 下限より古い | 起動の箇所の形で理由を出し、0 以外で終わる。CLI を上げて再実行する |

## セキュリティ

- Renovate の GitHub App の権限は Contents・Pull requests・Issues・Commit statuses の読み書きと、Checks・Metadata の読み取りに限る。
  Workflows の権限を持たないので、Renovate はワークフローのファイルを書き換えられない
- サプライチェーンの検査と関門と一覧の投稿は App を使わず、ワークフローの `GITHUB_TOKEN` の最小の権限で動く
- 関門・一覧の投稿・`renovate-config`・`--help` の照らし合わせの検証器は、既定ブランチ（か宛先の先頭）のコードで動き、PR や入力の ref の
  コードを動かさない。PR から来た値（artifact・表の JSON）はデータとしてだけ読む
- `renovate-gate` は `renovate/artifacts` の書き手を Renovate の App の bot に限り、書き込みの権限を持つ別の主体が後から書いた `success` を通さない
- Actions を 40 桁の SHA で固定し、タグの付け替えで中身が黙って変わらないようにする
- 結果 JSON と記録に GitHub の token と App の秘密鍵を出さない

## 運用

**導入の順序。** 実装の PR が `main` に入ってから、利用者が GitHub App を作ってこのリポジトリへ入れ、秘密鍵を秘密
`RENOVATE_APP_PRIVATE_KEY` に、クライアント ID を変数 `RENOVATE_APP_CLIENT_ID` に、slug を変数 `RENOVATE_APP_SLUG` に置く（C1・C2）。
`renovate-gate.yml` が状態を付け始めた後に、`main` の ruleset の必須へ `ndf/renovate-gate`（出元 GitHub Actions のアプリ 15368）を足す。
足す前に開いている PR は workflow の再実行で状態を得る。

**脆弱性の直しが 7 日に満たないとき、またはダウングレードが要るとき**は、Renovate の承認の経路では上げられないので、例外を 2 つの PR に分ける。

1. `renovate.json` に規則 12（種類 `脆弱性の例外`・勧告の ID・`<パッケージ>==<バージョン>`・外す日）だけを足す PR を承認してマージする
2. 依存の更新とパッケージマネージャー側の例外を同じ PR で入れる。uv では `exclude-newer-package` を足し、直接の依存なら `==` を書き換え、
   推移的な依存なら `uv lock --upgrade-package <パッケージ>==<バージョン>`。npm では `overrides` にバージョンを書き `npm install --min-release-age=0`。
   外す日を過ぎると脆弱性の知らせの「解いてよい固定」に出る

**解決できない上げ方と取得の失敗の確かめ**（#3370 の受け入れ条件 9 と 7）は、App を置いた後に検証用のブランチ `verify/3370-httpx` で行う。
作ったものは記録のファイルに 1 件ずつ書き、後始末は記録に書いたものだけを、1 件ずつ中身を確かめてから消す。

1. 本番の依存の一覧の Issue の `updated_at` と `renovate/` のブランチの一覧を控える
2. `main` の先端から `refs/heads/verify/3370-httpx` を作る（同じ名前があれば止まる）
3. そのブランチだけを対象にし、必須を `ndf/renovate-gate`（出元 15368）の 1 つだけにした検証用の ruleset を作る
4. `base_branch: verify/3370-httpx`・`verify_scenario: unresolvable` で打ち、httpx を 1.0.dev6 へ上げる PR の差分に `uv.lock` の変更が無く、
   `ndf/renovate-gate` が `failure` で、マージが拒まれることを見る
5. 検証用の依存の一覧の Issue の `updated_at` と本文を控え、`verify_scenario: fetch-failure` で打つ。記録に `renovate-verify.invalid` への
   取得の失敗の行があり、ジョブが失敗で終わり、Issue が控えと同じことを見る。書き換えていたら、受け入れ条件 7 の扱いを判断が要る点に挙げる
6. 記録の PR・Issue を閉じ、ruleset を消してから 2 つの ref を消す。本番の Issue とブランチの一覧が 1 の控えと同じことを見る

**導入の後の最初の実行で確かめること。** 承認を待つ更新が残るときに依存の一覧の Issue が `autoclose` で閉じないか（閉じるなら
`autoclose` を外し、閉じるのは `report` に任せる）、httpx が頭の固定の一覧にあり更新の候補の行に無いこと、`allowedCommands` が
action へ渡って `uv-pin` が打たれること、Dependabot が SHA の参照の注記も書き換えるか（書き換えなければ `action-pins` が落とすので
その PR で注記を直す）。`affected` は文字列での import とプラグインの読み込みを拾わない。

**切り戻し。** 動き出した Renovate を止めるときは変数 `RENOVATE_APP_CLIENT_ID` を消し（定時の実行が飛ぶ）、開いた更新の PR を閉じる。
下限の比べを外すと、今の起動の振る舞いに戻る。

## テスト観点

- `plugins/ndf` の依存（dev グループを含む）が、DBOS と httpx を除いて公開から 7 日たった最新の安定版にあり、4 つの uv のプロジェクトに
  `exclude-newer = "7 days"`、各 lock に `exclude-newer-span = "P7D"` があり、`uv lock --check` が通ること。定義だけを書き換えた PR で
  対応する検査が落ち、`script-structure.yml` からプロジェクトを 1 つ外すとテストが落ちること
- 7 日が Renovate・Dependabot・4 つの `pyproject.toml` で同じ値であること。どれか 1 つを `"6 days"` にするとテストが落ちること
- 定時のジョブが `schedule` と `workflow_dispatch` だけで起動し、承認と閉じる設定を外すとテストが落ちること
- httpx の規則が `==0.28.1` と欄つきの理由を、DBOS の規則が `enabled: false` と欄つきの理由を持ち、依存の一覧の Issue の頭の固定の一覧が
  規則と同じこと。欄を 1 つ消す・許すバージョンを広げる・固定の一覧から httpx を消すとテストが落ちること
- `statusCheckWhen.artifactError` が `"always"` で、`renovate-gate` が今の head で App の bot が書いた最新の `success` だけを通し、赤・
  未報告・保留・古い head の成功・別の主体が後から書いた成功を拒むこと。merge queue ではキューに入ったバージョンで判定し直すこと
- `plugins/ndf/pyproject.toml` の頭の注記が指すファイルがあり、作り直しの間の `11.0.0-dev.N` の注記が無いこと
- 追跡している lock から `ignorePaths` を除いたものがすべて Renovate の対象とサプライチェーンの検査の対象に入り、`UV_VERSION` の 2 か所を
  拾う規則があり、2 か所のバージョンとハッシュの組が同じこと。lock を 1 つ足して対象に足さない、片方のハッシュだけを変えると落ちること
- `affected` が記録した uv と npm の lock の差分から、変わったパッケージ・使うモジュール・そのテスト（無ければ「テストが無い」）・
  推移的な依存の親を出し、読めない lock では理由を出すこと。`post-affected` が今の head と違う SHA の結果を書かず、差分の無くなった PR の
  コメントを消すこと
- 下限より古いバージョンを返す偽の CLI を `PATH` の先に置くと、その CLI を起動する各エントリポイントが起動せずに CLI の名前・見つけた
  バージョン・下限を出して 0 以外で終わり、登録の解除では登録が残ること。下限と同じバージョンでは止まらず、同じ数字のプレリリースでは止まること。
  起動の箇所がすべて比べを持ち、比べを通らない起動の箇所を足すと落ちること
- `cli-help` が、見本の引数がトップレベルかサブコマンドの `--help` に無いと 0 以外で終わり、`--help` に見本に無い引数が増えても通り、
  `--image` を渡すと起動が見本の数によらず 1 回であること
- 外部の Action の参照がすべて 40 桁の SHA と具体的なバージョンの注記で、Dependabot の Actions の項目に `cooldown` の 7 日があること。タグの参照を
  1 つ足す、注記と違う SHA を書くと落ちること
- `report` が記録した `uv audit` と `npm audit` の結果から 1 件の Issue の本文を作り、0 件なら閉じ、取得の失敗を 1 つ混ぜると Issue に
  触れずに 0 以外で終わること。固定の判定が、制約の消失と緩和を出し、同値と強化を出さず、見直す日の当日は出さず前日は出すこと
- 週 1 回（と手動）の実行で依存の一覧の Issue と脆弱性の知らせが 1 件ずつになり、下限より古い CLI で NDF のコマンドが止まることは、
  リリースの後に確かめる

## 関連リンク

- 課題: #3370（依存の更新の仕組み）・#3691（対象と確かめを広げる）・#3380（設計レビュー P の依存の更新と供給網の指摘）
- 設計の元: #3347（決定 106「公開から 7 日たった最新の安定版」・決定 107・決定 108）
- [テストを CI に寄せる](ndf-ci-only-tests.md)・[継続的統合のジョブの宣言](cross-refactoring-lint-suite-and-ci-coverage.md)
- [最新の CLI のスモーク](runtime-smoke-cli-versions.md)・[カットポイントで claude を起動し直すラッパー](ndf-relay-segment-restart.md)
