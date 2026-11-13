"""契約の構造チェック（`scripts/check-ndf-contracts.py`）の振る舞いを固定する（#2336）。

一時ディレクトリへ作った小さな木に、違反を 1 つずつ入れて落ちることと、外すと通ることを確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
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
import run_core


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
    run_core.main("scripts/tool.py", main)
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
    "exit": {"0": "成功", "2": "読めない・呼び出しの誤り"},
}
SETTING = {
    "file": "worktree.json",
    "summary": "宣言",
    "place": "worktree",
    "scope": "team",
    "readers": ["plugins/ndf/scripts/lib/repo.py:read_worktree_decl"],
    "keys": {"base_branch": {"summary": "ベースブランチ"}},
}


LIMITS = {"version": 1, "policies": {"monitor.cli-margin": {"summary": "CLI の上限の差", "seconds": 120}}}


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
    wj(c / "limits.json", LIMITS)
    shutil.copy(REPO / "plugins/ndf/scripts/lib/limits.py", s / "lib/limits.py")
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


def test_key_place_main_is_accepted_and_other_values_stop_with_2(tree: Path):
    """認可に関わるキーはキーごとの place でメインディレクトリから読むと宣言できる（#2363）。"""
    f = tree / "plugins/ndf/contracts/settings/worktree.json.json"
    wj(f, dict(SETTING, keys={"base_branch": {"summary": "ベースブランチ", "place": "main"}}))
    assert run(tree, "--render")[0] == 0
    assert run(tree)[0] == 0
    wj(f, dict(SETTING, keys={"base_branch": {"summary": "ベースブランチ", "place": "tmp"}}))
    assert run(tree)[0] == 2


def git(root: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)


def _repo(tree: Path, ci_runs_check: bool, known=("env-undeclared",)) -> None:
    wf = tree / ".github/workflows/script-structure.yml"
    wf.parent.mkdir(parents=True)
    chk = tree / "scripts/check-ndf-contracts.py"
    if not chk.exists():
        chk.parent.mkdir(exist_ok=True)
        chk.write_text("ALLOW_KINDS = (" + "".join(f'"{k}", ' for k in known) + ")\n")
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
    t2 = tree / "plugins/ndf/scripts/tool2.py"
    t2.write_text(t2.read_text().replace('"scripts/tool.py"', '"scripts/tool2.py"'))
    for f in (tree / "plugins/ndf/contracts/entrypoints").glob("*.json"):
        f.unlink()
    wj(tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool2.py.json", dict(ENTRY, path="plugins/ndf/scripts/tool2.py"))
    (tree / "plugins/ndf/skills/a/SKILL.md").write_text("python3 scripts/tool2.py\n")
    row = dict(old, path="plugins/ndf/scripts/tool2.py")
    wj(tree / "scripts/contract-allow" / allow_file_name(row), row)
    run(tree, "--render")
    code, r = run(tree, "--base", "origin/develop")
    assert code == 0, r


def test_moving_two_allow_items_with_the_same_kind_and_name_offsets_both(monkeypatch):
    """同じ kind・name の項目を 2 つ同時に付け替えても、消した 2 件で足した 2 件を相殺する。"""
    sys.path.insert(0, str(REPO / "scripts"))
    from contracts_lib import ratchet

    rows = {f"{p}.json": {"kind": "env-undeclared", "name": "NDF_Y", "path": p} for p in ("a", "b", "c", "d")}
    monkeypatch.setattr(ratchet, "_names_at", lambda *a: {"a.json", "b.json"})
    monkeypatch.setattr(ratchet, "_row_at", lambda root, sha, rel, name: rows[name])
    monkeypatch.setattr(ratchet, "base_knows", lambda *a: True)
    now = [rows["c.json"], rows["d.json"]]
    assert ratchet.added(Path("."), "sha", "x", now, lambda r: f"{r['path']}.json") == []
    assert ratchet.added(Path("."), "sha", "x", [*now, dict(rows["c.json"], path="e")], lambda r: f"{r['path']}.json") != []


def test_rows_of_a_kind_the_base_checker_does_not_know_are_not_counted(tree: Path):
    """新しい検査の種類を入れる変更は、その種類の行を初めて置ける（#2337 の決定 6）。"""
    _repo(tree, ci_runs_check=True, known=())
    _add_allowed_violation(tree)
    code, r = run(tree, "--base", "origin/develop")
    assert code == 0 and r["metrics"]["ratchet"] == "checked", r


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


def test_entry_positional_args_keep_the_registered_order(tree: Path):
    """位置引数は登録した順で契約に載せる。並びが違えば、呼び出す側が逆に渡すため止める。"""
    edit(tree, 'ap.add_argument("--root")', 'ap.add_argument("--root")\n    ap.add_argument("target")\n    ap.add_argument("base")')
    _entry(tree, args={"options": ["--root"], "positionals": ["base", "target"]})
    code, r = run(tree)
    assert code == 1 and kinds(r) == [("contract-stale", "arg-order:")], r
    assert "target, base" in r["items"][0]["detail"]
    _entry(tree, args={"options": ["--root"], "positionals": ["target", "base"]})
    assert run(tree)[0] == 0
    code = subprocess.run(
        [sys.executable, str(CHECK), "--root", str(tree), "--draft", "plugins/ndf/scripts/tool.py"], capture_output=True, text=True
    )
    assert json.loads(code.stdout)["args"]["positionals"] == ["target", "base"]


def test_entry_manual_argv_ignores_an_imported_modules_parser(tree: Path):
    """入口が sys.argv を手で読むなら、import 先の別用途のパーサーの引数を入口の引数にしない。"""
    (tree / "plugins/ndf/scripts/acct.py").write_text(
        'import argparse\n\n\ndef parser():\n    p = argparse.ArgumentParser()\n    p.add_argument("--code")\n    return p\n'
    )
    edit(tree, '    ap = argparse.ArgumentParser()\n    ap.add_argument("--root")\n    a = ap.parse_args()\n', "    a = sys.argv[1:]\n")
    edit(tree, "repo.read_worktree_decl(a.root)", "repo.read_worktree_decl(a[0])")
    edit(tree, "import repo\n", "import acct\nimport repo\n")
    code, r = run(tree)
    assert code == 1
    assert ("args-unparsed", "") in kinds(r) and ("arg-undeclared", "--code") not in kinds(r), r


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


def test_scope_on_an_imported_library_counts_the_entrypoints_violation(tree: Path):
    """範囲がエントリポイントの import するモジュールだけでも、そこで増えた読みの違反で止まる。"""
    lib = tree / "plugins/ndf/scripts/lib/repo.py"
    lib.write_text(
        "import os\n" + REPO_LIB.replace("def read_worktree_decl(root):", 'def read_worktree_decl(root):\n    os.environ.get("NDF_Z")')
    )
    code, r = run(tree, "plugins/ndf/scripts/lib/repo.py")
    assert code == 1, r
    assert ("env-undeclared", "NDF_Z") in kinds(r)
    code, r = run(tree, "plugins/ndf/skills/a/SKILL.md")
    assert code == 0 and r["metrics"]["outside"] >= 1, r


@pytest.mark.parametrize(
    "scope",
    [
        "plugins/ndf/scripts",
        "./plugins/ndf/scripts",  # 先頭の ./ と末尾の / を除いてから照合する（#2363 round 6）
        "plugins/ndf/scripts/",
        "scripts/contracts_lib/extract_py.py",
        "scripts/lib/py_imports.py",
    ],
)
def test_scope_on_a_directory_or_the_checkers_parts_counts_the_violation(tree: Path, scope: str):
    """範囲がディレクトリでも、チェッカ自身の部品（scripts/contracts_lib/・scripts/lib/）でも、木の違反で止まる。"""
    edit(tree, 'os.environ.get("NDF_X")', 'os.environ.get("NDF_X") or os.getenv("NDF_Y")')
    code, r = run(tree, scope)
    assert code == 1 and ("env-undeclared", "NDF_Y") in kinds(r), r


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
    assert f.positionals == [("run", "target"), ("run", "a")]


def test_visit_call_nested_subcommand_keeps_its_parent():
    src = (
        "p = argparse.ArgumentParser()\n"
        "sub = p.add_subparsers()\n"
        "hp = sub.add_parser('history')\n"
        "hs = hp.add_subparsers()\n"
        "hi = hs.add_parser('import')\n"
        "hi.add_argument('progress')\n"
        "hi.add_argument('--history')\n"
    )
    f = _scan_call(src)
    assert f.positionals == [("history import", "progress")]
    assert f.options == {("history import", "--history")}
    assert not f.args_unresolved and not f.loose_args


@pytest.mark.parametrize(
    "src, unresolved, loose",
    [
        # 定数表の繰り返しで作る副命令
        ("s = p.add_subparsers()\nfor n in NAMES:\n    k = s.add_parser(n)\n    k.add_argument('--a')\n", True, True),
        # 補助関数の引数のパーサーへ足す引数
        ("s = p.add_subparsers()\ndef add(q):\n    q.add_argument('--root')\n", False, True),
    ],
)
def test_visit_call_marks_subcommands_it_cannot_resolve(src, unresolved, loose):
    f = _scan_call("p = argparse.ArgumentParser()\n" + src)
    assert (f.args_unresolved, f.loose_args, f.has_subparsers) == (unresolved, loose, True)


def test_visit_call_group_options_belong_to_their_subcommand():
    src = (
        "p = argparse.ArgumentParser()\n"
        "s = p.add_subparsers()\n"
        "r = s.add_parser('run')\n"
        "g = r.add_mutually_exclusive_group()\n"
        "g.add_argument('--a')\n"
    )
    f = _scan_call(src)
    assert f.options == {("run", "--a")} and not f.loose_args


def test_unresolved_subcommands_are_args_unparsed(tmp_path: Path):
    from contracts_lib.extract_py import PyExtractor

    (tmp_path / "t.py").write_text(
        "import argparse\n"
        "def main():\n"
        "    ap = argparse.ArgumentParser()\n"
        "    sub = ap.add_subparsers()\n"
        "    for n in ('a', 'b'):\n"
        "        sub.add_parser(n).add_argument('--x')\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    assert PyExtractor(tmp_path, {}).facts("t.py")["args_unresolved"] is True


def test_visit_call_add_argument_without_strings_still_marks_parser():
    f = _scan_call("p.add_argument(x)")
    assert f.has_parser is True
    assert f.options == set() and f.positionals == []


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


@pytest.mark.parametrize(
    ("body", "unresolved"),
    [
        ('PR="${1:?}"\nROUND="$2"\n', True),  # 位置引数を手で読む（launch-reviewer.sh の形。#2363 round 6）
        ('main "$@"\n', True),
        ('f() {\n  local x="$1"\n}\nf a\n', False),  # 関数の中の $1 はその関数の引数
        ('g() { echo "$1"; }\nn="${#ARR[@]}"\n', False),  # 1 行の関数と配列の長さ
        ('while [ $# -gt 0 ]; do\n  case "$1" in\n    --x) shift ;;\n  esac\ndone\n', False),  # case の --名前) は拾える
    ],
)
def test_shell_positionals_without_options_are_args_unparsed(tmp_path: Path, body: str, unresolved: bool):
    from contracts_lib.extract_sh import facts

    (tmp_path / "t.sh").write_text("#!/bin/bash\n" + body)
    assert facts(tmp_path, "t.sh", {})["args_unresolved"] is unresolved


@pytest.mark.parametrize(
    ("body", "unresolved"),
    [
        # --オプションに加えて副命令を読む（worktree-deps.sh の形。#2363 round 7）
        ('SUB="${1:-}"\nshift\nwhile [ $# -gt 0 ]; do\n  case "$1" in\n    --x) shift ;;\n  esac\ndone\n', True),
        # case の --名前) 以外の腕で位置引数を受ける
        ('while [ $# -gt 0 ]; do\n  case "$1" in\n    --x) shift ;;\n    *) PR="$1"; shift ;;\n  esac\ndone\n', True),
        # --名前) の腕の中で値を読むだけなら、オプションだけを受ける
        (
            'while [ $# -gt 0 ]; do\n  case "$1" in\n    --x)\n      X="$2"\n      shift 2\n      ;;\n    *) exit 2 ;;\n  esac\ndone\n',
            False,
        ),
    ],
)
def test_shell_positionals_beside_options_are_args_unparsed(tmp_path: Path, body: str, unresolved: bool):
    from contracts_lib.extract_sh import facts

    (tmp_path / "t.sh").write_text("#!/bin/bash\n" + body)
    assert facts(tmp_path, "t.sh", {})["args_unresolved"] is unresolved


@pytest.mark.parametrize(
    "src, dynamic",
    [
        ("def main(a):\n    Path(a.output).write_text('x')\n", {"main"}),
        ("def main(a):\n    open(a.output, 'w')\n", {"main"}),
        ("def main(a):\n    Path('d/b.md').write_text('x')\n", set()),
        ("def main(a):\n    open(a.output)\n", set()),
    ],
)
def test_visit_call_write_without_a_file_name_is_file_dynamic(src, dynamic):
    assert _scan_call(src).file_dynamic == dynamic


def test_entry_write_without_a_file_name_is_reported(tree: Path):
    edit(tree, 'Path("out.json").write_text("{}")', 'Path("out.json").write_text("{}")\n    Path(a.root).write_text("")')
    code, r = run(tree)
    assert code == 1
    item = _item(r, "file-dynamic")
    assert item["path"] == "plugins/ndf/scripts/tool.py" and item["name"] == "main"


def test_imports_resolve_from_the_scripts_dir_and_relative_levels(tmp_path: Path):
    """パッケージの奥のモジュールが `from ..paths import x`・`import pkg.y` で読む先もたどる（#2363 round 7）。"""
    from contracts_lib.extract_py import PyExtractor

    s = tmp_path / "plugins/ndf/skills/k/scripts"
    (s / "pkg/commands").mkdir(parents=True)
    (s / "e.py").write_text("from pkg.commands import setup\nif __name__ == '__main__':\n    setup.run()\n")
    (s / "pkg/__init__.py").write_text("")
    (s / "pkg/commands/__init__.py").write_text("")
    (s / "pkg/commands/setup.py").write_text("from ..paths import P\nimport pkg.other\ndef run():\n    return P\n")
    (s / "pkg/paths.py").write_text("import os\nP = os.environ.get('NDF_REL')\n")
    (s / "pkg/other.py").write_text("import os\nQ = os.getenv('NDF_ABS')\n")
    env = PyExtractor(tmp_path, {}).facts("plugins/ndf/skills/k/scripts/e.py")["env"]
    assert {"NDF_REL", "NDF_ABS"} <= set(env), env


FORM = {
    "name": "f",
    "summary": "s",
    "rule": "r",
    "writers": ["a"],
    "readers": ["b"],
    "cases": [{"text": "t", "blocks": [], "launch": False}],
}


def test_form_items_are_checked_and_unknown_keys_stop_with_2(tree: Path):
    """受け渡しの形（forms/。#2347 の決定 8）は鍵と例の組の型を確かめ、知らない鍵を断る。"""
    f = tree / "plugins/ndf/contracts/forms/f.json"
    wj(f, FORM)
    assert run(tree)[0] == 0
    wj(f, dict(FORM, extra=1))
    assert run(tree)[0] == 2
    wj(f, dict(FORM, cases=[{"text": "t", "blocks": [1], "launch": False}]))
    assert run(tree)[0] == 2
    wj(f, dict(FORM, cases=[{"message": {"kind": "request"}, "valid": True}]))
    assert run(tree)[0] == 0
    wj(f, dict(FORM, cases=[{"message": "m", "valid": True}]))
    assert run(tree)[0] == 2


# --- model.load: 区分ごとの読み込みと形の検査（現状固定） ---

from contracts_lib import model as _model  # noqa: E402

LOAD_ENTRY = {
    "version": 1,
    "path": "plugins/ndf/scripts/a.py",
    "summary": "s",
    "args": {"options": [], "positionals": []},
    "env": [],
    "settings": [],
    "stdin": "none",
    "stdout": "json",
    "writes": [],
    "reads": [],
    "exit": {"0": "ok"},
    "external": "x",
}
FILE_ITEM = {"name": "st", "pattern": "a/*.json", "kind": "state", "place": "worktree", "skill_readers": []}
SETTING_ITEM = {"file": "s.json", "place": "main", "scope": "team", "readers": ["r"], "keys": {"k": {"summary": "s"}}, "summary": "s"}
ENV_ITEM = {"name": "NDF_X", "set_by": "user", "summary": "s"}


def _load_one(tmp_path: Path, kind: str, fname: str, data) -> "_model.Contracts":
    d = tmp_path / _model.CONTRACTS / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return _model.load(tmp_path)


def test_load_empty_tree_returns_empty_contracts(tmp_path: Path):
    c = _model.load(tmp_path)
    assert (c.entrypoints, c.entry_files, c.files, c.settings, c.env, c.forms) == ({}, {}, {}, {}, {}, {})


def test_load_reads_every_kind_into_its_map(tmp_path: Path):
    _load_one(tmp_path, "entrypoints", _model.entry_file_name(LOAD_ENTRY["path"]), LOAD_ENTRY)
    _load_one(tmp_path, "files", "st.json", FILE_ITEM)
    _load_one(tmp_path, "settings", "s.json.json", SETTING_ITEM)
    _load_one(tmp_path, "env", "NDF_X.json", ENV_ITEM)
    c = _load_one(tmp_path, "forms", "f.json", FORM)
    assert c.entrypoints == {LOAD_ENTRY["path"]: LOAD_ENTRY}
    assert c.entry_files == {LOAD_ENTRY["path"]: "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__a.py.json"}
    assert c.files == {"st": FILE_ITEM}
    assert c.settings == {"s.json": SETTING_ITEM}
    assert c.env == {"NDF_X": ENV_ITEM}
    assert c.forms == {"f": FORM}


def test_load_ignores_non_json_files(tmp_path: Path):
    c = _load_one(tmp_path, "env", "NDF_X.txt", "not json")
    assert c.env == {}


@pytest.mark.parametrize(
    "kind, fname, data, message",
    [
        ("env", "NDF_X.json", "{", "契約の項目を読めない"),
        ("env", "NDF_X.json", "[]", "最上位がオブジェクトでない"),
        ("entrypoints", "wrong.json", LOAD_ENTRY, "ファイル名は plugins__ndf__scripts__a.py.json にする"),
        ("entrypoints", "plugins__ndf__scripts__a.py.json", dict(LOAD_ENTRY, callers=[]), "callers と external はどちらか 1 つ"),
        ("entrypoints", "plugins__ndf__scripts__a.py.json", dict(LOAD_ENTRY, stdin="bad"), "stdin は"),
        ("entrypoints", "plugins__ndf__scripts__a.py.json", dict(LOAD_ENTRY, exit={}), "exit は終了コード"),
        ("files", "st.json", dict(FILE_ITEM, kind="bad"), "kind は state/record/result/report のどれか: bad"),
        ("files", "st.json", dict(FILE_ITEM, place="bad"), "place は"),
        ("files", "other.json", FILE_ITEM, "ファイル名は st.json にする"),
        ("files", "st.json", {k: v for k, v in FILE_ITEM.items() if k != "pattern"}, "pattern が無い"),
        ("settings", "s.json.json", dict(SETTING_ITEM, scope="bad"), "scope は"),
        ("settings", "s.json.json", dict(SETTING_ITEM, readers=[1]), "readers は文字列の並び"),
        ("settings", "s.json.json", dict(SETTING_ITEM, keys={"k": "s"}), "keys.k は summary と"),
        ("settings", "s.json.json", dict(SETTING_ITEM, keys={"k": {"summary": "s", "x": 1}}), "keys.k は summary と"),
        (
            "settings",
            "s.json.json",
            dict(SETTING_ITEM, keys={"k": {"summary": "s", "place": "tmp"}}),
            "keys.k.place は worktree / main のどれか",
        ),
        ("settings", "other.json", SETTING_ITEM, "ファイル名は s.json.json にする"),
        ("env", "NDF_X.json", dict(ENV_ITEM, set_by="bad"), "set_by は"),
        ("env", "NDF_Y.json", ENV_ITEM, "ファイル名は NDF_X.json にする"),
        ("env", "NDF_X.json", dict(ENV_ITEM, summary=1), "summary の型が違う"),
        ("forms", "g.json", FORM, "ファイル名は f.json にする"),
        ("forms", "f.json", dict(FORM, cases=[]), "cases は 1 つ以上"),
        ("forms", "f.json", dict(FORM, cases=["x"]), "cases[0] はオブジェクト"),
    ],
)
def test_load_rejects_malformed_items_per_kind(tmp_path: Path, kind, fname, data, message):
    with pytest.raises(_model.ContractError) as e:
        _load_one(tmp_path, kind, fname, data)
    assert message in str(e.value)


# --- 形の宣言（shape）と区間の報告の例の組（#3242） ---

REPORT_FORM = json.loads((REPO / _model.CONTRACTS / "forms" / "unattended-report.json").read_text(encoding="utf-8"))


def _report_form(**shape) -> dict:
    return dict(REPORT_FORM, name="f", shape={**REPORT_FORM["shape"], **shape})


def test_the_unattended_report_form_and_shapeless_forms_load(tmp_path: Path):
    """プラグインの区間の報告の契約（shape つき）と、shape の無い既存の形をどちらも通す。"""
    c = _load_one(tmp_path, "forms", "f.json", _report_form())
    assert c.forms["f"]["shape"] == REPORT_FORM["shape"]
    assert _load_one(tmp_path, "forms", "f.json", FORM).forms == {"f": FORM}


def test_a_report_case_with_an_empty_when_loads(tmp_path: Path):
    """when が空の値（完了）と、区間の報告の例の組の形（text・result・errors）を通す。"""
    req = {**REPORT_FORM["shape"]["required"], "判断が要る": {"always": ["区分"], "when": []}}
    cases = [{"text": "- 結果: 完了\n", "result": "完了", "errors": []}]
    data = dict(_report_form(required=req), cases=cases)
    assert _load_one(tmp_path, "forms", "f.json", data).forms["f"]["cases"] == cases


@pytest.mark.parametrize(
    "data, message",
    [
        (_report_form(values=["完了"]), "values の型が違う"),
        (_report_form(values={}), "shape.values は値 → 意味"),
        (_report_form(items=[]), "shape.items は空でない"),
        (_report_form(material={"item": "材料", "body": "本文", "url_prefix": "https://"}), "shape.material は"),
        (_report_form(material={"item": "無い項目", "body": "本文", "heading": "## h", "url_prefix": "https://"}), "material.item は"),
        (_report_form(required={"完了": {"always": [], "when": []}}), "required のキーは values と同じ集合"),
        (
            _report_form(required={**REPORT_FORM["shape"]["required"], "完了": {"when": []}}),
            "required.完了 は always（並び）と when（並び）を持つ",
        ),
        (
            _report_form(required={**REPORT_FORM["shape"]["required"], "完了": {"always": ["無い項目"], "when": []}}),
            "required.完了.always は items の項目名",
        ),
        (
            _report_form(
                required={**REPORT_FORM["shape"]["required"], "完了": {"always": [], "when": [{"item": "区分", "need": ["理由"]}]}}
            ),
            "required.完了.when の要素は",
        ),
        (dict(_report_form(), shape=[]), "shape の型が違う"),
        (dict(_report_form(), cases=[{"text": "t", "result": "", "errors": [1]}]), "cases[0].errors は文字列の並び"),
    ],
)
def test_a_malformed_shape_or_report_case_is_refused(tmp_path: Path, data, message):
    with pytest.raises(_model.ContractError) as e:
        _load_one(tmp_path, "forms", "f.json", data)
    assert message in str(e.value)


def test_load_accepts_setting_key_extras_and_place(tmp_path: Path):
    keys = {"k": {"summary": "s", "migrate_from": "o", "duplicates": [], "place": "worktree"}}
    c = _load_one(tmp_path, "settings", "s.json.json", dict(SETTING_ITEM, keys=keys))
    assert c.settings["s.json"]["keys"] == keys


def test_load_stops_at_the_first_bad_item_in_name_order(tmp_path: Path):
    _load_one(tmp_path, "env", "NDF_A.json", dict(ENV_ITEM, name="NDF_A"))
    with pytest.raises(_model.ContractError) as e:
        _load_one(tmp_path, "env", "NDF_B.json", dict(ENV_ITEM, name="NDF_B", set_by="bad"))
    assert str(e.value).startswith("NDF_B.json:")


# --- 実行の核の検査（#2337） ---


def test_entrypoint_that_does_not_go_through_the_core_fails_by_name(tree: Path):
    edit(tree, 'run_core.main("scripts/tool.py", main)', "sys.exit(main())")
    code, r = run(tree)
    assert code == 1 and ("core-entry", "") in kinds(r)
    assert _item(r, "core-entry")["path"] == "plugins/ndf/scripts/tool.py"


def test_core_entry_id_must_be_the_contract_path(tree: Path):
    edit(tree, 'run_core.main("scripts/tool.py"', 'run_core.main("scripts/other.py"')
    code, r = run(tree)
    assert code == 1 and "プラグインの根からのパス" in _item(r, "core-entry")["detail"]


def test_shell_entrypoint_must_call_rc_enter_first(tree: Path):
    sh = tree / "plugins/ndf/scripts/t.sh"
    sh.write_text("#!/usr/bin/env bash\n# 説明\nset -e\necho hi\n")
    (tree / "plugins/ndf/skills/a/SKILL.md").write_text("python3 scripts/tool.py --root .\nbash scripts/t.sh\n")
    entry = dict(ENTRY, path="plugins/ndf/scripts/t.sh", env=[], settings=[], writes=[], args={"unparsed": "手で読む"})
    wj(tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__t.sh.json", entry)
    run(tree, "--render")
    assert ("core-entry", "") in [(i["kind"], i["name"]) for i in run(tree)[1]["items"] if i["path"].endswith("t.sh")]
    sh.write_text('#!/usr/bin/env bash\n# 説明\n. "$(dirname "$0")/lib/run-core.sh"; rc_enter "scripts/t.sh" "$@"\necho hi\n')
    assert not [i for i in run(tree)[1]["items"] if i["path"].endswith("t.sh") and i["kind"] == "core-entry"]


def test_hook_mark_needs_evidence(tree: Path):
    f = tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool.py.json"
    wj(f, dict(ENTRY, core={"exempt": "hook", "evidence": "hook が起動する"}))
    code, r = run(tree)
    assert code == 1 and ("core-exempt", "hook") in kinds(r)
    (tree / "plugins/ndf/hooks").mkdir(parents=True)
    (tree / "plugins/ndf/hooks/claude.json").write_text('{"command": "python3 scripts/tool.py"}')
    edit(tree, 'run_core.main("scripts/tool.py", main)', "sys.exit(main())")
    assert run(tree)[0] == 0


def test_unknown_core_mark_stops_with_2(tree: Path):
    f = tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool.py.json"
    wj(f, dict(ENTRY, core={"exempt": "other", "evidence": "x"}))
    assert run(tree)[0] == 2


@pytest.mark.parametrize("exit_map,name", [({"0": "成功", "2": "引数の誤り"}, "exit:2"), ({"0": "成功", "5": "失敗"}, "exit:5")])
def test_exit_values_must_start_with_the_table_label(tree: Path, exit_map, name):
    wj(tree / "plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__tool.py.json", dict(ENTRY, exit=exit_map))
    code, r = run(tree)
    assert code == 1 and ("exit-meaning", name) in kinds(r)


def test_own_exit_constants_fail(tree: Path):
    edit(tree, "def main():", "EXIT_FOUND_MANY = 4\n\n\ndef main():")
    code, r = run(tree)
    assert code == 1 and ("exit-meaning", "EXIT_FOUND_MANY") in kinds(r)


def test_reading_the_last_line_outside_the_common_reader_fails(tree: Path):
    (tree / "plugins/ndf/scripts/lib/reader.py").write_text(
        "import json\n\n\ndef last_json(out):\n    return json.loads(out.splitlines()[-1])\n"
    )
    code, r = run(tree)
    assert code == 1
    assert {("result-read", "def:last_json"), ("result-read", "last_json:last-line")} <= set(kinds(r))


def test_declared_writer_of_a_passed_file_is_not_file_dynamic(tree: Path):
    edit(tree, 'Path("out.json").write_text("{}")', 'Path("out.json").write_text("{}")\n    Path(a.root).write_text("{}")')
    assert ("file-dynamic", "main") in kinds(run(tree)[1])
    item = {"name": "res", "pattern": "*-result.json", "kind": "result", "place": "state-dir", "skill_readers": []}
    wj(tree / "plugins/ndf/contracts/files/res.json", dict(item, writers=["plugins/ndf/scripts/tool.py:main"]))
    run(tree, "--render")
    assert run(tree)[0] == 0


def test_base_knows_runs_once_per_kind(monkeypatch):
    """merge-base の検査を読む git show は、種類ごとに 1 回だけ起こす。"""
    sys.path.insert(0, str(REPO / "scripts"))
    from contracts_lib import ratchet

    calls = []
    monkeypatch.setattr(ratchet, "_names_at", lambda *a: set())
    monkeypatch.setattr(ratchet, "base_knows", lambda root, sha, kind: calls.append(kind) or True)
    now = [{"kind": k, "name": f"N{i}", "path": f"p{i}"} for i, k in enumerate(["a", "a", "b", "a", "b"])]
    assert len(ratchet.added(Path("."), "sha", "x", now, lambda r: f"{r['path']}.json")) == 5
    assert sorted(calls) == ["a", "b"]


def test_base_runtime_count_reads_the_base_with_two_git_processes(tmp_path: Path, monkeypatch):
    """ベースの runtime の印は、契約の数によらず git 2 回（一覧と一括読み）で数える。"""
    sys.path.insert(0, str(REPO / "scripts"))
    from contracts_lib import core_checks

    d = tmp_path / "plugins/ndf/contracts/entrypoints"
    d.mkdir(parents=True)
    for i, ex in enumerate(["runtime", "hook", None, "runtime"]):
        wj(d / f"e{i}.json", {"path": f"e{i}.py", **({"core": {"exempt": ex}} if ex else {})})
    (d / "broken.json").write_text("{")
    g = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g[:3], "init", "-q"], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "c"], check=True)
    sha = subprocess.run([*g[:3], "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    calls = []
    real = subprocess.run
    monkeypatch.setattr(core_checks.subprocess, "run", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    assert core_checks.base_runtime_count(tmp_path, sha) == 2
    assert len(calls) == 2


# ---------- 上限の宣言（#2343 の決定 13・不変条件 I1・I4） ----------


def _limits(tree: Path, policies: dict) -> None:
    wj(tree / "plugins/ndf/contracts/limits.json", {"version": 1, "policies": {**LIMITS["policies"], **policies}})


def test_a_policy_name_the_declaration_lacks_fails(tree: Path):
    (tree / "plugins/ndf/scripts/lib/part.py").write_text('import limits\n\n\ndef f():\n    return limits.seconds("part.wait")\n')
    code, r = run(tree)
    assert code == 1 and ("limits-unknown", "part.wait") in kinds(r)
    _limits(tree, {"part.wait": {"summary": "部品の待ち", "seconds": 5}})
    assert run(tree, "--render")[0] == 0
    code, r = run(tree)
    assert code == 0, r
    assert "`plugins/ndf/scripts/lib/part.py:f`" in (tree / "plugins/ndf/contracts/index.md").read_text()


def test_a_policy_no_code_reads_fails(tree: Path):
    _limits(tree, {"part.wait": {"summary": "部品の待ち", "seconds": 5}})
    run(tree, "--render")
    code, r = run(tree)
    assert code == 1 and kinds(r) == [("limits-unused", "part.wait")]


def test_a_name_kept_in_a_module_constant_counts_and_a_computed_name_fails(tree: Path):
    _limits(tree, {"part.wait": {"summary": "部品の待ち", "seconds": 5}})
    run(tree, "--render")
    part = tree / "plugins/ndf/scripts/lib/part.py"
    part.write_text('import limits as lim\n\nPOLICY = "part.wait"\n\n\ndef f(x):\n    return lim.policy(POLICY), lim.value(x, "seconds")\n')
    run(tree, "--render")
    code, r = run(tree)
    assert code == 1 and [k for k, _ in kinds(r)] == ["limits-unknown"]


def test_a_malformed_declaration_fails(tree: Path):
    _limits(tree, {"part.wait": {"summary": "部品の待ち", "seconds": -1}})
    code, r = run(tree)
    assert code == 1 and ("limits-shape", "") in kinds(r)


# ---------- 現状固定: base_runtime_count の分岐（I-006） ----------


def _commit_tree(root: Path) -> str:
    g = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    if not (root / ".git").exists():
        subprocess.run([*g[:3], "init", "-q"], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "c", "--allow-empty"], check=True)
    return subprocess.run([*g[:3], "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def test_base_runtime_count_returns_none_or_zero_when_it_cannot_count(tmp_path: Path):
    """現状固定: SHA が無い・解決できないと None、一覧が空なら 0。"""
    sys.path.insert(0, str(REPO / "scripts"))
    from contracts_lib import core_checks

    assert core_checks.base_runtime_count(tmp_path, None) is None
    assert core_checks.base_runtime_count(tmp_path, "") is None
    (tmp_path / "README").write_text("x")
    sha = _commit_tree(tmp_path)
    assert core_checks.base_runtime_count(tmp_path, sha) == 0  # entrypoints/ が無い
    assert core_checks.base_runtime_count(tmp_path, "0" * 40) is None
    assert core_checks.base_runtime_count(tmp_path / "no-repo", sha) is None


def test_base_runtime_count_counts_only_runtime_marks_at_the_given_commit(tmp_path: Path):
    """現状固定: 下位のディレクトリも数え、配列・壊れた JSON・空のファイル・一覧の外は数えない。数えるのは渡した SHA の木。"""
    sys.path.insert(0, str(REPO / "scripts"))
    from contracts_lib import core_checks

    d = tmp_path / "plugins/ndf/contracts/entrypoints"
    wj(d / "a.json", {"path": "a.py", "core": {"exempt": "runtime"}})
    wj(d / "sub" / "b.json", {"path": "b.py", "core": {"exempt": "runtime"}, "note": "改行を\n含む 値"})
    wj(d / "c.json", {"path": "c.py", "core": {"exempt": "hook"}})
    wj(d / "d.json", {"path": "d.py", "core": {}})
    (d / "list.json").write_text(json.dumps([{"core": {"exempt": "runtime"}}]))
    (d / "broken.json").write_text("{")
    (d / "empty.json").write_text("")
    (d / "plain.txt").write_text('{"core": {"exempt": "runtime"}}')  # 拡張子を問わない
    wj(tmp_path / "plugins/ndf/contracts/files/x.json", {"core": {"exempt": "runtime"}})  # 一覧の外
    first = _commit_tree(tmp_path)
    assert core_checks.base_runtime_count(tmp_path, first) == 3

    (d / "a.json").unlink()
    second = _commit_tree(tmp_path)
    assert core_checks.base_runtime_count(tmp_path, second) == 2
    assert core_checks.base_runtime_count(tmp_path, first[:12]) == 3  # 短縮形も読む


# --- model._check_entry: 入口の項目の形の検査を分岐ごとに固定する（現状固定） ---

_ENTRY_F = Path(_model.entry_file_name(LOAD_ENTRY["path"]))
_LEASE = {"key": "run:{run}", "unit": "run", "window_seconds": 60}


def _without(d: dict, k: str) -> dict:
    return {x: v for x, v in d.items() if x != k}


@pytest.mark.parametrize(
    "data",
    [
        LOAD_ENTRY,
        dict(_without(LOAD_ENTRY, "external"), callers=["a/SKILL.md"]),
        dict(LOAD_ENTRY, args={"unparsed": "手で読む"}),
        dict(LOAD_ENTRY, args={"options": [], "positionals": [], "subcommands": {"s": {"options": [], "positionals": []}}}),
        dict(LOAD_ENTRY, settings=[{"file": "w.json", "keys": ["k"]}]),
        dict(LOAD_ENTRY, lease={"": _LEASE, "s": dict(_LEASE, unit="session", release=True, shared=False)}),
        dict(LOAD_ENTRY, lease={}),
        dict(LOAD_ENTRY, core={"exempt": "hook", "evidence": "hook が起動する"}),
        dict(LOAD_ENTRY, core={"exempt": "runtime", "evidence": "e"}),
    ],
)
def test_check_entry_accepts_well_formed_items(data):
    assert _model._check_entry(_ENTRY_F, data) is None


@pytest.mark.parametrize(
    "data, message",
    [
        (dict(LOAD_ENTRY, extra=1), "知らないキー ['extra']"),
        (_without(LOAD_ENTRY, "summary"), "summary が無い"),
        (dict(LOAD_ENTRY, version="1"), "version の型が違う"),
        (dict(LOAD_ENTRY, external=1), "external の型が違う"),
        (dict(LOAD_ENTRY, lease=[]), "lease の型が違う"),
        (_without(LOAD_ENTRY, "external"), "callers と external はどちらか 1 つ"),
        (dict(LOAD_ENTRY, callers=["a"]), "callers と external はどちらか 1 つ"),
        (dict(LOAD_ENTRY, path="plugins/ndf/scripts/b.py"), "ファイル名は plugins__ndf__scripts__b.py.json にする"),
        (dict(LOAD_ENTRY, env=[1]), "env は文字列の並び"),
        (dict(LOAD_ENTRY, writes=[1]), "writes は文字列の並び"),
        (dict(LOAD_ENTRY, reads=[None]), "reads は文字列の並び"),
        (dict(_without(LOAD_ENTRY, "external"), callers=[1]), "callers は文字列の並び"),
        (dict(LOAD_ENTRY, stdin="bad"), "stdin は none/json/text/hook-json のどれか: bad"),
        (dict(LOAD_ENTRY, stdout="bad"), "stdout は step-result/json/kv/text/markdown/none のどれか: bad"),
        (dict(LOAD_ENTRY, args={"unparsed": " "}), "args の unparsed は理由の文字列だけを持つ"),
        (dict(LOAD_ENTRY, args={"unparsed": "r", "options": []}), "args の unparsed は理由の文字列だけを持つ"),
        (dict(LOAD_ENTRY, args={"options": []}), "positionals が無い"),
        (dict(LOAD_ENTRY, args={"options": [], "positionals": [], "subcommands": {"s": 1}}), "subcommands.s はオブジェクト"),
        (dict(LOAD_ENTRY, settings=["w.json"]), "settings の要素は file と keys を持つ"),
        (dict(LOAD_ENTRY, settings=[{"file": "w.json"}]), "settings の要素は file と keys を持つ"),
        (dict(LOAD_ENTRY, settings=[{"file": "w.json", "keys": "k"}]), "settings の要素は file と keys を持つ"),
        (dict(LOAD_ENTRY, exit={}), "exit は終了コード → 意味（unknown なら理由）"),
        (dict(LOAD_ENTRY, exit={"0": " "}), "exit は終了コード → 意味（unknown なら理由）"),
        (dict(LOAD_ENTRY, exit={"0": 0}), "exit は終了コード → 意味（unknown なら理由）"),
        (dict(LOAD_ENTRY, lease={"": 1}), "lease[''] は key・unit・window_seconds と任意の release・shared を持つ"),
        (dict(LOAD_ENTRY, lease={"": _without(_LEASE, "unit")}), "lease[''] は key・unit・window_seconds"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, x=1)}), "lease[''] は key・unit・window_seconds"),
        (dict(LOAD_ENTRY, lease={"s": dict(_LEASE, key="run")}), "lease['s'].key は入力の名前を {} で埋める鍵の形"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, key=1)}), "lease[''].key は"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, unit="day")}), "lease[''].unit は run/session"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, window_seconds=0)}), "lease[''].window_seconds は正の整数"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, window_seconds=True)}), "lease[''].window_seconds は正の整数"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, window_seconds=1.5)}), "lease[''].window_seconds は正の整数"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, release="yes")}), "lease[''].release は真偽値"),
        (dict(LOAD_ENTRY, lease={"": dict(_LEASE, shared=1)}), "lease[''].shared は真偽値"),
        (dict(LOAD_ENTRY, core={"exempt": "other", "evidence": "e"}), "core は exempt（hook/runtime）と evidence を持つ"),
        (dict(LOAD_ENTRY, core={"exempt": "hook"}), "core は exempt（hook/runtime）と evidence を持つ"),
        (dict(LOAD_ENTRY, core={"exempt": "hook", "evidence": " "}), "core.evidence は根拠の文"),
        (dict(LOAD_ENTRY, core={"exempt": "hook", "evidence": 1}), "core.evidence は根拠の文"),
    ],
)
def test_check_entry_rejects_each_malformed_shape(data, message):
    with pytest.raises(_model.ContractError) as e:
        _model._check_entry(_ENTRY_F, data)
    assert str(e.value).startswith(f"{_ENTRY_F.name}: ") and message in str(e.value)


def test_check_entry_reports_the_first_violation_in_check_order():
    """複数の違反があるとき、キーの検査 → callers/external → ファイル名 → 文字列の並び → stdin/stdout → args → settings → exit → lease → core の順で最初の 1 つで止まる。"""
    bad = dict(LOAD_ENTRY, path="x.py", env=[1], stdin="bad", exit={}, core={"exempt": "x", "evidence": "e"})
    order = [
        ("path", "ファイル名は"),
        ("env", "env は文字列の並び"),
        ("stdin", "stdin は"),
        ("exit", "exit は終了コード"),
        ("core", "core は exempt"),
    ]
    for key, message in order:
        with pytest.raises(_model.ContractError) as e:
            _model._check_entry(_ENTRY_F, bad)
        assert message in str(e.value)
        bad[key] = LOAD_ENTRY.get(key, {"exempt": "hook", "evidence": "e"})
    assert _model._check_entry(_ENTRY_F, bad) is None
