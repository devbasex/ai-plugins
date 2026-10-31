# 全体テストの台帳（#2291・#2204）

削る基準の番号は `docs/plugin-development-guide.md` の「テストを削る基準」の表の番号である。計測は実験版の
`plugins/ndf/scripts/experimental/whole-test-timing.py`（`measure` と `diff`）で取った。

## 着手前後の実測

（計測の後に書く）

## 遅いテストの表

（計測の後に書く）

## 消した・まとめたテスト

| テスト | 処置 | 基準 | 守るテスト・守らなくてよい理由 |
| --- | --- | --- | --- |
| `scripts/tests/test_root_conftest.py::test_the_root_conftest_declares_the_plugin` | 消した | 4 | 根の conftest の文字列を固定していた。`pytester` が読まれなければ `plugins/playwright-kit/skills/playwright-kit-ops/tests` の `pytester` を使うテストが落ちる |
| `scripts/tests/test_root_conftest.py::test_the_failure_names_the_bundle_and_the_command` | まとめた | 1 | 同じ起動を `scripts/tests/test_root_conftest.py::test_a_missing_command_fails_the_collection` が行い、束とコマンドの名前も確かめる |
| `scripts/tests/test_root_conftest.py::test_the_prefix_is_declared_once` | 消した | 4 | 根の conftest の文字列を固定していた。接頭辞の除去は `scripts/tests/test_root_conftest.py::test_no_monitor_variable_survives_into_a_test` が守る |
| `scripts/tests/test_check_script_structure.py::test_repository_matches_its_allow_list` | 消した | 6 | suite `script-structure`（`python3 scripts/check-script-structure.py`）と CI の `.github/workflows/script-structure.yml` が同じ検査をリポジトリ全体へ流す |
| `plugins/ndf/scripts/tests/test_supervise_limits.py::test_every_template_plan_gets_through_each_fix_loop_three_times` | まとめた | 5 | 17 本の雛形のプランのうち、修正のループの入口の組が同じものは同じ遷移を通る。組ごとに 1 本（impl・check・release・release-prod の形）を流す `plugins/ndf/scripts/tests/test_supervise_limits.py::test_every_fix_loop_shape_gets_through_each_fix_loop_three_times` が守る。修正のループの無いプランは上限に届く経路が無い |
