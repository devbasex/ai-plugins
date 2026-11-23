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
    ├── domains/handoff/record.py *     # write・show・listing・import_file
    ├── progress/store.py               # read_handoffs・read_events の本文の除外
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
    ├── domains/crossrefactor/launch.py・workspace.py・final_gate.py・final_gate_decide.py
    └── checks/places_check.py *        # 起点を自分で決める形を落とす
```

依存の向き: 呼び手（`progress`・`domains/*`）→ `core.places` → `contracts.facts`・`adapters.git` の一方向。`domains.handoff` → `progress.store`（事象の読み書き）・`domains.records.handoff`（形の検査）・`progress.model`（`machine_id`）。`core.places` は `domains` と `progress` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/core/places.py` | 新設 | 置き場の名前から起点どおりのパスを求め、作って無視させ、残骸を退避する | `contracts.facts`・`adapters.git` | モジュール | `resolve`・`prepare`・`main_dir`・`sweep`・`_locate`（新設）、`PlaceError`（新設） |
| `plugins/ndf/src/ndf/core/places_cli.py` | 新設 | `ndf places path`・`ndf places sweep` | `core.places`・`contracts` | モジュール | `register`・`_path`・`_sweep`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/record.py` | 新設 | 引継ぎの事象の追記と読み出し | `progress.store`・`progress.model`・`domains.records.handoff` | モジュール | `write`・`show`・`listing`・`import_file`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/cli.py` | 新設 | `ndf handoff write / show / list` | `domains.handoff.record` | モジュール | `register`（新設） |
| `plugins/ndf/src/ndf/progress/store.py` | 変更 | 引継ぎの本文を通常の読み出しから外す | — | モジュール | `read_handoffs`（新設）・`read_events`（変更） |
| `plugins/ndf/src/ndf/domains/records/handoff.py` | 変更 | 本文の文字列を検査する形にする | — | モジュール | `check_text`・`trim_text`（変更。今の `check_file`・`trim_file` から読み書きを外す） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | `handoff check` を消す | — | モジュール | `_handoff`・`_add_handoff`（削除） |
| `plugins/ndf/src/ndf/domains/relay/context_records.py` | 変更 | 最初の発言の `ndf handoff show` で復元を見つける | — | モジュール | `HANDOFF_PATH` → `HANDOFF_SHOW`（変更） |
| `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | 計画の記録を `places.prepare("ndf-parallel")` の下に置き、前の版の記録を移す | `core.places` | モジュール | `record_dir`（変更。シグネチャは同じ） |
| `plugins/ndf/src/ndf/domains/backlog/state.py`・`depth.py`・`progress/command.py` | 変更 | `main_dir` を `places` へ寄せ、設定のパスを `places` で求める | `core.places` | モジュール | `main_dir`（削除）・`state_dir`・`ndf_settings_path`（変更）・`PROJECT_FILE`・`MVV_FILE`（値の変更） |
| `plugins/ndf/src/ndf/core/converge/location.py` | 変更 | 作業場所の親を `places.resolve("ndf-work")` にする | `core.places` | モジュール | `default_base`・`work_root`（変更）、`main_dir`・`main_worktree`（削除。`places.main_dir` へ移す） |
| `plugins/ndf/src/ndf/domains/worktree/repo.py`・`create.py`・`setup.py` | 変更 | `.worktrees` を `prepare` で作り、登録を求めない。`.ndf/.gitignore` の行を `prepare` に任せる | `core.places` | モジュール | `locate`・`worktrees_dir`・`_check_location`・`init`（変更） |
| `plugins/ndf/src/ndf/domains/crossrefactor/launch.py`・`workspace.py` | 変更 | 起動の前に `.cross_refactoring/` を作り直す。`info/exclude` をやめる | `core.places` | モジュール | `run`（変更）・`_exclude_results`（削除）。`final_gate_decide.verify_final_fix`（変更。NDF のコミットを範囲の検査から外す） |
| `plugins/ndf/src/ndf/checks/places_check.py` | 新設 | 起点を自分で決める形と、一覧に無い置き場の id を落とす | `contracts.facts` | モジュール | `check`（新設） |

## 置き場の求め方（子の課題 1）

満たす決定: 決定 1・2・3、受け入れ条件 1〜4・7、不変条件 I1〜I3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `resolve` | `core/places.py` | 新設 | `resolve(place: str, cwd: Path, environ: Mapping[str, str] = os.environ, *, allow_retired: bool = False) -> Path` | 一覧の行の起点と path からパスを返す。作らない |
| `prepare` | `core/places.py` | 新設 | `prepare(place: str, cwd: Path, environ: Mapping[str, str] = os.environ) -> Path` | `resolve` のパスを作り、追跡しない行なら無視させて返す |
| `main_dir` | `core/places.py` | 新設 | `main_dir(cwd: Path) -> Path` | メインディレクトリ（`git worktree list` の先頭）を返す |
| `_path` | `core/places_cli.py` | 新設 | `_path(args: argparse.Namespace) -> dict[str, Any]` | `ndf places path <id> [--prepare]` の結果 JSON を作る |

- `_locate`（内部）: `_locate(row: Mapping[str, Any], cwd: Path, environ: Mapping[str, str]) -> Located`。行の起点と path から絶対パスを求める唯一の関数で、`resolve` と `sweep` の両方が使う。`Located` は `path`（絶対パス。`*` を含む path はパターンのまま）・`origin`（起点の絶対パス）・`overridden`（列 `env` の値を使ったか）を持つ。`retired` の行かどうかは見ない
- `resolve`
  - 事前条件: `place` は一覧の id。起点が `worktree`・`main`・`git` のときは `cwd` が git の作業ツリーの中
  - 事後条件: `_locate` の `path` を返す。列 `env` の値が `environ` にあればそのパス、無ければ起点（設計文書の「起点ごとの求め方」）に path を足した絶対パス。`retired` の行は、`allow_retired` が偽なら `PlaceError`（`invalid`）、真なら同じ求め方で返す（前の版の文書を読む `import_file` だけが使う）。`allow_retired` が真でも `retired` でない行は今のまま返す。`sweep` は同じ `_locate` を直接使う
  - 副作用: git の読み取りのコマンドだけ。起点のパスは 1 つのプロセスの中で `(cwd の実体, 起点)` ごとに覚え、同じ組の 2 回目からは git を打たない（「起動ごとの git の呼び出し数」）
- `prepare`
  - 事後条件: パスのフォルダがある（行の `kind` が `file` なら親のフォルダ）。無視の処理は、次の 3 つがすべて成り立つときだけ行う。(1) 行の `tracked` が偽。(2) `_locate` の `overridden` が偽（列 `env` の値で利用者が指定した先には何も書かない）。(3) 起点が `worktree`・`main` で、パスの実体が起点の実体の下にある（作業ツリーの外に解決されるパスには書かない）。そのとき、`kind` が `dir` なら中に `.gitignore` があり、無かったときは `*\n` だけが書かれている。`kind` が `file` で path が `.ndf/` の下なら、同じ起点の `.ndf/.gitignore` に `.ndf/` からの相対パスの行がある（無ければ作る。既存の行は消さない。同じ行は足さない）。起点が `git`・`state`・`config`・`env` の行は無視の処理をしない
  - 副作用: フォルダの作成、`.gitignore` の書き込み（同じディレクトリの一時ファイルから付け替える）。`.ndf/.gitignore` の読み取りから付け替えまでは、同じ起点の `.ndf/.gitignore.lock` を `fcntl.flock`（`LOCK_EX`）で取った間に行う（「並行と排他」）。symlink の `.gitignore` はたどらずに `PlaceError`
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
| 列 `kind` | 設定値（新設、省略可） | `locations.json` | 値は `dir`（省略時）・`file` の 2 つだけ。錠のファイルは `file` |
| 列 `retired` | 設定値（新設、省略可） | `locations.json` | 省略時は退避の対象でない。退避の対象の行は、退避の前提条件の名前を値に持つ: `plain`（前提なし）・`flock`（錠。「残骸の退避」の錠の条件）・`worktree-parent`（登録済みの worktree の親。同じく worktree の条件）・`empty-dir`（中が空のときだけ） |
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
| `prepare` | 追跡しないフォルダに `*` の `.gitignore`、ファイルの行に `.ndf/.gitignore` の行 | 既にある `.gitignore`・既にある行・`.ndf/.gitignore` が無い・2 つのスレッドが違う行を同時に足すと両方残る・列 `env` の値で作業ツリーの外を指すと `.gitignore` を書かない | symlink の `.gitignore`・書けないディレクトリ |
| `main_dir` | 先頭の worktree | worktree の中から | bare・共通ディレクトリが違う |

## 呼び手の置き換えと置き場の移し替え（子の課題 1・2）

満たす決定: 決定 1・2・3、受け入れ条件 1〜7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_dir` | `progress/plan/run.py` | 変更 | `record_dir(cwd: Path, plan: str) -> Path`（同じ） | `places.prepare("ndf-parallel", cwd)` の下の `<計画>` を返す。今の worktree の根の `.ndf/parallel/<計画>/`（前の版が書いた所）があれば移し替える（下の「計画の記録の移し替え」。子の課題 1） |
| `state_dir` | `domains/backlog/state.py` | 変更 | 同じ | 引数 `arg` があれば今のまま `mkdir` だけ（利用者の指定なので `.gitignore` を書かない）。無ければ `places.prepare("backlog-state", cwd)` の下に `repo_key(repo)` を `mkdir` する。環境変数 `NDF_UPKEEP_STATE_DIR` は `prepare` が列 `env` として読み、その先には `.gitignore` を書かない（`prepare` の条件 (2)）。既定先には `.ndf/backlog/state/.gitignore`（`*`）ができる。`main_dir` を呼ぶ今の分岐と `mkdir` だけの既定の分岐は消す |
| `ndf_settings_path` | `progress/command.py` | 変更 | `ndf_settings_path(cwd: Path, name: str) -> Path`（`name` の意味を「根からの相対パス」から「`.ndf/` の中のファイル名」に変える） | `places.resolve("ndf", cwd) / name` にする。同じ PR で渡す値を変える: `PROJECT_FILE` を `"project.json"`、`MVV_FILE` を `"mvv.json"` にし（`.ndf/` の前置きを外す）、呼び元の `command.ndf_settings`（`command.py` の `docs.behavior`・`project`・`ci_declared`・`mvv` の 4 か所）・`command.py` の `PROJECT_FILE` を読む所・`progress/invoke.py` の `ndf_settings(location, PROJECT_FILE)` はそのまま新しい値を渡す。`invoke.py` が `PROJECT_FILE` を利用者向けの文（`FROM_PROJECT` と `workflow` の検査の文）に使う 2 か所は、`.ndf/` を付けた表示の名前 `PROJECT_LABEL`（`command.py` に新設）に替える。`progress/plan/run.py` の `ndf_settings_path(cwd, <ndf-parallel の path>)` は `record_dir` の置き換えで消える。`git grep -n 'ndf_settings_path\|ndf_settings(' -- plugins/ndf/src` が示す呼び元はこれで全部 |
| `ndf_settings_path` | `domains/backlog/depth.py` | 変更 | `ndf_settings_path(cwd: Path) -> Path`（同じ） | `places.resolve(<backlog の location>, cwd) / <backlog の path>` にする（呼び元は `backlog/cli_input.py` の 1 か所で変えない） |
| `worktrees_dir`・`locate` | `domains/worktree/repo.py` | 変更 | 同じ | `places.resolve("worktrees")` と `places.main_dir` を使う |
| `_check_location` | `domains/worktree/create.py` | 変更 | 同じ | `check-ignore` で止めるのをやめ、`places.prepare("worktrees")` を呼ぶ（子の課題 2） |
| `init` | `domains/worktree/setup.py` | 変更 | 同じ | `.ndf/.gitignore` を自分で作るのをやめ、`places.prepare("ndf-local")` を呼ぶ |
| `default_base`・`work_root` | `core/converge/location.py` | 変更 | `default_base(cwd: Path) -> Path`（引数をメインディレクトリから `cwd` へ） | `places.resolve("ndf-work")` を返す（子の課題 2。「未確認のまま残ること」の 1 で戻すときは行の起点だけを変える） |
| `run` | `domains/crossrefactor/launch.py` | 変更 | 同じ | 読み取りの工程（`launch_decide.read_only_phases()`。結果は標準出力で回収し、担当はコミットしない）では何もしない（git の追加の呼び出しは 0）。コミットする工程では、担当を起動する前に、担当が動くディレクトリで `git ls-files -z -- .cross_refactoring` が空でなければ `git rm -r -q --cached -- .cross_refactoring` と `git commit -q --no-verify -m <固定の題> -- .cross_refactoring` で NDF 自身のコミットにし、その SHA を返す。フォルダを消し、`places.prepare("cross-refactoring", <担当のディレクトリ>)` で作り直す。担当の索引には削除が残らないので、削除は担当のコミットに入らない（子の課題 2） |
| `verify_final_fix`・`changed_by_commit` | `domains/crossrefactor/final_gate_decide.py`・`final_gate.py` | 変更 | `verify_final_fix(changed_by_commit, target_scope, prepared: frozenset[str] = frozenset())` | 範囲の検査から、起動の前に `run` が作った NDF のコミット（返した SHA の集まり `prepared`）だけを外す。担当のコミットが `.cross_refactoring/` を消していれば今までどおり範囲外として扱う（子の課題 2） |
| `_exclude_results` | `domains/crossrefactor/workspace.py` | 削除 | — | `info/exclude` への書き足しをやめる |
| 錠のパス | `domains/worktree/mainlock.py`・`domains/project/strategy_write.py`・`domains/loop/acceptance.py` | 変更 | 同じ | `places.resolve("ndf-locks")` の下へ（子の課題 2） |
| 設定の読み方 | `domains/worktree/ndf_settings.py`・`domains/release/cli.py` | 変更 | 同じ | ファイル全体をメインディレクトリから読むのをやめ、`ndf-files.json` の `main_keys`（`worktree` は `guard`・`remote`、`release` は全キー）だけをメインディレクトリから読む（子の課題 2） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `run`（launch） | 索引から外せない・フォルダを消せない | git の錠・権限 | `git rm`・`rmtree` | 止める（`GitError`・`OSError` のまま。担当は起動していない） | 今の起動の失敗と同じ | 何も起動していない。再実行可 |
| `_check_location` | `.worktrees` を作れない | 権限 | `prepare` | 止める | 終了コード 1 | 再実行可 |
| `record_dir` | 移し替え先に同じ名前の計画がある | 前の版と今の版が別々に書いた | 移す前の `exists` | 移さない。新しい置き場を使い、前の置き場のパスを結果 JSON の `warnings` に `legacy_plan_record` として出す（前の置き場は消さない） | 終了コード 0 と警告 | 人が中身を見て片付ける。再実行しても同じ警告 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つのレーンが同時に `prepare` | フォルダの中の `.gitignore` | 取らない。中身は常に `*` だけなので、一時ファイルからの付け替えでどちらが勝っても同じ | 冪等 |
| 2 つのレーンが同時に `prepare`（違う行を足す） | 同じ `.ndf/.gitignore` | 同じ起点の `.ndf/.gitignore.lock`（`.ndf/.gitignore` に自身の行を持つ）を `fcntl.flock(LOCK_EX)` で取り、読み取り・行の追加・一時ファイルからの付け替えを錠の中で行う。後に取った側は先の側の付け替え後の版を読むので、違う行を同時に足しても両方残る | 冪等（同じ行は足さない） |
| 移す前の版と後の版の NDF | 錠 | 無い（設計文書の「移す間の危険」。全セッションを入れ替えてから使う） | — |

### 計画の記録の移し替え（子の課題 1）

`record_dir(cwd, plan)` は、新しい置き場（メインディレクトリの `.ndf/parallel/<計画>/`）を返す前に、今の worktree の根の `.ndf/parallel/<計画>/`（前の版が linked worktree に書いた所）を見る。今の worktree がメインディレクトリならこの確かめはしない。

| 前の置き場 | 新しい置き場 | 扱い |
| --- | --- | --- |
| ある | 無い | 前の置き場を新しい置き場へ `rename`（別のファイルシステムなら複写してから消す）。動いている `detach` の子が前の `run.log` を開いていても、`rename` は開いたファイルを保つので書き足しは新しい置き場へ届く |
| ある | ある | 移さない。新しい置き場を読み書きし、前の置き場を `warnings` の `legacy_plan_record` に出す（上の異常系） |
| 無い | どちらでも | 何もしない |

確かめは `record_dir` を呼ぶ worktree の分だけで、ほかの linked worktree は見ない（その worktree から計画を読むときに移る）。worktree を消す前に記録を残したいときは、その worktree で計画のコマンドを 1 回打てば移る。

### 起動ごとの git の呼び出し数

| 呼び出し | 増える git のコマンド | 再利用 |
| --- | --- | --- |
| `resolve`・`prepare`（起点 `worktree`） | `rev-parse --show-toplevel` 1 回 | 同じプロセスで同じ `cwd` の 2 回目からは 0 |
| `resolve`・`prepare`（起点 `main`） | `worktree list --porcelain` 1 回と、今の `main_worktree` の確かめに要る `rev-parse` | 同上 |
| `resolve`・`prepare`（起点 `git`） | `rev-parse --git-common-dir` 1 回 | 同上 |
| cross-refactoring の読み取りの工程の起動 | 0 | — |
| cross-refactoring のコミットする工程の起動 | `ls-files` 1 回と、担当のディレクトリの起点 1 回（同じプロセスで同じディレクトリなら 0）。追跡されていたときだけ `rm --cached` と `commit` の 2 回 | 索引の確かめは起動ごとに打つ（前の担当が新しく追跡したかは起動ごとに変わる） |

`prepare` の無視の処理（`.gitignore` の有無と `.ndf/.gitignore` の行の確かめ）はファイルの読み取りだけで、git を打たない。

## 引継ぎの記録（子の課題 3）

満たす決定: 決定 4、不変条件 I4〜I6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write` | `domains/handoff/record.py` | 新設 | `write(target: str, body: str, cwd: Path, *, trim: bool = False, actor: str = "conductor") -> Written` | 形を検査し、通れば事象 `handoff_written` を 1 件追記する |
| `show` | `domains/handoff/record.py` | 新設 | `show(target: str, cwd: Path, *, seq: Optional[int] = None, history: bool = False) -> Handoff` | 最後の（か `seq` の）引継ぎを返す。`history` なら、その版までの全版の `history` の差分を古い順につないだものを返す |
| `listing` | `domains/handoff/record.py` | 新設 | `listing(cwd: Path, *, open_only: bool = False) -> list[HandoffSummary]` | このリポジトリのステートマシンで引継ぎを持つものを並べる |
| `import_file` | `domains/handoff/record.py` | 新設（子の課題 4） | `import_file(name: str, cwd: Path, *, to: Optional[str], now: datetime) -> Imported` | 前の版の `.ndf/handoff/<名>.md` と `<名>-history.md` を移す（下の「前の版の引継ぎ文書の移し替え」） |
| `read_handoffs` | `progress/store.py` | 新設 | `read_handoffs(conn, machine_id, *, upto: Optional[int] = None) -> list[model.Event]` | `handoff_written` の事象だけを `payload` つきで読む。引継ぎのコマンドだけが使う |
| `read_events` | `progress/store.py` | 変更 | 同じ | `handoff_written` の行は `payload` を SQL の `CASE` で `NULL` にして返す（本文と履歴を転送しない）。状態の畳み込み・`append_events` の確かめ・`progress/command.py` の `history`（`Event.as_dict()`）はこの形を使うので、本文が出ない |
| `check_text`・`trim_text` | `domains/records/handoff.py` | 変更 | `check_text(body: str, root: Path, limits: Limits) -> Report`・`trim_text(body: str) -> tuple[str, str]` | 今の検査と退避を文字列で行う。`root` は今の `check_file` の `root` と同じ役で、パスの存在の確かめ（`missing_path`）の相対パスの起点。`write` は `places.main_dir(cwd)` を渡すので、どの worktree・サブディレクトリから打っても同じ基準になる |
| `register` | `domains/handoff/cli.py` | 新設 | `register(sub: argparse._SubParsersAction) -> None` | 3 つのサブコマンドを足す |

- `write`
  - 事前条件: `target` は `<階層>:<キー>:<名前>` か `<階層>:<名前>`。後者はリポジトリを `cwd` から求めて `progress.model.machine_id` で完全な ID にする。ステートマシンが DB にある
  - 事後条件: 形が通れば、進行の事象に `kind = handoff_written`・`actor`・`payload = {"body", "history", "lines", "bytes"}` の 1 件が増えている。`history` はこの版で増えた分（差分）だけを持つ: `trim` なら `trim_text` で移したセクション、`trim` でなければ空文字列。蓄積した履歴は `show --history` が全版の差分をつないで返すので、通常の `write` を挟んでも前の版までの履歴は消えず、版ごとに累積を複写しないので重複も増えない。通らなければ何も書かない
  - 手順（1 トランザクション。今の `store.transaction`）: (1) `read_machine` で階層を得る。(2) 階層が `sprint` なら `read_definition` で今の定義の改訂 `rev` を得て下書きに入れる（`lane`・`plan` は `rev` を入れない）。(3) `read_events` で末尾の番号 `last`（事象が無ければ 0）を得る。(4) `append_events(conn, machine_id, [draft], expect_seq=last)`
  - 競合: 進行の更新やスプリントの書き込みが (3) と (4) の間に事象を足すと `Conflict` になる。`handoff_written` は状態を変えないので、トランザクションを捨てて (1) から読み直し、`rev` も読み直して最大 3 回やり直す。3 回とも `Conflict` なら書かずに `store_busy` で止める（終了コードは今の `store_busy`）。スプリントの定義が改訂されていれば新しい `rev` で書く
  - 副作用: DB への 1 トランザクション（今の事象の追記と同じ経路・同じ排他）
- `show`: 事後条件: `seq` が無ければ最後の `handoff_written`。`read_handoffs` で `handoff_written` だけを読み、ほかの事象の本文は読まない。読むだけ
- `listing`: `ndf_progress_machines` の `repo` がこのリポジトリのものに絞り、各ステートマシンの最後の `handoff_written`（番号と時刻だけ。本文は読まない）と今の状態を返す。`open_only` の「閉じていない」は階層ごとに決める: `sprint`・`plan` は `model.table_state` が `model.is_terminal` でない。`lane`（全体の状態を持たない）は、`model` で畳んだ行の状態のうち `is_terminal` でないものが 1 つ以上ある（行がまだ無いレーンは開いている）。すべての行が `completed`・`cancelled` のレーンは閉じている
- 再開の候補の絞り込み（手順 0、`block-next`）: `listing(open_only=True)` のうち、名前が入力に完全一致するものだけを候補にする（スプリントは課題番号から `machine_id` と同じ計算で作った名前、ループは「未確認のまま残ること」の 2 で決める ID）。部分一致や課題番号を含むだけの名前は候補にしない。閉じたステートマシンは候補にしない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write` | 形の違反 | 本文 | `check_text` | 書かずに返す | 終了コード 1、`items[].violation` | 直して再実行 |
| `write`・`show` | ステートマシンが無い・まだ引継ぎが無い | ID の誤り・対象の選び違い | `MACHINES` の引き当て | 止める | 終了コード 2 | 再実行可 |
| `write`・`show`・`listing` | DB に繋げない・取り合い | `NDF_PROGRESS_DB`・SQLite の錠 | `progress.store` | 今の `store_unreachable`・`store_busy` のまま | 今の終了コード | 再実行可（`store_busy` は今の再試行） |
| `write` | 末尾の番号がずれた | 進行の更新・スプリントの書き込みと同時 | `append_events` の `Conflict` | 読み直して最大 3 回やり直し、尽きたら止める | 尽きたら `store_busy` の終了コード | 書いていない。再実行可 |
| `import_file` | 書いた版を読み直すと本文か履歴が違う | DB の取り合い・文字コード | 書いた後の `read_handoffs` | ファイルを動かさずに止める | 終了コード 1 と理由 | ファイルは元の所にある。再実行可（書いた事象は残るが、`source` と SHA-256 が同じ版は 2 回目に足さない） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 事象の決まりの行 | 設定値 | `contracts/workflow/progress.json` | `{"level": "<sprint・lane・plan>", "kind": "handoff_written", "actors": ["conductor", "organizer"], "scope": "table"}` の 3 行 |
| 再試行の回数 | 設定値 | `contracts/limits.json` の `handoff.conflict_retries` | 3 |
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
| `write` | 書いて `show` で同じ本文・スプリントに今の `rev` で書く | 300 行ちょうど・16,000 バイトちょうど・`--trim` で移すセクションが無い・`--trim` の後に通常の `write` を挟んでも `show --history` が前の履歴を返す | 301 行・無い ID・DB に繋げない・`append_events` の `Conflict` を 1 回差し込むとやり直して書ける・3 回で `store_busy` |
| `show` | 最後の版・`--seq` の版・`--history` | 版が 1 つ | 引継ぎが無い |
| `listing` | 引継ぎを持つものだけ・`--open` | 0 件・行がすべて `completed` のレーンは `--open` に出ない・行の無いレーンは出る | 別のリポジトリのステートマシンを含めない |
| `read_events` | `handoff_written` の `payload` が `None` で、ほかの事象は今のまま | — | — |
| `import_file` | 本文と履歴が 1 版に入り、2 つのファイルが退避先へ動く | 履歴のファイルが無い・`--drop` | 書いた版の読み直しが違うと動かさない |

### 前の版の引継ぎ文書の移し替え（子の課題 4）

`ndf handoff import <名> (--to <ID> | --drop)` が `import_file` を呼ぶ。読むのは `places.resolve("retired-handoff", cwd, allow_retired=True)`（メインディレクトリの `.ndf/handoff`）の下の `<名>.md` と `<名>-history.md`（無ければ空）で、AI はパスを渡さず名前だけを渡す。

- `--to <ID>`: 本文を `check_text` に通し、`write` と同じ手順で `payload = {"body": <本文>, "history": <履歴のファイルの中身>, "lines", "bytes", "source": ".ndf/handoff/<名>.md", "source_sha256": {"body": ..., "history": ...}}` の 1 件を足す。同じトランザクションの後で `read_handoffs` で読み直し、本文・履歴・SHA-256 が一致したときだけ、2 つのファイルを `.git/ndf/retired/<now>/main/.ndf/handoff/` へ動かす（`sweep` と同じ並び）。動いている対象（`listing --open` に出るもの）の文書は conductor が `--to` で移す
- `--drop`: 終わった対象・ステートマシンの無い 10 系の対象の文書を、DB に書かずに同じ退避先へ動かす（14 日の間は戻せる）。どちらにするかは conductor が `listing --open` を見て決める
- `.ndf/handoff` のフォルダは `retired` の値 `empty-dir` の行で、`sweep` は中が空になるまで動かさない。移していない文書が残る間は `items[]` に `not_empty` と残りの数が出る

## 残骸の退避（子の課題 4）

満たす決定: 決定 5、不変条件 I7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `sweep` | `core/places.py` | 新設 | `sweep(cwd: Path, *, now: datetime, keep_days: int, environ: Mapping[str, str] = os.environ) -> SweepResult` | `retired` の行のパスを、行の前提条件と移す前の検査を通ったものだけ退避し、`keep_days` を過ぎた退避を消す |
| `_sweep` | `core/places_cli.py` | 新設 | `_sweep(args: argparse.Namespace) -> dict[str, Any]` | `ndf places sweep` の結果 JSON |

- `sweep`
  - 対象の求め方: `retired` の値を持つ各行を `_locate(row, cwd, environ)` で解く（`resolve` と同じ関数。`*` を含む path は `glob`）。実在するものを候補にする
  - 移す前の検査（全行に共通。1 つでも当たればその候補を動かさず、`items[]` に理由を出す）
    1. `in_use`: 候補が、`retired` でない行を今の `environ` で `_locate` したパス（実際に選ばれた保存先。列 `env` の上書きも含む）と同じか、その祖先である（候補を動かすと今の保存先ごと動く）。候補が今の保存先の子孫であること（`.ndf` の下の `.ndf/sprint-*`、`.worktrees` の下の `.worktrees/.ndf`）は止める理由にしない。たとえば「未確認のまま残ること」の 1 で `ndf-work` の行を `main` の `.worktrees/.ndf` に戻した環境や、`NDF_WORKTREE_BASE` が `.worktrees/.ndf` を指す環境では、`.worktrees/.ndf` は動かない
    2. `symlink`: 起点から候補までのパスの各要素を `lstat` し、symlink が 1 つでもある（`.ndf` が作業ツリーの外を指す symlink など）。あわせて候補の実体が起点の実体の下にない
    3. `tracked`: 起点が `worktree`・`main` の候補について、`git ls-files -z -- <候補…>` を起点ごとに 1 回打ち、候補の中に追跡されたファイルが 1 つでもある（利用者が `.ndf/sprint-*` に一致するファイルを追跡している場合など）。起点 `git` の候補は作業ツリーの外なので打たない
  - 行の前提条件（`retired` の値）
    - `plain`: 上の検査だけ
    - `flock`（錠のファイル）: 今のコードと同じ `fcntl.flock(fd, LOCK_EX | LOCK_NB)` で取る（`lockf`・`fcntl(F_SETLK)` は使わない。シェルの `flock` と今の `mainlock` の排他は `flock` 同士でしか見えない）。取れなければ `lock_held`。取れたら、`/proc/*/fd` を走査して同じ inode を開いているほかのプロセス（旧版の持ち主と、`flock` で待っている取得者。待つ取得者はファイルを開いている）が無いことを確かめ、あれば `lock_open_elsewhere` で動かさない。無ければ錠を持ったまま `rename` し、もう一度走査する。`rename` の間に開いたプロセスが現れたら、元の名前にファイルが無いときだけ元へ戻して `lock_reopened` とする。`/proc` が無い環境（macOS など）は確かめられないので動かさず `lock_unverifiable`
    - `worktree-parent`（`.worktrees/.ndf`）: `git worktree list --porcelain` で候補の下に登録された worktree を並べ、ロックの無いものを `git worktree remove`（`--force` を付けない）で外す。外した後にもう一度並べ、登録された worktree が 1 つでも残っていれば（ロック中・未コミットの変更で Git が削除を拒んだもの）親を動かさず `registered_worktrees_remain` と残りのパスを出す。残りが 0 のときだけ親を `rename` する
    - `empty-dir`（`.ndf/handoff`）: 中にファイルが 1 つでもあれば `not_empty`。前の版の文書は `ndf handoff import` が 1 件ずつ移す（「前の版の引継ぎ文書の移し替え」）
  - 事後条件: 検査と前提条件を通った候補が `.git/ndf/retired/<now の UTC の YYYYMMDDTHHMMSS>/<起点>/<path>` に移っている。移したパスと移せなかったパス（理由つき）を返す。`.git/ndf/retired/` の下で名前の日時が `keep_days` より古いディレクトリは消えている
  - 副作用: 同じファイルシステムの中の `rename`（別のファイルシステムなら複写してから消す）と、`worktree-parent` の `git worktree remove`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `sweep` | 移せない | 権限・移す前の検査・行の前提条件 | `rename`・上の検査 | そのパスを飛ばして続ける | 結果 JSON の `items[]` に理由（`in_use`・`symlink`・`tracked`・`lock_held`・`lock_open_elsewhere`・`lock_reopened`・`lock_unverifiable`・`registered_worktrees_remain`・`not_empty`・`os_error`）。全体は終了コード 0（`warnings` あり） | 次の `sweep` で再試行 |
| `sweep` | 古い退避を消せない | 権限 | `rmtree` | 飛ばして続ける | 同上 | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KEEP_DAYS` | 設定値 | `contracts/limits.json` の `places.retired_days` | 14 |
| `retired` の行 | 設定値 | `locations.json` | 下の表 |

`retired` の行は、すべて子の課題 4 の PR で `locations.json` に足す。子の課題 4 は子の課題 2・3 と #4237 の後に出す（設計文書の「子の課題の分け方と順番」）ので、「〜の後」という条件を一覧の値で表す必要は無い。`.ndf/loop` の行は、#4237 が閉じる前に子の課題 4 を出すなら足さない。

表の id は行の束の名前で、`locations.json` の id は path の名前を後ろに付けて行ごとに分ける（例 `retired-locks-main`、`retired-sprint-records-glob`）。

| id | 起点 | path | `kind` | `retired` |
| --- | --- | --- | --- | --- |
| `retired-sprint-records` | main | `.ndf/sprint-*`（`glob`）・`.ndf/sprints` の 2 行 | dir | `plain` |
| `retired-loop` | main | `.ndf/loop` | dir | `plain` |
| `retired-backlog-v10` | main | `.ndf/backlog/causes.jsonl`・`depths.jsonl`・`owner.json`・`review-*.json`（file）、`.ndf/backlog/overhaul`（dir）の各行 | 各行 | `plain` |
| `retired-handoff` | main | `.ndf/handoff` | dir | `empty-dir` |
| `retired-cross-review` | worktree | `.cross_review` | dir | `plain` |
| `retired-cross-work` | main | `.worktrees/.ndf` | dir | `worktree-parent` |
| `retired-git-v10` | git | `ndf/estimate`・`ndf/sprint-limpl`・`ndf/test-run`・`ndf/state.v10-*`（dir）、`ndf/step-history.jsonl`（file）の各行 | 各行 | `plain` |
| `retired-locks` | git | `ndf-main.lock`・`ndf-branch-strategy.lock` の 2 行 | file | `flock` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `sweep` | `retired` のパスが退避先へ移り、結果に並ぶ | 13 日前と 15 日前の退避・`glob` の 0 件・2 回目の `sweep`（何もしない）・ロックの無い worktree だけの `.worktrees/.ndf` は外してから動く | 取れない錠（別のスレッドで `flock` を持つ）・同じ錠をほかの記述子で開いたまま・書けない退避先・`retired` でないパスを動かさない・`ndf-work` が `.worktrees/.ndf` を指すと動かさない・ロック中の worktree が残ると親を動かさない・`.ndf` が外を指す symlink・追跡されたファイルを含む `.ndf/sprint-*`・中に文書が残る `.ndf/handoff` |
