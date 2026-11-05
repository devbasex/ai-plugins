# #1694: このリポジトリの runtime-plugin-validate.yml の 9 ジョブがテストの宣言に無く、cross-refactoring の項目が壊しても手元で拾えない

正は課題の本文（#1694）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> **このリポジトリの継続的統合の `runtime-plugin-validate.yml` の 9 ジョブ（生成物の同期・frontmatter・リポジトリの前提・手順書の変数・
> リンク・文書の行数・指示書・必須チェックの設定・`claude plugin validate`）は、テストの宣言（`.ndf/project.json` の `test.suites`）に無い。**
> cross-refactoring の項目がこれらを壊しても、項目の検証と手元の最終ゲートは拾わない。
>
> #464 の実装（PR #1697）の後の今の宣言で `refactor_lib.ci_coverage.compare_jobs` を打つと、ジョブ 24 件のうち宣言に無いものは次の 11 件である（2026-11-04、develop 252ddf82b）。
>
> - `runtime-plugin-validate.yml` の 10 件: `doc-line-limit-check`・`instruction-files-check`・`markdown-link-check`・
> `required-checks-check`・`runtime-plugin-build-check`・`runtime-plugin-validate`・`service-boundary-check`・`skill-frontmatter-check`・
> `skill-repo-assumptions-check`・`skill-shell-vars-check`
> - `runtime-plugin-smoke-latest.yml` の `runtime-smoke-latest`
>
> 宣言の `ci_jobs`・`ci_exempt` はワークフローのファイル（`.github/workflows/<名前>.yml`）の単位で書く。
>
> ## 受け入れ条件（案）
>
> - [ ] 上の 11 件のそれぞれが、宣言の suite の `ci_jobs` か `test.ci_exempt`（理由つき）のどちらかに載る
> - [ ] cross-refactoring の `init` がこのリポジトリで宣言に無いジョブを知らせない

（書き手: 起票者、日付: 2026-10-03 起票・2026-11-04 更新。#464 の設計の途中で範囲外として起票した記録で、利用者の指示の原文は無い）

> 人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-05）

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 宣言に無いジョブ 11 件のそれぞれを、suite の `ci_jobs` か `test.ci_exempt`（理由つき）に載せる | 起票時の受け入れ条件（案）1 | 受け入れ条件 1・2 |
| S2 | cross-refactoring の `init` がこのリポジトリで宣言に無いジョブを知らせない | 起票時の受け入れ条件（案）2 | 受け入れ条件 3 |
| S3 | 宣言の `ci_jobs`・`ci_exempt` はワークフローのファイルの単位で書く | 本文「宣言の `ci_jobs`・`ci_exempt` はワークフローのファイルの単位で書く」 | 採らない。突き合わせ（`refactor_lib/ci_coverage.py`）は `<パス>#<ジョブ id>` の形も受ける。`runtime-plugin-validate.yml` の中には手元で走らせるジョブと走らせないジョブが混ざるため、ジョブの単位で書く（前提 2） |
| S4 | 対象は「9 ジョブ」 | 題と本文の 1 段落目 | 採らない。2026-11-05 の再測定で、`runtime-plugin-validate.yml` のジョブは 10 件（`service-boundary-check` を含む）、ほかに `runtime-plugin-smoke-latest.yml` の 1 件がある。対象は 11 件とする |
| S5 | 各ジョブを suite にするか除外するかは、範囲テストの形（`{paths}` を受けられるか）を確かめて決める | 本文「なぜ範囲外か」 | 採らない。静的解析の suite（`kind: lint`）は `scope_command` を持たなくても全体の実行で判定できる（`plugins/ndf/scripts/lib/test_triage.py` の `lint_verdict`）。suite にする条件は前提 3 で決める |

## 目的

cross-refactoring の項目が、このリポジトリの継続的統合で検査している内容（生成物の同期・Skill の frontmatter・リンク・文書の行数・指示書など）を壊したとき、Pull Request を出す前の手元の検証で気づけるようにする。手元で走らせられない検査は、走らせない理由を宣言に残し、`init` が毎回「宣言に無いジョブ」を知らせる状態を無くす。

## 前提

- 前提 1: 変更の対象は、このリポジトリのプロジェクトの宣言（`.ndf/project.json` の `test`）だけである。突き合わせの処理（`ci_coverage.py`）・宣言のスキーマ・ワークフローは変えない
- 前提 2: ジョブの識別子は、1 つのファイルのジョブがすべて同じ扱いになるときはファイルの単位（`<パス>`）、扱いが分かれるときはジョブの単位（`<パス>#<ジョブ id>`）で書く
- 前提 3: ジョブを suite にする条件は、次の 3 つをすべて満たすことである。満たさないジョブは除外にする
  - 秘密・ネットワーク・Pull Request の文脈（宛先のブランチ・本文）を使わずに、手元で判定が決まる
  - 継続的統合のジョブと同じスクリプトを同じ引数で走らせる
  - 1 回の実行が 60 秒以内に終わる（2026-11-05 の手元の実測で最長は `validate-runtime-plugins.sh` の 12.2 秒）
- 前提 4: 前提 3 の判定の材料は下の「現状の実測」の表である。どのジョブをどちらに載せるか・suite の分け方（ジョブごとか、まとめるか）・`paths` は設計で決める
- 前提 5: `claude plugin validate` を走らせるジョブ（`runtime-plugin-validate`）は、手元に `claude` の CLI があることを要る。cross-refactoring のホストは Claude Code か Codex であり、Codex のホストでは CLI が無いことがある。suite にするか除外するかは設計で決め、決めた理由を宣言か設計に残す

### 現状の実測

2026-11-05、develop d51159151、このリポジトリの worktree で各ジョブの `run` の行を走らせた結果。

| ジョブ | 継続的統合で走らせるもの | 手元の終了コード | 手元の所要 | 手元で判定が決まらない要因 |
| --- | --- | --- | --- | --- |
| `runtime-plugin-build-check` | `bash scripts/build-runtime-plugins.sh --check` | 0 | 0.2 秒 | 無し |
| `skill-frontmatter-check` | `python3 scripts/check-skill-frontmatter.py` | 0 | 0.2 秒 | 無し |
| `skill-repo-assumptions-check` | `python3 scripts/check-skill-repo-assumptions.py` | 0 | 0.2 秒 | 無し |
| `skill-shell-vars-check` | `python3 scripts/check-skill-shell-vars.py` | 0 | 0.4 秒 | 無し |
| `service-boundary-check` | `python3 scripts/check-service-boundary.py` | 0 | 1.6 秒 | 無し |
| `runtime-plugin-validate` | `bash scripts/validate-runtime-plugins.sh`（継続的統合では先に `claude` の CLI を入れる） | 0 | 12.2 秒 | `claude` の CLI が要る（前提 5） |
| `markdown-link-check` | `python3 scripts/check-markdown-links.py --root .` | 0 | 7.3 秒 | 無し |
| `doc-line-limit-check` | `python3 scripts/check-doc-line-limit.py --root .` | 0 | 0.1 秒 | 無し |
| `instruction-files-check` | `python3 plugins/ndf/scripts/instructions-check.py --root .` | 0 | 0.2 秒 | 無し |
| `required-checks-check` | `python3 scripts/check-required-checks.py --repo … --branch <宛先>` | 未実行 | — | Pull Request の宛先のブランチと GitHub の API を使う |
| `runtime-smoke-latest` | 最新の CLI の版を解決して入れ、スモークを走らせる（定期と手動の起動だけ） | 未実行 | — | ネットワークから最新の CLI を入れる |

手元の所要は 1 回の実測で、ばらつきは測っていない。上の 9 本の検査スクリプトは、どれも対象のファイルのパスを引数に受けない（各スクリプトの `argparse` の定義を確かめた）。

## 対象範囲

含む:

- 宣言に無いジョブ 11 件を、suite の `ci_jobs` か `test.ci_exempt` に載せる
- suite にするジョブのために、静的解析の suite（`kind: lint`）を宣言へ足す
- 除外にするジョブの理由を書く

含まない:

- 検査スクリプトに、対象のファイルのパスを受ける引数（範囲テストの形）を足すこと
- 突き合わせの処理・宣言のスキーマ・ワークフローの変更
- このリポジトリ以外のプロジェクトの宣言
- 宣言の再解析（`project-decl.py check` が古いと判定する件。宣言の `analysis.inputs` の更新を含む）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 宣言に suite と除外を足した | 実装の変更 | 宣言が壊れると、cross-refactoring・cross-review・テストの実行（`test-run.py`）が宣言を読めず止まる（受け入れ条件 5） | — |
| E2 | `init` が継続的統合のジョブと宣言を突き合わせた | cross-refactoring の `init` | 突き合わせられないと「突き合わせられませんでした」の 1 行が出る | E1 |
| E3 | 着手前のテストで、足した静的解析の suite を全体で走らせた | cross-refactoring の着手前のテスト | 着手前から落ちる suite は、最終ゲートで既存失敗として判定から外れる | E1 |
| E4 | 最終ゲートで、足した静的解析の suite を全体で走らせた | cross-refactoring の最終ゲート | 項目が壊していれば変更起因として止まる（目的の振る舞い） | E3 |

E3・E4 の振る舞いは既存の処理（`lint_verdict`）が持ち、この課題では変えない。

## 用語

| 用語 | 意味 |
| --- | --- |
| プロジェクトの宣言 | この文書では、このリポジトリの `.ndf/project.json` を指す。本文では「宣言」と略す |
| ジョブの識別子 | 継続的統合のジョブを指す `<ワークフローのファイルのパス>#<job id>`。`#<job id>` を省くとそのファイルのジョブすべてを指す |
| 除外したジョブ | 手元の検証で走らせないと宣言したジョブ。宣言の `test.ci_exempt` に理由と組で書く |
| 宣言に無いジョブ | 継続的統合のジョブのうち、どの suite の `ci_jobs` にも除外したジョブにも当たらないもの |

## 受け入れ条件

- [ ] 1. 宣言に無いジョブ 11 件（上の「現状の実測」の表の行）のそれぞれが、suite の `ci_jobs` か `test.ci_exempt` の `job` のどちらか一方だけに、ファイルの単位かジョブの単位の識別子で当たる
- [ ] 2. `test.ci_exempt` に足した行は、`reason` が空でなく、手元で判定が決まらない要因（「現状の実測」の表の最後の列に当たるもの）か、走らせない理由を書いている
- [ ] 3. このリポジトリの宣言と作業ディレクトリで `compare_jobs` を打つと、`status` が `compared` で `undeclared` が 0 件になる（`init` が「宣言に無いもの」の行を出さない）
- [ ] 4. 足した suite のそれぞれは、`command` を作業ディレクトリの根で走らせると終了コード 0 で終わり、そのコマンドは `ci_jobs` に書いたジョブの `run` の行と同じスクリプトを同じ引数で呼ぶ（継続的統合だけで要る準備の手順は除く）
- [ ] 5. `project-decl.py check --root .` が 3（壊れている）を返さない
- [ ] 6. 既存の suite（`pytest`・`factory`・`script-structure`・`ndf-contracts`・`lint`）と既存の除外 5 件は、名前・コマンド・`ci_jobs`・理由が変わらない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 足した suite の `command` の所要の合計は、手元で 30 秒以内（2026-11-05 の実測では、`claude` の CLI を使うジョブを含めて 22.4 秒、含めないで 10.2 秒）。cross-refactoring の着手前のテストと最終ゲートは、この合計ずつ長くなる |
| システム環境 | 足した suite は、秘密とネットワークを使わずに走る |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（配布物を変えない） |
| データ | このリポジトリの宣言（`.ndf/project.json`）の `test` に suite と除外が増える。移行は無い |
| 既存の振る舞い | このリポジトリで cross-refactoring を走らせると、着手前のテストと最終ゲートで足した suite が走る。宣言の suite を読む cross-review とテストの実行（`test-run.py`）でも、足した suite が走る対象に入る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 突き合わせ（受け入れ条件 1・3） | `cd plugins/ndf/skills/cross-refactoring/scripts && python3 -c "import json,pathlib; from refactor_lib.ci_coverage import compare_jobs; r=compare_jobs(json.load(open('<根>/.ndf/project.json')), pathlib.Path('<根>')); print(r.as_state())"` |
| suite のコマンド（受け入れ条件 4） | 足した suite の `command` を根で 1 本ずつ走らせ、終了コードを見る |
| 宣言の形（受け入れ条件 5） | `python3 plugins/ndf/scripts/project-decl.py check --root .` の終了コード |
| 既存の宣言の不変（受け入れ条件 2・6） | `git diff` で `.ndf/project.json` の既存の要素が変わっていないことと、足した除外の理由を見る |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 変更は `.ndf/project.json` だけに置く。プラグイン（`plugins/`）とワークフロー（`.github/workflows/`）は変えない |
| コーディング規約 | 宣言の形は `plugins/ndf/skills/development-workflow/schemas/project.schema.json` に従う |
| テスト戦略 | 振る舞いの変更が無いためテストを足さない。受け入れ条件は検証手段のコマンドの実行結果で確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 足した suite のコマンドを手元で走らせる。突き合わせの結果を確かめる |
| 確認してから行う | 宣言（`.ndf/project.json`）の書き換え。共通原則の C7 に当たり、承認ゲート 2 で人が見る |
| 行わない | 検査スクリプト・ワークフロー・突き合わせの処理の変更。宣言の再解析 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 11 件のそれぞれを suite と除外のどちらに載せるか（前提 3・前提 5） | 設計 | 承認ゲート 1 |
| suite の分け方（ジョブごとに 1 つか、まとめて 1 つか）と各 suite の `paths` | 設計 | 承認ゲート 1 |
