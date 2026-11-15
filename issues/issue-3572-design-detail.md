# #3572 の詳細設計: ブランチ戦略の設定を `.ndf/branch-strategy.json` のセクション `branch_strategy` の 1 か所から読む

設計文書 [issue-3572-design.md](issue-3572-design.md) の「構成要素」のうち、決定 24 の 12・13 の行を、ファイルと関数の粒度へ下ろした文書である。
2 つの行は、設定の解決をセクションから読む形へ直すことと、ブランチ戦略の一覧から戦略が許す値の列を外すことである。語（ブランチ戦略のセクション・ブランチ戦略の一覧・マージの方式・
検証環境・バージョンの保存先・本番ブランチ・ベースブランチ）は設計文書の「用語」と「ブランチ戦略のセクション」の意味で使う。不変条件（I1〜I18）と
決定（決定 1〜31）の番号は設計文書と [決定の記録](issue-3572-design-decisions.md) の番号である。

**基準のコード。** 実装の PR https://github.com/devbasex/ai-plugins/pull/3587 の HEAD 84e6c33b4（`origin/sprint/lrel-lock`。2026-11-16 の
PR https://github.com/devbasex/ai-plugins/pull/3636 のマージ）である。決定 24 の 1〜10 の行は 84e6c33b4 にあり、この文書では関数を書かない。
1〜10 の行は、戦略ごとの振る舞い・マージの方式・merge queue・バージョンの保存先・検証環境の種類・`merge_group` である。「変更」「削除」は 84e6c33b4 の時点の
コードに対する差分である。

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
| `domains/release/land.py`・`bump.py`・`confirm.py`・`tagging.py`・`verify_ref.py`・`assemble.py`・`cli.py` の関数 | 戦略・方式・迂回の設定・検証環境・バージョンの保存先を `declaration.py` と `version_store.of` から受ける。変わるのは止まる理由の文言の設定ファイルの名前だけ |
| `domains/release/merge_method.py` の `rules_of`・`decide` | 入力は `declaration.merge_method_of` の値で、ファイルを読まない。モジュールの説明と止まる理由の文言の設定ファイルの名前だけが変わる |
| `domains/worktree/` の `create.py`・`repo.py`・`merged.py`・`merged_decide.py`・`follow.py`・`cli.py` | `worktree.declaration.Declaration` の `branch_flow`・`base_branch`・`production_branch`・`start_channel` を読む。値の出所がセクションに変わるだけで、キーの名前（`release_strategy`・`production_branch`）は同じ |
| `domains/records/mvv.py` と呼び手 `domains/records/cli.py` | ベースブランチを引数 `--base`（必須。`records/cli.py` の `signals.add_argument("--base", required=True, ...)`）で受け、設定ファイルを読まない |
| `domains/hooks/strict.py` | `.ndf/project.json` の `delivery` のうち `channel` が `production` の行だけを読む（`strict.py:367`）。検証環境の行を外しても判定は変わらない |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/
│   ├── domains/{branch-flows,ndf-files}.json
│   └── schemas/
│       ├── ndf-settings/{branch-strategy *,worktree,project,release}.schema.json
│       └── io/{project.result,worktree.args}.schema.json
├── docs/contracts/{commands,ndf-settings,files}.md
├── skills/
│   ├── development-workflow/{references/*.md,schemas/project.schema.json}
│   ├── merged/SKILL.md
│   ├── release/{SKILL.md,references/release-steps.md}
│   └── worktree/{SKILL.md,references/ndf-settings.md,schemas/worktree.schema.json}
├── src/ndf/
│   ├── core/
│   │   ├── branch_strategy.py *
│   │   └── route/__init__.py
│   ├── domains/
│   │   ├── project/{check,cli,measure_flow,write}.py
│   │   ├── release/{declaration,land,merge_method,records_trail,version_store}.py
│   │   └── worktree/declaration.py
│   └── progress/{command.py,gate/release.py}
└── tests/
    ├── core/test_branch_strategy.py *
    └── （フィクスチャを直すテスト。下の表）
```

**依存の向き。** 一方向で、循環は無い。新設の `core/branch_strategy.py` が依存するのは 2 つだけである。

- `ndf.contracts`: スキーマと事実の表を読む
- `ndf.adapters.git`: メインディレクトリと既定ブランチを読む

セクションを読む 5 つの領域は `core/branch_strategy` を呼び、互いを呼ばない。5 つは、リリース `domains/release`・スプリントの経路 `domains/worktree`・
マージ先の既定 `core/route`・リポジトリの解析 `domains/project`・工程の記録 `progress` である。
`core` から `domains` への import は 84e6c33b4 で 0 件で、この変更でも 0 件のままにする。数えたコマンドは次のとおりである。

```bash
git grep -l 'from ndf.domains' origin/sprint/lrel-lock -- plugins/ndf-next/src/ndf/core
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/src/ndf/core/branch_strategy.py` | 新設 | `.ndf/branch-strategy.json` をメインディレクトリから読み、セクションのスキーマで確かめ、既定を埋めた値を返す。セクションを読むどの領域もこれだけを通す | `ndf.contracts`・`ndf.contracts.facts`・`ndf.adapters.git` |
| `plugins/ndf-next/contracts/schemas/ndf-settings/branch-strategy.schema.json` | 新設 | セクションのキーと組み合わせの検査（戦略が許す値・`trunk` 戦略で必須のキー・読まれない値）を持つ。中身は確かめ 8 の案（決定 28） | 無し |
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
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | 止まる理由の文言の設定の保存先をセクションのキーへ直す（関数は変えない） | 無し（文言だけ） |
| `plugins/ndf-next/src/ndf/domains/release/merge_method.py` | 変更 | 同上 | 無し（文言だけ） |
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
| `plugins/ndf-next/skills/development-workflow/references/production-merge.md` | 変更 | マージの方式と迂回の設定の保存先をセクションのキーへ直す | 無し |
| `plugins/ndf-next/skills/development-workflow/references/project-analysis.md` | 変更 | 解析が書くファイルにセクションのファイルを足し、`.ndf/worktree.json` のブランチの項目を外す | 無し |
| `plugins/ndf-next/skills/development-workflow/references/parallel.md` | 変更 | ブランチ戦略の設定の保存先をセクションへ直す | 無し |
| `plugins/ndf-next/skills/development-workflow/references/parallel-work.md` | 変更 | 同上 | 無し |
| `plugins/ndf-next/skills/development-workflow/references/glossary.md` | 変更 | 同上（用語の説明の保存先） | 無し |
| `plugins/ndf-next/skills/merged/SKILL.md` | 変更 | 同上（マージ後のクリーンアップが読む行） | 無し |
| `plugins/ndf-next/skills/release/SKILL.md` | 変更 | マージの方式・検証環境・バージョンの保存先の設定の保存先をセクションへ直す | 無し |
| `plugins/ndf-next/skills/release/references/release-steps.md` | 変更 | 同上 | 無し |
| `plugins/ndf-next/tests/core/test_branch_strategy.py` | 新設 | セクションの読み取り・既定・スキーマの組み合わせの検査（確かめ 8 の例）を縛る | `ndf.core.branch_strategy` |
| `plugins/ndf-next/tests/domains/release/test_release_branch_strategy.py` | 新設 | リリースの解決がセクションだけを読み、ほかの 3 つの設定ファイルを開かないことを縛る | `declaration`・`version_store` |

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

次の 4 つは「関数の仕様」には書かない。設定ファイルの名前を書いた文字列だけが変わり、責務と失敗の扱いを変えないためである。
`verify_of`・`optional_verify_ref`・`records_trail` の本文の関数・`land.py` と `merge_method.py` の文言である。

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
| `answer_problem` | `domains/project/measure_flow.py` | 変更 | `answer_problem(value: Any) -> Optional[str]` | ブランチ運用の回答がセクションのスキーマに合わなければ理由を返す | 無し | `branch_strategy.problems({"version": 1, "branch_strategy": value})` の先頭 3 行。戦略が許す値と書ける PR の種類の破れもここに出る | 契約のスキーマの読み込み |
| `_start_from_table` | `domains/project/measure_flow.py` | 変更 | `_start_from_table(starts: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]` | 動いているスプリントブランチが少なければ、戦略が許す分岐元を出す | 無し | 許す分岐元は `branch_strategy.allowed(<行>, "start")`。証跡のコマンドはセクションのスキーマを指す | 契約のスキーマの読み込み |
| `declared_flow` | `domains/project/measure_flow.py` | 変更 | `declared_flow(section: Optional[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], bool]` | セクションを既定で埋めた 4 項目・書いたレビューの行・書いたかを返す | 無し | 4 項目のキーは `release_strategy`・`start`・`integrate`・`doc_prs`。セクションが合わなければ `ValueError` | 契約のスキーマの読み込み |
| `drift` | `domains/project/measure_flow.py` | 変更 | `drift(section: Optional[dict[str, Any]], item: Optional[dict[str, Any]]) -> list[dict[str, Any]]` | セクションと測った値のずれを返す | 無し | 84e6c33b4 の判定のまま。比べる宣言の値がセクション | 契約のスキーマの読み込み |
| `_strategy_part` | `domains/project/write.py` | 変更 | `_strategy_part(inp: Inputs, written_before: dict[str, Any]) -> tuple[list[File], list[dict[str, Any]], dict[str, str], tuple[str, ...], dict[str, Any]]` | セクションのベースブランチ・本番ブランチ・ブランチ運用を書く（84e6c33b4 の `_worktree_part`） | 無し | 書き先はセクションのファイル。ファイルが無ければ `{"version": 1, "branch_strategy": {...}}` で作る（解析で新しく作るのは C7 の対象外）。書くのは、キーの無い項目と前の解析の値のまま（指紋が一致する）の項目だけ（設計文書の「集約」の表）。書く前の中身が `branch_strategy.problems` で合わなければ書かない | ファイルの読み込み |
| `merge_worktree` | `domains/project/write.py` | 変更 | `merge_worktree(existing: dict[str, Any], branches: dict[str, Any], origin: str, written_before: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]` | セクションに欠けた `base_branch`・`production_branch` を足す | `existing` はセクション（オブジェクト） | 84e6c33b4 の規則のまま。指紋のキーは `branch-strategy.json#<キー>` | 無し |
| `merge_flow` | `domains/project/write.py` | 変更 | `merge_flow(doc: dict[str, Any], value: Any, origin: str, written_before: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]` | ブランチ運用の 4 項目とレビューの行をセクションの直下へ書く | `doc` はセクション | 84e6c33b4 は `branch_flow` の 1 キーへまとめて書いた。セクションでは各キーを直下に置き、指紋は `branch-strategy.json#branch_flow` の 1 つで 5 キーの組をまとめて持つ | 無し |

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

- 繰り返しの終わる条件: 繰り返しを持たない。`load` は 1 回の呼び出しでファイルを 1 回読む
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

**指紋のキーの頭を変える影響。** 解析の記録（`.ndf/project.json` の `analysis.written`）は `worktree.json#<キー>` の指紋を持つ。
この指紋は、新しいキーの頭 `branch-strategy.json#<キー>` と一致しない。移行（#3613）の後の最初の解析では、セクションの値を手で書いた値として扱い、書き換えない（判断の行を出す）。
値を壊す向きには倒れない。指紋のキーを移すかは移行の改名表の行が決める（#3613。決定 29）。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つのレーンのサブコマンドが同じセクションを読む | `.ndf/branch-strategy.json`（メインディレクトリ） | 排他を取らない。読むだけで、1 回の呼び出しで 1 回読む | 読み直せば同じ値になる |
| `ndf project write` がセクションを書き、別のレーンが読む | 同上 | 84e6c33b4 の解析の書き出しと同じ（書き出しは差分を示してから 1 回で書く。`.ndf/worktree.json` の書き出しと同じ） | 書き途中のファイルを読んだら `load` が JSON の誤りで止まり、再実行すれば読める |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `branch_strategy.load` の誤り | ファイルのパスと、スキーマの誤りの位置と理由（先頭 5 行） | 読む側の結果 JSON の `reasons`・`summary` | セクションのほかの値 |
| `records_trail` の迂回の記録 | 設定の保存先 `.ndf/branch-strategy.json` の `branch_strategy.production_merge.admin` | 本番の PR のコメント | token |
| `project.write._strategy_part` | 書いた・残した・判断を求めるキー（`branch-strategy.json#<キー>`）と差分 | 結果 JSON の `items` と差分 | 秘密の形の値（84e6c33b4 の `unified` が伏せる） |

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
| `answer_problem` | 合う回答で None | — | `trunk` で `start: base` の回答に理由 |
| `_start_from_table` | `trunk` で分岐元が `production` | 分岐元が測れていれば変えない | — |
| `declared_flow` | セクションを既定で埋める | 書いていなければ「書いていない」 | 合わないセクションで `ValueError` |
| `drift` | ずれの行 | ずれが無ければ空 | — |
| `_strategy_part` | セクションのファイルが無いリポジトリで `{"version": 1, "branch_strategy": ...}` を作る | 前の解析の値のままのキーを置き換える | 手で書いた値を変えない。合わないセクションで `WriteError`。`.ndf/worktree.json` を書かない |
| `merge_worktree` | 欠けた 2 キーを足す | 指紋のキーが `branch-strategy.json#base_branch` | — |
| `merge_flow` | 4 項目とレビューの行をセクションの直下へ書く | 前の解析の値のままなら置き換える | 手で書いた値を変えない |
