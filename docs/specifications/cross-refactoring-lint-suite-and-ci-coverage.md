# cross-refactoring: 項目が入れた整形・静的解析の違反を項目の検証で拾えず、push 前の検査か継続的統合で初めて落ちていた → 項目の範囲テストで落ち、宣言に無い継続的統合のジョブを `init` が知らせる

## 目的

- **違反を入れた項目は、その項目の範囲テストで失敗になる。** 整形・静的解析（`scripts/check-lint.sh` の ruff format --check・
  ruff check・shellcheck）の違反が、push 前の検査（`.githooks/pre-push`）や Pull Request の継続的統合まで残らない。
  失敗した項目は今の修正・取り消しの流れへ入る
- **継続的統合が走らせるのに宣言に無いジョブを、`init` の時点で名前つきで知らせる。** 宣言の書き漏れに、継続的統合を
  見る前に気づける。知らせるだけで起動は止めない

例: `/ndf:cross-refactoring 1700 --scope plugins/ndf/scripts` を起動する。

| 時点 | 振る舞い |
| --- | --- |
| `init` | テストの戦略を組んだ後、`.github/workflows/` のジョブを宣言と突き合わせ、宣言に無いジョブ（例: `runtime-plugin-validate.yml#skill-frontmatter-check`）を `🔎` の行で挙げる |
| 項目 `R3` のコミット | `plugins/ndf/scripts/lib/foo.py` の 1 行が 130 字になった |
| 項目の検証 | 範囲テストに `bash scripts/check-lint.sh -- plugins/ndf/scripts/lib/foo.py` が加わり、`ruff format --check` の違反で終了コード 1。項目の検証が失敗になる |

**手順と宣言の書き方は Skill が正である。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| `init` の知らせ・`ci_jobs` と `test.ci_exempt` の書き方・照合の規則 | [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md) の「継続的統合のジョブのうち宣言に無いもの」 |
| 範囲テストの組み立て（静的解析の suite の範囲） | [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md) と [`cross-refactoring-verify-and-final-gate.md`](cross-refactoring-verify-and-final-gate.md) の「範囲テストの組み立て」 |
| 宣言の形（`Suite.ci_jobs`・`Test.ci_exempt`） | [`project.schema.json`](../../plugins/ndf/skills/development-workflow/schemas/project.schema.json)（`project-decl.py schema` の生成物） |

この文書が扱うのは、Skill に書かない決定の理由、`check-lint.sh` の範囲の契約、突き合わせの部品の契約、
このリポジトリの宣言に置いた値である。

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「ジョブの識別子」「除外したジョブ」「宣言に無いジョブ」「範囲テスト」「全体テスト」である。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| このリポジトリの宣言の lint の suite と、`check-lint.sh` がパスを受けること | 言語別の整形・静的解析の規約を NDF が持ち、規約の無いリポジトリへ移植すること（#1691） |
| 宣言のキー `test.suites[].ci_jobs`・`test.ci_exempt`（NDF を使うすべてのリポジトリ） | cross-review の修正コミットが整形を通さないこと（#1507） |
| cross-refactoring の `init` の突き合わせ | 最終ゲート修正のコミットが push 前の検査で拒まれる経路（#1693。最終ゲートは入口で push してから静的解析を走らせる） |
| | `runtime-plugin-validate.yml` のジョブを suite にするか（#1694） |

## 背景

宣言の suite は種別 `kind`（`test` / `lint`）を持ち、静的解析の suite は変更したファイルのうち `paths` に当たるものを範囲にする
（#1483）。このリポジトリの宣言は pytest と構造チェック（#1668）だけで、`check-lint.sh` は範囲を受けなかった。
そのため項目の違反は項目の検証でも手元の最終ゲートでも拾えず、2026-09-30（PR 1564）と 2026-10-02（PR #1634）に
refactor が push 前の検査で止まった。

## 決定と理由

- **3 検査を 1 つの suite にまとめ、`check-lint.sh` にパスを受けさせる。** ファイルの振り分け（追跡しているか・拡張子・shebang）を
  `check-lint.sh` の 1 か所に保つためである。範囲テスト・全体テスト・CI（`lint.yml`）・`pre-push` が同じ規則で同じ検査を走らせる。
  検査ごとに glob で suite を分けると、拡張子の無い shebang のシェルスクリプト（`.githooks/pre-commit` など）を表せない
- **lint の suite の `paths` は `["."]` にし、対象の判定を `check-lint.sh` に任せる。** 対象外（`.md`・`.json` など）だけの項目で
  失敗させないためで、対象が 0 本ならツールを起動せずに 0 で終わる
- **照合の単位は継続的統合のジョブ（`<ワークフローのパス>#<job id>`）にし、宣言の側に受け持ちを明示する。** コマンドの文字列は
  比べない。このリポジトリの pytest は CI が `-n auto`、宣言が `-n 4 … --junitxml=…` で文が違い、文字列で比べると宣言にある
  検査を「無い」と知らせるためである。ブランチ保護の検査名（`ci.required_checks`）は保護を設けないリポジトリで空になる
- **除外は理由を必須にした別のキー `test.ci_exempt` に書く。** Pull Request の文脈でしか判定できないジョブ（宛先・本文・差分を
  見るもの）を毎回知らせると、知らせ全体が読まれなくなる。理由を必須にして、除外が書き漏れを隠す手段にならないようにする。
  キーは解析が書く `ci` でなく、利用者が手で書く `test` に置く
- **ジョブの読み取りは YAML ライブラリを使わず、解析の既存の読み方を共通の部品（`lib/ci_workflows.py`）へ移して使う。**
  `refactor.py` は標準ライブラリだけで動き、`ruamel.yaml` を使うと入口で uv の環境へ起動し直すことになる
- **突き合わせは結果を返すだけにし、出力と状態ファイルへの書き込みは `setup.py` が行う。** 知らせを出力と状態ファイルの
  両方に残して後から読めるようにし、行数の上限が近い `setup.py` に照合の処理を置かないためである

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | ジョブの識別子は `<ワークフローのパス>#<job id>` か `<ワークフローのパス>`（そのファイルのジョブすべて）の 2 形だけである（`model.JobId` の `^[^#\s]+\.ya?ml(#[^#\s]+)?$`） | `project-decl.py check` が落とす |
| I2 | `test.ci_exempt` の要素は空白だけでない理由を持つ | `project-decl.py check` が落とす |
| I3 | 突き合わせは起動を止めない。どの結果でも `init` の終了コードを変えない | 突き合わせの中の例外は「突き合わせられなかった」（`skipped`）に倒す |
| I4 | 宣言に無いと知らせるのは、どの suite の `ci_jobs` にも `test.ci_exempt[].job` にも当たらないジョブだけである | 宣言が覆うときは何も出さない |
| I5 | lint の suite の全体テストは、`bash scripts/check-lint.sh` を引数なしで走らせたものと同じファイルを見る | 引数なしの振る舞いは範囲を受け取る変更の前と同じ |
| I6 | `check-lint.sh` に渡したパスのうち、追跡されていない・消えた・検査の対象外のファイルは検査に入らず、それだけでは終了コード 0 で終わる | 対象外のファイルだけの項目は失敗にならない |

### `scripts/check-lint.sh` の範囲の契約

```text
bash scripts/check-lint.sh [--fix] [--] [<パス>...]
```

| 項目 | 契約 |
| --- | --- |
| 引数なし | 追跡されているファイルすべて（I5）。CI（`lint.yml`）と `pre-push` はこの形で呼ぶ |
| パスあり | `git --literal-pathspecs ls-files -z -- <パス>...` が返すファイル（ディレクトリは配下の追跡ファイル）のうち、作業ディレクトリに残るもの |
| 振り分け | `*.py` → ruff format --check と ruff check、`*.sh` と 1 行目が sh・bash の shebang の拡張子の無いファイル → shellcheck -S warning、ほかは対象外 |
| `--` | 以降をすべてパスとして読む。`--` より前の `-` で始まる知らない語は終了コード 2 |
| `--fix` | 検査の前に ruff check --fix と ruff format を Python の対象にだけ掛ける |
| 対象が 0 本 | ツールを起動せず `check-lint: 違反なし（Python 0 本・sh 0 本）` を出して 0 |
| 終了コード | 0 違反なし / 1 違反あり / 2 検査の仕組みが落ちた（git の外・uv が無い・引数の誤り・ツールの異常） |

パスは根からの相対で受ける。スクリプトは根へ `cd` してから読むため、根の外から相対パスで呼ぶと当たらない。
cross-refactoring は範囲テストを作業ディレクトリの根で走らせる。ツールは `uv run --frozen --project <根> --only-group lint` から起動し、
`uv.lock` の版を使う。

### 突き合わせ（`refactor_lib/ci_coverage.py` の `compare_jobs(decl, work)`）

1. 宣言の `ci` が無い・読めない（`test_strategy.ci_of` が `None`。`unknown` を持つか `workflows` が空のときも同じ）→ `skipped`（`ci が無い`）
2. `ci.provider` が `github-actions` でない → `skipped`（`provider が github-actions でない（<値>）`）
3. 作業ディレクトリの `.github/workflows/` の `*.yml` と `*.yaml` を名前の順に読む。読めないファイルがあれば `skipped`（`ワークフローを読めない（<パス>: <理由>）`）
4. 各ファイルの `jobs:` 直下の job id（`ci_workflows.job_ids`）から `<パス>#<job id>` を作る
5. 識別子そのものか、そのファイルのパスが宣言の `ci_jobs` か `ci_exempt[].job` にあれば覆われている。比較は文字列の一致だけで、glob も接頭辞も使わない
6. 覆われていない識別子を並べ替えて `undeclared` にする

ワークフローは GitHub の API でなく作業 worktree のファイルから読む。HEAD のワークフローと宣言を同じ木で比べるためである。
宣言の `ci.workflows`（解析が測った一覧）は使わない。ファイルの無い項目を持ち、解析の時点の値だからである。

`init` は戦略の行（`🧭`）の後に `CiCoverage.lines()` を標準エラーへ出す。

| 結果 | 出す行 |
| --- | --- |
| 宣言に無いジョブがある | `🔎 継続的統合のジョブ <N> 件のうち、宣言に無いもの <M> 件（手元の検証では走りません）`、識別子を 1 行に 1 つ、直し方の 1 行 |
| すべて覆う | 出さない |
| 突き合わせられなかった | `ℹ 継続的統合のジョブと宣言を突き合わせられませんでした（<理由>）` |

`lib/ci_workflows.py` の `job_ids` は解析（`project_lib/measure_ci.workflow_jobs`）も使い、ジョブの数え方は 1 つである。

## データ・設定

### 状態ファイルの `ci_coverage`

| キー | 型 | 意味 |
| --- | --- | --- |
| `status` | `compared` / `skipped` | 突き合わせたか、突き合わせられなかったか |
| `reason` | 文字列 | `skipped` の理由。`compared` なら空 |
| `jobs` | 整数 | 読んだジョブの数（`skipped` なら 0） |
| `undeclared` | ジョブの識別子の配列 | 宣言に無いジョブ |

書くのは新しい実行の `init` だけである。`ci_jobs` は `test_strategy.Suite` が宣言から読み、戦略とともに状態ファイルへ写す。
`ci_jobs` を持たない旧い状態ファイルも空として読める。

### このリポジトリの宣言（`.ndf/project.json` の `test`）

| suite | `kind` | `command` / `scope_command` | `paths` | `ci_jobs` |
| --- | --- | --- | --- | --- |
| `pytest` | `test` | `uv run … pytest . …` / `… pytest {paths} …` | `["."]` | `.github/workflows/pytest.yml` |
| `script-structure` | `lint` | `python3 scripts/check-script-structure.py` / `… {paths}` | `plugins/ndf` ほか | `.github/workflows/script-structure.yml` |
| `lint` | `lint` | `bash scripts/check-lint.sh` / `bash scripts/check-lint.sh -- {paths}` | `["."]` | `.github/workflows/lint.yml` |

`test.ci_exempt` は、手元の検証で結果が決まらないか走らせられない 5 本（`glossary.yml`・`pr-base-guard.yml`・
`pr-body-decisions.yml`・`runtime-plugin-authenticated-smoke.yml`・`runtime-plugin-smoke.yml`）を理由とともに持つ。
`runtime-plugin-validate.yml` のジョブはどこにも書かない。生成物の同期・frontmatter・リンクなど項目の変更で壊れうる検査で、
宣言に無いと知らせるのが正しいためである（扱いは #1694）。

`analysis.written.test` の指紋は宣言の値と一致しない（#1668 で手で suite を足したため）。解析の書き出し（`merge.merge_item`）は
一致しない項目を書き換えずに残すため、手で足した値は解析で消えない。

解析（`project-decl.py write`）が新しく作る宣言は `ci_jobs` を持たず、`init` はすべてのジョブを宣言に無いとして知らせる。

## テスト観点

- このリポジトリの宣言を `test_strategy.resolve` で解くと `kind: lint` で `{paths}` を持つ suite があり、ruff format・ruff check・
  shellcheck の違反をそれぞれ持つファイルを渡すと範囲テストが終了コード 1 になること（`scripts/tests/test_check_lint.py`）
- ruff format に違反する `.py` を変えたコミットで、`targets.verify_runs` が組んだ lint の範囲テストが 0 以外になること
  （`plugins/ndf/skills/cross-refactoring/tests/test_lint_suite_scope_git.py`）
- `.md` と `.json` だけ・追跡されていないファイル・消えたファイルを渡すと、ツールを起動せず 0 で終わること
- 引数なしと `git ls-files` の全ファイルを渡したときで対象の本数が同じで、HEAD で宣言の lint の `command` が 0 になること
- どの suite も受け持たないジョブが `undeclared` に入って `init` の出力に出て、`init` が止まらないこと
- ファイル単位と `#job` 単位の `ci_jobs` と `ci_exempt` ですべて覆うと、`undeclared` が空で行を出さないこと
- `ci` が無い・`provider` が `gitlab`・読めないワークフローで `skipped` と理由が返り、`init` が続くこと
  （`plugins/ndf/skills/cross-refactoring/tests/test_ci_coverage.py`）
- 既存の pytest・script-structure の範囲テストと全体テストのコマンドが、`ci_jobs` を足した後も同じ文で組まれること
- `project-decl.py check` が形の外のジョブの識別子と、理由の空の `ci_exempt` を落とすこと
  （`plugins/ndf/scripts/tests/test_project_decl_ci_jobs.py`）
- `measure_ci.workflow_jobs` の数が共通の部品へ移す前と同じであること（`plugins/ndf/scripts/tests/test_lib_ci_workflows.py`）
- `Strategy.as_state` → `from_state` で `ci_jobs` が残り、`ci_jobs` を持たない旧い状態ファイルも読めること

## 関連リンク

- [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md)
- [cross-refactoring-verify-and-final-gate.md](cross-refactoring-verify-and-final-gate.md)（範囲テストと静的解析の suite）
- [cross-refactoring-culprit-and-stop-revert.md](cross-refactoring-culprit-and-stop-revert.md)（構造チェックを範囲テストで走らせる宣言）
- [`scripts/check-lint.sh`](../../scripts/check-lint.sh)
- 課題: #464（関連 #1483・#1691・#1507・#1693・#1694）
