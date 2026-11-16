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


# 現状固定（I-008）: 足したテストのファイルを suite ごとにまとめる経路（collect._group）を、公開の collect.collected から固定する。
# 記録用のスクリプトが、呼ばれた印（タグ）と受けた引数を calls.log へ書き、引数のファイルを収集したものとして出す。
RECORDER = (
    "import sys\n"
    "with open('calls.log', 'a', encoding='utf-8') as fh:\n"
    "    fh.write(' '.join(sys.argv[1:]) + '\\n')\n"
    "print('\\n'.join(sys.argv[2:]))\n"
)


def _recorder(tag: str, slot: bool = True) -> str:
    return f"{sys.executable} recorder.py {tag}" + (" {paths}" if slot else " fixed.py")


def _calls(gitwork) -> list[str]:
    log = gitwork.work / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_group_current_behavior_sends_each_suite_its_own_files_in_order(gitwork):
    gitwork.make({"recorder.py": RECORDER})
    suites = [
        {"name": "py", "paths": ["tests"], "collect_command": _recorder("PY")},
        {"name": "web", "paths": ["web"], "collect_command": _recorder("WEB")},
    ]
    paths = ["web/b.test.ts", "tests/test_b.py", "tests/test_a.py", "web/a.test.ts"]
    got = collect.collected(gitwork.work, paths, suites, 120)
    # suite は最初に現れたファイルの順に 1 度ずつ呼ばれ、ファイルは渡した順のまま入る
    assert _calls(gitwork) == ["WEB web/b.test.ts web/a.test.ts", "PY tests/test_b.py tests/test_a.py"]
    assert got.collected == ("tests/test_a.py", "tests/test_b.py", "web/a.test.ts", "web/b.test.ts")
    assert (got.uncollected, got.undecided) == ((), ())


def test_group_current_behavior_first_matching_suite_wins(gitwork):
    # 範囲が重なれば、並びで先の suite がファイルを受け持つ（後の suite は呼ばれない）
    gitwork.make({"recorder.py": RECORDER})
    suites = [
        {"name": "first", "paths": ["tests/unit"], "collect_command": _recorder("FIRST")},
        {"name": "second", "paths": ["tests"], "collect_command": _recorder("SECOND")},
    ]
    got = collect.collected(gitwork.work, ["tests/unit/test_a.py", "tests/test_b.py"], suites, 120)
    assert _calls(gitwork) == ["FIRST tests/unit/test_a.py", "SECOND tests/test_b.py"]
    assert got.collected == ("tests/test_b.py", "tests/unit/test_a.py")


def test_group_current_behavior_first_match_without_collect_command_is_undecided(gitwork):
    # 先に当たった suite に collect_command が無ければ、後の suite が宣言を持っていても判定できない
    gitwork.make({"recorder.py": RECORDER})
    suites = [
        {"name": "bare", "paths": ["tests"]},
        {"name": "all", "paths": ["."], "collect_command": _recorder("ALL")},
    ]
    got = collect.collected(gitwork.work, ["tests/test_a.py", "other/test_c.py"], suites, 120)
    assert _calls(gitwork) == ["ALL other/test_c.py"]
    assert (got.collected, got.undecided) == (("other/test_c.py",), ("tests/test_a.py",))


def test_group_current_behavior_paths_default_and_unusable_declarations(gitwork):
    # paths を省いた suite は全体（.）を受け持つ。paths が並びでない suite は飛ばす。collect_command が文字列でなければ宣言なし
    gitwork.make({"recorder.py": RECORDER})
    suites = [
        {"name": "broken", "paths": "tests", "collect_command": _recorder("BROKEN")},
        {"name": "nonstr", "paths": ["lib"], "collect_command": ["not", "a", "string"]},
        {"name": "rest", "collect_command": _recorder("REST")},
    ]
    got = collect.collected(gitwork.work, ["tests/test_a.py", "lib/test_l.py"], suites, 120)
    assert _calls(gitwork) == ["REST tests/test_a.py"]
    assert (got.collected, got.uncollected, got.undecided) == (("tests/test_a.py",), (), ("lib/test_l.py",))


def test_group_current_behavior_path_outside_every_suite_is_undecided(gitwork):
    gitwork.make({"recorder.py": RECORDER})
    got = collect.collected(gitwork.work, ["src/test_x.py"], [{"name": "py", "paths": ["tests"], "collect_command": _recorder("PY")}], 120)
    assert _calls(gitwork) == []
    assert (got.collected, got.uncollected, got.undecided) == ((), (), ("src/test_x.py",))


def test_group_current_behavior_template_without_slot_runs_as_is(gitwork):
    # {paths} の無いテンプレートはそのまま打つ（ファイルを渡さない）。出力に無いファイルは収集されない
    gitwork.make({"recorder.py": RECORDER})
    suites = [{"name": "py", "paths": ["tests"], "collect_command": _recorder("PY", slot=False)}]
    got = collect.collected(gitwork.work, ["tests/test_a.py"], suites, 120)
    assert _calls(gitwork) == ["PY fixed.py"]
    assert got.uncollected == ("tests/test_a.py",)


def test_group_current_behavior_same_name_suites_share_one_call_with_the_last_declaration(gitwork):
    # 同じ名前の suite は 1 つの組にまとまり、打つコマンドと終了コードは後に当たった suite の宣言を使う
    gitwork.make({"recorder.py": RECORDER})
    suites = [
        {"name": "py", "paths": ["a"], "collect_command": _recorder("A")},
        {"name": "py", "paths": ["b"], "collect_command": _recorder("B")},
    ]
    got = collect.collected(gitwork.work, ["a/test_1.py", "b/test_2.py"], suites, 120)
    assert _calls(gitwork) == ["B a/test_1.py b/test_2.py"]
    assert got.collected == ("a/test_1.py", "b/test_2.py")
