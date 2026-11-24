# 用語集: NDF の cross-refactoring（`ndf-cross-refactoring`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

想定最大時間に収まるリファクタリング計画を立てて通す語。改善候補・改善項目・配分テーブル・最終ゲート

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| リファクタリング計画 | — | `cross-refactoring` が採る改善項目を決め、見送った提案と理由を残す出力 | 改修計画 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| バッファ | — | `cross-refactoring` の見積りで、想定最大時間から経過を引いた後に残しておく時間。最終ゲートの全体テスト・修正 1 回・最終ゲート修正 1 回と、最終ゲートと兼ねられないときだけ危険フラグの全体テスト | 予備時間 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ラウンドテスト | `round_test` | `cross-refactoring` で、`--scope` のテストの置く場所を走らせるコマンド | ラウンドのテスト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| グレード | — | `cross-refactoring` が候補ごとに付ける適用の価値（high / medium / low） | 等級 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| マージ処理 | — | 担当の結果やコミットをオーケストレーターが受け入れるコマンド（merge-proposals / merge-plan / merge-tests / merge-implement / merge-fix / merge-final-fix）。フェーズの「マージ」とは別 | — | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| 最終ゲート | `final_gate` | 全体テストか CI で合否を判定する、cross-refactoring の最後の手順（final-gate）。承認ゲートとは別 | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 最終ゲート修正 | — | 最終ゲートの失敗を直す起動と、そのマージ処理（final-fix / merge-final-fix）。改善項目に属さない | 最終ゲートの修正 | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 必須トレーラー | — | 担当がコミットメッセージのトレーラーとして書く Item-Id / Impl-Runtime / Impl-Model。最終ゲート修正は Item-Id を除く 2 つ | 必須の記名 | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| 帰属トレーラー | — | 実行環境がコミットメッセージの末尾へ足すトレーラー（Co-Authored-By: / Claude-Session:） | 帰属の段落 | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| プロダクションコード | — | コードの拡張子を持ち、テストの置く場所でないファイル（production_code_changes）。名前がテストの形でもテストの関数を持たない .py は、tests/ などのテスト用ディレクトリの配下になければ含む | 本番コード | — | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| テストの関数 | — | Python のファイルで、名前が test で始まる関数かメソッドの定義（async def を含む）。pytest が既定で集める形。名前がテストの形の .py は、これを持つときだけテストのファイルになる | — | — | `docs/specifications/cross-refactoring-scope-template-probe.md` |
| 実装担当 | — | リファクタリング計画・テスト追加・実装・修正・最終ゲート修正を通して担う 1 ランタイム（--implementer） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善候補 | — | 提案を path + symbol + smell のキーで集約したもの。リファクタリング計画へ渡す（candidates[]） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善項目 | — | リファクタリング計画が採った改善候補。I-001 の形の ID を持ち、Item-Id の同じコミットを何件でも持てる（テストを足すコミットは 1 件） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 配分テーブル | — | 種類ごとの 1 件あたりの所要（分）。履歴の直近からリファクタリング計画のたびに集計し、保存しない（plan.table） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 変更したファイル | — | 判定の起点から変わったファイルのうち、worktree に残るもの。起点は判定ごとに決まる。項目の範囲テストではその項目のコミット（実装と修正）、最終ゲートでは着手前の HEAD、`test-run.py whole` では merge-base である。そのうち静的解析の suite の `paths` に当たるものが、その suite の範囲になる | — | — | `docs/specifications/cross-refactoring-lint-suite-and-ci-coverage.md` |
| 危険フラグ | `danger` | cross-refactoring で、範囲テストでは覆えない変更（D1〜D5）。立てば全体テストを 1 度走らせる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フレーキー / 既存失敗 / 変更起因 | — | 全体テスト（着手前・危険フラグ・最終ゲート）で落ちたテストの 3 つの分類（flaky / preexisting / caused。着手前は baseline_test.existing_failures（既存失敗）、危険フラグは whole_test、最終ゲートは final_gate.checks[] の記録に書く）。ID は JUnit から読み、落ちたファイルだけを HEAD と比較の木で走らせ直して分ける。フレーキーと既存失敗は項目の合否に数えず、フレーキーはその実行の中で直す | 元からの失敗 | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 指標 | — | `cross-refactoring` が提案の前に対象範囲のコードを測定ツールで測った値。関数ごとの循環的複雑度（Python では認知的複雑度も）、ファイルごとの大きさ、行数、重複の箇所 | — | — | `docs/specifications/cross-refactoring-init-test-and-phase-windows.md` |
| 残すコミット | — | 取り消しの判定が残すと決めたコミット。取り消されていない改善項目に記録されたコミット・受け入れた最終ゲート修正のコミット・オーケストレーターが状態ファイルに記録したコミットのどれかで、公開してよいのはこれだけである | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 公開した地点 | — | cross-refactoring のオーケストレーターが最後に push した HEAD。まだ push していなければ plan.base_sha。これより前のコミットは取り消しで書き換えない | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 原因の項目 | — | 全体テストか範囲テストで変更起因として落ちたテストを、その変更で落とした改善項目。危険フラグの有無とは関係しない | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 巻き込まれた項目 | — | 範囲テストが落ち、原因の項目が自分以外だけに決まった改善項目。取り消さず、原因が片づいてから走らせ直す | — | — | — |
| 公開前の静的解析 | — | 最終ゲート修正のマージで、push の前に、修正のコミットが変えたファイルへ当てる静的解析の範囲テスト | — | — | — |
| 原因の手がかり | `basis` | 原因の項目を決めた根拠。path（落ちたテストの出力に項目の変えたファイルのパスが現れた）・isolate（項目を外した走らせ直しで通った）・undetermined（どちらでも決まらない）の 3 つ | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| リファクタリング計画のコメント | — | cross-refactoring が対象の Pull Request に 1 件だけ置き、結果の出口のたびに同じものを編集するリファクタリング計画の記録。目印 `<!-- cross-refactoring plan rf<ID> -->` で引き当てる | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 結果の出口 | — | リファクタリング計画ができた後に、cross-refactoring のスクリプトがその時点の結果を確定させて終了コードを返す地点（push の成功と失敗・最終ゲートの判定・打ち切り・取り消し・中断・finalize） | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 未確認 | `unconfirmed` | 取り消されずに残ったが、最終ゲートが passed になっていない改善項目の状態。採用とは数えない | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| プランの外の取り消し | — | cross-refactoring のスクリプトが終わった後に、conductor が改善項目のコミットを git revert で取り消すこと | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 公開の結果 | `publication` | 最後に試みた head ブランチへの push の結果（通った地点の SHA か、落ちた理由）。状態ファイルの publication に残す | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 未公開の改善項目 | `unpublished` | 手元の HEAD が、公開した地点から到達できないコミットを持つこと。結果 JSON の metrics.unpublished | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 設定に無いジョブ | — | CI のジョブのうち、どの suite の ci_jobs にも除外したジョブにも当たらないもの。cross-refactoring の init が知らせ、状態ファイルの ci_coverage.undeclared に残す | — | — | `docs/specifications/cross-refactoring-lint-suite-and-ci-coverage.md` |
| 単独起動 | — | --workflow-step を付けずに呼んだ cross-refactoring の起動。最終ゲートが cross-review の承認収束になる | — | — | — |
| 対象のリポジトリ | — | cross-refactoring が Pull Request を取り、作業ディレクトリを作るリポジトリ。打った場所（現在のディレクトリ）が属する git の worktree の根で決まる | — | — | — |
| 提案担当 | — | cross-refactoring の propose で改善の提案を出す参加者 | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 着手前のテスト | — | `cross-refactoring` の init が改修の前に 1 回走らせるテスト。CI に任せる戦略では --scope のテストの置く場所の範囲テスト、ほかは全体テスト（round-only はラウンドテストも） | — | — | — |
| 範囲テストの所要 | — | CI に任せる戦略で、着手前に走らせる範囲テストの所要の見込み（秒）。同じ範囲の履歴の実測 → 手元の全体テストの直近の実測 m → 設定の w の順に採る | — | — | `docs/specifications/cross-refactoring-init-test-and-phase-windows.md` |
| 直さなかった項目 | — | cross-refactoring で、結果を残して終わった修正の起動の後、修正の範囲に自分の Item-Id のコミットが 1 つも無い改善項目。次の検証の最初に取り消す | — | — | `docs/specifications/cross-refactoring-failed-item-rules.md` |
| 確かめ直し | — | cross-refactoring で、最終ゲートより前の取り消しで HEAD が変わった後、残った改善項目を新しい HEAD の範囲テストで判定し直すこと | — | — | `docs/specifications/cross-refactoring-failed-item-rules.md` |
| 新しいファイル | — | cross-refactoring の実行で、リファクタリング計画の起点（plan.base_sha）のツリーに無かったパス。置く場所によらず範囲の中として扱う | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 呼び手の書き換え | — | cross-refactoring で、範囲の外の既存のファイルのうち、項目が変えた名前の読み込み・呼び出しだけを書き換える変更。ハンクごとに機械で判定し、通したファイルは最終ゲートのレビューへ引き継ぐ | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 変えた名前 | — | cross-refactoring の判定の単位のコミットが、範囲の中のファイルと新しいファイルで変えた行に現れる識別子と、作った・消した・移したパスの語幹とディレクトリ名から、ありふれた語を除いたもの（修正・最終ゲート修正の単位では、その項目（最終ゲート修正なら採った全項目）の実装・修正のコミットが変えた名前も含める） | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 判定の単位 | — | cross-refactoring のマージが 1 度に同じ規則へ掛けるコミットの組。実装は項目ごとの全コミット、修正は修正の範囲の項目ごと、最終ゲート修正は修正の全コミット | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 代表の兆候 | — | cross-refactoring の提案の smell に観点の識別子が書かれたとき、写す先の兆候 | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| ありふれた語 | — | cross-refactoring の呼び手の書き換えの判定で、リファクタリング計画の起点のツリーのコードのファイルの 20% 以上かつ 3 ファイル以上に現れる識別子。変えた名前から除き、追加の行に現れてよい | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 止まっていた時間 | — | `cross-refactoring` を中断してから、init か drive.py を打ち直して再開するまでの時間。リファクタリング計画の前の中断を含む。再開の時刻 − 最後の動きの時刻で測り、想定最大時間に数えない | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 最後の動きの時刻 | — | `cross-refactoring` の作業ディレクトリ（`.cross_refactoring/`）の直下のファイルのうち、最も新しい更新時刻。状態ファイル・CLI のログ・テストのログ・結果ファイルと、心拍のファイルを含む。止まっていた時間の起点 | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 心拍のファイル | — | `cross-refactoring` の作業ディレクトリ直下の `cross-refactoring-rf<ID>-alive`。出力の有無によらず、CLI の監視とテストの実行が待っている間、心拍の間隔（15 秒。見回りの間隔に依らない）ごとに更新時刻を今にする。中断で止まると更新も止まり、最後の動きの時刻が稼働の終わりを指す | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 採り直し | — | `cross-refactoring` で検証の後に時間が残ったとき、budget で見送った候補と実装の終わりまでにコミットが無かった改善項目から、残った時間に入るものを順位の順に採り、テストの追加・実装・検証をもう 1 巡回すこと。計画し直さない | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 見送った改善項目 | — | リファクタリング計画が採った改善項目のうち、表示の状態が見送りのもの（実装の終わりまでにコミットが無く採り直しにも入らなかった not_done・足したテストが落ちた test_failed）。結果 JSON の metrics.deferred は、この数に時間（budget）で見送り項目に入っていない候補の数を足す（#1944） | — | — | `docs/specifications/cross-refactoring-pr-gate-and-skip-counts.md` |
| 見送った提案 | `deferred_items` | 計画に入らなかった提案と見送った改善項目を合わせたもの。理由を 1 つ持つ。状態ファイルの deferred_items | — | — | `docs/specifications/cross-refactoring-pr-gate-and-skip-counts.md` |
| テストの無い設定 | — | .ndf/project.json の test があり、unknown と strategy を持たず、種別・テンプレートの誤りが無く、どの suite も command と scope_command のどちらも持たないもの（suite が 0 個のときを含む）。解析が「テストが無い」と測った {"suites": []} を含む。cross-review の最終スイープは検証のコマンドを名指ししない | — | — | `docs/specifications/cross-review-no-test-declaration-sweep.md` |
| テンプレートの試走 | — | cross-refactoring の init が着手前のテストの後に、範囲テストのテンプレートへテストのファイルを最大 2 本入れて 1 回走らせ、{paths} に入るパスを受けるかを見分けること。不備か判定できなければ改修の前に止まる | — | — | `docs/specifications/cross-refactoring-scope-template-probe.md` |
| 実装の予算 | — | `--budget-minutes` の値（既定 60 分）。採る改善項目の見積り（テストの追加と実装の和）の合計の上限で、選定にだけ使う。締め切りを導かない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 実装時間 | — | 改善項目のテスト追加・実装・修正で担当の CLI が動いた時間。提案・選定・テストの実行・CI の待ちは含めない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 予算の延長 | — | グレード high の候補の見積りの和が実装の予算を超え 2 倍以下のとき、実効の予算をその和まで増やすこと | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| リファクタリングの成立 | — | 改善項目が、振る舞いを変えずに、計画の前提にした構造の上で直し切れる見込みがあること。成立しない例: 振る舞いを変えないと直せない・前提の構造が実在しない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 無関係な失敗 | — | 着手前の木（マージしたベースブランチを含む）でも落ちるテストの失敗。改善項目の合否に数えない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 停滞 | — | 修正の後の落ちたテストの集合が、前の回と同じであること | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 振動 | — | 修正の後の落ちたテストの集合が、前に見た集合へ戻ること | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 安全網の上限 | — | コーディングの CLI（テストの追加・実装・修正・最終ゲート修正）の起動ごとの秒の累計に置く上限。既定は実効の予算 × 2。異常の検知にだけ使い、当たっても取り消さずに止めて人へ渡す | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 実効の予算 | — | 予算の延長を当てた後の実装の予算。延ばさなければ実装の予算と同じ | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 比較の木 | — | HEAD から、取り消していない改善項目と最終ゲート修正のコミットをすべて外した木。着手の後にマージしたベースブランチの変更を含む。そこでも落ちるテストを無関係な失敗として見分ける | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| フレーキーの直し | — | cross-refactoring がその実行の中でフレーキーと見分けたテストを、実装担当に 1 テストにつき 1 回だけ直させる工程（flaky-fix）。直しは Flaky-Fix のトレーラーを持つテストだけのコミットで、項目の合否と実装時間に数えない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 変更要求 | — | 改善項目の中の手順の違反（記名の欠け・期待値の欠け・文言を固定するテスト・計画に無いコミット）を、項目を取り消さずに担当へ直させること | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 落ちたテストの材料 | — | 落ちたテストの ID・ログの保存先・走らせ直すコマンドの組。JUnit を読めないときもログから組み、修正と成立の判定へ渡す | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 判定の担い手 | — | 成立の判定と判定し直しを行うランタイム。参加者のうち実装担当でない者を、ホスト → 参加者の順で選ぶ | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 古い起点 | — | 中断した cross-refactoring を続けるとき、Pull Request の今の head が、作業場所の work の HEAD と同じでもその祖先でもないこと。続けずに退避して今の head から始め直す | — | — | — |
| 退避した作業場所 | — | 古い起点のために、消さずに rf<PR>.retired-<時刻> へ移した cross-refactoring の作業場所（work・read/<ランタイム>・logs）と状態ファイル。同じ Pull Request の作業場所の片付けが、作業の残りで外すかを決める | — | — | — |
| 作業場所 | — | ndf-next の cross-refactoring が Pull Request ごとに作る保存先 <保存先>/rf<PR>。書き込み用の work・担当ごとの読み取り用 read/<ランタイム>・logs・状態ファイルを持つ | — | — | — |
| 状態ファイル | — | ndf-next の cross-refactoring の実行の進みを持つ rf<PR>/state.json。完了の記録が無ければ、同じコマンドを打ち直すと記録のある手順を流し直さずに続ける | — | — | — |
| 退避の途中のマーカー | — | 作業場所の退避を始める前に退避した先へ置き、移し終えたら消すファイル。残っていれば、次の打ち直しは検証をせずに退避を続ける | — | — | — |
| 収集の確かめ | `collect_command` | cross-refactoring が、足したテストを全体の実行が収集するかを suite の `collect_command` で確かめる工程。成功と読む終了コードは suite の設定から読み、設定が無ければ 0 だけ | — | — | `docs/specifications/ndf-next-ci-failed-tests-junit.md` |
