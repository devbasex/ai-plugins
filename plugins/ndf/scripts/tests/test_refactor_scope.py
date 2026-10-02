"""refactor-scope.py: 検査が cross-refactoring へ渡す範囲を PR の差分と宣言から組む（#1484）。"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "refactor-scope.py"
_spec = importlib.util.spec_from_file_location("refactor_scope", SCRIPT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


def _tree(root: Path, *paths: str) -> Path:
    for p in paths:
        (root / p).mkdir(parents=True, exist_ok=True)
    return root


def test_scope_is_the_diff_files_not_their_directories(tmp_path):
    # #1417: 1 ファイルの変更でディレクトリ全体へ広げない
    root = _tree(tmp_path, "src/pkg/runtime", "tests")
    got = rs.build(["src/pkg/runtime/a.py"], ["tests/runtime"], [], root)
    assert got == ["src/pkg/runtime/a.py", "tests/runtime"]


def test_scope_adds_new_files_and_tests_near_the_changes_when_only_dot_is_declared(tmp_path):
    # #1295: 新設したファイルは差分に入り、宣言が . だけなら近くのテストの置き場所を足し、--scope は置き換えずに足す
    root = _tree(tmp_path, "lib/devbase/tui", "lib/devbase/commands", "tests/cli", "plugins/x/scripts/tests")
    files = ["lib/devbase/commands/env_rows.py", "lib/devbase/tui/app.py", "plugins/x/scripts/lib/m.py", "tests/cli/test_env.py"]
    got = rs.build(files, ["."], ["lib/devbase/tui", "lib/devbase/commands/env.py"], root)
    assert got == [
        "lib/devbase/commands/env_rows.py",
        "plugins/x/scripts/lib/m.py",
        "tests",
        "plugins/x/scripts/tests",
        "lib/devbase/tui",
        "lib/devbase/commands/env.py",
    ]


def test_cli_reads_the_pr_files_from_rest_and_drops_removed_ones(tmp_path):
    fake = tmp_path / "bin" / "gh"
    fake.parent.mkdir()
    fake.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"open({json.dumps(str(tmp_path / 'argv.json'))}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('src/a.py')\n"
    )
    fake.chmod(0o755)
    _tree(tmp_path, "src", "test")
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"}
    p = subprocess.run([sys.executable, str(SCRIPT), "--pr", "7", "--root", str(tmp_path)], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert p.stdout.split() == ["src/a.py", "test"]
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[:2] == ["api", "repos/{owner}/{repo}/pulls/7/files"] and "--paginate" in argv
    assert 'select(.status != "removed")' in argv[-1]


def test_cli_prints_nothing_when_the_pr_files_cannot_be_read(tmp_path):
    fake = tmp_path / "bin" / "gh"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho 'HTTP 404' >&2\nexit 1\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"}
    p = subprocess.run([sys.executable, str(SCRIPT), "--pr", "7", "--root", str(tmp_path)], capture_output=True, text=True, env=env)
    assert p.returncode == 2 and p.stdout == "" and "HTTP 404" in p.stderr
