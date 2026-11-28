# #4735 の詳細設計: Issue の報告を残したまま、要求をファイルで管理できるようにする

設計文書 [issue-4735-design.md](./issue-4735-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。要求仕様書は [issue-4735-requirements.md](./issue-4735-requirements.md) にある。

新設する関数が 2 つ以上あり、既存の関数のシグネチャも変えるので、詳細設計を別ファイルにした。

「実行の印」（1 回の作業を指す 12 桁の値）の出し方は設計文書の「データ構造」に、「信用する書き手」などの工程の言葉は設計文書の「この文書で使う工程の言葉」にある。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/src/ndf/
├── contracts/
│   ├── internal.py
│   └── worker_paths.py
├── adapters/
│   └── git.py
├── core/
│   ├── issue_docs.py
│   ├── issue_body.py *
│   ├── requirements_decide.py *
│   ├── requirements_docs.py *
│   ├── requirements_history.py *
│   ├── run_trailer_decide.py *
│   ├── worker.py
│   ├── plan/inputs_decide.py
│   ├── plan/review_units_decide.py
│   └── route/__init__.py
├── progress/
│   ├── command.py
│   ├── requirements_stage.py *
│   ├── invoke.py
│   ├── issue_values.py
│   ├── records.py
│   └── units_invoke.py
└── domains/
    ├── docrebuild/drive.py
    ├── loop/{acceptance.py, acceptance_decide.py, acceptance_release.py *, cli.py}
    ├── materials/{cli.py, collect.py}
    ├── records/{cli.py, requirements_cmd.py *, close_release.py *, cleanup_stage.py, close_decide.py}
    └── release/{cli.py, record.py, record_read.py, record_unversioned.py, record_decide.py,
                 acceptance_record.py *, acceptance_record_decide.py *}
```

依存は一方向だけで、循環は無い。層は `checks/rules.json` の `structure.layers` の順に `contracts`・`adapters`・`core`・`progress`・`domains` と並ぶ。各層は、並びで前にある層に依存する。要求仕様書の読み出し（`core/requirements_decide.py`・`core/requirements_history.py`・`core/requirements_docs.py`）、スプリント PR の「通した工程」に書く実行の行の形（`core/run_trailer_decide.py`）、本文の書き換え（`core/issue_body.py`）は `core` に置く。`progress` と `domains` の呼ぶ側がそこを使う。`core` から `progress` を呼ばないため、起点コミットの解決は `progress.command.base_ref` を使わずに `Git.commit_or_none` で行う。要求仕様書の 3 つのモジュールは `requirements_docs` → `requirements_history` → `requirements_decide` の向きにだけ import する（`requirements_docs` は `requirements_decide` も直接読む）。型と解析を一番下に置くのは、読み出し（`lookup`）が履歴の検索を呼び、履歴の検索も同じ型と解析を使うためである。実行の行は `progress` が書き `domains/release` が読むので、形と定数はどちらからも読める `core` に置く。構成要素の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

**新設のモジュールを設計文書の構成要素より 9 つ多く置く。** 9 つは次のモジュールである。

- `core/requirements_decide.py`
- `core/requirements_history.py`
- `core/run_trailer_decide.py`
- `progress/requirements_stage.py`
- `domains/records/requirements_cmd.py`
- `domains/records/close_release.py`
- `domains/loop/acceptance_release.py`
- `domains/release/acceptance_record.py`
- `domains/release/acceptance_record_decide.py`

`progress/requirements_stage.py` を分けるのは、`progress/command.py` に行を足せないためである。このファイルは行数の上限（`checks/rules.json` の `structure.max_lines` の 1,000 行）にちょうど達している。`core/requirements_decide.py` と `core/run_trailer_decide.py` は、上に書いた import の循環と、下の層が上の層を読むことを避けるためである。ほかの 6 つは、1 つのモジュールに置くと、上限に近づくか、判断だけの関数と I/O が同じモジュールに混ざるためである。判断だけの関数は、名前が `_decide` で終わるモジュールに置き、外部の I/O を持たない。新設のモジュールは設計文書の「設計の結果」の触るファイルに足した。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/worker_paths.py` | 変更 | 所有者だけが読める権限でファイルを置き、バイト列で読み、名前が空のときだけ作る | `contracts/worker_paths_open.py` | モジュール | 新設 `read_bytes`・`has_acl`・`create_file`。変更 `write_file`・`staged_write`・`_fill_data` |
| `contracts/internal.py` | 変更 | 保存した工程の定義へ `requirements_docs` のキーを足す移し方を持つ | `contracts/schemas/internal/common.schema.json` | モジュール | 新設 `_snapshot_v2` |
| `adapters/git.py` | 変更 | ツリーの一覧・名前つきの履歴・オブジェクトの有無・SHA の取得を 1 回の起動で行う | git | `Git` | 新設 `tree_entries`・`name_status_log`・`missing_objects`・`fetch_commits` |
| `core/issue_docs.py` | 変更 | 要求仕様書のパスの形と元の Issue の行を読む | 無し | モジュール | 新設 `requirements_issues`・`requirements_path`・`representative_issue`・`origin_issues` |
| `core/requirements_decide.py` | 新設 | 要求仕様書の型と、読んだバイト列の解析、条件の版の計算（I/O を持たない） | `core/issue_docs`・`core/plan/inputs_decide` | モジュール | `Doc`・`Criterion`・`Lookup`・`RequirementsError`・`criterion_digest`・`criteria_of`・`criteria_list_digest`・`parse_doc`・`needs_requirements` |
| `core/requirements_docs.py` | 新設 | Issue の番号から要求仕様書を探して返す | `core/issue_docs`・`core/requirements_decide`・`core/requirements_history`・`contracts/worker_paths`・`adapters/git` | モジュール | `base_commit`・`worktree_candidates`・`tree_candidates`・`lookup`・`find` |
| `core/requirements_history.py` | 新設 | 消えた要求仕様書の最終版と、配布した版の要求仕様書を git の履歴から選ぶ | `core/requirements_decide`・`adapters/git` | モジュール | `Touch`・`final_version`・`branch_deleted`・`release_docs` |
| `core/run_trailer_decide.py` | 新設 | スプリント PR の「通した工程」に書く実行の行を組み立て、読む | 無し | `Trailer`・モジュール | `TRAILER_WORDS`（定数）・`run_lines`・`parse_trailer` |
| `core/issue_body.py` | 新設 | Issue の本文を、番号ごとのロックの中で読んで自分のセクションだけ差し替える | `adapters/github`・`filelock` | モジュール | `BodyStore`・`GitHubBodies`・`Rewritten`・`BodyLocks`・`LockError`・`rewrite` |
| `core/plan/inputs_decide.py` | 変更 | 実行と要求仕様書の組ごとの確認の表を読む（`progress` と `domains` が共に使う） | 無し | `TableRow`・`RunTable`・モジュール | 新設 `RUN_HEADING`（定数）・`read_tables`・`counted` |
| `core/plan/review_units_decide.py` | 変更 | 実装計画の版の行と単位の外の行を読み、単位に版を持たせる | `core/requirements_decide` の型 | `Unit`・モジュール | 新設 `criteria_versions`・`outside_units`・`stamped`。変更 `Unit`・`lineup`・`acceptance_args` |
| `core/route/__init__.py` | 変更 | テンプレートの差し込み `acceptance` の自動の組み立てに実行の印とモードを足し、`pr` の前に置くコマンドを値で付け外す | `core/requirements_decide` | モジュール | 変更 `launch_values`・`check_acceptance`・`rendered`。新設 `_by_body_from` |
| `core/worker.py` | 変更 | 確認の記録の手順の文を要求仕様書と実行の印の形にする | 無し | モジュール | 変更 `ACCEPTANCE_RULES`（定数） |
| `progress/command.py` | 変更 | 文脈に実行の印を持たせ、要求の工程の出力を新設のモジュールへ渡す | `progress/requirements_stage` | モジュール | 変更 `_prepare_new`・`_cmd_finish`・`parser`。削除 `_requirements` |
| `progress/requirements_stage.py` | 新設 | 実行の印、要求の工程の出力の記録、要求仕様書の移送を持つ | `core/requirements_docs`・`core/requirements_decide`・`progress/store`・`contracts/worker_paths` | モジュール | `run_mark`・`context_run`・`recorded_docs`・`creation_finish`・`finish_requirements`・`MoveStep`・`resume_step`・`move_one`・`move_requirements`・`add_parser` |
| `progress/invoke.py` | 変更 | 工程の入力のモードと実行の印をプランの値へ渡す | `progress/requirements_stage` | モジュール | 変更 `_fill_from_issue` |
| `progress/issue_values.py` | 変更 | PR の本文の要求の部分（要求仕様書へのリンクと `Refs` の行）の文を組み立てる | `core/requirements_decide` の型 | モジュール | 新設 `requirements_refs` |
| `progress/units_invoke.py` | 変更 | 条件の数と版を要求仕様書から作り、スプリント PR の前に全条件を照らす | `core/requirements_docs`・`core/requirements_decide`・`core/plan/inputs_decide`（`read_tables`・`counted`） | モジュール | 新設 `_all_recorded`。変更 `_read_units`・`_unit_json`・`_held_lineup`・`sprint_pr`。削除 `_criteria_count` |
| `progress/records.py` | 変更 | 進行の記録を共通の書き換えで書き、スプリント PR の「通した工程」に実行の行を足す | `core/issue_body`・`core/requirements_docs`・`core/run_trailer_decide` | モジュール | 変更 `pr_section`・`sync_records`・`GitHubRecords` |
| `domains/records/cleanup_stage.py` | 変更 | クリーンアップの記録を共通の書き換えで書く | `core/issue_body` | モジュール | 変更 `record_issue`・`record_cleanup_stage` |
| `domains/records/requirements_cmd.py` | 新設 | `ndf records requirements show`・`stamp`・`pr-body` のエントリポイント | `core/requirements_docs`・`core/requirements_history`・`domains/release/acceptance_record`・`progress/issue_values` | モジュール | `add_parsers`・`_show`・`_stamp`・`_pr_body`・`write_out`・`stamp_plan` |
| `domains/records/cli.py` | 変更 | `requirements` と `close-release` のサブコマンドを登録し、`close-check --verification` に実行の確かめを足す | `domains/records/requirements_cmd`・`domains/records/close_release` | モジュール | 変更 `_parser`・`_judged`・`_add_close_check`・`_records_view`。新設 `_skipped_deploy` |
| `domains/records/close_decide.py` | 変更 | 確認対象の版が今と同じ行だけで合否を出し、閉じる条件の判定と閉じた後の扱いを決める | `domains/release/record_decide` | モジュール | 変更 `records_verdict`・`_targets_verdict`・`_last_check`。新設 `current_rows`・`ClosedEntry`・`close_verdict`・`after_close` |
| `domains/records/close_release.py` | 新設 | `ndf records close-release`（閉じる・記録を読み直す・開き直す・表を整理する） | `domains/records/close_decide`・`domains/release/record_read`・`domains/loop/acceptance_release`・`progress/flow`・`adapters/github`・`adapters/publish`・`adapters/git` | モジュール | `state_path`・`read_state`・`write_state`・`close_release`・`add_close_release` |
| `domains/loop/acceptance_decide.py` | 変更 | 実行と要求仕様書の組ごとの確認の表を書き換える | `core/plan/inputs_decide`・`core/requirements_decide` の型 | `Check`・モジュール | 新設 `table_text`・`table_digest`・`without_tables`。変更 `Check`・`recorded_body`。削除 `criteria`・`read_checks`・`unrecorded`・`merged`・`checks_table`・`recorded_rows` |
| `domains/loop/acceptance.py` | 変更 | `record`・`show`・`check`・`new-run` のエントリポイント | `core/requirements_docs`・`core/issue_body` | モジュール | 新設 `_new_run`・`parse_digests`。変更 `_record`・`_show`・`_check`・`_check_one`・`add_parsers`。削除 `lock_path`・`_unsettled`・`_reapply` |
| `domains/loop/acceptance_release.py` | 新設 | `show --release` と `prune` | `core/requirements_history`・`domains/release/acceptance_record` | モジュール | `show_release`・`prune`・`_prune_one` |
| `domains/materials/collect.py` | 変更 | 種類と影響を要求仕様書から読み、候補の出所に要求仕様書を足す | `core/requirements_decide` の型 | モジュール | 変更 `issue_materials`・`gather` |
| `domains/materials/cli.py` | 変更 | 要求仕様書を 1 回の読み出しで引いて `gather` へ渡す | `core/requirements_docs` | モジュール | 変更 `main` |
| `domains/docrebuild/drive.py` | 変更 | 複数の番号を名前に持つ要求仕様書も設計書と組にする | `core/issue_docs` | モジュール | 変更 `requirements_of`。削除 `REQUIREMENTS_FILE` |
| `domains/release/record_decide.py` | 変更 | リリース記録に `acceptance` を持たせ、テストの行を版で絞る | 無し | `Record`・モジュール | 変更 `Record`・`render_record`・`parse_record_json`・`to_test`・`_pair_rows` |
| `domains/release/acceptance_record_decide.py` | 新設 | 対応の記録の組み立て・確認対象の版・実行の選び方・有効な記録の選び方 | `core/requirements_decide`（`DIGEST_CHARS`）。判断だけ | `AcceptanceMap`・`RunEntry`・モジュール | `canonical`・`target_digest`・`first_targets`・`corrected_targets`・`chosen_run`・`valid_map`・`map_of` |
| `domains/release/acceptance_record.py` | 新設 | 含めた PR と確認の表を読み、対応の記録を作って書く・読む | `domains/release/acceptance_record_decide`・`core/requirements_history`・`core/run_trailer_decide` | モジュール | `build_map`・`read_map`・`record_acceptance`・`add_record_acceptance` |
| `domains/release/record.py` | 変更 | 記録の PR へ最初に `acceptance` 入りの記録を書き、テストの記録に版を書く | `domains/release/acceptance_record` | モジュール | 変更 `write`・`write_verification`・`_require_retest_reasons` |
| `domains/release/record_unversioned.py` | 変更 | バージョン番号を持たないリポジトリの最初の届け先の記録に `acceptance` を入れる | `domains/release/acceptance_record` | モジュール | 変更 `write_unversioned`・`_made` |
| `domains/release/record_read.py` | 変更 | 対応の記録がある値で `outputs.issues`・`acceptance_targets` を返す | `domains/release/acceptance_record` | モジュール | 変更 `read_release`・`_records_outputs` |
| `domains/release/cli.py` | 変更 | `record-acceptance` と `verification-record --records` の引数 | `domains/release/acceptance_record` | モジュール | 変更 `_parser`・`cmd_verification_record`・`cmd_records`・`_records_selection` |
| `plugins/ndf/contracts/` の `workflow/context.schema.json`・`workflow/stages/requirements.json`・`workflow/stages/worktree.json`・`workflow/progress.json`・`domains/locations.json`・`schemas/internal/common.schema.json`・`schemas/release-verification.schema.json`・`schemas/io/{records,loop,progress,release-steps}.{args,result}.schema.json`・`schemas/io/materials.result.schema.json`・`templates/params.json`・`templates/design.json`・`templates/impl.json`・`domains/ndf-files.json`・`worker/kinds.json`・`worker/document-rebuild.md` | 変更 | 文脈のキー（`run`・`requirements_docs`）・事象（`requirements_move`、完了した要求の工程への `outputs_recorded`）・内部データの形のバージョン・結果 JSON と引数・差し込みの値（`run`・`mode`）・設計と実装のプランの `pr-body` のステップ・起動指示の文 | — | — | — |

手順書（Skill と参照）・用語集・`docs/contracts/` の変更は、最後の「手順書と用語集」にまとめる。

## 要求仕様書の名前と形（`core/issue_docs.py`）

満たす決定: 決定 1、受け入れ条件 2、不変条件 I2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `REQUIREMENTS_PATH` | `core/issue_docs.py` | 新設（定数） | `re.Pattern[str]`（`^issues/issue-(\d+(?:-\d+)*)-requirements\.md$`） | 要求仕様書のパスの形 |
| `requirements_issues` | `core/issue_docs.py` | 新設 | `(path: str) -> frozenset[int]` | パスが要求仕様書の形なら名前の番号の集合、違えば空集合を返す |
| `requirements_path` | `core/issue_docs.py` | 新設 | `(numbers: Iterable[int]) -> str` | 番号を昇順に `-` でつないだ要求仕様書のパスを返す |
| `representative_issue` | `core/issue_docs.py` | 新設 | `(path: str) -> int` | 名前の番号のうち最も小さい番号（代表の Issue）を返す |
| `origin_issues` | `core/issue_docs.py` | 新設 | `(text: str, repo: str) -> frozenset[int]` | 最初の `##` より前の `元の Issue: https://github.com/<repo>/issues/<N>` の行から番号の集合を返す |

- `representative_issue`
  - 事前条件: `requirements_issues(path)` が空でない。空なら `ValueError`
- `origin_issues`
  - 事後条件: `repo` が違う URL、形の違う行、最初の `##` より後の行は数えない。大文字と小文字の違う `repo` は同じものとして扱う（GitHub のリポジトリ名の扱いと同じ）

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `requirements_issues` | `_name_numbers` | `core/issue_docs.py` を読んだ | 使う（パターンを渡すだけ） |
| `requirements_path`・`representative_issue` | `progress/issue_values.issue_numbers`（番号を小さい順に並べる） | `grep -rn 'sorted(int' src/ndf/progress` | 使えない（工程の入力の形を受け、パスを作らない） |
| `origin_issues` | `adapters/github_pulls.closing_keyword_issues` | `grep -rn 'def closing_keyword_issues'` | 使えない（closing keywords の語を読み、`元の Issue:` の行を読まない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `requirements_issues` | `issues/issue-7-8-requirements.md` → `{7, 8}` | `issue-78-requirements.md` は `{78}` で `{7}` に当たらない | 設計書のパスは空集合 |
| `origin_issues` | 2 行で `{7, 8}` | 最初の `##` の後の行は数えない | 別のリポジトリの URL は数えない |

## 要求仕様書の読み出し（`core/requirements_decide.py`・`core/requirements_docs.py`）

満たす決定: 決定 2・決定 10、受け入れ条件 2・7・8、不変条件 I2・I3・I6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Criterion` | `core/requirements_decide.py` | 新設（型） | `NamedTuple(number: int, text: str, digest: str)` | 受け入れ条件 1 つと条件の版 |
| `Doc` | `core/requirements_decide.py` | 新設（型） | `NamedTuple(path: str, source: str, commit: Optional[str], issues: tuple[int, ...], body: str, criteria: tuple[Criterion, ...], criteria_digest: str)` | 読んだ要求仕様書 1 本 |
| `Lookup` | `core/requirements_decide.py` | 新設（型） | `NamedTuple(docs: dict[int, Doc], missing: tuple[int, ...])` | 番号ごとの要求仕様書と、見つからない番号 |
| `RequirementsError` | `core/requirements_decide.py` | 新設（例外） | `(cause: str, detail: str, gaps: Sequence[dict[str, str]] = ())` | 読めない理由を結果 JSON の `cause` で持つ |
| `criterion_digest` | `core/requirements_decide.py` | 新設 | `(text: str) -> str` | チェックボックスの印と先頭の番号を除き前後の空白を落とした文の SHA-256 の先頭 12 桁を返す |
| `criteria_of` | `core/requirements_decide.py` | 新設 | `(body: str) -> tuple[Criterion, ...]` | `## 受け入れ条件` のチェックボックスを上から番号を振って読み、文の重なりを断る |
| `criteria_list_digest` | `core/requirements_decide.py` | 新設 | `(criteria: Sequence[Criterion]) -> str` | 条件の版を番号の順に改行でつないだ文字列の SHA-256 の先頭 12 桁を返す |
| `parse_doc` | `core/requirements_decide.py` | 新設 | `(path: str, raw: bytes, *, source: str, commit: Optional[str], repo: str) -> Doc` | バイト列を UTF-8 で読み、元の Issue の行と条件を確かめて `Doc` にする |
| `base_commit` | `core/requirements_docs.py` | 新設 | `(git: Git, remote: str, base: str) -> str` | `<remote>/<base>` を 1 回だけコミットに解決する |
| `worktree_candidates` | `core/requirements_docs.py` | 新設 | `(root: Path, numbers: frozenset[int]) -> dict[str, bytes]` | 作業場所の `issues/` から、番号を名前に含む要求仕様書を検査を通して読む |
| `tree_candidates` | `core/requirements_docs.py` | 新設 | `(git: Git, commit: str, numbers: frozenset[int]) -> dict[str, bytes]` | コミットのツリーから、番号を名前に含む通常ファイルの要求仕様書を読む |
| `lookup` | `core/requirements_docs.py` | 新設 | `(numbers: Sequence[int], *, root: Path, git: Git, repo: str, base: str, remote: str, ref: Optional[str] = None) -> Lookup` | 読み先を順にたどって番号ごとの要求仕様書を返し、見つからない番号を `missing` に入れる |
| `find` | `core/requirements_docs.py` | 新設 | `(numbers: Sequence[int], **kw: Any) -> list[Doc]` | `lookup` の結果を要求仕様書の重なりなしの並びで返し、`missing` があれば `incomplete` で止まる |
| `needs_requirements` | `core/requirements_decide.py` | 新設 | `(mode: str) -> bool` | そのモードが要求仕様書を持つかを返す。偽は `legacy-refactor` だけで、空の文字列は真 |

- `criteria_of`
  - 事後条件: 番号は 1 始まりで、数え方は今の `inputs_decide.criteria` と同じ。文の版が同じ条件が 2 つあれば、重なる番号を挙げて `RequirementsError("check_failed")`
- `parse_doc`
  - 事後条件: `issues` はファイル名の番号の昇順。`origin_issues(text, repo)` がファイル名の番号をすべて含む
- `lookup`
  - 事前条件: `ref` を渡すときは、作業場所と履歴を読まない
  - 事後条件: 1 つの番号に 1 本だけを返す。同じ要求仕様書を指す番号は同じ `Doc` を指す
  - 副作用: git の起動は、起点コミットの解決・ツリーの一覧・内容の読み出し・`<起点コミット>..HEAD` の履歴の照会の各 1 回以下で、番号の数に比例しない。ファイルの読み出しは `issues/` を 1 回開いた記述子から行う

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `parse_doc` | 元の Issue の URL が欠ける | 要求の工程が行を書かなかった | `origin_issues` の結果とファイル名の番号を比べた所 | 止める | `check_failed`。欠けた番号を `gaps` に載せる。終了コード 1 | 要求仕様書に行を足して打ち直せる |
| `parse_doc` | UTF-8 で読めない | 壊れたファイル | `bytes.decode` | 止める | `check_failed` | 直して打ち直せる |
| `criteria_of` | 条件の文が重なる | 同じ文の条件が 2 つある | 版の集合を作る所 | 止める | `check_failed`。重なる番号を書く | 直して打ち直せる |
| `worktree_candidates` | 候補がリンク・特殊ファイル・ほかの利用者の持ち物、`issues/` か祖先が差し替えられる、ACL の項目がある | 共有の環境で別の利用者が置いた | `worker_paths.check_owned`・`check_unshared`・`has_acl` | 止める（内容を読まない） | `precondition`。理由（`EntryUnsafe` の種類）を書く | 権限を直して打ち直せる |
| `tree_candidates` | エントリのモードが 100644・100755 でない | リンクやサブモジュール | `tree_entries` の結果 | 止める | `precondition` | 直したコミットで打ち直せる |
| `lookup` | 同じ読み先に同じ番号の要求仕様書が 2 本 | 統合の前後のファイルが両方ある | 番号ごとの候補を数える所 | 止める | `precondition`。候補のパスを並べる | 片方を消して打ち直せる |
| `lookup` | git を読めない・起点のリモート追跡ブランチが無い・履歴が浅い | 取得前、浅い clone | `GitError`・`commit_or_none` が None・`git rev-parse --is-shallow-repository` | 止める | `precondition`。`git fetch` か `--unshallow` を案内する | 取得して打ち直せる |
| `find` | 見つからない番号がある | 要求の工程を通っていない | `Lookup.missing` | 止める | `incomplete`。番号を `gaps` に載せる | 要求仕様書を書いて打ち直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `lookup` | `ref` あり | `tree_candidates(ref)` だけを読む |
| `lookup` | `ref` なし、作業場所に候補あり | 作業場所の候補を返す（順 1） |
| `lookup` | `ref` なし、作業場所に無く、`<起点>..HEAD` で消えている | `requirements_history.branch_deleted` の最終版を返す（順 2。`Doc.commit` は `Deleted.commit`） |
| `lookup` | `ref` なし、上のどちらにも無い | `tree_candidates(base_commit)` を返す（順 3） |
| `lookup` | どこにも無い | `missing` に入れる |

- 繰り返しの終わる条件: 番号ごとに最初に見つかった読み先で止める。読み先ごとの読み出しは、残った番号をまとめて 1 回で行う
- 計算量の注意: 番号の集合で 1 回絞った候補だけを読む。ツリーの一覧は `issues/` の下だけを取る

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `DIGEST_CHARS` | 定数 | `core/requirements_decide.py` | `12`（条件の版・条件一覧の版・実行の印・確認対象の版で共有する。ほかのモジュールはここを読む） |
| `SOURCES` | 定数 | `core/requirements_docs.py` | スキーマ `records.result.schema.json` の列挙を読む。今の値は `("worktree", "history", "tree", "release")`（結果 JSON の `source` の値） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `criteria_of` | `core/plan/inputs_decide.criteria` | `core/plan/inputs_decide.py` を読んだ | 使う（番号の数え方を共有し、版だけを足す） |
| `criterion_digest`・`criteria_list_digest` | `hashlib.sha256`・`domains/loop/acceptance_decide` の今の表 | `grep -rn sha256 src/ndf/core` | 使う（`hashlib`）。今の確認の表は版を持たないので、版を計算する既存の関数は無い |
| `parse_doc` | markdown-it-py（`domains/materials/collect._tokens`） | `grep -rn MarkdownIt src/ndf` | 使わない（`inputs_decide.criteria` が行で読んでおり、数え方を変えないため） |
| `worktree_candidates` | `contracts/worker_paths.open_workdir`・`read_bytes`・`check_owned`・`check_unshared`・`has_acl` | `contracts/worker_paths_open.py` を読んだ | 使う |
| `tree_candidates` | `Git.blobs`・`adapters/git.REGULAR_MODES` | `adapters/git.py` を読んだ | 使う。ツリーの一覧は新設の `Git.tree_entries` |
| `base_commit` | `progress.command.base_ref` | `progress/command.py` を読んだ | 使えない（`progress` の層にあり、`core` から呼ぶと層の向きに反する）。`Git.commit_or_none` を使う |
| `lookup`・`find` | `domains/docrebuild/drive.requirements_of` | `grep -rn requirements.md src/ndf` | 使えない（作業場所の単番号のパスだけを見る） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `criterion_digest` | 同じ文は同じ版 | `[x]` と `[ ]`、先頭の `1.` の有無、前後の空白で版が変わらない | 文を 1 字変えると版が変わる |
| `criteria_of` | 3 件を番号つきで返す | 条件 0 件は空の並び | 同じ文の条件 2 つで `check_failed` |
| `parse_doc` | URL がそろえば `Doc` | 番号 2 つの名前で 2 行 | 1 行欠けで `check_failed` |
| `worktree_candidates` | 追跡していないファイルも読む | 番号の一部（`78` と `7`）は当たらない | リンク・ほかの利用者が書ける・ACL の項目（`has_acl` を差し替え）で内容を返さない |
| `tree_candidates` | 通常ファイルを読む | 100755 も読む | リンクのエントリで止まる |
| `lookup` | 順 1・順 2・順 3 のそれぞれで読める | ローカルのベースブランチが古くても起点より前の削除は返らない | 同じ読み先に 2 本で止まる。浅い clone で止まる |
| `needs_requirements` | `standard` は真 | 空の文字列は真 | `legacy-refactor` だけが偽 |
| `find` | 2 つの番号が 1 本を指すと要素 1 つ | 番号 1 つ | 見つからない番号で `incomplete` |

## 履歴とリリースの読み出し（`core/requirements_history.py`）

満たす決定: 決定 2、受け入れ条件 7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Touch` | `core/requirements_history.py` | 新設（型） | `NamedTuple(commit: str, path: str, status: str, old: str)` | 履歴の中で要求仕様書を変えたコミット、パス、状態（`A`・`M`・`D`）、変更前の blob の id |
| `Deleted` | `core/requirements_history.py` | 新設（型） | `NamedTuple(commit: str, raw: bytes)` | 消えた要求仕様書の最終版。`commit` はそのパスにその内容があるコミット |
| `final_version` | `core/requirements_history.py` | 新設 | `(touches: Sequence[Touch], parents: Mapping[str, tuple[str, ...]], numbers: frozenset[int], heads: Sequence[str], fallback: Sequence[str], blobs: Callable[[Sequence[str]], list[Optional[bytes]]]) -> dict[str, Deleted]` | 設計文書の「消えた要求仕様書の最終版の選び方」の順 1〜4 で、番号ごとに 1 つの削除直前の内容を選ぶ |
| `branch_deleted` | `core/requirements_history.py` | 新設 | `(git: Git, base: str, numbers: frozenset[int]) -> dict[str, Deleted]` | `<base>..HEAD` で消えた要求仕様書の最終版を返す |
| `release_docs` | `core/requirements_history.py` | 新設 | `(git: Git, remote: str, mapping: Mapping[str, Any], numbers: frozenset[int], repo: str) -> list[Doc]` | 対応の記録の配布コミットと PR の head から、配布した要求仕様書を読む |

- `final_version`
  - 事前条件: `touches` は調べる履歴をすべて読み終えたもの。`parents` は、調べる範囲のすべてのコミット（要求仕様書を変えていないものを含む）の親
  - 事後条件: 返す内容は、削除のエントリが持つ変更前の blob（`old`）。`commit` は、削除したコミットの親のうち `<親>:<パス>` の内容がそれと同じ最初のもの。選べない番号は `RequirementsError("precondition")`
  - `blobs` は 1 回だけ呼ぶ。指定には、削除の候補ごとの `old` と、削除したコミットの全部の親の `<親>:<パス>` を並べる
  - マージコミットのエントリは、競合の解消で人が変えた分だけが出る（`name_status_log` の `--diff-merges=remerge`）。片側の削除がそのまま伝わったマージには、エントリが無い。マージの親や blob の一致から伝播を推定する規則は持たない
  - 両側が別々に直した内容を競合の解消で消した場合、`old` は git が作り直したマージの結果で、リポジトリに無い。`blobs` が None を返すので、`precondition` で止まる
- `release_docs`
  - 事後条件: 返す `Doc` の `source` は `release`、`commit` は配布コミット（消えたファイルは `Deleted.commit`）。どちらも `<commit>:<path>` に返した本文がある
  - 副作用: `Git.missing_objects`・`fetch_commits`・`name_status_log`・`tree_entries`・`blobs` を各 1 回以下呼ぶ。`fetch_commits` は手元に無い SHA があるときだけ打ち、ブランチの名前と `refs/pull/<N>/head` は使わない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `final_version` | 残るファイルが 2 つ以上、または前後の分からない削除で内容が違う | 統合と分割が入り組んだ履歴 | 順 4 | 止める | `precondition`。候補のパスと削除コミットを書く | 人が要求仕様書を置き直して打ち直す |
| `release_docs` | 記録の SHA を取得できない | リモートで消えた・権限が無い | `fetch_commits` の終了コード | 止める | `precondition` | 取得できる環境で打ち直せる |
| `release_docs` | 配布コミットにも履歴にも無い番号 | 要求の工程を通らないモードの Issue | 番号の集合の差 | 呼び出し元へ返す | 呼び出し元が `incomplete`（`legacy-refactor` だけの Issue は呼び出し元が除いて渡す） | 再実行しても同じ |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `final_version` | 消したコミットのツリーに同じ番号の別の要求仕様書がある、または後で同じ番号の別のファイルが足された | 消されたファイルは旧版として除く（順 1） |
| `final_version` | 残り 1 つ | そのファイルの削除を PR の head の履歴から探す。1 つも無ければ `fallback`（配布コミット）の履歴から探す（順 2） |
| `final_version` | 削除直前の内容がすべて同じ | その内容（順 3） |
| `final_version` | 内容が違い、ほかのすべての削除を祖先に持つ削除がある | その削除の直前の内容（順 3） |
| `final_version` | ほか | 止める（順 4） |

- 計算量の注意: 祖先の判定と、head の履歴に削除コミットがあるかの判定は、`parents` をメモリ上でたどる。`git merge-base` は打たないので、削除の候補が増えても Git の起動は増えない。`parents` を作る `git log` はパスで絞らない。パスで絞ると、要求仕様書を変えていないコミットが出力から落ち、親をたどれなくなるためである。要求仕様書のパスへの絞り込みは、出力を読んだ後に行う

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `final_version` | `Git.is_ancestor`・`Git.blobs` | `adapters/git.py` を読んだ | `Git.blobs` は使う（引数で受けて、テストで差し替える）。`Git.is_ancestor` は使わない（呼ぶたびに git を起動する） |
| `branch_deleted`・`release_docs` | `Git.log_of`・`Git.changed_files` | `adapters/git.py` を読んだ | 使えない（親とパスの状態を 1 回で取れない）。新設の `Git.name_status_log` を使う |
| `release_docs` | `domains/release/record_unversioned._first_parent_history` | `grep -rn 'def _first_parent_history'` | 使えない（第 1 親だけをたどり、PR の head の側の削除を見ない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `final_version` | 統合で旧版を消した後に最終版も消すと最終版 | squash で配布コミットに旧版の削除だけが残ると PR の head の最終版。要求仕様書を変えていないコミットとマージを挟んでも、後の削除を選ぶ | 決められなければ止まる。探した順の最初の削除を返さない |
| `branch_deleted` | このブランチで消した版を返す | 起点より前の削除は拾わない | 浅い clone で止まる |
| `release_docs` | リリースブランチで直した版を返す。未マージの `strict` の squash でも読める | マージの後にベースブランチが進んでも同じ版 | git の起動が PR と Issue の数にも削除の候補の数にもよらず各 1 回以下で、`git merge-base` を打たない（`Git` を差し替えて数える） |
| `final_version`（マージの解消） | 片側が消し、他方が直した後のマージで競合を削除で解消すると、直した版を返し、`commit` はその版を持つ親になる | 片側が直してから消し、他方が触らないか元の内容へ戻したマージ（競合が無い）では、消した側の版を返す | 両側が別々に直した内容をマージで消すと、`precondition` で止まる |

## 読むコマンド（`domains/records/requirements_cmd.py`）

満たす決定: 決定 2、受け入れ条件 3・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `add_parsers` | `domains/records/requirements_cmd.py` | 新設 | `(sub: Any) -> None` | `records requirements show`・`stamp`・`pr-body` の引数を登録する（`pr-body` の仕様は「工程へ渡す値の組み立て」） |
| `_show` | `domains/records/requirements_cmd.py` | 新設 | `(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 読み先を選んで要求仕様書を読み、要求仕様書 1 本につき 1 要素の結果 JSON を返す |
| `write_out` | `domains/records/requirements_cmd.py` | 新設 | `(out: Path, docs: Sequence[Doc]) -> dict[str, str]` | 本文を所有者だけが読める権限で書き、パスごとの `body_path` を返す |
| `_stamp` | `domains/records/requirements_cmd.py` | 新設 | `(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `stamp_plan` を呼び、番号ごとの条件の文を結果 JSON で返す |
| `stamp_plan` | `domains/records/requirements_cmd.py` | 新設 | `(root: Path, plan: str, *, git: Git, repo: str, base: str, remote: str) -> list[dict[str, Any]]` | 実装計画の版の行を、1 回の読み出しから作った条件一覧の版で書き直す |
| `_parser` | `domains/records/cli.py` | 変更 | 今のまま | `requirements_cmd.add_parsers` を呼ぶ 1 行を足す |

- `write_out`
  - 副作用: `out` を `worker_paths.open_workdir` で 1 回開き、`write_file(..., owner_only=True)` で置き、返す前に `check_unshared` を通す
- `stamp_plan`
  - 事前条件: `plan` は作業場所の `issues/` の実装計画（`issue_docs.PLAN_PATH` の形で、`review_units_decide.has_units` が真）
  - 事後条件: 変えるのは `受け入れ条件の版: #<番号>=<版>` の行だけ。置き換える直前に読み直し、最初に読んだ内容と違えば何も書かない
  - 副作用: 実装計画を `write_file` で置き換える

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_show` | `--ref` と `--release` を両方渡した | 引数の誤り | argparse の排他のグループ | 止める | 終了コード 2（argparse） | 直して打ち直せる |
| `_show` | 要求仕様書の読み出しの失敗 | `RequirementsError` | `requirements_docs.find`・`release_docs` | 止める | `cause` をそのまま `result.stopped` に渡す。終了コード 1 | 原因を直して打ち直せる |
| `write_out` | 書き先がリンク・ディレクトリ・特殊ファイル、またはほかの利用者が名前を作れる | 共有の環境 | `worker_paths.check_writable`・`check_unshared` | 止める（何も書かない） | `precondition` | 書き先を直して打ち直せる |
| `stamp_plan` | 実装計画の形でない・別の Issue の計画・リンク・外のパス | 引数の誤り | `PLAN_PATH`・`has_units`・番号の重なり・`check_owned` | 止める（何も書かない） | `invalid` | 直して打ち直せる |
| `stamp_plan` | 読んだ後に計画が変わった | 並行する書き込み | 置き換える直前の読み直し | 止める（何も書かない） | `precondition` | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `add_parsers` | `domains/loop/acceptance.add_parsers` の登録の形 | `domains/loop/acceptance.py` を読んだ | 使う（同じ形でサブコマンドを足す） |
| `write_out` | `domains/records/cli._write_result`（`records design check` の書き出し） | `domains/records/cli.py` を読んだ | 使えない（入力と同じファイルへの上書きを断る検査で、所有者だけの権限にしない）。`worker_paths.write_file` を使う |
| `stamp_plan` | `review_units_decide.has_units`・新設の `stamped` | `core/plan/review_units_decide.py` を読んだ | 使う |
| `_show`・`_stamp` | `core/requirements_docs.find`・`requirements_history.release_docs`・`acceptance_record.read_map` | この文書の各節 | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_show` | 2 つの番号が同じ要求仕様書を指すと要素 1 つ | `--out` で `body` の代わりに `body_path` | 読めない理由ごとの `cause` |
| `write_out` | 0600 で置く | 同名の 0644 のファイルがあっても 0600 になる | 書き先がリンク、差し替えられるディレクトリで何も書かない |
| `stamp_plan` | 版の行を書き、番号ごとの文を返す | 版の行が既にあれば置き換える | 要求仕様書・設計書・別の Issue の計画・リンク・外のパス・読んだ後の変更で書かない |

## 確認の記録（`domains/loop/`）

満たす決定: 決定 4・決定 10、受け入れ条件 7・9・10、不変条件 I4・I5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `TableRow` | `core/plan/inputs_decide.py` | 新設（型） | `NamedTuple(kind: str, means: str, version: str, number: int)` | 確認の表の 1 行。`version` は条件の版、`number` は表示用の番号 |
| `Check` | `domains/loop/acceptance_decide.py` | 変更（型） | `TableRow` の別名にする（今の `NamedTuple(kind, means)` から欄が増える） | 確認の記録の 1 行 |
| `RunTable` | `core/plan/inputs_decide.py` | 新設（型） | `NamedTuple(run: str, path: str, rows: tuple[TableRow, ...])` | 実行と要求仕様書の組の表 1 つ |
| `read_tables` | `core/plan/inputs_decide.py` | 新設 | `(body: str) -> dict[tuple[str, str], RunTable]` | `## 受け入れ条件の確認` の下の `### 実行 <印> / <パス>` ごとの表を読む |
| `counted` | `core/plan/inputs_decide.py` | 新設 | `(table: Optional[RunTable], criteria: Sequence[tuple[int, str]]) -> dict[int, TableRow]` | （番号、条件の版）の並びを受け、版が合う行だけを今の番号で返す |
| `recorded_body` | `domains/loop/acceptance_decide.py` | 変更 | `(body: str, run: str, path: str, criteria: Sequence[Criterion], pre_merge: Mapping[int, str], post_release: Iterable[int]) -> str` | 指した組の表だけを書き換え、版の合わない行を残し、ほかの組の表を変えない |
| `table_text` | `domains/loop/acceptance_decide.py` | 新設 | `(table: RunTable) -> str` | 表 1 つを見出しつきの Markdown にする |
| `table_digest` | `domains/loop/acceptance_decide.py` | 新設 | `(text: str) -> str` | 退避するコメントの識別子に使う、表の文の SHA-256 の先頭 12 桁を返す |
| `without_tables` | `domains/loop/acceptance_decide.py` | 新設 | `(body: str, drop: Iterable[tuple[str, str]], expected: Mapping[tuple[str, str], str]) -> tuple[str, list[tuple[str, str]]]` | 文が `expected` と同じ表だけを消し、消せなかった組を返す |
| `_record` | `domains/loop/acceptance.py` | 変更 | 今のまま（`argparse.Namespace -> dict`） | 要求仕様書と代表の Issue を決め、版を照らしてから共通の書き換えで記録する |
| `_show` | `domains/loop/acceptance.py` | 変更 | 今のまま | `--run` なら代表の Issue の表、`--release` なら `acceptance_release.show_release` を返す |
| `_check` | `domains/loop/acceptance.py` | 変更 | 今のまま | 要求仕様書ごとに条件と記録を照らす。`--mode legacy-refactor` だけは `ok` |
| `_check_one` | `domains/loop/acceptance.py` | 変更 | `(doc: Doc, table: Optional[RunTable], wanted: set[int]) -> tuple[list[Criterion], list[Criterion], dict[int, Check]]` | 1 本の要求仕様書の（照らす条件、記録の無い条件、記録）を返す |
| `parse_digests` | `domains/loop/acceptance.py` | 新設 | `(values: list[str], issues: list[int]) -> dict[int, str]` | `<Issue>:<条件一覧の版>` の並びを読む |
| `_new_run` | `domains/loop/acceptance.py` | 新設 | `(a: argparse.Namespace) -> dict[str, Any]` | 無作為な実行の印を `outputs.run` で返す |
| `add_parsers` | `domains/loop/acceptance.py` | 変更 | 今のまま | `--run`・`--criteria-digest`・`--mode`・`new-run`・`prune`・`show` の追加の引数を登録する |
| `show_release` | `domains/loop/acceptance_release.py` | 新設 | `(a: argparse.Namespace) -> dict[str, Any]` | 対応の記録と `--records`（`records` の出力）の版を照らし、配布した要求仕様書と Issue・実行ごとの区分を返す |
| `prune` | `domains/loop/acceptance_release.py` | 新設 | `(a: argparse.Namespace) -> dict[str, Any]` | 閉じた Issue の本文から確認の表を消し、対応の記録にコピーの無い表はコメントへ退避する |
| `_prune_one` | `domains/loop/acceptance_release.py` | 新設 | `(hub: GitHub, bodies: BodyStore, cwd: Path, issue: int, copies: Mapping[tuple[str, str], str]) -> dict[str, Any]` | Issue 1 件の表の退避と削除 |

- `_record`
  - 事前条件: `--run` と `--criteria-digest` がある（無ければ argparse が `invalid` の形で断る）
  - 事後条件: 代表の Issue の `(run, path)` の表に、渡した番号の今の版の行がそろう。ほかの組の表は変わらない
  - 副作用: 代表の番号のロックを取り、`issue_body.rewrite(..., verify=...)` で本文を読み・書き・読み直す
- `_new_run`
  - 事後条件: `secrets.token_hex(6)`（12 桁）を返す。打つたびに違う値になり、既定値を持たない
- `show_release`
  - 事後条件: 要求仕様書 1 本につき 1 要素で、`checks` は Issue ごとに `acceptance_record_decide.chosen_run` の実行の行だけを持つ。実行が無いか印が空の Issue は区分を返さない
- `prune`
  - 副作用: Issue ごとに、まず `issue_body.rewrite` を 1 回呼び、ロックの中で読んだ本文を対応の記録のコピーと比べる。退避する表が無ければ、`change` がコピーと同じ表を消した本文を返して終わる（本文の読みは 1 回）。退避する表があれば、この回の `change` は本文を変えずに返し、退避する表を控える。続けてコメントを読み、識別子 `<!-- ndf:acceptance-table <値> -->` と表の文が同じ信用する書き手のコメントが無いときだけ投稿する。投稿の後に `rewrite` をもう 1 回呼び、ロックの中で読み直した本文から、コピーと同じ表と退避した表を 1 回の PATCH で消す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_record` | 要求仕様書が無い | 要求の工程を通っていない | `requirements_docs.find` | 止める（何も書かない） | `incomplete` | 書いて打ち直せる |
| `_record` | 条件一覧の版が今と違う | 確かめた後に条件が並び替わった | `criteria_list_digest` と `--criteria-digest` の比較 | 止める（何も書かない） | `check_failed`。今の版を書く | 確かめ直して打ち直す |
| `_record` | 無い番号を渡した | 引数の誤り | `criteria` の番号の集合 | 止める | `invalid` | 直して打ち直せる |
| `_record` | 当て直しの上限を超えても記録がそろわない | ロックの外の書き手 | `issue_body.rewrite` の `settled` | 止める | `incomplete`（今と同じ文） | 打ち直せる |
| `_check` | 要求仕様書が無い | 同上 | `find` | 止める | `incomplete`。`--mode legacy-refactor` なら読まずに `ok` | 書いて打ち直せる |
| `_check` | `--criteria` で条件を絞る Issue に対応する `--criteria-digest <Issue>:<条件一覧の版>` が無い、または指定した版が現在の版と違う | 実装計画が古い | `parse_digests` と今の版 | 止める | 無ければ `invalid`、違えば `check_failed` | `stamp` を打ち直す |
| `show_release` | `--records` の Issue の集合か版が今読んだ記録と違う | `records` の後の訂正 | `acceptance_targets` の比較 | 止める（何も返さない） | `precondition`。`records` から打ち直すと書く | 打ち直せる |
| `prune` | 対応の記録を読めない | 記録が無い・編集された | `acceptance_record.read_map` | 止める（本文を変えない） | `precondition` | 打ち直せる |
| `_prune_one` | コメントの投稿の後で本文の更新に失敗 | GitHub の一時的な失敗 | `GitHubError` | 呼び出し元へ返す（その Issue を失敗の項目にする） | 項目に理由。全体は `incomplete` | 打ち直すと投稿を省いて本文の更新から続く |
| `_prune_one` | 退避の後に本文の表が変わった | 画面からの編集 | `without_tables` が返す消せなかった組 | 既定値で続ける（その表を残す） | 項目に残した組を書く | 次の `prune` が同じ手順で扱う |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並行する実装の単位の `record` | 代表の Issue の本文 | `issue_body.BodyLocks(cwd).path(代表の番号)` のファイルロック（同じリポジトリの worktree が共有） | 同じ入力で打ち直すと同じ本文になる |
| `record` と進行・クリーンアップの記録 | 同じ Issue の本文 | 同じロック（番号ごと） | 各自のセクションだけを差し替える |
| `prune` の打ち直し | Issue のコメント | 識別子のコメントの有無 | 投稿済みなら本文の更新から再開する |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_record`・`_check` | 要求仕様書のパス・代表の番号・実行の印・条件の番号と区分 | 結果 JSON の `items`・`outputs` | 条件の文（`gaps` の理由には今と同じく文を載せる） |
| `show_release` | 数えない行と理由 | 結果 JSON の `outputs.skipped` | 要求仕様書の本文（`--out` のときはパスだけ） |
| `prune` | Issue ごとの消した組・退避したコメントの URL・残した組 | 結果 JSON の `items` | 本文の全文 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `read_tables`・`table_text` | `core/plan/inputs_decide.checks_table_spans`・`check_rows`・`split_row`・`cell` | `core/plan/inputs_decide.py` を読んだ | 使う（表の切り出しと行の分割） |
| `counted` | `core/requirements_decide.Criterion` | この文書 | 使えない（`requirements_decide` が `inputs_decide.criteria` を読むので、型を受けると循環する）。（番号、条件の版）の組で受ける |
| `table_digest` | `core/requirements_decide.DIGEST_CHARS` | この文書 | 使う |
| `without_tables` | `inputs_decide.section_span` | 同上 | 使う |
| `parse_digests` | `acceptance.parse_criteria`（`<Issue>:<番号>` の読み方） | `domains/loop/acceptance.py` を読んだ | 使えない（値が番号でなく版）。形の確かめだけ同じ書き方にする |
| `_new_run` | `domains/docrebuild/drive.new_check_id` | `grep -rn 'token_hex' src/ndf` | 使えない（桁数が違う）。`secrets.token_hex` を使う |
| `show_release`・`prune` | `acceptance_record.read_map`・`requirements_history.release_docs`・`issue_body.rewrite` | この文書 | 使う |
| `_prune_one` | `GitHub.comments`・`GitHub.trusts`・`adapters/publish.Publisher.comment` | `adapters/github.py`・`adapters/publish.py` を読んだ | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `counted` | 並べ替え・印・番号の違いで記録が付いたまま | 組の表が無ければ 0 件 | 文を直した条件は記録なし |
| `recorded_body` | 指した組の表だけが変わる | 新しい組は表を足す | 版の合わない行を消さない。統合の前のパスの組は統合の後の表を変えない |
| `_record` | 本文に条件が無くても要求仕様書の番号で書ける。どちらの番号でも代表の Issue に書き、代表の番号のロックを取る | 変更なしは読み 1 回 | 無い番号・版の違い・要求仕様書なしで書かない |
| `_check` | `--criteria` の和集合。指定の無い Issue が混ざれば全条件 | `--no-criteria` は今のまま | 無い番号で失敗。本文の「進行」が `legacy-refactor` でも `--mode` が違えば止まる |
| `_new_run` | 12 桁の 16 進 | 2 回で違う値 | `record`・`check` は `--run` が無ければ `invalid` |
| `show_release` | まとめた要求仕様書の 2 つの Issue が別の実行なら本文 1 本・記録は Issue と実行ごと | すべての実行が `legacy-refactor` の Issue だけが「要求仕様書なし」 | 訂正の後の `--records`、Issue が足されただけの訂正で止まる |
| `prune` | 対応の記録のコピーと同じ表は消え、コピーの無い表は退避してから消える | `--no-release` はすべて退避。コピーと同じ表だけなら本文の読み 1 回・更新 1 回、退避があれば読み 2 回・更新 1 回（呼び出しを差し替えて数える） | `--release` も `--no-release` も無ければ何も変えない。信用しない書き手の同じ識別子では投稿を省かない。退避の間の本文の変更が残る |

## Issue の本文の書き換え（`core/issue_body.py`）

満たす決定: 決定 4、受け入れ条件 10、不変条件 I4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `BodyStore` | `core/issue_body.py` | 新設（型） | `Protocol`（`read(number: int) -> str`・`write(number: int, body: str) -> None`） | 本文の読み書きの差し替え口 |
| `GitHubBodies` | `core/issue_body.py` | 新設（型） | `dataclass(hub: GitHub)` | `BodyStore` の GitHub の実装（読みは `issue_body`、書きは REST の PATCH） |
| `Rewritten` | `core/issue_body.py` | 新設（型） | `NamedTuple(body: str, wrote: bool, settled: bool)` | 最後に読んだ本文・1 度でも書いたか・確かめが通ったか |
| `BodyLocks` | `core/issue_body.py` | 新設（型。`domains/loop/acceptance.lock_path` から移す） | `dataclass(cwd: Path)`（`path(issue: int) -> Path`） | Git の共通ディレクトリの下の、番号ごとのロックのパス。共通ディレクトリは最初の 1 回だけ求める |
| `LockError` | `core/issue_body.py` | 新設（型） | `OSError` の下位 | ロックのディレクトリかファイルを用意できない |
| `rewrite` | `core/issue_body.py` | 新設 | `(store: BodyStore, locks: BodyLocks, issue: int, change: Callable[[str], str], *, verify: Optional[Callable[[str], bool]] = None, retries: int = 0) -> Rewritten` | ロックの中で読み、`change` で差し替え、変わったときだけ PATCH し、`verify` があれば読み直して確かめる |

- `rewrite`
  - 事後条件: 本文が変わらなければ読み 1 回で終わる。変われば読み 1 回・PATCH 1 回、`verify` があれば読み直し 1 回。`verify` が偽なら `retries` 回まで読んだ本文からやり直す
  - 副作用: ロックファイル（`filelock`。待ちは `limits.seconds("locks.append")`）。GitHub の読みと PATCH

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `rewrite` | ロックを取れない | 別の書き手が長く持つ | `filelock.Timeout` | 呼び出し元へ返す | 呼び出し元が `store_in_use`（今の確認の記録と同じ） | 打ち直せる |
| `rewrite` | ロックを用意できない | Git の共通ディレクトリに書き込めない | ディレクトリの作成とロックの取得の `OSError` | `LockError` にして呼び出し元へ返す | 進行の記録は対象ごとの誤り、クリーンアップは対象ごとの `failed` の項目。確認の記録は `LockError` を捕まえず、`ndf` コマンドの共通の入口が結果 JSON にする（`status` は `stopped`、`cause.kind` は `unknown`、終了コードは 1） | 書き込めるようにして打ち直せる |
| `rewrite` | 読み・書きの失敗 | GitHub の失敗 | `GitHubError` | 呼び出し元へ返す | 呼び出し元の今の扱い（進行の記録は誤りに載せる、クリーンアップは `failed` の項目） | 打ち直せる |
| `rewrite` | 上限まで確かめが通らない | ロックの外の書き手 | `verify` | 呼び出し元へ返す（`settled=False`） | 呼び出し元が `incomplete` | 打ち直せる |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 確認の記録・進行の記録・クリーンアップの記録・`prune` | 同じ Issue の本文 | 番号ごとのファイルロック。確認の記録と `prune` は代表の番号、ほかは書き換える Issue の番号 | `change` は同じ入力で同じ本文を返す。画面の編集とは排他にならない（設計文書の「Issue の本文の書き換え」） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `BodyStore`・`GitHubBodies` | `progress/records.Records`・`GitHubRecords` | `progress/records.py` を読んだ | 使う（`core` へ移し、`progress/records` はここを読む） |
| `BodyLocks` | `domains/loop/acceptance.lock_path` | `domains/loop/acceptance.py` を読んだ | 使う（移す。ロックのパスは変えない） |
| `rewrite` | `domains/loop/acceptance._reapply` | 同上 | 使う（当て直しの形を一般にして移す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `rewrite` | 確認の記録と進行の記録が重なっても両方残る（ストアを差し替える） | 変更なしは読み 1 回で PATCH しない | ロックの外で読んだ本文を PATCH しない。上限を超えると `settled=False` |

## モードの判定の材料（`domains/materials/`）

満たす決定: 決定 7・決定 10、受け入れ条件 6・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `issue_materials` | `domains/materials/collect.py` | 変更 | `(number: int, body: str, files: list[str], designs: list[tuple[str, list[str]]], doc: Optional[Doc]) -> dict[str, Any]` | 種類と公開インタフェースを要求仕様書から読み、無ければ `記載なし`。候補は報告の本文と要求仕様書の両方から拾う |
| `gather` | `domains/materials/collect.py` | 変更 | `(root: Path, numbers: list[int], files: list[str], body_of: Callable[[int], str], docs: Mapping[int, Doc]) -> Gathered` | 番号ごとの要求仕様書を受けて材料を作る |
| `main` | `domains/materials/cli.py` | 変更 | 今のまま | `requirements_docs.lookup` を 1 回呼び、`missing` は要求仕様書なしとして扱う |

- `issue_materials`
  - 事後条件: `doc` があれば `issue_kind(doc.body)`・`public_interface(doc.body)`。無ければ両方 `記載なし`。本文の種類と影響は使わない。候補の `source` に `要求仕様書` が加わる（`materials.result.schema.json` の列挙に足す）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `main` | 要求仕様書が 2 本・URL の欠け・検査に通らない | `RequirementsError`（`incomplete` 以外） | `lookup` | 止める | 同じ `cause` で `result.stopped` | 直して打ち直せる |
| `main` | 要求仕様書が無い | 要求の工程の前 | `Lookup.missing` | 既定値で続ける | `ok`。種類と公開インタフェースは `記載なし` | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `issue_materials` | 本文に種類と影響が無く要求仕様書にあれば、その値 | 要求仕様書が無ければ `記載なし` で候補は本文から | 本文に種類と影響があっても使わない |
| `main` | まとめた要求仕様書をどちらの番号でも引ける | — | 2 本で止まる |

## 工程へ渡す値の組み立て（`progress/units_invoke.py`・`progress/issue_values.py` ほか）

満たす決定: 決定 9・決定 10、受け入れ条件 7・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Unit` | `core/plan/review_units_decide.py` | 変更（型） | 今の欄に `digests: tuple[tuple[int, str], ...]` を足す | 単位が指す Issue ごとの条件一覧の版 |
| `criteria_versions` | `core/plan/review_units_decide.py` | 新設 | `(text: str) -> dict[int, str]` | `受け入れ条件の版: #<番号>=<版>` の行を読む |
| `outside_units` | `core/plan/review_units_decide.py` | 新設 | `(text: str) -> tuple[tuple[int, int, str, str], ...]` | `単位の外: #<番号>:<条件の番号>（<区分>、<記録する担当>）` の行を読む |
| `stamped` | `core/plan/review_units_decide.py` | 新設 | `(text: str, digests: Mapping[int, str]) -> str` | 版の行を書き足すか置き換えた実装計画の文を返す |
| `lineup` | `core/plan/review_units_decide.py` | 変更 | `(plans: Sequence[PlanDoc], criteria: Mapping[int, int], issues: Sequence[int], digests: Optional[Mapping[int, str]] = None) -> tuple[Unit, ...]` | `digests` があれば版の行と照らし、どの単位にも単位の外にも無い条件を断る |
| `acceptance_args` | `core/plan/review_units_decide.py` | 変更 | `(unit: Unit, run: str, mode: str) -> list[str]` | `--run`・`--mode`・`--criteria-digest` を足した `check` の引数 |
| `launch_values` | `core/route/__init__.py` | 変更 | 今のまま | `acceptance` の補いに値 `run`・`mode` があれば `--run`・`--mode` を足す |
| `check_acceptance` | `core/route/__init__.py` | 変更 | 今のまま | `--run` の無い並びを断る（`ACCEPTANCE_FLAGS` に `--run`・`--mode`・`--criteria-digest` を足す） |
| `_by_body_from` | `core/route/__init__.py` | 新設 | `(plan: Mapping[str, Any], values: Mapping[str, Value]) -> dict[str, Any]` | `body_from` を持つステップの `run` の前につなぐか、キーを捨てる。`rendered` が埋める直前に呼ぶ |
| `_fill_from_issue` | `progress/invoke.py` | 変更 | 今のまま | 工程の入力の `mode` と、文脈の実行の印（`requirements_stage.context_run`）を値 `mode`・`run` に入れる |
| `_read_units` | `progress/units_invoke.py` | 変更 | `(g: Git, remote: str, plans: Sequence[PlanDoc], issues: Sequence[int], mode: str, root: Path) -> tuple[Unit, ...]` | `legacy-refactor` は今のまま本文から数え、ほかは要求仕様書から数と版を作る |
| `_unit_json`・`_held_lineup` | `progress/units_invoke.py` | 変更 | 今のまま | `units.json` に版を書き、読むときに今の版と違えば並びを作り直す |
| `_all_recorded` | `progress/units_invoke.py` | 新設 | `(ctx: Context) -> None` | すべての単位のマージの後に、全 Issue の全条件を代表の Issue の本文の 1 回の照会で照らす。`legacy-refactor` は何も読まない |
| `sprint_pr` | `progress/units_invoke.py` | 変更 | 今のまま | PR を開く前に `_all_recorded` を呼ぶ |
| `requirements_refs` | `progress/issue_values.py` | 新設 | `(docs: Sequence[Doc], issues: Sequence[int], repo: str) -> str` | 要求仕様書ごとのリンクの行と、全 Issue の `Refs #<番号>` の行を作る |
| `_pr_body` | `domains/records/requirements_cmd.py` | 新設 | `(args: argparse.Namespace, root: Path) -> dict[str, Any]` | push した head の要求仕様書から `requirements_refs` の文を作り、`--out` のファイルを置き換える |

- `_all_recorded`
  - 事前条件: 工程の入力のモードを `requirements_decide.needs_requirements` で確かめる。偽（`legacy-refactor`）なら、要求仕様書も Issue の本文も読まずに戻る。単位ごとの `check`・`_read_units`・`pr-body` も同じ関数でモードを分ける
  - 事後条件: 記録の無い条件があれば、条件を挙げて `UnitsStopped("incomplete")`。区分 `リリース後` の行は検証の結果が無くても記録ありに数える
  - 副作用: `domains/loop/acceptance.read_bodies` と同じ形の 1 回の GraphQL の照会（`GitHub.nodes`）。子プロセスは起動しない
- `requirements_refs`
  - 事後条件: リンクは `https://github.com/<repo>/blob/<Doc.commit>/<path>`。要求仕様書の行は重なりなし、`Refs #<番号>` は `issues` の全件
- `_pr_body`（`ndf records requirements pr-body --issue=<番号>... --mode=<モード> --branch=<ブランチ> --remote=<名前> --out=<ファイル> [-- <続けて打つコマンド>...]`）
  - 事前条件: head は作業場所の `HEAD` を 1 回だけ解決した SHA。`--remote` の `refs/heads/<ブランチ>`（`git ls-remote`）がその SHA であることを、打つたびに照会する。本文のリンクと `ls-remote` のリポジトリは `--remote`（`Git.remote_repo`）で、`gh pr create` の作り先もこれと同じであること。作り先は、環境変数 `GH_REPO` があればその値、無ければ `gh repo view --json nameWithOwner` の値で、大文字と小文字を区別せずに比べる（`gh repo view` は `GH_REPO` を読まない）。`requirements_docs.find(..., ref=<SHA>)` で読み、作業場所の要求仕様書（`worktree_candidates`）が読んだ内容と同じであること
  - 事後条件: `--issue` が 0 件か `--mode` が `legacy-refactor` なら、何も読まず書かずに `ok`。ほかは `--out` の中身が `requirements_refs` の文だけになる。モードが空なら要求仕様書を求める
  - 副作用: `--out` を `worker_paths.write_file(private=True)` で置き換え、`check_unshared` を通す
  - `--` の後にコマンドがあるとき: 成功したら結果 JSON を出さず、そのコマンドへ `os.execvp` で置き換わる（標準出力の結果と終了コードは続きのコマンドのもの）。失敗したら続きを打たずに、停止の結果 JSON を出す。テストは置き換えの関数を差し替える

**PR の本文は、PR を作るコマンドの直前に head から作り直す。** 独立したステップは足さず、設計と実装のテンプレート（`contracts/templates/design.json`・`impl.json`）の `pr` のステップに、前に置くコマンド `body_from` を足す。

```json
{"use": "pr", "body_from": ["ndf", "records", "requirements", "pr-body", "--issue={{refs}}", "--mode={{mode}}", "--branch={{branch}}", "--remote={{remote}}", "--out={{body_file}}", "--"]}
```

テンプレートを埋めるコード（`core/route`）が、値を見て `body_from` を扱う。埋めた後のプランに `body_from` のキーは残らない。

| `refs` と `mode` | `pr` のステップの `run` |
| --- | --- |
| `refs` が 1 件以上で、`requirements_decide.needs_requirements(mode)` が真 | `body_from` の後ろに共有の `run` をつないだ並び |
| `refs` が空か、`mode` が `legacy-refactor` | 共有の `run` のまま。`pr-body` は起動しない |

- 2 つのテンプレートの `params` に `refs` と `mode` を足し、`params.json` の `mode` の既定は空の文字列にする。`ndf progress` から組むと、`refs` は工程の入力のすべての Issue（今の `invoke._fill_refs`）、`mode` は工程の入力が入る。単位の経路の実装のプランも同じ値になる。`invoke` と `units_invoke` はこのために変えない
- `issue_values` が、プランを組む時点に本文のファイルを書く処理は変えない（このモジュールに足すのは `requirements_refs` である）。プランを組む時点では今のまま Issue の本文を `body_file` に書き、`legacy-refactor` ではそれがそのまま PR の本文になる。ほかのモードは `pr-body` が置き換える。組む時点で要求仕様書を読まないのは、その時点の `HEAD` が PR の head ではなく、worker のコミットの前の内容を指すためである
- `ndf route render`・`run` の単発の利用で `refs` を省くと、渡した `body_file` がそのまま本文になる。`refs` を渡すと `body_file` を置き換えるので、`params.json` の `body_file` の説明に書く
- `pr` のステップから打ち直しても `pr-body` を通るので、リモートの head を照会し直す。前の push の後に直したコミットを push していない場合も、共同作業者がリモートのブランチを進めた場合も、head が違うので PR を作らずに止まる
- PR が既にあれば、今のまま `pr` のステップの `probe` がその番号を使い、`run` を打たない。本文は書き換えず、`pr-body` も起動しない
- push した SHA を実行の保存先に控えて照会を省く案は採らない。控えは前に push した事実だけを示し、その後にリモートが進んだことを検出できない。省けるのは PR 1 本につき `git ls-remote` 1 回で、控えを持つ理由になる実測も無い（Value 8）
- 置き場所を `ndf records requirements` にする理由は 2 つある。要求仕様書を読むコマンドと同じ読み方を使えることと、`domains` から `progress` の `requirements_refs` を呼ぶ向きが層の決まりに合うことである。共有の `push`・`pr` のステップ（`parts/steps.json`）と `pr` のテンプレートは変えないので、コミットから本文を組む経路と `fix`・`close` のテンプレートには影響しない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `lineup` | 版の行が無い・今の版と違う | 実装計画を書いた後に条件を直した | `criteria_versions` と `digests` | 止める | `UnitsUnreadable` → `invalid`（理由に Issue と今の版） | `stamp` を打ち直す |
| `lineup` | どの単位にも単位の外にも無い条件 | 割り当ての漏れ | 条件の集合の差 | 止める | `invalid`。条件を挙げる | 実装計画を直す |
| `_read_units` | 要求仕様書を読めない | `RequirementsError` | `find` | 止める | `UnitsStopped(<cause>)` | 直して打ち直せる |
| `_all_recorded` | 記録の無い条件 | 単位の外の条件・飛ばした単位 | `inputs_decide.counted` | 止める（PR を作らない） | `incomplete`。条件を挙げる | 記録してから打ち直す |
| `_pr_body` | head に要求仕様書が無い・読めない | 要求仕様書をコミットしていない、元の Issue の行が欠ける | `find(ref=head)` の `RequirementsError` | 止める（`--out` を書かず、PR を作らない） | `cause` をそのまま返す（無ければ `incomplete` で番号を `gaps` に載せる）。終了コード 1。プランは `pr` で止まる | コミットして push し、打ち直す |
| `_pr_body` | 作業場所の要求仕様書が head の内容と違う | 未コミットの直し | 作業場所と head の内容の比較 | 同上 | `precondition`。パスを書く。終了コード 1 | コミットして push し、打ち直す |
| `_pr_body` | リモートの head が作業場所の `HEAD` と違う・照会できない | 直した後に push していない、共同作業者がリモートを進めた、リモートに届かない | `git ls-remote` の結果 | 同上 | `precondition`。手元とリモートをそろえて push するよう案内する。終了コード 1 | そろえて打ち直す |
| `_pr_body` | `gh` の作り先が `--remote` のリポジトリと違う・調べられない | GitHub CLI の既定リポジトリを別に設定した | `GH_REPO` か `gh repo view` の値の比較 | 同上 | `precondition`。両方の名前と `gh repo set-default` を案内する。終了コード 1 | 設定をそろえて打ち直す |
| `_pr_body` | `--out` を差し替えられる・リンク | 共有の環境 | `worker_paths` の検査（`EntryUnsafe`） | 同上 | `precondition`。終了コード 1 | 権限を直して打ち直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `criteria_versions`・`outside_units` | `review_units_decide._parse`（markdown-it-py） | `core/plan/review_units_decide.py` を読んだ | 使えない（表の外の行を読む）。行の正規表現で読む |
| `stamped` | `inputs_decide.section_span` | `core/plan/inputs_decide.py` を読んだ | 使えない（版の行は節に属さない）。行の置き換えで書く |
| `_all_recorded` | `domains/loop/acceptance.read_bodies` | `domains/loop/acceptance.py` を読んだ | 使えない（`domains` の層にあり `progress` から呼べない）。同じ `GitHub.nodes` を呼び、表は `inputs_decide.read_tables`・`counted` で読む |
| `requirements_refs` | `pr` のテンプレートの `Refs` の行（`contracts/templates/pr.json`） | `grep -rn Refs contracts/templates` | 使えない（コミットの件名から組む）。同じ行の形にそろえる |
| `_pr_body` | `_show` の `--ref`・`--out`、`write_out` | 「読むコマンド」の節 | 使えない（要求仕様書の本文を書き出し、PR の本文の形を作らず、head の照らしもしない）。読み出しは `requirements_docs.find`、書き出しは `worker_paths.write_file` を使う |
| `pr-body` を `pr` の前に置く形 | `ndf route test -- <コマンド>`（コマンドを後ろに受ける形）・`parts/steps.json` の `pr`・`push` の `probe` | `contracts/templates/parts/steps.json` を読んだ | `ndf route test` と同じく `--` の後に受ける。共有の `push`・`pr` は変えない。head の照らし方は `push` の `probe` と同じにする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `lineup` | 版が合えば今の並び | `digests` が None（`legacy-refactor`）は今のまま | 並べ替えの後の古い版・版の行の無い計画・割り当ての漏れで止まる |
| `acceptance_args` | `--run`・`--mode`・`--criteria-digest` が付く | 補った単位も `--run` を持つ | — |
| `_all_recorded` | 全件に記録があれば通る。区分 `リリース後` の記録だけで通る | 要求仕様書 3 本でも照会 1 回（呼び出しを差し替えて数える）。`legacy-refactor` は単位をすべてマージした後、要求仕様書も確認の行も無いままスプリント PR を開け、照会は 0 回 | 単位の外の条件に記録が無ければ止まる |
| `requirements_refs` | 要求仕様書ごとのリンクと全 Issue の `Refs` | 2 つの Issue が 1 本を指すとリンクは 1 行、`Refs` は 2 行 | `Refs` が 1 件欠けると落ちる |
| `_pr_body` | `--out` が head のコミットを指すリンクと `Refs` に置き換わり、続きのコマンドへ置き換わる | `legacy-refactor` と `--issue` 0 件は `--out` を変えない。モードが空なら要求仕様書を求める。`git ls-remote` と作り先の照会は打つたびに各 1 回（呼び出しを差し替えて数える。`GH_REPO` があれば `gh` は 0 回） | head に無い・作業場所と違う・リモートの head と違う・`gh` の作り先が違う場合は、`--out` を変えず、続きを打たずに止まる |
| 設計と実装のテンプレート | `refs` と要求仕様書を持つモードでは `pr` の `run` が `pr-body` で始まる | `refs` が空か `legacy-refactor` では共有の `pr` と同じ並びで、`pr-body` が現れない。単位の経路の実装のプランも同じ | 組んだプランに `body_from` のキーが残ると `model.parse` で落ちる |

## 進行の記録・クリーンアップの記録（`progress/records.py`・`core/run_trailer_decide.py`・`domains/records/cleanup_stage.py`）

満たす決定: 決定 4、受け入れ条件 10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `GitHubRecords`・`Records` | `progress/records.py` | 変更 | `core/issue_body.GitHubBodies`・`BodyStore` を指す名前にする | 読み書きの型を共通にする |
| `run_lines` | `core/run_trailer_decide.py` | 新設 | `(issues: Sequence[int], tables: Sequence[tuple[int, str]], run: str, mode: str) -> str` | 「通した工程」に足す `対象の Issue`・`確認の表`・`実行`・`モード` の行を作る |
| `pr_section` | `progress/records.py` | 変更 | `(v: View, tables: Sequence[tuple[int, str]]) -> str` | 今の工程の行の前に `run_trailer_decide.run_lines` の行を置く |
| `sync_records` | `progress/records.py` | 変更 | 今のまま | 書き先ごとに `issue_body.rewrite` で書く。PR の書き先では `requirements_docs.lookup` で `tables` を作る |
| `record_issue` | `domains/records/cleanup_stage.py` | 変更 | `(store: BodyStore, locks: BodyLocks, issue: int, stamp: str) -> dict[str, Any]` | `issue_body.rewrite` で工程「クリーンアップ」の記録を付ける |
| `record_cleanup_stage` | `domains/records/cleanup_stage.py` | 変更 | `(hub: GitHub, prs: Iterable[int], cwd: Path) -> list[dict[str, Any]]` | `record_issue` へストアと、実行で 1 つの `BodyLocks` を渡す |

- `sync_records`
  - 事後条件: 本文が変わらない書き先は読み 1 回で終わる（今と同じ回数）
  - 副作用: 手元の要求仕様書の読み出し（`lookup`）が PR の書き先 1 件につき 1 回。GitHub の呼び出しは増えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `sync_records` | 要求仕様書を引けない（PR の書き先） | `RequirementsError` | `lookup` | 呼び出し元へ返す（誤りに載せる） | 今の「進捗記録を書けない」の文に理由 | 打ち直せる |
| `record_issue` | ロックを取れない | 並行する書き込み | `filelock.Timeout` | 呼び出し元へ返す | `failed` の項目 | 打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `run_lines` | `pr_section` の行の組み立て | `progress/records.py` を読んだ | 使う（同じ行の形にする）。行の語は `run_trailer_decide.TRAILER_WORDS` を読み、読む側の `parse_trailer` と同じ語にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `run_lines`・`parse_trailer` | 要求仕様書 2 本で `確認の表` の行が 2 つ。書いた行を読み戻すと同じ値 | Issue 1 つ | 行の無い本文は None |
| `sync_records`・`record_issue` | 今のテストが通る | 変更なしで PATCH しない | 確認の記録と重なっても両方残る |

## リリースの対応の記録（`domains/release/`・`domains/records/`）

満たす決定: 決定 2・決定 4、受け入れ条件 7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `RunEntry` | `domains/release/acceptance_record_decide.py` | 新設（型） | `NamedTuple(pr: int, head: str, base: str, merged_at: str, issues: tuple[int, ...], run: str, mode: str, tables: tuple[dict[str, Any], ...])` | `runs` の 1 件 |
| `AcceptanceMap` | 同上 | 新設（型） | `NamedTuple(commit: str, release_pr: Optional[int], release_head: Optional[str], runs: tuple[RunEntry, ...], targets: dict[int, str], version: str, unit: Optional[str])` | 対応の記録 |
| `Trailer` | `core/run_trailer_decide.py` | 新設（型） | `NamedTuple(issues: tuple[int, ...], tables: tuple[tuple[int, str], ...], run: str, mode: str)` | 「通した工程」の行 |
| `parse_trailer` | 同上 | 新設 | `(body: str) -> Optional[Trailer]` | PR の本文の「通した工程」から実行の行を読む |
| `canonical` | `domains/release/acceptance_record_decide.py` | 新設 | `(value: Any) -> bytes` | キーを名前順、空白なし、UTF-8 の JSON。`tables` はパス、`rows` は条件の版の順に並べる |
| `target_digest` | 同上 | 新設 | `(*parts: Any) -> str` | `canonical` をつないだものの SHA-256 の先頭 12 桁 |
| `first_targets` | 同上 | 新設 | `(m: AcceptanceMap) -> dict[int, str]` | 最初の記録の Issue ごとの版（`commit`・番号・選ぶ実行の値。入力は設計文書の「確認対象の版」） |
| `corrected_targets` | 同上 | 新設 | `(prev: Mapping[int, str], before: Mapping[int, Any], after: Mapping[int, Any]) -> dict[int, str]` | 訂正の前後の読み出し結果を比べ、同じなら引き継ぎ、違えば前の版とつないで新しい版にする |
| `chosen_run` | 同上 | 新設 | `(m: AcceptanceMap, issue: int) -> Optional[RunEntry]` | その Issue を含み、マージが最も新しい実行（`merged_at` が空なら最も新しい） |
| `valid_map` | 同上 | 新設 | `(comments: Sequence[tuple[Authored, bool]], version: str, unit: Optional[str]) -> Optional[AcceptanceMap]` | 信用する書き手の記録のうち、訂正が無ければ最初、あれば最も新しい訂正を選ぶ。編集されたものがあれば例外 |
| `map_of` | 同上 | 新設 | `(raw: Mapping[str, Any], version: str, unit: Optional[str]) -> Optional[AcceptanceMap]` | JSON を型にし、形が違えば None |
| `build_map` | `domains/release/acceptance_record.py` | 新設 | `(hub: GitHub, git: Git, rec: Record, release_pr: Optional[int], release_head: Optional[str], commit: str) -> AcceptanceMap` | 含めた PR と `確認の表` の Issue の本文をまとめて 1 回ずつ読み、対応の記録を作る |
| `read_map` | 同上 | 新設 | `(hub: GitHub, pr: int, version: str, unit: Optional[str]) -> AcceptanceMap` | 記録の PR のコメントを 1 回読み、有効な対応の記録を返す |
| `record_acceptance` | 同上 | 新設 | `(args: argparse.Namespace) -> dict[str, Any]` | 手で置いたリリース記録に対応の記録を足すか、`--reason` で訂正する |
| `add_record_acceptance` | 同上 | 新設 | `(sub: Any) -> None` | `release-steps record-acceptance` の引数を登録する |
| `Record` | `domains/release/record_decide.py` | 変更（型） | 今の欄に `acceptance: Optional[dict[str, Any]] = None` を足す | リリース記録の JSON に `acceptance` を持つ |
| `render_record`・`parse_record_json` | 同上 | 変更 | 今のまま | `acceptance` を書き、読む |
| `to_test`・`_pair_rows` | 同上 | 変更 | `to_test(released, issues, verifications, targets: Optional[Mapping[int, str]] = None)`・`_pair_rows(verifications, environment, issue, target: Optional[str] = None)` | `targets` があれば版が今と同じ行だけを数える |
| `write` | `domains/release/record.py` | 変更 | 今のまま | ほかの PR より先に記録の PR（`included` の最も小さい番号）へ書き、そこに有効な `acceptance` があれば作り直さない |
| `write_verification` | 同上 | 変更 | `(..., records: Optional[Mapping[str, Any]] = None)` | 記録の行の `target` に `acceptance_targets` の版を書く |
| `_require_retest_reasons` | 同上 | 変更 | 今のまま | 版が今と同じ行だけから再テストの理由を求める |
| `write_unversioned`・`_made` | `domains/release/record_unversioned.py` | 変更 | 今のまま | 最初の届け先の記録の PR に `acceptance` を入れ、2 つ目以降と `--correct-targets` では入れない |
| `read_release`・`_records_outputs` | `domains/release/record_read.py` | 変更 | 今のまま | 対応の記録があれば `outputs.issues`・`acceptance_targets` を返し、`bodies` は `null`、`to_test` は常に返す |
| `cmd_records`・`cmd_verification_record`・`_parser`・`_records_selection` | `domains/release/cli.py` | 変更 | 今のまま | `--records` の受け渡しと `record-acceptance` の登録 |
| `current_rows` | `domains/records/close_decide.py` | 新設 | `(issue: int, rows: Sequence[Mapping[str, Any]], target: Optional[str]) -> tuple[list[Mapping[str, Any]], bool]` | 版が今と同じ行と、版の違う行か版の無い行があるかを返す |
| `records_verdict`・`_targets_verdict`・`_last_check` | 同上 | 変更 | 今のまま | `view` の `acceptance_targets` があれば、設計文書の `close-check` の 2 つの表で判定する |
| `read_release`・`_records_outputs` の `released_at` | `domains/release/record_read.py` | 変更 | 今のまま | 選んだ値と単位の最後のリリース記録の投稿時刻を `outputs.released_at` に足す（`records_outputs` のスキーマにも足す） |
| `_records_view` | `domains/records/cli.py` | 変更 | 今のまま | `records close-release` の `not_applicable` の結果も、`outputs` が `records_outputs` に合えば受ける |
| `_skipped_deploy` | `domains/records/cli.py` | 新設 | `(root: Path, machine_id: str, issue: int, rows: Sequence[Mapping[str, Any]]) -> Optional[str]` | `machine_id` のステートマシンでデプロイの工程が飛ばされ、Issue がその入力にあり、記録の `state_machine` が `machine_id` と同じかを確かめ、違えば止める理由を返す |
| `_judged`・`_add_close_check` | 同上 | 変更 | 今のまま | `--verification` に `--skipped-deploy` を必須にする |
| `ClosedEntry` | `domains/records/close_decide.py` | 新設（型） | `NamedTuple(issue: int, target: str, closed_at: str)` | このコマンドが閉じた 1 件 |
| `close_verdict` | 同上 | 新設 | `(issue: int, view: Mapping[str, Any], conditions: Mapping[str, Condition], stage: str, retro_after: bool) -> Verdict` | 設計文書の「閉じる条件の判定」。`records_verdict` の合否に、`stage` の最終工程の記録を足す。閉じる前と照合し直すときの両方が呼ぶ |
| `after_close` | 同上 | 新設 | `(entry: ClosedEntry, targeted: bool, closable: bool, state: str, closed_at: str) -> str` | `keep`・`reopen`・`report` を返す。`closed_at` が違うか開いていれば `report`、対象から外れたか閉じてよくなければ `reopen`、ほかは `keep`。`closable` は読み直した版での `close_verdict` が `CLOSE` かで、`entry.target` との違いだけでは開き直さない |
| `state_path` | `domains/records/close_release.py` | 新設 | `(main_root: Path, record_pr: int, version: str, unit: Optional[str]) -> Path` | `.ndf/closing/pr<記録の PR>-<値と単位の SHA-256 の先頭 12 桁>.json` |
| `read_state`・`write_state` | 同上 | 新設 | `(path: Path) -> list[ClosedEntry]`・`(path: Path, entries: Sequence[ClosedEntry]) -> None` | 状態ファイルを読み、置き換えて書く |
| `close_release` | 同上 | 新設 | `(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 設計文書の「閉じた後の照合し直し」の順 1〜7 |
| `add_close_release` | 同上 | 新設 | `(sub: Any) -> None` | `--pr`・`--version`・`--unit`・`--release`・`--require` と、`--stage`（`デプロイ`・`リリース後テスト`・`振り返り` のどれか。必須）を登録する |

- `build_map`
  - 事前条件: `included` の PR はすべてマージ済み。`release_pr` と同じ PR だけは `strict` の squash の未マージを許す
  - 事後条件: `runs` は単位の `included` の PR だけから作る。「通した工程」の行が無い PR は `Refs` と closing keywords の Issue を `issues` にし、`run`・`mode` を空、`tables` を空にする
  - 副作用: `GitHub.pulls`・`GitHub.nodes`（PR と Issue の本文、各 1 回）と、未照会の書き手ごとの `GitHub.permission`
- `record_acceptance`
  - 事後条件: 有効な `acceptance` があれば何も書かない。無ければ元の記録を指すコメントを 1 回追記する。`--reason` なら前後の `release_docs` の読み出しを比べた `targets` を持つ訂正を追記し、元を残す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `build_map` | マージしていない PR・配布コミットを確かめられない・単位を選べない | 記録の前提が欠ける | `GitHub.pulls`・`record.landed_commit`・単位の数 | 止める（何も書かない） | `precondition` | 前提を満たして打ち直せる |
| `read_map` | 記録が無い・編集された・形が違う | 旧い記録・手での編集 | `valid_map`・`map_of` | 止める | `precondition` | `record-acceptance` を打つ |
| `write` | 途中の PR で失敗 | GitHub の失敗 | `GitHubError` | 呼び出し元へ返す | 今のまま | 打ち直すと記録の PR の `acceptance` は 1 回目のまま |
| `write_verification` | 対応の記録がある値で `--records` が無い | 手順の漏れ | `read_map` の結果 | 止める（何も書かない） | `precondition` | `records` を打って渡す |
| `_skipped_deploy` | デプロイを通った実行・入力に無い Issue・別の実行の記録 | 誤った経路 | ステートマシンの行と記録の `state_machine` | 止める | `precondition` | 正しい ID で打ち直す |
| `close_release` | 対応の記録が無い | 旧い記録 | `read_release` の結果に `acceptance_targets` が無い | 止める（何も書かない） | `not_applicable`。`outputs` に読み出し結果を載せる | その出力を保存し、今の手順（`close-check` と `gh issue close`）で閉じる |
| `close_release` | close は成功したが状態を書く前に中断 | プロセスの停止 | 打ち直しで、閉じているのに状態ファイルに無い | 既定値で続ける（開き直さない） | `already_closed`。閉じてよくなければ理由を添える | 人が決める |
| `close_release` | 読み直し・開き直し・表の整理の失敗 | GitHub の失敗 | `GitHubError` | 止める（読み直しと開き直しの失敗では状態ファイルを残す。整理の失敗では閉じた Issue を開き直さない） | `incomplete` | 打ち直すと続きから進む |
| `close_release` | ロックを取れない | 二重の起動 | `filelock.Timeout`（状態ファイルごと） | 止める | `store_in_use` | 打ち直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `records_verdict` | 数える行がある | バージョン番号を持つリポジトリは最後の 1 件、持たないリポジトリは `pair_verdict` でまとめる |
| `records_verdict` | 数える行が無く、版の違う行か版の無い行がある | 閉じない（「再検証待ち」） |
| `records_verdict` | 数える行が無く、`--require` が有効で、設定でリリース後テストの条件を外していない | 閉じない |
| `records_verdict` | 次のすべてに当たる。数える行が無い。訂正でその Issue の確認対象の版が変わったか、訂正でその Issue が足された。同じリリースと単位にテストの記録が 1 件以上ある | 閉じない（設定でリリース後テストの条件を外していれば当てない） |
| `records_verdict` | ほか | 今の「その Issue の行が無い」扱い |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `ACCEPTANCE_KEY` | 定数 | `domains/release/acceptance_record_decide.py` | `"acceptance"`（リリース記録の JSON のキー。スキーマの正は `release-verification.schema.json`） |
| `TRAILER_WORDS` | 定数 | `core/run_trailer_decide.py` | `("対象の Issue", "確認の表", "実行", "モード")`（`run_lines` と `parse_trailer` がここを読む） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `build_map` | `record.released`・`landed_commit`・`included_pulls`・`GitHub.pulls`・`GitHub.nodes`・`GitHub.trusts` | `domains/release/record.py`・`adapters/github.py` を読んだ | 使う |
| `build_map`（行の無い PR の Issue） | `adapters/github_pulls.closing_keyword_issues` | `grep -rn closing_keyword_issues` | 使う |
| `valid_map` | `record_read.candidates`（信用する書き手と訂正の選び方） | `domains/release/record_read.py` を読んだ | 使えない（バージョンと届け先の一覧の選び方で、最初の投稿を選ばない）。`Authored.edited` と `GitHub.trusts` は使う |
| `canonical`・`target_digest` | `record_decide._json_block` | `domains/release/record_decide.py` を読んだ | 使えない（表示用で、キーの順と空白を決めない） |
| `corrected_targets` | `requirements_history.release_docs` | この文書 | 使う（前後の読み出し） |
| `chosen_run` | 無し | `grep -rn merged_at src/ndf/domains/release` | 新設 |
| `first_targets`・`map_of` | `record_decide.parse_record_json`（JSON から型へ） | `domains/release/record_decide.py` を読んだ | 使えない（リリース記録の欄だけを読む）。同じ形の確かめ方（`_is_int`・`_numbers`）は使う |
| `read_map` | `record._trusted_bodies`・`GitHub.comments` | `domains/release/record.py` を読んだ | 使えない（本文だけを返し、`edited` と投稿の順を落とす）。`GitHub.comments` を直に使う |
| `record_acceptance`・`add_record_acceptance` | `cli.cmd_record` の引数の形 | `domains/release/cli.py` を読んだ | 使う（`--pr`・`--version`・`--unit` を同じ名前にする） |
| `current_rows` | `close_decide._rows_of` | `domains/records/close_decide.py` を読んだ | 使う（Issue で絞った後に版で絞る） |
| `_skipped_deploy` | `progress/command.read_current`・`store` | `progress/command.py` を読んだ | 使う |
| `close_release` | `domains/records/cli._close_check`・`record_read.read_release`・`acceptance_release.prune`・`flow.close_rules`・`close_decide.conditions_of`・`GitHub.nodes`・`GitHub.rest`・`Publisher.comment` | `domains/records/cli.py`・`adapters/github.py`・`adapters/publish.py` を読んだ | 使う。判定は `close_verdict` を呼び、`_close_check` は呼ばない（保存したファイルを読む形のため）。整理は `prune` と同じ関数（`_prune_one`）を、順 4 で読んだ記録のコピーで呼ぶ |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `build_map` | 要求仕様書 2 本で `tables` に両方 | 行の無い PR は `run` が空 | マージしていない PR で止まる |
| `write` | 打ち直しで `acceptance` が変わらず増えない | 2 つ目の届け先・`--correct-targets` で作り直さない | — |
| `corrected_targets` | 要求仕様書か確認の表が変われば版が変わる | 別の PR の `base`・`merged_at` だけの訂正は版を引き継ぐ | A→B→A で前の版に戻らない |
| `read_release` | `Refs` だけの PR の Issue が `outputs.issues`・`to_test` に入る | 0 件の一覧も空の配列で返す | 対応の記録が無い値は `outputs.issues` のキーを持たない。PR の本文と権限を読まない |
| `records_verdict` | 確認対象の版が同じ合格で閉じる | バージョン番号を持たないリポジトリで合格の後の保留でも合格 | 訂正で足した Issue、版の無い行、`--require` で行が無い Issue は閉じない |
| `_skipped_deploy` | デプロイを飛ばした実行の記録を受ける | — | ID なし・デプロイを通った実行・前の実行の記録で止まる |
| `close_verdict` | 本番の記録・テストの合格・最終工程の記録がそろえば `CLOSE` | 設定で外した条件は求めない。`デプロイ` はテストの記録が無くても閉じる | リリース記録が無い、「振り返り:」の行が無いか古い、不合格がある場合は閉じない |
| `after_close` | 版が同じで閉じてよいなら `keep`。版が変わっても新しい版で閉じてよければ `keep` | 対象から外れた Issue は `reopen` | `closed_at` が違うか開いていれば `report` |
| `close_release` | 閉じて照合し、表を整理して、状態ファイルが消える | 読み直しの失敗の後の打ち直しで開き直る。整理の前に中断した後の打ち直しで、閉じている Issue の表が整理される。記録の読み出し 2 回・状態の照会 2 回（呼び出しを差し替えて数える） | 状態ファイルに無い閉じた Issue は開き直さない。対応の記録が無ければ何も書かず、記録の読み出しは 1 回 |

## 実行の印と要求の工程の出力（`progress/command.py`・`progress/requirements_stage.py`）

満たす決定: 決定 2・決定 3・決定 4、受け入れ条件 1・7、不変条件 I5

ここでの移送は、メインディレクトリの要求仕様書を worktree へ移すことである。順 1〜6 は設計文書の「要求仕様書の移送」にある。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `run_mark` | `progress/requirements_stage.py` | 新設 | `(machine_id: str) -> str` | ステートマシンの ID の SHA-256 の先頭 12 桁 |
| `context_run` | 同上 | 新設 | `(context: Mapping[str, Any], machine_id: str) -> str` | 文脈の `run` を返し、無い旧いステートマシンは ID から導く |
| `recorded_docs` | 同上 | 新設 | `(root: Path, issues: Sequence[int], paths: Sequence[str]) -> list[dict[str, str]]` | 要求の工程の出力のパスを検査を通して読み、パスと SHA-256 の組にする |
| `creation_finish` | 同上 | 新設（`command._requirements` を移す） | `(snap: stages.Snapshot, path: Optional[str], root: Path, issues: Sequence[int]) -> tuple[list[tuple[str, str]], dict[str, Any]]` | `--requirements` の出力を確かめ、終了のマーカーの一覧と文脈へ足す `requirements_docs` を返す |
| `finish_requirements` | 同上 | 新設 | `(st: store.Store, machine_id: str, output: Mapping[str, Any], root: Path, reason: Optional[str]) -> list[int]` | 要求の工程への `finish` で、完了済みなら出力の記録だけを足し、未完了なら今のとおり終了も書く |
| `MoveStep` | 同上 | 新設（型） | `Enum`（`stashed`・`written`・`done`） | 移送の進捗の事象 `requirements_move` の値 |
| `resume_step` | 同上 | 新設 | `(last: Optional[MoveStep], stashed: bool, linked: bool) -> int` | 最後の進捗、退避ファイルの有無、元の名前が退避ファイルと同じ inode を指すか（`linked`）から、再開する順を返す |
| `move_one` | 同上 | 新設 | `(st: store.Store, machine_id: str, main_dir: Path, worktree: Path, entry: Mapping[str, str]) -> dict[str, Any]` | 1 つのパスを設計文書の順 1〜6 で移す |
| `move_requirements` | 同上 | 新設 | `(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]` | `ndf progress move-requirements` のエントリポイント |
| `add_parser` | 同上 | 新設 | `(sub: Any) -> None` | `move-requirements` の引数を登録する |
| `_prepare_new` | `progress/command.py` | 変更 | 今のまま | 文脈に `run`（`--carry` ならコピー元の値、無ければ導いた値）と `requirements_docs` を入れる |
| `_cmd_finish` | `progress/command.py` | 変更 | 今のまま | 工程が要求の工程なら `finish_requirements` へ渡す |
| `parser` | `progress/command.py` | 変更 | 今のまま | `requirements_stage.add_parser` を呼ぶ |
| `_snapshot_v2` | `contracts/internal.py` | 新設 | `(row: Row) -> Row` | 保存した定義の要求の工程の `outputs` に `requirements_docs`、作業場所の用意の任意の入力に同じキーを足す |

- `finish_requirements`
  - 事後条件（要求の工程が未完了）: 今の `finish` と同じく終了のマーカー（`manual_finished`）を書き、出力を文脈へ書く。工程は完了になる
  - 事後条件（要求の工程が完了済み）: 状態は変えない。`outputs_recorded` の事象が 1 件増える
  - どちらの場合も、対象の Issue のどれかを名前に含むパスが無ければ何も書かない
- `move_one`
  - 事前条件: 実行とパスの組ごとのファイルロック（Git の共通ディレクトリの下）を、進捗を読む前に取る
  - 退避の完了: `os.link` と `unlink` の間で中断すると、元の名前と退避先が同じ inode を指して両方残る。このときは、元の名前を固定の名前 `<退避ファイル>.unlinking`（退避先と同じディレクトリ）へ `os.rename` で移し、移した先と退避ファイルの inode が同じことを確かめてから消す。違えば別のファイルに差し替わっているので、`os.link`（既存を上書きしない）で元の名前へ戻してから `.unlinking` を消し、`precondition` で止まる
  - `.unlinking` の残り: `move_one` は、`resume_step` を呼ぶ前に毎回この名前を調べる。あれば前の回が移した直後に中断しているので、上と同じ確かめをする。退避ファイルと同じ inode なら消す。違えば元の名前へ戻して `.unlinking` を消し、`precondition` で止まる。元の名前が埋まっていて戻せなければ、`.unlinking` を残してその場所を知らせる。名前は実行の ID とファイル名から決まるので、打ち直しで必ず見つかる
  - 事後条件: 照合したバイト列そのものを作業場所へ書く。元の名前の既存ファイルを上書きしない
  - 副作用: 退避先 `issues/.ndf-move-<ID の先頭 12 桁>-<ファイル名>`（所有者だけのディレクトリ）への `os.link` と `unlink`（既にあれば失敗する方法）。事象 `requirements_move` の追記
  - 順 6（元が追跡ファイルのとき）: 復元元のコミットを `git.commit_or_none("HEAD")` で 1 回だけ解決し、`Git.blobs` で `<そのコミット>:<パス>` のバイト列を読み、`worker_paths.create_file` で作る。`git checkout -- <パス>` は使わない。インデックスから復元し、そこにあるファイルを上書きするためである。名前が埋まっていて内容が復元するバイト列と同じなら、戻し終えたものとして完了にする（作った直後の中断からの再開）。違えば何も変えずに `precondition` で止まる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `recorded_docs` | 対象の Issue のパスが無い・読めない | 出力の漏れ | パスの番号と `issues` の比較・検査 | 止める（完了にしない） | `invalid`（工程の出力の規約の誤りと同じ） | 直して打ち直せる |
| `move_one` | SHA-256 が記録と違う | 工程の完了の後に書き直された | 順 2 | 止める（元の名前へ戻す） | `precondition`。退避先に残したならその場所を書く | 内容を読み、`finish` で記録し直す |
| `move_one` | 作業場所の同じパスに未コミットの別の内容 | 手での編集 | 順 4 | 止める（戻す） | `precondition` | 片付けて打ち直す |
| `move_one` | 元の名前に別のファイルができた | 並行する試行 | 順 3・5・6 の戻し | 止める（上書きしない） | `precondition`。場所を書く | 人が見て打ち直す |
| `move_requirements` | ロックを取れない | 二重の起動 | `filelock.Timeout` | 止める | `store_in_use` | 打ち直せる |
| `_snapshot_v2` | 保存した定義に要求の工程が無い | モードが違う | 工程の名前の照合 | 既定値で続ける（そのまま返す） | — | — |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `resume_step` | 進捗なしか「退避した」か「書いた」・`linked` | 元の名前を外して退避を済ませてから、下の行（退避あり）に従う |
| `resume_step` | 進捗なし・退避なし | 順 1 |
| `resume_step` | 進捗なしか「退避した」・退避あり | 順 2 |
| `resume_step` | 「退避した」・退避なし | 順 1 |
| `resume_step` | 「書いた」・退避あり | 順 5 |
| `resume_step` | 「書いた」・退避なし | 順 6 |
| `resume_step` | 「完了」 | 何もしない |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ移送の二重の起動 | 退避ファイル・メインディレクトリのパス | 実行とパスの組ごとのファイルロック | `resume_step` の表で再開する |
| 移送と同じ Issue の別の試行 | メインディレクトリの同じパス | 退避で名前を外し、照合したバイト列だけを書く | 照合の後の書き込みは移送先に混ざらない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `run_mark` | `core/requirements_decide.DIGEST_CHARS`・`hashlib` | この文書 | 使う |
| `context_run` | `command.machine_context` | `progress/command.py` を読んだ | 使う（文脈を読んでから `run` を引く） |
| `resume_step` | 無し | `grep -rn resume src/ndf/progress` | 新設（`units_invoke.resume_of` は単位の回の再開で、移送の進捗を読まない） |
| `move_requirements`・`add_parser` | `command.with_store`・`add_cwd`・`add_stage_target` | `progress/command.py` を読んだ | 使う（ストアの開き方と引数の形を同じにする） |
| `recorded_docs` | `requirements_docs.worktree_candidates` の検査 | この文書 | 使う |
| `creation_finish`・`finish_requirements` | `command._requirements`・`_cmd_finish`・`stages.outputs_draft`・`_append` | `progress/command.py` を読んだ | 使う（移して要求の工程の分岐を足す） |
| `move_one` | `contracts/worker_paths.read_bytes`・`write_file`・`check_owned` | `contracts/worker_paths.py` を読んだ | 使う。退避は `os.link`（既にあれば `FileExistsError`）で、`os.rename` は既存を上書きするので使わない。順 6 の復元は新設の `create_file` と `Git.blobs` を使う（`write_file` は既存を置き換える） |
| `move_one` | `worktree` の手順 3（メインディレクトリの変更を移す） | `plugins/ndf/skills/worktree/SKILL.md` を読んだ | 使えない（照合が無い） |
| `_snapshot_v2` | `_snapshot_v1`・`stage_value_key` | `contracts/internal.py` を読んだ | 使う（同じ形で足す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `context_run` | 別のステートマシンで値が変わる | `--carry` でコピー元と同じ。旧いステートマシンは再開しても `--carry` しても ID から導いた値 | — |
| `finish_requirements` | 未完了の工程に打つと完了になり出力が残る。完了した工程に打つと出力だけが残り状態は変わらない | 更新より前の定義を保存した実行でも残る（`_snapshot_v2`） | パスの欠けた出力を断る |
| `move_one` | 照合したバイト列を書く | 退避のリンクを作った直後・元の名前を `.unlinking` へ移した直後・退避の直後・書いた直後・順 6 でファイルを作った直後の中断から終わる。リンクの直後の中断では、元の名前を外してから順 6 でコミット済みの内容へ戻る | 照合の後の書き直しで移さない。`.unlinking` が別のファイルなら元の名前へ戻して止まる。二重の起動で退避ファイルを上書きしない。順 6 の直前に元の名前へ書かれた内容を上書きしない |

## Git アダプターと保存先の検査（`adapters/git.py`・`contracts/worker_paths.py`）

満たす決定: 決定 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Git.tree_entries` | `adapters/git.py` | 新設 | `(self, commit: str, prefix: str) -> list[tuple[str, str, str, str]]` | `git ls-tree -r -z` 1 回で（モード・種類・id・パス）を返す |
| `Git.name_status_log` | 同上 | 新設 | `(self, include: Sequence[str], exclude: Sequence[str]) -> list[tuple[str, tuple[str, ...], list[tuple[str, str, str]]]]` | `git log` 1 回で、範囲のすべてのコミットの（コミット・親・（状態・パス・変更前の blob）の並び）を返す。引数と形式は下の箇条書き |
| `Git.missing_objects` | 同上 | 新設 | `(self, specs: Sequence[str]) -> frozenset[str]` | `git cat-file --batch-check` 1 回で、`missing` と返る指定の集合を返す |
| `Git.fetch_commits` | 同上 | 新設 | `(self, remote: str, shas: Sequence[str]) -> Completed` | SHA を指定した 1 回の `git fetch` |
| `read_bytes` | `contracts/worker_paths.py` | 新設 | `(wd: WorkDir, name: str, *, owned: bool = False) -> Optional[bytes]` | `read_file` と同じ検査でバイト列を返す（`read_file` はこれを呼んで復号する） |
| `has_acl` | 同上 | 新設 | `(fd: int) -> bool` | 開いた記述子に拡張 ACL の項目があるか（Linux は `_has_acl`、macOS は `_has_acl_darwin`）。確かめられなければ真 |
| `create_file` | 同上 | 新設 | `(wd: WorkDir, name: str, data: bytes) -> bool` | 一時ディレクトリに書いてから `os.link` で公開する。名前が埋まっていれば何も変えずに偽を返す |
| `write_file`・`staged_write`・`_fill_data` | 同上 | 変更 | 今の引数に `owner_only: bool = False` を足す | 真なら公開の前に新しい inode を 0600 にし、置き換える前のファイルの権限を引き継がない |

- `name_status_log`
  - コマンド: `git log --raw --no-abbrev --no-renames --diff-merges=remerge -z --format=<コミットと親> --end-of-options <include>... ^<exclude>...`
  - 事前条件: `include` と `exclude` の要素は、すべて完全な SHA（40 桁か 64 桁の 16 進）。違えば起動せずに `ValueError`。ref の名前や範囲の記法は、呼び出し側が SHA に解決してから渡す。複数の head も 1 回の起動で渡す
  - `--diff-merges=remerge` は、マージを git が自動でやり直した結果と、実際のマージコミットとの差分を出す。付けないと、マージコミットは SHA と親だけが出て、競合の解消で足したファイルと消したファイルを拾えない。親ごとの差分（`separate`）は、どの親との差分かが書式に出ず、片側の変更が伝わっただけのエントリと区別できないので使わない
  - git 2.36 より古いとこの指定が無く、`GitError` になる。親が 3 つ以上のマージコミットには差分が出ない
  - `--raw --no-abbrev` を付けて、消えた内容を変更前の blob の id で受け取る

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Git.*` | git の失敗 | 履歴が無い・権限 | 終了コード | 呼び出し元へ返す（`GitError`） | 呼び出し元が `precondition` | 打ち直せる |
| `read_bytes` | リンク・読めない | 共有の環境 | `_open_file` | 呼び出し元へ返す（`EntryUnsafe`・`EntryUnreadable`） | 呼び出し元が `precondition` | 権限を直して打ち直せる |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `Git.tree_entries` | `domains/project/check.py` の `ls-tree -r HEAD` | `grep -rn ls-tree src/ndf` | 使えない（各所で直に打っている）。アダプターに 1 つ置く |
| `Git.name_status_log` | `Git.log_of` | `adapters/git.py` を読んだ | 使えない（リビジョンを 1 つしか受けず、`--end-of-options` の後に置く）。`Git.run_bytes` を直に使う |
| `Git.missing_objects` | `Git.blobs`（`cat-file --batch`） | 同上 | 使えない（内容まで読む）。同じ `run_bytes` を使う |
| `Git.fetch_commits` | `Git.fetch` | 同上 | 使う（refspec に SHA を渡す） |
| `read_bytes`・`has_acl` | `_open_file`・`_has_acl`・`_has_acl_darwin` | `contracts/worker_paths.py` を読んだ | 使う（公開する名前を足す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Git.missing_objects` | 無いパスだけを返す | 親子のパスを同時に渡しても両方を判定する | — |
| `write_file(owner_only=True)` | 0600 で置く | 同名の 0644 があっても 0600 | 書き先がリンクで書かない |

## ドキュメント再構成（`domains/docrebuild/drive.py`）

満たす決定: 決定 1、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `requirements_of` | `domains/docrebuild/drive.py` | 変更 | 今のまま | 作業場所の `issues/` のうち、`issue_docs.requirements_issues` が設計書の番号と重なる通常ファイルを組にする |

変える前は `issues/issue-<番号>-requirements.md` だけを番号ごとに探したので、`issue-7-8-requirements.md` が組にならなかった。変えた後は番号の重なりで探し、定数 `REQUIREMENTS_FILE` を消す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `requirements_of` | `issue-7-8-design.md` と `issue-7-8-requirements.md` が組になる | 番号の一部（`78`）は当たらない | リンクは組にしない |

## worker の起動指示（`contracts/worker/`・`core/worker.py`）

満たす決定: 決定 1・決定 4、受け入れ条件 1・7

関数は変えない。起動指示の文だけを変える。

| 保存先 | 変える前 | 変えた後 |
| --- | --- | --- |
| `core/worker.py` の `ACCEPTANCE_RULES` | `ndf loop acceptance record --issue <番号>` で区分を記録する | 単位の指示の `--run` と条件一覧の版を添えて `record` を打つ。条件の文は要求仕様書（`ndf records requirements show`）から読む |
| `contracts/worker/kinds.json` | 要求の工程の worker が Issue の本文へ要求を書く | 要求仕様書のファイルへ書き、Issue の本文を変えない |
| `contracts/worker/document-rebuild.md` | 要求仕様のコピー | 要求仕様書 |

## 手順書と用語集

満たす決定: 決定 1・決定 5・決定 6・決定 7・決定 8・決定 9、受け入れ条件 1・3・4・5・6

関数を持たないので、変える中身だけを並べる。

| 保存先 | 変える中身 |
| --- | --- |
| `skills/requirements-design/SKILL.md`・`references/spec-template.md`・`references/document-roles.md` | 本文へ要求を書く手順と一致の確かめを消し、要求仕様書に `元の Issue:` の行を書く。不具合は「今の姿」に根本原因を書く。設計文書の「進行中の作業」の手順を置く |
| `skills/design/SKILL.md`・`references/design-template.md`・`references/deliverables.md` | 手順 7 の「仕様のコピーが課題の本文と一致しているか」を消し、要求仕様書を読む |
| `skills/development-workflow/SKILL.md`・`references/approval-request.md`・`references/agent-layers.md`・`references/glossary.md` | 出所の表を要求仕様書にし、「種類と影響」の行を足す。手で組むプランの例に `--run`・`--mode` を足す |
| `skills/progress-tracking/SKILL.md` | 受け入れ条件のマーカーを付ける手順を消す。デプロイを飛ばした実行でなければ、先に `records` を打たずに `ndf records close-release` を打つ手順にする（`close-check`・`gh issue close`・照合し直し・`prune` を手順書の AI が組み立てない）。`not_applicable` のときは、その出力で今の手順へ進む |
| `skills/quality-gates/SKILL.md`・`references/design-match.md`・`references/definition-of-done.md`・`skills/fix/SKILL.md`・`skills/pr-review/SKILL.md`・`skills/pr/SKILL.md` | 要求への指摘の直しと実装で変えた条件の直しを要求仕様書の差分にする。PR の本文を要求仕様書へのリンクと `Refs` にする |
| `skills/release-verification/SKILL.md`・`skills/release/SKILL.md`・`references/release-steps.md` | `acceptance show --release` を 1 回打つ手順、マージの前の証跡を使う条件の確かめ（`git cat-file --batch-check` と `git --literal-pathspecs diff --quiet`）、`record-acceptance`、旧方式の Issue（設計文書の「マージ済みで残るのがリリース後テストだけの場合」）の手順 |
| `skills/worktree/SKILL.md`・`skills/document-rebuild/SKILL.md`・`skills/implementation-plan/SKILL.md` | `move-requirements` を打つ手順、まとめた要求仕様書を組にすること、`stamp` を打つ手順と「単位の外」の行 |
| `docs/glossary/glossary.json`・`docs/glossary.md`・`docs/glossary/ndf-design.md`・`docs/glossary/ndf-workflow.md` | 設計文書の「用語」の表のとおり足す・意味を変える・外す（`ndf records glossary render` で作り直す） |
| `docs/contracts/commands.md`・`docs/contracts/files.md`・`contracts/domains/ndf-files.json`・`contracts/domains/locations.json` | 新しいサブコマンドと、要求仕様書・退避先のファイル・`close-release` の状態ファイル。保存先 `ndf-closing`（メインディレクトリの `.ndf/closing`、追跡しない）を足し、状態ファイルは `close-release` が終えたときに消す |
