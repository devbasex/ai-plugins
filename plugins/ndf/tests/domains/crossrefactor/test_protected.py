"""テストの定義のファイルの判定（`protected_decide`。#4566 の決定 6）。"""

from __future__ import annotations

import pytest

from ndf.domains.crossrefactor import protected_decide as pd


def _of(tree=None, commands=("bash scripts/test.sh",), declared=(), search=()):
    return pd.protected_of(tree or {}, list(commands), list(declared), list(search))


@pytest.mark.parametrize("path", [".ndf/project.json", "a/.ndf/x", "package.json", "web/package.json", "tests/conftest.py",
                                  "deep/a/conftest.py", "phpunit.xml.dist", "docker-compose.ci.yaml", "Makefile"])  # fmt: skip
def test_fixed_names_are_protected_at_any_level(path):
    assert _of().covers(path)


@pytest.mark.parametrize("command", ["bash scripts/test.sh", "bash ./scripts/test.sh", "bash scripts/../scripts/test.sh"])
def test_a_script_named_by_the_command_is_protected_whatever_the_spelling(command):
    got = _of(commands=[command])
    assert got.covers("scripts/test.sh")
    # 親をファイルかリンクに置き換える変更も当たる。親の下のほかのファイルは当たらない
    assert got.covers("scripts")
    assert not got.covers("scripts/other.sh")


def test_the_value_of_an_option_with_equals_is_a_path():
    assert _of(commands=["pytest --config=cfg/pytest.cfg"]).covers("cfg/pytest.cfg")


def test_a_path_not_in_the_tree_is_still_protected():
    assert _of(tree={}, commands=["sh tools/new.sh"]).covers("tools/new.sh")


def test_a_link_protects_the_link_and_its_target_inside_the_tree():
    got = _of(tree={"scripts/test.sh": "../real/run.sh", "real/run.sh": None})
    assert got.covers("scripts/test.sh")
    assert got.covers("real/run.sh")


def test_a_file_reached_through_a_linked_directory_is_protected():
    tree = {"scripts": "tools/ci", "tools/ci/test.sh": None, "tools/ci/other.sh": None}
    got = _of(tree=tree, commands=["sh scripts/test.sh"])
    assert got.covers("scripts/test.sh") and got.covers("scripts")
    assert got.covers("tools/ci/test.sh")
    assert not got.covers("tools/ci/other.sh")


def test_a_link_out_of_the_tree_protects_only_the_link():
    got = _of(tree={"scripts/test.sh": "../../outside.sh"})
    assert got.covers("scripts/test.sh")
    assert not got.covers("outside.sh")


@pytest.mark.parametrize("element", ["", ".", "bin"])
def test_a_bare_command_word_is_protected_under_the_relative_path_elements(element):
    got = _of(commands=["runtests -q"], search=[element])
    want = "runtests" if element in ("", ".") else "bin/runtests"
    assert got.covers(want)


def test_declared_files_and_directories_are_protected():
    got = _of(declared=["package-lock.json", "tests/support/"])
    assert got.covers("package-lock.json")
    assert got.covers("tests/support/helper.py")
    assert not got.covers("src/a.py")


def test_inside_work_returns_absolute_words_under_the_roots():
    commands = ["pytest /w/repo/tests > /w/repo/report.txt", "pytest --rootdir=/w/repo", "cat /etc/x", "pytest tests", "x 2>/w/repo"]
    got = pd.inside_work(commands, ["/w/repo"])
    assert got == ("/w/repo/tests", "/w/repo/report.txt", "--rootdir=/w/repo", "/w/repo")


def test_inside_work_normalizes_dot_dot():
    assert pd.inside_work(["sh /w/other/../repo/x.sh"], ["/w/repo"]) == ("/w/other/../repo/x.sh",)
    assert pd.inside_work(["sh /w/repo/../other/x.sh"], ["/w/repo"]) == ()


def test_narrow_drops_entries_made_only_of_protected_files():
    tracked = ["src/a.py", "src/conftest.py", "package.json", "tests/support/h.py"]
    protected = _of(declared=["tests/support"])
    assert pd.narrow(["package.json", "src", "tests/support", "src/conftest.py"], tracked, protected) == ("src",)


def test_narrow_of_only_protected_is_empty():
    assert pd.narrow([".ndf"], [".ndf/project.json"], _of()) == ()
