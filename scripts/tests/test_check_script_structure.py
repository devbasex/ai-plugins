"""構造チェック（`scripts/check-script-structure.py`）の振る舞いを固定する（#1142 の決定 20・23。#3304 の後の形）。

一時ディレクトリへ作った根の `scripts/` と `plugins/*/hooks/` の木に対して打つ。実物の木は、例外リストと合っているかだけを見る。
"""

from __future__ import annotations

import importlib.util
import json
import re
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
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def kinds(result: dict) -> set[tuple[str, str]]:
    return {(i["kind"], i["name"]) for i in result["items"]}


WRAPPERS_PY = (
    'GROUPS = {"md": ["markdown_it"], "mdtable": ["tabulate"], "durable": ["dbos", "filelock"], "locks": ["filelock"],'
    ' "procs": ["psutil"]}\n'
)
MAIN = '\nif __name__ == "__main__":\n    main()\n'
REQ = "from ndf_wrappers import require\n"
UV_HOOK = '{"hooks": {"Stop": [{"hooks": [{"command": "uv run python x.py"}]}]}}\n'


def test_clean_tree_is_ok(tmp_path: Path):
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(tmp_path, "scripts/a.py", REQ + "\nrequire('md')\nimport markdown_it\n" + MAIN)
    code, r = run(tmp_path, [])
    assert code == 0
    assert r["tool"] == "check-script-structure" and r["status"] == "ok" and r["items"] == []


def test_the_plugin_tree_is_left_to_its_own_check(tmp_path: Path):
    """NDF の木（plugins/ndf/）の行数・関数の重複・依存は NDF の検査（ndf check）が見る。根のチェックは数えない。"""
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(tmp_path, "plugins/ndf/src/ndf/a.py", "def now():\n    return 1\n" + "x = 1\n" * 1200)
    put(tmp_path, "plugins/ndf/src/ndf/b.py", "def now():\n    return 2\n")
    put(tmp_path, "plugins/ndf/src/ndf/c.py", "import psutil\n" + MAIN)
    assert run(tmp_path, [])[0] == 0


def test_hook_commands_must_not_use_uv_run(tmp_path: Path):
    """決定 20: hook の command は uv run を挟まない（用意済みの環境の python を直に起動する）。"""
    put(tmp_path, "plugins/ndf/hooks/claude.json", UV_HOOK)
    put(tmp_path, "plugins/other/hooks/codex.json", '{"hooks": {"Stop": [{"hooks": [{"command": "python3 x.py"}]}]}}\n')
    code, r = run(tmp_path, [])
    assert code == 1 and kinds(r) == {("hook-deps", "plugins/ndf/hooks/claude.json:uv run")}
    row = {"path": "plugins/ndf/hooks/claude.json", "name": "uv run", "kind": "hook-deps", "reason": "#1 で外す"}
    assert run(tmp_path, [row])[0] == 0


def test_unused_allow_row_fails(tmp_path: Path):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    row = {"path": "scripts/a.py", "name": "md", "kind": "require-groups", "reason": "#1 で直す"}
    code, r = run(tmp_path, [row])
    assert code == 1
    assert kinds(r) == {("unused-allow", "scripts/a.py:md")}


@pytest.mark.parametrize(
    "row",
    [
        {"path": "scripts/a.py", "name": "md", "kind": "require-groups"},
        {"path": "scripts/a.py", "name": "md", "kind": "require-groups", "reason": ""},
        {"path": "scripts/a.py", "name": "f", "kind": "same-name", "reason": "x"},
        {"path": "scripts/a.py", "name": "", "kind": "lines", "reason": "x", "lines": 600},
        {"path": "scripts/a.py", "name": "md", "kind": "require-groups", "reason": "x", "lines": 600},
    ],
)
def test_broken_allow_row_is_a_usage_error(tmp_path: Path, row: dict):
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    code, r = run(tmp_path, [row])
    assert code == 2 and r["status"] == "stopped"


def test_allow_file_name_must_match_the_row(tmp_path: Path):
    """ファイル名は path・name・kind から一意に決まる。合わない名前は形の誤り。"""
    put(tmp_path, "scripts/a.py", "def f():\n    return 1\n")
    row = {"path": "scripts/a.py", "name": "md", "kind": "require-groups", "reason": "x"}
    assert structure.allow_file_name(row) == "scripts__a.py--md--require-groups.json"
    d = tmp_path / "allow"
    d.mkdir()
    (d / "other.json").write_text(json.dumps(row))
    p = subprocess.run([sys.executable, str(CHECK), "--root", str(tmp_path), "--allow", str(d)], capture_output=True, text=True)
    assert p.returncode == 2


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], capture_output=True, text=True
    )


def run_repo(repo: Path) -> tuple[int, dict]:
    """既定の置き場（<root>/scripts/script-structure-allow/）で走らせる。"""
    p = subprocess.run([sys.executable, str(CHECK), "--root", str(repo)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_parallel_branches_removing_different_rows_merge_without_conflict(tmp_path: Path):
    """並列の 2 つの枝が別の項目を消しても git merge は衝突しない。消し忘れは unused-allow で落ちる。"""
    repo = tmp_path / "repo"
    names = ("p1", "p2", "p3")
    rows = [{"path": f"plugins/{n}/hooks/h.json", "name": "uv run", "kind": "hook-deps", "reason": "#1 で外す"} for n in names]
    for n in names:
        put(repo, f"plugins/{n}/hooks/h.json", UV_HOOK)
    allow = write_allow(repo / "scripts" / "script-structure-allow", rows)
    assert git(repo, "init", "-q", "-b", "base").returncode == 0
    git(repo, "add", "-A")
    assert git(repo, "commit", "-qm", "base").returncode == 0
    for branch, i in (("a", 0), ("b", 1)):
        git(repo, "checkout", "-q", "-b", branch, "base")
        put(repo, f"plugins/{names[i]}/hooks/h.json", "{}\n")
        (allow / structure.allow_file_name(rows[i])).unlink()
        git(repo, "add", "-A")
        assert git(repo, "commit", "-qm", branch).returncode == 0
    git(repo, "checkout", "-q", "base")
    assert git(repo, "merge", "-q", "--no-edit", "a").returncode == 0
    m = git(repo, "merge", "-q", "--no-edit", "b")
    assert m.returncode == 0, m.stdout + m.stderr
    code, r = run_repo(repo)
    assert code == 0, r
    put(repo, "plugins/p3/hooks/h.json", "{}\n")
    code, r = run_repo(repo)
    assert code == 1
    assert kinds(r) == {("unused-allow", "plugins/p3/hooks/h.json:uv run")}
    assert structure.allow_file_name(rows[2]) in r["items"][0]["detail"]


def test_structure_baseline_runs_on_a_given_root(tmp_path: Path):
    put(tmp_path, "plugins/ndf/scripts/a.py", "def f():\n    return 1\n")
    put(tmp_path, "plugins/ndf/scripts/b.py", "def f():\n    return 1\n")
    p = subprocess.run([sys.executable, str(BASELINE), str(tmp_path / "plugins" / "ndf")], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "files 2 / lines 4" in p.stdout
    assert "defined in 2+ files: 1 (with identical bodies somewhere: 1)" in p.stdout


def test_claude_p_usage_takes_paths_as_arguments():
    p = subprocess.run([sys.executable, str(CLAUDE_P_USAGE), "--help"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    for opt in ("--repo", "--sv-root", "--projects", "--out"):
        assert opt in p.stdout


# --- require-groups: エントリポイントから届くグループを require が並べる（決定 23） ---------------------------------


def rg(r: dict) -> set[str]:
    """require-groups の違反（`<パス>:<グループ>`。パスは scripts/ からの相対）。"""
    return {i["name"].removeprefix("scripts/") for i in r["items"] if i["kind"] == "require-groups"}


def test_require_must_list_groups_reached_through_imports(tmp_path: Path):
    """エントリポイント → パッケージ → lib を経て md に届く形で、require に md が無ければ落ちる。"""
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(tmp_path, "scripts/lib/md.py", "from markdown_it import MarkdownIt\n")
    put(tmp_path, "scripts/lib/mvv.py", "import md\n")
    put(tmp_path, "scripts/sup_lib/__init__.py", "")
    put(tmp_path, "scripts/sup_lib/state.py", "import mvv\n")
    put(tmp_path, "scripts/sup_lib/table.py", "import tabulate\n")
    put(tmp_path, "scripts/sup_lib/cmds.py", "from . import state\nfrom .table import x\n")
    entry = REQ + "\nrequire({})\nfrom sup_lib import cmds\n" + MAIN
    put(tmp_path, "scripts/sup.py", entry.format('"mdtable"'))
    put(
        tmp_path, "scripts/lock.py", "import ndf_wrappers\n\nndf_wrappers.require('durable')\nimport filelock\n" + MAIN
    )  # 持つグループのどれかでよい
    put(tmp_path, "scripts/plain.py", "import md\n")  # __main__ の文を持たないモジュールはエントリポイントでない
    put(tmp_path, "scripts/tests/test_x.py", "import md\n" + MAIN)  # テストは見ない
    code, r = run(tmp_path, [])
    assert code == 1
    assert kinds(r) == {("require-groups", "scripts/sup.py:md")}
    put(tmp_path, "scripts/sup.py", entry.format('"md", "mdtable"'))
    code, r = run(tmp_path, [])
    assert code == 0, r


def lazy_llm_tree(tmp_path: Path, call_body: str) -> None:
    """require を呼ばないエントリポイント → 関数の中の import → procs → psutil。"""
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(tmp_path, "scripts/lib/procs.py", "import psutil\n")
    put(tmp_path, "scripts/lib/claude.py", "import procs\n")
    put(tmp_path, "scripts/llm_lib/__init__.py", "")
    put(tmp_path, "scripts/llm_lib/llm.py", f"def call():\n{call_body}")
    put(tmp_path, "scripts/mvv.py", "from llm_lib import llm\n\ndef main():\n    llm.call()\n" + MAIN)


def test_a_late_import_reaching_a_group_without_require_is_a_violation(tmp_path: Path):
    """関数の中の import から届くグループも、覆う require が無ければ落ち、届いた名前と書いたモジュールが出る。"""
    lazy_llm_tree(tmp_path, "    import claude\n")
    code, r = run(tmp_path, [])
    assert code == 1 and rg(r) == {"mvv.py:procs"}
    assert r["items"][0]["detail"] == "psutil（scripts/lib/procs.py）へ届くのに require() にグループ procs が無い"
    lazy_llm_tree(tmp_path, "    " + REQ + "\n    require('procs')\n    import claude\n")  # 呼ばれる側の宣言
    code, r = run(tmp_path, [])
    assert code == 0, r
    lazy_llm_tree(tmp_path, "    import claude\n    " + REQ + "\n    require('procs')\n")  # import の後の require は覆わない
    assert rg(run(tmp_path, [])[1]) == {"mvv.py:procs"}


@pytest.mark.parametrize(
    "body, bad",
    [
        pytest.param("    try:\n        import procs\n    except ImportError:\n        return\n", False, id="except-import-error"),
        pytest.param("    try:\n        import procs\n    except (ImportError, OSError):\n        return\n", False, id="except-tuple"),
        pytest.param("    if importlib.util.find_spec('psutil'):\n        import procs\n", False, id="if-find-spec"),
        pytest.param("    if ndf_wrappers.importable('procs') and x:\n        import procs\n", False, id="if-importable-and"),
        pytest.param("    if x or find_spec('psutil') is None:\n        return\n    import procs\n", False, id="return-when-missing"),
        pytest.param(
            "    if not ndf_wrappers.importable('procs'):\n        raise SystemExit(0)\n    import procs\n", False, id="raise-when-missing"
        ),
        pytest.param("    print(find_spec('psutil'))\n    import procs\n", True, id="check-without-branch"),
        pytest.param("    if find_spec('psutil'):\n        pass\n    import procs\n", True, id="after-the-branch"),
        pytest.param("    if find_spec('psutil') is None:\n        print(1)\n    import procs\n", True, id="no-return"),
    ],
)
def test_optional_imports_are_not_violations(tmp_path: Path, body: str, bad: bool):
    """無ければ代わりの手段へ落ちる import は違反にしない。確かめた後に枝を通らず読む import は違反。"""
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(tmp_path, "scripts/lib/procs.py", "import psutil\n")
    put(
        tmp_path,
        "scripts/opt.py",
        "import ndf_wrappers\nimport importlib.util\nfrom importlib.util import find_spec\n\ndef main():\n" + body + MAIN,
    )
    assert rg(run(tmp_path, [])[1]) == ({"opt.py:procs"} if bad else set())


@pytest.mark.parametrize(
    "rel, text, bad",
    [
        pytest.param(
            "scripts/other_fn.py",
            REQ + "\ndef f():\n    require('procs')\n\ndef main():\n    import procs\n",
            True,
            id="another-function",
        ),
        pytest.param(
            "scripts/lib/mon.py",
            REQ + "\ndef main():\n    require('procs')\n    run()\n\ndef run():\n    import procs\n",
            False,
            id="main-covers-functions",
        ),
        pytest.param("scripts/top.py", REQ + "import procs\n\ndef main():\n    require('procs')\n", True, id="main-does-not-cover-load"),
        pytest.param("scripts/before.py", REQ + "\ndef main():\n    import procs\n    require('procs')\n", True, id="main-before-require"),
        pytest.param(
            "scripts/under_if.py",
            REQ + "\ndef main():\n    if x:\n        require('procs')\n\ndef g():\n    import procs\n",
            True,
            id="main-under-if",
        ),
        pytest.param(
            "scripts/mod_top.py",
            REQ + "\nrequire('procs')\n\ndef main():\n    import procs\n",
            False,
            id="module-top-covers-functions",
        ),
    ],
)
def test_require_covers_only_its_own_reach(tmp_path: Path, rel: str, text: str, bad: bool):
    """関数の require は同じ関数の後だけ、main の本体の最上位の require はエントリポイントとして見るときの遅れた依存だけを覆う。"""
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(tmp_path, "scripts/lib/procs.py", "import psutil\n")
    put(tmp_path, rel, text + MAIN)
    assert rg(run(tmp_path, [])[1]) == ({rel.removeprefix("scripts/") + ":procs"} if bad else set())


def test_main_require_of_an_imported_module_does_not_cover_its_reader(tmp_path: Path):
    """ほかのモジュールから読まれたとき main は走らないため、その require は読む側を覆わない。"""
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(tmp_path, "scripts/lib/procs.py", "import psutil\n")
    put(tmp_path, "scripts/lib/mon.py", REQ + "\ndef main():\n    require('procs')\n\ndef run():\n    import procs\n" + MAIN)
    put(tmp_path, "scripts/user.py", "import mon\n\ndef main():\n    mon.run()\n" + MAIN)
    assert rg(run(tmp_path, [])[1]) == {"user.py:procs"}


def test_without_the_group_table_require_groups_is_not_checked(tmp_path: Path):
    """グループの表（scripts/lib/ndf_wrappers.py の GROUPS）が無い木は require-groups を見ない。"""
    put(tmp_path, "scripts/a.py", "import psutil\n" + MAIN)
    assert run(tmp_path, [])[0] == 0
    put(tmp_path, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    assert rg(run(tmp_path, [])[1]) == {"a.py:procs"}


def test_require_groups_allow_rows_name_their_issue():
    """require-groups の例外リストの項目は、reason に課題の番号を持つ。"""
    rows = [json.loads(f.read_text()) for f in (REPO / "scripts" / "script-structure-allow").glob("*--require-groups.json")]
    assert all(re.search(r"#\d+", r["reason"]) for r in rows), [r["path"] for r in rows if not re.search(r"#\d+", r["reason"])]


# ---------- ファイルの指定（#1668。項目の範囲テストの静的解析の suite） ----------


def scoped_tree(root: Path) -> None:
    """require-groups の違反 2 件と、違反の無いファイルを持つ木。"""
    put(root, "scripts/lib/ndf_wrappers.py", WRAPPERS_PY)
    put(root, "scripts/a.py", "import psutil\n" + MAIN)
    put(root, "scripts/b.py", "import tabulate\n" + MAIN)
    put(root, "scripts/clean.py", "def clean():\n    return 3\n" + MAIN)


def test_files_narrow_items_and_exit_code_to_the_given_files(tmp_path: Path):
    scoped_tree(tmp_path)
    code, r = run(tmp_path, [], "scripts/clean.py")
    assert code == 0 and r["status"] == "ok" and r["items"] == []
    assert r["metrics"]["targets"] == 1 and r["metrics"]["outside"] == 2
    code, r = run(tmp_path, [], str(tmp_path / "scripts/a.py"))
    assert code == 1 and kinds(r) == {("require-groups", "scripts/a.py:procs")}


def test_allow_rows_count_by_their_path_or_their_file(tmp_path: Path):
    """使われない例外の行は、行の path か、行のファイル自身を指定したときに数える。"""
    row = {"path": "scripts/gone.py", "name": "md", "kind": "require-groups", "reason": "#1 で直す"}
    put(tmp_path, "scripts/clean.py", "x = 1\n")
    assert run(tmp_path, [row], "scripts/clean.py")[0] == 0
    assert run(tmp_path, [row], "scripts/gone.py")[0] == 1
    code, r = run(tmp_path, [row], "allow/" + structure.allow_file_name(row))
    assert code == 1 and kinds(r) == {("unused-allow", "scripts/gone.py:md")}


@pytest.mark.parametrize("scope", ["scripts/check-script-structure.py", "scripts/lib/ndf_wrappers.py"])
def test_giving_the_check_itself_counts_every_violation(tmp_path: Path, scope: str):
    scoped_tree(tmp_path)
    full = run(tmp_path, [])[1]
    code, r = run(tmp_path, [], scope)
    assert code == 1 and r["items"] == full["items"] and len(r["items"]) == 2


def test_without_files_the_metrics_keep_their_keys(tmp_path: Path):
    scoped_tree(tmp_path)
    code, r = run(tmp_path, [])
    assert code == 1 and len(r["items"]) == 2
    assert "targets" not in r["metrics"] and "outside" not in r["metrics"]


def test_broken_allow_list_stops_even_with_files(tmp_path: Path):
    put(tmp_path, "scripts/clean.py", "x = 1\n")
    (tmp_path / "allow").mkdir()
    (tmp_path / "allow" / "x.json").write_text("{")
    code, _ = run(tmp_path, None, "--allow", str(tmp_path / "allow"), "scripts/clean.py")
    assert code == 2
