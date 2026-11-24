# #4337 の詳細設計: 起動の設定が無いリポジトリでも、設計とドキュメント再構成が流れる

設計書 [issue-4337-design.md](./issue-4337-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ以上あり、既存のクラス `TemplateValuesMissing` の引数も変えるので、設計書とは別のファイルにした。文中の「決定 N」「I N」「E N」は設計書のものを指す。「条件 N」は要求仕様（[issue-4337-requirements.md](./issue-4337-requirements.md)）のものを指す。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージとモジュールの構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/
│   ├── cli.py                         COMMANDS に document-rebuild を足す
│   ├── core/
│   │   └── issue_docs.py *            設計書の名前の正規表現・名前に含む課題の番号・実装計画のパスの書式
│   ├── progress/
│   │   ├── refactor_values.py         実装計画のパスの書式を core.issue_docs から読む
│   │   ├── invoke.py                  rebuild を derived_locally より先に決める。欠けの行に 3 つのキーを足す
│   │   └── rebuild_values.py *        設定が無いときの既定のコマンドを組む
│   ├── domains/
│   │   ├── loop/design_tests.py       正規表現を core.issue_docs から読む
│   │   └── docrebuild/ *
│   │       ├── __init__.py *          パッケージの説明だけ
│   │       ├── cli.py *               ndf document-rebuild run の引数と入口
│   │       ├── drive.py *             起点・差分・worker の起動・結果の確かめ
│   │       └── select_decide.py *     対象の文書を選ぶ純粋な処理
│   └── checks/children.json           新しいモジュールの持ち主を足す
├── contracts/
│   ├── worker/document-rebuild.md *   組み直す worker への起動指示
│   ├── workflow/stages/document-rebuild.json   inputs に mode を足す
│   ├── templates/params.json          rebuild の説明に既定を書き足す
│   └── schemas/io/document-rebuild.{args（生成）,result *}.schema.json
├── docs/contracts/commands.md         生成
├── skills/development-workflow/references/agent-layers.md   読み手役の例外
└── tests/
    ├── progress/test_invoke_rebuild.py *、test_sprint_templates.py
    └── domains/docrebuild/test_docrebuild_select.py *、test_docrebuild_run.py *
```

依存の向き: 層の向き（下から `contracts`・`adapters`・`core`・`progress`・`domains`）だけに従う。依存は次の 2 本で、互いを読まない。

- `progress.invoke` → `progress.rebuild_values` → `core.branch_strategy`・`adapters.git`・`core.route`
- `domains.docrebuild.cli` → `drive` → `select_decide` → `core.issue_docs`
- `progress.refactor_values` → `core.issue_docs`（実装計画のパスの書式だけ）

`progress.rebuild_values` は `domains.docrebuild` を読まず、コマンドの語の並びだけを組む（設計書の「コンテキスト」の上流と下流）。`domains.docrebuild` は `progress` を読まない（設計書の「コンテキスト」）。`domains.loop.design_tests` は `core.issue_docs` を読むだけで、`domains.docrebuild` とはつながらない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/issue_docs.py` | 新設 | 設計書のパスの形・名前に含む課題の番号・実装計画のパスの書式を 1 か所で持つ | `re` | モジュール `ndf.core.issue_docs` | `DESIGN_FILE`・`PLAN_FILE`・`design_issues`（すべて新設） |
| `src/ndf/progress/refactor_values.py` | 変更 | 実装計画のパスの書式を `core.issue_docs` から読む | `ndf.core.issue_docs` | モジュール | 定数 `PLAN_FILE` を消し、55 行目の参照を `issue_docs.PLAN_FILE` に替える |
| `src/ndf/domains/loop/design_tests.py` | 変更 | 正規表現を `core.issue_docs` から読む | `ndf.core.issue_docs` | モジュール | `_designs`（中身だけ変える） |
| `src/ndf/progress/rebuild_values.py` | 新設 | 設定が無いときの `rebuild`・`base`・`remote` を組み、決められなければ理由を返す | `ndf.core.route`・`ndf.core.branch_strategy`・`ndf.adapters.git`・`ndf.identity` | モジュール `ndf.progress.rebuild_values` | `Undecided`・`Filled`・`kinds_for`・`launch`・`fill`（すべて新設） |
| `src/ndf/progress/invoke.py` | 変更 | `rebuild` を、作業場所から求める差し込み（`route.derived_locally`。`base`・`remote` など）より先に決める。値が欠けたときの `items` の `rebuild` の行に 3 つのキーを載せる | `ndf.progress.rebuild_values` | `TemplateValuesMissing`、モジュール | `TemplateValuesMissing.__init__`・`TemplateValuesMissing.items`（変更）、`template_values`（変更）、定数 `PROJECT_KEYS`（`rebuild` の道を `rebuild_values.SETTING_PATH` から読む） |
| `src/ndf/domains/docrebuild/__init__.py` | 新設 | パッケージの説明だけを持つ | — | — | — |
| `src/ndf/domains/docrebuild/select_decide.py` | 新設 | 差分のパスと課題の番号から候補を挙げ、通常ファイルと実装計画の頭で対象を決める | `ndf.core.issue_docs` | モジュール | `Target`・`Selection`・`Inspection`・`kind_of`・`plan_paths`・`omits_design`・`candidates`・`from_diff`・`fresh`・`select`（すべて新設） |
| `src/ndf/domains/docrebuild/drive.py` | 新設 | 起点を決め、差分を読み、worker を 1 回起動し、結果を確かめて結果 JSON を組む | `ndf.adapters.git`・`ndf.contracts.result`・`ndf.identity`・`select_decide` | モジュール | `RunArgs`・`Precondition`・`BadReport`・`start_point`・`changed_paths`・`differs_from_start`・`contained`・`inspect`・`prompt_text`・`launch_worker`・`added_items`・`reader_gaps`・`reader_items`・`drive`（すべて新設） |
| `src/ndf/domains/docrebuild/cli.py` | 新設 | `ndf document-rebuild run` の引数を定義し、`drive` を呼び、`--out` と標準出力へ同じ結果を書く | `ndf.cli`・`ndf.identity`・`ndf.contracts.result`・`ndf.contracts.causes`・`drive` | モジュール | `_parser`・`adopt`・`main`（新設） |
| `src/ndf/cli.py` | 変更 | `COMMANDS` に `"document-rebuild": "ndf.domains.docrebuild.cli"` を足す | — | `COMMANDS` | — |
| `src/ndf/checks/children.json` | 変更 | 新しいモジュールの持ち主を足す（設計書の「設計の結果」の段落） | — | — | — |
| `contracts/worker/document-rebuild.md` | 新設 | 組み直す worker への起動指示のテンプレート | — | — | — |
| `contracts/workflow/stages/document-rebuild.json` | 変更 | `metadata.ndf.inputs` に `mode` を足す | — | — | — |
| `contracts/templates/params.json` | 変更 | `rebuild` の説明に、工程では既定のコマンドを組むことを書き足す | — | — | — |
| `contracts/schemas/io/document-rebuild.args.schema.json` | 新設（生成） | 引数のスキーマ。`checks/spec_render.py` が `cli._parser` から作る | — | — | — |
| `contracts/schemas/io/document-rebuild.result.schema.json` | 新設 | 結果 JSON の `items`・`metrics` の形 | — | — | — |
| `docs/contracts/commands.md` | 変更（生成） | コマンドの一覧に `document-rebuild` が加わる | — | — | — |
| `skills/development-workflow/references/agent-layers.md` | 変更 | 読み手役を立ててよい worker の例外を足す | — | — | — |
| `tests/progress/test_invoke_rebuild.py` | 新設 | 準備の既定・欠けの行・`base` と `remote` の一致を見る | — | — | — |
| `tests/progress/test_sprint_templates.py` | 変更 | 設定の無いリポジトリでプランに既定のコマンドが入ることを足す | — | — | — |
| `tests/domains/docrebuild/test_docrebuild_select.py` | 新設 | 選び方の純粋な処理を見る | — | — | — |
| `tests/domains/docrebuild/test_docrebuild_run.py` | 新設 | 起点・差分・worker の終わり方・記録の確かめを、git と worker を偽物に替えて見る | — | — | — |

地図のパスは、設計書の「設計の結果」の触るファイルと一致する（`__init__.py` と生成の 2 つを含む）。

## 課題の文書の名前（`ndf.core.issue_docs`）

満たす決定: 決定 2、受け入れ条件 3・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `DESIGN_FILE` | `src/ndf/core/issue_docs.py` | 新設（`design_tests.py` の 30 行目から移す） | `re.Pattern[str]`、値は `^issues/issue-(\d+(?:-\d+)*)-design[^/]*\.md$` | 設計書のパスの形を 1 か所で持つ |
| `PLAN_FILE` | `src/ndf/core/issue_docs.py` | 新設（`progress/refactor_values.py` の 20 行目から移す） | `str`、値は `"issues/issue-{number}-plan.md"` | 実装計画のパスの書式を 1 か所で持つ。リファクタリングの既定とドキュメント再構成の両方がここを読む |
| `design_issues` | `src/ndf/core/issue_docs.py` | 新設 | `(path: str) -> frozenset[int]` | パスが設計書の形なら名前の番号の並びを整数の集合で、違えば空集合を返す |
| `_designs` | `src/ndf/domains/loop/design_tests.py` | 変更（シグネチャは変えない） | 今のまま | 内包の条件を `want & issue_docs.design_issues(r)` に替え、モジュールの `DESIGN_FILE` を消す |
| `refactor_values` の実装計画の読み取り | `src/ndf/progress/refactor_values.py` | 変更（シグネチャは変えない） | 今のまま | `PLAN_FILE` を消し、`issue_docs.PLAN_FILE.format(number=number)` を読む |

`domains/materials/collect.py` の 25 行目にも同じ名前の正規表現があるが、番号 1 つだけを受ける別の形で、設計書の触るファイルに含まないので変えない。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `design_issues` | `issues/issue-4337-design.md` → `{4337}`、`issues/issue-7-8-design-detail.md` → `{7, 8}` | 課題 433 の照合で `issue-4337-design.md` が当たらない（集合で比べる） | `issues/sub/issue-1-design.md`・`issues/issue-1-requirements.md`・`docs/issue-1-design.md` → 空集合 |

`_designs` は既存の `ndf loop design-tests` のテストが、`refactor_values` は既存のリファクタリングの既定のテストが、今のまま通ることで見る。

## 既定の組み立て（`ndf.progress.rebuild_values`）

満たす決定: 決定 1・3・7、受け入れ条件 1・2・5・14・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Undecided` | `src/ndf/progress/rebuild_values.py` | 新設 | `@dataclass(frozen=True) Undecided(reason: str)`、メソッド `fields() -> dict[str, str]` | 既定を決められなかった理由を持ち、欠けの行へ足す `reason`・`setting`・`shape` の 3 つを返す |
| `Filled` | 同上 | 新設 | `@dataclass(frozen=True) Filled(values: dict[str, route.Value], undecided: Optional[Undecided] = None)` | 足す差し込みの値か、決められなかった理由のどちらかを持つ |
| `kinds_for` | 同上 | 新設 | `(template_name: str, mode: Optional[str]) -> tuple[str, ...]` | テンプレートの名前とモードから `--kind` の並びを返す。表に無い名前は空 |
| `launch` | 同上 | 新設 | `(base: str, remote: str, issues: Sequence[str], kinds: Sequence[str]) -> list[str]` | 既定のコマンドの語の並びを組む |
| `fill` | 同上 | 新設 | `(t: route.Template, values: Mapping[str, route.Value], issues: Sequence[str], mode: Optional[str], cwd: Path, *, project: Mapping[str, Any]) -> Filled` | 設定のコマンドが無いときだけ、`rebuild` と、テンプレートが使いまだ値の無い `base`・`remote` を決める |

- `launch`
  - 事後条件: `[ndf, "document-rebuild", "run", "--base", base, "--remote", remote, ("--issue", n)…, ("--kind", k)…, "--state-dir", "{state_dir}"]`。先頭は `identity.FIXED_WORDS["entrypoint"]`。`{state_dir}` は文字列のまま残し、プランの実行が絶対パスへ置き換える（設計書の引数の表）。`--out` はテンプレートが足す
- `fill`
  - 事前条件: `values` は `invoke._fill_from_sources` の後の値で、設定 `workflow.launch.rebuild` があれば `rebuild` に入っている。`issues` は工程の入力の課題（`invoke._issue_numbers`）
  - 事後条件: テンプレートが `rebuild` を使わない・`values` に `rebuild` がある・`kinds_for` が空、のどれかなら `Filled({})`。それ以外は `Filled({"rebuild": …, 足した base, 足した remote})` か `Filled({}, Undecided(...))`。返した `base` は `launch` の `--base` と、`remote` は `--remote` と同じ値である
  - 副作用: `values` に `remote` が無いときだけ `Git(cwd).remote()` を 1 回呼ぶ（中で `git worktree list --porcelain -z` を 1 回打つ）。ファイルは読まない（設定は呼び手が読んだ `project` を使う）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `fill` | マージ先を決められない | `branch_strategy` が無い・旧いキー・スキーマに合わない | `branch_strategy.of_project(project).pr_target("sprint")` の `StrategyError` | 呼び出し元へ返す（`Undecided(exc.reason)`） | `invoke` が `TemplateValuesMissing` にし、`ndf progress next` が `stopped`（`precondition`）。`items` の `rebuild` の行に 3 つのキー | 書くものが無い。設定を直して打ち直せる |
| `fill` | リモートの名前を決められない | `.ndf/worktree.json` の `remote` の値の誤り（`RemoteError`）・`git worktree list` が空（`GitError`） | `Git(cwd).remote()` | 呼び出し元へ返す（`Undecided(str(exc))`） | 同上 | 同上 |
| `fill` | 工程の入力に課題が無い | 工程の入力の形の誤り（工程の入力のスキーマが先に落とすので、通常は起きない） | `issues` が空 | 呼び出し元へ返す（`Undecided("工程の入力に課題が無い")`） | 同上 | 同上 |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `kinds_for` | `design`、モードは問わない | `("design",)` |
| `kinds_for` | `document-rebuild`、`documentation` 以外（`None` を含む） | `("design", "plan")` |
| `kinds_for` | `document-rebuild`、`documentation` | `("design", "plan", "manuscript")` |
| `kinds_for` | ほかの名前 | `()` |
| `fill` | `base` が `values` にある | その値を使い、`branch_strategy` を読まない。返す値に `base` を入れない |
| `fill` | `base` が無い | `of_project` で 1 回決める。テンプレートが `base` を使うなら返す値に入れる（`design` は使い、`document-rebuild` は使わない） |
| `fill` | `remote` が `values` にある | その値を使う。返す値に入れない |
| `fill` | `remote` が無い | `Git(cwd).remote()` で 1 回求め、テンプレートが `remote` を使うなら返す値に入れる（両方のテンプレートが使う） |

`base` を先に決め、失敗すれば `remote` を求めずに返す（子プロセスを打たずに止まる）。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KINDS` | 定数（テンプレートの名前 → モード → 種類の並び。`None` のキーが「ほかのモード」） | `src/ndf/progress/rebuild_values.py` | 上の判定表の 3 行 |
| `SETTING_PATH` | 定数 | 同上 | `("workflow", "launch", "rebuild")`。`invoke.PROJECT_KEYS["rebuild"]` はこれを読む |
| `SETTING` | 定数 | 同上 | `".".join(SETTING_PATH)`、つまり `"workflow.launch.rebuild"` |
| `SHAPE` | 定数 | 同上 | `"コマンドの語の並び（空でない文字列を 1 つ以上持つ配列）"` |

`invoke` は `rebuild_values` を読むので、`rebuild_values` から `invoke.PROJECT_KEYS` を読むと循環する。そこで設定のキーの道は `rebuild_values.SETTING_PATH` だけに置き、`invoke.PROJECT_KEYS` と `SETTING` の両方がそこから作る。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `fill` のマージ先 | `route.default_base(repo)` | `core/route/__init__.py` の 530 行目を読んだ | 使えない。ファイルを読み直し、`RouteError` の文に理由を埋めてしまう。読み込み済みの `project` を渡す `branch_strategy.of_project` を使う（`refactor_values._default_launch` と同じ） |
| `fill` のリモート | `route._remote_of_repo`・`Git.remote()` | 同ファイルの 566 行目、`adapters/git.py` の 361 行目を読んだ | `Git.remote()` を使う。`_remote_of_repo` は非公開で、`RouteError` に包んで理由の型を失う |
| `launch` | `route.review_launch`・`route.refactor_launch` | `grep -n "def .*_launch" core/route/__init__.py` | 使えない（コマンドが違う）。語の先頭を `identity.FIXED_WORDS["entrypoint"]` で取る形だけをそろえる |
| `Undecided` | `route.RouteError` | 同ファイルの 131 行目 | 使えない。例外にすると `invoke` の側で文から設定のキーを切り出すことになる（決定 7 の採らなかった案） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `kinds_for` | 判定表の 3 行 | `mode` が `None` | 表に無い名前で空 |
| `launch` | 課題 2 つ・種類 2 つで `--issue`・`--kind` が並ぶ | `{state_dir}` が置き換わらずに残る | — |
| `fill` | 設定の無い `design` で `rebuild`・`base`・`remote` が返り、`--base` と `base`、`--remote` と `remote` が同じ値。ベースブランチが `trunk`、リモートが `up` でも同じ（実装に `origin`・`main` を書かない） | 設定 `workflow.launch.rebuild` があれば `Filled({})`。`values` に `base`・`remote` があれば読まない（`Git.remote` を呼ばない） | `branch_strategy` が無い → `Undecided` で `Git.remote` を呼ばない。`RemoteError` → `Undecided` |

## 差し込みの値を集める処理（`ndf.progress.invoke`）

満たす決定: 決定 7、受け入れ条件 1・2・14・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `TemplateValuesMissing.__init__` | `src/ndf/progress/invoke.py` | 変更 | `(template_name: str, missing: Mapping[str, str], details: Optional[Mapping[str, Mapping[str, str]]] = None) -> None` | 欠けの名前ごとに、出所に加えて載せるキー（`details`）を持つ |
| `TemplateValuesMissing.items` | 同上 | 変更 | `-> list[dict[str, str]]`（今のまま） | 各行を `{"key", "source", **details[key]}` にする。`details` に無い名前は今のまま 2 つのキー |
| `template_values` | 同上 | 変更（シグネチャは変えない） | 今のまま | `_fill_from_sources` の直後、`route.derived_locally` の前に `rebuild_values.fill` を呼ぶ。`undecided` があれば `TemplateValuesMissing(t.name, {"rebuild": FROM_PROJECT}, {"rebuild": undecided.fields()})` を上げ、無ければ返した値を `values` に足す |

- `template_values`
  - 事後条件: `derived_locally` は値のある `base`・`remote` を求め直さない（`_omitted` が偽）。そのため設計のプランの `base` と `rebuild` の `--base` は同じ値になる。`branch_strategy` の読み取りと `Git.remote()` はそれぞれ 1 回になる（条件 14 の行の確かめ）
  - 副作用: 今のものに加えて無し（`Git.remote()` の 1 回は `derived_locally` から移るだけ）

`rebuild_values.fill` へ渡す `mode` は `run.inputs.get("mode")` である。工程「設計」の定義は既に `mode` を持ち、工程「ドキュメント再構成」の定義にはこの課題で足す（下の「契約とテンプレート」）。`run.inputs` の `mode` はテンプレートの `params` に無いので、`_fill_from_sources` が `values` へ入れず、`values.json` にも入らない。

`invoke.py` は今 982 行である。足すのは `template_values` の 4 行ほどと `TemplateValuesMissing` の数行で、ロジックは `rebuild_values` に置き、1,000 行を超えない。超えるときは `TemplateValuesMissing` の `details` の組み立てを `rebuild_values` 側の `Undecided.fields()` に寄せて行数を減らす。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `template_values` | `rebuild` の既定を決められない | `rebuild_values.fill` の `Undecided` | `template_values` の `fill` の直後 | 止める（`TemplateValuesMissing`） | `ndf progress next` の `stopped`（`precondition`）。`items` の `rebuild` の行が `key`・`source`・`reason`・`setting`・`shape` を持つ。工程の記録は書かない（今の `TemplateValuesMissing` の扱いと同じ） | 書くものが無い。設定を直して打ち直せる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `TemplateValuesMissing.items` | `details` の無い行は今のまま 2 キー | `details` のある行だけ 5 キー | — |
| `template_values` | 設定の無い、モード `standard` のスプリントの 2 つの工程で、プランの組み直すステップに `ndf document-rebuild run` と `--out` が入る（`test_sprint_templates.py`） | 設定がある 2 つの工程で設定のコマンドが入る（今のテストを残す） | `branch_strategy` の無いリポジトリで、2 つの工程とも 5 キーの行が返り、工程の記録が無い |

## 対象の選択（`ndf.domains.docrebuild.select_decide`）

満たす決定: 決定 2・4・9、受け入れ条件 3・6・7・8・9、I1

ファイルも git も読まない。通常ファイルかどうかと実装計画の本文は、`drive.inspect` が読んで渡す。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Target` | `src/ndf/domains/docrebuild/select_decide.py` | 新設 | `@dataclass(frozen=True) Target(path: str, kind: str)` | 対象の文書のパスと種類（`design`・`plan`・`manuscript`）を持つ |
| `Selection` | 同上 | 新設 | `@dataclass(frozen=True) Selection(targets: tuple[Target, ...], skipped: tuple[str, ...])` | 対象と、外したパスの説明（`warnings` へ載せる文）を持つ |
| `Inspection` | 同上 | 新設 | `@dataclass(frozen=True) Inspection(regular: frozenset[str], present: frozenset[str], excluded: Mapping[str, str], plans: Mapping[str, str], unreadable: Mapping[str, str])` | `drive.inspect` が作業場所で確かめた結果を持つ。`excluded` は通常ファイルでない候補と `drive.contained` の理由、`unreadable` は通常ファイルだが本文を読めなかった実装計画と読めなかった理由 |
| `kind_of` | 同上 | 新設 | `(path: str, issues: Collection[int], kinds: Collection[str]) -> Optional[str]` | 差分のパス 1 つの種類を返す。`design` は `issue_docs.design_issues(path) & issues` が空でないとき、`manuscript` は `issues/` の外の `.md` のとき。`kinds` に無い種類と、どれにも当たらないパスは `None` |
| `plan_paths` | 同上 | 新設 | `(issues: Sequence[int]) -> tuple[str, ...]` | 課題ごとの `issues/issue-<番号>-plan.md` を、課題の順に重なりなく返す |
| `omits_design` | 同上 | 新設 | `(text: str) -> bool` | 最初の `##` の見出しより前に、行頭が `省いた文書: 設計書` でその直後が `（` か行末の行があるかを返す |
| `candidates` | 同上 | 新設 | `(changed: Sequence[str], issues: Sequence[int], kinds: Sequence[str]) -> tuple[Target, ...]` | 差分から選んだ `design`・`manuscript` と、`plan` が `kinds` にあれば `plan_paths` を、通常ファイルかを確かめる前の候補として返す |
| `from_diff` | 同上 | 新設 | `(found: Sequence[Target]) -> bool` | 差分から選んだ候補（`design`・`manuscript`）が 1 件以上あるかを返す |
| `fresh` | 同上 | 新設 | `(found: Sequence[Target], differs: Collection[str]) -> tuple[Target, ...]` | 差分から選んだ候補のうち、起点の参照と内容が同じもの（`differs` に無いもの）を外す。`plan` はそのまま残す |
| `select` | 同上 | 新設 | `(found: Sequence[Target], seen: Inspection) -> Selection` | 通常ファイルでない候補と本文を読めなかった実装計画を理由とともに外し、`plan` は本文が `omits_design` を満たすものだけを残す |

- `candidates`
  - 事後条件: 並びは差分の順（`design`・`manuscript`）の後に `plan_paths` の順。同じパスは 1 回だけ。差分にある実装計画は `kind_of` が `None` を返すので、`plan` は差分によらず番号からだけ入る（決定 9）
- `select`
  - 事前条件（`seen` の各値）
    - `regular`: `drive.contained` が作業場所の中の通常ファイルと確かめた候補のパス
    - `present`: 作業場所に何かがあるパス（親ディレクトリにリンクがあるものを含む）
    - `excluded`: `regular` に無い候補のすべてと、`drive.contained` が返した理由
    - `plans`: 通常ファイルの `plan` の候補のうち、本文を読めたものの本文
    - `unreadable`: 通常ファイルの `plan` の候補のうち、本文を読めなかったもののパスと理由
    - `regular` にある `plan` の候補は、`plans` と `unreadable` のどちらか一方にだけある
  - 事後条件: `targets` は通常ファイルだけ（I1）。`skipped` は次の 3 つ。各要素は `<パス>: <理由>` の文で、`drive` がそのまま `warnings` に載せる
    - 差分から来た候補で通常ファイルでないもの（リンク・途中にリンクのあるもの・サブモジュール・作業場所に無いもの）。理由は `excluded` の値
    - `plan` の候補で `present` にあるが通常ファイルでないもの。理由は `excluded` の値
    - `plan` の候補で `unreadable` にあるもの（理由は `unreadable` の値）。`omits_design` を呼ばずに外す
  - `plan` の候補で作業場所に無いもの（実装計画を書かない課題）と、省いた行を持たないものは `skipped` に入れない（選ぶ条件に合わないだけで、外したのではない）

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `omits_design` | `省いた文書: 設計書（課題 1・…）` が最初の `##` より前 | 真 |
| `omits_design` | `省いた文書: 設計書` だけの行が最初の `##` より前 | 真 |
| `omits_design` | 同じ行が最初の `##` より後 | 偽 |
| `omits_design` | `省いた文書: 実行計画（…）`・`省いた文書: 詳細設計書（…）` | 偽 |
| `omits_design` | `省いた文書: 設計書類` のように、直後が `（` でも行末でもない | 偽 |
| `kind_of` | `issues/issue-<N>-requirements.md` | `None`（要求仕様のコピーは選ばない。前提 2） |
| `kind_of` | `issues/issue-<N>-plan.md` | `None`（差分からは選ばない） |
| `kind_of` | `docs/a.md`、`manuscript` が `kinds` に無い | `None`（条件 8） |

- 繰り返しの終わる条件: `omits_design` は最初の `##` の行で読み終える

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KINDS` | 定数 | `src/ndf/domains/docrebuild/select_decide.py` | `("design", "plan", "manuscript")`。`cli._parser` の `--kind` の `choices` もこれを読む |
| `OMITTED` | 定数 | 同上 | `"省いた文書: 設計書"` |
| `ISSUES_DIR` | 定数 | 同上 | `"issues/"` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `kind_of` | 課題の設計書・`documentation` の原稿 | 課題 433 と `issue-4337-design.md`、`issue-7-8-design.md` と課題 8 | 別の課題の設計書・要求仕様のコピー・実装計画 → `None` |
| `omits_design` | 判定表の真の 2 行 | `##` の直前の行・本文の 1 行目 | 判定表の偽の 3 行 |
| `candidates` | 工程「設計」で設計書だけ（条件 6）。モード `documentation` で原稿、モード `standard` で `docs/` を含まない（条件 7・8） | 差分が空でも `plan_paths` が入る（決定 9） | — |
| `fresh` | `differs` にある設計書と原稿が残る | `plan` は `differs` に無くても残る | 起点と内容が同じ設計書と原稿 → 外れる |
| `select` | 省いた行を持つ実装計画が残る（条件 9） | 実装計画が作業場所に無い → `skipped` に入らない。`unreadable` にある実装計画 → 選ばれず、`skipped` に理由付きで出る | 差分の `.md` のリンク・リンクの実装計画・`issues/` がリンクの設計書と実装計画が外れ `skipped` に出る（I1） |

## 組み直しの駆動（`ndf.domains.docrebuild.drive`）

満たす決定: 決定 1・2・3・5・6、受け入れ条件 4・5・10・11・12・13・16、I2〜I7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `RunArgs` | `src/ndf/domains/docrebuild/drive.py` | 新設 | `@dataclass(frozen=True) RunArgs(base: str, remote: str, issues: tuple[int, ...], kinds: tuple[str, ...], state_dir: Path, cwd: Path)` | 1 回の実行の引数を持つ |
| `Precondition` | 同上 | 新設 | `class Precondition(Exception)`、属性 `detail: str` | 起点か差分を決められないことを表す |
| `BadReport` | 同上 | 新設 | `class BadReport(Exception)`、属性 `detail: str` | worker の足すべき内容（結果ファイルの `items` に書く、対象の外で直すべきこと）の形の誤りを表す |
| `start_point` | 同上 | 新設 | `(g: git.Git, base: str, remote: str) -> str` | 設計書の「処理の流れ」の表どおりに起点の参照を決め、その SHA を返す |
| `changed_paths` | 同上 | 新設 | `(g: git.Git, start: str, head: str) -> list[str]` | 3 ドット差分の消していないパスを返す |
| `differs_from_start` | 同上 | 新設 | `(g: git.Git, start: str, head: str) -> set[str]` | 起点と `HEAD` で内容の違うパスを、2 ドット差分で返す |
| `contained` | 同上 | 新設 | `(root: Path, rel: str) -> Optional[str]` | パスが作業場所の中の通常ファイルなら `None`、違えば外す理由（`作業場所に無い`・`親ディレクトリにリンクがある`・`通常ファイルでない`）を返す |
| `inspect` | 同上 | 新設 | `(cwd: Path, found: Sequence[Target]) -> select_decide.Inspection` | 候補を `contained` で確かめ、通常ファイル・作業場所にあるもの・通常ファイルの実装計画の本文・本文を読めなかった実装計画の理由を返す |
| `prompt_text` | 同上 | 新設 | `(targets: Sequence[Target], requirements: Sequence[str], skill: Path) -> str` | 起動指示のテンプレートへ対象・要求仕様のコピー・手順書のパスを差し込む |
| `launch_worker` | 同上 | 新設 | `(args: RunArgs, prompt: str, runner: Runner) -> tuple[dict[str, Any], Path]` | 起動指示を保存先へ書き、`ndf worker run` を 1 回打ち、標準出力の結果 JSON と worker の結果ファイルのパスを返す |
| `added_items` | 同上 | 新設 | `(raw: Any, targets: Sequence[Target]) -> list[dict[str, str]]` | worker の `items` を確かめ、`id` を `add-<番号>` に振り直す |
| `reader_gaps` | 同上 | 新設 | `(raw: Any, targets: Sequence[Target]) -> list[dict[str, str]]` | 確かめの記録（`metrics.readers`。形は設計書の「確かめの記録」の表）の欠けを、対象の文書ごとの `gaps` の要素にする（I4） |
| `reader_items` | 同上 | 新設 | `(readers: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, str]], list[str]]` | `fail` の文書を、パスの順に `reader-<番号>` の項目と `warnings` の文にする（I5）。渡すのは、`reader_gaps` を通った対象の文書の記録を 1 件ずつだけ |
| `drive` | 同上 | 新設 | `(args: RunArgs, *, git_of: Callable[[Path], git.Git] = git.Git, runner: Runner = _run) -> dict[str, Any]` | 起点 → 差分 → 選択 → 起動 → 確かめの順に進め、結果 JSON を返す |

- `start_point`
  - 事後条件: 返すのは `refs/remotes/<remote>/<base>` か `refs/heads/<base>` を解いた SHA。参照は `Git.attempt("rev-parse", "-q", "--verify", "--end-of-options", f"{ref}^{{commit}}")` で読み、終了コード 0 なら SHA、1 なら読めない、それ以外は git の失敗とする（設計書の実測）。読む順と切り替えの条件は設計書の「処理の流れ」の表のとおり
  - 副作用: `git rev-parse` を 1 回（リモート追跡ブランチが読めないときは、`git remote` と手元の参照の `git rev-parse` を足して 3 回）
- `changed_paths`
  - 事前条件: `start`・`head` は `rev_parse` で解いた SHA
  - 事後条件: `git diff --name-only -z --diff-filter=d <start>...<head>` の出力を NUL で分けた並び（空要素を除く）。`git merge-base` は打たない
  - 副作用: `git diff` を 1 回
- `differs_from_start`
  - 事後条件: 2 ドット差分 `Git.diff_between(start, head, "--name-only", "-z")` の出力を、NUL で分けた集合（空要素を除く）。起点と内容が同じファイルは入らない
  - 副作用: `git diff` を 1 回。`drive` は `select_decide.from_diff` が真のときだけ呼ぶ
- `contained`
  - 事後条件: 次の 2 つがどちらも成り立つときだけ `None` を返す。`root` は `root.resolve(strict=True)` で解いたものを使う
    - `rel` の親ディレクトリを `resolve(strict=True)` で解いた結果が、`root` に `rel` の親をつないだパスと同じである（途中のディレクトリにリンクが無く、作業場所の外を指さない）。違えば `親ディレクトリにリンクがある`
    - 末尾を `os.lstat` した結果が通常ファイルである（末尾がリンク・ディレクトリ・サブモジュールでない）。違えば `通常ファイルでない`
  - 親ディレクトリを解けないときと、末尾の `lstat` が `FileNotFoundError` のときは `作業場所に無い` を返す
  - 副作用: 親ディレクトリの解決と、末尾の `os.lstat` を 1 回ずつ。git を打たない
- `inspect`
  - 事後条件: `contained` が `None` を返した候補だけを通常ファイルとする。`作業場所に無い` 以外の理由の候補は、作業場所にあるもの（`present`）に入れる
  - 副作用: 候補ごとに `contained` を 1 回。通常ファイルの `plan` だけ本文を読む（UTF-8）。読むときは `os.open` に `O_NOFOLLOW` を付け、確かめた後に末尾がリンクへ替わっていても辿らない。git を打たない
- `launch_worker`
  - 事後条件: 起動指示は `<state_dir>/document-rebuild.prompt.md`、worker の結果ファイルは `<state_dir>/document-rebuild.result.json`。argv は `[ndf, "worker", "run", "--kind", "document-rebuild", "--prompt-file", <起動指示>, "--result-file", <結果>, "--cwd", <cwd>, "--state-dir", <state_dir>]`
  - 副作用: 起動指示のファイルを書く。子プロセス `ndf worker run` を 1 回。その中で `claude -p` を通常 1 回、worker が結果ファイルを書かずに応答を終えたときは `core/worker.py` の `RESUMES`（今は 3）回まで再起動するので最大 4 回打つ。読み手役のサブエージェントは worker の会話の中で立ち、上限は設計書の「影響と危険」の Claude の起動の行にある。再起動の回数は `ndf worker run` の標準出力の `metrics.attempts` に出るので、`drive` はそれを `info` のログに出す
- `drive`
  - 事後条件: どの終わり方でも、設計書の「終わり方」の表の結果 JSON を返す。`ok` の `metrics` は `targets`（対象のパス）と `readers`（`reader_gaps` を通った、対象の文書ごとに 1 件の記録。対象の外の記録は入れない）。対象が 0 件なら `launch_worker` を呼ばない
  - 副作用: 上の関数のものだけ。コミット・`push`・GitHub への書き込みを持たない（条件 13）

`Runner` の形と既定の値は次のとおりである。テストは偽物に替える。

| 名前 | 値 |
| --- | --- |
| `Runner` | `Callable[[list[str], Path], subprocess.CompletedProcess[str]]` |
| 既定の `_run` | `subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)` を打つ |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `start_point` | `HEAD` を読めない | 作業場所が git でない・コミットが無い | `Git.rev_parse("HEAD")` の `GitError` | 止める（`Precondition`） | `stopped`（`precondition`）。`summary` に `HEAD` と git の文。終了コードは `causes.exit_code("precondition")` | 書くものが無い。打ち直せる |
| `start_point` | 起点を読めない | リモート追跡も手元も読めない | 両方の `rev-parse` が終了コード 1 | 止める（`Precondition`） | `stopped`（`precondition`）。`summary` に 2 つの参照の名前（I2。条件 11） | 同上 |
| `start_point` | 求めた名前のリモートが無い | `remote` の設定漏れ・誤記で、ほかの名前のリモートだけがある | `git remote` の一覧 | 止める（`Precondition`）。手元へ切り替えない | `stopped`（`precondition`）。`summary` に求めた名前と設定のキー `.ndf/worktree.json` の `remote` | 同上 |
| `start_point` | `git remote` を読めない | git の失敗 | `Git.run("remote")` の `GitError` | 止める（`Precondition`） | `stopped`（`precondition`）と git の文 | 同上 |
| `changed_paths` | 分岐点が無い | 履歴を共有しない | `Git.attempt` の終了コード 128 | 止める（`Precondition`） | `stopped`（`precondition`）。`summary` に起点の参照の名前と git の文 | 同上 |
| `changed_paths` | ほかの git の失敗 | 終了コード 0・128 以外 | 同上 | 止める（`Precondition`） | 同上 | 同上 |
| `differs_from_start` | git の失敗 | 終了コード 0 以外 | `Git.diff_between` の `GitError` | 止める（`Precondition`） | `stopped`（`precondition`）。`summary` に起点の参照の名前と git の文 | 同上 |
| `inspect` | 実装計画を読めない | 権限・UTF-8 でない | 読み取りの `OSError`・`UnicodeDecodeError` | 既定値で続ける。`Inspection.unreadable` にパスと理由（例外の種類と `strerror`、UTF-8 でないときは `UTF-8 で読めない`）を入れ、`plans` には入れない。`select` がその実装計画を選ばず `skipped` に入れる | `ok` の `warnings` に `<パス>: <理由>` が 1 件 | 無し |
| `launch_worker` | 起動指示を書けない | 保存先が無い・書けない | `write_text` の `OSError` | 止める | `stopped`（`precondition`）と保存先のパス | 打ち直せる |
| `launch_worker` | 標準出力に結果 JSON が無い | `ndf worker run` の異常終了 | 最後の空でない行の `json.loads` の失敗 | 止める | `stopped`（`bad_report`）。終了コードと標準エラーの末尾 | 打ち直せる（`ndf progress retry`） |
| `drive` | worker が `stopped` で終わった | 作業を残した・起動に失敗した | 標準出力の `status` | 止める。結果ファイルを読まない（I7） | `stopped`。`cause.kind` は worker の `cause.kind`、`incomplete` なら worker の `gaps` も載せる | 作業場所に途中のコミットが残る。打ち直しで対象を選び直す |
| `drive` | worker が `gate` で終わった | worker が判断を求めた | 同上 | 止める。`gate` を返さない（設計書の「worker の終わりの読み方」） | `stopped`（`work_stopped`）。`summary` に worker の `summary` | 判断の材料を足してから打ち直す |
| `drive` | worker の結果ファイルを読めない | 標準出力は `ok` だが、ファイルが消えた・JSON でない | `json.loads` の失敗・`OSError` | 止める | `stopped`（`bad_report`）とパス | 打ち直せる |
| `added_items` | 足すべき内容の形が違う | 辞書でない・`text` が空・`doc` が対象のパスでない（I3） | 要素ごとの確かめ | 止める（`BadReport`） | `stopped`（`bad_report`）。何番目の要素がどう違うか | 打ち直せる |
| `reader_gaps` | 記録が欠けた | `readers` が並びでない・対象の行が無い・`checks` の数や形が種類に合わない・`not_run` に理由が無い（I4） | 文書ごとの確かめ | 止める | `stopped`（`incomplete`）。`gaps` に文書ごとに `{"kind": "reader", "name": <パス>, "reason": <欠け>}` | 打ち直せる |

`--out` を書けないときは `cli.main` が扱う（下の「サブコマンドの入口」）。

### 処理の手順

`start_point` の判定は設計書の「処理の流れ」の表を写さずに使う。`reader_gaps` は対象の文書ごとに次を見る。

| 種類 | `verdict` | 欠けでない条件 |
| --- | --- | --- |
| `design`・`manuscript` | `not_run` | `checks` が空の並びで、`reason` が空でない文字列 |
| `plan`・`manuscript` | `self` | `checks` が空の並びで、`reason` が空でない文字列（読み手役の対象でないと判断した文書の種類） |
| `design`・`manuscript` | `pass`・`fail` | `checks` が 1 か 2。最後の `checks` の要素の `verdict` が、記録の `verdict` と同じ。`checks` が 2 なら先頭の要素の `verdict` が `fail`（2 回目は 1 回目が不合格のときだけ行う）。`checks` が 1 で記録の `verdict` が `fail` なら、記録の `reason` が要る |
| `design` | `self` | 欠け（設計書は必ず読み手役の対象） |
| `plan` | `self` 以外 | 欠け（実装計画は手順書で書いた担当が読み直す文書） |
| どれでも | ほかの値 | 欠け |

`checks` の各要素は、`verdict` が `pass` か `fail`、`restatements` が空でない文字列の並び、`fail` なら `reason` が空でない文字列であること。同じ対象の `doc` を持つ記録が 2 件以上あれば、どれが正しいか決められないので、その文書の欠け（`記録が 2 件以上ある`）にする。`readers` に対象の外の `doc` の記録があっても欠けにせず、形も確かめない。

`drive` は、`reader_gaps` が空のとき、`readers` から `doc` が対象のパスである記録だけを取り出す。取り出した並びは対象の文書ごとに 1 件で、どれも `reader_gaps` を通っている。`drive` はこの並びを `reader_items` に渡し、結果の `metrics.readers` にも同じ並びを書く。対象の外の記録は結果のどこにも書かず、件数だけを `warnings` に 1 件（`対象の外の文書の記録を N 件外した`）載せる。そのため、`items` と `warnings` には組み直した文書の不合格だけが現れ、専用の結果スキーマが `metrics.readers` の各要素に求める形（`cli.adopt` が確かめる）を、対象の外の記録が破ることもない。

`drive` の進み方:

| 段階 | 条件 | 結果 |
| --- | --- | --- |
| 起点と差分 | `Precondition` | `stopped`（`precondition`）。worker を起動しない |
| 候補 | `candidates` の後、`from_diff` が真 | `differs_from_start` を 1 回打ち、`fresh` で起点と内容が同じ設計書・原稿を外してから `inspect` へ渡す。偽なら打たない（設計書の「確かめ済みの文書を組み直さない」） |
| 選択 | 対象が 0 件 | `ok`。`summary` は「組み直す文書が無い（種類: …・起点: <参照の名前>）」、`items` は空、`metrics.targets` は空。`skipped` があれば `warnings` に載せる |
| 起動 | 標準出力が `ok` でない | 上の異常系の表の、`drive` の「worker が `stopped` で終わった」と「worker が `gate` で終わった」の 2 行 |
| 確かめ | `added_items` が `BadReport` | `stopped`（`bad_report`） |
| 確かめ | `reader_gaps` が空でない | `stopped`（`incomplete`） |
| 確かめ | どれも通った | `ok`。`items` は `added_items` の後に `reader_items` の項目。`warnings` は `skipped` と `reader_items` の文 |

確かめの順は `added_items` が先で、両方の欠けがあれば `bad_report` を返す（形の誤りは打ち直すしかなく、記録の欠けを並べても使えない）。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `WORK_KIND` | 定数 | `src/ndf/domains/docrebuild/drive.py` | `"document-rebuild"`（`ndf worker run --kind` の値と、保存先のファイル名の頭） |
| `PROMPT_TEMPLATE` | 定数 | 同上 | `contracts/worker/document-rebuild.md`（`identity.load().root` からの相対） |
| `SKILL` | 定数 | 同上 | `skills/document-rebuild/SKILL.md`（同上） |
| `REQUIREMENTS_FILE` | 定数 | 同上 | `"issues/issue-{number}-requirements.md"`。`inspect` と同じく `contained` が `None` を返したものだけを起動指示に載せる。外したものは `warnings` に理由を載せる |
| `ADD_PREFIX`・`READER_PREFIX` | 定数 | 同上 | `"add"`・`"reader"`（I6） |
| `NO_MERGE_BASE` | 定数 | 同上 | `128`（`git diff A...B` の分岐点の無いときの終了コード。設計書の実測） |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `drive` | 起点の参照の名前・候補と対象の数・worker の終わりの `status`・`claude -p` を起動した回数（`metrics.attempts`） | `logging` の `info`（`log.info(…, extra=…)`。`core/worker.py` の 717 行目と同じ形） | 起動指示の本文・文書の本文 |
| `drive` | 結果 JSON | 標準出力の最後の 1 行と `--out`（`cli.main`） | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `start_point` | `Git.rev_parse`・`Git.has_ref`・`command.base_ref` | `adapters/git.py` の 249・300 行目、`progress/command.py` の `base_ref` | `HEAD` は `rev_parse` を使う。起点の参照は `Git.attempt` で打つ。`rev_parse` は読めないときと git の失敗をどちらも `GitError` にして見分けず、`has_ref` は `git show-ref` を別に打つので子プロセスの回数（設計書の表）が増える。`base_ref` は手元を先に使う規則なので使わない（決定 3） |
| `differs_from_start` | `Git.diff_between` | `adapters/git.py` の 308 行目 | 使う。2 つの SHA の 2 ドット差分で、分岐点が無くても終了コード 128 にならないので、終了コードを見分ける必要が無い |
| `changed_paths` | `Git.diff_between`・`Git.run` | `adapters/git.py` の 308 行目 | 使えない。`diff_between` は 2 つの引数で 3 ドットを組めず、`run` は終了コード 128 を見分けない。`Git.attempt` で打って終了コードを読む |
| `launch_worker` | `progress.invoke.run_command`・`read_report`、`progress.gate.entry` の起動 | `progress/invoke.py` の 199・262 行目、`grep -rn "worker run" src/ndf` | 使えない。`domains.docrebuild` は `progress` を読まない（設計書の「コンテキスト」）。最後の行を読む処理は `contracts.result.received` を使い、その前の数行だけを書く |
| `added_items` | `crossreview.finish_decide.as_handed` | `domains/crossreview/finish_decide.py` の 267 行目 | 結果を読む側なので使わない。テストで `--out` の `items` を `as_handed` に通して読めることを見る（条件 12） |
| `prompt_text` | `string.Template`・`contracts/worker/prompt.md` | `contracts/worker/prompt.md` を読んだ | `string.Template.substitute` を使う。`prompt.md` は規則 1 で起動を禁じるので使わない（決定 5） |
| `reader_gaps` | `core.worker_outcome.gaps_of` | `core/worker_outcome.py` の 36 行目 | 使えない（worker の `gaps` の形を見る処理で、`readers` を見ない）。`gaps` の要素の形（`kind`・`name`・`reason`）だけをそろえる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `start_point` | リモート追跡ブランチが読める。ベースブランチが `trunk`、リモートが `up` でも同じ（条件 5） | 登録済みでリモート追跡が無い・リモートが 1 つも無い → 手元 | 両方読めない・ほかの名前だけ → `Precondition`（偽の git） |
| `changed_paths` | 日本語と空白を含むパス、消したファイルが出ない | 空の差分 | 終了コード 128 → `Precondition`。`merge-base` を打たない |
| `differs_from_start` | 起点と内容の違うパスだけが返る | 起点と `HEAD` が同じ内容 → 空集合 | `GitError` → `Precondition` |
| `added_items` | `id` を `add-1`… に振り直す | 同じ `id` 2 件・`reader-1` の `id`・`id` 無し → 全件が残り重ならない（I6） | `text` 無し・`doc` が対象に無い → `BadReport`（I3） |
| `reader_gaps` | `plan` の `self`・原稿の `self` と `pass`・設計書の `fail`（`checks` 2）・設計書と原稿の `not_run` | 設計書の `fail` で `checks` 1 と `reason`。対象の外の `doc` の記録が形を問わず欠けにならない | 対象の行が無い・同じ対象の行が 2 件・`restatements` が無い・`plan` に `pass` か `not_run`・設計書に `self`・`checks` 2 の先頭が `pass`・`not_run` か `self` に理由が無いか `checks` が空でない（I4） |
| `reader_items` | `fail` 1 件で `reader-1` と `warnings` 1 件（I5） | `fail` が無ければ空 | — |
| `drive` | `readers` に対象の外の `doc` の `fail` や形の違う記録があっても、`items` にも `metrics.readers` にも出ず、`warnings` に外した件数だけが出る。その結果が `cli.adopt` を通って `ok` で書かれる | — | 同じ対象の記録が 2 件 → `stopped`（`incomplete`） |
| `contained` | 作業場所の通常ファイル | 作業場所の根そのものがリンクで、その先の通常ファイル | 末尾がリンク・`issues/` がリンクで作業場所の外を指す・途中のディレクトリがリンクで作業場所の中を指す・存在しない（どれも理由を返す） |
| `drive` | squash で取り込み済みで内容の同じ設計書が渡らない（決定 2）。差分から選んだ候補が無ければ `differs_from_start` を呼ばない | — | — |
| `drive` | 条件 3・4 の作業場所で、渡る対象がこの課題の設計書だけ | 対象 0 件で `launch_worker` を呼ばない（条件 10） | worker の `stopped`・`gate` で結果ファイルを読まない。`ok` で標準出力の `items` を読まない（I7） |
| `drive` | 起動する子が `git` の読み取りと `ndf worker run` だけで、`push`・`commit` を含まない（条件 13） | — | — |

## サブコマンドの入口（`ndf.domains.docrebuild.cli`）

満たす決定: 決定 1・6、受け入れ条件 12・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_parser` | `src/ndf/domains/docrebuild/cli.py` | 新設 | `() -> argparse.ArgumentParser` | `run` の引数（`--base`・`--remote`・`--state-dir`・`--out` は必須、`--issue` は `type=int` で `action="append"`・`required=True`、`--kind` は `choices=select_decide.KINDS` で `action="append"`・`required=True`）を定義する |
| `adopt` | 同上 | 新設 | `(obj: dict[str, Any]) -> tuple[dict[str, Any], int]` | 結果を共通の結果スキーマと専用の結果スキーマで確かめ、書き出す結果と終了コードを決める |
| `main` | 同上 | 新設 | `(argv: list[str]) -> int` | 引数を読み、`drive.drive` を呼び、例外を停止理由にし、`adopt` が決めた同じ結果 JSON を `--out` と標準出力へ書く |

既存の `result.emit` は、形が違う結果を受けると代わりの `stopped`（`invalid`）を標準出力へ書き、呼び手には終了コードだけを返す。`emit` の後に元の結果を `--out` へ書くと、標準出力では止まった回の項目を、`--out` を読むレビューが引き継いでしまう。そこで、確かめと書き出す結果の決定を `adopt` に分け、`main` は `adopt` が返した 1 つの結果を両方へ書く。

- `adopt`
  - 事後条件: 次の 3 つの誤りが無ければ `(obj, result.code_for(obj))` を返す。1 つでもあれば `result.stopped(ENTRY, "invalid", <誤りの件数>)` と `causes.exit_code("invalid")` を返す
    - `result.errors(obj, result.code_for(obj))`（共通の結果スキーマと、`status` と停止理由の対応）
    - `contracts.broken_rules(obj, RESULT_SCHEMA)`（専用の結果スキーマ `io/document-rebuild.result`。`items` の `doc`・`metrics.readers` の形）
    - `result.code_for(obj)` が `None`（`stopped` に `cause` が無い）
  - 副作用: 誤りがあれば、`contracts.broken_rules` の文（結果の値を含まない）を標準エラーへ 1 行ずつ書く。`result.errors` の文は結果の値を含むので書かない
- `main`
  - 事後条件: `drive.drive` の結果（例外のときは停止理由の結果）を `adopt` に通し、返った結果を先に `--out` へ、次に `result.emit` で標準出力へ書く。`adopt` を通った結果なので `emit` は差し替えず、`--out` のファイルと標準出力の最後の 1 行は同じ JSON になる。`--out` は `core.plan.files.write_json` で一時ファイルから置き換える。終了コードは `adopt` が返したもの
  - 副作用: `--out` のファイルを書く。`cwd` は打った場所（`Path.cwd()`）

`spec_render.py` は `_parser` から引数のスキーマと `commands.md` を作る。`checks/spec_model.py` が読む形（`add_subparsers(dest="action")` の下の `run`）は `crossrefactor/cli.py` にそろえる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `main` | 引数の誤り | 必須の欠け・`--kind` の値の誤り・`--issue` が整数でない | `parse_args` の `SystemExit` | 止める | argparse の文を標準エラーへ。終了コードは `causes.exit_code("invalid")`。`--out` は書かない（読めていない） | 引数を直して打ち直す |
| `main` | 起点・差分の失敗 | `drive.Precondition` | `drive` の中で結果 JSON にする | 止める | `stopped`（`precondition`） | 打ち直せる |
| `main` | `--out` を書けない | 保存先の権限など | `write_json` の `OSError` | 止める | 標準出力に `stopped`（`precondition`）と `--out` のパス | 打ち直せる |
| `main` | 想定外の例外 | 実装の誤り | `except Exception` | 止める | `stopped`（`unknown`）を標準出力と `--out` へ（書ければ） | 打ち直せる |
| `adopt` | 結果の形が違う | `drive` の組み立ての誤り | 上の 3 つの確かめ | 止める（`stopped`（`invalid`）に差し替える） | 差し替えた `stopped`（`invalid`）を `--out` と標準出力へ。元の `items` はどちらにも書かない。誤りの文を標準エラーへ | 打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `RESULT_SCHEMA` | 定数 | `src/ndf/domains/docrebuild/cli.py` | `contracts.schema_path("io/document-rebuild.result")`（`domains/materials/collect.py` の 34 行目と同じ読み方） |
| `ENTRY` | 定数 | 同上 | `result.ENTRY` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `adopt` | 形の正しい `ok` をそのまま返す | `stopped` の結果も同じ終了コードで返す | `add-` の項目に `doc` が無い・`metrics.readers` の `verdict` が表に無い → `stopped`（`invalid`）に差し替わる |
| `main` | `--out` と標準出力の最後の 1 行が同じ JSON | — | `adopt` が差し替えたとき、`--out` にも差し替えた `stopped` が書かれ、元の `items` が残らない |

## 契約とテンプレート

満たす決定: 決定 4・5・6・8、受け入れ条件 1・2・7・12・16

関数を持たない変更をまとめる。

| ファイル | 変えること |
| --- | --- |
| `contracts/workflow/stages/document-rebuild.json` | `metadata.ndf.inputs` を `["issues", "worktree", "mode"]` にする。値を入れるコードは足さない（設計書の構成要素の表の工程の定義の行） |
| `contracts/templates/params.json` | `rebuild` の `summary` の後ろに「工程から組むときに設定（`workflow.launch.rebuild`）が無ければ、`ndf document-rebuild run` を組む」を、`review` の説明と同じ形で足す |
| `contracts/worker/document-rebuild.md` | `prompt_text` が差し込む `$targets`（対象のパスと種類の箇条）・`$requirements`・`$skill` を持つ。書くこと: 作業場所でコミットし push も Pull Request もしない・要求仕様のコピーを変えない・対象の外で直すべきことは当たる対象の `doc` を添えて結果ファイルの `items` に書く・文書ごとの確かめの記録を `metrics.readers` に書く（形は設計書の「確かめの記録」の表）・誰が確かめるかは設計書の「誰が確かめるか」の表のとおりで、`document-rebuild` の手順の「読み手役に読ませる」の「対象」の行に従う・読み手役に限りサブエージェントを、読み手役の対象の文書 1 件につき最大 2 回（1 回目が不合格のときだけ 2 回目）立ててよい・実装計画と対象に当たらない種類の原稿は自分で読み直し、`self` と文書の種類を書く・読み手役を立てられなければ `not_run` と理由を書く |
| `contracts/schemas/io/document-rebuild.result.schema.json` | `items` の要素は `id`・`text` が必須で、`add-` の要素は `doc` も必須。`metrics` は `targets`（文字列の並び）と `readers`（設計書の「確かめの記録」の表の形。`verdict` は `pass`・`fail`・`not_run`・`self`）。`refactor.result.schema.json` と同じく `result.schema.json` を土台にする |
| `skills/development-workflow/references/agent-layers.md` | 役割の表の worker の行とは別の行に、「`ndf worker run` が起動する組み直しの worker（`ndf document-rebuild run` が起動するもの）に限り、読み手役のサブエージェントを立ててよい。`ndf:worker` で起動した worker には当たらない」を足す（#4285 が足す、conductor が読み手役を立ててよいという文と別の行にする。設計書の「影響と危険」） |
| `src/ndf/checks/children.json` | `P1b` の `packages` に `ndf.progress.rebuild_values`、`M1` の `packages` に `ndf.domains.docrebuild`、`C3` の `packages` に `ndf.core.issue_docs` を足す。`needs` は変えない（`C3` は `P1b` と `M1` の両方の `needs` に既にある） |
| `src/ndf/cli.py` | `COMMANDS` に `"document-rebuild": "ndf.domains.docrebuild.cli"` を、辞書順の位置へ足す |

次のものは変えない（設計書の「設計の結果」）。

- `agents/worker.md`
- プランのテンプレート（`contracts/templates/design.json`・`document-rebuild.json`）
- `core/plan/`
- `progress/run.py`
