# #3572 の詳細設計: ブランチ戦略ごとにリリースの振る舞いを選び、承認した head だけを本番へ入れ、同時の承認は merge queue で確かめる

設計文書 [issue-3572-design.md](issue-3572-design.md) の「構成要素」を、ファイルと関数の粒度へ下ろした文書である。
語（承認した head・head の一致の確認・承認の経路・差し戻し・fix の worker・マージの方式・キューへの投入・バージョンの置き場・
バージョン上げの PR・本番の PR・検証環境・検証用ブランチ）は設計文書の「用語」の表の意味で使う。不変条件（I1〜I18）と決定（決定 1〜26）の
番号は設計文書と [決定の記録](issue-3572-design-decisions.md) の番号である。

**起点のコード。** 実装の PR https://github.com/devbasex/ai-plugins/pull/3587 の先端 d7d5fe0b1（`origin/sprint/lrel-lock`）である。
決定 24 のとおり、この PR を閉じずに直す。「変更」「削除」は d7d5fe0b1 の時点のコードに対する差分を、「develop から戻す」は
#3587 が外した develop（2026-11-15 の `origin/develop`）の関数を今の行のために戻すことを指す。

**インタフェース。** バージョンの置き場のインタフェースは、決定 22 のインタフェースの表（`units`・`read`・`write`・`paths`）を指す。この文書は `changes` を足した 5 つで書く。

**名前。** ブランチ戦略のキーと値は #3613 の改名の後の名前（`branch_flow.release_strategy`・`merge_base_branch`・`release_branch`・`trunk`）で書く。
d7d5fe0b1 のコードは改名の前の名前を持つ。#3613 が develop へ入った後に develop を取り込み、名前をそろえる（決定 24 の 11 行目・決定 26）。
この文書の定数 `TRUNK` は、そろえた後の行 `trunk` の id を指す。

**d7d5fe0b1 のまま使うもの。** 次は d7d5fe0b1 で設計文書の振る舞いを既に持ち、この文書では関数を書かない。

| 置き場 | 持っている振る舞い |
| --- | --- |
| テンプレート `release.json`・`merge.json`・`check.json`・`close.json`・部品 `parts/steps.json` | 本番のプランの 4 ステップ（I1・I2）と、`merge` の部品の直前の `release-guard`（I6・I13） |
| `release_plan.py`・`merge_decide.py`・`records_trail.py`・`materials_decide.py`・`record.py` | テンプレートの検査・承認の経路の判定（経路 (a) と (b) の見分けを含む。I5）・人のマージの記録・作ったバージョンの記録・本文の節の差し替え・リリース記録 |
| `trail.py` の既存の関数 | 承認の証跡の読み書き・迂回の記録（キューへの投入の記録の関数だけを足す） |
| `bump_decide.py` | 上げ幅のラベル・起点の行・`bump` の動きの判定表 |
| `progress/gate/release.py` | 待ちの読み直しで本番の PR がマージ済みなら `answer --route github-merge` を呼ぶ（E7）。キューへ入れただけの PR はマージ済みと読まず、待ちを続ける |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/
│   ├── causes.json
│   ├── domains/branch-flows.json
│   └── schemas/
│       ├── declarations/{worktree,project,release}.schema.json
│       └── io/release-steps.{args,result}.schema.json
├── docs/contracts/commands.md
├── src/ndf/
│   ├── adapters/github.py
│   └── domains/release/
│       ├── assemble.py
│       ├── bump.py
│       ├── cli.py
│       ├── confirm.py
│       ├── declaration.py
│       ├── land.py
│       ├── merge_method.py *
│       ├── pr_check.py
│       ├── tagging.py
│       ├── trail.py
│       ├── verify_decide.py
│       ├── verify_ref.py
│       └── version_store.py *
└── tests/
    ├── domains/release/
    │   ├── test_release_approval.py
    │   ├── test_release_assemble.py
    │   ├── test_release_bump.py
    │   ├── test_release_confirm.py
    │   ├── test_release_merge_method.py *
    │   ├── test_release_pr_check.py
    │   ├── test_release_record.py
    │   ├── test_release_verify_ref.py
    │   └── test_release_version_store.py *
    ├── domains/worktree/test_worktree_merged.py
    └── route/test_flows.py
.github/workflows/
├── ndf-next-release.yml
└── {pytest,lint,glossary,script-structure,runtime-plugin-smoke,runtime-plugin-validate,pr-base-guard,pr-body-decisions}.yml
AGENTS.md
docs/versioning-and-distribution.md
```

**依存の向き。** 一方向で、循環は無い。純粋な判定（`merge_method`・`verify_decide`・d7d5fe0b1 の `bump_decide`・`merge_decide`）は
GitHub と git のアダプタを読まない。`version_store` は `plugins`・`changes_decide`・`version_decide`・`gather`（バージョンとタグの読みだけ）と git のアダプタに依存し、
`bump`・`land` を import しない。`gather` は `version_store` を import しない。GitHub と git を読み書きするモジュール（`land`・`assemble`・`bump`・`confirm`・`pr_check`・
`verify_ref`・`tagging`）が判定と `version_store` を呼ぶ。サブコマンドのエントリポイント（`cli`）がそれらを呼ぶ。`bump` はバージョン上げの PR の
マージに `land.Lander` を使い、`land` は `bump` を import しない。`assemble` の今の行の経路は `bump.write_bump` を使い、`bump` は `assemble` を
import しない。ruleset と merge queue の語は `adapters/github.py` と `merge_method` の中に閉じる（設計文書の「コンテキスト」の腐敗防止層）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/contracts/causes.json` | 変更 | 止まった理由の種類 `queue_removed`（キューから外れた）と `behind`（head が本番ブランチの先端を含まない）を足す | `facts/exit-codes.json` |
| `plugins/ndf-next/contracts/domains/branch-flows.json` | 変更 | 既定の行を `merge_base_branch` へ戻し、`trunk` の `default` を偽にする（決定 16） | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/worktree.schema.json` | 変更 | `branch_flow.release_strategy` に `trunk`、`branch_flow.integrate` に `release` を持つことを #3613 の改名の後の名前で確かめる | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/project.schema.json` | 変更 | `production_merge.method`（`merge-queue`・`strict`）と、検証の行の `verify.kind`・`command`・`timeout_seconds` を足す | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/release.schema.json` | 変更 | `version_store`（`kind` が `plugin` か `json`。`json` は `path`・`key`・`name`）を足す | 無し |
| `plugins/ndf-next/contracts/schemas/io/release-steps.args.schema.json` | 変更 | `assemble`・`materials` の今の行の引数を develop から戻し、`--pr` と並べる | 無し |
| `plugins/ndf-next/contracts/schemas/io/release-steps.result.schema.json` | 変更 | `outputs.method`・`outputs.store`・`outputs.kind`・今の行の `outputs.sync_pr` と、`items` の種類 `queue`・`method`・`deploy` を足す | 無し |
| `plugins/ndf-next/docs/contracts/commands.md` | 変更 | サブコマンドの一覧を新しい契約に合わせる | 無し |
| `plugins/ndf-next/src/ndf/adapters/github.py` | 変更 | merge queue の設定の読み・承認した head を渡すキューへの投入・キューのエントリの読み・レビューの判定の読み・遅れのコミット数の読みを足す | 無し |
| `plugins/ndf-next/src/ndf/domains/release/merge_method.py` | 新設 | 設定ファイルのマージの方式と ruleset の事実から、方式か食い違いの理由を返す（I15） | 無し |
| `plugins/ndf-next/src/ndf/domains/release/version_store.py` | 新設 | バージョンの置き場のインタフェースと、アダプタ `plugin`・`json`、インタフェースを使うバージョンの予定の組み立て（I18） | `plugins`・`changes_decide`・`version_decide`・`gather`（タグの読みだけ） |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 変更 | `require_github_flow` を外し、マージの方式・ベースブランチ・検証の行の種類・バージョンの置き場を読む | `ndf.contracts.facts` |
| `plugins/ndf-next/src/ndf/domains/release/trail.py` | 変更 | キューへの投入の記録の書き込みと読み取りを足す（I16） | `adapters.github`・`adapters.publish` |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | 行とマージの方式で振る舞いを選び、キューへの投入と待ち・先端の取り込みの確かめ・今の行の同期の PR を持つ | `merge_method`・`merge_decide`・`trail`・`records_trail`・`declaration` |
| `plugins/ndf-next/src/ndf/domains/release/bump.py` | 変更 | バージョンの読み書きを `version_store` のインタフェースへ移し、`trunk` でない行では何もしない | `version_store`・`bump_decide`・`gather`・`land` |
| `plugins/ndf-next/src/ndf/domains/release/pr_check.py` | 変更 | バージョンの比べを `version_store` のインタフェースへ移し、行 `trunk` でだけ見る | `version_store`・`bump`・`declaration` |
| `plugins/ndf-next/src/ndf/domains/release/confirm.py` | 変更 | `trunk` ではバージョン上げの PR・タグ・GitHub Release を、今の行ではタグと GitHub Release を待つ | `version_store`・`records_trail` |
| `plugins/ndf-next/src/ndf/domains/release/tagging.py` | 変更 | バージョンの変わった単位を `version_store` のインタフェースで読む | `version_store` |
| `plugins/ndf-next/src/ndf/domains/release/verify_decide.py` | 変更 | 種類 `deploy` のコマンドの置き換えの語を埋める | 無し |
| `plugins/ndf-next/src/ndf/domains/release/verify_ref.py` | 変更 | 検証用ブランチの組み立てを共通にし、配り方を種類（`marketplace`・`deploy`）で選ぶ | `verify_decide`・`version_store`・`declaration` |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `trunk` では本文の承認資料の節だけ、今の行では develop のリリースブランチの組み立てを打つ | `gather`・`materials_decide`・`bump`・`declaration` |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | 行・方式・置き場の読みを各サブコマンドへ渡し、結果 JSON に新しい値を載せる | 上の各モジュール |
| `plugins/ndf-next/tests/domains/release/test_release_approval.py` | 変更 | `land` の方式ごとの振る舞いと今の行の同期の PR を縛る | `land`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_assemble.py` | 変更 | 行ごとの組み立てを縛る（今の行は develop のテストを戻す） | `assemble`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_bump.py` | 変更 | 置き場 `json` のバージョン上げと、`trunk` でない行の `not_applicable` を縛る | `bump`・`version_store`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_confirm.py` | 変更 | 今の行の待ちを縛る | `confirm`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_merge_method.py` | 新設 | マージの方式と ruleset の照らし合わせを縛る | `merge_method` |
| `plugins/ndf-next/tests/domains/release/test_release_pr_check.py` | 変更 | 置き場 `json` の比べと、今の行で何も見ないことを縛る | `pr_check`・`declaration` |
| `plugins/ndf-next/tests/domains/release/test_release_record.py` | 変更 | 今の行でも作ったバージョンの記録から読むことを縛る | `record` |
| `plugins/ndf-next/tests/domains/release/test_release_verify_ref.py` | 変更 | 種類 `deploy` と、検証の行の無いリポジトリを縛る | `verify_ref`・`verify_decide` |
| `plugins/ndf-next/tests/domains/release/test_release_version_store.py` | 新設 | インタフェースの 2 つのアダプタとアダプタの選び方を縛る | `version_store` |
| `plugins/ndf-next/tests/domains/worktree/test_worktree_merged.py` | 変更 | 行 `trunk` でスプリントブランチを残さないこと、既定の行が `merge_base_branch` であることを縛る | `domains/worktree` |
| `plugins/ndf-next/tests/route/test_flows.py` | 変更 | 行 `trunk` で起点と宛先が main、設定ファイルの無いリポジトリで今の起点と宛先になることを縛る | `ndf.core.route` |
| `.github/workflows/ndf-next-release.yml` | 変更 | main への push で `bump` と `publish` をどの行でも打ち、本番ブランチ宛ての PR の出来事で `pr-check` と `verify-ref` を打つ | `plugins/ndf-next/bin/ndf` |
| `.github/workflows/pytest.yml` | 変更 | 引き金に `merge_group` を足す | 無し |
| `.github/workflows/lint.yml` | 変更 | 同上 | 無し |
| `.github/workflows/glossary.yml` | 変更 | 同上 | 無し |
| `.github/workflows/script-structure.yml` | 変更 | 同上 | 無し |
| `.github/workflows/runtime-plugin-smoke.yml` | 変更 | 同上 | 無し |
| `.github/workflows/runtime-plugin-validate.yml` | 変更 | 同上 | 無し |
| `.github/workflows/pr-base-guard.yml` | 変更 | 引き金に `merge_group` を足し、キューの一時のコミットでは PR の時点の結論を引き継いで通す | 無し |
| `.github/workflows/pr-body-decisions.yml` | 変更 | 同上 | 無し |
| `AGENTS.md` | 変更 | 「版とリリースの方針」の 11.0.0 の段落を、ai-plugins が `trunk`・`merge-queue` を選ぶことと正本の節へのリンクに合わせる（決定 12） | 無し |
| `docs/versioning-and-distribution.md` | 変更 | 節「11.0.0 からの運用」に、マージの方式・検証環境の種類・バージョンの置き場の値を足す（決定 12） | 無し |

必須のチェックを出す workflow の一覧は、実装のときに ruleset の 17 個の名前から引き直す（設計文書の「設計の結果」）。上の 8 本はその候補である。

## クラスと関数の構造

型の関係は設計文書の「構造」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/adapters/github.py` | `GitHub`・`QueueConfig`・`QueueEntry` | `merge_queue`・`enqueue`・`queue_entry`・`review_decision`・`behind_by`（新設）。`branch_rules`・`merge`・`merge_as_admin`・`in_merge_queue`・`dequeue`（変えない。`merge` は方式の無い今の行でだけ、405 でキューへ倒す今の経路を使う） |
| `plugins/ndf-next/src/ndf/domains/release/merge_method.py` | `RulesFacts`・`Method`・モジュール | `rules_of`・`decide`（新設）。定数 `QUEUE`・`STRICT`・`METHODS` |
| `plugins/ndf-next/src/ndf/domains/release/version_store.py` | `Unit`・`VersionStore`（Protocol）・`PluginStore`・`JsonStore`・`VersionStoreError`・モジュール | `PluginStore.units`・`read`・`write`・`paths`・`changes`、`JsonStore.units`・`read`・`write`・`paths`・`changes`、`of`・`plan`・`bumped`・`unit_at`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | `VerifyDecl`・`Declaration`・モジュール | `merge_method_of`・`base_branch`（新設）・`verify_of`・`_parse`（変更）・`require_github_flow`（削除）。定数 `TRUNK`（d7d5fe0b1 の行の id の定数を #3613 の改名の後の名前にそろえたもの）。`flow_of`・`review_problems`・`optional_verify_ref` は定数の名前だけが変わる |
| `plugins/ndf-next/src/ndf/domains/release/trail.py` | モジュール | `render_queue_record`・`write_queue_record`・`queued_heads`（新設）。定数 `QUEUE_MARK`・`QUEUE_HEAD_LINE` を足す。`write_admin_record`・`_counts` は変えない |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | `Lander`・`Landed` | `land`・`_await_queue`・`_bypass`・`land_bump`・`_dequeued`（変更）・`_method_of`・`_land_queue`・`_land_strict`・`_behind`（新設）・`_open_sync`・`_sync`（develop から戻す）。欄 `Lander.flow`・`Lander.method`・`Lander.base`・`Landed.sync_pr`・`Landed.method` を足す |
| `plugins/ndf-next/src/ndf/domains/release/bump.py` | `Bumper`・モジュール | `allowed_paths`・`write_bump`・`planned_for`・`Bumper.bump`・`Bumper._tip`・`Bumper._plan`・`Bumper._outside`（変更）。欄 `Bumper.store` を足す |
| `plugins/ndf-next/src/ndf/domains/release/pr_check.py` | モジュール | `check`・`_versions`（変更） |
| `plugins/ndf-next/src/ndf/domains/release/confirm.py` | `Confirmed`・モジュール | `confirm`（変更）・`_read_release_branch`（新設）。型 `PluginOf` を `UnitOf` へ改名 |
| `plugins/ndf-next/src/ndf/domains/release/tagging.py` | モジュール | `bumped`・`publish`（変更）。`_released`・`_late` は `bumped` の引数だけが変わる |
| `plugins/ndf-next/src/ndf/domains/release/verify_decide.py` | モジュール | `command_of`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/verify_ref.py` | `Verified`・モジュール | `build`（変更）・`_deploy`（新設）。欄 `Verified.kind` を足し、`Verified.version` を `str \| None` にする |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | `Target`・`Built`・`ReleaseOpen`・モジュール | `assemble`・`collect`（変更）・`_assemble_branch`・`_branch_facts`・`open_releases`・`_commit_and_push`・`_open_pr`（新設。develop から戻す）。`Target` に欄 `channel`・`level`・`base` を戻し、`pr` を `int \| None` にする。`Built` に欄 `branch` を戻す |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | モジュール | `cmd_merge`・`_merge_result`・`_moves`・`cmd_bump`・`cmd_confirm`・`cmd_pr_check`・`cmd_verify_ref`・`cmd_assemble`・`cmd_publish`・`_target`・`_target_args`（変更）・`_store_of`（新設）・`_plugin_of`（削除。`version_store.unit_at` へ移す） |

`flow_of`・`review_problems`・`optional_verify_ref`・`tagging._released`・`tagging._late`・`_target`・`_target_args` は、定数の名前か引数の受け渡しだけが変わり、
責務と失敗の扱いを変えないため「関数の仕様」には書かない。`_target_args` は develop の `--channel`・`--level`・`--plugin` を `--pr` と並べて戻す。

## 関数の仕様

置き場の列は `plugins/ndf-next/src/ndf/domains/release/` を省き、ファイル名だけを書く。`github.py` は `plugins/ndf-next/src/ndf/adapters/github.py` である。

### マージの方式と GitHub のアダプタ

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rules_of` | `merge_method.py` | 新設 | `rules_of(rules: Sequence[Mapping[str, Any]], queue: QueueConfig \| None) -> RulesFacts` | 本番ブランチに効く規則の一覧と merge queue の設定から、照らし合わせに使う事実を取り出す | `rules` は `GET /rules/branches/{branch}` の応答の行 | `queue` が None でなければ `queue_enabled` が真。型 `required_status_checks` の行があれば `strict` はその `parameters.strict_required_status_checks_policy` の真偽、無ければ None（読めない）。`review_count` は型 `pull_request` の行の `required_approving_review_count` の最大（無ければ 0） | 無し |
| `decide` | `merge_method.py` | 新設 | `decide(flow: str, declared: str \| None, facts: RulesFacts, review: str \| None) -> Method` | 行・設定ファイルの方式・ruleset の事実・PR のレビューの判定から、使う方式か止まる理由を決める（I15） | `flow` は取り込み方の表の行の id | 「処理の手順」の判定表のとおり。`Method.problem` が None のときだけ `Method.value` を使ってよい。`value` は `QUEUE`・`STRICT`・None のどれか | 無し |
| `GitHub.merge_queue` | `github.py` | 新設 | `merge_queue(self, branch: str) -> QueueConfig \| None` | 本番ブランチの merge queue の設定を GraphQL `repository.mergeQueue(branch)` で読む | 無し | キューが無ければ None。あれば `merge_method`（小文字）を持つ | GitHub の GraphQL（照会） |
| `GitHub.enqueue` | `github.py` | 新設 | `enqueue(self, number: int, expect_head: str) -> MergeOutcome` | 承認した head を `expectedHeadOid` に渡して PR をキューへ入れる（STD10） | `expect_head` は 40 桁の SHA | 既に入っていれば入れ直さずに待ち（`MERGE_QUEUE_PENDING`）。入れたら待ち。PR の head が違えば要求を送らずに `refused`、GitHub が断れば読み直した head とともに `refused` | GitHub の GraphQL（照会・`enqueuePullRequest`） |
| `GitHub.queue_entry` | `github.py` | 新設 | `queue_entry(self, number: int) -> QueueEntry` | PR の `merged`・`headRefOid`・`isInMergeQueue`・`mergeQueueEntry.state` を 1 回の照会で読む | 無し | 4 つの値を持つ。キューに無ければ `state` は None | GitHub の GraphQL（照会） |
| `GitHub.review_decision` | `github.py` | 新設 | `review_decision(self, number: int) -> str \| None` | PR の `reviewDecision` を読む | 無し | `APPROVED`・`CHANGES_REQUESTED`・`REVIEW_REQUIRED` のどれか。レビューを求めない規則なら None | GitHub の GraphQL（照会） |
| `GitHub.behind_by` | `github.py` | 新設 | `behind_by(self, base: str, head: str) -> int` | `GET /compare/{base}...{head}` の `behind_by` を読む | 無し | 0 以上の整数。0 なら head は base の先端を祖先に持つ | GitHub の REST（照会） |

### 設定ファイルとバージョンの置き場

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `merge_method_of` | `declaration.py` | 新設 | `merge_method_of(root: Path) -> str \| None` | `.ndf/project.json` の `production_merge.method` を、メインディレクトリの設定ファイルから読む | 無し | 書いていなければ None。`merge_method.METHODS` にある値ならその値。ほかの値は `DeclarationError`（理由に取れる値） | ファイルの読み込み |
| `base_branch` | `declaration.py` | 新設 | `base_branch(root: Path) -> str` | `.ndf/worktree.json` の `base_branch` を読む（今の行の同期の PR の宛先） | 無し | 書いていればその値、無ければ `production_branch(root)` と同じ値 | ファイルの読み込み |
| `verify_of` | `declaration.py` | 変更 | `verify_of(root: Path) -> VerifyDecl` | 検証の行の種類と、種類ごとのキーを読む（決定 21） | 無し | `kind` が `marketplace` なら `ref`・`marketplace`、`deploy` なら `ref`・`command`（空でない文字列の配列）・`timeout_seconds`（正の整数、省けば `DEFAULT_TIMEOUT`）を持つ。行が無い・2 つ以上・種類が表に無い・キーが欠ければ `DeclarationError`（理由に取れる種類） | ファイルの読み込み |
| `_parse` | `declaration.py` | 変更 | `_parse(raw: dict) -> Declaration` | `.ndf/release.json` の `version_store` を `Declaration.version_store` に載せる | `raw` は JSON のオブジェクト | `version_store` が無ければ None、あればオブジェクトのまま（形は `version_store.of` が見る） | 無し |
| `of` | `version_store.py` | 新設 | `of(root: Path, decl: Declaration, flow: str) -> VersionStore` | 設定ファイルの置き場からアダプタを選ぶ（I18） | 無し | `kind` が `plugin` なら `PluginStore`、`json` なら `JsonStore`。置き場が無く `flow` が `TRUNK` でなければ `PluginStore`（今の経路）。置き場が無く `TRUNK` なら、または種類が表に無い・キーが欠ければ `VersionStoreError`（理由に取れる値） | 無し |
| `PluginStore.units` | `version_store.py` | 新設 | `units(self) -> list[Unit]` | マーケットプレイスの登録の順に、プラグインの名前と置き場を単位にする | 無し | `plugins.load` の行ごとに `Unit(name, dir)` | ファイルの読み込み |
| `PluginStore.read` | `version_store.py` | 新設 | `read(self, repo_git: Git, ref: str, unit: Unit) -> str \| None` | コミット `ref` の単位のバージョンを読む | 無し | `gather.version_at` と同じ値（定義が無ければ None） | git（ls-tree・show） |
| `PluginStore.write` | `version_store.py` | 新設 | `write(self, unit: Unit, new: str) -> list[str]` | 単位のバージョンを書き、書いたパスを返す | 作業場所の単位の定義がある | `plugins.bump(root, plugin, new, version_files)` と同じパスを返す | ファイルの書き込み |
| `PluginStore.paths` | `version_store.py` | 新設 | `paths(self) -> frozenset[str]` | バージョン上げの PR が変えてよいバージョンのパスを返す | 無し | `marketplace.json`・各単位の定義のファイル・設定ファイルの `version_files` | ファイルの読み込み |
| `PluginStore.changes` | `version_store.py` | 新設 | `changes(self, paths: Sequence[str], decl: Declaration) -> list[Change]` | 変わったファイルを単位ごとの変化に分ける | 無し | `changes_decide.changes(paths, {名前: 置き場}, decl.undistributed)` と同じ | ファイルの読み込み |
| `JsonStore.units` | `version_store.py` | 新設 | `units(self) -> list[Unit]` | 設定ファイルの `name` の 1 つの単位を返す | 無し | `[Unit(name, ROOT_UNIT_DIR)]` | 無し |
| `JsonStore.read` | `version_store.py` | 新設 | `read(self, repo_git: Git, ref: str, unit: Unit) -> str \| None` | コミット `ref` の `path` の `key` の値を読む | 無し | ファイルが無ければ None。値が文字列でなければ `VersionStoreError` | git（ls-tree・show） |
| `JsonStore.write` | `version_store.py` | 新設 | `write(self, unit: Unit, new: str) -> list[str]` | `path` の `key` だけを `new` にし、ほかの鍵と並びを保って書く | ファイルがあり、`key` が文字列 | `[path]` を返す。書いた後の JSON は `key` だけが前と違う | ファイルの書き込み |
| `JsonStore.paths` | `version_store.py` | 新設 | `paths(self) -> frozenset[str]` | バージョン上げの PR が変えてよいバージョンのパスを返す | 無し | `{path}` | 無し |
| `JsonStore.changes` | `version_store.py` | 新設 | `changes(self, paths: Sequence[str], decl: Declaration) -> list[Change]` | リポジトリ全体を 1 つの単位として変化を返す | 無し | パスに `ROOT_UNIT_DIR` の接頭辞を付けて `changes_decide.split` を呼んだ結果の 1 件（`changes_decide` はディレクトリの下のパスだけを数えるため） | 無し |
| `plan` | `version_store.py` | 新設 | `plan(store: VersionStore, repo_git: Git, paths: Sequence[str], decl: Declaration, level: str, tip_ref: str) -> list[Planned]` | 範囲の変化からバージョンを上げる単位と次のバージョンを決める（`gather.plan_bumps`・`clear_shipped` をインタフェースで置き換える） | `tip_ref` を fetch 済み | 変化があり `needs_bump` が真の単位ごとに、`tip_ref` のバージョンとタグの最大より大きい次のバージョン。単位の順は `units()` の順 | git（ls-remote のタグ・show） |
| `bumped` | `version_store.py` | 新設 | `bumped(store: VersionStore, repo_git: Git, revision: str) -> list[Unit]` | コミット `revision` でバージョンが変わった単位を返す | `revision` は親を持つ | 第 1 親と `revision` で `read` の値が違う単位を `units()` の順に返す | git（show） |
| `unit_at` | `version_store.py` | 新設 | `unit_at(store: VersionStore, repo_git: Git, revision: str, version: str) -> Unit` | タグを確かめる単位を決める（d7d5fe0b1 の `cli._plugin_of` の規則） | 無し | `bumped` のうち `revision` のバージョンが `version` のもの、無ければ `bumped` の先頭、それも無ければ `units()` の先頭 | git（show） |

### マージ

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Lander.land` | `land.py` | 変更 | `land(self, number: int) -> Landed` | 本番の PR を行とマージの方式に合った経路で入れ、承認を結果に載せる | `Lander.flow` と `Lander.method` は設定ファイルから読んだ値 | マージ済みなら `_resume_merged` の結果。本番の PR なら `_method_of` を通り、`QUEUE` は `_land_queue`、`STRICT` は `_land_strict`、None は d7d5fe0b1 の `_merge` の結果。今の行で本番ブランチへ入れたら `_sync` の結果（`sync_pr` を持つ）。`Landed.method` に使った方式 | GitHub の REST・GraphQL |
| `Lander._method_of` | `land.py` | 新設 | `_method_of(self, pull: Pull) -> tuple[str \| None, Landed \| None]` | ruleset と設定ファイルを照らし、方式か止まった結果を返す | `pull` は本番の PR | `decide` の `problem` があれば `(None, Landed(False, "precondition", 理由))`。理由は設定の値と ruleset の値を並べる。ruleset を読めなければ同じく `precondition` | GitHub の REST（規則）・GraphQL（キュー・レビューの判定） |
| `render_queue_record` | `trail.py` | 新設 | `render_queue_record(sha: str) -> str` | キューへの投入の記録の本文を組む | 無し | 本文は印 `QUEUE_MARK`・見出し・行 `QUEUE_HEAD_LINE` と `` `sha` `` を持ち、承認の証跡の印を持たない。`sha` が 40 桁の SHA でなければ `ValueError` | 無し |
| `write_queue_record` | `trail.py` | 新設 | `write_queue_record(hub: GitHub, number: int, sha: str) -> tuple[bool, str]` | キューへ入れた head を本番の PR のコメントに 1 回だけ残す（I16） | `sha` はキューへ入れた head | 同じ head の記録（信用する書き手が書き、編集されていないもの）があれば書かずに `(False, その URL)`。無ければ `render_queue_record(sha)` を投稿して `(True, URL)`。読めない・書けなければ `TrailError` | GitHub の REST（コメントの一覧・作成） |
| `queued_heads` | `trail.py` | 新設 | `queued_heads(hub: GitHub, number: int) -> frozenset[str]` | 本番の PR へキューへ入れた head の集合を読む（I16） | 無し | 印 `QUEUE_MARK` を持ち、信用する書き手（`GitHub.trusts`）が書き、作成の後に編集されていないコメントの行 `QUEUE_HEAD_LINE` の SHA だけを数える（`write_admin_record` の数え方と同じ）。無ければ空。読めなければ `TrailError` | GitHub の REST（コメントの一覧） |
| `Lander._land_queue` | `land.py` | 新設 | `_land_queue(self, pull: Pull, deadline: float) -> Landed` | 停止条件を確かめた後、承認した head でキューへ入れ、マージかキューから外れるのを待つ（決定 18） | 方式が `QUEUE` | 始めに `trail.queued_heads(hub, number)` を読み、承認した head が含まれ PR がマージされておらずキューにも無ければ、`enqueue` を呼ばずに `_dequeued` の結果（I16）。含まれてキューの中なら `_await_queue` の結果。`_blocker`（迂回なし）が止めれば その結果。`enqueue(number, pull.head_sha)` が `refused` なら `refused`。入れたら `trail.write_queue_record(hub, number, pull.head_sha)` で入れた head を残し、`_await_queue` の結果。記録を書けなければ（`TrailError`）キューから外して（`dequeue`）`unknown` で止まる。`merge_as_admin` を呼ばない（I17） | GitHub の REST・GraphQL |
| `Lander._land_strict` | `land.py` | 新設 | `_land_strict(self, pull: Pull, deadline: float) -> Landed` | head が本番ブランチの先端を含むかを確かめ、停止条件の後に承認した head でマージする（決定 19） | 方式が `STRICT` | `_behind` が止めれば `behind`。`_blocker` の後に `merge(expect_head=pull.head_sha, require_method=True)` を 1 回打つ。GitHub が断れば `_behind` を打ち直し、遅れていれば `behind`、いなければ断りの種類。`merge_as_admin` を呼ばない（I17） | GitHub の REST |
| `Lander._behind` | `land.py` | 新設 | `_behind(self, pull: Pull) -> Landed \| None` | head が本番ブランチの先端を祖先に持たなければ止まった結果を返す | 無し | `behind_by(production, pull.head_sha)` が 0 なら None、1 以上なら `Landed(False, "behind", 理由)`。理由に遅れのコミット数と「本番ブランチを取り込んで承認を取り直す」 | GitHub の REST（比べ） |
| `Lander._await_queue` | `land.py` | 変更 | `_await_queue(self, pull: Pull, deadline: float, judge: bool = True) -> Landed` | キューへ入れた PR のマージを待ち、外れたら `queue_removed` で止まる | PR はキューへ入っている | マージ済みなら d7d5fe0b1 のまま `merged`。外れたら `_dequeued` の結果（`queue_removed`）。承認が失効したらキューから外して `refused`。締め切りで `ci_pending` | GitHub の GraphQL（照会・`dequeuePullRequest`） |
| `_dequeued` | `land.py` | 変更 | `_dequeued(number: int) -> Landed` | キューから外れた PR の止まった結果を組む | 無し | 種類が `queue_removed`、理由に「同じ head では入れ直さない。直しを入れた head で承認を取り直す」 | 無し |
| `Lander._bypass` | `land.py` | 変更 | `_bypass(self, pull: Pull) -> bool` | 管理者の迂回でマージするかを決める | 無し | `Lander.method` が None でなければ、本番の PR でもバージョン上げの PR でも常に偽（I17）。ほかは d7d5fe0b1 のまま | GitHub の REST（承認の数） |
| `Lander._open_sync` | `land.py` | 変更 | `_open_sync(self) -> int \| None` | 本番ブランチからベースブランチへ戻す同期の PR を使い直すか作る（develop から戻す） | `Lander.flow` が `TRUNK` でない | develop の `_open_sync` と同じ | GitHub の REST（PR の一覧・作成） |
| `Lander._sync` | `land.py` | 変更 | `_sync(self, pull: Pull, deadline: float) -> Landed` | 本番ブランチへ入れた変更を同期の PR でベースブランチへ戻す（develop から戻す） | 同上 | develop の `_sync` と同じ。`trunk` の行では呼ばれない（I10） | GitHub の REST |
| `Lander.land_bump` | `land.py` | 変更 | `land_bump(self, number: int, allowed: Callable[[Pull], list[str]]) -> Landed` | バージョン上げの PR を、本番の PR と同じマージの方式で迂回せずに入れる（決定 20） | `allowed` はバージョンの置き場・CHANGELOG・手順の書き先の外のパスを返す | `allowed` が空でなければ、入れずに d7d5fe0b1 のまま止まる。`Lander.method` が `QUEUE` なら `enqueue(number, pull.head_sha)` で入れ、`_await_queue(pull, deadline, judge=False)` の結果（外れたら `queue_removed`）。`STRICT` なら `_behind` が止めれば `behind`、止めなければ `merge(expect_head=pull.head_sha, require_method=True)` を 1 回打つ。どちらも `production_merge.admin` が真でも `merge_as_admin` を呼ばない（I17）。方式が None なら d7d5fe0b1 のまま（承認の数が 0 なら普通のマージ、ほかは迂回の設定で PR のマージの迂回）。待ちの締め切りは d7d5fe0b1 の `land_bump` と同じ | GitHub の REST・GraphQL |

### バージョン上げ・PR の検査・CD の確かめ・タグ

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `allowed_paths` | `bump.py` | 変更 | `allowed_paths(store: VersionStore, decl: Declaration) -> frozenset[str]` | バージョン上げの PR が変えてよいパスを返す | 無し | `store.paths()`・CHANGELOG・手順の書き先の和。`plugins.load` を呼ばない | ファイルの読み込み（`store` を介す） |
| `write_bump` | `bump.py` | 変更 | `write_bump(root: Path, store: VersionStore, decl: Declaration, planned: Sequence[Planned], pulls: Sequence[PullFact], today: str) -> frozenset[str]` | バージョンと CHANGELOG のバージョンの節を書き、手順を流す | `root` は書く作業場所 | 単位ごとに `store.write` を呼び、CHANGELOG の見出しは単位の名前。書いたパスを返す | ファイルの書き込み・手順の外部コマンド |
| `planned_for` | `bump.py` | 変更 | `planned_for(root: Path, hub: GitHub, number: int, production: str, store: VersionStore) -> str \| None` | 本番の PR の予定のバージョンをインタフェースで読んで求める | 無し | 単位の先頭のバージョンを `store.read` で読み、ラベルの上げ幅で上げた値。読めなければ None | git（show）・GitHub の REST（ラベル） |
| `Bumper.bump` | `bump.py` | 変更 | `bump(self) -> Bumped` | `trunk` の行で、main の先端に合わせてバージョン上げの PR を進める | 無し | 行が `TRUNK` でなければ何も読まずに `not_applicable`。`require_github_flow` を呼ばない。ほかは d7d5fe0b1 のまま（読み書きは `self.store`） | git・GitHub の REST |
| `Bumper._tip` | `bump.py` | 変更 | `_tip(self, span: Range) -> _Tip` | main の先端の単位ごとのバージョンとタグの有無をインタフェースで読む | 無し | d7d5fe0b1 と同じ形。単位は `store.units()`、変わった単位は `version_store.bumped` | git |
| `Bumper._plan` | `bump.py` | 変更 | `_plan(self, span: Range) -> _Plan` | 範囲の PR・上げ幅・予定をインタフェースで決める | 無し | 予定は `version_store.plan` の値 | git・GitHub の REST（ラベル） |
| `Bumper._outside` | `bump.py` | 変更 | `_outside(self, base: str, head: str, decl: Declaration) -> list[str]` | バージョン上げの PR の差分のうち、バージョン上げでない変更を返す（I17） | 無し | d7d5fe0b1 と同じ判定。バージョンの対は `store.read` で読む | git |
| `check` | `pr_check.py` | 変更 | `check(root: Path, hub: GitHub, repo_git: Git, number: int) -> list[dict[str, str]] \| None` | 行 `trunk` の本番ブランチ宛ての PR だけを検査する（I7・I9） | 無し | 行が `TRUNK` でなければ None。バージョンの変更を許すのは、head のブランチが `release/v` で始まり、差分がバージョンの置き場・CHANGELOG・手順の書き先に限られる PR だけ（`bump._outside` と同じ判定。I7）。置き場が読めなければ `VersionStoreError` を返す側へ渡す。比べは `_versions` の値 | git・GitHub の REST |
| `_versions` | `pr_check.py` | 変更 | `_versions(repo_git: Git, store: VersionStore, ref: str) -> dict[str, str \| None]` | 単位ごとのバージョンを、単位の名前を鍵にして読む | 無し | `{unit.name: store.read(repo_git, ref, unit)}` | git（show） |
| `confirm` | `confirm.py` | 変更 | `confirm(hub: GitHub, repo_git: Git, number: int, production: str, flow: str, unit_of: UnitOf, watch: Watch) -> Confirmed` | 行に合わせて CD がそろうのを待ち、作ったバージョンを記録する | 本番の PR がマージ済み | `TRUNK` は d7d5fe0b1 のまま。ほかは `_read_release_branch` を読み直し、そろえば記録を 1 回書く（バージョン上げの PR の欄は本番の PR の番号） | GitHub の REST・git |
| `_read_release_branch` | `confirm.py` | 新設 | `_read_release_branch(hub: GitHub, repo_git: Git, merge_sha: str, unit_of: UnitOf, store: VersionStore) -> Confirmed` | 今の行で、マージコミットのバージョンのタグと GitHub Release を 1 回読む | 無し | マージコミットのバージョンを `store.read` で読み、タグと GitHub Release があれば `kind` が None。無ければ `missing` が `publish` | GitHub の REST（参照・Release）・git（show） |
| `bumped` | `tagging.py` | 変更 | `bumped(store: VersionStore, root: Path, revision: str) -> list[Unit]` | タグを作る単位をインタフェースで読む | 無し | `version_store.bumped(store, Git(root), revision)` | git |
| `publish` | `tagging.py` | 変更 | `publish(root: Path, hub: GitHub, revision: str, store: VersionStore) -> list[Made]` | 足りないタグと GitHub Release を作る | `revision` は main のコミット | d7d5fe0b1 のまま。単位とバージョンは `store` から読む。タグの形は `<単位の名前>--v<バージョン>` | GitHub の REST・git |

### 検証環境・承認資料・サブコマンド

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `command_of` | `verify_decide.py` | 新設 | `command_of(command: Sequence[str], ref: str, head: str) -> list[str]` | 種類 `deploy` のコマンドの `{ref}`・`{head}` を置き換える | 無し | 各要素の中の 2 つの語だけを置き換え、要素の数と並びを変えない。ほかの `{...}` は残す | 無し |
| `build` | `verify_ref.py` | 変更 | `build(root: Path, hub: GitHub, decl: VerifyDecl, now: datetime, store: VersionStore) -> Verified` | 検証用ブランチを組んで fast-forward で push し、種類の配り方を打つ（I9・I12） | `root` は本番ブランチを取り出した作業場所 | 組み立てと push は d7d5fe0b1 のまま。`marketplace` は `_rewrite` のコミットを足し、`version` を持つ。`deploy` は書き換えずに push し、`_deploy` を打ち、`version` は None。`kind` に種類 | git・GitHub の REST・`deploy` の外部コマンド |
| `_deploy` | `verify_ref.py` | 新設 | `_deploy(root: Path, decl: VerifyDecl, head: str) -> None` | 置き換えたコマンドを `timeout_seconds` まで流す | 検証用ブランチの push の後 | 終了コードが 0 なら何も返さない。ほかは `VerifyDeployFailed`（終了コードと標準エラーの末尾） | 外部コマンド |
| `assemble` | `assemble.py` | 変更 | `assemble(root: Path, hub: GitHub, target: Target) -> Built` | 行に合わせて承認資料を組む（決定 6・16） | 無し | `TRUNK` は d7d5fe0b1 のまま（`target.pr` が要る）。ほかは `_assemble_branch` の結果 | git・GitHub の REST |
| `collect` | `assemble.py` | 変更 | `collect(root: Path, hub: GitHub, target: Target) -> Facts` | 行に合わせて承認資料の材料を読む | 無し | `TRUNK` は d7d5fe0b1 のまま。ほかは develop の範囲（ベースブランチか本番ブランチ..）から `_branch_facts` | git・GitHub の REST |
| `_assemble_branch` | `assemble.py` | 新設 | `_assemble_branch(root: Path, hub: GitHub, target: Target) -> Built` | 今の行のリリースブランチを組み、バージョンを上げて PR を作る（develop の `assemble`） | `target.channel` がある | develop の `assemble` と同じ。バージョンの書き込みは `bump.write_bump`（置き場は `of` が選ぶ `PluginStore`） | git・GitHub の REST・ファイルの書き込み |
| `_branch_facts` | `assemble.py` | 新設 | `_branch_facts(root: Path, hub: GitHub, target: Target, span: Range) -> Facts` | 今の行の範囲から材料を読む（develop の `_facts`） | 範囲を fetch 済み | develop の `_facts` と同じ | git・GitHub の REST |
| `open_releases` | `assemble.py` | 新設 | `open_releases(hub: GitHub, target: Target) -> list[int]` | 本番ブランチ宛ての開いたリリースブランチの PR を読む（develop から戻す） | 無し | develop と同じ | GitHub の REST |
| `_commit_and_push` | `assemble.py` | 新設 | `_commit_and_push(repo_git: Git, branch: str, message: str) -> str` | リリースブランチのコミットと push（develop から戻す） | 無し | develop と同じ。強制 push を使わない | git |
| `_open_pr` | `assemble.py` | 新設 | `_open_pr(hub: GitHub, branch: str, base: str, title: str, body: str) -> tuple[int, str]` | リリースブランチの PR を作る（develop から戻す） | 無し | develop と同じ | GitHub の REST |
| `cmd_merge` | `cli.py` | 変更 | `cmd_merge(args: Namespace) -> dict` | 行・方式・ベースブランチ・置き場を `Lander` と `planned_for` へ渡す | 無し | `require_github_flow` を呼ばない。`outputs.method` を持ち、今の行では `outputs.sync_pr` を持つ | GitHub の REST・git |
| `_merge_result` | `cli.py` | 変更 | `_merge_result(args: Namespace, done: Landed, version: str \| None = None) -> dict` | マージの結果を結果 JSON にする | 無し | d7d5fe0b1 のまま。`queue_removed` は `items` に `{"kind": "queue", "name": "removed", "detail"}`、`precondition` の方式の食い違いは `{"kind": "method", "name": 設定の値, "detail": ruleset の値}` | 無し |
| `_moves` | `cli.py` | 変更 | `_moves(args: Namespace, kind: str, released: bool) -> dict[str, Any]` | 止まったマージの次の手を返す（決定 3） | 無し | `kind` が `refused`・`queue_removed`・`behind` なら `reject` だけ。ほかは d7d5fe0b1 のまま | 無し |
| `cmd_bump` | `cli.py` | 変更 | `cmd_bump(args: Namespace) -> dict` | 置き場と方式を読んで `Bumper` を組む | 無し | `outputs.store` に置き場の種類。行が `TRUNK` でなければ置き場を読まずに `not_applicable` | git・GitHub の REST |
| `cmd_confirm` | `cli.py` | 変更 | `cmd_confirm(args: Namespace) -> dict` | 行と置き場を `confirm` へ渡す | 無し | `marketplace.json` の有無で止まらない。ほかは d7d5fe0b1 のまま | GitHub の REST・git |
| `cmd_pr_check` | `cli.py` | 変更 | `cmd_pr_check(args: Namespace) -> dict` | `pr_check.check` の結果を結果 JSON にする | 無し | 行が `TRUNK` でなければ `ok`（理由「ブランチ戦略が trunk でないので検査しない」） | git・GitHub の REST |
| `cmd_verify_ref` | `cli.py` | 変更 | `cmd_verify_ref(args: Namespace) -> dict` | 検証の行が無ければ何もせず、あれば `build` を打つ | 無し | 行が無ければ `not_applicable`。`outputs` に `ref`・`head`・`version`・`included`・`kind`。コマンドの失敗は `deploy_failed` と `{"kind": "deploy", "name": 終了コード, "detail"}` | git・GitHub の REST・外部コマンド |
| `cmd_assemble` | `cli.py` | 変更 | `cmd_assemble(args: Namespace) -> dict` | 行に合わせた引数で `assemble` を打つ | 無し | `TRUNK` は `outputs` に `pr`・`version`、ほかは develop のまま（`branch` を持つ）。行に合わない引数は `invalid` | git・GitHub の REST |
| `cmd_publish` | `cli.py` | 変更 | `cmd_publish(args: Namespace) -> dict` | 置き場を読んで `publish` を打つ | 無し | 置き場の読みに失敗すれば `precondition` | GitHub の REST・git |
| `_store_of` | `cli.py` | 新設 | `_store_of(root: Path) -> VersionStore` | 行と設定ファイルからアダプタを選ぶ（`version_store.of` を呼ぶ） | 無し | `of(root, declaration.load(root), flow_of(root))` の値 | ファイルの読み込み |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `GitHub.merge_queue` | 照会が失敗した | ネットワーク・権限 | `GitHubError` | 呼び出し元へ返す | `land` が `precondition`（ruleset を読めない） | 何も変えていない。打ち直せる |
| `GitHub.enqueue` | head が違う | 承認の後の push | 送る前の PR の読み・GraphQL の断り | 呼び出し元へ返す（`refused`） | `stopped`・`refused`・動いた先の SHA。次の手は `reject` だけ（終了コード 1） | 入れていない。新しい head で承認を取り直す |
| `GitHub.enqueue` | 投入を断られた | 保護の条件を満たさない・キューが無い | GraphQL の誤り | 呼び出し元へ返す（`refused`） | `stopped`・`refused`・GitHub の理由 | 入れていない。ruleset を人が見る |
| `GitHub.queue_entry` | 照会が失敗した | ネットワーク | `GitHubError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown` | キューの中はそのまま。打ち直すと待ちを続ける |
| `GitHub.review_decision` | 照会が失敗した | ネットワーク | `GitHubError` | 呼び出し元へ返す | `land` が `precondition` | 何も変えていない |
| `GitHub.behind_by` | 比べが失敗した | ネットワーク・head の取り違え | `GitHubError` | 呼び出し元へ返す | `land` が `precondition` | 何も変えていない |
| `merge_method_of` | 表に無い値 | 設定ファイルの誤り | 値の確かめ | 止める（`DeclarationError`） | `stopped`・`precondition`・取れる値 `merge-queue`・`strict` | 設定ファイルを直して打ち直す |
| `base_branch` | 設定ファイルが壊れている | JSON の誤り | `_read_json` | 止める（`DeclarationError`） | `stopped`・`precondition` | 同上 |
| `verify_of` | 行が 2 つ以上・種類が表に無い・キーが欠ける | 設定ファイルの誤り | 行と鍵の確かめ | 止める（`DeclarationError`） | `stopped`・`precondition`・取れる種類 `marketplace`・`deploy` | 同上 |
| `PluginStore.units` | 登録が読めない | `marketplace.json` が無い・壊れている | `PluginsError` | 呼び出し元へ返す（`VersionStoreError` に包む） | `stopped`・`precondition`・「置き場 plugin は .claude-plugin/marketplace.json を要る」 | 設定ファイルの置き場を直す |
| `PluginStore.read` | 定義が読めない | JSON の誤り | `PluginsError` | 呼び出し元へ返す | `stopped`・`precondition` | 何も変えていない |
| `PluginStore.write` | 定義に版が無い・2 本の定義がそろわない | 定義の誤り | `PluginsError` | 呼び出し元へ返す | `stopped`・`precondition` | push していない。取り出しを捨てる |
| `PluginStore.paths` | 定義が無い | 登録とディレクトリの食い違い | `PluginsError` | 呼び出し元へ返す | `stopped`・`precondition` | 何も変えていない |
| `PluginStore.changes` | 登録が読めない | 同上 | `PluginsError` | 呼び出し元へ返す | `stopped`・`precondition` | 何も変えていない |
| `JsonStore.read` | 値が文字列でない・JSON が壊れている | ファイルの誤り | `json.loads`・型の確かめ | 止める（`VersionStoreError`） | `stopped`・`precondition`・パスと鍵 | 何も変えていない |
| `JsonStore.write` | ファイルが無い・鍵が無い | 設定ファイルの `path`・`key` の誤り | 読み込みと型の確かめ | 止める（`VersionStoreError`） | `stopped`・`precondition`・パスと鍵 | 書いていない |
| `plan` | タグを読めない | ネットワーク | `GitError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown` | 何も変えていない。打ち直せる |
| `bumped` | 親が読めない | 根のコミット | `GitError` | 既定値で続ける（空） | タグを作らない | 無し |
| `unit_at` | 単位が 1 つも無い | 置き場の誤り | 空の一覧 | 止める（`VersionStoreError`） | `stopped`・`precondition` | 何も変えていない |
| `Lander.land` | 方式が無い・食い違う・レビューの承認が要る | 設定ファイルと ruleset の食い違い | `_method_of` | 止める | `stopped`・`precondition`・理由に設定の値と ruleset の値 | マージもキューへの投入もしていない（I15）。設定か ruleset を直して打ち直す |
| `Lander.land` | 必須のチェックが落ちた・head が動いた | CI の失敗・承認の後のコミット | `_blocker`（d7d5fe0b1 のまま） | 止める | `stopped`・種類と落ちたチェック | 何も変えていない（I4） |
| `Lander._method_of` | ruleset を読めない | 権限・古い保護だけを使うリポジトリ | `GitHubError`・`strict` が None | 止める | `stopped`・`precondition`・「ruleset を読めない」 | 何も変えていない。設計文書の「未確認のまま残ること」の行 |
| `write_queue_record` | 記録を読めない・書けない | ネットワーク・権限 | `GitHubError` | 呼び出し元へ返す（`TrailError`） | `_land_queue` が扱う（下の行） | 何も書いていない |
| `queued_heads` | 記録を読めない | ネットワーク・権限 | `GitHubError` | 呼び出し元へ返す（`TrailError`） | `_land_queue` が扱う（下の行） | 何も変えていない |
| `Lander._land_queue` | 投入の記録を読めない | `queued_heads` の `TrailError` | `TrailError` | 止める（`enqueue` を呼ばない） | `stopped`・`unknown`・「キューへの投入の記録を読めない」。次の手は `rerun` | 何も変えていない。打ち直せる |
| `Lander._land_queue` | キューへ入れた後に投入の記録を書けない | `write_queue_record` の `TrailError` | `TrailError` | キューから外して（`dequeue`）止める | `stopped`・`unknown`・「キューへの投入の記録を書けない」。次の手は `rerun` | キューから外した。記録が無いため、打ち直しは同じ head を初めての投入として入れる（外したのは工程の側で、キューのチェックは結論を出していない）。外すのにも失敗すれば PR はキューの中のままで、打ち直しは `isInMergeQueue` を見て待ちを続ける |
| `Lander._land_queue` | キューへ入れられない | head の食い違い・保護の条件 | `enqueue` の `refused` | 止める | `stopped`・`refused`。次の手は `reject` だけ | 入れていない |
| `Lander._land_queue` | キューから外れた | 組み合わせのチェックの失敗・衝突・取り外し | `_await_queue` | 止める | `stopped`・`queue_removed`・`items` の `queue`。次の手は `reject` だけ | 同じ head で入れ直さない（I16）。打ち直しでも投入の記録で外れたと見分ける。直しを入れた head で承認を取り直す |
| `Lander._land_queue` | 待ちが切れた | キューの CI が長い | `_await_queue` の締め切り | 止める | `stopped`・`ci_pending`。次の手は `rerun`（キューへは入れ直さない） | キューの中のまま。打ち直すと待ちを続け、マージ済みなら記録へ進む |
| `Lander._land_strict` | head が先端を含まない | 先に別の PR がマージされた | `_behind` | 止める | `stopped`・`behind`・遅れのコミット数。次の手は `reject` だけ | マージしていない。本番ブランチを取り込んだ head で承認を取り直す |
| `Lander._land_strict` | マージを断られた | 確かめとマージの間に本番ブランチが動いた | `merge` の断り | `_behind` を打ち直して止める | `behind`（遅れていれば）か断りの種類 | マージしていない |
| `Lander._behind` | 比べを読めない | `behind_by` の失敗 | `GitHubError` | 呼び出し元へ返す | `stopped`・`precondition` | 何も変えていない |
| `Lander._await_queue` | 承認が失効した | 待ちの間の push か差し戻し | `_queued_refusal` | キューから外して止める | `stopped`・`refused` | キューから外した。新しい head で承認を取り直す |
| `Lander._await_queue` | キューを読めない | ネットワーク | `GitHubError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown` | キューの中のまま。打ち直せる |
| `Lander._bypass` | 承認の数を読めない | 権限 | `approvals` が None | 既定値で続ける（偽） | 迂回しない（今の規則） | 無し |
| `Lander._open_sync` | 同期の PR を作れない | 権限・GitHub の誤り | `GitHubError`（差分なしの 422 を除く） | 呼び出し元へ返す | `stopped`・`unknown`・「本番は出ている」 | 本番は出ている。打ち直すとマージ済みからの再開で同期の PR へ進む |
| `Lander._sync` | 同期の PR のチェックが落ちた | ベースブランチとの衝突など | `_merge` | 止める | `stopped`・種類・`outputs.sync_pr` | 本番は出ている。同期の PR を人が見る |
| `Lander.land_bump` | キューから外れた | キューの一時のコミットでチェックが落ちた・衝突 | `_await_queue` | 止める | `stopped`・`queue_removed`。`bump` はタグを作らない | バージョン上げの PR は開いたまま。次の main への push の `bump` が取り込みを足すか作り直す（バージョン上げの PR は承認を持たず I16 の対象外。`step=bump` の打ち直しは同じ head を入れ直す） |
| `Lander.land_bump` | head が main の先端を含まない・マージを断られた | `strict` で、確かめの前か後に別の PR が main へ入った | `_behind`・`merge` の断り | 止める | `stopped`・`behind` | マージしていない。次の main への push の `bump` が main を取り込むコミットを足す |
| `allowed_paths` | 置き場のパスを読めない | 置き場の誤り | `VersionStoreError` | 呼び出し元へ返す | `stopped`・`precondition` | 何も変えていない |
| `write_bump` | 置き場へ書けない・手順が落ちた | 置き場の誤り・手順のコマンドの失敗 | `VersionStoreError`・`StepFailed` | 呼び出し元へ返す | `stopped`・`precondition` か `check_failed` | push していない。取り出しを捨てる |
| `planned_for` | 置き場かラベルを読めない | 置き場の誤り・GitHub の誤り | `VersionStoreError`・`GitHubError`・`VersionError` | 既定値で続ける（None） | `outputs.version` が無い | 無し。バージョンは `confirm` が確定する |
| `Bumper.bump` | 置き場が無い | `trunk` で `version_store` を書いていない | `of` の `VersionStoreError` | 止める | `stopped`・`precondition`・取れる種類 `plugin`・`json` | 何もしていない（I18） |
| `Bumper._tip` | 置き場を読めない | 置き場の誤り | `VersionStoreError` | 呼び出し元へ返す | `stopped`・`precondition` | 何もしていない |
| `Bumper._plan` | ラベルが 2 つ以上 | PR の宣言の誤り | `LevelError` | 呼び出し元へ返す | `stopped`・`invalid` | ラベルを直してから `step=bump` で打ち直す |
| `Bumper._outside` | 差分を読めない | git の誤り | `GitError` | 呼び出し元へ返す | `stopped`・`precondition` | マージしていない |
| `check` | 置き場を読めない | 設定ファイルの誤り | `VersionStoreError` | 呼び出し元へ返す | `stopped`・`precondition`（PR のチェックが落ちる） | 設定ファイルを直すと次の出来事で打ち直される |
| `_versions` | 置き場を読めない | 同上 | `VersionStoreError` | 呼び出し元へ返す | 同上 | 同上 |
| `confirm` | 待ちの内にそろわない | `bump` か `publish` が止まった・遅れた | 締め切り | 止める | `stopped`・`deploy_failed`・「本番はマージ済み」と足りないもの。次の手は `rerun` の 1 つ | 何も書いていない。workflow を打ち直してから `confirm` を打ち直す |
| `_read_release_branch` | マージコミットのバージョンを読めない | 置き場の誤り | `VersionStoreError` | 呼び出し元へ返す | `stopped`・`precondition` | 何も書いていない |
| `tagging.bumped` | 置き場を読めない | 置き場の誤り | `VersionStoreError` | 呼び出し元へ返す | `publish` が `precondition` | 何も作っていない |
| `publish` | タグか GitHub Release を作れない | GitHub の誤り | `GitHubError` | 呼び出し元へ返す（d7d5fe0b1 のまま） | `stopped`・`infra_wait` か `unknown` | 作れた分は残る。打ち直すと足りない分だけを作る |
| `build` | PR の取り込みが衝突した | 2 本の PR が同じ箇所を変えた | `VerifyConflict` | 止める（d7d5fe0b1 のまま） | `stopped`・`base_conflict`・衝突した PR | 検証用ブランチを動かしていない |
| `build` | `deploy` のコマンドが落ちた | 配り方の失敗 | `_deploy` の `VerifyDeployFailed` | 止める | `stopped`・`deploy_failed`・`items` の `deploy` | 検証用ブランチは push のまま残す。次の出来事か手動の起動で打ち直す |
| `_deploy` | 時間切れ・終了コードが 0 でない | コマンドの失敗 | `subprocess` の時間切れと終了コード | 止める（`VerifyDeployFailed`） | 同上 | 子プロセスを止める。打ち直せる |
| `assemble` | 行に合わない引数 | `trunk` で `--pr` が無い・今の行で `--channel` が無い | 引数の確かめ | 止める（`AssembleError`） | `stopped`・`invalid` | 何もしていない |
| `collect` | 同上 | 同上 | 同上 | 止める | `stopped`・`invalid` | 何も変えていない |
| `_assemble_branch` | リリースブランチの PR が開いている・同じ名前のブランチがある | 前の組み立ての残り | `open_releases`・`ls-remote`（develop のまま） | 止める（`ReleaseOpen`・`AssembleError`） | `stopped`・`precondition` | develop のまま |
| `_branch_facts` | 上げるものが無い | 範囲に変化が無い | `clear_shipped` の空 | 止める（`NothingToRelease`） | `stopped`・`not_applicable` | 何もしていない |
| `open_releases` | 一覧を読めない | GitHub の誤り | `GitHubError` | 呼び出し元へ返す | `stopped`・`unknown` | 何もしていない |
| `_commit_and_push` | push を断られた | 同じ名前のブランチが同時に作られた | `GitError` | 呼び出し元へ返す | `stopped`・`precondition` | develop のまま |
| `_open_pr` | PR を作れない | 権限・GitHub の誤り | `GitHubError` | 呼び出し元へ返す | `stopped`・`unknown` | ブランチは残る。打ち直すと同じ名前で止まり、人が片付ける（develop のまま） |
| `cmd_merge` | マージが止まった | `land` の行のどれか | `Landed.kind` | 呼び出し元へ返す | `land` の行と同じ | 同上 |
| `_merge_result` | 止まった理由の種類が無い | `land` の誤り | `done.kind` が空 | 既定値で続ける（`unknown`） | `stopped`・`unknown` | 無し |
| `_moves` | 種類が止まった理由の表に無い | 新しい種類 | `merge_decide.suggested` | 既定値で続ける（`reject`） | 既定の手が `reject` | 無し |
| `cmd_bump` | バージョン上げが止まった | `Bumper.bump` の行のどれか | `_ERRORS` と `GitHubError` | 呼び出し元へ返す | 同上。バージョン上げの PR のコメントにも理由 | 同上 |
| `cmd_confirm` | 確かめが止まった | `confirm` の行のどれか | `Confirmed.kind` | 呼び出し元へ返す | 同上 | 同上 |
| `cmd_pr_check` | 検査で破れが出た | バージョンの変更・ラベル 2 つ・head が検証用ブランチ | `check` の戻り値 | 止める | `stopped`・`check_failed`・終了コード 1 | PR を直すと次の出来事で打ち直される |
| `cmd_verify_ref` | 組み直しが止まった | `build` の行のどれか | `_ERRORS` | 呼び出し元へ返す | `build` の行と同じ | 同上 |
| `cmd_assemble` | 組み立てが止まった | `assemble` の行のどれか | `_ERRORS` | 呼び出し元へ返す | 同上 | 同上 |
| `cmd_publish` | 置き場を読めない | 設定ファイルの誤り | `VersionStoreError` | 止める | `stopped`・`precondition` | 何も作っていない |
| `_store_of` | 置き場が選べない | 置き場の誤り | `VersionStoreError` | 呼び出し元へ返す | `stopped`・`precondition` | 何も変えていない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `merge_method.decide` | 行が `TRUNK`・方式が None | 問題「trunk ではマージの方式が要る」（I15） |
| `merge_method.decide` | 行が `TRUNK` でない・方式が None | `value` None（今のマージ） |
| `merge_method.decide` | 方式が `merge-queue`・`queue_enabled` が偽 | 問題「ruleset に merge queue が無い」 |
| `merge_method.decide` | 方式が `merge-queue`・キューのマージの方式が `merge` でない | 問題「キューのマージの方式が merge commit でない」（`land.MERGE_METHOD`） |
| `merge_method.decide` | 方式が `strict`・`queue_enabled` が真 | 問題「ruleset に merge queue があり、strict と食い違う」 |
| `merge_method.decide` | 方式が `strict`・`strict` が偽か None | 問題「必須のチェックの最新の取り込みが偽か読めない」 |
| `merge_method.decide` | 方式があり食い違いが無く、`review_count` が 1 以上・`review` が `APPROVED` でない | 問題「本番ブランチの規則がレビューの承認を求め、PR に承認が無い」（決定 17） |
| `merge_method.decide` | どれでもない | `value` が方式 |
| `Lander.land` | マージ済み | `_resume_merged`（d7d5fe0b1 のまま。今の行では続けて `_sync`） |
| `Lander.land` | 本番の PR でない（バージョン上げの PR・同期の PR） | 方式を読まずに d7d5fe0b1 の `_merge` |
| `Lander.land` | 本番の PR・`QUEUE` / `STRICT` / None | `_land_queue` / `_land_strict` / `_merge`（None で迂回の設定があれば迂回） |
| `Lander.land` | 今の行で本番ブランチへ入った | `_sync`（`sync_pr`） |
| `Lander._await_queue` | `merged` が真 | `merged`（経路は `_released` の `merge_decide.decide` が head で見分ける） |
| `Lander._await_queue` | `merged` が偽・`isInMergeQueue` が偽 | 読み直して `merged` なら `merged`、偽なら `queue_removed` |
| `Lander._await_queue` | キューの中・承認が失効 | キューから外して `refused` |
| `Lander._await_queue` | キューの中・締め切りの前 / 後 | `QUEUE_POLL_SECONDS` 待つ / `ci_pending` |
| `Lander.land_bump` | 方式 `QUEUE` / `STRICT` / None で承認の数 0 / None でそれ以外 | 承認なしの `enqueue` と待ち（`judge` が偽） / 先端を含めば head の一致の確認付きのマージ、含まなければ `behind` / 普通のマージ（キューへ倒れたら止める） / d7d5fe0b1 のまま PR のマージの迂回 |
| `version_store.of` | 置き場あり / 置き場なし・`TRUNK` でない / 置き場なし・`TRUNK` | 種類のアダプタ / `PluginStore` / `VersionStoreError` |
| `Bumper.bump`・`check` | 行が `TRUNK` でない | 何も読まない（`not_applicable` / None） |
| `confirm` | 行が `TRUNK` / でない | バージョン上げの PR・タグ・Release を待つ / マージコミットのバージョンのタグと Release を待つ |
| `verify_ref.build` | 種類 `marketplace` / `deploy` | `_rewrite` のコミットを足して push / 書き換えずに push し `_deploy` |
| `cmd_verify_ref` | 検証の行が無い | `not_applicable`（`build` を呼ばない） |
| `_moves` | `refused`・`queue_removed`・`behind` / ほか | `reject` だけ / `rerun`・`reject` |

- 繰り返しの終わる条件: `_await_queue` と `confirm` は締め切り（`clock()` が始めの時刻 + `budget` を超える）で終わる。キューのエントリの読み直しは、マージ済みか外れたかで終わる。`_land_strict` のマージは 1 回だけ打ち、断られても打ち直さない
- 計算量の注意: `_land_queue` の待ちは読み直し 1 回ごとに GraphQL を 1 回（`queue_entry`）と、`judge` のとき承認の記録の照会を打つ。間隔は `QUEUE_POLL_SECONDS`（30 秒）で、`ci_budget` を 3,600 秒にすると最大 120 回になる。`_method_of` は `land` の 1 回につき REST 1 回（規則の全ページ）と GraphQL 2 回

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `QUEUE`・`STRICT` | 定数 | `merge_method.py` | `"merge-queue"`・`"strict"` |
| `METHODS` | 定数 | `merge_method.py` | `(QUEUE, STRICT)`。設定ファイルのスキーマの列挙はこれと同じ値で、契約のテストが一致を確かめる |
| `RulesFacts` | 型（`queue_enabled: bool`・`queue_method: str \| None`・`strict: bool \| None`・`review_count: int`） | `merge_method.py` | — |
| `Method` | 型（`value: str \| None`・`problem: str \| None`・`declared: str \| None`・`actual: str`） | `merge_method.py` | `actual` は ruleset の値を人の読める 1 行にしたもの |
| `QUEUE_MARK`・`QUEUE_HEAD_LINE` | 定数 | `trail.py` | `<!-- {workflow_namespace}:queue-entry -->`（`ADMIN_MARK` と同じく `identity.FIXED_WORDS` から組む）・`"キューへ入れた head:"` |
| `QueueConfig` | 型（`merge_method: str \| None`） | `github.py` | — |
| `QueueEntry` | 型（`merged: bool`・`head: str`・`in_queue: bool`・`state: str \| None`） | `github.py` | — |
| `_MERGE_QUEUE_CONFIG`・`_QUEUE_ENTRY`・`_REVIEW_DECISION` | 定数（GraphQL の照会） | `github.py` | `_ENQUEUE`（d7d5fe0b1 のまま、`expectedHeadOid` を持つ）と並べる |
| `Lander.flow`・`Lander.method`・`Lander.base` | 欄 | `land.py` | `flow` は `declaration.flow_of`、`method` は `merge_method_of`、`base` は `base_branch` の値（`cli` が渡す） |
| `Landed.sync_pr`・`Landed.method` | 欄 | `land.py` | None |
| 種類 `queue_removed`・`behind` | 設定値 | `contracts/causes.json` | 区分は `refused` と同じ（`exit` が `precondition`・`route` が `conductor`・`retry` が null）。`route` が `retry` でないため `merge_decide.suggested` は `reject` を返す |
| `Unit` | 型（`name: str`・`dir: str`） | `version_store.py` | — |
| `KINDS` | 定数 | `version_store.py` | `("plugin", "json")`。スキーマの列挙と同じ値 |
| `ROOT_UNIT_DIR` | 定数 | `version_store.py` | `"."`（`JsonStore` の単位をリポジトリ全体にする置き場） |
| `Declaration.version_store` | 欄 | `declaration.py` | None |
| `VerifyDecl` | 型（`kind: str`・`ref: str`・`marketplace: str \| None`・`command: tuple[str, ...]`・`timeout_seconds: int`） | `declaration.py` | `command` は空、`timeout_seconds` は `DEFAULT_TIMEOUT`（600） |
| `VERIFY_KINDS` | 定数 | `declaration.py` | `("marketplace", "deploy")` |
| `PLACEHOLDERS` | 定数 | `verify_decide.py` | `("{ref}", "{head}")` |
| `TRUNK` | 定数 | `declaration.py` | `facts.ids("branch-flows", release_strategy="trunk")[0]`。表の行の id を写さない |
| 既定の行 | 設定値 | `contracts/domains/branch-flows.json` | 行 `merge_base_branch` の `default` が真、`trunk`・`release_branch` は偽 |
| `UnitOf` | 型（`Callable[[str, str], Unit]`） | `confirm.py` | `cli` が `version_store.unit_at` を包んで渡す |

`version_store` の設定ファイルの値（`.ndf/release.json`）は設計文書の「設定ファイルの形」の表の JSON である。ai-plugins の値の書き込みは 11.0.0 の入れ替えの PR が行い、
この実装では書かない（決定 23）。

workflow `ndf-next-release.yml` のジョブは、d7d5fe0b1 の表から次の 2 点を変える。どのジョブも `vars.NDF_RELEASE_RUNNER == 'ndf-next'` のときだけ動く。

| ジョブ | 変えること |
| --- | --- |
| `bump`・`publish` | 行で分けない。`bump` は `trunk` でない行で `not_applicable` を返し、`publish` は main への push のコミットに足りないタグを作る（今の行のタグの作り手。決定 16） |
| `verify-ref` | 検証の行が無ければ `not_applicable` で通る。種類 `deploy` のコマンドは、本番ブランチから取り出した設定ファイルのものだけを流す（設計文書の「非機能の実現方式」） |

必須のチェックを出す workflow には `merge_group:`（`types: [checks_requested]`）を足す。PR の本文か宛先を読む `pr-base-guard.yml` と `pr-body-decisions.yml` の
ジョブは、`github.event_name == 'merge_group'` のときに PR を読まない。「キューの一時のコミットは PR の時点の結論を引き継ぐ」と出して 0 で終わる（決定 23）。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つのレーンの `land` が、ほぼ同時に承認された A と B をキューへ入れる | main・merge queue | GitHub の merge queue が組み合わせと順を決める（STD7）。`land` は排他を取らない | B は「main + A + B」で確かめられる。A が外れても B のレーンは何もしない。打ち直しは `isInMergeQueue` が真なら入れ直さない |
| `land` の `strict` のマージと、別の PR のマージ | main の先端 | GitHub の最新の取り込みの規則が後のマージを断る。`land` は迂回しない（I17） | 断られたら `behind` で止まる。取り込んだ head の承認の後に打ち直す |
| 本番のプランの `merge` と、人の GitHub でのマージかキューへの投入 | 本番の PR | head の一致の確認（STD1・STD10）。先にマージした側が勝つ | 打ち直すと、マージ済みからの再開で経路を head で見分ける（I5） |
| main への push ごとの `bump` と `publish` | バージョン上げのブランチ・タグ | workflow の `concurrency`（`ndf-next-release-main`、`cancel-in-progress: false`） | d7d5fe0b1 のまま。`publish` はあるタグを作らない |
| バージョン上げの PR のマージと、本番の PR のマージ | main の先端 | `merge-queue` は両方をキューへ入れ、GitHub が組み合わせと順を決める（STD7）。`strict` は最新の取り込みの規則が後のマージを断る。どちらも迂回しない（I17） | キューから外れれば `queue_removed`、遅れていれば `behind` で止まる。次の push の `bump` が取り込みを足す |
| `verify-ref` の組み直しどうし | 検証用ブランチ | workflow の `concurrency`（`ndf-next-verify`）と fast-forward の push | d7d5fe0b1 のまま。`deploy` のコマンドは同じグループの中で 1 本ずつ流れる |
| `confirm` と CD | バージョン上げの PR・タグ | 排他を取らない。`confirm` は読むだけで、書くのは作ったバージョンの記録 1 件 | 同じバージョンの記録があれば書かない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_merge` | 使った方式・承認（経路・承認した人・承認した head・マージした head・マージコミット・承認ゲートの head）・落ちたチェック・キューから外れた理由・方式の食い違い（設定の値と ruleset の値）・次の手・今の行の同期の PR | 結果 JSON の `outputs`・`items` | token |
| `Lander._land_queue` | キューへ入れた head と、マージかキューから外れたこと | 結果 JSON の `summary` | token |
| `Lander._method_of` | 設定の値と ruleset の値（キューの有無・`strict`・承認の数） | 止まった結果の理由 | ruleset の迂回の対象の一覧 |
| `cmd_bump` | 置き場の種類・上げたバージョン・上げ幅・バージョン上げの PR・`state` | 結果 JSON の `outputs` と、止まったときはバージョン上げの PR のコメント | token |
| `cmd_confirm` | 作ったバージョン・タグ・バージョン上げの PR（今の行は本番の PR）。止まったときは足りないもの | 結果 JSON と、そろったときは本番の PR のコメント | token |
| `cmd_verify_ref` | 種類・組んだ検証用ブランチと head・含めた PR・`deploy` の終了コードと標準エラーの末尾 20 行 | 結果 JSON の `outputs`・`items` | token・コマンドの環境変数・取り込んだ PR の中身 |
| `cmd_pr_check` | 破れの種類・単位の名前かラベル・理由 | 結果 JSON の `items` | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `rules_of` | `land._review_count`・`github.ruleset_merge_methods` | d7d5fe0b1 の `land.py` と `github.py` を読んだ | `_review_count` の読み方を使う（`land` から `merge_method` へ移し、`land.approvals` もそれを呼ぶ）。`ruleset_merge_methods` は許すマージの方式だけを読むため使えない |
| `decide` | `github.pick_merge_method` | `github.py` を読んだ | 使えない（PR のマージの方式の選択で、ruleset の照らし合わせではない） |
| `GitHub.merge_queue` | `GitHub._enqueue` の照会 `_MERGE_QUEUE_STATE` | `github.py` を読んだ | 照会の `mergeQueue { configuration { mergeMethod } }` の部分と `_queue_merge_method` を使う |
| `GitHub.enqueue` | `GitHub._enqueue`・`_ENQUEUE`・`_preflight` | `github.py` を読んだ | `_ENQUEUE`（`expectedHeadOid`）と `_preflight` を使う。`_enqueue` は 405 の後の倒し先で、承認した head を引数に取らないため、方式の無い今の行の経路として残す |
| `GitHub.queue_entry` | `GitHub.in_merge_queue` | `github.py` を読んだ | 使えない（`isInMergeQueue` だけを読む）。照会の形を足す |
| `GitHub.review_decision` | 無し | `grep -rn reviewDecision plugins/ndf-next/src/ndf`（d7d5fe0b1）の結果が 0 件 | 照会を足す |
| `GitHub.behind_by` | `GitHub.compare_commits` | `github.py` を読んだ | 使えない（コミットの一覧を返し、`behind_by` を捨てる）。同じ照会の応答の `behind_by` を返す |
| `merge_method_of` | `declaration.production_admin`・`_trusted_project_path` | d7d5fe0b1 の `declaration.py` を読んだ | 使う（メインディレクトリの設定ファイルから読み、PR の作業場所の設定で方式を選べないようにする） |
| `base_branch` | `declaration.production_branch` | 同上 | 読み方（`BRANCH_FILE`・`_read_json`）を使う |
| `of` | `plugins.load`・release-please の release type（PRD1） | `plugins.py` と設計文書の「外部の調査」 | 種類で選ぶ形だけを倣う。`plugins.load` は `PluginStore` の中だけで呼ぶ |
| `PluginStore.units` | `plugins.load` | `plugins.py` を読んだ | 使う |
| `PluginStore.read` | `gather.version_at` | `gather.py` を読んだ | 使う |
| `PluginStore.write` | `plugins.bump` | `plugins.py` を読んだ | 使う |
| `PluginStore.paths` | `bump.allowed_paths` の前半 | d7d5fe0b1 の `bump.py` を読んだ | 中身を移す |
| `PluginStore.changes` | `gather.changes` | `gather.py` を読んだ | 中身を移す（`gather.changes` は今の行の `assemble` が使い続けるため消さない） |
| `JsonStore.units` | 無し | — | 単位 1 つを返すだけ |
| `JsonStore.read` | `gather._versions_at` の `git show` | `gather.py` を読んだ | 打ち方を使い直す |
| `JsonStore.write` | `plugins._write_json` | `plugins.py` を読んだ | 使えない（`plugins` の内部の関数）。同じ書式（`indent=2`・`ensure_ascii=False`・末尾の改行）で書く |
| `JsonStore.paths` | 無し | — | パス 1 つを返すだけ |
| `JsonStore.changes` | `changes_decide.split` | d7d5fe0b1 の `changes_decide.py` を読んだ（`_under` はディレクトリの接頭辞で切る） | 使う。パスに `ROOT_UNIT_DIR` の接頭辞を付けて渡す |
| `plan` | `gather.plan_bumps`・`gather.shipped`・`gather.clear_shipped`・`version_decide.next_version` | `gather.py` を読んだ | `clear_shipped` と `version_decide` を使う。`plan_bumps`・`shipped` は `plugins` を呼ぶため、同じ手順をインタフェースで書く |
| `bumped` | `tagging.bumped` | d7d5fe0b1 の `tagging.py` を読んだ | 中身をインタフェースへ移し、`tagging.bumped` はこれを呼ぶ |
| `unit_at` | `cli._plugin_of` | d7d5fe0b1 の `cli.py` を読んだ | 中身を移す（`cli._plugin_of` は消す） |
| `render_queue_record` | `trail.render_admin_record` | `trail.py` を読んだ | 形（印・見出し・SHA の行・SHA の確かめ）を倣う。迂回の記録とは印を分ける（迂回の記録を投入の記録に数えない） |
| `write_queue_record` | `trail.write_admin_record` | 同上 | 同じ head の記録を探して書かない手順と、`GitHubError`・`ValueError` を `TrailError` へ包む形を使い直す。照らした証跡の URL は持たない |
| `queued_heads` | `trail._counts`・`write_admin_record` の数え方 | 同上 | 数え方（信用する書き手・編集なし）を使う。`_counts` は担い手の判定を持つため、`write_admin_record` と同じ `not c.edited and hub.trusts(c)` で数える |
| `Lander._method_of` | `Lander.approvals` | `land.py` を読んだ | 規則の読み（`branch_rules`）を共有し、承認の数は `rules_of` が読む |
| `Lander._land_queue` | `Lander._blocker`・`_await_queue` | `land.py` を読んだ | 使う |
| `Lander._land_strict` | `Lander._blocker`・`GitHub.merge` | 同上 | 使う |
| `Lander._behind` | `ndf.adapters.git.Git.is_ancestor` | `git.py` を読んだ | 使えない（本番の作業場所は PR の head を fetch していない）。`behind_by` を使う |
| `_read_release_branch` | `confirm._tagged`・`confirm._has_release` | d7d5fe0b1 の `confirm.py` を読んだ | 使う |
| `command_of` | `declaration.run_step` の `{version}` の置き換え | `declaration.py` を読んだ | 置き換えの形（要素ごとの `str.replace`）を使い直す |
| `_deploy` | `declaration.run_step` | 同上 | 使えない（`Step` を受け、手順の失敗を `StepFailed` で返す）。`subprocess.run` を時間切れ付きで打つ形を使い直す |
| `_assemble_branch` | develop の `assemble.assemble`・`bump.write_bump` | develop と d7d5fe0b1 の `assemble.py`・`bump.py` を読んだ | develop の手順を戻し、develop の `_write` の代わりに `bump.write_bump` を使う（同じ書き込みを 2 か所に持たない） |
| `_branch_facts` | develop の `assemble._facts` | develop の `assemble.py` を読んだ | 戻す |
| `open_releases` | develop の `assemble.open_releases` | 同上 | 戻す |
| `_commit_and_push` | develop の `assemble._commit_and_push`・`Bumper._commit`・`Bumper._push` | 同上と d7d5fe0b1 の `bump.py` | develop の関数を戻す（`Bumper` のメソッドはバージョン上げのブランチの欄を読むため使えない） |
| `_open_pr` | develop の `assemble._open_pr` | develop の `assemble.py` を読んだ | 戻す |
| `_store_of` | `version_store.of` | — | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `rules_of` | キュー・`strict` 真・承認 1 の規則から 3 つの値 | 規則が空で `strict` が None・承認 0 | 型の違う行を数えない |
| `decide` | 判定表の 8 行をそれぞれ返す | 今の行で方式 None は問題なし | `trunk` で方式 None・キューなし・`strict` 偽・承認が要るのに無い、のそれぞれで `problem` |
| `GitHub.merge_queue` | 設定を読む | キューが無ければ None | 照会の誤りが届く |
| `GitHub.enqueue` | 承認した head を `expectedHeadOid` に渡す（記録した変数で見る） | 既に入っていれば投入を呼ばない | head が違えば投入を呼ばずに `refused`。GraphQL の断りで `refused` |
| `GitHub.queue_entry` | 4 つの値を読む | キューに無ければ `state` が None | 照会の誤りが届く |
| `GitHub.review_decision` | `APPROVED` を返す | null で None | 照会の誤りが届く |
| `GitHub.behind_by` | 遅れの数 | 0 | 照会の誤りが届く |
| `merge_method_of` | `merge-queue` を返す | 書いていなければ None | 表に無い値で `DeclarationError` |
| `base_branch` | `develop` を返す | 無ければ本番ブランチ | 壊れた JSON で `DeclarationError` |
| `verify_of` | `marketplace` と `deploy` の行をそれぞれ読む | `timeout_seconds` を省けば 600 | 種類が表に無い・`deploy` に `command` が無い・行が 2 つで `DeclarationError` |
| `_parse` | `version_store` を載せる | 無ければ None | — |
| `of` | `plugin`・`json` を選ぶ | 置き場なしの今の行で `PluginStore` | 置き場なしの `trunk`・表に無い種類で `VersionStoreError` |
| `PluginStore.units` | 登録の順の単位 | — | 登録が無ければ `VersionStoreError` |
| `PluginStore.read` | `gather.version_at` と同じ値 | 定義が無ければ None | 壊れた定義で誤りが届く |
| `PluginStore.write` | `plugin.json` と `version_files` を書く | — | 定義がそろわなければ誤りが届く |
| `PluginStore.paths` | 定義と登録のパス | `version_files` が空 | — |
| `PluginStore.changes` | 単位ごとの変化 | 変化なしで空の変化 | — |
| `JsonStore.units` | 設定の名前の 1 単位 | — | — |
| `JsonStore.read` | `package.json` の `version` | ファイルが無ければ None | 数値の値で `VersionStoreError` |
| `JsonStore.write` | `version` だけが変わり鍵の並びが保たれる | — | 鍵が無ければ `VersionStoreError` |
| `JsonStore.paths` | `{path}` | — | — |
| `JsonStore.changes` | 根の下のどのパスも数える | 配らないファイルだけなら `needs_bump` が偽 | — |
| `plan` | MINOR のラベルで X.(Y+1).0 | タグが main より大きければタグの次 | タグを読めなければ誤りが届く |
| `bumped` | バージョンの変わった単位 | 変わらなければ空 | 根のコミットで空 |
| `unit_at` | バージョンの一致する単位 | 一致が無ければ変わった先頭、それも無ければ登録の先頭 | 単位が無ければ `VersionStoreError` |
| `Lander.land` | `QUEUE` で `enqueue`、`STRICT` で `merge`、None で今のマージ | 今の行で `sync_pr` を持ち、`trunk` で同期の PR を作らない（I10） | 方式の食い違いで `merge` も `enqueue` も呼ばない（受け入れ条件 24） |
| `Lander._method_of` | 食い違いなしで方式 | — | ruleset を読めなければ `precondition` |
| `render_queue_record` | 印と SHA の行を持ち、承認の証跡の印を持たない | — | 40 桁でない SHA で `ValueError` |
| `write_queue_record` | 記録を 1 件書く | 同じ head の記録があれば書かない | 信用しない書き手か編集された同じ head の記録があっても書く。投稿の誤りで `TrailError` |
| `queued_heads` | 信用する書き手の記録の SHA を返す | 記録が無ければ空 | 信用しない書き手の記録と、作成の後に編集された記録を数えない。迂回の記録の SHA を数えない |
| `Lander._land_queue` | A・B を承認した head で入れ、両方のマージの記録が残り head は変わらない（受け入れ条件 22） | キューへ入れた後に PR が B でマージ済みなら人のマージの記録（`gate_head` が A）で `refused` にしない | 迂回の設定が真でも `merge_as_admin` を呼ばない（受け入れ条件 26）。外れたら `queue_removed` で `enqueue` を 2 回呼ばない（受け入れ条件 25）。投入の記録があってキューに無い PR で打ち直すと、`enqueue` を呼ばずに `queue_removed`。偽の書き手の投入の記録だけがある PR では `enqueue` を呼ぶ。記録を書けなければ `dequeue` を呼んで `unknown` |
| `Lander._land_strict` | 先端を含む head をマージする | マージを断られた後に遅れていれば `behind` | 先端を含まなければ `merge` を呼ばずに `behind`（受け入れ条件 23）。迂回を呼ばない |
| `Lander._behind` | 遅れ 0 で None | — | 遅れ 1 で `behind` |
| `Lander._await_queue` | マージで `merged` | 外れた直後の読み直しでマージ済みなら `merged` | 外れて `queue_removed`、承認の失効で `dequeue` を呼んで `refused`、締め切りで `ci_pending` |
| `_dequeued` | 種類 `queue_removed` | — | — |
| `Lander._bypass` | 方式なしの今の行で d7d5fe0b1 のまま | — | 方式のある本番の PR で偽 |
| `Lander._open_sync` | develop のテストを戻す | 差分なしで None | 422 以外の誤りが届く |
| `Lander._sync` | develop のテストを戻す | — | 同期の PR のチェックが落ちて止まる |
| `Lander.land_bump` | 方式 `QUEUE` でバージョン上げの PR の head を渡して `enqueue` を 1 回呼び、承認を照会せずに待つ。`STRICT` で先端を含む head を `merge` する | 方式なし・承認 0 で普通のマージ | `QUEUE`・`STRICT` で迂回の設定が真でも `merge_as_admin` を呼ばない（受け入れ条件 26）。置き場の外のパスで `enqueue` も `merge` も呼ばない。`STRICT` で先端を含まなければ `merge` を呼ばずに `behind`。外れたら `queue_removed` |
| `allowed_paths` | 置き場 `json` で `package.json` と手順の書き先 | — | `plugins.load` を呼ばない（アダプタの差し替えで記録した読み取りで見る。I18） |
| `write_bump` | 置き場 `json` で `package.json` だけを上げ、手順で `package-lock.json` を書く | CHANGELOG が無ければバージョンだけ | 置き場の誤りが届く |
| `planned_for` | 置き場から読んだ予定 | — | 読めなければ None |
| `Bumper.bump` | 置き場 `json` で MINOR のバージョン上げの PR（受け入れ条件 21） | 今の行で `not_applicable` | `trunk` で置き場なしは `precondition` |
| `Bumper._tip` | 単位ごとのバージョン | バージョン上げの履歴が無ければ `tagged` が真 | — |
| `Bumper._plan` | 範囲の PR の最大の上げ幅 | 変化なしで空 | ラベル 2 つで誤りが届く |
| `Bumper._outside` | バージョンの行だけの差分で空 | — | 置き場の中のバージョン以外の変更を返す |
| `check` | 置き場 `json` で `package.json` のバージョンの変更を破れにする | 今の行で None | 置き場の誤りが届く |
| `_versions` | 単位の名前を鍵にする | — | — |
| `confirm` | 今の行でマージコミットのタグと Release がそろえば記録を 1 回書く | 打ち直しで記録を 2 件にしない | 締め切りで `deploy_failed`・`missing` が `publish` |
| `_read_release_branch` | タグと Release があれば `kind` が None | — | タグが無ければ `publish` |
| `tagging.bumped` | 置き場の単位 | — | — |
| `publish` | 置き場 `json` で `<名前>--v<バージョン>` のタグ | 今の行で本番の PR のマージコミットにタグを作る（受け入れ条件 18） | — |
| `command_of` | `{ref}`・`{head}` を置き換える | 語の無い要素は変わらない | ほかの `{...}` を残す |
| `build` | 種類 `deploy` でコマンドが置き換えて呼ばれ、`marketplace.json` を読まない（受け入れ条件 20） | — | `deploy` のコマンドが落ちても検証用ブランチは push のまま |
| `_deploy` | 終了コード 0 で何も返さない | — | 時間切れと終了コード 1 で `VerifyDeployFailed` |
| `assemble` | `trunk` で本文の節だけ、今の行でリリースブランチと PR（受け入れ条件 18） | — | 行に合わない引数で `AssembleError` |
| `collect` | 行ごとの範囲 | — | 同上 |
| `_assemble_branch` | develop のテストを戻す | — | 開いたリリースブランチの PR で `ReleaseOpen` |
| `_branch_facts` | develop のテストを戻す | 変化なしで `NothingToRelease` | — |
| `open_releases` | develop のテストを戻す | 0 本で空 | — |
| `_commit_and_push` | develop のテストを戻す | — | push に強制の指定が無い |
| `_open_pr` | develop のテストを戻す | — | 誤りが届く |
| `cmd_merge` | `outputs.method` と、今の行で `outputs.sync_pr` | — | `require_github_flow` 相当の停止が今の行で出ない（受け入れ条件 18） |
| `_merge_result` | `queue`・`method` の項目 | — | — |
| `_moves` | ほかの種類で 2 つの手 | — | `refused`・`queue_removed`・`behind` で `reject` だけ |
| `cmd_bump` | `outputs.store` | 今の行で `not_applicable` | 止まった例外が種類つきの `stopped` |
| `cmd_confirm` | `marketplace.json` の無いリポジトリで止まらない | — | 止まったとき次の手が `rerun` の 1 つ |
| `cmd_pr_check` | 破れなしで 0 | 今の行で 0 | 破れで 1 |
| `cmd_verify_ref` | `outputs.kind` | 検証の行が無ければ `not_applicable` | コマンドの失敗で `deploy_failed` |
| `cmd_assemble` | 行ごとの `outputs` | — | 行に合わない引数で `invalid` |
| `cmd_publish` | 置き場を渡す | — | 置き場の誤りで `precondition` |
| `_store_of` | `of` の値 | — | — |
