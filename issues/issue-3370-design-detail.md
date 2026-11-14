# #3370 の詳細設計: Renovate の設定・動かすワークフロー・設定の検査のテスト

設計文書 [issue-3370-design.md](issue-3370-design.md) の「構成要素」と「処理の流れ」を、ファイルと設定の項目と関数の粒度へ下ろした文書である。触るものの大半は設定のファイル（JSON・YAML・TOML）で、Python の関数を持つのは設定の検査のテストだけである。このため「関数の仕様」には、テストの補助の関数と、Renovate を動かすワークフローのジョブを 1 つの処理の単位として並べる。

用語の対応（説明文は左の語で書く）:

| この文書の語 | 指すもの |
| --- | --- |
| Renovate の設定 | リポジトリの設定ファイル `.github/renovate.json` |
| 動かすワークフロー | `.github/workflows/renovate.yml` |
| 依存の宣言 | `plugins/ndf-next/pyproject.toml` |
| lock | `plugins/ndf-next/uv.lock` |
| 依存の一覧の Issue | Renovate の Dependency Dashboard（用語集の語） |
| 設定の検査のテスト | `scripts/tests/test_renovate_config.py` |
| 実行の token | GitHub App から実行ごとに作る、期限の短いインストールの token |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/
├── renovate.json            *
├── dependabot.yml
└── workflows/
    └── renovate.yml         *
plugins/ndf-next/
├── pyproject.toml
└── uv.lock
scripts/tests/
└── test_renovate_config.py  *
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `.github/renovate.json` | 新設 | Renovate に、対象・待つ日数・承認・まとめ方・依存ごとの規則を渡す | 無し |
| `.github/workflows/renovate.yml` | 新設 | 週 1 回と手動で、実行の token を作って Renovate を動かす | `.github/renovate.json`（Renovate が既定ブランチから読む） |
| `.github/dependabot.yml` | 変更 | 頭の注記を「Python の依存は Renovate が持つ」へ書き直す。更新の対象（GitHub Actions）は変えない | 無し |
| `plugins/ndf-next/pyproject.toml` | 変更 | `[tool.uv]` に 7 日の待ちを足し、頭の注記に版の選び方と上げる操作を書き、最初の版上げの版を持つ | 無し |
| `plugins/ndf-next/uv.lock` | 変更 | 依存の宣言から `uv lock` で作り直す | `plugins/ndf-next/pyproject.toml` |
| `scripts/tests/test_renovate_config.py` | 新設 | 設定の 3 ファイルの値が不変条件 I3・I5・I6・I7 と決定 3〜8 の形を保つことを縛る | `.github/renovate.json`・`.github/workflows/renovate.yml`・`plugins/ndf-next/pyproject.toml`・`plugins/ndf/scripts/lib/yamlio.py` |

**依存の向き。** 設定の検査のテストが 3 つの設定ファイルを読み、設定ファイルはテストを読まない。動かすワークフローは Renovate の設定を名前で参照せず、Renovate が既定ブランチから自分で見つける。lock は依存の宣言から作るコピーで、逆向きの参照は無い。依存は一方向で、循環は無い。

**読み込みの経路。** Renovate はリポジトリの設定を既定ブランチ（`main`）からだけ読み（`useBaseBranchConfig` の既定）、GitHub の定時のワークフローも既定ブランチの定義で動く。このため、実装の PR が `develop` に入っただけでは Renovate は動かず、リリースで `main` に載った後に動き始める。PR の宛先は設定の `baseBranchPatterns` で `develop` にする。

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。クラスは持たない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `.github/renovate.json` | 設定（JSON のオブジェクト） | 最上位のキーと `packageRules` の 5 規則（新設。値は「内部のデータと定数」） |
| `.github/workflows/renovate.yml` | ワークフロー | `jobs.renovate`（新設。ステップ `token`・`renovate` の 2 つ） |
| `plugins/ndf-next/pyproject.toml` | 依存の宣言 | `[tool.uv]` の `exclude-newer`（新設）・頭の注記（変更）・`dependencies` と `[dependency-groups]` の版（変更） |
| `.github/dependabot.yml` | Dependabot の設定 | 頭の注記（変更） |
| `scripts/tests/test_renovate_config.py` | テストのモジュール | 補助: `load_renovate`・`load_workflow`・`load_pyproject`・`rule_for`・`span_days`（新設）。テスト: `test_wait_days_match`・`test_rules_have_reasons`・`test_httpx_is_pinned`・`test_dbos_is_disabled`・`test_githubkit_note`・`test_scope_is_ndf_next`・`test_approval_and_dashboard`・`test_artifacts_check_always`・`test_vulnerability_alerts_off`・`test_workflow_triggers`・`test_workflow_token_permissions`・`test_pyproject_note_points_to_config`（新設） |

テストの関数は事前条件と副作用を持たない読み取りの確かめだけなので、「関数の仕様」には補助の関数だけを書き、テストごとに縛る中身は「単体テストの観点」に書く。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `jobs.renovate` | `.github/workflows/renovate.yml` | 新設 | 起動: `schedule`（月曜 00:00 UTC）・`workflow_dispatch`。入力: Actions の変数 `RENOVATE_APP_CLIENT_ID`・秘密 `RENOVATE_APP_PRIVATE_KEY`。出力: 終了の状態（成功 / 失敗） | 実行の token を作り、固定した版の Renovate をこのリポジトリだけに対して 1 回動かす | GitHub App がこのリポジトリに入っていて、変数と秘密が置かれている。既定ブランチに Renovate の設定がある | 成功なら、依存の一覧の Issue が今の遅れを表し、承認された更新のブランチと PR が `develop` を起点に 1 本ある。失敗なら、ジョブが失敗の状態で終わる | GitHub API（実行の token の作成と失効・Issue・ブランチ・PR・コミットの状態の書き込み）・PyPI への取得・コンテナの取得（`ghcr.io`）・uv の取得 |
| `load_renovate` | `scripts/tests/test_renovate_config.py` | 新設 | `() -> dict` | Renovate の設定を JSON として読んで返す | 無し | 戻り値は設定の最上位のオブジェクトである | ファイルの読み取り |
| `load_workflow` | `scripts/tests/test_renovate_config.py` | 新設 | `() -> dict` | 動かすワークフローを YAML として読んで返す | 無し | 戻り値はワークフローの最上位の対応表で、キー `on` は文字列のまま残る | ファイルの読み取り |
| `load_pyproject` | `scripts/tests/test_renovate_config.py` | 新設 | `() -> tuple[dict, str]` | 依存の宣言を TOML として読み、中身と生の文字列（頭の注記を見るため）を返す | 無し | 1 つ目は TOML の中身、2 つ目はファイルの文字列そのものである | ファイルの読み取り |
| `rule_for` | `scripts/tests/test_renovate_config.py` | 新設 | `(config: dict, package: str) -> dict` | `packageRules` から、`matchPackageNames` がちょうど `[package]` の規則を 1 つ返す | `config` は `load_renovate` の戻り値 | 戻り値は該当する規則がちょうど 1 つのときのその規則である | 無し |
| `span_days` | `scripts/tests/test_renovate_config.py` | 新設 | `(text: str) -> int` | `"7 days"` の形の期間を日数の整数へ直す | 無し | 戻り値は 1 以上の整数で、`"<整数> day"` か `"<整数> days"` の形だけを受ける | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `jobs.renovate` | 実行の token を作れない | App の秘密鍵が無い・失効した、App がリポジトリから外れた、変数の名前の誤り | ステップ `token`（`actions/create-github-app-token`） | 止める | ジョブが失敗で終わり、Actions の実行の一覧に赤が付く。Renovate は動かず、Issue と PR は変わらない | 後始末は要らない。秘密か App の導入を直してから手動の実行で打ち直せる |
| `jobs.renovate` | Renovate の設定を読めない・検証に落ちる | JSON の誤り・Renovate の版が知らないキー | Renovate の設定の検証（実行の初め） | 止める | Renovate は依存の一覧の Issue に「設定の誤り」を出し、ブランチと PR を作らない。ジョブの終了の状態は未確認（「未確認のまま残ること」） | 設定を直す PR を入れ、リリースで `main` に載った後に打ち直す |
| `jobs.renovate` | 既定ブランチに設定が無い | 実装の PR が `develop` に入っただけで、まだ `main` に載っていない | Renovate のリポジトリの初期化（`requireConfig: required`） | 既定値で続ける | Renovate はこのリポジトリを飛ばし、Issue と PR を作らない。ジョブは成功で終わる | 後始末は要らない。I8 の順序を守る仕組みとして働く |
| `jobs.renovate` | PyPI から版の一覧を取れない | PyPI の障害・ネットワークの誤り | Renovate の依存の取得（`Package lookup error`） | 呼び出し元へ返す（扱いは未確認） | 受け入れ条件 7 が求める「Issue を書き換えず、失敗で終わる」を満たすかは未確認。満たさないときは、Renovate の前に PyPI の JSON API へ 1 回届くかを確かめるステップを足し、届かなければジョブを失敗で終える（設計文書の「未確認のまま残ること」） | 取得が戻った後の定時か手動の実行で打ち直せる。Renovate は Issue を毎回書き直すので、途中の状態は残らない |
| `jobs.renovate` | lock を作り直せない | 上げる先の版どうしが解決できない・7 日に満たない版を uv が拒む | Renovate の lock の作り直し（uv の終了コード） | 呼び出し元へ返す | 更新の PR に警告のコメントと、赤の `renovate/artifacts` が付く（決定 8）。`develop` の宣言と lock は変わらない | 赤の PR はマージしない。直せない依存は規則に固定と理由を足す PR を入れ、次の実行で PR が作り直される |
| `jobs.renovate` | 同じ時刻に 2 つの実行が重なる | 定時の実行の最中に手動の実行を打った | ワークフローの `concurrency`（グループ `renovate`） | 既定値で続ける | 後の実行は前の実行が終わるまで待つ（取り消さない） | 後始末は要らない |
| `jobs.renovate` | 実行が長引く | Renovate のコンテナの取得の遅れ・uv の解決の長期化 | ジョブの `timeout-minutes: 30` | 止める | ジョブが取り消しで終わる | 実行の token は期限で失効する。次の実行で打ち直せる |
| `load_renovate` | ファイルが無い・JSON として読めない | 設定ファイルの削除・書式の誤り | `json.loads` / `Path.read_text` | 呼び出し元へ返す | テストが `FileNotFoundError` か `json.JSONDecodeError` で落ち、pytest の終了コードは 1 | 後始末は要らない。直して打ち直せる |
| `load_workflow` | ファイルが無い・YAML として読めない | ワークフローの削除・書式の誤り | `yamlio.load_yaml` | 呼び出し元へ返す | テストが `FileNotFoundError` か `yamlio.YamlError` で落ち、pytest の終了コードは 1 | 後始末は要らない。直して打ち直せる |
| `load_pyproject` | ファイルが無い・TOML として読めない | 依存の宣言の削除・書式の誤り | `tomllib.loads` | 呼び出し元へ返す | テストが `FileNotFoundError` か `tomllib.TOMLDecodeError` で落ち、pytest の終了コードは 1 | 後始末は要らない。直して打ち直せる |
| `rule_for` | 規則が 0 件か 2 件以上ある | 規則の削除・同じ依存へ規則を重ねた | 関数の中の件数の照合 | 呼び出し元へ返す | `AssertionError`（依存の名前と見つかった件数を含む）でテストが落ちる | 後始末は要らない |
| `span_days` | 期間の形が違う | `"1 week"`・`"P7D"`・`"7d"` などの別の書き方 | 関数の中の正規表現の照合 | 呼び出し元へ返す | `ValueError`（受けた文字列を含む）でテストが落ちる。2 か所の書き方を `"<整数> days"` に揃えることを強いる | 後始末は要らない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `jobs.renovate` | 既定ブランチに設定がない | 何もせず成功で終わる |
| `jobs.renovate` | 設定があり、遅れが無く、開いた更新の PR も無い | 依存の一覧の Issue を閉じる（`dependencyDashboardAutoclose`）。承認を待つ更新だけが残るときに閉じるかは未確認 |
| `jobs.renovate` | 遅れがあり、公開から 7 日と 30 分に満たない版だけ | Issue の「待っている更新」に出し、ブランチを作らない（`minimumReleaseAge` と既定の待ちの余白 30 分。`internalChecksFilter: strict`） |
| `jobs.renovate` | 遅れがあり、7 日を過ぎた版があり、Issue のチェックが入っていない | Issue の「承認を待つ更新」に 1 行（まとまり `plugins/ndf-next の依存`）として出し、ブランチを作らない |
| `jobs.renovate` | 上と同じで、チェックが入っている | `develop` から 1 本のブランチを作り、宣言を書き換え、`uv lock` で lock を作り直して PR を出す。`renovate/artifacts` を緑で付ける |
| `jobs.renovate` | チェックが入っていて、lock を作り直せない | ブランチに宣言の変更だけを置くか（未確認）、PR に赤の `renovate/artifacts` と警告のコメントを付ける |
| `jobs.renovate` | 開いた更新の PR があり、`develop` が進んだ | Renovate が PR を作り直し、lock も uv で作り直す（決定 3） |
| `jobs.renovate` | 対象の依存が httpx | `==0.28.1` の外の版を上げる先にしない |
| `jobs.renovate` | 対象の依存が dbos | 調べず、Issue の更新の候補にも出さない（`enabled: false`） |
| `jobs.renovate` | 今の版が公開から 365 日より古い依存 | プレリリースも上げる先の候補にする（`ignoreUnstable: false`）。httpx は上の固定が優先する |
| `rule_for` | `matchPackageNames == [package]` の規則が 1 件 / 0 件 / 2 件以上 | その規則を返す / 落とす / 落とす |
| `span_days` | `^(\d+) days?$` に当たる / 当たらない | 整数を返す / 落とす |

- 繰り返しの終わる条件: `rule_for` は `packageRules` を 1 回なめて終わる。`jobs.renovate` は Renovate の 1 回の実行で終わり、ワークフローは繰り返さない
- 計算量の注意: 無し（規則は 5 件、依存は 25 件）

**最初の版上げ（実装の PR が手で行う。決定 7）** は次の順に打つ。Renovate の手順ではないため上の表に含めない。

1. 依存の宣言の `[tool.uv]` に `exclude-newer = "7 days"` を足す
2. dbos と httpx を除く依存ごとに、`uv lock --upgrade-package <名前>` で選ばれた版を確かめる。宣言の `==` の版をその版へ書き換える（dev グループの 2 本も含む）
3. 引数なしの `uv lock` を打ち、続けて `uv lock --check` が 0 で終わることを確かめる
4. `uv run --frozen --project plugins/ndf-next pytest plugins/ndf-next/tests -q` が通るまで、上げた版で落ちたコードとテストを直す（受け入れ条件 11）
5. 版が変わった依存の一覧（名前・前の版・後の版）を実装の PR 本文に書く

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| 待つ日数（正） | 設定値 | 依存の宣言の `[tool.uv] exclude-newer` | `"7 days"` |
| 待つ日数（コピー） | 設定値 | Renovate の設定の `minimumReleaseAge` | `"7 days"`（正に合わせる。I6 をテストが縛る） |
| 待ちの余白 | 設定値 | Renovate の既定（`minimumReleaseAgeBuffer`） | 30 分。設定に書かない |
| 日付の無い版の扱い | 設定値 | Renovate の設定の `minimumReleaseAgeBehaviour` | `"timestamp-required"`（日付の無い版を上げる先にしない） |
| 宛先のブランチ | 設定値 | Renovate の設定の `baseBranchPatterns` | `["develop"]` |
| 対象の仕組み | 設定値 | Renovate の設定の `enabledManagers` | `["pep621"]` |
| 対象のファイル | 設定値 | Renovate の設定の `includePaths` | `["plugins/ndf-next/pyproject.toml"]`（lock は隣のものを Renovate が見つける） |
| 依存の一覧の Issue の題 | 設定値 | Renovate の設定の `dependencyDashboardTitle` | `"依存の一覧（plugins/ndf-next）"` |
| 承認の要否 | 設定値 | Renovate の設定の `dependencyDashboardApproval` | `true` |
| Issue を閉じる設定 | 設定値 | Renovate の設定の `dependencyDashboardAutoclose` | `true` |
| lock の作り直しのチェック | 設定値 | Renovate の設定の `statusCheckWhen.artifactError` | `"always"` |
| 脆弱性の知らせ | 設定値 | Renovate の設定の `vulnerabilityAlerts.enabled`・`osvVulnerabilityAlerts` | `false`・`false`（決定 6） |
| ラベル・コミットの接頭辞 | 設定値 | Renovate の設定の `labels`・`commitMessagePrefix` | `["dependencies"]`・`"Update:"`（Dependabot の設定と同じ） |
| 規則: まとめ方 | 設定値 | `packageRules[0]` | `matchFileNames: ["plugins/ndf-next/pyproject.toml"]`・`groupName: "plugins/ndf-next の依存"` |
| 規則: 古い版のプレリリース | 設定値 | `packageRules[1]` | `matchCurrentAge: "> 365 days"`・`ignoreUnstable: false` |
| 規則: httpx の固定 | 設定値 | `packageRules[2]` | `matchPackageNames: ["httpx"]`・`allowedVersions: "==0.28.1"` |
| 規則: DBOS を対象から外す | 設定値 | `packageRules[3]` | `matchPackageNames: ["dbos"]`・`enabled: false` |
| 規則: githubkit の見直しの文 | 設定値 | `packageRules[4]` | `matchPackageNames: ["githubkit"]`・`prBodyNotes` に 1 文 |
| Renovate の本体の版 | 定数 | 動かすワークフローの `renovate-version` | 実装の時点で公開から 7 日たった最新。2026-11-14 の時点では `44.221.6`（2026-11-06 公開） |
| 動かすアクションの版 | 定数 | 動かすワークフローの `uses` | `renovatebot/github-action@v46.3.12`（2026-11-02 公開。v46.3.13 は 7 日に満たない）・`actions/create-github-app-token@v3.3.0` |
| 定時の時刻 | 定数 | 動かすワークフローの `schedule` | `cron: "0 0 * * 1"`（月曜 09:00 JST。Dependabot と同じ曜日と時刻） |
| App のクライアント ID | 設定値 | Actions の変数 `RENOVATE_APP_CLIENT_ID` | 無し（導入のときに人が置く） |
| App の秘密鍵 | 秘密 | Actions の秘密 `RENOVATE_APP_PRIVATE_KEY` | 無し（導入のときに人が置く） |
| 実行の token の権限 | 定数 | 動かすワークフローの `permission-*` | contents・pull-requests・issues・statuses を `write`、checks を `read`。App に与えた権限の中で、さらに実行ごとに絞る |
| Renovate のグローバル設定 | 設定値 | 動かすワークフローの `env` | `RENOVATE_REPOSITORIES: ${{ github.repository }}`・`RENOVATE_ONBOARDING: "false"`・`RENOVATE_REQUIRE_CONFIG: required`・`RENOVATE_BINARY_SOURCE: install` |

**キーの形は固定する版に合わせる。** 2026-11-14 の Renovate の文書は、依存の一覧の Issue の題と閉じる設定を `dependencyDashboard` のオブジェクト（`title`・`autoclose`）に置く。固定する 44.221.6 の設定の定義（`lib/config/options/index.ts` の `44.221.6` のタグ）では、`dependencyDashboard` は真偽値である。題と閉じる設定は、`dependencyDashboardTitle`・`dependencyDashboardAutoclose` という別のキーである。新しい版は古いキーをオブジェクトへ移すと文書にあるため、古いキーで書く。Renovate の版を上げてオブジェクトの形へ書き換えるときは、設定の検査のテストのキーも同じ PR で替える。

**uv の版。** 動かすワークフローは `RENOVATE_BINARY_SOURCE: install` で、Renovate が実行のたびに uv を取得する。依存の宣言に `required-version` が無いため、取得するのはその時点の最新の uv である。CI の `astral-sh/setup-uv` も、リポジトリの根に `required-version` が無いため最新の uv を入れる（setup-uv の README）。両者が同じ時点の最新を使い、食い違えば更新の PR の上で CI の `uv lock --check` が落ちる。依存の宣言に `required-version` を足して固定する形は採らない。手元で別の版の uv を使う開発者の操作が拒まれるためである。

Renovate の設定の全体（実装の PR が置く形。理由の文は日本語で書く）:

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "description": "plugins/ndf-next の Python の依存だけを対象にする（#3370）。GitHub Actions の版は .github/dependabot.yml が持つ",
  "baseBranchPatterns": ["develop"],
  "enabledManagers": ["pep621"],
  "includePaths": ["plugins/ndf-next/pyproject.toml"],
  "dependencyDashboard": true,
  "dependencyDashboardTitle": "依存の一覧（plugins/ndf-next）",
  "dependencyDashboardApproval": true,
  "dependencyDashboardAutoclose": true,
  "minimumReleaseAge": "7 days",
  "minimumReleaseAgeBehaviour": "timestamp-required",
  "internalChecksFilter": "strict",
  "statusCheckWhen": { "artifactError": "always" },
  "vulnerabilityAlerts": { "enabled": false },
  "osvVulnerabilityAlerts": false,
  "labels": ["dependencies"],
  "commitMessagePrefix": "Update:",
  "packageRules": [
    {
      "description": "plugins/ndf-next の更新を 1 本の PR にまとめる（#3370 の決定 3）",
      "matchFileNames": ["plugins/ndf-next/pyproject.toml"],
      "groupName": "plugins/ndf-next の依存"
    },
    {
      "description": "今の版が公開から 365 日より古い依存だけ、プレリリースも上げる先にする（#3370 の決定 4）",
      "matchCurrentAge": "> 365 days",
      "ignoreUnstable": false
    },
    {
      "description": "githubkit 0.17.1 が httpx<1.0.0,>=0.23.0 を求めるため、0.28.1 に固定する（#3370 の決定 5）",
      "matchPackageNames": ["httpx"],
      "allowedVersions": "==0.28.1"
    },
    {
      "description": "DBOS の版は C1+ の続きが上げる。#3370 の対象の外（決定 7）",
      "matchPackageNames": ["dbos"],
      "enabled": false
    },
    {
      "description": "githubkit を上げたら、httpx の固定を見直す（#3370 の決定 5）",
      "matchPackageNames": ["githubkit"],
      "prBodyNotes": ["githubkit を上げる。新しい githubkit が httpx 1.x を許すなら、.github/renovate.json の httpx の規則（==0.28.1）を見直す"]
    }
  ]
}
```

動かすワークフローの形:

```yaml
name: renovate

# plugins/ndf-next の Python の依存の遅れを、依存の一覧の Issue に知らせる（#3370）。
# PR では走らせない（I5）。設定は .github/renovate.json（Renovate が既定ブランチから読む）
on:
  schedule:
    - cron: "0 0 * * 1"  # 月曜 09:00 JST
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: renovate
  cancel-in-progress: false

jobs:
  renovate:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - id: token
        uses: actions/create-github-app-token@v3.3.0
        with:
          client-id: ${{ vars.RENOVATE_APP_CLIENT_ID }}
          private-key: ${{ secrets.RENOVATE_APP_PRIVATE_KEY }}
          repositories: ${{ github.event.repository.name }}
          permission-contents: write
          permission-pull-requests: write
          permission-issues: write
          permission-statuses: write
          permission-checks: read
      - id: renovate
        uses: renovatebot/github-action@v46.3.12
        with:
          renovate-version: 44.221.6
          token: ${{ steps.token.outputs.token }}
        env:
          RENOVATE_REPOSITORIES: ${{ github.repository }}
          RENOVATE_ONBOARDING: "false"
          RENOVATE_REQUIRE_CONFIG: required
          RENOVATE_BINARY_SOURCE: install
```

依存の宣言の頭の注記に足す 3 行（「足す・版を上げるのは…」の行を置き換える）:

```toml
# 版は、公開から 7 日たった最新の安定版にする（[tool.uv] の exclude-newer と .github/renovate.json の minimumReleaseAge。#3370）。
# 版を上げるのは、依存の一覧の Issue（Renovate）でチェックを入れて届く PR である。固定と対象の外の依存と理由は .github/renovate.json にある。
# 手で足す・上げるときも、この宣言と uv.lock を同じ PR で変える。
```

Dependabot の設定の頭の注記（2 行を置き換える）:

```yaml
# Python の依存（plugins/ndf-next）は Renovate が持つ（.github/renovate.json。#3370）。ここは GitHub Actions の版だけを上げる。
```

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 定時の実行と手動の実行 | 依存の一覧の Issue・更新のブランチと PR | ワークフローの `concurrency` のグループ `renovate`。後の実行を待たせ、取り消さない | Renovate は実行のたびに Issue を今の状態で書き直し、ブランチを起点から作り直すため、同じ状態から何度打っても結果は同じである。途中で止まっても次の実行で揃う |
| Renovate の更新の PR と、子の PR（#1142 のレーン） | 依存の宣言と lock | 承認してから 1 本だけ作る（決定 3）。lock の衝突は Renovate が `develop` の上で `uv lock` で作り直して解く | 子の PR が先に入れば、Renovate が次の実行で更新の PR を作り直す |
| 最初の版上げ（実装の PR）と Renovate | 依存の宣言と lock | Renovate の設定が `main` に載るまで Renovate は何もしない（`requireConfig: required`）。App と秘密の導入をリリースの後に行う（I8） | 実装の PR は手で打つ 1 回だけで、繰り返さない |
| 更新の PR の上での開発者の直しと、Renovate の作り直し | 更新のブランチ | Renovate は、自分の外のコミットが載ったブランチを作り直さない（Renovate の既定） | 直しを載せた後は開発者が PR を進める。Renovate へ戻すときは依存の一覧の Issue の「作り直す」のチェックを使う |
| Dependabot と Renovate | 無し | 対象を分ける（Dependabot は GitHub Actions、Renovate は `plugins/ndf-next` の pep621） | 同じ依存を 2 つの仕組みが上げない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `jobs.renovate` | Renovate の実行のログ（読んだ依存・選んだ版・作った PR・取得の誤り） | Actions の実行のログ（Renovate の既定のレベル `info`） | 実行の token・App の秘密鍵（Actions が秘密として伏せ、Renovate も token をログで伏せる） |
| `jobs.renovate` | 遅れ・待っている更新・承認を待つ更新・設定の誤り | 依存の一覧の Issue の本文 | 秘密 |
| `jobs.renovate` | lock の作り直しの成否 | 更新の PR の `renovate/artifacts` のチェックと警告のコメント | 秘密 |
| `jobs.renovate` | githubkit を上げたときの httpx の見直しの文 | 更新の PR の本文（`prBodyNotes`） | 無し |
| `rule_for`・`span_days` | 落ちた理由（依存の名前と件数・受けた文字列） | テストの失敗の出力（`AssertionError`・`ValueError` の文） | 無し |

進捗記録と結果 JSON へは何も書かない。このリポジトリの開発の仕組みで、NDF の工程の外にあるためである。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `jobs.renovate` | `renovatebot/github-action`・`actions/create-github-app-token` | 両者の README と `action.yml`（`gh api repos/renovatebot/github-action/readme`・`gh api repos/actions/create-github-app-token/contents/action.yml?ref=v3.3.0`、2026-11-14） | 使う。App の例の形（token を作って渡す）をそのまま使い、`app-id` は非推奨のため `client-id` を使う |
| `jobs.renovate` | このリポジトリの既存のワークフローの App の token の使い方 | `grep -rln "create-github-app-token" .github/` が 0 件 | 使えない理由: 前例が無い。新しく書く |
| `jobs.renovate` | Renovate の `minimumReleaseAgePackageManagerSync`（uv の待ちの設定を Renovate が読む機能） | 設定の文書の `minimumReleaseAgePackageManagerSync` の節 | 使えない理由: 文書が実験の機能（振る舞いが変わりうる）とする。2 か所に置いてテストで縛る（決定 4） |
| `load_renovate` | 標準の `json` | 既存のテストの読み方（`grep -n "json.loads" scripts/tests/*.py`） | 使う |
| `load_workflow` | `plugins/ndf/scripts/lib/yamlio.py` の `load_yaml` | `grep -rn "yamlio.load_yaml" scripts/tests/` で `test_check_required_checks.py` の使い方を確かめた | 使う。YAML 1.2 で読むため、`on` が真偽値に化けない（PyYAML は化ける。`test_runtime_smoke_versions.py` の注記） |
| `load_pyproject` | 標準の `tomllib`・`plugins/ndf/scripts/lib/deps.py` | `grep -rn "tomllib" scripts/tests plugins/ndf/scripts/lib` が 0 件・`grep -n "^def " plugins/ndf/scripts/lib/deps.py`（uv の環境の用意だけで、TOML を読む関数を持たない） | 使う: `tomllib`（Python 3.11 以上の標準。根の `requires-python` が 3.11 以上）。`deps.py` は使えない理由: TOML を読む関数を持たない |
| `rule_for` | このリポジトリの Renovate の規則を引く部品 | Renovate の設定はこの課題で新設するため、`grep -rln "packageRules" scripts plugins/ndf/scripts` で前例を探した | 使えない理由: 前例が無い。新しく書く |
| `span_days` | `plugins/ndf/scripts/lib` の期間の読み取り | `grep -rn "def .*duration\|def .*days(" plugins/ndf/scripts/lib/*.py scripts/*.py`（`transcript_agents.py` の `_calculate_duration` だけで、記録の行から秒を数える別の用途） | 使えない理由: 受ける形が違う。2 行の正規表現で書く |

## 単体テストの観点

補助の関数の観点と、テストの関数ごとに縛る中身を並べる。テストは `scripts/tests/` に置き、根の全体テストで流れる。`jobs.renovate` の振る舞いはテストで動かせないため、設定の値で縛り、動きはリリース後テストで見る（設計文書の「テスト設計」と「未確認のまま残ること」）。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `jobs.renovate` | リリース後テスト: 手動の実行を 2 回打ち、依存の一覧の Issue が 1 件のまま書き直される | 設定が `main` に無い時点の手動の実行が、Issue と PR を作らずに成功で終わる | token の作成の失敗・lock の作り直しの失敗（検証用のブランチで httpx の規則を一時的に `1.0.dev6` を許す形にして 1 度動かす。決定 8）・PyPI の取得の失敗（「未確認のまま残ること」の確かめ方）。設定の読めない場合・重なり・長引きはリリース後テストで見ない（Renovate と Actions の振る舞いに任せる） |
| `load_renovate` | 実物の設定を読み、`packageRules` を持つ | 無し | ファイルが無い・JSON の誤りで落ちる（実物を壊さず、`tmp_path` の偽のファイルを読ませるテストは書かない。補助は実物の 1 ファイルだけを読むため） |
| `load_workflow` | 実物のワークフローを読み、キー `on` が文字列である | 無し | 読めない YAML で `yamlio.YamlError` |
| `load_pyproject` | 実物の宣言を読み、`tool.uv.exclude-newer` を持つ | 無し | 読めない TOML で `tomllib.TOMLDecodeError` |
| `rule_for` | httpx の規則を 1 つ返す | `matchPackageNames` が 2 つの名前を持つ規則は当たらない | 0 件・2 件で `AssertionError` |
| `span_days` | `"7 days"` → 7・`"1 day"` → 1 | `"0 days"` を拒む（1 以上） | `"P7D"`・`"1 week"`・`"7d"` で `ValueError` |
| `test_wait_days_match` | 依存の宣言の `exclude-newer` と Renovate の `minimumReleaseAge` が同じ日数（I6） | — | 片方を `"6 days"` にすると落ちる |
| `test_rules_have_reasons` | すべての `packageRules` と最上位が空でない `description` を持つ（I3） | — | 理由の文を消すと落ちる |
| `test_httpx_is_pinned` | httpx の規則の `allowedVersions` が `==0.28.1` で、宣言の httpx も `==0.28.1`（I7） | — | 範囲を `<1.0.0` に広げると落ちる |
| `test_dbos_is_disabled` | dbos の規則が `enabled: false`（決定 7） | — | 規則を消すか `true` にすると落ちる |
| `test_githubkit_note` | githubkit の規則の `prBodyNotes` が httpx の規則を指す文を持つ（決定 5） | — | 文を消すと落ちる |
| `test_scope_is_ndf_next` | `enabledManagers == ["pep621"]`・`includePaths == ["plugins/ndf-next/pyproject.toml"]`・`baseBranchPatterns == ["develop"]`（決定 6） | — | 対象の絞りか宛先を外すと落ちる |
| `test_approval_and_dashboard` | `dependencyDashboard`・`dependencyDashboardApproval`・`dependencyDashboardAutoclose` が `true`（受け入れ条件 7・E6） | — | どれかを外すと落ちる |
| `test_artifacts_check_always` | `statusCheckWhen.artifactError == "always"`（決定 8・I4） | — | 設定を外すと落ちる |
| `test_vulnerability_alerts_off` | `vulnerabilityAlerts.enabled` と `osvVulnerabilityAlerts` が `false`（決定 6） | — | 有効にすると落ちる |
| `test_workflow_triggers` | 起動のキーがちょうど `schedule` と `workflow_dispatch` で、`concurrency.cancel-in-progress` が `false`（I5） | — | `pull_request` か `push` を足すと落ちる |
| `test_workflow_token_permissions` | ワークフローの `permissions` が `contents: read` だけで、token のステップの `permission-*` が「内部のデータと定数」の 5 つだけ。`permission-workflows` を持たない | — | `permission-workflows` を足す、ワークフローの `permissions` を広げると落ちる |
| `test_pyproject_note_points_to_config` | 依存の宣言の頭の注記が `.github/renovate.json` を名指し、そのファイルがある（受け入れ条件 12） | — | 設定ファイルを消すか移すと落ちる |
