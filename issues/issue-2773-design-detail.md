# #2773 の詳細設計: リリース承認をリリースの PR へ残し、本番と開発版の反映を workflow で起こす

設計文書 [issue-2773-design.md](issue-2773-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。
語は設計文書の「用語」の表と同じ意味で使う（リリースの場合・承認の証跡・承認資料のコメント・開発版の workflow）。

この文書で使う略記:

| 略記 | 指すもの |
| --- | --- |
| 場合 1 | CD があり、レビューなしでマージできる（`cd-no-review`） |
| 場合 2 | CD があり、レビューが必須（`cd-review`） |
| 場合 3 | CD が無い（`no-cd`）。今の工程のまま |
| 本番の PR | `release/v<版>` → 本番チャネル（`main`）の PR。設計文書の「リリースの PR」 |
| 開発版の PR | `release/v<版>` → ベースブランチ（`develop`）の PR |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/workflows/
├── release-approval.yml *        証跡のチェックの workflow
└── release-dev.yml *             開発版の workflow
.ndf/supervise.json               release.dev_workflow の宣言
plugins/ndf/
├── contracts/limits.json         待ちの上限 2 つを足す
└── scripts/
    ├── release-steps.py          副コマンドのエントリポイント
    ├── merged-steps.py           merge-when-green・promote のエントリポイント
    ├── lib/
    │   ├── release_case.py *     リリースの場合の判定
    │   └── README.md             モジュールの一覧
    ├── release_lib/
    │   ├── approval_trail.py *   証跡の書式・判定・GitHub の読み書き
    │   ├── approval.py           承認の照合（場合ごとの経路）
    │   └── promotion.py          選んで集める取り込み方の本番のマージ
    ├── merged_lib/merge.py       --no-admin・promote の証跡
    └── supervise_lib/
        ├── release_templates.py  本番と開発版のプランの並び
        ├── delivery_templates.py promote のプランへ --no-admin
        ├── templates.py          検査のプランへ場合を渡す
        └── verify_steps.py       全体テストを外す条件
scripts/
├── check-required-checks.py      commit status の宣言を数える
└── required-checks-allow.json    statuses の宣言
```

依存の向きは「エントリポイント（`release-steps.py`・`merged-steps.py`・workflow）→ `release_lib` / `merged_lib` / `supervise_lib` → `lib`」の一方向である。
`lib/release_case.py` は `lib/delivery.py` と `lib/ci_workflows.py` だけを読み、`release_lib` を読まない。
`release_lib/approval_trail.py` は `lib/gh_rest.py`・`lib/approved_commit.py` を読む。
`release_lib/approval.py` は読まない（`approval.py` が `approval_trail.py` を読む）。
`merged_lib/merge.py` は `release_lib/approval_trail.py` と `lib/release_case.py` を読む。逆向きの読み込みは無く、循環しない。
workflow は既定ブランチを取り出して、`release-steps.py` と `merged-steps.py` を呼ぶだけである。Python のモジュールから workflow は読まない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `.github/workflows/release-approval.yml` | 新設 | main 宛ての PR の出来事とコメントの出来事で、既定ブランチの `approval-check` を打つ | `release-steps.py` |
| `.github/workflows/release-dev.yml` | 新設 | `workflow_dispatch` で受けた開発版の PR を確かめ、CI の通過を待って迂回なしでマージする | `merged-steps.py` |
| `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | 証跡の書式の解釈・権限と先端の照合（純粋な関数）と、コメント・権限・commit status・レビューの読み書き | `lib/gh_rest.py`・`lib/approved_commit.py` |
| `plugins/ndf/scripts/lib/release_case.py` | 新設 | `delivery` の宣言と本番チャネルの規則から 3 つの場合を決める | `lib/delivery.py`・`lib/ci_workflows.py`・`gh` |
| `plugins/ndf/scripts/lib/README.md` | 変更 | `release_case.py` の行を足す | 無し |
| `plugins/ndf/scripts/release-steps.py` | 変更 | 副コマンド `approval-check`・`approval-post`・`release-case` を足し、`approve --pr` と `release` を場合ごとの経路へ分ける | `release_lib/*`・`lib/release_case.py` |
| `plugins/ndf/scripts/release_lib/approval.py` | 変更 | 場合 1・2 の承認の照合（証跡・レビュー）と、場合ごとの引数の検査を足す | `release_lib/approval_trail.py` |
| `plugins/ndf/scripts/release_lib/promotion.py` | 変更 | 選んで集める取り込み方の本番のマージを、場合 1・2 では完全な一致だけで通す | `release_lib/approval.py` |
| `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | `--no-admin` を受け、`promote` が場合 1・2 で迂回なしにマージし、場合 1 ではその前に証跡を書く | `release_lib/approval_trail.py`・`lib/release_case.py` |
| `plugins/ndf/scripts/merged-steps.py` | 変更 | `merge-when-green` の引数の説明を `--admin` 固定でない文へ直す | `merged_lib/merge.py` |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | 場合 1・2 の本番のプランを「版上げ → 本番の PR → 承認資料 → 承認ゲート → 証跡 → マージ」に並べ、開発版のプランから承認資料のステップを外す | `lib/release_case.py` |
| `plugins/ndf/scripts/supervise_lib/delivery_templates.py` | 変更 | promote のプランの promote のステップへ、場合 1・2 で `--no-admin` を渡す | `lib/release_case.py` |
| `plugins/ndf/scripts/supervise_lib/templates.py` | 変更 | 検査のプランで、場合 1・2 なら全体テストを外す指定を渡す | `lib/release_case.py`・`supervise_lib/verify_steps.py` |
| `plugins/ndf/scripts/supervise_lib/verify_steps.py` | 変更 | 全体テストを外す関数が、呼び手の指定でも外せるようにする | 無し（今のまま） |
| `plugins/ndf/contracts/limits.json` | 変更 | 証跡のチェックの結果の待ちと、開発版の workflow の待ちの上限を足す | 無し |
| `scripts/check-required-checks.py` | 変更 | 宣言の `statuses` を読み、ジョブの名前と同じ扱いで必須の照合に数える | `scripts/required-checks-allow.json` |
| `scripts/required-checks-allow.json` | 変更 | `ndf/release-approval` の commit status と、証跡のチェックのジョブを必須にしない宣言を足す | 無し |
| `.ndf/supervise.json` | 変更 | `release.dev_workflow: "release-dev.yml"` を足す | 無し |
| `plugins/ndf/skills/release/SKILL.md` | 変更 | 3 つの場合と、workflow を起動して結果を待つ形を書く | 無し |
| `plugins/ndf/skills/release/references/release-steps.md` | 変更 | 新しい副コマンドと `approve --pr` の打ち方を書く | 無し |
| `plugins/ndf/skills/merged/SKILL.md` | 変更 | `merge-when-green --no-admin` と、場合 1・2 の promote を書く | 無し |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 変更 | リリース承認ゲートの置き場所が場合で変わることを書く | 無し |
| `plugins/ndf/skills/development-workflow/references/production-merge.md` | 変更 | 本番のマージの主体と承認の場所を場合ごとに書く | 無し |
| `docs/versioning-and-distribution.md` | 変更 | 正式版を出す手順を、承認の証跡と CD の経路で書き直す | 無し |
| `plugins/ndf/scripts/tests/test_release_approval_trail.py` | 新設 | 証跡の判定と 2 つの workflow の YAML を縛る | 対象のモジュール |
| `plugins/ndf/scripts/tests/test_release_case.py` | 新設 | 場合の判定を縛る | `lib/release_case.py` |
| `plugins/ndf/scripts/tests/test_release_steps.py` | 変更 | `release` の場合ごとの経路・`approve --pr`・`approval-check`・`approval-post` | `release-steps.py` |
| `plugins/ndf/scripts/tests/test_release_selected.py` | 変更 | 選んで集める取り込み方で、場合 1・2 の完全な一致 | `release_lib/promotion.py` |
| `plugins/ndf/scripts/tests/test_step_scripts.py` | 変更 | `merge-when-green --no-admin` と promote の証跡 | `merged-steps.py` |
| `plugins/ndf/scripts/tests/test_supervise_layout.py` | 変更 | 本番と開発版のプランの並び | `supervise_lib/release_templates.py` |
| `plugins/ndf/scripts/tests/test_supervise_ci_only.py` | 変更 | 場合 1・2 の検査のプランに全体テストが無い | `supervise_lib/templates.py` |
| `tests/test_check_required_checks.py` | 変更 | `statuses` の宣言の照合 | `scripts/check-required-checks.py` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/release_lib/approval_trail.py` | モジュール `approval_trail`・型 `Trail`・型 `Judgment`・例外 `TrailError` | 新設: `parse_trail`・`trails_of`・`judge`・`approved_review_commit`・`render_trail`・`render_request`・`read_comments`・`read_roles`・`read_permissions`・`read_head`・`read_reviews`・`open_prs_at`・`post_status`・`write_trail` |
| `plugins/ndf/scripts/lib/release_case.py` | モジュール `release_case`・型 `Case`・例外 `CaseUnreadable` | 新設: `required_approvals`・`decide`・`read_approvals`・`case_of` |
| `plugins/ndf/scripts/release-steps.py` | モジュール | 新設: `cmd_approval_check`・`cmd_approval_post`・`cmd_release_case`・`_release_prod_reviewed`・`_release_dev_by_workflow`・`_wait_ci_except`・`_wait_trail_status`。変更: `cmd_release`・`cmd_approve`・`wait_and_merge`・`build_parser` |
| `plugins/ndf/scripts/release_lib/approval.py` | モジュール | 新設: `approved_on_pr`・`approve_on_pr`。変更: `approved_of`・`approve` |
| `plugins/ndf/scripts/release_lib/promotion.py` | モジュール | 新設: `_merge_exact`。変更: `release_selected` |
| `plugins/ndf/scripts/merged_lib/merge.py` | モジュール | 新設: `_trail_before_merge`。変更: `add_wait_args`・`_merge_pr`・`promote` |
| `plugins/ndf/scripts/merged-steps.py` | モジュール | 変更: `build_parser`（説明の文だけ） |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | モジュール | 新設: `_reviewed_prod_steps`・`_approve_pr_step`・`_approval_post_step`。変更: `plan_release_package_plugin`・`_release_step` |
| `plugins/ndf/scripts/supervise_lib/delivery_templates.py` | モジュール | 変更: `plan_promote`（引数を 1 つ足すだけ） |
| `plugins/ndf/scripts/supervise_lib/templates.py` | モジュール | 変更: `plan_check`（引数を 1 つ渡すだけ） |
| `plugins/ndf/scripts/supervise_lib/verify_steps.py` | モジュール | 変更: `without_ci_wait`・`trim_check_steps` |
| `scripts/check-required-checks.py` | モジュール・型 `Declaration` | 変更: `read_declaration`・`compare`・`Declaration` |

## 関数の仕様

型の略記: `Repo` は `owner/name` の文字列、`Sha` は 40 桁の 16 進の文字列、`Root` は worktree の `Path`、`Args` は `argparse.Namespace`。

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `parse_trail` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(body: str) -> Sha \| None` | コメントの本文から、証跡の印と `承認したコミット:` の 40 桁を 1 つだけ読む | 無し | 印が無い・承認資料の印がある・40 桁が 0 か 2 つ以上なら `None`。それ以外は小文字の 40 桁 | 無し |
| `trails_of` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(comments: list[dict]) -> list[Trail]` | コメントの並びから証跡を `Trail`（SHA・`user.login`・`created_at`・編集の有無・コメントの番号）へ変える | 各要素は issue のコメントの API の形 | `parse_trail` が `None` のコメントは含まない。`updated_at != created_at` は `edited=True`。順序は `created_at` の昇順 | 無し |
| `judge` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(trails: list[Trail], roles: dict[str, str \| None], head: Sha) -> Judgment` | 承認権限を持つ人の最新の、編集されていない証跡と先端を照らす | `roles` は証跡を書いた人ごとの `role_name`（読めなければ `None`） | `Judgment.state` は `success` / `failure`。説明は 140 字以下。`success` は I1 を満たすときだけ | 無し |
| `approved_review_commit` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(reviews: list[dict], perms: dict[str, str \| None]) -> Sha \| None` | レビューの並びから、必須レビューの承認者として有効な人の最新の `APPROVED` のレビューの `commit_id` を返す | 各要素は PR のレビューの API の形。`perms` はレビューを書いた人ごとの `permission`（読めなければ `None`） | `perms` が `admin` か `write` の人の `APPROVED` だけを数え、無ければ `None`。`submitted_at` の最大のもの | 無し |
| `render_trail` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(sha: Sha, by: str) -> str` | 設計文書の「証跡のコメントの書式」の本文を組む | `by` は `user` か `mvv` | `parse_trail(render_trail(s, b)) == s` | 無し |
| `render_request` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(material: str, head: Sha) -> str` | 承認資料のコメントの本文（承認資料の印・先端の行・承認資料の本文）を組む | 無し | `parse_trail` は `None` を返す | 無し |
| `read_comments` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, pr: int) -> list[dict]` | PR のコメントをすべてのページで読む | `gh` が認証済み | 全ページを読んだ並び | ネットワーク（`GET repos/{repo}/issues/{pr}/comments`） |
| `read_roles` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, logins: list[str]) -> dict[str, str \| None]` | 書いた人ごとに共同作業者の `role_name` を読む | 無し | 読めない人は `None`。同じ人は 1 度だけ読む | ネットワーク（`GET repos/{repo}/collaborators/{login}/permission`） |
| `read_permissions` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, logins: list[str]) -> dict[str, str \| None]` | レビューを書いた人ごとに共同作業者の `permission` を読む | 無し | 読めない人は `None`。同じ人は 1 度だけ読む | ネットワーク（`GET repos/{repo}/collaborators/{login}/permission`。`read_roles` と同じ応答の別の欄） |
| `read_head` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, pr: int) -> tuple[Sha, str, str]` | PR の先端の SHA・宛先のブランチ・状態を読む | 無し | 3 つとも空でない | ネットワーク（`GET repos/{repo}/pulls/{pr}`） |
| `read_reviews` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, pr: int) -> list[dict]` | PR のレビューをすべてのページで読む | 無し | 全ページを読んだ並び | ネットワーク（`GET repos/{repo}/pulls/{pr}/reviews`） |
| `open_prs_at` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, sha: Sha, base: str) -> list[int]` | `base` 宛ての開いた PR のうち、先端が `sha` のものの番号を返す | 無し | 全ページを読み、`head.sha` が `sha` のものだけを昇順で返す | ネットワーク（`GET repos/{repo}/pulls?state=open&base={base}`） |
| `post_status` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, sha: Sha, j: Judgment) -> None` | 先端へ `ndf/release-approval` の commit status を付ける | 呼び手のトークンに `statuses: write` がある | 同じ SHA と名前の最新の状態が `j.state` になる | ネットワーク（`POST repos/{repo}/statuses/{sha}`） |
| `write_trail` | `plugins/ndf/scripts/release_lib/approval_trail.py` | 新設 | `(repo: Repo, pr: int, sha: Sha, by: str) -> tuple[bool, str]` | 先端が `sha` のときだけ証跡のコメントを置く。同じ人の同じ SHA の編集されていない証跡があれば置かない | 呼び手は利用者の `gh` の認証で動く | 戻り値の 1 つ目は置いたか、2 つ目はコメントの URL。先端が違えば `TrailError("head_moved")` | ネットワーク（PR とコメントの読み・`gh_rest.comment`・`gh_rest.viewer_login`） |
| `required_approvals` | `plugins/ndf/scripts/lib/release_case.py` | 新設 | `(rules: list[dict]) -> int \| None` | 規則の配列の `pull_request` 規則から承認数を読む | `rules` は `rules/branches/<branch>` の応答 | 規則が無ければ `None`。複数あれば最大の承認数 | 無し |
| `decide` | `plugins/ndf/scripts/lib/release_case.py` | 新設 | `(has_cd: bool, approvals: int \| None, why: str) -> Case` | CD の有無と承認数から 3 つの場合のどれかを返す | 無し | 判定表（処理の手順）のとおり。`Case.reason` は 1 行 | 無し |
| `read_approvals` | `plugins/ndf/scripts/lib/release_case.py` | 新設 | `(root: Root, branch: str) -> tuple[int \| None, str]` | 本番チャネルの ruleset を読み、規則が無ければ従来のブランチ保護を読んで承認数を返す | 無し | 読めなければ `(None, 読めなかった理由)` | ネットワーク（`gh api` を 2 回まで） |
| `case_of` | `plugins/ndf/scripts/lib/release_case.py` | 新設 | `(root: Root) -> Case` | 宣言と GitHub の設定から場合を決める | 無し | CD の行が無ければネットワークを使わずに場合 3 | ファイル（`.ndf/project.json`・`.ndf/worktree.json`）・ネットワーク |
| `cmd_approval_check` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(a: Args) -> NoReturn` | PR の証跡を判定し、先端へ commit status を付けて結果を出す | `--pr` がある | 宛先が本番チャネルでなければ何も付けずに 0。`open_prs_at` で同じ先端の開いた PR を読み、判定する PR のほかにあれば `failure`（I10）。`success` を付けたときは、付けた後に `open_prs_at` と証跡を読み直して判定し直し、`failure` になれば付け直す（`failure` は読み直さない）。PR が閉じていれば、同じ先端の開いた PR が 1 本だけのときにその PR を判定し、0 本なら何も付けない。判定できれば `success` / `failure` を付けて 0 | ネットワーク（読み・commit status） |
| `cmd_approval_post` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(a: Args) -> NoReturn` | 承認資料を承認資料のコメントとして PR に置く | `--approval` のファイルがあり、その「承認したコミット」が PR の先端と一致する | 同じ先端・同じ本文の承認資料のコメントが 1 件だけある | ファイルの読み・ネットワーク |
| `cmd_release_case` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(a: Args) -> NoReturn` | `case_of` の結果を `metrics` に出す | 無し | `metrics` に `case`・`approval_place`・`merger`・`reason` | ネットワーク（`case_of` 経由） |
| `cmd_release` | `plugins/ndf/scripts/release-steps.py` | 変更 | `(a: Args) -> NoReturn` | `--channel` と場合で経路を選ぶ | 今と同じ | 場合 3 と宣言の無い開発版は今の振る舞いのまま | 今と同じ（git・gh） |
| `_release_prod_reviewed` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(root: Root, a: Args, case: Case, plugin: str, plugins: list[str], branch: str, base: str, prod: str) -> NoReturn` | 場合 1・2 の本番の経路（開発版の PR のマージ・本番の PR・CI の待ち・承認の照合・迂回なしのマージ・公開の後）を通す | worktreeが `release/v<版>` で、版上げがコミット済み | 承認が先端と一致したときだけ本番の PR がマージ済み。一致しなければ 10 | git の push・ネットワーク（PR の作成・待ち・マージ） |
| `_wait_ci_except` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(root: Root, n: int, skip: tuple[str, ...], timeout: float) -> None` | `skip` の名前を除く PR のチェックが通るまで待つ | 無し | 戻れば、`skip` 以外のチェックがすべて `pass` か `skipping` | ネットワーク（`gh pr checks` の読み直し） |
| `_wait_trail_status` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(root: Root, repo: Repo, prod: str, head: Sha, timeout: float) -> None` | `ndf/release-approval` が本番チャネルの必須チェックにあるとき、先端のその状態が `success` になるまで待つ | 証跡を置いた後に呼ぶ | 必須でなければすぐ戻る。戻れば先端の状態は `success` | ネットワーク（規則・commit status の読み） |
| `_release_dev_by_workflow` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(root: Root, a: Args, workflow: str, pr: int) -> str` | 開発版の workflow を起動し、その実行の結論を返す | 開発版の PR が開いている | 戻り値は `success`。それ以外の結論は例外 | ネットワーク（`gh workflow run`・`gh run list`・`gh run view`） |
| `wait_and_merge` | `plugins/ndf/scripts/release-steps.py` | 変更 | `(root, n, expect=None, ci_wait=3600.0, gate_approved=None, no_admin=False) -> Sha \| None` | 今の待ちとマージに、迂回なしの指定を足す | 今と同じ | `no_admin` なら `merge-when-green` へ `--no-admin` を渡す | 今と同じ |
| `cmd_approve` | `plugins/ndf/scripts/release-steps.py` | 変更 | `(a: Args) -> NoReturn` | `--pr` があれば `approve_on_pr`、無ければ今の `approve` を呼ぶ | 今と同じ | `approval.Gate` は 10 で出す | 今と同じ |
| `approved_of` | `plugins/ndf/scripts/release_lib/approval.py` | 変更 | `(root, a, plugin, case: Case \| None = None) -> Sha \| None` | 場合 1・2 では承認したコミットの引数を拒む | 今と同じ | 場合 1・2 で `--approved-sha` か `--approval` があれば `StepError`（3）。無ければ `None` | 無し |
| `approved_on_pr` | `plugins/ndf/scripts/release_lib/approval.py` | 新設 | `(repo: Repo, pr: int, head: Sha, case: Case) -> Judgment` | 場合 1 は証跡、場合 2 は最新の承認のレビューで先端を照らす | `case` は場合 1 か 2 | 一致したときだけ `state == "success"` | ネットワーク（読み） |
| `approve` | `plugins/ndf/scripts/release_lib/approval.py` | 変更 | `(root, a) -> dict` | `--pr` があれば `approve_on_pr` へ委ね、無ければ今のまま承認資料へ記録する | 今と同じ | 今と同じ | 今と同じ |
| `approve_on_pr` | `plugins/ndf/scripts/release_lib/approval.py` | 新設 | `(root: Root, a: Args) -> dict` | 承認したコミットを決め（`--approved-sha` か `--facts-from`）、`write_trail` で証跡を置いた結果を返す | `--pr` と `--by` がある | 証跡が 1 件ある。先端が違えば `Gate`（10） | ネットワーク（`write_trail` 経由）・ファイルの読み（`--facts-from`） |
| `release_selected` | `plugins/ndf/scripts/release_lib/promotion.py` | 変更 | `(root, a, plugin, plugins, branch, base, prod, approved, ops, case: Case \| None = None) -> tuple` | 場合 1・2 では `_merge_exact`、場合 3 では今の `_merge_approved` でマージする | 今と同じ | 場合 1・2 で許可リストの許容と先端の入れ直しを使わない | 今と同じ |
| `_merge_exact` | `plugins/ndf/scripts/release_lib/promotion.py` | 新設 | `(root, a, plugin, branch, main_pr, case, items, metrics, ops) -> tuple[Sha, Judgment]` | 先端と承認の完全な一致を確かめ、迂回なしで 1 回だけマージする | 本番の PR が開いている | 一致しなければ 10。先端が動けば 10 | ネットワーク（読み・マージ） |
| `add_wait_args` | `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | `(m) -> None` | `--no-admin` を足す | 今と同じ | `a.no_admin` が既定 `False` で入る | 無し |
| `_merge_pr` | `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | `(root, a, n, watch, items, waits) -> None` | `a.no_admin` なら `gh pr merge` に `--admin` を付けない | 今と同じ | 失敗の文に迂回の有無を出す | 外部コマンド（`gh pr merge`） |
| `_trail_before_merge` | `plugins/ndf/scripts/merged_lib/merge.py` | 新設 | `(root: Root, a: Args, n: int, case: Case) -> None` | promote で、場合 1 なら照合した先端の証跡を置き、場合 2 ならレビューの承認と先端を照らす | `a.expect_head` が承認したコミット | 場合 1 は証跡が 1 件ある。場合 2 で一致しなければ 10 で止まる | ネットワーク |
| `promote` | `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | `(a, cleanup) -> NoReturn` | 場合 1・2 では `_trail_before_merge` の後に `no_admin=True` でマージする | 今と同じ | 場合 3 は今のまま | 今と同じ |
| `_reviewed_prod_steps` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 新設 | `(a, plugin: str, v: str, prs: str, repo: str \| None, approval: str, case: Case) -> list[dict]` | 場合 1・2 の本番のプランのステップを並べる | `a.channel == "prod"` | 並びは「処理の手順」の表のとおり | 無し |
| `_approve_pr_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 新設 | `(a, repo: str \| None, v: str, by: str) -> dict` | 本番の PR の番号を `gh pr list` で引き、`approve --pr` を打つステップを作る | 無し | `gate_next` は `end` | 無し |
| `_approval_post_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 新設 | `(a, repo: str \| None, v: str, approval: str) -> dict` | 承認資料を本番の PR へ置くステップを作る | 無し | `next` は `end`（承認ゲートで止まる） | 無し |
| `plan_release_package_plugin` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `(a) -> dict` | 本番で場合 1・2 なら `_reviewed_prod_steps` を使い、開発版では場合 1・2 のとき承認資料のステップを置かない | 今と同じ | 場合 3 のプランは今と同じ | ネットワーク（`case_of` 経由） |
| `_release_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `(a, v, dev, repo, approval, case: Case \| None = None) -> dict` | 場合 1・2 の本番では `--approval` を渡さない | 今と同じ | 場合 3 は今のまま | 無し |
| `without_ci_wait` | `plugins/ndf/scripts/supervise_lib/verify_steps.py` | 変更 | `(a, steps, after_whole, drop=(), merge_fail=None, force=False) -> list[dict]` | `force` でも全体テストのステップを外す | 今と同じ | `whole_on_ci(a) or force` のときだけ外す | 無し |
| `trim_check_steps` | `plugins/ndf/scripts/supervise_lib/verify_steps.py` | 変更 | `(a, steps, after_whole, refactor_off, cd_case=False) -> list[dict]` | `cd_case` を `without_ci_wait` の `force` へ渡す | 今と同じ | 今と同じ | 無し |
| `read_declaration` | `scripts/check-required-checks.py` | 変更 | `(path: Path) -> Declaration` | `statuses`（`name`・`workflow`・`reason`）を読む | 今と同じ | `Declaration.statuses` は空の並びが既定 | ファイルの読み |
| `compare` | `scripts/check-required-checks.py` | 変更 | `(names, undetermined, required, decl, allow_rel, root: Path) -> list[Finding]` | 宣言した commit status の名前を、ジョブの名前と同じく既知の必須として数える | 今と同じ | `statuses` の名前は「消えた必須」に出ない。`workflow` のファイルが無い宣言は「古い宣言」 | ファイルの存在の確認 |

`plan_promote`（`delivery_templates.py`）と `plan_check`（`templates.py`）は、引数を 1 つ足すか渡すだけである。
上の 3 つ（シグネチャ・責務・失敗の扱い）は変えない。`plan_promote` は場合 1・2 なら promote のコマンドへ `--no-admin` を足し、`plan_check` は `case_of(root).case != "no-cd"` を
`trim_check_steps` の `cd_case` へ渡す。`merged-steps.py` の `build_parser` は説明の文だけを直す。

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_comments` | API の失敗 | 権限・ネットワーク・上限 | `gh_rest` の戻り値の `ok` | 呼び出し元へ返す（`TrailError("unreadable")`） | 呼び手が出す | 後始末なし。打ち直せる |
| `read_roles` | 1 人の権限を読めない | 共同作業者でない・トークンの権限 | API の 404 / 403 | 既定値で続ける（その人を `None`） | `judge` の説明に「権限を読めない <login>」 | 後始末なし。打ち直せる |
| `read_permissions` | 1 人の権限を読めない | 共同作業者でない・トークンの権限 | API の 404 / 403 | 既定値で続ける（その人を `None`。`approved_review_commit` はその人の承認を数えない） | 呼び手の説明に「権限を読めない <login>」 | 後始末なし。打ち直せる |
| `open_prs_at` | API の失敗 | 権限・ネットワーク・上限 | `gh_rest` の戻り値の `ok` | 呼び出し元へ返す（`TrailError("unreadable")`） | 呼び手が出す（証跡のチェックは `error`） | 後始末なし。打ち直せる |
| `read_head` | PR を読めない | 番号の誤り・ネットワーク | API の失敗 | 呼び出し元へ返す（`TrailError("unreadable")`） | 呼び手が出す | 打ち直せる |
| `read_reviews` | API の失敗 | 権限・ネットワーク | API の失敗 | 呼び出し元へ返す（`TrailError("unreadable")`） | 呼び手が出す | 打ち直せる |
| `post_status` | commit status を付けられない | `statuses: write` が無い・ネットワーク | API の失敗 | 呼び出し元へ返す（`TrailError("unreadable")`） | 呼び手が出す | 打ち直せる（同じ名前の最新が効く） |
| `write_trail` | 先端が `sha` と違う | 承認の後に push された | `read_head` の SHA との比較 | 呼び出し元へ返す（`TrailError("head_moved")`） | 呼び手が 10 で出す | 置かない。承認資料を作り直して承認を取り直す |
| `write_trail` | コメントを置けない | 権限・ネットワーク | `gh_rest.comment` の `ok` | 呼び出し元へ返す（`TrailError("unreadable")`） | 呼び手が 1 で出す | 打ち直せる（同じ証跡は置き直さない） |
| `read_approvals` | 規則もブランチ保護も読めない | トークンの権限・ネットワーク | `gh api` の終了コード | 既定値で続ける（`None` と理由） | `decide` が場合 1 にし、`reason` に読めなかった理由 | 後始末なし |
| `case_of` | 宣言を読めない | `.ndf/project.json` の形の誤り | `delivery.load_delivery` の `problems` | 呼び出し元へ返す（`CaseUnreadable`） | 呼び手が出す | 宣言を直して打ち直す |
| `cmd_approval_check` | 判定の途中で読めない | `TrailError("unreadable")` | `approval_trail` の関数 | 止める（先端へ `error` を付けてから） | `stopped`・終了コード 1。commit status は `error` | 打ち直せる（workflow の再実行） |
| `cmd_approval_check` | PR が本番チャネル宛てでない | develop 宛てなど | `read_head` の宛先 | 既定値で続ける（何も付けない） | `ok`・0・「宛先が本番チャネルでない」 | 後始末なし |
| `cmd_approval_post` | 承認資料のファイルが無い | 前のステップが書いていない | ファイルの読み | 止める | `stopped`・3 | 承認資料を作って打ち直す |
| `cmd_approval_post` | 承認資料の「承認したコミット」が PR の先端と違う | 資料を作った後に先端が動いた | `approved_commit.material_sha` との比較 | 置かずに止める | `stopped`・3 | 新しい先端で承認資料を作り直す |
| `cmd_approval_post` | コメントを置けない | 権限・ネットワーク | `gh_rest.comment` | 止める | `stopped`・1 | 打ち直せる（同じ先端なら置き直さない） |
| `cmd_release_case` | 宣言を読めない | `CaseUnreadable` | `case_of` | 止める | `stopped`・3 | 宣言を直して打ち直す |
| `cmd_release` | 場合 1・2 で承認したコミットの引数がある | 場合 3 の打ち方のまま | `approved_of` | 止める | `stopped`・3。`next` に `approve --pr` の打ち方 | 引数を外して打ち直す |
| `_release_prod_reviewed` | 承認が無いか先端と違う | 承認の前・承認の後の push | `approved_on_pr` | 止める（承認ゲート） | `gate`・10。`metrics.approved_sha` は先端、`metrics.main_pr` | 本番の PR は開いたまま残す。承認の後に打ち直す |
| `_release_prod_reviewed` | GitHub がマージを拒む | レビュー必須なのに場合 1 と判定した・必須チェックが落ちた | `wait_and_merge` の `StepError` | 止める | `stopped`・1。理由に GitHub の拒否の文 | 本番の PR は開いたまま。設定を直して打ち直す |
| `_release_prod_reviewed` | 先端が動いた | マージを待つ間の push | `wait_and_merge` の `HeadMoved` | 止める（承認ゲート） | `gate`・10 | 入れ直さない。承認を取り直す |
| `_wait_ci_except` | チェックが落ちた | CI の失敗 | `gh pr checks` の `fail` | 止める | `stopped`・1。落ちたチェックの名前 | 直して push し、打ち直す |
| `_wait_ci_except` | 上限まで終わらない | CI の基盤待ち | 上限の秒 | 止める | `stopped`・75 | 打ち直せる |
| `_wait_trail_status` | `failure` か `error` のまま上限 | 証跡のチェックの workflow が落ちた・権限を読めない | commit status の読み | 止める | `stopped`・1。commit status の説明の 1 行 | 証跡を置き直すか workflow を再実行して打ち直す |
| `_release_dev_by_workflow` | 起動できない | 宣言のファイル名の誤り・`actions: write` が無い | `gh workflow run` の終了コード | 止める | `stopped`・1 | 宣言を直して打ち直す |
| `_release_dev_by_workflow` | 実行を見つけられない | 起動の反映の遅れ | `gh run list` の `displayTitle` の照合 | 再試行（上限まで読み直す） | 上限で `stopped`・75 | 打ち直せる（開発版の PR がマージ済みなら起動しない） |
| `_release_dev_by_workflow` | 結論が `success` でない | CI の失敗・宛先や版の検査で落ちた | `gh run view` の `conclusion` | 止める | `stopped`・1。実行の URL | 直して打ち直す |
| `_release_dev_by_workflow` | 上限まで終わらない | CI の基盤待ち | 上限の秒 | 止める | `stopped`・75 | 打ち直せる |
| `approve_on_pr` | 承認したコミットを決められない | `--approved-sha` も `--facts-from` も無い・facts の出力が無い | 引数と `_facts_of` | 止める | `stopped`・2 | 引数を足して打ち直す |
| `approve_on_pr` | 先端が承認したコミットと違う | `TrailError("head_moved")` | `write_trail` | 止める（承認ゲート） | `gate`・10 | 承認資料を作り直す |
| `approved_on_pr` | 読めない | `TrailError("unreadable")` | `approval_trail` の関数 | 呼び出し元へ返す | 呼び手が 1 で出す | 打ち直せる |
| `_merge_exact` | 先端と承認が違う・先端が動いた | 承認の外のコミット | `approved_on_pr`・`HeadMoved` | 止める（承認ゲート） | `gate`・10 | 組み直して承認を取り直す |
| `_trail_before_merge` | 場合 2 でレビューの承認のコミットが先端と違う | 承認の後の push・未承認・有効な人（`permission` が `admin` か `write`）の承認が無い | `approved_review_commit` との比較 | 止める（承認ゲート） | `gate`・10 | レビューの承認をやり直す |
| `_trail_before_merge` | 証跡を置けない | `TrailError` | `write_trail` | 止める | `head_moved` は 10、ほかは `stopped`・1 | 打ち直せる |
| `_merge_pr` | `gh pr merge` が拒む | 必須チェック・必須レビュー（迂回なし） | 終了コード | 止める | `stopped`・1。「`--admin` なし」を文に出す | 今と同じ |
| `wait_and_merge` | `merge-when-green` が止まる | CI の失敗・衝突・先端の移動・GitHub の拒否 | 子のプロセスの終了コードと `items` | 呼び出し元へ返す（今と同じ `HeadMoved`・`Conflicted`・`StepError`） | 呼び手が出す | 今と同じ |
| `cmd_approve` | `approve_on_pr` か `approve` が止まる | 下の 2 行 | `approval.Gate`・`StepError` | 止める | `Gate` は `gate`・10、ほかは `StepError` の終了コード | 打ち直せる |
| `approve` | `--pr` の経路で止まる | `approve_on_pr` の行のとおり | `approve_on_pr` | 呼び出し元へ返す | 呼び手が出す | 打ち直せる。`--pr` の無い経路は今と同じ |
| `release_selected` | 場合 1・2 で照合が通らない | `_merge_exact` の行のとおり | `_merge_exact` | 止める（承認ゲート） | `gate`・10 | 組み直して承認を取り直す。場合 3 は今と同じ |
| `promote` | `release_case.case_of` が止まる | 宣言を読めない | `CaseUnreadable` | 止める | `stopped`・3 | 宣言を直して打ち直す |
| `plan_release_package_plugin` | 場合を決められない | 宣言を読めない（`CaseUnreadable`） | `case_of` | 呼び出し元へ返す（`DeclError` に包む） | プランの組み立てが今の宣言の誤りと同じ形で止まる | 宣言を直して組み直す。規則を読めないときは場合 1 で組む（`decide` の表） |
| `compare` | `statuses` の `workflow` のファイルが無い | 宣言が古い・ファイル名の誤り | ファイルの存在の確認 | 既定値で続ける（「古い宣言」の指摘を足す） | 終了コード 1 と指摘の行 | 宣言を直す |
| `read_declaration` | `statuses` の形が違う | 宣言の書き誤り | JSON の検査 | 止める（`Unreadable`） | 終了コード 2 | 宣言を直す |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `judge` | 証跡が 0 件 | `failure`・「証跡が無い」 |
| `judge` | 証跡はあるが、すべて編集済み | `failure`・「編集された証跡だけがある」 |
| `judge` | 編集されていない証跡はあるが、書いた人がすべて admin・maintain でない（読めない人を含む） | `failure`・「承認権限の無い <login> の証跡だけがある」か「権限を読めない <login>」 |
| `judge` | 承認権限を持つ人の最新の証跡（`created_at` の最大）の SHA ≠ 先端 | `failure`・「証跡 <短い SHA> ≠ 先端 <短い SHA>」 |
| `judge` | 承認権限を持つ人の最新の証跡の SHA = 先端 | `success`・「承認: <login> <created_at>」 |
| `decide` | CD の行が無い | 場合 3（`no-cd`）。承認を取る場所は手元の承認資料、マージの主体は NDF |
| `decide` | CD の行があり、承認数 ≥ 1 | 場合 2（`cd-review`）。承認を取る場所は PR のレビュー、マージの主体は利用者のアカウント |
| `decide` | CD の行があり、承認数 = 0 | 場合 1（`cd-no-review`）。承認を取る場所は証跡のコメント、マージの主体は利用者のアカウント |
| `decide` | CD の行があり、承認数を読めない | 場合 1。`reason` に読めなかった理由 |
| `read_approvals` | ruleset に `pull_request` 規則がある | その承認数 |
| `read_approvals` | ruleset を読めたが規則が無い | 従来のブランチ保護の `required_approving_review_count`。保護が無い（404）なら 0 |
| `read_approvals` | ruleset もブランチ保護も読めない | `None` |
| `cmd_release` | `--channel dev`、宣言 `release.dev_workflow` が無い | 今の `_merge_release_pr` |
| `cmd_release` | `--channel dev`、宣言がある | 開発版の PR を作るか使い回し、開いていれば `_release_dev_by_workflow`。マージ済みなら起動しない |
| `cmd_release` | `--channel prod`、場合 3 | 今の経路（承認資料の照合・許可リスト・`--admin`） |
| `cmd_release` | `--channel prod`、場合 1・2、`--promotion` が選んで集める | `promotion.release_selected(..., case)` |
| `cmd_release` | `--channel prod`、場合 1・2、それ以外 | `_release_prod_reviewed` |
| `_release_prod_reviewed` | 本番の PR がマージ済み | 再開として公開の後（`_published`）へ進む |
| `_release_prod_reviewed` | 本番の PR が無い | 開発版の PR をマージ → `release/v<版>` を戻す → 本番の PR を作る → `_wait_ci_except` → 10 |
| `_release_prod_reviewed` | 本番の PR があり、承認が先端と一致しない | `_wait_ci_except` → 10 |
| `_release_prod_reviewed` | 本番の PR があり、場合 1 で証跡が先端と一致 | `_wait_trail_status` → `wait_and_merge(expect=先端, no_admin=True)` → `_published` |
| `_release_prod_reviewed` | 本番の PR があり、場合 2 でレビューの承認のコミットが先端と一致 | `wait_and_merge(expect=先端, no_admin=True)` → `_published` |
| `approve_on_pr` | `--approved-sha` がある | その SHA で `write_trail` |
| `approve_on_pr` | `--facts-from` がある | facts の出力の `approved_sha` と、`--answer-from` の利用者の承認の記録を今の `approve_from_facts` と同じ規則で確かめてから `write_trail` |
| `promote` | 場合 3 | 今のまま（`--admin`） |
| `promote` | 場合 1・2、`--gate-approved` が無い | 今のまま承認ゲートで止まる（`gate_stop`） |
| `promote` | 場合 1・2、`--gate-approved` がある | `_trail_before_merge` → `merge_when_green`（`no_admin=True`） |

- 繰り返しの終わる条件: `_wait_ci_except` は `skip` 以外のチェックがすべて終わるか、上限の秒（`--ci-wait`）で終わる。`_wait_trail_status` は `success` を読むか、上限（`release-steps.trail-status`）で終わる。`_release_dev_by_workflow` は実行の `status == completed` か、上限（`release-steps.dev-workflow`）で終わる。`read_comments`・`read_reviews` は次のページが無くなるまで読む
- 計算量の注意: `read_roles` は証跡を書いた人の数だけ API を呼ぶ（同じ人は 1 度）。証跡を書く人は承認権限を持つ数人で、上限を設けない。commit status は SHA と名前ごとに 1000 件が上限である（STD3）。証跡のチェックは出来事ごとに 1 件を付けるため、同じ先端で 1000 回を超えて出来事が起きると付けられない。リリースの PR では起きない回数として扱う

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Trail` | 型（`NamedTuple`: `sha`・`login`・`created_at`・`edited`・`comment_id`） | `plugins/ndf/scripts/release_lib/approval_trail.py` | — |
| `Judgment` | 型（`NamedTuple`: `state`・`description`・`approver`・`at`） | `plugins/ndf/scripts/release_lib/approval_trail.py` | — |
| `TrailError` | 型（例外。`kind` は `head_moved` / `unreadable`） | `plugins/ndf/scripts/release_lib/approval_trail.py` | — |
| `TRAIL_MARK` | 定数 | `plugins/ndf/scripts/release_lib/approval_trail.py` | `<!-- ndf:release-approval -->` |
| `REQUEST_MARK` | 定数 | `plugins/ndf/scripts/release_lib/approval_trail.py` | `<!-- ndf:release-approval-request -->` |
| `STATUS_CONTEXT` | 定数 | `plugins/ndf/scripts/release_lib/approval_trail.py` | `ndf/release-approval`。`release-steps.py` と `merged_lib/merge.py` はここを読む |
| `APPROVER_ROLES` | 定数 | `plugins/ndf/scripts/release_lib/approval_trail.py` | `("admin", "maintain")` |
| `DESCRIPTION_LIMIT` | 定数 | `plugins/ndf/scripts/release_lib/approval_trail.py` | `140`（commit status の説明の長さの上限。超える分は切る） |
| `Case` | 型（`NamedTuple`: `case`・`approval_place`・`merger`・`reason`） | `plugins/ndf/scripts/lib/release_case.py` | — |
| `CD_NO_REVIEW` / `CD_REVIEW` / `NO_CD` | 定数 | `plugins/ndf/scripts/lib/release_case.py` | `cd-no-review` / `cd-review` / `no-cd` |
| `CaseUnreadable` | 型（例外） | `plugins/ndf/scripts/lib/release_case.py` | — |
| `release-steps.trail-status` | 設定値（`seconds`・`interval`） | `plugins/ndf/contracts/limits.json` | 600 秒・10 秒 |
| `release-steps.dev-workflow` | 設定値（`seconds`・`interval`） | `plugins/ndf/contracts/limits.json` | 3900 秒（CI の待ち 3600 秒に起動の遅れを足す）・15 秒 |
| `release.dev_workflow` | 設定値 | `.ndf/supervise.json` | ai-plugins は `release-dev.yml`。無ければ手元でマージする |
| `Declaration.statuses` | 型の項目（`name`・`workflow`・`reason` の並び） | `scripts/check-required-checks.py` | 空の並び |

- 開発版の workflow の実行は、`run-name: "release-dev #${{ inputs.pr }}"` の表示名で起動した実行を見分ける。表示名の形は `release-dev.yml` と
  `_release_dev_by_workflow` の 2 か所に出る。そのため `_release_dev_by_workflow` は workflow のファイルの `run-name` を読んで照合の文を作り、
  文字列を Python の側に持たない
- 証跡の書式の印と行の名前（`承認したコミット:`）は `approval_trail.py` だけが持つ。手順書は書式を写さず、このモジュールを指す

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 証跡のチェックの workflow の実行（PR の出来事とコメントの出来事が続けて起きる） | 先端の SHA への `ndf/release-approval` の commit status | `concurrency: group: release-approval-<PR 番号>`・`cancel-in-progress: true`。同じ PR の中では後の実行だけが残る。**PR をまたぐ実行は排他しない**（group を 1 つにすると、GitHub は同じ group で待つ実行を取り消すため、別の PR の判定が抜ける） | 同じ PR の中では、判定が毎回すべてのコメントを読み直すため、どの実行が最後でも同じ結果になる。PR をまたぐと、PR A の実行が PR B の出現前に読んだ `success` を、B の実行の `failure` の後に付けうる。そこで `success` を付けた実行は付けた後に読み直し、同じ先端の PR が増えていれば `failure` に付け直す。B の実行の `failure` より後に A が `success` を付けたなら、A の読み直しは B の出現より後になり、B を見て `failure` に戻す。`failure` は安全側のため読み直さない |
| 本番のプランの `approve-pr` と、同じ PR への手での証跡の書き込み | PR のコメント | 取らない | 同じ人・同じ SHA の編集されていない証跡があれば `write_trail` は置かない。別の人の証跡が増えても判定は「最新の承認権限を持つ人の証跡」で決まる |
| `release --channel prod` の打ち直し（承認ゲートの前と後） | 本番の PR・開発版の PR | 取らない（ステップの実行器が同じプランを 1 本だけ走らせる） | 本番の PR は `find_pr` で使い回す。マージ済みなら公開の後から続ける |
| `approval-post` の打ち直し | PR のコメント | 取らない | 同じ先端・同じ本文の承認資料のコメントがあれば置かない。先端が変わるか資料を作り直せば新しい承認資料のコメントを置く |
| 開発版の workflow と、同じ開発版の PR への手でのマージ | 開発版の PR | `concurrency: group: release-dev-<PR 番号>`・`cancel-in-progress: false` | `merge-when-green` はマージ済みの PR を `already_merged` で扱う。`_release_dev_by_workflow` はマージ済みなら起動しない |
| `release-case` を読む複数のプランの組み立て | GitHub の規則（読みだけ） | 取らない | 読みだけで状態を持たない。呼ぶたびに読み直す |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_approval_check` | 判定（`state`・説明・承認した人・時刻・先端）・数えなかった証跡の数と理由 | 結果 JSON（`lib/step_result.py`）・commit status の説明 | トークン。コメントの本文 |
| `cmd_approval_post` | 置いたか・コメントの URL・先端 | 結果 JSON | 承認資料の本文（ファイルのパスだけ） |
| `cmd_release_case` | `case`・`approval_place`・`merger`・`reason` | 結果 JSON の `metrics` | 規則の応答の全体 |
| `approve_on_pr` | 証跡のコメントの URL・承認したコミット・担い手 | 結果 JSON | トークン |
| `_release_prod_reviewed` | 本番の PR・先端・承認の照合の結果・マージのコミット・場合 | 結果 JSON の `items` と `metrics`（`case` を足す） | 認証情報 |
| `_release_dev_by_workflow` | 実行の URL・結論・待った秒 | 結果 JSON の `items` | 認証情報 |
| `_merge_pr` | マージの記録に `admin: false`（`--no-admin` のとき） | 結果 JSON の `items` | 認証情報 |
| `_trail_before_merge` | 証跡の URL か、レビューの承認のコミット | 結果 JSON の `items` | 認証情報 |
| `release-approval.yml` | `approval-check` の結果 JSON | Actions の実行のログ | `GH_TOKEN` の値（`env` で渡し、`echo` しない） |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `parse_trail` | `lib/approved_commit.py` の `parse_sha` | `grep -n "^def " plugins/ndf/scripts/lib/approved_commit.py` | 使う（40 桁の検査）。印と行の切り出しは無いため足す |
| `trails_of` | `lib/gh_checks.py` の `fold_latest` | `grep -n "^def " plugins/ndf/scripts/lib/gh_checks.py` | 使えない理由: チェックの実行の形（名前・開始の時刻）に合わせた畳み方で、コメントの作成者と編集の有無を持たない |
| `judge` | `lib/approved_commit.py` の `compare_approved` | `grep -n "def compare_approved" -A5 plugins/ndf/scripts/lib/approved_commit.py` | 使えない理由: 許可リストの許容と木の比較を含み、完全な一致だけを通す決定 7 と違う |
| `approved_review_commit` | `lib/gh_parts.py` の `review_post` | `grep -rn "reviews" plugins/ndf/scripts/lib/gh_parts.py` | 使えない理由: レビューを書く部品で、読む部品が無い |
| `render_trail` | `lib/approved_commit.py` の `material_row` | `sed -n 179,197p plugins/ndf/scripts/lib/approved_commit.py` | 使えない理由: 承認資料の表の行を組む部品で、コメントの書式と違う |
| `render_request` | `release_lib/materials.py` | `grep -n "^def " plugins/ndf/scripts/release_lib/materials.py` | 使えない理由: 承認資料の節を書く部品で、本文を包む印を持たない。承認資料の本文はファイルから読んでそのまま包む |
| `read_comments` | `lib/gh_rest.py` の `_rest_pages` | `grep -n "def _rest_pages" -A15 plugins/ndf/scripts/lib/gh_rest.py` | 使う（ページを辿る部品。私有の名前のため、公開の関数 `rest_pages` として出し直すか、同じモジュールに読みの関数を足す。どちらにするかは実装で決める） |
| `read_roles` | 無し | `grep -rn "collaborators/" plugins/ndf/scripts scripts` が 0 件 | 足す（`gh api` を `gh_call` で呼ぶ） |
| `read_permissions` | `read_roles`（この設計で足す） | 同じ API（`collaborators/{login}/permission`）の応答の別の欄を読むことを `gh api` の出力で確かめた | 応答を読む部品を `read_roles` と共有し、読む欄（`permission`）だけを分ける |
| `open_prs_at` | `lib/gh_rest.py` の `pr_list` | `grep -n "^def " plugins/ndf/scripts/lib/gh_rest.py` | 使う（`state=open` と宛先で絞り、先端の SHA の比較だけを足す） |
| `read_head` | `lib/gh_rest.py` の `view_json` | `grep -n "def view_json" plugins/ndf/scripts/lib/gh_rest.py` | 使う（`headRefOid,baseRefName,state`） |
| `read_reviews` | `lib/gh_rest.py` の `_rest_pages` | 上と同じ | 使う |
| `post_status` | 無し | `grep -rn "/statuses/" plugins/ndf/scripts scripts` が 0 件 | 足す（`gh api --method POST`） |
| `write_trail` | `lib/gh_rest.py` の `comment`・`viewer_login` | `grep -n "def comment\|def viewer_login" plugins/ndf/scripts/lib/gh_rest.py` | 使う |
| `required_approvals` | `lib/ci_workflows.py` の `required_contexts` | `grep -n "def required_contexts" -A20 plugins/ndf/scripts/lib/ci_workflows.py` | 使えない理由: 必須チェックの名前を読む部品で、`pull_request` 規則の承認数を読まない。同じモジュールの隣に置かず、場合の判定に閉じる |
| `decide` | `lib/delivery.py` の `publishes_by_cd`・`merge_rows` | `grep -n "def merge_rows\|def publishes_by_cd" -A4 plugins/ndf/scripts/lib/delivery.py` | `merge_rows` を使う（CD の行の有無）。`publishes_by_cd` はタグを作る CD に限るため使わない |
| `read_approvals` | `lib/design_pr_merge.py` の規則の読み・`scripts/check-required-checks.py` の `read_required` | `grep -rn "rules/branches" plugins/ndf/scripts scripts` | 使えない理由: どちらも必須チェックの名前だけを返す。`gh api` の呼び方（`{owner}/{repo}` の置き換え）は `design_pr_merge.py` に倣う |
| `case_of` | `lib/delivery.py` の `load_delivery` | 上と同じ | 使う |
| `cmd_approval_check` | `lib/step_result.py` の `result`・`emit` | `release-steps.py` の既存の副コマンド | 使う |
| `cmd_approval_post` | `lib/gh_rest.py` の `comment` | 上と同じ | 使う |
| `cmd_release_case` | `lib/step_result.py` | 上と同じ | 使う |
| `_release_prod_reviewed` | `release-steps.py` の `_merge_release_pr`・`find_pr`・`create_pr`・`wait_and_merge`・`_published`・`repo.restore_branch` | `grep -n "^def " plugins/ndf/scripts/release-steps.py` | 使う。今の本番の経路から承認資料の照合と許可リストを外した形 |
| `_wait_ci_except` | `merged_lib/checks.py` の `GreenWatch`・`release-steps.py` の `pr_check_buckets`・`lib/waits.py` の `wait_until` | `grep -n "class GreenWatch" -A30 plugins/ndf/scripts/merged_lib/checks.py` | `pr_check_buckets` と `wait_until` を使う。`GreenWatch` はマージを前提にした読み直しで、落ち続ける `ndf/release-approval` を外せないため使わない |
| `_wait_trail_status` | `lib/ci_workflows.py` の `required_contexts`・`lib/waits.py` の `wait_until` | 上と同じ | 使う |
| `_release_dev_by_workflow` | `lib/ci_watch.py` | `grep -n "^def " plugins/ndf/scripts/lib/ci_watch.py` | 使えない理由: PR のチェックを見る部品で、`workflow_dispatch` の実行を見ない。`gh run list` と `gh run view` を `wait_until` で読み直す |
| `approved_on_pr` | `release_lib/approval.py` の `check_approved` | `sed -n 105,115p plugins/ndf/scripts/release_lib/approval.py` | 使えない理由: 許可リストの許容を含む（決定 7）。判定は `approval_trail.judge` を使う |
| `approve_on_pr` | `release_lib/approval.py` の `_facts_of`・`approve_from_facts` の承認の記録の読み | `sed -n 173,262p plugins/ndf/scripts/release_lib/approval.py` | 使う（facts の出力と利用者の承認の記録の読み） |
| `_merge_exact` | `release_lib/promotion.py` の `_merge_approved` | `sed -n 113,126p plugins/ndf/scripts/release_lib/promotion.py` | 使えない理由: 許可リストで比べ直して入れ直す。同じ `ops.wait_and_merge` を 1 回だけ呼ぶ形で足す |
| `_trail_before_merge` | `release_lib/approval_trail.py` の `write_trail`・`read_permissions`・`approved_review_commit` | この設計の新設 | 使う |
| `_reviewed_prod_steps` | `release_templates.py` の `_prepare_steps`・`_dev_approval_steps`・`_release_verify_steps`・`advise_release_steps`・`mvv_gate_steps` | `grep -n "^def " plugins/ndf/scripts/supervise_lib/release_templates.py` | 使う。並びだけを変える |
| `_approve_pr_step` | `delivery_templates.py` の `_promote_steps_mvv` の `gh pr list` による PR の番号の引き方 | `sed -n 153,160p plugins/ndf/scripts/supervise_lib/delivery_templates.py` | 使う（同じ引き方） |
| `_approval_post_step` | `release_templates.py` の `material_path` | 上と同じ | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `parse_trail` | 印と 40 桁が 1 つで SHA を返す | 大文字の 16 進・前後の空白・バッククォートの有無 | 印が無い・承認資料の印がある・40 桁が 0 / 2 つ・39 桁で `None` |
| `trails_of` | 証跡だけを作成の順に返す | `updated_at == created_at` は編集なし、1 秒違えば編集あり | 承認資料のコメントと無関係のコメントを含まない |
| `judge` | admin の最新の証跡が先端で `success`。maintain でも `success` | 古い証跡が先端・新しい証跡が別の SHA なら `failure`。説明が 140 字で切れる | 証跡なし・write / triage・権限を読めない・編集済みでそれぞれ `failure` と理由 |
| `approved_review_commit` | 最新の `APPROVED` の `commit_id` | `APPROVED` の後に `COMMENTED` があっても `APPROVED` を返す。read・triage・読めない人の後の `APPROVED` は飛ばし、write の人の前の `APPROVED` を返す | `APPROVED` が無ければ `None`。有効な人の `APPROVED` が無ければ `None` |
| `render_trail` | `parse_trail` で同じ SHA に戻る | `by` が `mvv` | — |
| `render_request` | 先端の行に先端を持つ | — | `parse_trail` が `None`（証跡に数えない） |
| `read_comments` | 2 ページを結合する（`gh` を偽物にする） | 0 件 | API の失敗で `TrailError("unreadable")` |
| `read_roles` | 人ごとの `role_name` | 同じ人を 1 度だけ読む | 404 / 403 で `None` |
| `read_head` | 先端・宛先・状態 | — | API の失敗で `TrailError("unreadable")` |
| `read_reviews` | 2 ページを結合する | 0 件 | API の失敗で `TrailError("unreadable")` |
| `post_status` | 名前・状態・説明を渡す | — | API の失敗で `TrailError("unreadable")` |
| `write_trail` | 先端が一致して置く | 同じ人の同じ SHA の編集されていない証跡があれば置かない。編集済みなら置く | 先端が違えば `head_moved`。投稿の失敗で `unreadable` |
| `required_approvals` | `pull_request` 規則の承認数 | 規則が 2 つなら最大。0 | 規則が無ければ `None` |
| `decide` | 判定表の 4 行 | 承認数 0 と 1 の境 | 読めない（`None`）で場合 1 |
| `read_approvals` | ruleset の承認数 | ruleset に規則が無くブランチ保護の 404 で 0 | 両方読めなければ `None` と理由 |
| `case_of` | ai-plugins の宣言と承認数 0 で場合 1 | CD の行が無ければ `gh` を呼ばない | 宣言を読めなければ `CaseUnreadable` |
| `open_prs_at` | 同じ先端の PR の番号を返す | 先端の違う PR を含めない | 読めないとき `TrailError("unreadable")` |
| `cmd_approval_check` | 通る PR で `success` を付けて 0。`success` を付けた直後の読み直しで同じ先端の PR が増えていれば `failure` に付け直す | develop 宛てで何も付けずに 0 | 読めないとき `error` を付けて 1 |
| `cmd_approval_post` | 置いて 0 | 同じ先端・同じ本文の 2 回目は置かずに 0。資料を作り直せば置き直す | ファイルが無いか、資料の承認したコミットが先端と違えば 3。投稿の失敗で 1 |
| `cmd_release_case` | `metrics` の 4 項目 | — | 宣言を読めなければ 3 |
| `cmd_release` | 場合 3 の今のテストが通る | 宣言の無い開発版は手元でマージ | 場合 1 で `--approval` を渡すと 3 |
| `_release_prod_reviewed` | 場合 1 で証跡が一致してマージ・場合 2 でレビューが一致してマージ | 本番の PR がマージ済みなら公開の後から | 証跡が無い・承認の後の push で 10。GitHub の拒否で 1。`--admin` を渡さない |
| `_wait_ci_except` | `ndf/release-approval` が落ちていても他が通れば戻る | `skipping` を通過として扱う | 他のチェックの失敗で 1、上限で 75 |
| `_wait_trail_status` | 必須でなければすぐ戻る。`pending` の後に `success` で戻る | — | `failure` のまま上限で 1 |
| `_release_dev_by_workflow` | `success` で戻る | 開発版の PR がマージ済みなら起動しない | 起動の失敗で 1、結論の失敗で 1、上限で 75 |
| `approved_of` | 場合 3 は今のまま | 場合 1・2 で引数が無ければ `None` | 場合 1・2 で `--approved-sha` / `--approval` があれば 3 |
| `approved_on_pr` | 場合 1 は証跡、場合 2 はレビューで `success` | — | 読めないとき例外 |
| `approve_on_pr` | `--approved-sha` で置く。`--facts-from` で置く | 同じ証跡は置き直さない | 引数が無ければ 2。先端が違えば 10 |
| `release_selected` | 場合 3 は今のまま | — | 場合 1・2 で許可リストの下のコミットが足されると 10（受け入れ条件 13） |
| `_merge_exact` | 一致して `no_admin=True` でマージ | — | 不一致・先端の移動で 10 |
| `add_wait_args` | `--no-admin` を受ける | 既定は `False` | — |
| `_merge_pr` | `--no-admin` で `--admin` を付けない | 既定は今のまま `--admin` | 拒否の文に迂回なしを出す |
| `_trail_before_merge` | 場合 1 で証跡を置く・場合 2 で一致を確かめる | — | 場合 2 の不一致で 10。`head_moved` で 10 |
| `promote` | 場合 1・2 で `--admin` を渡さない | 場合 3 は今のまま | 場合 1 の証跡の失敗でマージしない |
| `_reviewed_prod_steps` | bump〜snapshot が承認ゲートより前、`approve-pr` とマージが後 | MVV 判定のプランで `approve-pr` が `--by mvv` | 場合 2 で `approve-pr` を置かない |
| `_approve_pr_step` | `gh pr list` で本番の PR の番号を引くコマンド | — | — |
| `_approval_post_step` | `next` が `end` | — | — |
| `plan_release_package_plugin` | 場合 3 の本番と開発版のプランが今と同じ | 場合 1・2 の開発版のプランに facts・explain・others が無い | — |
| `_release_step` | 場合 1・2 の本番で `--approval` を渡さない | 場合 3 は渡す | — |
| `without_ci_wait` | `force` で test-all を外す | `force` も CI の戦略も無ければそのまま | — |
| `trim_check_steps` | `cd_case` を渡せば test-all が無い | — | — |
| `read_declaration` | `statuses` を読む | `statuses` が無ければ空 | `name` / `workflow` / `reason` 以外のキーで 2 |
| `compare` | 宣言した commit status が必須にあって「消えた必須」に出ない | — | `workflow` のファイルが無ければ「古い宣言」、`reason` が空なら「理由の無い宣言」 |
