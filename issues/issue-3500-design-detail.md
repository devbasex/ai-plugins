# #3500 の詳細設計: 作業ファイルの片付けと、NDF の一時フォルダの登録の初期化へのまとめ

設計文書 [issue-3500-design.md](./issue-3500-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 4 つあるため別ファイルにした。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/domains/locations.json
├── contracts/domains/ndf-files.json
├── src/ndf/domains/worktree/setup.py
├── src/ndf/domains/worktree/cli.py
├── src/ndf/domains/crossrefactor/workspace.py
├── src/ndf/domains/crossrefactor/drive.py
├── skills/worktree/SKILL.md
├── skills/development-workflow/SKILL.md
├── skills/cross-refactoring/docs/01-state-and-propose.md
└── tests/
    ├── domains/worktree/test_worktree_cli.py
    └── domains/crossrefactor/test_launch_and_phase.py
```

依存の向き: `worktree.cli` が `worktree.setup` を呼び、`setup` が置き場の表（`facts.rows("locations")`・`facts.rows("ndf-files")`）と git のアダプターを読む。`crossrefactor.drive` が `crossrefactor.workspace` を呼び、`workspace` は git のアダプターと `launch_decide.RESULT_DIR` を読む。worktree と crossrefactor は互いを読まず、共有するのは置き場の表だけなので、循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/contracts/domains/locations.json` | 変更 | 置き場の表に `.cross_refactoring` の行を足す | — | — | — |
| `plugins/ndf/contracts/domains/ndf-files.json` | 変更 | cross-refactoring の結果ファイルと進捗のファイルの行を足す | `locations.json` | — | — |
| `plugins/ndf/src/ndf/domains/worktree/setup.py` | 変更 | 登録すべき行を導き、欠けを出し、欠けた行だけを足す | 置き場の表・`adapters.git`・`atomic` | モジュール | `missing_ignores`（新設）・`register_ignores`（新設）・`init`（変更） |
| `plugins/ndf/src/ndf/domains/worktree/cli.py` | 変更 | `init` と `check` の結果に登録を載せる | `setup` | モジュール | `_init`（変更）・`_check`（変更） |
| `plugins/ndf/src/ndf/domains/crossrefactor/workspace.py` | 変更 | 登録を確かめ、起動の前に作業ファイルを片付ける | `adapters.git`・`launch_decide` | モジュール | `clear_results`（新設）・`_require_ignored`（新設）・`prepare`（変更）・`_exclude_results`（削除） |
| `plugins/ndf/src/ndf/domains/crossrefactor/drive.py` | 変更 | コードを書く工程の起動の前に片付けを呼び、登録の欠けを止まる理由にする | `workspace` | モジュール | `_launch_status`（変更）・`_open`（変更） |
| `plugins/ndf/skills/worktree/SKILL.md` | 変更 | 手順 2-2 のシェルの書き足しをやめ、`init` が足した行のコミットだけを残す | — | — | — |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 変更 | 手順 0 の表に「`check` が登録の欠けを出したら `ndf worktree init` を打つ」を足す | — | — | — |
| `plugins/ndf/skills/cross-refactoring/docs/01-state-and-propose.md` | 変更 | 作業場所の箇条を、登録の確かめと起動の前の片付けに書き換える | — | — | — |
| `plugins/ndf/tests/domains/worktree/test_worktree_cli.py` | 変更 | 受け入れ条件 3・4 | `worktree.cli` | — | — |
| `plugins/ndf/tests/domains/crossrefactor/test_launch_and_phase.py` | 変更 | 受け入れ条件 1・2・5・6 | `crossrefactor.drive` | — | — |

## 置き場の表（`locations.json`・`ndf-files.json`）

満たす決定: 決定 2、受け入れ条件 3

- `locations.json` に `{"id": "cross-refactoring-results", "root": "repo", "path": ".cross_refactoring", "tracked": false}` を足す（`root: repo` は担当が動く作業ディレクトリの根。表のほかの列は今の行と同じ形で埋める）
- `ndf-files.json` に、結果ファイル（`<runtime>-<phase>-<stem>-result.json`、`kind: exchange`、`ends: run`）と進捗のファイル（`<runtime>-<phase>-<stem>-progress.log`、`kind: record`、`ends: run`）の 2 行を、置き場 `cross-refactoring-results`・`tracked: false` で足す

## 登録（`worktree/setup.py`・`worktree/cli.py`）

満たす決定: 決定 2、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `missing_ignores` | `setup.py` | 新設 | `missing_ignores(main_dir: Path) -> list[Ignore]` | 登録すべき置き場のうち、無視の対象でないものを返す |
| `register_ignores` | `setup.py` | 新設 | `register_ignores(main_dir: Path) -> list[Ignore]` | 欠けた置き場の行を、置き場ごとの `.gitignore` へ足し、足したものを返す |
| `init` | `setup.py` | 変更 | 変えない | 設定を作るかどうかに依らず `register_ignores` を呼び、足したものを `Initialized` の新しい欄 `ignored` に載せる。`.ndf/.gitignore` を無いときだけ作る処理は `register_ignores` へ移す |
| `_init` | `cli.py` | 変更 | 変えない | 結果の `items` に足した置き場（ファイルと行）を載せ、`summary` に「`.gitignore` に N 行足した（コミットする）」を添える |
| `_check` | `cli.py` | 変更 | 変えない | 結果の `items` に `{"ignore_missing": [<パス>...]}` を載せる。ファイルは書かない |

- `Ignore`（`setup.py` の frozen dataclass、新設の型）: `path`（リポジトリの根からのパス。例 `.ndf/tmp`）・`file`（行を足す `.gitignore` のパス）・`line`（足す行。例 `tmp/`）
- `missing_ignores`
  - 事前条件: `main_dir` はリポジトリのメインディレクトリ
  - 返る行の出所: 置き場の表の `root` が `repo` か `main` で `tracked` が偽の行と、`ndf-files.json` の `worktree-local`（`.ndf/worktree.local.json`）。パスが `.ndf/` の下なら `file` は `.ndf/.gitignore`・`line` は `.ndf/` からの相対で末尾 `/`（ファイルは `/` を付けない）、それ以外は `file` は根の `.gitignore`・`line` はパスに末尾 `/`
  - 判定: `git check-ignore -q <path>/`（ファイルは `/` を付けない）の終了コードが 0 なら登録済み。1 なら欠け。それ以外は `SetupError`
- `register_ignores`
  - 事後条件: `missing_ignores` が空になる。既存の行は消さない。足すときは `file` の末尾に、無ければ改行を足してから `# NDF が置く追跡しないフォルダ（ndf worktree init が足した）` の見出しの行と足す行を書く（`atomic.write_text`）。`file` が symlink なら書かずに `SetupError`
  - 副作用: 根の `.gitignore` と `.ndf/.gitignore` を書く。コミットはしない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `missing_ignores` | `check-ignore` が 0・1 以外で終わる | git が無い・リポジトリでない | `missing_ignores` | 止める（`SetupError`） | `init`: `refused` と理由、終了コード 1。`check`: `precondition` と理由 | 何も書いていない。原因を除いて打ち直せる |
| `register_ignores` | 書けない | symlink・権限 | `register_ignores` | 止める（`SetupError`） | `refused` と理由 | 書けた分は残る。同じコマンドで打ち直せる（足すのは欠けだけ） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `register_ignores`（`init` を通す） | 空のリポジトリで根の 2 行と `.ndf/.gitignore` の 7 行が入る | 設定が既にある・2 回目で行が増えない・根の `tmp/` で `.ndf/tmp` が既に当たっていれば足さない・利用者の既存の行が残る | `.gitignore` が symlink |
| `_check` | 欠けが `ignore_missing` に並び、ファイルが変わらない | 欠けが無ければ空の並び | — |

## 作業場所（`crossrefactor/workspace.py`・`crossrefactor/drive.py`）

満たす決定: 決定 1・2、受け入れ条件 1・2・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `clear_results` | `workspace.py` | 新設 | `clear_results(g: git.Git) -> None` | 作業ディレクトリの `.cross_refactoring/` を消し、追跡されていれば索引から外す |
| `_require_ignored` | `workspace.py` | 新設 | `_require_ignored(g: git.Git) -> None` | `.cross_refactoring/` が無視の対象でなければ `ResultsNotIgnored` を上げる |
| `prepare` | `workspace.py` | 変更 | 変えない | `_exclude_results` の呼び出しを `_require_ignored(git.Git(ws.work))` に置き換える |
| `_exclude_results` | `workspace.py` | 削除 | — | `info/exclude` へ書かない |
| `_launch_status` | `drive.py` | 変更 | 変えない | 工程が `launch_decide.coding_phases()` に入るときだけ、`launch.run` の前に `workspace.clear_results(run.work)` を呼ぶ |
| `_open` | `drive.py` | 変更 | 変えない | `workspace.prepare` が上げた `ResultsNotIgnored` を `Finish("precondition", <案内>)` にする |

- `ResultsNotIgnored(Exception)`（`workspace.py`、新設の例外）
- `clear_results`
  - 事前条件: `g` は書き込み用の作業ディレクトリの git
  - 事後条件: `<作業ディレクトリ>/.cross_refactoring` が無く、`git ls-files -- .cross_refactoring` が空
  - 副作用: ディレクトリを消す（`shutil.rmtree`）。`ls-files -z -- .cross_refactoring` が空でなければ `rm -r -q --cached --ignore-unmatch -- .cross_refactoring` を打つ。ほかのファイル・HEAD・作業ツリーのほかの変更には触れない
- `_require_ignored`: `check-ignore -q .cross_refactoring/` が 0 なら何もしない。1 なら `ResultsNotIgnored`。それ以外は `GitError`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_require_ignored` | 登録が無い | 初期化を打っていない・登録のコミットより前に切ったブランチ | `workspace.prepare` | 止める（`ResultsNotIgnored` → `_open` が `precondition`） | `precondition`: 「`.cross_refactoring/` が `.gitignore` に無い。`ndf worktree init` を打って登録をコミットし、PR のブランチへベースを取り込んでから打ち直す」 | 担当は起動していない。登録を入れて打ち直せる |
| `clear_results` | 消せない・`rm --cached` が失敗 | 権限・git の誤り | `clear_results` | 止める（`OSError`・`GitError` のまま上げる。今の起動の失敗と同じ扱い） | 今の実行の停止と同じ | 担当は起動していない。打ち直すと項目の起点からやり直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `clear_results`（`_launch_status` を通す） | 前の作業ファイルが起動の時点で無い | 追跡されている作業ファイルが索引から外れ、偽物の次のコミットに削除が入る・ディレクトリが無ければ何もしない・読み取りの工程では呼ばれない | — |
| `_require_ignored`（`ndf refactor run` を通す） | 登録があれば続く | — | 登録が無ければ担当を起動せずに `precondition` |

## Skill の文書

満たす決定: 決定 1・2

- `skills/worktree/SKILL.md` の手順 2-2: シェルの `printf ... >> .gitignore` をやめ、「手順 0 の `ndf worktree init` が `.worktrees/` などの登録を足す。足した行（結果の `items`）があればコミットする」に書き換える
- `skills/development-workflow/SKILL.md` の手順 0: `ndf worktree check` の結果の `ignore_missing` が空でなければ `ndf worktree init` を打ち、足した行をコミットしてから手順 1 へ進む行を表に足す
- `skills/cross-refactoring/docs/01-state-and-propose.md` の「作業場所」: 「`.cross_refactoring/` は git の除外へ足す」を、「`.cross_refactoring/` が `.gitignore` に無ければ止まる（`ndf worktree init` が登録する）。コードを書く工程の担当を起動する前に、作業ディレクトリの `.cross_refactoring/` を消し、追跡されていれば索引から外す」に書き換える
