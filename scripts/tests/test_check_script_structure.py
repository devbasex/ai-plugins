"""構造チェック（`scripts/check-script-structure.py`）の振る舞いを固定する（#1142 の I4・I5・I14）。

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
    assert kinds(r) == {("same-body", "plugins/ndf/scripts/a.py:repo_slug"), ("same-body", "plugins/ndf/skills/x/scripts/b.py:_repo_slug")}


def test_same_name_with_different_bodies(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def now():\n    return 1\n")
    put(tmp_path, "scripts/b.py", "def now():\n    return 2\n")
    code, r = run(tmp_path, [])
    assert code == 1
    assert kinds(r) == {("same-name", "plugins/ndf/scripts/a.py:now"), ("same-name", "plugins/ndf/scripts/b.py:now")}
    allow = [{"path": f"plugins/ndf/scripts/{n}.py", "name": "now", "kind": "same-name", "reason": "L0 で統合"} for n in ("a", "b")]
    assert run(tmp_path, allow)[0] == 0


def test_rule_excluded_names_and_tests_are_not_counted(tmp_path: Path):
    body = (
        "def main():\n    return 0\n\ndef build_parser():\n    return 0\n\ndef _build_parser():\n    return 0\n\n"
        "def cmd_run(a):\n    return 0\n"
    )
    put(tmp_path, "scripts/a.py", body)
    put(tmp_path, "scripts/b.py", body)
    put(tmp_path, "scripts/a.sh", "usage() {\n  echo a\n}\n")
    put(tmp_path, "scripts/b.sh", "usage() {\n  echo a\n}\n")
    put(tmp_path, "scripts/tests/test_x.py", "def helper():\n    return 0\n" + "x = 1\n" * 501)
    put(tmp_path, "scripts/tests/helpers.py", "def helper():\n    return 0\n")
    code, r = run(tmp_path, [])
    assert code == 0 and r["items"] == []


def test_shell_functions_are_compared_by_normalized_lines(tmp_path: Path):
    put(tmp_path, "scripts/lib/a.sh", 'resolve_print_timeout() {\n  local t=1\n\n  # 注\n  echo "$t"\n}\n')
    put(tmp_path, "skills/x/scripts/b.sh", 'function resolve_print_timeout {\n    local t=1\n    echo "$t"\n}\n')
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


@pytest.mark.parametrize(
    "row",
    [
        {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name"},
        {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name", "reason": ""},
        {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "other", "reason": "x"},
        {"path": "plugins/ndf/scripts/a.py", "name": "", "kind": "lines", "reason": "x"},
        {"path": "plugins/ndf/scripts/a.py", "name": "", "kind": "lines", "reason": "x", "lines": "600"},
        {"path": "plugins/ndf/scripts/a.py", "name": "f", "kind": "same-name", "reason": "x", "lines": 600},
    ],
)
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
    assert structure.allow_file_name({"path": "plugins/ndf/x.sh", "name": "", "kind": "lines"}) == "plugins__ndf__x.sh--lines.json"
    d = tmp_path / "allow"
    d.mkdir()
    (d / "other.json").write_text(json.dumps(row))
    p = subprocess.run([sys.executable, str(CHECK), "--root", str(tmp_path), "--allow", str(d)], capture_output=True, text=True)
    assert p.returncode == 2


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], capture_output=True, text=True
    )


def test_parallel_branches_removing_different_rows_merge_without_conflict(tmp_path: Path):
    """並列の 2 つの枝が別の項目を消しても git merge は衝突しない。消し忘れは unused-allow で落ちる。"""
    repo = tmp_path / "repo"
    rows = [
        {"path": f"plugins/ndf/scripts/{n}.py", "name": "", "kind": "lines", "reason": "分ける前", "lines": 501}
        for n in ("big1", "big2", "big3")
    ]
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
    p = subprocess.run([sys.executable, str(BASELINE), str(tmp_path / "plugins" / "ndf")], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "files 2 / lines 4" in p.stdout
    assert "defined in 2+ files: 1 (with identical bodies somewhere: 1)" in p.stdout


def test_claude_p_usage_takes_paths_as_arguments():
    p = subprocess.run([sys.executable, str(CLAUDE_P_USAGE), "--help"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    for opt in ("--repo", "--sv-root", "--projects", "--out"):
        assert opt in p.stdout


# --- I14: 包みが受け持つ部品を、包みの外で使わない（決定 19） ---------------------------------


def test_wrapped_parts_outside_their_wrapper_fail(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "import fcntl\nfrom urllib import request\n")
    put(tmp_path, "scripts/b.py", "import os\nP = f'/proc/{os.getpid()}/stat'\n")
    put(tmp_path, "scripts/c.py", "import re\nF = re.compile(r'^\\s*(```|~~~)')\n")
    put(tmp_path, "scripts/d.py", "def f(s):\n    return s.startswith(('```', '~~~'))\n")
    put(tmp_path, "scripts/e.py", "import termios\nimport pty\nfrom urllib.request import urlopen\n")
    put(tmp_path, "scripts/supervise_lib/flow.py", "from dbos import DBOS\n")
    put(tmp_path, "scripts/lib/post_queue.py", "import dbos._sys_db\n")
    code, r = run(tmp_path, [])
    assert code == 1
    assert {k for k in kinds(r) if k[0] == "wrapped"} == {
        ("wrapped", "plugins/ndf/scripts/a.py:fcntl"),
        ("wrapped", "plugins/ndf/scripts/a.py:urllib.request"),
        ("wrapped", "plugins/ndf/scripts/b.py:proc-fs"),
        ("wrapped", "plugins/ndf/scripts/c.py:fence-regex"),
        ("wrapped", "plugins/ndf/scripts/d.py:fence-regex"),
        ("wrapped", "plugins/ndf/scripts/e.py:termios"),
        ("wrapped", "plugins/ndf/scripts/e.py:pty"),
        ("wrapped", "plugins/ndf/scripts/e.py:urllib.request"),
        ("wrapped", "plugins/ndf/scripts/supervise_lib/flow.py:dbos"),
        ("wrapped", "plugins/ndf/scripts/lib/post_queue.py:dbos"),
    }


def test_wrappers_may_use_their_parts_and_mentions_do_not_count(tmp_path: Path):
    put(tmp_path, "scripts/lib/locks.py", "import fcntl\n")
    put(tmp_path, "scripts/lib/procs.py", "P = '/proc/self/cgroup'\n")
    put(tmp_path, "scripts/lib/md.py", "import re\nF = re.compile('```')\n")
    put(tmp_path, "scripts/lib/notify.py", "import urllib.request\n")
    put(tmp_path, "scripts/relay_lib/terminal.py", "import pty\nimport termios\n")
    put(tmp_path, "scripts/lib/durable.py", "from dbos import DBOS, DBOSClient\n")
    # docstring の言及・囲みを書く側の文字列・urllib.parse は数えない
    put(
        tmp_path,
        "scripts/a.py",
        '"""/proc/ は procs が読む。"""\nimport urllib.parse\n'
        'def f(b):\n    """```text の囲みを書く。"""\n    return f"```text\\n{b}\\n```"\n',
    )
    code, r = run(tmp_path, [])
    assert code == 0, r["items"]


def test_wrapped_part_can_be_allowed_per_file_and_part(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "import fcntl\n")
    row = {"path": "plugins/ndf/scripts/a.py", "name": "fcntl", "kind": "wrapped", "reason": "D3 が置き換えたら消す"}
    assert structure.allow_file_name(row) == "plugins__ndf__scripts__a.py--fcntl--wrapped.json"
    code, r = run(tmp_path, [row])
    assert code == 0 and r["metrics"]["allowed"] == 1
    put(tmp_path, "scripts/a.py", "import os\n")
    code, r = run(tmp_path, [row])
    assert code == 1 and kinds(r) == {("unused-allow", "plugins/ndf/scripts/a.py:fcntl")}


def test_hook_path_must_not_use_deps(tmp_path: Path):
    """I13（決定 20）: hook.py から import でたどれるモジュールは deps.require() を呼ばず、hook の command は uv run を挟まない。"""
    put(tmp_path, "scripts/hook.py", "import helper\nfrom hook_lib import guard\n")
    put(tmp_path, "scripts/lib/helper.py", "def f():\n    import deps\n    deps.require('x')\n")
    put(tmp_path, "scripts/hook_lib/__init__.py", "")
    put(tmp_path, "scripts/hook_lib/guard.py", "from . import inner\nfrom . import guard2\n")
    put(tmp_path, "scripts/hook_lib/inner.py", "from deps import require\n")
    put(tmp_path, "scripts/hook_lib/plain.py", "import deps\nV = deps.venv_dir()\n")  # require を呼ばなければよい
    put(tmp_path, "scripts/hook_lib/guard2.py", "from . import plain\n")
    put(tmp_path, "scripts/other.py", "import deps\ndeps.require('x')\n")  # hook の経路でない
    put(tmp_path, "hooks/claude.json", '{"hooks": {"Stop": [{"hooks": [{"command": "uv run python x.py"}]}]}}\n')
    code, r = run(tmp_path, [])
    assert code == 1
    assert kinds(r) == {
        ("hook-deps", "plugins/ndf/scripts/lib/helper.py:deps.require"),
        ("hook-deps", "plugins/ndf/scripts/hook_lib/inner.py:deps.require"),
        ("hook-deps", "plugins/ndf/hooks/claude.json:uv run"),
    }


DEPS_PY = 'GROUPS = {"md": ["markdown_it"], "mdtable": ["tabulate"], "durable": ["dbos", "filelock"], "locks": ["filelock"]}\n'


def test_require_must_list_groups_reached_through_imports(tmp_path: Path):
    """決定 23: supervise.py の形（エントリポイント → パッケージ → lib を経て md に届く）で、require に md が無ければ落ちる。"""
    put(tmp_path, "scripts/lib/deps.py", DEPS_PY)
    put(tmp_path, "scripts/lib/md.py", "from markdown_it import MarkdownIt\n")
    put(tmp_path, "scripts/lib/mvv.py", "import md\n")
    put(tmp_path, "scripts/sup_lib/__init__.py", "")
    put(tmp_path, "scripts/sup_lib/state.py", "import mvv\n")
    put(tmp_path, "scripts/sup_lib/table.py", "import tabulate\n")
    put(tmp_path, "scripts/sup_lib/cmds.py", "from . import state\nfrom .table import x\n")
    entry = "import deps\n\ndeps.require({})\nfrom sup_lib import cmds\n"
    put(tmp_path, "scripts/sup.py", entry.format('"mdtable"'))
    # 関数の中の import と except ImportError で受ける import は読み込み時に届かない
    put(
        tmp_path,
        "scripts/lazy.py",
        "import deps\n\ndeps.require('mdtable')\ntry:\n    import markdown_it\nexcept ImportError:\n    pass\n\ndef f():\n    import md\n",
    )
    put(tmp_path, "scripts/lock.py", "import deps\n\ndeps.require('durable')\nimport filelock\n")  # 同じ名前は持つグループのどれかでよい
    put(tmp_path, "scripts/plain.py", "import md\n")  # require を呼ばないモジュールは見ない
    code, r = run(tmp_path, [])
    assert code == 1
    assert kinds(r) == {("require-groups", "plugins/ndf/scripts/sup.py:md")}
    put(tmp_path, "scripts/sup.py", entry.format('"md", "mdtable"'))
    code, r = run(tmp_path, [])
    assert code == 0, r


GOOD_BOUNDARY = {
    "scripts/supervise_lib/flow.py": "import durable\n\n@durable.workflow(name='w')\ndef plan_workflow():\n    for s in range(3):\n        pass\n",
    "scripts/supervise_lib/engine.py": (
        "class Engine:\n    def run(self, start=None):\n        from supervise_lib import flow\n\n        return flow.run_engine(self, start)\n"
    ),
    "scripts/supervise_lib/state.py": (
        "import json\n\nclass RunState:\n    def record(self):\n        data = {'log': self.log, 'llm': self.llm}\n"
        "        if self.project_mvv is not None:\n            data['project_mvv'] = 1\n"
        "        (self.dir / 'state.json').write_text(json.dumps(data))\n"
    ),
    "scripts/supervise_lib/queue.py": "import time\n\ndef cmd_queue(a):\n    from supervise_lib import flow\n\n    return flow.plan_workflow()\n",
    "scripts/lib/post_queue.py": (
        "import durable\n\nclass Queue:\n    def put(self):\n        return durable.workflow(name='n')(lambda r: r)\n\n"
        "    def _import_legacy(self):\n        return sorted(self.dir.glob('*.json'))\n"
    ),
    "skills/cross-review/scripts/drive.py": (
        "import durable\n\nclass Drive:\n    def run(self):\n        return durable.start(1)\n\n"
        "@durable.workflow(name='review')\ndef review_drive():\n    while True:\n        break\n"
    ),
    "skills/cross-refactoring/scripts/drive.py": (
        "import durable\n\nclass Drive:\n    def run(self):\n        return 1\n\n    def phases(self):\n        return 1\n\n"
        "    def final_gate(self):\n        return [x for x in range(2)]\n\n"
        "@durable.workflow(name='refactor_drive')\ndef refactor_drive():\n    for x in range(2):\n        pass\n"
    ),
}


def boundary_tree(root: Path, **broken: str) -> set[tuple[str, str]]:
    """I15 の置き場の最小の木を書き、broken（相対パスの `/` を `__` にした名前 → 中身）で差し替えて検査する。"""
    for rel, text in GOOD_BOUNDARY.items():
        put(root, rel, broken.get(rel.replace("/", "__").replace("-", "_").removesuffix(".py"), text))
    return {(v["path"].removeprefix("plugins/ndf/"), v["function"]) for v in structure.durable_boundary(root)}


def test_durable_boundary_passes_the_minimal_tree_and_skips_trees_without_it(tmp_path: Path):
    """I15: ループは耐久ワークフローの中だけ。置き場を 1 つも持たない木は見ない。"""
    assert structure.durable_boundary(tmp_path) == []
    assert boundary_tree(tmp_path) == set()


@pytest.mark.parametrize(
    "name, text, hit",
    [
        (
            "scripts__supervise_lib__engine",
            "class Engine:\n    def run(self, start=None):\n        from supervise_lib import flow\n        while True:\n            flow.step()\n",
            ("scripts/supervise_lib/engine.py", "Engine.run"),
        ),
        (
            "scripts__supervise_lib__state",
            "import json\n\nclass RunState:\n    def record(self):\n        data = {'log': 1}\n        data['next'] = 3\n"
            "        (self.dir / 'state.json').write_text(json.dumps(data))\n",
            ("scripts/supervise_lib/state.py", "RunState.record"),
        ),
        (
            "scripts__supervise_lib__queue",
            "import subprocess\nfrom supervise_lib import flow\n\ndef run_batch(ps):\n    return [p for p in ps]\n",
            ("scripts/supervise_lib/queue.py", ""),
        ),
        (
            "scripts__supervise_lib__queue",
            "import subprocess\nfrom supervise_lib import flow\n\ndef go(p):\n    return p.poll()\n",
            ("scripts/supervise_lib/queue.py", ""),
        ),
        (
            "scripts__lib__post_queue",
            "import durable, os\n\nclass Queue:\n    def put(self):\n        durable.workflow(name='n')(lambda r: r)\n"
            "        return os.open('x', os.O_CREAT)\n",
            ("scripts/lib/post_queue.py", "Queue"),
        ),
        (
            "skills__cross_review__scripts__drive",
            "import durable\n\nclass Drive:\n    def run(self):\n        for r in range(3):\n            pass\n\n"
            "@durable.workflow(name='review')\ndef review_drive():\n    pass\n",
            ("skills/cross-review/scripts/drive.py", "Drive.run"),
        ),
        (
            "skills__cross_refactoring__scripts__drive",
            "import durable\n\nclass Drive:\n    def run(self):\n        return 1\n\n    def phases(self):\n        return 1\n\n"
            "    def final_gate(self):\n        return 1\n\n    def save_ds(self):\n        pass\n\n"
            "@durable.workflow(name='refactor_drive')\ndef refactor_drive():\n    pass\n",
            ("skills/cross-refactoring/scripts/drive.py", "Drive"),
        ),
        (
            "skills__cross_refactoring__scripts__drive",
            "import durable\n\nclass Drive:\n    def run(self):\n        return 1\n\n    def final_gate(self):\n        return 1\n",
            ("skills/cross-refactoring/scripts/drive.py", "Drive.phases"),
        ),
    ],
)
def test_durable_boundary_catches_loops_and_file_state_brought_back(tmp_path: Path, name: str, text: str, hit: tuple[str, str]):
    """I15（C-1）: 置き場へ遷移のループ・state.json の再開位置・子プロセス・ファイルの待ち行列・drive の状態ファイルを戻すと落ちる。"""
    assert hit in boundary_tree(tmp_path, **{name: text})


def test_durable_boundary_needs_a_workflow_not_only_the_missing_loop(tmp_path: Path):
    """I15: ループを消して耐久ワークフローも置かない壊し方を落とす（import した先に有れば通る）。"""
    no_flow = "class Engine:\n    def run(self, start=None):\n        return None\n"
    got = boundary_tree(
        tmp_path,
        scripts__supervise_lib__engine=no_flow,
        skills__cross_review__scripts__drive="import durable\n\nclass Drive:\n    def run(self):\n        return 1\n",
    )
    assert got == {
        ("scripts/supervise_lib/engine.py", "durable.workflow"),
        ("skills/cross-review/scripts/drive.py", "durable.workflow"),
    }
    (tmp_path / "plugins/ndf/scripts/supervise_lib/engine.py").unlink()
    assert ("scripts/supervise_lib/engine.py", "") in {
        (v["path"].removeprefix("plugins/ndf/"), v["function"]) for v in structure.durable_boundary(tmp_path)
    }


# ---------- ファイルの指定（#1668。項目の範囲テストの静的解析の suite） ----------


def scoped_tree(root: Path) -> None:
    """行数の違反 1 件と同じ名前の違反 1 組と、違反の無いファイルを持つ木。"""
    put(root, "scripts/big.py", "x = 1\n" * 501)
    put(root, "scripts/lib/participants.py", "def setup():\n    return 1\n")
    put(root, "skills/x/scripts/setup.py", "def setup():\n    return 2\n")
    put(root, "scripts/clean.py", "def clean():\n    return 3\n")


def test_files_narrow_items_and_exit_code_to_the_given_files(tmp_path: Path):
    scoped_tree(tmp_path)
    code, r = run(tmp_path, [], "plugins/ndf/scripts/clean.py")
    assert code == 0 and r["status"] == "ok" and r["items"] == []
    assert r["metrics"]["targets"] == 1 and r["metrics"]["outside"] == 3
    code, r = run(tmp_path, [], "plugins/ndf/scripts/big.py")
    assert code == 1 and kinds(r) == {("lines", "plugins/ndf/scripts/big.py")}


def test_same_name_counts_when_either_file_is_given(tmp_path: Path):
    """#1634 の `participants.py` を指定すれば、ほかのファイルとの衝突が両側とも出る。"""
    scoped_tree(tmp_path)
    both = {
        ("same-name", "plugins/ndf/scripts/lib/participants.py:setup"),
        ("same-name", "plugins/ndf/skills/x/scripts/setup.py:setup"),
    }
    for given in ("plugins/ndf/scripts/lib/participants.py", "plugins/ndf/skills/x/scripts/setup.py"):
        code, r = run(tmp_path, [], given)
        assert code == 1 and kinds(r) == both


def test_same_body_counts_when_the_other_file_is_given(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    put(tmp_path, "scripts/b.py", "def g():\n    return 1\n")
    code, r = run(tmp_path, [], str(tmp_path / "plugins/ndf/scripts/b.py"))
    assert code == 1 and {k for k, _ in kinds(r)} == {"same-body"} and len(r["items"]) == 2


def test_lines_over_the_allow_row_count_only_for_the_given_file(tmp_path: Path):
    row = {"path": "plugins/ndf/scripts/big.py", "name": "", "kind": "lines", "reason": "分ける前", "lines": 600}
    put(tmp_path, "scripts/big.py", "x = 1\n" * 601)
    put(tmp_path, "scripts/clean.py", "x = 1\n")
    assert run(tmp_path, [row], "plugins/ndf/scripts/clean.py")[0] == 0
    code, r = run(tmp_path, [row], "plugins/ndf/scripts/big.py")
    assert code == 1 and kinds(r) == {("lines", "plugins/ndf/scripts/big.py")}


def test_allow_rows_count_by_their_path_or_their_file(tmp_path: Path):
    """使われない例外の行は、行の path か、行のファイル自身を指定したときに数える。"""
    row = {"path": "plugins/ndf/scripts/gone.py", "name": "f", "kind": "same-body", "reason": "移す前"}
    put(tmp_path, "scripts/clean.py", "x = 1\n")
    assert run(tmp_path, [row], "plugins/ndf/scripts/clean.py")[0] == 0
    assert run(tmp_path, [row], "plugins/ndf/scripts/gone.py")[0] == 1
    code, r = run(tmp_path, [row], "allow/" + structure.allow_file_name(row))
    assert code == 1 and kinds(r) == {("unused-allow", "plugins/ndf/scripts/gone.py:f")}


def test_giving_the_check_itself_counts_every_violation(tmp_path: Path):
    scoped_tree(tmp_path)
    full = run(tmp_path, [])[1]
    code, r = run(tmp_path, [], "scripts/check-script-structure.py")
    assert code == 1 and r["items"] == full["items"]


def test_without_files_the_metrics_keep_their_keys(tmp_path: Path):
    scoped_tree(tmp_path)
    code, r = run(tmp_path, [])
    assert code == 1 and len(r["items"]) == 3
    assert "targets" not in r["metrics"] and "outside" not in r["metrics"]


def test_broken_allow_list_stops_even_with_files(tmp_path: Path):
    put(tmp_path, "scripts/clean.py", "x = 1\n")
    (tmp_path / "allow").mkdir()
    (tmp_path / "allow" / "x.json").write_text("{")
    code, _ = run(tmp_path, None, "--allow", str(tmp_path / "allow"), "plugins/ndf/scripts/clean.py")
    assert code == 2
