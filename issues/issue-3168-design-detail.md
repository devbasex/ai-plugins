# #3168 の詳細設計: 選んで集める取り込み方の本番のリリースで、承認の後のコミットと組んだ時点の先頭を確かめる

設計文書 [issue-3168-design.md](issue-3168-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。
語の意味は設計文書の「ドメインモデル」の「用語」の表に従う。不変条件（I1〜I9）とドメインイベント（E1〜E7）の番号も
設計文書のものを指す。

この文書で使う語の対応:

| 語 | 指すもの |
| --- | --- |
| 許すコミットの判定 | 承認の後のコミットを 1 つずつ見て、本番チャネルへ入れてよいかを決める処理（`release_writes.py`） |
| 版を書く行の組み立て | 版上げが書き換える行を、ファイルのパス・行を選ぶ正規表現・行の範囲の組で並べたもの（`bump.Site` の一覧） |
| 版数の置き換えの対 | あるプラグインの、承認したコミットの時点の版と、比べた先端の時点の版の組 |
| 先頭の確かめ | 承認資料の「組んだ時点の先頭」の行を読み、承認したコミットとの関係と origin の今の先頭を比べる処理 |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── scripts/
│   ├── release-steps.py
│   ├── lib/
│   │   ├── approved_commit.py
│   │   └── branch_flow.py
│   ├── release_lib/
│   │   ├── approval.py
│   │   ├── bump.py
│   │   ├── promotion.py
│   │   └── release_writes.py *
│   ├── supervise_lib/
│   │   └── release_templates.py
│   └── tests/
│       ├── test_branch_flow.py
│       ├── test_release_selected.py
│       └── test_release_writes.py *
└── skills/
    ├── development-workflow/references/production-merge.md
    └── release/references/release-steps.md
docs/specifications/ndf-branch-flow-declaration.md
```

依存の向きは一方向である。エントリポイントの `release-steps.py` が `release_lib/` を呼ぶ。`release_lib/promotion.py` は、
同じディレクトリの `release_writes.py`・`approval.py` と、`lib/approved_commit.py`・`lib/branch_flow.py` を呼ぶ。
`release_writes.py` は `release_lib/bump.py`・`release_lib/declared_steps.py`（既存。変えない）と、定数 `UNGATED` のために
`release_lib/approval.py` を読むだけで、`promotion.py` は読まない。`approval.py` は `release_writes.py` を読まない。`bump.py` と `lib/` の 2 つは `release_lib/` の上の層を読まない。
`supervise_lib/release_templates.py` はコマンドの文字列を組むだけで、`release_lib/` を import しない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/release_lib/release_writes.py` | 新設 | 承認したコミットの時点の宣言と版から許す箇所を組み、承認の後のコミットを 1 つずつ許すか判定する | `release_lib/bump.py`・`release_lib/declared_steps.py`・`release_lib/approval.py`（`UNGATED` だけ）・`step_result.git` |
| `plugins/ndf/scripts/release_lib/bump.py` | 変更 | 版を書く行の組み立てを 1 か所に持ち、版上げと許すコミットの判定の両方へ渡す | `md`・`versions`・`step_result` |
| `plugins/ndf/scripts/release_lib/promotion.py` | 変更 | 先頭の確かめと許すコミットの判定を比べ直しの回のたびに通し、通ったときだけ本番チャネルへマージする | `release_writes`・`approval`・`approved_commit`・`branch_flow` |
| `plugins/ndf/scripts/release_lib/approval.py` | 変更 | 承認ゲートの結果に、先頭の確かめの項目・止めた理由・呼び手が渡す `next` と許さないパスを載せる | `approved_commit`・`step_result` |
| `plugins/ndf/scripts/lib/approved_commit.py` | 変更 | 承認資料の「組んだ時点の先頭」の行の形を持ち、書く行を作り、資料から読む | 無し（標準ライブラリと `proc`） |
| `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | 取り込み方の行の `moved` に、読めない先頭を動いたとして返す比べ方を足す | 無し（既存の依存のまま） |
| `plugins/ndf/scripts/release-steps.py` | 変更 | `approval-facts` に `--units` を足し、選んで集める取り込み方で必須にして組んだ時点の先頭を資料へ書く。`plan_bump` は版を書く行の組み立てを `bump.py` から読む | `release_lib/*`・`branch_flow` |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | 開発版のプランの `facts` のステップへ、`release-branch` と同じ単位を `--units` で渡す | `supervise_lib/release_flow.py`（既存） |
| `plugins/ndf/scripts/tests/test_release_writes.py` | 新設 | 許すコミットの判定と版を書く行の組み立てを、合成した git のリポジトリで確かめる | `release_lib/release_writes.py`・`release_lib/bump.py` |
| `plugins/ndf/scripts/tests/test_release_selected.py` | 変更 | 本番のリリースの止まる経路と通る経路、`approval-facts --units` を origin を含む合成したリポジトリで確かめる | `release-steps.py` |
| `plugins/ndf/scripts/tests/test_branch_flow.py` | 変更 | `Promotion.moved` の読めない先頭の扱いを確かめる | `lib/branch_flow.py` |
| `docs/specifications/ndf-branch-flow-declaration.md` | 変更 | 承認の後に許すコミット・止める条件・承認資料の新しい行を確定仕様へ書く | 無し |
| `plugins/ndf/skills/development-workflow/references/production-merge.md` | 変更 | 本番チャネルへのマージで止まる 4 つの理由と組み直しの手順を書く | 無し |
| `plugins/ndf/skills/release/references/release-steps.md` | 変更 | `approval-facts --units` と `metrics.reason` の新しい 3 つの値を書く | 無し |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `release_lib/release_writes.py` | モジュール（新設） | `ReleaseWrites`（新設）・`Judged`（新設）・`load`（新設）・`judge`（新設）・`judge_after`（新設）・`_show`（新設）・`_plugin_versions`（新設）・`_version_lines_of`（新設）・`_only_swaps`（新設） |
| `release_lib/bump.py` | モジュール | `Site`（新設）・`version_sites`（新設）・`matching_lines`（新設）・`version_lines`（新設）・`BumpPlan.sub`（変更。行の候補を `matching_lines` から取る。シグネチャ・責務・失敗の扱いは変えない） |
| `release_lib/promotion.py` | モジュール | `allowed_after`（変更）・`built_heads_at`（新設）・`current_heads`（新設）・`check_heads`（新設）・`selected_next`（新設）・`_merge_approved`（変更） |
| `release_lib/approval.py` | モジュール | `gate`（変更）・`heads_gate`（新設）・`HEADS_LEADS`（新設） |
| `lib/approved_commit.py` | モジュール | `BUILT`（新設）・`BuiltHeads`（新設）・`built_heads_row`（新設）・`built_heads`（新設）・`_parse_built`（新設） |
| `lib/branch_flow.py` | `Promotion` | `moved`（変更） |
| `release-steps.py` | モジュール | `cmd_approval_facts`（変更）・`plan_bump`（変更。組み立ての読み先を `bump.version_sites` へ変える。ndf のときは `bump.versioning_doc(ed)` を今どおり直接呼び、`version_sites` の外に残す）・`_bump_manifests`・`_bump_marketplace_and_readme`・`_bump_ndf_docs`（削除。`bump.versioning_doc(ed)` の呼び出しを除く中身は `bump.version_sites` へ移す）・`approval-facts` の引数の定義（変更。`--units`） |
| `supervise_lib/release_templates.py` | モジュール | `_dev_approval_steps`（変更）・その呼び元の開発版のプランの組み立て（変更。`release_units(a)` を渡す） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `load` | `release_lib/release_writes.py` | 新設 | `load(root: Path, approved: str, tip: str) -> ReleaseWrites` | 承認したコミットの時点の宣言と版、先端の時点の版から、許す箇所（`ReleaseWrites`）を組む | `approved` と `tip` がコミットとして読める | 戻り値の `exact` は `ALWAYS`（`CHANGELOG.md`）だけを、`prefixes` は `approval.UNGATED`（`docs/`・`issues/`）と本番の手順の `writes` だけを持ち、`pairs` は版が変わったプラグインだけを持つ | git を読む（`show`・`ls-tree`）。書かない |
| `judge` | `release_lib/release_writes.py` | 新設 | `judge(root: Path, rw: ReleaseWrites, sha: str) -> list[str] \| None` | 1 つのコミットの変えたパスのうち、許さないパスを返す | `sha` はマージでないコミット | 空のリストはコミットを許すことを、`None` は判定できないことを表す。許すパスは戻り値に入らない | git を読む（`diff`・`show`）。書かない |
| `judge_after` | `release_lib/release_writes.py` | 新設 | `judge_after(root: Path, approved: str, tip: str) -> Judged` | 承認したコミットから先端までの first-parent のマージでないコミットを判定し、許すものと許さないもの（理由のパスつき）に分ける | `approved` が `tip` の祖先 | `Judged.allowed` と `Judged.rejected` のキーを合わせると対象のコミットのすべてになり、重ならない | git を読む。書かない |
| `_show` | `release_lib/release_writes.py` | 新設 | `_show(root: Path, rev: str, rel: str) -> str \| None` | コミットの木のファイルの中身を読む | 無し | 読めないとき（ファイルが無い・git の失敗）は `None` | git を読む（`show`） |
| `_plugin_versions` | `release_lib/release_writes.py` | 新設 | `_plugin_versions(root: Path, rev: str) -> dict[str, tuple[str, str]] \| None` | コミットの木の各プラグインの `.claude-plugin/plugin.json` から、プラグインのディレクトリ → (名前, 版) を読む | 無し | 読めない `plugin.json` が 1 つでもあれば `None` | git を読む（`ls-tree`・`show`） |
| `_version_lines_of` | `release_lib/release_writes.py` | 新設 | `_version_lines_of(rw: ReleaseWrites, read: Callable[[str], str \| None], rel: str) -> dict[int, tuple[str, str]]` | 親の木の中身に版を書く行の組み立てを当て、ファイル `rel` で版上げが書き換える行の番号 → 版数の置き換えの対を返す | `read` は親のコミットの木を読む | 戻り値の行は、どれかのプラグインの `bump.Site` が選んだ行だけである | 無し（`read` を通して git を読む） |
| `_only_swaps` | `release_lib/release_writes.py` | 新設 | `_only_swaps(parent: str, child: str, lines: dict[int, tuple[str, str]]) -> bool` | 子の中身が、親の中身の `lines` の行の一部で旧版を新版へ置き換えたものと一致するかを返す | 無し | 真なら、行数が等しく、`lines` の外の行は親と同じで、`lines` の行は親と同じか置き換えた字面である | 無し |
| `version_sites` | `release_lib/bump.py` | 新設 | `version_sites(read: Callable[[str], str \| None], plugin: str, pdir: str, old: str) -> tuple[list[Site], list[str]]` | プラグインの版上げが書き換える行の組み立てと、組み立てられない箇所の手作業の一覧を返す | `pdir` はリポジトリの根からのプラグインのディレクトリ | 返す組み立ては `plan_bump` が今書き換える箇所（`plugin.json` 群の版と説明・`marketplace.json` の説明・`README.md` の一覧表・ndf の README の版）から `docs/versioning-and-distribution.md` を除いた集合である。この文書は `plan_bump` が ndf のときに `bump.versioning_doc(ed)` で今どおり直接書き換える | 無し（`read` を通して読む） |
| `matching_lines` | `release_lib/bump.py` | 新設 | `matching_lines(lines: list[str], line_re: str, start: int, stop: int \| None) -> list[int]` | 範囲の中で正規表現に合う行の番号を返す | 無し | 番号は昇順 | 無し |
| `version_lines` | `release_lib/bump.py` | 新設 | `version_lines(text: str, site: Site, old: str) -> list[int]` | 中身に組み立ての 1 つを当て、旧版を含む行を `site.count` 件まで返す | 無し | 戻り値は `matching_lines` の結果のうち `ver_pat(old)` に合う行の先頭から `count` 件 | 無し |
| `built_heads_row` | `lib/approved_commit.py` | 新設 | `built_heads_row(heads: BuiltHeads) -> tuple[str, str]` | 承認資料の表へ足す「組んだ時点の先頭」の (項目, 内容) を作る | 各 SHA が 40 桁の小文字の 16 進で、単位が 1 つ以上 | 内容は本番チャネルの行 1 つと単位の行を改行でつないだもの（表に書くときに `<br>` になる） | 無し |
| `built_heads` | `lib/approved_commit.py` | 新設 | `built_heads(path: Path) -> BuiltHeads \| None` | 承認資料の「組んだ時点の先頭」の行を読む | 無し | 行が無い・形が違うときは `None`。形の規則は設計文書の「データ構造」 | ファイルを読む |
| `_parse_built` | `lib/approved_commit.py` | 新設 | `_parse_built(cell: str) -> BuiltHeads \| None` | 欄の中身を `<br>` で分けて組んだ時点の先頭に読む | 無し | 本番チャネルがちょうど 1 つ・単位が 1 つ以上・単位の重複が無い・SHA がすべて 40 桁のときだけ値を返す | 無し |
| `Promotion.moved` | `lib/branch_flow.py` | 変更 | `moved(self, presented: dict, now: dict, missing_moved: bool = False) -> list[str]` | 提示の時点の先頭から動いた先頭の名前を返す。`missing_moved` が真なら、今の値が取れない先頭も動いたとして返す | `presented` と `now` は `{"production": SHA, "units": {ブランチ: SHA}}` の形 | `missing_moved` が偽なら今と同じ結果を返す | 無し |
| `allowed_after` | `release_lib/promotion.py` | 変更 | `allowed_after(root, approved: str, branch: str, n: int) -> tuple[ac.AllowedPR, dict[str, list[str]]]` | origin の `branch` の先端を取り直し、許すコミットだけを入れた `AllowedPR` と、許さないコミット → 理由のパスを返す | 無し | `AllowedPR.commits` は `judge_after` が許したコミットだけを持つ。読めなければ `commits` は空で、許さないものの理由は空のパスのリスト | git を読む（`fetch`・`rev-parse`） |
| `built_heads_at` | `release_lib/promotion.py` | 新設 | `built_heads_at(root, production: str, units: list[str], approved: str) -> tuple[ac.BuiltHeads \| None, list[dict]]` | origin を取り直して本番チャネルと各単位の先頭を読み、どれもが承認したコミットの祖先なら組んだ時点の先頭を返す | `units` が 1 つ以上 | 値を返すときは問題の項目が空。読めない・祖先でない先頭があれば値は `None` で、項目に名前と理由が並ぶ | git を読む（`fetch`・`rev-parse`・`merge-base`） |
| `current_heads` | `release_lib/promotion.py` | 新設 | `current_heads(root, built: ac.BuiltHeads) -> dict` | origin を取り直し、記録にある本番チャネルと単位の今の先頭を読む | 無し | 戻り値は `moved` が受ける形で、読めない先頭の値は `None` | git を読む（`fetch`・`rev-parse`） |
| `check_heads` | `release_lib/promotion.py` | 新設 | `check_heads(root, a, approved: str) -> HeadsCheck` | 承認資料の組んだ時点の先頭を読み、行の有無・承認したコミットの祖先か・origin の今の先頭との一致を確かめる | `a.approval` は承認資料のパスか `None` | `HeadsCheck.reason` は `None`・`heads_missing`・`heads_not_in_approved`・`heads_moved` のどれかで、`None` のときだけ I4〜I6 が成り立つ | ファイルを読む。git を読む |
| `selected_next` | `release_lib/promotion.py` | 新設 | `selected_next(a, built: ac.BuiltHeads \| None) -> str` | 選んで集める取り込み方で止めたときの、組み直し・承認資料の作り直し・打ち直しのコマンドの文を作る | 無し | 記録が無いときは単位を `<単位>` のまま残す | 無し |
| `_merge_approved` | `release_lib/promotion.py` | 変更 | `_merge_approved(root, a, plugin, branch, main_pr, approved, items, metrics, ops)`（変えない） | 比べ直しの回のたびに先頭の確かめと許すコミットの判定を通し、通ったときだけ比べた先端をマージする | 承認の記録を照合済み | マージしたときは I1〜I7 が最後の回で成り立っている。破れた回は承認ゲート（10）で出る | GitHub へのマージ（`ops.wait_and_merge`）。出力して終わる（`emit`） |
| `gate` | `release_lib/approval.py` | 変更 | `gate(root, a, plugin, base, verdict, items, metrics, moved=False, rejected=None, nxt=None, extra=(), reason=None) -> dict` | 承認の外の変更で止める結果を作る。`rejected` があれば許さない項目に理由のパスを足し、`nxt`・`extra`・`reason` が渡れば `next`・項目・止めた理由をそれで置き換える | 無し | 新しい引数を渡さなければ今と同じ結果を返す | GitHub を読む（`commit_pr`） |
| `heads_gate` | `release_lib/approval.py` | 新設 | `heads_gate(a, plugin, check: HeadsCheck, items: list[dict], metrics: dict, nxt: str) -> dict` | 先頭の確かめで止める結果（`status: gate`）を作る | `check.reason` が `None` でない | `metrics.reason`・`metrics.built_heads`・`metrics.current_heads` が埋まる | 無し |
| `cmd_approval_facts` | `release-steps.py` | 変更 | `cmd_approval_facts(a)`（変えない） | 承認資料を書き、選んで集める取り込み方では組んだ時点の先頭の行も書く | 無し | 選んで集める取り込み方で資料を書いたときは、資料に I4・I5 を満たす行がある。止まったときは資料を書いていない | ファイルを書く（承認資料）。git と GitHub を読む |
| `_dev_approval_steps` | `supervise_lib/release_templates.py` | 変更 | `_dev_approval_steps(a, repo, approval, prs, rts, head=None, units=()) -> list[dict]` | 開発版の承認資料のステップを組み、単位があれば `facts` のコマンドへ `--units` を足す | `units` を渡すときは `head` も渡す | `units` が空なら今と同じステップを返す | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load` | 本番の手順の宣言を読めない | 承認したコミットの `.ndf/release.json` が JSON でない・形の誤り | `declared_steps.parse_steps` の `DeclarationError` | 呼び出し元へ返す（`ReleaseWritesError`） | `judge_after` がすべてのコミットを許さないとして返し、本番のリリースは `outside_commits` で 10 | 書かないので後始末は無い。宣言を直したリリースブランチで組み直せば再実行できる |
| `load` | プラグインの版を読めない | `plugin.json` が無い・JSON でない・`version` が無い | `_plugin_versions` が `None` を返す | 呼び出し元へ返す（`ReleaseWritesError`） | 同上（10） | 同上 |
| `judge` | 変えたパスを読めない | `git diff` の失敗（第 1 親が無いなど） | `git` の終了コード | `None` を返す（I3） | `judge_after` がそのコミットを許さず、理由のパスは空。10 で `items[]` にそのコミットが載る | 再実行できる。同じ状態なら同じ結果になる |
| `judge` | 版数の記載のファイルの中身を読めない | 親か子の木にファイルが無い（追加・削除）・git の失敗 | `_show` が `None` を返す | そのパスを許さない（I2・I3） | そのコミットが `items[]` に載り、`paths` にそのファイルが入る（10） | 同上 |
| `_show` | ファイルを読めない | 木にファイルが無い・git の失敗 | `git show` の終了コード | 呼び出し元へ返す（`None`） | 無し（`judge` と `load` が受けて扱う） | 読むだけなので後始末は無い |
| `_plugin_versions` | プラグインの版を読めない | `plugin.json` が無い・JSON でない・`version` が文字列でない、`ls-tree` の失敗 | `_show` の `None`・JSON の読み取りの例外 | 呼び出し元へ返す（`None`） | 無し（`load` が `ReleaseWritesError` にする） | 同上 |
| `_version_lines_of` | 親の中身を読めない | 親の木にファイルが無い（ファイルの追加）・git の失敗 | `read` が `None` を返す | 呼び出し元へ返す（空の辞書。版を書く行が無いとして扱う） | 無し（`judge` がそのパスを許さない） | 同上 |
| `judge_after` | 対象のコミットを列挙できない | `rev-list` の失敗 | `git` の終了コード | 呼び出し元へ返す（`ReleaseWritesError`） | `allowed_after` が `commits` を空にし、本番のリリースは承認の後のすべてのコミットを許さずに 10 | 同上 |
| `version_sites` | 組み立てる箇所が見つからない | `README.md` に一覧表の行が無い・`marketplace.json` にプラグインの項目が無い | 中身の検索 | 既定値で続ける（その箇所の組み立てを足さず、手作業の一覧へ足す） | 版上げ（`bump`）では今と同じく `manual` に載る。許すコミットの判定では、その箇所を変えたコミットが許されない | 版上げの打ち直しは今と同じに働く |
| `built_heads` | 承認資料を読めない・行が無い・形が違う | 資料が無い・この変更の前に組んだ資料・手で書き換えた | `approved_commit._cells`・`_parse_built` | 既定値で続ける（`None` を返す） | 本番のリリースは `heads_missing` で 10 | 承認資料を作り直せば再実行できる |
| `built_heads_at` | 先頭を読めない | origin に単位か本番チャネルが無い・`fetch` の失敗 | `rev-parse` の終了コード | 呼び出し元へ返す（値は `None`、項目に `result: missing`） | `approval-facts` が資料を書かずに 1。`next` は `release-branch` の打ち直し | 書かないので後始末は無い。組み直してから打ち直せる |
| `built_heads_at` | 先頭が承認したコミットの祖先でない | `release-branch` の後に単位か本番チャネルへ push された | `merge-base --is-ancestor` の終了コード | 呼び出し元へ返す（値は `None`、項目に `result: not_in_approved`） | 同上（1） | 同上 |
| `allowed_after` | 先端を読めない・判定できない | `fetch`・`rev-parse` の失敗、`judge_after` の `ReleaseWritesError` | `git` の終了コード・例外 | 既定値で続ける（`commits` を空にした `AllowedPR` を返す） | 比較で承認の後のコミットがすべて許されず 10（`outside_commits` か `undecidable`） | 読むだけなので後始末は無い。打ち直せる |
| `current_heads` | 今の先頭を読めない | origin に単位が無い・`fetch` の失敗 | `rev-parse` の終了コード | 既定値で続ける（その先頭の値を `None` にする） | `check_heads` が動いたとして扱い 10（`heads_moved`、`result: missing`） | 同上 |
| `gate` | コミットを入れた PR を読めない | `gh api` の失敗 | `commit_pr` が `None` を返す | 既定値で続ける（項目の `pr` を省く。今と同じ） | 10 の結果は変わらず、項目に `pr` が無い | 同上 |
| `cmd_approval_facts` | `--units` が無い | 選んで集める取り込み方のリポジトリで単発で打った | `branch_flow.read_flow(root).row.selects_units` と引数 | 止める（`StepError`、`EXIT_UNREADABLE`） | 2。要約は「選んで集める取り込み方では `--units` が要る」、資料を書かない | `--units` を付けて打ち直せる |
| `cmd_approval_facts` | `--units` だけで `--head` が無い | 引数の組み合わせの誤り | 引数の検査 | 止める（`StepError`、`EXIT_UNREADABLE`） | 2 | 引数を直して打ち直せる |
| `cmd_approval_facts` | 取り込み方の宣言を読めない | `.ndf/worktree.json` の `branch_flow` の誤り | `branch_flow.FlowError` | 止める（`StepError`、`EXIT_PRECONDITION`） | 3。宣言の誤りの項目を要約に載せる | 宣言を直して打ち直せる |
| `check_heads` | 行が無い・形が違う | `built_heads` が `None`。`--approved-sha` で受けて資料が無いときも含む | `check_heads` の最初 | 呼び出し元へ返す（`reason: heads_missing`） | 10。`items[]` に `kind: cell`・`name: 組んだ時点の先頭`・`result: missing` | origin を読まずに止まるので後始末は無い。`next` の手順で作り直して打ち直せる |
| `check_heads` | 記録した先頭が承認したコミットの祖先でない | 資料の行を手で書き換えた | `merge-base --is-ancestor` | 呼び出し元へ返す（`reason: heads_not_in_approved`） | 10。祖先でない先頭ごとに `kind: branch`・`result: not_in_approved`・`recorded` | 同上 |
| `check_heads` | origin の先頭が記録と違う・読めない | 承認の後に単位か本番チャネルへ push された・単位が消された・`fetch` の失敗 | `Promotion.moved(..., missing_moved=True)` | 呼び出し元へ返す（`reason: heads_moved`） | 10。動いた先頭ごとに `kind: branch`・`role`・`result: moved / missing`・`recorded`・`current` | マージしていないので後始末は無い。組み直しと再承認の後に打ち直せる |
| `_merge_approved` | 先頭が動いた・許さないコミットがある | 上の 2 つの関数の結果 | 比べ直しの回の先頭 | 止める（`emit` で 10） | `status: gate`。両方あれば `metrics.reason` は `heads_moved` で、`items[]` に両方が並ぶ | 本番チャネルへはマージしていない。本番の PR は開いたまま残り、打ち直しで使い回す |
| `_merge_approved` | 比べた後に先端が動いた | マージの直前に積まれた | `ops.wait_and_merge` の `HeadMoved` | 再試行（`HEAD_MOVED_RETRIES` 回まで。回のたびに先頭の確かめから通し直す。I7） | 最後の回でも動けば 10（`head_moved`） | 同上 |
| `Promotion.moved` | 今の値が取れない先頭 | 呼び手が `None` を渡した | 値の検査 | `missing_moved` が真なら動いたとして返し、偽なら比べない（今の振る舞い） | 無し（値を返すだけ） | 無し |

## 処理の手順

`release_writes.judge` がパスごとに許すかを決める表（上から順に当て、最初に当たった行で決める）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `judge` | `diff` を読めない | `None`（コミットを許さない） |
| `judge` | パスが `exact` のどれか（`CHANGELOG.md`）と等しい | 許す |
| `judge` | `declared_steps.allowed(path, rw.prefixes)` が真（`prefixes` のどれかと等しいか、`<prefix>/` で始まる。`docs/`・`issues/` と本番の手順の `writes`） | 許す |
| `judge` | パスが版数の記載のファイル（`_version_lines_of` が行を 1 つ以上返す）で、親と子の両方の中身を読め、`_only_swaps` が真 | 許す |
| `judge` | パスが版数の記載のファイルで、上の行に当たらない（追加・削除・モードの変更・版を書く行の外の変更・旧版と新版の対の外の字面） | 許さない |
| `judge` | どれにも当たらない | 許さない |

モードの変更は `diff --raw` の旧と新のモードを比べて見る。追加・削除は `diff --raw` の状態（`A`・`D`）で見る。
`writes` は承認したコミットの `.ndf/release.json` の、`stage` が `production` か `any` の手順から集める。

`promotion._merge_approved` の比べ直しの回ごとの判定:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_merge_approved` | `check_heads` が `heads_missing` | origin とコミットを読まずに 10（`heads_missing`） |
| `_merge_approved` | `check_heads` が `heads_not_in_approved` | origin とコミットを読まずに 10（`heads_not_in_approved`） |
| `_merge_approved` | `check_heads` が `heads_moved`、許さないコミットは有っても無くても | 10（`heads_moved`）。許さないコミットがあれば `items[]` に足す |
| `_merge_approved` | `check_heads` が `None`、比較（`check_approved`）が通らない | 10（比較の理由。`outside_commits` なら `paths` つき） |
| `_merge_approved` | `check_heads` が `None`、比較が通り、マージで `HeadMoved`、回が残る | 次の回へ（先頭の確かめから） |
| `_merge_approved` | 同上で回が残らない | 10（`head_moved`） |
| `_merge_approved` | `check_heads` が `None`、比較が通り、マージした | マージコミットと判定を返す |

`cmd_approval_facts` の取り込み方と引数の組み合わせ:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `cmd_approval_facts` | `--units` があり `--head` が無い | 2（資料を書かない） |
| `cmd_approval_facts` | 選んで集める取り込み方、`--units` が無い | 2（資料を書かない） |
| `cmd_approval_facts` | 選んで集める取り込み方、`--units` と `--head` がある、`built_heads_at` が値を返す | 資料に組んだ時点の先頭の行を書き、`metrics.built_heads` に同じ値を載せて 10（今と同じ提示の終了コード） |
| `cmd_approval_facts` | 同上で `built_heads_at` が `None` | 1（資料を書かない。`next` は `release-branch --units` の打ち直し） |
| `cmd_approval_facts` | 既定の取り込み方、`--units` が無い | 今と同じ（行を書かない） |
| `cmd_approval_facts` | 既定の取り込み方、`--units` と `--head` がある | 選んで集める取り込み方と同じく行を書く（取り込み方で分岐するのは `--units` の必須の判定だけ） |

`check_heads` が先頭の役割を決める規則: `moved` が返す名前のうち `production` は本番チャネル、それ以外は単位である。
単位はスプリントブランチ（`sprint/` で始まる。`branch_flow.UNIT_MERGE`）で、`production` という名前と重ならない。

- 繰り返しの終わる条件: `_merge_approved` は `HEAD_MOVED_RETRIES + 1` 回で終わる（今と同じ）。`judge_after` の繰り返しは
  `rev-list` が返したコミットの数で終わる
- 計算量の注意: `judge` は版数の記載のファイルごとに親と子の中身を 1 回ずつ読み、`version_sites` を親の中身へ当てる。
  コミットの数 × 版数の記載のファイルの数（ndf で 6 ファイル）回の `git show` になる。承認の後のコミットは本番のプランで
  10 件前後のため、上限を置かない

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `ReleaseWrites` | 型（凍結した dataclass。`prefixes: tuple[str, ...]`・`exact: frozenset[str]`・`pairs: dict[str, tuple[str, str, str]]`（プラグインのディレクトリ → (名前, 旧版, 新版)）） | `release_lib/release_writes.py` | 無し |
| `Judged` | 型（dataclass。`allowed: list[str]`・`rejected: dict[str, list[str]]`） | `release_lib/release_writes.py` | 無し |
| `ReleaseWritesError` | 例外 | `release_lib/release_writes.py` | 無し |
| `ALWAYS` | 定数（承認の後でも許す完全一致のパス） | `release_lib/release_writes.py` | `("CHANGELOG.md",)` |
| `approval.UNGATED` | 定数（承認を取り直さずに通すパスの接頭辞） | `release_lib/approval.py`（既存。唯一の置き場） | `("docs/", "issues/")`。`release_writes.py` はこれを読み、写さない |
| `Site` | 型（凍結した dataclass。`rel`・`line_re`・`what`・`count`・`start`・`stop`・`required`） | `release_lib/bump.py` | `count=1`・`start=0`・`stop=None`・`required=True` |
| `BuiltHeads` | 型（凍結した dataclass。`production_branch: str`・`production: str`・`units: dict[str, str]`。`shape()` が `moved` の受ける形を返す） | `lib/approved_commit.py` | 無し |
| `BUILT` | 定数（承認資料の行の項目名） | `lib/approved_commit.py` | `"組んだ時点の先頭"` |
| `_BUILT_LINE` | 定数（欄の 1 行の正規表現） | `lib/approved_commit.py` | `^(本番チャネル\|リリースの単位) (\S+): ([0-9a-f]{40})$` |
| `HeadsCheck` | 型（dataclass。`reason: str \| None`・`items: list[dict]`・`built: BuiltHeads \| None`・`current: dict \| None`） | `release_lib/promotion.py` | 無し |
| `HEADS_MISSING`・`HEADS_NOT_IN_APPROVED`・`HEADS_MOVED` | 定数（`metrics.reason` の値） | `release_lib/promotion.py` | `"heads_missing"`・`"heads_not_in_approved"`・`"heads_moved"` |
| `HEADS_LEADS` | 定数（止めた理由ごとの要約の書き出し） | `release_lib/approval.py` | 3 つの理由ごとの文。既存の `GATE_LEADS` と同じ形 |

版を書く行の組み立ての唯一の置き場は `bump.version_sites` である。`plan_bump` と `release_writes` はどちらもここから読み、
ファイルの一覧と行を選ぶ正規表現を持たない（設計文書の決定 5）。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 開発版のプランの `release-branch` → `facts` と、ほかのレーンのスプリントブランチへの push | origin の単位のブランチ | 取らない。`facts` が origin を読み直し、承認したコミットの祖先でなければ止まる（I5） | `facts` は資料を毎回作り直す。止まった後は `release-branch` から打ち直せば同じ手順で通る |
| 本番の `release` と、ほかのレーンの単位・本番チャネルへの push | origin の単位・本番チャネル | 取らない。比べ直しの回のたびに origin を読み直し（I7）、マージは `--expect-head` で比べた先端に限る | マージの前に止まれば本番チャネルは変わらない。打ち直すと本番の PR を使い回し、先頭の確かめからやり直す |
| 本番の `release` の打ち直し（マージ済みの後） | 本番の PR | `ops.find_pr` が `MERGED` を見たら比べる処理を通らない（今と同じ） | マージは取り消せないため、先頭の確かめと許すコミットの判定をマージ済みの回に掛けない。公開と戻すマージだけをやり直す |
| 本番のプランの bump〜snapshot・judge → fix・同期とチェックのコミット | リリースブランチ | 本番のプランの中で順に走る（同時に書かない） | どのコミットも `release` の判定に掛かる。許さないコミットが積まれた後の打ち直しは、組み直しと再承認が要る |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_approval_facts` | 承認資料の「組んだ時点の先頭」の行。`metrics.built_heads`（本番チャネルと単位の名前と SHA） | 承認資料（Markdown）・結果 JSON | 秘密。PR 本文の中身（今と同じく資料の欄に要るものだけ） |
| `cmd_approval_facts` | 止めたとき、読めない・祖先でない先頭の名前と SHA | 結果 JSON の `items[]` と `summary` | 同上 |
| `approval.heads_gate` | `metrics.reason`・`metrics.built_heads`・`metrics.current_heads`・先頭ごとの項目・`next` | 結果 JSON | 秘密 |
| `approval.gate` | 許さないコミットの SHA・件名・理由のパス（`paths`） | 結果 JSON の `items[]` | コミットの差分の本文 |
| `release_writes.judge_after` | 何も出さない（値を返すだけ） | 無し | 無し |

進捗記録・ボードへの書き込みは足さない。ログのレベルを持つ出力は無く、結果 JSON が唯一の書き先である。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load` | `declared_steps.parse_steps` | `grep -rn "writes" --include=*.py plugins/ndf/scripts` | 使う。承認したコミットの `.ndf/release.json` を `_show` で読み、JSON を `parse_steps` へ渡す（`load_steps` は worktree の中身を読むため使えない） |
| `load` | `approval.UNGATED` | `release_lib/approval.py` を読んだ | 使う。`docs/`・`issues/` を写さない |
| `judge` | `declared_steps.allowed` | `release_lib/declared_steps.py` を読んだ | 使う。`prefixes` の当て方を `allowed(path, rw.prefixes)` に揃え、単純な前方一致で `CHANGELOG.md.orig` や `writes` に続く別名のパスを通さない |
| `judge` | `approved_commit._touched`・`_tree_diff_paths` | `lib/approved_commit.py` を読んだ | 使えない。パスしか返さず、追加・削除・モードの変更を見分けられない。`diff --raw -z --no-renames` を読む |
| `judge_after` | `approved_commit.compare_approved` | 同上 | 使えない。パスの許可リストの接頭辞だけを受け、版数の置き換えの対を見られない。判定の後の比較には今と同じく使う |
| `_show` | `version_merge._blob` | `grep -rn '"show", f"' plugins/ndf/scripts/lib plugins/ndf/scripts/release_lib` | 使えない。読めないときに空の文字列を返し、空のファイルと見分けられない（I3 に反する） |
| `_plugin_versions` | `release_version._version_at` | 同上 | 使えない。非公開の関数で、プラグインのディレクトリの一覧と名前を返さない |
| `version_sites` | `release-steps.py` の `_bump_manifests`・`_bump_marketplace_and_readme`・`_bump_ndf_docs` | `release-steps.py` を読んだ | 移す。`bump.versioning_doc(ed)` の呼び出しを除く中身をこの関数へ移し、元の 3 つを消す（決定 5）。`versioning_doc` の呼び出しは `plan_bump` に残す |
| `matching_lines` | `BumpPlan.sub` の行の選び方 | `release_lib/bump.py` を読んだ | 移す。`sub` の候補の選び方をこの関数へ切り出し、`sub` と `version_lines` の両方が呼ぶ |
| `version_lines` | `bump.ver_pat`・`bump.marketplace_range` | 同上 | 使う |
| `_version_lines_of` | `bump.version_sites`・`bump.version_lines` | 同上 | 使う。プラグインごとの組み立てを親の中身へ当てる部分だけをこの関数が持つ |
| `_only_swaps` | `bump.BumpPlan.apply`（bump-my-version の `replace`） | `release_lib/bump.py` を読み、設計文書の LIB1（`bump-my-version replace --help`）を見た | 使えない。ファイルを書き換える処理で、置き換えた後の中身を返さない。`bump.ver_pat` で置き換えた字面を作って比べる |
| `built_heads_row` | `approved_commit.material_row`・`parse_sha` | `lib/approved_commit.py` を読んだ | 使う。SHA は `parse_sha` で検め、(項目, 内容) の形は `material_row` に倣う |
| `built_heads` | `approved_commit._cells` | 同上 | 使う。表の行を `_cells` で読み、欄の中身を `_parse_built` へ渡す |
| `_parse_built` | `approved_commit.parse_sha`・`SHA_RE` | 同上 | 使う。SHA の形は `SHA_RE` で見る |
| `built_heads_at` | `promotion.build_branch` の先頭の読み方 | `release_lib/promotion.py` を読んだ | 使えない。組む処理の中で読み、組まずに読む関数が無い。同じ `git fetch` と `rev-parse` を使う |
| `current_heads` | `promotion.build_branch` の先頭の読み方・`built_heads_at` | 同上 | 使えない。`build_branch` は上と同じ。`built_heads_at` は祖先の確かめまで行い、読めない先頭で値を返さない。`current_heads` は読めない先頭を `None` として残す |
| `check_heads` | `Promotion.moved` | `grep -rn "\.moved(" plugins/ndf/scripts` | 使う。`missing_moved=True` で呼ぶ（決定 4） |
| `selected_next` | `approval.gate` の `next` の文 | `release_lib/approval.py` を読んだ | 使えない。`--approved-sha` での打ち直しを指し、組み直しを指さない。`gate` の `nxt` の引数から渡す |
| `heads_gate` | `approval.gate` | 同上 | 使えない。`Verdict` を前提にし、先頭の確かめは `Verdict` を持たない。結果の組み立ては同じ `step_result.result` を使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load` | 承認したコミットの `writes` と、版が変わったプラグインの対を読む | `.ndf/release.json` が無い（`writes` が空）。版が変わったプラグインが無い（`pairs` が空） | 宣言の JSON の誤りで `ReleaseWritesError`。承認の後のコミットで `writes` を広げても、承認したコミットの値で判定する |
| `judge` | bump・bump-others・changelog・notes・snapshot が作るコミットは空のリストを返す | `writes` のディレクトリの下の深いパス。`docs/` だけのコミット | `.py` を変えたコミット・`plugin.json` の `version` と `hooks` を同じコミットで変えたもの・起動コマンドの版つきのパッケージ名だけを変えたもの・旧版と同じ版のパッケージ名を新版へ差し替えたもの・版数の記載のファイルの追加と削除は、そのパスを返す。`diff` の失敗は `None` |
| `judge_after` | 許すコミットだけのとき `rejected` が空 | 承認したコミットと先端が同じ（対象が 0 件） | cherry-pick したマージでないコミットが `rejected` に入る。`rev-list` の失敗で `ReleaseWritesError` |
| `_only_swaps` | 選ばれた行の一部だけを置き換えた子で真 | 選ばれた行のどれも変えない子で真 | 行数が違う・選ばれていない行が変わる・選ばれた行の旧版を対の外の版へ置き換える、のどれでも偽 |
| `version_sites` | ndf と、ndf でないプラグインで、今の `plan_bump` と同じ箇所を返す | `.codex-plugin/plugin.json` が無いプラグイン | 一覧表の行が無い `README.md` で手作業の一覧に載る |
| `version_lines` | 範囲の中で旧版を含む行を `count` 件返す | `marketplace.json` の項目の範囲の境の行 | 旧版を含まない行は返さない |
| `built_heads_row`・`built_heads` | 書いた行を読み直すと同じ値になる | 単位が 1 つ・単位が 3 つ | 行が無い・本番チャネルが 2 つ・単位が無い・同じ単位が 2 つ・SHA が 39 桁、のどれでも `None` |
| `Promotion.moved` | `missing_moved=True` で、消えた単位を動いたとして返す | 既定の引数で今と同じ結果（読めない先頭を比べない） | 本番チャネルが読めないときに `production` を返す |
| `built_heads_at` | 組んだ直後は値を返す | 単位が 1 つ | 組んだ後に単位へ push すると `not_in_approved`。単位が origin に無いと `missing` |
| `check_heads` | 記録と origin が一致すれば `reason` が `None` | 先頭が記録と同じ SHA へ戻された（一致として通る） | 行が無い資料で `heads_missing`。記録を承認したコミットに含まれない SHA へ書き換えると `heads_not_in_approved`。単位へ 1 件 push・本番チャネルが進む・単位が消える、で `heads_moved` |
| `_merge_approved` | 先頭が一致し許さないコミットが無ければマージする | 比べ直しの回で先頭が一致したまま先端だけが許すコミットで進む（通る） | 比べ直しの前に単位へ push すると、その回で 10。先頭が動き許さないコミットもあると `reason` が `heads_moved` で `items[]` に両方 |
| `gate` | 新しい引数を渡さないと今と同じ結果 | `rejected` が空のパスのリスト（読めないコミット） | `nxt` を渡すと `next` が置き換わる |
| `heads_gate` | 3 つの理由のそれぞれで `metrics.reason` と `items[]` の形 | 記録が無いとき `metrics.built_heads` が `null` | 無し（止める結果を作るだけ） |
| `cmd_approval_facts` | `--head --units` で行を書く | 既定の取り込み方で `--units` なし（今と同じ資料） | 選んで集める取り込み方で `--units` なしは 2。`--units` だけは 2。祖先でない先頭は 1。どれも資料を書かない |
| `_dev_approval_steps` | 単位があるとき `facts` のコマンドに `--units` が入る | 単位が無いとき今と同じステップ | 無し（コマンドの文字列を組むだけ） |
