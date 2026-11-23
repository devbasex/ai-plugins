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
- **進め方:** 異常系の 4 行（ディレクトリが無い・JSON でない・同じキーが 2 つ・辞書でない）で落ちるテスト → 最小の実装 → 整理

### Task 3: リポジトリの設定のキーと、検査が設定を読む経路

- **対象ファイル:** `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json`、`plugins/ndf/src/ndf/checks/model.py`、`.ndf/project.json`、`plugins/ndf/tests/checks/test_model.py`
- **変更内容:** 設定に `toolchain`（`workflows`・`python_ci`・`plugin_pyprojects`）と `budget`（`tests`・`ci_jobs`・`unused_deps`・`replaceable`）のキーを足し、`Scope` に `settings` と `repo_files(glob)` を足す。このリポジトリの値（Python 3.11・3.13、上限 4,400・40・3・3）も同じタスクで入れる
- **満たす受け入れ条件:** 3・4・6・7
- **進め方:** スキーマを通る例と断る例、`repo_files` がプラグインの外のファイルを引けることで落ちるテスト → 実装

### Task 4: `limits` と `causes` を 1 項目 1 ファイルへ分ける

- **対象ファイル:** `plugins/ndf/contracts/limits/`（新設・223 + 1 ファイル）、`plugins/ndf/contracts/causes/`（新設・35 + 1 ファイル）、`plugins/ndf/contracts/limits.json`（削除）、`plugins/ndf/contracts/causes.json`（削除）、`plugins/ndf/contracts/schemas/limits.schema.json`、`plugins/ndf/contracts/schemas/causes.schema.json`、`plugins/ndf/src/ndf/contracts/limits.py`、`plugins/ndf/src/ndf/contracts/causes.py`、`plugins/ndf/src/ndf/domains/hooks/tables.py`
- **変更内容:** 分割の実体と、読み手の読み先の切り替え。スキーマは 1 項目の形（`policy.schema.json` 相当）へ変える。hook 側の `load_split` も同じ規則で足す
- **満たす受け入れ条件:** 2
- **進め方:** 分割はスクリプト（`.ndf/tmp/` に置く使い捨て。リポジトリには残さない）で機械的に作り、Task 1 のテストが変えずに通ることで同値を確かめる。hook の経路は標準ライブラリだけで動くことを `contracts/hooks/imports.json` の縛りで保つ。分割の前後で `ndf check` の所要を測り、設計書の「未確認のまま残ること」の行を埋める。戻すときは、この単位の Pull Request を revert すれば元の 2 ファイルへ戻る（分割の前後で中身が同じなので、ほかの単位が分割後の表へ項目を足していなければ 1 コミットで戻せる）

### Task 5: 分割の形を確かめる規則 `contracts-split`

- **対象ファイル:** `plugins/ndf/src/ndf/checks/contracts_split.py`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/tests/checks/test_contracts_split.py`（新設）
- **変更内容:** 一覧に挙げた表がディレクトリであること・1 ファイルが 1 項目だけを持つこと・もとの 1 ファイルが残っていないこと
- **満たす受け入れ条件:** 2
- **進め方:** 1 ファイルへ戻した入力で落ちるテスト → 実装

### Task 6: 配る構成の宣言と規則 `toolchain`

- **対象ファイル:** `plugins/ndf/contracts/data/toolchain.json`（新設）、`plugins/ndf/contracts/schemas/toolchain.schema.json`（新設）、`plugins/ndf/src/ndf/contracts/toolchain.py`（新設）、`plugins/ndf/src/ndf/checks/toolchain.py`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/contracts/hooks/imports.json`、`plugins/ndf/src/ndf/domains/hooks/boot.py`、`plugins/ndf/tests/contracts/test_toolchain.py`（新設）、`plugins/ndf/tests/checks/test_toolchain.py`（新設）、`plugins/ndf/tests/domains/hooks/test_boot.py`
- **変更内容:** 宣言（Python の下限・hook の下限・uv のバージョンと照合値）と、`pyproject.toml`・ワークフロー・`bin/ndf` との突き合わせ。`imports.json` の `boot.python` は消し、`boot.py` の読み先を宣言へ替える
- **満たす受け入れ条件:** 3・4
- **進め方:** 詳細設計書の「処理の手順」の 5 つの組み合わせで落ちるテスト → 実装

### Task 7: 件数の上限の規則 `budget` と置き換えの規則

- **対象ファイル:** `plugins/ndf/src/ndf/checks/budget.py`（新設）、`plugins/ndf/contracts/replaceable/`（新設・3 ファイル）、`plugins/ndf/contracts/schemas/replaceable.schema.json`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/tests/checks/test_budget.py`（新設）
- **変更内容:** 4 つの数え（テストの関数・CI のジョブ・使われない依存・置き換えられる自作）と、置き換えの規則 3 件（`fcntl.flock`・Markdown のテーブルの組み立て・打ち直しのループ）
- **満たす受け入れ条件:** 6・7
- **進め方:** 上限 + 1 の入力・`exempt` の保存先・名前の正規化で落ちるテスト → 実装。実測値（4,312・37・3・3）が上限の内に収まることを `ndf check` の実行で確かめる

### Task 8: CI のワークフローの Python と uv の固定

- **対象ファイル:** `.github/workflows/lint.yml`、`.github/workflows/pytest.yml`、`.github/workflows/glossary.yml`、`.github/workflows/pr-base-guard.yml`、`.github/workflows/script-structure.yml`、`.github/workflows/release-cd.yml`、`.github/workflows/ndf-release-reusable.yml`、`.github/workflows/deps-weekly.yml`、`.github/workflows/runtime-plugin-smoke.yml`、`.github/workflows/runtime-plugin-validate.yml`
- **変更内容:** `setup-uv` の 12 か所へ `version: 0.13.7` を足し、`setup-python` の `python-version` を宣言の一覧（3.11・3.13）の値へ替える。`lint.yml` の `pip install --upgrade uv` は `setup-uv` へ替える
- **満たす受け入れ条件:** 4
- **進め方:** Task 6 の規則が落ちない状態にするのが合格の判定。CI で確かめる（手元でワークフローは走らせない）

### Task 9: 実験版の置き場と規則 `experimental`

- **対象ファイル:** `plugins/ndf/contracts/domains/locations.json`、`plugins/ndf/src/ndf/checks/experimental.py`（新設）、`plugins/ndf/src/ndf/checks/run.py`、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/tests/checks/test_experimental.py`（新設）、`AGENTS.md`、`docs/glossary/glossary.json`
- **変更内容:** 保存先の表へ `plugins/ndf/experimental/` の行を足し、既定で動くものからの参照を落とす。`AGENTS.md` の「安定版と実験版」の置き場を「未定」から実際の保存先へ直す。**設計書の「用語」の表の 5 語（テストダブル・見本・分割した表・テストと CI の予算の 4 語を追加、「安定版と実験版」の保存先を意味の変更）を用語集（`docs/glossary/glossary.json`）へ反映し、`ndf records glossary render` で文書を作り直す**（設計 PR に載せるファイルを `issues/issue-3687-*` に限ったため、用語集の反映をこのタスクへ寄せた）
- **満たす受け入れ条件:** 8
- **進め方:** `src/` からの参照・`README.md` からの参照・実験版どうしの参照で落ちるテスト → 実装

### Task 10: 見本の宣言と `ndf samples verify`

- **対象ファイル:** `plugins/ndf/contracts/live-samples/`（新設）、`plugins/ndf/contracts/schemas/live-sample.schema.json`（新設）、`plugins/ndf/contracts/schemas/io/samples.args.schema.json`（新設）、`plugins/ndf/contracts/schemas/io/samples.result.schema.json`（新設）、`plugins/ndf/contracts/causes/samples_mismatch.json`（新設）、`plugins/ndf/contracts/limits/samples.fetch.json`（新設）、`plugins/ndf/src/ndf/domains/samples/verify.py`（新設）、`plugins/ndf/src/ndf/domains/samples/cli.py`（新設）、`plugins/ndf/src/ndf/route/cli.py`、`plugins/ndf/tests/samples/github/rest/issue.json`（新設）、`plugins/ndf/tests/domains/samples/test_verify.py`（新設）、`plugins/ndf/tests/domains/samples/test_cli.py`（新設）
- **変更内容:** 詳細設計書の「見本の突き合わせ」の 5 関数と、GitHub の REST の見本 1 件・ランタイムの CLI の見本 2 件の宣言。あわせて、**GitHub の見本を読むテストダブルを 1 つ（`plugins/ndf/tests/support/sample_hub.py`）作り、それを使うテストを 1 本置く**（設計書の決定 8。既存の 10 か所の `FakeHub` の寄せ替えは範囲外）
- **満たす受け入れ条件:** 1
- **進め方:** 偽の取得（記述を返す関数とコマンド）で、同じ形・キーが 1 つ増えた形・呼び出しの上限超えで落ちるテスト → 実装。本物の記述の取得は CI の毎日のジョブで初めて走る

### Task 11: 毎日のジョブと、見るブランチの既定

- **対象ファイル:** `.github/workflows/runtime-plugin-smoke-latest.yml`、`scripts/runtime-smoke-latest.py`
- **変更内容:** ジョブ `live-samples` を 1 件足し（matrix なし）、落ちたら相手ごとに 1 件の課題を作る。`ref` の既定を `develop` から `main` へ替える
- **満たす受け入れ条件:** 1
- **進め方:** ワークフローの合否は CI で見る。課題を作る部分は `runtime-smoke-latest.py` の既存のテストに相手の列を足して縛る

### Task 12: 依存を用意できないときの結果 JSON

- **対象ファイル:** `plugins/ndf/bin/ndf`、`plugins/ndf/tests/test_bin_ndf.py`
- **変更内容:** `exec` の前に `uv sync --frozen --no-dev --quiet` を打ち、0 以外なら `stop_deps_missing` を通す
- **満たす受け入れ条件:** 5
- **進め方:** 偽の `uv`（`sync` で 1 を返す）を `PATH` の先頭に置いて、結果 JSON 1 行と 69 で落ちるテスト → 実装

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの行数 | 先に要る単位 |
| --- | --- | --- | ---: | --- |
| コントラクトの分割 | T1・T2・T4 | 2 | 900 | — |
| 設定と検査の土台 | T3・T5 | 2・3・4・6・7 | 350 | コントラクトの分割 |
| 配る構成の宣言 | T6 | 3・4 | 420 | 設定と検査の土台 |
| 件数の上限 | T7 | 6・7 | 450 | 設定と検査の土台 |
| CI の固定 | T8 | 4 | 60 | 配る構成の宣言 |
| 実験版 | T9 | 8 | 260 | 設定と検査の土台 |
| 見本の突き合わせ | T10 | 1 | 590 | コントラクトの分割 |
| 毎日のジョブ | T11 | 1 | 120 | 見本の突き合わせ |
| 依存の用意 | T12 | 5 | 90 | — |

**最初に流す単位は「コントラクトの分割」である。** `limits` と `causes` の分割が入る前にほかの単位が分割後の表へ項目を足すと、足した項目が消える。単位の PR はスプリントブランチ宛てに出し、`cross-review` を収束まで回してからマージする。

並列にできるのは「配る構成の宣言」「件数の上限」「実験版」の 3 つ（いずれも「設定と検査の土台」の後、触るファイルは `run.py`・`rules.json` だけが重なる）と、「依存の用意」（ほかのどの単位とも触るファイルが重ならない）である。`run.py` と `rules.json` の重なりは、3 つの単位が順にスプリントブランチへマージされるため、取り込みの衝突として解く。

## リファクタリングの時機

- **準備のリファクタリングはしない。** 足す 4 規則は既存の `run.py` の並びへ 1 行ずつ足す形で、既存の規則を触らない
- TDD のステップ（落ちるテスト → 最小の実装 → 整理）を各タスクで回す
- スプリントの終わりのリファクタリングは、4 つの規則に共通の「設定の上限を読んで数と比べる」部分が重複していれば、そこで 1 つへ寄せる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 分割した 258 ファイルの差分が大きく、レビューが読み切れない | 分割はスクリプトで機械的に作り、同値であることを Task 1 の退行テストで示す。レビューは読み手（`limits.py`・`causes.py`）の差分と、代表の数ファイルを見る |
| `.github/workflows/` の 11 本が直前のレーン（#3961）の変更と重なる | Task 8 を「配る構成の宣言」の後の独立した単位にし、取り込みの衝突をその単位の中だけで解く |
| 毎日のジョブが本物の記述を取れずに落ち続ける | 取得の失敗は `infra_wait`（75）で、食い違い（`samples_mismatch`）と区別する。課題を作るのは食い違いのときだけにする |
| 置き換えられる自作の数え方が機械で決まらない | 構文木で数えられる 3 件（`fcntl.flock`・テーブルの組み立て・打ち直しのループ）に限り、宣言に挙げたものだけを数える（要求仕様の前提 3） |

## 完了の定義

- 受け入れ条件 1〜9 を満たす（条件 1 と 5 はリリース後テストで実機の確認を行う）
- `ndf check` が 0 件で通り、CI の 15 件の必須チェックが落ちない
- `.ndf/project.json` の `budget` の 4 つの値が、実測した件数を上回っている
