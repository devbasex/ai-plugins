# #3163 の詳細設計: PR の種類ごとに掛けるレビューと対象の差分を宣言で選ぶ

設計文書 [issues/issue-3163-design.md](issue-3163-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。語（レビューの単位・掛けるレビュー・衝突の解消の差分・衝突の解消のレビューの PR）と不変条件の番号（I1〜I12）は設計文書の「ドメインモデル」のものを使う。

読む前に知っておくと早い対応は次のとおりである。

| 説明文の語 | コードの値 |
| --- | --- |
| PR の種類（実装 PR・スプリントの PR・リリースブランチの PR・設計 PR・確定仕様化の PR） | `impl`・`sprint`・`release`・`design`・`spec` |
| 掛けるレビュー（cross-refactoring・cross-review・振る舞いの確認） | `cross-refactoring`・`cross-review`・`behavior` |
| 対象の差分（PR の差分全体・未検査の差分・衝突の解消の差分・掛けない） | `pr`・`unchecked`・`resolution`・`none` |
| 課題のまとまりの記録が有る / 無い | 引数の `a.bundles` が `None` でない / `None` |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/settings/worktree.json.json
├── skills/
│   ├── worktree/
│   │   ├── schemas/worktree.schema.json
│   │   └── references/declaration.md
│   └── release/references/release-steps.md
└── scripts/
    ├── lib/branch_flow.py
    ├── supervise_lib/
    │   ├── review_units.py *
    │   ├── sprint_bundles.py
    │   ├── sprint_waves.py
    │   ├── sprint.py
    │   ├── sprint_close_steps.py
    │   ├── release_flow.py
    │   └── release_templates.py
    ├── release_lib/review_range.py *
    ├── release-steps.py
    ├── project_lib/measure_branch_flow.py
    ├── project-decl.py
    └── tests/
        ├── test_branch_flow.py
        ├── test_sprint_branch_flow.py
        ├── test_review_units.py *
        ├── test_release_review_range.py *
        └── test_project_decl_branch_flow.py
docs/
├── glossary.md
└── glossary/glossary.json
```

依存の向きは一方向である。`lib/branch_flow.py` は値の表と読み分けだけを持ち、`supervise_lib`・`release_lib`・`project_lib` のどれも読まない。`supervise_lib/review_units.py` は `lib/branch_flow.py` と既存の `supervise_lib/templates.py`・`supervise_lib/paths.py`・`supervise_lib/decl.py` を読み、プランのテンプレート（`sprint_waves.py`・`sprint.py`・`sprint_close_steps.py`）がそれを呼ぶ。`release_lib/review_range.py` は `lib/branch_flow.py` と `lib/step_result.py` だけを読み、`release-steps.py` がそれを呼ぶ。`sprint_bundles.py` は関数を移した後もまとまりの読み出しだけを持ち、`review_units.py` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | レビューの単位の表・値オブジェクト・読み分けと組み合わせの検査（I1〜I4・I12）・スナップショットへの出し入れ・スキーマの断片を持つ | 無し（標準ライブラリと git） |
| `plugins/ndf/skills/worktree/schemas/worktree.schema.json` | 変更 | `branch_flow.reviews` の定義を `schema_fragment()` の出力に合わせる | `lib/branch_flow.py`（生成元） |
| `plugins/ndf/contracts/settings/worktree.json.json` | 変更 | `branch_flow` の要約にレビューの単位を足す | 無し |
| `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | 解いた行（`ReviewRow`）を受けて、プランへレビューのステップを差し込む・外す | `lib/branch_flow.py`・`supervise_lib/templates.py`・`supervise_lib/paths.py`・`supervise_lib/decl.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_bundles.py` | 変更 | `with_review`・`without_review`・`_redirect` を `review_units.py` へ移し、まとまりの読み出しと設計への受け渡しだけを残す | `supervise_lib/absorb.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | 実装・検査・pace `fast` の検査・設計の各プランを組む所で `review_units` を呼ぶ | `supervise_lib/review_units.py` |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | pace `fast` のステージで、cross-review を選ばなければ実装レビューのステージを置かない | `supervise_lib/review_units.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_close_steps.py` | 変更 | 確定仕様化の PR に、選んだときだけ cross-review のステップを置く | `supervise_lib/review_units.py` |
| `plugins/ndf/scripts/supervise_lib/release_flow.py` | 変更 | 開発版のデプロイのプランへ置く `review-open`・`review`・`review-close` のステップを組む | `lib/branch_flow.py`・`supervise_lib/templates.py` |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `release-branch` と `facts` の間にレビューのステップを置き、本番のリリースのプランの後片付けで一時のブランチを消す | `supervise_lib/release_flow.py` |
| `plugins/ndf/scripts/release_lib/review_range.py` | 新設 | 衝突の解消の差分を取り出し、レビューの PR を組む・閉じる・片付ける | `lib/branch_flow.py`・`lib/step_result.py` |
| `plugins/ndf/scripts/release-steps.py` | 変更 | サブコマンド `review-open`・`review-close` の引数を受け、`review_range` を呼んで結果 JSON を出す | `release_lib/review_range.py` |
| `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | 変更 | PR の種類ごとにレビューの印を数えてレビューの単位を推し、宣言とのずれを出す | `lib/branch_flow.py` |
| `plugins/ndf/scripts/project-decl.py` | 変更 | `drift` の結果へレビューの単位のずれの行を足す（`measure` は `measure_branch_flow` の出力をそのまま載せる） | `project_lib/measure_branch_flow.py` |
| `plugins/ndf/skills/worktree/references/declaration.md` | 変更 | レビューの単位の表・成り立たない組み合わせ・宣言の例 | 無し |
| `plugins/ndf/skills/release/references/release-steps.md` | 変更 | `review-open`・`review-close` の使い方と終了コード | 無し |
| `docs/glossary.md` | 変更 | 用語集の文書（`glossary.py render` で作り直す） | `docs/glossary/glossary.json` |
| `docs/glossary/glossary.json` | 変更 | 4 語（レビューの単位・衝突の解消の差分・衝突の解消のレビューの PR・掛けるレビュー） | 無し |
| `plugins/ndf/scripts/tests/test_branch_flow.py` | 変更 | 読み分け・組み合わせの検査・スナップショット・スキーマの一致 | `lib/branch_flow.py` |
| `plugins/ndf/scripts/tests/test_sprint_branch_flow.py` | 変更 | 宣言から組んだプランの `steps` と、打ち直しで変わらないこと | `supervise_lib/sprint.py` |
| `plugins/ndf/scripts/tests/test_review_units.py` | 新設 | ステップの差し込み・取り外しと行き先の付け替え | `supervise_lib/review_units.py` |
| `plugins/ndf/scripts/tests/test_release_review_range.py` | 新設 | 手元の git で組んだリリースブランチでの再マージの差分・範囲・片付け | `release_lib/review_range.py` |
| `plugins/ndf/scripts/tests/test_project_decl_branch_flow.py` | 変更 | 推した値・確からしさ・ずれの行 | `project_lib/measure_branch_flow.py` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/branch_flow.py` | `ReviewRow`（新設） | `runs`・`covers_impl`・`as_dict`（新設） |
| `plugins/ndf/scripts/lib/branch_flow.py` | `ReviewUnits`（新設） | `row`・`uncovered`・`overlaps`・`as_dict`（新設） |
| `plugins/ndf/scripts/lib/branch_flow.py` | `BranchFlow`（変更） | 属性 `reviews` を足す。`as_dict`・`resolve` は変えない |
| `plugins/ndf/scripts/lib/branch_flow.py` | `ResolvedFlow`（変更） | `as_snapshot`（変更） |
| `plugins/ndf/scripts/lib/branch_flow.py` | `FlowError`（変更） | `__init__`（変更: `because` を文面へ足す） |
| `plugins/ndf/scripts/lib/branch_flow.py` | モジュール | `parse_reviews`・`review_allowed`（新設）。`parse_flow`・`from_snapshot`・`schema_fragment`（変更） |
| `plugins/ndf/scripts/supervise_lib/review_units.py` | モジュール | `review_row`・`chosen_checks`・`insert_reviews`・`drop_unchosen`・`apply_impl`・`apply_check`・`apply_design`（新設）。`_redirect` は `sprint_bundles.py` から移す |
| `plugins/ndf/scripts/supervise_lib/sprint_bundles.py` | モジュール | `with_review`・`without_review`・`_redirect` を削除する |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | モジュール | `sprint_impl_plans`・`plan_sprint_check`・`plan_sprint_design`・`plan_fast_impl`・`plan_fast_check`（変更） |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | モジュール | `fast_sprint_plans`（変更） |
| `plugins/ndf/scripts/supervise_lib/sprint_close_steps.py` | モジュール | `_spec_pr_steps`（変更） |
| `plugins/ndf/scripts/supervise_lib/release_flow.py` | モジュール | `review_range_steps`（新設）。`release_branch_step`（変更: `next` を受ける） |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | モジュール | `plan_release_package_plugin`・`_cleanup_step`（変更） |
| `plugins/ndf/scripts/release_lib/review_range.py` | モジュール | `remerge_patch`・`build_review_pair`・`review_from`・`close_stale`・`open_review`・`close_review`（新設） |
| `plugins/ndf/scripts/release-steps.py` | モジュール | `cmd_review_open`・`cmd_review_close`（新設）。`build_parser`（変更） |
| `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | モジュール | `pr_kind`・`marks_of`・`infer_reviews`（新設）。`collect_flow`・`drift`（変更） |
| `plugins/ndf/scripts/project-decl.py` | モジュール | `cmd_drift` は呼ぶ関数を変えない（`mbf.drift` の行が増えるだけ）。名前だけを置く |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ReviewRow.runs` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `(self, check: str) -> bool` | 掛けるレビューの組にその値があるかを返す | `check` は `CHECKS` の値 | 戻り値は `check in self.checks` と等しい | 無し |
| `ReviewRow.covers_impl` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `(self) -> bool` | この行が実装の差分を cross-review で覆うかを返す | 行は I1・I2 を満たす | 真なのは `cross-review` を持ち、`diff` が `COVERING[kind]` と等しいときだけ | 無し |
| `ReviewUnits.row` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `(self, kind: str, bundled: bool) -> ReviewRow` | 種類の行を、書いていれば宣言から、無ければ既定値の表から解く | `kind` は `REVIEW_KINDS` の値 | 書いた種類は `bundled` に依らず同じ行を返す | 無し |
| `ReviewUnits.uncovered` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `(self) -> list[dict]` | まとまりの記録の有無の各場合で、実装の差分を覆う行が無ければ指摘の行を返す（I4） | 各行は I1〜I3 を満たす | 空なら両方の場合で 1 行以上が覆う | 無し |
| `ReviewUnits.overlaps` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `(self) -> list[dict]` | 書いた行と、書いていない種類の既定値の行が、同じ場合に実装の差分を覆えば指摘の行を返す（I12） | 各行は I1〜I3 を満たす | 空なら既定値による 2 回の cross-review は生じない | 無し |
| `parse_reviews` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `(value, promotion: Promotion) -> tuple[ReviewUnits, list[dict]]` | `reviews` の値を読み、形と組み合わせ（I1〜I4・I12）の指摘の行と一緒に返す | `promotion` は形を通った取り込み方の行 | 指摘が空のときだけ `ReviewUnits` は宣言の値を持つ。指摘があれば `ReviewUnits()` を返す | 無し |
| `review_allowed` | `plugins/ndf/scripts/lib/branch_flow.py` | 新設 | `() -> dict[str, dict]` | 種類 → 許す `checks`・`diff` と書ける条件の表を返す（スキーマ・測定の候補・文書の例の検査が読む） | 無し | `REVIEW_ALLOWED` のコピーで、呼ぶ側が書き換えても表は変わらない | 無し |
| `parse_flow` | `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `(value) -> BranchFlow` | 4 項目に加えて `reviews` を読み、すべての指摘を 1 つの `FlowError` で返す | 無し | 戻り値の `reviews` は I1〜I4・I12 を満たす | 無し |
| `FlowError.__init__` | `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `(self, items: list[dict])` | 指摘の行ごとに直すキー・値・許す値・理由（`because`）を 1 件 1 行の文面にする | 各行は `key`・`value`・`allowed` を持つ | `str(e)` が全行を含む | 無し |
| `ResolvedFlow.as_snapshot` | `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `(self, source: str) -> dict` | スナップショットに `reviews`（書いた種類だけ。無ければ `{}`）を足す | 無し | 戻り値は必ず `reviews` のキーを持つ（I6） | 無し |
| `from_snapshot` | `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `(snap: dict \| None, base, production) -> ResolvedFlow` | スナップショットの `reviews` を読み、無ければ `{}` として読む | 無し | 宣言のファイルを読まない | 無し |
| `schema_fragment` | `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `() -> dict` | `branch_flow` の定義に `reviews` を足す | 無し | 種類ごとの `checks` の `enum` と `diff` の `enum` が `REVIEW_ALLOWED` と一致する | 無し |
| `review_row` | `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | `(a, kind: str) -> ReviewRow` | 引数のスナップショット（`a.flow`）とまとまりの記録の有無から種類の行を解く | `apply_decls` か `pin_flow` が `a.flow` を置いた | 宣言のファイルを読まない | 無し |
| `chosen_checks` | `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | `(a, row: ReviewRow) -> tuple[str, ...]` | 行の掛けるレビューから、リファクタリングを止める条件（I7）に当たれば cross-refactoring を外した組を返す | 無し | 並びは常に `CHECKS` の順（リファクタリング → cross-review → 振る舞いの確認） | 無し |
| `insert_reviews` | `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | `(a, plan: dict, checks: tuple[str, ...]) -> dict` | 実装のプランの `ready` の前に、手元の送信 → 選んだレビューのステップ → 手元の早送りを置く | `plan["steps"]` が `ready` を持つ | `checks` が空なら `plan` を変えない。空でなければ `ready` へ向いていた遷移が `review-push` へ向く | 無し |
| `drop_unchosen` | `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | `(plan: dict, checks: tuple[str, ...]) -> dict` | 検査のプランから選ばないレビューのステップを外し、外したステップへの遷移を後ろのステップへ付け替える | 無し | 残ったステップの遷移は、外したステップの `id` を指さない | 無し |
| `apply_impl` | `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | `(a, plan: dict) -> dict` | 実装 PR の行を解いて `insert_reviews` を当てる | 無し | `diff` が `none` なら `plan` を変えない | 無し |
| `apply_check` | `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | `(a, plan: dict) -> dict` | スプリントの PR の行を解いて `drop_unchosen` を当てる | 無し | 宣言の無いリポジトリでは ndf v10.27.61 の `steps` と同じ並び（I5） | 無し |
| `apply_design` | `plugins/ndf/scripts/supervise_lib/review_units.py` | 新設 | `(a, plan: dict) -> dict` | 設計 PR の行が `none` なら設計のレビューのまとまりを外し、`review` への遷移を `requirements-recheck` へ付け替える | `plan` は `plan_sprint_design` の出力 | `pr` なら `plan` を変えない | 無し |
| `sprint_impl_plans` | `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | `(a, repo: str, pace: str \| None = None) -> dict` | まとまりの有無に依らず、各プランへ `apply_impl` を当てる | 無し | まとまりの記録が無く `impl` を書いていなければ、今と同じプラン | 無し |
| `plan_sprint_check` | `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | `(a, repo: str) -> dict` | `without_review` の代わりに `apply_check` を当てる | 無し | 同上（I5） | 無し |
| `plan_fast_check` | `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | `(a, repo, name, final=False, review_only=False) -> dict` | cross-refactoring を選ばなければ `review_only` として組み、`apply_check` を当てる | 無し | 検査の記録の `--review` の印と、置いたステップが一致する | 無し |
| `fast_sprint_plans` | `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | `(a) -> list[dict]` | `sprint` の行が cross-review を持たなければ実装レビューのステージを置かない | 無し | 宣言の無いリポジトリでは今と同じステージ | 無し |
| `_spec_pr_steps` | `plugins/ndf/scripts/supervise_lib/sprint_close_steps.py` | 変更 | `(a, refs: str, after: str) -> list[dict]` | `spec` の行が `pr` なら `pr` と `ready` の間に文書の cross-review（`spec-review`）を置く | 無し | `none` なら今と同じ並び | 無し |
| `review_range_steps` | `plugins/ndf/scripts/supervise_lib/release_flow.py` | 新設 | `(a, repo: str \| None, version: str, after: str) -> list[dict]` | `release` の行が `none` でなければ `review-open` → `review` → `review-close` のステップを返す | `version` は接尾辞を外した版 | `none` なら空のリスト | 無し |
| `release_branch_step` | `plugins/ndf/scripts/supervise_lib/release_flow.py` | 変更 | `(a, repo, version, units, next_id: str = "facts") -> dict` | 次のステップの `id` を引数で受ける | 無し | 既定では今と同じステップ | 無し |
| `plan_release_package_plugin` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `(a) -> dict` | 開発版で単位があれば、`release-branch` の後に `review_range_steps` を置き、judge の戻り先へ足す | 無し | `release` が `none` なら今と同じプラン | 無し |
| `_cleanup_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `(v: str, prs: str, repo: str \| None) -> dict` | 後片付けのコマンドへ、その版の衝突の解消のレビューの一時のブランチを消す処理を足す | 無し | ブランチが無ければ何もせず成功する | 無し |
| `remerge_patch` | `plugins/ndf/scripts/release_lib/review_range.py` | 新設 | `(root, frm: str, head: str) -> str` | `frm..head` の first-parent のマージの再マージの差分を 1 つのパッチにして返す | `frm` は `head` の祖先 | 衝突の無いマージだけなら空文字 | 外部コマンド（`git log`） |
| `build_review_pair` | `plugins/ndf/scripts/release_lib/review_range.py` | 新設 | `(root, head: str, patch: str) -> tuple[str, str]` | パッチを `head` へ逆に当てた土台のコミットと、その子で `head` と同じ tree の見せるコミットを作る | `patch` は空でない | 土台と見せるコミットの差分は `patch` と等しい。呼び手の作業ツリーに触れない | ファイル（一時の作業ツリー）・外部コマンド（`git worktree`・`git apply`・`git commit-tree`） |
| `review_from` | `plugins/ndf/scripts/release_lib/review_range.py` | 新設 | `(root, version: str, head: str, production: str) -> str` | 範囲の始まり（前回のレビュー済みの先頭か本番チャネル）を決める | `git fetch` の後 | 戻り値は `head` の祖先 | ネットワーク（`gh pr list`）・外部コマンド（`git merge-base`） |
| `close_stale` | `plugins/ndf/scripts/release_lib/review_range.py` | 新設 | `(root) -> list[dict]` | 開いている衝突の解消のレビューの PR を版に依らず閉じ、一時のブランチを消す（I11） | 無し | head が `REVIEW_PREFIX` で始まる開いた PR が 0 件 | ネットワーク（`gh pr close`・`git push --delete`） |
| `open_review` | `plugins/ndf/scripts/release_lib/review_range.py` | 新設 | `(root, version: str, diff: str, production: str) -> dict` | 片付け・範囲・差分・ブランチの送信・PR の作成を順に行い、結果の欄を返す | `diff` は `pr` か `resolution` | 戻り値の `review_pr` が開いた PR の番号。差分が空なら `review_pr` は `None` | ネットワーク（`git push`・`gh pr create`）・外部コマンド（`git`） |
| `close_review` | `plugins/ndf/scripts/release_lib/review_range.py` | 新設 | `(root, version: str) -> dict` | レビューの PR の直しのコミットをリリースブランチへ移して送り、レビュー済みの先頭を本文へ書き、PR を閉じて一時のブランチを消す | その版のレビューの PR が開いている | 成功したらリリースブランチの先頭が直しをすべて含む（I9） | ファイル（一時の作業ツリー）・ネットワーク（`git push`・`gh pr edit`・`gh pr close`） |
| `cmd_review_open` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(a) -> None` | 引数を `open_review` へ渡し、結果 JSON の `metrics.review_pr` に番号を出す | 無し | 差分が空なら終了コード 3 | ネットワーク（`open_review` を通して） |
| `cmd_review_close` | `plugins/ndf/scripts/release-steps.py` | 新設 | `(a) -> None` | 引数を `close_review` へ渡し、結果 JSON を出す | 無し | 失敗なら終了コード 1 で、PR と一時のブランチを残す | ネットワーク（`close_review` を通して） |
| `pr_kind` | `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | 新設 | `(head: str, base_ref: str, base: str, production: str) -> str \| None` | PR の head と宛先から PR の種類を決める | 無し | 当たらなければ `None` | 無し |
| `marks_of` | `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | 新設 | `(gh, number: int) -> frozenset[str]` | PR のレビューとコメントから、掛けたレビューの印の組を返す | `gh` は `measure_ci.Gh` | 戻り値は `CHECKS` の部分集合 | ネットワーク（`gh api` を 2 回） |
| `infer_reviews` | `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | 新設 | `(samples: dict[str, list[frozenset]], release_marks: dict[str, int]) -> dict` | 種類ごとの印の組から、掛けるレビューと対象の差分と確からしさを推す | 無し | 標本が `MIN_SAMPLES` に満たない種類は `unknown` と理由 | 無し |
| `collect_flow` | `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | 変更 | `(root, gh, base: str, production: str) -> dict` | 4 項目の推定に加えて、種類ごとに最大 `REVIEW_SAMPLES` 件の PR の印を集め、項目の `reviews` に推定を置く | 無し | `reviews` の推定は `write` の自動の書き出しへ渡さない（4 項目の `proposal` に入れない） | ネットワーク（`gh api`）・外部コマンド（`git`） |
| `drift` | `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | 変更 | `(declared: BranchFlow, item: dict \| None) -> list[dict]` | 4 項目のずれに加えて、`reviews.<種類>` のずれの行を返す | 無し | 書いていない種類は既定値の 2 列のどちらとも違うときだけ行を出す | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `parse_reviews` | 形の誤り | `reviews` がオブジェクトでない・知らない種類・知らないキー・表に無い値・`checks` の重複（I1・受け入れ条件 7） | 値を読む所 | 呼び出し元へ返す（指摘の行） | `parse_flow` が `FlowError` にまとめる | 後始末なし。宣言を直して打ち直す |
| `parse_reviews` | 組み合わせの誤り | `diff: none` で `checks` が空でない・それ以外で空（I2）・取り込み方が単位を選ばないのに `release` を書いた（I3） | 形を通った行ごと | 呼び出し元へ返す（`because` に理由。I3 は `promotion = <行>`） | 同上 | 同上 |
| `parse_reviews` | レビューされない差分・重なり | `uncovered`・`overlaps` が行を返した（I4・I12） | 形と組み合わせが通った後 | 呼び出し元へ返す | 同上 | 同上 |
| `parse_flow` | 宣言の誤り | 4 項目か `reviews` に指摘がある | 全項目を読んだ後 | 止める（`FlowError`。既定値へ倒さない） | `supervise.py new sprint` は既存の `DeclError` の経路で 1 件 1 行の文面と終了コード 2。`project-decl.py check / write` は既存の `_declared_flow` の経路で終了コード 3 | プランも目録も書かない。宣言を直して打ち直す |
| `from_snapshot` | スナップショットの誤り | 目録を手で書き換えた | `parse_flow` | 止める（`FlowError`） | 既存の経路のまま | 目録を直すか、スプリントを始め直す |
| `ResolvedFlow.as_snapshot` | 無し（値を写すだけ） | — | — | — | — | — |
| `insert_reviews` | `ready` が無い | テンプレートの変更で実装のプランの並びが変わった | `ready` の位置を探す所 | 止める（`ValueError`。プログラムの誤り） | `supervise.py` が落ち、トレースバック | テストが落ちる形で見つかる。プランは書かない |
| `drop_unchosen` | 無し（無い `id` は外さないだけ） | — | — | — | — | — |
| `apply_design` | `requirements-recheck` が無い | テンプレートの変更 | 付け替えの前 | 止める（`ValueError`） | 同上 | 同上 |
| `remerge_patch` | git が失敗 | `frm` が無い・git が古い（`--remerge-diff` は 2.36 から） | `git log` の終了コード | 止める（`StepError`、コード 1） | `review-open` が終了コード 1 と git の標準エラーの末尾 | 何も作っていない。原因を直して打ち直せる |
| `build_review_pair` | 逆に当てられない | 範囲の後のマージかレビューの直しが同じ行を変えた | `git apply -R --check` の終了コード | 止める（`StepError`、コード 1。推定で縮めない。I8） | 終了コード 1 と当たらなかったファイルの一覧 | 一時の作業ツリーを消す。PR とブランチは作らない。人が範囲を見て決める |
| `review_from` | 本文を読めない | `gh` の失敗 | `gh pr list` の終了コード | 止める（`StepError`、コード 1） | 終了コード 1 | 何も作っていない。打ち直せる |
| `review_from` | 前回の先頭が祖先でない | リリースブランチを手で作り直した | `git merge-base --is-ancestor` | 既定値で続ける（本番チャネルから） | 結果の `items` に「前回の先頭が祖先でないため本番チャネルから取る」を出す | 範囲が広がるだけで、打ち直せる |
| `close_stale` | 閉じられない | `gh` の失敗・権限 | `gh pr close` の終了コード | 止める（`StepError`、コード 1） | 終了コード 1 と PR の番号 | 閉じた分はそのまま。打ち直すと残りを閉じる |
| `open_review` | 差分が空 | 範囲のマージがどれも衝突しない | `remerge_patch` が空文字 | 呼び出し元へ返す（`review_pr` が `None`） | `cmd_review_open` が終了コード 3（打たずに `facts` へ） | ブランチも PR も作らない |
| `open_review` | 送れない・PR を作れない | push の拒否・`gh pr create` の失敗 | `git push`・`create_pr` の終了コード | 止める（`StepError`、コード 1） | 終了コード 1。プランは `judge` で止まる | 送ったブランチは残る。次の `review-open` の `close_stale` が消す |
| `close_review` | 直しを移せない | cherry-pick の衝突・リリースブランチが動いた（push の拒否） | `git cherry-pick`・`git push` の終了コード | 止める（`StepError`、コード 1。I9） | 終了コード 1。リリースブランチは動かさない | PR と一時のブランチを残す（人が見る材料）。次の `review-open` が片付ける |
| `close_review` | 開いた PR が無い | 打ち直しで既に閉じた | `find_pr` が `None` | 止める（`StepError`、コード 1） | 終了コード 1 と「その版のレビューの PR が開いていない」 | 開発版のデプロイのプランを `review-open` から打ち直す |
| `cmd_review_open` | 引数の誤り | `--diff` に `pr`・`resolution` 以外 | argparse | 止める | 終了コード 2 | 何も作らない |
| `cmd_review_close` | 下の `close_review` の失敗 | 同上 | 同上 | 呼び出し元へ返す | 同上 | 同上 |
| `marks_of` | 読めない | API の失敗・締め切り | `Gh.get` の例外 | 既定値で続ける（その PR を標本から外す） | `reviews.<種類>` の根拠に「読めない PR n 件」 | 打ち直せる |
| `collect_flow` | `gh` を使えない | 認証・ネットワーク | `GhUnavailable` | 呼び出し元へ返す（今のまま `unknown`） | 項目が `unknown` と理由 | 打ち直せる |
| `drift` | 無し（測れない種類は比べない） | — | — | — | — | — |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `ReviewUnits.row` | 種類を書いた | 書いた行（`bundled` を見ない） |
| `ReviewUnits.row` | 書いていない・`bundled` が真 | 既定値の表の「まとまりの記録あり」の列 |
| `ReviewUnits.row` | 書いていない・`bundled` が偽 | 既定値の表の「まとまりの記録なし」の列 |
| `ReviewUnits.uncovered` | `bundled` の真偽それぞれで、`impl`・`sprint`・`release` の解いた行のどれも `covers_impl` が偽 | その場合ごとに 1 行（キーは `reviews`、理由は「まとまりの記録が有る / 無いとき、実装の差分がどの PR でも cross-review されない」） |
| `ReviewUnits.overlaps` | `bundled` の真偽それぞれで、書いた行の `covers_impl` が真で、書いていない種類の既定値の行の `covers_impl` も真 | 書いていない種類のキー（`reviews.<種類>`）と、重なる書いた行の種類を理由に 1 行 |
| `ReviewUnits.overlaps` | 重なる 2 行がどちらも書いた行 | 指摘しない（利用者の意図とする） |
| `parse_reviews` | 形の誤りがある | 組み合わせ・I4・I12 を見ずに形の指摘だけを返す |
| `parse_reviews` | 形は通り、I2・I3 の誤りがある | I4・I12 を見ずに返す（誤った行で覆いを数えない） |
| `chosen_checks` | `refactor_suspended(a)` が真か `a.mode` が `NO_REFACTOR_MODES` にある | `cross-refactoring` を外す（I7） |
| `insert_reviews` | `checks` が空 | プランを変えない |
| `insert_reviews` | `checks` に `cross-refactoring` | `_inspect_steps` の `assess`・`refactor` を置く。`assess` の `skip_to` と `refactor` の `on_exit` の行き先は、次に置くステップへ付け替える |
| `insert_reviews` | `checks` に `behavior` | `BEHAVIOR_STEPS` を置き、`base-sync` を指す行き先を `review-pull` へ付け替える |
| `drop_unchosen` | ステップの `id` が選ばない組の `STEP_IDS` にある | 並びの順に外し、外した `id` への遷移をそのステップの `next` へ付け替える（外した後続が続けば、その先まで順に付け替わる） |
| `plan_fast_check` | `review_only` が偽で、`sprint` の行が `cross-refactoring` を選ばない | `review_only=True` として組む（記録の印と実行の条件を揃える） |
| `fast_sprint_plans` | `sprint` の行が `cross-review` を選ばない | 「実装レビュー」のステージを置かない |
| `review_range_steps` | `release` の行が `none` | 空のリスト（`release-branch` の次は今と同じ `facts`） |
| `review_range_steps` | `pr` か `resolution` | `review-open`（`on_exit` で 3 は `facts`、`pr_from` は `review_pr`、失敗は `judge`）→ `review`（`review_step`。収束しなければ `judge`）→ `review-close`（失敗は `judge`）→ `facts` |
| `review_from` | 同じ版の閉じたレビューの PR があり、本文のレビュー済みの先頭が `head` の祖先 | その先頭から（I10） |
| `review_from` | 上が無い・祖先でない | `origin/<本番チャネル>` から |
| `open_review` | `diff` が `resolution` | `remerge_patch(from, head)` が空なら空で返す。空でなければ `build_review_pair` の土台と見せるコミットを送る |
| `open_review` | `diff` が `pr` | 土台を範囲の始まり、見せるコミットを `head` そのものにする（パッチを作らない）。`from` と `head` が等しければ空で返す |
| `close_review` | レビューの PR の head が見せるコミットから進んでいない | 直しなし。本文へレビュー済みの先頭（リリースブランチの先頭）を書いて閉じる |
| `close_review` | 進んでいる | 見せるコミット..head の直しを、リリースブランチの先頭へ順に cherry-pick して送る（`diff: pr` では早送りと同じ結果） |
| `pr_kind` | head が `feat/issue-` | `impl` |
| `pr_kind` | head が `sprint/` で宛先がベースブランチ | `sprint` |
| `pr_kind` | head が `release/v` で宛先が本番チャネル、または head が `REVIEW_PREFIX` | `release` |
| `pr_kind` | head が `design/` / `spec/` | `design` / `spec` |
| `infer_reviews` | 種類の標本が `MIN_SAMPLES` に満たない | `unknown` と「標本が n 件で 3 件に満たない」 |
| `infer_reviews` | 最も多い印の組の割合が `confidence` で値を返す | `checks` はその組。`diff` は組が空なら `none`、`release` は印のある PR の多い側（レビューの PR なら `resolution`、本番チャネル宛なら `pr`）、ほかは種類が許す `none` 以外の 1 値 |
| `drift` | 書いた種類で、推した値と違う | ずれの行（`branch_flow.reviews.<種類>`） |
| `drift` | 書いていない種類で、推した値が既定値の 2 列のどちらとも違う | ずれの行 |

- 繰り返しの終わる条件: `remerge_patch` と `close_review` の cherry-pick は範囲のコミットの数だけ回る。`measure` の PR の読み出しは種類ごとに `REVIEW_SAMPLES` 件で止まる。`drop_unchosen` は外す `id` の数だけ回る
- 計算量の注意: `measure` は種類ごとに最大 10 件 × 2 回の API 呼び出し（5 種類で最大 100 回）。締め切り（`deadline`）を越えたら残りを読まずに `unknown` とする。収まるかは設計文書の「未確認のまま残ること」にある
- 開始の後に宣言を壊れた形へ変えると、打ち直しは `apply_decls` の宣言の読み込み（ndf v10.27.61 の振る舞い）で止まる。正しい形へ変えた場合は、スナップショットの値で組み、宣言の値を使わない（I6）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `REVIEWS_KEY` | 定数 | `plugins/ndf/scripts/lib/branch_flow.py` | `"reviews"` |
| `REVIEW_KINDS` | 定数 | `plugins/ndf/scripts/lib/branch_flow.py` | `("impl", "sprint", "release", "design", "spec")` |
| `CHECKS` | 定数 | `plugins/ndf/scripts/lib/branch_flow.py` | `("cross-refactoring", "cross-review", "behavior")`。並びがステップを置く順の唯一の置き場 |
| `DIFFS` | 定数 | `plugins/ndf/scripts/lib/branch_flow.py` | `("pr", "unchecked", "resolution", "none")` |
| `REVIEW_ALLOWED` | 定数（種類 → 許す `checks`・`diff`・単位を選ぶ取り込み方を要るか） | `plugins/ndf/scripts/lib/branch_flow.py` | 設計文書の「PR の種類ごとに許す値」の表 |
| `REVIEW_DEFAULTS` | 定数（種類 → `{True: ReviewRow, False: ReviewRow}`） | `plugins/ndf/scripts/lib/branch_flow.py` | 設計文書の「既定値の表」 |
| `COVERING` | 定数（実装の差分を覆う種類 → `diff`） | `plugins/ndf/scripts/lib/branch_flow.py` | `{"impl": "pr", "sprint": "unchecked", "release": "pr"}` |
| `REVIEW_PREFIX` | 定数 | `plugins/ndf/scripts/lib/branch_flow.py` | `"review/release/v"`（見せるブランチ。土台のブランチは後ろに `REVIEW_BASE_SUFFIX`） |
| `REVIEW_BASE_SUFFIX` | 定数 | `plugins/ndf/scripts/lib/branch_flow.py` | `"-base"` |
| `ReviewRow` | 型（frozen dataclass: `kind`・`checks: tuple`・`diff`） | `plugins/ndf/scripts/lib/branch_flow.py` | — |
| `ReviewUnits` | 型（frozen dataclass: `declared: dict[str, ReviewRow]`） | `plugins/ndf/scripts/lib/branch_flow.py` | `declared={}` |
| `STEP_IDS` | 定数（掛けるレビュー → ステップの `id` の並び） | `plugins/ndf/scripts/supervise_lib/review_units.py` | `cross-refactoring`: `assess`・`refactor`、`cross-review`: `review`、`behavior`: `behavior-targets`・`behavior`・`behavior-verify` |
| `DESIGN_REVIEW_IDS` | 定数 | `plugins/ndf/scripts/supervise_lib/review_units.py` | `_review_steps` が置く `id`（`review`・`review-settled`・`resolve-sync`・`resolve`・`push-resolve`・`sync-review`）。テストで `_review_steps` の出力と一致を確かめる |
| `REVIEWED_MARK` | 定数 | `plugins/ndf/scripts/release_lib/review_range.py` | `"<!-- ndf:reviewed-head {sha} -->"`（レビューの PR の本文に書くレビュー済みの先頭） |
| `REVIEW_SAMPLES` | 定数 | `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | `10` |
| 印の目印 | 定数 | `plugins/ndf/scripts/project_lib/measure_branch_flow.py` | `## 🤖 cross-review`・`<!-- cross-refactoring plan`・`<!-- ndf:behavior-check`。書き手の各 Skill の定数を import して使い、文字列を写さない（import できない書き手は実装のときにその定数を 1 か所へ寄せる） |

目録のスナップショットの `reviews` は `ReviewUnits.as_dict()` の出力（書いた種類 → `{"checks": [...], "diff": ...}`）で、永続の形は設計文書の「目録のスナップショット」が持つ。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じスプリントで `new sprint` の打ち直しと動いているプラン | 目録（`sprint.json`） | 取らない。書くのは `cmd_new_sprint` だけで、`reviews` は開始時の値をそのまま書き直す | 打ち直しはスナップショットから同じ `steps` を組む（I6） |
| 開発版のデプロイのプランの打ち直し（`review-open` から） | 衝突の解消のレビューの PR・一時のブランチ | `review-open` の始めの `close_stale` が、開いているものを閉じてから作る | 冪等。版ごとに開いているのは 1 組まで（I11） |
| `review-close` とリリースブランチの組み直し（`release-branch`） | origin のリリースブランチ | 同じプランの中で順に走るため重ならない。別の操作が先に送れば `git push` が早送りでないとして拒む | 拒まれたら終了コード 1 で止まり、`release-branch` からの打ち直しで直しは残る（origin の先頭から積む。I9） |
| 本番のリリースのプランの `cleanup` と、次の版の `review-open` | 一時のブランチ | 消す側はブランチの有無を見てから消す（無ければ何もしない） | 冪等 |
| `build_review_pair`・`close_review` を 2 つ同時に打つ | 手元のリポジトリの作業ツリーの一覧 | 一時の作業ツリーを `tempfile.mkdtemp` で別のパスに作り、呼び手の作業ツリーに触れない | 中断で残った一時の作業ツリーは `git worktree prune` の対象（既存の `build_branch` と同じ扱い） |
| `project-decl.py measure` を 2 つ同時に打つ | 無し（読むだけ） | 不要 | 冪等 |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `parse_flow` | 直すキー・値・許す値・理由を 1 件 1 行 | 例外の文面（`supervise.py` の標準エラー・`project-decl.py` の結果 JSON） | 宣言のファイルのほかのキーの値 |
| `cmd_review_open` | 範囲（始まり・先頭の SHA）・差分のファイルの数・PR の番号（`metrics.review_pr`）・`close_stale` で閉じた PR | 結果 JSON（`step_result.emit`） | トークン・認証情報（`gh` の出力をそのまま載せない。標準エラーは末尾 400 文字だけ） |
| `cmd_review_close` | 移した直しのコミットの数・送った先頭・閉じた PR | 結果 JSON | 同上 |
| `review_range_steps` | ステップの `stage`（「デプロイ」） | 進捗記録（既存の駆動が書く） | 無し |
| `collect_flow` | `reviews.<種類>` の値・確からしさ・根拠（コマンドと件数） | 測定の結果 JSON | PR の本文とコメントの中身（印の有無だけを数え、既存の `mask` を通す） |
| `drift` | `branch_flow.reviews.<種類>` のずれの行 | 結果 JSON の `items` と標準出力（`print_drift`） | 同上 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `ReviewRow.runs` | `Promotion` の属性で振る舞いを持つ形 | `lib/branch_flow.py` を読んだ | 使う（同じ frozen dataclass の形に倣う） |
| `ReviewRow.covers_impl` | 同上 | 同上 | 使う（形だけ。判定は新しい） |
| `ReviewUnits.row` | `PROMOTIONS` の表引き | 同上 | 使う（表引きの形に倣い、既定値を `REVIEW_DEFAULTS` に置く） |
| `ReviewUnits.uncovered` | `parse_flow` の組み合わせの検査（`because` を持つ指摘の行） | 同上 | 使う（指摘の行の形をそのまま使う） |
| `ReviewUnits.overlaps` | 同上 | 同上 | 使う |
| `parse_reviews` | `parse_flow` の形の検査・jsonschema | `lib/branch_flow.py` を読んだ。`grep -n jsonschema pyproject.toml` が 0 件 | `parse_flow` の形に倣う。jsonschema は使えない（依存を増やし、I4・I12 を書けない。設計文書の LIB3） |
| `review_allowed` | `allowed_values` | `lib/branch_flow.py` を読んだ | 使う（同じ役割の関数を種類ごとの表へ広げる） |
| `review_row` | `decl.sprint_flow` | `grep -n "def sprint_flow" supervise_lib/decl.py` | 使う（スナップショットの読み出しはこれだけを通す） |
| `chosen_checks` | `templates.refactor_suspended`・`templates.NO_REFACTOR_MODES`・`verify_steps.trim_check_steps` | `grep -n "def trim_check_steps\|def refactor_suspended" supervise_lib/*.py` | 前の 2 つを使う。`trim_check_steps` は実装のプランに当てられない（全体テストのステップも外すため）。検査のプランでは今のまま `trim_check_steps` が外す |
| `insert_reviews` | `sprint_bundles.with_review`・`templates._inspect_steps`・`templates.review_step`・`paths.BEHAVIOR_STEPS` | `grep -n "def with_review\|def _inspect_steps\|def review_step\|BEHAVIOR_STEPS =" supervise_lib/*.py` | 使う（`with_review` を一般にしたもの。ステップの中身は既存の部品から作り、書き写さない） |
| `drop_unchosen` | `sprint_bundles.without_review`・`sprint_bundles._redirect` | `grep -n "def without_review\|def _redirect" supervise_lib/sprint_bundles.py` | 使う（`without_review` を一般にし、`_redirect` を移す） |
| `apply_impl` | `sprint_bundles.with_review` | 同上 | 使う（置き換える） |
| `apply_check` | `sprint_bundles.without_review` | 同上 | 使う（置き換える） |
| `apply_design` | `sprint_waves._review_steps` | `grep -n "def _review_steps" supervise_lib/sprint_waves.py` | 使う（外す `id` の並びをこの出力と照らす） |
| `review_range_steps` | `release_flow.release_branch_step`・`templates.review_step`・エンジンの `pr_from` | `grep -rn "pr_from" supervise_lib/engine.py supervise_lib/plan.py` | 使う（`pr_from` で `{pr}` を渡す。#1273） |
| `remerge_patch` | git の `--remerge-diff` | 設計文書の「再マージの差分の実測」 | 使う（設計文書の LIB1） |
| `build_review_pair` | `release_lib/promotion.build_branch` の一時の作業ツリー・`git commit-tree` | `grep -n "mkdtemp\|worktree" release_lib/promotion.py` | 使う（一時の作業ツリーの作り方と後始末を同じ形にする） |
| `review_from` | `release-steps.find_pr`・`branch_flow.ancestor_of` | `grep -n "def find_pr\|def ancestor_of" release-steps.py lib/branch_flow.py` | `ancestor_of` を使う。`find_pr` は head と宛先の組で探すため、版の閉じた PR を探す `gh pr list --head` を `step_result.gh_json` で打つ |
| `close_stale` | `merged-steps.py cleanup`（マージ済みの PR の作業ツリーとローカルブランチの片付け） | `grep -n "def cmd_cleanup" merged-steps.py` | 使えない（マージ済みだけを扱い、閉じた PR を扱わない）。`gh pr close --delete-branch` を使う |
| `open_review` | `release-steps.create_pr` | `grep -n "def create_pr" release-steps.py` | 使う（`release_lib` から呼べるよう、呼び手が関数を渡す。`promotion.release_selected` の `ops` と同じ形） |
| `close_review` | `release-steps.find_pr`・`git cherry-pick` | 同上 | 使う |
| `cmd_review_open` | `cmd_release_branch` の形（引数 → `release_lib` → `emit`） | `grep -n "def cmd_release_branch" release-steps.py` | 使う |
| `cmd_review_close` | 同上 | 同上 | 使う |
| `pr_kind` | `infer` の head と宛先の分け方 | `project_lib/measure_branch_flow.py` を読んだ | 使う（同じ頭の定数を `branch_flow` から読む） |
| `marks_of` | `measure_ci.Gh.get` | `grep -n "class Gh" project_lib/measure_ci.py` | 使う（締め切りと例外の扱いを含めて） |
| `infer_reviews` | `confidence`・`infer_field` | `project_lib/measure_branch_flow.py` を読んだ | 使う（確からしさの基準を写さない） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `ReviewRow.runs` | 組にある値で真 | 空の組で常に偽 | 無し |
| `ReviewRow.covers_impl` | `impl` の `cross-review / pr` で真 | `release` の `cross-review / resolution` で偽 | 無し |
| `ReviewUnits.row` | 書いた種類は `bundled` の真偽で同じ行 | 5 種類すべてを書かない宣言で既定値の表の 10 セルと一致 | 無し |
| `ReviewUnits.uncovered` | 既定値だけの宣言で空 | まとまりの一方の場合だけ外れる宣言で 1 行 | `impl` が `none`・`sprint` に cross-review が無く・`release` が `cross-review / resolution` の宣言で行を返す |
| `ReviewUnits.overlaps` | `impl` と `sprint` の両方に cross-review を書いた宣言で空 | — | `impl` だけに `cross-review / pr` を書いた宣言で `reviews.sprint` の行 |
| `parse_reviews` | 設計文書の「宣言の形」の例が通る | `reviews: {}` が通る | 知らない種類・知らないキー・表に無い値・重複・I2・I3・I4・I12 のそれぞれで直すキーが出る |
| `review_allowed` | 5 種類が表と一致 | — | 戻り値を書き換えても表が変わらない |
| `parse_flow` | `reviews` の無い宣言が ndf v10.27.61 と同じ `BranchFlow` | `reviews` だけの誤りでも `FlowError` | 4 項目と `reviews` の誤りが 1 つの `FlowError` に並ぶ |
| `FlowError.__init__` | `because` のある行が文面に理由を持つ | `because` の無い行は今の文面のまま | 無し |
| `ResolvedFlow.as_snapshot` | 書いた種類だけが入る | 宣言が無くても `reviews: {}` を持つ | 無し |
| `from_snapshot` | 書き出した値を読み直すと同じ行 | `reviews` の無いスナップショットは `{}` として読む | 手で壊した値で `FlowError` |
| `schema_fragment` | `worktree.schema.json` の `branch_flow` と一致 | — | `declaration.md` の例がスキーマと `parse_flow` の両方に通る |
| `review_row` | スナップショットの値を返す | `a.flow` が無い呼び手は既定値の行 | 無し |
| `chosen_checks` | 選んだ組をそのまま返す | 並びの順に依らず `CHECKS` の順 | `refactor_suspended` かリファクタリングの無いモードで `cross-refactoring` が外れる（I7） |
| `insert_reviews` | 3 つすべてで `review-push` → `assess` → … → `behavior-verify` → `review-pull` → `ready` | 空の組でプランが変わらない | `ready` の無いプランで `ValueError` |
| `drop_unchosen` | `cross-review` だけを外すと、`refactor` の `next` が `behavior-targets` を指す | 3 つすべてを外すと、`pr` の `next` が `base-sync` を指す | 無し |
| `apply_impl` | まとまりの記録ありで `impl` を書かない宣言が `with_review` と同じ `steps` | `impl` に `none` を書くと変えない | 無し |
| `apply_check` | まとまりの記録ありで今の `without_review` と同じ `steps` | 宣言の無いリポジトリで ndf v10.27.61 と同じ並び（I5） | 無し |
| `apply_design` | `pr` で変えない | `none` で `pr` の次が `requirements-recheck` | 外す `id` が `_review_steps` と食い違うとテストが落ちる |
| `sprint_impl_plans` | 受け入れ条件 2 の宣言で選んだ `id` だけが現れる | まとまりの記録の有無で既定値が変わる | 無し |
| `plan_sprint_check` | 同上 | 同上 | 無し |
| `plan_fast_check` | cross-refactoring を選ばないと `prepare` の引数に `--review` | — | 無し |
| `fast_sprint_plans` | cross-review を選ばないと「実装レビュー」のステージが無い | 宣言の無いリポジトリで今と同じステージ | 無し |
| `_spec_pr_steps` | `pr` で `pr` → `spec-review` → `ready` | `none` で今と同じ | 無し |
| `review_range_steps` | `resolution` で 3 ステップ | `none` で空 | 無し |
| `release_branch_step` | `next_id` を渡すとその値 | 省くと `facts` | 無し |
| `plan_release_package_plugin` | `release` を書くと `release-branch` → `review-open` | 書かないと ndf v10.27.61 と同じ並び | 無し |
| `_cleanup_step` | 一時のブランチがあれば消すコマンドを持つ | 無くても成功する | 無し |
| `remerge_patch` | 衝突したマージだけが差分を持つ | 衝突の無い範囲で空文字 | 無い参照で `StepError` |
| `build_review_pair` | 土台と見せるコミットの差分が衝突の解消の差分と等しい | — | 後のマージが同じ行を変えた範囲で `StepError`、一時の作業ツリーが残らない |
| `review_from` | 前回の先頭が祖先ならそこから（I10） | 閉じた PR が無ければ本番チャネル | 前回の先頭が祖先でなければ本番チャネルと `items` の行。`gh` の失敗で `StepError` |
| `close_stale` | 開いた PR を閉じ、ブランチを消す（I11） | 開いた PR が無ければ何もしない | `gh` の失敗で `StepError` |
| `open_review` | `resolution` で PR を作り番号を返す | 空の差分で `review_pr` が `None` | 送れないとき `StepError` |
| `close_review` | 直しのコミットをリリースブランチへ移す（I9） | 直しが無ければ本文だけ書いて閉じる | cherry-pick の衝突でリリースブランチを動かさず `StepError`。開いた PR が無いとき `StepError` |
| `cmd_review_open` | 終了コード 0 と `metrics.review_pr` | 空の差分で終了コード 3 | 失敗で終了コード 1、`--diff none` で 2 |
| `cmd_review_close` | 終了コード 0 | — | 失敗で終了コード 1 |
| `pr_kind` | 6 つの head の形を種類へ分ける | 当たらない head で `None` | 無し |
| `marks_of` | 3 つの印を見分ける | 印の無い PR で空の組 | 読めない PR は標本から外す |
| `infer_reviews` | 標本 3 件以上で値と確からしさ | 2 件で `unknown` と理由 | 無し |
| `collect_flow` | 項目に `reviews` が載り、4 項目の `proposal` は変わらない | — | `gh` を使えないとき `unknown` |
| `drift` | 書いた種類のずれの行 | 書いていない種類は既定値の 2 列のどちらかに合えば出さない | 測れない種類は比べない |
