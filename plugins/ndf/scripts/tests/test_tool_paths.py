"""ツールのパス（#1436）: 共通の定義 `lib/tool_paths.py` と、`pr-steps.py` の plan / commit。

一時の git リポジトリで確かめる。cross-review と cross-refactoring の経路は各 Skill のテストにある
（`test_tool_paths_round.py` / `test_tool_paths_sync.py`）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
LIB = SCRIPTS / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import tool_paths  # noqa: E402

PY = sys.executable
SERENA = [".serena/project.yml", ".serena/serena_config.yml"]
MARK = "auth_secret: MARK-1436"


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=check).stdout


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def make_repo(root: Path, decl: dict | None = None, tracked=SERENA) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    for rel in [*tracked, "src/a.txt", ".idea/workspace.xml"]:
        write(root, rel, "old\n")
    if decl is not None:
        write(root, ".ndf/worktree.json", json.dumps(decl))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def serena_rewrites(root, paths=SERENA):
    for rel in paths:
        write(root, rel, f"{MARK}\n")


# --- load（I2） --------------------------------------------------------------------


def test_load_defaults_without_a_declaration(tmp_path):
    assert tool_paths.load_entries(make_repo(tmp_path / "r")) == SERENA


@pytest.mark.parametrize("decl", [{"version": 1}, {"version": 2}, {"base_branch": "develop"}])
def test_load_defaults_without_tool_paths(tmp_path, decl):
    assert tool_paths.load_entries(make_repo(tmp_path / "r", decl)) == SERENA


def test_load_adds_and_removes(tmp_path):
    decl = {"version": 1, "tool_paths": {"add": [".idea/", SERENA[0]], "remove": [SERENA[1]]}}
    assert tool_paths.load_entries(make_repo(tmp_path / "r", decl)) == [SERENA[0], ".idea/"]


@pytest.mark.parametrize(
    "decl",
    [
        {"version": 2, "tool_paths": {"add": ["x"]}},
        {"tool_paths": {"add": ["x"]}},
        {"version": 1, "tool_paths": ["x"]},
        {"version": 1, "tool_paths": {"add": "x"}},
        {"version": 1, "tool_paths": {"remove": [1]}},
    ],
)
def test_load_refuses_unreadable_tool_paths(tmp_path, decl):
    with pytest.raises(tool_paths.ToolPathsUnreadable):
        tool_paths.load_entries(make_repo(tmp_path / "r", decl))


def test_load_refuses_broken_json(tmp_path):
    root = make_repo(tmp_path / "r")
    write(root, ".ndf/worktree.json", "{broken")
    with pytest.raises(tool_paths.ToolPathsUnreadable) as e:
        tool_paths.load_entries(root)
    assert e.value.message.startswith(".ndf/worktree.json の tool_paths を読めない:")


def test_load_does_not_read_the_main_directory(tmp_path):
    """レビュー worktree に設定が無ければ、メインディレクトリにだけある設定を読まない。"""
    main = make_repo(tmp_path / "main")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", "--detach", str(wt))
    write(main, ".ndf/worktree.json", json.dumps({"version": 1, "tool_paths": {"remove": SERENA}}))
    assert tool_paths.load_entries(wt) == SERENA


# --- 照合と分類（I3） ---------------------------------------------------------------


def test_prefix_entry_needs_the_slash():
    assert tool_paths.is_tool_path("x/a", ["x/"])
    assert not tool_paths.is_tool_path("xy/a", ["x/"])
    assert not tool_paths.is_tool_path("x", ["x/"])
    assert tool_paths.is_tool_path("a/b.yml", ["a/b.yml"])
    assert not tool_paths.is_tool_path("a/b.yml.bak", ["a/b.yml"])


def test_split_puts_each_path_in_one_list():
    s = tool_paths.split_changes(["src/a.txt", SERENA[0], "x/1"], [SERENA[0], "x/"])
    assert s.user == ["src/a.txt"] and s.tool == [SERENA[0], "x/1"]
    assert not set(s.user) & set(s.tool)


# --- 印（I6） -----------------------------------------------------------------------


def test_hide_marks_only_tracked_paths(tmp_path):
    root = make_repo(tmp_path / "r", tracked=SERENA[:1])
    assert tool_paths.hide(root, SERENA) == SERENA[:1]
    assert git(root, "ls-files", "-v", "--", SERENA[0]).startswith("S ")
    serena_rewrites(root, SERENA[:1])
    assert git(root, "status", "--porcelain") == ""
    assert tool_paths.hidden(root, SERENA) == SERENA[:1]


def test_release_restores_head_and_drops_the_mark(tmp_path):
    root = make_repo(tmp_path / "r")
    tool_paths.hide(root, SERENA)
    serena_rewrites(root)
    assert tool_paths.release(root, SERENA) == SERENA
    assert (root / SERENA[0]).read_text() == "old\n"
    assert tool_paths.hidden(root, SERENA) == []


def test_committed_is_none_when_the_base_is_unknown(tmp_path):
    root = make_repo(tmp_path / "r")
    assert tool_paths.committed(root, "no-such-ref", SERENA) is None
    assert tool_paths.push_blocked(root, "no-such-ref", SERENA) == "ツールのパスの有無を確かめられないため push しない"


# --- pr-steps.py（受け入れ条件 5・6・8・10、I2・I6・I7） ------------------------------------


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(f"#!{PY}\nimport sys\nif sys.argv[1:3] == ['pr', 'list']:\n    print('[]')\n", encoding="utf-8")
    gh.chmod(0o755)
    e = dict(os.environ)
    e["PATH"] = f"{bindir}{os.pathsep}{e['PATH']}"
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    return e


def dev_worktree(tmp_path, decl=None):
    """main を起点に feature/x へ移った開発 worktree。origin は bare。"""
    root = make_repo(tmp_path / "repo", decl)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "main")
    git(root, "checkout", "-q", "-b", "feature/x")
    return root


def call(args, env, cwd):
    p = subprocess.run([PY, str(SCRIPTS / "pr-steps.py"), *args], capture_output=True, text=True, env=env, cwd=cwd)
    lines = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(lines[-1]) if lines else None), p.stdout + p.stderr


def committed_files(root):
    return git(root, "show", "--name-only", "--format=", "HEAD").split()


def test_plan_counts_tool_paths_apart_and_commit_leaves_them_out(tmp_path, env):
    """受け入れ条件 5・10、I6・I7。"""
    root = dev_worktree(tmp_path)
    serena_rewrites(root)
    write(root, "src/a.txt", "new\n")

    code, out, text = call(["plan"], env, root)
    assert code == 0, text
    assert out["metrics"]["uncommitted"] == 1
    changes = [i for i in out["items"] if i["kind"] == "changes"][0]
    assert changes["files"] == [" M src/a.txt"]
    tool = [i for i in out["items"] if i["kind"] == "tool_paths"]
    assert len(tool) == 1 and tool[0]["result"] == "excluded" and len(tool[0]["files"]) == 2

    code, out, text = call(["commit", "--message", "Add: a"], env, root)
    assert code == 0 and out["metrics"]["committed"] is True, text
    assert committed_files(root) == ["src/a.txt"]
    assert [i for i in out["items"] if i["kind"] == "tool_paths"][0]["result"] == "unstaged"
    for rel in SERENA:  # 中身は残る（捨てない）
        assert (root / rel).read_text() == f"{MARK}\n"
    assert "MARK-1436" not in text
    assert not any(line.startswith("S ") for line in git(root, "ls-files", "-v").splitlines())


def test_commit_with_only_tool_paths_makes_no_commit(tmp_path, env):
    root = dev_worktree(tmp_path)
    serena_rewrites(root)
    before = git(root, "rev-parse", "HEAD")
    code, out, text = call(["commit", "--message", "Add: x"], env, root)
    assert code == 0 and out["metrics"]["committed"] is False, text
    assert [i["kind"] for i in out["items"]] == ["tool_paths"]
    assert "git commit で直接コミットする" in out["summary"]
    assert git(root, "rev-parse", "HEAD") == before


def test_commit_still_commits_other_changes(tmp_path, env):
    """受け入れ条件 8: ツールのパスでない変更だけなら今と同じにコミットする。"""
    root = dev_worktree(tmp_path)
    write(root, "src/a.txt", "new\n")
    code, out, text = call(["commit", "--message", "Add: a"], env, root)
    assert code == 0 and out["metrics"]["committed"] is True, text
    assert committed_files(root) == ["src/a.txt"]
    assert not [i for i in out["items"] if i["kind"] == "tool_paths"]


def test_added_tool_path_is_left_out_and_removed_one_is_committed(tmp_path, env):
    """受け入れ条件 6・7（pr の経路）。"""
    decl = {"version": 1, "tool_paths": {"add": [".idea/workspace.xml"], "remove": [SERENA[0]]}}
    root = dev_worktree(tmp_path, decl)
    write(root, ".idea/workspace.xml", "new\n")
    write(root, SERENA[0], "new\n")
    code, out, text = call(["plan"], env, root)
    assert out["metrics"]["uncommitted"] == 1, text
    code, out, text = call(["commit", "--message", "Add: p"], env, root)
    assert committed_files(root) == [SERENA[0]], text


def test_unreadable_tool_paths_stop_pr_steps(tmp_path, env):
    """I2: 読めない設定で既定へ戻らず止まり、足したパスをコミットしない。"""
    root = dev_worktree(tmp_path, {"version": 2, "tool_paths": {"add": [".idea/workspace.xml"]}})
    write(root, ".idea/workspace.xml", f"{MARK}\n")
    before = git(root, "rev-parse", "HEAD")
    for args in (["plan"], ["commit", "--message", "Add: x"]):
        code, out, text = call(args, env, root)
        assert code != 0 and out["status"] == "stopped", text
        assert "tool_paths を読めない" in out["summary"]
    assert git(root, "rev-parse", "HEAD") == before


def test_default_is_the_single_definition_for_pr_steps(tmp_path, monkeypatch):
    """受け入れ条件 9（pr の経路）: 既定を差し替えると `pr-steps.py` の分類が追随する。"""
    spec = importlib.util.spec_from_file_location("pr_steps_1436", SCRIPTS / "pr-steps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.tool_paths is tool_paths
    root = make_repo(tmp_path / "r")
    write(root, "src/a.txt", "new\n")
    serena_rewrites(root)
    monkeypatch.setattr(tool_paths, "DEFAULT", ("src/a.txt",))
    user, tool = mod.tool_paths.split_status(root, mod.tool_paths.load_entries(root))
    assert [line[3:] for line in tool] == ["src/a.txt"]
    assert sorted(line[3:] for line in user) == SERENA
