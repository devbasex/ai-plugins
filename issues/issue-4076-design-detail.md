# #4076 の詳細設計: 工程と MVV（写し・指摘の基準 5・根拠の行の確かめ・記録）

設計文書 [issue-4076-design.md](./issue-4076-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。前提はスプリントブランチ `sprint/p12-lcrfast-r1` が main へ入った後の形である。このブランチは #4014 の `impact`・`effective_severity`、#4030 の `fixer_ci_only`、#4013 の修正担当の会話を持つ。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/adapters/git.py                     … Git.run_bytes（元のバイト列を返す）
├── src/ndf/core/converge/worktree.py           … 同期の fetch でベースブランチも取る
├── contracts/data/review-criteria.json         … 基準 5 の文と mvv の指示
├── src/ndf/domains/records/
│   ├── mvv.py                                   … current（写し）・review の記録・共通原則の本文
│   ├── mvv_gate.py                              … principles を mvv.py から読む
│   ├── mvv_decide.py                            … 項目の一覧・根拠の行の確かめ・兆候の種類
│   └── cli.py                                   … ndf records mvv current
├── src/ndf/domains/crossreview/
│   ├── criteria_decide.py                       … 基準 5 を組む
│   ├── drive.py                                 … 写しを得る・根拠の行を確かめる・指示に足す
│   ├── journal.py                               … mvv の行・NDF の指摘・レッドライン
│   ├── finish_decide.py                         … 欠け mvv_unresolved
│   ├── report_decide.py                         … 結果の outputs.mvv
│   ├── scope_decide.py                          … 再利用の条件と確認済みのセクションに MVV を足す
│   └── cli.py                                   … Basis に MVV を渡す
├── src/ndf/progress/gate/checks.py              … レビューのレッドラインと見送りで省かない
└── tests/
    ├── domains/records/test_mvv_current.py *
    ├── domains/records/test_mvv_citations.py *
    ├── domains/crossreview/test_crossreview_{criteria,journal,run,finish}.py
    └── progress/gate/test_gate_checks.py
```

依存の向き: `crossreview` が `records.mvv`（写しと記録）と `records.mvv_decide`（純粋な確かめ）を読む。`records` は `crossreview` を読まない。`mvv_decide` は git もファイルも読まない純粋な関数だけを持ち、`mvv.py` が git とファイルを読んで `mvv_decide` へ渡す。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | `mvv_material_decide.redlines` | モジュール | `items_of`（新設）・`citation_errors`（新設）・`fenced_lines`（新設）・`review_holds`（新設）・`count_signals`（変更） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の設定・本文・記録の読み書き | `git`・`mvv_decide`・`contracts` | `Snapshot`（新設）、モジュール | `current`・`record_review`・`review_rows`・`principles`（新設。`mvv_gate` から移す） |
| `src/ndf/adapters/git.py` | 変更 | Git の起動 | — | `Git`（変更） | `run_bytes`（新設。標準出力をバイト列のまま返す） |
| `src/ndf/core/converge/worktree.py` | 変更 | PR の worktree の同期 | `git` | モジュール | `fetch_head`・`sync_worktree`（変更。`base_branch` があれば同じ 1 回の fetch でベースも取る） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの直前の判定 | `mvv` | モジュール | `principles` を消し `mvv.principles` を呼ぶ。`base_facts`・`_judged` が対象の PR と head を `MvvFacts.review_holds` に渡す |
| `src/ndf/progress/gate/checks.py` | 変更 | 承認ゲートの機械の検査 | — | `MvvFacts`（変更） | `check_review_holds`（新設）・`refusals`（変更） |
| `src/ndf/domains/crossreview/scope_decide.py` | 変更 | 差分の範囲と再利用の条件 | — | `Basis`（変更） | `read_by_all_head`・`reviewed_of`・`state_scope`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` の入口 | `records.mvv` | モジュール | `Basis` を組む箇所に MVV の識別子を渡す。`cmd_scope` は前の `run` のラウンドの記録と `git ls-tree` 1 回で識別子を得る（ネットワークに出ない） |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv` | モジュール | `_mvv_current`（新設） |
| `contracts/data/review-criteria.json` | 変更 | 指摘の基準の文 | — | — | キー `mvv_criterion`・`mvv`（新設） |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 基準と指示の組み立て | `records.mvv.Snapshot` | モジュール | `criteria_lines`・`reviewer_block`・`fixer_block`（変更）・`mvv_item`（新設） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv`・`criteria_decide`・`journal` | `RunConfig`・`_Run`（変更） | `_start`・`_round`・`_review_prompt`・`_fix_prompt`・`_summary`・`_prepare_round`・`_reusable`（変更）・`_citations`（新設） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal` | `ingest`・`enter`・`apply_fix`・`skipped_texts`（変更）・`ingest_citations`（新設）・`handable`（新設） |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | `criteria_decide` | `Finding`（変更） | `_finding_gaps`・`finish`（変更） |
| `src/ndf/domains/crossreview/report_decide.py` | 変更 | 結果 JSON | — | モジュール | `mvv_outputs`（新設） |

## MVV の写し（`current`）

満たす決定: 決定 1・2、受け入れ条件 1・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Snapshot` | `records/mvv.py` | 新設 | `NamedTuple(state: str, version: Optional[int], sha256: Optional[str], path: Optional[Path], items: tuple[str, ...], blobs: Mapping[str, str])` | 承認済みの MVV の写しの結果。`blobs` は読んだ設定と本文のパスから Git の blob の id（`git show` が返した元のバイト列から Python で `sha1("blob <バイト長>\0" + バイト列)` を計算する。Git を打たない。文字列へ変えてから再エンコードした値を使わない）。`absent` は空 |
| `current` | `records/mvv.py` | 新設 | `current(root: Path, ref: str) -> Snapshot` | 参照の MVV の設定と本文を読み、承認済みなら写しを書いて返す |
| `items_of` | `records/mvv_decide.py` | 新設 | `items_of(body: str, principles: str) -> tuple[str, ...]` | 本文と共通原則から実在する項目の一覧を作る |
| `_mvv_current` | `records/cli.py` | 新設 | `_mvv_current(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `ndf records mvv current --base` の結果 JSON |

- `current`
  - 事前条件: `ref` は `<リモート>/<ブランチ>` の形で取得済み（`signals_ref` で作る）
  - 事後条件: 状態が `approved` なら `path` のファイルの中身は `ref` の本文と同じである。その sha256 は `ref` の `.ndf/mvv.json` の最後のバージョンの `sha256` と一致する。`approved` でなければ `path`・`items` は空で、ファイルを書かない
  - 手順: (1) `git rev-parse --verify <ref>^{commit}` で参照をコミットへ解決する。失敗は `MvvError`（`precondition`）。(2) そのコミットに `.ndf/mvv.json` があるかを `git cat-file -e <コミット>:.ndf/mvv.json` で確かめ、無いときだけ `absent` を返す。(3) 解決したコミットから設定（`decl_at` に同じコミットを渡す）と本文を読む。ここでの失敗（本文が無い・読めない・JSON でない・旧い形）はすべて `MvvError`（`precondition`）で、`absent` にしない。`decl_at` の `MvvError` を `absent` に読み替えない
  - バイト列の読み方: `Git.run` は `text=True` で起動し CRLF を LF に変えるので、設定と本文の `git show` には使わない。`adapters/git.py` に、同じ `NO_PROMPT` の環境で標準出力をバイト列のまま返す `Git.run_bytes(*args) -> bytes` を足し、`current` は設定と本文をこれで 1 回ずつ読む。同じバイト列から `blobs` の id を計算し、UTF-8 で復号して解析する（設定は `mvv.py` に足す `decl_from(raw: bytes, ref, root)` で `decl_at` と同じ検査をかける。`decl_at` は `run_bytes` の結果を `decl_from` へ渡す形にする）。本文の sha256 も同じバイト列から計算する。解析と id の計算で別々に Git を打たない
  - 副作用: `git rev-parse` と `git cat-file` を 1 回ずつ、`git show` を 2 回（設定と本文。どちらも `run_bytes`）。CLI の `_mvv_current` だけは、読む前に `git fetch <リモート> <ブランチ>` を 1 回打つ（cross-review は下の同期でベースブランチも取得した参照を渡すので打たない）。写しの置き場に 1 ファイルを書く（同じ名前があれば書かない）
- cross-review の `run` でのベースブランチの取得（`core/converge/worktree.py`）: 今の `fetch_head` は PR の head ブランチだけを取得するので、手元のベースの参照が古いと、ベースで新しい MVV が承認された後も `current` が旧版を読み、同じ head の収束を再利用してしまう。これを防ぐため、`fetch_head(g, head_branch, remote=None, base_branch: Optional[str] = None)` と `sync_worktree(g, head_branch, remote=None, base_branch: Optional[str] = None)` に引数 `base_branch` を足す。`base_branch` があれば、既存の 1 回の `git fetch -- <リモート> +refs/heads/<head>:refs/remotes/<リモート>/<head>` に refspec `+refs/heads/<base_branch>:refs/remotes/<リモート>/<base_branch>` を並べて 1 回で取る（Git の起動もネットワークの往復も増やさない）。`base_branch` が `head_branch` と同じか None なら今と同じ 1 本だけを取る
  - 手順: `drive._start` は `remote = g.remote()` を 1 回求め、`worktree.sync_worktree(g, cfg.branch, remote, base_branch=cfg.base)` を打つ。同期が成功した後に `current(root, mvv_decide.signals_ref(cfg.base, remote))` を呼ぶ（この参照は直前の fetch で更新したリモート追跡参照で、取得し直さない）。`crossreview/cli.py` の同期のコマンド（`worktree.sync_worktree(git.Git(Path(args.worktree)), args.branch)`）も `--base` を受けるときは同じく `base_branch` を渡す（後に打つ `review-scope` が読む参照を新しくする）
  - 取得の失敗: ベースの refspec も同じ `git fetch` に入るので、ベースを取れないときは同期ごと失敗し、今の同期の失敗と同じく `GitError` で `run` が止まる。古いベースの参照を読んで続けない
  - 他の呼び手（`crossrefactor` の `workspace`・`final_gate`・`drive`、`crossreview/push.py` の `_rebase_onto_origin`）は `base_branch` を渡さず、今と同じ
- `items_of`
  - 事後条件: `## Value` の下の行頭の `<番号>.` から `Value <番号>` を作る。`## 必ず人の承認が要る操作` の表と共通原則の表から `P<番号>`・`C<番号>` を作る（`mvv_material_decide.redlines` を使う）。`## Mission`・`## Vision` があれば `Mission`・`Vision` を作る。重複を除き、出た順

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `current` | 参照を解決できない | 取得していない・名前の誤り | `git rev-parse --verify` の失敗 | `MvvError` を呼び出し元へ返す（`absent` にしない） | `refused`（`precondition`）、`summary` に参照と理由 | 取得し直して打ち直す |
| `current` | 設定が無い | 解決したコミットに `.ndf/mvv.json` が無い | `git cat-file -e` の失敗（参照の解決は成功） | 状態 `absent` を返す | `ok`・`outputs.mvv.state: absent` | 無し |
| `current` | 設定はあるが本文を読めない | `.ndf/mvv.md` が無い・`git show` の失敗 | `git show` の失敗 | `MvvError` を呼び出し元へ返す | `refused`（`precondition`）、`summary` に読めないファイル | 直して打ち直す |
| `current` | 本文の sha256 が合わない | 承認の後に本文が変わった | sha256 の比較 | 状態 `mismatch` を返し写しを書かない | `ok`・`state: mismatch` | 無し |
| `current` | 設定が壊れている・旧い形 | JSON でない・`_refuse_legacy` | `decl_at` | `MvvError` を呼び出し元へ返す | `refused`（`precondition`）、`summary` に理由 | 設定を直して打ち直す |
| `_mvv_current` | 取得に失敗した | ネットワーク・リモートの名前 | `git fetch` の終了コード | 古い参照を読まずに止める | `refused`（`precondition`）、`summary` に取得の誤り | 打ち直せる |
| `current` | 写しを書けない | 保存先の権限・容量 | `OSError` | 呼び出し元へ返す | `refused`（`precondition`） | 打ち直せる（同じ名前は書き直さない） |
| `drive._start` | `current` が `MvvError` か `OSError` を投げた | 上の 4 行のどれか | `_start` の呼び出し | 相手役も修正担当も起動せず、起動を `refused`（`cause.kind: precondition`）で終える。MVV を抜いて続けない（設計の決定 1） | `ndf cross-review run` の `refused`、`summary` に `current` の誤りと直すファイル | 直して打ち直す。状態ファイルとジャーナルは書かない |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SNAPSHOT_DIR` | 定数 | `records/mvv.py` | `snapshots`（`_state_dir()` の下） |
| 写しの名前 | 規則 | `records/mvv.py` | `v<版>-<sha256 の先頭 12 文字>.md` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 複数のレーンの `current` | 写しのファイル | 一時ファイルへ書いて `os.replace`（同じ中身なので後勝ちでよい） | 名前が中身で決まるので、何度打っても同じファイル |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `current` | `mvv.decl_at`・`mvv_decide.latest`・`mvv_decide.status`・`signals_ref` | `grep -n "def " plugins/ndf/src/ndf/domains/records/mvv*.py` | 使う |
| `items_of` | `mvv_material_decide.redlines`・`mvv_gate.principles` | 同上 | `redlines` を使う（P・C の番号）。`principles` は `mvv_gate` が `mvv` を読むため `mvv.py` へ移す（循環を作らない）。Value の番号を拾う関数は無いので足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `current` | ベースの承認済みの本文から写しを書き、版 11・項目を返す。作業場所の本文が違っても写しはベースの中身 | 同じ中身で 2 回打つとファイルは 1 つ | 解決したコミットに設定が無い → `absent`。解決できない参照・設定はあるが本文が無い → `MvvError`（`absent` にならない）。sha256 が違う → `mismatch` で書かない。壊れた設定 → `MvvError` |
| `sync_worktree`・`fetch_head` | `base_branch` を渡すと 1 回の `git fetch` に head とベースの 2 本の refspec が並び、リモート追跡参照のベースが新しくなる。`drive._start` はその後に `current` を `<リモート>/<ベース>` で呼ぶ（手元のベースが古く、リモートで新しい MVV が承認済みのとき、新しい版を読み、前の収束を再利用しない） | `base_branch` が None か head と同じ → refspec は 1 本（今と同じ） | ベースを取れない → 同期ごと失敗し、`current` を呼ばない |
| `items_of` | Value 1〜17・P1〜P4・C1〜C8・Mission・Vision | `## Value` の下の入れ子の箇条（`    - `）を番号にしない | `## Value` が無い本文 → Value の項目が 0 件 |

## 指摘の基準 5

満たす決定: 決定 3、受け入れ条件 2・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_lines` | `criteria_decide.py` | 変更 | `criteria_lines(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | `mvv` が承認済みのときだけ基準 5 を足す |
| `reviewer_block` | `criteria_decide.py` | 変更 | `reviewer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | 基準 5 があるとき `mvv` のキーの書き方の文を足す |
| `fixer_block` | `criteria_decide.py` | 変更 | `fixer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | 基準 5 と、写しを読んで反するかを確かめ直す文を足す |
| `mvv_item` | `criteria_decide.py` | 新設 | `mvv_item(value: object, items: Sequence[str]) -> Optional[str]` | 相手役の `mvv` が項目の一覧にあればその値、無ければ None |

- `criteria_lines`: `mvv` が None か `state != "approved"` なら今と同じ行。基準 5 の文は `review-criteria.json` の `mvv_criterion`（`{version}`・`{path}` を埋める）
- `reviewer_block`: 基準 5 があるときだけ、`mvv.reviewer`（「基準 5 の指摘は mvv に反する項目を 1 つ書き、impact に反する文と利用者に起きることを書く。必ず人の承認が要る操作に当たるものは P・C の番号を mvv に書く。「出し直さない」の一覧にある指摘でも、必ず人の承認が要る操作に当たるなら P・C の番号を mvv に書いて出す」）を足す。見送りの抑制は通常の指摘にだけ掛け、レッドラインの申告は抑えない
- 指示の JSON の形（`drive._review_prompt` の最後の行）には、基準 5 があるときだけ `"mvv": "<基準 5 のとき反する項目 か 空>"` を足す

### 再利用の条件に MVV を足す

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Basis` | `scope_decide.py` | 変更 | `NamedTuple(base, reviewers, focus, mvv: Optional[str] = None)` | `mvv` は MVV の識別子 `"<state>:<version>:<sha256>"`（承認済みでなければ `"<state>::"`） |
| `read_by_all_head` | `scope_decide.py` | 変更 | （同じ） | ラウンドの記録の `MVV` が `basis.mvv` と違えば None（全差分を読み直し、収束を再利用しない） |
| `reviewed_of` | `scope_decide.py` | 変更 | `reviewed_of(state: Mapping[str, Any], mvv: Optional[str] = None) -> set[str]` | 状態ファイルの `reviewed_mvv` が `mvv` と違えば空の集合（前の MVV で通ったセクションを今回の基準では通っていないとみなす） |

- 守る振る舞い: ラウンドの記録と詳細レビュー済みのセクションと見送りに、その時の MVV の識別子（`"<state>:<version>:<sha256>"`）をひも付ける。今回の識別子と違う記録は、収束の再利用・詳細レビュー済み・「出し直さない」の一覧のどれにも使わない（前の MVV の基準で通ったものを、今回の基準 5 で通ったとみなさない）
- 識別子の無い前の記録（この変更より前の状態ファイル）は、今回が `absent` のときだけ同じとみなす
- `run` は起動の始めの同期でベースブランチも取得し、取得した参照から識別子を得る。`review-scope` はネットワークに出ず写しも書かず、手元の参照の MVV のファイルの blob の id を最後のラウンドの記録と照らして識別子を得る。確かめられなければ、狭い範囲を返さず全差分（`FULL`）を返す
- 壊し方（テストで落とす）: MVV の版が上がった後の起動が前の収束を返す。前の MVV で詳細レビューを通ったセクションが `unreviewed` に並ばない。前の MVV の見送りが「出し直さない」に載る。`review-scope` がネットワークに出るか写しを書く
- 記録のキーの名前・blob の id の取り方（元のバイト列から作る）・Git の起動の回数の削り方は、実装（`tdd-cycle`）で走らせて決める
### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `mvv_criterion` | 文 | `review-criteria.json` | 「プロジェクト MVV（版 {version}。本文は {path}）の Mission・Vision・Value の文に反する」 |
| `mvv_number` | 定数 | `review-criteria.json` | `"5"` |
| `mvv.reviewer`・`mvv.fixer`・`mvv.citation` | 文 | `review-criteria.json` | 相手役・修正担当への文と、根拠の行の誤りの指摘の文（`{path}`・`{line}`・`{cited}`・`{version}`） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `criteria_lines` | 承認済みで基準 5 が番号の順に出る（3 の重点と並ぶ） | `mvv` が None・`absent` で今と同じ行 | — |
| `mvv_item` | `Value 5` → `Value 5` | 前後の空白を除く | 一覧に無い `Value 99`・文字列でない値 → None |
| `read_by_all_head` | MVV の識別子が同じ → 今の振る舞い | 記録に `MVV` が無く今回が `absent` → 同じとみなす | 版か sha256 が違う・今回だけ承認済み → None |
| `reviewed_of`・`state_scope` | `reviewed_mvv` が同じ → 記録のセクション | `reviewed_mvv` が無く今回が `absent` → 記録のセクション | 識別子が違う → 空。設計の PR で全セクションが `unreviewed` に並ぶ |
| `cmd_scope` | 手元の参照の MVV が最後のラウンドと同じ → そのラウンドの識別子で範囲を決める。ネットワークに出ず写しを書かない | MVV の無いプロジェクト → 今と同じ範囲 | 確かめられない → `FULL` |
| `current` の識別子 | `cmd_scope` が同じ MVV から同じ識別子を得る | 改行の形（CRLF）でコミットした設定でも同じ | — |

## 根拠の行の確かめ

満たす決定: 決定 4、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `citation_errors` | `records/mvv_decide.py` | 新設 | `citation_errors(added: Sequence[tuple[str, int, str]], catalog: Mapping[int, Sequence[str]]) -> list[dict[str, Any]]` | 足した行（パス・行の番号・本文）の根拠の行から、引いた版の項目の一覧を `catalog` で引き、版の誤りとその版に実在しない項目を返す |
| `fenced_lines` | `records/mvv_decide.py` | 新設 | `fenced_lines(text: str) -> frozenset[int]` | 文書の全体から、コードのフェンス（行頭の空白の後の ` ``` ` か `~~~`）の内側とフェンスの行の番号（1 始まり）を返す |
| `_citations` | `crossreview/drive.py` | 新設 | `_citations(run: _Run, prep: _Prepared) -> Optional[list[dict[str, Any]]]` | PR 全体の差分の足した行を集め、フェンスの内側を除き、`citation_errors` の結果を NDF の指摘の形にする。確かめられなければ None |
| `ingest_citations` | `crossreview/journal.py` | 新設 | `ingest_citations(self, errors: Optional[Sequence[dict[str, Any]]], round_no: int) -> frozenset[str]` | NDF の指摘をジャーナルへ入れ、`by: ndf` の行の終わり方を確かめの結果で決め、触ったキーを返す |

- `citation_errors`
  - 根拠の行は、本文を左から空白と `- ` を除いた先頭が `根拠:` で、`（MVV バージョン <数>` を含む行。フェンスの内側の行は呼び手が `fenced_lines` で除く
  - 項目は `根拠:` の後ろから括弧の前までを ` / ` で分けたもの。`スプリント` で始まる項目と `根拠なし` は照らさない
  - 返す 1 件: `{"path", "line", "cited": "<行の項目と版>", "version": <引いた版>, "unknown": ["<その版に実在しない項目>"]}`。引いた版が `catalog` に無ければ版の誤りで、`unknown` は空にする（照らす一覧が無い）。版が `catalog` にあり、項目が全部 `catalog[版]` にあれば返さない
  - `catalog` は `{ベースブランチの承認済みの版: Snapshot.items}` の 1 件だけである（要求仕様の前提 1）。PR が MVV を変えても head の版を入れない。版と項目を別々に照らさず、必ず版から一覧を引く
- `fenced_lines`: フェンスの開きと同じ文字で 3 字以上の行で閉じる。閉じないまま終われば末尾までを内側とする
- `_citations`
  - 差分は `prep.diff`（相手役向け。継続のラウンドでは前の head からに狭まる）を読まない。`git diff <prep.base>...<prep.head>`（`prep.base` はその起動の merge-base）を別に打ち、PR 全体の差分を読む。範囲はラウンドによらずこの 1 つで、詳細レビューを通ったセクションの有無・前の head の有無で変わらない
  - unified diff の `+` の行（`+++` を除く）から、パス・新しい側の行の番号・本文を作る。根拠の行の候補（`citation_errors` の行の規則に合う行）を持つファイルだけ `git show <prep.head>:<パス>` で head の本文を読み、`fenced_lines` の番号に入る行を除く（hunk が既存のフェンスの途中から始まっても内外を誤らない）
  - 指摘の `text` は `review-criteria.json` の `mvv.citation` から作り、行の番号を含めない。行がずれても同じキーで出し直されるように、パスと引いた本文だけで作る
  - `git diff`・`git show` の失敗は None を返し、注意に載せる（空の一覧と区別する）
- `ingest_citations`: `errors` の各件を `by: ndf` の行として入れる（新しいキーは新しい行。既にあるキーは、終わり方が `fixed`・`deferred` でも `unresolved` に戻す）。`errors` に無いキーの `by: ndf` の行は `fixed` にする。`errors` が None（確かめられなかった）なら、既にある `by: ndf` の行を `fixed` にせず、`fixed` の行も `unresolved` に戻す（確かめていない head で解決を残さない）
- `apply_fix`: 修正担当の結果のうち `by: ndf` の行への `fixed`・`deferred` は行の終わり方を変えない（理由は `fixer_reason` に残す）。解決は次の `ingest_citations` だけが決める
- `drive._round`・`drive.drive`: 毎ラウンド相手役を起動する前に `_citations` → `ingest_citations` を打つ。加えて、終わり方を判定する直前（最後のラウンドの修正を送った後、または収束と判定する前）に、その時点の head で `_prepare_round` を通さず `_citations` → `ingest_citations` をもう 1 回打つ。これで修正担当の最後の修正も機械が確かめ、確かめられなければ `by: ndf` の未解決が残って `mvv_unresolved` で止まる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_citations` | 差分か head の本文を読めない | `git diff`・`git show` の失敗 | `GitError`・`OSError` | None を返し注意に載せる。`ingest_citations` が `by: ndf` の行を解決させない | 結果の `warnings`。最後の確かめで起きれば `mvv_unresolved` で止まる | 次のラウンドか打ち直しで読み直す |
| `citation_errors` | 版が数でない | `（MVV バージョン x` | 正規表現の不一致 | 根拠の行として拾わない | 無し | — |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `citation_errors` | 引いた版が `catalog` に無い | 1 件（版の誤り。`unknown` は空） |
| `citation_errors` | 引いた版が `catalog` にある・その版の一覧に無い項目がある（ほかの版にはある項目も含む） | 1 件（`unknown` に項目） |
| `citation_errors` | 引いた版が `catalog` にある・項目が全部その版の一覧にある | 返さない |
| `ingest_citations` | 誤りが前のラウンドにあり今回も出た（修正担当が `fixed`・`deferred` と答えていても） | `unresolved` |
| `ingest_citations` | 誤りが前のラウンドにあり今回は出ない | `fixed` |
| `ingest_citations` | 確かめられなかった（None） | 既存の `by: ndf` の行は全部 `unresolved` |
| `citation_errors` | 行頭が `根拠:` でない（文中の「根拠: Value N（MVV バージョン 1）」の引用） | 返さない |
| `_round` | MVV が承認済みでない | 確かめない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `citation_errors` | 版 1 を引いた行 → 1 件。`Value 18` → `unknown` | `スプリント Value 4`・`根拠なし` は照らさない。全角の括弧だけを読む。版 12 にだけある項目を版 11 で引く → `unknown`（`catalog` が版ごと） | 文中の引用・数でない版を拾わない。head の版を引く → 版の誤り |
| `fenced_lines` | ` ``` ` の間の行を返す | `~~~` のフェンス・閉じないフェンス・4 字のフェンスの中の 3 字の行 | — |
| `_citations` | `+` の行だけを読み、新しい側の行の番号を付ける。`prep.diff` が前の head からに狭まっても、merge-base からの全差分の誤りを返す | `+++` の行と削除の行を読まない。hunk が既存のフェンスの途中から始まる追加行を拾わない | `git diff` の失敗 → None と注意 |
| `ingest_citations` | 同じ誤りが次のラウンドも出れば同じ行を出し直す。直れば `fixed` | 見送り済みの行が再検出 → `unresolved` | None → 解決しない |
| `apply_fix` | `by: ndf` の行への `deferred` は終わり方を変えない | — | — |

## ジャーナルとレッドライン

満たす決定: 決定 3・5、受け入れ条件 2・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FindingJournal.ingest` | `journal.py` | 変更 | `ingest(self, raised, round_no, items: Sequence[str] = ()) -> frozenset[str]` | 相手役の `mvv` を `mvv_item` で確かめて行の `mvv` に書く。一覧に無ければ外して `self.warnings` に足す |
| `FindingJournal.handable` | `journal.py` | 新設 | `handable(self) -> list[dict[str, Any]]` | 修正担当へ渡す未解決の行（収束を止める重さで、`mvv` が `P`・`C` でないもの） |
| `FindingJournal.skipped_texts` | `journal.py` | 変更 | `skipped_texts(self, mvv: Optional[str] = None) -> list[str]` | 見送り済みの行のうち、行の `skipped_mvv` が `mvv` と同じものの本文だけを返す |
| `FindingJournal.apply_fix` | `journal.py` | 変更 | `apply_fix(self, items, handed, round_no, mvv: Optional[str] = None)` | `deferred` を書くとき行に `skipped_mvv: <今回の識別子>` を書く（`fixed` では消す） |
| `FindingJournal.enter` | `journal.py` | 変更 | `enter(self, round_no, raised, mvv: Optional[str] = None)` | 識別子の違う見送りが出し直されたら未解決へ戻して再判定させる |

- 守る振る舞い: ジャーナルへ入れるときは、(1) `mvv_item` で `mvv` を確かめ（一覧に無ければ外して注意。以降は普通の指摘）、(2) 有効な `P`・`C` ならレッドラインの行、(3) 有効な `Value`・`Vision`・`Mission` なら重さを `major` 以上に固定した MVV の指摘、の順に分ける
- レッドラインの行は、受けた `P`・`C` の項目をすべて残し（重複を除き、項目の一覧の順に並べる）、修正担当へ渡さず、収束を止めず、振動の判定にも入れない。同じ指摘の申告の到着順（同じラウンドの別の相手役・後のラウンド）と、先に `Value` として見送ったかによらず、`P`・`C` を一度でも受けた行はレッドラインになる
- MVV の識別子の違う見送りが出し直されたら、`fixed` の再発と同じく未解決へ戻し（`revived` に入れ、前のスレッドの resolve を根拠に `fixed` にしない）、今回の基準で再判定させる。戻すときは行の旧い分類（`mvv`・`mvv_pinned`・`redline`）と重要度を消し、今回の申告だけで設定し直す（`_raised_again` の「重い方を残す」を当てない。旧版の `major` が今回の `minor` の収束を止めない）
- 出し直されなかった識別子の違う見送りは、ジャーナルに履歴として残すが、今回の結果（`outputs.mvv`）と記録（`record_review` の `mvv-review`・`review_disputed`）の対象から外す。対象にするのは、行の `skipped_mvv` が今回の識別子と同じ見送りだけ（今回の版で申告されていない見送りを今回の head と版で書き直さず、承認ゲートも改訂の兆候も動かさない）
- 壊し方（テストで落とす）: 旧版の見送りが今回の `disputed`・`review_disputed` に載る。旧版の `major` の見送りが `mvv` 無しの `minor` で出し直され、`major` か `mvv_pinned` のまま残る。見送り済みの本文と同じ指摘を相手役が `P1` で出せない指示になる
- #4014 の `effective_severity`（`impact` の無い `major` を下げる）は、MVV の指摘と `by: ndf` の根拠の行の誤りに当てない。`impact` の無い MVV の指摘は下げずに注意へ載せる
- 壊し方（テストで落とす）: レッドラインが修正担当へ渡る・収束を止める。到着順を入れ替えると分類か残る項目が変わる。見送り済みの `Value 5` の行に後から来た `P1` が承認ゲートへ届かない。MVV の指摘が `minor` へ下がって渡らない
- 行のキーの名前（`redline_items`・`skipped_mvv` など）と、`enter`・`apply_fix`・`skipped_texts` の引数の足し方は、実装（`tdd-cycle`）で走らせて決める

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `ingest` | `mvv: Value 5` の行が `mvv` を持ち収束を止める | `Value 5` と `P1`・`P1` と `C7` を両方の到着順で入れても同じ分類と項目になる | 一覧に無い `mvv` → 外して注意 |
| `handable` | `P1` の行を渡さない・`Value 5` を渡す | 直した行・見送った行を渡さない | — |
| `skipped_texts` | 同じ識別子の見送りを返す | `skipped_mvv` が無く今回が `absent` → 返す | 識別子が違う見送り → 返さない |
| `enter` | 識別子の違う見送りが出し直される → 未解決に戻り `revived` に入り、後の起動で `fixed` にされない | 出し直されなかった見送りは残る。旧版の `major`・`mvv: Value 5` の見送りが `mvv` 無しの `minor` で出し直される → `minor`・`mvv_pinned` 無し | — |
| `skipped_texts`（指示） | 見送り済みの `Value 5` の本文が一覧に載っても、指示がレッドラインの申告を許す | — | — |
| `apply_fix` | `deferred` に今回の識別子が付く | 前の識別子の見送りを今回また見送る → 今回の識別子に書き直す | — |
| `_blocking` | `redline: true` の行 → 偽 | `mvv_pinned` の行に `effective_severity` を当てても `major` のまま | — |

## 終わり方と結果

満たす決定: 決定 5・6、受け入れ条件 4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_gaps` | `finish_decide.py` | 変更 | （同じ） | `redline: true` の行を最初に除く（どの欠けにもしない）。残りのうち、`mvv` を持つ行か `by: ndf` の根拠の行の誤りの未解決を、`unresolved_finding` でなく `mvv_unresolved` の欠けにする |
| `finish` | `finish_decide.py` | 変更 | （同じ） | 欠けに `mvv_unresolved` があれば、状態を止める側（`STOPPED`・`cause.kind: mvv_unresolved`）にする。`record_review` が承認ゲートの判定に要る行を書けなかったら `STOPPED`・`cause.kind: mvv_record_failed` にする |
| `mvv_outputs` | `report_decide.py` | 新設 | `mvv_outputs(rows: Sequence[Mapping[str, Any]], snapshot: Snapshot, head: str) -> dict[str, Any]` | 結果 JSON の `outputs.mvv`（`version`・`sha256`・`head`・`findings`・`citations`・`redlines`・`disputed`） |
| `record_review` | `records/mvv.py` | 新設 | `record_review(root: Path, pr: int, head: str, snapshot: Snapshot, rows: Sequence[Mapping[str, Any]]) -> int` | MVV の行ごとに `mvv-review` を、見送りごとに `review_disputed` を書き、書いた件数を返す |
| `review_rows` | `records/mvv.py` | 新設 | `review_rows(root: Path) -> list[dict[str, Any]]` | `mvv-validation.jsonl` の `mvv-review` の行を読む |
| `review_holds` | `records/mvv_decide.py` | 新設 | `review_holds(rows: Iterable[Mapping[str, Any]], repo: str, targets: Mapping[int, str]) -> list[str]` | 対象の PR と head（PR → head の SHA）ごとに、終わり方が `redline`・`deferred` の行を「PR #N の <項目>（<終わり方>）」の形で返す |
| `count_signals` | `records/mvv_decide.py` | 変更 | （同じ） | `review_disputed` を（repo・PR・指摘のキー）で 1 件にまとめ、`unknown` に足す |
| `check_review_holds` | `progress/gate/checks.py` | 新設 | `check_review_holds(f: MvvFacts) -> Optional[str]` | `f.review_holds` が空でなければ省かない理由を返す |
| `MvvFacts` | `progress/gate/checks.py` | 変更 | 欄 `review_holds: tuple[str, ...] = ()` を足す | — |

- 行の終わり方の対応: ジャーナルの行 → `mvv-review` の `outcome` → 結果の `outputs.mvv` は次のとおり。`redline: true` の行 → `redline` → `redlines`（`text` を持つ）。`mvv_pinned` の行で `deferred`（`skipped_mvv` が今回の識別子と同じもの。違うものは載せない） → `deferred` → `disputed`（`reason` を持つ）。`mvv_pinned` の行で `fixed`・`unresolved` → 同じ値 → `findings`。`by: ndf` の行 → `fixed`・`unresolved` → `citations`
- `mvv-review` の 1 行: `{"kind": "mvv-review", "repo", "pr", "head", "key", "id", "item", "outcome", "version", "sha256", "at"}` に、`deferred` は `reason`（修正担当の見送りの理由）、`redline` は `text`（指摘の本文）を足す。`redline` の `item` は行の `redline_items` を `・` でつないだ値（例 `P1・C7`）で、`review_holds` の「<項目>」と `outputs.mvv.redlines` の項目もこの値を使う（承認ゲートに申告の項目を全部載せる）。`key` はジャーナルの行の `key`（`oscillation.finding_key` の値。`by: ndf` はパスと引いた本文から作った値）で、起動をまたいで同じ指摘に同じ値になる。`id` は表示のためだけに持つ
- `record_review`: `drive._summary` が終わりに 1 回呼ぶ。`head` は終わり方を判定した head。重ねない判定は 2 つのファイルで別々に打つ。`mvv-validation.jsonl` は（repo・PR・head・key・終わり方・版・`item`）が同じ行があれば書かない（同じ head で後から `C7` が足された `redline` も書く）。`mvv-signals.jsonl` は（repo・PR・key・版）が同じ `review_disputed` の行があれば書かない。片方だけ書けて止まった起動も、次の起動で欠けた方だけを書く
- `count_signals`: 今の版の行だけを読み（今どおり）、`review_disputed` を（repo・PR・key）で 1 件にする。id では重ねない
- `Signals` の形は変えない（`unknown` の数に含める）。`count_signals` の `pairs` に `review_disputed` を足す
- `review_holds`: 各 PR について、その head の行があればその行だけを読み、無ければ記録された中で最後（`at` が最も新しい）の head の行を読む。どちらも無ければその PR は何も返さない。head を選んだ後に、指摘のキーごとに最後（`at` が最も新しい）の行だけを残し、その行の終わり方で判定する（同じ head で `P1` の後に `P1・C7` が書かれたら `P1・C7` だけ、`deferred` の後に `redline` が書かれたら `redline` だけ）。承認依頼の「MVV 判定」の行も同じ選び方の結果を使う
- 記録の欠落: `record_review` が `redline`・`deferred` の行（承認ゲートの判定に要る行）を書けなかったら、その起動は収束にせず止める（`STOPPED`・`cause.kind: mvv_record_failed`）。`fixed`・`unresolved` の行だけが書けなかったときは注意に留める
- 承認ゲートでの使い方（`mvv_gate._judged`）: 設計承認ゲートは `{PR の番号: pull.head_sha}`、リリース承認ゲートはスプリントの状態の単位の PR ごとの `{番号: その PR の最後の head}` を `targets` にして `review_holds` を呼び、`MvvFacts.review_holds` に入れる。`refusals` の検査の並びに `check_review_holds` を足し、理由があれば `may_skip` は偽になって `service:mvv` の承認を書かない（`progress/run.py` の `_consume` は今のまま人の回答を待つ）。LLM を呼ぶ前の検査なので費用は増えない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_review` | `redline`・`deferred` の行を書けない | 保存先の権限 | `OSError` | 収束にせず止める（承認ゲートが記録の無い PR を省けるため） | 終了コード `STOPPED`・`cause.kind: mvv_record_failed`、`warnings` に原因 | 保存先を直して `ndf cross-review run` を打ち直す（終わりにまた書く。重ねない） |
| `record_review` | `fixed`・`unresolved` の行だけを書けない | 保存先の権限 | `OSError` | 結果は変えず注意に載せる | 結果の `warnings` | 次の起動で書く（重ねない） |
| `finish` | `mvv_unresolved` で止まる | 上限のラウンドで MVV の指摘が残った | 欠けの種類 | 止まる | 終了コード `STOPPED`、`summary` に残った指摘と項目 | 直して `ndf cross-review run` を打ち直す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `record_review` | 種類・PR・head・指摘のキー・指摘の id・項目・終わり方・版・sha256・repo・時刻。見送りの理由、レッドラインの指摘の本文 | `mvv-validation.jsonl`・`mvv-signals.jsonl` | レッドラインでない指摘の本文（PR に残るので写さない） |
| `mvv_outputs` | 指摘の id・項目・相手役・終わり方・見送りの理由・レッドラインの本文 | 結果 JSON の `outputs.mvv` | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `finish` | MVV の未解決 → `mvv_unresolved` で止まる。普通の未解決だけ → 今どおり `incomplete` | MVV の指摘が全部直った → 今どおり。`redline: true` の未解決だけが残る → 欠け無し（収束） | — |
| `record_review` | 見送り 1 件 → `review_disputed` 1 行と `mvv-review` 1 行（`reason`・`head` つき） | 同じ起動のやり直しで重ねない。別の起動で id が `r1-3` に変わった同じ指摘も重ねない。前の版の行があっても新しい版の 2 行を書く。`mvv-review` だけがある状態から `review_disputed` だけを書き足す。識別子の違う見送り → 書かない | `redline`・`deferred` を書けない → `mvv_record_failed` で止まる。`fixed` だけを書けない → 注意 |
| `count_signals` | `review_disputed` 2 件（別のキー）→ `unknown` が 2 増える | 同じ（repo・PR・key）の 2 行は id が違っても 1 件。同じ id で別のキーは 2 件 | 前の版の行は数えない |
| `review_holds` | 対象の head の `redline`・`deferred` を返す。`item: P1・C7` の `redline` → `PR #N の P1・C7（redline）` | 対象の head の行が無い → 最後の head の行で判定。`fixed` だけ → 空。同じ head・キーに `P1` と後の `P1・C7` → `P1・C7` の 1 件。`deferred` の後に `redline` → `redline` の 1 件 | 別の repo の行は読まない |
| `check_review_holds` | `review_holds` があれば理由を返し `may_skip` が偽 | 空 → None | — |

## 手順書

満たす決定: 決定 2・7、受け入れ条件 1・6

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/project-mvv.md` | 節「工程での読み方」を足す（どの工程も写しの全体を読み、その工程の出力が触れる項目を本文から選んで判断と根拠の行に使う・`current` の結果の欄・人へ戻す形・「MVV なし」）。番号で読む範囲を固定しない。根拠の行の形は `decisions.md` へリンク |
| `skills/development-workflow/references/approval-request.md` | 「MVV 判定」の行の出所に、`review_holds` と同じ規則（対象の head、無ければ最後の head。その中で指摘のキーごとに最後の行）で選んだ `mvv-review` の行のうち終わり方が `redline`・`deferred` の行を足す。設計承認ゲートは設計 PR の行、リリース承認ゲートはスプリントの単位の PR の行を、項目と、`deferred` は行の `reason`、`redline` は行の `text` とともに載せる |
| `skills/development-workflow/references/agent-layers.md` | conductor → supervisor と supervisor → worker の起動指示の項目に「MVV」を足し、守る規則に「MVV の欄の写しを作業の前に読む」を足す |
| `skills/design/SKILL.md`・`skills/design/references/decisions.md`・`skills/tdd-cycle/SKILL.md`・`skills/requirements-design/SKILL.md` | 参照を `project-mvv.md#工程での読み方` へ直す。`decisions.md` の例の版を「`current` の版を書く」形にする |
| `skills/cross-refactoring/SKILL.md` | `$RF_MVV` を `current` の結果の 1 行と写しを読む指示で埋める手順 |
| `skills/cross-review/SKILL.md`・`skills/fix/SKILL.md` | 基準 5・`mvv_unresolved`・`outputs.mvv` の説明。`fix` の「指摘の基準」の表に基準 5 の写し |
| `docs/contracts/commands.md` | `ndf records mvv current` の行 |
| `docs/glossary/glossary.json` | 語「MVV の写し」「根拠の行の確かめ」「MVV に反する指摘」を追加し、`ndf records glossary render` で `docs/glossary.md` を作り直す |
