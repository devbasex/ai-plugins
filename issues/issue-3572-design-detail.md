# #3572 の詳細設計: ブランチ戦略の設定を `.ndf/branch-strategy.json` のセクション `branch_strategy` の 1 か所から読み、マージの方式 `serial` を足す

設計文書 [issue-3572-design.md](issue-3572-design.md) の「構成要素」のうち、決定 24 の 12・13・14 の行を、ファイルと関数の粒度へ下ろした文書である。
12・13 の行は、設定の解決をセクションから読む形へ直すことと、ブランチ戦略の一覧から戦略が許す値の列を外すことである。14 の行は、merge queue も `strict` も
使えないリポジトリのためのマージの方式 `serial`（決定 32）と、プランと公開範囲から使える方式を決めること（決定 33）である。語（ブランチ戦略のセクション・ブランチ戦略の一覧・マージの方式・
検証環境・バージョンの保存先・本番ブランチ・ベースブランチ・ロック・一時ブランチ）は設計文書の「用語」と「ブランチ戦略のセクション」の意味で使う。不変条件（I1〜I19）と
決定（決定 1〜33）の番号は設計文書と [決定の記録](issue-3572-design-decisions.md) の番号である。

**基準のコード。** 実装の PR https://github.com/devbasex/ai-plugins/pull/3587 の HEAD 84e6c33b4（`origin/sprint/lrel-lock`。2026-11-16 の
PR https://github.com/devbasex/ai-plugins/pull/3636 のマージ）である。決定 24 の 1〜10 の行は 84e6c33b4 にあり、この文書では関数を書かない。
1〜10 の行は、戦略ごとの振る舞い・マージの方式（`merge-queue`・`strict`）・merge queue・バージョンの保存先・検証環境の種類・`merge_group` である。14 の行は 84e6c33b4 に無く、
「マージの方式 `serial`」の各表がファイルと関数へ下ろす。「変更」「削除」は 84e6c33b4 の時点のコードに対する差分である。

**名前。** ブランチ戦略のキーと値は #3613 の改名の後の名前（`release_strategy`・`merge_base_branch`・`release_branch`・`trunk`）で書く。
84e6c33b4 のコードは改名の前の名前を持つ。キーと値は `promotion`・`direct`・`whole`・`selected`、行の id は `github-flow`・`promote-whole`・`promote-selected` である。
#3613 の改名が develop へ入った後に develop を取り込み、名前をそろえてからこの文書の変更を入れる（決定 24 の 11 の行・決定 26）。
定数 `TRUNK` は、そろえた後の `trunk` 戦略の id を指す（84e6c33b4 の `GITHUB_FLOW`）。
ファイルのパスも改名の後の名前で書く。設定のスキーマは `contracts/schemas/ndf-settings/`（84e6c33b4 の `contracts/schemas/declarations/`）に、
その契約の文書は `docs/contracts/ndf-settings.md`（84e6c33b4 の `declarations.md`）に置く。

**取り込みの順序。** この文書の変更は、#3613 の続きの PR（移す行と改名表の列 `to_file`・`where`・`transform`）と一緒に develop へ入る。続きの PR は
#3587 の head ブランチをマージ先にする（決定 29）。

**読む場所。** セクションはメインディレクトリ（既定ブランチの worktree）のファイルから読む（決定 27）。84e6c33b4 で呼ばれた worktree から読んでいた
`base_branch` も、メインディレクトリから読む。読んでいた箇所は `core/route` の `default_base`・`progress/command.py` の差分の範囲・リリースの `base_branch` である。
この読み方の差は設計文書の「人へ残す判断」の行にある。

**84e6c33b4 のまま使うもの。** 次は設定を読む関数を介してセクションの値を受けるだけで、シグネチャ・責務・失敗の扱いを変えない。

| ファイル | 変えない理由 |
| --- | --- |
| `domains/release/bump.py`・`confirm.py`・`tagging.py`・`verify_ref.py`・`assemble.py` の関数と、`land.py`・`cli.py` のうち「マージの方式 `serial`」の表に無い関数 | 戦略・方式・迂回の設定・検証環境・バージョンの保存先を `declaration.py` と `version_store.of` から受ける。変わるのは止まる理由の文言の設定ファイルの名前だけ |
| `domains/release/merge_method.py` の `rules_of`・`review_count` | 入力は ruleset の応答だけで、ファイルを読まない。`decide` と `_mismatch` は `serial` のために変わる（「マージの方式 `serial`」） |
| `domains/worktree/` の `create.py`・`repo.py`・`merged.py`・`merged_decide.py`・`follow.py`・`cli.py` | `worktree.declaration.Declaration` の `branch_flow`・`base_branch`・`production_branch`・`start_channel` を読む。値の出所がセクションに変わるだけで、キーの名前（`release_strategy`・`production_branch`）は同じ |
| `domains/records/mvv.py` と呼び手 `domains/records/cli.py` | ベースブランチを引数 `--base`（必須。`records/cli.py` の `signals.add_argument("--base", required=True, ...)`）で受け、設定ファイルを読まない |
| `domains/hooks/strict.py` | `.ndf/project.json` の `delivery` のうち `channel` が `production` の行だけを読む（`strict.py:367`）。検証環境の行を外しても判定は変わらない |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/
│   ├── causes.json
│   ├── domains/{branch-flows,ndf-files}.json
│   └── schemas/
│       ├── ndf-settings/{branch-strategy *,worktree,project,release}.schema.json
│       └── io/{project.result,release-steps.result,worktree.args}.schema.json
├── docs/contracts/{commands,ndf-settings,files}.md
├── skills/
│   ├── development-workflow/{references/*.md,schemas/project.schema.json}
│   ├── merged/SKILL.md
│   ├── release/{SKILL.md,references/release-steps.md}
│   └── worktree/{SKILL.md,references/ndf-settings.md,schemas/worktree.schema.json}
├── src/ndf/
│   ├── adapters/github.py
│   ├── core/
│   │   ├── branch_strategy.py *
│   │   └── route/__init__.py
│   ├── domains/
│   │   ├── project/{check,cli,measure_flow,write}.py
│   │   ├── release/{cli,declaration,land,merge_decide,merge_method,records_trail,serial *,version_store}.py
│   │   └── worktree/declaration.py
│   └── progress/{command.py,gate/release.py}
└── tests/
    ├── core/test_branch_strategy.py *
    ├── domains/release/{test_release_serial *,test_release_merge_method}.py
    └── （フィクスチャを直すテスト。下の表）
```

**依存の向き。** 一方向で、循環は無い。新設の `core/branch_strategy.py` が依存するのは 2 つだけである。

- `ndf.contracts`: スキーマと事実の表を読む
- `ndf.adapters.git`: メインディレクトリと既定ブランチを読む

新設の `domains/release/serial.py` が依存するのは `ndf.adapters.github`・`ndf.adapters.ci`・`domains/release/required_checks` の 3 つで、`land` を呼ばない。
`land` が `serial` を呼ぶ（`land` → `serial` → アダプタの一方向）。

セクションを読む 5 つの領域は `core/branch_strategy` を呼び、互いを呼ばない。5 つは、リリース `domains/release`・スプリントの経路 `domains/worktree`・
マージ先の既定 `core/route`・リポジトリの解析 `domains/project`・工程の記録 `progress` である。
`core` から `domains` への import は 84e6c33b4 で 0 件で、この変更でも 0 件のままにする。数えたコマンドは次のとおりである。

```bash
git grep -l 'from ndf.domains' origin/sprint/lrel-lock -- plugins/ndf-next/src/ndf/core
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/core/branch_strategy.py` | 新設 | `.ndf/branch-strategy.json` をメインディレクトリから読み、セクションのスキーマで確かめ、既定を埋めた値を返す。セクションを読むどの領域もこれだけを通す | `ndf.contracts`・`ndf.contracts.facts`・`ndf.adapters.git` |
| `plugins/ndf-next/contracts/schemas/ndf-settings/branch-strategy.schema.json` | 新設 | セクションのキーと組み合わせの検査（戦略が許す値・`trunk` 戦略で必須のキー・読まれない値）を持つ。中身は確かめ 8 の案（決定 28）。解析の回答に当たる部分を `$defs.flow`、回答の検査を `$defs.flow_answer` に分け、`$defs.section` は `flow` を参照する。`production_merge.method` の列挙は `merge-queue`・`strict`・`serial` | 無し |
| `plugins/ndf-next/contracts/domains/ndf-files.json` | 変更 | 行 `branch-strategy`（`path: branch-strategy.json`・`keys`・`main_keys` が `["branch_strategy"]`）を足し、`worktree`・`project`・`release` の行の `keys`・`main_keys` から移したキーを外す | 無し |
| `plugins/ndf-next/contracts/domains/branch-flows.json` | 変更 | 列 `start`・`doc_prs`・`integrate`・`review_kinds` を外し、`summary` の保存先の記述をセクションへ直す（決定 24 の 13 の行） | 無し |
| `plugins/ndf-next/contracts/schemas/ndf-settings/worktree.schema.json` | 変更 | `base_branch`・`production_branch`・`branch_flow` を外す | 無し |
| `plugins/ndf-next/contracts/schemas/ndf-settings/project.schema.json` | 変更 | `production_merge` と `$defs.DeliveryVerify`・`delivery` の行の `verify` と `channel` の値 `verify` を外す | 無し |
| `plugins/ndf-next/contracts/schemas/ndf-settings/release.schema.json` | 変更 | `version_store` を外す（決定 27） | 無し |
| `plugins/ndf-next/contracts/schemas/io/project.result.schema.json` | 変更 | 解析の書き出しの行の説明の保存先をセクションのファイルへ直す | 無し |
| `plugins/ndf-next/contracts/schemas/io/worktree.args.schema.json` | 変更 | `--flow-record` の説明を「セクションのコピー」へ直す | 無し |
| `plugins/ndf-next/docs/contracts/ndf-settings.md` | 変更 | 契約のスキーマと保存先の表から生成し直す（`ndf check` の spec-docs が差分を落とす） | 生成元のスキーマ |
| `plugins/ndf-next/docs/contracts/files.md` | 変更 | 同上（保存先の表の行 `branch-strategy`） | 生成元の表 |
| `plugins/ndf-next/docs/contracts/commands.md` | 変更 | サブコマンドの説明のうち、設定ファイルの名前を書いた行をセクションのファイルへ直す | 無し |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 変更 | ブランチ戦略・本番ブランチ・ベースブランチ・マージの方式・迂回の設定・検証環境・バージョンの保存先をセクションから読む | `ndf.core.branch_strategy`・`ndf.contracts.facts` |
| `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 変更 | 保存先の値をセクションの `release.version_store` から受ける | `declaration` |
| `plugins/ndf-next/src/ndf/domains/release/records_trail.py` | 変更 | 迂回の記録に書く設定の保存先をセクションのファイルとキー `branch_strategy.production_merge.admin` にする | `ndf.core.branch_strategy` |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | 止まる理由の文言の設定の保存先をセクションのキーへ直す。方式 `serial` の順（ロック・一時ブランチ・確かめ・マージ・第 1 親の確かめ）と、リポジトリの事実を読む `_method_of` を足す（決定 32・33） | `serial`・`merge_method`・`ndf.adapters.github` |
| `plugins/ndf-next/src/ndf/domains/release/merge_method.py` | 変更 | 値 `serial` と、公開範囲・持ち主の種類・規則の読み取りの可否・キューの有無から使える方式の一覧を決める判定を足す（決定 33）。純粋な処理のまま | `ndf.adapters.github` の定数だけ |
| `plugins/ndf-next/src/ndf/domains/release/serial.py` | 新設 | 本番ブランチのロックの取り・延長・返し、一時ブランチの作成・消去、PR の head で観測したチェックの名前、マージコミットの第 1 親の確かめ（I19・決定 32） | `ndf.adapters.github`・`ndf.adapters.ci`・`required_checks` |
| `plugins/ndf-next/src/ndf/adapters/github.py` | 変更 | `repo_info`・`ref_head`・`matching_refs`・`create_ref`・`update_ref`・`delete_ref`・`git_commit`・`create_commit`・`merge_branches` を足す。どれも ruleset を書かず、`update_ref` は `force: false` だけを送る | `githubkit`（今のまま） |
| `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | 変更 | `Approval` に欄 `tested_base`（`serial` で確かめた本番ブランチの HEAD。ほかは None）を足す | 無し |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `REJECT_ONLY` に `combination_failed` を足す。`untested_merge`（本番はマージ済み）でも次の手 `rerun` を出し、`items` に `lock`・`conflict`・`serial` の行を足す | `land` |
| `plugins/ndf-next/contracts/causes.json` | 変更 | 行 `combination_failed`（`std_type: validation`・`exit: precondition`・`route: conductor`。`queue_removed` と同じ形）と `untested_merge`（`std_type: validation`・`exit: failed`・`route: conductor`）を足す | 無し |
| `plugins/ndf-next/contracts/schemas/io/release-steps.result.schema.json` | 変更 | `approval.tested_base` と、`items` の種類 `lock`・`conflict`・`serial` を足す | 無し |
| `plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 変更 | `Declaration` がセクションの値を持ち、ブランチ戦略のプロパティをセクションから返す | `ndf.core.branch_strategy` |
| `plugins/ndf-next/src/ndf/core/route/__init__.py` | 変更 | `default_base` がセクションの `base_branch` を読む | `ndf.core.branch_strategy` |
| `plugins/ndf-next/src/ndf/progress/command.py` | 変更 | スプリントの差分の範囲のベースブランチと条件の語 `prod_branch` をセクションから読む | `ndf.core.branch_strategy` |
| `plugins/ndf-next/src/ndf/progress/gate/release.py` | 変更 | 本番ブランチをセクションから読む | `ndf.core.branch_strategy` |
| `plugins/ndf-next/src/ndf/domains/project/check.py` | 変更 | 解析の入力の宣言として、`.ndf/worktree.json` の代わりにセクションを読む | `ndf.core.branch_strategy` |
| `plugins/ndf-next/src/ndf/domains/project/cli.py` | 変更 | ブランチ運用を測るブランチの名前をセクションから受ける | `check` |
| `plugins/ndf-next/src/ndf/domains/project/measure_flow.py` | 変更 | 戦略が許す値・既定・レビューの単位の形を、ブランチ戦略の一覧と `worktree.schema.json` の代わりにセクションのスキーマから読む | `ndf.core.branch_strategy` |
| `plugins/ndf-next/src/ndf/domains/project/write.py` | 変更 | ベースブランチ・本番ブランチ・ブランチ運用の書き出し先をセクションのファイルにする | `ndf.core.branch_strategy`・`measure_flow` |
| `plugins/ndf-next/skills/worktree/SKILL.md` | 変更 | ブランチ戦略の設定の保存先の説明をセクションのファイルへ直す | 無し |
| `plugins/ndf-next/skills/worktree/references/ndf-settings.md` | 変更 | `.ndf/worktree.json` の説明から移したキーを外し、セクションのファイルへの案内を置く | 無し |
| `plugins/ndf-next/skills/worktree/schemas/worktree.schema.json` | 変更 | 移したキーを外す（契約のスキーマとは別のファイル。2 つの `diff` は一致しない） | 無し |
| `plugins/ndf-next/skills/development-workflow/schemas/project.schema.json` | 変更 | `production_merge` と検証環境の行を外す（同上） | 無し |
| `plugins/ndf-next/skills/development-workflow/references/production-merge.md` | 変更 | マージの方式と迂回の設定の保存先をセクションのキーへ直す。プランと公開範囲から方式を選ぶ表と、`serial` を使うリポジトリが必須のチェックを出す workflow の `push` の `branches` に `ndf-serial/**` を足し、`concurrency` の `group` を `${{ startsWith(github.ref, 'refs/heads/ndf-serial/') && github.ref || github.run_id }}`・`cancel-in-progress: true` にする手順を書く（一時ブランチを作った時点の M の run を、マージコミットの push が取り消す。決定 32・33） | 無し |
| `plugins/ndf-next/skills/development-workflow/references/project-analysis.md` | 変更 | 解析が書くファイルにセクションのファイルを足し、`.ndf/worktree.json` のブランチの項目を外す | 無し |
| `plugins/ndf-next/skills/development-workflow/references/parallel.md` | 変更 | ブランチ戦略の設定の保存先をセクションへ直す | 無し |
| `plugins/ndf-next/skills/development-workflow/references/parallel-work.md` | 変更 | 同上 | 無し |
| `plugins/ndf-next/skills/development-workflow/references/glossary.md` | 変更 | 同上（用語の説明の保存先） | 無し |
| `plugins/ndf-next/skills/merged/SKILL.md` | 変更 | 同上（マージ後のクリーンアップが読む行） | 無し |
| `plugins/ndf-next/skills/release/SKILL.md` | 変更 | マージの方式・検証環境・バージョンの保存先の設定の保存先をセクションへ直す | 無し |
| `plugins/ndf-next/skills/release/references/release-steps.md` | 変更 | 同上 | 無し |
| `plugins/ndf-next/tests/core/test_branch_strategy.py` | 新設 | セクションの読み取り・既定・スキーマの組み合わせの検査（確かめ 8 の例）を縛る | `ndf.core.branch_strategy` |
| `plugins/ndf-next/tests/domains/release/test_release_branch_strategy.py` | 新設 | リリースの解決がセクションだけを読み、ほかの 3 つの設定ファイルを開かないことを縛る | `declaration`・`version_store` |
| `plugins/ndf-next/tests/domains/release/test_release_serial.py` | 新設 | `serial.py` の関数と `land`・`land_bump` の `serial` の順（設計文書の「テスト設計」の p6-onefile:2-4 の行）を、偽の GitHub で縛る。足したアダプタの関数の要求の形（`force: false`・削除できる接頭辞）と状態ごとの戻り値も、録った応答でここで縛る | `serial`・`land`・`ndf.adapters.github` |
| `plugins/ndf-next/tests/domains/release/test_release_merge_method.py` | 変更 | 使える方式の表（p6-onefile:2-5 の行）の組み合わせを足す | `merge_method` |

### フィクスチャを直すテスト

次の 27 本は、84e6c33b4 で旧い保存先へ値を書いてテストを準備する。どれもフィクスチャをセクションのファイルへ移すだけで、縛る振る舞いを変えない。

| 旧い保存先 | 値 |
| --- | --- |
| `.ndf/worktree.json` | `base_branch`・`production_branch`・`branch_flow` |
| `.ndf/project.json` | `production_merge`・検証環境の行 |
| `.ndf/release.json` | `version_store` |

一覧は次のコマンドの結果である。

```bash
git grep -l -E 'branch_flow|"production_branch"|production_branch=|"base_branch"|production_merge|version_store|"channel": "verify"|promotion' origin/sprint/lrel-lock -- plugins/ndf-next/tests
```

| テストのファイル（`plugins/ndf-next/tests/` から） |
| --- |
| `domains/project/test_project_cli.py`・`domains/project/test_project_measure.py` |
| `domains/release/test_release_approval.py`・`test_release_assemble.py`・`test_release_bump.py`・`test_release_confirm.py`・`test_release_files.py`・`test_release_merge_method.py`・`test_release_plan.py`・`test_release_pr_check.py`・`test_release_verify_ref.py`・`test_release_version_store.py` |
| `domains/test_branch_flows.py`・`domains/test_ndf_settings.py` |
| `domains/worktree/conftest.py`・`test_worktree_decide.py`・`test_worktree_env_decide.py`・`test_worktree_envs.py`・`test_worktree_git.py`・`test_worktree_merged.py` |
| `progress/gate/test_gate_release.py`・`progress/test_invoke.py`・`progress/test_run.py`・`progress/test_sprint_templates.py` |
| `route/test_cli.py`・`route/test_flows.py`・`skills/test_schema_refs.py` |

## クラスと関数の構造

型の関係は設計文書の「構造」の図を指し、ここでは描き直さない。

| ファイル | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/core/branch_strategy.py` | `BranchStrategy`・`BranchStrategyError`・モジュール | `path_of`・`main_dir`・`load`・`problems`・`defaults`・`allowed`・`default_branch`・`BranchStrategy.as_record`（新設）。定数 `FILE_ROW`・`SECTION`・`SCHEMA` |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | `Declaration`・`VerifyDecl`・モジュール | `production_admin`・`merge_method_of`・`base_branch`・`production_branch`・`flow_of`・`review_problems`・`optional_verify`・`_parse`（変更）・`version_store_of`（新設）・`_trusted_project_path`・`_branch_flow`・`_flow_row`（削除）。定数 `BRANCH_FILE`・`PRODUCTION_KEY`・`FLOW_CHOICES`・`PROJECT_FILE` を外し、`GITHUB_FLOW` を `TRUNK` にする。欄 `Declaration.version_store` を外す。`verify_of`・`optional_verify_ref` は文言だけが変わる |
| `plugins/ndf-next/src/ndf/domains/release/version_store.py` | モジュール | `of`（変更。シグネチャは変えない） |
| `plugins/ndf-next/src/ndf/domains/release/records_trail.py` | モジュール | 迂回の記録の本文を作る関数の文言だけが変わる。`PROJECT_FILE` の import を `branch_strategy.path_of` の相対パスへ置き換える |
| `plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | `Declaration`・モジュール | `load`（変更）。`Declaration.branch_flow`・`base_branch`・`production_branch`・`start_channel`（変更）。欄 `Declaration.strategy` を足す。定数 `START_CHANNELS` を外す |
| `plugins/ndf-next/src/ndf/core/route/__init__.py` | モジュール | `default_base`（変更）。定数 `BRANCH_DECLARATION`・`BASE_KEY` を外す |
| `plugins/ndf-next/src/ndf/progress/command.py` | モジュール | `_sprint_range`・`facts_of` の語 `prod_branch`（変更）。定数 `WORKTREE_FILE` を外す |
| `plugins/ndf-next/src/ndf/progress/gate/release.py` | モジュール | `_off_production`（変更） |
| `plugins/ndf-next/src/ndf/domains/project/check.py` | モジュール | `load`・`declared_branches`（変更） |
| `plugins/ndf-next/src/ndf/domains/project/cli.py` | モジュール | `_flow_branches`（変更） |
| `plugins/ndf-next/src/ndf/domains/project/measure_flow.py` | モジュール | `default_flow`・`review_allowed`・`answer_problem`・`_start_from_table`・`declared_flow`・`drift`（変更）・`_rows`（削除）。定数 `WORKTREE_SCHEMA` を外す |
| `plugins/ndf-next/src/ndf/domains/project/write.py` | `Inputs`・モジュール | `_worktree_part` を `_strategy_part` へ（変更）・`merge_worktree`・`merge_flow`（変更）。定数 `WORKTREE_TAG` を `STRATEGY_TAG` へ。欄 `Inputs.worktree` を `Inputs.strategy` へ |

| `plugins/ndf-next/src/ndf/domains/release/serial.py` | `LockState`・`LockHold`・`Built`・`SerialStop`・モジュール | `lock_ref`・`temp_ref`・`lock_message`・`release_message`・`parse_lock`・`is_free`・`acquire`・`wait_lock`・`extend`・`still_mine`・`release`・`sweep`・`build`・`observed_names`・`untested_parent`（新設）。定数 `LOCK_PREFIX`・`TEMP_PREFIX`・`REBUILD_LIMIT`・`LOCK_POLL_SECONDS` |
| `plugins/ndf-next/src/ndf/domains/release/merge_method.py` | `RepoFacts`・`Method`・モジュール | `usable`・`rules_state`（新設）・`decide`・`_mismatch`（変更）。定数 `SERIAL` を足し `METHODS` に入れる。欄 `Method.usable` を足す |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | `Lander`・`Landed` | `_method_of`・`land`・`_bump_by_method`・`_resume_merged`（変更）・`_watch_names`・`_land_serial`・`_serial_round`（新設）・`_released`（変更）。欄 `Lander.now`（UTC の時刻を返す関数）と `Landed.tested_base` を足す |
| `plugins/ndf-next/src/ndf/adapters/github.py` | `GitHub`・`RepoInfo`・`GitCommit`・`BranchMerge` | `repo_info`・`ref_head`・`matching_refs`・`create_ref`・`update_ref`・`delete_ref`・`git_commit`・`create_commit`・`merge_branches`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | `Approval` | 欄 `tested_base`（既定 None）を足す。`as_json` は欄をそのまま出す（関数は変えない） |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | モジュール | `_merge_result`（変更）。定数 `REJECT_ONLY` に `combination_failed`、`UNTESTED_MERGE` を足す |

次の 4 つは「関数の仕様」には書かない。設定ファイルの名前を書いた文字列だけが変わり、責務と失敗の扱いを変えないためである。
`verify_of`・`optional_verify_ref`・`records_trail` の本文の関数・`land.py` と `merge_method.py` の既存の止まる理由の文言である（`serial` のために変える関数は「マージの方式 `serial`」に書く）。

## 関数の仕様

ファイルの列は `plugins/ndf-next/src/ndf/` を省く。

### セクションの読み取り（`core/branch_strategy.py`）

| 関数 | ファイル | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `path_of` | `core/branch_strategy.py` | 新設 | `path_of(main: Path) -> Path` | セクションのファイルのパスを保存先の表から作る | 無し | `main / <places の ndf の path> / <ndf-files の branch-strategy の path>`。名前を写さない | 無し |
| `main_dir` | `core/branch_strategy.py` | 新設 | `main_dir(root: Path) -> Path` | `root` の属するリポジトリのメインディレクトリを返す | 無し | `git rev-parse --git-common-dir` の親。git の外なら `root` | git（rev-parse） |
| `load` | `core/branch_strategy.py` | 新設 | `load(root: Path) -> BranchStrategy` | メインディレクトリのセクションを読み、スキーマで確かめ、既定を埋める（決定 27・28） | 無し | ファイルが無ければ `declared` が偽で、どの欄も `defaults()` の値（ブランチの名前は None）。あれば `problems` が空のときだけ値を返す。`release_strategy` は書いた値か既定の `merge_base_branch` | ファイルの読み込み・git（rev-parse） |
| `problems` | `core/branch_strategy.py` | 新設 | `problems(doc: object) -> list[str]` | ファイルの中身をセクションのスキーマで確かめ、誤りを位置つきで返す | 無し | 誤りが無ければ空。各行は `<JSON の位置>: <理由>`。スキーマの `if`/`then` の誤りも同じ形 | 契約のスキーマの読み込み |
| `defaults` | `core/branch_strategy.py` | 新設 | `defaults() -> dict[str, Any]` | セクションのキーの既定をスキーマの `default` から返す | 無し | `release_strategy`・`start`・`doc_prs`・`integrate` の 4 つの既定（`merge_base_branch`・`base`・`base`・`check`）。表やコードに写さない | 契約のスキーマの読み込み |
| `allowed` | `core/branch_strategy.py` | 新設 | `allowed(release_strategy: str, key: str) -> tuple[str, ...]` | 戦略が許すキーの値をスキーマの `if`/`then` から返す | `key` は `start`・`doc_prs`・`integrate`・`reviews` のどれか | `reviews` は書ける PR の種類、ほかは許す値。行が表に無ければ空 | 契約のスキーマの読み込み |
| `default_branch` | `core/branch_strategy.py` | 新設 | `default_branch(root: Path) -> str \| None` | `origin` の既定ブランチの名前を返す（セクションに本番ブランチ・ベースブランチが無いときの既定） | 無し | `git symbolic-ref --short refs/remotes/origin/HEAD` の `origin/` を外した値。読めなければ None | git（symbolic-ref） |
| `BranchStrategy.as_record` | `core/branch_strategy.py` | 新設 | `as_record(self) -> dict[str, Any]` | 既定を埋めたセクションを、スプリントの開始時の記録とマージ後のクリーンアップが読む形で返す | 無し | キー `release_strategy`・`start`・`doc_prs`・`integrate`・`reviews` と、書いてあれば `base_branch`・`production_branch` | 無し |

### リリースの解決（`domains/release/declaration.py`・`version_store.py`）

| 関数 | ファイル | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `production_admin` | `domains/release/declaration.py` | 変更 | `production_admin(root: Path) -> bool` | セクションの `production_merge.admin` が真かを返す（#3506 の I1） | 無し | `branch_strategy.load(root).admin`。セクションを読めない・スキーマに合わなければ偽（迂回しない。84e6c33b4 の「読めない宣言は迂回しない」のまま） | ファイルの読み込み |
| `merge_method_of` | `domains/release/declaration.py` | 変更 | `merge_method_of(root: Path) -> str \| None` | セクションの `production_merge.method` を返す（決定 17） | 無し | 書いていなければ None。値の確かめはスキーマが行い、この関数は表の列挙を見ない | ファイルの読み込み |
| `base_branch` | `domains/release/declaration.py` | 変更 | `base_branch(root: Path, production: str) -> str` | セクションの `base_branch`（既存のブランチ戦略の同期の PR のマージ先）を返す | 無し | 書いていればその値、無ければ `production` | ファイルの読み込み |
| `production_branch` | `domains/release/declaration.py` | 変更 | `production_branch(root: Path) -> str` | セクションの `production_branch` を返す | 無し | 書いていればその値、無ければ `branch_strategy.default_branch(root)` | ファイルの読み込み・git（symbolic-ref） |
| `flow_of` | `domains/release/declaration.py` | 変更 | `flow_of(root: Path) -> str` | セクションの `release_strategy` が選ぶブランチ戦略の一覧の行の id を返す | 無し | `facts.ids("branch-flows", release_strategy=<値>)[0]`。戦略が許す値は確かめない（スキーマが確かめ済み） | ファイルの読み込み |
| `review_problems` | `domains/release/declaration.py` | 変更 | `review_problems(root: Path) -> list[dict[str, str]]` | `trunk` 戦略で、`impl`・`sprint` のどちらも実装の差分を cross-review しない破れを返す（#3119 の I2） | 無し | 行が `TRUNK` でなければ空。書ける PR の種類の破れは返さない（スキーマの `if`/`then` が読む時点で落とす。決定 28）。入力はセクションだけ | ファイルの読み込み |
| `optional_verify` | `domains/release/declaration.py` | 変更 | `optional_verify(root: Path) -> VerifyDecl \| None` | セクションの `verify` を返す（決定 21・27） | 無し | 書いていなければ None。あれば `VerifyDecl`（種類ごとの鍵は 84e6c33b4 と同じ）。行の数と鍵の欠けの確かめはスキーマの `oneOf` が行う | ファイルの読み込み |
| `version_store_of` | `domains/release/declaration.py` | 新設 | `version_store_of(root: Path) -> Mapping[str, Any] \| None` | セクションの `release.version_store` を返す（決定 22・30） | 無し | 書いていなければ None。あればオブジェクトのまま（種類の選択は `version_store.of`） | ファイルの読み込み |
| `_parse` | `domains/release/declaration.py` | 変更 | `_parse(raw: dict) -> Declaration` | `.ndf/release.json` の手順・CHANGELOG・書き換えるファイルを読む | `raw` は JSON のオブジェクト | `version_store` を読まない。旧いキーが残ったファイルで止めるのは #3613 の旧いキーで止まる読み取り（決定 29） | 無し |
| `of` | `domains/release/version_store.py` | 変更 | `of(root: Path, decl: Declaration, flow: str) -> VersionStore` | セクションの保存先からアダプタを選ぶ（I18） | 無し | 保存先の値を `declaration.version_store_of(root)` から受ける。選び方と止まり方は 84e6c33b4 のまま。止まる理由の保存先は `branch_strategy.release.version_store` | ファイルの読み込み |

### セクションを読むほかの領域

| 関数 | ファイル | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `load` | `domains/worktree/declaration.py` | 変更 | `load(main_dir: Path) -> Optional[Declaration]` | 共有設定と個人設定を重ね、セクションの値を `Declaration.strategy` に載せる | 無し | 共有設定が無ければ None（84e6c33b4 のまま。worktree の運用を使わないリポジトリ）。あれば `strategy` に `branch_strategy.load(main_dir)` | ファイルの読み込み・git（rev-parse） |
| `Declaration.branch_flow` | `domains/worktree/declaration.py` | 変更 | `branch_flow(self) -> dict[str, Any]` | スプリントの開始時の記録とマージ後のクリーンアップが読む値を返す | 無し | `self.strategy.as_record()`。共有設定の `branch_flow` を読まない | 無し |
| `Declaration.base_branch` | `domains/worktree/declaration.py` | 変更 | `base_branch(self) -> Optional[str]` | セクションの `base_branch` を返す | 無し | 書いていなければ None（呼び手 `repo.base_branch` が origin の既定ブランチへ倒す。84e6c33b4 のまま） | 無し |
| `Declaration.production_branch` | `domains/worktree/declaration.py` | 変更 | `production_branch(self) -> Optional[str]` | セクションの `production_branch` を返す | 無し | 書いていなければ None | 無し |
| `Declaration.start_channel` | `domains/worktree/declaration.py` | 変更 | `start_channel(self) -> str` | セクションの `start` を返す | 無し | `base` か `production`。値の確かめはスキーマが行い、この関数は止めない | 無し |
| `default_base` | `core/route/__init__.py` | 変更 | `default_base(repo: Path) -> Optional[str]` | マージ先の既定を、セクションの `base_branch`、無ければ origin の既定ブランチにする | 無し | `branch_strategy.load(repo).base_branch` か `branch_strategy.default_branch(repo)`。セクションがスキーマに合わなければ `RouteError`（84e6c33b4 は壊れた JSON を黙って無視した） | ファイルの読み込み・git |
| `_sprint_range` | `progress/command.py` | 変更 | `_sprint_range(context: Mapping[str, Any], cwd: Path) -> tuple[Git, str]` | スプリントブランチとベースブランチの分岐点からの範囲を返す | 無し | ベースブランチは `branch_strategy.load(cwd).base_branch`。無いか `-` で始まれば `FactUnknown`（84e6c33b4 と同じ文言の保存先をセクションへ直す） | ファイルの読み込み・git |
| `facts_of` の語 `prod_branch` | `progress/command.py` | 変更 | （語の求め方。`lambda: bool`） | 本番ブランチが設定に書かれているかを返す | 無し | `branch_strategy.load(cwd).production_branch is not None` | ファイルの読み込み |
| `_off_production` | `progress/gate/release.py` | 変更 | `_off_production(now: Target, cwd: Path) -> bool` | 読み直した PR のマージ先が本番ブランチと違うかを返す | 無し | 本番ブランチは `branch_strategy.load(cwd).production_branch`。読めなければ偽（84e6c33b4 のまま、副命令が確かめて止まる） | ファイルの読み込み |
| `load` | `domains/project/check.py` | 変更 | `load(main: Path) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]` | 解析の宣言と、セクション（書いたままの値）を読む | 無し | 2 つ目はセクションのファイルの `branch_strategy`（無ければ None）。スキーマに合わなければ `JsonFileError` | ファイルの読み込み |
| `declared_branches` | `domains/project/check.py` | 変更 | `declared_branches(section: Optional[dict[str, Any]]) -> tuple[str, ...]` | セクションのベースブランチと本番ブランチの名前を返す | 無し | `base_branch`・`production_branch` のうち文字列のもの | 無し |
| `_flow_branches` | `domains/project/cli.py` | 変更 | `_flow_branches(tree: measure_repo.Tree, section: Optional[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]` | ブランチ運用を測るベースブランチと本番ブランチをセクションから決める | 無し | 84e6c33b4 と同じ規則（無ければ既定ブランチ）で、読む先がセクション | 無し |
| `default_flow` | `domains/project/measure_flow.py` | 変更 | `default_flow() -> dict[str, Any]` | 書いていないセクションが使う 4 項目を返す | 無し | `branch_strategy.defaults()` の値。ブランチ戦略の一覧の列を読まない | 契約のスキーマの読み込み |
| `review_allowed` | `domains/project/measure_flow.py` | 変更 | `review_allowed(kind: str) -> tuple[list[str], list[str]]` | 種類が許す `checks` と `diff` を返す | `kind` は PR の種類 | セクションのスキーマの `$defs.reviews` の列挙 | 契約のスキーマの読み込み |
| `answer_problem` | `domains/project/measure_flow.py` | 変更 | `answer_problem(value: Any) -> Optional[str]` | ブランチ運用の回答が、セクションのスキーマのうち回答に当たる部分に合わなければ理由を返す | `value` は解析の 4 項目（`release_strategy`・`start`・`integrate`・`doc_prs`）と `reviews` だけを持つ回答（`as_item` の測った値と `write.flow_problem` が渡す答え） | `contracts.schema_errors(value, f"{branch_strategy.SCHEMA}#/$defs/flow_answer")` の先頭 3 行。戦略が許す値と書ける PR の種類の破れもここに出る。`trunk` の回答に `production_merge`・`release` が無くても止めない（その 2 つは解析の回答に含まれず、セクション全体の検査 `branch_strategy.problems` が見る） | 契約のスキーマの読み込み |
| `_start_from_table` | `domains/project/measure_flow.py` | 変更 | `_start_from_table(starts: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]` | 動いているスプリントブランチが少なければ、戦略が許す分岐元を出す | 無し | 許す分岐元は `branch_strategy.allowed(<行>, "start")`。証跡のコマンドはセクションのスキーマを指す | 契約のスキーマの読み込み |
| `declared_flow` | `domains/project/measure_flow.py` | 変更 | `declared_flow(section: Optional[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], bool]` | セクションを既定で埋めた 4 項目・書いたレビューの行・書いたかを返す | 無し | 4 項目のキーは `release_strategy`・`start`・`integrate`・`doc_prs`。セクションが合わなければ `ValueError` | 契約のスキーマの読み込み |
| `drift` | `domains/project/measure_flow.py` | 変更 | `drift(section: Optional[dict[str, Any]], item: Optional[dict[str, Any]]) -> list[dict[str, Any]]` | セクションと測った値のずれを返す | 無し | 84e6c33b4 の判定のまま。比べる宣言の値がセクション | 契約のスキーマの読み込み |
| `_strategy_part` | `domains/project/write.py` | 変更 | `_strategy_part(inp: Inputs, written_before: dict[str, Any]) -> tuple[list[File], list[dict[str, Any]], dict[str, str], tuple[str, ...], dict[str, Any]]` | セクションのベースブランチ・本番ブランチ・ブランチ運用を書く（84e6c33b4 の `_worktree_part`） | 無し | 書き先はセクションのファイル。ファイルが無ければ `{"version": 1, "branch_strategy": {...}}` で作る（解析で新しく作るのは C7 の対象外）。書くのは、キーの無い項目と前の解析の値のまま（指紋が一致する）の項目だけ（設計文書の「集約」の表）。書く前の中身が `branch_strategy.problems` で合わなければ書かない | ファイルの読み込み |
| `merge_worktree` | `domains/project/write.py` | 変更 | `merge_worktree(existing: dict[str, Any], branches: dict[str, Any], origin: str, written_before: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]` | セクションに欠けた `base_branch`・`production_branch` を足す | `existing` はセクション（オブジェクト） | 84e6c33b4 の規則のまま。指紋のキーは `branch-strategy.json#<キー>` | 無し |
| `merge_flow` | `domains/project/write.py` | 変更 | `merge_flow(doc: dict[str, Any], value: Any, origin: str, written_before: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]` | ブランチ運用の 4 項目とレビューの行をセクションの直下へ書く | `doc` はセクション | 84e6c33b4 は `branch_flow` の 1 キーへまとめて書いた。セクションでは各キーを直下に置き、指紋は `branch-strategy.json#branch_flow` の 1 つで 5 キーの組をまとめて持つ | 無し |

### マージの方式 `serial`（`domains/release/serial.py`・`merge_method.py`・`land.py`・`adapters/github.py`・`cli.py`）

順は決定 32 の 1〜6 で、関数は次の分担で持つ。`land` が順を回し、`serial` が ref とロックを扱い、`merge_method` が使える方式を決め、アダプタが REST を送る。

| 関数 | ファイル | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `lock_ref` | `domains/release/serial.py` | 新設 | `lock_ref(production: str) -> str` | 本番ブランチのロックのブランチの名前を返す | `production` は空でない | `ndf-lock/<production>` | 無し |
| `temp_ref` | `domains/release/serial.py` | 新設 | `temp_ref(production: str, number: int, attempt: int) -> str` | 一時ブランチの名前を返す | `attempt` は 1 以上 | `ndf-serial/<production>/<number>-<attempt>` | 無し |
| `lock_message` | `domains/release/serial.py` | 新設 | `lock_message(owner: int, approved: str, expires: datetime) -> str` | ロックを取るコミットのメッセージを作る | `expires` は UTC | 1 行目 `ndf-lock: 持ち主` と、行 `持ち主: #<owner>`・`承認済みのコミット: <SHA>`・`期限: <ISO 8601 の UTC>` | 無し |
| `release_message` | `domains/release/serial.py` | 新設 | `release_message(owner: int) -> str` | ロックを返すコミットのメッセージを作る | 無し | 1 行目 `ndf-lock: 返した` と行 `持ち主: #<owner>` | 無し |
| `parse_lock` | `domains/release/serial.py` | 新設 | `parse_lock(message: str) -> LockState \| None` | ロックのコミットのメッセージを読む | 無し | 1 行目が `ndf-lock: 持ち主` か `ndf-lock: 返した` で、要る行がそろえば `LockState`。ほかは None | 無し |
| `is_free` | `domains/release/serial.py` | 新設 | `is_free(state: LockState \| None, now: datetime) -> bool` | ロックを取ってよいかを返す | 無し | ブランチが無い（呼び手が None を渡さない）・`返した`・期限が `now` 以前なら真。`parse_lock` が None のコミットは偽（NDF の形でないコミットを上書きしない） | 無し |
| `acquire` | `domains/release/serial.py` | 新設 | `acquire(hub: GitHub, production: str, owner: int, approved: str, expires: datetime, now: datetime) -> LockHold \| None` | ロックを 1 回だけ取りにいく | 無し | 取れたら `LockHold`。ほかの持ち主が期限の内か、fast-forward でなく断られたら None | GitHub: `ref_head`・`git_commit`・`create_commit`・`create_ref` か `update_ref`（`force: false`）。ロックのコミットのツリーは `EMPTY_TREE`（`.github/workflows/` を持たないため、ロックのブランチへの push で workflow が起動しない。決定 32）。ブランチが無いとき、最初のロックのコミットは親を持たない |
| `wait_lock` | `domains/release/serial.py` | 新設 | `wait_lock(hub: GitHub, production: str, owner: int, approved: str, budget: float, clock: Callable[[], float], now: Callable[[], datetime], sleep: Callable[[float], None]) -> LockHold` | ロックが取れるまで `LOCK_POLL_SECONDS` ごとに `acquire` を繰り返す | `budget` は 0 より大きい | 期限は取った時刻 + `budget` | `acquire` と同じ・待ち |
| `extend` | `domains/release/serial.py` | 新設 | `extend(hub: GitHub, hold: LockHold, expires: datetime, now: datetime) -> LockHold` | 作り直しの前にロックの期限を延ばす | `hold` は自分のロック | 期限を `expires` にしたコミット（ツリーは `EMPTY_TREE`）を足した `LockHold` | GitHub: `git_commit`・`create_commit`・`update_ref`（`force: false`） |
| `still_mine` | `domains/release/serial.py` | 新設 | `still_mine(hub: GitHub, hold: LockHold, now: datetime) -> bool` | マージの直前にロックがまだ自分のもので期限の内かを返す | 無し | ロックのブランチの HEAD が `hold.commit` で、`hold.expires` が `now` より後なら真 | GitHub: `ref_head` |
| `release` | `domains/release/serial.py` | 新設 | `release(hub: GitHub, hold: LockHold) -> str \| None` | ロックを返す | 無し | HEAD が `hold.commit` なら `返した` のコミット（ツリーは `EMPTY_TREE`）を足して None。ほかの持ち主へ移っていれば足さずに None。返せなければ理由の文字列 | GitHub: `ref_head`・`git_commit`・`create_commit`・`update_ref`（`force: false`） |
| `sweep` | `domains/release/serial.py` | 新設 | `sweep(hub: GitHub, production: str) -> list[str]` | ロックを取った直後に、前の持ち主が残した一時ブランチを消す | ロックを持っている | 消したブランチの名前の一覧。`ndf-serial/<production>/` の下だけを消す | GitHub: `matching_refs`・`delete_ref` |
| `build` | `domains/release/serial.py` | 新設 | `build(hub: GitHub, production: str, number: int, attempt: int, approved: str) -> Built` | 本番ブランチの HEAD（M）から一時ブランチを作り、承認済みのコミット（A）をマージする | ロックを持っている | `Built(base=M, ref=<一時ブランチ>, sha=<マージコミット>)`。A が既に M に含まれていれば `sha` は M | GitHub: `ref_head`・`create_ref`・`merge_branches` |
| `observed_names` | `domains/release/serial.py` | 新設 | `observed_names(hub: GitHub, sha: str) -> tuple[str, ...]` | PR の head で走ったチェック（check run と commit status）の名前を返す（決定 32 の 1） | 無し | 名前の重なりを除いた並び。走っていなければ空 | GitHub: `check_runs`・`statuses`（`RequiredChecks.runs` を通す） |
| `untested_parent` | `domains/release/serial.py` | 新設 | `untested_parent(hub: GitHub, merge_sha: str, tested_base: str) -> str \| None` | マージコミットの第 1 親が確かめた M かを見る（決定 32 の 6） | 無し | 第 1 親が `tested_base` なら None、違えば第 1 親の SHA | GitHub: `commit_parents` |
| `rules_state` | `domains/release/merge_method.py` | 新設 | `rules_state(error: GitHubError \| None) -> str` | 規則の読み取りの結果を 3 つに分ける（決定 33 の 2 行目） | 無し | 失敗が無ければ `read`。状態 403 で本文に `PLAN_REFUSAL` の語を含めば `plan`。ほかは `unknown` | 無し |
| `usable` | `domains/release/merge_method.py` | 新設 | `usable(repo: RepoFacts, facts: RulesFacts) -> tuple[str, ...] \| None` | 公開範囲・持ち主の種類・規則の読み取りの可否・キューの有無から使える方式を返す（決定 33 の表） | 無し | Organization の public は 3 つ。個人の public は `strict`・`serial`。private で `plan` は `serial`。private で `read` はキューがあれば 3 つ、無ければ `strict`・`serial`。`unknown` は None（行を決めない） | 無し |
| `decide` | `domains/release/merge_method.py` | 変更 | `decide(flow: str \| None, declared: str \| None, facts: RulesFacts, review: str \| None, required_flow: str, repo: RepoFacts) -> Method` | 宣言の方式・ruleset の事実・リポジトリの事実から、使う方式か止まる理由を決める（I15・決定 33） | 無し | 一覧が None で宣言が `serial` なら `serial`（ruleset と照らさない）。None でほかの方式なら「本番ブランチの規則を読めない」。宣言が一覧に無ければ問題に一覧を書き、`Method.usable` に一覧を載せる。ほかは 84e6c33b4 の判定 | 無し |
| `_mismatch` | `domains/release/merge_method.py` | 変更 | `_mismatch(declared: str, facts: RulesFacts) -> str \| None` | 宣言と ruleset の食い違いを返す | 無し | `serial` で ruleset に merge queue があれば「ruleset に merge queue があり、PR のマージを GitHub が断る」。ほかは 84e6c33b4 のまま | 無し |
| `_method_of` | `domains/release/land.py` | 変更 | `_method_of(self, pull: Pull) -> tuple[str \| None, Landed \| None]` | リポジトリの事実と規則を読み、方式か止まった結果を返す | 無し | `repo_info` を読み、`branch_rules` の失敗を `rules_state` で分け、`decide` へ渡す。止まった結果の `mismatch` の 2 つ目に ruleset の値と使える方式の一覧 | GitHub: `repo_info`・`branch_rules`・`merge_queue`・`review_decision` |
| `land` | `domains/release/land.py` | 変更 | `land(self, number: int) -> Landed` | 方式が `serial` なら `_land_serial` へ回す | 無し | 84e6c33b4 の分岐に `SERIAL` の行を足す。`serial` では迂回を呼ばない（I17） | `_land_serial` と同じ |
| `_watch_names` | `domains/release/land.py` | 新設 | `_watch_names(self, sha: str, names: tuple[str, ...], deadline: float) -> tuple[str \| None, tuple[ci.Check, ...]]` | 規則を読まずに、名前で指したチェックを `sha` で時刻 `deadline` まで待つ | `names` は空でない | `_watch` と同じ形（止まった理由の種類か None、落ちたチェック） | GitHub: `check_runs`・`statuses`・待ち |
| `_land_serial` | `domains/release/land.py` | 新設 | `_land_serial(self, pull: Pull, deadline: float, judge: bool = True) -> Landed` | 決定 32 の 1〜6 を回す | `pull` は本番の PR かバージョンアップの PR | `judge` が真なら、チェックの待ちの前後と一時ブランチの確かめの後に承認の記録を読み直す（`_refusal`。84e6c33b4 の `_blocker` と同じ順）。マージしたら `Landed(merged=True, method="serial")` と `tested_base`。どの経路でも、抜けるときに一時ブランチを消してロックを返す（`try`/`finally`） | GitHub（下の各関数）・ロックのコミット |
| `_serial_round` | `domains/release/land.py` | 新設 | `_serial_round(self, pull: Pull, hold: LockHold, names: tuple[str, ...], attempt: int) -> tuple[Landed \| None, LockHold, bool]` | 一時ブランチを 1 回作って確かめ、マージまで進める | ロックを持っている | (結果, 延ばしたロック, 作り直すか)。HEAD が M から動いていれば結果 None・作り直す真 | GitHub: `build`・`_watch_names`・`ref_head`・`merge` |
| `_bump_by_method` | `domains/release/land.py` | 変更 | `_bump_by_method(self, pull: Pull, method: str, deadline: float) -> Landed` | 方式でバージョンアップの PR を入れる | 無し | `serial` は `_behind` を見ずに `_land_serial(pull, deadline, judge=False)`（一時ブランチが M と合わせて確かめるため）。ほかは 84e6c33b4 のまま | `_land_serial` と同じ |
| `_released` | `domains/release/land.py` | 変更 | `_released(self, pull: Pull, done: Landed) -> Landed` | 本番ブランチへ入れた結果に承認を足す | 無し | 84e6c33b4 のまま、承認に `done.tested_base` を写す（`Approval.tested_base`。入出力の契約の `approval.tested_base`） | GitHub: `pull` |
| `_resume_merged` | `domains/release/land.py` | 変更 | `_resume_merged(self, number: int, pull: Pull, deadline: float) -> Landed` | マージ済みの PR からの再開で、`serial` が自分でマージした（承認の経路 `gate`）なら、マージコミットで走る本番ブランチの push の CI を待つ（`untested_merge` からの `rerun`） | 無し | 通れば 84e6c33b4 の再開の結果。落ちれば `test_failed` か `check_failed`、待ちが切れれば `ci_pending`。経路 `github-merge` では待たない（決定 32 の「守れないこと」） | GitHub: `_watch_names` |
| `repo_info` | `adapters/github.py` | 新設 | `repo_info(self) -> RepoInfo` | 公開範囲と持ち主の種類を読む | 無し | `RepoInfo(private: bool, owner_type: str)`（`GET /repos/{o}/{r}` の `private`・`owner.type`） | GitHub（REST） |
| `ref_head` | `adapters/github.py` | 新設 | `ref_head(self, branch: str) -> str \| None` | ブランチの HEAD の SHA を読む | 無し | `GET /git/ref/heads/{branch}` の `object.sha`。404 なら None | 同上 |
| `matching_refs` | `adapters/github.py` | 新設 | `matching_refs(self, prefix: str) -> list[str]` | 接頭辞に当たるブランチの名前を読む | 無し | `GET /git/matching-refs/heads/{prefix}` の名前（`refs/heads/` を外す） | 同上 |
| `create_ref` | `adapters/github.py` | 新設 | `create_ref(self, branch: str, sha: str) -> bool` | ブランチを作る | 無し | 201 なら真、409・422 なら偽（既にある。STD16）。ほかは `GitHubError` | 同上 |
| `update_ref` | `adapters/github.py` | 新設 | `update_ref(self, branch: str, sha: str) -> bool` | ブランチを fast-forward で進める | 無し | `force: false` だけを送る。200 なら真、422（fast-forward でない）なら偽 | 同上 |
| `delete_ref` | `adapters/github.py` | 新設 | `delete_ref(self, branch: str) -> None` | 一時ブランチを消す | `branch` は `ndf-serial/` で始まる（ほかは `ValueError`。ロックのブランチと本番ブランチを消さない） | 204 か 404・422（無い）で返る | 同上 |
| `git_commit` | `adapters/github.py` | 新設 | `git_commit(self, sha: str) -> GitCommit` | コミットのメッセージ・ツリー・親を読む | 無し | `GitCommit(message, tree, parents)`。`commit_parents` はこれを呼ぶ形にする | 同上 |
| `create_commit` | `adapters/github.py` | 新設 | `create_commit(self, message: str, tree: str, parents: Sequence[str]) -> str` | ロックのコミットを作る（ref は動かさない） | 無し | 作ったコミットの SHA。`serial` は `tree` に `EMPTY_TREE` だけを渡す | 同上 |
| `merge_branches` | `adapters/github.py` | 新設 | `merge_branches(self, base: str, head: str, message: str) -> BranchMerge` | 一時ブランチへ承認済みのコミットをマージする（`POST /merges`。STD16） | `base` は `ndf-serial/` で始まる | 201 は `BranchMerge(sha=<SHA>, conflict=False)`、204 は `sha=None`（マージするものが無い）、409 は `conflict=True` | 同上 |
| `_merge_result` | `domains/release/cli.py` | 変更 | `_merge_result(args: argparse.Namespace, done: land.Landed, version: str \| None = None) -> dict` | `serial` の止まり方を結果 JSON にする | 無し | `items` に `lock`・`conflict`・`serial` の行を足す。`untested_merge` はマージ済みでも次の手 `rerun` だけを出す（`_moves` の `released=True`）。`combination_failed` は `reject` だけ（`REJECT_ONLY`） | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | クリーンアップと再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `main_dir` | git の外・共通ディレクトリを読めない | git でない場所で実行した | `GitError` | 既定値で続ける（`root`） | 無し（呼ばれた場所のファイルを読む。84e6c33b4 の `_trusted_project_path` と同じ） | 無し |
| `load` | ファイルを読めない・JSON でない | 権限・壊れたファイル | `OSError`・`ValueError` | 止める（`BranchStrategyError`。位置と理由を持つ） | 読む側が原因 `precondition`・理由にファイルのパスと誤り | 何も書いていない。ファイルを直して再実行する |
| `load` | スキーマに合わない | 行が許さない値・`trunk` で必須のキーが無い・読まれない値・表に無い値 | `problems` | 止める（`BranchStrategyError`。先頭 5 行の位置と理由） | 読む側が原因 `precondition`・理由にスキーマの誤りの位置（受け入れ条件 2m） | 同上。既定の戦略・方式・保存先へ倒さない |
| `problems` | スキーマを読めない | ndf-next のパッケージのファイルの欠け | `contracts.ContractError` | 呼び出し元へ返す | `stopped`・`invalid` | ndf-next を入れ直す |
| `defaults` | スキーマを読めない | 同上 | `contracts.ContractError` | 呼び出し元へ返す | 同上 | 同上 |
| `allowed` | スキーマを読めない | 同上 | `contracts.ContractError` | 呼び出し元へ返す | 同上 | 同上 |
| `default_branch` | origin の既定ブランチが無い | `origin/HEAD` を持たない clone | `GitError` | 既定値で続ける（None） | 呼び手が扱う（下の `production_branch` の行） | 無し |
| `production_admin` | セクションを読めない・合わない | 上の `load` の行 | `BranchStrategyError` | 既定値で続ける（偽） | 迂回しない。セクションの誤りは同じ実行の `merge_method_of` か `flow_of` が `precondition` で出す | 無し |
| `merge_method_of` | セクションを読めない・合わない | 同上 | `BranchStrategyError` | 止める（`DeclarationError` に包む） | `stopped`・`precondition`・スキーマの誤りの位置 | 何も変えていない（I15） |
| `base_branch` | 同上 | 同上 | `BranchStrategyError` | 止める（`DeclarationError`） | `stopped`・`precondition` | 同上 |
| `production_branch` | セクションに無く、既定ブランチも読めない | `origin/HEAD` が無いリポジトリでセクションが無い | `default_branch` の None | 止める（`DeclarationError`） | `stopped`・`precondition`・「`branch_strategy.production_branch` を書くか `--production` を渡す」 | 同上 |
| `production_branch` | セクションを読めない・合わない | 上の `load` の行 | `BranchStrategyError` | 止める（`DeclarationError`） | `stopped`・`precondition` | 同上 |
| `flow_of` | 同上 | 同上 | `BranchStrategyError` | 止める（`DeclarationError`） | `stopped`・`precondition` | 同上 |
| `review_problems` | 同上 | 同上 | `BranchStrategyError` | 止める（`DeclarationError`） | `pr-check` が `stopped`・`precondition` | 設定を直すと次のイベントで再実行される |
| `optional_verify` | 同上 | 同上 | `BranchStrategyError` | 止める（`DeclarationError`） | `verify-ref` が `stopped`・`precondition` | 検証ブランチを動かしていない |
| `version_store_of` | 同上 | 同上 | `BranchStrategyError` | 止める（`DeclarationError`） | `bump`・`pr-check`・`publish` が `stopped`・`precondition` | 何もしていない（I18） |
| `of` | 保存先の値を読めない | 上の行 | `DeclarationError` | 呼び出し元へ返す（`VersionStoreError` に包む） | `stopped`・`precondition` | 同上 |
| `load`（worktree） | セクションを読めない・合わない | 上の `load` の行 | `BranchStrategyError` | 止める（worktree の `DeclarationError` に包む） | `ndf worktree` が `stopped`・`precondition`・スキーマの誤りの位置 | worktree を作っていない |
| `default_base` | セクションを読めない・合わない | 同上 | `BranchStrategyError` | 止める（`RouteError` に包む） | `ndf route` が `stopped`（84e6c33b4 の `RouteError` の扱い）・理由にスキーマの誤りの位置 | プランを書いていない |
| `_sprint_range` | セクションを読めない・合わない | 同上 | `BranchStrategyError`（`ContractError` の子） | 呼び出し元へ返す | 工程の進行が `stopped`・`invalid`（84e6c33b4 の宣言の読み誤りと同じ扱い） | 進行表を進めていない |
| `facts_of` の語 `prod_branch` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `_off_production` | 同上 | 同上 | `ContractError` | 既定値で続ける（偽） | 副命令を起動し、副命令が `precondition` で止まる | 無し |
| `load`（project） | セクションがスキーマに合わない | 上の `load` の行 | `problems` | 止める（`JsonFileError`） | `ndf project check` が `broken` | 解析を書いていない |
| `answer_problem` | スキーマを読めない | ndf-next のパッケージのファイルの欠け | `contracts.ContractError` | 呼び出し元へ返す | `stopped`・`invalid` | ndf-next を入れ直す |
| `declared_flow` | セクションが合わない | 手で書いた値の誤り | `answer_problem` | 止める（`ValueError`） | `ndf project drift` が `stopped`・`invalid`・理由 | 何も書いていない |
| `_strategy_part` | 書く前のセクションが合わない | 手で書いた値の誤り | `branch_strategy.problems` | 止める（`WriteError`） | `ndf project write` が `stopped`・`invalid`・スキーマの誤りの位置 | 何も書いていない。セクションを直して再実行する |
| `_strategy_part` | 書き出すセクションが合わない | 解析の回答の誤り | `branch_strategy.problems` | 止める（`WriteError`） | 同上・「書き出すセクションが形に合わない（解析器の誤り）」 | 同上 |
| `default_flow` | スキーマを読めない | ndf-next のパッケージのファイルの欠け | `contracts.ContractError` | 呼び出し元へ返す | `stopped`・`invalid` | 同上 |
| `review_allowed` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `_start_from_table` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `drift` | 同上 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `acquire` | ロックのブランチの更新が断られた | ほかの `land` が先に進めた（fast-forward でない） | `update_ref`・`create_ref` の偽 | 既定値で続ける（None。`wait_lock` が読み直す） | 無し | 無し |
| `acquire` | ロックのブランチの HEAD が NDF の形でない | 人がロックのブランチへ push した | `parse_lock` の None | 既定値で続ける（取れない扱い。`wait_lock` の待ちが切れて止まる） | `wait_lock` の行 | ロックのブランチを人が直す（NDF は強制 push も削除もしない） |
| `wait_lock` | `budget` の秒を過ぎても取れない | ほかの PR の `land` がロックを持つ | 時刻 | 止める（`SerialStop`、原因 `ci_pending`） | `stopped`・`ci_pending`・`items` に `{"kind": "lock", "name": <持ち主の PR>}`、次の手 `rerun` | 何も変えていない。再実行する |
| `extend`・`release` | 更新が断られた | 期限が切れてほかの持ち主が取った | `update_ref` の偽 | `extend` は止める（`SerialStop`、`ci_pending`）。`release` は既定値で続ける（返すものが無い） | `extend` は `stopped`・`ci_pending`・理由「ロックを失った」 | 一時ブランチは `finally` で消す。再実行する |
| `release` | GitHub へ届かない | ネットワーク | `GitHubError` | 既定値で続ける（理由の文字列を返し、`land` が `detail` に足す） | 結果は変えず `detail` に「ロックを返せなかった（期限 <UTC> で切れる）」 | 期限が切れると次の取り手が上書きする |
| `sweep`・`delete_ref` | 一時ブランチを消せない | ネットワーク・権限 | `GitHubError` | 既定値で続ける（消せなかった名前を `detail` に足す） | `detail` だけ | 次にロックを取った `land` が消す |
| `build` | 一時ブランチへのマージが衝突した | M と A の組み合わせが衝突する | `merge_branches` の `conflict` | 止める（`SerialStop`、原因 `combination_failed`） | `stopped`・`combination_failed`・`items` に `{"kind": "conflict", "name": <一時ブランチ>}`、次の手 `reject` だけ | 一時ブランチを消し、ロックを返す。直しを入れた head で承認を取り直す |
| `build` | 一時ブランチを作れない | 同じ名前が残っている | `create_ref` の偽 | 止める（`SerialStop`、原因 `unknown`） | `stopped`・`unknown` | `sweep` の後に起きるのは、`sweep` が消せなかったときだけ。再実行する |
| `observed_names` | PR の head でチェックが 1 つも走っていない | workflow が無い・PR のイベントで起動しない | 空の並び | 止める（原因 `precondition`） | `stopped`・`precondition`・「確かめるチェックが無い」 | 何も変えていない |
| `_watch_names` | 一時ブランチのチェックが落ちた | M と A の組み合わせでテストが落ちる | `ci.WatchResult.cause` | 止める（原因 `combination_failed`、落ちたチェックを `failed` に載せる） | `stopped`・`combination_failed`・`items` に落ちたチェック、次の手 `reject` だけ | 一時ブランチを消し、ロックを返す |
| `_watch_names` | 一時ブランチのチェックが `ci_budget` の秒で終わらない | CI の混雑・workflow が `ndf-serial/**` で起動しない | `ci_pending` | 止める（原因 `ci_pending`） | `stopped`・`ci_pending`、次の手 `rerun` と `reject` | 同上。起動しないときは `production-merge.md` の手順で workflow を直す |
| `_serial_round` | 作り直しが `REBUILD_LIMIT` 回を超えた | ロックの外で本番ブランチが続けて動いた | 回数 | 止める（原因 `ci_pending`） | `stopped`・`ci_pending`・理由に作り直しの回数 | 同上 |
| `_serial_round` | マージの直前にロックが自分のものでない・期限切れ | 待ちが期限を越えた | `still_mine` の偽 | 止める（原因 `ci_pending`。マージしない） | `stopped`・`ci_pending` | 同上 |
| `_serial_round` | PR のマージを GitHub が断った | head が動いた・キューの規則がある | `MergeOutcome.kind` | 止める（84e6c33b4 の `merge` の止まり方のまま） | `stopped`・GitHub の断りの種類 | 同上 |
| `untested_parent` | 第 1 親が M でない | 読み直しとマージの間に人が別の PR をマージした | 戻り値の SHA | 止める（原因 `untested_merge`。本番はマージ済み） | `stopped`・`untested_merge`・`items` に `{"kind": "serial", "name": "untested", "detail": <M と第 1 親>}`、次の手 `rerun` だけ | 再開した `land` が `_resume_merged` で push の CI を待つ |
| `_resume_merged` | push の CI が落ちた | 確かめていない組み合わせが壊れていた | `_watch_names` | 止める（`test_failed` か `check_failed`） | `stopped`・落ちたチェック | 本番はマージ済み。直しは次のスプリントの PR で入れる（I4） |
| `_method_of` | リポジトリの事実を読めない | ネットワーク・権限 | `GitHubError` | 止める（原因 `precondition`） | `stopped`・`precondition`・「リポジトリの公開範囲を読めない」 | 何も変えていない |
| `_method_of` | 宣言の方式が使える方式の一覧に無い | private の Free で `strict` を書いた など | `decide` の問題 | 止める（原因 `precondition`） | `stopped`・`precondition`・`items` に `{"kind": "method", "name": <設定の値>, "detail": <使える方式の一覧>}` | 設定を直して再実行する |
| `delete_ref` | `ndf-serial/` で始まらない名前 | 呼び手の誤り | 名前の検査 | 止める（`ValueError`） | 呼び手の誤り（テストが落とす） | 何も消していない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `branch_strategy.load` | ファイルが無い | `declared` 偽・既定の 4 項目・ほかは None か空 |
| `branch_strategy.load` | ファイルがあり、`branch_strategy` が無い（`{"version": 1}`） | 同上で `declared` 真 |
| `branch_strategy.load` | ファイルがあり、`problems` が空 / 空でない | 既定を埋めた値 / `BranchStrategyError` |
| `branch_strategy.allowed` | 戦略が `trunk`・キー `start` / `reviews` | `("production",)` / `("impl", "sprint", "design", "spec")`（確かめ 8 のスキーマの案の値） |
| `release.declaration.production_admin` | セクションが読める・`admin` 真 / 偽か無い / 読めない | 真 / 偽 / 偽 |
| `release.declaration.production_branch` | セクションに書いてある / 無く既定ブランチがある / どちらも無い | その値 / 既定ブランチ / `DeclarationError` |
| `release.declaration.review_problems` | 戦略が `TRUNK` でない / `TRUNK` で `impl`・`sprint` の両方を書き、どちらも cross-review しない / ほか | 空 / `reviews` の `cross-review` の 1 行 / 空 |
| `worktree.declaration.load` | 共有設定が無い / ある・セクションのファイルが無い / ある・セクションが合わない | None / `strategy` が既定の値 / `DeclarationError` |
| `route.default_base` | セクションに `base_branch` がある / 無く origin の既定ブランチがある / どちらも無い / セクションが合わない | その値 / 既定ブランチ / None / `RouteError` |
| `project.write._strategy_part` | セクションのファイルが無い / あり、キーが無い / あり、前の解析の値のまま / あり、手で書いた値 | ファイルを作る / キーを足す / 新しい値で置き換える / 変えずに `kept` か判断の行（84e6c33b4 の `_owned_decision`） |
| `serial.is_free` | `返した` / 期限が過ぎた / 期限の内 / NDF の形でない | 真 / 真 / 偽 / 偽 |
| `merge_method.usable` | Organization の public / 個人の public / private・`plan` / private・`read`・キューあり / private・`read`・キューなし / `unknown` | 3 つ / `strict`・`serial` / `serial` / 3 つ / `strict`・`serial` / None |
| `merge_method.decide` | 一覧が None・宣言 `serial` / 一覧が None・ほかの宣言 / 宣言が一覧に無い / 一覧にある | `serial` / 「規則を読めない」で止まる / 一覧を載せて止まる / 84e6c33b4 の ruleset との一致の確認（`serial` はキューがあれば止まる） |
| `land._serial_round` | 一時ブランチのチェックが通り、HEAD が M のまま、ロックが自分のもの / HEAD が動いた / ロックを失った / チェックが落ちた・衝突した | マージして第 1 親を確かめる / 一時ブランチを消して作り直す / `ci_pending` / `combination_failed` |
| `land._land_serial` | 作り直しが 1〜3 回目 / 4 回目に入る | `_serial_round` を回す / `ci_pending` |
| `land._resume_merged`（`serial`） | 承認の経路 `gate` / `github-merge` | push の CI を待つ / 待たずに 84e6c33b4 の再開 |

- 繰り返しの終わる条件: セクションの読み取りは繰り返しを持たない（`load` は 1 回の呼び出しでファイルを 1 回読む）。`serial.wait_lock` は時刻が `budget` を過ぎたら、`land._land_serial` は作り直しが `REBUILD_LIMIT`（3）回を超えたら終わる。`serial.sweep` は `matching_refs` の名前の数だけ回る
- 計算量の注意: 1 回のサブコマンドで `load` が複数回呼ばれる（`flow_of`・`merge_method_of`・`production_branch` など）。ファイルは小さく（設定の例で 30 行以下）、
  呼び出しごとに読み直す。スキーマの読み込みはモジュールの中で 1 回に抑える（`functools.cache`）。セクションの値をサブコマンドの間で持ち越さないのは、
  1 回のサブコマンドの中でメインディレクトリのファイルが変わる経路が無く、持ち越す仕組みの方が大きいためである

## 内部のデータと定数

| 名前 | 種類 | ファイル | 既定値 |
| --- | --- | --- | --- |
| `FILE_ROW` | 定数 | `core/branch_strategy.py` | `"branch-strategy"`（保存先の表の行の id。パスは表から引く） |
| `SECTION` | 定数 | `core/branch_strategy.py` | `"branch_strategy"`（保存先の表の行の `keys` の唯一の値と同じ。契約のテストが一致を確かめる） |
| `SCHEMA` | 定数 | `core/branch_strategy.py` | `contracts.schema_path("ndf-settings/branch-strategy")` |
| `BranchStrategy` | 型（`declared: bool`・`base_branch: str \| None`・`production_branch: str \| None`・`release_strategy: str`・`start: str`・`doc_prs: str`・`integrate: str`・`reviews: Mapping[str, Any]`・`merge_method: str \| None`・`admin: bool`・`verify: Mapping[str, Any] \| None`・`version_store: Mapping[str, Any] \| None`・`raw: Mapping[str, Any]`） | `core/branch_strategy.py` | `raw` は書いたままのセクション（解析の書き出しとずれの比べが使う） |
| `BranchStrategyError` | 型（`contracts.ContractError` の子。`path: Path`・`reason: str`） | `core/branch_strategy.py` | — |
| 保存先の表の行 `branch-strategy` | 設定値 | `contracts/domains/ndf-files.json` | `{"id": "branch-strategy", "place": "ndf", "path": "branch-strategy.json", "kind": "declaration", "tracked": true, "writer": "人と解析（M7）", "ends": "kept", "keys": ["branch_strategy"], "main_keys": ["branch_strategy"]}` |
| 行 `worktree`・`project`・`release` の `keys` | 設定値 | 同上 | `worktree` から `base_branch`・`production_branch`・`branch_flow`、`project` から `production_merge`、`release` から `version_store` を外す。`worktree` と `project` の `main_keys` は空になるため列を省く |
| ブランチ戦略の一覧の列 | 設定値 | `contracts/domains/branch-flows.json` | `release_strategy`・`default`・`keeps_sprint`・`release_from`・`release_units` の 5 列（決定 28） |
| `TRUNK` | 定数 | `domains/release/declaration.py` | `facts.ids("branch-flows", release_strategy="trunk")[0]` |
| `STRATEGY_TAG` | 定数 | `domains/project/write.py` | `"branch-strategy.json"`（指紋のキーの頭。84e6c33b4 の `WORKTREE_TAG`） |
| `Declaration.strategy` | 欄 | `domains/worktree/declaration.py` | `branch_strategy.load` の値 |
| `Inputs.strategy` | 欄 | `domains/project/write.py` | `check.load` の 2 つ目の値 |
| `SERIAL` | 定数 | `domains/release/merge_method.py` | `"serial"`（セクションのスキーマの列挙と同じ値） |
| `PLAN_REFUSAL` | 定数 | `domains/release/merge_method.py` | `("Upgrade to GitHub Pro", "make this repository public")`。規則の読み取りがプランの不足で断られた印（実物の応答で確かめる。設計文書の「未確認のまま残ること」の `serial` の行の (1)） |
| `RepoFacts` | 型（`private: bool`・`organization: bool`・`rules: str`（`read`・`plan`・`unknown`）） | `domains/release/merge_method.py` | — |
| `Method.usable` | 欄（`tuple[str, ...] \| None`） | 同上 | None は行を決めなかった |
| `LOCK_PREFIX`・`TEMP_PREFIX` | 定数 | `domains/release/serial.py` | `"ndf-lock/"`・`"ndf-serial/"` |
| `EMPTY_TREE` | 定数 | 同上 | `"4b825dc642cb6eb9a060e54bf8d69288fbee4904"`（空のツリー。`git hash-object -t tree /dev/null` の値。ロックのコミットのツリー。決定 32） |
| `REBUILD_LIMIT` | 定数 | 同上 | `3`（決定 32。最初に `serial` を使ったリポジトリの記録で見直す） |
| `LOCK_POLL_SECONDS` | 定数 | 同上 | `30.0`（`land.QUEUE_POLL_SECONDS` と同じ間隔） |
| `LockState` | 型（`owner: int`・`approved: str \| None`・`expires: datetime \| None`・`released: bool`） | 同上 | — |
| `LockHold` | 型（`production: str`・`owner: int`・`commit: str`・`expires: datetime`） | 同上 | — |
| `Built` | 型（`base: str`・`ref: str`・`sha: str`） | 同上 | — |
| `SerialStop` | 型（例外。`kind: str`・`detail: str`・`items: list[dict]`） | 同上 | `land` が `Landed` に直す |
| `RepoInfo`・`GitCommit`・`BranchMerge` | 型（`private`・`owner_type` / `message`・`tree`・`parents` / `sha`・`conflict`） | `adapters/github.py` | — |
| `Landed.tested_base`・`Approval.tested_base` | 欄（`str \| None`） | `land.py`・`merge_decide.py` | None（`serial` 以外） |
| `Lander.now` | 欄（`Callable[[], datetime]`） | `land.py` | `lambda: datetime.now(timezone.utc)`（ロックの期限は機械をまたぐため、単調な時計でなく UTC の時刻で書く） |
| 行 `combination_failed`・`untested_merge` | 設定値 | `contracts/causes.json` | ファイル配置の表の行 |

**指紋のキーの頭を変える影響。** 解析の記録（`.ndf/project.json` の `analysis.written`）は `worktree.json#<キー>` の指紋を持つ。
この指紋は、新しいキーの頭 `branch-strategy.json#<キー>` と一致しない。移行（#3613）の後の最初の解析では、セクションの値を手で書いた値として扱い、書き換えない（判断の行を出す）。
値を壊す向きには倒れない。指紋のキーを移すかは移行の改名表の行が決める（#3613。決定 29）。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つのレーンのサブコマンドが同じセクションを読む | `.ndf/branch-strategy.json`（メインディレクトリ） | 排他を取らない。読むだけで、1 回の呼び出しで 1 回読む | 読み直せば同じ値になる |
| `ndf project write` がセクションを書き、別のレーンが読む | 同上 | 84e6c33b4 の解析の書き出しと同じ（書き出しは差分を示してから 1 回で書く。`.ndf/worktree.json` の書き出しと同じ） | 書き途中のファイルを読んだら `load` が JSON の誤りで止まり、再実行すれば読める |
| 2 つのレーン（か手元のレーンと CD）の `land`・`land_bump` が同じ本番ブランチへ `serial` で入れる | 本番ブランチと `ndf-lock/<本番ブランチ>` | ロックのブランチを `force: false` の更新で進めた方だけが持つ（比べて書き換える操作。STD16）。マージの直前に `still_mine` を読む（I19） | ロックを持ったまま終わっても、期限の後に次の取り手が上書きする。再実行した `land` はロックを取り直し、マージ済みなら `_resume_merged` へ進む |
| ロックの外で人が GitHub で本番ブランチへマージする | 本番ブランチ | 縛れない（ブランチ保護が無い） | 確かめの前なら HEAD の読み直しで作り直し、マージの後なら `untested_parent` が `untested_merge` で止める（決定 32 の「守れないこと」） |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `branch_strategy.load` の誤り | ファイルのパスと、スキーマの誤りの位置と理由（先頭 5 行） | 読む側の結果 JSON の `reasons`・`summary` | セクションのほかの値 |
| `records_trail` の迂回の記録 | 設定の保存先 `.ndf/branch-strategy.json` の `branch_strategy.production_merge.admin` | 本番の PR のコメント | token |
| `project.write._strategy_part` | 書いた・残した・判断を求めるキー（`branch-strategy.json#<キー>`）と差分 | 結果 JSON の `items` と差分 | 秘密の形の値（84e6c33b4 の `unified` が伏せる） |
| `serial.acquire`・`extend`・`release` | ロックのコミット（持ち主の PR・承認済みのコミット・期限・`返した`） | `ndf-lock/<本番ブランチ>` のコミットの列（誰がいつロックを持ったかの記録） | token |
| `land._land_serial` | 確かめた M・一時ブランチ・作り直しの回数・消せなかった一時ブランチ・返せなかったロック | 結果 JSON の `detail`・`items`・`approval.tested_base` | 一時ブランチのチェックのログの本文 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存のコンポーネント | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `path_of` | `domains/worktree/declaration.py` の `_file` | 84e6c33b4 の同じファイルを読んだ | 作り方（`places` と `ndf-files` から引く）を使う。`_file` は worktree の内部の関数のため、`core` に同じ 2 行の作り方を置き、`_file` は残す |
| `main_dir` | `domains/release/declaration.py` の `_trusted_project_path`・`domains/project/check.py` の `repo_dirs` | 同上 | `_trusted_project_path` の手順（`common_dir().parent`、git の外は `root`）を移し、元は消す。`repo_dirs` は git の外で止める別の約束のため使わない |
| `load` | `domains/worktree/declaration.py` の `read_object`・`domains/records/jsonfile.read_object` | 同上 | `core` から `domains` を呼べないため使えない。読み方（オブジェクトでなければ誤り）を倣う |
| `problems` | `contracts.schema_errors` | 84e6c33b4 の `domains/project/check.py` が使う形を読んだ | 使う |
| `defaults` | `measure_flow.default_flow`（表の既定の戦略の各列の先頭） | 84e6c33b4 の `measure_flow.py` を読んだ | 使えない（表の列を外す）。スキーマの `default` を読む形に置き換え、`default_flow` はこれを呼ぶ |
| `allowed` | `release.declaration._flow_row`・`measure_flow._rows` | 同上と `declaration.py` を読んだ | 使えない（表の列を引く）。スキーマの `allOf` の `if`/`then` を読む形に置き換え、2 つは消す |
| `default_branch` | `core/route.default_base` の後半・`domains/worktree/repo.default_branch`・`domains/project/measure_repo.default_branch` | 3 つを読んだ | `default_base` の後半（`symbolic-ref`）を移し、`default_base` はこれを呼ぶ。worktree と解析の 2 つは `Git`・`Tree` を受ける別の形で、呼び手が多いため残す |
| `BranchStrategy.as_record` | `domains/worktree/repo.py` の開始時の記録（`branch_flow` に `production_branch` を足す） | 84e6c33b4 の `repo.py:87-89` を読んだ | 記録の形（ブランチ戦略の値と本番ブランチ）を同じキーで返す。`repo.py` は `Declaration.branch_flow` を読むだけになる |
| `version_store_of` | `release.declaration._parse` の `version_store` | 84e6c33b4 の `declaration.py` を読んだ | 中身（オブジェクトでなければ誤り）はスキーマが持つため、値を渡すだけにする |
| `observed_names`・`_watch_names` | `land._watch`・`RequiredChecks.runs`・`ci.read`・`ci.Watch` | 84e6c33b4 の `land.py`・`required_checks.py` を読んだ | `runs` と `ci.read`・`ci.Watch` を使う。`_watch` は本番ブランチの規則から名前を読み、規則を読めない private の Free で使えないため、名前を引数で受ける `_watch_names` を足し、`_watch` は残す |
| `untested_parent` | `GitHub.commit_parents` | 84e6c33b4 の `github.py` を読んだ | 使う |
| `wait_lock` | `land._await_queue` の待ち（`clock`・`sleep`・`QUEUE_POLL_SECONDS`） | 84e6c33b4 の `land.py` を読んだ | 待ちの形（締め切りと間隔）を倣う。キューの読みを持つため、そのものは使えない |
| `create_ref`・`update_ref`・`delete_ref`・`matching_refs`・`ref_head`・`git_commit`・`create_commit`・`merge_branches`・`repo_info` | `GitHub.rest`・`_translating`・`_pages` | 同上 | 要求は `rest` で送り、誤りの直しは `_translating`、一覧は `_pages` を使う。ref と Git のオブジェクトを扱う関数は 84e6c33b4 に無い（`git grep -n 'git/refs\|/merges' origin/sprint/lrel-lock -- plugins/ndf-next/src` の該当 0 件） |
| `usable`・`rules_state` | `merge_method.rules_of`・`_mismatch` | 84e6c33b4 の `merge_method.py` を読んだ | `rules_of` の事実（キューの有無）を入力に使う。プランの判定は持たないため足す |
| ロックの仕組み全体 | merge queue・`strict`・Mergify・bors-ng（決定 32 の比べた候補） | 設計文書の「外部の調査」（LIB4・LIB5・STD9・STD14〜16） | 決定 32 のとおり、origin のブランチのコミットの列を使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `path_of` | `<main>/.ndf/branch-strategy.json` | — | — |
| `main_dir` | worktree から実行するとメインディレクトリ | git の外で `root` | — |
| `load` | ai-plugins の例と Web サービスの例（確かめ 8）を読み、`release_strategy`・`merge_method`・`verify`・`version_store` を返す。スプリントの worktree のファイルを書き換えても、メインディレクトリの値を返す | ファイルが無ければ既定の 4 項目。`{"version": 1}` で同じ | 確かめ 8 の 13 の壊した例のそれぞれで `BranchStrategyError` と位置。壊れた JSON で `BranchStrategyError` |
| `problems` | 確かめ 8 の 4 つの通る例で空 | `branch_strategy` の無いファイルで空 | `trunk` で `production_merge.method` が無い・`trunk` で `release.version_store` が無い・`merge_base_branch` 戦略で `release.version_store` がある・`method` と `admin: true`・`trunk` の `reviews` に `release` のそれぞれで 1 行以上 |
| `defaults` | 4 つの既定 | — | — |
| `allowed` | `trunk` の `start` は `production` だけ | `release_branch` の `reviews` は `release` を含む | 一覧に無い戦略で空 |
| `default_branch` | `origin/HEAD` の名前 | — | `origin/HEAD` が無ければ None |
| `BranchStrategy.as_record` | 既定を埋めた 5 キーと 2 つのブランチの名前 | ブランチの名前を書いていなければ持たない | — |
| `production_admin` | セクションの `admin: true` で真 | セクションが無ければ偽 | セクションが壊れていても偽（迂回しない） |
| `merge_method_of` | `merge-queue` を返す | 書いていなければ None | セクションがスキーマに合わなければ `DeclarationError` |
| `base_branch` | `develop` を返す | 無ければ `production` | 同上 |
| `production_branch` | セクションの値 | 無ければ既定ブランチ | 既定ブランチも無ければ `DeclarationError` |
| `flow_of` | `trunk` 戦略の id | セクションが無ければ既定の戦略（受け入れ条件 18） | — |
| `review_problems` | `trunk` で `impl` が cross-review すれば空 | 既存のブランチ戦略で空 | `trunk` で `impl`・`sprint` のどちらも cross-review しなければ 1 行 |
| `optional_verify` | `marketplace` と `deploy` の値 | 書いていなければ None | — |
| `version_store_of` | `{"kind": "json", ...}` を返す | 書いていなければ None | — |
| `_parse` | 手順と CHANGELOG を読む | — | — |
| `of` | セクションの `json` で `JsonStore` | 既存のブランチ戦略で保存先なしは `PluginStore` | `trunk` で保存先なしは `VersionStoreError` |
| 解決の全体（`test_release_branch_strategy.py`） | セクションだけを置いたリポジトリで `flow_of`・`merge_method_of`・`production_admin`・`optional_verify`・`version_store_of`・`production_branch`・`base_branch` が値を返す | — | 解決の関数が `.ndf/worktree.json`・`.ndf/project.json`・`.ndf/release.json` を開かない（ファイルを開く呼び出しを記録して見る。受け入れ条件 2m） |
| `load`（worktree） | セクションの値を `strategy` に載せる | 共有設定が無ければ None | セクションが合わなければ `DeclarationError` |
| `Declaration.branch_flow` | `as_record` の値 | — | — |
| `Declaration.base_branch` | セクションの値 | 無ければ None | — |
| `Declaration.production_branch` | セクションの値 | 無ければ None | — |
| `Declaration.start_channel` | `trunk` で `production` | 書いていなければ `base` | — |
| `default_base` | セクションの `base_branch` | 無ければ origin の既定ブランチ | セクションが合わなければ `RouteError`。スプリントの worktree のファイルの値を読まない |
| `_sprint_range` | セクションの `base_branch` からの範囲 | — | 無ければ `FactUnknown` |
| `facts_of` の語 `prod_branch` | セクションに書けば真 | 書かなければ偽 | — |
| `_off_production` | マージ先が本番ブランチと違えば真 | マージ先が分からなければ偽 | セクションが読めなければ偽 |
| `load`（project） | セクションを返す | 無ければ None | 合わなければ `JsonFileError` |
| `declared_branches` | 2 つの名前 | セクションが無ければ空 | — |
| `_flow_branches` | セクションの 2 つの名前 | 片方が無ければ既定ブランチ | — |
| `default_flow` | スキーマの既定 | — | — |
| `review_allowed` | `impl` の `checks` と `diff` | — | — |
| `answer_problem` | 合う回答で None。`trunk` の 4 項目だけ（`production_merge`・`release` の無い）回答で None | — | `trunk` で `start: base` の回答に理由。4 項目と `reviews` の外のキーを持つ回答に理由 |
| `_start_from_table` | `trunk` で分岐元が `production` | 分岐元が測れていれば変えない | — |
| `declared_flow` | セクションを既定で埋める | 書いていなければ「書いていない」 | 合わないセクションで `ValueError` |
| `drift` | ずれの行 | ずれが無ければ空 | — |
| `_strategy_part` | セクションのファイルが無いリポジトリで `{"version": 1, "branch_strategy": ...}` を作る | 前の解析の値のままのキーを置き換える | 手で書いた値を変えない。合わないセクションで `WriteError`。`.ndf/worktree.json` を書かない |
| `merge_worktree` | 欠けた 2 キーを足す | 指紋のキーが `branch-strategy.json#base_branch` | — |
| `merge_flow` | 4 項目とレビューの行をセクションの直下へ書く | 前の解析の値のままなら置き換える | 手で書いた値を変えない |
| `lock_ref`・`temp_ref` | `ndf-lock/main`・`ndf-serial/main/12-1` | — | — |
| `lock_message`・`parse_lock` | 作ったメッセージを読み戻すと同じ持ち主・コミット・期限 | `返した` を読む | NDF の形でないメッセージで None |
| `is_free` | `返した` で真 | 期限ちょうどで真 | 期限の内で偽。None で偽 |
| `acquire` | ブランチが無いとき作って取る。`返した` の上に足して取る。作ったコミットの `tree` が `EMPTY_TREE` | 期限切れの上に足して取る | 期限の内のロックで None。`update_ref` が偽で None。`force: true` を送らない |
| `wait_lock` | 2 回目の読みで取れる | — | `budget` を過ぎて `SerialStop`（`ci_pending`・`lock` の項目） |
| `extend`・`still_mine`・`release` | 自分のロックを延ばす・返す | 期限ちょうどで `still_mine` が偽 | ほかの持ち主へ移っていれば `extend` は `SerialStop`、`release` は足さない |
| `sweep` | `ndf-serial/main/` の下を消す | 残っていなければ空 | `ndf-lock/` と本番ブランチを消さない |
| `build` | M から作り A をマージした `Built` | A が M に含まれていれば `sha` が M | 衝突で `SerialStop`（`combination_failed`） |
| `observed_names` | check run と commit status の名前 | 同じ名前の重なりを 1 つにする | 走っていなければ空 |
| `untested_parent` | 第 1 親が M で None | — | 第 1 親が M でなければその SHA |
| `rules_state` | 失敗なしで `read` | 403 と `PLAN_REFUSAL` の語で `plan` | 403 でほかの本文・500・届かない で `unknown` |
| `usable` | 5 つの行のそれぞれで決定 33 の表の方式 | private・`read`・キューなしで `merge-queue` を含まない | `unknown` で None |
| `decide` | private の Free で `serial` を通す | 一覧が None で `serial` を通す | private の Free で `strict` は止まり、`Method.usable` が `("serial",)`。一覧が None で `strict` は止まる。キューのある本番ブランチで `serial` は止まる |
| `_land_serial` | A・B を承認すると、A が（M+A）の後に `expect_head` = A でマージされ、B は（M+A+B）でマージされる（ロックを返した後）。どちらも承認を取り直さない | 確かめの間に HEAD が動くと作り直す。3 回まで | 4 回目で `ci_pending`。一時ブランチのチェックが落ちて `combination_failed`。期限切れの持ち主がマージしない。第 1 親が M でなく `untested_merge`（`merged` 真）。どの止まり方でも一時ブランチを消し、ロックを返す |
| `_bump_by_method` | `serial` で `_behind` を呼ばずに `_land_serial` へ | — | — |
| `_resume_merged` | `serial`・経路 `gate` で push の CI を待って `ok` | 経路 `github-merge` で待たない | push の CI が落ちて `test_failed` |
| `_merge_result` | `serial` のマージで `approval.tested_base` を出す | `untested_merge` で次の手が `rerun` だけ | `combination_failed` で次の手が `reject` だけ |
| `create_ref`・`update_ref`・`delete_ref`・`merge_branches` | 201・200・204・201 で成功の値 | 409・422 で偽、204 で `sha` None | `delete_ref` に `ndf-lock/main` で `ValueError`。`update_ref` の要求の本文の `force` が偽 |
