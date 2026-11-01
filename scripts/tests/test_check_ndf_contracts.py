"""契約の構造チェック（`scripts/check-ndf-contracts.py`）の振る舞いを固定する（#2336）。

一時ディレクトリへ作った小さな木に、違反を 1 つずつ入れて落ちることと、外すと通ることを確かめる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-ndf-contracts.py"
sys.path.insert(0, str(REPO / "scripts" / "lib"))
from allowlist import allow_file_name  # noqa: E402

TOOL = '''"""道具の要約。"""
import argparse
import os
import sys
from pathlib import Path

import repo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    a = ap.parse_args()
    v = os.environ.get("NDF_X")
    decl = repo.read_worktree_decl(a.root)[0]
    base = decl.get("base_branch")
    Path("out.json").write_text("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
REPO_LIB = """import json
from pathlib import Path


def read_worktree_decl(root):
    return json.loads((Path(root) / ".ndf" / "worktree.json").read_text()), None
"""
ENTRY = {
    "version": 1,
    "path": "plugins/ndf/scripts/tool.py",
    "summary": "道具の要約。",
    "callers": ["plugins/ndf/skills/a/SKILL.md"],
    "args": {"options": ["--root"], "positionals": []},
    "env": ["NDF_X"],
    "settings": [{"file": "worktree.json", "keys": ["base_branch"]}],
    "stdin": "none",
    "stdout": "none",
    "writes": ["out"],
    "reads": [],
    "exit": {"0": "成功", "2": "引数の誤り"},
}
SETTING = {
    "file": "worktree.json",
    "summary": "宣言",
    "place": "worktree",
    "scope": "team",
    "readers": ["plugins/ndf/scripts/lib/repo.py:read_worktree_decl"],
    "keys": {"base_branch": {"summary": "ベースブランチ"}},
}


def wj(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")


def env_clean() -> dict:
    return {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_BASE_REF")}


def run(root: Path, *extra: str, env: dict | None = None) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(CHECK), "--root", str(root), *extra], capture_output=True, text=True, env=env or env_clean())
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip().startswith("{") else {}


def kinds(r: dict) -> list[tuple[str, str]]:
    return [(i["kind"], i["name"]) for i in r["items"]]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    s = tmp_path / "plugins/ndf/scripts"
    (s / "lib").mkdir(parents=True)
    (s / "tool.py").write_text(TOOL)
    (s / "lib/repo.py").write_text(REPO_LIB)
    (tmp_path / "plugins/ndf/skills/a").mkdir(parents=True)
    (tmp_path / "plugins/ndf/skills/a/SKILL.md").write_text("python3 scripts/tool.py --root .\n")
    c = tmp_path / "plugins/ndf/contracts"
    wj(c / "entrypoints/plugins__ndf__scripts__tool.py.json", ENTRY)
    wj(c / "settings/worktree.json.json", SETTING)
    wj(c / "env/NDF_X.json", {"name": "NDF_X", "set_by": "user", "summary": "x"})
    wj(c / "files/out.json", {"name": "out", "pattern": "out.json", "kind": "result", "place": "worktree", "skill_readers": []})
    assert run(tmp_path, "--render")[0] == 0
    return tmp_path


def edit(root: Path, old: str, new: str) -> None:
    f = root / "plugins/ndf/scripts/tool.py"
    f.write_text(f.read_text().replace(old, new))


def test_clean_tree_passes_and_counts_entrypoints(tree: Path):
    (tree / "plugins/ndf/scripts/lib_only.py").write_text("X = 1\n")
    code, r = run(tree)
    assert code == 0, r
    assert r["metrics"]["entrypoints"] == 1 and r["metrics"]["candidates"] == 2
    assert r["metrics"]["ratchet"] == "skipped"


def test_undeclared_entrypoint_fails_with_a_draft(tree: Path):
    (tree / "plugins/ndf/scripts/new.py").write_text('import sys\nif __name__ == "__main__":\n    sys.exit(0)\n')
    code, r = run(tree)
    assert code == 1
    item = next(i for i in r["items"] if i["kind"] == "entry-undeclared")
    assert item["path"] == "plugins/ndf/scripts/new.py" and '"path": "plugins/ndf/scripts/new.py"' in item["detail"]
    code, out = (
        0,
        subprocess.run(
            [sys.executable, str(CHECK), "--root", str(tree), "--draft", "plugins/ndf/scripts/new.py"], capture_output=True, text=True
        ),
    )
    assert out.returncode == 0 and json.loads(out.stdout)["path"] == "plugins/ndf/scripts/new.py"


def test_draft_rejects_a_non_entrypoint(tree: Path):
    assert run(tree, "--draft", "plugins/ndf/scripts/lib/repo.py")[0] == 2


@pytest.mark.parametrize(
    "old,new,kind,name",
    [
        ('ap.add_argument("--root")', 'ap.add_argument("--root")\n    ap.add_argument("--new")', "arg-undeclared", "--new"),
        ('os.environ.get("NDF_X")', 'os.environ.get("NDF_X") or os.getenv("NDF_Y")', "env-undeclared", "NDF_Y"),
        ('decl.get("base_branch")', 'decl.get("base_branch") or decl.get("zzz")', "setting-undeclared", "worktree.json#zzz"),
        (
            'Path("out.json").write_text("{}")',
            'Path("out.json").write_text("{}")\n    open("other.json", "w")',
            "file-undeclared",
            "other.json",
        ),
    ],
)
def test_reads_and_writes_outside_the_contract_fail(tree: Path, old, new, kind, name):
    edit(tree, old, new)
    code, r = run(tree)
    assert code == 1
    assert (kind, name) in kinds(r)
    assert next(i for i in r["items"] if i["kind"] == kind)["path"] == "plugins/ndf/scripts/tool.py"


@pytest.mark.parametrize(
    "old,new,name",
    [
        ('ap.add_argument("--root")', 'ap.add_argument("--rootx")', "arg:--root"),
        ('v = os.environ.get("NDF_X")', "v = None", "env:NDF_X"),
        ('base = decl.get("base_branch")', "base = None", "setting:worktree.json#base_branch"),
        ('Path("out.json").write_text("{}")', "pass", "write:out"),
    ],
)
def test_contract_entries_the_code_no_longer_has_are_stale(tree: Path, old, new, name):
    edit(tree, old, new)
    code, r = run(tree)
    assert code == 1 and ("contract-stale", name) in kinds(r)


def test_setting_opened_outside_its_reader_fails(tree: Path):
    edit(tree, "def main():", 'def peek(root):\n    return (Path(root) / ".ndf" / "worktree.json").read_text()\n\n\ndef main():')
    code, r = run(tree)
    assert ("setting-open-outside-reader", "worktree.json") in kinds(r)


def test_duplicate_setting_needs_an_allow_item(tree: Path):
    s = dict(SETTING, keys={"base_branch": {"summary": "x", "duplicates": "project.json#base"}})
    wj(tree / "plugins/ndf/contracts/settings/worktree.json.json", s)
    run(tree, "--render")
    code, r = run(tree)
    assert ("setting-duplicate", "worktree.json#base_branch") in kinds(r)


def test_callers_must_exist_and_name_the_entrypoint(tree: Path):
    (tree / "plugins/ndf/skills/a/SKILL.md").write_text("nothing\n")
    code, r = run(tree)
    assert ("caller-stale", "plugins/ndf/skills/a/SKILL.md") in kinds(r)
    wj(tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool.py.json", dict(ENTRY, callers=[]))
    run(tree, "--render")
    assert ("no-caller", "") in kinds(run(tree)[1])


def test_index_must_match_the_items(tree: Path):
    wj(tree / "plugins/ndf/contracts/env/NDF_X.json", {"name": "NDF_X", "set_by": "ci", "summary": "x"})
    code, r = run(tree)
    assert code == 1 and [i["kind"] for i in r["items"]] == ["index-stale"]


def test_allow_items_cover_violations_and_unused_ones_fail(tree: Path):
    edit(tree, 'os.environ.get("NDF_X")', 'os.environ.get("NDF_X") or os.getenv("NDF_Y")')
    allow = tree / "scripts/contract-allow"
    row = {"path": "plugins/ndf/scripts/tool.py", "name": "NDF_Y", "kind": "env-undeclared", "reason": "初版"}
    wj(allow / allow_file_name(row), row)
    assert run(tree)[0] == 0
    edit(tree, ' or os.getenv("NDF_Y")', "")
    code, r = run(tree)
    assert code == 1 and ("unused-allow", "NDF_Y") in kinds(r)
    (allow / allow_file_name(row)).unlink()
    assert run(tree)[0] == 0


def test_malformed_item_stops_with_2(tree: Path):
    wj(tree / "plugins/ndf/contracts/env/NDF_X.json", {"name": "NDF_X"})
    assert run(tree)[0] == 2


def git(root: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)


def _repo(tree: Path, ci_runs_check: bool) -> None:
    wf = tree / ".github/workflows/script-structure.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text("run: python3 scripts/check-ndf-contracts.py\n" if ci_runs_check else "run: python3 scripts/other.py\n")
    git(tree, "init", "-q")
    git(tree, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base", "--allow-empty")
    git(tree, "add", "-A")
    git(tree, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base")
    git(tree, "update-ref", "refs/remotes/origin/develop", "HEAD")


def _add_allowed_violation(tree: Path, name: str = "NDF_Y") -> None:
    edit(tree, 'os.environ.get("NDF_X")', f'os.environ.get("NDF_X") or os.getenv("{name}")')
    row = {"path": "plugins/ndf/scripts/tool.py", "name": name, "kind": "env-undeclared", "reason": "初版"}
    wj(tree / "scripts/contract-allow" / allow_file_name(row), row)


def test_adding_an_allow_item_fails_against_the_merge_base(tree: Path):
    _repo(tree, ci_runs_check=True)
    _add_allowed_violation(tree)
    code, r = run(tree, "--base", "origin/develop")
    assert code == 1 and r["metrics"]["ratchet"] == "checked"
    assert ("allow-added", "NDF_Y") in kinds(r)


def test_renaming_an_allow_item_with_the_same_kind_and_name_passes(tree: Path):
    _add_allowed_violation(tree)
    _repo(tree, ci_runs_check=True)
    old = {"path": "plugins/ndf/scripts/tool.py", "name": "NDF_Y", "kind": "env-undeclared", "reason": "初版"}
    (tree / "scripts/contract-allow" / allow_file_name(old)).unlink()
    (tree / "plugins/ndf/scripts/tool.py").rename(tree / "plugins/ndf/scripts/tool2.py")
    for f in (tree / "plugins/ndf/contracts/entrypoints").glob("*.json"):
        f.unlink()
    wj(tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool2.py.json", dict(ENTRY, path="plugins/ndf/scripts/tool2.py"))
    (tree / "plugins/ndf/skills/a/SKILL.md").write_text("python3 scripts/tool2.py\n")
    row = dict(old, path="plugins/ndf/scripts/tool2.py")
    wj(tree / "scripts/contract-allow" / allow_file_name(row), row)
    run(tree, "--render")
    code, r = run(tree, "--base", "origin/develop")
    assert code == 0, r


def test_first_version_before_ci_runs_the_check_is_initial(tree: Path):
    (tree / "scripts").mkdir(exist_ok=True)
    (tree / "scripts/check-ndf-contracts.py").write_text("# exists at the merge base\n")
    _repo(tree, ci_runs_check=False)
    _add_allowed_violation(tree)
    code, r = run(tree, "--base", "origin/develop")
    assert code == 0 and r["metrics"]["ratchet"] == "initial"


def test_ci_without_a_base_stops_with_3(tree: Path):
    code, r = run(tree, env=dict(env_clean(), CI="true"))
    assert code == 3


def test_this_repository_matches_its_contracts():
    code, r = run(REPO)
    assert code == 0, r["items"][:5]


# --- Checker.entry の分岐ごとの現状固定（I-001） ---


def _entry(tree: Path, **kw) -> None:
    wj(tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool.py.json", dict(ENTRY, **kw))
    assert run(tree, "--render")[0] == 0


def _item(r: dict, kind: str) -> dict:
    return next(i for i in r["items"] if i["kind"] == kind)


def test_entry_manual_argv_is_args_unparsed(tree: Path):
    edit(tree, '    ap = argparse.ArgumentParser()\n    ap.add_argument("--root")\n    a = ap.parse_args()\n', "    a = sys.argv[1:]\n")
    edit(tree, "repo.read_worktree_decl(a.root)", "repo.read_worktree_decl(a[0])")
    code, r = run(tree)
    assert code == 1
    item = _item(r, "args-unparsed")
    assert (item["path"], item["name"]) == ("plugins/ndf/scripts/tool.py", "")
    assert ("contract-stale", "arg:--root") in kinds(r)


def test_entry_unparsed_contract_skips_arg_comparison_but_still_reports(tree: Path):
    _entry(tree, args={"unparsed": "手で読む"})
    code, r = run(tree)
    assert code == 1
    assert kinds(r) == [("args-unparsed", "")]


def test_entry_positional_args_are_compared(tree: Path):
    edit(tree, 'ap.add_argument("--root")', 'ap.add_argument("--root")\n    ap.add_argument("target")')
    code, r = run(tree)
    assert code == 1 and ("arg-undeclared", "target") in kinds(r)
    assert "positional" in _item(r, "arg-undeclared")["detail"]
    _entry(tree, args={"options": ["--root"], "positionals": ["target"]})
    assert run(tree)[0] == 0


def test_entry_env_in_contract_without_env_item_points_to_env_dir(tree: Path):
    (tree / "plugins/ndf/contracts/env/NDF_X.json").unlink()
    run(tree, "--render")
    code, r = run(tree)
    assert code == 1
    item = _item(r, "env-undeclared")
    assert item["name"] == "NDF_X"
    assert "plugins/ndf/contracts/env/NDF_X.json" in item["detail"]


def test_entry_env_missing_from_entry_points_to_entry_file(tree: Path):
    edit(tree, 'os.environ.get("NDF_X")', 'os.environ.get("NDF_X") or os.getenv("NDF_Y")')
    item = _item(run(tree)[1], "env-undeclared")
    assert item["name"] == "NDF_Y"
    assert "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool.py.json の env" in item["detail"]


def test_entry_dynamic_env_read_is_reported(tree: Path):
    edit(tree, 'v = os.environ.get("NDF_X")', 'v = os.environ.get("NDF_X")\n    w = os.environ.get("NDF_" + str(v))')
    code, r = run(tree)
    assert code == 1
    item = _item(r, "env-dynamic")
    assert item["path"] == "plugins/ndf/scripts/tool.py" and item["name"] == "main"


def test_entry_reads_without_file_item_is_stale(tree: Path):
    _entry(tree, reads=["nothing"])
    code, r = run(tree)
    assert code == 1 and kinds(r) == [("contract-stale", "read:nothing")]


def test_entry_reads_with_file_item_passes(tree: Path):
    _entry(tree, reads=["out"])
    assert run(tree)[0] == 0


def test_entry_setting_without_settings_item_is_stale(tree: Path):
    _entry(tree, settings=[{"file": "worktree.json", "keys": ["base_branch"]}, {"file": "nope.json", "keys": []}])
    code, r = run(tree)
    assert code == 1 and ("contract-stale", "setting:nope.json") in kinds(r)


def test_entry_write_whose_file_item_is_missing_is_stale(tree: Path):
    _entry(tree, writes=["out", "ghost"])
    code, r = run(tree)
    assert code == 1 and kinds(r) == [("contract-stale", "write:ghost")]


def test_entry_write_matches_a_glob_pattern(tree: Path):
    wj(
        tree / "plugins/ndf/contracts/files/out.json",
        {"name": "out", "pattern": "*.json", "kind": "result", "place": "worktree", "skill_readers": []},
    )
    assert run(tree, "--render")[0] == 0
    assert run(tree)[0] == 0


def test_entry_exit_unknown_is_reported_with_its_reason(tree: Path):
    _entry(tree, exit={"unknown": "読み切れない"})
    code, r = run(tree)
    assert code == 1
    item = _item(r, "exit-unknown")
    assert (item["name"], item["detail"]) == ("", "読み切れない")


def test_entry_external_contract_has_no_caller_check(tree: Path):
    e = {k: val for k, val in ENTRY.items() if k != "callers"}
    wj(tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool.py.json", dict(e, external="手で呼ぶ"))
    assert run(tree, "--render")[0] == 0
    (tree / "plugins/ndf/skills/a/SKILL.md").write_text("nothing\n")
    assert run(tree)[0] == 0


def test_entry_reports_every_kind_at_once(tree: Path):
    edit(tree, 'ap.add_argument("--root")', 'ap.add_argument("--new")')
    edit(tree, 'os.environ.get("NDF_X")', 'os.getenv("NDF_Y")')
    edit(tree, 'decl.get("base_branch")', 'decl.get("zzz")')
    edit(tree, 'Path("out.json").write_text("{}")', 'open("other.json", "w")')
    _entry(tree, reads=["nothing"], exit={"unknown": "不明"})
    (tree / "plugins/ndf/skills/a/SKILL.md").write_text("nothing\n")
    code, r = run(tree)
    assert code == 1
    assert sorted(kinds(r)) == sorted(
        [
            ("arg-undeclared", "--new"),
            ("contract-stale", "arg:--root"),
            ("env-undeclared", "NDF_Y"),
            ("contract-stale", "env:NDF_X"),
            ("setting-undeclared", "worktree.json#zzz"),
            ("contract-stale", "setting:worktree.json#base_branch"),
            ("file-undeclared", "other.json"),
            ("contract-stale", "write:out"),
            ("contract-stale", "read:nothing"),
            ("exit-unknown", ""),
            ("caller-stale", "plugins/ndf/skills/a/SKILL.md"),
        ]
    )


# --- _Scan.visit_Call: 呼び出しの形ごとに拾う事実（現状固定） ---

sys.path.insert(0, str(REPO / "scripts"))
from contracts_lib.extract_py import ModFacts, _Scan  # noqa: E402


def _scan_call(src: str, settings: dict | None = None, readers: dict | None = None, stdout: str = "none") -> ModFacts:
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(src))
    f = ModFacts(rel="m.py", stdout=stdout)
    f.constants = {"NAME": "NDF_C", "OUT": "c.json", "CODE": 4}
    _Scan(f, settings or {}, readers or {}).visit(tree)
    return f


@pytest.mark.parametrize(
    "src, env, dynamic",
    [
        ("os.environ.get('NDF_A')", {"NDF_A": 1}, set()),
        ("environ.get(NAME)", {"NDF_C": 1}, set()),
        ("os.getenv('NDF_B', 'x')", {"NDF_B": 1}, set()),
        ("def f(n):\n    return os.getenv(n)", {}, {"f"}),
        ("os.environ.setdefault('NDF_S', '1')", {}, set()),
        ("os.environ.get()", {}, set()),
    ],
)
def test_visit_call_env_reads(src, env, dynamic):
    f = _scan_call(src)
    assert f.env == env
    assert f.env_dynamic == dynamic


def test_visit_call_get_on_reader_variable_is_a_setting_key():
    src = "def g():\n    d = read_decl()\n    d.get('base_branch')\n    other.get('x')\n"
    f = _scan_call(src, settings={"worktree.json": {}}, readers={"lib.py:read_decl": ["worktree.json"]})
    assert f.setting_keys == {("worktree.json", "base_branch"): 3}
    assert f.env == {}


def test_visit_call_add_argument_collects_options_and_positionals():
    src = (
        "p = argparse.ArgumentParser()\n"
        "s = p.add_subparsers()\n"
        "r = s.add_parser('run')\n"
        "p.add_argument('--root', '-r')\n"
        "r.add_argument('target')\n"
        "r.add_argument('a', 'b')\n"
        "p.add_argument(x)\n"
    )
    f = _scan_call(src)
    assert f.has_parser is True
    assert f.options == {("", "--root"), ("", "-r")}
    assert f.positionals == {("run", "target"), ("run", "a")}


def test_visit_call_add_argument_without_strings_still_marks_parser():
    f = _scan_call("p.add_argument(x)")
    assert f.has_parser is True
    assert f.options == set() and f.positionals == set()


@pytest.mark.parametrize(
    "src, writes",
    [
        ("open('a.json', 'w')", {"a.json": 1}),
        ("open('a.json', mode='a')", {"a.json": 1}),
        ("open('a.json', 'x')", {"a.json": 1}),
        ("open('a.json')", {}),
        ("open('a.json', 'r')", {}),
        ("open('a.json', mode='rb')", {}),
        ("io.open('a.json', 'w')", {}),
        ("Path('d/b.md').write_text('x')", {"b.md": 1}),
        ("Path(OUT).write_bytes(b'')", {"c.json": 1}),
        ("write_atomic(f'runs/{n}-x.json', {})", {"*-x.json": 1}),
        ("append_jsonl('log.jsonl', {})", {"log.jsonl": 1}),
        ("write_atomic()", {}),
        ("open('state.json', 'w')", {}),
    ],
)
def test_visit_call_writes(src, writes):
    f = _scan_call(src, settings={"state.json": {}})
    assert f.writes == writes


@pytest.mark.parametrize(
    "src, exits",
    [
        ("sys.exit(3)", {"3": ""}),
        ("raise SystemExit(5)", {"5": ""}),
        ("sys.exit(CODE)", {"4": "CODE"}),
        ("exit(2)", {}),
        ("other.exit(2)", {}),
        ("sys.exit()", {}),
        ("sys.exit(True)", {}),
        ("raise StepError('m', 6)", {"6": ""}),
        ("raise StepError('m')", {}),
    ],
)
def test_visit_call_exits(src, exits):
    assert _scan_call(src).exits == exits


@pytest.mark.parametrize(
    "src, before, after",
    [
        ("print('x')", "none", "text"),
        ("print('x')", "json", "json"),
        ("json.dumps({})", "none", "json"),
        ("json.dumps({})", "text", "json"),
        ("json.dumps({})", "step-result", "step-result"),
        ("other.dumps({})", "none", "none"),
        ("print(json.dumps({}))", "none", "json"),
        ("emit({})", "json", "step-result"),
        ("emit_result({})", "none", "step-result"),
        ("emit({})\nprint('x')", "none", "step-result"),
    ],
)
def test_visit_call_stdout_kind(src, before, after):
    assert _scan_call(src, stdout=before).stdout == after


def test_visit_call_visits_nested_calls():
    f = _scan_call("sys.exit(os.getenv('NDF_N'))")
    assert f.env == {"NDF_N": 1}
    assert f.exits == {}
