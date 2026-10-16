# #1483: テストの戦略の宣言が suite の種別と起動の失敗を持たず、cross-review / cross-refactoring / supervise の判定がそれぞれ食い違う

正は課題の本文（#1483）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 修正レイヤー

テストの戦略の宣言と解釈（`plugins/ndf/scripts/lib/test_strategy.py` と `.ndf/project.json` の `test`）。

宣言が suite の種別（テスト / 静的解析）と起動の失敗を持たないため、cross-review・cross-refactoring・supervise の呼ぶ側がそれぞれ別に推測し、判定が食い違う。

## 修正方針

- **新設**: 宣言に suite の種別（テスト / 静的解析）を持たせる
- **移動**（`move_responsibility`）: 全体テストの解決と起動の失敗の判別を、呼ぶ側から `test_strategy.py` へ移す

## 子 issue

| 課題 | 残る個別の作業 |
| --- | --- |
| #464 | cross-refactoring の `--baseline-test` に整形・静的解析を含める |
| #357 | cross-review の最終スイープの検証が、宣言と起動の引数 `--verify-command` から候補を決める |
| #1434 | 範囲テストの雛形が静的解析のとき、着手前テスト・最終ゲート・テスト整備ラウンドの判定を種別に合わせる |
| #1437 | supervise new mission の `_from_args` が、静的解析の `--test-cmd` から `{paths}` 空の全体テストを組まない |
| #1459 | 範囲テストが起動できないことを判別し、適用と取り消しをくり返さずに止める |

## 依頼（原文）

> ## 完了条件
>
> - `.ndf/project.json` の `test` が suite の種別を宣言でき、`test_strategy.py` が種別と起動の失敗を返す
> - cross-review・cross-refactoring・supervise が、テストの種別と全体テストを `test_strategy.py` から受け取る

（上の「修正レイヤー」「修正方針」「子 issue」も起票時の原文のまま残している。）

## 目的

- 静的解析しか持たないリポジトリ（シェルスクリプトだけの範囲など）でも、cross-refactoring・supervise・cross-review が同じ宣言から同じ全体テストと範囲テストを組み、変更と無関係に常に落ちるテスト（`shellcheck .`）を走らせない
- 整形・静的解析の違反を、項目ごとの検証と最終ゲートの時点で拾い、継続的統合で初めて落ちる状態をなくす
- テストのコマンドが起動できないことを「テストが落ちた」と区別し、適用と取り消しをくり返さずに 1 回目で止める

### 現状（2026-09-29 に `develop` の `a6b7499b` で確かめた事実）

| # | 事実 | 場所 |
| --- | --- | --- |
| F1 | 宣言の suite（`Suite`）は `name` / `runner` / `command` / `scope_command` / `junit` / `container` / `needs` / `paths` を持ち、種別を持たない | `plugins/ndf/scripts/project_lib/model.py:43` |
| F2 | 引数の雛形（`--baseline-test` / `--round-test` / supervise の `--test-cmd`）から戦略を解くと、全体テストは雛形の `{paths}` を `.` にしたもの（`whole_command_of`）になり、宣言の `suites[].command` を使わない | `test_strategy.py` の `_from_args` |
| F3 | 全体テスト（`whole_commands`）は文字列としてシェル経由で走り、範囲テスト（`scope_words`）は語の並びとしてシェルを介さずに走る。同じ宣言のコマンドの解釈が 2 通りある | `lib/test_triage.py:46`（`shell=isinstance(command, str)`）、`test_strategy.py` の `scope_words` |
| F4 | 戦略 `round-only` のラウンドテストは suite の `command` を ` && ` でつないだ 1 本の文字列になり、前の suite の `cd` が後の suite に効く | `test_strategy.py` の `resolve` |
| F5 | 起動できないときは終了コード 127 として返り、テストが落ちたときと同じ扱いで先へ進む | `lib/test_triage.py:57`、`cross-refactoring/scripts/refactor_lib/process.py:61` / `:104` |
| F6 | cross-review は `test_strategy.py` を読まない。最終スイープの検証は探し方（`docs/02-fix-and-rotation.md` の Step 7.5）だけで決まる | `plugins/ndf/skills/cross-review/scripts/drive.py` |

## 前提

- 前提 1: 種別は **テスト** と **静的解析** の 2 値とする。整形の検査（`ruff format --check`・`black --check` など）は静的解析に入れる。種別を書かない suite はテストとして扱う（既存の宣言は書き換えずに今と同じ振る舞いになる）
- 前提 2: 種別のキー名と値の綴りは `design` が決める。この要求は「2 値を宣言でき、既定がテスト」という振る舞いだけを決める
- 前提 3: 引数で渡す雛形（`--baseline-test` / `--round-test` / `--test-cmd`）の種別は既定でテストとする。静的解析の雛形を引数で渡すときは、種別を別の引数で添える（引数名は `design` が決める）。雛形の語（`shellcheck` など）から種別を推測しない（`test_strategy.py` の I1「戦略はコマンドの語を見ずに決まる」を保つ）
- 前提 4: 静的解析の suite の範囲は **その項目で変更したファイル**、テストの suite の範囲は **その項目のテストの対象**（`test_targets`）とする
- 前提 5: 静的解析の suite の全体テストは、宣言の `command` があればそれを使う。宣言の `command` が無く雛形だけのときは、`{paths}` を範囲（cross-refactoring の `--scope`、supervise の `--tests`）のパスで埋める。`{paths}` を `.` にしない
- 前提 6: 宣言の `command` / `scope_command` の解釈は **シェル経由** に揃える（F3）。範囲テストは `{paths}` の 1 語を、シェルの引用で守った対象の語の並びへ置き換えてからシェルへ渡す。`test_strategy.py` の I2（加工は `{paths}` の置き換えだけ）を保つ
- 前提 7: 起動の失敗とは、プロセスを起動できない（起動の例外）か、シェルが「コマンドが見つからない・実行できない」を返した（終了コード 127 / 126）ことを指す。テストのコマンド自身が 126 / 127 を返す場合との区別はしない（起動の失敗として止める側へ倒す。誤って止めても人が 1 回で直せる）
- 前提 8: cross-review の最終スイープは、宣言の `test` があればその全体テスト（両方の種別）を検証として名指しする。宣言が無ければ今の探し方（Step 7.5）に従う。起動の引数 `--verify-command` は今の意味（実行検証 Step 2.5 で走らせてよいコマンドの許可の一覧）のまま変えず、最終スイープへは流用しない

## 対象範囲

含む:
- `.ndf/project.json` の `test.suites[]` の種別の宣言（スキーマ・`project_lib/model.py`・`project-decl.py` の検証）
- `test_strategy.py` が suite ごとの種別・種別ごとの全体テスト・範囲テストの組み立て・起動の失敗の判別を返すこと
- `test-run.py`（`scope` / `whole`）が同じ解き方で走らせ、起動の失敗を別の結果として返すこと
- cross-refactoring の着手前テスト・項目ごとの範囲テスト・テスト整備ラウンド・最終ゲート・`--scope` のテストの置き場所の検査が種別に従うこと（#464・#1434・#1459）
- supervise の `new`（`supervise_lib/decl.py`）が `--test-cmd` と宣言から全体テストを組むときの解き方（#1437）
- cross-review の最終スイープのプロンプト（`drive.py` の `sweep_prompt`）へ宣言の全体テストを渡すこと（#357）
- 既存の状態ファイル（種別を持たない `strategy`）を再開したときにテストとして読むこと

含まない:
- 同じ項目の Revert / Reapply の回数の上限と、取り消しで積まれたコミットの扱い（取り消しと公開の層。#817 #1399 #1237）
- 最終ゲート修正のラウンドに上限を掛けること（#1434 の期待する振る舞いの 2 つ目のうち「上限」の部分。この変更では `shellcheck .` を組まないことで原因を除く）
- このリポジトリの `.ndf/project.json` に静的解析の suite（`check-script-structure.py`・`scripts/check-lint.sh`・ruff）を足すこと（共通原則 C7。人の承認を得てから別に行う。未決の表）
- 解析（`project-decl.py` の解析・`detect-test-runner` #853）が整形・静的解析の suite を自動で見つけて宣言へ書くこと
- 言語別の整形・静的解析の規約を ndf が Skill のリファレンスで持ち、対象のリポジトリへ移植すること（#464 の「決めること」の 2 つ目）
- 継続的統合の検査と宣言を `init` で突き合わせること（#464 の案 B の後半）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 宣言を読んだ | cross-refactoring の `init`・supervise の `new`・`test-run.py`・cross-review の最終スイープの組み立て | 種別の値が 2 値のどれでもない → 宣言の検証の失敗として止まり、どの suite の何が不正かを示す | — |
| E2 | 戦略を解いた（suite ごとの種別・全体テスト・範囲テストの雛形が決まった） | E1 と引数 | 解けない → `StrategyError`（今と同じ） | E1 |
| E3 | 着手前の全体テストを走らせた | cross-refactoring の着手前・supervise の実装プランの `test-all` | 起動の失敗 → E7。落ちた → 今と同じく既存失敗として記録 | E2 |
| E4 | 項目の範囲テストを走らせた（テストは対象、静的解析は変更したファイル） | 項目の適用の後 | 起動の失敗 → E7。落ちた → 今と同じく項目を取り消す | E2・項目の適用 |
| E5 | テスト整備ラウンドで足したテストを判定した | 計画がテストを足すと決めた項目 | テストの種別の suite が足したテストを受け持たない → 判定しないで先へ進み、その旨を記録する | E2 |
| E6 | 最終ゲートで全体テストを走らせた | 全項目の後 | 起動の失敗 → E7。落ちた → 今と同じく修正へ回る | E2 |
| E7 | 起動の失敗を報告して止まった | E3 / E4 / E6 の起動の失敗 | — | E3 / E4 / E6 のどれか |
| E8 | 最終スイープのプロンプトに検証のコマンドを書いた | cross-review の最終スイープ | 宣言が無い → 今の探し方の文を書く | E2 |

E5 の「受け持たない」ときの扱いと、E7 の後に何が残るか（途中まで適用した項目）は受け入れ条件で決めた。

## 用語

| 用語 | 意味 |
| --- | --- |
| suite の種別 | 宣言の suite が「テスト」か「静的解析（整形の検査を含む）」か。既定はテスト |
| 起動の失敗 | テストのコマンドのプロセスを起動できない、またはシェルが終了コード 126 / 127 を返したこと。テストが落ちたこととは別に扱う |

## 受け入れ条件

### 宣言

- [ ] AC1: `.ndf/project.json` の `test.suites[]` に種別（テスト / 静的解析）を書いた宣言が、スキーマと `project-decl.py` の検証を通る
- [ ] AC2: 種別に 2 値以外の値を書いた宣言は検証で失敗し、どの suite（添字か名前）のどのキーが不正かを出力に含む
- [ ] AC3: 種別を書かない suite を持つ既存の宣言（このリポジトリの `.ndf/project.json` を含む）で、`test_strategy.resolve` の戦略・全体テスト・範囲テストが変更前と同じになる（退行しない）

### 解き方（`test_strategy.py`）

- [ ] AC4: `test_strategy.py` が解いた戦略から、suite ごとの種別を読める。状態ファイルへ写した形（`as_state`）にも種別が残り、種別を持たない既存の状態ファイルを `from_state` で読むとテストになる
- [ ] AC5: 静的解析の suite だけを持ち `command` を持たない戦略で、全体テストのコマンドに `{paths}` を `.` にしたもの（例: `shellcheck -s bash .`）が 1 つも含まれない
- [ ] AC6: 引数の雛形と宣言の `suites[].command` の両方があるとき、全体テストは宣言の `command` になる（#1437 の期待する振る舞いの 1 つ目）
- [ ] AC7: 静的解析の雛形を引数で渡し、宣言の `command` が無いとき、全体テストは `{paths}` を範囲のパスで埋めたものになる。範囲のパスも無ければ全体テストは「無い」になり、その理由が戦略の `notes` に残る
- [ ] AC8: 範囲テストの組み立てで、テストの suite には対象のテストの並びが、静的解析の suite にはその項目で変更したファイルの並びが入る
- [ ] AC9: 同じ宣言の `command` / `scope_command` を、`test-run.py` と cross-refactoring が同じ方式（シェル経由）で走らせる。`(cd sub && pytest -q {paths})` の形の `scope_command` が、どちらでも起動の失敗にならずに走る（#1459 の期待する動きの 3 つ目の前半）
- [ ] AC10: 戦略 `round-only` で `command` を持つ suite が 2 つ以上あるとき、前の suite の作業ディレクトリの変更が後の suite に効かない（`cd a && x` と `cd b && y` の 2 つの suite が両方とも走る。#1459 の期待する動きの 3 つ目の後半）
- [ ] AC11: `test_strategy.py` が、終了コードと起動の例外から「通った / 落ちた / 起動の失敗」を返す関数を持ち、`test-run.py` と cross-refactoring の両方がこの 1 つの関数で判別する（同じ役割の関数を呼ぶ側に分けない）

### `test-run.py`

- [ ] AC12: `test-run.py scope` / `whole` は、起動の失敗のとき「落ちた」と別の状態（結果 JSON の `status` か `items`）と、落ちたときと別の終了コードを返す。出力に起動できなかったコマンドと理由を含む

### cross-refactoring

- [ ] AC13: 着手前・項目ごと・最終ゲートのどれかでテストのコマンドが起動の失敗になったら、その時点で止まり、次の項目の適用も取り消しも行わない。報告に起動できなかったコマンドと理由を書き、終了コードは「検証を終えた」（0）と別の値になる（#1459 の期待する動きの 1 つ目）
- [ ] AC14: 宣言の静的解析の suite（例: `ruff format --check {paths}`）が、項目ごとの範囲テストと最終ゲートで走る。項目が整形の違反を入れたら、その項目の範囲テストで落ちる（#464）
- [ ] AC15: テスト整備ラウンドで足したテストの成否は、テストの種別の suite で判定する。足したテストを静的解析の suite にかけた結果で項目を取り消さない（#1434 の表の 3）
- [ ] AC16: テストの種別の suite が 1 つも無い戦略では、テスト整備ラウンドを行わず、`--scope` にテストの置き場所を求める検査で止まらない。どちらも行わなかったことを計画か報告に残す（#1434 の期待する振る舞いの 3 つ目と 4 つ目）
- [ ] AC17: `--baseline-test 'uvx --from shellcheck-py shellcheck -s bash {paths}'` を静的解析の種別で渡し、`--scope` がシェルスクリプトだけのとき、着手前と最終ゲートで `shellcheck ... .`（ディレクトリを渡す形）を走らせない（#1434 の表の 1 と 2）

### supervise

- [ ] AC18: `supervise.py new sprint ... --test-cmd '<静的解析の雛形> {paths}' --tests <ファイル>` を静的解析の種別で渡したとき、実装プランの `test-all` は `{paths}` を `.` にした雛形を走らせない。宣言の `command` があればそれを、無ければ `--tests` のパスで埋めた雛形を走らせる（#1437）

### cross-review

- [ ] AC19: 宣言の `test` があるとき、最終スイープのプロンプトは宣言の全体テスト（テストと静的解析の両方）のコマンドを名指しし、探し方（Step 7.5 の一覧）を書かない（#357）
- [ ] AC20: 宣言の `test` が無いとき、最終スイープのプロンプトは変更前と同じ探し方を書く。`--verify-command` の意味（実行検証 Step 2.5 の許可の一覧）と振る舞いは変わらない

### 起きてはいけないこと

- [ ] AC21: 全体テスト（`uv run --frozen --project . --all-extras pytest . -q -n 4`）が変更前と同じく通る
- [ ] AC22: 範囲テストのコマンドの組み立てで、対象のパスに空白・シェルの特殊文字（`$`・`;`・`(`）を含んでも、1 つのパスが 1 つの引数として渡り、シェルに解釈されない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 種別・全体テストの解決・起動の失敗の判別は `test_strategy.py` の 1 か所にあり、cross-review・cross-refactoring・supervise・`test-run.py` に同じ役割の関数を置かない（プロジェクト Value 6） |
| 移行性 | 種別を持たない宣言と、種別を持たない状態ファイル（中断した cross-refactoring の再開）が、書き換えずに今と同じ振る舞いで動く（AC3・AC4） |
| セキュリティ | シェル経由へ揃えても、対象のパスはシェルに解釈されない（AC22） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 宣言 `.ndf/project.json` の `test.suites[]` に任意のキーが 1 つ増える（後方互換）。cross-refactoring・supervise の引数に種別を添える引数が 1 つ増える（任意）。`test-run.py` の結果 JSON に起動の失敗の状態が増える |
| データ | スキーマ `project.schema.json` の `Suite` が変わる。移行は要らない（既定がテスト） |
| 既存の振る舞い | 範囲テストがシェル経由になる（F3）。`round-only` のラウンドテストが suite ごとに分かれる（F4）。起動の失敗で止まる（F5）。cross-review の最終スイープが宣言のコマンドを名指しする（F6） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（範囲は `plugins/ndf/scripts/tests` と `plugins/ndf/skills/cross-refactoring/scripts/tests`・`cross-review/scripts/tests`） |
| 静的解析 | `bash scripts/check-lint.sh`、`python3 scripts/check-script-structure.py`（あれば）、`python3 scripts/check-skill-frontmatter.py` |
| 宣言の検証 | 種別を書いた宣言・不正な値の宣言をテストの一時ディレクトリに置き、`project-decl.py` の検証を通す（AC1・AC2） |
| 実機の再現 | 静的解析の suite だけを宣言した一時リポジトリ（シェルスクリプト 1 本）で `test-run.py whole` と `scope` を走らせ、`shellcheck .` が走らないこと・起動できないコマンドで起動の失敗の状態が返ることを見る（AC5・AC12・AC17） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 共通の層は `plugins/ndf/scripts/lib/`。Skill ごとのスクリプトから共通の層を呼び、同じ役割の定数・関数を Skill 側に置かない（`AGENTS.md`、プロジェクト Value 6） |
| コーディング規約 | `test_strategy.py` は純粋な処理だけを置く（モジュールの説明文の I1・I2）。標準ライブラリだけで書く。ruff は `pyproject.toml` の `[tool.ruff]` |
| テスト戦略 | `test_strategy.py` の単体テストで種別・全体テスト・範囲テスト・起動の失敗の判別を固定し、`test-run.py` と cross-refactoring は一時リポジトリで実際にコマンドを走らせる結合テストで確かめる。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、ruff の適用、宣言のスキーマ（`project.schema.json`）と `project_lib/model.py` の同期 |
| 確認してから行う | このリポジトリの `.ndf/project.json` に静的解析の suite を足すこと（C7） |
| 行わない | 取り消しの層（Revert / Reapply の上限）の変更、解析による静的解析の suite の自動検出、雛形の語から種別を推測すること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 種別のキー名と値の綴り、引数の雛形に種別を添える引数の名前（前提 2・3） | `design`（この課題の設計） | 設計 PR |
| このリポジトリの `.ndf/project.json` に `check-script-structure.py`・`scripts/check-lint.sh`・ruff を静的解析の suite として足すか（#464 の振り返りからの追記。C7 に当たる） | 利用者 | 実装の Pull Request の前 |
| 最終ゲート修正のラウンドに上限を掛けるか（#1434 の期待する振る舞いの 2 つ目） | 利用者（#1434 に残す） | この課題の完了後 |

## 関連

- #817 #1399 #1237（取り消しと公開の層。#1459 のくり返しで積まれたコミットの扱いはそちら）
