# #3308 の詳細設計: スプリントに属さないリリースを、本番チャネルの宣言の取り込み方で組む

設計文書 [issue-3308-design.md](issue-3308-design.md) の「構成要素」と「処理の流れ」を、ファイルと関数の粒度へ下ろした文書である。
語の意味（本番チャネルの宣言・開始時の記録・スナップショット）は設計文書の「用語」と用語集に従う。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── scripts/
│   ├── lib/
│   │   └── repo.py                              （本番チャネルの宣言を読む関数を足す）
│   ├── supervise_lib/
│   │   └── decl.py                              （種別リリースの読み先を替える）
│   └── tests/
│       └── test_release_production_flow.py *    （受け入れ条件 1〜4・7・I5・決定 2・可用性）
└── skills/worktree/references/
    └── declaration.md                           （読み先の規則の 1 行を直す）
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/repo.py` | 変更 | origin の本番チャネルの先頭にある `.ndf/worktree.json` を読み、宣言か読めない理由を返す | `proc`（git の起動） |
| `plugins/ndf/scripts/supervise_lib/decl.py` | 変更 | 種別リリースのプランで、ブランチ運用のスナップショットを本番チャネルの宣言から作る | `repo`・`branch_flow` |
| `plugins/ndf/scripts/tests/test_release_production_flow.py` | 新設 | bare の origin を持つ一時リポジトリで、まとめたリリースと `new release` のプランの読み先を確かめる | `supervise.py`・`lib/parallel_release.py`・`tests/inproc.py` |
| `plugins/ndf/skills/worktree/references/declaration.md` | 変更 | 宣言の書き方の手順書の「`branch_flow` の読み先」の行を、決定 1 の規則に合わせる | 無し |

依存の向きは `supervise_lib/decl.py` → `lib/repo.py` → `lib/proc.py` の一方向である。`repo.py` は今も `branch_flow` と `decl`（`lib/decl.py`）を読むが、
足す関数は `supervise_lib` を読まない。`supervise_lib/decl.py` は今も `repo` を読んでおり、新しい依存の向きを足さない。循環は生まれない。

## クラスと関数の構造

型（クラス）は変えない。ブランチ運用の値は、今と同じ `branch_flow.ResolvedFlow.as_snapshot` の辞書で渡す（設計文書の「構成要素」）。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/repo.py` | モジュール `repo` | `production_decl`（新設）・`_fetch_tracking`（新設）・定数 `PRODUCTION_FETCH_TIMEOUT`（新設） |
| `plugins/ndf/scripts/supervise_lib/decl.py` | モジュール `decl` | `apply_decls`（変更）・`flow_snapshot`（変更）・`production_flow_decl`（新設） |
| `plugins/ndf/scripts/lib/parallel_release.py` | モジュール `parallel_release` | 変えない。`plan_parallel_release` は種別リリースで `apply_decls` を呼んでおり、その結果がそのまま提示と本番のプランへ入る |
| `plugins/ndf/scripts/lib/branch_flow.py` | モジュール `branch_flow` | 変えない。読んだ宣言を `of_decl` で読み、`resolve` と `as_snapshot` でスナップショットにする |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `_fetch_tracking` | `plugins/ndf/scripts/lib/repo.py` | 新設 | `(root, name: str) -> bool` | origin の `refs/heads/<name>` を `refs/remotes/origin/<name>` へ取り込み直し、成否を返す | `root` は git の木。`name` は空でないブランチ名 | 真なら追跡ブランチは取り込んだ時点の origin の先頭を指す。偽なら追跡ブランチは呼ぶ前のまま（無ければ無いまま） | 外部コマンド（`git fetch`）。ネットワーク。`refs/remotes/origin/<name>` の書き換え |
| `production_decl` | `plugins/ndf/scripts/lib/repo.py` | 新設 | `(root, production: str \| None) -> tuple[dict \| None, str]` | 本番チャネルの宣言（`origin/<本番チャネル>` の `.ndf/worktree.json`）を読み、読めたら `(宣言, 読んだ場所)`、読めなければ `(None, 読めない理由)` を返す | `production` は呼び手が今の宣言と既定ブランチから解いた名前（I5）。`root` は `None` でもよい | 読めた宣言は `version == 1` の JSON オブジェクトである。2 つ目の値は、読めたときは `origin/<本番チャネル> の .ndf/worktree.json`、読めないときは理由の 1 文 | `_fetch_tracking` を 1 回呼ぶ。外部コマンド（`git rev-parse`・`git show`） |
| `production_flow_decl` | `plugins/ndf/scripts/supervise_lib/decl.py` | 新設 | `(a, roots: list[Path]) -> tuple[dict, str, str \| None]` | 種別リリースのプランのために、`branch_flow` を読む宣言・読んだ場所・読めない理由の 3 つを返す | `a.production_branch` は `apply_decls` が解き終えている | 1 つ目は読めた宣言か `{}`。2 つ目は `origin/<本番チャネル> の .ndf/worktree.json の branch_flow`。3 つ目は読めたら `None`、読めなければ理由 | `production_decl` を通した外部コマンドとネットワーク |
| `flow_snapshot` | `plugins/ndf/scripts/supervise_lib/decl.py` | 変更 | `(a, wt: dict, where: str = FLOW_SOURCE, why: str \| None = None) -> dict` | 渡された宣言の `branch_flow` からスナップショットを作り、出所の欄に読んだ場所か読めない理由を書く | `a.base`・`a.production_branch` は解き終えている | 引数に既にスナップショットがあればそれを返す。無ければ `source` の欄は、宣言に `branch_flow` があれば `where`、無ければ `既定値（<where> が無い）`、`why` があれば `既定値（<why>）` | 無し |
| `apply_decls` | `plugins/ndf/scripts/supervise_lib/decl.py` | 変更 | `(a) -> None`（変えない） | 引数に無い値を宣言から埋める。ブランチ運用の値だけ、種別で読み先を選ぶ | 今と同じ | 種別がリリースで引数にスナップショットが無ければ、`a.flow` は本番チャネルの宣言から作ったもの。ほかの種別は今と同じくメインディレクトリの宣言から作ったもの（I4） | 種別リリースだけ、`production_flow_decl` を通した外部コマンドとネットワークが加わる |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_fetch_tracking` | 取り込み直しが 0 以外で終わる | origin に本番チャネルが無い（終了コード 128）・origin が無い・URL に届かない・認証が要る | `proc.run` の終了コード | 既定値で続ける（偽を返す） | 無し（呼び手が出所の欄に書く） | 後始末なし。手元の追跡ブランチは書き換わらない（実測。下の「処理の手順」の後の注記）。打ち直せる |
| `_fetch_tracking` | 取り込み直しが時間内に終わらない | ネットワークの応答が無い | `subprocess.TimeoutExpired` | 既定値で続ける（偽を返す） | 無し | `subprocess.run` が子を止める。打ち直せる |
| `_fetch_tracking` | git を起動できない | git が無い | `OSError` | 既定値で続ける（偽を返す） | 無し | 後始末なし |
| `production_decl` | 本番チャネルの名前が無い | 宣言にも既定ブランチにも本番チャネルが無い | 引数 `production` が空 | 呼び出し元へ返す（`None` と理由） | 無し（呼び手が出所に書く） | 後始末なし |
| `production_decl` | git の木が無い | 宣言を探す木がどれも git でない | 引数 `root` が `None` | 呼び出し元へ返す（`None` と理由） | 無し | 後始末なし |
| `production_decl` | origin に本番チャネルの追跡ブランチが無い | 取り込みに失敗し、手元にも追跡ブランチが無い | `git rev-parse --verify --quiet` が 0 以外 | 呼び出し元へ返す（`None` と理由） | 無し | 後始末なし。origin に本番チャネルを作れば打ち直しで読める |
| `production_decl` | 宣言のファイルが無い | 本番チャネルの先頭に `.ndf/worktree.json` が無い | `git show` が 0 以外（`proc.git_out` が `None`） | 呼び出し元へ返す（`None` と理由） | 無し | 後始末なし |
| `production_decl` | JSON として読めない・最上位がオブジェクトでない | 本番チャネルの宣言が壊れている | `json.loads` の `ValueError`・型の判定 | 呼び出し元へ返す（`None` と理由。止めない。決定 3） | 無し | 後始末なし |
| `production_decl` | `version` が 1 でない | 本番チャネルの宣言が別の版の形 | `version` の比較 | 呼び出し元へ返す（`None` と理由。決定 3） | 無し | 後始末なし |
| `production_flow_decl` | 本番チャネルの宣言を読めない | `production_decl` の上の各行（名前が無い・木が無い・追跡ブランチ・ファイル・JSON・`version`） | `production_decl` の戻り値の 1 つ目が `None` | 既定値で続ける（`({}, 場所, 理由)` を返し、`flow_snapshot` が既定値の行で組む。I2） | 無し（スナップショットの出所に `既定値（<理由>）` が載る） | 後始末なし。本番チャネルの宣言を直せば打ち直しで読める |
| `flow_snapshot` | `branch_flow` が取り込み方の表に合わない・知らないキーを持つ | 渡された宣言の値の誤り | `branch_flow.of_decl` の `FlowError` | 止める（`DeclError`。既定値へ倒さない。I3） | `DeclError` の文言は `<where>: branch_flow.<キー> = <値>（許す値: …）`。`new release` は `argparse` の `error` で終了コード 2 | 何も書かずに止まる。宣言を直して打ち直せる |
| `apply_decls` | 種別リリースで `DeclError` | `flow_snapshot` の I3 | `flow_snapshot` | 呼び出し元へ返す（例外をそのまま上げる） | `new release`: 終了コード 2 と直すキー。まとめたリリース: `build_plans` が `present.json`・`release.json` を書く前に例外が上がり、プランを書かない | 後始末なし。本番チャネルの宣言を直して打ち直せる |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `apply_decls` | 種別がリリースで、引数のスナップショットが空 | `production_flow_decl` の結果で `flow_snapshot` を呼ぶ（I1） |
| `apply_decls` | 種別がリリースで、引数にスナップショットがある | `flow_snapshot` が引数のスナップショットを返す。本番チャネルの宣言を読まない（スプリントの中のリリースは `plan_release` を直接呼び、`apply_decls` を通らない） |
| `apply_decls` | 種別が `sprint`・`close` ほか | 今と同じくメインディレクトリの宣言で `flow_snapshot` を呼ぶ。`sprint_waves.pin_flow` が開始時の記録で置き換える（I4） |
| `production_decl` | `production` が空 | `(None, "本番チャネルの名前が決まらない")` |
| `production_decl` | `root` が `None` | `(None, "宣言を探す木に git の木が無い")` |
| `production_decl` | 取り込みの成否に依らず、`refs/remotes/origin/<本番チャネル>` が無い | `(None, "origin/<本番チャネル> が無い")` |
| `production_decl` | 追跡ブランチはあり、`.ndf/worktree.json` が無い | `(None, "origin/<本番チャネル> に .ndf/worktree.json が無い")` |
| `production_decl` | ファイルはあるが JSON として読めないか、最上位がオブジェクトでない | `(None, "origin/<本番チャネル> の .ndf/worktree.json を JSON のオブジェクトとして読めない")` |
| `production_decl` | オブジェクトだが `version` が 1 でない | `(None, "origin/<本番チャネル> の .ndf/worktree.json の version が 1 でない")` |
| `production_decl` | オブジェクトで `version` が 1 | `(宣言, "origin/<本番チャネル> の .ndf/worktree.json")` |
| `flow_snapshot` | `why` がある | `branch_flow` を読まずに既定値の行のスナップショット。出所は `既定値（<why>）`（I2） |
| `flow_snapshot` | `why` が無く、宣言に `branch_flow` が無い | 既定値の行。出所は `既定値（<where> が無い）`（受け入れ条件 2 の `promotion` を書かない宣言と同じ結果） |
| `flow_snapshot` | `why` が無く、`branch_flow` が表に合う | 宣言の行。出所は `where` |
| `flow_snapshot` | `why` が無く、`branch_flow` が表に合わない | `DeclError(f"{where}: {FlowError の文言}")`（I3） |

- 繰り返しの終わる条件: 繰り返しは無い。取り込み直しは 1 回だけで、再試行しない
- 計算量の注意: 種別リリースのプランを 1 回組むごとに、`git fetch` を 1 回、`git rev-parse` と `git show` を 1 回ずつ打つ。所要は設計文書の「未確認のまま残ること」の取り込み直しの時間に当たる
- 取り込み直しが失敗したとき（origin に本番チャネルが無い・URL に届かない）、手元の追跡ブランチは書き換わらずに残る。2026-11-13 に一時リポジトリで実測した（取り込みは終了コード 128、`git rev-parse --verify --quiet refs/remotes/origin/main` は 0 のまま）。origin から本番チャネルを消したリポジトリでは古い追跡ブランチの宣言を読むが、`check-pr-base.sh` も同じ取り込みと読み出しを使うため、組む側と検査の判定は揃う

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `PRODUCTION_FETCH_TIMEOUT` | 定数（秒） | `plugins/ndf/scripts/lib/repo.py` | 60。プランを組む処理を、応答の無い origin で止め続けないための上限。取り込みの所要は未測定で、根拠は設計文書の「未確認のまま残ること」に置く |
| `WORKTREE_DECL` | 定数（既存） | `plugins/ndf/scripts/lib/repo.py` | `.ndf/worktree.json`。`production_decl` は `git show` のパスにこれを使い、パスを新しく書かない |
| `FLOW_SOURCE` | 定数（既存） | `plugins/ndf/scripts/supervise_lib/decl.py` | `.ndf/worktree.json の branch_flow`。`flow_snapshot` の `where` の既定値 |
| 種別リリースの判定 | 比較（`a.kind == "release"`） | `plugins/ndf/scripts/supervise_lib/decl.py` の `apply_decls` | 定数を足さない。種別の名前は `USES_PRODUCTION` と同じ `"release"` を使う |
| スナップショットの `source` | 辞書の欄（既存の形） | `branch_flow.ResolvedFlow.as_snapshot` | 形を変えない。値の文字列だけが「読んだ場所」か「既定値（理由）」になる |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並列の計画の複数のレーンが、同じメインディレクトリでまとめたリリースや `new release` を組む | `refs/remotes/origin/<本番チャネル>` | 取らない。git の参照の更新はロックファイルで原子的に行われ、同時の取り込みの一方が失敗しても `_fetch_tracking` は偽を返して手元の追跡ブランチを読む | 冪等。取り込みは origin の先頭へ合わせるだけで、何回打っても同じ状態になる。途中で止まっても追跡ブランチは前の値か新しい値のどちらか |
| リリースのプランの組み立てと、`release-steps.py` のマージのステップの取り込み（`git fetch -q origin --tags`） | 同上 | 取らない（同じ理由） | 同上 |
| テストが、このリポジトリそのものを木にして `new release` を組む（`tests/test_supervise_layout.py` の `_release_plan`） | 開発者の手元の `refs/remotes/origin/main` | 取らない。ふだんの `git fetch` と同じ書き換えで、worktreeとローカルのブランチに触れない | 冪等。ネットワークに届かなければ取り込みを飛ばして手元の値で組む |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `flow_snapshot` | 読んだ場所か、既定値へ倒した理由 | スナップショットの `source` の欄。プランの記録（`ブランチ運用`）と承認資料へそのまま載る | origin の URL（認証情報を含みうる） |
| `production_decl` | 読めない理由の 1 文 | 戻り値だけ（標準出力・標準エラーへ出さない） | `git` の標準エラーの全文（origin の URL を含みうる） |
| `_fetch_tracking` | 無し | 無し（成否だけを戻り値で返す） | `git fetch` の標準エラー |
| `apply_decls` | I3 の `DeclError` の文言（読んだ場所・直すキー・許す値） | `new release` は標準エラーと終了コード 2。まとめたリリースは呼び手（並列の計画）の結果へ | 宣言の全文 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_fetch_tracking` | `repo._remote_has_branch`（`GIT_TERMINAL_PROMPT=0` で `proc.run` を打つ形） | `grep -rn "GIT_TERMINAL_PROMPT" --include=*.py plugins/ndf/scripts`（テストを除くと `lib/repo.py:117` の 1 件） | 形を使う（同じ `env` の作り方と `OSError` の扱い）。関数は `ls-remote` を打つもので、取り込みには使えない |
| `_fetch_tracking` | `supervise_lib/design_stage.py` の `_git_retrying` | `grep -rn '"show", f"' --include=*.py plugins/ndf/scripts` | 使えない。失敗を例外で上げて再試行するもので、失敗を無視して手元の値で続ける要件（可用性）に合わない |
| `_fetch_tracking` | `scripts/check-pr-base.sh` の 39 行の取り込み | ファイルを読んだ | 同じ refspec（`refs/heads/<名前>:refs/remotes/origin/<名前>`）を使う。組む側と検査が同じ参照を読むため |
| `production_decl` | `release_lib/publish.py` の `git show <rev>:<path>`・`proc.git_out` | 上の検索 | `proc.git_out` を使う（失敗を `None` で返す）。`publish.py` の関数は CHANGELOG を読むもので使えない |
| `production_decl` | `lib/decl.py` の `load_file` | `grep -n "def load_file" plugins/ndf/scripts/lib/decl.py` | 使えない。ファイルのパスを読むもので、ref の上の中身（文字列）を読めない。JSON の読み方と理由の書き方は同じ形に揃える |
| `production_decl` | `repo.read_worktree_decl` | ファイルを読んだ | 使えない。worktreeとメインディレクトリのファイルを読むもので、origin の ref を読まない |
| `production_flow_decl` | `repo.first_git_tree`（宣言を探す木の並びから git の木を選ぶ） | `grep -n "def first_git_tree" plugins/ndf/scripts/lib/repo.py` | 使う。`apply_decls` が本番チャネルの名前を解くのと同じ木を使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_fetch_tracking` | bare の origin に本番チャネルがあり、真を返して追跡ブランチが origin の先頭になる | origin の先頭が手元の追跡ブランチと同じとき真を返し、何も変えない | origin に本番チャネルが無い・origin の URL が存在しない場所・origin が無いのそれぞれで偽を返し、手元の追跡ブランチが残る（時間切れと git が無い場合は、`proc.run` を差し替えて例外を上げさせる） |
| `production_decl` | `main` に `version == 1` の宣言があり、宣言と `origin/main の .ndf/worktree.json` を返す | origin の URL が届かない場所でも、手元の `origin/main` の宣言を返す（非機能の可用性） | 名前が空・木が無い・追跡ブランチが無い・ファイルが無い・JSON でない・最上位が配列・`version` が 2 のそれぞれで、`None` と理由を返す（受け入れ条件 4） |
| `production_flow_decl` | `main` の宣言を読み、`(宣言, 場所, None)` を返す | `a.production_branch` が `None` のとき、`({}, 場所, 理由)` を返す | 読めないとき 3 つ目に理由が入る（`production_decl` の各行） |
| `flow_snapshot` | `main` に `selected` と `reviews.release` があり `develop` が `whole` のとき、開発版の `new release` のプランのレビューの単位が `main` の値になる（決定 2） | `main` の宣言が `branch_flow` を持たない・`promotion: whole` を明示するのどちらでも、出所以外は同じスナップショットになる（受け入れ条件 1・2） | `main` の `branch_flow` が表に合わない組・知らないキーで `DeclError` になり、文言に `origin/main の .ndf/worktree.json の branch_flow` と直すキーが入る（I3） |
| `apply_decls` | 種別リリースで `develop` が `selected`、`main` が宣言なしのとき、まとめたリリースの本番のプランと `new release --channel prod` に `--promotion selected` と `release-branch` のステップが無い（受け入れ条件 1・I1） | `main` と `develop` が同じ `selected` のとき、プランが変更前と同じ（受け入れ条件 3）。`main` の宣言に別の `base_branch`・`production_branch` を書いても、起点と本番チャネルの名前はメインディレクトリの値（I5） | 種別 `sprint`・`close` では `main` の宣言を壊しても止まらず、開始時の記録で組まれる（受け入れ条件 7・I4）。種別リリースで I3 の宣言のとき、`new release` は終了コード 2、まとめたリリースは `present.json` を書かない |
