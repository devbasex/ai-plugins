# #4090 #4099 の詳細設計: MVV の写し・根拠の行の確かめ・ジャーナルの置き換え

設計文書 [issue-4090-4099-design.md](./issue-4090-4099-design.md) の決定を、#4076 の詳細設計（[issue-4076-design-detail.md](./issue-4076-design-detail.md)）に対する変更として、ファイルと関数の粒度へ下ろした文書である。ここに書かない関数と規則は #4076 の詳細設計のままである。前提のコードは #4076 と同じく、スプリントブランチ `sprint/p12-lcrfast-r1` が main へ入った後の形である。

## 地図

#4076 の詳細設計の地図に足す・変える行だけを書く。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | `adapters.markdown`（足す） | モジュール | `body_path`（新設）・`parse_citation`（新設）・`fenced_lines`（#4076 の新設を置き換え）・`citation_errors`（#4076 の新設を置き換え） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の設定・本文・記録の読み書き | `git`・`mvv_decide` | `Snapshot`（欄を足す） | `current`（置き換え）・`_write_verified`（新設）・`record_review`（引数を足す） |
| `src/ndf/domains/records/preflight.py` | 変更 | 手順 0 の確かめ | `mvv_decide` | モジュール | `mvv_body`（`body_path` を呼ぶ。返す値は同じ） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv`・`git` | モジュール | `_mvv_current`（取得を置き換え） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv` | `RunConfig`・`_Run`・`_Prepared`（欄を足す） | `_start`・`_prepare_round`・`_merge_base`・`_citations`（置き換え）・`_base_lines`（新設）・`_settle_resolved`・`_summary`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal` | `ingest`・`enter`・`carry`（変更）・`restate`（新設） |
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
| `Snapshot` | `records/mvv.py` | 変更 | #4076 の欄に `commit: str`・`principles_sha256: str`・`principles_path: Optional[Path]` を足す | `commit` は手順 (1) で解いたコミット（状態によらず置く）。`principles_*` は共通原則の写し（承認済みのときだけパスを置く） |
| `body_path` | `records/mvv_decide.py` | 新設 | `body_path(decl: Mapping[str, Any]) -> str` | 設定の最上位の `body`（文字列でなければ表 `ndf-files` の `mvv-body` の既定）を `.ndf/` からの相対で解き、リポジトリの根からの POSIX のパスを返す。絶対パス・`..` を含む値・空は `MvvError` |
| `current` | `records/mvv.py` | 変更 | `current(root: Path, ref: str) -> Snapshot`（同じ） | 下の手順 |
| `_write_verified` | `records/mvv.py` | 新設 | `_write_verified(path: Path, data: bytes, sha256: str) -> Path` | `path` が無いか、中身の SHA-256 が `sha256` と違えば、`data` を同じディレクトリの一時ファイルへ書いて `os.replace` で置き換える。同じなら書かない |
| `_mvv_current` | `records/cli.py` | 変更 | （同じ） | `git.Git(root).fetch_branch_or_raise(<リモート>, <ブランチ>)` を打ってから `current(root, <リモート>/<ブランチ>)` を呼ぶ |

- `current` の手順（#4076 の手順を置き換える）
  1. `git rev-parse --verify <ref>^{commit}` でコミットへ解く。失敗は `MvvError`（`precondition`）
  2. `git cat-file -e <コミット>:.ndf/mvv.json` が失敗すれば `absent`（`commit` と共通原則の sha256 は置く）
  3. 設定を `run_bytes` で読み、`decl_from` で検査する。`body_path(decl)` で本文のパスを決め、本文を `run_bytes` で読む。読めなければ `MvvError`
  4. `mvv_decide.status` で状態を決める。`mismatch` は `MvvError`（「ベースの MVV の本文が承認の記録と合わない: <本文のパス>」）。`unapproved` はそのまま返す
  5. 承認済みなら、本文の写しと共通原則の写し（`mvv.principles` のバイト列から sha256 を求め、`principles-<先頭 12 文字>.md`）を `_write_verified` で書き、`items_of` で項目の一覧を作る。`blobs` の鍵は設定のパスと `body_path` のパス
  - 副作用: `git rev-parse`・`git cat-file` を 1 回ずつ、`git show` を 2 回（#4076 と同じ）。写しのファイルを 2 つまで読み、違えば書く
- `drive._start`（#4076 の手順を置き換える）: `remote = g.remote()`、`sync_worktree(g, cfg.branch, remote, base_branch=cfg.base)` の後に `snap = current(root, f"{remote}/{cfg.base}")` を呼び、`cfg = cfg._replace(base_commit=snap.commit)` とする。`RunConfig` に `base_commit: Optional[str] = None` を足す。以後の `_changed(run.g, cfg.base_commit, …)`・`_merge_base`（`run.g.merge_base(cfg.base_commit, head)`）・差分（`f"{scope.base or merge_base}...HEAD"`）は名前を Git に渡さない
- `cmd_scope`（`review-scope`）: 手元の `<リモート>/<ベース>` を `git rev-parse` で 1 回コミットに解き、そのコミットで `git ls-tree` を打つ。本文のパスは `ls-tree` で得た設定の blob を `git cat-file` で読み `body_path` で解く（設定の blob が最後のラウンドの記録と同じなら、記録のパスを使い読まない）。共通原則の sha256 はプラグインのファイルから求める。ネットワークに出ず写しを書かない（#4076 のまま）

### 異常系の処理

#4076 の表の行を次のとおり置き換える・足す。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `current` | 設定はあるが本文を読めない | 設定の `body` のパスに本文が無い・`git show` の失敗 | `git show` の失敗 | `MvvError` | `refused`（`precondition`）、`summary` に `body_path` のパス | 直して打ち直す |
| `body_path` | パスが不正 | 絶対パス・`..`・空 | 値の検査 | `MvvError` | `refused`（`precondition`）、`summary` に値 | 設定を直して打ち直す |
| `current` | 本文の sha256 が合わない | 承認の後に本文が変わった | sha256 の比較 | `MvvError`（`mismatch` を状態として返さない） | `ndf records mvv current`・`ndf cross-review run` とも `refused`（`precondition`）、`summary` に「ベースの MVV の本文が承認の記録と合わない」とパス | 本文を戻すか承認し直して打ち直す。cross-review は相手役を起動せず、状態ファイルとジャーナルを書かない |
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
| `current` | 既定外のパスで承認したリポジトリ → 承認済み・`blobs` にそのパス。`commit` が解いたコミット | 同じ中身の写しは書き直さない。違う中身の写しは検証済みの中身になる | `mismatch` → `MvvError`。`unapproved` → 状態を返す |
| `_mvv_current` | `--single-branch` で別のブランチを clone した作業場所で新しい版を返す | — | 取得の失敗 → `refused` |
| `drive._start` | ローカルの `main` が古くても、merge-base・差分・`Basis.base` がリモートのコミットから作られる | — | `MvvError` → 相手役を起動しない |

## 基準 5 と識別子

満たす決定: 決定 11・15（#4076 の詳細設計の「指摘の基準 5」の節と「再利用の条件に MVV を足す」の表を置き換える）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_lines` | `criteria_decide.py` | 変更 | （#4076 と同じ） | `mvv.state == "approved"` のときだけ基準 5 を足す。`mismatch` は `current` が止めるので、ここには来ない |
| `mvv_criterion` | `review-criteria.json` | 変更 | 文 | 「プロジェクト MVV（版 {version}。本文は {path}）の Mission・Vision・Value の文に反する。必ず人の承認が要る操作は、本文の表と NDF の共通原則（{principles}）の C1〜C8 で決める」 |
| `Basis.mvv` | `scope_decide.py` | 変更 | 文字列 | 識別子 `"<state>:<version>:<sha256>:<共通原則の sha256>"`（承認済みでなければ `"<state>:::<共通原則の sha256>"`） |

- 識別子の無い前の記録は、今回が `absent` のときだけ同じとみなす（#4076 のまま。共通原則の部分は照らさない）
- 壊し方（テストで落とす）: 共通原則の本文を変えた後の起動が前の収束を返す。基準 5 の指示に共通原則の写しのパスが無い

## 根拠の行の確かめ

満たす決定: 決定 2・3・4・8・12・14（#4076 の詳細設計の「根拠の行の確かめ」の節を置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `fenced_lines` | `records/mvv_decide.py` | 置き換え | `fenced_lines(text: str) -> frozenset[int]` | `adapters.markdown.code_lines(text)` が真の行の番号（1 始まり）を返す。自前の走査をしない |
| `parse_citation` | `records/mvv_decide.py` | 新設 | `parse_citation(line: str, decision: bool) -> Optional[Citation]` | 1 行を候補か決め、候補なら（項目の並び・版か None）を返す。候補でなければ None |
| `citation_errors` | `records/mvv_decide.py` | 置き換え | `citation_errors(lines: Sequence[tuple[str, int, str, bool]], catalog: Mapping[int, Sequence[str]]) -> list[dict[str, Any]]` | 確かめる行（パス・行の番号・本文・決定の根拠の行か）を `parse_citation` に通し、誤りを返す |
| `_citations` | `crossreview/drive.py` | 置き換え | `_citations(run: _Run, prep: _Prepared) -> Optional[list[dict[str, Any]]]` | 下の手順。確かめられなければ None |
| `_base_lines` | `crossreview/drive.py` | 新設 | `_base_lines(hunks: Sequence[Hunk], head_line: int) -> Optional[int]` | head の変わっていない行の番号を、hunk の行数の差からベースの行の番号へ写す。足した行なら None |
| `_Prepared` | `crossreview/drive.py` | 変更 | 欄 `docs: Mapping[str, str]`（`_changed` が読んだ head の Markdown の本文）と `full_diff: Optional[Path]`（範囲が `FULL` で起点が merge-base のときだけ、書いた差分のファイル）を足す | — |
| `_settle_resolved` | `crossreview/drive.py` | 変更 | （同じ） | `row.get("by") == "ndf"` の行を飛ばす |

- `parse_citation`
  - 候補: 左から空白と `- ` を除いた先頭が `根拠:` で、`MVV` を含むか、`decision` が真（`### 決定 <数>` の見出しの下で次の見出しまでの行）
  - 解析: `根拠:` の後ろから、`「` と対応する `」` の間（入れ子を数える。閉じなければ行末まで）を除く。残りで最初の `（MVV バージョン (\d+)` を探し、その前を ` / ` と `・` で分けて前後の空白を除いた値を項目にする。`スプリント` で始まる項目と `根拠なし` は照らさない（#4076 のまま）
  - 版を読めない候補は版を None で返す（`（MVV 版 11）`・版の欠け・`（MVV なし）` を含む）
- `citation_errors` の返す 1 件: `{"path", "line", "cited", "kind", "version", "unknown"}`。`kind` は `format`（版が None。`version` は None・`unknown` は空）・`version`（版が `catalog` に無い）・`unknown`（版はあり、その版の一覧に無い項目がある）。`catalog` は #4076 のまま `{ベースの承認済みの版: Snapshot.items}` の 1 件
- `_citations` の手順
  1. 鍵 `(prep.merge_base, prep.head, 識別子)` が `run.citations` にあれば、その結果を返す（Git を打たない）
  2. 全差分: `prep.full_diff` があればそのファイルを読み、無ければ `git diff <merge_base>...<head>` を 1 回打つ
  3. 差分の各ファイルの hunk から、足した行（パス・新しい側の番号）と hunk の行数を作る
  4. head の本文: `prep.docs` にあるものを使い、無い候補のファイル（Markdown でないファイル）は `_committed_texts(run.g, …)` で 1 回にまとめて読む
  5. 各ファイルで `fenced_lines` の外にある候補の行のうち、(a) 足した行と、(b) 足していない行を集める。(b) を持つファイルだけ、ベース（`merge_base`。rename は元のパス）の本文を `_committed_texts` で 1 回にまとめて読み、`_base_lines` で写したベースの行がベースの `fenced_lines` に入る行だけを残す
  6. 決定の根拠の行かは head の本文の ATX の見出し（`adapters.markdown.atx_headings`）で決める
  7. `citation_errors` を当て、成功した結果を `run.citations` に置き、`run.checked_head = prep.head` とする
  - `GitError`・`OSError`・`UnicodeDecodeError` は None を返して注意に載せる。`run.citations` と `checked_head` は変えない
- `ingest_citations`: #4076 のまま（`errors` が None なら既存の `by: ndf` の行を解決させない）
- ラウンドの記録: 書くときに `mvv_checked = (承認済みの MVV が無い) or (run.checked_head == そのラウンドの head)` を足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_citations` | 差分か本文を読めない | `git diff`・`git show` の失敗 | `GitError`・`OSError` | None。使い回しに置かず、`checked_head` を変えない | 結果の `warnings`。最後の head で起きれば `mvv_unresolved` で止まる（NDF の指摘が 0 件でも） | 次のラウンドか打ち直しで読み直す |
| `parse_citation` | 版を読めない | 書式の外れ・版の欠け | 正規表現の不一致 | 候補なら `format` の誤り。候補でなければ拾わない | NDF の指摘（`major`） | 直せば次の確かめで消える |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fenced_lines` | 囲みの間の行とフェンスの行 | 4 字で開き 3 字の行を含む例・リストの中のフェンス・チルダ・閉じないフェンス | — |
| `parse_citation` | ` / ` の 2 項目・`・` と引用文の 2 項目 | 引用文の中の `（` と ` / `・閉じない `「`・版の括弧の後ろの `。STD1` | `（MVV 版 11）`・版の欠けの決定の根拠の行・`（MVV なし）` → 版 None。決定の外の `根拠: 実測` → 候補でない |
| `_base_lines` | hunk の前後の行を写す | ファイルの先頭と末尾の hunk | 足した行 → None |
| `_citations` | 足した行と、フェンスの行だけを消して本文に出た行を拾う | ベースから本文の変わらない行を拾わない。同じ鍵の 2 回目は Git を打たない。範囲が狭まったラウンドでは `git diff` を 1 回打つ | 失敗 → None、`checked_head` が変わらない |
| `_settle_resolved` | 普通の行は今どおり `fixed` | `by: ndf` の行は `unresolved` のまま | — |

## ジャーナルと引継ぎ

満たす決定: 決定 5・6（#4076 の詳細設計の「ジャーナルとレッドライン」の節の `enter` の行と「行のキーの名前は実装で決める」を置き換える）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FindingJournal.ingest`・`enter` | `journal.py` | 変更 | 引数 `mvv: Optional[str]`（識別子）を受ける（#4076 で足す引数） | 分類を置くときに `judged_mvv` を書き、`mvv_pinned` で固定するときは前の重さを `unpinned_severity` に残す。出し直された行の `judged_mvv` が今回と違えば、`restate` してから今回の申告で分類する（終わり方によらない） |
| `FindingJournal.restate` | `journal.py` | 新設 | `restate(self, mvv: Optional[str]) -> list[str]` | `judged_mvv` を持ち今回の識別子と違う行の分類を外し、外した id を返す。`_start` が `carry` の後・最初のラウンドの前に 1 回打つ |
| `FindingJournal.carry` | `journal.py` | 変更 | `carry(self, carried, items: Sequence[str] = (), mvv: Optional[str] = None)` | `Carried` の分類を戻す。`judged_mvv` が今回と同じ件は `mvv_item` で項目を照らし直して戻し、違う件は旧い分類を `mvv_history` に入れて分類なしで戻す |
| `Carried` | `prior_decide.py` | 変更 | 欄 `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv`（どれも既定 None）を足す | — |
| `recorded` | `prior_decide.py` | 変更 | `recorded(remarks) -> tuple[list[Carried], dict[int, Mapping[str, Any]]]` | スレッドの無い件を今どおり返し、スレッドのある件の分類を `thread` → 分類の表で返す |
| `carried` | `prior_decide.py` | 変更 | `carried(threads, tracked, marks: Mapping[int, Mapping[str, Any]] = {})` | スレッドから戻す件に、`marks` の同じスレッドの分類を重ねる |
| `open_block` | `report_decide.py` | 変更 | （同じ） | 1 件に `mvv`・`mvv_pinned`・`redline_items`・`judged_mvv` を足す。`redline` の行と `by: ndf` の行は `_light` でも載せる。自由記述でない値なので `clean` を通さない |

- `restate` の外し方: 旧い `judged_mvv`・`mvv`・`redline_items`・`outcome`・`skipped_mvv` を `mvv_history` へ追記し、`mvv`・`mvv_pinned`・`redline`・`redline_items`・`judged_mvv` を消し、`unpinned_severity` があれば `severity` をそれに戻して消す。終わり方が `fixed`・`deferred` の行は終わり方をそのまま残す（今回の結果と記録の対象からは `judged_mvv` が無いので外れる）。`unresolved` の行は普通の未解決の指摘として残る
- #4084 との統合: `restate` と `enter` の分類し直しは、#4084 の決定 4 の「MVV の再判定」と同じ時点である。後に入る PR が `unkept_severity`・`kept_round` の置き直しを両方の経路で打つ

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `enter` | 旧版の `P1` の行が新版で `Value 5` として出し直される → レッドラインでなく `major`・`judged_mvv` は新版・`mvv_history` に旧い `P1` | 旧版の `fixed` の `Value 5` の行が `mvv` 無しの `minor` で出し直される → `minor`・分類なし | — |
| `restate` | 出し直されない旧版の `redline` の行 → 分類なし・履歴あり | 識別子が同じ行は変えない | — |
| `carry`・`recorded`・`carried` | 空の状態ディレクトリから、`Value 5`・`P1`・`by: ndf` の 3 行が分類つきで戻る。スレッドのある件にも重なる | 識別子の違う件は分類なしで戻る | 一覧の `mvv` が今回の項目の一覧に無い → 外して注意 |
| `open_block` | 3 つの分類を埋める | `minor` のレッドラインも載る | — |

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
| `_summary` | `drive.py` | 変更 | （同じ） | `finish` に `mvv_checked`（承認済みの MVV が無いか、`checked_head` が終わり方を判定する head と同じ）を渡し、`mvv_outputs` と `record_review` に識別子と `hub.clean` を渡す |

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
| `record_review`・`mvv_outputs` | 偽の秘密を含む理由と本文が伏せられて書かれる | 伏せた後が同じ理由の打ち直しは重ねない | `judged_mvv` の違う行 → 書かない・載せない |
