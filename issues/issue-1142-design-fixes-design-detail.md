# #1142 の詳細設計: ラベル `rebuild-1142` の残りを閉じる 14 本の子 Issue

設計文書 [issue-1142-design-fixes.md](issue-1142-design-fixes.md) の子 Issue（N1〜N12）を、ファイルと関数の粒度へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。流す順序と、子 Issue ごとの先に要るもの・消す古いテストは頭の詳細設計 [issue-1142-design-detail.md](issue-1142-design-detail.md) の「実装計画」が持つ。

**表の行は子 Issue の名前を括弧に持つ。** 2 本の子 Issue が同じファイルを触る行は、名前を両方書いた。書く関数は、新設する関数と、シグネチャ・責務・失敗の扱いのどれかを変える関数だけで、子 Issue ごとに大事なものから 8 個までに絞った。N12 の関数は #2711 の設計文書（ブランチ `design/issue-2711`）の「構成要素」にあり、ここでは描き直さない。

| 子 Issue | 主題 | 解消する Issue |
| --- | --- | --- |
| `N1` | キューの再開と参照の解決 | #1592・#2344・#2408・#2489・#2528・#2568・#2569・#2638・#2648・#2663・#2664・#2665・#2806 |
| `N2a` | worker の受け渡しと指示文 | #1734・#1742・#1958・#1984・#2005・#3111・#3129 |
| `N2b` | 設計の結果・受け入れ条件・重なりの判定・マージの後の作業場所 | #1811・#2056・#2165・#2246・#2274・#2357・#2453・#2539・#2553・#2596・#2969・#3022・#3186 |
| `N3` | 並列の計画の状態とレーンの生存 | #2477・#2584・#2617・#2637・#2647・#2649・#2808・#2936・#3173・#3243 |
| `N4` | 中継・hook の転送・依存の環境 | #1877・#1919・#2324・#2438・#2461・#2464・#2504・#2563・#2583・#2753・#3105 |
| `N5` | cross-refactoring の振る舞い | #620・#622・#750・#2057・#2096・#2158・#3038・#3049 |
| `N6` | cross-review の同期と worktree | #1707・#2440・#2770・#3106・#3109 |
| `N7` | 棚卸し（backlog-refinement） | #1595・#1640・#2064・#2232・#2423・#2540 |
| `N8a` | 工程の終わりの記録・引継ぎ文書・用語集 | #1616・#1715・#1785・#1835・#1893・#2293 |
| `N8b` | 使用量の帳簿と MVV の記録 | #1978・#1979・#2575・#2576・#2603・#2614・#2777 |
| `N9` | テストの揺れ・残骸・隔離・後始末 | #1735・#1967・#2130・#2758・#2776・#2798・#2964・#2974・#3140 |
| `N10` | 監視・外部 AI・マージの hook | #1411・#2059・#2338・#2446・#2448 |
| `N11` | プランの雛形のステップ（SF2 から分けた） | #1502・#2049・#2054・#2118・#2140・#2370・#3003・#3108・#3112 |
| `N12` | テストの同時実行の枠（#2711） | #2711 |

## ファイル配置

パッケージ・モジュール構成（触るファイルのあるディレクトリだけ。括弧は触る子 Issue）:

```text
./  （N10・N12）
.ndf/  （N8a）
docs/  （N12）
docs/glossary/  （N12）
docs/specifications/  （N3・N11）
plugins/ndf/contracts/  （N5・N7・N8a・N10）
plugins/ndf/contracts/entrypoints/  （N2a・N2b・N10）
plugins/ndf/contracts/env/  （N10）
plugins/ndf/contracts/files/  （N10）
plugins/ndf/scripts/  （N1・N2a・N2b・N3・N4・N6・N8a・N8b・N10・N11・N12）
plugins/ndf/scripts/hook_lib/  （N4・N12）
plugins/ndf/scripts/lib/  （N1・N2a・N2b・N3・N4・N5・N6・N7・N8a・N8b・N9・N10・N12）
plugins/ndf/scripts/relay_lib/  （N3・N4）
plugins/ndf/scripts/supervise_lib/  （N1・N2a・N2b・N4・N7・N11・N12）
plugins/ndf/scripts/tests/  （N1・N2a・N2b・N3・N4・N5・N6・N7・N8a・N8b・N9・N10・N11・N12）
plugins/ndf/skills/backlog-refinement/  （N7）
plugins/ndf/skills/backlog-refinement/scripts/  （N7）
plugins/ndf/skills/backlog-refinement/tests/  （N7）
plugins/ndf/skills/cross-refactoring/  （N5）
plugins/ndf/skills/cross-refactoring/prompts/  （N5）
plugins/ndf/skills/cross-refactoring/scripts/  （N5・N12）
plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/  （N5・N12）
plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/  （N5）
plugins/ndf/skills/cross-refactoring/tests/  （N5・N9）
plugins/ndf/skills/cross-review/  （N6）
plugins/ndf/skills/cross-review/scripts/  （N5・N6）
plugins/ndf/skills/cross-review/scripts/review_lib/  （N6）
plugins/ndf/skills/cross-review/scripts/review_lib/commands/  （N6）
plugins/ndf/skills/cross-review/tests/  （N6・N9）
plugins/ndf/skills/design/references/  （N2b）
plugins/ndf/skills/development-workflow/references/  （N3・N10・N12）
plugins/ndf/skills/development-workflow/scripts/lib/  （N10）
plugins/ndf/skills/development-workflow/tests/  （N9・N10）
plugins/ndf/skills/fix/scripts/  （N6）
plugins/ndf/skills/install-wrapper/  （N4）
plugins/ndf/skills/plan-to-spec/  （N8a）
plugins/ndf/skills/progress-tracking/  （N8a）
plugins/ndf/skills/refactoring/references/  （N5）
plugins/ndf/skills/release/tests/  （N9）
plugins/ndf/skills/requirements-design/references/  （N8a）
plugins/ndf/skills/retrospective/  （N8a）
plugins/ndf/skills/worktree/tests/  （N9）
scripts/  （N8a）
scripts/contracts_lib/  （N10）
scripts/script-structure-allow/  （N10）
scripts/tests/  （N9）
```

依存の向きは今のまま一方向である。新設するモジュールは同じディレクトリの既存の部品（`lib/step_result.py`・`lib/causes.py`・`lib/jsonio.py` ほか）だけを import し、呼び出し元から import される向きだけを持つ。テストのファイルは対象のモジュールだけを import する。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `.ndf/glossary.json` | 変更 | document に出力先のディレクトリを足す（N8a） | `plugins/ndf/scripts/glossary.py` |
| `conftest.py` | 変更 | テストごとに NDF_RUNS_DIR を一時ディレクトリへ向け、NDF_RUN_ID などを外す（plugins/ndf/conftest.py も同じ）（N10）。#2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `docs/glossary.md` | 変更 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `docs/glossary/glossary.json` | 変更 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` | 変更 | 「振る舞いの確認」の節を、マージの前の確かめ直しの形に合わせる（N11） | 無し |
| `docs/specifications/ndf-parallel-organizer.md` | 変更 | 動かないレーン・一時レーンの途中停止・止めた計画の再開の確定仕様を今の判定に合わせる（N3） | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__acceptance-check.py.json` | 変更 | show の --complete を宣言する（N2b） | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__check-trigger.py.json` | 変更 | 読む環境変数と終了コード 1・3 を足す（N10） | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__supervise.py.json` | 変更 | 副命令 plan-edit・plan-edits の引数を宣言する（N2a） | 無し |
| `plugins/ndf/contracts/env/NDF_RUN_ID.json` | 新設 | 実行の識別子の環境変数の契約（NDF_RUN_LOG・NDF_RUN_LOG_SINK も同じ形で足す）（N10） | 無し |
| `plugins/ndf/contracts/files/` | 変更 | 実行の記録と全文ファイルの行を足す（N10） | 無し |
| `plugins/ndf/contracts/index.md` | 変更 | NDF_UPKEEP_STATE_DIR の既定の置き場の記述を直す（N7） | 無し |
| `plugins/ndf/contracts/limits.json` | 変更 | 試走の上限の下限（秒）を持つ（N5）。supervise.depth-work を宣言の無いときの既定だけにする（宣言があれば宣言の秒）（N7）。引継ぎ文書の行数とバイト数の上限を持つ（N8a）。run-log.line の bytes: 32000 を足す（N10） | 無し |
| `plugins/ndf/scripts/acceptance-check.py` | 変更 | show に、受け入れ条件の全件に区分があるかを終了コードで返す --complete を足す（N2b） | `plugins/ndf/scripts/lib/acceptance.py` |
| `plugins/ndf/scripts/behavior-check.py` | 変更 | `since` を足し、記録の head から今の head までに振る舞いを決める文書が変わったかを返す（N11） | `plugins/ndf/scripts/lib/diff_kinds.py` |
| `plugins/ndf/scripts/check-trigger.py` | 変更 | `findings_of` が state.json でなく進捗ログの頭からの実行の区間から件数を読む（N1）。実行の核を通るエントリポイントにし、検査の記録の行へ run_id と failed_reason を足す（N10）。検査の記録の行に review の行き先（進んだ・人へ戻した）と理由を写す（N11） | `plugins/ndf/scripts/supervise_lib/state.py` |
| `plugins/ndf/scripts/design-match.py` | 変更 | _path_present が `/` を含む名前を末尾の一致でも探す（N2b）。採らなかった案と改名の前の名前を missing から外す（N11） | 無し |
| `plugins/ndf/scripts/glossary.py` | 変更 | 宣言の document を索引と出力先のディレクトリの組で読み、コンテキストごとのファイルと索引を出す。stale_document と check の生成物の除外を複数ファイルへ合わせる（N8a） | 無し |
| `plugins/ndf/scripts/handoff.py` | 変更 | 本体の検査をバイト数・雛形に無い節の退避・パスの実在まで広げる（N8a） | `plugins/ndf/scripts/lib/handoff_doc.py` |
| `plugins/ndf/scripts/hook-env.py` | 変更 | sync_env が deps.python_request の値を --python で渡す（N4） | `plugins/ndf/scripts/lib/deps.py` |
| `plugins/ndf/scripts/hook.py` | 変更 | merge-target が --repo を受け、作業ディレクトリと違う対象の宣言を GitHub から読む（N10）。#2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | `plugins/ndf/scripts/lib/delivery.py` |
| `plugins/ndf/scripts/hook_lib/shell_checks.py` | 変更 | シェルの構文木から NDF のスクリプトのつなぎと sleep の並びを見つける関数を足す（N4） | `plugins/ndf/scripts/lib/shparse.py` |
| `plugins/ndf/scripts/hook_lib/test_guard.py` | 新設 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | 変更 | guard_sleep が背景の Bash でも NDF のスクリプトのつなぎと sleep の後の NDF のスクリプトを拒む（N4） | `plugins/ndf/scripts/hook_lib/shell_checks.py` |
| `plugins/ndf/scripts/lib/README.md` | 変更 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/lib/acceptance.py` | 変更 | 受け入れ条件の節の有無と空の判定、退行しない条件の前提の確認の行の判定を持つ（N2b） | `plugins/ndf/scripts/lib/gh_sections.py` |
| `plugins/ndf/scripts/lib/claude_accounts.py` | 変更 | 指定のアカウント（pinned.json）の読み書きを持ち、choose が指定を先に見る（N4） | 無し |
| `plugins/ndf/scripts/lib/closing.py` | 変更 | コミットの範囲からclosing keywordsを持つコミットを集める closing_commits を足す（N2a） | `plugins/ndf/scripts/lib/proc.py` |
| `plugins/ndf/scripts/lib/deps.py` | 変更 | python の選び方（python_request）と、python の版を含む環境の置き場（venv_dir）を 1 か所に持つ。venv_version の dev への倒れを外す（N4） | 無し |
| `plugins/ndf/scripts/lib/design_results.py` | 変更 | 触るファイルのセルの先頭の括弧から PR の単位の名前を読み、行に持たせる（N2b） | 無し |
| `plugins/ndf/scripts/lib/durable.py` | 変更 | ある識別子の耐久の記録（.sqlite と .lock）を退避する `retire(kind, identity)` を足す（N1）。KEEP_DAYS を durable_keys から引く（N10） | 無し |
| `plugins/ndf/scripts/lib/durable_keys.py` | 変更 | 記録の寿命 KEEP_DAYS = 30 を持つ（N10） | 無し |
| `plugins/ndf/scripts/lib/handoff_doc.py` | 変更 | 本文からパスの候補を取り出す関数を持つ（N8a） | 無し |
| `plugins/ndf/scripts/lib/launch-cli.sh` | 変更 | kiro の起動の前後で .amazonq/cli-agents を写して戻す（N10） | 無し |
| `plugins/ndf/scripts/lib/limits.py` | 変更 | 上限の値に bytes を足す（N10） | 無し |
| `plugins/ndf/scripts/lib/loop_drive.py` | 変更 | 完了した回を返す案内に、呼ぶ側の drive が渡す打ち直しの手段だけを書く（N5） | 無し |
| `plugins/ndf/scripts/lib/monitor_loop.py` | 変更 | 警告を種類ごとに 1 回ずつ出し、標準エラーへの 3 か所を run_log.diag、[:200] の 3 か所を run_log.clip へ置き換える（N10） | `plugins/ndf/scripts/lib/run_log.py` |
| `plugins/ndf/scripts/lib/monitor_patterns.py` | 変更 | 利用上限の一般語の照合を CLI が出す行の形に限る（N10） | 無し |
| `plugins/ndf/scripts/lib/monitor_scan.py` | 変更 | 早期の警告を種類ごとの組で返す関数を足す（N10） | `plugins/ndf/scripts/lib/monitor_patterns.py` |
| `plugins/ndf/scripts/lib/parallel_args.py` | 変更 | stop の引数に --now を足す（--force はリリースの途中でも止める意味のまま残す）（N3） | 無し |
| `plugins/ndf/scripts/lib/parallel_end.py` | 変更 | step_halt が relay.py stop の確かめた結果を読み、_ask_unended が古い版のレーンの終わりを記録から読む（N3） | `plugins/ndf/scripts/relay_lib/hub_client.py` |
| `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | レーンの続きの位置（途中のプランの状態ディレクトリ・次のステップ・--resume の 1 行）を読む関数を足す（N3） | `plugins/ndf/scripts/lib/sprint_loop_record.py` |
| `plugins/ndf/scripts/lib/parallel_launch.py` | 変更 | 認証で止まったレーンの起こし直しを、アカウントが使えるときだけ launch_once で打つ（N3） | `plugins/ndf/scripts/lib/claude_accounts.py` |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | 台帳の行の種類に paused（区切りで止めた・続きの位置つき）を足し、plan_state が止めた計画を作り直せる状態として返す（N3） | 無し |
| `plugins/ndf/scripts/lib/parallel_liveness.py` | 新設 | レーンの一覧の行・開発ループの記録・起動の記録・ラッパーの止まりの理由から、レーンの生死を 1 つの判定に決める純粋な関数（DBOS を読まない）（N3） | plugins/ndf/scripts/lib/parallel_decide.py（RF6a が置く判断の関数の型） |
| `plugins/ndf/scripts/lib/parallel_observe.py` | 変更 | _ask_helper の「今の起動より前の行」「届く途中の依頼」の判定を parallel_liveness へ移す（N3） | `plugins/ndf/scripts/lib/parallel_liveness.py` |
| `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | _observe_run・_ask_stalls・_track_ls_failure の判定を parallel_liveness へ渡し、判定に従って問いを立てる・取り下げるだけにする（N3） | `plugins/ndf/scripts/lib/parallel_liveness.py` |
| `plugins/ndf/scripts/lib/procs.py` | 変更 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/lib/project_mvv.py` | 変更 | 記録の repo をメインディレクトリのパスにする（N8b） | `plugins/ndf/scripts/lib/decl.py` |
| `plugins/ndf/scripts/lib/project_mvv_signals.py` | 変更 | 覆しの種類に unknown_pass を足し、同じ組の覆しを 1 件にまとめて数える（N8b） | `plugins/ndf/scripts/lib/project_mvv.py` |
| `plugins/ndf/scripts/lib/ref_sources.py` | 新設 | 参照の解決に要る外の値を読む殻（課題を閉じる PR・設計 PR のマージの状態と head・兄弟のプランの報告・進捗ログ・版の判定）（N1） | `plugins/ndf/scripts/lib/release_version.py・plugins/ndf/scripts/lib/gh_rest.py` |
| `plugins/ndf/scripts/lib/refs.py` | 新設 | 参照の種類（version・record_pr・queue_prs・plan_result・counts）を値か解決できない理由へ解く判断の関数。読み手を引数で受け、ファイルも GitHub も直に読まない（N1） | `plugins/ndf/contracts/causes.json（SF1a）` |
| `plugins/ndf/scripts/lib/result_posts.py` | 変更 | push_fix の取り込みと送り直し、_resolve_fix_inputs の管理情報の確かめ（N6） | 無し |
| `plugins/ndf/scripts/lib/run_core.py` | 変更 | 親の実行の識別子を受け、書き口を開き、ctx.log を書き口へつなぎ、終わりの行を書く。書けなければ結果を stopped にする（N10） | `plugins/ndf/scripts/lib/run_log.py` |
| `plugins/ndf/scripts/lib/run_log.py` | 新設 | 実行の記録の書き口（出力先の選択・秘密を伏せる・32,000 バイトを超える文字列の全文ファイル・診断・切り詰めの前の全文）（N10） | `plugins/ndf/scripts/lib/secret_redact.py・plugins/ndf/scripts/lib/state_home.py・plugins/ndf/scripts/lib/limits.py` |
| `plugins/ndf/scripts/lib/run_state.py` | 変更 | 残る状態の片付けで lock されたworktreeを unlock してから外す（N6） | 無し |
| `plugins/ndf/scripts/lib/secret_redact.py` | 変更 | 伏せる処理と縮める処理を分ける（N10） | 無し |
| `plugins/ndf/scripts/lib/sprint_loop.py` | 変更 | `cmd_hold_question` が保留のループの 1 回の締め直しの途中も引き、`cmd_finish` が質問待ちへ戻したループの 1 回を断る（N1） | `plugins/ndf/scripts/lib/sprint_loop_record.py` |
| `plugins/ndf/scripts/lib/sprint_loop_argv.py` | 変更 | 設計を飛ばす課題を設計 PR のマージの状態から決め、`--dev` を省いたときは版を参照で持つプランを組む（N1） | `plugins/ndf/scripts/lib/ref_sources.py` |
| `plugins/ndf/scripts/lib/sprint_loop_gate2.py` | 変更 | `finish_pipeline` が止まったプランを 2 度流さず、`answered_lap` が答えの中身を metrics とループの 1 回の question に残す（N1） | 無し |
| `plugins/ndf/scripts/lib/sprint_loop_record.py` | 変更 | ループの 1 回の秒がリリース待ちと質問待ちの両方の区間を足す（N1）。upkeep の状態の置き場の決め方を写さず、upkeep と同じ置き場を読む（_upkeep_state_dir）（N7） | 無し |
| `plugins/ndf/scripts/lib/stage-skills.json` | 変更 | pauses（fix・sweep → fix）と、届けてよい手順の Skill の一覧を足す（N2a） | 無し |
| `plugins/ndf/scripts/lib/stage_skills.py` | 変更 | 対応表の pauses（pause の種類 → 手順の Skill）を読み、形と名前を確かめる（N2a） | `plugins/ndf/scripts/lib/stage-skills.json` |
| `plugins/ndf/scripts/lib/step_result.py` | 変更 | 結果の任意の鍵 original_result を受ける（N10） | 無し |
| `plugins/ndf/scripts/lib/test_slots.py` | 新設 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/lib/test_strategy.py` | 変更 | suite の宣言に収集の確かめのテンプレート（collect_command）を足し、渡したファイルが全体の実行に収集されるかを返す（N5） | 無し |
| `plugins/ndf/scripts/lib/test_triage.py` | 新設 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/lib/test_weight.py` | 新設 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/lib/usage_aggregate.py` | 変更 | 期間の絞りを応答ごとの時刻にし、閉じた課題を JST の両端で数え、サブエージェントの抜けを会話の記録から補う（N8b） | `plugins/ndf/scripts/lib/usage_capture.py` |
| `plugins/ndf/scripts/lib/usage_capture.py` | 変更 | 会話の記録の読みを応答ごとの最後の行と時刻つきにし、サブエージェントの 2 回目以降の停止は差を書く（N8b） | `plugins/ndf/scripts/lib/usage_ledger.py` |
| `plugins/ndf/scripts/lib/usage_ledger.py` | 変更 | 帳簿のディレクトリを書き手によらず 1 つに決める（N8b） | 無し |
| `plugins/ndf/scripts/lib/wait_notice.py` | 変更 | classify_reply_wait に、別の場所での答えの案内を待ちと分ける種類を足す（N4） | 無し |
| `plugins/ndf/scripts/lib/worktree-testenv-sweep.sh` | 変更 | 親の無い行の置き場の判定を祖先へ広げ、持ち主の照合を読み込んだ値を受ける関数に分ける（N9） | 無し |
| `plugins/ndf/scripts/merged-steps.py` | 変更 | remove_worktree が lock されたworktreeを unlock してから外す（N6）。後片付けの後に、PR が閉じる課題ごとに工程「後片付け」を記録する（N8a） | 無し |
| `plugins/ndf/scripts/parallel.py` | 変更 | cmd_stop を区切りで止める既定と --now（今の止め方）に分け、止めた位置を台帳へ書いてから知らせる（N3） | `plugins/ndf/scripts/lib/parallel_ledger.py` |
| `plugins/ndf/scripts/plan-to-spec-steps.py` | 変更 | --spec を複数受け、語ごとの出所を選ぶ。用語集の文書を複数ファイルで書く（N8a） | `plugins/ndf/scripts/glossary.py` |
| `plugins/ndf/scripts/project-mvv.py` | 変更 | signals の表示に unknown_pass を別の数として出す（N8b） | `plugins/ndf/scripts/lib/project_mvv_signals.py` |
| `plugins/ndf/scripts/relay_lib/claude.py` | 変更 | after_mark が比べる時刻を、シグナルファイルを最初に書いた時刻にする（N4） | 無し |
| `plugins/ndf/scripts/relay_lib/hub.py` | 変更 | ラッパーの記録の最後の止まりの理由（認証で替えられない）を読む関数を足す（N3）。restart を受けたリレーハブが、上限を対象を外して判定し、通れば止めてから起こす。起動の環境に CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false を足す（N4） | 無し |
| `plugins/ndf/scripts/relay_lib/hub_client.py` | 変更 | stop_named が止まったことを確かめた結果を返し、_ls_item が止まりの理由（認証）と再起動の試みの回数を出す（N3）。cmd_restart が自分で止めず、リレーハブへ restart を頼むだけにする（N4） | `plugins/ndf/scripts/relay_lib/hub.py` |
| `plugins/ndf/scripts/relay_lib/install.py` | 変更 | PATH の入口 ndf-relay の設置・撤去・有無の表示（N4） | 無し |
| `plugins/ndf/scripts/relay_lib/mark.py` | 変更 | hold_once の条件と prose_wait_reason の材料を変え、握りつぶし（except Exception: return None）を外す（N4） | `plugins/ndf/scripts/relay_lib/record.py` |
| `plugins/ndf/scripts/relay_lib/pin.py` | 削除 | 指定の読み書きを lib/claude_accounts.py へ移したため消す（N4） | 無し |
| `plugins/ndf/scripts/relay_lib/record.py` | 変更 | 差し戻しの判定の材料と、切り替えない tick の理由と、シグナルファイルの最初に書いた時刻を記録する（N4） | 無し |
| `plugins/ndf/scripts/relay_lib/run.py` | 変更 | _tick が判定の結果を型で返し、切り替えない理由を記録へ書く。parse_iso(...) or time.time() の既定値への倒れを外す（N4） | `plugins/ndf/scripts/relay_lib/claude.py` |
| `plugins/ndf/scripts/relay_lib/runtime.py` | 変更 | sync が deps.python_request の値を --python で渡し、--no-managed-python の 2 回打ちをやめる（N4） | `plugins/ndf/scripts/lib/deps.py` |
| `plugins/ndf/scripts/relay_lib/switch.py` | 変更 | _pick_pinned が ca.pinned を読む（N4） | `plugins/ndf/scripts/lib/claude_accounts.py` |
| `plugins/ndf/scripts/spec-copy.py` | 変更 | check が本文の受け入れ条件の節と前提の確認の行も確かめる（N2b） | `plugins/ndf/scripts/lib/acceptance.py` |
| `plugins/ndf/scripts/sprint-close.py` | 変更 | 記録の PR にリリース後テストの記録があれば条件 2 を見る（N8a） | 無し |
| `plugins/ndf/scripts/supervise.py` | 変更 | queue に `--fresh` を足す（N1）。副命令 plan-edit・plan-edits の引数と COMMANDS の行を足す（N2a） | `plugins/ndf/scripts/supervise_lib/queue.py` |
| `plugins/ndf/scripts/supervise_lib/admission.py` | 変更 | 触るファイルの宣言が無いプランをすべてのプランと重なるものとして扱う（N2b） | 無し |
| `plugins/ndf/scripts/supervise_lib/claude.py` | 変更 | pause の worker の会話を残す起動（--no-session-persistence を外し session を返す）を選べるようにする（N2a）。choose・choose_env の結果に指定が効くことだけを確かめる（呼び出しの形は変えない）（N4） | 無し |
| `plugins/ndf/scripts/supervise_lib/commands.py` | 変更 | cmd_expected のリポジトリの根の組み立て（324 行目）を plan_repo へ置き換える（N2b） | `plugins/ndf/scripts/supervise_lib/paths.py` |
| `plugins/ndf/scripts/supervise_lib/delivery_templates.py` | 変更 | リリースの record の `--prs` を参照で書く（N1） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/supervise_lib/design_stage.py` | 変更 | 読めなかった設計の課題と、PR の単位が 2 つ以上の課題で止まる（N2b） | `plugins/ndf/scripts/lib/design_results.py` |
| `plugins/ndf/scripts/supervise_lib/design_steps.py` | 変更 | 要求のステップの次を requirements-check へ戻し、書いた本文を確かめてから設計へ進む（N2b） | 無し |
| `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | `run` の経路でもステップの直前に参照を解決し、キューの続きで開始のステップを持つプランも実行条件を評価する（リリース承認ゲート の後の `run --from` は評価しない）（N1）。keep_cwd が使えないworktreeからリポジトリの根へ移り、report の前にも呼ぶ（N2b） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/supervise_lib/flow.py` | 変更 | `fill_step` を refs の呼び出しへ替え、`prefix_of` の中身鍵をプランのハッシュ（キーの並びに依らない）にし、`launch_queue` の fresh で配下の review の記録も退避する（N1）。重なりの判定へ宣言の有無を渡す（311〜320 行の files の組み立て）（N2b） | `plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/scripts/supervise_lib/paths.py` | 変更 | PR 本文の下書きの置き場の定数 PR_BODY_DRAFT を置く（N2a）。プランのリポジトリの根を決める plan_repo と、worktreeとして使えるかを見る usable_worktree を置く（N2b）。振る舞いの確認のステップ（BEHAVIOR_STEPS）にマージの前の確かめ直しのステップを足す（N11） | 無し |
| `plugins/ndf/scripts/supervise_lib/plan.py` | 変更 | 残った差し込みの検出（`QUEUE_NAMED`）に `{queue_prs}` を含め、差し込みの規則の説明を直す（N1） | 無し |
| `plugins/ndf/scripts/supervise_lib/plan_edit.py` | 新設 | プランの書き換え（控え・差分・理由の記録）と、書き換えの一覧を持つ（N2a） | `plugins/ndf/scripts/lib/jsonio.py` |
| `plugins/ndf/scripts/supervise_lib/pr.py` | 変更 | PrStep._push が push の前に closing_commits を照らし、当たれば push せずに止める（N2a）。PR 本文の「テスト」の表を、行き先で分岐した非 0 と後の再確認で置き換えた結果を区別して全文で載せる（N11） | `plugins/ndf/scripts/lib/closing.py` |
| `plugins/ndf/scripts/supervise_lib/prompts.py` | 変更 | WORK_SYSTEM に前景で待つ行を足し、FULL_SYSTEM・PR_SYSTEM のclosing keywordsの行の範囲を限り、例に `Fix: #番号` を入れる（N2a） | 無し |
| `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | `fill_stage`・`fill_queue_prs`・`_release_skipped_by_condition`・`queue_prs` を消し、ステージの前の判断を refs の結果（流す・流さない unfilled・飛ばす）で決める。`--fresh` を受ける（N1）。touched_files が宣言の無いプランを None で返し、読めないプランを空として扱わずに止める（N2b） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/supervise_lib/queue_fill.py` | 削除 | 差し込みの解決（`_fill_prs`・`_fill_states`・`_fill_work`・`_fill_record`・`fill_release_version`）を refs へ移して消す。`named`・`results_named`・`plan_pr` は refs へ移す（N1） | 無し |
| `plugins/ndf/scripts/supervise_lib/queue_resume.py` | 変更 | `--from` の照合（match_plan）、実行条件の評価、完了したプランのハッシュの照合で再開の位置を決める（N1） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | 版・記録の PR・前のステージの PR を差し込みの文字列でなく参照で書く（N1）。verify のステップの cmd から git pull を外し、`--root` にリリースの worktree を渡す（N11） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | `close_plan` が release-verify を常に置き、記録の PR の参照が 0 に解けたときに飛ばす実行条件を持たせる（N1）。PR の単位ごとにプランを組む形を採る場合だけ、実装のプランの組み立てを単位ごとにする（N2b） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_close_steps.py` | 変更 | 見直し・大整理の work ステップの打ち切りを宣言の秒から組む（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_inputs.py` | 変更 | body_lack が lib/acceptance.py の判定を呼ぶ（N2b） | `plugins/ndf/scripts/lib/acceptance.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_revise.py` | 変更 | 出し直しの設計のプランへ設計 PR の head を起点のコミットとして書く（N1） | `plugins/ndf/scripts/lib/ref_sources.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 変更 | `has_record`・`record_pr_ref` を組む時点の判断から参照の宣言へ替える（N1） | 無し |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | 設計のプランの requirements-check に再確認のステップ（requirements-recheck）を宣言する欄を書く（N11） | `plugins/ndf/scripts/supervise_lib/pr.py` |
| `plugins/ndf/scripts/supervise_lib/state.py` | 変更 | `step_line` がステップの counts を進捗ログの行に書く（N1） | 無し |
| `plugins/ndf/scripts/supervise_lib/steps.py` | 変更 | deliver_skills が pause の種類を受けて届ける Skill を引く（N2a）。condition_cwd のリポジトリの根の組み立てを plan_repo へ置き換える（N2b）。#2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | `plugins/ndf/scripts/lib/stage_skills.py` |
| `plugins/ndf/scripts/supervise_lib/templates.py` | 変更 | plan_to_merge の pr のステップへ append の既定を置き、IMPL_RULES に PR 本文の下書きの置き場とclosing keywordsの例を足す（N2a）。plan_to_merge に受け入れ条件の区分のステップ（work と run）を足す（N2b）。検査のプランの refactor・review・振る舞いの確かめ直し、実装の指示の規則、fix の作業場所を直す（N11） | `plugins/ndf/scripts/supervise_lib/paths.py` |
| `plugins/ndf/scripts/supervise_lib/verify_steps.py` | 変更 | CI テストの宣言のある並び（ci_only_to_merge）の pr のステップにも同じ append を置く（N2a）。CI テストの宣言のある並び（ci_only_to_merge）にも同じ区分のステップを置く（N2b） | `plugins/ndf/scripts/supervise_lib/templates.py` |
| `plugins/ndf/scripts/supervise_lib/worker_steps.py` | 変更 | _delegate_pause が pause の種類の手順書を届け、結果ファイルが無ければ同じ会話を起こし直す（N2a） | `plugins/ndf/scripts/supervise_lib/steps.py` |
| `plugins/ndf/scripts/test-run.py` | 変更 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/test-slot.py` | 新設 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/tests/cmd_match.py` | 新設 | コマンドの文字列を shlex.split した語の並びで比べるテストのヘルパー（N9） | 無し |
| `plugins/ndf/scripts/tests/conftest.py` | 変更 | 本物の状態の根への書き込みを落とす autouse の前提を足す（N9） | 無し |
| `plugins/ndf/scripts/tests/test_acceptance_checks.py` | 変更 | section_lack・premise_missing と show --complete の単体テストを足す（N2b） | `plugins/ndf/scripts/lib/acceptance.py` |
| `plugins/ndf/scripts/tests/test_auth_probe.py` | 変更 | 壁時計の上限を待たなかった場合の時間に合わせる（N9） | 無し |
| `plugins/ndf/scripts/tests/test_behavior_check.py` | 変更 | `since` の 3 通り（変わった・変わらない・読めない）を確かめる（N11） | `plugins/ndf/scripts/behavior-check.py` |
| `plugins/ndf/scripts/tests/test_check_trigger.py` | 変更 | 途中から流し直した検査の件数が記録の行へ入ることを確かめる（N1）。検査の行の run_id と failed_reason のテストを足す（N10） | `plugins/ndf/scripts/check-trigger.py` |
| `plugins/ndf/scripts/tests/test_claude_account_env.py` | 変更 | 壁時計の上限を待たなかった場合の時間に合わせる（N9） | 無し |
| `plugins/ndf/scripts/tests/test_closing.py` | 変更 | closing_commits の単体テストを足す（N2a） | `plugins/ndf/scripts/lib/closing.py` |
| `plugins/ndf/scripts/tests/test_deps.py` | 変更 | python_request と置き場の名前のテストを足す（N4） | 無し |
| `plugins/ndf/scripts/tests/test_design_match.py` | 変更 | サブディレクトリからの相対パスが present になるテストへ書き換える（N2b）。採らなかった案と改名の前の名前の扱いを確かめる（N11） | `plugins/ndf/scripts/design-match.py` |
| `plugins/ndf/scripts/tests/test_design_results_rewrite.py` | 変更 | unread で止まること・PR の単位が 2 つで止まることのテストへ書き換える（N2b） | `plugins/ndf/scripts/supervise_lib/design_stage.py` |
| `plugins/ndf/scripts/tests/test_drive_refactor.py` | 変更 | pause の worker の起こし直しと手順書の届けのテストへ書き換える（N2a） | `plugins/ndf/scripts/supervise_lib/worker_steps.py` |
| `plugins/ndf/scripts/tests/test_external_ai_run.py` | 変更 | 子プロセスのケースの起動の確かめを順に依らない形にする（N9） | `plugins/ndf/skills/external-ai/scripts/external-ai.py` |
| `plugins/ndf/scripts/tests/test_glossary_split.py` | 新設 | コンテキストごとの出力と索引・行数の上限・古い文書の検出のテスト（N8a） | `plugins/ndf/scripts/glossary.py` |
| `plugins/ndf/scripts/tests/test_handoff.py` | 変更 | バイト数・雛形に無い節の退避・パスの実在のテストを足す（N8a） | `plugins/ndf/scripts/handoff.py` |
| `plugins/ndf/scripts/tests/test_hook_deadline.py` | 変更 | 壁時計の上限を待たなかった場合の時間に合わせる（N9） | 無し |
| `plugins/ndf/scripts/tests/test_lib_launch_cli_runtimes.py` | 変更 | 偽の kiro-cli が .amazonq/cli-agents を書き換えても起動の後に戻るテストを足す（N10） | 無し |
| `plugins/ndf/scripts/tests/test_lib_loop_drive.py` | 変更 | 案内の手段を受け取る形のテストへ作り直す（N5） | `plugins/ndf/scripts/lib/loop_drive.py` |
| `plugins/ndf/scripts/tests/test_merged_cleanup_stage.py` | 新設 | 後片付けの記録が閉じる課題ごとに 1 回だけ打たれるテスト（N8a） | `plugins/ndf/scripts/merged-steps.py` |
| `plugins/ndf/scripts/tests/test_monitor_layout.py` | 変更 | 500 行の確かめ（test_each_module_is_at_most_500_lines）を消す（N9）。コメントのスナップショットの 2 つの形（path:line [user] 本文・[PR-COMMENT] [user] 本文）を利用上限に読まないテストを足す（N10） | `scripts/check-script-structure.py（RF2）` |
| `plugins/ndf/scripts/tests/test_monitor_warnings.py` | 新設 | 警告を種類ごとに 1 回ずつ出すテスト（N10） | `plugins/ndf/scripts/lib/monitor_loop.py` |
| `plugins/ndf/scripts/tests/test_no_command_parsing.py` | 削除 | 外した関数が無いことだけを確かめる（入力の cross-refactoring/tests/ でなく plugins/ndf/scripts/tests/ にある）（N9） | 無し |
| `plugins/ndf/scripts/tests/test_parallel_end.py` | 変更 | step_halt の期待値を確かめた止まりの結果に合わせる（N3） | 無し |
| `plugins/ndf/scripts/tests/test_parallel_liveness.py` | 新設 | 生死の判定の単体テスト（DBOS と tmux を起こさない）（N3） | `plugins/ndf/scripts/lib/parallel_liveness.py` |
| `plugins/ndf/scripts/tests/test_parallel_run.py` | 変更 | 見回りの判定を _Run 越しに見るテストを外し、判定に従って問いを立てる経路だけを残す（N3） | 無し |
| `plugins/ndf/scripts/tests/test_parallel_stop_resume.py` | 新設 | 区切りで止める・止めた計画を同じ名前で再開する・リリースの連番が重ならないことの結合テスト（耐久の記録を実際に開く）（N3） | `plugins/ndf/scripts/parallel.py` |
| `plugins/ndf/scripts/tests/test_progress_record_stages.py` | 変更 | 通過記録を課題の本文へ直に置くフィクスチャで 2 件の前提を作る（N9） | 無し |
| `plugins/ndf/scripts/tests/test_project_mvv_signals_repo.py` | 新設 | worktree の判定の記録を数えることと unknown_pass を覆しに数えないことのテスト（N8b） | `plugins/ndf/scripts/lib/project_mvv_signals.py` |
| `plugins/ndf/scripts/tests/test_refs.py` | 新設 | 参照の種類ごとの解決と解決できない理由を DBOS も GitHub も起こさずに確かめる（N1） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/tests/test_relay.py` | 変更 | mark・tick・install の振る舞いを見るテストを上の 2 本へ移した分を外す（4,884 行）（N4）。test_guard_* と 3686 行の壁時計の確かめをシグナルファイルのファイルと十分な上限で待つ形にする（N9） | 無し |
| `plugins/ndf/scripts/tests/test_relay_account_use.py` | 変更 | 指定が ca.choose に効くテストへ作り直す（N4） | 無し |
| `plugins/ndf/scripts/tests/test_relay_hub.py` | 変更 | stop_named の確かめと ls の止まりの理由のテストを足す（N3）。restart の上限と停止の順、同じ名前の止まったラッパーの起動、起動の環境のテストを足す（N4） | 無し |
| `plugins/ndf/scripts/tests/test_relay_mark.py` | 新設 | hold_once と prose_wait_reason の単体テスト（hook の入力を JSON で渡す）（N4） | `plugins/ndf/scripts/relay_lib/mark.py` |
| `plugins/ndf/scripts/tests/test_relay_tick.py` | 新設 | _tick の判定と理由の記録の単体テスト（会話の記録の JSON Lines を一時ファイルで渡す）（N4） | `plugins/ndf/scripts/relay_lib/run.py` |
| `plugins/ndf/scripts/tests/test_release_version.py` | 変更 | queue_fill を直に呼ぶ版の差し込みのテストを refs の version の種類のテストへ作り直す（N1） | `plugins/ndf/scripts/lib/refs.py` |
| `plugins/ndf/scripts/tests/test_result_posts.py` | 変更 | push の拒否の後の取り込みと、管理情報の無いworktreeの理由のテストを足す（N6） | 無し |
| `plugins/ndf/scripts/tests/test_run_log.py` | 新設 | 書き口の単体テストと、子 4 つ × 100 行の並行の書き込み・SIGKILL の結合テスト（N10） | `plugins/ndf/scripts/lib/run_log.py` |
| `plugins/ndf/scripts/tests/test_spec_copy.py` | 変更 | 節の無い本文・前提の確認の無い本文で check が落ちるテストを足す（N2b） | `plugins/ndf/scripts/spec-copy.py` |
| `plugins/ndf/scripts/tests/test_spec_finalize_many.py` | 新設 | 複数の --spec がコミットに入ることと語ごとの出所のテスト（N8a） | `plugins/ndf/scripts/plan-to-spec-steps.py` |
| `plugins/ndf/scripts/tests/test_sprint_close_acceptance.py` | 変更 | 期待値を cmd_match で比べる（N9） | `plugins/ndf/scripts/tests/cmd_match.py` |
| `plugins/ndf/scripts/tests/test_sprint_close_depth_timeout.py` | 新設 | work ステップの打ち切りが宣言の秒になるテスト（N7） | `plugins/ndf/scripts/supervise_lib/sprint_close_steps.py` |
| `plugins/ndf/scripts/tests/test_sprint_close_verification_default.py` | 新設 | --with-verification なしでもリリース後テストの記録があれば条件 2 を見るテスト（N8a） | `plugins/ndf/scripts/sprint-close.py` |
| `plugins/ndf/scripts/tests/test_sprint_inputs.py` | 変更 | body_lack が共有の判定を使うことを確かめる（N2b） | `plugins/ndf/scripts/supervise_lib/sprint_inputs.py` |
| `plugins/ndf/scripts/tests/test_sprint_loop.py` | 変更 | 保留のループの 1 回への hold-question・遅れた finish の断り・止まったプランを 2 度流さない・設計 PR のマージで --designed を確かめる（N1）。g2_ledger が一時ディレクトリの下だけへ書く（N9） | `plugins/ndf/scripts/lib/sprint_loop.py` |
| `plugins/ndf/scripts/tests/test_stage_skills.py` | 変更 | pauses の読み取りと誤りのテストを足す（N2a） | `plugins/ndf/scripts/lib/stage_skills.py` |
| `plugins/ndf/scripts/tests/test_supervise.py` | 変更 | 登録の外れたworktreeと消えたworktreeで、後のステップと report がリポジトリの根で走るテストを足す（N2b）。検査のプランの refactor の --sync-command・review の行き先・振る舞いの確かめ直しのステップを、組んだプランの JSON で確かめる。あわせて _machine_body の表の載せ方（分岐の非 0・置き換え・全文）を確かめる（今の _machine_body のテストの置き場）（N11） | `plugins/ndf/scripts/supervise_lib/engine.py` |
| `plugins/ndf/scripts/tests/test_supervise_contract.py` | 変更 | 期待値を cmd_match で比べる（N9） | `plugins/ndf/scripts/tests/cmd_match.py` |
| `plugins/ndf/scripts/tests/test_supervise_layout.py` | 変更 | test_no_file_exceeds_500_lines を消し（構造の検査へ一本化）、test_each_subcommand_has_help を代表へ縮める（N9） | `scripts/check-script-structure.py（RF2）` |
| `plugins/ndf/scripts/tests/test_supervise_new.py` | 変更 | 実装のプランの pr のステップが append の既定を持つテストを足す（N2a） | `plugins/ndf/scripts/supervise_lib/templates.py` |
| `plugins/ndf/scripts/tests/test_supervise_plan_edit.py` | 新設 | plan-edit と plan-edits の単体テスト（N2a） | `plugins/ndf/scripts/supervise_lib/plan_edit.py` |
| `plugins/ndf/scripts/tests/test_supervise_queue.py` | 変更 | 宣言の無いプランが直列になるテストを足す（N2b） | `plugins/ndf/scripts/supervise_lib/admission.py` |
| `plugins/ndf/scripts/tests/test_supervise_queue_continue.py` | 変更 | `--from` のフルパス・相対パス・実行条件・完了の照合を確かめる（N1） | `plugins/ndf/scripts/supervise_lib/queue_resume.py` |
| `plugins/ndf/scripts/tests/test_supervise_stall.py` | 変更 | 壁時計の上限を待たなかった場合の時間に合わせる（N9） | 無し |
| `plugins/ndf/scripts/tests/test_test_guard.py` | 新設 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/tests/test_test_slots.py` | 新設 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/scripts/tests/test_test_strategy_collect.py` | 新設 | 収集の判定のテスト（N5） | `plugins/ndf/scripts/lib/test_strategy.py` |
| `plugins/ndf/scripts/tests/test_token_guard.py` | 変更 | 背景の Bash のつなぎと sleep の拒否・通すもののテストを足す（N4）。壁時計の上限を待たなかった場合の時間に合わせる（N9） | 無し |
| `plugins/ndf/scripts/tests/test_training_optout.py` | 変更 | 壁時計の上限を直し、test_experimental_copy_is_gone を消す（N9） | 無し |
| `plugins/ndf/scripts/tests/test_usage_transcript_counts.py` | 新設 | 同じ message.id の最後の行・2 回目の停止の差・期間の境界・JST の両端・最後の応答の補いのテスト（N8b） | `plugins/ndf/scripts/lib/usage_aggregate.py` |
| `plugins/ndf/scripts/tests/test_waiting_templates.py` | 変更 | 差し替えた間隔で 0 と 124 の両方を確かめる（N9） | `plugins/ndf/scripts/tests/waiting_templates.py` |
| `plugins/ndf/scripts/tests/test_whole_test_timing.py` | 変更 | started を回ごとの集まりで比べる（N9） | 無し |
| `plugins/ndf/scripts/tests/waiting_templates.py` | 変更 | 雛形を取り出すときに待ちの間隔（sleep 5）もテストの値へ差し替える（N9） | `plugins/ndf/skills/development-workflow/references/waiting.md` |
| `plugins/ndf/skills/backlog-refinement/SKILL.md` | 変更 | 状態の置き場と、持ち越しを次の回が拾うこと（--add で渡す手当てが要らないこと）を書く（N7） | 無し |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` | 変更 | 状態の置き場をメインディレクトリへ移し、上限で切る並びで manual と前回の持ち越しを先に残す（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py` |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py` | 変更 | 置き場の関数を状態にも使い、深さの上限（秒）を返す副命令と、打ち切りの記録（event timed_out と持ち越し）を持つ。既定から usd を外す（N7） | 無し |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py` | 変更 | 後ろ倒しを組まなかった回は後ろ倒しの却下を古いと見なさない。境界から判断待ちを除く（N7） | 無し |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py` | 変更 | 却下の照合を本人の候補に限り、依存先だけの一致を plan の誤りにする（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py` |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_inputs.py` | 変更 | 見直しのときは古い見積と前回の表から戻した見積を excluded で返す（N7） | 無し |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_routes.py` | 変更 | 前回の持ち越しを経路 carried として候補へ足す（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` |
| `plugins/ndf/skills/backlog-refinement/tests/test_upkeep_carry.py` | 新設 | 持ち越しが次の candidates で拾われることと、--add の課題が上限で切られないことのテスト（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` |
| `plugins/ndf/skills/backlog-refinement/tests/test_upkeep_rank.py` | 変更 | 容量の宣言があるときに判断待ちが境界に数えられないテストを足す（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py` |
| `plugins/ndf/skills/backlog-refinement/tests/test_upkeep_rejected.py` | 新設 | #1595 の最小の 4 候補の形と、前倒しを全部退けたときの後ろ倒しの却下のテスト（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py` |
| `plugins/ndf/skills/backlog-refinement/tests/test_upkeep_review_scoring.py` | 新設 | 見直しの順位の算出が古い見積を excluded で返すことと、直後の差分の棚卸しで見直しが起動しないことのテスト（N7） | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_inputs.py` |
| `plugins/ndf/skills/cross-refactoring/SKILL.md` | 変更 | --scope と触る関数の関係、範囲テストの絞り方、最終ゲートの範囲外の扱いを書く（N5） | 無し |
| `plugins/ndf/skills/cross-refactoring/prompts/final-fix.md` | 変更 | 直してよいファイルをリファクタリング計画の対象範囲に限り、範囲外の原因は直さず報告する指示を書く（N5） | 無し |
| `plugins/ndf/skills/cross-refactoring/prompts/judge.md` | 変更 | 結果の置き場を読み取り用ディレクトリの中に書く（N5） | 無し |
| `plugins/ndf/skills/cross-refactoring/prompts/plan.md` | 変更 | 結果の置き場を読み取り用ディレクトリの中に書く（N5） | 無し |
| `plugins/ndf/skills/cross-refactoring/prompts/propose.md` | 変更 | 提案の対象を触る関数に限る指示と、結果の置き場（読み取り用ディレクトリの中）を書く（N5） | 無し |
| `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 変更 | 打ち直しの手段を持たないことを keep_finished_output へ渡す（N5） | `plugins/ndf/scripts/lib/loop_drive.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/launch-cli.sh` | 変更 | propose・plan・judge の STEM を担当の読み取り用ディレクトリの .cross_refactoring/ に置き、終了後に一時ディレクトリへ回収する（N5） | `plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh` |
| `plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh` | 変更 | 読み取り用ディレクトリに .cross_refactoring/ を作り、既存の ignore_dir で差分から外す（N5） | 無し |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor.py` | 変更 | init で触る関数の集合を差分から引いて状態へ書く（明示の --symbols も受ける）（N5）。#2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/baseline.py` | 変更 | 着手前の範囲テストの {paths} へテストのファイルを渡す（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/final_fix.py` | 変更 | 最終ゲートの直しのコミットが範囲外のファイルを変えたら取り込まずに止まる（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope_check.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/gate.py` | 変更 | 範囲外の失敗を最終ゲートの記録へ残し、止まった理由を causes.json の種類で返す（N5） | `plugins/ndf/contracts/causes.json` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/implement.py` | 変更 | add-tests の確かめで収集の判定を呼び、収集されないテストを差し戻す（N5） | `plugins/ndf/scripts/lib/test_strategy.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/plan.py` | 変更 | 対応が引けなかった項目の由来（最終ゲートへ寄せた）を項目へ書く（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/targets.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/propose.py` | 変更 | 取り込みで触る関数の集合を状態から渡す（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/proposals.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/process.py` | 変更 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/proposals.py` | 変更 | 触る関数の外への提案を見送り（範囲外）にする（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` | 変更 | 差分が触る関数・シンボルを引く関数と、置き場所の配下のテストのファイルを並べる関数を持つ（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/codemetrics_read.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope_check.py` | 変更 | 範囲が空のとき検査を飛ばさず、呼ぶ側へ範囲が無いことを返す。最終ゲートの直しでは呼び手の書き換えの例外を使わない選択肢を持つ（N5） | 無し |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/targets.py` | 変更 | 項目の範囲テストの対象を、触ったモジュールを import するテストのファイルへ絞る。ディレクトリの対象を受けない（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/template_check.py` | 変更 | 試走の上限を下限つきで決める（N5） | `plugins/ndf/contracts/limits.json` |
| `plugins/ndf/skills/cross-refactoring/tests/test_added_tests_collected.py` | 新設 | PR #612 の R1-001 の形（補助モジュールへ足したテスト）を差し戻すテスト（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/implement.py` |
| `plugins/ndf/skills/cross-refactoring/tests/test_ci_only.py` | 変更 | 着手前の範囲テストへファイルを渡すことを確かめる形へ直す（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/baseline.py` |
| `plugins/ndf/skills/cross-refactoring/tests/test_final_fix_outside.py` | 新設 | 最終ゲートの直しが範囲外を変えたら止まるテスト（volareinc/carte の PR #81 の形）（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/final_fix.py` |
| `plugins/ndf/skills/cross-refactoring/tests/test_removed_review_phase.py` | 削除 | 外した副命令が無いことだけを確かめる（N9） | 無し |
| `plugins/ndf/skills/cross-refactoring/tests/test_scope_tests_by_imports.py` | 新設 | 範囲テストの対象の絞り込みと、引けないときの寄せのテスト（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/targets.py` |
| `plugins/ndf/skills/cross-refactoring/tests/test_template_check.py` | 変更 | 上限の直前で着手前のテストが通ったときも試走が ok になるテストを足す（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/template_check.py` |
| `plugins/ndf/skills/cross-refactoring/tests/test_touched_symbols.py` | 新設 | 差分から触る関数を引くことと、範囲外の提案の見送りのテスト（N5） | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` |
| `plugins/ndf/skills/cross-review/SKILL.md` | 変更 | 起動の 1 行を対象のリポジトリで打つ形にする（N6） | 無し |
| `plugins/ndf/skills/cross-review/scripts/drive.py` | 変更 | 打ち直しの手段として --reopen を keep_finished_output へ渡す（N5）。修正担当への指示と待つ結果ファイルを元の PR 番号でそろえる（N6） | `plugins/ndf/scripts/lib/loop_drive.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/commands/start_round.py` | 変更 | 修正必須の判定に記録が無く、結果ファイルが残っていれば取り込んでから進む（N6） | `plugins/ndf/skills/cross-review/scripts/review_lib/commands/merge_fix.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/fix_result.py` | 変更 | _matches_pr が元の番号と今の番号のどちらも受ける（N6） | `plugins/ndf/skills/cross-review/scripts/review_lib/store.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/github.py` | 変更 | 対象のリポジトリを決める順を 1 つの関数にし、gh repo view への落ちを外す（N6） | 無し |
| `plugins/ndf/skills/cross-review/scripts/review_lib/store.py` | 変更 | 今の PR 番号から元の PR 番号の状態ファイルを引く関数を足し、_resolve_tmp_dir の握りつぶし（except: pass）を外す（N6） | 無し |
| `plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py` | 変更 | worktreeの作成の後の lock、流し直しの初めの origin への同期、片付けの前の unlock（N6） | 無し |
| `plugins/ndf/skills/cross-review/tests/test_bg_wait.py` | 変更 | 壁時計の上限 4 か所を待たなかった場合の時間に合わせる（N9） | 無し |
| `plugins/ndf/skills/cross-review/tests/test_review_repo.py` | 新設 | 対象のリポジトリの決め方の単体テスト（N6） | `plugins/ndf/skills/cross-review/scripts/review_lib/github.py` |
| `plugins/ndf/skills/cross-review/tests/test_skill_layout.py` | 削除 | 文言・行数の固定（N9） | 無し |
| `plugins/ndf/skills/cross-review/tests/test_state_merge_fix.py` | 変更 | 巻き直しの後の番号で merge-fix を打つテストを足す（N6） | 無し |
| `plugins/ndf/skills/cross-review/tests/test_state_start_round_sync.py` | 変更 | 結果ファイルが残るときに start-round が取り込むテストを足す（N6） | 無し |
| `plugins/ndf/skills/cross-review/tests/test_workspace_sync.py` | 新設 | worktreeの lock・origin への同期の結合テスト（一時の git リポジトリ）（N6） | `plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py` |
| `plugins/ndf/skills/design/references/design-template.md` | 変更 | 設計の結果の書き方に、PR の単位の名前をセルの先頭の括弧に書く決まりを足す（N2b） | 無し |
| `plugins/ndf/skills/development-workflow/references/pace.md` | 変更 | 検査の記録の行の鍵の表へ run_id・failed_reason を足す（N10） | 無し |
| `plugins/ndf/skills/development-workflow/references/parallel-work.md` | 変更 | #2711 の設計文書の「構成要素」の役割（テストの同時実行の枠）（N12） | 無し |
| `plugins/ndf/skills/development-workflow/references/parallel.md` | 変更 | 「止める」と並べて「再開する」の手順を書き、問いの条件（開発ループの止まり・認証の止まり）を今の判定に合わせる（N3） | 無し |
| `plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh` | 変更 | 対象のリポジトリを取り出し、PR の問い合わせと本番チャネルの判定をその slug で行う（N10） | `plugins/ndf/scripts/hook.py` |
| `plugins/ndf/skills/development-workflow/tests/test_conductor_entrypoints.py` | 変更 | test_listed_subcommand_is_accepted を代表へ縮める（N9） | 無し |
| `plugins/ndf/skills/development-workflow/tests/test_workflow_guard.py` | 変更 | -R / --repo・REST の経路で別のリポジトリの PR を引くテストを足す（N10） | 無し |
| `plugins/ndf/skills/fix/scripts/fix-steps.py` | 変更 | result_dir の既定をworktreeの .cross_review にする（N6） | 無し |
| `plugins/ndf/skills/install-wrapper/SKILL.md` | 変更 | ndf-relay の置き場と、PATH に無いときの 1 行を書く（N4） | 無し |
| `plugins/ndf/skills/plan-to-spec/SKILL.md` | 変更 | spec-finalize に書き換えた仕様をすべて渡すことを書く（N8a） | 無し |
| `plugins/ndf/skills/progress-tracking/SKILL.md` | 変更 | --with-verification の説明を条件 2 の既定に合わせる（N8a） | 無し |
| `plugins/ndf/skills/refactoring/references/code-smells.md` | 変更 | 「手を付ける範囲」を課題が触る関数へ合わせる（利用者の判断の後）（N5） | 無し |
| `plugins/ndf/skills/release/tests/test_completion_check.py` | 変更 | test_the_skill_md_stays_within_its_budget を消す（N9） | 無し |
| `plugins/ndf/skills/requirements-design/references/glossary-format.md` | 変更 | 宣言の document の形と出力の形を書く（N8a） | 無し |
| `plugins/ndf/skills/retrospective/SKILL.md` | 変更 | スプリントを閉じる手順から --with-verification の付け忘れの余地を外す（N8a） | 無し |
| `plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py` | 変更 | 親ごと消えた行の回収と、照合の関数の境目の 6 件を reclaim を丸ごと打たずに確かめる（N9） | `plugins/ndf/scripts/lib/worktree-testenv-sweep.sh` |
| `scripts/check-doc-line-limit.py` | 変更 | EXEMPT から docs/glossary.md を外す（N8a） | `plugins/ndf/scripts/glossary.py` |
| `scripts/contracts_lib/model.py` | 変更 | 環境変数の行の任意の鍵 secret を受ける（N10） | 無し |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__run_log.py--fcntl--wrapped.json` | 新設 | run_log.py が fcntl を直に読む許可（N10） | 無し |
| `scripts/tests/test_root_conftest.py` | 変更 | 収集を 1 回にして分け方の判定へ渡す（N9） | 無し |

## クラスと関数の構造

型の関係は設計文書 [issue-1142-design.md](issue-1142-design.md) の「構造」の節を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/behavior-check.py` | モジュール | `behavior_since`（新設。N11） |
| `plugins/ndf/scripts/check-trigger.py` | モジュール | `findings_of`（変更。N1） |
| `plugins/ndf/scripts/design-match.py` | モジュール | `_path_present`（変更。N2b）・`extract_names`（変更。N11） |
| `plugins/ndf/scripts/glossary.py` | モジュール | `render_files`（新設。N8a） |
| `plugins/ndf/scripts/handoff.py` | モジュール | `check_body`（新設。N8a）・`trim`（変更。N8a） |
| `plugins/ndf/scripts/hook_lib/shell_checks.py` | モジュール | `chained_ndf_wait`（新設。N4） |
| `plugins/ndf/scripts/lib/acceptance.py` | モジュール | `section_lack`（新設。N2b） |
| `plugins/ndf/scripts/lib/claude_accounts.py` | モジュール | `choose`（変更。N4） |
| `plugins/ndf/scripts/lib/closing.py` | モジュール | `closing_commits`（新設。N2a） |
| `plugins/ndf/scripts/lib/deps.py` | モジュール | `python_request`（新設。N4） |
| `plugins/ndf/scripts/lib/launch-cli.sh` | モジュール | `run_runtime（kiro の枝）`（変更。N10） |
| `plugins/ndf/scripts/lib/monitor_loop.py` | モジュール | `monitor_agent`（変更。N10） |
| `plugins/ndf/scripts/lib/monitor_scan.py` | モジュール | `early_warnings`（新設。N10） |
| `plugins/ndf/scripts/lib/parallel_lane.py` | モジュール | `lane_resume_point`（新設。N3） |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | モジュール | `plan_state`（変更。N3） |
| `plugins/ndf/scripts/lib/parallel_liveness.py` | モジュール | `judge_lane`（新設。N3）・`plan_questions`（新設。N3） |
| `plugins/ndf/scripts/lib/parallel_run.py` | モジュール | `_Run.apply_liveness`（新設。N3） |
| `plugins/ndf/scripts/lib/project_mvv.py` | モジュール | `mvv_repo_key`（変更。N8b） |
| `plugins/ndf/scripts/lib/ref_sources.py` | モジュール | `closing_prs`（新設。N1）・`design_pr_state`（新設。N1） |
| `plugins/ndf/scripts/lib/refs.py` | モジュール | `resolve`（新設。N1）・`queue_prs_of`（新設。N1） |
| `plugins/ndf/scripts/lib/result_posts.py` | モジュール | `push_fix`（変更。N6）・`_resolve_fix_inputs`（変更。N6） |
| `plugins/ndf/scripts/lib/run_core.py` | モジュール | `main`（変更。N10） |
| `plugins/ndf/scripts/lib/run_log.py` | モジュール | `open_run`（新設。N10）・`diag`（新設。N10）・`clip`（新設。N10） |
| `plugins/ndf/scripts/lib/sprint_loop.py` | モジュール | `cmd_hold_question`（変更。N1） |
| `plugins/ndf/scripts/lib/stage_skills.py` | モジュール | `skills_for_pause`（新設。N2a） |
| `plugins/ndf/scripts/lib/test_strategy.py` | モジュール | `collected`（新設。N5） |
| `plugins/ndf/scripts/lib/usage_capture.py` | モジュール | `transcript_usage`（変更。N8b） |
| `plugins/ndf/scripts/lib/usage_ledger.py` | モジュール | `ledger_dir`（変更。N8b） |
| `plugins/ndf/scripts/lib/worktree-testenv-sweep.sh` | モジュール | `_reclaim_location_seen`（変更。N9）・`_reclaim_owner_of`（新設。N9） |
| `plugins/ndf/scripts/merged-steps.py` | モジュール | `remove_worktree`（変更。N6）・`record_cleanup_stage`（新設。N8a） |
| `plugins/ndf/scripts/parallel.py` | モジュール | `cmd_stop`（変更。N3） |
| `plugins/ndf/scripts/plan-to-spec-steps.py` | モジュール | `plan_glossary`（変更。N8a） |
| `plugins/ndf/scripts/relay_lib/hub.py` | モジュール | `last_block`（新設。N3）・`Hub.restart`（新設。N4） |
| `plugins/ndf/scripts/relay_lib/hub_client.py` | モジュール | `stop_named`（変更。N3） |
| `plugins/ndf/scripts/relay_lib/install.py` | モジュール | `place_launcher`（新設。N4） |
| `plugins/ndf/scripts/relay_lib/mark.py` | モジュール | `hold_once`（変更。N4）・`prose_wait_reason`（変更。N4） |
| `plugins/ndf/scripts/relay_lib/run.py` | モジュール | `Relay._tick`（変更。N4） |
| `plugins/ndf/scripts/spec-copy.py` | モジュール | `cmd_check`（変更。N2b） |
| `plugins/ndf/scripts/supervise_lib/admission.py` | モジュール | `overlap_of`（変更。N2b） |
| `plugins/ndf/scripts/supervise_lib/design_stage.py` | モジュール | `cmd_design_results`（変更。N2b） |
| `plugins/ndf/scripts/supervise_lib/engine.py` | モジュール | `Engine.keep_cwd`（変更。N2b） |
| `plugins/ndf/scripts/supervise_lib/paths.py` | モジュール | `plan_repo`（新設。N2b） |
| `plugins/ndf/scripts/supervise_lib/plan_edit.py` | モジュール | `edit_plan`（新設。N2a）・`list_edits`（新設。N2a） |
| `plugins/ndf/scripts/supervise_lib/pr.py` | モジュール | `PrStep._push`（変更。N2a）・`_machine_body`（変更。N11）・`recheck_of`（新設。N11） |
| `plugins/ndf/scripts/supervise_lib/queue_resume.py` | モジュール | `match_plan`（新設。N1）・`resume_targets`（変更。N1） |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | モジュール | `release_verify_cmd`（新設。N11） |
| `plugins/ndf/scripts/supervise_lib/steps.py` | モジュール | `deliver_skills`（変更。N2a） |
| `plugins/ndf/scripts/supervise_lib/templates.py` | モジュール | `acceptance_steps`（新設。N2b）・`_inspect_steps`（変更。N11）・`fix_worktree`（変更。N11） |
| `plugins/ndf/scripts/supervise_lib/worker_steps.py` | モジュール | `resume_until_result`（新設。N2a）・`DriveStep._delegate_pause`（変更。N2a） |
| `plugins/ndf/scripts/tests/cmd_match.py` | モジュール | `same_cmd`（新設。N9） |
| `plugins/ndf/scripts/tests/conftest.py` | モジュール | `_forbid_real_state`（新設。N9） |
| `plugins/ndf/scripts/tests/waiting_templates.py` | モジュール | `extract_template`（変更。N9） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` | モジュール | `state_dir`（新設。N7）・`_candidate_result`（変更。N7） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py` | モジュール | `depth_limits`（新設。N7） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py` | モジュール | `_stale_rejections`（変更。N7）・`_slots`（変更。N7） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py` | モジュール | `RankPlan.candidate`（変更。N7）・`RankPlan._check_want`（変更。N7） |
| `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_inputs.py` | モジュール | `estimates`（変更。N7） |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/baseline.py` | モジュール | `locations_of`（変更。N5） |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/final_fix.py` | モジュール | `_verify_final_fix_commits`（変更。N5） |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/implement.py` | モジュール | `_run_added_tests`（変更。N5） |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/proposals.py` | モジュール | `build_candidates`（変更。N5） |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` | モジュール | `touched_symbols`（新設。N5） |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/targets.py` | モジュール | `tests_for_change`（新設。N5） |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/template_check.py` | モジュール | `probe_budget`（新設。N5） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/commands/start_round.py` | モジュール | `_require_fix_for_changes`（変更。N6） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/github.py` | モジュール | `resolve_target_repo`（新設。N6） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/store.py` | モジュール | `state_pr`（新設。N6） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py` | モジュール | `sync_worktree`（新設。N6）・`lock_review_worktree`（新設。N6） |
| `plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh` | モジュール | `wf_merge_target`（変更。N10） |
| `scripts/tests/test_root_conftest.py` | モジュール | `_collected_ids`（変更。N9） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `resolve` | `plugins/ndf/scripts/lib/refs.py` | 新設 | `resolve(plan: dict, step: dict, ctx: RefContext, src: RefSources) -> Resolved` | （N1）プランとステップが持つ参照をすべて値へ解き、解いた値と出所、解けなかった参照と種類を返す | plan はプランの型で読んだもの。ctx は今の起動の items・状態ディレクトリ・開始のステップを持つ | Resolved.values は参照名から値へ、Resolved.unresolved は参照名から causes.json の種類と理由へ。空・0・unknown で埋めた値を values に入れない | 無し |
| `queue_prs_of` | `plugins/ndf/scripts/lib/refs.py` | 新設 | `queue_prs_of(items: list[dict], closing: list[int] ／ Unreadable, conditional: bool) -> list[int] ／ Unresolved` | （N1）前のステージの完了した計画の PR と、計画の課題を閉じる PR の和を重ねず昇順で返す | closing は ref_sources.closing_prs の結果 | 和が空で conditional なら空の並びを返し実行条件に任せる。空で conditional でなければ Unresolved（empty）、closing が読めなければ Unresolved（unreadable） | 無し |
| `match_plan` | `plugins/ndf/scripts/supervise_lib/queue_resume.py` | 新設 | `match_plan(plans: list[str], given: str, cwd: Path) -> list[str]` | （N1）`--from` に渡された名前・フルパス・相対パスを前の起動の一覧のプランへ照らす | plans は一覧の stages の絶対パス | ファイル名の stem・末尾の一致・解いた絶対パスの一致のどれかで当たったプランを返す。無ければ空 | 無し |
| `resume_targets` | `plugins/ndf/scripts/supervise_lib/queue_resume.py` | 変更 | `resume_targets(done: Path, overrides: dict[str, str], digests: dict[str, str]) -> ResumePlan` | （N1）前の起動の一覧と done から、プランごとに流す・流さない（完了）・人へ戻す（完了の後に変わった）と開始のステップを決める | 一覧（done の隣の plans.json）が stages を持つ | 完了でハッシュが同じプランは流さない。完了でハッシュが違い `--from` の無いプランは gate の対象に入る。`--from` は常に勝つ | 無し |
| `closing_prs` | `plugins/ndf/scripts/lib/ref_sources.py` | 新設 | `closing_prs(root: str, issues: list[int]) -> list[int] ／ Unreadable` | （N1）計画の課題を閉じる Pull Request の番号を GitHub から引く | issues は計画の `課題` の欄 | 番号の並びか、読めなかった理由 | gh の呼び出し |
| `design_pr_state` | `plugins/ndf/scripts/lib/ref_sources.py` | 新設 | `design_pr_state(root: str, issue: int) -> DesignPr ／ Unreadable` | （N1）課題の設計 PR の番号・マージの有無・ラベル・head のブランチを引く | root は git のリポジトリ | 設計 PR が無ければ DesignPr(number=None) | gh の呼び出し |
| `findings_of` | `plugins/ndf/scripts/check-trigger.py` | 変更 | `findings_of(state: Path) -> tuple[dict, str]` | （N1）検査の計画の進捗ログから、最後の頭からの実行より後の区間で、ステップごとに最後の件数を読む | state は計画の状態ディレクトリ | 進捗ログが無いか読めない行は飛ばし、読めない項目は null。1 回だけの実行は今と同じ値 | 無し |
| `cmd_hold_question` | `plugins/ndf/scripts/lib/sprint_loop.py` | 変更 | `cmd_hold_question(env: Env, name: str, ref: str, plan: str, step: str, parallel: str ／ None = None) -> dict` | （N1）`--plan` が属するループの 1 回（進行中か締め直しの途中の保留のループの 1 回）を引き、問いを question に積んで質問待ちにする | ループの記録がある | 当たるループの 1 回が無ければ記録を変えずに stopped（ループの 1 回が見つからないことと --plan）。保留のループの 1 回の release の答えと本番の版は残る | loop.json の書き込み |
| `resume_until_result` | `plugins/ndf/scripts/supervise_lib/worker_steps.py` | 新設 | `resume_until_result(ctx, system: str, res: dict, rfile: Path, cwd: str, timeout: int, *, full: bool) -> dict` | （N2a）結果ファイルが無いまま応答を終えた worker の会話を RESUME_PROMPT で RESUMES 回まで起こし直す（full のステップと pause の worker が共有する） | res は ctx.claude.call の結果。rfile は前の試行の分を消してある | rfile がある・会話の id が無い・回数を使い切った、のいずれかで最後の結果を返す | claude -p の起動と使用量の帳簿への記録 |
| `DriveStep._delegate_pause` | `plugins/ndf/scripts/supervise_lib/worker_steps.py` | 変更 | `_delegate_pause(self, ctx, step: dict, item: dict, kind: str, res_file: Path, cwd: str, texts: list[str]) -> Cause ／ None` | （N2a）pause を worker へ渡し、種類の手順書を届け、結果ファイルが無ければ起こし直し、それでも無ければ止まった理由の種類を返す | item は lib/drive_pause.pause の形（pause・prompt_file・result_file） | 成功なら None、失敗なら causes.json の種類（work_stopped・invalid）と理由 | worker の起動・帳簿・texts への追記 |
| `deliver_skills` | `plugins/ndf/scripts/supervise_lib/steps.py` | 変更 | `deliver_skills(ctx, step: dict, pause: str ／ None = None) -> tuple[str, str ／ None]` | （N2a）工程の行に加え、pause の種類があれば対応表の pauses から手順の Skill を引いて本文の節にする | 対応表が読める | (節, None) か ("", 誤り)。進捗ログに names と pause を 1 行書く | 進捗ログへの追記 |
| `skills_for_pause` | `plugins/ndf/scripts/lib/stage_skills.py` | 新設 | `skills_for_pause(kind: str, table: dict) -> list[str]` | （N2a）pause の種類に届ける手順の Skill の名前を表から返す | table は load_table が確かめた形 | 表に無い種類は空の並び（届けない） | 無し |
| `closing_commits` | `plugins/ndf/scripts/lib/closing.py` | 新設 | `closing_commits(root: str, base_ref: str) -> list[dict]` | （N2a）base_ref..HEAD のコミットのうち、件名か本文に GitHub のclosing keywordsを持つものを {sha, subject, words} で返す | root は git のworktree、base_ref は解決できる | 当たりが無ければ空の並び | git log の実行 |
| `PrStep._push` | `plugins/ndf/scripts/supervise_lib/pr.py` | 変更 | `_push(self, ctx, base: str) -> tuple[str, Cause ／ None]` | （N2a）push の前にclosing keywordsのコミットを照らし、当たれば push せずに refused で止め、当たらなければ push する | base は PR の宛先 | 当たれば結果の text にコミットと語と直し方（件名を `… （#N）` の参照の形へ直す）を載せる | git push |
| `edit_plan` | `plugins/ndf/scripts/supervise_lib/plan_edit.py` | 新設 | `edit_plan(plan: Path, new: Path, reason: str, now: str) -> dict` | （N2a）プランを new の中身へ置き換え、前の中身を控え、差分と理由と日時をプランの進捗ログへ 1 行書く | reason が空でない。new は読める JSON でステップの検査を通る | プランが new の中身になり、`<プラン>.bak-<日時>` と進捗ログの kind: plan_edit の行がある | プラン・控え・進捗ログへの書き込み |
| `list_edits` | `plugins/ndf/scripts/supervise_lib/plan_edit.py` | 新設 | `list_edits(sprint_dir: Path) -> list[dict]` | （N2a）スプリントの置き場の各プランの進捗ログから kind: plan_edit の行を集めて返す | sprint_dir がある | 日時の順の {plan, at, reason, diff} の並び | 無し |
| `overlap_of` | `plugins/ndf/scripts/supervise_lib/admission.py` | 変更 | `overlap_of(a_files: list[str] ／ None, b_files: list[str] ／ None, shared: Iterable[Mapping] = ()) -> list[str] ／ None` | （N2b）2 本のプランの重なりを返し、どちらかが宣言を持たなければ重なりとする | None は「触るファイルを宣言していない」、空の並びは「何も触らない」と区別して渡される | 宣言が無ければ ["(宣言なし)", "(宣言なし)"] のような印の組、等しいファイルか共有の一覧ならそのパス、どれも無ければ None | 無し |
| `section_lack` | `plugins/ndf/scripts/lib/acceptance.py` | 新設 | `section_lack(body: str) -> str ／ None` | （N2b）本文の `## 受け入れ条件` の節が無いか空なら理由を返す（sprint_inputs と spec-copy が共有する唯一の判定） | body は課題の本文の文字列 | 節があり空でない行が 1 行以上あれば None、無ければ「節が無い」、空なら「節が空」 | 無し |
| `cmd_check` | `plugins/ndf/scripts/spec-copy.py` | 変更 | `cmd_check(a) -> NoReturn` | （N2b）本文と写しの節の一致に加え、本文の受け入れ条件の節（section_lack）と、退行しない条件の前提の確認の行（premise_missing）を確かめる | 課題の本文と写しを読める | 欠けがあれば items に節の名前と理由を載せて終了コード 1（cause は check_failed） | gh issue view の実行 |
| `acceptance_steps` | `plugins/ndf/scripts/supervise_lib/templates.py` | 新設 | `acceptance_steps(issues: list[int], next_id: str) -> list[dict]` | （N2b）完了判定の工程に、worker が `acceptance-check.py record` で区分を書くステップと、`acceptance-check.py show --complete` で全件の区分を確かめる run のステップを組む | issues はプランの `課題`（取り込んだ課題を含む） | run のステップは区分の欠けで落ち、on_fail が区分のステップへ戻る | 無し |
| `cmd_design_results` | `plugins/ndf/scripts/supervise_lib/design_stage.py` | 変更 | `cmd_design_results(a, read=read_results) -> tuple[dict, int ／ None]` | （N2b）設計の結果を読み、読めなかった設計の課題か PR の単位が 2 つ以上の課題があれば、プランも design-results.json も書かずに止まる | a.design は設計承認ゲートを通った設計の課題 | unread が空でなければ status stopped・cause precondition・next に「設計 PR のマージの後に打ち直す」を載せ、プランを書かない | プランと design-results.json の書き込み（止まるときは書かない） |
| `_path_present` | `plugins/ndf/scripts/design-match.py` | 変更 | `_path_present(path: str, files: set[str], dirs: set[str]) -> str` | （N2b）根からのパス、ファイル名、`/` を含む名前の末尾の一致の順で探し、結果を present / missing / ambiguous で返す | files・dirs は HEAD の一覧 | 末尾の一致が 1 件なら present、2 件以上なら ambiguous（扱いは notes）、0 件なら missing | 無し |
| `plan_repo` | `plugins/ndf/scripts/supervise_lib/paths.py` | 新設 | `plan_repo(plan: Mapping) -> Path ／ None` | （N2b）プランの `リポジトリ`、無ければ `作業場所` の /.worktrees/ より前を、リポジトリの根として返す（engine・commands・steps の 3 か所の組み立てを 1 つにする） | plan は読んだプランの JSON | どちらからも決まらなければ None | 無し |
| `Engine.keep_cwd` | `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | `keep_cwd(self) -> None` | （N2b）作業場所が無いか、ディレクトリが残っていても `.git` の指す先が無ければ、plan_repo の根へ cwd を移す。report の冒頭でも呼ぶ | self.ctx.cwd は前のステップの作業場所 | cwd は使えるworktreeかリポジトリの根。根も無ければ cwd を変えず、進捗ログへ 1 行書く | self.ctx.cwd の書き換えと進捗ログへの追記 |
| `judge_lane` | `plugins/ndf/scripts/lib/parallel_liveness.py` | 新設 | `judge_lane(row: LsRow ／ None, loop: LoopView ／ None, launch: LaunchView ／ None, now: float, limit: float) -> Liveness` | （N3）1 本のレーンの生死を alive・stalled・loop_stopped・auth_blocked・ended・unknown のどれか 1 つと理由に決める | row は relay.py ls の 1 行（一覧が失敗した回は呼ばない）。loop は開発ループの記録の止まりの条件と時刻、launch は今の起動の指示時刻と再起動の試みの列 | Liveness は種類・理由の文・最後に動いた時刻・再起動の試みの回数を持つ。row が None なら ended ではなく unknown を返す（一覧に無いことを止まりと読まない） | 無し |
| `plan_questions` | `plugins/ndf/scripts/lib/parallel_liveness.py` | 新設 | `plan_questions(verdicts: dict[str, Liveness], open_questions: list[dict], stall_until: dict[str, float], now: float) -> QuestionPlan` | （N3）判定から、立てる問い（stall・helper・unended）と取り下げる問いと、起こし直すレーンを決める | open_questions は _Run.questions の写し | QuestionPlan は ask・withdraw・relaunch の 3 つの列を持つ。同じレーン・同じ区切りで問いは 1 件だけ | 無し |
| `_Run.apply_liveness` | `plugins/ndf/scripts/lib/parallel_run.py` | 新設 | `_Run.apply_liveness(self, plan: QuestionPlan) -> None` | （N3）plan_questions の結果どおりに問いを立て・取り下げ・起こし直しを launch_once へ渡す | 耐久ワークフローの見回りの 1 回の中で呼ぶ | questions と stall_until が更新され、publish_questions で wait へ出る | 耐久ワークフローの状態の更新と、起こし直しのときの relay.py restart の実行 |
| `stop_named` | `plugins/ndf/scripts/relay_lib/hub_client.py` | 変更 | `stop_named(name: str) -> StopOutcome` | （N3）1 本のラッパーを止め、tmux のセッションとラッパーのプロセスが終わったことを確かめた結果を返す | name は登録簿か作業ディレクトリにある名前 | StopOutcome は found・stopped・state（stopped / running / absent）・reason を持つ。セッションかプロセスが残れば stopped は偽で reason に残ったものを書く | 停止のファイルの書き込み・tmux kill-session・プロセスの木の停止・リレーハブの記録への 1 行 |
| `last_block` | `plugins/ndf/scripts/relay_lib/hub.py` | 新設 | `last_block(d: str) -> Block ／ None` | （N3）ラッパーの記録（log.jsonl）の最後の start より後に、認証で替えられるアカウントが無かった行（event: account・reason: auth・to: null）があればその理由と時刻を返す | d はラッパーの作業ディレクトリ | 該当の行が無ければ None。記録を読めなければ BlockReadError を投げる（止まりが無いと読まない） | 無し |
| `plan_state` | `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | `plan_state(main: Path, name: str, status: Optional[str]) -> str` | （N3）計画の状態に paused（区切りで止めた）を足し、running・paused・lost・closed・legacy・fresh のどれかを返す | status は耐久ワークフローの状態（無ければ None） | 耐久ワークフローが SUCCESS でも台帳の最後が paused の行なら paused。paused は run_plan の retire_if で記録を退避して台帳から作り直す対象になる | 無し |
| `lane_resume_point` | `plugins/ndf/scripts/lib/parallel_lane.py` | 新設 | `lane_resume_point(main: Path, plan: str, lane: str) -> ResumePoint` | （N3）レーンの次に流すもの（途中のプランの状態ディレクトリ・次のステップ・--resume の 1 行）を開発ループかスプリントの記録から読む | plan.json にそのレーンがある | ResumePoint は kind（plan / lap / none）・state_dir・step・command・reason を持つ。記録が無いときは kind が none で reason に読めなかったものを書く | 無し |
| `cmd_stop` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_stop(a: argparse.Namespace) -> None` | （N3）既定では各レーンへ区切りで止まる印を送り、--now では今どおりすぐ止め、どちらも各レーンの続きの位置を台帳の paused の行へ書く | 計画の検査が通る | 台帳に paused の行が 1 行あり、結果 JSON の items にレーンごとの続きの位置と止まったかが載る | 台帳への追記・耐久ワークフローへの stop の知らせ・relay.py stop の実行 |
| `hold_once` | `plugins/ndf/scripts/relay_lib/mark.py` | 変更 | `hold_once(d: str, section: int ／ None, block: str) -> bool` | （N4）背景の作業が残る ndf-next の応答で、同じ区間・同じ候補について Stop を 1 度だけ止めるかを決める | d はラッパーの作業ディレクトリ。block は応答の ndf-next の候補 | 同じ区間・同じ候補の印（held.json）が無ければ印を書いて真、あれば偽。stop_hook_active を読まない | held.json の書き込み |
| `prose_wait_reason` | `plugins/ndf/scripts/relay_lib/mark.py` | 変更 | `prose_wait_reason(d: str, record: RelayRecord, data: dict, asked_open: bool) -> WaitVerdict` | （N4）ブロックの無い応答が、このセッションの利用者の答えを本文で待って終わったかを、本文の分類と事実（背景の処理・判断の記録・別の場所の案内）から決める | data は Stop の hook の入力（background_tasks・last_assistant_message・transcript_path） | WaitVerdict は hold（真偽）・reason（差し戻しの文か None）・materials（判定に使った事実）を持つ。hold が真のときだけ wait-held.json を held: true にする | wait-held.json の書き込みと、ラッパーの記録への判定の 1 行 |
| `Relay._tick` | `plugins/ndf/scripts/relay_lib/run.py` | 変更 | `Relay._tick(self) -> TickDecision` | （N4）シグナルファイル・会話の記録・入力のアイドル・質問の有無から、次の区間へ切り替えるか、切り替えない理由を 1 つ決める | シグナルファイル（next.json）は first_written_at と written_at を持つ | TickDecision は switch（真偽）・reason（アイドルの途中・質問・取りやめ・応答の再開・停止シグナルファイル・再起動のループ）・mark を持つ。目標が未達の判定は first_written_at より後の goal_status で決める | 切り替えない理由が前の tick と変わったときだけラッパーの記録へ 1 行（同じ理由を tick ごとに書かない） |
| `Hub.restart` | `plugins/ndf/scripts/relay_lib/hub.py` | 新設 | `Hub.restart(self, req: dict, now: Live, registry: dict, ws: list[Wrapper]) -> dict` | （N4）restart の依頼で、対象を数えずに名前と上限を判定し、通ったときだけ対象を止めてから起こす | 対象の名前が登録簿にある。リレーハブ自身ではない | 断るときは対象を止めずに理由と終了コード 3。通れば launch の結果（起動の直後の生存の確かめを含む）を返す | 対象のラッパーの停止・tmux のセッションの作成・登録簿とリレーハブの記録の書き込み |
| `place_launcher` | `plugins/ndf/scripts/relay_lib/install.py` | 新設 | `place_launcher(bin_dir: str) -> LauncherResult` | （N4）PATH の入口 ndf-relay（~/.claude/ndf/relay.py を python3 で呼ぶだけの sh）を置き、PATH に bin_dir が無ければ入れる 1 行を返す | コピー（~/.claude/ndf/relay.py）を置いた後に呼ぶ | LauncherResult は path・placed（新しく置いたか）・on_path・hint を持つ。同じ中身が既にあれば書き直さない。別の中身の ndf-relay があれば上書きせずに理由を返す | bin_dir/ndf-relay の書き込み（0755） |
| `choose` | `plugins/ndf/scripts/lib/claude_accounts.py` | 変更 | `choose(exclude=(), keep=(), now: float ／ None = None, current: str ／ None = None) -> Choice` | （N4）使うアカウントを選ぶ。指定（pinned.json）があり、除外に入らず使える状態ならそれを、無ければ残りの量と閾値で選ぶ | アカウントの置き場（store_dir）がある | Choice に選んだ理由（pinned / usage）が載る。指定が使えないときは理由を Choice に載せて残りの量で選ぶ（黙って外さない） | 無し |
| `python_request` | `plugins/ndf/scripts/lib/deps.py` | 新設 | `python_request(root: Path = PLUGIN_ROOT) -> PythonChoice` | （N4）共有の依存の環境を作る python を、requires-python を満たすシステムの python があればそれ、無ければ uv 管理の python と 1 つに決める | root に pyproject.toml がある | PythonChoice は request（uv の --python に渡す値）・version（major.minor）を持つ。venv_dir は ~/.cache/ndf/venv/<NDF の版>-py<major.minor> を返す | 無し（python の版を問う子プロセスの起動だけ） |
| `chained_ndf_wait` | `plugins/ndf/scripts/hook_lib/shell_checks.py` | 新設 | `chained_ndf_wait(cmd: str) -> ChainHit ／ None` | （N4）コマンドの構文木から、NDF のスクリプトを 2 つ以上 && / ; でつないだ並びと、sleep の後に NDF のスクリプトを続ける並びを見つける | cmd は Bash の Tool の command | 見つかれば並びの種類と該当の語を返す。単独の supervise.py queue / wait・merged-steps.py merge-when-green と until [ -s <ファイル> ]; do sleep N; done は None | 無し |
| `collected` | `plugins/ndf/scripts/lib/test_strategy.py` | 新設 | `collected(strategy: Strategy, paths: list[str], work: str, timeout: float) -> CollectVerdict` | （N5）渡したテストのファイルのうち、受け持つ suite の全体の実行が収集するものと収集しないものを分けて返す | paths は作業ディレクトリからの相対パスで実在する。strategy は宣言から読んだもの | CollectVerdict は collected・uncollected・undecided（collect_command の宣言が無い suite のファイル）の 3 つの並びを持ち、どの paths もちょうど 1 つに入る | suite の collect_command（例: pytest --collect-only -q {paths}）を作業ディレクトリで 1 suite 1 回起動する |
| `_run_added_tests` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/implement.py` | 変更 | `_run_added_tests(path: pathlib.Path, state: dict[str, Any], intake: Intake) -> None` | （N5）足したテストが今のコードで通ることに加え、全体の実行に収集されることを確かめ、収集されないものを差し戻す | intake.accepted に項目ごとのコミットがある | 収集されないテストを足した項目は intake.sendbacks に kind=not_collected で載る。宣言が無く判定できない項目は項目の記録に undecided として残り、最終ゲートの全体テストへ寄せる | 範囲テストと収集の確かめを起動する。ログを tmp_dir へ書く |
| `touched_symbols` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` | 新設 | `touched_symbols(work: str, base: str, head: str, scope: list[str]) -> dict[str, set[str]]` | （N5）base..head の差分が触る行を含む関数・シンボルの名前を、範囲の中のファイルごとに返す | base と head が作業ディレクトリで解ける | 差分のある範囲の中のファイルだけがキーになる。関数の外の行（モジュールの頭など）だけを触ったファイルは空の集合を持つ | git diff を 1 回起動する。Python は codemetrics_read の構文木の読み、ほかの言語は lizard の関数の行の範囲を読む |
| `build_candidates` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/proposals.py` | 変更 | `build_candidates(proposals: dict[str, list[dict[str, Any]]], threshold: str, symbols: dict[str, set[str]], groups: int = CANDIDATE_GROUPS, per_group: int = CANDIDATES_PER_GROUP) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]` | （N5）鍵ごとにまとめた提案のうち、触る関数の外を指すものを out_of_scope で見送り、残りから候補を選ぶ | symbols は init が状態へ書いた触る関数の集合 | path が symbols に無いか、symbol が symbols[path] に無い提案は候補に入らず、見送りの理由 out_of_scope で返る | 無し |
| `locations_of` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/baseline.py` | 変更 | `scope_test_files(strategy: ts.Strategy, scope: list[str], work: pathlib.Path) -> list[str]` | （N5）CI に任せる戦略の着手前の範囲テストへ入れる、置き場所の配下のテストのファイルを返す（名前を変える） | scope は init の --scope | 返す並びはファイルだけで、ディレクトリを含まない。置き場所の配下にテストのファイルが 1 本も無ければ空 | git ls-files で置き場所の配下を読む |
| `probe_budget` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/template_check.py` | 新設 | `probe_budget(timeout: float, baseline_seconds: float, floor: float) -> float` | （N5）雛形の試走の上限を、着手前のテストの残りと下限の大きい方で決める | floor は limits.json の cross-refactoring.template-probe の秒 | 返す値は floor 以上 | 無し |
| `_verify_final_fix_commits` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/final_fix.py` | 変更 | `_verify_final_fix_commits(state: dict[str, Any], work: str, payload: dict[str, Any], ordered_range: list[str]) -> FinalFixVerdict` | （N5）最終ゲートの直しのコミットを確かめ、リファクタリング計画の対象範囲の外のファイルを変えたコミットを範囲外として返す | 状態に target_scope がある。無ければ呼ぶ側が止める | FinalFixVerdict は未申告・問題・範囲外のファイルの並びを持つ。範囲外が 1 件でもあれば取り込まない。呼び手の書き換えの例外は最終ゲートの直しには使わない | git を起動してコミットの事実とハンクを読む |
| `tests_for_change` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/targets.py` | 新設 | `tests_for_change(changed: list[str], work: str, strategy: ts.Strategy) -> TestPick` | （N5）項目が触るソースのファイルから、それを import するか名前の対応するテストのファイルを選ぶ | changed は項目の計画が触るファイル | TestPick は files（選んだテストのファイル）と unmapped（対応を引けなかったソース）を持つ。files にディレクトリは入らない | テストのファイルを読んで import を構文木で読む |
| `resolve_target_repo` | `plugins/ndf/skills/cross-review/scripts/review_lib/github.py` | 新設 | `resolve_target_repo(pr: int, repo_arg: str ／ None, worktree: str ／ None, cwd: str) -> RepoTarget` | （N6）レビューの対象のリポジトリを --repo・状態ファイル・cwd の origin の順で決める | 無し | RepoTarget は slug と出所（arg / state / origin）を持つ。どれでも決まらなければ RepoUnresolved を投げ、cwd が git リポジトリでないことを理由に書く（gh repo view へ落ちない） | git remote get-url の子プロセスの起動だけ |
| `push_fix` | `plugins/ndf/scripts/lib/result_posts.py` | 変更 | `push_fix(worktree: pathlib.Path ／ str, head_branch: str, fix_commit: str ／ None) -> PushResult` | （N6）修正を送り先のブランチへ送る。拒まれたら origin の先頭を取り込んで自分の未送信のコミットを載せ直し、1 回だけ送り直す | worktree は登録されたworktreeで、fix_commit がその HEAD の祖先 | PushResult に取り込んだか（rebased）と取り込んだ origin の先頭が載る。載せ直しで衝突したら取り込みを取り消して reason に diverged と衝突したファイルを書く | git fetch・rebase・push（強制の push はしない） |
| `sync_worktree` | `plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py` | 新設 | `sync_worktree(worktree: str, head_branch: str) -> SyncResult` | （N6）流し直しの初めに、レビュー用のworktreeを origin の先頭へ合わせ、送っていない自分のコミットだけを載せ直す | worktreeの登録がある | SyncResult は前の HEAD・新しい HEAD・載せ直したコミットの数・衝突したファイルを持つ。衝突したらworktreeを前の HEAD に戻す | git fetch・reset・cherry-pick（worktreeの中だけ） |
| `lock_review_worktree` | `plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py` | 新設 | `lock_review_worktree(worktree: str, pr: int) -> None` | （N6）worktreeを作った直後に git worktree lock --reason 'ndf cross-review pr<番号>' を掛ける | git worktree add が成功した後 | git worktree list --porcelain の行に locked が付く。prune はこの登録を消さない | リポジトリの .git/worktrees/<名前>/locked の作成 |
| `remove_worktree` | `plugins/ndf/scripts/merged-steps.py` | 変更 | `remove_worktree(root, path, label, merge_commit=None) -> tuple[bool, str ／ None]` | （N6）片付けでworktreeを外す。lock されていれば理由が NDF の cross-review のものだけ unlock してから外す | PR がマージか閉じられている | lock の理由が NDF のものでなければ外さずに理由を返す（利用者の lock を外さない） | git worktree unlock・remove |
| `_resolve_fix_inputs` | `plugins/ndf/scripts/lib/result_posts.py` | 変更 | `_resolve_fix_inputs(args: argparse.Namespace) -> tuple[FixInputs ／ None, Stop ／ None]` | （N6）単独の fix 命令の入力を決める前に、worktreeの管理情報があるかを確かめる | 無し | 管理情報が無ければ Stop(cause='worktree_unregistered', worktreeのパスと .git が指す先)。リポジトリは resolve_target_repo の規則で決める | git rev-parse の子プロセスの起動だけ |
| `state_pr` | `plugins/ndf/skills/cross-review/scripts/review_lib/store.py` | 新設 | `state_pr(pr: int) -> StateRef` | （N6）渡された PR 番号が巻き直しの後の番号なら、元の番号の状態ファイルを引く | 状態の置き場（CROSS_REVIEW_TMP_DIR かworktreeの .cross_review）が決まっている | StateRef は origin_pr・current_pr・path を持つ。どの状態ファイルの current_pr にも当たらなければ origin_pr = pr | 無し |
| `_require_fix_for_changes` | `plugins/ndf/skills/cross-review/scripts/review_lib/commands/start_round.py` | 変更 | `_require_fix_for_changes(round_no: int, verdict: str ／ None, fix: dict ／ None, pending: pathlib.Path ／ None) -> FixGate` | （N6）修正必須の判定に修正の記録が無いとき、結果ファイルが残っていれば取り込む手順へ回す | pending は元の PR 番号の結果ファイルのパス（無ければ None） | FixGate は ok・ingest（取り込むファイル）・stop（理由と次に打つ 1 行）のどれか。終了コード 5 で止めるのは結果ファイルも無いときだけ | 無し（取り込みは呼び出し元が merge_fix.ingest_fix で行う） |
| `RankPlan.candidate` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py` | 変更 | `candidate(self, n: int, direction: str) -> dict ／ None` | （N7）plan の番号と向きに当たる本人の候補だけを返す | rank.json を読んである | f["number"] == n の候補だけを返す。依存先（group）にしか無い番号は None | 無し |
| `RankPlan._check_want` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py` | 変更 | `_check_want(self, n: int, d: str, act: dict ／ str, forward_acts: set) -> str ／ None` | （N7）plan の却下と reschedule が本人の候補を指すかを確かめ、依存先だけに当たる番号を誤りの文にする | candidate が本人の候補だけを返す | 依存先だけに当たる番号は「#n は #m の依存先で、本人の候補が無い」の誤りになり、apply は書かずに終了コード 2 | 無し |
| `_stale_rejections` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py` | 変更 | `_stale_rejections(rejected: list[dict], seen: set[str], built: set[str]) -> list[dict]` | （N7）この回に組んだ向き（built）の却下のうち、どの候補とも合わないものだけを古い記録として返す | built は前倒し・後ろ倒しのうちこの回に候補を組んだ向き | 後ろ倒しを組まなかった回は、後ろ倒しの却下を返さない | 無し |
| `state_dir` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` | 新設 | `state_dir(root: Path, repo: str, arg: str ／ None) -> Path` | （N7）upkeep の状態の置き場を、引数 → NDF_UPKEEP_STATE_DIR → メインディレクトリの .ndf/backlog/state/<repo> の順に決めて返す（_state_dir を置き換える） | root は作業ディレクトリかメインディレクトリ | 返すディレクトリは存在する。一時ディレクトリへは倒さない | ディレクトリを作る |
| `_candidate_result` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` | 変更 | `_candidate_result(a, repo, since, open_issues, closed, paths, idents, routes, empty_milestones, notes, waits, carried: list[int]) -> tuple[dict, list[int]]` | （N7）候補を上限で切るとき manual → commit-subject → 前回の持ち越し → 経路の数 → 番号の順で残し、切った分を持ち越しとして返す | carried は前回の candidates.json の metrics.deferred のうち今も open の番号 | manual の課題は上限を超えても items に残る（上限より manual が多いときの扱いは notes）。deferred は candidates.json に書かれ、次の回の carried になる | 無し（書くのは呼ぶ側の cmd_candidates） |
| `_slots` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py` | 変更 | `_slots(board, ranked) -> tuple[dict ／ None, dict ／ None, dict ／ None]` | （N7）大きい課題の枠・品質の枠・切り出しの境界を決める。境界へは尺度を超える課題と人の判断待ちの課題を渡さない | ranked の行は waiting_decision を持つ | 境界の中に判断待ちの行が入らない | 無し |
| `depth_limits` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py` | 新設 | `depth_limits(root: Path, depth: str) -> DepthLimits` | （N7）見直し・大整理の 1 回の秒の上限を .ndf/backlog.json の depths と既定から返す（副命令 depth limits <深さ> の中身） | depth は review か overhaul | DepthLimits.seconds は正の整数。既定から usd を外す | 宣言のファイルを読む |
| `estimates` | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_inputs.py` | 変更 | `estimates(issues, scores, stored, previous, cfg, since=None, digests: dict[int, str] ／ None = None) -> tuple[dict, list, list]` | （N7）課題ごとの見積を --scores → 記録 → 前回の表の順に採る。digests を渡す（見直しの回）と、要約値の違う記録と前回の表から戻した見積を excluded に入れる | digests は今の課題の要約値（snapshot_digest） | 見直しの回の excluded に、理由「見積が古い（採点し直す）」の課題が並ぶ。差分の棚卸し（digests なし）は今のとおり | 無し |
| `render_files` | `plugins/ndf/scripts/glossary.py` | 新設 | `render_files(g: dict, decl: Declaration) -> dict[str, str]` | （N8a）用語集からコンテキストごとのファイル（docs/glossary/<id>.md）と索引（docs/glossary.md）の本文を作り、パス → 本文で返す（render_text を置き換える） | decl.document が索引のパス、decl.document_dir が出力先のディレクトリ | どのファイルも上限の行数以下。上限を超えるコンテキストがあれば RenderError を上げる（扱いは notes） | 無し（書くのは cmd_render と plan-to-spec の write_glossary） |
| `plan_glossary` | `plugins/ndf/scripts/plan-to-spec-steps.py` | 変更 | `plan_glossary(loaded, removed: set[str], specs: list[str], root: Path) -> GlossaryPlan ／ None` | （N8a）消す設計を pending_source に持つ語の source を、その語を本文に含む確定仕様のファイルへ移す | specs はリポジトリの根からの相対パスで実在する | 語ごとに本文に語を含むファイルが 1 つならそれを source にする。0 か 2 以上なら StepError（--source-of <語>=<ファイル> で明示させる）。先頭のファイルへ倒さない | specs の本文を読む |
| `check_body` | `plugins/ndf/scripts/handoff.py` | 新設 | `check_body(text: str, root: Path, limits: HandoffLimits) -> CheckReport` | （N8a）引継ぎ文書の本体の節の形・行数・バイト数・本文に書いたパスの実在を確かめた結果を返す（cmd_check の判定の中身） | text は本体の全文。limits は limits.json の行数とバイト数 | CheckReport は violations・lines・bytes・missing_paths を持ち、どれか 1 つでも当たれば通らない | パスの実在を見るためファイルシステムを読む |
| `trim` | `plugins/ndf/scripts/handoff.py` | 変更 | `trim(text: str, history: Path, name: str) -> tuple[str, list[str]]` | （N8a）「前の会話の進み」の節に加え、雛形に無い節を退避先の末尾へ移す | 雛形の節の一覧を読める | 返す本文の ## の節は雛形の節だけになる | 退避先のファイルへ追記する |
| `record_cleanup_stage` | `plugins/ndf/scripts/merged-steps.py` | 新設 | `record_cleanup_stage(main_dir: str, prs: list[int]) -> list[dict]` | （N8a）後片付けを終えた PR が閉じる課題ごとに、工程「後片付け」が未記録なら progress-record.sh で記録し、結果の項目を返す | 後片付けの本体（cleanup）が終わっている | 記録済みの課題（本文の - [x] 後片付け）は打ち直さず skipped。記録した課題は recorded | gh で PR の閉じる課題と課題の本文を読み、progress-record.sh を起動して本文を書き換える |
| `transcript_usage` | `plugins/ndf/scripts/lib/usage_capture.py` | 変更 | `transcript_usage(path: Path, since: datetime ／ None = None, until: datetime ／ None = None) -> tuple[object, str]` | （N8b）会話の記録 1 本の使用量を、message.id ごとに最後の行の usage で 1 回だけ数え、期間を渡すと応答の時刻が期間内の応答だけを数える | path は Claude Code の会話の記録 | 同じ id の行が複数あれば最後の行の値。期間外の応答は入らない。数える応答が無ければ "unknown" | ファイルを読む |
| `ledger_dir` | `plugins/ndf/scripts/lib/usage_ledger.py` | 変更 | `ledger_dir(env: dict ／ None = None) -> Path` | （N8b）帳簿のディレクトリを NDF_USAGE_DIR → 状態のホームの usage の順に決める。CLAUDE_PLUGIN_DATA は見ない | 無し | hook から呼んでもスクリプトから呼んでも同じディレクトリを返す。HOME も無ければ例外（/tmp へ倒さない） | 無し |
| `mvv_repo_key` | `plugins/ndf/scripts/lib/project_mvv.py` | 変更 | `mvv_repo_key(root) -> str` | （N8b）MVV の記録の repo を、worktree ならメインディレクトリの絶対パスで返す | root は git のworktree | 同じリポジトリの worktree とメインディレクトリで同じ値 | decl.main_dir が git を 1 回起動する（プロセスの中で覚える） |
| `_reclaim_location_seen` | `plugins/ndf/scripts/lib/worktree-testenv-sweep.sh` | 変更 | `_reclaim_location_seen <worktree のパス>（終了コード 0 = 確かめられた）` | （N9）消えた worktree の置き場が、回収を打った位置から確かめられるかを返す | MAIN_DIR が設定されている | メインディレクトリの下か、親か最も近い残っている祖先がメインディレクトリと同じファイルシステムなら 0。祖先が / まで残らないか別のファイルシステムなら 1（/tmp の誤判定を防ぐ今の規則を保つ） | 無し（stat を読むだけ） |
| `_reclaim_owner_of` | `plugins/ndf/scripts/lib/worktree-testenv-sweep.sh` | 新設 | `_reclaim_owner_of <ps の出力のファイル> <行の worktree の一覧のファイル>（RECLAIM_OWNER と RECLAIM_WHY を設定）` | （N9）読み込んだコンテナの作業ディレクトリと行の worktree の一覧から、持ち主を none・own・foreign に決める | 2 つのファイルは読める | docker と台帳を呼ばない。_reclaim_check_owner は読み込みとこの関数の呼び出しだけになる | 無し |
| `_forbid_real_state` | `plugins/ndf/scripts/tests/conftest.py` | 新設 | `_forbid_real_state(tmp_path_factory, monkeypatch) -> None（autouse の fixture）` | （N9）テストの間、本物の状態の根を指す環境変数をすべて一時ディレクトリへ向け、本物の根の下のパスを開いて書くと落とす | 本物の根は Path.home() から決める | 本物の根の下へ書くテストは失敗し、パスが出る | 環境変数の差し替えと open の監視（テストの間だけ） |
| `same_cmd` | `plugins/ndf/scripts/tests/cmd_match.py` | 新設 | `same_cmd(actual: str, expected: list[str]) -> bool` | （N9）コマンドの文字列を shlex.split して語の並びで比べる | actual は shlex.quote で組んだコマンド | 引用の有無とパスの文字の種類に依らず同じ結果 | 無し |
| `extract_template` | `plugins/ndf/scripts/tests/waiting_templates.py` | 変更 | `extract_template(name: str, limit: int, interval: float = 0.2) -> str` | （N9）waiting.md の待ちの雛形を取り出し、上限と待ちの間隔をテストの値へ差し替える | 雛形に sleep 5 と上限の秒が 1 か所ずつある | 差し替えられなければ落ちる（雛形の形が変わったことを知らせる） | 無し |
| `_collected_ids` | `scripts/tests/test_root_conftest.py` | 変更 | `_collected_ids() -> list[str] と _shard_of(ids: list[str], shard: tuple[int, int]) -> list[str]` | （N9）収集を 1 回だけ子プロセスで走らせ、分け方の判定は収集の結果へ当てる | SHARD_TOTAL・SHARD_INDEX を外した環境 | 子プロセスの起動が 3 回から 1 回になり、分け方の判定の結果は今と同じ | pytest --collect-only の子プロセス 1 回 |
| `open_run` | `plugins/ndf/scripts/lib/run_log.py` | 新設 | `open_run(entry: str, run_id: str, parent_id: str ／ None, location: str ／ None) -> RunLog` | （N10）出力先を設定（NDF_RUN_LOG_SINK、既定は file）から決めて開き、start の行を書き、開いた記録をプロセスに 1 つ持つ | プロセスでまだ記録を開いていない | current() が開いた RunLog を返す。子へ渡す NDF_RUN_ID・NDF_RUN_LOG が決まる | 記録のファイルの作成と start の行の追記、起点の実行では 30 日を超えた日付のディレクトリの削除 |
| `diag` | `plugins/ndf/scripts/lib/run_log.py` | 新設 | `diag(text: str, short: str ／ None = None, **fields) -> None` | （N10）診断を標準エラー（short があれば short）と実行の記録（event: diag、全文）の両方へ出す | 無し（記録が開いていなければ、親の文脈を受け継いでいるときだけ子の実行として開く） | 標準エラーへの文は渡された文のまま（改行を潰さず足さない）。記録の行は秘密を伏せた後 | 標準エラーへの書き込みと記録への追記 |
| `clip` | `plugins/ndf/scripts/lib/run_log.py` | 新設 | `clip(text: str, limit: int, what: str) -> str` | （N10）人へ見せる文を limit 字に切る前に、全文を記録へ残す | limit は 1 以上 | limit 字以下ならそのまま返す。超えれば記録へ全文と切った文を書いてから先頭 limit 字を返す | 記録への追記（標準エラーへは書かない） |
| `main` | `plugins/ndf/scripts/lib/run_core.py` | 変更 | `main(entry_id: str, body: Callable, argv: list[str] ／ None = None) -> NoReturn` | （N10）実行の識別子を振り、書き口を開き、本体の結果と止まった理由を切らずに終わりの行へ書いてから結果 JSON を出す | entry_id が契約（contracts/entrypoints/）にある | 終わりの行の fields は結果 JSON の全体と終了コードを持つ。記録に書けなかったときは結果が stopped | 記録への start・end の行、標準出力への結果 JSON、子へ渡す環境変数の書き換え |
| `early_warnings` | `plugins/ndf/scripts/lib/monitor_scan.py` | 新設 | `early_warnings(paths: monitor_types.AgentPaths, agent: str) -> dict[str, str]` | （N10）早期の警告を種類（stderr・denials）ごとの組で返す | paths の各ファイルは無くてもよい | 種類から警告の文への辞書。警告が無い種類は鍵を持たない | 無し |
| `monitor_agent` | `plugins/ndf/scripts/lib/monitor_loop.py` | 変更 | `monitor_agent(...) -> monitor_types.AgentStatus（引数は今のまま）` | （N10）監視のループで、早期の警告を種類ごとに 1 回ずつ run_log.diag で出す | 今のまま | warned_early_error の 1 個の真偽を、出した種類の集合に替える。標準エラーへ出す文は今と同じ | 標準エラーと実行の記録への診断 |
| `wf_merge_target` | `plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh` | 変更 | `wf_merge_target <コマンド> -> 1 行に「<slug> <番号>」（slug が無ければ - ）` | （N10）gh pr merge と REST のマージの経路から、対象のリポジトリと PR の番号の組を取り出す | コマンドは Bash の Tool の command | -R / --repo / -R=<slug> / --repo=<slug> / repos/<所有者>/<名前>/pulls/<番号>/merge を読む。どれも無ければ slug は -（作業ディレクトリの origin を使う） | 無し |
| `run_runtime（kiro の枝）` | `plugins/ndf/scripts/lib/launch-cli.sh` | 変更 | `run_runtime -> 終了コード（kiro のとき）` | （N10）kiro-cli を起動する前にworktreeの .amazonq/cli-agents の追跡の状態を控え、終わった後に追跡対象の変更を戻し、起動の間に増えた .bak を消す | WORKDIR は git のworktree | 起動の前後で git status --porcelain -- .amazonq の結果が同じ | worktreeの .amazonq/cli-agents の書き戻しと .bak の削除 |
| `_machine_body` | `plugins/ndf/scripts/supervise_lib/pr.py` | 変更 | `_machine_body(ctx: RunContext) -> str` | （N11）PR 本文の「テスト」の表を、ステップごとに最後の結果で組む | ctx.state.results にステップの exit・text・cause がある | 行き先で分岐した非 0（causes.json の種類が分岐を表すもの）は失敗として載らず、同じ確かめの再確認（requirements-recheck など）があればその結果で置き換わる。要約は切らない | 無し |
| `recheck_of` | `plugins/ndf/scripts/supervise_lib/pr.py` | 新設 | `recheck_of(step_id: str, steps: list[dict]) -> str ／ None` | （N11）確かめのステップに対応する再確認のステップの id を、プランのステップの宣言（`rechecks: <id>`）から引く | steps はプランの steps | 宣言が無ければ None | 無し |
| `_inspect_steps` | `plugins/ndf/scripts/supervise_lib/templates.py` | 変更 | `_inspect_steps(a, pr_ref: str, scope: str) -> list[dict]` | （N11）検査の assess・refactor・review のステップを組む | a.sync_checks は宣言から読んだ同期とチェックの並び | 同期のコマンドがあれば refactor の cmd に `--sync-command` が付く。review は on_cause で「未解決 0 件の上限・error」を次の工程へ、それ以外を人へ渡す | 無し |
| `fix_worktree` | `plugins/ndf/scripts/supervise_lib/templates.py` | 変更 | `fix_worktree(branch: str, root: Path) -> str` | （N11）new fix に --worktree が無いときの作業場所を、メインディレクトリの .worktrees/<ブランチ> にする | root は今のディレクトリ | `decl.main_dir(root)` が解けた場所の下を返す。解けなければ例外 DeclError（今のディレクトリへ倒れない） | git の起動（main_dir の中） |
| `behavior_since` | `plugins/ndf/scripts/behavior-check.py` | 新設 | `behavior_since(pr: int, root: Path, recorded: str, head: str) -> SinceResult` | （N11）振る舞いの確認を記録した head から今の head までの差分に、振る舞いを決める文書があるかを返す | recorded は最後の記録の head の SHA | changed（確かめ直しが要る）・same（要らない）・unreadable のどれか | git の起動 |
| `release_verify_cmd` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 新設 | `release_verify_cmd(ref: str, version: str, runtimes: str, worktree: str) -> str` | （N11）verify のステップのコマンドを、メインディレクトリで取り込まずにリリースの worktree を `--root` に渡す形で組む | worktree はリリースのプランの作業場所 | cmd に git pull を含まない | 無し |
| `extract_names` | `plugins/ndf/scripts/design-match.py` | 変更 | `extract_names(doc: str, text: str, seen: set[str]) -> tuple[list[dict], int]` | （N11）設計文書からテストとファイルの名前を集める | text は設計文書の本文 | 「採らなかった案」の節の名前と、改名の宣言（`A → B`）の前の名前は返す並びに入らない | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `resolve` | （N1）参照を解けない（記録の PR が無い・版を読めない・前のプランの完了した結果が無い・`{queue_prs}` の集合が空） | 前の起動の成果が無いか GitHub・origin を読めない | ステップの直前（キューのステージの前と run のステップの前） | 呼び出し元へ返す | キューは項目に「流さなかった」・unfilled・causes.json の種類と理由。run は最初のステップを打たずに stopped と gaps の ref:<名前> | 無し（プランのファイルを書き換えないため戻すものが無い） |
| `closing_prs` | （N1）gh が失敗する・上限で終わらない | GitHub の一時障害か権限 | refs の queue_prs の解決 | 呼び出し元へ返す | Unreadable（理由つき）。refs が空と区別して unresolved に入れる | 無し |
| `design_pr_state` | （N1）gh が失敗する | GitHub の一時障害 | loop sprint の組み立てと出し直しの設計のプランの組み立て | 止める | loop sprint が stopped と種類（GitHub を読めない）と next（同じコマンド）を返し、プランを組まない | 無し |
| `resume_targets` | （N1）完了の後にプランのファイルが変わっている | conductor がプランを書き足した | queue --resume | 止める | gate（終了コード 10 番台）、変わったプランと `--from <プラン>=<ステップ>` のコマンドを next に | 無し |
| `retire（lib/durable.py）` | （N1）耐久の記録を退避できない（lock が掴まれている・移せない） | 同じキューがまだ動いている | queue --fresh | 止める | stopped と動いているキューの done のパス。何も流さない | 途中まで移したファイルを元の名前へ戻す |
| `cmd_hold_question` | （N1）当たるループの 1 回が無い | --plan がどのループの 1 回のプランでもない | loop hold-question | 呼び出し元へ返す | stopped（ループの 1 回が見つからないことと渡した --plan） | 無し（記録を書く前に判定する） |
| `resume_until_result` | （N2a）起こし直しても結果ファイルが無い | worker が背景の待ちで終わるか、指示を守らない | worker_steps.py | 呼び出し元へ返す | work_stopped と「<種類> の worker が RESUMES 回起こし直しても結果ファイルを書かなかった」 | 無し（worktreeの未コミットの変更は残し、次のステップの判断に任せる） |
| `resume_until_result` | （N2a）会話の id が無く起こし直せない | --no-session-persistence の起動 | worker_steps.py | 呼び出し元へ返す | work_stopped と「会話を残さない起動のため起こし直せない」 | 無し |
| `deliver_skills` | （N2a）対応表か SKILL.md を読めない | リリースの欠け・表の形の誤り | steps.py | 止める | precondition と読めなかったパス | 無し（worker を起動しない） |
| `closing_commits` | （N2a）git log が失敗する | base_ref が無い・worktreeでない | closing.py | 呼び出し元へ返す | 例外（呼び手の _push が invalid として止まる） | 無し |
| `PrStep._push` | （N2a）closing keywordsのコミットがある | worker が `Fix: #N` の件名で書いた | pr.py | 止める | refused と、当たったコミットの sha・件名・語 | 無し（push しない） |
| `edit_plan` | （N2a）理由が空・新しい中身が読めない・ステップの検査に通らない | 呼び出しの誤り | plan_edit.py | 止める | invalid と理由（プランは書き換えない） | 無し |
| `edit_plan` | （N2a）同じプランを別の plan-edit が書き換えている | conductor の並行の操作 | plan_edit.py | 止める | refused（錠を取れない） | 無し |
| `cmd_design_results` | （N2b）設計の結果を読めない設計の課題がある | 設計 PR がまだマージされていない・表が無い | design_stage.py | 止める | stopped、cause precondition、metrics.unread、next に設計 PR の番号と打ち直しのコマンド | 無し（プランと design-results.json を書かない） |
| `cmd_design_results` | （N2b）1 つの課題に PR の単位が 2 つ以上ある | 設計が実装を複数の PR に分けた | design_stage.py | 止める | stopped、cause precondition、課題の番号と単位の名前 | 無し |
| `cmd_check` | （N2b）受け入れ条件の節が無い・空・前提の確認の行が無い | 要求の工程が本文の形を外した | spec-copy.py | 呼び出し元へ返す | 終了コード 1、items に理由（プランは on_fail の requirements へ進む） | 無し |
| `acceptance_steps` | （N2b）区分の欠けが残る | worker が一部の条件だけを記録した | templates.py（run のステップ） | 再試行 | show --complete の終了コード 1 と欠けた条件の番号。上限の回数の後は work_stopped で止まる | 無し |
| `Engine.keep_cwd` | （N2b）リポジトリの根も無い | プランの `リポジトリ` の誤り・根の移動 | engine.py | 呼び出し元へ返す | cwd を変えず、後のステップが invalid（worktreeでない）で止まる。進捗ログに 1 行 | 無し |
| `overlap_of` | （N2b）プランの JSON を読めない | 壊れたプランのファイル | queue.py の touched_files | 止める | invalid とプランのパス（空の宣言として流さない） | 無し |
| `stop_named` | （N3）tmux のセッションかラッパーのプロセスが待ちの秒数の後も残る | kill-session の失敗、プロセスが TERM を無視、ls の pending の行 | relay_lib/hub_client.py | 呼び出し元へ返す | StopOutcome(stopped=False, state='running', reason=残ったもの)。cmd_stop_named は終了コード 1 と cause を返す | 止まりかけの印（停止のファイル）は残す。次の stop が同じ印を使う |
| `last_block` | （N3）ラッパーの記録を読めない | 権限・壊れた行・ディレクトリの消失 | relay_lib/hub.py | 呼び出し元へ返す | BlockReadError。_ls_item は行に block_error を書き、judge_lane は unknown を返す | 無し |
| `_Run.apply_liveness` | （N3）起こし直しの relay.py restart が断られるか立ち上がらない | 上限・tmux の失敗・アカウントがまだ使えない | lib/parallel_run.py と lib/parallel_launch.py | 再試行 | launch_once の打ち直し 1 回の後も立ち上がらなければ区分 spawn の問い（今の決まり） | 無し |
| `cmd_stop` | （N3）台帳へ paused の行を書けない | OSError・排他の時間切れ | parallel.py | 止める | 結果 stopped・cause は状態の書き込みの種類（SF1a の causes.json）。耐久ワークフローへ stop を送らずに終わる（続きの位置の無いまま止めない） | 無し |
| `cmd_stop` | （N3）区切りの待ちの上限を過ぎても止まらないレーンがある | 長いステップ・止まったままのレーン | parallel.py | 呼び出し元へ返す | 結果 stopped・not_stopped にレーン名・next に --now の 1 行 | 台帳の paused の行は残す（--now で同じ行を上書きしない、続きの位置を足すだけ） |
| `lane_resume_point` | （N3）開発ループかスプリントの記録が無いか読めない | 引継ぎだけで動くレーン・記録の破損 | lib/parallel_lane.py | 呼び出し元へ返す | ResumePoint(kind='none', reason=読めなかったパス)。cmd_stop は items にそのまま載せ、再開はレーンの引継ぎ文書の起動の 1 行で起こす | 無し |
| `prose_wait_reason` | （N4）状態ファイル（wait-held.json・asked.json）を読めないか書けない | 権限・壊れた JSON | relay_lib/mark.py | 呼び出し元へ返す | WaitVerdict(hold=False, reason=None, materials に読めなかったファイル)。cmd_mark はラッパーの記録へ判定できなかった 1 行を書く（今の except Exception: return None の握りつぶしをやめる） | 無し |
| `Relay._tick` | （N4）シグナルファイルの時刻を読めない | written_at が無い・形が違う | relay_lib/run.py | 止める | halt('bad-mark', 理由)。今の parse_iso(...) or time.time() で今の時刻へ倒す形をやめる | シグナルファイルを消す（drop_mark） |
| `Hub.restart` | （N4）対象を止めた後に起動が失敗する | tmux の失敗・起動の直後のセッションの消失 | relay_lib/hub.py | 呼び出し元へ返す | tmux-failed と終了コード 1。登録簿の行は残し、次の restart で同じ名前を起こせる | 無し |
| `place_launcher` | （N4）bin_dir に書けない・別の中身の ndf-relay がある | 権限・利用者が置いた同名のファイル | relay_lib/install.py | 呼び出し元へ返す | install は ndf-relay を置けなかった理由を 1 行出し、ラッパーの導入そのものは成功で終える（終了コードは 0 のまま、status に置けていないことを出す） | 無し |
| `choose` | （N4）pinned.json を読めない | 壊れた JSON・権限 | lib/claude_accounts.py | 呼び出し元へ返す | PinReadError。今の relay_lib/pin.pinned の「読めなければ None」をやめ、呼び出し元（区間の起動・supervise の子）が理由を出して止める | 無し |
| `python_request` | （N4）要件を満たす python が無く、uv 管理の python も入れられない | システムの python が古い・ネットワークが無い | lib/deps.py | 止める | EXIT_DEPS_MISSING（69）と理由（_stop の今の形） | 無し |
| `chained_ndf_wait` | （N4）コマンドを構文木にできない | shparse が読めない構文 | hook_lib/shell_checks.py | 呼び出し元へ返す | None ではなく ParseFailed を返し、guard_sleep は拒まずに通す（今の has_unreadable_error と同じ扱い）。通したことを hook の記録へ 1 行残す | 無し |
| `collected` | （N5）collect_command が起動できない・終了コードが 0 と 5 以外 | 宣言のテンプレートの誤りかランナーが無い | suite ごとの起動 | 呼び出し元へ返す | CollectVerdict ではなく例外 CollectError（suite 名・コマンド・終了コード） | 無し |
| `collected` | （N5）上限の秒を超える | 収集が遅い | suite ごとの起動 | 呼び出し元へ返す | CollectError（timed_out） | 起動したプロセスを止める |
| `_run_added_tests` | （N5）収集の確かめが CollectError を返す | 宣言の誤り | add-tests の取り込み | 止める | launch.stop と同じ止まり方で、causes.json の種類（案: test_collect_failed）を返す | 取り込みの記録は書かない |
| `touched_symbols` | （N5）git diff が失敗する・ファイルを構文木として読めない | base が無い・構文の誤り | init | 止める | init が終了コード 4 で止まり、causes.json の種類（案: scope_unreadable）とファイル名を返す。ファイル単位へ倒さない | 状態ファイルは書かない |
| `_verify_final_fix_commits` | （N5）範囲外のファイルを変えたコミットがある | 落ちたテストの原因が範囲外にある | merge-final-fix | 止める | 最終ゲートの記録に outside_failures（落ちたテスト・変えた範囲外のファイル）を書き、causes.json の種類（案: final_gate_outside_scope）で返す | 直しのコミットは HEAD へ取り込まない（今の _close_failed_final_fix で戻す） |
| `tests_for_change` | （N5）テストのファイルを読めない | 権限・壊れたファイル | plan の範囲テストの組み立て | 呼び出し元へ返す | そのソースを unmapped に入れ、理由を項目に残す | 無し |
| `resolve_target_repo` | （N6）対象のリポジトリが決まらない | cwd が git リポジトリでない・origin が無い・--repo も状態ファイルも無い | review_lib/github.py | 止める | 終了コード 3 と「対象のリポジトリを決められない。対象のリポジトリで打つか --repo を渡す」 | 無し |
| `push_fix` | （N6）取り込みで衝突する | 同じ行を別の push が変えた | lib/result_posts.py | 呼び出し元へ返す | PushResult(ok=False, reason='diverged', 衝突したファイル)。merge-fix は終了コード 1 で、手で取り込む 1 行を next に載せる | git rebase --abort でworktreeを取り込みの前へ戻す |
| `push_fix` | （N6）取り込んだ後の送り直しも拒まれる | 取り込みの間にさらに別の push | lib/result_posts.py | 呼び出し元へ返す | PushResult(ok=False, reason='rejected_twice')。再試行は 1 回だけ | 無し（worktreeは取り込んだ後のまま。次の流し直しの sync_worktree が合わせる） |
| `sync_worktree` | （N6）載せ直しで衝突する | 自分の未送信のコミットと origin の先頭が同じ行を変えた | review_lib/workspace.py | 止める | SyncResult に衝突したファイル。init は終了コード 1 で止まり、前の HEAD のコミットを理由に書く | 前の HEAD へ戻す（コミットを失わない） |
| `lock_review_worktree` | （N6）lock を掛けられない | git の失敗・管理情報への書き込み権限 | review_lib/workspace.py | 止める | worktreeを外してから終了コード 1（lock の無いまま作業を始めない） | git worktree remove で作ったばかりのworktreeを外す |
| `remove_worktree` | （N6）lock の理由が NDF のものでない | 利用者が手で lock した | merged-steps.py | 呼び出し元へ返す | 外さずに items へ理由（lock の理由の文）を載せる | 無し |
| `_resolve_fix_inputs` | （N6）worktreeの管理情報が無い | 別の環境の git worktree prune・片付け | lib/result_posts.py | 止める | 終了コード 1 と「worktreeの管理情報が無い（<パス>、.git が指す <先>）」。リポジトリを決められないとは書かない | 無し |
| `state_dir` | （N7）メインディレクトリを解けない・ディレクトリを作れない | git の外・権限 | candidates・rank・apply の頭 | 止める | 終了コード 2 と causes.json の種類（案: state_dir_unavailable）。一時ディレクトリへ倒さない | 無し |
| `_candidate_result の呼び出し元（cmd_candidates）` | （N7）前回の candidates.json が壊れている | 書きかけ・手での編集 | carried を読むとき | 止める | 終了コード 2（今の _read_state と同じ）。空の持ち越しとして続けない | 無し |
| `RankPlan.update_rejected` | （N7）rejected.json を書けない | 権限・ディスク | apply | 呼び出し元へ返す | StepError を apply が受け、終了コード 2 | jsonio.write_atomic のため書きかけは残らない |
| `depth_limits` | （N7）depths の宣言の形が違う | 宣言の誤り | プランを組むとき（sprint_close_steps） | 止める | プランを組まずに終了コード 2 と宣言のキーを返す。5400 秒へ倒さない | 無し |
| `sprint_close_steps の work ステップ` | （N7）宣言の秒で打ち切られる | 候補が多い | 見直し・大整理の本体 | 呼び出し元へ返す | on_fail で depth record --event timed_out を打ち、残りの番号を depths.jsonl の行へ書く | owner release を今どおり打つ |
| `render_files` | （N8a）1 つのコンテキストが行数の上限を超える | ndf-workflow のコンテキストが 474 行（2026-11-10）で伸び続けている | render・render --check・spec-finalize | 止める | 終了コード 1 とコンテキストの id・行数。扱い（さらに分ける・その 1 ファイルだけ除外する）は notes | ファイルを書かない |
| `plan_glossary` | （N8a）語を含む確定仕様のファイルが 0 か 2 以上 | 定義をどのファイルにも書いていない・複数に書いた | spec-finalize | 止める | 終了コード 2（EXIT_PRECONDITION）と語の一覧。設計を消さない | 無し（書き込みの前に判定する） |
| `check_body` | （N8a）本文に書いたパスが無い | 作る前に「作成済み」と書いた | handoff.py check（書いた直後と区間の頭） | 呼び出し元へ返す | 終了コード 1 と missing_paths の一覧 | 無し |
| `trim` | （N8a）退避先を書けない | 権限・ディスク | check --trim | 止める | 終了コード 2。本体は書き換えない | 退避先を先に書き、成功してから本体を書く順にする |
| `record_cleanup_stage` | （N8a）PR の閉じる課題を読めない・progress-record.sh が失敗する | gh の失敗・本文の更新の失敗 | merged-steps.py cleanup の後 | 呼び出し元へ返す | 課題ごとの項目を failed にし、後片付けの結果を partial（causes.json の種類。案: stage_record_failed）で返す。後片付けの本体は戻さない | 無し |
| `transcript_usage` | （N8b）会話の記録を読めない | 消えた・権限 | SubagentStop の hook と集計 | 既定値で続ける | "unknown" を返し、帳簿の行は unknown になる（確定仕様の I5。hook を落とさない） | 無し |
| `ledger_dir` | （N8b）HOME も NDF_STATE_HOME も無い | 環境変数の無い起動 | 帳簿へ書く口と usage-report.py | 呼び出し元へ返す | 例外。書く口（append_safely）は帳簿へ書かずに標準エラーへ 1 行出して続ける（I5）。集計は終了コード 2 | 無し |
| `mvv_repo_key` | （N8b）メインディレクトリを解けない | git の外 | MVV の記録を書くとき | 呼び出し元へ返す | 例外。今の「根の絶対パス」へ倒さない | 無し |
| `_reclaim_location_seen` | （N9）祖先の stat を読めない | 権限か、/ まで祖先が残らない | worktree-testenv.sh reclaim | 呼び出し元へ返す | unseen のまま（理由「回収を打った位置から worktree の置き場を確かめられない」） | 無し |
| `_reclaim_check_owner（読み込みの側）` | （N9）docker ps が失敗するか上限で終わらない | docker の不調 | reclaim | 呼び出し元へ返す | 今と同じく RECLAIM_OWNER=failed と理由 | 一時ファイルは RECLAIM_TMP ごと消す（今のまま） |
| `_forbid_real_state` | （N9）テストが本物の根の下へ書こうとした | 環境変数を通らずにパスを決めるコードかヘルパー | pytest の各テスト | 止める | そのテストの失敗とパス | 書く前に落とすため本物の台帳は変わらない |
| `open_run` | （N10）記録の置き場に書けない・設定の値が知らない出力先 | 権限・ディスクの空き・NDF_RUN_LOG_SINK の誤り | lib/run_log.py | 止める | 標準エラーに理由、結果は stopped（#2338 の前提 5）。cause は SF1a の causes.json の状態の書き込みの種類 | 無し |
| `diag` | （N10）途中で記録へ書けなくなる | ディスクの空き・置き場の消失 | lib/run_log.py | 呼び出し元へ返す | 例外を投げずに RunLog.failed に理由を持ち、標準エラーへ理由を 1 回だけ出す。核は終わりの前後で failed を見て結果を stopped にする | 無し |
| `open_run` | （N10）30 日を超えた日付のディレクトリを消せない | 権限・別のプロセスが使っている | lib/run_log.py | 既定値で続ける | 診断の行を書いて本体を続ける（#2338 の受け入れ条件 1e） | 無し |
| `wf_merge_target` | （N10）対象のリポジトリの宣言（.ndf/project.json）を読めない | 対象が別のリポジトリで GitHub から読めない・権限が無い | workflow-merge.sh と hook.py merge-target | 止める | wf_deny_undetermined で「宛先が本番系へ出るか（対象のリポジトリの宣言を読めない）」と理由を出してマージを止める | 無し |
| `run_runtime（kiro の枝）` | （N10）起動の後に .amazonq を戻せない | git checkout の失敗・worktreeでない | lib/launch-cli.sh | 呼び出し元へ返す | err.log へ戻せなかったパスを 1 行書き、kiro の終了コードとは別に起動の記録（<stem>-launch.json）へ restore_failed を残す | 控えた写し（stem の隣）は残し、手で戻せるようにする |
| `fix_worktree` | （N11）メインディレクトリが解けない | git でないディレクトリで打った | supervise.py new fix | 止める | 終了コード 2 と「--worktree を渡すか git のリポジトリで打つ」 | 無し（プランを書く前に判定する） |
| `behavior_since` | （N11）差分を取れない（記録の head が手元に無い・fetch できない） | head が force-push で消えた・GitHub の一時障害 | 検査のプランのマージの前 | 呼び出し元へ返す | unreadable。プランは確かめ直しの側（確かめ直すか人へ戻す）へ進み、黙って通さない | 無し |
| `release_verify_cmd で組んだ verify のステップ` | （N11）verify-install が落ちる | リリースが届いていない・ランタイムの導入の失敗 | リリースのプラン | 呼び出し元へ返す | 今と同じく verify の終了コードと種類。メインディレクトリの未コミットの変更が理由になることは無くなる | 無し |
| `DriveStep._delegate_pause` | （N2a）副作用（worker の起動・帳簿・texts への追記）が失敗する | ファイル・外部コマンドの誤り | `DriveStep._delegate_pause` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `hold_once` | （N4）副作用（held.json の書き込み）が失敗する | ファイル・外部コマンドの誤り | `hold_once` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `locations_of` | （N5）副作用（git ls-files で置き場所の配下を読む）が失敗する | ファイル・外部コマンドの誤り | `locations_of` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `_require_fix_for_changes` | （N6）副作用（無し（取り込みは呼び出し元が merge_fix.ingest_fix で行う））が失敗する | ファイル・外部コマンドの誤り | `_require_fix_for_changes` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `_candidate_result` | （N7）副作用（無し（書くのは呼ぶ側の cmd_candidates））が失敗する | ファイル・外部コマンドの誤り | `_candidate_result` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `_collected_ids` | （N9）副作用（pytest --collect-only の子プロセス 1 回）が失敗する | ファイル・外部コマンドの誤り | `_collected_ids` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `clip` | （N10）副作用（記録への追記（標準エラーへは書かない））が失敗する | ファイル・外部コマンドの誤り | `clip` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `main` | （N10）副作用（記録への start・end の行、標準出力への結果 JSON、子へ渡す環境変数の書き換え）が失敗する | ファイル・外部コマンドの誤り | `main` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |
| `monitor_agent` | （N10）副作用（標準エラーと実行の記録への診断）が失敗する | ファイル・外部コマンドの誤り | `monitor_agent` の中 | 呼び出し元へ返す | 止まった理由の種類（`contracts/causes.json`）を持つ結果 JSON。既定値へ倒さない | 書きかけを残さず、打ち直せる |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| 子 Issue の PR | 解消する Issue の直しが develop に既にある（設計文書の決定 72 の 11 件） | 新しい変更を作らず、再現の手順を 1 回通した結果と `Closes` の行を PR 本文に載せる |
| 子 Issue の PR | 再現の手順で直っていない | 単体テストの観点の行を足して直す |
| 子 Issue の PR | 直す場所が流す順序の後ろの子 Issue と重なる | 自分の範囲の関数だけを直し、重なる行は後ろの子 Issue が base-sync の後に直す |

- 繰り返しの終わる条件: 待ちと打ち直しは、どれも `contracts/limits.json` の上限（回数か秒）で終わる。上限に達したら止まった理由の種類を返し、続けない
- 計算量の注意: N1 の参照の解決と N3 の台帳の読み直しは、プランと台帳の行の数に比例する。外部の呼び出し（`gh`）はループの中で打たず、1 回のクエリでまとめる（smells の決定 41）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `RefKind` | 型 | `plugins/ndf/scripts/lib/refs.py` | （N1）version・record_pr・queue_prs・plan_result・counts の 5 種類 |
| `Resolved` | 型 | `plugins/ndf/scripts/lib/refs.py` | （N1）values（参照名→値）・sources（参照名→出所）・unresolved（参照名→種類と理由） |
| `ResumePlan` | 型 | `plugins/ndf/scripts/supervise_lib/queue_resume.py` | （N1）stages・max・プランごとの (開始のステップ, 流す／流さない／人へ戻す, 理由) |
| `ref-unresolved・ref-unreadable・plan-changed-after-done` | 設定値 | plugins/ndf/contracts/causes.json（SF1a が置く表へ行を足す） | （N1）行き先は stopped・stopped・gate |
| `PR_BODY_DRAFT` | 定数 | `plugins/ndf/scripts/supervise_lib/paths.py` | （N2a）{state_dir}/work/pr_body.md |
| `pauses` | 設定値 | `plugins/ndf/scripts/lib/stage-skills.json` | （N2a）{"fix": ["fix"], "sweep": ["fix"]} |
| `PLAN_EDIT_KIND` | 定数 | `plugins/ndf/scripts/supervise_lib/plan_edit.py` | （N2a）plan_edit |
| `RESUMES` | 定数 | `plugins/ndf/scripts/supervise_lib/worker_steps.py` | （N2a）3（既存。pause の worker にも当てる） |
| `UNDECLARED` | 定数 | `plugins/ndf/scripts/supervise_lib/admission.py` | （N2b）(宣言なし) |
| `premise_marker` | 設定値 | plugins/ndf/scripts/lib/acceptance.py（置き場は notes） | （N2b）前提の確認: |
| `acceptance の再試行の上限` | 設定値 | `plugins/ndf/contracts/limits.json` | （N2b）未決（既存の fix の上限に合わせる案） |
| `Liveness` | 型 | `plugins/ndf/scripts/lib/parallel_liveness.py` | （N3）NamedTuple(kind, reason, last_moved, restarts) |
| `ResumePoint` | 型 | `plugins/ndf/scripts/lib/parallel_lane.py` | （N3）NamedTuple(kind, state_dir, step, command, reason) |
| `StopOutcome` | 型 | `plugins/ndf/scripts/relay_lib/hub_client.py` | （N3）NamedTuple(found, stopped, state, reason) |
| `parallel.stop-boundary` | 設定値 | `plugins/ndf/contracts/limits.json` | （N3）区切りを待つ上限の秒数。値は設計で決める（案: wait の上限と同じ 3600） |
| `parallel-repo.stall` | 設定値 | plugins/ndf/contracts/limits.json（今ある。NDF_PARALLEL_STALL_MINUTES で分を変えられる） | （N3）3600 秒（#2505 の決定のまま変えない） |
| `WaitVerdict` | 型 | `plugins/ndf/scripts/relay_lib/mark.py` | （N4）NamedTuple(hold, reason, materials) |
| `TickDecision` | 型 | `plugins/ndf/scripts/relay_lib/run.py` | （N4）NamedTuple(switch, reason, mark) |
| `PythonChoice` | 型 | `plugins/ndf/scripts/lib/deps.py` | （N4）NamedTuple(request, version) |
| `LAUNCHER_DIR` | 定数 | `plugins/ndf/scripts/relay_lib/install.py` | （N4）~/.local/bin |
| `LANE_ENV` | 定数 | `plugins/ndf/scripts/relay_lib/hub.py` | （N4）{"CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false"} |
| `NDF_SLEEP_GUARD` | 設定値 | `plugins/ndf/contracts/env/NDF_SLEEP_GUARD.json（今は無い。足す）` | （N4）有効（0 で止める。背景の Bash のつなぎの拒否も同じ変数で止める） |
| `test.suites[].collect_command` | 設定値 | .ndf/project.json（読み手は plugins/ndf/scripts/lib/test_strategy.py） | （N5）無し（無ければ収集の判定は undecided） |
| `cross-refactoring.template-probe` | 設定値 | `plugins/ndf/contracts/limits.json` | （N5）設計で決める（案: 120 秒。PR #2152 の試走は 2.26 秒、uv と pytest-xdist の起動を含めても十分な値） |
| `OUT_OF_SCOPE` | 定数 | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/proposals.py` | （N5）"out_of_scope"（見送りの理由） |
| `symbols` | 型 | cross-refactoring の状態ファイル | （N5）dict[str, list[str]]（ファイル → 触る関数） |
| `CollectVerdict・TestPick・FinalFixVerdict` | 型 | `plugins/ndf/scripts/lib/test_strategy.py・plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/targets.py・plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/final_fix.py` | （N5）dataclass |
| `LOCK_REASON` | 定数 | `plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py` | （N6）ndf cross-review pr<番号> |
| `RepoTarget` | 型 | `plugins/ndf/skills/cross-review/scripts/review_lib/github.py` | （N6）NamedTuple(slug, source) |
| `SyncResult` | 型 | `plugins/ndf/skills/cross-review/scripts/review_lib/workspace.py` | （N6）NamedTuple(before, after, replayed, conflicts) |
| `StateRef` | 型 | `plugins/ndf/skills/cross-review/scripts/review_lib/store.py` | （N6）NamedTuple(origin_pr, current_pr, path) |
| `push の再試行の回数` | 定数 | `plugins/ndf/scripts/lib/result_posts.py` | （N6）1 |
| `ROUTE_CARRIED` | 定数 | `plugins/ndf/skills/backlog-refinement/scripts/upkeep_routes.py` | （N7）"carried"（ROUTES へ足す） |
| `depths.review.seconds・depths.overhaul.seconds` | 設定値 | .ndf/backlog.json（既定は plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py の DEFAULTS） | （N7）review 1800 秒（今の既定）。overhaul は今の既定に秒が無く、足す値は設計で決める |
| `depths.<深さ>.usd` | 設定値 | plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py の DEFAULTS | （N7）外す（読み手が無い） |
| `STATE_SUBDIR` | 定数 | `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` | （N7）"state"（.ndf/backlog/state/<repo>） |
| `document_dir` | 設定値 | `.ndf/glossary.json` | （N8a）"docs/glossary/"（索引は document の docs/glossary.md のまま） |
| `max_lines（用語集の 1 ファイル）` | 設定値 | `.ndf/glossary.json` | （N8a）500 |
| `handoff.max-lines・handoff.max-bytes` | 設定値 | `plugins/ndf/contracts/limits.json` | （N8a）300 行（今の DEFAULT_MAX_LINES）・バイト数は設計で決める（案: 16000。m653 の区間で 1 回の読みが 32,302 文字） |
| `NAME_RE` | 定数 | `plugins/ndf/scripts/handoff.py` | （N8a）parallel-<計画名> を足す |
| `OVERRIDE_UNKNOWN_PASS` | 定数 | `plugins/ndf/scripts/lib/project_mvv_signals.py` | （N8b）"unknown_pass" |
| `JST の両端` | 定数 | `plugins/ndf/scripts/lib/usage_aggregate.py` | （N8b）<since>T00:00:00+09:00..<until>T23:59:59+09:00 |
| `REAL_STATE_ROOTS` | 定数 | `plugins/ndf/scripts/tests/conftest.py` | （N9）~/.claude/ndf/state と ~/.local/state/ndf |
| `TEST_WAIT_INTERVAL` | 定数 | `plugins/ndf/scripts/tests/waiting_templates.py` | （N9）0.2 秒 |
| `run-log.line` | 設定値 | `plugins/ndf/contracts/limits.json` | （N10）bytes: 32000 |
| `KEEP_DAYS` | 定数 | `plugins/ndf/scripts/lib/durable_keys.py` | （N10）30 |
| `NDF_RUN_LOG_SINK` | 設定値 | `plugins/ndf/contracts/env/NDF_RUN_LOG_SINK.json` | （N10）file |
| `RunLog` | 型 | `plugins/ndf/scripts/lib/run_log.py` | （N10）run_id・parent_id・entry・location・failed を持つクラス |
| `利用上限の一般語の照合` | 定数 | plugins/ndf/scripts/lib/monitor_patterns.py の REASONED_FATAL | （N10）^(?:ERROR:\s*)?(?:quota exceeded／rate limit exceeded)\b（行頭の CLI の出力の形だけ） |
| `rechecks` | 設定値 | プランのステップの欄（plugins/ndf/scripts/supervise_lib/design_steps.py の requirements-check と plugins/ndf/scripts/supervise_lib/sprint_waves.py の requirements-recheck） | （N11）requirements-check → requirements-recheck |
| `review-unconverged-clean` | 設定値 | plugins/ndf/contracts/causes.json（SF1a の表へ行を足す） | （N11）未解決 0 件で上限か error。行き先は次の工程 |
| `IMPL_RULES の全体テストの行` | 定数 | `plugins/ndf/scripts/supervise_lib/templates.py` | （N11）全体テストを打たない（後段のステップが受け持つ） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| （N1）同じ done のキューの打ち直しと `--fresh` が同時に走りうるため、`--fresh` は耐久の記録の lock を掴めたときだけ退避する。参照の解決は読むだけで、プランのファイルを書き換えないため同時に流れるキューと取り合わない（版の取り合いは今の release_version の判定のまま） | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N2a）plan-edit どうしはプランのファイルを lib/locks.exclusive で直列にする。run と queue はプランを起動の時点で読むため、流れている間の書き換えは次の起動から効く。流れているプランを見分ける印は今のコードに無い（`grep -rn 'exclusive(' plugins/ndf/scripts/supervise_lib/` は 0 件）。pause の worker の起こし直しは 1 つのステップの中で直列に走る | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N2b）design-results はスプリントのプランを 1 回の起動で書き換えるため、queue がそのステージのプランを流す前に走る順序（manifest のステージの並び）に頼る。acceptance-check.py record は課題ごとの錠の中で本文を読み書きする（既存）ため、同じ課題を持つ 2 本のプランが並行しても上書きし合わない | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N3）耐久ワークフローの見回り・オーガナイザーの parallel.py stop/start・レーンの request が同じ台帳へ追記する。追記は既存の append_ledger_row の排他（locks.exclusive）だけで取り、start は既存の open_parallel_record の記録の排他で run を 1 本に保つ。 | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N4）Stop の hook と区間の tick はラッパーごとの作業ディレクトリのファイルを読み書きし、別のラッパーとは置き場が分かれる。リレーハブの restart は受付箱を 1 本ずつ処理する今の形のまま上限の判定と停止と起動を 1 回の処理の中で行う。共有の依存の環境は置き場の名前に python の版を含めることで、別の python での作り直しが走っているプロセスの環境と重ならない（同じ python どうしの uv sync は uv の置き場の排他に任せる）。 | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N5）提案・リファクタリング計画・判定は担当ごとに並列に走る。結果ファイルを担当ごとの読み取り用ディレクトリへ移すため、書く先は重ならない。作業ディレクトリ（work/）を書き換える手順は今どおり 1 つずつ走り、実行の持ち主（run_owner）が排他を取る | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N6）人や別のレーンが同じ PR のブランチへ push するのと、収束ループの修正の push が重なる。強制の push はせず、拒まれたら取り込んで 1 回だけ送り直す。別のコンテナや同じリポジトリの別のセッションの git worktree prune とレビュー用のworktreeが重なるのは、作った直後の lock で防ぐ（lock は git の管理情報のファイルで、別のマウント名前空間からも見える）。 | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N7）複数の worktree とセッションがメインディレクトリの同じ .ndf/backlog/ を読む。見直し・大整理は今の持ち主の宣言（owner.json の錠）で 1 つずつ走る。candidates.json・rejected.json・estimates.json は jsonio.write_atomic で書き、apply は locks.exclusive で状態のディレクトリを囲む | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N8a）SubagentStop の hook は別々のサブエージェントで同時に走る。同じ agent_id の差を書くときは帳簿のファイルの錠（locks.append_locked と同じ錠）の中で既存の行を読んでから書く。後片付けは並列のレーンから同時に打たれうるため、課題の本文の更新は progress-record.sh の側の 1 回の取得と更新に任せ、記録済みを見て打ち直さない。MVV の記録は追記だけ | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N8b）SubagentStop の hook は別々のサブエージェントで同時に走る。同じ agent_id の差を書くときは帳簿のファイルの錠（locks.append_locked と同じ錠）の中で既存の行を読んでから書く。後片付けは並列のレーンから同時に打たれうるため、課題の本文の更新は progress-record.sh の側の 1 回の取得と更新に任せ、記録済みを見て打ち直さない。MVV の記録は追記だけ | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N9）テストは pytest -n 4 と CI の 2 シャードで並列に走る。_forbid_real_state は各ワーカーの一時ディレクトリへ向けるため取り合わない。回収（reclaim）は今の錠（RECLAIM の lock）のまま | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N10）子の実行が親と同じ記録のファイルへ並行に書く。書き口のファイルの出力先が排他（fcntl.flock）の中で書きかけの行を閉じてから 1 行を書き切る（#2338 の設計の決定 1・6）。別のコンテナは自分の起点の識別子のファイルにだけ書くため、同じファイルを取り合わない。 | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N11）雛形はプランを組む時点の純粋な組み立てで、同時に走る処理と取り合うものは無い。#2140・#3108 の直しで verify がメインディレクトリへ書かなくなり、並べて動く別のスプリントのメインディレクトリの変更と取り合わなくなる | 子 Issue の触るファイルと状態の置き場 | 上の文のとおり | 書き込みは `jsonio.write_atomic` で置き換え、打ち直しても同じ中身になる |
| （N12）レーン・担当・CI の外の全体テスト | 作業マシンの枠の置き場（`NDF_TEST_SLOTS_DIR`） | 枠のファイルを 1 本ずつ排他で取る（#2711 の設計文書） | 枠の持ち主のプロセスが消えたら、次に取る側が枠を回収する |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| 子 Issue の手順のスクリプト | 止まった理由の種類（`cause`）と次のコマンド（`next`）を持つ 1 行の結果 JSON | 標準出力（`lib/step_result.py` の形） | 認証情報・トークン・API キー（N4 の中継の環境変数、N10 の監視の err.log の中身） |
| 子 Issue の PR | 解消する Issue の再現の手順を 1 回通した結果と `Closes` の行 | PR 本文 | 再現できなかった Issue の `Closes`（理由を書いて開いたまま残す） |
| N8b の使用量の帳簿 | 応答ごとの使用量の行 | 帳簿の JSONL | 応答の本文 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `resolve` | （N1）plugins/ndf/scripts/lib/release_version.py（次に空いている版・既出の判定）と plugins/ndf/scripts/supervise_lib/queue_fill.py の fill_release_version | grep -n '^def ' plugins/ndf/scripts/lib/release_version.py plugins/ndf/scripts/supervise_lib/queue_fill.py | 使う（版の規則は release_version.py のまま、queue_fill の解決の手順を version の種類へ移す） |
| `closing_prs` | （N1）plugins/ndf/scripts/sprint_close_lib/record_find.py（課題を閉じる PR の手順 2）と plugins/ndf/scripts/lib/closing.py | find plugins/ndf -name record_find.py; grep -rn closing_numbers plugins/ndf/scripts（#2663 の用語の定義が同じ手順を指す） | 使う（課題を閉じる PR の定義を 1 か所にする） |
| `match_plan` | （N1）plugins/ndf/scripts/supervise_lib/queue_fill.py の named | grep -n 'def named' plugins/ndf/scripts/supervise_lib/queue_fill.py | 使う（stem と末尾の一致はそのまま、絶対パスの一致を足して refs へ移す） |
| `design_pr_state` | （N1）plugins/ndf/scripts/supervise_lib/design_stage.py の merged_design_pr | grep -n 'def merged_design_pr' plugins/ndf/scripts/supervise_lib/design_stage.py | 使う（マージ済みの判定はそのまま。head のブランチだけを足す） |
| `retire（lib/durable.py）` | （N1）plugins/ndf/scripts/lib/durable.py の cancel_others・purge_old | grep -n '^def ' plugins/ndf/scripts/lib/durable.py | 使えない理由: cancel_others は途中の耐久ワークフローを止めるだけで完了の記録を残し、purge_old は日数で消すため、同じ並びを頭から流す用途に合わない。退避の関数を足す |
| `resume_until_result` | （N2a）WorkStep.execute の full の起こし直しのループ（worker_steps.py:209-213） | grep -n 'RESUMES' plugins/ndf/scripts/supervise_lib/worker_steps.py | 使う（ループを関数へ移し、full と pause で共有する） |
| `skills_for_pause` | （N2a）stage_skills.skills_for_step・render | grep -n '^def ' plugins/ndf/scripts/lib/stage_skills.py | 使う（名前の解決は skills_for_step と同じ表、本文の節は render） |
| `closing_commits` | （N2a）lib/closing.py の CLOSING（_KEYWORD が `:?` を許し `Fix: #N` に当たる） | grep -n '_KEYWORD\／CLOSING' plugins/ndf/scripts/lib/closing.py | 使う（照合の形を 2 か所に書かない） |
| `closing_commits` | （N2a）lib/proc.py の git | grep -n 'def git' plugins/ndf/scripts/lib/proc.py | 使う |
| `edit_plan` | （N2a）lib/jsonio.write_atomic・append_jsonl、supervise_lib/paths.state_dir_of | grep -n '^def ' plugins/ndf/scripts/lib/jsonio.py; grep -n 'def state_dir_of' plugins/ndf/scripts/supervise_lib/paths.py | 使う（書き込みは write_atomic、進捗ログは append_jsonl、置き場は state_dir_of） |
| `edit_plan` | （N2a）lib/locks.exclusive | grep -n '^def ' plugins/ndf/scripts/lib/locks.py | 使う（同じプランへの書き換えを直列にする） |
| `section_lack` | （N2b）sprint_inputs.body_lack と gh_sections.get_section | grep -n 'def body_lack' -A 8 plugins/ndf/scripts/supervise_lib/sprint_inputs.py | 使う（body_lack の中身を lib/acceptance.py へ移し、spec-copy.py からも読めるようにする。spec-copy.py は supervise_lib を読まない） |
| `acceptance_steps` | （N2b）acceptance-check.py record・show と lib/acceptance.read_check_kinds・criteria | grep -n '^def ' plugins/ndf/scripts/lib/acceptance.py; sed -n 1,20p plugins/ndf/scripts/acceptance-check.py | 使う（区分の表の読み書きは既存。全件の確かめだけを show に足す） |
| `cmd_design_results` | （N2b）DesignResults.unread と ReadFailed の止まり方 | grep -n 'unread' plugins/ndf/scripts/supervise_lib/design_stage.py | 使う（ReadFailed と同じくプランを書かずに止める形へ寄せる） |
| `_path_present` | （N2b）design_docs.git_list・design-match.py の judge_names の files・dirs | grep -n 'def judge_names' -A 6 plugins/ndf/scripts/design-match.py | 使う（一覧は judge_names が 1 回だけ取る） |
| `plan_repo` | （N2b）engine.py:177・commands.py:324・steps.py:77 の同じ組み立て | grep -n '/.worktrees/' plugins/ndf/scripts/supervise_lib/*.py | 使う（3 か所を置き換える） |
| `Engine.keep_cwd` | （N2b）lib/worktree_base.py の git-common-dir の読み取り | grep -n 'git-common-dir' plugins/ndf/scripts/lib/worktree_base.py | 使えない（git を起動する。`.git` のファイルの gitdir の行を読んで有無を見る方が軽い。採るかは実装で測る） |
| `overlap_of` | （N2b）admission.overlaps・groups | grep -n '^def ' plugins/ndf/scripts/supervise_lib/admission.py | 使う（overlaps と groups は overlap_of を呼ぶため、宣言の無いプランの扱いが伝わる） |
| `judge_lane` | （N3）ブランチ sprint/lcore-13 の plugins/ndf/scripts/lib/parallel_watch.py（ask_loop_stops・ask_stalls・ask_helper・withdraw_settled、214 行） | git show sprint/lcore-13:plugins/ndf/scripts/lib/parallel_watch.py ／ grep -n '^def ' | 使う。判定の条件とテスト（test_parallel_watch.py の 462 行）を移す。_Run を引数に取る形は純粋な関数の形へ直す |
| `judge_lane` | （N3）plugins/ndf/scripts/lib/parallel_launch.py の came_up・_moved_since | grep -n 'def came_up\／def _moved_since' plugins/ndf/scripts/lib/parallel_launch.py | 使う（今の起動より後に動いたかの判定） |
| `last_block` | （N3）plugins/ndf/scripts/relay_lib/switch.py の REASON_AUTH と log(event='account', reason=..., to=None) | grep -n 'REASON_AUTH\／event="account"' plugins/ndf/scripts/relay_lib/switch.py | 使う（書き手の行の形をそのまま読む） |
| `stop_named` | （N3）plugins/ndf/scripts/relay_lib/tmux.py の session_gone と relay_running | grep -n 'def session_gone\／def has_session' plugins/ndf/scripts/relay_lib/tmux.py | 使う |
| `plan_state` | （N3）plugins/ndf/scripts/lib/durable.py の retire_if と parallel_ledger.restore | grep -n 'retire_if\／def restore' plugins/ndf/scripts/lib/durable.py plugins/ndf/scripts/lib/parallel_ledger.py | 使う（失われた計画の作り直しと同じ経路に paused を載せる） |
| `lane_resume_point` | （N3）plugins/ndf/scripts/lib/parallel_lane.py の lane_plans・lane_waits | grep -n 'def lane_plans\／def lane_waits' plugins/ndf/scripts/lib/parallel_lane.py | 使えない（数と待ちだけを返し、途中のステップの位置を持たない）。読み方の部品は参考にする |
| `chained_ndf_wait` | （N4）plugins/ndf/scripts/hook_lib/shell_checks.py の starts_plan・_command_starts_plan・_skip_wrappers と lib/shparse.py | grep -n '^def ' plugins/ndf/scripts/hook_lib/shell_checks.py | 使う（構文木の歩き方と NDF のスクリプトの語の見分けをそのまま使う） |
| `Hub.restart` | （N4）plugins/ndf/scripts/relay_lib/hub_client.py の stop_named と hub.py の judge・launch | grep -n 'def stop_named\／def judge\／def launch' plugins/ndf/scripts/relay_lib/hub*.py | 使う（停止は stop_named、上限は judge に置き換える対象を外す引数を足す） |
| `choose` | （N4）plugins/ndf/scripts/relay_lib/pin.py の pinned・set_pinned・clear（ca.store_dir を使う） | cat plugins/ndf/scripts/relay_lib/pin.py | 使う（中身を lib/claude_accounts.py へ移す） |
| `python_request` | （N4）plugins/ndf/scripts/relay_lib/runtime.py の sync の --no-managed-python の打ち分け | sed -n 96,115p plugins/ndf/scripts/relay_lib/runtime.py | 使えない（uv sync の失敗で選び分けるため、走るたびに選ぶ python が変わりうる）。選び方の方針（システムの python を先に）だけを使う |
| `place_launcher` | （N4）plugins/ndf/scripts/relay_lib/version_dir.py の place_copy_to と install.py の _same | grep -n 'def place_copy_to\／def _same' plugins/ndf/scripts/relay_lib/*.py | 使う |
| `touched_symbols` | （N5）plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/codemetrics_read.py の構文木の読み（symbol・lineno・end_lineno を集める）と codemetrics.py の lizard の起動 | grep -n 'end_lineno\／lizard' plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/codemetrics*.py | 使う（関数の行の範囲がそろっている） |
| `touched_symbols` | （N5）plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope_check.py の commit_hunks・changed_names | grep -n 'def changed_names\／commit_hunks' plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope_check.py | ハンクの読みは使う。changed_names は識別子の集合で、関数の範囲を持たないため使えない |
| `locations_of` | （N5）plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py の test_files_under | grep -n 'def test_files_under' plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py | 使う |
| `collected` | （N5）plugins/ndf/scripts/lib/test_triage.py の read_junit と、cross-refactoring の scope_verdict | grep -rn 'read_junit' plugins/ndf/scripts/lib plugins/ndf/skills/cross-refactoring/scripts | 使えない。junit が書かれるのは全体テストの時点で、add-tests の確かめに間に合わない |
| `tests_for_change` | （N5）scripts/lib/py_imports.py（imported_files） | grep -n '^def ' scripts/lib/py_imports.py | 使えない。リポジトリの検査用でリリース対象に入らず、NDF のスクリプトの根を前提にする。同じ考え方（ast の Import と ImportFrom を読む）を cross-refactoring の側に置く |
| `tests_for_change` | （N5）plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py の _matches_by_name（名前の印でテストを見分ける） | grep -n '_matches_by_name' plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py | Python 以外の言語の名前の対応（test_<語幹> など）に使う |
| `probe_budget` | （N5）plugins/ndf/scripts/lib/limits.py の limits.seconds | grep -n 'def seconds' plugins/ndf/scripts/lib/limits.py | 使う |
| `launch-cli.sh の configure_phase` | （N5）plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh の ignore_dir | grep -n 'ignore_dir' plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh | 使う（読み取り用ディレクトリの .cross_refactoring/ を差分から外す） |
| `resolve_target_repo` | （N6）review_lib/github.py の _repo_from_resume・_repo_from_git と plugins/ndf/scripts/lib/repo.py の owner_repo_from_url | grep -n 'def _repo_from' plugins/ndf/skills/cross-review/scripts/review_lib/github.py | 使う（順を 1 つの関数にまとめ、_repo_from_gh の gh repo view への落ちを外す） |
| `resolve_target_repo` | （N6）plugins/ndf/scripts/lib/proc.py の git_root（#1655 の設計が足す検査） | grep -n 'def git_root' plugins/ndf/scripts/lib/proc.py | 使う（cwd が git リポジトリかの判定） |
| `lock_review_worktree` | （N6）git worktree lock（prune が lock した登録を消さないことを一時のリポジトリで確かめた: lock した wt2 は prune の後も list に残り、lock しない wt1 は消えた。lock したworktreeは remove・remove --force で拒まれ、-f -f で外れる） | git worktree add → lock → worktreeを mv → git worktree prune → git worktree list | 使う |
| `push_fix` | （N6）lib/result_posts.py の _git と formatting.format_before_push | grep -n 'def _git\／format_before_push' plugins/ndf/scripts/lib/result_posts.py | 使う |
| `_require_fix_for_changes` | （N6）review_lib/commands/merge_fix.py の ingest_fix | grep -n 'def ingest_fix' plugins/ndf/skills/cross-review/scripts/review_lib/commands/merge_fix.py | 使う（呼び出し元が残った結果ファイルを渡す） |
| `state_dir` | （N7）plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py の backlog_dir（メインディレクトリの .ndf/backlog/。repo_lib.main_dir で解く） | grep -n 'def backlog_dir' plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py | 使う |
| `state_dir` | （N7）plugins/ndf/scripts/lib/sprint_loop_record.py の _upkeep_state_dir（同じ置き場の規則の写し） | grep -rn 'NDF_UPKEEP_STATE_DIR' plugins/ndf --include=*.py | 使えない（写しのため）。upkeep と同じ置き場を読む形へ直す |
| `_candidate_result` | （N7）plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py の unscored と routes.by_num | grep -n 'def unscored' plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_cmd.py | unscored は使う（見直しの採点の欠けと同じ基準にそろえる） |
| `depth_limits` | （N7）plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py の settings | grep -n 'def settings' plugins/ndf/skills/backlog-refinement/scripts/upkeep_depth.py | 使う（depths の重ね合わせ） |
| `_slots` | （N7）plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py の _over_and_dependents（境界から除く集合を作る） | grep -n '_over_and_dependents' plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py | 使う（判断待ちの番号を同じ集合へ足す） |
| `record_cleanup_stage` | （N8a）plugins/ndf/scripts/lib/parallel_release.py の closes_by_pr（PR → 閉じる課題。closingIssuesReferences を読む） | grep -n 'def closes_by_pr' plugins/ndf/scripts/lib/parallel_release.py | 使う |
| `record_cleanup_stage` | （N8a）plugins/ndf/scripts/supervise_lib/state.py の record_stage（progress-record.sh を課題ごとに打つ） | grep -n 'def record_stage' plugins/ndf/scripts/supervise_lib/state.py | 使えない（プランの記録の経路に結びついている）。打ち方（bash progress-record.sh <課題> stage 後片付け）はそろえる |
| `check_body` | （N8a）plugins/ndf/scripts/lib/handoff_doc.py の level_headings・find_section | grep -n '^def ' plugins/ndf/scripts/lib/handoff_doc.py | 使う |
| `render_files` | （N8a）scripts/check-doc-line-limit.py の LIMIT | grep -n 'LIMIT' scripts/check-doc-line-limit.py | 使えない（リリース対象の外）。上限は宣言（.ndf/glossary.json）に持たせる |
| `mvv_repo_key` | （N8b）plugins/ndf/scripts/lib/decl.py の main_dir（lib/repo.py が main_dir として再び公開） | grep -rn '^def main_dir\／main_dir = ' plugins/ndf/scripts/lib | 使う |
| `transcript_usage` | （N8b）scripts/token-usage.py の scan_file（message.id で重複を除き最後の usage を取る） | grep -n 'def scan_file' scripts/token-usage.py | 使えない（リポジトリの検査用でリリース対象に入らない）。数え方をそろえ、一致をテストで確かめる |
| `_forbid_real_state` | （N9）plugins/ndf/scripts/tests/conftest.py の _isolated_mvv_state ほかの _isolated_* の fixture | grep -n '_isolated' plugins/ndf/scripts/tests/conftest.py conftest.py | 使う（今の差し替えはそのまま、本物の根への書き込みを落とす確かめを足す） |
| `_reclaim_owner_of` | （N9）plugins/ndf/scripts/lib/worktree-testenv-sweep.sh の _reclaim_rows・_reclaim_limited | grep -n '^_reclaim' plugins/ndf/scripts/lib/worktree-testenv-sweep.sh | 使う（読み込みは今の関数のまま、判断だけを分ける） |
| `same_cmd` | （N9）標準ライブラリの shlex.split | python3 -c 'import shlex' | 使う |
| `extract_template` | （N9）plugins/ndf/scripts/tests/waiting_templates.py の雛形を取り出すヘルパー（上限の差し替え） | ls plugins/ndf/scripts/tests/waiting_templates.py | 使う（間隔の差し替えを足す） |
| `open_run` | （N10）plugins/ndf/scripts/lib/state_home.py（#2766 の利用者の根）と lib/secret_redact.py | ls plugins/ndf/scripts/lib/state_home.py plugins/ndf/scripts/lib/secret_redact.py | 使う（置き場の根と秘密を伏せる処理） |
| `open_run` | （N10）Python 標準の logging.Handler（#2338 の設計の外部の調査で採用） | issues/issue-2338-design-research.md の採否の表 | 使う。structlog・loguru・concurrent-log-handler は設計の決定 1 で採らない（標準ライブラリだけで書き、依存の欠けで止めたことも記録へ残すため） |
| `early_warnings` | （N10）plugins/ndf/scripts/lib/monitor_scan.py の _scan_early_warn と _claude_denials_warning | grep -n 'def _scan_early_warn\／def _claude_denials_warning' plugins/ndf/scripts/lib/monitor_scan.py | 使う（2 つを種類の鍵で束ねる） |
| `wf_merge_target` | （N10）workflow-merge.sh の _wf_scan_gh_verb（-R / --repo の値を飛ばす処理を既に持つ） | sed -n 20,40p plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh | 使う（飛ばしている値を捨てずに slug として拾う） |
| `run_runtime（kiro の枝）` | （N10）plugins/ndf/scripts/lib/tool_paths.py の skip-worktree の印（hide） | grep -n 'def hide' plugins/ndf/scripts/lib/tool_paths.py | 使えない（印は index へ入るのを防ぐだけで、worktreeのファイルの書き換えと .bak の追加は防がない） |
| `fix_worktree` | （N11）plugins/ndf/scripts/lib/decl.py の main_dir | grep -n 'def main_dir\／_resolve_main_dir' plugins/ndf/scripts/lib/decl.py | 使う（core.worktree を読むためサブモジュールでもworktreeを返す） |
| `behavior_since` | （N11）plugins/ndf/scripts/lib/diff_kinds.py の classify_files と behavior-check.py の behavior_targets | grep -n '^def ' plugins/ndf/scripts/lib/diff_kinds.py plugins/ndf/scripts/behavior-check.py | 使う（振る舞いを決める文書の判定はそのまま、差分の範囲だけを記録の head からにする） |
| `_inspect_steps` | （N11）plugins/ndf/scripts/supervise_lib/decl.py の sync_checks_of | grep -rn 'sync_checks_of' plugins/ndf/scripts/supervise_lib | 使う（同期のコマンドを宣言から読む） |
| `release_verify_cmd` | （N11）plugins/ndf/scripts/release-verification-steps.py の verify-install の --root | #2140 の本文（--root を渡して 3 ランタイムで通った）。grep -n -- '--root' plugins/ndf/scripts/release-verification-steps.py で確かめる | 使う |
| `queue_prs_of` | （N1）同じ名前の関数 | `git grep -n 'def queue_prs_of' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `list_edits` | （N2a）同じ名前の関数 | `git grep -n 'def list_edits' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `plan_questions` | （N3）同じ名前の関数 | `git grep -n 'def plan_questions' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `_Run.apply_liveness` | （N3）同じ名前の関数 | `git grep -n 'def apply_liveness' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `sync_worktree` | （N6）同じ名前の関数 | `git grep -n 'def sync_worktree' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `state_pr` | （N6）同じ名前の関数 | `git grep -n 'def state_pr' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `diag` | （N10）同じ名前の関数 | `git grep -n 'def diag' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `clip` | （N10）同じ名前の関数 | `git grep -n 'def clip' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |
| `recheck_of` | （N11）同じ名前の関数 | `git grep -n 'def recheck_of' -- 'plugins/*.py' 'scripts/*.py'`（2026-11-13） | 無い。新設する |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `resolve` | （N1）版・記録の PR・前のステージの PR を持つプランで、すべての参照が値に解け、出所が残る。解決の前と後でプランのファイルの sha256 が同じ | 参照を持たないプランは読み手を 1 度も呼ばない | 記録の PR が無い・版を読めない・前のプランの結果が無い、のそれぞれで unresolved に種類と理由が入り values に入らない |
| `queue_prs_of` | （N1）前のステージの PR #140・#143 と閉じる PR #143 で [140, 143] | 和が空で実行条件を持つプランは空の並び | 閉じる PR を読めないときは unreadable、和が空なら empty と区別される |
| `match_plan` | （N1）ファイル名・末尾の一致（impl-2044）・一覧のフルパス・同じファイルの相対パスで同じプランに当たる | `4-check.json`（拡張子つき）は解いたパスで当たる | どれにも当たらなければ空で、呼び出し元が「名前かフルパスで渡す」を出す |
| `resume_targets` | （N1）完了したプランは流さない。止まったプランは終えたステップから | キーの並びだけを変えたプランは同じハッシュで流さない | 完了の後に中身が変わり --from が無ければ人へ戻す。--from があれば指定のステップから流す |
| `findings_of` | （N1）1 回目の review が rounds 4・fixed 8 で終わり、--from base-sync で流し直すと行の rounds 4・fixed 8 | 同じステップが 2 回の実行で件数を持つと最後の実行の値。頭から流し直した実行はそれより前を読まない | 進捗ログが無い・壊れた行を含むときも落ちず、読めない項目は null |
| `cmd_hold_question` | （N1）リリース待ちのループの 1 回の締め直しの途中で受け付け、質問待ちになり release の答えと版が残る | 進行中と締め直しの途中のループの 1 回が同時にあるとき、--plan の属するループの 1 回へ当たる | どのループの 1 回にも当たらなければ記録を変えずに stopped |
| `resume_until_result` | （N2a）1 回目に結果ファイルが無く 2 回目に書けば 2 回目の結果を返す | RESUMES 回目で書けば返し、RESUMES+1 回は起動しない | 会話の id が無ければ起こし直さずに work_stopped |
| `skills_for_pause` | （N2a）fix は ["fix"] | 表に無い種類（newtext）は空 | pauses の値に一覧に無い名前があれば load_table が SkillsError |
| `closing_commits` | （N2a）件名 `Fix: #12 …` と本文 `Closes #3` のコミットを返す | `Fix: … （#12）` と `fixture #12` は当たらない | base_ref が無ければ例外 |
| `PrStep._push` | （N2a）当たりが無ければ push する | 当たりがあれば git push を呼ばない | 結果の text に sha と件名が載り、cause が refused |
| `edit_plan` | （N2a）控え・プラン・進捗ログの 1 行がそろう | 差分が無ければ書き換えず記録もしない | 理由が空・JSON が読めない・ステップの検査に落ちるなら invalid、錠を取れなければ refused で、どれも何も書かない |
| `list_edits` | （N2a）2 本のプランの 3 件を日時の順に返す | 書き換えが無ければ空 | 壊れた行は読み飛ばさず invalid で止める |
| `overlap_of` | （N2b）片方が None なら重なりの組になる | 両方が空の並び（何も触らない）なら重ならない | 読めないプランは touched_files で invalid |
| `section_lack` | （N2b）節があり 1 行あれば None | `### 受け入れ条件` の入れ子だけなら「節が無い」、節が空行だけなら「節が空」 | body が空文字なら「節が無い」 |
| `cmd_check` | （N2b）写しが一致し節がそろえば 0 | 退行しない条件の隣に `前提の確認:` があれば 0、無ければ 1 | 本文を読めなければ 2 |
| `acceptance_steps` | （N2b）plan_to_merge の並びに区分の work と run が入り、run の on_fail が work へ戻る | 取り込んだ課題を含む 2 課題で、show --complete を課題ごとに打つ | 区分の欠けで run が落ちる |
| `cmd_design_results` | （N2b）全課題が読めればプランを書いて ok | unread が 1 件でもあれば stopped でプランが変わらない | PR の単位が 2 つの課題で stopped |
| `_path_present` | （N2b）`routes/factory.test.ts` が `factory/api/src/routes/factory.test.ts` で present | `a/bc.py` は `x/ab/bc.py` に当たらない（要素の単位の末尾の一致） | 末尾の一致が 2 件なら ambiguous |
| `plan_repo` | （N2b）`リポジトリ` があればそれ | `作業場所` だけで /.worktrees/ を含めばその前 | どちらからも決まらなければ None |
| `Engine.keep_cwd` | （N2b）作業場所が消えていれば根へ移る | ディレクトリが残り `.git` の gitdir が無ければ根へ移る（#3022 の形） | 根も無ければ cwd を変えず進捗ログに 1 行 |
| `judge_lane` | （N3）一覧が running で開発ループの記録が mvv_changed で止まった行は loop_stopped を返す | 最後に動いた時刻がちょうど区切りの 60 分のとき stalled、59 分で alive。今の起動の指示より前の stopped の行は unknown | row が None（一覧に無い）・時刻を持たない行は ended にせず unknown を返す |
| `plan_questions` | （N3）stalled のレーンに stall の問いが 1 件、loop_stopped に区切りを待たずに 1 件立つ | 問いの後に一覧の時刻が問いの時刻より進むと withdraw に入る。待つと答えたレーンは次の区切りまで問わない | auth_blocked でアカウントが使えないときは relaunch に入れず stall の問いにする |
| `stop_named` | （N3）セッションとプロセスが消えると stopped が真 | 待ちの秒数の最後の 0.1 秒で消えた場合も真 | kill-session の後もセッションが残る偽の tmux で stopped が偽と reason |
| `last_block` | （N3）start の後の account・auth・to null の行を返す | その後に新しい start の行があれば None | 記録の行が壊れていれば BlockReadError |
| `plan_state` | （N3）SUCCESS で台帳の最後が paused なら paused | paused の後に closed の行があれば closed | 台帳が読めなければ OSError を呼び出し元へ（fresh と読まない） |
| `cmd_stop` | （N3）2 本のレーンの計画で、区切りで止めた後に start を打つと同じ計画名で run が動き、リリースの連番が前の続きになる | リリースの途中で止めた計画の再開で releases/<n> の置き場を上書きしない | 台帳に書けない置き場で結果 stopped と cause、耐久ワークフローへ stop が届かない |
| `hold_once` | （N4）背景の作業が残る ndf-next の応答で、stop_hook_active が真でも 1 回目は真 | 同じ区間・同じ候補の 2 回目は偽。区間か候補が変われば真 | held.json に書けなければ OSError を呼び出し元へ |
| `prose_wait_reason` | （N4）このセッションが本文で承認を待って終わった応答は hold が真 | 別のセッションの画面で答えるよう案内する応答・承認という語を含む報告・背景の処理が動いている応答は hold が偽で materials に理由 | wait-held.json が壊れていれば hold が偽で materials に読めなかったファイル |
| `Relay._tick` | （N4）シグナルファイルの後にアイドルの秒数が過ぎ、goal_status が未達のままなら switch が真 | シグナルファイルを Stop のたびに書き直しても、first_written_at より後の goal_status で未達と読む（#2438 の 7 回のループの 1 回を記録で再現） | written_at を持たないシグナルファイルは halt で止める |
| `Hub.restart` | （N4）上限 3 で 3 本が動いているとき、そのうち 1 本の restart が通る | 動いていない 1 本の restart は今どおり上限に数える | 上限で断るとき対象は止まらず動いたまま |
| `place_launcher` | （N4）空の bin_dir に ndf-relay を置き、任意のディレクトリから ndf-relay ls が動く | 同じ中身があれば placed が偽。bin_dir が PATH に無ければ hint に 1 行 | 別の中身の ndf-relay があれば上書きしない |
| `choose` | （N4）指定のアカウントが使えればそれを選び、理由が pinned | 指定が除外に入るか上限に達していれば残りの量で選び、理由に指定が使えないことが載る | pinned.json が壊れていれば PinReadError |
| `python_request` | （N4）requires-python を満たすシステムの python があれば request がその python | システムの python が 3.9 なら uv 管理の python。置き場の名前に py3.13 か py3.14 が入る | どちらも無ければ EXIT_DEPS_MISSING |
| `chained_ndf_wait` | （N4）merged-steps.py merge-when-green 1 && supervise.py queue p.json を見つける | sleep 20; merged-steps.py merge-when-green 1 を見つける。until [ -s f ]; do sleep 5; done と単独の supervise.py wait は None | 読めない構文は ParseFailed |
| `collected` | （N5）test_a.py と補助の helpers.py を渡すと、collected に test_a.py、uncollected に helpers.py が入る | collect_command の無い suite のファイルは undecided に入る | テンプレートが起動できないと CollectError |
| `_run_added_tests` | （N5）PR #612 の R1-001 の形（tests/doc_staleness_helpers.py へ足したテスト）で項目が not_collected で差し戻される | CI テストの宣言のあるリポジトリでは今どおり走らせない | 収集の確かめが失敗すると止まり、causes.json の種類を返す |
| `touched_symbols` | （N5）関数 f の本文の 1 行を変えた差分で {path: {f}} を返す | モジュールの頭の import だけを変えたファイルは空の集合 | 構文の誤りのファイルで止まり、ファイル単位へ倒さない |
| `build_candidates` | （N5）同じファイルの別の関数 g への提案が out_of_scope で見送られる（devbasex/devbase#191 の cmd_scale の形） | クラスのメソッド（Class.method）の名前の一致 | symbols が空の dict なら提案はすべて out_of_scope |
| `locations_of` | （N5）tests/ の下の test_x.py・test_y.py を返し、python3 -m unittest {paths} が通る | 置き場所にテストのファイルが無いと空 | 無し |
| `probe_budget` | （N5）timeout 540・所要 100 で 440 | timeout 540・所要 533.9 で floor を返す | 無し |
| `_verify_final_fix_commits` | （N5）範囲の中だけを変えた直しは取り込まれる | 範囲の外の新しいファイルを足した直し（扱いは notes の判断） | 範囲外の frontend/vite.config.ts を変えた直しは取り込まれず、記録に outside_failures が残る |
| `tests_for_change` | （N5）lib/foo.py を触る項目で、import foo を持つ tests/test_foo.py だけを選ぶ | どのテストも import しないソースは unmapped | ディレクトリの対象（plugins/ndf/scripts/tests）は選ばない |
| `resolve_target_repo` | （N6）対象のリポジトリの中の cwd で origin から slug が決まる | --repo があれば origin より先、状態ファイルの repo があれば origin より先 | Skill のディレクトリ（git リポジトリでない）で --repo も状態ファイルも無ければ RepoUnresolved |
| `push_fix` | （N6）拒まれずに送れる | 1 回拒まれ、取り込みが衝突せずに送り直せる（一時の bare リポジトリへ別の clone から先に push しておく） | 同じ行を変えた push の後は diverged で、worktreeは取り込みの前の HEAD |
| `sync_worktree` | （N6）origin が 5 コミット進んだ後、自分の未送信のコミット 1 つを載せ直す | 未送信のコミットが無ければ origin の先頭に合わせるだけ | 衝突したら前の HEAD に戻り conflicts にファイル |
| `lock_review_worktree` | （N6）作ったworktreeに locked が付く | worktreeのディレクトリを見えなくして prune しても登録が残る | lock に失敗したらworktreeを外して止める |
| `remove_worktree` | （N6）NDF の理由の lock を外してからworktreeを外す | lock していないworktreeは今どおり | 利用者の理由の lock は外さず理由を返す |
| `_resolve_fix_inputs` | （N6）管理情報のあるworktreeで入力が決まる | --repo を渡しても管理情報が無ければ止まる | .git が指す先の無いworktreeで worktree_unregistered |
| `state_pr` | （N6）巻き直しの後の番号 2769 から元の 2767 の状態ファイルを引く | 巻き直していない PR はそのままの番号 | 状態ファイルが壊れていれば例外を呼び出し元へ（今の except: pass で既定の置き場へ倒す形をやめる） |
| `_require_fix_for_changes` | （N6）記録があれば ok | 記録が無く結果ファイルが残れば ingest | 記録も結果ファイルも無ければ stop と終了コード 5 |
| `RankPlan.candidate` | （N7）#1595 の最小の形（#23 の group に 21・33・36）で、4 件がそれぞれ本人の move_digest で rejected.json に残る | 本人の候補と依存先の両方に現れる番号は本人の候補 | 依存先にしか無い番号の却下で apply が終了コード 2 |
| `_stale_rejections` | （N7）後ろ倒しの候補を組んだ回に合わない却下は古いとして返す | 前倒しを全部退け、後ろ倒しを組まなかった回は後ろ倒しの却下を返さない | 無し |
| `state_dir` | （N7）worktree から打ってもメインディレクトリの .ndf/backlog/state/<repo> を返す | NDF_UPKEEP_STATE_DIR があればそれを使う | git の外で止まる |
| `_candidate_result` | （N7）--add の 33 件と diff-path の 38 件で --limit 45 のとき、--add の 33 件がすべて items に残る（#2064 の表） | 前回の持ち越しが carried で次の回の候補に戻る | 前回の記録が壊れていれば止まる |
| `_slots` | （N7）順位 1〜4 が判断待ちで容量の宣言があるとき、境界が 5 位以降から取られる | 全行が判断待ちなら境界は empty | 無し |
| `depth_limits` | （N7）宣言の depths.review.seconds 900 で 900 | 宣言が無ければ既定 | 形の違う宣言で終了コード 2 |
| `estimates` | （N7）見直しの回に要約値の違う記録の課題が excluded に入る | 差分の棚卸しでは記録と前回の表から採る（今の振る舞い） | 無し |
| `render_files` | （N8a）コンテキスト 2 つの用語集で docs/glossary/<id>.md が 2 本と索引が出る | 語の無いコンテキストのファイル | 上限を超えるコンテキストで止まる |
| `plan_glossary` | （N8a）03 と 05 の 2 ファイルを渡すと両方がコミットに入り、語ごとに定義のあるファイルが source になる | --spec が 1 つなら今どおり | どのファイルにも語が無ければ終了コード 2 |
| `check_body` | （N8a）雛形どおりで上限の内なら通る | 行数は上限の内でバイト数だけ超える | 書いたパス（例: sv/sprint-m1743/7-release-dev3.json）が無いと missing_paths に載る |
| `trim` | （N8a）雛形に無い節（「セッション 95 の記録」など）が退避先へ移る | 移す節が無ければ本体を書き換えない | 退避先を書けなければ本体を変えない |
| `record_cleanup_stage` | （N8a）Closes #N の PR の後片付けで #N の「後片付け」がチェックされる | まとめのプランで記録済みの課題は打ち直さない | gh が失敗すると partial |
| `transcript_usage` | （N8b）同じ id の 2 行で output_tokens が 1 → 50 なら 50 | JST の日付の境界をまたぐ会話で期間内の応答だけを数える | 読めないと unknown |
| `ledger_dir` | （N8b）CLAUDE_PLUGIN_DATA があっても状態のホームの usage を返す | NDF_USAGE_DIR が最優先 | HOME が無ければ例外 |
| `mvv_repo_key` | （N8b）worktree から呼ぶとメインディレクトリのパス | メインディレクトリから呼ぶと同じ値 | git の外で例外 |
| `_reclaim_location_seen` | （N9）親が残りメインディレクトリと同じファイルシステムなら 0 | 親が無く祖父母が同じファイルシステムなら 0（rf<番号>/work の形） | 祖先が別のファイルシステム（/tmp）なら 1 で unseen のまま |
| `_reclaim_owner_of` | （N9）作業ディレクトリが行の worktree の下なら own | 値の空のコンテナの行は none | 別の複製の worktree なら foreign |
| `_forbid_real_state` | （N9）一時ディレクトリへの書き込みは通る | 本物の根を読むだけのテストは通る | 本物の根の下へ書くと落ちパスが出る |
| `same_cmd` | （N9）ASCII のパスで今の期待値と同じ結果 | 全角を含むパスで引用されたコマンドも一致 | 語が 1 つ違えば一致しない |
| `_collected_ids` | （N9）2 つのシャードの和が全体と一致し重ならない | 収集の子プロセスは 1 回 | 収集が失敗すると落ちる |
| `open_run` | （N10）既定の設定で利用者の根の下の runs/<リポジトリ>/<UTC の日付>/<起点の識別子>.jsonl に start の行が書かれる | NDF_RUN_ID を受けた子の行の parent_id が親の run_id。31 日前の日付のディレクトリが消え、30 日前は残る | 書けない置き場で標準エラーに理由が出て結果が stopped |
| `diag` | （N10）標準エラーと記録の両方に同じ文が出る | short を渡すと標準エラーは short、記録は全文と display | 秘密を入れた環境変数の値を text に入れても記録に値が出ない |
| `clip` | （N10）limit 字以下はそのまま返し、記録に何も書かない | limit + 1 字で記録に全文が残り、先頭 limit 字が返る | 記録が開いていなければ切るだけ |
| `early_warnings` | （N10）標準エラーの記録の警告と拒否の件数の両方を返す | 片方だけのとき鍵は 1 つ | ファイルが無ければ空の辞書 |
| `monitor_agent` | （N10）標準エラーの警告を出した後に permission_denials が非致命で見つかると、拒否の件数の警告が 1 回出る | 同じ種類はループの 1 回ごとに繰り返さない | 記録が開いていないときも標準エラーへは同じ文が出る |
| `wf_merge_target` | （N10）gh pr merge 527 --repo volareinc/carmo-cdk --admin で「volareinc/carmo-cdk 527」 | -R=<slug>・-R<slug>・gh api -X PUT repos/<所有者>/<名前>/pulls/527/merge・&& でつないだ 2 件 | 対象の宣言を読めなければマージを止める |
| `run_runtime（kiro の枝）` | （N10）偽の kiro-cli が .amazonq/cli-agents/x.json を書き換えて x.json.bak を残しても、起動の後に x.json が元に戻り .bak が無い | 起動の前からある利用者の .bak は消さない | worktreeでないディレクトリでは写さずに起動し、記録に restore_skipped を残す |
| `_machine_body` | （N11）requirements-check が分岐の 2、requirements-recheck が 0 のとき、表は recheck の 0 で載る | 再確認のステップが無い分岐の非 0 は「分岐した」として載り失敗と書かれない | 再確認も非 0 なら失敗として全文で載る |
| `_inspect_steps` | （N11）sync_checks に同期のコマンドがあると refactor の cmd に --sync-command が付く | sync_checks が無いと付かない | review が未解決 1 件で上限なら人へ戻す行き先になる |
| `fix_worktree` | （N11）通常のリポジトリと linked worktree で今と同じ場所 | サブモジュールの直下で <サブモジュール>/.worktrees/<ブランチ> | git でないディレクトリで DeclError |
| `behavior_since` | （N11）記録の後に振る舞いを決める文書が変わると changed | 取り込みのマージのコミットだけなら same で claude -p を起動しない | 記録の head を読めないと unreadable |
| `extract_names` | （N11）設計が書いたテストの名前を集める | 「採らなかった案」の節の名前と改名の前の名前は入らない | 節の見出しが無い文書でも落ちずに全体から集める |
| （N12）`test_slots`・`test_guard` | #2711 の設計文書の「テスト設計」の行 | 枠の上限ちょうど | メモリの下限を割ったときに待たせるか止める |
