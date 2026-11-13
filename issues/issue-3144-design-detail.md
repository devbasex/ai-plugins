# #3144 の詳細設計: 詳細設計の工程・設計単体のレビュー・詳細設計の検査のファイル配置と関数

設計文書 `issues/issue-3144-design.md` の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。節の形は設計文書の「入出力の契約」の「詳細設計の文書の形」に従う。

書く関数は、新設する関数と、シグネチャ・責務・失敗の扱いのいずれかを変える関数だけである（要求の前提 3）。関数名・定数名・パスは、2026-11-13 にこの worktree の HEAD で `grep -n` を打って在ることを確かめた（新設のものを除く）。

**同じ事実は 1 か所のデータに置き、読む側はそこから引く**（設計文書の決定 12）。設計文書の種類と名前は契約 `contracts/design-docs.json`、詳細設計を通るモードは工程の分類 `WF_STAGE_MATRIX`、詳細設計の節の見出しは雛形 `detail-template.md` が持つ。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.ndf/
└── glossary.json                          # このリポジトリの用語チェックの宣言
docs/
├── glossary.md
└── glossary/glossary.json
plugins/ndf/
├── contracts/
│   └── design-docs.json *                 # 設計文書の種類と名前の契約（唯一の置き場）
├── scripts/
│   ├── design-survey.py                   # 検査のエントリポイント
│   ├── glossary.py
│   ├── lib/
│   │   ├── design_detail.py *             # 詳細設計の文書の 5 規則の判定
│   │   ├── doc_kinds.py *                 # 設計文書の種類の契約の唯一の読み手
│   │   ├── stage-skills.json
│   │   └── transcript_agents.py
│   ├── supervise_lib/
│   │   ├── design_steps.py                # 設計のプランの段（書く・PR・ゲート）
│   │   ├── paths.py
│   │   ├── procedures.py
│   │   ├── prompts.py
│   │   ├── sprint_revise.py
│   │   └── sprint_waves.py
│   └── tests/
│       ├── fixtures/supervise-contract/plans/sprint/1-design-16.json
│       ├── test_design_survey.py
│       ├── test_doc_kinds.py *
│       ├── test_supervise_limits.py
│       └── test_supervise_new.py
└── skills/
    ├── cross-review/
    │   ├── docs/08-review-categories.md
    │   └── scripts/{classifications.py, review_lib/categories.py, review_lib/review_focus.py}
    ├── design/
    │   ├── SKILL.md
    │   └── references/{detailed-design.md *, detail-template.md *, deliverables.md, design-template.md}
    ├── development-workflow/
    │   ├── SKILL.md
    │   ├── references/{agent-layers.md, parallel-work.md, projects-tracking.md, stage-notes.md, workflow-modes.md}
    │   ├── scripts/lib/workflow-common.sh
    │   └── tests/test_stage_check.py
    ├── implementation-plan/SKILL.md
    └── quality-gates/references/design-match.md
```

依存の向きは、エントリポイント → `lib/` の部品、`sprint_waves` → `design_steps` / `procedures` → `prompts` / `paths` の一方向である。`lib/doc_kinds.py` は標準ライブラリだけを使い、ほかの部品を読まない。`lib/design_detail.py` は `md`・`design_docs`・`doc_kinds` だけを読み、`design-survey.py` も `supervise_lib` も読まない。循環は作らない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/contracts/design-docs.json` | 新設 | 設計文書の置き場と、種類ごとの名前の末尾・行数の上限を見るか・用語チェックの既定の対象か・設計 PR の文書か・雛形のパスを持つ | 無し |
| `plugins/ndf/scripts/lib/doc_kinds.py` | 新設 | `design-docs.json` を読み、真偽の名前ごとの末尾・パスの形・パスの種類・雛形のパスを返す | 無し（標準ライブラリだけ） |
| `plugins/ndf/scripts/lib/design_detail.py` | 新設 | 詳細設計の文書 1 本を節・異常系・再利用・ファイルの規則で判定し、指摘の行を返す。節の一覧は雛形から読む | `md`・`design_docs`・`doc_kinds` |
| `plugins/ndf/skills/design/references/detail-template.md` | 新設 | 詳細設計の文書の雛形（10 の `##` 見出しと各節の表の列）。節の見出しの唯一の置き場 | 無し |
| `plugins/ndf/scripts/design-survey.py` | 変更 | まとまりごとに詳細設計の文書を探して判定を呼び、`--require-detail` で文書の欠けを指摘する | `design_detail`・`design_docs`・`design_results`・`md`・`step_result` |
| `plugins/ndf/scripts/supervise_lib/design_steps.py` | 変更 | 工程の分類で詳細設計が必須のモードだけ、設計単体のレビュー・直し・詳細設計の段を `design` の後に並べる | `paths`・`prompts`・`decl` |
| `plugins/ndf/scripts/supervise_lib/prompts.py` | 変更 | 設計単体のレビューと直しの指示の文を持ち、設計との突き合わせの指示に詳細設計を足す | 無し |
| `plugins/ndf/scripts/supervise_lib/paths.py` | 変更 | `cross-review` のレビュー観点のファイルと `workflow-common.sh` のパスを持つ | 無し（既存の `DRIVES`・`SKILLS` から求める） |
| `plugins/ndf/scripts/supervise_lib/procedures.py` | 変更 | 外部の調査の検査の段に `--require-detail` を付けるかを引数で受ける | `paths`・`prompts` |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | 詳細設計を通るモードかを `design_steps.detail_design_runs` で引き、`survey_steps` へ渡す | `design_steps`・`procedures` |
| `plugins/ndf/scripts/supervise_lib/sprint_revise.py` | 変更 | 出し直しの指摘を詳細設計の段の指示にも入れる | `revise_note` |
| `plugins/ndf/scripts/glossary.py` | 変更 | 宣言を作るときの用語チェックの対象（`DEFAULT_PATHS`）を `doc_kinds` から導く | `doc_kinds` |
| `.ndf/glossary.json` | 変更 | このリポジトリの用語チェックの対象に詳細設計の文書を足す | 無し |
| `plugins/ndf/scripts/lib/transcript_agents.py` | 変更 | 工程「詳細設計」をフェーズ「設計」へ写す | 無し |
| `plugins/ndf/scripts/lib/stage-skills.json` | 変更 | 工程「詳細設計」に `document-style` を届ける | 無し |
| `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh` | 変更 | 工程の分類に「詳細設計」の行を「設計」の直後へ足す | 無し |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 変更 | 工程表に「詳細設計」の行と工程の決まりを足す | 無し |
| `plugins/ndf/skills/development-workflow/references/workflow-modes.md` | 変更 | 標準フローの図に設計 → 詳細設計 → ドキュメント再構成の経路を足す | 無し |
| `plugins/ndf/skills/development-workflow/references/projects-tracking.md` | 変更 | ボードの値の対応表に「詳細設計」の行を足す | 無し |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | 工程とフェーズの表に「詳細設計 → 設計」を足す | 無し |
| `plugins/ndf/skills/development-workflow/references/parallel-work.md` | 変更 | 動く単位の表に「詳細設計」を設計のプランの中の段として足す | 無し |
| `plugins/ndf/skills/development-workflow/references/stage-notes.md` | 変更 | 詳細設計の工程を置いた理由を書く | 無し |
| `plugins/ndf/skills/design/SKILL.md` | 変更 | 手順に「詳細設計を書く」を足し、設計のプランの段から呼ばれたときの止め方を書く。「進行を記録する」の契機に、詳細設計を書き始めるときの `projects-sync.sh <課題> stage "詳細設計"` を足す。設計のプランの段（`/ndf:design #<番号> 詳細設計`）から呼ばれたときは、工程「設計」を打ち直さない | 無し |
| `plugins/ndf/skills/design/references/detailed-design.md` | 新設 | 10 の項目の書き方・省く書き方・既存の参照との分担を持つ。節の見出しと表の列は並べ直さず雛形を指す | `detail-template.md` |
| `plugins/ndf/skills/design/references/deliverables.md` | 変更 | パッケージ・モジュール構成の行に「詳細設計を通るモードでは詳細設計の文書に書く」を添える | 無し |
| `plugins/ndf/skills/design/references/design-template.md` | 変更 | 詳細設計の文書の雛形 `detail-template.md` へのリンクを置く（雛形を写さない） | `detail-template.md` |
| `plugins/ndf/skills/cross-review/scripts/classifications.py` | 変更 | 定数 `DESIGN_DOC_FILE_SUFFIXES` を消し、行数の検査とドメインモデルの有無の対象を `doc_kinds` から引く | `doc_kinds`（関数の中で `scripts/lib` を `sys.path` へ足す） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/categories.py` | 変更 | 定数 `DESIGN_DOC_SUFFIXES` を消し、設計 Pull Request の文書かを `doc_kinds` から引く | `doc_kinds` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/review_focus.py` | 変更 | 設計 Pull Request の詳細レビューの観点に詳細設計の 6 観点を足す | 無し |
| `plugins/ndf/skills/cross-review/docs/08-review-categories.md` | 変更 | `design` の分類の説明に詳細設計の文書と 6 観点を足す | 無し |
| `plugins/ndf/skills/implementation-plan/SKILL.md` | 変更 | 対象ファイルを詳細設計のファイル配置から引き写す手順を足す | 無し |
| `plugins/ndf/skills/quality-gates/references/design-match.md` | 変更 | 突き合わせの対象に詳細設計のファイル配置と関数の仕様を足す | 無し |
| `docs/glossary/glossary.json` | 変更 | 「詳細設計」「異常系の処理」の出所を `detailed-design.md` へ移す | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` で作り直す | `docs/glossary/glossary.json` |
| `plugins/ndf/scripts/tests/test_design_survey.py` | 変更 | 5 規則・雛形の読み取り・`--require-detail` のテストを足す | `design-survey.py` |
| `plugins/ndf/scripts/tests/test_doc_kinds.py` | 新設 | 契約の読み取り・種類の判定・読む側（用語チェック・行数の検査・設計 PR の分類）が契約から引くことのテスト | `doc_kinds`・`glossary`・`classifications`・`categories` |
| `plugins/ndf/scripts/tests/test_supervise_new.py` | 変更 | 設計のプランの段の並び・工程の分類から引くモードごとの有無・工程の分類を読めないときの止まり方・出し直しの指摘のテストを足す | `supervise_lib` |
| `plugins/ndf/scripts/tests/test_supervise_limits.py` | 変更 | `DESIGN_ROUTE` に新しい段を足す | `supervise_lib` |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/1-design-16.json` | 変更 | `standard` の設計のプランの書き出しを作り直す | 無し |
| `plugins/ndf/skills/development-workflow/tests/test_stage_check.py` | 変更 | 工程表と対応表の行名を工程の分類と突き合わせるテストを足す | `workflow-common.sh` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図にある。ここでは描き直さず、置き場ごとの最上位の名前だけを並べる（新設と変更だけ）。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/doc_kinds.py` | モジュール `doc_kinds` | `DocKindsError`・`DECLARATION`・`use`・`_table`・`suffixes`・`patterns`・`kind_of`・`template_path`（すべて新設） |
| `plugins/ndf/scripts/lib/design_detail.py` | モジュール `design_detail` | `DetailTemplateError`・`sections`・`is_detail`・`check_detail`・`_section_table`（すべて新設） |
| `plugins/ndf/scripts/design-survey.py` | クラス `Doc` | `__init__`（`text` を持たせる） |
| `plugins/ndf/scripts/design-survey.py` | モジュール `design-survey` | `detail_items`・`_result_files`（新設）、`cmd_check`・`build_parser`（変更） |
| `plugins/ndf/scripts/supervise_lib/design_steps.py` | モジュール `design_steps` | `DESIGN_REVIEW_NOTE`・`detail_design_runs`・`detail_steps`（新設）、`writing_steps`・`pr_steps`（変更） |
| `plugins/ndf/scripts/supervise_lib/prompts.py` | モジュール `prompts` | `DESIGN_REVIEW_PROMPT`・`DESIGN_FIX_PROMPT`（新設）、`DESIGN_MATCH_PROMPT`（変更） |
| `plugins/ndf/scripts/supervise_lib/paths.py` | モジュール `paths` | `REVIEW_FOCUS`・`WORKFLOW_COMMON`（新設） |
| `plugins/ndf/scripts/supervise_lib/procedures.py` | モジュール `procedures` | `survey_steps`（変更） |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | モジュール `sprint_waves` | `plan_sprint_design`（変更。`survey_steps` へ渡す引数だけ） |
| `plugins/ndf/scripts/supervise_lib/sprint_revise.py` | モジュール `sprint_revise` | `with_revise_note`（変更） |
| `plugins/ndf/scripts/glossary.py` | モジュール `glossary` | `DEFAULT_PATHS`（変更。`doc_kinds.patterns("glossary")` から導く） |
| `plugins/ndf/skills/cross-review/scripts/classifications.py` | モジュール `classifications` | `DESIGN_DOC_FILE_SUFFIXES`（削除）、`oversized_design_docs`・`has_domain_model`（変更） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/categories.py` | モジュール `categories` | `DESIGN_DOC_SUFFIXES`（削除）、`_is_design_doc_path`（変更） |

`plan_sprint_design` は、シグネチャ・責務・失敗の扱いを変えない（`survey_steps` を呼ぶ引数を 1 つ足すだけ）。そのため次の「関数の仕様」には書かない。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `use` | `lib/doc_kinds.py` | 新設 | `use(path: str \| Path \| None) -> None` | 読む契約のファイルを差し替え、読んだ表を捨てる（テストが使う） | 無し | `None` ならプラグインの契約へ戻る | モジュールの読み込み先と控えを書き換える |
| `_table` | `lib/doc_kinds.py` | 新設 | `_table() -> tuple[str, tuple[dict, ...]]` | 契約を 1 プロセスに 1 回読み、形を確かめて置き場と種類の行を返す | 無し | 置き場と、契約の並びのままの種類の行。形の誤りは `DocKindsError` | 契約のファイルを読む（初回だけ） |
| `suffixes` | `lib/doc_kinds.py` | 新設 | `suffixes(flag: str) -> tuple[str, ...]` | 真偽の名前（`line_limit`・`glossary`・`review`）が真の種類の名前の末尾を返す | 無し | 契約の並びの末尾。知らない名前は `DocKindsError` | 無し |
| `patterns` | `lib/doc_kinds.py` | 新設 | `patterns(flag: str) -> list[str]` | `suffixes(flag)` を置き場と合わせたパスの形（`issues/*-design.md` など）で返す | 無し | `<置き場>/*<末尾>` の一覧 | 無し |
| `kind_of` | `lib/doc_kinds.py` | 新設 | `kind_of(rel: str, placed: bool = True) -> str \| None` | パスの名前の末尾から設計文書の種類を返す | 無し | 最も長く一致する末尾の種類。`placed` が真なら、親のディレクトリが置き場でないパスは `None` | 無し |
| `template_path` | `lib/doc_kinds.py` | 新設 | `template_path(kind: str) -> Path` | 種類の行の雛形のパスを、プラグインの根からの絶対パスで返す | 無し | パスを返すだけで在るかは見ない。雛形を持たない種類は `DocKindsError` | 無し |
| `sections` | `lib/design_detail.py` | 新設 | `sections() -> tuple[str, ...]` | 詳細設計の雛形の `##` 見出しを並びのまま返す | 無し | 1 つ以上の見出し。読めない・0 個・規則が名指す節が欠ける雛形は `DetailTemplateError` | 雛形のファイルを読む（初回だけ） |
| `is_detail` | `lib/design_detail.py` | 新設 | `is_detail(path: str) -> bool` | パスの名前が詳細設計の文書（`issue-<番号>-design-detail.md`）かを返す | 無し | 名前が `design_docs.DESIGN_NAME` に合い、`doc_kinds.kind_of(path, placed=False) == "detail"` のときだけ真 | 無し |
| `check_detail` | `lib/design_detail.py` | 新設 | `check_detail(path: str, text: str, result_files: set[str] \| None, why: str = "") -> list[dict]` | 1 本の詳細設計の文書を `detail_section`・`detail_failure`・`detail_reuse`・`detail_file` で判定する | `text` は `path` の中身。`result_files` は頭の設計文書の「設計の結果」の触るファイルで、読めなければ `None` と理由 `why`。`None` で `why` が空なら照合する先が無い（`legacy-refactor` の書式）ことを表し、`detail_file` を当てない | 指摘の行（`name`・`rule`・`text`）を、規則の順・行の順で返す。節の並びは `sections()`。指摘が無ければ空の一覧 | 無し |
| `_section_table` | `lib/design_detail.py` | 新設 | `_section_table(text: str, title: str) -> tuple[md.Section \| None, md.Table \| None, str \| None]` | `##` の見出しが `title` の節と、その節の最初の表と、`省く:` の理由を返す | 無し | 節が無ければ `(None, None, None)`。理由は `省く:` の後の字を空白で詰めたもの（無ければ `None`） | 無し |
| `detail_items` | `design-survey.py` | 新設 | `detail_items(group: list[Doc], require: bool) -> list[dict]` | まとまりの中の詳細設計の文書ごとに `check_detail` を当て、`require` なら文書の欠けを指摘する | `group` は `survey_groups` が返した 1 つのまとまり | 指摘の行の一覧。`require` で詳細設計の文書が無いまとまりには `detail_missing` の行が 1 つ。雛形か契約を読めなければ `StepError`（終了コード 2） | 無し |
| `_result_files` | `design-survey.py` | 新設 | `_result_files(head: Doc \| None) -> tuple[set[str] \| None, str]` | 頭の設計文書の「設計の結果」から、扱いが「実装する」の行の触るファイルを集める | 無し | 読めれば `(ファイルの集合, "")`。頭の設計文書が `legacy-refactor` の書式（「設計の結果」の節が無く、「現状の構造」と「変更後の構造」の節がある）なら `(None, "")`。それ以外で読めなければ `(None, 理由)` | 無し |
| `Doc.__init__` | `design-survey.py` | 変更 | `Doc(path: str, text: str)` | 1 つの文書から既存の読み取りを行い、詳細設計の判定のために `text` を持つ | 既存どおり | 既存の属性に `self.text` が加わる | 無し |
| `cmd_check` | `design-survey.py` | 変更 | `cmd_check(a: argparse.Namespace) -> None` | まとまりごとに既存の 4 規則と詳細設計の 5 規則を当て、1 行の JSON と終了コードで返す | `a.issue` か `a.file` のどちらか。`a.require_detail` は真偽 | 指摘が無ければ終了コード 0、あれば 1。`metrics` に `detail_docs` と規則ごとの件数が加わる | 標準出力へ 1 行の JSON・プロセスの終了コード・git の読み取り（`--issue` のとき） |
| `build_parser` | `design-survey.py` | 変更 | `build_parser() -> argparse.ArgumentParser` | `check` に `--require-detail` を足す | 無し | `--require-detail` は `--issue`・`--file` のどちらとも組め、既定は偽 | 無し |
| `detail_design_runs` | `supervise_lib/design_steps.py` | 新設 | `detail_design_runs(mode: str) -> bool` | 工程の分類でモード `mode` の「詳細設計」が必須（`R`）かを返す | `mode` は `supervise.py new` が受けたモード | `wf_stage_class <mode> 詳細設計` が `R` なら真、ほかの区分なら偽。モードごとに 1 回だけ bash を起動する（`functools.lru_cache`）。工程の分類を引けなければ `DeclError` | bash を 1 回起動して `workflow-common.sh` を読む |
| `detail_steps` | `supervise_lib/design_steps.py` | 新設 | `detail_steps(n: int) -> list[dict]` | `design-review`・`design-review-check`・`design-fix`・`detail-design` の 4 段を組む | `n` は課題の番号 | 段の遷移は設計文書の決定 4 の表のとおりで、最後の段の `next` は `rebuild` | 無し |
| `writing_steps` | `supervise_lib/design_steps.py` | 変更 | `writing_steps(a, n: int, copy: str) -> list[dict]`（変えない） | 設計の工程に入る前の用語集の検査・要求・設計文書を書く段を並べ、詳細設計を通るモードでは `detail_steps` を続ける | `a.mode` を持つ | `detail_design_runs(a.mode)` が真なら `design` の `next` が `design-review` で、`design` の指示に「詳細設計は後の段が書く」が加わる。偽なら今と同じ並び | 無し |
| `pr_steps` | `supervise_lib/design_steps.py` | 変更 | `pr_steps(a, n: int, doc: str) -> list[dict]`（変えない） | 設計 PR を出す段を組み、本文へ足すファイルに設計単体のレビューの結果を加える | 既存どおり | `pr` の段の `append` が `[DESIGN_GLOSSARY_NOTE, DESIGN_REVIEW_NOTE]` になる | 無し |
| `survey_steps` | `supervise_lib/procedures.py` | 変更 | `survey_steps(n: int, next_id: str, require_detail: bool = False) -> list` | 外部の調査の検査の 3 段を組み、詳細設計を通るモードでは検査に `--require-detail` を付ける | 既存どおり | `require_detail` が真なら `survey-check`・`survey-recheck` の `cmd` が `--require-detail` で終わり、`fix-survey` の指示に詳細設計の規則の直し方が加わる。偽なら今と同じ | 無し |
| `with_revise_note` | `supervise_lib/sprint_revise.py` | 変更 | `with_revise_note(plan: dict, a, n: int, doc: str) -> dict`（変えない） | 出し直しの指摘を要求・設計に加えて詳細設計の段の指示にも入れる | 既存どおり | プランに `detail-design` の段があれば、その指示の末尾に `revise_note.prompt_text(data, <詳細設計の文書>)` が加わる。無ければ今と同じ | 入力のファイルを読む（既存の `revise_note.read_input`） |
| `oversized_design_docs` | `cross-review/scripts/classifications.py` | 変更 | `oversized_design_docs(root, paths, limit: int = DESIGN_DOC_MAX_LINES) -> list[dict]`（変えない） | 行数の上限を見る種類の文書のうち、上限を超えるものを返す | 既存どおり | 対象は `doc_kinds.kind_of(p)` が `suffixes("line_limit")` の種類のパス。今の 2 種類に `detail` が加わる | 文書を読む（既存）・初回に `scripts/lib` を `sys.path` へ足す |
| `has_domain_model` | `cross-review/scripts/classifications.py` | 変更 | `has_domain_model(root, paths) -> bool`（変えない） | 変えた設計文書のどれかに `## ドメインモデル` があるかを返す | 既存どおり | 対象は `kind_of(p, placed=False)` が `suffixes("line_limit")` の種類のパス（今の `DESIGN_DOC_FILE_SUFFIXES` と同じ集合に `detail` が加わる） | 文書を読む（既存）・初回に `scripts/lib` を `sys.path` へ足す |
| `_is_design_doc_path` | `cross-review/scripts/review_lib/categories.py` | 変更 | `_is_design_doc_path(path: str) -> bool`（変えない） | パスが設計 Pull Request の文書かを返す | 無し | `doc_kinds.kind_of(path)` が `suffixes("review")` の種類のときだけ真 | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `use` | 渡したパスが無い | テストの誤り | 次の `_table` の読み取り | 呼び出し元へ返す（`_table` が `DocKindsError`） | テストの失敗 | 後始末なし |
| `_table` | 契約を読めない | ファイルが無い・UTF-8 でない・JSON でない・同じ種類が 2 つ・`version` が違う・真偽の値が真偽でない | 読み取りと形の確かめ（`limits.read_table` に倣う） | 止める（`DocKindsError` を投げる。既定値で続けない） | 呼び出し元の扱いで出る。`design-survey.py` は `stopped`・終了コード 2。`glossary.py` と `cross-review` は取り込みの時点か関数の中で例外になり、終了コード 1 | リリース物の欠けで、`test_doc_kinds.py` が落とす。契約を直して打ち直せる |
| `sections` | 雛形を読めない | `template_path("detail")` のファイルが無い・UTF-8 でない | 読み取り | 止める（`DetailTemplateError` を投げる） | `design-survey.py` は `stopped`・終了コード 2（`detail_items` が `StepError` にする） | 雛形を直して打ち直せる |
| `sections` | 雛形の `##` 見出しが 0 個か、規則が名指す 4 節が無い | 雛形の見出しを変えた・消した | `md.headings` の結果を数える | 止める（同上） | 同上。`text` に欠けた節の名前 | 同上 |
| `detail_items` | 契約か雛形を読めない | `DocKindsError`・`DetailTemplateError` | `sections` と `kind_of` の呼び出し | 止める（`StepError(<理由>, 2)` にして投げる。既存の `main_with` が `stopped` にする） | `stopped`・終了コード 2 | 後始末なし。原因を直して打ち直せる |
| `cmd_check` | git を読めない | worktree でない・HEAD が無い（`--issue` のとき） | 既存の `require_repo` と `find_closed` | 止める（既存どおり） | `stopped` の 1 行の JSON・終了コード 2 | 後始末なし。原因を直して打ち直せる |
| `cmd_check` | 文書を読めない | `--file` のパスが無い・UTF-8 でない | 既存の `read_files` | 止める（既存どおり） | `stopped`・終了コード 2 | 同上 |
| `cmd_check` | 文書が 1 つも無い | `--issue` の番号の文書が HEAD に無い | 既存の `find_closed` の戻り値 | 止める（既存どおり） | `stopped`・終了コード 3 | 同上 |
| `cmd_check` | 詳細設計の規則に指摘がある | 節の欠け・異常系の行の欠け・再利用の行の欠け・触るファイルの外のパス・詳細設計の文書の欠け | `detail_items` | 呼び出し元へ返す（既存の 4 規則と同じ指摘の一覧へ混ぜる） | `stopped`・終了コード 1。`items` の `rule` が `detail_*` | 後始末なし。文書を直して打ち直せる |
| `_result_files` | 頭の設計文書がまとまりに無い | `--file` に詳細設計の文書だけを渡した | `head` が `None` | 既定値で続ける（`None` と理由を返し、`check_detail` が `detail_file` の指摘にする） | `detail_file` の行（`name` は `<パス> 設計の結果`、`text` に「頭の設計文書が読んだ文書に無い」） | 頭の設計文書も渡して打ち直せる |
| `_result_files` | 設計の結果の表を読めない | 節が無く `legacy-refactor` の 2 節も欠ける・表の見出しが違う（`parse_results` が `None`） | `design_results.parse_results` の戻り値 | 既定値で続ける（同上） | `detail_file` の行（`text` に「設計の結果の表を読めない」） | 設計文書を直して打ち直せる |
| `_result_files` | 触るファイルのセルの書式が違う | セルにコードスパンの外の字・リンクがある | `parse_results` が投げる `DesignCellError` を捕まえる | 既定値で続ける（例外の文を理由にする） | `detail_file` の行（`text` に例外の文の先頭 300 字） | 同上 |
| `check_detail` | 節の表の形が崩れている | 列の数が見出しと合わない・表が無い | `_section_table` が表を返さない | 既定値で続ける（「行が無い」として `detail_section` にする） | `detail_section` の行 | 文書を直して打ち直せる |
| `check_detail` | 関数の仕様の表に「副作用」か「種類」の列が無い | 列の名前を変えた | 見出しの行で列を探して見つからない | 既定値で続ける（その規則を当てず、`detail_section` に「列 <名前> が無い」を出す） | `detail_section` の行 | 同上 |
| `detail_design_runs` | 工程の分類を引けない | bash を起動できない（`OSError`）・`workflow-common.sh` が無い・「詳細設計」の行が無い・知らないモード（いずれも終了コード 1） | `subprocess.run` の例外と終了コード | 止める（`DeclError` を投げる。既存の `supervise.py new` が `ap.error` にする）。プランを作らない | 標準エラーに `工程の分類を読めない: <理由>`・終了コード 2 | 書き出したプランは無い。原因を直して打ち直せる |
| `detail_design_runs` | 区分が `R`・`C`・`-` のどれでもない | 工程の分類の書式が壊れた | 出力の字を確かめる | 止める（同上） | 同上 | 同上 |
| `with_revise_note` | 出し直しの指摘の入力を読めない | ファイルが無い・JSON でない・形が違う | 既存の `revise_note.read_input`（`OSError` / `ValueError`） | 呼び出し元へ返す（既存どおり。この変更で増えない） | 既存どおり `new sprint` が止まる | 既存どおり |
| `oversized_design_docs` | 契約を読めない | `DocKindsError` | `doc_kinds` の呼び出し | 呼び出し元へ返す（捕まえない。リリース物の欠けを読み飛ばさない） | `cross-review` の駆動が例外で止まる | 契約を直して打ち直せる |
| `oversized_design_docs` | `scripts/lib` を取り込めない | プラグインの外へ `classifications.py` だけを写した | `import doc_kinds` の `ImportError` | 呼び出し元へ返す（同上） | 同上 | 同上 |
| `has_domain_model` | 契約を読めない・`scripts/lib` を取り込めない | 同上 | 同上 | 呼び出し元へ返す（同上） | 同上 | 同上 |
| `detail_steps` | `design-review` の worker が失敗した | 起動の失敗・時間切れ・結果ファイルが `stopped` | プランの work の段の判定（既存） | 止める（`on_fail` を置かない） | プランの報告「結果: 止まった」と止まった段 `design-review` | 作業ディレクトリの `design-review.md` は残る。`--from design-review` で打ち直せる（上書きする） |
| `detail_steps` | `design-review.md` が無いまま `design-review-check` に来た | worker が完了を返したがファイルを書かなかった | `grep` の終了コード 2 | 既存の run の段の失敗の扱い（judge が直し・やり直し・止めるを選ぶ） | プランの報告に `design-review-check(exit=2)` | 後始末なし。`--from design-review` で打ち直せる |
| `detail_steps` | `design-fix` が指摘を直せない | 設計の判断が要る・要求の変更が要る | worker の判断 | 既定値で続ける（扱いの列に `直さない: <理由>` を書いて詳細設計の段へ進む） | 設計 Pull Request の本文の「設計単体のレビュー」の表 | 後始末なし。人が設計承認ゲートで見る |
| `detail_steps` | `detail-design` の worker が 1,000 行の上限に当たる | 主題が大きい | worker が行数を数える（設計の段と同じ） | 止める（設計を 2 本に分けると報告する） | プランの報告「結果: 止まった」 | 書きかけはコミットしない。設計を分けてから `--from detail-design` で打ち直す |

## 処理の手順

`detail_design_runs` が打つコマンドは次のとおりで、2026-11-13 に手元で確かめた（今の HEAD には「詳細設計」の行が無いため、`設計` の行で確かめた）。

```bash
bash -c 'source "$1" && wf_stage_class "$2" "$3"' _ <WORKFLOW_COMMON> <mode> 詳細設計
```

| 打った条件（確かめた値） | 出力 | 終了コード | `detail_design_runs` |
| --- | --- | --- | --- |
| `standard`・`legacy-refactor` で行がある（`設計` → `R`） | `R` | 0 | 真 |
| `light` で行がある（`設計` → `C`） | `C` か `-` | 0 | 偽 |
| 知らないモード（`bogus`） | 無し | 1 | `DeclError` |
| 行が無い（今の `詳細設計`） | 無し | 1 | `DeclError` |
| `workflow-common.sh` が無い | 標準エラーに `No such file or directory` | 1 | `DeclError` |

`writing_steps` の段の並び（工程の分類の「詳細設計」の行で決まる。実装後の値）:

| `a.mode` | 「詳細設計」の区分 | `design` の `next` | 続く段 |
| --- | --- | --- | --- |
| `standard`・`legacy-refactor` | `R` | `design-review` | `design-review` → `design-review-check` →（指摘ありなら `design-fix`）→ `detail-design` → `rebuild` |
| `light`・`operation`・`documentation` | `-` | `rebuild` | 無し（今と同じ） |

`design-review-check` の `cmd` は次のとおりで、終了コード 0・1・2 を 2026-11-13 に手元で確かめた。

```bash
grep -qx -- '- 無し' {state_dir}/work/design-review.md
```

| `design-review.md` | 終了コード | 行き先 |
| --- | --- | --- |
| `- 無し` の 1 行がある | 0 | `detail-design` |
| 指摘の表だけがある | 1 | `design-fix`（`on_exit`） |
| 無い | 2 | run の段の失敗の扱い |

`check_detail` の判定（`sections()` の節ごと）:

| 節 | 表の行 | `省く:` の理由 | 結果 |
| --- | --- | --- | --- |
| 見出しが無い | — | — | `detail_section` |
| ある | 1 行以上 | どちらでも | 指摘なし |
| ある | 0 行 | 空でない | 指摘なし |
| ある | 0 行 | 無いか空 | `detail_section` |

`_result_files` の判定（頭の設計文書の書式）:

| 頭の設計文書 | 「設計の結果」の節 | 「現状の構造」と「変更後の構造」の節 | 戻り値 | `detail_file` |
| --- | --- | --- | --- | --- |
| 無い | — | — | `(None, 理由)` | 1 行 |
| ある | ある（表を読める） | どちらでも | `(ファイルの集合, "")` | 集合に無いパスごとに 1 行 |
| ある | ある（表を読めない） | どちらでも | `(None, 理由)` | 1 行 |
| ある | 無い | 2 つともある | `(None, "")` | 当てない（ファイル配置の表が正本） |
| ある | 無い | 欠けている | `(None, 理由)` | 1 行 |

`cmd_check` の詳細設計の判定（まとまりごと）:

| 詳細設計の文書 | `--require-detail` | 結果 |
| --- | --- | --- |
| ある | どちらでも | 文書ごとに `check_detail` を当てる |
| 無い | 付けた | `detail_missing` を 1 行（`name` は `#<まとまりの番号>`） |
| 無い | 付けない | 指摘なし（`light` の受け入れ条件のファイルなど） |

`kind_of` の判定（契約の 4 行のとき）:

| パス | `placed` | 結果 |
| --- | --- | --- |
| `issues/issue-7-design-detail.md` | どちらでも | `detail`（`-design.md` には当たらない） |
| `issues/issue-7-design-decisions.md` | どちらでも | `decisions` |
| `issues/issue-7-design-api.md`（分けた設計文書） | どちらでも | `None`（今の `DESIGN_DOC_SUFFIXES` と同じ） |
| `docs/issue-7-design.md` | 真 / 偽 | `None` / `design` |

- 繰り返しは、まとまりの文書・節の表の行・契約の行を 1 回ずつ読むだけで、終わる条件は入力の尽きである
- 計算量は文書の行数に比例する。Markdown の解析は `md` の `_tokens` の `lru_cache`（16 件）が同じ文書の解析を使い回す
- 契約と雛形は 1 プロセスに 1 回だけ読む。`detail_design_runs` はモードごとに 1 回だけ bash を起動する

## 内部のデータと定数

**新設の定数は 8 個から 6 個に減った。** 消したのは `SECTIONS`・`DETAIL_SUFFIX`・`DETAIL_DESIGN_MODES` と規則の名前の行である。足したのは、契約と工程の分類のパスを持つ `DECLARATION`・`WORKFLOW_COMMON` で、どちらも事実を持たずに置き場を指す。事実は新設のデータ 2 つ（契約と雛形）が持つ。変更の定数のうち `DESIGN_DOC_FILE_SUFFIXES`・`DESIGN_DOC_SUFFIXES` は消し、`DEFAULT_PATHS` は導出に変えた。最後の列が、その事実の唯一の置き場である。

| 名前 | 種類 | 置き場 | 既定値 | 何から導くか（唯一の置き場） |
| --- | --- | --- | --- | --- |
| 設計文書の種類の契約 | 設定値（新設） | `contracts/design-docs.json` | 置き場 `issues` と 4 行（設計文書の「入出力の契約」の「設計文書の種類の契約」） | これ自体が唯一の置き場 |
| 詳細設計の雛形 | 設定値（新設） | `skills/design/references/detail-template.md` | 10 の `##` 見出しと各節の表の列 | これ自体が唯一の置き場。パスは契約の `detail` の行 |
| `DECLARATION` | 定数（新設） | `lib/doc_kinds.py` | プラグインの根の `contracts/design-docs.json` | `limits.DECLARATION` と同じ探し方（モジュールの置き場からの相対） |
| `DESIGN_REVIEW_NOTE` | 定数（新設） | `supervise_lib/design_steps.py` | `{state_dir}/work/design-review.md` | これ自体。4 段と `pr_steps` の `append` がここから引く |
| `REVIEW_FOCUS` | 定数（新設） | `supervise_lib/paths.py` | `DRIVES["cross-review"].parent / "review_lib" / "review_focus.py"` | 既存の `DRIVES` |
| `WORKFLOW_COMMON` | 定数（新設） | `supervise_lib/paths.py` | `SKILLS / "development-workflow" / "scripts" / "lib" / "workflow-common.sh"` | 既存の `SKILLS` |
| `DESIGN_REVIEW_PROMPT` | 定数（新設。`format` で `focus`・`n` を埋める） | `supervise_lib/prompts.py` | 設計文書と要求のコピーだけを読み、`REVIEW_FOCUS` の `MODEL_REVIEW_TEMPLATE` と `DESIGN_REVIEW_TEMPLATE` の観点で指摘を作業ディレクトリの `design-review.md` へ書く。設計文書を書き換えない・コミットしない | これ自体。観点は写さず `REVIEW_FOCUS` から読ませる。プロンプトを定数で持つ作りは既存に従う（決定 12。#1142） |
| `DESIGN_FIX_PROMPT` | 定数（新設） | `supervise_lib/prompts.py` | `design-review.md` の扱いが `未` の行を直してコミットし（push しない）、扱いを `直した` か `直さない: <理由>` に替える | 同上 |
| `DESIGN_MATCH_PROMPT` | 定数（変更） | `supervise_lib/prompts.py` | 手順に「詳細設計の文書があれば、ファイル配置と関数の仕様の表を実装と照らす」を足す | 同上 |
| `DEFAULT_PATHS` | 定数（変更。導出に変える） | `glossary.py` | `doc_kinds.patterns("glossary")`（今の 3 つに `issues/*-design-detail.md` が加わる） | 設計文書の種類の契約 |
| `check.paths` | 設定値（変更） | `.ndf/glossary.json` | `issues/*-design-detail.md` を足す | このリポジトリの宣言（リポジトリごとの設定で、定数ではない） |
| `DESIGN_DOC_FILE_SUFFIXES` | 定数（削除） | `cross-review/scripts/classifications.py` | — | 設計文書の種類の契約（`line_limit`） |
| `DESIGN_DOC_SUFFIXES` | 定数（削除） | `cross-review/scripts/review_lib/categories.py` | — | 設計文書の種類の契約（`review`） |
| `DESIGN_REVIEW_TEMPLATE` | 定数（変更） | `cross-review/scripts/review_lib/review_focus.py` | 要求の「レビューで見る観点」の 6 行を足す | これ自体。`design-review` の段も読む |
| `STEP_PHASES` | 定数（変更） | `lib/transcript_agents.py` | `"詳細設計": "設計"` を足す | 既存どおり `agent-layers.md` の表とテストで一致を縛る組（決定 12。#1142） |
| `stages` | 設定値（変更） | `lib/stage-skills.json` | `"詳細設計": ["document-style"]` を足す | これ自体（既存の置き場） |
| `WF_STAGE_MATRIX` | 定数（変更） | `workflow-common.sh` | `詳細設計\t-\t-\tR\tR\t-` を「設計」の行の直後へ足す | これ自体。詳細設計を通るモードはここから引く |
| 規則が名指す節と列 | 関数の中の字 | `lib/design_detail.py` の `check_detail` | 節「ファイル配置」「関数の仕様」「異常系の処理」「既存の部品の再利用」、列「種類」「副作用」 | 規則そのものの一部。雛形の見出しに無ければ `sections()` が止める |

規則の名前（`detail_section`・`detail_failure`・`detail_reuse`・`detail_file`・`detail_missing`）は定数にせず、結果の行の `rule` の値として書く。`design-survey.py` の契約（`contracts/entrypoints/plugins__ndf__scripts__design-survey.py.json`）は規則の一覧を持たない。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 課題ごとの設計のプラン（queue が並べる） | 無し。プランごとに worktree（`design/issue-<番号>`）と状態ディレクトリ（`design-review.md` の置き場）が別 | 取らない（共有しない） | `design-review` は `design-review.md` を上書きするため、打ち直しても結果が重ならない |
| `design-fix` と後の段 | `design-review.md`（扱いの列） | 段は 1 本の流れで順に動き、同時に書かない | `design-fix` は扱いが `未` の行だけを直すため、途中で止まっても `--from design-fix` で残りから続く |
| `design-survey.py check` の並行の起動 | 無し（HEAD か worktree のファイルと、契約・雛形を読むだけ） | 取らない | 読むだけで、何度打っても同じ結果 |
| `detail_design_runs` の bash | 無し（`workflow-common.sh` を読むだけ） | 取らない | 読むだけ。控えはプロセスの中だけ |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_check` | 結果の 1 行の JSON（`items` に `detail_*` の行、`metrics` に `detail_docs`・規則ごとの件数） | 標準出力（既存の `emit`） | 文書の本文の全文（指摘の行の `text` は該当の 1 行だけ） |
| `detail_items` | 契約か雛形を読めない理由 | 結果の JSON の `summary`（`stopped`） | 無し |
| `detail_design_runs` | 工程の分類を読めない理由 | 標準エラー（`ap.error`） | 環境変数 |
| `detail_steps` が組む `design-review` | 設計単体のレビューの結果 | 作業ディレクトリの `design-review.md` → 設計 Pull Request の本文（`pr_steps` の `append`） | 環境変数・トークンなどの秘密の値（指示の文で引用を禁じる） |
| `detail_steps` が組む段 | 工程の記録（`stage` が「設計」「詳細設計」） | 既存のプランの記録（`projects-sync.sh`）と進捗ログ | 無し（既存どおり） |

基準は `logging-guidelines` が持つ。ここは適用先だけを書いた。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_table` | `limits.read_table`・`limits.validate`・`limits.DECLARATION`（契約の探し方と読み方） | `grep -n "DECLARATION\|def read_table\|def validate" plugins/ndf/scripts/lib/limits.py` | 倣う。`limits` は上限の方針の表で形が違うため関数は使えない。探し方（`_LIB_DIR.parents[1] / "contracts"` と `_LIB_DIR.parent / "contracts"`）・1 プロセスに 1 回の読み・重複の拒否・`version` の確かめを同じ形で書く |
| `use` | `limits.use` | 同上 | 倣う（テストの差し替えの入口を同じ名前と形にする） |
| `suffixes` | `classifications.DESIGN_DOC_FILE_SUFFIXES`・`categories.DESIGN_DOC_SUFFIXES` | `grep -rn "DESIGN_DOC_SUFFIXES\|DESIGN_DOC_FILE_SUFFIXES" plugins/ndf --include=*.py` | 使えない。同じ事実を 2 か所に持つ定数で、これを消して契約から引く側に変える |
| `patterns` | `glossary.DEFAULT_PATHS` | `grep -n "DEFAULT_PATHS" plugins/ndf/scripts/glossary.py` | 使えない。同じ事実の 3 か所目で、契約から導く側に変える |
| `kind_of` | `design_docs.DESIGN_NAME`・`name_numbers` | `grep -n "DESIGN_NAME\|REQUIREMENTS_NAME" plugins/ndf/scripts/lib/design_docs.py` | 使えない。`DESIGN_NAME` は番号を取る正規表現で、分けた設計文書（`-design-<名前>.md`）にも合い、種類を見分けない。`DESIGN_NAME` を契約へ寄せるのは #1142（決定 12） |
| `template_path` | `limits.DECLARATION` のプラグインの根の求め方 | 同上（`_table` の行） | 倣う。プラグインの根は契約の親のディレクトリ |
| `sections` | `md.headings` | `grep -n "^def " plugins/ndf/scripts/lib/md.py` | 使う。見出しの読み取りは自作しない（`level == 2` の `title` を並べる） |
| `is_detail` | `design_docs.DESIGN_NAME`・`doc_kinds.kind_of` | `grep -n "DESIGN_NAME" plugins/ndf/scripts/lib/design_docs.py` | 使う。番号の形は `DESIGN_NAME`、種類は `kind_of` で見分け、末尾の字を持たない |
| `check_detail` | `md.section_named`・`md.tables` | `grep -n "^def " plugins/ndf/scripts/lib/md.py` | 使う。節と表の読み取りは自作しない |
| `_section_table` | 節の中の最初の表を取る関数 | `grep -rn "start <= t.start <" plugins/ndf/scripts --include=*.py` | 共通の関数は無い。同じ形の読み取りが `design_results.parse_results`・`design-survey.py` の `_read_survey` と `_left_table_rows`・`mode-materials.py` にある。`md.py` へ共通の関数を足すのは既存のコードの構造の作り直しで、#1142 の範囲のため、この課題では足さない |
| `detail_items` | `survey_groups`（まとまりの組み方） | `design-survey.py` を読んだ | 使う。まとまりは既存の 4 規則と同じものを使い、組み直さない |
| `_result_files` | `design_results.parse_results`・`Row.files` | `grep -n "def parse_results" plugins/ndf/scripts/lib/design_results.py` | 使う。設計の結果の表を 2 通りに読まない |
| `detail_design_runs` | `wf_stage_class`（`workflow-common.sh`）・Python から `workflow-common.sh` を読む既存の箇所 | `grep -rn "workflow-common.sh" plugins/ndf/scripts --include=*.py` | `wf_stage_class` を使う（工程の分類の表を Python で読み直さない）。Python から読む箇所はテスト（`test_stage_skills.py` が `bash -c 'source …; wf_stages'`）だけで、本体の部品は無い。起動の形はそのテストに倣う |
| `detail_steps` | `survey_steps`（検査 → 直し → 検査の段）・`_review_steps`（`cross-review` の段） | `grep -n "def survey_steps\|def _review_steps" plugins/ndf/scripts/supervise_lib/*.py` | 使えない。前者は run の検査を打ち直す輪で、後者は Pull Request を要る。段の辞書の形と `on_exit` の書き方は前者に倣う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `use` | 差し替えた契約から読む | `None` でプラグインの契約へ戻る | — |
| `_table` | プラグインの契約を読み、4 行を並びのまま返す | 2 回呼んでも読みは 1 回 | ファイルが無い・JSON でない・同じ種類が 2 つ・`version` が違う・真偽の値が文字列のとき `DocKindsError` |
| `suffixes` | `glossary`・`review` で 4 つ、`line_limit` で `requirements` を除く 3 つ | 契約の並びのまま返す | 知らない真偽の名前で `DocKindsError` |
| `patterns` | `glossary` で `issues/*-requirements.md` から `issues/*-design-detail.md` までの 4 つ | — | 知らない真偽の名前で `DocKindsError` |
| `kind_of` | `issues/issue-7-design-detail.md` が `detail` | `-design.md` と `-design-detail.md` を取り違えない。分けた設計文書（`-design-api.md`）が `None`。`placed=False` で `issues/` の外の名前も見分ける | `placed=True` で `docs/` の下が `None` |
| `template_path` | `detail` で `skills/design/references/detail-template.md` の絶対パス | — | 雛形を持たない種類（`design`）・知らない種類で `DocKindsError` |
| `sections` | プラグインの雛形から 10 の見出しを並びのまま返す | 雛形の `#` の見出しと `###` の見出しを数えない | 雛形が無い・`##` が 0 個・「関数の仕様」が無い雛形で `DetailTemplateError` |
| `is_detail` | `issues/issue-7-design-detail.md` が真 | `issue-7-8-design-detail.md` が真、`issue-7-design.md`・`issue-7-design-decisions.md` が偽 | `notes/detail.md` が偽 |
| `check_detail` | 雛形の節すべてに表の行があり、副作用・新設・パスがそろった文書で指摘なし | 全節が `省く: <理由>` の文書で指摘なし。`result_files` が `None` で `why` が空なら `detail_file` を出さない。節の本文が `省く:` だけ（理由が空）で `detail_section`。雛形に節を足すと、その節の無い文書で `detail_section` が出る（節の一覧が雛形から来ること） | 節の欠け（`detail_section`）・副作用ありの関数が異常系の表に無い（`detail_failure`）・`新設` が再利用の表に無い（`detail_reuse`）・パスが触るファイルに無い（`detail_file`）。`変更` の関数には再利用を求めない。異常系の表の 2 列目以降に名前があっても照合しない |
| `_section_table` | 見出しと表のある節で表を返す | 表が 2 つある節で最初の表を返す。コードブロックの中の `省く:` を読まない | 見出しが無ければ `(None, None, None)` |
| `detail_items` | 詳細設計の文書のあるまとまりで `check_detail` の結果を返す | 番号を共有する 2 つの課題のまとまりで、文書ごとに判定する | `require` で文書が無いまとまりに `detail_missing` が 1 行。雛形を読めなければ `StepError`（終了コード 2） |
| `_result_files` | 「実装する」の行の触るファイルを集める | 「取り込む」「閉じる」の行のファイルを数えない。括弧の注記の中のパスを数えない。`legacy-refactor` の書式の頭の設計文書で `(None, "")` | 頭の設計文書が無い・表が読めない・`DesignCellError`・「設計の結果」が無く `legacy-refactor` の 2 節も欠けるときに `None` と理由 |
| `cmd_check` | 指摘なしで終了コード 0、`metrics.detail_docs` が詳細設計の文書の数 | `--require-detail` を `--file` と組める | 規則の指摘で終了コード 1。雛形を読めなければ終了コード 2。既存の 2・3 は変わらない |
| `build_parser` | `--require-detail` を受ける | 省けば偽 | 値を渡すと argparse が終了コード 2 |
| `detail_design_runs` | 工程の分類で `R` のモード（`standard`・`legacy-refactor`）が真 | `-` のモードが偽。同じモードで 2 回呼んでも bash は 1 回 | `WORKFLOW_COMMON` を「詳細設計」の行の無い写しへ差し替えると `DeclError`。存在しないパスへ差し替えると `DeclError` |
| `detail_steps` | 4 段の `next` と `on_exit` が決定 4 の表のとおり | `design-fix` の `next` が `detail-design`（打ち直しの輪を作らない） | `design-review-check` の `cmd` を 3 通りのファイルで打って 0・1・2 |
| `writing_steps` | `standard`・`legacy-refactor` で `design` の `next` が `design-review` | `legacy-refactor` も同じ並び。工程の分類の写しで `light` の区分を `R` にすると `light` にも 4 段が並ぶ（モードを工程の分類から引くこと） | `light`・`operation`・`documentation` に 4 段が無い |
| `pr_steps` | `append` に `DESIGN_REVIEW_NOTE` がある | ファイルが無いモードで本文が変わらない（既存の `with_appended`） | — |
| `survey_steps` | `require_detail=True` で `cmd` に `--require-detail` | 既定（偽）で `cmd` が今と同じ | — |
| `with_revise_note` | 指摘の入力があれば `detail-design` の指示に指摘の文が入る | `detail-design` の段が無いプラン（`light`）で今と同じ | 入力を読めなければ既存どおり例外 |
| `oversized_design_docs` | 1,001 行の `issues/issue-7-design-detail.md` を返す | 1,001 行の `-requirements.md` を返さない（`line_limit` が偽） | 契約を読めなければ `DocKindsError` |
| `has_domain_model` | `## ドメインモデル` を持つ `-design.md` で真 | `issues/` の外のパスも見る（今と同じ） | 契約を読めなければ `DocKindsError` |
| `_is_design_doc_path` | `issues/issue-7-design-detail.md` が真 | `issues/issue-7-requirements.md` が真、`issues/issue-7-design-api.md` が偽（今と同じ） | `docs/issue-7-design.md` が偽 |
