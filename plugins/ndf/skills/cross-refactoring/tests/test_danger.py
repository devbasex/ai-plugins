"""危険の印（D1〜D5）の判定を、**実際の git リポジトリ**で確かめる（#933 の「危険の印」）。"""
from __future__ import annotations

import importlib
import subprocess

import pytest


@pytest.fixture(scope="module")
def danger(refactor):
    return importlib.import_module("refactor_lib.danger")


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


def _write(repo, rel, text):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo, message="c"):
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@e.st", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    _write(repo, "src/refactor_lib/plan.py", "def run():\n    return 1\n")
    _write(repo, "src/refactor_lib/__init__.py", "")
    _write(repo, "src/refactor_lib/other.py", "x = 1\n")
    _write(repo, "tests/test_plan.py", "from refactor_lib import plan\n\ndef test_run():\n    assert plan.run()\n")
    _write(repo, "tools/uses_planner.py", "from refactor_lib import planner\n")
    _write(repo, "docs/guide.md", "refactor_lib/plan と refactor_lib.plan を説明する\n")
    _write(repo, "notes.txt", "from refactor_lib import plan\n")
    _commit(repo, "init")
    return repo


SCOPE = ["src", "tests"]


def test_d1_other_files(danger):
    assert not danger.d1(["src/a.py", "tests/test_a.py"], "src/a.py", ["tests/test_a.py"])
    assert danger.d1(["src/a.py", "src/b.py"], "src/a.py", [])


def test_d2_detects_delete_and_rename(danger, repo):
    _write(repo, "src/refactor_lib/other.py", "x = 2\n")
    modified = _commit(repo)
    assert not danger.d2(str(repo), [modified])
    _git("mv", "src/refactor_lib/other.py", "src/refactor_lib/moved.py", cwd=repo)
    renamed = _commit(repo)
    assert danger.d2(str(repo), [modified, renamed])
    (repo / "src/refactor_lib/moved.py").unlink()
    deleted = _commit(repo)
    assert danger.d2(str(repo), [deleted])


def test_d3_no_hit_when_only_docs_and_similar_names_refer(danger, repo):
    # docs/・*.md・*.txt の言及と、`planner` のような語の一部は当たらない
    assert danger.d3(str(repo), ["src/refactor_lib/plan.py"], "run", SCOPE) == []


def test_d3_hits_each_module_reference_form_outside_scope(danger, repo):
    _write(repo, "app/a.py", "from refactor_lib import (info, plan)\n")
    _write(repo, "app/b.py", "import refactor_lib.plan\n")
    _write(repo, "app/c.ts", "import x from '../src/refactor_lib/plan'\n")
    _commit(repo)
    hits = danger.d3(str(repo), ["src/refactor_lib/plan.py"], "run", SCOPE)
    assert hits == ["refactor_lib/plan", "refactor_lib\\.plan", "refactor_lib import .*\\bplan\\b"]


def test_d3_ignores_non_code_files_and_scope(danger, repo):
    _write(repo, "app/config.yaml", "module: refactor_lib.plan\n")
    _commit(repo)
    assert danger.d3(str(repo), ["src/refactor_lib/plan.py"], "run", SCOPE) == []
    # --scope の中（tests/）からの参照は外からの参照でない
    assert danger.d3(str(repo), ["src/refactor_lib/plan.py"], "", ["src"]) == [
        "refactor_lib import .*\\bplan\\b"]


def test_d3_qualified_symbol_and_entry(danger, repo):
    _write(repo, "app/use.py", "obj = Foo.run\n")
    _commit(repo)
    assert danger.d3(str(repo), ["src/refactor_lib/other.py"], "Foo.run", SCOPE) == ["Foo\\.run"]
    assert danger.d3(str(repo), ["src/refactor_lib/__init__.py"], "", SCOPE) == [
        "entry:src/refactor_lib/__init__.py"]
    # テストのファイルは探さない
    assert danger.d3(str(repo), ["tests/test_plan.py"], "", SCOPE) == []


def test_d3_root_file_uses_line_start_import(danger, repo):
    _write(repo, "helper.py", "x = 1\n")
    _write(repo, "app/use.py", "import helper\n")
    _write(repo, "app/other.py", "from pkg import helper\n")
    _commit(repo)
    assert danger.d3(str(repo), ["helper.py"], "", SCOPE) == ["^[[:space:]]*import helper\\b"]


def test_d3_grep_failure_raises_the_flag(danger, tmp_path):
    # git のリポジトリでない場所では git grep が 128 で終わる。当たり無しと読まない
    hits = danger.d3(str(tmp_path), ["src/pkg/mod.py"], "", SCOPE)
    assert hits and all(h.startswith("grep-failed:") for h in hits)


def test_d4_name_or_symbol_in_limited_tests(danger, repo):
    work = str(repo)
    assert not danger.d4(work, ["tests/test_plan.py"], "src/refactor_lib/plan.py", "")
    assert not danger.d4(work, ["tests/test_plan.py"], "src/refactor_lib/other.py", "Plan.run")
    assert danger.d4(work, ["tests/test_plan.py"], "src/refactor_lib/other.py", "Foo.build")
    assert danger.d4(work, None, "src/refactor_lib/plan.py", "run")
    assert danger.d4(work, [], "src/refactor_lib/plan.py", "run")


def test_limited_test_files(danger, repo):
    assert danger.limited_test_files_from_targets(
        ["tests/test_plan.py::test_run", "tests/test_plan.py", "tests/x.py"]
    ) == ["tests/test_plan.py", "tests/x.py"]
    assert danger.limited_test_files_from_round_test("pytest tests -q", str(repo)) == [
        "tests/test_plan.py"]
    # 対象の語が無いコマンドは挙げられない（決定 21）
    assert danger.limited_test_files_from_round_test("make test", str(repo)) is None


def test_item_flags_combines(danger, repo):
    _write(repo, "src/refactor_lib/plan.py", "def run():\n    return 2\n")
    _write(repo, "src/refactor_lib/other.py", "x = 3\n")
    sha = _commit(repo)
    # symbol の名前がテストに現れると other.py も覆うとみなすため、現れない名前にする
    item = {"path": "src/refactor_lib/plan.py", "symbol": "build_all", "tests": []}
    files = ["src/refactor_lib/plan.py", "src/refactor_lib/other.py"]
    out = danger.item_flags(str(repo), item, [sha], files, SCOPE, ["tests/test_plan.py"], True)
    # other.py の名前は限ったテストに現れない → D4
    assert out == {"flags": ["D1", "D4", "D5"], "hits": []}

    only = danger.item_flags(str(repo), item, [sha], ["src/refactor_lib/plan.py"],
                             SCOPE, ["tests/test_plan.py"], False)
    assert only == {"flags": [], "hits": []}
