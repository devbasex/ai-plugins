# #1630: cross-refactoring の範囲テストが変わったファイルのディレクトリを discover し、テストを tests/ にだけ置くプロジェクトで落ちて fix を繰り返す

正は課題の本文（#1630）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

cross-refactoring の改修ごとのテスト（範囲テスト）は、`.ndf/project.json` の `scope_command` の `{paths}` へ、計画の `test_targets`（テストのファイルのパス）を入れて走らせる（`plugins/ndf/scripts/lib/test_strategy.py` の `scope_runs` と `suite_groups`。変更したファイルを入れるのは静的解析の suite だけ）。unittest の `discover -s` は開始のディレクトリしか受けないため、`scope_command` が `python3 -m unittest discover -s {paths}` だと、`{paths}` にファイルのパスが入った時点で落ちる。コードに原因が無いのに「範囲テストが落ちた項目 9 件を修正へ回します」となり、fix の担当が「コード起因の失敗なし・コミットなし」を返すまま、fix のラウンドを上限まで繰り返した。

手元の再現（`scripts/storylib` に `__init__.py` と `x.py`、`tests/test_x.py` に 1 本のテスト）:

| `discover -s` に渡すもの | 結果 |
| --- | --- |
| `scripts/storylib`（テストの無いディレクトリ） | `NO TESTS RAN`、exit 5 |
| `tests/test_x.py`（ファイル） | `ImportError`、exit 1 |
| `tests`（テストのディレクトリ） | exit 0 |

宣言の `{paths}` にファイルとディレクトリのどちらが入るかの契約は宣言の側に書かれておらず、development-workflow の手順 0 で宣言を作るときにも unittest の `scope_command` の書き方の案内が無い。

回避として、検査のプランの cross-refactoring の引数へ `--round-test "python3 -m unittest discover -s tests"` を書き、改修ごとのテストを全体に固定して流し直した。

## どこで見つけたか

takemi-ohama/novel のスプリント world（PR takemi-ohama/novel#58）の検査のフェーズ（supervise.py の 5-check の `04-refactor`）。13 件の改修のうち 9 件が範囲テストで落ち、fix のフェーズが 10 回以上続いた。

## なぜこの変更の範囲外なのか

開発対象リポジトリの変更ではなく、範囲テストの `{paths}` に何が入るか（`test_targets` のファイルのパス）と、宣言の `scope_command` がそれを受けられるかの契約の問題である（`scripts/lib/test_strategy.py` と development-workflow の手順 0 の宣言の解析）。開発対象リポジトリでは `--round-test` を固定する回避しかできない（MVV なし: 上流）。

## 直さないと何が起きるか

ファイルのパスを受けないランナー（unittest の `discover -s` など）を `scope_command` に書いたプロジェクトでは、範囲テストが常に落ち、改修が fix のラウンドを使い切るか、全体テストへ手で固定するまで進まない。fix の時間と context を無駄に使う。

## 受け入れ条件（案）

- [ ] `scope_command` に `python3 -m unittest discover -s {paths}` のようなファイルのパスを受けない雛形を書いた宣言で、範囲テストが落ちて改修が fix へ回ることが無い（範囲テストが通るか、宣言の検査の時点で止まり直し方が出る）
- [ ] 宣言の契約（`project.schema.json` の `Suite` の説明と、development-workflow の手順 0 の宣言の作成の案内）から、`{paths}` にテストのファイルのパスが入ることが分かる

## 直す場所

- 現象レイヤー: cross-refactoring の範囲テストが毎回落ち、fix のラウンドを使い切る（supervise の検査のフェーズ）
- 修正レイヤー: 宣言の契約。`scope_command` の `{paths}` にファイルのパスが入ることの明記と、宣言の検査（`project-decl.py check` / 手順 0）での受けられない雛形の検出
- 修正方針: 向きの修正と新設。`{paths}` にテストのファイルのパス（`test_targets` の `::` より前）が入る契約を `plugins/ndf/skills/development-workflow/schemas/project.schema.json` の `Suite` の説明と `plugins/ndf/skills/development-workflow/references/project-analysis.md` の `test` の行に書き、`project-decl.py check`（または cross-refactoring の `init` の着手前の範囲テスト）でファイルを受けない `scope_command` を見分けて止めるか案内する

## 由来

takemi-ohama/novel issue #32 / PR #58（スプリント world の振り返り）

## 依頼（原文）

> #1630。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。本文を書いたら `spec-copy.py write 1630 issues/issue-1630-requirements.md` で写しを作り、コミットする（push しない）。

（書き手: AI の起動指示、日付: 2026-10-24。利用者の指示の原文ではない）

この課題の題と、本文の「何を見つけたか」〜「由来」の節は、takemi-ohama/novel のスプリント world の振り返りで AI が書いたものである（書き手: 起票者）。利用者の指示の原文は無い。題と本文の条件は、次の「起票者の推定」で扱いを決める。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 範囲テストは「変わったファイルのディレクトリ」を `discover` する（題） | 題 | 採らない。コードは計画の `test_targets` の `::` より前のパス（テストのファイル）を `{paths}` へ入れる（`plugins/ndf/scripts/lib/test_strategy.py` の `suite_groups`・`scope_runs`）。変更したファイルを入れるのは静的解析の suite だけである。本文の「何を見つけたか」も同じことを書いている |
| S2 | 落ちるのは「テストを tests/ にだけ置くプロジェクト」である（題） | 題 | 採らない。落ちる条件はテストの置き場所ではなく、`scope_command` のランナーがテストのファイルのパスを受けないことである（下の「調べたこと」の表）。置き場所が `tests/` でも、ファイルを受けるランナー（pytest・`python3 -m unittest <ファイル>`）なら落ちない |
| S3 | ファイルを受けないテンプレートで、範囲テストが落ちて改修が fix へ回ることが無い（範囲テストが通るか、宣言の検査の時点で止まり直し方が出る）（受け入れ条件（案）1） | 本文「受け入れ条件（案）」 | 受け入れ条件 1〜3 へ。ただし「宣言の検査の時点」は前提 2 のとおり、着手前に止まることも含める |
| S4 | 宣言の契約（`project.schema.json` の `Suite` の説明と手順 0 の案内）から、`{paths}` にテストのファイルのパスが入ることが分かる（受け入れ条件（案）2） | 本文「受け入れ条件（案）」 | 受け入れ条件 4・5 へ。ただし「ファイルのパスが入る」だけでは足りない（着手前の範囲テストはディレクトリを入れる。下の「調べたこと」）ため、入るパスの種類を `{paths}` を埋める場所ごとに書く条件にする |
| S5 | 受けられないテンプレートを `project-decl.py check`（または cross-refactoring の `init` の着手前の範囲テスト）で見分ける（直す場所） | 本文「直す場所」 | 前提 2・未決 1。見分ける場所と手段は設計が決める |
| S6 | fix のラウンドを上限まで繰り返した原因は、テンプレートがファイルを受けないことである（何を見つけたか） | 本文「何を見つけたか」 | 受け入れ条件 1 の対象。fix の担当が「コード起因の失敗なし・コミットなし」を返しても次のラウンドへ進む振る舞いそのものは含まない（未決 2） |

## 調べたこと

`{paths}` を埋める場所（2026-10-24 の develop のコード）:

| 場所 | `{paths}` に入るもの | 根拠 |
| --- | --- | --- |
| cross-refactoring の改修ごとの範囲テスト（計画・実装・検証） | 計画の `test_targets` の `::` より前のパス（テストのファイル。項目が足すテストのファイルを含む） | `refactor_lib/targets.py` の `limited_runs`・`scope_runs_for` → `test_strategy.scope_runs` |
| cross-refactoring の `init` の着手前の範囲テスト（全体テストを CI に任せる戦略のときだけ） | `--scope` のうちテストの置き場所とみなしたもの（ディレクトリかファイル） | `refactor_lib/baseline.py` の `locations_of`・`commands_of` |
| `test-run.py` の範囲テスト | 呼ぶ側が渡したパス | `plugins/ndf/scripts/test-run.py` |
| 全体テストの組み立て（`command` が無いとき） | `.` | `test_strategy.whole_command_of` |

範囲テストの成否は終了コードだけで決まり（`test_strategy.outcome`）、0 でも 126・127 でもない終了コードは「テストが落ちた（failed）」になる。テンプレートがパスを受けずに 1 や 5 で終わったときも、コードの失敗と同じ扱いで fix へ回る。

unittest の手元の再現（Python 3.14.4。`scripts/storylib/{__init__,x}.py` と `tests/test_x.py`・`tests/test_y.py`）:

| コマンド | 結果 |
| --- | --- |
| `python3 -m unittest discover -s scripts/storylib` | `NO TESTS RAN`、exit 5 |
| `python3 -m unittest discover -s tests/test_x.py` | `ImportError: Start directory is not importable`、exit 1 |
| `python3 -m unittest discover -s tests/test_x.py tests/test_y.py` | 2 つ目を開始のディレクトリと読まず、exit 1 |
| `python3 -m unittest discover -s tests` | exit 0 |
| `python3 -m unittest tests/test_x.py tests/test_y.py` | exit 0 |
| `python3 -m unittest tests`（`__init__.py` の無いディレクトリ） | `NO TESTS RAN`、exit 5 |
| `python3 -m unittest .` | `ValueError: Empty module name`、exit 1 |

unittest には、ファイルとディレクトリの両方を受ける 1 つの書き方が無い。`discover -s {paths}` はファイルを受けず、`python3 -m unittest {paths}` はファイルを受けるがディレクトリと `.` を受けない。

## 目的

- `scope_command` に、NDF が `{paths}` へ入れるパスを受けないテンプレートを書いたプロジェクトでも、cross-refactoring の改修が fix のラウンドを無駄に使わずに進むか、着手の前に止まって直し方が分かるようにする
- 宣言を書く人（手順 0 で解析する AI と、それを確かめる人）が、`{paths}` に何が入るかを宣言の契約から知れるようにする

## 前提

- 前提 1: テンプレートへの加工は「`{paths}` の字句を、シェルの引用で守った対象の並びへ置き換える」ことだけのまま変えない（`test_strategy.py` の I2）。種別と戦略をコマンドの語・ランナーの名前で決めない（I1）。したがって、テンプレートがファイルを受けるかを、コマンドの語（`discover -s` など）を見て判定しない
- 前提 2: 受けられないテンプレートを見分ける時点は、改修のラウンドに入る前（宣言の検査か、cross-refactoring の `init`）のどちらでもよい。どちらにするか、また実際に走らせて見分けるかは設計が決める（未決 1）
- 前提 3: 対象のランナーは unittest の `discover -s` を代表に確かめるが、受け入れ条件はランナーに依らない形で書く（ファイルを受けないランナーは unittest に限らない）
- 前提 4: `scope_command` を持たない suite（`round-only`）と、静的解析の suite（`kind: lint`。`{paths}` に変更したファイルが入る）の振る舞いは変えない

## 対象範囲

含む:
- `{paths}` に入るパスの種類を、埋める場所ごとに宣言の契約（`project.schema.json` の `Suite` の説明と、それを生成する `project_lib/model.py`、`development-workflow` の `references/project-analysis.md` の `test` の行）へ書くこと
- 手順 0 の宣言の作成の案内で、ファイルのパスを受けないランナー（unittest の `discover -s` など）をどう書くかを示すこと
- テンプレートが `{paths}` に入るパスを受けないことを、改修のラウンドに入る前に見分けて止め、直し方を出すこと（場所は前提 2）
- テンプレートの不備で範囲テストが落ちたものを、コードの失敗として fix へ回さないこと

含まない:
- fix の担当が「コード起因の失敗なし・コミットなし」を返しても次のラウンドへ進む、fix のラウンドの止め方そのもの（未決 2）
- `{paths}` に入れるパスの種類を変えること（ファイルをディレクトリへ直すなど）。設計で必要と分かったら、受け入れ条件を書き換えた事実を残して足す
- takemi-ohama/novel の宣言を直すこと（開発対象リポジトリの変更）
- `.ndf/supervise.json` の `test` と `.ndf/project.json` の `test.suites` の二重持ち（#2011）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 宣言に `scope_command` を書いた | 手順 0 の解析か、人の手書き | テンプレートが `{paths}` に入るパスを受けない形で書かれる（今はここで分からない） | — |
| E2 | 宣言を検査した | `project-decl.py check`・戦略の解決（`test_strategy._check_templates`） | 字句の不備（`{paths}` が無い・引用の中）だけで止まる。パスを受けるかは見ない | E1 |
| E3 | 着手前のテストを走らせた | cross-refactoring の `init` | 全体テストが落ちれば止まる。CI に任せる戦略ではディレクトリで範囲テストを走らせる | E2 |
| E4 | 改修の範囲テストを組み立てた | 改修の計画・実装・検証 | `test_targets` が使えなければ項目を見送る | E3 |
| E5 | 範囲テストが落ちた | E4 のコマンドの終了コードが 0・126・127 以外 | テンプレートの不備でもコードの失敗と同じ `failed` になる（この課題の現象） | E4 |
| E6 | 落ちた項目を fix へ回した | E5 | fix の担当がコードの失敗なしを返しても、同じ失敗を繰り返す | E5 |

E2 と E3 のあいだに、テンプレートが `{paths}` に入るパスを受けるかを確かめるイベントが無い。受け入れ条件 1・2 がこれを足す。

## 用語

| 用語 | 意味 |
| --- | --- |
| 範囲テスト | 用語集のとおり。ここでは範囲テストのテンプレートの `{paths}` に対象のパスを入れて走らせるテスト |
| 範囲テストのテンプレート | 用語集のとおり（宣言の `scope_command` か、`{paths}` を含む引数） |
| 着手前のテスト | 用語集のとおり（cross-refactoring の `init` が改修の前に 1 回走らせるテスト） |

## 受け入れ条件

- [ ] 1. `scope_command` が `python3 -m unittest discover -s {paths}` で、`command` が `python3 -m unittest discover -s tests` の宣言を持つ再現のリポジトリ（「調べたこと」の配置）で cross-refactoring を走らせると、改修のラウンドに入る前に、0 以外の終了コードで止まる
- [ ] 2. 受け入れ条件 1 で止まったとき、標準エラーか結果の JSON に、当たった suite の名前・今の `scope_command`・`{paths}` に入れたパス・直す箇所（`.ndf/project.json` の `test.suites[<番号>].scope_command`）が出る
- [ ] 3. 受け入れ条件 1 の宣言で改修の範囲テストが走ることがあっても、テンプレートの不備による失敗の項目が fix の担当へ回らない（fix のラウンドが 0 回である）
- [ ] 4. `project.schema.json` の `Suite` の説明（`project_lib/model.py` から生成したもの）に、`{paths}` に入るパスの種類が、上の「調べたこと」の場所ごと（改修の範囲テスト・着手前の範囲テスト・静的解析）に書かれている。`python3 plugins/ndf/scripts/project-decl.py schema` の出力と公開のスキーマが一致する（`test_project_decl.py` の `test_published_schema_matches_the_model` が通る）
- [ ] 5. `references/project-analysis.md` の `test` の行（手順 0 の案内）に、`{paths}` に入るパスの種類と、ファイルのパスを受けないランナーの書き方（unittest を例に）が書かれている。書いた例のコマンドを再現のリポジトリで走らせ、案内どおりの結果（終了コード）になることを確かめてから書く
- [ ] 6. 退行しない: このリポジトリの宣言（pytest の `scope_command`）で、テストのファイルを `{paths}` に入れた範囲テストが今と同じコマンドで組まれ、受け入れ条件 1 の止まり方が起きない
- [ ] 7. 退行しない: 静的解析の suite（`kind: lint`）と `scope_command` の無い suite（`round-only`）の範囲テストの組み立てが変わらない（既存のテストが通る）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 受け入れ条件 1 の見分けのために足す実行は、cross-refactoring の 1 回の起動につき suite ごとに高々 1 回とし、その所要を着手前のテストの上限（`INIT_TEST_SHARE`）の中に収める |
| 運用・保守性 | 見分けた結果（止めた理由・当たった suite）が結果の JSON か状態ファイルに残り、後から読める |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。cross-refactoring が改修の前に止まる場合が増える（宣言のテンプレートが `{paths}` に入るパスを受けないとき）。宣言の形（`project.schema.json` のキー）は変えない。今まで落ちていた宣言だけが止まるため、互換の経路は要らない |
| データ | 変わらない（`.ndf/project.json` の形は同じ） |
| 既存の振る舞い | テンプレートの不備で落ちていた範囲テストが fix へ回らず、着手前に止まる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4` |
| 静的解析 | `python3 scripts/check-script-structure.py`・`python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .` |
| スキーマ | `python3 plugins/ndf/scripts/project-decl.py schema --out plugins/ndf/skills/development-workflow/schemas/project.schema.json` で作り直し、`test_published_schema_matches_the_model` が通ること |
| 手動確認 | リリース後テストで、unittest の `discover -s {paths}` を宣言に書いた再現のリポジトリ（または takemi-ohama/novel の写し）で `/ndf:cross-refactoring` を走らせ、受け入れ条件 1・2 の止まり方と出力を見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 範囲テストの組み立ては `plugins/ndf/scripts/lib/test_strategy.py` に寄せる（I10。cross-refactoring・supervise・`test-run.py`・cross-review が同じ関数を使う）。Skill ごとに見分けの関数を分けない（MVV の Value 6） |
| コーディング規約 | 標準ライブラリだけで書く（`test_strategy.py` の冒頭）。`AGENTS.md` の「コードやスクリプトが呼ぶ外部コマンドも、書く前に実行して挙動を確かめる」 |
| テスト戦略 | テンプレートの見分けは単体テスト（`plugins/ndf/scripts/tests/test_lib_test_strategy.py` の並び）で、止まり方は cross-refactoring の `init` の結合のテストで確かめる。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存のテストの実行、案内に書くコマンドの事前の実行 |
| 確認してから行う | cross-refactoring が止まる条件を増やすこと（承認ゲート 1 で人が見る） |
| 行わない | コマンドの語・ランナーの名前によるテンプレートの判定（前提 1）、開発対象リポジトリの宣言の書き換え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. テンプレートがパスを受けるかを見分ける場所（`project-decl.py check` / 戦略の解決 / cross-refactoring の `init`）と手段（1 つのテストのファイルで実際に走らせるか、ほかの手段か）。全体テストと範囲テストの結果が食い違ったときにテンプレートの不備とみなす規則を含む | 設計（`design`） | 承認ゲート 1 |
| 2. fix の担当が「コード起因の失敗なし・コミットなし」を返したのに同じ失敗で次のラウンドへ進む振る舞いを、この課題で止めるか、別の課題にするか | 設計（`design`）。別にするなら `out-of-scope` で起票する | 承認ゲート 1 |
| 3. `{paths}` に入るパスの種類をそろえる（例: 着手前の範囲テストもファイルを入れる）かどうか。そろえれば unittest の `python3 -m unittest {paths}` が 1 つの書き方で通る | 設計（`design`） | 承認ゲート 1 |

## MVV との突き合わせ

反する疑いは無い。どのプロジェクトのランナーでも同じ工程を通すこと（Value 5）、現象の場所（fix のラウンド）ではなく宣言の契約と範囲テストの組み立ての共通の層で直すこと（Value 6）、fix の時間を無駄にしないこと（Mission）に沿う。共通原則の C1〜C8 と P1 に当たる操作は含まない（利用者の `.ndf/` は書き換えず、NDF のテンプレートと説明だけを変える）。
