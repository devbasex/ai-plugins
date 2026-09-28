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
    env["FAKE_GH_PRS"] = json.dumps(
        {
            "1": {"headRefName": "feature/x", "state": "MERGED"},
            "2": {"headRefName": "feature/y", "state": "MERGED"},
            "3": {"headRefName": "feature/gone", "state": "MERGED"},
            "4": {"headRefName": "feature/open", "state": "OPEN"},
        }
    )
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
    code, out, err = call(
        "plan-to-spec-steps.py",
        ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md", "--title", "X", "--root", str(repo)],
        env,
    )
    assert code == 0, err
    check_shape(out)
    assert out["tool"] == "plan-to-spec" and out["status"] == "ok"
    assert not (repo / "docs/design/x-design.md").exists()
    idx = (repo / "docs/specifications/README.md").read_text(encoding="utf-8")
    assert "- [x.md](x.md) — X" in idx
    assert git(repo, "status", "--porcelain").strip() == ""
    assert git(repo, "log", "-1", "--format=%s").strip() == "Docs: x.md を確定仕様にする"
    assert out["metrics"]["commit"] == git(repo, "rev-parse", "HEAD").strip()


def test_spec_finalize_leaves_unrelated_staged_changes_out_of_the_commit(repo, env):
    spec_repo(repo)
    write(repo, "other.txt", "利用者の作業\n")
    git(repo, "add", "--", "other.txt", "docs/specifications/x.md")
    code, _, err = call(
        "plan-to-spec-steps.py",
        ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md", "--title", "X", "--root", str(repo)],
        env,
    )
    assert code == 0, err
    committed = set(git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == {"docs/design/x-design.md", "docs/specifications/README.md", "docs/specifications/x.md"}
    assert git(repo, "diff", "--cached", "--name-only").split() == ["other.txt"]


def test_spec_finalize_skips_index_lines_inside_a_fence(repo, env):
    """索引の行は囲みの外だけを数える（lib/md.py。行の字面で見ていた頃は、囲みの中の例の後ろへ足した）。"""
    spec_repo(repo)
    write(repo, "docs/specifications/README.md", "# 索引\n\n- [a.md](a.md) — A\n\n```md\n- [z.md](z.md) — Z\n```\n")
    git(repo, "commit", "-q", "-am", "index")
    code, _, err = call(
        "plan-to-spec-steps.py",
        ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md", "--title", "X", "--root", str(repo)],
        env,
    )
    assert code == 0, err
    idx = (repo / "docs/specifications/README.md").read_text(encoding="utf-8")
    assert idx.startswith("# 索引\n\n- [a.md](a.md) — A\n- [x.md](x.md) — X\n\n```md\n")


@pytest.mark.parametrize("repeat", [True, False])
def test_spec_finalize_removes_every_design_given_by_repeated_or_listed_flags(repo, env, repeat):
    designs = ["docs/design/x-design.md", "issues/PLAN1_x.md", "issues/PLAN1_x-measure.py"]
    for rel in designs[1:]:
        write(repo, rel, "x\n")
    spec_repo(repo)
    flags = [f for d in designs for f in ("--design", d)] if repeat else ["--design", *designs]
    code, out, err = call(
        "plan-to-spec-steps.py", ["spec-finalize", "--spec", "docs/specifications/x.md", *flags, "--title", "X", "--root", str(repo)], env
    )
    assert code == 0, err
    assert out["metrics"]["removed_designs"] == 3
    for rel in designs:
        assert not (repo / rel).exists(), rel
    assert git(repo, "status", "--porcelain").strip() == ""


def test_spec_finalize_promotes_glossary_terms_of_the_removed_design(repo, env):
    """消した設計を pending_source に持つ語だけ、source を確定仕様へ移して文書ごと同じコミットに入れる。"""
    spec_repo(repo)
    decl = {
        "version": 1,
        "format": "json",
        "source": "docs/glossary/glossary.json",
        "document": "docs/glossary.md",
        "check": {"source_paths": ["docs/specifications/*.md"]},
    }
    g = {
        "version": 1,
        "contexts": [{"id": "c", "name": "C"}],
        "terms": [
            {"term": "移る語", "context": "c", "meaning": "m", "pending_source": "docs/design/x-design.md"},
            {"term": "残る語", "context": "c", "meaning": "m", "pending_source": "docs/design/y-design.md"},
        ],
    }
    write(repo, ".ndf/glossary.json", json.dumps(decl, ensure_ascii=False))
    write(repo, "docs/glossary/glossary.json", json.dumps(g, ensure_ascii=False))
    write(repo, "docs/design/y-design.md", "# 別の設計\n")
    assert (
        subprocess.run([sys.executable, str(SCRIPTS / "glossary.py"), "render", "--root", str(repo)], capture_output=True).returncode == 0
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "glossary")
    code, out, err = call(
        "plan-to-spec-steps.py",
        ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md", "--root", str(repo)],
        env,
    )
    assert code == 0, err
    terms = {t["term"]: t for t in json.loads((repo / "docs/glossary/glossary.json").read_text())["terms"]}
    assert terms["移る語"] == {"term": "移る語", "context": "c", "meaning": "m", "source": "docs/specifications/x.md"}
    assert terms["残る語"]["pending_source"] == "docs/design/y-design.md" and "source" not in terms["残る語"]
    assert "docs/specifications/x.md" in (repo / "docs/glossary.md").read_text(encoding="utf-8")
    assert git(repo, "status", "--porcelain").strip() == ""
    assert [i["name"] for i in out["items"] if i["kind"] == "glossary"] == ["移る語"]
    check = subprocess.run(
        [sys.executable, str(SCRIPTS / "glossary.py"), "check", "--rules", "structure", "--root", str(repo)], capture_output=True, text=True
    )
    assert check.returncode == 0, check.stdout


GOOD_DECL = '{"version": 1, "format": "json", "source": "g.json", "document": "g.md"}'
TERM = '"term": "語", "context": "c", "meaning": "m"'
PENDING = '{{"version": 1, "contexts": [{{"id": "c", "name": "C"}}], "terms": [{{{0}, "pending_source": "{1}/design/x-design.md"}}]}}'


@pytest.mark.parametrize(
    "decl,source",
    [
        ('{"version": 1}', None),
        ('{"source": "docs/glossary/none.json"}', None),
        ("not json", None),
        (GOOD_DECL, "not json"),
        (GOOD_DECL, '{"version": 1, "terms": {}}'),
        (GOOD_DECL, None),
        (GOOD_DECL, PENDING.format('"context": "c", "meaning": "m"', "docs")),
        (GOOD_DECL, PENDING.format(TERM, "./docs")),
    ],
)
def test_spec_finalize_stops_on_a_broken_glossary_declaration(repo, env, decl, source):
    """宣言・正本が読めないか、語の形（term・pending_source の書き方）が崩れていれば、設計を消す前に止める。"""
    spec_repo(repo)
    write(repo, ".ndf/glossary.json", decl)
    if source is not None:
        write(repo, "g.json", source)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "decl")
    code, _, _ = call(
        "plan-to-spec-steps.py",
        ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md", "--root", str(repo)],
        env,
    )
    assert code == 3
    assert (repo / "docs/design/x-design.md").is_file()
    assert git(repo, "status", "--porcelain").strip() == ""


def test_spec_finalize_keeps_the_design_when_the_glossary_cannot_be_written(repo, env):
    """用語集の文書を書けなければ、設計を git rm せずに止める。"""
    spec_repo(repo)
    write(repo, ".ndf/glossary.json", GOOD_DECL)
    write(repo, "g.json", PENDING.format(TERM, "docs"))
    write(repo, "g.md/keep", "文書の場所をディレクトリで塞ぐ\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "decl")
    code, _, _ = call(
        "plan-to-spec-steps.py",
        ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md", "--root", str(repo)],
        env,
    )
    assert code == 1
    assert (repo / "docs/design/x-design.md").is_file()
    assert "x-design.md" not in git(repo, "diff", "--cached", "--name-only")


def test_spec_finalize_missing_spec_is_precondition(repo, env):
    code, out, _ = call("plan-to-spec-steps.py", ["spec-finalize", "--spec", "nope.md", "--design", "keep.txt", "--root", str(repo)], env)
    assert code == 3
    assert out["status"] == "stopped" and "nope.md" in out["summary"]


# --- bump / changelog（release-steps.py） ------------------------------------


def plugin_repo(repo, name="mcp-x", version="1.0.0", heading="1.0.0"):
    d = f"plugins/mcp/{name}"
    write(
        repo,
        f"{d}/.claude-plugin/plugin.json",
        json.dumps({"name": name, "version": version, "description": f"X (v{version})"}, indent=2) + "\n",
    )
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
    code, out, err = call(
        "release-steps.py", ["changelog", "--version", "1.1.0", "--prs", "5", "6", "--plugin", "mcp-x", "--root", str(repo)], env
    )
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
    code, out, _ = call(
        "release-steps.py", ["changelog", "--version", "1.1.0", "--prs", "5", "--plugin", "mcp-x", "--root", str(repo)], env
    )
    assert code == 0
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8").count("（#5）") == 1


def test_changelog_gh_failure_stops(repo, env):
    write(repo, "CHANGELOG.md", "# Changelog\n")
    code, out, _ = call("release-steps.py", ["changelog", "--version", "1.1.0", "--prs", "99", "--root", str(repo)], env)
    assert code == 1 and out["status"] == "stopped" and "gh pr view 99" in out["summary"]


def test_release_steps_run_and_check_are_unchanged(repo, env):
    assert subprocess.run([PY, str(SCRIPTS / "release-steps.py"), "check", "--root", str(repo)], env=env).returncode == 2
    p = subprocess.run(
        [PY, str(SCRIPTS / "release-steps.py"), "run", "--root", str(repo), "--stage", "production", "--version", "1.0.0"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0 and p.stdout == ""


def test_verify_install_unknown_runtime_is_2(repo, env):
    code, out, _ = call(
        "release-verification-steps.py",
        ["verify-install", "--ref", "develop", "--expect", "1.0.0", "--runtimes", "nope", "--root", str(repo)],
        env,
    )
    assert code == 2 and out["tool"] == "release-verification" and out["status"] == "stopped"


def load_verification():
    import importlib.util

    spec = importlib.util.spec_from_file_location("release_verification_steps", SCRIPTS / "release-verification-steps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("installed", ["symlink", "copy", "differs", "missing"])
def test_compare_files_follows_a_symlink_to_a_directory(tmp_path, installed):
    """dev.agy/skills/<名前> → ../../skills/<名前> のようなディレクトリへの symlink を比べられる。"""
    mod = load_verification()
    src = tmp_path / "src" / "p"
    (src / "skills" / "a").mkdir(parents=True)
    (src / "skills" / "a" / "SKILL.md").write_text("x\n")
    (src / "agy").mkdir()
    (src / "agy" / "a").symlink_to("../skills/a")
    dst = tmp_path / "dst"
    (dst / "skills" / "a").mkdir(parents=True)
    (dst / "skills" / "a" / "SKILL.md").write_text("x\n")
    (dst / "agy").mkdir()
    if installed == "symlink":
        (dst / "agy" / "a").symlink_to("../skills/a")
    elif installed in ("copy", "differs"):
        (dst / "agy" / "a").mkdir()
        (dst / "agy" / "a" / "SKILL.md").write_text("x\n" if installed == "copy" else "y\n")
    out = mod.compare_files(tmp_path / "src", dst, "p", ["p/agy/a", "p/skills/a/SKILL.md"], "claude")
    assert out == ([] if installed in ("symlink", "copy") else ["claude: p/agy/a"])


def test_compare_files_skips_symlinks_for_a_runtime_that_drops_them(tmp_path):
    """codex は symlink を導入先へ入れないので、symlink が無くても不一致にしない。"""
    mod = load_verification()
    src = tmp_path / "src" / "p"
    (src / "skills" / "a").mkdir(parents=True)
    (src / "skills" / "a" / "SKILL.md").write_text("x\n")
    (src / "agy").mkdir()
    (src / "agy" / "a").symlink_to("../skills/a")
    dst = tmp_path / "dst"
    (dst / "skills" / "a").mkdir(parents=True)
    (dst / "skills" / "a" / "SKILL.md").write_text("y\n")
    files = ["p/agy/a", "p/skills/a/SKILL.md"]
    out = mod.compare_files(tmp_path / "src", dst, "p", files, "codex", keeps_symlinks=False)
    assert out == ["codex: p/skills/a/SKILL.md"]


def kiro_project(tmp_path):
    """この checkout の dev.kiro/install.sh で <tmp>/proj へ導入し、(src, proj) を返す。"""
    src = SCRIPTS.parents[2]
    proj = tmp_path / "proj"
    proj.mkdir()
    p = subprocess.run(
        ["bash", str(src / "plugins/ndf/dev.kiro/install.sh"), "--project", str(proj), "--yes"],
        capture_output=True,
        text=True,
        cwd=str(src),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return src, proj


def test_compare_kiro_accepts_what_the_installer_made(tmp_path):
    src, proj = kiro_project(tmp_path)
    assert load_verification().compare_kiro(src, proj) == []


@pytest.mark.parametrize("broken", ["skill_link", "prompt", "steering", "agent", "policy_link"])
def test_compare_kiro_reports_missing_or_changed_outputs(tmp_path, broken):
    """Kiro の生成物（skills / prompts / steering / agents）の欠落・内容違いを不一致にする。"""
    src, proj = kiro_project(tmp_path)
    kiro = proj / ".kiro"
    if broken == "skill_link":
        (kiro / "skills" / "pr").unlink()
        want = "kiro: .kiro/skills/pr"
    elif broken == "prompt":
        (kiro / "prompts" / "pr.md").write_text("changed\n", encoding="utf-8")
        want = "kiro: .kiro/prompts/pr.md"
    elif broken == "steering":
        (kiro / "steering" / "ndf-policies.md").write_text("stale\n", encoding="utf-8")
        want = "kiro: .kiro/steering/ndf-policies.md"
    elif broken == "agent":
        (kiro / "agents" / "ndf.json").write_text("{", encoding="utf-8")
        want = "kiro: .kiro/agents/ndf.json"
    else:
        (kiro / "skills" / "ndf-policies").symlink_to(src / "plugins/ndf/skills/ndf-policies")
        want = "kiro: .kiro/skills/ndf-policies が残っている（steering へ移した Skill）"
    assert load_verification().compare_kiro(src, proj) == [want]


# --- phase-steps.py（互換の入口） --------------------------------------------


@pytest.mark.parametrize("root_first", [True, False])
def test_phase_steps_forwards_with_root_before_or_after(repo, env, root_first):
    spec_repo(repo)
    sub = ["spec-finalize", "--spec", "docs/specifications/x.md", "--design", "docs/design/x-design.md"]
    args = ["--root", str(repo), *sub] if root_first else [*sub, "--root", str(repo)]
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


def clone_with_upstream(tmp_path, repo):
    """repo を origin にした主ディレクトリ（develop）と、origin へ a.md を足したコミットを返す。"""
    main = tmp_path / "main"
    git(tmp_path, "clone", "-q", "-b", "develop", str(repo), str(main))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(main, "config", k, v)
    write(repo, "a.md", "写し\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a")
    return main


@pytest.mark.parametrize("local, pulled", [("写し\n", True), ("別の中身\n", False)])
def test_cleanup_pull_removes_untracked_file_equal_to_upstream(repo, env, tmp_path, local, pulled):
    main = clone_with_upstream(tmp_path, repo)
    write(main, "a.md", local)  # 取り込む内容と同じ（または違う）未追跡のファイル
    env["FAKE_GH_PRS"] = json.dumps({"1": {"headRefName": "feature/gone", "state": "MERGED"}})
    code, out, err = call("merged-steps.py", ["cleanup", "1", "--root", str(main)], env)
    by = {(i["kind"], i["name"]): i for i in out["items"]}
    if pulled:
        assert code == 0, err
        assert by[("untracked", "a.md")]["result"] == "removed"
        assert by[("main_dir", str(main))]["result"] == "pulled"
        assert git(main, "log", "-1", "--format=%s").strip() == "a"
    else:
        assert code != 0
        assert ("untracked", "a.md") not in by and by[("main_dir", str(main))]["result"] == "stopped"
        assert (main / "a.md").read_text(encoding="utf-8") == "別の中身\n"


def clone_with_main_and_develop(tmp_path, cur):
    """origin の HEAD が main で develop もある上流を clone し、主ディレクトリを `cur` に置く（宣言は無い）。"""
    up = tmp_path / "up"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(up, "config", k, v)
    write(up, "keep.txt", "head\n")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "init")
    git(up, "branch", "develop")
    main = tmp_path / "main"
    git(tmp_path, "clone", "-q", "-b", cur, str(up), str(main))
    return main


@pytest.mark.parametrize("cur, result", [("main", "pulled"), ("develop", "kept")])
def test_cleanup_base_defaults_to_origin_head_not_develop(tmp_path, env, cur, result):
    # 宣言の無いリポジトリでは、起点は origin の HEAD（main）から決まる。develop があっても採らない
    main = clone_with_main_and_develop(tmp_path, cur)
    env["FAKE_GH_PRS"] = json.dumps({"1": {"headRefName": "feature/gone", "state": "MERGED"}})
    code, out, err = call("merged-steps.py", ["cleanup", "1", "--root", str(main)], env)
    assert code == 0, err
    item = {(i["kind"], i["name"]): i for i in out["items"]}[("main_dir", str(main))]
    assert item["result"] == result
    if result == "kept":
        assert "main でなく develop" in item["reason"]


def test_cleanup_base_follows_declaration(tmp_path, env):
    main = clone_with_main_and_develop(tmp_path, "develop")
    write(main, ".ndf/worktree.json", json.dumps({"version": 1, "base_branch": "develop"}))
    env["FAKE_GH_PRS"] = json.dumps({"1": {"headRefName": "feature/gone", "state": "MERGED"}})
    code, out, err = call("merged-steps.py", ["cleanup", "1", "--root", str(main)], env)
    assert code == 0, err
    assert {(i["kind"], i["name"]): i for i in out["items"]}[("main_dir", str(main))]["result"] == "pulled"


@pytest.mark.parametrize(("local", "same"), [(b"a\nb\n", True), (b"a\r\nb\r\n", False)])
def test_same_untracked_compares_bytes_not_text(repo, tmp_path, local, same):
    """上流が LF で手元の未追跡が CRLF なら別物とみなし、消す一覧に入れない。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("merged_steps", SCRIPTS / "merged-steps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    up = tmp_path / "up.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(up))
    git(repo, "remote", "add", "origin", str(up))
    git(repo, "fetch", "-q", "origin")
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "new.txt").write_bytes(b"a\nb\n")
    git(repo, "add", "new.txt")
    git(repo, "commit", "-q", "-m", "new")
    git(repo, "push", "-q", "origin", "side:develop")
    git(repo, "checkout", "-q", "develop")
    git(repo, "branch", "-q", "-D", "side")
    git(repo, "branch", "-q", "--set-upstream-to", "origin/develop")
    git(repo, "fetch", "-q", "origin")
    (repo / "new.txt").write_bytes(local)
    pull = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only", "-q"], capture_output=True, text=True)
    assert "untracked working tree files would be overwritten" in pull.stderr
    assert mod.same_untracked(repo, pull) == (["new.txt"] if same else [])


def test_verify_install_source_comes_from_origin_and_declaration(repo):
    """#1336 の決定 8・I9: 導入元の owner/repo は origin、ref の候補とプラグインは宣言から読む。ai-plugins の宣言では今と同じ値。"""
    import argparse

    mod = load_verification()
    git(repo, "remote", "add", "origin", "https://github.com/devbasex/ai-plugins.git")
    write(repo, ".ndf/worktree.json", json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
    write(repo, ".ndf/supervise.json", json.dumps({"release": {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}}))
    write(repo, ".claude-plugin/marketplace.json", json.dumps({"name": "ai-plugins"}))
    where, plugins = mod.install_source(repo, argparse.Namespace(ref="develop", plugins=None))
    assert where == ("devbasex/ai-plugins", "ai-plugins", "main") and plugins == ["ndf"]
    with pytest.raises(mod.StepError, match="--ref trunk"):
        mod.install_source(repo, argparse.Namespace(ref="trunk", plugins=None))
    write(repo, ".ndf/worktree.json", json.dumps({"version": 1, "base_branch": "trunk", "production_branch": "live"}))
    where, _ = mod.install_source(repo, argparse.Namespace(ref="trunk", plugins="foo"))
    assert where[2] == "live"


@pytest.mark.parametrize(
    "case",
    ["ok", "version_mismatch", "failed", "file_mismatch", "user_env_changed"],
)
def test_verify_install_assembles_items_and_metrics(tmp_path, monkeypatch, case):
    """現状固定: cmd_verify_install が導入の結果・中身の比較・利用者の環境から items と metrics を組み立てる。"""
    import argparse
    import types

    mod = load_verification()
    calls = []

    def fake_git(root, *args):
        calls.append(args)
        out = {
            "rev-parse": "abcdef1234567890\n",
            "tag": "ndf--v1.2.0 ndf--v1.1.0-dev.3 ndf--v1.1.0 ndf--v1.0.0\n",
            "diff": "plugins/ndf/a.txt plugins/ndf/b.txt\n",
        }.get(args[0], "")
        return types.SimpleNamespace(stdout=out)

    def fake_run(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    snaps = iter([{"claude": "x"}, {"claude": "y" if case == "user_env_changed" else "x"}])
    seen = {}

    def verify_claude(env, ref, plugins, expect, where):
        code = 1 if case == "failed" else 0
        ver = "9.9.9" if case == "version_mismatch" else "1.2.0"
        return {"exit": code, "version": {"ndf": ver, "mcp-serena": "0.1.0"}}, {"ndf": tmp_path / "inst", "mcp-serena": None}

    def compare_files(src, d, rel, changed, name, keeps_symlinks):
        seen["compare"] = (rel, changed, name, keeps_symlinks)
        return ["claude: a.txt が違う"] if case == "file_mismatch" else []

    emitted = []
    monkeypatch.setattr(mod, "git_root", lambda r: tmp_path)
    monkeypatch.setattr(mod, "install_source", lambda root, a: (("o/r", "m", "main"), ["ndf", "mcp-serena"]))
    monkeypatch.setattr(mod, "plugin_dir", lambda root, p: root / "plugins" / p)
    monkeypatch.setattr(mod, "git", fake_git)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "user_env_snapshot", lambda: next(snaps))
    monkeypatch.setattr(mod, "isolated_env", lambda tmp: {})
    monkeypatch.setattr(mod, "verify_claude", verify_claude)
    monkeypatch.setattr(mod, "compare_files", compare_files)
    monkeypatch.setattr(mod, "emit", emitted.append)

    a = argparse.Namespace(root=str(tmp_path), ref="develop", expect="1.2.0", runtimes="claude", plugins=None)
    mod.cmd_verify_install(a)
    out = emitted[0]

    assert ("tag", "--list", "ndf--v*", "--sort=-v:refname") in calls
    # 前の正式版は今の版と開発版を除いた最新のタグ
    assert out["metrics"]["prev_tag"] == "ndf--v1.1.0"
    assert ("diff", "--name-only", "ndf--v1.1.0", "abcdef1234567890", "--", "plugins/ndf") in calls
    assert seen["compare"] == ("plugins/ndf", ["plugins/ndf/a.txt", "plugins/ndf/b.txt"], "claude", True)
    assert out["metrics"]["rev"] == "abcdef12" and out["metrics"]["ref"] == "develop"
    kinds = [(i["kind"], i["name"], i["result"]) for i in out["items"]]
    assert ("file", "claude: mcp-serena の導入先が無い", "mismatch") in kinds
    rt = {"ok": "ok", "version_mismatch": "version_mismatch", "failed": "failed"}.get(case, "ok")
    assert ("runtime", "claude", rt) in kinds
    assert out["status"] == "stopped"  # mcp-serena の導入先が無いので常に止まる
    assert out["metrics"]["user_env_unchanged"] is (case != "user_env_changed")
    if case == "user_env_changed":
        assert ("user_env", "claude", "changed") in kinds
    assert out["metrics"]["mismatch"] == (2 if case == "file_mismatch" else 1)


def test_verify_install_all_ok_emits_ok(tmp_path, monkeypatch):
    """現状固定: 版・中身・利用者の環境がそろえば ok で、summary に ref と版が出る。"""
    import argparse
    import types

    mod = load_verification()

    def fake_git(root, *args):
        out = {"rev-parse": "abcdef1234567890\n", "tag": "ndf--v1.2.0\n"}.get(args[0], "")
        return types.SimpleNamespace(stdout=out)

    emitted = []
    monkeypatch.setattr(mod, "git_root", lambda r: tmp_path)
    monkeypatch.setattr(mod, "install_source", lambda root, a: (("o/r", "m", "main"), ["ndf"]))
    monkeypatch.setattr(mod, "plugin_dir", lambda root, p: root / "plugins" / p)
    monkeypatch.setattr(mod, "git", fake_git)
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
    monkeypatch.setattr(mod, "user_env_snapshot", lambda: {"k": "v"})
    monkeypatch.setattr(mod, "isolated_env", lambda tmp: {})
    monkeypatch.setattr(mod, "verify_codex", lambda *a: ({"exit": 0, "version": "1.2.0"}, {"ndf": tmp_path}))
    monkeypatch.setattr(mod, "compare_files", lambda *a, **k: [])
    monkeypatch.setattr(mod, "emit", emitted.append)

    mod.cmd_verify_install(argparse.Namespace(root=None, ref="develop", expect="1.2.0", runtimes="codex", plugins=None))
    out = emitted[0]
    assert out["status"] == "ok"
    assert out["metrics"]["prev_tag"] is None and out["metrics"]["mismatch"] == 0
    assert [(i["kind"], i["name"], i["result"]) for i in out["items"]] == [("runtime", "codex", "ok")]
    assert "develop（abcdef12）" in out["summary"] and "v1.2.0" in out["summary"]
