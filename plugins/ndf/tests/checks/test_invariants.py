from __future__ import annotations

import pytest

from ndf.checks import invariants
from ndf.checks.model import CheckError


def test_own_name_in_code_fails(plugin, where):
    plugin.write(
        "src/ndf/a.py",
        '"""ndfdev の説明"""\nP = "ndfdev"\nQ = "a/ndf-dev/b"\nR = "ndfdev:design"\nS = "ndf-dev"\nT = "ndf"\n'
        'U = ".ndf/"\nV = "/ndf:design"\nW = "ndf.checks.run"\nX = "src/ndf/a.py"\nY = "ndfdevgen"\n',
    )
    plugin.write("bin/run", "#!/bin/sh\n# ndfdev/ を見る\necho plugins/ndf-dev/x\n")
    plugin.write("skills/a/SKILL.md", "/ndfdev:design を呼ぶ\n")
    found = invariants.own_name(plugin.scope())
    lines = [("run", 3), ("a.py", 2), ("a.py", 3), ("a.py", 4), ("a.py", 6), ("a.py", 7), ("a.py", 8)]
    assert where(found) == lines


def test_only_fixed_word_definitions_pass(plugin, where):
    plugin.write("src/ndf/identity.py", 'FINAL_NAME = "ndf"\nFIXED_WORDS = {\n    "ndf_settings_dir": ".ndf",\n}\nZ = ".ndf"\n')
    plugin.write("src/ndf/b.py", 'FIXED_WORDS = {"d": ".ndf"}\n')
    found = invariants.own_name(plugin.scope())
    assert where(found) == [("b.py", 1), ("identity.py", 5)]


def test_imports_outside_declared_fail(plugin):
    plugin.write("tests/x/test_a.py", 'import json\nimport requests\nfrom ndf import a\nP = "plugins/ndf/scripts"\n')
    found = invariants.undeclared_imports(plugin.scope())
    assert [(f.rule, f.line) for f in found] == [("I3", 2)]


def test_sibling_module_is_own(plugin):
    plugin.write("tests/x/_helper.py", "X = 1\n")
    plugin.write("tests/x/test_a.py", "from _helper import X\n")
    plugin.write("tests/y/test_b.py", "from _helper import X\n")
    found = invariants.undeclared_imports(plugin.scope())
    assert [(f.path.rsplit("/", 2)[-2], f.line) for f in found] == [("y", 1)]


def test_pytest_pythonpath_module_is_own(plugin):
    plugin.write("tests/support/_server.py", "X = 1\n")
    plugin.write("tests/x/test_a.py", "from _server import X\nimport _absent\n")
    found = invariants.undeclared_imports(plugin.scope())
    assert [(f.rule, f.line) for f in found] == [("I3", 2)]


def test_declared_dependency_is_allowed(plugin):
    plugin.write("src/ndf/a.py", "import httpx\nimport pytest\n")
    assert invariants.undeclared_imports(plugin.scope()) == []


# 現状固定（I-006）: `pythonpath_tops` と、`pyproject.toml` を読む `undeclared_imports` のエントリポイント
def test_pythonpath_tops_lists_direct_py_stems(plugin):
    plugin.write("tests/support/_server.py", "X = 1\n")
    plugin.write("tests/support/notes.txt", "x\n")
    plugin.write("tests/support/deep/_inner.py", "X = 1\n")
    plugin.write("src/top.py", "X = 1\n")
    assert invariants.pythonpath_tops(plugin.root) == frozenset({"_server", "top"})


@pytest.mark.parametrize(
    "toml",
    [
        "",
        "[tool]\n",
        "[tool.pytest]\n",
        "[tool.pytest.ini_options]\n",
        '[tool.pytest.ini_options]\npythonpath = "tests/support"\n',
        "[tool.pytest.ini_options]\npythonpath = []\n",
        '[tool.pytest.ini_options]\npythonpath = ["absent"]\n',
    ],
)
def test_pythonpath_tops_without_listed_dirs_is_empty(plugin, toml):
    plugin.write("tests/support/_server.py", "X = 1\n")
    plugin.write("pyproject.toml", toml)
    assert invariants.pythonpath_tops(plugin.root) == frozenset()


@pytest.mark.parametrize("toml", [None, "[project\n"])
def test_undeclared_imports_unreadable_pyproject_stops(plugin, toml):
    path = plugin.root / "pyproject.toml"
    if toml is None:
        path.unlink()
    else:
        path.write_text(toml, encoding="utf-8")
    with pytest.raises(CheckError, match="pyproject.toml を読めない"):
        invariants.undeclared_imports(plugin.scope())


# 現状固定（I-020）: `declared_tops` の依存の名前の読み方と、入れた依存の最上位の名前
def _declared(tmp_path, toml):
    (tmp_path / "pyproject.toml").write_text(toml, encoding="utf-8")
    return invariants.declared_tops(tmp_path)


def test_declared_tops_guesses_tops_from_uninstalled_dependency_names(tmp_path):
    """入れていない依存は、バージョン・extras・marker を切って正規化した名前の `-` を `_` にして推す。グループも読む。"""
    toml = (
        "[project]\n"
        "dependencies = [\"Zz.Fake_Dist[extra] ; python_version>'3'\", \"zz-other ==1\", \"zz-ne!=1\", \"zz-lt<2\"]\n"
        "[dependency-groups]\n"
        'dev = ["zz-dev~=1"]\n'
        'lint = ["ZZ__Lint>=0"]\n'
    )  # fmt: skip
    assert _declared(tmp_path, toml) == frozenset({"zz_fake_dist", "zz_other", "zz_ne", "zz_lt", "zz_dev", "zz_lint"})


def test_declared_tops_includes_installed_top_level_modules(tmp_path):
    """入れた依存は、パッケージ（distribution）が入れる最上位のモジュール（`pytest` なら `_pytest`）も持つ。"""
    toml = '[project]\ndependencies = []\n[dependency-groups]\ndev = ["pytest==0"]\n'
    assert {"pytest", "_pytest"} <= _declared(tmp_path, toml)


def test_declared_tops_without_any_dependency_is_empty(tmp_path):
    assert _declared(tmp_path, "[project]\ndependencies = []\n[dependency-groups]\n") == frozenset()


@pytest.mark.parametrize(
    ("toml", "key"),
    [("[project]\ndependencies = []\n", "dependency-groups"), ("[dependency-groups]\n", "project")],
)
def test_declared_tops_missing_table_raises_key_error(tmp_path, toml, key):
    """`[project]` と `[dependency-groups]` の片方が無いと `KeyError`（`CheckError` ではない）。"""
    with pytest.raises(KeyError, match=key):
        _declared(tmp_path, toml)


def test_declared_tops_unreadable_pyproject_stops(tmp_path):
    with pytest.raises(CheckError, match="pyproject.toml を読めない"):
        invariants.declared_tops(tmp_path)


def test_tops_from_matches_any_normalized_owner_and_guesses_uninstalled_names():
    """入れた依存は持つパッケージのどれか（正規化して比べる）が定義にあれば拾い、名前からの推しも足す（導入済みの環境に依らない）。"""
    packages = {"ab": ["A_B"], "x": ["zz", "c"], "y": ["zz"]}
    assert invariants._tops_from({"a-b", "c"}, packages) == frozenset({"ab", "x", "a_b", "c"})
