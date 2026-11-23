# #3500 の詳細設計: 置き場を求める関数・引継ぎの記録

設計文書 `issues/issue-3500-design.md` の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。書き方と、別ファイルにする条件は `design` の detailed-design.md にある。実装は子の課題 1〜4 に分ける（設計文書の「子の課題の分け方と順番」）。子の課題 4（残骸の片付け）は手順書だけで関数を持たないので、ここには書かない。各セクションの頭に、受け持つ子の課題を書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── domains/locations.json          # 起点の値・行の追加と削除
│   ├── domains/ndf-files.json          # design・document の行、scratch の行の移し替え、main_keys
│   └── workflow/progress.json          # 事象 handoff_written の 3 行
└── src/ndf/
    ├── core/places.py *                # resolve・prepare・main_dir
    ├── core/places_cli.py *            # ndf places path
    ├── domains/handoff/__init__.py *
    ├── domains/handoff/record.py *     # write・show・listing
    ├── progress/store.py               # read_handoff・read_handoff_histories・read_events の本文の除外
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
    ├── domains/crossrefactor/launch.py・workspace.py・drive.py・final_gate.py
    └── checks/places_check.py *        # 起点を自分で決める形を落とす
```

依存の向き: 呼び手（`progress`・`domains/*`）→ `core.places` → `contracts.facts`・`adapters.git` の一方向。`domains.handoff` → `progress.store`（事象の読み書き）・`domains.records.handoff`（形の検査）・`progress.model`（`machine_id`）。`core.places` は `domains` と `progress` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/core/places.py` | 新設 | 置き場の名前から起点どおりのパスを求め、作って無視させる | `contracts.facts`・`adapters.git` | モジュール | `resolve`・`prepare`・`main_dir`・`_locate`（新設）、`PlaceError`（新設） |
| `plugins/ndf/src/ndf/core/places_cli.py` | 新設 | `ndf places path` | `core.places`・`contracts` | モジュール | `register`・`_path`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/record.py` | 新設 | 引継ぎの事象の追記と読み出し | `progress.store`・`progress.model`・`domains.records.handoff` | モジュール | `write`・`show`・`listing`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/cli.py` | 新設 | `ndf handoff write / show / list` | `domains.handoff.record` | モジュール | `register`（新設） |
| `plugins/ndf/src/ndf/progress/store.py` | 変更 | 引継ぎの本文を通常の読み出しから外す | — | モジュール | `read_handoff`・`read_handoff_histories`（新設）・`read_events`（変更） |
| `plugins/ndf/src/ndf/domains/records/handoff.py` | 変更 | 本文の文字列を検査する形にする | — | モジュール | `check_text`・`trim_text`（変更。今の `check_file`・`trim_file` から読み書きを外す） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | `handoff check` を消す | — | モジュール | `_handoff`・`_add_handoff`（削除） |
| `plugins/ndf/src/ndf/domains/relay/context_records.py` | 変更 | 最初の発言の `ndf handoff show` で復元を見つける | — | モジュール | `HANDOFF_PATH` → `HANDOFF_SHOW`（変更） |
| `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | 計画の記録を `places.prepare("ndf-parallel")` の下に置く（前の版の記録は手で一度だけ移す。設計文書の「残骸の片付け」） | `core.places` | モジュール | `record_dir`（変更。シグネチャは同じ） |
| `plugins/ndf/src/ndf/domains/backlog/state.py`・`depth.py`・`progress/command.py` | 変更 | `main_dir` を `places` へ寄せ、設定のパスを `places` で求める | `core.places` | モジュール | `main_dir`（削除）・`state_dir`・`ndf_settings_path`（変更）・`PROJECT_FILE`・`MVV_FILE`（値の変更） |
| `plugins/ndf/src/ndf/core/converge/location.py` | 変更 | 作業場所の親を `places.resolve("ndf-work")` にする | `core.places` | モジュール | `default_base`・`work_root`（変更）、`main_dir`・`main_worktree`（削除。`places.main_dir` へ移す） |
| `plugins/ndf/src/ndf/domains/worktree/repo.py`・`create.py`・`setup.py` | 変更 | `.worktrees` を `prepare` で作り、登録を求めない。`.ndf/.gitignore` の行を `prepare` に任せる | `core.places` | モジュール | `locate`・`worktrees_dir`・`_check_location`・`init`（変更） |
| `plugins/ndf/src/ndf/domains/crossrefactor/launch.py`・`workspace.py`・`drive.py`・`final_gate.py` | 変更 | コードを書く工程の起点を取る前に `.cross_refactoring/` を作り直す。`info/exclude` をやめる | `core.places` | モジュール | `clear_results`（新設）・`_exclude_results`（削除）・`drive._record_phase`・`final_gate.run_gate`（変更）。`launch.run`・`final_gate_decide.verify_final_fix` は変えない |
| `plugins/ndf/src/ndf/checks/places_check.py` | 新設 | 起点を自分で決める形と、一覧に無い置き場の id を落とす | `contracts.facts` | モジュール | `check`（新設） |

## 置き場の求め方（子の課題 1）

満たす決定: 決定 1・2・3、受け入れ条件 1〜4・7、不変条件 I1〜I3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `resolve` | `core/places.py` | 新設 | `resolve(place: str, cwd: Path, environ: Mapping[str, str] = os.environ) -> Path` | 一覧の行の起点と path からパスを返す。作らない |
| `prepare` | `core/places.py` | 新設 | `prepare(place: str, cwd: Path, environ: Mapping[str, str] = os.environ, *, root: Optional[Path] = None) -> Path` | `resolve` のパスを作り、追跡しない行なら無視させて返す。`root` は起点 `worktree` の行で呼び手が worktree の根を既に持つときに渡し、渡すと起点を求める git を打たない（起点 `worktree` 以外の行に渡すと `PlaceError`（`invalid`）） |
| `main_dir` | `core/places.py` | 新設 | `main_dir(cwd: Path) -> Path` | メインディレクトリ（`git worktree list` の先頭）を返す |
| `_path` | `core/places_cli.py` | 新設 | `_path(args: argparse.Namespace) -> dict[str, Any]` | `ndf places path <id> [--prepare]` の結果 JSON を作る |

- `_locate`（内部）: `_locate(row: Mapping[str, Any], cwd: Path, environ: Mapping[str, str]) -> Located`。行の起点と path から絶対パスを求める唯一の関数で、`resolve` と `prepare` が使う。`Located` は `path`（絶対パス。`*` を含む path はパターンのまま）・`origin`（起点の絶対パス）・`overridden`（列 `env` の値を使ったか）を持つ
- `resolve`
  - 事前条件: `place` は一覧の id。起点が `worktree`・`main`・`git` のときは `cwd` が git の作業ツリーの中
  - 事後条件: `_locate` の `path` を返す。列 `env` の値が `environ` にあればそのパス、無ければ起点（設計文書の「起点ごとの求め方」）に path を足した絶対パス
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
| 足す行 | 設定値 | `locations.json` | `ndf-state`（git・`ndf/state`）・`ndf-registry`（git・`ndf/worktree-registry.json`・file）・`ndf-trash`（git・`ndf/worktree-trash`）・`ndf-locks`（git・`ndf/locks`）・`ndf-work`（git・`ndf/work`・env `NDF_WORKTREE_BASE`）・`cross-refactoring`（worktree・`.cross_refactoring`）・`ndf-notes`（main・`.ndf/notes`）・`backlog-research`（main・`.ndf/backlog/research`）・`ndf-local`（main・`.ndf/worktree.local.json`・file） |
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
| `resolve` | 起点ごとに、メインディレクトリ・linked worktree・サブディレクトリから同じパス | `--separate-git-dir`・サブモジュール・列 `env` の値あり | 無い id・git の外 |
| `prepare` | 追跡しないフォルダに `*` の `.gitignore`、ファイルの行に `.ndf/.gitignore` の行 | 既にある `.gitignore`・既にある行・`.ndf/.gitignore` が無い・2 つのスレッドが違う行を同時に足すと両方残る・列 `env` の値で作業ツリーの外を指すと `.gitignore` を書かない | symlink の `.gitignore`・書けないディレクトリ |
| `main_dir` | 先頭の worktree | worktree の中から | bare・共通ディレクトリが違う |

## 呼び手の置き換えと置き場の移し替え（子の課題 1・2）

満たす決定: 決定 1・2・3、受け入れ条件 1〜7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `record_dir` | `progress/plan/run.py` | 変更 | `record_dir(cwd: Path, plan: str) -> Path`（同じ） | `places.prepare("ndf-parallel", cwd)` の下の `<計画>` を返す。前の版の記録の移し替えはしない（手で一度だけ移す） |
| `state_dir` | `domains/backlog/state.py` | 変更 | 同じ | 引数 `arg` があれば今のまま `mkdir` だけ（利用者の指定なので `.gitignore` を書かない）。無ければ `places.prepare("backlog-state", cwd)` の下に `repo_key(repo)` を `mkdir` する。環境変数 `NDF_UPKEEP_STATE_DIR` は `prepare` が列 `env` として読み、その先には `.gitignore` を書かない（`prepare` の条件 (2)）。既定先には `.ndf/backlog/state/.gitignore`（`*`）ができる。`main_dir` を呼ぶ今の分岐と `mkdir` だけの既定の分岐は消す |
| `ndf_settings_path` | `progress/command.py` | 変更 | `ndf_settings_path(cwd: Path, name: str) -> Path`（`name` の意味を「根からの相対パス」から「`.ndf/` の中のファイル名」に変える） | `places.resolve("ndf", cwd) / name` にする。同じ PR で渡す値を変える: `PROJECT_FILE` を `"project.json"`、`MVV_FILE` を `"mvv.json"` にし（`.ndf/` の前置きを外す）、呼び元の `command.ndf_settings`（`command.py` の `docs.behavior`・`project`・`ci_declared`・`mvv` の 4 か所）・`command.py` の `PROJECT_FILE` を読む所・`progress/invoke.py` の `ndf_settings(location, PROJECT_FILE)` はそのまま新しい値を渡す。`invoke.py` が `PROJECT_FILE` を利用者向けの文（`FROM_PROJECT` と `workflow` の検査の文）に使う 2 か所は、`.ndf/` を付けた表示の名前 `PROJECT_LABEL`（`command.py` に新設）に替える。`progress/plan/run.py` の `ndf_settings_path(cwd, <ndf-parallel の path>)` は `record_dir` の置き換えで消える。`git grep -n 'ndf_settings_path\|ndf_settings(' -- plugins/ndf/src` が示す呼び元はこれで全部 |
| `ndf_settings_path` | `domains/backlog/depth.py` | 変更 | `ndf_settings_path(cwd: Path) -> Path`（同じ） | `places.resolve(<backlog の location>, cwd) / <backlog の path>` にする（呼び元は `backlog/cli_input.py` の 1 か所で変えない） |
| `worktrees_dir`・`locate` | `domains/worktree/repo.py` | 変更 | 同じ | `places.resolve("worktrees")` と `places.main_dir` を使う |
| `_check_location` | `domains/worktree/create.py` | 変更 | 同じ | `check-ignore` で止めるのをやめ、`places.prepare("worktrees")` を呼ぶ（子の課題 2） |
| `init` | `domains/worktree/setup.py` | 変更 | 同じ | `.ndf/.gitignore` を自分で作るのをやめ、`places.prepare("ndf-local")` を呼ぶ |
| `default_base`・`work_root` | `core/converge/location.py` | 変更 | `default_base(cwd: Path) -> Path`（引数をメインディレクトリから `cwd` へ） | `places.resolve("ndf-work")` を返す（子の課題 2。「未確認のまま残ること」の 1 で戻すときは行の起点だけを変える） |
| `clear_results` | `domains/crossrefactor/launch.py` | 新設 | `clear_results(work: Path, scratch: Path) -> bool` | `work`（担当がコミットする作業場所の根）の `.cross_refactoring/` を作り直す。追跡されていたら削除だけの NDF のコミットを作って真を返す。手順は下の「起動の前の作り直し」（子の課題 2） |
| `run` | `domains/crossrefactor/launch.py` | 変えない | `run(req, dirs, launcher) -> tuple[Optional[dict[str, Any]], agents.AgentStatus]`（同じ） | 作り直しは呼ばない。SHA も返さない |
| `_record_phase` | `domains/crossrefactor/drive.py` | 変更 | 同じ | 工程がコードを書く工程（`launch_decide.coding_phases()`）なら、HEAD を起点として読む前に `launch.clear_results(run.ws.work, run.ws.logs)` を呼ぶ。読み取りの工程（`plan` など）では呼ばない |
| `run_gate` | `domains/crossrefactor/final_gate.py` | 変更 | `run_gate(g, hub, cfg, fix, sleep, ci_port=None, ready: Callable[[], None] = lambda: None)` | 直しの周ごとに `before = g.rev_parse("HEAD")` の直前で `ready()` を呼ぶ。`drive._final` は `ready` に `lambda: _record_phase(run, "final-fix")` を渡し、`fix` の中の `_record_phase` を消す |
| `verify_final_fix`・`changed_by_commit`・`flaky_decide.violations`・`flaky_decide.results` | `final_gate_decide.py`・`final_gate.py`・`flaky_decide.py` | 変えない | 同じ | NDF のコミットは起点（`before`・`base`）より前にあるので、`before..HEAD`・`base..HEAD` には担当のコミットだけが入る。担当のコミットが `.cross_refactoring/` を消したり足したりしていれば、今までどおり範囲外・違反として扱う |
| `_exclude_results` | `domains/crossrefactor/workspace.py` | 削除 | — | `info/exclude` への書き足しをやめる |
| 錠のパス | `domains/worktree/mainlock.py`・`domains/project/strategy_write.py`・`domains/loop/acceptance.py` | 変更 | 同じ | `places.resolve("ndf-locks")` の下へ（子の課題 2） |
| 設定の読み方 | `domains/worktree/ndf_settings.py`・`domains/release/cli.py` | 変更 | 同じ | ファイル全体をメインディレクトリから読むのをやめ、`ndf-files.json` の `main_keys`（`worktree` は `guard`・`remote`、`release` は全キー）だけをメインディレクトリから読む（子の課題 2） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `clear_results` | 退避できない・コミットできない | git の錠・権限 | `rename`・`git commit` | 止める（`GitError`・`OSError` のまま。担当は起動していない） | 今の起動の失敗と同じ | コミットの前に止まったら退避したフォルダを元へ戻す。再実行可（追跡が残っていれば同じ手順をやり直し、無ければ作り直すだけ） |
| `_check_location` | `.worktrees` を作れない | 権限 | `prepare` | 止める | 終了コード 1 | 再実行可 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つのレーンが同時に `prepare` | フォルダの中の `.gitignore` | 取らない。中身は常に `*` だけなので、一時ファイルからの付け替えでどちらが勝っても同じ | 冪等 |
| 2 つのレーンが同時に `prepare`（違う行を足す） | 同じ `.ndf/.gitignore` | 同じ起点の `.ndf/.gitignore.lock`（`.ndf/.gitignore` に自身の行を持つ）を `fcntl.flock(LOCK_EX)` で取り、読み取り・行の追加・一時ファイルからの付け替えを錠の中で行う。後に取った側は先の側の付け替え後の版を読むので、違う行を同時に足しても両方残る | 冪等（同じ行は足さない） |
| 移す前の版と後の版の NDF | 錠 | 無い（設計文書の「移す間の危険」。全セッションを入れ替えてから使う） | — |

### 起動ごとの git の呼び出し数

| 呼び出し | 増える git のコマンド | 再利用 |
| --- | --- | --- |
| `resolve`・`prepare`（起点 `worktree`） | `rev-parse --show-toplevel` 1 回 | 同じプロセスで同じ `cwd` の 2 回目からは 0 |
| `resolve`・`prepare`（起点 `main`） | `worktree list --porcelain` 1 回と、今の `main_worktree` の確かめに要る `rev-parse` | 同上 |
| `resolve`・`prepare`（起点 `git`） | `rev-parse --git-common-dir` 1 回 | 同上 |
| cross-refactoring の読み取りの工程の起動 | 0 | — |
| cross-refactoring の作業場所の用意（`workspace.prepare`、1 回の実行に 1 回） | −1（`_exclude_results` の `rev-parse --git-path info/exclude` が無くなる） | — |
| cross-refactoring のコードを書く工程の起動（`clear_results`） | `ls-files` 1 回。追跡されていたときだけ `commit` 1 回。起点は `run.ws.work` を `prepare(..., root=)` に渡すので 0 | 下の「起動ごとの索引の確かめ」 |

起動ごとの索引の確かめ: `ls-files` は、コードを書く工程の起動の数（1 回の実行で、項目ごとの `add-tests` と `implement`、フレーキーの直しの起動、最後の修正の周の数の和）だけ打つ。項目が 10 の実行なら約 20〜25 回で、1 回は数ミリ秒（担当の起動は 1 回が数分）。前の担当が `git add -f` で作業ファイルを新しく追跡したかは起動ごとに変わり、前の起動のコミットを調べる（`diff-tree`）のも git を 1 回打つので、`ls-files` を起動ごとに 1 回打つのが最小である。読み取りの工程では打たない。

### 起動の前の作り直し（`clear_results`、子の課題 2）

`git rm --cached` の後に `git commit -- <パス>` を打つと、パスを指定した `git commit` は索引でなく作業ツリーの中身を読むので削除にならない（[git commit の仕様](https://git-scm.com/docs/git-commit#_description) ）。そこで作業ツリーから先に退かし、索引には触れずにパスを指定してコミットする。

1. `git ls-files -z -- .cross_refactoring` が空なら 4 へ進む
2. `.cross_refactoring/` を、作業場所の外で同じファイルシステムの一時フォルダ（`scratch`。呼び手は `rf<PR>/logs` を渡す。その下に `tempfile.mkdtemp(prefix="cross_refactoring-")` で作る）へ `rename` で退避する
3. `git commit -q --no-verify -m <固定の題> -- .cross_refactoring` を打つ。作業ツリーにファイルが無いので、追跡されていたファイルの削除だけが 1 つのコミットに入り、ほかのステージ済みの変更は索引に残ってコミットに入らない（2026-11-23 に一時のリポジトリで確かめた）。失敗したら退避したフォルダを元へ戻して止める
4. 退避したフォルダは消さずに `scratch` の下に残す。1 から来たときも、残っている `.cross_refactoring/` が空でなければ同じく `scratch` の下の新しい一時フォルダへ `rename` で退避し、消さない。そのうえで `places.prepare("cross-refactoring", work, root=work)` で作り直す

退避を消さないのは、`.cross_refactoring/` の中の追跡ファイルへのコミットしていない編集と、同居する未追跡のファイルを、削除のコミットからは戻せないため。中身が前の担当の作業結果だけか、利用者の変更を含むかはコードで見分けられないので、どちらも戻せる形で残す。退避先は実行の `logs` の下なので、担当の出力と同じく実行の作業場所と一緒に片付き、作業場所の外なので担当の起動とコミットには入らない。退避したパスは結果 JSON の `outputs.cleared`（無ければ空）に出す

工程の起点（`_record_phase` の HEAD、最後の修正の `before`）はこの後に読むので、NDF のコミットは起点より前にある。範囲の検査・フレーキーの直しの検査と集計は、起点から HEAD までの担当のコミットだけを見る。起点は今のまま実行の状態（`phases`）に保存されるので、NDF のコミットの SHA を別に保存して渡す必要は無い。

`prepare` の無視の処理（`.gitignore` の有無と `.ndf/.gitignore` の行の確かめ）はファイルの読み取りだけで、git を打たない。

## 引継ぎの記録（子の課題 3）

満たす決定: 決定 4、不変条件 I4〜I6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write` | `domains/handoff/record.py` | 新設 | `write(target: str, body: str, cwd: Path, *, trim: bool = False, actor: str = "conductor") -> Written` | 形を検査し、通れば事象 `handoff_written` を 1 件追記する |
| `show` | `domains/handoff/record.py` | 新設 | `show(target: str, cwd: Path, *, seq: Optional[int] = None, history: bool = False) -> Handoff` | 最後の（か `seq` の）引継ぎを返す。`history` なら、その版までの全版の `history` の差分を古い順につないだものを返す |
| `listing` | `domains/handoff/record.py` | 新設 | `listing(cwd: Path, *, open_only: bool = False) -> list[HandoffSummary]` | このリポジトリのステートマシンで引継ぎを持つものを並べる（`ndf handoff list`） |
| `read_handoff` | `progress/store.py` | 新設 | `read_handoff(conn, machine_id, *, seq: Optional[int] = None) -> Optional[model.Event]` | `handoff_written` の 1 件を `payload` つきで読む。`seq` が無ければ `ORDER BY seq DESC LIMIT 1`、あれば `seq = :seq` の完全一致。引継ぎのコマンドだけが使う |
| `read_handoff_histories` | `progress/store.py` | 新設 | `read_handoff_histories(conn, machine_id, *, upto: int) -> list[str]` | `seq <= upto` の `handoff_written` の `payload` の `history` だけを古い順に読む（本文を転送しない。SQLAlchemy の JSON の添字 `sa.type_coerce(payload, sa.JSON)["history"].as_string()` で SQLite と PostgreSQL の差を吸収する）。`show --history` だけが使う |
| `read_events` | `progress/store.py` | 変更 | 同じ | `handoff_written` の行は `payload` を SQL の `CASE` で `NULL` にして返す（本文と履歴を転送しない）。状態の畳み込み・`append_events` の確かめ・`progress/command.py` の `history`（`Event.as_dict()`）はこの形を使うので、本文が出ない |
| `check_text`・`trim_text` | `domains/records/handoff.py` | 変更 | `check_text(body: str, root: Path, limits: Limits) -> Report`・`trim_text(body: str) -> tuple[str, str]` | 今の検査と退避を文字列で行う。`root` は今の `check_file` の `root` と同じ役で、パスの存在の確かめ（`missing_path`）の相対パスの起点。`write` は `places.main_dir(cwd)` を渡すので、どの worktree・サブディレクトリから打っても同じ基準になる |
| `register` | `domains/handoff/cli.py` | 新設 | `register(sub: argparse._SubParsersAction) -> None` | 3 つのサブコマンドを足す |

- `write`
  - 事前条件: `target` は `<階層>:<キー>:<名前>` か `<階層>:<名前>`。後者はリポジトリを `cwd` から求めて `progress.model.machine_id` で完全な ID にする。ステートマシンが DB にある
  - 事後条件: 形が通れば、進行の事象に `kind = handoff_written`・`actor`・`payload = {"body", "history", "lines", "bytes"}` の 1 件が増えている。`history` はこの版で増えた分（差分）だけを持つ: `trim` なら `trim_text` で移したセクション、`trim` でなければ空文字列。蓄積した履歴は `show --history` が全版の差分をつないで返すので、通常の `write` を挟んでも前の版までの履歴は消えず、版ごとに累積を複写しないので重複も増えない。通らなければ何も書かない
  - 手順（1 トランザクション。今の `store.transaction`）: (1) `read_machine` で階層を得る。(2) 階層が `sprint` なら `read_definition` で今の定義の改訂 `rev` を得て下書きに入れる（`lane`・`plan` は `rev` を入れない）。(3) `read_events` で末尾の番号 `last`（事象が無ければ 0）を得る。(4) `append_events(conn, machine_id, [draft], expect_seq=last)`
  - 競合: 進行の更新やスプリントの書き込みが (3) と (4) の間に事象を足すと `Conflict` になる。`handoff_written` は状態を変えないので、トランザクションを捨てて (1) から読み直し、`rev` も読み直して最大 3 回やり直す。3 回とも `Conflict` なら書かずに `store_busy` で止める（終了コードは今の `store_busy`）。スプリントの定義が改訂されていれば新しい `rev` で書く
  - 副作用: DB への 1 トランザクション（今の事象の追記と同じ経路・同じ排他）
- `show`: 事後条件: `seq` が無ければ最後の `handoff_written`、あれば `seq` の版。本文は `read_handoff` の 1 件だけを読み、過去の版の本文もほかの事象も読まない。`history` のときだけ、続けて `read_handoff_histories(upto=<返す版の seq>)` で履歴の差分だけを読む。読むだけ
- `listing`: `ndf_progress_machines` の `repo` がこのリポジトリのものに絞り、各ステートマシンの最後の `handoff_written`（番号と時刻だけ。本文は読まない。無ければ空）と今の状態を返す。`handoff_written` を持つものだけを返す。`open_only` の「閉じていない」は階層ごとに決める: `sprint`・`plan` は `model.table_state` が `model.is_terminal` でない。`lane`（全体の状態を持たない）は、`model` で畳んだ行の状態のうち `is_terminal` でないものが 1 つ以上ある（行がまだ無いレーンは開いている）。すべての行が `completed`・`cancelled` のレーンは閉じている
- 再開の候補の絞り込み（手順 0、`block-next`）: `listing(open_only=True)` のうち、名前が入力に完全一致するものだけを候補にする（スプリントは課題番号から `machine_id` と同じ計算で作った名前、ループは「未確認のまま残ること」の 2 で決める ID）。部分一致や課題番号を含むだけの名前は候補にしない。閉じたステートマシンは候補にしない。`list --open` は DB に引継ぎがある対象だけを返すので、候補が 0 件なら、入力から求めた ID で `show` を打って前の版の文書を確かめてから「引継ぎなし」とする（下の「切り替えの間の再開」）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write` | 形の違反 | 本文 | `check_text` | 書かずに返す | 終了コード 1、`items[].violation` | 直して再実行 |
| `write`・`show` | ステートマシンが無い・まだ引継ぎが無い | ID の誤り・対象の選び違い | `MACHINES` の引き当て | 止める | 終了コード 2 | 再実行可 |
| `write`・`show`・`listing` | DB に繋げない・取り合い | `NDF_PROGRESS_DB`・SQLite の錠 | `progress.store` | 今の `store_unreachable`・`store_busy` のまま | 今の終了コード | 再実行可（`store_busy` は今の再試行） |
| `write` | 末尾の番号がずれた | 進行の更新・スプリントの書き込みと同時 | `append_events` の `Conflict` | 読み直して最大 3 回やり直し、尽きたら止める | 尽きたら `store_busy` の終了コード | 書いていない。再実行可 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 事象の決まりの行 | 設定値 | `contracts/workflow/progress.json` | `{"level": "<sprint・lane・plan>", "kind": "handoff_written", "actors": ["conductor", "organizer"], "scope": "table"}` の 3 行 |
| 再試行の回数 | 設定値 | `contracts/limits.json` の `handoff.conflict_retries` | 3 |
| 上限 | 設定値 | `contracts/limits.json` | 今の `handoff.lines` 300・`handoff.bytes` 16,000 |
| `HANDOFF_SHOW` | 定数 | `relay/context_records.py` | `ndf handoff show` の語の正規表現（切り替えの間は前の版の `.ndf/handoff/<名>.md` の語も） |
| `LEGACY_NAMES` | 定数 | `domains/handoff/record.py` | 旧ファイル名の前置き（`issue-`・`sprint-`・`parallel-`・無し）と移し先の階層の表（「旧ファイル名と移し先」） |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `write` | `machine_id`・`seq`・`lines`・`bytes`・違反 | 結果 JSON | 本文（結果 JSON に入れない） |
| `show` | 本文 | 標準出力（`--json` なら結果 JSON の `outputs.body`） | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `write` | 書いて `show` で同じ本文・スプリントに今の `rev` で書く | 300 行ちょうど・16,000 バイトちょうど・`--trim` で移すセクションが無い・`--trim` の後に通常の `write` を挟んでも `show --history` が前の履歴を返す | 301 行・無い ID・DB に繋げない・`append_events` の `Conflict` を 1 回差し込むとやり直して書ける・3 回で `store_busy` |
| `show` | 最後の版・`--seq` の版・`--history` | 版が 1 つ・通常の `show` が過去の版の本文を読まない（`read_handoff` の SQL が 1 行） | 引継ぎが無い・無い `--seq` |
| `listing` | 引継ぎを持つものだけ・`--open` | 0 件・行がすべて `completed` のレーンは `--open` に出ない・行の無いレーンは出る | 別のリポジトリのステートマシンを含めない |
| `read_events` | `handoff_written` の `payload` が `None` で、ほかの事象は今のまま | — | — |
