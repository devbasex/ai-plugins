# #3500 の詳細設計: 置き場を求める関数・引継ぎの記録・残骸の退避

設計文書 `issues/issue-3500-design.md` の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の detailed-design.md にある。実装は子の課題 1〜4 に分ける（設計文書の「子の課題の分け方と順番」）。各セクションの頭に、受け持つ子の課題を書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── domains/locations.json          # 起点の値・行の追加と削除・列 retired
│   ├── domains/ndf-files.json          # design・document の行、scratch の行の移し替え、main_keys
│   └── workflow/progress.json          # 事象 handoff_written の 3 行
└── src/ndf/
    ├── core/places.py *                # resolve・prepare・main_dir・sweep
    ├── core/places_cli.py *            # ndf places path / sweep
    ├── domains/handoff/__init__.py *
    ├── domains/handoff/record.py *     # write・show・listing
    ├── domains/handoff/cli.py *        # ndf handoff write / show / list
    ├── domains/records/handoff.py      # 形の検査（今のまま使う。ファイルの読み書きを外す）
    ├── domains/records/cli.py          # handoff check を消す
    ├── domains/relay/context_records.py# 復元の検出
    ├── progress/plan/run.py            # record_dir
    ├── domains/backlog/state.py・depth.py
    ├── progress/command.py             # ndf_settings_path
    ├── core/converge/location.py       # main_dir・main_worktree を places へ
    ├── domains/worktree/repo.py・create.py・setup.py・ndf_settings.py
    ├── domains/release/cli.py
    ├── domains/crossrefactor/launch.py・workspace.py
    └── checks/places_check.py *        # 起点を自分で決める形を落とす
```

依存の向き: 呼び手（`progress`・`domains/*`）→ `core.places` → `contracts.facts`・`adapters.git` の一方向。`domains.handoff` → `progress.store`（事象の読み書き）・`domains.records.handoff`（形の検査）・`progress.model`（`machine_id`）。`core.places` は `domains` と `progress` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/core/places.py` | 新設 | 置き場の名前から起点どおりのパスを求め、作って無視させ、残骸を退避する | `contracts.facts`・`adapters.git` | モジュール | `resolve`・`prepare`・`main_dir`・`sweep`（新設）、`PlaceError`（新設） |
| `plugins/ndf/src/ndf/core/places_cli.py` | 新設 | `ndf places path`・`ndf places sweep` | `core.places`・`contracts` | モジュール | `register`・`_path`・`_sweep`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/record.py` | 新設 | 引継ぎの事象の追記と読み出し | `progress.store`・`progress.model`・`domains.records.handoff` | モジュール | `write`・`show`・`listing`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/cli.py` | 新設 | `ndf handoff write / show / list` | `domains.handoff.record` | モジュール | `register`（新設） |
| `plugins/ndf/src/ndf/domains/records/handoff.py` | 変更 | 本文の文字列を検査する形にする | — | モジュール | `check_text`・`trim_text`（変更。今の `check_file`・`trim_file` から読み書きを外す） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | `handoff check` を消す | — | モジュール | `_handoff`・`_add_handoff`（削除） |
| `plugins/ndf/src/ndf/domains/relay/context_records.py` | 変更 | 最初の発言の `ndf handoff show` で復元を見つける | — | モジュール | `HANDOFF_PATH` → `HANDOFF_SHOW`（変更） |
| `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | 計画の記録を `places.prepare("ndf-parallel")` の下に置く | `core.places` | モジュール | `record_dir`（変更。シグネチャは同じ） |
| `plugins/ndf/src/ndf/domains/backlog/state.py`・`depth.py` | 変更 | `main_dir` を `places` へ寄せる | `core.places` | モジュール | `main_dir`（削除）・`state_dir`・`ndf_settings_path`（変更） |
| `plugins/ndf/src/ndf/core/converge/location.py` | 変更 | 作業場所の親を `places.resolve("ndf-work")` にする | `core.places` | モジュール | `default_base`・`work_root`（変更）、`main_dir`・`main_worktree`（削除。`places.main_dir` へ移す） |
| `plugins/ndf/src/ndf/domains/worktree/repo.py`・`create.py`・`setup.py` | 変更 | `.worktrees` を `prepare` で作り、登録を求めない。`.ndf/.gitignore` の行を `prepare` に任せる | `core.places` | モジュール | `locate`・`worktrees_dir`・`_check_location`・`init`（変更） |
| `plugins/ndf/src/ndf/domains/crossrefactor/launch.py`・`workspace.py` | 変更 | 起動の前に `.cross_refactoring/` を作り直す。`info/exclude` をやめる | `core.places` | モジュール | `run`（変更）・`_exclude_results`（削除） |
| `plugins/ndf/src/ndf/checks/places_check.py` | 新設 | 起点を自分で決める形と、一覧に無い置き場の id を落とす | `contracts.facts` | モジュール | `check`（新設） |

## 置き場の求め方（子の課題 1）

満たす決定: 決定 1・2・3、受け入れ条件 1〜4・7、不変条件 I1〜I3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `resolve` | `core/places.py` | 新設 | `resolve(place: str, cwd: Path, environ: Mapping[str, str] = os.environ) -> Path` | 一覧の行の起点と path からパスを返す。作らない |
| `prepare` | `core/places.py` | 新設 | `prepare(place: str, cwd: Path, environ: Mapping[str, str] = os.environ) -> Path` | `resolve` のパスを作り、追跡しない行なら無視させて返す |
| `main_dir` | `core/places.py` | 新設 | `main_dir(cwd: Path) -> Path` | メインディレクトリ（`git worktree list` の先頭）を返す |
| `_path` | `core/places_cli.py` | 新設 | `_path(args: argparse.Namespace) -> dict[str, Any]` | `ndf places path <id> [--prepare]` の結果 JSON を作る |

- `resolve`
  - 事前条件: `place` は一覧の id。起点が `worktree`・`main`・`git` のときは `cwd` が git の作業ツリーの中
  - 事後条件: 列 `env` の値が `environ` にあればそのパス。無ければ起点（設計文書の「起点ごとの求め方」）に path を足した絶対パス。`retired` の行は `PlaceError`（`invalid`）
  - 副作用: git の読み取りのコマンドだけ
- `prepare`
  - 事後条件: パスのフォルダがある。行の `tracked` が偽で path がフォルダ（`kind` が `dir`）なら、中に `.gitignore` があり、無かったときは `*\n` だけが書かれている。行の `kind` が `file` で path が `.ndf/` の下なら、同じ起点の `.ndf/.gitignore` に `.ndf/` からの相対パスの行がある（無ければ作る。既存の行は消さない。同じ行は足さない）。起点が `git`・`state`・`config`・`env` の行は無視の処理をしない
  - 副作用: フォルダの作成、`.gitignore` の書き込み（同じディレクトリの一時ファイルから付け替える）。symlink の `.gitignore` はたどらずに `PlaceError`
- `main_dir`: 今の `converge/location.main_worktree` の本体を移す（先頭が bare・報告されたパスが最上位でない・共通ディレクトリが違うなら `PlaceError`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `resolve` | 一覧に無い id | 呼び手の誤り | 一覧の引き当て | 止める（`PlaceError`、`invalid`） | `ndf places path` は終了コード 2 | 再実行可 |
| `resolve`・`main_dir` | git の外・bare・主 worktree を決められない | 呼ばれた場所 | `git` のコマンド | 止める（`PlaceError`、`precondition`） | 終了コード 3（precondition） | 再実行可 |
| `prepare` | 作れない・書けない・symlink | 権限・ディスク・外のファイル | `mkdir`・書き込み | 止める（`OSError` を `PlaceError` にして上げる） | 終了コード 1 と理由 | 書きかけの一時ファイルを消す。再実行可 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 列 `root` の値 | 設定値 | `locations.json` | `worktree`・`main`・`git`・`state`・`config`・`env`（`repo` を `worktree` へ改名） |
| 列 `kind` | 設定値（新設、省略可） | `locations.json` | `dir`（省略時）・`file` |
| 列 `retired` | 設定値（新設、省略可） | `locations.json` | 偽 |
| 足す行 | 設定値 | `locations.json` | `ndf-state`（git・`ndf/state`）・`ndf-registry`（git・`ndf/worktree-registry.json`・file）・`ndf-trash`（git・`ndf/worktree-trash`）・`ndf-locks`（git・`ndf/locks`）・`ndf-work`（git・`ndf/work`・env `NDF_WORKTREE_BASE`）・`ndf-retired`（git・`ndf/retired`）・`cross-refactoring`（worktree・`.cross_refactoring`）・`ndf-notes`（main・`.ndf/notes`）・`backlog-research`（main・`.ndf/backlog/research`）・`ndf-local`（main・`.ndf/worktree.local.json`・file） |
| 直す行 | 設定値 | `locations.json` | `worktrees`・`ndf-parallel` を `main` へ |
| 消す行 | 設定値 | `locations.json` | `ndf-sprints`・`ndf-handoff`（子の課題 3）。`ndf-loop` は #4237 |
| `IGNORE_BODY` | 定数 | `core/places.py` | `"*\n"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `resolve` | `domains/records/files.py`（`ndf-files` の行の location から一覧の path を引く） | `git grep -n 'facts.value("locations"' origin/main -- plugins/ndf/src` | 相対パスだけを返し起点を持たない。`resolve` が起点を足し、`files.py` は相対パスの部品として残す |
| `main_dir` | `converge/location.main_worktree` | 同じ検索と読み | 確かめを含めてそのまま移す |
| `prepare` | 前の設計の `ignored_dir.ensure` | — | 起点と行の種類を知る `prepare` に畳む |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `resolve` | 起点ごとに、メインディレクトリ・linked worktree・サブディレクトリから同じパス | `--separate-git-dir`・サブモジュール・列 `env` の値あり | 無い id・git の外・`retired` の行 |
| `prepare` | 追跡しないフォルダに `*` の `.gitignore`、ファイルの行に `.ndf/.gitignore` の行 | 既にある `.gitignore`・既にある行・`.ndf/.gitignore` が無い | symlink の `.gitignore`・書けないディレクトリ |
| `main_dir` | 先頭の worktree | worktree の中から | bare・共通ディレクトリが違う |

## 呼び手の置き換えと置き場の移し替え（子の課題 1・2）

満たす決定: 決定 1・2・3、受け入れ条件 1〜7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_dir` | `progress/plan/run.py` | 変更 | `record_dir(cwd: Path, plan: str) -> Path`（同じ） | `places.prepare("ndf-parallel", cwd)` の下の `<計画>` を返す（子の課題 1） |
| `state_dir` | `domains/backlog/state.py` | 変更 | 同じ | 既定の起点を `places.resolve("backlog-state")` にする（引数と環境変数の優先は今のまま） |
| `ndf_settings_path` | `progress/command.py`・`domains/backlog/depth.py` | 変更 | 同じ | `places.resolve("ndf", cwd) / name` にする |
| `worktrees_dir`・`locate` | `domains/worktree/repo.py` | 変更 | 同じ | `places.resolve("worktrees")` と `places.main_dir` を使う |
| `_check_location` | `domains/worktree/create.py` | 変更 | 同じ | `check-ignore` で止めるのをやめ、`places.prepare("worktrees")` を呼ぶ（子の課題 2） |
| `init` | `domains/worktree/setup.py` | 変更 | 同じ | `.ndf/.gitignore` を自分で作るのをやめ、`places.prepare("ndf-local")` を呼ぶ |
| `default_base`・`work_root` | `core/converge/location.py` | 変更 | `default_base(cwd: Path) -> Path`（引数をメインディレクトリから `cwd` へ） | `places.resolve("ndf-work")` を返す（子の課題 2。「未確認のまま残ること」の 1 で戻すときは行の起点だけを変える） |
| `run` | `domains/crossrefactor/launch.py` | 変更 | 同じ | 担当を起動する前に、担当が動くディレクトリで `git ls-files -z -- .cross_refactoring` が空でなければ `git rm -r -q --cached --ignore-unmatch -- .cross_refactoring`、フォルダを消し、`places.prepare("cross-refactoring", <担当のディレクトリ>)` で作り直す（子の課題 2） |
| `_exclude_results` | `domains/crossrefactor/workspace.py` | 削除 | — | `info/exclude` への書き足しをやめる |
| 錠のパス | `domains/worktree/mainlock.py`・`domains/project/strategy_write.py`・`domains/loop/acceptance.py` | 変更 | 同じ | `places.resolve("ndf-locks")` の下へ（子の課題 2） |
| 設定の読み方 | `domains/worktree/ndf_settings.py`・`domains/release/cli.py` | 変更 | 同じ | ファイル全体をメインディレクトリから読むのをやめ、`ndf-files.json` の `main_keys`（`worktree` は `guard`・`remote`、`release` は全キー）だけをメインディレクトリから読む（子の課題 2） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `run`（launch） | 索引から外せない・フォルダを消せない | git の錠・権限 | `git rm`・`rmtree` | 止める（`GitError`・`OSError` のまま。担当は起動していない） | 今の起動の失敗と同じ | 何も起動していない。再実行可 |
| `_check_location` | `.worktrees` を作れない | 権限 | `prepare` | 止める | 終了コード 1 | 再実行可 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つのレーンが同時に `prepare` | 同じ `.gitignore`・`.ndf/.gitignore` | 取らない。一時ファイルからの付け替えで、どちらが勝っても中身は同じ（`*` か、行を足した版）。`.ndf/.gitignore` は行を足す前に読み直し、付け替えの後に行が無ければ 1 回だけやり直す | 冪等 |
| 移す前の版と後の版の NDF | 錠 | 無い（設計文書の「移す間の危険」。全セッションを入れ替えてから使う） | — |

## 引継ぎの記録（子の課題 3）

満たす決定: 決定 4、不変条件 I4〜I6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write` | `domains/handoff/record.py` | 新設 | `write(target: str, body: str, cwd: Path, *, trim: bool = False, actor: str = "conductor") -> Written` | 形を検査し、通れば事象 `handoff_written` を 1 件追記する |
| `show` | `domains/handoff/record.py` | 新設 | `show(target: str, cwd: Path, *, seq: Optional[int] = None) -> Handoff` | 最後の（か `seq` の）引継ぎを返す |
| `listing` | `domains/handoff/record.py` | 新設 | `listing(cwd: Path, *, open_only: bool = False) -> list[HandoffSummary]` | このリポジトリのステートマシンで引継ぎを持つものを並べる |
| `check_text`・`trim_text` | `domains/records/handoff.py` | 変更 | `check_text(body: str, limits: Limits) -> Report`・`trim_text(body: str) -> tuple[str, str]` | 今の検査と退避を文字列で行う（パスの存在の確かめ `missing_path` は、本文がリポジトリの外に出るので `cwd` の根から探す形を残す） |
| `register` | `domains/handoff/cli.py` | 新設 | `register(sub: argparse._SubParsersAction) -> None` | 3 つのサブコマンドを足す |

- `write`
  - 事前条件: `target` は `<階層>:<キー>:<名前>` か `<階層>:<名前>`。後者はリポジトリを `cwd` から求めて `progress.model.machine_id` で完全な ID にする。ステートマシンが DB にある
  - 事後条件: 形が通れば、進行の事象に `kind = handoff_written`・`actor`・`payload = {"body", "history", "lines", "bytes"}` の 1 件が増えている。`trim` なら `trim_text` で移したセクションが前の版の `history` の末尾に足されて `history` に入る。通らなければ何も書かない
  - 副作用: DB への 1 トランザクション（今の事象の追記と同じ経路・同じ排他）
- `show`: 事後条件: `seq` が無ければ最後の `handoff_written`。読むだけ
- `listing`: `ndf_progress_machines` の `repo` がこのリポジトリのものに絞り、各ステートマシンの最後の `handoff_written` と、`progress.model` で畳んだ今の状態を返す。`open_only` は終わりの状態（`terminal`）でないものだけ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write` | 形の違反 | 本文 | `check_text` | 書かずに返す | 終了コード 1、`items[].violation` | 直して再実行 |
| `write`・`show` | ステートマシンが無い・まだ引継ぎが無い | ID の誤り・対象の選び違い | `MACHINES` の引き当て | 止める | 終了コード 2 | 再実行可 |
| `write`・`show`・`listing` | DB に繋げない・取り合い | `NDF_PROGRESS_DB`・SQLite の錠 | `progress.store` | 今の `store_unreachable`・`store_busy` のまま | 今の終了コード | 再実行可（`store_busy` は今の再試行） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 事象の決まりの行 | 設定値 | `contracts/workflow/progress.json` | `{"level": "<sprint・lane・plan>", "kind": "handoff_written", "actors": ["conductor", "organizer"], "scope": "table"}` の 3 行 |
| 上限 | 設定値 | `contracts/limits.json` | 今の `handoff.lines` 300・`handoff.bytes` 16,000 |
| `HANDOFF_SHOW` | 定数 | `relay/context_records.py` | `ndf handoff show` の語の正規表現 |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `write` | `machine_id`・`seq`・`lines`・`bytes`・違反 | 結果 JSON | 本文（結果 JSON に入れない） |
| `show` | 本文 | 標準出力（`--json` なら結果 JSON の `outputs.body`） | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write` | 書いて `show` で同じ本文 | 300 行ちょうど・16,000 バイトちょうど・`--trim` で移すセクションが無い | 301 行・無い ID・DB に繋げない |
| `show` | 最後の版・`--seq` の版・`--history` | 版が 1 つ | 引継ぎが無い |
| `listing` | 引継ぎを持つものだけ・`--open` | 0 件 | 別のリポジトリのステートマシンを含めない |

## 残骸の退避（子の課題 4）

満たす決定: 決定 5、不変条件 I7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `sweep` | `core/places.py` | 新設 | `sweep(cwd: Path, *, now: datetime, keep_days: int) -> SweepResult` | `retired` の行のパスを退避し、`keep_days` を過ぎた退避を消す |
| `_sweep` | `core/places_cli.py` | 新設 | `_sweep(args: argparse.Namespace) -> dict[str, Any]` | `ndf places sweep` の結果 JSON |

- `sweep`
  - 事後条件: `retired` の行のパス（起点どおりに解いたもの。`*` を含む path は `glob`）で実在するものが `.git/ndf/retired/<now の UTC の YYYYMMDDTHHMMSS>/<起点>/<path>` に移っている。移したパスと移せなかったパス（理由つき）を返す。`.git/ndf/retired/` の下で名前の日時が `keep_days` より古いディレクトリは消えている
  - 副作用: 同じファイルシステムの中の `rename`（別のファイルシステムなら複写してから消す）
  - 錠のファイル（`retired` の行で `kind` が `lock`）は、`fcntl` で取れたときだけ移す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `sweep` | 移せない | 権限・使用中の錠 | `rename`・`fcntl` | そのパスを飛ばして続ける | 結果 JSON の `items[]` に理由。全体は終了コード 0（`warnings` あり） | 次の `sweep` で再試行 |
| `sweep` | 古い退避を消せない | 権限 | `rmtree` | 飛ばして続ける | 同上 | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KEEP_DAYS` | 設定値 | `contracts/limits.json` の `places.retired_days` | 14 |
| `retired` の行 | 設定値 | `locations.json` | `.ndf/sprint-*`・`.ndf/sprints`・`.ndf/loop`（#4237 の後）・`.ndf/backlog/{causes.jsonl,depths.jsonl,owner.json,overhaul,review-*.json}`・`.ndf/handoff`（子の課題 3 の後）・`.cross_review`・`.worktrees/.ndf`（子の課題 2 の後。ロックの無い worktree を `git worktree remove` してから）・`.git/ndf/{estimate,sprint-limpl,step-history.jsonl,test-run,state.v10-*}`・`.git/ndf-main.lock`・`.git/ndf-branch-strategy.lock` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `sweep` | `retired` のパスが退避先へ移り、結果に並ぶ | 13 日前と 15 日前の退避・`glob` の 0 件・2 回目の `sweep`（何もしない） | 取れない錠・書けない退避先・`retired` でないパスを動かさない |
