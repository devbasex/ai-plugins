# #3687: 宣言を 1 か所にそろえ、検査と毎日のジョブで食い違いと量を止める

## 関連リンク

- 要求仕様: [issue-3687-requirements.md](./issue-3687-requirements.md)（正は #3687 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3687-design.md](./issue-3687-design.md)・詳細設計書: [issue-3687-design-detail.md](./issue-3687-design-detail.md)
- 単位の正本: [issue-3687-plan.units.json](./issue-3687-plan.units.json)（下の 2 つの表は同じ中身を人が読む形で写したもの）
- モード: `standard`（公開インタフェース（コマンド・宣言）が変わり、触るファイルが 4 を超える）

## 修正対象

詳細設計書の「地図」の表のとおり。`.ndf/project.json` と `.github/workflows/` の 11 本は地図の外にあり、タスク 3・8 が触る。

## タスク分解

最初のタスクは機能追加の決まりどおり、今の振る舞いを縛る退行テストである。

### Task 1: 今の上限と停止理由の表の読みを縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/contracts/test_limits.py`、`plugins/ndf/tests/contracts/test_causes.py`
- **変更内容:** 分割の前に、`limits.table()` が 223 項目・`causes.table()` が 35 項目を返すことと、代表の 3 項目（`test-run.test`・`launch.weight.test`・`ci_pending`）の値を縛る。分割の後も同じテストが通る
- **満たす受け入れ条件:** 2
- **進め方:** 今のまま通るテストを足す（退行の網）

### Task 2: 分割した表の読み手と、分ける表の一覧

- **対象ファイル:** `plugins/ndf/src/ndf/contracts/__init__.py`、`plugins/ndf/contracts/domains/split-tables.json`（新設）、`plugins/ndf/contracts/facts/fact.schema.json`、`plugins/ndf/tests/contracts/test_split.py`（新設）
- **変更内容:** 詳細設計書の「分割した表の読み手」の `load_split`・`split_tables` と、分ける表の一覧（`limits`・`causes` の 2 行）
- **満たす受け入れ条件:** 2
- **進め方:** 異常系の 5 行（ディレクトリが無い・JSON でない・項目でないファイル（`a.JSON` など）・大小文字だけが違うキーが 2 つ・辞書でない）で落ちるテスト → 最小の実装 → 整理

### Task 3: リポジトリの設定のキーと、検査が設定を読む経路

- **対象ファイル:** `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json`、`plugins/ndf/src/ndf/checks/model.py`、`.ndf/project.json`、`plugins/ndf/tests/checks/test_model.py`
- **変更内容:** 設定に `toolchain`（`workflows`・`python_ci`・`python_compat`・`plugin_pyprojects`・`launchers`）と `budget`（`tests`・`ci_jobs`・`unused_deps`・`replaceable`・`dep_modules`・`dep_exempt`）のキーを足し、`Scope` に `settings` と `repo_files(glob)` を足す。このリポジトリの値（Python 3.11・3.13、下限の確かめに `script-structure.yml` だけで 3.10、上限 4,400・40・3・3、`launchers` に `plugins/ndf/bin/ndf`）は、規則が読み始める単位で入れる（`toolchain` は Task 6・8 の単位、`budget` は Task 7 の単位。この単位ではスキーマとキーを読む経路だけを足し、値を入れても数える規則がまだ無い状態にしない）
- **満たす受け入れ条件:** 3・4・6・7
- **進め方:** スキーマを通る例と断る例、`repo_files` がプラグインの外のファイルを引けることで落ちるテスト → 実装

### Task 4: `limits` と `causes` を 1 項目 1 ファイルへ分ける

- **対象ファイル:** `plugins/ndf/contracts/limits/`（新設・223 + 1 ファイル）、`plugins/ndf/contracts/causes/`（新設・35 + 1 ファイル）、`plugins/ndf/contracts/limits.json`（削除）、`plugins/ndf/contracts/causes.json`（削除）、`plugins/ndf/contracts/schemas/limits.schema.json`、`plugins/ndf/contracts/schemas/causes.schema.json`、`plugins/ndf/src/ndf/contracts/limits.py`、`plugins/ndf/src/ndf/contracts/causes.py`、`plugins/ndf/src/ndf/domains/hooks/tables.py`。**旧ファイルを読む既存の検査・コード・テスト**: `scripts/contracts_lib/limits_decl.py`、`scripts/check-ndf-contracts.py`、`scripts/tests/test_check_ndf_contracts.py`、`plugins/ndf/src/ndf/contracts/facts.py`、`plugins/ndf/contracts/domains/loop-phases.json`、`plugins/ndf/src/ndf/checks/spec_model.py`、`plugins/ndf/src/ndf/checks/children.json`、`plugins/ndf/src/ndf/domains/hooks/budget.py`、`plugins/ndf/src/ndf/domains/hooks/registration.py`、`plugins/ndf/src/ndf/domains/hooks/guards/session.py`、`plugins/ndf/src/ndf/domains/hooks/cli.py`、`plugins/ndf/tests/contracts/conftest.py`、`plugins/ndf/tests/contracts/test_contracts_cli.py`、`plugins/ndf/tests/contracts/test_routing.py`、`plugins/ndf/tests/contracts/test_version.py`、`plugins/ndf/tests/contracts/test_facts.py`、`plugins/ndf/tests/checks/test_child_deps.py`、`plugins/ndf/tests/domains/hooks/test_budget.py`、`plugins/ndf/tests/contracts/test_split.py`（hook 側の `load_split` を CLI 側と同じ入力で引数化して足す）
- **変更内容:** 分割の実体と、読み手の読み先の切り替え。スキーマは 1 項目の形（`policy.schema.json` 相当）へ変える。hook 側の `load_split` も同じ規則で足し、`policy_value` は詳細設計書の「hook の単一キーの読み」のとおり 1 項目のファイルだけを読む。hook の起動のたびに `main.py` が呼ぶ `budget.budget_for`（`policy_name`）は、`policies()` で全件を組み立てず、使う予算の 1 項目だけを読む形に替える（`has_policy` を足す）。SessionStart と `ndf hooks check-budget` の予算の検査は `policies(prefix="hook.")` の 23 項目だけを読む。詳細設計書の「hook の 1 起動あたりの読み込み」の 4 つの事象で、読んだファイルの数が許す上限以下であることを単体テスト（プロセスの中で guard を呼ぶ）で縛る。**旧ファイルを読む既存の経路も同じ単位で移す**: 必須チェック `ndf-contracts-check` が呼ぶ `limits_decl.py` の `DECL` と読み方を `contracts/limits/` へ替える。事実の表の列の参照の形 `limits.json#<接頭辞>`（`facts.py` の `LIMITS_REF` と `loop-phases.json`）を `limits#<接頭辞>` へ改める。`spec_model.py` の `load_json("causes.json")` を `causes.table()` へ替える。`children.json` の表のキーを `contracts/limits/`・`contracts/causes/` へ替える。hook の文言が直し先として示す `limits.json` を `contracts/limits/<方針の名前>.json` へ替える。テストの写しの道具 `contracts_copy` に、分割した表の 1 項目を差し替える形（`contracts_copy("causes/infra_wait.json", ...)`）を足し、上に挙げたテストの旧ファイルの操作をその形へ移す。移した後に `git grep -n 'limits\.json\|causes\.json' -- plugins/ndf scripts .github` が、`causes.jsonl`（実行の記録のファイル名）と変更の履歴を除いて 0 件になることを確かめる
- **満たす受け入れ条件:** 2
- **進め方:** 分割はスクリプト（`.ndf/tmp/` に置く使い捨て。リポジトリには残さない）で機械的に作り、Task 1 のテストが変えずに通ることで同値を確かめる。hook の経路は標準ライブラリだけで動くことを `contracts/hooks/imports.json` の縛りで保つ。分割の前後で `ndf check` の所要と、hook の代表の 4 つの事象の上限の表の読みの所要（プロセスの中で測る）を測り、設計書の「未確認のまま残ること」の行を埋める。戻すときは、この単位の Pull Request を revert すれば元の 2 ファイルへ戻る（分割の前後で中身が同じなので、ほかの単位が分割後の表へ項目を足していなければ 1 コミットで戻せる）

### Task 5: 分割の形を確かめる規則 `contracts-split`

- **対象ファイル:** `plugins/ndf/src/ndf/checks/contracts_split.py`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/tests/checks/test_contracts_split.py`（新設）
- **変更内容:** 一覧に挙げた表がディレクトリであること・1 ファイルが 1 項目だけを持つこと・もとの 1 ファイルが残っていないこと
- **満たす受け入れ条件:** 2
- **進め方:** 1 ファイルへ戻した入力で落ちるテスト → 実装

### Task 6: 配る構成の宣言と規則 `toolchain`

- **対象ファイル:** `plugins/ndf/contracts/data/toolchain.json`（新設）、`plugins/ndf/contracts/schemas/toolchain.schema.json`（新設）、`plugins/ndf/src/ndf/contracts/toolchain.py`（新設）、`plugins/ndf/src/ndf/checks/toolchain.py`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/contracts/hooks/imports.json`、`plugins/ndf/src/ndf/domains/hooks/boot.py`、`plugins/ndf/tests/contracts/test_toolchain.py`（新設）、`plugins/ndf/tests/checks/test_toolchain.py`（新設）、`plugins/ndf/tests/domains/hooks/test_boot.py`、`.ndf/project.json`
- **変更内容:** 宣言（Python の下限・hook の下限・uv のバージョンと照合値）と、`pyproject.toml`・ワークフロー・`bin/ndf` との突き合わせ。ワークフローでは `setup-uv` 以外の uv の導入（`pip install uv`・`pipx install uv`・`astral.sh/uv` の取得）も違反にする。`imports.json` の `boot.python` は消し、`boot.py` の読み先を宣言へ替える。`.ndf/project.json` へ `toolchain` の値を入れる
- **満たす受け入れ条件:** 3・4
- **進め方:** 詳細設計書の「処理の手順」の組み合わせで落ちるテスト → 実装。Task 8 と同じ Pull Request に入れる（規則が入った時点で既存のワークフローの違反を 0 件にする）

### Task 7: 件数の上限の規則 `budget` と置き換えの規則

- **対象ファイル:** `plugins/ndf/src/ndf/checks/budget.py`（新設）、`plugins/ndf/contracts/replaceable/`（新設・3 ファイル）、`plugins/ndf/contracts/schemas/replaceable.schema.json`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/contracts/schemas/io/check.result.schema.json`、`plugins/ndf/tests/checks/test_budget.py`（新設）、`.ndf/project.json`
- **変更内容:** 4 つの数え（テストの関数・CI のジョブ・使われない依存・置き換えられる自作）と、置き換えの規則 3 件（`fcntl.flock`・Markdown のテーブルの組み立て・打ち直しのループ）。4 つの件数は違反の有無にかかわらず `ndf check` の結果 JSON の `metrics.budget` へ出す（`budget.measure` を `run.py` が呼ぶ）。`ndf check` の結果のスキーマ（`check.result.schema.json`）は `metrics` を `additionalProperties: false` で閉じているので、同じ単位で `metrics.budget`（`tests`・`ci_jobs`・`unused_deps`・`replaceable` の 4 つの整数を必須に持ち、ほかのキーを持たない辞書）を足す。`.ndf/project.json` へ `budget` の値（`dep_modules`・`dep_exempt` を含む）を入れる
- **満たす受け入れ条件:** 6・7
- **進め方:** 上限 + 1 の入力・`exempt` の保存先・名前の正規化・依存名の例外で落ちるテストと、違反 0 件の入力でも `metrics.budget` に 4 つの件数が出て、その結果 JSON が `check.result.schema.json` に通ることで落ちるテスト → 実装。実測値（4,312・37・3・3）が上限の内に収まることを `ndf check` の実行で確かめる

### Task 8: CI のワークフローの Python と uv の固定

- **対象ファイル:** `.github/workflows/lint.yml`、`.github/workflows/pytest.yml`、`.github/workflows/glossary.yml`、`.github/workflows/pr-base-guard.yml`、`.github/workflows/script-structure.yml`、`.github/workflows/release-cd.yml`、`.github/workflows/ndf-release-reusable.yml`、`.github/workflows/deps-weekly.yml`、`.github/workflows/runtime-plugin-smoke.yml`、`.github/workflows/runtime-plugin-validate.yml`
- **変更内容:** `setup-uv` の 12 か所へ `version: 0.13.7` を足し、`setup-python` の `python-version` を宣言の一覧（3.11・3.13）の値へ替える。ただし `script-structure.yml` の 2 か所の `3.10`（`script-structure-check` と `ndf-contracts-check` で、根の `scripts/` のスクリプトを対応の下限の版で動かす PATH の `python3`）は替えずに残し、`toolchain.python_compat` で許す（3.11 以上へ置き換えると、3.10 で動かなくなったスクリプトが必須チェックを通る）。`plugin-deps.py` 用の `3.12` は 3.13 へ替える。`lint.yml` の `pip install --upgrade uv` は `setup-uv` へ替える。`script-structure.yml` の差分の判定を 2 つに分ける。今の `NDF_PATHS` と出力 `ndf` は変えず、`ndf` を見る既存の手順（plugin.json の一致・エントリポイントの構文・選べる値の後方互換）は今までどおりそれで動かす。`ndf check` を打つ手順 `Check the NDF plugin` だけを、新しい出力 `ndf_check`（`NDF_PATHS` に `.github/workflows` を足した一覧 `NDF_CHECK_PATHS` に差分が当たれば true。規則 `toolchain` と `budget` が読むリポジトリ側の入力）で動かす。ワークフローだけを変えた Pull Request で増えるのは `ndf check` の 1 回だけで、ほかの NDF の手順は起動しない（`.ndf` と `plugins/ndf`（`pyproject.toml`・`uv.lock`・`bin/ndf`・テスト）は既に `NDF_PATHS` に入っている）
- **満たす受け入れ条件:** 3・4
- **進め方:** **Task 6 と同じ単位・同じ Pull Request に入れる。** 規則 `toolchain` を必須チェック `script-structure-check` の中で有効にするのと、既存のワークフローの違反（`version` 無しの `setup-uv` 12 か所・`3.x`・`3.12` の `python-version`・`pip install --upgrade uv`）を直すのを同じ Pull Request で行い、規則が入った時点で違反 0 件にする。合否は CI で確かめる（手元でワークフローは走らせない）。起動条件は、この Pull Request の差分が `.github/workflows/` を含むので、その CI の `Decide whether the NDF checks apply` の出力が `ndf_check=true` になることで確かめる。あわせて、`.github/workflows/pytest.yml` の 1 行だけを変えるコミットを作業用のブランチで 1 度だけ Pull Request に載せ、`ndf_check=true`・`ndf=false` になって（`ndf check` だけが走り、ほかの NDF の手順は飛ばされる）規則 `toolchain` が違反を出すことを見てから戻す（確かめた結果は単位の Pull Request の本文に書く）

### Task 9: 実験版の置き場と規則 `experimental`

- **対象ファイル:** `plugins/ndf/contracts/domains/locations.json`、`plugins/ndf/src/ndf/checks/experimental.py`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/tests/checks/test_experimental.py`（新設）、`AGENTS.md`、`docs/glossary/glossary.json`
- **変更内容:** 保存先の表へ `experimental/ndf/`（リポジトリの根。配布元 `plugins/ndf/` の外）の行を足し、既定で動くもの（`bin/`・`.claude-plugin/`・`.codex-plugin/` を含む）からの参照を落とす。宣言の保存先の列そのものと規則の実装・テストは検査の対象から外す（詳細設計書の「実験版の参照の検査」の除外の一覧）。`AGENTS.md` の「安定版と実験版」の置き場を「未定」から実際の保存先へ直す。**設計書の「用語」の表の 5 語（テストダブル・見本・分割した表・テストと CI の予算の 4 語を追加、「安定版と実験版」の保存先を意味の変更）を用語集（`docs/glossary/glossary.json`）へ反映し、`ndf records glossary render` で文書を作り直す**（設計 PR に載せるファイルを `issues/issue-3687-*` に限ったため、用語集の反映をこのタスクへ寄せた）
- **満たす受け入れ条件:** 8
- **進め方:** `src/` からの参照・`bin/` からの参照・`README.md` からの参照・`locations.json` の保存先の列・実験版どうしの参照で落ちるテスト → 実装

### Task 10: 見本の宣言と `ndf samples verify`

- **対象ファイル:** `plugins/ndf/contracts/live-samples/`（新設）、`plugins/ndf/contracts/schemas/live-sample.schema.json`（新設）、`plugins/ndf/contracts/schemas/io/samples.args.schema.json`（新設）、`plugins/ndf/contracts/schemas/io/samples.result.schema.json`（新設）、`plugins/ndf/contracts/causes/samples_mismatch.json`（新設）、`plugins/ndf/contracts/limits/samples.fetch.json`（新設）、`plugins/ndf/contracts/schemas/limits.schema.json`（1 項目の形へ `retries`・`peers` を足す）、`plugins/ndf/src/ndf/adapters/http.py`（`fetch_body` に `max_bytes`・`follow` を足す）、`plugins/ndf/tests/adapters/test_notify.py`（`fetch_body` の本文の上限と 3xx のテスト）、`plugins/ndf/src/ndf/domains/samples/verify.py`（新設）、`plugins/ndf/src/ndf/domains/samples/cli.py`（新設）、`plugins/ndf/src/ndf/route/cli.py`、`plugins/ndf/tests/samples/github/rest/issue.json`（新設）、`plugins/ndf/tests/domains/samples/test_verify.py`（新設）、`plugins/ndf/tests/domains/samples/test_cli.py`（新設）、`plugins/ndf/tests/support/sample_hub.py`（新設）、`plugins/ndf/tests/support/test_sample_hub.py`（新設）
- **変更内容:** 詳細設計書の「見本の突き合わせ」の関数と、GitHub の REST の見本 1 件の宣言。GitHub の記述は OpenAPI 3.1 の bundled 版を、`fetch_body` を転送を追わず（`follow=False`）本文の上限（`samples.fetch` の `max_bytes`、64 MiB）付きで 1 URL 1 要求で取り（`request_json` は本文の上限 1 MiB で 14,395,662 バイトの記述を受け取れないため使わない）、文書の全体を検証器へ渡して `#/components/schemas/...` の参照を解く。食い違いの説明は `schema_errors` の整形（`ValidationError.message`。値を含みうる）を使わず、パスと検証の規則だけから組み立てる（`describe`）。GitHub の `issue` の定義は `additionalProperties` を書かないため、定義に無いキーは JSON Schema の検証とは別の判定（`unknown_keys`）で落とす。ランタイムの CLI（claude・codex）の `--help` は既存の `cli-help` の突き合わせが引き続き持ち、この単位では見本の宣言を置かない（同じ `--help` を 2 回打たない）。相手ごとの呼び出しの上限は `contracts/limits/samples.fetch.json` の `peers` に置く。あわせて、**GitHub の見本を読むテストダブルを 1 つ（`plugins/ndf/tests/support/sample_hub.py`）作り、それを使うテストを 1 本置く**（設計書の決定 8。既存の 10 か所の `FakeHub` の寄せ替えは範囲外）
- **満たす受け入れ条件:** 1
- **進め方:** 偽の取得（記述を返す関数）で、同じ形・キーが 1 つ増えた形・呼び出しの上限超え・取得の失敗と別の見本の食い違いが同時に起きる形・転送（3xx）を返す形で落ちるテストと、`fetch_body` へ本物と同じ大きさ（14,395,662 バイト）の本文と 3xx を返す `httpx.MockTransport` を渡すテスト → 実装。偽の記述は本物と同じ参照の構造（`issue` が `#/components/schemas/simple-user` などを `$ref` で指し、null を取る列を `type` の配列で書き、`additionalProperties` を書かない）にする。型の誤りの見本に目印の値を入れ、結果 JSON に出ないことも確かめる。**単位の Pull Request をマージする前に、本物の記述へ 1 度当てる。** 偽の記述だけでは、宣言した URL の実際の OpenAPI（14,395,662 バイト・参照の深さ・`allOf` などの組み合わせ）と見本の組み合わせでの参照の解決と未知のキー・足されたキーの判定の誤りが見つからないため、単位の確かめの手順に次を含める。作業ツリーで `ndf samples verify --peer github` を 1 回打ち（宣言の URL へ要求 1 回。単体テストではなくコマンドの手動の実行で、テストを CI に寄せる設定の対象外）、(1) 結果が `ok`（0）であること、(2) 取った記述を `.ndf/tmp/` へ 1 度保存し、その写しを返す関数を `verify` の `fetch` へ渡して（`uv run python` の 1 回の呼び出し。要求を増やさない）、見本の最上位に 1 キーを足した写しと、記述の `issue` の定義の `properties` へ 1 キーを足した写しで、それぞれ `samples_mismatch`（1）になること、(3) 結果 JSON の `metrics.calls` の `github` が 1 であることを見る。打ったコマンド・終了コード・結果 JSON の最後の 1 行・記述の取得の日時（JST）を単位の Pull Request の本文に書く。`ok` にならなければ、見本か実装を直してから出す（毎日のジョブで初めて本物に当たる形にしない）

### Task 11: 毎日のジョブと、見るブランチの既定

- **対象ファイル:** `.github/workflows/runtime-plugin-smoke-latest.yml`、`scripts/runtime-smoke-latest.py`、`scripts/tests/test_runtime_smoke_latest.py`
- **変更内容:** ジョブ `live-samples` を 1 件（matrix なし）と、課題を作るジョブ `report` を 1 件足す。`ref` の既定を `develop` から `main` へ替える。ランタイムの CLI は `live-samples` に入れない（ランタイムの `--help` は既存の matrix のジョブの `cli-help` が引き続き持つ）
  - **権限の境界**: ワークフロー全体の `permissions` を `contents: read` だけにし、今ある `issues: write` を外す。書き込みの権限（`issues: write`）は `report` のジョブだけが持つ。今の matrix のジョブ `runtime-smoke-latest` は入力の `ref` を取り出して `resolve` とスモークのスクリプトを動かすため、書き込みの権限を継がせない。その最後の手順 `Report the failure as an issue` は消し、課題の報告は `report` へ移す
  - **`live-samples`**: `permissions: contents: read`。`main` を取り出し（入力の `ref` を使わない）、`ndf samples verify --peer github` の結果 JSON（標準出力の最後の 1 行）を `tmp/live-samples/result.json` に書く。食い違いでこの手順は 1（`samples_mismatch`）で落ちるので、成果物（artifact）`live-samples` のアップロードの手順は `if: always()` で動かし、`if-no-files-found: ignore` にする（検証の前に落ちて結果 JSON が無い日は何も上げない）
  - **matrix のジョブの結果を渡す**: `runtime-smoke-latest` の最後に `if: always()` の手順を足し、ランタイムと `job.status` と入れた版の一覧（`$LATEST_VERSIONS`。無くてよい）を `tmp/runtime-smoke-latest/outcome/<ランタイム>.json` に書く（`needs.<job>.result` は matrix の全体で 1 つの値なので、どのランタイムが落ちたかは成果物で渡す）。**別の成果物にはしない。** 今の `Upload smoke artifacts`（`runtime-smoke-latest-<ランタイム>`）を `cli-help` の 2 つの手順と結果を書く手順の後（ジョブの最後）へ移し、`path` に `tmp/runtime-smoke/<ランタイム>` と `tmp/runtime-smoke-latest/outcome/` の 2 つを並べて、最終の状態を 1 回でまとめて上げる（`if: always()`・`if-no-files-found: ignore` は今のまま）。アップロードの回数はランタイムごとに今と同じ 1 回で、成功の日も増えない。成果物の中の並びは 2 つのパスの共通の親 `tmp/` からの相対（`runtime-smoke/<ランタイム>/…`・`runtime-smoke-latest/outcome/<ランタイム>.json`）に変わる（`tests/runtime-smoke/README.md` は成果物の並びを書いていないので直さない）。`report` はこの成果物を落とす（スモークのログも一緒に落ちるが、アップロードを増やさないことを優先する）
  - **`report`**: `needs: [runtime-smoke-latest, live-samples]`、`if: ${{ !cancelled() && contains(needs.*.result, 'failure') && (github.event_name == 'schedule' || ((inputs.ref || 'main') == 'main' && (inputs.floors_ref || 'main') == 'main')) }}`。前段のどれかが失敗で終わった日だけ起動し（`live-samples` が食い違いで 1 を返して失敗しても飛ばされない）、すべて成功の日は起動しない。**課題へ書くのは、検証したコードがすべて `main` の実行（定期の起動か、`ref` と `floors_ref` が空か `main` の手の起動）だけである。** 任意の `ref` か `floors_ref` を試した手の起動では、matrix のジョブがその `ref` のコードを読み取りの権限で動かし、成果物を書けるため、`report` は起動しない（結果は各ジョブの成果物とジョブの要約で見る）。`permissions: contents: read, issues: write`。`main` の `scripts/runtime-smoke-latest.py` だけを疎の取り出し（`persist-credentials: false`）で取り、`actions/download-artifact` で上の成果物を落とし、`report` で読む。結果 JSON の成果物が無ければ（検証の前の失敗）見本の報告は何もせずに終える。`live-samples` が失敗で終わっても、成果物の結果 JSON の `items` の `kind: mismatch` を相手ごとに数え、1 件以上の相手ごとに課題を作る（`kind: unreachable` だけの相手には作らない）。ランタイムの成果物は `status` が `failure` のランタイムだけ、今の `report --runtime` と同じ課題を作る。宣言のコマンドやリポジトリのコード（入力の `ref` のもの）を、このジョブでは動かさない
  - **成果物の値の照合（`report` の側）**: 成果物の中身は課題の題と本文へ入るので、`main` の実行でも次の形に限って使い、外れた値は課題へ写さない。(1) ランタイム名は `main` の `runtime-smoke-latest.py` の `RUNTIMES`（matrix の `runtime` の一覧と同じ `claude`・`codex`。照合の正はこの定数で、matrix と食い違えば今の `--runtime` の `choices` と同じく報告の側が断る）にあるものだけを読む。読むファイルは `RUNTIMES` の名前ごとの `outcome/<ランタイム>.json` だけで、ほかのファイルは読まない。ファイルの中の `runtime` がファイル名と違えば、そのランタイムは報告せずに標準エラーへ出す。(2) `status` は `success`・`failure`・`cancelled` のどれかだけを受け、ほかは報告しない。(3) 版の一覧は、ランタイムごとに許すキー（`VERSION_KEYS`。`claude` は `CLAUDE_CODE_VERSION`、`codex` は `CODEX_VERSION`。`RESOLVERS` が出すキーと同じ）の `<キー>=<版>` の行で、版が既存の `VERSION_RE` に合うものだけを本文へ写す。ほかの行は捨て、1 行も残らなければ今の「解決できなかった」と同じ表示にする。(4) 見本の結果 JSON は `main` の `ndf samples verify` が書いたものだが、同じく `items` の `peer` は `[a-z0-9-]+`、`sample` は `contracts/live-samples/` の宣言の形のパス、`detail` は 1 行 500 文字までに限り、外れた項目は数えない
  - **`report` の引数**: `--samples <結果 JSON のパス>`（見本の報告）と `--outcomes <ディレクトリ>`（ランタイムの報告）を足し、どちらも在るものだけを読む。読むのは JSON のデータだけである
- **満たす受け入れ条件:** 1
- **進め方:** ワークフローの合否は CI で見る。課題を作る部分は `runtime-smoke-latest.py` の既存のテストに、相手の列（`kind: mismatch` のある相手だけに作る・`unreachable` だけでは作らない・結果 JSON が無ければ何もしない）と、ランタイムの成果物（`failure` のランタイムだけに作る）の列を足して縛る。成果物の値の照合は、`RUNTIMES` に無いランタイム名のファイル・ファイル名と中身の `runtime` が違うファイル・許すキーの外の行（`EVIL=1`）・版の形に合わない値（`` 1.0`@x` ``）・形の外の `peer` を入れた入力で、`run_gh` へ渡る題と本文にそれらが入らないことで縛る。`report` の起動の条件（任意の `ref` では起動しない）はワークフローの `if` で持ち、手の起動の 1 回（`ref` に作業用のブランチ）で `report` が飛ばされることを単位の Pull Request の本文に書く。ジョブの数は 2 件増え（37 → 39）、`budget.ci_jobs` の上限 40 の内に収まる

### Task 12: 依存を用意できないときの結果 JSON

- **対象ファイル:** `plugins/ndf/bin/ndf`、`plugins/ndf/tests/bin/test_start.py`
- **変更内容:** `exec` の前に `uv sync --frozen --no-dev --quiet` を打ち、0 以外なら `stop_deps_missing` を通す。`exec` は `uv run --no-sync --no-dev --quiet` へ替え、同期済みの同じ環境を使う（同期の経路を 1 起動に 1 回だけ通す）
- **満たす受け入れ条件:** 5
- **進め方:** 単体テストは子のプロセスを起動しない（`tdd-cycle` の「テストに書かないもの」）。同期の失敗のメッセージ（ログの最終行を含む）を `start.emit_deps_missing` へ直に渡し、結果 JSON 1 行と `deps_missing`（69）になることで落ちるテスト → 実装。シェルのランチャーを通した確認（偽の `uv` で同期を落とす・同期済みで 2 回打つ）はテストに足さず、受け入れ条件 5 のリリース後テスト（実機の確認）で行う。`bin/ndf` の変更は `shellcheck`（lint）で見る

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの行数 | 先に要る単位 |
| --- | --- | --- | ---: | --- |
| コントラクトの分割 | T1・T2・T4 | 2 | 900 | — |
| 設定と検査の土台 | T3・T5 | 2・3・4・6・7 | 350 | コントラクトの分割 |
| 配る構成の宣言と CI の固定 | T6・T8 | 3・4 | 480 | 設定と検査の土台 |
| 件数の上限 | T7 | 6・7 | 450 | 設定と検査の土台 |
| 実験版 | T9 | 8 | 260 | 設定と検査の土台 |
| 見本の突き合わせ | T10 | 1 | 590 | コントラクトの分割 |
| 毎日のジョブ | T11 | 1 | 260 | 見本の突き合わせ |
| 依存の用意 | T12 | 5 | 90 | — |

**最初に流す単位は「コントラクトの分割」である。** `limits` と `causes` の分割が入る前にほかの単位が分割後の表へ項目を足すと、足した項目が消える。単位の PR はスプリントブランチ宛てに出し、`cross-review` を収束まで回してからマージする。

並列にできるのは「配る構成の宣言と CI の固定」「件数の上限」「実験版」の 3 つ（いずれも「設定と検査の土台」の後、触るファイルは `run.py`・`rules.json` だけが重なる）と、「依存の用意」（ほかのどの単位とも触るファイルが重ならない）である。`run.py` と `rules.json` の重なりは、3 つの単位が順にスプリントブランチへマージされるため、取り込みの衝突として解く。

## リファクタリングの時機

- **準備のリファクタリングはしない。** 足す 4 規則は既存の `run.py` の並びへ 1 行ずつ足す形で、既存の規則を触らない
- TDD のステップ（落ちるテスト → 最小の実装 → 整理）を各タスクで回す
- スプリントの終わりのリファクタリングは、4 つの規則に共通の「設定の上限を読んで数と比べる」部分が重複していれば、そこで 1 つへ寄せる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 分割した 258 ファイルの差分が大きく、レビューが読み切れない | 分割はスクリプトで機械的に作り、同値であることを Task 1 の退行テストで示す。レビューは読み手（`limits.py`・`causes.py`）の差分と、代表の数ファイルを見る |
| `.github/workflows/` の 11 本が直前のレーン（#3961）の変更と重なる | Task 8 を「配る構成の宣言と CI の固定」の単位に入れ、取り込みの衝突をその単位の中だけで解く（規則の有効化と違反の直しを分けると、規則の Pull Request が必須チェックで落ちる） |
| 毎日のジョブが本物の記述を取れずに落ち続ける | 取得の失敗は `infra_wait`（75）で、食い違い（`samples_mismatch`）と区別する。課題を作るのは食い違いのある相手だけにする。同じ実行で取得の失敗と別の相手の食い違いが起きたときは、結果は `samples_mismatch`（1）で、`items` に両方が載り、報告側は食い違いのある相手の課題を作る |
| 置き換えられる自作の数え方が機械で決まらない | 構文木で数えられる 3 件（`fcntl.flock`・テーブルの組み立て・打ち直しのループ）に限り、宣言に挙げたものだけを数える（要求仕様の前提 3） |

## 完了の定義

- 受け入れ条件 1〜9 を満たす（条件 1 と 5 はリリース後テストで実機の確認を行う）
- `ndf check` が 0 件で通り、CI の 15 件の必須チェックが落ちない
- `.ndf/project.json` の `budget` の 4 つの値が、実測した件数を上回っている
