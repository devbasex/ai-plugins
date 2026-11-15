# #3616: ndf-next の CI の修正: 落ちたテストを pytest のログの形でしか読めず、vitest・jest・PHPUnit のリポジトリで毎回止まる

正は課題の本文（#3616）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者（並列の計画 p6 のオーガナイザー）が推して足した条件。この課題で利用者が書いた文は「依頼（原文）」の引用 1 文だけである。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、要求を書いた者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 落ちたテストを、設定ファイルの `test.ci.junit_artifacts` が指す JUnit の成果物（GitHub Actions の artifact）から読む | 旧い本文の「対象範囲」「受け入れ条件」1 行目 | 受け入れ条件 1〜6 |
| S2 | 対象のランナーは vitest・jest・PHPUnit・pytest の 4 つ | 旧い本文の「受け入れ条件」1 行目 | 目的の文が名指しする playwright を足し、5 つにする（受け入れ条件 1）。nd-factory の設定ファイルに playwright の suite（`web-e2e`）がある |
| S3 | ランナーごとのログの読み方をアダプタに分ける | 旧い本文の「対象範囲」 | 受け入れ条件 7〜9。部品の呼び名は、用語集の「アダプター」（外部ライブラリを呼ぶ唯一のモジュール）と重ならないよう「ログのパーサー」とする |
| S4 | JUnit の成果物の設定も読めるログの形も無いときは、足りない設定のキーを出して止まる | 旧い本文の「受け入れ条件」2 行目 | 受け入れ条件 10・11 |
| S5 | cross-refactoring の収集で、許す終了コードを suite の設定から読み、設定が無ければ 0 だけを成功にする | 旧い本文の「受け入れ条件」3 行目 | 受け入れ条件 12〜14 |
| S6 | jest は収集が 0 件のとき終了コード 1 を返す | 旧い本文の表の 12 番 | 前提 6（未確認として扱う） |
| S7 | CI のサービスは GitHub Actions だけを扱う | 旧い本文の「対象範囲」の「含まない」 | 含まない |
| S8 | ndf-next は未リリースなので互換は要らない | 旧い本文の「影響」 | 前提 1 |
| S9 | 設計の「適用範囲」に、Web サービス・モバイルアプリ・業務文書だけのリポジトリの 3 種での振る舞いの表と「ai-plugins だけの事情」の欄を書く | 「設計への指示」 | 受け入れ条件 16 |
| S10 | ai-plugins の値（pytest の終了コード 5 など）は ai-plugins の `.ndf/` の設定へ移す | 「設計への指示」 | 前提 5・受け入れ条件 15 |
| S11 | #1142 の本文の子の表と `plugins/ndf-next/src/ndf/checks/children.json` へこの課題を足す | 「設計への指示」 | 受け入れ条件 17 |
| S12 | 文書と承認の資料は業界の語で書き、#3613 の対応表に合わせる | 「設計への指示」 | 前提とする取り決め |
| S13 | 今の作りは ai-plugins の CI（pytest）で作って試したため、ほかのランナーで確かめていない | 旧い本文の「起票者の推定」 | 目的の背景として扱う。受け入れ条件 1 で 5 つのランナーの見本を通して確かめる |

## 目的

ndf-next（`plugins/ndf-next/`。11.0 で今の `plugins/ndf/` と入れ替わる作り直しの NDF）の CI の修正のループを、pytest 以外のテストランナーを使うリポジトリでも人へ戻さずに回す。

- 今は、CI で落ちたテストを読む処理が pytest のログの行（`FAILED <パス>.py::<名前>`）しか読まない（`src/ndf/adapters/ci.py:58` の `_PYTEST_FAILED`、`:265` の `failed_test_nodes`）
- そのため vitest・jest・PHPUnit・playwright のリポジトリでは、CI の修正の後に手元で確かめるステップ（プランの雛形のステップ `ci-local`。`contracts/templates/parts/steps.json:73`）が、サブコマンド `ndf route test --failed-from` の「落ちたテストが無い」で毎回 `precondition` に止まる（`src/ndf/core/route/local_tests.py` の `_scoped_plan`）
- 設定ファイル（`.ndf/project.json`）のスキーマには JUnit の成果物を指すキー `test.ci.junit_artifacts` があるが、CI の結果を読む側でこのキーを読む箇所が無い（`grep -rn junit plugins/ndf-next/src` の当たりは、CI の所要時間を測る `src/ndf/domains/project/measure_ci.py` だけ）
- cross-refactoring の収集の確かめは、終了コード 5（pytest の「収集が 0 件」）をどのランナーでも成功と読む（`src/ndf/domains/crossrefactor/collect.py:28` の `COLLECT_OK = frozenset({0, 5})`）
- #1142（ndf-next への作り直し）の受け入れ条件「同等。既知の不具合は引き継がない」に当たる。発端は利用者の指摘（「依頼（原文）」）で、ai-plugins を開発する事情（pytest・終了コード 5）が、NDF を汎用に使う側の振る舞いに入り込んでいる

## 前提

- 前提 1: ndf-next は未リリースのため、CI の結果 JSON・設定ファイルのスキーマ・`ndf route test --failed-from` の入力の形を変えても、互換の経路は持たない
- 前提 2: 落ちたテストの一覧の要素は、今の CI の結果 JSON の `metrics.failed_tests` と同じ形（`<テストのファイル>::<テストの名前>`。ファイルは作業ディレクトリの根からの相対パス）にそろえる。`ci-local` がファイルを取り出す処理（`::` より前）は、ランナーに依らず同じに保つ
- 前提 3: 設定ファイルに `test.ci.junit_artifacts` があれば、JUnit の成果物をログより先に読む。成果物を読めない（成果物が無い・期限切れ・権限が無い）ときはログの読み方へ移り、どちらでも読めなければ受け入れ条件 10 のとおり止まる
- 前提 4: ログのパーサーは、落ちたテストのチェックに当たる suite の `runner`（設定ファイルの `test.suites[].runner`）で選ぶ。チェックと suite の対応の決め方は設計で決める
- 前提 5: ai-plugins の設定ファイルには、収集の確かめのコマンド（`test.suites[].collect_command`）を持つ suite が無い（`grep -rn collect_command .ndf/` の当たりは 0 件）。そのため、終了コード 5 を ai-plugins の設定へ移すのは、ai-plugins が `collect_command` を足すときに同じ suite へ書く形になる。この課題では ai-plugins に `collect_command` を足さない
- 前提 6: 「jest は収集が 0 件のとき終了コード 1 を返す」は未確認である。収集の許す終了コードはランナーの事実を NDF が持たず、利用者が suite の設定に書くので、この事実の真偽は受け入れ条件に影響しない
- 前提 7: JUnit の XML で落ちたテストは、`<testcase>` の子に `<failure>` か `<error>` を持つものとする。`<skipped>` だけを持つ testcase は落ちたテストに数えない
- 前提 8: 代表のリポジトリの今の状態は次のとおり（2026-11-15 に確かめた）。設計の「適用範囲」の表の材料にする

| リポジトリ | 確かめたこと | 根拠 |
| --- | --- | --- |
| `/work/nd-factory`（Web サービス。pytest・vitest・playwright） | CI は JUnit の成果物を上げていない | `.github/workflows/*.yml` に `upload-artifact` の当たりが 0 件。pytest の行（`ci.yml:39`）だけが `--junitxml` を書く |
| `/work/carmo-system-console`（Laravel・PHPUnit） | CI は `junit-*` の名前で JUnit の成果物を上げている。NDF の設定ファイル（`.ndf/project.json`）は無い | `.github/workflows/laravel.yml:87`・`:619` の `actions/upload-artifact@v4`、`:621` の `name: junit-dlite-…` |

## 対象範囲

含む:

- JUnit の成果物（設定ファイルの `test.ci.junit_artifacts`）から、落ちたテストの一覧を読むこと
- 落ちたテストをログから読む処理を、ランナーごとのパーサーに分けること（pytest の今の読み方はその 1 つになる）
- 落ちたテストを読めないとき、足りない設定のキーを出して止めること
- cross-refactoring の収集の確かめで、許す終了コードを suite の設定から読むこと。そのためのキーを設定ファイルのスキーマへ足すこと
- 設計の「適用範囲」に、代表のリポジトリでの振る舞いの表と「ai-plugins だけの事情」の欄を書くこと
- #1142 の本文の子の表と `plugins/ndf-next/src/ndf/checks/children.json` へこの課題を足すこと

含まない:

- GitHub Actions 以外の CI のサービスへの対応
- 今の `plugins/ndf/`（11.0 で入れ替わる）
- 各リポジトリの CI に JUnit の成果物を上げるジョブを足すこと（利用者のリポジトリの設定であり、NDF は足りないキーを知らせるまでにする）
- ai-plugins の設定ファイルへ `collect_command` を足すこと（前提 5）
- ndf-next 全体の語の置き換え（#3613 が持つ）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | PR の CI でテストのチェックが落ちた | PR への push | — | — |
| E2 | 落ちたテストのチェックのジョブを特定した | CI を待つサブコマンド（`ndf ci`）が結論を読んだ | ジョブを読めなければ、落ちたテストは空のまま理由を `log_error` に残す（今の振る舞い） | E1 |
| E3 | JUnit の成果物から落ちたテストを読んだ | 設定ファイルに `test.ci.junit_artifacts` がある | 成果物を読めなければ E4 へ移る（前提 3） | E2 |
| E4 | ジョブのログから、ランナーのパーサーで落ちたテストを読んだ | E3 が無いか失敗した | 読めるパーサーが無い・読めた件数が 0 なら、一覧は空になる | E2 |
| E5 | 落ちたテストの一覧を CI の結果 JSON へ書いた | E3 か E4 が終わった | — | E3 か E4 |
| E6 | CI の修正の後、落ちたテストのファイルを suite の範囲のテストで打った | プランのステップ `ci-local` | 一覧が空なら、足りない設定のキーを出して `precondition` で止まる | E5 |
| E7 | cross-refactoring の収集の確かめのコマンドが終わった | 足したテストの収集を確かめる工程 | 許す終了コードの外なら `CollectError` で止まる | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| JUnit の成果物 | CI のジョブが上げた artifact のうち、JUnit の XML を持つもの。設定ファイルの `test.ci.junit_artifacts` が名前の glob で指す |
| ログのパーサー | 1 つのテストランナーの CI のログの形から、落ちたテストの一覧を読む部品。suite の `runner` ごとに 1 つ |
| 収集の確かめ | cross-refactoring が、足したテストを全体の実行が収集するかを suite の `collect_command` で確かめる工程 |

## 受け入れ条件

JUnit の成果物から読む:

- [ ] 1. vitest・jest・PHPUnit・pytest・playwright の JUnit の XML の見本（各 1 件以上の落ちたテストと、1 件以上の通ったテスト・`<skipped>` のテストを含む）から、落ちたテストだけを `<テストのファイル>::<テストの名前>` の形の一覧で返す（テスト。ランナーごとに 1 件）
- [ ] 2. 見本の XML は、各ランナーを実際に動かして書かせたものか、ランナーの公式の文書に載る形のものとし、テストの近くにどちらかを書く
- [ ] 3. testcase のファイルが絶対パス（PHPUnit の `file` 属性など）のとき、一覧の要素は作業ディレクトリの根からの相対パスになる（テスト）
- [ ] 4. 1 つの testcase からファイルを決められない（属性にファイルが無い）とき、その testcase の名前と、足りない属性を止まった理由に出す（テスト）
- [ ] 5. 分割ジョブなどで JUnit の XML が複数あるとき、一覧は重ねずに昇順で並ぶ（テスト）
- [ ] 6. 設定ファイルに `test.ci.junit_artifacts` があり、名前の当たる成果物が無いときは、ログのパーサーで読む（テスト）

ログから読む:

- [ ] 7. pytest のログからは、今と同じ一覧を返す（退行しない）
      前提の確認: 今の振る舞いはテスト `tests/adapters/test_ci.py::test_failed_test_nodes_and_files_from_log` が固定している（手元でテストを走らせない設定のため、実行は CI で確かめる）
- [ ] 8. suite の `runner` にパーサーがあれば、そのパーサーでログを読む。`runner` にパーサーが無ければ、ログを読まずに一覧を空にする（pytest の形を当てない）（テスト）
- [ ] 9. ログのパーサーの一覧は 1 か所にあり、パーサーを 1 つ足すときに CI の結果を読む処理を書き換えない（テスト: 一覧へ試験用のパーサーを足すと、そのランナーのログが読まれる）

読めないとき:

- [ ] 10. 落ちたテストのチェックがあるのに一覧が空で、設定ファイルに `test.ci.junit_artifacts` が無いとき、`ndf route test --failed-from` は打たずに `precondition` で止まり、止まった理由に設定のキー `test.ci.junit_artifacts` と、ログを読めなかった suite の `runner` を出す（テスト）
- [ ] 11. 設定ファイルに `test.ci.junit_artifacts` があって成果物とログのどちらでも読めないとき、止まった理由に成果物の名前の glob と、読めなかった理由を出す（テスト）

収集の確かめ:

- [ ] 12. suite の設定に許す終了コードの並び（キーの名前は設計で決める）があれば、その並びの終了コードだけを収集の成功と読む（テスト）
- [ ] 13. 許す終了コードの設定が無い suite では、0 だけを成功と読み、5 は `CollectError` で止まる（テスト）
- [ ] 14. 設定ファイルのスキーマに 12 のキーがあり、整数の並び以外の値はスキーマの検査で落ちる（テスト）
- [ ] 15. ndf-next の `src/` に、終了コード 5 を収集の成功として持つ定数が残らない（`grep` の結果を PR に添える）

設計と記録:

- [ ] 16. 設計の「適用範囲」に、Web サービス（TypeScript・vitest か jest・develop→main・OpenAPI）・モバイルアプリ（Flutter か Swift）・業務文書だけのリポジトリの 3 種と、ai-plugins の 4 行で、直した後に CI の修正のループで何が起きるかの表があり、表に「ai-plugins だけの事情」の欄がある
- [ ] 17. #1142 の本文の子の表と `plugins/ndf-next/src/ndf/checks/children.json` にこの課題があり、子の表の検査（`ndf check`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 成果物の取得は、今のログの取得と同じ上限（`limits.json` の `github.failed-logs` の件数）に収める |
| 運用・保守性 | 止まった理由の文から、利用者が設定ファイルのどのキーを足せばよいかが分かる（受け入れ条件 10・11） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。設定ファイルのスキーマに収集の許す終了コードのキーが増え、`ndf route test --failed-from` の止まった理由の文が変わる。互換なし（前提 1） |
| データ | CI の結果 JSON の `metrics.failed_tests` の要素の形は変わらない（前提 2）。読む元に JUnit の成果物が増える |
| 既存の振る舞い | pytest 以外のランナーのログに pytest の形を当てなくなる。収集の確かめで、設定の無い suite の終了コード 5 が成功から `CollectError` に変わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | PR の CI（pytest のジョブ）。このリポジトリは手元でテストを走らせない設定（`test.ci.only`） |
| 静的解析・型検査 | `python3 /persistent/ai/.claude/plugins/cache/ai-plugins/ndf/10.27.70/scripts/test-run.py scope --paths <変更したパス>` |
| 手動確認 | リリース後テストで、JUnit の成果物を上げる PHPUnit のリポジトリ（`/work/carmo-system-console` の形）の CI の落ちを `ndf route test --failed-from` に渡し、`precondition` で止まらずに落ちたテストのファイルを打つことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | CI と GitHub の読み取りは外部との接続の層（`src/ndf/adapters/`）に置き、ランナーの事実は suite の設定から読む。ai-plugins の事情（pytest・終了コード 5）を `src/` に持ち込まない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。JUnit の XML の読み取りは標準ライブラリか既存の依存のライブラリから選び、選んだ理由を設計に書く（MVV の Value 12） |
| テスト戦略 | ランナーごとの見本の XML とログを入力にした単体テストで受け入れ条件 1〜13 を確かめる。`.md` の文言を照合するテストは書かない |
| 語 | 文書と承認の資料は業界の語で書く。基準は `.ndf/handoff/lepic-note-terms.md`（「完了条件」はそのまま）と #3613 の対応表 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 変更した範囲の静的解析、受け入れ条件ごとのテストの追加 |
| 確認してから行う | 設定ファイルのスキーマのキーの追加（設計承認ゲートで見る）。ai-plugins の `.ndf/` の書き換え（リリース承認ゲートで見る） |
| 行わない | 利用者のリポジトリの CI の書き換え、GitHub Actions 以外の CI への対応、#3613 の範囲の語の一斉置き換え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 収集の許す終了コードのキーの名前と置き場（suite の下か `collect_command` と組か） | 設計 | 設計承認ゲート |
| 落ちたテストのチェックと suite の対応の決め方（`ci_jobs` を使うか、チェックの名前か） | 設計 | 設計承認ゲート |
| JUnit の XML を読むライブラリ | 設計 | 設計承認ゲート |
| vitest・jest・PHPUnit・playwright のログのパーサーをこの課題でいくつ持つか（JUnit の成果物を推す場合、ログのパーサーは pytest だけでもよいか） | 設計 | 設計承認ゲート |

## 依頼（原文）

> ai-plugins(ndf) を開発することと、ndf を使って様々な開発を汎用的に進行することの区別がついていない設計が入り込んでしまう

（書き手: 利用者、日付: 2026-11-16（JST）。起票者が本文の「設計への指示」に引用したもの。点検の一覧は #3617 の本文）

> #3616。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。本文を書いたら `spec-copy.py write 3616 issues/issue-3616-requirements.md` で写しを作り、コミットする（push しない）。

（書き手: AI の起動指示、日付: 2026-11-15。利用者の指示の原文として扱わない）

起票の時点の本文（書き手: 起票者＝並列の計画 p6 のオーガナイザー、日付: 2026-11-16（JST））:

> ndf-next が CI で落ちたテストを読む処理は、pytest のログの形（`FAILED <x>.py::`）しか読めない。vitest・playwright・PHPUnit・jest のリポジトリでは CI の修正の後の確かめ（`ci-local`）が毎回 `precondition` で止まり、CI の修正のループが人へ戻る。#1142 の受け入れ条件（同等。既知の不具合は引き継がない）に当たる。
>
> | # | 場所 | ai-plugins の前提 | 汎用のリポジトリで起きること |
> | --- | --- | --- | --- |
> | 3 | `src/ndf/adapters/ci.py:58`（`_PYTEST_FAILED`）・`:266`・`src/ndf/core/route/__init__.py:35-39`・`contracts/templates/parts/steps.json:73` | 落ちたテストを pytest の行からしか拾わない | `route test --failed-from` が「落ちたテストが無い」で `precondition`。宣言のスキーマに `ci.junit_artifacts` があるのに、`src/` に `junit` を読む箇所が 0 件 |
> | 12 | `domains/crossrefactor/collect.py:27`（`COLLECT_OK = {0, 5}`） | 終了コード 5 を pytest の「収集 0 件」として全ランナーで受ける | ほかのランナーの 5 を成功と読む恐れ。jest は 0 件で 1 を返して止まる |
>
> 対象範囲
> - 含む: JUnit の成果物（宣言の `ci.junit_artifacts`）から落ちたテストを読むこと、ランナーごとのログの読み方をアダプタに分けること、suite の宣言に許す終了コードを持たせること
> - 含まない: CI のサービス（GitHub Actions 以外）への対応
>
> 受け入れ条件
> - [ ] vitest・jest・PHPUnit・pytest の JUnit の XML（見本）から、落ちたテストの一覧を同じ形で返す（テスト）
> - [ ] JUnit の成果物の宣言が無く、ログの形も読めないときは、何が足りないか（宣言のキー）を出して止まる
> - [ ] cross-refactoring の収集で、許す終了コードを suite の宣言から読み、宣言が無ければ 0 だけを成功にする
>
> 設計への指示（オーガナイザー、2026-11-16 02:4x JST）
> - 設計の「適用範囲」に、代表のリポジトリでの振る舞いの表を必ず書く（#3618 の設計の型を先取り）。少なくとも次の 3 種で、直した後に何が起きるかを書く
>   - Web サービス（TypeScript・vitest/jest・develop→main・OpenAPI）
>   - モバイルアプリ（Flutter か Swift）
>   - 業務文書だけのリポジトリ
>   - 参照してよい実物: `/work/nd-factory`（`promotion: selected`・`base_branch: qa/dev`）・`/work/carmo-system-console`（Laravel・PHPUnit）
> - 表に「ai-plugins だけの事情」の欄も書き、ai-plugins の値（pytest の終了コード 5 など）は ai-plugins の `.ndf/` の宣言へ移す
> - 文書と承認の資料は業界の語で書く（基準は `.ndf/handoff/lepic-note-terms.md`。「完了条件」はそのまま）。#3613 が ndf-next 全体の語を置き換えるので、新しく書く語はその対応表に合わせる
> - #1142 の受け入れ条件に入る（ラベル `rebuild-1142`）。#1142 の本文の子の表と `plugins/ndf-next/src/ndf/checks/children.json` へこの Issue を足す（行き先の表の決まりに従う）
> - 発端は利用者の指摘「ai-plugins(ndf) を開発することと、ndf を使って様々な開発を汎用的に進行することの区別がついていない設計が入り込んでしまう」（点検の一覧は #3617 の本文）
>
> 影響: `route test --failed-from` と `ci-local` の入力の形が増える。ndf-next は未リリースなので互換は要らない。
>
> 起票者の推定: ai-plugins の CI（pytest）で作って試したため、ほかのランナーで確かめていない。
>
> 由来: 並列の計画 p6 のオーガナイザーの点検（2026-11-16）。#3615 と同じ点検の 3・12 番。
