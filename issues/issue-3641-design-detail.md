# #3641 の詳細設計: 今の NDF に GitHub flow を入れ、ai-plugins を移す

設計文書 [issue-3641-design.md](issue-3641-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。
決定の番号（決定 1〜15）は [issue-3641-design-decisions.md](issue-3641-design-decisions.md) を指す。不変条件の番号（I1〜I17）は設計文書の「ドメインモデル」の表を指す。マージの方式 `serial` の振る舞いは [issue-3641-design-serial.md](issue-3641-design-serial.md) にあり、この文書はそのファイルと関数を書く。

この文書で使う語:

| 語 | 意味 |
| --- | --- |
| ブランチ戦略 `trunk` | ブランチ戦略の一覧（`lib/branch_flow.py` の `PROMOTIONS`）に足す GitHub flow |
| 本番の PR | ブランチ戦略 `trunk` では、スプリントブランチから main へ出すスプリントの PR（版上げの PR は含まない） |
| 版上げの PR | CD がマージの後に main の HEAD から作る `release/v<バージョン>` の PR |
| 承認済みのコミット | リリース承認ゲートで承認した本番の PR の head のコミット。承認資料の承認の記録（`lib/approved_commit.py` の `recorded_sha`）が持つ |
| 結果 JSON | NDF のサブコマンドが標準出力の最後の行に出す `{"tool", "status", "summary", "items", "metrics", "outputs"}` の形（`lib/step_result.py` の `result`） |
| 一時マージコミット | `serial` で、本番ブランチの HEAD を第 1 親、承認済みのコミットを第 2 親にして作り、ブランチ `ndf-serial/<本番ブランチ>` へ push するコミット。そのブランチがロックを兼ねる |

## ファイル配置

### パッケージ・モジュール構成

触るファイルだけを載せる。`*` は新設である。

```text
plugins/ndf/
├── contracts/
│   ├── index.md
│   └── entrypoints/plugins__ndf__scripts__trunk-steps.py.json *
├── skills/
│   ├── worktree/schemas/worktree.schema.json
│   └── development-workflow/schemas/project.schema.json
└── scripts/
    ├── trunk-steps.py *
    ├── lib/
    │   ├── branch_flow.py
    │   ├── diff_kinds.py
    │   └── project_decl.py
    ├── project_lib/model.py
    ├── release_lib/
    │   ├── merge_method.py *
    │   ├── method_select.py *
    │   ├── serial_merge.py *
    │   └── trunk_cd.py *
    ├── merged_lib/merge.py
    ├── supervise_lib/
    │   ├── release_flow.py
    │   ├── release_templates.py
    │   ├── sprint.py
    │   ├── sprint_routes.py
    │   └── sprint_waves.py
    └── tests/
        ├── test_branch_flow.py
        ├── test_sprint_branch_flow.py
        ├── test_merge_method.py *
        ├── test_method_select.py *
        ├── test_serial_merge.py *
        └── test_trunk_cd.py *
scripts/
├── check-pr-base.sh
├── ci-heavy-skip.py
├── script-structure-allow/
│   ├── plugins__ndf__scripts__lib__branch_flow.py--lines.json
│   └── plugins__ndf__scripts__merged_lib__merge.py--lines.json *
└── tests/test_pr_base_guard.py
.github/workflows/
├── release-cd.yml
└── （必須のチェックを出す 8 本）
```

### 依存の向き

**依存は一方向で、循環が無い。** 下の層から順に次のとおり並ぶ。

1. 最下層: ブランチ戦略の一覧（`lib/branch_flow.py`）・差分の分類（`lib/diff_kinds.py`）・設定の読み（`lib/project_decl.py`）
2. 方式の判定（`release_lib/method_select.py`）と `serial` のロックと一時マージコミット（`release_lib/serial_merge.py`）。どちらも `release_lib/` のほかの新設モジュールを読まない
3. CD のバージョンアップ（`release_lib/trunk_cd.py`。`serial` の版上げの PR のマージに `serial_merge` を呼ぶ）
4. main へのマージ（`release_lib/merge_method.py`。版上げの PR の見分けに `trunk_cd`、方式の判定に `method_select`、`serial` に `serial_merge` を呼ぶ）
5. 最上層: エントリポイント（`trunk-steps.py`）とマージのコンポーネント（`merged_lib/merge.py`）

`trunk_cd` は `merge_method` を読まない。`serial_merge` はマージの API を実行せず、呼び手が渡したマージの関数をロックの中で呼ぶ。そのため main へのマージの書き手は `merge_method`（本番の PR）と `trunk_cd`（版上げの PR）のままである。プランの組み立て（`supervise_lib/`）はサブコマンドをコマンドの文字列として作成するだけで、`release_lib/` の 2 つの新設モジュールを import しない。`supervise_lib/sprint_routes.py` は今の依存（`release_templates` → `release_flow`）の向きのまま `release_flow` を呼ぶ。

### ファイルごとの役割

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | ブランチ戦略 `trunk`・マージ先の値 `release`・ブランチ戦略が本番ブランチへ直接入れるかの判定・ベースブランチと本番ブランチの一致の検査（I1）を持つ | 無し |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__branch_flow.py--lines.json` | 変更 | 行数の例外の値を、ブランチ戦略 `trunk` を足した後の行数へ上げ、理由に #3641 を書き足す | 無し |
| `plugins/ndf/skills/worktree/schemas/worktree.schema.json` | 変更 | `branch_flow.schema_fragment()` から作り直し、`promotion` に `trunk`、`integrate` に `release` が載る | `lib/branch_flow.py` |
| `plugins/ndf/scripts/project_lib/model.py` | 変更 | 本番の PR のマージの方式（`ProductionMerge`）に `method` を足し、`admin` を省けるようにする | 無し |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | `project_lib/model.py` から作り直し、`production_merge.method` が載る | `project_lib/model.py` |
| `plugins/ndf/scripts/lib/project_decl.py` | 変更 | マージの方式の値を読む関数を足す | `project_lib/model.py`（今の読み方のまま） |
| `plugins/ndf/scripts/lib/diff_kinds.py` | 変更 | `scripts/ci-heavy-skip.py` にある「差分が版数と説明だけか」の判定を移し、プラグインの中から呼べる正本にする | 無し |
| `scripts/ci-heavy-skip.py` | 変更 | 移した判定を `diff_kinds` から読む。判定の振る舞いは変えない | `lib/diff_kinds.py` |
| `plugins/ndf/scripts/merged_lib/required_checks.py` | 変更 | 本番ブランチの ruleset の読み（merge queue の規則の有無・`strict_required_status_checks_policy`・`required_approving_review_count`）を 1 つの関数に足し、`merge_method` と `trunk_cd` が同じ読みを使う | 今のまま |
| `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | main のバージョンの集約の書き手。範囲・バージョン種別・版上げの PR の作成と取り込み・CD の確かめ・版上げの PR の見分け（I3）・版上げの PR の承認の数の確かめ（I14）・`serial` での版上げの PR のマージ（`confirm`） | `lib/diff_kinds.py`・`lib/gh_call.py`・`merged_lib/required_checks.py`・`release_lib/serial_merge.py`・`release-steps.py`（サブプロセス） |
| `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | main へのマージの集約のただ 1 つの書き手。再開・検証・方式の判定と ruleset の検証・追加・マージ・待ち（I4・I5・I6・I11・I13・I14・I15・I17） | `release_lib/method_select.py`・`release_lib/serial_merge.py`・`release_lib/trunk_cd.py`・`merged_lib/required_checks.py`・`lib/branch_flow.py`・`lib/project_decl.py`・`lib/gh_call.py` |
| `plugins/ndf/scripts/release_lib/method_select.py` | 新設 | 所有者・公開範囲・プランを読み、方式を選ぶ表から使える方式を返す（I17）。読めなければ「不明」 | `lib/gh_call.py` |
| `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `serial` のロック（取る・作り直す・返す・引き取る。I16）・一時マージコミットの作成・`ci.required_checks` の待ち・マージの直前の HEAD の読み直し（I15）。マージは呼び手の関数で行う | `lib/gh_call.py`・`lib/project_decl.py`・`lib/waits.py`・`merged_lib/required_checks.py` |
| `plugins/ndf/scripts/trunk-steps.py` | 新設 | サブコマンド `facts`・`land`・`confirm`・`cd`・`pr-check` の引数を読み、モジュールを呼んで結果 JSON にする | `release_lib/merge_method.py`・`release_lib/trunk_cd.py`・`lib/approved_commit.py`・`lib/step_result.py` |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__trunk-steps.py.json` | 新設 | `trunk-steps.py` の入出力の契約（サブコマンド・引数・終了コード・読む設定） | 無し |
| `plugins/ndf/contracts/index.md` | 変更 | 契約の一覧に `trunk-steps.py` の行を足す | 無し |
| `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | ブランチ戦略 `trunk` で本番ブランチ宛てのとき、承認ゲートの判定とマージを `merge_method` へ渡す | `release_lib/merge_method.py` |
| `scripts/script-structure-allow/plugins__ndf__scripts__merged_lib__merge.py--lines.json` | 新設 | 呼び出しの 2 行で 500 行を超える分の例外。理由に #3641 と「処理は `merge_method.py` に置いた」を書く | 無し |
| `plugins/ndf/scripts/supervise_lib/release_flow.py` | 変更 | ブランチ戦略 `trunk` の承認資料のプラン・リリースのワークフロー・検査のプランのマージの外し・進め方の断りを持つ | `supervise_lib/decl.py`・`supervise_lib/paths.py`・`supervise_lib/procedures.py`・`supervise_lib/release_cd.py`・`supervise_lib/verify_steps.py` |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | 承認の記録を書くステップの次のステップを引数で受ける | 今のまま |
| `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 変更 | ブランチ戦略 `trunk` ではリリースのテンプレート（開発版 → 本番）の経路を作成せず、ブランチ戦略 `trunk` のステージを返す | `supervise_lib/release_flow.py` |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | 並列のレーンがまとめたリリースへリクエストを送らない条件と、進め方の断りの呼び出しを替える | `supervise_lib/release_flow.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | ブランチ戦略 `trunk` の検査のプランからマージのステップを外す | `supervise_lib/release_flow.py` |
| `scripts/check-pr-base.sh` | 変更 | マージ先の設定が `trunk` なら head の名前を問わず通し、`trunk-steps.py pr-check` を実行する | `plugins/ndf/scripts/trunk-steps.py` |
| `.github/workflows/release-cd.yml` | 変更 | main への push で `trunk-steps.py cd` を実行し、シークレットと権限を渡す | `plugins/ndf/scripts/trunk-steps.py` |
| `.github/workflows/runtime-plugin-validate.yml` | 変更 | `on:` に `merge_group` を足す（決定 9） | 無し |
| `.github/workflows/glossary.yml` | 変更 | `on:` に `merge_group` を足す（決定 9） | 無し |
| `.github/workflows/pr-base-guard.yml` | 変更 | `on:` に `merge_group` を足す。PR の本文とマージ先を読むジョブは、`merge_group` の起動では判定を行わずに成功で終わる（決定 9） | 無し |
| `.github/workflows/lint.yml` | 変更 | `on:` に `merge_group` を足す（決定 9） | 無し |
| `.github/workflows/pr-body-decisions.yml` | 変更 | `on:` に `merge_group` を足す。PR の本文とマージ先を読むジョブは、`merge_group` の起動では判定を行わずに成功で終わる（決定 9） | 無し |
| `.github/workflows/pytest.yml` | 変更 | `on:` に `merge_group` を足す（決定 9） | 無し |
| `.github/workflows/script-structure.yml` | 変更 | `on:` に `merge_group` を足す（決定 9） | 無し |
| `.github/workflows/runtime-plugin-smoke.yml` | 変更 | `on:` に `merge_group` を足す（決定 9） | 無し |
| `plugins/ndf/scripts/tests/test_branch_flow.py` | 変更 | ブランチ戦略 `trunk` の表の値と I1 のテストを足す | `lib/branch_flow.py` |
| `plugins/ndf/scripts/tests/test_sprint_branch_flow.py` | 変更 | ブランチ戦略 `trunk` のステージとリリースのワークフローの並び（I7・I8・I12）のテストを足す | `supervise_lib/` |
| `plugins/ndf/scripts/tests/test_merge_method.py` | 新設 | `merge_method` の単体テスト（偽の GitHub の応答） | `release_lib/merge_method.py` |
| `plugins/ndf/scripts/tests/test_method_select.py` | 新設 | 方式を選ぶ表の各行と「不明」の単体テスト（偽の GitHub の応答） | `release_lib/method_select.py` |
| `plugins/ndf/scripts/tests/test_serial_merge.py` | 新設 | ロックの 4 操作・期限・作り直し・待ちの単体テスト（一時の bare リポジトリと偽のチェックの応答） | `release_lib/serial_merge.py` |
| `plugins/ndf/scripts/tests/test_trunk_cd.py` | 新設 | `trunk_cd` の単体テスト（一時のリポジトリと偽の GitHub の応答） | `release_lib/trunk_cd.py` |
| `scripts/tests/test_pr_base_guard.py` | 変更 | マージ先の設定が `trunk` のときの判定のテストを足す | `scripts/check-pr-base.sh` |

## クラスと関数の構造

型の関係は設計文書の「構造」のセクション（ブランチ戦略の一覧の行）を指し、ここでは描き直さない。新しい型は値オブジェクトだけで、継承を持たない。

| 格納場所 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/branch_flow.py` | `Promotion` | `integrates_to_production`（新設） |
| `plugins/ndf/scripts/lib/branch_flow.py` | `BranchFlow` | `resolve`（変更） |
| `plugins/ndf/scripts/lib/branch_flow.py` | モジュール | `_resolved`（新設）・`from_snapshot`（変更）・`PROMOTIONS` と `INTEGRATE_POINTS` の値（変更） |
| `plugins/ndf/scripts/project_lib/model.py` | `ProductionMerge` | 欄 `method` を足し、`admin` を省けるようにする（変更） |
| `plugins/ndf/scripts/lib/project_decl.py` | モジュール | `production_method`（新設） |
| `plugins/ndf/scripts/merged_lib/required_checks.py` | モジュール | `branch_rules`（新設） |
| `plugins/ndf/scripts/lib/diff_kinds.py` | モジュール | `manifest_lines_only`・`changed_kinds`・`version_only`（`scripts/ci-heavy-skip.py` から移す。`kinds_of` は `changed_kinds` へ名前を変える）、`MANIFEST_RE`・`MANIFEST_LINE_RE`（移す） |
| `scripts/ci-heavy-skip.py` | モジュール | `version_only` と `kinds_of` の呼び出しを `diff_kinds` へ替える（関数の定義を消す） |
| `plugins/ndf/scripts/release_lib/trunk_cd.py` | `CdOutcome`・`ConfirmOutcome`・`CdStop`（新設） | — |
| `plugins/ndf/scripts/release_lib/trunk_cd.py` | モジュール | `is_bump_pr`・`pr_check`・`last_bump_commit`・`changed_plugins`・`bump_level`・`planned_version`・`open_bump_pr`・`create_bump_pr`・`update_bump_pr`・`land_bump_serial`・`run_cd`・`confirm`（すべて新設） |
| `plugins/ndf/scripts/release_lib/merge_method.py` | `PrState`・`LandResult`・`MergeStop`（新設） | — |
| `plugins/ndf/scripts/release_lib/merge_method.py` | モジュール | `applies`・`read_pr`・`record_user_merge`・`check_method`・`ungated_changes`・`ungated_allowed`・`enqueue`・`merge_strict`・`merge_serial`・`wait_landed`・`land`・`merge_from_watch`（すべて新設） |
| `plugins/ndf/scripts/release_lib/method_select.py` | `RepoFacts`（新設） | — |
| `plugins/ndf/scripts/release_lib/method_select.py` | モジュール | `repo_facts`・`usable_methods`（すべて新設） |
| `plugins/ndf/scripts/release_lib/serial_merge.py` | `Lock`・`SerialStop`（新設） | — |
| `plugins/ndf/scripts/release_lib/serial_merge.py` | モジュール | `read_lock`・`build_staging`・`push_lock`・`acquire`・`release`・`wait_staging`・`run_serial`（すべて新設） |
| `plugins/ndf/scripts/trunk-steps.py` | モジュール | `cmd_facts`・`cmd_land`・`cmd_confirm`・`cmd_cd`・`cmd_pr_check`・`build_parser`・`main`（すべて新設） |
| `plugins/ndf/scripts/merged_lib/merge.py` | モジュール | `merge_when_green`（変更。呼び出しの 2 か所だけ） |
| `plugins/ndf/scripts/supervise_lib/release_flow.py` | モジュール | `pace_flow_refusal`（`fast_flow_refusal` を置き換える）・`trunk_check_steps`・`plan_trunk_material`・`plan_release_trunk`（新設） |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | モジュール | `approve_step`（変更。引数 `next_id` を足す） |
| `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | モジュール | `has_release_template`（変更）・`route_waves`（変更）・`trunk_waves`（新設） |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | モジュール | `release_lane`（変更。条件 1 行）・`pace_refusal`（変更。呼び出し 1 行） |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | モジュール | `plan_sprint_check`（変更。2 行） |

`release_lane`・`pace_refusal`・`plan_sprint_check`・`merge_when_green`・`approve_step`・`has_release_template` は、引数か分岐を 1 つ足すだけである。下の「関数の仕様」に変える点だけを書く。

## 関数の仕様

| 関数 | 格納場所 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Promotion.integrates_to_production` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `@property -> bool` | ブランチ戦略の許すマージ先がすべて本番ブランチへ入れる時点（`INTEGRATE_POINTS` の値が `production`）かを返す | 無し | ブランチ戦略 `trunk` で真、`whole`・`selected` で偽。ブランチ戦略の名前で分岐しない | 無し |
| `_resolved` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `(flow: BranchFlow, start: str \| None, base: str \| None, production: str \| None) -> ResolvedFlow` | 解いたブランチ名で `ResolvedFlow` を作り、本番ブランチへ直接入れるブランチ戦略でベースと本番が違えば止める（I1） | `flow` は `parse_flow` を通った値 | 返した値のブランチ戦略が `integrates_to_production` なら `base_branch == production_branch`（どちらかが `None` のときは比べない） | 無し |
| `BranchFlow.resolve` | `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `(base: str \| None, production: str \| None) -> ResolvedFlow` | 分岐元を解き、`_resolved` で作る（I1 を検査に足す） | 今のまま | `_resolved` の事後条件 | 無し |
| `from_snapshot` | `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `(snap: dict \| None, base: str \| None, production: str \| None) -> ResolvedFlow` | 目録のスナップショットを `_resolved` で読む（I1 を検査に足す） | 今のまま | `_resolved` の事後条件 | 無し |
| `production_method` | `plugins/ndf/scripts/lib/project_decl.py` | 新設 | `(root) -> str \| None` | `.ndf/project.json` の `production_merge.method` を返す | 無し | 値が `merge-queue`・`strict`・`serial` のどれかなら値、キーが無い・型が違う・読めないなら `None`（既定の方式で代用しない。代用するかは呼び手が決める） | ファイルの読み |
| `version_only` | `plugins/ndf/scripts/lib/diff_kinds.py` | 変更 | `(root: str, a: str, b: str, behavior_as_code: bool = False) -> tuple[bool, str]` | `a..b` の差分が版数と説明だけかを返す（`scripts/ci-heavy-skip.py` から格納場所だけを移す） | `a`・`b` は `root` で解けるコミット | 判定と省かない理由が移す前と同じ | 外部コマンド（`git diff`） |
| `branch_rules` | `plugins/ndf/scripts/merged_lib/required_checks.py` | 新設 | `(root, branch: str) -> dict` | 本番ブランチの ruleset から、merge queue の規則の有無・`strict_required_status_checks_policy`・求める承認の数を読む（I6・I14 の正本） | 無し | `{merge_queue: bool, strict: bool, approvals: int}`。複数の規則があれば、真の値と承認の数の最大を採る。読めなければ `None` を返す（承認が要らないとみなさない。呼び手が止める） | ネットワーク（`GET /repos/{o}/{r}/rules/branches/{branch}`） |
| `is_bump_pr` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, base: str, head: str, head_ref: str) -> tuple[bool, str]` | 版数を変えてよい PR（版上げの PR）かを返す（I3 の例外の唯一の判定） | `base`・`head` は `root` で解けるコミット | head のブランチが `release/v` で始まり、かつ `version_only(root, base, head)` が真のときだけ真。偽なら理由を返す | 外部コマンド（`git diff`） |
| `pr_check` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, base: str, head: str, head_ref: str) -> tuple[int, str]` | 本番ブランチ宛ての PR がバージョンの格納場所を変えていないかを判定する（I3） | 同上 | `plugins/*/.claude-plugin/plugin.json` の `version` の行が `base..head` で変わらなければ 0、変わっていて `is_bump_pr` が偽なら 1 と理由「バージョンは CD がマージの後に上げる」 | 外部コマンド（`git diff`） |
| `last_bump_commit` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, plugin: str, tip: str) -> str` | 範囲の始点（`tip` から辿れる、配るプラグインのバージョンを最後に書いたコミット）を返す | `tip` は解けるコミット | `git log -1 --format=%H -G '"version"' <tip> -- plugins/<plugin>/.claude-plugin/plugin.json` の結果。マージコミットではなく版上げのコミットを指す | 外部コマンド（`git log`） |
| `changed_plugins` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, a: str, b: str) -> list[str]` | 範囲 `a..b` で中身が変わった配るプラグインの名前を返す | 同上 | `.claude-plugin/marketplace.json` に載るプラグインのうち、`plugins/<名前>/` の下のファイルが変わったものの名前を、`marketplace.json` の並びの順で返す | 外部コマンド（`git diff --name-only`）・ファイルの読み |
| `bump_level` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, a: str, b: str) -> str` | 範囲にマージされた PR のラベルからバージョン種別を決める | 同上 | `release:major` > `release:minor` > `release:patch` の最大。ラベルの無い PR は `patch`。PR が 1 本も無ければ `patch` | 外部コマンド（`git log --first-parent --merges`）・ネットワーク（`gh pr view --json labels`） |
| `planned_version` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, plugin: str, level: str, ref: str) -> str` | `ref` の版にバージョン種別を当てた予定のバージョンを返す（承認資料と版上げの PR が使う） | `ref` に `plugin` の `plugin.json` がある | `semver` の `bump_<level>` の結果 | ファイルの読み（`git show`） |
| `open_bump_pr` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, production: str) -> dict \| None` | 開いた版上げの PR（head が `release/v` で始まり、マージ先が本番ブランチ）を返す | 無し | 最も番号の小さい 1 本の `{number, headRefName, headRefOid, reviewDecision}`、無ければ `None` | ネットワーク（`gh pr list`） |
| `create_bump_pr` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, tip: str, plugins: dict[str, str], range_: tuple[str, str], token: str) -> int` | main の HEAD からバージョンアップのブランチを作り、バージョンアップと CHANGELOG のセクションのコミットを足して PR を作り、自動マージをリクエストする（`serial` ではリクエストしない） | `token` が空でない。`tip` は本番ブランチの HEAD。`plugins` は名前 → 新しいバージョン | `release/v<配るプラグインのバージョン>` が origin にあり、版数と説明と CHANGELOG だけを変える PR が開いていて、自動マージか merge queue への追加がリクエストされている。PR の番号を返す。リクエストの後に `branch_rules` の承認の数が 1 以上なら `CdStop("precondition")` を投げ、理由に求める承認の数と PR の番号を持つ（I14） | 外部コマンド（`release-steps.py bump`・`changelog` のサブプロセス、`git commit`・`git push`）・ネットワーク（`gh pr create`・`gh pr merge --merge --match-head-commit`・`branch_rules`） |
| `update_bump_pr` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, pr: dict, token: str) -> bool` | `strict` で、開いた版上げの PR が main の HEAD を含まなければ main を取り込み、新しい head で自動マージを再リクエストする | `pr` は `open_bump_pr` の値。`token` が空でない | 取り込んだら真、取り込む必要が無ければ偽。真のとき PR の head は main の HEAD を祖先に持ち、自動マージはその head でリクエストされている。再リクエストした後に `branch_rules` の承認の数が 1 以上で PR の `reviewDecision` が `APPROVED` でなければ `CdStop("precondition")`（I14。`create_bump_pr` と同じ理由） | ネットワーク（`GET compare`・`PUT update-branch`・`gh pr view`・`gh pr merge`・`branch_rules`） |
| `run_cd` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, revision: str, method: str \| None, token: str \| None) -> CdOutcome` | CD の 1 回の実行（設計文書の「処理の流れ」の手順 1〜5）を順に行う | `revision` は本番ブランチへの push の HEAD。ブランチ戦略が `trunk` | タグが足りなければ作られている。版上げの PR は開いたものが高々 1 本（I9）。開いた版上げの PR を見つけて何もしないときも、`branch_rules` の承認の数が 1 以上で PR の `reviewDecision` が `APPROVED` でなければ `CdStop("precondition")` で止まり、注釈に求める承認の数と PR の番号を書く（I14）。結果の `cause` と `summary` が止まった理由を持つ | 外部コマンド（`release-steps.py publish` のサブプロセス）・ネットワーク（`branch_rules`）・ほかは `create_bump_pr`・`update_bump_pr` と同じ |
| `confirm` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, merge_commit: str, budget: int) -> ConfirmOutcome` | マージコミットを含む main の HEAD にタグと GitHub Release がそろうのを待つ。`serial` では開いた版上げの PR を `land_bump_serial` で入れてから待つ | `merge_commit` は main に入ったコミット | そろえば `ok` と版、CD が変化なしで終われば `ok` と空の版、CD の失敗なら `cd_failed`、上限を超えれば `ci_pending` | ネットワーク（`gh run list`・`gh run view`・`gh api` のタグと Release の照会） |
| `applies` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, base_ref: str) -> bool` | この PR のマージを `merge_method` が持つか（ブランチ戦略が `trunk` で、マージ先が本番ブランチ）を返す | 無し | 宣言が読めないときは偽（今のマージへ渡す。設定の不備は `land` と `check_method` が止める） | ファイルの読み |
| `read_pr` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, n: int) -> PrState` | PR の状態を GraphQL 1 回で読む | 無し | `PrState` の欄がすべて埋まる | ネットワーク（`gh api graphql`） |
| `record_user_merge` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, pr: PrState, approved: str \| None, method: str \| None) -> bool` | 人が GitHub でマージした PR に、人のマージの記録をコメントで 1 回だけ書く（I11） | `pr.merged` が真 | マーカー（`MERGE_MARKER`）のあるコメントが PR に 1 本ある。書いたら真、既にあれば偽 | ネットワーク（`gh pr view --json comments`・`gh pr comment`） |
| `check_method` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, method: str \| None, branch: str, pr: PrState) -> None` | 設定の方式が表で使えるか（I17）、本番ブランチの ruleset とレビューの承認を検証する（I6・I14） | 無し | 戻れば、方式が `METHODS` にあり、`usable_methods` が使えるとした（または不明）、ruleset と一致し、要るレビューの承認がある。`serial` は merge queue の規則が無く `strict` が偽（ruleset が無いことを含む）を一致とし、`ci.required_checks` が空でないことを求める。ruleset は `branch_rules` で読む | ネットワーク（`repo_facts`・`branch_rules`・GraphQL `mergeQueue(branch)`） |
| `ungated_changes` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, pr: PrState) -> list[str]` | 承認ゲートを通らずに入れてはいけない変化（配るプラグインの中身と、利用者の設定 `.ndf/` の下・指示書）を返す（I5・C7） | `pr.base_oid`・`pr.head_oid` が解ける | 指示書は、どの深さでもファイル名が `AGENTS.md`・`CLAUDE.md` のパスである（Claude Code と Codex は下位のディレクトリの指示書も読むため）。先に差分にある設定と指示書のパスを集め、版上げの PR（`trunk_cd.is_bump_pr` が真）でもそれを返す。版上げの PR で設定と指示書の変化が無ければ空。それ以外の PR は、設定と指示書のパスに `trunk_cd.changed_plugins(merge-base, head)` のプラグインを足して返す | 外部コマンド（`git fetch`・`git merge-base`・`git diff`） |
| `ungated_allowed` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, info: dict) -> bool` | 承認ゲートを通らないマージで、ブランチ戦略 `trunk` の本番ブランチ宛ての PR を通してよいかを判定する（I5） | `info` はマージのコンポーネントが最初に読んだ PR の情報 | `applies` が偽なら偽（今の承認ゲートの判定へ戻す）。真のとき、`ungated_changes` が空なら真、空でなければ `MergeStop` | 外部コマンドとネットワーク（`ungated_changes`） |
| `enqueue` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, pr: PrState, head: str) -> None` | 承認済みのコミットを `expectedHeadOid` に渡してキューへ入れ、入ったことを PR の状態で確かめる（決定 6） | `check_method` を通った | 戻れば PR はキューにある。入っていなければ自動マージを外して `MergeStop("not_enqueued")` | ネットワーク（GraphQL `enqueuePullRequest`・`disablePullRequestAutoMerge`・`read_pr`） |
| `merge_strict` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, pr: PrState, head: str) -> None` | head が本番ブランチの HEAD を祖先に持つかを確かめ、承認済みのコミットを `sha` に渡して迂回せずにマージする | `check_method` を通った | 戻れば GitHub がマージを受けた。含まなければ `MergeStop("behind")`、head の不一致（409）なら `MergeStop("head_moved")` | ネットワーク（`GET compare`・`PUT /pulls/{n}/merge`） |
| `merge_serial` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, pr: PrState, head: str, budget: int) -> str` | `serial_merge.run_serial` に、承認済みのコミットを `sha` に渡して迂回せずにマージする関数を渡す（I15） | `check_method` を通った。`ci.required_checks` が空でない | マージコミットを返す。`SerialStop` を同じ原因の `MergeStop` にする。マージの API の 409 は `MergeStop("head_moved")` | ネットワーク（`PUT /pulls/{n}/merge`）・`run_serial` の副作用 |
| `repo_facts` | `plugins/ndf/scripts/release_lib/method_select.py` | 新設 | `(root) -> RepoFacts` | 本番の PR のリポジトリの所有者の種類・公開範囲・プランを読む | 無し | `visibility`・`owner_type` は読めた値か `None`。`plan` は public では読まず `None`、private・internal では Organization なら `GET /orgs/{org}` の `plan.name`、個人アカウントなら `GET /user` の `login` が所有者のときだけ `plan.name`。読めなければ `None` と理由（`reason`） | ネットワーク（`GET /repos/{o}/{r}`・`GET /orgs/{org}`・`GET /user`） |
| `usable_methods` | `plugins/ndf/scripts/release_lib/method_select.py` | 新設 | `(facts: RepoFacts) -> tuple[str, ...] \| None` | 方式を選ぶ表から使える方式を返す（I17） | 無し | 表の行に当たれば使える方式の並び（`serial` を必ず含む）。公開範囲・所有者が無い、private・internal で `plan` が `free`・`pro`・`team` でないときは `None`（不明）。internal は private と同じ行 | 無し |
| `read_lock` | `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `(root, branch: str) -> Lock \| None` | `ndf-serial/<branch>` の HEAD とトレーラーを読む | 無し | ブランチが無ければ `None`。あれば `Lock(sha, pr, approved, holder, deadline)`。トレーラーが読めなければ `deadline` が `None` | 外部コマンド（`git ls-remote`・`git fetch`・`git log -1 --format=%(trailers)`） |
| `build_staging` | `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `(root, base_oid: str, approved: str, pr: int, holder: str, deadline: datetime) -> str` | 一時の worktree で `base_oid` に `git merge --no-ff <approved>` を実行し、一時マージコミットを作る | 2 つのコミットが手元にある | 第 1 親が `base_oid`、第 2 親が `approved`、メッセージの 1 行目が `ndf-serial: #<pr>` でトレーラー 4 つを持つコミットの SHA。衝突すれば `SerialStop("conflict")` | 外部コマンド（`git worktree add`・`git merge`・`git worktree remove`） |
| `push_lock` | `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `(root, branch: str, new: str \| None, expect: str) -> bool` | `--force-with-lease=refs/heads/ndf-serial/<branch>:<expect>` で push する（`new` が `None` なら削除）（I16。STD14） | `expect` は空（無いこと）か 40 桁の SHA | 受け手が受ければ真、期待する値の違いで断れば偽。ほかの失敗は `SerialStop("precondition")` | 外部コマンド（`git push`） |
| `acquire` | `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `(root, branch: str, staging: str, end: float) -> Lock` | ロックを取る。ほかの持ち主がいれば期限を読み、過ぎていれば引き取り、過ぎていなければ `end` まで待つ | `staging` は `build_staging` の値 | 戻れば `ndf-serial/<branch>` が `staging`。`end` を超えるか、トレーラーの無いブランチなら `SerialStop("lock_busy")`（持ち主の PR の番号と期限を `summary` に） | `push_lock`・`read_lock` の副作用。待ちは `lib/waits.py` |
| `release` | `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `(root, branch: str, mine: str) -> str` | 期待する値 `mine` でロックを消す | 無し | `released` か、既に引き取られていれば `taken_over`（何もしない） | `push_lock` の副作用 |
| `wait_staging` | `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `(root, sha: str, checks: list[str], end: float) -> None` | 一時マージコミットに `checks` の名前のチェックがそろって終わるのを待つ | `checks` が空でない | 戻ればすべて成功。1 つでも失敗なら `SerialStop("staging_failed")`（失敗したチェックの名前と SHA）。`end` を超えれば `SerialStop("ci_pending")`（チェックが 1 つも付いていなければ `on.push` の確かめを `summary` に） | ネットワーク（`GET /commits/{sha}/check-runs`）。待ちは `lib/waits.py` |
| `run_serial` | `plugins/ndf/scripts/release_lib/serial_merge.py` | 新設 | `(root, branch: str, pr: int, approved: str, budget: int, merge: Callable[[], str]) -> tuple[str, list[dict]]` | ロック → 一時マージコミットのチェック → HEAD の読み直し（動けば作り直し）→ 期限の確かめ → `merge` → 第 1 親と tree の比べ → ロックの返却を順に行う（設計の `serial` の状態の遷移） | `ci.required_checks` が空でない | マージコミットと `items` を返す。マージは HEAD が一時マージコミットの第 1 親と同じで、期限の前のときだけ（I15・I16）。比べが違えば `items` に `untested_combination`。止まるとき（`SerialStop`）も、マージした後も、ロックを返してから戻る | 上の関数と `merge` の副作用の和 |
| `land_bump_serial` | `plugins/ndf/scripts/release_lib/trunk_cd.py` | 新設 | `(root, pr: dict, budget: int) -> str` | `serial` で、`confirm` が版上げの PR を `run_serial` で入れる | `pr` は `open_bump_pr` の値。`is_bump_pr` が真で、設定と指示書の変化が無い（I5） | マージコミットを返す。`pr` の `headRefOid` を承認済みのコミットの代わりに `sha` に渡す。`SerialStop` は `ConfirmOutcome` の同じ原因になる | `run_serial` の副作用・ネットワーク（`PUT /pulls/{n}/merge`） |
| `wait_landed` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, n: int, budget: int) -> PrState` | マージされるか、キューから外れるか、上限を超えるまで PR の状態を読む | `enqueue` か `merge_strict` の後 | 戻れば `merged` が真。外れれば `MergeStop("queue_removed")`、上限で `MergeStop("ci_pending")` | ネットワーク（`read_pr` の繰り返し。間隔は `lib/waits.py`） |
| `land` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, n: int, approved: str, gated: bool, budget: int) -> LandResult` | 本番の PR を、再開・検証・方式の検証・追加かマージ・待ちの順に通す（設計文書の `land` の状態の遷移。`serial` は `merge_serial`） | `approved` は 40 桁の SHA | `ok` のとき main は承認済みのコミットを含むマージコミットを持つ。`stopped` のとき、承認ゲートを通っていない head は main へ入っていない | 上の関数の副作用の和 |
| `merge_from_watch` | `plugins/ndf/scripts/release_lib/merge_method.py` | 新設 | `(root, a, n: int, head: str, items: list[dict]) -> None` | マージのコンポーネントが緑を確かめた後のマージを、方式に従って実行する（`--admin` を実行しない） | `applies` が真。`head` は緑を確かめた head | `items` にマージの項目（`result: merged`・`method`・`head`・`admin: false`）が足される | `land` と同じ（`gated` は `a.gate_approved` の有無） |
| `merge_when_green` | `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | 今のまま | 承認ゲートの判定の前に `merge_method.ungated_allowed` を、マージの前に `merge_method.applies` を見て、真なら `merge_from_watch` へ渡す | 今のまま | `applies` が偽のとき、振る舞いが今と同じ（I10） | 今のまま |
| `cmd_facts` | `plugins/ndf/scripts/trunk-steps.py` | 新設 | `(a: argparse.Namespace) -> NoReturn` | 本番の PR の承認資料を、範囲「本番ブランチの HEAD..PR の head」と予定のバージョンで作成する | ブランチ戦略が `trunk`。`--pr` と `--approval` がある | 承認資料に承認済みのコミットの行（PR の head）と予定のバージョンが載る。結果 JSON の `metrics.approved_sha` が PR の head | 外部コマンド（`release-steps.py approval-facts`・`notes` のサブプロセス）・ネットワーク |
| `cmd_land` | `plugins/ndf/scripts/trunk-steps.py` | 新設 | `(a) -> NoReturn` | 承認済みのコミット（`--approved` か、`--approval` の承認の記録）を読み、`merge_method.land` を呼んで結果 JSON にする | ブランチ戦略が `trunk`（違えば `not_applicable`） | 結果 JSON の `status` と `outputs`（`merge_commit`・`method`・`by`）が契約の形 | `land` の副作用 |
| `cmd_confirm` | `plugins/ndf/scripts/trunk-steps.py` | 新設 | `(a) -> NoReturn` | `trunk_cd.confirm` を呼び、結果 JSON と CD の結果のファイル（`--out`）を書く | ブランチ戦略が `trunk` | `--out` のファイルが `release_lib/cd_result.py` の `save_result` の形で書かれる | ファイルの書き込み・`confirm` の副作用 |
| `cmd_cd` | `plugins/ndf/scripts/trunk-steps.py` | 新設 | `(a) -> NoReturn` | ブランチ戦略が `trunk` なら `trunk_cd.run_cd`、そうでなければ今の `release-steps.py publish` だけを実行する | `--revision` がある | 止まったとき終了コード 1 と `::error::` の注釈を標準出力に出す | `run_cd` の副作用 |
| `cmd_pr_check` | `plugins/ndf/scripts/trunk-steps.py` | 新設 | `(a) -> NoReturn` | `trunk_cd.pr_check` を呼び、終了コードと理由を出す | `--base`・`--head`・`--head-ref` がある | 終了コードが `pr_check` の値 | 外部コマンド（`git diff`） |
| `pace_flow_refusal` | `plugins/ndf/scripts/supervise_lib/release_flow.py` | 新設 | `(a, pin, name: str, channel) -> str \| None` | 進め方を使ってよいかの、ブランチ戦略と開発版チャネルに関わる断りの理由を返す（`fast_flow_refusal` を置き換える） | `pin` は `sprint_waves.pin_flow` | ブランチ戦略 `trunk` で `fast` なら理由を返す（決定 3）。ブランチ戦略 `trunk` では開発版チャネルの形を断りに使わない。ほかのブランチ戦略は今の `fast_flow_refusal` と `channel` の判定と同じ | 無し |
| `trunk_check_steps` | `plugins/ndf/scripts/supervise_lib/release_flow.py` | 新設 | `(steps: list[dict]) -> list[dict]` | 検査のプランから `merge-gate`・`merge`・`merge-approved` を外し、それらへ進むステップの行き先を `end` にする | `steps` は `plan_check` の並び | 返した並びに 3 つの id が無く、どのステップの `next`・`on_fail`・`gate_next`・`on_exit` の値も 3 つの id を指さない | 無し |
| `plan_trunk_material` | `plugins/ndf/scripts/supervise_lib/release_flow.py` | 新設 | `(a, repo: str, advise: bool) -> dict` | ブランチ戦略 `trunk` の承認資料のプラン（`trunk-steps.py facts` →（助言の MVV 判定））を作成する | ブランチ戦略が `trunk`。`a.release` が `package-plugin` の宣言 | プランは作業のステップ（`type: work`）と修正ステップを持たない | 無し |
| `plan_release_trunk` | `plugins/ndf/scripts/supervise_lib/release_flow.py` | 新設 | `(a, repo: str, mvv: str \| None) -> dict` | ブランチ戦略 `trunk` のリリースのワークフロー（承認の記録 → `land` → `confirm` → 導入の確かめ → 記録 → クリーンアップ）を作成する | 同上 | 並びが I7・I8 を満たす: `type: work`・`judge_fix_steps(fix=True)` の直し・同期の PR・`--channel dev`・`verify-install --ref <ベースブランチ>` を持たない | 無し |
| `approve_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `(a, repo: str \| None, approval: str, next_id: str = "bump") -> dict` | 承認の記録を書くステップの次を引数で受ける | 今のまま | 既定値で今と同じステップ | 無し |
| `trunk_waves` | `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 新設 | `(a, repo: str, then_of: str, mvv: str \| None, condition: dict \| None, note: str \| None) -> list[dict]` | ブランチ戦略 `trunk` の検査の後のステージを返す | ブランチ戦略が `trunk` | `normal` は 承認資料 → リリース承認ゲート → 本番、`auto` は 承認資料 → 本番（先頭が MVV 判定）。まとめの後（`condition` あり）は空。開発版のステージを持たない（I12） | 無し |
| `route_waves` | `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 変更 | 今のまま | ブランチ戦略 `trunk` なら先頭で `trunk_waves` を返す | 今のまま | ブランチ戦略 `trunk` でなければ今と同じ並び（I10） | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_resolved` | ベースと本番が違う | ブランチ戦略 `trunk` の設定で `base_branch` と `production_branch` が別の値 | `_resolved` の比べ | 止める（`FlowError`） | `branch_flow.base_branch = 'develop'（許す値: main）（理由: promotion = trunk）` の形。呼び手（worktree の用意・プランの組み立て）が今の `FlowError` の扱いで終了コード 1 | 後始末なし。設定を直せば再実行できる |
| `production_method` | 読めない | ファイルが無い・JSON の構文エラー・型の違い | `read_project_decl` | 既定値で続ける（`None` を返す） | 無し（判定は `check_method` が出す） | 無し |
| `version_only` | 差分を取れない | `git diff` の失敗・コミットが無い | `changed_kinds` | 呼び出し元へ返す（偽と理由） | 呼び手が理由を出す | 無し |
| `is_bump_pr` | 判定できない | `version_only` が偽を返した | `is_bump_pr` | 呼び出し元へ返す（偽と理由） | 呼び手が理由を出す | 無し |
| `pr_check` | バージョンを変えた | PR が `plugin.json` の `version` を変え、版上げの PR でない | `pr_check` | 止める | `::error::バージョンは CD がマージの後に上げる（変えたファイル: …）`、終了コード 1 | PR から版数の変更を外して push し直せば通る |
| `pr_check` | 差分を取れない | `git diff` の失敗（浅い clone など） | `pr_check` | 止める | `::error::` に git の出力の末尾、終了コード 1 | `fetch-depth: 0` で取り直して再実行できる |
| `last_bump_commit` | 始点が無い | `plugin.json` の版を書いたコミットが辿れない | `last_bump_commit` | 止める（`CdStop("precondition")`） | `run_cd` の結果の `summary` | 無し。浅い clone なら取り直して再実行できる |
| `changed_plugins` | 一覧を読めない | `marketplace.json` が無い・壊れている | `changed_plugins` | 止める（`CdStop("precondition")`） | 同上 | 無し |
| `bump_level` | ラベルを読めない | `gh pr view` の失敗（ネットワーク・権限） | `bump_level` | 再試行（`lib/gh_call.py` の今の再試行）の後に止める（`CdStop("precondition")`） | 同上 | 版上げの PR は作られていない。次の push か workflow の再実行で同じ範囲を読み直す |
| `planned_version` | 版を読めない | `plugin.json` が無い・版が semver でない | `planned_version` | 止める（`CdStop("precondition")`・`cmd_facts` では終了コード 1） | 同上 | 無し |
| `open_bump_pr` | 一覧を読めない | `gh pr list` の失敗 | `open_bump_pr` | 再試行の後に止める（`CdStop("precondition")`） | 同上 | 版上げの PR を 2 本作らないよう、読めないときは作らずに止める（I9） |
| `create_bump_pr` | 書き換えが落ちる | `release-steps.py bump`・`changelog` の終了コードが 0 でない | `create_bump_pr` | 止める（`CdStop("precondition")`） | `summary` にサブコマンドの出力の末尾 | 一時の作業ディレクトリを消す。push 前なので origin に残るものは無い。再実行できる |
| `create_bump_pr` | push か PR の作成が落ちる | トークンの権限不足・同じ名前のブランチが origin にある | `create_bump_pr` | 止める（`CdStop("precondition")`） | `summary` に「`NDF_RELEASE_TOKEN` の権限（contents・pull_requests の書き込み）を確かめる」 | ブランチだけが残った場合、次の実行の `open_bump_pr` は PR が無いため作り直しを試み、同じ名前の push で止まる。残ったブランチを消すのは人（C4 に当たる削除は自動で行わない） |
| `create_bump_pr` | 自動マージのリクエストが落ちる | `gh pr merge` の失敗 | `create_bump_pr` | 呼び出し元へ返す（PR は作られている） | `run_cd` は `ok` で、`summary` に「自動マージをリクエストできなかった。版上げの PR #N を手でマージする」 | 次の push の実行は開いた PR を見て作らない（I9）。`strict` では `update_bump_pr` が再リクエストする |
| `update_bump_pr` | 取り込みが落ちる | `update-branch` の 422（衝突・`expected_head_sha` の不一致） | `update_bump_pr` | 止める（`CdStop("precondition")`） | `summary` に API のエラーメッセージ | 版上げの PR は残る。次の push の実行が読み直して再試行する |
| `run_cd` | シークレットが無い | `NDF_RELEASE_TOKEN` が空 | `run_cd` の手順 2 の前 | 止める（タグの作成までは済ませる） | `::error::cause=precondition 秘密 NDF_RELEASE_TOKEN が無い`、終了コード 1 | マージは止まらない。シークレットを置いた後の push で残りの範囲がバージョンアップへ進む |
| `run_cd` | タグか Release の作成が落ちる | `release-steps.py publish` の終了コードが 0 でない | `run_cd` の手順 1 | 止める | `::error::cause=precondition` と `publish` の出力の末尾、終了コード 1 | workflow の再実行で `publish` は足りないものだけを作る（冪等） |
| `create_bump_pr`・`update_bump_pr`・`run_cd` | 承認が要る | `branch_rules` の承認の数が 1 以上で、版上げの PR に承認が無い | 自動マージのリクエストの後。`run_cd` では開いた版上げの PR を見つけて何もしないとき | 止める（`CdStop("precondition")`。PR と自動マージのリクエストは残す） | `::error::cause=precondition 版上げの PR #N は承認が M 件要る`、終了コード 1 | 人が GitHub で版上げの PR を承認すれば、残した自動マージのリクエストでマージされる。`confirm` は再実行できる |
| `branch_rules` | ruleset を読めない | `GET rules/branches` の失敗（ネットワーク・権限） | `branch_rules` | 呼び出し元へ返す（`None`） | 呼び手が `precondition` で止まる（`check_method` は `MergeStop`、`trunk_cd` は `CdStop`） | 何も書いていない。再実行できる |
| `confirm` | CD が失敗した | そのマージか版上げの PR のマージの push の CD の実行が失敗で終わった（承認が要る版上げの PR の `precondition` を含む） | `confirm` の実行の照会 | 止める（`cd_failed`） | 結果 JSON の `summary` に実行の URL と、注釈の `cause` と理由。終了コード 1 | 原因を直して workflow を再実行した後、`confirm` を再実行できる |
| `confirm` | 上限を超えた | 版上げの PR のチェックかキューが `budget` の秒で終わらない | `confirm` の待ち | 止める（`ci_pending`） | `summary` に版上げの PR の番号と、チェックかキューのどちらで待つか。終了コード 1 | 再実行できる（読むだけで書かない） |
| `read_pr` | 読めない | GraphQL の失敗・PR が無い | `read_pr` | 再試行の後に止める（`MergeStop("precondition")`） | `land` の結果 JSON | 何も書いていないので再実行できる |
| `record_user_merge` | コメントが書けない | 権限・ネットワーク | `record_user_merge` | 呼び出し元へ返す（`land` は `ok` のまま、`items` に `result: comment_failed`） | `summary` に「人のマージの記録を書けなかった」 | 再実行でマーカーが無いため書き直す |
| `check_method` | 方式が無い・表に無い値 | `production_merge.method` が無いか `merge-queue`・`strict` でない | `check_method` | 止める（`MergeStop("precondition")`） | `summary` に設定の値と許す値。終了コード 1 | 設定を直してから再実行できる |
| `check_method` | ruleset と食い違う | 方式 `merge-queue` で ruleset に merge queue が無い、`strict` で `strict_required_status_checks_policy` が偽 | `check_method` | 止める（`MergeStop("precondition")`） | `summary` に設定の値と ruleset の値の 2 つ | ruleset か設定を直してから再実行できる |
| `check_method` | 承認が要る | ruleset の `required_approving_review_count` が 1 以上で、PR の `reviewDecision` が `APPROVED` でない | `check_method` | 止める（`MergeStop("precondition")`） | `summary` に要る承認の数と今の状態 | レビューの承認を得てから再実行できる |
| `ungated_allowed` | プラグインを変える | 承認ゲートを通らない PR が配るプラグインの中身を変える | `ungated_allowed` | 止める（`MergeStop("precondition")`） | マージのコンポーネントの結果 JSON の `summary` に「リリース対象を変える PR はリリースのワークフローを通す」と変わったプラグインの名前。終了コード 1 | マージしていない。リリースのワークフロー（ブランチ戦略 `trunk`、PR の番号を受ける）で出し直す |
| `land` | head が動いた | PR の head と承認済みのコミットが違う | `land` の検証 | 止める（`head_moved`） | `summary` に 2 つの SHA と「承認を取り直す」。終了コード 1 | 追加もマージもしていない。承認資料を作り直して承認を取り直す |
| `enqueue` | キューに入らない | すぐに入れられない PR で GitHub が自動マージに切り替わった・エラーを返した | 追加の後の `read_pr` | 止める（`not_enqueued`）。`autoMergeRequest` があれば `disablePullRequestAutoMerge` で外す | `summary` に GitHub のエラーメッセージか「キューに無い」 | 自動マージは外してある。必須のチェックがそろってから再実行できる |
| `merge_strict` | HEAD を含まない | `compare` の `behind_by` が 1 以上 | `merge_strict` | 止める（`behind`） | `summary` に `behind_by` の値と「main を取り込んで承認を取り直す」 | スプリントの経路で main を取り込み、新しい head で承認を取り直す |
| `merge_strict` | head の不一致 | マージの API の 409 | `merge_strict` | 止める（`head_moved`） | `land` と同じ | `land` と同じ |
| `merge_strict` | ruleset がマージを拒む | マージの API の 405・422（必須のチェックや承認がそろわない） | `merge_strict` | 止める（`MergeStop("precondition")`）。`--admin` を付けて実行し直さない（I13） | `summary` に「方式があるため `--admin` を使わない」と GitHub のエラーメッセージ。終了コード 1 | マージしていない。チェックか承認がそろってから `land` を再実行できる |
| `merge_serial` | `serial` の手順で止まる | `run_serial` の `SerialStop` | `merge_serial` | 止める（同じ原因の `MergeStop`） | `land` の結果 JSON の `cause` と `summary` | ロックは `run_serial` が返している。原因ごとの次にすることは設計の「止まった理由」 |
| `check_method` | 方式が表で使えない | `usable_methods` の並びに設定の方式が無い | `check_method`（I6 の前） | 止める（`MergeStop("method_unavailable")`） | `summary` に設定の値・所有者・公開範囲・プランと使える方式。`outputs.usable_methods`。終了コード 1 | 何も書いていない。設定を直してから再実行できる |
| `check_method` | `serial` で見るチェックが無い | `ci.required_checks` が空 | `check_method` | 止める（`MergeStop("precondition")`） | `summary` に「`serial` は `ci.required_checks` を待つ」 | 設定を足してから再実行できる |
| `repo_facts` | プランを読めない | owner でない・`GET /user` の所有者が別の人・API の失敗 | `repo_facts` | 既定値で続ける（`plan` が `None`、`reason` に理由） | `check_method` が I6 で止まるときだけ `summary` に理由と「確かに使えるのは `serial` だけ」 | 無し |
| `read_lock` | 読めない | `git ls-remote`・`git fetch` の失敗（ネットワーク・権限） | `read_lock` | 再試行の後に止める（`SerialStop("precondition")`） | `summary` に git の出力の末尾 | 何も書いていない。再実行できる |
| `wait_staging` | チェックを読めない | `GET /commits/{sha}/check-runs` の失敗 | `wait_staging` | 再試行（`lib/gh_call.py` の今の再試行）の後に止める（`SerialStop("precondition")`） | `summary` に API のエラーメッセージ | `run_serial` がロックを返す。再実行できる |
| `build_staging` | 衝突する | 本番ブランチの HEAD と承認済みのコミットが衝突する | `git merge` の終了コード | 止める（`SerialStop("conflict")`） | `summary` に衝突したファイル | 一時の worktree を消す。ロックを持っていれば返す。スプリントの経路で main を取り込み承認を取り直す |
| `push_lock` | push が失敗する | 期待する値の違い以外（権限・ネットワーク） | `git push` の出力 | 再試行（`lib/gh_call.py` と同じ回数）の後に止める（`SerialStop("precondition")`） | `summary` に git の出力の末尾 | ロックを取れていなければ何も残らない。返せなかったロックは期限の後にほかの工程が引き取る |
| `acquire` | ロックを取れない | 期限の前の持ち主が `end` まで返さない・トレーラーの無いブランチ | `acquire` | 止める（`SerialStop("lock_busy")`） | `summary` に持ち主の PR の番号と期限。トレーラーが無ければ「`ndf-serial/<branch>` を人が消すか、期限のトレーラーを持つコミットにする」 | 自動では消さない。再実行できる |
| `run_serial` | 自分のロックが引き取られた | 作り直しの push が期待する値の違いで断られた | `push_lock` が偽 | 止める（`SerialStop("lock_busy")`）。マージしない | `summary` に引き取った持ち主 | 返却は行わない（自分のロックではない）。再実行できる |
| `run_serial` | 期限を過ぎた | チェックの待ちと作り直しが `budget` を使い切った | マージの直前の期限の確かめ | 止める（`SerialStop("ci_pending")`）。マージしない | `summary` に一時マージコミットの SHA と待ったチェック | ロックを返す。`land` を再実行するとロックを取るところから続く |
| `run_serial` | マージの後の比べが違う | 読み直しとマージの API の間にロックの外のマージが入った | 第 1 親と tree の比べ | 続ける（`ok`）。`items` に `untested_combination` | `summary` に「main の CI を確かめる」 | マージは済んでいる。ロックを返す |
| `release` | 返せない | 既に引き取られている | `push_lock` が偽 | 続ける（`items` に `lock_release: taken_over`） | 無し | 無し |
| `land_bump_serial` | 版上げの PR を入れられない | `run_serial` の `SerialStop` | `confirm` | 止める（同じ原因の `ConfirmOutcome`） | `summary` に版上げの PR の番号と原因 | 版上げの PR は残る。`confirm` を再実行できる |
| `wait_landed` | キューから外れた | 組み合わせのチェックの失敗・衝突 | `wait_landed` | 止める（`queue_removed`）。同じ head で入れ直さない | `summary` に外れた PR の番号 | スプリントの経路へ差し戻す |
| `wait_landed` | 上限を超えた | キューかチェックが `budget` の秒で終わらない | `wait_landed` | 止める（`ci_pending`） | `summary` に PR の番号とキューの位置 | キューに残っている。`land` を再実行するとマージ済みか待ちの続きから進む |
| `cmd_land` | 承認の記録が無い | `--approval` の資料に承認の記録が無い | `cmd_land`（`approved_commit.recorded_sha`） | 止める（`precondition`） | `summary` に資料のパスと「リリース承認ゲートの承認を記録する」。終了コード 1 | 承認の記録を書いてから再実行できる |
| `applies` | 宣言を読めない | `.ndf/worktree.json` か `.ndf/project.json` が無い・壊れている | `applies` | 既定値で続ける（偽を返し、今のマージへ渡す） | 無し（宣言の不備は worktree の用意とプランの組み立てが `FlowError` で出す） | 無し |
| `ungated_changes` | 差分を取れない | `git fetch` か `git diff` の失敗 | `ungated_changes` | 止める（`MergeStop("precondition")`。変えていないとみなさない） | 呼び手の結果 JSON の `summary` に git の出力の末尾。終了コード 1 | マージしていない。取り直して再実行できる |
| `merge_from_watch` | マージが止まる | `merge_strict`・`enqueue`・`wait_landed` の `MergeStop` | `merge_from_watch` | 止める（マージのコンポーネントの結果 JSON の `stopped`） | `summary` に `MergeStop` の原因と理由。終了コード 1 | マージのコンポーネントの今の再開のまま。再実行するとマージ済みかキューの待ちから続く |
| `merge_when_green` | ブランチ戦略 `trunk` のマージが止まる | `ungated_allowed` か `merge_from_watch` の `MergeStop` | `merge_when_green` の 2 つの呼び出し | 止める（今の `emit` の形で `stopped`） | `summary` に原因と理由。終了コード 1 | クリーンアップを行わない。再実行できる |
| `cmd_facts` | 承認資料を作成できない | `approval-facts`・`notes` の終了コードが 0 でない・PR が読めない・版を読めない | `cmd_facts` | 止める | 結果 JSON の `stopped` と、サブコマンドの出力の末尾。終了コード 1 | 承認資料は前の版のまま残る（`approval-facts` の今の振る舞い）。再実行できる |
| `cmd_confirm` | 結果のファイルを書けない | `--out` の保存先が無い・書けない | `cmd_confirm` | 止める | 結果 JSON の `stopped` と OS のエラーメッセージ。終了コード 1 | 読むだけの処理なので再実行できる |
| `cmd_cd` | バージョンアップで止まる | `run_cd` の `CdStop` | `cmd_cd` | 止める | `::error::cause=<原因> <理由>`、終了コード 1 | 次の push か workflow の再実行で同じ範囲を読み直す |
| `cmd_pr_check` | 判定できない | `pr_check` が差分を取れない | `cmd_pr_check` | 止める | `::error::` に git の出力の末尾、終了コード 1 | 取り直して再実行できる |
| `plan_trunk_material`・`plan_release_trunk` | 宣言が足りない | `release.plugin`・`release.runtimes`・本番ブランチが無い | `release_templates._validate_release_decl`（今の関数を呼ぶ） | 止める（`DeclError`） | `new sprint` の今の断りの出力 | 宣言を直して再実行できる |
| `trunk_waves` | 本番ブランチが無い | 本番ブランチを解けない | `trunk_waves` | 止める（`DeclError`） | 同上 | 同上 |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `land` | PR がマージ済み | `record_user_merge` を呼び、`ok`（`by: user`） |
| `land` | 開いている・head ≠ 承認済みのコミット | `head_moved` |
| `land` | 開いている・head = 承認済みのコミット・`check_method` が止める | `precondition` |
| `land` | 一致・方式 `merge-queue`・キューに既にある | 追加を実行せずに `wait_landed` |
| `land` | 一致・方式 `merge-queue`・キューに無い | `enqueue` → `wait_landed` → `ok`（`by: ndf`） |
| `land` | 一致・方式 `strict` | `merge_strict` → `wait_landed` → `ok`（`by: ndf`） |
| `land` | 一致・方式 `serial` | `merge_serial` → `ok`（`by: ndf`）。`wait_landed` を通らない（`run_serial` がマージまで待つ） |
| `land` | 一致・方式が表で使えない | `method_unavailable`（I17） |
| `run_serial` | チェックが通った・HEAD が第 1 親と同じ・期限の前 | `merge` を呼ぶ → 比べ → 返却 |
| `run_serial` | チェックが通った・HEAD が動いた | 新しい HEAD で `build_staging` → 期待する値つきで作り直し → `wait_staging` へ戻る |
| `run_serial` | ロックが既にある・期限を過ぎた | 期待する値（期限を過ぎたコミット）つきで引き取る |
| `run_serial` | ロックが既にある・期限の前 | `lib/waits.py` で待ち、`end` を超えたら `lock_busy` |
| `usable_methods` | public・Organization | `merge-queue`・`strict`・`serial` |
| `usable_methods` | public・個人アカウント | `strict`・`serial` |
| `usable_methods` | private・internal・`free` | `serial` |
| `usable_methods` | private・`pro`・`team` | `strict`・`serial` |
| `usable_methods` | private・internal・それ以外か読めない | `None`（不明） |
| `confirm` | 方式 `serial`・開いた版上げの PR がある | `land_bump_serial` の後に、タグと GitHub Release を待つ |
| `land` | `gated` が偽（承認ゲートを通らない）・`ungated_changes` が空でない | `precondition`（I5） |
| `ungated_allowed` | `applies` が偽 | 偽（今の `gate_stop` の判定へ戻す） |
| `ungated_allowed` | `applies` が真・`ungated_changes` が空（設定と指示書を含まない文書だけ・設定と指示書を変えない版上げの PR） | 真（承認ゲートで止めずにマージへ進む） |
| `ungated_allowed` | `applies` が真・`ungated_changes` が空でない | `MergeStop("precondition")` |
| `merge_when_green` の承認ゲートの判定 | `verdict.stops` が真・`a.gate_approved` が無い・`ungated_allowed` が偽 | 今の `gate_stop` |
| `merge_when_green` の承認ゲートの判定 | `verdict.stops` が真・`a.gate_approved` が無い・`ungated_allowed` が真 | 止めずに緑を待つ |
| `merge_when_green` のマージ | `applies` が真 | `merge_from_watch`（`--admin` を実行しない。I13） |
| `merge_when_green` のマージ | `applies` が偽 | 今の `_merge_pr` |
| `run_cd` | HEAD の版にタグが無い | 手順 1: `publish` を実行する |
| `run_cd` | 開いた版上げの PR がある・方式 `strict`・PR が HEAD を含まない | 手順 2: `update_bump_pr` を実行して終わる |
| `run_cd` | 開いた版上げの PR がある・それ以外・`branch_rules` の承認の数が 0 か PR の `reviewDecision` が `APPROVED` | 手順 2: 何もせずに終わる（I9） |
| `run_cd` | 開いた版上げの PR がある・それ以外・`branch_rules` の承認の数が 1 以上・PR の `reviewDecision` が `APPROVED` でない | `precondition`（注釈に求める承認の数と PR の番号。I14） |
| `run_cd` | 開いた PR が無い・`changed_plugins(last_bump_commit..先端)` が空 | 手順 3: 終わる（`ok`、バージョンアップなし） |
| `run_cd` | 開いた PR が無い・変化がある・シークレットが無い | `precondition` |
| `run_cd` | 開いた PR が無い・変化がある・シークレットがある | 手順 4〜5: `create_bump_pr` |
| `run_cd` | 版上げの PR を作るか取り込んだ・`branch_rules` の承認の数が 1 以上・PR に承認が無い | `precondition`（注釈に求める承認の数と PR の番号。I14） |
| `confirm` | HEAD の版のタグと GitHub Release があり、`merge_commit` がタグの祖先 | `ok`（版） |
| `confirm` | そのマージの push の CD の実行が成功で終わり、開いた版上げの PR が無く、タグの版が変わらない | `ok`（空の版。終了コード 3） |
| `confirm` | どちらかの push の CD の実行が失敗で終わった | `cd_failed` |
| `confirm` | 上のどれでもなく上限を超えた | `ci_pending` |
| `pr_check` | `version` の行が変わらない | 0 |
| `pr_check` | 変わった・`is_bump_pr` が真 | 0 |
| `pr_check` | 変わった・`is_bump_pr` が偽 | 1 |
| `check-pr-base.sh` | マージ先の設定の `promotion` が `trunk` | head の名前を問わず `trunk-steps.py pr-check` の終了コードで終わる |
| `check-pr-base.sh` | `selected`・それ以外 | 今の判定（I10） |

- 繰り返しの終わる条件: `wait_landed`・`confirm`・`acquire`・`wait_staging` は、終わりの状態（マージ済み・外れた・タグがそろった・CD の実行が終わった）か、`budget` の秒（`ci_budget`。プランの `ci_wait_timeout` と同じ値を `--wait` で渡す）のどちらかで終わる。待ちは `lib/waits.py` を使い、自前の `sleep` の繰り返しを書かない（構造の検査の `sleep-loop`）
- 計算量の注意: `bump_level` は範囲のマージの数だけ `gh pr view` を実行する。範囲はバージョンアップ 1 回分で、並列のレーンの上限（6 本）とまとめの PR を足しても 20 本程度である。上限を超えたら GitHub の利用の上限を `lib/gh_quota.py` の今の扱いで待つ

## 内部のデータと定数

| 名前 | 種類 | 格納場所 | 既定値 |
| --- | --- | --- | --- |
| ブランチ戦略 `trunk` | 定数（`PROMOTIONS` の値） | `plugins/ndf/scripts/lib/branch_flow.py` | 許す分岐元 `production`・文書のマージ先 `base`・マージ先 `release`、残さない、選ばない、見るブランチ `("production",)` |
| `INTEGRATE_POINTS["release"]` | 定数 | `plugins/ndf/scripts/lib/branch_flow.py` | `"production"` |
| `ProductionMerge.method` | 設定値 | `plugins/ndf/scripts/project_lib/model.py` | `None`（値は `Literal["merge-queue", "strict", "serial"]`） |
| `ProductionMerge.admin` | 設定値 | `plugins/ndf/scripts/project_lib/model.py` | `None`（省けるようにする。`production_admin` は今のとおり `true` のときだけ真） |
| `MANIFEST_RE`・`MANIFEST_LINE_RE` | 定数 | `plugins/ndf/scripts/lib/diff_kinds.py`（`scripts/ci-heavy-skip.py` から移す。唯一の格納場所） | 今の値 |
| `BUMP_PREFIX` | 定数 | 置かない。`lib/branch_flow.py` の `RELEASE_PREFIX`（`release/v`）を使う | — |
| `LEVEL_LABELS` | 定数 | `plugins/ndf/scripts/release_lib/trunk_cd.py` | `("release:major", "release:minor", "release:patch")` |
| `TOKEN_ENV` | 定数 | `plugins/ndf/scripts/release_lib/trunk_cd.py` | `"NDF_RELEASE_TOKEN"` |
| `CdOutcome` | 型（値オブジェクト） | `plugins/ndf/scripts/release_lib/trunk_cd.py` | `status`・`cause`・`summary`・`version`・`bump_pr`・`items` |
| `ConfirmOutcome` | 型（値オブジェクト） | `plugins/ndf/scripts/release_lib/trunk_cd.py` | `status`・`cause`・`summary`・`version`・`bump_pr`・`run_url` |
| `CdStop` | 型（例外） | `plugins/ndf/scripts/release_lib/trunk_cd.py` | `cause`・`summary` |
| `METHODS` | 定数 | `plugins/ndf/scripts/release_lib/merge_method.py` | `("merge-queue", "strict", "serial")`。`project_lib/model.py` の `Literal` と同じ値で、テストが一致を見る |
| `CAUSES` | 定数 | `plugins/ndf/scripts/release_lib/merge_method.py` | `("head_moved", "precondition", "not_enqueued", "queue_removed", "behind", "ci_pending", "method_unavailable", "lock_busy", "conflict", "staging_failed")`。`confirm` の `cd_failed` は `trunk_cd.py` が持つ |
| `MERGE_MARKER` | 定数 | `plugins/ndf/scripts/release_lib/merge_method.py` | `"<!-- ndf:user-merge -->"` |
| `PrState` | 型（値オブジェクト） | `plugins/ndf/scripts/release_lib/merge_method.py` | `number`・`node_id`・`merged`・`head_oid`・`head_ref`・`base_ref`・`base_oid`・`in_queue`・`auto_merge`・`review_decision`・`merge_commit` |
| `LandResult` | 型（値オブジェクト） | `plugins/ndf/scripts/release_lib/merge_method.py` | `status`・`cause`・`summary`・`merge_commit`・`method`・`by`・`items` |
| `MergeStop` | 型（例外） | `plugins/ndf/scripts/release_lib/merge_method.py` | `cause`・`summary` |
| `METHOD_TABLE` | 定数 | `plugins/ndf/scripts/release_lib/method_select.py` | 設計の「マージの方式を選ぶ表」の 6 行（所有者・公開範囲・プラン → 使える方式）。唯一の格納場所 |
| `RepoFacts` | 型（値オブジェクト） | `plugins/ndf/scripts/release_lib/method_select.py` | `owner`・`owner_type`・`visibility`・`plan`・`reason` |
| `SERIAL_PREFIX` | 定数 | `plugins/ndf/scripts/release_lib/serial_merge.py` | `"ndf-serial/"` |
| `TRAILERS` | 定数 | `plugins/ndf/scripts/release_lib/serial_merge.py` | `("NDF-Serial-PR", "NDF-Serial-Approved", "NDF-Serial-Holder", "NDF-Serial-Deadline")` |
| `Lock` | 型（値オブジェクト） | `plugins/ndf/scripts/release_lib/serial_merge.py` | `sha`・`pr`・`approved`・`holder`・`deadline`（UTC。読めなければ `None`） |
| `SerialStop` | 型（例外） | `plugins/ndf/scripts/release_lib/serial_merge.py` | `cause`・`summary`。原因は `CAUSES` の部分集合 |
| 承認資料の保存先 | 設定値 | `plugins/ndf/scripts/supervise_lib/release_flow.py` | `issues/approval-<プラグイン>-<スプリント名>.md`（予定のバージョンは承認資料の中に書き、名前に入れない。版はマージの後に CD が決めるため） |
| `confirm` の結果のファイル | 設定値 | プランの `cwd` の `supervise_lib/verify_steps.py` の `CD_RESULT_FILE` | 今の値（`record` が `--cd-result` で読む今の形） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並列のレーン（最大 6 本）の `land` | main・merge queue | GitHub の merge queue が組み合わせを順に確かめる（`merge-queue`）。`strict` は GitHub の `sha` の検証と最新の取り込みの規則が後の 1 本を断る（`behind`・`head_moved`） | `land` は始めに PR の状態を読み直す。マージ済みなら記録だけ、キューにあれば待ちから続ける |
| 並列のレーンの `land`（`serial`） | main・ブランチ `ndf-serial/<本番ブランチ>` | ロックのブランチを期待する値つきの push で取る（I16）。マージの直前に HEAD を読み直す（I15） | `land` は始めに PR の状態を読み直す。止まった `land` はロックを返している。落ちた持ち主のロックは期限の後に引き取られる |
| `serial` の `land` と `confirm`（版上げの PR） | 同上 | 同じロックを取る | どちらも再実行できる。ロックの外のマージ（人の画面でのマージ）は排他しない（設計の「残る危険」） |
| CD の実行どうし（push ごと） | 版上げの PR・`release/v<版>` のブランチ・タグ | `release-cd.yml` の `concurrency: release-cd`（`cancel-in-progress: false`・`queue: max`）が 1 本ずつ走らせる | 各実行は HEAD から状態を読み直す。`publish` は足りないものだけを作り、開いた版上げの PR があれば作らない（I9） |
| `land` と CD の実行 | main の HEAD | 共有の書き込みは無い（`land` は本番の PR、CD は版上げの PR を書く） | `confirm` がマージコミットを含む HEAD のタグを待つため、順序に依らない |
| 同じ PR への `land` の再実行 | PR のコメント（人のマージの記録） | 書く直前にコメントの一覧を読み、`MERGE_MARKER` があれば書かない | 1 つのリリースのワークフローが 1 本の PR を持つため、同じ PR の `land` は同時に走らない。再実行は記録を 2 本に増やさない（I11） |
| 人の GitHub でのマージと `land` | 本番の PR | 排他しない。`land` の読み直しがマージ済みを見つける | `land` はもう一度マージしない（I11） |
| `merge-when-green`（文書の PR）と `land` | main | merge queue（同上） | マージのコンポーネントの今の再開の振る舞いのまま |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_land` | `status`・`cause`・`summary`・`outputs`（`merge_commit`・`method`・`by`）・`items`（読んだ PR の状態・追加・マージ） | 結果 JSON（標準出力の最後の行） | トークン |
| `run_serial` | ロックの取得・引き取り・作り直しの回数・一時マージコミットの SHA・返却（`lock_release`）・`untested_combination` | `land` の結果 JSON の `items` | トークン |
| `cmd_confirm` | `status`・`cause`・`summary`（実行の URL・版上げの PR の番号）・`outputs.version`・`outputs.bump_pr` | 結果 JSON と `--out` の CD の結果のファイル | トークン |
| `cmd_cd` | 止まった理由（`cause=<原因> <理由>`） | 標準出力の `::error::` の注釈（`confirm` が `gh run view` で読む） | トークン（シークレットの名前だけを出し、値を出さない） |
| `cmd_cd` | 実行の要約（作ったタグ・版上げの PR の番号・取り込んだか） | 標準出力の結果 JSON（workflow の実行のログ） | トークン |
| `cmd_facts` | 予定のバージョン・承認済みのコミット（`metrics.approved_sha`） | 結果 JSON と承認資料 | 無し |
| `cmd_pr_check` | 理由（変えたファイル） | 標準出力の `::error::` の注釈 | 無し |
| `record_user_merge` | 承認済みのコミット・マージコミット・方式・マーカー | 本番の PR のコメント | 利用者の名前（GitHub の画面がマージした人を持つ） |
| `create_bump_pr`・`update_bump_pr` | push と API の結果 | 結果 JSON の `items` | トークン。`git push` は `GH_TOKEN` を環境に渡した `gh auth git-credential` で認証し、URL にトークンを書かない |
| `plan_release_trunk` の各ステップ | ステップの結果 | 今の進捗記録（工程「デプロイ」「後片付け」） | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存のコンポーネント | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `Promotion.integrates_to_production` | `INTEGRATE_POINTS`・`ResolvedFlow.check_target` | `lib/branch_flow.py` を読んだ | 使う。`check_target` と同じ表を読む |
| `_resolved` | `FlowError` | 同上 | 使う。エラーの形を足さない |
| `production_method` | `project_decl.production_admin` | `grep -rn production_merge plugins/ndf/scripts` | 使う。読み方を同じにし、`read_project_decl` を呼ぶ |
| `is_bump_pr` | `scripts/ci-heavy-skip.py` の `version_only` | `grep -rn version_only scripts plugins/ndf/scripts` | 使う。`lib/diff_kinds.py` へ移して呼ぶ（プラグインの中からリポジトリの `scripts/` は読めない） |
| `pr_check` | `version_only` | 同上 | 使う（`is_bump_pr` を通す） |
| `last_bump_commit` | `release_lib/publish.py` の `version_at` | `release_lib/publish.py` を読んだ | 使えない。`version_at` は版の値を返し、版を書いたコミットを返さない |
| `changed_plugins` | `release-steps.py changed-plugins` | `grep -n cmd_changed_plugins release-steps.py` | 使えない。始点がタグで、範囲の始点（版上げのコミット）を受けない。名前の列挙だけを `marketplace.json` から行う |
| `bump_level` | `lib/gh_call.py` の `gh` | `lib/gh_call.py` を読んだ | 使う（ラベルの照会） |
| `planned_version` | `release_lib/bump.py`・`semver` | `grep -n "^def" release_lib/bump.py` | 使う（`semver` の `bump_*`） |
| `open_bump_pr` | `merged_lib/merge.py` の `_open_promote_pr` | `merged_lib/merge.py` を読んだ | 使えない。head の名前を 1 つに決めて探し、`release/v` の前方一致で探せない |
| `create_bump_pr` | `release-steps.py bump`・`changelog`（`plan_bump`・`cmd_changelog`） | `grep -n "def plan_bump\|def cmd_changelog" release-steps.py` | 使う。サブプロセスで呼び、書き換えを写さない（決定 10・11） |
| `update_bump_pr` | `lib/gh_call.py` の `gh` | 同上 | 使う（`update-branch` の API） |
| `run_cd` | `release-steps.py publish`（`cmd_publish`） | `.github/workflows/release-cd.yml` を読んだ | 使う。サブプロセスで呼ぶ |
| `confirm` | `release_lib/cd_result.py` の `read_cd`・`save_result` | `grep -n "^def" release_lib/cd_result.py` | 一部を使う。`save_result` で書く形を同じにする。`read_cd` は宣言の CD の行の実行を 1 本読むだけで、版上げの PR のマージの push を辿れないため、照会は新しく書く |
| `applies` | `merged_lib/merge.py` の `delivery.judge_target` | `merged_lib/merge.py` を読んだ | 一部を使う。マージ先が本番ブランチかは同じ宣言から読み、ブランチ戦略の判定を足す |
| `read_pr` | `merged_lib/pr_info.py` | `grep -n "^def" merged_lib/pr_info.py` | 使えない。`isInMergeQueue`・`autoMergeRequest`・`id` を読まない |
| `record_user_merge` | `release_lib/record.py` の `post_comment` | `grep -n "^def" release_lib/record.py` | 使う（コメントの投稿） |
| `branch_rules` | `merged_lib/required_checks.py` の ruleset の読み（`_rule_checks`・`_gh_pages`） | `grep -n "rules/branches" -r plugins/ndf/scripts` | 一部を使う。ruleset の取得を同じ関数で行い、merge queue・`strict`・承認の数の欄の読みを `branch_rules` に足す |
| `check_method` | `merged_lib/required_checks.py` の `branch_rules`・`method_select.usable_methods` | この文書の関数 | 使う。`merge_method` と `trunk_cd` は同じ `branch_rules` を呼ぶ |
| `create_bump_pr` | `merged_lib/required_checks.py` の `branch_rules` | 同上 | 使う |
| `update_bump_pr` | `merged_lib/required_checks.py` の `branch_rules` | 同上 | 使う |
| `ungated_changes` | `trunk_cd.is_bump_pr`・`trunk_cd.changed_plugins` | この文書の関数 | 使う。判定を 2 か所に持たない |
| `ungated_allowed` | `ungated_changes` | 同上 | 使う |
| `enqueue` | `gh pr merge --match-head-commit` | 決定 6 | 使えない。チェックの前なら自動マージに切り替わり、`land` の外でマージが起きる |
| `merge_strict` | `merged_lib/merge.py` の `_merge_pr` | `merged_lib/merge.py` を読んだ | 使えない。`--admin` を既定で実行し、`compare` の祖先の確かめを持たない |
| `wait_landed` | `lib/waits.py` | `grep -n tenacity scripts/check-script-structure.py` | 使う（待ちの包み） |
| `acquire` | `lib/lease.py`（手元の持ち主のコンポーネント）・`lib/locks.py`（ファイルロック）・`lib/waits.py` | `grep -n "^def" lib/lease.py lib/locks.py lib/waits.py` | `lease.py`・`locks.py` は使えない。どちらもコンテナの中の状態のディレクトリに記録を置き、ほかのマシンやコンテナの `land` を排他できない（決定 14）。待ちは `waits.py` を使う |
| `read_lock` | `release_lib/review_range.py` の `_remote_has`・`_fetch_branch` | `grep -n "^def" release_lib/review_range.py` | 一部を使う。ブランチの有無と取得は同じ形（`ls-remote --exit-code`・`fetch`）にし、トレーラーは `git log --format=%(trailers)` で新しく読む |
| `push_lock` | `release_lib/review_range.py` の `delete_branches` | 同上 | 使えない。期待する値を渡さずに消す。期待する値つきの push（STD14）は今のコンポーネントに無い |
| `release` | `push_lock` | この文書の関数 | 使う |
| `run_serial` | `acquire`・`build_staging`・`wait_staging`・`release` | この文書の関数 | 使う。ロックのコンポーネントとして `lib/lease.py`・`lib/locks.py` は使えない（`acquire` の行） |
| `usable_methods` | `lib/project_decl.py`・`merged_lib/required_checks.py` | `grep -rn "visibility" plugins/ndf/scripts/lib plugins/ndf/scripts/release_lib plugins/ndf/scripts/merged_lib` | 使えない。プランと公開範囲から方式を選ぶ表は今のコードに無い。表を新しく置く（`METHOD_TABLE`） |
| `wait_staging` | `lib/waits.py` | `grep -n "^def" lib/waits.py` | 使う（待ちの包み） |
| `wait_staging` | `merged_lib/required_checks.py` の `_latest_passed` | `grep -n "^def" merged_lib/required_checks.py` | 一部を使う。check run の最新を採る判定を公開して呼び、対象を PR でなく一時マージコミットの SHA にする |
| `build_staging` | `release_lib/promotion.py` の `build_branch` | `grep -rn "worktree\", \"add" plugins/ndf/scripts` | 使えない。単位の PR を順にマージしてリリースブランチを作る関数で、2 つの親の一時マージコミットを返さない。一時の worktree の作り方（`--detach` で作り、終わりに必ず消す）を同じにする |
| `repo_facts` | `lib/gh_call.py` の `gh` | `lib/gh_call.py` を読んだ | 使う |
| `merge_serial` | `merge_strict` のマージの API の呼び出し・`serial_merge.run_serial` | この文書の関数 | 一部を使う。`sha` を渡し `--admin` を付けない形を `merge_strict` と同じにする |
| `land_bump_serial` | `serial_merge.run_serial`・`merge_strict` のマージの API の呼び出しの形 | この文書の関数 | 使う。`trunk_cd` は `merge_method` を読まないため、マージの API の呼び出しは同じ形で自分に置く |
| `land` | `release-steps.py release`（今の本番のマージ） | `release-steps.py` を読んだ | 使えない。リリースブランチを作成し、同期の PR を作る（I8） |
| `merge_from_watch` | `merge_strict`・`enqueue`・`wait_landed` | この文書の関数 | 使う |
| `cmd_facts` | `release-steps.py approval-facts`・`notes` | `release_templates._dev_approval_steps` を読んだ | 使う。`--head <PR の head>` で範囲を「本番ブランチの HEAD..PR の head」にする |
| `cmd_land` | `lib/approved_commit.py` の `recorded_sha` | `grep -n "^def" lib/approved_commit.py` | 使う |
| `cmd_confirm` | `release_lib/cd_result.py` の `save_result` | 同上 | 使う |
| `cmd_cd` | `release-steps.py publish` | 同上 | 使う |
| `cmd_pr_check` | `trunk_cd.pr_check` | この文書の関数 | 使う |
| `pace_flow_refusal` | `release_flow.fast_flow_refusal` | `supervise_lib/release_flow.py` を読んだ | 置き換える。断りの文を 1 か所にまとめる |
| `trunk_check_steps` | `sprint_waves.keep_branch_merge` | `supervise_lib/sprint_waves.py` を読んだ | 使えない。マージのステップを包むだけで外さない |
| `plan_trunk_material` | `release_templates.advise_release_steps`・`release_cd.mvv_gate_steps` | `supervise_lib/release_templates.py` を読んだ | 使う（助言の MVV 判定） |
| `plan_release_trunk` | `release_templates.approve_step`・`_cleanup_step`・`release_cd.mvv_gate_steps`・`verify_steps.handoff_step`・`reject_step`・`release_verify.judge_record_step` | 同上 | 使う。`judge_fix_steps` は `fix=False` で呼ぶ |
| `trunk_waves` | `sprint_routes.normal_gate_2_stage`・`sprint.prod_plans` | `supervise_lib/sprint_routes.py` を読んだ | 一部を使う。ゲートのステージの文は承認資料の名前が違うため、同じ組み立てで名前だけを替える |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Promotion.integrates_to_production` | ブランチ戦略 `trunk` で真 | `whole`・`selected` で偽 | — |
| `_resolved` | ブランチ戦略 `trunk` で `main`・`main` を通す | 本番ブランチが `None` のとき比べない | `develop`・`main` で `FlowError` |
| `production_method` | `merge-queue`・`strict` を返す | キーが無い → `None` | 表に無い値・型の違い → `None` |
| `version_only` | 移す前の `scripts/tests/test_ci_heavy_skip.py` が変えずに通る | 版数の行だけ | `git diff` の失敗で偽 |
| `is_bump_pr` | `release/v` と版数だけの差分で真 | 版数と CHANGELOG の差分で真 | `release/v` でコードを含む差分で偽 |
| `pr_check` | 版数を変えない PR で 0 | 版上げの PR で 0 | 版数を変える PR で 1 と理由 |
| `last_bump_commit` | 版上げのコミットを返し、そのマージコミットを返さない | バージョンアップが 1 回だけの履歴 | 版を書いたコミットが無い → `CdStop` |
| `changed_plugins` | `plugins/ndf/` の変化で `["ndf"]` | 文書だけの範囲で空 | `marketplace.json` が壊れている → `CdStop` |
| `bump_level` | `release:minor` と無ラベルで `minor` | PR が無い範囲で `patch` | 照会の失敗 → `CdStop` |
| `planned_version` | `10.27.71` と `minor` で `10.28.0` | `major` | 版が semver でない → `CdStop` |
| `open_bump_pr` | 開いた `release/v` の PR を返す | 2 本あれば番号の小さい方 | 照会の失敗 → `CdStop` |
| `create_bump_pr` | PR を作り、自動マージをリクエストする | `merge-queue` と `strict` で同じリクエスト。承認の数 0 で止まらない | `bump` の失敗・push の失敗 → `CdStop`、自動マージの失敗は PR を残して返す。承認の数 1 で承認が無い → PR を残して `CdStop("precondition")` と数と番号 |
| `update_bump_pr` | HEAD を含まない PR に読んだ head を渡して取り込み、新しい head で再リクエストする | 含む PR では何もしない | 422 → `CdStop` |
| `run_cd` | 変化のある範囲で版上げの PR を 1 本作る | 開いた PR があると作らない・文書だけの範囲で作らない・開いた PR が承認済みなら承認の数 1 でも止まらない | シークレットが無い → タグを作って `precondition`、終了コード 1 と注釈。開いた版上げの PR があり承認の数 1 で承認が無い → 何も作らずに `precondition` と数と番号 |
| `confirm` | タグと Release がそろうと `ok` と版 | 変化なしの成功で空の版 | 実行の失敗で `cd_failed` と URL、上限で `ci_pending` と版上げの PR の番号 |
| `applies` | ブランチ戦略 `trunk`・マージ先 main で真 | マージ先がほかのブランチで偽 | 宣言が読めないと偽 |
| `read_pr` | 偽の GraphQL の応答から欄を埋める | マージ済みの PR | 照会の失敗 → `MergeStop` |
| `record_user_merge` | マーカーの無い PR に 1 本書く | マーカーのある PR に書かない | 書けないと偽と項目 |
| `check_method` | 方式と ruleset が一致すれば戻る | `strict` の承認 0 | 方式が無い・食い違い・承認が無い → `precondition` と 2 つの値 |
| `ungated_changes` | プラグイン・設定・指示書の変化を返す | 設定と指示書を変えない版上げの PR で空。`.ndf/` か `AGENTS.md` だけを変える PR・`plugins/x/CLAUDE.md` だけを変える PR・`AGENTS.md` を変える版上げの PR で空でない | 差分を取れない → `MergeStop` |
| `ungated_allowed` | 文書だけの PR で真 | ブランチ戦略 `trunk` でないマージ先で偽 | プラグインを変える PR で `MergeStop` |
| `enqueue` | 承認済みのコミットを `expectedHeadOid` に渡す | 既にキューにある PR | 応答が成功でもキューに無い → 自動マージを外して `not_enqueued` |
| `merge_strict` | `behind_by` 0 で `sha` 付きのマージ | `behind_by` 0 で `admin` が真でも迂回しない | `behind_by` 1 → `behind`、409 → `head_moved`、405・422 → `precondition`（`--admin` で実行し直さない） |
| `merge_serial` | `run_serial` にマージの関数を渡し、マージコミットを返す | `admin` が真でも迂回しない | `SerialStop` を同じ原因の `MergeStop` にする、409 → `head_moved` |
| `repo_facts` | Organization の owner で `plan` を読む | public でプランを読まない | owner でない・API の失敗 → `plan` が `None` と理由 |
| `usable_methods` | 表の 6 行それぞれ | internal を private と同じに扱う | 読めないプラン・Enterprise Cloud の名前 → `None` |
| `read_lock` | トレーラー 4 つを読む | ブランチが無い → `None` | トレーラーが無い → `deadline` が `None` |
| `build_staging` | 第 1 親と第 2 親とトレーラー | 承認済みのコミットが既に HEAD の祖先 | 衝突 → `conflict`、一時の worktree が残らない |
| `push_lock` | 空の期待する値で作る | 期待する値が今の値と同じで更新・削除 | 期待する値が違う → 偽で、ref が変わらない（一時の bare リポジトリ） |
| `acquire` | 無いブランチを取る | 期限ちょうどの境 | 期限の前で `end` を超える・トレーラーが無い → `lock_busy` |
| `release` | 自分のロックを消す | — | 引き取られている → `taken_over` で何も消さない |
| `wait_staging` | すべて成功で戻る | 上限ちょうど | 1 つ失敗 → `staging_failed`、チェックが付かない → `ci_pending` と `on.push` の案内 |
| `run_serial` | 動かない HEAD でマージしてロックを返す | HEAD が動けば作り直してからマージ | 期限を過ぎた持ち主はマージしない、比べが違えば `untested_combination`、どの止まり方でもロックを返す |
| `land_bump_serial` | 版上げの PR の head を `sha` に渡して入れる | — | `SerialStop` を `ConfirmOutcome` の原因にする |
| `wait_landed` | マージで戻る | 上限ちょうど | 外れた → `queue_removed`（入れ直さない）、上限超え → `ci_pending` |
| `land` | 一致でキューへ入れてマージ済みで `ok` | マージ済みで `ok`、追加もマージも実行しない | head の不一致 → `head_moved`、追加もマージも実行しない |
| `merge_from_watch` | 方式に従ってマージし、項目の `admin` が偽 | `merge-queue` と `strict` | `MergeStop` を結果 JSON の `stopped` にする |
| `merge_when_green` | ブランチ戦略 `trunk` で `gh pr merge` を直接実行しない | ブランチ戦略 `whole`・`selected` で今のテストが変えずに通る | プラグインを変える承認の無い PR で止まる |
| `cmd_facts` | 承認資料に PR の head と予定の版が載る | ラベルの無い PR で PATCH の版 | ブランチ戦略が `trunk` でない → `not_applicable` |
| `cmd_land` | `--approval` の承認の記録を読む | `--approved` を直接渡す | 承認の記録が無い → `precondition` |
| `cmd_confirm` | `--out` に `save_result` の形を書く | 空の版で終了コード 3 | `confirm` の止まりを終了コード 1 にする |
| `cmd_cd` | ブランチ戦略 `trunk` で `run_cd` | ブランチ戦略 `whole` で `publish` だけ | 止まったら終了コード 1 と注釈 |
| `cmd_pr_check` | 0 を返す | 版上げの PR で 0 | 1 と注釈 |
| `pace_flow_refusal` | ブランチ戦略 `trunk` の `auto` で `None`（開発版チャネルを問わない） | ブランチ戦略 `whole` の判定が今と同じ | ブランチ戦略 `trunk` の `fast` で理由 |
| `trunk_check_steps` | 3 つの id が消える | `collect` → `pr` → …の行き先が `end` で閉じる | — |
| `plan_trunk_material` | `facts` だけの並び（`advise` で MVV 判定） | — | 宣言が足りない → `DeclError` |
| `plan_release_trunk` | 並びが 承認の記録 → `land` → `confirm` → 導入の確かめ（`--ref main`）→ `record` → `cleanup` | `auto` で先頭が MVV 判定 | `type: work`・修正ステップ・`--channel dev`・同期の PR が無い（I7・I8） |
| `trunk_waves` | `normal` で 3 ステージ、`auto` で 2 ステージ | まとめの後で空 | 開発版のステージが無い（I12） |
