# 全体テストの台帳（#2309・#2204）

削る基準と速くする手段は `docs/plugin-development-guide.md` の「テストを削る基準」にある。基準の番号はその表の番号である。計測は実験版の `plugins/ndf/scripts/experimental/whole-test-timing.py`（`measure`・`diff`・`startup`）で取った。実測の値の持ち主は `issues/issue-2309-timing/` の記録である。この台帳の秒は記録から組んだ写しで、食い違ったら記録を正とする。

## 実測の記録

計測のマシンはこの開発コンテナ（CPU 8 個・メモリ 30 GB）、並列の数は宣言のまま（pytest の `-n 4`）。宣言のコマンド（`.ndf/project.json`）は変えていない。

| 計測 | 記録 | 結果の 1 行 |
| --- | --- | --- |
| 着手前 | `issues/issue-2309-timing/before.json.gz` | `issues/issue-2309-timing/before.result.json` |
| 着手後 1 | `issues/issue-2309-timing/after-1.json.gz` | `issues/issue-2309-timing/after-1.result.json` |
| 着手後 2 | `issues/issue-2309-timing/after-2.json.gz` | `issues/issue-2309-timing/after-2.result.json` |
| 着手後 3 | `issues/issue-2309-timing/after-3.json.gz` | `issues/issue-2309-timing/after-3.result.json` |
| 着手後 4 | `issues/issue-2309-timing/after-4.json.gz` | `issues/issue-2309-timing/after-4.result.json` |
| 起動の秒（着手前・着手後） | `issues/issue-2309-timing/startup-before.json`・`startup-after.json` | 同じファイル |
| 着手前後の照合（`diff`） | `issues/issue-2309-timing/diff.json` | 同じファイル |

- 着手前は、計測のスクリプトに deselect の記録を足したコミット（`fcbf56d92`）の複製で測った。`measure` が書いた JSON を、後で足した `write_timing_record` で gzip へ縮めて置いた（中身は変えていない）
- 計測の間、同じマシンでほかのスプリントのテストが並んで動いていた。開始時の load average は 8 以下でも、途中の負荷は 10〜13 まで上がった。秒には負荷の差が入っている

## 着手前後の実測

| 計測 | 日時（JST） | 開始時の load average | 判定に使うか | 壁時計 | suite ごとの秒（1 秒以上） | 件数（skip・deselect） | 各テストの所要の合計 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 着手前 | 2026-11-10 14:15 | 4.01 | 使う | 1129.0 秒 | pytest 1064.8（2 件が落ちた）・script-structure 18.7・runtime-plugin-validate 14.3・ndf-contracts 12.3・markdown-link-check 8.5・lint 7.1・repo-secrets-check 1.6 | 12994（4・0） | 4048.6 秒 |
| 着手後 1 | 2026-11-10 14:37 | 9.93 | 参考（8 を超えた） | 1095.4 秒 | pytest 1049.8（すべて通った）・script-structure 13.0（落ちた。下の注）・runtime-plugin-validate 10.6・ndf-contracts 8.2・markdown-link-check 5.9・lint 5.4・repo-secrets-check 1.1 | 12988（4・0） | 3807.5 秒 |
| 着手後 2 | 2026-11-10 17:49 | 2.25 | 使う | 806.4 秒 | pytest 760.2・script-structure 12.8・ndf-contracts 8.4・lint 5.1・repo-secrets-check 1.9・runtime-plugin-validate 10.8・markdown-link-check 5.9（すべて通った） | 12917（skip 4・deselect 0） | 2809.7 秒 |
| 着手後 3 | 2026-11-10 18:03 | 3.16 | 使う | 918.8 秒 | pytest 859.8・script-structure 15.7・ndf-contracts 9.5・lint 8.6・repo-secrets-check 1.6・runtime-plugin-validate 14.6・markdown-link-check 7.0（すべて通った） | 12917（skip 4・deselect 0） | 3215.5 秒 |
| 着手後 4 | 2026-11-10 18:19 | 7.46 | 使う | 1014.8 秒 | pytest 965.1・script-structure 14.6・ndf-contracts 9.1・lint 5.4・repo-secrets-check 1.3・runtime-plugin-validate 11.6・markdown-link-check 6.2（すべて通った） | 12917（skip 4・deselect 0） | 3677.0 秒 |

- 180 秒に届いていない。着手後の壁時計は 1095.4 秒で、差は約 915 秒である。各テストの所要の合計は 241.1 秒（6 %）縮んだ。設計の見込み（手段 1〜5 で 420〜900 秒）より小さいのは、手段 4（プロセスの中への置き換え）を入れず、手段 5 が 7 件に留まったため
- 受け入れ条件 1 の 3 回の計測は、着手後 2〜4 で取った（開始時の load average 2.25・3.16・7.46、すべて 8 以下）。3 回とも全 suite が通り、壁時計は 806.4・918.8・1014.8 秒で、180 秒との差は 626〜835 秒である。着手後 1（9.93）は参考の値として残す。途中の負荷はほかのスプリントのテストで上がるため、3 回の差には負荷の差が入っている
- 設計の決定 5（手段 4: 子プロセスを起こすテストをプロセスの中の呼び出しへ置き換える）は入れていない。設計の見込み（決定 1）でも 180 秒に届かず、残りは 1 スプリントに収まらないと設計が書いているため、設計と違う点として PR に書く
- 着手前の pytest で落ちた 2 件（`plugins/ndf/skills/cross-review/tests/test_state_offline_fetch.py::test_the_repository_is_resolved_from_git`・`plugins/ndf/scripts/tests/test_supervise_slow.py::test_limit_wait_is_not_counted_as_slow`）は、着手後には通った。負荷で時間の判定がずれる種類のテストである
- 着手後 1 の `script-structure` は、計測のスクリプトに足した `write_record`・`read_record` がほかのスクリプトの同名の関数と重なって落ちた。名前を `write_timing_record`・`read_timing_record` へ変えて通した（計測の後のコミット）
- 照合（`whole-test-timing.py diff`）: 台帳に無い消えたテスト 0 件・skip に変わったテスト 0 件・deselect に変わったテスト 0 件・定義が残る消したテスト 0 件（`issues/issue-2309-timing/diff.json`）

## 起動の秒

`whole-test-timing.py startup` で `--help` を 10 回ずつ流した中央値（`issues/issue-2309-timing/startup-before.json`・`startup-after.json`）。着手前は着手前の木の複製、着手後はこの作業場所で、続けて測った。開始時の load average は 9.73 と 10.7 で、どちらも参考の値である。

| スクリプト | 着手前 | 着手後 |
| --- | --- | --- |
| `supervise.py` | 409.7 ms | 328.6 ms |
| `parallel.py` | 793.9 ms | 771.2 ms |
| `release-steps.py` | 290.0 ms | 285.0 ms |
| `sprint-state.py` | 302.9 ms | 228.8 ms |
| `test-run.py` | 206.1 ms | 231.5 ms |
| `glossary.py` | 162.2 ms | 114.2 ms |

## 遅いテストの表

「速くした」は、この課題の手段が当たるファイル（偽の HTTP サーバーを使うファイルと、`plugins/ndf/scripts/tests` で耐久ワークフローや supervise をプロセスの中で動かすファイル）で、着手後の秒が着手前の 8 割未満になったもの。「そのまま」はそれ以外（負荷の差による増減を含む）。着手後の秒は着手後 1 の値である。

### 1 本あたりの秒の長い順の上位 30 件（着手前）

| # | テスト | 着手前の秒 | 処置 | 着手後の秒 |
| --- | --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/tests/test_supervise_limits.py::test_every_fix_loop_shape_gets_through_each_fix_loop_three_times` | 17.34 | そのまま | 15.02 |
| 2 | `scripts/tests/test_check_ndf_contracts.py::test_this_repository_matches_its_contracts` | 15.55 | 消した | — |
| 3 | `plugins/ndf/scripts/tests/test_relay.py::test_guard_question_blocks_exit` | 13.04 | そのまま | 12.61 |
| 4 | `plugins/ndf/skills/cross-refactoring/tests/test_fix_rules_git.py::test_items_failing_the_same_structure_check_are_dropped_after_one_fix` | 12.60 | そのまま | 18.79 |
| 5 | `plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py::test_env_and_up_wait_for_the_reservation_and_stop` | 11.04 | そのまま | 11.11 |
| 6 | `plugins/ndf/scripts/tests/test_supervise_queue.py::test_a_gate_stays_a_gate_and_a_stopped_plan_runs_again_from_the_top` | 9.49 | 速くした | 3.94 |
| 7 | `plugins/ndf/scripts/tests/test_drive_refactor.py::test_refactor_drive_workflow_step_finishes_with_counts` | 8.89 | そのまま | 7.85 |
| 8 | `plugins/ndf/scripts/tests/test_supervise_queue_continue.py::test_resume_reruns_a_gate_from_the_same_step` | 8.79 | そのまま | 5.38 |
| 9 | `plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py::test_a_discard_that_ignores_sigterm_is_stopped_with_its_children` | 8.78 | そのまま | 8.90 |
| 10 | `plugins/ndf/scripts/tests/test_drive_refactor.py::test_refactor_drive_pauses_for_cross_review_then_finalizes` | 8.44 | そのまま | 8.81 |
| 11 | `plugins/ndf/scripts/tests/test_relay.py::test_run_goal_unmet_cancelled[question]` | 8.14 | そのまま | 8.36 |
| 12 | `plugins/ndf/scripts/tests/test_relay.py::test_run_goal_unmet_cancelled[user]` | 8.10 | そのまま | 8.11 |
| 13 | `plugins/ndf/scripts/tests/test_base_sync.py::test_impl_route_goes_back_three_times_and_merges` | 8.04 | 速くした | 6.06 |
| 14 | `plugins/ndf/scripts/tests/test_supervise_contract.py::test_results_keep_their_keys_and_exit_codes` | 7.93 | そのまま | 7.48 |
| 15 | `plugins/ndf/scripts/tests/test_relay.py::test_run_goal_unmet_cancelled[background]` | 7.87 | そのまま | 8.16 |
| 16 | `plugins/ndf/scripts/tests/test_progress_record_stages.py::test_the_release_without_a_gap_says_nothing_more` | 7.67 | そのまま | 8.14 |
| 17 | `plugins/ndf/scripts/tests/test_supervise_queue_continue.py::test_resume_with_from_and_without_a_listing` | 7.55 | そのまま | 4.75 |
| 18 | `plugins/ndf/scripts/tests/test_relay.py::test_run_spin_broken_by_long_section` | 7.32 | そのまま | 7.98 |
| 19 | `plugins/ndf/scripts/tests/test_supervise_queue.py::test_a_needed_plan_of_an_earlier_stage_or_with_a_gate_record` | 7.31 | そのまま | 6.75 |
| 20 | `plugins/ndf/scripts/tests/test_relay.py::test_run_waits_for_user_input_quiet` | 7.10 | そのまま | 6.81 |
| 21 | `plugins/ndf/scripts/tests/test_supervise.py::test_queue_tells_only_the_files_of_plans_running_when_the_worker_starts` | 7.09 | そのまま | 6.48 |
| 22 | `plugins/ndf/scripts/tests/test_progress_record_stages.py::test_a_conditional_stage_without_a_record_is_shown_apart` | 6.85 | そのまま | 10.94 |
| 23 | `plugins/ndf/scripts/tests/test_supervise_queue_continue.py::test_resume_continues_from_the_stopped_step_and_runs_the_next_stage` | 6.54 | そのまま | 6.79 |
| 24 | `plugins/ndf/scripts/tests/test_base_sync.py::test_check_route_goes_back_and_reaches_record` | 6.50 | 速くした | 4.88 |
| 25 | `plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py::test_a_reservation_of_another_container_is_kept_until_its_time` | 6.46 | そのまま | 5.92 |
| 26 | `plugins/ndf/scripts/tests/test_supervise_queue.py::test_tagged_steps_do_not_exceed_the_resource_limit` | 6.39 | そのまま | 6.70 |
| 27 | `plugins/ndf/scripts/tests/test_supervise_queue.py::test_killed_queue_counts_the_rounds_of_a_loop_across_the_kill` | 6.07 | 速くした | 4.33 |
| 28 | `plugins/ndf/scripts/tests/test_parallel_hold_lanes.py::test_the_running_plan_finishes_and_the_next_plan_is_not_run_and_resume_flows_after_the_hold` | 6.02 | そのまま | 5.96 |
| 29 | `scripts/tests/test_lock_common.py::test_the_timeout_override_reaches_only_the_stage_state` | 5.91 | そのまま | 5.93 |
| 30 | `plugins/ndf/scripts/tests/test_supervise_queue.py::test_relaunching_with_other_plans_cancels_the_running_queue_before_launch` | 5.90 | そのまま | 5.17 |

### ファイルごとの合計の上位 10 本（着手前）

| # | ファイル | 着手前（件・秒） | 処置 | 着手後（件・秒） |
| --- | --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/tests/test_relay.py` | 459 件・323.4 | そのまま | 459 件・322.3 |
| 2 | `plugins/ndf/scripts/tests/test_supervise.py` | 188 件・186.9 | そのまま | 188 件・157.9 |
| 3 | `plugins/ndf/scripts/tests/test_parallel_run.py` | 88 件・115.2 | そのまま | 88 件・106.2 |
| 4 | `plugins/ndf/scripts/tests/test_supervise_queue.py` | 35 件・115.1 | そのまま | 35 件・99.6 |
| 5 | `plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py` | 63 件・75.0 | そのまま | 63 件・64.8 |
| 6 | `plugins/ndf/skills/worktree/tests/test_testenv_prepare.py` | 59 件・68.5 | そのまま | 59 件・50.2 |
| 7 | `plugins/ndf/skills/worktree/tests/test_testenv.py` | 133 件・64.9 | そのまま | 133 件・53.5 |
| 8 | `plugins/ndf/skills/cross-review/tests/test_review_drive_resume.py` | 25 件・55.8 | そのまま | 25 件・50.6 |
| 9 | `plugins/ndf/scripts/tests/test_progress_record_stages.py` | 35 件・48.4 | そのまま | 35 件・62.6 |
| 10 | `plugins/ndf/scripts/tests/test_relay_account.py` | 54 件・45.4 | 速くした | 54 件・27.8 |

## 消したテスト

| テスト | 処置 | 基準 | 守るテスト・守らなくてよい理由 |
| --- | --- | --- | --- |
| `scripts/tests/test_check_ndf_contracts.py::test_this_repository_matches_its_contracts` | 消した | 6 | suite `ndf-contracts`（`python3 scripts/check-ndf-contracts.py`）と CI の `.github/workflows/script-structure.yml` が同じ検査をリポジトリ全体へ流す |
| `scripts/tests/test_doc_line_limit.py::test_the_repository_satisfies_the_limit` | 消した | 6 | suite `doc-line-limit-check`（`python3 scripts/check-doc-line-limit.py --root .`）が同じ検査を流す |
| `scripts/tests/test_check_repo_secrets.py::test_this_repository_passes` | 消した | 6 | suite `repo-secrets-check`（`python3 scripts/check-repo-secrets.py`）が同じ検査を流す |
| `scripts/tests/test_check_skill_repo_assumptions.py::test_real_repository_passes` | 消した | 6 | suite `skill-repo-assumptions-check`（`python3 scripts/check-skill-repo-assumptions.py`）が、`plugins/ndf/skills` を含むすべての family を走査する |
| `scripts/tests/test_check_skill_repo_assumptions.py::test_default_scan_covers_the_real_repository` | 消した | 6 | 同上。消したテストは suite と同じコマンドを同じ作業ディレクトリで打っていた |
| `scripts/tests/test_cross_skill_refs.py::test_the_check_passes_on_the_repository` | 消した | 6 | suite `runtime-plugin-validate`（`scripts/validate-runtime-plugins.sh` が `check-cross-skill-refs.py --root` を流す）。例外の組の一致は `scripts/tests/test_cross_skill_refs.py` の残るテストが守る |
| `scripts/tests/test_doc_staleness.py::test_real_repository_passes` | 消した | 6 | suite `runtime-plugin-validate`（`scripts/validate-runtime-plugins.sh` が `check-doc-staleness.py --root` を流す） |

まとめた・置き換えたテストは無い。

## 使った速くする手段

| 手段（文書の一覧の項目） | 当てたもの |
| --- | --- |
| テストヘルパーの固定の待ちを縮める | 偽の HTTP サーバーを回す 6 本（`plugins/ndf/scripts/tests/account_fake.py`・`test_wait_notify.py`・`test_training_optout.py`・`test_claude_api_keys.py`・`test_lib_notify.py`・`plugins/playwright-kit/skills/playwright-authoring/tests/test_app_ready.py`）を `poll_interval=0.01` にした |
| プロセスの中で走るテストの待ちの間隔を、テストの側で差し替える | `plugins/ndf/scripts/tests/conftest.py` の `_short_durable_polls` が、読み込み済みの `durable` の `POLL_SECONDS` と `wait`・`wait_from` の既定の間隔を 1/10 にする |
| テストの対象のスクリプトの起動を縮める | `plugins/ndf/scripts/lib/md.py`（`markdown_it`）・`mdtable.py`（`tabulate`）・`locks.py`（`filelock`）の import を使う関数の中へ移した |

## 起動を縮めなかったもの

| スクリプト | 起動の大半 | 縮めなかった理由 |
| --- | --- | --- |
| `parallel.py` | 永続ワークフローのライブラリ（`dbos`）。`lib/parallel_route.py` が `@durable.step` で import の時に手順を登録する | 登録を遅らせると、耐久ワークフローの起動と再開の前に手順が登録済みである前提が崩れる。利用者から見える振る舞い（再開）を変える恐れがあり、前提 4 の範囲に入らない |
| `release-steps.py`・`test-run.py`（と `supervise.py`・`sprint-state.py` の残り） | `pydantic`（`lib/schema.py` の `BaseModel` を、宣言の形のクラスが import の時に継ぐ） | クラスの定義が import の時に要る。遅らせるにはモジュールの分け方を変える必要があり、設計の決定 4 の範囲（サードパーティの import を関数の中へ移すだけ）を超える |

## 名前だけが変わったテスト

パラメータの id にテストの対象のスクリプトの絶対パスが入るテストは、着手前を複製で測ったため nodeid が変わった。同じテストが着手後も走っている。

| テスト（着手前の nodeid） | 処置 | 基準 | 守るテスト |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/tests/test_progress_record_stages.py::test_the_hook_does_not_record_from_the_command_text[bash "/work/ai-plugins/.ndf/sprint-lspeed-1/6-impl-2309-state/work/base/plugins/ndf/scripts/projects-sync.sh" 42 stage "\u8a2d\u8a08"]` | そのまま（名前だけが変わった） | — | 着手後の同じテスト（パラメータの id の絶対パスが作業場所のもの） |
