"""各 Skill へ移した手順のスクリプト（#857 / #872 / #862）と、互換の入口 phase-steps.py（#846）。

一時の git リポジトリを作り、gh は PATH の先頭に置いた偽物（FAKE_GH_PRS の JSON を返す）で置き換える。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable

FAKE_GH = """#!{py}
import json, os, sys
a = sys.argv[1:]
prs = json.loads(os.environ.get("FAKE_GH_PRS", "{{}}"))
if a[:2] == ["pr", "view"] and a[2] in prs:
    print(json.dumps(prs[a[2]]))
    sys.exit(0)
sys.stderr.write("fake gh: " + " ".join(a) + "\\n")
sys.exit(1)
"""


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH.format(py=PY), encoding="utf-8")
    gh.chmod(0o755)
    e = dict(os.environ)
    e["PATH"] = f"{bindir}{os.pathsep}{e['PATH']}"
    e["NDF_PRESENTATION_DIR"] = str(tmp_path / "pres")
    e["NDF_WORKTREE_BASE"] = str(tmp_path / "wtbase")
    e["FAKE_GH_PRS"] = "{}"
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    return e


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    write(root, "keep.txt", "head\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def call(script, args, env, cwd=None):
    p = subprocess.run([PY, str(SCRIPTS / script), *args], capture_output=True, text=True, env=env, cwd=cwd)
    lines = p.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines else None
    return p.returncode, out, p.stderr


def check_shape(out):
    assert set(out) >= {"tool", "status", "summary", "items", "metrics"}
    assert out["status"] in ("ok", "gate", "stopped")


# --- cleanup（merged-steps.py） ---------------------------------------------

def test_cleanup_removes_merged_and_stops_on_unmerged(repo, env, tmp_path):
    # 取り込み先を main と宣言し、主ディレクトリ（develop）では pull させない
    write(repo, ".ndf/worktree.json", json.dumps({"base_branch": "main"}))
    for b in ("feature/x", "feature/y"):
        git(repo, "worktree", "add", "-q", "-b", b, str(tmp_path / b.replace("/", "-")))
    wy = tmp_path / "feature-y"
    write(wy, "y.txt", "y\n")
    git(wy, "add", "-A")
    git(wy, "commit", "-q", "-m", "y")
    write(tmp_path / "feature-x", "ignored.log", "junk\n")  # 未追跡のファイルは退避して外す
    env["FAKE_GH_PRS"] = json.dumps({
        "1": {"headRefName": "feature/x", "state": "MERGED"},
        "2": {"headRefName": "feature/y", "state": "MERGED"},
        "3": {"headRefName": "feature/gone", "state": "MERGED"},
        "4": {"headRefName": "feature/open", "state": "OPEN"},
    })
    code, out, err = call("merged-steps.py", ["cleanup", "1", "2", "3", "4", "--root", str(repo)], env)
    assert code == 10, err
    check_shape(out)
    assert out["tool"] == "merged" and out["status"] == "gate"
    by = {(i["kind"], i["name"]): i for i in out["items"]}
    assert by[("branch", "feature/x")]["result"] == "deleted"
    assert by[("branch", "feature/x")]["restore"].startswith("git branch feature/x ")
    assert by[("branch", "feature/y")]["result"] == "stopped"
    assert by[("branch", "feature/gone")]["result"] == "absent"  # 無いブランチは止めない（#769）
    assert by[("pr", "feature/open")]["result"] == "kept"
    assert not (tmp_path / "feature-x").exists() and not (tmp_path / "feature-y").exists()
    assert "feature/x" not in git(repo, "branch", "--list")
    assert "feature/y" in git(repo, "branch", "--list")
    assert out["next"] == "同意を得たら git branch -D feature/y"
    pres = Path(out["presentation_path"]).read_text(encoding="utf-8")
    assert "`git branch -D feature/y`" in pres and "## 戻し方" in pres
    assert out["metrics"]["stopped"] == 1


def test_cleanup_all_merged_is_ok(repo, env, tmp_path):
    write(repo, ".ndf/worktree.json", json.dumps({"base_branch": "main"}))
    git(repo, "worktree", "add", "-q", "-b", "feature/x", str(tmp_path / "wx"))
    env["FAKE_GH_PRS"] = json.dumps({"1": {"headRefName": "feature/x", "state": "MERGED"}})
    code, out, err = call("merged-steps.py", ["cleanup", "1"], env, cwd=repo)
    assert code == 0, err
    assert out["status"] == "ok" and "presentation_path" not in out
    assert out["metrics"]["removed_worktrees"] == 1 and out["metrics"]["deleted_branches"] == 1



def test_cleanup_from_inside_the_worktree_it_removes(repo, env, tmp_path):
    # 計画の merge のステップは作業場所（消す作業ツリー）で打つ。消した後も主ディレクトリから続ける
    write(repo, ".ndf/worktree.json", json.dumps({"base_branch": "main"}))
    wx = tmp_path / "wx"
    git(repo, "worktree", "add", "-q", "-b", "feature/x", str(wx))
    env["FAKE_GH_PRS"] = json.dumps({"1": {"headRefName": "feature/x", "state": "MERGED"}})
    code, out, err = call("merged-steps.py", ["cleanup", "1", "--root", str(wx)], env, cwd=repo)
    assert code == 0, (out, err)
    assert not wx.exists() and "feature/x" not in git(repo, "branch", "--list")

# --- spec-finalize（plan-to-spec-steps.py） ---------------------------------

def spec_repo(repo):
    write(repo, "docs/design/x-design.md", "# 設計\n")
    write(repo, "docs/specifications/README.md", "# 索引\n\n- [a.md](a.md) — A\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "design")
    write(repo, "docs/specifications/x.md", "# X の仕様\n")


def test_spec_finalize_removes_design_and_indexes_spec(repo, env):
    spec_repo(repo)
    code, out, err = call("plan-to-spec-steps.py",
                          ["spec-finalize", "--spec", "docs/specifications/x.md",
                           "--design", "docs/design/x-design.md", "--title", "X", "--root", str(repo)], env)
    assert code == 0, err
    check_shape(out)
    assert out["tool"] == "plan-to-spec" and out["status"] == "ok"
    assert not (repo / "docs/design/x-design.md").exists()
    idx = (repo / "docs/specifications/README.md").read_text(encoding="utf-8")
    assert "- [x.md](x.md) — X" in idx
    assert git(repo, "status", "--porcelain").strip() == ""
    assert git(repo, "log", "-1", "--format=%s").strip() == "Docs: x.md を確定仕様にする"
    assert out["metrics"]["commit"] == git(repo, "rev-parse", "HEAD").strip()


def test_spec_finalize_missing_spec_is_precondition(repo, env):
    code, out, _ = call("plan-to-spec-steps.py",
                        ["spec-finalize", "--spec", "nope.md", "--design", "keep.txt", "--root", str(repo)], env)
    assert code == 3
    assert out["status"] == "stopped" and "nope.md" in out["summary"]


# --- bump / changelog（release-steps.py） ------------------------------------

def plugin_repo(repo, name="mcp-x", version="1.0.0", heading="1.0.0"):
    d = f"plugins/mcp/{name}"
    write(repo, f"{d}/.claude-plugin/plugin.json",
          json.dumps({"name": name, "version": version, "description": f"X (v{version})"}, indent=2) + "\n")
    write(repo, f"{d}/README.md", f"# {name}\n\n## v{heading} へ更新するとき\n\n前の版の説明\n\n## 使い方\n\nx\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "plugin")
    return repo / d


def test_bump_updates_manifest_and_update_heading(repo, env):
    pdir = plugin_repo(repo)
    code, out, err = call("release-steps.py", ["bump", "--plugin", "mcp-x", "--to", "1.1.0", "--root", str(repo)], env)
    assert code == 0, err
    check_shape(out)
    assert out["tool"] == "release" and out["status"] == "ok"
    pj = json.loads((pdir / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert pj["version"] == "1.1.0" and pj["description"] == "X (v1.1.0)"
    assert "## v1.1.0 へ更新するとき" in (pdir / "README.md").read_text(encoding="utf-8")
    assert out["metrics"]["from"] == "1.0.0" and out["metrics"]["to"] == "1.1.0"
    files = [i["name"] for i in out["items"] if i["result"] == "updated"]
    assert "plugins/mcp/mcp-x/.claude-plugin/plugin.json" in files
    assert any(i["result"] == "manual" for i in out["items"]) and out["next"]


def test_bump_unknown_plugin_is_precondition(repo, env):
    code, out, _ = call("release-steps.py", ["bump", "--plugin", "nope", "--to", "1.1.0", "--root", str(repo)], env)
    assert code == 3 and out["status"] == "stopped"


def test_changelog_adds_section_and_replaces_update_guide(repo, env):
    pdir = plugin_repo(repo, heading="1.1.0")
    write(repo, "CHANGELOG.md", "# Changelog\n\n## [mcp-x 1.0.0] - 2026-01-01\n\n- old（#1）\n")
    env["FAKE_GH_PRS"] = json.dumps({"5": {"title": "Add: x"}, "6": {"title": "Fix: y"}})
    code, out, err = call("release-steps.py", ["changelog", "--version", "1.1.0", "--prs", "5", "6",
                                                "--plugin", "mcp-x", "--root", str(repo)], env)
    assert code == 0, err
    check_shape(out)
    cl = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert cl.index("## [mcp-x 1.1.0] - ") < cl.index("## [mcp-x 1.0.0]")
    assert "- Add: x（#5）\n- Fix: y（#6）" in cl
    rd = (pdir / "README.md").read_text(encoding="utf-8")
    assert "前の版の説明" not in rd and "- Add: x（#5）" in rd and "## 使い方" in rd
    assert {i["result"] for i in out["items"]} == {"added", "replaced"}
    assert out["next"]

    # もう一度呼んでも同じ PR を重ねない
    code, out, _ = call("release-steps.py", ["changelog", "--version", "1.1.0", "--prs", "5",
                                              "--plugin", "mcp-x", "--root", str(repo)], env)
    assert code == 0
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8").count("（#5）") == 1


def test_changelog_gh_failure_stops(repo, env):
    write(repo, "CHANGELOG.md", "# Changelog\n")
    code, out, _ = call("release-steps.py", ["changelog", "--version", "1.1.0", "--prs", "99",
                                              "--root", str(repo)], env)
    assert code == 1 and out["status"] == "stopped" and "gh pr view 99" in out["summary"]


def test_release_steps_run_and_check_are_unchanged(repo, env):
    assert subprocess.run([PY, str(SCRIPTS / "release-steps.py"), "check", "--root", str(repo)],
                          env=env).returncode == 2
    p = subprocess.run([PY, str(SCRIPTS / "release-steps.py"), "run", "--root", str(repo),
                        "--stage", "production", "--version", "1.0.0"], env=env, capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout == ""


def test_verify_install_unknown_runtime_is_2(repo, env):
    code, out, _ = call("release-verification-steps.py",
                        ["verify-install", "--ref", "develop", "--expect", "1.0.0", "--runtimes", "nope",
                         "--root", str(repo)], env)
    assert code == 2 and out["tool"] == "release-verification" and out["status"] == "stopped"


# --- phase-steps.py（互換の入口） --------------------------------------------

@pytest.mark.parametrize("root_first", [True, False])
def test_phase_steps_forwards_with_root_before_or_after(repo, env, root_first):
    spec_repo(repo)
    sub = ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md"]
    args = (["--root", str(repo), *sub] if root_first else [*sub, "--root", str(repo)])
    code, out, err = call("phase-steps.py", args, env, cwd=str(repo.parent))
    assert code == 0, err
    assert out["tool"] == "plan-to-spec" and out["status"] == "ok"


def test_phase_steps_forwards_exit_code(repo, env):
    code, out, _ = call("phase-steps.py", [f"--root={repo}", "bump", "--plugin", "nope", "--to", "1.1.0"], env)
    assert code == 3 and out["tool"] == "release"


@pytest.mark.parametrize("args", [[], ["nope"], ["--root"]])
def test_phase_steps_rejects_bad_calls_with_2(env, args):
    p = subprocess.run([PY, str(SCRIPTS / "phase-steps.py"), *args], capture_output=True, text=True, env=env)
    assert p.returncode == 2 and p.stdout == ""
