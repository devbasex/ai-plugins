# 用語集: NDF の CI とテスト（`ndf-ci-test`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

テストの戦略・範囲テストと全体テスト・CI のチェックの待ちと必須のチェックの突き合わせ・差分の種類の語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| スタックしたチェック | — | 実行が終わったのに pending のまま残った CI のチェック | 取り残されたチェック | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| テストダブル | — | テストが本物の代わりに差し込む偽の相手 | — | — | `docs/specifications/ndf-live-samples-verify.md` |
| 見本 | — | 本物の相手の入出力を記録した JSON。テストダブルはこれだけから応答を組み立てる | — | — | `docs/specifications/ndf-live-samples-verify.md` |
| テストと CI の予算 | — | テストの関数・CI のジョブ・使われない依存・置き換えられる自作の件数の上限 | — | — | `docs/specifications/ndf-split-tables-toolchain-and-budget.md` |
| 文言固定テスト | — | リポジトリで追跡している .md を読み、その文字列・見出し・表の並びを検証するテスト。書かない | — | — | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| 範囲テスト | — | 変更が触った範囲に限って走らせるテスト | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| suite の種別 | `kind` | テストの設定（.ndf/project.json の test.suites[]）の 1 件がテスト（test）か静的解析（lint。整形の検査を含む）か。キーは kind。書かなければ test | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 起動の失敗 | — | テストのコマンドのプロセスを起動できない、またはシェルが終了コード 126 / 127 を返したこと。テストが落ちたこととは別に扱う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| チェック | — | 機械が合否を返すもの。CI のジョブと、mvv-gate.py・doc-lint.py などのスクリプト | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 全体テスト | — | リポジトリ全体を範囲にするテストか静的解析。テストは設定の suites[].command、無ければ範囲テストのテンプレートの {paths} を . にしたもの（その旨を注記に残す）。静的解析は設定の command、無ければテンプレートの {paths} を範囲のパスで埋めたもの（範囲のパスが無ければ組まず、その旨を注記に残す） | 全体のテスト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 消したテスト | — | 着手前に走っていて、着手後の結果（走った・skip・deselect）のどれにも無く、テストのジャーナルに載るテストの nodeid。計測のスクリプトの diff が、着手後の木にその定義が残っていないことを検証する対象 | — | — | `docs/specifications/ndf-whole-test-duration.md` |
| 現状固定テスト | — | 構造を変える前の出力をそのまま期待値にしたテスト。構造を変える前後で同じ出力が出ることを縛る（英語の characterization test） | — | — | `plugins/ndf/skills/refactoring/references/characterization-tests.md` |
| doc-lint | — | 追加した Markdown の行に、検討の痕跡・課題番号の由来・比較の語が無いかを見るチェック | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| テストの戦略 | `strategy` | 範囲テストの走らせ方・全体テストの保存先（手元か CI か）・落ちたテストの見分け方の組。local-full / local-scoped-ci-whole / round-only の 3 つ。設定の test.strategy か、同じ関数が所要から導く（#1334） | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| 範囲テストのテンプレート | `scope_command` | {paths} を引用の外の 1 字句として含むテストのコマンド（設定の scope_command か、{paths} を含む引数）。{paths} をシェルの引用で守った対象の並びへ置き換え、シェルで走らせる。{paths} には、改修の範囲テストではテストのファイルのパス、CI に任せる戦略の着手前のテストではテストの置く場所（ディレクトリかファイル）、静的解析では変更したファイルのパスが入る | 範囲テストの雛形 | — | `docs/specifications/cross-refactoring-scope-template-probe.md` |
| JUnit の保存先 | `junit` | テストのコマンドが JUnit XML を書くファイルの、作業ディレクトリからの相対パス（設定の suites[].junit）。NDF はコマンドへ引数を足さず、このファイルを読む | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ジョブの識別子 | — | CI のジョブを指す `<ワークフローのファイルのパス>#<job id>`。`#<job id>` を省くとそのファイルのジョブすべてを指す。設定の suites[].ci_jobs と test.ci_exempt が使う | — | — | `docs/specifications/cross-refactoring-lint-suite-and-ci-coverage.md` |
| 除外したジョブ | `ci_exempt` | 手元の検証で走らせないと定義したジョブ。設定の test.ci_exempt に理由と組で書く | — | — | `docs/specifications/cross-refactoring-lint-suite-and-ci-coverage.md` |
| 必須のチェック | — | ブランチの ruleset の required_status_checks に context の名前で載ったチェック。落ちるか結果が無いとマージを塞ぐ | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| チェックの名前 | — | ジョブの結果が Pull Request に載るときの名前。ジョブの name:（無ければ job id）に matrix の値が付いたもの。必須のチェックはこの名前で検証する | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 必須にしないジョブ | — | 必須のチェックとの突き合わせで差に数えないと、理由と組で定義したジョブ | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 突き合わせのチェック | — | ワークフローのジョブから決まるチェックの名前と、マージ先のブランチの必須のチェックと、必須のチェックの設定を突き合わせ、差があれば落ちるチェック | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 追加待ちのチェック | — | 必須にすると決めたが、ruleset へ足す承認（リリース承認ゲート）を待っているチェック。突き合わせのチェックは差に数えない | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 置き換わった失敗 | — | PR の先頭のコミットで、同じチェック（`workflowName` と `name` の組）により新しい項目があるときの古い項目の失敗。CI のチェックの結論に数えない | — | — | `docs/specifications/ndf-ci-wait-superseded-failures-and-infra-wait.md` |
| 基盤待ち | — | Runner が付かずに取り消された CI のジョブ（Runner が付かなかった取り消し）があり、ほかに中身の失敗が無い状態。修正へ回さず、再実行して待ち直す。待ち切れなければ `merge-when-green` は終了コード 75 で止まる | — | — | `docs/specifications/ndf-ci-wait-superseded-failures-and-infra-wait.md` |
| Runner が付かなかった取り消し | — | 結論が `cancelled` で、ジョブのステップが 0 件、Runner の名前が空の CI のジョブ。GitHub Actions の障害や Runner 不足で起き、REST のジョブの照会で見分ける | — | — | `docs/specifications/ndf-ci-wait-superseded-failures-and-infra-wait.md` |
| push 前の検査 | — | `core.hooksPath` の `pre-push` フックが `git push` の前に打つ検査。不合格なら push が拒まれる。中身はプロジェクトごとに違う | — | — | `docs/specifications/ndf-sprint-acceptance-check-and-prepush-fix.md` |
| push の修正 | — | 実装のプランで、push 前の検査の不合格を直してコミットし、`pr` のステップを打ち直させる修正の worker（`fix-push` のステップ） | — | — | `docs/specifications/ndf-sprint-acceptance-check-and-prepush-fix.md` |
| 所要の記録 | — | `.ndf/project.json` の `test_duration.measured` にある全体テストの所要（秒。w）。出所は ndf-record → ci-junit → ci-steps の順に採る。設定の解析が書き、プランの作成と test-run.py が読む | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| 全体テストの実測 | — | 同じリポジトリで先に走った全体テストの壁時計の秒。所要の記録とは別に残し、後のプランと test-run.py が予算なしのテストの上限を決めるときに読む（#1631） | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| テストの上限 | `limits` | test_strategy.limits が返す init_test_timeout・test_timeout・whole_timeout（秒）。ステップの timeout はこれに with_margin の余裕を足した値で、テストの上限には余裕を足さない | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| 全体テストのキー | `whole_key` | 戦略の全体テストのコマンドの集合から作る識別子（whole_key）。全体テストの実測をコマンドごとに分け、コマンドの違う実測を混ぜない（#1631） | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| 文書のファイル | — | 差分のうち、文章として読まれるファイル。既定は Markdown と、用語集の設定が指す用語集のファイルで、プロジェクトの設定（.ndf/project.json の docs）で変えられる | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| コードのファイル | — | 差分のうち文書のファイルでないもの。スクリプト・設定・テスト・ワークフローを含む | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| テストが読む文書 | — | 文書のファイルのうち、プロジェクトの設定でテストとの対応を定義したもの（用語集・Skill の本文など） | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| 振る舞いの確認 | — | Skill の本文・エージェント定義・手順書を変えたとき、AI がその手順どおりに動くかを実際に起動して確かめること | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| 振る舞いを決める文書 | — | 文書のファイルのうち、AI の振る舞いを決めるもの（Skill の本文・手順書・エージェント定義）。既定は skills/ の下の Markdown と agents/ の直下の Markdown | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| CI テストの設定 | — | `.ndf/project.json` の `test.ci.only: true`。このリポジトリのテストは CI でだけ走らせると表し、設定があれば手元でテストを走らせない | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| 手元のテスト | — | worker・supervisor・プランのステップ・cross-refactoring が、作業しているマシンで走らせるテスト（範囲テスト・全体テスト・指摘の検証・打ち直しを含む） | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| CI の結果の監視 | — | PR の CI のテストのチェックを、待たずに 1 回だけ読むステップ（`ci-peek`）をプランの区切りに置き、失敗を見つけたら CI の失敗の改修を起動する仕組み | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| CI の失敗の改修 | — | CI の結果の監視が見つけたテストの失敗を直し、同じ PR へ push すること | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| テストのチェック | — | 合否に使う PR のチェックの名前の集まり。`test.ci.check` があればその 1 つ、無ければ `ci.required_checks`、どちらも無ければ head のすべてのチェック | — | — | `docs/specifications/ndf-ci-only-tests.md` |
| 衝突の状態 | `mergeable` | Pull Request をベースブランチへマージできるかを GitHub が計算した値（`mergeable`。MERGEABLE・CONFLICTING・UNKNOWN）。CONFLICTING の Pull Request では GitHub が pull_request のワークフローを起動しない | — | — | `docs/specifications/ndf-whole-test-ci-wait-conflict-stop.md` |
| CI の待ち | — | CI に任せる戦略の全体テストで、test-run.py が Pull Request のチェックの結論が出るまで照会を繰り返すこと。上限は ci_wait_timeout | — | — | `docs/specifications/ndf-whole-test-ci-wait-conflict-stop.md` |
| CI | — | Pull Request ごとに自動で走る検査（GitHub Actions のワークフロー） | 継続的統合 | — | `docs/specifications/ndf-glossary-deprecated-meaning.md` |
| マシンに依らない条件 | — | 同じ入力なら、合否がコードの振る舞いと、条件の文に書いた測る場所だけで決まる受け入れ条件。正しさ・回数と計算量・同じマシンの上で測った基準との相対の 3 つの形。正しさと回数と計算量は、どのマシンで実行しても同じ合否になる。相対は、変更の影響がマシンごとに違うため比もマシンで変わり、スペックが固定された測る場所を同じ文に書いたときだけこの条件に当たる（非機能の条件の書き方） | — | — | — |
| 時間で区切った負荷試験 | — | 「N 分流す」のように、流す時間と同時に動かす本数を決めて所要や失敗を測る試験。結果がマシンのスペックで変わるため、受け入れ条件に置かない | — | — | — |
| 測る場所 | — | 秒数の上限と、同じマシンの上で測る相対の比を測るマシン。CI のランナーのように、スペックが設定で固定され、ほかの処理と CPU を取り合わない場所に限る。共有の開発機は当たらない | — | — | — |
| JUnit の成果物 | `junit_artifacts` | CI のジョブが上げた artifact のうち、JUnit の XML を持つもの。設定ファイルの `test.ci.junit_artifacts` が名前の glob で指し、CI で落ちたテストをログより先に読む元になる | — | — | `docs/specifications/ndf-next-ci-failed-tests-junit.md` |
| ログのパーサー | — | 1 つのテストランナー（suite の `runner`）の CI のログの形から、落ちたテストを `<テストのファイル>::<テストの名前>` の並びで読む部品。外部ライブラリを呼ぶ唯一のモジュールの意味の「アダプター」とは別 | — | — | `docs/specifications/ndf-next-ci-failed-tests-junit.md` |
