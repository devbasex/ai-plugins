# #3672 の削除の一覧と、置き換える設計・子の Issue の行き先

設計文書 [issue-3672-design.md](issue-3672-design.md) から分けた文書である。決定の番号は
[issue-3672-design-decisions.md](issue-3672-design-decisions.md) を、不変条件の番号（`I<数>`）は設計文書の「ドメインモデル」を指す。

この文書は受け入れ条件 1・21・25 の材料を持つ。

| セクション | 受け入れ条件 |
| --- | --- |
| 削除の一覧 | 21（CI の検査が各行を照らす） |
| 置き換える設計と開いている設計 PR の行き先 | 1 |
| 子の Issue の行き先 | 25 |

## 削除の一覧

### 数えた範囲と、数えたコマンド

旧い宣言のキーを読む・書く・説明する箇所は、次のコマンドで数えた（2026-11-16、このブランチの先端 c77111045）。

| 範囲 | コマンド | 結果 |
| --- | --- | --- |
| 10 系のコード（テストを除く） | `grep -rlE 'branch_flow\|production_merge\|version_store\|base_branch\|production_branch\|promotion' plugins/ndf/scripts --include=*.py --include=*.sh \| grep -v /tests/` | 55 ファイル |
| 10 系の `delivery`・`release.dev_workflow` を読むコード | `grep -rlE "dev_workflow\|\"delivery\"\|'delivery'" plugins/ndf/scripts --include=*.py \| grep -v tests` | 8 ファイル（うち 6 は上と重なる。新しいのは `lib/parallel_route.py`・`release_lib/reviewed.py`） |
| 11.0 のコード | `grep -rlE '<上と同じ語>\|"delivery"\|\.delivery\|dev_workflow' plugins/ndf-next/src --include=*.py` | 21 ファイル |
| 11.0 のスキーマ・表 | `grep -rcE '<上と同じ語>' plugins/ndf-next --include=*.json` | 12 ファイル・38 行 |
| 文書と Skill の本文 | `grep -rcE '<上と同じ語>'` を `plugins/ndf/skills`・`plugins/ndf-next`・`docs`・ルートの文書へ | 10 系の Skill 23 ファイル、11.0 26 ファイル、`docs/` 27 ファイル、`AGENTS.md` 2 行 |
| テスト | 同じ語を `plugins/ndf/scripts/tests`・`plugins/ndf/skills/*/tests`・`plugins/ndf-next/tests`・`scripts/tests` へ | 67・24・20・4 ファイル |

`version_store` は、コード・スキーマ・テスト・CI のどこにも無い（`grep -rn version_store .` の当たりは用語集 2 行と #3572 の設計文書だけ）。
#3572 の設計が足すと決めたキーで、まだ実装されていないためである。新しいセクションの `release.version_store` はこの名前を引き継ぐ（決定 2）。

ほかの `scripts/repo-assumptions-allow/` の例外は、一覧に載せない。`worktree.schema.json`・`production-merge.md`・`project-analysis.md`・`declaration.md` を指す例外で、指す先のファイルが残るためである。
書き直した後に当たりが無くなった例外は、実装の PR で消す。

複数のファイルにまたがる行（D32・D33・D34・D37）のパスは、[issue-3672-design-removal-paths.md](issue-3672-design-removal-paths.md) に 1 ファイル 1 行で並べた。
その文書は語の集合に `dev_workflow`・`delivery` を足して数え直したため、上の表の件数と違う。

`plugins/ndf/scripts/lib/gh_pr_info.py` と `plugins/ndf/skills/cross-review/scripts/` の当たりは、一覧に載せない。
どちらも PR の宛先（`baseRefName`）を入れる変数の名前で、宣言を読んでいない。

### 一覧

「対象」の列が「ファイルを消す」の行は、ファイルごと消す。ほかの行は、ファイルに残る旧いキーの読み・書き・説明を消し、新しいセクションを読む部品（「置き換え先」）へ替える。

**宣言の実ファイル（ai-plugins の `.ndf/`。C7）**

| # | パス | 対象 | 置き換え先 |
| --- | --- | --- | --- |
| D1 | `.ndf/worktree.json` | `base_branch`・`production_branch`・`branch_flow`（`start`・`integrate`・`doc_prs`・`promotion`・`reviews`） | `.ndf/project.json` の `branch_strategy` |
| D2 | `.ndf/project.json` | `production_merge`・`delivery` | 同じファイルの `branch_strategy.merge`・`branch_strategy.release.deliveries` |
| D3 | `.ndf/supervise.json` | `release`（`form`・`plugin`・`runtimes`・`dev_workflow`） | `branch_strategy.release`・`branch_strategy.channels` |
| D4 | `.ndf/release.json` | `version_files`（ai-plugins の実物には無い。利用者のリポジトリで移す対象） | `branch_strategy.release.version_store.files` |

**スキーマと表**

| # | パス | 対象 | 置き換え先 |
| --- | --- | --- | --- |
| D5 | `plugins/ndf-next/contracts/schemas/declarations/worktree.schema.json`・`plugins/ndf-next/skills/worktree/schemas/worktree.schema.json`・`plugins/ndf/skills/worktree/schemas/worktree.schema.json` | `base_branch`・`production_branch`・`branch_flow` の定義 | `project.schema.json` の `$defs/BranchStrategy` |
| D6 | `plugins/ndf-next/contracts/schemas/declarations/project.schema.json`・`plugins/ndf-next/skills/development-workflow/schemas/project.schema.json`・`plugins/ndf/skills/development-workflow/schemas/project.schema.json` | `production_merge`・`delivery` の定義 | 同上 |
| D7 | `plugins/ndf-next/contracts/schemas/declarations/supervise.schema.json`・`plugins/ndf-next/contracts/schemas/declarations/release.schema.json`・`plugins/ndf/skills/release/schemas/release.schema.json` | `release`（supervise）・`version_files`（release）の定義 | 同上 |
| D8 | `plugins/ndf-next/contracts/domains/branch-flows.json` | 列 `promotion`・行 `promote-whole`・`promote-selected` | #3613 の改名表のとおり列 `release_strategy`・行 `merge-base-branch`・`release-branch`・`trunk` |
| D9 | `plugins/ndf-next/contracts/domains/ndf-files.json` | project・worktree・worktree-local のキーの一覧の旧いキー | 新しいセクションのキー |
| D10 | `plugins/ndf-next/contracts/schemas/io/release-steps.args.schema.json`・`worktree.result.schema.json`・`worktree.args.schema.json`・`project.result.schema.json`・`plugins/ndf-next/contracts/templates/params.json` | 引数と結果の説明の旧いキー | 新しいセクションのキー |

**10 系のコード（`plugins/ndf/scripts/`）**

| # | パス | 対象 | 置き換え先 |
| --- | --- | --- | --- |
| D11 | `lib/branch_flow.py` | ファイルを消す（取り込み方の表 `PROMOTIONS`・`of_decl`・スナップショット・スキーマの生成） | `lib/branch_strategy.py`（新設。読む部品の 1 つ） |
| D12 | `lib/repo.py`・`lib/worktree-branch.sh`・`lib/worktree-declaration.sh`・`lib/decl.py`・`lib/delivery.py`・`lib/project_decl.py`・`lib/release_case.py` | `base_branch`・`production_branch`・`branch_flow.start`・`production_merge`・`delivery` の読み | `lib/branch_strategy.py`。シェルは `branch_strategy.py get <キー>` を呼ぶ |
| D13 | `lib/parallel_repo.py`・`lib/parallel_lane.py`・`lib/parallel_external.py`・`lib/parallel_release.py`・`lib/parallel_route.py`・`lib/gate1_requests.py`・`lib/default_branch_candidates.py` | 同上の読みと、`branch_flow` の定数の参照 | 同上 |
| D14 | `project-decl.py`・`project_lib/__init__.py`・`project_lib/merge.py`・`project_lib/model.py`・`project_lib/fingerprint.py`・`project_lib/measure_repo.py` | 旧い 4 か所の書き出し（`merge_worktree`・`merge_flow`・`delivery_channels`）とモデル | `project_lib/branch_strategy_write.py`（新設。作る・更新する・消す） |
| D15 | `project_lib/measure_branch_flow.py` | ファイルを消す | `project_lib/branch_strategy_measure.py`（新設。値ごとの根拠を出す） |
| D16 | `supervise_lib/decl.py`・`review_units.py`・`release_flow.py`・`steps.py`・`plan.py`・`paths.py`・`new_args.py`・`pr.py`・`slow.py`・`release_templates.py`・`delivery_templates.py`・`release_cd.py`・`sprint.py`・`sprint_routes.py`・`sprint_waves.py` | 旧いキーの読みと `flow_snapshot`（スプリントの開始時のスナップショット） | `lib/branch_strategy.py`。スナップショットはセクションの全体を持つ（決定 9） |
| D17 | `release_lib/promotion.py` | ファイルを消す（行 `selected` のリリースブランチの組み立てを行の名前で分ける） | `release_lib/strategy_route.py`（新設。`release_strategy` の値で分ける） |
| D18 | `release_lib/review_range.py`・`included.py`・`names.py`・`approval_trail.py`・`context.py`・`reviewed.py` | 旧いキーと `promotion`・`dev_workflow`・`form` の読み | `lib/branch_strategy.py` |
| D19 | `release-steps.py`・`merged-steps.py`・`pr-steps.py`・`pr_steps_lib/gh_pr.py`・`test-run.py`・`doc-lint.py`・`check-trigger.py`・`experimental/issue-body.py` | 旧いキーの読み | `lib/branch_strategy.py` |
| D20 | `worktree-setup.sh`・`worktree-session.sh` | `base_branch`・`production_branch`・`branch_flow.start` の読みと、init の案内の文 | `branch_strategy.py get`。案内は作成のコマンドを示す |
| D21 | `plugins/ndf/skills/pr-review/scripts/pr-review-steps.py` | `existing_base_branch` 経由の読み | `lib/branch_strategy.py` |

**11.0 のコード（`plugins/ndf-next/src/ndf/`）**

| # | パス | 対象 | 置き換え先 |
| --- | --- | --- | --- |
| D22 | `domains/worktree/declaration.py`・`repo.py`・`create.py`・`merged_decide.py`・`merged.py`・`cli.py` | 旧いキーの読みと開始時のスナップショット | `core/branch_strategy.py`（新設。読む部品の 1 つ） |
| D23 | `domains/project/measure_flow.py`・`write.py`・`cli.py`・`check.py`・`measure_repo.py` | 旧い 4 か所の測定と書き出し | `domains/project/strategy_measure.py`・`strategy_write.py`（新設） |
| D24 | `domains/release/declaration.py`・`land.py`・`trail.py`・`cli.py` | `production_merge.admin` と release.json・supervise.json の読み | `core/branch_strategy.py` |
| D25 | `domains/hooks/strict.py` | `delivery` の読み | 同上 |
| D26 | `core/route/__init__.py`・`progress/command.py` | `base_branch`・`production_branch` の読み | 同上 |

`domains/worktree/follow.py`・`session_decide.py`・`__init__.py` は `follow_branch` だけを読む。`follow_branch` は worktree の振る舞いの設定で `.ndf/worktree.json` に残し（決定 1）、追従先のブランチ名は新しいセクションの `branches.base` から読む。
`domains/records/mvv.py`・`mvv_decide.py` の当たりは引数の名前 `base_branch` で、宣言を読んでいない。2 つとも一覧に載せない。

**CI と補助のスクリプト**

| # | パス | 対象 | 置き換え先 |
| --- | --- | --- | --- |
| D27 | `scripts/check-pr-base.sh`・`.github/workflows/pr-base-guard.yml` | 宛先のブランチの `.ndf/worktree.json` の `branch_flow.promotion`・`base_branch` の読み | `branch_strategy.py get` で宛先のブランチのセクションを読む |
| D28 | `.github/workflows/ndf-next-release.yml`（99〜102 行目）・`.github/workflows/release-dev.yml`（43 行目） | jq での `.base_branch`・`.production_branch` の読み | 同上。`release-dev.yml` は ai-plugins が開発版をやめる（ai-plugins の値）ので消す |
| D29 | `scripts/ci-heavy-skip.py`・`scripts/check-ndf-contracts.py`・`scripts/contracts_lib/ratchet.py`・`scripts/ndf-next-parity/parity/fixes.json` | 旧いキーの読みと期待値 | 新しいセクションのキー |
| D30 | `scripts/repo-assumptions-allow/plugins__ndf__scripts__lib__branch_flow.py--quoted-develop--not-a-violation.json`・`scripts/repo-assumptions-allow/plugins__ndf__scripts__lib__branch_flow.py--quoted-main--not-a-violation.json`・`scripts/repo-assumptions-allow/plugins__ndf__scripts__lib__branch_flow.py--release-branch--not-a-violation.json`・`scripts/script-structure-allow/plugins__ndf__scripts__lib__branch_flow.py--lines.json` | ファイルを消す（指す先の `lib/branch_flow.py` を D11 で消す） | 新しいファイルで当たれば同じ形で足す |

**文書と Skill の本文**

| # | パス | 対象 | 置き換え先 |
| --- | --- | --- | --- |
| D31 | `plugins/ndf/skills/worktree/references/declaration.md`・`plugins/ndf-next/skills/worktree/references/declaration.md` | ブランチ運用の宣言のセクション（宣言のセクションを手で書く手順を含む） | 作成のコマンドを打つ手順と、新しいセクションを読む案内（受け入れ条件 7） |
| D32 | 10 系の Skill の本文とエージェントの定義 24 ファイル（パスは [issue-3672-design-removal-paths.md](issue-3672-design-removal-paths.md) の D32-1〜D32-24） | 旧いキーの説明 | 新しいセクションのキーの説明 |
| D33 | 11.0 の Skill の本文・契約の文書（`plugins/ndf-next/docs/contracts/`）・エージェントの定義 26 ファイル（パスは同じ文書の D33-1〜D33-26） | 同上 | 同上 |
| D34 | `docs/specifications/` の確定仕様 20 ファイル（パスは同じ文書の D34-1〜D34-20） | 旧いキーの説明 | この課題の確定仕様（`plan-to-spec`）が置き換え、旧い説明を消す |
| D35 | `docs/glossary/glossary.json`・`docs/glossary.md` | 旧いキーを語として持つ項目 | 新しいセクションの語（設計文書の「用語」の表） |
| D36 | `AGENTS.md`（19〜20 行目）・`docs/versioning-and-distribution.md` | `base_branch`・`follow_branch` の説明と、develop と開発版のチャネルの説明 | ai-plugins の値（設計文書の「ai-plugins の値」）の説明。C7 |

**テスト**

| # | パス | 対象 | 置き換え先 |
| --- | --- | --- | --- |
| D37 | `plugins/ndf/scripts/tests/` の 71 ファイル・`plugins/ndf/skills/*/tests/` の 23 ファイル・`plugins/ndf-next/tests/` の 23 ファイル・`scripts/tests/` の 4 ファイル（パスは同じ文書の D37a〜D37d） | 旧いキーを入力にした宣言の fixture | 新しいセクションの fixture。代表のリポジトリ 5 種の宣言は 1 か所の fixture にまとめる（テスト設計） |

`branch_flow.py` を消すと、D12〜D21 の 10 系の読み手の参照は `branch_strategy.py` 1 本へ寄る。旧い分岐（行 `whole`・`selected` の名前で振る舞いを分ける `if`）は D11・D16・D17・D18・D27 にあり、`release_strategy` の値で分ける形へ替える。

### CI の検査が照らす形

一覧の各行は、実装で `scripts/legacy-branch-decl.json`（新設）の 1 行になる。行は「パス」と「消す語」を持ち、
検査 `scripts/check-legacy-branch-decl.py`（新設）は、そのパスに語が 1 件でも残れば落ちる。パスのファイルが無いときは通す（ファイルごと消した行）。
消す語は、宣言のキーとして読む形（`"base_branch"`・`'base_branch'`・`.base_branch`）と、文書で宣言のキーとして書く形（`` `base_branch` ``）である。

旧いキーの名前が残ってよいのは、次の置き場だけである（I8）。検査はこれらを一覧に載せない。

| 置き場 | 理由 |
| --- | --- |
| 10 系の移行のコマンド `plugins/ndf/scripts/project_lib/branch_strategy_migrate.py`（新設）とそのテスト | 旧い 4 か所を読んで新しいセクションへ移す |
| 10 系の旧い宣言の検査 `plugins/ndf/scripts/lib/branch_strategy_legacy.py`（新設）とそのテスト | 旧いキーの位置を出して止める（受け入れ条件 22） |
| 11.0 の移す行の表 `plugins/ndf-next/contracts/domains/ndf-settings-renames.json`（#3613 かこの課題が新設。決定 3）と、10 系のそのコピー `plugins/ndf/scripts/data/ndf-settings-renames.json` | 旧いキーの位置と移す先を持つ |
| 11.0 の移行のコマンド `plugins/ndf-next/src/ndf/domains/project/strategy_migrate.py` と旧い宣言の検査 `plugins/ndf-next/src/ndf/core/branch_strategy_legacy.py`（どちらも新設）とそのテスト | 10 系の 2 行と同じ |
| `CHANGELOG.md`・`docs/archive/`・`docs/development-history/`・`issues/` | 書いた日の記録で、以後の構成変更に追随しない |

## 置き換える設計と開いている設計 PR の行き先

| 設計 | この設計が置き換える部分 | 残る部分と行き先 |
| --- | --- | --- |
| #3119（閉じた） | ai-plugins のブランチ運用（スプリントブランチを main から切り、リリースブランチで届ける） | 無し。ai-plugins の値は設計文書の「ai-plugins の値」が持つ |
| #3121（閉じた） | ブランチ運用の宣言の形（起点・結合・リリースの組み方） | 無し。不合格の条件 7・10 は #3309・#3310 が持ち、この課題の子で解く |
| #3572（`issues/issue-3572-design*.md`、PR #3628 でマージ済み） | 設定ファイルの形（3 か所に分けた形）・行の値・ai-plugins の値 | 承認した先端だけをマージする仕組み（本番のプランから worker を外す・head の一致の確認・`merge-queue` と `strict` の判定・人のマージの記録）は、この設計の I5〜I7 と決定 6・7 が取り込む。11.0 の実装はこの課題の 11.0 の読み手の部分で行う |
| 設計 PR #3649（#3572 の出し直し、開いたまま） | 宣言を 1 つのセクションにまとめる差分（handoff `branch-strategy-one-file.md`）と ai-plugins の値 | **閉じる。** 取り込む部分は上の行と同じ。#3119 の設計の差分（決定 21・25）も、この設計の ai-plugins の値が置き換える |
| #3641（設計 PR #3650、開いたまま） | 10 系の GitHub flow の行・設定のキー `promotion`・ai-plugins の行き先・マージの方式 `serial` | **設計 PR #3650 は閉じる。** 10 系の `trunk` の行と承認した先端のマージはこの設計の決定 6・7 が取り込む。`serial`（NDF が取るロック）は採らない（決定 7）。#3641 の Issue の残り（ai-plugins の main の ruleset の変更と、develop と開発版の停止）は、受け入れ条件 24 の前提なのでこの課題の中で行う（設計文書の「ai-plugins だけの事情」） |
| #3576 | ndf-next の常設のリリースブランチの取り込み方の宣言 | 宣言は `release_strategy` の値と `branches` が持つ。#3572 の設計の決定 13 のとおり閉じたまま |
| #3615 | リリースの形と版数の置き場を宣言で選ぶ | 宣言は `release.form`・`release.version_store` が持つ。CD を起動する workflow のテンプレート（`json` の版数の置き場で CD を回す形）は #3615 に残る |
| #3506（閉じた） | 管理者の迂回のマージの宣言の置き場・ndf-next・ai-plugins の値 | 宣言は `merge.admin` が持つ（I6） |
| #1338 | `--admin`・merge commit・develop と main の 2 本への縛り | 宣言は `merge.admin`・`merge.commit`・`release_strategy` が持つ。製品の既定は迂回を使わない |

## 子の Issue の行き先

「この Issue で解決」は、この課題の受け入れ条件か削除の一覧が持つ。「子として後で直す」は、この課題の後に別の PR で直す。

| Issue | 系 | 行き先 | 理由 |
| --- | --- | --- | --- |
| #1755 | 10 系 | 子として後で直す | CHANGELOG のセクションの差し替えの不具合で、宣言に依らない |
| #1787 | 10 系 | 子として後で直す | merge-when-green の終了コードの文書化で、宣言に依らない |
| #2060 | 10 系 | この Issue で解決 | `release/references/release-steps.md` は D32 で書き直す。範囲の PR と承認の材料の打ち方を同じ書き直しで足す |
| #2166 | 10 系 | この Issue で解決 | 受け入れ条件 17（テスト）と 24（ai-plugins の実機）が確かめる |
| #2783 | 10 系 | 子として後で直す | まとめた正式版のリリース記録の書き手で、宣言に依らない |
| #3151 | 10 系 | 子として後で直す | 並列のまとめたリリースの組み立てで、`release_branch` の行の振る舞いの不足 |
| #3156 | 10 系 | この Issue で解決 | 起点と宛先を `branches` から読む（D13） |
| #3175 | 10 系 | 子として後で直す | 承認の後の待ちの不具合で、宣言に依らない |
| #3276 | 10 系 | この Issue で解決 | 開発版のデプロイを承認ゲートにしない（受け入れ条件 19 の 5 種のテスト） |
| #3309 | 10 系 | この Issue で解決 | 受け入れ条件 16 の CI の検査が持つ |
| #3310 | 10 系 | この Issue で解決 | スプリントの開始時にセクションの全体のスナップショットを取る（決定 9） |
| #3387 | 10 系 | 子として後で直す | 既定ブランチの解き方の依存で、宣言に依らない |
| #3401 | 10 系 | 子として後で直す | 設計だけのスプリントの PR の作り方 |
| #3481 | 10 系 | 子として後で直す | 消えた作業場所から gh を呼ぶ不具合 |
| #3573 | 10 系 | 子として後で直す | 直しは PR #3575 にあり、残りはリリース後テスト |
| #3598 | 10 系 | この Issue で解決 | 組んだ版のスナップショットと今のセクションを同じ形で比べる（決定 9） |
| #3603 | 10 系 | 子として後で直す | 設計承認ゲートの問いの種類の不具合 |
| #3608 | 10 系 | この Issue で解決 | 受け入れ条件 18（証跡の書き手を 1 つにする） |
| #3611 | 10 系 | 子として後で直す | 承認資料の検査の結果の上書き |
| #3641 | 10 系 | この Issue で解決 | ruleset の変更と develop と開発版の停止は受け入れ条件 24 の前提で、この課題の中で終える（上の表） |
| #3656 | 10 系 | 子として後で直す | 取り消された CI の読み違い |
| #3661 | 10 系 | この Issue で解決 | 受け入れ条件 17 のテストの入力 |
| #3671 | 10 系 | この Issue で解決 | 受け入れ条件 18 のテストの入力 |
| #3378 | 11.0 | この Issue で解決 | ブランチ運用・マージの方式の宣言と持ち主をこの設計が決める |
| #3487 | 11.0 | 子として後で直す | #2783 の 11.0 の側 |
| #3503 | 11.0 | 子として後で直す | 環境ブランチへの届け方 |
| #3572 | 11.0 | この Issue で解決 | 宣言と承認した先端のマージは受け入れ条件 2・6・17 が持つ |
| #3615 | 11.0 | 子として後で直す | 宣言の部分はこの Issue で解決し、CD のテンプレートが残る |
| #3617 | 11.0 | 子として後で直す | ブランチ運用の決め打ちは受け入れ条件 16 が持ち、残りの 12 件の直しが残る |
| #3623 | 11.0 | 子として後で直す | リリース対象のファイルの範囲 |
| #1338 | 両方 | この Issue で解決 | `merge.admin`・`merge.commit` と製品の既定（迂回なし） |
| #3618 | 両方 | 子として後で直す | 守りの全体（レッドライン・設計の型・レビュー）のうち、CI の検査だけをこの Issue が足す |

数: この Issue で解決 14 本、子として後で直す 18 本、閉じる 0 本（計 32 本）。
