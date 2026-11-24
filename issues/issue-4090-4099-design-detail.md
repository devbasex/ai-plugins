# #4090 #4099 の詳細設計: MVV の写し・根拠の行の確かめ・ジャーナルの置き換え

設計文書 [issue-4090-4099-design.md](./issue-4090-4099-design.md) の決定を、#4076 の詳細設計（[issue-4076-design-detail.md](./issue-4076-design-detail.md)）に対する変更として、ファイルと関数の粒度へ下ろした文書である。ここに書かない関数と規則は #4076 の詳細設計のままである。前提のコードは #4076 と同じく、スプリントブランチ `sprint/p12-lcrfast-r1` が main へ入った後の形である。

## 地図

#4076 の詳細設計の地図に足す・変える行だけを書く。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | `adapters.markdown`（足す） | モジュール | `body_path`（新設）・`parse_citation`（新設）・`decision_lines`（新設）・`fenced_lines`（#4076 の新設を置き換え）・`citation_errors`（#4076 の新設を置き換え）・`review_holds`（#3959 の新設に条件を足す） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の設定・本文・記録の読み書き | `git`・`mvv_decide` | `Snapshot`（欄を足す） | `current`（置き換え）・`_write_verified`（新設）・`record_review`（引数を足す）・`record_key`（新設） |
| `src/ndf/domains/records/preflight.py` | 変更 | 手順 0 の確かめ | `mvv_decide` | モジュール | `mvv_body`（`body_path` を呼ぶ。返す値は同じ） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv`・`git` | モジュール | `_mvv_current`（取得を置き換え・`--allow-mismatch` を足す） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv`・`journal` | `RunConfig`・`_Run`（欄 `clean` を足す）・`_Prepared`（欄を足す）・`_Target`（新設） | `_start`・`_prepare_round`・`_merge_base`・`_committed_texts`・`_citations`（置き換え）・`_final_target`・`_final_citations`・`_review_diff`・`_diff_files`・`_base_lines`（新設）・`_settle_resolved`・`_summary`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal` | `ingest`・`enter`・`carry`・`ingest_citations`（変更）・`restate`・`citation_key`（新設） |
| `src/ndf/domains/crossreview/prior_decide.py` | 変更 | 前の実行から引き継ぐ指摘 | `report_decide` | `Carried`（欄を足す） | `carried`・`recorded`（変更） |
| `src/ndf/domains/crossreview/report_decide.py` | 変更 | 記録のコメントと結果 JSON | — | モジュール | `open_block`（変更）・`mvv_outputs`（引数を足す）・`once`（新設） |
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
| `_write_verified` | `records/mvv.py` | 新設 | `_write_verified(path: Path, data: bytes, sha256: str) -> Path` | `path` が無いか、中身の SHA-256 が `sha256` と違えば、`data` を同じディレクトリの一時ファイルへ書いて `os.replace` で置き換える。同じなら書かない |
| `_mvv_current` | `records/cli.py` | 変更 | （同じ）。引数 `--allow-mismatch` を足す | `git.Git(root).fetch_branch_or_raise(<リモート>, <ブランチ>)` を打ってから `current(root, <リモート>/<ブランチ>, allow_mismatch=<引数>)` を呼ぶ |

- `current` の手順（#4076 の手順を置き換える）
  1. `git rev-parse --verify <ref>^{commit}` でコミットへ解く。失敗は `MvvError`（`precondition`）
  2. `git ls-tree -z <コミット> -- .ndf/mvv.json` を打つ。成功して出力が空のとき（ツリーに設定のエントリが無いと確かめられたとき）だけ `absent`（`commit` は置き、共通原則は読まない）。`ls-tree` の失敗、エントリが通常のファイルの blob でない（`tree`・`commit`・シンボリックリンク）は `MvvError`（`precondition`）
  3. 設定を `ls-tree` で得た blob の名前で `run_bytes` で読み、`decl_from` で検査する。`body_path(decl)` で本文のパスを決め、本文を `run_bytes` で読む。設定の blob の読み取りの失敗（blob の欠損を含む）・本文が読めないときは `MvvError`（`precondition`）。読めなかったことを `absent` に落とさない
  4. `mvv_decide.status` で状態を決める。`mismatch` は、`allow_mismatch` が偽なら `MvvError`（「ベースの MVV の本文が承認の記録と合わない: <本文のパス>」）。真なら状態 `mismatch`・項目なし・写しを書かずに返す（基準 5 は外れる）。`unapproved` はそのまま返す
  5. 承認済みなら、本文の写しと共通原則の写し（`mvv.principles` のバイト列から sha256 を求め、`principles-<先頭 12 文字>.md`）を `_write_verified` で書き、`items_of` で項目の一覧を作る。`blobs` の鍵は設定のパスと `body_path` のパス
  - 副作用: `git rev-parse`・`git ls-tree` を 1 回ずつ、`git show`（`run_bytes`）を 2 回（`absent` では 0 回）。写しのファイルを 2 つまで読み、違えば書く
- `drive._start`（#4076 の手順を置き換える。正本は [issue-4076-design-detail.md](./issue-4076-design-detail.md) の「MVV の写し」の手順で、ここは同じ内容である）: `remote = g.remote()` を 1 回求め、MVV を読むブランチを決める。`--base` がブランチ名ならその名前、`<リモート>/<ブランチ>` ならリモート名を外した名前、コミットの SHA などブランチに解けない値なら Pull Request のマージ先のブランチ（`baseRefName`。読めなければ `MvvError` と同じ扱いで相手役を起動せずに止める）である。`sync_worktree(g, cfg.branch, remote, base_branch=<そのブランチ>)` の後に `snap = current(root, <そのブランチのリモート追跡参照>)` を呼び、`cfg = dataclasses.replace(cfg, base_commit=<差分の起点>)` とする（`RunConfig` は frozen dataclass）。差分の起点は、`--base` がブランチ（リモート名つきを含む）なら `snap.commit`、SHA などなら渡されたコミットで、MVV を読んだコミット（`snap.commit`）とは別の値として持つ。`current` の `MvvError`（`mismatch` を含む）は #4076 のとおり相手役を起動せずに `refused` で止める（`allow_mismatch` を渡さない）。`RunConfig`（`crossreview/settings.py`）に `base_commit: Optional[str] = None` を足す。以後の `changes.changed(run.g, cfg.base_commit, …)`・`_merge_base`（`run.g.merge_base(cfg.base_commit, head)`）・差分（`f"{scope.base or merge_base}...HEAD"`）は名前を Git に渡さない
- `cmd_scope`（`review-scope`）: 手元の `<リモート>/<MVV を読むブランチ>`（`--base` からの決め方は `drive._start` と同じ。`--base` が SHA のときは `run` が残したマージ先のブランチ）を `git rev-parse` で 1 回コミットに解き、そのコミットで `git ls-tree` を打つ。本文のパスは `ls-tree` で得た設定の blob を `git cat-file` で読み `body_path` で解く（設定の blob が最後のラウンドの記録と同じなら、記録のパスを使い読まない）。共通原則の sha256 は、承認済みのときだけプラグインのファイルから求める（識別子の規則は「基準 5 と識別子」）。ネットワークに出ず写しを書かない（#4076 のまま）

### 異常系の処理

#4076 の表の行を次のとおり置き換える・足す。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `current` | 設定の有無を確かめられない | `git ls-tree` の失敗・設定のエントリが通常のファイルでない・設定の blob の欠損 | `ls-tree` の終了コードと出力・`git show` の失敗 | `MvvError`（`absent` にしない） | `refused`（`precondition`）、`summary` に `.ndf/mvv.json` とコミット | 直して打ち直す |
| `current` | 設定はあるが本文を読めない | 設定の `body` のパスに本文が無い・`git show` の失敗 | `git show` の失敗 | `MvvError` | `refused`（`precondition`）、`summary` に `body_path` のパス | 直して打ち直す |
| `body_path` | パスが不正 | 絶対パス・`..`・空 | 値の検査 | `MvvError` | `refused`（`precondition`）、`summary` に値 | 設定を直して打ち直す |
| `current` | 本文の sha256 が合わない | 承認の後に本文が変わった | sha256 の比較 | `MvvError`（`allow_mismatch` が偽のとき。`mismatch` を状態として返さない） | `ndf records mvv current`・`ndf cross-review run` とも `refused`（`precondition`）、`summary` に「ベースの MVV の本文が承認の記録と合わない」とパス | 本文を戻すか承認し直して打ち直す。直す PR も cross-review を通らないので、人が cross-review を通さずにマージする。直す工程の起動指示は `ndf records mvv current --allow-mismatch`（`ok`・`state: mismatch`・警告つき）で写す。cross-review は相手役を起動せず、状態ファイルとジャーナルを書かない |
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
| `current` | 既定外のパスで承認したリポジトリ → 承認済み・`blobs` にそのパス。`commit` が解いたコミット | 同じ中身の写しは書き直さない。違う中身の写しは検証済みの中身になる。ツリーに `.ndf/mvv.json` が無い → `absent` | `mismatch` → `MvvError`（`allow_mismatch=True` なら状態 `mismatch`・写しなし）。`unapproved` → 状態を返す。設定の blob を欠損させたコミット・`ls-tree` の失敗 → `MvvError`（`absent` にならない） |
| `_mvv_current` | `--single-branch` で別のブランチを clone した作業場所で新しい版を返す | `--allow-mismatch` で `mismatch` のベース → `ok`・`state: mismatch`・警告 | 取得の失敗 → `refused` |
| `drive._start` | ローカルの `main` が古くても、merge-base・差分・`Basis.base` がリモートのコミットから作られる | `mismatch` のベースで、PR が本文を戻していても相手役を起動しない | `MvvError` → 相手役を起動しない |

## 基準 5 と識別子

満たす決定: 決定 11・15（#4076 の詳細設計の「指摘の基準 5」の節と「再利用の条件に MVV を足す」の表を置き換える）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_lines` | `criteria_decide.py` | 変更 | （#4076 と同じ） | `mvv.state == "approved"` のときだけ基準 5 を足す。`mismatch` は `current` が止めるので、ここには来ない |
| `mvv_criterion` | `review-criteria.json` | 変更 | 文 | 「プロジェクト MVV（版 {version}。本文は {path}）の Mission・Vision・Value の文に反する。必ず人の承認が要る操作は、本文の表と NDF の共通原則（{principles}）の C1〜C8 で決める」 |
| `Basis.mvv` | `scope_decide.py` | 変更 | 文字列 | 識別子。承認済みは `"approved:<version>:<sha256>:<共通原則の sha256>"`。承認済みでない（`absent`・`unapproved`）は `"<state>:::"` で、共通原則の sha256 を含めない（基準 5 が無く共通原則を渡さないので、NDF の版上げで共通原則が変わっても前の収束と詳細レビュー済みを再利用する） |
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
| `_final_citations` | `crossreview/drive.py` | 新設 | `_final_citations(run: _Run, head: str) -> Optional[list[dict[str, Any]]]` | 終わり方の判定の直前の確かめ。`_final_target` と `_citations` を同じ `try` の中で打ち、どちらの `GitError`・`OSError`・`UnicodeDecodeError` も None にして注意に載せる（`run.citations` と `checked_head` は変えない）。`_summary` はこの関数だけを呼び、承認済みの MVV があるときだけ呼ぶ（無ければ `_final_target` も作らない） |
| `_citations` | `crossreview/drive.py` | 置き換え | `_citations(run: _Run, target: _Target) -> Optional[list[dict[str, Any]]]` | 下の手順。確かめられなければ None。`_prepare_round` は `_Target` を作った直後に、確かめの成否によらず `run.last_target = <ラウンドの _Target>` を置いてから `_citations(run, run.last_target)` を呼ぶ。`_summary` の前は `_final_citations(run, head)` を呼ぶ。`_prepare_round` を通し直さない。`_prepare_round` も `_summary` も、承認済みの MVV が無い起動では `_citations`・`_final_citations` を呼ばない（Git を打たない）。そのときは `ingest_citations([], run.mvv)`（Git を打たない）で、前の起動から戻した未解決の `by: ndf` の行を `fixed` にする（基準 5 が無く確かめない起動で、確かめでしか解決できない行が残り続けない） |
| `_committed_texts` | `crossreview/drive.py` | 変更 | `_committed_texts(g: git.Git, commit: str, paths: Sequence[str]) -> dict[str, str]` | `commit` のツリーの `paths` の本文を、何件あっても Git を 1 回だけ起動して読む（今の `HEAD` 固定をやめ、コミットを引数に取る）。既存の呼び手（`_changed`）は `"HEAD"` を渡す |
| `_review_diff` | `crossreview/drive.py` | 新設 | `_review_diff(g: git.Git, base: str, head: str) -> str` | `git -c core.quotePath=false -c diff.noprefix=false -c diff.mnemonicPrefix=false -c diff.suppressBlankEmpty=false diff --no-color --no-ext-diff --no-textconv --no-relative --src-prefix=a/ --dst-prefix=b/ --unified=3 --find-renames <base>...<head>` を 1 回打つ。利用者の設定（`diff.noprefix`・`diff.mnemonicPrefix`・`diff.relative`・`diff.context`・`diff.suppressBlankEmpty`・`diff.external`・属性の `textconv`）で接頭辞・パス・hunk の行の形・本文が変わらないよう、機械で読む形をコマンドの側で固定する（`--no-textconv` で、hunk の行がコミットの blob の行と番号ごと対応する）。相手役に渡す差分のファイル（`_prepare_round`）と `_citations` の全差分の両方がこの関数で作るので、`full_diff` を読み直しに使える |
| `_diff_files` | `crossreview/drive.py` | 新設 | `_diff_files(text: str) -> list[DiffFile]` | `_review_diff` の出力を、ファイルごとの（新しいパス・元のパス・hunk の並び・足した行の番号・消した行の本文と足した行の本文）に分ける。新しいパスは `+++ b/<パス>`、改名は `rename from <パス>` と `rename to <パス>` の行から読む。`core.quotePath=false` でも Git が引用する（`"` で始まる）パスは C の書き方の引用を解いて読む。`+++ /dev/null`（消したファイル）は飛ばす |
| `_base_lines` | `crossreview/drive.py` | 新設 | `_base_lines(hunks: Sequence[Hunk], head_line: int) -> Optional[int]` | head の変わっていない行の番号を、hunk の行数の差からベースの行の番号へ写す。足した行なら None |
| `_Prepared` | `crossreview/drive.py` | 変更 | 欄 `docs: Mapping[str, str]` と `full_diff: Optional[Path]` を足す（意味は `_Target` と同じ） | — |
| `_settle_resolved` | `crossreview/drive.py` | 変更 | （同じ） | `row.get("by") == "ndf"` の行を飛ばす |
| `citation_key` | `journal.py` | 新設 | `citation_key(error: Mapping[str, Any]) -> str` | 誤りの照合キー。`[path, cited, kind]` の生の値を区切りの曖昧さの無い JSON（`json.dumps(..., ensure_ascii=False, separators=(",", ":"))`）にした UTF-8 の SHA-256 の 16 進を返す。伏せ字も鍵も通さない（キーは手元のジャーナルだけに置き、公開のコメントに載せない） |
| `_Run` | `crossreview/drive.py` | 変更 | 欄 `clean: Callable[[Sequence[str]], Sequence[str]]` を足す | `_start` が記録のコメントに渡すのと同じ `hub.clean` を置く。`_summary` が `mvv_outputs` と `record_review` に渡す。`_citations` は使わない |
| `FindingJournal.ingest_citations` | `journal.py` | 変更 | `ingest_citations(self, errors: Optional[Sequence[Mapping[str, Any]]], mvv: str)` | 下の規則。誤りごとに `citation_key` を作って照らす。`clean` を呼ばない |

- `parse_citation`
  - 候補: 左から空白と `- ` を除いた先頭が `根拠:` で、`MVV` を含むか、`decision` が真
  - 解析: `根拠:` の後ろから、`「` と対応する `」` の間（入れ子を数える。閉じなければ行末まで）を除く。残りで最初の `（MVV` を探し、そこから最初の `）` までを版の表記として、全体が `（MVV バージョン (\d+)(・スプリント MVV [0-9a-f]{8})?）` に一致するときだけ版を読む。閉じの `）` の後ろ（`。STD1` など）は読まない。`（MVV` の前を ` / ` と `・` で分けて前後の空白を除いた値を項目にする。`スプリント` で始まる項目と `根拠なし` は照らさない（#4076 のまま）
  - 版を読めない候補は版を None で返す（`（MVV 版 11）`・`（MVV バージョン 11x）`・閉じの `）` の欠け・版の欠け・`（MVV なし）` を含む）
- `citation_errors` の返す 1 件: `{"path", "line", "cited", "kind", "version", "unknown"}`。`cited` は種類によらず、`根拠:` の後ろから引用文（`「`〜`」`）を除き、最初の `（MVV` から後ろ（無ければ何も除かない）を除いて前後の空白を除いた文字列である（`format` でも行の原文・引用文・版の表記を入れない。例 `- 根拠: Value 3「…」（MVV 版 11）` → `Value 3`）。`unknown` は一覧に無い項目の並び。`cited`・`unknown` は PR 本文から取った自由な文字列なので、結果と記録へ書く前に `clean` を通す（「終わり方と結果」の `mvv_outputs`・`record_review`）。`kind` は `format`（版が None。`version` は None・`unknown` は空）・`version`（版が `catalog` に無い）・`unknown`（版はあり、その版の一覧に無い項目がある）。`catalog` は #4076 のまま `{ベースの承認済みの版: Snapshot.items}` の 1 件
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
  7. `citation_errors` を当て、結果を `run.citations` に置き（照合キーは `ingest_citations` が `citation_key` で作るので、ここでは足さない）、`run.checked_head = target.head` とする（`run.last_target` はここで置かない。`_prepare_round` が確かめの前に置く）。GitHub を呼ばず、`clean` も呼ばない
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
  - 誤りのキーは `citation_key(error)`（`[path, cited, kind]` の生の値の SHA-256）で、`by: ndf` の行を作るときに行の `citation_key` に書く。照合はこの欄だけで行い、表示用の `text` や `oscillation.finding_key(text)` を使わない（`text` は記録のコメントで伏せ字が入り得る）
  - キーは手元の状態ファイル（ジャーナル）だけに持つ。`open_block`・`Carried`・`recorded`・`carried` は持たず、記録のコメントの一覧とスレッドの引継ぎに載らない。公開しないので、伏せ字も鍵も通さない（決定 7）
  - `citation_key` の無い `by: ndf` の行（記録のコメントから戻した行・この版より前の記録から戻した行）はどの誤りにも当たらないので `fixed` になり、同じ誤りは新しい行として 1 回だけ足される。別の状態ディレクトリからの再開ではこの経路になる（決定 7 の代償。誤りは落ちない）。同じ状態ディレクトリでの再開は、ジャーナルの行がキーを持つので元の行に当たる
  - 誤りのキー（`citation_key`）に当たる既存の `by: ndf` の行は未解決のまま `judged_mvv = mvv` に置き直す。当たる行が無い誤りは新しい `by: ndf` の行（`major`・`judged_mvv = mvv`）として足す
  - 未解決の `by: ndf` の行で誤りのキーに当たらないものは、`judged_mvv` によらず `fixed`（`judged_mvv = mvv`）にする。`by: ndf` の行を解決するのはこの経路だけである（決定 12）
  - 呼び手は `_prepare_round` と `_summary` の前の確かめで、`_citations`（`_final_citations`）が返した誤りと `run.mvv`（今回の識別子）を渡す。`hub.clean` は `run.clean` として記録のコメントと `_summary`（`mvv_outputs`・`record_review`）だけが使い、`_citations` と `ingest_citations` は使わない
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
| `citation_errors` | 4 要素の行で決定の根拠の行の版の欠けを `format` にする。`- 根拠: Value 3「…」（MVV 版 11）` の `format` の `cited` は `Value 3`（引用文と版の表記を含まない） | 版の表記の無い決定の根拠の行 `- 根拠: Value 3「…」` の `cited` は `Value 3` | 3 要素の行 → `TypeError`（既定値で決定の外として通さない） |
| `_diff_files` | 新しいパス・足した行・hunk | 日本語を含むパス（引用されない）・`"` を含むパス（引用を解く）・改名（元のパスが `rename from`）・消したファイルは飛ばす | — |
| `_review_diff` | `+++ b/<パス>` と `@@ -a,b +c,d @@` の形で出る | 利用者の設定 `diff.noprefix=true`・`diff.mnemonicPrefix=true`・`diff.context=0`・`diff.suppressBlankEmpty=true` と `textconv` の属性を置いたリポジトリでも、同じ差分が出て `_citations` が根拠の行の誤りを拾う | — |
| `_base_lines` | hunk の前後の行を写す | ファイルの先頭と末尾の hunk | 足した行 → None |
| `_citations` | 足した行と、フェンスの行だけを消して本文に出た行を拾う。承認済みの MVV がある PR で、Markdown でないファイル（lockfile など）の `根拠:` を含まない 1 行だけを変えた → head の本文を読まない（全差分の後の Git が 0 回）。日本語のパスの文書と改名した文書でも拾う | ベースから本文の変わらない行を拾わない。ベースの本文は `merge_base` のコミットと元のパスで読む（head の本文を読まない）。同じ鍵の 2 回目は Git を打たず、1 回目と同じ結果を返す。同じ別リポジトリへの参照を含む誤りが 3 件ある確かめで、`clean` の呼び出しと公開範囲の照会（`hub.visibilities`）が 0 回。範囲が狭まったラウンドでは全差分を 1 回打つ。上の表の場合ごとの起動の数（0 回の 2 つの場合と、両側を読む場合の上限 3） | 失敗 → None、`checked_head` が変わらない |
| `_final_target`・`_final_citations` | 最後のラウンドと同じ head → 既存の結果を使い Git を打たない。承認済みの MVV が無い PR で最後のラウンドの修正を push した → `git merge-base` を打たない（`_final_target` を作らない） | 最後のラウンドの後に push した head → そのコミットの差分と本文を読み（計 4 回まで）、新しい head の誤りを拾い `checked_head` がその head になる。最初のラウンドの確かめが失敗した後の終わり方の判定 → `last_target` があり、同じ head なら確かめ直す | merge-base の失敗 → 例外を外へ出さず None・警告・`mvv_unresolved`。`last_target` が無い → merge-base から対象を作る（`AttributeError` にならない） |
| `citation_key`・`ingest_citations` | 新しい誤り → `judged_mvv` が今回の `by: ndf` の行。消えた誤り → `fixed`。同じ状態ディレクトリでの再開で同じ誤りを再検出 → 元の行が未解決のまま置き直され、新しい行を足さない。`citation_key` は生の `[path, cited, kind]` だけで決まり、伏せ字の規則・秘密の環境変数・参照先の公開範囲を変えても同じ | 照合キーを持たない `by: ndf` の行（記録のコメントから戻した行）がある状態で同じ誤りを再検出 → 元の行が `fixed`・新しい行が 1 回だけ足される。識別子の違う既存の行も、誤りに当たれば今回の識別子へ、当たらなければ `fixed` | `errors` が None → 何も変えない |
| `_settle_resolved` | 普通の行は今どおり `fixed` | `by: ndf` の行は `unresolved` のまま | — |

## ジャーナルと引継ぎ

満たす決定: 決定 5・6（#4076 の詳細設計の「ジャーナルとレッドライン」の節の `enter` の行と「行のキーの名前は実装で決める」を置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FindingJournal.ingest`・`enter` | `journal.py` | 変更 | 引数 `mvv: Optional[str]`（識別子）を受ける（#4076 で足す引数） | 分類を置くときに `judged_mvv` を書き、`mvv_pinned` で固定するときは前の重さを `unpinned_severity` に残す。出し直された行の `judged_mvv` が今回と違えば、`restate` と同じ外し方をしてから今回の申告で分類する（終わり方によらない） |
| `FindingJournal.restate` | `journal.py` | 新設 | `restate(self, mvv: Optional[str]) -> list[str]` | `judged_mvv` を持ち今回の識別子と違う行の分類を外し、外した id を返す。`_start` が `carry` の後・最初のラウンドの前に 1 回打つ |
| `FindingJournal.carry` | `journal.py` | 変更 | `carry(self, carried, items: Sequence[str] = (), mvv: Optional[str] = None)` | `Carried` の分類と `unpinned_severity` を戻す。重ねない判定は今どおり（同じ id か `finding_key` の同じ行があれば入れない）で、戻した `by: ndf` の行は `citation_key` を持たない。`judged_mvv` が今回と同じ件は `mvv_item` で項目を照らし直して戻す。違う件は旧い分類を `mvv_history` に入れ、`unpinned_severity` があれば重さをそれへ戻して分類なしで戻す（`restate` と同じ外し方） |
| `Carried` | `prior_decide.py` | 変更 | 欄 `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity`・`thread`（どれも既定 None）を足す | `thread` は記録のコメントの一覧にあったスレッドの番号。照合キーは持たない（決定 7） |
| `recorded` | `prior_decide.py` | 変更 | `recorded(remarks) -> tuple[list[Carried], dict[int, Mapping[str, Any]]]` | 信頼済みの最新の記録のコメントから、スレッドの無い件と、スレッドの有無と resolve によらず `by: ndf` の件を返す（`by: ndf` の件は `thread` を持ち `comment` は None）。スレッドのある件の `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity` を `thread` → 値の表で返す |
| `carried` | `prior_decide.py` | 変更 | `carried(threads, tracked, marks: Mapping[int, Mapping[str, Any]] = {})` | 開いたスレッドから戻す件に、`marks` の同じスレッドの値（`recorded` の表の 5 つの欄）を重ねる（resolve 済みを除くのは今どおり） |
| `drive._start` の引継ぎ | `drive.py` | 変更 | — | `carried` の結果に、`recorded` の `by: ndf` の件のうち `thread` が開いたスレッドに無いもの（resolve 済み・消えたスレッド）を足して `carry` に渡す。開いたスレッドにある `by: ndf` の件は `carried` の側を使い、重ねない |
| `open_block` | `report_decide.py` | 変更 | （同じ） | 1 件に `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`・`unpinned_severity` を足す。照合キー（`citation_key`）は載せない。`redline` の行と `by: ndf` の行は `_light` でも載せる。自由記述でない値なので `clean` を通さない |

- `restate` の外し方: 旧い `judged_mvv`・`mvv`・`redline_items`・`outcome`・`skipped_mvv` を `mvv_history` へ追記し、`mvv`・`mvv_pinned`・`redline`・`redline_items`・`judged_mvv` を消し、`unpinned_severity` があれば `severity` をそれに戻して消す。終わり方が `fixed`・`deferred` の行は終わり方をそのまま残す（今回の結果と記録の対象からは `judged_mvv` が無いので外れる）。`unresolved` の行は普通の未解決の指摘として残る。`by: ndf` の行は外した後も `by: ndf` のままで、次の `ingest_citations` が今回の識別子で置き直すか `fixed` にする
- 記録の一覧から戻す `by: ndf` の行: 信頼済みの最新の記録のコメントに残る `by: ndf` の行は、人がスレッドを resolve していても戻す。戻した行を解決できるのは `ingest_citations` の機械の確かめだけで、確かめが失敗した（None）起動では未解決のまま残る（決定 12）
- #4084 との統合: `restate` と `enter` の分類し直しは、#4084 の決定 4 の「MVV の再判定」と同じ時点である。後に入る PR が `unkept_severity`・`kept_round` の置き直しを両方の経路で打つ

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `enter` | 旧版の `P1` の行が新版で `Value 5` として出し直される → レッドラインでなく `major`・`judged_mvv` は新版・`mvv_history` に旧い `P1` | 旧版で相手役の申告が `minor` で `mvv_pinned` により `major` に固定された（`unpinned_severity: minor`）`fixed` の `Value 5` の行が、`mvv` 無しの `minor` で出し直される → `minor`・分類なし。固定前も `major` だった（`unpinned_severity: major`、または固定していない `major`）旧版の行が `minor` で出し直される → 重い方を残す規則で `major` のまま・分類なし・未解決なら収束を止める（決定 6 の代償） | — |
| `restate` | 出し直されない旧版の `redline` の行 → 分類なし・履歴あり。`unpinned_severity` のある行 → 重さが戻る | 識別子が同じ行は変えない | — |
| `carry`・`recorded`・`carried` | 空の状態ディレクトリから、`Value 5`・`P1`・`by: ndf` の 3 行が分類つきで戻る。スレッドのある件にも重なる | 識別子の違う件は分類なしで戻る。元が `minor` で `mvv_pinned` により `major` に固定された行を、別の状態ディレクトリで新版から再開 → `minor` に戻る。人が resolve した `by: ndf` のスレッドの行が、空の状態ディレクトリからの再開で未解決のまま戻り、確かめが失敗した起動でも未解決のまま。開いたスレッドの `by: ndf` の行は 1 件だけ戻る | 一覧の `mvv` が今回の項目の一覧に無い → 外して注意 |
| `open_block` | 3 つの分類と `unpinned_severity` を埋める | `minor` のレッドラインも載る。`by: ndf` の行にも照合キーが載らない | — |
| 検出から再開まで（`ingest_citations`・`open_block`・`recorded`・`carry`・`mvv_outputs`） | 根拠の行の誤りを検出 → `judged_mvv` が今回の `by: ndf` の行 → 記録のコメントの一覧に載る（照合キーは載らない）→ 空の状態ディレクトリから再開 → 同じ識別子で戻り、`outputs.mvv.citations` と MVV の記録に載る | 識別子を変えて再開 → 分類が外れ、次の確かめで今回の識別子に置き直される。秘密に見える文字列を `cited` に含む根拠の行の誤り（記録のコメントの `text` が伏せられる）を、一覧の経路とスレッドの経路のそれぞれから空の状態ディレクトリで再開して再検出 → 元の行が `fixed` になり、新しい行が 1 回だけ足される（誤りは落ちない。決定 7 の代償） | — |

## 終わり方と結果

満たす決定: 決定 4・6・7（#4076 の詳細設計の「終わり方と結果」の節の `_finding_gaps`・`record_review`・`mvv_outputs`・ログの表と、「`mvv-review` の 1 行」の `key` の定義（ジャーナルの行の `key`）を `record_key` の値に置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_gaps` | `finish_decide.py` | 変更 | 名前付きの引数 `mvv_checked: bool = True` を足す | 偽なら `mvv_unresolved` の欠けを足す（行の有無によらない） |
| `finish` | `finish_decide.py` | 変更 | 同じ引数を `_finding_gaps` へ渡す | — |
| `read_by_all_head` | `scope_decide.py` | 変更 | （同じ） | `basis.mvv` が承認済みの識別子で、ラウンドの記録の `mvv_checked` が真でなければ、そのラウンドを収束として返さない（None） |
| `mvv_outputs` | `report_decide.py` | 変更 | `mvv_outputs(rows, snapshot, head, mvv: str, clean: Cleaner = _same)` | `judged_mvv == mvv` の行だけを載せ、`disputed` の `reason`・`redlines` の `text`・`citations` の `path`・`cited`・`unknown` の各項目を 1 つの並びにして `clean` に 1 回で通し、出力にはそれぞれ伏せた後の値を置く。ジャーナルの行の `path`・`cited`・`unknown` は生のまま残し、修正担当への指示と照合キーはそれを使う。生のパスは結果 JSON に載せない |
| `record_review` | `records/mvv.py` | 変更 | `record_review(root, pr, head, snapshot, rows, mvv: str, clean: Optional[Callable[[Sequence[str]], Sequence[str]]])` | `judged_mvv == mvv` の行だけを書き、`reason`・`text`・`by: ndf` の行の `item`（`cited`）を書く前に 1 回で `clean` に通す。`mvv-review` と `review_disputed` の `key` は `record_key(行の text)` で、`clean` に通さない（伏せ字は表示のためだけに使い、記録上の同一性に使わない）。重ねない判定（`mvv-validation.jsonl` の（repo・PR・key）の最後の行・`mvv-signals.jsonl` の（repo・PR・key・版））と `count_signals` の数え方は #4076 のままで、理由・本文は伏せた後の値で、`key` は `record_key` の値で比べる。`clean` が None（照会に失敗した起動）のときは照会せず、伏せる欄（`reason`・`text`・`by: ndf` の行の `item`）を置かずに `withheld: true` を置いた `mvv-review` の行を書く。`key`・`id`・MVV の項目の番号の `item`（`P1・C7`・`Value 5` など。自由記述でない）・終わり方・版・`head` は普段どおり書き、`review_disputed` も普段どおり（自由記述の欄を持たない）書く。`mvv-validation.jsonl` の重ねない判定は比べる組に `withheld` を足し、次の 2 つを加える: 伏せた行は、最後の行が伏せていない行で（head・終わり方・版）が同じなら書かない（分かっている理由を伏せた行で上書きしない）。伏せていない行は、最後の行が伏せた行なら書く（記録の復旧）。`mvv-signals.jsonl` の重ねない判定は変えない（版ごとに 1 回で、伏せたかによらない）。書けない行の扱い（`ReviewWrite`・`mvv_record_failed`）は #3959 のまま |
| `review_holds` | `records/mvv_decide.py` | 変更（#3959 の新設に条件を足す。決定 16） | （#3959 のまま） | #3959 の 2 つの規則に 3 つ目を足す: キーの最後の行（#3959 の「最後」の決め方）が `withheld: true` で版が `mvv` と同じなら、終わり方によらず保留「PR #N の <項目>（記録の自由記述が未完了）」を返す（記録が復旧するまでゲートを省かない）。`redline`・`deferred` の保留は今どおりで、その理由・本文が伏せた行から来るときは、承認資料に `WITHHELD` の文を写す |
| `record_key` | `records/mvv.py` | 新設 | `record_key(text: str) -> str` | 記録の `key`。ジャーナルの行の生の `text`（`by: ndf` の行はパスと引いた本文から作った `text`。ジャーナルの `key` と同じ元）に、先に `run_log.redact`（秘密の環境変数の値とトークンの形）を当て、その後で `oscillation.finding_key`（空白の圧縮・小文字化・切り詰め）を当てた文字列の SHA-256 の 16 進 64 文字を返す。伏せてから小文字化・切り詰めるので、大文字小文字の混ざった秘密も、切り詰めの境界にまたがる秘密も、ハッシュの元に入らない（元に入らないのでキーから秘密を戻すことも、候補を照らすこともできない）。参照先の公開範囲を照会せず、`Hub.clean` も通さないので、別々の非公開リポジトリへの参照だけが違う指摘は別のキーになり、参照先の公開範囲が変わっても同じ指摘のキーは変わらない |
| `once` | `report_decide.py` | 新設 | `once(clean: Cleaner) -> Once` | 1 回の `_summary` の中だけ使う `clean` の包み。呼ばれた値の並びのうち、まだ伏せていない値だけを 1 回で `clean` に通し、値ごとの結果を覚えて返す。`clean` が例外を送出したら、その呼び出しと以後のどの値にも固定の文 `WITHHELD`（「（参照先の公開範囲を確かめられず伏せた）」）を返し、`clean` を呼び直さない。欄 `failure` に最初の例外の原因を固定文で持つ（失敗が無ければ None）。原因は `gh.GitHubError` の構造化された欄だけから決め、例外の文（`str(e)`。GraphQL のサーバーが返したエラーメッセージを含み、照会先のリポジトリ名が入りうる）は使わない: `quota` があれば「レート制限」、`unreached` が真なら「通信の障害」、それ以外は「GitHub が照会を断った（HTTP <status>）」（`status` は整数）。`gh.GitHubError` 以外の例外は「照会の失敗（<例外の型名>）」とする。例外の文は結果 JSON にも `warnings` にも渡さない。覚えるのは `mvv_outputs` から `record_review` までで、公開の書き先へ投稿する経路（記録のコメント）には渡さない |
| `_summary` | `drive.py` | 変更 | （同じ） | 承認済みの MVV があるか（`snap.state == "approved"`）を最初に判定する。承認済みで再利用でないときだけ、終わり方を判定する head で `_final_citations(run, head)` と `ingest_citations` を打つ。承認済みでない（`absent`・`unapproved`）ときは `_final_citations` も `_final_target` も呼ばない（決定 4 のとおり確かめないので、最後の修正で head が変わっても `git merge-base` を打たない）。`finish` には `mvv_checked`（承認済みの MVV が無いか、`run.checked_head` が終わり方を判定する head と同じ）を渡す。`run.clean`（記録のコメントと同じ `hub.clean`）を `once` で包んだ値を 1 つ作り、`mvv_outputs`、`record_review` の順に識別子とともに渡す。`record_review` が伏せる値（`reason`・`text`・`cited`）は `mvv_outputs` が伏せる値に含まれるので、`record_review` は照会しない。包みの `failure` があれば、注意「MVV の結果の自由記述を伏せた（参照先の公開範囲を照会できない）: <原因>」を足して `outputs.mvv` は `WITHHELD` の値のまま返し、`record_review` には `clean=None` を渡して自由記述を持たない行を書かせ、注意「MVV の記録（mvv-validation.jsonl）の自由記述を伏せて書いた（次の起動で書き直す。それまで設計承認ゲートは省かない）: <原因>」を足す。`record_review` の `ReviewWrite` の扱い（`missing` があれば `mvv_record_failed`）は #3959 のまま。それ以外の終わり方と結果の返却は変えない |
| 再利用の経路 | `drive.py` | 変更 | — | `read_by_all_head` がラウンドを返して `_summary(reused=True)` に入る前に、`run.checked_head = <そのラウンドの head>` とする（`read_by_all_head` は同じ識別子の `Basis` で `mvv_checked: true` のラウンドだけを返すので、その head の確かめは成功済み）。再利用の経路では `_citations` を打たない |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `record_review` | #4076 の項目。見送りの理由・レッドラインの本文・根拠の行の誤りの `item` は `clean` を通した値。`key` は `record_key` の値 | `mvv-validation.jsonl`・`mvv-signals.jsonl` | 伏せる前の自由記述・本文の断片を含む `key`（ジャーナルの行の `key`）・レッドラインでない指摘の本文・`judged_mvv` が今回と違う行・`WITHHELD` の値（照会に失敗した起動では伏せる欄を置かず `withheld: true` を置く） |
| `mvv_outputs` | #4076 の項目。理由・本文・`citations` のパス・`cited`・`unknown` は `clean` を通した値（照会に失敗した起動では `WITHHELD`） | 結果 JSON の `outputs.mvv` | 同上。伏せる前の `citations` のパス・`cited`・`unknown` |

### 異常系

| 関数 | 失敗 | 検出 | 例外 | 扱い | 出力 | 復旧 |
| --- | --- | --- | --- | --- | --- | --- |
| `_summary`（`once`） | 参照先の公開範囲を照会できない（通信の障害・レート制限） | `Publisher.guarded` の照会 | `gh.GitHubError`（`once` が受け、`_summary` へは出さない） | 終わり方は変えず、結果を返す。`outputs.mvv` の伏せる欄はすべて `WITHHELD`（生の値も、伏せる前の値の一部も出さない）。`record_review` は `clean=None` で呼び、自由記述を持たない `withheld: true` の `mvv-review` の行と、普段どおりの `review_disputed` の行を書く（`WITHHELD` の文を書くと重ねない判定と承認資料の理由が壊れ、何も書かないと #3959 の設計承認ゲートが保留なしと読んで `fast`・`auto` で省き、`count_signals` が見送りを数え落とすため） | 結果の `warnings` に 2 件（結果を伏せた・記録の自由記述を伏せて書いた）。`<原因>` は `once` の固定文で、例外の文を含めない。`redline`・`deferred` の行を書けなければ #3959 のとおり `mvv_record_failed` | 照会が通る次の起動で書き直す。キーの最後の行が伏せた行の間は、`review_holds` がそのキーを保留にし、設計承認ゲートは省かない。同じ head の打ち直しで `_reusable` が真（失敗した起動の最後のラウンドが収束で、未解決の指摘も決めていない足すべき内容も無く、同じ比較基点・同じ識別子）なら、相手役を呼ばずに `_summary(reused=True)` に入り、`record_review` が伏せていない行を書く。それ以外（ラウンドの上限で止まった未収束の起動・未解決の指摘が残る起動・head が変わった打ち直し）は再利用せず相手役を回し、`record_review` はその起動の終わりのジャーナルの行を書く。ジャーナルに残る指摘はどの終わり方でも同じ `key` で伏せていない行が書かれ、保留が外れる（`fixed` なら `deferred` の保留も外れる）。`review_disputed` は失敗した起動で書いた 1 行のままで、次の起動で同じ指摘が見送られても直されても足さない。別の状態ディレクトリで打ち直してジャーナルに無い指摘は伏せた行のまま残り、その PR の設計承認ゲートは人に問い続ける（#3959 の「余分な人の承認は許す」の側に倒す） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_finding_gaps`・`finish` | `mvv_checked` が偽 → NDF の指摘が 0 件でも `mvv_unresolved` で止まる | MVV が無い → 真とみなし今どおり | — |
| `read_by_all_head` | `mvv_checked: true` のラウンドを返す | MVV が無いとき `mvv_checked` を見ない | `mvv_checked` が偽・無いラウンド → None |
| 再利用の経路（`_start`・`_summary`） | 承認済みの MVV があり、同じ head・同じ識別子で `mvv_checked: true` の収束がある打ち直し → 相手役も `_citations` も打たず、`mvv_unresolved` にならず収束を返す | MVV が無い打ち直しは今どおり | `mvv_checked` が偽のラウンドしか無い → 再利用せずラウンドを回す |
| `record_review`・`mvv_outputs` | 偽の秘密を含む理由と本文が伏せられて書かれる。偽の秘密をファイル名に含む根拠の行の誤り → `outputs.mvv.citations` の `path` が伏せられ、`clean` の呼び出しは理由・本文と合わせて 1 回。ジャーナルの行の `path` は生のまま。偽の秘密を項目に置いた根拠の行（`- 根拠: <偽の秘密>（MVV バージョン 11）` の `unknown` と、`- 根拠: <偽の秘密>「…」（MVV 版 11）` の `format`）→ `outputs.mvv.citations` の `cited`・`unknown` と `mvv-validation.jsonl` の `item` に偽の秘密が無く、`format` の `cited` に引用文と版の表記が無い。偽の秘密を本文の先頭に引いた相手役の MVV の指摘（見送りとレッドライン）→ `mvv-validation.jsonl` と `mvv-signals.jsonl` のどの欄にも偽の秘密が無く、`key` は 16 進 64 文字 | 伏せた後が同じ理由の打ち直しは重ねない。同じ指摘の打ち直し（id が変わる別の起動を含む）は `key` が同じで、両ファイルとも行を足さず、`count_signals` も 1 件のまま | `judged_mvv` の違う行 → 書かない・載せない |
| `record_review`（`clean=None`）・`review_holds` | 見送り・レッドライン・`by: ndf` の行 → `mvv-review` は `withheld: true` で `reason`・`text`・`cited` の `item` を持たず、`key`・`id`・項目の番号・終わり方・版・`head` を持つ。`review_disputed` は普段どおり 1 行。`clean` は呼ばない。最後の行が伏せた `fixed` のキー → 保留「PR #N の <項目>（記録の自由記述が未完了）」 | 伏せた行の後に同じ（head・終わり方・版）の伏せていない行 → 書く。伏せていない行の後に同じ（head・終わり方・版）の伏せた行 → 書かない。伏せた行の後に同じ伏せた行 → 書かない。旧版の伏せた `fixed` が最後の行のキー → 記録の未完了で保留しない | `redline` の伏せた行の追記が `OSError` → `ReviewWrite.missing` に載る（#3959 のとおり `mvv_record_failed`） |
| `record_key` | 大文字小文字の混ざった偽の秘密（`AbCd…`。環境変数に置く）を含む本文 → キーに偽の秘密も、その小文字の形も無い。同じ本文の空白と大文字小文字だけを変えた本文 → 同じキー | 偽の秘密が `review.body-match` の切り詰めの幅をまたぐ位置にある本文 → キーにも記録のどの欄にも偽の秘密の前半・後半の断片が無く、打ち直しでキーが変わらない。公開の書き先で、別々の非公開リポジトリへの参照だけが違う 2 つの見送り → `clean` の後の理由は同じでもキーは 2 つで、`review_disputed` が 2 行。一方を `fixed`、もう一方を `deferred` にした打ち直し → `mvv-review` の最後の行がキーごとに別で、`fixed` が他方の `deferred` を解除しない（#3959 の保留）。同じ指摘の参照先を公開から非公開へ変えて打ち直す → `clean` の後の理由は変わり `mvv-review` を 1 行足すが、キーは同じで `review_disputed` を足さない | — |
| 確かめから記録まで（`_citations`・`ingest_citations`・`_summary`） | 公開の書き先で、同じ公開の別リポジトリへの参照を `cited` と見送りの理由とレッドラインの本文に含む PR を 1 ラウンドで収束させる → 偽の GitHub の参照先の `visibilities` の呼び出しが、`_citations` と `ingest_citations` からは 0 回（照合キーを作る経路は照会しない）。`mvv_outputs` と `record_review` を合わせて 1 回で、`record_review` からは 0 回。記録のコメントの照会は `Hub.clean` の今の振る舞いのまま（使い回しは #4156） | 確かめを 2 回（ラウンドと終わり方の判定の直前の新しい head）打っても、確かめの経路の照会は 0 回のまま。再利用の経路（記録のコメントを投稿し直さない）では、`mvv_outputs` と `record_review` を合わせて 1 回 | 偽の `visibilities` が `gh.GitHubError` を送出する起動で、見送り 1 件・レッドライン 1 件・根拠の行の誤り 1 件を収束させる → 結果が返り、`outputs.mvv` の理由・本文・`citations` のパス・`cited`・`unknown` が `WITHHELD` で生の値が無く、注意が 2 件、`visibilities` の呼び出しは 1 回だけ。`mvv-validation.jsonl` に `withheld: true` の行が 3 行（`reason`・`text`・`by: ndf` の `item` の欄が無く、`WITHHELD` の文も無い）、`mvv-signals.jsonl` に `review_disputed` が 1 行。続けて `fast` の設計承認ゲートの `_judged` を打つ → LLM が「従う」でも `skip` は偽で、`holds` に `redline`・`deferred` と記録の未完了が載る。続けて照会が通る偽で同じ head を打ち直す → 再利用の経路で `mvv-validation.jsonl` に伏せていない行が 3 行足され、`mvv-signals.jsonl` は増えず、`review_holds` から記録の未完了が外れる（`redline`・`deferred` の保留は残る）。照会に失敗した起動で見送った指摘を、次の起動（head が変わる）で修正担当が直す → `mvv-review` の `fixed` が足されて `deferred` の保留が外れ、`review_disputed` は増えず、`count_signals` の `unknown` はその指摘で 1 のまま。照会に失敗した起動を 2 回続ける → 2 回目は両 JSONL に行を足さない。伏せていない行の後に照会に失敗した起動を同じ head・同じ状態で打つ → 伏せた行を足さない。サーバーのエラーメッセージに偽の非公開リポジトリ名を含む `gh.GitHubError`（`quota` あり・`unreached` 真・どちらでもない）→ `warnings` の原因がそれぞれ「レート制限」「通信の障害」「GitHub が照会を断った（HTTP <status>）」で、結果 JSON のどこにもリポジトリ名が無い。ラウンドの上限で未収束のまま照会に失敗した起動を同じ head で打ち直す → 再利用せず相手役を回し、両 JSONL にはその起動の終わりの行だけが足される |
