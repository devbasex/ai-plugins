# #3572 の詳細設計: GitHub flow を土台にし、承認した先端だけを main へ入れ、承認の後に直しのステップを持たない

設計文書 [issue-3572-design.md](issue-3572-design.md) の「構成要素」を、ファイルと関数の粒度へ下ろした文書である。
語（承認した先端・先端の照合・承認の経路・差し戻し・直しのステップ・版上げの PR・版上げのコミット・上げ幅の宣言・検証の ref・
2 つ目のマーケットプレイス）は設計文書の「用語」の表の意味で使う。不変条件（I1〜I13）と決定（決定 1〜15）の番号は設計文書と
[決定の記録](issue-3572-design-decisions.md) の番号である。

**起点のコード。** スプリントの PR https://github.com/devbasex/ai-plugins/pull/3587 の先端 441dc236e（`origin/sprint/lrel-lock`）である。
決定 15 のとおり、この PR を直して出す。そのため「変更」「削除」は 441dc236e の時点のコードに対する差分を指す。
承認ゲートの待ち（`progress/gate/release.py`・`answering.py`・`asking.py`）は 441dc236e の形のまま使い、この文書では関数を書かない。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除）:

```text
plugins/ndf-next/
├── contracts/
│   ├── causes.json
│   ├── domains/branch-flows.json
│   ├── schemas/
│   │   ├── declarations/worktree.schema.json
│   │   └── io/
│   │       ├── release-steps.args.schema.json
│   │       └── release-steps.result.schema.json
│   └── templates/
│       ├── check.json
│       ├── close.json
│       ├── merge.json
│       ├── params.json
│       ├── parts/steps.json
│       └── release.json
├── docs/contracts/commands.md
├── src/ndf/domains/release/
│   ├── assemble.py
│   ├── bump.py *
│   ├── bump_decide.py *
│   ├── cli.py
│   ├── confirm.py *
│   ├── declaration.py
│   ├── land.py
│   ├── materials_decide.py
│   ├── merge_decide.py
│   ├── pr_check.py *
│   ├── prepare.py -
│   ├── record.py
│   ├── release_plan.py
│   ├── tagging.py
│   ├── trail.py
│   ├── verify_decide.py *
│   └── verify_ref.py *
└── tests/
    ├── domains/release/
    │   ├── test_release_approval.py
    │   ├── test_release_assemble.py
    │   ├── test_release_bump.py *
    │   ├── test_release_confirm.py *
    │   ├── test_release_plan.py
    │   ├── test_release_pr_check.py *
    │   ├── test_release_prepare.py -
    │   ├── test_release_record.py
    │   └── test_release_verify_ref.py *
    ├── domains/worktree/test_worktree_merged.py
    └── route/test_flows.py
```

**依存の向き。** 一方向で、循環は無い。純粋な判定（`release_plan`・`merge_decide`・`bump_decide`・`verify_decide`・`materials_decide`）は
GitHub と git のアダプタを読まず、`version_decide` と `changelog_decide` だけに依存する。GitHub と git を読み書きするモジュール
（`land`・`trail`・`assemble`・`bump`・`confirm`・`pr_check`・`verify_ref`・`record`・`tagging`）が判定を呼ぶ。副命令のエントリポイント
（`cli`）がそれらを呼ぶ。`bump` は版上げの PR のマージに `land.Lander` を使い、`land` は `bump` を import しない。
取り込み方の表は `declaration` が `ndf.contracts.facts` で読み、スプリントの経路（`domains/worktree`）を import しない。
承認ゲートの待ちはリリースの領域を import せず、副命令を子プロセスで打つ（441dc236e のまま）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/contracts/templates/release.json` | 変更 | 本番のプランを `merge` → `confirm` → `record` → `cleanup` の 4 ステップにする | `params.json` |
| `plugins/ndf-next/contracts/templates/merge.json` | 変更 | `release-guard` を先頭から `merge` の部品の直前へ移す | `parts/steps.json` |
| `plugins/ndf-next/contracts/templates/check.json` | 変更 | `merge-gate` の直前に `release-guard` を置く | `parts/steps.json` |
| `plugins/ndf-next/contracts/templates/close.json` | 変更 | `merge` の部品の直前に `release-guard` を置く | `parts/steps.json` |
| `plugins/ndf-next/contracts/templates/parts/steps.json` | 変更 | 部品 `release-guard` を足す | 無し |
| `plugins/ndf-next/contracts/templates/params.json` | 変更 | 本番のプランが `base` を使わなくなることに合わせ、引数の説明を直す | 無し |
| `plugins/ndf-next/contracts/causes.json` | 変更 | 止まった理由の種類 `production_pr`（本番の PR をデプロイの工程へ渡した）を足す | `facts/exit-codes.json` |
| `plugins/ndf-next/contracts/domains/branch-flows.json` | 変更 | 行 `github-flow` を足して既定にする（決定 5） | 無し |
| `plugins/ndf-next/contracts/schemas/declarations/worktree.schema.json` | 変更 | `branch_flow.promotion` に `direct`、`branch_flow.integrate` に `release` を足す | 無し |
| `plugins/ndf-next/contracts/schemas/io/release-steps.args.schema.json` | 変更 | 足す副命令（`bump`・`confirm`・`pr-check`・`verify-ref`）と外す副命令・引数の形を持つ | 無し |
| `plugins/ndf-next/contracts/schemas/io/release-steps.result.schema.json` | 変更 | 足す `outputs` と `items` の種類（`cd`・`version`・`level`・`verify-ref`・`conflict`）を持つ | 無し |
| `plugins/ndf-next/docs/contracts/commands.md` | 変更 | 副命令の一覧を新しい契約に合わせる | 無し |
| `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | 変更 | 本番のプランの禁止のステップと、`merge` の部品の直前の `release-guard` を判定する | `ndf.contracts.causes` |
| `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | 変更 | 次の手から次の版への回しを外す | 無し |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | 同期の PR と版の読みを外し、版上げの PR を証跡なしでマージする口を足す | `merge_decide`・`trail`・`required_checks` |
| `plugins/ndf-next/src/ndf/domains/release/trail.py` | 変更 | 作った版の記録のコメントを書き、読む | `merge_decide`・`approval_decide` |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | ブランチを作らず、本番の PR の範囲から承認資料を組んで本文の節へ書く | `gather`・`materials_decide`・`bump` |
| `plugins/ndf-next/src/ndf/domains/release/materials_decide.py` | 変更 | PR の本文の承認資料の節だけを差し替える | 無し |
| `plugins/ndf-next/src/ndf/domains/release/bump_decide.py` | 新設 | 上げ幅・起点・版上げの PR の差分の範囲・`bump` の動きを決める | `version_decide` |
| `plugins/ndf-next/src/ndf/domains/release/bump.py` | 新設 | 版上げの PR を作り・進め・置き換え、必須のチェックの後に照合付きでマージする | `bump_decide`・`gather`・`changelog_decide`・`plugins`・`land` |
| `plugins/ndf-next/src/ndf/domains/release/confirm.py` | 新設 | 本番の PR のマージを含む版上げの PR のマージ・タグ・そのタグの GitHub Release を待ち、作った版を記録する | `trail`・`version_decide` |
| `plugins/ndf-next/src/ndf/domains/release/pr_check.py` | 新設 | main 宛ての PR が版数を変えていないか・上げ幅のラベルが 1 つ以下か・head が検証の ref でないかを見る | `bump_decide`・`declaration` |
| `plugins/ndf-next/src/ndf/domains/release/verify_decide.py` | 新設 | 検証の ref に含める PR・検証の版・`marketplace.json` の書き換えを決める | `version_decide` |
| `plugins/ndf-next/src/ndf/domains/release/verify_ref.py` | 新設 | 一時の worktree で main の先端へ PR の先端を取り込み、fast-forward で検証の ref を進める | `verify_decide`・`plugins`・`declaration` |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 変更 | 取り込み方の行と、`delivery` の検証の行を読む | `ndf.contracts.facts` |
| `plugins/ndf-next/src/ndf/domains/release/record.py` | 変更 | リリース記録の版を、ブランチの名前ではなく作った版の記録から読む | `trail` |
| `plugins/ndf-next/src/ndf/domains/release/tagging.py` | 変更 | GitHub Release の本文の末尾に、次の版の節に載る PR を足す | `bump_decide`・`changelog_decide` |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | 副命令 `bump`・`confirm`・`pr-check`・`verify-ref` を足し、`defer`・`prepare-next` と開発版の引数を外す | 上の各モジュール |
| `plugins/ndf-next/src/ndf/domains/release/prepare.py` | 削除 | 次の版のリリースブランチの用意（GitHub flow で無くなる） | — |
| `plugins/ndf-next/tests/domains/release/test_release_plan.py` | 変更 | 雛形の検査と `guard` を縛る | `release_plan` |
| `plugins/ndf-next/tests/domains/release/test_release_approval.py` | 変更 | 同期の PR と次の版への回しの行を外し、`land_bump` を縛る | `land`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_assemble.py` | 変更 | 承認資料だけを書く形を縛る | `assemble`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_bump.py` | 新設 | 上げ幅・起点・競合・差分の範囲・冪等を縛る | `bump`・`bump_decide`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_confirm.py` | 新設 | CD の確かめの待ち・止まり方・記録の 1 回を縛る | `confirm`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_pr_check.py` | 新設 | 版数・ラベル・検証の ref の検査を縛る | `pr_check` |
| `plugins/ndf-next/tests/domains/release/test_release_verify_ref.py` | 新設 | 含める PR・衝突・fast-forward・名前の書き換えを縛る | `verify_ref`・`verify_decide` |
| `plugins/ndf-next/tests/domains/release/test_release_record.py` | 変更 | 作った版の記録から版を読むことを縛る | `record` |
| `plugins/ndf-next/tests/domains/release/test_release_prepare.py` | 削除 | `prepare.py` と一緒に外す | — |
| `plugins/ndf-next/tests/domains/worktree/test_worktree_merged.py` | 変更 | 行 `github-flow` でスプリントブランチを残さないことを縛る | `domains/worktree` |
| `plugins/ndf-next/tests/route/test_flows.py` | 変更 | 行 `github-flow` の宣言で起点と宛先が main になることを縛る | `ndf.core.route` |
| `.github/workflows/ndf-next-release.yml` | 変更 | main への push で `bump`・`publish`、PR の出来事で `pr-check`・`verify-ref` を打つ | `plugins/ndf-next/bin/ndf` |
| `.ndf/project.json` | 変更 | `delivery` に検証の行を足す（決定 9） | 無し |
| `AGENTS.md` | 変更 | 「版とリリースの方針」に、11.0.0 の入れ替えで GitHub flow へ移る 1 段落と正本へのリンクを足す（決定 12） | 無し |
| `docs/versioning-and-distribution.md` | 変更 | 節「11.0.0 からの運用（GitHub flow）」を足す（決定 12） | 無し |

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | モジュール | `forbidden`（変更）・`guard_before_merge`（新設。`guard_first` を置き換える）・`guard_cause`（変更）・`is_production_pr`（変えない） |
| `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | モジュール | `suggested`（変えない）。定数 `NEXT_MOVES` から `DEFER` を外す |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | `Lander`・`Landed` | `land`（変更）・`_resume_merged`（変更）・`_released`（変更）・`land_bump`（新設）。削除: `release_version`・`_open_sync`・`_sync`・欄 `Lander.base`・欄 `Landed.sync_pr`・欄 `Landed.version` |
| `plugins/ndf-next/src/ndf/domains/release/trail.py` | モジュール | `render_version_record`（新設）・`write_version_record`（新設）・`read_version_record`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | `Target`・`Built`・モジュール | `Target`（欄を変更）・`Built`（欄を変更）・`collect`（変更）・`assemble`（変更）・`_span`（変更）・`_facts`（変更）。削除: `_remote_releases`・`_version_or_none`・`_shipped_of`・`_standing`・`_prepared`・`open_release_heads`・`_write`（`bump` へ移す）・`_commit_and_push`・`_open_pr`・`_from_shipped`・`_on_origin`・`_existing`・`_deferred`・`_rewritten`・`_merge_in`・`_advance`・`_rename`・`_reassemble`・`_checkout`・`ReleaseOpen`・`SOURCES` |
| `plugins/ndf-next/src/ndf/domains/release/materials_decide.py` | モジュール | `splice`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/bump_decide.py` | モジュール | `LevelError`・`OpenBump`（新設の型）・`level_of`・`widest`・`planned`・`origin_of`・`message_of`・`only_bump`・`action`・`late`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/bump.py` | `Bumper`・`Bumped` | `Bumper.bump`・`Bumper._span`・`Bumper._open_bump`・`Bumper._create`・`Bumper._update`・`Bumper._replace`・`Bumper._merge`（新設）・`write_bump`（`assemble._write` を移す）・`planned_for`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/confirm.py` | `Confirmed`・モジュール | `confirm`・`_bump_containing`・`_tagged`・`_has_release`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/pr_check.py` | `PrFacts`・モジュール | `problems`・`check`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/verify_decide.py` | `OpenPull`（新設の型）・モジュール | `included`・`version_of`・`marketplace_of`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/verify_ref.py` | `VerifyConflict`・`Verified`・モジュール | `build`・`_open_pulls`・`_merge_pulls`・`_commit_on_top`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | `VerifyDecl`・モジュール | `flow_of`・`require_github_flow`・`verify_of`（新設）。`production_branch`（変えない） |
| `plugins/ndf-next/src/ndf/domains/release/record.py` | モジュール | `released`（変更） |
| `plugins/ndf-next/src/ndf/domains/release/tagging.py` | モジュール | `publish`（変更）・`_late`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | モジュール | `cmd_bump`・`cmd_confirm`・`cmd_pr_check`・`cmd_verify_ref`（新設）・`cmd_merge`・`_merge_result`・`_moves`・`_answer_merged`・`cmd_guard`・`cmd_assemble`・`cmd_materials`・`cmd_cleanup`（変更）・`_target`（変更）。削除: `cmd_defer`・`cmd_prepare_next`・`_target_args` の `--channel`・`--level`・`--source`。`ARGS` と `_ERRORS` に行を足し、外した副命令の行を消す |

`cmd_record` は呼ぶ先（`record.write`）の版の読み方だけが変わり、引数・責務・失敗の扱いを変えないため「関数の仕様」には書かない。

## 関数の仕様

置き場の列は `plugins/ndf-next/src/ndf/domains/release/` を省き、ファイル名だけを書く。

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `forbidden` | `release_plan.py` | 変更 | `forbidden(plan: Mapping[str, Any]) -> list[str]` | `release: true` のプランが I1・I2・I10 を破るステップを返す | `plan` は `ndf.core.route.expanded` の形 | 441dc236e の 3 種に加え、`run` が `AFTER_COMMANDS` の副命令を打つステップを 1 件 1 行で返す。`release` が偽なら空 | 無し |
| `guard_before_merge` | `release_plan.py` | 新設 | `guard_before_merge(plan: Mapping[str, Any]) -> list[str]` | `merge` の部品を持つプランで、最初の `MERGE_PART_IDS` のステップの直前が `release-guard` で、その原因の行き先が `end` かを返す（I6・I13） | `plan` は `expanded` の形 | `MERGE_PART_IDS` のステップが無ければ空。あれば、直前の id・`exit_cause` の `GUARD_EXIT` の値・`on_cause` の `production_pr` の行き先が合うときだけ空 | 無し |
| `guard_cause` | `release_plan.py` | 変更 | `guard_cause() -> str` | 本番の PR でデプロイの工程へ渡す原因の種類を返す | 無し | `"production_pr"` を返す。`GUARD_EXIT` は止まった理由の表から求まる | 無し |
| `Lander.land` | `land.py` | 変更 | `land(self, number: int) -> Landed` | 本番の PR を照合付きでマージし、承認を結果に載せる。同期の PR を作らない（I10） | `number` は main 宛ての PR | head の名前を問わない。マージ済みなら `_resume_merged` の結果。止まった結果は 441dc236e のまま `failed` を持ち、push もマージも打っていない | GitHub の REST（441dc236e のまま。PR の作成を除く） |
| `Lander._resume_merged` | `land.py` | 変更 | `_resume_merged(self, number: int, pull: Pull, deadline: float) -> Landed` | マージ済みの本番の PR の承認を決め、人のマージなら記録を書き、迂回の記録を補う | `pull.merged` が真 | 戻す PR を作らない。本番の PR なら `production_sha` と `approval` を持つ | GitHub の REST（PR の照会・コメントの作成） |
| `Lander._released` | `land.py` | 変更 | `_released(self, pull: Pull, done: Landed) -> Landed` | 本番ブランチへ入れた結果に、マージコミットと承認を足す | `done.merged` が真 | `production_sha` が PR の `merge_sha`。版は持たない（版は CD が決める。決定 7） | GitHub の REST（PR の照会・コメントの一覧と作成） |
| `Lander.land_bump` | `land.py` | 新設 | `land_bump(self, number: int, allowed: Callable[[Pull], list[str]]) -> Landed` | 版上げの PR を、承認の証跡を読まずに、差分の判定と必須のチェックの後に照合付きでマージする（決定 7） | `self.admin` が真。`allowed` は外れた差分のパスを返す | `allowed` が空を返し、必須のチェックが通り、迂回の判定（`_bypass`）が真のときだけ `merge_as_admin(expect_head=先端)` を打っている。どれかが欠ければマージせずに止まった結果 | GitHub の REST（PR の照会・チェックの読み・マージ） |
| `render_version_record` | `trail.py` | 新設 | `render_version_record(version: str, tag: str, bump_pr: int) -> str` | 作った版の記録の本文を組む | `version` は接尾辞の無い版 | 印 `VERSION_MARK` と 3 行（作った版・タグ・版上げの PR）を持ち、証跡の印を持たない | 無し |
| `write_version_record` | `trail.py` | 新設 | `write_version_record(hub: GitHub, number: int, version: str, tag: str, bump_pr: int) -> tuple[bool, str]` | 本番の PR へ作った版の記録を 1 回だけ書く | 書く token が信用される | 信用する書き手の編集されていない同じ版の記録があれば書かずに `(False, URL)`、無ければ書いて `(True, URL)` | GitHub の REST（コメントの一覧・作成） |
| `read_version_record` | `trail.py` | 新設 | `read_version_record(hub: GitHub, number: int) -> str \| None` | 本番の PR の作った版の記録から版を読む | 無し | 信用する書き手の最新の記録の版。無ければ None | GitHub の REST（コメントの一覧） |
| `collect` | `assemble.py` | 変更 | `collect(root: Path, hub: GitHub, target: Target) -> materials_decide.Facts` | 本番の PR の範囲（main の先端..PR の先端）から承認資料の材料を読む | 取り込み方の行が `github-flow` | `facts.head` が PR の先端、予定の版が上げ幅の宣言から求めた版。ブランチを作らない | git（fetch）・GitHub の REST（PR・ラベル・含む PR） |
| `assemble` | `assemble.py` | 変更 | `assemble(root: Path, hub: GitHub, target: Target) -> Built` | 承認資料を組み、PR の本文の承認資料の節だけを書き直す（決定 6） | 同上 | PR の本文の承認資料の節が `facts.head` を対象にし、節の外は変わっていない。先端を動かしていない | git（fetch）・GitHub の REST（本文の更新） |
| `_span` | `assemble.py` | 変更 | `_span(run: _Run) -> gather.Range` | 範囲を `origin/<本番ブランチ>..refs/pull/<番号>/head` にする | 無し | 両端を fetch 済み。開発版と常設のリリースブランチの経路を持たない | git（fetch） |
| `_facts` | `assemble.py` | 変更 | `_facts(run: _Run, span: gather.Range) -> materials_decide.Facts` | 範囲の変更と含む PR から材料を読み、上げ幅を PR のラベルから求める | `span` の両端が fetch 済み | チャネルは常に本番。上げるものが無ければ `NothingToRelease` | git・GitHub の REST |
| `splice` | `materials_decide.py` | 新設 | `splice(body: str, materials: str) -> str` | PR の本文の承認資料の節を差し替える（無ければ末尾に足す） | `materials` は印を持たない | 印 `MATERIALS_START`〜`MATERIALS_END` の間が `materials` で、印の外の本文は元のまま | 無し |
| `level_of` | `bump_decide.py` | 新設 | `level_of(labels: Sequence[str]) -> str` | 1 本の PR のラベルから上げ幅を決める | 無し | `LEVEL_LABELS` の該当が 1 つならその幅、0 なら `patch`。2 つ以上なら `LevelError` | 無し |
| `widest` | `bump_decide.py` | 新設 | `widest(levels: Sequence[str]) -> str` | 範囲の PR の上げ幅のうち最も大きいものを返す | 各値は `version_decide.LEVELS` | 空なら `patch` | 無し |
| `planned` | `bump_decide.py` | 新設 | `planned(current: str, level: str) -> str` | 今の版と上げ幅から次の版を求める | `current` は接尾辞の無い版 | `version_decide.production_next(current, level)` と同じ値 | 無し |
| `origin_of` | `bump_decide.py` | 新設 | `origin_of(message: str) -> str \| None` | 版上げのコミットのメッセージから起点の SHA を読む | 無し | 行 `起点: <40 桁>` があればその SHA、無ければ None | 無し |
| `message_of` | `bump_decide.py` | 新設 | `message_of(plugin: str, version: str, origin: str) -> str` | 版上げのコミットのメッセージを組む | `origin` は 40 桁の SHA | 題 `Release: <プラグイン> v<版>` と行 `起点: <SHA>` を持ち、`origin_of` で同じ SHA が読める | 無し |
| `only_bump` | `bump_decide.py` | 新設 | `only_bump(paths: Sequence[str], allowed: frozenset[str]) -> list[str]` | 版上げの PR の差分が、版のファイルと CHANGELOG に限られるかを判定する（I7） | 無し | `allowed` に無いパスを入力の順で返す。空なら限られている | 無し |
| `action` | `bump_decide.py` | 新設 | `action(tip_tagged: bool, changed: bool, opened: OpenBump \| None, version: str) -> str` | main の先端の状態から `bump` の動きを 1 つ決める（設計文書の「main へのマージの後（CD）」の 2 つの表） | 無し | 「処理の手順」の判定表のとおり `PUBLISH`・`NOTHING`・`CREATE`・`UPDATE`・`REPLACE` のどれか | 無し |
| `late` | `bump_decide.py` | 新設 | `late(included: Sequence[int], listed: Sequence[int]) -> list[int]` | タグを打つコミットに含まれ、版の節に載らない PR を返す | 無し | `included` にあって `listed` に無い番号を小さい順に返す | 無し |
| `Bumper.bump` | `bump.py` | 新設 | `bump(self) -> Bumped` | main の先端に合わせて版上げの PR を作るか進めるか置き換え、マージまで進める（I8・I11） | 取り込み方の行が `github-flow`。`--root` は main を取り出した作業場所 | `state` が `merged`・`reused`・`published`・`not_applicable` のどれか。強制 push を打っていない | git（fetch・switch・merge・commit・push）・GitHub の REST |
| `Bumper._span` | `bump.py` | 新設 | `_span(self) -> gather.Range` | 範囲（前の版上げのコミットの起点..main の先端）を決める | main を fetch 済み | 起点は main の祖先にある最新の版上げのコミットの `起点:`。無ければ main の版のタグのコミット、どちらも無ければ根のコミット | git（log・rev-parse） |
| `Bumper._open_bump` | `bump.py` | 新設 | `_open_bump(self) -> bump_decide.OpenBump \| None` | main 宛ての開いた版上げの PR を 1 本読む | 無し | head が `release/v` で始まる開いた PR が 1 本ならその番号・版・head。2 本以上なら `BumpError` | GitHub の REST（PR の一覧） |
| `Bumper._create` | `bump.py` | 新設 | `_create(self, version: str, span: gather.Range, pulls: Sequence[materials_decide.PullFact]) -> int` | main の先端から `release/v<版>` を作り、版上げのコミットを足して版上げの PR を作る | origin に同じ名前のブランチが無い | ブランチの先端の親が main の先端で、差分は `write_bump` が書いたファイルだけ。PR の番号を返す | git・GitHub の REST（PR の作成） |
| `Bumper._update` | `bump.py` | 新設 | `_update(self, opened: bump_decide.OpenBump, span: gather.Range, pulls: Sequence[materials_decide.PullFact]) -> None` | 開いた版上げの PR へ main を取り込むコミットと、版の節を組み直したコミットを足す | 版が同じ | head の先端が前の先端と main の先端の両方を祖先に持ち、版の節が範囲の PR を並べる。起点の行は main の新しい先端 | git（merge・commit・push）・GitHub の REST |
| `Bumper._replace` | `bump.py` | 新設 | `_replace(self, opened: bump_decide.OpenBump, version: str) -> None` | 古い版の版上げの PR を理由つきで閉じ、そのブランチを消す | 版が違う | 古い PR が閉じ、コメント「版 <古い版> を <新しい版> へ置き換えた」があり、古いブランチが origin に無い | GitHub の REST（コメント・PR の更新・参照の削除） |
| `Bumper._merge` | `bump.py` | 新設 | `_merge(self, number: int) -> land.Landed` | main の先端を含むときだけ、版上げの PR を `land_bump` でマージする | PR は版上げの PR | main の先端が head の祖先でなければマージせずに `reused` の結果。含めば `land_bump` の結果 | GitHub の REST・git（is-ancestor） |
| `write_bump` | `bump.py` | 新設 | `write_bump(root: Path, decl: Declaration, planned: Sequence[version_decide.Planned], pulls: Sequence[materials_decide.PullFact], today: str) -> frozenset[str]` | 版のファイルと CHANGELOG の版の節を書き、宣言の手順を流す（`assemble._write` を移す） | `root` は版上げのブランチを取り出した場所 | 書いたパスを返す。返したパスは `only_bump` の `allowed` に入る | ファイルの書き込み・宣言の手順の外部コマンド |
| `planned_for` | `bump.py` | 新設 | `planned_for(root: Path, hub: GitHub, number: int, production: str) -> str \| None` | 本番の PR の予定の版を、PR のラベルと main の版から求める | 無し | マージ済みなら第 1 親の版、そうでなければ origin の main の版を `planned` で上げた版。版を読めなければ None | git（show）・GitHub の REST（ラベル） |
| `confirm` | `confirm.py` | 新設 | `confirm(hub: GitHub, repo_git: Git, number: int, production: str, budget: float, clock: Callable[[], float], sleep: Callable[[float], None]) -> Confirmed` | 本番の PR のマージコミットを含む版上げの PR のマージ・タグ・そのタグの GitHub Release を待ち、作った版を本番の PR へ記録する | 本番の PR がマージ済み | 3 つがそろえば版・タグ・版上げの PR を持ち、作った版の記録が 1 件ある。待ちが切れれば止まった結果で、記録を書いていない | GitHub の REST（PR の一覧・参照の照会・Release の照会・コメント）・git（fetch・is-ancestor） |
| `_bump_containing` | `confirm.py` | 新設 | `_bump_containing(hub: GitHub, repo_git: Git, merge_sha: str, production: str) -> tuple[int, str, str] \| None` | マージ済みの版上げの PR のうち、マージコミットが `merge_sha` を含む最も小さい版のものを探す | main を fetch 済み | (PR の番号, 版, マージコミット)。無ければ None | GitHub の REST（閉じた PR の一覧）・git（is-ancestor） |
| `_tagged` | `confirm.py` | 新設 | `_tagged(hub: GitHub, tag: str) -> bool` | タグが origin にあるかを読む | 無し | タグの参照があれば真、404 なら偽 | GitHub の REST（参照の照会） |
| `_has_release` | `confirm.py` | 新設 | `_has_release(hub: GitHub, tag: str) -> bool` | タグの GitHub Release があるかを読む | 無し | `/releases/tags/<タグ>` があれば真、404 なら偽 | GitHub の REST（Release の照会） |
| `problems` | `pr_check.py` | 新設 | `problems(facts: PrFacts) -> list[dict[str, str]]` | main 宛ての PR の事実から、版数・上げ幅のラベル・検証の ref の破れを返す（I7・I9） | 無し | 項目 `{"kind", "name", "detail"}` を 1 件ずつ返す。版上げの PR は版数の破れを返さない | 無し |
| `check` | `pr_check.py` | 新設 | `check(root: Path, hub: GitHub, repo_git: Git, number: int) -> list[dict[str, str]] \| None` | 宣言の行が `github-flow` のときだけ PR の事実を集めて `problems` を返す | 無し | 行が `github-flow` でなければ None（何も見ない）。そうでなければ `problems` の結果 | git（fetch・diff）・GitHub の REST（PR・ラベル） |
| `included` | `verify_decide.py` | 新設 | `included(pulls: Sequence[OpenPull], verify_ref: str, repo: str) -> list[OpenPull]` | 検証の ref に含める PR を決める | 無し | 下書きでなく、head が検証の ref でも `release/v` でもなく、head のリポジトリが `repo` の PR を番号の順に返す | 無し |
| `version_of` | `verify_decide.py` | 新設 | `version_of(main_version: str, at: datetime) -> str` | 検証の版を決める | `at` は UTC | `<main の版>-next.<YYYYMMDDHHMMSS>` | 無し |
| `marketplace_of` | `verify_decide.py` | 新設 | `marketplace_of(raw: Mapping[str, Any], name: str) -> dict[str, Any]` | `marketplace.json` の名前を 2 つ目のマーケットプレイスの名前へ書き換える | `raw` は `marketplace.json` の中身 | `name` だけが変わり、ほかの鍵は元のまま | 無し |
| `build` | `verify_ref.py` | 新設 | `build(root: Path, hub: GitHub, decl: declaration.VerifyDecl, now: datetime) -> Verified` | 一時の worktree で検証の ref を組み直し、fast-forward で push する（I9・I12） | `root` は main を取り出した作業場所。取り込み方の行が `github-flow` | 検証の ref の先端が、前の先端と組んだコミットの両方を親に持つ。衝突すれば ref を動かさず `VerifyConflict`。一時の worktree を消している | git（worktree・fetch・merge・commit-tree・push）・GitHub の REST（PR の一覧） |
| `_open_pulls` | `verify_ref.py` | 新設 | `_open_pulls(hub: GitHub, production: str) -> list[verify_decide.OpenPull]` | main 宛ての開いた PR を読む | 無し | 全ページを読み、番号・先端・head の名前・head のリポジトリ・下書きの印を持つ | GitHub の REST（PR の一覧） |
| `_merge_pulls` | `verify_ref.py` | 新設 | `_merge_pulls(work: Git, pulls: Sequence[verify_decide.OpenPull]) -> None` | PR の先端を順に取り込みのマージで入れる | `work` は main の先端を取り出した一時の worktree | すべて入れば HEAD が全 PR の先端を祖先に持つ。衝突すれば `merge --abort` の後に `VerifyConflict` | git（fetch `refs/pull/<番号>/head`・merge） |
| `_commit_on_top` | `verify_ref.py` | 新設 | `_commit_on_top(work: Git, previous: str \| None) -> str` | 今の木を持ち、前の検証の ref の先端と HEAD を親にするコミットを作る | `previous` は origin の検証の ref の先端（初回は None） | 返す SHA は `previous` を祖先に持つ（初回は HEAD だけが親） | git（commit-tree） |
| `flow_of` | `declaration.py` | 新設 | `flow_of(root: Path) -> str` | 宣言 `.ndf/worktree.json` の `branch_flow.promotion` から、取り込み方の表の行の id を返す | 無し | 宣言に `branch_flow` が無ければ既定の行の id。表に無い値なら `DeclarationError` | ファイルの読み込み |
| `require_github_flow` | `declaration.py` | 新設 | `require_github_flow(root: Path) -> None` | 取り込み方の行が `github-flow` でなければ止める（決定 5） | 無し | 行が `github-flow` なら何もしない。ほかは理由「取り込み方 <行> は ndf-next のリリースで扱わない」の `DeclarationError` | ファイルの読み込み |
| `verify_of` | `declaration.py` | 新設 | `verify_of(root: Path) -> VerifyDecl` | `.ndf/project.json` の `delivery` の検証の行から ref の名前とマーケットプレイスの名前を読む | 無し | `channel` が `verify` の行が 1 つで、`verify.ref` と `verify.marketplace` が空でない文字列 | ファイルの読み込み |
| `released` | `record.py` | 変更 | `released(hub: GitHub, number: int, production: str) -> tuple[Pull, record_decide.Record]` | マージした本番の PR と、作った版の記録の版のリリース記録を返す | 無し | 版は `trail.read_version_record` の値で、head の名前を読まない | GitHub の REST（PR・コメント・比べ） |
| `publish` | `tagging.py` | 変更 | `publish(root: Path, hub: GitHub, revision: str) -> list[Made]` | 足りないタグと GitHub Release を作り、本文の末尾に次の版の節に載る PR を足す | `revision` は版上げの PR のマージコミット | 441dc236e のまま作り、`_late` が空でなければ本文に節「次の版の節に載る PR」がある | GitHub の REST・git |
| `_late` | `tagging.py` | 新設 | `_late(root: Path, revision: str, notes: str) -> list[int]` | タグを打つコミットに含まれ版の節に載らない PR を読む | 無し | 第 2 親の版上げのコミットの起点から第 1 親までに入った PR のうち、`notes` に番号の無いもの | git（log・show） |
| `cmd_bump` | `cli.py` | 新設 | `cmd_bump(args: argparse.Namespace) -> dict` | 副命令 `bump` の結果 JSON を組む | 無し | `outputs` に `version`・`level`・`pr`・`state` | `Bumper.bump` を介した git・GitHub の REST |
| `cmd_confirm` | `cli.py` | 新設 | `cmd_confirm(args: argparse.Namespace) -> dict` | 副命令 `confirm` の結果 JSON を組む | 無し | そろえば `ok` で `outputs` に `version`・`tag`・`bump_pr`。そろわなければ `stopped`・種類 `deploy_failed`、`items` に `{"kind": "cd", ...}`、`routes` に `rerun` の 1 つ | `confirm` を介した GitHub の REST・git |
| `cmd_pr_check` | `cli.py` | 新設 | `cmd_pr_check(args: argparse.Namespace) -> dict` | 副命令 `pr-check` の結果 JSON を組む | 無し | 破れが無いか行が `github-flow` でなければ `ok`（終了コード 0）。あれば `stopped`・種類 `check_failed`（終了コード 1）と `items` | `pr_check.check` を介した git・GitHub の REST |
| `cmd_verify_ref` | `cli.py` | 新設 | `cmd_verify_ref(args: argparse.Namespace) -> dict` | 副命令 `verify-ref` の結果 JSON を組む | 無し | `outputs` に `ref`・`head`・`version`・`included`。衝突は `stopped`・種類 `base_conflict` と `{"kind": "conflict", "name": "#<番号>"}` | `verify_ref.build` を介した git・GitHub の REST |
| `cmd_merge` | `cli.py` | 変更 | `cmd_merge(args: argparse.Namespace) -> dict` | `Landed` と予定の版を結果 JSON にする | 取り込み方の行が `github-flow` | `outputs.version` が `planned_for` の値。値の項目に `sync_pr` が無い。止まった結果の `moves` は `rerun`・`reject` の 2 つ | `land` と `planned_for` を介した GitHub の REST・git |
| `_moves` | `cli.py` | 変更 | `_moves(args: argparse.Namespace, kind: str, released: bool) -> dict[str, Any]` | 止まったマージの次の手と既定の手を返す（決定 3） | 無し | `released` が偽なら `rerun`・`reject` の 2 つ、真なら `rerun` だけ。`defer` を返さない | 無し |
| `_answer_merged` | `cli.py` | 変更 | `_answer_merged(args: argparse.Namespace) -> dict` | 人のマージを承認の証跡にし、予定の版を出す | 無し | `outputs.version` が `planned_for` の値（head の名前を読まない） | GitHub の REST・git |
| `cmd_guard` | `cli.py` | 変更 | `cmd_guard(args: argparse.Namespace) -> dict` | ブランチの開いた PR が本番の PR ならデプロイの工程へ渡し、head が検証の ref なら断る | 無し | 本番の PR なら `stopped`・種類 `production_pr`（終了コード 3）。head が検証の ref なら `stopped`・`refused`（終了コード 1）。ほかは `ok` | GitHub の REST（PR の照会）・ファイルの読み込み |
| `cmd_assemble` | `cli.py` | 変更 | `cmd_assemble(args: argparse.Namespace) -> dict` | `--pr` の本文の承認資料の節を書いた結果を返す | 無し | `outputs` に `pr`・`version`。`branch` を持たない | `assemble` を介した git・GitHub の REST |
| `cmd_materials` | `cli.py` | 変更 | `cmd_materials(args: argparse.Namespace) -> dict` | `--pr` の承認資料を組んで出す（本文は書かない） | 無し | 441dc236e のまま（引数だけが変わる） | `collect` を介した git・GitHub の REST・ファイルの書き込み |
| `cmd_cleanup` | `cli.py` | 変更 | `cmd_cleanup(args: argparse.Namespace) -> dict` | `--branch` が無ければ `--pr` の head のブランチの worktree を外す | `--branch` か `--pr` のどちらかがある | 外した worktree と残した worktree を項目に持つ | git（worktree）・GitHub の REST（`--pr` のとき） |

`_target` は `assemble.Target(_production_of(args), args.pr, args.plugin)` を返すだけで、失敗の扱いを持たないため表に書かない。

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Lander.land` | 必須のチェックが落ちた・先端が動いた | CI の失敗・承認の後のコミット | `_blocker`（441dc236e のまま） | 止める | `stopped`・種類は 441dc236e のまま。`moves` は `rerun`・`reject` | 何も変えていない。次の手を選んで打つ |
| `Lander._resume_merged` | 人のマージの記録・迂回の記録を書けない | GitHub の誤り | `trail.TrailError` | 既定値で続ける（`record_error` に理由） | `stopped`・`unknown`・「本番は出ている」 | 本番は出ている。打ち直すと記録だけを書く |
| `Lander._released` | PR を読み直せない | GitHub の誤り | `GitHubError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown` | 本番は出ている。打ち直すとマージ済みからの再開で決め直す |
| `Lander.land_bump` | 差分が版のファイルと CHANGELOG の外に及ぶ | 版上げのブランチへ別のコミットが積まれた | `allowed` の戻り値 | 止める（マージしない） | `stopped`・`refused`・外れたパス | 何もマージしていない。ブランチを人が見る |
| `Lander.land_bump` | 迂回できない | 宣言が無い・承認数を読めない・token の持ち主が管理者の役割を持たない | `self.admin`・`_bypass`・`merge_as_admin` の断り（`not_ci`） | 止める | `stopped`・`precondition` か `not_ci`。理由に「ruleset を変えずに済む手段が無い（要求の未決 3）」 | 版上げの PR は開いたまま。人へ戻す |
| `Lander.land_bump` | 必須のチェックが落ちた・待ちが切れた | 版上げの PR の CI | `_watch` | 止める | `stopped`・`test_failed`・`check_failed`・`ci_pending` | 打ち直すと同じ PR の先端で待ち直す |
| `write_version_record` | コメントを書けない・SHA の形でない | GitHub の誤り | `GitHubError` | 止める（`TrailError`） | 呼び出し元（`confirm`）が扱う | 書けていない。打ち直せる（同じ版の記録があれば書かない） |
| `read_version_record` | コメントを読めない | GitHub の誤り | `GitHubError` | 呼び出し元へ返す | 呼び出し元（`record.released`）が扱う | 何も変えていない |
| `collect` | 取り込み方の行が `github-flow` でない | ai-plugins の宣言が入れ替えの前 | `declaration.require_github_flow` | 止める | `stopped`・`precondition`・理由「取り込み方 <行> は ndf-next のリリースで扱わない」 | 何もしていない |
| `collect` | PR の先端を取り込めない | PR が閉じた・番号の誤り | `gather.fetch` の `GitError` | 止める | `stopped`・`precondition` | 何もしていない |
| `collect` | 上げ幅のラベルが 2 つ以上 | PR の宣言の誤り | `bump_decide.level_of` の `LevelError` | 止める | `stopped`・`invalid`・ラベルの一覧 | ラベルを直して打ち直す |
| `assemble` | 本文を書けない | GitHub の誤り | `Publisher.edit_body` の `GitHubError` | 呼び出し元へ返す | `stopped`・`unknown` | 本文が古い間は `present` が先端の食い違いで断る。打ち直せる |
| `_span` | 本番ブランチか PR の先端を fetch できない | 名前の誤り・ネットワーク | `GitError` | 止める | `stopped`・`precondition` | 何もしていない |
| `_facts` | 上げるものが無い | 範囲に配るものの変化が無い | `gather.clear_shipped` の空 | 止める（`NothingToRelease`） | `stopped`・`not_applicable` | 何もしていない |
| `Bumper.bump` | 開いた版上げの PR が 2 本以上 | 手で作った・前の置き換えの途中で止まった | `_open_bump` | 止める（`BumpError`） | `stopped`・`precondition`・PR の番号 | 人が 1 本に減らしてから打ち直す |
| `Bumper.bump` | 取り込み方の行が `github-flow` でない | 入れ替えの前 | `require_github_flow` | 止める | `stopped`・`precondition` | 何もしていない |
| `Bumper._span` | 起点の SHA が main の祖先にない | 版上げのコミットのメッセージを手で書き換えた | `git merge-base --is-ancestor` | 止める（`BumpError`） | `stopped`・`precondition`・起点の SHA | 何もしていない |
| `Bumper._open_bump` | PR の一覧を読めない | GitHub の誤り | `GitHubError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown` | 何もしていない。打ち直せる |
| `Bumper._create` | 同じ名前のブランチが既にある | 前の実行が PR を作る前に止まった | push の断り（`GitError`）か参照の照会 | 既にあり、その先端の親が main の先端で差分が版上げだけなら PR だけを作って続ける。そうでなければ止める | `stopped`・`precondition`・ブランチの名前 | 打ち直せる。ブランチは消さない |
| `Bumper._create` | 宣言の手順が落ちた | 手順のコマンドの失敗 | `declaration.StepFailed` | 止める | `stopped`・`check_failed` | push していない。打ち直せる |
| `Bumper._update` | main の取り込みが衝突した | 版上げのブランチと main が版のファイルで重なった | `git merge` の `GitError` | 止める（`merge --abort` の後） | `stopped`・`base_conflict`・衝突したパス。設計文書の「CD が止まったとき」の次の手 | 取り出しを戻す。人が見てから `step=bump` で打ち直す |
| `Bumper._update` | push が non-fast-forward で断られた | 同時に別の `bump` が進めた | `GitError` | 止める | `stopped`・`precondition` | 次の main への push の `bump` が進める |
| `Bumper._replace` | 閉じる・消すのを断られた | 権限・GitHub の誤り | `GitHubError` | 呼び出し元へ返す | `stopped`・`unknown` | 新しい PR は作っていない。打ち直すと閉じるところからやり直す |
| `Bumper._merge` | `land_bump` が止まった | `land_bump` の行のどれか | `Landed.kind` | 呼び出し元へ返す | `land_bump` の行と同じ | `land_bump` の行と同じ |
| `write_bump` | 版のファイルが読めない・CHANGELOG が壊れている | 宣言の誤り | `plugins_mod.PluginsError`・`changelog_decide` の例外 | 呼び出し元へ返す | `stopped`・`precondition` | 書きかけのファイルは取り出しを捨てて消える（push していない） |
| `planned_for` | ラベルか main の版を読めない | GitHub の誤り・版の形の誤り | `GitHubError`・`VersionError` | 既定値で続ける（None） | 結果の `outputs.version` が無い | 無し。版は `confirm` が確定する |
| `confirm` | 待ちの内に版上げの PR のマージ・タグ・GitHub Release のどれかがそろわない | CD が止まった・遅れた | `clock` と `budget` | 止める | `stopped`・`deploy_failed`・理由に「本番はマージ済み」と、足りないもの（版上げの PR のマージなら `bump`、タグか GitHub Release なら `publish`）。次の手は `rerun` の 1 つ | 何も書いていない。workflow を `step=bump` で打ち直してから `confirm` を打ち直す |
| `confirm` | 本番の PR がマージされていない | `merge` の前に打った | `hub.pull(number).merged` | 止める | `stopped`・`precondition` | 何もしていない |
| `confirm` | 作った版の記録を書けない | GitHub の誤り | `trail.TrailError` | 止める | `stopped`・`unknown`・「版とタグはそろっている」 | 打ち直すと記録だけを書く |
| `_bump_containing` | 閉じた PR の一覧を読めない・1 ページに収まらない | GitHub の誤り・版上げの PR が `PER_PAGE` 件を超えて更新された | `GitHubError`・件数 | 呼び出し元へ返す（件数は `BumpError`） | `stopped`・`unknown` か `precondition` | 何もしていない。打ち直せる |
| `_tagged` | 参照を読めない | 404 以外の GitHub の誤り | `GitHubError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown` | 何もしていない |
| `check` | PR の先端を取り込めない・PR を読めない | GitHub の誤り・閉じた PR | `GitError`・`GitHubError` | 呼び出し元へ返す | `stopped`・`precondition` か `unknown`（PR のチェックが落ちる） | 何もしていない。PR を更新すると打ち直される |
| `build` | PR の取り込みが衝突した | 2 本の PR が同じ箇所を変えた | `_merge_pulls` の `VerifyConflict` | 止める | `stopped`・`base_conflict`・衝突した PR の番号 | ref を動かしていない。一時の worktree は消す。どちらかの PR が直ると次の出来事で組み直される |
| `build` | push を断られた | 同時に別の組み直しが進めた・前の先端が祖先でない | `GitError` | 止める | `stopped`・`precondition` | ref は動いていない。次の出来事か手動の起動で組み直す |
| `build` | 宣言に検証の行が無い | `.ndf/project.json` に行が無い | `verify_of` の `DeclarationError` | 止める | `stopped`・`precondition` | 何もしていない |
| `_open_pulls` | PR の一覧を読めない | GitHub の誤り | `GitHubError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown` | 何もしていない |
| `_merge_pulls` | PR の先端を取り込めない | PR が直前に閉じた | `git fetch` の `GitError` | 止める | `stopped`・`precondition`・PR の番号 | 一時の worktree を消す。閉じた PR の出来事で組み直される |
| `_commit_on_top` | コミットを作れない | 一時の worktree の破損 | `GitError` | 止める | `stopped`・`precondition` | 一時の worktree を消す |
| `flow_of` | 宣言が壊れている・表に無い値 | 宣言の誤り | JSON の読み込み・`facts.rows` が空 | 止める（`DeclarationError`） | `stopped`・`precondition`・宣言の値 | 宣言を直して打ち直す |
| `require_github_flow` | 行が `github-flow` でない | 入れ替えの前 | `flow_of` の値 | 止める（`DeclarationError`） | `stopped`・`precondition` | 何もしていない |
| `verify_of` | 行が無い・2 つ以上・値が空 | 宣言の誤り | 行と鍵の確かめ | 止める（`DeclarationError`） | `stopped`・`precondition` | 宣言を直して打ち直す |
| `released` | 作った版の記録が無い | `confirm` の前に `record` を打った | `read_version_record` が None | 止める（`NotReleased`） | `stopped`・`not_applicable`・「`confirm` を先に打つ」 | 何も書いていない |
| `publish` | タグか GitHub Release を作れない | GitHub の誤り | `GitHubError` | 呼び出し元へ返す（441dc236e のまま） | `stopped`・`infra_wait` か `unknown` | 作れた分は残る。打ち直すと足りない分だけを作る |
| `_late` | 版上げのコミットの起点を読めない | `revision` が版上げの PR のマージでない | `origin_of` が None | 既定値で続ける（空） | 本文に節を足さない | 無し |
| `cmd_bump` | 版上げが止まった | `Bumper.bump` の行のどれか | `_ERRORS` と `GitHubError` | 呼び出し元へ返す（結果 JSON） | `Bumper.bump` の行と同じ。版上げの PR のコメントにも理由を書く | 同上 |
| `cmd_confirm` | 確かめが止まった | `confirm` の行のどれか | `Confirmed.kind` | 呼び出し元へ返す | `confirm` の行と同じ | 同上 |
| `cmd_pr_check` | 検査で破れが出た | 版数を変えた・ラベルが 2 つ以上・head が検証の ref | `problems` の戻り値 | 止める | `stopped`・`check_failed`・終了コード 1・`items` | PR を直すと次の出来事で打ち直される |
| `cmd_verify_ref` | 組み直しが止まった | `build` の行のどれか | `_ERRORS` | 呼び出し元へ返す | `build` の行と同じ | 同上 |
| `cmd_merge` | マージが止まった | `land` の行のどれか | `Landed.kind` | 呼び出し元へ返す | `stopped`・種類・`moves` は 2 つ | `land` の行と同じ |
| `_moves` | 止まった理由の種類が表に無い | `land` の新しい種類 | `merge_decide.suggested` | 既定値で続ける（`reject`） | `suggested` が `reject` | 無し |
| `_answer_merged` | マージされていない | 人がまだマージしていない | `trail.NotMerged` | 呼び出し元へ返す（441dc236e のまま） | `stopped`・`not_applicable` | 待ちの次の読み直しで打ち直す |
| `cmd_guard` | 本番の PR だった | スプリントの PR を `merge` のプランで流した | `release_plan.is_production_pr` | 止める（デプロイの工程へ渡す） | `stopped`・`production_pr`・終了コード 3・理由「デプロイの工程で流す」。雛形は `end` へ進む | 何も変えていない |
| `cmd_guard` | head が検証の ref だった | 検証の ref を main へ入れようとした | `verify_of` の ref と head の名前の比べ | 止める | `stopped`・`refused`・終了コード 1・理由「検証の ref は main へ戻さない」 | 何も変えていない |
| `cmd_assemble` | 組み立てが止まった | `assemble` の行のどれか | `_ERRORS` | 呼び出し元へ返す | `assemble` の行と同じ | 同上 |
| `cmd_materials` | 材料を組めない | `collect` の行のどれか | `_ERRORS` | 呼び出し元へ返す | `collect` の行と同じ | 何も変えていない |
| `cmd_cleanup` | `--branch` も `--pr` も無い・PR を読めない | 呼び出しの誤り・GitHub の誤り | 引数の確かめ・`GitHubError` | 止める | `stopped`・`invalid` か `unknown` | 何も外していない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `bump_decide.action` | 先端の版にタグが無い | `PUBLISH`（版を上げず、`publish` へ渡す） |
| `bump_decide.action` | タグがある・範囲に変化が無い | `NOTHING`（`not_applicable`。I11） |
| `bump_decide.action` | タグがある・変化がある・開いた版上げの PR が無い | `CREATE` |
| `bump_decide.action` | タグがある・変化がある・開いた版上げの PR があり版が同じ | `UPDATE` |
| `bump_decide.action` | タグがある・変化がある・開いた版上げの PR があり版が違う | `REPLACE`（閉じてから `CREATE`） |
| `Bumper._merge` | main の先端が head の祖先でない | マージしない。`state=reused` |
| `Bumper._merge` | 祖先で、`land_bump` が通った | `state=merged` |
| `bump_decide.level_of` | 該当のラベルが 0・1・2 以上 | `patch`・その幅・`LevelError` |
| `pr_check.problems` | head が検証の ref | `verify-ref` の破れ（ほかを見ない） |
| `pr_check.problems` | head が `release/v` で、差分が `only_bump` で空 | 破れなし（版上げの PR） |
| `pr_check.problems` | それ以外で、マニフェストの `version` が merge-base と PR の先端で違う | `version` の破れ。理由「版数は CD がマージの時点に上げる」 |
| `pr_check.problems` | 上げ幅のラベルが 2 つ以上 | `level` の破れ |
| `release_plan.guard_before_merge` | `MERGE_PART_IDS` のステップが無い | 空 |
| `release_plan.guard_before_merge` | 最初の該当のステップの直前が `release-guard` で、`exit_cause` の `GUARD_EXIT` が `production_pr`、`on_cause` の `production_pr` が `end` | 空 |
| `release_plan.guard_before_merge` | どれかを破る | 破ったものごとに 1 行 |
| `cmd_guard` | 本番ブランチが宣言から決まらない | `ok`（441dc236e のまま。本番ブランチを持たないリポジトリの `merge` のプランを止めない） |
| `cmd_guard` | 開いた PR が無い | `ok` |
| `cmd_guard` | 開いた PR の head が検証の ref | `refused` |
| `cmd_guard` | 開いた PR が main 宛て | `production_pr` |
| `confirm` | 版上げの PR のマージ・タグ・GitHub Release の 3 つがある | `ok` と記録の書き込み（既にあれば書かない） |
| `confirm` | どれかが無く、締め切りの前 | `CONFIRM_POLL_SECONDS` 秒待って読み直す |
| `confirm` | どれかが無く、締め切りを過ぎた | `deploy_failed` で止まる |
| `verify_decide.included` | 下書き・head が検証の ref・head が `release/v`・head のリポジトリが別（フォーク） | 含めない |

- 繰り返しの終わる条件: `confirm` は締め切り（`clock()` が始めの時刻 + `budget` を超える）で終わる。`Lander.land_bump` の待ちは 441dc236e の `_watch` の締め切りで終わる。`_merge_pulls` は PR の数だけ回り、衝突で止まる
- 計算量の注意: `verify_ref.build` は main 宛ての開いた PR 1 本ごとに `git fetch` と `git merge` を 1 回ずつ打つ。PR の数に比例して時間が延びるため、workflow の `timeout-minutes` は 30 分にする。`confirm` は読み直し 1 回ごとに GitHub の REST を 3 回（閉じた PR の一覧・タグの参照・タグの Release）打つ

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `AFTER_COMMANDS` | 定数 | `release_plan.py` | `frozenset({"prepare-next", "defer"})`（本番のプランに置かない副命令。I10） |
| `MERGE_PART_IDS` | 定数 | `release_plan.py` | `("merge-gate", "merge")`（並びの先にある方の直前に `release-guard` を求める） |
| `GUARD_ID` | 定数 | `release_plan.py` | `"release-guard"`（441dc236e のまま） |
| `GUARD_EXIT` | 定数 | `release_plan.py` | `causes.exit_code(guard_cause())`。`production_pr` の区分 `precondition` の終了コード 3（`contracts/facts/exit-codes.json`）。雛形の鍵との一致は雛形のテストが確かめる |
| `NEXT_MOVES` | 定数 | `merge_decide.py` | `(RERUN, REJECT)` |
| `Landed` | 型 | `land.py` | 欄 `sync_pr`・`version` を外す。ほかは 441dc236e のまま |
| `VERSION_MARK` | 定数 | `trail.py` | `<!-- ndf:release-version -->`（名前空間は `identity.FIXED_WORDS['workflow_namespace']` から組む） |
| `VERSION_LINE`・`TAG_LINE`・`BUMP_PR_LINE` | 定数 | `trail.py` | `"作った版:"`・`"タグ:"`・`"版上げの PR:"` |
| `Target` | 型（`production: str`・`pr: int`・`plugin: str \| None`） | `assemble.py` | `plugin` は None |
| `Built` | 型（`pr: int`・`version: str`・`url: str`・`facts: materials_decide.Facts`） | `assemble.py` | — |
| `MATERIALS_START`・`MATERIALS_END` | 定数 | `materials_decide.py` | `<!-- ndf:release-materials:start -->`・`<!-- ndf:release-materials:end -->` |
| `LEVEL_LABELS` | 定数 | `bump_decide.py` | `{"release:major": "major", "release:minor": "minor", "release:patch": "patch"}`。workflow とスキーマは写さず、`pr-check` の結果の理由にこの一覧を出す |
| `ORIGIN_LINE` | 定数 | `bump_decide.py` | `"起点:"` |
| `PUBLISH`・`NOTHING`・`CREATE`・`UPDATE`・`REPLACE` | 定数 | `bump_decide.py` | 同じ名前の小文字の文字列 |
| `BUMP_STATES` | 定数 | `bump_decide.py` | `("merged", "reused", "published", "not_applicable")`。結果のスキーマの列挙はこれと同じ値で、契約のテストが一致を確かめる |
| `OpenBump` | 型（`number: int`・`version: str`・`head_ref: str`・`head_sha: str`） | `bump_decide.py` | — |
| `Bumper` | 型（`root: Path`・`hub: GitHub`・`lander: land.Lander`・`production: str`・`today: str`） | `bump.py` | `today` は UTC の日付 |
| `Bumped` | 型（`state: str`・`version: str \| None`・`level: str \| None`・`pr: int \| None`・`landed: land.Landed \| None`） | `bump.py` | — |
| `Confirmed` | 型（`version`・`tag`・`bump_pr`・`kind`・`missing`・`detail`） | `confirm.py` | 止まったとき `kind` は `deploy_failed`、`missing` は `bump` か `publish` |
| `CONFIRM_POLL_SECONDS` | 定数 | `confirm.py` | `30.0`（`land.QUEUE_POLL_SECONDS` と同じ値を参照する） |
| `PrFacts` | 型（`head_ref`・`base_version`・`head_version`・`changed`・`labels`・`verify_ref`・`allowed`） | `pr_check.py` | — |
| `OpenPull` | 型（`number`・`head_sha`・`head_ref`・`head_repo`・`draft`） | `verify_decide.py` | — |
| `NEXT_LABEL` | 定数 | `verify_decide.py` | `"next"`（検証の版の接尾辞） |
| `VerifyDecl` | 型（`ref: str`・`marketplace: str`） | `declaration.py` | 宣言に行が無ければ止まる（既定値を持たない） |
| `GITHUB_FLOW` | 定数 | `declaration.py` | `"github-flow"`（表の行の id） |
| 行 `github-flow` | 設定値 | `contracts/domains/branch-flows.json` | 決定 5 の表の値。`default` は真で、`promote-whole` の `default` を偽にする |
| 種類 `production_pr` | 設定値 | `contracts/causes.json` | `{"kind": "production_pr", "meaning": "本番の PR を承認の前の工程として終え、デプロイの工程へ渡した", "std_type": "validation", "exit": "precondition", "route": "conductor", "retry": null}` |
| 検証の行 | 設定値 | `.ndf/project.json` の `delivery` | 設計文書の「入出力の契約」の JSON（`verify.ref` は `verify/next`、`verify.marketplace` は `ai-plugins-next`） |

本番のプラン（`release.json`）のステップは次のとおりである。`base` は使わなくなる（同期の PR を作らないため）。

```json
{"params": ["repo", "pr", "ci_budget"],
 "steps": [
   {"id": "merge", "run": ["ndf", "release-steps", "merge", "--root", "{{repo}}", "--pr", "{{pr}}", "--budget", "{{ci_budget}}"]},
   {"id": "confirm", "run": ["ndf", "release-steps", "confirm", "--root", "{{repo}}", "--pr", "{{pr}}", "--budget", "{{ci_budget}}"]},
   {"id": "record", "run": ["ndf", "release-steps", "record", "--root", "{{repo}}", "--pr", "{{pr}}"]},
   {"id": "cleanup", "run": ["ndf", "release-steps", "cleanup", "--root", "{{repo}}", "--pr", "{{pr}}"], "next": "end"}
 ]}
```

部品 `release-guard`（`parts/steps.json`）は次のとおりである。
`{"use": "release-guard"}` は、`merge.json` と `close.json` では `{"use": "merge"}` の直前、`check.json` では `{"use": "merge-gate"}` の直前に置く。

```json
{"id": "release-guard", "run": ["ndf", "release-steps", "guard", "--root", "{{repo}}", "--branch", "{{branch}}"],
 "exit_cause": {"1": "refused", "3": "production_pr"}, "on_cause": {"production_pr": "end"}}
```

workflow `ndf-next-release.yml` のジョブは次のとおりにする。どのジョブも `vars.NDF_RELEASE_RUNNER == 'ndf-next'` のときだけ出来事で動く。

| ジョブ | 引き金 | 打つ副命令 | token | 取り出すもの |
| --- | --- | --- | --- | --- |
| `pr-check` | `pull_request`（main 宛ての `opened`・`synchronize`・`reopened`・`labeled`・`unlabeled`） | `pr-check --pr` | `github.token`（`contents: read`・`pull-requests: read`） | PR 側の木（秘密を持たない） |
| `verify-ref` | `pull_request_target`（main 宛ての `opened`・`synchronize`・`reopened`・`closed`・`ready_for_review`）・`workflow_dispatch`（`step=verify-ref`） | `verify-ref` | `NDF_RELEASE_TOKEN`（push の 1 回だけ渡す） | main の先端（`persist-credentials: false`） |
| `bump` | `push`（main）・`workflow_dispatch`（`step=bump`） | `bump --budget` | `NDF_RELEASE_TOKEN` | main の先端 |
| `publish` | `bump` の後（`state` が `published`）・`workflow_dispatch`（`step=publish`） | `publish --revision` | `github.token` | main の先端 |
| `merge`・`approval` | 441dc236e のまま | `merge`・`record` | 441dc236e のまま | 既定ブランチ |
| `assemble` | `workflow_dispatch`（`step=assemble`、入力 `pr`） | `assemble --pr` | `NDF_RELEASE_TOKEN` | 既定ブランチ |

`concurrency` は `bump`・`publish` を `ndf-next-release-main` の 1 つのグループに、`verify-ref` を `ndf-next-verify` の 1 つのグループに入れる。
どちらも `cancel-in-progress: false` にする。入力 `channel`・`level` は外す。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 本番のプランの `merge` と、人の GitHub でのマージ | 本番の PR と main | GitHub のマージの照合（`expect_head`。STD1）。先にマージした側が勝ち、後の `merge` は断られる | 断られた `merge` を打ち直すと、マージ済みからの再開（`_resume_merged`）に入る（441dc236e のまま） |
| main への push ごとの `bump` | 版上げのブランチと版上げの PR | workflow の `concurrency`（`ndf-next-release-main`）で 1 本ずつ流す。push は先端を進めるだけで、動いていれば git が断る | 同じ main の先端で 2 回打つと、2 回目は開いた版上げの PR を `UPDATE` で読み、差分が同じならコミットを足さない（I8） |
| `bump` の `_merge` と、別の PR の main へのマージ | main の先端 | マージの直前に main の先端が head の祖先かを確かめ、祖先でなければマージしない。確かめとマージの間の移動は防がない（設計文書の「未確認のまま残ること」） | 次の push の `bump` が `UPDATE` で進める。間に入った PR は `publish` の本文の末尾と次の版の節に載る |
| `confirm` と CD の `bump`・`publish` | 版上げの PR・タグ | 排他を取らない。`confirm` は読むだけで、書くのは本番の PR の作った版の記録 1 件 | 打ち直すと同じ版の記録があれば書かない |
| `verify-ref` の組み直しどうし | 検証の ref | workflow の `concurrency`（`ndf-next-verify`）。push は fast-forward だけで、先端が動いていれば断られる | 断られたら止まり、次の出来事で main と開いた PR から組み直す。組み直しは毎回 main の先端から始めるため、途中で止まった組み直しの残りを持たない |
| `verify-ref` と `bump` | 無し | 検証の ref は main と版上げのブランチを書かない | — |
| `pr-check` と `bump` | 版上げの PR | 排他を取らない。`pr-check` は読むだけ | — |
| `record` の打ち直し | 含めた PR のコメント | 441dc236e のまま（同じ版の記録があれば書かない） | 版は作った版の記録から読むため、打ち直しても版が変わらない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_merge` | 予定の版・承認（経路・承認した人のログイン・3 つの SHA）・落ちたチェック・次の手 2 つと既定の手 | 結果 JSON の `outputs`・`items` | token |
| `cmd_bump` | 上げた版・上げ幅・版上げの PR の番号・`state`・止まった理由 | 結果 JSON の `outputs` と、止まったときは版上げの PR のコメント | token |
| `Bumper._replace` | 置き換えた古い版と新しい版 | 古い版上げの PR のコメント | 無し |
| `cmd_confirm` | 作った版・タグ・版上げの PR。止まったときは足りないものと「本番はマージ済み」 | 結果 JSON の `outputs`・`items`・`routes` | token |
| `write_version_record` | 作った版・タグ・版上げの PR の 3 行 | 本番の PR のコメント | token・メールアドレス |
| `cmd_pr_check` | 破れの種類・パスかラベル・理由 | 結果 JSON の `items`（PR のチェックの出力に出る） | 無し |
| `cmd_verify_ref` | 組んだ ref と先端・検証の版・含めた PR の番号と先端・衝突した PR | 結果 JSON の `outputs`・`items` | token。取り込んだ PR の中身 |
| `publish` | 次の版の節に載る PR の番号 | GitHub Release の本文の末尾と結果 JSON の `items` | 無し |
| `cmd_guard` | PR の番号・宛先・「デプロイの工程で流す」か「検証の ref は main へ戻さない」 | 結果 JSON の `summary` | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `guard_before_merge` | `release_plan.guard_first` | 441dc236e の `release_plan.py` を読んだ | 使えない（最初のステップだけを見る）。置き換え、`_steps`・`_run` を使い直す |
| `Lander.land_bump` | `Lander._merge`・`_blocker` | 441dc236e の `land.py` を読んだ | `_blocker` は使えない（本番ブランチ宛ての PR の証跡を求める）。`_watch`・`_bypass`・`hub.merge_as_admin` を使う |
| `render_version_record` | `trail.render_merge_record` | 441dc236e の `trail.py` を読んだ | 使えない（人のマージの 5 行を持つ）。行の組み方と印の名前空間を同じにする |
| `write_version_record` | `trail.write_merge_record`・`_existing_record` | 同上 | `_existing_record` を使う（印・値・編集なし・信用で既存の記録を探す） |
| `read_version_record` | `trail._line_of`・`hub.trusts` | 同上 | 使う |
| `splice` | `materials_decide.render` | 441dc236e の `materials_decide.py` を読んだ | 使えない（本文の全体を組む）。`render` の出力を印で囲んで差し込む |
| `level_of` | `version_decide.LEVELS` | `version_decide.py` を読んだ | 使う（値の検証）。ラベルからの対応は無い |
| `widest` | `semver.Version.NAMES` の順 | `version_decide.py` の `LEVELS = semver.Version.NAMES[:3]` | 使う（`LEVELS` の位置で比べる） |
| `planned` | `version_decide.production_next` | 同上 | 使う（呼ぶだけ） |
| `origin_of` | `trail._line_of` | `trail.py` を読んだ | 使えない（リリースの領域の判定が `trail` を import すると純粋でなくなる）。行の読み 1 つを持つ |
| `message_of` | `assemble.assemble` の題 `Release: <プラグイン> v<版>` | `assemble.py` を読んだ | 題の形を使い直す |
| `only_bump` | `assemble._rewritten` | `assemble.py` を読んだ | 使えない（削除する関数で、ファイルの集合を組む側）。集合は `write_bump` の戻り値で受ける |
| `action` | `version_decide.clear_of` | `version_decide.py` を読んだ | 使えない（版の繰り上げだけ）。判定表を持つ |
| `late` | `included_decide.numbers` | `grep -n "def numbers" plugins/ndf-next/src/ndf/domains/release/included_decide.py` | 含む PR の番号の読みは呼び出し元（`tagging._late`）が使う。差を取るだけを持つ |
| `Bumper.bump` | release-please-action（LIB1）・semantic-release（LIB2） | 設計文書の「外部の調査」 | 使えない（上げ幅をコミットの書式から読む。決定 7） |
| `Bumper._span` | `gather.Range`・`gather.included` | `gather.py` を読んだ | 使う（範囲の型と含む PR の読み） |
| `Bumper._open_bump` | `assemble.open_release_heads` | `assemble.py` を読んだ | 使えない（削除する。宛先の本番ブランチの開いた PR を読む形は同じ REST で書く） |
| `Bumper._create` | `assemble._commit_and_push`・`_open_pr` | 同上 | 同じ git と REST の打ち方を `bump.py` に移す（`assemble.py` からは消える） |
| `Bumper._update` | `assemble._merge_in` | 同上 | 使えない（コードの衝突を許す範囲が違う）。衝突したら止める |
| `Bumper._replace` | `cli.cmd_defer` の `PATCH /pulls/<番号>` | `cli.py` を読んだ | 打ち方を使い直す（`cmd_defer` は削除する） |
| `Bumper._merge` | `ndf.adapters.git.Git.is_ancestor` | `grep -n "def is_ancestor" plugins/ndf-next/src/ndf/adapters/git.py` | 使う |
| `write_bump` | `assemble._write` | `assemble.py` を読んだ | 移す（中身は同じ。書いたパスを返すように変える） |
| `planned_for` | `gather.shipped` | `gather.py` を読んだ | 使う（main の版の読み） |
| `confirm` | `land.Lander._await_queue` の待ち | `land.py` を読んだ | 待ちの形（`clock`・`sleep` の差し替え）を使い直す |
| `_bump_containing` | `record.included_of`・`hub.compare_commits` | `record.py` を読んだ | 使えない（含めた PR を数える）。`Git.is_ancestor` を使う |
| `_tagged` | `tagging._missing` | `tagging.py` を読んだ | 使う（404 を偽と読む） |
| `_has_release` | `tagging._missing`（`/releases/tags/<タグ>` を読む `_release` と同じ照会） | `tagging.py` を読んだ | 使う（404 を偽と読む） |
| `problems` | `release_plan.is_production_pr` | `release_plan.py` を読んだ | 使えない（宛先だけを見る）。判定を持つ |
| `check` | `gather.fetch`・`plugins_mod.load`・`plugins_mod.version` | `grep -n "^def " plugins/ndf-next/src/ndf/domains/release/plugins.py` | 使う |
| `included` | `assemble.open_release_heads` の head の判定 | `assemble.py` を読んだ | 使えない（削除する）。判定を持つ |
| `version_of` | `version_decide.dev_next` | `version_decide.py` を読んだ | 使えない（番号を足す形で、時刻を持たない）。`parse` で形を確かめる |
| `marketplace_of` | `plugins_mod.bump` の `marketplace.json` の書き換え | `plugins.py` の 125〜135 行を読んだ | 使えない（版だけを書く）。名前の書き換えを持ち、版は `plugins_mod.bump` で書く |
| `build` | `ndf.adapters.git.Git` の `run`・`attempt` | `git.py` を読んだ | 使う。一時の worktree は `git worktree add --detach` で作る（`cleanup.git_remover` で消す） |
| `_open_pulls` | `GitHub.pulls_by_head` | `grep -n "def pulls_by_head" plugins/ndf-next/src/ndf/adapters/github.py` | 使えない（head の名前で引く。`Pull` は下書きと head のリポジトリを持たない）。`hub.rest` の生の応答を読む |
| `_merge_pulls` | `gather.fetch` | `gather.py` を読んだ | 使えない（ブランチの名前を fetch する）。`refs/pull/<番号>/head` を fetch する |
| `_commit_on_top` | 無し | `grep -rn "commit-tree" plugins/ndf-next/src/ndf` の結果が 0 件 | 無い。`git commit-tree` を `Git.run` で打つ |
| `flow_of` | `domains/worktree/merged_decide` の `facts.rows("branch-flows", ...)` | `merged_decide.py` を読んだ | 読み口（`facts.rows`）を使う。worktree の領域は import しない |
| `require_github_flow` | `flow_of` | — | 使う |
| `verify_of` | `declaration.production_admin` の `_admin_declaration` | `declaration.py` を読んだ | 使う（宣言はメインディレクトリから読み、PR の作業場所が足した宣言で ref を選べないようにする） |
| `_late` | `bump_decide.origin_of`・`included_decide.numbers` | 上の行 | 使う |
| `cmd_bump` | `cli.ARGS`・`HANDLERS` | `cli.py` を読んだ | 使う（表へ行を足すと名前が作られる） |
| `cmd_confirm` | 同上 | 同上 | 使う |
| `cmd_pr_check` | 同上 | 同上 | 使う |
| `cmd_verify_ref` | 同上 | 同上 | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `forbidden` | 雛形をすべて `expanded` で開き、`release: true` のものが空 | 最初のステップの前に 1 つ足すと 1 行 | `release.json` に `{{worker}}`・`push`・`prepare-next` のステップを戻すと行を返す |
| `guard_before_merge` | `merge`・`check`・`close` を開いた形が空 | `merge` の部品を持たない雛形で空 | `release-guard` を外す・先頭へ移す・`on_cause` を `ci-fix` にする・鍵を `GUARD_EXIT` と違えると行を返す |
| `guard_cause` | `production_pr` を返し、`GUARD_EXIT` が 3 | — | 表に無い種類にすると `causes.exit_code` が落ちる |
| `Lander.land` | 通った本番の PR（head が `sprint/` で始まる）で `approval` を持ち、PR の作成を呼ばない | マージキューの後も同じ | 落ちたチェックで push もマージも呼ばない。先端が動くと `refused` |
| `Lander._resume_merged` | 人のマージの記録を書き、PR の作成を呼ばない | 記録が既にあれば書かない | 記録を書けなければ `record_error` |
| `Lander._released` | `production_sha` が `merge_sha` | `version` の欄が無い | PR を読めなければ誤りが届く |
| `Lander.land_bump` | 差分が版だけ・チェックが通る・迂回の宣言がある PR を `merge_as_admin(expect_head=先端)` で 1 回マージ | 証跡のコメントが 1 件も無くても止まらない | 外れたパスで `refused`。宣言なしで `precondition`。チェックが落ちてマージを呼ばない |
| `render_version_record` | 印と 3 行を持つ | — | 接尾辞のある版で `ValueError` |
| `write_version_record` | 書いて `(True, URL)` | 同じ版の記録があれば `(False, URL)` | 信用しない書き手の同じ印は数えずに書く。GitHub の誤りで `TrailError` |
| `read_version_record` | 最新の記録の版 | 記録が 2 件なら新しい方 | 信用しない書き手の記録は読まない |
| `collect` | 範囲が main..PR の先端で、`facts.head` が PR の先端 | ラベル `release:minor` で予定の版が MINOR | 行が `promote-whole` で `precondition`。ラベル 2 つで `invalid` |
| `assemble` | 本文の承認資料の節だけが変わり、ブランチの作成と push を呼ばない | 節が無い本文の末尾に足す | 本文の書き込みの誤りが届く |
| `_span` | 両端が `origin/main` と `refs/pull/<番号>/head` | — | fetch の誤りで `GitError` |
| `_facts` | 含む PR と変更を読む | 文書だけの変更で `NothingToRelease` | — |
| `splice` | 印の間を差し替え、外を変えない | 印が無ければ末尾に足す | 印が片方だけなら末尾に足し、壊れた印を残さない |
| `level_of` | `release:major` で `major` | ラベル無しで `patch` | 2 つで `LevelError` |
| `widest` | `patch`・`minor` で `minor` | 空で `patch` | — |
| `planned` | `11.0.0` と `minor` で `11.1.0` | — | 接尾辞のある版で `VersionError` |
| `origin_of` | `message_of` の出力から同じ SHA | 行が無ければ None | 40 桁でない値で None |
| `message_of` | 題と起点の行を持つ | — | — |
| `only_bump` | 版と CHANGELOG だけで空 | 空の差分で空 | ほかのパスを 1 つ足すとそのパスを返す |
| `action` | 判定表の 5 行をそれぞれ返す | — | — |
| `late` | 載っていない番号を小さい順 | 全部載っていれば空 | — |
| `Bumper.bump` | 版 X.Y.Z の main に MINOR の PR が入ると X.(Y+1).0 の版上げの PR を作ってマージし、`state=merged` | 同じ先端で 2 回打つと版上げの PR は 1 本。範囲が文書だけで `not_applicable` | 開いた版上げの PR が 2 本で `precondition`。行が `promote-whole` で `precondition` |
| `Bumper._span` | 起点が前の版上げのコミットの行の SHA | 版上げのコミットが無ければタグのコミット | 起点が main の祖先でなければ `BumpError` |
| `Bumper._open_bump` | 開いた 1 本を読む | 0 本で None | 2 本で `BumpError` |
| `Bumper._create` | 先端の親が main の先端で、差分が版だけ | 同じ名前のブランチが版上げだけを持てば PR だけを作る | 宣言の手順が落ちると push しない |
| `Bumper._update` | PATCH の PR が続くと、同じ PR へ main の取り込みと版の節の組み直しのコミットを足す | 差分が同じならコミットを足さない | 衝突で `merge --abort` の後に止まる。push に強制の指定が無い（偽の git の呼び出しの記録で見る） |
| `Bumper._replace` | PATCH の後に MINOR が入ると古い PR を閉じ、ブランチを消し、理由のコメントを書く | — | 閉じるのを断られると新しい PR を作らない |
| `Bumper._merge` | 祖先なら `land_bump` を呼ぶ | 祖先でなければ呼ばずに `reused` | `land_bump` の止まった結果を返す |
| `write_bump` | 版のファイルと CHANGELOG の節を書き、パスを返す | CHANGELOG が無ければ版だけ | 版のファイルが読めなければ誤りが届く |
| `planned_for` | ラベル `release:minor` と main の版から MINOR の版 | マージ済みは第 1 親の版から求める | ラベルを読めなければ None |
| `confirm` | 版上げの PR のマージ・タグ・GitHub Release がそろうと版を返し、記録を 1 件書く。タグだけで Release が無ければ待ち続ける | 打ち直しで記録を 2 件にしない | 締め切りで `deploy_failed`・`missing` と「本番はマージ済み」。`ok` を返さない |
| `_bump_containing` | 含む版上げの PR のうち最も小さい版を返す | 含むものが無ければ None | 件数が `PER_PAGE` 以上で `BumpError` |
| `_tagged` | 参照があれば真 | 404 で偽 | ほかの誤りが届く |
| `_has_release` | Release があれば真 | 404 で偽 | ほかの誤りが届く |
| `problems` | 版を変えない PR で空 | 版上げの PR で空 | 版を変えた PR で `version`、ラベル 2 つで `level`、head が検証の ref で `verify-ref` |
| `check` | 行が `github-flow` で `problems` の結果 | 行が `promote-whole` で None | PR を読めなければ誤りが届く |
| `included` | P1・P2 を番号の順に返す | 下書き・検証の ref・`release/v`・フォークを外す | — |
| `version_of` | `11.0.0` と時刻から `11.0.0-next.20261115120000` | — | 版でない値で `VersionError` |
| `marketplace_of` | `name` だけが変わる | — | `name` の無い中身でも足す |
| `build` | P1・P2 を取り込み、`marketplace.json` の名前が宣言の値で、先端が前の先端を親に持つ | 初回（ref が無い）は HEAD だけが親 | P1 と P2 が衝突すると push を呼ばず衝突した PR を返す。push に強制の指定が無い。一時の worktree を消す |
| `_open_pulls` | 2 ページを読み切る | 0 件で空 | 誤りが届く |
| `_merge_pulls` | 順に取り込む | 0 本で何もしない | 衝突で `VerifyConflict`・`merge --abort` を呼ぶ |
| `_commit_on_top` | 2 つの親を持つ | `previous` が None で 1 つ | — |
| `flow_of` | `promotion: direct` で `github-flow` | `branch_flow` が無ければ既定の行 | 表に無い値で `DeclarationError` |
| `require_github_flow` | `github-flow` で何もしない | — | `promote-whole` で理由つきの `DeclarationError` |
| `verify_of` | 行の 2 つの値を返す | — | 行が無い・2 つ・空で `DeclarationError` |
| `released` | 作った版の記録の版を使う | head が `sprint/` でも読める | 記録が無ければ `NotReleased` |
| `publish` | 版上げの PR のマージコミットでタグと GitHub Release を作る | 遅れて入った PR が無ければ本文に節を足さない | 遅れて入った PR があれば本文の末尾に並べる |
| `_late` | 起点から第 1 親までの載らない PR | 起点が読めなければ空 | — |
| `cmd_bump` | `outputs` に 4 つの鍵 | `state=reused` も `ok` | 止まった例外が種類つきの `stopped` になり、版上げの PR に理由のコメントを書く |
| `cmd_confirm` | `outputs` に 3 つの鍵 | — | 止まったとき `routes` が `rerun` の 1 つ |
| `cmd_pr_check` | 破れなしで終了コード 0 | 行が `github-flow` でなければ 0 | 破れで終了コード 1 と `items` |
| `cmd_verify_ref` | `outputs` に 4 つの鍵 | 含める PR が 0 本でも main の先端で組む | 衝突で `base_conflict` |
| `cmd_merge` | 値の項目に `sync_pr` が無く、`outputs.version` が予定の版 | — | 止まった結果の `moves` が 2 つで `defer` を持たない |
| `_moves` | 2 つの手 | `released` で `rerun` だけ | 表に無い種類で既定の手が `reject` |
| `_answer_merged` | head が `sprint/` の PR で予定の版を出す | 版を読めなければ `outputs` を持たない | マージされていなければ `not_applicable` |
| `cmd_guard` | ベースブランチ宛ての PR で 0 | 開いた PR が無いブランチで 0 | main 宛ての PR で `production_pr`・終了コード 3。head が検証の ref で `refused`・終了コード 1 |
| `cmd_assemble` | `outputs` に `pr`・`version` | — | 引数 `--channel` を渡すと引数の誤り |
| `cmd_materials` | `--pr` の材料を出す | — | `--source` を渡すと引数の誤り |
| `cmd_cleanup` | `--pr` の head の worktree を外す | `--branch` があれば PR を読まない | どちらも無ければ `invalid` |
