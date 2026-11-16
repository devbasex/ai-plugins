# #3672 の詳細設計: ブランチ戦略の宣言を 1 つの節で表し、10 系と 11.0 をその節だけで動かす

設計文書 [issue-3672-design.md](issue-3672-design.md) の「構成要素」と「データ構造」「処理の流れ」を、ファイルと関数の粒度へ下ろした文書である。
本文の番号は次の文書を指す。

| 番号 | 指す文書 |
| --- | --- |
| 不変条件（`I<数>`）・組み合わせの規則（`R<数>`）・ドメインイベント（`E<数>`） | 設計文書 |
| 決定の番号 | [issue-3672-design-decisions.md](issue-3672-design-decisions.md) |
| 削除の一覧（`D<数>`） | [issue-3672-design-removal.md](issue-3672-design-removal.md) |

**10 系と 11.0 は同じ名前・同じ責務の関数を 1 つずつ持つ。** 表では 1 行に両方の置き場を並べ、シグネチャは 10 系の形で書く（11.0 は同じ引数を `Path`・`Optional` の型注釈で持つ）。
2 つの系の結果の形は、設計文書の「入出力の契約」のスキーマ 1 つで縛る。

用語の対応（本文で使う語と、コードの名前）:

| 本文の語 | コードの名前 |
| --- | --- |
| 節 | `.ndf/project.json` の `branch_strategy` |
| 読む部品 | 10 系 `lib/branch_strategy.py`・11.0 `core/branch_strategy.py` |
| 旧い宣言の検査 | 10 系 `lib/branch_strategy_legacy.py`・11.0 `core/branch_strategy_legacy.py` |
| 解析のサブコマンド | 10 系 `project-decl.py branch-strategy`・11.0 `ndf project branch-strategy` |
| 既定の表 | `branch-strategy-presets.json` |
| 移す行の表 | `ndf-settings-renames.json` |
| 作成の結果 | `branch-strategy.result.schema.json` の形の 1 行の JSON |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除）:

```text
plugins/ndf-next/
├── contracts/
│   ├── schemas/declarations/project.schema.json      # $defs/BranchStrategy の正本
│   ├── schemas/declarations/{worktree,supervise,release}.schema.json
│   ├── schemas/io/branch-strategy.result.schema.json  *
│   └── domains/
│       ├── branch-strategy-presets.json               *
│       ├── ndf-settings-renames.json                  *（#3613 が先なら変更）
│       └── branch-flows.json
└── src/ndf/
    ├── adapters/github.py
    ├── core/branch_strategy.py                        *
    ├── core/branch_strategy_legacy.py                 *
    └── domains/
        ├── project/{cli.py, strategy_measure.py*, strategy_write.py*, strategy_migrate.py*}
        ├── worktree/declaration.py
        └── release/{declaration.py, land.py}
plugins/ndf/
├── pyproject.toml / uv.lock                           # jsonschema のグループ
├── skills/{development-workflow,worktree,release}/schemas/*.schema.json
└── scripts/
    ├── data/{branch-strategy-presets.json*, ndf-settings-renames.json*}
    ├── lib/{branch_strategy.py*, branch_strategy_legacy.py*, branch_flow.py-, deps.py, gh_graphql.py, release_case.py}
    ├── project-decl.py
    ├── project_lib/{branch_strategy_measure.py*, branch_strategy_write.py*, branch_strategy_migrate.py*, measure_branch_flow.py-}
    ├── release_lib/{strategy_route.py*, land.py*, approval_trail.py, promotion.py-}
    ├── supervise_lib/{decl.py, sprint_waves.py}
    ├── release-steps.py
    └── worktree-setup.sh
scripts/
├── check-legacy-branch-decl.py                        *
├── legacy-branch-decl.json                            *
├── check-skill-repo-assumptions.py
├── check-ndf-contracts.py
└── check-pr-base.sh
```

依存の向きは一方向である。契約（スキーマ・既定の表・移す行の表）は何も読まない。旧い宣言の検査は移す行の表だけを読み、読む部品は旧い宣言の検査と
スキーマを読む。解析のサブコマンドは読む部品と既定の表を読み、工程の部品（worktree・スプリント・リリース）は読む部品だけを読む。読む部品は解析も工程の部品も読まないため、循環は無い。
CI の検査は契約とコードを読むだけで、工程の部品から呼ばれない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/contracts/schemas/declarations/project.schema.json` | 変更 | `$defs/BranchStrategy`（キー・値・R1〜R10）の正本を持ち、`production_merge`・`delivery` の定義を消す | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/worktree.schema.json` | 変更 | `base_branch`・`production_branch`・`branch_flow` の定義を消す（D5） | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/supervise.schema.json` | 変更 | `release` の定義を消す（D7） | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/release.schema.json` | 変更 | `version_files` の定義を消す（D7） | 無し |
| `plugins/ndf-next/contracts/schemas/io/branch-strategy.result.schema.json` | 新設 | 作成の結果の形 | 無し |
| `plugins/ndf-next/contracts/domains/branch-strategy-presets.json` | 新設 | 代表のリポジトリの種類 5 つの節の値と、根拠の閾値の正本 | 無し |
| `plugins/ndf-next/contracts/domains/ndf-settings-renames.json` | 新設（#3613 が先なら変更） | 旧いキーの位置 → 節のキーの位置・値の対応・`remove` の行 | 無し |
| `plugins/ndf-next/contracts/domains/branch-flows.json` | 変更 | 列 `promotion` を `release_strategy` へ、行 id を `merge-base-branch`・`release-branch`・`trunk` へ改名する（D8） | 無し |
| `plugins/ndf-next/src/ndf/core/branch_strategy.py` | 新設 | 節を読み、スキーマと旧いキーで検査し、型にして返す | `core/branch_strategy_legacy.py`・`ndf.contracts` |
| `plugins/ndf-next/src/ndf/core/branch_strategy_legacy.py` | 新設 | 移す行の表から旧いキーの位置を探す | `ndf.contracts` |
| `plugins/ndf-next/src/ndf/domains/project/strategy_measure.py` | 新設 | 既存のリポジトリの事例と文書を測り、値・根拠・問いを出す | `core/branch_strategy.py`・`adapters/git.py`・`adapters/github.py` |
| `plugins/ndf-next/src/ndf/domains/project/strategy_write.py` | 新設 | 既定の行と答えから節を組み、検査を通した中身だけを書く・消す | `core/branch_strategy.py` |
| `plugins/ndf-next/src/ndf/domains/project/strategy_migrate.py` | 新設 | 旧い 4 か所を節へ移す差分を作り、ハッシュが合えば書く | `core/branch_strategy.py`・`core/branch_strategy_legacy.py`・`strategy_write.py` |
| `plugins/ndf-next/src/ndf/domains/project/cli.py` | 変更 | サブコマンド `branch-strategy` を足し、`drift` と旧いブランチの解きを消す | `strategy_*.py` |
| `plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 変更 | 起点と宛先を節から読み、スプリントの開始時のスナップショットを書く | `core/branch_strategy.py` |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 変更 | `version_files` と supervise.json の `release` の読みを消し、節の `release` を読む | `core/branch_strategy.py` |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | 承認した head を節の主体と方式で入れ、ruleset と版上げの PR を確かめる | `core/branch_strategy.py`・`adapters/github.py` |
| `plugins/ndf-next/src/ndf/adapters/github.py` | 変更 | キューへの投入に先頭へ入れる指定を足す | 無し |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | `$defs/BranchStrategy` のコピー | 無し |
| `plugins/ndf/skills/worktree/schemas/worktree.schema.json` | 変更 | 旧いキーの定義を消す（D5） | 無し |
| `plugins/ndf/skills/release/schemas/release.schema.json` | 変更 | `version_files` の定義を消す（D7） | 無し |
| `plugins/ndf/scripts/data/branch-strategy-presets.json` | 新設 | 既定の表のコピー | 無し |
| `plugins/ndf/scripts/data/ndf-settings-renames.json` | 新設 | 移す行の表のコピー | 無し |
| `plugins/ndf/scripts/lib/branch_strategy.py` | 新設 | 11.0 の読む部品と同じ責務。シェルから `get` で読む CLI を持つ | `lib/branch_strategy_legacy.py`・`lib/deps.py` |
| `plugins/ndf/scripts/lib/branch_strategy_legacy.py` | 新設 | 11.0 の旧い宣言の検査と同じ責務 | 無し |
| `plugins/ndf/scripts/lib/branch_flow.py` | 削除 | （D11） | — |
| `plugins/ndf/scripts/lib/deps.py` | 変更 | 依存のグループ `jsonschema` を足す | 無し |
| `plugins/ndf/pyproject.toml` | 変更 | 任意の依存 `jsonschema`（11.0 と同じ版 4.26.0）を足す | 無し |
| `plugins/ndf/uv.lock` | 変更 | 上の依存を固定する | 無し |
| `plugins/ndf/scripts/lib/gh_graphql.py` | 変更 | merge queue の有無を読み、承認した head をキューへ入れる関数を足す | 無し |
| `plugins/ndf/scripts/lib/release_case.py` | 変更 | 迂回の要否を節の `merge.admin` から読む | `lib/branch_strategy.py` |
| `plugins/ndf/scripts/project-decl.py` | 変更 | サブコマンド `branch-strategy` を足し、`drift` と旧い 4 か所の書き出しを消す（D14） | `project_lib/branch_strategy_*.py` |
| `plugins/ndf/scripts/project_lib/branch_strategy_measure.py` | 新設 | 11.0 の `strategy_measure.py` と同じ責務 | `lib/branch_strategy.py`・`lib/gh_rest.py` |
| `plugins/ndf/scripts/project_lib/branch_strategy_write.py` | 新設 | 11.0 の `strategy_write.py` と同じ責務 | `lib/branch_strategy.py` |
| `plugins/ndf/scripts/project_lib/branch_strategy_migrate.py` | 新設 | 11.0 の `strategy_migrate.py` と同じ責務 | `lib/branch_strategy.py`・`lib/branch_strategy_legacy.py`・`branch_strategy_write.py` |
| `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | 削除 | （D15） | — |
| `plugins/ndf/scripts/release_lib/promotion.py` | 削除 | （D17） | — |
| `plugins/ndf/scripts/release_lib/strategy_route.py` | 新設 | 取り込み方の値から、版上げの PR の起点・宛先・同期の PR の要否を決める | `lib/branch_strategy.py` |
| `plugins/ndf/scripts/release_lib/land.py` | 新設 | 承認した head を節の主体と方式で入れ、ruleset と版上げの PR を確かめる | `lib/branch_strategy.py`・`lib/gh_rest.py`・`lib/gh_graphql.py` |
| `plugins/ndf/scripts/release_lib/approval_trail.py` | 変更 | 証跡の書き手をケースごとに 1 つに決める関数を足す | `lib/branch_strategy.py` |
| `plugins/ndf/scripts/release-steps.py` | 変更 | サブコマンド `land` を足し、`wait_and_merge` の迂回の引数を消す | `release_lib/land.py`・`release_lib/strategy_route.py` |
| `plugins/ndf/scripts/supervise_lib/decl.py` | 変更 | スプリントの開始時のスナップショットを節の全体にする（決定 9） | `lib/branch_strategy.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | PR の宛先をスナップショットから読む | `lib/branch_strategy.py` |
| `plugins/ndf/scripts/worktree-setup.sh` | 変更 | 起点を `branch_strategy.py get` で読み、止まったら理由をそのまま出す | `lib/branch_strategy.py` |
| `plugins/ndf/skills/development-workflow/references/project-analysis.md` | 変更 | 解析のサブコマンドを打つ手順と、問いを人へ回す手順 | 無し |
| `plugins/ndf/skills/worktree/references/declaration.md` | 変更 | 節を手で書く手順を消し、作成のコマンドを示す（D31） | 無し |
| `scripts/check-legacy-branch-decl.py` | 新設 | 削除の一覧の各行のパスに消す語が残れば落とす | `scripts/legacy-branch-decl.json` |
| `scripts/legacy-branch-decl.json` | 新設 | 削除の一覧（D1〜D37）を 1 ファイル 1 行で持つ | 無し |
| `scripts/check-skill-repo-assumptions.py` | 変更 | スキーマの `default`・既定の表の ai-plugins の名前・宛先の決め打ち・条件なしの迂回を落とす（決定 13） | 無し |
| `scripts/check-ndf-contracts.py` | 変更 | 正本とコピー（スキーマの定義・既定の表・移す行の表）の一致を確かめる | 無し |
| `scripts/check-pr-base.sh` | 変更 | 宛先のブランチの節を `branch_strategy.py get --ref` で読む（D27） | `lib/branch_strategy.py` |
| `scripts/ndf-choices.json` | 変更 | 選べる値の基準を `release_strategy` の値へ改名し、`removed` に #3672 の行を足す | 無し |
| `.ndf/project.json` | 変更 | 移行のコマンドで ai-plugins の値の節を書く（C7。手で書かない） | 無し |
| `.ndf/worktree.json` | 変更 | 移行のコマンドで旧いキーを消す（C7。手で書かない） | 無し |
| `.ndf/supervise.json` | 変更 | 移行のコマンドで旧いキーを消す（C7。手で書かない） | 無し |
| `.github/workflows/release-dev.yml` | 削除 | ai-plugins の開発版をやめる（P1） | — |
| `AGENTS.md` | 変更 | ai-plugins の値の説明へ書き直す（D36。C7） | 無し |
| `docs/versioning-and-distribution.md` | 変更 | ai-plugins の値の説明へ書き直す（D36） | 無し |
| `plugins/ndf/scripts/tests/test_branch_strategy.py` | 新設 | 10 系の単体テスト（「単体テストの観点」） | 5 種の宣言の fixture |
| `plugins/ndf-next/tests/test_branch_strategy.py` | 新設 | 11.0 の単体テスト | 5 種の宣言の fixture |
| `scripts/tests/test_check_legacy_branch_decl.py` | 新設 | 削除の一覧の検査のテスト | `scripts/check-legacy-branch-decl.py` |

5 種の宣言の fixture は、11.0 の `plugins/ndf-next/tests/fixtures/branch_strategy/` の 1 か所に置く。10 系のテストはそのパスを読む（設計文書の「テスト設計」）。

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/branch_strategy.py`・`plugins/ndf-next/src/ndf/core/branch_strategy.py` | `Strategy`（不変の値）・`StrategyError`・モジュール | 新設: `load`・`load_at_ref`・`validate`・`get`・`snapshot`・`snapshot_diff`・`Strategy.pr_target`・`Strategy.sprint_start_ref`・`main`（10 系だけ。`get` の CLI） |
| `plugins/ndf/scripts/lib/branch_strategy_legacy.py`・`plugins/ndf-next/src/ndf/core/branch_strategy_legacy.py` | `Move`・`Found`・モジュール | 新設: `load_moves`・`find_legacy`・`legacy_reason` |
| `plugins/ndf/scripts/project_lib/branch_strategy_measure.py`・`plugins/ndf-next/src/ndf/domains/project/strategy_measure.py` | `Samples`・`Measured`・モジュール | 新設: `collect_samples`・`count_key`・`doc_claims`・`pick_kind`・`measure` |
| `plugins/ndf/scripts/project_lib/branch_strategy_write.py`・`plugins/ndf-next/src/ndf/domains/project/strategy_write.py` | `WritePlan`・モジュール | 新設: `init_values`・`apply_answers`・`plan_write`・`plan_remove`・`commit_plan` |
| `plugins/ndf/scripts/project_lib/branch_strategy_migrate.py`・`plugins/ndf-next/src/ndf/domains/project/strategy_migrate.py` | モジュール | 新設: `plan_migration` |
| `plugins/ndf/scripts/project-decl.py`・`plugins/ndf-next/src/ndf/domains/project/cli.py` | モジュール | 新設: `cmd_branch_strategy`。変更: `build_parser`（10 系）・`_parser`（11.0）。削除: `cmd_drift`・`_drift`・`_declared_flow`・`_merge_worktree_part`・`_flow_branches` |
| `plugins/ndf/scripts/release_lib/strategy_route.py` | `Route`・モジュール | 新設: `route_of` |
| `plugins/ndf/scripts/release_lib/land.py`・`plugins/ndf-next/src/ndf/domains/release/land.py` | 10 系はモジュール、11.0 は `Lander` | 新設: `ruleset_mismatch`・`check_release_pr`・`land_approved`（11.0 は `Lander.land` を変更して同じ手順にする）。11.0 の変更: `Lander._bypass`・`Lander.land` |
| `plugins/ndf/scripts/release_lib/approval_trail.py` | モジュール | 新設: `trail_writer` |
| `plugins/ndf/scripts/lib/gh_graphql.py`・`plugins/ndf-next/src/ndf/adapters/github.py` | 10 系はモジュール、11.0 は `GitHub` | 新設: `merge_queue_enabled`・`enqueue_pr`（10 系）。変更: `GitHub._enqueue`（11.0。`jump` を足す） |
| `plugins/ndf/scripts/lib/release_case.py` | `Case` | 変更: `case_at`（迂回の要否を節から読む） |
| `plugins/ndf/scripts/release-steps.py` | モジュール | 新設: `cmd_land`。変更: `wait_and_merge`（引数 `no_admin` を消す） |
| `plugins/ndf/scripts/supervise_lib/decl.py` | モジュール | 変更: `flow_snapshot` を `strategy_snapshot` へ置き換える。削除: `production_flow_decl` |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | モジュール | 呼ぶ先をスナップショットの `Strategy.pr_target` へ替える（シグネチャ・責務は変えない） |
| `plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | `Declaration` | 変更: `load`（起点と宛先を節から読む）。新設: `write_start_snapshot` |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | `Declaration` | 変更: `_parse`（`version_files` を読まない） |
| `plugins/ndf/scripts/worktree-setup.sh` | シェル | 変更: `resolve_base`（`branch_strategy.py get /branches/base` を呼ぶ） |
| `scripts/check-legacy-branch-decl.py` | モジュール | 新設: `load_rows`・`scan_rows`・`main` |
| `scripts/check-skill-repo-assumptions.py` | モジュール | 新設: `branch_strategy_problems` |
| `scripts/check-ndf-contracts.py` | モジュール | 新設: `copy_mismatches` |
| `scripts/check-pr-base.sh` | シェル | 変更: 宛先の節の読みを `branch_strategy.py get --ref` へ替える |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `load` | 10 系 `lib/branch_strategy.py`・11.0 `core/branch_strategy.py` | 新設 | `load(root: Path) -> Strategy` | メインディレクトリの節を読み、旧いキーとスキーマで検査して型にする | `root` がリポジトリのメインディレクトリ | 返した `Strategy` はスキーマ（R1〜R10）を通り、旧い 4 か所に旧いキーが無い（I1・I3・I4） | `.ndf/` の 4 ファイルを読む |
| `load_at_ref` | 同上 | 新設 | `load_at_ref(root: Path, ref: str) -> Strategy` | 指定したブランチの節を `git show <ref>:.ndf/project.json` で読み、`load` と同じ検査をする | `ref` が `origin/<名前>` の形で取得済み | `load` と同じ | `git show` を 4 回呼ぶ |
| `validate` | 同上 | 新設 | `validate(section: dict) -> list[dict]` | 節をスキーマ `$defs/BranchStrategy` で検査し、落ちた規則とキーの位置を返す | `section` が JSON の値 | 返す各要素は `{"rule": "R<数>" か "shape", "pointer"}`。空なら通った | スキーマのファイルを読む（初回だけ。プロセスの中で使い回す） |
| `get` | 同上 | 新設 | `get(strategy: Strategy, pointer: str) -> Any` | 節の中の 1 つの値を JSON Pointer で引く | `pointer` が `/` で始まる | 値があれば返す。無ければ `StrategyError` | 無し |
| `main` | 10 系 `lib/branch_strategy.py` | 新設 | `main(argv: list[str]) -> int`（`get <pointer> [--ref <ref>] [--root <dir>]`） | シェルから節の値を 1 つ読む CLI | 引数が 1 つの pointer | 値を標準出力へ 1 行で出し 0。止まれば理由を標準エラーへ出す | `.ndf/` を読む |
| `snapshot` | 10 系 `lib/branch_strategy.py`・11.0 `core/branch_strategy.py` | 新設 | `snapshot(strategy: Strategy, source: str) -> dict` | スプリントの開始時に控える節の全体を作る | `strategy` が `load` の戻り値 | `{"source", "digest", "section"}`。`digest` は節の正規化した JSON の SHA-256 | 無し |
| `snapshot_diff` | 同上 | 新設 | `snapshot_diff(snap: dict, now: Strategy) -> list[str]` | スナップショットと今の節の違うキーの位置を並べる（I13） | `snap` が `snapshot` の戻り値 | 違うキーの JSON Pointer の昇順の一覧。同じなら空 | 無し |
| `Strategy.pr_target` | 同上 | 新設 | `pr_target(self, kind: str) -> str` | PR の種類（`sprint`・`doc`・`release`）ごとの宛先のブランチ名を返す | `kind` が 3 つのどれか | 設計文書の「代表のリポジトリの種類の既定」の 2 つ目の表の宛先と同じ | 無し |
| `Strategy.sprint_start_ref` | 同上 | 新設 | `sprint_start_ref(self) -> str` | スプリントブランチを切る元のブランチの名前（スプリントの起点）を返す | 無し | `sprint.start` が `base` なら `branches.base`、`production` なら `branches.production` | 無し |
| `load_moves` | 10 系 `lib/branch_strategy_legacy.py`・11.0 `core/branch_strategy_legacy.py` | 新設 | `load_moves(path: Path \| None = None) -> list[Move]` | 移す行の表のうち、節へ移す行と節のキーの `remove` の行だけを読む | 表が JSON の配列 | 各 `Move` は `file`・`old`・`new_file`・`new`（`remove` は `new` が None）を持つ | 表のファイルを読む |
| `find_legacy` | 同上 | 新設 | `find_legacy(files: dict[str, dict], moves: list[Move]) -> list[Found]` | 読んだ `.ndf/` の中身から旧いキーの位置を探す | `files` はファイル名 → 中身（無いファイルは入れない） | 旧いキーの位置（ファイルと JSON Pointer）の一覧。無ければ空 | 無し |
| `legacy_reason` | 同上 | 新設 | `legacy_reason(found: list[Found], system: str) -> str` | 止まる理由の文（ファイル・キーの位置・移行のコマンド）を作る | `found` が空でない。`system` は `10` か `11` | 設計文書の「入出力の契約」の止まり方の文の形 | 無し |
| `collect_samples` | 10 系 `project_lib/branch_strategy_measure.py`・11.0 `domains/project/strategy_measure.py` | 新設 | `collect_samples(root: Path, gh_repo: str \| None, deadline: float) -> Samples` | 直近のマージ済み PR・タグ・Release・ruleset・ワークフロー・版数のファイルを集める | `deadline` は `time.monotonic()` の値 | `Samples` は各事例の一覧と、読めなかった種類（`unreadable`）を持つ | `gh api` と `git` を呼ぶ（ネットワーク） |
| `count_key` | 同上 | 新設 | `count_key(pointer: str, observed: list[tuple[Any, str]], rule: dict) -> Measured` | 測るキー 1 つについて、値ごとの事例を数えて値と根拠を決める | `observed` は（値・事例の参照）の並び。`rule` は既定の表の閾値 | 3 件以上かつ 9 割以上の値があれば `value` と `evidence`、無ければ `why` が `insufficient` か `conflict` | 無し |
| `doc_claims` | 同上 | 新設 | `doc_claims(root: Path) -> list[dict]` | README・CONTRIBUTING・AGENTS.md からブランチ名と宛先を述べる行を拾う | 無し | 各要素は `{"pointer", "value", "ref": "<パス>:<行>"}` | 3 つの文書を読む |
| `pick_kind` | 同上 | 新設 | `pick_kind(measured: dict[str, Measured], presets: dict) -> tuple[str \| None, list[str]]` | 測った値がすべて合う種類の既定の行を 1 つ選ぶ | `measured` の各値は決まっている | 合う行が 1 つならその名前と空の一覧、0 か 2 つ以上なら None と候補の一覧 | 無し |
| `measure` | 同上 | 新設 | `measure(root: Path, gh_repo: str \| None, deadline: float) -> dict` | 既存のリポジトリを測り、作成の結果（値・根拠・問い）を組む | 無し | 結果はスキーマ `branch-strategy.result.schema.json` を通る。`questions` が空でなければ `status: ask` で、`values` は書く候補であって書いていない（I11） | `collect_samples`・`doc_claims` の副作用 |
| `init_values` | 10 系 `project_lib/branch_strategy_write.py`・11.0 `domains/project/strategy_write.py` | 新設 | `init_values(kind: str, facts: dict, presets: dict) -> tuple[dict, list[dict]]` | 種類の既定の行に測った名前（本番ブランチ・プラグイン・ランタイム・ワークフロー）を入れた節と、merge queue が分からないときの問いを返す | `kind` が 5 つのどれか | 節の値は既定の行と測った名前だけからできる。各値の根拠は `preset` か `repo` | 無し |
| `apply_answers` | 同上 | 新設 | `apply_answers(result: dict, answers: dict) -> dict` | 問いへの答えを節の値へ入れ、既定から変えたら種類を `custom` にする | `answers` は pointer → 値。`result` は `measure` か `init` の結果 | 答えた pointer の根拠は `answer`。答えていない問いが残れば `status: ask` のまま | 無し |
| `plan_write` | 同上 | 新設 | `plan_write(root: Path, section: dict) -> WritePlan` | 書く前の検査（`validate`）を通し、今の `.ndf/project.json` との差分とハッシュを作る | 無し | 検査に落ちれば `failed` を持つ計画（書かない）。通れば `diff`（JSON Patch）と `digest`（差分と今のファイルの中身の SHA-256） | `.ndf/project.json` を読む |
| `plan_remove` | 同上 | 新設 | `plan_remove(root: Path) -> WritePlan` | 節を消す差分とハッシュを作る | 無し | 節が無ければ空の差分 | `.ndf/project.json` を読む |
| `commit_plan` | 同上 | 新設 | `commit_plan(root: Path, plan: WritePlan, digest: str \| None) -> None` | 計画を書く。ハッシュを渡したときは今のファイルから作り直した計画と一致するときだけ書く | `plan.failed` が空 | 書いた後のファイルは計画の差分を当てた中身と同じ。途中で止まってもファイルは書く前か後のどちらか | `.ndf/` のファイルを一時ファイルから置き換える |
| `plan_migration` | 10 系 `project_lib/branch_strategy_migrate.py`・11.0 `domains/project/strategy_migrate.py` | 新設 | `plan_migration(root: Path, moves: list[Move]) -> WritePlan` | 旧い 4 か所の値を移す行の表どおりに節へ移し、旧いキーを消す差分を作る | 無し | 移した後の節が `validate` を通れば `diff` と `digest`。通らなければ `failed`。旧いキーが無く `remove` の行も当たらなければ空の差分 | `.ndf/` の 4 ファイルを読む |
| `cmd_branch_strategy` | 10 系 `project-decl.py`・11.0 `domains/project/cli.py` | 新設 | `cmd_branch_strategy(a: argparse.Namespace) -> int` | 動詞（`init`・`measure`・`write`・`migrate`・`remove`）を受けて上の関数を組み、作成の結果を 1 行の JSON で出す | 引数が動詞ごとの必須を満たす（`init` は `--kind` を省ける。省けば問いを返す） | 終了コードは `ok` が 0、`ask` が 3、`stopped` が 1、引数の誤りが 2 | 動詞ごとの関数の副作用 |
| `route_of` | 10 系 `release_lib/strategy_route.py` | 新設 | `route_of(strategy: Strategy) -> Route` | 取り込み方の値から、版上げの PR の起点・宛先・同期の PR の要否を決める | 無し | `merge_base_branch` は起点 base・同期あり、`release_branch` は起点 production・同期あり、`trunk` は起点 production・同期なし。承認の対象が `production_pr` なら版上げの PR は無い（`Route.release_pr` が偽） | 無し |
| `ruleset_mismatch` | 10 系 `release_lib/land.py`・11.0 `domains/release/land.py` | 新設 | `ruleset_mismatch(strategy: Strategy, rules: list[dict], queue: bool) -> list[dict]` | 節の値と本番ブランチの ruleset（承認の数・merge queue の有無・最新の取り込みの要否）の食い違いを並べる（I10） | `rules` は `rules/branches/<本番>` の応答 | 各要素は `{"key", "declared", "ruleset"}`。空なら一致 | 無し |
| `check_release_pr` | 同上 | 新設 | `check_release_pr(strategy: Strategy, pr: dict, prod: dict, queue: list[dict]) -> str \| None` | 版上げの PR がマージの方式ごとの確かめ（I7）を通るかを判定する | `pr` は head・切った時点の版数、`prod` は本番の先端と版数、`queue` はキューの PR | 通れば None。落ちれば理由の文 | 無し |
| `land_approved` | 10 系 `release_lib/land.py`（11.0 は `Lander.land` の変更） | 新設 | `land_approved(root: Path, strategy: Strategy, pr: int, approved_head: str) -> dict` | 承認した head を、節の主体と方式で本番ブランチへ入れる | 承認の証跡が今の head にある | 返す `{"result": "merged" / "queued" / "human" / "stopped", "head", "reason"}`。`merged`・`queued` のとき入れた head は `approved_head` と同じ（I5） | `gh api` でマージかキューへの投入、PR のコメントを書く |
| `Lander.land` | 11.0 `domains/release/land.py` | 変更 | `land(self, number: int) -> Landed`（変えない） | 10 系の `land_approved` と同じ手順で入れる。迂回は節の `merge.admin` だけで決める | 同上 | 同上（`Landed` の形は変えない） | 同上 |
| `Lander._bypass` | 同上 | 変更 | `_bypass(self, pull: Pull) -> bool`（変えない） | 迂回の要否を `production_merge.admin` ではなく節の `merge.admin` から読む | 無し | `merge.method` が `direct` で `merge.admin` が真のときだけ真（I6） | 無し |
| `trail_writer` | 10 系 `release_lib/approval_trail.py`（11.0 は `domains/release/trail.py` の同名の関数） | 新設 | `trail_writer(strategy: Strategy, merged_by_human: bool) -> str` | 承認の証跡の書き手をケースごとに 1 つ返す（I12） | 無し | `answer`・`land`・`github` のどれか（設計文書の「承認とマージ」の表） | 無し |
| `merge_queue_enabled` | 10 系 `lib/gh_graphql.py` | 新設 | `merge_queue_enabled(repo: str, branch: str) -> bool \| None` | 本番ブランチに merge queue があるかを読む | 無し | 読めなければ None | GraphQL を 1 回呼ぶ |
| `enqueue_pr` | 10 系 `lib/gh_graphql.py` | 新設 | `enqueue_pr(repo: str, pr: int, expected_head: str, jump: bool) -> gh_quota.Attempt` | 承認した head を指定してキューへ入れる。版上げの PR は先頭へ入れる | `expected_head` は完全な SHA | head が違えば GitHub が断る（STD1） | GraphQL の `enqueuePullRequest` を 1 回呼ぶ |
| `GitHub._enqueue` | 11.0 `adapters/github.py` | 変更 | `_enqueue(self, pull: Pull, method: Optional[str], jump: bool = False) -> Optional[MergeOutcome]` | 先頭へ入れる指定を足す | 同上 | 同上 | GraphQL を呼ぶ |
| `case_at` | 10 系 `lib/release_case.py` | 変更 | `case_at(root, strict: bool = False) -> Case`（変えない） | 迂回の要否を節の `merge.admin` から決める（ruleset の承認の数からは決めない） | 節が読める | `Case.admin_merge` は `merge.admin` と同じ | `gh api` で ruleset を読む |
| `cmd_land` | 10 系 `release-steps.py` | 新設 | `cmd_land(a: argparse.Namespace) -> int` | `land_approved` を呼び、結果を 1 行の JSON で出す | `--pr` を渡す | `merged`・`queued`・`human` は 0、`stopped` は 1 | `land_approved` の副作用 |
| `wait_and_merge` | 10 系 `release-steps.py` | 変更 | `wait_and_merge(root, n, expect=None, ci_wait=3600.0, gate_approved=None)` | 迂回の要否を引数でなく節から決める | 同上 | 迂回は I6 のときだけ | `gh` でマージする |
| `strategy_snapshot` | 10 系 `supervise_lib/decl.py` | 変更（`flow_snapshot` を置き換える） | `strategy_snapshot(a, root: Path) -> dict` | スプリントの開始時に節の全体を控える（決定 9） | 節が読める | プランの状態に `snapshot` の戻り値が入る。2 回目以降の呼び出しは控えを変えない | プランの状態のファイルを書く |
| `write_start_snapshot` | 11.0 `domains/worktree/declaration.py` | 新設 | `write_start_snapshot(main_dir: Path, state_dir: Path) -> dict` | 10 系の `strategy_snapshot` と同じ責務 | 同上 | 同上 | 状態のファイルを書く |
| `Declaration.load` | 11.0 `domains/worktree/declaration.py` | 変更 | `load(main_dir: Path) -> Optional[Declaration]`（変えない） | 起点と宛先を節から読み、worktree.json からは worktree の作り方だけを読む | 無し | 旧いキーがあれば `DeclarationError`（理由は `legacy_reason`） | `.ndf/` を読む |
| `resolve_base` | 10 系 `worktree-setup.sh` | 変更 | `resolve_base`（標準出力へブランチ名） | 起点を `branch_strategy.py get /branches/base` で読む | 無し | 読めなければ理由を標準エラーへ出して終了コード 3 | `branch_strategy.py` を呼ぶ |
| `load_rows` | `scripts/check-legacy-branch-decl.py` | 新設 | `load_rows(path: Path) -> list[dict]` | 削除の一覧のデータを読む | 無し | 各行は `{"id": "D<数>", "path", "words"}` | ファイルを読む |
| `scan_rows` | 同上 | 新設 | `scan_rows(root: Path, rows: list[dict]) -> list[dict]` | 各行のパスに消す語が残る箇所を並べる | 無し | 各要素は `{"id", "path", "line", "word"}`。パスのファイルが無い行は通す | パスのファイルを読む |
| `branch_strategy_problems` | `scripts/check-skill-repo-assumptions.py` | 新設 | `branch_strategy_problems(root: Path) -> list[str]` | スキーマの `default`・既定の表の ai-plugins の名前・宛先の決め打ち・条件なしの迂回を見つける（I14） | 無し | 問題 1 件につき 1 行（パス:行と理由） | ファイルを読む |
| `copy_mismatches` | `scripts/check-ndf-contracts.py` | 新設 | `copy_mismatches(root: Path) -> list[str]` | 正本とコピーの 3 組（定義・既定の表・移す行の表）の違いを並べる | 無し | 違う組ごとに 1 行。同じなら空 | ファイルを読む |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load` | 節が無い | 作成のコマンドを打っていない | `.ndf/project.json` に `branch_strategy` が無い | 止める（I3。既定値へ倒さない） | `StrategyError`。理由は「`.ndf/project.json` に節がありません。`<作成のコマンド>` で作ってください」。CLI は 3 | 無し。作った後に再実行できる |
| `load` | 旧いキーが残る | NDF の版を上げたが移していない | `find_legacy` | 止める（I4） | `legacy_reason` の文。CLI は 3 | 無し。移行のコマンドの後に再実行できる |
| `load` | スキーマに落ちる | 手で書いた・版のスキーマが変わった | `validate` | 止める | 落ちた規則とキーの位置を並べた理由。CLI は 1 | 無し。`write` で直した後に再実行できる |
| `load` | JSON として読めない | ファイルの破損・重複したキー | JSON の読み込み | 止める | ファイルと行の位置。CLI は 2 | 無し |
| `load` | 節が 2 か所にある | 旧い 4 か所に `branch_strategy` を書いた | `find_legacy`（表の行に載る） | 止める（I1） | `legacy_reason` の文。CLI は 3 | 同上 |
| `load_at_ref` | ブランチが無い・ファイルが無い | 取得していない・宛先のブランチに節が無い | `git show` の終了コード | 止める | ref とパス。CLI は 3 | 取得の後に再実行できる |
| `validate` | スキーマのファイルが無い・壊れている | 導入の破損 | スキーマの読み込み | 止める | スキーマのパスと誤り。終了コード 2 | 再導入の後に再実行できる |
| `validate` | jsonschema が入っていない（10 系） | 依存のグループが未導入 | `deps.require("jsonschema")` | 止める（`deps` の既定の振る舞い） | `deps` の案内。終了コード 69 | `deps` が uv で入れて再実行できる |
| `main` | pointer の値が無い | キーの誤り | `get` | 止める | 「`<pointer>` は節にありません」。終了コード 2 | 無し |
| `load_moves` | 表が無い・形が違う | 導入の破損 | 表の読み込み | 止める | 表のパスと誤り。終了コード 2 | 再導入の後に再実行できる |
| `collect_samples` | GitHub を読めない | 認証が無い・上限・ネットワーク | `gh api` の終了コード | 既定値で続ける（その種類の事例を 0 件として `unreadable` に載せる） | 結果の `questions` に `insufficient`（理由に読めなかった種類） | 認証の後に `measure` を打ち直せる |
| `collect_samples` | 時間切れ | 標本が多い・応答が遅い | `deadline` を過ぎた | 既定値で続ける（集めた分だけで数える） | 結果の `warnings` に打ち切った種類 | 打ち直せる |
| `doc_claims` | 文書が読めない | 文字コード・権限 | ファイルの読み込み | 既定値で続ける（その文書を飛ばす） | 結果の `warnings` にパス | 打ち直せる |
| `measure` | 種類が決まらない | 合う既定の行が 0 か 2 つ以上 | `pick_kind` | 呼び出し元へ返す（`no_kind` の問い。書かない） | `status: ask`。終了コード 3 | 答えを `write --answers` で渡す |
| `measure` | 事例が足りない・食い違う | 3 件・9 割に届かない、文書と事例が違う | `count_key`・`doc_claims` の比べ | 呼び出し元へ返す（`insufficient`・`conflict` の問い。書かない。I11） | `status: ask`。終了コード 3 | 同上 |
| `plan_write` | 検査に落ちる | 答えが規則 R1〜R10 に反する | `validate` | 呼び出し元へ返す（書かない。I2） | `status: stopped`・`failed`。終了コード 1 | ファイルは変わらない。答えを直して打ち直せる |
| `plan_write` | `.ndf/project.json` が読めない | 破損 | JSON の読み込み | 止める | パスと誤り。終了コード 2 | 無し |
| `plan_remove` | `.ndf/project.json` が読めない | 破損 | JSON の読み込み | 止める | パスと誤り。終了コード 2 | 無し |
| `commit_plan` | ハッシュが合わない | 示した後にファイルが変わった・別の実行が先に書いた | 作り直した計画の `digest` との比べ | 止める | 「示した差分と今の差分が違います。打ち直してください」。終了コード 1 | ファイルは変わらない。差分を示す実行から打ち直す |
| `commit_plan` | 書き込みに失敗 | 容量・権限 | 一時ファイルの書き込みか置き換え | 止める | パスと誤り。終了コード 1 | 一時ファイルを消す。対象のファイルは書く前のまま。打ち直せる |
| `plan_migration` | 移した後の節が検査に落ちる | 旧い値の組み合わせが新しい規則に反する（例: `whole` で base と production が同じ） | `validate` | 呼び出し元へ返す（書かない） | `status: stopped`・`failed` と旧いキーの位置。終了コード 1 | ファイルは変わらない。人が `write --answers` で値を選ぶ |
| `plan_migration` | 値の対応が表に無い | 表に無い旧い値 | 移す行の値の対応 | 呼び出し元へ返す（問い） | `status: ask`。終了コード 3 | 同上 |
| `cmd_branch_strategy` | 引数の誤り | 動詞・`--kind` の値の誤り | argparse | 止める | 使い方。終了コード 2 | 無し |
| `ruleset_mismatch` | （食い違いを返す関数。失敗は呼び出し元で扱う） | — | — | 呼び出し元へ返す | `land_approved` の行のとおり | — |
| `land_approved` | head が動いた | 承認の後にコミットが積まれた（#3661） | 証跡の head と今の head の比べ | 止める（I5・E10） | 承認を取り直す依頼。終了コード 1 | 何も入れない。承認を取り直して打ち直せる |
| `land_approved` | ruleset と節が食い違う | `via: gate` で ruleset が承認を求める等 | `ruleset_mismatch` | 止める（I10） | 節の値と ruleset の値。終了コード 1 | 何も入れない。ruleset か節を直して打ち直せる |
| `land_approved` | 版上げの PR が確かめに落ちる | `strict` で先端を含まない、`merge-queue` で版数が衝突する | `check_release_pr` | 止める（I7） | 理由と、版上げの PR を作り直す依頼。終了コード 1 | 何も入れない。作り直して承認を取り直す |
| `land_approved` | GitHub がマージかキューへの投入を断る | head の食い違い・必須の検査の失敗・権限 | `gh api` の応答 | 止める（再試行しない。断られた理由が承認の前提を崩すため） | 応答の種類と本文。終了コード 1 | 何も入れていない。理由を直して打ち直せる |
| `land_approved` | 人が先にマージした | `actor: human` か人の操作 | PR の状態が merged | 呼び出し元へ返す（`human`。人のマージの記録を書く） | `result: human`。終了コード 0 | 記録が既にあれば書かない（冪等） |
| `land_approved` | 証跡のコメントが書けない | 権限・上限 | `gh api` の応答 | 止める（I12。問いを送り直さない） | 書けなかった理由。終了コード 1 | 打ち直せる |
| `Lander.land` | 10 系の `land_approved` と同じ | 同じ | 同じ | 同じ | `Landed` の `result` と理由（形は変えない） | 同じ |
| `merge_queue_enabled` | 読めない | 権限・ネットワーク | GraphQL の応答 | 呼び出し元へ返す（None。`init` は `merge_queue_unknown` の問いにする） | 問いとして出る | 打ち直せる |
| `enqueue_pr` | 断られる | head の食い違い・`jump` の権限が無い | GraphQL の誤り | 呼び出し元へ返す（`land_approved` が止める） | 上の `land_approved` の行 | 同上 |
| `GitHub._enqueue` | 同上 | 同上 | 同上 | 同上 | 同上 | 同上 |
| `case_at` | ruleset が読めない | 権限・ネットワーク | `gh api` の応答 | 止める（`strict` のとき）。そうでなければ今の振る舞いのまま | 今の文 | 打ち直せる |
| `cmd_land` | `land_approved` の失敗 | 同上 | 同上 | 呼び出し元へ返す | 1 行の JSON と終了コード | 同上 |
| `wait_and_merge` | 節が読めない | `load` の失敗 | `load` | 止める | `load` の理由 | 同上 |
| `strategy_snapshot` | 節が読めない | `load` の失敗 | `load` | 止める（スプリントを始めない） | `load` の理由。終了コード 3 | 直した後にスプリントを始め直せる |
| `strategy_snapshot` | 状態のファイルが書けない | 容量・権限 | 書き込み | 止める | パスと誤り。終了コード 1 | 打ち直せる |
| `write_start_snapshot` | `strategy_snapshot` と同じ | 同じ | 同じ | 同じ | 同じ | 同じ |
| `Declaration.load` | `load` と同じ | 同じ | 同じ | 止める（`DeclarationError`） | `load` の理由 | 同じ |
| `resolve_base` | `branch_strategy.py` が止まる | 節が無い・旧いキー | 終了コード | 止める（worktree を作らない） | `branch_strategy.py` の理由をそのまま。終了コード 3 | 直した後に打ち直せる |
| `main` | `load` の失敗 | 同上 | 同上 | 止める | 同上 | 同上 |
| `load_rows` | データが無い・形が違う | 誤った編集 | JSON の読み込み | 止める | パスと誤り。終了コード 2 | 無し |
| `scan_rows` | パスのファイルが読めない | 権限・バイナリ | 読み込み | 止める（CI を通さない） | パスと誤り。終了コード 2 | 無し |
| `branch_strategy_problems` | 対象のファイルが無い | 置き場の誤り | 読み込み | 止める | パス。終了コード 2 | 無し |
| `copy_mismatches` | 正本かコピーが無い | 置き場の誤り | 読み込み | 止める | パス。終了コード 2 | 無し |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `load` | 旧いキーあり | 止まる（I4）。スキーマは見ない |
| `load` | 旧いキーなし・節なし | 止まる（I3） |
| `load` | 旧いキーなし・節あり・スキーマに落ちる | 止まる（落ちた規則） |
| `load` | 旧いキーなし・節あり・スキーマを通る | `Strategy` を返す |
| `cmd_branch_strategy` | `init`・`--kind` あり・履歴なし | `init_values` → `plan_write` → `commit_plan`。merge queue が分からなければ問いを返して書かない |
| `cmd_branch_strategy` | `init`・`--kind` なし・履歴なし | `no_kind` の問い（5 つの種類を候補）。書かない（受け入れ条件 8） |
| `cmd_branch_strategy` | `init`・履歴あり | 止まり、`measure` を打つよう案内する（既存のリポジトリに種類の既定を当てない） |
| `cmd_branch_strategy` | `measure` | `measure` の結果を出す。書かない |
| `cmd_branch_strategy` | `write --answers <ファイル>`・`--dry-run` | `apply_answers` → `plan_write`。差分とハッシュを出し、書かない |
| `cmd_branch_strategy` | `write --answers <ファイル>`・`--digest <ハッシュ>` | 同上の計画を作り直し、ハッシュが合えば `commit_plan` |
| `cmd_branch_strategy` | `migrate`（ハッシュなし） | `plan_migration` の差分とハッシュを出す。書かない |
| `cmd_branch_strategy` | `migrate --digest <ハッシュ>` | 計画を作り直し、ハッシュが合えば `commit_plan` |
| `cmd_branch_strategy` | `remove`（ハッシュなし / あり） | `plan_remove` の差分を出す / 合えば書く |
| `count_key` | 総数 3 件未満 | `insufficient` |
| `count_key` | 総数 3 件以上・最多の値が 9 割以上 | その値と事例の一覧 |
| `count_key` | 総数 3 件以上・最多の値が 9 割未満 | `conflict`（値ごとの件数を候補に） |
| `measure` | 測るキーで問いが 1 つ以上 | 種類を選ばずに `ask`（測るキーの問いだけを返す） |
| `measure` | 測るキーがすべて決まり、文書の述べる値と違うキーがある | 違うキーごとに `conflict`。`ask` |
| `measure` | 測るキーがすべて決まり、文書と合い、種類が 1 つに決まる | 種類の既定から埋めるキーを足して `ok`（書く候補）。`approval.via` が `gate` で ruleset が承認を求めれば `conflict` を足して `ask` |
| `measure` | 同上で種類が 0 か 2 つ以上 | `no_kind`。`ask` |
| `measure` | 既に節があり、測った値と違う | 違うキーを `diff` に出す（更新の材料）。書かない |
| `route_of` | `merge_base_branch` | 版上げの PR は base から切り production へ。マージの後に production → base の同期の PR |
| `route_of` | `release_branch` | production から切ったリリースブランチへ選んだスプリントを集め、production へ。同期の PR |
| `route_of` | `trunk`・`release_pr` | production から切り production へ。同期の PR なし |
| `route_of` | `trunk`・`production_pr` | 版上げの PR なし（スプリントの PR が本番の PR を兼ねる） |
| `trail_writer` | `via: gate`・`actor: ndf`・人がマージしていない | `answer`（リリース承認ゲートの答えを書く手順） |
| `trail_writer` | `via: gate`・人が先にマージした、または `actor: human` | `land`（人のマージの記録） |
| `trail_writer` | `via: review` | `github`（NDF は書かない） |
| `check_release_pr` | `merge-queue`・本番の先端の版数が切った時点と同じ・キューに別の版上げの PR なし | 通る（先端を含むかは見ない） |
| `check_release_pr` | `merge-queue`・版数が違う、またはキューに別の版上げの PR あり | 落ちる |
| `check_release_pr` | `strict`・head が本番の先端を含む | 通る |
| `check_release_pr` | `strict`・含まない | 落ちる |
| `check_release_pr` | `direct` | 通る（確かめない） |
| `land_approved` | PR が merged | `human`（記録を書く） |
| `land_approved` | 証跡の head ≠ 今の head | `stopped`（I5） |
| `land_approved` | `ruleset_mismatch` が空でない | `stopped`（I10） |
| `land_approved` | 版上げの PR で `check_release_pr` が落ちる | `stopped`（I7） |
| `land_approved` | `actor: human` | 何もせず `human` の待ちを返す（人のマージを待つ） |
| `land_approved` | `actor: ndf`・`merge-queue` | `enqueue_pr`（版上げの PR は `jump: true`）→ `queued` |
| `land_approved` | `actor: ndf`・`strict` か `direct` | `sha` 付きでマージ（`direct` かつ `merge.admin` のときだけ迂回）→ `merged` |

- 繰り返しの終わる条件: `collect_samples` は PR 100 件・タグ 100 件・Release 100 件の上限か `deadline` で終わる。ほかの関数は繰り返しを持たないか、入力の長さで終わる
- 計算量の注意: `collect_samples` の PR ごとの `git` の呼び出し（起点の判定）は PR の数に比例する。上限 100 件で打ち切る。`scan_rows` は削除の一覧の行の数（ファイル約 300）×語の数で、CI の 1 ジョブの中に収まる

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Strategy` | 型（不変。節の dict を持ち、`pr_target`・`sprint_start_ref` を持つ） | 10 系 `lib/branch_strategy.py`・11.0 `core/branch_strategy.py` | — |
| `StrategyError` | 型（`reason: str`・`exit_code: int`） | 同上 | — |
| `Move` | 型（`file`・`old`・`new_file`・`new`・`values`） | 10 系 `lib/branch_strategy_legacy.py`・11.0 `core/branch_strategy_legacy.py` | — |
| `Found` | 型（`file`・`pointer`） | 同上 | — |
| `Samples` | 型（`prs`・`tags`・`releases`・`rulesets`・`workflows`・`version_files`・`unreadable`） | 10 系 `project_lib/branch_strategy_measure.py`・11.0 `domains/project/strategy_measure.py` | — |
| `Measured` | 型（`value`・`evidence`・`why`・`candidates`） | 同上 | — |
| `WritePlan` | 型（`files`・`diff`・`digest`・`failed`） | 10 系 `project_lib/branch_strategy_write.py`・11.0 `domains/project/strategy_write.py` | — |
| `Route` | 型（`release_pr: bool`・`source`・`target`・`sync: bool`） | 10 系 `release_lib/strategy_route.py` | — |
| 根拠の閾値（標本の最小の数・割合） | 設定値 | 既定の表 `branch-strategy-presets.json` の `evidence` | `min_samples: 3`・`min_ratio: 0.9` |
| 標本の上限（PR・タグ・Release） | 設定値 | 同上の `evidence` | `limit: 100` |
| 測るキーと種類の既定から埋めるキーの区分 | 設定値 | 同上の `measured_keys` | 設計文書の「作る・更新する」の表のとおり |
| 作成のコマンドの文（系ごと） | 定数 | 10 系 `lib/branch_strategy_legacy.py`・11.0 `core/branch_strategy_legacy.py` の `COMMAND` | 10 系 `python3 "$SCRIPTS/project-decl.py" branch-strategy`・11.0 `ndf project branch-strategy` |
| スキーマの置き場 | 定数 | 読む部品の `SCHEMA` | 11.0 `declarations/project.schema.json#/$defs/BranchStrategy`、10 系は `skills/development-workflow/schemas/project.schema.json` の同じ定義 |
| ハッシュの算法 | 定数 | 書く部品の `DIGEST` | SHA-256（正規化した JSON。キーの昇順・区切りの空白なし） |

閾値を 1 か所に持つため、今の解析の `MIN_FLOW_SAMPLES`・`HIGH`・`MID` は、測る部品を置き換える時点で消す。
これらの定数は 11.0 では `measure_flow.py`、10 系では `measure_branch_flow.py` にある。
2 つの系がそれぞれの定数を持たず、既定の表の値を読む。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 解析の `write`・`migrate`・`remove` が 2 つ同時に走る | `.ndf/project.json` ほか `.ndf/` の 4 ファイル | ロックを取らない。書く直前に今のファイルから計画を作り直し、示した差分のハッシュと比べる。違えば書かない | 同じ答えとハッシュで打ち直すと、2 回目は空の差分で何も書かない。書き込みは一時ファイルと `os.replace` で、途中で止まってもファイルは前か後のどちらか |
| 複数のファイルを書く `migrate` が途中で止まる | `.ndf/` の 4 ファイル | 書く順を「`.ndf/project.json` に節を書く → 旧い 3 ファイルのキーを消す」に固定する | 途中で止まると節と旧いキーが両方ある状態になり、読む部品は I1 で止まる。`migrate` を打ち直すと、節は表どおりの値で上書きされ旧いキーが消える（同じ結果になる） |
| 複数のレーンが同じ本番ブランチへ入れる | 本番ブランチ | `merge-queue` は GitHub のキュー、`strict` は `sha` を付けたマージを GitHub が断る、`direct` は Git の衝突で止まる。NDF はロックを取らない（決定 7） | `land_approved` は PR が merged なら記録だけを書き、記録があれば書かない |
| スプリントの開始と、節の更新 | 節とプランの状態 | スプリントは開始時のスナップショットだけを読む（I13） | `strategy_snapshot` は控えがあれば上書きしない。違いは `snapshot_diff` が結果に出す |
| 承認の証跡の書き手と、リリース承認ゲートの問い | PR のコメント | 書き手をケースごとに 1 つにする（`trail_writer`） | 今の head の証跡があれば問いを送らない（I12） |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_branch_strategy` | 作成の結果（`status`・`kind`・`values`・`evidence`・`questions`・`diff`・`digest`・`failed`） | 結果 JSON（標準出力の 1 行） | トークン・`gh` の認証の情報・文書の行の全文（パスと行番号だけ） |
| `measure` | 読めなかった種類と打ち切った種類 | 結果 JSON の `warnings` | 同上 |
| `load` | 止まった理由（ファイル・キーの位置・コマンド） | 呼び出し元の結果 JSON の `reason`、CLI は標準エラー | 節の値の全体 |
| `land_approved` | 承認した head・入れた head・方式・結果 | 結果 JSON と PR のコメント（人のマージの記録） | トークン |
| `land_approved` | 止まった理由（head の違い・ruleset の食い違い・確かめに落ちた理由） | 結果 JSON の `reason` と承認を取り直す依頼 | 同上 |
| `strategy_snapshot`・`write_start_snapshot` | スナップショットのハッシュと、今の節との違うキー | プランの状態と結果 JSON | 無し |
| `scan_rows`・`branch_strategy_problems`・`copy_mismatches` | 当たった行（`D<数>`・パス・行・語） | CI の標準出力 | 無し |

基準は `logging-guidelines` Skill に従う。進捗記録は工程の部品が今のまま打ち、この課題の関数は打たない。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load` | 10 系 `lib/branch_flow.py` の `read_flow`・`of_decl`、11.0 `domains/worktree/declaration.py` の `load` | `grep -nE '^def ' plugins/ndf/scripts/lib/branch_flow.py plugins/ndf-next/src/ndf/domains/worktree/declaration.py` | 使えない。旧い 4 か所を読む形で、消す対象（D11・D22） |
| `load_at_ref` | 10 系 `scripts/check-pr-base.sh` の `git show` の読み | `grep -n 'git show' scripts/check-pr-base.sh` | 使えない。シェルの中の処理で、関数として呼べない。読みの部分だけを移す |
| `validate` | 11.0 `ndf.contracts.schema_errors`、10 系 `lib/schema.py`（pydantic） | `grep -nE '^def ' plugins/ndf-next/src/ndf/contracts/__init__.py plugins/ndf/scripts/lib/schema.py` | 11.0 は `schema_errors` を使う。10 系は pydantic のモデルで書くと JSON Schema の `if`/`then` の規則を 2 か所に持つため使えず、jsonschema（11.0 と同じ版）を依存のグループに足して使う（決定 2） |
| `get` | `lib/json_pick.py` | `grep -nE '^def ' plugins/ndf/scripts/lib/json_pick.py` | 10 系は `json_pick` の pointer の解きを使う。11.0 は `jsonpointer` を使わず、節の dict を辿る数行で書く（11.0 に同じ役割の関数が無いことを `grep -rn 'def .*pointer' plugins/ndf-next/src` で確かめた） |
| `main` | `lib/json_pick.py` の CLI | 同上 | 使えない。節の検査（旧いキー・スキーマ）を通してから読む必要がある |
| `snapshot` | 10 系 `supervise_lib/decl.py` の `flow_snapshot`、`lib/branch_flow.py` の `ResolvedFlow.as_snapshot` | `grep -n 'flow_snapshot\|as_snapshot' -r plugins/ndf/scripts --include=*.py` | 使えない。4 項目だけを控える形で、節の全体に置き換える（決定 9） |
| `snapshot_diff` | 11.0 `domains/release/user_config_decide.diff` | `grep -rn 'def diff' plugins/ndf-next/src/ndf/domains/release` | 使えない。設定ディレクトリのファイルの差分で、JSON のキーの差分ではない |
| `Strategy.pr_target` | `lib/branch_flow.py` の `ResolvedFlow.doc_target`・`check_target` | 上の `grep` | 使えない。行の名前（`whole`・`selected`）で分ける形（D11） |
| `Strategy.sprint_start_ref` | 同上 | 同上 | 同上 |
| `load_moves` | `lib/legacy_names.py`・`lib/legacy_values.py` | `sed -n 1,30p plugins/ndf/scripts/lib/legacy_names.py` | 使えない。旧名を新しい名前へ渡して受け付ける仕組みで、この課題は旧いキーを読まずに止める（決定 10） |
| `find_legacy` | 同上と #3613 の旧いキーの検査 | 同上、`issues/issue-3613-design.md` | 使えない。#3613 のマージの順に依らないため（決定 10） |
| `legacy_reason` | `lib/legacy_names.py` の `refusal` | 同上 | 使えない。引数の名前の案内で、ファイルとキーの位置を持たない |
| `collect_samples` | 10 系 `project_lib/measure_branch_flow.py` の `collect_flow`・`collect_reviews`、11.0 `domains/project/measure_flow.py`、10 系 `lib/release_case.py` の `read_approvals`、`lib/ci_workflows.py` | `grep -nE '^def ' plugins/ndf/scripts/project_lib/measure_branch_flow.py plugins/ndf/scripts/lib/release_case.py` | PR の起点の判定（`sprint_start`・`pr_kind`）と ruleset の読み（`read_approvals`）は新しいファイルへ移して使う。PR の読み方を 2 か所に持たない（決定 4） |
| `count_key` | `measure_branch_flow.py` の `confidence`・`infer_field` | 同上 | 数え方を移して使う。閾値は既定の表から読む形にする |
| `doc_claims` | `project_lib/measure_repo.py`・11.0 `measure_repo.py` の文書の読み | `grep -n 'README\|CONTRIBUTING\|AGENTS' plugins/ndf/scripts/project_lib/measure_repo.py` | 使えない。文書から言語とテストを測る読みで、ブランチ名の行を拾わない |
| `pick_kind` | 無し | `grep -rn 'preset' plugins/ndf/scripts plugins/ndf-next/src --include=*.py` | 当たり無し。新しく書く |
| `measure` | 10 系 `project-decl.py` の `measure_project`、11.0 `cli.py` の `measure_project` | `grep -n 'def measure_project' -r plugins` | 使えない。リポジトリ全体の測定で、節の値と問いの形を返さない。`branch-strategy measure` は別に組む |
| `init_values` | 無し（既定の表は新設） | 同上 | 新しく書く |
| `apply_answers` | 11.0 `write.py` の `resolve_item`、10 系 `measure_branch_flow.py` の `bad_answer` | `grep -nE '^def ' plugins/ndf-next/src/ndf/domains/project/write.py` | 使えない。項目ごとの答えを宣言の項目へ入れる形で、節の pointer と `custom` の扱いを持たない |
| `plan_write` | 11.0 `write.py` の `merge_project`・`value_digest` | 同上 | `value_digest` の正規化を使う。差分は JSON Patch の生成に `jsonpatch` を使わず、節の dict の比べで作る（節のキーが固定のため） |
| `plan_remove` | 同上 | 同上 | 同上 |
| `commit_plan` | 10 系 `project-decl.py` の `_put_text`、`lib/jsonio.py` | `grep -nE '^def ' plugins/ndf/scripts/lib/jsonio.py` | 一時ファイルからの置き換えは `jsonio` を使う |
| `plan_migration` | #3613 の `ndf settings migrate` | `issues/issue-3613-design.md` | 使えない。節へ移す行を扱わない（決定 10） |
| `cmd_branch_strategy` | 今の `cmd_write`・`_write` | 上の `grep` | 使えない。旧い 4 か所を書く形（D14・D23） |
| `route_of` | `release_lib/promotion.py` の `release_selected`・`build_branch` | `grep -nE '^def ' plugins/ndf/scripts/release_lib/promotion.py` | リリースブランチの組み立て（`build_branch`）は `release_branch` の行で使うため移す。行の名前で分ける部分は使えない（D17） |
| `ruleset_mismatch` | 10 系 `lib/release_case.py` の `required_approvals`、11.0 `land.py` の `Lander.approvals`・`required` | `grep -nE 'def (required_approvals|approvals|required)' -r plugins` | 承認の数の読みは使う。merge queue と最新の取り込みの要否の読みを足す |
| `check_release_pr` | `release_lib/promotion.py` の `allowed_after` | 同上 | 使えない。行 `selected` の許す PR の判定で、方式ごとの確かめを持たない |
| `land_approved` | 10 系 `release-steps.py` の `wait_and_merge`、`release_lib/promotion.py` の `_merge_exact`、`lib/gh_rest.py` の `pr_merge(sha=)` | `grep -n 'def wait_and_merge\|def _merge_exact\|def pr_merge' -r plugins/ndf/scripts` | `pr_merge(sha=)` と `wait_and_merge` の CI の待ちを使う。head の一致の確認は `_merge_exact` の手順を移す |
| `trail_writer` | `release_lib/approval_trail.py` の `judge`・`write_trail`・`write_admin_record` | `grep -nE '^def ' plugins/ndf/scripts/release_lib/approval_trail.py` | 書く関数は使う。書き手を決める関数は無いので足す |
| `merge_queue_enabled` | 11.0 `adapters/github.py` の `_MERGE_QUEUE_STATE`、10 系 `lib/gh_graphql.py` の `graphql` | `grep -rn 'mergeQueue\|enqueuePullRequest' plugins/ndf/scripts plugins/ndf-next/src --include=*.py` | 10 系に当たり無し。`graphql` を使い、問い合わせの文は 11.0 と同じにする |
| `enqueue_pr` | 同上 | 同上 | 同上 |
| `write_start_snapshot` | 10 系の `strategy_snapshot` | 上 | 系が違うため共有できない。同じ形で書く |
| `cmd_land` | `release-steps.py` の既存のサブコマンドの組み立て | `grep -n 'add_parser' plugins/ndf/scripts/release-steps.py` | 引数の共通部（`parents=[common]`）を使う |
| `load_rows` | `scripts/check-skill-repo-assumptions.py` の例外の読み | `grep -n 'def ' scripts/check-skill-repo-assumptions.py` | 使えない。語の一致の例外で、パスと語の組を持たない |
| `scan_rows` | 同上の `compile_words`・`scan_contracts` | 同上 | 語の正規表現の組み立て（`compile_words`）は使う |
| `branch_strategy_problems` | 同上の `scan_contracts_with_defaults`・`default_problems` | 同上 | スキーマの `default` の検出は `scan_contracts_with_defaults` を使い、規則を足す |
| `copy_mismatches` | `scripts/check-ndf-contracts.py` の既存の検査 | `grep -nE '^def ' scripts/check-ndf-contracts.py` | 既存の一致の検査の組に 3 組を足す形にする。同じ関数があれば実装でそれに寄せる |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load` | 5 種の fixture がそれぞれ `Strategy` になる | 節だけがあり、旧い 3 ファイルが無い | 節が無い（I3）/ 旧いキーが 1 つ残る（I4）/ スキーマに落ちる / JSON が壊れている / 節が 2 か所（I1） |
| `load_at_ref` | 宛先のブランチの節を読む | 宛先のブランチにだけ節がある | ブランチが無い / ファイルが無い |
| `validate` | 5 種の既定が通る | 規則の境（`trunk` で base と production が同じ） | R1〜R10 の落ちる入力がそれぞれ落ちる（設計文書の「組み合わせの規則」）/ スキーマが無い / 10 系で依存が無い |
| `get` | pointer で値を引く | 値が `null`（`verify`） | 無い pointer |
| `main` | `get /branches/base` が 1 行を出す | `--ref` を渡す | 無い pointer で 2 / 節が無いと 3 |
| `snapshot` | ハッシュが節の中身だけで決まる | キーの順が違う同じ節で同じハッシュ | （副作用なし。異常なし） |
| `snapshot_diff` | 違うキーを並べる | 同じなら空 | （異常なし） |
| `Strategy.pr_target` | 5 種で宛先が設計文書の 2 つ目の表と同じ | `doc` の宛先が `sprint` のとき | 知らない種類で `ValueError` |
| `Strategy.sprint_start_ref` | `base`・`production` の 2 値 | base と production が同じ（`trunk`） | （異常なし） |
| `load_moves` | 節へ移す行と `remove` の行だけを返す | #3613 の行（同じファイルの中の改名）を含む表から除く | 表が無い / 形が違う |
| `find_legacy` | ai-plugins の今の 4 ファイルで全部の旧いキーを返す | `remove` の行のキーだけが残る | （異常なし） |
| `legacy_reason` | 10 系と 11.0 で違うコマンドを出す | 旧いキーが 1 つ | （異常なし） |
| `collect_samples` | 記録した `gh` の応答から事例を集める | PR が 0 件 | 読めない種類が `unreadable` に載る / 時間切れで打ち切る |
| `count_key` | 3 件・9 割で値と根拠 | ちょうど 3 件・ちょうど 9 割 | 2 件で `insufficient` / 8 割で `conflict` |
| `doc_claims` | 文書の宛先の行を拾う | 文書が無い | 読めない文書を飛ばす |
| `pick_kind` | develop→main と trunk の fixture で 1 つに決まる | `merge-queue`※ が `strict` と合う | 合う行が 0 / 2 つ以上で候補を返す |
| `measure` | 受け入れ条件 9・10 の fixture で問いなしに値と根拠 | 既に節があり測った値と同じなら空の差分 | 受け入れ条件 11 の fixture で `conflict`・`insufficient` / `via: gate` と ruleset の承認で `conflict` |
| `init_values` | 5 種で既定と同じ節（受け入れ条件 8・15） | 既定ブランチの名前が `main` 以外 | merge queue が分からないと問いを返す |
| `apply_answers` | 答えた pointer の根拠が `answer` | 既定と同じ値を答えると種類を変えない | 答えていない問いが残ると `ask` |
| `plan_write` | 差分とハッシュを返す | 同じ節で空の差分 | 規則に落ちる答えで `failed`・1 バイトも書かない（受け入れ条件 12） / ファイルが壊れている |
| `plan_remove` | 節を消す差分 | 節が無いと空の差分 | ファイルが壊れている |
| `commit_plan` | ハッシュが合えば書く | 2 回目は空の差分で書かない | ハッシュが違うと書かない / 書き込みの失敗でファイルが前のまま |
| `plan_migration` | ai-plugins の今の 4 ファイルを節へ移す（受け入れ条件 20） | `remove` の行のキーだけを消す | 移した後に規則に落ちる / 表に無い値で問い / 途中で止まった状態から打ち直すと同じ結果 |
| `cmd_branch_strategy` | 動詞ごとの終了コード（0・3・1） | `init` で `--kind` を省く | 引数の誤りで 2 |
| `route_of` | 3 つの取り込み方と 2 つの承認の対象 | `trunk`・`production_pr` で版上げの PR が無い | （異常なし） |
| `ruleset_mismatch` | 一致で空 | ruleset が無い（private の Free）と `strict` は一致 | `via: gate` で承認 1 / `merge-queue` でキューが無い |
| `check_release_pr` | 方式ごとの通る入力 | `merge-queue` で先端を含まないが版数が同じなら通る | `strict` で先端を含まない / `merge-queue` で版数の衝突・キューに別の版上げの PR |
| `land_approved` | `merge-queue` で `queued`・`strict` で `merged` | 人が先にマージ済みで記録を 1 回だけ書く | #3661 の形で止まる / #3572 の形（承認の外のコミット）で止まる / ruleset の食い違い / GitHub が断る / 証跡を書けない |
| `Lander.land` | 10 系と同じ入力で同じ結果 | 同じ | 同じ |
| `Lander._bypass` | `direct`・`admin: true` で真 | `direct`・`admin: false` で偽 | （スキーマが R3 で他の組み合わせを止める） |
| `trail_writer` | 表の 4 ケース | 人が先にマージした `via: gate` | （異常なし） |
| `merge_queue_enabled` | 有る / 無い | — | 読めないと None |
| `enqueue_pr` | `jump` の有無で要求の形が変わる | — | head の違いで断られる |
| `GitHub._enqueue` | 同上 | — | 同上 |
| `case_at` | `merge.admin` が真で迂回の場合 | 偽で迂回しない | ruleset が読めない |
| `cmd_land` | 結果ごとの終了コード | — | `land_approved` の失敗で 1 |
| `wait_and_merge` | 迂回を節から決める | — | 節が読めないと止まる |
| `strategy_snapshot` | 節の全体を控える | 2 回目は上書きしない | 節が読めないとスプリントを始めない / 書けない |
| `write_start_snapshot` | 同上 | 同上 | 同上 |
| `Declaration.load` | 起点と宛先を節から読む | `follow_branch` の追う先が `branches.base` | 旧いキーで `DeclarationError` |
| `resolve_base` | 節の base を出す | — | 節が無いと 3 で worktree を作らない |
| `load_rows` | D1〜D37 の行を読む | — | 形が違うと 2 |
| `scan_rows` | 語が残れば当たる | パスのファイルが無い行は通す | 読めないと 2 |
| `branch_strategy_problems` | スキーマの `default`・既定の表の ai-plugins の名前・宛先の決め打ち・条件なしの迂回で当たる | タグの形が `{plugin}--v{version}` なら当たらない | 対象のファイルが無いと 2 |
| `copy_mismatches` | 3 組が同じなら空 | — | 片方だけを変えると当たる / ファイルが無いと 2 |
