"""構造チェック（`scripts/check-script-structure.py`）の振る舞いを固定する（#1142 の I4・I5）。

一時ディレクトリへ作った `plugins/ndf/` の木に対して打つ。実物の木は、例外リストと合っているかだけを見る。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-script-structure.py"
BASELINE = REPO / "scripts" / "measure" / "structure-baseline.py"
CLAUDE_P_USAGE = REPO / "scripts" / "measure" / "claude-p-usage.py"


def run(root: Path, allow: list[dict] | None = None, *extra: str) -> tuple[int, dict]:
    args = [sys.executable, str(CHECK), "--root", str(root)]
    if allow is not None:
        path = root / "allow.json"
        path.write_text(json.dumps(allow, ensure_ascii=False))
        args += ["--allow", str(path)]
    p = subprocess.run([*args, *extra], capture_output=True, text=True)
    return p.returncode, (json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {})


def put(root: Path, rel: str, text: str) -> None:
    p = root / "plugins" / "ndf" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def kinds(result: dict) -> set[tuple[str, str]]:
    return {(i["kind"], i["name"]) for i in result["items"]}


def test_clean_tree_is_ok(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    put(tmp_path, "scripts/b.py", "def g():\n    return 2\n")
    code, r = run(tmp_path, [])
    assert code == 0
    assert r["tool"] == "check-script-structure" and r["status"] == "ok" and r["items"] == []


def test_file_over_1000_lines_fails_unless_allowed(tmp_path: Path):
    put(tmp_path, "scripts/big.py", "x = 1\n" * 1001)
    put(tmp_path, "scripts/ok.py", "x = 1\n" * 1000)
    code, r = run(tmp_path, [])
    assert code == 1 and r["status"] == "stopped"
    assert kinds(r) == {("lines", "plugins/ndf/scripts/big.py")}
    row = {"path": "plugins/ndf/scripts/big.py", "name": "", "kind": "lines", "reason": "分ける前"}
    assert run(tmp_path, [row])[0] == 0


def test_same_body_ignores_docstring_annotations_and_name(tmp_path: Path):
    put(tmp_path, "scripts/a.py", 'def repo_slug(p: str) -> str:\n    """一方。"""\n    return p.strip("/")\n')
    put(tmp_path, "skills/x/scripts/b.py", "def _repo_slug(p):\n    return p.strip('/')\n")
    code, r = run(tmp_path, [])
    assert code == 1
    assert kinds(r) == {("same-body", "plugins/ndf/scripts/a.py:repo_slug"),
                        ("same-body", "plugins/ndf/skills/x/scripts/b.py:_repo_slug")}


def test_same_name_with_different_bodies(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def now():\n    return 1\n")
    put(tmp_path, "scripts/b.py", "def now():\n    return 2\n")
    code, r = run(tmp_path, [])
    assert code == 1
    assert kinds(r) == {("same-name", "plugins/ndf/scripts/a.py:now"),
                        ("same-name", "plugins/ndf/scripts/b.py:now")}
    allow = [{"path": f"plugins/ndf/scripts/{n}.py", "name": "now", "kind": "same-name", "reason": "L0 で統合"}
             for n in ("a", "b")]
    assert run(tmp_path, allow)[0] == 0


def test_rule_excluded_names_and_tests_are_not_counted(tmp_path: Path):
    body = "def main():\n    return 0\n\ndef build_parser():\n    return 0\n\ndef _build_parser():\n    return 0\n\n" \
           "def cmd_run(a):\n    return 0\n"
    put(tmp_path, "scripts/a.py", body)
    put(tmp_path, "scripts/b.py", body)
    put(tmp_path, "scripts/a.sh", "usage() {\n  echo a\n}\n")
    put(tmp_path, "scripts/b.sh", "usage() {\n  echo a\n}\n")
    put(tmp_path, "scripts/tests/test_x.py", "def helper():\n    return 0\n" + "x = 1\n" * 1001)
    put(tmp_path, "scripts/tests/helpers.py", "def helper():\n    return 0\n")
    code, r = run(tmp_path, [])
    assert code == 0 and r["items"] == []


def test_shell_functions_are_compared_by_normalized_lines(tmp_path: Path):
    put(tmp_path, "scripts/lib/a.sh", "resolve_print_timeout() {\n  local t=1\n\n  # 注\n  echo \"$t\"\n}\n")
    put(tmp_path, "skills/x/scripts/b.sh", "function resolve_print_timeout {\n    local t=1\n    echo \"$t\"\n}\n")
    put(tmp_path, "skills/y/scripts/c.sh", "resolve_print_timeout() { echo 2; }\n")
    code, r = run(tmp_path, [])
    assert code == 1
    assert kinds(r) == {
        ("same-body", "plugins/ndf/scripts/lib/a.sh:resolve_print_timeout"),
        ("same-body", "plugins/ndf/skills/x/scripts/b.sh:resolve_print_timeout"),
        ("same-name", "plugins/ndf/scripts/lib/a.sh:resolve_print_timeout"),
        ("same-name", "plugins/ndf/skills/x/scripts/b.sh:resolve_print_timeout"),
        ("same-name", "plugins/ndf/skills/y/scripts/c.sh:resolve_print_timeout"),
    }


def test_python_and_shell_names_are_separate(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def log():\n    return 1\n")
    put(tmp_path, "scripts/a.sh", "log() {\n  echo 1\n}\n")
    assert run(tmp_path, [])[0] == 0


def test_unused_allow_row_fails(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    row = {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name", "reason": "直した"}
    code, r = run(tmp_path, [row])
    assert code == 1
    assert kinds(r) == {("unused-allow", "plugins/ndf/scripts/a.py:f")}


@pytest.mark.parametrize("row", [
    {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name"},
    {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name", "reason": ""},
    {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "other", "reason": "x"},
])
def test_broken_allow_row_is_a_usage_error(tmp_path: Path, row: dict):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    code, r = run(tmp_path, [row])
    assert code == 2 and r["status"] == "stopped"


def test_repository_matches_its_allow_list():
    """実物の木は例外リストと合う。直した移行ステップは、同じ PR で例外リストの行を消す。"""
    p = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True, cwd=REPO)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]


def test_structure_baseline_runs_on_a_given_root(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    put(tmp_path, "scripts/b.py", "def f():\n    return 1\n")
    p = subprocess.run([sys.executable, str(BASELINE), str(tmp_path / "plugins" / "ndf")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "files 2 / lines 4" in p.stdout
    assert "defined in 2+ files: 1 (with identical bodies somewhere: 1)" in p.stdout


def test_claude_p_usage_takes_paths_as_arguments():
    p = subprocess.run([sys.executable, str(CLAUDE_P_USAGE), "--help"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    for opt in ("--repo", "--sv-root", "--projects", "--out"):
        assert opt in p.stdout
