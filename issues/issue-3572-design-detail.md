# #3572 の詳細設計: 承認した先端だけを本番ブランチへ入れ、承認の後に直しのステップを持たない

設計文書 [issue-3572-design.md](issue-3572-design.md) の「構成要素」を、ファイルと関数の粒度へ下ろした文書である。
置き場のパスはすべて `plugins/ndf-next/` からの相対で書く。語（承認した先端・先端の照合・承認の経路・差し戻し・次の版への回し・
直しのステップ）は設計文書の「用語」の表の意味で使う。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf-next/
├── contracts/
│   ├── schemas/io/
│   │   ├── release-steps.args.schema.json
│   │   └── release-steps.result.schema.json
│   └── templates/
│       ├── merge.json
│       ├── params.json
│       └── release.json
├── src/ndf/
│   ├── domains/release/
│   │   ├── assemble.py
│   │   ├── cli.py
│   │   ├── declaration.py
│   │   ├── land.py
│   │   ├── merge_decide.py *
│   │   ├── prepare.py *
│   │   ├── release_plan.py *
│   │   └── trail.py
│   └── progress/gate/
│       └── release.py
└── tests/
    ├── domains/release/
    │   ├── test_release_approval.py
    │   ├── test_release_assemble.py
    │   ├── test_release_plan.py *
    │   └── test_release_prepare.py *
    └── progress/gate/
        └── test_gate_release.py
```

依存の向きは一方向で、循環は無い。純粋な判定（`release_plan`・`merge_decide`）は GitHub と git のアダプタを読まず、
`version_decide` だけに依存する。GitHub を読み書きするモジュール（`land`・`trail`・`prepare`・`assemble`）が判定を呼び、副命令の
エントリポイント（`cli`）がそれらを呼ぶ。承認ゲートの待ち（`progress/gate/release.py`）はリリースの領域のモジュールを import しない。
副命令 `ndf release-steps answer` を子プロセスで打ち、結果 JSON だけを読む。コンテキストの関係の顧客 / 供給者を、import の向きでも
守るためである。雛形（`contracts/templates/`）はコードを持たず、副命令を並べるだけである。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf-next/contracts/templates/release.json` | 変更 | 本番のプランを `merge` → `record` → `prepare-next` の 3 ステップにし、worker のステップを外す | `params.json` |
| `plugins/ndf-next/contracts/templates/merge.json` | 変更 | 最初のステップに `release-guard` を置き、本番の PR を `merge` のプランで流さない | `params.json`・`parts/steps.json` |
| `plugins/ndf-next/contracts/templates/params.json` | 変更 | 本番のプランが使う引数 `pr` の意味を足す | 無し |
| `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | 新設 | 雛形を展開したプランが承認の後に直しのステップを持たないかと、PR を `merge` のプランで流してよいかを判定する | 無し |
| `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | 新設 | マージ済みの PR の事実と承認の証跡から、承認の経路・承認した人・承認した先端・承認ゲートの承認の先端を決める | 無し |
| `plugins/ndf-next/src/ndf/domains/release/prepare.py` | 新設 | 出した版の次の PATCH のリリースブランチを、本番ブランチへ入ったマージコミットから 1 本だけ作る | `version_decide`・`ndf.adapters.github` |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | マージの結果に版・承認の経路・落ちたチェックを足し、人のマージの記録を書く | `merge_decide`・`trail`・`version_decide` |
| `plugins/ndf-next/src/ndf/domains/release/trail.py` | 変更 | 承認の証跡に承認の経路の行を足し、人のマージを承認とする証跡と人のマージの記録を書き、読む | `approval_decide`・`merge_decide` |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | 既にあるリリースブランチを進め、開いた本番の PR のあるブランチを組み直し、PR を作る前に改名する | `version_decide`・`gather`・`materials_decide` |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 変更 | 宣言の本番ブランチの名前を読む | 無し |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | 副命令 `prepare-next`・`guard`・`defer` を足し、`assemble`・`materials`・`answer`・`merge` の引数と結果を変える | `land`・`trail`・`assemble`・`prepare`・`release_plan`・`declaration` |
| `plugins/ndf-next/src/ndf/progress/gate/release.py` | 変更 | リリース承認ゲートの待ちで、本番の PR がマージ済みなら副命令に証跡を書かせ、承認ゲートの答えを承認にする | `ndf.progress.invoke`（子プロセスの起動） |
| `plugins/ndf-next/contracts/schemas/io/release-steps.args.schema.json` | 変更 | 足した副命令と引数の形を持つ | 無し |
| `plugins/ndf-next/contracts/schemas/io/release-steps.result.schema.json` | 変更 | 足した `outputs` と `items` の形を持つ | 無し |
| `plugins/ndf-next/tests/domains/release/test_release_plan.py` | 新設 | 雛形の検査と `guard` の判定を縛る | `release_plan`・`ndf.core.route` |
| `plugins/ndf-next/tests/domains/release/test_release_prepare.py` | 新設 | 次の版のリリースブランチの用意を縛る | `prepare`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_approval.py` | 変更 | 落ちたチェックと次の手・人のマージの記録・経路の判定を縛る | `land`・`trail`・`merge_decide`・`_fakes` |
| `plugins/ndf-next/tests/domains/release/test_release_assemble.py` | 変更 | 既にあるブランチの進め・組み直し・改名を縛る | `assemble`・`_fakes` |
| `plugins/ndf-next/tests/progress/gate/test_gate_release.py` | 変更 | 承認ゲートの待ちが人のマージを承認として書くことを縛る | `progress/gate/release.py` |

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | モジュール | `forbidden`（新設）・`guard_first`（新設）・`is_production_pr`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | モジュール | `MergeFacts`・`Approved`・`Approval`（新設の型）・`facts_of`（新設）・`decide`（新設）・`suggested`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/prepare.py` | モジュール | `Prepared`（新設の型）・`next_branch`（新設）・`prepare_next`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | `Lander` | `land`（変更）・`_resume_merged`（変更）・`_watch`（変更）・`_blocker`（変更）・`_approval`（新設）・`_record_merge`（新設）。`Landed` に `version`・`approval`・`failed`・`merge_record` を足す |
| `plugins/ndf-next/src/ndf/domains/release/trail.py` | モジュール | `answer`（変更）・`answer_merged`（新設）・`approvals`（新設）・`render_merge_record`（新設）・`write_merge_record`（新設）・`_route_of`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/assemble.py` | `Target`・モジュール | `Target.source`（足す欄）・`assemble`（変更）・`collect`（変更）・`_existing`（新設）・`_advance`（新設）・`_rename`（新設）・`_reassemble`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | モジュール | `production_branch`（新設） |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | モジュール | `cmd_prepare_next`・`cmd_guard`・`cmd_defer`（新設）・`cmd_answer`・`cmd_merge`・`cmd_assemble`・`cmd_materials`（変更）・`_production_of`（新設）。`ARGS` と `_ERRORS` に行を足す |
| `plugins/ndf-next/src/ndf/progress/gate/release.py` | モジュール | `take_reply`（変更）・`_merged_reply`（新設） |

`cmd_record` は `--production` を省けるようにするだけで、責務・失敗の扱いを変えないため「関数の仕様」には書かない。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `forbidden` | `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | 新設 | `forbidden(plan: Mapping[str, Any]) -> list[str]` | `release: true` のプランが不変条件 I1・I2 を破るステップを返す | `plan` は `ndf.core.route.expanded` の形（`use` を開き、差し込みは埋めていない） | `release` が真でなければ空。真なら、`run[0]` が `{{worker}}` のステップ・id が `FIX_STEPS` のステップ・最初のステップの `run` が `MERGE_RUN` で始まらないことを、1 件 1 行で返す | 無し |
| `guard_first` | `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | 新設 | `guard_first(plan: Mapping[str, Any]) -> list[str]` | `merge` のプランの最初のステップが `release-guard` で、その止まる原因に行き先が無いかを返す | `plan` は `expanded` の形 | 最初のステップの id が `GUARD_ID` で、`exit_cause` の `3` が `refused`、`on_cause` に `refused` が無いときだけ空 | 無し |
| `is_production_pr` | `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | 新設 | `is_production_pr(base_ref: str, production: str) -> bool` | PR を `merge` のプランで流してはいけないか（I8）を返す | `production` は空でない | `base_ref == production` のときだけ真 | 無し |
| `facts_of` | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | 新設 | `facts_of(raw: Mapping[str, Any]) -> MergeFacts` | 「Get a pull request」の応答（STD2）から、マージの事実を読む | `raw` は GitHub の REST の応答 | `merged` が偽なら `MergeFacts(merged=False, …)`。真なら `merged_by` のログイン・`head.sha`・`merge_commit_sha` を持つ。`merged_by` が null なら `by` は空文字 | 無し |
| `decide` | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | 新設 | `decide(facts: MergeFacts, found: Sequence[Approved]) -> Approval \| None` | マージ済みの PR の承認の経路・承認した人・承認した先端・承認ゲートの承認の先端を決める（I5） | `found` は信用する書き手の承認の証跡だけ（`trail.approvals`）を古い順に並べたもの | マージされていなければ None。`approved_head` と `merged_head` は常に `facts.head`。判定の表（「処理の手順」）のとおり `route`・`by`・`gate_head` が決まる | 無し |
| `suggested` | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | 新設 | `suggested(kind: str) -> str` | 止まった理由の種類から既定の次の手を 1 つ返す（決定 3） | 無し | `RERUN_KINDS` にあれば `rerun`、ほかは `reject`。`defer` は返さない | 無し |
| `next_branch` | `plugins/ndf-next/src/ndf/domains/release/prepare.py` | 新設 | `next_branch(released_ref: str) -> tuple[str, str]` | 出した版のリリースブランチの名前から、次の版とそのブランチの名前を返す | `released_ref` は `release/v<版>` | （PATCH を 1 つ上げた版, `release/v<その版>`）。開発版の接尾辞は付けない | 無し |
| `prepare_next` | `plugins/ndf-next/src/ndf/domains/release/prepare.py` | 新設 | `prepare_next(hub: GitHub, number: int, production: str) -> Prepared` | マージ済みの本番の PR から、次の版のリリースブランチを本番ブランチのマージコミットから 1 本だけ作る（I6） | `number` は本番の PR | origin に `release/v<次の版>` が 1 本だけあり、新しく作ったときは先端が PR の `merge_sha` である。既にあれば作らず `created=False` | GitHub の REST（ブランチの照会と `POST /git/refs`） |
| `Lander.land` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | `land(self, number: int) -> Landed` | 今の振る舞いに加え、結果に版・承認・落ちたチェックを載せる | 今のまま | 本番の PR をマージした（再開を含む）結果は `version` と `approval` を持つ。止まった結果は `failed` に落ちたチェックを持ち、push もマージも打っていない | GitHub の REST（今のまま） |
| `Lander._watch` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | `_watch(self, pull: Pull, deadline: float) -> tuple[str \| None, tuple[ci.Check, ...]]` | 必須のチェックを待ち、止まった理由の種類と落ちたチェックを返す | 今のまま | 通れば `(None, ())`。止まれば種類と、最後の読みの `failed_tests` と `failed_other` | GitHub の REST（今のまま） |
| `Lander._blocker` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | `_blocker(self, pull: Pull, deadline: float, bypass: bool = False) -> Landed \| None` | 止まった結果に落ちたチェックを足す | 今のまま | チェックで止まった `Landed` の `failed` が `_watch` の落ちたチェックである | GitHub の REST（今のまま） |
| `Lander._approval` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 新設 | `_approval(self, number: int) -> merge_decide.Approval \| None` | マージ済みの PR を読み直し、承認を決める | 本番ブランチ宛ての PR | マージ済みなら `merge_decide.decide` の結果、そうでなければ None | GitHub の REST（PR の照会・コメントの一覧） |
| `Lander._record_merge` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 新設 | `_record_merge(self, number: int, done: Landed, approval: merge_decide.Approval) -> Landed` | 経路が `github-merge` のときだけ人のマージの記録を書き、結果に URL か書けなかった理由を足す | `done.merged` が真 | 経路が `gate` なら `done` に `approval` を足しただけ。`github-merge` なら `merge_record` か `record_error` を持つ | GitHub の REST（コメントの作成） |
| `Lander._resume_merged` | `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | `_resume_merged(self, number: int, pull: Pull, deadline: float) -> Landed` | 今の戻すマージと迂回の記録の補いに加え、承認を決めて人のマージの記録を書く | 今のまま | 本番ブランチ宛てなら `approval` を持ち、経路が `github-merge` なら記録の書き込みを 1 回試みている | GitHub の REST（今のまま＋コメントの作成） |
| `answer` | `plugins/ndf-next/src/ndf/domains/release/trail.py` | 変更 | `answer(hub: GitHub, number: int, value: str, by: str, presented: str) -> approval_decide.Judgment` | 証跡に承認の経路 `gate` の行を足して書く | 今のまま | 書いた証跡が行 `承認の経路: gate` を持つ。ほかは今のまま | GitHub の REST（今のまま） |
| `answer_merged` | `plugins/ndf-next/src/ndf/domains/release/trail.py` | 新設 | `answer_merged(hub: GitHub, number: int) -> tuple[approval_decide.Judgment, merge_decide.MergeFacts]` | マージ済みの本番の PR に、マージした先端への承認の証跡（経路 `github-merge`）を書く | 無し | PR がマージ済みなら、マージした先端への承認の証跡が 1 件あり（既にあれば書かない）、判定が `approved` である | GitHub の REST（PR の照会・コメントの作成） |
| `approvals` | `plugins/ndf-next/src/ndf/domains/release/trail.py` | 新設 | `approvals(hub: GitHub, number: int) -> list[merge_decide.Approved]` | 信用する書き手の承認の証跡を、経路と承認した人つきで古い順に読む | 無し | 差し戻しと信用しない書き手のコメントを含まない。経路の行が無い証跡は `gate` | GitHub の REST（コメントの一覧） |
| `render_merge_record` | `plugins/ndf-next/src/ndf/domains/release/trail.py` | 新設 | `render_merge_record(approval: merge_decide.Approval) -> str` | 人のマージの記録の本文を組む | `approval.merged_head` と `approval.merge_sha` は 40 桁の SHA | 印 `MERGE_MARK` と 5 行（承認の経路・承認した人・マージした先端・マージコミット・承認ゲートの承認の先端）を持つ。証跡の印を持たない | 無し |
| `write_merge_record` | `plugins/ndf-next/src/ndf/domains/release/trail.py` | 新設 | `write_merge_record(hub: GitHub, number: int, approval: merge_decide.Approval) -> tuple[bool, str]` | 人のマージの記録を 1 回だけ書く（決定 8） | 経路が `github-merge` | 信用する書き手の編集されていない記録で同じマージした先端のものがあれば書かずに `(False, URL)`、無ければ書いて `(True, URL)` | GitHub の REST（コメントの一覧・作成） |
| `assemble` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `assemble(root: Path, hub: GitHub, target: Target) -> Built` | 既にあるリリースブランチを作り直さずに進め、開いた本番の PR のあるブランチを組み直す（I6・I7） | 今のまま | ブランチが無ければ今のまま作る。あれば `_advance` で進めてから版と CHANGELOG のコミットを足す。開いた本番の PR の head なら PR を作らずに本文を書き直す。強制 push を打たない | git（fetch・switch・merge・commit・push）・GitHub の REST |
| `collect` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 変更 | `collect(root: Path, hub: GitHub, target: Target) -> materials_decide.Facts` | `target.source` に合わせて範囲の先端を決める | 今のまま | `release` なら範囲の先端は origin のリリースブランチ、`base` なら今のまま | git（fetch） |
| `_existing` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 新設 | `_existing(hub: GitHub, target: Target, current: str, planned: str) -> tuple[str \| None, int \| None]` | 組むべき既存のリリースブランチの名前と、その開いた本番の PR の番号を返す | `current` は本番ブランチの今の版、`planned` は計画した版 | 計画した版のブランチか、同じ前の版から PATCH で用意したブランチがあればその名前。PR はそのブランチを head にする開いた本番の PR | GitHub の REST（ブランチと PR の照会） |
| `_advance` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 新設 | `_advance(repo_git: git_mod.Git, branch: str, until: str, source: str) -> None` | 既にあるリリースブランチの先端を、組む起点まで先端を戻さずに進めた状態で取り出す | `branch` は origin にある | `base` は origin のブランチの先端が `until` の祖先なら fast-forward、そうでなく開いた本番の PR のある組み直しなら取り込みのマージ。`release` は origin のブランチの先端をそのまま取り出す | git（switch・merge） |
| `_rename` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 新設 | `_rename(hub: GitHub, old: str, new: str) -> None` | 用意したリリースブランチを計画した版の名前へ改名する（決定 5） | `old` を head にする開いた本番の PR が無い | origin に `new` があり `old` が無い | GitHub の REST（`POST /branches/{old}/rename`） |
| `_reassemble` | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | 新設 | `_reassemble(hub: GitHub, number: int, facts: materials_decide.Facts) -> None` | 開いた本番の PR の本文の承認資料を新しい先端で書き直す（決定 7） | `facts.head` は push した新しい先端 | PR の本文が `facts.head` を対象にした承認資料である | GitHub の REST（PR の本文の更新） |
| `production_branch` | `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 新設 | `production_branch(root: Path) -> str` | 宣言 `.ndf/worktree.json` の `production_branch` を読む | 無し | 空でない名前を返す | ファイルの読み込み |
| `cmd_prepare_next` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 新設 | `cmd_prepare_next(args: argparse.Namespace) -> dict` | 副命令 `prepare-next` の引数を読み、`prepare.prepare_next` の結果を結果 JSON にする | 無し | `outputs` に `branch`・`version`・`created` | GitHub の REST（`prepare_next` を介して） |
| `cmd_guard` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 新設 | `cmd_guard(args: argparse.Namespace) -> dict` | ブランチの開いた PR の base を読み、本番の PR なら止まった結果を返す | 無し | 本番の PR なら `stopped`（種類 `refused`）、そうでなければ `ok` | GitHub の REST（PR の照会） |
| `cmd_defer` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 新設 | `cmd_defer(args: argparse.Namespace) -> dict` | 本番の PR を閉じ、リリースブランチを残す（次の版への回し） | 無し | PR が閉じ、head のブランチは消していない。`outputs` に `closed`・`branch` | GitHub の REST（`PATCH /pulls/{n}`） |
| `cmd_answer` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `cmd_answer(args: argparse.Namespace) -> dict` | `--route` で `trail.answer` と `trail.answer_merged` を分ける | `gate` は `--answer`・`--presented`・`--by` がある | `outputs` に `state`。`github-merge` は `by`・`head`・`version` も持つ | GitHub の REST |
| `cmd_merge` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `cmd_merge(args: argparse.Namespace) -> dict` | `Landed` の版・承認・落ちたチェック・次の手を結果 JSON にする | 無し | 止まった結果は `items` に落ちたチェック、`routes` に `rerun`・`reject`・`defer`、`outputs.suggested` に既定の手を持つ | GitHub の REST（`land` を介して） |
| `cmd_assemble` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `cmd_assemble(args: argparse.Namespace) -> dict` | `--source` を `Target` へ渡し、組み直したかを結果に足す | 無し | `outputs.reassembled` が真なら PR を作っていない | git・GitHub の REST（`assemble` を介して） |
| `cmd_materials` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `cmd_materials(args: argparse.Namespace) -> dict` | `--source` を `Target` へ渡す | 無し | 今のまま | git（`collect` を介して） |
| `_production_of` | `plugins/ndf-next/src/ndf/domains/release/cli.py` | 新設 | `_production_of(args: argparse.Namespace) -> str` | `--production` があればそれ、無ければ宣言の本番ブランチを返す | 無し | 空でない名前 | ファイルの読み込み（`declaration.production_branch` を介して） |
| `take_reply` | `plugins/ndf-next/src/ndf/progress/gate/release.py` | 変更 | `take_reply(st: store.Store, table_id: str, env: Mapping[str, str], observe: Observe = no_observe, runner: invoke.Runner = invoke.run_command, cwd: Optional[Path] = None) -> Optional[dict[str, Any]]` | オーガナイザーの答えが無いとき、本番の PR のマージを承認ゲートの答えにする（E6） | 今のまま | オーガナイザーの答えがあれば今のまま。無く、本番の PR がマージ済みなら承認の答えを書いた結果を返す。どちらでもなければ None | 子プロセス（`ndf release-steps answer`）・進行表の書き込み |
| `_merged_reply` | `plugins/ndf-next/src/ndf/progress/gate/release.py` | 新設 | `_merged_reply(q: Question, runner: invoke.Runner, cwd: Path) -> Optional[dict[str, Any]]` | 副命令 `answer --route github-merge` を打ち、マージ済みなら結果 JSON の `outputs` を返す | `q.pr` がある | マージ済みなら `outputs`（`state` が `approved`・`by`・`head`・`version`）。マージされていなければ None | 子プロセス（`ndf release-steps answer`） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `prepare_next` | 出した PR がマージされていない | 本番の PR のマージの前にステップが打たれた | `hub.pull(number).merged` が偽 | 止める（`AssembleError`） | 結果 JSON `stopped`・種類 `precondition`・終了コードは結果の `exit` | 何も作っていない。マージの後に打ち直せる |
| `prepare_next` | head がリリースブランチの形でない | 本番の PR の head が `release/v<版>` でない | `version_decide.version_of_branch` の `VersionError` | 止める | `stopped`・`precondition` | 何も作っていない。打ち直しても同じ |
| `prepare_next` | ブランチを作れない | 権限が無い・GitHub の誤り | `hub.rest` の `GitHubError`（422 は既にあると読む） | 422 で既にあれば `created=False` で続ける。ほかは呼び出し元へ返す | `stopped`・`infra_wait`（再試行の秒があるとき）か `unknown` | 作れていない。打ち直せる（既にあれば作らない） |
| `Lander.land` | 必須のチェックが落ちた | CI の失敗 | `_watch` の止まった理由 | 止める。push もマージもしない | `stopped`・種類は `test_failed` / `check_failed` など。`items` に落ちたチェック、`routes` に 3 つの手 | 変えていない。`rerun` か `reject` か `defer` を選んで打つ |
| `Lander.land` | 先端が承認した先端から動いた | 承認の後にコミットが積まれた | `_refusal` の先端の比べ | 止める | `stopped`・`refused`・理由に 2 つの先端の先頭 12 文字 | 変えていない。打ち直しでマージ済みからの再開に入れる（受け入れ条件 8） |
| `Lander._approval` | PR を読めない | GitHub の誤り | `hub.rest` の `GitHubError` | 呼び出し元へ返す | `stopped`・`infra_wait` か `unknown`。本番はマージ済み | 本番は出ている。打ち直すとマージ済みからの再開で決め直す |
| `Lander._record_merge` | 人のマージの記録を書けない | GitHub の誤り | `trail.write_merge_record` の `TrailError` | 既定値で続ける（`record_error` に理由を置き、戻すマージは済ませる） | `stopped`・`unknown`・理由に「本番は出ている」と書けなかった理由（I5） | 本番は出ている。打ち直すと記録だけを書く（印があれば書かない） |
| `answer_merged` | PR がマージされていない | 人がまだマージしていない | `merge_decide.facts_of(...).merged` が偽 | 呼び出し元へ返す（`NotMerged`） | `stopped`・`not_applicable` | 何も書いていない。待ちの次の読み直しで打ち直す |
| `answer_merged` | 書いた証跡が判定に届かない | 書き手が信用されない・編集された | 書いた後の `read` の判定が `approved` でない | 止める（`TrailError`） | `stopped`・`refused` | 証跡は残る。token を直して打ち直せる（同じ先端の証跡があれば書かない） |
| `answer_merged` | PR の base が本番ブランチでない | 開発版の PR に打った | `pull.base_ref` が宣言の本番ブランチでない | 止める（`TrailError`） | `stopped`・`refused` | 何も書いていない |
| `write_merge_record` | コメントを読めない・書けない | GitHub の誤り・SHA の形が違う | `GitHubError`・`ValueError` | 止める（`TrailError`） | 呼び出し元（`_record_merge`）が扱う | 書けていない。打ち直せる |
| `assemble` | 既にあるブランチを進められない | `base` で、origin のブランチの先端が組む起点の祖先でなく、開いた本番の PR も無い | `_advance` の `is_ancestor` | 止める（`AssembleError`） | `stopped`・`precondition`・理由にブランチの名前と 2 つの先端 | 何も push していない。ブランチを人が片付けてから打ち直す |
| `assemble` | 開いた本番の PR があるのに版の上げ幅が変わった | 計画した版が PR の head の版と違い、同じ前の版から上げたもの | `_existing` | 止める（`AssembleError`。改名を呼ばない） | `stopped`・`precondition`・理由に「`defer` で PR を閉じてから打ち直す」 | 何も変えていない |
| `assemble` | 別の版の開いた本番の PR がある | 前の版の PR が残っている | `open_releases` と `_existing` | 止める（`ReleaseOpen`） | `stopped`・`refused` | 何も変えていない。閉じた後に打ち直す |
| `assemble` | 取り込みのマージが衝突した | 直しとリリースブランチの変更が重なった | `git merge` の失敗（`GitError`） | 止める | `stopped`・`precondition`・衝突したパス | `git merge --abort` で取り出しを戻す。直しを入れてから打ち直す |
| `_rename` | 改名を断られた | 権限・保護・同じ名前が既にある | `GitHubError` | 呼び出し元へ返す | `stopped`・`unknown` | 改名していない。打ち直せる |
| `production_branch` | 宣言が無い・名前が無い | `.ndf/worktree.json` に `production_branch` が無い | ファイルの読み込みと鍵の確かめ | 止める（`DeclarationError`） | `stopped`・`precondition`・宣言の置き場 | 何もしていない。宣言を書くか `--production` を渡して打ち直す |
| `cmd_guard` | ブランチの開いた PR が無い | PR を作る前に `merge` のプランが流れた | `hub.pulls_by_head` が空 | 既定値で続ける（`ok`。後の `conflict-check` が PR の無いことで止まる） | `ok`・理由に「開いた PR が無い」 | 無し |
| `cmd_guard` | 本番の PR だった | 本番の PR を `ndf route merge` で流した | `release_plan.is_production_pr` | 止める | `stopped`・`refused`・終了コード 3・理由に「`release` のプランで流す」 | 何も変えていない。`merge` のプランは原因 `refused` に行き先を持たないので止まる |
| `cmd_defer` | 本番の PR でない・開いていない | 番号の取り違え・既に閉じた | `pull.base_ref` と `pull.state` | 止める | `stopped`・`precondition` | 何も変えていない |
| `Lander._watch` | チェックを読めない | GitHub の誤り | `self._runs` の `GitHubError` | 呼び出し元へ返す（今のまま） | `stopped`・`infra_wait` か `unknown` | 何も変えていない。打ち直せる |
| `Lander._blocker` | 証跡かチェックで止まった | 承認が無い・先端が動いた・チェックが落ちた | `_refusal` と `_watch` | 呼び出し元へ返す（止まった `Landed`） | `land` の行と同じ | 何も変えていない |
| `Lander._resume_merged` | 戻すマージが止まった | 戻す PR のチェックが落ちた | `_sync` の結果 | 呼び出し元へ返す（`approval` は持たせる） | `stopped`・チェックの種類。本番は出ている | 打ち直すとマージ済みからの再開で戻すマージだけをやり直す（記録は印があれば書かない） |
| `answer` | 先端が提示から動いた・答えが届かない | 提示の後にコミットが積まれた・書き手が信用されない | 書く前と書いた後の `read` | 止める（`TrailError`。今のまま） | `stopped`・`refused` | 今のまま |
| `approvals` | コメントを読めない | GitHub の誤り | `hub.comments` の `GitHubError` | 呼び出し元へ返す | 呼び出し元の行の扱い | 何も変えていない |
| `collect` | 取り出しの HEAD が範囲の先端でない | 別のブランチで打った | `rev_parse` の比べ | 止める（`AssembleError`。今のまま） | `stopped`・`precondition` | 何も変えていない |
| `collect` | `release` でリリースブランチが origin に無い | `prepare-next` の前に打った | `gather.fetch` の `GitError` | 止める | `stopped`・`precondition` | 何も変えていない |
| `_existing` | 開いた PR かブランチを読めない | GitHub の誤り・開いた PR が 1 ページを超える | `GitHubError`・`open_releases` の `AssembleError` | 呼び出し元へ返す | `stopped`・`unknown` か `precondition` | 何も変えていない |
| `_advance` | 取り込みのマージが衝突した・祖先でない | 直しと重なった・用意の外のコミットがある | `git merge` の `GitError`・`is_ancestor` | 止める（`--abort` の後に `GitError`、祖先でなければ `AssembleError`） | `stopped`・`precondition` | 取り出しを戻す。打ち直せる |
| `_reassemble` | 本文を書けない | GitHub の誤り | `Publisher.edit_body` の `GitHubError` | 呼び出し元へ返す | `stopped`・`unknown`。先端は push 済みで本文が古い | 本文が古い間は `present` が先端の食い違いで断る（`trail.present`）。打ち直すと本文だけを書き直す |
| `cmd_prepare_next` | 用意が止まった | `prepare_next` の行のどれか | `_ERRORS` と `GitHubError` | 呼び出し元へ返す（結果 JSON） | `prepare_next` の行と同じ | `prepare_next` の行と同じ |
| `cmd_answer` | 引数の組が足りない | `gate` で `--answer`・`--presented`・`--by` のどれかが無い | `cmd_answer` の引数の確かめ | 止める | `stopped`・`invalid` | 何もしていない |
| `cmd_merge` | マージが止まった | `land` の行のどれか | `Landed.kind` | 呼び出し元へ返す（結果 JSON に次の手） | `stopped`・種類・`routes` に 3 つの手 | `land` の行と同じ |
| `cmd_assemble` | 組み立てが止まった | `assemble` の行のどれか | `_ERRORS` | 呼び出し元へ返す | `assemble` の行と同じ | `assemble` の行と同じ |
| `cmd_materials` | 材料を組めない | `collect` の行のどれか | `_ERRORS` | 呼び出し元へ返す | `collect` の行と同じ | 何も変えていない |
| `_production_of` | 本番ブランチが決まらない | `--production` も宣言も無い | `declaration.production_branch` の `DeclarationError` | 止める | `stopped`・`precondition` | 宣言を書くか引数を渡して打ち直す |
| `take_reply` | 承認ゲートの答えを書けない | 問いが待ちでない・見た対象が変わった | `answer` の `model.Refused` | 呼び出し元へ返す | `_took` の警告 | 待ちは残る。次の `ndf progress` で打ち直す（冪等の鍵で重ならない） |
| `_merged_reply` | 副命令が止まった（マージされていない） | 人がまだマージしていない | 結果 JSON の `status` が `stopped` で種類が `not_applicable` | 既定値で続ける（None を返し、待ちを残す） | 出さない | 無し |
| `_merged_reply` | 副命令が止まった（ほかの理由） | GitHub の誤り・証跡が届かない | 結果 JSON の `status` が `stopped` でほかの種類 | 呼び出し元へ返す（`model.Refused`） | `_took` が警告「リリースの依頼への答えを承認ゲートの答えにできなかった: …」 | 待ちは残る。次の `ndf progress` で打ち直す |
| `_merged_reply` | 結果 JSON を読めない | 子プロセスが起動できない・出力が JSON でない | `json.loads` の誤り・`OSError` | 呼び出し元へ返す（`model.Refused`） | 同上 | 待ちは残る |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `merge_decide.decide` | マージされていない | None |
| `merge_decide.decide` | マージ済み・マージした先端への最新の承認の証跡の経路が `gate` | `route=gate`・`by` はその証跡の書き手・`gate_head` はマージした先端 |
| `merge_decide.decide` | マージ済み・マージした先端への最新の承認の証跡の経路が `github-merge` | `route=github-merge`・`by` はその証跡の「承認した人」・`gate_head` は経路 `gate` の最新の承認の先端（無ければ None） |
| `merge_decide.decide` | マージ済み・マージした先端への承認の証跡が無い（受け入れ条件 8） | `route=github-merge`・`by` は `facts.by`・`gate_head` は経路 `gate` の最新の承認の先端（A）。B への承認として扱い、A の承認を B へ引き継がない |
| `release_plan.forbidden` | `release` が偽 | 空 |
| `release_plan.forbidden` | `release` が真・最初のステップの `run` が `MERGE_RUN` で始まる・worker も `FIX_STEPS` も無い | 空 |
| `release_plan.forbidden` | `release` が真・どれかを破る | 破ったステップごとに 1 行 |
| `merge_decide.suggested` | 種類が `infra_wait`・`ci_pending` | `rerun` |
| `merge_decide.suggested` | 種類が `test_failed`・`check_failed`・`refused`・ほか | `reject` |
| `assemble` | 計画した版のブランチが無い・用意したブランチも無い | 今のまま作る（ベースブランチか `release` の先端から） |
| `assemble` | 用意したブランチ（同じ前の版の PATCH）があり、計画した版と違い、開いた本番の PR が無い | `_rename` で計画した版の名前へ改名してから進める |
| `assemble` | 計画した版のブランチがあり、開いた本番の PR が無い | `_advance` で進め、版と CHANGELOG のコミットを足して PR を作る |
| `assemble` | 計画した版のブランチがあり、それを head にする開いた本番の PR がある | `_advance` で進め、版と CHANGELOG のコミットを足し、`_reassemble` で本文を書き直す（PR を作らない） |
| `assemble` | 開いた本番の PR の head の版が計画した版と違い、同じ前の版から上げたもの | `precondition` で止まる（改名しない） |
| `assemble` | 開いた本番の PR の head がそれ以外の名前 | `ReleaseOpen` で止まる |
| `_advance` | `source=base`・origin のブランチの先端が `until` の祖先 | `until` を取り出す（fast-forward と同じ中身。push は先端を進めるだけ） |
| `_advance` | `source=base`・祖先でない・開いた本番の PR がある | origin のブランチの先端を取り出し、`until` を取り込みのマージで入れる |
| `_advance` | `source=base`・祖先でない・開いた本番の PR が無い | `AssembleError` |
| `_advance` | `source=release` | origin のブランチの先端を取り出す（範囲の先端もこのブランチ） |
| `take_reply` | オーガナイザーの答えがある | 今のまま |
| `take_reply` | 答えが無い・問いが PR を持たない | None |
| `take_reply` | 答えが無い・PR を持つ・副命令が `ok` で `state=approved` | 承認の答えを書く（書き手 `MERGE_ACTOR`、冪等の鍵 `github-merge:<問いの id>`、見た対象は `observe(q)`） |

- 繰り返しの終わる条件: 新しい関数に繰り返しの待ちは無い。人のマージの検知は、承認ゲートの待ちを読み直す `ndf progress` の 1 回ごとに 1 回だけ打つ
- 計算量の注意: `_merged_reply` は、リリース承認ゲートが PR を持って待つ間、`ndf progress` の 1 回ごとに子プロセス 1 つと GitHub の照会 1 回（PR の照会。マージ済みのときだけコメントの一覧と作成）を足す。ほかの承認ゲートでは打たない

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `MergeFacts` | 型（`merged: bool`・`by: str`・`head: str`・`merge_sha: str`・`base_ref: str`・`head_ref: str`） | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | — |
| `Approved` | 型（`order: int`・`author: str`・`sha: str`・`route: str`・`by: str`） | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | — |
| `Approval` | 型（`route`・`by`・`approved_head`・`merged_head`・`merge_sha`・`gate_head`。`as_json()` で結果 JSON の `outputs.approval` の形） | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | — |
| `GATE_ROUTE`・`MERGE_ROUTE`・`ROUTES` | 定数 | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | `"gate"`・`"github-merge"`・その 2 つ。`cli.py` の `--route` の選択肢はここを読む |
| `RERUN_KINDS` | 定数 | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | `frozenset({"infra_wait", "ci_pending"})` |
| `NEXT_MOVES` | 定数 | `plugins/ndf-next/src/ndf/domains/release/merge_decide.py` | `("rerun", "reject", "defer")` |
| `ROUTE_LINE`・`APPROVER_LINE` | 定数 | `plugins/ndf-next/src/ndf/domains/release/trail.py` | `"承認の経路:"`・`"承認した人:"`（証跡と人のマージの記録が共有する） |
| `MERGE_MARK` | 定数 | `plugins/ndf-next/src/ndf/domains/release/trail.py` | `<!-- ndf:merge-approval -->`（名前空間は `identity.FIXED_WORDS['workflow_namespace']` から組む） |
| `MERGED_HEAD_LINE`・`MERGE_SHA_LINE`・`GATE_HEAD_LINE` | 定数 | `plugins/ndf-next/src/ndf/domains/release/trail.py` | `ADMIN_SHA_LINE`（`"マージした先端:"`）を使い直す・`"マージコミット:"`・`"承認ゲートの承認の先端:"` |
| `FIX_STEPS` | 定数 | `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | `frozenset({"push", "fix-push", "ci-fix", "fix-base", "base-sync"})` |
| `MERGE_RUN` | 定数 | `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | `("ndf", "release-steps", "merge")` |
| `GUARD_ID` | 定数 | `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | `"release-guard"` |
| `GUARD_EXIT` | 定数 | `plugins/ndf-next/src/ndf/domains/release/release_plan.py` | `3`（`merge.json` の `exit_cause` の鍵と同じ値。雛形のテストが一致を確かめる） |
| `Prepared` | 型（`branch: str`・`version: str`・`created: bool`） | `plugins/ndf-next/src/ndf/domains/release/prepare.py` | — |
| `Target.source` | 型の欄 | `plugins/ndf-next/src/ndf/domains/release/assemble.py` | `"base"`。選択肢 `SOURCES = ("base", "release")` は同じファイルが持つ |
| `Landed.version`・`approval`・`failed`・`merge_record` | 型の欄 | `plugins/ndf-next/src/ndf/domains/release/land.py` | `None`・`None`・`()`・`None` |
| `MERGE_ACTOR` | 定数 | `plugins/ndf-next/src/ndf/progress/gate/release.py` | `"service:github-merge"`（承認ゲートの答えた者の形 `ACTORS` に合う。マージした人のログインは答えの理由に書く） |
| 引数 `pr` | 設定値 | `plugins/ndf-next/contracts/templates/params.json` | 既定なし（工程「デプロイ」の入力 `pr`） |
| 宣言 `production_branch` | 設定値 | `.ndf/worktree.json`（使う側のリポジトリ） | 無ければ `--production` が要る |

`release.json` のステップの形は次のとおりである（雛形の差し込みは `params.json` の名前だけ）。

```json
{"params": ["repo", "pr", "base", "ci_budget"],
 "steps": [
   {"id": "merge", "run": ["ndf", "release-steps", "merge", "--root", "{{repo}}", "--pr", "{{pr}}", "--base", "{{base}}", "--budget", "{{ci_budget}}"]},
   {"id": "record", "run": ["ndf", "release-steps", "record", "--root", "{{repo}}", "--pr", "{{pr}}"]},
   {"id": "prepare-next", "run": ["ndf", "release-steps", "prepare-next", "--root", "{{repo}}", "--pr", "{{pr}}"], "next": "end"}
 ]}
```

`merge.json` の最初に足すステップは次のとおりである。

```json
{"id": "release-guard", "run": ["ndf", "release-steps", "guard", "--root", "{{repo}}", "--branch", "{{branch}}"], "exit_cause": {"3": "refused"}}
```

`refused` は止まった理由の表（`contracts/causes.json`）の既存の種類で、表を変えない。

工程「デプロイ」の出力 `delivery` は、本番のプランのどのステップも返さない。工程の出力は定義の鍵のうち返されたものだけを書く
（`ndf.progress.stages.outputs_draft` の `keep_declared`）ため、欠けても誤りにならない。`delivery` を読む側を次のコマンドで探した結果は、`deploy.json` と `release.json` の 2 件だけである。

```bash
grep -rln delivery contracts/workflow/stages/ contracts/templates/ src/ndf/progress
```

工程の定義と雛形の外に読み手は無い。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 本番のプランの `merge` と、人の GitHub でのマージ | 本番の PR と本番ブランチ | GitHub のマージの照合（`expect_head`。STD1）。先にマージされた側が勝ち、後の `merge` は 405 か先端の食い違いで断られる | 断られた `merge` を打ち直すと、マージ済みからの再開（`_resume_merged`）に入る |
| 承認ゲートの待ち（`_merged_reply`）と、本番のプランの `merge` の再開 | 本番の PR のコメント（承認の証跡・人のマージの記録） | 排他を取らない。書く前に同じ先端の印のあるコメントを探し、あれば書かない | 証跡と記録はどちらも同じマージした先端について 1 件に収まる。同時に書けば 2 件になりうるが、判定は最新の 1 件で決まり、内容は同じ |
| `prepare-next` の打ち直しと、別のレーンの `prepare-next` | origin のリリースブランチの名前 | GitHub の参照の作成（`POST /git/refs`）が同じ名前を 422 で断る | 422 を「既にある」と読み、`created=False` で返す |
| `assemble` と、リリースブランチへのスプリントのマージ | origin のリリースブランチ | push は先端を進めるだけで、先端が動いていれば non-fast-forward で git が断る | 断られたら `precondition` で止まる。打ち直すと新しい先端から組み直す |
| `assemble` の打ち直し | 開いた本番の PR | 開いた本番の PR があれば組み直しに入り、新しい PR を作らない | 版と CHANGELOG のコミットが既に今の中身と同じなら、コミットを足さずに本文だけを書き直す |
| `defer` と `assemble` | 本番の PR | 排他を取らない。`defer` は PR を閉じるだけで、`assemble` は閉じた PR を見ない | `defer` の打ち直しは閉じた PR に `precondition` で止まり、何も変えない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_merge` | 版・承認（経路・承認した人のログイン・3 つの SHA）・落ちたチェックの名前と結論・次の手・既定の手 | 結果 JSON の `outputs`・`items`・`routes` | token |
| `Lander._record_merge` | 人のマージの記録（5 行） | 本番の PR のコメント | token・メールアドレス |
| `answer_merged` | 承認の証跡（答え・承認したコミット・担い手 `user`・承認の経路・承認した人） | 本番の PR のコメント | token |
| `cmd_prepare_next` | 作ったか既にあったブランチの名前・版・作ったか | 結果 JSON の `outputs` | 無し |
| `cmd_guard` | 止まった理由（PR の番号・base・「`release` のプランで流す」） | 結果 JSON の `summary`（標準出力） | 無し |
| `cmd_defer` | 閉じた PR の番号と残したブランチ | 結果 JSON の `outputs` | 無し |
| `cmd_assemble` | 組み直したか・改名したか（前の名前） | 結果 JSON の `outputs.reassembled`・`outputs.renamed_from` | 無し |
| `take_reply` | 人のマージを承認とした答え（理由に PR の番号・マージした人のログイン・先端の先頭 12 文字） | 進行表の答えの事象 | token |
| `_merged_reply` | 副命令が止まった理由 | `model.Refused` を介して `_took` の警告 | 子プロセスの標準エラーの全文（先頭の 1 行だけを理由に使う） |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `forbidden` | `ndf.core.plan.model.errors`（プランの形の誤り） | `src/ndf/core/plan/model.py` を読んだ（`errors` は id の重なり・行き先・止まった理由の種類・答えの語だけを見る） | 使えない。リリースの語（worker・直しのステップ）を核へ持ち込まない（決定 2） |
| `forbidden` | `ndf.core.route.expanded`（`use` を開いた形） | `grep -n "def expanded" src/ndf/core/route/__init__.py` | 使う。テストが雛形をこの関数で開いてから渡す |
| `guard_first` | `ndf.core.plan.model._kind_errors` | `grep -n "_kind_errors" -A4 src/ndf/core/plan/model.py` | 使えない（種類が表にあるかだけを見る）。止まる原因が表にあることはこの関数で確かめられるため、`refused` を使う根拠にする |
| `is_production_pr` | `ndf.domains.release.land.Lander._bypass` の本番の判定（`pull.base_ref != self.production`） | `land.py` を読んだ | 使えない（`Lander` のメソッドで、宣言の迂回の判定と一体）。比べ 1 つを純粋な関数に切り出し、`land` も使い直す |
| `facts_of` | `ndf.adapters.github.Pull.from_rest` | `grep -n "class Pull" -A30 src/ndf/adapters/github.py` | 使えない。`Pull` は `merged_by` を持たず、足すとリリースの外のアダプタ（`adapters/github.py`）を変える。`hub.rest("GET", ".../pulls/<番号>")` の生の応答から読む |
| `decide` | `ndf.domains.release.approval_decide.judge` | `approval_decide.py` を読んだ | 使えない（今の先端への最新の答えだけを返し、経路と別の先端の承認を返さない）。証跡の読み取り（`parse`・`trails`）は `trail.approvals` が使う |
| `suggested` | `ndf.contracts.causes` の `route` の列 | `grep -n '"kind"' contracts/causes.json` | 使えない（行き先は人・conductor・ステップの区分で、`reject` と `defer` を分けない）。2 値の対応だけを持つ |
| `next_branch` | `version_decide.version_of_branch`・`production_next`・`branch_of` | `grep -n "^def " src/ndf/domains/release/version_decide.py` | 使う（3 つを組み合わせる） |
| `prepare_next` | `ndf.domains.release.tagging` の参照の作成（`POST /git/refs`） | `grep -rn "git/refs" src/ndf` | 使う形に倣う（タグの関数はタグの名前を組むため、呼ばずに同じ REST を打つ） |
| `Lander._approval` | `trail.user_judgment_at` | `trail.py` を読んだ | 使えない（利用者の証跡の判定だけで、経路を返さない） |
| `Lander._record_merge` | `Lander._record`（迂回の記録） | `land.py` を読んだ | 使えない（迂回の記録を書く）。結果の欄 `record_error` は同じものを使い直す |
| `answer_merged` | `trail.answer` | `trail.py` を読んだ | 使う。先端は PR から読んだマージした先端を `presented` に渡し、行を足す点だけが違う |
| `approvals` | `trail.judged` と `approval_decide.trails` | `trail.py` を読んだ | 使う（信用の判定 `_counts` と証跡の読み取りを同じに保ち、経路の行だけを足して読む） |
| `render_merge_record` | `trail.render_admin_record` | `trail.py` を読んだ | 使えない（迂回の記録の行を持つ）。SHA の形の確かめ `_SHA` と行の見出し `ADMIN_SHA_LINE` を使い直す |
| `write_merge_record` | `trail.write_admin_record` | `trail.py` を読んだ | 使えない（照らした証跡の URL を書く）。既にある記録の探し方（印・SHA・編集なし・信用）を同じにする |
| `_existing` | `assemble.open_releases` | `assemble.py` を読んだ | 使う（開いた本番の PR の番号を得て、head の名前を読み直す） |
| `_advance` | `ndf.adapters.git.Git.is_ancestor` | `grep -n "def is_ancestor" src/ndf/adapters/git.py` | 使う |
| `_rename` | GitHub のアダプタの改名の関数 | `grep -rn "/rename" src/ndf` の結果が 0 件 | 無い。`hub.rest("POST", ".../branches/<名前>/rename")`（STD4）を打つ |
| `_reassemble` | `ndf.adapters.publish.Publisher.edit_body` | `assemble._open_pr` を読んだ | 使う |
| `production_branch` | `ndf.domains.worktree.declaration.Declaration.production_branch` | `grep -rn "from ndf.domains.worktree" src/ndf/domains --include=*.py` の結果が worktree の領域の外に 0 件 | 使えない（領域をまたぐ import の前例が無い）。`ndf.core.route.default_base` と同じ読み方（宣言の JSON の 1 鍵）で読む |
| `cmd_prepare_next` | `cli.ARGS`・`HANDLERS` の表と `cmd_record` の結果の組み方 | `cli.py` を読んだ | 使う（表へ行を足すだけで、名前は関数の名前から作られる） |
| `cmd_guard` | `cli.cmd_approval_check`（本番ブランチ宛てでない PR を `not_applicable` で返す判定） | `cli.py` を読んだ | 使えない（PR の番号で読み、ブランチから読まない）。ブランチの開いた PR は `GitHub.pulls_by_head` で読む |
| `cmd_defer` | `ndf.domains.backlog.issues_gh.close_with_comment`（課題を閉じる） | `grep -rn '"state": "closed"\|def close' src/ndf` | 使えない（課題を閉じる関数で、別の領域にある）。`PATCH /pulls/<番号>` を `hub.rest` で打つ |
| `_production_of` | `cli._branches`（`--production` 必須） | `cli.py` を読んだ | 使う（`--production` を必須から省けるに変え、省いたときだけ宣言を読む） |
| `_merged_reply` | `ndf.progress.invoke.ndf_command`・`ndf_env`・`run_command` | `grep -n "def ndf_command\|Runner\|def run_command" src/ndf/progress/invoke.py` | 使う（子プロセスの argv・環境変数・テストの差し替え口） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `forbidden` | `contracts/templates/` の雛形をすべて `expanded` で開き、`release: true` のものが空を返す | 最初のステップの前に別のステップを 1 つ足すと 1 行返す | `release.json` に `{{worker}}` のステップか `push` を戻すと行を返す。`merge.json` に `release: true` を付けた形は `ci-fix`・`push`・`fix-push` を返す |
| `guard_first` | `merge.json` を開いた形が空を返す | `exit_cause` の値を別の種類にすると行を返す | 最初のステップを外すか、`on_cause` に `refused` → `ci-fix` を足すと行を返す。`GUARD_EXIT` と雛形の鍵が違うと行を返す |
| `is_production_pr` | base が本番ブランチで真 | 名前の前後に空白や大文字の違いがあれば偽（そのまま比べる） | ベースブランチ宛てで偽 |
| `facts_of` | マージ済みの応答から 4 つの値を読む | `merged_by` が null で `by` が空 | `merged` が偽で `merged=False` |
| `decide` | 経路 `gate` の証跡のマージした先端で `route=gate` | 承認の証跡が 1 件も無いマージで `route=github-merge`・`gate_head=None` | A への `gate` の証跡と B のマージで `approved_head=B`・`gate_head=A`。`gate_head` を B にすると落ちる |
| `suggested` | `test_failed` で `reject` | `ci_pending` と `infra_wait` で `rerun` | 表に無い種類で `reject` |
| `next_branch` | `release/v10.27.68` から `("10.27.69", "release/v10.27.69")` | 開発版の接尾辞を持つ名前から接尾辞の無い次の版 | リリースブランチでない名前で `VersionError` |
| `prepare_next` | マージ済みの PR から、`merge_sha` を起点に次の版のブランチを作る | 2 回打って 2 回目が `created=False` で作らない | マージされていない PR で `precondition`。参照の作成が 422 以外で落ちると誤りを返す |
| `Lander.land` | 通った本番の PR の結果が `version` と `approval`（`route=gate`）を持つ | マージキューで待った後のマージも `approval` を持つ | 落ちたチェックで `failed` に名前、push もマージも呼ばない（偽の GitHub の呼び出しの記録で見る）。先端が動くと `refused` で 2 つの先端 |
| `Lander._watch` | 通れば `(None, ())` | 落ちたテストとテスト以外が両方あれば両方を返す | 待ちの切れで `ci_pending` と空の落ちたチェック |
| `Lander._blocker` | 止まらなければ None | 待ちの後の読み直しで止まると `failed` は空 | チェックで止まると `failed` を持つ |
| `Lander._approval` | マージ済みで `decide` の結果 | マージされていなければ None | PR を読めなければ `GitHubError` が呼び出し元へ届く |
| `Lander._record_merge` | 経路 `github-merge` で記録を書き URL を持つ | 経路 `gate` で書かない | 書けなければ `record_error` を持ち、`cmd_merge` が「本番は出ている」と書いて `stopped` |
| `Lander._resume_merged` | 人がマージした PR の再開で、記録を書き戻すマージをする | 記録が既にあれば書かない（打ち直し） | 受け入れ条件 8 の形（`refused` の後に B がマージ済み）で `approved_head=B`・`gate_head=A` |
| `answer` | 書いた証跡が `承認の経路: gate` を持ち、判定に届く | 経路の行の無い古い証跡も `approvals` が `gate` と読む | 先端が動いていれば今のまま断る |
| `answer_merged` | マージ済みの PR に経路 `github-merge`・承認した人がマージした人の証跡を書く | 同じ先端の証跡があれば書かない | マージされていない PR で `NotMerged`。本番ブランチ宛てでなければ `TrailError`。届かなければ `TrailError` |
| `approvals` | 信用する承認の証跡を古い順に返す | 差し戻しは含めない | 信用しない書き手・編集された証跡を含めない |
| `render_merge_record` | 5 行と印を持ち、証跡の印を持たない | `gate_head` が None のとき行の値が「（無し）」 | SHA の形が違えば `ValueError` |
| `write_merge_record` | 記録を書いて `(True, URL)` | 同じ先端の記録があれば `(False, URL)` | 信用しない書き手か編集された同じ印のコメントは記録に数えずに書く。GitHub の誤りで `TrailError` |
| `assemble` | 用意したブランチ（コミットを持たない）を `base` で fast-forward で進め、PR を作る | 開いた本番の PR のあるブランチへ直しを足して打つと、PR を作らずに本文を新しい先端で書き直し、A への判定が `stale`（`--source` の 2 値の両方） | 祖先でない既存のブランチで `precondition`。別の名前の開いた本番の PR で `ReleaseOpen`。強制 push の引数を渡すと落ちる（偽の git の呼び出しの記録で見る） |
| `collect` | `base` で今の資料 | `release` で範囲の先端がリリースブランチ | HEAD が範囲の先端でなければ今のまま `AssembleError` |
| `_existing` | 計画した版のブランチと開いた PR の番号を返す | 用意したブランチの版が計画した版と違えばその名前を返す | 両方が無ければ `(None, None)` |
| `_advance` | 祖先なら `until` を取り出す | `release` で origin の先端を取り出す | 取り込みのマージが衝突すれば `--abort` してから `GitError` |
| `_rename` | 改名の REST を 1 回呼ぶ | 開いた本番の PR があれば呼ばない（`assemble` 側で止まる） | 断られれば `GitHubError` |
| `_reassemble` | 本文が新しい先端を対象にする | 中身が同じでも本文を書き直す | GitHub の誤りが呼び出し元へ届く |
| `production_branch` | 宣言の名前を返す | 値の前後の空白は誤りとして扱う | 宣言が無い・鍵が無い・文字列でないとき `DeclarationError` |
| `cmd_prepare_next` | `outputs` に 3 つの鍵 | `created=False` の結果も `ok` | 止まった例外が種類つきの `stopped` になる |
| `cmd_guard` | ベースブランチ宛ての PR で終了コード 0 | 開いた PR が無いブランチで 0 | 本番の PR で `refused`・終了コード 3 |
| `cmd_defer` | PR を閉じ、ブランチを消す REST を呼ばない | — | 閉じた PR・本番宛てでない PR で `precondition` |
| `cmd_answer` | `--route gate` で今の振る舞い | `--route` を省くと `gate` | `gate` で `--presented` が無ければ引数の誤り。`github-merge` でマージされていなければ `not_applicable` |
| `cmd_merge` | 止まった結果の `routes` に 3 つの手、`outputs.suggested` に既定の手 | 通った結果は `routes` を持たない | `routes` から `defer` が欠けると落ちる |
| `cmd_assemble` | `--source` を省くと `base` | 組み直しで `outputs.reassembled` が真 | 選択肢に無い値で引数の誤り |
| `cmd_materials` | `--source release` が `Target` へ届く | — | 選択肢に無い値で引数の誤り |
| `_production_of` | `--production` があればそれ | 省けば宣言の値 | 両方が無ければ `DeclarationError` |
| `take_reply` | 答え待ちのリリース承認ゲートで、副命令の `ok` を受けて承認の答えを書く（書き手 `MERGE_ACTOR`、出力に版と PR） | 同じ問いで 2 回呼んでも答えは 1 件（冪等の鍵） | オーガナイザーの答えがあれば副命令を打たない。PR を持たない問いで打たない |
| `_merged_reply` | `ok` の `outputs` を返す | `not_applicable` で None | ほかの止まった結果・JSON でない出力・起動できないときに `model.Refused` |
