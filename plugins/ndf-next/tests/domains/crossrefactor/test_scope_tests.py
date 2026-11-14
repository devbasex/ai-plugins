"""範囲テストに渡すテストのファイルの選び方。

- #3049: 項目の範囲テストを触ったモジュールに対応するテストへ絞り、ディレクトリ（実質の全体テスト）を渡さない
- #2096: 全体テストを CI に任せる戦略の着手前の範囲テストは、ファイルだけを `{paths}` へ入れる
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from ndf.domains.crossrefactor import scope, tests_decide

FILES = {
    "lib/foo.py": "def f():\n    return 1\n",
    "lib/bar.py": "def g():\n    return 2\n",
    "lib/orphan.py": "X = 1\n",
    "tests/test_foo.py": "import unittest\nfrom lib import foo\n\n\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(foo.f(), 1)\n",
    "tests/test_bar.py": "import unittest\nimport lib.bar\n\n\nclass T(unittest.TestCase):\n    def test_g(self):\n        self.assertEqual(lib.bar.g(), 2)\n",
    "tests/helpers.py": "VALUE = 1\n",
    "web/button.ts": "export const b = 1\n",
    "web/button.test.ts": "import { b } from './button'\n",
}


def test_item_tests_are_the_tests_that_import_the_touched_module(gitwork):
    gitwork.make(FILES)
    got = scope.tests_for_change(gitwork.work, ["lib/foo.py"])
    assert got.files == ("tests/test_foo.py",)
    assert got.unmapped == ()


def test_item_tests_never_contain_a_directory(gitwork):
    gitwork.make(FILES)
    got = scope.tests_for_change(gitwork.work, ["lib/foo.py", "lib/bar.py", "web/button.ts"])
    assert got.files == ("tests/test_bar.py", "tests/test_foo.py", "web/button.test.ts")
    assert all((gitwork.work / f).is_file() for f in got.files)


def test_source_without_a_test_is_unmapped(gitwork):
    gitwork.make(FILES)
    got = scope.tests_for_change(gitwork.work, ["lib/orphan.py"])
    assert got.files == () and got.unmapped == ("lib/orphan.py",)


def test_touched_test_file_selects_itself():
    got = tests_decide.tests_for_change(["tests/test_foo.py"], {"tests/test_foo.py": "import x\n"})
    assert got.files == ("tests/test_foo.py",)


def test_unreadable_test_is_reported_and_still_matched_by_name():
    got = tests_decide.tests_for_change(["lib/foo.py"], {"tests/test_foo.py": "def broken(:\n", "tests/test_x.py": None})
    assert got.files == ("tests/test_foo.py",)
    assert got.unreadable == ("tests/test_foo.py", "tests/test_x.py")


def test_scope_test_files_are_files_and_run_with_a_file_only_template(gitwork):
    # #2096: python3 -m unittest {paths} はディレクトリを受けない。置き場所の配下のテストのファイルを渡す
    gitwork.make(FILES)
    files = scope.scope_test_files(gitwork.work, ["tests", "lib"])
    # tests/ の下の補助のファイルも、共有の表（置き場の行）ではテストのファイルに数える
    assert files == ["tests/helpers.py", "tests/test_bar.py", "tests/test_foo.py"]
    template = f"{sys.executable} -m unittest {{paths}}"
    done = subprocess.run(tests_decide.fill_paths(template, files), shell=True, cwd=gitwork.work, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_scope_without_tests_gives_no_files(gitwork):
    gitwork.make(FILES)
    assert scope.scope_test_files(gitwork.work, ["lib"]) == []


def test_fill_paths_quotes_and_requires_the_slot():
    assert tests_decide.fill_paths("run {paths}", ["a b.py", "c.py"]) == "run 'a b.py' c.py"
    with pytest.raises(ValueError, match="paths"):
        tests_decide.fill_paths("run", ["a.py"])


@pytest.mark.parametrize(
    ("path", "expected"),
    [("tests/test_a.py", True), ("pkg/a_test.go", True), ("src/latest_version.py", False), ("web/contest_rules.ts", False), ("spec/x.rb", True)],
)
def test_test_file_names_follow_the_shared_table(path, expected):
    assert tests_decide.is_test_file(path) is expected


def test_module_names_of_a_package():
    assert tests_decide.module_names("src/ndf/domains/x/__init__.py") == frozenset({"src.ndf.domains.x", "ndf.domains.x", "domains.x", "x"})
