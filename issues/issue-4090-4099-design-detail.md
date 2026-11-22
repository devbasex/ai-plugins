# #4090 #4099 の詳細設計: MVV の写し・根拠の行の確かめ・ジャーナルの置き換え

設計文書 [issue-4090-4099-design.md](./issue-4090-4099-design.md) の決定を、#4076 の詳細設計（[issue-4076-design-detail.md](./issue-4076-design-detail.md)）に対する変更として、ファイルと関数の粒度へ下ろした文書である。ここに書かない関数と規則は #4076 の詳細設計のままである。前提のコードは #4076 と同じく、スプリントブランチ `sprint/p12-lcrfast-r1` が main へ入った後の形である。

## 地図

#4076 の詳細設計の地図に足す・変える行だけを書く。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | `adapters.markdown`（足す） | モジュール | `body_path`（新設）・`parse_citation`（新設）・`decision_lines`（新設）・`fenced_lines`（#4076 の新設を置き換え）・`citation_errors`（#4076 の新設を置き換え） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の設定・本文・記録の読み書き | `git`・`mvv_decide` | `Snapshot`（欄を足す）・`MvvMismatch`（新設） | `current`（置き換え）・`head_approved`（新設）・`_write_verified`（新設）・`record_review`（引数を足す） |
| `src/ndf/domains/records/preflight.py` | 変更 | 手順 0 の確かめ | `mvv_decide` | モジュール | `mvv_body`（`body_path` を呼ぶ。返す値は同じ） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv`・`git` | モジュール | `_mvv_current`（取得を置き換え・`--allow-mismatch` を足す） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv` | `RunConfig`・`_Run`・`_Prepared`（欄を足す）・`_Target`（新設） | `_start`・`_prepare_round`・`_merge_base`・`_committed_texts`・`_citations`（置き換え）・`_final_target`・`_final_citations`・`_review_diff`・`_diff_files`・`_base_lines`（新設）・`_settle_resolved`・`_summary`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal` | `ingest`・`enter`・`carry`・`ingest_citations`（変更）・`restate`（新設） |
| `src/ndf/domains/crossreview/prior_decide.py` | 変更 | 前の実行から引き継ぐ指摘 | `report_decide` | `Carried`（欄を足す） | `carried`・`recorded`（変更） |
| `src/ndf/domains/crossreview/report_decide.py` | 変更 | 記録のコメントと結果 JSON | — | モジュール | `open_block`（変更）・`mvv_outputs`（引数を足す） |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | — | モジュール | `_finding_gaps`・`finish`（引数を足す） |
| `src/ndf/domains/crossreview/scope_decide.py` | 変更 | 差分の範囲と再利用の条件 | — | モジュール | `read_by_all_head`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` の入口 | `records.mvv` | モジュール | `cmd_scope`（ベースのコミットと共通原則の sha256 を求める） |

依存の向きは #4076 のままで、`mvv_decide` が `adapters.markdown`（git もファイルも読まない純粋な解析）を読むことだけが増える。循環は無い。

## MVV の写し

満たす決定: 決定 1・9・10・11・13・15（#4076 の詳細設計の「MVV の写し（`current`）」の節を置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Snapshot` | `records/mvv.py` | 変更 | #4076 の欄に `commit: str`・`principles_sha256: str`・`principles_path: Optional[Path]` を足す | `commit` は手順 (1) で解いたコミット（状態によらず置く）。`principles_*` は共通原則の sha256 と写し（承認済みのときだけ置き、それ以外は `""` と None） |
| `body_path` | `records/mvv_decide.py` | 新設 | `body_path(decl: Mapping[str, Any]) -> str` | 設定の最上位の `body`（文字列でなければ表 `ndf-files` の `mvv-body` の既定）を `.ndf/` からの相対で解き、リポジトリの根からの POSIX のパスを返す。絶対パス・`..` を含む値・空は `MvvError` |
| `current` | `records/mvv.py` | 変更 | `current(root: Path, ref: str, allow_mismatch: bool = False) -> Snapshot` | 下の手順 |
| `MvvMismatch` | `records/mvv.py` | 新設 | `class MvvMismatch(MvvError)`。欄 `snapshot: Snapshot` | `mismatch` のときに `current` が投げる。`snapshot` は `allow_mismatch=True` で返す値と同じ（状態 `mismatch`・`commit` は解いたコミット・項目なし・写しなし）。呼び手はこれを使い、`current` を打ち直さない |
| `head_approved` | `records/mvv.py` | 新設 | `head_approved(root: Path, commit: str) -> bool` | `commit` のツリーの設定と本文が承認の記録と一致するか（`mvv_decide.status` が `approved`）。設定を `git show <commit>:.ndf/mvv.json`（`run_bytes`）で読み、`decl_from` で検査し、`body_path(decl)` の本文を `git show` で読む（Git は 2 回。設定が無い・設定か本文を読めない・検査に通らない・状態が `approved` でないは偽）。写しを書かない |
| `_write_verified` | `records/mvv.py` | 新設 | `_write_verified(path: Path, data: bytes, sha256: str) -> Path` | `path` が無いか、中身の SHA-256 が `sha256` と違えば、`data` を同じディレクトリの一時ファイルへ書いて `os.replace` で置き換える。同じなら書かない |
| `_mvv_current` | `records/cli.py` | 変更 | （同じ）。引数 `--allow-mismatch` を足す | `git.Git(root).fetch_branch_or_raise(<リモート>, <ブランチ>)` を打ってから `current(root, <リモート>/<ブランチ>, allow_mismatch=<引数>)` を呼ぶ |

- `current` の手順（#4076 の手順を置き換える）
  1. `git rev-parse --verify <ref>^{commit}` でコミットへ解く。失敗は `MvvError`（`precondition`）
  2. `git ls-tree -z <コミット> -- .ndf/mvv.json` を打つ。成功して出力が空のとき（ツリーに設定のエントリが無いと確かめられたとき）だけ `absent`（`commit` は置き、共通原則は読まない）。`ls-tree` の失敗、エントリが通常のファイルの blob でない（`tree`・`commit`・シンボリックリンク）は `MvvError`（`precondition`）
  3. 設定を `ls-tree` で得た blob の名前で `run_bytes` で読み、`decl_from` で検査する。`body_path(decl)` で本文のパスを決め、本文を `run_bytes` で読む。設定の blob の読み取りの失敗（blob の欠損を含む）・本文が読めないときは `MvvError`（`precondition`）。読めなかったことを `absent` に落とさない
  4. `mvv_decide.status` で状態を決める。`mismatch` は、`allow_mismatch` が偽なら `MvvMismatch`（「ベースの MVV の本文が承認の記録と合わない: <本文のパス>」）。真なら状態 `mismatch`・項目なし・写しを書かずに返す（基準 5 は外れる）。`unapproved` はそのまま返す
  5. 承認済みなら、本文の写しと共通原則の写し（`mvv.principles` のバイト列から sha256 を求め、`principles-<先頭 12 文字>.md`）を `_write_verified` で書き、`items_of` で項目の一覧を作る。`blobs` の鍵は設定のパスと `body_path` のパス
  - 副作用: `git rev-parse`・`git ls-tree` を 1 回ずつ、`git show`（`run_bytes`）を 2 回（`absent` では 0 回）。写しのファイルを 2 つまで読み、違えば書く
- `drive._start`（#4076 の手順を置き換える）: `remote = g.remote()`、`sync_worktree(g, cfg.branch, remote, base_branch=cfg.base)` の後に `snap = current(root, f"{remote}/{cfg.base}")` を呼び、`cfg = cfg._replace(base_commit=snap.commit)` とする。`MvvMismatch` を受けたときだけ、`head_approved(root, "HEAD")`（ベースを取り込んだ後の PR の head。Git 2 回）が真なら、MVV を直す PR として `snap = e.snapshot` で続け（`current` もベースの解き直しも打たない）、結果の `warnings` に「ベースの MVV が承認の記録と合わないため、MVV を直す PR として基準 5 を外して続けた」を載せる。偽なら #4076 のとおり `refused` で止まる（`summary` に「PR を取り込んだ後も MVV の本文が承認の記録と合わない」と本文のパス）。ファイル名で判定しないので、壊れた本文への追記や `.ndf/mvv.json` の無関係な編集では続かない。ベースが `mismatch` で head が承認と一致するなら PR は設定か本文を必ず変えているので、変わったファイルは読まない。続けたときは `run.recovery_head` に確かめた head のコミットを置く。`RunConfig` に `base_commit: Optional[str] = None` を足す。以後の `_changed(run.g, cfg.base_commit, …)`・`_merge_base`（`run.g.merge_base(cfg.base_commit, head)`）・差分（`f"{scope.base or merge_base}...HEAD"`）は名前を Git に渡さない
- `cmd_scope`（`review-scope`）: 手元の `<リモート>/<ベース>` を `git rev-parse` で 1 回コミットに解き、そのコミットで `git ls-tree` を打つ。本文のパスは `ls-tree` で得た設定の blob を `git cat-file` で読み `body_path` で解く（設定の blob が最後のラウンドの記録と同じなら、記録のパスを使い読まない）。共通原則の sha256 は、承認済みのときだけプラグインのファイルから求める（識別子の規則は「基準 5 と識別子」）。ネットワークに出ず写しを書かない（#4076 のまま）

### 異常系の処理

#4076 の表の行を次のとおり置き換える・足す。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `current` | 設定の有無を確かめられない | `git ls-tree` の失敗・設定のエントリが通常のファイルでない・設定の blob の欠損 | `ls-tree` の終了コードと出力・`git show` の失敗 | `MvvError`（`absent` にしない） | `refused`（`precondition`）、`summary` に `.ndf/mvv.json` とコミット | 直して打ち直す |
| `current` | 設定はあるが本文を読めない | 設定の `body` のパスに本文が無い・`git show` の失敗 | `git show` の失敗 | `MvvError` | `refused`（`precondition`）、`summary` に `body_path` のパス | 直して打ち直す |
| `body_path` | パスが不正 | 絶対パス・`..`・空 | 値の検査 | `MvvError` | `refused`（`precondition`）、`summary` に値 | 設定を直して打ち直す |
| `current` | 本文の sha256 が合わない | 承認の後に本文が変わった | sha256 の比較 | `MvvMismatch`（`allow_mismatch` が偽のとき。`mismatch` を状態として返さない） | `ndf records mvv current`・`ndf cross-review run` とも `refused`（`precondition`）、`summary` に「ベースの MVV の本文が承認の記録と合わない」とパス | 本文を戻すか承認し直す PR を作る。その PR の cross-review は、PR を取り込んだ後の head の設定と本文が承認と一致する（`head_approved`）ので MVV なしとして続く（警告つき）。直す工程の起動指示は `ndf records mvv current --allow-mismatch`（`ok`・`state: mismatch`・警告つき）で写す。それ以外の cross-review は相手役を起動せず、状態ファイルとジャーナルを書かない |
| `_summary`（直す PR で続けたとき） | 最後の head で MVV が承認と合わない | 修正担当の push が本文か設定を壊した・確かめの Git の失敗 | `head_approved` が偽 | `mvv_checked` を偽にする | `mvv_unresolved`（NDF の指摘が 0 件でも）、`warnings` に「PR の head の MVV が承認の記録と合わない」 | PR を直して打ち直す |
| `_write_verified` | 写しを書けない | 権限・容量 | `OSError` | 呼び出し元へ返す | `refused`（`precondition`） | 打ち直せる |
| `_mvv_current` | 取得に失敗した | ネットワーク・リモートの名前 | `fetch_branch_or_raise` の `GitError` | 古い参照を読まずに止める | `refused`（`precondition`）、`summary` に取得の誤り | 打ち直せる |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 複数のレーンの `current` | 本文と共通原則の写し | 一時ファイルへ書いて `os.replace`（中身は名前と検証済みのバイト列だけで決まるので後勝ちでよい） | 違う中身のファイルがあっても次の `current` で検証済みの中身に戻る |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `body_path` | 既定（キー無し）→ `.ndf/mvv.md`。`"docs/x.md"` → `.ndf/docs/x.md` | 文字列でない `body` → 既定 | 絶対パス・`../x.md`・空 → `MvvError` |
| `current` | 既定外のパスで承認したリポジトリ → 承認済み・`blobs` にそのパス。`commit` が解いたコミット | 同じ中身の写しは書き直さない。違う中身の写しは検証済みの中身になる。ツリーに `.ndf/mvv.json` が無い → `absent` | `mismatch` → `MvvMismatch`（`allow_mismatch=True` なら状態 `mismatch`・写しなし）。`unapproved` → 状態を返す。設定の blob を欠損させたコミット・`ls-tree` の失敗 → `MvvError`（`absent` にならない） |
| `_mvv_current` | `--single-branch` で別のブランチを clone した作業場所で新しい版を返す | `--allow-mismatch` で `mismatch` のベース → `ok`・`state: mismatch`・警告 | 取得の失敗 → `refused` |
| `drive._start` | ローカルの `main` が古くても、merge-base・差分・`Basis.base` がリモートのコミットから作られる | `mismatch` のベースで、PR を取り込んだ後の本文と設定が承認と一致する → 基準 5 なしで続き、警告が載る。このときの Git の起動は `current` の 4 回と `head_approved` の 2 回だけ（`changed_files` と `current` の打ち直しが無い）。最後のラウンドの後の push で本文を壊した → `mvv_unresolved` | `MvvError` → 相手役を起動しない。`mismatch` のベースで、PR が本文も設定も変えない・壊れた本文へ追記した・`.ndf/mvv.json` の無関係なキーだけを変えた → 相手役を起動しない |

## 基準 5 と識別子

満たす決定: 決定 11・15（#4076 の詳細設計の「指摘の基準 5」の節と「再利用の条件に MVV を足す」の表を置き換える）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_lines` | `criteria_decide.py` | 変更 | （#4076 と同じ） | `mvv.state == "approved"` のときだけ基準 5 を足す。`mismatch` は `current` が止めるので、ここには来ない |
| `mvv_criterion` | `review-criteria.json` | 変更 | 文 | 「プロジェクト MVV（版 {version}。本文は {path}）の Mission・Vision・Value の文に反する。必ず人の承認が要る操作は、本文の表と NDF の共通原則（{principles}）の C1〜C8 で決める」 |
| `Basis.mvv` | `scope_decide.py` | 変更 | 文字列 | 識別子。承認済みは `"approved:<version>:<sha256>:<共通原則の sha256>"`。承認済みでない（`absent`・`unapproved`・直す PR で続けた `mismatch`）は `"<state>:::"` で、共通原則の sha256 を含めない（基準 5 が無く共通原則を渡さないので、NDF の版上げで共通原則が変わっても前の収束と詳細レビュー済みを再利用する） |
| `cmd_scope` | `crossreview/cli.py` | 変更 | （同じ） | 識別子を上と同じ規則で作る。共通原則の sha256 をプラグインのファイルから求めるのは、ベースが承認済みのときだけ |

- 識別子の無い前の記録は、今回が `absent` のときだけ同じとみなす（#4076 のまま）
- 壊し方（テストで落とす）: 承認済みのベースで、共通原則の本文を変えた後の起動が前の収束を返す。基準 5 の指示に共通原則の写しのパスが無い。`absent` のベースで、共通原則の本文を変えた後の起動が前の収束を再利用しない

## 根拠の行の確かめ

満たす決定: 決定 2・3・4・8・12・14（#4076 の詳細設計の「根拠の行の確かめ」の節を置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `fenced_lines` | `records/mvv_decide.py` | 置き換え | `fenced_lines(text: str) -> frozenset[int]` | `adapters.markdown.code_lines(text)` が真の行の番号（1 始まり）を返す。自前の走査をしない |
| `decision_lines` | `records/mvv_decide.py` | 新設 | `decision_lines(text: str) -> frozenset[int]` | `adapters.markdown.atx_headings(text)` で、`### 決定 <数>` の見出しの次の行から次の見出しの前までの行の番号（1 始まり）を返す。cross-review の `_citations` と #3959 の `cite` の両方が、決定の根拠の行かをこの関数だけで決める |
| `parse_citation` | `records/mvv_decide.py` | 新設 | `parse_citation(line: str, decision: bool) -> Optional[Citation]` | 1 行を候補か決め、候補なら（項目の並び・版か None）を返す。候補でなければ None |
| `citation_errors` | `records/mvv_decide.py` | 置き換え | `citation_errors(lines: Sequence[tuple[str, int, str, bool]], catalog: Mapping[int, Sequence[str]]) -> list[dict[str, Any]]` | 確かめる行（パス・行の番号・本文・決定の根拠の行か）を `parse_citation` に通し、誤りを返す。4 番目の要素は呼び手が `decision_lines` で決めた値で、既定値を持たない |
| `_Target` | `crossreview/drive.py` | 新設 | `NamedTuple`。欄 `merge_base: str`・`head: str`・`docs: Mapping[str, str]`・`full_diff: Optional[Path]` | 確かめる対象。`_prepare_round` はラウンドの `_Prepared` から作り（`docs` は `_changed` が読んだ head の Markdown の本文、`full_diff` は範囲が `FULL` で起点が merge-base のときだけ書いた差分のファイル）、終わり方の判定の直前は `_final_target` が作る |
| `_final_target` | `crossreview/drive.py` | 新設 | `_final_target(run: _Run, head: str) -> _Target` | 終わり方を判定する head の対象。`run.last_target` があり、その `head` が `head` と同じならその `_Target` を返す（Git を打たない）。`run.last_target` が無い（ラウンドの対象を作れなかった）か head が違えば（最後のラウンドで修正を push した後）`run.g.merge_base(cfg.base_commit, head)` を 1 回打ち、`docs={}`・`full_diff=None` の `_Target` を返す。`merge_base` の `GitError` はそのまま投げ、受けるのは `_final_citations` |
| `_final_citations` | `crossreview/drive.py` | 新設 | `_final_citations(run: _Run, head: str) -> Optional[list[dict[str, Any]]]` | 終わり方の判定の直前の確かめ。`_final_target` と `_citations` を同じ `try` の中で打ち、どちらの `GitError`・`OSError`・`UnicodeDecodeError` も None にして注意に載せる（`run.citations` と `checked_head` は変えない）。`_summary` はこの関数だけを呼ぶ |
| `_citations` | `crossreview/drive.py` | 置き換え | `_citations(run: _Run, target: _Target) -> Optional[list[dict[str, Any]]]` | 下の手順。確かめられなければ None。`_prepare_round` は `_Target` を作った直後に、確かめの成否によらず `run.last_target = <ラウンドの _Target>` を置いてから `_citations(run, run.last_target)` を呼ぶ。`_summary` の前は `_final_citations(run, head)` を呼ぶ。`_prepare_round` を通し直さない |
| `_committed_texts` | `crossreview/drive.py` | 変更 | `_committed_texts(g: git.Git, commit: str, paths: Sequence[str]) -> dict[str, str]` | `commit` のツリーの `paths` の本文を、何件あっても Git を 1 回だけ起動して読む（今の `HEAD` 固定をやめ、コミットを引数に取る）。既存の呼び手（`_changed`）は `"HEAD"` を渡す |
| `_review_diff` | `crossreview/drive.py` | 新設 | `_review_diff(g: git.Git, base: str, head: str) -> str` | `git -c core.quotePath=false -c diff.noprefix=false -c diff.mnemonicPrefix=false -c diff.suppressBlankEmpty=false diff --no-color --no-ext-diff --no-textconv --no-relative --src-prefix=a/ --dst-prefix=b/ --unified=3 --find-renames <base>...<head>` を 1 回打つ。利用者の設定（`diff.noprefix`・`diff.mnemonicPrefix`・`diff.relative`・`diff.context`・`diff.suppressBlankEmpty`・`diff.external`・属性の `textconv`）で接頭辞・パス・hunk の行の形・本文が変わらないよう、機械で読む形をコマンドの側で固定する（`--no-textconv` で、hunk の行がコミットの blob の行と番号ごと対応する）。相手役に渡す差分のファイル（`_prepare_round`）と `_citations` の全差分の両方がこの関数で作るので、`full_diff` を読み直しに使える |
| `_diff_files` | `crossreview/drive.py` | 新設 | `_diff_files(text: str) -> list[DiffFile]` | `_review_diff` の出力を、ファイルごとの（新しいパス・元のパス・hunk の並び・足した行の番号・消した行の本文と足した行の本文）に分ける。新しいパスは `+++ b/<パス>`、改名は `rename from <パス>` と `rename to <パス>` の行から読む。`core.quotePath=false` でも Git が引用する（`"` で始まる）パスは C の書き方の引用を解いて読む。`+++ /dev/null`（消したファイル）は飛ばす |
| `_base_lines` | `crossreview/drive.py` | 新設 | `_base_lines(hunks: Sequence[Hunk], head_line: int) -> Optional[int]` | head の変わっていない行の番号を、hunk の行数の差からベースの行の番号へ写す。足した行なら None |
| `_Prepared` | `crossreview/drive.py` | 変更 | 欄 `docs: Mapping[str, str]` と `full_diff: Optional[Path]` を足す（意味は `_Target` と同じ） | — |
| `_settle_resolved` | `crossreview/drive.py` | 変更 | （同じ） | `row.get("by") == "ndf"` の行を飛ばす |
| `FindingJournal.ingest_citations` | `journal.py` | 変更 | `ingest_citations(self, errors: Optional[Sequence[Mapping[str, Any]]], mvv: str)` | 下の規則 |

- `parse_citation`
  - 候補: 左から空白と `- ` を除いた先頭が `根拠:` で、`MVV` を含むか、`decision` が真
  - 解析: `根拠:` の後ろから、`「` と対応する `」` の間（入れ子を数える。閉じなければ行末まで）を除く。残りで最初の `（MVV` を探し、そこから最初の `）` までを版の表記として、全体が `（MVV バージョン (\d+)(・スプリント MVV [0-9a-f]{8})?）` に一致するときだけ版を読む。閉じの `）` の後ろ（`。STD1` など）は読まない。`（MVV` の前を ` / ` と `・` で分けて前後の空白を除いた値を項目にする。`スプリント` で始まる項目と `根拠なし` は照らさない（#4076 のまま）
  - 版を読めない候補は版を None で返す（`（MVV 版 11）`・`（MVV バージョン 11x）`・閉じの `）` の欠け・版の欠け・`（MVV なし）` を含む）
- `citation_errors` の返す 1 件: `{"path", "line", "cited", "kind", "version", "unknown"}`。`kind` は `format`（版が None。`version` は None・`unknown` は空）・`version`（版が `catalog` に無い）・`unknown`（版はあり、その版の一覧に無い項目がある）。`catalog` は #4076 のまま `{ベースの承認済みの版: Snapshot.items}` の 1 件
- `_citations` の手順
  1. 鍵 `(target.merge_base, target.head, 識別子)` が `run.citations` にあれば、その結果を返す（Git を打たない）
  2. 全差分: `target.full_diff` があればそのファイルを読み、無ければ `_review_diff(run.g, target.merge_base, target.head)` を 1 回打つ。`_diff_files` で分ける
  3. 確かめる候補のファイルは次の 2 つだけとし、ほかのファイルの本文は読まない。足した行を持つだけのファイル（生成したソース・lockfile の 1 行の変更など）は候補にしない
     - (a) を調べるファイル: 足した行のどれかが `parse_citation` の候補の前置き（左から空白と `- ` を除いた先頭が `根拠:`）に当たるファイル。決定の根拠の行かは本文が要るので、ここでは `MVV` の有無も見出しも見ない
     - (b) を調べるファイル: hunk の消した行か足した行に、引用の `>` とリストの印を除いた行頭が 3 字以上の `` ` `` か `~` の行を持つファイル
     - (a) を調べるファイルで確かめるのは、その前置きに当たる足した行だけである。(a) にも (b) にも当たらないラウンドは、全差分を読んだ後に Git を打たず空の誤りを返す
  4. head の本文: 候補のファイルのうち `target.docs` にあるものを使い、無いもの（Markdown でないファイル・`_final_target` の新しい head）は `_committed_texts(run.g, target.head, <新しいパス>)` で 1 回にまとめて読む
  5. 各候補のファイルで `fenced_lines` の外にある候補の行のうち、(a) 足した行を集める。(b) を調べるファイルに、足していない候補の行があるときだけ、そのファイル群のベースの本文を `_committed_texts(run.g, target.merge_base, <元のパス>)`（改名は `rename from` のパス）で 1 回にまとめて読み、`_base_lines` で写したベースの行がベースの `fenced_lines` に入る行だけを (b) として残す
  6. 決定の根拠の行かは head の本文の `decision_lines` で決める
  7. `citation_errors` を当て、成功した結果を `run.citations` に置き、`run.checked_head = target.head` とする（`run.last_target` はここで置かない。`_prepare_round` が確かめの前に置く）
  - `GitError`・`OSError`・`UnicodeDecodeError` は None を返して注意に載せる。`run.citations` と `checked_head` は変えない
- `_citations` 1 回の Git の起動の数（決定 3・14。`_changed` と相手役の差分の読み取りは数えない）

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
  - `errors` が None なら何も変えない（既存の `by: ndf` の行を解決させない。#4076 のまま）
  - 誤りのキーは `citation_key`（`[path, cited, kind]` を区切りの曖昧さの無い JSON にした UTF-8 の SHA-256 の 16 進）で、`by: ndf` の行を作るときに行の `citation_key` に書く。照合はこの欄だけで行い、表示用の `text` や `oscillation.finding_key(text)` を使わない（`text` は記録のコメントで伏せ字が入り得るので、再開の後に同じ誤りから同じ値を作れない）。`citation_key` の無い `by: ndf` の行（この版より前の記録から戻した行）はどの誤りにも当たらないので `fixed` になり、同じ誤りは新しい行として 1 回だけ足される
  - 誤りのキー（`citation_key`）に当たる既存の `by: ndf` の行は未解決のまま `judged_mvv = mvv` に置き直す。当たる行が無い誤りは新しい `by: ndf` の行（`major`・`judged_mvv = mvv`）として足す
  - 未解決の `by: ndf` の行で誤りのキーに当たらないものは、`judged_mvv` によらず `fixed`（`judged_mvv = mvv`）にする。`by: ndf` の行を解決するのはこの経路だけである（決定 12）
  - 呼び手は `_prepare_round` と `_summary` の前の確かめで、`run.mvv`（今回の識別子）を渡す
- ラウンドの記録: 書くときに `mvv_checked = (承認済みの MVV が無い) or (run.checked_head == そのラウンドの head)` を足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_citations` | 差分か本文を読めない | `git diff`・`git grep`・`git merge-base` の失敗 | `GitError`・`OSError` | None。使い回しに置かず、`checked_head` を変えない | 結果の `warnings`。最後の head で起きれば `mvv_unresolved` で止まる（NDF の指摘が 0 件でも） | 次のラウンドか打ち直しで読み直す |
| `parse_citation` | 版を読めない | 書式の外れ・版の欠け・閉じの `）` の欠け・版の数字の後ろの余分な文字 | 正規表現の不一致 | 候補なら `format` の誤り。候補でなければ拾わない | NDF の指摘（`major`） | 直せば次の確かめで消える |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fenced_lines` | 囲みの間の行とフェンスの行 | 4 字で開き 3 字の行を含む例・リストの中のフェンス・チルダ・閉じないフェンス | — |
| `decision_lines` | `### 決定 1` の下の行から次の見出しの前まで | 文書の末尾の決定・`#### ` の小見出しで終わる | 決定の見出しが無い → 空 |
| `parse_citation` | ` / ` の 2 項目・`・` と引用文の 2 項目・`（MVV バージョン 11・スプリント MVV 3f9a1c2e）` | 引用文の中の `（` と ` / `・閉じない `「`・版の括弧の後ろの `。STD1` | `（MVV 版 11）`・`（MVV バージョン 11x）`・閉じの `）` を消した行・版の欠けの決定の根拠の行・`（MVV なし）` → 版 None。決定の外の `根拠: 実測` → 候補でない |
| `citation_errors` | 4 要素の行で決定の根拠の行の版の欠けを `format` にする | — | 3 要素の行 → `TypeError`（既定値で決定の外として通さない） |
| `_diff_files` | 新しいパス・足した行・hunk | 日本語を含むパス（引用されない）・`"` を含むパス（引用を解く）・改名（元のパスが `rename from`）・消したファイルは飛ばす | — |
| `_review_diff` | `+++ b/<パス>` と `@@ -a,b +c,d @@` の形で出る | 利用者の設定 `diff.noprefix=true`・`diff.mnemonicPrefix=true`・`diff.context=0`・`diff.suppressBlankEmpty=true` と `textconv` の属性を置いたリポジトリでも、同じ差分が出て `_citations` が根拠の行の誤りを拾う | — |
| `_base_lines` | hunk の前後の行を写す | ファイルの先頭と末尾の hunk | 足した行 → None |
| `_citations` | 足した行と、フェンスの行だけを消して本文に出た行を拾う。承認済みの MVV がある PR で、Markdown でないファイル（lockfile など）の `根拠:` を含まない 1 行だけを変えた → head の本文を読まない（全差分の後の Git が 0 回）。日本語のパスの文書と改名した文書でも拾う | ベースから本文の変わらない行を拾わない。ベースの本文は `merge_base` のコミットと元のパスで読む（head の本文を読まない）。同じ鍵の 2 回目は Git を打たない。範囲が狭まったラウンドでは全差分を 1 回打つ。上の表の場合ごとの起動の数（0 回の 2 つの場合と、両側を読む場合の上限 3） | 失敗 → None、`checked_head` が変わらない |
| `_final_target`・`_final_citations` | 最後のラウンドと同じ head → 既存の結果を使い Git を打たない | 最後のラウンドの後に push した head → そのコミットの差分と本文を読み（計 4 回まで）、新しい head の誤りを拾い `checked_head` がその head になる。最初のラウンドの確かめが失敗した後の終わり方の判定 → `last_target` があり、同じ head なら確かめ直す | merge-base の失敗 → 例外を外へ出さず None・警告・`mvv_unresolved`。`last_target` が無い → merge-base から対象を作る（`AttributeError` にならない） |
| `ingest_citations` | 新しい誤り → `judged_mvv` が今回の `by: ndf` の行。消えた誤り → `fixed` | 識別子の違う既存の行も、誤りに当たれば今回の識別子へ、当たらなければ `fixed` | `errors` が None → 何も変えない |
| `_settle_resolved` | 普通の行は今どおり `fixed` | `by: ndf` の行は `unresolved` のまま | — |

## ジャーナルと引継ぎ

満たす決定: 決定 5・6（#4076 の詳細設計の「ジャーナルとレッドライン」の節の `enter` の行と「行のキーの名前は実装で決める」を置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FindingJournal.ingest`・`enter` | `journal.py` | 変更 | 引数 `mvv: Optional[str]`（識別子）を受ける（#4076 で足す引数） | 分類を置くときに `judged_mvv` を書き、`mvv_pinned` で固定するときは前の重さを `unpinned_severity` に残す。出し直された行の `judged_mvv` が今回と違えば、`restate` と同じ外し方をしてから今回の申告で分類する（終わり方によらない）。下の「分類し直し待ちの行」も今回の申告だけで分類する |
| `FindingJournal.restate` | `journal.py` | 新設 | `restate(self, mvv: Optional[str]) -> list[str]` | `judged_mvv` を持ち今回の識別子と違う行の分類を外し、外した id を返す。`_start` が `carry` の後・最初のラウンドの前に 1 回打つ。外した行は `mvv_history` の最後の件で見分けられる（返した id は注意と記録のためで、`enter` はジャーナルの行だけを見る） |
| `FindingJournal.carry` | `journal.py` | 変更 | `carry(self, carried, items: Sequence[str] = (), mvv: Optional[str] = None)` | `Carried` の分類と `unpinned_severity` を戻す。`citation_key` を持つ件は行の `citation_key` に戻し、重ねない判定もこの欄で行う（`text` の `finding_key` で重ねない）。`judged_mvv` が今回と同じ件は `mvv_item` で項目を照らし直して戻す。違う件は旧い分類を `mvv_history` に入れ、`unpinned_severity` があれば重さをそれへ戻して分類なしで戻す（`restate` と同じ外し方） |
| `Carried` | `prior_decide.py` | 変更 | 欄 `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity`・`citation_key`・`thread`（どれも既定 None）を足す | `thread` は記録のコメントの一覧にあったスレッドの番号。`citation_key` は `by: ndf` の行の照合キー（`ingest_citations` の規則） |
| `recorded` | `prior_decide.py` | 変更 | `recorded(remarks) -> tuple[list[Carried], dict[int, Mapping[str, Any]]]` | 信頼済みの最新の記録のコメントから、スレッドの無い件と、スレッドの有無と resolve によらず `by: ndf` の件を返す（`by: ndf` の件は `thread` と `citation_key` を持ち `comment` は None）。スレッドのある件の分類と `citation_key` を `thread` → 値の表で返す |
| `carried` | `prior_decide.py` | 変更 | `carried(threads, tracked, marks: Mapping[int, Mapping[str, Any]] = {})` | 開いたスレッドから戻す件に、`marks` の同じスレッドの分類と `citation_key` を重ねる（resolve 済みを除くのは今どおり） |
| `drive._start` の引継ぎ | `drive.py` | 変更 | — | `carried` の結果に、`recorded` の `by: ndf` の件のうち `thread` が開いたスレッドに無いもの（resolve 済み・消えたスレッド）を足して `carry` に渡す。開いたスレッドにある `by: ndf` の件は `carried` の側を使い、重ねない |
| `open_block` | `report_decide.py` | 変更 | （同じ） | 1 件に `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity`・`citation_key`（`by: ndf` の行だけ）を足す。`redline` の行と `by: ndf` の行は `_light` でも載せる。自由記述でない値なので `clean` を通さない |

- `restate` の外し方: 旧い `judged_mvv`・`mvv`・`redline_items`・`outcome`・`skipped_mvv` を `mvv_history` へ追記し、`mvv`・`mvv_pinned`・`redline`・`redline_items`・`judged_mvv` を消し、`unpinned_severity` があれば `severity` をそれに戻して消す。終わり方が `fixed`・`deferred` の行は終わり方をそのまま残す（今回の結果と記録の対象からは `judged_mvv` が無いので外れる）。`unresolved` の行は普通の未解決の指摘として残る。`by: ndf` の行は外した後も `by: ndf` のままで、次の `ingest_citations` が今回の識別子で置き直すか `fixed` にする
- 分類し直し待ちの行: `judged_mvv` が無く、`mvv_history` の最後の件の `judged_mvv` が今回の識別子と違う行（`restate`・`carry` が外した後、まだ出し直されていない行）。`enter` はこの行の出し直しで、前の重さとの重い方を残す規則を当てず、今回の申告に `effective_severity` を当てた重さと今回の分類だけを置く。分類を置いた後は `judged_mvv` が今回の識別子になり、以後の出し直しは今の規則に戻る。行に持つので、同じ状態ディレクトリで中断から再開しても同じに働く
- 記録の一覧から戻す `by: ndf` の行: 信頼済みの最新の記録のコメントに残る `by: ndf` の行は、人がスレッドを resolve していても戻す。戻した行を解決できるのは `ingest_citations` の機械の確かめだけで、確かめが失敗した（None）起動では未解決のまま残る（決定 12）
- #4084 との統合: `restate` と `enter` の分類し直しは、#4084 の決定 4 の「MVV の再判定」と同じ時点である。後に入る PR が `unkept_severity`・`kept_round` の置き直しを両方の経路で打つ

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `enter` | 旧版の `P1` の行が新版で `Value 5` として出し直される → レッドラインでなく `major`・`judged_mvv` は新版・`mvv_history` に旧い `P1` | 旧版の `fixed` の `Value 5` の行が `mvv` 無しの `minor` で出し直される → `minor`・分類なし。起動時の `restate` で分類を外した、相手役の申告が `major` の旧版の行が、新版で `minor` として出し直される → `minor`（重い方を残さない）で収束を止めない。その後のラウンドで `major` と出し直されれば今どおり重い方 | — |
| `restate` | 出し直されない旧版の `redline` の行 → 分類なし・履歴あり。`unpinned_severity` のある行 → 重さが戻る | 識別子が同じ行は変えない | — |
| `carry`・`recorded`・`carried` | 空の状態ディレクトリから、`Value 5`・`P1`・`by: ndf` の 3 行が分類つきで戻る。スレッドのある件にも重なる | 識別子の違う件は分類なしで戻る。元が `minor` で `mvv_pinned` により `major` に固定された行を、別の状態ディレクトリで新版から再開 → `minor` に戻る。人が resolve した `by: ndf` のスレッドの行が、空の状態ディレクトリからの再開で未解決のまま戻り、確かめが失敗した起動でも未解決のまま。開いたスレッドの `by: ndf` の行は 1 件だけ戻る | 一覧の `mvv` が今回の項目の一覧に無い → 外して注意 |
| `open_block` | 3 つの分類と `unpinned_severity` を埋める | `minor` のレッドラインも載る | — |
| 検出から再開まで（`ingest_citations`・`open_block`・`recorded`・`carry`・`mvv_outputs`） | 根拠の行の誤りを検出 → `judged_mvv` が今回の `by: ndf` の行 → 記録のコメントの一覧に載る → 空の状態ディレクトリから再開 → 同じ識別子で戻り、`outputs.mvv.citations` と MVV の記録に載る | 識別子を変えて再開 → 分類が外れ、次の確かめで今回の識別子に置き直される。秘密に見える文字列を `cited` に含む根拠の行の誤り（記録のコメントの `text` が伏せられる）を、一覧の経路とスレッドの経路のそれぞれから空の状態ディレクトリで再開して再検出 → 元の行が未解決のまま `judged_mvv` が置き直され、新しい行を足さない | — |

## 終わり方と結果

満たす決定: 決定 4・6・7（#4076 の詳細設計の「終わり方と結果」の節の `_finding_gaps`・`record_review`・`mvv_outputs`・ログの表を置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_gaps` | `finish_decide.py` | 変更 | 名前付きの引数 `mvv_checked: bool = True` を足す | 偽なら `mvv_unresolved` の欠けを足す（行の有無によらない） |
| `finish` | `finish_decide.py` | 変更 | 同じ引数を `_finding_gaps` へ渡す | — |
| `read_by_all_head` | `scope_decide.py` | 変更 | （同じ） | `basis.mvv` が承認済みの識別子で、ラウンドの記録の `mvv_checked` が真でなければ、そのラウンドを収束として返さない（None） |
| `mvv_outputs` | `report_decide.py` | 変更 | `mvv_outputs(rows, snapshot, head, mvv: str, clean: Cleaner = _same)` | `judged_mvv == mvv` の行だけを載せ、`reason`・`text` を `clean` に 1 回で通す |
| `record_review` | `records/mvv.py` | 変更 | `record_review(root, pr, head, snapshot, rows, mvv: str, clean: Callable[[Sequence[str]], Sequence[str]])` | `judged_mvv == mvv` の行だけを書き、`reason`・`text` を書く前に `clean` に通す。重ねない判定は伏せた後の値で比べる |
| `_summary` | `drive.py` | 変更 | （同じ） | 再利用でないときは、終わり方を判定する head で `_final_citations(run, head)` と `ingest_citations` を打ってから、`finish` に `mvv_checked`（承認済みの MVV が無いか、`run.checked_head` が終わり方を判定する head と同じ）を渡す。MVV を直す PR として続けた（`snap.state == "mismatch"`）ときは、終わり方を判定する head が `run.recovery_head` と違えば `head_approved(root, head)` を 1 回打ち（Git 2 回）、偽なら `mvv_checked` を偽にする（同じ head なら打たない）。`mvv_outputs` と `record_review` に識別子と `hub.clean` を渡す |
| 再利用の経路 | `drive.py` | 変更 | — | `read_by_all_head` がラウンドを返して `_summary(reused=True)` に入る前に、`run.checked_head = <そのラウンドの head>` とする（`read_by_all_head` は同じ識別子の `Basis` で `mvv_checked: true` のラウンドだけを返すので、その head の確かめは成功済み）。再利用の経路では `_citations` を打たない |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `record_review` | #4076 の項目。見送りの理由とレッドラインの本文は `clean` を通した値 | `mvv-validation.jsonl`・`mvv-signals.jsonl` | 伏せる前の自由記述・レッドラインでない指摘の本文・`judged_mvv` が今回と違う行 |
| `mvv_outputs` | #4076 の項目。理由と本文は `clean` を通した値 | 結果 JSON の `outputs.mvv` | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_finding_gaps`・`finish` | `mvv_checked` が偽 → NDF の指摘が 0 件でも `mvv_unresolved` で止まる | MVV が無い → 真とみなし今どおり | — |
| `read_by_all_head` | `mvv_checked: true` のラウンドを返す | MVV が無いとき `mvv_checked` を見ない | `mvv_checked` が偽・無いラウンド → None |
| 再利用の経路（`_start`・`_summary`） | 承認済みの MVV があり、同じ head・同じ識別子で `mvv_checked: true` の収束がある打ち直し → 相手役も `_citations` も打たず、`mvv_unresolved` にならず収束を返す | MVV が無い打ち直しは今どおり | `mvv_checked` が偽のラウンドしか無い → 再利用せずラウンドを回す |
| `record_review`・`mvv_outputs` | 偽の秘密を含む理由と本文が伏せられて書かれる | 伏せた後が同じ理由の打ち直しは重ねない | `judged_mvv` の違う行 → 書かない・載せない |
