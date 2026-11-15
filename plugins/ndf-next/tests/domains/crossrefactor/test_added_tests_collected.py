"""#620: テストを足す項目が、全体の実行の収集しないファイルへテストを足しても検証を通る不具合の再現と直し。

PR #612 の R1-001 の形（補助のモジュール `tests/doc_staleness_helpers.py` へ足したテスト）は、範囲テストで通っても
全体の実行（pytest の `python_files`）に入らない。収集の確かめがこの形を `uncollected` に分ける。
"""

from __future__ import annotations

import sys

import pytest

from ndf.domains.crossrefactor import collect, tests_decide

FILES = {
    "tests/test_doc.py": "def test_doc():\n    assert True\n",
    "tests/doc_staleness_helpers.py": "def test_added_by_the_item():\n    assert True\n",
}
COLLECT = f"{sys.executable} -m pytest --collect-only -q -p no:cacheprovider tests"


def suite(**extra):
    return {"name": "pytest", "runner": "pytest", "command": "pytest tests", "paths": ["tests"], **extra}


def test_test_added_to_a_helper_module_is_not_collected(gitwork):
    gitwork.make(FILES)
    got = collect.collected(gitwork.work, ["tests/test_doc.py", "tests/doc_staleness_helpers.py"], [suite(collect_command=COLLECT)], 120)
    assert got.collected == ("tests/test_doc.py",)
    assert got.uncollected == ("tests/doc_staleness_helpers.py",)
    assert got.undecided == ()


def test_suite_without_a_collect_declaration_is_undecided(gitwork):
    gitwork.make(FILES)
    got = collect.collected(gitwork.work, ["tests/doc_staleness_helpers.py"], [suite()], 120)
    assert got.undecided == ("tests/doc_staleness_helpers.py",)


def test_collect_failure_stops_instead_of_passing(gitwork):
    gitwork.make(FILES)
    with pytest.raises(collect.CollectError, match="終了コード 3"):
        collect.collected(gitwork.work, ["tests/test_doc.py"], [suite(collect_command=f"{sys.executable} -c 'raise SystemExit(3)'")], 120)


EXIT_5 = f"{sys.executable} -c 'raise SystemExit(5)'"


def test_declared_exit_codes_are_read_as_success(gitwork):
    # 受け入れ条件 12: collect_exit_codes の並びの終了コードだけを成功と読む（5 は収集が 0 件で、どのファイルも収集されない）
    gitwork.make(FILES)
    got = collect.collected(gitwork.work, ["tests/test_doc.py"], [suite(collect_command=EXIT_5, collect_exit_codes=[0, 5])], 120)
    assert got.uncollected == ("tests/test_doc.py",)
    exit_1 = f"{sys.executable} -c 'raise SystemExit(1)'"
    with pytest.raises(collect.CollectError, match="終了コード 1"):
        collect.collected(gitwork.work, ["tests/test_doc.py"], [suite(collect_command=exit_1, collect_exit_codes=[0])], 120)


def test_exit_code_5_stops_a_suite_without_the_declaration(gitwork):
    # 受け入れ条件 13: 宣言の無い suite は 0 だけを成功と読み、pytest の 5 も CollectError にする
    gitwork.make(FILES)
    with pytest.raises(collect.CollectError, match="終了コード 5"):
        collect.collected(gitwork.work, ["tests/test_doc.py"], [suite(collect_command=EXIT_5)], 120)


@pytest.mark.parametrize(
    ("declared", "codes"),
    [({"collect_exit_codes": [0, 5]}, {0, 5}), ({}, {0}), ({"collect_exit_codes": [True]}, {0}), ({"collect_exit_codes": 5}, {0})],
)
def test_exit_codes_of_reads_the_suite_declaration(declared, codes):
    assert collect.exit_codes_of(declared) == frozenset(codes)


def test_collect_timeout_stops(gitwork):
    gitwork.make(FILES)
    slow = f"{sys.executable} -c 'import time; time.sleep(5)'"
    with pytest.raises(collect.CollectError, match="上限"):
        collect.collected(gitwork.work, ["tests/test_doc.py"], [suite(collect_command=slow)], 0.2)


def test_collected_files_reads_node_ids_and_plain_paths():
    out = "tests/test_a.py::test_x\ntests/test_a.py::T::test_y\nweb/a.test.ts\n\n2 tests collected in 0.01s\n"
    assert tests_decide.collected_files(out) == frozenset({"tests/test_a.py", "web/a.test.ts"})


def test_every_path_lands_in_exactly_one_group():
    got = tests_decide.collect_verdict(["a", "b", "c"], {"a": frozenset({"a"}), "b": frozenset({"a"})})
    assert (got.collected, got.uncollected, got.undecided) == (("a",), ("b",), ("c",))
