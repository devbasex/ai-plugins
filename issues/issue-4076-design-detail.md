# #4076 の詳細設計: 工程と MVV（写し・指摘の基準 5・根拠の行の確かめ・記録）

設計文書 [issue-4076-design.md](./issue-4076-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。#4090 #4099 の詳細設計の置き換えと実装計画の保存先の変更を反映した最終形で、実装はこの文書を読む。前提はスプリントブランチ `sprint/p12-lcrfast-r1` が main へ入った後の形である。このブランチは #4014 の `impact`・`effective_severity`、#4030 の `fixer_ci_only`、#4013 の修正担当の会話を持つ。

- 「直しの決定 N」は直しの設計 [issue-4090-4099-design.md](./issue-4090-4099-design.md) の決定 N を、「決定 N」は #4076 の設計文書の決定 N を指す。採らなかった案と PR #4121 の指摘の経緯は直しの設計にある
- 節の題や変えていない文の「写し」は、この文書の「スナップショット」と同じものである

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/adapters/git.py                     … Git.run_bytes（元のバイト列を返す）
├── src/ndf/core/converge/worktree.py           … 同期の fetch でベースブランチも取る
├── contracts/data/review-criteria.json         … 基準 5 の文と mvv の指示
├── src/ndf/domains/records/
│   ├── mvv.py                                   … current（スナップショット）・review の記録・共通原則の本文・本文の行の分類
│   ├── mvv_gate.py                              … principles を mvv.py から読む
│   ├── mvv_decide.py                            … 項目の一覧・本文のパス・根拠の行の判定・兆候の種類
│   ├── preflight.py                             … mvv_body が body_path を呼ぶ
│   └── cli.py                                   … ndf records mvv current
├── src/ndf/domains/crossreview/
│   ├── criteria_decide.py                       … 基準 5 を組む
│   ├── drive.py                                 … スナップショットを得る・確かめと指示の配線
│   ├── changes.py *                             … 変更の読み取り（drive.py から移す）・全差分と本文・根拠の行の確かめ
│   ├── settings.py *                            … レビューの設定の読み取り（drive.py から移す）
│   ├── journal.py                               … mvv の分類・NDF の指摘・レッドライン
│   ├── prior_decide.py                          … 記録のコメントから分類を引き継ぐ
│   ├── finish_decide.py                         … 欠け mvv_unresolved
│   ├── report_decide.py                         … 結果の outputs.mvv・記録のコメントの一覧
│   ├── scope_decide.py                          … 再利用の条件と確認済みのセクションに MVV を足す
│   └── cli.py                                   … Basis に MVV を渡す
└── tests/
    ├── adapters/test_git.py
    ├── domains/records/test_mvv_current.py *
    ├── domains/records/test_mvv_citations.py *
    ├── domains/records/test_mvv_record_review.py *
    ├── domains/records/test_{cli,mvv,preflight}.py
    ├── domains/crossreview/test_crossreview_mvv.py *        … 起動を通す MVV のテスト
    ├── domains/crossreview/test_crossreview_citations.py *  … 根拠の行の確かめのテスト
    ├── domains/crossreview/test_crossreview_{criteria,journal,run,finish,scope,prior,report,push_sync,fixer_sessions}.py
    └── route/test_flows.py
```

依存の向き: `crossreview` が `records.mvv`（スナップショットと記録と、本文の行の分類）と `records.mvv_decide`（純粋な判定）を読む。`records` は `crossreview` を読まない。`mvv_decide` は git もファイルも `adapters` も読まない純粋な関数だけを持ち、`mvv.py` が git・ファイル・`adapters.markdown` を読んで `mvv_decide` へ渡す。`crossreview` の中では `drive.py` と `cli.py` が `changes.py`・`settings.py` を読み、この 2 つは `drive.py` を読まない（`_Run` は引数で受ける）。循環は無い。

保存先の 3 つの決め方（実装計画 [issue-4076-plan.md](./issue-4076-plan.md) の「修正対象」）:

- `drive.py` は 998 行で、構造の規則（`checks/rules.json` の `structure.max_lines` 1,000 行）を足すだけで超える。今の変更の読み取り（`_Changed`・`_regular_blobs`・`_local_text`・`_committed_texts`・`_changed`・`_sections`・`changed_sections`）を `changes.py` へ、設定の読み取り（`ReviewSettings`・`_settings_path`・`_inside`・`read_settings`・`_blob_id`・`_proposed_differs`・`outside_checkout` と、`outside_checkout` が使う `RunConfig`・`DriveStopped`・`DEFAULT_MAX_ROUNDS`）を `settings.py` へ、中身を変えずに先に移す（モジュールをまたいで読む名前は先頭の下線を外す。`Changed`・`changed`・`sections`・`settings_path`・`blob_id`・`proposed_differs`・`REGULAR_MODES`。新設する `Target`・`review_diff` も同じ）。全差分と本文の読み取りと根拠の行の確かめは `changes.py` に足す
- `fenced_lines`・`decision_lines` は `adapters.markdown` を呼ぶので `mvv.py` に置く。名前が `_decide` で終わるモジュールは `ndf.adapters` を読めない（`structure.decide_forbidden_imports`）。本文を読まない `parse_citation`・`citation_errors` は `mvv_decide.py` に置く
- cross-review の起動を通すテストは `test_crossreview_mvv.py`・`test_crossreview_citations.py` に置く。`test_crossreview_run.py`（872 行）へ足すと、同じ 1,000 行の規則を超える

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | `mvv_material_decide.redlines` | `MvvError`（`mvv.py` から移す。`mvv.MvvError` は同じクラスを指す。`mvv` を読めない `body_path` が送出するため）・`Citation`（新設）、モジュール | `items_of`・`approved_form`・`body_path`・`parse_citation`・`citation_errors`（新設）・`count_signals`（変更） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の設定・本文・記録の読み書き | `git`・`adapters.markdown`・`mvv_decide`・`contracts` | `Snapshot`（新設）、モジュール | `current`・`decl_from`・`_write_verified`・`fenced_lines`・`decision_lines`・`record_review`・`record_key`（新設）・`principles`（`mvv_gate` から移す） |
| `src/ndf/domains/records/preflight.py` | 変更 | 手順 0 の確かめ | `mvv_decide` | モジュール | `mvv_body`（`body_path` を呼ぶ。正しいパスなら返す値は同じ）・`mvv_state`・`mvv_checked`（変更。不正なパスを既存の停止結果へ変える） |
| `src/ndf/adapters/git.py` | 変更 | Git の起動 | — | `Git`（変更） | `run_bytes`（新設。標準出力をバイト列のまま返す） |
| `src/ndf/core/converge/worktree.py` | 変更 | PR の worktree の同期 | `git` | モジュール | `fetch_head`・`sync_worktree`（変更。`base_branch` があれば同じ 1 回の fetch でベースも取る） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの直前の判定 | `mvv` | モジュール | `principles` を消し `mvv.principles` を呼ぶ |
| `src/ndf/domains/crossreview/scope_decide.py` | 変更 | 差分の範囲と再利用の条件 | — | `Basis`（変更） | `read_by_all_head`・`reviewed_of`・`state_scope`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` のコマンド | `records.mvv`・`changes`・`settings` | モジュール | `Basis` を組む箇所に MVV の識別子を渡す。`cmd_scope` はベースのコミットと、承認済みのときの共通原則の sha256 を求めて識別子を得る（ネットワークに出ない） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` のコマンド | `mvv`・`git` | モジュール | `_mvv_current`（新設） |
| `contracts/data/review-criteria.json` | 変更 | 指摘の基準の文 | — | — | キー `mvv_criterion`・`mvv`（新設） |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 基準と指示の組み立て | `records.mvv.Snapshot` | モジュール | `criteria_lines`・`reviewer_block`・`fixer_block`（変更）・`mvv_item`（新設） |
| `src/ndf/domains/crossreview/changes.py` | 新設 | 差分と本文の読み取りと、根拠の行の確かめ | `git`・`records.mvv`・`records.mvv_decide` | `Changed`（`drive` から移す）・`Target`・`DiffFile`・`Hunk`（新設） | `_regular_blobs`・`_local_text`・`changed`・`sections`・`changed_sections`（`drive` から移す）・`_committed_texts`（移して引数を足す）・`review_diff`・`_diff_files`・`_base_lines`・`_markdown_rows`・`_citations`・`_final_target`・`_final_citations`（新設） |
| `src/ndf/domains/crossreview/settings.py` | 新設 | レビューの設定の読み取り | `git` | `ReviewSettings`・`DriveStopped`（`drive` から移す）・`RunConfig`（移して欄を足す） | `settings_path`・`_inside`・`read_settings`・`blob_id`・`proposed_differs`・`outside_checkout`（`drive` から移す） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv`・`changes`・`settings`・`criteria_decide`・`journal` | `_Run`・`_Prepared`（変更。欄を足す） | `_start`・`_round`・`_review_prompt`・`_fix_prompt`・`_summary`・`_prepare_round`・`_merge_base`・`_reusable`・`_settle_resolved`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal`、モジュール | `enter`・`carry`・`apply_fix`・`skipped_texts`（変更）・`ingest_citations`・`handable`・`restate`・`citation_key`（新設） |
| `src/ndf/domains/crossreview/prior_decide.py` | 変更 | 前の実行から引き継ぐ指摘 | `report_decide` | `Carried`（変更。欄を足す） | `carried`・`recorded`（変更） |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | `criteria_decide` | `Finding`（変更） | `_finding_gaps`・`finish`（変更） |
| `src/ndf/domains/crossreview/report_decide.py` | 変更 | 記録のコメントと結果 JSON | — | `Once`（新設）、モジュール | `open_block`（変更）・`mvv_outputs`・`once`（新設） |

## MVV の写し（`current`）

満たす決定: 決定 1・2、直しの決定 1・9・10・11・13・15、受け入れ条件 1・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Snapshot` | `records/mvv.py` | 新設 | `NamedTuple(state: str, version: Optional[int], sha256: Optional[str], path: Optional[Path], items: tuple[str, ...], blobs: Mapping[str, str], commit: str, principles_sha256: str, principles_path: Optional[Path])` | MVV を読んだ結果。`blobs` は読んだ設定と本文のパスから Git の blob の id（`git show` が返した元のバイト列から Python で `sha1("blob <バイト長>\0" + バイト列)` を計算する。Git を打たない。文字列へ変えてから再エンコードした値を使わない）。`absent` は空。`commit` は手順 1 で解いたコミット（状態によらず置く）。`principles_*` は共通原則の sha256 とスナップショット（承認済みのときだけ置き、それ以外は `""` と None） |
| `body_path` | `records/mvv_decide.py` | 新設 | `body_path(decl: Mapping[str, Any]) -> str` | 設定の最上位の `body`（文字列でなければ表 `ndf-files` の `mvv-body` の既定）を `.ndf/` からの相対で解き、リポジトリの根からの POSIX のパスを返す。絶対パス・`..` を含む値・空は `MvvError` |
| `current` | `records/mvv.py` | 新設 | `current(root: Path, ref: str, allow_mismatch: bool = False) -> Snapshot` | 参照の MVV の設定と本文を読み、承認済みならスナップショットを書いて返す。承認の記録と合わない本文は、`allow_mismatch` が偽なら `MvvError` |
| `_write_verified` | `records/mvv.py` | 新設 | `_write_verified(path: Path, data: bytes, sha256: str) -> Path` | `path` が無いか、通常のファイルでないか、所有者のほかに権限を与えているか、中身の SHA-256 が `sha256` と違えば、`data` を同じディレクトリの一時ファイル（0600）へ書いて `os.replace` で置き換える。中身も権限も同じなら書かない（本文は非公開のリポジトリのものを含む） |
| `approved_form` | `records/mvv_decide.py` | 新設 | `approved_form(decl: Optional[Mapping[str, Any]], body: bytes) -> bytes` | 本文を、元の形・LF・CRLF のうち sha256 が承認の記録と合う形にして返す（どれも合わなければ元のまま）。`preflight.sha256_of` と `current` の両方がこれを通すので、`core.autocrlf`・`eol=crlf` で作業場所と Git の blob の改行だけが違っても判定がそろう |
| `items_of` | `records/mvv_decide.py` | 新設 | `items_of(body: str, principles: str) -> tuple[str, ...]` | 本文と共通原則から実在する項目の一覧を作る |
| `_mvv_current` | `records/cli.py` | 新設 | `_mvv_current(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `ndf records mvv current --base`（`--allow-mismatch` を受ける）の結果 JSON。`git.Git(root).fetch_branch_or_raise(<リモート>, <ブランチ>)` を打ってから `current(root, refs/remotes/<リモート>/<ブランチ>, allow_mismatch=<引数>)` を呼ぶ（同じ名前の手元のブランチを読まないよう、参照の全体を渡す） |

- `current`
  - 事前条件: `ref` は取得済みの参照（`<リモート>/<ブランチ>`）かコミット
  - 事後条件: 状態が `approved` なら `path` のファイルの中身は `ref` の本文と同じで、`principles_path` のファイルの中身はプラグインの共通原則の本文と同じである。本文の sha256 は `ref` の `.ndf/mvv.json` の最後のバージョンの `sha256` と一致する。`approved` でなければ `path`・`items`・`principles_path` は空で、ファイルを書かない
  - 手順
    1. `git rev-parse --verify <ref>^{commit}` でコミットへ解く。失敗は `MvvError`（`precondition`）
    2. `git ls-tree -z <コミット> -- .ndf/mvv.json` を打つ。成功して出力が空のとき（ツリーに設定のエントリが無いと確かめられたとき）だけ `absent`（`commit` は置き、共通原則は読まない）。`ls-tree` の失敗、エントリが通常のファイルの blob でない（`tree`・`commit`・シンボリックリンク）は `MvvError`（`precondition`）
    3. 設定を `ls-tree` で得た blob の名前で `run_bytes` で読み、`decl_from` で検査する。設定の blob の読み取りの失敗（blob の欠損を含む）・JSON でない・旧い形は `MvvError`（`precondition`）。読めなかったことを `absent` に落とさない。`mvv_decide.status(decl, None)` が `unapproved`（承認済みのハッシュが無い）なら、本文のパスを解かず本文も読まずに `unapproved` を返す（設定だけを先に置いたプロジェクトを止めない。`blobs` は設定だけ）
    4. 承認済みのハッシュがあるときだけ、`body_path(decl)` で本文のパスを決め、本文を必須で読む（読めなければ `MvvError`）。本文の sha256 を `mvv_decide.status` に渡して状態を決める。`mismatch` は、`allow_mismatch` が偽なら `MvvError`（「ベースの MVV の本文が承認の記録と合わない: <本文のパス>」）。真なら状態 `mismatch`・項目なし・スナップショットを書かずに返す（基準 5 は外れる）
    5. 承認済みなら、本文のスナップショットと共通原則のスナップショット（`mvv.principles` のバイト列から sha256 を求め、`principles-<先頭 12 文字>.md`）を `_write_verified` で書き、`items_of` で項目の一覧を作る。`blobs` の鍵は設定のパスと `body_path` のパス
  - バイト列の読み方: `Git.run` は `text=True` で起動し CRLF を LF に変えるので、設定と本文の `git show` には使わない。`adapters/git.py` に、同じ `NO_PROMPT` の環境で標準出力をバイト列のまま返す `Git.run_bytes(*args) -> bytes` を足し、`current` は設定と本文をこれで 1 回ずつ読む。同じバイト列から `blobs` の id を計算し、UTF-8 で復号して解析する（設定は `mvv.py` に足す `decl_from(raw: bytes, ref, root)` で `decl_at` と同じ検査をかける。`decl_at` は `run_bytes` の結果を `decl_from` へ渡す形にする）。本文の sha256 は、同じバイト列を `approved_form` に通してから計算する。解析と id の計算で別々に Git を打たない
  - 副作用: `git ls-tree` を 1 回（`ref` が完全な SHA でなければ前に `git rev-parse` を 1 回）と、設定と既定のパスの本文をまとめて読む `git cat-file --batch`（`Git.blobs`）を 1 回（`absent` では 0 回。本文が既定のパスに無いときだけ、もう 1 回読む）。cross-review は同期で得たベースのコミット（`SyncResult.base`。PR のブランチの先頭と同じ 1 回の `rev-parse` で読む）を渡すので、参照を解決し直さない。スナップショットは、シンボリックリンクをたどった実体のパスに置き、レビューする worktree の中（引数 `outside`）と、ほかの利用者が根までのどこかの項目を差し替えられる保存先なら、書かずに `MvvError` にする。スナップショットのファイルを 2 つまで読み、違えば書く。CLI の `_mvv_current` だけは、読む前に `fetch_branch_or_raise` で `+refs/heads/<ブランチ>:refs/remotes/<リモート>/<ブランチ>` を 1 回取得する（`git fetch <リモート> <ブランチ>` は、`--single-branch` で別のブランチを clone した作業場所でリモート追跡参照を更新しない）。cross-review は下の同期でベースブランチも取得した参照を渡すので打たない
- cross-review の `run` でのベースブランチの取得（`core/converge/worktree.py`）: 今の `fetch_head` は PR の head ブランチだけを取得するので、手元のベースの参照が古いと、ベースで新しい MVV が承認された後も `current` が旧版を読み、同じ head の収束を再利用してしまう。これを防ぐため、`fetch_head(g, head_branch, remote=None, base_branch: Optional[str] = None)` と `sync_worktree(g, head_branch, remote=None, base_branch: Optional[str] = None)` に引数 `base_branch` を足す。`base_branch` があれば、既存の 1 回の `git fetch -- <リモート> +refs/heads/<head>:refs/remotes/<リモート>/<head>` に refspec `+refs/heads/<base_branch>:refs/remotes/<リモート>/<base_branch>` を並べて 1 回で取る（Git の起動もネットワークの往復も増やさない）。`base_branch` が `head_branch` と同じか None なら今と同じ 1 本だけを取る
  - 手順: `drive._start` は `remote = g.remote()` を 1 回求め、MVV を読むブランチを決める。`--base` がブランチ名ならその名前、先頭が `<g.remote() の値>/` と一致する形（`origin/main` など）ならその部分を外した名前である（`sprint/…` のようにスラッシュを含むブランチ名はそのまま使う）。コミットの SHA などブランチに解けない値なら、Pull Request のマージ先のブランチ（`baseRefName`。既に PR を読んでいる呼び出しがあればその応答から取る）を使い、読めなければ `MvvError` と同じ扱いで相手役を起動せずに止める。`worktree.sync_worktree(g, cfg.branch, remote, base_branch=<そのブランチ>)` を打ち、同期が成功した後に `snap = current(root, <そのブランチのリモート追跡参照>)` を呼び（この参照は直前の fetch で更新したリモート追跡参照で、取得し直さない。`allow_mismatch` を渡さない）、`cfg = dataclasses.replace(cfg, base_commit=<差分の起点>)` とする。差分の起点は、`--base` がブランチなら `snap.commit`、SHA などなら渡されたコミットで、MVV を読んだコミット（`snap.commit`）とは別の値として持つ。`RunConfig` に `base_commit: Optional[str] = None` を足す。以後の `changed(run.g, cfg.base_commit, …)`・`_merge_base`（`run.g.merge_base(cfg.base_commit, head)`）・差分（`f"{scope.base or merge_base}...HEAD"`）・`Basis` はこのコミットを使い、`cfg.base`（名前）は表示と取得にだけ使う。`crossreview/cli.py` の同期のコマンド（`worktree.sync_worktree(git.Git(Path(args.worktree)), args.branch)`）も `--base` を受けるときは同じく `base_branch` を渡す（後に打つ `review-scope` が読む参照を新しくする）
  - 取得の失敗: ベースの refspec も同じ `git fetch` に入るので、ベースを取れないときは同期ごと失敗し、今の同期の失敗と同じく `GitError` で `run` が止まる。古いベースの参照を読んで続けない
  - 他の呼び手（`crossrefactor` の `workspace`・`final_gate`・`drive`、`crossreview/push.py` の `_rebase_onto_origin`）は `base_branch` を渡さず、今と同じ
- `cmd_scope`（`review-scope`）: 手元の `<リモート>/<MVV を読むブランチ>`（`--base` が SHA のときは `run` が残したマージ先のブランチ）を `git rev-parse` で 1 回コミットに解き、そのコミットを `mvv.identity_at` に渡して識別子を得る。`identity_at` は、`current` からスナップショットの書き込みを除いた読み取り（`_read`）を `current` と共有するので、本文のパスの決め方・承認済みのハッシュの無い設定の扱い・共通原則の sha256 を求める条件は `current` と同じになる（識別子の規則は「再利用の条件に MVV を足す」）。ネットワークに出ずスナップショットを書かない（範囲を決める Git の操作のすべてで、部分クローンの手元に無いオブジェクトも取得しない。MVV か変わったセクションを読めなければ全差分にする）。マージ先のブランチは、ラウンドの記録の `mvv`（`{"id": <識別子>, "mvv_branch": <MVV を読んだブランチ>}`）の `mvv_branch` のうち最後のものを使い、項目を別に増やさない。`mvv_branch` の記録が無ければ、渡されたコミットの MVV で代用せずに全差分（`FULL`）を返す。差分の比較元も、`run` と同じく、`--base` がブランチなら手元の `<リモート>/<ベース>` のコミット、コミットならそのコミットにする
- `items_of`
  - 事後条件: `## Value` の下の行頭の `<番号>.` から `Value <番号>` を作る。`## 必ず人の承認が要る操作` の表と共通原則の表から `P<番号>`・`C<番号>` を作る（`mvv_material_decide.redlines` を使う）。`## Mission`・`## Vision` があれば `Mission`・`Vision` を作る。重複を除き、出た順。呼び手（`mvv.items`）がコードブロックの行を空にしてから渡す（例の見出しと番号を項目にしない）
- `preflight.mvv_state`・`mvv_checked`（手順 0 と承認ゲート）
  - `mvv_state` は、`mvv_decide.status(decl, None)` が `unapproved` なら本文のパスを解かずに返す（今と同じ）。それ以外で `mvv_body` が `MvvError` を送出したら `unreadable` にする。例外を外へ出さない
  - `mvv_checked` は、状態が `unreadable` で設定を読めているとき、`body_path(decl)` の `MvvError` の文（設定のパスと `body` の値）を `stopped`（`precondition`）の `summary` に使う。`MvvError` でなければ今の文
  - 承認ゲートの `mvv_gate.base_facts` は `mvv_state` が `approved` のときだけ `mvv_body` を呼ぶので、不正なパスは状態 `unreadable` の事実になり、機械の検査 1 が今どおり承認ゲートを省かない側へ倒す（`mvv_gate` に受ける箇所を足さない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `current` | 参照を解決できない | 取得していない・名前の誤り | `git rev-parse --verify` の失敗 | `MvvError` を呼び出し元へ返す（`absent` にしない） | `refused`（`precondition`）、`summary` に参照と理由 | 取得し直して打ち直す |
| `current` | 設定が無い | 解決したコミットのツリーに `.ndf/mvv.json` のエントリが無い | `git ls-tree` が成功して出力が空 | 状態 `absent` を返す | `ok`・`outputs.mvv.state: absent` | 無し |
| `current` | 設定の有無を確かめられない | `git ls-tree` の失敗・設定のエントリが通常のファイルでない・設定の blob の欠損 | `ls-tree` の終了コードと出力・`git show` の失敗 | `MvvError`（`absent` にしない） | `refused`（`precondition`）、`summary` に `.ndf/mvv.json` とコミット | 直して打ち直す |
| `current` | 承認済みのハッシュがあるのに本文を読めない | 設定の `body` のパスに本文が無い・`git show` の失敗 | `git show` の失敗 | `MvvError` | `refused`（`precondition`）、`summary` に `body_path` のパス | 直して打ち直す |
| `body_path` | パスが不正 | 絶対パス・`..`・空 | 値の検査 | `MvvError`。`current` はそのまま返し、`preflight.mvv_state` は `unreadable` に変える | `current` は `refused`（`precondition`）、手順 0 は `stopped`（`precondition`）で、どちらも `summary` に設定のパスと値。承認ゲートは省かずに人へ問う | 設定を直して打ち直す |
| `current` | 本文の sha256 が合わない | 承認の後に本文が変わった | sha256 の比較 | `MvvError`（`allow_mismatch` が偽のとき。`mismatch` を状態として返さない） | `ndf records mvv current`・`ndf cross-review run` とも `refused`（`precondition`）、`summary` に「ベースの MVV の本文が承認の記録と合わない」とパス | 本文を戻すか承認し直して打ち直す。直す PR も cross-review を通らないので、人が cross-review を通さずにマージする。直す工程の起動指示は `ndf records mvv current --allow-mismatch`（`ok`・`state: mismatch`・警告つき）の結果を書く。cross-review は相手役を起動せず、状態ファイルとジャーナルを書かない |
| `current` | 設定が壊れている・旧い形 | JSON でない・`_refuse_legacy` | `decl_at` | `MvvError` を呼び出し元へ返す | `refused`（`precondition`）、`summary` に理由 | 設定を直して打ち直す |
| `_mvv_current` | 取得に失敗した | ネットワーク・リモートの名前 | `fetch_branch_or_raise` の `GitError` | 古い参照を読まずに止める | `refused`（`precondition`）、`summary` に取得の誤り | 打ち直せる |
| `_write_verified` | スナップショットを書けない | 保存先の権限・容量 | `OSError` | 呼び出し元へ返す。`drive._start` は `DriveStopped("precondition", …)`、CLI は `precondition` の結果に変える | `refused`（`precondition`） | 打ち直せる |
| `drive._start` | `current` が `MvvError` か `OSError` を投げた | 上のどれか | `_start` の呼び出し | 相手役も修正担当も起動せず、起動を `refused`（`cause.kind: precondition`）で終える。MVV を抜いて続けない（決定 1） | `ndf cross-review run` の `refused`、`summary` に `current` の誤りと直すファイル | 直して打ち直す。状態ファイルとジャーナルは書かない |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SNAPSHOT_DIR` | 定数 | `records/mvv.py` | `snapshots`（`_state_dir()` の下） |
| 本文のスナップショットの名前 | 規則 | `records/mvv.py` | `v<バージョン>-<sha256 の先頭 12 文字>.md` |
| 共通原則のスナップショットの名前 | 規則 | `records/mvv.py` | `principles-<sha256 の先頭 12 文字>.md` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 複数のレーンの `current` | 本文と共通原則のスナップショット | 一時ファイルへ書いて `os.replace`（中身は名前と検証済みのバイト列だけで決まるので後勝ちでよい） | 違う中身のファイルがあっても次の `current` で検証済みの中身に戻る |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `current` | `mvv.decl_at`・`mvv_decide.latest`・`mvv_decide.status` | `grep -n "def " plugins/ndf/src/ndf/domains/records/mvv*.py` | 使う |
| `items_of` | `mvv_material_decide.redlines`・`mvv_gate.principles` | 同上 | `redlines` を使う（P・C の番号）。`principles` は `mvv_gate` が `mvv` を読むため `mvv.py` へ移す（循環を作らない）。Value の番号を拾う関数は無いので足す |
| `body_path` | `preflight.mvv_body` | 同上 | 作業場所のファイルのパスを返す関数で、ベースのコミットから読む `current` とは基点が違う。パスを解く規則だけを純粋な関数へ切り出し、`preflight.mvv_body` もそれを呼ぶ |
| `_mvv_current` の取得 | `Git.fetch_branch_or_raise` | `adapters/git.py` | 使う（取得先を明示した refspec） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `body_path` | 既定（キー無し）→ `.ndf/mvv.md`。`"docs/x.md"` → `.ndf/docs/x.md` | 文字列でない `body` → 既定 | 絶対パス・`../x.md`・空 → `MvvError` |
| `current` | ベースの承認済みの本文からスナップショットを書き、バージョン 11・項目を返す。作業場所の本文が違ってもスナップショットはベースの中身。既定外のパスで承認したリポジトリ → 承認済み・`blobs` にそのパス。`commit` が解いたコミット | 同じ中身で 2 回打つと書き直さずファイルは 1 つ。同じ名前で違う中身のファイルは検証済みの中身になる。ツリーに `.ndf/mvv.json` が無い → `absent` | 解決できない参照・設定はあるが本文が無い・壊れた設定・設定の blob を欠損させたコミット・`ls-tree` の失敗 → `MvvError`（`absent` にならない）。`mismatch` → `MvvError`（`allow_mismatch=True` なら状態 `mismatch`・スナップショットなし）。承認済みのハッシュの無い設定だけがあり本文が無いベース → `unapproved`（止まらず本文も読まない） |
| `mvv_state`・`mvv_checked` | 既定外の正しいパス → 今と同じ状態 | 承認済みのハッシュの無い設定は、パスが不正でも `unapproved` | 承認済みのハッシュがあり `body` が絶対パス・`../x.md`・空 → `mvv_state` は `unreadable`、`mvv_checked` は `stopped` で `summary` に `body` の値、`base_facts` は例外を出さず状態 `unreadable` |
| `_mvv_current` | `--single-branch` で別のブランチを clone した作業場所で新しいバージョンを返す | `--allow-mismatch` で `mismatch` のベース → `ok`・`state: mismatch`・警告 | 取得の失敗 → `refused` |
| `sync_worktree`・`fetch_head` | `base_branch` を渡すと 1 回の `git fetch` に head とベースの 2 本の refspec が並び、リモート追跡参照のベースが新しくなる。`drive._start` はその後に `current` を `<リモート>/<ベース>` で呼ぶ（手元のベースが古く、リモートで新しい MVV が承認済みのとき、新しい版を読み、前の収束を再利用しない） | `base_branch` が None か head と同じ → refspec は 1 本（今と同じ） | ベースを取れない → 同期ごと失敗し、`current` を呼ばない |
| `drive._start` | ローカルの `main` が古くても、merge-base・差分・`Basis.base` がリモートのコミットから作られる | `mismatch` のベースで、PR が本文を戻していても相手役を起動しない | `MvvError` → 相手役を起動しない |
| `items_of` | Value 1〜17・P1〜P4・C1〜C8・Mission・Vision | `## Value` の下の入れ子の箇条（`    - `）を番号にしない | `## Value` が無い本文 → Value の項目が 0 件 |

## 指摘の基準 5

満たす決定: 決定 3、直しの決定 11・15、受け入れ条件 2・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_lines` | `criteria_decide.py` | 変更 | `criteria_lines(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | `mvv` が承認済みのときだけ基準 5 を足す |
| `reviewer_block` | `criteria_decide.py` | 変更 | `reviewer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | 基準 5 があるとき `mvv` のキーの書き方の文を足す |
| `fixer_block` | `criteria_decide.py` | 変更 | `fixer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | 基準 5 と、写しを読んで反するかを確かめ直す文を足す |
| `mvv_item` | `criteria_decide.py` | 新設 | `mvv_item(value: object, items: Sequence[str]) -> Optional[str]` | 相手役の `mvv` が項目の一覧にあればその値、無ければ None |

- `criteria_lines`: `mvv` が None か `state != "approved"` なら今と同じ基準の文。`mismatch` は `current` が止めるので、ここには来ない。基準 5 の文は `review-criteria.json` の `mvv_criterion`（`{version}`・`{path}`・`{principles}` を埋める）
- `reviewer_block`: 基準 5 があるときだけ、`mvv.reviewer`（「基準 5 の指摘は mvv に反する項目を 1 つ書き、impact に反する文と利用者に起きることを書く。必ず人の承認が要る操作に当たるものは P・C の番号を mvv に書く。「出し直さない」の一覧にある指摘でも、必ず人の承認が要る操作に当たるなら P・C の番号を mvv に書いて出す」）を足す。見送りの抑制は通常の指摘にだけ掛け、レッドラインの申告は抑えない
- 指示の JSON の形（`drive._review_prompt` の最後の行）には、基準 5 があるときだけ `"mvv": "<基準 5 のとき反する項目 か 空>"` を足す

### 再利用の条件に MVV を足す

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Basis` | `scope_decide.py` | 変更 | `NamedTuple(base, reviewers, focus, mvv: Optional[str] = None)` | `mvv` は MVV の識別子。承認済みは `"approved:<version>:<sha256>:<共通原則の sha256>"`。承認済みでない（`absent`・`unapproved`）は `"<state>:::"` で、共通原則の sha256 を含めない |
| `read_by_all_head` | `scope_decide.py` | 変更 | （同じ） | ラウンドの記録の `MVV` が `basis.mvv` と違えば None（全差分を読み直し、収束を再利用しない）。`basis.mvv` が承認済みの識別子で、ラウンドの記録の `mvv_checked` が真でない（無い・偽）ときも None |
| `reviewed_of` | `scope_decide.py` | 変更 | `reviewed_of(state: Mapping[str, Any], mvv: Optional[str] = None) -> set[str]` | 状態ファイルの `reviewed_mvv` が `mvv` と違えば空の集合（前の MVV で通ったセクションを今回の基準では通っていないとみなす） |
| `identity_of` | `records/mvv_decide.py` | 新設 | `identity_of(state: str, version: Optional[int] = None, sha256: Optional[str] = None, principles_sha256: str = "") -> str` | 上の規則で識別子を作る純粋な関数。`Snapshot.identity` が呼ぶ |
| `identity_at` | `records/mvv.py` | 新設 | `identity_at(root: Path, ref: str) -> str` | スナップショットを書かずに参照の MVV の識別子を返す。参照・設定・本文を読めないときと、本文が承認の記録と合わないときは `MvvError` |
| `mvv_branch` | `scope_decide.py` | 新設 | `mvv_branch(state: Mapping[str, Any]) -> Optional[str]` | ラウンドの記録から、最後に MVV を読んだブランチを返す |
| `cmd_scope` | `crossreview/cli.py` | 変更 | （同じ） | 識別子を `identity_at` で得る。共通原則の sha256 をプラグインのファイルから求めるのは、ベースが承認済みのときだけ |

- 守る振る舞い: ラウンドの記録と詳細レビュー済みのセクションと見送りに、その時の MVV の識別子をひも付ける。今回の識別子と違う記録は、収束の再利用・詳細レビュー済み・「出し直さない」の一覧のどれにも使わない（前の MVV の基準で通ったものを、今回の基準 5 で通ったとみなさない）
- 承認済みでない識別子に共通原則の sha256 を含めないのは、基準 5 が無く共通原則を渡さないためである。含めると NDF のバージョンを上げるたびに、MVV を持たない全利用先で前の収束と詳細レビュー済みを再利用できなくなる
- 識別子の無い前の記録（この変更より前の状態ファイル）は、今回が `absent` のときだけ同じとみなす
- `run` は起動の始めの同期でベースブランチも取得し、取得した参照から識別子を得る。`review-scope` はネットワークに出ずスナップショットも書かず、手元の参照の MVV を `run` と同じ関数で読んで識別子を得る（blob の id を記録と比べる形にすると、バージョンと本文を変えずに設定だけを変えたときに `run` と違う範囲を返す）。確かめられなければ（本文が承認の記録と合わない・参照が無い・MVV を読むブランチの記録が無い）、どの記録とも違う識別子（`UNKNOWN_MVV`）として全差分（`FULL`）を返し、理由を `warnings` に載せる
- 壊し方（テストで落とす）: MVV のバージョンが上がった後の起動が前の収束を返す。前の MVV で詳細レビューを通ったセクションが `unreviewed` に並ばない。前の MVV の見送りが「出し直さない」に載る。`review-scope` がネットワークに出るかスナップショットを書く。承認済みのベースで、共通原則の本文を変えた後の起動が前の収束を返す。基準 5 の指示に共通原則のスナップショットのパスが無い。`absent` のベースで、共通原則の本文を変えた後の起動が前の収束を再利用しない
- 記録のキーは、ラウンドの記録の `mvv`（`id`・`mvv_branch`）と状態ファイルの `reviewed_mvv`（識別子）にした。`review-scope` が Git を起動する回数は、MVV を読む分で `git ls-tree` と `git cat-file --batch` の 2 回と、参照が SHA でないときの `git rev-parse` の 1 回である

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `mvv_criterion` | 文 | `review-criteria.json` | 「プロジェクト MVV（バージョン {version}。本文は {path}）の Mission・Vision・Value の文に反する。必ず人の承認が要る操作は、本文の表と NDF の共通原則（{principles}）の C1〜C8 で決める」 |
| `mvv_number` | 定数 | `review-criteria.json` | `"5"` |
| `mvv.reviewer`・`mvv.fixer`・`mvv.citation` | 文 | `review-criteria.json` | 相手役・修正担当への文と、根拠の行の誤りの指摘の文（`{path}`・`{cited}`・`{version}`。行の番号を入れない） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `criteria_lines` | 承認済みで基準 5 が番号の順に出る（3 の重点と並ぶ） | `mvv` が None・`absent` で今と同じ行 | — |
| `mvv_item` | `Value 5` → `Value 5` | 前後の空白を除く | 一覧に無い `Value 99`・文字列でない値 → None |
| `read_by_all_head` | MVV の識別子が同じで `mvv_checked: true` → 今の振る舞い | 記録に `MVV` が無く今回が `absent` → 同じとみなす。MVV が無いとき `mvv_checked` を見ない | バージョンか sha256 か共通原則の sha256 が違う・今回だけ承認済み・承認済みで `mvv_checked` が偽か無い → None |
| `reviewed_of`・`state_scope` | `reviewed_mvv` が同じ → 記録のセクション | `reviewed_mvv` が無く今回が `absent` → 記録のセクション | 識別子が違う → 空。設計の PR で全セクションが `unreviewed` に並ぶ |
| `cmd_scope` | 手元の参照の MVV が最後のラウンドと同じ → そのラウンドの識別子で範囲を決める。ネットワークに出ず写しを書かない | MVV の無いプロジェクト → 今と同じ範囲 | 確かめられない → `FULL` |
| `current` の識別子 | `cmd_scope` が同じ MVV から同じ識別子を得る | 改行の形（CRLF）でコミットした設定でも同じ | — |
| 識別子 | 承認済みで共通原則の本文を変えると識別子が変わる | `absent`・`unapproved` は `"<state>:::"` で、共通原則の本文を変えても同じ | — |

## 根拠の行の確かめ

満たす決定: 決定 4、直しの決定 2・3・4・8・12・14、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `fenced_lines` | `records/mvv.py` | 新設 | `fenced_lines(text: str) -> frozenset[int]` | `adapters.markdown.code_lines(text)` が真の行の番号（1 始まり）を返す。自前の走査をしない |
| `decision_lines` | `records/mvv.py` | 新設 | `decision_lines(text: str) -> frozenset[int]` | `adapters.markdown.atx_headings(text)` で、`### 決定 <数>` の見出しの次の行から次の見出しの前までの行の番号（1 始まり）を返す。cross-review の `_citations` と #3959 の `cite` の両方が、決定の根拠の行かをこの関数だけで決める |
| `parse_citation` | `records/mvv_decide.py` | 新設 | `parse_citation(line: str, decision: bool) -> Optional[Citation]` | 1 行を候補か決め、候補なら（項目の並び・バージョンか None）を返す。候補でなければ None |
| `citation_errors` | `records/mvv_decide.py` | 新設 | `citation_errors(lines: Sequence[tuple[str, int, str, bool]], catalog: Mapping[int, Sequence[str]]) -> list[dict[str, Any]]` | 確かめる行（パス・行の番号・本文・決定の根拠の行か）を `parse_citation` に通し、誤りを返す。4 番目の要素は呼び手が `decision_lines` で決めた値で、既定値を持たない |
| `Target` | `crossreview/changes.py` | 新設 | `NamedTuple`。欄 `merge_base: str`・`head: str`・`docs: Mapping[str, str]`・`full_diff: Optional[Path]` | 確かめる対象。`_prepare_round` はラウンドの `_Prepared` から作り（`docs` は `changed` が読んだ head の Markdown の本文、`full_diff` は範囲が `FULL` で起点が merge-base のときだけ書いた差分のファイル）、終わり方の判定の直前は `_final_target` が作る |
| `_final_target` | `crossreview/changes.py` | 新設 | `_final_target(run: _Run, head: str) -> Target` | 終わり方を判定する head の対象。`run.last_target` があり、その `head` が `head` と同じならその `Target` を返す（Git を打たない）。`run.last_target` が無い（ラウンドの対象を作れなかった）か head が違えば（最後のラウンドで修正を push した後）`run.g.merge_base(cfg.base_commit, head)` を 1 回打ち、`docs={}`・`full_diff=None` の `Target` を返す。`merge_base` の `GitError` はそのまま投げ、受けるのは `_final_citations` |
| `_final_citations` | `crossreview/changes.py` | 新設 | `_final_citations(run: _Run, head: str) -> Optional[list[dict[str, Any]]]` | 終わり方の判定の直前の確かめ。`_final_target` と `_citations` を同じ `try` の中で打ち、どちらの `GitError`・`OSError`・`UnicodeDecodeError` も None にして注意に載せる（`run.citations` と `checked_head` は変えない）。`_summary` はこの関数だけを呼び、承認済みの MVV があるときだけ呼ぶ（無ければ `_final_target` も作らない） |
| `_citations` | `crossreview/changes.py` | 新設 | `_citations(run: _Run, target: Target) -> Optional[list[dict[str, Any]]]` | 下の手順。確かめられなければ None。`_prepare_round` は `Target` を作った直後に、確かめの成否によらず `run.last_target = <ラウンドの Target>` を置いてから `_citations(run, run.last_target)` を呼ぶ。`_summary` の前は `_final_citations(run, head)` を呼ぶ。`_prepare_round` を通し直さない。`_prepare_round` も `_summary` も、承認済みの MVV が無い起動では `_citations`・`_final_citations` を呼ばない（Git を打たない）。そのときは `ingest_citations([], run.mvv)`（Git を打たない）で、前の起動から戻した未解決の `by: ndf` の指摘を `fixed` にする（基準 5 が無く確かめない起動で、確かめでしか解決できない指摘が残り続けない） |
| `_committed_texts` | `crossreview/changes.py` | 変更（`drive.py` から移した後） | `_committed_texts(g: git.Git, commit: str, paths: Sequence[str]) -> dict[str, str]` | `commit` のツリーの `paths` の本文を、何件あっても Git を 1 回だけ起動して読む（今の `HEAD` 固定をやめ、コミットを引数に取る）。既存の呼び手（`changed`）は `"HEAD"` を渡す。改行を変えずに `Git.run_bytes` で読む（`git grep` は空のファイルだけのとき終了コード 1 を返すので、`run_bytes` に受ける終了コードの引数 `codes` を足す） |
| `review_diff` | `crossreview/changes.py` | 新設 | `review_diff(g: git.Git, base: str, head: str) -> str` | `git -c core.quotePath=false -c diff.noprefix=false -c diff.mnemonicPrefix=false -c diff.suppressBlankEmpty=false diff --no-color --no-ext-diff --no-textconv --no-relative --src-prefix=a/ --dst-prefix=b/ --unified=3 --inter-hunk-context=0 --find-renames -l1000 --submodule=short <base>...<head>` を 1 回打つ。利用者の設定（`diff.noprefix`・`diff.mnemonicPrefix`・`diff.relative`・`diff.context`・`diff.interHunkContext`・`diff.suppressBlankEmpty`・`diff.renameLimit`・`diff.submodule`・`diff.external`・属性の `textconv`）で接頭辞・パス・hunk の行の形・本文が変わらないよう、機械で読む形をコマンドの側で固定する（`--no-textconv` で、hunk の行がコミットの blob の行と番号ごと対応する）。改名の検出の上限は Git の既定と同じ 1000 に固定し、外さない（相手役に渡す差分を作るラウンドごとに、消した数×足した数の比較が際限なく増えるため）。出力は `Git.run_bytes` で読んで UTF-8 に直す（読めない字は置き換える）。文字列で読むと行の途中の `\r` が改行に変わり、hunk の行数が合わなくなる。相手役に渡す差分のファイル（`_prepare_round`）と `_citations` の全差分の両方がこの関数で作るので、`full_diff` を読み直しに使える |
| `_diff_files` | `crossreview/changes.py` | 新設 | `_diff_files(text: str) -> list[DiffFile]` | `review_diff` の出力を、ファイルごとの（新しいパス・元のパス・hunk の並び・足した行の番号・消した行の本文と足した行の本文）に分ける。新しいパスは `+++ b/<パス>`、改名は `rename from <パス>` と `rename to <パス>` の行から読む。`core.quotePath=false` でも Git が引用する（`"` で始まる）パスは C の書き方の引用を解いて読む。空白を含むパスの後ろに Git が足すタブを除く。`+++ /dev/null`（消したファイル）と、パスの行の無いファイル（バイナリ・実行権限だけの変更・空のファイル）は飛ばす。足した行（`DiffFile.added`。行の番号から本文）と消した行（`DiffFile.removed`）は hunk の行から求める。行の番号は Git の数え方（`\n` だけで区切る） |
| `_base_lines` | `crossreview/changes.py` | 新設 | `_base_lines(hunks: Sequence[Hunk], head_line: int) -> Optional[int]` | head の変わっていない行の番号を、hunk の行数の差からベースの行の番号へ換算する。足した行なら None |
| `_markdown_rows` | `crossreview/changes.py` | 新設 | `_markdown_rows(text: str) -> list[tuple[int, str]]` | 本文の行を Markdown の解析と同じ数え方（`\n`・`\r\n`・`\r` で区切る）で並べ、行ごとに Git の行の番号と本文を返す。並びの n 番目が `fenced_lines`・`decision_lines` の n 行目に当たる。`\r` だけの改行を含む文書では 2 つの番号が違うので、`_citations` はこの関数で対応させる |
| `_Prepared` | `crossreview/drive.py` | 変更 | 欄 `docs: Mapping[str, str]` と `full_diff: Optional[Path]` を足す（意味は `Target` と同じ） | — |
| `_settle_resolved` | `crossreview/drive.py` | 変更 | （同じ） | `by` が `ndf` の指摘を飛ばす（終わり方を変えない） |
| `citation_key` | `journal.py` | 新設 | `citation_key(error: Mapping[str, Any]) -> str` | 誤りの照合キー。`[path, cited, kind]` の生の値を区切りの曖昧さの無い JSON（`json.dumps(..., ensure_ascii=False, separators=(",", ":"))`）にした UTF-8 の SHA-256 の 16 進を返す。伏せ字も鍵も通さない（キーは手元のジャーナルだけに置き、公開のコメントに載せない） |
| `_Run` | `crossreview/drive.py` | 変更 | 欄 `clean: Callable[[Sequence[str]], Sequence[str]]`・`snapshot: Snapshot`・`mvv: str`・`checked_head: Optional[str]`・`last_target: Optional[Target]`・`citations: dict` を足す | `clean` は `_start` が記録のコメントに渡すのと同じ `hub.clean` を置き、`_summary` が `mvv_outputs` と `record_review` に渡す（`_citations` は使わない）。`snapshot` は起動の始めに `current` が返した値、`mvv` は今回の識別子。`checked_head` は確かめが最後に成功した head、`citations` は（merge-base・head・識別子）→ 成功した確かめの結果、`last_target` は最後のラウンドの対象 |
| `FindingJournal.ingest_citations` | `journal.py` | 新設 | `ingest_citations(self, errors: Optional[Sequence[Mapping[str, Any]]], mvv: str) -> bool` | 下の規則。指摘を足したか、既存の指摘の欄の値が変わったときだけ真を返す。誤りごとに `citation_key` を作って突き合わせる。`clean` を呼ばない |

- `parse_citation`
  - 候補: 左から空白と `- ` を除いた先頭が `根拠:` で、`MVV` を含むか、`decision` が真
  - 解析: `根拠:` の後ろから、`「` と対応する `」` の間（入れ子を数える。終わりが無ければ行末まで）を除く。残りで最初の `（MVV` を探し、そこから最初の `）` までをバージョンの表記として、全体が `（MVV バージョン (\d+)(・スプリント MVV [0-9a-f]{8})?）` に一致するときだけバージョンを読む。終わりの `）` の後ろ（`。STD1` など）は読まない。`（MVV` の前を ` / ` と `・` で分けて前後の空白を除いた値を項目にする。`スプリント` で始まる項目と `根拠なし` は確かめない
  - バージョンを読めない候補はバージョンを None で返す（`（MVV 版 11）`・`（MVV バージョン 11x）`・終わりの `）` の欠け・バージョンの欠け・`（MVV なし）` を含む）
- `citation_errors` の返す 1 件: `{"path", "line", "cited", "kind", "version", "unknown"}`。`cited` は種類によらず、`根拠:` の後ろから引用文（`「`〜`」`）を除き、最初の `（MVV` から後ろ（無ければ何も除かない）を除いて前後の空白を除いた文字列である（`format` でも行の原文・引用文・バージョンの表記を入れない。例 `- 根拠: Value 3「…」（MVV 版 11）` → `Value 3`）。`unknown` は一覧に無い項目の並び。`cited`・`unknown` は PR 本文から取った自由な文字列なので、結果と記録へ書く前に `clean` を通す（「終わり方と結果」の `mvv_outputs`・`record_review`）。`kind` は `format`（バージョンが None。`version` は None・`unknown` は空）・`version`（バージョンが `catalog` に無い）・`unknown`（バージョンはあり、そのバージョンの一覧に無い項目がある）。`catalog` は `{ベースの承認済みのバージョン: Snapshot.items}` の 1 件だけで、PR が MVV を変えても head のバージョンを入れない（要求仕様の前提 1）。バージョンと項目を別々に比べず、必ずバージョンから一覧を引く
- `_citations` の手順
  1. 鍵 `(target.merge_base, target.head, 識別子)` が `run.citations` にあれば、その結果を返す（Git を打たない）
  2. 全差分: `target.full_diff` があればそのファイルを改行を変えずに読み（`Path.read_text` は `\r` を改行に変えるので使わない）、無ければ `review_diff(run.g, target.merge_base, target.head)` を 1 回打つ。`_diff_files` で分ける
  3. 確かめる候補のファイルは次の 2 つだけとし、ほかのファイルの本文は読まない。足した行を持つだけのファイル（生成したソース・lockfile の 1 行の変更など）は候補にしない
     - (a) を調べるファイル: 足した行のどれかが `parse_citation` の候補の前置き（左から空白と `- ` を除いた先頭が `根拠:`）に当たるファイル。足した行は Git の 1 行のまま見ず、`_markdown_rows` で Markdown の行に分けてから前置きに当てる（`説明\r- 根拠: …` の 2 行目も当たる）。決定の根拠の行かは本文が要るので、ここでは `MVV` の有無も見出しも見ない
     - (b) を調べるファイル: hunk の消した行か足した行を `_markdown_rows` で Markdown の行に分け、引用の `>` とリストの印を除いた行頭が 3 字以上の `` ` `` か `~` の行を持つファイル
     - 改名の検出が上限（`review_diff` の `-l1000`）を超えた PR では、書き換えて改名した文書が、元のパスの無い、全行を足したファイルとして出る。改名を読み直さず、その全行を足した行として (a) で確かめる。囲みを消して本文に出た行も (a) に入るので誤りは落ちない。代わりに、その文書ではベースから変わらない根拠の行の誤りも拾う（「変わらない行を拾わない」の例外）
     - (a) を調べるファイルで確かめるのは、その前置きに当たる、足した行の Markdown の行だけである。足した Git の行の中の Markdown の行は、ベースに同じ文があってもすべて足した行とみなす。(a) にも (b) にも当たらないラウンドは、全差分を読んだ後に Git を打たず空の誤りを返す
  4. head の本文: 候補のファイルのうち `target.docs` にあるものを使い、無いもの（Markdown でないファイル・`_final_target` の新しい head）は `_committed_texts(run.g, target.head, <新しいパス>)` で 1 回にまとめて読む
  5. 各候補のファイルで `fenced_lines` の外にある候補の行のうち、(a) 足した行を集める。(b) を調べるファイルに、足していない候補の行があるときだけ、そのファイル群のベースの本文を `_committed_texts(run.g, target.merge_base, <元のパス>)`（改名は `rename from` のパス）で 1 回にまとめて読み、`_base_lines` で換算したベースの行がベースの `fenced_lines` に入る行だけを (b) として残す。head とベースの本文の行は `_markdown_rows` で並べ、足した行と `_base_lines` は Git の番号、`fenced_lines`・`decision_lines` は並びの番号で引く。1 つの Git の行に Markdown の行が複数あるときは、head の（Git の番号, その Git の行の中で何番目の Markdown の行か）を、`_base_lines` で換算したベースの（Git の番号, 同じ何番目）に当て、ベースの並びの番号を決めて `fenced_lines` を引く（足していない Git の行は両側で本文が同じなので、分け方も同じになる）
  6. 決定の根拠の行かは head の本文の `decision_lines` で決める
  7. `citation_errors` を当て、結果を `run.citations` に置き（照合キーは `ingest_citations` が `citation_key` で作るので、ここでは足さない）、`run.checked_head = target.head` とする（`run.last_target` はここで置かない。`_prepare_round` が確かめの前に置く）。GitHub を呼ばず、`clean` も呼ばない
  - `GitError`・`OSError`・`UnicodeDecodeError` は None を返して注意に載せる。`run.citations` と `checked_head` は変えない
- `_citations` 1 回の Git の起動の数（直しの決定 3・14。`changed` と相手役の差分の読み取りは数えない）

  | 場合 | `git merge-base` | 全差分 | head のまとめ読み | ベースのまとめ読み | 計 |
  | --- | --- | --- | --- | --- | --- |
  | 同じ鍵の確かめが既にある（無修正の収束の終わり方の判定の直前を含む） | 0 | 0 | 0 | 0 | 0 |
  | 範囲が `FULL`・候補のファイルがすべて Markdown で `docs` にある・(b) を調べるファイルに足していない候補の行が無い | 0 | 0 | 0 | 0 | 0 |
  | 範囲が狭まったラウンド | 0 | 1 | 0〜1 | 0〜1 | 1〜3 |
  | Markdown でない候補のファイルがある | 0 | 0〜1 | 1 | 0〜1 | 1〜3 |
  | フェンスの行を消した・足したファイルに、足していない候補の行がある | 0 | 0〜1 | 0〜1 | 1 | 1〜3 |
  | 最後のラウンドの後に push した新しい head（`_final_target`） | 1 | 1 | 1 | 0〜1 | 3〜4 |

  上限は、ラウンドの中の確かめで 3 回（全差分・head・ベースを各 1 回）、終わり方の判定の直前の新しい head で 4 回である。ファイルの数によって増えない
- `ingest_citations` の規則
  - `errors` が None なら何も変えない（既存の `by: ndf` の指摘を解決させない）
  - 誤りのキーは `citation_key(error)`（`[path, cited, kind]` の生の値の SHA-256）で、`by: ndf` の指摘を作るときに指摘の `citation_key` に書く。照合はこの欄だけで行い、表示用の `text` や `oscillation.finding_key(text)` を使わない（`text` は記録のコメントで伏せ字が入り得る）
  - キーは手元の状態ファイル（ジャーナル）だけに持つ。`open_block`・`Carried`・`recorded`・`carried` は持たず、記録のコメントの一覧とスレッドの引継ぎに載らない。公開しないので、伏せ字も鍵も通さない（直しの決定 7）
  - `citation_key` の無い `by: ndf` の指摘（記録のコメントから戻した指摘・この変更より前の記録から戻した指摘）はどの誤りにも当たらないので `fixed` になり、同じ誤りは新しい指摘として 1 回だけ足される。別の状態ディレクトリからの再開ではこの経路になる（直しの決定 7 の代償。誤りは落ちない）。同じ状態ディレクトリでの再開は、ジャーナルの指摘がキーを持つので元の指摘に当たる
  - 誤りのキー（`citation_key`）に当たる既存の `by: ndf` の指摘は、終わり方が `fixed`・`deferred` でも `unresolved` にし、`judged_mvv = mvv` に置き直す。当たる指摘が無い誤りは新しい `by: ndf` の指摘（`major`・`judged_mvv = mvv`）として足す。引いた項目は `cited`・`unknown` に生のまま置き、`mvv`・`mvv_pinned`・`redline`・`redline_items` は置かない。`P1` を誤って引いた指摘も `handable` に入り、修正担当へ渡って収束を止める
  - 未解決の `by: ndf` の指摘で誤りのキーに当たらないものは、`judged_mvv` によらず `fixed`（`judged_mvv = mvv`）にする。`by: ndf` の指摘を解決するのはこの経路だけである（直しの決定 12）
  - 呼び手は `_prepare_round` と `_summary` の前の確かめで、`_citations`（`_final_citations`）が返した誤りと `run.mvv`（今回の識別子）を渡す。`hub.clean` は `run.clean` として記録のコメントと `_summary`（`mvv_outputs`・`record_review`）だけが使い、`_citations` と `ingest_citations` は使わない
- 指摘の `text` は `review-criteria.json` の `mvv.citation` から作り、行の番号を含めない。パスと引いた本文だけで作るので、行がずれても同じ文になる
- `apply_fix`: 修正担当の結果のうち `by: ndf` の指摘への `fixed`・`deferred` は終わり方を変えない（理由は `fixer_reason` に残す）。返す件にも入れない（#4109。送り直しの後に `restore_fixed` が未解決の指摘を `fixed` へ戻さないため）。解決は次の `ingest_citations` だけが決める。`redline` の指摘への結果も同じく終わり方を変えず、返す件に入れない（人の承認を待つ）。`restore_fixed` も `redline` の指摘は `fixed` へ戻さない
- 確かめる時点: 毎ラウンド、相手役を起動する前に `_prepare_round` が `_citations` → `ingest_citations` を打つ。ただし、そのラウンドの head が PR に届いていなければ（`_Run.sent` と違う。前の起動の修正担当がコミットだけを残して終わった）`ingest_citations` を打たず、結果を状態ファイルの `unsent_citations` だけに残し、ラウンドの記録の `mvv_checked` は偽にする。加えて、終わり方を判定する直前（最後のラウンドの修正を送った後、または収束と判定する前）に、その時点の head で `_prepare_round` を通さず `_final_citations` → `ingest_citations` をもう 1 回打つ。これで修正担当の最後の修正も機械が確かめ、確かめられなければ `mvv_checked` が偽になって `mvv_unresolved` で止まる
- 実装の形: モジュールをまたいで読む `_citations`・`_final_citations` は下線を外した。引数は `changes.CitationRun`（`drive._Run` のうち確かめが読み書きする欄の Protocol）で受け、`changes.py` は `drive.py` を読まない。承認済みの MVV が無いときは `citations` が Git を打たずに空を返す。誤りの 1 件には指摘の文 `text` を足して返し、文は `review-criteria.json` の `mvv.citation` と、誤りの種類ごとの `mvv.citation_reasons` から作る。`ingest_citations(errors, mvv, round_no, *, recorded=True)` は、新しい指摘の id を `r<ラウンド>-n<番号>` にし、`round_no` のラウンドの記録が無い head（最後の修正を送った後）では次のラウンドの番号を付け、未解決の指摘の位置を変えない（PR のスレッドはラウンドの記録の head に付くため）。指摘にするのは、PR に届いた最後のコミット（始めの同期で取得した先頭か、この起動で送ったコミット。`_Run.sent`）の誤りだけである。作業場所に残る最後のコミット（修正を送れなかった・修正担当が途中で終わったときは作業場所の HEAD）がそれと違えば別に確かめ、誤りの中身と失敗の原因は状態ファイル（所有者だけが読める権限で置き換える）の `unsent_citations` だけに残し（PR の記録・注意・結果 JSON に載せない）、件数を `Review.unsent_citations` で `finish` へ渡して `mvv_unresolved` で止める
- ラウンドの記録: 書くときに `mvv_checked = (承認済みの MVV が無い) or (run.checked_head == そのラウンドの head == run.sent)` を足す。終わり方を判定する直前の確かめが同じ head で成功したら、この起動の最後のラウンドの記録を真に直して保存する

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_citations` | 差分か本文を読めない | `git diff`・`git grep`・`git merge-base` の失敗 | `GitError`・`OSError` | None。使い回しに置かず、`checked_head` を変えない | 結果の `warnings`。最後の head で起きれば `mvv_unresolved` で止まる（NDF の指摘が 0 件でも） | 次のラウンドか打ち直しで読み直す |
| `parse_citation` | バージョンを読めない | 書式の外れ・バージョンの欠け・終わりの `）` の欠け・バージョンの数字の後ろの余分な文字 | 正規表現の不一致 | 候補なら `format` の誤り。候補でなければ拾わない | NDF の指摘（`major`） | 直せば次の確かめで消える |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `citation_errors` | 候補だがバージョンを読めない | 1 件（`kind: format`。`version` は None・`unknown` は空） |
| `citation_errors` | 引いたバージョンが `catalog` に無い | 1 件（`kind: version`。`unknown` は空） |
| `citation_errors` | 引いたバージョンが `catalog` にある・そのバージョンの一覧に無い項目がある（ほかのバージョンにはある項目も含む） | 1 件（`kind: unknown`。`unknown` に項目） |
| `citation_errors` | 引いたバージョンが `catalog` にある・項目が全部そのバージョンの一覧にある | 返さない |
| `citation_errors` | 先頭が `根拠:` でない（文中の「根拠: Value N（MVV バージョン 1）」の引用） | 返さない |
| `ingest_citations` | 誤りが前のラウンドにあり今回も出た（修正担当が `fixed`・`deferred` と答えていても） | `unresolved` |
| `ingest_citations` | 誤りが前のラウンドにあり今回は出ない | `fixed` |
| `ingest_citations` | 確かめられなかった（None） | 何も変えない（`checked_head` も変わらず、`mvv_unresolved` で止まる） |
| `_prepare_round`・`_summary` | MVV が承認済みでない | 確かめない（前の起動から戻した未解決の `by: ndf` の指摘は `ingest_citations([], run.mvv)` で `fixed`） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fenced_lines` | 囲みの間の行とフェンスの行 | 4 字で開き 3 字の行を含む例・リストの中のフェンス・チルダ・終わりの無いフェンス | — |
| `decision_lines` | `### 決定 1` の下の行から次の見出しの前まで | 文書の末尾の決定・`#### ` の小見出しで終わる | 決定の見出しが無い → 空 |
| `parse_citation` | ` / ` の 2 項目・`・` と引用文の 2 項目・`（MVV バージョン 11・スプリント MVV 3f9a1c2e）` | 引用文の中の `（` と ` / `・終わりの無い `「`・バージョンの括弧の後ろの `。STD1` | `（MVV 版 11）`・`（MVV バージョン 11x）`・終わりの `）` を消した行・バージョンの欠けの決定の根拠の行・`（MVV なし）` → バージョン None。決定の外の `根拠: 実測` → 候補でない |
| `citation_errors` | バージョン 1 を引いた行 → `version`。`Value 18` → `unknown`。4 要素の値で決定の根拠の行のバージョンの欠けを `format` にする。`- 根拠: Value 3「…」（MVV 版 11）` の `format` の `cited` は `Value 3`（引用文とバージョンの表記を含まない） | `スプリント Value 4`・`根拠なし` は確かめない。バージョン 12 にだけある項目をバージョン 11 で引く → `unknown`（`catalog` がバージョンごと）。バージョンの表記の無い決定の根拠の行 `- 根拠: Value 3「…」` の `cited` は `Value 3` | head のバージョンを引く → `version`。3 要素の値 → `TypeError`（既定値で決定の外として通さない） |
| `_diff_files` | 新しいパス・足した行・hunk | 日本語を含むパス（引用されない）・`"` を含むパス（引用を解く）・改名（元のパスが `rename from`）・消したファイルは飛ばす | — |
| `review_diff` | `+++ b/<パス>` と `@@ -a,b +c,d @@` の形で出る | 利用者の設定 `diff.noprefix=true`・`diff.mnemonicPrefix=true`・`diff.context=0`・`diff.interHunkContext=20`（離れた 2 行を変えた文書）・`diff.suppressBlankEmpty=true`・`diff.renameLimit=1`・`diff.submodule=diff` と `textconv` の属性を置いたリポジトリでも、同じ差分が出て `_citations` が根拠の行の誤りを拾う | — |
| `_base_lines` | hunk の前後の行を換算する | ファイルの先頭と末尾の hunk・片側が 0 行の hunk | 足した行 → None |
| `_markdown_rows` | 並びの長さが `adapters.markdown.code_lines` の長さと同じ | `\r` だけの改行・`\r\r\n`・末尾の `\r`・空の文書・`\r`・`\n` 以外の改行の字（U+2028 など） | — |
| `_citations` | 足した行と、フェンスの行だけを消して本文に出た行を拾う。承認済みの MVV がある PR で、Markdown でないファイル（lockfile など）の `根拠:` を含まない 1 行だけを変えた → head の本文を読まない（全差分の後の Git が 0 回）。日本語のパスの文書と改名した文書でも拾う。足した Git の 1 行 `説明\r- 根拠: Value 999（MVV バージョン 1）` の 2 つ目の Markdown の行を拾う | ベースから本文の変わらない行を拾わない。足していない 1 つの Git の行に `\r` で区切った根拠の行が 2 つあり、ベースでは前の片方だけがリストの中のフェンス（前の行の `- ` + フェンス）の内側にあって、フェンスの行を普通の箇条に変えた → その片方だけを拾う。改名として出ない（元のパスの無い）文書 → 全行を足した行として確かめ、ベースを読まない。ベースの本文は `merge_base` のコミットと元のパスで読む（head の本文を読まない）。同じ鍵の 2 回目は Git を打たず、1 回目と同じ結果を返す。同じ別リポジトリへの参照を含む誤りが 3 件ある確かめで、`clean` の呼び出しと公開範囲の照会（`hub.visibilities`）が 0 回。範囲が狭まったラウンドでは全差分を 1 回打つ。上の表の場合ごとの起動の数（0 回の 2 つの場合と、両側を読む場合の上限 3） | 失敗 → None、`checked_head` が変わらない |
| `_final_target`・`_final_citations` | 最後のラウンドと同じ head → 既存の結果を使い Git を打たず、ジャーナルが変わらないので状態ファイルも書き直さない。承認済みの MVV が無い PR で最後のラウンドの修正を push した → `git merge-base` を打たない（`_final_target` を作らない） | 最後のラウンドの後に push した head → そのコミットの差分と本文を読み（計 4 回まで）、新しい head の誤りを拾い `checked_head` がその head になる。その指摘は結果を返す前に状態ファイルにあり、記録のコメントの一覧にも載る。同じ状態ディレクトリの打ち直しで確かめが失敗しても未解決のまま残る。最初のラウンドの確かめが失敗した後の終わり方の判定 → `last_target` があり、同じ head なら確かめ直す | merge-base の失敗 → 例外を外へ出さず None・警告・`mvv_unresolved`。`last_target` が無い → merge-base から対象を作る（`AttributeError` にならない） |
| `citation_key`・`ingest_citations` | 新しい誤り → `judged_mvv` が今回の `by: ndf` の指摘。前のバージョンの `P1` を引いた誤り → `mvv` も `redline` も持たず `handable` に入り、残れば `mvv_unresolved`。消えた誤り → `fixed`。同じ状態ディレクトリでの再開で同じ誤りを再検出 → 元の指摘が未解決のまま置き直され、新しい指摘を足さない。`citation_key` は生の `[path, cited, kind]` だけで決まり、伏せ字の規則・秘密の環境変数・参照先の公開範囲を変えても同じ | 照合キーを持たない `by: ndf` の指摘（記録のコメントから戻した指摘）がある状態で同じ誤りを再検出 → 元の指摘が `fixed`・新しい指摘が 1 回だけ足される。識別子の違う既存の指摘も、誤りに当たれば今回の識別子へ、当たらなければ `fixed` | `errors` が None → 何も変えない |
| `apply_fix` | `by: ndf` の指摘と `redline` の指摘への `fixed`・`deferred` は終わり方を変えない | — | — |
| `_settle_resolved` | 普通の指摘は今どおり `fixed` | `by: ndf` の指摘は `unresolved` のまま | — |

## ジャーナルとレッドライン

満たす決定: 決定 3・5、直しの決定 5・6、受け入れ条件 2・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FindingJournal.handable` | `journal.py` | 新設 | `handable(self) -> list[dict[str, Any]]` | 修正担当へ渡す未解決の指摘（収束を止める重さで、`mvv` が `P`・`C` でないもの） |
| `FindingJournal.skipped_texts` | `journal.py` | 変更 | `skipped_texts(self, mvv: Optional[str] = None) -> list[str]` | 見送り済みの指摘のうち、`skipped_mvv` が `mvv` と同じものの本文だけを返す（`skipped_mvv` の無い見送りは、今回が `absent` のときだけ同じとみなす） |
| `FindingJournal.apply_fix` | `journal.py` | 変更 | `apply_fix(self, items, handed, round_no, *, mvv: Optional[str] = None)` | `deferred` を書くとき、修正担当を起動したときの識別子 `mvv` を `skipped_mvv` に書く（`fixed` では消す）。前の起動が残した結果（`_ingest_pending`・`merge_fix`）は、どの識別子での見送りか決められないので `mvv` を渡さず、`skipped_mvv` を書かない（起動したラウンドの識別子で書くのは #4084 の設計の `sent_mvv`）。`by: ndf` の指摘と `redline` の指摘の終わり方は変えず、返す件にも入れない（#4109） |
| `FindingJournal.enter` | `journal.py` | 変更 | `enter(self, round_no, raised, items: Sequence[str] = (), mvv: Optional[str] = None) -> frozenset[str]` | 相手役の `mvv` を `mvv_item` で確かめて指摘の `mvv` に書く。一覧に無ければ外し、`impact` の無い MVV の指摘とともに `self.warnings` に足す（呼び手が結果の注意へ移す。相手役が書いた値は載せず指摘の id だけを載せる）。分類を置くときに `judged_mvv` を書き、`mvv_pinned` で固定するときは前の重さを `unpinned_severity` に残す。出し直された指摘の `judged_mvv` が今回と違えば、`restate` と同じ外し方をしてから今回の申告で分類する（終わり方によらない）。識別子の違う見送りの出し直しは未解決へ戻して再判定させる。同じ識別子で `Value` などとして見送った指摘が初めて `P`・`C` で出し直されたときも未解決へ戻す（記録のコメントの一覧は未解決の指摘だけを載せるので、見送りのままでは別の状態ディレクトリからの再開でレッドラインが消える） |
| `FindingJournal.restate` | `journal.py` | 新設 | `restate(self, mvv: Optional[str]) -> list[str]` | `judged_mvv` を持ち今回の識別子と違う指摘の分類を外し、外した id を返す。`drive` が引継ぎ（`_carry`）の後・収束の再利用の判定と最初のラウンドの前に 1 回打つ |
| `FindingJournal.carry` | `journal.py` | 変更 | `carry(self, carried, items: Sequence[str] = (), mvv: Optional[str] = None)` | `Carried` の分類と `unpinned_severity` を戻す。重ねない判定は今どおり（同じ id か `finding_key` の同じ指摘があれば入れない）で、戻した `by: ndf` の指摘は `citation_key` を持たない。`judged_mvv` が今回と同じ件は `mvv_item` で項目を確かめ直して戻す。違う件は前の分類を `mvv_history` に入れ、`unpinned_severity` があれば重さをそれへ戻して分類なしで戻す（`restate` と同じ外し方） |
| `Carried` | `prior_decide.py` | 変更 | 欄 `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity`・`thread`（どれも既定 None）を足す | `thread` は記録のコメントの一覧にあったスレッドの番号。照合キーは持たない（直しの決定 7） |
| `recorded` | `prior_decide.py` | 変更 | `recorded(remarks) -> tuple[list[Carried], dict[int, Mapping[str, Any]]]` | 信頼済みの最新の記録のコメントから、スレッドの無い件と、スレッドの有無と resolve によらず `by: ndf` の件を返す（`by: ndf` の件は `thread` を持ち `comment` は None）。スレッドのある件の `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity` を `thread` → 値の表で返す |
| `carried` | `prior_decide.py` | 変更 | `carried(threads, tracked, marks: Mapping[int, Mapping[str, Any]] = {})` | 開いたスレッドから戻す件に、`marks` の同じスレッドの値（`recorded` の表の 5 つの欄）を重ねる（resolve 済みを除くのは今どおり） |
| `drive._carry`（起動の始めの引継ぎ） | `drive.py` | 変更 | — | `carried` の結果に、`recorded` の `by: ndf` の件のうち、`thread` が「`carried` が実際に返した件の `comment`」にも「状態ファイルが既に持つスレッド（`tracked`）」にも無いものを足して `carry` に渡す（resolve 済み・消えたスレッドに加え、開いていても `carried` が書き手や書式の検査で除いたスレッドの件を落とさない）。スレッドが開いているかでは決めない。`carried` が返した `by: ndf` の件はその側を使い、重ねない |
| `open_block` | `report_decide.py` | 変更 | （同じ） | 1 件に `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity` を足す。照合キー（`citation_key`）は載せない。`redline` の指摘と `by: ndf` の指摘は `_light` でも載せる。自由記述でない値なので `clean` を通さない |

- 守る振る舞い: 相手役の指摘をジャーナルへ入れるときは（`by: ndf` の指摘は `mvv` を持たず、この分類を通さない）、(1) `mvv_item` で `mvv` を確かめ（一覧に無ければ外して注意。以降は普通の指摘）、(2) 有効な `P`・`C` ならレッドラインの指摘、(3) 有効な `Value`・`Vision`・`Mission` なら重さを `major` 以上に固定した MVV の指摘、の順に分ける
- レッドラインの指摘は、受けた `P`・`C` の項目をすべて残し（重複を除き、項目の一覧の順に並べる）、修正担当へ渡さず、収束を止めず、振動の判定にも入れない。重さは固定せず（`Value` として固定していた重さも外す）、要約と記録のコメントでは「人の承認を待つ」件数として、未解決・軽い指摘と分けて数える。同じ指摘の申告の到着順（同じラウンドの別の相手役・後のラウンド）と、先に `Value` として見送ったかによらず、同じ識別子で `P`・`C` を一度でも受けた指摘はレッドラインになる
- 識別子が変わったとき: `judged_mvv` が今回の識別子と違う指摘は、相手役が出し直したかと終わり方によらず、今回の判定・結果・記録に使う前に分類を外す（`restate`）。出し直された指摘は今回の申告だけで分類し直す
- `restate` の外し方: 前の `judged_mvv`・`mvv`・`redline_items`・`outcome`・`skipped_mvv` を `mvv_history` へ追記し、`mvv`・`mvv_pinned`・`redline`・`redline_items`・`judged_mvv` を消し、`unpinned_severity` があれば `severity` をそれに戻して消す。終わり方が `fixed`・`deferred` の指摘は終わり方をそのまま残す（今回の結果と記録の対象からは `judged_mvv` が無いので外れる）。`unresolved` の指摘は普通の未解決の指摘として残る。`by: ndf` の指摘は外した後も `by: ndf` のままで、次の `ingest_citations` が今回の識別子で置き直すか `fixed` にする
- 識別子の違う見送りが出し直されたら、`fixed` の再発と同じく未解決へ戻し（`revived` に入れ、前のスレッドの resolve を根拠に `fixed` にしない）、今回の基準で再判定させる。重さは `unpinned_severity` へ戻した値と今回の申告の重い方になる（`_raised_again` の規則のまま。固定前から重かった指摘は軽く出し直されても重いまま残り、修正担当へ渡る。直しの決定 6 の代償）
- 出し直されなかった識別子の違う指摘（見送りを含む）は、ジャーナルに履歴として残すが、今回の結果（`outputs.mvv`）と記録（`record_review` の `mvv-review`・`review_disputed`）の対象から外す。対象にするのは、`judged_mvv` が今回の識別子と同じ指摘だけである（今回のバージョンで申告されていない見送りを今回の head とバージョンで書き直さず、改訂の兆候を動かさない）。`skipped_mvv` は見送りの時点の識別子で、`skipped_texts` だけが使う
- 記録の一覧から戻す `by: ndf` の指摘: 信頼済みの最新の記録のコメントに残る `by: ndf` の指摘は、人がスレッドを resolve していても戻す。戻した指摘を解決できるのは `ingest_citations` の機械の確かめだけで、確かめが失敗した（None）起動では未解決のまま残る（直しの決定 12）
- #4084 との統合: `restate` と `enter` の分類し直しは、#4084 の設計の決定 4 の「MVV の再判定」と同じ時点である。後に入る PR が `unkept_severity`・`kept_round` の置き直しを両方の経路で打つ
- 壊し方（テストで落とす）: 前のバージョンの見送り・レッドラインが今回の `disputed`・`redlines`・`review_disputed` に載る。`mvv_pinned` で `major` に固定した前のバージョンの指摘が、`mvv` 無しの `minor` で出し直されて `major` か `mvv_pinned` のまま残る。分類の履歴を消す。見送り済みの本文と同じ指摘を相手役が `P1` で出せない指示になる
- #4014 の `effective_severity`（`impact` の無い `major` を下げる）は、MVV の指摘と `by: ndf` の根拠の行の誤りに当てない。`impact` の無い MVV の指摘は下げずに注意へ載せる
- 壊し方（テストで落とす）: レッドラインが修正担当へ渡る・収束を止める。到着順を入れ替えると分類か残る項目が変わる。見送り済みの `Value 5` の指摘に後から来た `P1` がレッドラインとして結果と記録に残らない。MVV の指摘が `minor` へ下がって渡らない

### 内部のデータ

| 保存先 | 項目 | 型 | 意味 | 書く時点 |
| --- | --- | --- | --- | --- |
| ジャーナルの指摘 | `mvv`・`mvv_pinned`・`redline`・`redline_items` | 文字列・真偽値・真偽値・配列 | MVV の分類（反する項目・重さを固定したか・レッドラインか・受けた `P`・`C` の項目） | 分類を置くとき |
| ジャーナルの指摘 | `skipped_mvv` | 文字列か無い | 見送った時点の識別子 | `apply_fix` が `deferred` を書くとき |
| ジャーナルの指摘 | `judged_mvv` | 文字列か無い | 分類を決めたときの識別子。分類を持たない指摘は持たない | `enter`・`carry`・`ingest_citations` が分類を置くとき |
| ジャーナルの指摘 | `unpinned_severity` | 重要度か無い | `mvv_pinned` で `major` 以上に固定する前の重さ（相手役の申告に `effective_severity` を当てた値） | 固定するとき |
| ジャーナルの指摘 | `mvv_history` | 配列か無い | 識別子が変わって外した分類（`judged_mvv`・`mvv`・`redline_items`・`outcome`・`skipped_mvv`）。追記だけ | 分類を外すとき |
| ジャーナルの指摘（`by: ndf`） | `cited`・`unknown` | 文字列・配列 | 引いた項目の部分と一覧に無い項目（生の値。`mvv` の代わりに持つ） | `ingest_citations` が指摘を作るとき |
| ジャーナルの指摘（`by: ndf`） | `citation_key` | 文字列か無い | 根拠の行の誤りの照合キー。手元の状態ファイルだけに持つ | `ingest_citations` が指摘を作るとき |
| ラウンドの記録 | `mvv` | オブジェクト | `id`（識別子）と `mvv_branch`（MVV を読んだブランチ） | ラウンドの記録を書くとき |
| 状態ファイル | `reviewed_mvv` | 文字列か無い | `reviewed_sections` のセクションを読んだときの識別子 | `reviewed_sections` を書くとき |
| ラウンドの記録 | `mvv_checked` | 真偽値 | このラウンドの head で根拠の行の確かめが成功したか | ラウンドの記録を書くとき |
| 記録のコメントの一覧（`open_block` の 1 件） | `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity` | ジャーナルの指摘と同じ | 再開で分類・固定前の重さを戻すため | 記録のコメントを書くとき |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `enter`（分類） | `mvv: Value 5` の指摘が `mvv`・`judged_mvv` を持ち収束を止める | 同じ識別子の `Value 5` と `P1`・`P1` と `C7` を両方の到着順で入れても同じ分類と項目になる | 一覧に無い `mvv` → 外して注意 |
| `handable` | `P1` の指摘を渡さない・`Value 5` を渡す | 直した指摘・見送った指摘を渡さない | — |
| `skipped_texts` | 同じ識別子の見送りを返す | `skipped_mvv` が無く今回が `absent` → 返す | 識別子が違う見送り → 返さない |
| `enter` | 識別子の違う見送りが出し直される → 未解決に戻り `revived` に入り、後の起動で `fixed` にされない。前のバージョンの `P1` の指摘が新しいバージョンで `Value 5` として出し直される → レッドラインでなく `major`・`judged_mvv` は今回・`mvv_history` に前の `P1` | 前のバージョンで相手役の申告が `minor` で `mvv_pinned` により `major` に固定された（`unpinned_severity: minor`）`fixed` の `Value 5` の指摘が、`mvv` 無しの `minor` で出し直される → `minor`・分類なし。固定前も `major` だった（`unpinned_severity: major`、または固定していない `major`）前のバージョンの指摘が `minor` で出し直される → 重い方を残す規則で `major` のまま・分類なし・未解決なら収束を止める | — |
| `restate` | 出し直されない前のバージョンの `redline` の指摘 → 分類なし・履歴あり。`unpinned_severity` のある指摘 → 重さが戻る | 識別子が同じ指摘は変えない | — |
| `carry`・`recorded`・`carried` | 空の状態ディレクトリから、`Value 5`・`P1`・`by: ndf` の 3 件が分類つきで戻る。スレッドのある件にも重なる | 識別子の違う件は分類なしで戻る。元が `minor` で `mvv_pinned` により `major` に固定された指摘を、別の状態ディレクトリで新しいバージョンから再開 → `minor` に戻る。人が resolve した `by: ndf` のスレッドの指摘が、空の状態ディレクトリからの再開で未解決のまま戻り、確かめが失敗した起動でも未解決のまま。開いたスレッドの `by: ndf` の指摘は 1 件だけ戻る。開いたスレッドの最初のコメントが書き換わって `carried` が書式の検査で除く `by: ndf` の指摘も、記録のコメントから 1 件だけ戻り、確かめが失敗した起動で未解決のまま残る。状態ファイルが持つスレッドの件は足さない | 一覧の `mvv` が今回の項目の一覧に無い → 外して注意 |
| `open_block` | 3 つの分類と `unpinned_severity` を埋める | `minor` のレッドラインも載る。`by: ndf` の指摘にも照合キーが載らない | — |
| 検出から再開まで（`ingest_citations`・`open_block`・`recorded`・`carry`・`mvv_outputs`） | 根拠の行の誤りを検出 → `judged_mvv` が今回の `by: ndf` の指摘 → 記録のコメントの一覧に載る（照合キーは載らない）→ 空の状態ディレクトリから再開 → 同じ識別子で戻り、`outputs.mvv.citations` と MVV の記録に載る | 識別子を変えて再開 → 分類が外れ、次の確かめで今回の識別子に置き直される。秘密に見える文字列を `cited` に含む根拠の行の誤り（記録のコメントの `text` が伏せられる）を、一覧の経路とスレッドの経路のそれぞれから空の状態ディレクトリで再開して再検出 → 元の指摘が `fixed` になり、新しい指摘が 1 回だけ足される（誤りは落ちない。直しの決定 7 の代償） | — |
| `skipped_texts`（指示） | 見送り済みの `Value 5` の本文が一覧に載っても、指示がレッドラインの申告を許す | — | — |
| `apply_fix` | `deferred` に今回の識別子が付く | 前の識別子の見送りを今回また見送る → 今回の識別子に書き直す | — |
| `_blocking` | `redline: true` の指摘 → 偽 | `mvv_pinned` の指摘に `effective_severity` を当てても `major` のまま | — |

## 終わり方と結果

満たす決定: 決定 5・6、直しの決定 4・6・7、受け入れ条件 4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_gaps` | `finish_decide.py` | 変更 | 名前付きの引数 `mvv_checked: bool = True` を足す | `redline: true` の指摘を最初に除く（どの欠けにもしない）。残りのうち、`mvv` を持つ指摘か `by: ndf` の根拠の行の誤りの未解決を、`unresolved_finding` でなく `mvv_unresolved` の欠けにする。`mvv_checked` が偽なら、指摘の有無によらず `mvv_unresolved` の欠けを足す。PR に届いていないコミットの誤りの件数が 0 でなければ、件数だけを書いた `mvv_unresolved` の欠けを足す。`by: ndf` の指摘の欠けの理由は id と固定の文で書き、本文を載せない（結果 JSON に出すときは、ほかの欠け・判断・注意も `run_log.redact` を通す） |
| `finish` | `finish_decide.py` | 変更 | 確かめの成否は `Review.mvv_checked` で受け（`summary.json` に残し、`ndf cross-review finish` も読む）、`_finding_gaps` へ渡す | 欠けに `mvv_unresolved` があれば、状態を止める側（`STOPPED`・`cause.kind: mvv_unresolved`。終了コードは `work_stopped` と同じ。`contracts/causes/` に足す）にする。結果 JSON の最上位の `gaps` は `incomplete` だけが持てるので、欠けは `outputs.gaps` に載せる |
| `_summary` | `drive.py` | 変更 | （同じ） | 終わり方の判定の直前に根拠の行を確かめ、`mvv_outputs` と `record_review` を呼ぶ。下の手順 |
| 再利用の経路 | `drive.py` | 変更 | — | `read_by_all_head` がラウンドを返して `_summary(reused=True)` に入ったら、確かめ終えたとみなす（`read_by_all_head` は同じ識別子の `Basis` で `mvv_checked: true` のラウンドだけを返すので、その head の確かめは成功済み）。再利用の経路では `_citations` を打たない |
| `mvv_outputs` | `report_decide.py` | 新設 | `mvv_outputs(rows: Sequence[Mapping[str, Any]], snapshot: Snapshot, head: str, mvv: str, clean: Cleaner = _same) -> dict[str, Any]` | 結果 JSON の `outputs.mvv`（`version`・`sha256`・`head`・`findings`・`citations`・`redlines`・`disputed`）。`judged_mvv == mvv` の指摘だけを載せる。`disputed` の `reason`・`redlines` の `text`・`citations` の `path`・`cited`・`unknown` の各項目を 1 つの並びにして `clean` に 1 回で通し、出力にはそれぞれ伏せた後の値を置く。ジャーナルの `path`・`cited`・`unknown` は生のまま残し、修正担当への指示と照合キーはそれを使う。生のパスは結果 JSON に載せない |
| `record_review` | `records/mvv.py` | 新設 | `record_review(root: Path, pr: int, head: str, snapshot: Snapshot, rows: Sequence[Mapping[str, Any]], mvv: str, clean: Optional[Callable[[Sequence[str]], Sequence[str]]]) -> int` | `judged_mvv == mvv` の指摘ごとに `mvv-review` を、見送りごとに `review_disputed` を書き、書いた件数を返す（返り値を `ReviewWrite` に変えるのは #3959）。`reason`・`text`・`by: ndf` の指摘の `item`（`cited`）を書く前に 1 回で `clean` に通す |
| `record_key` | `records/mvv.py` | 新設 | `record_key(text: str) -> str` | 記録の `key`。ジャーナルの指摘の生の `text`（`by: ndf` の指摘はパスと引いた本文から作った `text`。ジャーナルの `key` と同じ元）に、先に `run_log.redact`（秘密の環境変数の値とトークンの形）を当て、その後で `oscillation.finding_key`（空白の圧縮・小文字化・切り詰め）を当てた文字列の SHA-256 の 16 進 64 文字を返す。伏せてから小文字化・切り詰めるので、大文字小文字の混ざった秘密も、切り詰めの境界にまたがる秘密も、ハッシュの元に入らない（元に入らないのでキーから秘密を戻すことも、候補を突き合わせることもできない）。参照先の公開範囲を照会せず、`Hub.clean` も通さないので、別々の非公開リポジトリへの参照だけが違う指摘は別のキーになり、参照先の公開範囲が変わっても同じ指摘のキーは変わらない |
| `once` | `report_decide.py` | 新設 | `once(clean: Cleaner) -> Once` | 1 回の `_summary` の中だけ使う `clean` の包み。呼ばれた値の並びのうち、まだ伏せていない値だけを 1 回で `clean` に通し、値ごとの結果を覚えて返す。`clean` が例外を送出したら、その呼び出しと以後のどの値にも固定の文 `WITHHELD`（「（参照先の公開範囲を確かめられず伏せた）」）を返し、`clean` を呼び直さない。欄 `failure` に最初の例外の原因を固定文で持つ（失敗が無ければ None）。原因は `gh.GitHubError` の構造化された欄だけから決め、例外の文（`str(e)`。GraphQL のサーバーが返したエラーメッセージを含み、照会先のリポジトリ名が入りうる）は使わない: `quota` があれば「レート制限」、`unreached` が真なら「通信の障害」、それ以外は「GitHub が照会を断った（HTTP <status>）」（`status` は整数）。`gh.GitHubError` 以外の例外は「照会の失敗（<例外の型名>）」とする。例外の文は結果 JSON にも `warnings` にも渡さない。覚えるのは `mvv_outputs` から `record_review` までで、公開の書き先へ投稿する経路（記録のコメント）には渡さない |
| `count_signals` | `records/mvv_decide.py` | 変更 | （同じ） | `review_disputed` を（repo・PR・指摘のキー）で 1 件にまとめ、`unknown` に足す |

- `_summary` の手順
  1. 承認済みの MVV があるか（`snap.state == "approved"`）を最初に判定する。承認済みで再利用でないときだけ、終わり方を判定する head で `_final_citations(run, head)` と `ingest_citations` を打つ。承認済みでない（`absent`・`unapproved`）ときは `_final_citations` も `_final_target` も呼ばない（最後の修正で head が変わっても `git merge-base` を打たない）
     - `ingest_citations` が真を返したとき（ラウンドの末尾の保存の後にジャーナルが変わったとき）だけ、`finish` の判定・要約・結果の返却より前に `_save(run)` を 1 回打つ。変わらなければ書かない（変わったジャーナルを状態ファイルへ残す。記録のコメントも状態ファイルから作るので、最後の修正で初めて見つかった指摘がコメントの一覧にも載る）
  2. `finish` に `mvv_checked`（承認済みの MVV が無いか、`run.checked_head` が終わり方を判定する head と同じ）を渡す
  3. `run.clean`（記録のコメントと同じ `hub.clean`）を `once` で包んだ値を 1 つ作り、`mvv_outputs`、`record_review` の順に識別子とともに渡す。`record_review` が伏せる値（`reason`・`text`・`cited`）は `mvv_outputs` が伏せる値に含まれるので、`record_review` は照会しない
  4. 包みの `failure` があれば、注意「MVV の結果の自由記述を伏せた（参照先の公開範囲を照会できない）: <原因>」を足して `outputs.mvv` は `WITHHELD` の値のまま返し、`record_review` には `clean=None` を渡して自由記述を持たない記録を書かせ、注意「MVV の記録（mvv-validation.jsonl）の自由記述を伏せて書いた（次の起動で書き直す。それまで設計承認ゲートは省かない）: <原因>」を足す。`record_review` が書けなかったら結果は変えず注意に載せる（`ReviewWrite.missing` と `mvv_record_failed` は #3959）。それ以外の終わり方と結果の返却は変えない
- 終わり方の対応: ジャーナルの指摘 → `mvv-review` の `outcome` → 結果の `outputs.mvv` は次のとおり（どれも `judged_mvv` が今回の識別子と同じものだけ）。`redline: true` の指摘 → `redline` → `redlines`（`text` を持つ）。`mvv_pinned` の指摘で `deferred` → `deferred` → `disputed`（`reason` を持つ）。`mvv_pinned` の指摘で `fixed`・`unresolved` → 同じ値 → `findings`。`by: ndf` の指摘 → `fixed`・`unresolved` → `citations`
- `mvv-review` の 1 件: `{"kind": "mvv-review", "repo", "pr", "head", "key", "id", "item", "outcome", "version", "sha256", "at"}` に、`deferred` は `reason`（修正担当の見送りの理由）、`redline` は `text`（指摘の本文）を足す。`redline` の `item` は `redline_items` を `・` でつないだ値（例 `P1・C7`）で、`outputs.mvv.redlines` の項目もこの値を使う（申告の項目を全部残す）。`key` は `record_key(指摘の text)` で、起動をまたいで同じ指摘に同じ値になり、`clean` に通さない（伏せ字は表示のためだけに使い、記録上の同一性に使わない）。`id` は表示のためだけに持つ
- `clean` が None（照会に失敗した起動）の記録: 照会せず、伏せる欄（`reason`・`text`・`by: ndf` の指摘の `item`）を置かずに `withheld: true` を置いた `mvv-review` を書く。`key`・`id`・MVV の項目の番号の `item`（`P1・C7`・`Value 5` など。自由記述でない）・終わり方・バージョン・`head` は普段どおり書き、`review_disputed` も普段どおり（自由記述の欄を持たない）書く。#3959 の `review_holds` は、キーの最後の記録が今のバージョンの `withheld: true` なら終わり方によらず保留にし、記録が復旧するまで設計承認ゲートを省かない（この規則と承認資料への `WITHHELD` の文は #3959 の実装が持つ）
- バージョンの照合: この節の「バージョン」は常に（`version`・`sha256`）の組である。`record_review` は `snapshot` の組を使い、`version` だけで比べない
- 重ねない判定: `drive._summary` が終わりに 1 回 `record_review` を呼ぶ。`head` は終わり方を判定した head。判定は 2 つのファイルで別々に打つ
  - `mvv-validation.jsonl`: 同じ（repo・PR・key）の最後の記録（下の「最後」の決め方）と（head・終わり方・バージョン・`item`・`reason`・`text`・`withheld`）がすべて同じときだけ書かない（持たない欄どうしは同じとみなす。`reason`・`text` は伏せた後の値で比べる）。履歴のどこかに同じ記録があるかでは決めない（同じ head で `deferred` → `fixed` → `deferred` と戻れば 3 件目も書く。同じ状態の再実行は重ねない。同じ head で後から `C7` が足された `redline` も、別の起動が見送り直して理由だけが変わった `deferred` も書く。結果の `outputs.mvv.disputed` と記録の最後の理由をそろえ、承認資料が古い理由を読まないためである）
  - 伏せた記録は、最後の記録が伏せていない記録で（head・終わり方・バージョン）が同じなら書かない（分かっている理由を伏せた記録で上書きしない）。伏せていない記録は、最後の記録が伏せた記録なら書く（記録の復旧）
  - `mvv-signals.jsonl`: （repo・PR・key・バージョン）が同じ `review_disputed` があれば書かない（伏せたかによらない）。片方だけ書けて止まった起動も、次の起動で欠けた方だけを書く
- `count_signals`: 今の版の行だけを読み（今どおり）、`review_disputed` を（repo・PR・key）で 1 件にする。id では重ねない
- `Signals` の形は変えない（`unknown` の数に含める）。`count_signals` の `pairs` に `review_disputed` を足す
- 「最後」の決め方: キーごとの行の順は `at` の古い順で、`at` が同じ（`jsonl.now()` は秒精度）ときは `mvv-validation.jsonl` の追記順に従う。`record_review` の重ねない判定もこの順の最後の行を使う

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_review` | 行を書けない | 保存先の権限 | `OSError` | 結果は変えず注意に載せる | 結果の `warnings` | 次の起動で書く（重ねない） |
| `finish` | `mvv_unresolved` で止まる | 上限のラウンドで MVV の指摘か根拠の行の誤りが残った、または最後の head で確かめられなかった | 欠けの種類・`mvv_checked` | 止まる | 終了コード `STOPPED`、`summary` に残った指摘と項目 | 直して `ndf cross-review run` を打ち直す |
| `_summary`（`once`） | 参照先の公開範囲を照会できない（通信の障害・レート制限） | `Publisher.guarded` の照会 | `gh.GitHubError`（`once` が受け、`_summary` へは出さない） | 終わり方は変えず、結果を返す。`outputs.mvv` の伏せる欄はすべて `WITHHELD`（生の値も、伏せる前の値の一部も出さない）。`record_review` は `clean=None` で呼び、自由記述を持たない `withheld: true` の `mvv-review` と、普段どおりの `review_disputed` を書く（`WITHHELD` の文を書くと重ねない判定と承認資料の理由が壊れ、何も書かないと #3959 の設計承認ゲートが保留なしと読んで `fast`・`auto` で省き、`count_signals` が見送りを数え落とすため） | 結果の `warnings` に 2 件（結果を伏せた・記録の自由記述を伏せて書いた）。`<原因>` は `once` の固定文で、例外の文を含めない。`redline`・`deferred` の記録を書けなければ #3959 のとおり `mvv_record_failed` | 照会が通る次の起動で書き直す。キーの最後の記録が伏せた記録の間は、`review_holds` がそのキーを保留にし、設計承認ゲートは省かない。同じ head の打ち直しで `_reusable` が真（失敗した起動の最後のラウンドが収束で、未解決の指摘も決めていない足すべき内容も無く、同じ比較基点・同じ識別子）なら、相手役を呼ばずに `_summary(reused=True)` に入り、`record_review` が伏せていない記録を書く。それ以外（ラウンドの上限で止まった未収束の起動・未解決の指摘が残る起動・head が変わった打ち直し）は再利用せず相手役を回し、`record_review` はその起動の終わりのジャーナルの指摘を書く。ジャーナルに残る指摘はどの終わり方でも同じ `key` で伏せていない記録が書かれ、保留が外れる（`fixed` なら `deferred` の保留も外れる）。`review_disputed` は失敗した起動で書いた 1 件のままで、次の起動で同じ指摘が見送られても直されても足さない。別の状態ディレクトリで打ち直してジャーナルに無い指摘は伏せた記録のまま残り、その PR の設計承認ゲートは人に問い続ける（#3959 の「余分な人の承認は許す」の側に倒す） |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `record_review` | 種類・PR・head・`record_key` の値・指摘の id・項目・終わり方・バージョン・sha256・repo・時刻。見送りの理由・レッドラインの指摘の本文・根拠の行の誤りの `item` は `clean` を通した値 | `mvv-validation.jsonl`・`mvv-signals.jsonl` | 伏せる前の自由記述・本文の断片を含む `key`（ジャーナルの `key`）・レッドラインでない指摘の本文（PR に残る）・`judged_mvv` が今回と違う指摘・`WITHHELD` の値（照会に失敗した起動では伏せる欄を置かず `withheld: true` を置く） |
| `mvv_outputs` | 指摘の id・項目・相手役・終わり方。見送りの理由・レッドラインの本文・`citations` のパス・`cited`・`unknown` は `clean` を通した値（照会に失敗した起動では `WITHHELD`） | 結果 JSON の `outputs.mvv` | `judged_mvv` が今回と違う指摘・伏せる前の自由記述・伏せる前の `citations` のパス・`cited`・`unknown` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_finding_gaps`・`finish` | MVV の未解決 → `mvv_unresolved` で止まる。普通の未解決だけ → 今どおり `incomplete`。`mvv_checked` が偽 → NDF の指摘が 0 件でも `mvv_unresolved` で止まる | MVV の指摘が全部直った → 今どおり。`redline: true` の未解決だけが残る → 欠け無し（収束）。MVV が無い → `mvv_checked` を真とみなし今どおり | — |
| 再利用の経路（`_start`・`_summary`） | 承認済みの MVV があり、同じ head・同じ識別子で `mvv_checked: true` の収束がある打ち直し → 相手役も `_citations` も打たず、`mvv_unresolved` にならず収束を返す | MVV が無い打ち直しは今どおり | `mvv_checked` が偽のラウンドしか無い → 再利用せずラウンドを回す |
| `record_review` | 見送り 1 件 → `review_disputed` 1 件と `mvv-review` 1 件（`reason`・`head` つき） | 同じ起動のやり直しで重ねない。別の起動で id が `r1-3` に変わった同じ指摘も重ねない。前のバージョンの記録があっても新しいバージョンの 2 件を書く。`mvv-review` だけがある状態から `review_disputed` だけを書き足す。同じ head・同じキーの `deferred` で理由だけを変えた再実行 → 新しい理由の `mvv-review` を 1 件足し、`review_disputed` は足さない | 書けない → 注意 |
| `record_review`・`mvv_outputs` | 偽の秘密を含む理由と本文が伏せられて書かれる。偽の秘密をファイル名に含む根拠の行の誤り → `outputs.mvv.citations` の `path` が伏せられ、`clean` の呼び出しは理由・本文と合わせて 1 回。ジャーナルの指摘の `path` は生のまま。偽の秘密を項目に置いた根拠の行（`- 根拠: <偽の秘密>（MVV バージョン 11）` の `unknown` と、`- 根拠: <偽の秘密>「…」（MVV 版 11）` の `format`）→ `outputs.mvv.citations` の `cited`・`unknown` と `mvv-validation.jsonl` の `item` に偽の秘密が無く、`format` の `cited` に引用文とバージョンの表記が無い。偽の秘密を本文の先頭に引いた相手役の MVV の指摘（見送りとレッドライン）→ `mvv-validation.jsonl` と `mvv-signals.jsonl` のどの欄にも偽の秘密が無く、`key` は 16 進 64 文字 | 伏せた後が同じ理由の打ち直しは重ねない。同じ指摘の打ち直し（id が変わる別の起動を含む）は `key` が同じで、両ファイルとも記録を足さず、`count_signals` も 1 件のまま | `judged_mvv` の違う指摘 → 書かない・載せない |
| `record_review`（`clean=None`） | 見送り・レッドライン・`by: ndf` の指摘 → `mvv-review` は `withheld: true` で `reason`・`text`・`cited` の `item` を持たず、`key`・`id`・項目の番号・終わり方・バージョン・`head` を持つ。`review_disputed` は普段どおり 1 件。`clean` は呼ばない | 伏せた記録の後に同じ（head・終わり方・バージョン）の伏せていない記録 → 書く。伏せていない記録の後に同じ（head・終わり方・バージョン）の伏せた記録 → 書かない。伏せた記録の後に同じ伏せた記録 → 書かない | — |
| `record_key` | 大文字小文字の混ざった偽の秘密（`AbCd…`。環境変数に置く）を含む本文 → キーに偽の秘密も、その小文字の形も無い。同じ本文の空白と大文字小文字だけを変えた本文 → 同じキー | 偽の秘密が `review.body-match` の切り詰めの幅をまたぐ位置にある本文 → キーにも記録のどの欄にも偽の秘密の前半・後半の断片が無く、打ち直しでキーが変わらない。公開の書き先で、別々の非公開リポジトリへの参照だけが違う 2 つの見送り → `clean` の後の理由は同じでもキーは 2 つで、`review_disputed` が 2 件。一方を `fixed`、もう一方を `deferred` にした打ち直し → `mvv-review` の最後の記録がキーごとに別で、`fixed` が他方の `deferred` を解除しない（#3959 の保留）。同じ指摘の参照先を公開から非公開へ変えて打ち直す → `clean` の後の理由は変わり `mvv-review` を 1 件足すが、キーは同じで `review_disputed` を足さない | — |
| 確かめから記録まで（`_citations`・`ingest_citations`・`_summary`） | 公開の書き先で、同じ公開の別リポジトリへの参照を `cited` と見送りの理由とレッドラインの本文に含む PR を 1 ラウンドで収束させる → 偽の GitHub の参照先の `visibilities` の呼び出しが、`_citations` と `ingest_citations` からは 0 回（照合キーを作る経路は照会しない）。`mvv_outputs` と `record_review` を合わせて 1 回で、`record_review` からは 0 回。記録のコメントの照会は `Hub.clean` の今の振る舞いのまま（使い回しは #4156） | 確かめを 2 回（ラウンドと終わり方の判定の直前の新しい head）打っても、確かめの経路の照会は 0 回のまま。再利用の経路（記録のコメントを投稿し直さない）では、`mvv_outputs` と `record_review` を合わせて 1 回 | 偽の `visibilities` が `gh.GitHubError` を送出する起動で、見送り 1 件・レッドライン 1 件・根拠の行の誤り 1 件を収束させる → 結果が返り、`outputs.mvv` の理由・本文・`citations` のパス・`cited`・`unknown` が `WITHHELD` で生の値が無く、注意が 2 件、`visibilities` の呼び出しは 1 回だけ。`mvv-validation.jsonl` に `withheld: true` の記録が 3 件（`reason`・`text`・`by: ndf` の `item` の欄が無く、`WITHHELD` の文も無い）、`mvv-signals.jsonl` に `review_disputed` が 1 件。続けて照会が通る偽で同じ head を打ち直す → 再利用の経路で `mvv-validation.jsonl` に伏せていない記録が 3 件足され、`mvv-signals.jsonl` は増えない。照会に失敗した起動で見送った指摘を、次の起動（head が変わる）で修正担当が直す → `mvv-review` の `fixed` が足され、`review_disputed` は増えず、`count_signals` の `unknown` はその指摘で 1 のまま。照会に失敗した起動を 2 回続ける → 2 回目は両 JSONL に記録を足さない。伏せていない記録の後に照会に失敗した起動を同じ head・同じ状態で打つ → 伏せた記録を足さない。サーバーのエラーメッセージに偽の非公開リポジトリ名を含む `gh.GitHubError`（`quota` あり・`unreached` 真・どちらでもない）→ `warnings` の原因がそれぞれ「レート制限」「通信の障害」「GitHub が照会を断った（HTTP <status>）」で、結果 JSON のどこにもリポジトリ名が無い。ラウンドの上限で未収束のまま照会に失敗した起動を同じ head で打ち直す → 再利用せず相手役を回し、両 JSONL にはその起動の終わりの記録だけが足される |
| `count_signals` | `review_disputed` 2 件（別のキー）→ `unknown` が 2 増える | 同じ（repo・PR・key）の 2 行は id が違っても 1 件。同じ id で別のキーは 2 件 | 前の版の行は数えない |

- `review_holds`・`ReviewWrite.missing`・設計承認ゲートの `_judged` を通す観点（伏せた記録の間は省かない・復旧で保留が外れる）は、#3959 の実装が縛る

## 手順書

満たす決定: 決定 2・7、受け入れ条件 1・6

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/project-mvv.md` | 節「工程での読み方」を足す（どの工程も写しの全体を読み、その工程の出力が触れる項目を本文から選んで判断と根拠の行に使う・`current` の結果の欄・人へ戻す形・「MVV なし」）。番号で読む範囲を固定しない。根拠の行の形は `decisions.md` へリンク |
| `skills/development-workflow/references/agent-layers.md` | conductor → supervisor と supervisor → worker の起動指示の項目に「MVV」を足し、守る規則に「MVV の欄の写しを作業の前に読む」を足す |
| `skills/design/SKILL.md`・`skills/design/references/decisions.md`・`skills/tdd-cycle/SKILL.md`・`skills/requirements-design/SKILL.md` | 参照を `project-mvv.md#工程での読み方` へ直す。`decisions.md` の例のバージョンを「`current` のバージョンを書く」形にし、「根拠の行」の表に `・` の区切りと引用文の形を足す |
| `skills/cross-refactoring/SKILL.md` | `$RF_MVV` を `current` の結果の 1 行と写しを読む指示で埋める手順 |
| `skills/cross-review/SKILL.md`・`skills/fix/SKILL.md` | 基準 5・`mvv_unresolved`・`outputs.mvv` の説明。`fix` の「指摘の基準」の表に基準 5 の写し |
| `docs/contracts/commands.md` | `ndf records mvv current` の行 |
| `contracts/schemas/result.schema.json`・`contracts/version.json` | 結果 JSON の `outputs` のキーに `mvv` を足し、コントラクトのバージョンを 1 つ上げる（`outputs` は決めたキーだけを許す） |
| `docs/glossary/glossary.json` | 語「MVV のスナップショット」「根拠の行の確かめ」「MVV に反する指摘」を追加し、`ndf records glossary render` で `docs/glossary.md` を作り直す |
