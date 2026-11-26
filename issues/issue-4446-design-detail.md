# #4446 の詳細設計: 承認資料と質問の文を、承認ゲートの前の工程で読み手役に確かめさせる

設計書 [issue-4446-design.md](./issue-4446-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。読み手は、実装する人とコードレビュー担当のエンジニアである。新設する関数が 2 つ以上あり、既存の関数のシグネチャも変える。そのため、設計書に畳まずに別ファイルにした（`design` の手順 6 の条件）。

**設計書の「未確認のまま残ること」の 2 行は、工程「計画」で決めた（`issue-4446-plan.md`）。** PR の題と本文の書き戻し（設計書の「処理の流れ」の手順 8）はしないと決めた（設計書の決定 10）。`gate` は PR を書き換えず、合格した承認資料をコメントで足す。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── domains/
│   │   ├── ndf-files.json                      # reading-docs・reading-materials の行を足す
│   │   └── release-forms.json                  # 列 approve を足す
│   ├── schemas/
│   │   ├── internal/reading-docs.schema.json   *
│   │   └── io/document-rebuild.{args,result}.schema.json
│   ├── limits/release-bump.assemble.json       *
│   ├── worker/
│   │   ├── document-rebuild.md
│   │   └── document-rebuild-gate.md            *
│   └── workflow/
│       ├── ndf.schema.json                     # kind に reading を足す
│       ├── parallel-plan.schema.json           # 行の入力 kind: release
│       ├── modes/release-bump.json             *
│       └── stages/{design,deploy}.json, stages/release-bump.json *
├── src/ndf/
│   ├── domains/
│   │   ├── docrebuild/{cli.py, drive.py, gate.py *, gate_decide.py *}
│   │   ├── records/{reading.py, cli.py}
│   │   ├── hooks/guards/reading_check.py
│   │   ├── worktree/merged.py
│   │   └── release/{forms_decide.py, approval_decide.py, trail.py, cli.py}
│   └── progress/
│       ├── stages.py, model.py, run.py, folding.py *, command.py
│       ├── gate/{asking.py, answering.py, release.py, entry.py, bump.py *}
│       └── lane/{launch.py, types.py}
├── skills/ (document-rebuild/SKILL.md, development-workflow/references/*, release/*, issue-plan-strategy/references/parallel-plan.md)
├── docs/contracts/commands.md
└── tests/ (下の「単体テストの観点」の各ファイル)
```

依存の向き: `ndf.progress` → `ndf.domains.docrebuild` は呼ばない。工程を進める仕組みは、`ndf document-rebuild gate` をステップのコマンド（`ndf.script`）として子のプロセスで打つ。`ndf.domains.docrebuild.gate` → `ndf.domains.records.reading`（質問の組み立て・2 つの記録の読み書き）→ `ndf.domains.release.forms_decide`（リリースの形の表）の一方向である。`ndf.domains.records.reading` は `ndf.progress` を読まない。`ndf.progress.gate.answering` → `ndf.progress.run` の向きは今のままである。新しい `reread` も `answering` に置く（`run` から `answering` を読むと循環するため）。型の関係は設計書の「構成要素」の図を指し、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/docrebuild/gate.py` | 新設 | `ndf document-rebuild gate` の 1 回の実行を組む | `records.reading`・`records.pull_view`・`adapters.git`・`docrebuild.drive`・`docrebuild.gate_decide` | `GateArgs`・`Run`・`Plan`・`Read`・`Final` と関数 | `drive_gate`・`fix_head`・`same_tree`・`open_review_again`・`working_material`・`plan_reads`・`write_facts`・`gate_prompt`・`verify_worker`・`push_fixed`・`gate_docs`・`doc_file`・`refetch_check`・`send_back`・`post_material`・`write_records`・`complete_check`（新設） |
| `src/ndf/domains/docrebuild/gate_decide.py` | 新設 | gate の判断の部品（ファイル・git・GitHub を触らない） | `records.reading` | 関数 | `strip_marks`・`changed_part`・`body_edited`・`question_text`・`quotes_of`・`record_gap`・`carried_quotes`・`row_verdict`・`rows_of_record`・`doc_rows_of`・`remained_items`（新設） |
| `src/ndf/domains/records/pull_view.py` | 新設 | PR の題・本文・head・変更量・コメントを 1 回の取得で読み、コメントを足す（gate と check が使う） | `adapters.github` | `PullView` と関数 | `pull_view`・`add_comment`（新設） |
| `src/ndf/domains/docrebuild/cli.py` | 変更 | サブコマンド `gate` の引数を受ける | `docrebuild.gate` | モジュール | `_parser`・`_gate_args`・`main`（変更） |
| `src/ndf/domains/docrebuild/drive.py` | 変更 | 組み直しで読ませた文書を読ませたものの記録へ書き、`not_run` で止める | `records.reading` | モジュール | `_checked`・`_reader_gap`（変更）、`record_reads`・`regular_file`・`run_worker_at`・`worker_report`・`not_run_detail`（新設） |
| `src/ndf/domains/records/reading.py` | 変更 | 承認の質問の組み立て・読ませたものの記録の読み書き・`check` の照合 | `reading_decide`・`records.pull_view`・`release.forms_decide`・`adapters.github` | モジュール | `gate_questions`・`material_hashes`・`docs_path`・`materials_path`・`append_doc`・`completed`・`latest_read`・`remained_of`・`pending_review`・`select_reads`・`remained_section`・`split_section`・`comment_has`・`row_fingerprint`（新設）、`add`・`check`（変更） |
| `src/ndf/domains/worktree/merged.py` | 変更 | クリーンアップで、閉じた PR の承認資料のコピーを消す | `contracts.facts`・`identity` | モジュール | `after_merge`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `reading check` に `--gate`・`--pr`・`--material`・`--questions-out` を足す | `records.reading` | モジュール | `_add_reading`・`_reading_check`（変更） |
| `src/ndf/domains/hooks/guards/reading_check.py` | 変更 | 止める理由の文の案内を、読み手役のステップの打ち直しに変える | — | モジュール | `denial`（変更） |
| `src/ndf/domains/release/forms_decide.py` | 変更 | 形の行に `approve` を持たせる | `contracts.facts` | `Form` | `Form`・`form_of`（変更） |
| `src/ndf/domains/release/approval_decide.py` | 変更 | 提示と回答の証跡に資料のハッシュを足し、判定を担い手で分ける | — | `Trail`・`Presentation` | `render_request`・`render_trail`・`parse`・`judge`（変更）、`parse_request`（新設） |
| `src/ndf/domains/release/trail.py` | 変更 | 提示と回答を資料のハッシュで結び付ける | `approval_decide` | モジュール | `present`・`answer`・`judged`・`read`（変更） |
| `src/ndf/domains/release/cli.py` | 変更 | `answer` に `--material-sha256` を足し、開いた版上げの PR を読む `open-releases` を足す | `release.trail` | モジュール | `_answer_args`・`cmd_answer`・`cmd_present`（変更）、`cmd_open_releases`（新設） |
| `src/ndf/progress/stages.py` | 変更 | 種類 `reading` のステップを読み、承認ゲートの数と MVV 判定の探し方から外す | — | `StepDef`・`StageDef` | `StageDef.gates`（変更）、`StageDef.mvv_before`・`StageDef.reading_for`・`StageDef.from_reading`・`step_context`（新設） |
| `src/ndf/progress/model.py` | 変更 | 読み手役のステップを省くかを決める | — | モジュール | `reading_skip`（新設） |
| `src/ndf/progress/run.py` | 変更 | 読み手役のステップの省略・引数の足し方・設計のレビューへの戻しを流す | `model`・`invoke`・`stages` | モジュール | `_running_move`・`_consume`・`_run_step`・`_step_draft`（変更）、`reading_args`・`review_again_drafts`（新設） |
| `src/ndf/progress/folding.py` | 新設 | 事象の列をステップの進みへ畳み込む（`run.py` から分けた） | `model`・`invoke`・`stages` | `View`・`StepProgress` | `payload_of`・`step_progress`（`run.py` から移す） |
| `src/ndf/progress/gate/asking.py` | 変更 | 回答が固定する対象に PR の本文と題の SHA-256 を足す | `run` | `Target` | `Target`・`Target.differs`・`Target.as_dict`・`Target.from_dict`（変更） |
| `src/ndf/progress/gate/answering.py` | 変更 | 待ちへ戻すときに読み手役のステップを未了に戻す。待ちの出し直しを足す | `run`・`asking` | モジュール | `github_observe`・`recheck`（変更）、`reread`・`_reading_reset`・`_step_once`・`_reread_direct`・`lane_env`・`in_lane`（新設・変更） |
| `src/ndf/progress/gate/release.py` | 変更 | 版上げのスプリントでは `bump-release` のリクエストを書く | `asking` | モジュール | `_plan_request`・`_request_fields`（変更）、`_bump_body`（新設） |
| `src/ndf/progress/gate/bump.py` | 新設 | 版上げのスプリントのステップ（`ndf progress bump-assemble`・`bump-present`・`bump-record`） | `lane`・`plan`・`store`・`asking` | モジュール | `bump_assemble`・`bump_present`・`bump_record`・`main`（新設） |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf progress reread` を受ける | `answering` | モジュール | `progress_main`（変更）、`_cmd_reread`・`reread_parser`（新設） |
| `src/ndf/progress/command.py` | 変更 | `new` に `--release-requests` を足し、`release-bump` は課題無しで作る | `stages` | モジュール | `_sprint_new`・`_cmd_new`（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | 行の入力 `kind: release` を起動するコマンドを組む | `lane.types` | モジュール | `start_commands`・`_workflow_args`（変更）、`release_commands`（新設） |
| `src/ndf/progress/lane/types.py` | 変更 | 行の入力のリクエストを読む | — | `LaneRow` | `LaneRow.requests`（新設） |
| `contracts/workflow/stages/design.json`・`deploy.json` | 変更 | `design-reading`・`release-reading` を足す | — | — | — |
| `contracts/workflow/modes/release-bump.json`・`contracts/workflow/stages/release-bump.json`・`contracts/limits/release-bump.assemble.json` | 新設 | 版上げのスプリントの工程と、`assemble` の実行を待つ上限と間隔 | — | — | — |
| `contracts/workflow/ndf.schema.json`・`parallel-plan.schema.json` | 変更 | ステップの種類 `reading` と行の入力 `kind: release` | — | — | — |
| `contracts/schemas/internal/reading-docs.schema.json` | 新設 | 読ませたものの記録の行の形 | — | — | — |
| `contracts/schemas/io/document-rebuild.args.schema.json`・`document-rebuild.result.schema.json` | 変更 | `gate` の引数と結果 | — | — | — |
| `contracts/domains/ndf-files.json`・`release-forms.json` | 変更 | 記録の保存先と、リリースの形の列 `approve` | — | — | — |
| `contracts/worker/document-rebuild.md`・`document-rebuild-gate.md` | 変更・新設 | worker の起動指示 | — | — | — |

`src/ndf/progress/stages.py`・`src/ndf/progress/gate/release.py`・`src/ndf/progress/gate/entry.py` は、設計書の「設計の結果」の触るファイルに無かった。関数の粒度まで下ろすと、これらのファイルも変える必要がある。そのため、設計書の触るファイルにも足した（理由は各構成要素の小見出し）。

## `ndf document-rebuild gate`（新設のサブコマンド）

満たす決定: 決定 2・3・4・5・8、受け入れ条件 2・4・5・6・9、非機能の条件

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drive_gate` | `docrebuild/gate.py` | 新設 | `drive_gate(args: GateArgs, *, hub_of: HubOf, ci_watch: CiWatch, form_of: Callable[[Path], Optional[str]], git_of: Callable[[Path], git.Git] = git.Git, runner: drive.Runner = drive._run) -> dict[str, Any]` | 設計書の「処理の流れ」の手順 1〜9 を順に呼び、結果 JSON を返す |
| `fix_head` | `docrebuild/gate.py` | 新設 | `fix_head(run: Run) -> None` | PR を 1 回取得して head・題・本文を `run` に固定する |
| `open_review_again` | `docrebuild/gate.py` | 新設 | `open_review_again(run: Run) -> Optional[dict[str, Any]]` | 解消していない再レビュー待ちの行を扱う。進めてよければ None、終えるなら結果 JSON |
| `same_tree` | `docrebuild/gate.py` | 新設 | `same_tree(run: Run) -> None` | 設計のゲートでは、作業場所の HEAD が固定した head と同じで、変更が無いことを確かめる |
| `plan_reads` | `docrebuild/gate.py` | 新設 | `plan_reads(run: Run, rows: Sequence[Mapping[str, Any]], form: Optional[str]) -> Plan` | 質問を作り、読ませるもの・差分のファイル・worker が直してよいファイルを決める |
| `working_material` | `docrebuild/gate.py` | 新設 | `working_material(run: Run, rows: Sequence[Mapping[str, Any]]) -> tuple[str, str, Optional[str], Optional[Mapping[str, Any]]]` | 承認資料の始めの本文・基準のセクション・前に読ませた本文・基準の完了の行を返す |
| `gate_docs` | `docrebuild/gate.py` | 新設 | `gate_docs(run: Run) -> dict[str, str]` | 設計 PR の文書と固定した head の blob の SHA。head か作業場所で通常ファイルでない文書があれば止める |
| `doc_file` | `docrebuild/gate.py` | 新設 | `doc_file(run: Run, path: str) -> Path` | 作業場所の文書を開く前に、通常ファイルで、途中のディレクトリを含めてシンボリックリンクを経由しないことを確かめる（`drive.regular_file`） |
| `write_facts` | `docrebuild/gate.py` | 新設 | `write_facts(run: Run) -> Path` | 素材のファイル `approval-facts.json` を書く |
| `gate_prompt` | `docrebuild/gate.py` | 新設 | `gate_prompt(run: Run, plan: Plan, facts: Path) -> str` | 起動指示 `document-rebuild-gate.md` に、読ませるファイルと直してよいファイルを埋める |
| `verify_worker` | `docrebuild/gate.py` | 新設 | `verify_worker(run: Run, plan: Plan, report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]` | 設計書の手順 5 の確かめ。対象の名前から記録を返し、欠けがあれば `Incomplete`（`gaps` を持つ）を投げる |
| `complete_check` | `docrebuild/gate.py` | 新設 | `complete_check(run: Run, plan: Plan, found: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]` | 残った文のセクションと変更要求の見分けから手順 9 までを順に呼び、結果 JSON を返す |
| `strip_marks` | `docrebuild/gate_decide.py` | 新設 | `strip_marks(text: str, expected: str) -> str` | 末尾の 1 組の印の内側が `expected` と 1 字も違わなければそれを除いた本文、違えば印の行だけを消した本文を返す |
| `push_fixed` | `docrebuild/gate.py` | 新設 | `push_fixed(run: Run, docs: Sequence[str]) -> str` | 直した文書をコミットし、再レビュー待ちの行を書いてから push し、CI を待つ。push したコミットを返す |
| `refetch_check` | `docrebuild/gate.py` | 新設 | `refetch_check(run: Run, head: str) -> PullView` | PR を 1 回取得し直し、head・題・本文が動いていないかを確かめる |
| `send_back` | `docrebuild/gate.py` | 新設 | `send_back(run: Run, plan: Plan, targets: Sequence[str], raised: Mapping[str, Sequence[Mapping[str, str]]]) -> dict[str, Any]` | 題の案か PR の本文の行の直しがあるとき、変更要求（設計書の「差し戻し」。承認する人が回答で返す変更要求とは別）として止める。`reading-docs.jsonl` に `sent_back` の行を追記する。確認の完了行と `reading-checks.jsonl` の行を書かずに `stopped`（`precondition`）を返す（設計書の決定 10。PR の題と本文は書き換えない） |
| `post_material` | `docrebuild/gate.py` | 新設 | `post_material(run: Run, now: PullView, final: str) -> None` | 合格した承認資料の最終版を PR のコメントとして 1 件足す。同じ承認資料のコメントがあれば足さない（比べ方は `check` と同じ `reading.comment_has`） |
| `write_records` | `docrebuild/gate.py` | 新設 | `write_records(run: Run, plan: Plan, final: Final, head: str, pushed: bool) -> dict[str, Any]` | 設計書の手順 9 の 1〜5 を順に書き、`outputs.check` の値を返す |
| `_parser`・`main` | `docrebuild/cli.py` | 変更 | 今のまま | サブコマンド `gate` を足し、`run` と同じ `adopt` で結果 JSON を受ける |

- `drive_gate`
  - 事前条件: `args.gate` は `design` か `release`。`--material` は `release` のときだけ。引数の形は `document-rebuild.args.schema.json` が確かめ済み
  - 事後条件: 終わり方ごとに次のとおり
    - 今回読み終えた `ok`: `reading-docs.jsonl` に完了の行があり（読ませた対象か引き継いだ残った文があれば、その行も）、`reading-checks.jsonl` に 1 行が足されている
    - 今回直した文書を push した `ok`（`review_again`）: 再レビュー待ちの行と完了の行があり、`reading-checks.jsonl` には足さない
    - 解消していない再レビュー待ちの行を `open_review_again` が返した `ok`（`review_again`）: PR の固定の後すぐに終える。worker の起動も PR の取得し直しもせず、完了の行も足さない
    - `stopped`: この実行の完了の行は無い（例外は「異常系の処理」の `write_records` の後の保存の失敗）
  - 副作用: GitHub の PR の取得（2 回。`open_review_again` で終えるときは 1 回）・コメントの追記（PR の本文の経路で、同じ承認資料のコメントが無いときだけ。PR の更新はしない）、`ndf worker run` の子のプロセス 1 回（読ませる対象があるときだけ）、`git commit`・`git push`・`ndf ci watch`（文書を直したときだけ）、2 つの記録とコピーのファイルへの追記と置き換え
- `fix_head`
  - 事前条件: `--pr` が無ければ、経路ごとの決め方（設計書の「承認する PR と承認資料」の表）で PR が決まる。`--material` があるときは、番号が `--pr` か工程の入力 `pr` で決まり、チェックアウト中のブランチからは引かない
  - 事後条件: `run.view`（`PullView`）が PR の番号・ブランチ・head・題・本文・ベースを持ち、以後の基準になる。作業場所との比較は `same_tree` が行う
  - 副作用: GitHub の PR の取得 1 回
- `push_fixed`
  - 事前条件: worker が直した文書が、`plan.fixable` のファイルの中だけにある
  - 事後条件: 再レビュー待ちの行が push の前に書かれている。push が通り、CI が通った
  - 副作用: `reading-docs.jsonl` への追記・`git commit`・`git push`（強制しない）・`ndf ci watch`

### 異常系の処理

どの失敗で止まったときも、完了の行を書かずに終える。打ち直すと、手順 1 から進む。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `fix_head` | PR が決まらない | `--pr` が無く、ブランチの PR が無い。`--material` の経路で `--pr` も工程の入力 `pr` も無い | PR の決め方 | 止める | `stopped`（`precondition`）・終了コード 3（`contracts/facts/exit-codes.json` の `precondition`） | 後始末なし。PR を作ってから打ち直せる |
| `fix_head` | GitHub を読めない | 認証・通信 | `pull_view.pull_view` | 止める | `stopped`（`precondition`） | 打ち直せる |
| `same_tree` | 作業場所の HEAD が PR の head と違う・変更が残っている | 作業場所が遅れた・手で触った | `git rev-parse HEAD`・`git status --porcelain` | 止める | `stopped`（`precondition`）、`cause.detail` に両方の SHA | 作業場所を揃えてから打ち直す |
| `gate_docs`・`doc_file` | 文書が通常ファイルでない・シンボリックリンクを経由する | Git のモードがシンボリックリンクなど・作業場所の差し替え | `git ls-tree` のモードと `drive.regular_file` | 開かずに止める | `stopped`（`precondition`） | 通常ファイルに直してから打ち直す |
| `open_review_again` | 再レビュー待ちのコミットの CI が落ちた・監視が中断した | 直した文書の CI | `ndf ci watch` の結果 | 止める | `stopped`（`check_failed`） | 再レビュー待ちの行は残す。CI を直して打ち直す |
| `open_review_again` | 再レビュー待ちのコミットを push し直せない | 権限・競合 | `git push` の終了コード | 止める | `stopped`（`precondition`） | 行は残す。打ち直せる |
| `working_material` | `--material` の行 `提示したコミット:` が固定した head と違う | `materials` を組んだ後に PR が動いた | `approval_decide.material_head` | 止める | `stopped`（`precondition`） | `materials` を組み直してから打ち直す |
| `drive_gate` | 読み手役の worker が `ok` 以外で終えた | worker の止まり | `drive.worker_report` | 止める | `worker_report` が worker の結果から `document-rebuild` の `stopped` を組み直して返す（`run` と共通） | 打ち直せる |
| `verify_worker` | 読ませたものごとの記録が欠けた・`not_run` がある | worker が読み手役を立てなかった | 結果ファイルの `metrics.readers` | 止める | 欠けは `stopped`（`incomplete`）と `gaps`。`not_run` は `stopped`（`precondition`）（受け入れ条件 5） | 打ち直せる |
| `verify_worker` | 最後に読ませた後に中身が変わった・直してよいファイルの外を変えた・`--material` の経路で行 `提示したコミット:` を変えた・変更要求の済んだ PR で本文から写した行を変えた・許していない題の案を書いた | worker の手順違反 | ハッシュの照合・`git status --porcelain -z --untracked-files=all`（`Git.status_paths`。未追跡とステージ済みの変更を含む）・`approval_decide.material_head`・`gate_decide.body_edited`・題の案のファイルの有無 | 止める | `stopped`（`incomplete`）と `gaps` | worker の版は `--state-dir` に残す。打ち直せる |
| `push_fixed` | push できない | 権限・競合 | `git push` の終了コード | 止める | `stopped`（`precondition`） | 再レビュー待ちの行は残す。打ち直すと `open_review_again` が push をやり直す |
| `push_fixed` | push した head の CI が落ちた | 直した文書が検査を落とした | `ndf ci watch` | 止める | `stopped`（`check_failed`） | 行は残す |
| `refetch_check` | head が動いた | 読ませている間に別のコミットが足された | 取得し直した `head_sha` | 止める | `stopped`（`precondition`） | 打ち直すと、足された部分だけを読ませる |
| `refetch_check` | 題か本文がほかの人に変えられた | 読ませている間の編集 | 控えた題・本文との比較 | 止める | `stopped`（`precondition`） | 書き換えない。打ち直せる |
| `send_back` | 題か本文を直さないと通じない | 読み手役の指摘 | 題の案のファイル・`gate_decide.body_edited` | 止める（変更要求） | `stopped`（`precondition`）、`cause.detail` に直す対象・指摘・直しの案のパス | 変更要求の行を残す。直してから工程を再開する。2 回目は変更要求しない |
| `write_records` | 追記か置き換えに失敗した | ファイルを書けない | `OSError` | 止める | `stopped`（`precondition`） | `reading.add` より前で止まると、承認に使う行は前のままで、`check` は前に完了した確認と突き合わせる。`reading.add` の後で完了の行を書けないと、`check` は `no_record` を返す。打ち直せる |
| `complete_check` | 作業用のファイルへ最終版を書けない | ファイルを書けない | `OSError` | 止める | `stopped`（`precondition`） | `write_records` の後なので、完了の行は残る。打ち直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `open_review_again` | 再レビュー待ちの行が無い | None（読ませる手順へ進む） |
| `open_review_again` | 行がある・`--reviewed` が行のコミットと同じ | 解消の行を追記して None |
| `open_review_again` | 行がある・PR の head がそのコミットを含む | CI が通れば `review_again` の `ok`、落ちれば `stopped`（`check_failed`） |
| `open_review_again` | 行がある・含まない・作業場所のブランチにある | push をやり直して上の行へ進む。届かなければ `stopped`（`precondition`） |
| `open_review_again` | 行がある・含まない・作業場所のブランチにも無い | 取り消しの行を追記して None |
| `drive_gate` | 読ませるものが無い（`plan.targets` が空） | worker を起動せず、固定した head で `complete_check` だけを打つ |
| `drive_gate` | worker が文書を直した・`--no-doc-fix` が無い・変更要求に当たらない | `push_fixed` → `refetch_check` → `write_records`（`reading-checks.jsonl` には書かない）の後、`review_again` を返す |
| `drive_gate` | worker が判断の材料だけを直した | push しない。`refetch_check` → `post_material` → `write_records` |
| `drive_gate` | worker が題の案を書いた・PR の本文から写した行を変えた（PR の本文の経路で、変更要求がまだ無い） | 文書も直していれば先に `push_fixed`（コミット・再レビュー待ちの行・push・CI）を打つ。`refetch_check` → `send_back`。`write_records` は打たない。残った再レビュー待ちの行は、次の実行の `open_review_again` が扱う |

- 繰り返しの終わる条件: worker の中の読み手役は立場ごとに最大 2 回。設計のレビューへ戻すのは、同じ承認ゲートの前で 1 回まで（工程を進める仕組みが `--no-doc-fix` を渡す）
- 計算量の注意: 2 つの記録は追記だけのファイルで、行は PR とゲートの数に比例して増える。`reading-docs.jsonl` は読むたびに全行を走査する。通常の `gate` の 1 回は、`doc_rows()` を 4 回呼ぶ（再レビュー待ちの検査、読ませる対象の決定、残った文の引き継ぎ、承認に使う記録の `reads` の選択。push したときは最後の 1 回が無い）。`latest_read` などは、渡された行を走査するだけでファイルを読まない

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `GateArgs` | 型（`gate`・`pr`・`material`・`reviewed`・`no_doc_fix`・`state_dir`・`cwd`・`inputs`） | `docrebuild/gate.py` | — |
| `Run` | 型（`args`・`g`・`hub`・`ci_watch`・`check`・`repo`・`remote`・手順 1 で固定した `view`） | `docrebuild/gate.py` | — |
| `Read`・`Final` | 型（読ませる対象 1 件・承認資料の最終版と読ませた行） | `docrebuild/gate.py` | — |
| `Plan` | 型（質問とそのキー、承認資料のファイルと始めの本文、文書の blob、読ませる対象 `targets`、直してよい `fixable` など） | `docrebuild/gate.py` | — |
| `WORK_KIND` | 定数（`ndf worker run --kind` の値） | `docrebuild/gate.py` | `document-rebuild-gate` |
| `PROMPT_TEMPLATE` | 定数 | `docrebuild/gate.py` | `contracts/worker/document-rebuild-gate.md` |
| `FACTS_FILE`・`MATERIAL_FILE`・`TITLE_FILE` | 定数（`--state-dir` の下のファイル名） | `docrebuild/gate.py` | `approval-facts.json`・`material.md`・`title.txt` |
| 印の 2 行・見出し「読み手役が残した文」 | 定数 | `records/reading.py`（`check` も同じ値を使うので、1 か所に置く） | `<!-- ndf:reader-remained:begin -->`・`<!-- ndf:reader-remained:end -->` |
| 立場（質問の文と承認資料は承認する人、文書はエンジニア） | 定数 `APPROVER`・`ENGINEER` | `records/reading.py` | — |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 別のレーンの `gate`・`run`・`check` | `reading-docs.jsonl`・`reading-checks.jsonl` | 今の `jsonl.append_locked` のロックで 1 行ずつ追記する | 確認の id と完了の行で、途中で止まった確認を使わない（設計書の「確認の id と完了の行」） |
| 同じ PR への `gate` の 2 本 | コピーのファイル `reading-materials/<リポジトリ>/<ゲート>-<PR>.md` | 一時ファイルへ書いてから `os.replace` で置き換える | コピーは最後の完了の行の 2 つのハッシュと合うときだけ差分の元にする |
| `gate` と人の PR の編集 | PR の題と本文（`gate` は書き換えない） | `refetch_check` が head・題・本文を固定した値と比べる | 動いていれば止めて打ち直す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `drive_gate` | 固定した head・読ませる対象の数・worker の状態 | `log.info`（`drive.drive` と同じ形の `extra`） | PR の本文・質問の文・引用 |
| `drive_gate` | 通常の `ok` は `outputs.questions`・`outputs.head`・`outputs.material`（`path`・`sha256`・`file_sha256`）・`outputs.check`・`metrics.reads`（`name`・`reader`・`round`・`verdict`）・最上位の `items`。レビューへ戻す `ok` は `outputs.review_again` と `outputs.check` | 結果 JSON | — |
| `write_records` | 読ませたもの・残った文の引用（`quotes`）・完了の行 | `reading-docs.jsonl` | 本文そのもの |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `drive_gate` | `docrebuild.drive` の worker の起動と結果の読み取り | `grep -n "def run_worker_at\|def worker_report" src/ndf/domains/docrebuild/drive.py` | `run` と `gate` が `run_worker_at(state_dir, cwd, kind, prompt, runner)` と `worker_report(out, result_file)` を共用する |
| `open_review_again` | `adapters.git.Git.is_ancestor` | `grep -n "def is_ancestor" src/ndf/adapters/git.py` | 使う（`git merge-base --is-ancestor`） |
| `fix_head` | `records.pull_view.pull_view`・`Git.rev_parse`・`Git.status_paths` | `grep -n "def pull\b\|def rev_parse\|def status_paths" src/ndf/adapters/*.py` | 使う |
| `working_material` | `approval_decide.material_head` | `grep -n "def material_head" src/ndf/domains/release/approval_decide.py` | 使う |
| `strip_marks` | `records.reading.split_section` | `grep -n "def split_section" src/ndf/domains/records/reading.py` | 使う。除き方は `reading.material_hashes` と共有する |
| `plan_reads` | `adapters.git.Git.blobs` | `grep -n "def blobs" src/ndf/adapters/git.py` | 使う。`gate_docs` が `git ls-tree` で固定した head の blob の ID を得て、`_plan_doc` が前と今の blob の ID を `blobs` に渡す（1 回の起動）。全体を読ませる文書は作業場所のファイルを渡す。差分の部分を切り出す部品は `grep -rn "段落" src/ndf/domains/docrebuild` で 0 件なので新設する |
| `write_facts` | `ndf.domains.release` の `materials` の変更量の計算 | `grep -rn "def cmd_materials" src/ndf/domains/release/cli.py` | 使えない。`materials` は資料の文を組むため。PR の取得結果から組む |
| `gate_prompt` | `docrebuild.drive.prompt_text` | `grep -n "def prompt_text" src/ndf/domains/docrebuild/drive.py` | 使えない。差し込みが違う。テンプレートの読み込みと `string.Template` の使い方は同じ形にする |
| `verify_worker` | `docrebuild.drive.not_run_detail` | `grep -n "def not_run_detail" src/ndf/domains/docrebuild/drive.py` | `not_run` の停止の文を `run` と共用する。記録の欠けの判定は `gate_decide.record_gap` と `gate._target_gap`（ハッシュの照合を含む）が持ち、直してよいファイルの外の変更の検査とともに新設する |
| 残った文のセクション | `records.reading.remained_section` | `grep -n "def remained_section" src/ndf/domains/records/reading.py` | `reading.py` に置く（`check` も同じセクションを作る） |
| `push_fixed` | `ndf ci watch`（`ndf.ci_cli`） | `grep -n "\"ci\"" src/ndf/cli.py` | 子のプロセスで打つ。テストは `ci_watch` を差し替える |
| `refetch_check` | `records.pull_view.pull_view` | 同上 | 使う |
| `post_material` | `records.pull_view.add_comment`（中で `adapters.github.GitHub.rest`） | `grep -n "def add_comment" src/ndf/domains/records/pull_view.py` | 使う（`POST /repos/<repo>/issues/<番号>/comments`）。`github.py` が安全網の 1000 行を超えるため、取得とコメントの口は `records/pull_view.py` に置いた |
| `write_records` | `records.reading.add`・`append_doc`。コピーの置き換えは `os.replace`（`core/worker.py` と同じ形） | `grep -rn "os.replace" src/ndf/core` | 使う。原子的に書く共通の関数は無い（`grep -rn "def write_atomic\|def atomic_write" src/ndf` で 0 件） |

### 単体テストの観点

テストは `tests/domains/docrebuild/test_docrebuild_gate.py`（新設）に置き、worker・GitHub・git・CI を差し替える。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `drive_gate` | 設計の経路で PR の本文と質問を読ませ、記録する | 読ませるものが無いとき worker を起動せず、PR の取得は 2 回 | 異常系の表の行ごと |
| `open_review_again` | 処理の手順の表の 5 行 | `--reviewed` が別のコミット | CI が落ちたままでは `review_again` を返さない |
| `strip_marks` | 基準と同じ内側を除く | 印が 2 組・内側に文が足された・見出しの後ろに文がある | 印の行だけを消し、内側の文は残す |
| `reading.remained_section` | 同じ残った文から同じ文字列 | 残った文が 0 件のときはセクションを作らない | — |
| `verify_worker` | 合格 | 一部だけ読ませた対象のハッシュが元の全体のもの | 中身の変更・決めたファイルの外・`提示したコミット:` の変更・`not_run` |
| `push_fixed` | 行を書いてから push する順 | — | push の失敗・CI の失敗で行が残る |
| `write_records` | 手順 9 の順に書く | 引き継いだ残った文を、読ませなかった対象でも書く | 完了の行の書き込みの失敗で `check` が `no_record` |
| `gate_docs`・`doc_file` | 通常ファイルの文書を読ませる | worker が文書をリンクに差し替えた後も開かない | Git のモードがリンク・作業場所のリンク・途中のディレクトリのリンク |

## 読み手役の worker の起動指示（`contracts/worker/document-rebuild-gate.md`）

満たす決定: 決定 3・8、受け入れ条件 2・5・9

関数は持たない。`gate_prompt` が埋める差し込みの位置だけを決める。

| 差し込み | 中身 | 埋める関数 |
| --- | --- | --- |
| 読む Skill | `skills/document-rebuild/SKILL.md` の「承認の前の確認」 | `gate_prompt` |
| 素材のファイル | `approval-facts.json` のパス | `gate_prompt` |
| 読ませるファイル | `Plan` の対象ごとのファイルと立場。設計 PR の文書を一部だけ読ませるときは、切り出したファイル。承認資料は作業用のファイルを渡し、前に読ませた本文があれば `previous-material.md` も渡す。worker は資料を書き足した後に、変わった段落とその前後だけを読ませる | `gate_prompt` |
| 直してよいファイル | `Plan.fixable`。`--no-doc-fix` のときは文書を除く | `gate_prompt` |
| 結果ファイルの形 | `metrics.readers` の要素に、対象の名前・合否と、確かめごとの対象全体のハッシュ・引用 | 起動指示 `document-rebuild-gate.md` の「確かめの記録」が持ち、`gate_decide.record_gap` が確かめる |

読み手役の起動の許可は、`document-rebuild.md` と同じく「この worker に限り、対象 1 件につき最大 2 回」と書く。`agent-layers.md` の例外の段落に、`ndf document-rebuild gate` が起動した worker を足す。

## `document-rebuild` の Skill（セクション「承認の前の確認」）

満たす決定: 決定 7・8、受け入れ条件 8・9

関数は持たない。セクションの中身は、承認資料を書く → 読み手役に読ませる → 直す → 差分で事実を確かめる、の順の手順である。資料に載せる項目は `approval-request.md` の「承認資料は 2 層に分ける」「承認ゲートごとの違い」を指し、コピーしない。記録は worker が書かず、gate のサブコマンドが書く。description のトリガーに「承認資料」の語を足し、`python3 scripts/check-skill-frontmatter.py` で確かめる。

## 承認の質問の組み立て（`ndf.domains.records.reading`）

満たす決定: 決定 1、受け入れ条件 2・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `gate_questions` | `records/reading.py` | 新設 | `gate_questions(gate: str, number: int, title: str, form: Optional[str]) -> list[dict[str, Any]]` | 設計書の「承認の質問の決まった形」の表から、`AskUserQuestion` の `questions` を作る。`metadata` を持たない |

- `gate_questions`
  - 事前条件: `gate` は `design` か `release`。`release` のときは `form` がプロジェクトの宣言 `branch_strategy.release.form` の値
  - 事後条件: 同じ引数から同じ `questions` を返し、`reading_decide.question_key` で同じキーになる
  - 副作用: 無し（リリースの形の表を読むだけ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `gate_questions` | 形の宣言が無い・表が扱わない形 | 宣言の不足・`supported` が偽 | `forms_decide.form_of` の `FormUnsupported` を `reading._approve_of` が `FormMissing` に変える | 呼び出し元へ返す（`FormMissing`） | `gate`・`check` が `stopped`（`precondition`） | 宣言を直して打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 質問の文の型・選択肢の名前と説明の文 | 定数 | `records/reading.py` の 1 か所（`gate` と `check` が同じ関数を呼ぶ） | 設計書の「承認の質問の決まった形」の表 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `gate_questions` | `reading_decide.question_key`・`forms_decide.form_of` | `grep -n "def question_key\|def form_of" src/ndf/domains` | 使う。質問の文を作る部品は無い（`ndf.progress.gate.asking.question` は id と選択肢だけ） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `gate_questions` | 設計の質問。リリースの質問に `mobile` の `approve` が入る | 題の前後の空白でキーが変わらない | 宣言が無い・扱わない形 |

## リリースの形の表（`contracts/domains/release-forms.json`）

満たす決定: 決定 1、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Form` | `release/forms_decide.py` | 変更 | `Form(id, label, reach, supported, why, approve: Optional[str] = None)` | 列 `approve` を持つ |
| `form_of` | `release/forms_decide.py` | 変更 | 今のまま | 行から `approve` も読む |

表の列 `approve` の値は、設計書の「リリースの形」の表のとおり。`supported` が偽の行には値を置かない（`form_of` がその行で止めるため）。

### 単体テストの観点

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `form_of` | 扱う形の行に `approve` がある | `approve` の無い扱う形の行は表の検査で落ちる |

## `ndf records reading check`（変更）

満たす決定: 決定 1・4、受け入れ条件 6・7・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `records/reading.py` | 変更 | `check(gate: str, number: int, material: Optional[Path], root: Path, hub: Callable[[str], GitHub]) -> tuple[list[str], list[dict[str, Any]]]` | PR をコメントも含めて 1 回取得し（`pull_view`）、その題で質問を作り直し、同じ取得の head と承認資料で照合する。（足りないもの, 質問）を返す |
| `material_hashes` | `records/reading.py` | 新設 | `material_hashes(text: str, expected: str) -> tuple[str, str]` | （読ませた版のハッシュ, ファイル全体のハッシュ）。`gate_decide.strip_marks` と同じ `split_section` の規則で除く |
| `row_fingerprint` | `records/reading.py` | 新設 | `row_fingerprint(row: Mapping[str, Any]) -> str` | 記録の行の JSON をキーの順を固定して作った SHA-256 |
| `_reading_check` | `records/cli.py` | 変更 | `_reading_check(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `--file` の入力を受けず、`--gate`・`--pr`・`--material`・`--questions-out` を受ける。質問をファイルへ書く |
| `_add_reading` | `records/cli.py` | 変更 | 今のまま | `check` だけ引数を分ける |

- `check`
  - 事前条件: `number` は正の整数。`material` は会話のセッションのリリースのときだけ
  - 事後条件: 足りないものは `no_record`・`no_pr`・`head_moved`・`material_changed` のどれか。判定の順は設計書の「確認の id と完了の行」の 1〜3
  - `material_changed` の条件（PR の本文の経路。`material` が無い）: 次のどちらかが外れたとき。(a) 同じ取得のコメントに、完了の行のファイル全体の SHA-256 と同じ承認資料があるか（`comment_has`）。(b) 今の PR の本文の SHA-256 が、完了の行の PR の本文のハッシュと同じか
  - `material_changed` の条件（`material` がある）: そのファイルの読ませた版のハッシュかファイル全体のハッシュが、完了の行と違うとき。コメントと本文は比べない
  - `reading-materials` のコピーは照合に使わない（`gate` が差分の元にするだけである）
  - 副作用: コメントを含む GitHub の PR の取得 1 回・`git fetch`（今と同じ）

`gate` と `check` が同じ値を出すよう、2 つは末尾のセクションの判定に同じ `reading.split_section` を呼ぶ（`records` から `docrebuild` を読まない向きを保つ）。違いは基準と合わないときで、`material_hashes` は何も除かずにハッシュを取り、`gate_decide.strip_marks` は作業用のファイルから印の行だけを消す。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | GitHub を読めない・PR が無い | 認証・番号の誤り | `GitHub.pulls` | 呼び出し元へ返す | `stopped`（`unknown`。今と同じ） | 打ち直せる |
| `check` | 足りないものがある | 記録が無い・head や本文や題が動いた | `reading_decide.missing` と完了の行の照合 | 呼び出し元へ返す | `stopped`（`precondition`）と `items` の `missing` | 問う側が `ndf progress reread` か `gate` を打ち直す |
| `_reading_check` | 質問のファイルを書けない | パス | `OSError` | 止める | `stopped`（`precondition`） | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `material_hashes` | `progress.gate.asking.material_sha256` | `grep -n "def material_sha256" src/ndf/progress/gate/asking.py` | 使えない。ファイル全体のハッシュだけで、印の範囲を除かない。`records` から `progress` を読まない向きも保つ |
| `row_fingerprint` | `reading_decide.question_key` の JSON の固定の仕方 | `grep -n "sort_keys=True" src/ndf/domains/records/reading_decide.py` | 同じ `json.dumps(..., sort_keys=True, separators=(",", ":"))` を使う |

### 単体テストの観点

テストは `tests/domains/records/test_reading.py` に足す。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 記録した質問と同じキーの質問を作り、通す。PR の取得は 1 回 | 完了の行の無い最後の記録の行 → `no_record` | 本文だけの変更・承認資料のコメントの編集か削除 → `material_changed`。題だけの変更 → `no_record` |
| `material_hashes` | セクションを除いた値と全体の値が違う | 印が 2 組 → 何も除かない | — |
| `row_fingerprint` | キーの順が違っても同じ値 | — | — |

## 読ませたものの記録（`reading-docs.jsonl`）と承認資料のコピー

満たす決定: 決定 4、受け入れ条件 1・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `docs_path` | `records/reading.py` | 新設 | `docs_path() -> Path` | `reading-docs.jsonl` のパス（表 `ndf-files` の行 `reading-docs`） |
| `materials_path` | `records/reading.py` | 新設 | `materials_path(repo: str, gate: str, number: int) -> Path` | コピーのパス（表 `ndf-files` の行 `reading-materials`） |
| `_material_copies` | `worktree/merged.py` | 新設 | `_material_copies(repo: str) -> dict[int, list[Path]]` | リポジトリのコピーを PR の番号ごとに返す。保存先は表 `locations` と `ndf-files` から引く（`worktree` は子の表の依存の向きで `records.reading` を読めない） |
| `after_merge` | `worktree/merged.py` | 変更 | 今のまま（`hub` は `repo` も持つ） | `_material_copies` の PR の状態を、渡した PR と同じ `pulls` の 1 回の取得で読み、閉じた PR のコピーを消す（開いている PR と読めない番号は残す） |
| `append_doc` | `records/reading.py` | 新設 | `append_doc(row: Mapping[str, Any]) -> dict[str, Any]` | 1 行を `jsonl.append_locked` で追記する。形は `reading-docs.schema.json` |
| `completed` | `records/reading.py` | 新設 | `completed(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]` | 確認の id から完了の行 |
| `latest_read` | `records/reading.py` | 新設 | `latest_read(rows: Sequence[Mapping[str, Any]], key: tuple[Any, ...]) -> Optional[Mapping[str, Any]]` | 設計書の「読ませるものの決め方」のキーで、完了した確認の最後の読ませた行を引く |
| `remained_of` | `records/reading.py` | 新設 | `remained_of(rows: Sequence[Mapping[str, Any]], repo: str, gate: str, number: int) -> list[Mapping[str, Any]]` | 同じ PR とゲートで最後に完了した確認の `remained` の行 |
| `pending_review` | `records/reading.py` | 新設 | `pending_review(rows, repo: str, branch: str) -> Optional[Mapping[str, Any]]` | 解消していない再レビュー待ちの行 |
| `select_reads` | `records/reading.py` | 新設 | `select_reads(current: Sequence[Mapping[str, Any]], past: Sequence[Mapping[str, Any]], hashes: Mapping[str, str], carried: Iterable[str], base: Mapping[str, Any]) -> list[dict[str, Any]]` | 設計書の「`reads` の作り方」の 1〜4 で、`reads` の要素を作る |
| `remained_section` | `records/reading.py` | 新設 | `remained_section(remained: Sequence[Mapping[str, Any]]) -> str` | 残った文から、印付きの「読み手役が残した文」のセクションを作る。同じ入力から同じ文字列を返し、残った文が 0 件なら空文字を返す |
| `add` | `records/reading.py` | 変更 | 今のまま | 追記した行を返す（今と同じ）。`gate` が指紋を作るのに使う |

- `append_doc`
  - 事後条件: 行の種類は `read`（読ませたもの）・`done`（完了）・`review_pending`・`review_resolved`・`review_cancelled`・`sent_back`（変更要求）のどれか。種類ごとの必須のキーはスキーマが決める（`sent_back` は `check`・`gate`・`pr`・`targets`）
  - 副作用: ファイルへの追記

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `append_doc` | 形が違う | 呼ぶ側の誤り | `internal.InternalError`（`invalid`） | 呼び出し元へ返す（`ReadingInvalid`） | 呼んだサブコマンドが `stopped`（`precondition`） | 書かない。打ち直せる |
| `latest_read` などの読み取り | 新しい版の行がある | 新しい NDF が書いた | `jsonl.read_rows` の `InternalError` | 呼び出し元へ返す | `stopped`（`precondition`） | NDF を上げる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `select_reads` | 今回の結果がある | 完了の行が無くても候補に入れる |
| `select_reads` | 過去の結果だけ | 完了した確認の行で、`base`（今回の確認のリポジトリ・ブランチ・ゲート・PR）から作るキーが同じで、ハッシュが今と同じものだけを候補にする |
| `select_reads` | 候補が複数の確認にまたがる | 最も新しい確認を選び、その中の最後の回を使う（回（`round`）の値の大小で選ばない） |
| `select_reads` | 対象が `carried` にある | `round: 2`・`result: remained` |

前に残った文の扱いの判定は `gate_decide.carried_quotes` が持つ。引用の連続する空白を 1 つにそろえ、対象の今の全体に部分文字列として無ければ解消とする。同じ指摘は、対象とそろえた引用が同じものである。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `docs_path`・`materials_path` | `records.mvv.record_path`（`checks_path` と同じ） | `grep -n "def checks_path" src/ndf/domains/records/reading.py` | 使う。表 `ndf-files` に行 `reading-docs`・`reading-materials` を足す |
| `append_doc` | `records.jsonl.append_locked` | `grep -n "def append_locked" src/ndf/domains/records/jsonl.py` | 使う |
| `completed`・`latest_read`・`remained_of`・`pending_review` | `records.jsonl.read_rows`・`reading_decide.used_row` | `grep -n "def read_rows\|def used_row" src/ndf/domains/records` | 読み取りは `read_rows` を使う。行の選び方は `used_row` と違う（完了の行で絞る）ので新設 |
| `select_reads` | — | `grep -rn "def .*reads" src/ndf/domains/records` で 0 件 | 新設 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `select_reads` | 新しい確認の 1 回目の `passed` を採る | 途中で止まった確認の行を使わない | `name: question` が必ず入る |
| `pending_review` | 解消の行で消える | 取り消しの行で消える | — |
| `latest_read` | 文書の行を作業場所のブランチで引く | PR の番号で引かない | — |

## `ndf document-rebuild run`（変更）

満たす決定: 決定 4・5、受け入れ条件 1・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_reads` | `docrebuild/drive.py` | 新設 | `record_reads(g: git.Git, branch: str, report: Mapping[str, Any], targets: Sequence[Target], *, remote: str) -> None` | 読み手役が読んだ文書を、確認の id・ブランチ・パス・blob・立場・回・合否で追記し、完了の行を書く |
| `_checked` | `docrebuild/drive.py` | 変更 | 今のまま | worker の結果を確かめて `ok` か `stopped` を返す。`record_reads` は打たない |
| `drive` | `docrebuild/drive.py` | 変更 | 今のまま | `_checked` の結果が `ok` のときだけ `record_reads` を打つ |
| `_reader_gap` | `docrebuild/drive.py` | 変更 | 今のまま | `not_run` を欠けとして返す（今は ok で通す） |

- `record_reads`
  - 事前条件: worker の結果が `reader_gaps` を通っている
  - 事後条件: blob は作業場所の HEAD の SHA ではなく、`git hash-object` の文書の blob の SHA
  - 副作用: `reading-docs.jsonl` への追記

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_reader_gap` | `not_run` | 読み手役を立てられなかった | 結果ファイルの `verdict` | 止める | `stopped`（`precondition`） | 打ち直せる |
| `record_reads` | 追記できない | ファイル | `OSError` | 止める | `stopped`（`precondition`） | 完了の行が無いので、次の gate は全体を読ませる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `record_reads` | `records.reading.append_doc`（この変更で新設） | 上の「読ませたものの記録」の表 | 使う。作業場所の文書の blob の SHA は、`Git.run` で `git hash-object` を打って得る |

### 単体テストの観点

テストは `tests/domains/docrebuild/test_docrebuild_run.py` に足す。

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `record_reads` | 文書ごとに行と完了の行がある | blob を HEAD の SHA にすると落ちる |
| `_reader_gap` | `pass`・`fail` は通す | `not_run` で止まる |

## 工程の定義（`stages/design.json`・`stages/deploy.json`・`ndf.schema.json`・`stages.py`）

満たす決定: 決定 2、受け入れ条件 3・4

ステップの種類 `reading` は、`call` を `ndf.script`（argv は `ndf document-rebuild gate --gate <種類>`）とする。`metadata.ndf.kind` は `reading` にし、`metadata.ndf.gate` にどのゲートの前かを書く。`invoke.CALLS` に新しい呼び方を足さないためである。`ndf.schema.json` は、`kind` の値に `reading` を足し、`gate` を書いてよい種類を `gate` と `reading` にする。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `StageDef.gates` | `progress/stages.py` | 変更 | 今のまま | 種類 `gate` のステップだけを数える（`reading` の `gate` を承認ゲートに数えない） |
| `StageDef.mvv_before` | `progress/stages.py` | 新設 | `mvv_before(self, step_id: str) -> Optional[str]` | 承認ゲートの前の MVV 判定のステップの id。そのゲートの読み手役のステップがあればその直前、無ければ承認ゲートの直前（版上げの `bump-present` が間にあっても `release-mvv` を返す） |
| `StageDef.reading_for` | `progress/stages.py` | 新設 | `reading_for(self, gate: Optional[str]) -> Optional[StepDef]` | 同じ工程で、そのゲートの前の種類 `reading` のステップ |
| `StageDef.from_reading` | `progress/stages.py` | 新設 | `from_reading(self, gate: Optional[str]) -> list[str]` | そのゲートの読み手役のステップから承認ゲートの直前までのステップの id |
| `step_context` | `progress/stages.py` | 新設 | `step_context(context: Mapping[str, Any], stage: StageDef, done: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]` | スプリントの文脈の上へ、同じ工程の済んだステップの出力（工程の `outputs` にあるキーだけ）をステップの並び順に重ねる |

版上げの工程のために、定義に次の 3 つと検査を足す（`stages.py`・`definition.py`・`ndf.schema.json`）。

| 足すもの | 内容 |
| --- | --- |
| `metadata.ndf.on_reject` | 承認ゲートが持つ。却下か変更要求の回答で、行を失敗にせず、間のステップを打たずに済んだとして指定のステップへ進む（`stages.passed_gate`） |
| `metadata.ndf.optional_outputs` | 工程の `outputs` のうち、終わりの検査で必須にしないキー（版上げは `delivery`・`product_version`） |
| 検査 I11 | 読み手役のステップの後、種類 `record` のステップを除いた次が、同じ工程の同じゲートの承認ゲートである |
| 検査 I12 | `on_reject` は承認ゲートだけが持ち、同じ工程の後ろのステップを指す |

`stages.py` を触るのは、`gates` が `st.gate` を持つステップをすべて数えるからである。`reading` のステップを足すと、承認ゲートが 2 つに数えられる。すると、工程を無効にできるかの判定（`_refuse_disabled_gate`）と表示（`command._row`）が変わる。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `StageDef.mvv_before` | `StageDef.before` | `grep -n "def before" src/ndf/progress/stages.py` | `before` は残し（ほかの呼び出しがある）、`reading` を飛ばす版を足す |
| `StageDef.reading_for` | — | `grep -rn "reading" src/ndf/progress/stages.py` で 0 件 | 新設 |

### 単体テストの観点

テストは `tests/progress/test_definition.py` と `tests/progress/test_model.py` に足す。

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| 工程の定義 | `design-reading` が `design-mvv` と `design-gate` の間、`release-reading` が `release-mvv` と `release-gate` の間 | 順を入れ替える・間に `record` 以外のステップを置くと定義の検査（I11）で落ちる |
| `StageDef.gates` | 読み手役のステップを足しても承認ゲートは 1 つ | — |
| `StageDef.mvv_before` | `design-gate` から `design-mvv` を返す | 読み手役のステップが無い古い定義でも直前のステップを返す。版上げの `release-gate` から `release-mvv` を返す |

## 工程を進める仕組み（`ndf.progress.run`・`model`・`gate.answering`・`gate.asking`・`gate.entry`）

満たす決定: 決定 2・6、受け入れ条件 3・6・7・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `reading_skip` | `progress/model.py` | 新設 | `reading_skip(done: Mapping[str, Mapping[str, Any]], before: Optional[str], gate: Optional[str], waiting_gate: bool, in_lane: bool, mode: str) -> bool` | 読み手役のステップを打たずに済ませるか。MVV 判定が同じゲートを省くとき（承認ゲートの待ちの記録があるときは除く）と、設計書の「版上げのスプリント」の判定表の 2 行目のとき |
| `_running_move` | `progress/run.py` | 変更 | 今のまま | 承認ゲートの MVV 判定を `s.mvv_before` で探す。読み手役のステップを `reading_skip` で済ませる |
| `_consume` | `progress/run.py` | 変更 | 今のまま | MVV 判定を `s.mvv_before` で探す |
| `reading_args` | `progress/run.py` | 新設 | `reading_args(v: View, s: stages.StageDef, gate: Optional[str]) -> list[str]` | 工程の記録とゲートの名前から `--reviewed <コミット>` と `--no-doc-fix` を作る |
| `_run_step` | `progress/run.py` | 変更 | 今のまま | 種類 `reading` のステップは、`_reading_skipped` で済ませるかを決め、打つなら `_reading_run` で引数を足す |
| `review_again_drafts` | `progress/run.py` | 新設 | `review_again_drafts(v: View, s: stages.StageDef, gate: Optional[str], report: Mapping[str, Any]) -> list[model.EventDraft]` | `outputs.review_again` を受けて、設計のレビュー・MVV 判定・読み手役のステップを未了に戻す事象と、戻したことの記録を作る |
| `_reading_skipped` | `progress/run.py` | 新設 | `_reading_skipped(d, v, s, step, prog) -> Optional[list[model.EventDraft]]` | `reading_skip` で済ませるなら、済ませた記録の事象を返す。レーンかどうかは `Env.in_lane` で引く |
| `_reading_run` | `progress/run.py` | 新設 | `_reading_run(v, s, step, run, work_dir) -> invoke.StageRun` | argv に `--state-dir`・`--out` と `reading_args` を足す。通常の経路と `_reread_direct` が共用する |
| `lane_env`・`in_lane` | `progress/gate/answering.py` | 変更・新設 | `in_lane(st, machine_id) -> bool` | `lane.sprint_row` でレーンの行を引く関数を `run.Env.in_lane` に入れる |
| `_step_once` | `progress/gate/answering.py` | 新設 | `_step_once(st, machine_id, cwd, env) -> dict[str, Any]` | 読み手役のステップを 1 つだけ同じプロセスで流す。次が承認ゲート・失敗・終わりならその結果を返し、続きがあれば `run.advance` で作業を起動する |
| `_reread_direct` | `progress/gate/answering.py` | 新設 | `_reread_direct(st, machine_id, cwd, env) -> dict[str, Any]` | 読み手役のステップの無い古い定義で `ndf document-rebuild gate` を直接打つ |
| `_step_draft` | `progress/run.py` | 変更 | `_step_draft(...) -> list[model.EventDraft]`（1 件から並びへ変える） | `review_again` があれば `review_again_drafts` を続けて返す |
| `Target` | `progress/gate/asking.py` | 変更 | `Target(head, material, body: Optional[str] = None, title: Optional[str] = None)` | 回答が固定する対象に PR の本文と題の sha256 を足す。`differs` も同じ項目を比べ、`as_dict`・`from_dict` は `body_sha256`・`title_sha256` として記録し読み戻す |
| `github_observe` | `progress/gate/answering.py` | 変更 | 今のまま | 同じ 1 回の取得で、head に加えて本文と題の sha256 を読む |
| `recheck` | `progress/gate/answering.py` | 変更 | 今のまま | 待ちへ戻すとき、同じトランザクションで `_reading_reset` の事象を足す |
| `_reading_reset` | `progress/gate/answering.py` | 新設 | `_reading_reset(v: run.View, stage: str, gate: Optional[str]) -> list[model.EventDraft]` | 同じ工程の、そのゲートの読み手役のステップから承認ゲートの直前まで（版上げは `bump-present` を含む）の済んだステップを未了に戻す事象 |
| `reread` | `progress/gate/answering.py` | 新設 | `reread(st: store.Store, machine_id: str, cwd: Path, env: run.Env) -> dict[str, Any]` | 設計書の「待ちの出し直し」の 1〜3。待ちの知らせは呼び手（`_cmd_reread`）が作る |
| `_cmd_reread`・`reread_parser` | `progress/gate/entry.py` | 新設 | `_cmd_reread(a, cwd, st, observe: Optional[Observe] = None) -> dict[str, Any]`・`reread_parser() -> argparse.ArgumentParser` | `ndf progress reread <ID>` を受ける |
| `progress_main` | `progress/gate/entry.py` | 変更 | 今のまま | `reread` を `_cmd_reread` へ渡す |

`reread` は `answering.py` に置き、受け口は `entry.py` に置く。`run.py` から `answering` を読むと循環するためである（`answering` は `run` を読む）。

- `reread`
  - 事前条件: 承認ゲートが人の回答を待っている
  - 事後条件: 待ちの記録が新しい質問の id で書かれ、読み手役のステップが打ち直されている。承認ゲートの前にほかのステップ（`bump-present`）が残るときは、作業を起動して `wait` を返し、待ちの知らせは承認ゲートに着いてから得る。`review_again` なら待ちの知らせを返さない
  - 副作用: 状態の DB への事象の追記（書く前に読んだ `rev` で書く）、`ndf document-rebuild gate` の子のプロセス

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `reread` | 承認ゲートが待っていない | 打つ時点の誤り | `run.decide` の手 | 止める | `stopped`（`precondition`） | 何も書かない |
| `reread` | 書く前に回答が書かれた | 競合 | 読んだ時点の待ち（ステップと `anchor`）と今の待ち | 何も書かずに終える | `ok`（回答を読む案内） | 書かれた回答を捨てない |
| `reread` | 読み手役のステップが止まった | gate の `stopped` | ステップの結果 | 呼び出し元へ返す | そのステップの止まり | 打ち直せる |
| `reread` | 工程の定義に読み手役のステップが無い | 古い定義を読み込んだスプリント | `StageDef.reading_for` が None | `ndf document-rebuild gate` を直接打つ | gate の結果に従う | `--reviewed`・`--no-doc-fix` は `run.reading_args` と同じ規則で作って渡す |
| `_step_draft` | 読み手役のステップの出力が規約に合わない | gate の誤り | `definition.validate_io` | 止める | `bad_report` | 打ち直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `reading_skip` | 1. MVV 判定が同じゲートで `skip: true`・承認ゲートの待ちの記録が無い | 済ませる（版上げのスプリントでも） |
| `reading_skip` | MVV 判定が `skip: true`・承認ゲートの待ちの記録がある | 下の条件へ進む |
| `reading_skip` | 2. 1 で済ませない・`gate: release`・モードが `release-bump` | 打つ |
| `reading_skip` | 3. 1・2 に当たらない・`gate: release`・計画のレーンの行にある | 済ませる |
| `reading_skip` | 4. 1 に当たらない・`gate: release`・計画の外 | 打つ |
| `reading_skip` | `gate: design` | 1 の条件だけで決める（レーンでも打つ） |
| `reading_args` | 同じゲートの最後の `review_again` の記録より後に、種類 `review` のステップの完了の事象がある | `--reviewed <コミット>` を足す（`stages.reading_flags`。工程の記録の順序だけで判定し、git の祖先の関係は確かめない） |
| `reading_args` | 同じ承認ゲートの前の `review_again` が 1 件以上 | `--no-doc-fix` も足す |

レーンの行にあるかは `lane.sprint_row` で決める。`ndf.progress.run` はレーンを読まないので、`_reading_skipped` は `Env.in_lane` を呼び、`answering.lane_env` がその実装を入れる。`reading_skip` は純粋な関数に保つため、引いた値を渡す。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `reread` と `ndf answer` | 承認ゲートの事象 | `run.append` のトランザクションと 待ちの照合（`recheck` と同じ） | 回答が先なら出し直さない |
| `reread` と `next` | 作業の排他 | `store.run_lock`（今と同じ） | 作業が動いていれば `wait` を案内する |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `reading_skip` | `model.mvv_skip` | `grep -n "def mvv_skip" src/ndf/progress/model.py` | 中で使う。レーンとモードの判定を足す |
| `reading_args` | `invoke.stage_run` の `args` | `grep -n "def stage_run" src/ndf/progress/invoke.py` | 使う。argv の後ろに足す |
| `review_again_drafts` | `answering._undropped`・`recheck` の待ちへ戻す事象の形 | `grep -n "def _undropped\|def recheck" src/ndf/progress/gate/answering.py` | 事象の形（`STEP_EVENT` の `status`）を同じにする。`run` から `answering` を読めないので、形は `run` の中で組む |
| `_reading_reset` | 同上 | 同上 | 使う |
| `reread` | `answering.recheck`・`run._run_step`・`run.advance` | `grep -n "def advance\|def _run_step" src/ndf/progress/run.py` | 待ちの書き換えは `recheck` と同じトランザクションの形。読み手役のステップは `_step_once` が `run._run_step` で 1 つだけ流し、続きがあるときと `review_again` の後だけ `advance` を使う |
| `_cmd_reread`・`reread_parser` | `entry.head_parser`・`_cmd_head` | `grep -n "def head_parser" src/ndf/progress/gate/entry.py` | 同じ形で足す |

### 単体テストの観点

テストは `tests/progress/test_run.py`・`tests/progress/test_model.py`・`tests/progress/gate/test_gate_answer.py` に足す。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `reading_skip` | 処理の手順の表の行ごと | 別のゲートの `skip: true` では済ませない | — |
| `_running_move` | 読み手役のステップを挟んでも承認ゲートが MVV 判定を見つける | — | — |
| `review_again_drafts` | 設計のレビューから流し直す | 2 回目は `--no-doc-fix` | — |
| `recheck` | head が動いたとき読み手役のステップを未了に戻す | 本文だけ・題だけの変更でも戻す | — |
| `reread` | 新しい質問の id と対象で待ちの知らせを返す | `review_again` で待ちの知らせを返さない | 待っていない・回答が先に書かれた |

## 手順書と hook の止める理由の文

満たす決定: 決定 6、受け入れ条件 3・7・8・10

| 関数・文書 | 保存先 | 種類 | 変えること |
| --- | --- | --- | --- |
| `denial` | `hooks/guards/reading_check.py` | 変更 | 止める理由の案内を「`ndf records reading check --gate` で確かめ、合わなければ `ndf progress reread` か `gate` を打ち直す」に変える。シグネチャと判定は変えない |
| `approval-request.md`・`asking.md`・`agent-layers.md`・`parallel.md`・`release-steps.md`・`release/SKILL.md`・`parallel-plan.md`・`docs/contracts/commands.md` | 各パス | 変更 | 設計書の「承認を問う側の手順」と、オーガナイザーの手順書の表のとおり。読み手役を問う側が起動する手順を外す |

テストは今の `tests/domains/hooks/test_reading_check.py` がそのまま通ることで確かめる。手順書の文言は照合しない（参照切れは `ndf check` が見る）。

## 会話のセッションのリリースの証跡（`ndf release-steps present`・`answer`）

満たす決定: 決定 2、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `render_request` | `release/approval_decide.py` | 変更 | `render_request(material: str, head: str, material_sha256: str) -> str` | 行 `提示した資料: <SHA-256>` を足す |
| `render_trail` | `release/approval_decide.py` | 変更 | `render_trail(answer: str, sha: str, by: str, material_sha256: Optional[str] = None) -> str` | 担い手が利用者で、資料のハッシュが渡されたときだけ行 `回答した資料:` を足す。ハッシュを必須にする検査はしない（3 引数の呼び出しは旧い形式と MVV 判定の証跡を作る）。新しい利用者の回答にハッシュを必須にするのは `trail.answer` である |
| `parse_request` | `release/approval_decide.py` | 新設 | `parse_request(order: int, author: str, body: str) -> Optional[Presentation]` | 提示のコメントを、順・head・資料のハッシュ・行 `提示した資料:` の有無（`hashed`）として読む |
| `parse` | `release/approval_decide.py` | 変更 | 今のまま | `Trail` に担い手と資料のハッシュを足して読む |
| `judge` | `release/approval_decide.py` | 変更 | `judge(found: Sequence[Trail], presented: Sequence[Presentation], head: str) -> Judgment` | 設計書の表の「判定」の行のとおり、担い手で分けて判定する |
| `present` | `release/trail.py` | 変更 | 今のまま | 最新の提示の資料のハッシュが同じで回答があるときだけ提示しない |
| `answer` | `release/trail.py` | 変更 | `answer(hub: GitHub, number: int, value: str, by: str, presented: Shown, writer: str = WRITER_ANSWER)`（`Shown` は head と資料のハッシュ） | `--by user` は回答した資料の SHA-256 を最新の提示と照合する。`--by mvv` は資料の SHA-256 を指定した場合に拒否する |
| `judged`・`read` | `release/trail.py` | 変更 | 今のまま | 提示のコメントも読んで `judge` へ渡す |
| `_answer_args`・`cmd_answer` | `release/cli.py` | 変更 | 今のまま | `--material-sha256` を足す |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `answer` | `--by user` で資料のハッシュが無い・最新の提示と違う | 提示し直した資料への回答でない | 最新の提示との比較 | 呼び出し元へ返す（`TrailError`） | `stopped`（今の `TrailError` と同じ形） | 書かない。新しい資料に回答を取り直す |
| `answer` | `--by mvv` に資料のハッシュが渡された | 呼ぶ側の誤り | 引数 | 呼び出し元へ返す（`TrailError`） | `stopped` | 書かない |
| `judge` | この変更の前の形式で書かれた利用者の証跡（資料のハッシュの行が無い）がある | この変更を入れる前に回答した | `Presentation` の `hashed` が真の提示が同じ head にあるか | 同じ head に行 `提示した資料:` を持つ提示があれば、旧い形式の承認を数えない。該当する提示が無ければ、旧い形式の提示が残っていても承認を引き継ぐ | 数えないときは `post_status` が承認済みにしない | 数えないときは新しい資料への回答を取り直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `parse_request` | `approval_decide.material_head`・`_line_value` | `grep -n "def material_head\|def _line_value" src/ndf/domains/release/approval_decide.py` | 使う（行の値の読み取り） |

### 単体テストの観点

テストは `tests/domains/release/test_release_approval.py` に足す。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge` | 最新の提示と同じ資料への利用者の回答で承認 | MVV 判定の回答の後に提示があれば、その提示への利用者の回答で判定 | 前の資料への回答では承認にしない |
| `present` | 資料が違えば回答があっても提示し直す | 同じ資料・回答ありでは提示しない | — |
| `answer` | — | 提示の無い `--by mvv` を書く | 前の資料のハッシュで止まる |

## 版上げのスプリント（モード `release-bump`）と計画の行の入力

満たす決定: 決定 9、受け入れ条件 10

工程「版上げ」のステップの並びは設計書の「版上げのスプリント」の表のとおりで、`stages/release-bump.json` に書く。`bump-assemble`・`bump-present`・`bump-record` は `ndf.script` のステップで、`progress/gate/bump.py` が受ける。`bump-assemble` は workflow の `assemble` を起動し、開いた版上げの PR の本文を承認資料のファイルへ写す。テンプレートは置かず、`ndf release-steps materials` も打たない。`release-gate` は `on_reject: bump-record` を持ち、工程は `optional_outputs` に `delivery`・`product_version` を持つ（「工程の定義」のセクションの表）。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_run_step` | `progress/run.py` | 変更 | 今のまま | `invoke.resolve_inputs` へ `v.context()` の代わりに `stages.step_context` の結果を渡す |
| `bump_assemble` | `progress/gate/bump.py` | 新設 | `bump_assemble(a, cwd, st, io, environ) -> dict[str, Any]` | 起動した実行の id と版上げの PR を `progress-work/bump/<ハッシュ>/dispatched` に保存し、`outputs.pr`・`outputs.material` を返す |
| `bump_present` | `progress/gate/bump.py` | 新設 | 同じ形 | 工程の入力の `pr`・`material.path` で `ndf release-steps present` を打つ |
| `bump_record` | `progress/gate/bump.py` | 新設 | 同じ形 | `plan.record_release` と `lane.record_answer` を 1 回のトランザクションで書く。`product_version` が無ければ `rejected` |
| `cmd_open_releases` | `release/cli.py` | 新設 | `cmd_open_releases(args) -> dict` | 本番ブランチ宛ての開いた版上げの PR の番号を返す |
| `LaneRow.requests` | `progress/lane/types.py` | 新設 | `requests(self) -> list[str]`（プロパティ） | 行の入力 `kind: release` のリクエストの ID の並び。ほかの行は空 |
| `release_commands` | `progress/lane/launch.py` | 新設 | `release_commands(plan: str, lane: str, lane_table: str, row: LaneRow) -> list[dict[str, Any]]` | `ndf progress new <名前> --mode release-bump --release-requests ... --parent <レーン>`・`lanes row start`・`progress next` を組む |
| `start_commands` | `progress/lane/launch.py` | 変更 | 今のまま | `kind: release` の行を `release_commands` へ渡す |
| `_workflow_args` | `progress/lane/launch.py` | 変更 | 今のまま | `kind: release` の行は `--release-requests` の並びを返す |
| `_sprint_new`・`_cmd_new` | `progress/command.py` | 変更 | 今のまま | `--release-requests` を受け、モード `release-bump` は `--issues` 無しで作る。ほかのモードに `--release-requests` を渡したら断る |
| `_request_fields`・`_plan_request`・`_bump_body` | `progress/gate/release.py` | 変更・新設 | 今のまま | `_request_fields` は共通のキーを作る。`_plan_request` がモードで種類を選び、`release-bump` なら `_bump_body` が、同じ工程のステップの出力を重ねた文脈から `bump-release` の中身（共通のキー・`material` のパス・`release_requests`）を作る |

同じ工程の前のステップの出力は、`step_context` が文脈に重ね、環境変数 `NDF_STAGE_INPUT` で後ろのステップへ渡る。argv に差し込みは持たせない。`ndf document-rebuild gate`・`ndf progress bump-present`・`bump-record` は、`pr`・`material` などを `invoke.stage_inputs` で読む。`gate` は引数があれば引数を優先する。`material` などの足りない文脈のキーは、スプリントの文脈のスキーマに足す。テストは `tests/progress/test_run.py` に足し、観点は「後ろのステップが前のステップの出力を受ける」「定義の `outputs` に無いキーは渡らない」「後のステップの同じキーが前の値を上書きする」である。

リクエストを書くのは、`asking.py` ではなく `gate/release.py` の `_plan_request` である。そのため、設計書の「設計の結果」の触るファイルに `gate/release.py` を足した。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `start_commands` | 行の入力の形が違う | 計画の誤り | `parallel-plan.schema.json` と `Unsupported` | 止める | 今の `Unsupported` と同じ | 行を直す |
| `_cmd_new` | `release-bump` にリクエストが無い・ほかのモードにリクエストがある | 引数の誤り | 引数の検査 | 止める | `definition.WorkflowDefError`（今の `--loop` と同じ扱い） | 作らない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `LaneRow.requests` | `LaneRow.issues` | `grep -n "def issues" src/ndf/progress/lane/types.py` | 同じ形で足す |
| `release_commands` | `launch.loop_commands`・`start_commands` | `grep -n "def loop_commands" src/ndf/progress/lane/launch.py` | 同じ形（`result.command` の並び）で足す |

### 単体テストの観点

テストは `tests/progress/test_lane_launch.py`・`tests/progress/gate/test_gate_release.py`・`tests/progress/test_definition.py` に足す。

| 関数 | 正常 | 異常 |
| --- | --- | --- |
| `release_commands` | `--mode release-bump --release-requests` の起動を組む | — |
| 工程「版上げ」の定義 | 並びが `bump-assemble`・`release-mvv`・`release-reading`・`bump-present`・`release-gate`・`release`・`bump-record` | 読み手役のステップと承認ゲートの間は `record` の `bump-present` だけを許し（I11）、ほかの種類を置くと定義の検査で落ちる |
| `_plan_request` | 版上げのスプリントは `bump-release` を書く | `lane-release` を書くと落ちる |
| `bump_record` | まとめたリクエストを 1 回のトランザクションで閉じ、各スプリントのレーンの行に回答を載せる | `product_version` が無ければ `rejected` |
