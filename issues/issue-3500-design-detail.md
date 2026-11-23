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
    ├── core/places.py *                # resolve・prepare・main_dir・adopt_legacy・retired_slot・sweep
    ├── core/places_cli.py *            # ndf places path / sweep
    ├── domains/handoff/__init__.py *
    ├── domains/handoff/record.py *     # write・show・listing・import_file
    ├── progress/store.py               # read_handoff・read_handoff_histories・read_handoff_sources・read_events の本文の除外
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
    ├── domains/worktree/removal.py         # 外す前に前の版の計画の記録を移す
    ├── domains/crossrefactor/launch.py・workspace.py・drive.py・final_gate.py
    └── checks/places_check.py *        # 起点を自分で決める形を落とす
```

依存の向き: 呼び手（`progress`・`domains/*`）→ `core.places` → `contracts.facts`・`adapters.git` の一方向。`domains.handoff` → `progress.store`（事象の読み書き）・`domains.records.handoff`（形の検査）・`progress.model`（`machine_id`）。`core.places` は `domains` と `progress` を読まない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/core/places.py` | 新設 | 置き場の名前から起点どおりのパスを求め、作って無視させ、前の版の置き場から移し、残骸を退避する | `contracts.facts`・`adapters.git` | モジュール | `resolve`・`prepare`・`main_dir`・`adopt_legacy`・`retired_slot`・`sweep`・`_locate`（新設）、`PlaceError`（新設） |
| `plugins/ndf/src/ndf/core/places_cli.py` | 新設 | `ndf places path`・`ndf places sweep` | `core.places`・`contracts` | モジュール | `register`・`_path`・`_sweep`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/record.py` | 新設 | 引継ぎの事象の追記と読み出し | `progress.store`・`progress.model`・`domains.records.handoff` | モジュール | `write`・`show`・`listing`・`import_file`（新設） |
| `plugins/ndf/src/ndf/domains/handoff/cli.py` | 新設 | `ndf handoff write / show / list` | `domains.handoff.record` | モジュール | `register`（新設） |
| `plugins/ndf/src/ndf/progress/store.py` | 変更 | 引継ぎの本文を通常の読み出しから外す | — | モジュール | `read_handoff`・`read_handoff_histories`・`read_handoff_sources`（新設）・`read_events`（変更） |
| `plugins/ndf/src/ndf/domains/records/handoff.py` | 変更 | 本文の文字列を検査する形にする | — | モジュール | `check_text`・`trim_text`（変更。今の `check_file`・`trim_file` から読み書きを外す） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | `handoff check` を消す | — | モジュール | `_handoff`・`_add_handoff`（削除） |
| `plugins/ndf/src/ndf/domains/relay/context_records.py` | 変更 | 最初の発言の `ndf handoff show` で復元を見つける | — | モジュール | `HANDOFF_PATH` → `HANDOFF_SHOW`（変更） |
| `plugins/ndf/src/ndf/progress/plan/run.py` | 変更 | 計画の記録を `places.prepare("ndf-parallel")` の下に置く（前の版の記録の移し替えは `places.adopt_legacy` を `worktree/removal.py` が呼ぶ） | `core.places` | モジュール | `record_dir`（変更。シグネチャは同じ） |
| `plugins/ndf/src/ndf/domains/backlog/state.py`・`depth.py`・`progress/command.py` | 変更 | `main_dir` を `places` へ寄せ、設定のパスを `places` で求める | `core.places` | モジュール | `main_dir`（削除）・`state_dir`・`ndf_settings_path`（変更）・`PROJECT_FILE`・`MVV_FILE`（値の変更） |
| `plugins/ndf/src/ndf/core/converge/location.py` | 変更 | 作業場所の親を `places.resolve("ndf-work")` にする | `core.places` | モジュール | `default_base`・`work_root`（変更）、`main_dir`・`main_worktree`（削除。`places.main_dir` へ移す） |
| `plugins/ndf/src/ndf/domains/worktree/repo.py`・`create.py`・`setup.py` | 変更 | `.worktrees` を `prepare` で作り、登録を求めない。`.ndf/.gitignore` の行を `prepare` に任せる | `core.places` | モジュール | `locate`・`worktrees_dir`・`_check_location`・`init`（変更） |
| `plugins/ndf/src/ndf/domains/crossrefactor/launch.py`・`workspace.py`・`drive.py`・`final_gate.py` | 変更 | コードを書く工程の起点を取る前に `.cross_refactoring/` を作り直す。`info/exclude` をやめる | `core.places` | モジュール | `clear_results`（新設）・`_exclude_results`（削除）・`drive._record_phase`・`final_gate.run_gate`（変更）。`launch.run`・`final_gate_decide.verify_final_fix` は変えない |
| `plugins/ndf/src/ndf/domains/worktree/removal.py` | 変更 | worktree を外す前に、その worktree に前の版が書いた計画の記録をメインディレクトリへ移す | `core.places` | モジュール | `remove_evacuating`（変更） |
| `plugins/ndf/src/ndf/checks/places_check.py` | 新設 | 起点を自分で決める形と、一覧に無い置き場の id を落とす | `contracts.facts` | モジュール | `check`（新設） |

## 置き場の求め方（子の課題 1）

満たす決定: 決定 1・2・3、受け入れ条件 1〜4・7、不変条件 I1〜I3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `resolve` | `core/places.py` | 新設 | `resolve(place: str, cwd: Path, environ: Mapping[str, str] = os.environ, *, allow_retired: bool = False) -> Path` | 一覧の行の起点と path からパスを返す。作らない |
| `prepare` | `core/places.py` | 新設 | `prepare(place: str, cwd: Path, environ: Mapping[str, str] = os.environ, *, root: Optional[Path] = None) -> Path` | `resolve` のパスを作り、追跡しない行なら無視させて返す。`root` は起点 `worktree` の行で呼び手が worktree の根を既に持つときに渡し、渡すと起点を求める git を打たない（起点 `worktree` 以外の行に渡すと `PlaceError`（`invalid`）） |
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
| `record_dir` | `progress/plan/run.py` | 変更 | `record_dir(cwd: Path, plan: str) -> Path`（同じ） | `places.prepare("ndf-parallel", cwd)` の下の `<計画>` を返す。移し替えはしない（下の「計画の記録の移し替え」の `adopt_legacy` が持つ） |
| `adopt_legacy` | `core/places.py` | 新設 | `adopt_legacy(place: str, worktree: Path, cwd: Path) -> Adopted` | 起点 `main` の行について、前の版が `worktree` の根から数えて書いた所（`worktree / <path>`）の中身を、新しい置き場へ移す。`Adopted` は `moved`（移したパス）・`kept`（移さなかったパスと理由）を持つ（下の「計画の記録の移し替え」。子の課題 1） |
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
| `clear_results` | 退避できない・コミットできない・退避を消せない | git の錠・権限 | `rename`・`git commit`・`rmtree` | 止める（`GitError`・`OSError` のまま。担当は起動していない） | 今の起動の失敗と同じ | コミットの前に止まったら退避したフォルダを元へ戻す。再実行可（追跡が残っていれば同じ手順をやり直し、無ければ作り直すだけ） |
| `adopt_legacy` | 移し替え先に同じ名前がある・書き手が開いたまま別のファイルシステムにある・移せない | 前の版と今の版が別々に書いた・動いている `detach`・権限 | 移す前の `exists`・`st_dev` の比較と `/proc/*/fd` の走査・`rename` | 移さない（前の置き場は消さない）。`kept` に理由（`exists`・`writer_open`・`writer_unverifiable`・`os_error`）とパスを入れる | 呼んだコマンドの結果 JSON の `warnings` に `legacy_plan_record` とパス・理由（終了コード 0）。worktree を外すコマンドは、`kept` があるときその worktree を外さない | 人が中身を見て片付ける。再実行すると、書き手が終わっていれば移る |
| `_check_location` | `.worktrees` を作れない | 権限 | `prepare` | 止める | 終了コード 1 | 再実行可 |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 2 つのレーンが同時に `prepare` | フォルダの中の `.gitignore` | 取らない。中身は常に `*` だけなので、一時ファイルからの付け替えでどちらが勝っても同じ | 冪等 |
| 2 つのレーンが同時に `prepare`（違う行を足す） | 同じ `.ndf/.gitignore` | 同じ起点の `.ndf/.gitignore.lock`（`.ndf/.gitignore` に自身の行を持つ）を `fcntl.flock(LOCK_EX)` で取り、読み取り・行の追加・一時ファイルからの付け替えを錠の中で行う。後に取った側は先の側の付け替え後の版を読むので、違う行を同時に足しても両方残る | 冪等（同じ行は足さない） |
| 移す前の版と後の版の NDF | 錠 | 無い（設計文書の「移す間の危険」。全セッションを入れ替えてから使う） | — |

### 計画の記録の移し替え（子の課題 1）

前の版は計画の記録を linked worktree の根の `.ndf/parallel/<計画>/` に書いた。この記録が失われるのは worktree を外すときなので、移し替えは worktree を外す共通の手順に置く。計画を再始動する必要は無い。

- 入口: `domains/worktree/removal.remove_evacuating` が、テスト環境を閉じた後、無視したファイルを退避する前に `places.adopt_legacy("ndf-parallel", item.path, cwd)` を呼ぶ。`ndf worktree merged`・`ndf worktree cleanup` はどちらもこの手順を通るので、利用者が worktree を外す普通の経路で必ず移る。`kept` が 1 つでもあれば、その worktree は外さない（`Removal` の `refused` に `legacy_plan_record` を入れる）
- 結果 JSON: `merged`・`cleanup` の結果 JSON の `outputs.adopted[]` に移したパス（前と後）、`warnings[]` に `{"code": "legacy_plan_record", "path": <前の置き場>, "reason": <kept の理由>}` を出す
- `record_dir` と `detach` は移し替えをしない（計画の起動の経路に移し替えの git とファイル操作を足さない）

`adopt_legacy` は前の置き場の下の計画ごと（`<worktree>/.ndf/parallel/<計画>/`）に次の扱いをする。`worktree` がメインディレクトリなら何もしない。

| 前の置き場 | 新しい置き場 | 同じファイルシステムか（`st_dev`） | 扱い |
| --- | --- | --- | --- |
| ある | 無い | 同じ | `rename` で移す。動いている `detach` の子が前の `run.log` を開いていても、`rename` は開いたファイルを保つので書き足しは新しい置き場へ届く |
| ある | 無い | 違う | `/proc/*/fd` を走査し、前の置き場の下のファイルの inode を開いているプロセスがあれば移さない（`writer_open`）。`/proc` が無い環境は確かめられないので移さない（`writer_unverifiable`）。開いているプロセスが無いときだけ、新しい置き場の隣の一時フォルダへ複写し、`rename` で新しい置き場にしてから前の置き場を消す。書き手が開いたまま複写すると、複写の後の書き足しは前のファイルにだけ届き、前を消すと失われるため |
| ある | ある | どちらでも | 移さない（`exists`）。新しい置き場を読み書きし、前の置き場は消さない |
| 無い | どちらでも | — | 何もしない |

`writer_open` で外さなかった worktree は、書き手（`detach` で切り離した `ndf lanes run`）が終わった後の次の `merged`・`cleanup` で移ってから外れる。

### 起動ごとの git の呼び出し数

| 呼び出し | 増える git のコマンド | 再利用 |
| --- | --- | --- |
| `resolve`・`prepare`（起点 `worktree`） | `rev-parse --show-toplevel` 1 回 | 同じプロセスで同じ `cwd` の 2 回目からは 0 |
| `resolve`・`prepare`（起点 `main`） | `worktree list --porcelain` 1 回と、今の `main_worktree` の確かめに要る `rev-parse` | 同上 |
| `resolve`・`prepare`（起点 `git`） | `rev-parse --git-common-dir` 1 回 | 同上 |
| cross-refactoring の読み取りの工程の起動 | 0 | — |
| cross-refactoring の作業場所の用意（`workspace.prepare`、1 回の実行に 1 回） | −1（`_exclude_results` の `rev-parse --git-path info/exclude` が無くなる） | — |
| cross-refactoring のコードを書く工程の起動（`clear_results`） | `ls-files` 1 回。追跡されていたときだけ `commit` 1 回。起点は `run.ws.work` を `prepare(..., root=)` に渡すので 0 | 下の「起動ごとの索引の確かめ」 |
| `adopt_legacy`（worktree を外すとき） | 0（ファイルの `stat` と `rename` だけ） | — |

起動ごとの索引の確かめ: `ls-files` は、コードを書く工程の起動の数（1 回の実行で、項目ごとの `add-tests` と `implement`、フレーキーの直しの起動、最後の修正の周の数の和）だけ打つ。項目が 10 の実行なら約 20〜25 回で、1 回は数ミリ秒（担当の起動は 1 回が数分）。前の担当が `git add -f` で作業ファイルを新しく追跡したかは起動ごとに変わり、前の起動のコミットを調べる（`diff-tree`）のも git を 1 回打つので、`ls-files` を起動ごとに 1 回打つのが最小である。読み取りの工程では打たない。

### 起動の前の作り直し（`clear_results`、子の課題 2）

`git rm --cached` の後に `git commit -- <パス>` を打つと、パスを指定した `git commit` は索引でなく作業ツリーの中身を読むので削除にならない（[git commit の仕様](https://git-scm.com/docs/git-commit#_description) ）。そこで作業ツリーから先に退かし、索引には触れずにパスを指定してコミットする。

1. `git ls-files -z -- .cross_refactoring` が空なら 4 へ進む
2. `.cross_refactoring/` を、作業場所の外で同じファイルシステムの一時フォルダ（`scratch`。呼び手は `rf<PR>/logs` を渡す。その下に `tempfile.mkdtemp` で作る）へ `rename` で退避する
3. `git commit -q --no-verify -m <固定の題> -- .cross_refactoring` を打つ。作業ツリーにファイルが無いので、追跡されていたファイルの削除だけが 1 つのコミットに入り、ほかのステージ済みの変更は索引に残ってコミットに入らない（2026-11-23 に一時のリポジトリで確かめた）。失敗したら退避したフォルダを元へ戻して止める
4. 退避したフォルダ（あれば）と、残っている `.cross_refactoring/` を消し、`places.prepare("cross-refactoring", work, root=work)` で作り直す

工程の起点（`_record_phase` の HEAD、最後の修正の `before`）はこの後に読むので、NDF のコミットは起点より前にある。範囲の検査・フレーキーの直しの検査と集計は、起点から HEAD までの担当のコミットだけを見る。起点は今のまま実行の状態（`phases`）に保存されるので、NDF のコミットの SHA を別に保存して渡す必要は無い。

`prepare` の無視の処理（`.gitignore` の有無と `.ndf/.gitignore` の行の確かめ）はファイルの読み取りだけで、git を打たない。

## 引継ぎの記録（子の課題 3）

満たす決定: 決定 4、不変条件 I4〜I6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `write` | `domains/handoff/record.py` | 新設 | `write(target: str, body: str, cwd: Path, *, trim: bool = False, actor: str = "conductor") -> Written` | 形を検査し、通れば事象 `handoff_written` を 1 件追記する |
| `show` | `domains/handoff/record.py` | 新設 | `show(target: str, cwd: Path, *, seq: Optional[int] = None, history: bool = False) -> Handoff` | 最後の（か `seq` の）引継ぎを返す。`history` なら、その版までの全版の `history` の差分を古い順につないだものを返す |
| `listing` | `domains/handoff/record.py` | 新設 | `listing(cwd: Path, *, open_only: bool = False, with_handoff: bool = True) -> list[HandoffSummary]` | このリポジトリのステートマシンを並べる。`with_handoff` が真なら引継ぎを持つものだけ（`ndf handoff list`）、偽なら引継ぎの有無によらない（`import_file` の行き先の選定） |
| `import_file` | `domains/handoff/record.py` | 新設 | `import_file(name: str, cwd: Path, *, to: Optional[str], now: datetime) -> Imported` | 前の版の `.ndf/handoff/<名>.md` と `<名>-history.md` を移す（下の「前の版の引継ぎ文書の移し替え」） |
| `read_handoff` | `progress/store.py` | 新設 | `read_handoff(conn, machine_id, *, seq: Optional[int] = None) -> Optional[model.Event]` | `handoff_written` の 1 件を `payload` つきで読む。`seq` が無ければ `ORDER BY seq DESC LIMIT 1`、あれば `seq = :seq` の完全一致。引継ぎのコマンドだけが使う |
| `read_handoff_histories` | `progress/store.py` | 新設 | `read_handoff_histories(conn, machine_id, *, upto: int) -> list[str]` | `seq <= upto` の `handoff_written` の `payload` の `history` だけを古い順に読む（本文を転送しない。SQLAlchemy の JSON の添字 `sa.type_coerce(payload, sa.JSON)["history"].as_string()` で SQLite と PostgreSQL の差を吸収する）。`show --history` だけが使う |
| `read_handoff_sources` | `progress/store.py` | 新設 | `read_handoff_sources(conn, machine_id) -> list[tuple[int, Optional[str], Optional[dict[str, str]]]]` | 各 `handoff_written` の `seq`・`payload` の `source`・`source_sha256` だけを読む（本文を転送しない）。`import_file` の再開と新版の判定が使う |
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
- `listing`: `ndf_progress_machines` の `repo` がこのリポジトリのものに絞り、各ステートマシンの最後の `handoff_written`（番号と時刻だけ。本文は読まない。無ければ空）と今の状態を返す。`with_handoff` が真なら `handoff_written` を持つものだけを返す。`open_only` の「閉じていない」は階層ごとに決める: `sprint`・`plan` は `model.table_state` が `model.is_terminal` でない。`lane`（全体の状態を持たない）は、`model` で畳んだ行の状態のうち `is_terminal` でないものが 1 つ以上ある（行がまだ無いレーンは開いている）。すべての行が `completed`・`cancelled` のレーンは閉じている
- 再開の候補の絞り込み（手順 0、`block-next`）: `listing(open_only=True)` のうち、名前が入力に完全一致するものだけを候補にする（スプリントは課題番号から `machine_id` と同じ計算で作った名前、ループは「未確認のまま残ること」の 2 で決める ID）。部分一致や課題番号を含むだけの名前は候補にしない。閉じたステートマシンは候補にしない。`list --open` は DB に引継ぎがある対象だけを返すので、候補が 0 件なら、入力から求めた ID で `show` を打って前の版の文書を確かめてから「引継ぎなし」とする（下の「切り替えの間の再開」）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `write` | 形の違反 | 本文 | `check_text` | 書かずに返す | 終了コード 1、`items[].violation` | 直して再実行 |
| `write`・`show` | ステートマシンが無い・まだ引継ぎが無い | ID の誤り・対象の選び違い | `MACHINES` の引き当て | 止める | 終了コード 2 | 再実行可 |
| `write`・`show`・`listing` | DB に繋げない・取り合い | `NDF_PROGRESS_DB`・SQLite の錠 | `progress.store` | 今の `store_unreachable`・`store_busy` のまま | 今の終了コード | 再実行可（`store_busy` は今の再試行） |
| `write` | 末尾の番号がずれた | 進行の更新・スプリントの書き込みと同時 | `append_events` の `Conflict` | 読み直して最大 3 回やり直し、尽きたら止める | 尽きたら `store_busy` の終了コード | 書いていない。再実行可 |
| `import_file` | 書いた版を読み直すと本文か履歴が違う | DB の取り合い・文字コード | 書いた後の `read_handoff` | ファイルを動かさずに止める | 終了コード 1 と理由 | ファイルは元の所にある。再実行可（書いた事象は残るが、`source` と SHA-256 が同じ版は 2 回目に足さない） |
| `import_file` | 名前が単一のファイル名でない・本文か履歴が旧フォルダの中の通常のファイルでない | 入力の誤り・symlink・別の種類のファイル | 読む前の名前の検査と、記述子でたどった旧フォルダの要素と `lstat` | 読まずに止める | 終了コード 2 と理由（`invalid_name`・`not_regular_file`・`outside_folder`） | 何もしていない。再実行可 |
| `import_file` | 行き先を 1 つに決められない | 名前に完全一致する閉じていないステートマシンが 2 つ以上 | 行き先の選定 | 書かずに止める | 終了コード 2 と候補の ID | 何もしていない。`--to <ID>` で再実行 |
| `import_file` | 退避の途中で失敗した | 権限・ディスク | `rename` | 止める | 終了コード 1 と、動かしたファイルと残ったファイル | 再実行で残りだけを動かす（下の「再開」） |

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
| `import_file` | 本文と履歴が 1 版に入り、2 つのファイルが退避先へ動く・閉じたステートマシンと無いステートマシンの文書は書かずに動く | 履歴のファイルが無い・新方式の版が既にある対象は書かずに動く・本文だけ動いた後の再実行で履歴だけが動き版が増えない・引継ぎを持たない閉じていないステートマシンが行き先に選ばれる・`issue-<番号>`・`sprint-<名>`・`parallel-<名>` の旧ファイル名がそれぞれのステートマシンへ移る・`show sprint:<名>` が `sprint-<名>.md` を `outputs.legacy` に返す・`list --open` が 0 件でも旧文書だけの対象は手順 0 の `show` で見つかる | 書いた版の読み直しが違うと動かさない・`sprint-<名>` が同じ名前の閉じていないスプリントとレーンの両方に当たると終了コード 2・`../x`・`a/b` の名前・外を指す symlink の本文と履歴・外を指す symlink の `.ndf/handoff` と `.ndf`（読まず・動かさない）・同じ名前の閉じていないステートマシンが 2 つ |

### 前の版の引継ぎ文書の移し替え（子の課題 3）

再開の読み先を DB に切り替える子の課題 3 の PR に、移し替えも入れる。切り替えた版を入れた時点で、前の版の文書から DB へ移す手段がそろっている。

`ndf handoff import <名> [--to <ID>]` が `import_file` を呼ぶ。読むのは `places.resolve("retired-handoff", cwd, allow_retired=True)`（メインディレクトリの `.ndf/handoff`）の下の `<名>.md` と `<名>-history.md`（無ければ空）で、AI はパスを渡さず名前だけを渡す（一覧の `retired-handoff` の行は子の課題 3 で足す。`sweep` は子の課題 4 まで無い）。

- 読む前の検査（1 つでも当たれば読まず・書かず・動かさずに終了コード 2）
  1. `invalid_name`: `<名>` が `^[A-Za-z0-9][A-Za-z0-9._-]*$` に一致しない、`..` を含む、`-history` で終わる（区切り文字・親ディレクトリ・絶対パスを受けない）
  2. `not_regular_file`: 本文と（あれば）履歴を `lstat` して、通常のファイルでない（symlink・ディレクトリ・FIFO など）
  3. `outside_folder`: メインディレクトリから旧フォルダまでのパスの要素（`.ndf`・`.ndf/handoff`）のどれかが symlink かディレクトリでない。`realpath` の比較は使わない（本文の親と旧フォルダは同じパスなので、`.ndf/handoff` が外を指す symlink でも一致してしまう）
  - 旧フォルダは記述子でたどって開く: メインディレクトリを `os.open(main, O_RDONLY | O_DIRECTORY)` で開き、そこから `.ndf`、`handoff` を順に `os.open(<要素>, O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=<親の記述子>)` で開く（要素が symlink なら `ELOOP`、ディレクトリでなければ `ENOTDIR` で `outside_folder`）。以後の検査・読み取り・退避は、この検査済みの旧フォルダの記述子を基準にし、パスの文字列で旧フォルダを引き直さない
  - 本文と履歴の `lstat` は `os.lstat(<名>.md, dir_fd=<旧フォルダの記述子>)`、読むときは `os.open(<名>.md, O_RDONLY | O_NOFOLLOW, dir_fd=<旧フォルダの記述子>)` で開き、開いた記述子の `fstat` が `lstat` と同じ inode であることを確かめる（検査と読み取りの間に差し替えられたものを読まない）。退避は `os.rename(<名>.md, <名>.md, src_dir_fd=<旧フォルダの記述子>, dst_dir_fd=<退避先の記述子>)` で行い、その前にも同じ `lstat` をもう一度取って inode が変わっていれば動かさない。途中で親の要素が symlink に差し替えられても、開いた記述子は元のディレクトリを指したままなので外のファイルを読まず・動かさない
- 行き先の選定（引継ぎの事象の有無に依らず、ステートマシンの状態で決める）
  - `--to <ID>` があればその ID。無ければ下の「旧ファイル名と移し先」で `<名>` から求めた（階層, 名前）の組に、階層と名前の両方が完全一致する `listing(open_only=True, with_handoff=False)` の項目。1 つならそこへ書く。2 つ以上なら止める（終了コード 2 と候補）
  - 0 なら書かずに退避だけする（閉じたステートマシンの文書と、ステートマシンの無い 10 系の対象の文書。14 日の間は戻せる）。`--drop` の選択肢は無くし、AI が稼働中かを判断しない
- 書く（行き先があるとき）: 先に `read_handoff_sources` で行き先の版を読む
  - 新版を優先: `source` を持たない `handoff_written`（`ndf handoff write` が書いた版）が 1 つでもあれば、前の版の文書を書かずに退避だけし、`outputs.superseded = true` を返す（`show` の最新版を古い内容で上書きしない）
  - 移し済み: `source` が `.ndf/handoff/<名>.md` の版があれば書かない（下の「再開」）
  - どちらでもなければ、本文を `check_text` に通し、`write` と同じ手順で `payload = {"body": <本文>, "history": <履歴のファイルの中身>, "lines", "bytes", "source": ".ndf/handoff/<名>.md", "source_sha256": {"body": ..., "history": ...}}` の 1 件を足す。トランザクションの後で `read_handoff(seq=<書いた seq>)` で読み直し、本文・履歴・SHA-256 が一致したときだけ退避へ進む
- 退避: `places.retired_slot(cwd, now)`（下の「残骸の退避」の、実行ごとに衝突しない退避先）の下の `main/.ndf/handoff/` へ、本文、履歴の順に `rename` する（行き先に同じ名前があれば上書きせずに止める）
- 再開: 退避の途中で止まった後の再実行は、DB の移し済みの版の `source_sha256` を正とする。旧フォルダに残っているファイルのうち、SHA-256 がその版の記録と一致するものだけを退避し、版は足さない。本文が既に退避済みで履歴だけが残る場合も、履歴の SHA-256 で同じ版の残りと分かるので退避できる。一致しないファイルは動かさず終了コード 1（移した後に人が書き足した内容を失わない）
- `.ndf/handoff` のフォルダは `retired` の値 `empty-dir` の行で、`sweep`（子の課題 4）は中が空になるまで動かさない。移していない文書が残る間は `items[]` に `not_empty` と残りの数が出る
- 旧ファイル名と移し先: 前の版の手順書は、ステートマシンの名前と違うファイル名で文書を書いた（ai-plugins の実物は `issue-<番号>`・`sprint-<名>`・`parallel-<計画>` と、それ以外の名前）。名前と ID の対応は `domains/handoff/record.py` の定数 `LEGACY_NAMES` 1 か所が持ち、`import_file` の行き先の選定と `show` の旧文書の確かめが同じ表を両方向に使う

  | 旧ファイル名（`.md` を除く） | 移し先の候補（階層, 名前） | ID から旧ファイル名を作る向き（`show`） |
  | --- | --- | --- |
  | `issue-<番号>` | (`sprint`, 課題番号から `machine_id` と同じ計算で作ったスプリントの名前) | `sprint` の ID の名前が課題番号から作った形なら `issue-<番号>` |
  | `sprint-<名>` | (`sprint`, `<名>`)・(`lane`, `<名>`) | `sprint`・`lane` の ID なら `sprint-<名>` |
  | `parallel-<名>` | (`plan`, `<名>`) | `plan` の ID なら `parallel-<名>` |
  | それ以外 | (`sprint`, `<名>`)・(`lane`, `<名>`)・(`plan`, `<名>`) | どの ID でも `<名>`（ID の名前の部分そのまま） |

  候補のどれにも当たらなければ行き先は 0（退避だけ）、2 つ以上に当たれば終了コード 2 と候補（`--to` で選ぶ）
- 切り替えの間の再開: 手順 0 は、次のどの入口でも「引継ぎなし」と判断する前に前の版の文書を確かめる
  - 入力に ID がある・`list --open` が 0 件で入力から ID を求めた: `ndf handoff show <ID>` が「まだ引継ぎが無い」（終了コード 2）を返し、`outputs.legacy` に旧フォルダの文書の名前があるときは、結果 JSON の `outputs.next`（`ndf handoff import <名> --to <ID>`）を打ってから `show` をやり直す。`show` は上の表の右の列で ID から旧ファイル名を作り（2 つ以上あれば全部）、有無だけを `lstat` で確かめ、読まない。ステートマシンがまだ DB に無い ID でも旧文書の有無は返す（`next` は付けず、`import` は退避だけになる）
  - 再開コマンドが前の版のパス（`.ndf/handoff/<名>.md`）を含む: 手順 0 はパスから `<名>` だけを取り出して `ndf handoff import <名>` を打ち、結果 JSON の `outputs.to`（行き先の ID）で `show` を読む。行き先が 0 なら文書は退避だけされ、手順 0 は「引継ぎなし」として状態の確かめから始める（閉じた対象の文書）。復元の検出（`HANDOFF_SHOW`）は、切り替えの間は前の版のパスの語も検出する

## 残骸の退避（子の課題 4）

満たす決定: 決定 5、不変条件 I7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `sweep` | `core/places.py` | 新設 | `sweep(cwd: Path, *, now: datetime, keep_days: int, environ: Mapping[str, str] = os.environ) -> SweepResult` | `retired` の行のパスを、行の前提条件と移す前の検査を通ったものだけ退避し、`keep_days` を過ぎた退避を消す |
| `retired_slot` | `core/places.py` | 新設 | `retired_slot(cwd: Path, now: datetime) -> Path` | 実行ごとに衝突しない退避先を `.git/ndf/retired/` の下に作って返す（`sweep` と `import_file` が使う。子の課題 3 で足す） |
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
    - `worktree-parent`（`.worktrees/.ndf`）: `git worktree remove` は戻せない（無視されたファイルは黙って消え、detached HEAD にしかないコミットはどの参照にも残らない）。そこで、`git worktree list --porcelain` で候補の下に登録された worktree を並べ、次をすべて満たすものだけを `git worktree remove`（`--force` を付けない）で外す。1 つでも満たさない worktree は外さずに理由を出す
      1. NDF の終わった作業場所である: パスが cross 系の作業場所の並び（`<候補>/rf<番号>/work`・`<候補>/rf<番号>/read/<ランタイム>`）に一致し、ロックが無い（NDF は動いている作業場所に `lock_review_worktree` でロックを掛け、片付けで外す）。違えば `not_ndf_workspace`・`locked`
      2. 最近使われていない: その worktree の管理ディレクトリ（`.git/worktrees/<名>/`）の `HEAD`・`index` の更新時刻が `places.recent_hours`（`contracts/limits.json`、既定 24 時間）より前。違えば `recently_used`
      3. 固有のコミットが無い: HEAD が `git for-each-ref --count=1 --contains <HEAD> refs/heads refs/remotes refs/tags` で 1 つ以上の参照から届く（`rev-list --all` は他の worktree の HEAD を含むので使わない）。届かなければ `unreachable_commits`
      4. 失うファイルが無い: `git -C <worktree> status --porcelain --ignored --untracked-files=all` が空（変更・未追跡・無視されたファイルが無い）。空でなければ `local_files`（`.cross_refactoring/` の結果が残る作業場所もここで残る。`prepare` が置いた `.cross_refactoring/.gitignore` だけのものは数えない）

      外した後にもう一度並べ、登録された worktree が 1 つでも残っていれば親を動かさず `registered_worktrees_remain` と残りのパスと理由を出す。残りが 0 のときだけ親を `rename` で退避する。外した worktree で失われるのは、参照から届くコミットと同じ中身の作業ツリーと Git の登録だけである
    - `empty-dir`（`.ndf/handoff`）: 中にファイルが 1 つでもあれば `not_empty`。前の版の文書は `ndf handoff import` が 1 件ずつ移す（「前の版の引継ぎ文書の移し替え」）
  - 退避先（`places.retired_slot(cwd, now) -> Path`。`sweep` と `import_file` が使う）: 実行ごとに `.git/ndf/retired/` の下へ `tempfile.mkdtemp(prefix="<now の UTC の YYYYMMDDTHHMMSS>-")` で新しいフォルダを作って返す。名前の後ろの乱数で、同じリポジトリの別の worktree から同じ秒に打っても行き先が重ならない。1 回の実行の中では、候補ごとに `<退避先>/<起点>/<path>` が違う（起点と path の組は一覧で一意）
  - 上書きしない: `rename` の前に行き先の親を作り、行き先が既にあれば動かさず `dest_exists` とする（`rename` はファイルや空のディレクトリを黙って置き換えるため）。`sweep` も `import_file` も既存の退避を上書きしない
  - 事後条件: 検査と前提条件を通った候補が `<退避先>/<起点>/<path>` に移っている。移したパスと移せなかったパス（理由つき）を返す。`.git/ndf/retired/` の下で名前の先頭の日時が `keep_days` より古いディレクトリは消えている
  - 副作用: 同じファイルシステムの中の `rename`（別のファイルシステムなら複写してから消す。残骸の候補には書き手がいないので、計画の記録のような開いたままの書き手の扱いは要らない。錠は `flock` の前提条件で開いた者がいないことを確かめてある）と、`worktree-parent` の上の 4 条件を満たした worktree の `git worktree remove`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `sweep` | 移せない | 権限・移す前の検査・行の前提条件 | `rename`・上の検査 | そのパスを飛ばして続ける | 結果 JSON の `items[]` に理由（`in_use`・`symlink`・`tracked`・`lock_held`・`lock_open_elsewhere`・`lock_reopened`・`lock_unverifiable`・`not_ndf_workspace`・`locked`・`recently_used`・`unreachable_commits`・`local_files`・`registered_worktrees_remain`・`not_empty`・`dest_exists`・`os_error`）。全体は終了コード 0（`warnings` あり） | 次の `sweep` で再試行 |
| `sweep` | 古い退避を消せない | 権限 | `rmtree` | 飛ばして続ける | 同上 | 同上 |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KEEP_DAYS` | 設定値 | `contracts/limits.json` の `places.retired_days` | 14 |
| 最近使った作業場所の時間 | 設定値 | `contracts/limits.json` の `places.recent_hours` | 24 |
| `retired` の行 | 設定値 | `locations.json` | 下の表 |

`retired` の行は、`retired-handoff`（子の課題 3 の `import_file` が読む）を除いて子の課題 4 の PR で `locations.json` に足す。子の課題 4 は子の課題 2・3 と #4237 の後に出す（設計文書の「子の課題の分け方と順番」）ので、「〜の後」という条件を一覧の値で表す必要は無い。`.ndf/loop` の行は、#4237 が閉じる前に子の課題 4 を出すなら足さない。

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
| `sweep` | `retired` のパスが退避先へ移り、結果に並ぶ | 13 日前と 15 日前の退避・`glob` の 0 件・2 回目の `sweep`（何もしない）・4 条件を満たす worktree だけの `.worktrees/.ndf` は外してから動く | 取れない錠（別のスレッドで `flock` を持つ）・同じ錠をほかの記述子で開いたまま・書けない退避先・`retired` でないパスを動かさない・`ndf-work` が `.worktrees/.ndf` を指すと動かさない・ロック中の worktree が残ると親を動かさない・detached HEAD にしかないコミットを持つ worktree・無視されたファイル（`.cross_refactoring/` の結果）が残る worktree・24 時間以内に使った worktree・NDF の並びでない worktree は外さない・同じ秒に 2 回打っても退避先が重ならず、行き先が既にあれば上書きしない・`.ndf` が外を指す symlink・追跡されたファイルを含む `.ndf/sprint-*`・中に文書が残る `.ndf/handoff` |
