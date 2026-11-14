# #3506 の詳細設計: 本番の PR を宣言に従って管理者の迂回でマージする

設計文書 [issues/issue-3506-design.md](issue-3506-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。不変条件（I1〜I9）と決定（決定 1〜8）は設計文書の番号で指し、リリースの場合の値（`cd-no-review` / `cd-review` / `cd-admin` / `no-cd`）は名前で指す。決定の本文は [issue-3506-design-decisions.md](issue-3506-design-decisions.md) にある。

この文書では、旧いプラグインを `plugins/ndf/scripts/` からの相対パスで、作り直しを `plugins/ndf-next/src/ndf/` からの相対パスで書く。表で関数を指すときは「モジュール名.関数名」か「クラス名.メソッド名」で書く。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
.ndf/project.json                                   宣言（production_merge.admin: true）
plugins/ndf/
├── contracts/settings/project.json.json            キー production_merge の置き場（main）
├── skills/development-workflow/schemas/project.schema.json   型から作り直す
├── skills/release/SKILL.md・references/release-steps.md
├── skills/development-workflow/references/production-merge.md
├── skills/merged/SKILL.md
└── scripts/
    ├── project_lib/model.py                        ProductionMerge・ProjectDecl.production_merge
    ├── lib/project_decl.py                         production_admin
    ├── lib/release_case.py                         cd-admin と場合の属性
    ├── lib/README.md
    ├── release_lib/approval_trail.py               担い手の読み取り・迂回の記録の書き込み
    ├── release_lib/approval.py                     担い手で絞る照合
    ├── release_lib/reviewed.py                     本番のマージの頼み手・記録の結果の行
    ├── release_lib/promotion.py                    選んで集める取り込み方の結果の行と再開
    ├── merged_lib/merge.py                         _merge_pr・merge_when_green・promote
    ├── supervise_lib/release_cd.py                 本番のプランの部品
    └── tests/（test_release_case.py・test_release_steps.py・test_release_cd.py・test_sprint_close_merge_green.py）
plugins/ndf-next/
├── src/ndf/domains/release/declaration.py          production_admin
├── src/ndf/domains/release/land.py                 Lander の迂回と記録
├── src/ndf/domains/release/trail.py                迂回の記録
├── src/ndf/domains/release/cli.py                  cmd_merge の出力
├── src/ndf/adapters/github.py                      merge_as_admin・review_decision
└── tests/domains/release/test_release_approval.py
docs/specifications/ndf-release-approval-on-pr.md
docs/glossary/glossary.json・docs/glossary.md
```

依存の向きは今のまま一方向である。

- 旧いプラグインでは `lib/release_case.py` が `lib/project_decl.py` を読む。`release_lib/`・`merged_lib/`・`supervise_lib/` は `lib/release_case.py` を読む
- `merged_lib/merge.py` は既に `release_lib/reviewed.py` と `release_lib/approval_trail.py` を読んでいる。`release_lib/` から `merged_lib/` への読み込みは足さない（`release_lib/reviewed.py` は `merge-when-green` を子のプロセスで呼ぶ）
- 作り直しでは `domains/release/land.py` が `adapters/github.py` と `domains/release/trail.py` を読む。`domains/release/cli.py` は `declaration.py` と `land.py` を読む。作り直しは旧いプラグインを読み込まない

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/project_lib/model.py` | 変更 | 宣言の型に `ProductionMerge` と `ProjectDecl.production_merge` を足す | `schema`（pydantic の既存の基底） |
| `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 変更 | `project-decl.py schema` の出力で作り直す生成物 | `project_lib/model.py` |
| `plugins/ndf/contracts/settings/project.json.json` | 変更 | キー `production_merge` を `place: main` で登録する | 無し |
| `plugins/ndf/scripts/lib/project_decl.py` | 変更 | 迂回の宣言を I1 の規則で読む唯一の関数を足す | `lib/decl.py` |
| `plugins/ndf/scripts/lib/release_case.py` | 変更 | 4 つ目の場合と場合の属性を持つ | `lib/delivery.py`・`lib/project_decl.py`・`gh_call` |
| `plugins/ndf/scripts/lib/README.md` | 変更 | `release_case.py` の説明の場合の表に `cd-admin` を足す | 無し |
| `plugins/ndf/scripts/release_lib/approval_trail.py` | 変更 | 証跡の担い手と URL を読み、迂回の記録を書く | `gh_rest`・`approved_commit` |
| `plugins/ndf/scripts/release_lib/approval.py` | 変更 | `cd-admin` では担い手が利用者の証跡だけで照らす | `release_lib/approval_trail.py`・`lib/release_case.py` |
| `plugins/ndf/scripts/release_lib/reviewed.py` | 変更 | 場合の属性で証跡の待ちと迂回を決め、迂回の記録の結果の行と報告を組む | `release_lib/approval_trail.py`・`release_lib/approval.py`・`lib/release_case.py` |
| `plugins/ndf/scripts/release_lib/promotion.py` | 変更 | 選んで集める取り込み方の結果の行に迂回の有無を書き、再開で記録を書かせる | `release_lib/reviewed.py` |
| `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | 迂回の有無を結果の行に書き、断られた後の先端を読み直し、`promote` の迂回と記録を持つ | `release_lib/reviewed.py`・`release_lib/approval_trail.py`・`lib/release_case.py` |
| `plugins/ndf/scripts/supervise_lib/release_cd.py` | 変更 | `cd-admin` の MVV 判定のプランを承認ゲートで止め、証跡を利用者の担い手で書く | `lib/release_case.py` |
| `plugins/ndf/scripts/tests/test_release_case.py` | 変更 | 場合の判定と属性を縛る | `lib/release_case.py` |
| `plugins/ndf/scripts/tests/test_release_steps.py` | 変更 | `release --channel prod` と選んで集める取り込み方の迂回・記録・止まり方を縛る | `release-steps.py` |
| `plugins/ndf/scripts/tests/test_release_cd.py` | 変更 | 本番のプランの部品の分岐を縛る | `supervise_lib/release_cd.py` |
| `plugins/ndf/scripts/tests/test_sprint_close_merge_green.py` | 変更 | `merge-when-green` と `promote` の迂回・断られた後の読み直し・記録を縛る | `merged_lib/merge.py` |
| `plugins/ndf-next/src/ndf/domains/release/declaration.py` | 変更 | 作り直しで迂回の宣言を I1 の規則で読む | 無し（標準ライブラリ） |
| `plugins/ndf-next/src/ndf/adapters/github.py` | 変更 | 迂回のマージと、PR のレビューの判定の読み取りを持つ | githubkit（既存） |
| `plugins/ndf-next/src/ndf/domains/release/trail.py` | 変更 | 迂回の記録を書く | `adapters/github.py`・`adapters/publish.py`・`approval_decide.py` |
| `plugins/ndf-next/src/ndf/domains/release/land.py` | 変更 | 宣言と承認数で迂回するかを決め、迂回のマージと記録を進める | `adapters/github.py`・`domains/release/trail.py` |
| `plugins/ndf-next/src/ndf/domains/release/cli.py` | 変更 | `release-steps merge` の出力に `admin` と `admin_record` を足す | `declaration.py`・`land.py` |
| `plugins/ndf-next/tests/domains/release/test_release_approval.py` | 変更 | 作り直しの迂回・止まり方・迂回しない場合を縛る | `land.py`・`tests/domains/release/_fakes.py` |
| `.ndf/project.json` | 変更 | ai-plugins の迂回の宣言を持つ（C7。リリース承認ゲートで人が見る） | 無し |
| `docs/specifications/ndf-release-approval-on-pr.md` | 変更 | 場合の表と I7 を宣言の有無で分けて書く | 無し |
| `plugins/ndf/skills/release/SKILL.md` | 変更 | 場合の表に `cd-admin` を足す | 無し |
| `plugins/ndf/skills/release/references/release-steps.md` | 変更 | 場合の表と結果の行の `admin` を足す | 無し |
| `plugins/ndf/skills/development-workflow/references/production-merge.md` | 変更 | 場合の表に `cd-admin` を足す | 無し |
| `plugins/ndf/skills/merged/SKILL.md` | 変更 | `promote` の場合の表に `cd-admin` を足す | 無し |
| `docs/glossary/glossary.json` | 変更 | リリースの場合・迂回の宣言・迂回の記録の意味を持つ | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` の出力で作り直す生成物 | `docs/glossary/glossary.json` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `project_lib/model.py` | `ProductionMerge`（新設）・`ProjectDecl` | `ProductionMerge.admin`（新設）・`ProjectDecl.production_merge`（新設） |
| `lib/project_decl.py` | モジュール | `production_admin`（新設） |
| `lib/release_case.py` | `Case` とモジュール | `Case.trail_approval`・`Case.admin_merge`・`Case.human_approval`（新設）・`Case.has_cd`（説明だけ変更）・`decide_case`・`case_of`（変更） |
| `release_lib/approval_trail.py` | `Trail`・`Judgment` とモジュール | `Trail.by`・`Trail.url`・`Judgment.url`（新設）・`parse_by`・`render_admin_record`・`write_admin_record`（新設）・`trails_of`・`judge`・`judge_trails_on`（変更） |
| `release_lib/approval.py` | モジュール | `approved_on_pr`（変更）・`_approved_from_pr`（案内の分岐だけ変更） |
| `release_lib/reviewed.py` | `ProdOps` とモジュール | `admin_record_item`・`admin_record_on_resume`・`admin_back`（新設）・`release_prod`・`_open_prod_pr`・`merge_on_pr_approval`（変更）・`ProdOps.published`（型の注記だけ変更） |
| `release_lib/promotion.py` | モジュール | `release_selected`（変更） |
| `merged_lib/merge.py` | モジュール | `_moved_after_refusal`・`_admin_after`（新設）・`_merge_pr`・`merge_when_green`・`promote`・`_trail_before_merge`（変更） |
| `supervise_lib/release_cd.py` | モジュール | `cd_prod_steps`（変更）・`cd_release_wait`（条件を属性で書き直すだけ。振る舞いは変えない）・`release_cd_case`（説明だけ変更）・`RULE_RELEASE_PROD_CD`（文言を変更） |
| `domains/release/declaration.py`（作り直し） | モジュール | `production_admin`（新設） |
| `adapters/github.py`（作り直し） | `GitHub`・`Authored` とモジュール | `GitHub.merge_as_admin`・`GitHub.review_decision`（新設）・`_refused_by_graphql`（新設）・`Authored.url`（新設。既定は空） |
| `domains/release/trail.py`（作り直し） | モジュール | `render_admin_record`・`write_admin_record`（新設） |
| `domains/release/land.py`（作り直し） | `Lander`・`Landed` | `Lander.admin`・`Landed.admin`・`Landed.admin_record`・`Landed.record_error`（新設）・`Lander.approvals`・`Lander._bypass`・`Lander._record`（新設）・`Lander._merge`・`Lander.land`（変更） |
| `domains/release/cli.py`（作り直し） | モジュール | `cmd_merge`（変更） |

## 関数の仕様

旧いプラグインと作り直しで同じ名前の関数は、モジュール名を付けて区別する。`approval_trail.write_admin_record` は旧いプラグイン、`trail.write_admin_record` は作り直しの関数である。

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `project_decl.production_admin` | `lib/project_decl.py` | 新設 | `production_admin(root) -> bool` | 迂回の宣言があるかを I1 の規則で返す | `root` はリポジトリの木 | 値が JSON の `true` のときだけ真。キーが無い・`Unknown`・型の違い・読めないときは偽 | `.ndf/project.json` を読む（キーの置き場は契約の `main`） |
| `Case.trail_approval` | `lib/release_case.py` | 新設 | `trail_approval -> bool`（プロパティ） | 承認を証跡で取る場合かを返す | 無し | `cd-no-review`・`cd-admin` で真 | 無し |
| `Case.admin_merge` | `lib/release_case.py` | 新設 | `admin_merge -> bool`（プロパティ） | 迂回でマージする場合かを返す | 無し | `cd-admin` だけ真 | 無し |
| `Case.human_approval` | `lib/release_case.py` | 新設 | `human_approval -> bool`（プロパティ） | MVV 判定で承認ゲートを代えない場合かを返す | 無し | `cd-review`・`cd-admin` で真 | 無し |
| `release_case.decide_case` | `lib/release_case.py` | 変更 | `decide_case(has_cd: bool, approvals: int \| None, why: str, admin: bool = False) -> Case` | CD の有無・承認数・迂回の宣言から場合を決める | 無し | CD があり承認数が 1 以上で `admin` が真なら `cd-admin`。承認数が 0 か `None` なら `admin` に依らず `cd-no-review`（I2） | 無し |
| `release_case.case_of` | `lib/release_case.py` | 変更 | `case_of(root) -> Case`（変わらない） | 宣言と GitHub の設定から場合を決める | 無し | 宣言の `problems` が空のときだけ `production_admin` の結果を `decide_case` へ渡す | GitHub の API を読む（既存）・宣言を読む |
| `approval_trail.parse_by` | `release_lib/approval_trail.py` | 新設 | `parse_by(body: str) -> str \| None` | 証跡の本文の `担い手:` の行の値を返す | 本文は証跡の印を持つ | 行が無ければ `None`、あれば前後の空白を除いた値 | 無し |
| `approval_trail.trails_of` | `release_lib/approval_trail.py` | 変更 | `trails_of(comments: list[dict]) -> list[Trail]`（変わらない） | コメントの並びから証跡を読む | 無し | 各 `Trail` が `by`（`parse_by`）と `url`（`html_url`）を持つ。迂回の記録は証跡の印を持たないため含まれない | 無し |
| `approval_trail.judge` | `release_lib/approval_trail.py` | 変更 | `judge(trails, roles, head, by: str \| None = None) -> Judgment` | 承認権限を持つ人の最新の証跡と先端を照らす | 無し | `by` を渡せば担い手がその値の証跡だけを数える。成功のとき `Judgment.url` に照らした証跡の URL を入れる | 無し |
| `approval_trail.judge_trails_on` | `release_lib/approval_trail.py` | 変更 | `judge_trails_on(repo: str, pr: int, head: str, by: str \| None = None) -> Judgment` | PR の上の証跡を読んで `judge` へ渡す | 無し | `by` を `judge` へそのまま渡す | GitHub の API を読む（既存） |
| `approval_trail.render_admin_record` | `release_lib/approval_trail.py` | 新設 | `render_admin_record(sha: str, trail_url: str) -> str` | 迂回の記録の本文（設計文書の「迂回の記録」の形）を作る | `sha` は 40 桁 | 本文が `ADMIN_MARK` と `マージした先端:` の行を持ち、証跡の印 `TRAIL_MARK` を含まない | 無し |
| `approval_trail.write_admin_record` | `release_lib/approval_trail.py` | 新設 | `write_admin_record(repo: str, pr: int, sha: str, trail_url: str) -> tuple[bool, str]` | 同じ先端の記録が無いときだけ迂回の記録を置く（I7） | 本番の PR はマージ済み | (置いたか, コメントの URL)。同じ先端の記録が既にあれば置かずに `(False, その URL)` | GitHub の API を読み、コメントを 1 件書く |
| `approval.approved_on_pr` | `release_lib/approval.py` | 変更 | `approved_on_pr(repo, pr, head, case) -> Judgment`（変わらない） | PR の上の承認と先端を照らす | `case.has_cd` | `case.human_approval` かつ `case.trail_approval` なら `by="user"` で照らす（I9）。`cd-review` はレビューで照らす（変えない） | GitHub の API を読む（既存） |
| `reviewed.admin_record_item` | `release_lib/reviewed.py` | 新設 | `admin_record_item(repo: str, pr: int, sha: str, trail_url: str) -> dict` | 迂回の記録を書き、結果の行を返す | 迂回でマージした直後か、`admin_record_on_resume` が条件を確かめた後 | `{"kind": "comment", "name": <URL か #番号>, "result": "written" \| "kept" \| "unwritten"}`。書けなければ `reason` を添えた `unwritten` を返し、例外を上げない | `approval_trail.write_admin_record` を呼ぶ |
| `reviewed.admin_record_on_resume` | `release_lib/reviewed.py` | 新設 | `admin_record_on_resume(root, repo: str, case, pr: int) -> dict \| None` | マージ済みの本番の PR に、迂回したのに記録が無ければ記録を書く | 本番の PR はマージ済み | `case.admin_merge` が偽・レビューの判定が `APPROVED`・利用者の証跡が先端と一致しない、のどれかなら `None`。ほかは `admin_record_item` の結果の行 | `gh pr view` で判定と先端を読む・証跡を読む・コメントを書く |
| `reviewed.admin_back` | `release_lib/reviewed.py` | 新設 | `admin_back(back=None) -> Callable[[list, dict, str], tuple[str, int, str]]` | 公開の後に、記録を書けなかったことを本番の完了と分けて報告する関数を作る（決定 7） | `back` は `promotion.merge_back_after` の形か `None` | 返す関数は先に `back` を打ち、結果の行に `unwritten` の記録があれば状態を `stopped`・終了コードを 1（`back` が既に止まっていればその終了コード）にし、要約に「迂回の記録を書けなかった（本番は出ている）」を足す | `back` が持つもの（戻すマージ） |
| `reviewed.release_prod` | `release_lib/reviewed.py` | 変更 | `release_prod(ctx, case, ops)`（変わらない） | CD のある場合の本番を、承認の照合・マージ・記録・公開の後まで進める | `case.has_cd` | 案内の分岐が `case.trail_approval` で決まる。結果の行の `admin` が `case.admin_merge`。`ops.published` へ `admin_back()` を渡す | 既存のもの |
| `reviewed._open_prod_pr` | `release_lib/reviewed.py` | 変更 | `_open_prod_pr(root, a, plugin, plugins, branch, base, prod, items, metrics, ops, case) -> int` | 本番の PR を引くか作る。マージ済みなら記録を確かめて公開の後へ進む | 無し | マージ済みのとき、`admin_record_on_resume` の結果の行を足してから `ops.published(..., back=admin_back())` を呼ぶ | 既存のもの・`admin_record_on_resume` のもの |
| `reviewed.merge_on_pr_approval` | `release_lib/reviewed.py` | 変更 | `merge_on_pr_approval(...)`（引数は変わらない） | 承認の照合の後に、場合に応じて迂回の有無を決めてマージを頼み、迂回したら記録を書く | `case.has_cd` | `case.trail_approval` なら証跡の commit status を待つ。`no_admin` は `not case.admin_merge`。迂回でマージしたら `admin_record_item` の結果の行を `items` へ足す | 既存のもの・コメントを書く |
| `promotion.release_selected` | `release_lib/promotion.py` | 変更 | `release_selected(...)`（変わらない） | 選んで集める取り込み方の本番の PR をマージし、公開の後の `back` を返す | 無し | マージした行に `admin`（場合があれば `case.admin_merge`、無ければ偽）を書く。マージ済みからの再開で場合があれば `admin_record_on_resume` の結果の行を足す。返す `back` は `admin_back(merge_back_after(...))` | 既存のもの・コメントを書く |
| `merge._moved_after_refusal` | `merged_lib/merge.py` | 新設 | `_moved_after_refusal(root, n: int, sha: str \| None) -> str \| None` | マージを断られた後に PR を読み直し、先端が動いていればその先端を返す（I6） | `gh pr merge` が 0 以外で終わった | 読み直した先端が `sha` と違えばその先端、同じか `sha` が無いか読めなければ `None` | `gh pr view` を 1 回打つ |
| `merge._merge_pr` | `merged_lib/merge.py` | 変更 | `_merge_pr(root, a, n, watch, items, waits, trail_item=None)`（変わらない） | 緑を確かめた先端で `gh pr merge` を打ち、結果の行を足す | `watch.wait()` が終わっている | マージした行に `admin`（`not no_admin`）を必ず書く。断られて先端が動いていれば `head_moved` の項目で止まる（終了コード 1） | `gh pr merge` を打つ（既存）・`gh pr view` を打つ |
| `merge.merge_when_green` | `merged_lib/merge.py` | 変更 | `merge_when_green(a, cleanup, ..., trail_item=None, after_merge=None)` | チェックを待ってマージし、結果を出す | 無し | `after_merge` を渡せば、`_merge_pr` の後と `already_merged` のときの両方で結果を出す前に呼ぶ。`after_merge` が文を返せば `stopped`（終了コード 1）で「マージした。<文>」を出す | 既存のもの・`after_merge` のもの |
| `merge._admin_after` | `merged_lib/merge.py` | 新設 | `_admin_after(root, repo: str, case, n: int, sha: str, trail_url: str) -> Callable[[list[dict]], str \| None]` | `promote` が `merge_when_green` へ渡す、記録を書く処理を作る | `case.admin_merge` | 返す関数は、`items` にマージ済みの行があれば `admin_record_on_resume`、マージした行があれば `admin_record_item` の結果の行を `items` へ足し、`unwritten` なら報告の文を返す（ほかは `None`） | 返す関数がコメントを書く |
| `merge.promote` | `merged_lib/merge.py` | 変更 | `promote(a, cleanup)`（変わらない） | 本番チャネルへのリリースの PR を、承認ゲートの後にマージする | 無し | CD のある場合の `a.no_admin` は `not case.admin_merge`。`case.admin_merge` なら `_admin_after` を `after_merge` で渡す | 既存のもの |
| `merge._trail_before_merge` | `merged_lib/merge.py` | 変更 | `_trail_before_merge(root, a, n, case) -> dict \| None`（変わらない） | マージの前に証跡を書くかレビューを照らす | `case.has_cd` | `case.trail_approval` なら証跡を書く。`case.human_approval` で `a.gate_approved` が `mvv` なら書かずに承認ゲート（10）で止まる（I9）。返す証跡の行の `name` は証跡の URL | GitHub の API を読み、コメントを書く（既存） |
| `release_cd.cd_prod_steps` | `supervise_lib/release_cd.py` | 変更 | `cd_prod_steps(a, v, prs, repo, approval, approval_steps, case) -> list[dict]`（変わらない） | 本番の PR の承認ゲートの後のステップを組む | `case` は CD のある場合の名前 | `approve-pr` を置くのは `case` が `TRAIL_APPROVAL` にあるとき。MVV 判定の記録の後の行き先は `case` が `HUMAN_APPROVAL` にあれば `end`、なければ `approve-pr`。`approve-pr` の担い手は MVV 判定のプランかつ `HUMAN_APPROVAL` に無いときだけ `mvv`、ほかは `user` | 無し |
| `declaration.production_admin` | `domains/release/declaration.py` | 新設 | `production_admin(root: Path) -> bool` | 作り直しで迂回の宣言があるかを I1 の規則で返す | `root` はリポジトリの根（workflow では既定ブランチを取り出した木） | 値が JSON の `true` のときだけ真。ファイルが無い・読めない・壊れている・型の違いは偽 | `.ndf/project.json` を読む |
| `GitHub.merge_as_admin` | `adapters/github.py` | 新設 | `merge_as_admin(number: int, expect_head: str, method: Optional[str] = None) -> MergeOutcome` | GraphQL の `mergePullRequest` を `expectedHeadOid` 付きで打つ（決定 5） | 迂回すると `Lander._bypass` が決めた | 成功なら `merged` が真。断られたら PR を読み直して `refusal_kind` の種類を返す。`merge` と同じ先の確認（`_preflight`・許されたマージの方式）を通る | GraphQL を 2 回（PR の ID・マージ）と REST を読む |
| `GitHub.review_decision` | `adapters/github.py` | 新設 | `review_decision(number: int) -> str` | PR の `reviewDecision` を大文字で返す | 無し | 値が無ければ空の文字列 | GraphQL を 1 回読む |
| `_refused_by_graphql` | `adapters/github.py` | 新設 | `_refused_by_graphql(exc: GitHubError) -> bool` | GraphQL の誤りが GitHub の拒否か（届かなかった・上限ではないか）を返す | 無し | `status == 0`・`unreached` が偽・`quota` が `None` のときだけ真 | 無し |
| `trail.render_admin_record` | `domains/release/trail.py` | 新設 | `render_admin_record(sha: str, trail_url: str) -> str` | 作り直しの迂回の記録の本文を作る | `sha` は 40 桁 | 旧いプラグインと同じ印と行を持ち、証跡の印を含まない | 無し |
| `trail.write_admin_record` | `domains/release/trail.py` | 新設 | `write_admin_record(hub: GitHub, number: int, sha: str) -> tuple[bool, str]` | 同じ先端の記録が無いときだけ、照らした証跡を指す迂回の記録を置く（I7） | 本番の PR はマージ済み | (置いたか, コメントの URL)。照らした証跡は `read` の判定の `trail.order` の位置のコメントの URL | GitHub の API を読み、コメントを 1 件書く |
| `Lander.approvals` | `domains/release/land.py` | 新設 | `approvals(branch: str) -> int \| None` | 宛先のブランチの承認数を読む | 無し | ruleset の `pull_request` 規則があればその最大、無ければ従来の保護の承認数、保護が無ければ 0、読めなければ `None` | GitHub の API を読む |
| `Lander._bypass` | `domains/release/land.py` | 新設 | `_bypass(pull: Pull) -> bool` | この PR を迂回でマージするかを決める（I2） | 無し | `self.admin` が真・宛先が本番ブランチ・`approvals` が 1 以上、のすべてで真 | `approvals` のもの |
| `Lander._merge` | `domains/release/land.py` | 変更 | `_merge(pull: Pull, bypass: bool = False) -> Landed` | チェックの待ち・証跡の読み直しの後にマージする | 無し | `bypass` なら `merge_as_admin`、偽なら今の `merge`。戻す PR のマージは常に偽で呼ぶ | 既存のもの |
| `Lander._record` | `domains/release/land.py` | 新設 | `_record(pull: Pull, done: Landed) -> Landed` | 迂回でマージした結果に、迂回の記録の URL か書けなかった理由を足す | 本番の PR は迂回でマージした | `admin` が真。書けたら `admin_record` に URL、書けなければ `record_error` に理由 | `trail.write_admin_record` のもの |
| `Lander.land` | `domains/release/land.py` | 変更 | `land(number: int) -> Landed`（変わらない） | リリースの PR をマージし、本番ブランチへ入れたら戻す PR まで進める | 無し | 迂回でマージしたら戻す PR の後に `_record` を通す。マージ済みからの再開で `_bypass` が真かつ `review_decision` が `APPROVED` でなければ `_record` を通す | 既存のもの |
| `cli.cmd_merge` | `domains/release/cli.py` | 変更 | `cmd_merge(args) -> dict`（変わらない） | `release-steps merge` の結果を組む | 無し | `Lander` へ `admin=declaration.production_admin(root)` を渡す。出力に `admin` と、あれば `admin_record` を書く。マージ済みで `record_error` があれば `stopped`（種類 `admin_record`）で、`production_sha` を出力に残す | 宣言を読む |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `project_decl.production_admin` | 宣言を読めない | ファイルが壊れている・キーの型が違う | `read_project_decl` が `{}` を返す・`isinstance` の判定 | 既定値で続ける（偽。I1） | 出力しない。ファイルが壊れていれば、先に `case_of` が `problems` で止める | 無し。直して打ち直す |
| `release_case.case_of` | 宣言のファイルが壊れている | JSON として読めない | `delivery.load_delivery` の `problems`（既存） | 止める（`CaseUnreadable` → `case_or_stop` が 3） | `stopped`・3（既存の文言）。`--admin` のマージに届かない | 宣言を直して打ち直す |
| `release_case.case_of` | 承認数を読めない | GitHub の API の失敗 | `read_approvals` が `None`（既存） | 既定値で続ける（`cd-no-review`。I2・決定 3） | 迂回なしでマージし、レビューが本当に必須なら GitHub が断って 1 | 無し |
| `approval_trail.judge_trails_on` | 証跡を読めない | GitHub の API の失敗 | `_gh_api` | 呼び出し元へ返す（`TrailError("unreadable")`。既存） | `merge_on_pr_approval` が `stopped`・1 | 打ち直せる |
| `approval.approved_on_pr` | 証跡かレビューを読めない | GitHub の API の失敗 | `judge_trails_on`・`read_review_commit` の `TrailError`（既存） | 呼び出し元へ返す | `merge_on_pr_approval` が `stopped`・1。担い手が `mvv` の証跡だけのときは失敗ではなく failure の判定を返し、呼び出し元が `gate`・10 | 打ち直せる |
| `approval_trail.write_admin_record` | コメントを読めない・書けない | GitHub の API の失敗・権限の不足 | `read_comments`・`gh_rest.comment` の `ok` | 呼び出し元へ返す（`TrailError("unreadable")`） | 呼び出し元が `unwritten` の行にする | 打ち直すと、同じ先端の記録が無ければ書く |
| `reviewed.admin_record_item` | 記録を書けない | `TrailError` | `approval_trail.write_admin_record` | 既定値で続ける（`unwritten` の行。例外を上げない） | 公開の後に `admin_back` が `stopped`・1 と「迂回の記録を書けなかった（本番は出ている）」を出す | 打ち直しは再開の経路で記録だけを書く（決定 7） |
| `reviewed.admin_record_on_resume` | 判定か先端を読めない | `gh pr view` の失敗・証跡を読めない | `gh_json` の `StepError`・`TrailError` | 既定値で続ける（`unwritten` の行。理由に読めなかったことを書く） | `admin_back` が `stopped`・1 | 打ち直せる |
| `reviewed.admin_back` | 戻すマージと記録の両方で止まった | `back` が `stopped` を返し、記録も `unwritten` | 返す関数の中 | 止める（`back` の終了コードを使い、要約に両方を書く） | `stopped`・`back` の終了コード | 打ち直すと本番のマージと公開を飛ばし、戻すマージと記録だけをやり直す |
| `reviewed.release_prod` | 承認が先端と一致しない・担い手が `mvv` だけ | 証跡が無い・古い・MVV 判定の証跡 | `merge_on_pr_approval` の `judged.state` | 止める（承認ゲート） | `gate`・10（I4・I9） | 承認を書いて打ち直す |
| `reviewed._open_prod_pr` | マージ済みの PR の記録を書けない | `admin_record_on_resume` が `unwritten` | 結果の行 | 既定値で続ける（公開の後へ進み、`admin_back` が報告する） | `stopped`・1 | 打ち直せる |
| `reviewed.merge_on_pr_approval` | チェックが落ちた・上限・先端が動いた | CI の失敗・待ちの上限・承認の後の push | `wait_and_merge` の `StepError`・`HeadMoved`（既存） | 止める | 落ちたら 1、上限なら 75、先端が動いたら `gate`・10（I5・I6） | 直して打ち直す。PR は開いたまま |
| `promotion.release_selected` | マージ済みの PR の記録を書けない | `admin_record_on_resume` が `unwritten` | 結果の行 | 既定値で続ける（`admin_back` が報告する） | `stopped`・1 | 打ち直せる |
| `merge._moved_after_refusal` | 読み直せない | `gh pr view` の失敗 | `pr_state` の `StepError` | 既定値で続ける（`None`。今の `stopped` のまま） | `_merge_pr` の今の `stopped`・1 | 打ち直せる |
| `merge._merge_pr` | GitHub が断った | 先端が動いた・権限の不足・規則を満たさない | `gh pr merge` の終了コード | 止める。先端が動いていれば `head_moved` の項目、衝突なら既存の 4、ほかは既存の `stopped` | `head_moved` は `stopped`・1（`release-steps.py` の `wait_and_merge` が `HeadMoved` にし、本番の経路は 10） | マージしない。承認を取り直して打ち直す |
| `merge.merge_when_green` | 記録を書けない | `after_merge` が文を返した | `after_merge` の戻り値 | 止める（マージは済んでいる） | `stopped`・1「#<番号> をマージした。迂回の記録を書けなかった（本番は出ている）」 | 打ち直すと待ちが `already_merged` を見て記録だけを書く |
| `merge._admin_after` | 記録を書けない | `unwritten` の行 | 返す関数の中 | 呼び出し元へ返す（報告の文） | `merge_when_green` の `stopped`・1 | 打ち直せる |
| `merge.promote` | 宣言のファイルが壊れている | JSON として読めない | `release_case.case_or_stop`（既存） | 止める | `stopped`・3 | 宣言を直して打ち直す |
| `merge._trail_before_merge` | `cd-admin` で担い手が `mvv` | 手順を外れた `--gate-approved mvv` | `case.human_approval` と `a.gate_approved` | 止める（証跡を書かない。I9） | `gate`・10「MVV 判定では本番チャネルの承認レビューを代えない」 | 利用者の承認を得て `--gate-approved user` で打ち直す |
| `merge._trail_before_merge` | 証跡を書けない | GitHub の API の失敗 | `TrailError("unreadable")`（既存） | 止める | `stopped`・1 | 打ち直せる |
| `declaration.production_admin` | 宣言を読めない | ファイルが無い・壊れている | `OSError`・`json.JSONDecodeError` | 既定値で続ける（偽。迂回しない） | 出力の `admin` が `false` | 無し |
| `GitHub.merge_as_admin` | GitHub が断った | 先端が動いた・迂回の権限が無い・規則を満たさない | GraphQL の誤り（`_refused_by_graphql`） | 呼び出し元へ返す（PR を読み直し、`refusal_kind` の種類の `MergeOutcome`） | `cmd_merge` が `stopped`（種類 `refused` など） | マージしない。記録を置かない。直して打ち直す |
| `GitHub.merge_as_admin` | 届かない・上限 | ネットワーク・GraphQL の枠 | `_refused_by_graphql` が偽 | 呼び出し元へ返す（`GitHubError` を投げ直す） | workflow の `merge` が失敗 | 打ち直せる |
| `GitHub.review_decision` | 読めない | GitHub の API の失敗 | `graphql` の `GitHubError` | 呼び出し元へ返す | `Lander.land` が `_record` へ進まず、例外として workflow が失敗 | 打ち直せる |
| `trail.write_admin_record` | コメントを読めない・書けない | GitHub の API の失敗 | `GitHubError` | 呼び出し元へ返す（`TrailError`） | `_record` が `record_error` に入れる | 打ち直せる |
| `Lander.approvals` | 承認数を読めない | GitHub の API の失敗（404 以外） | `GitHubError` | 既定値で続ける（`None`。迂回しない。I2） | 出力の `admin` が `false` | 無し |
| `Lander._bypass` | 承認数を読めない | `approvals` が `None` | 戻り値 | 既定値で続ける（偽） | 同上 | 無し |
| `Lander._merge` | チェックが落ちた・証跡が無い・先端が動いた | CI・承認・push | `_watch`・`_refusal`（既存） | 呼び出し元へ返す（`test_failed`・`refused`） | `stopped`。マージしない | 直して打ち直す |
| `Lander._record` | 記録を書けない | `TrailError` | `trail.write_admin_record` | 既定値で続ける（`record_error`） | `cmd_merge` が `stopped`（種類 `admin_record`） | 打ち直すと再開の経路で記録だけを書く |
| `Lander.land` | 戻す PR で止まった | 戻す PR のチェック | `_sync`（既存） | 呼び出し元へ返す。迂回の記録は戻す PR の結果に依らず書く | `stopped`（既存の種類） | 打ち直せる |
| `cli.cmd_merge` | 記録を書けない | `record_error` | `Landed` | 止める（本番は出ている） | `stopped`・種類 `admin_record`。`production_sha` は出力に残り、workflow の publish へ渡る（既存の「戻す PR で止まっても渡す」形） | 打ち直せる |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `release_case.decide_case` | CD が無い | `no-cd` |
| `release_case.decide_case` | CD があり、承認数が 1 以上、`admin` が真 | `cd-admin`（承認を取る場所: リリースの PR の証跡のコメント・マージの主体: 利用者のアカウント（管理者の迂回）） |
| `release_case.decide_case` | CD があり、承認数が 1 以上、`admin` が偽 | `cd-review` |
| `release_case.decide_case` | CD があり、承認数が 0 か `None`（`admin` は問わない） | `cd-no-review` |
| `approval.approved_on_pr` | `cd-review` | レビューのコミットで照らす（変えない） |
| `approval.approved_on_pr` | `cd-no-review` | 担い手を問わずに証跡で照らす（変えない） |
| `approval.approved_on_pr` | `cd-admin` | 担い手が `user` の証跡だけで照らす |
| `reviewed.admin_record_on_resume` | `case.admin_merge` が偽 | `None`（何もしない） |
| `reviewed.admin_record_on_resume` | 迂回する場合で、レビューの判定が `APPROVED` | `None`（承認レビューを満たしてマージされた） |
| `reviewed.admin_record_on_resume` | 迂回する場合で、判定が `APPROVED` でなく、利用者の証跡が先端と一致する | `admin_record_item`（同じ先端の記録があれば `kept`） |
| `reviewed.admin_record_on_resume` | 迂回する場合で、判定が `APPROVED` でなく、利用者の証跡が先端と一致しない | `None`（NDF の迂回でマージしたと言えないため記録しない） |
| `merge._merge_pr` | 成功 | マージした行（`admin` は `not no_admin`） |
| `merge._merge_pr` | 断られ、衝突 | 既存の `stop_conflict`（4） |
| `merge._merge_pr` | 断られ、`_moved_after_refusal` が先端を返した | `head_moved` の項目で `stopped`（1） |
| `merge._merge_pr` | 断られ、先端が同じか読めない | 既存の `stopped`（1） |
| `merge._trail_before_merge` | `trail_approval` が真、`human_approval` が真、担い手が `mvv` | 承認ゲート（10）。証跡を書かない |
| `merge._trail_before_merge` | `trail_approval` が真、それ以外 | 証跡を書いて commit status を待つ |
| `merge._trail_before_merge` | `trail_approval` が偽（`cd-review`） | レビューを照らす（変えない） |
| `release_cd.cd_prod_steps` | `cd-no-review`・MVV 判定のプラン | MVV 判定 → 記録 → `approve-pr --by mvv`（変えない） |
| `release_cd.cd_prod_steps` | `cd-review`・MVV 判定のプラン | MVV 判定 → 記録 → `end`。`approve-pr` を置かない（変えない） |
| `release_cd.cd_prod_steps` | `cd-admin`・MVV 判定のプラン | MVV 判定 → 記録 → `end`。その後に `approve-pr --by user` を置く（`run <計画> --from approve-pr` で続ける） |
| `release_cd.cd_prod_steps` | MVV 判定でないプラン | `approval-post` → `end`。`TRAIL_APPROVAL` の場合だけ `approve-pr --by user` を置く（`cd-admin` は `cd-no-review` と同じ形） |
| `Lander._bypass` | 宣言が無い | 偽（今のマージ） |
| `Lander._bypass` | 宣言があり、宛先が本番ブランチでない（戻す PR） | 偽 |
| `Lander._bypass` | 宣言があり、本番ブランチ宛て、承認数が 0 か `None` | 偽（記録を置かず `admin` は `false`。I2） |
| `Lander._bypass` | 宣言があり、本番ブランチ宛て、承認数が 1 以上 | 真 |
| `Lander.land` | マージ済みの本番の PR・`_bypass` が真・`review_decision` が `APPROVED` でない | 戻す PR の後に `_record`（同じ先端の記録があれば置かない） |
| `Lander.land` | マージ済みの本番の PR・それ以外 | 今のまま戻す PR へ進む |

- 繰り返しの終わる条件: 新しい繰り返しは足さない。チェックの待ちは既存の `GreenWatch`（旧いプラグイン）と `ci.Watch`（作り直し）の上限で終わる
- 計算量の注意: 記録の有無の確かめは PR のコメントを全部読む（旧いプラグインは既存の `read_comments` のページ読み、作り直しは `github.list-pages` の上限）。本番の PR のコメントは数十件で、新しい上限は足さない

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `CD_ADMIN` | 定数 | `lib/release_case.py` | `"cd-admin"` |
| `TRAIL_APPROVAL` | 定数（`frozenset`） | `lib/release_case.py` | `{CD_NO_REVIEW, CD_ADMIN}` |
| `ADMIN_MERGE` | 定数（`frozenset`） | `lib/release_case.py` | `{CD_ADMIN}` |
| `HUMAN_APPROVAL` | 定数（`frozenset`） | `lib/release_case.py` | `{CD_REVIEW, CD_ADMIN}`。`Case` の 3 つの属性と `release_cd.cd_prod_steps` はこの 3 つの集合だけを読む（場合の属性の唯一の置き場） |
| `ProductionMerge` | 型 | `project_lib/model.py` | `admin: bool`（必須） |
| `ProjectDecl.production_merge` | 型 | `project_lib/model.py` | `None`（`Optional[Union[ProductionMerge, Unknown]]`）。解析が書くキーの一覧 `ITEM_KEYS` に入れない |
| `keys.production_merge` | 設定値 | `contracts/settings/project.json.json` | `{"summary": ..., "place": "main"}` |
| `Trail.by` / `Trail.url` | 型 | `release_lib/approval_trail.py` | 末尾に足し、既定は `None` / `""`（既存の作り方を壊さない） |
| `Judgment.url` | 型 | `release_lib/approval_trail.py` | `None` |
| `ADMIN_MARK` | 定数 | `release_lib/approval_trail.py` | `"<!-- ndf:admin-merge -->"` |
| `ADMIN_SHA_LINE` | 定数 | `release_lib/approval_trail.py` | `"マージした先端:"` |
| `ADMIN_TRAIL_LINE` | 定数 | `release_lib/approval_trail.py` | `"照らした承認の証跡:"` |
| `ADMIN_MARK`（作り直し） | 定数 | `domains/release/trail.py` | 旧いプラグインと同じ文字列。作り直しは旧いプラグインを読み込まないため、同じ値を別に持つ。2 つが揃っていることは両方のテストが同じ本文の形で縛る |
| `GRAPHQL_MERGE_METHODS` | 定数 | `adapters/github.py` | `{"merge": "MERGE", "squash": "SQUASH", "rebase": "REBASE"}` |
| `_PULL_ID` / `_ADMIN_MERGE` / `_REVIEW_DECISION` | 定数（GraphQL の文） | `adapters/github.py` | PR の ID の読み取り・`mergePullRequest(input: {pullRequestId, expectedHeadOid, mergeMethod})`・`reviewDecision` の読み取り |
| `Lander.admin` | 型 | `domains/release/land.py` | `False`（既存の呼び出しとテストの作り方を壊さない） |
| `Landed.admin` / `Landed.admin_record` / `Landed.record_error` | 型 | `domains/release/land.py` | `False` / `None` / `None` |
| `Authored.url` | 型 | `adapters/github.py` | `""`。`from_rest` が `html_url` を入れる |
| 結果の行の `admin` | 結果 JSON の項目 | `merged_lib/merge.py`・`release_lib/reviewed.py`・`release_lib/promotion.py` | マージした行に必ず書く（I8） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 本番のマージ（`release --channel prod`・`promote`）と、証跡のチェックの workflow（`release-approval.yml` の `approval-check`） | 本番の PR の先端・`ndf/release-approval` の commit status | 取らない（既存）。迂回の記録は証跡の印を持たないため、記録のコメントは workflow を起こさない | マージは `--match-head-commit` で先端を固定する（I6） |
| 迂回のマージの直後に、同じ本番の PR へ打ち直しが走る | 本番の PR のコメント | 取らない。同じ先端の記録があれば書かない（読んでから書く） | 打ち直しは何度でも 1 件になる。2 つの打ち直しが同時に読んで同時に書くと 2 件になりうる（下の残リスク） |
| 作り直しの `release-steps merge` の workflow の起動が重なる | 本番の PR・戻す PR・コメント | workflow の `concurrency` の group（既存）が同じリリースの起動を 1 本ずつにする | `Lander.land` はマージ済みからの再開で記録を書く |
| 本番のマージの途中の中断（プロセスの停止） | 本番の PR | 無し | マージの前に止まれば PR は開いたまま。マージの後・記録の前に止まれば、再開の経路（`admin_record_on_resume`・`Lander.land`）が記録を書く |

旧いプラグインの本番のマージは、プランが 1 本ずつ流し、同じ版の `release` を 2 つ同時に打つ手順は無い。手で同時に打った場合だけ記録が 2 件になりうる。2 件になってもマージの判定と証跡の判定には数えないため、マージの結果は変わらない。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `reviewed.release_prod` | 本番の PR の行（`admin`）・迂回の記録の行（`written` / `kept` / `unwritten`）・`metrics.case` | 結果 JSON | token |
| `promotion.release_selected` | 同上 | 結果 JSON | token |
| `reviewed.admin_back` | 記録を書けなかったことを含む要約 | 結果 JSON の `summary` と `status` | API の応答の本文（理由は先頭 200 字まで） |
| `merge._merge_pr` | マージした行の `admin`・`head_moved` の項目（`head`・`expected`） | 結果 JSON | `gh` の標準エラーの全文（先頭 300 字まで。既存） |
| `merge.merge_when_green` | 迂回の記録の行・記録を書けなかったときの要約 | 結果 JSON | token |
| `approval_trail.write_admin_record` | 迂回の記録のコメント（マージした先端・照らした証跡の URL・宣言の置き場） | 本番の PR のコメント | 承認した人のアカウント以外の個人の情報・token |
| `trail.write_admin_record` | 同上 | 本番の PR のコメント | 同上 |
| `cli.cmd_merge` | `outputs.admin`・`outputs.admin_record`・`production_sha` | 結果 JSON（workflow の出力） | token |

進捗記録（`projects-sync.sh`）へは何も足さない。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `project_decl.production_admin` | `project_decl.policy`（`ndf_policies` の真偽の読み取り） | `grep -n "def policy" plugins/ndf/scripts/lib/project_decl.py` | 使えない: `policy` は `ndf_policies` の下のキーだけを読む。同じ形（`is True` の判定）で 1 行の関数を足す |
| `Case.trail_approval` | 値どうしの比較（`case.case == CD_NO_REVIEW`） | `grep -rn "CD_NO_REVIEW\|CD_REVIEW" plugins/ndf/scripts --include=*.py` | 使えない: 値を足すたびに比較の置き場を書き換えることになる（決定 2）。属性へ置き換える |
| `Case.admin_merge` | 同上 | 同上 | 同上 |
| `Case.human_approval` | 同上 | 同上 | 同上 |
| `approval_trail.parse_by` | `approval_trail._line_shas`（行の見出しで値を読む） | `grep -n "def _line_shas" plugins/ndf/scripts/release_lib/approval_trail.py` | 使えない: 40 桁の SHA だけを拾う。同じ行の読み方で文字列を返す関数を足す |
| `approval_trail.render_admin_record` | `approval_trail.render_trail` | `grep -n "def render_" plugins/ndf/scripts/release_lib/approval_trail.py` | 使えない: 証跡の印を持ち、証跡の判定に数えられる。印を分けた別の本文が要る |
| `approval_trail.write_admin_record` | `approval_trail.write_trail`（同じ人・同じ SHA があれば置かない） | 同上 `grep -n "def write_trail"` | 使う部品: `read_comments`・`gh_rest.comment`・`TrailError` を使う。照合の対象（証跡の判定・先端の読み直し）が違うため関数は分ける |
| `reviewed.admin_record_item` | `merge._trail_before_merge` の結果の行の形（`kind: comment`） | `grep -n '"kind": "comment"' plugins/ndf/scripts/merged_lib/merge.py` | 使う: 同じ行の形を使う |
| `reviewed.admin_record_on_resume` | `release-steps.py` の `pr_view`・`reviewed.gh_json` | `grep -n "def pr_view\|gh_json" plugins/ndf/scripts/release-steps.py plugins/ndf/scripts/release_lib/reviewed.py` | 使う: `gh_json` で `gh pr view --json reviewDecision,headRefOid` を読む（`release_lib/` から `release-steps.py` を読み込まない） |
| `reviewed.admin_back` | `promotion.merge_back_after`（本番の完了と分けて報告する `back`） | `grep -n "def merge_back_after" -A16 plugins/ndf/scripts/release_lib/promotion.py` | 使う: 同じ `back` の形（`(状態, 終了コード, 要約)`）に合わせ、`merge_back_after` の `back` を包む |
| `merge._moved_after_refusal` | `merge._conflicted`（断られた後の読み直し）・`pr_state` | `grep -n "def _conflicted" -A8 plugins/ndf/scripts/merged_lib/merge.py` | 使う: `pr_state` で読み直す。`_conflicted` と同じ読みを 2 回打たないよう、`_merge_pr` は読み直しの結果を 1 回だけ取る |
| `merge._admin_after` | `merge_when_green` の `trail_item`（`promote` から渡す項目） | `grep -n "trail_item" plugins/ndf/scripts/merged_lib/merge.py` | 使えない: 項目はマージの前に決まる。記録はマージの後に書くため、項目ではなく処理を渡す |
| `declaration.production_admin` | 作り直しの `domains/hooks/strict.py` の `.ndf/project.json` の読み取り | `grep -n "_PROJECT_FILE" -A12 plugins/ndf-next/src/ndf/domains/hooks/strict.py` | 使えない: hook の判定の層に置かれ、リリースの層から読むと層の向きが逆になる。置き場の語（`identity.FIXED_WORDS["declaration_dir"]`）だけを使う |
| `GitHub.merge_as_admin` | `GitHub.merge`（`_preflight`・`allowed_merge_methods`・`pick_merge_method`・`refusal_kind`） | `grep -n "def merge\|def _preflight\|def refusal_kind" plugins/ndf-next/src/ndf/adapters/github.py` | 使う: 先の確認と断られた後の種類の決め方を共有し、要求だけを `mergePullRequest` にする |
| `GitHub.review_decision` | `GitHub.repository`（`repository(owner, name)` の下を読む） | `grep -n "def repository" plugins/ndf-next/src/ndf/adapters/github.py` | 使う |
| `_refused_by_graphql` | `GitHubError` の `status`・`unreached`・`quota` | `grep -n "class GitHubError" -A14 plugins/ndf-next/src/ndf/adapters/github.py` | 使う: 既存の属性だけで判定する |
| `trail.render_admin_record` | `approval_decide.render_trail` | `grep -n "def render_" plugins/ndf-next/src/ndf/domains/release/approval_decide.py` | 使えない: 証跡の印を持つ。別の本文が要る |
| `trail.write_admin_record` | `adapters/publish.py` の `Publisher.comment`・`trail.read` | `grep -n "def comment" -A4 plugins/ndf-next/src/ndf/adapters/publish.py` | 使う: 書き込みは `Publisher.comment`（URL を返す）、照らした証跡は `read` の判定から引く |
| `Lander.approvals` | 旧いプラグインの `release_case.read_approvals`・`Lander.required` | `grep -n "def read_approvals" plugins/ndf/scripts/lib/release_case.py`・`grep -n "def required" plugins/ndf-next/src/ndf/domains/release/land.py` | 使えない: 作り直しは旧いプラグインを読み込まない。`required` と同じ `rules/branches` の読みの形で、承認数を読む関数を足す |
| `Lander._bypass` | 無し | `grep -rn "bypass\|admin" plugins/ndf-next/src/ndf/domains/release` | 該当なし（検索の結果は 0 件）。足す |
| `Lander._record` | `Lander._sync`（`Landed` に結果を足す形） | `grep -n "def _sync" -A12 plugins/ndf-next/src/ndf/domains/release/land.py` | 使う: `_replace` で `Landed` へ足す形に合わせる |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `project_decl.production_admin` | `{"production_merge": {"admin": true}}` で真 | キーが無い・`{"admin": false}`・`{"admin": "true"}`・`{"unknown": "…"}` で偽 | ファイルが壊れていると偽（`case_of` が先に 3 で止めることは `case_of` の行で縛る） |
| `release_case.decide_case` | CD あり・承認数 1・宣言ありで `cd-admin` | 承認数 0・`None` で宣言があっても `cd-no-review`（I2）。宣言なし・承認数 1 で `cd-review` | 無し |
| `release_case.case_of` | ai-plugins の宣言（受け入れ条件 9）で承認数 1 のとき `cd-admin` | 承認数を読めないとき `cd-no-review` | 壊れた `.ndf/project.json` で `CaseUnreadable` |
| `approval_trail.judge` | `by="user"` で利用者の証跡が先端と一致すれば success と URL | `by` を渡さなければ担い手を問わない（今と同じ） | 担い手が `mvv` の証跡だけなら failure |
| `approval_trail.write_admin_record` | 記録が無ければ 1 件書く | 同じ先端の記録があれば書かない。別の先端の記録があれば書く | コメントを書けなければ `TrailError` |
| `reviewed.merge_on_pr_approval` | `cd-admin` で証跡が先端と一致し、チェックが通ると迂回ありで頼み、記録の行が足される | `cd-no-review`・`cd-review` では `no_admin` が真のまま（I3） | 証跡が無い・担い手が `mvv` だけで 10。チェックが落ちて 1、上限で 75、先端が動いて 10 |
| `reviewed.release_prod` | 結果の行の `admin` が `true`・終了コード 0 | 迂回なしの場合は `admin` が `false` | 記録を書けないと `stopped`・1、要約に「本番は出ている」 |
| `reviewed.admin_record_on_resume` | マージ済み・判定が `APPROVED` でない・記録が無いとき書く | 判定が `APPROVED`・記録が既にあるときは書かない | 判定を読めないと `unwritten` |
| `reviewed.admin_back` | 記録が書けていれば `back` の結果のまま | `back` が `None` | 戻すマージと記録の両方で止まると `back` の終了コードで両方を書く |
| `promotion.release_selected` | 選んで集める取り込み方の `cd-admin` で、行の `admin` が `true` | 場合が無い（`no-cd`）とき `admin` が `false` | マージ済みからの再開で記録を書けないと 1 |
| `merge._merge_pr` | 迂回のとき `admin: true` を書く | 迂回なしのとき `admin: false` | 断られて先端が違えば `head_moved`、同じなら今の `stopped`、衝突なら 4 |
| `merge._moved_after_refusal` | 先端が違えば新しい先端 | `sha` が無いとき `None` | 読み直せなければ `None` |
| `merge.promote` | `--gate-approved user` の `cd-admin` で、証跡を書いてから迂回でマージする（受け入れ条件 5） | `cd-no-review` では迂回なし | 証跡が無い・承認と先端が違うと 10、チェックが落ちて 1、上限で 75、先端が動いて `head_moved` の 1 |
| `merge._trail_before_merge` | `cd-admin`・`user` で証跡を書く | `cd-review` はレビューを照らす（今と同じ） | `cd-admin`・`mvv` で証跡を書かずに 10（I9） |
| `merge.merge_when_green` | `after_merge` の行が結果に入る | `already_merged` のときも `after_merge` を呼ぶ | `after_merge` が文を返すと 1 |
| `release_cd.cd_prod_steps` | `cd-admin` の MVV 判定のプランで、記録の後が `end`、`approve-pr` が `--by user` | MVV 判定でないプランの `cd-admin` は `cd-no-review` と同じ形 | `cd-admin` を MVV 判定の行き先へつなぐと落ちる |
| `declaration.production_admin` | `true` で真 | キーが無い・`"true"` で偽 | ファイルが無い・壊れていると偽 |
| `GitHub.merge_as_admin` | `mergePullRequest` を `expectedHeadOid` 付きで打ち、`merged` が真 | 先端が `_preflight` で違えば要求を送らない | GraphQL の拒否で読み直した種類を返す。届かない誤りは投げ直す |
| `Lander._bypass` | 宣言あり・本番ブランチ宛て・承認数 1 で真 | 承認数 0・`None`、戻す PR で偽 | 承認数を読めないと偽 |
| `Lander.land` | 迂回でマージし、記録を置き、`admin` が真（受け入れ条件 8） | 宣言なし・承認数 0 で同期の REST のマージのまま、記録なし | 証跡が無いと `refused`、チェックが落ちると `test_failed`、先端が動くと `refused`。どれもマージしない。マージ済みからの再開で記録が無ければ書く |
| `cli.cmd_merge` | 出力に `admin: true` と `admin_record` | 迂回しないとき `admin: false` | 記録を書けないと `stopped`（`admin_record`）で `production_sha` が残る |
