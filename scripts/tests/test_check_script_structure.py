"""構造チェック（`scripts/check-script-structure.py`）の振る舞いを固定する（#1142 の I4・I5）。

一時ディレクトリへ作った `plugins/ndf/` の木に対して打つ。実物の木は、例外リストと合っているかだけを見る。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-script-structure.py"
BASELINE = REPO / "scripts" / "measure" / "structure-baseline.py"
CLAUDE_P_USAGE = REPO / "scripts" / "measure" / "claude-p-usage.py"


_spec = importlib.util.spec_from_file_location("check_script_structure", CHECK)
structure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(structure)


def write_allow(d: Path, rows: list[dict]) -> Path:
    """例外リストを 1 項目 1 ファイルの置き場へ書く。"""
    d.mkdir(parents=True, exist_ok=True)
    for r in rows:
        (d / structure.allow_file_name(r)).write_text(json.dumps(r, ensure_ascii=False) + "\n")
    return d


def run(root: Path, allow: list[dict] | None = None, *extra: str) -> tuple[int, dict]:
    args = [sys.executable, str(CHECK), "--root", str(root)]
    if allow is not None:
        args += ["--allow", str(write_allow(root / "allow", allow))]
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


def test_file_over_500_lines_fails_unless_allowed(tmp_path: Path):
    put(tmp_path, "scripts/big.py", "x = 1\n" * 501)
    put(tmp_path, "scripts/ok.py", "x = 1\n" * 500)
    code, r = run(tmp_path, [])
    assert code == 1 and r["status"] == "stopped"
    assert kinds(r) == {("lines", "plugins/ndf/scripts/big.py")}
    row = {"path": "plugins/ndf/scripts/big.py", "name": "", "kind": "lines", "reason": "分ける前", "lines": 501}
    assert run(tmp_path, [row])[0] == 0


def test_allowed_file_fails_when_it_grows_past_the_listed_lines(tmp_path: Path):
    """例外リストのラチェット（決定 18）: 載せた行数を 1 行でも超えたら落ちる。減るのはよい。"""
    row = {"path": "plugins/ndf/scripts/big.py", "name": "", "kind": "lines", "reason": "分ける前", "lines": 600}
    put(tmp_path, "scripts/big.py", "x = 1\n" * 601)
    code, r = run(tmp_path, [row])
    assert code == 1
    assert kinds(r) == {("lines", "plugins/ndf/scripts/big.py")}
    assert "600" in r["items"][0]["detail"]
    put(tmp_path, "scripts/big.py", "x = 1\n" * 599)
    assert run(tmp_path, [row])[0] == 0


def test_new_file_over_500_lines_is_not_covered_by_other_rows(tmp_path: Path):
    row = {"path": "plugins/ndf/scripts/big.py", "name": "", "kind": "lines", "reason": "分ける前", "lines": 600}
    put(tmp_path, "scripts/big.py", "x = 1\n" * 600)
    put(tmp_path, "scripts/new.py", "x = 1\n" * 501)
    code, r = run(tmp_path, [row])
    assert code == 1
    assert kinds(r) == {("lines", "plugins/ndf/scripts/new.py")}


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
    put(tmp_path, "scripts/tests/test_x.py", "def helper():\n    return 0\n" + "x = 1\n" * 501)
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
    {"path": "plugins/ndf/scripts/a.py", "name": "", "kind": "lines", "reason": "x"},
    {"path": "plugins/ndf/scripts/a.py", "name": "", "kind": "lines", "reason": "x", "lines": "600"},
    {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name", "reason": "x", "lines": 600},
])
def test_broken_allow_row_is_a_usage_error(tmp_path: Path, row: dict):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    code, r = run(tmp_path, [row])
    assert code == 2 and r["status"] == "stopped"


def test_allow_file_name_must_match_the_row(tmp_path: Path):
    """ファイル名は path・name・kind から一意に決まる。合わない名前は形の誤り。"""
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    put(tmp_path, "scripts/b.py", "def f():\n    return 2\n")
    row = {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name", "reason": "x"}
    assert structure.allow_file_name(row) == "plugins__ndf__scripts__a.py--f--same-name.json"
    assert structure.allow_file_name({"path": "plugins/ndf/x.sh", "name": "", "kind": "lines"}) \
        == "plugins__ndf__x.sh--lines.json"
    d = tmp_path / "allow"
    d.mkdir()
    (d / "other.json").write_text(json.dumps(row))
    p = subprocess.run([sys.executable, str(CHECK), "--root", str(tmp_path), "--allow", str(d)],
                       capture_output=True, text=True)
    assert p.returncode == 2


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          capture_output=True, text=True)


def test_parallel_branches_removing_different_rows_merge_without_conflict(tmp_path: Path):
    """並列の 2 つの枝が別の項目を消しても git merge は衝突しない。消し忘れは unused-allow で落ちる。"""
    repo = tmp_path / "repo"
    rows = [{"path": f"plugins/ndf/scripts/{n}.py", "name": "", "kind": "lines", "reason": "分ける前", "lines": 501}
            for n in ("big1", "big2", "big3")]
    for n in ("big1", "big2", "big3"):
        put(repo, f"scripts/{n}.py", "x = 1\n" * 501)
    allow = write_allow(repo / "scripts" / "script-structure-allow", rows)
    assert git(repo, "init", "-q", "-b", "base").returncode == 0
    git(repo, "add", "-A")
    assert git(repo, "commit", "-qm", "base").returncode == 0
    for branch, n in (("a", "big1"), ("b", "big2")):
        git(repo, "checkout", "-q", "-b", branch, "base")
        put(repo, f"scripts/{n}.py", "x = 1\n" * 10)
        (allow / structure.allow_file_name(rows[int(n[-1]) - 1])).unlink()
        git(repo, "add", "-A")
        assert git(repo, "commit", "-qm", branch).returncode == 0
    git(repo, "checkout", "-q", "base")
    assert git(repo, "merge", "-q", "--no-edit", "a").returncode == 0
    m = git(repo, "merge", "-q", "--no-edit", "b")
    assert m.returncode == 0, m.stdout + m.stderr
    code, r = run_repo(repo)
    assert code == 0, r
    put(repo, "scripts/big3.py", "x = 1\n" * 10)
    code, r = run_repo(repo)
    assert code == 1
    assert kinds(r) == {("unused-allow", "plugins/ndf/scripts/big3.py")}
    assert structure.allow_file_name(rows[2]) in r["items"][0]["detail"]


def run_repo(repo: Path) -> tuple[int, dict]:
    """既定の置き場（<root>/scripts/script-structure-allow/）で走らせる。"""
    p = subprocess.run([sys.executable, str(CHECK), "--root", str(repo)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


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
