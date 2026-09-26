"""supervise.py と supervise_lib/ の形（#1142 の C1）。

エントリポイント（副命令・`new` の種別・`example` の形）が分けた後も変わらないことと、
supervise_lib のモジュールの行数と import の向き（設計の「import の向き」）を固定する。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
PKG = SCRIPTS / "supervise_lib"

SUBCOMMANDS = ["run", "history", "expected", "example", "new", "queue", "wait", "design-glossary", "note",
               "sync-check"]
NEW_KINDS = ["impl", "fix", "check", "release", "mission", "close"]
MODULES = ["__init__", "paths", "decl", "plan", "prompts", "claude", "state", "slow", "steps", "worker_steps", "pr",
           "engine", "templates", "release_templates", "mission_waves", "mission", "queue", "commands", "new_args"]
# プランの実行の部品（ハンドラー・状態・監視・claude）とプランを作る側は Engine を import しない
NO_ENGINE = ["decl", "plan", "prompts", "claude", "state", "slow", "steps", "worker_steps", "pr", "templates",
             "release_templates", "mission_waves", "mission", "queue", "new_args", "paths"]


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SUPERVISE), *args], capture_output=True, text=True, timeout=60)


def lib_imports(name: str) -> set[str]:
    """supervise_lib の中の `name` が import する supervise_lib のモジュール。"""
    tree = ast.parse((PKG / f"{name}.py").read_text(encoding="utf-8"))
    got: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("supervise_lib"):
            if node.module == "supervise_lib":
                got |= {a.name for a in node.names}
            else:
                got.add(node.module.split(".", 1)[1])
        elif isinstance(node, ast.ImportFrom) and node.level:
            got |= {a.name for a in node.names} if not node.module else {node.module}
        elif isinstance(node, ast.Import):
            got |= {a.name.split(".", 1)[1] for a in node.names if a.name.startswith("supervise_lib.")}
    return got


def test_subcommands_do_not_change():
    p = run("--help")
    assert p.returncode == 0
    assert "{" + ",".join(SUBCOMMANDS) + "}" in p.stdout


@pytest.mark.parametrize("cmd", SUBCOMMANDS)
def test_each_subcommand_has_help(cmd):
    assert run(cmd, "--help").returncode == 0


def test_new_kinds_do_not_change():
    p = run("new", "--help")
    assert p.returncode == 0 and "{" + ",".join(NEW_KINDS) + "}" in p.stdout
    for kind in NEW_KINDS:
        assert run("new", kind, "--help").returncode == 0, kind


def test_example_is_a_plan():
    p = run("example")
    plan = json.loads(p.stdout)
    assert p.returncode == 0 and plan["steps"] and "作業場所" in plan


def test_new_without_required_exits_2():
    assert run("new", "impl", "--worktree", ".").returncode == 2


def test_package_has_the_designed_modules():
    assert sorted(f.stem for f in PKG.glob("*.py")) == sorted(MODULES)


@pytest.mark.parametrize("path", [SUPERVISE, *sorted(PKG.glob("*.py"))], ids=lambda p: p.name)
def test_no_file_exceeds_500_lines(path):
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 500


@pytest.mark.parametrize("name", NO_ENGINE)
def test_only_commands_imports_engine(name):
    assert "engine" not in lib_imports(name)


def test_imports_have_no_cycle():
    graph = {m: lib_imports(m) & set(MODULES) for m in MODULES}

    def visit(m: str, path: tuple[str, ...]) -> None:
        assert m not in path, " → ".join((*path, m))
        for n in graph[m]:
            visit(n, (*path, m))

    for m in MODULES:
        visit(m, ())
