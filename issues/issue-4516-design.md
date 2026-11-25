# プロジェクトの設定: 解析をやり直しても、設定の値が変わらなければ追跡するファイルに差分が出ない（#4516）

解析の書き出し（`ndf project write`）は、実行のたびに変わる値を追跡する設定 `.ndf/project.json` へ書く。そのため、設定が変わらなくても差分が出て `git pull` を止める。実行のたびに変わる値（解析の記録と計測値）は、中に置いた `.gitignore` でディレクトリ全体を git から外した `.ndf/measured/` の別のファイルへ移す。`.ndf/project.json` には、設定の値と、どの値を解析が書いたかの指紋だけを残す。置き場と移すキーの採否は「決定の記録」に、関数の粒度は [issue-4516-design-detail.md](./issue-4516-design-detail.md) に、調べた製品は末尾の「外部の調査」にある。

## 今の姿

何が起きたか・目的・受け入れ条件は要求仕様（[issue-4516-requirements.md](./issue-4516-requirements.md)、正は #4516 の本文）にある。この文書は「どう解くか」と「なぜその解き方か」だけを書く。

```mermaid
flowchart LR
  M["ndf project measure<br/>測定の結果（一時ファイル）"] --> W["ndf project write<br/>write.py"]
  W -->|"設定の値・書いた値の指紋<br/>解析の記録・計測値（すべて 1 ファイル）"| P[(".ndf/project.json<br/>git が追跡する")]
  P -->|"解析の記録"| C["ndf project check<br/>check.py"]
  P -->|"書いた値の指紋"| W
```

`.ndf/project.json` のうち、設定の値でないものは次の 4 種類である（このリポジトリの `main` の `.ndf/project.json` で確かめた）。

| 種類 | キー | 書き手 | 読み手 |
| --- | --- | --- | --- |
| 解析の記録（解析器のバージョン・解析した時刻・入力のファイルのハッシュ・ブランチの構成） | `analysis.analyzer`・`analysis.at`・`analysis.inputs`・`analysis.branches` | `write.plan` | `check.judge`（設定が古くなっていないかの判定） |
| 書いた値の指紋 | `analysis.written` | `write.merge_project`・`write._declare_ci_tests` | `write.written_digests`（人が直した値か解析が書いた値かの見分け） |
| 計測値（言語ごとのファイル数・ワークフローごとの直近の実行時間） | `languages[].files`・`ci.workflows[].wall_seconds` | `measure_repo.measure_languages`・`measure_ci`。実行時間は `write._carry_walls` が前の値を引き継ぐ | 書き出しのほかに無い |
| テストの所要時間（廃止の予定） | `test_duration` | 今の書き出しは書かない（スキーマで `deprecated`。書く項目の一覧 `ITEM_KEYS` から外れる） | 無い |

読み手の有無は `grep -rn "wall_seconds\|test_duration\|\"analysis\"\|languages" plugins/ndf/src scripts experimental` で確かめた（テストを除く）。

- 入力のファイルは、解析が読むファイル（CI のワークフローの定義・`pyproject.toml` などの依存とテストの設定・指示書）で、一覧は `check.is_input` が決める。そのハッシュは HEAD のコミットの blob の ID である
- 書いた値の指紋は、項目の値全体から作る。そのため、ファイル数や実行時間が変わると `languages`・`ci` の指紋も変わり、計測値の揺れが指紋の差分としても出る
- 書き出しは、時刻のほかに変わりが無ければ `.ndf/project.json` を書かない（`_same_but_time`）。しかし入力のファイルが 1 つでも変わると、入力のファイルのハッシュの表が変わって書く
- NDF が書くファイルの一覧（`plugins/ndf/contracts/domains/ndf-files.json`。保存先のディレクトリの定義は `locations.json` が持つ）の行 `project-measured` は、計測値をリポジトリの外の状態の保存先（`~/.local/state/ndf/measured/<リポジトリ>/project.json`）に置くと定めるが、書くコードは無い

## 文脈と制約

- 書き出しは、clone した元のディレクトリ（worktree でない側。以下メインディレクトリ）の `.ndf/` に書く。worktree から打っても同じ位置になる（`check.repo_dirs` が返すメインディレクトリ）
- `.ndf/project.json` は利用者の設定のファイルで、それを変える Pull Request は取り込む前に人が確かめる（NDF の共通原則の C7）。書き出しそのものは「コミットしない限り取り消せる」ので承認を求めない、と `project-analysis.md` が定めている
- このリポジトリの方針（MVV）の Value 4「普及のフェーズに入るまで、後方互換は求めない」に従い、旧い形を読む仕組みを持たない。次の書き出しが新しい形へ移す
- `ndf project branch-strategy` は `.ndf/project.json` のセクション `branch_strategy` だけを書く。解析の書き出しはこのセクションを変えない
- スキーマは `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` が正である。`plugins/ndf/skills/development-workflow/schemas/project.schema.json` はバイト単位で同一のコピーで、`scripts/check-ndf-contracts.py` が一致を確かめる
- 設定の説明の文書（`plugins/ndf/docs/contracts/ndf-settings.md`）とファイルの説明の文書（`files.md`）は、ファイルの一覧・保存先のディレクトリの定義・スキーマから生成する（`ndf.checks.spec_render`）。手で書かない

## 解き方の全体像

```mermaid
flowchart LR
  M["ndf project measure"] --> W["ndf project write"]
  W -->|"① 無いときだけ作る"| G[(".ndf/measured/.gitignore<br/>中身は *")]
  W -->|"② 設定の値・書いた値の指紋"| P[(".ndf/project.json<br/>追跡する")]
  W -->|"③ 解析の記録・計測値"| R[(".ndf/measured/project.json<br/>追跡しない")]
  R -->|"解析の記録"| C["ndf project check"]
  P -->|"書いた値の指紋"| W
  R -->|"前の実行時間"| W
```

- `.ndf/project.json` には、設定の値と書いた値の指紋（`analysis.written`）だけが残る。指紋は計測値を除いた値から作るので、設定の値が変わったときだけ変わる
- 解析の記録と計測値は `.ndf/measured/project.json` に書く。書き出しは、`.ndf/measured/.gitignore` が無いときだけ中身 `*` で作り、ディレクトリ全体を git から外す（pytest・ruff のキャッシュと同じ形）
- `ndf project check` は `.ndf/measured/project.json` の解析の記録で判定する。無い・読めないときは「古い」と判定し、開発の作業の始めの手順に解析をやり直させる
- 既存の `.ndf/project.json` に残る解析の記録・計測値・テストの所要時間（旧い形のキー）も「古い」と判定し、次の書き出しが取り除く。項目ごとに人が直したか解析が書いたかは、計測値を取り除く前の値のハッシュを前の指紋と比べて見分ける

## ドメインモデル

### コンテキスト

| コンテキスト | この範囲で意味が 1 つに決まる語 |
| --- | --- |
| プロジェクトの設定（`ndf-project-settings`） | 設定・解析・解析の記録・計測値・書いた値の指紋・測った値 |

### 集約

| 集約 | 書き換えてよい主体 | 集約ルート | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| 設定 | 人と解析の書き出し（人が直した値は解析が書き換えない） | `.ndf/project.json` | 項目（`languages`・`test`・`ci` など） | 書いた値の指紋（`analysis.written`） |
| 解析の記録と計測値 | 解析の書き出しだけ（人は書かない） | `.ndf/measured/project.json` | 無し | 解析の記録（入力のファイルのハッシュ・ブランチの構成など）・ファイル数の表・実行時間の表 |

### 不変条件

| # | 集約 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | 設定 | 書き出しの後、`.ndf/project.json` に解析の記録・計測値・テストの所要時間のキーが無い | 書き出しがスキーマの検査（`additionalProperties`）で止まる |
| I2 | 設定 | 書いた値の指紋は、計測値を取り除いた値から作る。設定の値が同じなら指紋も同じ | テストが落ちる |
| I3 | 設定 | 解析が書いたかの見分けは、計測値を取り除く前の値と前の指紋で行う（移行の 1 回目に、解析が書いた値を人が直した値と取り違えない） | テストが落ちる |
| I4 | 解析の記録 | 解析の記録のディレクトリには、中身 `*` の `.gitignore` がある。書き出しは、記録より先に `.gitignore` を置く | 書き出しが止まる（終了コード 1） |
| I5 | 解析の記録 | 解析の記録が無い・読めない・形が違うとき、判定は「古い」である（「壊れている」にしない） | テストが落ちる |
| I6 | 設定 | `.ndf/project.json` に旧い形のキーが残っているとき、判定は「古い」で、書き出しはそのキーを取り除いてから形を確かめる（旧い形を「壊れている」にしない） | テストが落ちる |

### ドメインイベント

番号は要求仕様のドメインイベントを引き継ぐ。

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | 設定が古くなっていないかを確かめた | `check.judge` | 開発の作業の始めの手順。NDF の開発の手順（`development-workflow` の Skill の手順 0）が `ndf project check` を打ち、終了コード 3 なら同じ手順が `ndf project measure` と `ndf project write` を打って解析をやり直す |
| E2 | リポジトリと CI を測った | `ndf project measure` | `ndf project write` |
| E3 | `.ndf/measured/` を git から外す `.gitignore` を置いた | `write.plan` → `cli._write` | git |
| E4 | 設定を書き出した | `write.plan` → `cli._write` | 次の E4（書いた値の指紋） |
| E5 | 解析の記録と計測値を書き出した | `write.plan` → `cli._write` | 次の E1・次の E5（前の実行時間） |

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `measured.py`（新設） | 解析の記録の形を持つ。設定の値から計測値を取り分ける・旧い形のキーを取り除く・前の実行時間を引き継ぐ・記録を読む |
| `write.py`（変更） | 設定の値を決め、解析の記録を組み、`.gitignore`・`.ndf/project.json`・解析の記録の 3 つのファイルを書く計画を作る |
| `check.py`（変更） | 設定が古くなっていないかを、`.ndf/project.json` でなく `.ndf/measured/project.json` の解析の記録から判定する |
| `cli.py`（変更） | 書き出しの入力に、前の解析の記録を足す |
| 設定のスキーマ（変更） | `analysis` を `written` だけにし、`languages[].files`・`ci.workflows[].wall_seconds`・`test_duration` を外す |
| 解析の記録のスキーマ（新設） | `plugins/ndf/contracts/schemas/internal/project-measured.schema.json` |
| ファイルの一覧と保存先のディレクトリの定義（変更） | 保存先の表（`locations.json`）の項目 `measured` の場所を、リポジトリの `.ndf/measured` に変える。ファイルの表（`ndf-files.json`）の行 `project-measured` のキーを新しい形に合わせ、`.gitignore` の行を足す |

## データ構造

解析の記録と計測値を置くファイル（`.ndf/measured/project.json`。コードとスキーマの名前は `measured`）の形:

```json
{
  "version": 1,
  "analyzer": 3,
  "at": "2026-11-25T11:02:57+00:00",
  "inputs": {".github/workflows/pytest.yml": "2187b0d3…", "pyproject.toml": "3634277f…"},
  "branches": {"head": "main", "present": ["develop", "main"]},
  "file_counts": {"python": 1064, "shell": 21, "javascript": 8},
  "wall_seconds": {".github/workflows/pytest.yml": 922.0, ".github/workflows/lint.yml": 19.0}
}
```

| キー | 型 | 必須 | 中身 |
| --- | --- | --- | --- |
| `version` | 整数（1） | 必須 | 形のバージョン |
| `analyzer` | 整数 | 必須 | 解析器のバージョン（`check.ANALYZER`） |
| `at` | 文字列（ISO 8601） | 必須 | 解析した時刻（UTC） |
| `inputs` | パス → blob の ID の表 | 必須 | 入力のファイルのハッシュ（`check.inputs`） |
| `branches` | `{head, present}` | 必須 | ブランチの構成（`check.branch_state`） |
| `file_counts` | 言語の名前 → 整数 | 任意 | 言語ごとのファイル数。`languages` が不明なら無い |
| `wall_seconds` | ワークフローのパス → 数 | 任意 | ワークフローごとの直近の実行時間。測れなかったワークフローは前の値を引き継ぐ |

`.ndf/project.json` の変わる箇所:

| キー | 今 | 変えた後 |
| --- | --- | --- |
| `analysis` | `{analyzer, at, inputs, branches, written}` | `{written}` |
| `languages[]` | `{name, frameworks, files, version?}` | `{name, frameworks, version?}` |
| `ci.workflows[]` | `{path, jobs, wall_seconds?}` | `{path, jobs}` |
| `test_duration` | 廃止の予定（書かれない。残っていれば読める） | スキーマから消す。残っていれば次の書き出しが取り除く |

書き出しは、`.ndf/measured/.gitignore` が無いときだけ作る。既にあれば変えない。中身は `# NDF の解析が書く記録。コミットしない` と `*` の 2 行である。

## 処理の流れ

```mermaid
sequenceDiagram
  participant CLI as cli._write
  participant W as write.plan
  participant MS as measured
  participant FS as メインディレクトリの .ndf/
  CLI->>W: 今の設定・測定の結果・回答（解析が決められない項目に、作業を進める AI が答えた値）・前の .ndf/measured/project.json
  W->>W: 項目ごとに、人が直したか解析が書いたかを見分ける（取り除く前の値と前の指紋）
  W->>MS: split（新しい値から計測値を取り分ける）
  W->>W: 設定の値から指紋を作る・スキーマを確かめる
  W->>MS: carry_walls（測れなかったワークフローの実行時間を引き継ぐ）
  W->>W: 解析の記録を組む・スキーマを確かめる
  W-->>CLI: 書くファイル（.gitignore → project.json → 解析の記録の順）
  CLI->>FS: 書く（変わったものだけ）
```

書く順は `.ndf/measured/.gitignore` → `.ndf/project.json` → `.ndf/measured/project.json` である。`.ndf/measured/project.json` を最後に書くのは、その中の解析の記録が「この入力で解析を終えた」ことを表し、判定がそれを読むためである。`.gitignore` を最初に書くのは、記録のファイルが git の対象から外れていない状態で置かれ、誤ってコミットされるのを防ぐためである（I4）。途中で止まったときの状態と次の判定は次のとおりで、どれも解析のやり直しで揃う。

| 止まった所 | 残る状態 | 次の `ndf project check` |
| --- | --- | --- |
| `.gitignore` の後 | `.gitignore` だけが新しい | 記録が無い、または記録の入力のハッシュが今と違えば「古い」 |
| `.ndf/project.json` の後で、記録が無いか入力が違う | 設定は新しく、記録は無いか前の解析のもの | 「古い」 |
| `.ndf/project.json` の後で、前の記録の入力が今と同じ | 設定は新しく、記録の時刻と計測値だけが前のまま | 「新しい」。設定は既に書き終えているので困らない |

## 決定の記録

### 決定 1: 追跡するファイルを書き換えずに解析の記録を git から外すため、`.ndf/measured/` の中に `*` の `.gitignore` を置く

- **質問**: 解析の記録と計測値をどこに置き、どう git から外すか
- **選んだ案**: メインディレクトリの `.ndf/measured/project.json` に置き、同じディレクトリに中身 `*` の `.gitignore` を書き出しが作る。保存先の表の項目 `measured` を「リポジトリの `.ndf/measured`・追跡しない」へ直す
- 採らなかった案: ファイルの一覧の今の行のとおり、リポジトリの外の状態の保存先（`~/.local/state/ndf/measured/<リポジトリ>/`）に置く（利用者の指示は「.gitignore される別ファイル」で、リポジトリの外は指示と食い違う。clone を消しても記録だけが残り、片付けの対象が増える）
- 採らなかった案: `.ndf/project.local.json` に置き、`.ndf/.gitignore` へ行を足す（理由は下の 2 つ）
  - 追跡する `.ndf/.gitignore` に差分が 1 回出て、利用者の設定の書き換え（C7）になる
  - 名前の `.local` は、`worktree.local.json` で「各自の機械の値で共有設定を上書きする個人設定」の意味で使っている。上書きしないファイルに付けると、1 語が 2 つの意味になる
- 採らなかった案: 根の `.gitignore` に行を足す（`project-analysis.md` の「`.ndf/` の追跡」は、NDF が根の `.gitignore` を書き換えないと定めている）
- **代償**: `.ndf/` の下にディレクトリが 1 つ増える。`git status --ignored` には `.ndf/measured/` が出る
- 作り方: 標準に倣う（pytest・ruff のキャッシュと同じく、ディレクトリの中に `*` の `.gitignore` を置く）
- 比べた候補: STD1 / PRD1 / PRD2
- 根拠: Value 6「NDF が作業で生んだもの（ブランチ・作業ディレクトリ・状態の記録・一時ファイル）は役目を終えると自動で片付き、利用者のストレージとリポジトリの構造は常に整理された状態に保たれる」・Value 12「広く利用され、メンテナンスが活発で実績のある、著名かつ最新のものがあればそれが使われ」（MVV バージョン 11）。STD1・PRD1・PRD2

### 決定 2: 新しく clone した場所でも人が直した値と解析が書いた値を見分けるため、書いた値の指紋は `.ndf/project.json` に残す

- **質問**: 書いた値の指紋（`analysis.written`）も、解析の記録と一緒に別のファイルへ移すか
- **選んだ案**: 移さない。`.ndf/project.json` の `analysis` を `{written}` だけにする。指紋は計測値を取り除いた値から作るので、設定の値が変わったときだけ変わり、push しても問題が無い
- 採らなかった案: 指紋も移し、記録が無ければすべての値を人が直した値とみなす（新しく clone した場所では、解析が書いた値がすべて人の値になり、その後も解析が値を直せない。`languages` のような測った値がその場所で古いまま固まる）
- 採らなかった案: 指紋も移し、記録が無ければすべての値を解析が書いた値とみなす（人が直した値を解析が書き換える。`project-analysis.md` の「手で書いた値は変えない」を破る）
- **代償**: `.ndf/project.json` に人が書かないキー（`analysis.written`）が 1 つ残る。設定の値を変えた Pull Request では、その項目の指紋の 1 行も変わる
- 根拠: Value 3「同じ操作を打ち直しても、結果は 1 回打ったときと変わらない」（MVV バージョン 11）。指紋を設定と一緒に追跡すれば、どこで clone して書き出しても、人が直した値と解析が書いた値の見分けが同じになる

### 決定 3: 設定として共有する意味の無い値だけを移すため、移すキーを実行と時刻で変わる値に限る

- **質問**: どのキーを `.ndf/project.json` から外すか
- **選んだ案**: 解析の記録（解析器のバージョン・時刻・入力のファイルのハッシュ・ブランチの構成）と計測値（言語ごとのファイル数・ワークフローごとの実行時間）を移す。テストの所要時間（`test_duration`）は読み手も書き手も無い廃止の予定のキーなので、移さずにスキーマから消し、残っていれば取り除く。ワークフローのジョブ数（`ci.workflows[].jobs`）は残す。ジョブ数は、ワークフローの定義と、解析が選ぶ 1 回の実行（ジョブを飛ばしていない直近の成功の実行）のジョブ数の大きい方で決まる。そのため、マトリクスの数が実行ごとに変わるワークフローを除けば、ワークフローを変えない限り変わらない
- 採らなかった案: 入力のファイルのハッシュとブランチの構成を `.ndf/project.json` に残す（ワークフローや指示書を変えるたびに、設定の値が同じでも差分が出る。受け入れ条件 1 を満たさない）
- 採らなかった案: 読み手の無いファイル数と実行時間を書くのをやめる（利用者の指示は「別ファイルに配置する」で、消すことではない。消すかは、読み手の要否と一緒に別に決める）
- **代償**: マトリクスの数が実行ごとに変わるワークフローでは、ジョブ数が変わり、`.ndf/project.json` に差分が出る。この差分は許す（ジョブ数はワークフローの構成を表す設定の値として扱う）。このリポジトリの 11 本のワークフローでは変わっていない（`main` の `.ndf/project.json` の差分に `jobs` の行が無い）
- 根拠: Value 1「利用者が手を止めるのは、作るかを決めるときと、出してよいかを決めるときだけである」・Value 8「データ構造は正規化を既定にし、同じ事実を 2 か所に持たない」（MVV バージョン 11）

### 決定 4: 移行のための別のコマンドを持たないため、次の書き出しが旧い形の値を取り除いて移す

- **質問**: 既存の `.ndf/project.json` に残る解析の記録と計測値を、どう新しい形へ移すか
- **選んだ案**: 次の `ndf project write` が移す。旧い形のキーは、`analysis` の `written` 以外のすべて・`languages[].files`・`ci.workflows[].wall_seconds`・`test_duration` である
  - `ndf project check` は、旧い形のキーを見つけたら「古い」と判定し、開発の作業の始めの手順に解析をやり直させる（I6）
  - 書き出しは、項目ごとに、計測値を取り除く前の値のハッシュを前の指紋と比べる（I3）。一致すれば解析が書いた値として新しい値へ置き換え、違えば人が直した値として計測値のキーだけを取り除いて残す
  - 前の `analysis` の時刻と入力のファイルのハッシュは捨て、新しい解析の値で解析の記録を作る
- 採らなかった案: 移行のコマンド（`ndf settings migrate` の改名の表の行）を足す（改名の表はキーの名前と値を改める表で、ファイルをまたいで値を移す形を持たない。解析の記録は次の解析で作り直せるので、旧い値を移す意味も無い）
- 採らなかった案: 旧い形の `analysis` も読む互換を持つ（Value 4 が互換の仕組みを求めない）
- **代償**: 移行の 1 回目は判定が必ず「古い」になり、解析が 1 回走る。旧い形のキーを取り除く処理（`measured.strip_legacy`）を残し続ける。消す時期は決めていない
- 根拠: Value 4「普及のフェーズに入るまで、後方互換は求めない。旧い設定の形を残す互換の仕組みを持たず、移行のコマンドできれいな最終形へ移す」（MVV バージョン 11）。この設計では、次の `ndf project write` が移行のコマンドの役を兼ねる。別のコマンドを足さないのは、解析の記録が次の解析で作り直せるためである

### 決定 5: 利用者に直させないため、解析の記録が無い・壊れているときは「古い」と判定する

- **質問**: 解析の記録が無い・読めない・形が違うとき、`ndf project check` は何を返すか
- **選んだ案**: 「古い」（終了コード 3）を返し、理由に「解析の記録が無い」か「解析の記録が読めない（<誤り>）」を出す。開発の作業の始めの手順が解析をやり直し、書き出しが記録を作り直す
- 採らなかった案: 読めない記録を「壊れている」（終了コード 2）にする（作業の始めの手順が先へ進まず、利用者に直させる。記録は人が書かないファイルで、作り直せば直る）
- **代償**: 新しく clone した場所と、記録を消した場所では、最初の作業で解析が 1 回走る（タイムアウト 120 秒。GitHub の API の読み取りのリクエストが数十回）
- 根拠: Value 1「ワークフロー上の課題（再起動・告知・確認）で、利用者に操作を求めない」・Value 2「利用者が払う費用（LLM の token・API の利用料・CI の時間・マシンの資源）が小さい」（MVV バージョン 11）

### 決定 6: 同じ事実を 2 か所に持たないため、計測値は名前から値への表で持つ

- **質問**: `.ndf/measured/project.json` で、計測値を設定と同じ配列の形で持つか、名前から値への表で持つか
- **選んだ案**: 言語の名前 → ファイル数（`file_counts`）、ワークフローのパス → 実行時間（`wall_seconds`）の表で持つ。言語とワークフローの並びは設定だけが持つ
- 採らなかった案: 設定の `languages[]`・`ci.workflows[]` と同じ配列をコピーする（言語とワークフローの並びを 2 か所に持ち、片方だけが変わると食い違う）
- **代償**: 言語の名前が重なる測定の結果では、後の行が前の行を上書きする。今の測定は言語の名前ごとに 1 行を返す（`measure_repo.measure_languages`）
- 根拠: Value 8「データ構造は正規化を既定にし、同じ事実を 2 か所に持たない」（MVV バージョン 11）

## 影響と危険

- **働く範囲**: NDF を使うすべてのリポジトリで働く。開発の作業の始めの手順で `ndf project check`・`ndf project write` を通るとき
- **プロジェクトごとに違うもの**: 無い。置き場は保存先の表が持ち、設定も引数も足さない
- **当たるモード**: 作業の始めの手順を通るすべてのモード（`standard`・`light`・`operation`・`documentation`・`legacy-refactor`）
- **壊れうる箇所**: 移行の 1 回目に、利用者の `.ndf/project.json` に数十行の差分が出る。利用者がコミットして片付ける。この手順は、この実装で `project-analysis.md` に書き足す
- **前のバージョンの NDF との混在**: 前のバージョンのスキーマは `analysis.analyzer` などと `languages[].files` を必須にしている。そのため、前のバージョンの NDF は新しい形の `.ndf/project.json` を「壊れている」と判定する。NDF を新しいバージョンへ上げれば直る（要求仕様の「今の利用者への影響と互換」）
- **同じファイルを触る別の変更**: 設計の時点で無い。開いた Pull Request 100 件の変更ファイルに、`write.py`・`check.py`・`cli.py`・設定のスキーマ・ファイルの表・保存先の表を触るものは無かった（`gh pr list --state open --json files`、2026-11-25）。実装の計画で確かめ直す
- **新しく clone した場所**: 最初の作業で解析が 1 回走る（決定 5 の代償）。worktree はメインディレクトリの記録を読むので走らない

### 代表のリポジトリでの振る舞い

| 代表のリポジトリ | 設定の要点 | 直した後に起きること | この種類で扱えなくなること |
| --- | --- | --- | --- |
| Web サービス | `languages`・`test`・`ci`・`services` を解析が書く | 解析の記録と計測値が `.ndf/measured/` へ移り、`.ndf/project.json` は設定の値だけになる | 無し |
| モバイルアプリ | 同上（CI の実行時間が長い） | 同上。実行時間は `.ndf/measured/project.json` だけに残る | 無し |
| 業務文書だけ | `languages` が少ない・CI が無いこともある | 同上。`file_counts`・`wall_seconds` が無い記録になる | 無し |

### このリポジトリだけの事情

| 値 | 今の状態と片付き方 | 持つ Issue |
| --- | --- | --- |
| メインディレクトリの `.ndf/project.json` にある利用者の未コミットの差分（ファイル数と `analysis` の書き直し） | 実装をリリースした後、書き出しをやり直すと解析の記録と計測値が移る。差分は計測値の削除と `analysis` の縮小だけになり、利用者がコミットする | #4516 |
| `ci` が人の値として扱われている（`analysis.written` に `ci` が無い） | `ci` の値は今後も解析が書き換えない。移行では `wall_seconds` だけを取り除く | 無し（今の振る舞いのまま） |

## 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| 解析の記録 | 解析が書く、解析をいつどの入力で行ったかの値（解析器のバージョン・解析した時刻・入力のファイルのハッシュ・ブランチの構成）。git で追跡しないファイルに置き、設定には置かない | 追加 |
| 計測値 | 解析が測る、実行ごとに変わる数（言語ごとのファイル数・CI のワークフローごとの実行時間）。解析の記録と同じ、git で追跡しないファイルに置き、設定には置かない | 追加 |
| 書いた値の指紋 | 解析が書いた設定の値の SHA-256。今の値の指紋と一致すれば解析が書いた値、違えば人が直した値として扱う。設定の値が変わったときだけ変わるため、設定と一緒に追跡する | 追加 |

## テスト設計

番号は要求仕様の受け入れ条件と、上の不変条件である。

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1・I2 | 同じ測定の結果で、時刻・入力のファイルのハッシュ・ファイル数・実行時間だけを変えて 2 回書き出すと、2 回目に `write.plan` が返す書き込み対象に `.ndf/project.json` が含まれない | 計測値を設定に残す・指紋を計測値込みの値から作ると落ちる |
| 2・3・I1 | 書き出した `.ndf/project.json` の `analysis` が `written` だけで、`languages[].files`・`ci.workflows[].wall_seconds`・`test_duration` が無い | 項目のどれかを取り除き忘れると落ちる |
| 4・5・6・I4 | 書き出しの後、`.ndf/measured/project.json` に `git check-ignore` が真。追跡するファイルの変更は `.ndf/project.json` だけ。根の `.gitignore` に `!.ndf/measured/` を書いても外れたまま | `.ndf/measured/.gitignore` を置かない・`.ndf/.gitignore` へ書くと落ちる |
| 7 | 記録の入力のファイルのハッシュと今の HEAD が同じなら「新しい」、入力のファイルかブランチの構成を変えると「古い」 | 判定が `.ndf/project.json` の `analysis` を読むままだと落ちる |
| 8・I5 | 記録が無い・JSON でない・スキーマに合わないとき「古い」（終了コード 3）で、理由に解析の記録が無い・読めないと出る | 「壊れている」（終了コード 2）を返すと落ちる |
| 9 | 記録の無い状態で、前の指紋が一致する項目は新しい値になり、一致しない項目は残る | 記録が無いと指紋を読まない・すべてを置き換えると落ちる |
| 10・I6 | 旧い形（`analysis` の全体・`files`・`wall_seconds`・`test_duration` を持つ）の設定で、判定が「古い」（終了コード 3）を返す | 旧い形を「壊れている」にすると落ちる |
| 11・12・I3 | 10 の設定から書き出すと旧いキーが消え、旧い指紋が一致した項目は新しい値で置き換わり、一致しない項目は計測値だけを取り除いて残る | 取り除いた後の値でハッシュを比べる（解析が書いた値が人の値になる）と落ちる |
| 13 | 新しい測定で実行時間の無いワークフローは、前の記録の同じパスの実行時間を残し、出力に「前の値を残した」の行が出る | 引き継ぎを外す・設定の側で引き継ぐと落ちる |
| 14 | `ndf check`（生成した説明の文書の食い違い・スキーマのコピーの一致）と `python3 scripts/check-skill-frontmatter.py` が通る | 生成物を作り直さない・コピーを直さないと落ちる |

詳細設計は [issue-4516-design-detail.md](./issue-4516-design-detail.md) にある（新設する関数 4・シグネチャを変える関数 2 のため別ファイルにした）。

## 設計の結果

| 課題 | 扱い | 統合先 | 触るファイル |
| --- | --- | --- | --- |
| #4516 | 実装する | — | `plugins/ndf/src/ndf/domains/project/measured.py`、`plugins/ndf/src/ndf/domains/project/write.py`、`plugins/ndf/src/ndf/domains/project/check.py`、`plugins/ndf/src/ndf/domains/project/cli.py`、`plugins/ndf/contracts/schemas/ndf-settings/project.schema.json`、`plugins/ndf/skills/development-workflow/schemas/project.schema.json`、`plugins/ndf/contracts/schemas/internal/project-measured.schema.json`、`plugins/ndf/contracts/domains/ndf-files.json`、`plugins/ndf/contracts/domains/locations.json`、`plugins/ndf/docs/contracts/ndf-settings.md`、`plugins/ndf/docs/contracts/files.md`、`plugins/ndf/skills/development-workflow/references/project-analysis.md`、`plugins/ndf/tests/domains/project/test_project_cli.py`、`plugins/ndf/tests/domains/project/test_project_measured.py` |

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | 追跡しないファイルの外し方（`.gitignore` の優先順位と、共有するパターンと個人のパターンの使い分け）に git の仕様がある |
| 主要な製品 | 調べる | 実行で変わるファイルをリポジトリの中に書くツールが、どう追跡から外しているかを比べる |
| ライブラリとサービス | 調べない | JSON を読み書きするだけで、既存の `ndf.domains.records.jsonfile`・`ndf.contracts` で足りる |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | gitignore | 下の階層の `.gitignore` のパターンが、上の階層のものに優先する（"patterns in the higher level files being overridden by those in lower level files down to the directory containing the file"）。共有するパターンは `.gitignore` に置き、clone で配る | 解析の記録のディレクトリに置いた `.gitignore` は、根の `.gitignore` の外さない行より優先する（受け入れ条件 6）。手元で `!.ndf/measured/` を根に書いても外れたままなことを確かめた | https://git-scm.com/docs/gitignore （2026-11-25）・`git check-ignore -v`（git 2.53.0、2026-11-25） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | pytest（キャッシュ `.pytest_cache/`） | キャッシュのディレクトリを作るとき、中に `# Created by pytest automatically.` と `*` の `.gitignore` と `CACHEDIR.TAG` を置き、利用者の `.gitignore` を書き換えない | 決定 1 の形（ディレクトリの中に `*` の `.gitignore` を置く） | `.venv/lib/python3.14/site-packages/_pytest/cacheprovider.py` の 49 行目（pytest 9.2.0、2026-11-25） |
| PRD2 | ruff（キャッシュ `.ruff_cache/`） | 同じく、中に `# Automatically created by ruff.` と `*` の `.gitignore` を置く | 決定 1 の形 | `/work/ai-plugins/.ruff_cache/.gitignore`（2026-11-25） |

調べた量: 候補 3 件・取得の試み 4 回。
