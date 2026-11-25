# #4446 の詳細設計: 承認資料と質問の文を、承認ゲートの前の工程で読み手役に確かめさせる

設計書 [issue-4446-design.md](./issue-4446-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。読み手は、実装する人とコードレビュー担当のエンジニアである。新設する関数が 2 つ以上あり、既存の関数のシグネチャも変える。そのため、設計書に畳まずに別ファイルにした（`design` の手順 6 の条件）。

**設計書の「未確認のまま残ること」の 2 行は、この文書でも決めていない。** 工程「計画」で決める点は、該当する行に「計画で決める」と書いて残した。特に、PR の題と本文の書き戻し（設計書の「処理の流れ」の手順 8）は、計画で直し方が決まるまで実装しない。そのため、書き戻しの関数は名前と責務だけを置き、異常系の扱いは決めていない。

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
│   ├── templates/release-bump.json             *
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
│   │   ├── docrebuild/{cli.py, drive.py, gate.py *}
│   │   ├── records/{reading.py, cli.py}
│   │   ├── hooks/guards/reading_check.py
│   │   └── release/{forms_decide.py, approval_decide.py, trail.py, cli.py}
│   └── progress/
│       ├── stages.py, model.py, run.py, command.py
│       ├── gate/{asking.py, answering.py, release.py, entry.py}
│       └── lane/{launch.py, types.py}
├── skills/ (document-rebuild/SKILL.md, development-workflow/references/*, release/*, issue-plan-strategy/references/parallel-plan.md)
├── docs/contracts/commands.md
└── tests/ (下の「単体テストの観点」の各ファイル)
```

依存の向き: `ndf.progress` → `ndf.domains.docrebuild` は呼ばない。工程を進める仕組みは、`ndf document-rebuild gate` をステップのコマンド（`ndf.script`）として子のプロセスで打つ。`ndf.domains.docrebuild.gate` → `ndf.domains.records.reading`（質問の組み立て・2 つの記録の読み書き）→ `ndf.domains.release.forms_decide`（リリースの形の表）の一方向である。`ndf.domains.records.reading` は `ndf.progress` を読まない。`ndf.progress.gate.answering` → `ndf.progress.run` の向きは今のままである。新しい `reread` も `answering` に置く（`run` から `answering` を読むと循環するため）。型の関係は設計書の「構成要素」の図を指し、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/docrebuild/gate.py` | 新設 | `ndf document-rebuild gate` の 1 回の実行を組む | `records.reading`・`records.pull_view`・`adapters.git`・`docrebuild.drive`・`docrebuild.gate_decide` | `GateArgs`・`Run`・`Plan`・`Read`・`Final` と関数 | `drive_gate`・`fix_head`・`same_tree`・`open_review_again`・`working_material`・`plan_reads`・`write_facts`・`gate_prompt`・`verify_worker`・`push_fixed`・`refetch_check`・`send_back`・`post_material`・`write_records`・`complete_check`（新設） |
| `src/ndf/domains/docrebuild/gate_decide.py` | 新設 | gate の判断の部品（ファイル・git・GitHub を触らない） | `records.reading` | 関数 | `strip_marks`・`changed_part`・`body_edited`・`question_text`・`quotes_of`・`record_gap`・`carried_quotes`・`doc_rows_of`・`comment_has`・`remained_items`（新設） |
| `src/ndf/domains/records/pull_view.py` | 新設 | PR の題・本文・head・変更量・コメントを 1 回の取得で読み、コメントを足す（gate と check が使う） | `adapters.github` | `PullView` と関数 | `pull_view`・`add_comment`（新設） |
| `src/ndf/domains/docrebuild/cli.py` | 変更 | サブコマンド `gate` の引数を受ける | `docrebuild.gate` | モジュール | `_parser`・`_gate_args`・`main`（変更） |
| `src/ndf/domains/docrebuild/drive.py` | 変更 | 組み直しで読ませた文書を読ませたものの記録へ書き、`not_run` で止める | `records.reading` | モジュール | `_checked`・`_reader_gap`（変更）、`record_reads`（新設） |
| `src/ndf/domains/records/reading.py` | 変更 | 承認の質問の組み立て・読ませたものの記録の読み書き・`check` の照合 | `reading_decide`・`release.forms_decide`・`adapters.github` | モジュール | `gate_questions`・`material_hashes`・`docs_path`・`materials_path`・`append_doc`・`completed`・`latest_read`・`remained_of`・`pending_review`・`select_reads`・`row_fingerprint`（新設）、`add`・`check`（変更） |
| `src/ndf/domains/records/cli.py` | 変更 | `reading check` に `--gate`・`--pr`・`--material`・`--questions-out` を足す | `records.reading` | モジュール | `_add_reading`・`_reading_check`（変更） |
| `src/ndf/domains/hooks/guards/reading_check.py` | 変更 | 止める理由の文の案内を、読み手役のステップの打ち直しに変える | — | モジュール | `denial`（変更） |
| `src/ndf/domains/release/forms_decide.py` | 変更 | 形の行に `approve` を持たせる | `contracts.facts` | `Form` | `Form`・`form_of`（変更） |
| `src/ndf/domains/release/approval_decide.py` | 変更 | 提示と回答の証跡に資料のハッシュを足し、判定を担い手で分ける | — | `Trail`・`Presentation` | `render_request`・`render_trail`・`parse`・`judge`（変更）、`parse_request`（新設） |
| `src/ndf/domains/release/trail.py` | 変更 | 提示と回答を資料のハッシュで結び付ける | `approval_decide` | モジュール | `present`・`answer`・`judged`・`read`（変更） |
| `src/ndf/domains/release/cli.py` | 変更 | `answer` に `--material-sha256` を足す | `release.trail` | モジュール | `_answer_args`・`cmd_answer`・`cmd_present`（変更） |
| `src/ndf/progress/stages.py` | 変更 | 種類 `reading` のステップを読み、承認ゲートの数と MVV 判定の探し方から外す | — | `StepDef`・`StageDef` | `StageDef.gates`（変更）、`StageDef.mvv_before`・`StageDef.reading_for`（新設） |
| `src/ndf/progress/model.py` | 変更 | 読み手役のステップを省くかを決める | — | モジュール | `reading_skip`（新設） |
| `src/ndf/progress/run.py` | 変更 | 読み手役のステップの省略・引数の足し方・設計のレビューへの戻しを流す | `model`・`invoke`・`stages` | モジュール | `_running_move`・`_consume`・`_run_step`・`_step_draft`（変更）、`reading_args`・`review_again_drafts`（新設） |
| `src/ndf/progress/gate/asking.py` | 変更 | 回答が固定する対象に本文のハッシュと質問のキーを足す | `run` | `Target` | `Target`・`Target.differs`・`Target.as_dict`・`Target.from_dict`（変更） |
| `src/ndf/progress/gate/answering.py` | 変更 | 待ちへ戻すときに読み手役のステップを未了に戻す。待ちの出し直しを足す | `run`・`asking` | モジュール | `github_observe`・`recheck`（変更）、`reread`・`_reading_reset`（新設） |
| `src/ndf/progress/gate/release.py` | 変更 | 版上げのスプリントでは `bump-release` のリクエストを書く | `asking` | モジュール | `_plan_request`・`_request_fields`（変更） |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf progress reread` を受ける | `answering` | モジュール | `progress_main`（変更）、`_cmd_reread`・`reread_parser`（新設） |
| `src/ndf/progress/command.py` | 変更 | `new` に `--release-requests` を足し、`release-bump` は課題無しで作る | `stages` | モジュール | `_sprint_new`・`_cmd_new`（変更） |
| `src/ndf/progress/lane/launch.py` | 変更 | 行の入力 `kind: release` を起動するコマンドを組む | `lane.types` | モジュール | `start_commands`・`_workflow_args`（変更）、`release_commands`（新設） |
| `src/ndf/progress/lane/types.py` | 変更 | 行の入力のリクエストを読む | — | `LaneRow` | `LaneRow.requests`（新設） |
| `contracts/workflow/stages/design.json`・`deploy.json` | 変更 | `design-reading`・`release-reading` を足す | — | — | — |
| `contracts/workflow/modes/release-bump.json`・`contracts/workflow/stages/release-bump.json`・`contracts/templates/release-bump.json` | 新設 | 版上げのスプリントの工程 | — | — | — |
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
| `drive_gate` | `docrebuild/gate.py` | 新設 | `drive_gate(args: GateArgs, *, hub_of: Callable[[str], GitHub] = ..., git_of: Callable[[Path], git.Git] = git.Git, runner: Runner = drive._run, ci_watch: CiWatch = ...) -> dict[str, Any]` | 設計書の「処理の流れ」の手順 1〜9 を順に呼び、結果 JSON を返す |
| `open_review_again` | `docrebuild/gate.py` | 新設 | `open_review_again(args: GateArgs, fixed: Fixed, g: git.Git, ci_watch: CiWatch) -> Optional[dict[str, Any]]` | 解消していない再レビュー待ちの行を扱う。進めてよければ None、終えるなら結果 JSON |
| `fix_head` | `docrebuild/gate.py` | 新設 | `fix_head(args: GateArgs, hub: GitHub, g: git.Git) -> Fixed` | PR を 1 回取得して head・題・本文を控え、作業場所の HEAD と比べる |
| `working_material` | `docrebuild/gate.py` | 新設 | `working_material(args: GateArgs, fixed: Fixed, base: Optional[Mapping[str, Any]]) -> Path` | 承認資料の作業用のファイルを用意し、基準と違う印を外す |
| `strip_marks` | `docrebuild/gate.py` | 新設 | `strip_marks(text: str, expected: Optional[str]) -> tuple[str, str]` | 末尾の 1 組の印の内側が `expected` と 1 字も違わなければ、（除いた本文, 元の全体）を返す。違えば印の行だけを消した本文を返す |
| `plan_reads` | `docrebuild/gate.py` | 新設 | `plan_reads(args: GateArgs, fixed: Fixed, material: Path, questions: list[dict[str, Any]]) -> Plan` | 読ませるもの・差分のファイル・worker が直してよいファイルを決める |
| `write_facts` | `docrebuild/gate.py` | 新設 | `write_facts(args: GateArgs, fixed: Fixed, mvv_gate: Optional[Mapping[str, Any]]) -> Path` | 素材のファイル `approval-facts.json` を書く |
| `gate_prompt` | `docrebuild/gate.py` | 新設 | `gate_prompt(plan: Plan, facts: Path, no_doc_fix: bool) -> str` | 起動指示 `document-rebuild-gate.md` に、読ませるファイルと直してよいファイルを埋める |
| `verify_worker` | `docrebuild/gate.py` | 新設 | `verify_worker(report: Mapping[str, Any], plan: Plan, fixed: Fixed, g: git.Git) -> list[dict[str, str]]` | 設計書の手順 5 の確かめ。欠けた対象を `gaps` の形で返す |
| `render_remained` | `docrebuild/gate.py` | 新設 | `render_remained(remained: Sequence[Mapping[str, Any]]) -> str` | 残った文から、印付きの「読み手役が残した文」のセクションを作る。同じ入力から同じ文字列を返す |
| `push_fixed` | `docrebuild/gate.py` | 新設 | `push_fixed(args: GateArgs, fixed: Fixed, docs: Sequence[str], g: git.Git, ci_watch: CiWatch) -> str` | 再レビュー待ちの行を書いてからコミット・push し、CI を待つ。push したコミットを返す |
| `refetch_check` | `docrebuild/gate.py` | 新設 | `refetch_check(hub: GitHub, fixed: Fixed, head: str) -> Pull` | PR を 1 回取得し直し、head・題・本文が動いていないかを確かめる |
| `send_back` | `docrebuild/gate.py` | 新設 | `send_back(run, plan, targets, raised) -> dict[str, Any]` | 題の案か PR の本文の行の直しがあるとき、変更要求の行を書き、記録を書かずに `stopped`（`precondition`）で止める（設計書の決定 10。PR の題と本文は書き換えない） |
| `post_material` | `docrebuild/gate.py` | 新設 | `post_material(run, now, final) -> None` | 合格した承認資料の最終版を PR のコメントとして 1 件足す。同じ SHA-256 の本文のコメントがあれば足さない（改行の CRLF と末尾の改行の有無をそろえて比べる） |
| `record` | `docrebuild/gate.py` | 新設 | `record(args: GateArgs, fixed: Fixed, plan: Plan, report: Mapping[str, Any], check_id: str, pushed: Optional[str]) -> dict[str, Any]` | 設計書の手順 9 の 1〜5 を順に書き、`outputs.check` の値を返す |
| `_parser`・`main` | `docrebuild/cli.py` | 変更 | 今のまま | サブコマンド `gate` を足し、`run` と同じ `adopt` で結果 JSON を受ける |

- `drive_gate`
  - 事前条件: `args.gate` は `design` か `release`。`--material` は `release` のときだけ。引数の形は `document-rebuild.args.schema.json` が確かめ済み
  - 事後条件: `ok` なら、`reading-docs.jsonl` に今回の確認の行と完了の行があり、`review_again` が無ければ `reading-checks.jsonl` に 1 行が足されている。`stopped` なら、完了の行は無い
  - 副作用: GitHub の PR の取得（2 回）・コメントの追記（PR の本文の経路で、同じ承認資料のコメントが無いときだけ。PR の更新はしない）、`ndf worker run` の子のプロセス 1 回、`git commit`・`git push`・`ndf ci watch`（文書を直したときだけ）、2 つの記録とコピーのファイルへの追記と置き換え
- `fix_head`
  - 事前条件: `--pr` が無ければ、経路ごとの決め方（設計書の「承認する PR と承認資料」の表）で PR が決まる
  - 事後条件: `Fixed` は PR の番号・ブランチ・head・題・本文・ベースを持ち、以後の基準になる。設計のステージでは、作業場所の HEAD が head と同じで、変更が無い
  - 副作用: GitHub の PR の取得 1 回
- `push_fixed`
  - 事前条件: worker が直した文書が、`plan.fixable` のファイルの中だけにある
  - 事後条件: 再レビュー待ちの行が push の前に書かれている。push が通り、CI が通った
  - 副作用: `reading-docs.jsonl` への追記・`git commit`・`git push`（強制しない）・`ndf ci watch`

### 異常系の処理

どの失敗で止まったときも、完了の行を書かずに終える。打ち直すと、手順 1 から進む。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `fix_head` | PR が決まらない | `--pr` が無く、ブランチの PR が無い。`--material` の経路で `--pr` が無い | PR の決め方 | 止める | `stopped`（`precondition`）・終了コード 0（結果 JSON の契約どおり） | 後始末なし。PR を作ってから打ち直せる |
| `fix_head` | GitHub を読めない | 認証・通信 | `GitHub.pull` | 止める | `stopped`（`precondition`） | 打ち直せる |
| `fix_head` | 作業場所の HEAD が PR の head と違う・変更が残っている | 作業場所が遅れた・手で触った | `git rev-parse HEAD`・`git status --porcelain` | 止める | `stopped`（`precondition`）、`cause.detail` に両方の SHA | 作業場所を揃えてから打ち直す |
| `open_review_again` | 再レビュー待ちのコミットの CI が落ちた・監視が中断した | 直した文書の CI | `ndf ci watch` の結果 | 止める | `stopped`（`check_failed`） | 再レビュー待ちの行は残す。CI を直して打ち直す |
| `open_review_again` | 再レビュー待ちのコミットを push し直せない | 権限・競合 | `git push` の終了コード | 止める | `stopped`（`precondition`） | 行は残す。打ち直せる |
| `working_material` | `--material` の行 `提示したコミット:` が固定した head と違う | `materials` を組んだ後に PR が動いた | `approval_decide.material_head` | 止める | `stopped`（`precondition`） | `materials` を組み直してから打ち直す |
| `drive_gate` | 読み手役の worker が `ok` 以外で終えた | worker の止まり | `drive._worker_result` | 止める | worker の `stopped` をそのまま返す（`drive._worker_stopped` と同じ） | 打ち直せる |
| `verify_worker` | 読ませたものごとの記録が欠けた・`not_run` がある | worker が読み手役を立てなかった | 結果ファイルの `metrics.readers` | 止める | 欠けは `stopped`（`incomplete`）と `gaps`。`not_run` は `stopped`（`precondition`）（受け入れ条件 5） | 打ち直せる |
| `verify_worker` | 最後に読ませた後に中身が変わった・決めたファイルの外を変えた・行 `提示したコミット:` を変えた | worker の手順違反 | ハッシュと `git diff --name-only` の比較 | 止める | `stopped`（`incomplete`）と `gaps` | worker の版は `--state-dir` に残す。打ち直せる |
| `verify_worker` | 作り直した質問のキーが 2 回目に読ませたキーと違う | 新しい題の案が質問に入らない | `gate_questions` の結果の比較 | 計画で決める（設計書の「未確認」の (4)） | 計画で決める | 計画で決める |
| `push_fixed` | push できない | 権限・競合 | `git push` の終了コード | 止める | `stopped`（`precondition`） | 再レビュー待ちの行は残す。打ち直すと `open_review_again` が push をやり直す |
| `push_fixed` | push した head の CI が落ちた | 直した文書が検査を落とした | `ndf ci watch` | 止める | `stopped`（`check_failed`） | 行は残す |
| `refetch_check` | head が動いた | 読ませている間に別のコミットが足された | 取得し直した `head_sha` | 止める | `stopped`（`precondition`） | 打ち直すと、足された部分だけを読ませる |
| `refetch_check` | 題か本文がほかの人に変えられた | 読ませている間の編集 | 控えた題・本文との比較 | 止める | `stopped`（`precondition`） | 書き換えない。打ち直せる |
| `send_back` | 題か本文を直さないと通じない | 読み手役の指摘 | 題の案のファイル・`gate_decide.body_edited` | 止める（変更要求） | `stopped`（`precondition`）、`cause.detail` に直す対象・指摘・直しの案のパス | 変更要求の行を残す。直してから工程を再開する。2 回目は変更要求しない |
| `record` | 追記か置き換えに失敗した | ファイルを書けない | `OSError` | 止める | `stopped`（`precondition`） | 完了の行が無いので、`check` は `no_record` を返す。打ち直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `open_review_again` | 再レビュー待ちの行が無い | None（読ませる手順へ進む） |
| `open_review_again` | 行がある・`--reviewed` が行のコミットと同じ | 解消の行を追記して None |
| `open_review_again` | 行がある・PR の head がそのコミットを含む | CI が通れば `review_again` の `ok`、落ちれば `stopped`（`check_failed`） |
| `open_review_again` | 行がある・含まない・作業場所のブランチにある | push をやり直して上の行へ進む。届かなければ `stopped`（`precondition`） |
| `open_review_again` | 行がある・含まない・作業場所のブランチにも無い | 取り消しの行を追記して None |
| `drive_gate` | 読ませるものが無い（`plan.targets` が空） | worker を起動せず、固定した head で `record` だけを打つ |
| `drive_gate` | worker が文書を直した・`--no-doc-fix` が無い | `push_fixed` を打ち、`record` は `reading-checks.jsonl` に書かずに `review_again` を返す |
| `drive_gate` | worker が判断の材料だけを直した | push しない。`refetch_check` → `post_material` → `write_records` |
| `drive_gate` | worker が題の案を書いた・PR の本文から写した行を変えた | push しない。`refetch_check` → `send_back` |

- 繰り返しの終わる条件: worker の中の読み手役は立場ごとに最大 2 回。設計のレビューへ戻すのは、同じ承認ゲートの前で 1 回まで（工程を進める仕組みが `--no-doc-fix` を渡す）
- 計算量の注意: 2 つの記録は追記だけのファイルで、1 回の実行で全行を 1 回だけ読む。行は PR とゲートの数に比例して増えるので、読むのは `reading.latest_read` などの関数の中の 1 回にまとめる

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `GateArgs` | 型（`gate`・`pr`・`material`・`reviewed`・`no_doc_fix`・`cwd`・`state_dir`・`remote`） | `docrebuild/gate.py` | — |
| `Fixed` | 型（PR の番号・URL・ブランチ・ベース・head・題・本文） | `docrebuild/gate.py` | — |
| `Plan` | 型（読ませる対象ごとの名前・元の全体のハッシュ・読ませるファイル・立場、`fixable` のパスの集合） | `docrebuild/gate.py` | — |
| `WORK_KIND` | 定数（`ndf worker run --kind` の値） | `docrebuild/gate.py` | `document-rebuild-gate` |
| `PROMPT_TEMPLATE` | 定数 | `docrebuild/gate.py` | `contracts/worker/document-rebuild-gate.md` |
| `FACTS_FILE`・`MATERIAL_FILE`・`TITLE_FILE` | 定数（`--state-dir` の下のファイル名） | `docrebuild/gate.py` | `approval-facts.json`・`material.md`・`title.txt` |
| 印の 2 行・見出し「読み手役が残した文」 | 定数 | `records/reading.py`（`check` も同じ値を使うので、1 か所に置く） | `<!-- ndf:reader-remained:begin -->`・`<!-- ndf:reader-remained:end -->` |
| 立場（質問の文と承認資料は承認する人、文書はエンジニア） | 定数 | 計画で決める（設計書の「未確認」の (6)） | — |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 別のレーンの `gate`・`run`・`check` | `reading-docs.jsonl`・`reading-checks.jsonl` | 今の `jsonl.append_locked` のロックで 1 行ずつ追記する | 確認の id と完了の行で、途中で止まった確認を使わない（設計書の「確認の id と完了の行」） |
| 同じ PR への `gate` の 2 本 | コピーのファイル `reading-materials/<ゲート>-<PR>.md` | 一時ファイルへ書いてから `os.replace` で置き換える | コピーは最後の完了の行の 2 つのハッシュと合うときだけ差分の元にする |
| `gate` と人の PR の編集 | PR の題と本文 | `refetch_check` と、書き換えの後の照合（計画で決める） | 止めて打ち直す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `drive_gate` | 固定した head・読ませる対象の数・worker の状態 | `log.info`（`drive.drive` と同じ形の `extra`） | PR の本文・質問の文・引用 |
| `drive_gate` | `outputs.questions`・`outputs.head`・`outputs.material`・`outputs.check`・`metrics.readers`・最上位の `items` | 結果 JSON | — |
| `record` | 読ませたもの・残った文の引用（`quotes`）・完了の行 | `reading-docs.jsonl` | 本文そのもの |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `drive_gate` | `docrebuild.drive.run_worker`・`_worker_result`・`_read_report`・`_worker_stopped` | `grep -n "def run_worker\|def _worker" src/ndf/domains/docrebuild/drive.py` | 使う。`run_worker` は `RunArgs` に依存しないよう、保存先と kind を引数に取る形へ広げる |
| `open_review_again` | `adapters.git.Git.is_ancestor` | `grep -n "def is_ancestor" src/ndf/adapters/git.py` | 使う（`git merge-base --is-ancestor`） |
| `fix_head` | `adapters.github.GitHub.pull`・`Git.rev_parse`・`Git.status_paths` | `grep -n "def pull\b\|def rev_parse\|def status_paths" src/ndf/adapters/*.py` | 使う |
| `working_material` | `approval_decide.material_head` | `grep -n "def material_head" src/ndf/domains/release/approval_decide.py` | 使う |
| `strip_marks` | — | `grep -rn "reader-remained" src/ndf` で 0 件 | 新設。除き方は `reading.material_hashes` と共有する |
| `plan_reads` | `adapters.git.Git.blobs`（`<コミット>:<パス>` を 1 回の起動で読む） | `grep -n "def blobs" src/ndf/adapters/git.py` | 使う。差分の部分と前後の段落を切り出す部品は `grep -rn "段落" src/ndf/domains/docrebuild` で 0 件なので新設する |
| `write_facts` | `ndf.domains.release` の `materials` の変更量の計算 | `grep -rn "def cmd_materials" src/ndf/domains/release/cli.py` | 使えない。`materials` は資料の文を組むため。PR の取得結果から組む |
| `gate_prompt` | `docrebuild.drive.prompt_text` | `grep -n "def prompt_text" src/ndf/domains/docrebuild/drive.py` | 使えない。差し込みが違う。テンプレートの読み込みと `string.Template` の使い方は同じ形にする |
| `verify_worker` | `docrebuild.drive.reader_gaps`・`_reader_gap` | `grep -n "def reader_gaps" src/ndf/domains/docrebuild/drive.py` | 使う（`not_run` と欠けの判定）。ハッシュの照合とファイルの外の変更は新設 |
| `render_remained` | — | `grep -rn "読み手役が残した文" src/ndf` で 0 件 | 新設 |
| `push_fixed` | `ndf ci watch`（`ndf.ci_cli`） | `grep -n "\"ci\"" src/ndf/cli.py` | 子のプロセスで打つ。テストは `ci_watch` を差し替える |
| `refetch_check` | `adapters.github.GitHub.pull` | 同上 | 使う |
| `post_material` | `adapters.github.GitHub.rest` | `grep -n "def rest" src/ndf/adapters/github.py` | 使う（`POST /repos/<repo>/issues/<番号>/comments`）。`github.py` が安全網の 1000 行を超えるため、取得とコメントの口は `records/pull_view.py` に置いた |
| `record` | `records.reading.add`・`append_doc`。コピーの置き換えは `os.replace`（`core/worker.py` と同じ形） | `grep -rn "os.replace" src/ndf/core` | 使う。原子的に書く共通の関数は無い（`grep -rn "def write_atomic\|def atomic_write" src/ndf` で 0 件） |

### 単体テストの観点

テストは `tests/domains/docrebuild/test_docrebuild_gate.py`（新設）に置き、worker・GitHub・git・CI を差し替える。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `drive_gate` | 設計の経路で PR の本文と質問を読ませ、記録する | 読ませるものが無いとき worker を起動せず、PR の取得は 2 回 | 異常系の表の行ごと |
| `open_review_again` | 処理の手順の表の 5 行 | `--reviewed` が別のコミット | CI が落ちたままでは `review_again` を返さない |
| `strip_marks` | 基準と同じ内側を除く | 印が 2 組・内側に文が足された・見出しの後ろに文がある | 何も除かない |
| `render_remained` | 同じ残った文から同じ文字列 | 残った文が 0 件のときはセクションを作らない | — |
| `verify_worker` | 合格 | 一部だけ読ませた対象のハッシュが元の全体のもの | 中身の変更・決めたファイルの外・`提示したコミット:` の変更・`not_run` |
| `push_fixed` | 行を書いてから push する順 | — | push の失敗・CI の失敗で行が残る |
| `record` | 手順 9 の順に書く | 引き継いだ残った文を、読ませなかった対象でも書く | 完了の行の書き込みの失敗で `check` が `no_record` |

## 読み手役の worker の起動指示（`contracts/worker/document-rebuild-gate.md`）

満たす決定: 決定 3・8、受け入れ条件 2・5・9

関数は持たない。`gate_prompt` が埋める差し込みの位置だけを決める。

| 差し込み | 中身 | 埋める関数 |
| --- | --- | --- |
| 読む Skill | `skills/document-rebuild/SKILL.md` の「承認の前の確認」 | `gate_prompt` |
| 素材のファイル | `approval-facts.json` のパス | `gate_prompt` |
| 読ませるファイル | `Plan` の対象ごとのファイルと立場。一部だけのときは切り出したファイル | `gate_prompt` |
| 直してよいファイル | `Plan.fixable`。`--no-doc-fix` のときは文書を除く | `gate_prompt` |
| 結果ファイルの形 | `metrics.readers` の要素に、対象・元の全体のハッシュ・立場・回・合否・確かめた差分 | `document-rebuild.result.schema.json` が持つ。起動指示は場所を指す |

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
| `gate_questions` | 形の宣言が無い・表が扱わない形 | 宣言の不足・`supported` が偽 | `forms_decide.form_of` の `FormUnsupported` | 呼び出し元へ返す | `gate`・`check` が `stopped`（`precondition`） | 宣言を直して打ち直す |

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
| `Form` | `release/forms_decide.py` | 変更 | `Form(id, label, reach, supported, why, approve: str)` | 列 `approve` を持つ |
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
| `check` | `records/reading.py` | 変更 | `check(gate: str, number: int, material: Optional[Path], root: Path, hub: Callable[[str], GitHub]) -> tuple[list[str], list[dict[str, Any]]]` | PR を 1 回取得し、その題で質問を作り直し、同じ取得の head と本文で照合する。（足りないもの, 質問）を返す |
| `material_hashes` | `records/reading.py` | 新設 | `material_hashes(text: str, expected: Optional[str]) -> tuple[str, str]` | （読ませた版のハッシュ, ファイル全体のハッシュ）。`gate.strip_marks` と同じ規則で除く |
| `row_fingerprint` | `records/reading.py` | 新設 | `row_fingerprint(row: Mapping[str, Any]) -> str` | 記録の行の JSON をキーの順を固定して作った SHA-256 |
| `_reading_check` | `records/cli.py` | 変更 | `_reading_check(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `--file` の入力を受けず、`--gate`・`--pr`・`--material`・`--questions-out` を受ける。質問をファイルへ書く |
| `_add_reading` | `records/cli.py` | 変更 | 今のまま | `check` だけ引数を分ける |

- `check`
  - 事前条件: `number` は正の整数。`material` は会話のセッションのリリースのときだけ
  - 事後条件: 足りないものは `no_record`・`no_pr`・`head_moved`・`material_changed` のどれか。判定の順は設計書の「確認の id と完了の行」の 1〜3
  - 副作用: GitHub の PR の取得 1 回・`git fetch`（今と同じ）

`gate` と `check` が同じ値を出すよう、2 つは同じ除き方を使う。`material_hashes` が `gate.strip_marks` を呼ぶのではなく、`gate.strip_marks` が `material_hashes` の除き方を使う（`records` から `docrebuild` を読まない向きを保つ）。

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
| `check` | 記録した質問と同じキーの質問を作り、通す。PR の取得は 1 回 | 完了の行の無い最後の記録の行 → `no_record` | 本文だけの変更 → `material_changed`。題だけの変更 → `no_record` |
| `material_hashes` | セクションを除いた値と全体の値が違う | 印が 2 組 → 何も除かない | — |
| `row_fingerprint` | キーの順が違っても同じ値 | — | — |

## 読ませたものの記録（`reading-docs.jsonl`）と承認資料のコピー

満たす決定: 決定 4、受け入れ条件 1・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `docs_path` | `records/reading.py` | 新設 | `docs_path() -> Path` | `reading-docs.jsonl` のパス（表 `ndf-files` の行 `reading-docs`） |
| `materials_path` | `records/reading.py` | 新設 | `materials_path(gate: str, number: int) -> Path` | コピーのパス（表 `ndf-files` の行 `reading-materials`） |
| `append_doc` | `records/reading.py` | 新設 | `append_doc(row: Mapping[str, Any]) -> dict[str, Any]` | 1 行を `jsonl.append_locked` で追記する。形は `reading-docs.schema.json` |
| `completed` | `records/reading.py` | 新設 | `completed(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]` | 確認の id から完了の行 |
| `latest_read` | `records/reading.py` | 新設 | `latest_read(rows, key: tuple[str, ...]) -> Optional[Mapping[str, Any]]` | 設計書の「読ませるものの決め方」のキーで、完了した確認の最後の読ませた行を引く |
| `remained_of` | `records/reading.py` | 新設 | `remained_of(rows, repo: str, gate: str, number: int) -> list[Mapping[str, Any]]` | 同じ PR とゲートで最後に完了した確認の `remained` の行 |
| `pending_review` | `records/reading.py` | 新設 | `pending_review(rows, repo: str, branch: str) -> Optional[Mapping[str, Any]]` | 解消していない再レビュー待ちの行 |
| `select_reads` | `records/reading.py` | 新設 | `select_reads(current: Sequence[Mapping[str, Any]], past: Iterable[Mapping[str, Any]], hashes: Mapping[str, str], carried: Iterable[str]) -> list[dict[str, Any]]` | 設計書の「`reads` の作り方」の 1〜4 で、`reads` の要素を作る |
| `add` | `records/reading.py` | 変更 | 今のまま | 追記した行を返す（今と同じ）。`gate` が指紋を作るのに使う |

- `append_doc`
  - 事後条件: 行の種類は `read`（読ませたもの）・`done`（完了）・`review_pending`・`review_resolved`・`review_cancelled` のどれか。種類ごとの必須のキーはスキーマが決める
  - 副作用: ファイルへの追記

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `append_doc` | 形が違う | 呼ぶ側の誤り | `internal.InternalError`（`invalid`） | 呼び出し元へ返す（`ReadingInvalid`） | 呼んだサブコマンドが `stopped`（`incomplete`） | 書かない。打ち直せる |
| `latest_read` などの読み取り | 新しい版の行がある | 新しい NDF が書いた | `jsonl.read_rows` の `InternalError` | 呼び出し元へ返す | `stopped`（`precondition`） | NDF を上げる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `select_reads` | 今回の結果がある | 完了の行が無くても候補に入れる |
| `select_reads` | 過去の結果だけ | 完了した確認の行で、ハッシュが今と同じものだけを候補にする |
| `select_reads` | 候補が複数の確認にまたがる | 最も新しい確認を選び、その中の最後の回を使う（回（`round`）の値の大小で選ばない） |
| `select_reads` | 対象が `carried` にある | `round: 2`・`result: remained` |

前に残った文の扱いの判定（「今の中身に無い」の判定・同じ指摘とみなす基準・全体を読ませたときの扱い）は、設計書の「未確認」の (3) のとおり計画で決める。

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
| `record_reads` | `docrebuild/drive.py` | 新設 | `record_reads(g: git.Git, branch: str, report: Mapping[str, Any], targets: Sequence[Target]) -> None` | 読み手役が読んだ文書を、確認の id・ブランチ・パス・blob・立場・回・合否で追記し、完了の行を書く |
| `_checked` | `docrebuild/drive.py` | 変更 | 今のまま | `ok` を返す前に `record_reads` を打つ |
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
| `record_reads` | `records.reading.append_doc`（この変更で新設）・`Git.blobs` | 上の「読ませたものの記録」の表 | 使う。blob の SHA は `git hash-object` の値を `Git` の `run` で得る |

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
| `StageDef.mvv_before` | `progress/stages.py` | 新設 | `mvv_before(self, step_id: str) -> Optional[str]` | 承認ゲートの直前のステップの id。種類 `reading` のステップを飛ばす |
| `StageDef.reading_for` | `progress/stages.py` | 新設 | `reading_for(self, gate: str) -> Optional[StepDef]` | 同じ工程で、そのゲートの前の種類 `reading` のステップ |

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
| 工程の定義 | `design-reading` が `design-mvv` と `design-gate` の間、`release-reading` が `release-mvv` と `release-gate` の間 | 順を入れ替えると定義の検査で落ちる |
| `StageDef.gates` | 読み手役のステップを足しても承認ゲートは 1 つ | — |
| `StageDef.mvv_before` | `design-gate` から `design-mvv` を返す | 読み手役のステップが無い古い定義でも直前のステップを返す |

## 工程を進める仕組み（`ndf.progress.run`・`model`・`gate.answering`・`gate.asking`・`gate.entry`）

満たす決定: 決定 2・6、受け入れ条件 3・6・7・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `reading_skip` | `progress/model.py` | 新設 | `reading_skip(done: Mapping[str, Mapping[str, Any]], before: Optional[str], gate: Optional[str], waiting_gate: bool, in_lane: bool, mode: str) -> bool` | 読み手役のステップを打たずに済ませるか。MVV 判定が同じゲートを省くとき（承認ゲートの待ちの記録があるときは除く）と、設計書の「版上げのスプリント」の判定表の 2 行目のとき |
| `_running_move` | `progress/run.py` | 変更 | 今のまま | 承認ゲートの MVV 判定を `s.mvv_before` で探す。読み手役のステップを `reading_skip` で済ませる |
| `_consume` | `progress/run.py` | 変更 | 今のまま | MVV 判定を `s.mvv_before` で探す |
| `reading_args` | `progress/run.py` | 新設 | `reading_args(v: View, s: stages.StageDef, step: stages.StepDef) -> list[str]` | 工程の記録から `--reviewed <コミット>` と `--no-doc-fix` を足す |
| `_run_step` | `progress/run.py` | 変更 | 今のまま | 種類 `reading` のステップの argv に `reading_args` を足す |
| `review_again_drafts` | `progress/run.py` | 新設 | `review_again_drafts(v: View, s: stages.StageDef, report: Mapping[str, Any]) -> list[model.EventDraft]` | `outputs.review_again` を受けて、設計のレビュー・MVV 判定・読み手役のステップを未了に戻す事象と、戻したことの記録を作る |
| `_step_draft` | `progress/run.py` | 変更 | `_step_draft(...) -> list[model.EventDraft]`（1 件から並びへ変える） | `review_again` があれば `review_again_drafts` を続けて返す |
| `Target` | `progress/gate/asking.py` | 変更 | `Target(head, material, body: Optional[str] = None, question_key: Optional[str] = None)` | 回答が固定する対象に PR の本文のハッシュと質問のキーを足す。`differs`・`as_dict`・`from_dict` も同じ項目を扱う |
| `github_observe` | `progress/gate/answering.py` | 変更 | 今のまま | 同じ 1 回の取得で、head に加えて本文のハッシュと題から作った質問のキーを読む |
| `recheck` | `progress/gate/answering.py` | 変更 | 今のまま | 待ちへ戻すとき、同じトランザクションで `_reading_reset` の事象を足す |
| `_reading_reset` | `progress/gate/answering.py` | 新設 | `_reading_reset(v: run.View, stage: str, gate: str) -> list[model.EventDraft]` | 同じ工程の、そのゲートの前の読み手役のステップを未了に戻す事象 |
| `reread` | `progress/gate/answering.py` | 新設 | `reread(st: store.Store, machine_id: str, cwd: Path, env: run.Env, observe: Observe) -> dict[str, Any]` | 設計書の「待ちの出し直し」の 1〜3 |
| `_cmd_reread`・`reread_parser` | `progress/gate/entry.py` | 新設 | `_cmd_reread(a, cwd, st) -> dict[str, Any]`・`reread_parser() -> argparse.ArgumentParser` | `ndf progress reread <ID>` を受ける |
| `progress_main` | `progress/gate/entry.py` | 変更 | 今のまま | `reread` を `_cmd_reread` へ渡す |

`reread` は `answering.py` に置き、受け口は `entry.py` に置く。`run.py` から `answering` を読むと循環するためである（`answering` は `run` を読む）。

- `reread`
  - 事前条件: 承認ゲートが人の回答を待っている
  - 事後条件: 待ちの記録が新しい質問の id で書かれ、読み手役のステップが打ち直されている。`review_again` なら待ちの知らせを返さない
  - 副作用: 状態の DB への事象の追記（書く前に読んだ `rev` で書く）、`ndf document-rebuild gate` の子のプロセス

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `reread` | 承認ゲートが待っていない | 打つ時点の誤り | `run.decide` の手 | 止める | `stopped`（`precondition`） | 何も書かない |
| `reread` | 書く前に回答が書かれた | 競合 | 読んだ `rev` と今の `rev` | 何も書かずに終える | `ok`（回答を読む案内） | 書かれた回答を捨てない |
| `reread` | 読み手役のステップが止まった | gate の `stopped` | ステップの結果 | 呼び出し元へ返す | そのステップの止まり | 打ち直せる |
| `reread` | 工程の定義に読み手役のステップが無い | 古い定義を読み込んだスプリント | `StageDef.reading_for` が None | `ndf document-rebuild gate` を直接打つ | gate の結果に従う | `--reviewed`・`--no-doc-fix` を渡すかは計画で決める（設計書の「未確認」の (7)） |
| `_step_draft` | 読み手役のステップの出力が規約に合わない | gate の誤り | `definition.validate_io` | 止める | `bad_report` | 打ち直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `reading_skip` | MVV 判定が同じゲートで `skip: true`・承認ゲートの待ちの記録が無い | 済ませる |
| `reading_skip` | MVV 判定が `skip: true`・承認ゲートの待ちの記録がある | 打つ |
| `reading_skip` | `gate: release`・モードが `release-bump` | 打つ |
| `reading_skip` | `gate: release`・計画のレーンの行にある | 済ませる |
| `reading_skip` | `gate: release`・計画の外 | 打つ |
| `reading_skip` | `gate: design` | MVV 判定の行だけで決める（レーンでも打つ） |
| `reading_args` | 工程の記録に `review_again` があり、そのコミットを含む head で設計のレビューが済んだ | `--reviewed <コミット>` を足す |
| `reading_args` | 同じ承認ゲートの前の `review_again` が 1 件以上 | `--no-doc-fix` も足す |

レーンの行にあるかは `asking.release_request_of` と同じ `lane.sprint_row` で決める。`reading_skip` は純粋な関数に保つため、`run.py` が先に `in_lane` を引いて渡す。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `reread` と `ndf answer` | 承認ゲートの事象 | `run.append` のトランザクションと `rev` の照合（`recheck` と同じ） | 回答が先なら出し直さない |
| `reread` と `next` | 作業の排他 | `_work_lock`（今と同じ） | 取れなければ待つ |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `reading_skip` | `model.mvv_skip` | `grep -n "def mvv_skip" src/ndf/progress/model.py` | 中で使う。レーンとモードの判定を足す |
| `reading_args` | `invoke.stage_run` の `args` | `grep -n "def stage_run" src/ndf/progress/invoke.py` | 使う。argv の後ろに足す |
| `review_again_drafts` | `answering._undropped`・`recheck` の待ちへ戻す事象の形 | `grep -n "def _undropped\|def recheck" src/ndf/progress/gate/answering.py` | 事象の形（`STEP_EVENT` の `status`）を同じにする。`run` から `answering` を読めないので、形は `run` の中で組む |
| `_reading_reset` | 同上 | 同上 | 使う |
| `reread` | `answering.recheck`・`run.advance` | `grep -n "def advance" src/ndf/progress/run.py` | 待ちの書き換えは `recheck` と同じトランザクションの形、ステップの実行は `advance` を使う |
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
| `render_trail` | `release/approval_decide.py` | 変更 | `render_trail(answer: str, sha: str, by: str, material_sha256: Optional[str]) -> str` | 担い手が利用者のときだけ行 `回答した資料:` を足す |
| `parse_request` | `release/approval_decide.py` | 新設 | `parse_request(order: int, author: str, body: str) -> Optional[Presentation]` | 提示のコメントを、順・head・資料のハッシュとして読む |
| `parse` | `release/approval_decide.py` | 変更 | 今のまま | `Trail` に担い手と資料のハッシュを足して読む |
| `judge` | `release/approval_decide.py` | 変更 | `judge(found: Sequence[Trail], presented: Sequence[Presentation], head: str) -> Judgment` | 設計書の表の「判定」の行のとおり、担い手で分けて判定する |
| `present` | `release/trail.py` | 変更 | 今のまま | 最新の提示の資料のハッシュが同じで回答があるときだけ提示しない |
| `answer` | `release/trail.py` | 変更 | `answer(hub, number, value, by, presented, material_sha256: Optional[str], writer=...)` | `--by user` は資料のハッシュを照合し、`--by mvv` は受けない |
| `judged`・`read` | `release/trail.py` | 変更 | 今のまま | 提示のコメントも読んで `judge` へ渡す |
| `_answer_args`・`cmd_answer` | `release/cli.py` | 変更 | 今のまま | `--material-sha256` を足す |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `answer` | `--by user` で資料のハッシュが無い・最新の提示と違う | 提示し直した資料への回答でない | 最新の提示との比較 | 呼び出し元へ返す（`TrailError`） | `stopped`（今の `TrailError` と同じ形） | 書かない。新しい資料に回答を取り直す |
| `answer` | `--by mvv` に資料のハッシュが渡された | 呼ぶ側の誤り | 引数 | 呼び出し元へ返す（`TrailError`） | `stopped` | 書かない |
| `judge` | この変更の前の形式で書かれた利用者の証跡（資料のハッシュの行が無い） | 変更の前の回答 | `Trail` の資料のハッシュが None | 数えない | `post_status` が承認済みにしない | 提示し直して回答を取り直す |

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

工程「版上げ」のステップの並びは設計書の「版上げのスプリント」の表のとおりで、`stages/release-bump.json` に書く。`bump-assemble` は、テンプレート `release-bump`（`contracts/templates/release-bump.json`）のプランである。このプランは、範囲の引数を作るコマンド・`ndf release-steps materials`・`assemble` を並べる。`bump-present`・`bump-record` は `ndf.script` のステップである。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `LaneRow.requests` | `progress/lane/types.py` | 新設 | `requests(self) -> list[str]`（プロパティ） | 行の入力 `kind: release` のリクエストの ID の並び。ほかの行は空 |
| `release_commands` | `progress/lane/launch.py` | 新設 | `release_commands(plan: str, lane: str, lane_table: str, row: LaneRow) -> list[dict[str, Any]]` | `ndf progress new <名前> --mode release-bump --release-requests ... --parent <レーン>`・`lanes row start`・`progress next` を組む |
| `start_commands` | `progress/lane/launch.py` | 変更 | 今のまま | `kind: release` の行を `release_commands` へ渡す |
| `_workflow_args` | `progress/lane/launch.py` | 変更 | 今のまま | `kind: release` の行は `--release-requests` の並びを返す |
| `_sprint_new`・`_cmd_new` | `progress/command.py` | 変更 | 今のまま | `--release-requests` を受け、モード `release-bump` は `--issues` 無しで作る。ほかのモードに `--release-requests` を渡したら断る |
| `_request_fields`・`_plan_request` | `progress/gate/release.py` | 変更 | 今のまま | モードが `release-bump` なら種類 `bump-release` で、版上げの PR・`outputs.material` のパス・まとめたリクエストを書く |

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
| 工程「版上げ」の定義 | 並びが `bump-assemble`・`release-mvv`・`release-reading`・`bump-present`・`release-gate`・`release`・`bump-record` | 入れ替えると定義の検査で落ちる |
| `_plan_request` | 版上げのスプリントは `bump-release` を書く | `lane-release` を書くと落ちる |
| `bump-record` の並び | まとめたリクエストを 1 回の `lanes release` で閉じ、リクエストごとに `answered` を書く | 変更を求められたら `--result rejected` |
