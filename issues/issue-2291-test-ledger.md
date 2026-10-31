# 全体テストの台帳（#2291・#2204）

削る基準の番号は `docs/plugin-development-guide.md` の「テストを削る基準」の表の番号である。計測は実験版の
`plugins/ndf/scripts/experimental/whole-test-timing.py`（`measure` と `diff`）で取った。

## 着手前後の実測

計測のマシンはこの開発コンテナ（CPU 8 個・メモリ 30 GB）、並列の数は宣言のまま（pytest の `-n 4`）。

| 計測 | 日時（JST） | 開始時の load average | 壁時計 | suite ごとの秒 | 件数（skip） | 各テストの所要の合計 |
| --- | --- | --- | --- | --- | --- | --- |
| 着手前 | 2026-10-31 23:21 | 7.63 | 962.9 秒 | pytest 932.5 秒・factory 15.8 秒・script-structure 8.9 秒・lint 5.7 秒（pytest は 1 件が落ちた。根の conftest の文字列を固定していた `scripts/tests/test_root_conftest.py::test_the_root_conftest_declares_the_plugin` で、後始末の部品を根の conftest に足した後に測ったため。下の表で消した） | 10777（4） | 3454.6 秒 |
| 着手後 | 2026-11-01 01:46 | 1.33 | 635.8 秒 | pytest 607.5 秒・factory 14.8 秒・script-structure 8.4 秒・lint 5.1 秒 | 10781（4） | 2273.8 秒 |

- 着手前と着手後で開始時の load average が違う（7.63 と 1.33。どちらも CPU の数 8 以下）。所要の差には負荷の差も入っている。後始末の部品とテストの書き直しの前半を入れた後の途中の計測（load average 1.31）は壁時計 623.0 秒・合計 2,295.0 秒で、着手後とほぼ同じだった
- 180 秒に届いていない。着手後の壁時計 635.8 秒のうち pytest が 607.5 秒で、残りの 3 つの suite は計 28.3 秒である。180 秒に入れるには pytest を約 150 秒（各テストの所要の合計で約 550 秒）にする必要があり、今の 2,273.8 秒から約 1,700 秒を減らす差が残る
- 0.3 秒未満のテスト（途中の計測で 9,028 件）だけで合計 481 秒ある。残りの時間は、本物のスクリプトと子のプロセスを起こして待つ結合テスト（中継・supervise の queue と再開・cross-review と cross-refactoring の drive）に集まっている
- 照合（`whole-test-timing.py diff`）: 台帳に無い消えたテスト 0 件・skip に変わったテスト 0 件。着手後の件数が増えたのは、後始末の部品と計測の実験版のテストを足したため

## 遅いテストの表

「速くした」は、ファイルを書き換え、着手後の秒が着手前の 8 割未満になったもの。「そのまま」は書き換えていないか、縮みが 2 割に届かないもの（負荷の差による縮みを含む）。

### 1 本あたりの秒の長い順の上位 30 件（着手前）

| # | テスト | 着手前の秒 | 処置 | 着手後の秒 |
| --- | --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/tests/test_supervise_limits.py::test_every_template_plan_gets_through_each_fix_loop_three_times` | 78.52 | まとめた | 14.39 |
| 2 | `scripts/tests/test_root_conftest.py::test_a_bundle_outside_the_table_is_not_checked` | 41.70 | 速くした | 0.48 |
| 3 | `plugins/ndf/skills/cross-refactoring/tests/test_fix_rules_git.py::test_items_failing_the_same_structure_check_are_dropped_after_one_fix` | 18.72 | そのまま | 12.20 |
| 4 | `plugins/ndf/scripts/tests/test_supervise_contract.py::test_results_keep_their_keys_and_exit_codes` | 16.82 | 速くした | 7.49 |
| 5 | `plugins/ndf/scripts/tests/test_drive_refactor.py::test_refactor_drive_workflow_step_finishes_with_counts` | 12.91 | そのまま | 5.04 |
| 6 | `scripts/tests/test_check_script_structure.py::test_repository_matches_its_allow_list` | 12.76 | 消した | — |
| 7 | `plugins/ndf/scripts/tests/test_relay.py::test_run_waits_for_user_input_quiet` | 11.45 | 速くした | 6.51 |
| 8 | `plugins/ndf/scripts/tests/test_progress_record_stages.py::test_the_release_without_a_gap_says_nothing_more` | 10.66 | そのまま | 5.78 |
| 9 | `plugins/ndf/scripts/tests/test_drive_refactor.py::test_refactor_drive_pauses_for_cross_review_then_finalizes` | 9.99 | そのまま | 4.95 |
| 10 | `plugins/ndf/scripts/tests/test_relay.py::test_slow_usage_endpoint_does_not_stall_relay` | 9.88 | 速くした | 4.82 |
| 11 | `plugins/ndf/scripts/tests/test_supervise_contract.py::test_subcommands_keep_their_arguments` | 9.13 | 速くした | 0.10 |
| 12 | `plugins/ndf/scripts/tests/test_base_sync.py::test_impl_route_goes_back_three_times_and_merges` | 8.42 | そのまま | 6.84 |
| 13 | `plugins/ndf/skills/cross-review/tests/test_review_drive_resume.py::test_the_same_head_returns_the_previous_result` | 7.77 | そのまま | 3.12 |
| 14 | `plugins/ndf/scripts/tests/test_progress_record_stages.py::test_a_conditional_stage_without_a_record_is_shown_apart` | 7.76 | そのまま | 4.95 |
| 15 | `plugins/ndf/scripts/tests/test_supervise_queue.py::test_a_needed_plan_of_an_earlier_stage_or_with_a_gate_record` | 7.52 | そのまま | 5.48 |
| 16 | `plugins/ndf/scripts/tests/test_base_sync.py::test_check_route_goes_back_and_reaches_record` | 7.52 | 速くした | 5.99 |
| 17 | `scripts/tests/test_agy_distribution.py::test_generated_links_are_not_collected_twice` | 7.50 | そのまま | 5.75 |
| 18 | `plugins/ndf/scripts/tests/test_relay.py::test_run_spin_broken_by_long_section` | 7.21 | そのまま | 6.25 |
| 19 | `plugins/ndf/scripts/tests/test_relay.py::test_guard_question_blocks_exit` | 6.88 | 速くした | 3.05 |
| 20 | `plugins/ndf/scripts/tests/test_supervise.py::test_queue_tells_only_the_files_of_plans_running_when_the_worker_starts` | 6.50 | そのまま | 6.05 |
| 21 | `plugins/ndf/skills/cross-refactoring/tests/test_whole_test_triage_git.py::test_a_failure_caused_by_the_change_goes_to_fix_and_is_kept_when_fixed` | 6.34 | そのまま | 4.51 |
| 22 | `plugins/ndf/scripts/tests/test_supervise_queue.py::test_tagged_steps_do_not_exceed_the_resource_limit` | 6.13 | そのまま | 5.81 |
| 23 | `plugins/ndf/skills/cross-review/tests/test_review_drive_resume.py::test_a_record_without_head_returns_the_previous_result` | 6.10 | そのまま | 2.98 |
| 24 | `plugins/ndf/scripts/tests/test_supervise_queue_continue.py::test_resume_continues_from_the_stopped_step_and_runs_the_next_stage` | 6.09 | そのまま | 5.58 |
| 25 | `scripts/tests/test_lock_common.py::test_the_timeout_override_reaches_only_the_stage_state` | 5.96 | そのまま | 5.94 |
| 26 | `plugins/ndf/skills/cross-refactoring/tests/test_stop_revert_git.py::test_plan_a_reverts_only_the_culprit_and_the_rechecked_gate_passes` | 5.90 | そのまま | 3.11 |
| 27 | `plugins/ndf/skills/cross-refactoring/tests/test_whole_test_triage_git.py::test_without_time_to_fix_the_newest_flagged_items_are_reverted_until_it_passes` | 5.83 | そのまま | 5.09 |
| 28 | `plugins/ndf/scripts/tests/test_progress_record_stages.py::test_the_record_stage_of_supervise_is_recorded` | 5.78 | そのまま | 2.10 |
| 29 | `plugins/ndf/scripts/tests/test_drive_review.py::test_review_drive_pauses_for_fix_then_sweep_then_finishes` | 5.72 | そのまま | 4.45 |
| 30 | `plugins/ndf/skills/cross-refactoring/tests/test_stop_revert_git.py::test_every_child_limit_from_the_cut_off_to_the_recheck_ends_by_the_end` | 5.61 | そのまま | 3.07 |

### ファイルごとの合計の上位 10 本（着手前）

| # | ファイル | 着手前（件・秒） | 処置 | 着手後（件・秒） |
| --- | --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/tests/test_relay.py` | 423 件・303.0 | 速くした | 423 件・205.5 |
| 2 | `plugins/ndf/scripts/tests/test_supervise.py` | 184 件・181.6 | 速くした | 184 件・143.1 |
| 3 | `plugins/ndf/scripts/tests/test_supervise_limits.py` | 15 件・103.2 | 速くした | 15 件・31.4 |
| 4 | `plugins/ndf/scripts/tests/test_supervise_queue.py` | 35 件・99.4 | そのまま | 35 件・85.7 |
| 5 | `plugins/ndf/skills/cross-review/tests/test_review_drive_resume.py` | 25 件・71.7 | そのまま | 25 件・48.7 |
| 6 | `plugins/ndf/scripts/tests/test_glossary.py` | 105 件・71.0 | 速くした | 105 件・7.9 |
| 7 | `plugins/ndf/scripts/tests/test_progress_record_stages.py` | 36 件・65.4 | そのまま | 36 件・32.2 |
| 8 | `plugins/ndf/scripts/tests/test_supervise_new.py` | 70 件・52.8 | 速くした | 70 件・11.1 |
| 9 | `plugins/ndf/scripts/tests/test_relay_account.py` | 54 件・51.4 | そのまま | 54 件・38.8 |
| 10 | `scripts/tests/test_root_conftest.py` | 18 件・48.9 | 速くした・消した | 15 件・5.2 |


## 消した・まとめたテスト

| テスト | 処置 | 基準 | 守るテスト・守らなくてよい理由 |
| --- | --- | --- | --- |
| `scripts/tests/test_root_conftest.py::test_the_root_conftest_declares_the_plugin` | 消した | 4 | 根の conftest の文字列を固定していた。`pytester` が読まれなければ `plugins/playwright-kit/skills/playwright-kit-ops/tests` の `pytester` を使うテストが落ちる |
| `scripts/tests/test_root_conftest.py::test_the_failure_names_the_bundle_and_the_command` | まとめた | 1 | 同じ起動を `scripts/tests/test_root_conftest.py::test_a_missing_command_fails_the_collection` が行い、束とコマンドの名前も確かめる |
| `scripts/tests/test_root_conftest.py::test_the_prefix_is_declared_once` | 消した | 4 | 根の conftest の文字列を固定していた。接頭辞の除去は `scripts/tests/test_root_conftest.py::test_no_monitor_variable_survives_into_a_test` が守る |
| `scripts/tests/test_check_script_structure.py::test_repository_matches_its_allow_list` | 消した | 6 | suite `script-structure`（`python3 scripts/check-script-structure.py`）と CI の `.github/workflows/script-structure.yml` が同じ検査をリポジトリ全体へ流す |
| `plugins/ndf/scripts/tests/test_supervise_limits.py::test_every_template_plan_gets_through_each_fix_loop_three_times` | まとめた | 5 | 17 本の雛形のプランのうち、修正のループの入口の組が同じものは同じ遷移を通る。組ごとに 1 本（impl・check・release・release-prod の形）を流す `plugins/ndf/scripts/tests/test_supervise_limits.py::test_every_fix_loop_shape_gets_through_each_fix_loop_three_times` が守る。修正のループの無いプランは上限に届く経路が無い |

## 打ち切りの確かめ（受け入れ条件 6・7）

2026-11-01 02:00 JST ごろ に手元で確かめた。数えたのは、作業場所のパスをコマンドラインに含むプロセスのうち、打ち切る前から動いていた作業者のセッションと言語サーバーを除いたもの。

| 打ち切り方 | 打ち切る前 | 打ち切りから 10 秒後 |
| --- | --- | --- |
| `test_triage.run_command` に宣言の pytest のコマンドを上限 30 秒で渡す（cross-refactoring の着手前のテストと同じ打ち切る側。30.2 秒で打ち切った） | — | 0 件 |
| `pytest -n 4`（`test_relay.py`・`test_supervise_queue.py`）を 25 秒流し、親だけへ `kill -TERM` | 11 件 | 0 件 |

`python3 plugins/ndf/scripts/test-run.py whole --limit 30` は、PR の無いこのブランチでは CI のチェックの結論を待つ側へ進み、手元のテストを流さなかったため、上の 1 行目で打ち切る側を直に呼んで確かめた。
