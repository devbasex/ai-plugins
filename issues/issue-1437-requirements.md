# #1437: supervise new sprint: --test-cmd が静的解析のとき、実装と検査の全体テストが {paths} 空のまま組まれて常に落ちる

正は課題の本文（#1437）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何が起きたか
>
> volareinc/carmo-cdk で `supervise.py new sprint ... --test-cmd 'uvx --from shellcheck-py shellcheck -s bash {paths}' --tests images/redmine7/postresync.sh` を流した（ndf 10.17.40、2026-09-28）。範囲テストは通ったが、実装プランの `test-all`（`test-run.py whole --template ...`）が 0.0 秒で落ち、judge が「JUnit の宣言が無く、原因を決められない」で stop した。
>
> `test-run.py whole` は範囲ではなく全体を流すため、雛形の `{paths}` に全体（`.`）が入り `shellcheck .` になる。shellcheck はディレクトリを読めず終了コード 2 で、変更と無関係に常に落ちる。`.ndf/project.json` の `test.suites[].command`（pytest）は使われなかった。
>
> ## 期待する振る舞い
>
> - 全体テストは宣言の `suites[].command` を使う（`--help` の「全体テストは宣言の suites[].command」のとおり）。`--test-cmd` の雛形から全体テストを組まない
> - 雛形しか無いときは、`{paths}` を `--tests` のパスで埋めるか、全体テストを「無い」として飛ばし、その旨を報告に残す
>
> ## 関連
>
> #1434（cross-refactoring の着手前テストと最終ゲートで同じ `shellcheck .` が起きた）
>
> 親 issue: #1483（修正レイヤー: テストの戦略の宣言と解釈（`scripts/lib/test_strategy.py` の `_from_args` と `.ndf/project.json` の `test`））

## 現状（2026-09-30 に develop の `dfeb3517` で確かめた）

親の #1483（閉じ済み）で、suite の種別（`test` / `lint`）と `--test-kind` が入った。`lib/test_strategy.resolve` を `--test-cmd` の雛形 `uvx --from shellcheck-py shellcheck -s bash {paths}`・`--tests images/redmine7/postresync.sh` で 4 通りに解いた結果は次のとおり。

| 宣言の `test` | `--test-kind` | 全体テスト（テスト） | 全体テスト（静的解析） | 報告の注記 |
| --- | --- | --- | --- | --- |
| あり（pytest） | test（既定） | 宣言の `command`（pytest） | 無し | 無し |
| あり（pytest） | lint | 宣言の `command`（pytest） | `shellcheck ... images/redmine7/postresync.sh` | 無し |
| 無し | test（既定） | **`shellcheck -s bash .`** | 無し | **無し** |
| 無し | lint | 無し | `shellcheck ... images/redmine7/postresync.sh` | 無し |

- 期待の 1 つ目（宣言があれば全体テストは宣言の `command`）は満たされている。報告の事例（carmo-cdk は宣言あり）は、今の develop では pytest が全体テストになる
- 期待の 2 つ目のうち、静的解析の雛形（`--test-kind lint`）は `{paths}` を `--tests` で埋め、`--tests` が無ければ全体テストを組まずに注記を残す（`NO_LINT_WHOLE`）
- **残る隙間は 3 行目である。** 宣言が無く、静的解析のコマンドを `--test-kind` を付けずに（既定の test として）渡すと、全体テストは `{paths}` を `.` にした `shellcheck -s bash .` になり、常に落ちる。そのとき報告にも計画にも、全体テストを雛形から組んだことが残らない
- 種別はコマンドの語から推し量らない（#1483 の I1）。このため、3 行目を `shellcheck` の語で見分けて直すことはしない

## 目的

- `--test-cmd` の雛形から全体テストを組むとき、それが報告と計画の画面に現れ、利用者と judge が「雛形の `{paths}` を `.` にしたコマンドが全体テストである」と分かる状態にする
- 報告の事例（宣言あり）と静的解析の雛形の扱いが、今後の変更で退行しないよう回帰テストで固定する

## 前提

- 前提 1: 種別の判定はコマンドの語を見ない（#1483 の I1 を守る）。`--test-kind` を付けずに渡した静的解析のコマンドを、NDF は自動で静的解析と扱わない
- 前提 2: 宣言が無く `--test-kind test` の雛形から全体テスト（`{paths}` を `.`）を組む振る舞いは残す。pytest のような雛形では `.` が正しい全体テストであり、これを止めると宣言の無いプロジェクトで全体テストが消える
- 前提 3: 注記は既存の経路（`Strategy.notes` → `test-run.py` の結果の `items` の `note`）へ載せる。新しい出力の欄は作らない
- 前提 4: 報告の事例で judge が「JUnit の宣言が無く原因を決められない」で止まった判定の扱い（静的解析の失敗の見分け）は #1483 の `lint_verdict` が持ち、この課題では変えない

## 対象範囲

含む:
- 宣言が無く、`--test-cmd`（または `test-run.py --template` / cross-refactoring の `--baseline-test`・`--round-test` の雛形）の種別が test で、全体テストを雛形の `{paths}` を `.` にして組んだときの注記
- `supervise.py new`（impl・fix・check・sprint・close）が計画を作るとき、その注記を標準エラーへ 1 度出すこと
- 同じく、戦略のほかの注記（`--test-kind lint` で範囲のパスが無いときの `NO_LINT_WHOLE` を含む）も標準エラーへ 1 度出すこと（設計の決定 3。設計の承認で範囲に加えた）
- 報告の事例（宣言あり＋雛形）と静的解析の雛形（`--tests` あり・なし）の回帰テスト

含まない:
- コマンドの語から種別を推し量ること（前提 1）
- 雛形から全体テストを組む振る舞いそのものの廃止（前提 2）
- cross-refactoring の着手前テスト・最終ゲート・テスト整備ラウンドの判定（#1434）
- judge の判定規則と `lint_verdict` の変更（前提 4）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者が `--test-cmd` の雛形と種別を渡した | `supervise.py new` / `test-run.py --template` / cross-refactoring の起動 | 雛形の不備（`{paths}` が無いなど）は `StrategyError` で止まる（既存） | — |
| E2 | 戦略が全体テストを決めた | E1 と宣言の読み取り | 宣言が壊れていれば `StrategyError`（既存） | E1 |
| E3 | 全体テストを雛形から組んだことが注記として戦略に載った | E2 で、宣言の `command` が無く種別が test | —（注記を足すだけで失敗の経路は無い） | E2 |
| E4 | `supervise.py new` が計画を書き、注記を標準エラーへ出した | 計画の作成 | 計画の作成が失敗すれば既存どおり止まる | E3 |
| E5 | `test-run.py whole` が全体テストを走らせ、結果の `items` に注記を残した | 実装プランの `test-all` ステップ | 全体テストが落ちれば既存どおり `failed` / `launch_failed` を返し、注記は落ちたときも残る | E3 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 全体テスト | 範囲を絞らずに走らせるテストか静的解析。宣言の `suites[].command`、無ければ雛形から組む |
| 範囲テスト | 雛形の `{paths}` を対象のパスで埋めて走らせるテストか静的解析 |
| 範囲テストの雛形 | `{paths}` を 1 語で含むコマンド。この課題では引数（`--test-cmd` / `--template` / `--baseline-test` / `--round-test`）で渡すもの |

## 受け入れ条件

- [ ] AC1: 宣言が無く、種別 test の雛形 `shellcheck -s bash {paths}` で `test_strategy.resolve` を解くと、`Strategy.notes` に「全体テストは雛形の `{paths}` を `.` にしたコマンドで、静的解析なら `--test-kind lint` か宣言の `test.suites` を使う」旨の注記が 1 件入る
- [ ] AC2: AC1 と同じ入力で `test-run.py whole --template 'shellcheck -s bash {paths}'` を宣言の無いリポジトリで走らせると、結果 JSON の `items` に AC1 の注記が `note` として入る（全体テストが落ちたときも入る）
- [ ] AC3: 宣言が無く `supervise.py new sprint --test-cmd 'shellcheck -s bash {paths}' --tests a.sh` で計画を作ると、標準エラーに AC1 の注記が 1 度出る。計画の作成は止まらない（終了コードは注記の無いときと同じ）
- [ ] AC4: 宣言の `test.suites[].command` があるとき、雛形の種別が test でも lint でも、`resolve` の全体テスト（テスト）は宣言の `command` だけで、雛形の `{paths}` を `.` にしたコマンドを含まない。AC1 の注記は出ない（報告の事例の回帰）
- [ ] AC5: 宣言があり `--test-kind lint`・`--tests images/redmine7/postresync.sh` の `supervise.py new sprint` の計画で、`test-all` ステップのコマンドが `--paths images/redmine7/postresync.sh` を含み、`resolve` の全体テスト（静的解析）が `{paths}` をそのパスで埋めたものになる（`.` を含まない）
- [ ] AC6: `--test-kind lint` で範囲のパスが無いとき、静的解析の全体テストは組まれず、注記 `NO_LINT_WHOLE` が結果の `items` に残る（既存の振る舞いの回帰）
- [ ] AC7: 宣言が無く種別 test の雛形 `pytest {paths} -q` の全体テストは、これまでどおり `pytest . -q` である（前提 2 の退行なし）
- [ ] AC8: 全体テスト（`uv run --frozen --project . --all-extras pytest . -q -n 4`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 注記の文は `lib/test_strategy.py` の定数 1 か所に置き、`supervise` と `test-run.py` は `Strategy.notes` を読むだけにする（写さない） |
| 移行性 | 既存の宣言・引数・結果 JSON の欄は変えない。`items` に `note` が 1 件増えるだけである |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（引数・終了コード・結果 JSON の欄は同じ。`note` の件数が増える場合がある） |
| データ | 無し |
| 既存の振る舞い | 宣言が無く種別 test の雛形を渡したとき、注記が増える。全体テストのコマンドは変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_lib_test_strategy.py plugins/ndf/scripts/tests/test_supervise_new.py plugins/ndf/scripts/tests/test_test_run.py -q`（範囲）、全体は AC8 のコマンド |
| 静的解析 | `ruff check plugins/ndf/scripts` |
| 手動確認 | 無し（すべて自動テストで判定する） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 戦略の判断は `plugins/ndf/scripts/lib/test_strategy.py` に置く（#1483 の I10: cross-refactoring・supervise・`test-run.py`・cross-review が同じ関数を使う）。呼ぶ側に判定を写さない |
| コーディング規約 | `pyproject.toml` の `[tool.ruff]`。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |
| テスト戦略 | `resolve` の単体テストで AC1・AC4・AC7、`test-run.py` と `supervise.py new` の結合テスト（一時リポジトリ）で AC2・AC3・AC5・AC6 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 範囲テストと全体テストの実行、ruff の適用 |
| 確認してから行う | 全体テストを雛形から組む振る舞いを止めること、結果 JSON の欄の追加 |
| 行わない | コマンドの語による種別の推測、#1434 の範囲（cross-refactoring の判定）への変更、judge の規則の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 注記の出し方を標準エラーの 1 行に留めるか、計画の JSON にも残すか | 設計（`design`）で決める | 設計 PR まで |

## 関連

- #1434（cross-refactoring の着手前テストと最終ゲートで同じ `shellcheck .` が起きた）
- 親 issue: #1483（閉じ済み。suite の種別と `--test-kind` を入れた）
