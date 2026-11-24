# #4331 の詳細設計: テストの所要を残し、main の中央値が上限を超えたら知らせる

設計文書 [issue-4331-design.md](./issue-4331-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。別ファイルにしたのは、新設する関数が 2 つ以上あり、既存の関数のシグネチャを 1 つ（`_file_of`）変えるためである。決定の番号は設計文書の「決定の記録」、受け入れ条件の番号は要求仕様（[issue-4331-requirements.md](./issue-4331-requirements.md)）の番号を指す。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
.github/workflows/pytest.yml
.ndf/project.json
docs/specifications/ndf-split-tables-toolchain-and-budget.md
plugins/ndf/tests/
├── conftest.py
└── test_ok_tools_record.py *
scripts/
├── pytest_shard.py
├── pytest-shard-seconds.json
├── ci_test_durations.py *
├── ci-test-duration.json *
└── tests/
    ├── test_pytest_shard.py *
    └── test_ci_test_durations.py *
```

依存の向き: ワークフローが 2 つのスクリプトを呼び、スクリプトは設定ファイルと実測秒の表を読む。`scripts/ci_test_durations.py` は標準ライブラリと `gh` コマンドだけに頼り、`scripts/pytest_shard.py` も NDF のコード（`plugins/ndf/src`）も import しない（決定 4）。`scripts/pytest_shard.py` は pytest のプラグインとして NDF のテストの手順に読み込まれるが、NDF の `conftest.py` とは互いに import しない。依存は一方向で、循環は無い。構成要素どうしの関係は設計文書の「構成要素」の図にあり、ここでは描き直さない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `.github/workflows/pytest.yml` | 変更 | テストのジョブが 2 種類の JUnit XML と試行の番号を上げ、中央値のジョブが `trend` を打つ | `scripts/pytest_shard.py`、`scripts/ci_test_durations.py` | ジョブ `pytest-shard`（変更）・`pytest-duration`（新設） | — |
| `scripts/pytest_shard.py` | 変更 | ファイル単位の割り当てと、NDF のテストを収集の前に外す pytest のプラグインと、実測秒の表の作り直し | `scripts/pytest-shard-seconds.json` | モジュール `pytest_shard` | `shard_env`・`_test_files`・`pytest_configure`・`pytest_configure_node`・`pytest_ignore_collect`（新設）、`_file_of`・`_seconds`（変更） |
| `scripts/pytest-shard-seconds.json` | 変更 | 根と NDF のテストのファイルごとの実測秒 | — | データ | — |
| `scripts/ci_test_durations.py` | 新設 | 1 回の所要を出す `run` と、この回の集計を書いて直近の中央値を上限と比べる `trend` | `scripts/ci-test-duration.json`、`gh` | モジュール `ci_test_durations`（クラス `Gh`・`Part`・`Match`・`Search`） | `read_config`・`shard_jobs`・`parse_junit`・`read_parts`・`match_attempts`・`current_total`・`past_totals`・`verdict`・`render_trend`・`fetch_run`・`render_run`・`cmd_run`・`cmd_trend`・`main`、`Gh.get`・`Gh.raw` |
| `scripts/ci-test-duration.json` | 新設 | 中央値に数える回数・上限の秒・遅い順に出す件数 | — | データ | — |
| `scripts/tests/test_pytest_shard.py` | 新設 | 割り当てのプラグインと `seconds` の範囲テスト | `scripts/pytest_shard.py` | テスト | — |
| `scripts/tests/test_ci_test_durations.py` | 新設 | `run` と `trend` の範囲テスト（`gh` の応答を差し替える） | `scripts/ci_test_durations.py` | テスト | — |
| `plugins/ndf/tests/conftest.py` | 変更 | `NDF_OK_TOOLS_OUT` を渡された実行は記録を書き出すだけにする | — | モジュール `conftest` | `pytest_sessionfinish`（変更） |
| `plugins/ndf/tests/test_ok_tools_record.py` | 新設 | ok の出力の記録の範囲テスト | `plugins/ndf/tests/conftest.py` | テスト | — |
| `.ndf/project.json` | 変更 | `budget` から `tests` のキーを外す（決定 5） | — | データ | — |
| `docs/specifications/ndf-split-tables-toolchain-and-budget.md` | 変更 | このリポジトリはテストの量を実行時間で見ること、件数の上限は NDF の機能として残ることを書く（決定 5） | — | 文書 | — |

## テストのジョブ

満たす決定: 決定 1、決定 6、決定 8、受け入れ条件 1、受け入れ条件 9、受け入れ条件 10、受け入れ条件 11

ジョブ `pytest-shard` の手順を次のように並べ直す。ワークフローは関数を持たないので、手順ごとの条件と結果を判定表で書く。

### 処理の手順

| 手順 | 条件（`if`） | 結果 |
| --- | --- | --- |
| 試行の番号（新設） | 常に走る | ワークスペースの根に `shard-attempt-${{ matrix.shard }}.txt` を書く。中身は `${{ github.run_attempt }}` の 1 行である |
| 根のテスト（名前 `根のテスト` を付ける） | 今と同じ | コマンドは今と同じ。`run` が手順の秒を名前で引くため、名前を付ける |
| `pytest-junit-<番号>` を上げる | `always()`（今と同じ） | `path` に `pytest-junit-<番号>.xml` と `shard-attempt-<番号>.txt` の 2 行を書く。`overwrite: true` を足す。`if-no-files-found: warn` は今のまま |
| NDF のテストのファイルを決める | — | 手順ごと消す（受け入れ条件 9） |
| NDF のテスト（名前は今のまま `NDF のテスト`） | `!cancelled()`（今と同じ） | `uv run --frozen --project . pytest -q -n auto -p pytest_shard --junitxml=../../pytest-ndf-junit-${{ matrix.shard }}.xml` を打つ。環境変数は `SHARD_TOTAL: 6`・`SHARD_INDEX: ${{ matrix.shard }}`・`NDF_OK_TOOLS_OUT`（今と同じ）・`PYTHONPATH: ${{ github.workspace }}/scripts` |
| `pytest-ndf-junit-<番号>` を上げる（新設） | `!cancelled()` | `path` に `pytest-ndf-junit-<番号>.xml` と `shard-attempt-<番号>.txt` の 2 行を書く。`overwrite: true`、`if-no-files-found: warn` |
| `ndf-ok-tools-<番号>` を上げる | 今と同じ | `overwrite: true` を足し、`retention-days` を 1 から 30 へ変える |

- NDF の JUnit XML をワークスペースの根に書くのは、成果物の中のファイルの配置を根の成果物とそろえるためである。`upload-artifact` は、`path` に並べたファイルの最も近い共通の親を成果物の根にする。NDF の XML を `plugins/ndf` の下に置くと、成果物の中が `plugins/ndf/pytest-ndf-junit-<番号>.xml` になる
- `-p pytest_shard` は、xdist の各 worker にも同じ引数で渡る。`PYTHONPATH` は環境変数なので worker のプロセスも受け継ぐ
- NDF のテストを流すと、JUnit XML の `classname` は `plugins/ndf` を起点にした名前（`tests.test_x.TestY`）になる。`--import-mode=importlib`（`plugins/ndf/pyproject.toml` の `addopts`）でも、`classname` は nodeid から作られるので同じ形である

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 成果物の名前 `pytest-junit-<番号>`・`pytest-ndf-junit-<番号>` | 設定値 | `.github/workflows/pytest.yml` | — |
| 試行の番号のファイルの名前 `shard-attempt-<番号>.txt` | 設定値 | `.github/workflows/pytest.yml` | — |
| 手順の名前 `根のテスト`・`NDF のテスト` | 設定値 | `.github/workflows/pytest.yml` | — |

成果物の名前の頭・試行の番号のファイルの名前・手順の名前は、読む側の `scripts/ci_test_durations.py` と `scripts/pytest_shard.py` も定数で持つ（それぞれの「内部のデータと定数」）。ワークフローの YAML からスクリプトへ値を渡す経路が無いため、2 か所に置く。食い違うと、`run` が「XML が 1 つも読めない」で 1 を返し、`trend` が無効な集計を書くので、実装の Pull Request の CI の `run` で気づける。

## 割り当て

満たす決定: 決定 6、決定 7、決定 9、受け入れ条件 8、受け入れ条件 10

`scripts/pytest_shard.py` に、NDF のテストを収集の前に外す pytest のフックを足し、`seconds` が NDF の XML も読むようにする。根のテストの割り当て（根の `conftest.py` の `_select_shard`）と `files` のサブコマンドは変えない。`assign_files` も変えない。表に秒の無いファイルを、渡したファイルのうち秒のあるものの中央値で数える今の振る舞いが、NDF のファイルだけを渡せば設計文書の「最初の割り当て」のとおりになるためである。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `shard_env` | `scripts/pytest_shard.py` | 新設 | `shard_env(environ: Mapping[str, str]) -> tuple[int, int] \| None` | `SHARD_TOTAL`・`SHARD_INDEX` から `(index, total)` を返す。`SHARD_TOTAL` が無ければ `None` |
| `_test_files` | `scripts/pytest_shard.py` | 新設 | `_test_files(config: pytest.Config) -> list[str]` | `testpaths` の下で `python_files` に当たるファイルを、リポジトリのルートから見たパスで並べる |
| `pytest_configure` | `scripts/pytest_shard.py` | 新設 | `pytest_configure(config: pytest.Config) -> None` | 割り当て（ファイルから担当のジョブへの対応）を 1 回だけ決めるか、worker では controller から受け取り、`config.stash` に保存する |
| `pytest_configure_node` | `scripts/pytest_shard.py` | 新設 | `pytest_configure_node(node) -> None` | controller が、保存した対応を worker の `node.workerinput` へ入れる |
| `pytest_ignore_collect` | `scripts/pytest_shard.py` | 新設 | `pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool \| None` | テストのファイルの担当がこのジョブでなければ `True` を返して収集から外す |
| `_file_of` | `scripts/pytest_shard.py` | 変更 | `_file_of(classname: str, base: Path) -> str \| None`（引数 `base` を足す） | `classname` を `base` の下に実在するファイルへ戻し、リポジトリのルートから見たパスを返す |
| `_seconds` | `scripts/pytest_shard.py` | 変更 | `_seconds(a: argparse.Namespace) -> int`（変えない） | 成果物の名前で根と NDF の XML を見分け、両方からファイルごとの秒の表を作り直す |

- `shard_env`
  - 事前条件: 無い
  - 事後条件: 返すときは `0 <= index < total` である。どちらかが整数でないか、`index >= total` なら `pytest.UsageError` を上げる（メッセージは根の `conftest.py` の `_shard` と同じ）
- `_test_files`
  - 事前条件: `config.rootpath` がリポジトリの中にある
  - 事後条件: 返すパスは重複が無く、辞書順に並ぶ。`norecursedirs` に当たるディレクトリの下のファイルは含まない。`python_files` との照合はファイル名だけを `fnmatch` で比べる（NDF の設定は既定の `test_*.py`・`*_test.py` で、区切りを含むパターンを使わない）
  - 副作用: ファイルシステムを走査する
- `pytest_configure`
  - 事前条件: `-p pytest_shard` で読み込まれている
  - 事後条件: `shard_env` が `None` なら何も保存しない。そうでなければ `config.stash[OWNER]` に `dict[str, int]`（リポジトリのルートから見たパスから担当のジョブの番号）と、`config.stash[INDEX]` にこのジョブの番号がある。`config` に `workerinput` があるプロセス（xdist の worker）は、`config.workerinput[OWNER_KEY]` をそのまま保存し、`_test_files` と `read_seconds` を呼ばない。ほかのプロセス（controller と、`-n` を付けない実行）は、`assign_files(_test_files(config), total, read_seconds())` の結果を保存する
  - 副作用: controller では実測秒の表を読む
- `pytest_configure_node`
  - `@pytest.hookimpl(optionalhook=True)` を付ける。xdist が無い環境で、pytest が未知のフックとして読み込みを止めないためである
  - 事後条件: `config.stash` に対応があれば、`node.workerinput[OWNER_KEY]` に同じ対応がある。無ければ何も入れない
- `pytest_ignore_collect`
  - 事後条件: 対応が無い実行・ディレクトリ・`python_files` に当たらないファイルでは `None` を返す。`False` は返さない（`False` はほかのフックの判断を打ち消して収集を強いる）。テストのファイルでは、`owner.get(<リポジトリのルートから見たパス>, 0)` がこのジョブの番号と違えば `True`、同じなら `None` を返す
  - リポジトリの外のパスは、絶対パスを表のキーにして同じ規則で引く（キーに当たらないのでジョブ 0 が持つ）
- `_file_of`
  - 事後条件: `base` の下の `/`.join(先頭からの部分) + `.py` を長い方から探し、最初に実在したファイルを `ROOT` から見たパスで返す。見つからなければ `None`
- `_seconds`
  - 事後条件: `<JUnit XML の置き場>` の下のすべての `*.xml` を読む。置き場から見た XML のパスに `pytest-ndf-junit-` を含むものは `base` を `ROOT / "plugins" / "ndf"` に、ほかは `ROOT` にして `_file_of` を呼ぶ。表を書くのは、すべての XML が読めて、項目が 1 つ以上あったときだけである
  - 副作用: `scripts/pytest-shard-seconds.json` を書き直す。`summary` の文を「根の conftest.py と NDF のテストの手順のプラグインがジョブの割り当てに使う」に変える

モジュールの説明（docstring）の「NDF のテストは `--seconds` を渡さず、ファイルの項目数で分ける」の段落は、プラグインの使い方（`-p pytest_shard` と `PYTHONPATH`）に書き換える。`files` のサブコマンドは残す（受け入れ条件 10 の確かめが使う）。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `shard_env` | 値が整数でない・番号が範囲の外 | ワークフローか手元の環境変数の誤り | `shard_env` | 止める（`pytest.UsageError`） | pytest がメッセージを出し、終了コード 4 | 後始末は無い。値を直して再実行できる |
| `pytest_configure` | 実測秒の表が読めない | 表が壊れている・無い | `read_seconds`（既存） | 既定値で続ける（空の表。どのファイルも 1 秒で数え、ファイルの数で分ける） | 出力は無い。偏りは `run` のジョブごとの所要で見える | 表を作り直して再実行できる |
| `pytest_configure` | worker の `workerinput` に対応が無い | controller が `pytest_configure_node` を呼ばなかった（xdist の版の違いなど） | `pytest_configure`（worker） | 止める（`pytest.UsageError`） | 「controller から割り当てを受け取れなかった」を出し、終了コード 4 | 割り当てを worker で計算し直すと、ジョブの中で 2 通りの割り当てが混ざりうるため、続けない |
| `_seconds` | XML が読めない | 壊れた XML・取得の途中の ZIP | `_seconds` の `ET.parse` | 止める | 読めなかった XML のパスを標準エラーへ書き、終了コード 1。表は書かない | 表は元のまま残る。成果物を取り直して再実行できる |
| `_seconds` | 項目が 1 つも無い | 置き場の誤り | `_seconds`（今と同じ） | 止める | 今と同じメッセージで終了コード 1 | 今と同じ |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `OWNER`・`INDEX` | 定数（`pytest.StashKey`） | `scripts/pytest_shard.py` | — |
| `OWNER_KEY` | 定数（`workerinput` のキー） | `scripts/pytest_shard.py` | `"pytest_shard_owner"` |
| `NDF_JUNIT` | 定数（NDF の XML を見分ける名前の頭） | `scripts/pytest_shard.py` | `"pytest-ndf-junit-"` |
| `NDF_ROOT` | 定数 | `scripts/pytest_shard.py` | `ROOT / "plugins" / "ndf"` |
| `SHARD_TOTAL_ENV`・`SHARD_INDEX_ENV` | 定数 | `scripts/pytest_shard.py` | `"SHARD_TOTAL"`・`"SHARD_INDEX"` |

`SHARD_TOTAL_ENV`・`SHARD_INDEX_ENV` と範囲の判定は、根の `conftest.py` の `_shard` も持つ。根の `conftest.py` は設計の結果の触るファイルに入っていないので、この変更では `_shard` を `shard_env` へ寄せない。

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 6 つのテストのジョブ | 実測秒の表（読むだけ） | 要らない。どのジョブも同じ表と同じファイルの一覧から同じ割り当てを決める | 同じ入力なら何度打っても同じ割り当てになる |
| 1 つのジョブの xdist の controller と worker | 割り当ての対応 | controller だけが計算し、`workerinput` で写しを渡す | worker は計算しないので、worker の間で割り当てが食い違わない |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `shard_env` | 根の `conftest.py` の `_shard` | `rg -n "SHARD_TOTAL" --type py` | 使えない。`conftest.py` は pytest が読み込むファイルで、NDF のテストの手順から import すると根の前提（`pytest_plugins` など）まで読み込む |
| `_test_files` | pytest の `Session.perform_collect`・`_pytest.pathlib.fnmatch_ex` | pytest 9.2.0 のソースを `rg -n "def fnmatch_ex\|def perform_collect"` で確かめた | 使えない。`perform_collect` は収集そのもので、前に持ち分を決める目的に合わない。`fnmatch_ex` は非公開の関数である |
| `pytest_configure`・`pytest_configure_node` | pytest-xdist 3.8.0 の `workerinput` と `pytest_configure_node` | xdist の `newhooks.py` と `workermanage.py` を `rg -n "workerinput\|configure_node"` で確かめた | 使う。worker へ値を渡す公開の手段である |
| `pytest_ignore_collect` | NDF の `conftest.py` の `pytest_ignore_collect` | `rg -n "def pytest_ignore_collect" plugins/ndf/tests` | 並べて置く。あちらは起点の違う実行を外すだけで、持ち分を見ない。どちらも `None` か `True` しか返さないので、順序によらず結果が同じになる |

### 単体テストの観点

`scripts/tests/test_pytest_shard.py` に置く。`config` は `getini`・`rootpath`・`stash` を持つ偽物を渡し、子プロセスの pytest は起動しない。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `shard_env` | `SHARD_TOTAL=6`・`SHARD_INDEX=2` で `(2, 6)` | `SHARD_TOTAL` が無いと `None`。`SHARD_INDEX=5` は通る | `SHARD_INDEX=6`・`"x"` で `UsageError` |
| `_test_files` | `tests/a/test_x.py` と `tests/test_y.py` を、リポジトリのルートから見たパスで返す | `support/helper.py`（`python_files` に当たらない）を含まない | `norecursedirs` に当たる `tests/.cache/test_z.py` を含まない |
| `pytest_configure` | 表に重いファイルを置くと、そのファイルが空いているジョブへ入る（受け入れ条件 8） | `workerinput` に対応を持つ `config` では、`_test_files` と `read_seconds` が呼ばれた数が 0 | `workerinput` に対応が無い worker で `UsageError` |
| `pytest_configure_node` | 保存した対応が `node.workerinput` に入る | 対応が無い実行では何も入れない | — |
| `pytest_ignore_collect` | 6 つのジョブの持ち分の和が一覧と一致し、重ならない（受け入れ条件 10） | 一覧に無いファイルはジョブ 0 だけが持つ。サブディレクトリの中のファイルをジョブ 3 に割り当てると、ジョブ 3 ではディレクトリもファイルも外れない | ディレクトリでは常に `None`。対応が無い実行では常に `None` |
| `_file_of` | `tests.test_example.TestX` を `base=NDF_ROOT` で `plugins/ndf/tests/test_example.py` に戻す | 先頭の部分だけが実在するとき、長い方から探して当たったものを返す | どこにも無ければ `None` |
| `_seconds` | 根と NDF の XML を並べた置き場から、`plugins/ndf/tests/...` と `scripts/tests/...` の両方のキーを持つ表を作る | — | 壊れた XML で 1 を返し、表を書き直さない |

`files --total 2 --index <番号>` に `tests/a.py` の 2 件と `tests/b.py`・`tests/c.py` の 1 件ずつを渡すと、ジョブ 0 が `tests/a.py`、ジョブ 1 が `tests/b.py` と `tests/c.py` を出すことも、このファイルで縛る（受け入れ条件 10。今の出力が変わらないこと）。

## 実測秒の表

満たす決定: 決定 7、決定 9

`scripts/pytest-shard-seconds.json` の形（`summary` と `seconds`）は変えない。キーに `plugins/ndf/tests/...` が加わる。中身は、実装の Pull Request の CI の成果物を `gh run download <run の ID> -p 'pytest-*junit-*' -D <保存先>` で取り、`python3 scripts/pytest_shard.py seconds <保存先>` で作り直した値にする。使う回の条件は設計文書の処理の流れの「実測秒の作り直し」のとおりである。関数は持たない。

## 所要の読み取り

満たす決定: 決定 1、決定 2、決定 3、決定 4、決定 10、受け入れ条件 2、受け入れ条件 3、受け入れ条件 4、受け入れ条件 5、受け入れ条件 6

`scripts/ci_test_durations.py` を新設する。Python の標準ライブラリだけを使い、実行機の `python3` で動く（決定 4）。外から呼ばれる形（引数・出力・終了コード）は設計文書の「入出力のコントラクト」が持つ。ここはその内部の関数だけを書く。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Gh.get` | `scripts/ci_test_durations.py` | 新設 | `get(self, path: str, kind: str) -> Any` | `gh api <path>` を 1 回打ち、JSON を返す。呼び出しの数を `kind` ごとに数える |
| `Gh.raw` | `scripts/ci_test_durations.py` | 新設 | `raw(self, path: str, kind: str) -> bytes` | `gh api <path>` を 1 回打ち、応答をそのまま返す（成果物の ZIP）。数え方は `get` と同じ |
| `read_config` | `scripts/ci_test_durations.py` | 新設 | `read_config(path: Path = CONFIG) -> dict[str, int \| float]` | 所要の設定を読み、3 つのキーの型を確かめる |
| `shard_jobs` | `scripts/ci_test_durations.py` | 新設 | `shard_jobs(jobs: list[dict]) -> tuple[int, dict[int, dict]]` | ジョブの一覧から名前が `pytest (<番号>/<総数>)` のジョブを拾い、総数と番号ごとのジョブを返す |
| `parse_junit` | `scripts/ci_test_durations.py` | 新設 | `parse_junit(data: bytes) -> list[tuple[str, float]]` | JUnit XML の `testcase` ごとに、テストの id（`classname::name`）と `time` を返す |
| `read_parts` | `scripts/ci_test_durations.py` | 新設 | `read_parts(xml_dir: Path) -> dict[tuple[str, int], Part]` | 成果物を取った先から、`(根か NDF, ジョブの番号)` ごとに XML の項目と試行の番号を読む |
| `match_attempts` | `scripts/ci_test_durations.py` | 新設 | `match_attempts(parts: dict[tuple[str, int], Part], total: int, jobs: dict[int, dict]) -> Match` | ジョブの番号ごとに、2 つの成果物がそろい、試行の番号が一覧の `run_attempt` と同じかを照合する |
| `current_total` | `scripts/ci_test_durations.py` | 新設 | `current_total(xml_dir: Path, gh: Gh, run_id: int, attempt: int) -> tuple[dict, list[str]]` | この回の集計の JSON と、読めなかったものの説明を返す |
| `past_totals` | `scripts/ci_test_durations.py` | 新設 | `past_totals(gh: Gh, run_number: int, need: int, now: datetime) -> Search` | 実行の一覧と成果物の一覧から過去の回の有効な集計を、実行番号の大きい順に `need` 回まで集める |
| `verdict` | `scripts/ci_test_durations.py` | 新設 | `verdict(totals: list[float], cfg: dict) -> dict` | 合計の中央値・上限・差と、超えたかを返す |
| `render_trend` | `scripts/ci_test_durations.py` | 新設 | `render_trend(current: dict, unreadable: list[str], search: Search \| None, judged: dict \| None, calls: Counter[str]) -> tuple[str, list[str], str]` | 標準出力の本文・注記の行・ジョブの要約の Markdown を作る |
| `fetch_run` | `scripts/ci_test_durations.py` | 新設 | `fetch_run(gh: Gh, run_id: int, dest: Path) -> list[dict]` | 1 回の実行の `pytest-*junit-*` の成果物を `dest` の下へ展開し、最新の試行のジョブの一覧を返す |
| `render_run` | `scripts/ci_test_durations.py` | 新設 | `render_run(parts: dict[tuple[str, int], Part], match: Match, jobs: dict[int, dict], top: int) -> str` | `run` の出力（先頭の行・合計・遅い順の上位・ジョブごとの所要）を作る |
| `cmd_run` | `scripts/ci_test_durations.py` | 新設 | `cmd_run(args: argparse.Namespace, gh: Gh) -> int` | サブコマンド `run` を通す |
| `cmd_trend` | `scripts/ci_test_durations.py` | 新設 | `cmd_trend(args: argparse.Namespace, gh: Gh) -> int` | サブコマンド `trend` を通す |
| `main` | `scripts/ci_test_durations.py` | 新設 | `main(argv: list[str] \| None = None, gh: Gh \| None = None) -> int` | 引数を読み、サブコマンドへ振り分ける。テストは `gh` に偽物を渡す |

- `Gh.get`・`Gh.raw`
  - 事前条件: `gh` が認証済み（CI では `GH_TOKEN`）。リポジトリは作業ディレクトリの `origin`（`gh` の既定）で、パスの `{owner}/{repo}` は `gh` が置き換える
  - 事後条件: `self.calls[kind]` が 1 増えている。`kind` は `jobs`・`pages`・`zips`・`artifacts` のどれかで、`trend` の最後の行に出すのは前の 3 つである
  - 副作用: 外部コマンド `gh api --method GET <path>` を `CALL_TIMEOUT` 秒の上限で打つ（ネットワーク）
- `read_parts`
  - 事前条件: `xml_dir` の下が、成果物の名前ごとのディレクトリ（`actions/download-artifact` を `merge-multiple` なしで使ったときと、`fetch_run` の展開の形）である
  - 事後条件: ディレクトリの名前が `pytest-junit-<番号>` なら根、`pytest-ndf-junit-<番号>` なら NDF の部品になる。各部品は、中の `*.xml` の項目（読めなければ `error` に理由）と、`shard-attempt-<番号>.txt` の整数（無いか読めなければ `None`）を持つ
  - 副作用: ファイルを読む
- `match_attempts`
  - 事後条件: `Match` は、ジョブの番号 0〜`total - 1` のそれぞれを、`missing`（根か NDF の部品が無いか読めない）・`stale`（どちらかの部品の試行の番号が一覧の `run_attempt` と違う）・`accepted`（照合が通った）のどれか 1 つに入れる。`failed` は `accepted` のうち一覧の `conclusion` が `success` でない番号である。`shard_attempts` は長さ `total` で、`accepted` の番号には試行の番号を、ほかには `None` を置く
- `current_total`
  - 事後条件: 返す集計は設計文書の「集計の JSON」の形どおりで、`run` と `attempt` は引数の値である。`valid` が真になるのは、ジョブの一覧が取れ、`total` が 1 以上で、`missing` と `stale` と `failed` がどれも空のときだけである。真なら `root_seconds`・`ndf_seconds`・`total_seconds` に小数第 1 位で丸めた秒を、偽なら 3 つとも `None` と `reason` を入れる
  - 副作用: ジョブの一覧を 1 回引く（`kind="jobs"`、`actions/runs/<run の ID>/jobs?filter=latest&per_page=100`）
- `past_totals`
  - 事前条件: `need >= 1`
  - 事後条件: `Search.totals` は数えた過去の回の `(run の ID, 合計の秒)` を実行番号の大きい順に持ち、長さは `need` 以下である。`Search.stop` は止めた理由（`enough`・`zip_limit`・`page_limit`・`retention`・`exhausted`・`list_error`）、`Search.unreadable` は数えなかった回の ID と理由を持つ
  - 副作用: 実行の一覧・成果物の一覧のページ（`kind="pages"`）と集計の ZIP（`kind="zips"`）を引く
- `fetch_run`
  - 事後条件: `dest/<成果物の名前>/` の下に、期限切れでない `pytest-junit-*`・`pytest-ndf-junit-*` の成果物の ZIP を展開してある
  - 副作用: 成果物の一覧（`actions/runs/<run の ID>/artifacts?per_page=100`）・ジョブの一覧・成果物の ZIP を引く。`dest` にファイルを書く（呼ぶ側の `cmd_run` が一時ディレクトリを渡し、終わりに消す）
- `cmd_trend`
  - 事後条件: `--out` に集計の JSON がある（書けなかったときを除く）。`GITHUB_STEP_SUMMARY` があれば要約が書き足してある。終了コードは、引数の誤りと設定の誤りのときだけ 2 で、ほかは 0 である
  - 副作用: `--out` と `GITHUB_STEP_SUMMARY` のファイルを書く。標準出力へ注記の行を書く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Gh.get`・`Gh.raw` | `gh` が無い | 実行機か手元に `gh` が入っていない | `subprocess.run` の `FileNotFoundError` | 呼び出し元へ返す（`GhUnavailable`） | 呼び出し元の行のとおり | 後始末は無い |
| `Gh.get`・`Gh.raw` | 認証できない・API の呼び出しの失敗・`CALL_TIMEOUT` を過ぎた | トークン切れ・権限不足・GitHub の障害 | 終了コードが 0 でない・`TimeoutExpired` | 呼び出し元へ返す（`GhError`。標準エラーの先頭の 1 行を持つ）。再試行しない | 呼び出し元の行のとおり | 再実行できる |
| `Gh.get` | 応答が JSON でない | API の応答の変化 | `json.loads` | 呼び出し元へ返す（`GhError`） | 呼び出し元の行のとおり | 再実行できる |
| `read_config` | 設定が無い・JSON でない・キーが無い・型が違う（`runs`・`top` が 1 以上の整数でない、`limit_seconds` が正の数でない） | 設定ファイルの誤り | `read_config` | 止める（`ConfigError`） | 標準エラーへファイルのパスと誤りを書き、`run`・`trend` とも 2 | 設定を直して再実行できる |
| `read_parts` | XML が壊れている | 取得の途中で切れた・書き出しの途中で落ちた | `parse_junit` の `ET.ParseError` | 既定値で続ける（その部品の `error` に理由を入れ、`match_attempts` が `missing` に数える） | 呼び出し元の行のとおり | — |
| `current_total` | ジョブの一覧が取れない | `GhError`・`GhUnavailable` | `current_total` | 既定値で続ける（無効な集計。`reason` は「ジョブの一覧を取れない」） | `::warning title=テストの所要の読み取り::` の行。終了コード 0 | 再実行で作り直せる |
| `current_total` | 組がそろわない | 一部のジョブの成果物が無い・古い試行・落ちたジョブ・壊れた XML | `match_attempts` | 既定値で続ける（無効な集計。`reason` に欠けた番号・古い試行の番号・読めない XML を並べる） | `::warning title=テストの所要の読み取り::` に番号を書く。終了コード 0 | 再実行で作り直せる |
| `past_totals` | 実行の一覧・成果物の一覧のページが取れない | `GhError`・`GhUnavailable` | `past_totals` | 止める（`stop="list_error"`。判定しない） | `::warning title=テストの所要の読み取り::` に取れなかった一覧を書く。この回の集計は書く。終了コード 0 | 次の `main` の実行で読み直される |
| `past_totals` | 過去のある回の ZIP が取れない・ZIP に JSON が無い・JSON が壊れている・キーが足りない・`run` が実行の ID と違う | 成果物の破損・別の回の成果物 | `past_totals` | 既定値で続ける（その回を数えず次の回へ進み、`unreadable` に ID を足す） | 数えなかった回の ID を `::warning title=テストの所要の読み取り::` に並べる。終了コード 0 | — |
| `cmd_trend` | `--out` を書けない | 書き先のディレクトリが無い・権限 | `cmd_trend` | 既定値で続ける（判定と出力は続ける） | 標準エラーと `::warning title=テストの所要の読み取り::` に書く。終了コード 0。上げる手順が `if-no-files-found: warn` で知らせる | 再実行できる |
| `cmd_run` | `gh` を使えない・run の ID の実行が無い・成果物の一覧が取れない | `GhUnavailable`・`GhError` | `fetch_run` | 止める | 標準エラーへ理由を書き、2 | 一時ディレクトリを消す。再実行できる |
| `cmd_run` | 根か NDF の XML が 1 つも読めない | 残す前の回・保存期間を過ぎた回 | `cmd_run`（`read_parts` の結果） | 止める | 標準エラーへ「根の JUnit XML が無い」か「NDF の JUnit XML が無い」（両方なら 2 行）を書き、1 | 一時ディレクトリを消す |
| `cmd_run` | 一部の成果物の ZIP が取れない | `GhError` | `fetch_run` | 既定値で続ける（その成果物を無いとして扱い、部分集計にする） | 先頭の行の「部分集計（欠けたジョブ: <番号>…）」。終了コード 0 | — |
| `main` | 引数の誤り | 利用者の打ち間違い | `argparse` | 止める | `argparse` の使い方の表示と 2 | — |

`cmd_trend` の中で上の表に無い例外が出たときは、捕まえずに 1 で終わる。中央値のジョブは `continue-on-error: true` を持つので、ワークフローの結論には入らない（決定 3）。

### 処理の手順

`current_total` の `valid` の判定:

| ジョブの一覧 | 総数 `total` | 組（`missing`・`stale`・`failed`） | 結果 |
| --- | --- | --- | --- |
| 取れない | 一覧から読めないので、成果物のディレクトリの番号の最大に 1 を足した数にする | — | 無効。`shard_attempts` はその長さで、すべて `None` |
| 取れた | 0（`pytest (<番号>/<総数>)` のジョブが無い） | — | 無効。`reason` は「テストのジョブが一覧に無い」。`shard_attempts` は上の行と同じ求め方の長さで、すべて `None` |
| 取れた | 1 以上 | どれも空 | 有効 |
| 取れた | 1 以上 | どれかが空でない | 無効。`reason` に空でないものを並べる |

`past_totals` が候補の実行ごとに行うこと（上から順に当て、当たったところで決める）:

| 条件 | 結果 |
| --- | --- |
| `totals` が `need` 回になった（`cmd_trend` は `need` に `runs − 1` を渡すので、この回を含めて `runs` 回そろった） | `stop="enough"` で終わる |
| 候補の実行の `created_at` が `now − RETENTION_DAYS` 日より前 | `stop="retention"` で終わる |
| 実行の一覧の候補が尽き、次のページが要るが、ページの取得が `PAGE_LIMIT` に達した | `stop="page_limit"` で終わる |
| 実行の一覧の最後のページを読み、候補が尽きた | `stop="exhausted"` で終わる |
| 成果物の一覧に `workflow_run.id` がこの実行の ID で `expired` が偽の成果物が無く、読んだ成果物の最も古い `created_at` がこの実行の `created_at` より新しく、次のページがある | 成果物の一覧の次のページを読み、同じ候補で判断し直す。ページの取得が `PAGE_LIMIT` に達していれば `stop="page_limit"` で終わる |
| 上の行に当たらず、成果物が見つからない | この実行を数えず次の候補へ進む（ZIP を取らない） |
| 成果物が見つかり、ZIP の取得が `2 × (runs − 1)` 回に達している | `stop="zip_limit"` で終わる |
| ZIP を取り、`run` が実行の ID と同じで、`valid` が真で、`attempt` が実行の `run_attempt` と同じ | `totals` に `(ID, total_seconds)` を足す |
| ZIP を取り、`valid` が偽か、`attempt` が `run_attempt` と違う | 数えず次の候補へ進む。`unreadable` には足さない |
| ZIP が取れない・JSON が読めない・キーが足りない・`run` が違う | 数えず、`unreadable` に ID と理由を足して次の候補へ進む |

`cmd_trend` の流れと、判定するかの規則:

| この回の集計 | `past_totals` の結果 | 判定 | 注記の行 |
| --- | --- | --- | --- |
| 無効 | 呼ばない（API の呼び出しを増やさない） | しない | `::warning title=テストの所要の読み取り::`（この回の理由） |
| 有効 | `totals` が `runs − 1` 回（中央値に数える値が `runs` 回） | `verdict` で判定する | 超えたら `::warning title=テストの所要::`。上限以下なら出さない |
| 有効 | `totals` が `runs − 1` 回に満たない | しない | `::notice title=テストの所要::`（そろった回数と止めた理由） |
| どちらでも | `unreadable` が空でない・`stop="list_error"` | 上の行のとおり | 上の行に加えて `::warning title=テストの所要の読み取り::` |

- 繰り返しの終わる条件: `past_totals` は、ページの取得が 2 つの一覧の合計で `PAGE_LIMIT`（10）回、ZIP の取得が `2 × (runs − 1)` 回で必ず止まる。どちらも取得の前に数を確かめる
- 計算量の注意: 実行の一覧は `created_at` の新しい順に返る。実行番号は作られた順に振られるので、ページをまたいでも実行番号の大きい順と食い違わない。ページの中だけは `run_number` の大きい順に並べ直す。成果物の一覧は読んだページを `workflow_run.id` から成果物への辞書に足していき、候補ごとに一覧を読み直さない
- `verdict` の中央値は `statistics.median` で求める。偶数回なら中央の 2 つの平均になる。`over` は `median > limit_seconds` で、等しいときは超えない（受け入れ条件 5）。差は `median − limit_seconds` で、超えたときに正になる

`render_run` の出力の並び（`cmd_run` が標準出力へ書く）:

1. `match.missing` か `match.stale` が空でなければ「部分集計（欠けたジョブ: <番号>、古い試行のジョブ: <番号>）」。空のほうの項目は書かない
2. `match.failed` が空でなければ「失敗したジョブを含む（<番号>）」
3. 根・NDF のそれぞれの合計の秒と件数、両方の合計（`accepted` の部品だけを足す）
4. 遅い順の上位 `top` 件（秒・根か NDF か・テストの id）。同じ秒はテストの id の辞書順
5. ジョブごとの所要の表（番号・結果 `conclusion`・ジョブ全体・`根のテスト` の手順・`NDF のテスト` の手順の秒）と、最も長いジョブ。手順が見つからないか時刻が無いセルは `-` にする

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Part` | 型（`dataclass`。`kind`・`index`・`cases`・`attempt`・`error`） | `scripts/ci_test_durations.py` | — |
| `Match` | 型（`dataclass`。`total`・`accepted`・`missing`・`stale`・`failed`・`shard_attempts`） | `scripts/ci_test_durations.py` | — |
| `Search` | 型（`dataclass`。`totals`・`stop`・`unreadable`） | `scripts/ci_test_durations.py` | — |
| `CONFIG` | 定数 | `scripts/ci_test_durations.py` | `ROOT / "scripts" / "ci-test-duration.json"` |
| `ROOT_ARTIFACT`・`NDF_ARTIFACT` | 定数（成果物の名前の頭） | `scripts/ci_test_durations.py` | `"pytest-junit-"`・`"pytest-ndf-junit-"` |
| `TOTAL_ARTIFACT` | 定数 | `scripts/ci_test_durations.py` | `"ci-test-duration-total"` |
| `ATTEMPT_FILE` | 定数 | `scripts/ci_test_durations.py` | `"shard-attempt-{index}.txt"` |
| `SHARD_JOB` | 定数（正規表現） | `scripts/ci_test_durations.py` | `r"^pytest \((\d+)/(\d+)\)$"` |
| `ROOT_STEP`・`NDF_STEP` | 定数（手順の名前） | `scripts/ci_test_durations.py` | `"根のテスト"`・`"NDF のテスト"` |
| `WORKFLOW` | 定数 | `scripts/ci_test_durations.py` | `"pytest.yml"` |
| `PER_PAGE` | 定数 | `scripts/ci_test_durations.py` | `100` |
| `PAGE_LIMIT` | 定数 | `scripts/ci_test_durations.py` | `10` |
| `RETENTION_DAYS` | 定数（リポジトリの成果物の保存期間） | `scripts/ci_test_durations.py` | `90` |
| `CALL_TIMEOUT` | 定数（`gh` の 1 回の上限の秒） | `scripts/ci_test_durations.py` | `30` |
| `runs`・`limit_seconds`・`top` | 設定値 | `scripts/ci-test-duration.json`（所要の設定） | 設計文書の「所要の設定」の形 |

- テストのジョブの数（6）は定数に置かない。`shard_jobs` がジョブの名前の `<総数>` から読む。ワークフローの `matrix` と 2 か所に持たないためである
- `RETENTION_DAYS` は、リポジトリの設定（`actions/permissions/artifact-and-log-retention` の `days`）の写しである。その API は管理者の権限を要し、中央値のジョブのトークン（`actions: read`）では読めないため、定数で持つ。設定を変えたら、ここも変える
- ZIP の取得の上限 `2 × (runs − 1)` は、設定の `runs` から求め、定数に置かない

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並んで走る `main` への push の実行の中央値のジョブ | 成果物 `ci-test-duration-total`（実行ごとに別の成果物） | 要らない。書くのは自分の実行の成果物だけで、ほかの実行の成果物は読むだけである | 後から始まった実行が先に終わっても、候補は実行番号で並べるので数える回は変わらない。まだ集計を上げていない実行は「成果物が見つからない」で飛ばされ、数えないほうへ倒れる |
| 同じ実行の再実行（失敗したジョブだけの再実行） | 同じ名前の成果物 | `overwrite: true` で最新の試行の成果物に置き換える | `trend` は同じ入力から同じ集計を書く。前の試行の集計が残っても、読む側が `attempt` と `run_attempt` の照合で外す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `render_trend` | 数えた回の ID と合計の秒、中央値、上限、差、止めた理由、最後の行に API の呼び出しの数（`jobs`・`pages`・`zips`） | 標準出力 | `GH_TOKEN` の値、`gh` の標準エラーの 2 行目以降 |
| `render_trend` | 設計文書の「知らせの出し分け」の表のとおりの注記の行 | 標準出力（workflow commands） | 同上 |
| `render_trend` | 同じ内容の Markdown の表 | `GITHUB_STEP_SUMMARY` のファイル（環境変数があるときだけ） | 同上 |
| `Gh.get`・`Gh.raw` | 失敗した API のパスと `gh` の標準エラーの先頭の 1 行 | 呼び出し元が注記か標準エラーへ書く | 同上 |

注記の行の本文に入れる値（回の ID・秒・理由）は、改行と `%` を workflow commands の決まり（`%25`・`%0A`）で置き換えてから書く。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `Gh.get`・`Gh.raw` | NDF の `measure_ci.py` の `Gh`、`scripts/check-required-checks.py` の `_gh_get`、`scripts/ci-heavy-skip.py` の `gh_api` | `rg -n "gh\", \"api\|def gh_api\|class Gh" scripts plugins/ndf/src` | 使えない。`Gh` は NDF のコードで、import しない（決定 4）。2 つのスクリプトの関数はスクリプトの中の非公開の関数で、`scripts/lib` に `gh` の共通の部品は無い（`ls scripts/lib`） |
| `parse_junit` | NDF の `measure_ci.py` の `junit_seconds`、`scripts/pytest_shard.py` の `_seconds` | `rg -n "testcase" scripts plugins/ndf/src` | 使えない。`junit_seconds` は合計だけを返し、テストごとの秒を返さない。`_seconds` はファイルごとに足し込むので、遅い順の上位に要るテストの id を持たない |
| `verdict` | `statistics.median` | 標準ライブラリ | 使う |
| `read_parts`・`fetch_run` | `zipfile`・`tempfile`・`xml.etree.ElementTree` | 標準ライブラリ | 使う |
| `read_config`・`shard_jobs`・`match_attempts`・`current_total`・`past_totals`・`render_trend`・`render_run`・`cmd_run`・`cmd_trend`・`main` | 同じ役割の関数 | `rg -n "run_attempt\|filter=latest\|GITHUB_STEP_SUMMARY" scripts plugins/ndf/src` | 無い。`GITHUB_STEP_SUMMARY` を書く部品も、試行の照合も、このリポジトリに無い |

### 単体テストの観点

`scripts/tests/test_ci_test_durations.py` に置く。`Gh` の代わりに、パスから応答（JSON・ZIP のバイト列・例外）を引く偽物を `main(argv, gh=...)` へ渡す。成果物は `tmp_path` に XML と試行の番号のファイルを書いて作る。実際の待ちと子プロセスは使わない。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `read_config` | 3 つのキーを読む | `limit_seconds` に整数を書いても数として読む | キーが無い・`runs` が 0・JSON でないと `ConfigError` |
| `shard_jobs` | `pytest (0/6)`〜`pytest (5/6)` から総数 6 と 6 つのジョブを返す | `pytest (所要)` と `pytest (NDF の ok の出力)` を拾わない | 該当が無いと総数 0 |
| `parse_junit` | `classname::name` と `time` の組を返す | `time` が無い項目は 0 秒 | 壊れた XML で `ET.ParseError` |
| `read_parts` | 根と NDF のディレクトリを見分け、試行の番号を読む | `shard-attempt-<番号>.txt` が無いと `attempt` が `None` | 壊れた XML の部品は `error` を持つ |
| `match_attempts` | 6 つとも試行が一致し成功なら `accepted` が 6 つ | 試行の番号が 1 つだけ違うジョブは `stale` に入る。`conclusion` が `failure` のジョブは `accepted` と `failed` の両方に入る | 根だけあって NDF が無いジョブは `missing` |
| `current_total` | 組がそろうと `valid` が真で、根・NDF・合計の秒を持つ | `shard_attempts` が番号 0〜5 の順に並ぶ | 試行の番号が違う・ジョブの一覧が取れない・XML が壊れている、のどれでも `valid` が偽、秒が `None`、`reason` を持ち、`--out` のファイルは書かれる |
| `past_totals` | 1 ページ目で 9 回がそろうと、ページ 2 回・ZIP 9 回で `enough` | 18 回目の ZIP で 9 回がそろうと `enough`。そろわなければ `zip_limit`。候補が 1 ページ目で足りないと 2 ページ目を読む。実行番号がこの回以上の実行は数えない。成果物の作成時刻が逆になっても実行番号の順で数える | 壊れた 1 回を飛ばして次の回を数える。`valid` が偽の集計と、`attempt` が `run_attempt` と違う集計を数えない。`run` が違う集計を数えず `unreadable` に足す。一覧が取れないと `list_error` |
| `verdict` | 中央値が上限を超えると `over` が真で、差が正 | 中央値が上限と等しいと `over` が偽 | — |
| `render_trend` | 超えたとき `::warning title=テストの所要::` に中央値・上限・超えた秒が入る | 上限以下では `::warning` の行が無い。回数が足りないと `::notice title=テストの所要::` が 1 行だけ出る | 読めないものがあると `::warning title=テストの所要の読み取り::` に回の ID が入る |
| `cmd_trend` | 超えたときも 0 を返す。`GITHUB_STEP_SUMMARY` に表を書く | 設定の `limit_seconds` だけを変えると判定が変わる | `gh` が無いときも 0 を返し、この回の集計（無効）を書く |
| `fetch_run`・`cmd_run`・`render_run` | 根と NDF の両方の合計・遅い順の上位・ジョブごとの手順の秒を出す | 一部のジョブの成果物が無い・古い試行のジョブがあると、先頭に部分集計の行が出て 0 を返す。落ちたジョブの最新の XML は合計に入り、先頭に「失敗したジョブを含む」が出る | 根か NDF の XML が 1 つも無いと 1 を返し、無い側を標準エラーへ書く。期限切れの成果物は取りに行かない。`gh` が無いと 2 |
| `main` | サブコマンドへ振り分ける | — | 引数が足りないと 2 |

## 所要の設定

満たす決定: 決定 2、決定 4、非機能の「運用・保守性」

`scripts/ci-test-duration.json` を新設する。形は設計文書の「所要の設定」の表のとおりで、`runs` は 10、`top` は 20 にする。`limit_seconds` は、実装の Pull Request の CI で `run` を打って測った合計の 1.25 倍を 10 秒単位で切り上げた値にする（設計文書の「未確認のまま残ること」）。ほかのキーを足さない。読むのは `read_config` だけである。

## 中央値のジョブ

満たす決定: 決定 3、決定 10、受け入れ条件 4、受け入れ条件 6

ジョブ `pytest-duration`（名前 `pytest (所要)`）を `pytest.yml` に足す。集約のジョブ `pytest` の `needs` には入れない。ワークフローのジョブの数は 39 から 40 になる。

### 処理の手順

| 手順 | 条件（`if`） | 結果 |
| --- | --- | --- |
| ジョブ全体 | `!cancelled() && github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.pytest-shard.result == 'success' && needs.pytest-ndf-ok-tools.result == 'success'` | `needs: [pytest-shard, pytest-ndf-ok-tools]`・`continue-on-error: true`・`permissions: { actions: read, contents: read }`・`runs-on: ubuntu-latest` |
| checkout | ジョブの条件のとおり | `actions/checkout`（ほかのジョブと同じ固定の版） |
| この回の XML を取る | ジョブの条件のとおり | `actions/download-artifact`（同じ固定の版）で `pattern: pytest-*junit-*`・`path: ${{ runner.temp }}/junit`。`merge-multiple` は付けない（成果物の名前ごとのディレクトリを `read_parts` が読む） |
| 中央値 | `!cancelled()`（取得が落ちても無効な集計を書く） | `python3 scripts/ci_test_durations.py trend "$RUNNER_TEMP/junit" --run ${{ github.run_id }} --run-number ${{ github.run_number }} --attempt ${{ github.run_attempt }} --out "$RUNNER_TEMP/ci-test-duration-total.json"`。環境変数 `GH_TOKEN: ${{ github.token }}` |
| 集計を上げる | `always()` | `actions/upload-artifact` で `name: ci-test-duration-total`・`path: ${{ runner.temp }}/ci-test-duration-total.json`・`overwrite: true`・`if-no-files-found: warn` |

- setup-python も setup-uv も置かない。実行機の `python3` で `trend` を打つ（決定 4、非機能の「ワークフロー全体」）
- 実行の一覧と成果物の一覧の API は、ワークフローの `permissions` の `actions: read` で読める。ジョブの `permissions` は、ワークフローの上位の指定を置き換える

### 並行と排他

同時に走りうる処理と排他の取り方は、「所要の読み取り」の「並行と排他」の表のとおりである。

## ok の出力の記録

満たす決定: 決定 8、受け入れ条件 11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `pytest_sessionfinish` | `plugins/ndf/tests/conftest.py` | 変更 | `pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None`（変えない） | worker は記録を controller へ渡す。controller か並列でない実行は、`NDF_OK_TOOLS_OUT` があれば記録を書いて終わり、無ければ全体の実行のときだけ ok の無いサブコマンドで落とす |

- `pytest_sessionfinish`
  - 事後条件: `NDF_OK_TOOLS_OUT` が空でない実行では、そのファイルに検証済みのサブコマンドの名前が 1 行ずつ辞書順に並び、`session.exitstatus` は変わらない。空か無い実行の振る舞いは今と同じである
  - 副作用: `NDF_OK_TOOLS_OUT` のファイルを書く（今と同じ）

モジュールの説明（docstring）の「各ジョブは環境変数 `NDF_OK_TOOLS_OUT` のファイルへ記録を書き、後のジョブ `ndf-next-ok-tools` が合わせて確かめる」を、「`NDF_OK_TOOLS_OUT` を渡された実行は記録を書くだけで、自分では確かめない。合わせて確かめるのは後のジョブ `pytest (NDF の ok の出力)` である」に書き換える。`is_full_run` は変えない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pytest_sessionfinish` | 記録のファイルを書けない | 書き先のディレクトリが無い・権限 | `Path.write_text` の `OSError`（今と同じく捕まえない） | 止める | pytest が内部エラーを出し、0 以外で終わる。後の `upload-artifact` が `if-no-files-found: error` で落ちる | 再実行できる |

### 単体テストの観点

`plugins/ndf/tests/test_ok_tools_record.py` に置く。`session` と `config` は偽物を渡し、`_ok_tools` と `cli.COMMANDS` を `monkeypatch` で差し替えて `pytest_sessionfinish` を直接呼ぶ。子プロセスの pytest は起動しない。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `pytest_sessionfinish` | `NDF_OK_TOOLS_OUT` を渡した全体の実行で、検証済みでないサブコマンドがあっても `exitstatus` が 0 のままで、ファイルに名前が並ぶ | `NDF_OK_TOOLS_OUT` が空の文字列なら、渡さないときと同じに扱う | `NDF_OK_TOOLS_OUT` を渡さない全体の実行で、検証済みでないサブコマンドがあれば `exitstatus` が 1 になる |
