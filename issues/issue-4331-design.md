# テストの所要: CI のテスト 1 件ごとの秒を残し、main の中央値が上限を超えたら知らせる（#4331）

テストの量の目安が件数の上限だけで、機能を足すたびに上限を上げ直し、遅いテストも見つけられない。NDF のテストにも JUnit XML を書かせて 1 件ごとの秒を CI の成果物に残し、`main` の直近 10 回の中央値が上限を超えたら `main` の実行に警告を出す（1 回の所要はこのリポジトリのスクリプトで読む）。あわせて NDF のテストを実測の秒でジョブへ分けて 6 つのジョブの収集だけの手順をなくし、このリポジトリの設定からテストの件数の上限を外す。比べた案は決定の記録に、調べた製品とライブラリは末尾の外部の調査にある。

省いた文書: 実行計画（課題 1・PR 1）

## 今の姿

何が起きたか・目的・受け入れ条件は要求仕様（[issue-4331-requirements.md](./issue-4331-requirements.md)、正は [#4331](https://github.com/devbasex/ai-plugins/issues/4331) の本文）にある。この文書は「どう解くか」と「なぜその解き方か」だけを書く。

変わるのは、ワークフロー `Python tests` の 2 か所である（定義は [.github/workflows/pytest.yml](../.github/workflows/pytest.yml) ）。

| 変わる所 | 図での位置 |
| --- | --- |
| 6 つのジョブの中の NDF のテストの手順 | 「（変わる）」を付けた 2 つの手順 |
| その後ろに足すジョブ | 図に無い（解き方の全体像の図にある） |

```mermaid
graph TD
    S[pytest-scope<br/>テストを省くかの判定] --> J[pytest 0/6 〜 5/6]
    subgraph J[6 つのジョブ pytest-shard]
        R[根のテスト<br/>JUnit XML を残す] --> C[NDF のテストのファイルを決める（変わる）<br/>collect-only を各ジョブで打つ]
        C --> N[NDF のテスト（変わる）<br/>JUnit XML を書かない]
    end
    J --> O[pytest NDF の ok の出力<br/>6 ジョブの記録を合わせる]
    O --> A[pytest<br/>必須のチェックの集約]
```

この文書では、テストを 2 つに分けて呼ぶ。パスは、断りが無ければリポジトリのルートから見たパスである。

| 呼び方 | 指すもの |
| --- | --- |
| 根のテスト | リポジトリのルートで `pytest .` を打って流すテスト（ルートの `conftest.py` と `scripts/tests` などのテスト）。`plugins/ndf` の下の NDF のテストは集めない |
| NDF のテスト | `plugins/ndf` を起点（pytest の `rootdir`）にして流す、`plugins/ndf/tests` の下のテスト |

ok の出力の確かめは、NDF の各サブコマンドについて、正常終了（結果 JSON の状態が `ok`）の出力を検証したテストが少なくとも 1 件あることを確かめる仕組みである。検証とは、結果 JSON が結果のコントラクトとサブコマンドの結果のスキーマに合うことを、テストの間に確かめることを指す。1 件も無いサブコマンドがあれば、その名前を出して失敗にする。CI では、各ジョブが検証済みのサブコマンドの名前を記録する。後のジョブ `pytest (NDF の ok の出力)` が 6 ジョブの記録を合わせ、NDF の全サブコマンド（`cli.COMMANDS`）を網羅していなければ失敗にする。このジョブが失敗すると、必須のチェック `pytest` も失敗する。

設計に要る今の構造は次のとおりである。

| 対象 | 今の形 | 保存先 |
| --- | --- | --- |
| ジョブへの割り当て | 重いファイルから順に、いちばん空いているジョブへ入れる（`assign_files`）。根のテストは、ルートの `conftest.py` が全件を集めた後に持ち分でない項目を外す | [scripts/pytest_shard.py](../scripts/pytest_shard.py)、[conftest.py](../conftest.py) |
| ファイルごとの実測秒 | リポジトリのルートから見たパスをキーにした秒。根のテストの JUnit XML から `seconds` で作り直す | [scripts/pytest-shard-seconds.json](../scripts/pytest-shard-seconds.json) |
| NDF のテストの割り当て | `--collect-only` の出力のテストの数で分け、ファイルの一覧を引数で渡す。JUnit XML の `classname` は `plugins/ndf` から見た名前で、実測秒のキーと合わない | `pytest.yml` の「NDF のテストのファイルを決める」 |
| ok の出力の確かめ | NDF の `conftest.py` は、全体の実行（引数が `plugins/ndf` か `testpaths` のディレクトリだけで、絞り込みが無い）のときだけ自分で確かめる。CI はファイルを引数で渡すので全体の実行にならない。そのため各ジョブは記録を `NDF_OK_TOOLS_OUT` のファイルへ書き、後のジョブが合わせる | [plugins/ndf/tests/conftest.py](../plugins/ndf/tests/conftest.py) |
| 件数の上限 | 規則 `budget` は、設定 `budget` にキーがある項目だけ上限と比べる。件数はキーの有無にかかわらず結果 JSON の `metrics.budget` に出る | [plugins/ndf/src/ndf/checks/budget.py](../plugins/ndf/src/ndf/checks/budget.py)、`.ndf/project.json` の `budget.tests`（6,000） |

2026-11-25（JST）の `main` の実行（run 45495601478）で、ジョブ `pytest (0/6)` の手順の所要は次のとおりだった。値は `gh api repos/devbasex/ai-plugins/actions/runs/45495601478/jobs` の手順の時刻の差である。

| 手順 | 秒 |
| --- | ---: |
| NDF のテストのファイルを決める | 18 |
| NDF のテスト | 54 |
| 根のテスト | 14 |

成果物は 12 個で、根のテストの `pytest-junit-0`〜`5` と ok の記録の `ndf-ok-tools-0`〜`5` である。NDF のテストの JUnit XML は無い。

## 文脈と制約

- テストの所要の秒で Pull Request を落とさない。実行機の速さで秒が変わり、コードと関係なく落ちるためである。根拠は [#3423](https://github.com/devbasex/ai-plugins/issues/3423) の方針と要求の S1 である
- 分ける単位はファイルのままにする。同じファイルのテストを別のジョブへ割ると、ファイル単位の前提が割れる。前提とはモジュールの読み込みと一時ディレクトリである（ルートの `conftest.py` の説明）
- どのファイルもちょうど 1 つのジョブで流れる（受け入れ条件 10）。6 つのジョブがそれぞれ同じ入力から同じ割り当てを決めることで成り立たせる
- ok の出力の確かめの形を変えない。6 つのジョブが検証済みのサブコマンドの名前を記録し、後のジョブ `pytest (NDF の ok の出力)` が合わせて、全サブコマンドを網羅しているかで合否を決める（受け入れ条件 11）
- ワークフローのジョブの数は 39 で、設定の上限 `budget.ci_jobs` は 40 である（`.github/workflows/*.yml` の `jobs` のキーを数えた値）。足せるジョブは 1 つだけである
- 成果物の保存期間は、リポジトリの設定で 90 日である。値は `gh api repos/devbasex/ai-plugins/actions/permissions/artifact-and-log-retention` の `days` で見た。過ぎた回は読めなくてよい（要求の前提 2）
- 中央値の上限は設定ファイルの 1 か所に置き、上限を変えるのにコードの変更を要らないようにする（要求の非機能の条件）

## 解き方の全体像

```mermaid
graph TD
    subgraph J[6 つのジョブ pytest-shard]
        R[根のテスト<br/>pytest-junit-i] --> N[NDF のテスト<br/>pytest-ndf-junit-i]
    end
    J --> O[pytest NDF の ok の出力]
    J --> D[pytest 所要<br/>main の push のときだけ]
    D --> W[警告の注記と<br/>ジョブの要約]
    U[利用者] --> T[scripts/ci_test_durations.py run]
    T -->|成果物とジョブの時刻を読む| J
```

- 6 つのジョブは、根のテストと NDF のテストの両方で JUnit XML を書き、成果物 `pytest-junit-<番号>` と `pytest-ndf-junit-<番号>` に残す
- NDF のテストは、ファイルごとの実測秒からジョブへ分ける。分けるのは収集の前に持ち分を決める pytest のプラグイン（`scripts/pytest_shard.py` を `-p` で読む）で、収集だけの手順はなくなる
- 利用者は `python3 scripts/ci_test_durations.py run <run の ID>` で 1 回の所要を読む。根と NDF の両方について、合計・遅い順の上位・ジョブごとの所要が出る
- `main` への push の実行では、足したジョブ `pytest (所要)` が直近 10 回の中央値を上限（`scripts/ci-test-duration.json`）と比べる。超えたら、超えた秒を警告の注記とジョブの要約に書く。どの場合も終了コードは 0 である
- このリポジトリの `.ndf/project.json` から `budget.tests` を外す。規則 `budget` のテストの件数の上限は、NDF の機能としては残る

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | CI の 1 回ごとに、根と NDF のテスト 1 件ごとの秒を成果物に残す | CI（`main` と Pull Request の両方） |
| F2 | 1 回の実行を指して、所要の合計・遅い順の上位・ジョブごとの所要を出す | このリポジトリの開発者と AI |
| F3 | `main` の直近の中央値が上限を超えたことと、超えた秒を知らせる | `main` の CI と、その結果を見る開発者 |
| F4 | NDF のテストを、ファイルごとの実測秒でジョブへ分ける | CI |

## 構成要素

| 要素 | 責務 | 保存先 |
| --- | --- | --- |
| テストのジョブ | 根と NDF のテストを流し、それぞれの JUnit XML を成果物に上げる | `.github/workflows/pytest.yml` のジョブ `pytest-shard` |
| 割り当て | ファイルの一覧と実測秒から各ファイルの持ち分のジョブを決める。NDF のテストでは pytest のプラグインとして、持ち分でないファイルを収集の前に外す。JUnit XML から実測秒を作り直す | `scripts/pytest_shard.py` |
| 実測秒の表 | ファイルごとの秒。キーはリポジトリのルートから見たパスで、根と NDF のテストの両方を持つ | `scripts/pytest-shard-seconds.json` |
| 所要の読み取り | 成果物の JUnit XML とジョブの時刻を GitHub から読み、1 回の所要（`run`）を出す。`main` の 1 回の合計を集計の JSON に書き、過去の回の集計と合わせて直近の中央値（`trend`）を出す | `scripts/ci_test_durations.py`（新設） |
| 所要の設定 | 中央値に数える回数と上限の秒、遅い順に出す件数 | `scripts/ci-test-duration.json`（新設） |
| 中央値のジョブ | `main` への push の実行で、この回の JUnit XML を取って `trend` を打ち、結果を注記と要約に書く。この回の集計を成果物 `pytest-duration-total` に上げる。失敗してもワークフローの結論に入らない | `.github/workflows/pytest.yml` のジョブ `pytest-duration`（新設、名前は `pytest (所要)`） |
| ok の出力の記録 | `NDF_OK_TOOLS_OUT` を渡された実行は、記録を書き出すだけにして、自分では確かめない | `plugins/ndf/tests/conftest.py` |

```mermaid
graph TD
    subgraph CI[pytest.yml]
        J[テストのジョブ] --> D[中央値のジョブ]
    end
    subgraph S[scripts]
        P[割り当て] --> E[実測秒の表]
        T[所要の読み取り] --> F[所要の設定]
    end
    J --> P
    D --> T
    J --> K[ok の出力の記録]
```

## 処理の流れ

`main` への push の 1 回で、所要が残り、中央値が比べられるまでの順序である。

```mermaid
sequenceDiagram
    participant J as テストのジョブ（6 つ）
    participant G as GitHub の成果物
    participant D as 中央値のジョブ
    J->>J: 根のテスト（JUnit XML）
    J->>J: NDF のテスト（割り当てのフックが持ち分を決め、ok の出力の記録と JUnit XML を書く）
    J->>G: pytest-junit-i と pytest-ndf-junit-i を上げる
    Note over D: pytest (NDF の ok の出力) の成功の後に走る
    D->>G: この回の pytest-*junit-* を取る（actions/download-artifact）
    D->>D: trend がこの回の合計を求め、集計の JSON に書く
    D->>G: 過去の回の pytest-duration-total を一覧で 1 回引き、新しい順に取る
    D->>D: この回と過去の回を合わせて設定 `runs` の回数だけ取り、合計の中央値を上限と比べる
    D-->>D: 超えたら警告の注記と要約、終了コード 0
    D->>G: この回の集計を pytest-duration-total に上げる
```

中央値のジョブは、テストと ok の出力の確かめがどちらも成功した `main` への push の回だけを数え、その回の合計を集計として残す。過去の回は XML を読み直さず、残した集計だけを読む。

- **起動の条件**: `needs` に `pytest-shard` と `pytest-ndf-ok-tools` を置き、`github.event_name == 'push'`・`github.ref == 'refs/heads/main'`・両方の `result == 'success'` のときだけ走る。ok の出力の確かめが失敗した回は、必須のチェック `pytest` も失敗する回なので数えない（要求の前提 1）
- **ワークフローの結論に入れない**: ジョブに `continue-on-error: true` を付け、集約のジョブ `pytest` の `needs` にも入れない。checkout・uv の準備・成果物の取得が失敗しても、`main` のワークフローは失敗にならない（決定 3）
- **この回の合計**: この回は実行の途中なので、成果物 `pytest-*junit-*` を同じ回の中で `actions/download-artifact` で取る（STD3）。`trend` は、根と NDF の XML がテストのジョブの数（6）だけそろい、すべて読めたときだけ、合計の秒を集計の JSON（`run`・根の秒・NDF の秒・合計の秒）に書く。ジョブはそのファイルを成果物 `pytest-duration-total` に上げる。書かれなかった回は上げない（`if-no-files-found: ignore`）
- **過去の回**: `gh api 'repos/{owner}/{repo}/actions/artifacts?name=pytest-duration-total&per_page=100'` を 1 回打ち、成果物の一覧を得る。`expired` が真のもの、この回のもの、同じ回の 2 つ目以降（ジョブの再実行で増えたもの）を除き、新しい順に 1 つずつ ZIP を取る。この成果物は上の起動の条件を満たした回にしか無いので、失敗した回・Pull Request の回・NDF の JUnit XML を残す前の回・保存期間を過ぎた回は、一覧の時点で数えない（要求の前提 1・2）
- **数える回数**: この回を含めて設定 `runs`（10）回そろったところで取得をやめる。一覧を最後まで見てもそろわないときは、何回そろったかを書き、超えたかの判定をしない

1 回の `main` への push で増える GitHub API の呼び出しは、成果物の一覧の 1 回と、過去の回の集計の ZIP（数百バイト）の取得が多くて 9 回である。過去の回の XML を毎回読み直す方式では、10 回分の成果物の一覧と、多くて 108 個（9 回 × 12 個）の ZIP の取得が要る。1 回の XML の成果物は、根と NDF の 6 個ずつで 12 個である。この回の 12 個の取得は、どちらの方式でも要る。

`trend` が読めないものに当たったときは、次のように扱う。どの場合も理由を `::warning` の行で出し、終了コードは 0 である。

| 読めないもの | 扱い |
| --- | --- |
| この回の XML（そろわない・壊れていて読めない） | 集計の JSON を書かず、判定もしない |
| 成果物の一覧（`gh` を使えない・API の呼び出しが失敗した） | 判定しない。この回の集計の JSON は書く |
| 過去のある回の集計（ZIP の取得の失敗・JSON が壊れている・キーが足りない） | その回を数えず、次に古い回を取る。数えなかった回の ID を警告に並べる |

実測秒の作り直し（要求の E7）は、今と同じく手で打つ。

1. `gh run download <run の ID> -p 'pytest-*junit-*' -D <保存先>` で成果物を取る
2. `python3 scripts/pytest_shard.py seconds <保存先>` が、根と NDF の両方の XML から表を作り直す

成果物の名前に `ndf` を含む XML では、`classname` が `plugins/ndf` から見たパスになっている。`seconds` はこれをリポジトリのルートから見たパスへ戻し、表のキーをそろえる。

## 入出力のコントラクト

足すのはこのリポジトリのスクリプトで、NDF のコマンドと結果 JSON は変わらない。

| 項目 | `run` | `trend` |
| --- | --- | --- |
| 名前 | `python3 scripts/ci_test_durations.py run <run の ID> [--top <件数>]` | `python3 scripts/ci_test_durations.py trend <XML のディレクトリ> --run <run の ID> --out <集計の JSON>` |
| 入力 | run の ID（必須）。`--top` は遅い順に出す件数で、省けば設定の `top` | `<XML のディレクトリ>` は、この回の成果物 `pytest-*junit-*` を取った先。`--run` はこの回の ID で、集計の JSON に書き、過去の回の一覧から除く。`--out` はこの回の集計を書くファイル。リポジトリは `gh` の既定（作業ディレクトリの `origin`） |
| 出力（成功） | 標準出力へ、根と NDF のそれぞれの合計の秒と件数、両方の合計、遅い順の上位（秒・根と NDF のどちらのテストか・テストの id）、ジョブごとの所要（ジョブ全体・根のテストの手順・NDF のテストの手順の秒）、最も長いジョブ | `--out` へこの回の集計の JSON。標準出力へ、数えた回の ID と合計の秒、中央値、上限、差。`GITHUB_STEP_SUMMARY` があれば同じ内容を Markdown の表で書き足す。超えたときは `::warning title=テストの所要::` の行を出す |
| 失敗の形 | 根か NDF の JUnit XML が 1 つも読めない（残す前の回・保存期間を過ぎた回）ときは、どちらが無いかを標準エラーへ書いて 1 で終わる。一部のジョブの XML だけが無いときは、出力に欠けたジョブを書いて 0 で終わる。引数の誤りと `gh` を使えないときは 2 | 引数の誤りは 2。読めないものに当たったときは、処理の流れの表のとおりに扱い、0 で終わる。超えたときも 0 |
| 互換性 | 新設 | 新設 |

所要の設定（`scripts/ci-test-duration.json`）の形:

| キー | 型 | 意味 |
| --- | --- | --- |
| `runs` | 整数 | 中央値に数える回数（10） |
| `limit_seconds` | 数 | 中央値の上限の秒（テスト 1 件ごとの秒の、根と NDF の合計） |
| `top` | 整数 | `run` が遅い順に出す件数（20） |

## 非機能の実現方式

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 性能・拡張性 | 所要を残す仕組みを足した後も、`Python tests` の 6 ジョブの所要の合計が変更前より増えない（受け入れ条件 9 の前と後の秒で示す） | 6 ジョブがそれぞれ打っていた収集だけの手順をなくす。持ち分でないファイルは収集の前に外すので、NDF のテストの手順が集めるのは持ち分のファイルだけで今と変わらない。足すのは JUnit XML の書き出しと成果物 1 つの上げだけで、中央値のジョブは 6 ジョブの外で `main` への push のときだけ走る | 実装の Pull Request の CI の実行を指して `run` を打ち、変更前の run 45280461372 と同じ形（6 ジョブの合計と最も長いジョブ）で Pull Request に書く |
| 運用・保守性 | 中央値の上限は設定ファイルの 1 か所に置き、上限を変えるのにコードの変更が要らない | 上限と回数は `scripts/ci-test-duration.json` だけに置き、スクリプトはそこから読む | 設定の `limit_seconds` だけを変えて `trend` を打ち、判定が変わることを範囲テストで見る |

## 決定の記録

各決定の「根拠」の行の「Value <番号>」は、このリポジトリのプロジェクト MVV（判断の基準を書いた文書。バージョン 11、[.ndf/mvv.md](../.ndf/mvv.md)）の Value の番号と、その文の引用である。

### 決定 1: 実行機と分け方が変わっても値が跳ねないよう、主に見る量をテスト 1 件ごとの秒の合計にする

- **質問**: 中央値と上限で見る量を、テスト 1 件ごとの秒の合計・最も長いジョブの秒・ワークフローの所要のどれにするか
- **選んだ案**: 根と NDF の JUnit XML の `testcase` の `time` の合計にする。最も長いジョブの秒とジョブごとの所要は、`run` の出力に並べて読めるようにする
- 採らなかった案: 最も長いジョブの秒（分け方・準備の手順・実行機の割り当てで変わり、分け方を変えるたびに値が不連続に変わる。テストを足したことと区別できない）
- 採らなかった案: ワークフローの所要（キューの待ちと起動の時間を含み、2026-11-24 の直近 60 回で 36〜583 秒とぶれた）
- **代償**: 利用者が待つ時間（最も長いジョブ）は上限の対象にならない。分け方の偏りは `run` の出力で読むことになる
- 根拠: Value 2「CI の時間」・Value 13「効果は実測（費用・秒・件数）で示され」（MVV バージョン 11）。STD1

### 決定 2: ぶれで知らせが出ないよう、main の push の直近 10 回の中央値で比べ、10 回そろうまでは判定しない

- **質問**: 中央値に数える回数と、回数が足りないときの扱いをどうするか
- **選んだ案**: 成功した `main` への push の回のうち、両方の JUnit XML がそろった直近 10 回の中央値で比べる。そろった回が 10 回に満たなければ、そろった回数を書いて判定しない。回数は設定の `runs` に置く
- 採らなかった案: 1 回の秒で比べる（実行機の速さで秒が変わり、コードと関係なく知らせが出る。#3423 の方針）
- 採らなかった案: そろった回が少なくても、ある回だけで中央値を出す（NDF の JUnit XML を残し始めた直後は 1〜2 回で判定することになり、1 回の秒で比べるのと変わらない）
- **代償**: 変更をマージしてから 10 回の `main` の実行が終わるまで、超えたかの判定が出ない
- 根拠: Value 3「同じ手順は毎回同じ結果になる」（MVV バージョン 11）。回数は NDF の設定の解析にそろえる。そちらも実行の記録の中央値を直近 10 件で求めている（`plugins/ndf/src/ndf/domains/project/measure_ci.py` の `RECENT`）

### 決定 3: Pull Request を落とさず課題の一覧も増やさないよう、超えたことは main の実行の警告の注記とジョブの要約で知らせる

- **質問**: 中央値が上限を超えたことを、いつ誰が求め、どこへ知らせるか
- **選んだ案**: `main` への push の実行に足したジョブ `pytest (所要)` が求める。中央値・上限・超えた秒を、`::warning` の注記とジョブの要約（`GITHUB_STEP_SUMMARY`）へ書く。終了コードは常に 0 で、`main` の実行も失敗にしない。スクリプトの前の準備（checkout・uv の準備・成果物の取得）の失敗もワークフローの結論に入れないよう、ジョブに `continue-on-error: true` を付け、集約のジョブ `pytest` の `needs` にも入れない
- 採らなかった案: 超えたら Issue を自動で起票する（上限を超えている間は `main` の実行ごとに起票か更新が要り、課題の一覧が作業の残りで膨らむ）
- 採らなかった案: Pull Request へコメントする（1 回の秒が Pull Request の判断に入り、要求の S1 と受け入れ条件 6 に反する）
- 採らなかった案: 定期の別のワークフローで求める（`budget.ci_jobs` の残りの 1 つを使うのは同じで、マージから知らせまでに定期の間隔の遅れが入る）
- **代償**: 知らせは `main` の実行の画面と注記を見たときにしか目に入らない。人が気付くかは見る習慣に頼る
- 根拠: Value 6「利用者の Issue の一覧は、作業の残りや思いつきで膨らまない」・Value 5（MVV バージョン 11）。STD2

### 決定 4: このリポジトリの CI の形を NDF へ持ち込まないよう、所要の読み取りと上限をこのリポジトリのスクリプトと設定ファイルに置く

- **質問**: 所要を読むコマンドと中央値の上限を、NDF（コマンドと `.ndf/project.json`）に置くか、このリポジトリのスクリプトと設定ファイルに置くか
- **選んだ案**: `scripts/ci_test_durations.py` と `scripts/ci-test-duration.json` を新設する。GitHub からは `gh api` で読み、XML は標準ライブラリで読む。NDF のコードは import しない
- 採らなかった案: NDF のサブコマンドにし、上限を `.ndf/project.json` に置く（6 つのジョブ・根と NDF の 2 つのテストの組・成果物の名前は、このリポジトリの CI の形である。ほかのリポジトリで同じ形を前提にできない。使うのがまだこのリポジトリだけなのに、NDF の設定とスキーマへこの形を足すことになる）
- 採らなかった案: 結果を公開するアクション（LIB2）を足す（1 回の結果を Pull Request のチェックとして出すもので、直近の複数回の中央値と上限の比べ方を持たない。`checks: write` の権限も要る）
- **代償**: ほかのリポジトリで同じことをするには、NDF の機能として作り直す必要がある。`gh` で CI の記録を読むコードが、NDF の `measure_ci.py` と別にもう 1 つできる
- 作り方: 集計と判定は自前で作る。GitHub からの読み取りは GitHub CLI（LIB3）、XML の読み取りと中央値は Python の標準ライブラリ（LIB4）を使う
- 比べた候補: LIB2 / LIB3 / LIB4
- 根拠: Value 4「ai-plugins の流儀を、使う側のプロジェクトに持ち込まない」・Value 9「まだ要らない仕組みは先回りして作らず」（MVV バージョン 11）

### 決定 5: 件数の上限の上げ直しをやめるため、このリポジトリの設定から `budget.tests` を外し、NDF の規則は残す

- **質問**: テストの件数の上限 `budget.tests` を無くすか残すか
- **選んだ案**: このリポジトリの `.ndf/project.json` から `budget.tests` のキーを外す。規則 `budget` のコードと設定のスキーマは変えず、テストの件数は今と同じく `metrics.budget.tests` に出る。確定仕様（`docs/specifications/ndf-split-tables-toolchain-and-budget.md`）には次の 2 つを書く。このリポジトリはテストの量を実行時間で見ること、件数の上限は NDF の機能として残ることである
- 採らなかった案: NDF の規則からテストの件数の上限を無くす（実行時間を測る仕組みを持たないリポジトリから、量の上限を置く手段がなくなる。規則は設定にキーがあるときだけ効くので、残しても使わないリポジトリに影響しない）
- 採らなかった案: このリポジトリでも上限を残す（機能を足すたびに上限の上げ直しの作業と Pull Request が要り、遅いテストを見つける役にも立たない）
- **代償**: このリポジトリではテストの件数がいくら増えても Pull Request は落ちない。量の歯止めは決定 3 の知らせだけになる
- 根拠: Value 4「設定か引数を渡せば同じ工程が使える」・Value 10「コード・テスト・文書の量と未解決の不具合の数が測られ」（MVV バージョン 11）

### 決定 6: 収集だけの手順をなくすため、NDF のテストの持ち分を収集の前に pytest のプラグインで決める

- **質問**: NDF のテストを実測の秒でジョブへ分け、6 ジョブがそれぞれ打つ `--collect-only` の重なりをどう減らすか
- **選んだ案**: `scripts/pytest_shard.py` に pytest のフック `pytest_configure` と `pytest_ignore_collect` を置き、NDF のテストの手順で `-p pytest_shard` を付けて読み込む。
  - 読めるよう `scripts` を `PYTHONPATH` に足す。足すと `scripts/` のモジュールが NDF のテストの import と同じ名前で衝突しうるので、2026-11-25 に `scripts/` の最上位のモジュール 24 個と、NDF の環境と `tests/support` のモジュール名を比べた。重なりは 0 件だった
  - 持ち分は pytest のプロセスごとに 1 回だけ決める。`pytest_configure` で、ファイルの一覧を探し、実測秒の表を読み、`assign_files` で持ち分を決めて、このジョブの持ち分のファイルの集合を `config` に保存する。一覧は、設定の `testpaths` の下で `python_files` に当たるファイルである。pytest-xdist の各 worker も別のプロセスなので、それぞれ 1 回ずつ同じ入力から同じ持ち分を決める
  - `pytest_ignore_collect` は収集の候補のパスごとに呼ばれるので、保存した集合を引くだけにし、ファイルの探索と表の読み取りをしない。持ち分でないファイルは収集の前に外れる
  - 一覧に無いファイルを pytest が訪れたときは、どのジョブでも同じになる規則（ジョブ 0 の持ち分）で決める
  - 環境変数は根のテストと同じ `SHARD_TOTAL`・`SHARD_INDEX` を使う
- 採らなかった案: pytest-split（LIB1）を使う（テスト 1 件の単位で割り、同じファイルのテストが別のジョブへ分かれる。文脈と制約のファイル単位の前提が割れる）
- 採らなかった案: 1 つのジョブで 1 回だけ収集し、結果を成果物で 6 ジョブへ配る（6 ジョブがその 1 つを待つので、最も長いジョブが延びる）
- 採らなかった案: `git ls-files` で作ったファイルの一覧を引数で渡す（一覧に無いファイルは、どのジョブにも渡らないので流れない。まだコミットしていないテストのファイルや、pytest の設定と `git ls-files` の絞り方が食い違ったファイルがこれに当たる。選んだ案では、pytest が訪れたファイルを必ずどれかのジョブが持つ。一覧に無ければジョブ 0 が持つので、流れないファイルが出ない）
- **代償**: NDF のテストの手順が `scripts/` のモジュールに頼るようになる。根のテストは今の割り当て（全件を集めた後に外す）のままで、2 つの外し方が並ぶ
- 作り方: 自前で作る
- 比べた候補: LIB1 / LIB5
- 根拠: Value 2「CI の時間」・Value 11「同じ規則・定数・処理は 1 か所にまとまり」（MVV バージョン 11）。割り当ての関数 `assign_files` は根のテストと同じものを使う

### 決定 7: 実測秒の表を 1 つに保つため、キーをリポジトリのルートから見たパスにそろえる

- **質問**: NDF のテストの実測秒を、今の表に入れるか別の表にするか
- **選んだ案**: 今の `scripts/pytest-shard-seconds.json` に入れ、キーはリポジトリのルートから見たパス（`plugins/ndf/tests/...`）にそろえる。`seconds` は成果物の名前で根と NDF の XML を見分け、NDF の `classname` を、`plugins/ndf` から見たパスからリポジトリのルートから見たパスへ戻す。フックはファイルのパスをリポジトリのルートから見たパスへ直して表を引く
- 採らなかった案: NDF のテスト用の表を別のファイルにする（同じ形の表が 2 つになり、作り直しのコマンドも 2 つ要る）
- **代償**: `seconds` が成果物の名前の付け方（名前に `ndf` を含むか）に頼る
- 根拠: Value 8「同じ事実を 2 か所に持たない」・Value 11（MVV バージョン 11）

### 決定 8: 分けて流す実行が自分で落ちないよう、`NDF_OK_TOOLS_OUT` を渡された実行は記録を書き出すだけにする

- **質問**: 引数でファイルを渡さなくなると、NDF の `conftest.py` は各ジョブを全体の実行とみなし、そのジョブの中だけで ok の出力の確かめを行う。各ジョブが流すのは持ち分のファイルだけなので、ok の出力を検証したテストがほかのジョブにしか無いサブコマンドが出る。そのサブコマンドの名前で、ジョブが失敗する。分けて流す実行をどう見分けるか
- **選んだ案**: `NDF_OK_TOOLS_OUT` を渡された実行は、検証済みのサブコマンドの名前をそのファイルへ書いて、自分では確かめない。網羅の確かめは、6 つのファイルを読む後のジョブ `pytest (NDF の ok の出力)` が今と同じく行う。記録が欠けたサブコマンドがあれば、そのジョブが失敗し、必須のチェック `pytest` も失敗する
- 採らなかった案: NDF の `conftest.py` が `SHARD_TOTAL` を読んで見分ける（このリポジトリの CI の環境変数の名前を NDF のテストへ持ち込む）
- 採らなかった案: 外したファイルがあったかで見分ける（pytest-xdist では収集が各 worker で起き、確かめを行う controller からは外したファイルが見えない）
- **代償**: `NDF_OK_TOOLS_OUT` を渡して手元で全体を流すと、ok の出力の確かめが行われない
- 根拠: Value 9「責務の分かれた設計で作られる」（MVV バージョン 11）

### 決定 9: 実測秒の作り直しは、今と同じく手で打つ

- **質問**: 実測秒の表をいつ誰が作り直すか（要求の E7）
- **選んだ案**: 偏りが目立ったときに、開発者が成果物を取って `seconds` を打ち、Pull Request で直す。最初の作り直しは実装の Pull Request の CI の成果物で行う
- 採らなかった案: `main` の CI が表を作り直して自動でコミットする（`main` への直接の push は ruleset が禁じる。Pull Request を作らせると、その Pull Request が CI のたびに増える）
- **代償**: 表が古いと割り当ての偏りが戻る。偏りは `run` の出力のジョブごとの所要で読むことになる
- 根拠: Value 6「使うほど散らからない」（MVV バージョン 11）。`scripts/pytest_shard.py` の説明（値が古くても割り当ては成り立ち、偏りが戻るだけである）

### 決定 10: 中央値のジョブが同じ回の XML を毎回読み直さないよう、各回の合計を集計の成果物に残し、過去の回はそれだけを読む

- **質問**: 直近 10 回の合計を、各回の JUnit XML から毎回求め直すか、各回で求めた合計を残して読むか
- **選んだ案**: 中央値のジョブが、この回の合計を集計の JSON に書いて成果物 `pytest-duration-total` に上げる。過去の回は、この成果物の一覧を 1 回引き、各回の集計だけを取る（処理の流れ）
- 採らなかった案: 過去の回の XML を毎回読み直す（1 回の `main` への push で、10 回分の成果物の一覧と多くて 108 個の ZIP の取得が要る。直前の回とほぼ同じ 9 回分の集計を、push のたびに繰り返す）
- 採らなかった案: 直近 10 回の合計を 1 つの成果物に持ち回す（前の回の値をそのまま引き継ぐので、1 回の誤った値がそれ以後のすべての回に残る。前の回の成果物が無いと、つながりが切れる）
- **代償**: 合計は XML の写しになり、合計の求め方を変えても、過去の回の集計は前の求め方のままである。求め方を変えたときは、保存期間の内で 10 回が入れ替わるまで、新旧の値が混ざる
- 根拠: Value 8「正本の外に写した値（集計・キャッシュ）は、読む速さや費用を実測した根拠を設計文書に書いてから足される」・Value 2「CI の時間」（MVV バージョン 11）。呼び出しの数は処理の流れのとおりで、ジョブの秒は「未確認のまま残ること」で測る

## 影響と危険

- **働く範囲**: このリポジトリの CI とスクリプトだけである。NDF のコマンド・結果 JSON・設定のスキーマ・規則 `budget` は変わらない。NDF の配布物で変わるのは、テストの `plugins/ndf/tests/conftest.py` の ok の出力の確かめだけで、利用者のリポジトリでは働かない
- **プロジェクトごとに違うもの**: 上限と回数はこのリポジトリの設定ファイル `scripts/ci-test-duration.json` で受ける。NDF の設定には置かない
- **当たるモード**（NDF が工程を進める形の種類。`standard`・`light` など）: 無い（NDF の工程の振る舞いを変えない）
- **ジョブの数**: ジョブを 1 つ足すと 40 になり、上限 `budget.ci_jobs` の 40 に並ぶ。次にジョブを足す変更は、上限を上げるかジョブをまとめる必要がある
- **最初の割り当て**: `assign_files` は、表に秒の無いファイルを、割り当てる NDF のファイルのうち表に秒のあるものの秒の中央値で数える。秒のある NDF のファイルが 1 つも無ければ、1 秒で数える。根のテストの秒は、この代わりの値に使わない。実装の途中で NDF の実測秒が表に無い間は、すべての NDF のファイルが 1 秒で数えられ、ファイルの数で分けることになる。実装の Pull Request の CI の成果物で表を作り直してから、受け入れ条件 8・9 の秒を測る
- **同じファイルを触る別の変更**: `.github/workflows/pytest.yml` と `.ndf/project.json` は多くの変更が触る。マージの前に `main` を取り込み、ジョブの数を数え直す

### 代表のリポジトリでの振る舞い

NDF の配布物で変わるのはテストの `conftest.py` だけで、利用者のリポジトリでは読まれない。

| 代表のリポジトリ | 設定の要点 | 直した後に起きること | この種類で扱えなくなること |
| --- | --- | --- | --- |
| Web サービス | テストのコマンドと CI のジョブを `.ndf/project.json` の `test` に書く | 規則 `budget` のテストの件数の上限は、設定にキーがあれば今と同じく効く。設計・設計の検査・cross-review・MVV 判定の振る舞いは変わらない | 無し |
| モバイルアプリ | 同上 | 同上 | 無し |
| 業務文書だけ | テストを持たないことがある | 同上（テストのファイルが無ければ件数は 0 件） | 無し |

### ai-plugins だけの事情

| 値 | 置く所 | 持つ Issue |
| --- | --- | --- |
| 中央値に数える回数・上限の秒・遅い順に出す件数 | `scripts/ci-test-duration.json` | [#4331](https://github.com/devbasex/ai-plugins/issues/4331) |
| テストを 6 つのジョブへ分けること、根と NDF の 2 つのテストの組、成果物の名前 | `.github/workflows/pytest.yml` | [#4331](https://github.com/devbasex/ai-plugins/issues/4331) |
| テストの件数の上限を置かないこと | `.ndf/project.json` の `budget`（`tests` のキーを持たない） | [#4331](https://github.com/devbasex/ai-plugins/issues/4331) |

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1. 1 件ごとの所要が成果物に残る | ワークフローの NDF のテストの手順が `--junitxml` を付け、成果物 `pytest-ndf-junit-<番号>` を上げる（実装の Pull Request の CI の成果物の一覧で見る） | ワークフローの検査ではなく手動確認で見る。リリース後の確認の計画の条件 1 |
| 2. 1 回の所要がコマンド 1 つで出る | `run` が、根と NDF の XML を読んで、両方の合計・遅い順の上位・ジョブごとの所要を出す（`gh` の応答を差し替えた範囲テスト） | NDF の XML を数えない・遅い順を逆にする・ジョブの手順の秒を取り違えると落ちる |
| 3. 記録が無い回は 0 以外で終わる | 根か NDF の XML が 1 つも無い回で、`run` が無い側を標準エラーに出して 1 を返す。期限切れ（`expired`）の成果物も無いとして扱う | 片方だけ無い回を 0 で返す・期限切れの成果物を読みに行くと落ちる |
| 4. 中央値が上限を超えたら分かる | そろった回が 10 回あり中央値が上限を超えると、`trend` が中央値・上限・超えた秒と `::warning` の行を出す | 平均で比べる・超えた秒の符号を逆にする・注記を出さないと落ちる |
| 5. 上限以下なら知らせが出ない | 中央値が上限と等しいとき・下回るとき、`trend` が `::warning` の行を出さない。そろった回が 10 回に満たないときも出さない | 等しいときに出す・回数が足りないのに判定すると落ちる |
| 6. Pull Request の CI を落とさない | `trend` は超えたときも `gh` を使えないときも 0 を返す。中央値のジョブは `main` への push のときだけ走り、`continue-on-error: true` を持ち、集約のジョブ `pytest` の `needs` に入らない（ワークフローの定義で見る） | 超えたときに 0 以外を返すと落ちる |
| 読めない回の扱い | 過去の回の集計のうち 1 回が壊れていても、次に古い回を取って 10 回そろえて判定する。成果物の一覧が取れないときは判定せず 0 を返す。この回の XML がそろわないときは集計の JSON を書かない（`gh` の応答を差し替えた範囲テスト） | 壊れた 1 回で探索をやめる・期限切れの集計を数える・そろわない XML で集計を書くと落ちる |
| 7. `budget.tests` の決定がそろう | `.ndf/project.json` の `budget` に `tests` が無く、`ndf check` の規則 `budget` がテストの件数を上限と比べない（既存の `plugins/ndf/tests/checks/test_budget.py` の「キーが無い項目は比べない」で縛られている） | 規則の側は既存のテストが持つ。設定と確定仕様の一致はテストで縛らず、cross-review で見る |
| 8. 実測の秒で分ける | フックが、実測秒の表の秒で持ち分を決める。表に重いファイルを置くと、そのファイルが空いているジョブへ入る | 項目の数で分ける・リポジトリのルートから見たパスへ直さずに表を引くと落ちる |
| 9. 収集の手順の秒が減り、前と後が示される | ワークフローから「NDF のテストのファイルを決める」の手順が無くなる。秒は実装の Pull Request に書く（手動確認） | テストでは縛らない。秒は実行機で変わる |
| 10. どのファイルもちょうど 1 つのジョブで流れる | 同じファイルの一覧と表から 6 つの持ち分を作ると、和が一覧と一致し、重ならない。一覧に無いファイルも 1 つのジョブだけが持つ。前提の確認の `files` の出力（`tests/a.py` と、`tests/b.py`・`tests/c.py`）も変わらない | 一覧に無いファイルをどのジョブも外す・2 つのジョブが持つと落ちる |
| 11. ok の出力の確かめが 6 ジョブの記録で決まる | `NDF_OK_TOOLS_OUT` を渡した全体の実行は、検証済みのサブコマンドの名前を書いて、検証済みでないサブコマンドがあっても落ちない。渡さない全体の実行は、今と同じく、検証済みでないサブコマンドがあれば落ちる | 渡したときに確かめる・渡さないときに確かめないと落ちる |
| 実測秒の作り直し | `seconds` が根と NDF の XML から、リポジトリのルートから見たパスをキーにした表を作る | NDF の `classname` をリポジトリのルートから見たパスへ戻さないと、表に `plugins/ndf/tests/...` のキーが入らず落ちる |

## 設計の結果

| 課題 | 扱い | 統合先 | 触るファイル |
| --- | --- | --- | --- |
| #4331 | 実装する | — | `.github/workflows/pytest.yml`、`scripts/pytest_shard.py`、`scripts/pytest-shard-seconds.json`、`scripts/ci_test_durations.py`（新設）、`scripts/ci-test-duration.json`（新設）、`scripts/tests/test_pytest_shard.py`（新設）、`scripts/tests/test_ci_test_durations.py`（新設）、`plugins/ndf/tests/conftest.py`、`plugins/ndf/tests/test_ok_tools_record.py`（新設）、`.ndf/project.json`、`docs/specifications/ndf-split-tables-toolchain-and-budget.md` |

## 人へ残す判断

当たるレッドラインの番号は、NDF の共通原則の「必ず人の承認が要る操作」の表の番号である（[plugins/ndf/contracts/data/ndf-common-principles.md](../plugins/ndf/contracts/data/ndf-common-principles.md)）。C7 は、利用者の設定（`.ndf/`）と指示書を書き換える操作である。

| 判断 | 当たるレッドライン | なぜ当たるか |
| --- | --- | --- |
| このリポジトリの `.ndf/project.json` から `budget.tests` を外す（決定 5） | C7 | 利用者の設定（`.ndf/`）を書き換える。変更は実装の中でコミットし、設計承認ゲートとリリース承認ゲートで人が見る |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 上限の初めの値 | NDF のテストの JUnit XML がまだ無く、根と NDF の合計の秒が分からない（根だけは 2026-11-24 の 1 回で 136 秒）。実装の Pull Request の CI の実行で `run` を打って合計を測り、その 1.25 倍を 10 秒単位で切り上げた値を `limit_seconds` に置く |
| ルートの `conftest.py` が NDF のテストで読まれないこと | NDF のテストの手順は `plugins/ndf` を起点に流すので、ルートの `conftest.py`（`SHARD_TOTAL` で項目を外す）は読まれない見込みである。実装で同じ環境変数を渡して流し、ルートの側の外し方が働かないことを NDF のテストの件数で確かめる |
| 中央値のジョブの所要 | 過去の回の集計を取る方式で、API の呼び出しは 1 回の `main` への push で一覧 1 回と ZIP 多くて 9 回になる見込みだが、ジョブの秒は測っていない。実装の後の最初の `main` の実行で、ジョブの秒を測って Pull Request に書く |
| 収集の前に外したときの手順の秒 | 持ち分でないファイルを外すと各 worker の収集が持ち分だけになる見込みで、受け入れ条件 9 の前と後の秒は実装の Pull Request の CI で分かる |

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | テスト 1 件ごとの秒は JUnit XML の形と pytest の書き方に、知らせの形と成果物の読める時期は GitHub Actions の仕様に従う |
| 主要な製品 | 調べない | 比べる相手は CI の上でテストの結果と秒を扱うアクションとプラグインで、ライブラリとサービスの表で比べる |
| ライブラリとサービス | 調べる | ジョブへの分け方（pytest-split・pytest-xdist）と、結果を出すアクションを使えるかを決める |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | pytest の JUnit XML（`--junitxml`・`junit_duration_report`） | `testcase` の `time` は既定で準備・本体・後始末の合計（`total`） | 決定 1 の合計は `time` をそのまま足す | https://raw.githubusercontent.com/pytest-dev/pytest/main/doc/en/reference/reference.rst の `junit_duration_report`（2026-11-25） |
| STD2 | GitHub Actions の workflow commands | `::warning title=...::` の行が注記になり、`GITHUB_STEP_SUMMARY` のファイルがジョブの要約になる | 決定 3 の知らせの形 | https://raw.githubusercontent.com/github/docs/main/content/actions/reference/workflows-and-actions/workflow-commands.md の「Setting a warning message」（2026-11-25） |
| STD3 | GitHub Actions の成果物 v4 | 上げた直後から API で取得できる。保存期間は既定 90 日で 1〜90 日 | 処理の流れで、実行の途中のこの回の成果物を、同じ回の中央値のジョブが取る。保存期間は前提 2 のとおり | https://github.blog/changelog/2023-12-14-github-actions-artifacts-v4-is-now-generally-available/ 、https://raw.githubusercontent.com/actions/upload-artifact/main/README.md の「Retention Period」（2026-11-25） |

### ライブラリとサービス

| # | 候補 | バージョン | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | pytest-split | 0.11.0 | MIT | 2026-02-03 | 無し | 満たさない。テスト 1 件の単位で割り、ファイル単位の前提を保てない（アルゴリズムは `duration_based_chunks` と `least_duration`） | https://pypi.org/pypi/pytest-split/json 、https://raw.githubusercontent.com/jerry-git/pytest-split/master/README.md （2026-11-25） |
| LIB2 | EnricoMi/publish-unit-test-result-action | v2.26.0 | Apache-2.0 | 2026-11-06 | 無し | 満たさない。1 回の結果をチェックとして出すもので、直近の複数回の中央値と上限の比べ方を持たない。`checks: write` が要る | https://raw.githubusercontent.com/EnricoMi/publish-unit-test-result-action/master/README.md の「Permissions」、https://api.github.com/repos/EnricoMi/publish-unit-test-result-action/releases/latest （2026-11-25） |
| LIB3 | GitHub CLI（`gh api`・`gh run download`） | 2.x（このリポジトリの NDF が既に使う） | MIT | 確かめていない | 無し | 満たす。成果物の一覧・取得とジョブの時刻を読める | `plugins/ndf/src/ndf/domains/project/measure_ci.py` の `Gh`（2026-11-25） |
| LIB4 | Python 標準ライブラリ `xml.etree.ElementTree`・`statistics` | Python 3.13 | PSF | — | 無し | 満たす。JUnit XML の読み取りと中央値 | `plugins/ndf/src/ndf/domains/project/measure_ci.py` の `junit_seconds`・`ndf_record`（2026-11-25） |
| LIB5 | pytest-xdist | 根と NDF の `uv.lock` の固定のもの | MIT | 確かめていない | 無し | 一部だけ。1 つのジョブの中の並列（`-n auto`）は使い続けるが、ジョブをまたいだ割り当ては持たない | `.github/workflows/pytest.yml` の `-n auto`、`plugins/ndf/pyproject.toml`（2026-11-25） |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| GitHub CLI と pytest-xdist の最後のリリースの日付 | 試していない | どちらも既に使っている依存で、この設計で使い方を変えない |

調べた量: 候補 8 件・取得の試み 10 回。
